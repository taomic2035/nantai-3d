"""Deterministic, topology-derived camera plan for the synthetic village."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .scene_plan import (
    CameraAnchor,
    ClusterId,
    PlanPoint,
    SceneObject,
    ScenePlan,
    build_scene_plan,
    canonical_scene_plan_bytes,
    terrain_height_m,
)

EXPECTED_SCENE_PLAN_SHA256 = "1a05b678a61ca15228ac3be219864699d0ad333e9a2210cb16277147a32283d4"
MAX_CAMERA_PLAN_BYTES = 4 * 1024 * 1024
MAX_CAMERA_PLACEMENT_ATTEMPTS = 64
MIN_TERRAIN_CLEARANCE_M = 1.4
MIN_NONTRAIN_TO_TRAIN_DISTANCE_M = 8.0
PROJECTION_NEAR_M = 0.1
MATRIX_DECIMALS = 9

CameraCategory = Literal["outer", "ground", "courtyard", "bridge"]
DatasetSplit = Literal["train", "val", "test"]
Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]

_FOV_BY_CATEGORY: dict[CameraCategory, float] = {
    "outer": 75.0,
    "ground": 65.0,
    "courtyard": 65.0,
    "bridge": 55.0,
}
_EXPECTED_IDS = tuple(
    [*(f"camera-outer-{index:03d}" for index in range(1, 9))]
    + [*(f"camera-ground-{index:03d}" for index in range(1, 9))]
    + [*(f"camera-courtyard-{index:03d}" for index in range(1, 5))]
    + [*(f"camera-bridge-{index:03d}" for index in range(1, 5))]
)
_EXPECTED_SPLITS: tuple[DatasetSplit, ...] = (
    *("train" for _ in range(18)),
    *("val" for _ in range(4)),
    *("test" for _ in range(2)),
)


class CameraPlanError(ValueError):
    """Stable public error for camera generation and untrusted loading."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CameraIntrinsics(FrozenModel):
    width_px: Literal[1024] = 1024
    height_px: Literal[576] = 576
    fx: float = Field(gt=0, allow_inf_nan=False)
    fy: float = Field(gt=0, allow_inf_nan=False)
    cx: Literal[512.0] = 512.0
    cy: Literal[288.0] = 288.0


class CameraPose(FrozenModel):
    camera_id: str = Field(pattern=r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")
    sequence_index: int = Field(ge=1, le=24)
    category: CameraCategory
    split: DatasetSplit
    source_anchor_ids: tuple[str, ...] = Field(min_length=1)
    fov_x_deg: Literal[55.0, 65.0, 75.0]
    intrinsics: CameraIntrinsics
    look_at_target: PlanPoint
    c2w_opencv: Matrix4
    c2w_blender: Matrix4
    visible_building_ids: tuple[str, ...]
    placement_attempts: int = Field(ge=1, le=MAX_CAMERA_PLACEMENT_ATTEMPTS)

    @model_validator(mode="after")
    def _validate_camera(self) -> CameraPose:
        if not self.camera_id.startswith(f"camera-{self.category}-"):
            raise ValueError("camera ID prefix must match category")
        if self.fov_x_deg != _FOV_BY_CATEGORY[self.category]:
            raise ValueError("camera FOV does not match category allowlist")
        focal = _q9(512.0 / math.tan(math.radians(self.fov_x_deg) / 2))
        if self.intrinsics.fx != focal or self.intrinsics.fy != focal:
            raise ValueError("camera intrinsics do not match horizontal FOV")
        if tuple(sorted(self.source_anchor_ids)) != self.source_anchor_ids or len(
            set(self.source_anchor_ids)
        ) != len(self.source_anchor_ids):
            raise ValueError("source anchor IDs must be unique and sorted")
        if tuple(sorted(self.visible_building_ids)) != self.visible_building_ids or len(
            set(self.visible_building_ids)
        ) != len(self.visible_building_ids):
            raise ValueError("visible building IDs must be unique and sorted")
        if any(
            value == 0.0 and math.copysign(1.0, value) < 0
            for matrix in (self.c2w_opencv, self.c2w_blender)
            for row in matrix
            for value in row
        ) or any(
            value == 0.0 and math.copysign(1.0, value) < 0
            for value in (
                self.look_at_target.x_m,
                self.look_at_target.y_m,
                self.look_at_target.z_m,
            )
        ):
            raise ValueError("camera matrices must not encode negative zero")
        opencv = np.asarray(self.c2w_opencv, dtype=float)
        blender = np.asarray(self.c2w_blender, dtype=float)
        if not np.all(np.isfinite(opencv)) or not np.all(np.isfinite(blender)):
            raise ValueError("camera matrices must contain finite numbers")
        if not np.array_equal(opencv[3], np.array([0.0, 0.0, 0.0, 1.0])):
            raise ValueError("camera matrix must have a homogeneous rigid last row")
        rotation = opencv[:3, :3]
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=2e-8, rtol=0):
            raise ValueError("OpenCV camera matrix rotation must be rigid")
        if not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=2e-8):
            raise ValueError("OpenCV camera matrix determinant must be +1")
        target = np.array(
            [
                self.look_at_target.x_m,
                self.look_at_target.y_m,
                self.look_at_target.z_m,
            ],
            dtype=float,
        )
        direction = target - opencv[:3, 3]
        distance = float(np.linalg.norm(direction))
        if distance <= 1e-9 or not np.allclose(
            rotation[:, 2],
            direction / distance,
            atol=2e-8,
            rtol=0,
        ):
            raise ValueError("OpenCV forward axis must aim at the explicit look-at target")
        expected_blender = _matrix4(opencv @ np.diag([1.0, -1.0, -1.0, 1.0]))
        if self.c2w_blender != expected_blender:
            raise ValueError("Blender conversion must equal OpenCV c2w @ diag(1,-1,-1,1)")
        if not all(_is_quantized(value, MATRIX_DECIMALS) for row in rotation for value in row):
            raise ValueError("camera rotation must use the nine-decimal grid")
        if not all(_is_quantized(value, 3) for value in opencv[:3, 3]):
            raise ValueError("camera translation must use the millimeter grid")
        return self


class SpatialCellCoverage(FrozenModel):
    spatial_cell: str = Field(pattern=r"^cell-r[1-3]-c[1-4]$")
    camera_count: int = Field(ge=2, le=24)


class ClusterCoverage(FrozenModel):
    cluster: ClusterId
    camera_count: int = Field(ge=6, le=24)


class CameraPlan(FrozenModel):
    schema_version: Literal[1] = 1
    plan_id: Literal["synthetic-mountain-village-camera-plan-v1"]
    scene_plan_id: Literal["synthetic-mountain-village-scene-v1"]
    scene_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_system: Literal["opencv-c2w-right-down-forward-meters"]
    blender_conversion: Literal["c2w_blender=c2w_opencv@diag(1,-1,-1,1)"]
    image_width_px: Literal[1024] = 1024
    image_height_px: Literal[576] = 576
    matrix_quantization_decimals: Literal[9] = 9
    translation_quantization_m: Literal[0.001] = 0.001
    minimum_terrain_clearance_m: Literal[1.4] = 1.4
    minimum_nontrain_to_train_distance_m: Literal[8.0] = 8.0
    projection_near_m: Literal[0.1] = 0.1
    cameras: tuple[CameraPose, ...] = Field(min_length=24, max_length=24)
    spatial_cell_coverage: tuple[SpatialCellCoverage, ...] = Field(min_length=12, max_length=12)
    cluster_coverage: tuple[ClusterCoverage, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _validate_plan(self) -> CameraPlan:
        if self.scene_plan_sha256 != EXPECTED_SCENE_PLAN_SHA256:
            raise ValueError("camera plan scene digest is not the tracked scene digest")
        identifiers = tuple(camera.camera_id for camera in self.cameras)
        if identifiers != _EXPECTED_IDS:
            raise ValueError("camera ID sequence and stable order are invalid")
        if tuple(camera.sequence_index for camera in self.cameras) != tuple(range(1, 25)):
            raise ValueError("camera sequence indices must be contiguous and stable")
        if tuple(camera.split for camera in self.cameras) != _EXPECTED_SPLITS:
            raise ValueError("camera split assignment or counts are invalid")
        if Counter(camera.category for camera in self.cameras) != {
            "outer": 8,
            "ground": 8,
            "courtyard": 4,
            "bridge": 4,
        }:
            raise ValueError("camera category budgets are invalid")
        scene = _tracked_scene_plan()
        _validate_cameras_against_scene(self, scene)
        return self


@dataclass(frozen=True)
class _CameraSpec:
    camera_id: str
    category: CameraCategory
    split: DatasetSplit
    source_anchor_ids: tuple[str, ...]
    base_position: tuple[float, float, float]
    target: tuple[float, float, float]


def _q3(value: float) -> float:
    return round(float(value) + 0.0, 3)


def _q9(value: float) -> float:
    return round(float(value) + 0.0, MATRIX_DECIMALS)


def _is_quantized(value: float, decimals: int) -> bool:
    scale = 10**decimals
    return abs(value * scale - round(value * scale)) <= 1e-6


def _matrix4(matrix: np.ndarray) -> Matrix4:
    values = np.asarray(matrix, dtype=float)
    return tuple(tuple(_q9(value) for value in row) for row in values)  # type: ignore[return-value]


def _intrinsics(fov_x_deg: float) -> CameraIntrinsics:
    focal = _q9(512.0 / math.tan(math.radians(fov_x_deg) / 2))
    return CameraIntrinsics(fx=focal, fy=focal)


def _scene_digest(scene: ScenePlan) -> str:
    return hashlib.sha256(canonical_scene_plan_bytes(scene)).hexdigest()


@lru_cache(maxsize=1)
def _tracked_scene_plan() -> ScenePlan:
    scene = build_scene_plan()
    if _scene_digest(scene) != EXPECTED_SCENE_PLAN_SHA256:
        raise CameraPlanError("tracked scene digest does not match camera-plan contract")
    return scene


def _anchor_distance(anchor: CameraAnchor) -> float:
    return math.dist(
        (anchor.position.x_m, anchor.position.y_m, anchor.position.z_m),
        (anchor.target.x_m, anchor.target.y_m, anchor.target.z_m),
    )


def _preflight_anchors(scene: ScenePlan) -> None:
    for anchor in scene.camera_anchors:
        if (
            anchor.anchor_type in {"route", "intersection", "bridge"}
            and _anchor_distance(anchor) <= 1e-9
        ):
            label = "route" if anchor.anchor_type == "route" else anchor.anchor_type
            raise CameraPlanError(f"degenerate {label} anchor: {anchor.anchor_id}")


def _unit_xy(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise CameraPlanError("topology-derived camera direction is degenerate")
    return dx / length, dy / length


def _nearest_source_route_frame(
    anchor: CameraAnchor,
    scene: ScenePlan,
) -> tuple[tuple[float, float], tuple[float, float]]:
    paths = [
        item
        for item in scene.objects
        if item.semantic_class == "path"
        and item.polyline is not None
        and item.polyline.route_id == anchor.source_id
    ]
    if not paths and anchor.anchor_type == "intersection":
        paths = [item for item in scene.objects if item.semantic_class == "path"]
    if not paths:
        raise CameraPlanError(f"camera anchor has no source route: {anchor.anchor_id}")
    feature = (
        (anchor.target.x_m, anchor.target.y_m)
        if anchor.anchor_type == "intersection"
        else (anchor.position.x_m, anchor.position.y_m)
    )
    candidates = []
    for path in sorted(paths, key=lambda item: item.object_id):
        for segment_index, (start, end) in enumerate(
            zip(path.polyline.points, path.polyline.points[1:], strict=False),
        ):
            edge_x, edge_y = end.x_m - start.x_m, end.y_m - start.y_m
            length_squared = edge_x * edge_x + edge_y * edge_y
            if length_squared <= 1e-9:
                raise CameraPlanError(
                    f"source route contains a degenerate segment: {path.object_id}"
                )
            fraction = (
                (feature[0] - start.x_m) * edge_x + (feature[1] - start.y_m) * edge_y
            ) / length_squared
            fraction = min(1.0, max(0.0, fraction))
            nearest = (
                start.x_m + fraction * edge_x,
                start.y_m + fraction * edge_y,
            )
            distance = math.hypot(feature[0] - nearest[0], feature[1] - nearest[1])
            candidates.append(
                (
                    distance,
                    path.object_id,
                    segment_index,
                    nearest,
                    _unit_xy((start.x_m, start.y_m), (end.x_m, end.y_m)),
                )
            )
    minimum_distance = min(row[0] for row in candidates)
    nearest_candidates = [
        row for row in candidates if math.isclose(row[0], minimum_distance, abs_tol=1e-9)
    ]
    selector = max if anchor.anchor_type == "intersection" else min
    _, _, _, nearest, tangent = selector(
        nearest_candidates,
        key=lambda row: (row[1], row[2]),
    )
    return (_q3(nearest[0]), _q3(nearest[1])), tangent


def _build_specs(scene: ScenePlan) -> tuple[_CameraSpec, ...]:
    anchors = {anchor.anchor_id: anchor for anchor in scene.camera_anchors}
    clusters = sorted(
        (anchor for anchor in anchors.values() if anchor.anchor_type == "cluster"),
        key=lambda anchor: anchor.anchor_id,
    )
    if len(clusters) != 3:
        raise CameraPlanError("scene must provide exactly three cluster camera anchors")
    target = tuple(
        _q3(sum(getattr(anchor.target, axis) for anchor in clusters) / len(clusters))
        for axis in ("x_m", "y_m", "z_m")
    )
    source_clusters = tuple(anchor.anchor_id for anchor in clusters)
    center_x, center_y = target[0], target[1]
    radius_x = scene.extent.width_m * 0.44
    radius_y = scene.extent.depth_m * 0.44
    specs: list[_CameraSpec] = []
    for index in range(8):
        angle = math.radians(22.5 + 45 * index)
        x_m = _q3(center_x + radius_x * math.cos(angle))
        y_m = _q3(center_y + radius_y * math.sin(angle))
        z_m = _q3(terrain_height_m(x_m, y_m, scene.extent) + 90.0)
        specs.append(
            _CameraSpec(
                camera_id=f"camera-outer-{index + 1:03d}",
                category="outer",
                split="train",
                source_anchor_ids=source_clusters,
                base_position=(x_m, y_m, z_m),
                target=target,
            )
        )

    navigation = sorted(
        (anchor for anchor in anchors.values() if anchor.anchor_type in {"route", "intersection"}),
        key=lambda anchor: anchor.anchor_id,
    )
    if len(navigation) != 4:
        raise CameraPlanError("scene must provide four route/intersection camera anchors")
    ground_index = 0
    for anchor in navigation:
        route_target, tangent = _nearest_source_route_frame(anchor, scene)
        perpendicular = (-tangent[1], tangent[0])
        clearance = max(
            MIN_TERRAIN_CLEARANCE_M,
            anchor.position.z_m
            - terrain_height_m(anchor.position.x_m, anchor.position.y_m, scene.extent),
        )
        for signed_offset in (-4.0, 4.0):
            ground_index += 1
            x_m = _q3(route_target[0] + perpendicular[0] * signed_offset)
            y_m = _q3(route_target[1] + perpendicular[1] * signed_offset)
            z_m = _q3(terrain_height_m(x_m, y_m, scene.extent) + clearance)
            target_z_m = terrain_height_m(
                route_target[0],
                route_target[1],
                scene.extent,
            )
            specs.append(
                _CameraSpec(
                    camera_id=f"camera-ground-{ground_index:03d}",
                    category="ground",
                    split="train",
                    source_anchor_ids=(anchor.anchor_id,),
                    base_position=(x_m, y_m, z_m),
                    target=(route_target[0], route_target[1], target_z_m),
                )
            )

    courtyards = sorted(
        (anchor for anchor in anchors.values() if anchor.anchor_type == "courtyard"),
        key=lambda anchor: anchor.anchor_id,
    )
    object_map = {item.object_id: item for item in scene.objects}
    if len(courtyards) != 4:
        raise CameraPlanError("scene must provide four courtyard camera anchors")
    for index, anchor in enumerate(courtyards, 1):
        courtyard = object_map[anchor.source_id]
        if courtyard.polygon is None:
            raise CameraPlanError("courtyard camera anchor source has no polygon topology")
        first, second = courtyard.polygon.ring[:2]
        direction = _unit_xy((first.x_m, first.y_m), (second.x_m, second.y_m))
        x_m = _q3(anchor.position.x_m - direction[0] * 6.0)
        y_m = _q3(anchor.position.y_m - direction[1] * 6.0)
        clearance = max(
            MIN_TERRAIN_CLEARANCE_M,
            anchor.position.z_m
            - terrain_height_m(anchor.position.x_m, anchor.position.y_m, scene.extent),
        )
        z_m = _q3(terrain_height_m(x_m, y_m, scene.extent) + clearance)
        specs.append(
            _CameraSpec(
                camera_id=f"camera-courtyard-{index:03d}",
                category="courtyard",
                split="train" if index <= 2 else "val",
                source_anchor_ids=(anchor.anchor_id,),
                base_position=(x_m, y_m, z_m),
                target=(anchor.target.x_m, anchor.target.y_m, anchor.target.z_m),
            )
        )

    bridges = sorted(
        (anchor for anchor in anchors.values() if anchor.anchor_type == "bridge"),
        key=lambda anchor: anchor.anchor_id,
    )
    if len(bridges) != 2:
        raise CameraPlanError("scene must provide two bridge camera anchors")
    bridge_index = 0
    for anchor_number, anchor in enumerate(bridges):
        direction = _unit_xy(
            (anchor.position.x_m, anchor.position.y_m),
            (anchor.target.x_m, anchor.target.y_m),
        )
        perpendicular = (-direction[1], direction[0])
        clearance = max(
            MIN_TERRAIN_CLEARANCE_M,
            anchor.position.z_m
            - terrain_height_m(anchor.position.x_m, anchor.position.y_m, scene.extent),
        )
        for signed_offset in (-4.0, 4.0):
            bridge_index += 1
            x_m = _q3(anchor.position.x_m + perpendicular[0] * signed_offset)
            y_m = _q3(anchor.position.y_m + perpendicular[1] * signed_offset)
            z_m = _q3(terrain_height_m(x_m, y_m, scene.extent) + clearance)
            specs.append(
                _CameraSpec(
                    camera_id=f"camera-bridge-{bridge_index:03d}",
                    category="bridge",
                    split="val" if anchor_number == 0 else "test",
                    source_anchor_ids=(anchor.anchor_id,),
                    base_position=(x_m, y_m, z_m),
                    target=(anchor.target.x_m, anchor.target.y_m, anchor.target.z_m),
                )
            )
    return tuple(specs)


def _inside_building_obb(position: np.ndarray, building: SceneObject) -> bool:
    angle = math.radians(building.transform.yaw_deg)
    cosine, sine = math.cos(angle), math.sin(angle)
    dx = float(position[0]) - building.transform.x_m
    dy = float(position[1]) - building.transform.y_m
    local_x = dx * cosine + dy * sine
    local_y = -dx * sine + dy * cosine
    return (
        abs(local_x) <= building.dimensions.width_m / 2
        and abs(local_y) <= building.dimensions.depth_m / 2
    )


def _position_is_valid(
    position: np.ndarray,
    scene: ScenePlan,
    train_centers: tuple[np.ndarray, ...],
) -> bool:
    if not np.all(np.isfinite(position)):
        return False
    half_width, half_depth = scene.extent.width_m / 2, scene.extent.depth_m / 2
    if abs(position[0]) > half_width or abs(position[1]) > half_depth:
        return False
    if (
        position[2] - terrain_height_m(float(position[0]), float(position[1]), scene.extent)
        < MIN_TERRAIN_CLEARANCE_M - 1e-9
    ):
        return False
    if any(
        _inside_building_obb(position, item)
        for item in scene.objects
        if item.semantic_class == "building"
    ):
        return False
    return all(
        float(np.linalg.norm(position - center)) >= MIN_NONTRAIN_TO_TRAIN_DISTANCE_M
        for center in train_centers
    )


def _look_at_c2w(position: np.ndarray, target: np.ndarray) -> Matrix4:
    forward = target - position
    length = float(np.linalg.norm(forward))
    if not math.isfinite(length) or length <= 1e-9:
        raise CameraPlanError("camera look-at direction is degenerate")
    forward /= length
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    right_length = float(np.linalg.norm(right))
    if right_length <= 1e-9:
        raise CameraPlanError("camera look-at direction is parallel to world up")
    right /= right_length
    down = np.cross(forward, right)
    matrix = np.eye(4)
    matrix[:3, 0] = right
    matrix[:3, 1] = down
    matrix[:3, 2] = forward
    matrix[:3, 3] = position
    return _matrix4(matrix)


def _place_camera(
    spec: _CameraSpec,
    scene: ScenePlan,
    train_centers: tuple[np.ndarray, ...],
    attempt_limit: int,
) -> tuple[np.ndarray, Matrix4, int]:
    base = np.asarray(spec.base_position, dtype=float)
    target = np.asarray(spec.target, dtype=float)
    base_clearance = max(
        MIN_TERRAIN_CLEARANCE_M,
        base[2] - terrain_height_m(float(base[0]), float(base[1]), scene.extent),
    )
    phase = int.from_bytes(hashlib.sha256(spec.camera_id.encode("ascii")).digest()[:2], "big")
    phase_angle = 2 * math.pi * phase / 65536
    for attempt in range(attempt_limit):
        if attempt == 0:
            offset_x = offset_y = 0.0
        else:
            slot = (attempt - 1) % 8
            ring = (attempt - 1) // 8 + 1
            angle = phase_angle + slot * math.pi / 4
            offset_x = 2.0 * ring * math.cos(angle)
            offset_y = 2.0 * ring * math.sin(angle)
        x_m = _q3(base[0] + offset_x)
        y_m = _q3(base[1] + offset_y)
        try:
            z_m = _q3(terrain_height_m(x_m, y_m, scene.extent) + base_clearance)
        except ValueError:
            continue
        position = np.array([x_m, y_m, z_m], dtype=float)
        if not _position_is_valid(position, scene, train_centers):
            continue
        try:
            matrix = _look_at_c2w(position, target)
        except CameraPlanError:
            continue
        return position, matrix, attempt + 1
    raise CameraPlanError(f"camera placement attempt limit reached for {spec.camera_id}")


def _projected_buildings(
    matrix: Matrix4,
    intrinsics: CameraIntrinsics,
    scene: ScenePlan,
) -> tuple[str, ...]:
    c2w = np.asarray(matrix, dtype=float)
    rotation, eye = c2w[:3, :3], c2w[:3, 3]
    visible = []
    for building in (item for item in scene.objects if item.semantic_class == "building"):
        world = np.array(
            [building.transform.x_m, building.transform.y_m, building.transform.z_m],
            dtype=float,
        )
        camera_point = rotation.T @ (world - eye)
        if camera_point[2] <= PROJECTION_NEAR_M:
            continue
        u = intrinsics.fx * camera_point[0] / camera_point[2] + intrinsics.cx
        v = intrinsics.fy * camera_point[1] / camera_point[2] + intrinsics.cy
        if 0.0 <= u < intrinsics.width_px and 0.0 <= v < intrinsics.height_px:
            visible.append(building.object_id)
    return tuple(sorted(visible))


def _coverage_rows(
    cameras: tuple[CameraPose, ...],
    scene: ScenePlan,
) -> tuple[tuple[SpatialCellCoverage, ...], tuple[ClusterCoverage, ...]]:
    object_map = {item.object_id: item for item in scene.objects}
    cells: Counter[str] = Counter()
    clusters: Counter[ClusterId] = Counter()
    for camera in cameras:
        visible = [object_map[identifier] for identifier in camera.visible_building_ids]
        cells.update({item.spatial_cell for item in visible})
        clusters.update({item.cluster for item in visible if item.cluster is not None})
    expected_cells = [f"cell-r{row}-c{column}" for row in range(1, 4) for column in range(1, 5)]
    if any(cells[cell] < 2 for cell in expected_cells):
        raise CameraPlanError(
            "projection coverage requires every spatial cell in at least two cameras"
        )
    if any(clusters[cluster] < 6 for cluster in ("creekside", "central", "upper")):
        raise CameraPlanError("projection coverage requires every cluster in at least six cameras")
    return (
        tuple(
            SpatialCellCoverage(spatial_cell=cell, camera_count=cells[cell])
            for cell in expected_cells
        ),
        tuple(
            ClusterCoverage(cluster=cluster, camera_count=clusters[cluster])
            for cluster in ("creekside", "central", "upper")
        ),
    )


def _validate_cameras_against_scene(plan: CameraPlan, scene: ScenePlan) -> None:
    anchor_map = {anchor.anchor_id: anchor for anchor in scene.camera_anchors}
    buildings = [item for item in scene.objects if item.semantic_class == "building"]
    specs = _build_specs(scene)
    expected_train_centers: list[np.ndarray] = []
    train_centers = tuple(
        np.asarray(camera.c2w_opencv, dtype=float)[:3, 3]
        for camera in plan.cameras
        if camera.split == "train"
    )
    for camera, spec in zip(plan.cameras, specs, strict=True):
        if camera.source_anchor_ids != spec.source_anchor_ids:
            raise ValueError("camera topology-derived source anchor provenance is invalid")
        expected_target = PlanPoint(
            x_m=spec.target[0],
            y_m=spec.target[1],
            z_m=spec.target[2],
        )
        if camera.look_at_target != expected_target:
            raise ValueError("camera topology-derived look-at target is invalid")
        expected_anchor_types = {
            "outer": {"cluster"},
            "ground": {"route", "intersection"},
            "courtyard": {"courtyard"},
            "bridge": {"bridge"},
        }[camera.category]
        if any(identifier not in anchor_map for identifier in camera.source_anchor_ids):
            raise ValueError("camera source anchor is unknown")
        if {
            anchor_map[identifier].anchor_type for identifier in camera.source_anchor_ids
        } - expected_anchor_types:
            raise ValueError("camera source anchor type does not match category")
        expected_forbidden = tuple(expected_train_centers) if spec.split != "train" else ()
        expected_position, expected_matrix, expected_attempts = _place_camera(
            spec,
            scene,
            expected_forbidden,
            MAX_CAMERA_PLACEMENT_ATTEMPTS,
        )
        if camera.c2w_opencv != expected_matrix or camera.placement_attempts != expected_attempts:
            raise ValueError("deterministic camera pose does not match scene topology")
        if spec.split == "train":
            expected_train_centers.append(expected_position)
        position = np.asarray(camera.c2w_opencv, dtype=float)[:3, 3]
        comparison_centers = train_centers if camera.split != "train" else ()
        if not _position_is_valid(position, scene, comparison_centers):
            raise ValueError("camera position violates terrain, OBB, extent, or split separation")
        if any(_inside_building_obb(position, building) for building in buildings):
            raise ValueError("camera center is inside a rotated building OBB")
        expected_visible = _projected_buildings(camera.c2w_opencv, camera.intrinsics, scene)
        if camera.visible_building_ids != expected_visible:
            raise ValueError("camera projection coverage payload is invalid")
    try:
        cells, clusters = _coverage_rows(plan.cameras, scene)
    except CameraPlanError as exc:
        raise ValueError(str(exc)) from exc
    if plan.spatial_cell_coverage != cells or plan.cluster_coverage != clusters:
        raise ValueError("camera projection coverage summary is invalid")


def build_camera_plan(
    scene: ScenePlan | None = None,
    *,
    attempt_limit: int = MAX_CAMERA_PLACEMENT_ATTEMPTS,
) -> CameraPlan:
    """Build the fixed 24-view canary plan from verified scene topology."""

    active = scene or build_scene_plan()
    if (
        not isinstance(attempt_limit, int)
        or isinstance(attempt_limit, bool)
        or not 1 <= attempt_limit <= MAX_CAMERA_PLACEMENT_ATTEMPTS
    ):
        raise ValueError(
            f"attempt limit must be an integer between 1 and {MAX_CAMERA_PLACEMENT_ATTEMPTS}",
        )
    _preflight_anchors(active)
    digest = _scene_digest(active)
    if digest != EXPECTED_SCENE_PLAN_SHA256:
        raise CameraPlanError("scene digest is unknown; camera plan generation is fail-closed")
    specs = _build_specs(active)
    cameras: list[CameraPose] = []
    train_centers: list[np.ndarray] = []
    for sequence_index, spec in enumerate(specs, 1):
        forbidden = tuple(train_centers) if spec.split != "train" else ()
        position, opencv, attempts = _place_camera(spec, active, forbidden, attempt_limit)
        intrinsics = _intrinsics(_FOV_BY_CATEGORY[spec.category])
        blender = _matrix4(np.asarray(opencv) @ np.diag([1.0, -1.0, -1.0, 1.0]))
        camera = CameraPose(
            camera_id=spec.camera_id,
            sequence_index=sequence_index,
            category=spec.category,
            split=spec.split,
            source_anchor_ids=spec.source_anchor_ids,
            fov_x_deg=_FOV_BY_CATEGORY[spec.category],
            intrinsics=intrinsics,
            look_at_target=PlanPoint(
                x_m=spec.target[0],
                y_m=spec.target[1],
                z_m=spec.target[2],
            ),
            c2w_opencv=opencv,
            c2w_blender=blender,
            visible_building_ids=_projected_buildings(opencv, intrinsics, active),
            placement_attempts=attempts,
        )
        cameras.append(camera)
        if spec.split == "train":
            train_centers.append(position)
    camera_tuple = tuple(cameras)
    cells, clusters = _coverage_rows(camera_tuple, active)
    return CameraPlan(
        plan_id="synthetic-mountain-village-camera-plan-v1",
        scene_plan_id=active.plan_id,
        scene_plan_sha256=digest,
        coordinate_system="opencv-c2w-right-down-forward-meters",
        blender_conversion="c2w_blender=c2w_opencv@diag(1,-1,-1,1)",
        cameras=camera_tuple,
        spatial_cell_coverage=cells,
        cluster_coverage=clusters,
    )


def canonical_camera_plan_bytes(plan: CameraPlan) -> bytes:
    text = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CameraPlanError(f"camera plan contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int]:
    return result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns


def load_camera_plan(path: Path) -> CameraPlan:
    """Load only bounded, stable, canonical camera-plan JSON from a direct path."""

    path = Path(path).absolute()
    try:
        parent = path.parent
        if _is_linklike(path) or _is_linklike(parent) or parent.resolve(strict=True) != parent:
            raise CameraPlanError("camera plan path has a redirected leaf or parent")
        before = path.stat()
        if before.st_size <= 0 or before.st_size > MAX_CAMERA_PLAN_BYTES:
            raise CameraPlanError("camera plan size is invalid")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_signature(before) != _stat_signature(opened):
                raise CameraPlanError("camera plan changed before bounded read")
            raw = stream.read(MAX_CAMERA_PLAN_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_CAMERA_PLAN_BYTES
            or _stat_signature(opened) != _stat_signature(after_open)
            or _stat_signature(before) != _stat_signature(after)
        ):
            raise CameraPlanError("camera plan changed during bounded read")
        json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        plan = CameraPlan.model_validate_json(raw)
        if raw != canonical_camera_plan_bytes(plan):
            raise CameraPlanError("camera plan must be canonical JSON")
        return plan
    except CameraPlanError:
        raise
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise CameraPlanError(f"camera plan cannot be trusted: {exc}") from exc
