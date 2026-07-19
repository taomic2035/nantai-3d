"""Fail-closed private source packs for H3 AI-authored material candidates.

The prompt and response metadata describe a generation request.  Trust starts
at captured bytes: every candidate is read, hashed, decoded, and compared with
its private selection receipt before one selected object per slot is published.
The published pack remains synthetic preview evidence and never authorizes a
public release.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from pipeline.studio_jobs import (
    JobContractError,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GeneratorVersion = Annotated[str, StringConstraints(min_length=1)]

H3_SOURCE_PACK_SCHEMA = "nantai.h3-ai-material-source-pack.v1"
H3_SELECTION_RECEIPT_SCHEMA = (
    "nantai.h3-ai-material-selection-receipt.v1"
)
H3_GENERATION_POLICY_ID = "h3-ai-material-candidates-v1"
H3_CANDIDATE_AUDIT_ALGORITHM_ID = "h3-candidate-pixel-audit-v1"
H3_SOURCE_PACK_MANIFEST = "manifest.json"
H3_HERO_SLOTS = (
    "material-weathered-timber-01",
    "material-dark-timber-01",
    "material-gray-roof-tile-01",
    "material-fieldstone-01",
    "material-dry-stone-wall-01",
    "material-rammed-earth-01",
    "material-packed-earth-01",
    "material-terrace-soil-01",
)

MAX_SELECTION_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_SOURCE_PACK_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_NATIVE_SOURCE_BYTES = 128 * 1024 * 1024
MAX_NATIVE_SOURCE_PIXELS = 64 * 1024 * 1024
MIN_NATIVE_SOURCE_DIMENSION = 1024


class H3MaterialSourceError(ValueError):
    """An H3 source receipt or published source pack cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _portable_relative_path(value: str, *, label: str) -> str:
    parsed = PurePosixPath(value)
    if (
        "\\" in value
        or value.startswith("/")
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError(f"{label} must be a portable relative POSIX path")
    return value


class CandidateAudit(FrozenModel):
    algorithm_id: Literal[
        "h3-candidate-pixel-audit-v1"
    ] = H3_CANDIDATE_AUDIT_ALGORITHM_ID
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    colour_mode: Literal["RGB", "RGBA"]
    alpha_nonopaque_fraction: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    clipped_fraction: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    contrast_stddev: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    edge_energy: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    dominant_perspective_score: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    opposite_edge_disagreement: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )


class CandidateAuditPolicy(FrozenModel):
    policy_id: Literal[
        "h3-ai-candidate-audit-policy-v1"
    ] = "h3-ai-candidate-audit-policy-v1"
    algorithm_id: Literal[
        "h3-candidate-pixel-audit-v1"
    ] = H3_CANDIDATE_AUDIT_ALGORITHM_ID
    minimum_width: Literal[1024] = 1024
    minimum_height: Literal[1024] = 1024
    maximum_alpha_nonopaque_fraction: Literal[0.0] = 0.0
    maximum_clipped_fraction: float = Field(
        ge=0.0,
        le=0.02,
        allow_inf_nan=False,
    )
    maximum_dominant_perspective_score: float = Field(
        ge=0.0,
        le=0.35,
        allow_inf_nan=False,
    )
    maximum_edge_energy: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    maximum_opposite_edge_disagreement: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    frozen_before_selection: Literal[True] = True


class CandidateDescriptor(FrozenModel):
    source_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1, le=MAX_NATIVE_SOURCE_BYTES)
    width: int = Field(ge=MIN_NATIVE_SOURCE_DIMENSION)
    height: int = Field(ge=MIN_NATIVE_SOURCE_DIMENSION)
    media_type: Literal["image/png"] = "image/png"
    audit: CandidateAudit

    @field_validator("source_path")
    @classmethod
    def _source_path_is_portable(cls, value: str) -> str:
        value = _portable_relative_path(value, label="candidate source_path")
        if PurePosixPath(value).suffix.lower() != ".png":
            raise ValueError("candidate source_path must end in .png")
        return value


class CandidateSelection(FrozenModel):
    selected_candidate_sha256: Sha256
    review_kind: Literal["human-visual-review"] = "human-visual-review"
    review_reason: str = Field(min_length=20)
    trust_effect: Literal["none-appearance-only"] = "none-appearance-only"


class RightsReview(FrozenModel):
    status: Literal["private-project-use-only"] = "private-project-use-only"
    evidence: Literal[
        "user-approved-ai-generation-workflow"
    ] = "user-approved-ai-generation-workflow"
    public_release_authorized: Literal[False] = False

    @field_validator("public_release_authorized", mode="before")
    @classmethod
    def _public_release_stays_unauthorized(cls, value: object) -> object:
        if value is not False:
            raise ValueError("public release authorization must remain false")
        return value


class SelectionReceiptRecord(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    prompt: str = Field(min_length=40)
    prompt_sha256: Sha256
    generator_product: Literal[
        "openai-image-generation"
    ] = "openai-image-generation"
    generator_version: GeneratorVersion | None
    generator_version_evidence: Literal[
        "generation-response",
        "not-exposed-by-generation-response",
    ]
    candidates: tuple[CandidateDescriptor, ...] = Field(
        min_length=3,
        max_length=3,
    )
    selection: CandidateSelection
    rights_review: RightsReview

    @field_validator("candidates", mode="before")
    @classmethod
    def _exact_candidate_count(cls, value: object) -> object:
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError("candidates must contain exactly 3 items")
        return tuple(value)

    @field_validator("generator_version_evidence", mode="before")
    @classmethod
    def _known_generator_version_evidence(cls, value: object) -> object:
        if value not in {
            "generation-response",
            "not-exposed-by-generation-response",
        }:
            raise ValueError("generator version evidence is not recognized")
        return value

    @model_validator(mode="after")
    def _receipt_record_is_self_consistent(self) -> SelectionReceiptRecord:
        if hashlib.sha256(self.prompt.encode("utf-8")).hexdigest() != (
            self.prompt_sha256
        ):
            raise ValueError("prompt SHA-256 disagrees with the complete prompt")
        if self.generator_version is None:
            if (
                self.generator_version_evidence
                != "not-exposed-by-generation-response"
            ):
                raise ValueError(
                    "generator version evidence must declare response absence",
                )
        elif self.generator_version_evidence != "generation-response":
            raise ValueError(
                "generator version evidence must identify the generation response",
            )
        candidate_hashes = [candidate.sha256 for candidate in self.candidates]
        if len(candidate_hashes) != len(set(candidate_hashes)):
            raise ValueError("candidate SHA-256 values must be unique")
        if self.selection.selected_candidate_sha256 not in candidate_hashes:
            raise ValueError("selected candidate SHA-256 is not in candidates")
        return self


class SelectionReceipt(FrozenModel):
    schema_version: Literal[
        "nantai.h3-ai-material-selection-receipt.v1"
    ] = H3_SELECTION_RECEIPT_SCHEMA
    generation_policy_id: Literal[
        "h3-ai-material-candidates-v1"
    ] = H3_GENERATION_POLICY_ID
    synthetic: Literal[True] = True
    ai_generated: Literal[True] = True
    real_photo_textures: Literal[False] = False
    audit_policy: CandidateAuditPolicy
    records: tuple[SelectionReceiptRecord, ...]

    @model_validator(mode="after")
    def _records_are_exact_and_ordered(self) -> SelectionReceipt:
        slot_ids = tuple(record.slot_id for record in self.records)
        if slot_ids != H3_HERO_SLOTS:
            raise ValueError(
                "selection receipt must contain the exact ordered H3 hero slots",
            )
        return self


class NativeSourceDescriptor(FrozenModel):
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1, le=MAX_NATIVE_SOURCE_BYTES)
    width: int = Field(ge=MIN_NATIVE_SOURCE_DIMENSION)
    height: int = Field(ge=MIN_NATIVE_SOURCE_DIMENSION)
    media_type: Literal["image/png"] = "image/png"
    mode: Literal["RGB", "RGBA"]

    @field_validator("object_path")
    @classmethod
    def _object_path_is_content_addressed(cls, value: str) -> str:
        return _portable_relative_path(value, label="native source object_path")

    @model_validator(mode="after")
    def _path_matches_sha(self) -> NativeSourceDescriptor:
        if self.object_path != f"sources/{self.sha256}.png":
            raise ValueError(
                "native source object_path must be content-addressed by SHA-256",
            )
        return self


class PublishedSelection(FrozenModel):
    candidate_count: Literal[3] = 3
    selected_candidate_sha256: Sha256
    review_kind: Literal["human-visual-review"] = "human-visual-review"
    review_reason: str = Field(min_length=20)
    trust_effect: Literal["none-appearance-only"] = "none-appearance-only"


class H3SourceRecord(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    prompt: str = Field(min_length=40)
    prompt_sha256: Sha256
    generator_product: Literal[
        "openai-image-generation"
    ] = "openai-image-generation"
    generator_version: GeneratorVersion | None
    generator_version_evidence: Literal[
        "generation-response",
        "not-exposed-by-generation-response",
    ]
    native_source: NativeSourceDescriptor
    selection: PublishedSelection
    rights_review: RightsReview

    @model_validator(mode="after")
    def _record_is_self_consistent(self) -> H3SourceRecord:
        if hashlib.sha256(self.prompt.encode("utf-8")).hexdigest() != (
            self.prompt_sha256
        ):
            raise ValueError("prompt SHA-256 disagrees with the complete prompt")
        if (
            self.selection.selected_candidate_sha256
            != self.native_source.sha256
        ):
            raise ValueError("selected candidate disagrees with native source")
        if self.generator_version is None:
            if (
                self.generator_version_evidence
                != "not-exposed-by-generation-response"
            ):
                raise ValueError(
                    "generator version evidence must declare response absence",
                )
        elif self.generator_version_evidence != "generation-response":
            raise ValueError(
                "generator version evidence must identify the generation response",
            )
        return self


class H3MaterialSourcePack(FrozenModel):
    schema_version: Literal[
        "nantai.h3-ai-material-source-pack.v1"
    ] = H3_SOURCE_PACK_SCHEMA
    source_pack_id: Sha256
    synthetic: Literal[True] = True
    ai_generated: Literal[True] = True
    real_photo_textures: Literal[False] = False
    geometry_usability: Literal["preview-only"] = "preview-only"
    metric_alignment: Literal[False] = False
    verification_level: Literal["L0"] = "L0"
    generation_policy_id: Literal[
        "h3-ai-material-candidates-v1"
    ] = H3_GENERATION_POLICY_ID
    records: tuple[H3SourceRecord, ...]

    @model_validator(mode="after")
    def _records_are_exact_and_ordered(self) -> H3MaterialSourcePack:
        slot_ids = tuple(record.slot_id for record in self.records)
        if slot_ids != H3_HERO_SLOTS:
            raise ValueError(
                "source pack must contain the exact ordered H3 hero slots",
            )
        return self


@dataclass(frozen=True)
class PreparedH3MaterialSourcePack:
    root: Path
    manifest: H3MaterialSourcePack


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_h3_source_pack_bytes(
    pack: H3MaterialSourcePack,
    *,
    exclude_source_pack_id: bool = False,
) -> bytes:
    payload = pack.model_dump(mode="json")
    if exclude_source_pack_id:
        payload.pop("source_pack_id")
    return _canonical_json_bytes(payload)


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise H3MaterialSourceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_stable_bytes(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    if _is_linklike(path) or not path.is_file():
        raise H3MaterialSourceError(f"{label} must be a regular file")
    try:
        before = path.stat()
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise H3MaterialSourceError(f"{label} size is invalid")
        with path.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
        after = path.stat()
    except OSError as exc:
        raise H3MaterialSourceError(f"{label} cannot be read") from exc
    if (
        len(payload) != before.st_size
        or len(payload) > maximum_bytes
        or _stat_signature(before) != _stat_signature(after)
    ):
        raise H3MaterialSourceError(f"{label} changed during bounded read")
    return payload


def _bounded_metric(value: float) -> float:
    return round(float(np.clip(value, 0.0, 1.0)), 8)


def _audit_candidate_payload(payload: bytes) -> CandidateAudit:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise H3MaterialSourceError(
                        "H3 candidate audit requires a PNG",
                    )
                width, height = image.size
                colour_mode = image.mode
                if colour_mode not in {"RGB", "RGBA"}:
                    raise H3MaterialSourceError(
                        "H3 candidate audit requires RGB or RGBA",
                    )
                rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except H3MaterialSourceError:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise H3MaterialSourceError(
            "H3 candidate audit could not decode the PNG",
        ) from exc
    if width <= 0 or height <= 0 or width * height > MAX_NATIVE_SOURCE_PIXELS:
        raise H3MaterialSourceError(
            "H3 candidate audit dimensions are invalid",
        )

    rgb = rgba[..., :3].astype(np.float32) / 255.0
    alpha = rgba[..., 3]
    luminance = (
        rgb[..., 0] * 0.2126
        + rgb[..., 1] * 0.7152
        + rgb[..., 2] * 0.0722
    )
    horizontal = np.abs(np.diff(luminance, axis=1))
    vertical = np.abs(np.diff(luminance, axis=0))
    edge_energy = (
        float(horizontal.mean()) + float(vertical.mean())
    ) * 0.5

    quarter_height = max(1, height // 4)
    quarter_width = max(1, width // 4)
    top_energy = float(horizontal[:quarter_height].mean())
    bottom_energy = float(horizontal[-quarter_height:].mean())
    left_energy = float(vertical[:, :quarter_width].mean())
    right_energy = float(vertical[:, -quarter_width:].mean())
    spatial_peak = max(
        top_energy,
        bottom_energy,
        left_energy,
        right_energy,
        1.0 / 255.0,
    )
    dominant_perspective_score = max(
        abs(top_energy - bottom_energy),
        abs(left_energy - right_energy),
    ) / spatial_peak

    row_disagreement = float(
        np.abs(rgb[0].astype(np.float32) - rgb[-1]).mean(),
    )
    column_disagreement = float(
        np.abs(rgb[:, 0].astype(np.float32) - rgb[:, -1]).mean(),
    )
    opposite_edge_disagreement = (
        row_disagreement + column_disagreement
    ) * 0.5
    clipped_fraction = float(
        np.logical_or(rgb <= (2.0 / 255.0), rgb >= (253.0 / 255.0)).mean(),
    )
    return CandidateAudit(
        width=width,
        height=height,
        colour_mode=colour_mode,
        alpha_nonopaque_fraction=_bounded_metric(
            float(np.count_nonzero(alpha != 255)) / float(alpha.size),
        ),
        clipped_fraction=_bounded_metric(clipped_fraction),
        contrast_stddev=_bounded_metric(float(luminance.std())),
        edge_energy=_bounded_metric(edge_energy),
        dominant_perspective_score=_bounded_metric(
            dominant_perspective_score,
        ),
        opposite_edge_disagreement=_bounded_metric(
            opposite_edge_disagreement,
        ),
    )


def audit_h3_candidate(path: Path) -> CandidateAudit:
    """Measure deterministic pixel evidence without selecting a candidate."""

    payload = _read_stable_bytes(
        Path(path),
        maximum_bytes=MAX_NATIVE_SOURCE_BYTES,
        label="H3 candidate",
    )
    return _audit_candidate_payload(payload)


def _read_selection_receipt(path: Path) -> SelectionReceipt:
    path = Path(path)
    raw = _read_stable_bytes(
        path,
        maximum_bytes=MAX_SELECTION_RECEIPT_BYTES,
        label="selection receipt",
    )
    try:
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                H3MaterialSourceError(
                    f"selection receipt contains non-finite JSON: {value}",
                ),
            ),
        )
        receipt = SelectionReceipt.model_validate_json(raw)
    except H3MaterialSourceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise H3MaterialSourceError(
            f"selection receipt validation failed: {exc}",
        ) from exc
    if raw != _canonical_json_bytes(receipt.model_dump(mode="json")):
        raise H3MaterialSourceError("selection receipt is not canonical JSON")
    return receipt


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)(),
    )


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _require_real_directory(path: Path, *, label: str) -> Path:
    if _is_linklike(path):
        raise H3MaterialSourceError(
            f"{label} must not be a symlink or junction",
        )
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise H3MaterialSourceError(f"{label} is not a real path") from exc
    if not path.is_dir() or not _same_path(resolved, path.absolute()):
        raise H3MaterialSourceError(
            f"{label} must be a real path without redirected ancestors",
        )
    return path


def _prepare_real_directory(raw_path: Path, *, label: str) -> Path:
    path = Path(raw_path).expanduser().absolute()
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        if _is_linklike(cursor):
            raise H3MaterialSourceError(
                f"{label} ancestor must not be a symlink or junction",
            )
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise H3MaterialSourceError(
                f"{label} has no existing real ancestor",
            )
        cursor = parent
    _require_real_directory(cursor, label=f"{label} ancestor")
    for directory in reversed(missing):
        try:
            directory.mkdir(exist_ok=False)
            _flush_directory(directory.parent)
        except FileExistsError:
            pass
        _require_real_directory(directory, label=label)
    return _require_real_directory(path, label=label)


def _stat_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _flush_file(path: Path) -> None:
    if os.name == "nt":
        WindowsNtfsDurabilityBackend.flush_file(path)
        return
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _flush_directory(path: Path) -> None:
    if os.name == "nt":
        WindowsNtfsDurabilityBackend.flush_directory(path)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_candidate_image(
    payload: bytes,
    *,
    descriptor: CandidateDescriptor | NativeSourceDescriptor,
    label: str,
) -> Literal["RGB", "RGBA"]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise H3MaterialSourceError(
                        f"{label} decoded format must be PNG",
                    )
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
                mode = image.mode
                text_metadata = dict(getattr(image, "text", {}))
                if mode == "RGBA":
                    alpha = image.getchannel("A")
                    alpha_range = alpha.getextrema()
                else:
                    alpha_range = None
    except H3MaterialSourceError:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise H3MaterialSourceError(f"{label} is not a valid PNG") from exc
    if width * height > MAX_NATIVE_SOURCE_PIXELS:
        raise H3MaterialSourceError(f"{label} exceeds the pixel limit")
    if (width, height) != (descriptor.width, descriptor.height):
        raise H3MaterialSourceError(
            f"{label} dimensions disagree with its descriptor",
        )
    if mode not in {"RGB", "RGBA"}:
        raise H3MaterialSourceError(f"{label} must decode as RGB or RGBA")
    if alpha_range is not None and alpha_range != (255, 255):
        raise H3MaterialSourceError(f"{label} alpha must be fully opaque")
    if text_metadata:
        raise H3MaterialSourceError(
            f"{label} must not contain PNG text metadata",
        )
    if isinstance(descriptor, NativeSourceDescriptor) and (
        descriptor.mode != mode
    ):
        raise H3MaterialSourceError(
            f"{label} colour mode disagrees with its descriptor",
        )
    return mode


def _verify_candidate_audit_policy(
    audit: CandidateAudit,
    policy: CandidateAuditPolicy,
    *,
    label: str,
) -> None:
    if (
        audit.width < policy.minimum_width
        or audit.height < policy.minimum_height
    ):
        raise H3MaterialSourceError(
            f"{label} is below the frozen minimum dimensions",
        )
    if (
        audit.alpha_nonopaque_fraction
        > policy.maximum_alpha_nonopaque_fraction
    ):
        raise H3MaterialSourceError(
            f"{label} must be fully opaque under the frozen audit policy",
        )
    if audit.clipped_fraction > policy.maximum_clipped_fraction:
        raise H3MaterialSourceError(
            f"{label} exceeds the frozen clipping threshold",
        )
    if (
        audit.dominant_perspective_score
        > policy.maximum_dominant_perspective_score
    ):
        raise H3MaterialSourceError(
            f"{label} exceeds the frozen perspective threshold",
        )
    if audit.edge_energy > policy.maximum_edge_energy:
        raise H3MaterialSourceError(
            f"{label} exceeds the frozen edge-energy threshold",
        )
    if (
        audit.opposite_edge_disagreement
        > policy.maximum_opposite_edge_disagreement
    ):
        raise H3MaterialSourceError(
            f"{label} exceeds the frozen opposite-edge threshold",
        )


def _candidate_path(receipt_path: Path, relative_path: str) -> Path:
    root = _require_real_directory(
        receipt_path.parent.absolute(),
        label="selection receipt directory",
    )
    candidate = root / Path(relative_path)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise H3MaterialSourceError(
            "candidate source_path does not resolve to a real file",
        ) from exc
    if root not in resolved.parents:
        raise H3MaterialSourceError(
            "candidate source_path escapes the selection receipt directory",
        )
    if not _same_path(resolved, candidate.absolute()):
        raise H3MaterialSourceError(
            "candidate source_path must not traverse a redirected ancestor",
        )
    return candidate


def _verified_candidates(
    receipt_path: Path,
    receipt: SelectionReceipt,
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for record in receipt.records:
        for index, descriptor in enumerate(record.candidates, start=1):
            path = _candidate_path(receipt_path, descriptor.source_path)
            payload = _read_stable_bytes(
                path,
                maximum_bytes=MAX_NATIVE_SOURCE_BYTES,
                label=f"{record.slot_id} candidate {index}",
            )
            if len(payload) != descriptor.bytes:
                raise H3MaterialSourceError(
                    f"{record.slot_id} candidate {index} byte count disagrees",
                )
            digest = hashlib.sha256(payload).hexdigest()
            if digest != descriptor.sha256:
                raise H3MaterialSourceError(
                    f"{record.slot_id} candidate {index} SHA-256 disagrees",
                )
            _verify_candidate_image(
                payload,
                descriptor=descriptor,
                label=f"{record.slot_id} candidate {index}",
            )
            measured_audit = _audit_candidate_payload(payload)
            if measured_audit != descriptor.audit:
                raise H3MaterialSourceError(
                    f"{record.slot_id} candidate {index} audit disagrees",
                )
            _verify_candidate_audit_policy(
                measured_audit,
                receipt.audit_policy,
                label=f"{record.slot_id} candidate {index}",
            )
            payloads[digest] = payload
    if len(payloads) != len(H3_HERO_SLOTS) * 3:
        raise H3MaterialSourceError(
            "candidate source bytes must be unique across all H3 slots",
        )
    return payloads


def _pack_from_receipt(
    receipt: SelectionReceipt,
    candidate_payloads: dict[str, bytes],
) -> H3MaterialSourcePack:
    records = []
    for receipt_record in receipt.records:
        selected_sha = receipt_record.selection.selected_candidate_sha256
        selected = next(
            descriptor
            for descriptor in receipt_record.candidates
            if descriptor.sha256 == selected_sha
        )
        payload = candidate_payloads[selected_sha]
        selected_mode = _verify_candidate_image(
            payload,
            descriptor=selected,
            label=f"{receipt_record.slot_id} selected candidate",
        )
        records.append(
            H3SourceRecord(
                slot_id=receipt_record.slot_id,
                prompt=receipt_record.prompt,
                prompt_sha256=receipt_record.prompt_sha256,
                generator_product=receipt_record.generator_product,
                generator_version=receipt_record.generator_version,
                generator_version_evidence=(
                    receipt_record.generator_version_evidence
                ),
                native_source=NativeSourceDescriptor(
                    object_path=f"sources/{selected.sha256}.png",
                    sha256=selected.sha256,
                    bytes=len(payload),
                    width=selected.width,
                    height=selected.height,
                    media_type="image/png",
                    mode=selected_mode,
                ),
                selection=PublishedSelection(
                    candidate_count=3,
                    selected_candidate_sha256=selected.sha256,
                    review_kind=receipt_record.selection.review_kind,
                    review_reason=receipt_record.selection.review_reason,
                    trust_effect=receipt_record.selection.trust_effect,
                ),
                rights_review=receipt_record.rights_review,
            ),
        )
    provisional = H3MaterialSourcePack(
        source_pack_id="0" * 64,
        records=tuple(records),
    )
    source_pack_id = hashlib.sha256(
        canonical_h3_source_pack_bytes(
            provisional,
            exclude_source_pack_id=True,
        ),
    ).hexdigest()
    return provisional.model_copy(
        update={"source_pack_id": source_pack_id},
    )


def _write_payload(path: Path, payload: bytes) -> None:
    if _is_linklike(path) or path.exists():
        raise H3MaterialSourceError(
            "staged source-pack payload path must start absent",
        )
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _directory_closure(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
        ),
    )


def load_h3_source_pack(root: Path) -> H3MaterialSourcePack:
    root = Path(root).expanduser().absolute()
    try:
        _require_real_directory(root, label="H3 source pack")
        sources_root = _require_real_directory(
            root / "sources",
            label="H3 source directory",
        )
        manifest_path = root / H3_SOURCE_PACK_MANIFEST
        raw = _read_stable_bytes(
            manifest_path,
            maximum_bytes=MAX_SOURCE_PACK_MANIFEST_BYTES,
            label="H3 source manifest",
        )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                H3MaterialSourceError(
                    f"H3 source manifest contains non-finite JSON: {value}",
                ),
            ),
        )
        pack = H3MaterialSourcePack.model_validate_json(raw)
        if raw != canonical_h3_source_pack_bytes(pack):
            raise H3MaterialSourceError(
                "H3 source manifest is not canonical JSON",
            )
        expected_id = hashlib.sha256(
            canonical_h3_source_pack_bytes(
                pack,
                exclude_source_pack_id=True,
            ),
        ).hexdigest()
        if pack.source_pack_id != expected_id:
            raise H3MaterialSourceError(
                "H3 source-pack ID disagrees with canonical bytes",
            )
        expected_files = {H3_SOURCE_PACK_MANIFEST, "sources"}
        for record in pack.records:
            descriptor = record.native_source
            object_path = root / Path(descriptor.object_path)
            payload_bytes = _read_stable_bytes(
                object_path,
                maximum_bytes=MAX_NATIVE_SOURCE_BYTES,
                label=f"{record.slot_id} native source",
            )
            if hashlib.sha256(payload_bytes).hexdigest() != descriptor.sha256:
                raise H3MaterialSourceError(
                    f"{record.slot_id} native source SHA-256 disagrees",
                )
            if len(payload_bytes) != descriptor.bytes:
                raise H3MaterialSourceError(
                    f"{record.slot_id} native source byte count disagrees",
                )
            _verify_candidate_image(
                payload_bytes,
                descriptor=descriptor,
                label=f"{record.slot_id} native source",
            )
            expected_files.add(descriptor.object_path)
        if _directory_closure(root) != tuple(sorted(expected_files)):
            raise H3MaterialSourceError(
                "H3 source-pack directory closure disagrees with manifest",
            )
        if any(_is_linklike(path) for path in sources_root.iterdir()):
            raise H3MaterialSourceError(
                "H3 source-pack objects must not be links",
            )
        return pack
    except H3MaterialSourceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise H3MaterialSourceError(
            f"H3 source manifest validation failed: {exc}",
        ) from exc
    except OSError as exc:
        raise H3MaterialSourceError(
            f"H3 source-pack filesystem failure: {exc}",
        ) from exc


def read_verified_h3_source(
    root: Path,
    *,
    pack: H3MaterialSourcePack,
    slot_id: str,
) -> bytes:
    verified = load_h3_source_pack(root)
    if verified != pack:
        raise H3MaterialSourceError(
            "provided H3 source-pack evidence disagrees with disk",
        )
    matches = [record for record in verified.records if record.slot_id == slot_id]
    if len(matches) != 1:
        raise H3MaterialSourceError(
            f"H3 source slot is absent or ambiguous: {slot_id}",
        )
    descriptor = matches[0].native_source
    payload = _read_stable_bytes(
        Path(root).expanduser().absolute() / Path(descriptor.object_path),
        maximum_bytes=MAX_NATIVE_SOURCE_BYTES,
        label=f"{slot_id} native source",
    )
    if (
        len(payload) != descriptor.bytes
        or hashlib.sha256(payload).hexdigest() != descriptor.sha256
    ):
        raise H3MaterialSourceError(
            f"{slot_id} native source verification changed during read",
        )
    return payload


def prepare_h3_source_pack(
    selection_receipt: Path,
    output_root: Path,
) -> PreparedH3MaterialSourcePack:
    """Verify all candidates and publish selected source bytes atomically."""

    selection_receipt = Path(selection_receipt).expanduser().absolute()
    try:
        receipt = _read_selection_receipt(selection_receipt)
        candidate_payloads = _verified_candidates(
            selection_receipt,
            receipt,
        )
        pack = _pack_from_receipt(receipt, candidate_payloads)
        publication_root = _prepare_real_directory(
            output_root,
            label="H3 source publication root",
        )
        final_root = publication_root / pack.source_pack_id
        lock_path = publication_root / ".h3-source-pack.lock"
        with ProjectFileLock(lock_path, role="writer"):
            if final_root.exists() or _is_linklike(final_root):
                verified = load_h3_source_pack(final_root)
                if verified != pack:
                    raise H3MaterialSourceError(
                        "existing H3 source-pack identity has different evidence",
                    )
                return PreparedH3MaterialSourcePack(
                    root=final_root,
                    manifest=verified,
                )
            staging = publication_root / (
                f".{pack.source_pack_id}.{uuid.uuid4().hex}.tmp"
            )
            try:
                staging.mkdir(exist_ok=False)
                sources_root = staging / "sources"
                sources_root.mkdir(exist_ok=False)
                for record in pack.records:
                    descriptor = record.native_source
                    payload = candidate_payloads[descriptor.sha256]
                    _write_payload(staging / descriptor.object_path, payload)
                _write_payload(
                    staging / H3_SOURCE_PACK_MANIFEST,
                    canonical_h3_source_pack_bytes(pack),
                )
                _flush_directory(sources_root)
                _flush_directory(staging)
                if load_h3_source_pack(staging) != pack:
                    raise H3MaterialSourceError(
                        "staged H3 source pack changed during verification",
                    )
                os.rename(staging, final_root)
                _flush_directory(publication_root)
                verified = load_h3_source_pack(final_root)
                if verified != pack:
                    raise H3MaterialSourceError(
                        "published H3 source pack changed after rename",
                    )
                return PreparedH3MaterialSourcePack(
                    root=final_root,
                    manifest=verified,
                )
            finally:
                if staging.exists() and not _is_linklike(staging):
                    shutil.rmtree(staging)
    except H3MaterialSourceError:
        raise
    except JobContractError as exc:
        raise H3MaterialSourceError(
            f"H3 source publication lock is unavailable: {exc}",
        ) from exc
    except ValidationError as exc:
        raise H3MaterialSourceError(
            f"H3 material source validation failed: {exc}",
        ) from exc
    except OSError as exc:
        raise H3MaterialSourceError(
            f"H3 material source filesystem failure: {exc}",
        ) from exc
