"""Fail-closed production-camera clearance evidence and operator policy.

The ray rows in this module are raw scene measurements.  The threshold is an
explicit, versioned operator policy.  A passing decision only means that this
one training-suitability filter found no declared obstruction; it never
upgrades geometry, renderer, metric, alignment, or coverage trust.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PRODUCTION_CLEARANCE_POLICY_SCHEMA = (
    "nantai.synthetic-village.production-clearance-policy.v1"
)
PRODUCTION_CLEARANCE_EVIDENCE_SCHEMA = (
    "nantai.synthetic-village.production-camera-clearance-evidence.v1"
)
PRODUCTION_CLEARANCE_DECISION_SCHEMA = (
    "nantai.synthetic-village.production-camera-clearance-decision.v1"
)
PRODUCTION_CLEARANCE_SAMPLE_GRID = (-0.9, -0.45, 0.0, 0.45, 0.9)
PRODUCTION_CLEARANCE_SAMPLE_POINTS = tuple(
    (sample_x, sample_y)
    for sample_y in PRODUCTION_CLEARANCE_SAMPLE_GRID
    for sample_x in PRODUCTION_CLEARANCE_SAMPLE_GRID
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


class ProductionClearancePolicy(FrozenModel):
    """Operator-selected synthetic-village clearance threshold."""

    schema_version: Literal[
        "nantai.synthetic-village.production-clearance-policy.v1"
    ] = PRODUCTION_CLEARANCE_POLICY_SCHEMA
    policy_id: Literal["synthetic-village-clearance-v1"] = (
        "synthetic-village-clearance-v1"
    )
    sample_grid: tuple[float, ...] = PRODUCTION_CLEARANCE_SAMPLE_GRID
    upper_middle_min_sample_y: Literal[0.0] = 0.0
    near_distance_m: float = Field(
        gt=0.0,
        le=100.0,
        allow_inf_nan=False,
    )
    minimum_upper_middle_near_hit_count: int = Field(ge=1, le=15)
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _validate_fixed_grid(self) -> ProductionClearancePolicy:
        if self.sample_grid != PRODUCTION_CLEARANCE_SAMPLE_GRID:
            raise ValueError(
                "clearance policy must use the versioned fixed 5x5 sample grid",
            )
        return self


class ProductionClearanceRayEvidence(FrozenModel):
    """One first-hit measurement; absent registry fields remain unknown."""

    sample_x: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    sample_y: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    hit: bool
    distance_m: float | None = Field(
        default=None,
        gt=0.0,
        allow_inf_nan=False,
    )
    object_name: str | None = Field(default=None, min_length=1)
    stable_id: int | None = Field(default=None, ge=0)
    part_id: int | None = Field(default=None, ge=0)
    semantic_id: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_hit_fields(self) -> ProductionClearanceRayEvidence:
        identity = (
            self.object_name,
            self.stable_id,
            self.part_id,
            self.semantic_id,
        )
        if self.hit:
            if self.distance_m is None:
                raise ValueError("hit requires a measured distance")
        elif self.distance_m is not None or any(row is not None for row in identity):
            raise ValueError("miss cannot carry hit evidence")
        return self


class ProductionCameraClearanceEvidence(FrozenModel):
    """The exact ordered 5x5 ray set for one production camera."""

    schema_version: Literal[
        "nantai.synthetic-village.production-camera-clearance-evidence.v1"
    ] = PRODUCTION_CLEARANCE_EVIDENCE_SCHEMA
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    rays: tuple[ProductionClearanceRayEvidence, ...] = Field(
        min_length=25,
        max_length=25,
    )

    @model_validator(mode="after")
    def _validate_sample_grid(self) -> ProductionCameraClearanceEvidence:
        points = tuple((row.sample_x, row.sample_y) for row in self.rays)
        if points != PRODUCTION_CLEARANCE_SAMPLE_POINTS:
            raise ValueError(
                "clearance evidence must use the exact fixed 5x5 sample grid",
            )
        return self


class ProductionCameraClearanceDecision(FrozenModel):
    """Policy evaluation over one immutable raw evidence object."""

    schema_version: Literal[
        "nantai.synthetic-village.production-camera-clearance-decision.v1"
    ] = PRODUCTION_CLEARANCE_DECISION_SCHEMA
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    measured_upper_middle_near_hit_count: int = Field(ge=0, le=15)
    passes: bool
    failed_rule_ids: tuple[
        Literal["upper-middle-near-hit-count"],
        ...,
    ] = ()
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _validate_decision_shape(self) -> ProductionCameraClearanceDecision:
        expected_failures = () if self.passes else ("upper-middle-near-hit-count",)
        if self.failed_rule_ids != expected_failures:
            raise ValueError("clearance pass flag disagrees with failed rule IDs")
        return self


def canonical_production_clearance_policy_bytes(
    policy: ProductionClearancePolicy,
) -> bytes:
    return _canonical(policy.model_dump(mode="json"))


def production_clearance_policy_sha256(
    policy: ProductionClearancePolicy,
) -> str:
    return hashlib.sha256(
        canonical_production_clearance_policy_bytes(policy),
    ).hexdigest()


def canonical_production_camera_clearance_evidence_bytes(
    evidence: ProductionCameraClearanceEvidence,
) -> bytes:
    return _canonical(evidence.model_dump(mode="json"))


def production_camera_clearance_evidence_sha256(
    evidence: ProductionCameraClearanceEvidence,
) -> str:
    return hashlib.sha256(
        canonical_production_camera_clearance_evidence_bytes(evidence),
    ).hexdigest()


def evaluate_production_camera_clearance(
    evidence: ProductionCameraClearanceEvidence,
    *,
    policy: ProductionClearancePolicy,
) -> ProductionCameraClearanceDecision:
    """Apply an explicit threshold without mutating or promoting raw evidence."""

    near_hit_count = sum(
        1
        for row in evidence.rays
        if (
            row.hit
            and row.sample_y >= policy.upper_middle_min_sample_y
            and row.distance_m is not None
            and row.distance_m < policy.near_distance_m
        )
    )
    passes = near_hit_count < policy.minimum_upper_middle_near_hit_count
    return ProductionCameraClearanceDecision(
        camera_id=evidence.camera_id,
        policy_sha256=production_clearance_policy_sha256(policy),
        evidence_sha256=production_camera_clearance_evidence_sha256(evidence),
        measured_upper_middle_near_hit_count=near_hit_count,
        passes=passes,
        failed_rule_ids=() if passes else ("upper-middle-near-hit-count",),
    )
