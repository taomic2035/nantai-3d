"""Topology-aware replacement-pose search for obstructed production cameras.

Replaces the legacy hardcoded ``{010, 039}`` whitelist + fixed world-coordinate
offset with a content-addressed deterministic arc-length search along the
camera's bound polyline topology (HANDOFF-OPUS-006 Task 5, REVIEW-CODEX-014
P0 fix).

Fail-closed contract (HANDOFF-OPUS-006 §3 / Task 5 §1):

  * The caller MUST supply a *failing* ``ProductionCameraClearanceDecision``
    whose ``camera_id`` matches the requested camera and whose
    ``policy_sha256`` matches ``candidate_policy.clearance_policy_sha256``.
    A passing decision, a wrong camera ID, or a wrong policy SHA is rejected
    -- we will not search a replacement for a camera that did not actually
    fail this clearance policy.
  * The caller MUST supply ``preflight_report_sha256`` -- the SHA of the
    ``ProductionClearanceReport`` they have already bound into their journal.
    This function does NOT open the journal; it records the SHA into every
    emitted candidate so downstream verification can re-derive the chain.
    A malformed SHA is rejected; an unbound SHA is the caller's lie to catch
    later, not this function's.
  * Each candidate is a deterministic ``(arc_length_offset, lateral_offset)``
    pair drawn from ``candidate_policy``. No random search. No per-camera
    hardcoding. No ``{010, 039}`` whitelist.
  * A candidate that passes the geometry gates (scene extent, half-width
    when required, spacing to other cameras, ground-route 30 m spacing,
    unique centres, plan rebuild) is tagged ``passes_geometry_gates=True``.
    The first such candidate becomes ``accepted_geometry_candidate``.
  * Geometry viability is NOT acceptance (Task 5 §3). Acceptance requires
    fresh Blender clearance, six-layer render, post-render policy, and
    before/after RGB comparison. This function does none of those; it only
    emits geometry-viable candidates for downstream to feed into the real
    pipeline.

The function does NOT mutate the input plan. ``accepted_geometry_candidate``
carries ``predicted_plan_sha256`` and ``predicted_camera_registry_sha256``
so the caller can verify, after building a fresh plan, that the result
matches what this search predicted.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from pydantic import ValidationError

from .camera_plan import _look_at_c2w, _q3
from .production_preflight import ProductionCameraClearanceDecision
from .production_profile import (
    MAX_GROUND_ROUTE_CAMERA_SPACING_M,
    ROUTE_LOOKAHEAD_M,
    PolylineTopologySource,
    ProductionCameraPlan,
    ProductionCameraPose,
    ProductionProfileError,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)
from .scene_plan import ScenePlan, build_scene_plan, terrain_height_m

_HEX_CHARS = frozenset("0123456789abcdef")


# --------------------------------------------------------------------------- #
# Content-addressed candidate policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReposeCandidatePolicy:
    """Content-addressed policy describing which offsets to try, in order.

    The order of ``arc_length_offsets_m`` and ``lateral_offsets_m`` defines
    the deterministic candidate order: arc_length is the outer loop,
    lateral is the inner loop.  The first candidate whose geometry gates
    pass becomes ``accepted_geometry_candidate``.

    ``clearance_policy_sha256`` MUST equal
    ``failing_decision.policy_sha256``.  This binds the candidate search to
    the exact clearance policy that rejected the camera -- a different
    policy cannot authorize a replacement for a decision it did not make.
    """

    clearance_policy_sha256: str
    arc_length_offsets_m: tuple[float, ...]
    lateral_offsets_m: tuple[float, ...]
    min_spacing_to_other_cameras_m: float
    require_within_half_width: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.clearance_policy_sha256, str)
            or len(self.clearance_policy_sha256) != 64
            or any(c not in _HEX_CHARS for c in self.clearance_policy_sha256)
        ):
            raise ProductionProfileError(
                "clearance_policy_sha256 must be a 64-hex-char SHA-256 string",
            )
        if not self.arc_length_offsets_m:
            raise ProductionProfileError(
                "arc_length_offsets_m must not be empty",
            )
        if not self.lateral_offsets_m:
            raise ProductionProfileError(
                "lateral_offsets_m must not be empty",
            )
        for value in self.arc_length_offsets_m:
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ProductionProfileError(
                    "arc_length_offsets_m must be finite numbers",
                )
        for value in self.lateral_offsets_m:
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ProductionProfileError(
                    "lateral_offsets_m must be finite numbers",
                )
        if (
            not isinstance(self.min_spacing_to_other_cameras_m, (int, float))
            or not math.isfinite(self.min_spacing_to_other_cameras_m)
            or self.min_spacing_to_other_cameras_m <= 0.0
        ):
            raise ProductionProfileError(
                "min_spacing_to_other_cameras_m must be a strictly positive finite number",
            )

    @property
    def policy_sha256(self) -> str:
        return hashlib.sha256(
            canonical_repose_candidate_policy_bytes(self),
        ).hexdigest()


def canonical_repose_candidate_policy_bytes(
    policy: ReposeCandidatePolicy,
) -> bytes:
    """Canonical JSON bytes for content addressing (deterministic, sorted)."""
    payload: dict[str, Any] = {
        "clearance_policy_sha256": policy.clearance_policy_sha256,
        "arc_length_offsets_m": list(policy.arc_length_offsets_m),
        "lateral_offsets_m": list(policy.lateral_offsets_m),
        "min_spacing_to_other_cameras_m": policy.min_spacing_to_other_cameras_m,
        "require_within_half_width": policy.require_within_half_width,
    }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# Candidate and search result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReposeCandidate:
    """One deterministic candidate position derived from the policy.

    ``predicted_plan_sha256`` and ``predicted_camera_registry_sha256`` are
    computed by rebuilding the full 180-camera plan with this candidate
    substituted.  They are ``None`` only when the plan rebuild itself
    failed (e.g. centre collision with another camera) -- in which case
    ``passes_geometry_gates`` is also ``False``.
    """

    camera_id: str
    arc_length_offset_m: float
    lateral_offset_m: float
    arc_length_m: float
    position_m: tuple[float, float, float]
    look_at_m: tuple[float, float, float]
    c2w_opencv: tuple[
        tuple[float, float, float, float],
        tuple[float, float, float, float],
        tuple[float, float, float, float],
        tuple[float, float, float, float],
    ]
    passes_geometry_gates: bool
    failure_reasons: tuple[str, ...] = ()
    predicted_plan_sha256: str | None = None
    predicted_camera_registry_sha256: str | None = None


@dataclass(frozen=True)
class ReplacementPoseSearch:
    """The full deterministic candidate sequence plus the geometry-viable pick.

    ``accepted_geometry_candidate`` is ``None`` when every candidate failed
    the geometry gates.  A non-``None`` value means the candidate may
    proceed to fresh Blender clearance + six-layer render + post-render
    policy (Task 5 §3); it does NOT mean the canonical plan may change
    yet.  The caller must run §3 and only then build a new canonical plan.
    """

    camera_id: str
    failing_decision: ProductionCameraClearanceDecision
    preflight_report_sha256: str
    candidate_policy: ReposeCandidatePolicy
    topology_ref: str
    candidates: tuple[ReposeCandidate, ...]
    accepted_geometry_candidate: ReposeCandidate | None
    previous_plan_sha256: str
    previous_camera_registry_sha256: str
    previous_pose_sha256: str

    @property
    def search_sha256(self) -> str:
        """Content-addressed SHA of the search inputs + candidate verdicts.

        Same inputs -> same SHA.  Any input change (report SHA, policy,
        camera, topology) -> different SHA.  Bind into the caller's
        journal so downstream can verify which search produced which
        candidate.
        """
        payload: dict[str, Any] = {
            "camera_id": self.camera_id,
            "failing_decision_sha256": _decision_sha256(self.failing_decision),
            "preflight_report_sha256": self.preflight_report_sha256,
            "candidate_policy_sha256": self.candidate_policy.policy_sha256,
            "topology_ref": self.topology_ref,
            "previous_plan_sha256": self.previous_plan_sha256,
            "previous_camera_registry_sha256": self.previous_camera_registry_sha256,
            "previous_pose_sha256": self.previous_pose_sha256,
            "candidates": [
                {
                    "arc_length_offset_m": c.arc_length_offset_m,
                    "lateral_offset_m": c.lateral_offset_m,
                    "passes_geometry_gates": c.passes_geometry_gates,
                    "failure_reasons": list(c.failure_reasons),
                }
                for c in self.candidates
            ],
        }
        canonical = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _decision_sha256(decision: ProductionCameraClearanceDecision) -> str:
    payload = decision.model_dump(mode="json")
    return hashlib.sha256(
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    ).hexdigest()


def _pose_sha256(pose: ProductionCameraPose) -> str:
    """Stable SHA of the pose's mutable-on-repose fields only.

    ``camera_id``/``group_id``/``sequence_index``/``topology_ref`` are
    preserved by the search, so they are not part of the pose identity
    here; only the fields a candidate actually changes enter the digest.
    """
    payload: dict[str, Any] = {
        "camera_id": pose.camera_id,
        "arc_length_m": pose.arc_length_m,
        "position_m": list(pose.position_m),
        "look_at_m": list(pose.look_at_m),
        "c2w_opencv": [list(row) for row in pose.c2w_opencv],
    }
    return hashlib.sha256(
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    ).hexdigest()


def _point_at_arc_length(
    source: PolylineTopologySource,
    arc_length: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ``(point, unit_tangent)`` at the given arc length on the polyline.

    Arc length is clamped to ``[0, total_length]`` by the caller; this
    helper assumes the input is already in range.
    """
    points = source.points
    cumulative = [0.0]
    for a, b in zip(points, points[1:], strict=False):
        cumulative.append(cumulative[-1] + math.dist(a, b))
    total = cumulative[-1]
    target = max(0.0, min(total, arc_length))
    for i in range(len(cumulative) - 1):
        if cumulative[i] <= target <= cumulative[i + 1]:
            span = cumulative[i + 1] - cumulative[i]
            t = 0.0 if span <= 0 else (target - cumulative[i]) / span
            start, end = points[i], points[i + 1]
            point = (
                start[0] + t * (end[0] - start[0]),
                start[1] + t * (end[1] - start[1]),
            )
            tangent = (end[0] - start[0], end[1] - start[1])
            norm = math.hypot(*tangent) or 1.0
            return point, (tangent[0] / norm, tangent[1] / norm)
    # Unreachable when ``PolylineTopologySource.__post_init__`` has rejected
    # degenerate and adjacent-duplicate inputs (``total > 0`` and the final
    # segment always contains ``target == total``).  Fail closed instead of
    # returning a fabricated ``(points[-1], (1.0, 0.0))`` tangent -- a silent
    # fallback here would invent orientation data the caller did not measure.
    raise ProductionProfileError(
        "_point_at_arc_length could not locate arc_length="
        f"{arc_length!r} on topology_ref={source.topology_ref!r} "
        f"(total_length={total!r}, point_count={len(points)}); "
        "this should be impossible after PolylineTopologySource validation",
    )


def _build_predicted_plan(
    plan: ProductionCameraPlan,
    camera_id: str,
    new_pose: ProductionCameraPose,
) -> tuple[str, str] | None:
    """Rebuild the 180-camera plan with one camera substituted.

    Returns ``(plan_sha256, camera_registry_sha256)`` or ``None`` if the
    plan rebuild itself fails (e.g. unique-centre violation caught by the
    frozen validator).  We do not invent a SHA -- the rebuild either
    succeeds and we hash it, or it fails and we report None.
    """
    new_cameras = tuple(
        new_pose if c.camera_id == camera_id else c for c in plan.cameras
    )
    try:
        candidate_plan = plan.model_copy(update={"cameras": new_cameras})
        # Re-validate by round-tripping canonical JSON -- this is what the
        # real plan builder does, and it forces every validator to rerun.
        candidate_plan = ProductionCameraPlan.model_validate_json(
            canonical_production_plan_bytes(candidate_plan),
        )
    except (ValidationError, ValueError, TypeError):
        return None
    plan_sha = hashlib.sha256(
        canonical_production_plan_bytes(candidate_plan),
    ).hexdigest()
    registry_sha = production_camera_registry_digest(candidate_plan)
    return plan_sha, registry_sha


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def search_replacement_pose(
    *,
    plan: ProductionCameraPlan,
    camera_id: str,
    failing_decision: ProductionCameraClearanceDecision,
    preflight_report_sha256: str,
    topology: PolylineTopologySource,
    candidate_policy: ReposeCandidatePolicy,
    scene: ScenePlan | None = None,
) -> ReplacementPoseSearch:
    """Search deterministic arc-length offsets for one obstructed camera.

    See module docstring for the full fail-closed contract.  This function
    does NOT mutate ``plan``; it returns a sequence of candidates plus the
    first geometry-viable pick.  Acceptance requires Task 5 §3 evidence
    (fresh Blender clearance + six-layer render + post-render policy +
    before/after RGB) which is the caller's responsibility.
    """
    scene = scene or build_scene_plan()

    # ----- §1: input validation / fail-closed binding ---------------------- #

    if failing_decision.passes:
        raise ProductionProfileError(
            "failing_decision.passes is True -- cannot search a replacement "
            "for a camera that already passed clearance",
        )
    if failing_decision.camera_id != camera_id:
        raise ProductionProfileError(
            f"failing_decision.camera_id={failing_decision.camera_id} "
            f"disagrees with requested camera_id={camera_id}",
        )
    if (
        failing_decision.policy_sha256
        != candidate_policy.clearance_policy_sha256
    ):
        raise ProductionProfileError(
            "failing_decision.policy_sha256 disagrees with "
            "candidate_policy.clearance_policy_sha256 -- cannot bind a "
            "candidate search to a clearance policy that did not reject "
            "the camera",
        )
    if (
        not isinstance(preflight_report_sha256, str)
        or len(preflight_report_sha256) != 64
        or any(c not in _HEX_CHARS for c in preflight_report_sha256)
    ):
        raise ProductionProfileError(
            "preflight_report_sha256 must be a 64-hex-char SHA-256 string",
        )

    # ----- locate the camera in the plan ----------------------------------- #

    original_pose: ProductionCameraPose | None = None
    for camera in plan.cameras:
        if camera.camera_id == camera_id:
            original_pose = camera
            break
    if original_pose is None:
        raise ProductionProfileError(
            f"camera_id={camera_id} is not present in this plan",
        )

    # ----- bind topology to camera ----------------------------------------- #

    if not isinstance(topology, PolylineTopologySource):
        raise ProductionProfileError(
            f"topology must be a PolylineTopologySource for now; "
            f"got {type(topology).__name__}. Elevated-pedestrian and "
            f"perimeter repose are not yet implemented.",
        )
    if topology.topology_ref != original_pose.topology_ref:
        raise ProductionProfileError(
            f"topology.topology_ref={topology.topology_ref} disagrees with "
            f"camera's topology_ref={original_pose.topology_ref}",
        )
    if original_pose.arc_length_m is None:
        raise ProductionProfileError(
            f"camera {camera_id} has arc_length_m=None; cannot search "
            f"along topology (audit-overview cameras are not reposeable)",
        )
    total_length = topology.length_m
    if not (0.0 <= original_pose.arc_length_m <= total_length):
        raise ProductionProfileError(
            f"camera {camera_id} arc_length_m={original_pose.arc_length_m} "
            f"is outside topology length [0, {total_length:.3f}]",
        )

    # ----- collect other cameras' positions for spacing checks ------------- #

    other_positions: list[tuple[float, float, float]] = [
        c.position_m for c in plan.cameras if c.camera_id != camera_id
    ]
    same_route_cameras: list[ProductionCameraPose] = []
    if original_pose.group_id == "ground-route":
        same_route_cameras = [
            c for c in plan.cameras
            if c.group_id == "ground-route"
            and c.topology_ref == original_pose.topology_ref
            and c.camera_id != camera_id
        ]

    half_width_extent = scene.extent.width_m / 2
    half_depth_extent = scene.extent.depth_m / 2

    # ----- §2: deterministic candidate search ------------------------------ #

    candidates: list[ReposeCandidate] = []
    accepted: ReposeCandidate | None = None

    for arc_offset in candidate_policy.arc_length_offsets_m:
        for lateral_offset in candidate_policy.lateral_offsets_m:
            new_arc_length = (original_pose.arc_length_m or 0.0) + arc_offset
            failure_reasons: list[str] = []

            # Gate 1: arc length must stay on the polyline.
            if new_arc_length < 0.0 or new_arc_length > total_length:
                failure_reasons.append(
                    f"arc_length={new_arc_length:.3f} outside topology "
                    f"[0, {total_length:.3f}]",
                )
                # We still emit a candidate row (with zeroed pose fields)
                # so the candidate sequence is complete and deterministic;
                # the caller can see *why* each offset was rejected.
                candidates.append(ReposeCandidate(
                    camera_id=camera_id,
                    arc_length_offset_m=arc_offset,
                    lateral_offset_m=lateral_offset,
                    arc_length_m=new_arc_length,
                    position_m=(0.0, 0.0, 0.0),
                    look_at_m=(0.0, 0.0, 0.0),
                    c2w_opencv=(
                        (1.0, 0.0, 0.0, 0.0),
                        (0.0, 1.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0, 0.0),
                        (0.0, 0.0, 0.0, 1.0),
                    ),
                    passes_geometry_gates=False,
                    failure_reasons=tuple(failure_reasons),
                ))
                continue

            point, tangent = _point_at_arc_length(topology, new_arc_length)
            # Left normal = tangent rotated +90 degrees.
            normal = (-tangent[1], tangent[0])

            # Gate 2: lateral must stay inside corridor when required.
            if (
                candidate_policy.require_within_half_width
                and abs(lateral_offset) > topology.half_width_m
            ):
                failure_reasons.append(
                    f"|lateral_offset|={abs(lateral_offset):.3f} exceeds "
                    f"half_width={topology.half_width_m:.3f}",
                )

            new_x = point[0] + normal[0] * lateral_offset
            new_y = point[1] + normal[1] * lateral_offset

            # Gate 3: scene extent (with 1 m safety margin).
            if not (-half_width_extent + 1.0 <= new_x <= half_width_extent - 1.0):
                failure_reasons.append(
                    f"x={new_x:.3f} leaves scene extent "
                    f"[-{half_width_extent - 1.0:.3f}, {half_width_extent - 1.0:.3f}]",
                )
            if not (-half_depth_extent + 1.0 <= new_y <= half_depth_extent - 1.0):
                failure_reasons.append(
                    f"y={new_y:.3f} leaves scene extent "
                    f"[-{half_depth_extent - 1.0:.3f}, {half_depth_extent - 1.0:.3f}]",
                )

            new_z = terrain_height_m(
                max(-half_width_extent, min(half_width_extent, new_x)),
                max(-half_depth_extent, min(half_depth_extent, new_y)),
                scene.extent,
            ) + original_pose.eye_height_m

            # Look-at: ahead on the same topology, offset by same lateral.
            lookahead_arc = new_arc_length + ROUTE_LOOKAHEAD_M
            if lookahead_arc > total_length:
                # Past route end: project forward using the local tangent.
                ahead_x = new_x + tangent[0] * ROUTE_LOOKAHEAD_M
                ahead_y = new_y + tangent[1] * ROUTE_LOOKAHEAD_M
            else:
                ahead_point, _ = _point_at_arc_length(topology, lookahead_arc)
                ahead_x = ahead_point[0] + normal[0] * lateral_offset
                ahead_y = ahead_point[1] + normal[1] * lateral_offset
            ahead_x = max(-half_width_extent + 1.0, min(half_width_extent - 1.0, ahead_x))
            ahead_y = max(-half_depth_extent + 1.0, min(half_depth_extent - 1.0, ahead_y))
            ahead_z = terrain_height_m(ahead_x, ahead_y, scene.extent) + original_pose.eye_height_m

            position_q = (_q3(new_x), _q3(new_y), _q3(new_z))
            look_q = (_q3(ahead_x), _q3(ahead_y), _q3(ahead_z))
            matrix = _look_at_c2w(
                np.array(position_q, dtype=float),
                np.array(look_q, dtype=float),
            )

            # Gate 4: unique centre (no collision with existing cameras).
            if position_q in other_positions:
                failure_reasons.append(
                    "reposed centre collides with an existing camera",
                )

            # Gate 5: minimum 3D spacing to every OTHER camera.
            if other_positions:
                min_spacing = min(
                    math.dist(position_q, other) for other in other_positions
                )
                if min_spacing < candidate_policy.min_spacing_to_other_cameras_m:
                    failure_reasons.append(
                        f"min spacing to other cameras={min_spacing:.3f} < "
                        f"{candidate_policy.min_spacing_to_other_cameras_m:.3f}",
                    )

            # Gate 6: ground-route 30 m spacing on the same route.
            if same_route_cameras:
                sorted_arc = sorted(
                    [(c.arc_length_m or 0.0, c.position_m) for c in same_route_cameras]
                    + [(new_arc_length, position_q)]
                )
                for left, right in zip(sorted_arc, sorted_arc[1:], strict=False):
                    gap = math.dist(left[1], right[1])
                    if gap > MAX_GROUND_ROUTE_CAMERA_SPACING_M:
                        failure_reasons.append(
                            f"ground-route spacing violation: {gap:.3f}m > "
                            f"{MAX_GROUND_ROUTE_CAMERA_SPACING_M:.3f}m",
                        )
                        break

            passes = not failure_reasons

            # Predicted plan SHA: only meaningful if geometry gates pass.
            predicted_plan_sha: str | None = None
            predicted_registry_sha: str | None = None
            if passes:
                new_pose = original_pose.model_copy(
                    update={
                        "position_m": position_q,
                        "look_at_m": look_q,
                        "arc_length_m": _q3(new_arc_length),
                        "c2w_opencv": matrix,
                    },
                )
                predicted = _build_predicted_plan(plan, camera_id, new_pose)
                if predicted is None:
                    # Plan rebuild failed -- this is itself a geometry
                    # failure (e.g. a validator the gates above missed).
                    failure_reasons.append(
                        "predicted plan rebuild failed (validator rejected)",
                    )
                    passes = False
                else:
                    predicted_plan_sha, predicted_registry_sha = predicted

            candidate = ReposeCandidate(
                camera_id=camera_id,
                arc_length_offset_m=arc_offset,
                lateral_offset_m=lateral_offset,
                arc_length_m=_q3(new_arc_length),
                position_m=position_q,
                look_at_m=look_q,
                c2w_opencv=matrix,
                passes_geometry_gates=passes,
                failure_reasons=tuple(failure_reasons),
                predicted_plan_sha256=predicted_plan_sha,
                predicted_camera_registry_sha256=predicted_registry_sha,
            )
            candidates.append(candidate)
            if passes and accepted is None:
                accepted = candidate

    return ReplacementPoseSearch(
        camera_id=camera_id,
        failing_decision=failing_decision,
        preflight_report_sha256=preflight_report_sha256,
        candidate_policy=candidate_policy,
        topology_ref=topology.topology_ref,
        candidates=tuple(candidates),
        accepted_geometry_candidate=accepted,
        previous_plan_sha256=hashlib.sha256(
            canonical_production_plan_bytes(plan),
        ).hexdigest(),
        previous_camera_registry_sha256=production_camera_registry_digest(plan),
        previous_pose_sha256=_pose_sha256(original_pose),
    )


def build_reposed_plan(
    search: ReplacementPoseSearch,
    *,
    plan: ProductionCameraPlan,
) -> tuple[ProductionCameraPlan, str, str]:
    """Rebuild the 180-camera plan from a search's accepted candidate.

    This is the public helper for Task 5 §3 callers: given a
    ``ReplacementPoseSearch`` whose ``accepted_geometry_candidate`` is not
    None, rebuild ``plan`` with the accepted candidate's pose substituted
    in, and verify the rebuilt plan's SHA matches what the search
    predicted.  If the SHA does not match, fail closed -- the caller has
    a different plan than what the search validated, and must not proceed
    to Blender clearance / six-layer render.

    The caller MUST pass the same plan instance it passed to
    ``search_replacement_pose``.  If a different or mutated plan is
    passed, the rebuilt SHA will differ from
    ``accepted.predicted_plan_sha256`` and the function will fail closed.

    Returns ``(new_plan, new_plan_sha256, new_camera_registry_sha256)``.

    Raises ``ProductionProfileError`` if:

    * ``search.accepted_geometry_candidate`` is None (geometry search
      found no viable candidate -- caller must not call this function);
    * the rebuilt plan fails pydantic re-validation (e.g. a validator
      the geometry gates missed caught the substitution);
    * the rebuilt plan's SHA does not match
      ``accepted.predicted_plan_sha256`` (caller passed a different or
      mutated plan between calling ``search_replacement_pose`` and this
      function);
    * the rebuilt plan's camera registry SHA does not match
      ``accepted.predicted_camera_registry_sha256``.

    This function does NOT run Blender, does NOT render, and does NOT
    approve the reposed plan for canonical use.  It only rebuilds the
    plan and verifies its identity against what the search predicted.
    """
    accepted = search.accepted_geometry_candidate
    if accepted is None:
        raise ProductionProfileError(
            "cannot build a reposed plan: search.accepted_geometry_candidate "
            "is None (geometry search found no viable candidate)",
        )
    if accepted.predicted_plan_sha256 is None:
        raise ProductionProfileError(
            "cannot build a reposed plan: accepted candidate has "
            "predicted_plan_sha256=None (geometry gates passed but the "
            "plan rebuild inside search_replacement_pose failed -- this "
            "should be impossible, please report)",
        )
    if accepted.predicted_camera_registry_sha256 is None:
        raise ProductionProfileError(
            "cannot build a reposed plan: accepted candidate has "
            "predicted_camera_registry_sha256=None (same as above)",
        )

    # Verify the caller passed the same plan that produced this search.
    # The search recorded the plan's SHA as previous_plan_sha256; if the
    # caller passed a different plan, this check fails closed before we
    # even try to rebuild.
    actual_plan_sha = hashlib.sha256(
        canonical_production_plan_bytes(plan),
    ).hexdigest()
    if actual_plan_sha != search.previous_plan_sha256:
        raise ProductionProfileError(
            f"cannot build a reposed plan: the plan passed to this "
            f"function has SHA {actual_plan_sha}, but the search was "
            f"performed on a plan with SHA {search.previous_plan_sha256}. "
            f"Pass the same plan instance you passed to "
            f"search_replacement_pose.",
        )

    # Locate the original camera in the plan.
    original_pose: ProductionCameraPose | None = None
    for camera in plan.cameras:
        if camera.camera_id == search.camera_id:
            original_pose = camera
            break
    if original_pose is None:
        raise ProductionProfileError(
            f"cannot build a reposed plan: camera_id={search.camera_id} "
            f"is not present in the plan",
        )

    # Reconstruct the new pose by copying mutable-on-repose fields from
    # the candidate.  Other fields (topology_ref, group_id, sequence_index,
    # eye_height_m, fov_x_deg, intrinsics, audit_only, disclosure) are
    # preserved from the original camera.
    new_pose = original_pose.model_copy(
        update={
            "position_m": accepted.position_m,
            "look_at_m": accepted.look_at_m,
            "arc_length_m": accepted.arc_length_m,
            "c2w_opencv": accepted.c2w_opencv,
        },
    )

    new_cameras = tuple(
        new_pose if c.camera_id == search.camera_id else c
        for c in plan.cameras
    )
    try:
        candidate_plan = plan.model_copy(update={"cameras": new_cameras})
        # Re-validate by round-tripping canonical JSON -- this forces
        # every validator to rerun, exactly like _build_predicted_plan did
        # inside search_replacement_pose.
        candidate_plan = ProductionCameraPlan.model_validate_json(
            canonical_production_plan_bytes(candidate_plan),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise ProductionProfileError(
            f"cannot build a reposed plan: the rebuilt plan failed "
            f"pydantic re-validation: {exc}",
        ) from exc

    plan_sha = hashlib.sha256(
        canonical_production_plan_bytes(candidate_plan),
    ).hexdigest()
    registry_sha = production_camera_registry_digest(candidate_plan)

    if plan_sha != accepted.predicted_plan_sha256:
        raise ProductionProfileError(
            f"cannot build a reposed plan: rebuilt plan SHA {plan_sha} "
            f"does not match the search's predicted_plan_sha256 "
            f"{accepted.predicted_plan_sha256}.  The caller has either "
            f"mutated the plan between search_replacement_pose and "
            f"build_reposed_plan, or the search was constructed by hand "
            f"with a fabricated predicted_plan_sha256.",
        )
    if registry_sha != accepted.predicted_camera_registry_sha256:
        raise ProductionProfileError(
            f"cannot build a reposed plan: rebuilt camera registry SHA "
            f"{registry_sha} does not match the search's "
            f"predicted_camera_registry_sha256 "
            f"{accepted.predicted_camera_registry_sha256}.",
        )

    return candidate_plan, plan_sha, registry_sha


