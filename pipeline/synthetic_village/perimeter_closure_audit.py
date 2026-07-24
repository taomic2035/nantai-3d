"""Fail-closed sixteen-camera audit plan for the exact-266 closure scene.

The audit is still synthetic, L0 and modeled-unverified.  Camera materialization
and visibility targets are deterministic modeling evidence only; they never
promote the design references to calibrated multiview input.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import canary
from .perimeter_closure_module import (
    PERIMETER_CLOSURE_MODULE_ORDER,
    ModuleId,
    PerimeterClosurePlan,
    SectorId,
    perimeter_closure_plan_sha256,
)
from .production_preflight import (
    ProductionCameraClearanceDecision,
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    evaluate_production_camera_clearance,
    production_clearance_policy_sha256,
)
from .production_profile import ProductionCameraPose, _look_at_c2w, _pose
from .production_quality_gates import (
    ProductionFrameQualityPolicyV2,
    production_frame_quality_policy_v2_sha256,
)
from .production_render import (
    LocalProductionQualityPolicy,
    local_production_quality_policy_sha256,
)

PERIMETER_CLOSURE_AUDIT_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-audit-plan.v1"
)
PERIMETER_CLOSURE_AUDIT_PLAN_ID = (
    "synthetic-village-perimeter-closure-audit-v1"
)
PERIMETER_CLOSURE_AUDIT_EYE_HEIGHT_M = 1.6
PERIMETER_CLOSURE_AUDIT_FOV_X_DEG = 62.0
PERIMETER_CLOSURE_AUDIT_DISCLOSURE = (
    "audit-only-modeled-scene-perimeter-closure"
)
PERIMETER_CLOSURE_CLEARANCE_REQUEST_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-clearance-request.v1"
)
PERIMETER_CLOSURE_CLEARANCE_REPORT_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-clearance-report.v1"
)
PERIMETER_CLOSURE_RENDER_REQUEST_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-render-frame-request.v1"
)
PERIMETER_CLOSURE_BUILD_ADAPTER = (
    "windows-perimeter-closure-audit-v1"
)
PERIMETER_CLOSURE_AUDIT_PROFILE_ID = (
    "synthetic-village-perimeter-closure-audit-v1"
)
_RENDER_CAPABILITY = {
    "schema_version": (
        "nantai.synthetic-village.perimeter-closure-render-capability.v1"
    ),
    "renderer_id": "blender-4.5.11-six-layer-exact266-v1",
    "instance_id_min": 1,
    "instance_id_max": 266,
    "clearance_probe": "fixed-5x5-first-hit-distance-v1",
    "artifacts": [
        "rgb",
        "depth",
        "normal",
        "instance-mask",
        "semantic-mask",
        "camera-metadata",
    ],
    "target_visibility": "uint16-instance-mask-positive-pixels-v1",
    "seam_visibility": "uint16-instance-mask-positive-pixels-v1",
    "trust_effect": "none-quality-filter-only",
}
PERIMETER_CLOSURE_AUDIT_CAMERA_ORDER = tuple(
    f"audit-{module_id}-{direction}"
    for module_id in PERIMETER_CLOSURE_MODULE_ORDER
    for direction in ("inward", "outward")
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Direction = Literal["inward", "outward"]
Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


class PerimeterClosureAuditError(ValueError):
    """The closure audit plan or one of its identities is not trustworthy."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _q3(value: float) -> float:
    return round(float(value), 3)


def _sample_terrain(
    terrain_height_at: Callable[[float, float], float],
    x_m: float,
    y_m: float,
) -> float:
    try:
        value = float(terrain_height_at(float(x_m), float(y_m)))
    except (TypeError, ValueError, OverflowError) as exc:
        raise PerimeterClosureAuditError(
            "audit terrain sample cannot be evaluated"
        ) from exc
    if not math.isfinite(value):
        raise PerimeterClosureAuditError(
            "audit terrain sample must be finite"
        )
    return _q3(value)


class PerimeterClosureAuditCamera(ProductionCameraPose):
    """One route-endpoint camera plus the exact overlay instances it audits."""

    audit_camera_id: str = Field(
        pattern=(
            r"^audit-closure-(?:upstream|northeast|east|southeast|downstream"
            r"|southwest|west|northwest)-(?:inward|outward)$"
        ),
    )
    module_id: ModuleId
    sector: SectorId
    direction: Direction
    source_plan_sha256: Sha256
    position_terrain_z_m: float = Field(allow_inf_nan=False)
    look_at_terrain_z_m: float = Field(allow_inf_nan=False)
    required_target_instance_ids: tuple[int, ...] = Field(
        min_length=6,
        max_length=6,
    )
    required_seam_instance_ids: tuple[int, int]

    group_id: Literal["audit-overview"] = "audit-overview"
    arc_length_m: Literal[None] = None
    eye_height_m: Literal[1.6] = PERIMETER_CLOSURE_AUDIT_EYE_HEIGHT_M
    fov_x_deg: Literal[62.0] = PERIMETER_CLOSURE_AUDIT_FOV_X_DEG
    audit_only: Literal[True] = True
    disclosure: Literal[
        "audit-only-modeled-scene-perimeter-closure"
    ] = PERIMETER_CLOSURE_AUDIT_DISCLOSURE

    @model_validator(mode="after")
    def _camera_is_measured_and_rigid(self) -> PerimeterClosureAuditCamera:
        expected_position_z = _q3(
            self.position_terrain_z_m + self.eye_height_m
        )
        expected_look_z = _q3(
            self.look_at_terrain_z_m + self.eye_height_m
        )
        if (
            self.position_m[2] != expected_position_z
            or self.look_at_m[2] != expected_look_z
        ):
            raise ValueError(
                "camera eye height disagrees with independent terrain samples"
            )
        if self.position_m == self.look_at_m:
            raise ValueError("camera position and look anchor must differ")
        if math.dist(self.position_m, self.look_at_m) < 1.0:
            raise ValueError("camera anchor pair must span at least one metre")
        expected_matrix = _look_at_c2w(
            np.asarray(self.position_m, dtype=float),
            np.asarray(self.look_at_m, dtype=float),
        )
        if not np.allclose(
            np.asarray(self.c2w_opencv, dtype=float),
            np.asarray(expected_matrix, dtype=float),
            atol=1e-9,
            rtol=0.0,
        ):
            raise ValueError("camera matrix disagrees with position/look anchors")
        return self


class PerimeterClosureAuditPlan(FrozenModel):
    """The exact sixteen-camera route/seam audit bound to one exact-266 build."""

    schema_version: Literal[
        "nantai.synthetic-village.perimeter-closure-audit-plan.v1"
    ] = PERIMETER_CLOSURE_AUDIT_SCHEMA
    plan_id: Literal[
        "synthetic-village-perimeter-closure-audit-v1"
    ] = PERIMETER_CLOSURE_AUDIT_PLAN_ID
    exact_build_id: Sha256
    exact_build_report_sha256: Sha256
    exact_blend_sha256: Sha256
    object_registry_sha256: Sha256
    perimeter_closure_plan_sha256: Sha256
    perimeter_closure_plan: PerimeterClosurePlan
    camera_count: Literal[16] = 16
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    metric_alignment: Literal[False] = False
    real_photo_textures: Literal[False] = False
    training_use: Literal["forbidden-as-multiview"] = (
        "forbidden-as-multiview"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )
    cameras: tuple[PerimeterClosureAuditCamera, ...] = Field(
        min_length=16,
        max_length=16,
    )

    @model_validator(mode="after")
    def _plan_is_exact(self) -> PerimeterClosureAuditPlan:
        closure_sha = perimeter_closure_plan_sha256(
            self.perimeter_closure_plan
        )
        if self.perimeter_closure_plan_sha256 != closure_sha:
            raise ValueError("perimeter closure source plan SHA is invalid")
        if tuple(camera.audit_camera_id for camera in self.cameras) != (
            PERIMETER_CLOSURE_AUDIT_CAMERA_ORDER
        ):
            raise ValueError("audit camera order is not the canonical sixteen")
        if tuple(camera.camera_id for camera in self.cameras) != tuple(
            f"camera-audit-overview-{index:03d}"
            for index in range(1, 17)
        ):
            raise ValueError("materialized camera IDs are not exact")
        if tuple(camera.sequence_index for camera in self.cameras) != tuple(
            range(1, 17)
        ):
            raise ValueError("materialized camera sequence is not exact")

        seam_ids = tuple(
            module.parts[4].instance_id
            for module in self.perimeter_closure_plan.modules
        )
        for module_index, module in enumerate(
            self.perimeter_closure_plan.modules
        ):
            pair = self.cameras[module_index * 2 : module_index * 2 + 2]
            expected_targets = tuple(
                part.instance_id for part in module.parts
            )
            expected_seams = (
                seam_ids[module_index],
                seam_ids[(module_index + 1) % len(seam_ids)],
            )
            for direction_index, camera in enumerate(pair):
                direction = ("inward", "outward")[direction_index]
                expected_position_anchor = (
                    module.outer_anchor_m
                    if direction == "inward"
                    else module.inner_anchor_m
                )
                expected_look_anchor = (
                    module.inner_anchor_m
                    if direction == "inward"
                    else module.outer_anchor_m
                )
                if (
                    camera.module_id != module.module_id
                    or camera.sector != module.sector
                    or camera.direction != direction
                    or camera.audit_camera_id
                    != f"audit-{module.module_id}-{direction}"
                    or camera.source_plan_sha256 != closure_sha
                    or camera.topology_ref
                    != f"batch24-perimeter-closure-{module.sector}"
                    or camera.position_m[:2]
                    != expected_position_anchor[:2]
                    or camera.look_at_m[:2] != expected_look_anchor[:2]
                    or camera.required_target_instance_ids
                    != expected_targets
                    or camera.required_seam_instance_ids != expected_seams
                ):
                    raise ValueError(
                        "audit camera disagrees with its module anchor contract"
                    )
            inward, outward = pair
            if (
                inward.position_m == outward.position_m
                or inward.position_m != outward.look_at_m
                or inward.look_at_m != outward.position_m
            ):
                raise ValueError(
                    "audit camera pair must reverse distinct route anchors"
                )
        return self


def build_perimeter_closure_audit_plan(
    *,
    perimeter_closure_plan: PerimeterClosurePlan,
    exact_build_id: str,
    exact_build_report_sha256: str,
    exact_blend_sha256: str,
    object_registry_sha256: str,
    terrain_height_at: Callable[[float, float], float],
) -> PerimeterClosureAuditPlan:
    """Build two terrain-bound reciprocal cameras for every closure sector."""

    source_sha = perimeter_closure_plan_sha256(perimeter_closure_plan)
    seam_ids = tuple(
        module.parts[4].instance_id
        for module in perimeter_closure_plan.modules
    )
    cameras: list[PerimeterClosureAuditCamera] = []
    for module_index, module in enumerate(perimeter_closure_plan.modules):
        inner_ground = _sample_terrain(
            terrain_height_at,
            module.inner_anchor_m[0],
            module.inner_anchor_m[1],
        )
        outer_ground = _sample_terrain(
            terrain_height_at,
            module.outer_anchor_m[0],
            module.outer_anchor_m[1],
        )
        inner_eye = (
            module.inner_anchor_m[0],
            module.inner_anchor_m[1],
            _q3(inner_ground + PERIMETER_CLOSURE_AUDIT_EYE_HEIGHT_M),
        )
        outer_eye = (
            module.outer_anchor_m[0],
            module.outer_anchor_m[1],
            _q3(outer_ground + PERIMETER_CLOSURE_AUDIT_EYE_HEIGHT_M),
        )
        targets = tuple(part.instance_id for part in module.parts)
        seams = (
            seam_ids[module_index],
            seam_ids[(module_index + 1) % len(seam_ids)],
        )
        for direction_index, direction in enumerate(
            ("inward", "outward")
        ):
            sequence_index = module_index * 2 + direction_index + 1
            position = outer_eye if direction == "inward" else inner_eye
            look_at = inner_eye if direction == "inward" else outer_eye
            position_ground = (
                outer_ground if direction == "inward" else inner_ground
            )
            look_ground = (
                inner_ground if direction == "inward" else outer_ground
            )
            pose = _pose(
                camera_id=f"camera-audit-overview-{sequence_index:03d}",
                group_id="audit-overview",
                sequence_index=sequence_index,
                topology_ref=(
                    f"batch24-perimeter-closure-{module.sector}"
                ),
                arc_length_m=None,
                position=position,
                look_at=look_at,
                eye_height_m=PERIMETER_CLOSURE_AUDIT_EYE_HEIGHT_M,
                fov_x_deg=PERIMETER_CLOSURE_AUDIT_FOV_X_DEG,
                disclosure=PERIMETER_CLOSURE_AUDIT_DISCLOSURE,
            )
            cameras.append(
                PerimeterClosureAuditCamera(
                    **pose.model_dump(mode="python"),
                    audit_camera_id=f"audit-{module.module_id}-{direction}",
                    module_id=module.module_id,
                    sector=module.sector,
                    direction=direction,
                    source_plan_sha256=source_sha,
                    position_terrain_z_m=position_ground,
                    look_at_terrain_z_m=look_ground,
                    required_target_instance_ids=targets,
                    required_seam_instance_ids=seams,
                )
            )
    try:
        return PerimeterClosureAuditPlan(
            exact_build_id=exact_build_id,
            exact_build_report_sha256=exact_build_report_sha256,
            exact_blend_sha256=exact_blend_sha256,
            object_registry_sha256=object_registry_sha256,
            perimeter_closure_plan_sha256=source_sha,
            perimeter_closure_plan=perimeter_closure_plan,
            cameras=tuple(cameras),
        )
    except (ValidationError, ValueError) as exc:
        raise PerimeterClosureAuditError(
            "perimeter closure audit plan construction failed"
        ) from exc


def canonical_perimeter_closure_audit_plan_bytes(
    plan: PerimeterClosureAuditPlan,
) -> bytes:
    return _canonical(plan.model_dump(mode="json"))


def perimeter_closure_audit_plan_sha256(
    plan: PerimeterClosureAuditPlan,
) -> str:
    return hashlib.sha256(
        canonical_perimeter_closure_audit_plan_bytes(plan)
    ).hexdigest()


def perimeter_closure_object_registry_sha256(
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
) -> str:
    """Hash one ordered exact-266 registry using the repository JSON contract."""

    return hashlib.sha256(
        _canonical(
            [
                row.model_dump(mode="json")
                for row in object_registry
            ]
        )
    ).hexdigest()


def _audit_camera_registry_sha256(
    plan: PerimeterClosureAuditPlan,
) -> str:
    return hashlib.sha256(
        _canonical(
            [
                {
                    "audit_camera_id": camera.audit_camera_id,
                    "camera_id": camera.camera_id,
                    "module_id": camera.module_id,
                    "direction": camera.direction,
                    "c2w_opencv": camera.c2w_opencv,
                    "required_target_instance_ids": (
                        camera.required_target_instance_ids
                    ),
                    "required_seam_instance_ids": (
                        camera.required_seam_instance_ids
                    ),
                }
                for camera in plan.cameras
            ]
        )
    ).hexdigest()


def perimeter_closure_renderer_capability_sha256() -> str:
    """Return the frozen six-layer exact-266 renderer capability digest."""

    return hashlib.sha256(_canonical(_RENDER_CAPABILITY)).hexdigest()


class PerimeterClosureClearanceRequest(FrozenModel):
    """One all-camera fresh preflight request bound to exact-266 bytes."""

    schema_version: Literal[
        "nantai.synthetic-village.perimeter-closure-clearance-request.v1"
    ] = PERIMETER_CLOSURE_CLEARANCE_REQUEST_SCHEMA
    profile_id: Literal[
        "synthetic-village-perimeter-closure-audit-v1"
    ] = PERIMETER_CLOSURE_AUDIT_PROFILE_ID
    audit_plan: PerimeterClosureAuditPlan
    audit_plan_sha256: Sha256
    camera_registry_sha256: Sha256
    selected_camera_ids: tuple[str, ...] = Field(
        min_length=16,
        max_length=16,
    )
    build_id: Sha256
    build_report_sha256: Sha256
    blend_sha256: Sha256
    perimeter_closure_plan_sha256: Sha256
    blender_executable_sha256: Sha256
    audit_script_sha256: Sha256
    object_registry_sha256: Sha256
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=266,
        max_length=266,
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
    policy_sha256: Sha256
    preflight_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _request_is_exact(self) -> PerimeterClosureClearanceRequest:
        if self.audit_plan_sha256 != (
            perimeter_closure_audit_plan_sha256(self.audit_plan)
        ):
            raise ValueError("clearance audit plan digest is invalid")
        if self.camera_registry_sha256 != (
            _audit_camera_registry_sha256(self.audit_plan)
        ):
            raise ValueError("clearance camera registry digest is invalid")
        expected_camera_ids = tuple(
            camera.camera_id for camera in self.audit_plan.cameras
        )
        if self.selected_camera_ids != expected_camera_ids:
            raise ValueError("clearance camera set is not the exact sixteen")
        identity_pairs = (
            (self.build_id, self.audit_plan.exact_build_id),
            (
                self.build_report_sha256,
                self.audit_plan.exact_build_report_sha256,
            ),
            (self.blend_sha256, self.audit_plan.exact_blend_sha256),
            (
                self.perimeter_closure_plan_sha256,
                self.audit_plan.perimeter_closure_plan_sha256,
            ),
            (
                self.object_registry_sha256,
                self.audit_plan.object_registry_sha256,
            ),
        )
        if any(left != right for left, right in identity_pairs):
            raise ValueError("clearance exact build identity disagrees")
        if tuple(row.instance_id for row in self.object_registry) != tuple(
            range(1, 267)
        ):
            raise ValueError("clearance object registry is not exact 1..266")
        if self.object_registry_sha256 != (
            perimeter_closure_object_registry_sha256(self.object_registry)
        ):
            raise ValueError("clearance object registry digest disagrees")
        if (
            self.auxiliary_registry != canary.AUXILIARY_REGISTRY
            or self.semantic_registry
            != canary._semantic_registry()  # noqa: SLF001
        ):
            raise ValueError("clearance auxiliary or semantic registry is invalid")
        if self.policy_sha256 != production_clearance_policy_sha256(
            self.policy
        ):
            raise ValueError("clearance policy digest is invalid")
        payload = self.model_dump(mode="json", exclude={"preflight_id"})
        if self.preflight_id != hashlib.sha256(
            _canonical(payload)
        ).hexdigest():
            raise ValueError("clearance preflight ID is not canonical")
        return self


def build_perimeter_closure_clearance_request(
    *,
    plan: PerimeterClosureAuditPlan,
    blender_executable_sha256: str,
    audit_script_sha256: str,
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...],
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...],
    policy: ProductionClearancePolicy,
) -> PerimeterClosureClearanceRequest:
    """Build a canonical all-sixteen exact-266 clearance request."""

    payload: dict[str, object] = {
        "audit_plan": plan,
        "audit_plan_sha256": perimeter_closure_audit_plan_sha256(plan),
        "camera_registry_sha256": _audit_camera_registry_sha256(plan),
        "selected_camera_ids": tuple(
            camera.camera_id for camera in plan.cameras
        ),
        "build_id": plan.exact_build_id,
        "build_report_sha256": plan.exact_build_report_sha256,
        "blend_sha256": plan.exact_blend_sha256,
        "perimeter_closure_plan_sha256": (
            plan.perimeter_closure_plan_sha256
        ),
        "blender_executable_sha256": blender_executable_sha256,
        "audit_script_sha256": audit_script_sha256,
        "object_registry_sha256": (
            perimeter_closure_object_registry_sha256(object_registry)
        ),
        "object_registry": object_registry,
        "auxiliary_registry": auxiliary_registry,
        "semantic_registry": semantic_registry,
        "policy": policy,
        "policy_sha256": production_clearance_policy_sha256(policy),
    }
    unsigned = PerimeterClosureClearanceRequest.model_construct(
        preflight_id="0" * 64,
        **payload,
    )
    preflight_id = hashlib.sha256(
        _canonical(
            unsigned.model_dump(mode="json", exclude={"preflight_id"})
        )
    ).hexdigest()
    try:
        return PerimeterClosureClearanceRequest(
            preflight_id=preflight_id,
            **payload,
        )
    except (ValidationError, ValueError) as exc:
        raise PerimeterClosureAuditError(
            f"perimeter closure clearance request construction failed: {exc}"
        ) from exc


def canonical_perimeter_closure_clearance_request_bytes(
    request: PerimeterClosureClearanceRequest,
) -> bytes:
    return _canonical(request.model_dump(mode="json"))


class PerimeterClosureClearanceReport(FrozenModel):
    """Raw Blender ray measurements and policy decisions for all 16 cameras."""

    schema_version: Literal[
        "nantai.synthetic-village.perimeter-closure-clearance-report.v1"
    ] = PERIMETER_CLOSURE_CLEARANCE_REPORT_SCHEMA
    profile_id: Literal[
        "synthetic-village-perimeter-closure-audit-v1"
    ] = PERIMETER_CLOSURE_AUDIT_PROFILE_ID
    preflight_id: Sha256
    request_sha256: Sha256
    audit_plan_sha256: Sha256
    camera_registry_sha256: Sha256
    build_id: Sha256
    build_report_sha256: Sha256
    blend_sha256: Sha256
    perimeter_closure_plan_sha256: Sha256
    blender_executable_sha256: Sha256
    audit_script_sha256: Sha256
    object_registry_sha256: Sha256
    policy: ProductionClearancePolicy
    policy_sha256: Sha256
    evidence: tuple[ProductionCameraClearanceEvidence, ...] = Field(
        min_length=16,
        max_length=16,
    )
    decisions: tuple[ProductionCameraClearanceDecision, ...] = Field(
        min_length=16,
        max_length=16,
    )
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _report_shape_is_exact(self) -> PerimeterClosureClearanceReport:
        evidence_ids = tuple(row.camera_id for row in self.evidence)
        decision_ids = tuple(row.camera_id for row in self.decisions)
        if evidence_ids != decision_ids or len(set(evidence_ids)) != 16:
            raise ValueError(
                "clearance evidence and decision camera order disagrees"
            )
        if self.policy_sha256 != production_clearance_policy_sha256(
            self.policy
        ):
            raise ValueError("clearance report policy digest is invalid")
        return self


def build_perimeter_closure_clearance_report(
    *,
    request: PerimeterClosureClearanceRequest,
    evidence: tuple[ProductionCameraClearanceEvidence, ...],
) -> PerimeterClosureClearanceReport:
    expected_ids = request.selected_camera_ids
    if tuple(row.camera_id for row in evidence) != expected_ids:
        raise PerimeterClosureAuditError(
            "clearance evidence is not the exact request-ordered set"
        )
    decisions = tuple(
        evaluate_production_camera_clearance(row, policy=request.policy)
        for row in evidence
    )
    return PerimeterClosureClearanceReport(
        preflight_id=request.preflight_id,
        request_sha256=hashlib.sha256(
            canonical_perimeter_closure_clearance_request_bytes(request)
        ).hexdigest(),
        audit_plan_sha256=request.audit_plan_sha256,
        camera_registry_sha256=request.camera_registry_sha256,
        build_id=request.build_id,
        build_report_sha256=request.build_report_sha256,
        blend_sha256=request.blend_sha256,
        perimeter_closure_plan_sha256=(
            request.perimeter_closure_plan_sha256
        ),
        blender_executable_sha256=request.blender_executable_sha256,
        audit_script_sha256=request.audit_script_sha256,
        object_registry_sha256=request.object_registry_sha256,
        policy=request.policy,
        policy_sha256=request.policy_sha256,
        evidence=evidence,
        decisions=decisions,
    )


def canonical_perimeter_closure_clearance_report_bytes(
    report: PerimeterClosureClearanceReport,
) -> bytes:
    return _canonical(report.model_dump(mode="json"))


def verify_perimeter_closure_clearance_report(
    report: PerimeterClosureClearanceReport,
    *,
    request: PerimeterClosureClearanceRequest,
) -> None:
    """Recompute every policy decision and bind all request identities."""

    identity_pairs = (
        (report.preflight_id, request.preflight_id),
        (
            report.request_sha256,
            hashlib.sha256(
                canonical_perimeter_closure_clearance_request_bytes(request)
            ).hexdigest(),
        ),
        (report.audit_plan_sha256, request.audit_plan_sha256),
        (report.camera_registry_sha256, request.camera_registry_sha256),
        (report.build_id, request.build_id),
        (report.build_report_sha256, request.build_report_sha256),
        (report.blend_sha256, request.blend_sha256),
        (
            report.perimeter_closure_plan_sha256,
            request.perimeter_closure_plan_sha256,
        ),
        (
            report.blender_executable_sha256,
            request.blender_executable_sha256,
        ),
        (report.audit_script_sha256, request.audit_script_sha256),
        (report.object_registry_sha256, request.object_registry_sha256),
        (report.policy, request.policy),
        (report.policy_sha256, request.policy_sha256),
    )
    if any(left != right for left, right in identity_pairs):
        raise PerimeterClosureAuditError(
            "clearance report identity disagrees with request"
        )
    if tuple(row.camera_id for row in report.evidence) != (
        request.selected_camera_ids
    ):
        raise PerimeterClosureAuditError(
            "clearance report camera evidence disagrees"
        )
    expected_decisions = tuple(
        evaluate_production_camera_clearance(row, policy=request.policy)
        for row in report.evidence
    )
    if report.decisions != expected_decisions:
        raise PerimeterClosureAuditError(
            "clearance decision disagrees with raw camera evidence"
        )


def _opencv_c2w_to_blender(matrix: Matrix4) -> Matrix4:
    converted = np.asarray(matrix, dtype=float) @ np.diag(
        [1.0, -1.0, -1.0, 1.0]
    )
    converted[converted == 0.0] = 0.0
    return tuple(
        tuple(float(converted[row, column]) for column in range(4))
        for row in range(4)
    )


class PerimeterClosureRenderFrameRequest(FrozenModel):
    """One exact-266 six-layer frame request after a passing preflight."""

    schema_version: Literal[
        "nantai.synthetic-village.perimeter-closure-render-frame-request.v1"
    ] = PERIMETER_CLOSURE_RENDER_REQUEST_SCHEMA
    profile_id: Literal[
        "synthetic-village-perimeter-closure-audit-v1"
    ] = PERIMETER_CLOSURE_AUDIT_PROFILE_ID
    audit_plan: PerimeterClosureAuditPlan
    audit_plan_sha256: Sha256
    camera_registry_sha256: Sha256
    audit_camera_id: str
    render_id: Sha256
    build_adapter: Literal[
        "windows-perimeter-closure-audit-v1"
    ] = PERIMETER_CLOSURE_BUILD_ADAPTER
    build_id: Sha256
    build_report_sha256: Sha256
    blend_sha256: Sha256
    perimeter_closure_plan_sha256: Sha256
    blender_executable_sha256: Sha256
    audit_script_sha256: Sha256
    engine_script_sha256: Sha256
    object_registry_sha256: Sha256
    preflight_id: Sha256
    clearance_report_sha256: Sha256
    clearance_policy_sha256: Sha256
    clearance_decision: ProductionCameraClearanceDecision
    renderer_capability_sha256: Sha256
    local_quality_policy: LocalProductionQualityPolicy
    local_quality_policy_sha256: Sha256
    post_render_policy: ProductionFrameQualityPolicyV2
    post_render_policy_sha256: Sha256
    settings: canary.RenderSettings
    camera: PerimeterClosureAuditCamera
    required_target_instance_ids: tuple[int, ...] = Field(
        min_length=6,
        max_length=6,
    )
    required_seam_instance_ids: tuple[int, int]
    requested_c2w_blender: Matrix4
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=266,
        max_length=266,
    )
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...] = Field(
        min_length=3,
        max_length=3,
    )
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...] = Field(
        min_length=15,
        max_length=15,
    )
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    fidelity: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _frame_request_is_exact(
        self,
    ) -> PerimeterClosureRenderFrameRequest:
        if self.audit_plan_sha256 != (
            perimeter_closure_audit_plan_sha256(self.audit_plan)
        ):
            raise ValueError("render audit plan digest is invalid")
        if self.camera_registry_sha256 != (
            _audit_camera_registry_sha256(self.audit_plan)
        ):
            raise ValueError("render camera registry digest is invalid")
        selected = next(
            (
                row
                for row in self.audit_plan.cameras
                if row.audit_camera_id == self.audit_camera_id
            ),
            None,
        )
        if selected is None or self.camera != selected:
            raise ValueError("render camera is not in the exact audit plan")
        identity_pairs = (
            (self.build_id, self.audit_plan.exact_build_id),
            (
                self.build_report_sha256,
                self.audit_plan.exact_build_report_sha256,
            ),
            (self.blend_sha256, self.audit_plan.exact_blend_sha256),
            (
                self.perimeter_closure_plan_sha256,
                self.audit_plan.perimeter_closure_plan_sha256,
            ),
            (
                self.object_registry_sha256,
                self.audit_plan.object_registry_sha256,
            ),
        )
        if any(left != right for left, right in identity_pairs):
            raise ValueError("render exact build identity disagrees")
        if (
            self.required_target_instance_ids
            != self.camera.required_target_instance_ids
            or self.required_seam_instance_ids
            != self.camera.required_seam_instance_ids
        ):
            raise ValueError("render target or seam visibility contract drifts")
        if (
            not self.clearance_decision.passes
            or self.clearance_decision.camera_id != self.camera.camera_id
            or self.clearance_decision.policy_sha256
            != self.clearance_policy_sha256
        ):
            raise ValueError(
                "render request lacks a passing bound clearance decision"
            )
        if self.renderer_capability_sha256 != (
            perimeter_closure_renderer_capability_sha256()
        ):
            raise ValueError("render capability digest is invalid")
        if self.local_quality_policy_sha256 != (
            local_production_quality_policy_sha256(
                self.local_quality_policy
            )
        ):
            raise ValueError("local frame quality policy digest is invalid")
        if self.post_render_policy_sha256 != (
            production_frame_quality_policy_v2_sha256(
                self.post_render_policy
            )
        ):
            raise ValueError("post-render policy digest is invalid")
        if tuple(row.instance_id for row in self.object_registry) != tuple(
            range(1, 267)
        ):
            raise ValueError("render object registry is not exact 1..266")
        if self.object_registry_sha256 != (
            perimeter_closure_object_registry_sha256(self.object_registry)
        ):
            raise ValueError("render object registry digest disagrees")
        if (
            self.auxiliary_registry != canary.AUXILIARY_REGISTRY
            or self.semantic_registry
            != canary._semantic_registry()  # noqa: SLF001
        ):
            raise ValueError("render auxiliary or semantic registry is invalid")
        if not np.allclose(
            np.asarray(self.requested_c2w_blender, dtype=float),
            np.asarray(
                _opencv_c2w_to_blender(self.camera.c2w_opencv),
                dtype=float,
            ),
            atol=1e-9,
            rtol=0.0,
        ):
            raise ValueError("render Blender matrix is invalid")
        payload = self.model_dump(mode="json", exclude={"render_id"})
        if self.render_id != hashlib.sha256(_canonical(payload)).hexdigest():
            raise ValueError("render ID does not bind the exact frame request")
        return self


def build_perimeter_closure_render_frame_request(
    *,
    plan: PerimeterClosureAuditPlan,
    audit_camera_id: str,
    blender_executable_sha256: str,
    audit_script_sha256: str,
    engine_script_sha256: str,
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...],
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...],
    clearance_report: PerimeterClosureClearanceReport,
    local_quality_policy: LocalProductionQualityPolicy,
    post_render_policy: ProductionFrameQualityPolicyV2,
) -> PerimeterClosureRenderFrameRequest:
    """Build one six-layer request after re-verifying the fresh preflight."""

    clearance_request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256=blender_executable_sha256,
        audit_script_sha256=audit_script_sha256,
        object_registry=object_registry,
        auxiliary_registry=auxiliary_registry,
        semantic_registry=semantic_registry,
        policy=clearance_report.policy,
    )
    verify_perimeter_closure_clearance_report(
        clearance_report,
        request=clearance_request,
    )
    camera = next(
        (
            row
            for row in plan.cameras
            if row.audit_camera_id == audit_camera_id
        ),
        None,
    )
    if camera is None:
        raise PerimeterClosureAuditError(
            "render audit camera is not in the exact plan"
        )
    decision = next(
        (
            row
            for row in clearance_report.decisions
            if row.camera_id == camera.camera_id
        ),
        None,
    )
    if decision is None or not decision.passes:
        raise PerimeterClosureAuditError(
            "render camera did not pass fresh clearance"
        )
    payload: dict[str, object] = {
        "audit_plan": plan,
        "audit_plan_sha256": perimeter_closure_audit_plan_sha256(plan),
        "camera_registry_sha256": _audit_camera_registry_sha256(plan),
        "audit_camera_id": audit_camera_id,
        "build_id": plan.exact_build_id,
        "build_report_sha256": plan.exact_build_report_sha256,
        "blend_sha256": plan.exact_blend_sha256,
        "perimeter_closure_plan_sha256": (
            plan.perimeter_closure_plan_sha256
        ),
        "blender_executable_sha256": blender_executable_sha256,
        "audit_script_sha256": audit_script_sha256,
        "engine_script_sha256": engine_script_sha256,
        "object_registry_sha256": (
            perimeter_closure_object_registry_sha256(object_registry)
        ),
        "preflight_id": clearance_report.preflight_id,
        "clearance_report_sha256": hashlib.sha256(
            canonical_perimeter_closure_clearance_report_bytes(
                clearance_report
            )
        ).hexdigest(),
        "clearance_policy_sha256": clearance_report.policy_sha256,
        "clearance_decision": decision,
        "renderer_capability_sha256": (
            perimeter_closure_renderer_capability_sha256()
        ),
        "local_quality_policy": local_quality_policy,
        "local_quality_policy_sha256": (
            local_production_quality_policy_sha256(local_quality_policy)
        ),
        "post_render_policy": post_render_policy,
        "post_render_policy_sha256": (
            production_frame_quality_policy_v2_sha256(post_render_policy)
        ),
        "settings": canary.RenderSettings(),
        "camera": camera,
        "required_target_instance_ids": (
            camera.required_target_instance_ids
        ),
        "required_seam_instance_ids": (
            camera.required_seam_instance_ids
        ),
        "requested_c2w_blender": _opencv_c2w_to_blender(
            camera.c2w_opencv
        ),
        "object_registry": object_registry,
        "auxiliary_registry": auxiliary_registry,
        "semantic_registry": semantic_registry,
    }
    unsigned = PerimeterClosureRenderFrameRequest.model_construct(
        render_id="0" * 64,
        **payload,
    )
    render_id = hashlib.sha256(
        _canonical(unsigned.model_dump(mode="json", exclude={"render_id"}))
    ).hexdigest()
    try:
        return PerimeterClosureRenderFrameRequest(
            render_id=render_id,
            **payload,
        )
    except (ValidationError, ValueError) as exc:
        raise PerimeterClosureAuditError(
            f"perimeter closure render request construction failed: {exc}"
        ) from exc


def canonical_perimeter_closure_render_request_bytes(
    request: PerimeterClosureRenderFrameRequest,
) -> bytes:
    return _canonical(request.model_dump(mode="json"))


def verify_perimeter_closure_audit_plan(
    plan: PerimeterClosureAuditPlan,
    *,
    perimeter_closure_plan: PerimeterClosurePlan,
    exact_build_id: str | None = None,
    exact_build_report_sha256: str | None = None,
    exact_blend_sha256: str | None = None,
    object_registry_sha256: str | None = None,
) -> None:
    """Recheck the embedded plan and any caller-supplied exact identities."""

    if plan.perimeter_closure_plan != perimeter_closure_plan:
        raise PerimeterClosureAuditError(
            "perimeter closure source plan bytes disagree"
        )
    closure_sha = perimeter_closure_plan_sha256(perimeter_closure_plan)
    if plan.perimeter_closure_plan_sha256 != closure_sha:
        raise PerimeterClosureAuditError(
            "perimeter closure source plan SHA disagrees"
        )
    expected = (
        ("build", plan.exact_build_id, exact_build_id),
        (
            "report",
            plan.exact_build_report_sha256,
            exact_build_report_sha256,
        ),
        ("blend", plan.exact_blend_sha256, exact_blend_sha256),
        (
            "registry",
            plan.object_registry_sha256,
            object_registry_sha256,
        ),
    )
    for label, actual, requested in expected:
        if requested is not None and actual != requested:
            raise PerimeterClosureAuditError(
                f"perimeter closure audit {label} identity disagrees"
            )


__all__ = [
    "PERIMETER_CLOSURE_AUDIT_CAMERA_ORDER",
    "PERIMETER_CLOSURE_AUDIT_EYE_HEIGHT_M",
    "PERIMETER_CLOSURE_AUDIT_SCHEMA",
    "PERIMETER_CLOSURE_CLEARANCE_REPORT_SCHEMA",
    "PERIMETER_CLOSURE_CLEARANCE_REQUEST_SCHEMA",
    "PERIMETER_CLOSURE_RENDER_REQUEST_SCHEMA",
    "PerimeterClosureAuditCamera",
    "PerimeterClosureAuditError",
    "PerimeterClosureAuditPlan",
    "PerimeterClosureClearanceReport",
    "PerimeterClosureClearanceRequest",
    "PerimeterClosureRenderFrameRequest",
    "build_perimeter_closure_audit_plan",
    "build_perimeter_closure_clearance_report",
    "build_perimeter_closure_clearance_request",
    "build_perimeter_closure_render_frame_request",
    "canonical_perimeter_closure_audit_plan_bytes",
    "canonical_perimeter_closure_clearance_report_bytes",
    "canonical_perimeter_closure_clearance_request_bytes",
    "canonical_perimeter_closure_render_request_bytes",
    "perimeter_closure_audit_plan_sha256",
    "perimeter_closure_object_registry_sha256",
    "perimeter_closure_renderer_capability_sha256",
    "verify_perimeter_closure_audit_plan",
    "verify_perimeter_closure_clearance_report",
]
