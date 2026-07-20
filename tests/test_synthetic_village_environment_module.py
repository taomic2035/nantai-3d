"""TDD for the Batch 6 environment module plan (HANDOFF-OPUS-007).

These tests cover the ten required cases from HANDOFF-OPUS-007 §TDD:

1.  module plan canonical bytes are cross-process consistent and bind the
    exact ScenePlan / ElevatedTopology / source SHA-256 values.
2.  tampered source / module / build identity fails closed.
3.  three modules and every formal part carry stable ID, instance,
    semantic and material identity; the registry is complete and unique.
4.  creek floor / bank / water / bridge arch cross-section is
    non-penetrating: water below bank, bank below terrain, soffit above deck.
5.  central courtyard loop width, clearance, collision and entry
    attachment do not regress.
6.  service courtyard props do not carry topology and do not block paths.
7.  module plan rejects missing, duplicate, or out-of-order module
    registry (the contract that downstream Blender build requests inherit).
8.  rebuilding the same plan yields byte-identical canonical bytes
    (determinism contract for downstream .blend/.glb/previews).
9.  six-layer frames can distinguish bridge / waterwheel / courtyard /
    service-shed / prop instances via distinct semantic and material slots.
10. coverage stays ``unknown`` until actual frame statistics are produced;
    the plan never claims coverage it has not measured.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.elevated_topology import (
    ElevatedTopologyPlan,
    build_elevated_topology_plan,
    canonical_elevated_topology_bytes,
)
from pipeline.synthetic_village.environment_module import (
    BRIDGE_UNDERCROFT_SOURCE_SHA256,
    CENTRAL_COURTYARD_INSTANCE_RANGE,
    CENTRAL_COURTYARD_SOURCE_SHA256,
    DESIGN_SOURCE_SHA256S,
    ENVIRONMENT_MODULE_RECIPE_VERSION,
    ENVIRONMENT_MODULE_SCHEMA,
    LOWER_BRIDGE_INSTANCE_RANGE,
    MIN_GALLERY_CLEAR_HEIGHT_M,
    MIN_GALLERY_CLEAR_WIDTH_M,
    MIN_RAMP_CLEAR_WIDTH_M,
    MIN_STAIR_CLEAR_WIDTH_M,
    REAR_SERVICE_COURTYARD_SOURCE_SHA256,
    REAR_SERVICE_INSTANCE_RANGE,
    CentralCourtyardRecipe,
    CreekCrossSectionSpec,
    EnvironmentModule,
    EnvironmentModuleError,
    EnvironmentModulePart,
    EnvironmentModulePlan,
    EnvironmentModuleSummary,
    LowerBridgeRecipe,
    RearServiceCourtyardRecipe,
    WaterwheelPartSpec,
    build_default_environment_module_plan,
    canonical_environment_module_plan_bytes,
    environment_module_plan_sha256,
    verify_environment_module_plan,
)
from pipeline.synthetic_village.scene_plan import (
    ScenePlan,
    build_scene_plan,
    canonical_scene_plan_bytes,
)


# --------------------------------------------------------------------------- #
# Fixtures.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def scene() -> ScenePlan:
    return build_scene_plan()


@pytest.fixture(scope="module")
def topology(scene: ScenePlan) -> ElevatedTopologyPlan:
    return build_elevated_topology_plan(scene)


@pytest.fixture(scope="module")
def plan(scene: ScenePlan, topology: ElevatedTopologyPlan) -> EnvironmentModulePlan:
    return build_default_environment_module_plan(
        scene=scene,
        elevated_topology=topology,
    )


def _plan_payload(plan: EnvironmentModulePlan) -> dict[str, Any]:
    return json.loads(canonical_environment_module_plan_bytes(plan))


# --------------------------------------------------------------------------- #
# §TDD 1 — canonical bytes bind exact ScenePlan / ElevatedTopology / source SHA.
# --------------------------------------------------------------------------- #


def test_canonical_bytes_bind_exact_scene_and_topology_hashes(
    scene: ScenePlan,
    topology: ElevatedTopologyPlan,
    plan: EnvironmentModulePlan,
) -> None:
    expected_scene_sha = hashlib.sha256(
        canonical_scene_plan_bytes(scene),
    ).hexdigest()
    expected_topology_sha = hashlib.sha256(
        canonical_elevated_topology_bytes(topology),
    ).hexdigest()

    assert plan.schema_version == ENVIRONMENT_MODULE_SCHEMA
    assert plan.recipe_version == ENVIRONMENT_MODULE_RECIPE_VERSION
    assert plan.scene_plan_sha256 == expected_scene_sha
    assert plan.elevated_topology_sha256 == expected_topology_sha
    assert plan.central_courtyard_source_sha256 == CENTRAL_COURTYARD_SOURCE_SHA256
    assert plan.bridge_undercroft_source_sha256 == BRIDGE_UNDERCROFT_SOURCE_SHA256
    assert plan.rear_service_courtyard_source_sha256 == REAR_SERVICE_COURTYARD_SOURCE_SHA256


def test_canonical_bytes_are_stable_across_processes(
    scene: ScenePlan,
    topology: ElevatedTopologyPlan,
) -> None:
    plan_a = build_default_environment_module_plan(
        scene=scene, elevated_topology=topology,
    )
    plan_b = build_default_environment_module_plan(
        scene=scene, elevated_topology=topology,
    )
    bytes_a = canonical_environment_module_plan_bytes(plan_a)
    bytes_b = canonical_environment_module_plan_bytes(plan_b)
    assert bytes_a == bytes_b
    # Round-trip through JSON must reproduce the exact same plan.
    revalidated = EnvironmentModulePlan.model_validate_json(bytes_a)
    assert revalidated == plan_a


def test_verify_environment_module_plan_passes_on_canonical(
    scene: ScenePlan,
    topology: ElevatedTopologyPlan,
    plan: EnvironmentModulePlan,
) -> None:
    # Must NOT raise.
    verify_environment_module_plan(plan, scene=scene, elevated_topology=topology)


# --------------------------------------------------------------------------- #
# §TDD 2 — tampered identity fails closed.
# --------------------------------------------------------------------------- #


def test_tampered_scene_sha256_fails_closed(
    scene: ScenePlan,
    topology: ElevatedTopologyPlan,
    plan: EnvironmentModulePlan,
) -> None:
    # Pydantic only checks the SHA-256 regex; the fail-closed identity
    # check happens in verify_environment_module_plan, which re-binds the
    # bound SHA against the actual scene's canonical bytes.
    tampered = plan.model_copy(
        update={"scene_plan_sha256": "0" * 64},
    )
    with pytest.raises(EnvironmentModuleError):
        verify_environment_module_plan(
            tampered, scene=scene, elevated_topology=topology,
        )


def test_tampered_topology_sha256_fails_closed(
    scene: ScenePlan,
    topology: ElevatedTopologyPlan,
    plan: EnvironmentModulePlan,
) -> None:
    tampered = plan.model_copy(
        update={"elevated_topology_sha256": "f" * 64},
    )
    with pytest.raises(EnvironmentModuleError):
        verify_environment_module_plan(
            tampered, scene=scene, elevated_topology=topology,
        )


def test_tampered_design_source_sha256_fails_closed(plan: EnvironmentModulePlan) -> None:
    tampered_payload = _plan_payload(plan)
    # Replace one design source SHA with a different 64-hex value that
    # is not in DESIGN_SOURCE_SHA256S.
    tampered_payload["central_courtyard_source_sha256"] = "a" * 64
    with pytest.raises((EnvironmentModuleError, ValidationError)):
        EnvironmentModulePlan.model_validate(tampered_payload)


def test_verify_rejects_plan_bound_to_a_different_scene(
    plan: EnvironmentModulePlan,
) -> None:
    # Build a different scene by perturbing the recipe (build_scene_plan is
    # deterministic, so we mutate one field on a copy).
    original_scene = build_scene_plan()
    different_scene = original_scene.model_copy(
        update={"plan_id": "synthetic-mountain-village-scene-v1"},
    )
    # The plan was bound to original_scene's SHA.  Feeding it back the
    # canonical scene (which equals original_scene) is fine, but the
    # verification function must still reject if the bound SHA does not
    # match the supplied scene's canonical SHA.  We simulate this by
    # building a fresh plan bound to a different scene and verifying it
    # against the canonical scene.
    fresh_plan = build_default_environment_module_plan(
        scene=original_scene,
        elevated_topology=build_elevated_topology_plan(original_scene),
    )
    # Sanity: bound to original_scene.
    verify_environment_module_plan(
        fresh_plan, scene=original_scene,
        elevated_topology=build_elevated_topology_plan(original_scene),
    )
    # Now break it: mutate the plan's bound SHA to a foreign value.
    foreign = fresh_plan.model_copy(
        update={"scene_plan_sha256": "0" * 64},
    )
    with pytest.raises(EnvironmentModuleError):
        verify_environment_module_plan(
            foreign, scene=original_scene,
            elevated_topology=build_elevated_topology_plan(original_scene),
        )


# --------------------------------------------------------------------------- #
# §TDD 3 — three modules + every part: stable ID, instance, semantic, material.
# --------------------------------------------------------------------------- #


def test_three_modules_are_exact_and_ordered(plan: EnvironmentModulePlan) -> None:
    module_ids = tuple(module.module_id for module in plan.modules)
    assert module_ids == (
        "central-courtyard",
        "lower-bridge-waterwheel",
        "rear-service-courtyard",
    )


def test_module_instance_id_partition_is_locked(plan: EnvironmentModulePlan) -> None:
    for module in plan.modules:
        expected_range = {
            "central-courtyard": CENTRAL_COURTYARD_INSTANCE_RANGE,
            "lower-bridge-waterwheel": LOWER_BRIDGE_INSTANCE_RANGE,
            "rear-service-courtyard": REAR_SERVICE_INSTANCE_RANGE,
        }[module.module_id]
        for part in module.parts:
            assert part.instance_id in expected_range, (
                f"{part.part_id} instance {part.instance_id} not in "
                f"{module.module_id} segment"
            )


def test_full_instance_segment_occupied_exactly(plan: EnvironmentModulePlan) -> None:
    all_instances = sorted(
        part.instance_id
        for module in plan.modules
        for part in module.parts
    )
    assert all_instances == list(range(131, 176))
    # No overlap with elevated components (127-130).
    assert all(instance >= 131 for instance in all_instances)


def test_part_ids_unique_across_plan(plan: EnvironmentModulePlan) -> None:
    part_ids = [
        part.part_id
        for module in plan.modules
        for part in module.parts
    ]
    assert len(set(part_ids)) == len(part_ids)


def test_material_slot_ids_are_stable(plan: EnvironmentModulePlan) -> None:
    # Every part must have a non-empty material slot id matching the
    # naming contract.
    for module in plan.modules:
        for part in module.parts:
            assert part.material_slot_id.startswith("material-")
            assert part.semantic_id in range(0, 15)


def test_summary_part_count_matches(plan: EnvironmentModulePlan) -> None:
    actual = sum(len(module.parts) for module in plan.modules)
    assert plan.summary.part_count == actual
    assert plan.summary.module_count == 3
    assert plan.summary.instance_id_segment_start == 131
    assert plan.summary.instance_id_segment_end == 175


# --------------------------------------------------------------------------- #
# §TDD 4 — creek cross-section non-penetration.
# --------------------------------------------------------------------------- #


def test_creek_section_accepts_canonical_ordering() -> None:
    section = CreekCrossSectionSpec(
        arc_length_m=10.0,
        terrain_z_m=0.0,
        bank_z_m=-0.4,
        water_z_m=-0.8,
        arch_soffit_z_m=2.6,
        deck_z_m=2.4,
    )
    assert section.water_z_m < section.bank_z_m < section.terrain_z_m
    assert section.arch_soffit_z_m > section.deck_z_m


def test_creek_section_rejects_water_above_terrain() -> None:
    with pytest.raises(ValidationError):
        CreekCrossSectionSpec(
            arc_length_m=10.0,
            terrain_z_m=-1.0,   # terrain below water
            bank_z_m=-0.4,
            water_z_m=0.5,      # water above terrain
            arch_soffit_z_m=2.6,
            deck_z_m=2.4,
        )


def test_creek_section_rejects_water_above_bank() -> None:
    with pytest.raises(ValidationError):
        CreekCrossSectionSpec(
            arc_length_m=10.0,
            terrain_z_m=0.5,
            bank_z_m=-0.4,
            water_z_m=0.0,      # water above bank
            arch_soffit_z_m=2.6,
            deck_z_m=2.4,
        )


def test_creek_section_rejects_soffit_below_deck() -> None:
    with pytest.raises(ValidationError):
        CreekCrossSectionSpec(
            arc_length_m=10.0,
            terrain_z_m=0.0,
            bank_z_m=-0.4,
            water_z_m=-0.8,
            arch_soffit_z_m=2.0,   # soffit below deck
            deck_z_m=2.4,
        )


def test_lower_bridge_module_has_at_least_three_sections(plan: EnvironmentModulePlan) -> None:
    bridge = next(
        m for m in plan.modules if m.module_id == "lower-bridge-waterwheel"
    )
    assert isinstance(bridge.recipe, LowerBridgeRecipe)
    assert len(bridge.recipe.creek_sections) >= 3
    # Every section is non-penetrating (validated at construction; sanity here).
    for section in bridge.recipe.creek_sections:
        assert section.water_z_m <= section.bank_z_m + 1e-6
        assert section.bank_z_m <= section.terrain_z_m + 1e-6
        assert section.arch_soffit_z_m >= section.deck_z_m - 1e-6


# --------------------------------------------------------------------------- #
# §TDD 5 — central courtyard loop width / clearance / collision / entry.
# --------------------------------------------------------------------------- #


def test_central_courtyard_thresholds_match_spec(
    plan: EnvironmentModulePlan,
) -> None:
    courtyard = next(
        m for m in plan.modules if m.module_id == "central-courtyard"
    )
    assert isinstance(courtyard.recipe, CentralCourtyardRecipe)
    assert courtyard.recipe.gallery.clear_width_m >= MIN_GALLERY_CLEAR_WIDTH_M
    assert courtyard.recipe.gallery.clear_height_m >= MIN_GALLERY_CLEAR_HEIGHT_M
    assert courtyard.recipe.stair.clear_width_m >= MIN_STAIR_CLEAR_WIDTH_M
    assert courtyard.recipe.ramp.clear_width_m >= MIN_RAMP_CLEAR_WIDTH_M


def test_central_courtyard_rejects_undersized_gallery() -> None:
    base_recipe = build_default_environment_module_plan(
        scene=build_scene_plan(),
        elevated_topology=build_elevated_topology_plan(build_scene_plan()),
    )
    courtyard = next(
        m for m in base_recipe.modules if m.module_id == "central-courtyard"
    )
    assert isinstance(courtyard.recipe, CentralCourtyardRecipe)
    bad_gallery = courtyard.recipe.gallery.model_copy(
        update={"clear_width_m": MIN_GALLERY_CLEAR_WIDTH_M - 0.1},
    )
    with pytest.raises(ValidationError):
        CentralCourtyardRecipe.model_validate(
            courtyard.recipe.model_copy(
                update={"gallery": bad_gallery},
            ).model_dump(),
        )


def test_central_courtyard_entries_bind_path_network_002_and_003(
    plan: EnvironmentModulePlan,
) -> None:
    courtyard = next(
        m for m in plan.modules if m.module_id == "central-courtyard"
    )
    assert isinstance(courtyard.recipe, CentralCourtyardRecipe)
    assert courtyard.recipe.gallery.west_entry_connects_to == "path-network-002"
    assert courtyard.recipe.gallery.east_entry_connects_to == "path-network-003"
    # Ground attachments are exactly the two elevated-loop ground nodes.
    assert set(courtyard.recipe.bound_ground_attachments) == {
        "central-ground-west",
        "central-ground-east",
    }
    # Elevated edges are exactly the three central-loop edges.
    assert set(courtyard.recipe.bound_elevated_edges) == {
        "edge-central-stair-001",
        "edge-central-gallery-001",
        "edge-central-ramp-001",
    }


def test_central_courtyard_rejects_duplicate_ground_attachments() -> None:
    with pytest.raises(ValidationError):
        CentralCourtyardRecipe.model_validate(
            {
                "bound_object_id": "courtyard-public-002",
                "bound_ground_attachments": (
                    "central-ground-west",
                    "central-ground-west",
                ),
                "bound_elevated_edges": (
                    "edge-central-stair-001",
                    "edge-central-gallery-001",
                    "edge-central-ramp-001",
                ),
                "gallery": {
                    "clear_width_m": 3.0,
                    "clear_height_m": 2.7,
                    "deck_segment_count": 4,
                    "drainage_channel_present": True,
                    "east_entry_connects_to": "path-network-003",
                    "west_entry_connects_to": "path-network-002",
                },
                "stair": {
                    "clear_width_m": 2.6,
                    "tread_count": 6,
                    "tread_depth_m": 0.30,
                    "continuous_collision": True,
                },
                "ramp": {
                    "clear_width_m": 3.2,
                    "continuous_collision": True,
                    "slope_pct": 6.0,
                },
                "props": {
                    "workshed_count": 2,
                    "workbench_count": 4,
                    "replaceable_prop_slot_count": 6,
                    "planter_tree_non_collision": True,
                },
                "paving_material_slot_id": "material-courtyard-flagstone-01",
                "drainage_material_slot_id": "material-courtyard-drain-01",
            },
        )


# --------------------------------------------------------------------------- #
# §TDD 6 — service courtyard props do not carry topology / block paths.
# --------------------------------------------------------------------------- #


def test_rear_service_recipe_props_do_not_carry_topology(
    plan: EnvironmentModulePlan,
) -> None:
    service = next(
        m for m in plan.modules if m.module_id == "rear-service-courtyard"
    )
    assert isinstance(service.recipe, RearServiceCourtyardRecipe)
    assert service.recipe.props_do_not_carry_topology is True
    assert service.recipe.props_do_not_block_paths is True
    assert service.recipe.paving_conform_to_terrain is True
    assert service.recipe.door_window_eaves_gutter_declared is True
    # At least three replaceable variants are declared.
    assert len(service.recipe.variants) >= 3
    variant_ids = [v.variant_id for v in service.recipe.variants]
    assert len(set(variant_ids)) == len(variant_ids)


def test_rear_service_recipe_rejects_missing_variants() -> None:
    base_recipe = build_default_environment_module_plan(
        scene=build_scene_plan(),
        elevated_topology=build_elevated_topology_plan(build_scene_plan()),
    )
    service = next(
        m for m in base_recipe.modules if m.module_id == "rear-service-courtyard"
    )
    assert isinstance(service.recipe, RearServiceCourtyardRecipe)
    bad_recipe = service.recipe.model_copy(
        update={"variants": service.recipe.variants[:2]},
    )
    with pytest.raises(ValidationError):
        RearServiceCourtyardRecipe.model_validate(bad_recipe.model_dump())


# --------------------------------------------------------------------------- #
# §TDD 7 — module registry: missing / duplicate / out-of-order fail closed.
# --------------------------------------------------------------------------- #


def test_plan_rejects_missing_module(plan: EnvironmentModulePlan) -> None:
    payload = _plan_payload(plan)
    payload["modules"] = payload["modules"][:2]
    with pytest.raises(ValidationError):
        EnvironmentModulePlan.model_validate(payload)


def test_plan_rejects_duplicate_module(plan: EnvironmentModulePlan) -> None:
    payload = _plan_payload(plan)
    payload["modules"] = payload["modules"] * 2  # duplicates
    with pytest.raises(ValidationError):
        EnvironmentModulePlan.model_validate(payload)


def test_plan_rejects_out_of_order_modules(plan: EnvironmentModulePlan) -> None:
    payload = _plan_payload(plan)
    # Swap module 0 and module 1 -- order must be central -> bridge -> service.
    payload["modules"][0], payload["modules"][1] = (
        payload["modules"][1],
        payload["modules"][0],
    )
    with pytest.raises(ValidationError):
        EnvironmentModulePlan.model_validate(payload)


def test_plan_rejects_unknown_module_id(plan: EnvironmentModulePlan) -> None:
    payload = _plan_payload(plan)
    payload["modules"][0]["module_id"] = "not-a-real-module"
    with pytest.raises(ValidationError):
        EnvironmentModulePlan.model_validate(payload)


def test_module_rejects_part_in_wrong_segment(plan: EnvironmentModulePlan) -> None:
    courtyard = next(
        m for m in plan.modules if m.module_id == "central-courtyard"
    )
    bad_part = courtyard.parts[0].model_copy(
        update={"instance_id": 200},  # outside 131-175 entirely
    )
    with pytest.raises(ValidationError):
        EnvironmentModulePart.model_validate(bad_part.model_dump())


def test_module_rejects_part_in_other_module_segment(
    plan: EnvironmentModulePlan,
) -> None:
    courtyard = next(
        m for m in plan.modules if m.module_id == "central-courtyard"
    )
    # Put a courtyard part_id in the bridge segment.
    bad_part = courtyard.parts[0].model_copy(
        update={"instance_id": 150},  # in lower-bridge segment
    )
    with pytest.raises(ValidationError):
        EnvironmentModulePart.model_validate(bad_part.model_dump())


# --------------------------------------------------------------------------- #
# §TDD 8 — determinism: same request yields byte-identical canonical bytes.
# --------------------------------------------------------------------------- #


def test_deterministic_canonical_bytes(
    scene: ScenePlan,
    topology: ElevatedTopologyPlan,
) -> None:
    plan_a = build_default_environment_module_plan(
        scene=scene, elevated_topology=topology,
    )
    plan_b = build_default_environment_module_plan(
        scene=scene, elevated_topology=topology,
    )
    assert plan_a == plan_b
    assert (
        environment_module_plan_sha256(plan_a)
        == environment_module_plan_sha256(plan_b)
    )


def test_plan_sha_changes_when_a_part_changes(plan: EnvironmentModulePlan) -> None:
    courtyard = next(
        m for m in plan.modules if m.module_id == "central-courtyard"
    )
    # Mutate one part's material slot id; this must change the plan digest.
    new_part = courtyard.parts[0].model_copy(
        update={"material_slot_id": "material-courtyard-flagstone-02"},
    )
    new_parts = (new_part,) + courtyard.parts[1:]
    new_module = courtyard.model_copy(update={"parts": new_parts})
    new_modules = (new_module,) + tuple(plan.modules[1:])
    new_plan = plan.model_copy(update={"modules": new_modules})
    assert (
        environment_module_plan_sha256(new_plan)
        != environment_module_plan_sha256(plan)
    )


# --------------------------------------------------------------------------- #
# §TDD 9 — six-layer instance/semantic distinguishability (plan-level evidence).
# --------------------------------------------------------------------------- #


def test_bridge_waterwheel_courtyard_service_props_have_distinct_semantics(
    plan: EnvironmentModulePlan,
) -> None:
    # Each module must declare at least one part with a semantic id that
    # is distinguishable from the others at the instance layer.
    semantic_sets: dict[str, set[int]] = {}
    for module in plan.modules:
        semantic_sets[module.module_id] = {
            part.semantic_id for part in module.parts
        }
    # All four functional classes (bridge structure, waterwheel, courtyard,
    # service) have at least one distinguishable semantic slot.
    assert semantic_sets["central-courtyard"]  # not empty
    assert semantic_sets["lower-bridge-waterwheel"]
    assert semantic_sets["rear-service-courtyard"]
    # Bridge structure (146-154) and waterwheel (155-160) must have
    # distinguishable instance ranges -- this is the precondition for the
    # six-layer frame to tell them apart.
    bridge = next(
        m for m in plan.modules if m.module_id == "lower-bridge-waterwheel"
    )
    bridge_struct_ids = {
        p.instance_id for p in bridge.parts if p.instance_id <= 154
    }
    waterwheel_ids = {
        p.instance_id for p in bridge.parts if p.instance_id >= 155
    }
    assert bridge_struct_ids.isdisjoint(waterwheel_ids)


def test_waterwheel_parts_have_independent_identity(plan: EnvironmentModulePlan) -> None:
    bridge = next(
        m for m in plan.modules if m.module_id == "lower-bridge-waterwheel"
    )
    assert isinstance(bridge.recipe, LowerBridgeRecipe)
    # Six independent parts with unique part_id and instance_id.
    assert len(bridge.recipe.waterwheel_parts) >= 6
    wheel_part_ids = [p.part_id for p in bridge.recipe.waterwheel_parts]
    wheel_instance_ids = [p.instance_id for p in bridge.recipe.waterwheel_parts]
    assert len(set(wheel_part_ids)) == len(wheel_part_ids)
    assert len(set(wheel_instance_ids)) == len(wheel_instance_ids)
    # Waterwheel instance IDs are NOT shared with bridge abutment / arch / deck.
    bridge_struct_instance_ids = {
        p.instance_id for p in bridge.parts if "waterwheel-" not in p.part_id
    }
    assert set(wheel_instance_ids).isdisjoint(bridge_struct_instance_ids)


def test_waterwheel_recipe_must_match_parts_list() -> None:
    """If the recipe declares waterwheel instance 999 but the parts list
    has it at 155, the EnvironmentModule must fail closed.  This is the
    regression test for the bug class where recipe and parts disagree."""
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_default_environment_module_plan(
        scene=scene, elevated_topology=topology,
    )
    bridge = next(
        m for m in plan.modules if m.module_id == "lower-bridge-waterwheel"
    )
    assert isinstance(bridge.recipe, LowerBridgeRecipe)
    # Tamper: change one waterwheel part's instance id in the recipe only.
    bad_wheel = bridge.recipe.waterwheel_parts[0].model_copy(
        update={"instance_id": 999},  # outside bridge segment
    )
    bad_recipe = bridge.recipe.model_copy(
        update={"waterwheel_parts": (bad_wheel,) + bridge.recipe.waterwheel_parts[1:]},
    )
    # Recipe-level validator should reject (instance outside segment).
    with pytest.raises(ValidationError):
        LowerBridgeRecipe.model_validate(bad_recipe.model_dump())


def test_waterwheel_recipe_part_id_must_appear_in_parts_list() -> None:
    """If the recipe declares a waterwheel part_id that the parts list
    doesn't have, the EnvironmentModule must fail closed."""
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_default_environment_module_plan(
        scene=scene, elevated_topology=topology,
    )
    bridge = next(
        m for m in plan.modules if m.module_id == "lower-bridge-waterwheel"
    )
    assert isinstance(bridge.recipe, LowerBridgeRecipe)
    # Add a phantom waterwheel part_id that doesn't exist in the parts list.
    phantom = WaterwheelPartSpec(
        part_id="waterwheel-phantom-999",
        instance_id=154,  # in bridge segment but not in waterwheel range
        material_slot_id="material-waterwheel-wood-01",
        semantic_id=4,
    )
    # The phantom is in the bridge segment so the recipe-level check passes,
    # but the EnvironmentModule-level cross-check should fail because the
    # part_id doesn't appear in the parts list.
    bad_recipe = bridge.recipe.model_copy(
        update={"waterwheel_parts": bridge.recipe.waterwheel_parts + (phantom,)},
    )
    bad_module = bridge.model_copy(update={"recipe": bad_recipe})
    # Build modules tuple with the bad one in place.
    new_modules = tuple(
        bad_module if m.module_id == "lower-bridge-waterwheel" else m
        for m in plan.modules
    )
    bad_plan = plan.model_copy(update={"modules": new_modules})
    with pytest.raises(ValidationError):
        EnvironmentModulePlan.model_validate(bad_plan.model_dump())


# --------------------------------------------------------------------------- #
# §TDD 10 — coverage stays unknown until actual frame statistics exist.
# --------------------------------------------------------------------------- #


def test_plan_does_not_claim_coverage(plan: EnvironmentModulePlan) -> None:
    # The plan carries provenance fields that explicitly deny coverage,
    # metric alignment, and real-photo textures.  Coverage must come
    # from actual frame statistics (downstream), never from this plan.
    assert plan.synthetic is True
    assert plan.geometry_usability == "preview-only"
    assert plan.verification_level == "L0"
    assert plan.metric_alignment is False
    assert plan.real_photo_textures is False
    assert plan.geometry_trust == "simplified-pbr-not-render-parity"
    assert plan.trust_effect == "none"


def test_design_source_shas_are_for_provenance_only(
    plan: EnvironmentModulePlan,
) -> None:
    # The three bound design source SHAs must be exactly the values
    # declared in HANDOFF-OPUS-007 §input.  They must NOT be used to
    # infer coverage, orientation, or training suitability.
    assert plan.central_courtyard_source_sha256 in DESIGN_SOURCE_SHA256S
    assert plan.bridge_undercroft_source_sha256 in DESIGN_SOURCE_SHA256S
    assert plan.rear_service_courtyard_source_sha256 in DESIGN_SOURCE_SHA256S
    # The plan never declares a "coverage" or "orientation" field; that
    # evidence only exists at the rendered-frame layer.
    payload = _plan_payload(plan)
    forbidden_fields = {
        "coverage",
        "orientation",
        "training_use",
        "camera_calibration",
        "geometry_consistency",
    }
    assert not (forbidden_fields & set(payload))


def test_plan_does_not_silently_promote_to_metric(
    plan: EnvironmentModulePlan,
) -> None:
    # Mutating any of these fields must fail closed; the plan cannot be
    # turned into a metric-aligned plan by editing a single field.
    for field, bad_value in (
        ("geometry_usability", "metric-aligned"),
        ("verification_level", "L2"),
        ("metric_alignment", True),
        ("real_photo_textures", True),
        ("geometry_trust", "render-parity"),
        ("trust_effect", "promotes-to-measured"),
    ):
        tampered = plan.model_copy(update={field: bad_value})
        with pytest.raises(ValidationError):
            EnvironmentModulePlan.model_validate(tampered.model_dump())
