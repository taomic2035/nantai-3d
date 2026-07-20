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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from . import canary
from .production_profile import (
    PRODUCTION_PROFILE_ID,
    ProductionCameraPlan,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)

PRODUCTION_CLEARANCE_POLICY_SCHEMA = (
    "nantai.synthetic-village.production-clearance-policy.v1"
)
PRODUCTION_CLEARANCE_EVIDENCE_SCHEMA = (
    "nantai.synthetic-village.production-camera-clearance-evidence.v1"
)
PRODUCTION_CLEARANCE_DECISION_SCHEMA = (
    "nantai.synthetic-village.production-camera-clearance-decision.v1"
)
PRODUCTION_CLEARANCE_REQUEST_SCHEMA = (
    "nantai.synthetic-village.production-clearance-request.v1"
)
PRODUCTION_CLEARANCE_REPORT_SCHEMA = (
    "nantai.synthetic-village.production-clearance-report.v1"
)
PRODUCTION_CLEARANCE_SAMPLE_GRID = (-0.9, -0.45, 0.0, 0.45, 0.9)
PRODUCTION_CLEARANCE_SAMPLE_POINTS = tuple(
    (sample_x, sample_y)
    for sample_y in PRODUCTION_CLEARANCE_SAMPLE_GRID
    for sample_x in PRODUCTION_CLEARANCE_SAMPLE_GRID
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProductionPreflightError(RuntimeError):
    """Fail-closed host validation error for clearance requests and reports."""


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
    stable_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    part_id: str | None = Field(default=None, min_length=1)
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


def _object_registry_sha256(
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
) -> str:
    return hashlib.sha256(
        canary._canonical_json_bytes(  # noqa: SLF001
            [row.model_dump(mode="json") for row in object_registry],
        ),
    ).hexdigest()


def _preflight_id_from_payload(payload: dict[str, object]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


class ProductionClearanceRequest(FrozenModel):
    """Canonical all-camera probe request bound to one exact Blender scene."""

    schema_version: Literal[
        "nantai.synthetic-village.production-clearance-request.v1"
    ] = PRODUCTION_CLEARANCE_REQUEST_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = (
        PRODUCTION_PROFILE_ID
    )
    production_plan: ProductionCameraPlan
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_camera_ids: tuple[str, ...] = Field(min_length=1, max_length=180)
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=130,
        max_length=130,
    )
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...] = Field(
        min_length=3,
        max_length=3,
    )
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...] = Field(
        min_length=15,
        max_length=15,
    )
    policy: ProductionClearancePolicy
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _validate_identities(self) -> ProductionClearanceRequest:
        expected_plan_sha256 = hashlib.sha256(
            canonical_production_plan_bytes(self.production_plan),
        ).hexdigest()
        if self.production_plan_sha256 != expected_plan_sha256:
            raise ValueError("production plan digest is invalid")
        if (
            self.camera_registry_sha256
            != production_camera_registry_digest(self.production_plan)
        ):
            raise ValueError("production camera registry digest is invalid")
        all_camera_ids = tuple(
            row.camera_id for row in self.production_plan.cameras
        )
        selected = set(self.selected_camera_ids)
        expected_selected = tuple(
            row for row in all_camera_ids if row in selected
        )
        if (
            len(selected) != len(self.selected_camera_ids)
            or self.selected_camera_ids != expected_selected
        ):
            raise ValueError(
                "selected camera IDs must be a unique plan-ordered subset",
            )
        if self.object_registry_sha256 != _object_registry_sha256(
            self.object_registry,
        ):
            raise ValueError("object registry digest is invalid")
        if tuple(row.instance_id for row in self.object_registry) != tuple(
            range(1, 131),
        ):
            raise ValueError("object registry is not the stable 130-instance contract")
        if self.auxiliary_registry != canary.AUXILIARY_REGISTRY:
            raise ValueError("auxiliary registry is not stable")
        if self.semantic_registry != canary._semantic_registry():  # noqa: SLF001
            raise ValueError("semantic registry is not stable")
        if self.policy_sha256 != production_clearance_policy_sha256(
            self.policy,
        ):
            raise ValueError("clearance policy digest is invalid")
        payload = self.model_dump(mode="json", exclude={"preflight_id"})
        if self.preflight_id != _preflight_id_from_payload(payload):
            raise ValueError("preflight ID does not bind the request inputs")
        return self


class ProductionClearanceReport(FrozenModel):
    """Runtime evidence whose identities must be verified against its request."""

    schema_version: Literal[
        "nantai.synthetic-village.production-clearance-report.v1"
    ] = PRODUCTION_CLEARANCE_REPORT_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = (
        PRODUCTION_PROFILE_ID
    )
    preflight_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: tuple[ProductionCameraClearanceEvidence, ...] = Field(
        min_length=1,
        max_length=180,
    )
    decisions: tuple[ProductionCameraClearanceDecision, ...] = Field(
        min_length=1,
        max_length=180,
    )
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )


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


def build_production_clearance_request(
    *,
    plan: ProductionCameraPlan,
    selected_camera_ids: tuple[str, ...],
    build_id: str,
    blender_executable_sha256: str,
    preflight_script_sha256: str,
    blend_sha256: str,
    build_report_sha256: str,
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...],
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...],
    policy: ProductionClearancePolicy,
) -> ProductionClearanceRequest:
    """Build and content-address one exact scene-bound preflight request."""

    payload: dict[str, object] = {
        "schema_version": PRODUCTION_CLEARANCE_REQUEST_SCHEMA,
        "profile_id": PRODUCTION_PROFILE_ID,
        "production_plan": plan.model_dump(mode="json"),
        "production_plan_sha256": hashlib.sha256(
            canonical_production_plan_bytes(plan),
        ).hexdigest(),
        "camera_registry_sha256": production_camera_registry_digest(plan),
        "selected_camera_ids": list(selected_camera_ids),
        "build_id": build_id,
        "blender_executable_sha256": blender_executable_sha256,
        "preflight_script_sha256": preflight_script_sha256,
        "blend_sha256": blend_sha256,
        "build_report_sha256": build_report_sha256,
        "object_registry_sha256": _object_registry_sha256(object_registry),
        "object_registry": [
            row.model_dump(mode="json") for row in object_registry
        ],
        "auxiliary_registry": [
            row.model_dump(mode="json") for row in auxiliary_registry
        ],
        "semantic_registry": [
            row.model_dump(mode="json") for row in semantic_registry
        ],
        "policy": policy.model_dump(mode="json"),
        "policy_sha256": production_clearance_policy_sha256(policy),
        "synthetic": True,
        "geometry_trust": "simplified-pbr-not-render-parity",
        "trust_effect": "none-quality-filter-only",
    }
    payload["preflight_id"] = _preflight_id_from_payload(payload)
    return ProductionClearanceRequest.model_validate_json(_canonical(payload))


def canonical_production_clearance_request_bytes(
    request: ProductionClearanceRequest,
) -> bytes:
    return _canonical(request.model_dump(mode="json"))


def production_clearance_preflight_id(
    request: ProductionClearanceRequest,
) -> str:
    payload = request.model_dump(mode="json", exclude={"preflight_id"})
    return _preflight_id_from_payload(payload)


def production_clearance_request_sha256(
    request: ProductionClearanceRequest,
) -> str:
    return hashlib.sha256(
        canonical_production_clearance_request_bytes(request),
    ).hexdigest()


def build_production_clearance_report(
    request: ProductionClearanceRequest,
    *,
    evidence: tuple[ProductionCameraClearanceEvidence, ...],
) -> ProductionClearanceReport:
    """Build a report from raw evidence using only the bound request policy."""

    evidence_camera_ids = tuple(row.camera_id for row in evidence)
    if evidence_camera_ids != request.selected_camera_ids:
        raise ProductionPreflightError(
            "clearance evidence camera set disagrees with request",
        )
    decisions = tuple(
        evaluate_production_camera_clearance(row, policy=request.policy)
        for row in evidence
    )
    return ProductionClearanceReport(
        preflight_id=request.preflight_id,
        request_sha256=production_clearance_request_sha256(request),
        production_plan_sha256=request.production_plan_sha256,
        camera_registry_sha256=request.camera_registry_sha256,
        build_id=request.build_id,
        blender_executable_sha256=request.blender_executable_sha256,
        preflight_script_sha256=request.preflight_script_sha256,
        blend_sha256=request.blend_sha256,
        build_report_sha256=request.build_report_sha256,
        object_registry_sha256=request.object_registry_sha256,
        policy_sha256=request.policy_sha256,
        evidence=evidence,
        decisions=decisions,
    )


def canonical_production_clearance_report_bytes(
    report: ProductionClearanceReport,
) -> bytes:
    return _canonical(report.model_dump(mode="json"))


def parse_production_clearance_report_bytes(
    raw: bytes,
) -> ProductionClearanceReport:
    """Parse only bounded, duplicate-free canonical report bytes."""

    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > 32 * 1024 * 1024
    ):
        raise ProductionPreflightError(
            "clearance report size is invalid",
        )

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ProductionPreflightError(
                    f"clearance report contains duplicate JSON key: {key}",
                )
            result[key] = value
        return result

    try:
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProductionPreflightError(
                    f"clearance report contains non-finite JSON number: {value}",
                ),
            ),
        )
        report = ProductionClearanceReport.model_validate_json(raw)
    except ProductionPreflightError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ProductionPreflightError(
            "clearance report is invalid UTF-8 JSON",
        ) from exc
    if raw != canonical_production_clearance_report_bytes(report):
        raise ProductionPreflightError(
            "clearance report must be canonical JSON",
        )
    return report


def verify_production_clearance_report(
    report: ProductionClearanceReport,
    *,
    request: ProductionClearanceRequest,
) -> None:
    """Recompute every identity and decision; raise on any contradiction."""

    actual_identities = (
        report.preflight_id,
        report.request_sha256,
        report.production_plan_sha256,
        report.camera_registry_sha256,
        report.build_id,
        report.blender_executable_sha256,
        report.preflight_script_sha256,
        report.blend_sha256,
        report.build_report_sha256,
        report.object_registry_sha256,
        report.policy_sha256,
    )
    expected_identities = (
        request.preflight_id,
        production_clearance_request_sha256(request),
        request.production_plan_sha256,
        request.camera_registry_sha256,
        request.build_id,
        request.blender_executable_sha256,
        request.preflight_script_sha256,
        request.blend_sha256,
        request.build_report_sha256,
        request.object_registry_sha256,
        request.policy_sha256,
    )
    if actual_identities != expected_identities:
        raise ProductionPreflightError(
            "clearance report identity disagrees with request",
        )
    evidence_camera_ids = tuple(row.camera_id for row in report.evidence)
    decision_camera_ids = tuple(row.camera_id for row in report.decisions)
    if (
        evidence_camera_ids != request.selected_camera_ids
        or decision_camera_ids != request.selected_camera_ids
    ):
        raise ProductionPreflightError(
            "clearance report camera set disagrees with request",
        )
    expected_decisions = tuple(
        evaluate_production_camera_clearance(row, policy=request.policy)
        for row in report.evidence
    )
    if report.decisions != expected_decisions:
        raise ProductionPreflightError(
            "clearance report decision disagrees with raw evidence and policy",
        )
