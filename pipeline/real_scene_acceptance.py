"""Fail-closed human visual evidence and real-scene acceptance contracts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import zlib
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

from pipeline.real_dataset import canonical_model_bytes


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


def _portable_relative_path(value: str, *, label: str) -> str:
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or len(value) > 240
    ):
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
                "human review policy must use the complete ordered "
                "visual category contract"
            )
        return value

    @field_validator("required_pose_ids")
    @classmethod
    def _poses_are_unique_content_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        import re

        if (
            len(value) != len(set(value))
            or any(
                re.fullmatch(_POSE_PATTERN, item) is None
                for item in value
            )
        ):
            raise ValueError(
                "human review poses must be unique content-addressed ids"
            )
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
    dispositions: tuple[HumanReviewDisposition, ...] = Field(
        min_length=1
    )
    screenshots: tuple[HumanScreenshotBinding, ...] = Field(
        min_length=1
    )

    @field_validator("reviewer")
    @classmethod
    def _reviewer_is_bounded_text(cls, value: str) -> str:
        if (
            value != value.strip()
            or any(ord(character) < 32 for character in value)
        ):
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
        if (
            len(categories) != len(set(categories))
            or len(pose_ids) != len(set(pose_ids))
        ):
            raise ValueError(
                "human review categories and screenshots must be unique"
            )
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
    return "human-review-" + hashlib.sha256(
        _canonical_payload(payload)
    ).hexdigest()


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
        raise RealSceneAcceptanceError(
            "review root is unavailable"
        ) from exc
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise RealSceneAcceptanceError(
            "review root must be a non-symlink directory"
        )
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise RealSceneAcceptanceError(
                f"{label} is unavailable"
            ) from exc
        if stat.S_ISLNK(mode):
            raise RealSceneAcceptanceError(
                f"{label} contains a symlink"
            )
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
            raise RealSceneAcceptanceError(
                f"screenshot {binding.pose_id} is not a regular file"
            )
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise RealSceneAcceptanceError(
                f"screenshot {binding.pose_id} byte length is invalid"
            )
        payload = path.read_bytes()
        after = path.lstat()
    except RealSceneAcceptanceError:
        raise
    except OSError as exc:
        raise RealSceneAcceptanceError(
            f"screenshot {binding.pose_id} cannot be read"
        ) from exc
    if (
        _stat_signature(before) != _stat_signature(after)
        or len(payload) != before.st_size
    ):
        raise RealSceneAcceptanceError(
            f"screenshot {binding.pose_id} changed while being read"
        )
    return payload


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if not payload.startswith(_PNG_SIGNATURE):
        raise RealSceneAcceptanceError(
            "screenshot is not a lossless PNG"
        )
    offset = len(_PNG_SIGNATURE)
    chunks: list[bytes] = []
    idat_chunks: list[bytes] = []
    ihdr: bytes | None = None
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise RealSceneAcceptanceError(
                "screenshot is not a lossless PNG"
            )
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(payload):
            raise RealSceneAcceptanceError(
                "screenshot is not a lossless PNG"
            )
        kind = payload[offset + 4:offset + 8]
        data = payload[offset + 8:offset + 8 + length]
        declared_crc = struct.unpack(">I", payload[end - 4:end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != declared_crc:
            raise RealSceneAcceptanceError(
                "screenshot is not a lossless PNG"
            )
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
        raise RealSceneAcceptanceError(
            "screenshot is not a lossless PNG"
        )
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
        raise RealSceneAcceptanceError(
            "screenshot PNG format is unsupported"
        )
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
        raise RealSceneAcceptanceError(
            "screenshot PNG pixel data cannot decode"
        ) from exc
    if (
        len(pixels) != expected_bytes
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
        or any(
            pixels[row * (row_bytes + 1)] > 4
            for row in range(height)
        )
    ):
        raise RealSceneAcceptanceError(
            "screenshot PNG pixel data cannot decode"
        )
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
        policy = HumanReviewPolicy.model_validate(
            policy.model_dump(by_alias=True)
        )
    except (AttributeError, ValueError) as exc:
        raise RealSceneAcceptanceError(
            f"human review policy is invalid: {exc}"
        ) from exc
    extra_categories = set(dispositions) - set(
        policy.required_categories
    )
    if extra_categories:
        raise RealSceneAcceptanceError(
            "human review contains unsupported categories"
        )
    extra_poses = set(screenshots) - set(policy.required_pose_ids)
    missing_poses = set(policy.required_pose_ids) - set(screenshots)
    if extra_poses or missing_poses:
        raise RealSceneAcceptanceError(
            "human review screenshot pose set differs from policy"
        )
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
            raise RealSceneAcceptanceError(
                f"invalid disposition for {category}: {exc}"
            ) from exc
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
        "policy_sha256": hashlib.sha256(
            canonical_human_review_policy_bytes(policy)
        ).hexdigest(),
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
        raise RealSceneAcceptanceError(
            f"human visual review is invalid: {exc}"
        ) from exc


def _validated_review(
    review: HumanVisualReview,
) -> HumanVisualReview:
    if "accepted" in getattr(review, "__dict__", {}):
        raise RealSceneAcceptanceError(
            "review-authored aggregate acceptance is forbidden"
        )
    try:
        return HumanVisualReview.model_validate(
            review.model_dump(by_alias=True)
        )
    except (AttributeError, ValueError) as exc:
        raise RealSceneAcceptanceError(
            f"human visual review is invalid: {exc}"
        ) from exc


def validate_human_visual_review(
    policy: HumanReviewPolicy,
    review: HumanVisualReview,
    root: Path,
) -> HumanReviewDecision:
    """Reopen every screenshot and derive the human-review gate."""

    try:
        policy = HumanReviewPolicy.model_validate(
            policy.model_dump(by_alias=True)
        )
    except (AttributeError, ValueError) as exc:
        raise RealSceneAcceptanceError(
            f"human review policy is invalid: {exc}"
        ) from exc
    review = _validated_review(review)
    expected_policy_sha = hashlib.sha256(
        canonical_human_review_policy_bytes(policy)
    ).hexdigest()
    if review.policy_sha256 != expected_policy_sha:
        raise RealSceneAcceptanceError(
            "human review policy SHA-256 disagrees"
        )
    if review.source_role != policy.source_role:
        raise RealSceneAcceptanceError(
            "human review source role differs from policy"
        )
    categories = tuple(row.category for row in review.dispositions)
    if categories != policy.required_categories:
        raise RealSceneAcceptanceError(
            "human review category order differs from policy"
        )
    pose_ids = tuple(row.pose_id for row in review.screenshots)
    if pose_ids != policy.required_pose_ids:
        raise RealSceneAcceptanceError(
            "human review screenshot order differs from policy"
        )
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
                f"screenshot {binding.pose_id} SHA, byte length, "
                "or dimensions disagree"
            )
    unknown = tuple(
        row.category
        for row in review.dispositions
        if row.disposition == "unknown"
    )
    rejected = tuple(
        row.category
        for row in review.dispositions
        if row.disposition == "rejected"
    )
    return HumanReviewDecision(
        accepted=not unknown and not rejected,
        unknown_categories=unknown,
        rejected_categories=rejected,
        screenshot_count=len(review.screenshots),
    )
