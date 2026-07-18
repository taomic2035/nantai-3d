"""Private immutable revision contracts for Nantai Studio.

This module derives capture evidence only from a verified ingest manifest plus
the measured SHA-256 of that manifest's exact file bytes.  It does not publish
bundles, expose private source metadata, or infer trust from names.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pipeline.ingest_manifest import (
    IngestManifest,
    IngestParams,
    Sha256,
    sha256_file,
    verify_ingest_artifact,
)
from pipeline.studio_ledger import canonical_json

CAPTURE_MANIFEST_FILENAME = "manifest.json"
PRIVATE_INGEST_MANIFEST_FILENAME = "ingest_manifest.json"
MAX_PRIVATE_MANIFEST_BYTES = 4 * 1024 * 1024


class CaptureBundleError(ValueError):
    """A private capture bundle is incomplete, unsafe, or hash-damaged."""


@dataclass(frozen=True)
class PreparedCaptureBundle:
    """Verified immutable capture bytes before or after publication."""

    manifest: CaptureRevisionManifest
    manifest_digest: str
    bundle: Path


def _portable_capture_path(value: str) -> str:
    reserved_windows_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    invalid_windows_characters = frozenset('<>"|?*')
    parts = value.split("/")
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or ":" in value
        or value.startswith("/")
        or "//" in value
        or any(part in {"", ".", ".."} for part in parts)
        or any(
            any(
                ord(character) < 32
                or character in invalid_windows_characters
                for character in part
            )
            for part in parts
        )
        or any(part.endswith((" ", ".")) for part in parts)
        or any(
            part.split(".", 1)[0].upper() in reserved_windows_names
            for part in parts
        )
    ):
        raise ValueError("logical_path must be a portable relative POSIX path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value:
        raise ValueError("logical_path must be a portable relative POSIX path")
    return value


class CapturePayload(BaseModel):
    """One immutable image or extracted video frame in a capture bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_path: str
    sha256: Sha256
    byte_length: int = Field(ge=1)
    source_kind: Literal["photo", "video-frame"]
    source_ordinal: int = Field(ge=0)
    frame_index: int | None = Field(default=None, ge=0)

    _validate_logical_path = field_validator("logical_path")(
        _portable_capture_path,
    )

    @model_validator(mode="after")
    def _validate_frame_contract(self) -> CapturePayload:
        if self.source_kind == "photo" and self.frame_index is not None:
            raise ValueError("photo payload cannot declare a video frame index")
        if self.source_kind == "video-frame" and self.frame_index is None:
            raise ValueError("video-frame payload requires a frame index")
        return self


class CaptureRevisionManifest(BaseModel):
    """Private canonical manifest for one immutable CaptureRevision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["capture-revision"] = "capture-revision"
    revision_id: str = Field(pattern=r"^capture-[0-9a-f]{32}$")
    created_utc: datetime
    provenance: Literal["measured", "synthetic", "unknown"]
    synthetic: bool
    source_count: int = Field(ge=1)
    output_count: int = Field(ge=1)
    ingest_session_id: str = Field(pattern=r"^ingest-[0-9a-f]{64}$")
    ingest_manifest_sha256: Sha256
    ingest_parameters: IngestParams
    payloads: tuple[CapturePayload, ...]

    @field_validator("created_utc")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_utc must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def _validate_capture_contract(self) -> CaptureRevisionManifest:
        expected_provenance = "synthetic" if self.synthetic else "measured"
        if self.provenance != expected_provenance:
            raise ValueError(
                "capture provenance must agree with the synthetic flag",
            )
        if len(self.payloads) != self.output_count:
            raise ValueError("output_count must equal the payload count")
        logical_paths = [payload.logical_path.casefold() for payload in self.payloads]
        if len(logical_paths) != len(set(logical_paths)):
            raise ValueError("capture payload logical paths must be unique")
        source_ordinals = {payload.source_ordinal for payload in self.payloads}
        if source_ordinals != set(range(self.source_count)):
            raise ValueError(
                "payload source ordinals must cover every declared source",
            )
        source_kinds: dict[int, str] = {}
        for payload in self.payloads:
            previous = source_kinds.setdefault(
                payload.source_ordinal,
                payload.source_kind,
            )
            if previous != payload.source_kind:
                raise ValueError(
                    "one capture source cannot mix photo and video-frame payloads",
                )
        return self


def build_capture_manifest(
    *,
    revision_id: str,
    ingest: IngestManifest,
    ingest_manifest_sha256: str,
    synthetic: bool,
    created_utc: datetime,
) -> CaptureRevisionManifest:
    """Derive a capture manifest from verified ingest evidence.

    ``ingest_manifest_sha256`` must be measured from the exact verified file.
    Re-serializing ``ingest`` is intentionally not accepted as byte evidence.
    """

    if not isinstance(ingest, IngestManifest):
        raise TypeError("ingest must be a verified IngestManifest")
    payloads = tuple(
        CapturePayload(
            logical_path=output.output_path,
            sha256=output.output_sha256,
            byte_length=output.output_bytes,
            source_kind=(
                "photo" if source.kind == "photo" else "video-frame"
            ),
            source_ordinal=source_ordinal,
            frame_index=output.source_frame_index,
        )
        for source_ordinal, source in enumerate(ingest.sources)
        for output in source.outputs
    )
    return CaptureRevisionManifest(
        revision_id=revision_id,
        created_utc=created_utc,
        provenance="synthetic" if synthetic else "measured",
        synthetic=synthetic,
        source_count=len(ingest.sources),
        output_count=len(payloads),
        ingest_session_id=ingest.session_id,
        ingest_manifest_sha256=ingest_manifest_sha256,
        ingest_parameters=ingest.params,
        payloads=payloads,
    )


def canonical_manifest_bytes(manifest: CaptureRevisionManifest) -> bytes:
    """Return deterministic ASCII JSON with exactly one trailing LF."""

    if not isinstance(manifest, CaptureRevisionManifest):
        raise TypeError("manifest must be a CaptureRevisionManifest")
    value = manifest.model_dump(mode="json")
    return (canonical_json(value) + "\n").encode("ascii")


def capture_manifest_digest(manifest: CaptureRevisionManifest) -> str:
    """Return the content identity of the canonical private manifest."""

    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)(),
    )


def _require_real_directory(path: Path, *, label: str) -> Path:
    absolute = path.expanduser().absolute()
    if _is_linklike(absolute):
        raise CaptureBundleError(f"{label} must not be a symlink or junction")
    try:
        resolved = absolute.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CaptureBundleError(f"{label} directory is missing") from exc
    if resolved != absolute or not absolute.is_dir():
        raise CaptureBundleError(f"{label} must be a real directory")
    return absolute


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _read_stable_bytes(
    path: Path,
    *,
    label: str,
    maximum: int,
) -> bytes:
    try:
        if _is_linklike(path) or not path.is_file():
            raise CaptureBundleError(f"{label} is missing or link-like")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > maximum:
                raise CaptureBundleError(f"{label} is too large")
            payload = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        path_after = path.stat()
    except CaptureBundleError:
        raise
    except OSError as exc:
        raise CaptureBundleError(f"{label} cannot be read") from exc
    if (
        len(payload) > maximum
        or _stat_signature(before) != _stat_signature(after)
        or _stat_signature(after) != _stat_signature(path_after)
        or _is_linklike(path)
    ):
        raise CaptureBundleError(f"{label} changed while being read")
    return payload


def _verify_regular_file(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    label: str,
) -> None:
    try:
        if _is_linklike(path) or not path.is_file():
            raise CaptureBundleError(f"{label} is missing or link-like")
        before = path.stat()
        digest = sha256_file(path)
        after = path.stat()
    except CaptureBundleError:
        raise
    except OSError as exc:
        raise CaptureBundleError(f"{label} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after) or _is_linklike(path):
        raise CaptureBundleError(f"{label} changed while being hashed")
    if after.st_size != expected_size:
        raise CaptureBundleError(f"{label} size does not match its manifest")
    if digest != expected_sha256:
        raise CaptureBundleError(f"{label} hash does not match its manifest")


def _scan_bundle_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}

    def scan_error(error: OSError) -> None:
        raise CaptureBundleError("capture bundle recursive scan failed") from error

    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
        onerror=scan_error,
    ):
        parent = Path(directory)
        for name in [*directory_names, *file_names]:
            candidate = parent / name
            if _is_linklike(candidate):
                raise CaptureBundleError(
                    "capture bundle contains a symlink or junction",
                )
        for name in file_names:
            candidate = parent / name
            if not candidate.is_file():
                raise CaptureBundleError(
                    "capture bundle contains a non-regular file",
                )
            files[candidate.relative_to(root).as_posix()] = candidate
    return files


def prepare_capture_bundle(
    *,
    stage_dir: str | Path,
    input_dir: str | Path,
    bundle_dir: str | Path,
    revision_id: str,
    synthetic: bool,
    created_utc: datetime,
) -> PreparedCaptureBundle:
    """Build one absent-only private bundle from a verified ingest stage."""

    stage = _require_real_directory(Path(stage_dir), label="ingest stage")
    bundle = Path(bundle_dir).expanduser().absolute()
    _require_real_directory(bundle.parent, label="capture work parent")
    if bundle.exists() or _is_linklike(bundle):
        raise CaptureBundleError("capture work bundle must be absent")

    try:
        ingest = verify_ingest_artifact(stage, input_dir=input_dir)
    except Exception as exc:
        raise CaptureBundleError("ingest stage verification failed") from exc
    ingest_path = stage / PRIVATE_INGEST_MANIFEST_FILENAME
    ingest_bytes = _read_stable_bytes(
        ingest_path,
        label="verified ingest manifest",
        maximum=MAX_PRIVATE_MANIFEST_BYTES,
    )
    try:
        exact_ingest = IngestManifest.model_validate_json(ingest_bytes)
    except ValueError as exc:
        raise CaptureBundleError("verified ingest manifest is invalid") from exc
    if exact_ingest != ingest:
        raise CaptureBundleError(
            "verified ingest manifest changed after stage verification",
        )

    bundle.mkdir(exist_ok=False)
    payload_root = bundle / "payload"
    payload_root.mkdir(exist_ok=False)
    (bundle / PRIVATE_INGEST_MANIFEST_FILENAME).write_bytes(ingest_bytes)
    for source in ingest.sources:
        for output in source.outputs:
            source_path = stage / PurePosixPath(output.output_path)
            destination = payload_root / PurePosixPath(output.output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _verify_regular_file(
                source_path,
                expected_size=output.output_bytes,
                expected_sha256=output.output_sha256,
                label=f"staged payload {output.output_path}",
            )
            shutil.copyfile(source_path, destination, follow_symlinks=False)
            _verify_regular_file(
                destination,
                expected_size=output.output_bytes,
                expected_sha256=output.output_sha256,
                label=f"copied payload {output.output_path}",
            )

    manifest = build_capture_manifest(
        revision_id=revision_id,
        ingest=ingest,
        ingest_manifest_sha256=hashlib.sha256(ingest_bytes).hexdigest(),
        synthetic=synthetic,
        created_utc=created_utc,
    )
    (bundle / CAPTURE_MANIFEST_FILENAME).write_bytes(
        canonical_manifest_bytes(manifest),
    )
    return verify_capture_bundle(bundle)


def verify_capture_bundle(
    bundle_dir: str | Path,
) -> PreparedCaptureBundle:
    """Verify every declared byte of an immutable private capture bundle."""

    bundle = _require_real_directory(
        Path(bundle_dir),
        label="capture bundle",
    )
    files = _scan_bundle_files(bundle)
    manifest_bytes = _read_stable_bytes(
        bundle / CAPTURE_MANIFEST_FILENAME,
        label="capture manifest",
        maximum=MAX_PRIVATE_MANIFEST_BYTES,
    )
    try:
        manifest = CaptureRevisionManifest.model_validate_json(manifest_bytes)
    except ValueError as exc:
        raise CaptureBundleError("capture manifest is invalid") from exc
    if manifest_bytes != canonical_manifest_bytes(manifest):
        raise CaptureBundleError("capture manifest is not canonical")

    ingest_bytes = _read_stable_bytes(
        bundle / PRIVATE_INGEST_MANIFEST_FILENAME,
        label="private ingest manifest",
        maximum=MAX_PRIVATE_MANIFEST_BYTES,
    )
    if hashlib.sha256(ingest_bytes).hexdigest() != (
        manifest.ingest_manifest_sha256
    ):
        raise CaptureBundleError("private ingest manifest hash does not match")
    try:
        ingest = IngestManifest.model_validate_json(ingest_bytes)
    except ValueError as exc:
        raise CaptureBundleError("private ingest manifest is invalid") from exc
    expected_manifest = build_capture_manifest(
        revision_id=manifest.revision_id,
        ingest=ingest,
        ingest_manifest_sha256=manifest.ingest_manifest_sha256,
        synthetic=manifest.synthetic,
        created_utc=manifest.created_utc,
    )
    if expected_manifest != manifest:
        raise CaptureBundleError(
            "capture manifest does not match embedded ingest evidence",
        )

    declared = {
        CAPTURE_MANIFEST_FILENAME,
        PRIVATE_INGEST_MANIFEST_FILENAME,
        *(
            f"payload/{payload.logical_path}"
            for payload in manifest.payloads
        ),
    }
    actual = set(files)
    if actual != declared:
        raise CaptureBundleError(
            "capture bundle contains missing or undeclared files",
        )
    for payload in manifest.payloads:
        _verify_regular_file(
            files[f"payload/{payload.logical_path}"],
            expected_size=payload.byte_length,
            expected_sha256=payload.sha256,
            label=f"capture payload {payload.logical_path}",
        )
    if _read_stable_bytes(
        bundle / CAPTURE_MANIFEST_FILENAME,
        label="capture manifest",
        maximum=MAX_PRIVATE_MANIFEST_BYTES,
    ) != manifest_bytes:
        raise CaptureBundleError(
            "capture manifest changed during bundle verification",
        )
    if _read_stable_bytes(
        bundle / PRIVATE_INGEST_MANIFEST_FILENAME,
        label="private ingest manifest",
        maximum=MAX_PRIVATE_MANIFEST_BYTES,
    ) != ingest_bytes:
        raise CaptureBundleError(
            "private ingest manifest changed during bundle verification",
        )
    return PreparedCaptureBundle(
        manifest=manifest,
        manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
        bundle=bundle,
    )
