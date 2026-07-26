"""Content-addressed real-scene training inputs.

This module owns deterministic train/held-out partitioning and, later, the
portable training-job bundle. A split is an ordering decision over verified
capture identities; it does not promote capture, registration or geometry
trust.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pipeline.real_scene_capture import PreparedRealCapture
from pipeline.studio_revisions import canonical_manifest_bytes


class RealSceneTrainingError(ValueError):
    """Training inputs are incomplete, ambiguous or content-damaged."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _portable_path(value: str) -> str:
    parsed = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError("logical_path must be a portable relative POSIX path")
    return value


class TrainingImageIdentity(FrozenModel):
    """One capture image bound by both logical identity and measured bytes."""

    logical_path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)

    _validate_logical_path = field_validator("logical_path")(_portable_path)


class HeldOutSplit(FrozenModel):
    """Canonical content-ordered train/evaluation partition."""

    schema_id: Literal["nantai.held-out-split.v1"] = Field(
        default="nantai.held-out-split.v1",
        alias="schema",
        serialization_alias="schema",
    )
    selection_rule: Literal["sha256-logical-path-order-first-n"] = (
        "sha256-logical-path-order-first-n"
    )
    ratio: float = Field(gt=0.0, lt=1.0, allow_inf_nan=False)
    total_count: int = Field(ge=2)
    train: tuple[TrainingImageIdentity, ...] = Field(min_length=1)
    held_out: tuple[TrainingImageIdentity, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _partition_is_exact(self) -> HeldOutSplit:
        combined = (*self.held_out, *self.train)
        if len(combined) != self.total_count:
            raise ValueError("split members must equal total_count")
        if len(set(combined)) != len(combined):
            raise ValueError("split contains duplicate content identities")
        if tuple(sorted(self.held_out, key=_identity_key)) != self.held_out:
            raise ValueError("held_out identities must be content ordered")
        if tuple(sorted(self.train, key=_identity_key)) != self.train:
            raise ValueError("train identities must be content ordered")
        expected_held_out = _round_half_up(self.total_count, self.ratio)
        if len(self.held_out) != expected_held_out:
            raise ValueError("held_out count does not match ratio")
        return self


def _identity_key(identity: TrainingImageIdentity) -> tuple[str, str]:
    return identity.sha256, identity.logical_path


def _round_half_up(count: int, ratio: float) -> int:
    try:
        ratio_decimal = Decimal(str(ratio))
    except (InvalidOperation, ValueError) as exc:
        raise RealSceneTrainingError("ratio must be a finite number") from exc
    rounded = (Decimal(count) * ratio_decimal).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(rounded)


def held_out_split_canonical_bytes(split: HeldOutSplit) -> bytes:
    """Return sorted compact ASCII JSON with one trailing LF."""

    return (
        json.dumps(
            split.model_dump(mode="json", by_alias=True),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def build_held_out_split(
    capture: PreparedRealCapture,
    ratio: float = 0.10,
) -> HeldOutSplit:
    """Partition capture identities independently of manifest/filename order."""

    if (
        isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
        or not math.isfinite(float(ratio))
        or not 0.0 < float(ratio) < 1.0
    ):
        raise RealSceneTrainingError("ratio must be finite and between 0 and 1")
    manifest = capture.capture.manifest
    if manifest.output_count != len(manifest.payloads):
        raise RealSceneTrainingError(
            "capture output_count differs from payload count"
        )
    if len(manifest.payloads) < 2:
        raise RealSceneTrainingError(
            "capture requires at least two images for a held-out partition"
        )

    identities = tuple(
        TrainingImageIdentity(
            logical_path=payload.logical_path,
            sha256=payload.sha256,
        )
        for payload in manifest.payloads
    )
    if len(set(identities)) != len(identities):
        raise RealSceneTrainingError(
            "capture contains duplicate content identities"
        )

    manifest_sha256 = hashlib.sha256(
        canonical_manifest_bytes(manifest)
    ).hexdigest()
    if capture.capture.manifest_digest != manifest_sha256:
        raise RealSceneTrainingError(
            "capture manifest digest differs from canonical manifest bytes"
        )

    ordered = tuple(sorted(identities, key=_identity_key))
    held_out_count = _round_half_up(len(ordered), float(ratio))
    if held_out_count <= 0 or held_out_count >= len(ordered):
        raise RealSceneTrainingError(
            "ratio does not produce a non-empty train/held-out partition"
        )
    return HeldOutSplit(
        ratio=float(ratio),
        total_count=len(ordered),
        held_out=ordered[:held_out_count],
        train=ordered[held_out_count:],
    )
