"""Pure-Python v1 model + canonical LF JSON serializer for the
``nantai.synthetic-village.roaming-graph.v1`` artifact.

This module is the GLM-side producer. It builds a frozen, fail-closed graph
document from declared rooms, portals and route loops, and serializes it to
canonical LF JSON that the browser-side validator
(``web/viewer/roaming-graph.mjs``) accepts.

Trust boundary (must never be crossed by this module):

- status is always ``candidate``;
- synthetic is always ``true``;
- geometry / connectivity / coverage / arbitrary-coordinate-reachability /
  trust_effect are Literal-locked to the modeled-unverified family;
- every room and portal is bound to a 64-hex SHA-256 measured from a real
  collision-proxy payload (placeholders and fabricated hashes fail closed);
- IDs are stable lowercase-hyphenated, never derived from image pixels or
  engine names;
- directed edges are auto-generated as exactly two reciprocal edges per
  portal (A->B and B->A); callers cannot inject extra or one-way edges;
- route loops are validated as closed chains of at least three unique
  edges whose endpoints link head-to-tail.

A structurally valid graph with multiple connected components is allowed;
the browser labels it ``fragmented``. This producer does NOT promote a
fragmented graph to ``graph-connected`` and does NOT reject it either.

Out of scope (owned by Codex):
- browser-side validation / view model / HUD;
- Studio optional loader and ``setCameraPose`` jump;
- ``web/data/roaming-graph.json`` (must never be written by this module).

Out of scope (future Blender emitter):
- measuring portal endpoints and clearance from real geometry;
- binding collision-proxy SHAs from a real build report.
This module only validates and serializes what a future emitter feeds it.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Constants — must match web/viewer/roaming-graph.mjs
# ---------------------------------------------------------------------------

ROAMING_GRAPH_SCHEMA = "nantai.synthetic-village.roaming-graph.v1"
SCHEMA_VERSION = 1

STABLE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

MAX_ABS_COORDINATE_M = 10_000_000
MAX_CLEARANCE_M = 100
MAX_ROOMS = 4096
MAX_PORTALS = 8192
MAX_EDGES = MAX_PORTALS * 2
MAX_LOOPS = 4096

ROOM_KINDS = ("exterior", "interior", "transition")

_RE_STABLE_ID = re.compile(STABLE_ID_PATTERN)
_RE_SHA256 = re.compile(SHA256_PATTERN)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RoamingGraphError(ValueError):
    """Raised when a roaming graph cannot be built or fails fail-closed
    validation. The message describes the offending field; no partial
    artifact is published."""


# ---------------------------------------------------------------------------
# Frozen base
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    """Common base for roaming-graph records: ``extra='forbid'``,
    ``frozen=True``, ``strict=True`` so a caller cannot sneak in unknown
    fields or mutate a built graph.

    ``model_copy`` is overridden to re-validate through ``model_validate``
    so trust-locked Literal fields cannot be silently mutated (pydantic's
    default ``model_copy(update=...)`` skips validators)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    def model_copy(self, *, update: dict | None = None, deep: bool = False):  # type: ignore[override]
        if not update:
            return super().model_copy(deep=deep)
        data = self.model_dump()
        data.update(update)
        return type(self).model_validate(data)


# ---------------------------------------------------------------------------
# Header pieces
# ---------------------------------------------------------------------------


class CoordinateFrame(_Frozen):
    name: Literal["synthetic-village-world-enu"]
    units: Literal["meters"]
    handedness: Literal["right"]
    axes: dict[Literal["x", "y", "z"], Literal["east", "north", "up"]]

    @model_validator(mode="after")
    def _axes_match(self) -> CoordinateFrame:
        if self.axes != {"x": "east", "y": "north", "z": "up"}:
            raise ValueError(
                "coordinate_frame.axes must be {x:east, y:north, z:up}")
        return self


class TrustDeclarations(_Frozen):
    geometry: Literal["modeled-unverified"]
    connectivity: Literal["machine-checked-graph-only"]
    coverage: Literal["not-verified"]
    arbitrary_coordinate_reachability: Literal["not-claimed"]
    trust_effect: Literal["none"]


class GraphBindings(_Frozen):
    scene_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    build_report_sha256: str = Field(pattern=SHA256_PATTERN)
    source_plan_sha256: str = Field(pattern=SHA256_PATTERN)
    collision_manifest_sha256: str = Field(pattern=SHA256_PATTERN)


# ---------------------------------------------------------------------------
# Rooms + portals + edges + loops
# ---------------------------------------------------------------------------


def _validate_center(value: tuple[float, float, float]) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ValueError("center_enu_m must be a 3-tuple")
    for c in value:
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            raise ValueError(f"center_enu_m component not numeric: {c!r}")
        if math_is_invalid(c):
            raise ValueError(f"center_enu_m component not finite: {c!r}")
        if abs(c) > MAX_ABS_COORDINATE_M:
            raise ValueError(
                f"center_enu_m component out of bounds: {c} "
                f"(max abs {MAX_ABS_COORDINATE_M}m)")
    return (float(value[0]), float(value[1]), float(value[2]))


def math_is_invalid(c: float) -> bool:
    import math
    return math.isnan(c) or math.isinf(c)


class Room(_Frozen):
    room_id: str = Field(pattern=STABLE_ID_PATTERN)
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["exterior", "interior", "transition"]
    center_enu_m: tuple[float, float, float]
    collision_proxy_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_center(self) -> Room:
        _validate_center(self.center_enu_m)
        if self.label.strip() != self.label or not self.label.strip():
            raise ValueError(
                f"room label must be non-empty trimmed string (got {self.label!r})")
        return self


class Portal(_Frozen):
    portal_id: str = Field(pattern=STABLE_ID_PATTERN)
    room_ids: tuple[str, str]
    endpoints_enu_m: tuple[tuple[float, float, float], tuple[float, float, float]]
    clear_width_m: float = Field(gt=0, allow_inf_nan=False, le=MAX_CLEARANCE_M)
    clear_height_m: float = Field(gt=0, allow_inf_nan=False, le=MAX_CLEARANCE_M)
    collision_proxy_sha256: str = Field(pattern=SHA256_PATTERN)
    source_input_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_structure(self) -> Portal:
        if self.room_ids[0] == self.room_ids[1]:
            raise ValueError(
                f"portal {self.portal_id}: room_ids must be two distinct rooms "
                f"(got {self.room_ids})")
        if len(self.endpoints_enu_m) != 2:
            raise ValueError(
                f"portal {self.portal_id}: endpoints_enu_m must be a 2-tuple")
        for ep in self.endpoints_enu_m:
            _validate_center(ep)
        return self


class DirectedEdge(_Frozen):
    edge_id: str = Field(pattern=STABLE_ID_PATTERN)
    portal_id: str = Field(pattern=STABLE_ID_PATTERN)
    from_room_id: str = Field(pattern=STABLE_ID_PATTERN)
    to_room_id: str = Field(pattern=STABLE_ID_PATTERN)

    @model_validator(mode="after")
    def _distinct_endpoints(self) -> DirectedEdge:
        if self.from_room_id == self.to_room_id:
            raise ValueError(
                f"edge {self.edge_id}: from_room_id == to_room_id "
                f"({self.from_room_id})")
        return self


class RouteLoop(_Frozen):
    loop_id: str = Field(pattern=STABLE_ID_PATTERN)
    edge_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_edges(self) -> RouteLoop:
        if len(self.edge_ids) < 3:
            raise ValueError(
                f"loop {self.loop_id}: edge_ids must contain >= 3 edges "
                f"(got {len(self.edge_ids)})")
        if len(set(self.edge_ids)) != len(self.edge_ids):
            raise ValueError(
                f"loop {self.loop_id}: edge_ids must be unique "
                f"(got duplicates)")
        for eid in self.edge_ids:
            if not _RE_STABLE_ID.match(eid):
                raise ValueError(
                    f"loop {self.loop_id}: edge_id {eid!r} is not a stable id")
        return self


# ---------------------------------------------------------------------------
# Assembled graph
# ---------------------------------------------------------------------------


class RoamingGraph(_Frozen):
    schema_version: Literal[1]
    graph_schema: Literal["nantai.synthetic-village.roaming-graph.v1"]
    graph_id: str = Field(pattern=STABLE_ID_PATTERN)
    status: Literal["candidate"]
    synthetic: Literal[True]
    coordinate_frame: CoordinateFrame
    trust: TrustDeclarations
    bindings: GraphBindings
    entry_room_id: str = Field(pattern=STABLE_ID_PATTERN)
    rooms: tuple[Room, ...]
    portals: tuple[Portal, ...]
    directed_edges: tuple[DirectedEdge, ...]
    route_loops: tuple[RouteLoop, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_counts(self) -> RoamingGraph:
        if not (2 <= len(self.rooms) <= MAX_ROOMS):
            raise RoamingGraphError(
                f"rooms count out of bounds: {len(self.rooms)} "
                f"(need 2..{MAX_ROOMS})")
        if not (1 <= len(self.portals) <= MAX_PORTALS):
            raise RoamingGraphError(
                f"portals count out of bounds: {len(self.portals)} "
                f"(need 1..{MAX_PORTALS})")
        if not (2 <= len(self.directed_edges) <= MAX_EDGES):
            raise RoamingGraphError(
                f"directed_edges count out of bounds: {len(self.directed_edges)} "
                f"(need 2..{MAX_EDGES})")
        if len(self.route_loops) > MAX_LOOPS:
            raise RoamingGraphError(
                f"route_loops count out of bounds: {len(self.route_loops)} "
                f"(max {MAX_LOOPS})")
        if len(self.directed_edges) != len(self.portals) * 2:
            raise RoamingGraphError(
                f"directed_edges must be exactly 2x portals "
                f"(got {len(self.directed_edges)} edges, "
                f"{len(self.portals)} portals)")
        return self


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _edge_id_for(portal_id: str, from_room: str, to_room: str) -> str:
    """Deterministic edge id derived from a SHA-256 of
    ``<portal_id>::<from_room>-><to_room>``.

    The id is ``edge-<16 hex>`` — fixed length, stable across runs, and
    short enough to fit the 64-char stable-id limit even when
    ``portal_id`` / ``room_id`` values are long module IDs.  Re-building
    the same graph from the same inputs always yields the same edge IDs
    (no uuid, no insertion-order dependence).
    """
    import hashlib
    raw = f"{portal_id}::{from_room}->{to_room}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"edge-{h}"


def build_roaming_graph(
    *,
    graph_id: str,
    entry_room_id: str,
    bindings: GraphBindings,
    rooms: Sequence[Room],
    portals: Sequence[Portal],
    route_loops: Sequence[RouteLoop] = (),
) -> RoamingGraph:
    """Assemble a fail-closed ``RoamingGraph`` from declared rooms, portals
    and route loops.

    Directed edges are auto-generated: every portal produces exactly two
    reciprocal directed edges (A->B and B->A), in portal_id / room order.
    The caller cannot inject extra or one-way edges.

    Structural validation (fail-closed):
      - duplicate room / portal / edge / loop ids;
      - dangling rooms (no incident portal);
      - portals referencing unknown rooms;
      - self-portals (caught earlier by Portal);
      - unknown entry room;
      - loops referencing unknown edges;
      - open or broken loop chains (head-to-tail check).
    """
    if not _RE_STABLE_ID.match(graph_id):
        raise RoamingGraphError(f"graph_id is not a stable id: {graph_id!r}")
    if not _RE_STABLE_ID.match(entry_room_id):
        raise RoamingGraphError(
            f"entry_room_id is not a stable id: {entry_room_id!r}")

    rooms_t = tuple(rooms)
    portals_t = tuple(portals)
    loops_t = tuple(route_loops)

    # Duplicate room ids
    room_ids = [r.room_id for r in rooms_t]
    if len(set(room_ids)) != len(room_ids):
        dup = next(rid for rid in room_ids if room_ids.count(rid) > 1)
        raise RoamingGraphError(f"duplicate room_id: {dup}")

    # Duplicate portal ids
    portal_ids = [p.portal_id for p in portals_t]
    if len(set(portal_ids)) != len(portal_ids):
        dup = next(pid for pid in portal_ids if portal_ids.count(pid) > 1)
        raise RoamingGraphError(f"duplicate portal_id: {dup}")

    # Duplicate loop ids
    loop_ids = [lp.loop_id for lp in loops_t]
    if len(set(loop_ids)) != len(loop_ids):
        dup = next(lid for lid in loop_ids if loop_ids.count(lid) > 1)
        raise RoamingGraphError(f"duplicate loop_id: {dup}")

    room_id_set = set(room_ids)

    # Entry room must exist
    if entry_room_id not in room_id_set:
        raise RoamingGraphError(
            f"entry_room_id {entry_room_id!r} not in rooms")

    # Portals must reference existing rooms; collect incident rooms.
    incident_rooms: set[str] = set()
    for p in portals_t:
        for rid in p.room_ids:
            if rid not in room_id_set:
                raise RoamingGraphError(
                    f"portal {p.portal_id} references unknown room {rid!r}")
            incident_rooms.add(rid)

    # Every room must have at least one incident portal.
    orphans = room_id_set - incident_rooms
    if orphans:
        raise RoamingGraphError(
            f"rooms with no incident portal: {sorted(orphans)}")

    # Auto-generate reciprocal directed edges (2 per portal).
    edges: list[DirectedEdge] = []
    edge_ids_seen: set[str] = set()
    for p in portals_t:
        a, b = p.room_ids
        for from_room, to_room in ((a, b), (b, a)):
            eid = _edge_id_for(p.portal_id, from_room, to_room)
            if eid in edge_ids_seen:
                raise RoamingGraphError(
                    f"auto-generated edge id collision: {eid} "
                    f"(portal {p.portal_id}); this indicates a duplicate "
                    f"portal_id or room_id combination")
            edges.append(DirectedEdge(
                edge_id=eid,
                portal_id=p.portal_id,
                from_room_id=from_room,
                to_room_id=to_room,
            ))
            edge_ids_seen.add(eid)

    # Loops must reference existing edges and form closed chains.
    edge_map = {e.edge_id: e for e in edges}
    for loop in loops_t:
        for eid in loop.edge_ids:
            if eid not in edge_map:
                raise RoamingGraphError(
                    f"loop {loop.loop_id} references unknown edge {eid!r}")
        ordered = [edge_map[eid] for eid in loop.edge_ids]
        n = len(ordered)
        for i in range(n):
            cur = ordered[i]
            nxt = ordered[(i + 1) % n]
            if cur.to_room_id != nxt.from_room_id:
                raise RoamingGraphError(
                    f"loop {loop.loop_id} is not a closed chain: "
                    f"edge[{i}] {cur.edge_id} ends at {cur.to_room_id!r} "
                    f"but edge[{(i + 1) % n}] {nxt.edge_id} starts at "
                    f"{nxt.from_room_id!r}")

    graph = RoamingGraph(
        schema_version=SCHEMA_VERSION,
        graph_schema=ROAMING_GRAPH_SCHEMA,
        graph_id=graph_id,
        status="candidate",
        synthetic=True,
        coordinate_frame=CoordinateFrame(
            name="synthetic-village-world-enu",
            units="meters",
            handedness="right",
            axes={"x": "east", "y": "north", "z": "up"},
        ),
        trust=TrustDeclarations(
            geometry="modeled-unverified",
            connectivity="machine-checked-graph-only",
            coverage="not-verified",
            arbitrary_coordinate_reachability="not-claimed",
            trust_effect="none",
        ),
        bindings=bindings,
        entry_room_id=entry_room_id,
        rooms=rooms_t,
        portals=portals_t,
        directed_edges=tuple(edges),
        route_loops=loops_t,
    )
    return graph


# ---------------------------------------------------------------------------
# Serialization — canonical LF JSON
# ---------------------------------------------------------------------------


def _room_to_dict(room: Room) -> dict:
    return {
        "room_id": room.room_id,
        "label": room.label,
        "kind": room.kind,
        "center_enu_m": list(room.center_enu_m),
        "collision_proxy_sha256": room.collision_proxy_sha256,
    }


def _portal_to_dict(portal: Portal) -> dict:
    return {
        "portal_id": portal.portal_id,
        "room_ids": list(portal.room_ids),
        "endpoints_enu_m": [list(ep) for ep in portal.endpoints_enu_m],
        "clear_width_m": portal.clear_width_m,
        "clear_height_m": portal.clear_height_m,
        "collision_proxy_sha256": portal.collision_proxy_sha256,
        "source_input_sha256": portal.source_input_sha256,
    }


def _edge_to_dict(edge: DirectedEdge) -> dict:
    return {
        "edge_id": edge.edge_id,
        "portal_id": edge.portal_id,
        "from_room_id": edge.from_room_id,
        "to_room_id": edge.to_room_id,
    }


def _loop_to_dict(loop: RouteLoop) -> dict:
    return {
        "loop_id": loop.loop_id,
        "edge_ids": list(loop.edge_ids),
    }


def graph_to_dict(graph: RoamingGraph) -> dict:
    """Return the canonical dict form of ``graph``.

    Field order is fixed (sorted_keys on json.dumps guarantees this for
    serialization), so two calls on the same graph produce byte-identical
    JSON.
    """
    return {
        "schema_version": graph.schema_version,
        "graph_schema": graph.graph_schema,
        "graph_id": graph.graph_id,
        "status": graph.status,
        "synthetic": graph.synthetic,
        "coordinate_frame": {
            "name": graph.coordinate_frame.name,
            "units": graph.coordinate_frame.units,
            "handedness": graph.coordinate_frame.handedness,
            "axes": dict(graph.coordinate_frame.axes),
        },
        "trust": {
            "geometry": graph.trust.geometry,
            "connectivity": graph.trust.connectivity,
            "coverage": graph.trust.coverage,
            "arbitrary_coordinate_reachability": graph.trust.arbitrary_coordinate_reachability,
            "trust_effect": graph.trust.trust_effect,
        },
        "bindings": {
            "scene_artifact_sha256": graph.bindings.scene_artifact_sha256,
            "build_report_sha256": graph.bindings.build_report_sha256,
            "source_plan_sha256": graph.bindings.source_plan_sha256,
            "collision_manifest_sha256": graph.bindings.collision_manifest_sha256,
        },
        "entry_room_id": graph.entry_room_id,
        "rooms": [_room_to_dict(r) for r in graph.rooms],
        "portals": [_portal_to_dict(p) for p in graph.portals],
        "directed_edges": [_edge_to_dict(e) for e in graph.directed_edges],
        "route_loops": [_loop_to_dict(lp) for lp in graph.route_loops],
    }


def serialize_roaming_graph(graph: RoamingGraph) -> str:
    """Serialize ``graph`` to canonical LF JSON.

    The output is bit-stable across runs:
      - ``sort_keys=True`` so field order never varies;
      - ``ensure_ascii=False`` so non-ASCII labels are preserved;
      - ``separators=(",", ":")`` for compact output;
      - trailing ``"\\n"`` (LF only, no CR).
    """
    data = graph_to_dict(graph)
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return blob + "\n"
