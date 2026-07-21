"""Evidence-bound post-render quality policy for production camera frames."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import canary
from .production_journal import (
    ProductionArtifactRecord,
    expected_production_artifacts,
)
from .production_profile import (
    PRODUCTION_PROFILE_ID,
    ProductionCameraPlan,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)

POLICY_SCHEMA = "nantai.synthetic-village.production-frame-quality-policy.v2"
STATISTICS_SCHEMA = (
    "nantai.synthetic-village.production-frame-layer-statistics.v2"
)
REQUEST_SCHEMA = "nantai.synthetic-village.production-frame-quality-request.v2"
DECISION_SCHEMA = "nantai.synthetic-village.production-frame-quality-decision.v2"
REPORT_SCHEMA = "nantai.synthetic-village.production-frame-quality-report.v2"
TOTAL_FRAME_PIXELS = 1024 * 576
Sha256 = str

QualityRuleId = Literal[
    "depth-near-concentration",
    "near-instance-dominance",
    "sky-dominance",
    "upper-ground-dominance",
    "upper-instance-dominance",
    "valid-depth-pixel-ratio",
    "valid-normal-pixel-ratio",
    "valid-semantic-pixel-ratio",
]
_MINIMUM_RULES = frozenset(
    {
        "valid-depth-pixel-ratio",
        "valid-normal-pixel-ratio",
        "valid-semantic-pixel-ratio",
    },
)
_MAXIMUM_RULES = frozenset(
    {
        "depth-near-concentration",
        "near-instance-dominance",
        "sky-dominance",
        "upper-ground-dominance",
        "upper-instance-dominance",
    },
)
_RULE_IDS = tuple(sorted((*_MINIMUM_RULES, *_MAXIMUM_RULES)))


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProductionFrameQualityError(RuntimeError):
    """Quality evidence cannot be verified without inventing trust."""


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class ProductionFrameQualityRule(FrozenModel):
    rule_id: QualityRuleId
    rule_version: Literal["v2"] = "v2"
    threshold: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    description: str = Field(min_length=20)

    @property
    def is_minimum(self) -> bool:
        return self.rule_id in _MINIMUM_RULES


class ProductionFrameQualityPolicyV2(FrozenModel):
    """Operator policy including every measurement convention it relies on."""

    schema_version: Literal[
        "nantai.synthetic-village.production-frame-quality-policy.v2"
    ] = POLICY_SCHEMA
    policy_id: Literal["synthetic-village-frame-quality-v2"] = (
        "synthetic-village-frame-quality-v2"
    )
    near_depth_m: float = Field(gt=0.0, le=100.0, allow_inf_nan=False)
    upper_region_end_row_exclusive: int = Field(ge=1, lt=576)
    ground_semantic_ids: tuple[int, ...] = Field(min_length=1)
    sky_semantic_id: Literal[0] = 0
    ratio_round_digits: Literal[6] = 6
    near_depth_denominator: Literal[
        "all-pixels",
        "valid-depth-pixels",
    ] = "valid-depth-pixels"
    upper_dominance_denominator: Literal[
        "upper-region-pixels"
    ] = "upper-region-pixels"
    near_instance_dominance_denominator: Literal[
        "near-depth-pixels"
    ] = "near-depth-pixels"
    rules: tuple[ProductionFrameQualityRule, ...] = Field(
        min_length=8,
        max_length=8,
    )

    @model_validator(mode="after")
    def _validate_policy(self) -> ProductionFrameQualityPolicyV2:
        if tuple(row.rule_id for row in self.rules) != _RULE_IDS:
            raise ValueError(
                "policy rules must be the exact sorted eight-rule contract",
            )
        if (
            tuple(sorted(set(self.ground_semantic_ids)))
            != self.ground_semantic_ids
            or any(row <= self.sky_semantic_id or row > 255 for row in self.ground_semantic_ids)
        ):
            raise ValueError(
                "ground semantic IDs must be unique, sorted, non-background IDs",
            )
        return self


def canonical_production_frame_quality_policy_v2_bytes(
    policy: ProductionFrameQualityPolicyV2,
) -> bytes:
    return _canonical(policy.model_dump(mode="json"))


def production_frame_quality_policy_v2_sha256(
    policy: ProductionFrameQualityPolicyV2,
) -> str:
    return _sha256(canonical_production_frame_quality_policy_v2_bytes(policy))


def candidate_synthetic_village_frame_quality_policy_v2(
    *,
    minimum_valid_depth_pixel_ratio: float,
    minimum_valid_normal_pixel_ratio: float,
    minimum_valid_semantic_pixel_ratio: float,
    maximum_sky_pixel_ratio: float,
    maximum_upper_ground_pixel_ratio: float,
    maximum_near_depth_pixel_ratio: float,
    maximum_near_instance_dominance_ratio: float,
    maximum_upper_instance_dominance_ratio: float,
    near_depth_m: float,
    upper_region_end_row_exclusive: int,
    ground_semantic_ids: tuple[int, ...],
) -> ProductionFrameQualityPolicyV2:
    """Build a named candidate; production callers must pass every threshold."""

    thresholds = {
        "depth-near-concentration": (
            maximum_near_depth_pixel_ratio,
            "Maximum fraction of the declared depth denominator nearer than cutoff.",
        ),
        "near-instance-dominance": (
            maximum_near_instance_dominance_ratio,
            "Maximum share of near-depth pixels owned by one registered instance.",
        ),
        "sky-dominance": (
            maximum_sky_pixel_ratio,
            "Maximum all-frame share carrying the declared sky semantic ID.",
        ),
        "upper-ground-dominance": (
            maximum_upper_ground_pixel_ratio,
            "Maximum upper-region share carrying a declared ground semantic ID.",
        ),
        "upper-instance-dominance": (
            maximum_upper_instance_dominance_ratio,
            "Maximum upper-region share owned by one registered nonzero instance.",
        ),
        "valid-depth-pixel-ratio": (
            minimum_valid_depth_pixel_ratio,
            "Minimum all-frame share with finite positive depth evidence.",
        ),
        "valid-normal-pixel-ratio": (
            minimum_valid_normal_pixel_ratio,
            "Minimum all-frame share with finite unit-normal evidence.",
        ),
        "valid-semantic-pixel-ratio": (
            minimum_valid_semantic_pixel_ratio,
            "Minimum all-frame share carrying a registered semantic ID.",
        ),
    }
    return ProductionFrameQualityPolicyV2(
        near_depth_m=near_depth_m,
        upper_region_end_row_exclusive=upper_region_end_row_exclusive,
        ground_semantic_ids=ground_semantic_ids,
        rules=tuple(
            ProductionFrameQualityRule(
                rule_id=rule_id,
                threshold=thresholds[rule_id][0],
                description=thresholds[rule_id][1],
            )
            for rule_id in _RULE_IDS
        ),
    )


class ProductionFrameLayerStatistics(FrozenModel):
    """Raw integer counts emitted from decoded frame buffers."""

    schema_version: Literal[
        "nantai.synthetic-village.production-frame-layer-statistics.v2"
    ] = STATISTICS_SCHEMA
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    total_pixel_count: Literal[TOTAL_FRAME_PIXELS] = TOTAL_FRAME_PIXELS
    upper_pixel_count: int = Field(ge=1, lt=TOTAL_FRAME_PIXELS)
    valid_depth_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)
    valid_normal_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)
    registered_instance_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)
    valid_semantic_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)
    sky_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)
    upper_ground_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)
    near_depth_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)
    dominant_near_instance_id: int | None = Field(default=None, ge=1)
    dominant_near_instance_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)
    dominant_upper_instance_id: int | None = Field(default=None, ge=1)
    dominant_upper_instance_pixel_count: int = Field(ge=0, le=TOTAL_FRAME_PIXELS)

    @model_validator(mode="after")
    def _counts_fit_declared_denominators(
        self,
    ) -> ProductionFrameLayerStatistics:
        if (
            self.upper_ground_pixel_count > self.upper_pixel_count
            or self.near_depth_pixel_count > self.valid_depth_pixel_count
            or self.dominant_near_instance_pixel_count
            > self.near_depth_pixel_count
            or self.dominant_upper_instance_pixel_count
            > self.upper_pixel_count
        ):
            raise ValueError("layer statistic count exceeds its denominator")
        for label, instance_id, count in (
            (
                "near",
                self.dominant_near_instance_id,
                self.dominant_near_instance_pixel_count,
            ),
            (
                "upper",
                self.dominant_upper_instance_id,
                self.dominant_upper_instance_pixel_count,
            ),
        ):
            if (count > 0) != (instance_id is not None):
                raise ValueError(
                    f"dominant {label} count and registered instance ID disagree",
                )
        return self

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    @property
    def valid_depth_pixel_ratio(self) -> float:
        return self._ratio(self.valid_depth_pixel_count, self.total_pixel_count)

    @property
    def valid_normal_pixel_ratio(self) -> float:
        return self._ratio(self.valid_normal_pixel_count, self.total_pixel_count)

    @property
    def registered_instance_pixel_ratio(self) -> float:
        return self._ratio(
            self.registered_instance_pixel_count,
            self.total_pixel_count,
        )

    @property
    def valid_semantic_pixel_ratio(self) -> float:
        return self._ratio(
            self.valid_semantic_pixel_count,
            self.total_pixel_count,
        )

    @property
    def sky_pixel_ratio(self) -> float:
        return self._ratio(self.sky_pixel_count, self.total_pixel_count)

    @property
    def upper_ground_pixel_ratio(self) -> float:
        return self._ratio(
            self.upper_ground_pixel_count,
            self.upper_pixel_count,
        )

    @property
    def near_depth_pixel_ratio(self) -> float:
        return self._ratio(
            self.near_depth_pixel_count,
            self.valid_depth_pixel_count,
        )

    @property
    def near_instance_dominance_ratio(self) -> float:
        return self._ratio(
            self.dominant_near_instance_pixel_count,
            self.near_depth_pixel_count,
        )

    @property
    def upper_instance_dominance_ratio(self) -> float:
        return self._ratio(
            self.dominant_upper_instance_pixel_count,
            self.upper_pixel_count,
        )


def canonical_production_frame_layer_statistics_bytes(
    statistics: ProductionFrameLayerStatistics,
) -> bytes:
    return _canonical(statistics.model_dump(mode="json"))


def production_frame_layer_statistics_sha256(
    statistics: ProductionFrameLayerStatistics,
) -> str:
    return _sha256(canonical_production_frame_layer_statistics_bytes(statistics))


class ProductionFrameEvidenceBinding(FrozenModel):
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    runtime_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: tuple[ProductionArtifactRecord, ...] = Field(
        min_length=6,
        max_length=6,
    )

    @model_validator(mode="after")
    def _exact_six_files(self) -> ProductionFrameEvidenceBinding:
        if tuple((row.kind, row.path) for row in self.artifacts) != (
            expected_production_artifacts(self.camera_id)
        ):
            raise ValueError("frame evidence is not the exact six-file contract")
        return self


def _object_registry_sha256(object_registry: tuple[object, ...]) -> str:
    return _sha256(
        canary._canonical_json_bytes(
            [row.model_dump(mode="json") for row in object_registry],
        ),
    )


class ProductionFrameQualityRequestV2(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.production-frame-quality-request.v2"
    ] = REQUEST_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = (
        PRODUCTION_PROFILE_ID
    )
    production_plan: ProductionCameraPlan
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_camera_ids: tuple[str, ...] = Field(min_length=1, max_length=180)
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=130,
    )
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...] = Field(
        min_length=15,
    )
    frames: tuple[ProductionFrameEvidenceBinding, ...] = Field(
        min_length=1,
        max_length=180,
    )
    policy: ProductionFrameQualityPolicyV2
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _verify_all_identities(self) -> ProductionFrameQualityRequestV2:
        plan_sha = _sha256(canonical_production_plan_bytes(self.production_plan))
        if (
            self.production_plan_sha256 != plan_sha
            or self.camera_registry_sha256
            != production_camera_registry_digest(self.production_plan)
            or self.object_registry_sha256
            != _object_registry_sha256(self.object_registry)
            or self.policy_sha256
            != production_frame_quality_policy_v2_sha256(self.policy)
        ):
            raise ValueError("quality request identity is invalid")
        plan_ids = tuple(row.camera_id for row in self.production_plan.cameras)
        selected = set(self.selected_camera_ids)
        if (
            len(selected) != len(self.selected_camera_ids)
            or self.selected_camera_ids
            != tuple(row for row in plan_ids if row in selected)
            or tuple(row.camera_id for row in self.frames)
            != self.selected_camera_ids
        ):
            raise ValueError(
                "selected cameras and frame evidence must be one plan-ordered set",
            )
        semantic_ids = {row.semantic_id for row in self.semantic_registry}
        if (
            self.policy.sky_semantic_id not in semantic_ids
            or not set(self.policy.ground_semantic_ids) <= semantic_ids
        ):
            raise ValueError("quality policy references an unknown semantic ID")
        expected = _sha256(
            canonical_production_frame_quality_request_v2_bytes(
                self,
                exclude_request_id=True,
            ),
        )
        if self.request_id != expected:
            raise ValueError("quality request ID does not bind every input")
        return self


def canonical_production_frame_quality_request_v2_bytes(
    request: ProductionFrameQualityRequestV2,
    *,
    exclude_request_id: bool = False,
) -> bytes:
    exclude = {"request_id"} if exclude_request_id else None
    return _canonical(request.model_dump(mode="json", exclude=exclude))


def build_production_frame_quality_request_v2(
    *,
    plan: ProductionCameraPlan,
    selected_camera_ids: tuple[str, ...],
    build_id: str,
    render_id: str,
    blender_executable_sha256: str,
    renderer_script_sha256: str,
    blend_sha256: str,
    build_report_sha256: str,
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...],
    journal_sha256: str,
    frames: tuple[ProductionFrameEvidenceBinding, ...],
    policy: ProductionFrameQualityPolicyV2,
) -> ProductionFrameQualityRequestV2:
    payload = {
        "schema_version": REQUEST_SCHEMA,
        "profile_id": PRODUCTION_PROFILE_ID,
        "production_plan": plan,
        "production_plan_sha256": _sha256(
            canonical_production_plan_bytes(plan),
        ),
        "camera_registry_sha256": production_camera_registry_digest(plan),
        "selected_camera_ids": selected_camera_ids,
        "build_id": build_id,
        "render_id": render_id,
        "blender_executable_sha256": blender_executable_sha256,
        "renderer_script_sha256": renderer_script_sha256,
        "blend_sha256": blend_sha256,
        "build_report_sha256": build_report_sha256,
        "journal_sha256": journal_sha256,
        "object_registry_sha256": _object_registry_sha256(object_registry),
        "object_registry": object_registry,
        "semantic_registry": semantic_registry,
        "frames": frames,
        "policy": policy,
        "policy_sha256": production_frame_quality_policy_v2_sha256(policy),
        "synthetic": True,
        "geometry_trust": "simplified-pbr-not-render-parity",
        "trust_effect": "none-quality-filter-only",
    }
    unsigned = ProductionFrameQualityRequestV2.model_construct(
        request_id="0" * 64,
        **payload,
    )
    request_id = _sha256(
        canonical_production_frame_quality_request_v2_bytes(
            unsigned,
            exclude_request_id=True,
        ),
    )
    return ProductionFrameQualityRequestV2(
        request_id=request_id,
        **payload,
    )


class ProductionFrameQualityRuleDecision(FrozenModel):
    rule_id: QualityRuleId
    rule_version: Literal["v2"] = "v2"
    threshold: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    measured: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    comparison: Literal["minimum", "maximum"]
    passes: bool
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _decision_matches_numbers(self) -> ProductionFrameQualityRuleDecision:
        expected_comparison = (
            "minimum" if self.rule_id in _MINIMUM_RULES else "maximum"
        )
        expected_passes = (
            self.measured >= self.threshold
            if expected_comparison == "minimum"
            else self.measured <= self.threshold
        )
        if (
            self.comparison != expected_comparison
            or self.passes != expected_passes
        ):
            raise ValueError("rule decision disagrees with measured evidence")
        return self


class ProductionFrameQualityDecisionV2(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.production-frame-quality-decision.v2"
    ] = DECISION_SCHEMA
    camera_id: str
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    statistics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_decisions: tuple[ProductionFrameQualityRuleDecision, ...] = Field(
        min_length=8,
        max_length=8,
    )
    passes: bool
    failed_rule_ids: tuple[QualityRuleId, ...]
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _aggregate_matches_rules(self) -> ProductionFrameQualityDecisionV2:
        if tuple(row.rule_id for row in self.rule_decisions) != _RULE_IDS:
            raise ValueError("decision rule set is incomplete or reordered")
        failures = tuple(
            row.rule_id for row in self.rule_decisions if not row.passes
        )
        if self.failed_rule_ids != failures or self.passes != (not failures):
            raise ValueError("aggregate decision disagrees with rule decisions")
        return self


def _measured_ratio(
    statistics: ProductionFrameLayerStatistics,
    rule_id: QualityRuleId,
    policy: ProductionFrameQualityPolicyV2,
) -> float:
    if rule_id == "depth-near-concentration":
        denominator = (
            statistics.total_pixel_count
            if policy.near_depth_denominator == "all-pixels"
            else statistics.valid_depth_pixel_count
        )
        return statistics._ratio(statistics.near_depth_pixel_count, denominator)
    return {
        "near-instance-dominance": statistics.near_instance_dominance_ratio,
        "sky-dominance": statistics.sky_pixel_ratio,
        "upper-ground-dominance": statistics.upper_ground_pixel_ratio,
        "upper-instance-dominance": statistics.upper_instance_dominance_ratio,
        "valid-depth-pixel-ratio": statistics.valid_depth_pixel_ratio,
        "valid-normal-pixel-ratio": statistics.valid_normal_pixel_ratio,
        "valid-semantic-pixel-ratio": statistics.valid_semantic_pixel_ratio,
    }[rule_id]


def evaluate_production_frame_quality_v2(
    statistics: ProductionFrameLayerStatistics,
    *,
    policy: ProductionFrameQualityPolicyV2,
) -> ProductionFrameQualityDecisionV2:
    decisions = tuple(
        ProductionFrameQualityRuleDecision(
            rule_id=rule.rule_id,
            threshold=rule.threshold,
            measured=_measured_ratio(statistics, rule.rule_id, policy),
            comparison="minimum" if rule.is_minimum else "maximum",
            passes=(
                _measured_ratio(statistics, rule.rule_id, policy)
                >= rule.threshold
                if rule.is_minimum
                else _measured_ratio(statistics, rule.rule_id, policy)
                <= rule.threshold
            ),
        )
        for rule in policy.rules
    )
    failures = tuple(row.rule_id for row in decisions if not row.passes)
    return ProductionFrameQualityDecisionV2(
        camera_id=statistics.camera_id,
        policy_sha256=production_frame_quality_policy_v2_sha256(policy),
        statistics_sha256=production_frame_layer_statistics_sha256(statistics),
        rule_decisions=decisions,
        passes=not failures,
        failed_rule_ids=failures,
    )


class ProductionFrameQualityReportV2(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.production-frame-quality-report.v2"
    ] = REPORT_SCHEMA
    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    statistics: tuple[ProductionFrameLayerStatistics, ...] = Field(
        min_length=1,
        max_length=180,
    )
    decisions: tuple[ProductionFrameQualityDecisionV2, ...] = Field(
        min_length=1,
        max_length=180,
    )
    rejected_camera_ids: tuple[str, ...]
    synthetic: Literal[True] = True
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )


def canonical_production_frame_quality_report_v2_bytes(
    report: ProductionFrameQualityReportV2,
) -> bytes:
    return _canonical(report.model_dump(mode="json"))


def build_production_frame_quality_report_v2(
    request: ProductionFrameQualityRequestV2,
    *,
    statistics: tuple[ProductionFrameLayerStatistics, ...],
) -> ProductionFrameQualityReportV2:
    instance_ids = {row.instance_id for row in request.object_registry}
    if tuple(row.camera_id for row in statistics) != request.selected_camera_ids:
        raise ProductionFrameQualityError(
            "statistics camera set does not match bound frames",
        )
    if any(
        instance_id is not None and instance_id not in instance_ids
        for row in statistics
        for instance_id in (
            row.dominant_near_instance_id,
            row.dominant_upper_instance_id,
        )
    ):
        raise ProductionFrameQualityError(
            "statistics reference an instance absent from the bound registry",
        )
    decisions = tuple(
        evaluate_production_frame_quality_v2(row, policy=request.policy)
        for row in statistics
    )
    return ProductionFrameQualityReportV2(
        request_id=request.request_id,
        request_sha256=_sha256(
            canonical_production_frame_quality_request_v2_bytes(request),
        ),
        statistics=statistics,
        decisions=decisions,
        rejected_camera_ids=tuple(
            row.camera_id for row in decisions if not row.passes
        ),
    )


def verify_production_frame_quality_report_v2(
    report: ProductionFrameQualityReportV2,
    *,
    request: ProductionFrameQualityRequestV2,
) -> None:
    expected = build_production_frame_quality_report_v2(
        request,
        statistics=report.statistics,
    )
    if report != expected:
        raise ProductionFrameQualityError(
            "quality report does not match bound evidence and host recomputation",
        )
