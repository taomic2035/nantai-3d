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

from .perimeter_closure_module import (
    PERIMETER_CLOSURE_MODULE_ORDER,
    ModuleId,
    PerimeterClosurePlan,
    SectorId,
    perimeter_closure_plan_sha256,
)
from .production_profile import ProductionCameraPose, _look_at_c2w, _pose

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
PERIMETER_CLOSURE_AUDIT_CAMERA_ORDER = tuple(
    f"audit-{module_id}-{direction}"
    for module_id in PERIMETER_CLOSURE_MODULE_ORDER
    for direction in ("inward", "outward")
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Direction = Literal["inward", "outward"]


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
    "PerimeterClosureAuditCamera",
    "PerimeterClosureAuditError",
    "PerimeterClosureAuditPlan",
    "build_perimeter_closure_audit_plan",
    "canonical_perimeter_closure_audit_plan_bytes",
    "perimeter_closure_audit_plan_sha256",
    "verify_perimeter_closure_audit_plan",
]
