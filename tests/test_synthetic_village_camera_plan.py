"""Deterministic synthetic-village camera-plan tests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections import Counter

import numpy as np
import pytest
from pydantic import ValidationError

import pipeline.synthetic_village.camera_plan as camera_plan
from pipeline.synthetic_village.camera_plan import (
    EXPECTED_SCENE_PLAN_SHA256,
    MAX_CAMERA_PLACEMENT_ATTEMPTS,
    CameraPlan,
    CameraPlanError,
    build_camera_plan,
    canonical_camera_plan_bytes,
    load_camera_plan,
)
from pipeline.synthetic_village.scene_plan import (
    PlanPoint,
    build_scene_plan,
    terrain_height_m,
)


def _camera_position(camera) -> np.ndarray:
    return np.asarray(camera.c2w_opencv, dtype=float)[:3, 3]


def _inside_building_obb(position: np.ndarray, building) -> bool:
    angle = math.radians(building.transform.yaw_deg)
    cosine, sine = math.cos(angle), math.sin(angle)
    dx = position[0] - building.transform.x_m
    dy = position[1] - building.transform.y_m
    local_x = dx * cosine + dy * sine
    local_y = -dx * sine + dy * cosine
    return (
        abs(local_x) <= building.dimensions.width_m / 2
        and abs(local_y) <= building.dimensions.depth_m / 2
    )


def _projected_building_ids(camera, scene) -> tuple[str, ...]:
    matrix = np.asarray(camera.c2w_opencv, dtype=float)
    rotation = matrix[:3, :3]
    eye = matrix[:3, 3]
    intrinsics = camera.intrinsics
    visible = []
    for building in (item for item in scene.objects if item.semantic_class == "building"):
        world = np.array(
            [
                building.transform.x_m,
                building.transform.y_m,
                building.transform.z_m,
            ],
            dtype=float,
        )
        camera_point = rotation.T @ (world - eye)
        if camera_point[2] <= camera_plan.PROJECTION_NEAR_M:
            continue
        u = intrinsics.fx * camera_point[0] / camera_point[2] + intrinsics.cx
        v = intrinsics.fy * camera_point[1] / camera_point[2] + intrinsics.cy
        if 0.0 <= u < intrinsics.width_px and 0.0 <= v < intrinsics.height_px:
            visible.append(building.object_id)
    return tuple(sorted(visible))


def _point_segment_distance(point, start, end) -> float:
    edge_x, edge_y = end[0] - start[0], end[1] - start[1]
    length_squared = edge_x * edge_x + edge_y * edge_y
    fraction = ((point[0] - start[0]) * edge_x + (point[1] - start[1]) * edge_y) / length_squared
    fraction = min(1.0, max(0.0, fraction))
    nearest = (start[0] + fraction * edge_x, start[1] + fraction * edge_y)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _projects(camera, world) -> bool:
    matrix = np.asarray(camera.c2w_opencv, dtype=float)
    point = matrix[:3, :3].T @ (np.asarray(world, dtype=float) - matrix[:3, 3])
    if point[2] <= camera_plan.PROJECTION_NEAR_M:
        return False
    u = camera.intrinsics.fx * point[0] / point[2] + camera.intrinsics.cx
    v = camera.intrinsics.fy * point[1] / point[2] + camera.intrinsics.cy
    return 0 <= u < camera.intrinsics.width_px and 0 <= v < camera.intrinsics.height_px


def test_default_camera_plan_is_deterministic_and_has_exact_budget_and_splits():
    scene = build_scene_plan()
    first = build_camera_plan(scene)
    second = build_camera_plan(scene)

    assert first == second
    assert canonical_camera_plan_bytes(first) == canonical_camera_plan_bytes(second)
    assert first.scene_plan_sha256 == EXPECTED_SCENE_PLAN_SHA256
    assert len(first.cameras) == 24
    assert Counter(camera.category for camera in first.cameras) == {
        "outer": 8,
        "ground": 8,
        "courtyard": 4,
        "bridge": 4,
    }
    assert Counter(camera.split for camera in first.cameras) == {
        "train": 18,
        "val": 4,
        "test": 2,
    }
    assert [camera.camera_id for camera in first.cameras] == [
        *(f"camera-outer-{index:03d}" for index in range(1, 9)),
        *(f"camera-ground-{index:03d}" for index in range(1, 9)),
        *(f"camera-courtyard-{index:03d}" for index in range(1, 5)),
        *(f"camera-bridge-{index:03d}" for index in range(1, 5)),
    ]
    assert [camera.sequence_index for camera in first.cameras] == list(range(1, 25))


def test_intrinsics_and_opencv_blender_rigid_matrices_are_explicit():
    plan = build_camera_plan(build_scene_plan())
    fov_by_category = {"outer": 75.0, "ground": 65.0, "courtyard": 65.0, "bridge": 55.0}
    conversion = np.diag([1.0, -1.0, -1.0, 1.0])

    for camera in plan.cameras:
        assert camera.fov_x_deg == fov_by_category[camera.category]
        expected_focal = round(512.0 / math.tan(math.radians(camera.fov_x_deg) / 2), 9)
        assert camera.intrinsics.model_dump() == {
            "width_px": 1024,
            "height_px": 576,
            "fx": expected_focal,
            "fy": expected_focal,
            "cx": 512.0,
            "cy": 288.0,
        }
        opencv = np.asarray(camera.c2w_opencv, dtype=float)
        blender = np.asarray(camera.c2w_blender, dtype=float)
        assert np.all(np.isfinite(opencv))
        assert np.allclose(opencv[3], [0, 0, 0, 1], atol=1e-10)
        assert np.allclose(opencv[:3, :3].T @ opencv[:3, :3], np.eye(3), atol=2e-8)
        assert np.linalg.det(opencv[:3, :3]) == pytest.approx(1.0, abs=2e-8)
        assert np.allclose(blender, opencv @ conversion, atol=1e-10)


def test_positions_are_safe_and_coverage_is_recomputed_from_projection():
    scene = build_scene_plan()
    plan = build_camera_plan(scene)
    buildings = [item for item in scene.objects if item.semantic_class == "building"]
    train = [_camera_position(item) for item in plan.cameras if item.split == "train"]
    assert len({tuple(_camera_position(item)) for item in plan.cameras}) == 24
    building_map = {item.object_id: item for item in buildings}
    cell_counts: Counter[str] = Counter()
    cluster_counts: Counter[str] = Counter()

    for camera in plan.cameras:
        position = _camera_position(camera)
        assert position[2] - terrain_height_m(position[0], position[1], scene.extent) >= 1.4
        assert not any(_inside_building_obb(position, building) for building in buildings)
        if camera.split != "train":
            assert all(np.linalg.norm(position - train_position) >= 8.0 for train_position in train)
        assert camera.visible_building_ids == _projected_building_ids(camera, scene)
        visible = [building_map[identifier] for identifier in camera.visible_building_ids]
        cell_counts.update({item.spatial_cell for item in visible})
        cluster_counts.update({item.cluster for item in visible})

    assert set(cell_counts) == {
        f"cell-r{row}-c{column}" for row in range(1, 4) for column in range(1, 5)
    }
    assert min(cell_counts.values()) >= 2
    assert set(cluster_counts) == {"creekside", "central", "upper"}
    assert min(cluster_counts.values()) >= 6
    assert {
        entry.spatial_cell: entry.camera_count for entry in plan.spatial_cell_coverage
    } == cell_counts
    assert {entry.cluster: entry.camera_count for entry in plan.cluster_coverage} == cluster_counts


def test_each_ground_camera_aims_at_and_sees_its_local_source_route():
    scene = build_scene_plan()
    plan = build_camera_plan(scene)
    paths = [item for item in scene.objects if item.semantic_class == "path"]

    for camera in (item for item in plan.cameras if item.category == "ground"):
        anchor = next(
            anchor
            for anchor in scene.camera_anchors
            if anchor.anchor_id == camera.source_anchor_ids[0]
        )
        source_paths = [
            path for path in paths if path.polyline.route_id == anchor.source_id
        ] or paths
        target = camera.look_at_target
        target_xy = (target.x_m, target.y_m)
        segments = [
            (left, right, path.polyline.width_m)
            for path in source_paths
            for left, right in zip(
                path.polyline.points,
                path.polyline.points[1:],
                strict=False,
            )
        ]
        assert (
            min(
                _point_segment_distance(
                    target_xy,
                    (left.x_m, left.y_m),
                    (right.x_m, right.y_m),
                )
                - width / 2
                for left, right, width in segments
            )
            <= 1e-6
        )

        visible_local_samples = 0
        for left, right, _width in segments:
            length = math.hypot(right.x_m - left.x_m, right.y_m - left.y_m)
            sample_count = max(2, math.ceil(length / 0.5))
            for index in range(sample_count + 1):
                fraction = index / sample_count
                x_m = left.x_m + fraction * (right.x_m - left.x_m)
                y_m = left.y_m + fraction * (right.y_m - left.y_m)
                if math.hypot(x_m - target.x_m, y_m - target.y_m) > 30.0:
                    continue
                world = (x_m, y_m, terrain_height_m(x_m, y_m, scene.extent) + 0.05)
                visible_local_samples += _projects(camera, world)
        assert visible_local_samples >= 5


def test_plan_is_strict_frozen_and_rejects_unknown_scene_or_degenerate_route():
    scene = build_scene_plan()
    plan = build_camera_plan(scene)
    with pytest.raises(ValidationError):
        plan.cameras[0].fov_x_deg = 65.0
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CameraPlan.model_validate_json(
            json.dumps({**plan.model_dump(mode="json"), "unexpected": True}),
        )

    unknown_scene = scene.model_copy(update={"terrain_min_m": 0.001})
    with pytest.raises(CameraPlanError, match="scene digest"):
        build_camera_plan(unknown_scene)

    route_index = next(
        index for index, anchor in enumerate(scene.camera_anchors) if anchor.anchor_type == "route"
    )
    route = scene.camera_anchors[route_index]
    degenerate = route.model_copy(
        update={
            "target": PlanPoint(
                x_m=route.position.x_m,
                y_m=route.position.y_m,
                z_m=route.position.z_m,
            ),
        },
    )
    anchors = list(scene.camera_anchors)
    anchors[route_index] = degenerate
    bad_scene = scene.model_copy(update={"camera_anchors": tuple(anchors)})
    with pytest.raises(CameraPlanError, match="degenerate route anchor"):
        build_camera_plan(bad_scene)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload["cameras"][0].update(fov_x_deg=65.0), "FOV"),
        (
            lambda payload: payload["cameras"][0]["intrinsics"].update(
                fx=payload["cameras"][0]["intrinsics"]["fx"] + 1,
            ),
            "intrinsics",
        ),
        (lambda payload: payload["cameras"][0].update(split="val"), "split"),
        (lambda payload: payload["cameras"][0].update(camera_id="camera-outer-999"), "ID"),
        (
            lambda payload: payload["cameras"][0].update(
                visible_building_ids=payload["cameras"][0]["visible_building_ids"][1:],
            ),
            "projection",
        ),
        (lambda payload: payload.update(scene_plan_sha256="0" * 64), "scene digest"),
    ],
)
def test_model_rejects_fov_intrinsics_split_id_coverage_and_digest_tampering(mutate, message):
    payload = build_camera_plan(build_scene_plan()).model_dump(mode="json")
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        CameraPlan.model_validate_json(json.dumps(payload))


def test_model_rejects_nonfinite_nonrigid_reflected_and_wrong_blender_matrices():
    base = build_camera_plan(build_scene_plan()).model_dump(mode="json")

    payload = json.loads(json.dumps(base))
    payload["cameras"][0]["c2w_opencv"][0][0] = float("nan")
    with pytest.raises(ValidationError, match="finite"):
        CameraPlan.model_validate_json(json.dumps(payload))

    payload = json.loads(json.dumps(base))
    payload["cameras"][0]["c2w_opencv"][0][0] *= 2
    with pytest.raises(ValidationError, match="rigid"):
        CameraPlan.model_validate_json(json.dumps(payload))

    payload = json.loads(json.dumps(base))
    reflected = np.asarray(payload["cameras"][0]["c2w_opencv"], dtype=float)
    reflected[:3, [0, 1]] = reflected[:3, [1, 0]]
    reflected_blender = reflected @ np.diag([1.0, -1.0, -1.0, 1.0])
    reflected_blender[reflected_blender == 0] = 0.0
    payload["cameras"][0]["c2w_opencv"] = reflected.tolist()
    payload["cameras"][0]["c2w_blender"] = reflected_blender.tolist()
    with pytest.raises(ValidationError, match="determinant"):
        CameraPlan.model_validate_json(json.dumps(payload))

    payload = json.loads(json.dumps(base))
    payload["cameras"][0]["c2w_blender"][0][3] += 0.001
    with pytest.raises(ValidationError, match="Blender conversion"):
        CameraPlan.model_validate_json(json.dumps(payload))


def test_model_rejects_valid_pose_reordering_and_false_anchor_provenance():
    base = build_camera_plan(build_scene_plan()).model_dump(mode="json")

    payload = json.loads(json.dumps(base))
    pose_fields = (
        "c2w_opencv",
        "c2w_blender",
        "visible_building_ids",
        "placement_attempts",
    )
    for field in pose_fields:
        payload["cameras"][0][field], payload["cameras"][1][field] = (
            payload["cameras"][1][field],
            payload["cameras"][0][field],
        )
    with pytest.raises(ValidationError, match="deterministic camera pose"):
        CameraPlan.model_validate_json(json.dumps(payload))

    payload = json.loads(json.dumps(base))
    ground = next(item for item in payload["cameras"] if item["category"] == "ground")
    replacement = next(
        item["source_anchor_ids"]
        for item in payload["cameras"]
        if item["category"] == "ground" and item["source_anchor_ids"] != ground["source_anchor_ids"]
    )
    ground["source_anchor_ids"] = replacement
    with pytest.raises(ValidationError, match="topology-derived source anchor"):
        CameraPlan.model_validate_json(json.dumps(payload))


def test_model_and_loader_reject_signed_zero_canonical_malleability(tmp_path):
    payload = build_camera_plan(build_scene_plan()).model_dump(mode="json")
    payload["cameras"][0]["c2w_opencv"][3][0] = -0.0
    payload["cameras"][0]["c2w_blender"][3][0] = -0.0

    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with pytest.raises(ValidationError, match="negative zero"):
        CameraPlan.model_validate_json(encoded)

    path = tmp_path / "signed-zero.json"
    path.write_text(encoded, encoding="utf-8")
    with pytest.raises(CameraPlanError, match="negative zero"):
        load_camera_plan(path)


def test_fallback_search_is_bounded_and_attempt_limit_is_strict(monkeypatch):
    calls = 0

    def reject(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(camera_plan, "_position_is_valid", reject)
    with pytest.raises(CameraPlanError, match="placement attempt limit"):
        build_camera_plan(build_scene_plan(), attempt_limit=3)
    assert calls == 3
    assert MAX_CAMERA_PLACEMENT_ATTEMPTS >= 3

    with pytest.raises(ValueError, match="attempt limit"):
        build_camera_plan(build_scene_plan(), attempt_limit=True)


def test_loader_requires_canonical_duplicate_free_stable_regular_path(tmp_path, monkeypatch):
    plan = build_camera_plan(build_scene_plan())
    canonical = canonical_camera_plan_bytes(plan)
    path = tmp_path / "camera-plan.json"
    path.write_bytes(canonical)
    assert load_camera_plan(path) == plan

    path.write_text(json.dumps(plan.model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(CameraPlanError, match="canonical"):
        load_camera_plan(path)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(
        canonical.replace(
            b'  "schema_version": 1',
            b'  "schema_version": 1,\n  "schema_version": 1',
            1,
        ),
    )
    with pytest.raises(CameraPlanError, match="duplicate JSON key"):
        load_camera_plan(duplicate)
    with pytest.raises(CameraPlanError, match="camera plan"):
        load_camera_plan(tmp_path / "missing.json")

    stable = tmp_path / "stable.json"
    stable.write_bytes(canonical)
    real_signature = camera_plan._stat_signature
    signature_calls = 0

    def drifting_signature(result):
        nonlocal signature_calls
        signature_calls += 1
        signature = real_signature(result)
        if signature_calls == 6:
            return (*signature[:3], signature[3] + 1)
        return signature

    monkeypatch.setattr(camera_plan, "_stat_signature", drifting_signature)
    with pytest.raises(CameraPlanError, match="changed during bounded read"):
        load_camera_plan(stable)


def test_loader_rejects_redirected_parent_on_windows(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction test")
    target = tmp_path / "real"
    target.mkdir()
    (target / "camera-plan.json").write_bytes(
        canonical_camera_plan_bytes(build_camera_plan(build_scene_plan())),
    )
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
        with pytest.raises(CameraPlanError, match="redirected"):
            load_camera_plan(junction / "camera-plan.json")
    finally:
        junction.rmdir()


def test_camera_plan_canonical_digest_is_fixed():
    digest = hashlib.sha256(
        canonical_camera_plan_bytes(build_camera_plan(build_scene_plan())),
    ).hexdigest()
    assert digest == "94714ff7f5929a6480c9c9bf01a6154a7e757fb00fc4e8f5f1e79e640ecbab6f"
