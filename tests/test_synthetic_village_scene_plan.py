"""Deterministic synthetic-village scene-plan tests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections import Counter

import pytest
from pydantic import ValidationError

import pipeline.synthetic_village.scene_plan as scene_plan
from pipeline.synthetic_village.defaults import build_default_recipe
from pipeline.synthetic_village.scene_plan import (
    MAX_PLACEMENT_ATTEMPTS,
    PlacementError,
    ScenePlan,
    build_scene_plan,
    canonical_scene_plan_bytes,
    load_scene_plan,
    terrain_height_m,
)


def test_default_scene_plan_is_byte_deterministic_and_uses_tracked_recipe():
    first = build_scene_plan()
    second = build_scene_plan(build_default_recipe())

    assert first == second
    assert canonical_scene_plan_bytes(first) == canonical_scene_plan_bytes(second)
    assert first.seed == 20260715
    assert first.extent.model_dump() == {
        "width_m": 700.0,
        "depth_m": 500.0,
        "relief_m": 120.0,
    }
    assert first.placement_attempts <= MAX_PLACEMENT_ATTEMPTS == 10_000


def test_default_scene_has_exact_building_clusters_and_all_spatial_cells():
    plan = build_scene_plan()
    buildings = [item for item in plan.objects if item.semantic_class == "building"]

    assert len(buildings) == 70
    assert Counter(item.cluster for item in buildings) == {
        "creekside": 22,
        "central": 28,
        "upper": 20,
    }
    assert {item.spatial_cell for item in buildings} == {
        f"cell-r{row}-c{column}" for row in range(1, 4) for column in range(1, 5)
    }


def test_default_scene_contains_required_environment_and_prop_elements():
    counts = Counter(item.semantic_class for item in build_scene_plan().objects)

    assert counts["bridge"] == 2
    for semantic_class in (
        "creek",
        "pond",
        "path",
        "field",
        "orchard",
        "bamboo",
        "prop",
    ):
        assert counts[semantic_class] > 0


def test_object_order_ids_and_instance_ids_are_globally_stable():
    plan = build_scene_plan()
    ids = [item.object_id for item in plan.objects]
    instance_ids = [item.instance_id for item in plan.objects]
    buildings = [item for item in plan.objects if item.semantic_class == "building"]

    assert len(ids) == len(set(ids))
    assert instance_ids == list(range(1, len(plan.objects) + 1))
    assert buildings[22].object_id == "building-central-001"
    assert buildings[0].object_id == "building-creekside-001"
    assert buildings[-1].object_id == "building-upper-020"


def test_buildings_respect_extent_separation_and_platform_fit():
    plan = build_scene_plan()
    buildings = [item for item in plan.objects if item.semantic_class == "building"]
    half_width = plan.extent.width_m / 2
    half_depth = plan.extent.depth_m / 2

    for item in buildings:
        assert -half_width <= item.transform.x_m <= half_width
        assert -half_depth <= item.transform.y_m <= half_depth
        assert item.platform_relief_m <= scene_plan.MAX_PLATFORM_RELIEF_M
        assert item.transform.z_m >= terrain_height_m(
            item.transform.x_m,
            item.transform.y_m,
            plan.extent,
        )
    for index, left in enumerate(buildings):
        for right in buildings[index + 1 :]:
            distance = math.hypot(
                left.transform.x_m - right.transform.x_m,
                left.transform.y_m - right.transform.y_m,
            )
            assert distance >= scene_plan.MIN_BUILDING_SEPARATION_M


def test_default_building_footprints_have_horizontal_clearance():
    buildings = [item for item in build_scene_plan().objects if item.semantic_class == "building"]

    for index, left in enumerate(buildings):
        for right in buildings[index + 1 :]:
            assert (
                scene_plan.building_footprint_clearance_m(left, right) + 1e-9
                >= scene_plan.MIN_BUILDING_FOOTPRINT_CLEARANCE_M
            )


def test_scene_contract_is_frozen_strict_canonical_and_fail_closed(tmp_path):
    plan = build_scene_plan()
    path = tmp_path / "scene-plan.json"
    path.write_bytes(canonical_scene_plan_bytes(plan))
    assert load_scene_plan(path) == plan

    with pytest.raises(ValidationError):
        scene_plan.SceneObject.model_validate(
            {**plan.objects[0].model_dump(mode="json"), "unexpected": True},
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ScenePlan.model_validate_json(
            json.dumps({**plan.model_dump(mode="json"), "unexpected": True}),
        )
    outside = plan.model_dump(mode="json")
    outside["objects"][0]["transform"]["x_m"] = 999.0
    with pytest.raises(ValidationError, match="extent"):
        ScenePlan.model_validate_json(json.dumps(outside))

    overlap = plan.model_dump(mode="json")
    overlap_buildings = [
        item for item in overlap["objects"] if item["semantic_class"] == "building"
    ]
    left, right = min(
        (
            (left, right)
            for index, left in enumerate(overlap_buildings)
            for right in overlap_buildings[index + 1 :]
        ),
        key=lambda pair: math.hypot(
            pair[0]["transform"]["x_m"] - pair[1]["transform"]["x_m"],
            pair[0]["transform"]["y_m"] - pair[1]["transform"]["y_m"],
        ),
    )
    for item in (left, right):
        item["transform"]["yaw_deg"] = 0.0
        item["dimensions"]["width_m"] = 10.0
        item["dimensions"]["depth_m"] = 8.0
    right["transform"]["x_m"] = round(left["transform"]["x_m"] + 8.0, 3)
    right["transform"]["y_m"] = left["transform"]["y_m"]
    for item in (left, right):
        x_m, y_m = item["transform"]["x_m"], item["transform"]["y_m"]
        heights = [
            terrain_height_m(x_m + offset_x, y_m + offset_y, plan.extent)
            for offset_x in (-5.0, 5.0)
            for offset_y in (-4.0, 4.0)
        ]
        item["base_z_m"] = round(max(heights), 3)
        item["platform_relief_m"] = round(max(heights) - min(heights), 3)
        item["transform"]["z_m"] = round(
            item["base_z_m"] + item["dimensions"]["height_m"] / 2,
            3,
        )
        item["spatial_cell"] = scene_plan._cell_id(x_m, y_m, plan.extent)
    with pytest.raises(ValidationError, match="footprint clearance"):
        ScenePlan.model_validate_json(json.dumps(overlap))
    with pytest.raises(ValidationError):
        plan.objects[0].transform.x_m = 0.0

    payload = plan.model_dump(mode="json")
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical"):
        load_scene_plan(path)


def test_rejection_sampling_has_a_hard_attempt_limit(monkeypatch):
    monkeypatch.setattr(scene_plan, "_candidate_xy", lambda *_args, **_kwargs: (0.0, 0.0))

    with pytest.raises(PlacementError, match="attempt limit"):
        build_scene_plan(attempt_limit=25)


def test_scene_emits_explicit_topology_and_camera_anchors():
    plan = build_scene_plan()
    by_class = {
        semantic: [item for item in plan.objects if item.semantic_class == semantic]
        for semantic in {item.semantic_class for item in plan.objects}
    }

    assert len(by_class["courtyard"]) >= 4
    assert len(by_class["retaining-wall"]) > 0
    assert any(item.building_role == "community-hall" for item in by_class["building"])
    assert all(item.polyline is not None for item in by_class["creek"] + by_class["path"])
    assert all(
        item.polygon is not None
        for semantic in ("pond", "field", "orchard", "bamboo", "courtyard")
        for item in by_class[semantic]
    )
    creek_id = by_class["creek"][0].object_id
    assert all(item.bridge.crosses_object_id == creek_id for item in by_class["bridge"])
    assert {anchor.anchor_type for anchor in plan.camera_anchors} == {
        "cluster",
        "route",
        "intersection",
        "courtyard",
        "bridge",
    }
    creek = by_class["creek"][0]
    assert (creek.polyline.points[0].x_m, creek.polyline.points[0].y_m) == (
        -340.0,
        -212.0,
    )
    assert (creek.polyline.points[-1].x_m, creek.polyline.points[-1].y_m) == (
        335.0,
        235.0,
    )
    lower = by_class["bridge"][0]
    assert lower.transform.yaw_deg == -55.0
    assert lower.bridge.bank_anchors[0].x_m == -180.736


def test_topology_is_terrain_conforming_connected_and_reserved_from_buildings():
    plan = build_scene_plan()
    for item in plan.objects:
        points = item.polyline.points if item.polyline else ()
        points += item.polygon.ring if item.polygon else ()
        if item.bridge:
            points += item.bridge.bank_anchors
        for point in points:
            assert point.z_m == terrain_height_m(point.x_m, point.y_m, plan.extent)
    assert scene_plan.route_network_connects_required_nodes(plan)
    assert scene_plan.path_bridge_crossings_are_direct(plan)
    for building in (item for item in plan.objects if item.semantic_class == "building"):
        assert not scene_plan.building_overlaps_reserved_feature(building, plan.objects)


def test_terrain_range_quantization_and_canonical_digest_are_fixed():
    plan = build_scene_plan()
    assert plan.terrain_min_m == 0.0
    assert plan.terrain_max_m == 120.0
    assert plan.terrain_model_id == "nantai-terrain-height-v1"
    assert plan.terrain_max_m - plan.terrain_min_m == pytest.approx(120.0)
    for item in plan.objects:
        for value in (
            item.transform.x_m,
            item.transform.y_m,
            item.transform.z_m,
            item.dimensions.width_m,
            item.dimensions.depth_m,
            item.dimensions.height_m,
        ):
            assert value * 1000 == pytest.approx(round(value * 1000), abs=1e-8)
        assert item.transform.yaw_deg % 5 == 0
    digest = hashlib.sha256(canonical_scene_plan_bytes(plan)).hexdigest()
    assert digest == "1a05b678a61ca15228ac3be219864699d0ad333e9a2210cb16277147a32283d4"


def test_scene_validator_recomputes_derived_fields_and_topology():
    plan = build_scene_plan()

    for field, value, message in (
        ("spatial_cell", "cell-r3-c4", "spatial cell"),
        ("base_z_m", 99.0, "base"),
    ):
        payload = plan.model_dump(mode="json")
        payload["objects"][0][field] = value
        with pytest.raises(ValidationError, match=message):
            ScenePlan.model_validate_json(json.dumps(payload))

    payload = plan.model_dump(mode="json")
    nonbuilding = next(item for item in payload["objects"] if item["semantic_class"] != "building")
    nonbuilding["cluster"] = "central"
    with pytest.raises(ValidationError, match="nonbuilding|non-building"):
        ScenePlan.model_validate_json(json.dumps(payload))

    payload = plan.model_dump(mode="json")
    bridge = next(item for item in payload["objects"] if item["semantic_class"] == "bridge")
    bridge["bridge"]["bank_anchors"][1] = bridge["bridge"]["bank_anchors"][0]
    with pytest.raises(ValidationError, match="bridge|topology"):
        ScenePlan.model_validate_json(json.dumps(payload))

    payload = plan.model_dump(mode="json")
    payload["objects"][-1]["object_id"] = "prop-rural-999"
    with pytest.raises(ValidationError, match="ID sequence"):
        ScenePlan.model_validate_json(json.dumps(payload))

    payload = plan.model_dump(mode="json")
    creek = next(item for item in payload["objects"] if item["semantic_class"] == "creek")
    creek["dimensions"]["width_m"] = 1.0
    with pytest.raises(ValidationError, match="non-building"):
        ScenePlan.model_validate_json(json.dumps(payload))

    payload = plan.model_dump(mode="json")
    payload["camera_anchors"][0]["position"]["x_m"] += 1.0
    with pytest.raises(ValidationError, match="camera anchors"):
        ScenePlan.model_validate_json(json.dumps(payload))

    payload = plan.model_dump(mode="json")
    payload["terrain_model_id"] = "unknown-terrain"
    with pytest.raises(ValidationError, match="terrain_model_id"):
        ScenePlan.model_validate_json(json.dumps(payload))


def test_loader_wraps_errors_and_rejects_redirected_parent(tmp_path):
    with pytest.raises(scene_plan.ScenePlanError, match="scene plan"):
        load_scene_plan(tmp_path / "missing.json")

    canonical = canonical_scene_plan_bytes(build_scene_plan())
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(
        canonical.replace(
            b'  "schema_version": 1',
            b'  "schema_version": 1,\n  "schema_version": 1',
            1,
        ),
    )
    with pytest.raises(scene_plan.ScenePlanError, match="duplicate JSON key"):
        load_scene_plan(duplicate)

    if os.name != "nt":
        return
    target = tmp_path / "real"
    target.mkdir()
    path = target / "plan.json"
    path.write_bytes(canonical)
    junction = tmp_path / "redirect"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    try:
        with pytest.raises(scene_plan.ScenePlanError, match="redirected"):
            load_scene_plan(junction / "plan.json")
    finally:
        junction.rmdir()


def test_default_buildings_and_attempt_count_are_exactly_seed_bound(tmp_path):
    plan = build_scene_plan()

    attempts = plan.model_dump(mode="json")
    attempts["placement_attempts"] += 1
    with pytest.raises(ValidationError, match="attempt"):
        ScenePlan.model_validate_json(json.dumps(attempts))

    material = plan.model_dump(mode="json")
    material["objects"][0]["material_family"] = "pale-plaster"
    with pytest.raises(ValidationError, match="deterministic building"):
        ScenePlan.model_validate_json(json.dumps(material))

    roles = plan.model_dump(mode="json")
    central = [item for item in roles["objects"] if item["cluster"] == "central"]
    central[0]["building_role"] = "residence"
    central[1]["building_role"] = "community-hall"
    with pytest.raises(ValidationError, match="deterministic building"):
        ScenePlan.model_validate_json(json.dumps(roles))

    shifted = plan.model_dump(mode="json")
    building = shifted["objects"][0]
    building["transform"]["x_m"] += 0.001
    x_m = building["transform"]["x_m"]
    y_m = building["transform"]["y_m"]
    dimensions = scene_plan.Dimensions.model_validate(building["dimensions"])
    corners = scene_plan._footprint_corners_values(
        x_m,
        y_m,
        building["transform"]["yaw_deg"],
        dimensions,
    )
    heights = [terrain_height_m(x, y, plan.extent) for x, y in corners]
    building["base_z_m"] = round(max(heights), 3)
    building["platform_relief_m"] = round(max(heights) - min(heights), 3)
    building["transform"]["z_m"] = round(
        building["base_z_m"] + building["dimensions"]["height_m"] / 2,
        3,
    )
    building["spatial_cell"] = scene_plan._cell_id(x_m, y_m, plan.extent)
    with pytest.raises(ValidationError, match="deterministic building"):
        ScenePlan.model_validate_json(json.dumps(shifted))

    tampered_path = tmp_path / "tampered-canonical.json"
    tampered_path.write_text(
        json.dumps(material, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(scene_plan.ScenePlanError, match="deterministic building"):
        load_scene_plan(tampered_path)
