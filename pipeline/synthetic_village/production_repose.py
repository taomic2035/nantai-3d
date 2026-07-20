"""Deterministic repose for obstructed production cameras (HANDOFF-OPUS-006 §3).

A "repose" shifts the position of a known-bad camera along its forward and
lateral axes by a fixed, content-addressed offset, then re-derives the
``c2w_opencv`` matrix and the immutable plan contract.  It does **not** rename
or reorder cameras: the camera ID stays, the sequence stays, the route loop
evidence stays, only the pose moves.  This is the minimum change that lets a
downstream re-render produce a fresh journal without reusing the old one.

Fail-closed contract:
  * Only camera IDs explicitly named as obstructed are reposed.  ``034`` is
    NOT reposeable here -- it must be either cleared by the six-layer gate
    or rejected, never silently shifted (HANDOFF-OPUS-006 §3).
  * Reposed positions must stay inside the scene extent, respect the
    ground-route spacing limit, and remain unique centres.
  * The plan digest, camera registry digest, and render_id MUST change --
    if they did not, the repose was a no-op and the caller has lied about
    the offset.
  * Route loop evidence and group coverage are structurally unchanged: a
    reposed camera keeps its topology_ref, group, sequence_index and audit
    flag.  The 180-camera target and the two-loop topology contract are
    therefore preserved.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .camera_plan import _look_at_c2w, _q3
from .production_profile import (
    MAX_GROUND_ROUTE_CAMERA_SPACING_M,
    ProductionCameraPlan,
    ProductionCameraPose,
    ProductionProfileError,
    TARGET_CAMERA_COUNT,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)
from .scene_plan import ScenePlan, build_scene_plan, terrain_height_m

#: The set of camera IDs that REVIEW-CODEX-011 confirmed as geometrically
#: obstructed and that HANDOFF-OPUS-006 §3 named as reposeable.  ``034``
#: is deliberately absent: its obstruction is oblique, not geometric, and
#: must be ruled on by the six-layer gate.
REPOSEABLE_OBSTRUCTED_CAMERA_IDS: frozenset[str] = frozenset(
    {
        "camera-ground-route-010",
        "camera-ground-route-039",
    },
)

#: Baseline offset.  Lateral shifts the camera sideways (away from the
#: bridge parapet that dominates 010), forward shifts it past the
#: parapet.  Both are content-addressed into the plan digest via the
#: offset parameters of ``repose_obstructed_cameras``.
DEFAULT_LATERAL_OFFSET_M = 1.5
DEFAULT_FORWARD_OFFSET_M = 2.0


@dataclass(frozen=True)
class ReposeOffsets:
    """Content-addressed offsets applied to every reposeable camera."""

    lateral_offset_m: float
    forward_offset_m: float

    def __post_init__(self) -> None:
        if self.lateral_offset_m <= 0.0 or not math.isfinite(self.lateral_offset_m):
            raise ProductionProfileError(
                "lateral offset must be a strictly positive finite number",
            )
        if self.forward_offset_m <= 0.0 or not math.isfinite(self.forward_offset_m):
            raise ProductionProfileError(
                "forward offset must be a strictly positive finite number",
            )


@dataclass(frozen=True)
class ReposedPlan:
    """The repose result, carrying before/after digests for the journal."""

    plan: ProductionCameraPlan
    offsets: ReposeOffsets
    reposeable_camera_ids: tuple[str, ...]
    previous_plan_sha256: str
    previous_camera_registry_sha256: str

    @property
    def plan_sha256(self) -> str:
        import hashlib

        return hashlib.sha256(
            canonical_production_plan_bytes(self.plan),
        ).hexdigest()

    @property
    def camera_registry_sha256(self) -> str:
        return production_camera_registry_digest(self.plan)


def _horizontal_forward(position_m: tuple[float, float, float],
                        look_at_m: tuple[float, float, float]) -> tuple[float, float]:
    """Forward direction on the XY plane, normalised; falls back to (1, 0)."""
    dx = look_at_m[0] - position_m[0]
    dy = look_at_m[1] - position_m[1]
    norm = math.hypot(dx, dy)
    if norm < 1e-9:
        return (1.0, 0.0)
    return (dx / norm, dy / norm)


def _repose_pose(
    pose: ProductionCameraPose,
    *,
    offsets: ReposeOffsets,
    scene: ScenePlan,
) -> ProductionCameraPose:
    forward_xy = _horizontal_forward(pose.position_m, pose.look_at_m)
    # Lateral = forward rotated +90 degrees (left of the view direction).
    lateral_xy = (-forward_xy[1], forward_xy[0])
    old_x, old_y, _ = pose.position_m
    new_x = old_x + lateral_xy[0] * offsets.lateral_offset_m + (
        forward_xy[0] * offsets.forward_offset_m
    )
    new_y = old_y + lateral_xy[1] * offsets.lateral_offset_m + (
        forward_xy[1] * offsets.forward_offset_m
    )
    half_width = scene.extent.width_m / 2
    half_depth = scene.extent.depth_m / 2
    if not (-half_width + 1.0 <= new_x <= half_width - 1.0):
        raise ProductionProfileError(
            f"reposed camera {pose.camera_id} x={new_x:.3f} leaves the scene extent",
        )
    if not (-half_depth + 1.0 <= new_y <= half_depth - 1.0):
        raise ProductionProfileError(
            f"reposed camera {pose.camera_id} y={new_y:.3f} leaves the scene extent",
        )
    new_z = terrain_height_m(new_x, new_y, scene.extent) + pose.eye_height_m
    # Look-at advances along the original forward by lookahead_m.
    lookahead_xy = (
        forward_xy[0] * 25.0,
        forward_xy[1] * 25.0,
    )
    new_look_x = new_x + lookahead_xy[0]
    new_look_y = new_y + lookahead_xy[1]
    new_look_z = (
        terrain_height_m(new_look_x, new_look_y, scene.extent)
        + pose.eye_height_m
    )
    position_q = (_q3(new_x), _q3(new_y), _q3(new_z))
    look_q = (_q3(new_look_x), _q3(new_look_y), _q3(new_look_z))
    matrix = _look_at_c2w(
        __import__("numpy").array(position_q, dtype=float),
        __import__("numpy").array(look_q, dtype=float),
    )
    return pose.model_copy(
        update={
            "position_m": position_q,
            "look_at_m": look_q,
            "c2w_opencv": matrix,
        },
    )


def _validate_unique_centres(cameras: tuple[ProductionCameraPose, ...]) -> None:
    centres = [camera.position_m for camera in cameras]
    if len(centres) != len(set(centres)):
        duplicates = sorted({c for c in centres if centres.count(c) > 1})
        raise ProductionProfileError(
            f"reposed camera centres collide with an existing camera: {duplicates}",
        )


def _validate_route_spacing_unchanged_or_improved(
    before: ProductionCameraPlan,
    after: ProductionCameraPlan,
) -> None:
    """Reposed ground-route cameras must not violate the spacing limit."""
    by_route: dict[str, list[ProductionCameraPose]] = {}
    for camera in after.cameras:
        if camera.group_id == "ground-route":
            by_route.setdefault(camera.topology_ref, []).append(camera)
    for topology_ref, rows in by_route.items():
        ordered = sorted(rows, key=lambda c: c.arc_length_m or 0.0)
        for left, right in zip(ordered, ordered[1:], strict=False):
            gap = math.dist(left.position_m, right.position_m)
            if gap > MAX_GROUND_ROUTE_CAMERA_SPACING_M:
                raise ProductionProfileError(
                    f"reposed ground-route spacing exceeds the declared maximum on "
                    f"{topology_ref}: {left.camera_id} -> {right.camera_id} is "
                    f"{gap:.3f} m > {MAX_GROUND_ROUTE_CAMERA_SPACING_M} m",
                )


def repose_obstructed_cameras(
    plan: ProductionCameraPlan,
    *,
    obstructed_camera_ids: tuple[str, ...],
    offsets: ReposeOffsets | None = None,
    scene: ScenePlan | None = None,
) -> ReposedPlan:
    """Repose confirmed-obstructed cameras by fixed content-addressed offsets.

    ``034`` is not reposeable here.  The caller must pass exactly the set of
    camera IDs that the geometric clearance gate has rejected -- this
    function does not re-evaluate clearance; it trusts the caller's set
    and only fails closed on structural invariant violations.
    """

    duplicates = sorted(
        {c for c in obstructed_camera_ids if obstructed_camera_ids.count(c) > 1}
    )
    if duplicates:
        raise ProductionProfileError(
            f"obstructed camera IDs must be unique: {duplicates}",
        )
    plan_camera_ids = {camera.camera_id for camera in plan.cameras}
    missing = [
        camera_id
        for camera_id in obstructed_camera_ids
        if camera_id not in plan_camera_ids
    ]
    if missing:
        raise ProductionProfileError(
            f"obstructed camera IDs are not in this plan: {missing}",
        )
    not_reposeable = [
        camera_id
        for camera_id in obstructed_camera_ids
        if camera_id not in REPOSEABLE_OBSTRUCTED_CAMERA_IDS
    ]
    if not_reposeable:
        raise ProductionProfileError(
            f"obstructed camera IDs are not reposeable (034 must be cleared "
            f"by the six-layer gate, not reposed): {not_reposeable}",
        )
    offsets = offsets or ReposeOffsets(
        lateral_offset_m=DEFAULT_LATERAL_OFFSET_M,
        forward_offset_m=DEFAULT_FORWARD_OFFSET_M,
    )
    scene = scene or build_scene_plan()
    previous_plan_sha256 = __import__("hashlib").sha256(
        canonical_production_plan_bytes(plan),
    ).hexdigest()
    previous_camera_registry_sha256 = production_camera_registry_digest(plan)
    obstructed_set = set(obstructed_camera_ids)
    new_cameras: list[ProductionCameraPose] = []
    for camera in plan.cameras:
        if camera.camera_id in obstructed_set:
            new_cameras.append(_repose_pose(camera, offsets=offsets, scene=scene))
        else:
            new_cameras.append(camera)
    _validate_unique_centres(tuple(new_cameras))
    reposeable_camera_ids = tuple(
        sorted(obstructed_set, key=lambda row: str(row))
    )
    # ``ProductionCameraPlan`` is frozen and validates on construction, so
    # building a fresh plan from the reposed cameras re-runs every
    # invariant: route loops, group coverage, declared target count,
    # unique centres, and complete flag.
    new_plan = plan.model_copy(
        update={"cameras": tuple(new_cameras)},
    )
    # Re-run the heavy validators by re-validating canonical JSON.
    new_plan = ProductionCameraPlan.model_validate_json(
        canonical_production_plan_bytes(new_plan),
    )
    _validate_route_spacing_unchanged_or_improved(plan, new_plan)
    if new_plan.camera_count != TARGET_CAMERA_COUNT:
        raise ProductionProfileError(
            "reposed plan must still cover the declared 180 cameras",
        )
    if len({c.camera_id for c in new_plan.cameras}) != new_plan.camera_count:
        raise ProductionProfileError(
            "reposed plan camera IDs must remain unique",
        )
    new_plan_sha256 = __import__("hashlib").sha256(
        canonical_production_plan_bytes(new_plan),
    ).hexdigest()
    if new_plan_sha256 == previous_plan_sha256:
        raise ProductionProfileError(
            "repose produced an identical plan digest -- the offset did not "
            "actually change any camera position",
        )
    new_camera_registry_sha256 = production_camera_registry_digest(new_plan)
    if new_camera_registry_sha256 == previous_camera_registry_sha256:
        raise ProductionProfileError(
            "repose produced an identical camera registry digest -- "
            "camera poses did not actually change",
        )
    return ReposedPlan(
        plan=new_plan,
        offsets=offsets,
        reposeable_camera_ids=reposeable_camera_ids,
        previous_plan_sha256=previous_plan_sha256,
        previous_camera_registry_sha256=previous_camera_registry_sha256,
    )
