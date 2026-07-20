"""生产档 (180 相机) profile 的契约测试。

核心: 生产档【绝不】允许放大或复用 canary 的 24 相机契约, 且【绝不】允许
用几何撒点冒充"沿可行走拓扑布点"。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import camera_plan as canary_camera_plan
from pipeline.synthetic_village.elevated_topology import (
    build_elevated_topology_plan,
    canonical_elevated_topology_bytes,
)
from pipeline.synthetic_village.production_profile import (
    GROUP_SPECS,
    PRODUCTION_JOURNAL_SCHEMA,
    PRODUCTION_PROFILE_ID,
    TARGET_CAMERA_COUNT,
    ElevatedPolylineTopologySource,
    ProductionProfileError,
    build_production_camera_plan,
    canonical_production_plan_bytes,
    production_batch_slice,
    production_camera_registry_digest,
    resolve_topology_sources,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan, terrain_height_m

REPO_ROOT = Path(__file__).resolve().parents[1]


def _distance_to_polyline_3d(
    point: tuple[float, float, float],
    polyline: tuple[tuple[float, float, float], ...],
) -> float:
    best = math.inf
    for start, end in zip(polyline, polyline[1:], strict=False):
        delta = tuple(end[index] - start[index] for index in range(3))
        length_sq = sum(value * value for value in delta)
        offset = tuple(point[index] - start[index] for index in range(3))
        fraction = (
            0.0
            if length_sq == 0.0
            else max(
                0.0,
                min(1.0, sum(offset[index] * delta[index] for index in range(3)) / length_sq),
            )
        )
        projected = tuple(
            start[index] + delta[index] * fraction for index in range(3)
        )
        best = min(best, math.dist(point, projected))
    return best


# --------------------------------------------------------------------------
# 铁律 1: 绝不动 24 canary 契约
# --------------------------------------------------------------------------


def test_canary_24_camera_contract_is_untouched() -> None:
    """canary 是快速门禁, 放大它就等于拆掉它。"""
    field = canary_camera_plan.CameraPlan.model_fields["cameras"]
    constraints = {meta.__class__.__name__: meta for meta in field.metadata}
    assert any(getattr(m, "min_length", None) == 24 for m in field.metadata), constraints
    assert any(getattr(m, "max_length", None) == 24 for m in field.metadata), constraints


def test_canary_sequence_index_and_counts_still_capped_at_24() -> None:
    source = (REPO_ROOT / "pipeline" / "synthetic_village" / "camera_plan.py").read_text(
        encoding="utf-8"
    )
    assert "sequence_index: int = Field(ge=1, le=24)" in source
    assert "cameras: tuple[CameraPose, ...] = Field(min_length=24, max_length=24)" in source
    assert len(canary_camera_plan._EXPECTED_IDS) == 24


def test_canary_render_camera_ids_still_exactly_24() -> None:
    from pipeline.synthetic_village import canary

    assert len(canary.RENDER_CAMERA_IDS) == 24


def test_production_journal_schema_is_not_the_canary_journal_schema() -> None:
    """journal 不与 canary 混用 —— schema 字符串不同 => render_id 自动分叉。"""
    from pipeline.synthetic_village import canary

    assert PRODUCTION_JOURNAL_SCHEMA != canary.RENDER_JOURNAL_SCHEMA


def test_production_camera_ids_are_rejected_by_the_canary_render_contract() -> None:
    """生产档相机 ID 不得落进 canary 的 4 个类别 —— 结构上不可能与 canary 混淆。"""
    canary_pattern = re.compile(r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")
    plan = build_production_camera_plan()
    for camera in plan.cameras:
        assert not canary_pattern.match(camera.camera_id), camera.camera_id


# --------------------------------------------------------------------------
# 铁律 2: 不许用几何撒点冒充"沿可行走图"
# --------------------------------------------------------------------------


def test_elevated_pedestrian_resolves_all_real_walkable_edges() -> None:
    scene = build_scene_plan()
    resolved = resolve_topology_sources(scene)
    elevated = resolved["elevated-pedestrian"]
    assert all(isinstance(row, ElevatedPolylineTopologySource) for row in elevated)
    topology = build_elevated_topology_plan(scene)
    assert {row.topology_ref for row in elevated} == {
        edge.edge_id for edge in topology.edges
    }
    assert {row.loop_id for row in elevated} == {"central-loop", "upper-loop"}
    assert all(row.length_m > 0 and row.half_width_m >= 0.9 for row in elevated)


def test_plan_places_the_full_target_from_verified_topology() -> None:
    plan = build_production_camera_plan()
    assert plan.camera_count == len(plan.cameras)
    assert plan.declared_target_count == TARGET_CAMERA_COUNT
    assert plan.camera_count == TARGET_CAMERA_COUNT
    assert plan.complete is True
    assert plan.unplaced_groups == ()
    topology = build_elevated_topology_plan()
    assert plan.elevated_topology_sha256 == hashlib.sha256(
        canonical_elevated_topology_bytes(topology)
    ).hexdigest()


def test_exactly_48_elevated_cameras_follow_real_3d_centerlines() -> None:
    topology = build_elevated_topology_plan()
    edges = {edge.edge_id: edge for edge in topology.edges}
    cameras = [
        camera
        for camera in build_production_camera_plan().cameras
        if camera.group_id == "elevated-pedestrian"
    ]

    assert len(cameras) == 48
    assert [camera.camera_id for camera in cameras] == [
        f"camera-elevated-pedestrian-{index:03d}"
        for index in range(1, 49)
    ]
    assert {camera.topology_ref for camera in cameras} == set(edges)
    for camera in cameras:
        edge = edges[camera.topology_ref]
        deck_point = (
            camera.position_m[0],
            camera.position_m[1],
            camera.position_m[2] - camera.eye_height_m,
        )
        assert _distance_to_polyline_3d(
            deck_point,
            tuple(point.position_m for point in edge.centerline),
        ) <= 0.005, camera.camera_id


def test_each_elevated_edge_is_observed_in_both_travel_directions() -> None:
    topology = build_elevated_topology_plan()
    edges = {edge.edge_id: edge for edge in topology.edges}
    directions: dict[str, set[int]] = {edge_id: set() for edge_id in edges}
    for camera in build_production_camera_plan().cameras:
        if camera.group_id != "elevated-pedestrian":
            continue
        edge = edges[camera.topology_ref]
        forward = (
            edge.centerline[-1].position_m[0] - edge.centerline[0].position_m[0],
            edge.centerline[-1].position_m[1] - edge.centerline[0].position_m[1],
            edge.centerline[-1].position_m[2] - edge.centerline[0].position_m[2],
        )
        gaze = tuple(
            camera.look_at_m[index] - camera.position_m[index]
            for index in range(3)
        )
        dot = sum(forward[index] * gaze[index] for index in range(3))
        assert abs(dot) > 1e-3
        directions[camera.topology_ref].add(1 if dot > 0 else -1)

    assert directions and all(signs == {-1, 1} for signs in directions.values())


def test_ground_route_cameras_lie_on_the_real_walkable_path_network() -> None:
    """每个 ground-route 相机中心必须在真实 path polyline 的 width/2 之内。"""
    scene = build_scene_plan()
    segments: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    for item in scene.objects:
        if item.semantic_class == "path":
            pts = [(p.x_m, p.y_m) for p in item.polyline.points]
            for a, b in zip(pts, pts[1:], strict=False):
                segments.append((a, b, item.polyline.width_m))

    def distance_to_network(x: float, y: float) -> float:
        best = math.inf
        for (ax, ay), (bx, by), _w in segments:
            dx, dy = bx - ax, by - ay
            length_sq = dx * dx + dy * dy
            if length_sq == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_sq))
            px, py = ax + t * dx, ay + t * dy
            best = min(best, math.hypot(x - px, y - py))
        return best

    plan = build_production_camera_plan()
    ground = [c for c in plan.cameras if c.group_id == "ground-route"]
    assert ground, "ground-route 组必须有相机"
    for camera in ground:
        x, y, _z = camera.position_m
        assert distance_to_network(x, y) <= 3.2 / 2 + 1e-6, (camera.camera_id, x, y)


def test_ground_route_is_not_a_circle_around_the_centre() -> None:
    """反证'按一个圆平均撒点': 到场景中心的半径必须【显著不等】。"""
    plan = build_production_camera_plan()
    ground = [c for c in plan.cameras if c.group_id == "ground-route"]
    radii = [math.hypot(c.position_m[0], c.position_m[1]) for c in ground]
    spread = (max(radii) - min(radii)) / max(radii)
    assert spread > 0.5, f"半径过于均匀, 疑似圆形撒点: {spread:.3f}"


def test_ground_route_cameras_follow_terrain_relief_not_a_constant_height() -> None:
    scene = build_scene_plan()
    plan = build_production_camera_plan()
    ground = [c for c in plan.cameras if c.group_id == "ground-route"]
    heights = [c.position_m[2] for c in ground]
    assert max(heights) - min(heights) > 20.0, "地面漫游相机高度必须跟随地形起伏"
    for camera in ground:
        x, y, z = camera.position_m
        # 位置落在声明的毫米栅格上 (translation_quantization_m=0.001), 且 x/y 的
        # 量化会经地形坡度传播到 z, 故容差取 5mm —— 不是精确相等。
        assert z - terrain_height_m(x, y, scene.extent) == pytest.approx(
            camera.eye_height_m, abs=5e-3
        )


def test_environment_corridor_cameras_track_the_creek_polyline() -> None:
    scene = build_scene_plan()
    creek = next(o for o in scene.objects if o.semantic_class == "creek")
    pts = [(p.x_m, p.y_m) for p in creek.polyline.points]
    segments = list(zip(pts, pts[1:], strict=False))

    def distance_to_creek(x: float, y: float) -> float:
        best = math.inf
        for (ax, ay), (bx, by) in segments:
            dx, dy = bx - ax, by - ay
            length_sq = dx * dx + dy * dy
            if length_sq == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_sq))
            best = min(best, math.hypot(x - (ax + t * dx), y - (ay + t * dy)))
        return best

    plan = build_production_camera_plan()
    corridor = [c for c in plan.cameras if c.group_id == "environment-corridor"]
    assert corridor
    for camera in corridor:
        x, y, _z = camera.position_m
        assert distance_to_creek(x, y) <= 8.0, camera.camera_id


def test_no_camera_is_placed_inside_a_building_or_below_terrain_clearance() -> None:
    """相机绝不允许落在建筑体内或低于地形净空 —— 这类位姿是不可渲染的假证据。"""
    import numpy as np

    from pipeline.synthetic_village.camera_plan import (
        MIN_TERRAIN_CLEARANCE_M,
        _inside_building_obb,
    )

    scene = build_scene_plan()
    buildings = [o for o in scene.objects if o.semantic_class == "building"]
    plan = build_production_camera_plan()
    for camera in plan.cameras:
        position = np.array(camera.position_m, dtype=float)
        x, y, z = camera.position_m
        assert z - terrain_height_m(x, y, scene.extent) >= MIN_TERRAIN_CLEARANCE_M - 1e-6, (
            camera.camera_id
        )
        for building in buildings:
            assert not _inside_building_obb(position, building), (
                camera.camera_id,
                building.object_id,
            )


def test_adjacent_ground_route_cameras_are_close_enough_to_share_view() -> None:
    """相邻节点必须形成连续共视 —— 间距不得超过 30m。"""
    plan = build_production_camera_plan()
    ground = [c for c in plan.cameras if c.group_id == "ground-route"]
    by_route: dict[str, list] = {}
    for camera in ground:
        by_route.setdefault(camera.topology_ref, []).append(camera)
    checked = 0
    for cameras in by_route.values():
        ordered = sorted(cameras, key=lambda c: c.arc_length_m or 0.0)
        for left, right in zip(ordered, ordered[1:], strict=False):
            gap = math.dist(left.position_m, right.position_m)
            assert gap <= 30.0, (left.camera_id, right.camera_id, gap)
            checked += 1
    assert checked > 0


def test_ground_route_samples_span_each_whole_route_segment() -> None:
    """相机必须【铺满】每条路段, 不能挤在一小段里却声称覆盖了整条路线。

    (变异 M8 暴露: 只测'相邻间距 <= 30m'时, 把全部相机压进路段前 5%
    反而让间距更小 —— 会静默通过。)
    """
    scene = build_scene_plan()
    lengths: dict[str, float] = {}
    for item in scene.objects:
        if item.semantic_class == "path":
            pts = [(p.x_m, p.y_m) for p in item.polyline.points]
            lengths[item.object_id] = sum(
                math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False)
            )

    plan = build_production_camera_plan()
    by_route: dict[str, list[float]] = {}
    for camera in plan.cameras:
        if camera.group_id == "ground-route":
            by_route.setdefault(camera.topology_ref, []).append(camera.arc_length_m or 0.0)

    assert set(by_route) == set(lengths), "每条真实路段都必须分到相机"
    for route_id, arcs in by_route.items():
        total = lengths[route_id]
        assert min(arcs) <= total * 0.15, (route_id, "路段起始段没有相机")
        assert max(arcs) >= total * 0.85, (route_id, "路段末段没有相机")
        span = (max(arcs) - min(arcs)) / total
        assert span >= 0.7, (route_id, f"相机只铺满路段的 {span:.1%}")


# --------------------------------------------------------------------------
# 铁律 5: 相机增多绝不提升 geometry trust
# --------------------------------------------------------------------------


def test_more_cameras_never_upgrade_trust() -> None:
    plan = build_production_camera_plan()
    assert plan.synthetic is True
    assert plan.geometry_trust == "simplified-pbr-not-render-parity"
    assert plan.verification_level == "L2"


def test_audit_overview_is_explicitly_disclosed_not_disguised_as_ground() -> None:
    """audit-overview 必须显式标记, 不冒充地面漫游视角。"""
    plan = build_production_camera_plan()
    overview = [c for c in plan.cameras if c.group_id == "audit-overview"]
    assert overview
    for camera in overview:
        assert camera.camera_id.startswith("camera-audit-overview-")
        assert camera.audit_only is True
        assert "not-a-pedestrian-viewpoint" in camera.disclosure
    for camera in plan.cameras:
        if camera.group_id != "audit-overview":
            assert camera.audit_only is False


# --------------------------------------------------------------------------
# 铁律 4: 稳定 batch 切片 / 子集
# --------------------------------------------------------------------------


def test_batch_slice_is_stable_and_partitions_exactly_once() -> None:
    plan = build_production_camera_plan()
    ids = [c.camera_id for c in plan.cameras]
    seen: list[str] = []
    for index in range(4):
        batch = production_batch_slice(plan, batch_index=index, batch_count=4)
        assert batch == production_batch_slice(plan, batch_index=index, batch_count=4)
        seen.extend(batch)
    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(set(seen))


def test_batch_slice_rejects_out_of_range() -> None:
    plan = build_production_camera_plan()
    with pytest.raises(ProductionProfileError):
        production_batch_slice(plan, batch_index=4, batch_count=4)
    with pytest.raises(ProductionProfileError):
        production_batch_slice(plan, batch_index=0, batch_count=0)


def test_registry_digest_is_deterministic_and_binds_camera_ids() -> None:
    plan = build_production_camera_plan()
    assert production_camera_registry_digest(plan) == production_camera_registry_digest(plan)
    assert re.fullmatch(r"[0-9a-f]{64}", production_camera_registry_digest(plan))


def test_plan_bytes_are_canonical_and_reloadable() -> None:
    plan = build_production_camera_plan()
    raw = canonical_production_plan_bytes(plan)
    assert raw.endswith(b"\n")
    assert json.loads(raw)["profile_id"] == PRODUCTION_PROFILE_ID


def test_group_specs_match_the_handoff_counts() -> None:
    counts = {spec.group_id: spec.target_count for spec in GROUP_SPECS}
    assert counts == {
        "ground-route": 72,
        "elevated-pedestrian": 48,
        "perimeter-inward": 32,
        "environment-corridor": 16,
        "audit-overview": 12,
    }
    assert sum(counts.values()) == TARGET_CAMERA_COUNT


# --------------------------------------------------------------------------
# 没做的必须【说出来】: 交付物不许静默漏掉整条需求
#
# req 5 (近重复 pose / 孤立相机 / 坏帧 / 过低有效像素占比 fail-closed) 一行都
# 没实现; req 6 (两个闭环给 COLMAP loop closure) 因 48 相机 fail-closed 而
# 结构上不可能满足。两条都【不在】任何"做不到/没做"清单里 —— 读者会以为
# 它们落地了。这正是"不假装可以又不说实际问题"要禁止的。
# --------------------------------------------------------------------------


def test_plan_declares_every_requirement_it_did_not_deliver() -> None:
    from pipeline.synthetic_village.production_profile import UNDELIVERED_REQUIREMENT_IDS

    plan = build_production_camera_plan()
    declared = {row.requirement_id for row in plan.undelivered_requirements}
    assert declared == set(UNDELIVERED_REQUIREMENT_IDS)
    assert "req-5-pose-quality-fail-closed" in declared
    assert "req-6-route-loop-closure" not in declared
    for row in plan.undelivered_requirements:
        assert row.status in {"not-implemented", "structurally-unreachable"}
        assert len(row.reason) >= 20
        assert row.reason != row.requirement_id


def test_req3_front_back_coverage_is_declared_undelivered() -> None:
    """req 3 (每个组件至少有正面和反向覆盖) 【一行没实现】, 所以必须进这张表。

    `undelivered_requirements` 自述"空元组的含义是全部交付", 所以 req 3 缺席
    等于在【机器可读层面断言它已交付】—— Codex 会据此安排后续工作。

    归因必须精确: 缺的是 object_registry 里的【朝向】(一个可以补上的输入),
    【不是】"这份证据做不到"。coverage_audit 确实从被 journal 锚定的 normal 层
    交付了 observed_normal_angular_spread_deg —— 但那条量说的是"看没看到不同
    的面", 不是"哪个面是正面", 所以它【不能】算作 req 3 的交付。
    """

    plan = build_production_camera_plan()
    row = next(
        (r for r in plan.undelivered_requirements if r.requirement_id.startswith("req-3")),
        None,
    )
    assert row is not None, "req 3 没实现却不在 undelivered_requirements 里 = 声称已交付"
    # "没做"就说没做 —— 不许说成"做不到"
    assert row.status == "not-implemented"
    assert "orientation" in row.reason
    # 必须指向那条【确实交付了】的连续量, 且说明它顶替不了 req 3
    assert "observed_normal_angular_spread_deg" in row.reason


def test_undelivered_requirements_never_fake_a_delivered_scalar() -> None:
    """绝不允许用未实现检测的 0 冒充"已检测"。

    一个恒 0 的标量读起来像"检测过, 没问题" —— 而实际是"根本没检测"。
    """

    plan = build_production_camera_plan()
    payload = json.loads(canonical_production_plan_bytes(plan))
    for faked in (
        "isolated_cameras",
        "near_duplicate_pairs",
        "components_with_three_view_support",
    ):
        assert faked not in payload, f"未实现的检测不得以标量形式出现: {faked}"


def test_req5_reason_distinguishes_delivered_frame_gate_from_missing_pose_gates() -> None:
    """req 5 整体未交付，但 reason 不能抹掉已经存在的逐帧质量门。

    `render-production-local` 已经把渲染失败/超时和 operator-selected
    valid-pixel threshold 写入 180 帧 journal。计划仍不能声称 req 5 交付，
    因为近重复阈值、孤立相机和只看天空/地面的语义坏帧检测尚未实现。
    """

    plan = build_production_camera_plan()
    row = next(
        r
        for r in plan.undelivered_requirements
        if r.requirement_id == "req-5-pose-quality-fail-closed"
    )

    assert row.status == "not-implemented"
    assert "local production runner" in row.reason
    assert "valid-pixel" in row.reason
    assert "implemented" in row.reason
    assert "near-duplicate" in row.reason
    assert "isolated" in row.reason
    assert "sky/ground" in row.reason
    assert "no renderer exists" not in row.reason


def test_req6_loop_closure_is_backed_by_two_verified_route_loops() -> None:
    plan = build_production_camera_plan()
    assert {
        row.loop_id for row in plan.route_loops
    } == {"central-loop", "upper-loop"}
    assert all(row.ground_connected for row in plan.route_loops)
    assert all(len(row.ground_attachment_node_ids) == 2 for row in plan.route_loops)
    assert all(len(row.elevated_edge_ids) >= 3 for row in plan.route_loops)
    assert "req-6-route-loop-closure" not in {
        row.requirement_id for row in plan.undelivered_requirements
    }


def test_route_loop_and_group_summaries_reject_external_mutation() -> None:
    payload = _plan_payload()
    payload["route_loops"][0]["elevated_edge_ids"][0] = "fabricated-edge"
    with pytest.raises(ValidationError, match="route loop evidence"):
        _reload(payload)

    payload = _plan_payload()
    payload["group_coverage"][1]["camera_count"] -= 1
    with pytest.raises(ValidationError, match="group coverage"):
        _reload(payload)


def test_pose_separation_evidence_reports_the_distribution_without_inventing_a_threshold() -> None:
    """req 5 没实现 —— 但【实测分布】可以如实给出, 让 Codex 自己判断。

    绝不编阈值: "多近算近重复" 定不出来, 就报分布 + 显式声明没有阈值,
    绝不挑一个数假装它是判据。
    """

    from pipeline.synthetic_village.production_profile import pose_separation_evidence

    plan = build_production_camera_plan()
    evidence = pose_separation_evidence(plan)

    assert evidence["threshold"] is None
    assert "no-threshold" in evidence["disclaimer"]
    assert evidence["pair_count"] > 0
    assert evidence["nearest_pair_m"] > 0.0

    # 分布必须是【实算】的: 最近一对必须真的是最近的
    nearest = evidence["nearest_pair"]
    by_id = {c.camera_id: c.position_m for c in plan.cameras}
    recomputed = math.dist(by_id[nearest[0]], by_id[nearest[1]])
    # 报告值是 round(..., 3), 故容差取 1e-3 —— 不是精确相等
    assert recomputed == pytest.approx(evidence["nearest_pair_m"], abs=1e-3)
    everything = [
        math.dist(a.position_m, b.position_m)
        for index, a in enumerate(plan.cameras)
        for b in plan.cameras[index + 1 :]
    ]
    assert min(everything) == pytest.approx(evidence["nearest_pair_m"], abs=1e-3)
    assert len(everything) == evidence["pair_count"]


# --------------------------------------------------------------------------
# 常量必须【真的约束】它声称约束的东西
# --------------------------------------------------------------------------


def test_route_spacing_constant_actually_constrains_placement() -> None:
    """MAX_GROUND_ROUTE_CAMERA_SPACING_M 以前是死常量 —— 全仓库无任何引用, 测试硬编码 30.0。

    有人按"改配置"的直觉把它调成 15.0 收紧间距, 代码行为完全不变 (它没被任何
    布点逻辑读取), 测试也照绿 —— 常量与它声称约束的东西之间没有连线。
    """

    from pipeline.synthetic_village import production_profile
    from pipeline.synthetic_village.production_profile import (
        MAX_GROUND_ROUTE_CAMERA_SPACING_M,
    )

    plan = build_production_camera_plan()
    ground = [c for c in plan.cameras if c.group_id == "ground-route"]
    by_route: dict[str, list] = {}
    for camera in ground:
        by_route.setdefault(camera.topology_ref, []).append(camera)
    checked = 0
    for cameras in by_route.values():
        ordered = sorted(cameras, key=lambda c: c.arc_length_m or 0.0)
        for left, right in zip(ordered, ordered[1:], strict=False):
            gap = math.dist(left.position_m, right.position_m)
            assert gap <= MAX_GROUND_ROUTE_CAMERA_SPACING_M, (left.camera_id, right.camera_id, gap)
            checked += 1
    assert checked > 0

    # 连线证明: 把常量收紧到真实最大间距之下 -> 布点必须 fail-closed
    largest = max(
        math.dist(left.position_m, right.position_m)
        for cameras in by_route.values()
        for left, right in zip(
            sorted(cameras, key=lambda c: c.arc_length_m or 0.0),
            sorted(cameras, key=lambda c: c.arc_length_m or 0.0)[1:],
            strict=False,
        )
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(production_profile, "MAX_GROUND_ROUTE_CAMERA_SPACING_M", largest / 2)
        with pytest.raises(ProductionProfileError, match="spacing"):
            build_production_camera_plan()


# --------------------------------------------------------------------------
# req 1: 180 个 pose 全部【有限】且无重复中心 —— 契约必须真的挣得这两半
#
# 这些契约是给【反序列化】兜底的 (canonical_production_plan_bytes 明确支持
# reload): builder 永远造合法的, 所以全套测试从不触发它们。
# --------------------------------------------------------------------------


def _plan_payload() -> dict:
    return json.loads(canonical_production_plan_bytes(build_production_camera_plan()))


def _reload(payload: dict):
    from pipeline.synthetic_village.production_profile import ProductionCameraPlan

    return ProductionCameraPlan.model_validate_json(json.dumps(payload))


def test_clean_plan_round_trips_so_the_rejection_tests_mean_something() -> None:
    """先证明干净 round-trip 通过 —— 否则下面每条 raises 都可能是假阳性。"""

    plan = build_production_camera_plan()
    assert _reload(_plan_payload()) == plan


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position_m", [float("nan"), 0.0, 0.0]),
        ("position_m", [float("inf"), 0.0, 0.0]),
        ("look_at_m", [0.0, 0.0, float("-inf")]),
    ],
)
def test_non_finite_pose_is_rejected_on_reload(field: str, value: list) -> None:
    """req 1「全部有限」: NaN/Inf 位姿必须 fail-closed。

    Python json 会写出裸 NaN/Infinity 并能被读回 —— 一份被篡改或被下游工具
    写坏的 plan JSON 可以带着 NaN 位姿通过 model_validate_json。
    """

    payload = _plan_payload()
    payload["cameras"][0][field] = value
    with pytest.raises(ValidationError):
        _reload(payload)


def test_non_finite_c2w_matrix_is_rejected_on_reload() -> None:
    payload = _plan_payload()
    payload["cameras"][0]["c2w_opencv"][0][3] = float("nan")
    with pytest.raises(ValidationError):
        _reload(payload)


def test_duplicate_camera_centres_are_rejected_on_reload() -> None:
    """req 1「无重复中心」: 两台相机占同一个中心 = 退化基线, 必须 fail-closed。"""

    payload = _plan_payload()
    payload["cameras"][1]["position_m"] = list(payload["cameras"][0]["position_m"])
    with pytest.raises(ValidationError, match="duplicate|unique"):
        _reload(payload)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda p: p.update(camera_count=p["camera_count"] + 1), "camera_count must equal"),
        (lambda p: p.update(complete=False), "complete must be true only"),
        (
            lambda p: p["cameras"][1].update(camera_id=p["cameras"][0]["camera_id"]),
            "camera IDs must be unique",
        ),
        (lambda p: p["cameras"][3].update(sequence_index=99), "dense and ordered"),
        (
            lambda p: p["unplaced_groups"].append(
                {
                    "group_id": "elevated-pedestrian",
                    "camera_count": 1,
                    "reason": "contradictory externally injected unplaced camera",
                }
            ),
            "must equal the declared target",
        ),
        (
            lambda p: p["cameras"][0].update(group_id="audit-overview"),
            "prefix must match its group",
        ),
        (lambda p: p["cameras"][0].update(audit_only=True), "audit_only must be set"),
    ],
)
def test_plan_contracts_reject_externally_constructed_lies(mutate, match: str) -> None:
    """这 7 道 validator 以前全部无测试 —— 逐个换成 `pass` 都不红。

    根因: 全套测试只消费 build_production_camera_plan() 造出的那一个 plan,
    而 builder 永远造合法的, 所以 validator 在测试里从不被触发。
    """

    payload = _plan_payload()
    mutate(payload)
    with pytest.raises(ValidationError, match=match):
        _reload(payload)


def test_a_complete_plan_cannot_drop_a_camera() -> None:
    """完整计划少一台相机必须 fail-closed，不能继续声称 180/180。"""

    payload = _plan_payload()
    payload["cameras"].pop()
    payload["camera_count"] -= 1
    with pytest.raises(ValidationError, match="must equal the declared target"):
        _reload(payload)


# --------------------------------------------------------------------------
# 分辨率: 生产档必须与审计内核读的是【同一个】画幅契约
# --------------------------------------------------------------------------


def test_cli_plan_production_surfaces_the_undelivered_requirements() -> None:
    """CLI 输出是 Codex 【实际会读】的东西 —— 没做的需求必须出现在这里。

    只把它藏在 plan JSON 里等同于没说。
    """

    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "scripts/synthetic_village.py", "plan-production"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(proc.stdout)
    assert summary["camera_count"] == TARGET_CAMERA_COUNT
    assert summary["complete"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", summary["elevated_topology_sha256"])
    assert {row["loop_id"] for row in summary["route_loops"]} == {
        "central-loop",
        "upper-loop",
    }
    rows = summary["undelivered_requirements"]
    ids = {row["requirement_id"] for row in rows}
    assert "req-5-pose-quality-fail-closed" in ids
    assert "req-6-route-loop-closure" not in ids
    for row in rows:
        assert row["status"] in {"not-implemented", "structurally-unreachable"}
        assert len(row["reason"]) >= 20


def test_production_intrinsics_match_the_coverage_audit_frame_contract() -> None:
    """生产档渲染出的帧必须能被 coverage_audit 读进去。

    defaults.py:231 的 full-180 档声明 2048x1152, 与这里的 1024x576 差 4 倍
    像素, 两者互不引用 —— 按 full-180 渲染 180 帧送进审计会掩码 shape 不符而
    硬失败, 整批算力作废。这条测试至少锁死【生产档这一侧】不再漂移。
    """

    from pipeline.synthetic_village.coverage_audit import FRAME_HEIGHT_PX, FRAME_WIDTH_PX

    plan = build_production_camera_plan()
    for camera in plan.cameras:
        assert camera.intrinsics.width_px == FRAME_WIDTH_PX
        assert camera.intrinsics.height_px == FRAME_HEIGHT_PX
