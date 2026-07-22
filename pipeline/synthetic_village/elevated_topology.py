"""Scene-bound walkable topology for the synthetic village's elevated routes.

The plan is evidence for deterministic synthetic geometry, not evidence of a
measured or reconstructed site.  It stays separate from the immutable
``ScenePlan`` v1 and binds the exact canonical scene bytes it was checked
against.  Reference images are design inputs only and never enter this plan.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .scene_plan import (
    ScenePlan,
    _footprint_corners,
    _point_segment_distance,
    _polygon_polyline_clearance_m,
    _ring_xy,
    _segments_intersect,
    build_scene_plan,
    canonical_scene_plan_bytes,
    terrain_height_m,
)

ELEVATED_TOPOLOGY_SCHEMA = "nantai.synthetic-village.elevated-topology.v1"
MIN_ELEVATED_CLEARANCE_M = 3.0
MIN_BUILDING_ENVELOPE_M = 0.5
MIN_DRAINAGE_CLEARANCE_M = 1.5

ComponentKind = Literal[
    "switchback-stair",
    "covered-timber-gallery",
    "terrace-ramp-junction",
    "cross-level-covered-passage",
]
LoopId = Literal["central-loop", "upper-loop"]

_EXPECTED_COMPONENTS = (
    ("elevated-switchback-stair-v1", "switchback-stair", 127),
    ("covered-timber-gallery-v1", "covered-timber-gallery", 128),
    ("terrace-ramp-junction-v1", "terrace-ramp-junction", 129),
    ("cross-level-covered-passage-v1", "cross-level-covered-passage", 130),
)


class ElevatedTopologyError(ValueError):
    """Raised when topology does not verify against its tracked scene."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WalkablePoint(FrozenModel):
    position_m: tuple[
        float,
        float,
        float,
    ]

    @model_validator(mode="after")
    def _finite_mm_position(self) -> WalkablePoint:
        if not all(math.isfinite(value) for value in self.position_m):
            raise ValueError("walkable point coordinates must be finite")
        if not all(_is_mm(value) for value in self.position_m):
            raise ValueError("walkable point coordinates must use the millimetre grid")
        return self


class WalkableNode(FrozenModel):
    node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    position_m: tuple[float, float, float]
    level: Literal["ground", "elevated"]
    ground_route_ref: str | None = Field(
        default=None,
        pattern=r"^path-[a-z0-9]+(?:-[a-z0-9]+)*$",
    )

    @model_validator(mode="after")
    def _level_contract(self) -> WalkableNode:
        if not all(math.isfinite(value) and _is_mm(value) for value in self.position_m):
            raise ValueError("walkable node coordinates must be finite millimetre values")
        if self.level == "ground" and self.ground_route_ref is None:
            raise ValueError("ground node requires a ground route reference")
        if self.level == "elevated" and self.ground_route_ref is not None:
            raise ValueError("elevated node cannot claim a ground route reference")
        return self


class CollisionEnvelope(FrozenModel):
    deck_thickness_m: float = Field(gt=0, allow_inf_nan=False)
    railing_height_m: float = Field(ge=1.0, allow_inf_nan=False)
    covered: bool
    head_clearance_m: float | None = Field(default=None, allow_inf_nan=False)
    drainage_clearance_m: float = Field(
        ge=MIN_DRAINAGE_CLEARANCE_M,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def _covered_headroom(self) -> CollisionEnvelope:
        if self.covered and (
            self.head_clearance_m is None or self.head_clearance_m < 2.1
        ):
            raise ValueError("covered walkable edge requires at least 2.1 m headroom")
        if not self.covered and self.head_clearance_m is not None:
            raise ValueError("uncovered walkable edge cannot declare covered headroom")
        return self


class WalkableEdge(FrozenModel):
    edge_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    component_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    component_kind: ComponentKind
    loop_id: LoopId
    start_node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    end_node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    width_m: float = Field(ge=1.8, allow_inf_nan=False)
    centerline: tuple[WalkablePoint, ...] = Field(min_length=2)
    collision: CollisionEnvelope

    @model_validator(mode="after")
    def _non_degenerate_edge(self) -> WalkableEdge:
        if self.start_node_id == self.end_node_id:
            raise ValueError("walkable edge endpoints must be distinct")
        if len({point.position_m for point in self.centerline}) < 2:
            raise ValueError("walkable edge centerline must be non-degenerate")
        return self


class ElevatedComponent(FrozenModel):
    component_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    component_kind: ComponentKind
    instance_id: int = Field(ge=127, le=130)
    edge_ids: tuple[str, ...] = Field(min_length=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _stable_component_members(self) -> ElevatedComponent:
        if tuple(sorted(self.edge_ids)) != self.edge_ids or len(set(self.edge_ids)) != len(
            self.edge_ids
        ):
            raise ValueError("component edge IDs must be unique and stable")
        if len(set(self.material_slot_ids)) != len(self.material_slot_ids):
            raise ValueError("component material slot IDs must be unique")
        return self


class ElevatedLoop(FrozenModel):
    loop_id: LoopId
    connected: Literal[True]
    ground_attachment_count: Literal[2]
    edge_count: int = Field(ge=3)
    edge_ids: tuple[str, ...] = Field(min_length=3)


class ElevatedTopologySummary(FrozenModel):
    loop_count: Literal[2]
    ground_attachment_count: Literal[4]
    component_count: Literal[4]


class ElevatedTopologyPlan(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.elevated-topology.v1"
    ] = ELEVATED_TOPOLOGY_SCHEMA
    scene_plan_id: Literal["synthetic-mountain-village-scene-v1"]
    scene_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_system: Literal["right-handed-z-up-meters"]
    synthetic: Literal[True]
    verification_level: Literal["L2"]
    geometry_trust: Literal["simplified-pbr-not-render-parity"]
    semantic_id: Literal[14]
    nodes: tuple[WalkableNode, ...] = Field(min_length=8)
    edges: tuple[WalkableEdge, ...] = Field(min_length=6)
    components: tuple[ElevatedComponent, ...] = Field(min_length=4, max_length=4)
    loops: tuple[ElevatedLoop, ...] = Field(min_length=2, max_length=2)
    summary: ElevatedTopologySummary

    @model_validator(mode="after")
    def _validate_graph_contract(self) -> ElevatedTopologyPlan:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("walkable node IDs must be unique")
        if node_ids != sorted(node_ids):
            raise ValueError("walkable node IDs must use stable order")

        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("walkable edge IDs must be unique")
        if edge_ids != sorted(edge_ids):
            raise ValueError("walkable edge IDs must use stable order")

        actual_components = tuple(
            (row.component_id, row.component_kind, row.instance_id)
            for row in self.components
        )
        if actual_components != _EXPECTED_COMPONENTS:
            raise ValueError("elevated components do not match the stable v1 contract")

        nodes = {node.node_id: node for node in self.nodes}
        edge_map = {edge.edge_id: edge for edge in self.edges}
        owned_edge_ids = [
            edge_id for component in self.components for edge_id in component.edge_ids
        ]
        if sorted(owned_edge_ids) != edge_ids or len(set(owned_edge_ids)) != len(
            owned_edge_ids
        ):
            raise ValueError("component edge ownership must be exact and unique")
        for component in self.components:
            for edge_id in component.edge_ids:
                edge = edge_map[edge_id]
                if (
                    edge.component_id != component.component_id
                    or edge.component_kind != component.component_kind
                ):
                    raise ValueError("component edge ownership metadata is inconsistent")
        for edge in self.edges:
            if edge.start_node_id not in nodes or edge.end_node_id not in nodes:
                raise ValueError("walkable edge references an unknown node")
            if edge.centerline[0].position_m != nodes[edge.start_node_id].position_m:
                raise ValueError("walkable edge start does not match its node")
            if edge.centerline[-1].position_m != nodes[edge.end_node_id].position_m:
                raise ValueError("walkable edge end does not match its node")

        expected_loops = _derive_loops(self.nodes, self.edges)
        if self.loops != expected_loops:
            raise ValueError("elevated loop summaries do not derive from graph evidence")
        # Only ground nodes that participate in loop edges count as loop
        # ground attachments.  Isolated anchor nodes (module-adjacent
        # ground reference points for reciprocal-route role cameras) are
        # valid canonical topology reference points on declared paths but
        # do not participate in any loop and must not inflate the loop
        # attachment count.
        used_node_ids = {
            edge.start_node_id for edge in self.edges
        } | {edge.end_node_id for edge in self.edges}
        expected_summary = ElevatedTopologySummary(
            loop_count=2,
            ground_attachment_count=sum(
                node.level == "ground"
                for node in self.nodes
                if node.node_id in used_node_ids
            ),
            component_count=len(self.components),
        )
        if self.summary != expected_summary:
            raise ValueError("elevated topology summary does not derive from graph evidence")
        return self


def _is_mm(value: float) -> bool:
    return abs(value * 1000 - round(value * 1000)) <= 1e-7


def _q(value: float) -> float:
    return round(float(value) + 0.0, 3)


def _point(position_m: tuple[float, float, float]) -> WalkablePoint:
    return WalkablePoint(position_m=tuple(_q(value) for value in position_m))


def _node(
    *,
    node_id: str,
    x_m: float,
    y_m: float,
    scene: ScenePlan,
    level: Literal["ground", "elevated"],
    ground_route_ref: str | None = None,
    clearance_m: float = 0.0,
) -> WalkableNode:
    return WalkableNode(
        node_id=node_id,
        position_m=(
            _q(x_m),
            _q(y_m),
            _q(terrain_height_m(x_m, y_m, scene.extent) + clearance_m),
        ),
        level=level,
        ground_route_ref=ground_route_ref,
    )


def _transition_centerline(
    start: WalkableNode,
    intermediate_xy: tuple[tuple[float, float], ...],
    end: WalkableNode,
) -> tuple[WalkablePoint, ...]:
    xy = (
        (start.position_m[0], start.position_m[1]),
        *intermediate_xy,
        (end.position_m[0], end.position_m[1]),
    )
    denominator = len(xy) - 1
    return tuple(
        _point(
            (
                x_m,
                y_m,
                start.position_m[2]
                + (end.position_m[2] - start.position_m[2]) * index / denominator,
            )
        )
        for index, (x_m, y_m) in enumerate(xy)
    )


def _elevated_centerline(
    start: WalkableNode,
    intermediate_xy: tuple[tuple[float, float], ...],
    end: WalkableNode,
    scene: ScenePlan,
    *,
    clearance_m: float,
) -> tuple[WalkablePoint, ...]:
    points = [_point(start.position_m)]
    points.extend(
        _point(
            (
                x_m,
                y_m,
                terrain_height_m(x_m, y_m, scene.extent) + clearance_m,
            )
        )
        for x_m, y_m in intermediate_xy
    )
    points.append(_point(end.position_m))
    return tuple(points)


def _collision(*, covered: bool) -> CollisionEnvelope:
    return CollisionEnvelope(
        deck_thickness_m=0.24,
        railing_height_m=1.1,
        covered=covered,
        head_clearance_m=2.4 if covered else None,
        drainage_clearance_m=MIN_DRAINAGE_CLEARANCE_M,
    )


def _derive_loops(
    nodes: tuple[WalkableNode, ...],
    edges: tuple[WalkableEdge, ...],
) -> tuple[ElevatedLoop, ...]:
    nodes_by_id = {node.node_id: node for node in nodes}
    result = []
    for loop_id in ("central-loop", "upper-loop"):
        loop_edges = tuple(edge for edge in edges if edge.loop_id == loop_id)
        edge_ids = tuple(edge.edge_id for edge in loop_edges)
        graph: dict[str, set[str]] = defaultdict(set)
        for edge in loop_edges:
            graph[edge.start_node_id].add(edge.end_node_id)
            graph[edge.end_node_id].add(edge.start_node_id)
        used_nodes = set(graph)
        if not used_nodes:
            raise ValueError(f"{loop_id} has no graph evidence")
        visited = {next(iter(used_nodes))}
        queue = deque(visited)
        while queue:
            current = queue.popleft()
            for neighbor in graph[current] - visited:
                visited.add(neighbor)
                queue.append(neighbor)
        ground_count = sum(nodes_by_id[node_id].level == "ground" for node_id in used_nodes)
        if visited != used_nodes:
            raise ValueError(f"{loop_id} must be connected")
        if ground_count != 2:
            raise ValueError(f"{loop_id} must have exactly two ground attachments")
        if len(loop_edges) < 3:
            raise ValueError(f"{loop_id} must contain at least three edges")
        result.append(
            ElevatedLoop(
                loop_id=loop_id,
                connected=True,
                ground_attachment_count=2,
                edge_count=len(loop_edges),
                edge_ids=edge_ids,
            )
        )
    return tuple(result)


def build_elevated_topology_plan(
    scene: ScenePlan | None = None,
) -> ElevatedTopologyPlan:
    """Build the deterministic v1 topology bound to an exact ScenePlan."""

    active = scene or build_scene_plan()
    digest = hashlib.sha256(canonical_scene_plan_bytes(active)).hexdigest()
    nodes = tuple(
        sorted(
            (
                _node(
                    node_id="central-ground-west",
                    x_m=-50,
                    y_m=-12,
                    scene=active,
                    level="ground",
                    ground_route_ref="path-network-002",
                ),
                _node(
                    node_id="central-upper-west",
                    x_m=-35,
                    y_m=-17,
                    scene=active,
                    level="elevated",
                    clearance_m=3.6,
                ),
                _node(
                    node_id="central-upper-east",
                    x_m=20,
                    y_m=10,
                    scene=active,
                    level="elevated",
                    clearance_m=3.6,
                ),
                _node(
                    node_id="central-ground-east",
                    x_m=30,
                    y_m=27,
                    scene=active,
                    level="ground",
                    ground_route_ref="path-network-003",
                ),
                _node(
                    node_id="upper-ground-west",
                    x_m=145,
                    y_m=104,
                    scene=active,
                    level="ground",
                    ground_route_ref="path-network-003",
                ),
                _node(
                    node_id="upper-upper-west",
                    x_m=151,
                    y_m=116,
                    scene=active,
                    level="elevated",
                    clearance_m=3.8,
                ),
                _node(
                    node_id="upper-upper-east",
                    x_m=188,
                    y_m=128,
                    scene=active,
                    level="elevated",
                    clearance_m=3.8,
                ),
                _node(
                    node_id="upper-ground-east",
                    x_m=182,
                    y_m=135,
                    scene=active,
                    level="ground",
                    ground_route_ref="path-network-003",
                ),
                # Phase 4.5: isolated module-anchor ground nodes for
                # reciprocal-route role camera binding.  These are
                # canonical topology reference points on declared paths
                # but do not participate in any loop/edge.  Each sits on
                # an existing path-network polyline vertex so verify
                # accepts it without scene-plan changes.
                _node(
                    node_id="bridge-ground-001",
                    x_m=-165,
                    y_m=-78,
                    scene=active,
                    level="ground",
                    ground_route_ref="path-network-002",
                ),
                _node(
                    node_id="gallery-ground-001",
                    x_m=58,
                    y_m=43,
                    scene=active,
                    level="ground",
                    ground_route_ref="path-network-003",
                ),
                _node(
                    node_id="watermill-ground-001",
                    x_m=-180.736,
                    y_m=-106.808,
                    scene=active,
                    level="ground",
                    ground_route_ref="path-network-001",
                ),
            ),
            key=lambda node: node.node_id,
        )
    )
    by_id = {node.node_id: node for node in nodes}
    edges = tuple(
        sorted(
            (
                WalkableEdge(
                    edge_id="edge-central-stair-001",
                    component_id="elevated-switchback-stair-v1",
                    component_kind="switchback-stair",
                    loop_id="central-loop",
                    start_node_id="central-ground-west",
                    end_node_id="central-upper-west",
                    width_m=2.4,
                    centerline=_transition_centerline(
                        by_id["central-ground-west"],
                        ((-46, -18), (-41, -12)),
                        by_id["central-upper-west"],
                    ),
                    collision=_collision(covered=False),
                ),
                WalkableEdge(
                    edge_id="edge-central-gallery-001",
                    component_id="covered-timber-gallery-v1",
                    component_kind="covered-timber-gallery",
                    loop_id="central-loop",
                    start_node_id="central-upper-west",
                    end_node_id="central-upper-east",
                    width_m=2.6,
                    centerline=_elevated_centerline(
                        by_id["central-upper-west"],
                        ((-10, -10),),
                        by_id["central-upper-east"],
                        active,
                        clearance_m=3.6,
                    ),
                    collision=_collision(covered=True),
                ),
                WalkableEdge(
                    edge_id="edge-central-ramp-001",
                    component_id="terrace-ramp-junction-v1",
                    component_kind="terrace-ramp-junction",
                    loop_id="central-loop",
                    start_node_id="central-upper-east",
                    end_node_id="central-ground-east",
                    width_m=3.0,
                    centerline=_transition_centerline(
                        by_id["central-upper-east"],
                        ((25, 14), (29, 21)),
                        by_id["central-ground-east"],
                    ),
                    collision=_collision(covered=False),
                ),
                WalkableEdge(
                    edge_id="edge-upper-ascent-001",
                    component_id="cross-level-covered-passage-v1",
                    component_kind="cross-level-covered-passage",
                    loop_id="upper-loop",
                    start_node_id="upper-ground-west",
                    end_node_id="upper-upper-west",
                    width_m=2.8,
                    centerline=_transition_centerline(
                        by_id["upper-ground-west"],
                        ((148, 110),),
                        by_id["upper-upper-west"],
                    ),
                    collision=_collision(covered=False),
                ),
                WalkableEdge(
                    edge_id="edge-upper-gallery-001",
                    component_id="cross-level-covered-passage-v1",
                    component_kind="cross-level-covered-passage",
                    loop_id="upper-loop",
                    start_node_id="upper-upper-west",
                    end_node_id="upper-upper-east",
                    width_m=2.8,
                    centerline=_elevated_centerline(
                        by_id["upper-upper-west"],
                        ((170, 125),),
                        by_id["upper-upper-east"],
                        active,
                        clearance_m=3.8,
                    ),
                    collision=_collision(covered=True),
                ),
                WalkableEdge(
                    edge_id="edge-upper-descent-001",
                    component_id="cross-level-covered-passage-v1",
                    component_kind="cross-level-covered-passage",
                    loop_id="upper-loop",
                    start_node_id="upper-upper-east",
                    end_node_id="upper-ground-east",
                    width_m=2.8,
                    centerline=_transition_centerline(
                        by_id["upper-upper-east"],
                        ((190, 132),),
                        by_id["upper-ground-east"],
                    ),
                    collision=_collision(covered=False),
                ),
            ),
            key=lambda edge: edge.edge_id,
        )
    )
    components = (
        ElevatedComponent(
            component_id="elevated-switchback-stair-v1",
            component_kind="switchback-stair",
            instance_id=127,
            edge_ids=("edge-central-stair-001",),
            material_slot_ids=("material-fieldstone-01", "material-moss-stone-01"),
        ),
        ElevatedComponent(
            component_id="covered-timber-gallery-v1",
            component_kind="covered-timber-gallery",
            instance_id=128,
            edge_ids=("edge-central-gallery-001",),
            material_slot_ids=(
                "material-weathered-timber-01",
                "material-fieldstone-01",
                "material-gray-roof-tile-01",
            ),
        ),
        ElevatedComponent(
            component_id="terrace-ramp-junction-v1",
            component_kind="terrace-ramp-junction",
            instance_id=129,
            edge_ids=("edge-central-ramp-001",),
            material_slot_ids=("material-fieldstone-01", "material-packed-earth-01"),
        ),
        ElevatedComponent(
            component_id="cross-level-covered-passage-v1",
            component_kind="cross-level-covered-passage",
            instance_id=130,
            edge_ids=(
                "edge-upper-ascent-001",
                "edge-upper-descent-001",
                "edge-upper-gallery-001",
            ),
            material_slot_ids=(
                "material-weathered-timber-01",
                "material-fieldstone-01",
                "material-gray-roof-tile-01",
            ),
        ),
    )
    loops = _derive_loops(nodes, edges)
    plan = ElevatedTopologyPlan(
        scene_plan_id=active.plan_id,
        scene_plan_sha256=digest,
        coordinate_system=active.coordinate_system,
        synthetic=True,
        verification_level="L2",
        geometry_trust="simplified-pbr-not-render-parity",
        semantic_id=14,
        nodes=nodes,
        edges=edges,
        components=components,
        loops=loops,
        summary=ElevatedTopologySummary(
            loop_count=2,
            ground_attachment_count=4,
            component_count=4,
        ),
    )
    verify_elevated_topology_plan(plan, active)
    return plan


def _polyline_distance(
    left: tuple[tuple[float, float], ...],
    right: tuple[tuple[float, float], ...],
) -> float:
    if any(
        _segments_intersect(left_start, left_end, right_start, right_end)
        for left_start, left_end in zip(left, left[1:], strict=False)
        for right_start, right_end in zip(right, right[1:], strict=False)
    ):
        return 0.0
    distances = []
    for point in left:
        for start, end in zip(right, right[1:], strict=False):
            distances.append(_point_segment_distance(point, start, end))
    for point in right:
        for start, end in zip(left, left[1:], strict=False):
            distances.append(_point_segment_distance(point, start, end))
    return min(distances)


def verify_elevated_topology_plan(
    plan: ElevatedTopologyPlan,
    scene: ScenePlan | None = None,
) -> None:
    """Fail closed unless the graph and its horizontal envelopes fit the scene."""

    active = scene or build_scene_plan()
    expected_digest = hashlib.sha256(canonical_scene_plan_bytes(active)).hexdigest()
    if (
        plan.scene_plan_id != active.plan_id
        or plan.scene_plan_sha256 != expected_digest
    ):
        raise ElevatedTopologyError("scene plan digest does not match topology evidence")
    if plan.coordinate_system != active.coordinate_system:
        raise ElevatedTopologyError("scene coordinate system does not match topology")

    paths = {
        item.object_id: item
        for item in active.objects
        if item.semantic_class == "path"
    }
    for node in plan.nodes:
        x_m, y_m, z_m = node.position_m
        if (
            abs(x_m) > active.extent.width_m / 2
            or abs(y_m) > active.extent.depth_m / 2
        ):
            raise ElevatedTopologyError(f"node {node.node_id} exceeds scene extent")
        terrain = terrain_height_m(x_m, y_m, active.extent)
        if node.level == "ground":
            route = paths.get(node.ground_route_ref)
            if route is None:
                raise ElevatedTopologyError(
                    f"ground node {node.node_id} references an unknown path"
                )
            route_xy = tuple(
                (point.x_m, point.y_m) for point in route.polyline.points
            )
            distance = min(
                _point_segment_distance((x_m, y_m), start, end)
                for start, end in zip(route_xy, route_xy[1:], strict=False)
            )
            if distance > route.polyline.width_m / 2 + 1e-6:
                raise ElevatedTopologyError(
                    f"ground node {node.node_id} is outside its path surface"
                )
            if z_m != terrain:
                raise ElevatedTopologyError(
                    f"ground node {node.node_id} does not match terrain"
                )
        elif z_m - terrain < MIN_ELEVATED_CLEARANCE_M - 1e-9:
            raise ElevatedTopologyError(
                f"elevated node {node.node_id} lacks absolute terrain clearance"
            )

    buildings = [
        item for item in active.objects if item.semantic_class == "building"
    ]
    creek = next(
        item for item in active.objects if item.semantic_class == "creek"
    )
    creek_xy = tuple(
        (point.x_m, point.y_m) for point in creek.polyline.points
    )
    pond = next(item for item in active.objects if item.semantic_class == "pond")
    pond_xy = _ring_xy(pond.polygon)
    by_node = {node.node_id: node for node in plan.nodes}
    for edge in plan.edges:
        if edge.centerline[0].position_m != by_node[edge.start_node_id].position_m:
            raise ElevatedTopologyError(f"edge {edge.edge_id} start node is inconsistent")
        if edge.centerline[-1].position_m != by_node[edge.end_node_id].position_m:
            raise ElevatedTopologyError(f"edge {edge.edge_id} end node is inconsistent")
        points = tuple(
            (point.position_m[0], point.position_m[1])
            for point in edge.centerline
        )
        if any(
            _polygon_polyline_clearance_m(_footprint_corners(building), points)
            < edge.width_m / 2 + MIN_BUILDING_ENVELOPE_M
            for building in buildings
        ):
            raise ElevatedTopologyError(
                f"edge {edge.edge_id} collision envelope intersects a building"
            )
        drainage_threshold = (
            edge.width_m / 2 + edge.collision.drainage_clearance_m
        )
        creek_clearance = _polyline_distance(points, creek_xy)
        if creek_clearance < (
            creek.polyline.width_m / 2 + drainage_threshold
        ):
            raise ElevatedTopologyError(
                f"edge {edge.edge_id} violates creek water drainage clearance"
            )
        if _polygon_polyline_clearance_m(pond_xy, points) < drainage_threshold:
            raise ElevatedTopologyError(
                f"edge {edge.edge_id} violates pond water drainage clearance"
            )


def canonical_elevated_topology_bytes(plan: ElevatedTopologyPlan) -> bytes:
    """Return deterministic, path-free canonical bytes for content addressing."""

    text = json.dumps(
        plan.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")
