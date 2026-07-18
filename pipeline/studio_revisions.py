"""Private immutable revision contracts for Nantai Studio.

This module derives capture evidence only from a verified ingest manifest plus
the measured SHA-256 of that manifest's exact file bytes.  It does not publish
bundles, expose private source metadata, or infer trust from names.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pipeline.ingest_manifest import IngestManifest, IngestParams, Sha256
from pipeline.studio_ledger import canonical_json


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
