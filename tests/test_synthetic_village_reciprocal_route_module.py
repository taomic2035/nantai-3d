"""Reciprocal-route module plan tests (HANDOFF-OPUS-009).

These tests lock the canonical bytes, content addressing, instance
segment partition, module ordering, and v1-immutability of the
``ReciprocalRouteModulePlan``.  They do NOT exercise Blender runtime or
promote modeled-unverified trust.
"""

from __future__ import annotations

import hashlib
import json
import math

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
    _CENTRAL_CONTOUR_Y_M,
    _CENTRAL_FLOOR_CLEARANCE_M,
    BATCH8_ARCHIVE_SHA256,
    BATCH8_RELEASE_MANIFEST_SHA256,
    BATCH9_ARCHIVE_SHA256,
    BATCH9_RELEASE_MANIFEST_SHA256,
    BRIDGE_CROSSING_INSTANCE_RANGE,
    CENTRAL_DOWNHILL_INSTANCE_RANGE,
    FOREST_BOUNDARY_INSTANCE_RANGE,
    GALLERY_UNDERPASS_INSTANCE_RANGE,
    LOWER_VALLEY_UPHILL_INSTANCE_RANGE,
    MIN_ROUTE_CLEARANCE_M,
    RECIPROCAL_ROLE_TARGET_GROUP_IDS,
    RECIPROCAL_ROUTE_MODULE_ORDER,
    RECIPROCAL_ROUTE_RECIPE_VERSION,
    RECIPROCAL_ROUTE_SCHEMA,
    REPLACEMENT_OBSTRUCTED_CAMERA_IDS,
    ROLE_CAMERA_APPROACH_OFFSET_M,
    ROLE_CAMERA_LOOKAHEAD_M,
    ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M,
    WATERMILL_TAILRACE_INSTANCE_RANGE,
    PartLayoutSpec,
    ReciprocalRoleCameraCandidate,
    ReciprocalRouteError,
    ReciprocalRouteModule,
    ReciprocalRouteModulePart,
    ReciprocalRouteModulePlan,
    WalkableNodeBinding,
    build_default_reciprocal_route_module_plan,
    build_ground_route_replacement_candidate,
    canonical_reciprocal_route_module_plan_bytes,
    materialize_reciprocal_role_candidate,
    reciprocal_route_module_plan_sha256,
    verify_reciprocal_route_module_plan,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan, terrain_height_m


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
            geometry_family=first_part.geometry_family,
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
            expected_orientation = (
                270.0 if module.module_id == "central-courtyard-downhill" else 0.0
            )
            assert part.part_layout.orientation_deg == expected_orientation


def test_plan_carries_explicit_geometry_family_on_every_part(plan) -> None:
    """Runtime geometry classification is canonical plan data, not a name guess."""

    parts = [part for module in plan.modules for part in module.parts]
    assert len(parts) == 43
    assert all(part.geometry_family for part in parts)
    assert {part.geometry_family for part in parts} == {
        "open-path",
        "covered-passage",
        "bridge-deck",
        "building-shell",
        "structural-frame",
        "drainage-channel",
        "retaining-structure",
        "guard-rail",
        "service-prop",
        "vegetation-band",
    }


def test_part_rejects_missing_geometry_family(plan) -> None:
    """A legacy or tampered part cannot fall back to a universal primitive."""

    payload = plan.modules[0].parts[0].model_dump(mode="json")
    payload.pop("geometry_family")
    with pytest.raises(ValidationError, match="geometry_family"):
        ReciprocalRouteModulePart.model_validate(payload)


def test_part_rejects_semantically_incompatible_geometry_family(plan) -> None:
    """A path root cannot silently label a roofed building mesh as walkable path."""

    payload = plan.modules[0].parts[0].model_dump(mode="json")
    assert payload["semantic_id"] != 3
    payload["geometry_family"] = "covered-passage"
    with pytest.raises(ValidationError, match="geometry family.*semantic"):
        ReciprocalRouteModulePart.model_validate_json(json.dumps(payload))


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

    Phase 4.3 amendments (FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md
    §"待处理" item 3) lift bridge z 50 -> 55 and watermill z 45 -> 52
    so the modules no longer intersect aux-terrain.  The min_z is
    therefore 52.0 (was 45.0 in Phase 3).  All other AABB corners
    remain identical because xy layouts are unchanged.

    REVIEW-CODEX-018 measured the Phase 3 mesh AABB:
      min=(-180.8, -98.3, 44.7), max=(120.8, 168.3, 78.3).
    The 0.8/0.3 offsets come from box half-extent (1.6/2, 0.6/2).  The
    part *centers* for Phase 4.3 are therefore:
      min_center = (-180.0, -97.5, 52.0)
      max_center = (120.0, 167.5, 78.0)
    The mesh AABB itself changes because extent_m.z grew from 0.6 to
    2.5 (Phase 4.3 item 1: 5-panel passage geometry).  That change is
    asserted separately in
    ``test_module_geometry_emits_five_panel_passage``.
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
    # forest base_y = 30 (Phase 4.5.2 relocated from 80 to bring
    # candidates within 30 m of upper-ground-west); last forest part
    # (instance 211): 30 + (211-176)*2.5 = 30 + 87.5 = 117.5
    assert min_y == -97.5
    assert max_y == 117.5
    # Phase 4.3: watermill base_z lifted 45 -> 52 to clear aux-terrain
    # peak ~48.64 m at the watermill's y range.  gallery base_z = 78
    # is unchanged.
    assert min_z == 52.0
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


def test_role_candidates_follow_built_module_floor_and_route_direction(plan) -> None:
    """Candidate geometry must be derived from the same module parts it audits.

    This prevents module-layout changes from leaving cameras above, below, or
    behind the modeled passage while still satisfying schema-only checks.
    """

    modules = {module.module_id: module for module in plan.modules}
    for candidate in plan.role_camera_candidates:
        parts = sorted(
            modules[candidate.role_module_id].parts,
            key=lambda part: part.instance_id,
        )
        first = parts[0].part_layout.center_m
        last = parts[-1].part_layout.center_m
        route = tuple(last[axis] - first[axis] for axis in range(3))
        route_length = math.dist(first, last)
        assert route_length > 0.0
        direction = tuple(value / route_length for value in route)
        expected_position = (
            first[0] - direction[0] * ROLE_CAMERA_APPROACH_OFFSET_M,
            first[1] - direction[1] * ROLE_CAMERA_APPROACH_OFFSET_M,
            first[2] + candidate.eye_height_m,
        )
        expected_look_at = tuple(
            expected_position[axis] + direction[axis] * ROLE_CAMERA_LOOKAHEAD_M
            for axis in range(3)
        )

        assert candidate.position_m == pytest.approx(expected_position)
        assert candidate.look_at_m == pytest.approx(expected_look_at)


def test_central_route_uses_free_contour_above_terrain(plan, topology) -> None:
    """The canary route must not duplicate the existing courtyard module.

    It starts on the free y=40 contour near ``central-ground-east`` and each
    flat floor starts 0.5 m above the analytic terrain (about 0.1 m above
    the Blender terrain mesh) and remains above the descending contour.
    This remains a
    modeled-unverified placement; the Blender probe owns collision evidence.
    """

    module = next(
        row for row in plan.modules if row.module_id == "central-courtyard-downhill"
    )
    parts = sorted(module.parts, key=lambda part: part.instance_id)
    assert parts[0].part_layout.center_m[:2] == (30.0, 40.0)
    assert all(part.part_layout.orientation_deg == 270.0 for part in parts)
    assert all(part.part_layout.center_m[1] == 40.0 for part in parts)
    expected_floor_z = round(terrain_height_m(30.0, 40.0) + 0.5, 3)
    assert all(part.part_layout.center_m[2] == expected_floor_z for part in parts)
    for part in parts:
        x, y, z = part.part_layout.center_m
        assert z >= round(terrain_height_m(x, y) + 0.5, 3)

    node = next(
        row for row in topology.nodes if row.node_id == "central-ground-east"
    )
    candidate = next(
        row
        for row in plan.role_camera_candidates
        if row.role_module_id == "central-courtyard-downhill"
    )
    assert math.dist(candidate.position_m, node.position_m) < 30.0


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


# --------------------------------------------------------------------------- #
# Phase 4.4 (P0-2 item 1): WalkableNodeBinding schema upgrade.
# --------------------------------------------------------------------------- #


def test_walkable_node_binding_accepts_valid_node() -> None:
    """A WalkableNodeBinding with a valid node_id (matching
    ``^[a-z0-9]+(?:-[a-z0-9]+)*$``), finite position, and Literal-locked
    level must be accepted."""

    binding = WalkableNodeBinding(
        node_id="central-ground-east",
        node_position_m=(30.0, 27.0, 71.0),
        level="ground",
        ground_route_ref="path-network-003",
    )
    assert binding.node_id == "central-ground-east"
    assert binding.node_position_m == (30.0, 27.0, 71.0)
    assert binding.level == "ground"


def test_walkable_node_binding_rejects_invalid_node_id_pattern() -> None:
    """node_id must match ``^[a-z0-9]+(?:-[a-z0-9]+)*$`` (no underscores,
    no uppercase, no leading dash)."""

    with pytest.raises(ValidationError):
        WalkableNodeBinding(
            node_id="Central_Ground_East",  # uppercase + underscore
            node_position_m=(30.0, 27.0, 71.0),
            level="ground",
            ground_route_ref="path-network-003",
        )


def test_walkable_node_binding_rejects_unknown_level() -> None:
    """level must be Literal-locked to ``"ground"`` or ``"elevated"``;
    other values (e.g. ``"aerial"``) must be rejected."""

    with pytest.raises(ValidationError):
        WalkableNodeBinding(
            node_id="central-ground-east",
            node_position_m=(30.0, 27.0, 71.0),
            level="aerial",  # not in Literal
            ground_route_ref="path-network-003",
        )


def test_candidate_defaults_to_populated_walkable_node_binding(plan) -> None:
    """REVIEW-CODEX-021: default candidates must have ``bound_walkable_node``
    populated (not None) — the nearest ground node whose
    ``ground_route_ref`` matches the candidate's ``topology_ref`` is
    deterministically selected at plan construction time.  This replaces
    the Phase 4.2 behaviour where ``bound_walkable_node`` was None by
    default and the caller was expected to fill it later."""

    for candidate in plan.role_camera_candidates:
        assert candidate.bound_walkable_node is not None
        assert candidate.bound_walkable_node.ground_route_ref == candidate.topology_ref


def test_candidate_accepts_walkable_node_binding_within_distance() -> None:
    """When bound_walkable_node is populated and the candidate's
    position_m is within ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M of
    the node, the candidate must be accepted."""

    # Candidate at (40, 30, 71) bound to node at (30, 27, 71):
    # 3D distance = sqrt(100 + 9 + 0) = ~10.44 m, well under 30 m.
    candidate = ReciprocalRoleCameraCandidate(
        role_module_id="central-courtyard-downhill",
        camera_id="camera-reciprocal-role-001",
        topology_ref="path-network-003",
        arc_length_m=None,
        position_m=(40.0, 30.0, 71.0),
        look_at_m=(40.0, 5.0, 70.0),
        eye_height_m=1.6,
        fov_x_deg=65.0,
        audit_only=False,
        disclosure="modeled-unverified standing-eye at the courtyard downhill gate",
        bound_production_plan_sha256="0" * 64,
        bound_camera_registry_sha256="0" * 64,
        bound_walkable_node=WalkableNodeBinding(
            node_id="central-ground-east",
            node_position_m=(30.0, 27.0, 71.0),
            level="ground",
            ground_route_ref="path-network-003",
        ),
    )
    assert candidate.bound_walkable_node is not None
    assert candidate.bound_walkable_node.node_id == "central-ground-east"


def test_candidate_rejects_walkable_node_beyond_max_distance() -> None:
    """When bound_walkable_node is populated but the candidate's
    position_m is beyond ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M of
    the node, the candidate must be rejected (a candidate claiming to
    bind a node it is clearly not near)."""

    # Candidate at (40, 30, 71) bound to node at (300, 300, 71):
    # 3D distance ~370 m, far over 30 m threshold.
    with pytest.raises(ValidationError, match="ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M"):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(40.0, 30.0, 71.0),
            look_at_m=(40.0, 5.0, 70.0),
            eye_height_m=1.6,
            fov_x_deg=65.0,
            audit_only=False,
            disclosure="modeled-unverified standing-eye at the courtyard downhill gate",
            bound_production_plan_sha256="0" * 64,
            bound_camera_registry_sha256="0" * 64,
            bound_walkable_node=WalkableNodeBinding(
                node_id="central-ground-east",
                node_position_m=(300.0, 300.0, 71.0),
                level="ground",
                ground_route_ref="path-network-003",
            ),
        )


def test_plan_sha_changes_when_walkable_node_binding_changes(plan) -> None:
    """Populating bound_walkable_node on any candidate must change
    plan_sha256 (content-addressed binding: any field change alters
    the plan SHA so render identity flows from the binding)."""

    first_candidate = plan.role_camera_candidates[0]
    bound_candidate = first_candidate.model_copy(
        update={
            "bound_walkable_node": WalkableNodeBinding(
                node_id="central-ground-east",
                node_position_m=(30.0, 27.0, first_candidate.position_m[2]),
                level="ground",
                ground_route_ref="path-network-003",
            ),
        },
    )
    bound_plan = plan.model_copy(
        update={
            "role_camera_candidates": (
                bound_candidate,
                *plan.role_camera_candidates[1:],
            ),
        },
    )
    assert (
        reciprocal_route_module_plan_sha256(bound_plan)
        != reciprocal_route_module_plan_sha256(plan)
    )


def test_walkable_node_max_distance_constant_is_locked() -> None:
    """ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M must equal
    ROLE_CAMERA_LOOKAHEAD_M + 5.0 = 30.0 m.  Locking the constant
    prevents silent drift in the geometry gate."""

    assert ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M == 30.0


# --------------------------------------------------------------------------- #
# REVIEW-CODEX-021: six-role topology binding false-green fix
# --------------------------------------------------------------------------- #


def test_all_six_role_candidates_bind_same_ref_ground_node_within_30m(plan, topology) -> None:
    """REVIEW-CODEX-021 §1: each candidate's ``topology_ref`` must equal
    its ``bound_walkable_node.ground_route_ref``, and the 3D distance
    must be ≤ 30 m.  This is the core fix — three roles previously bound
    to path networks with no ground node within 30 m."""

    import math as _math

    ground_nodes = {n.node_id: n for n in topology.nodes if n.level == "ground"}
    for candidate in plan.role_camera_candidates:
        binding = candidate.bound_walkable_node
        assert binding is not None, candidate.role_module_id
        assert binding.ground_route_ref == candidate.topology_ref, (
            f"{candidate.role_module_id}: topology_ref={candidate.topology_ref!r} "
            f"!= ground_route_ref={binding.ground_route_ref!r}"
        )
        assert binding.node_id in ground_nodes, (
            f"{candidate.role_module_id}: bound node {binding.node_id} not in topology"
        )
        node = ground_nodes[binding.node_id]
        assert node.ground_route_ref == candidate.topology_ref
        distance = _math.dist(candidate.position_m, binding.node_position_m)
        assert distance <= ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M, (
            f"{candidate.role_module_id}: distance {distance:.3f} m > "
            f"{ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M} m"
        )
        assert binding.node_position_m == node.position_m


def test_candidate_rejects_topology_ref_mismatch_with_bound_node() -> None:
    """REVIEW-CODEX-021: candidate's ``topology_ref`` must equal
    ``bound_walkable_node.ground_route_ref`` — a candidate claiming to
    be on path-network-003 but binding a node on path-network-001 is
    a false-green binding."""

    with pytest.raises(ValidationError, match="does not match"):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(40.0, 30.0, 71.0),
            look_at_m=(40.0, 5.0, 70.0),
            eye_height_m=1.6,
            fov_x_deg=65.0,
            audit_only=False,
            disclosure="modeled-unverified standing-eye test candidate",
            bound_production_plan_sha256="0" * 64,
            bound_camera_registry_sha256="0" * 64,
            bound_walkable_node=WalkableNodeBinding(
                node_id="bridge-ground-east",
                node_position_m=(-180.0, -90.0, 47.3),
                level="ground",
                ground_route_ref="path-network-001",
            ),
        )


def test_tamper_bound_node_id_rejected(plan) -> None:
    """REVIEW-CODEX-021: changing ``bound_walkable_node.node_id`` to a
    non-existent node must change the plan SHA (content-addressed)."""

    first = plan.role_camera_candidates[0]
    tampered = first.model_copy(
        update={
            "bound_walkable_node": first.bound_walkable_node.model_copy(
                update={"node_id": "fake-node-id"},
            ),
        },
    )
    tampered_plan = plan.model_copy(
        update={"role_camera_candidates": (tampered, *plan.role_camera_candidates[1:])},
    )
    assert (
        reciprocal_route_module_plan_sha256(tampered_plan)
        != reciprocal_route_module_plan_sha256(plan)
    )


def test_tamper_bound_node_position_rejected(plan) -> None:
    """REVIEW-CODEX-021: changing ``bound_walkable_node.node_position_m``
    must change the plan SHA."""

    first = plan.role_camera_candidates[0]
    tampered = first.model_copy(
        update={
            "bound_walkable_node": first.bound_walkable_node.model_copy(
                update={"node_position_m": (31.0, 28.0, 75.0)},
            ),
        },
    )
    tampered_plan = plan.model_copy(
        update={"role_camera_candidates": (tampered, *plan.role_camera_candidates[1:])},
    )
    assert (
        reciprocal_route_module_plan_sha256(tampered_plan)
        != reciprocal_route_module_plan_sha256(plan)
    )


def test_tamper_bound_node_route_rejected(plan) -> None:
    """REVIEW-CODEX-021: changing ``bound_walkable_node.ground_route_ref``
    to a value that mismatches the candidate's ``topology_ref`` must
    fail validation (false-green binding prevention)."""

    first = plan.role_camera_candidates[0]
    tampered_data = first.model_dump(mode="python")
    tampered_data["bound_walkable_node"]["ground_route_ref"] = "path-network-001"
    with pytest.raises(ValidationError, match="does not match"):
        ReciprocalRoleCameraCandidate.model_validate(tampered_data)


def test_tamper_bound_node_level_rejected(plan) -> None:
    """REVIEW-CODEX-021: changing ``bound_walkable_node.level`` to
    ``"elevated"`` must change the plan SHA (content-addressed)."""

    first = plan.role_camera_candidates[0]
    tampered = first.model_copy(
        update={
            "bound_walkable_node": first.bound_walkable_node.model_copy(
                update={"level": "elevated"},
            ),
        },
    )
    tampered_plan = plan.model_copy(
        update={"role_camera_candidates": (tampered, *plan.role_camera_candidates[1:])},
    )
    assert (
        reciprocal_route_module_plan_sha256(tampered_plan)
        != reciprocal_route_module_plan_sha256(plan)
    )


def test_production_and_reciprocal_plan_canonical_bytes_deterministic(
    scene, topology, env_module_plan,
) -> None:
    """REVIEW-CODEX-021: building the plan twice must produce identical
    canonical bytes (deterministic construction)."""

    prod_plan = build_production_camera_plan()
    first = build_default_reciprocal_route_module_plan(
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=env_module_plan,
        production_camera_plan=prod_plan,
    )
    second = build_default_reciprocal_route_module_plan(
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=env_module_plan,
        production_camera_plan=prod_plan,
    )
    assert (
        canonical_reciprocal_route_module_plan_bytes(first)
        == canonical_reciprocal_route_module_plan_bytes(second)
    )
    assert (
        reciprocal_route_module_plan_sha256(first)
        == reciprocal_route_module_plan_sha256(second)
    )


# --------------------------------------------------------------------------- #
# Phase 4.4 (P0-2 item 2): materialize_reciprocal_role_candidate
# --------------------------------------------------------------------------- #


def test_reciprocal_role_target_group_ids_constant_is_locked() -> None:
    """RECIPROCAL_ROLE_TARGET_GROUP_IDS must be exactly the four
    standing-eye-compatible groups, excluding ``audit-overview`` (which
    is an aerial overview group, not a pedestrian viewpoint).  Locking
    the set prevents silent admission of audit-overview."""

    assert RECIPROCAL_ROLE_TARGET_GROUP_IDS == frozenset(
        {"ground-route", "elevated-pedestrian", "perimeter-inward", "environment-corridor"}
    )
    assert "audit-overview" not in RECIPROCAL_ROLE_TARGET_GROUP_IDS


def test_materialize_reciprocal_role_candidate_produces_valid_ground_route_pose(
    plan,
) -> None:
    """Materializing a candidate to ``ground-route`` produces a
    ``ProductionCameraPose`` whose placement, disclosure, topology_ref,
    eye_height, and FOV come from the candidate, whose ``audit_only``
    is ``False`` (locked by candidate schema + non-audit group), and
    whose ``intrinsics`` + ``c2w_opencv`` are computed by the same
    private ``_pose`` helper used by the 180-camera plan."""

    candidate = plan.role_camera_candidates[0]
    pose = materialize_reciprocal_role_candidate(
        candidate,
        target_group_id="ground-route",
        target_sequence_index=180,
        target_camera_id="camera-ground-route-180",
    )
    assert pose.camera_id == "camera-ground-route-180"
    assert pose.group_id == "ground-route"
    assert pose.sequence_index == 180
    assert pose.topology_ref == candidate.topology_ref
    assert pose.disclosure == candidate.disclosure
    assert pose.eye_height_m == candidate.eye_height_m
    assert pose.fov_x_deg == candidate.fov_x_deg
    assert pose.audit_only is False
    # Position is quantized to 3 decimals via _q3 (matches 180-camera plan).
    for axis_idx in range(3):
        assert round(pose.position_m[axis_idx], 3) == pose.position_m[axis_idx]
        assert round(pose.look_at_m[axis_idx], 3) == pose.look_at_m[axis_idx]
    # Intrinsics + c2w_opencv are finite and non-identity (computed by
    # _look_at_c2w + _intrinsics).  c2w_opencv is a 4x4 finite matrix.
    assert len(pose.c2w_opencv) == 4
    assert all(len(row) == 4 for row in pose.c2w_opencv)
    # The pose's arc_length_m is carried through (None for default candidates).
    assert pose.arc_length_m is None


@pytest.mark.parametrize(
    "target_group_id,target_camera_id",
    [
        ("elevated-pedestrian", "camera-elevated-pedestrian-180"),
        ("perimeter-inward", "camera-perimeter-inward-180"),
        ("environment-corridor", "camera-environment-corridor-180"),
    ],
)
def test_materialize_reciprocal_role_candidate_accepts_all_non_audit_groups(
    plan,
    target_group_id,
    target_camera_id,
) -> None:
    """The helper accepts every group in
    ``RECIPROCAL_ROLE_TARGET_GROUP_IDS`` (every standing-eye-compatible
    group) and produces a pose whose ``group_id`` matches the request."""

    candidate = plan.role_camera_candidates[0]
    pose = materialize_reciprocal_role_candidate(
        candidate,
        target_group_id=target_group_id,
        target_sequence_index=180,
        target_camera_id=target_camera_id,
    )
    assert pose.group_id == target_group_id
    assert pose.camera_id == target_camera_id
    assert pose.audit_only is False


def test_materialize_reciprocal_role_candidate_rejects_audit_overview_group(
    plan,
) -> None:
    """``audit-overview`` is an aerial overview group (altitude ~190 m),
    not a pedestrian viewpoint (1.6 m).  Materializing a standing-eye
    candidate as audit-overview would silently lie about the viewpoint,
    so the helper must fail-closed with ``ReciprocalRouteError``."""

    candidate = plan.role_camera_candidates[0]
    with pytest.raises(ReciprocalRouteError, match="audit-overview"):
        materialize_reciprocal_role_candidate(
            candidate,
            target_group_id="audit-overview",
            target_sequence_index=180,
            target_camera_id="camera-audit-overview-180",
        )


def test_materialize_reciprocal_role_candidate_rejects_invalid_target_camera_id_pattern(
    plan,
) -> None:
    """The ``target_camera_id`` must match the ``ProductionCameraPose``
    camera_id regex.  Passing a candidate-style id
    (``camera-reciprocal-role-001``) must be rejected by the
    ``ProductionCameraPose`` validator inside ``_pose``."""

    candidate = plan.role_camera_candidates[0]
    with pytest.raises(ValidationError):
        materialize_reciprocal_role_candidate(
            candidate,
            target_group_id="ground-route",
            target_sequence_index=180,
            target_camera_id="camera-reciprocal-role-001",
        )


@pytest.mark.parametrize("bad_sequence_index", [0, 181, -1, 200])
def test_materialize_reciprocal_role_candidate_rejects_sequence_index_out_of_range(
    plan,
    bad_sequence_index,
) -> None:
    """``sequence_index`` must be in ``[1, 180]`` (the production camera
    count).  Out-of-range values must be rejected by the
    ``ProductionCameraPose`` validator inside ``_pose``."""

    candidate = plan.role_camera_candidates[0]
    with pytest.raises(ValidationError):
        materialize_reciprocal_role_candidate(
            candidate,
            target_group_id="ground-route",
            target_sequence_index=bad_sequence_index,
            target_camera_id="camera-ground-route-180",
        )


def test_materialize_reciprocal_role_candidate_quantizes_position_to_3_decimals(
    plan,
) -> None:
    """The materialized pose's ``position_m`` + ``look_at_m`` must be
    quantized to 3 decimals via ``_q3`` (same as the 180-camera plan).
    The default plan's candidates are built from
    ``terrain_height_m`` whose values may have >3 decimal places; the
    materialization must round them so the pose is byte-stable across
    processes."""

    from pipeline.synthetic_village.camera_plan import _q3

    candidate = plan.role_camera_candidates[0]
    pose = materialize_reciprocal_role_candidate(
        candidate,
        target_group_id="ground-route",
        target_sequence_index=180,
        target_camera_id="camera-ground-route-180",
    )
    expected_position = tuple(_q3(v) for v in candidate.position_m)
    expected_look_at = tuple(_q3(v) for v in candidate.look_at_m)
    assert pose.position_m == expected_position
    assert pose.look_at_m == expected_look_at


def test_materialize_reciprocal_role_candidate_computes_intrinsics_and_c2w_consistently(
    plan,
) -> None:
    """The materialized pose's ``intrinsics`` + ``c2w_opencv`` must be
    computed by the same private ``_intrinsics`` + ``_look_at_c2w``
    helpers used by the 180-camera plan, so the materialized pose is
    byte-identical to what the plan would have produced for the same
    placement."""

    import numpy as np

    from pipeline.synthetic_village.camera_plan import _intrinsics, _look_at_c2w, _q3

    candidate = plan.role_camera_candidates[0]
    pose = materialize_reciprocal_role_candidate(
        candidate,
        target_group_id="ground-route",
        target_sequence_index=180,
        target_camera_id="camera-ground-route-180",
    )
    # Intrinsics: fx == fy == _intrinsics(fov_x_deg).fx (the helper builds
    # a square-focal intrinsics from FOV).
    expected_intrinsics = _intrinsics(candidate.fov_x_deg)
    assert pose.intrinsics.fx == expected_intrinsics.fx
    assert pose.intrinsics.fy == expected_intrinsics.fy
    # c2w_opencv: matches _look_at_c2w(quantized_position, quantized_look_at).
    position_q = np.array(
        tuple(_q3(v) for v in candidate.position_m), dtype=float,
    )
    look_q = np.array(
        tuple(_q3(v) for v in candidate.look_at_m), dtype=float,
    )
    expected_c2w = _look_at_c2w(position_q, look_q)
    assert pose.c2w_opencv == expected_c2w


# --------------------------------------------------------------------------- #
# Phase 4.4 (P0-2 item 3): build_ground_route_replacement_candidate
# --------------------------------------------------------------------------- #


def test_replacement_obstructed_camera_ids_constant_is_locked() -> None:
    """``REPLACEMENT_OBSTRUCTED_CAMERA_IDS`` must be exactly the two
    cameras rejected by the 180-camera clearance audit
    (REVIEW-CODEX-011).  Locking the set prevents silent admission of
    cameras that were not actually rejected."""

    assert REPLACEMENT_OBSTRUCTED_CAMERA_IDS == frozenset(
        {"camera-ground-route-010", "camera-ground-route-039"}
    )


def test_min_route_clearance_m_constant_is_locked() -> None:
    """``MIN_ROUTE_CLEARANCE_M`` must equal 2.4 m, matching the probe's
    threshold.  Locking the constant prevents silent drift in the
    clearance gate."""

    assert MIN_ROUTE_CLEARANCE_M == 2.4


def test_build_replacement_candidate_produces_valid_candidate(
    plan, topology,
) -> None:
    """Building a replacement candidate with valid inputs produces a
    ``ReciprocalRoleCameraCandidate`` whose ``position_m`` is the bound
    walkable node's ground position + standing-eye height, whose
    ``camera_id`` maps to the module's role index, and whose
    ``bound_walkable_node`` is populated."""

    ground_node = next(
        node for node in topology.nodes
        if node.level == "ground" and node.node_id == "central-ground-east"
    )
    binding = WalkableNodeBinding(
        node_id=ground_node.node_id,
        node_position_m=ground_node.position_m,
        level="ground",
        ground_route_ref="path-network-003",
    )
    look_at = (
        ground_node.position_m[0],
        ground_node.position_m[1] + 25.0,
        ground_node.position_m[2] + 1.6,
    )
    candidate = build_ground_route_replacement_candidate(
        obstructed_camera_id="camera-ground-route-010",
        role_module_id="central-courtyard-downhill",
        topology_ref="path-network-003",
        bound_walkable_node=binding,
        look_at_m=look_at,
        bound_production_plan_sha256="a" * 64,
        bound_camera_registry_sha256="b" * 64,
        probe_clearance_min_m=2.475,
        disclosure=(
            "modeled-unverified replacement for camera-ground-route-010 on "
            "central-courtyard-downhill passage; fresh preflight required"
        ),
    )
    # position_m = node_position + (0, 0, 1.6)
    assert candidate.position_m == (
        ground_node.position_m[0],
        ground_node.position_m[1],
        ground_node.position_m[2] + 1.6,
    )
    # camera_id maps to role index 1 (central-courtyard-downhill is first)
    assert candidate.camera_id == "camera-reciprocal-role-001"
    # bound_walkable_node is populated
    assert candidate.bound_walkable_node is not None
    assert candidate.bound_walkable_node.node_id == "central-ground-east"
    # role_module_id, topology_ref, disclosure carried verbatim
    assert candidate.role_module_id == "central-courtyard-downhill"
    assert candidate.topology_ref == "path-network-003"
    assert candidate.fov_x_deg == 65.0
    assert candidate.eye_height_m == 1.6
    assert candidate.audit_only is False


@pytest.mark.parametrize("obstructed_id", ["camera-ground-route-010", "camera-ground-route-039"])
def test_build_replacement_candidate_accepts_both_obstructed_ids(
    plan, topology, obstructed_id,
) -> None:
    """Both obstructed camera ids from the clearance audit are accepted."""

    ground_node = next(
        node for node in topology.nodes
        if node.level == "ground" and node.node_id == "central-ground-east"
    )
    binding = WalkableNodeBinding(
        node_id=ground_node.node_id,
        node_position_m=ground_node.position_m,
        level="ground",
        ground_route_ref="path-network-003",
    )
    look_at = (
        ground_node.position_m[0],
        ground_node.position_m[1] + 25.0,
        ground_node.position_m[2] + 1.6,
    )
    candidate = build_ground_route_replacement_candidate(
        obstructed_camera_id=obstructed_id,
        role_module_id="central-courtyard-downhill",
        topology_ref="path-network-003",
        bound_walkable_node=binding,
        look_at_m=look_at,
        bound_production_plan_sha256="a" * 64,
        bound_camera_registry_sha256="b" * 64,
        probe_clearance_min_m=2.475,
        disclosure=(
            f"modeled-unverified replacement for {obstructed_id}; "
            f"fresh preflight required"
        ),
    )
    assert candidate.bound_walkable_node is not None


def test_build_replacement_candidate_rejects_unknown_obstructed_camera_id(
    topology,
) -> None:
    """An obstructed_camera_id not in
    ``REPLACEMENT_OBSTRUCTED_CAMERA_IDS`` must be rejected -- we will
    not search a replacement for a camera that was not actually
    rejected by the clearance audit."""

    ground_node = next(
        node for node in topology.nodes if node.level == "ground"
    )
    binding = WalkableNodeBinding(
        node_id=ground_node.node_id,
        node_position_m=ground_node.position_m,
        level="ground",
        ground_route_ref="path-network-001",
    )
    look_at = (
        ground_node.position_m[0],
        ground_node.position_m[1] + 25.0,
        ground_node.position_m[2] + 1.6,
    )
    with pytest.raises(ReciprocalRouteError, match="reposeable obstructed"):
        build_ground_route_replacement_candidate(
            obstructed_camera_id="camera-ground-route-099",
            role_module_id="central-courtyard-downhill",
            topology_ref="path-network-003",
            bound_walkable_node=binding,
            look_at_m=look_at,
            bound_production_plan_sha256="a" * 64,
            bound_camera_registry_sha256="b" * 64,
            probe_clearance_min_m=2.475,
            disclosure="modeled-unverified replacement; fresh preflight required",
        )


def test_build_replacement_candidate_rejects_insufficient_clearance(
    topology,
) -> None:
    """``probe_clearance_min_m < MIN_ROUTE_CLEARANCE_M`` (2.4 m) must be
    rejected -- a passage with insufficient clearance cannot host a
    standing-eye candidate."""

    ground_node = next(
        node for node in topology.nodes if node.level == "ground"
    )
    binding = WalkableNodeBinding(
        node_id=ground_node.node_id,
        node_position_m=ground_node.position_m,
        level="ground",
        ground_route_ref="path-network-001",
    )
    look_at = (
        ground_node.position_m[0],
        ground_node.position_m[1] + 25.0,
        ground_node.position_m[2] + 1.6,
    )
    with pytest.raises(ReciprocalRouteError, match="insufficient clearance"):
        build_ground_route_replacement_candidate(
            obstructed_camera_id="camera-ground-route-010",
            role_module_id="central-courtyard-downhill",
            topology_ref="path-network-003",
            bound_walkable_node=binding,
            look_at_m=look_at,
            bound_production_plan_sha256="a" * 64,
            bound_camera_registry_sha256="b" * 64,
            probe_clearance_min_m=2.0,
            disclosure="modeled-unverified replacement; fresh preflight required",
        )


def test_build_replacement_candidate_rejects_elevated_walkable_node(
    topology,
) -> None:
    """An elevated walkable node must be rejected -- ground-route
    replacements require a ground-level node (standing-eye on a path
    network), not an elevated walkway."""

    elevated_node = next(
        node for node in topology.nodes if node.level == "elevated"
    )
    binding = WalkableNodeBinding(
        node_id=elevated_node.node_id,
        node_position_m=elevated_node.position_m,
        level="elevated",
        ground_route_ref="path-network-001",
    )
    look_at = (
        elevated_node.position_m[0],
        elevated_node.position_m[1] + 25.0,
        elevated_node.position_m[2],
    )
    with pytest.raises(ReciprocalRouteError, match="ground-level walkable node"):
        build_ground_route_replacement_candidate(
            obstructed_camera_id="camera-ground-route-010",
            role_module_id="central-courtyard-downhill",
            topology_ref="path-network-003",
            bound_walkable_node=binding,
            look_at_m=look_at,
            bound_production_plan_sha256="a" * 64,
            bound_camera_registry_sha256="b" * 64,
            probe_clearance_min_m=2.475,
            disclosure="modeled-unverified replacement; fresh preflight required",
        )


def test_build_replacement_candidate_can_be_materialized_to_target_pose(
    plan, topology,
) -> None:
    """The replacement candidate can be materialized via
    ``materialize_reciprocal_role_candidate`` to a ``ProductionCameraPose``
    whose ``camera_id`` is the obstructed camera's id (e.g.,
    ``camera-ground-route-010``) and whose ``group_id`` is ``ground-route``.

    This closes the P0-2 item 3 loop: the replacement candidate built by
    ``build_ground_route_replacement_candidate`` can be fed into
    ``materialize_reciprocal_role_candidate`` to produce the
    ``ProductionCameraPose`` that replaces the obstructed pose in the
    180-camera plan."""

    ground_node = next(
        node for node in topology.nodes
        if node.level == "ground" and node.node_id == "central-ground-east"
    )
    binding = WalkableNodeBinding(
        node_id=ground_node.node_id,
        node_position_m=ground_node.position_m,
        level="ground",
        ground_route_ref="path-network-003",
    )
    look_at = (
        ground_node.position_m[0],
        ground_node.position_m[1] + 25.0,
        ground_node.position_m[2] + 1.6,
    )
    candidate = build_ground_route_replacement_candidate(
        obstructed_camera_id="camera-ground-route-010",
        role_module_id="central-courtyard-downhill",
        topology_ref="path-network-003",
        bound_walkable_node=binding,
        look_at_m=look_at,
        bound_production_plan_sha256="a" * 64,
        bound_camera_registry_sha256="b" * 64,
        probe_clearance_min_m=2.475,
        disclosure=(
            "modeled-unverified replacement for camera-ground-route-010; "
            "fresh preflight + six-layer render required"
        ),
    )
    pose = materialize_reciprocal_role_candidate(
        candidate,
        target_group_id="ground-route",
        target_sequence_index=10,
        target_camera_id="camera-ground-route-010",
    )
    assert pose.camera_id == "camera-ground-route-010"
    assert pose.group_id == "ground-route"
    assert pose.sequence_index == 10
    assert pose.audit_only is False
    assert pose.topology_ref == "path-network-003"


# --------------------------------------------------------------------------- #
# Phase 4.4 fail-closed audit (REVIEW-OPUS-006) fixes
# --------------------------------------------------------------------------- #


def test_reciprocal_route_module_order_matches_plan_module_order(plan) -> None:
    """``RECIPROCAL_ROUTE_MODULE_ORDER`` must exactly match the module
    order validated by ``ReciprocalRouteModulePlan``'s
    ``_modules_are_exact_and_ordered`` validator.  If the two tuples
    drift apart, ``build_ground_route_replacement_candidate`` would
    map a module to the wrong role index, producing a candidate with
    a wrong ``camera_id``.  This TDD locks the two tuples together."""

    plan_module_order = tuple(module.module_id for module in plan.modules)
    assert RECIPROCAL_ROUTE_MODULE_ORDER == plan_module_order


@pytest.mark.parametrize(
    "bad_clearance,label",
    [
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (float("-inf"), "-inf"),
        (True, "bool-true"),
        (False, "bool-false"),
    ],
)
def test_build_replacement_candidate_rejects_non_finite_clearance(
    topology, bad_clearance, label,
) -> None:
    """``probe_clearance_min_m`` must be a finite real number.  NaN and
    Inf silently bypass the ``< MIN_ROUTE_CLEARANCE_M`` comparison
    (``nan < 2.4`` is False, ``inf < 2.4`` is False), so a caller
    passing NaN or Inf would construct a replacement candidate without
    verified clearance -- a fail-open hole.  ``bool`` is a subclass of
    ``int`` in Python, so ``isinstance(True, (int, float))`` is True and
    ``math.isfinite(True)`` is True; an explicit ``bool`` check is
    required (GLM-P2 per FEEDBACK-HANDOFF-CODEX-012).  This TDD locks
    the fix."""

    ground_node = next(
        node for node in topology.nodes if node.level == "ground"
    )
    binding = WalkableNodeBinding(
        node_id=ground_node.node_id,
        node_position_m=ground_node.position_m,
        level="ground",
        ground_route_ref="path-network-001",
    )
    look_at = (
        ground_node.position_m[0],
        ground_node.position_m[1] + 25.0,
        ground_node.position_m[2] + 1.6,
    )
    with pytest.raises(ReciprocalRouteError, match="finite real number"):
        build_ground_route_replacement_candidate(
            obstructed_camera_id="camera-ground-route-010",
            role_module_id="central-courtyard-downhill",
            topology_ref="path-network-003",
            bound_walkable_node=binding,
            look_at_m=look_at,
            bound_production_plan_sha256="a" * 64,
            bound_camera_registry_sha256="b" * 64,
            probe_clearance_min_m=bad_clearance,
            disclosure="modeled-unverified replacement; fresh preflight required",
        )


# --------------------------------------------------------------------------- #
# GLM-P2 (FEEDBACK-HANDOFF-CODEX-012 §"GLM-P2"): schema-level
# ``allow_inf_nan=False`` defense in depth for tuple[float, float, float]
# fields.  These tests confirm the field-level rejection fires before the
# model_validator's ``math.isfinite`` check, providing two independent layers.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_value,label",
    [
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (float("-inf"), "-inf"),
    ],
)
def test_role_camera_candidate_rejects_non_finite_look_at(
    bad_value: float, label: str,
) -> None:
    """``look_at_m`` with NaN/Inf must be rejected at schema level
    (GLM-P2: ``allow_inf_nan=False`` on the tuple element type)."""

    with pytest.raises(ValidationError):
        ReciprocalRoleCameraCandidate(
            role_module_id="central-courtyard-downhill",
            camera_id="camera-reciprocal-role-001",
            topology_ref="path-network-003",
            arc_length_m=None,
            position_m=(40.0, 30.0, 70.0),
            look_at_m=(bad_value, 5.0, 70.0),
            eye_height_m=1.6,
            fov_x_deg=65.0,
            audit_only=False,
            disclosure="modeled-unverified standing-eye at the courtyard downhill gate",
            bound_production_plan_sha256="0" * 64,
            bound_camera_registry_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    "bad_value,label",
    [
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (float("-inf"), "-inf"),
    ],
)
def test_walkable_node_binding_rejects_non_finite_position(
    bad_value: float, label: str,
) -> None:
    """``WalkableNodeBinding.node_position_m`` with NaN/Inf must be
    rejected at schema level (GLM-P2: ``allow_inf_nan=False`` on the
    tuple element type)."""

    with pytest.raises(ValidationError):
        WalkableNodeBinding(
            node_id="test-node",
            node_position_m=(bad_value, 0.0, 0.0),
            level="ground",
            ground_route_ref="path-network-001",
        )


@pytest.mark.parametrize(
    "bad_value,label",
    [
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (float("-inf"), "-inf"),
    ],
)
def test_part_layout_rejects_non_finite_extent(
    bad_value: float, label: str,
) -> None:
    """``PartLayoutSpec.extent_m`` with NaN/Inf must be rejected at
    schema level (GLM-P2: ``allow_inf_nan=False`` on the tuple element
    type).  The model_validator's positivity check is a second layer."""

    with pytest.raises(ValidationError):
        PartLayoutSpec(
            center_m=(0.0, 0.0, 0.0),
            extent_m=(bad_value, 1.6, 0.6),
            orientation_deg=0.0,
        )


# --------------------------------------------------------------------------- #
# GLM-P1 (FEEDBACK-HANDOFF-CODEX-012 §"GLM-P1"): terrain dual-truth audit.
#
# The Blender terrain mesh samples the same analytic ``terrain_height_m``
# formula on a 4 m grid and linearly interpolates across triangulated
# faces.  At off-grid points the smooth sine curve and the faceted mesh
# diverge.  These tests quantify the discrepancy and fail-closed if the
# module floor clearance is insufficient to absorb it.
# --------------------------------------------------------------------------- #

#: Grid spacing used by the Blender terrain mesh builder
#: (scripts/blender/build_synthetic_village.py SURFACE_TERRAIN_SPACING_M).
#: Hardcoded here to avoid importing the Blender script (which has
#: Blender-only dependencies).  If the spacing changes in the build
#: script, this constant must be updated and the tests re-run.
_TERRAIN_MESH_GRID_SPACING_M = 4.0


def _grid_index(value: float, spacing: float, origin: float) -> int:
    """Return the lower grid index for a world coordinate."""
    return int((value - origin) / spacing)


def _barycentric_mesh_height(
    x: float,
    y: float,
    extent,
    spacing: float = _TERRAIN_MESH_GRID_SPACING_M,
) -> float:
    """Simulate the Blender terrain mesh's barycentric interpolation.

    The mesh triangulates each grid cell quad into two triangles along
    the bottom-left to top-right diagonal.  This function computes the
    exact barycentric interpolation for whichever triangle the point
    falls in, matching the Blender mesh's rendered height.
    """
    origin_x = -extent.width_m / 2
    origin_y = -extent.depth_m / 2

    col = _grid_index(x, spacing, origin_x)
    row = _grid_index(y, spacing, origin_y)

    x0 = origin_x + col * spacing
    y0 = origin_y + row * spacing
    x1 = x0 + spacing
    y1 = y0 + spacing

    z_bl = terrain_height_m(x0, y0, extent)
    z_br = terrain_height_m(x1, y0, extent)
    z_tl = terrain_height_m(x0, y1, extent)
    z_tr = terrain_height_m(x1, y1, extent)

    fx = (x - x0) / spacing
    fy = (y - y0) / spacing

    # Diagonal from BL (x0,y0) to TR (x1,y1): fx >= fy -> Triangle 1
    if fx >= fy:
        # Triangle BL, BR, TR: lambda1 = 1-fx, lambda2 = fx-fy, lambda3 = fy
        return z_bl * (1 - fx) + z_br * (fx - fy) + z_tr * fy
    # Triangle BL, TR, TL: lambda1 = 1-fy, lambda2 = fx, lambda3 = fy-fx
    return z_bl * (1 - fy) + z_tr * fx + z_tl * (fy - fx)


def test_terrain_mesh_interpolation_error_bounded_by_central_clearance(
    scene,
) -> None:
    """The maximum terrain mesh interpolation error at the central
    courtyard contour (y=40) must be less than
    ``_CENTRAL_FLOOR_CLEARANCE_M`` (0.5 m).

    The central courtyard module places its floor at
    ``terrain_height_m(x, 40) + _CENTRAL_FLOOR_CLEARANCE_M``.  If the
    Blender mesh's interpolated height exceeds the analytic height by
    more than the clearance, the module floor would be embedded in the
    rendered terrain -- a fail-closed violation.

    This test simulates the mesh interpolation and fails if the clearance
    is insufficient at any point along the y=40 contour where module
    parts are placed (x from 25 to 50, covering the 7-part central
    courtyard module + camera approach offset).
    """
    extent = scene.extent
    y = _CENTRAL_CONTOUR_Y_M

    max_error = 0.0
    max_error_x = 0.0

    # Sample every 0.1 m along the central contour where parts are placed.
    # Module parts span x from 30 to 45 (7 parts at 2.5 m spacing);
    # candidate camera is at x ~ 25 (approach offset 5 m before first part).
    x = 25.0
    while x <= 50.0:
        analytic = terrain_height_m(x, y, extent)
        mesh = _barycentric_mesh_height(x, y, extent)
        error = mesh - analytic  # positive: mesh is higher -> floor embedded
        if error > max_error:
            max_error = error
            max_error_x = x
        x += 0.1

    assert max_error < _CENTRAL_FLOOR_CLEARANCE_M, (
        f"terrain mesh interpolation error {max_error:.4f} m at "
        f"({max_error_x:.1f}, {y}) exceeds central floor clearance "
        f"{_CENTRAL_FLOOR_CLEARANCE_M} m; module floor would be embedded "
        f"in rendered terrain"
    )


def test_terrain_mesh_max_interpolation_error_across_scene(scene) -> None:
    """Measure the maximum terrain mesh interpolation error across the
    entire scene to document the scale of the dual-truth discrepancy.

    This test does NOT fail on a threshold -- it reports the maximum
    error and the location where it occurs, establishing the empirical
    bound for the dual-truth audit.  The central clearance test above
    uses this bound to fail-closed at the module location.
    """
    extent = scene.extent
    spacing = _TERRAIN_MESH_GRID_SPACING_M

    max_error = 0.0
    max_error_loc = (0.0, 0.0)

    # Sample at 0.5 m resolution across the scene interior (skip the
    # exact boundary where terrain is 0 or 120).
    x = -extent.width_m / 2 + spacing
    while x < extent.width_m / 2 - spacing:
        y = -extent.depth_m / 2 + spacing
        while y < extent.depth_m / 2 - spacing:
            # Only check off-grid points (grid points have zero error)
            fx = (x - (-extent.width_m / 2)) % spacing
            fy = (y - (-extent.depth_m / 2)) % spacing
            if fx < 0.01 or fy < 0.01:
                y += 0.5
                continue
            analytic = terrain_height_m(x, y, extent)
            mesh = _barycentric_mesh_height(x, y, extent)
            error = abs(mesh - analytic)
            if error > max_error:
                max_error = error
                max_error_loc = (x, y)
            y += 0.5
        x += 0.5

    # Report the bound.  This is an empirical observation, not a gate.
    # The gate is in test_terrain_mesh_interpolation_error_bounded_by_central_clearance.
    assert max_error > 0.0, "no interpolation error found (sampling bug)"
    # The error must be sub-meter for the 4 m grid on this terrain.
    assert max_error < 1.0, (
        f"terrain mesh max interpolation error {max_error:.4f} m at "
        f"{max_error_loc} exceeds 1.0 m; grid spacing may be too coarse "
        f"or terrain formula may have changed"
    )

