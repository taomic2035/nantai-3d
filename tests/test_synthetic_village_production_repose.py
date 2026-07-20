"""TDD for topology-aware replacement-pose search (HANDOFF-OPUS-006 Task 5).

REVIEW-CODEX-014 P0 requires the legacy hardcoded
``REPOSEABLE_OBSTRUCTED_CAMERA_IDS={010, 039}`` whitelist + fixed world
offset to be replaced with a content-addressed deterministic arc-length
candidate search that consumes a *failing* clearance decision and the
camera's bound polyline topology.

This test module asserts the §1 contract (input validation + binding) and
the §2 contract (deterministic candidates with recalculated pose / arc /
matrix / spacing / plan SHA).  It does NOT assert §3 (fresh Blender
clearance + six-layer render + post-render policy + before/after RGB) --
that is the caller's downstream responsibility and is documented as such
in the function's docstring.
"""

from __future__ import annotations

import hashlib
import math

import pytest

from pipeline.synthetic_village.elevated_topology import (
    build_elevated_topology_plan,
)
from pipeline.synthetic_village.production_preflight import (
    ProductionCameraClearanceDecision,
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    ProductionClearanceRayEvidence,
    build_production_clearance_report,
    build_production_clearance_request,
    canonical_production_clearance_report_bytes,
    production_clearance_policy_sha256,
)
from pipeline.synthetic_village.production_profile import (
    PolylineTopologySource,
    ProductionProfileError,
    build_production_camera_plan,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
    resolve_topology_sources,
)
from pipeline.synthetic_village.production_repose import (
    ReposeCandidatePolicy,
    build_reposed_plan,
    canonical_repose_candidate_policy_bytes,
    search_replacement_pose,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan
from tests.test_synthetic_village_production_render import _request as _render_request

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _plan():
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    return build_production_camera_plan(scene, topology)


def _topology_for_camera(plan, camera_id) -> PolylineTopologySource:
    """Resolve the polyline source a ground-route camera is bound to."""
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    sources = resolve_topology_sources(scene, topology)
    camera = next(c for c in plan.cameras if c.camera_id == camera_id)
    ground_sources = sources["ground-route"]
    match = next(
        s for s in ground_sources if s.topology_ref == camera.topology_ref
    )
    return match


def _failing_decision(
    camera_id: str = "camera-ground-route-010",
    policy_sha256: str = "0" * 64,
) -> ProductionCameraClearanceDecision:
    return ProductionCameraClearanceDecision(
        camera_id=camera_id,
        policy_sha256=policy_sha256,
        evidence_sha256="1" * 64,
        measured_upper_middle_near_hit_count=6,
        passes=False,
        failed_rule_ids=("upper-middle-near-hit-count",),
    )


def _passing_decision(
    camera_id: str = "camera-ground-route-010",
    policy_sha256: str = "0" * 64,
) -> ProductionCameraClearanceDecision:
    return ProductionCameraClearanceDecision(
        camera_id=camera_id,
        policy_sha256=policy_sha256,
        evidence_sha256="1" * 64,
        measured_upper_middle_near_hit_count=0,
        passes=True,
        failed_rule_ids=(),
    )


def _policy(clearance_sha: str = "0" * 64) -> ReposeCandidatePolicy:
    return ReposeCandidatePolicy(
        clearance_policy_sha256=clearance_sha,
        arc_length_offsets_m=(-3.0, -2.0, 2.0, 3.0),
        lateral_offsets_m=(0.0,),
        min_spacing_to_other_cameras_m=2.5,
        require_within_half_width=True,
    )


_VALID_REPORT_SHA = "a" * 64


# --------------------------------------------------------------------------- #
# §1: Input validation -- the API must reject every form of unbound evidence.
# --------------------------------------------------------------------------- #


def test_search_rejects_passing_decision() -> None:
    """A passing decision is not evidence of obstruction."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    with pytest.raises(ProductionProfileError, match="passes is True"):
        search_replacement_pose(
            plan=plan,
            camera_id="camera-ground-route-010",
            failing_decision=_passing_decision(),
            preflight_report_sha256=_VALID_REPORT_SHA,
            topology=topology,
            candidate_policy=_policy(),
        )


def test_search_rejects_wrong_camera_id() -> None:
    """decision.camera_id must equal the requested camera_id."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    with pytest.raises(ProductionProfileError, match="disagrees with requested"):
        search_replacement_pose(
            plan=plan,
            camera_id="camera-ground-route-010",
            failing_decision=_failing_decision(camera_id="camera-ground-route-039"),
            preflight_report_sha256=_VALID_REPORT_SHA,
            topology=topology,
            candidate_policy=_policy(),
        )


def test_search_rejects_wrong_policy_sha() -> None:
    """candidate_policy.clearance_policy_sha256 must match decision.policy_sha256."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    with pytest.raises(ProductionProfileError, match="clearance_policy_sha256"):
        search_replacement_pose(
            plan=plan,
            camera_id="camera-ground-route-010",
            failing_decision=_failing_decision(policy_sha256="0" * 64),
            preflight_report_sha256=_VALID_REPORT_SHA,
            topology=topology,
            candidate_policy=_policy(clearance_sha="b" * 64),
        )


def test_search_rejects_malformed_report_sha() -> None:
    """preflight_report_sha256 must be a 64-hex-char SHA-256 string."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    for bad in ("short", "g" * 64, "A" * 64, "", "0" * 63):
        with pytest.raises(ProductionProfileError, match="64-hex-char"):
            search_replacement_pose(
                plan=plan,
                camera_id="camera-ground-route-010",
                failing_decision=_failing_decision(),
                preflight_report_sha256=bad,
                topology=topology,
                candidate_policy=_policy(),
            )


def test_search_rejects_camera_not_in_plan() -> None:
    """camera_id must be present in the plan."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    with pytest.raises(ProductionProfileError, match="not present in this plan"):
        search_replacement_pose(
            plan=plan,
            camera_id="camera-ground-route-999",
            failing_decision=_failing_decision(camera_id="camera-ground-route-999"),
            preflight_report_sha256=_VALID_REPORT_SHA,
            topology=topology,
            candidate_policy=_policy(),
        )


def test_search_rejects_topology_mismatch() -> None:
    """topology.topology_ref must equal the camera's topology_ref."""
    plan = _plan()
    wrong_topology = _topology_for_camera(plan, "camera-ground-route-039")
    with pytest.raises(ProductionProfileError, match="topology_ref"):
        search_replacement_pose(
            plan=plan,
            camera_id="camera-ground-route-010",
            failing_decision=_failing_decision(),
            preflight_report_sha256=_VALID_REPORT_SHA,
            topology=wrong_topology,
            candidate_policy=_policy(),
        )


def test_search_rejects_camera_with_null_arc_length() -> None:
    """audit-overview cameras have arc_length_m=None and cannot be searched."""
    plan = _plan()
    audit_camera = next(
        c for c in plan.cameras if c.group_id == "audit-overview"
    )
    # audit-overview has no polyline topology; build a dummy 2-point source
    # with the audit topology_ref just to reach the arc_length check.
    dummy = PolylineTopologySource(
        group_id="audit-overview",
        topology_ref=audit_camera.topology_ref,
        points=((0.0, 0.0), (1.0, 0.0)),
        half_width_m=1.0,
    )
    with pytest.raises(ProductionProfileError, match="arc_length_m=None"):
        search_replacement_pose(
            plan=plan,
            camera_id=audit_camera.camera_id,
            failing_decision=_failing_decision(camera_id=audit_camera.camera_id),
            preflight_report_sha256=_VALID_REPORT_SHA,
            topology=dummy,
            candidate_policy=_policy(),
        )


def test_search_rejects_arc_length_outside_topology() -> None:
    """If the camera's arc_length_m is outside [0, topology.length_m], fail."""
    plan = _plan()
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    sources = resolve_topology_sources(scene, topology)
    camera_010 = next(c for c in plan.cameras if c.camera_id == "camera-ground-route-010")
    ground_sources = sources["ground-route"]
    # Pick a different (shorter) topology source with the same ref pattern
    # but build an artificial one whose length is too short.
    real_source = next(
        s for s in ground_sources if s.topology_ref == camera_010.topology_ref
    )
    # Truncate to a single segment shorter than camera_010.arc_length_m.
    short_source = PolylineTopologySource(
        group_id="ground-route",
        topology_ref=real_source.topology_ref,
        points=real_source.points[:2],
        half_width_m=real_source.half_width_m,
    )
    if short_source.length_m >= (camera_010.arc_length_m or 0.0):
        pytest.skip("could not construct a too-short topology for this camera")
    with pytest.raises(ProductionProfileError, match="outside topology length"):
        search_replacement_pose(
            plan=plan,
            camera_id="camera-ground-route-010",
            failing_decision=_failing_decision(),
            preflight_report_sha256=_VALID_REPORT_SHA,
            topology=short_source,
            candidate_policy=_policy(),
        )


# --------------------------------------------------------------------------- #
# §2: Deterministic candidate search along the topology polyline.
# --------------------------------------------------------------------------- #


def test_search_produces_candidates_in_policy_order() -> None:
    """Candidates must appear in declared (arc_offset, lateral_offset) order."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    expected_order = [
        (arc, lat)
        for arc in (-3.0, -2.0, 2.0, 3.0)
        for lat in (0.0,)
    ]
    actual_order = [
        (c.arc_length_offset_m, c.lateral_offset_m) for c in result.candidates
    ]
    assert actual_order == expected_order


def test_search_accepts_first_geometry_viable_candidate() -> None:
    """accepted_geometry_candidate is the first candidate whose geometry gates pass."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    accepted = result.accepted_geometry_candidate
    assert accepted is not None
    assert accepted.passes_geometry_gates is True
    assert accepted.failure_reasons == ()
    # The accepted candidate is the first passing one in the candidate list.
    first_passing = next(
        c for c in result.candidates if c.passes_geometry_gates
    )
    assert accepted is first_passing


def test_search_returns_none_when_all_candidates_fail() -> None:
    """When every offset fails geometry gates, accepted_geometry_candidate is None."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    # Absurd offsets guarantee every candidate leaves the route.
    absurd_policy = ReposeCandidatePolicy(
        clearance_policy_sha256="0" * 64,
        arc_length_offsets_m=(1_000_000.0,),
        lateral_offsets_m=(0.0,),
        min_spacing_to_other_cameras_m=2.5,
        require_within_half_width=True,
    )
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=absurd_policy,
    )
    assert result.accepted_geometry_candidate is None
    assert all(not c.passes_geometry_gates for c in result.candidates)


def test_accepted_candidate_recalculates_pose_fields() -> None:
    """position, look_at, c2w_opencv, arc_length_m all change from the original."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    accepted = result.accepted_geometry_candidate
    assert accepted is not None
    original = next(c for c in plan.cameras if c.camera_id == "camera-ground-route-010")
    assert accepted.position_m != original.position_m
    assert accepted.look_at_m != original.look_at_m
    assert accepted.c2w_opencv != original.c2w_opencv
    # arc_length_m changes by exactly the accepted arc_length_offset_m.
    expected_arc = round(
        (original.arc_length_m or 0.0) + accepted.arc_length_offset_m, 3,
    )
    assert accepted.arc_length_m == expected_arc


def test_search_rejects_out_of_extent_candidate() -> None:
    """A candidate that leaves the scene extent is tagged as failed."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    # Huge negative arc offset drives the candidate off the route start and
    # likely off the extent; lateral push worsens it.
    huge_policy = ReposeCandidatePolicy(
        clearance_policy_sha256="0" * 64,
        arc_length_offsets_m=(-10_000.0,),
        lateral_offsets_m=(0.0,),
        min_spacing_to_other_cameras_m=2.5,
        require_within_half_width=True,
    )
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=huge_policy,
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.passes_geometry_gates is False
    assert any("outside topology" in reason or "leaves scene" in reason
               for reason in candidate.failure_reasons)


def test_search_rejects_lateral_beyond_half_width() -> None:
    """When require_within_half_width=True, lateral > half_width fails."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    lateral_policy = ReposeCandidatePolicy(
        clearance_policy_sha256="0" * 64,
        arc_length_offsets_m=(0.0,),
        lateral_offsets_m=(topology.half_width_m + 1.0,),
        min_spacing_to_other_cameras_m=2.5,
        require_within_half_width=True,
    )
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=lateral_policy,
    )
    candidate = result.candidates[0]
    assert candidate.passes_geometry_gates is False
    assert any("half_width" in reason for reason in candidate.failure_reasons)


def test_search_allows_lateral_beyond_half_width_when_not_required() -> None:
    """When require_within_half_width=False, lateral can exceed half_width."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    lateral_policy = ReposeCandidatePolicy(
        clearance_policy_sha256="0" * 64,
        arc_length_offsets_m=(2.0,),
        lateral_offsets_m=(topology.half_width_m + 0.3,),
        min_spacing_to_other_cameras_m=1.0,
        require_within_half_width=False,
    )
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=lateral_policy,
    )
    candidate = result.candidates[0]
    # May or may not pass other gates, but must NOT fail on half_width.
    assert not any("half_width" in r for r in candidate.failure_reasons)


def test_search_ground_route_spacing_check() -> None:
    """A candidate that would violate the 30m ground-route spacing is rejected."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    # Tiny offset keeps the candidate on the same spot -- but spacing is
    # about distance to NEIGHBORS, not self.  Construct a policy whose
    # arc offsets stay on the route; the spacing check is exercised by
    # the normal search (the 30m limit is generous enough that normal
    # candidates pass, but we verify the check exists by ensuring every
    # accepted candidate honours it).
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    accepted = result.accepted_geometry_candidate
    assert accepted is not None
    # Re-derive the max ground-route gap with the accepted pose substituted.
    same_route = [
        c for c in plan.cameras
        if c.group_id == "ground-route"
        and c.topology_ref == topology.topology_ref
        and c.camera_id != "camera-ground-route-010"
    ]
    sorted_arc = sorted(
        [(c.arc_length_m or 0.0, c.position_m) for c in same_route]
        + [(accepted.arc_length_m, accepted.position_m)]
    )
    for left, right in zip(sorted_arc, sorted_arc[1:], strict=False):
        gap = math.dist(left[1], right[1])
        assert gap <= 30.0, (
            f"accepted candidate would violate 30m ground-route spacing: "
            f"{gap:.3f}m"
        )


def test_search_predicted_plan_sha_differs_from_original() -> None:
    """The accepted candidate's predicted plan SHA must differ from the original."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    accepted = result.accepted_geometry_candidate
    assert accepted is not None
    assert accepted.predicted_plan_sha256 is not None
    assert accepted.predicted_camera_registry_sha256 is not None
    assert accepted.predicted_plan_sha256 != result.previous_plan_sha256
    assert (
        accepted.predicted_camera_registry_sha256
        != result.previous_camera_registry_sha256
    )


def test_search_does_not_mutate_plan() -> None:
    """The original plan must remain byte-identical after the search."""
    plan = _plan()
    original_bytes = canonical_production_plan_bytes(plan)
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    assert canonical_production_plan_bytes(plan) == original_bytes


def test_search_sha_is_deterministic_and_content_addressed() -> None:
    """Same inputs -> same search_sha256. Different report SHA -> different."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    a = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    b = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    assert a.search_sha256 == b.search_sha256
    c = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256="c" * 64,
        topology=topology,
        candidate_policy=_policy(),
    )
    assert c.search_sha256 != a.search_sha256


def test_search_binds_all_input_shas_into_result() -> None:
    """The result records every input SHA for downstream journal verification."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    assert result.camera_id == "camera-ground-route-010"
    assert result.failing_decision.camera_id == "camera-ground-route-010"
    assert result.failing_decision.passes is False
    assert result.preflight_report_sha256 == _VALID_REPORT_SHA
    assert result.candidate_policy.policy_sha256 == hashlib.sha256(
        canonical_repose_candidate_policy_bytes(_policy()),
    ).hexdigest()
    assert result.topology_ref == topology.topology_ref
    assert result.previous_plan_sha256 == hashlib.sha256(
        canonical_production_plan_bytes(plan),
    ).hexdigest()
    assert result.previous_camera_registry_sha256 == (
        production_camera_registry_digest(plan)
    )


# --------------------------------------------------------------------------- #
# Candidate policy validation
# --------------------------------------------------------------------------- #


def test_candidate_policy_rejects_empty_offsets() -> None:
    with pytest.raises(ProductionProfileError, match="arc_length_offsets_m"):
        ReposeCandidatePolicy(
            clearance_policy_sha256="0" * 64,
            arc_length_offsets_m=(),
            lateral_offsets_m=(0.0,),
            min_spacing_to_other_cameras_m=2.5,
            require_within_half_width=True,
        )
    with pytest.raises(ProductionProfileError, match="lateral_offsets_m"):
        ReposeCandidatePolicy(
            clearance_policy_sha256="0" * 64,
            arc_length_offsets_m=(1.0,),
            lateral_offsets_m=(),
            min_spacing_to_other_cameras_m=2.5,
            require_within_half_width=True,
        )


def test_candidate_policy_rejects_non_finite_offsets() -> None:
    with pytest.raises(ProductionProfileError, match="finite"):
        ReposeCandidatePolicy(
            clearance_policy_sha256="0" * 64,
            arc_length_offsets_m=(float("inf"),),
            lateral_offsets_m=(0.0,),
            min_spacing_to_other_cameras_m=2.5,
            require_within_half_width=True,
        )


def test_candidate_policy_rejects_invalid_clearance_sha() -> None:
    with pytest.raises(ProductionProfileError, match="clearance_policy_sha256"):
        ReposeCandidatePolicy(
            clearance_policy_sha256="short",
            arc_length_offsets_m=(1.0,),
            lateral_offsets_m=(0.0,),
            min_spacing_to_other_cameras_m=2.5,
            require_within_half_width=True,
        )


def test_candidate_policy_rejects_non_positive_min_spacing() -> None:
    with pytest.raises(ProductionProfileError, match="min_spacing"):
        ReposeCandidatePolicy(
            clearance_policy_sha256="0" * 64,
            arc_length_offsets_m=(1.0,),
            lateral_offsets_m=(0.0,),
            min_spacing_to_other_cameras_m=0.0,
            require_within_half_width=True,
        )


def test_candidate_policy_sha_is_content_addressed() -> None:
    """Same contents -> same SHA; any field change -> different SHA."""
    policy_a = _policy()
    policy_b = _policy()
    assert policy_a.policy_sha256 == policy_b.policy_sha256
    policy_c = ReposeCandidatePolicy(
        clearance_policy_sha256="0" * 64,
        arc_length_offsets_m=(-3.0, -2.0, 2.0, 3.1),  # last changed
        lateral_offsets_m=(0.0,),
        min_spacing_to_other_cameras_m=2.5,
        require_within_half_width=True,
    )
    assert policy_c.policy_sha256 != policy_a.policy_sha256


def test_candidate_policy_binds_clearance_policy_sha() -> None:
    """The candidate policy carries the clearance policy SHA it is bound to."""
    from pipeline.synthetic_village.production_preflight import (
        ProductionClearancePolicy,
    )
    clearance_policy = ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )
    bound = ReposeCandidatePolicy(
        clearance_policy_sha256=production_clearance_policy_sha256(
            clearance_policy,
        ),
        arc_length_offsets_m=(1.0,),
        lateral_offsets_m=(0.0,),
        min_spacing_to_other_cameras_m=2.5,
        require_within_half_width=True,
    )
    assert bound.clearance_policy_sha256 == (
        production_clearance_policy_sha256(clearance_policy)
    )


# --------------------------------------------------------------------------- #
# Real-binding smoke test: 010 and 039 both yield a geometry-viable candidate.
# --------------------------------------------------------------------------- #


def test_010_and_039_both_yield_geometry_viable_candidate() -> None:
    """REVIEW-CODEX-011 confirmed 010 and 039 as geometrically obstructed.
    The topology-aware search must produce at least one geometry-viable
    replacement candidate for each, without any {010, 039} whitelist."""
    plan = _plan()
    for camera_id in ("camera-ground-route-010", "camera-ground-route-039"):
        topology = _topology_for_camera(plan, camera_id)
        result = search_replacement_pose(
            plan=plan,
            camera_id=camera_id,
            failing_decision=_failing_decision(camera_id=camera_id),
            preflight_report_sha256=_VALID_REPORT_SHA,
            topology=topology,
            candidate_policy=_policy(),
        )
        assert result.accepted_geometry_candidate is not None, (
            f"{camera_id} produced no geometry-viable candidate"
        )
        assert result.accepted_geometry_candidate.predicted_plan_sha256 is not None


# --------------------------------------------------------------------------- #
# End-to-end binding smoke test
# --------------------------------------------------------------------------- #


def _real_clearance_report_with_010_failing():
    """Build a real ProductionClearanceReport where 010 fails and 034/039 pass.

    Reuses the same _render_request() pipeline as production_preflight tests
    so the plan / object registry / build identities all come from one
    canonical source instead of hand-filled hex strings.
    """
    render_request = _render_request()
    plan = render_request.production_plan
    policy = ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )
    clearance_request = build_production_clearance_request(
        plan=plan,
        selected_camera_ids=(
            "camera-ground-route-010",
            "camera-ground-route-034",
            "camera-ground-route-039",
        ),
        build_id=render_request.build_id,
        blender_executable_sha256=render_request.blender_executable_sha256,
        preflight_script_sha256="6" * 64,
        blend_sha256=render_request.blend_sha256,
        build_report_sha256=render_request.build_report_sha256,
        object_registry=render_request.object_registry,
        auxiliary_registry=render_request.auxiliary_registry,
        semantic_registry=render_request.semantic_registry,
        policy=policy,
    )
    # 010: all 15 upper-middle rays hit at 0.5 m -> fails
    # 034/039: no hits -> pass
    hit_set = {
        (sample_x, sample_y): 0.5
        for sample_x in policy.sample_grid
        for sample_y in (0.0, 0.45, 0.9)
    }
    evidence = tuple(
        ProductionCameraClearanceEvidence(
            camera_id=camera_id,
            rays=tuple(
                ProductionClearanceRayEvidence(
                    sample_x=sample_x,
                    sample_y=sample_y,
                    hit=(camera_id == "camera-ground-route-010"
                         and (sample_x, sample_y) in hit_set),
                    distance_m=(
                        0.5
                        if camera_id == "camera-ground-route-010"
                        and (sample_x, sample_y) in hit_set
                        else None
                    ),
                    object_name=(
                        "SV_Lower_Bridge"
                        if camera_id == "camera-ground-route-010"
                        and (sample_x, sample_y) in hit_set
                        else None
                    ),
                    stable_id=(
                        "lower-bridge"
                        if camera_id == "camera-ground-route-010"
                        and (sample_x, sample_y) in hit_set
                        else None
                    ),
                    part_id=(
                        "deck"
                        if camera_id == "camera-ground-route-010"
                        and (sample_x, sample_y) in hit_set
                        else None
                    ),
                    semantic_id=(
                        3
                        if camera_id == "camera-ground-route-010"
                        and (sample_x, sample_y) in hit_set
                        else None
                    ),
                )
                for sample_y in (-0.9, -0.45, 0.0, 0.45, 0.9)
                for sample_x in (-0.9, -0.45, 0.0, 0.45, 0.9)
            ),
        )
        for camera_id in clearance_request.selected_camera_ids
    )
    report = build_production_clearance_report(
        clearance_request,
        evidence=evidence,
    )
    return clearance_request, report


def test_search_consumes_real_clearance_report_sha_end_to_end() -> None:
    """End-to-end: preflight report -> failing decision -> repose search.

    The existing _VALID_REPORT_SHA = "a"*64 tests prove the API accepts
    a well-formed SHA, but they do NOT prove the SHA actually comes from a
    real ProductionClearanceReport. This test builds a real report (with
    canonical bytes, real policy SHA, real evidence, real decisions) and
    feeds its SHA + the real failing decision into search_replacement_pose,
    proving the repose search is content-bound to actual preflight output
    rather than to a hand-filled string.
    """
    clearance_request, report = _real_clearance_report_with_010_failing()

    # Sanity: 010 failed, 034/039 passed
    decisions_by_id = {d.camera_id: d for d in report.decisions}
    failing = decisions_by_id["camera-ground-route-010"]
    assert failing.passes is False
    assert failing.failed_rule_ids == ("upper-middle-near-hit-count",)
    assert decisions_by_id["camera-ground-route-034"].passes is True
    assert decisions_by_id["camera-ground-route-039"].passes is True

    # The real report SHA (canonical bytes -> sha256)
    real_report_sha = hashlib.sha256(
        canonical_production_clearance_report_bytes(report),
    ).hexdigest()

    # The real policy SHA must match the decision's policy_sha256
    real_policy_sha = production_clearance_policy_sha256(
        clearance_request.policy,
    )
    assert failing.policy_sha256 == real_policy_sha
    assert report.policy_sha256 == real_policy_sha

    # Repose candidate policy bound to the REAL clearance policy SHA
    plan = clearance_request.production_plan
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    candidate_policy = ReposeCandidatePolicy(
        clearance_policy_sha256=real_policy_sha,
        arc_length_offsets_m=(-3.0, -2.0, 2.0, 3.0),
        lateral_offsets_m=(0.0,),
        min_spacing_to_other_cameras_m=2.5,
        require_within_half_width=True,
    )

    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=failing,
        preflight_report_sha256=real_report_sha,
        topology=topology,
        candidate_policy=candidate_policy,
    )

    # The repose search must carry the REAL report SHA verbatim -- not a
    # hand-filled placeholder. This is the binding REVIEW-CODEX-014 P0
    # asked for: repose cannot be searched without a real failing
    # decision bound to a real preflight report.
    assert result.preflight_report_sha256 == real_report_sha
    assert result.failing_decision == failing
    assert result.candidate_policy.clearance_policy_sha256 == real_policy_sha

    # Geometry viability still works on the real plan
    assert result.accepted_geometry_candidate is not None
    assert result.accepted_geometry_candidate.passes_geometry_gates is True
    assert result.accepted_geometry_candidate.predicted_plan_sha256 is not None
    assert (
        result.accepted_geometry_candidate.predicted_plan_sha256
        != result.previous_plan_sha256
    )

    # The search_sha256 must bind the real report SHA into its canonical
    # bytes -- swapping the report SHA for a different hex string must
    # produce a different search_sha256.
    different_report_sha = "b" * 64
    different_result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=failing,
        preflight_report_sha256=different_report_sha,
        topology=topology,
        candidate_policy=candidate_policy,
    )
    assert different_result.search_sha256 != result.search_sha256


# --------------------------------------------------------------------------- #
# Adversarial boundary tests
# --------------------------------------------------------------------------- #


def test_search_binds_previous_pose_sha256_of_original_camera() -> None:
    """previous_pose_sha256 must be the SHA of the original camera's pose.

    If the original camera's pose changes (e.g. someone tampered with its
    position before invoking the search), previous_pose_sha256 must change
    too -- otherwise a caller could silently substitute a different
    starting pose and the journal would not detect it.
    """
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    result = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )

    # Tamper with the original camera's position_m.  We cannot use
    # model_copy on the plan directly because plan.cameras is a tuple;
    # rebuild the tuple with one substituted camera.
    original_camera = next(
        c for c in plan.cameras if c.camera_id == "camera-ground-route-010"
    )
    tampered_camera = original_camera.model_copy(
        update={"position_m": (
            original_camera.position_m[0] + 0.001,
            original_camera.position_m[1],
            original_camera.position_m[2],
        )},
    )
    # Re-validate via canonical bytes so the new pose is actually a
    # ProductionCameraPose, not an unvalidated copy.
    tampered_cameras = tuple(
        tampered_camera if c.camera_id == "camera-ground-route-010" else c
        for c in plan.cameras
    )
    tampered_plan = plan.model_copy(update={"cameras": tampered_cameras})
    tampered_plan = type(plan).model_validate_json(
        canonical_production_plan_bytes(tampered_plan),
    )

    tampered_result = search_replacement_pose(
        plan=tampered_plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )

    assert (
        tampered_result.previous_pose_sha256
        != result.previous_pose_sha256
    ), (
        "previous_pose_sha256 must change when the original camera's pose "
        "changes; otherwise a caller could substitute a different starting "
        "pose and the journal would not detect the substitution"
    )


def test_search_sha_changes_when_candidate_verdicts_change() -> None:
    """search_sha256 must bind the candidate verdict sequence, not just inputs.

    Two searches with the same plan/policy/report SHA but different
    candidate verdict sequences must produce different search_sha256.
    We force different verdicts by shifting the original camera's
    arc_length near the topology end so positive arc offsets fall outside.
    """
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    original_camera = next(
        c for c in plan.cameras if c.camera_id == "camera-ground-route-010"
    )
    # Move 010's arc_length to near the end of the topology so +2.0/+3.0
    # offsets fall outside the topology and produce failing candidates.
    new_arc = topology.length_m - 1.0
    moved_camera = original_camera.model_copy(
        update={"arc_length_m": new_arc},
    )
    moved_cameras = tuple(
        moved_camera if c.camera_id == "camera-ground-route-010" else c
        for c in plan.cameras
    )
    moved_plan = plan.model_copy(update={"cameras": moved_cameras})
    moved_plan = type(plan).model_validate_json(
        canonical_production_plan_bytes(moved_plan),
    )

    # Sanity: the moved plan still has 010 as a valid camera with a
    # non-null arc_length within the topology range.
    moved_camera_after = next(
        c for c in moved_plan.cameras
        if c.camera_id == "camera-ground-route-010"
    )
    assert moved_camera_after.arc_length_m == new_arc
    assert 0.0 <= new_arc <= topology.length_m

    result_original = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    result_moved = search_replacement_pose(
        plan=moved_plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )

    # Both searches use the same policy and report SHA.  The candidate
    # verdicts MUST differ: in result_moved, the +2.0 and +3.0 offsets
    # should now fall outside topology length and fail Gate 1.
    original_verdicts = tuple(
        c.passes_geometry_gates for c in result_original.candidates
    )
    moved_verdicts = tuple(
        c.passes_geometry_gates for c in result_moved.candidates
    )
    assert original_verdicts != moved_verdicts, (
        "test setup failed: moving the camera near topology end should "
        "change which candidates pass geometry gates"
    )

    # search_sha256 must differ because the candidate verdict sequence
    # is part of the canonical bytes.  If it didn't, a caller could
    # alter the plan to produce different verdicts and reuse an old
    # search_sha256 in the journal.
    assert result_original.search_sha256 != result_moved.search_sha256, (
        "search_sha256 must change when candidate verdicts change, even "
        "if the policy / report SHA / camera_id are identical"
    )


# --------------------------------------------------------------------------- #
# build_reposed_plan: public helper for Task 5 §3 callers
# --------------------------------------------------------------------------- #


def _successful_search(plan=None):
    """Run search_replacement_pose for 010 and return (plan, search)."""
    if plan is None:
        plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    search = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=_policy(),
    )
    assert search.accepted_geometry_candidate is not None
    return plan, search


def test_build_reposed_plan_rebuilds_with_accepted_candidate() -> None:
    """build_reposed_plan returns a plan whose SHA matches predicted."""
    plan, search = _successful_search()
    accepted = search.accepted_geometry_candidate

    new_plan, plan_sha, registry_sha = build_reposed_plan(
        search=search, plan=plan,
    )

    assert plan_sha == accepted.predicted_plan_sha256
    assert registry_sha == accepted.predicted_camera_registry_sha256
    # The new plan is not the same as the old plan (different SHA).
    assert plan_sha != search.previous_plan_sha256

    # The new plan is a valid ProductionCameraPlan (already enforced by
    # model_validate_json inside build_reposed_plan, but double-check).
    new_plan_sha = hashlib.sha256(
        canonical_production_plan_bytes(new_plan),
    ).hexdigest()
    assert new_plan_sha == plan_sha


def test_build_reposed_plan_substitutes_reposed_camera() -> None:
    """The new plan has the reposed camera substituted in, others preserved."""
    plan, search = _successful_search()
    accepted = search.accepted_geometry_candidate
    new_plan, _, _ = build_reposed_plan(search=search, plan=plan)

    # The reposed camera in the new plan has the accepted candidate's pose.
    new_camera = next(
        c for c in new_plan.cameras
        if c.camera_id == "camera-ground-route-010"
    )
    assert new_camera.position_m == accepted.position_m
    assert new_camera.look_at_m == accepted.look_at_m
    assert new_camera.arc_length_m == accepted.arc_length_m
    assert new_camera.c2w_opencv == accepted.c2w_opencv

    # The other 179 cameras are unchanged.
    original_by_id = {c.camera_id: c for c in plan.cameras}
    for camera in new_plan.cameras:
        if camera.camera_id == "camera-ground-route-010":
            continue
        assert camera == original_by_id[camera.camera_id], (
            f"camera {camera.camera_id} changed when only 010 should have"
        )


def test_build_reposed_plan_preserves_camera_metadata() -> None:
    """The reposed camera keeps topology_ref/group_id/intrinsics/etc."""
    plan, search = _successful_search()
    accepted = search.accepted_geometry_candidate
    new_plan, _, _ = build_reposed_plan(search=search, plan=plan)

    original_camera = next(
        c for c in plan.cameras if c.camera_id == "camera-ground-route-010"
    )
    new_camera = next(
        c for c in new_plan.cameras
        if c.camera_id == "camera-ground-route-010"
    )
    # Mutable-on-repose fields are substituted.
    assert new_camera.position_m == accepted.position_m
    assert new_camera.look_at_m == accepted.look_at_m
    assert new_camera.arc_length_m == accepted.arc_length_m
    assert new_camera.c2w_opencv == accepted.c2w_opencv
    # Metadata fields are preserved.
    assert new_camera.camera_id == original_camera.camera_id
    assert new_camera.group_id == original_camera.group_id
    assert new_camera.sequence_index == original_camera.sequence_index
    assert new_camera.topology_ref == original_camera.topology_ref
    assert new_camera.eye_height_m == original_camera.eye_height_m
    assert new_camera.fov_x_deg == original_camera.fov_x_deg
    assert new_camera.intrinsics == original_camera.intrinsics
    assert new_camera.audit_only == original_camera.audit_only
    assert new_camera.disclosure == original_camera.disclosure


def test_build_reposed_plan_rejects_none_accepted_candidate() -> None:
    """If the search found no viable candidate, build_reposed_plan fails."""
    plan = _plan()
    topology = _topology_for_camera(plan, "camera-ground-route-010")
    absurd_policy = ReposeCandidatePolicy(
        clearance_policy_sha256="0" * 64,
        arc_length_offsets_m=(1_000_000.0,),
        lateral_offsets_m=(0.0,),
        min_spacing_to_other_cameras_m=2.5,
        require_within_half_width=True,
    )
    search = search_replacement_pose(
        plan=plan,
        camera_id="camera-ground-route-010",
        failing_decision=_failing_decision(),
        preflight_report_sha256=_VALID_REPORT_SHA,
        topology=topology,
        candidate_policy=absurd_policy,
    )
    assert search.accepted_geometry_candidate is None
    with pytest.raises(ProductionProfileError, match="accepted_geometry_candidate"):
        build_reposed_plan(search=search, plan=plan)


def test_build_reposed_plan_rejects_wrong_plan() -> None:
    """If the caller passes a different plan, fail closed."""
    plan_a, search = _successful_search()
    # Build a different plan: 180 cameras but with one moved.
    # We construct it by mutating plan_a through the canonical round-trip.
    original_camera = next(
        c for c in plan_a.cameras if c.camera_id == "camera-ground-route-010"
    )
    moved_camera = original_camera.model_copy(
        update={"position_m": (
            original_camera.position_m[0] + 0.001,
            original_camera.position_m[1],
            original_camera.position_m[2],
        )},
    )
    moved_cameras = tuple(
        moved_camera if c.camera_id == "camera-ground-route-010" else c
        for c in plan_a.cameras
    )
    plan_b = plan_a.model_copy(update={"cameras": moved_cameras})
    plan_b = type(plan_a).model_validate_json(
        canonical_production_plan_bytes(plan_b),
    )

    # Sanity: plan_b has a different SHA than plan_a.
    plan_a_sha = hashlib.sha256(
        canonical_production_plan_bytes(plan_a),
    ).hexdigest()
    plan_b_sha = hashlib.sha256(
        canonical_production_plan_bytes(plan_b),
    ).hexdigest()
    assert plan_a_sha != plan_b_sha

    with pytest.raises(ProductionProfileError, match="SHA"):
        build_reposed_plan(search=search, plan=plan_b)


def test_build_reposed_plan_returns_revalidated_plan() -> None:
    """The returned plan has been through model_validate_json round-trip."""
    plan, search = _successful_search()
    new_plan, _, _ = build_reposed_plan(search=search, plan=plan)

    # model_validate_json enforces strict mode + re-runs all model
    # validators. If the reposed camera violated any invariant the plan
    # builder relies on, build_reposed_plan would have raised. So just
    # by returning successfully, the new_plan is a fully validated plan.
    # We can assert it has the right camera count and is frozen.
    assert len(new_plan.cameras) == len(plan.cameras)
    # Pydantic frozen models raise on setattr.
    with pytest.raises((TypeError, ValueError, AttributeError)):
        new_plan.cameras = new_plan.cameras[:1]  # type: ignore[misc]



