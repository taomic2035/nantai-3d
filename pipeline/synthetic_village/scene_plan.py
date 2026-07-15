"""Deterministic, Blender-independent synthetic-village scene planning."""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .contracts import DefaultResourceRecipe, SceneExtent
from .defaults import build_default_recipe

DEFAULT_SEED = 20260715
MIN_BUILDING_SEPARATION_M = 8.0
MIN_BUILDING_FOOTPRINT_CLEARANCE_M = 0.75
FEATURE_RESERVATION_CLEARANCE_M = 1.0
MAX_PLATFORM_RELIEF_M = 3.0
MAX_PLACEMENT_ATTEMPTS = 10_000
MAX_SCENE_PLAN_BYTES = 8 * 1024 * 1024
MM = 0.001

ClusterId = Literal["creekside", "central", "upper"]
SemanticClass = Literal[
    "building",
    "bridge",
    "creek",
    "pond",
    "path",
    "field",
    "orchard",
    "bamboo",
    "courtyard",
    "retaining-wall",
    "prop",
]
AnchorType = Literal["cluster", "route", "intersection", "courtyard", "bridge"]

CLUSTER_CENTERS: dict[ClusterId, tuple[float, float]] = {
    "creekside": (-180.0, -90.0),
    "central": (0.0, 0.0),
    "upper": (170.0, 115.0),
}
CLUSTER_BUDGETS: dict[ClusterId, int] = {
    "creekside": 22,
    "central": 28,
    "upper": 20,
}
CLUSTER_ORDER = tuple(CLUSTER_CENTERS)
SEMANTIC_ORDER = (
    "building",
    "bridge",
    "creek",
    "pond",
    "path",
    "field",
    "orchard",
    "bamboo",
    "courtyard",
    "retaining-wall",
    "prop",
)
FEATURE_BUDGETS = {
    "bridge": 2,
    "creek": 1,
    "pond": 1,
    "path": 6,
    "field": 12,
    "orchard": 2,
    "bamboo": 4,
    "courtyard": 4,
    "retaining-wall": 8,
    "prop": 16,
}


class ScenePlanError(ValueError):
    """Stable public error for untrusted scene-plan loading."""


class PlacementError(ValueError):
    """Raised when rejection sampling cannot satisfy the scene contract."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Transform(FrozenModel):
    x_m: float = Field(allow_inf_nan=False)
    y_m: float = Field(allow_inf_nan=False)
    z_m: float = Field(allow_inf_nan=False)
    yaw_deg: float = Field(ge=-180.0, lt=180.0, allow_inf_nan=False)


class Dimensions(FrozenModel):
    width_m: float = Field(gt=0, allow_inf_nan=False)
    depth_m: float = Field(gt=0, allow_inf_nan=False)
    height_m: float = Field(gt=0, allow_inf_nan=False)


class PlanPoint(FrozenModel):
    x_m: float = Field(allow_inf_nan=False)
    y_m: float = Field(allow_inf_nan=False)
    z_m: float = Field(allow_inf_nan=False)


class PolylineTopology(FrozenModel):
    points: tuple[PlanPoint, ...] = Field(min_length=2)
    width_m: float = Field(gt=0, allow_inf_nan=False)
    route_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    segment_index: int = Field(ge=0)


class PolygonTopology(FrozenModel):
    ring: tuple[PlanPoint, ...] = Field(min_length=5)

    @model_validator(mode="after")
    def _closed_ring(self) -> PolygonTopology:
        if self.ring[0] != self.ring[-1]:
            raise ValueError("polygon ring must be explicitly closed")
        if len(set(self.ring[:-1])) < 4:
            raise ValueError("polygon ring must contain four distinct vertices")
        return self


class BridgeTopology(FrozenModel):
    bank_anchors: tuple[PlanPoint, PlanPoint]
    crosses_object_id: str = Field(pattern=r"^creek-[a-z0-9]+(?:-[a-z0-9]+)*$")


class CameraAnchor(FrozenModel):
    anchor_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    anchor_type: AnchorType
    position: PlanPoint
    target: PlanPoint
    source_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SceneObject(FrozenModel):
    object_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    semantic_class: SemanticClass
    instance_id: int = Field(ge=1)
    transform: Transform
    dimensions: Dimensions
    material_family: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    spatial_cell: str = Field(pattern=r"^cell-r[1-3]-c[1-4]$")
    cluster: ClusterId | None
    building_role: Literal["residence", "community-hall"] | None = None
    base_z_m: float | None = Field(default=None, allow_inf_nan=False)
    platform_relief_m: float = Field(ge=0, allow_inf_nan=False)
    polyline: PolylineTopology | None = None
    polygon: PolygonTopology | None = None
    bridge: BridgeTopology | None = None
    overlap_object_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_shape_contract(self) -> SceneObject:
        if not self.object_id.startswith(f"{self.semantic_class}-"):
            raise ValueError("object ID prefix must match semantic class")
        if self.semantic_class == "building":
            if self.cluster is None or self.building_role is None or self.base_z_m is None:
                raise ValueError("building cluster, role and base are required")
            if self.polyline or self.polygon or self.bridge:
                raise ValueError("building topology must use its rotated footprint")
            if self.overlap_object_ids:
                raise ValueError("building overlaps cannot be declared")
        else:
            if self.cluster is not None:
                raise ValueError("non-building cluster must be null")
            if self.building_role is not None or self.base_z_m is not None:
                raise ValueError("non-building role and base must be null")
            if self.platform_relief_m != 0.0:
                raise ValueError("non-building platform relief must be zero")
        if self.semantic_class in {"creek", "path", "retaining-wall"}:
            if self.polyline is None or self.polygon or self.bridge:
                raise ValueError("linear feature requires only polyline topology")
        elif self.semantic_class in {"pond", "field", "orchard", "bamboo", "courtyard"}:
            if self.polygon is None or self.polyline or self.bridge:
                raise ValueError("area feature requires only polygon topology")
        elif self.semantic_class == "bridge":
            if self.bridge is None or self.polyline or self.polygon:
                raise ValueError("bridge requires only bridge topology")
        elif self.semantic_class in {"prop", "building"} and (
            self.polyline or self.polygon or self.bridge
        ):
            raise ValueError("solid object must not contain route topology")
        if len(self.overlap_object_ids) != len(set(self.overlap_object_ids)):
            raise ValueError("declared overlap IDs must be unique")
        return self


def _q(value: float) -> float:
    return round(float(value) + 0.0, 3)


def _is_mm(value: float) -> bool:
    return abs(value * 1000 - round(value * 1000)) <= 1e-7


def terrain_height_m(x_m: float, y_m: float, extent: SceneExtent | None = None) -> float:
    """Return the tracked analytic terrain height, quantized to one millimeter."""

    active = extent or build_default_recipe().scene
    if not math.isfinite(x_m) or not math.isfinite(y_m):
        raise ValueError("terrain coordinates must be finite")
    if abs(x_m) > active.width_m / 2 or abs(y_m) > active.depth_m / 2:
        raise ValueError("terrain coordinates are outside the scene extent")
    t = (y_m + active.depth_m / 2) / active.depth_m
    interior = (
        (
            9.0 * math.sin(math.pi * (x_m + active.width_m / 2) / active.width_m)
            + 4.0 * math.sin(2 * math.pi * (x_m + active.width_m / 2) / active.width_m)
        )
        * 4
        * t
        * (1 - t)
    )
    return _q(active.relief_m * t + interior)


def _point(x_m: float, y_m: float, extent: SceneExtent) -> PlanPoint:
    x_m, y_m = _q(x_m), _q(y_m)
    return PlanPoint(x_m=x_m, y_m=y_m, z_m=terrain_height_m(x_m, y_m, extent))


def _cell_id(x_m: float, y_m: float, extent: SceneExtent) -> str:
    column = min(3, max(0, int((x_m + extent.width_m / 2) / (extent.width_m / 4))))
    row = min(2, max(0, int((y_m + extent.depth_m / 2) / (extent.depth_m / 3))))
    return f"cell-r{row + 1}-c{column + 1}"


def _footprint_corners_values(
    x_m: float,
    y_m: float,
    yaw_deg: float,
    dimensions: Dimensions,
) -> tuple[tuple[float, float], ...]:
    angle = math.radians(yaw_deg)
    cosine, sine = math.cos(angle), math.sin(angle)
    return tuple(
        (
            x_m + local_x * cosine - local_y * sine,
            y_m + local_x * sine + local_y * cosine,
        )
        for local_x, local_y in (
            (-dimensions.width_m / 2, -dimensions.depth_m / 2),
            (dimensions.width_m / 2, -dimensions.depth_m / 2),
            (dimensions.width_m / 2, dimensions.depth_m / 2),
            (-dimensions.width_m / 2, dimensions.depth_m / 2),
        )
    )


def _footprint_corners(item: SceneObject) -> tuple[tuple[float, float], ...]:
    return _footprint_corners_values(
        item.transform.x_m,
        item.transform.y_m,
        item.transform.yaw_deg,
        item.dimensions,
    )


def _projection(
    polygon: tuple[tuple[float, float], ...],
    axis: tuple[float, float],
) -> tuple[float, float]:
    values = [x_m * axis[0] + y_m * axis[1] for x_m, y_m in polygon]
    return min(values), max(values)


def _polygons_intersect(
    left: tuple[tuple[float, float], ...],
    right: tuple[tuple[float, float], ...],
) -> bool:
    for polygon in (left, right):
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            edge_x, edge_y = end[0] - start[0], end[1] - start[1]
            length = math.hypot(edge_x, edge_y)
            axis = (-edge_y / length, edge_x / length)
            left_min, left_max = _projection(left, axis)
            right_min, right_max = _projection(right, axis)
            if left_max < right_min or right_max < left_min:
                return False
    return True


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    edge_x, edge_y = end[0] - start[0], end[1] - start[1]
    length_squared = edge_x * edge_x + edge_y * edge_y
    fraction = ((point[0] - start[0]) * edge_x + (point[1] - start[1]) * edge_y) / length_squared
    fraction = min(1.0, max(0.0, fraction))
    nearest = (start[0] + fraction * edge_x, start[1] + fraction * edge_y)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _segments_intersect(a, b, c, d) -> bool:
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return cross(a, b, c) * cross(a, b, d) <= 0 and cross(c, d, a) * cross(c, d, b) <= 0


def _polygon_clearance_m(left, right) -> float:
    if _polygons_intersect(left, right):
        return 0.0
    distances = []
    for points, edges in ((left, right), (right, left)):
        for point in points:
            for index, start in enumerate(edges):
                distances.append(
                    _point_segment_distance(point, start, edges[(index + 1) % len(edges)])
                )
    return min(distances)


def _polygon_polyline_clearance_m(polygon, points) -> float:
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        for point_index in range(len(points) - 1):
            if _segments_intersect(start, end, points[point_index], points[point_index + 1]):
                return 0.0
    distances = []
    for point in polygon:
        for index in range(len(points) - 1):
            distances.append(_point_segment_distance(point, points[index], points[index + 1]))
    for point in points:
        for index, start in enumerate(polygon):
            distances.append(
                _point_segment_distance(point, start, polygon[(index + 1) % len(polygon)])
            )
    return min(distances)


def building_footprint_clearance_m(left: SceneObject, right: SceneObject) -> float:
    if left.semantic_class != "building" or right.semantic_class != "building":
        raise ValueError("footprint clearance is defined only for buildings")
    return _polygon_clearance_m(_footprint_corners(left), _footprint_corners(right))


def _ring_xy(topology: PolygonTopology) -> tuple[tuple[float, float], ...]:
    return tuple((point.x_m, point.y_m) for point in topology.ring[:-1])


def _reserved_conflict_polygon(
    footprint: tuple[tuple[float, float], ...],
    feature: SceneObject,
) -> bool:
    if feature.polyline:
        points = tuple((point.x_m, point.y_m) for point in feature.polyline.points)
        return (
            _polygon_polyline_clearance_m(footprint, points)
            < feature.polyline.width_m / 2 + FEATURE_RESERVATION_CLEARANCE_M
        )
    if feature.polygon:
        return (
            _polygon_clearance_m(footprint, _ring_xy(feature.polygon))
            < FEATURE_RESERVATION_CLEARANCE_M
        )
    if feature.bridge:
        return _polygon_clearance_m(footprint, _footprint_corners(feature)) < 1.0
    return False


def building_overlaps_reserved_feature(
    building: SceneObject,
    objects: tuple[SceneObject, ...],
) -> bool:
    footprint = _footprint_corners(building)
    reserved = {"creek", "pond", "path", "field", "orchard", "bamboo", "courtyard", "bridge"}
    return any(
        item.semantic_class in reserved
        and item.object_id not in building.overlap_object_ids
        and _reserved_conflict_polygon(footprint, item)
        for item in objects
    )


def undeclared_feature_overlaps(
    objects: tuple[SceneObject, ...],
) -> tuple[tuple[str, str], ...]:
    areas = [item for item in objects if item.polygon]
    linear = [item for item in objects if item.polyline]
    conflicts: list[tuple[str, str]] = []
    for index, left in enumerate(areas):
        for right in areas[index + 1 :]:
            if _polygon_clearance_m(_ring_xy(left.polygon), _ring_xy(right.polygon)) != 0:
                continue
            if (
                right.object_id not in left.overlap_object_ids
                and left.object_id not in right.overlap_object_ids
            ):
                conflicts.append((left.object_id, right.object_id))
    for area in areas:
        for route in linear:
            points = tuple((point.x_m, point.y_m) for point in route.polyline.points)
            distance = _polygon_polyline_clearance_m(_ring_xy(area.polygon), points)
            if distance > route.polyline.width_m / 2:
                continue
            if (
                route.object_id not in area.overlap_object_ids
                and area.object_id not in route.overlap_object_ids
            ):
                conflicts.append((area.object_id, route.object_id))
    return tuple(sorted(conflicts))


def _stable_sort_key(item: SceneObject) -> tuple[int, int, str]:
    cluster = CLUSTER_ORDER.index(item.cluster) if item.cluster in CLUSTER_ORDER else 0
    return SEMANTIC_ORDER.index(item.semantic_class), cluster, item.object_id


def _bridge_crosses_creek(bridge: SceneObject, creek: SceneObject) -> bool:
    anchors = tuple((point.x_m, point.y_m) for point in bridge.bridge.bank_anchors)
    creek_points = tuple((point.x_m, point.y_m) for point in creek.polyline.points)
    for index in range(len(creek_points) - 1):
        start, end = creek_points[index], creek_points[index + 1]
        if not _segments_intersect(anchors[0], anchors[1], start, end):
            continue
        tangent_x, tangent_y = end[0] - start[0], end[1] - start[1]
        sides = [
            tangent_x * (anchor[1] - start[1]) - tangent_y * (anchor[0] - start[0])
            for anchor in anchors
        ]
        if sides[0] * sides[1] < 0:
            return True
    return False


def route_network_connects_required_nodes(plan: ScenePlan) -> bool:
    graph: dict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    for item in plan.objects:
        if item.semantic_class == "path":
            points = [(point.x_m, point.y_m) for point in item.polyline.points]
        elif item.semantic_class == "bridge":
            points = [(point.x_m, point.y_m) for point in item.bridge.bank_anchors]
        else:
            continue
        for left, right in zip(points, points[1:], strict=False):
            graph[left].add(right)
            graph[right].add(left)
    required = [CLUSTER_CENTERS[cluster] for cluster in CLUSTER_ORDER]
    if any(node not in graph for node in required):
        return False
    visited = {required[0]}
    queue = deque(visited)
    while queue:
        node = queue.popleft()
        for neighbor in graph[node] - visited:
            visited.add(neighbor)
            queue.append(neighbor)
    return all(node in visited for node in required)


def path_bridge_crossings_are_direct(plan: ScenePlan) -> bool:
    creek = next(item for item in plan.objects if item.semantic_class == "creek")
    creek_points = tuple((point.x_m, point.y_m) for point in creek.polyline.points)
    bridge_edges = {
        frozenset((point.x_m, point.y_m) for point in bridge.bridge.bank_anchors): 0
        for bridge in plan.objects
        if bridge.semantic_class == "bridge"
    }
    for path in (item for item in plan.objects if item.semantic_class == "path"):
        points = tuple((point.x_m, point.y_m) for point in path.polyline.points)
        for left, right in zip(points, points[1:], strict=False):
            edge = frozenset((left, right))
            if edge in bridge_edges:
                bridge_edges[edge] += 1
                continue
            if any(
                _segments_intersect(left, right, creek_points[index], creek_points[index + 1])
                for index in range(len(creek_points) - 1)
            ):
                return False
    return bool(bridge_edges) and all(count == 1 for count in bridge_edges.values())


def _expected_object_ids() -> list[str]:
    identifiers = [
        f"building-{cluster}-{number:03d}"
        for cluster in CLUSTER_ORDER
        for number in range(1, CLUSTER_BUDGETS[cluster] + 1)
    ]
    identifiers.extend(("bridge-lower-001", "bridge-upper-002", "creek-main-001"))
    identifiers.append("pond-irrigation-001")
    identifiers.extend(f"path-network-{index:03d}" for index in range(1, 7))
    identifiers.extend(f"field-terrace-{index:03d}" for index in range(1, 13))
    identifiers.extend(f"orchard-slope-{index:03d}" for index in range(1, 3))
    identifiers.extend(f"bamboo-grove-{index:03d}" for index in range(1, 5))
    identifiers.extend(f"courtyard-public-{index:03d}" for index in range(1, 5))
    identifiers.extend(f"retaining-wall-{index:03d}" for index in range(1, 9))
    identifiers.extend(f"prop-rural-{index:03d}" for index in range(1, 17))
    return identifiers


class ScenePlan(FrozenModel):
    schema_version: Literal[1] = 1
    plan_id: Literal["synthetic-mountain-village-scene-v1"]
    recipe_id: Literal["synthetic-mountain-village-v1"]
    coordinate_system: Literal["right-handed-z-up-meters"]
    seed: Literal[20260715]
    generator: Literal["numpy-pcg64-raw-v1"] = "numpy-pcg64-raw-v1"
    terrain_model_id: Literal["nantai-terrain-height-v1"] = "nantai-terrain-height-v1"
    extent: SceneExtent
    terrain_min_m: float = Field(allow_inf_nan=False)
    terrain_max_m: float = Field(allow_inf_nan=False)
    minimum_building_separation_m: Literal[8.0] = 8.0
    minimum_footprint_clearance_m: Literal[0.75] = 0.75
    maximum_platform_relief_m: Literal[3.0] = 3.0
    placement_attempts: int = Field(ge=70, le=MAX_PLACEMENT_ATTEMPTS)
    objects: tuple[SceneObject, ...] = Field(min_length=1)
    camera_anchors: tuple[CameraAnchor, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_complete_plan(self) -> ScenePlan:
        expected_extent = SceneExtent(width_m=700.0, depth_m=500.0, relief_m=120.0)
        if self.extent != expected_extent:
            raise ValueError("scene extent must match the tracked v1 recipe")
        if self.terrain_min_m != 0.0 or self.terrain_max_m != 120.0:
            raise ValueError("terrain min/max must encode the actual 120 m relief")
        if self.terrain_max_m - self.terrain_min_m != self.extent.relief_m:
            raise ValueError("terrain range must match extent relief")
        ids = [item.object_id for item in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("scene object IDs must be globally unique")
        if [item.instance_id for item in self.objects] != list(range(1, len(ids) + 1)):
            raise ValueError("instance IDs must be globally contiguous")
        if list(self.objects) != sorted(self.objects, key=_stable_sort_key):
            raise ValueError("scene objects are not in stable canonical order")
        if ids != _expected_object_ids():
            raise ValueError("complete stable object ID sequence is invalid")
        counts = Counter(item.semantic_class for item in self.objects)
        expected_counts = {"building": 70, **FEATURE_BUDGETS}
        if counts != expected_counts:
            raise ValueError("scene feature budgets or complete ID sequence are invalid")
        buildings = [item for item in self.objects if item.semantic_class == "building"]
        if Counter(item.cluster for item in buildings) != CLUSTER_BUDGETS:
            raise ValueError("building cluster budgets must be exactly 22/28/20")
        expected_cells = {f"cell-r{row}-c{column}" for row in range(1, 4) for column in range(1, 5)}
        if {item.spatial_cell for item in buildings} != expected_cells:
            raise ValueError("all twelve spatial cells must contain a building")
        if sum(item.building_role == "community-hall" for item in buildings) != 1:
            raise ValueError("exactly one community-hall building is required")
        object_map = {item.object_id: item for item in self.objects}
        expected_features = {
            row["object_id"]: _make_scene_object(row, 1) for row in _feature_rows(self.extent)
        }
        expected_buildings, expected_attempts = _expected_buildings_and_attempts(
            self.extent,
        )
        if self.placement_attempts != expected_attempts:
            raise ValueError("placement attempt count does not match deterministic seed")
        for item in self.objects:
            if item.semantic_class != "building":
                expected_feature = expected_features.get(item.object_id)
                actual_payload = item.model_dump(mode="json", exclude={"instance_id"})
                expected_payload = expected_feature.model_dump(
                    mode="json",
                    exclude={"instance_id"},
                )
                if actual_payload != expected_payload:
                    raise ValueError(
                        "non-building transform, dimensions and topology must match v1",
                    )
            if any(identifier not in object_map for identifier in item.overlap_object_ids):
                raise ValueError("declared overlap references an unknown object")
            numeric = [
                item.transform.x_m,
                item.transform.y_m,
                item.transform.z_m,
                item.transform.yaw_deg,
                item.dimensions.width_m,
                item.dimensions.depth_m,
                item.dimensions.height_m,
                item.platform_relief_m,
            ]
            if item.base_z_m is not None:
                numeric.append(item.base_z_m)
            if not all(_is_mm(value) for value in numeric) or item.transform.yaw_deg % 5:
                raise ValueError("object numbers must use millimeter and five-degree grids")
            if item.semantic_class in {"building", "bridge", "prop"}:
                footprint = _footprint_corners(item)
                if any(
                    abs(x_m) > self.extent.width_m / 2 + 1e-7
                    or abs(y_m) > self.extent.depth_m / 2 + 1e-7
                    for x_m, y_m in footprint
                ):
                    raise ValueError("scene object footprint exceeds extent")
            if item.spatial_cell != _cell_id(item.transform.x_m, item.transform.y_m, self.extent):
                raise ValueError("spatial cell does not match object position")
            topology_points = []
            if item.polyline:
                topology_points.extend(item.polyline.points)
            if item.polygon:
                topology_points.extend(item.polygon.ring)
            if item.bridge:
                topology_points.extend(item.bridge.bank_anchors)
            for point in topology_points:
                if point.z_m != terrain_height_m(point.x_m, point.y_m, self.extent):
                    raise ValueError("topology point must conform to terrain")
                if not all(_is_mm(value) for value in (point.x_m, point.y_m, point.z_m)):
                    raise ValueError("topology point must use the millimeter grid")
            if item.polyline or item.polygon or item.bridge:
                frame = _topology_frame(
                    item.polyline,
                    item.polygon,
                    item.bridge,
                    item.dimensions.depth_m,
                )
                expected_x, expected_y, expected_width, expected_depth, expected_yaw = frame
                if (
                    item.transform.x_m != expected_x
                    or item.transform.y_m != expected_y
                    or item.transform.yaw_deg != expected_yaw
                    or item.dimensions.width_m != expected_width
                    or item.dimensions.depth_m != expected_depth
                    or item.transform.z_m
                    != _q(
                        terrain_height_m(expected_x, expected_y, self.extent)
                        + item.dimensions.height_m / 2
                    )
                ):
                    raise ValueError("feature transform and dimensions must derive from topology")
            if item.semantic_class == "creek" and (
                item.polyline.width_m != 8.0
                or item.polyline.route_id != "creek-main"
                or item.polyline.segment_index != 0
            ):
                raise ValueError("creek route contract is invalid")
            if item.semantic_class == "path" and (
                item.polyline.width_m != 3.2 or item.polyline.route_id != "village-network"
            ):
                raise ValueError("path width or route contract is invalid")
            if item.semantic_class == "retaining-wall" and (
                item.polyline.width_m != 0.6 or item.polyline.segment_index != 0
            ):
                raise ValueError("retaining-wall route contract is invalid")
        for item in buildings:
            corner_heights = [
                terrain_height_m(x, y, self.extent) for x, y in _footprint_corners(item)
            ]
            expected_base = _q(max(corner_heights))
            expected_relief = _q(max(corner_heights) - min(corner_heights))
            if item.base_z_m != expected_base:
                raise ValueError("building base does not match terrain platform")
            if item.platform_relief_m != expected_relief or expected_relief > MAX_PLATFORM_RELIEF_M:
                raise ValueError("building platform relief does not match terrain fit")
            if item.transform.z_m != _q(expected_base + item.dimensions.height_m / 2):
                raise ValueError("building elevation does not match its base")
            if building_overlaps_reserved_feature(item, self.objects):
                raise ValueError("building overlaps an undeclared reserved feature")
        for index, left in enumerate(buildings):
            for right in buildings[index + 1 :]:
                if (
                    math.hypot(
                        left.transform.x_m - right.transform.x_m,
                        left.transform.y_m - right.transform.y_m,
                    )
                    < MIN_BUILDING_SEPARATION_M
                ):
                    raise ValueError("building centers violate minimum separation")
                if building_footprint_clearance_m(left, right) + 1e-9 < 0.75:
                    raise ValueError("building footprint clearance is below 0.75 m")
        actual_building_payloads = [
            item.model_dump(mode="json", exclude={"instance_id"}) for item in buildings
        ]
        expected_building_payloads = [
            item.model_dump(mode="json", exclude={"instance_id"}) for item in expected_buildings
        ]
        if actual_building_payloads != expected_building_payloads:
            raise ValueError("deterministic building payload does not match seed and generator")
        creek = next(item for item in self.objects if item.semantic_class == "creek")
        for bridge in (item for item in self.objects if item.semantic_class == "bridge"):
            if bridge.bridge.crosses_object_id != creek.object_id or not _bridge_crosses_creek(
                bridge, creek
            ):
                raise ValueError("bridge must cross the tracked creek between opposite banks")
            anchors = bridge.bridge.bank_anchors
            if anchors[0] == anchors[1]:
                raise ValueError("bridge bank anchors must be on opposite sides")
        routes: dict[str, list[int]] = defaultdict(list)
        for path in (item for item in self.objects if item.semantic_class == "path"):
            routes[path.polyline.route_id].append(path.polyline.segment_index)
        if any(sorted(indices) != list(range(len(indices))) for indices in routes.values()):
            raise ValueError("path route segment sequence is incomplete")
        if not route_network_connects_required_nodes(self):
            raise ValueError("path and bridge network must connect all clusters")
        if not path_bridge_crossings_are_direct(self):
            raise ValueError("path bridge crossings must be direct and occur exactly once")
        if undeclared_feature_overlaps(self.objects):
            raise ValueError("scene contains an undeclared feature overlap")
        anchor_ids = [anchor.anchor_id for anchor in self.camera_anchors]
        if anchor_ids != sorted(anchor_ids) or len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("camera anchor IDs must be unique and stable")
        if {anchor.anchor_type for anchor in self.camera_anchors} != {
            "cluster",
            "route",
            "intersection",
            "courtyard",
            "bridge",
        }:
            raise ValueError("camera anchor taxonomy is incomplete")
        for anchor in self.camera_anchors:
            if anchor.source_id not in object_map and anchor.source_id not in {
                *CLUSTER_ORDER,
                "village-network",
                "intersection-central",
            }:
                raise ValueError("camera anchor source is unknown")
        if self.camera_anchors != _camera_anchors(self.extent, self.objects):
            raise ValueError("camera anchors do not match stable topology-derived anchors")
        return self


def _polyline(
    coords: tuple[tuple[float, float], ...],
    width_m: float,
    route_id: str,
    segment_index: int,
    extent: SceneExtent,
) -> PolylineTopology:
    return PolylineTopology(
        points=tuple(_point(x, y, extent) for x, y in coords),
        width_m=_q(width_m),
        route_id=route_id,
        segment_index=segment_index,
    )


def _topology_frame(
    polyline: PolylineTopology | None,
    polygon: PolygonTopology | None,
    bridge: BridgeTopology | None,
    depth_m: float,
) -> tuple[float, float, float, float, float]:
    if polyline:
        points = polyline.points
        padding = polyline.width_m
        yaw = 0.0
    elif polygon:
        points = polygon.ring[:-1]
        padding = 0.0
        yaw = 0.0
    elif bridge:
        points = bridge.bank_anchors
        padding = 0.0
        delta_x = points[1].x_m - points[0].x_m
        delta_y = points[1].y_m - points[0].y_m
        yaw = _q(round(math.degrees(math.atan2(delta_y, delta_x)) / 5) * 5)
    else:
        raise ValueError("topology frame requires topology")
    x_values = [point.x_m for point in points]
    y_values = [point.y_m for point in points]
    center_x = _q((min(x_values) + max(x_values)) / 2)
    center_y = _q((min(y_values) + max(y_values)) / 2)
    if bridge:
        width = _q(math.dist((points[0].x_m, points[0].y_m), (points[1].x_m, points[1].y_m)))
        depth = _q(depth_m)
    else:
        width = _q(max(x_values) - min(x_values) + padding)
        depth = _q(max(y_values) - min(y_values) + padding)
    return center_x, center_y, width, depth, yaw


def _polygon(
    center_x: float,
    center_y: float,
    width: float,
    depth: float,
    extent: SceneExtent,
) -> PolygonTopology:
    coords = (
        (center_x - width / 2, center_y - depth / 2),
        (center_x + width / 2, center_y - depth / 2),
        (center_x + width / 2, center_y + depth / 2),
        (center_x - width / 2, center_y + depth / 2),
        (center_x - width / 2, center_y - depth / 2),
    )
    return PolygonTopology(ring=tuple(_point(x, y, extent) for x, y in coords))


def _feature_rows(extent: SceneExtent) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    def add(
        semantic: SemanticClass,
        object_id: str,
        x: float,
        y: float,
        width: float,
        depth: float,
        height: float,
        material: str,
        *,
        yaw: float = 0.0,
        polyline: PolylineTopology | None = None,
        polygon: PolygonTopology | None = None,
        bridge: BridgeTopology | None = None,
        overlaps: tuple[str, ...] = (),
    ) -> None:
        if polyline or polygon or bridge:
            x, y, width, depth, yaw = _topology_frame(
                polyline,
                polygon,
                bridge,
                depth,
            )
        rows.append(
            {
                "object_id": object_id,
                "semantic_class": semantic,
                "x_m": _q(x),
                "y_m": _q(y),
                "z_m": _q(terrain_height_m(x, y, extent) + height / 2),
                "yaw_deg": _q(yaw),
                "dimensions": Dimensions(width_m=_q(width), depth_m=_q(depth), height_m=_q(height)),
                "material_family": material,
                "spatial_cell": _cell_id(x, y, extent),
                "cluster": None,
                "building_role": None,
                "base_z_m": None,
                "platform_relief_m": 0.0,
                "polyline": polyline,
                "polygon": polygon,
                "bridge": bridge,
                "overlap_object_ids": overlaps,
            }
        )

    creek_coords = (
        (-340, -212),
        (-305, -196),
        (-270, -177),
        (-230, -154),
        (-195.479, -129.339),
        (-175, -115),
        (-154.521, -100.661),
        (-118, -78),
        (-78, -57),
        (-35, -36),
        (5, -14),
        (45, 8),
        (78, 30),
        (104.521, 55.661),
        (125, 70),
        (145.479, 84.339),
        (185, 107),
        (225, 137),
        (265, 170),
        (305, 207),
        (335, 235),
    )
    add(
        "creek",
        "creek-main-001",
        -2.5,
        11.5,
        675,
        8,
        0.4,
        "shallow-water",
        yaw=35,
        polyline=_polyline(creek_coords, 8, "creek-main", 0, extent),
    )
    bridge_specs = (
        ("bridge-lower-001", (-175.0, -115.0), (-180.736, -106.808), (-169.264, -123.192)),
        ("bridge-upper-002", (125.0, 70.0), (119.838, 77.372), (130.162, 62.628)),
    )
    for object_id, center, left, right in bridge_specs:
        anchors = (_point(*left, extent), _point(*right, extent))
        add(
            "bridge",
            object_id,
            *center,
            20,
            5,
            2.5,
            "fieldstone",
            yaw=-55,
            bridge=BridgeTopology(
                bank_anchors=anchors,
                crosses_object_id="creek-main-001",
            ),
            overlaps=("creek-main-001",),
        )
    path_specs = (
        (
            (-330, -215),
            (-285, -190),
            (-235, -158),
            (-169.264, -123.192),
            (-180.736, -106.808),
            (-185, -96),
            (-180, -90),
            (-205, -82),
        ),
        ((-205, -82), (-165, -78), (-130, -58), (-90, -35), (-50, -12), (0, 0), (0, 15)),
        ((0, 15), (30, 27), (58, 43), (82, 67), (112, 82), (145, 104), (170, 115), (182, 135)),
        (
            (82, 67),
            (95, 61),
            (110, 70),
            (119.838, 77.372),
            (130.162, 62.628),
            (151, 46),
            (175, 31),
            (195, 15),
        ),
        (
            (-169.264, -123.192),
            (-150, -120),
            (-105, -107),
            (-45, -87),
            (20, -65),
            (85, -43),
            (145, -15),
            (195, 15),
        ),
        ((182, 135), (205, 149), (235, 168), (205, 190), (255, 210), (305, 225)),
    )
    for index, coords in enumerate(path_specs):
        object_id = f"path-network-{index + 1:03d}"
        midpoint = ((coords[0][0] + coords[1][0]) / 2, (coords[0][1] + coords[1][1]) / 2)
        length = math.dist(coords[0], coords[1])
        yaw = (
            round(
                math.degrees(math.atan2(coords[1][1] - coords[0][1], coords[1][0] - coords[0][0]))
                / 5
            )
            * 5
        )
        add(
            "path",
            object_id,
            *midpoint,
            length,
            3.2,
            0.3,
            "packed-earth",
            yaw=yaw,
            polyline=_polyline(coords, 3.2, "village-network", index, extent),
            overlaps=("bridge-lower-001", "bridge-upper-002", "creek-main-001"),
        )
    add(
        "pond",
        "pond-irrigation-001",
        270,
        -175,
        42,
        28,
        1.2,
        "shallow-water",
        polygon=_polygon(270, -175, 42, 28, extent),
    )
    field_centers = list(
        (-300 + column * 200, -210 + row * 200) for row in range(3) for column in range(4)
    )
    field_centers[0] = (-290, -235)
    field_centers[5] = (-100, 25)
    field_centers[6] = (100, -120)
    field_centers[11] = (315, 145)
    for index, (x, y) in enumerate(field_centers, 1):
        add(
            "field",
            f"field-terrace-{index:03d}",
            x,
            y,
            52,
            30,
            0.8,
            "terrace-soil",
            polygon=_polygon(x, y, 52, 30, extent),
        )
    for index, (x, y) in enumerate(((-270, 145), (300, 50)), 1):
        add(
            "orchard",
            f"orchard-slope-{index:03d}",
            x,
            y,
            48,
            36,
            6,
            "orchard-leaf",
            polygon=_polygon(x, y, 48, 36, extent),
        )
    for index, (x, y) in enumerate(((-300, 40), (-245, 205), (245, -45), (305, 95)), 1):
        add(
            "bamboo",
            f"bamboo-grove-{index:03d}",
            x,
            y,
            28,
            22,
            12,
            "bamboo-stem",
            polygon=_polygon(x, y, 28, 22, extent),
        )
    courtyard_specs = (
        ((-205, -82), ("path-network-001", "path-network-002")),
        ((0, 15), ("path-network-002", "path-network-003")),
        ((182, 135), ("path-network-003", "path-network-006")),
        ((82, 67), ("path-network-003", "path-network-004")),
    )
    for index, ((x, y), overlaps) in enumerate(courtyard_specs, 1):
        add(
            "courtyard",
            f"courtyard-public-{index:03d}",
            x,
            y,
            24,
            18,
            0.2,
            "wet-stone-paving",
            polygon=_polygon(x, y, 24, 18, extent),
            overlaps=overlaps,
        )
    for index, (x, y) in enumerate(
        (
            (-300, -165),
            (-250, -145),
            (-200, -165),
            (-150, -145),
            (-300, -35),
            (-250, -35),
            (-200, -35),
            (-150, -35),
        ),
        1,
    ):
        coords = ((x - 18, y), (x + 18, y))
        add(
            "retaining-wall",
            f"retaining-wall-{index:03d}",
            x,
            y,
            36,
            0.6,
            2.2,
            "fieldstone",
            polyline=_polyline(coords, 0.6, f"retaining-{index:03d}", 0, extent),
        )
    for index in range(1, 17):
        x = -320 + ((index - 1) % 8) * 90
        y = -235 + ((index - 1) // 8) * 465
        add("prop", f"prop-rural-{index:03d}", x, y, 2, 1.4, 1.8, "weathered-timber")
    return rows


def _raw_between(rng: np.random.Generator, low: float, high: float) -> float:
    low_mm = math.ceil(low * 1000)
    high_mm = math.floor(high * 1000)
    if low_mm > high_mm:
        raise ValueError("quantized random interval is empty")
    raw = int(rng.bit_generator.random_raw())
    return (low_mm + raw % (high_mm - low_mm + 1)) / 1000


def _coverage_assignments() -> dict[ClusterId, list[str]]:
    return {
        "creekside": ["cell-r1-c1", "cell-r1-c2", "cell-r2-c1", "cell-r3-c1"],
        "central": ["cell-r1-c3", "cell-r2-c2", "cell-r2-c3", "cell-r3-c2", "cell-r3-c3"],
        "upper": ["cell-r1-c4", "cell-r2-c4", "cell-r3-c4"],
    }


def _cell_bounds(cell: str, extent: SceneExtent, margin: float):
    row, column = int(cell[6]) - 1, int(cell[9]) - 1
    cell_width, cell_depth = extent.width_m / 4, extent.depth_m / 3
    return (
        -extent.width_m / 2 + column * cell_width + margin,
        -extent.width_m / 2 + (column + 1) * cell_width - margin,
        -extent.depth_m / 2 + row * cell_depth + margin,
        -extent.depth_m / 2 + (row + 1) * cell_depth - margin,
    )


def _candidate_xy(rng, cluster, coverage_cell, extent, margin):
    if coverage_cell:
        x_min, x_max, y_min, y_max = _cell_bounds(coverage_cell, extent, margin)
    else:
        center_x, center_y = CLUSTER_CENTERS[cluster]
        x_min, x_max, y_min, y_max = center_x - 105, center_x + 105, center_y - 90, center_y + 90
        x_min, x_max = (
            max(x_min, -extent.width_m / 2 + margin),
            min(x_max, extent.width_m / 2 - margin),
        )
        y_min, y_max = (
            max(y_min, -extent.depth_m / 2 + margin),
            min(y_max, extent.depth_m / 2 - margin),
        )
    return _raw_between(rng, x_min, x_max), _raw_between(rng, y_min, y_max)


def _building_specs():
    coverage = _coverage_assignments()
    for cluster in CLUSTER_ORDER:
        for number in range(1, CLUSTER_BUDGETS[cluster] + 1):
            yield (
                cluster,
                number,
                coverage[cluster][number - 1] if number <= len(coverage[cluster]) else None,
            )


def _make_scene_object(row: dict[str, object], instance_id: int) -> SceneObject:
    return SceneObject(
        object_id=row["object_id"],
        semantic_class=row["semantic_class"],
        instance_id=instance_id,
        transform=Transform(x_m=row["x_m"], y_m=row["y_m"], z_m=row["z_m"], yaw_deg=row["yaw_deg"]),
        dimensions=row["dimensions"],
        material_family=row["material_family"],
        spatial_cell=row["spatial_cell"],
        cluster=row["cluster"],
        building_role=row["building_role"],
        base_z_m=row["base_z_m"],
        platform_relief_m=row["platform_relief_m"],
        polyline=row["polyline"],
        polygon=row["polygon"],
        bridge=row["bridge"],
        overlap_object_ids=row["overlap_object_ids"],
    )


def _place_buildings(rng, extent, feature_objects, attempt_limit):
    placed: list[SceneObject] = []
    attempts = 0
    materials = ("rammed-earth", "pale-plaster", "fieldstone", "dark-timber")
    yaw_grid = (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 165.0, 170.0, 175.0)
    for cluster, number, coverage_cell in _building_specs():
        dimensions = Dimensions(
            width_m=_raw_between(rng, 8, 11),
            depth_m=_raw_between(rng, 7, 9),
            height_m=_raw_between(rng, 5.5, 9.5),
        )
        yaw = yaw_grid[int(rng.bit_generator.random_raw()) % len(yaw_grid)]
        margin = math.hypot(dimensions.width_m, dimensions.depth_m) / 2
        while True:
            attempts += 1
            if attempts > attempt_limit:
                raise PlacementError(
                    f"building placement exceeded hard attempt limit {attempt_limit}"
                )
            x, y = _candidate_xy(rng, cluster, coverage_cell, extent, margin)
            corners = _footprint_corners_values(x, y, yaw, dimensions)
            heights = [terrain_height_m(cx, cy, extent) for cx, cy in corners]
            base, relief = _q(max(heights)), _q(max(heights) - min(heights))
            row = {
                "object_id": f"building-{cluster}-{number:03d}",
                "semantic_class": "building",
                "x_m": x,
                "y_m": y,
                "z_m": _q(base + dimensions.height_m / 2),
                "yaw_deg": yaw,
                "dimensions": dimensions,
                "material_family": materials[(number - 1) % 4],
                "spatial_cell": _cell_id(x, y, extent),
                "cluster": cluster,
                "building_role": "community-hall"
                if cluster == "central" and number == 1
                else "residence",
                "base_z_m": base,
                "platform_relief_m": relief,
                "polyline": None,
                "polygon": None,
                "bridge": None,
                "overlap_object_ids": (),
            }
            candidate = _make_scene_object(row, 1)
            if relief > MAX_PLATFORM_RELIEF_M:
                continue
            if any(math.hypot(x - b.transform.x_m, y - b.transform.y_m) < 8 for b in placed):
                continue
            if any(building_footprint_clearance_m(candidate, b) + 1e-9 < 0.75 for b in placed):
                continue
            if any(_reserved_conflict_polygon(corners, feature) for feature in feature_objects):
                continue
            placed.append(candidate)
            break
    return placed, attempts


def _expected_buildings_and_attempts(
    extent: SceneExtent,
) -> tuple[list[SceneObject], int]:
    feature_rows = _feature_rows(extent)
    feature_objects = tuple(_make_scene_object(row, 1) for row in feature_rows)
    rng = np.random.Generator(np.random.PCG64(DEFAULT_SEED))
    return _place_buildings(
        rng,
        extent,
        feature_objects,
        MAX_PLACEMENT_ATTEMPTS,
    )


def _camera_anchors(
    extent: SceneExtent, objects: tuple[SceneObject, ...]
) -> tuple[CameraAnchor, ...]:
    anchors = []
    for cluster, (x, y) in CLUSTER_CENTERS.items():
        anchors.append(
            CameraAnchor(
                anchor_id=f"anchor-cluster-{cluster}",
                anchor_type="cluster",
                position=PlanPoint(x_m=x, y_m=y, z_m=_q(terrain_height_m(x, y, extent) + 8)),
                target=_point(x, y, extent),
                source_id=cluster,
            )
        )
    for index, (x, y, source) in enumerate(
        ((-180, -90, "village-network"), (0, 0, "village-network"), (170, 115, "village-network")),
        1,
    ):
        anchors.append(
            CameraAnchor(
                anchor_id=f"anchor-route-{index:03d}",
                anchor_type="route",
                position=PlanPoint(
                    x_m=float(x), y_m=float(y), z_m=_q(terrain_height_m(x, y, extent) + 3)
                ),
                target=_point(x + 10, y, extent),
                source_id=source,
            )
        )
    anchors.append(
        CameraAnchor(
            anchor_id="anchor-intersection-central",
            anchor_type="intersection",
            position=PlanPoint(x_m=0.0, y_m=-12.0, z_m=_q(terrain_height_m(0, -12, extent) + 3)),
            target=_point(0, 0, extent),
            source_id="intersection-central",
        )
    )
    for item in (o for o in objects if o.semantic_class == "courtyard"):
        anchors.append(
            CameraAnchor(
                anchor_id=f"anchor-{item.object_id}",
                anchor_type="courtyard",
                position=PlanPoint(
                    x_m=item.transform.x_m,
                    y_m=item.transform.y_m,
                    z_m=_q(terrain_height_m(item.transform.x_m, item.transform.y_m, extent) + 3),
                ),
                target=_point(item.transform.x_m, item.transform.y_m, extent),
                source_id=item.object_id,
            )
        )
    for item in (o for o in objects if o.semantic_class == "bridge"):
        anchors.append(
            CameraAnchor(
                anchor_id=f"anchor-{item.object_id}",
                anchor_type="bridge",
                position=PlanPoint(
                    x_m=item.transform.x_m,
                    y_m=_q(item.transform.y_m - 10),
                    z_m=_q(
                        terrain_height_m(item.transform.x_m, item.transform.y_m - 10, extent) + 3
                    ),
                ),
                target=_point(item.transform.x_m, item.transform.y_m, extent),
                source_id=item.object_id,
            )
        )
    return tuple(sorted(anchors, key=lambda anchor: anchor.anchor_id))


def build_scene_plan(
    recipe: DefaultResourceRecipe | None = None, *, attempt_limit: int = 10_000
) -> ScenePlan:
    active = recipe or build_default_recipe()
    if active != build_default_recipe():
        raise ValueError("scene planner accepts only the exact tracked v1 recipe")
    if (
        not isinstance(attempt_limit, int)
        or isinstance(attempt_limit, bool)
        or not 1 <= attempt_limit <= 10_000
    ):
        raise ValueError("attempt limit must be an integer between 1 and 10000")
    feature_rows = _feature_rows(active.scene)
    feature_objects = tuple(_make_scene_object(row, 1) for row in feature_rows)
    rng = np.random.Generator(np.random.PCG64(active.seed))
    buildings, attempts = _place_buildings(rng, active.scene, feature_objects, attempt_limit)
    rows = [
        {
            **item.model_dump(),
            "x_m": item.transform.x_m,
            "y_m": item.transform.y_m,
            "z_m": item.transform.z_m,
            "yaw_deg": item.transform.yaw_deg,
        }
        for item in buildings
    ] + feature_rows
    rows.sort(
        key=lambda row: (
            SEMANTIC_ORDER.index(row["semantic_class"]),
            CLUSTER_ORDER.index(row["cluster"]) if row["cluster"] in CLUSTER_ORDER else 0,
            row["object_id"],
        )
    )
    objects = tuple(_make_scene_object(row, index) for index, row in enumerate(rows, 1))
    return ScenePlan(
        plan_id="synthetic-mountain-village-scene-v1",
        recipe_id=active.recipe_id,
        coordinate_system=active.coordinate_system,
        seed=active.seed,
        extent=active.scene,
        terrain_min_m=terrain_height_m(0, -active.scene.depth_m / 2, active.scene),
        terrain_max_m=terrain_height_m(0, active.scene.depth_m / 2, active.scene),
        placement_attempts=attempts,
        objects=objects,
        camera_anchors=_camera_anchors(active.scene, objects),
    )


def canonical_scene_plan_bytes(plan: ScenePlan) -> bytes:
    text = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScenePlanError(f"scene plan contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int]:
    return result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns


def load_scene_plan(path: Path) -> ScenePlan:
    path = Path(path).absolute()
    try:
        parent = path.parent
        if _is_linklike(path) or _is_linklike(parent) or parent.resolve(strict=True) != parent:
            raise ScenePlanError("scene plan path has a redirected leaf or parent")
        before = path.stat()
        if before.st_size <= 0 or before.st_size > MAX_SCENE_PLAN_BYTES:
            raise ScenePlanError("scene plan size is invalid")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_signature(before) != _stat_signature(opened):
                raise ScenePlanError("scene plan changed before bounded read")
            raw = stream.read(MAX_SCENE_PLAN_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_SCENE_PLAN_BYTES
            or _stat_signature(opened) != _stat_signature(after_open)
            or _stat_signature(before) != _stat_signature(after)
        ):
            raise ScenePlanError("scene plan changed during bounded read")
        json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        plan = ScenePlan.model_validate_json(raw)
        if raw != canonical_scene_plan_bytes(plan):
            raise ScenePlanError("scene plan must be canonical JSON")
        return plan
    except ScenePlanError:
        raise
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ScenePlanError(f"scene plan cannot be trusted: {exc}") from exc
