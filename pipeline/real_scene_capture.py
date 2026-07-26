"""Verified real-scene capture preparation.

This layer selects source media and binds existing ingest/capture contracts.
It proves byte closure only; ``synthetic=False`` does not promote geometry.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

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


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    measured = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                measured += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise RealSceneCaptureError(
            f"cannot hash source media {path.name}: {exc}"
        ) from exc
    return measured, digest.hexdigest()


def _require_absent_capture_boundary(run_root: Path) -> Path:
    boundary = run_root / "capture"
    if boundary.exists() or boundary.is_symlink():
        raise RealSceneCaptureError("capture output boundary must be absent")
    try:
        boundary.mkdir(parents=True)
    except OSError as exc:
        raise RealSceneCaptureError(
            f"cannot create capture output boundary: {exc}"
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
    try:
        shutil.copyfile(source_path, destination, follow_symlinks=False)
        after = source_path.stat()
    except OSError as exc:
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
        raise RealSceneCaptureError("source media changed while copying")
    copied_bytes, copied_sha = _sha256_file(destination)
    if copied_bytes != expected_bytes or copied_sha != expected_sha256:
        raise RealSceneCaptureError("copied source media failed byte verification")


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
        raise RealSceneCaptureError(str(exc)) from exc

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
        raise RealSceneCaptureError(f"ingest failed: {exc}") from exc
    ingest_bytes = (ingest_root / MANIFEST_FILENAME).read_bytes()
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
            f"capture bundle preparation failed: {exc}"
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
            f"capture rights validation failed: {exc}"
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
            f"private media ingest failed: {exc}"
        ) from exc

    selected_root = boundary / "source"
    try:
        selected_root.mkdir()
    except OSError as exc:
        raise RealSceneCaptureError(
            f"cannot create private source copy: {exc}"
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
            f"private source copy differs from ingest evidence: {exc}"
        ) from exc
    if copied_ingest != ingest:
        raise RealSceneCaptureError(
            "private source copy changed the ingest manifest identity"
        )

    try:
        ingest_bytes = (ingest_root / MANIFEST_FILENAME).read_bytes()
    except OSError as exc:
        raise RealSceneCaptureError(
            f"cannot read private ingest evidence: {exc}"
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
            f"private capture bundle preparation failed: {exc}"
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
    payload = (model.model_dump_json(indent=2) + "\n").encode("utf-8")
    try:
        path.write_bytes(payload)
    except OSError as exc:
        raise RealSceneCaptureError(
            f"cannot write {path.name}: {exc}"
        ) from exc
    return payload


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
        raise RealSceneCaptureError(f"cannot create SfM boundary: {exc}") from exc
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
        raise RealSceneCaptureError(f"COLMAP registration failed: {exc}") from exc
    try:
        registration_bytes = registration_path.read_bytes()
        reparsed = RegistrationResult.model_validate_json(registration_bytes)
    except (OSError, ValueError) as exc:
        raise RealSceneCaptureError(
            f"registration evidence is unreadable: {exc}"
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
                f"COLMAP sparse model enumeration failed: {exc}"
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
    capture_manifest_bytes = capture_manifest_path.read_bytes()
    try:
        quality = build_registration_quality_report(
            registration=registration,
            registration_json_bytes=registration_bytes,
            capture_manifest=capture.capture.manifest,
            capture_manifest_bytes=capture_manifest_bytes,
            policy=policy,
            sparse_enumeration=sparse_enumeration,
            invocation_succeeded=True,
            engine_version=None,
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
            f"registration quality evidence is inconsistent: {exc}"
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
