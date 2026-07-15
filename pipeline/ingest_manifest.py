"""Strict, portable provenance contract for one successful ingest artifact."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

MANIFEST_FILENAME = "ingest_manifest.json"
SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
PHOTO_SOURCE_SUFFIXES = frozenset({
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp", ".bmp",
})
VIDEO_SOURCE_SUFFIXES = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv", ".flv", ".webm",
})
SUPPORTED_SOURCE_SUFFIXES = PHOTO_SOURCE_SUFFIXES | VIDEO_SOURCE_SUFFIXES

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PortablePath = Annotated[str, StringConstraints(min_length=1)]


class IngestArtifactError(ValueError):
    """The staged ingest tree cannot be trusted for publication."""


def sha256_file(path: str | Path) -> str:
    """Return a lowercase SHA-256 digest without loading the file into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_relative_path(value: str) -> str:
    reserved_windows_names = {
        "CON", "PRN", "AUX", "NUL", "CLOCK$",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    parts = value.split("/")
    invalid_windows_characters = frozenset('<>"|?*')
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or ":" in value
        or value.startswith("/")
        or "//" in value
        or any(part in {"", ".", ".."} for part in parts)
        or any(
            any(ord(character) < 32 or character in invalid_windows_characters
                for character in part)
            for part in parts
        )
        or any(part.endswith((" ", ".")) for part in parts)
        or any(part.split(".", 1)[0].upper() in reserved_windows_names for part in parts)
    ):
        raise ValueError("path must be a portable relative POSIX path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value:
        raise ValueError("path must be a portable relative POSIX path")
    return value


class IngestParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fps: float = Field(gt=0, le=30, allow_inf_nan=False)
    max_frames: int = Field(ge=1, le=10_000)
    blur_threshold: float = Field(ge=0, allow_inf_nan=False)
    max_long_edge: int = Field(ge=256, le=16_384)


class GpsObservation(BaseModel):
    """Raw EXIF position evidence; absent altitude remains unknown."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    altitude_m: float | None = Field(default=None, allow_inf_nan=False)


class FrameMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_path: PortablePath
    output_sha256: Sha256
    output_bytes: int = Field(ge=1)
    source_frame_index: int | None = Field(default=None, ge=0)
    preserves_source_bytes: bool

    _validate_output_path = field_validator("output_path")(_portable_relative_path)


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: PortablePath
    source_sha256: Sha256
    kind: Literal["photo", "video"]
    bytes: int = Field(ge=1)
    exif_datetime: str | None = None
    gps: GpsObservation | None = None
    exif_source: Literal["photo-exif", "none"] = "none"
    source_fps: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    duration_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    outputs: tuple[FrameMapping, ...]

    _validate_source_path = field_validator("source_path")(_portable_relative_path)

    @field_validator("exif_datetime")
    @classmethod
    def _validate_exif_datetime(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        try:
            parsed = datetime.strptime(normalized, "%Y:%m:%d %H:%M:%S")
            if parsed.strftime("%Y:%m:%d %H:%M:%S") != normalized:
                raise ValueError("non-canonical EXIF datetime")
        except ValueError as exc:
            raise ValueError(
                "EXIF datetime must use YYYY:MM:DD HH:MM:SS",
            ) from exc
        return normalized

    @model_validator(mode="after")
    def _validate_kind_contract(self) -> SourceRecord:
        suffix = PurePosixPath(self.source_path).suffix.lower()
        if not self.outputs:
            raise ValueError("every successful source requires at least one output")
        if self.exif_source == "none" and (
            self.exif_datetime is not None or self.gps is not None
        ):
            raise ValueError("EXIF values require exif_source=photo-exif")
        if self.exif_source == "photo-exif" and (
            self.exif_datetime is None and self.gps is None
        ):
            raise ValueError("photo-exif requires captured EXIF evidence")

        if self.kind == "photo":
            if suffix not in PHOTO_SOURCE_SUFFIXES:
                raise ValueError("photo source requires a supported photo suffix")
            if self.source_fps is not None or self.duration_s is not None:
                raise ValueError("photo sources cannot declare video timing")
            if len(self.outputs) != 1:
                raise ValueError("photo sources require exactly one copied output")
            output = self.outputs[0]
            if output.output_path != self.source_path:
                raise ValueError("photo output must use its deterministic source path")
            if output.source_frame_index is not None or not output.preserves_source_bytes:
                raise ValueError("photo output must be an EXIF-preserving copy")
            if output.output_sha256 != self.source_sha256 or output.output_bytes != self.bytes:
                raise ValueError("photo output bytes must exactly match its source")
        else:
            if suffix not in VIDEO_SOURCE_SUFFIXES:
                raise ValueError("video source requires a supported video suffix")
            if self.exif_source != "none" or self.exif_datetime is not None or self.gps is not None:
                raise ValueError("video frames cannot claim photo EXIF evidence")
            if self.source_fps is None or self.duration_s is None:
                raise ValueError("video source requires measured fps and duration")
            frame_indexes: list[int] = []
            for ordinal, output in enumerate(self.outputs):
                if output.source_frame_index is None or output.preserves_source_bytes:
                    raise ValueError("video output requires a source frame and no EXIF claim")
                expected_path = (
                    f"{self.source_path}.frames/frame_{ordinal:06d}.jpg"
                )
                if output.output_path != expected_path:
                    raise ValueError("video output path is not deterministic")
                frame_indexes.append(output.source_frame_index)
            if any(
                current >= following
                for current, following in zip(
                    frame_indexes, frame_indexes[1:], strict=False,
                )
            ):
                raise ValueError("video source frame indexes must be strictly increasing")
        return self


class IngestManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    session_id: str = Field(pattern=r"^ingest-[0-9a-f]{64}$")
    created_utc: datetime
    tool: Literal["pipeline.ingest"] = "pipeline.ingest"
    params: IngestParams
    sources: tuple[SourceRecord, ...]
    total_output_frames: int = Field(ge=1)

    @field_validator("created_utc")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_utc must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def _validate_manifest_contract(self) -> IngestManifest:
        if not self.sources:
            raise ValueError("successful ingest requires at least one source")
        source_paths = [source.source_path for source in self.sources]
        output_paths = [
            output.output_path for source in self.sources for output in source.outputs
        ]
        if len(source_paths) != len({path.casefold() for path in source_paths}):
            raise ValueError("source paths must be unique")
        if len(output_paths) != len({path.casefold() for path in output_paths}):
            raise ValueError("output paths must be unique")
        if self.total_output_frames != len(output_paths):
            raise ValueError("total_output_frames must equal declared outputs")
        for source in self.sources:
            if source.kind != "video":
                continue
            if len(source.outputs) > self.params.max_frames:
                raise ValueError("video outputs exceed params.max_frames")
            sampling_step = max(1, int(round(source.source_fps / self.params.fps)))
            if any(
                output.source_frame_index % sampling_step != 0
                for output in source.outputs
            ):
                raise ValueError("video frame does not match the declared sampling step")
        expected_session = derive_session_id(self.params, self.sources)
        if self.session_id != expected_session:
            raise ValueError("session_id does not match params and sources")
        return self


def derive_session_id(
    params: IngestParams,
    sources: tuple[SourceRecord, ...] | list[SourceRecord],
) -> str:
    """Derive a portable content ID from parameters and immutable source bytes."""

    payload = {
        "params": params.model_dump(mode="json"),
        "sources": sorted(
            (source.model_dump(mode="json") for source in sources),
            key=lambda source: source["source_path"],
        ),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "ingest-" + hashlib.sha256(encoded).hexdigest()


def build_manifest(
    *,
    created_utc: datetime,
    params: IngestParams,
    sources: tuple[SourceRecord, ...] | list[SourceRecord],
) -> IngestManifest:
    """Construct a manifest with its canonical session ID and output total."""

    frozen_sources = tuple(sources)
    return IngestManifest(
        session_id=derive_session_id(params, frozen_sources),
        created_utc=created_utc,
        params=params,
        sources=frozen_sources,
        total_output_frames=sum(len(source.outputs) for source in frozen_sources),
    )


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)()
    )


def _require_real_directory(raw_path: str | Path, *, label: str) -> Path:
    path = Path(raw_path).expanduser().absolute()
    if _is_linklike(path):
        raise IngestArtifactError(f"{label} must not be a symlink or junction")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IngestArtifactError(f"{label} directory is missing") from exc
    if resolved != path or not resolved.is_dir():
        raise IngestArtifactError(f"{label} must be a real directory")
    return resolved


def _scan_regular_files(root: Path, *, label: str) -> dict[str, Path]:
    files: dict[str, Path] = {}

    def scan_error(error: OSError) -> None:
        raise IngestArtifactError(f"{label} recursive scan failed") from error

    for directory, directory_names, file_names in os.walk(
        root, followlinks=False, onerror=scan_error
    ):
        parent = Path(directory)
        for name in [*directory_names, *file_names]:
            candidate = parent / name
            if _is_linklike(candidate):
                raise IngestArtifactError(f"{label} contains a symlink or junction")
        for name in file_names:
            candidate = parent / name
            if not candidate.is_file():
                raise IngestArtifactError(f"{label} contains a non-regular file")
            relative = candidate.relative_to(root).as_posix()
            files[relative] = candidate
    return files


def _load_bounded_manifest(path: Path) -> IngestManifest:
    if not path.is_file() or _is_linklike(path):
        raise IngestArtifactError("ingest manifest is missing")
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_MANIFEST_BYTES:
                raise IngestArtifactError("ingest manifest is too large")
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
            after = os.fstat(stream.fileno())
        path_after = path.stat()
    except IngestArtifactError:
        raise
    except OSError as exc:
        raise IngestArtifactError("ingest manifest cannot be read") from exc
    if len(payload) > MAX_MANIFEST_BYTES:
        raise IngestArtifactError("ingest manifest is too large")
    if (
        _stat_signature(before) != _stat_signature(after)
        # Windows can report slightly different ctime precision through an
        # open handle and a path lookup; identity, size, and mtime remain
        # directly comparable across the two views.
        or _stat_signature(after)[:-1] != _stat_signature(path_after)[:-1]
        or _is_linklike(path)
    ):
        raise IngestArtifactError("ingest manifest changed while being verified")
    try:
        return IngestManifest.model_validate_json(payload)
    except (OSError, UnicodeError, ValueError) as exc:
        raise IngestArtifactError("ingest manifest is invalid") from exc


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return identity and mutation evidence available on supported hosts."""

    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _verify_file(path: Path, *, expected_size: int, expected_sha256: str, label: str) -> None:
    try:
        if _is_linklike(path):
            raise IngestArtifactError(f"{label} must not be a symlink or junction")
        before = path.stat()
    except OSError as exc:
        raise IngestArtifactError(f"{label} is missing") from exc
    if before.st_size != expected_size:
        raise IngestArtifactError(f"{label} size does not match the manifest")
    try:
        actual_sha256 = sha256_file(path)
        after = path.stat()
    except OSError as exc:
        raise IngestArtifactError(f"{label} sha256 cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after) or _is_linklike(path):
        raise IngestArtifactError(f"{label} changed while being verified")
    if actual_sha256 != expected_sha256:
        raise IngestArtifactError(f"{label} sha256 does not match the manifest")


def verify_ingest_artifact(
    stage_dir: str | Path,
    *,
    input_dir: str | Path,
) -> IngestManifest:
    """Validate a successful staged artifact against live source and output bytes."""

    stage = _require_real_directory(stage_dir, label="stage")
    source_root = _require_real_directory(input_dir, label="input")
    stage_files = _scan_regular_files(stage, label="stage")
    source_files = _scan_regular_files(source_root, label="input")
    manifest = _load_bounded_manifest(stage / MANIFEST_FILENAME)

    declared_sources = {source.source_path for source in manifest.sources}
    live_sources = {
        relative
        for relative in source_files
        if PurePosixPath(relative).suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
    }
    if live_sources != declared_sources:
        raise IngestArtifactError("live supported source set does not match the manifest")

    declared_outputs = {
        output.output_path
        for source in manifest.sources
        for output in source.outputs
    }
    actual_outputs = set(stage_files) - {MANIFEST_FILENAME}
    missing = declared_outputs - actual_outputs
    extra = actual_outputs - declared_outputs
    if missing:
        raise IngestArtifactError("staged artifact is missing declared outputs")
    if extra:
        raise IngestArtifactError("staged artifact contains undeclared files")

    for source in manifest.sources:
        source_path = source_files.get(source.source_path)
        if source_path is None:
            raise IngestArtifactError("declared source is missing")
        _verify_file(
            source_path,
            expected_size=source.bytes,
            expected_sha256=source.source_sha256,
            label=f"source {source.source_path}",
        )
        for output in source.outputs:
            output_path = stage_files[output.output_path]
            _verify_file(
                output_path,
                expected_size=output.output_bytes,
                expected_sha256=output.output_sha256,
                label=f"output {output.output_path}",
            )
    return manifest
