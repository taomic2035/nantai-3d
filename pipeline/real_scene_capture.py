"""Verified real-scene capture preparation.

This layer selects source media and binds existing ingest/capture contracts.
It proves byte closure only; ``synthetic=False`` does not promote geometry.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pipeline.durable_io import (
    DurableIOError,
    first_linklike_path,
    publish_file_noreplace,
)
from pipeline.ingest import ingest_all
from pipeline.ingest_manifest import (
    MANIFEST_FILENAME,
    verify_ingest_artifact,
)
from pipeline.real_dataset import (
    CaptureRightsReceipt,
    DatasetEvidenceError,
    HfDatasetSource,
    LocalCaptureSource,
    canonical_model_bytes,
    validate_capture_rights,
)
from pipeline.real_dataset_fetch import (
    DatasetDownloadError,
    verify_hf_dataset,
)
from pipeline.recon_schema import (
    AxisConvention,
    CoordinateUnits,
    FrameProvenance,
    RegistrationResult,
)
from pipeline.registration import register
from pipeline.registration_quality import (
    RegistrationQualityPolicy,
    RegistrationQualityReport,
    SparseModelEnumeration,
    build_registration_quality_report,
    enumerate_sparse_models,
    validate_registration_quality,
)
from pipeline.studio_revisions import (
    PreparedCaptureBundle,
    prepare_capture_bundle,
)


class RealSceneCaptureError(ValueError):
    """Source media cannot produce an auditable capture revision."""


@dataclass(frozen=True)
class PreparedRealCapture:
    source_sha256: str
    dataset_receipt_sha256: str
    selected_paths: tuple[str, ...]
    capture: PreparedCaptureBundle

    @property
    def capture_manifest(self):
        return self.capture.manifest

    @property
    def payload_root(self) -> Path:
        return self.capture.bundle / "payload"


@dataclass(frozen=True)
class RealSfmResult:
    registration: RegistrationResult
    registration_path: Path
    registration_sha256: str
    sparse_enumeration: SparseModelEnumeration | None
    quality: RegistrationQualityReport
    quality_path: Path
    quality_sha256: str


def _cross_surface_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _same_surface_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _is_linklike(path: Path, observed: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0)) & reparse_flag
    ):
        return True
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _sha256_file(path: Path) -> tuple[int, str]:
    try:
        redirected = first_linklike_path(
            Path(path.absolute().anchor), path
        )
        before = path.lstat()
    except OSError as exc:
        raise RealSceneCaptureError(
            "source media cannot be inspected"
        ) from exc
    except ValueError as exc:
        raise RealSceneCaptureError(
            "source media cannot be inspected"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(path, before)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise RealSceneCaptureError(
            "source media is not a regular file"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RealSceneCaptureError(
            "source media cannot be read"
        ) from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise RealSceneCaptureError(
            "source media cannot be read"
        ) from exc
    digest = hashlib.sha256()
    measured = 0
    try:
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(descriptor_before.st_mode)
                or _cross_surface_signature(descriptor_before)
                != _cross_surface_signature(before)
            ):
                raise RealSceneCaptureError(
                    "source media changed before hash"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                measured += len(chunk)
                digest.update(chunk)
            descriptor_after = os.fstat(stream.fileno())
        after = path.lstat()
    except RealSceneCaptureError:
        raise
    except OSError as exc:
        raise RealSceneCaptureError(
            "source media cannot be read"
        ) from exc
    if (
        _same_surface_signature(descriptor_before)
        != _same_surface_signature(descriptor_after)
        or _same_surface_signature(before)
        != _same_surface_signature(after)
        or _cross_surface_signature(descriptor_after)
        != _cross_surface_signature(after)
        or measured != before.st_size
    ):
        raise RealSceneCaptureError(
            "source media changed while hashing"
        )
    return measured, digest.hexdigest()


_MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def _stable_read_bytes(
    path: Path,
    *,
    label: str,
    max_bytes: int = _MAX_MANIFEST_BYTES,
) -> bytes:
    """Read a trust-critical manifest via a single controlled descriptor."""
    try:
        redirected = first_linklike_path(
            Path(path.absolute().anchor), path
        )
        before = path.lstat()
    except OSError as exc:
        raise RealSceneCaptureError(
            f"{label} cannot be inspected"
        ) from exc
    except ValueError as exc:
        raise RealSceneCaptureError(
            f"{label} cannot be inspected"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(path, before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > max_bytes
    ):
        raise RealSceneCaptureError(f"{label} is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RealSceneCaptureError(f"{label} cannot be read") from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise RealSceneCaptureError(f"{label} cannot be read") from exc
    payload = bytearray()
    try:
        with stream:
            fd_before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(fd_before.st_mode)
                or _cross_surface_signature(fd_before)
                != _cross_surface_signature(before)
            ):
                raise RealSceneCaptureError(
                    f"{label} changed before read"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise RealSceneCaptureError(
                        f"{label} exceeds its byte limit"
                    )
            fd_after = os.fstat(stream.fileno())
        after = path.lstat()
    except RealSceneCaptureError:
        raise
    except OSError as exc:
        raise RealSceneCaptureError(f"{label} cannot be read") from exc
    if (
        _same_surface_signature(fd_before)
        != _same_surface_signature(fd_after)
        or _same_surface_signature(before) != _same_surface_signature(after)
        or _cross_surface_signature(fd_after) != _cross_surface_signature(after)
        or len(payload) != before.st_size
    ):
        raise RealSceneCaptureError(f"{label} changed while being read")
    return bytes(payload)


def _require_absent_capture_boundary(run_root: Path) -> Path:
    boundary = run_root / "capture"
    if boundary.exists() or boundary.is_symlink():
        raise RealSceneCaptureError("capture output boundary must be absent")
    try:
        boundary.mkdir(parents=True)
    except OSError as exc:
        raise RealSceneCaptureError(
            "cannot create capture output boundary"
        ) from exc
    if boundary.is_symlink() or boundary.resolve(strict=True) != boundary.absolute():
        raise RealSceneCaptureError("capture output boundary is redirected")
    return boundary


def _select_hf_capture_paths(
    source: HfDatasetSource,
    receipt_paths: tuple[str, ...],
) -> tuple[str, ...]:
    prefix = f"{source.capture_subtree}/"
    direct: list[str] = []
    nested: list[str] = []
    for relative_path in receipt_paths:
        if not relative_path.startswith(prefix):
            continue
        remainder = relative_path.removeprefix(prefix)
        if "/" in remainder:
            nested.append(relative_path)
        else:
            direct.append(relative_path)
    if nested:
        raise RealSceneCaptureError(
            "capture_subtree contains nested members; source selection is ambiguous"
        )
    if not direct:
        raise RealSceneCaptureError("capture_subtree contains no direct source media")
    return tuple(sorted(direct))


def _stable_copy(
    source_path: Path,
    destination: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    try:
        before = source_path.lstat()
    except OSError as exc:
        raise RealSceneCaptureError("source media disappeared before copy") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RealSceneCaptureError("source media is not a regular file")
    measured_bytes, measured_sha = _sha256_file(source_path)
    if measured_bytes != expected_bytes:
        raise RealSceneCaptureError("source media length changed before copy")
    if measured_sha != expected_sha256:
        raise RealSceneCaptureError("source media sha256 changed before copy")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_fd, staging_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(staging_fd, "wb") as staging:
            # os.open + O_NOFOLLOW: do not reopen source by name via Path.open,
            # which follows symlinks and reopens a TOCTOU window after the
            # initial lstat. _sha256_file already uses this safe open pattern.
            source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            source_descriptor = os.open(source_path, source_flags)
            try:
                source_stream = os.fdopen(source_descriptor, "rb", buffering=0)
            except OSError:
                try:
                    os.close(source_descriptor)
                except OSError:
                    pass
                raise
            with source_stream:
                shutil.copyfileobj(source_stream, staging)
            staging.flush()
            os.fsync(staging.fileno())
            # lstat (not stat): stat() follows symlinks, so a symlink swap
            # would report the target's identity and could bypass the
            # identity check if the target matches.
            after = source_path.lstat()
    except OSError as exc:
        _discard_staging(staging_path)
        raise RealSceneCaptureError("source media copy failed") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        _discard_staging(staging_path)
        raise RealSceneCaptureError("source media changed while copying")
    copied_bytes, copied_sha = _sha256_file(Path(staging_path))
    if copied_bytes != expected_bytes or copied_sha != expected_sha256:
        _discard_staging(staging_path)
        raise RealSceneCaptureError("copied source media failed byte verification")
    try:
        publish_file_noreplace(staging_path, destination)
    except FileExistsError:
        _discard_staging(staging_path)
        raise RealSceneCaptureError(
            "destination already exists"
        ) from None
    except DurableIOError as exc:
        _discard_staging(staging_path)
        if exc.published:
            raise RealSceneCaptureError(
                "copy published but durability unconfirmed"
            ) from exc
        raise RealSceneCaptureError(
            "copy publication failed"
        ) from exc
    except OSError as exc:
        _discard_staging(staging_path)
        raise RealSceneCaptureError(
            "copy publication failed"
        ) from exc


def prepare_real_capture(
    source: HfDatasetSource | LocalCaptureSource,
    source_root: Path,
    run_root: Path,
) -> PreparedRealCapture:
    """Create one immutable capture revision from revalidated source bytes."""

    if not isinstance(source, HfDatasetSource):
        raise RealSceneCaptureError(
            "local-capture preparation requires the Task 8 private-media adapter"
        )
    try:
        receipt = verify_hf_dataset(source, source_root)
    except DatasetDownloadError as exc:
        raise RealSceneCaptureError(
            "dataset verification failed"
        ) from exc

    selected_paths = _select_hf_capture_paths(
        source,
        tuple(entry.relative_path for entry in receipt.entries),
    )
    entries = {entry.relative_path: entry for entry in receipt.entries}
    boundary = _require_absent_capture_boundary(run_root)
    selected_root = boundary / "source"
    selected_root.mkdir()
    dataset_root = source_root / "dataset"
    for relative_path in selected_paths:
        entry = entries[relative_path]
        logical_name = PurePosixPath(relative_path).name
        _stable_copy(
            dataset_root.joinpath(*relative_path.split("/")),
            selected_root / logical_name,
            expected_bytes=entry.actual_bytes,
            expected_sha256=entry.actual_sha256,
        )

    ingest_root = boundary / "ingest"
    try:
        ingest_all(selected_root, ingest_root)
    except Exception as exc:
        raise RealSceneCaptureError("ingest failed") from exc
    ingest_bytes = _stable_read_bytes(
        ingest_root / MANIFEST_FILENAME,
        label="ingest manifest",
    )
    revision_id = (
        "capture-" + hashlib.sha256(ingest_bytes).hexdigest()[:32]
    )
    try:
        capture = prepare_capture_bundle(
            stage_dir=ingest_root,
            input_dir=selected_root,
            bundle_dir=boundary / "bundle",
            revision_id=revision_id,
            synthetic=False,
            created_utc=datetime.now(UTC),
        )
    except Exception as exc:
        raise RealSceneCaptureError(
            "capture bundle preparation failed"
        ) from exc
    return PreparedRealCapture(
        source_sha256=hashlib.sha256(canonical_model_bytes(source)).hexdigest(),
        dataset_receipt_sha256=hashlib.sha256(
            canonical_model_bytes(receipt)
        ).hexdigest(),
        selected_paths=selected_paths,
        capture=capture,
    )


def prepare_local_capture(
    source: LocalCaptureSource,
    media_root: Path,
    rights: CaptureRightsReceipt,
    run_root: Path,
) -> PreparedRealCapture:
    """Create an immutable capture revision from private local media.

    Runtime absolute paths are used only to copy and verify the selected input
    bytes.  Portable evidence retains relative paths, source SHA, rights SHA
    (through the source record), and the content-addressed ingest manifest.
    """

    try:
        validate_capture_rights(source, rights)
    except DatasetEvidenceError as exc:
        raise RealSceneCaptureError(
            "capture rights validation failed"
        ) from exc

    boundary = _require_absent_capture_boundary(run_root)
    ingest_root = boundary / "ingest"
    try:
        ingest_all(media_root, ingest_root)
        ingest = verify_ingest_artifact(
            ingest_root,
            input_dir=media_root,
        )
    except Exception as exc:
        raise RealSceneCaptureError(
            "private media ingest failed"
        ) from exc

    selected_root = boundary / "source"
    try:
        selected_root.mkdir()
    except OSError as exc:
        raise RealSceneCaptureError(
            "cannot create private source copy"
        ) from exc
    selected_paths = tuple(
        sorted(record.source_path for record in ingest.sources)
    )
    by_path = {record.source_path: record for record in ingest.sources}
    for relative_path in selected_paths:
        record = by_path[relative_path]
        portable = PurePosixPath(relative_path)
        _stable_copy(
            Path(media_root).joinpath(*portable.parts),
            selected_root.joinpath(*portable.parts),
            expected_bytes=record.bytes,
            expected_sha256=record.source_sha256,
        )

    try:
        copied_ingest = verify_ingest_artifact(
            ingest_root,
            input_dir=selected_root,
        )
    except Exception as exc:
        raise RealSceneCaptureError(
            "private source copy differs from ingest evidence"
        ) from exc
    if copied_ingest != ingest:
        raise RealSceneCaptureError(
            "private source copy changed the ingest manifest identity"
        )

    try:
        ingest_bytes = _stable_read_bytes(
            ingest_root / MANIFEST_FILENAME,
            label="private ingest manifest",
        )
    except RealSceneCaptureError as exc:
        raise RealSceneCaptureError(
            "cannot read private ingest evidence"
        ) from exc
    ingest_sha256 = hashlib.sha256(ingest_bytes).hexdigest()
    revision_id = f"capture-{ingest_sha256[:32]}"
    try:
        capture = prepare_capture_bundle(
            stage_dir=ingest_root,
            input_dir=selected_root,
            bundle_dir=boundary / "bundle",
            revision_id=revision_id,
            synthetic=False,
            created_utc=datetime.now(UTC),
        )
    except Exception as exc:
        raise RealSceneCaptureError(
            "private capture bundle preparation failed"
        ) from exc
    return PreparedRealCapture(
        source_sha256=hashlib.sha256(
            canonical_model_bytes(source)
        ).hexdigest(),
        dataset_receipt_sha256=ingest_sha256,
        selected_paths=selected_paths,
        capture=capture,
    )


def _write_model_json(path: Path, model) -> bytes:
    """Write a canonical JSON model via private staging + fsync + no-replace.

    Uses ``publish_file_noreplace`` from ``pipeline.durable_io`` for a
    cross-platform atomic no-replace publication with directory durability,
    preventing a TOCTOU attacker from silently swapping evidence.
    """
    payload = (model.model_dump_json(indent=2) + "\n").encode("utf-8")
    staging_fd, staging_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(staging_fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        publish_file_noreplace(staging_path, path)
    except FileExistsError:
        _discard_staging(staging_path)
        raise RealSceneCaptureError(
            "quality report output already exists"
        ) from None
    except DurableIOError as exc:
        _discard_staging(staging_path)
        if exc.published:
            raise RealSceneCaptureError(
                "quality report published but durability unconfirmed"
            ) from exc
        raise RealSceneCaptureError(
            "cannot write quality report"
        ) from exc
    except OSError as exc:
        _discard_staging(staging_path)
        raise RealSceneCaptureError(
            "cannot write quality report"
        ) from exc
    return payload


def _discard_staging(staging_path: str) -> None:
    try:
        os.unlink(staging_path)
    except OSError:
        pass


def run_real_sfm(
    capture: PreparedRealCapture,
    run_root: Path,
    policy: RegistrationQualityPolicy,
) -> RealSfmResult:
    """Run COLMAP and derive its training gate from authoritative artifacts."""

    sfm_root = run_root / "sfm"
    if sfm_root.exists() or sfm_root.is_symlink():
        raise RealSceneCaptureError("SfM output boundary must be absent")
    try:
        sfm_root.mkdir(parents=True)
    except OSError as exc:
        raise RealSceneCaptureError("cannot create SfM boundary") from exc
    registration_path = sfm_root / "registration.json"
    colmap_root = sfm_root / "colmap"
    try:
        registration = register(
            capture.payload_root,
            out_json=registration_path,
            engine="colmap",
            workspace=colmap_root,
        )
    except Exception as exc:
        raise RealSceneCaptureError("COLMAP registration failed") from exc
    try:
        registration_bytes = _stable_read_bytes(
            registration_path,
            label="registration evidence",
        )
        reparsed = RegistrationResult.model_validate_json(registration_bytes)
    except (RealSceneCaptureError, ValueError) as exc:
        raise RealSceneCaptureError(
            "registration evidence is unreadable"
        ) from exc
    if reparsed != registration:
        raise RealSceneCaptureError(
            "registration object differs from registration.json bytes"
        )

    sparse_enumeration: SparseModelEnumeration | None = None
    if registration.engine == "colmap":
        try:
            sparse_enumeration = enumerate_sparse_models(
                colmap_root / "sparse",
                capture.capture.manifest.output_count,
            )
        except ValueError as exc:
            raise RealSceneCaptureError(
                "COLMAP sparse model enumeration failed"
            ) from exc
        selected = next(
            model
            for model in sparse_enumeration.models
            if model.model_index == sparse_enumeration.selected_model_index
        )
        pose_names = {pose.image for pose in registration.poses}
        if set(selected.images) != pose_names:
            raise RealSceneCaptureError(
                "selected sparse model images differ from registration poses"
            )
        capture_names = {
            payload.logical_path for payload in capture.capture.manifest.payloads
        }
        if not pose_names <= capture_names:
            raise RealSceneCaptureError(
                "registration poses contain images outside the capture manifest"
            )
        frame = registration.pose_frame
        if (
            frame.provenance != FrameProvenance.SFM
            or frame.axes != AxisConvention.SFM_ARBITRARY
            or frame.units != CoordinateUnits.ARBITRARY
        ):
            raise RealSceneCaptureError(
                "COLMAP registration has contradictory coordinate provenance"
            )
    elif registration.engine != "mock":
        raise RealSceneCaptureError(
            f"unexpected registration engine: {registration.engine}"
        )

    capture_manifest_path = capture.capture.bundle / "manifest.json"
    capture_manifest_bytes = _stable_read_bytes(
        capture_manifest_path,
        label="capture manifest",
    )
    try:
        quality = build_registration_quality_report(
            registration=registration,
            registration_json_bytes=registration_bytes,
            capture_manifest=capture.capture.manifest,
            capture_manifest_bytes=capture_manifest_bytes,
            policy=policy,
            sparse_enumeration=sparse_enumeration,
            invocation_succeeded=True,
        )
        validate_registration_quality(
            report=quality,
            policy=policy,
            registration_json_bytes=registration_bytes,
            capture_manifest_bytes=capture_manifest_bytes,
            sparse_enumeration=sparse_enumeration,
        )
    except ValueError as exc:
        raise RealSceneCaptureError(
            "registration quality evidence is inconsistent"
        ) from exc
    quality_path = sfm_root / "registration-quality-report.json"
    quality_bytes = _write_model_json(quality_path, quality)
    return RealSfmResult(
        registration=registration,
        registration_path=registration_path,
        registration_sha256=hashlib.sha256(registration_bytes).hexdigest(),
        sparse_enumeration=sparse_enumeration,
        quality=quality,
        quality_path=quality_path,
        quality_sha256=hashlib.sha256(quality_bytes).hexdigest(),
    )
