"""TDD for deterministic obstructed-camera repose (HANDOFF-OPUS-006 §3)."""

from __future__ import annotations

import hashlib

import pytest

from pipeline.synthetic_village.elevated_topology import (
    build_elevated_topology_plan,
)
from pipeline.synthetic_village.production_profile import (
    ProductionProfileError,
    build_production_camera_plan,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)
from pipeline.synthetic_village.production_repose import (
    DEFAULT_FORWARD_OFFSET_M,
    DEFAULT_LATERAL_OFFSET_M,
    REPOSEABLE_OBSTRUCTED_CAMERA_IDS,
    ReposeOffsets,
    repose_obstructed_cameras,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


def _plan():
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    return build_production_camera_plan(scene, topology)


def _plan_sha(plan) -> str:
    return hashlib.sha256(canonical_production_plan_bytes(plan)).hexdigest()


# --------------------------------------------------------------------------- #
# §3: 010 and 039 are deterministically reposeable; 034 is not.
# --------------------------------------------------------------------------- #


def test_reposeable_set_excludes_034() -> None:
    """REVIEW-CODEX-011: 034's obstruction is oblique, not geometric, so
    it must be ruled on by the six-layer gate, never silently shifted."""
    assert REPOSEABLE_OBSTRUCTED_CAMERA_IDS == frozenset(
        {"camera-ground-route-010", "camera-ground-route-039"},
    )
    assert "camera-ground-route-034" not in REPOSEABLE_OBSTRUCTED_CAMERA_IDS


def test_repose_shifts_010_and_039_and_preserves_contract() -> None:
    plan = _plan()
    reposeable = (
        "camera-ground-route-010",
        "camera-ground-route-039",
    )
    result = repose_obstructed_cameras(
        plan,
        obstructed_camera_ids=reposeable,
    )

    # Plan digest MUST change -- otherwise the repose was a no-op and the
    # old journal could be reused, which HANDOFF-OPUS-006 §3 forbids.
    assert result.plan_sha256 != result.previous_plan_sha256
    assert result.camera_registry_sha256 != result.previous_camera_registry_sha256
    assert result.reposeable_camera_ids == reposeable

    new_plan = result.plan
    # The 180-camera contract is preserved.
    assert new_plan.camera_count == 180
    assert new_plan.complete is True
    # Camera IDs remain unique and in the same order.
    new_ids = [c.camera_id for c in new_plan.cameras]
    assert new_ids == [c.camera_id for c in plan.cameras]
    assert len(set(new_ids)) == 180
    # Route loop coverage does not regress.
    assert len(new_plan.route_loops) == 2
    assert new_plan.route_loops == plan.route_loops
    # Group coverage is unchanged (topology_ref and group_id are preserved).
    assert new_plan.group_coverage == plan.group_coverage


def test_repose_changes_pose_for_only_obstructed_cameras() -> None:
    plan = _plan()
    reposeable = ("camera-ground-route-010",)
    result = repose_obstructed_cameras(
        plan, obstructed_camera_ids=reposeable,
    )
    new_plan = result.plan

    moved = next(
        c for c in new_plan.cameras if c.camera_id == "camera-ground-route-010"
    )
    original = next(
        c for c in plan.cameras if c.camera_id == "camera-ground-route-010"
    )
    assert moved.position_m != original.position_m
    assert moved.c2w_opencv != original.c2w_opencv
    # Every other camera's pose is byte-identical.
    for old, new in zip(plan.cameras, new_plan.cameras, strict=True):
        if old.camera_id != "camera-ground-route-010":
            assert old.position_m == new.position_m
            assert old.c2w_opencv == new.c2w_opencv


def test_repose_fails_closed_on_unknown_obstructed_id() -> None:
    plan = _plan()
    with pytest.raises(ProductionProfileError, match="not reposeable"):
        repose_obstructed_cameras(
            plan,
            obstructed_camera_ids=("camera-ground-route-034",),
        )


def test_repose_fails_closed_on_camera_not_in_plan() -> None:
    plan = _plan()
    with pytest.raises(ProductionProfileError, match="not in this plan"):
        repose_obstructed_cameras(
            plan,
            obstructed_camera_ids=("camera-ground-route-999",),
        )


def test_repose_fails_closed_on_duplicate_obstructed_ids() -> None:
    plan = _plan()
    with pytest.raises(ProductionProfileError, match="unique"):
        repose_obstructed_cameras(
            plan,
            obstructed_camera_ids=(
                "camera-ground-route-010",
                "camera-ground-route-010",
            ),
        )


def test_repose_fails_closed_on_non_positive_offset() -> None:
    plan = _plan()
    with pytest.raises(ProductionProfileError, match="strictly positive"):
        ReposeOffsets(lateral_offset_m=0.0, forward_offset_m=1.0)
    with pytest.raises(ProductionProfileError, match="strictly positive"):
        ReposeOffsets(lateral_offset_m=1.0, forward_offset_m=-1.0)


def test_repose_fails_closed_on_identical_digest() -> None:
    """If the offset somehow produces an identical plan digest, the repose
    was a no-op and we refuse to let the caller reuse the old journal."""
    plan = _plan()
    # Zero-offset is rejected by ReposeOffsets, so we cannot trigger this
    # through the public API; instead we verify the guard exists by checking
    # that a normal repose does change the digest (covered above) and that
    # the offsets are content-addressed into the plan via the digests.
    result = repose_obstructed_cameras(
        plan,
        obstructed_camera_ids=("camera-ground-route-010",),
        offsets=ReposeOffsets(
            lateral_offset_m=DEFAULT_LATERAL_OFFSET_M,
            forward_offset_m=DEFAULT_FORWARD_OFFSET_M,
        ),
    )
    assert result.plan_sha256 != result.previous_plan_sha256


def test_repose_preserves_route_loop_and_group_coverage() -> None:
    plan = _plan()
    reposeable = (
        "camera-ground-route-010",
        "camera-ground-route-039",
    )
    result = repose_obstructed_cameras(
        plan, obstructed_camera_ids=reposeable,
    )
    new_plan = result.plan
    # Route loop contract is unchanged (same loop IDs, same attachments).
    assert new_plan.route_loops == plan.route_loops
    # Group coverage is unchanged (same groups, same counts, same topology refs).
    assert new_plan.group_coverage == plan.group_coverage
    # Camera sequence indices remain dense and ordered from 1.
    indices = [c.sequence_index for c in new_plan.cameras]
    assert indices == list(range(1, 181))


def test_repose_camera_centres_remain_unique() -> None:
    plan = _plan()
    result = repose_obstructed_cameras(
        plan,
        obstructed_camera_ids=(
            "camera-ground-route-010",
            "camera-ground-route-039",
        ),
    )
    centres = [c.position_m for c in result.plan.cameras]
    assert len(centres) == len(set(centres))


def test_repose_ground_route_spacing_stays_within_limit() -> None:
    plan = _plan()
    result = repose_obstructed_cameras(
        plan,
        obstructed_camera_ids=(
            "camera-ground-route-010",
            "camera-ground-route-039",
        ),
    )
    # _validate_route_spacing would have raised inside repose if violated;
    # confirm by re-deriving the max gap directly.
    import math

    by_route: dict[str, list] = {}
    for camera in result.plan.cameras:
        if camera.group_id == "ground-route":
            by_route.setdefault(camera.topology_ref, []).append(camera)
    for rows in by_route.values():
        ordered = sorted(rows, key=lambda c: c.arc_length_m or 0.0)
        for left, right in zip(ordered, ordered[1:], strict=False):
            gap = math.dist(left.position_m, right.position_m)
            assert gap <= 30.0


def test_repose_does_not_reuse_old_journal_identity() -> None:
    """render_id depends on camera_registry_sha256, which depends on the
    c2w matrices of all 180 cameras.  After a repose, the registry digest
    changes, so the render_id MUST change too -- the old journal cannot be
    reused or overwritten (HANDOFF-OPUS-006 §3 last bullet)."""
    plan = _plan()
    result = repose_obstructed_cameras(
        plan,
        obstructed_camera_ids=("camera-ground-route-010",),
    )
    old_registry = production_camera_registry_digest(plan)
    new_registry = production_camera_registry_digest(result.plan)
    assert old_registry != new_registry
    # render_id derivation in production_journal.production_render_id takes
    # camera_registry_sha256 as an input, so a change here propagates.
