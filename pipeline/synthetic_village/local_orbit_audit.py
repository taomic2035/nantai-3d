"""Content-addressed, audit-only waterwheel orbit for the exact-218 scene.

The eight cameras inspect modeled synthetic geometry.  They are not a calibrated
multiview capture and cannot raise geometry, metric, or training trust.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .production_profile import (
    FiniteVector3,
    ProductionCameraPlan,
    _pose,
    canonical_production_plan_bytes,
)
from .scene_plan import (
    ScenePlan,
    build_scene_plan,
    canonical_scene_plan_bytes,
    terrain_height_m,
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_PRIMARY_AZIMUTHS = tuple(range(0, 360, 45))
_PRIMARY_CAMERA_IDS = tuple(
    f"audit-waterwheel-az{azimuth:03d}" for azimuth in _PRIMARY_AZIMUTHS
)
_MATERIALIZED_CAMERA_IDS = tuple(
    f"camera-audit-overview-{index:03d}" for index in range(1, 9)
)
_SUPPORT_AZIMUTHS = (22.5, 112.5, 202.5, 292.5)
_MIN_TERRAIN_CLEARANCE_M = 1.8
_MAX_LOCAL_ORBIT_PLAN_BYTES = 512 * 1024


class LocalOrbitPlanError(RuntimeError):
    """Raised when an orbit plan cannot be loaded as stable canonical bytes."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LocalOrbitCamera(FrozenModel):
    orbit_camera_id: str = Field(
        pattern=r"^audit-waterwheel-az(?:000|045|090|135|180|225|270|315)$",
    )
    materialized_camera_id: str = Field(
        pattern=r"^camera-audit-overview-00[1-8]$",
    )
    azimuth_deg: int = Field(ge=0, lt=360, multiple_of=45)
    radius_m: Literal[20.0] = 20.0
    position_m: FiniteVector3
    look_at_m: FiniteVector3
    fov_x_deg: Literal[65.0] = 65.0
    audit_only: Literal[True] = True


class LocalOrbitAuditPlan(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.local-orbit-audit-plan.v1"
    ] = "nantai.synthetic-village.local-orbit-audit-plan.v1"
    source_production_plan_sha256: Sha256
    environment_module_plan_sha256: Sha256
    exact_build_id: Sha256
    exact_blend_sha256: Sha256
    anchor_m: FiniteVector3
    cameras: tuple[LocalOrbitCamera, ...] = Field(min_length=8, max_length=8)
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    training_use: Literal["forbidden-as-multiview"] = "forbidden-as-multiview"
    trust_effect: Literal["none-quality-filter-only"] = "none-quality-filter-only"

    @model_validator(mode="after")
    def _validate_orbit(self) -> LocalOrbitAuditPlan:
        if tuple(row.azimuth_deg for row in self.cameras) != _PRIMARY_AZIMUTHS:
            raise ValueError("local orbit cameras must use the exact ordered azimuth tuple")
        if tuple(row.orbit_camera_id for row in self.cameras) != _PRIMARY_CAMERA_IDS:
            raise ValueError("local orbit camera IDs must match the ordered azimuth tuple")
        if (
            tuple(row.materialized_camera_id for row in self.cameras)
            != _MATERIALIZED_CAMERA_IDS
        ):
            raise ValueError("local orbit materialized camera IDs are not exact")
        anchor_x, anchor_y, anchor_z = self.anchor_m
        expected_look = _q3((anchor_x, anchor_y, anchor_z + 0.4))
        scene = _canonical_scene()
        for row in self.cameras:
            expected_position = _orbit_position(
                anchor=self.anchor_m,
                azimuth_deg=row.azimuth_deg,
                radius_m=row.radius_m,
                scene=scene,
            )
            if row.position_m != expected_position or row.look_at_m != expected_look:
                raise ValueError("local orbit pose must be derived from anchor")
        return self


def _q3(values: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(round(float(value), 3) for value in values)


@lru_cache(maxsize=1)
def _canonical_scene() -> ScenePlan:
    return build_scene_plan()


def _orbit_position(
    *,
    anchor: tuple[float, float, float],
    azimuth_deg: float,
    radius_m: float,
    scene: ScenePlan,
) -> tuple[float, float, float]:
    angle = math.radians(azimuth_deg)
    x_m = anchor[0] + radius_m * math.cos(angle)
    y_m = anchor[1] + radius_m * math.sin(angle)
    z_m = (
        terrain_height_m(x_m, y_m, scene.extent)
        + _MIN_TERRAIN_CLEARANCE_M
    )
    return _q3((x_m, y_m, z_m))


def _exact_rotation_pose(**kwargs):
    """Keep local audit rotations orthonormal before Blender float32 storage."""

    pose = _pose(**kwargs)
    position = np.asarray(pose.position_m, dtype=float)
    target = np.asarray(pose.look_at_m, dtype=float)
    forward = target - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.asarray((0.0, 0.0, 1.0)))
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    matrix = np.eye(4, dtype=float)
    matrix[:3, 0] = right
    matrix[:3, 1] = down
    matrix[:3, 2] = forward
    matrix[:3, 3] = position
    exact = tuple(
        tuple(float(matrix[row, column]) for column in range(4))
        for row in range(4)
    )
    return pose.model_copy(update={"c2w_opencv": exact})


def canonical_local_orbit_plan_bytes(plan: LocalOrbitAuditPlan) -> bytes:
    payload = plan.model_dump(mode="json")
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


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LocalOrbitPlanError(
                f"local orbit plan contains duplicate JSON key: {key}",
            )
        result[key] = value
    return result


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int]:
    return result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns


def load_local_orbit_plan(path: Path) -> LocalOrbitAuditPlan:
    """Load only bounded, stable, canonical local-orbit plan bytes."""

    path = Path(path).absolute()
    try:
        parent = path.parent
        if (
            _is_linklike(path)
            or _is_linklike(parent)
            or parent.resolve(strict=True) != parent
        ):
            raise LocalOrbitPlanError(
                "local orbit plan path has a redirected leaf or parent",
            )
        before = path.stat()
        if before.st_size <= 0 or before.st_size > _MAX_LOCAL_ORBIT_PLAN_BYTES:
            raise LocalOrbitPlanError("local orbit plan size is invalid")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_signature(before) != _stat_signature(opened):
                raise LocalOrbitPlanError(
                    "local orbit plan changed before bounded read",
                )
            raw = stream.read(_MAX_LOCAL_ORBIT_PLAN_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
        if (
            len(raw) != before.st_size
            or len(raw) > _MAX_LOCAL_ORBIT_PLAN_BYTES
            or _stat_signature(opened) != _stat_signature(after_open)
            or _stat_signature(before) != _stat_signature(after)
        ):
            raise LocalOrbitPlanError(
                "local orbit plan changed during bounded read",
            )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        plan = LocalOrbitAuditPlan.model_validate_json(raw)
        if raw != canonical_local_orbit_plan_bytes(plan):
            raise LocalOrbitPlanError("local orbit plan must be canonical JSON")
        return plan
    except LocalOrbitPlanError:
        raise
    except (
        OSError,
        RuntimeError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        raise LocalOrbitPlanError(
            f"local orbit plan cannot be trusted: {exc}",
        ) from exc


def local_orbit_plan_sha256(plan: LocalOrbitAuditPlan) -> str:
    return hashlib.sha256(canonical_local_orbit_plan_bytes(plan)).hexdigest()


def build_waterwheel_local_orbit_plan(
    *,
    source_plan: ProductionCameraPlan,
    environment_module_plan_sha256: str,
    exact_build_id: str,
    exact_blend_sha256: str,
    anchor_m: tuple[float, float, float],
) -> LocalOrbitAuditPlan:
    """Build the fixed eight-direction audit plan bound to exact scene bytes."""

    anchor = _q3(anchor_m)
    anchor_x, anchor_y, anchor_z = anchor
    look_at = _q3((anchor_x, anchor_y, anchor_z + 0.4))
    scene = _canonical_scene()
    if hashlib.sha256(canonical_scene_plan_bytes(scene)).hexdigest() != (
        source_plan.scene_plan_sha256
    ):
        raise LocalOrbitPlanError(
            "local orbit source plan is not bound to the canonical terrain",
        )
    cameras = tuple(
        LocalOrbitCamera(
            orbit_camera_id=orbit_camera_id,
            materialized_camera_id=materialized_camera_id,
            azimuth_deg=azimuth,
            position_m=_orbit_position(
                anchor=anchor,
                azimuth_deg=azimuth,
                radius_m=20.0,
                scene=scene,
            ),
            look_at_m=look_at,
        )
        for azimuth, orbit_camera_id, materialized_camera_id in zip(
            _PRIMARY_AZIMUTHS,
            _PRIMARY_CAMERA_IDS,
            _MATERIALIZED_CAMERA_IDS,
            strict=True,
        )
    )
    return LocalOrbitAuditPlan(
        source_production_plan_sha256=hashlib.sha256(
            canonical_production_plan_bytes(source_plan),
        ).hexdigest(),
        environment_module_plan_sha256=environment_module_plan_sha256,
        exact_build_id=exact_build_id,
        exact_blend_sha256=exact_blend_sha256,
        anchor_m=anchor,
        cameras=cameras,
    )


def materialize_local_orbit_render_plan(
    source_plan: ProductionCameraPlan,
    orbit_plan: LocalOrbitAuditPlan,
) -> ProductionCameraPlan:
    """Derive a render-only plan without mutating the canonical source plan."""

    source_sha = hashlib.sha256(canonical_production_plan_bytes(source_plan)).hexdigest()
    if source_sha != orbit_plan.source_production_plan_sha256:
        raise ValueError("local orbit source production plan SHA-256 disagrees")
    source_audit = tuple(
        camera for camera in source_plan.cameras if camera.group_id == "audit-overview"
    )
    if len(source_audit) != 12:
        raise ValueError("source production plan must contain exact 12 audit cameras")

    replacements = []
    for source_camera, orbit_camera in zip(
        source_audit[:8],
        orbit_plan.cameras,
        strict=True,
    ):
        replacements.append(
            _exact_rotation_pose(
                camera_id=source_camera.camera_id,
                group_id="audit-overview",
                sequence_index=source_camera.sequence_index,
                topology_ref="batch22-waterwheel-local-orbit",
                arc_length_m=None,
                position=orbit_camera.position_m,
                look_at=orbit_camera.look_at_m,
                eye_height_m=1.6,
                fov_x_deg=orbit_camera.fov_x_deg,
                disclosure="audit-only-modeled-scene-waterwheel-local-orbit",
            ),
        )

    anchor_x, anchor_y, anchor_z = orbit_plan.anchor_m
    support_look = _q3((anchor_x, anchor_y, anchor_z + 0.4))
    for source_camera, azimuth in zip(
        source_audit[8:],
        _SUPPORT_AZIMUTHS,
        strict=True,
    ):
        angle = math.radians(azimuth)
        replacements.append(
            _exact_rotation_pose(
                camera_id=source_camera.camera_id,
                group_id="audit-overview",
                sequence_index=source_camera.sequence_index,
                topology_ref="batch22-waterwheel-local-orbit",
                arc_length_m=None,
                position=_q3(
                    (
                        anchor_x + 18.0 * math.cos(angle),
                        anchor_y + 18.0 * math.sin(angle),
                        anchor_z + 4.0,
                    ),
                ),
                look_at=support_look,
                eye_height_m=4.0,
                fov_x_deg=65.0,
                disclosure="audit-only-modeled-scene-waterwheel-support-orbit",
            ),
        )

    replacement_by_id = {camera.camera_id: camera for camera in replacements}
    payload = source_plan.model_dump(mode="json")
    payload["cameras"] = [
        replacement_by_id.get(camera.camera_id, camera).model_dump(mode="json")
        for camera in source_plan.cameras
    ]
    for row in payload["post_render_quality_expectation"]["group_expectations"]:
        if row["group_id"] == "audit-overview":
            row["expected_dominant_semantic"] = "mixed"
            row["disclosure"] = (
                "local-modeled-scene-orbit-expects-mixed-architecture-water-ground"
            )
    return ProductionCameraPlan.model_validate_json(
        json.dumps(payload, ensure_ascii=False, allow_nan=False),
    )
