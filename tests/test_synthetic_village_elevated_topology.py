"""Elevated walkable topology must be real scene-bound graph evidence."""

from __future__ import annotations

import hashlib
import json
import math

import pytest

from pipeline.synthetic_village.canary import VISUAL_MATERIAL_SLOT_IDS
from pipeline.synthetic_village.elevated_topology import (
    ELEVATED_TOPOLOGY_SCHEMA,
    MIN_ELEVATED_CLEARANCE_M,
    ElevatedTopologyError,
    ElevatedTopologyPlan,
    build_elevated_topology_plan,
    canonical_elevated_topology_bytes,
    verify_elevated_topology_plan,
)
from pipeline.synthetic_village.scene_plan import (
    build_scene_plan,
    canonical_scene_plan_bytes,
    terrain_height_m,
)


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.dist(point, start)
    fraction = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / length_sq
    fraction = max(0.0, min(1.0, fraction))
    nearest = (start[0] + fraction * dx, start[1] + fraction * dy)
    return math.dist(point, nearest)


def _plan_payload() -> dict[str, object]:
    return json.loads(canonical_elevated_topology_bytes(build_elevated_topology_plan()))


def test_plan_binds_the_exact_tracked_scene_without_changing_scene_plan_v1() -> None:
    scene = build_scene_plan()
    plan = build_elevated_topology_plan(scene)

    assert plan.schema_version == ELEVATED_TOPOLOGY_SCHEMA
    assert plan.scene_plan_id == scene.plan_id
    assert plan.scene_plan_sha256 == hashlib.sha256(
        canonical_scene_plan_bytes(scene)
    ).hexdigest()
    assert plan.synthetic is True
    assert plan.verification_level == "L2"
    assert plan.geometry_trust == "simplified-pbr-not-render-parity"
    assert plan.semantic_id == 14
    assert all(item.semantic_class != "elevated-walkway" for item in scene.objects)


def test_four_components_have_stable_kinds_and_reserved_instance_ids() -> None:
    plan = build_elevated_topology_plan()

    assert [
        (component.component_id, component.component_kind, component.instance_id)
        for component in plan.components
    ] == [
        ("elevated-switchback-stair-v1", "switchback-stair", 127),
        ("covered-timber-gallery-v1", "covered-timber-gallery", 128),
        ("terrace-ramp-junction-v1", "terrace-ramp-junction", 129),
        ("cross-level-covered-passage-v1", "cross-level-covered-passage", 130),
    ]
    edge_ids = [edge.edge_id for edge in plan.edges]
    assert edge_ids == sorted(edge_ids)
    assert {
        edge_id
        for component in plan.components
        for edge_id in component.edge_ids
    } == set(edge_ids)
    assert sum(len(component.edge_ids) for component in plan.components) == len(
        edge_ids
    )
    assert {
        material_id
        for component in plan.components
        for material_id in component.material_slot_ids
    } <= set(VISUAL_MATERIAL_SLOT_IDS)


def test_two_elevated_alternatives_form_two_explicit_ground_connected_loops() -> None:
    plan = build_elevated_topology_plan()

    assert plan.summary.loop_count == 2
    assert plan.summary.ground_attachment_count == 4
    assert plan.summary.component_count == 4
    assert {row.loop_id for row in plan.loops} == {"central-loop", "upper-loop"}
    assert all(row.connected for row in plan.loops)
    assert all(row.ground_attachment_count == 2 for row in plan.loops)
    assert all(row.edge_count >= 3 for row in plan.loops)
    loop_edges = [set(row.edge_ids) for row in plan.loops]
    assert loop_edges[0].isdisjoint(loop_edges[1])


def test_ground_attachments_lie_on_the_declared_real_path_and_match_terrain() -> None:
    scene = build_scene_plan()
    plan = build_elevated_topology_plan(scene)
    paths = {
        item.object_id: item
        for item in scene.objects
        if item.semantic_class == "path"
    }

    ground = [node for node in plan.nodes if node.level == "ground"]
    assert len(ground) == 4
    for node in ground:
        assert node.ground_route_ref in paths
        route = paths[node.ground_route_ref]
        points = [(point.x_m, point.y_m) for point in route.polyline.points]
        distance = min(
            _point_segment_distance(
                (node.position_m[0], node.position_m[1]), start, end
            )
            for start, end in zip(points, points[1:], strict=False)
        )
        assert distance <= route.polyline.width_m / 2 + 1e-6
        assert node.position_m[2] == pytest.approx(
            terrain_height_m(node.position_m[0], node.position_m[1], scene.extent),
            abs=1e-9,
        )


def test_elevated_nodes_and_edges_have_absolute_clearance_and_buildable_envelopes() -> None:
    scene = build_scene_plan()
    plan = build_elevated_topology_plan(scene)

    elevated = [node for node in plan.nodes if node.level == "elevated"]
    assert elevated
    for node in elevated:
        assert node.ground_route_ref is None
        terrain = terrain_height_m(node.position_m[0], node.position_m[1], scene.extent)
        assert node.position_m[2] - terrain >= MIN_ELEVATED_CLEARANCE_M

    by_node = {node.node_id: node for node in plan.nodes}
    for edge in plan.edges:
        assert edge.centerline[0].position_m == by_node[edge.start_node_id].position_m
        assert edge.centerline[-1].position_m == by_node[edge.end_node_id].position_m
        assert edge.width_m >= 1.8
        assert edge.collision.deck_thickness_m > 0
        assert edge.collision.railing_height_m >= 1.0
        if edge.collision.covered:
            assert edge.collision.head_clearance_m >= 2.1
        for point in edge.centerline:
            assert all(math.isfinite(value) for value in point.position_m)
            assert all(
                abs(value * 1000 - round(value * 1000)) <= 1e-7
                for value in point.position_m
            )

    verify_elevated_topology_plan(plan, scene)


def test_all_ground_nodes_participate_in_edges() -> None:
    """GLM-P0 (FEEDBACK-HANDOFF-CODEX-012): all ground nodes must
    participate in at least one edge.  Isolated anchor nodes are rejected
    by Codex's directive: "不要登记孤立节点来过距离门"."""
    scene = build_scene_plan()
    plan = build_elevated_topology_plan(scene)

    used_node_ids = {
        edge.start_node_id for edge in plan.edges
    } | {edge.end_node_id for edge in plan.edges}
    ground_nodes = [n for n in plan.nodes if n.level == "ground"]
    for node in ground_nodes:
        assert node.node_id in used_node_ids, (
            f"ground node {node.node_id} is isolated (not in any edge); "
            f"Codex rejects isolated nodes"
        )
    assert plan.summary.ground_attachment_count == len(ground_nodes)
    verify_elevated_topology_plan(plan, scene)


def test_verifier_rejects_scene_digest_building_and_water_collisions() -> None:
    scene = build_scene_plan()
    clean = build_elevated_topology_plan(scene)

    changed_digest = clean.model_copy(update={"scene_plan_sha256": "0" * 64})
    with pytest.raises(ElevatedTopologyError, match="scene.*digest"):
        verify_elevated_topology_plan(changed_digest, scene)

    building = next(item for item in scene.objects if item.semantic_class == "building")
    payload = _plan_payload()
    gallery = next(
        edge
        for edge in payload["edges"]
        if edge["component_kind"] == "covered-timber-gallery"
    )
    gallery["centerline"][1]["position_m"][:2] = [
        building.transform.x_m,
        building.transform.y_m,
    ]
    colliding = ElevatedTopologyPlan.model_validate_json(json.dumps(payload))
    with pytest.raises(ElevatedTopologyError, match="building"):
        verify_elevated_topology_plan(colliding, scene)

    creek = next(item for item in scene.objects if item.semantic_class == "creek")
    payload = _plan_payload()
    passage = next(
        edge
        for edge in payload["edges"]
        if edge["component_kind"] == "cross-level-covered-passage"
        and len(edge["centerline"]) >= 3
    )
    creek_point = creek.polyline.points[len(creek.polyline.points) // 2]
    passage["centerline"][1]["position_m"][:2] = [
        creek_point.x_m,
        creek_point.y_m,
    ]
    wet = ElevatedTopologyPlan.model_validate_json(json.dumps(payload))
    with pytest.raises(ElevatedTopologyError, match="water|drainage|creek"):
        verify_elevated_topology_plan(wet, scene)


def test_canonical_bytes_are_deterministic_path_free_and_strictly_reloadable() -> None:
    plan = build_elevated_topology_plan()
    first = canonical_elevated_topology_bytes(plan)
    second = canonical_elevated_topology_bytes(build_elevated_topology_plan())

    assert first == second
    assert first.endswith(b"\n")
    assert b"/Users/" not in first
    assert b"component-elevated-switchback-stair-01.png" not in first
    assert ElevatedTopologyPlan.model_validate_json(first) == plan

    payload = json.loads(first)
    payload["nodes"][1]["node_id"] = payload["nodes"][0]["node_id"]
    with pytest.raises(ValueError, match="node IDs|unique"):
        ElevatedTopologyPlan.model_validate_json(json.dumps(payload))
