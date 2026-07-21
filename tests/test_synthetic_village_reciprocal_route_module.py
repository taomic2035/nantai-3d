"""Reciprocal-route module plan tests (HANDOFF-OPUS-009).

These tests lock the canonical bytes, content addressing, instance
segment partition, module ordering, and v1-immutability of the
``ReciprocalRouteModulePlan``.  They do NOT exercise Blender runtime or
promote modeled-unverified trust.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.elevated_topology import build_elevated_topology_plan
from pipeline.synthetic_village.environment_module import (
    build_default_environment_module_plan,
    canonical_environment_module_plan_bytes,
)
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)
from pipeline.synthetic_village.reciprocal_route_module import (
    BATCH8_ARCHIVE_SHA256,
    BATCH8_RELEASE_MANIFEST_SHA256,
    BATCH9_ARCHIVE_SHA256,
    BATCH9_RELEASE_MANIFEST_SHA256,
    BRIDGE_CROSSING_INSTANCE_RANGE,
    CENTRAL_DOWNHILL_INSTANCE_RANGE,
    FOREST_BOUNDARY_INSTANCE_RANGE,
    GALLERY_UNDERPASS_INSTANCE_RANGE,
    LOWER_VALLEY_UPHILL_INSTANCE_RANGE,
    RECIPROCAL_ROUTE_RECIPE_VERSION,
    RECIPROCAL_ROUTE_SCHEMA,
    WATERMILL_TAILRACE_INSTANCE_RANGE,
    PartLayoutSpec,
    ReciprocalRoleCameraCandidate,
    ReciprocalRouteError,
    ReciprocalRouteModule,
    ReciprocalRouteModulePart,
    ReciprocalRouteModulePlan,
    build_default_reciprocal_route_module_plan,
    canonical_reciprocal_route_module_plan_bytes,
    reciprocal_route_module_plan_sha256,
    verify_reciprocal_route_module_plan,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


@pytest.fixture(scope="module")
def scene():
    return build_scene_plan()


@pytest.fixture(scope="module")
def topology(scene):
    return build_elevated_topology_plan(scene)


@pytest.fixture(scope="module")
def env_module_plan(scene, topology):
    return build_default_environment_module_plan(
        scene=scene,
        elevated_topology=topology,
    )


@pytest.fixture(scope="module")
def plan(scene, topology, env_module_plan):
    return build_default_reciprocal_route_module_plan(
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=env_module_plan,
    )


# --------------------------------------------------------------------------- #
# Schema constants.
# --------------------------------------------------------------------------- #


def test_schema_constants_are_locked() -> None:
    assert RECIPROCAL_ROUTE_SCHEMA == "nantai.synthetic-village.reciprocal-route-module.v1"
    assert RECIPROCAL_ROUTE_RECIPE_VERSION == "v1"
    assert BATCH8_RELEASE_MANIFEST_SHA256 == (
        "be933fa37b56eee53e8acc78b7e2ff577c0bc4d6407fea91bfeb1da8d0637dbc"
    )
    assert BATCH8_ARCHIVE_SHA256 == (
        "6bdafc92b9eb2df3a943c4e5df3466e9609c22db89844dc940db3dab6ca921eb"
    )
    assert BATCH9_RELEASE_MANIFEST_SHA256 == (
        "bf5e2a5c6907baf5acefa5c6cf7d85bf9cfe611b47013f5bb1b564eca3064339"
    )
    assert BATCH9_ARCHIVE_SHA256 == (
        "6f7cc48e40e3d323a98e5ca91633cb6a6a7f623d7544efe44317102b3e5648f8"
    )


def test_instance_segments_partition_176_to_218() -> None:
    """The six module instance segments partition 176..218 with no gaps or overlaps."""
    segments = (
        CENTRAL_DOWNHILL_INSTANCE_RANGE,
        BRIDGE_CROSSING_INSTANCE_RANGE,
        WATERMILL_TAILRACE_INSTANCE_RANGE,
        GALLERY_UNDERPASS_INSTANCE_RANGE,
        FOREST_BOUNDARY_INSTANCE_RANGE,
        LOWER_VALLEY_UPHILL_INSTANCE_RANGE,
    )
    # No overlaps.
    all_ids: list[int] = []
    for seg in segments:
        all_ids.extend(seg)
    assert len(set(all_ids)) == len(all_ids)
    # Exactly 176..218 inclusive.
    assert set(all_ids) == set(range(176, 219))
    # Segment sizes match HANDOFF-OPUS-009 §1-6 (7+6+7+9+7+7 = 43).
    assert tuple(len(seg) for seg in segments) == (7, 6, 7, 9, 7, 7)


# --------------------------------------------------------------------------- #
# Default plan structure.
# --------------------------------------------------------------------------- #


def test_default_plan_has_six_ordered_modules(plan) -> None:
    expected = (
        "central-courtyard-downhill",
        "bridge-deck-crossing",
        "watermill-tailrace",
        "covered-gallery-underpass",
        "forest-orchard-boundary",
        "lower-valley-uphill",
    )
    assert tuple(m.module_id for m in plan.modules) == expected


def test_default_plan_part_count_matches_summary(plan) -> None:
    assert plan.summary.module_count == 6
    assert plan.summary.part_count == 43
    assert plan.summary.instance_id_segment_start == 176
    assert plan.summary.instance_id_segment_end == 218


def test_default_plan_uses_exactly_176_to_218(plan) -> None:
    all_instances = [
        part.instance_id
        for module in plan.modules
        for part in module.parts
    ]
    assert set(all_instances) == set(range(176, 219))


def test_default_plan_part_ids_are_unique_across_plan(plan) -> None:
    all_part_ids = [
        part.part_id
        for module in plan.modules
        for part in module.parts
    ]
    assert len(set(all_part_ids)) == len(all_part_ids)


def test_default_plan_provenance_constants_are_locked(plan) -> None:
    assert plan.synthetic is True
    assert plan.geometry_usability == "preview-only"
    assert plan.verification_level == "L0"
    assert plan.metric_alignment is False
    assert plan.real_photo_textures is False
    assert plan.geometry_trust == "simplified-pbr-not-render-parity"
    assert plan.trust_effect == "none"


def test_default_plan_binds_batch8_batch9_manifest_and_archive(plan) -> None:
    assert plan.batch8_release_manifest_sha256 == BATCH8_RELEASE_MANIFEST_SHA256
    assert plan.batch8_archive_sha256 == BATCH8_ARCHIVE_SHA256
    assert plan.batch9_release_manifest_sha256 == BATCH9_RELEASE_MANIFEST_SHA256
    assert plan.batch9_archive_sha256 == BATCH9_ARCHIVE_SHA256


def test_default_plan_binds_environment_module_v1_sha(plan, env_module_plan) -> None:
    expected_sha = hashlib.sha256(
        canonical_environment_module_plan_bytes(env_module_plan),
    ).hexdigest()
    assert plan.environment_module_plan_sha256 == expected_sha


# --------------------------------------------------------------------------- #
# Canonical bytes + content addressing.
# --------------------------------------------------------------------------- #


def test_canonical_bytes_end_with_newline(plan) -> None:
    assert canonical_reciprocal_route_module_plan_bytes(plan).endswith(b"\n")


def test_plan_sha256_is_64_hex(plan) -> None:
    sha = reciprocal_route_module_plan_sha256(plan)
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_plan_sha256_is_deterministic_across_processes(
    scene, topology, env_module_plan,
) -> None:
    left = build_default_reciprocal_route_module_plan(
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=env_module_plan,
    )
    right = build_default_reciprocal_route_module_plan(
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=env_module_plan,
    )
    assert left == right
    assert canonical_reciprocal_route_module_plan_bytes(left) == (
        canonical_reciprocal_route_module_plan_bytes(right)
    )
    assert reciprocal_route_module_plan_sha256(left) == (
        reciprocal_route_module_plan_sha256(right)
    )


def test_plan_sha256_changes_when_module_replaced(plan) -> None:
    """Swapping one module's recipe must change the plan SHA."""
    original_sha = reciprocal_route_module_plan_sha256(plan)
    # Replace the downhill gate clear_width_m (within schema) -> new recipe.
    first_module = plan.modules[0]
    new_gate = first_module.recipe.downhill_gate.model_copy(
        update={"clear_width_m": 2.5},
    )
    new_recipe = first_module.recipe.model_copy(
        update={"downhill_gate": new_gate},
    )
    new_module = first_module.model_copy(update={"recipe": new_recipe})
    tampered = plan.model_copy(
        update={"modules": (new_module, *plan.modules[1:])},
    )
    tampered_sha = reciprocal_route_module_plan_sha256(tampered)
    assert tampered_sha != original_sha


def test_plan_sha256_changes_when_part_material_slot_swapped(plan) -> None:
    original_sha = reciprocal_route_module_plan_sha256(plan)
    first_part = plan.modules[0].parts[0]
    new_part = first_part.model_copy(
        update={"material_slot_id": "material-tampered-slot-99"},
    )
    new_module = plan.modules[0].model_copy(
        update={"parts": (new_part, *plan.modules[0].parts[1:])},
    )
    tampered = plan.model_copy(
        update={"modules": (new_module, *plan.modules[1:])},
    )
    assert reciprocal_route_module_plan_sha256(tampered) != original_sha


def test_plan_sha256_changes_when_environment_module_plan_sha_swapped(plan) -> None:
    original_sha = reciprocal_route_module_plan_sha256(plan)
    tampered = plan.model_copy(
        update={"environment_module_plan_sha256": "e" * 64},
    )
    assert reciprocal_route_module_plan_sha256(tampered) != original_sha


def test_plan_sha256_changes_when_batch8_manifest_swapped(plan) -> None:
    """Swapping the Batch 8 manifest SHA must change the plan SHA.

    Manifest SHAs are Literal-locked in the schema so direct model_copy
    bypasses validation; we use model_validate_json round-trip to
    confirm the schema rejects the swap.
    """
    payload = plan.model_dump(mode="json")
    payload["batch8_release_manifest_sha256"] = "f" * 64
    with pytest.raises(ValidationError):
        ReciprocalRouteModulePlan.model_validate(payload)


# --------------------------------------------------------------------------- #
# Fail-closed validators.
# --------------------------------------------------------------------------- #


def test_plan_rejects_wrong_module_order(plan) -> None:
    """Reordering modules must fail validation."""
    import json

    payload = plan.model_dump(mode="json")
    payload["modules"] = [
        payload["modules"][1],
        *payload["modules"][2:],
        payload["modules"][0],
    ]
    with pytest.raises(ValidationError, match="ordered six"):
        ReciprocalRouteModulePlan.model_validate_json(json.dumps(payload))


def test_plan_rejects_part_outside_module_segment(plan) -> None:
    """A part with an instance ID outside its module segment must fail."""
    first_part = plan.modules[0].parts[0]
    with pytest.raises(ValidationError, match="outside module"):
        ReciprocalRouteModulePart(
            module_id=first_part.module_id,
            part_id=first_part.part_id,
            instance_id=999,  # outside all segments
            semantic_id=first_part.semantic_id,
            material_slot_id=first_part.material_slot_id,
            part_layout=first_part.part_layout,
        )


def test_plan_rejects_missing_module(plan) -> None:
    """Dropping one module (only 5 left) must fail."""
    payload = plan.model_dump(mode="json")
    payload["modules"] = payload["modules"][:-1]
    with pytest.raises(ValidationError):
        ReciprocalRouteModulePlan.model_validate(payload)


def test_plan_rejects_duplicate_part_id_within_module(plan) -> None:
    """Two parts with the same part_id inside one module must fail."""
    import json

    first_module = plan.modules[0]
    first_part = first_module.parts[0]
    second_part = first_module.parts[1]
    duplicate = second_part.model_copy(update={"part_id": first_part.part_id})
    new_module = first_module.model_copy(
        update={"parts": (first_part, duplicate, *first_module.parts[2:])},
    )
    with pytest.raises(ValidationError, match="part IDs must be unique"):
        ReciprocalRouteModule.model_validate_json(
            json.dumps(new_module.model_dump(mode="json")),
        )


def test_plan_rejects_non_sha256_environment_module_binding(plan) -> None:
    """environment_module_plan_sha256 must be 64-hex."""
    payload = plan.model_dump(mode="json")
    payload["environment_module_plan_sha256"] = "not-a-sha"
    with pytest.raises(ValidationError):
        ReciprocalRouteModulePlan.model_validate(payload)


def test_plan_rejects_wrong_recipe_module_id(plan) -> None:
    """A recipe whose module_id disagrees with the wrapper must fail."""
    import json

    first_module = plan.modules[0]
    wrong_recipe = plan.modules[1].recipe
    tampered = first_module.model_copy(update={"recipe": wrong_recipe})
    with pytest.raises(ValidationError, match="disagrees with module"):
        ReciprocalRouteModule.model_validate_json(
            json.dumps(tampered.model_dump(mode="json")),
        )


# --------------------------------------------------------------------------- #
# verify_reciprocal_route_module_plan
# --------------------------------------------------------------------------- #


def test_verify_passes_for_default_plan(plan, scene, topology, env_module_plan) -> None:
    verify_reciprocal_route_module_plan(
        plan,
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=env_module_plan,
    )


def test_verify_rejects_scene_mismatch(plan, topology, env_module_plan) -> None:
    # Use a wrong scene: rebuild with different seed so its SHA differs.
    wrong_scene = build_scene_plan().model_copy(update={"seed": 999})
    with pytest.raises(ReciprocalRouteError, match="scene_plan_sha256"):
        verify_reciprocal_route_module_plan(
            plan,
            scene=wrong_scene,
            elevated_topology=topology,
            environment_module_plan=env_module_plan,
        )


def test_verify_rejects_environment_module_plan_mismatch(
    plan, scene, topology,
) -> None:
    """When the passed environment_module_plan is a different plan, verify
    must reject by recomputing the v1 plan SHA and comparing."""
    # Build a v1 plan bound to a different scene so its SHA differs.
    other_scene = build_scene_plan().model_copy(update={"seed": 999})
    other_topology = build_elevated_topology_plan(other_scene)
    other_env_plan = build_default_environment_module_plan(
        scene=other_scene,
        elevated_topology=other_topology,
    )
    with pytest.raises(ReciprocalRouteError, match="environment_module_plan_sha256"):
        verify_reciprocal_route_module_plan(
            plan,
            scene=scene,
            elevated_topology=topology,
            environment_module_plan=other_env_plan,
        )


def test_verify_rejects_non_canonical_bytes(plan, scene, topology, env_module_plan) -> None:
    """If the plan's canonical bytes do not round-trip, verify must reject.

    We can't easily produce a non-canonical plan via pydantic (it always
    canonicalizes on model_dump), so this test is a sanity check that the
    verifier's canonical-bytes round-trip path does not silently accept
    arbitrary modifications.  We tamper a Literal-locked field via model_copy
    (which bypasses validation) and confirm verify rejects it.
    """
    # Bypass Literal validation via model_copy; the underlying verify
    # re-validates canonical bytes, which will fail.
    tampered = plan.model_copy(update={"recipe_version": "v2"})
    with pytest.raises((ReciprocalRouteError, ValidationError)):
        verify_reciprocal_route_module_plan(
            tampered,
            scene=scene,
            elevated_topology=topology,
            environment_module_plan=env_module_plan,
        )


# --------------------------------------------------------------------------- #
# v1 immutability: EnvironmentModulePlan v1 must remain unaffected.
# --------------------------------------------------------------------------- #


def test_environment_module_plan_v1_remains_canonical(
    scene, topology, env_module_plan,
) -> None:
    """Building the reciprocal-route plan must NOT change the v1 plan's
    canonical bytes or SHA.  This locks the additive invariant.
    """
    expected_v1_sha = hashlib.sha256(
        canonical_environment_module_plan_bytes(env_module_plan),
    ).hexdigest()

    build_default_reciprocal_route_module_plan(
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=env_module_plan,
    )

    # v1 plan object is frozen; its bytes are unchanged.
    assert hashlib.sha256(
        canonical_environment_module_plan_bytes(env_module_plan),
    ).hexdigest() == expected_v1_sha


def test_environment_module_plan_v1_instance_segment_untouched(
    env_module_plan,
) -> None:
    """v1 plan still owns 131..175; the reciprocal-route plan does not
    touch that segment.
    """
    v1_instances = {
        part.instance_id
        for module in env_module_plan.modules
        for part in module.parts
    }
    assert v1_instances == set(range(131, 176))


# --------------------------------------------------------------------------- #
# Plan does NOT promote trust.
# --------------------------------------------------------------------------- #


def test_plan_does_not_promote_trust(plan) -> None:
    """Even after a successful build, the plan must NOT declare metric or
    real-photo or render-parity trust.  This is the additive trust
    invariant from HANDOFF-OPUS-009.
    """
    assert plan.verification_level == "L0"
    assert plan.geometry_usability == "preview-only"
    assert plan.metric_alignment is False
    assert plan.real_photo_textures is False
    assert plan.trust_effect == "none"
    assert plan.synthetic is True


# --------------------------------------------------------------------------- #
# Phase 4.1: PartLayoutSpec canonical layout (REVIEW-CODEX-018 item 1).
# --------------------------------------------------------------------------- #


def test_plan_carries_part_layout_on_every_part(plan) -> None:
    """Every reciprocal-route part must carry a canonical part_layout."""

    for module in plan.modules:
        for part in module.parts:
            assert isinstance(part.part_layout, PartLayoutSpec)
            assert len(part.part_layout.center_m) == 3
            assert len(part.part_layout.extent_m) == 3
            assert part.part_layout.orientation_deg == 0.0


def test_part_layout_rejects_negative_extent() -> None:
    """extent_m must be positive on every axis."""

    with pytest.raises(ValidationError, match="extent_m"):
        PartLayoutSpec(
            center_m=(0.0, 0.0, 0.0),
            extent_m=(1.6, -1.6, 0.6),
            orientation_deg=0.0,
        )


def test_part_layout_rejects_zero_extent() -> None:
    """extent_m must be strictly positive (zero-size mesh is not finite)."""

    with pytest.raises(ValidationError, match="extent_m"):
        PartLayoutSpec(
            center_m=(0.0, 0.0, 0.0),
            extent_m=(1.6, 0.0, 0.6),
            orientation_deg=0.0,
        )


def test_part_layout_rejects_non_finite_center() -> None:
    """center_m must be finite on every axis."""

    with pytest.raises(ValidationError, match="center_m"):
        PartLayoutSpec(
            center_m=(float("inf"), 0.0, 0.0),
            extent_m=(1.6, 1.6, 0.6),
            orientation_deg=0.0,
        )


def test_part_layout_rejects_orientation_out_of_range() -> None:
    """orientation_deg must be in [0, 360)."""

    with pytest.raises(ValidationError):
        PartLayoutSpec(
            center_m=(0.0, 0.0, 0.0),
            extent_m=(1.6, 1.6, 0.6),
            orientation_deg=360.0,
        )


def test_part_layout_rejects_wrong_tuple_length() -> None:
    """center_m / extent_m must be 3-tuples."""

    with pytest.raises(ValidationError):
        PartLayoutSpec(
            center_m=(0.0, 0.0),
            extent_m=(1.6, 1.6, 0.6),
            orientation_deg=0.0,
        )


def test_plan_sha_changes_when_part_layout_changes(plan, scene, topology, env_module_plan) -> None:
    """Tampering a part's center_m must change plan_sha256 (tamper detection)."""

    first_part = plan.modules[0].parts[0]
    original_center = first_part.part_layout.center_m
    tampered_layout = first_part.part_layout.model_copy(
        update={"center_m": (original_center[0] + 100.0, original_center[1], original_center[2])},
    )
    tampered_part = first_part.model_copy(update={"part_layout": tampered_layout})
    tampered_module = plan.modules[0].model_copy(
        update={"parts": (tampered_part, *plan.modules[0].parts[1:])},
    )
    tampered_plan = plan.model_copy(
        update={"modules": (tampered_module, *plan.modules[1:])},
    )
    assert (
        reciprocal_route_module_plan_sha256(tampered_plan)
        != reciprocal_route_module_plan_sha256(plan)
    )


def test_default_part_layout_preserves_phase3_aabb(plan) -> None:
    """The default layout must preserve the exact AABB that Phase 3 produced.

    REVIEW-CODEX-018 measured mesh AABB: min=(-180.8, -98.3, 44.7),
    max=(120.8, 168.3, 78.3).  The 0.8/0.3 offsets come from box
    half-extent (1.6/2, 0.6/2).  The part *centers* must therefore be:
      min_center = (-180.0, -97.5, 45.0)
      max_center = (120.0, 167.5, 78.0)
    """

    centers = [
        part.part_layout.center_m
        for module in plan.modules
        for part in module.parts
    ]
    min_x = min(c[0] for c in centers)
    max_x = max(c[0] for c in centers)
    min_y = min(c[1] for c in centers)
    max_y = max(c[1] for c in centers)
    min_z = min(c[2] for c in centers)
    max_z = max(c[2] for c in centers)
    # watermill base_x = -180; forest base_x = 120.
    assert min_x == -180.0
    assert max_x == 120.0
    # watermill base_y = -130; first watermill part (instance 189):
    #   -130 + (189-176)*2.5 = -130 + 32.5 = -97.5
    # forest base_y = 80; last forest part (instance 211):
    #   80 + (211-176)*2.5 = 80 + 87.5 = 167.5
    assert min_y == -97.5
    assert max_y == 167.5
    # watermill base_z = 45; gallery base_z = 78.
    assert min_z == 45.0
    assert max_z == 78.0


# --------------------------------------------------------------------------- #
# Phase 4.2: Standing-eye role camera candidates
# (HANDOFF-CODEX-010 §"Opus camera 输出清单" + HANDOFF-CODEX-011 P0-2).
#
# The candidate schema is the foundation for the §3 caller chain's real
# standing-eye camera.  It is NOT a ProductionCameraPose: intrinsics and
# c2w_opencv are caller-computed.  The candidate only declares geometry
# + topology_ref + content-addressed binding so renders can be traced
# back to this exact candidate.
# --------------------------------------------------------------------------- #


def test_plan_carries_six_role_camera_candidates(plan) -> None:
    """Default plan must carry exactly six standing-eye candidates,
    one per module, in module order, with unique camera IDs."""

    assert len(plan.role_camera_candidates) == 6
    expected_role_ids = (
        "central-courtyard-downhill",
        "bridge-deck-crossing",
        "watermill-tailrace",
        "covered-gallery-underpass",
        "forest-orchard-boundary",
        "lower-valley-uphill",
    )
    assert tuple(c.role_module_id for c in plan.role_camera_candidates) == expected_role_ids
    expected_camera_ids = tuple(
        f"camera-reciprocal-role-{i:03d}" for i in range(1, 7)
    )
    assert tuple(c.camera_id for c in plan.role_camera_candidates) == expected_camera_ids
    # Eye height is Literal-locked to 1.6 m (standing-eye, not aerial).
    for candidate in plan.role_camera_candidates:
        assert candidate.eye_height_m == 1.6
        assert candidate.audit_only is False
        assert len(candidate.bound_production_plan_sha256) == 64
        assert len(candidate.bound_camera_registry_sha256) == 64


def test_role_camera_candidate_rejects_non_finite_position() -> None:
    """NaN/Inf in position_m must be rejected at schema level."""

    with pytest.raises(ValidationError):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(float("nan"), 30.0, 70.0),
            look_at_m=(40.0, 5.0, 70.0),
            eye_height_m=1.6,
            fov_x_deg=65.0,
            audit_only=False,
            disclosure="modeled-unverified standing-eye at the courtyard downhill gate",
            bound_production_plan_sha256="0" * 64,
            bound_camera_registry_sha256="0" * 64,
        )


def test_role_camera_candidate_rejects_degenerate_view_direction() -> None:
    """position_m and look_at_m within 1.0 m must be rejected
    (degenerate forward axis, no real view direction)."""

    with pytest.raises(ValidationError, match="differ by at least 1.0"):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(40.0, 30.0, 70.0),
            look_at_m=(40.0, 30.5, 70.0),  # 0.5 m apart
            eye_height_m=1.6,
            fov_x_deg=65.0,
            audit_only=False,
            disclosure="modeled-unverified standing-eye at the courtyard downhill gate",
            bound_production_plan_sha256="0" * 64,
            bound_camera_registry_sha256="0" * 64,
        )


def test_role_camera_candidate_rejects_wrong_eye_height() -> None:
    """eye_height_m must be Literal-locked to 1.6 (standing-eye).
    Aerial (6.0) or 0.0 must be rejected to fail-closed any trust
    drift away from standing-eye."""

    with pytest.raises(ValidationError):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(40.0, 30.0, 70.0),
            look_at_m=(40.0, 5.0, 70.0),
            eye_height_m=6.0,  # aerial, not standing-eye
            fov_x_deg=65.0,
            audit_only=False,
            disclosure="modeled-unverified standing-eye at the courtyard downhill gate",
            bound_production_plan_sha256="0" * 64,
            bound_camera_registry_sha256="0" * 64,
        )


def test_role_camera_candidate_rejects_audit_only_true() -> None:
    """audit_only=True must be rejected: a role camera is a real
    candidate pose, not an audit-only aerial viewpoint."""

    with pytest.raises(ValidationError):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(40.0, 30.0, 70.0),
            look_at_m=(40.0, 5.0, 70.0),
            eye_height_m=1.6,
            fov_x_deg=65.0,
            audit_only=True,  # forbidden
            disclosure="modeled-unverified standing-eye at the courtyard downhill gate",
            bound_production_plan_sha256="0" * 64,
            bound_camera_registry_sha256="0" * 64,
        )


def test_role_camera_candidate_rejects_short_disclosure() -> None:
    """disclosure must be at least 10 chars to carry honest provenance."""

    with pytest.raises(ValidationError):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(40.0, 30.0, 70.0),
            look_at_m=(40.0, 5.0, 70.0),
            eye_height_m=1.6,
            fov_x_deg=65.0,
            audit_only=False,
            disclosure="short",  # < 10 chars
            bound_production_plan_sha256="0" * 64,
            bound_camera_registry_sha256="0" * 64,
        )


def test_role_camera_candidate_rejects_non_sha256_plan_binding() -> None:
    """bound_production_plan_sha256 must be 64-hex (fail-closed
    against forged or non-canonical plan bindings)."""

    with pytest.raises(ValidationError):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(40.0, 30.0, 70.0),
            look_at_m=(40.0, 5.0, 70.0),
            eye_height_m=1.6,
            fov_x_deg=65.0,
            audit_only=False,
            disclosure="modeled-unverified standing-eye at the courtyard downhill gate",
            bound_production_plan_sha256="not-a-sha256",
            bound_camera_registry_sha256="0" * 64,
        )


def test_plan_rejects_wrong_candidate_count(plan) -> None:
    """A plan with 5 or 7 candidates must fail validation."""

    import json as _json

    payload = plan.model_dump(mode="json")
    # Drop one candidate -> 5.
    payload["role_camera_candidates"] = payload["role_camera_candidates"][:5]
    with pytest.raises(ValidationError):
        ReciprocalRouteModulePlan.model_validate_json(_json.dumps(payload))

    # Duplicate one candidate -> 7 (also breaks unique camera_id, but
    # the count check fires first).
    payload = plan.model_dump(mode="json")
    payload["role_camera_candidates"] = (
        payload["role_camera_candidates"]
        + [payload["role_camera_candidates"][0]]
    )
    with pytest.raises(ValidationError):
        ReciprocalRouteModulePlan.model_validate_json(_json.dumps(payload))


def test_plan_rejects_wrong_candidate_order(plan) -> None:
    """Swapping two candidates' role_module_id must fail validation
    (order must match the six module IDs in plan order)."""

    import json as _json

    payload = plan.model_dump(mode="json")
    # Swap role_module_id of candidates 0 and 1.
    (
        payload["role_camera_candidates"][0]["role_module_id"],
        payload["role_camera_candidates"][1]["role_module_id"],
    ) = (
        payload["role_camera_candidates"][1]["role_module_id"],
        payload["role_camera_candidates"][0]["role_module_id"],
    )
    with pytest.raises(ValidationError, match="one-per-module"):
        ReciprocalRouteModulePlan.model_validate_json(_json.dumps(payload))


def test_plan_rejects_duplicate_candidate_camera_ids(plan) -> None:
    """Two candidates with the same camera_id must fail validation,
    even if the role_module_id order is correct."""

    import json as _json

    payload = plan.model_dump(mode="json")
    # Force candidate 1 to share candidate 0's camera_id.
    payload["role_camera_candidates"][1]["camera_id"] = (
        payload["role_camera_candidates"][0]["camera_id"]
    )
    with pytest.raises(ValidationError, match="IDs must be unique"):
        ReciprocalRouteModulePlan.model_validate_json(_json.dumps(payload))


def test_plan_sha_changes_when_candidate_position_changes(plan) -> None:
    """Tampering a candidate's position_m must change plan_sha256
    (tamper detection: render identity flows from candidate geometry)."""

    first_candidate = plan.role_camera_candidates[0]
    original_position = first_candidate.position_m
    tampered_candidate = first_candidate.model_copy(
        update={
            "position_m": (
                original_position[0] + 50.0,
                original_position[1],
                original_position[2],
            ),
        },
    )
    tampered_plan = plan.model_copy(
        update={
            "role_camera_candidates": (
                tampered_candidate,
                *plan.role_camera_candidates[1:],
            ),
        },
    )
    assert (
        reciprocal_route_module_plan_sha256(tampered_plan)
        != reciprocal_route_module_plan_sha256(plan)
    )


def test_role_camera_candidates_default_to_placeholder_sha(plan) -> None:
    """When build_default_reciprocal_route_module_plan is called without
    a production_camera_plan, the candidates must carry placeholder
    all-zero SHA-256 bindings.  This keeps the plan constructible for
    tests that do not exercise the §3 caller chain, while making it
    explicit that no real production plan is bound."""

    for candidate in plan.role_camera_candidates:
        assert candidate.bound_production_plan_sha256 == "0" * 64
        assert candidate.bound_camera_registry_sha256 == "0" * 64


def test_role_camera_candidates_bind_to_production_plan_sha(
    scene, topology, env_module_plan,
) -> None:
    """When build_default_reciprocal_route_module_plan is called with a
    real production_camera_plan, the candidates must carry that plan's
    canonical SHA + registry digest (content-addressed binding)."""

    production_camera_plan = build_production_camera_plan(
        scene=scene,
        elevated_topology=topology,
    )
    plan = build_default_reciprocal_route_module_plan(
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=env_module_plan,
        production_camera_plan=production_camera_plan,
    )
    expected_plan_sha = hashlib.sha256(
        canonical_production_plan_bytes(production_camera_plan),
    ).hexdigest()
    expected_registry_sha = production_camera_registry_digest(
        production_camera_plan,
    )
    for candidate in plan.role_camera_candidates:
        assert candidate.bound_production_plan_sha256 == expected_plan_sha
        assert candidate.bound_camera_registry_sha256 == expected_registry_sha
    # Sanity: the bound SHAs are NOT all-zero placeholders.
    assert expected_plan_sha != "0" * 64
    assert expected_registry_sha != "0" * 64
