"""Fail-closed human visual evidence and real-scene acceptance contracts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from pipeline.real_dataset import (
    REAL_DATASET_SOURCE,
    CaptureRightsReceipt,
    DatasetLock,
    DatasetReceipt,
    HfDatasetSource,
    LocalCaptureSource,
    canonical_model_bytes,
    validate_capture_rights,
    validate_dataset_receipt,
)
from pipeline.real_scene_import import (
    _load_training_material,
    validate_real_scene_import_receipt,
)
from pipeline.real_scene_training import (
    held_out_split_canonical_bytes,
    verify_production_training_job_bundle,
)
from pipeline.recon_schema import RegistrationResult
from pipeline.registration_quality import (
    RegistrationQualityPolicy,
    RegistrationQualityReport,
    validate_registration_quality,
)
from pipeline.render_evaluation import (
    RenderEvaluationPolicy,
    RenderEvaluationProtocol,
    RenderEvaluationReport,
    canonical_render_evaluation_bytes,
    validate_render_evaluation,
)
from pipeline.studio_revisions import verify_capture_bundle
from pipeline.training_provenance import (
    request_canonical_sha256,
    result_canonical_sha256,
)
from pipeline.viewer_acceptance import (
    ViewerPerformancePolicy,
    ViewerPerformanceReport,
    derive_viewer_decision,
)


class RealSceneAcceptanceError(ValueError):
    """Acceptance evidence is incomplete, drifted, or self-authored."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


VisualCategory = Literal[
    "scene-envelope",
    "floaters",
    "view-dependent-colour",
    "exposure-seams",
    "transparent-surfaces",
    "navigable-holes",
    "fidelity-label",
]
VisualDisposition = Literal["accepted", "rejected", "unknown"]

REQUIRED_VISUAL_CATEGORIES: tuple[VisualCategory, ...] = (
    "scene-envelope",
    "floaters",
    "view-dependent-colour",
    "exposure-seams",
    "transparent-surfaces",
    "navigable-holes",
    "fidelity-label",
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_POSE_PATTERN = r"^pose-[0-9a-f]{64}$"
_REVIEW_ID_PATTERN = r"^human-review-[0-9a-f]{64}$"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_ACCEPTANCE_DOCUMENT_BYTES = 4 * 1024 * 1024
_MAX_STRUCTURED_EVIDENCE_BYTES = 1024 * 1024 * 1024
_MAX_TRAINING_BUNDLE_BYTES = 16 * 1024 * 1024 * 1024


def _portable_relative_path(value: str, *, label: str) -> str:
    if not value or "\\" in value or "\x00" in value or len(value) > 240:
        raise ValueError(f"{label} must be a portable relative path")
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != value
    ):
        raise ValueError(f"{label} must be a portable relative path")
    return value


class HumanReviewPolicy(FrozenModel):
    schema_id: Literal["nantai.human-review-policy.v1"] = Field(
        default="nantai.human-review-policy.v1",
        alias="schema",
        serialization_alias="schema",
    )
    source_role: Literal[
        "internal-canary",
        "production-acceptance",
    ]
    required_categories: tuple[VisualCategory, ...]
    required_pose_ids: tuple[str, ...] = Field(min_length=3)
    maximum_screenshot_bytes: int = Field(
        ge=1_024,
        le=100 * 1024 * 1024,
    )

    @field_validator("required_categories")
    @classmethod
    def _categories_are_exact(
        cls,
        value: tuple[VisualCategory, ...],
    ) -> tuple[VisualCategory, ...]:
        if value != REQUIRED_VISUAL_CATEGORIES:
            raise ValueError(
                "human review policy must use the complete ordered visual category contract"
            )
        return value

    @field_validator("required_pose_ids")
    @classmethod
    def _poses_are_unique_content_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        import re

        if len(value) != len(set(value)) or any(
            re.fullmatch(_POSE_PATTERN, item) is None for item in value
        ):
            raise ValueError("human review poses must be unique content-addressed ids")
        return value


class HumanReviewDisposition(FrozenModel):
    category: VisualCategory
    disposition: VisualDisposition


class HumanScreenshotBinding(FrozenModel):
    pose_id: str = Field(pattern=_POSE_PATTERN)
    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    byte_count: int = Field(ge=1)
    width: int = Field(ge=1, le=16_384)
    height: int = Field(ge=1, le=16_384)

    @field_validator("path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        return _portable_relative_path(
            value,
            label="screenshot path",
        )


class HumanVisualReview(FrozenModel):
    schema_id: Literal["nantai.human-visual-review.v1"] = Field(
        default="nantai.human-visual-review.v1",
        alias="schema",
        serialization_alias="schema",
    )
    review_id: str = Field(pattern=_REVIEW_ID_PATTERN)
    source_role: Literal[
        "internal-canary",
        "production-acceptance",
    ]
    reviewer: str = Field(min_length=2, max_length=100)
    reviewed_at: datetime
    policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    dispositions: tuple[HumanReviewDisposition, ...] = Field(min_length=1)
    screenshots: tuple[HumanScreenshotBinding, ...] = Field(min_length=1)

    @field_validator("reviewer")
    @classmethod
    def _reviewer_is_bounded_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("reviewer must be trimmed printable text")
        return value

    @field_validator("reviewed_at")
    @classmethod
    def _reviewed_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("reviewed_at must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def _receipt_is_content_addressed(self) -> HumanVisualReview:
        categories = tuple(row.category for row in self.dispositions)
        pose_ids = tuple(row.pose_id for row in self.screenshots)
        if len(categories) != len(set(categories)) or len(pose_ids) != len(set(pose_ids)):
            raise ValueError("human review categories and screenshots must be unique")
        if self.review_id != _human_review_content_id(self):
            raise ValueError("human review content id disagrees")
        return self


class HumanReviewDecision(FrozenModel):
    schema_id: Literal["nantai.human-review-decision.v1"] = Field(
        default="nantai.human-review-decision.v1",
        alias="schema",
        serialization_alias="schema",
    )
    accepted: bool
    unknown_categories: tuple[VisualCategory, ...]
    rejected_categories: tuple[VisualCategory, ...]
    screenshot_count: int = Field(ge=0)


def canonical_human_review_policy_bytes(
    policy: HumanReviewPolicy,
) -> bytes:
    return canonical_model_bytes(policy)


def canonical_human_review_bytes(
    review: HumanVisualReview,
) -> bytes:
    return canonical_model_bytes(review)


def _canonical_payload(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _human_review_content_id(review: HumanVisualReview) -> str:
    payload = review.model_dump(
        mode="json",
        by_alias=True,
        exclude={"review_id"},
    )
    return "human-review-" + hashlib.sha256(_canonical_payload(payload)).hexdigest()


def _stat_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
    )


def _member_path(root: Path, relative: str, *, label: str) -> Path:
    try:
        root_mode = root.lstat().st_mode
    except OSError as exc:
        raise RealSceneAcceptanceError("review root is unavailable") from exc
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise RealSceneAcceptanceError("review root must be a non-symlink directory")
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise RealSceneAcceptanceError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(mode):
            raise RealSceneAcceptanceError(f"{label} contains a symlink")
    return current


def _read_stable_screenshot(
    root: Path,
    binding: HumanScreenshotBinding,
    *,
    maximum_bytes: int,
) -> bytes:
    path = _member_path(
        root,
        binding.path,
        label=f"screenshot {binding.pose_id}",
    )
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise RealSceneAcceptanceError(f"screenshot {binding.pose_id} is not a regular file")
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise RealSceneAcceptanceError(f"screenshot {binding.pose_id} byte length is invalid")
        payload = path.read_bytes()
        after = path.lstat()
    except RealSceneAcceptanceError:
        raise
    except OSError as exc:
        raise RealSceneAcceptanceError(f"screenshot {binding.pose_id} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after) or len(payload) != before.st_size:
        raise RealSceneAcceptanceError(f"screenshot {binding.pose_id} changed while being read")
    return payload


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if not payload.startswith(_PNG_SIGNATURE):
        raise RealSceneAcceptanceError("screenshot is not a lossless PNG")
    offset = len(_PNG_SIGNATURE)
    chunks: list[bytes] = []
    idat_chunks: list[bytes] = []
    ihdr: bytes | None = None
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise RealSceneAcceptanceError("screenshot is not a lossless PNG")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        end = offset + 12 + length
        if end > len(payload):
            raise RealSceneAcceptanceError("screenshot is not a lossless PNG")
        kind = payload[offset + 4 : offset + 8]
        data = payload[offset + 8 : offset + 8 + length]
        declared_crc = struct.unpack(">I", payload[end - 4 : end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != declared_crc:
            raise RealSceneAcceptanceError("screenshot is not a lossless PNG")
        chunks.append(kind)
        if kind == b"IHDR":
            ihdr = data
        if kind == b"IDAT":
            idat_chunks.append(data)
        offset = end
        if kind == b"IEND":
            break
    if (
        offset != len(payload)
        or not chunks
        or chunks[0] != b"IHDR"
        or chunks.count(b"IHDR") != 1
        or b"IDAT" not in chunks
        or chunks.count(b"IEND") != 1
        or chunks[-1] != b"IEND"
        or ihdr is None
        or len(ihdr) != 13
    ):
        raise RealSceneAcceptanceError("screenshot is not a lossless PNG")
    width, height = struct.unpack(">II", ihdr[:8])
    bit_depth, colour_type, compression, filtering, interlace = ihdr[8:]
    if (
        width < 1
        or height < 1
        or width > 16_384
        or height > 16_384
        or width * height > 16_777_216
        or bit_depth != 8
        or colour_type not in {2, 6}
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        raise RealSceneAcceptanceError("screenshot PNG format is unsupported")
    channels = 3 if colour_type == 2 else 4
    row_bytes = width * channels
    expected_bytes = height * (row_bytes + 1)
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(
            b"".join(idat_chunks),
            expected_bytes + 1,
        )
    except zlib.error as exc:
        raise RealSceneAcceptanceError("screenshot PNG pixel data cannot decode") from exc
    if (
        len(pixels) != expected_bytes
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
        or any(pixels[row * (row_bytes + 1)] > 4 for row in range(height))
    ):
        raise RealSceneAcceptanceError("screenshot PNG pixel data cannot decode")
    return width, height


def _screenshot_binding(
    root: Path,
    *,
    pose_id: str,
    relative: str,
    maximum_bytes: int,
) -> HumanScreenshotBinding:
    relative = _portable_relative_path(
        relative,
        label="screenshot path",
    )
    placeholder = HumanScreenshotBinding(
        pose_id=pose_id,
        path=relative,
        sha256="0" * 64,
        byte_count=1,
        width=1,
        height=1,
    )
    payload = _read_stable_screenshot(
        root,
        placeholder,
        maximum_bytes=maximum_bytes,
    )
    width, height = _png_dimensions(payload)
    return HumanScreenshotBinding(
        pose_id=pose_id,
        path=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        width=width,
        height=height,
    )


def record_human_visual_review(
    *,
    policy: HumanReviewPolicy,
    root: Path,
    reviewer: str,
    dispositions: dict[str, str],
    screenshots: dict[str, str],
    reviewed_at: datetime | None = None,
) -> HumanVisualReview:
    """Build a content-addressed receipt; missing dispositions stay unknown."""

    try:
        policy = HumanReviewPolicy.model_validate(policy.model_dump(by_alias=True))
    except (AttributeError, ValueError) as exc:
        raise RealSceneAcceptanceError(f"human review policy is invalid: {exc}") from exc
    extra_categories = set(dispositions) - set(policy.required_categories)
    if extra_categories:
        raise RealSceneAcceptanceError("human review contains unsupported categories")
    extra_poses = set(screenshots) - set(policy.required_pose_ids)
    missing_poses = set(policy.required_pose_ids) - set(screenshots)
    if extra_poses or missing_poses:
        raise RealSceneAcceptanceError("human review screenshot pose set differs from policy")
    rows: list[HumanReviewDisposition] = []
    for category in policy.required_categories:
        try:
            rows.append(
                HumanReviewDisposition(
                    category=category,
                    disposition=dispositions.get(
                        category,
                        "unknown",
                    ),
                )
            )
        except ValueError as exc:
            raise RealSceneAcceptanceError(f"invalid disposition for {category}: {exc}") from exc
    screenshot_rows = tuple(
        _screenshot_binding(
            root,
            pose_id=pose_id,
            relative=screenshots[pose_id],
            maximum_bytes=policy.maximum_screenshot_bytes,
        )
        for pose_id in policy.required_pose_ids
    )
    timestamp = reviewed_at or datetime.now(UTC)
    values = {
        "source_role": policy.source_role,
        "reviewer": reviewer,
        "reviewed_at": timestamp,
        "policy_sha256": hashlib.sha256(canonical_human_review_policy_bytes(policy)).hexdigest(),
        "dispositions": tuple(rows),
        "screenshots": screenshot_rows,
    }
    draft = HumanVisualReview.model_construct(
        review_id="human-review-" + "0" * 64,
        **values,
    )
    try:
        return HumanVisualReview(
            review_id=_human_review_content_id(draft),
            **values,
        )
    except ValueError as exc:
        raise RealSceneAcceptanceError(f"human visual review is invalid: {exc}") from exc


def _validated_review(
    review: HumanVisualReview,
) -> HumanVisualReview:
    if "accepted" in getattr(review, "__dict__", {}):
        raise RealSceneAcceptanceError("review-authored aggregate acceptance is forbidden")
    try:
        return HumanVisualReview.model_validate(review.model_dump(by_alias=True))
    except (AttributeError, ValueError) as exc:
        raise RealSceneAcceptanceError(f"human visual review is invalid: {exc}") from exc


def validate_human_visual_review(
    policy: HumanReviewPolicy,
    review: HumanVisualReview,
    root: Path,
) -> HumanReviewDecision:
    """Reopen every screenshot and derive the human-review gate."""

    try:
        policy = HumanReviewPolicy.model_validate(policy.model_dump(by_alias=True))
    except (AttributeError, ValueError) as exc:
        raise RealSceneAcceptanceError(f"human review policy is invalid: {exc}") from exc
    review = _validated_review(review)
    expected_policy_sha = hashlib.sha256(canonical_human_review_policy_bytes(policy)).hexdigest()
    if review.policy_sha256 != expected_policy_sha:
        raise RealSceneAcceptanceError("human review policy SHA-256 disagrees")
    if review.source_role != policy.source_role:
        raise RealSceneAcceptanceError("human review source role differs from policy")
    categories = tuple(row.category for row in review.dispositions)
    if categories != policy.required_categories:
        raise RealSceneAcceptanceError("human review category order differs from policy")
    pose_ids = tuple(row.pose_id for row in review.screenshots)
    if pose_ids != policy.required_pose_ids:
        raise RealSceneAcceptanceError("human review screenshot order differs from policy")
    for binding in review.screenshots:
        payload = _read_stable_screenshot(
            root,
            binding,
            maximum_bytes=policy.maximum_screenshot_bytes,
        )
        dimensions = _png_dimensions(payload)
        if (
            len(payload) != binding.byte_count
            or hashlib.sha256(payload).hexdigest() != binding.sha256
            or dimensions != (binding.width, binding.height)
        ):
            raise RealSceneAcceptanceError(
                f"screenshot {binding.pose_id} SHA, byte length, or dimensions disagree"
            )
    unknown = tuple(row.category for row in review.dispositions if row.disposition == "unknown")
    rejected = tuple(row.category for row in review.dispositions if row.disposition == "rejected")
    return HumanReviewDecision(
        accepted=not unknown and not rejected,
        unknown_categories=unknown,
        rejected_categories=rejected,
        screenshot_count=len(review.screenshots),
    )


class AcceptanceEvidenceReference(FrozenModel):
    """One immutable file consumed by the aggregate decision."""

    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    byte_length: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        return _portable_relative_path(
            value,
            label="acceptance evidence path",
        )


class AcceptanceDirectoryReference(FrozenModel):
    """One real directory below the aggregate report boundary."""

    path: str

    @field_validator("path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        return _portable_relative_path(
            value,
            label="acceptance directory path",
        )


class RealSceneAcceptancePointer(FrozenModel):
    """Mutable selector for one immutable aggregate report.

    The pointer carries no authored decision. Consumers must reopen the bound
    report bytes and derive acceptance with ``validate_real_scene_acceptance``.
    """

    schema_id: Literal["nantai.real-scene-acceptance-pointer.v1"] = Field(
        default="nantai.real-scene-acceptance-pointer.v1",
        alias="schema",
        serialization_alias="schema",
    )
    report_path: str
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    report_byte_length: int = Field(ge=1, le=_MAX_ACCEPTANCE_DOCUMENT_BYTES)

    @field_validator("report_path")
    @classmethod
    def _report_path_is_portable(cls, value: str) -> str:
        return _portable_relative_path(
            value,
            label="acceptance pointer report path",
        )

    @model_validator(mode="after")
    def _report_name_is_content_addressed(self) -> RealSceneAcceptancePointer:
        expected = f"real-scene-acceptance-{self.report_sha256}.json"
        if PurePosixPath(self.report_path).name != expected:
            raise ValueError("acceptance pointer report filename differs from its SHA-256")
        return self


def _is_below(relative: str, directory: str) -> bool:
    return relative.startswith(f"{directory}/")


class RealSceneAcceptance(FrozenModel):
    """References only; every decision is re-derived by the validator."""

    schema_id: Literal["nantai.real-scene-acceptance.v1"] = Field(
        default="nantai.real-scene-acceptance.v1",
        alias="schema",
        serialization_alias="schema",
    )
    source_role: Literal["internal-canary", "production-acceptance"]
    source: AcceptanceEvidenceReference
    rights_receipt: AcceptanceEvidenceReference | None = None
    fetch_root: AcceptanceDirectoryReference
    dataset_lock: AcceptanceEvidenceReference | None = None
    dataset_receipt: AcceptanceEvidenceReference | None = None
    capture_bundle: AcceptanceDirectoryReference
    capture_manifest: AcceptanceEvidenceReference
    prepared_capture_evidence: AcceptanceEvidenceReference
    sfm_root: AcceptanceDirectoryReference
    registration: AcceptanceEvidenceReference
    registration_policy: AcceptanceEvidenceReference
    registration_report: AcceptanceEvidenceReference
    training_root: AcceptanceDirectoryReference
    training_bundle: AcceptanceEvidenceReference
    import_root: AcceptanceDirectoryReference
    import_receipt: AcceptanceEvidenceReference
    render_root: AcceptanceDirectoryReference
    render_policy: AcceptanceEvidenceReference
    render_report: AcceptanceEvidenceReference
    viewer_policy: AcceptanceEvidenceReference
    viewer_report: AcceptanceEvidenceReference
    human_review_policy: AcceptanceEvidenceReference
    human_visual_review: AcceptanceEvidenceReference

    @model_validator(mode="after")
    def _reference_shape_is_exact(self) -> RealSceneAcceptance:
        if self.source_role == "internal-canary":
            if self.rights_receipt is not None:
                raise ValueError("internal canary cannot carry a rights receipt")
            if self.dataset_lock is None or self.dataset_receipt is None:
                raise ValueError("internal canary requires dataset lock and receipt")
        elif (
            self.rights_receipt is None
            or self.dataset_lock is not None
            or self.dataset_receipt is not None
        ):
            raise ValueError(
                "production acceptance requires rights and forbids "
                "remote dataset lock/receipt claims"
            )

        references = _acceptance_references(self)
        paths = tuple(reference.path for reference in references)
        if len(paths) != len(set(paths)):
            raise ValueError("acceptance evidence paths must be unique")
        directories = (
            self.fetch_root.path,
            self.capture_bundle.path,
            self.sfm_root.path,
            self.training_root.path,
            self.import_root.path,
            self.render_root.path,
        )
        if len(directories) != len(set(directories)):
            raise ValueError("acceptance directory paths must be unique")

        required_locations = (
            (self.capture_manifest.path, self.capture_bundle.path),
            (self.prepared_capture_evidence.path, self.sfm_root.path),
            (self.registration.path, self.sfm_root.path),
            (self.registration_policy.path, self.sfm_root.path),
            (self.registration_report.path, self.sfm_root.path),
            (self.training_bundle.path, self.training_root.path),
            (self.import_receipt.path, self.import_root.path),
            (self.render_policy.path, self.render_root.path),
            (self.render_report.path, self.render_root.path),
        )
        if any(not _is_below(path, directory) for path, directory in required_locations):
            raise ValueError("acceptance evidence is outside its declared directory")
        if self.dataset_lock is not None and (
            self.dataset_lock.path != f"{self.fetch_root.path}/dataset-lock.json"
        ):
            raise ValueError("dataset lock must use the verified fetch-root location")
        if self.dataset_receipt is not None and (
            self.dataset_receipt.path != f"{self.fetch_root.path}/dataset-receipt.json"
        ):
            raise ValueError("dataset receipt must use the verified fetch-root location")
        if self.rights_receipt is not None and (
            not _is_below(
                self.rights_receipt.path,
                self.fetch_root.path,
            )
        ):
            raise ValueError("rights receipt must be below the fetch root")
        if self.capture_manifest.path != f"{self.capture_bundle.path}/manifest.json":
            raise ValueError("capture manifest must be the bundle manifest")
        if self.training_bundle.path != (
            f"{self.training_root.path}/training-bundle/training-job.zip"
        ):
            raise ValueError("training bundle must use the verified training location")
        if self.import_receipt.path != f"{self.import_root.path}/import-receipt.json":
            raise ValueError("import receipt must use the verified import location")
        if self.render_root.path != f"{self.training_root.path}/remote-result":
            raise ValueError("render evidence must use the verified remote result")
        if (
            self.render_policy.path != f"{self.render_root.path}/render-evaluation/policy.json"
            or self.render_report.path != f"{self.render_root.path}/render-evaluation/report.json"
        ):
            raise ValueError("render policy/report must use the remote result locations")
        return self


AcceptanceGateId = Literal[
    "dataset",
    "capture",
    "sfm",
    "production-training",
    "import-integrity",
    "render-quality",
    "viewer-performance",
    "human-review",
    "release-rights",
    "metric-alignment",
]
AcceptanceGateState = Literal[
    "accepted",
    "rejected",
    "not-applicable",
]


class AcceptanceGate(FrozenModel):
    gate: AcceptanceGateId
    state: AcceptanceGateState
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _state_matches_reasons(self) -> AcceptanceGate:
        if self.state == "accepted" and self.reasons:
            raise ValueError("accepted gate cannot carry rejection reasons")
        if self.state != "accepted" and not self.reasons:
            raise ValueError("rejected/not-applicable gate requires a reason")
        return self


class AcceptanceDecision(FrozenModel):
    schema_id: Literal["nantai.real-scene-acceptance-decision.v1"] = Field(
        default="nantai.real-scene-acceptance-decision.v1",
        alias="schema",
        serialization_alias="schema",
    )
    source_role: Literal["internal-canary", "production-acceptance"]
    technical_accepted: bool
    canary_accepted: bool
    production_release_allowed: bool
    gates: tuple[AcceptanceGate, ...]
    failed_gates: tuple[AcceptanceGateId, ...]
    reasons: tuple[str, ...]
    report_sha256: str = Field(pattern=_SHA256_PATTERN)


@dataclass(frozen=True)
class _ValidatedAcceptanceEvidence:
    release_rights_allowed: bool
    sfm_accepted: bool
    training_quality_role: Literal["preview-only", "production"]
    geometry_usability: Literal["preview-only", "metric-aligned"]
    target_units: Literal["arbitrary", "meters"]
    alignment_rms_m: float | None
    render_accepted: bool
    render_failures: tuple[str, ...]
    viewer_accepted: bool
    viewer_failures: tuple[str, ...]
    human_accepted: bool
    human_failures: tuple[str, ...]


class _PreparedCaptureEvidence(FrozenModel):
    schema_id: Literal["nantai.prepared-capture-evidence.v1"] = Field(
        default="nantai.prepared-capture-evidence.v1",
        alias="schema",
        serialization_alias="schema",
    )
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    capture_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)


def canonical_real_scene_acceptance_bytes(
    report: RealSceneAcceptance,
) -> bytes:
    return canonical_model_bytes(report)


def canonical_real_scene_acceptance_pointer_bytes(
    pointer: RealSceneAcceptancePointer,
) -> bytes:
    return canonical_model_bytes(pointer)


def _relative_to_acceptance_root(
    root: Path,
    path: Path,
    *,
    label: str,
) -> str:
    boundary = Path(root).expanduser().absolute()
    candidate = Path(path).expanduser().absolute()
    try:
        relative = candidate.relative_to(boundary).as_posix()
    except ValueError as exc:
        raise RealSceneAcceptanceError(f"{label} must stay below the acceptance root") from exc
    try:
        return _portable_relative_path(relative, label=label)
    except ValueError as exc:
        raise RealSceneAcceptanceError(str(exc)) from exc


def acceptance_directory_reference(
    root: Path,
    path: Path,
) -> AcceptanceDirectoryReference:
    reference = AcceptanceDirectoryReference(
        path=_relative_to_acceptance_root(
            root,
            path,
            label="acceptance directory",
        )
    )
    _directory_member(Path(root).expanduser().absolute(), reference)
    return reference


def acceptance_evidence_reference(
    root: Path,
    path: Path,
) -> AcceptanceEvidenceReference:
    boundary = Path(root).expanduser().absolute()
    relative = _relative_to_acceptance_root(
        boundary,
        path,
        label="acceptance evidence",
    )
    member = _member_path(
        boundary,
        relative,
        label=f"acceptance evidence {relative}",
    )
    digest = hashlib.sha256()
    try:
        before = member.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RealSceneAcceptanceError(f"acceptance evidence {relative} is not a regular file")
        if before.st_size <= 0 or before.st_size > _MAX_TRAINING_BUNDLE_BYTES:
            raise RealSceneAcceptanceError(f"acceptance evidence {relative} length is invalid")
        with member.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = member.lstat()
    except RealSceneAcceptanceError:
        raise
    except OSError as exc:
        raise RealSceneAcceptanceError(f"acceptance evidence {relative} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RealSceneAcceptanceError(f"acceptance evidence {relative} changed while read")
    return AcceptanceEvidenceReference(
        path=relative,
        sha256=digest.hexdigest(),
        byte_length=before.st_size,
    )


def publish_real_scene_acceptance(
    report: RealSceneAcceptance,
    root: Path,
) -> tuple[Path, AcceptanceDecision]:
    """Publish one immutable content-addressed aggregate and revalidate it."""

    boundary = Path(root).expanduser().absolute()
    try:
        mode = boundary.lstat().st_mode
    except OSError as exc:
        raise RealSceneAcceptanceError("acceptance root is unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise RealSceneAcceptanceError("acceptance root must be a real directory")
    payload = canonical_real_scene_acceptance_bytes(report)
    digest = hashlib.sha256(payload).hexdigest()
    path = boundary / f"real-scene-acceptance-{digest}.json"
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        reference = AcceptanceEvidenceReference(
            path=path.name,
            sha256=digest,
            byte_length=len(payload),
        )
        existing = _hash_reference(
            boundary,
            reference,
            retain_bytes=True,
        )
        if existing != payload:
            raise RealSceneAcceptanceError(
                "content-addressed acceptance path contains different bytes"
            ) from None
    except OSError as exc:
        raise RealSceneAcceptanceError("acceptance report cannot be published") from exc
    else:
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise RealSceneAcceptanceError("acceptance report cannot be published") from exc
    return path, validate_real_scene_acceptance(path)


def publish_real_scene_acceptance_pointer(
    report_path: Path,
    root: Path,
) -> Path:
    """Atomically select one content-addressed report below ``root``."""

    boundary = Path(root).expanduser().absolute()
    try:
        mode = boundary.lstat().st_mode
    except OSError as exc:
        raise RealSceneAcceptanceError("acceptance pointer root is unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise RealSceneAcceptanceError("acceptance pointer root must be a real directory")
    report = acceptance_evidence_reference(
        boundary,
        report_path,
    )
    pointer = RealSceneAcceptancePointer(
        report_path=report.path,
        report_sha256=report.sha256,
        report_byte_length=report.byte_length,
    )
    payload = canonical_real_scene_acceptance_pointer_bytes(pointer)
    destination = boundary / "latest-acceptance.json"
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".latest-acceptance-",
            suffix=".tmp",
            dir=boundary,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = ""
    except OSError as exc:
        raise RealSceneAcceptanceError("acceptance pointer cannot be published") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                Path(temporary).unlink()
            except OSError:
                pass
    return destination


def load_latest_real_scene_acceptance(root: Path) -> Path | None:
    """Resolve and byte-verify the configured aggregate report, if present."""

    boundary = Path(root).expanduser().absolute()
    try:
        root_mode = boundary.lstat().st_mode
    except OSError as exc:
        raise RealSceneAcceptanceError("acceptance pointer root is unavailable") from exc
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise RealSceneAcceptanceError("acceptance pointer root must be a real directory")
    pointer_path = boundary / "latest-acceptance.json"
    try:
        before = pointer_path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RealSceneAcceptanceError("acceptance pointer cannot be inspected") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RealSceneAcceptanceError("acceptance pointer must be a regular file")
    if before.st_size <= 0 or before.st_size > _MAX_ACCEPTANCE_DOCUMENT_BYTES:
        raise RealSceneAcceptanceError("acceptance pointer length is invalid")
    try:
        payload = pointer_path.read_bytes()
        after = pointer_path.lstat()
        pointer = RealSceneAcceptancePointer.model_validate_json(payload)
    except (OSError, ValidationError) as exc:
        raise RealSceneAcceptanceError("acceptance pointer is invalid") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RealSceneAcceptanceError("acceptance pointer changed while read")
    if payload != canonical_real_scene_acceptance_pointer_bytes(pointer):
        raise RealSceneAcceptanceError("acceptance pointer is not canonical JSON")
    reference = AcceptanceEvidenceReference(
        path=pointer.report_path,
        sha256=pointer.report_sha256,
        byte_length=pointer.report_byte_length,
    )
    _hash_reference(
        boundary,
        reference,
        retain_bytes=True,
    )
    return _member_path(
        boundary,
        pointer.report_path,
        label="acceptance pointer report",
    )


def _acceptance_references(
    report: RealSceneAcceptance,
) -> tuple[AcceptanceEvidenceReference, ...]:
    optional = (
        report.rights_receipt,
        report.dataset_lock,
        report.dataset_receipt,
    )
    return (
        report.source,
        *(item for item in optional if item is not None),
        report.capture_manifest,
        report.prepared_capture_evidence,
        report.registration,
        report.registration_policy,
        report.registration_report,
        report.training_bundle,
        report.import_receipt,
        report.render_policy,
        report.render_report,
        report.viewer_policy,
        report.viewer_report,
        report.human_review_policy,
        report.human_visual_review,
    )


def _acceptance_directories(
    report: RealSceneAcceptance,
) -> tuple[AcceptanceDirectoryReference, ...]:
    return (
        report.fetch_root,
        report.capture_bundle,
        report.sfm_root,
        report.training_root,
        report.import_root,
        report.render_root,
    )


def _directory_member(
    root: Path,
    reference: AcceptanceDirectoryReference,
) -> Path:
    path = _member_path(
        root,
        reference.path,
        label=f"acceptance directory {reference.path}",
    )
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise RealSceneAcceptanceError(
            f"acceptance directory {reference.path} is unavailable"
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise RealSceneAcceptanceError(
            f"acceptance directory {reference.path} is not a real directory"
        )
    return path


def _hash_reference(
    root: Path,
    reference: AcceptanceEvidenceReference,
    *,
    retain_bytes: bool,
) -> bytes | None:
    path = _member_path(
        root,
        reference.path,
        label=f"acceptance evidence {reference.path}",
    )
    try:
        before = path.lstat()
    except OSError as exc:
        raise RealSceneAcceptanceError(
            f"acceptance evidence {reference.path} is unavailable"
        ) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RealSceneAcceptanceError(
            f"acceptance evidence {reference.path} is not a regular file"
        )
    maximum = _MAX_STRUCTURED_EVIDENCE_BYTES if retain_bytes else _MAX_TRAINING_BUNDLE_BYTES
    if before.st_size != reference.byte_length or before.st_size > maximum:
        raise RealSceneAcceptanceError(
            f"acceptance evidence {reference.path} byte length disagrees"
        )
    digest = hashlib.sha256()
    retained = bytearray() if retain_bytes else None
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                if retained is not None:
                    retained.extend(chunk)
        after = path.lstat()
    except OSError as exc:
        raise RealSceneAcceptanceError(
            f"acceptance evidence {reference.path} cannot be read"
        ) from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RealSceneAcceptanceError(f"acceptance evidence {reference.path} changed while read")
    if digest.hexdigest() != reference.sha256:
        raise RealSceneAcceptanceError(f"acceptance evidence {reference.path} SHA-256 disagrees")
    return bytes(retained) if retained is not None else None


def _preflight_acceptance_references(
    report: RealSceneAcceptance,
    root: Path,
) -> dict[str, bytes]:
    for directory in _acceptance_directories(report):
        _directory_member(root, directory)
    payloads: dict[str, bytes] = {}
    for reference in _acceptance_references(report):
        retain = reference != report.training_bundle
        payload = _hash_reference(
            root,
            reference,
            retain_bytes=retain,
        )
        if payload is not None:
            payloads[reference.path] = payload
    return payloads


def _unique_json(payload: bytes, *, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RealSceneAcceptanceError(f"{label} contains duplicate JSON key {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except RealSceneAcceptanceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RealSceneAcceptanceError(f"{label} is not canonical JSON") from exc


def _canonical_model_from_bytes(
    payload: bytes,
    model_type: type[BaseModel],
    *,
    label: str,
    canonicalizer=canonical_model_bytes,
):
    parsed = _unique_json(payload, label=label)
    try:
        model = model_type.model_validate(parsed)
    except ValidationError as exc:
        raise RealSceneAcceptanceError(f"{label} is invalid: {exc}") from exc
    if payload != canonicalizer(model):
        raise RealSceneAcceptanceError(f"{label} is not canonical")
    return model


def _viewer_model_from_bytes(
    payload: bytes,
    model_type: type[BaseModel],
    *,
    label: str,
):
    """Validate the UTF-8 canonical form emitted by the pinned JS harness."""

    parsed = _unique_json(payload, label=label)
    try:
        canonical = (
            json.dumps(
                parsed,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise RealSceneAcceptanceError(f"{label} cannot be canonicalized") from exc
    if payload != canonical:
        raise RealSceneAcceptanceError(f"{label} is not canonical")
    try:
        return model_type.model_validate(parsed)
    except ValidationError as exc:
        raise RealSceneAcceptanceError(f"{label} is invalid: {exc}") from exc


def _source_from_bytes(payload: bytes):
    parsed = _unique_json(payload, label="real dataset source")
    try:
        source = REAL_DATASET_SOURCE.validate_python(parsed)
    except ValidationError as exc:
        raise RealSceneAcceptanceError(f"real dataset source is invalid: {exc}") from exc
    if payload != canonical_model_bytes(source):
        raise RealSceneAcceptanceError("real dataset source is not canonical")
    return source


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RealSceneAcceptanceError(
                f"accepted scene artifact is not a regular file: {path.name}"
            )
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = path.lstat()
        if _stat_signature(before) != _stat_signature(after):
            raise RealSceneAcceptanceError(
                f"accepted scene artifact changed while read: {path.name}"
            )
    except RealSceneAcceptanceError:
        raise
    except OSError as exc:
        raise RealSceneAcceptanceError(f"cannot hash accepted scene artifact: {path.name}") from exc
    return digest.hexdigest()


def _render_policy_failures(
    policy: RenderEvaluationPolicy,
) -> tuple[str, ...]:
    failures: list[str] = []
    expected_protocol = RenderEvaluationProtocol(
        width=800,
        height=600,
        crop_mode="center-crop",
        colour_space="srgb",
        alpha_handling="reject",
        mask_handling="none",
        ssim_window_size=11,
        ssim_sigma=1.5,
        ssim_data_range=1.0,
        lpips_backbone="alex",
    )
    if policy.protocol != expected_protocol:
        failures.append("render evaluation protocol differs from production v1")
    if policy.minimum_mean_psnr < 24.0:
        failures.append("minimum mean PSNR policy is below 24.0 dB")
    if policy.minimum_mean_ssim < 0.80:
        failures.append("minimum mean SSIM policy is below 0.80")
    if policy.maximum_mean_lpips > 0.25:
        failures.append("maximum mean LPIPS policy exceeds 0.25")
    if policy.minimum_worst_psnr < 18.0:
        failures.append("minimum worst-frame PSNR policy is below 18.0 dB")
    return tuple(failures)


def _viewer_policy_failures(
    policy: ViewerPerformancePolicy,
) -> tuple[str, ...]:
    failures: list[str] = []
    if (policy.viewport_width, policy.viewport_height) != (1280, 720):
        failures.append("viewer viewport policy must be exactly 1280x720")
    if policy.warmup_frame_count != 120 or policy.measured_frame_count != 600:
        failures.append(
            "viewer sampling policy must be exactly 120 warmup plus 600 measured frames"
        )
    if policy.maximum_interactive_ms > 10_000.0:
        failures.append("viewer interactive policy exceeds 10 seconds")
    if policy.maximum_p50_frame_ms > 33.34:
        failures.append("viewer p50 policy exceeds 33.34 ms")
    if policy.maximum_p95_frame_ms > 50.0:
        failures.append("viewer p95 policy exceeds 50 ms")
    if policy.maximum_worst_frame_ms > 250.0:
        failures.append("viewer worst-frame policy exceeds 250 ms")
    return tuple(failures)


def _read_acceptance_member(
    root: Path,
    relative: str,
    *,
    label: str,
    maximum_bytes: int = _MAX_STRUCTURED_EVIDENCE_BYTES,
) -> bytes:
    path = _member_path(root, relative, label=label)
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RealSceneAcceptanceError(f"{label} is not a regular file")
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise RealSceneAcceptanceError(f"{label} length is outside the allowed range")
        payload = path.read_bytes()
        after = path.lstat()
    except RealSceneAcceptanceError:
        raise
    except OSError as exc:
        raise RealSceneAcceptanceError(f"{label} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RealSceneAcceptanceError(f"{label} changed while read")
    return payload


def _validate_packaged_render_evaluation(
    *,
    policy: RenderEvaluationPolicy,
    report: RenderEvaluationReport,
    training_bundle_path: Path,
    remote_result_root: Path,
):
    """Rebuild the evaluator's read-only view from verified package bytes."""

    try:
        bundle = verify_production_training_job_bundle(training_bundle_path)
    except ValueError as exc:
        raise RealSceneAcceptanceError(
            f"production training bundle cannot be reopened: {exc}"
        ) from exc
    split_bytes = held_out_split_canonical_bytes(bundle.split)
    if hashlib.sha256(split_bytes).hexdigest() != policy.held_out_split_sha256:
        raise RealSceneAcceptanceError("packaged evaluation split differs from render policy")
    source_bytes: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(bundle.path, "r") as archive:
            for identity in bundle.split.held_out:
                payload = archive.read(f"evaluation/payload/{identity.logical_path}")
                if hashlib.sha256(payload).hexdigest() != identity.sha256:
                    raise RealSceneAcceptanceError("held-out evaluation source SHA-256 disagrees")
                source_bytes[identity.logical_path] = payload
    except RealSceneAcceptanceError:
        raise
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise RealSceneAcceptanceError("held-out evaluation sources cannot be reopened") from exc

    transforms = _read_acceptance_member(
        remote_result_root,
        "render-evaluation/transforms.json",
        label="packaged render transforms",
    )
    config = _read_acceptance_member(
        remote_result_root,
        "render-evaluation/trainer-config.yml",
        label="packaged trainer config",
    )
    remote_payloads: dict[str, bytes] = {}
    for frame in report.frames:
        for target in (frame.render_path, frame.camera_path):
            prefix = "result/"
            if not target.startswith(prefix):
                raise RealSceneAcceptanceError("render evaluation artifact path is outside result")
            remote = target[len(prefix) :]
            remote_payloads[target] = _read_acceptance_member(
                remote_result_root,
                remote,
                label=f"packaged render artifact {remote}",
                maximum_bytes=2 * 1024 * 1024 * 1024,
            )

    with tempfile.TemporaryDirectory(
        prefix="nantai-accept-render-",
    ) as temporary:
        view = Path(temporary) / "run"

        def write(relative: str, payload: bytes) -> None:
            path = view.joinpath(*PurePosixPath(relative).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        write(policy.held_out_split_path, split_bytes)
        write(policy.transforms_path, transforms)
        write(report.trainer_config_path, config)
        for logical_path, payload in source_bytes.items():
            write(f"{policy.source_root}/{logical_path}", payload)
        for relative, payload in remote_payloads.items():
            write(relative, payload)
        return validate_render_evaluation(policy, report, view)


def _validate_acceptance_evidence(
    report: RealSceneAcceptance,
    root: Path,
    payloads: dict[str, bytes],
) -> _ValidatedAcceptanceEvidence:
    source = _source_from_bytes(payloads[report.source.path])
    if source.role != report.source_role:
        raise RealSceneAcceptanceError("dataset source role differs from aggregate report")
    source_sha = hashlib.sha256(canonical_model_bytes(source)).hexdigest()

    if isinstance(source, HfDatasetSource):
        assert report.dataset_lock is not None
        assert report.dataset_receipt is not None
        lock = _canonical_model_from_bytes(
            payloads[report.dataset_lock.path],
            DatasetLock,
            label="dataset lock",
        )
        receipt = _canonical_model_from_bytes(
            payloads[report.dataset_receipt.path],
            DatasetReceipt,
            label="dataset receipt",
        )
        validate_dataset_receipt(
            source,
            lock,
            receipt,
            root / report.fetch_root.path / "dataset",
        )
        dataset_receipt_sha = hashlib.sha256(canonical_model_bytes(receipt)).hexdigest()
        release_rights_allowed = False
    elif isinstance(source, LocalCaptureSource):
        assert report.rights_receipt is not None
        rights = _canonical_model_from_bytes(
            payloads[report.rights_receipt.path],
            CaptureRightsReceipt,
            label="capture rights receipt",
        )
        validate_capture_rights(source, rights)
        release_rights_allowed = bool(
            source.redistribution_allowed
            and source.release_inclusion_allowed
            and rights.redistribution_allowed
            and rights.release_inclusion_allowed
        )
        dataset_receipt_sha = ""
    else:  # pragma: no cover - discriminated union is closed
        raise RealSceneAcceptanceError("unsupported real dataset source")

    capture = verify_capture_bundle(root / report.capture_bundle.path)
    if capture.manifest.synthetic:
        raise RealSceneAcceptanceError("real-scene acceptance forbids synthetic capture evidence")
    if capture.manifest_digest != report.capture_manifest.sha256 or payloads[
        report.capture_manifest.path
    ] != canonical_model_bytes(capture.manifest):
        raise RealSceneAcceptanceError("capture manifest reference differs from verified bundle")
    if isinstance(source, LocalCaptureSource):
        dataset_receipt_sha = capture.manifest.ingest_manifest_sha256

    prepared = _canonical_model_from_bytes(
        payloads[report.prepared_capture_evidence.path],
        _PreparedCaptureEvidence,
        label="prepared capture evidence",
    )
    if (
        prepared.source_sha256 != source_sha
        or prepared.dataset_receipt_sha256 != dataset_receipt_sha
        or prepared.capture_manifest_sha256 != capture.manifest_digest
    ):
        raise RealSceneAcceptanceError("prepared capture evidence breaks source/capture closure")

    registration_bytes = payloads[report.registration.path]
    try:
        registration = RegistrationResult.model_validate_json(registration_bytes)
    except ValidationError as exc:
        raise RealSceneAcceptanceError(f"registration evidence is invalid: {exc}") from exc
    policy = _canonical_model_from_bytes(
        payloads[report.registration_policy.path],
        RegistrationQualityPolicy,
        label="registration quality policy",
    )
    registration_report = _canonical_model_from_bytes(
        payloads[report.registration_report.path],
        RegistrationQualityReport,
        label="registration quality report",
    )
    validate_registration_quality(
        registration_report,
        policy,
        registration_bytes,
        capture_manifest_bytes=payloads[report.capture_manifest.path],
        sparse_enumeration=registration_report.model_enumeration,
    )
    sfm_accepted = bool(
        registration.engine == "colmap"
        and registration_report.engine == "colmap"
        and registration_report.quality_accepted
        and registration_report.training_allowed
    )

    material = _load_training_material(root / report.training_root.path)
    if material.bundle_sha256 != report.training_bundle.sha256:
        raise RealSceneAcceptanceError("training bundle reference differs from verified material")
    registration_bindings = tuple(
        binding
        for binding in material.request.input_bindings
        if binding.artifact_kind == "registration_json"
    )
    if len(registration_bindings) != 1:
        raise RealSceneAcceptanceError("training request has no unique registration binding")
    training_registration = material.input_bytes_by_path.get(registration_bindings[0].artifact_path)
    if (
        training_registration is None
        or hashlib.sha256(training_registration).hexdigest() != report.registration.sha256
    ):
        raise RealSceneAcceptanceError("training registration differs from accepted SfM evidence")

    imported = validate_real_scene_import_receipt(
        root / report.import_receipt.path,
        root / report.import_root.path,
    )
    if (
        imported.source_role != report.source_role
        or imported.training_quality_role != material.quality_role
        or imported.training_bundle_sha256 != material.bundle_sha256
        or imported.training_request_sha256 != request_canonical_sha256(material.request)
        or imported.training_result_sha256 != result_canonical_sha256(material.result)
    ):
        raise RealSceneAcceptanceError("import receipt differs from source/training evidence")

    render_policy = _canonical_model_from_bytes(
        payloads[report.render_policy.path],
        RenderEvaluationPolicy,
        label="render evaluation policy",
        canonicalizer=canonical_render_evaluation_bytes,
    )
    render_report = _canonical_model_from_bytes(
        payloads[report.render_report.path],
        RenderEvaluationReport,
        label="render evaluation report",
        canonicalizer=canonical_render_evaluation_bytes,
    )
    held_out_bindings = tuple(
        binding
        for binding in material.request.input_bindings
        if binding.artifact_kind == "held_out_split"
    )
    if (
        len(held_out_bindings) != 1
        or held_out_bindings[0].artifact_sha256 != render_policy.held_out_split_sha256
    ):
        raise RealSceneAcceptanceError("render evaluation split differs from training request")
    render_decision = _validate_packaged_render_evaluation(
        policy=render_policy,
        report=render_report,
        training_bundle_path=root / report.training_bundle.path,
        remote_result_root=root / report.render_root.path,
    )
    render_policy_failures = _render_policy_failures(render_policy)

    viewer_policy = _viewer_model_from_bytes(
        payloads[report.viewer_policy.path],
        ViewerPerformancePolicy,
        label="viewer performance policy",
    )
    viewer_report = _viewer_model_from_bytes(
        payloads[report.viewer_report.path],
        ViewerPerformanceReport,
        label="viewer performance report",
    )
    scene_manifest_path = root / report.import_root.path / imported.manifest_path
    if (
        viewer_report.source_role != report.source_role
        or viewer_report.scene_manifest_sha256 != _sha256_file(scene_manifest_path)
    ):
        raise RealSceneAcceptanceError("viewer report differs from accepted scene manifest")
    viewer_decision = derive_viewer_decision(
        viewer_policy,
        viewer_report,
    )
    viewer_policy_failures = _viewer_policy_failures(viewer_policy)

    human_policy = _canonical_model_from_bytes(
        payloads[report.human_review_policy.path],
        HumanReviewPolicy,
        label="human review policy",
        canonicalizer=canonical_human_review_policy_bytes,
    )
    human_review = _canonical_model_from_bytes(
        payloads[report.human_visual_review.path],
        HumanVisualReview,
        label="human visual review",
        canonicalizer=canonical_human_review_bytes,
    )
    if (
        human_policy.source_role != report.source_role
        or human_policy.required_pose_ids != viewer_policy.required_pose_ids
    ):
        raise RealSceneAcceptanceError(
            "human review policy differs from viewer pose/source contract"
        )
    human_decision = validate_human_visual_review(
        human_policy,
        human_review,
        root,
    )
    human_failures: list[str] = []
    if human_decision.unknown_categories:
        human_failures.append("unknown categories: " + ", ".join(human_decision.unknown_categories))
    if human_decision.rejected_categories:
        human_failures.append(
            "rejected categories: " + ", ".join(human_decision.rejected_categories)
        )
    return _ValidatedAcceptanceEvidence(
        release_rights_allowed=release_rights_allowed,
        sfm_accepted=sfm_accepted,
        training_quality_role=material.quality_role,
        geometry_usability=imported.geometry_usability,
        target_units=imported.target_units,
        alignment_rms_m=imported.alignment_rms_m,
        render_accepted=(render_decision.accepted and not render_policy_failures),
        render_failures=(
            *render_policy_failures,
            *render_decision.failed_thresholds,
        ),
        viewer_accepted=(viewer_decision.accepted and not viewer_policy_failures),
        viewer_failures=(
            *viewer_policy_failures,
            *viewer_decision.failed_gates,
        ),
        human_accepted=human_decision.accepted,
        human_failures=tuple(human_failures),
    )


def _accepted_gate(gate: AcceptanceGateId) -> AcceptanceGate:
    return AcceptanceGate(gate=gate, state="accepted")


def _rejected_gate(
    gate: AcceptanceGateId,
    *reasons: str,
) -> AcceptanceGate:
    return AcceptanceGate(
        gate=gate,
        state="rejected",
        reasons=tuple(reasons) or ("gate rejected",),
    )


def _decision_from_evidence(
    report: RealSceneAcceptance,
    evidence: _ValidatedAcceptanceEvidence,
    *,
    report_sha256: str,
) -> AcceptanceDecision:
    gates: list[AcceptanceGate] = [
        _accepted_gate("dataset"),
        _accepted_gate("capture"),
        (
            _accepted_gate("sfm")
            if evidence.sfm_accepted
            else _rejected_gate(
                "sfm",
                "non-mock COLMAP registration gate is not accepted",
            )
        ),
        (
            _accepted_gate("production-training")
            if evidence.training_quality_role == "production"
            else _rejected_gate(
                "production-training",
                "preview-only training cannot satisfy acceptance",
            )
        ),
        _accepted_gate("import-integrity"),
        (
            _accepted_gate("render-quality")
            if evidence.render_accepted
            else _rejected_gate(
                "render-quality",
                *(evidence.render_failures or ("render gate rejected",)),
            )
        ),
        (
            _accepted_gate("viewer-performance")
            if evidence.viewer_accepted
            else _rejected_gate(
                "viewer-performance",
                *(evidence.viewer_failures or ("viewer gate rejected",)),
            )
        ),
        (
            _accepted_gate("human-review")
            if evidence.human_accepted
            else _rejected_gate(
                "human-review",
                *(evidence.human_failures or ("human review rejected",)),
            )
        ),
    ]
    if report.source_role == "internal-canary":
        gates.extend(
            (
                AcceptanceGate(
                    gate="release-rights",
                    state="not-applicable",
                    reasons=("internal canary is never a release source",),
                ),
                AcceptanceGate(
                    gate="metric-alignment",
                    state="not-applicable",
                    reasons=("internal canary remains arbitrary and unaligned",),
                ),
            )
        )
    else:
        gates.append(
            _accepted_gate("release-rights")
            if evidence.release_rights_allowed
            else _rejected_gate(
                "release-rights",
                "processing, redistribution, and release rights are not all authorized",
            )
        )
        metric = bool(
            evidence.geometry_usability == "metric-aligned"
            and evidence.target_units == "meters"
            and evidence.alignment_rms_m is not None
            and evidence.alignment_rms_m <= 0.25
        )
        gates.append(
            _accepted_gate("metric-alignment")
            if metric
            else _rejected_gate(
                "metric-alignment",
                "measured metric alignment is absent or exceeds 0.25 m RMS",
            )
        )
    rejected = tuple(gate for gate in gates if gate.state == "rejected")
    technical_ids = {
        "dataset",
        "capture",
        "sfm",
        "production-training",
        "import-integrity",
        "render-quality",
        "viewer-performance",
        "human-review",
        "metric-alignment",
    }
    technical_accepted = not any(
        gate.state == "rejected" and gate.gate in technical_ids for gate in gates
    )
    canary_accepted = bool(report.source_role == "internal-canary" and technical_accepted)
    production_release_allowed = bool(
        report.source_role == "production-acceptance"
        and technical_accepted
        and all(gate.state == "accepted" for gate in gates)
    )
    reasons = tuple(f"{gate.gate}: {reason}" for gate in rejected for reason in gate.reasons)
    return AcceptanceDecision(
        source_role=report.source_role,
        technical_accepted=technical_accepted,
        canary_accepted=canary_accepted,
        production_release_allowed=production_release_allowed,
        gates=tuple(gates),
        failed_gates=tuple(gate.gate for gate in rejected),
        reasons=reasons,
        report_sha256=report_sha256,
    )


_FORBIDDEN_AUTHORED_DECISIONS = frozenset(
    {
        "accepted",
        "technical_accepted",
        "canary_accepted",
        "production_release_allowed",
        "failed_gates",
        "gates",
    }
)


def _load_real_scene_acceptance(
    report_path: Path,
) -> tuple[RealSceneAcceptance, bytes, Path]:
    path = Path(report_path).expanduser().absolute()
    try:
        before = path.lstat()
    except OSError as exc:
        raise RealSceneAcceptanceError("real-scene acceptance report is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RealSceneAcceptanceError("real-scene acceptance report must be a regular file")
    if before.st_size <= 0 or before.st_size > _MAX_ACCEPTANCE_DOCUMENT_BYTES:
        raise RealSceneAcceptanceError("real-scene acceptance report length is invalid")
    try:
        payload = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise RealSceneAcceptanceError("real-scene acceptance report cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RealSceneAcceptanceError("real-scene acceptance report changed while read")
    parsed = _unique_json(
        payload,
        label="real-scene acceptance report",
    )
    if not isinstance(parsed, dict):
        raise RealSceneAcceptanceError("real-scene acceptance report must be an object")
    authored = sorted(_FORBIDDEN_AUTHORED_DECISIONS & set(parsed))
    if authored:
        raise RealSceneAcceptanceError(
            "report-authored aggregate decision is forbidden: " + ", ".join(authored)
        )
    try:
        report = RealSceneAcceptance.model_validate(parsed)
    except ValidationError as exc:
        raise RealSceneAcceptanceError(f"real-scene acceptance report is invalid: {exc}") from exc
    if payload != canonical_real_scene_acceptance_bytes(report):
        raise RealSceneAcceptanceError("real-scene acceptance report is not canonical")
    root = path.parent
    return report, payload, root


def validate_real_scene_acceptance(
    report_path: Path,
) -> AcceptanceDecision:
    """Reopen all real-scene evidence and derive the only trusted decision."""

    report, payload, root = _load_real_scene_acceptance(report_path)
    first_payloads = _preflight_acceptance_references(report, root)
    try:
        evidence = _validate_acceptance_evidence(
            report,
            root,
            first_payloads,
        )
    except RealSceneAcceptanceError:
        raise
    except (OSError, ValueError) as exc:
        raise RealSceneAcceptanceError(f"real-scene acceptance evidence is invalid: {exc}") from exc
    second_payloads = _preflight_acceptance_references(report, root)
    if first_payloads != second_payloads:
        raise RealSceneAcceptanceError("real-scene acceptance evidence changed during validation")
    return _decision_from_evidence(
        report,
        evidence,
        report_sha256=hashlib.sha256(payload).hexdigest(),
    )
