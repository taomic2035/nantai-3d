"""RED tests for the roaming-graph v1 pure-Python producer.

These tests define the contract that
``pipeline/synthetic_village/roaming_graph.py`` must satisfy before any
implementation is written. They encode the fail-closed rules from
``handoff/HANDOFF-GLM-009-roaming-graph-producer.md`` and the viewer-side
validator at ``web/viewer/roaming-graph.mjs``.

The producer must:

- emit a canonical LF JSON document whose header is bit-stable across runs;
- fail closed on duplicate / dangling / non-reciprocal / non-finite /
  non-positive / malformed / uppercase / open-loop / unknown-entry /
  trust-promoted inputs;
- bind every room and portal to a real collision-proxy SHA (a placeholder
  or fabricated SHA must raise, not pass);
- preserve the fixed trust values ``status=candidate``,
  ``synthetic=true``, ``geometry=modeled-unverified`` etc.

A structurally valid graph with multiple connected components is allowed;
it is represented as ``fragmented`` by the viewer, not rejected here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.synthetic_village.roaming_graph import (
    GraphBindings,
    Portal,
    RoamingGraph,
    RoamingGraphError,
    Room,
    RouteLoop,
    build_roaming_graph,
    serialize_roaming_graph,
)
from scripts import emit_roaming_graph

# 64-char lowercase hex SHA-256 stubs. The producer must NOT invent these;
# they stand in for real collision-proxy payload SHAs measured from an exact
# Blender build. Tests use them only to exercise the structural contract.
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64
_SHA_E = "e" * 64
_SHA_F = "f" * 64
_SHA_G = "0123456789abcdef" * 4


def test_host_driver_reports_missing_collision_sha_as_structured_error() -> None:
    manifest = {
        "rooms": [
            {
                "room_id": "room-a",
                "label": "Room A",
                "kind": "exterior",
                "center_enu_m": [0.0, 0.0, 0.0],
            },
        ],
    }

    with pytest.raises(
        emit_roaming_graph.EmitGraphError,
        match=r"rooms\[0\].*collision_proxy_sha256",
    ):
        emit_roaming_graph._build_rooms(manifest)


def _bindings() -> GraphBindings:
    return GraphBindings(
        scene_artifact_sha256=_SHA_A,
        build_report_sha256=_SHA_B,
        source_plan_sha256=_SHA_C,
        collision_manifest_sha256=_SHA_D,
    )


def _two_room_graph() -> RoamingGraph:
    """Smallest valid graph: 2 rooms, 1 portal, 2 reciprocal edges, 0 loops."""
    return build_roaming_graph(
        graph_id="courtyard-pair-v1",
        entry_room_id="courtyard-east",
        bindings=_bindings(),
        rooms=(
            Room(
                room_id="courtyard-east",
                label="East Courtyard",
                kind="exterior",
                center_enu_m=(10.0, 5.0, 0.0),
                collision_proxy_sha256=_SHA_E,
            ),
            Room(
                room_id="courtyard-west",
                label="West Courtyard",
                kind="exterior",
                center_enu_m=(-10.0, 5.0, 0.0),
                collision_proxy_sha256=_SHA_F,
            ),
        ),
        portals=(
            Portal(
                portal_id="archway-01",
                room_ids=("courtyard-east", "courtyard-west"),
                endpoints_enu_m=((10.0, 5.0, 0.0), (-10.0, 5.0, 0.0)),
                clear_width_m=2.0,
                clear_height_m=2.5,
                collision_proxy_sha256=_SHA_G,
                source_input_sha256=_SHA_A,
            ),
        ),
        route_loops=(),
    )


# ---------------------------------------------------------------------------
# Header + bindings
# ---------------------------------------------------------------------------


class TestRoamingGraphHeader:
    def test_valid_two_room_graph_passes(self) -> None:
        graph = _two_room_graph()
        assert graph.schema_version == 1
        assert graph.graph_schema == "nantai.synthetic-village.roaming-graph.v1"
        assert graph.graph_id == "courtyard-pair-v1"
        assert graph.status == "candidate"
        assert graph.synthetic is True
        assert graph.entry_room_id == "courtyard-east"
        assert graph.coordinate_frame.name == "synthetic-village-world-enu"
        assert graph.coordinate_frame.units == "meters"
        assert graph.coordinate_frame.handedness == "right"
        assert graph.coordinate_frame.axes == {"x": "east", "y": "north", "z": "up"}

    def test_trust_fields_are_literal_locked(self) -> None:
        graph = _two_room_graph()
        assert graph.trust.geometry == "modeled-unverified"
        assert graph.trust.connectivity == "machine-checked-graph-only"
        assert graph.trust.coverage == "not-verified"
        assert graph.trust.arbitrary_coordinate_reachability == "not-claimed"
        assert graph.trust.trust_effect == "none"

    def test_bindings_must_be_64_hex(self) -> None:
        with pytest.raises(ValueError):
            GraphBindings(
                scene_artifact_sha256="short",
                build_report_sha256=_SHA_B,
                source_plan_sha256=_SHA_C,
                collision_manifest_sha256=_SHA_D,
            )

    def test_uppercase_hash_rejected(self) -> None:
        with pytest.raises(ValueError):
            GraphBindings(
                scene_artifact_sha256="A" * 64,
                build_report_sha256=_SHA_B,
                source_plan_sha256=_SHA_C,
                collision_manifest_sha256=_SHA_D,
            )

    def test_status_cannot_be_promoted(self) -> None:
        # The producer must not let a caller write status=accepted.
        graph = _two_room_graph()
        assert graph.status == "candidate"
        with pytest.raises(ValueError):
            graph.model_copy(update={"status": "accepted"})

    def test_synthetic_cannot_be_false(self) -> None:
        graph = _two_room_graph()
        with pytest.raises(ValueError):
            graph.model_copy(update={"synthetic": False})

    def test_trust_cannot_be_promoted_to_metric(self) -> None:
        graph = _two_room_graph()
        with pytest.raises(ValueError):
            graph.model_copy(
                update={"trust": graph.trust.model_copy(update={"geometry": "metric-aligned"})}
            )


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------


class TestRoamingGraphRooms:
    def test_room_id_must_be_lowercase_hyphenated(self) -> None:
        with pytest.raises(ValueError):
            Room(
                room_id="CourtyardEast",
                label="East",
                kind="exterior",
                center_enu_m=(0.0, 0.0, 0.0),
                collision_proxy_sha256=_SHA_E,
            )

    def test_room_id_must_not_start_with_dash(self) -> None:
        with pytest.raises(ValueError):
            Room(
                room_id="-courtyard",
                label="C",
                kind="exterior",
                center_enu_m=(0.0, 0.0, 0.0),
                collision_proxy_sha256=_SHA_E,
            )

    def test_room_kind_must_be_known(self) -> None:
        with pytest.raises(ValueError):
            Room(
                room_id="r",
                label="L",
                kind="underwater",  # not in {exterior, interior, transition}
                center_enu_m=(0.0, 0.0, 0.0),
                collision_proxy_sha256=_SHA_E,
            )

    def test_room_center_must_be_finite(self) -> None:
        with pytest.raises(ValueError):
            Room(
                room_id="r",
                label="L",
                kind="exterior",
                center_enu_m=(float("nan"), 0.0, 0.0),
                collision_proxy_sha256=_SHA_E,
            )

    def test_room_center_must_be_bounded(self) -> None:
        with pytest.raises(ValueError):
            Room(
                room_id="r",
                label="L",
                kind="exterior",
                center_enu_m=(1e9, 0.0, 0.0),  # > 1e7 metres
                collision_proxy_sha256=_SHA_E,
            )

    def test_room_collision_proxy_sha_must_be_64_hex(self) -> None:
        with pytest.raises(ValueError):
            Room(
                room_id="r",
                label="L",
                kind="exterior",
                center_enu_m=(0.0, 0.0, 0.0),
                collision_proxy_sha256="placeholder",
            )

    def test_room_label_must_be_nonempty(self) -> None:
        with pytest.raises(ValueError):
            Room(
                room_id="r",
                label="   ",
                kind="exterior",
                center_enu_m=(0.0, 0.0, 0.0),
                collision_proxy_sha256=_SHA_E,
            )


# ---------------------------------------------------------------------------
# Portals + edges
# ---------------------------------------------------------------------------


class TestRoamingGraphPortals:
    def test_portal_must_have_two_distinct_rooms(self) -> None:
        with pytest.raises(ValueError):
            Portal(
                portal_id="p",
                room_ids=("r", "r"),  # self-portal
                endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                clear_width_m=1.0,
                clear_height_m=2.0,
                collision_proxy_sha256=_SHA_G,
                source_input_sha256=_SHA_A,
            )

    def test_portal_must_have_two_endpoints(self) -> None:
        with pytest.raises(ValueError):
            Portal(
                portal_id="p",
                room_ids=("a", "b"),
                endpoints_enu_m=((0, 0, 0),),  # only one
                clear_width_m=1.0,
                clear_height_m=2.0,
                collision_proxy_sha256=_SHA_G,
                source_input_sha256=_SHA_A,
            )

    def test_portal_endpoint_must_be_finite(self) -> None:
        with pytest.raises(ValueError):
            Portal(
                portal_id="p",
                room_ids=("a", "b"),
                endpoints_enu_m=((float("inf"), 0, 0), (1, 0, 0)),
                clear_width_m=1.0,
                clear_height_m=2.0,
                collision_proxy_sha256=_SHA_G,
                source_input_sha256=_SHA_A,
            )

    def test_portal_clear_width_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            Portal(
                portal_id="p",
                room_ids=("a", "b"),
                endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                clear_width_m=0.0,
                clear_height_m=2.0,
                collision_proxy_sha256=_SHA_G,
                source_input_sha256=_SHA_A,
            )

    def test_portal_clear_height_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            Portal(
                portal_id="p",
                room_ids=("a", "b"),
                endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                clear_width_m=1.0,
                clear_height_m=-2.0,
                collision_proxy_sha256=_SHA_G,
                source_input_sha256=_SHA_A,
            )

    def test_portal_clearance_must_be_bounded(self) -> None:
        with pytest.raises(ValueError):
            Portal(
                portal_id="p",
                room_ids=("a", "b"),
                endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                clear_width_m=500.0,  # > 100 m
                clear_height_m=2.0,
                collision_proxy_sha256=_SHA_G,
                source_input_sha256=_SHA_A,
            )

    def test_portal_collision_proxy_sha_must_be_64_hex(self) -> None:
        with pytest.raises(ValueError):
            Portal(
                portal_id="p",
                room_ids=("a", "b"),
                endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                clear_width_m=1.0,
                clear_height_m=2.0,
                collision_proxy_sha256="not-a-sha",
                source_input_sha256=_SHA_A,
            )

    def test_portal_source_input_sha_must_be_64_hex(self) -> None:
        with pytest.raises(ValueError):
            Portal(
                portal_id="p",
                room_ids=("a", "b"),
                endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                clear_width_m=1.0,
                clear_height_m=2.0,
                collision_proxy_sha256=_SHA_G,
                source_input_sha256="",
            )


# ---------------------------------------------------------------------------
# Builder-level structural rules
# ---------------------------------------------------------------------------


class TestRoamingGraphBuilder:
    def test_duplicate_room_id_rejected(self) -> None:
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=(
                    Room(
                        room_id="r-1",
                        label="A",
                        kind="exterior",
                        center_enu_m=(0, 0, 0),
                        collision_proxy_sha256=_SHA_E,
                    ),
                    Room(
                        room_id="r-1",
                        label="B",
                        kind="exterior",
                        center_enu_m=(1, 0, 0),
                        collision_proxy_sha256=_SHA_F,
                    ),
                ),
                portals=(
                    Portal(
                        portal_id="p",
                        room_ids=("r-1", "r-2"),
                        endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                        clear_width_m=1.0,
                        clear_height_m=2.0,
                        collision_proxy_sha256=_SHA_G,
                        source_input_sha256=_SHA_A,
                    ),
                ),
                route_loops=(),
            )

    def test_duplicate_portal_id_rejected(self) -> None:
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=(
                    Room(
                        room_id="r-1",
                        label="A",
                        kind="exterior",
                        center_enu_m=(0, 0, 0),
                        collision_proxy_sha256=_SHA_E,
                    ),
                    Room(
                        room_id="r-2",
                        label="B",
                        kind="exterior",
                        center_enu_m=(1, 0, 0),
                        collision_proxy_sha256=_SHA_F,
                    ),
                    Room(
                        room_id="r-3",
                        label="C",
                        kind="exterior",
                        center_enu_m=(2, 0, 0),
                        collision_proxy_sha256=_SHA_G,
                    ),
                ),
                portals=(
                    Portal(
                        portal_id="p",
                        room_ids=("r-1", "r-2"),
                        endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                        clear_width_m=1.0,
                        clear_height_m=2.0,
                        collision_proxy_sha256=_SHA_G,
                        source_input_sha256=_SHA_A,
                    ),
                    Portal(
                        portal_id="p",
                        room_ids=("r-2", "r-3"),
                        endpoints_enu_m=((1, 0, 0), (2, 0, 0)),
                        clear_width_m=1.0,
                        clear_height_m=2.0,
                        collision_proxy_sha256=_SHA_G,
                        source_input_sha256=_SHA_A,
                    ),
                ),
                route_loops=(),
            )

    def test_dangling_room_rejected(self) -> None:
        """A room with no incident portal must fail closed."""
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=(
                    Room(
                        room_id="r-1",
                        label="A",
                        kind="exterior",
                        center_enu_m=(0, 0, 0),
                        collision_proxy_sha256=_SHA_E,
                    ),
                    Room(
                        room_id="r-2",
                        label="B",
                        kind="exterior",
                        center_enu_m=(1, 0, 0),
                        collision_proxy_sha256=_SHA_F,
                    ),
                    Room(
                        room_id="r-3",
                        label="C",
                        kind="exterior",
                        center_enu_m=(2, 0, 0),
                        collision_proxy_sha256=_SHA_G,
                    ),
                ),
                portals=(
                    Portal(
                        portal_id="p",
                        room_ids=("r-1", "r-2"),
                        endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                        clear_width_m=1.0,
                        clear_height_m=2.0,
                        collision_proxy_sha256=_SHA_G,
                        source_input_sha256=_SHA_A,
                    ),
                    # r-3 has no portal
                ),
                route_loops=(),
            )

    def test_portal_referencing_unknown_room_rejected(self) -> None:
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=(
                    Room(
                        room_id="r-1",
                        label="A",
                        kind="exterior",
                        center_enu_m=(0, 0, 0),
                        collision_proxy_sha256=_SHA_E,
                    ),
                    Room(
                        room_id="r-2",
                        label="B",
                        kind="exterior",
                        center_enu_m=(1, 0, 0),
                        collision_proxy_sha256=_SHA_F,
                    ),
                ),
                portals=(
                    Portal(
                        portal_id="p",
                        room_ids=("r-1", "r-ghost"),
                        endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                        clear_width_m=1.0,
                        clear_height_m=2.0,
                        collision_proxy_sha256=_SHA_G,
                        source_input_sha256=_SHA_A,
                    ),
                ),
                route_loops=(),
            )

    def test_unknown_entry_room_rejected(self) -> None:
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-ghost",
                bindings=_bindings(),
                rooms=(
                    Room(
                        room_id="r-1",
                        label="A",
                        kind="exterior",
                        center_enu_m=(0, 0, 0),
                        collision_proxy_sha256=_SHA_E,
                    ),
                    Room(
                        room_id="r-2",
                        label="B",
                        kind="exterior",
                        center_enu_m=(1, 0, 0),
                        collision_proxy_sha256=_SHA_F,
                    ),
                ),
                portals=(
                    Portal(
                        portal_id="p",
                        room_ids=("r-1", "r-2"),
                        endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                        clear_width_m=1.0,
                        clear_height_m=2.0,
                        collision_proxy_sha256=_SHA_G,
                        source_input_sha256=_SHA_A,
                    ),
                ),
                route_loops=(),
            )

    def test_two_room_graph_auto_generates_reciprocal_edges(self) -> None:
        graph = _two_room_graph()
        assert len(graph.directed_edges) == 2
        edge_ids = {e.edge_id for e in graph.directed_edges}
        assert len(edge_ids) == 2
        directions = {(e.from_room_id, e.to_room_id) for e in graph.directed_edges}
        assert ("courtyard-east", "courtyard-west") in directions
        assert ("courtyard-west", "courtyard-east") in directions
        # Both edges share the same portal_id
        portal_ids = {e.portal_id for e in graph.directed_edges}
        assert portal_ids == {"archway-01"}

    def test_min_two_rooms_required(self) -> None:
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=(
                    Room(
                        room_id="r-1",
                        label="A",
                        kind="exterior",
                        center_enu_m=(0, 0, 0),
                        collision_proxy_sha256=_SHA_E,
                    ),
                ),
                portals=(),
                route_loops=(),
            )

    def test_min_one_portal_required(self) -> None:
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=(
                    Room(
                        room_id="r-1",
                        label="A",
                        kind="exterior",
                        center_enu_m=(0, 0, 0),
                        collision_proxy_sha256=_SHA_E,
                    ),
                    Room(
                        room_id="r-2",
                        label="B",
                        kind="exterior",
                        center_enu_m=(1, 0, 0),
                        collision_proxy_sha256=_SHA_F,
                    ),
                ),
                portals=(),
                route_loops=(),
            )


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------


class TestRoamingGraphLoops:
    def _three_room_triangle(self) -> RoamingGraph:
        return build_roaming_graph(
            graph_id="triangle-v1",
            entry_room_id="r-1",
            bindings=_bindings(),
            rooms=(
                Room(
                    room_id="r-1",
                    label="A",
                    kind="exterior",
                    center_enu_m=(0, 0, 0),
                    collision_proxy_sha256=_SHA_E,
                ),
                Room(
                    room_id="r-2",
                    label="B",
                    kind="exterior",
                    center_enu_m=(1, 0, 0),
                    collision_proxy_sha256=_SHA_F,
                ),
                Room(
                    room_id="r-3",
                    label="C",
                    kind="exterior",
                    center_enu_m=(0.5, 1, 0),
                    collision_proxy_sha256=_SHA_G,
                ),
            ),
            portals=(
                Portal(
                    portal_id="p-1-2",
                    room_ids=("r-1", "r-2"),
                    endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                    clear_width_m=1.0,
                    clear_height_m=2.0,
                    collision_proxy_sha256=_SHA_G,
                    source_input_sha256=_SHA_A,
                ),
                Portal(
                    portal_id="p-2-3",
                    room_ids=("r-2", "r-3"),
                    endpoints_enu_m=((1, 0, 0), (0.5, 1, 0)),
                    clear_width_m=1.0,
                    clear_height_m=2.0,
                    collision_proxy_sha256=_SHA_G,
                    source_input_sha256=_SHA_A,
                ),
                Portal(
                    portal_id="p-3-1",
                    room_ids=("r-3", "r-1"),
                    endpoints_enu_m=((0.5, 1, 0), (0, 0, 0)),
                    clear_width_m=1.0,
                    clear_height_m=2.0,
                    collision_proxy_sha256=_SHA_G,
                    source_input_sha256=_SHA_A,
                ),
            ),
            route_loops=(),
        )

    def test_triangle_graph_has_six_edges(self) -> None:
        graph = self._three_room_triangle()
        assert len(graph.directed_edges) == 6  # 3 portals * 2 reciprocal

    def test_valid_closed_loop_passes(self) -> None:
        triangle = self._three_room_triangle()
        # Find one A->B->C->A chain
        e_ab = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-1" and e.to_room_id == "r-2"
        )
        e_bc = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-2" and e.to_room_id == "r-3"
        )
        e_ca = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-3" and e.to_room_id == "r-1"
        )
        loop = RouteLoop(
            loop_id="triangle-abc",
            edge_ids=(e_ab.edge_id, e_bc.edge_id, e_ca.edge_id),
        )
        graph = build_roaming_graph(
            graph_id="triangle-loop-v1",
            entry_room_id="r-1",
            bindings=_bindings(),
            rooms=triangle.rooms,
            portals=triangle.portals,
            route_loops=(loop,),
        )
        assert len(graph.route_loops) == 1

    def test_loop_with_too_few_edges_rejected(self) -> None:
        triangle = self._three_room_triangle()
        e_ab = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-1" and e.to_room_id == "r-2"
        )
        with pytest.raises(ValueError):
            RouteLoop(loop_id="l", edge_ids=(e_ab.edge_id,))  # < 3

    def test_loop_with_duplicate_edge_rejected(self) -> None:
        triangle = self._three_room_triangle()
        e_ab = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-1" and e.to_room_id == "r-2"
        )
        e_bc = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-2" and e.to_room_id == "r-3"
        )
        with pytest.raises(ValueError):
            RouteLoop(loop_id="l", edge_ids=(e_ab.edge_id, e_bc.edge_id, e_ab.edge_id))

    def test_loop_with_dangling_edge_rejected(self) -> None:
        triangle = self._three_room_triangle()
        e_ab = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-1" and e.to_room_id == "r-2"
        )
        e_bc = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-2" and e.to_room_id == "r-3"
        )
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=triangle.rooms,
                portals=triangle.portals,
                route_loops=(
                    RouteLoop(
                        loop_id="l", edge_ids=(e_ab.edge_id, e_bc.edge_id, "nonexistent-edge")
                    ),
                ),
            )

    def test_open_loop_rejected(self) -> None:
        """Chain A->B->C->B does not close (last.to != first.from)."""
        triangle = self._three_room_triangle()
        e_ab = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-1" and e.to_room_id == "r-2"
        )
        e_bc = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-2" and e.to_room_id == "r-3"
        )
        e_cb = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-3" and e.to_room_id == "r-2"
        )
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=triangle.rooms,
                portals=triangle.portals,
                route_loops=(
                    RouteLoop(loop_id="l", edge_ids=(e_ab.edge_id, e_bc.edge_id, e_cb.edge_id)),
                ),
            )

    def test_broken_loop_rejected(self) -> None:
        """Chain A->B then C->A (B.to != C.from) is broken."""
        triangle = self._three_room_triangle()
        e_ab = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-1" and e.to_room_id == "r-2"
        )
        e_ca = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-3" and e.to_room_id == "r-1"
        )
        e_ac = next(
            e for e in triangle.directed_edges if e.from_room_id == "r-1" and e.to_room_id == "r-3"
        )
        with pytest.raises(RoamingGraphError):
            build_roaming_graph(
                graph_id="g",
                entry_room_id="r-1",
                bindings=_bindings(),
                rooms=triangle.rooms,
                portals=triangle.portals,
                route_loops=(
                    RouteLoop(loop_id="l", edge_ids=(e_ab.edge_id, e_ca.edge_id, e_ac.edge_id)),
                ),
            )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestRoamingGraphSerialization:
    def test_serialize_round_trips_through_viewer_validator(self) -> None:
        """The JSON produced by ``serialize_roaming_graph`` must be valid
        under the browser validator's contract (we re-check the header
        fields here; full JS validation is in web/viewer/roaming-graph.test.mjs).
        """
        graph = _two_room_graph()
        blob = serialize_roaming_graph(graph)
        data = json.loads(blob)
        assert data["schema_version"] == 1
        assert data["graph_schema"] == "nantai.synthetic-village.roaming-graph.v1"
        assert data["graph_id"] == "courtyard-pair-v1"
        assert data["status"] == "candidate"
        assert data["synthetic"] is True
        assert data["entry_room_id"] == "courtyard-east"
        assert data["coordinate_frame"]["name"] == "synthetic-village-world-enu"
        assert data["coordinate_frame"]["units"] == "meters"
        assert data["coordinate_frame"]["handedness"] == "right"
        assert data["coordinate_frame"]["axes"] == {"x": "east", "y": "north", "z": "up"}
        assert data["trust"] == {
            "geometry": "modeled-unverified",
            "connectivity": "machine-checked-graph-only",
            "coverage": "not-verified",
            "arbitrary_coordinate_reachability": "not-claimed",
            "trust_effect": "none",
        }
        assert data["bindings"] == {
            "scene_artifact_sha256": _SHA_A,
            "build_report_sha256": _SHA_B,
            "source_plan_sha256": _SHA_C,
            "collision_manifest_sha256": _SHA_D,
        }
        assert len(data["rooms"]) == 2
        assert len(data["portals"]) == 1
        assert len(data["directed_edges"]) == 2
        assert data["route_loops"] == []

    def test_serialization_is_canonical_lf(self) -> None:
        """Two calls on the same graph must produce byte-identical output
        (deterministic, sort_keys, LF newlines)."""
        graph = _two_room_graph()
        a = serialize_roaming_graph(graph)
        b = serialize_roaming_graph(graph)
        assert a == b
        assert a.endswith("\n")
        assert "\r" not in a

    def test_serialized_edge_ids_are_stable(self) -> None:
        """Edge IDs are derived deterministically from portal_id + direction,
        not from a random uuid or insertion order."""
        graph = _two_room_graph()
        edges_by_portal = {}
        for edge in graph.directed_edges:
            edges_by_portal.setdefault(edge.portal_id, []).append(edge)
        ids = sorted(e.edge_id for e in edges_by_portal["archway-01"])
        # Re-build with same inputs — must produce same edge IDs.
        graph2 = _two_room_graph()
        ids2 = sorted(e.edge_id for e in graph2.directed_edges)
        assert ids == ids2

    def test_serialized_graph_has_no_trust_promotion(self) -> None:
        graph = _two_room_graph()
        blob = serialize_roaming_graph(graph)
        # Forbidden readiness / trust language.
        forbidden = [
            "360-ready",
            "360-ready-evidence",
            "coverage-complete",
            "arbitrary-coordinate-ready",
            "metric-aligned",
            "accepted",
        ]
        lower = blob.lower()
        for word in forbidden:
            assert word not in lower, f"trust-promoting term leaked: {word}"


# ---------------------------------------------------------------------------
# Fragmented (multi-component) valid graph
# ---------------------------------------------------------------------------


class TestRoamingGraphFragmented:
    def test_fragmented_graph_passes(self) -> None:
        """Two disconnected room-pairs form a structurally valid graph.
        The viewer labels this ``fragmented``; the producer must NOT reject
        it just because there is more than one connected component."""
        graph = build_roaming_graph(
            graph_id="fragmented-v1",
            entry_room_id="r-1",
            bindings=_bindings(),
            rooms=(
                Room(
                    room_id="r-1",
                    label="A",
                    kind="exterior",
                    center_enu_m=(0, 0, 0),
                    collision_proxy_sha256=_SHA_E,
                ),
                Room(
                    room_id="r-2",
                    label="B",
                    kind="exterior",
                    center_enu_m=(1, 0, 0),
                    collision_proxy_sha256=_SHA_F,
                ),
                Room(
                    room_id="r-3",
                    label="C",
                    kind="exterior",
                    center_enu_m=(100, 0, 0),
                    collision_proxy_sha256=_SHA_G,
                ),
                Room(
                    room_id="r-4",
                    label="D",
                    kind="exterior",
                    center_enu_m=(101, 0, 0),
                    collision_proxy_sha256=_SHA_A,
                ),
            ),
            portals=(
                Portal(
                    portal_id="p-1-2",
                    room_ids=("r-1", "r-2"),
                    endpoints_enu_m=((0, 0, 0), (1, 0, 0)),
                    clear_width_m=1.0,
                    clear_height_m=2.0,
                    collision_proxy_sha256=_SHA_G,
                    source_input_sha256=_SHA_A,
                ),
                Portal(
                    portal_id="p-3-4",
                    room_ids=("r-3", "r-4"),
                    endpoints_enu_m=((100, 0, 0), (101, 0, 0)),
                    clear_width_m=1.0,
                    clear_height_m=2.0,
                    collision_proxy_sha256=_SHA_G,
                    source_input_sha256=_SHA_A,
                ),
            ),
            route_loops=(),
        )
        assert len(graph.rooms) == 4
        assert len(graph.portals) == 2
        assert len(graph.directed_edges) == 4


# ---------------------------------------------------------------------------
# Cross-validation against the JS viewer validator
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_output_accepted_by_js_validator(tmp_path) -> None:
    """Build the canonical 2-room graph in Python, serialize to canonical
    LF JSON, then feed the bytes into the browser-side
    ``isRoamingGraph`` validator via node. The JS validator must accept
    the Python output.

    This is the producer↔consumer contract proof required by
    HANDOFF-GLM-009 step 9. A test fixture is not production evidence —
    it only proves the two validators agree on the schema.
    """
    graph = _two_room_graph()
    blob = serialize_roaming_graph(graph)
    json_path = tmp_path / "graph.json"
    json_path.write_text(blob, encoding="utf-8")

    # Embed the absolute path as a JSON string literal so node reads it
    # directly (avoids argv parsing quirks with `-e` + `--`).
    path_literal = json.dumps(str(json_path))
    js = """
import('./roaming-graph.mjs').then(async (mod) => {
  const fs = await import('fs');
  const blob = fs.readFileSync(%PATH%, 'utf8');
  const data = JSON.parse(blob);
  const ok = mod.isRoamingGraph(data);
  const vm = mod.roamingGraphViewModel(data);
  console.log(JSON.stringify({
    isRoamingGraph: ok,
    status: vm.status,
    room_count: vm.room_count,
    reachable_room_count: vm.reachable_room_count,
    component_count: vm.component_count,
    portal_count: vm.portal_count,
    loop_count: vm.loop_count,
    provenance_label: vm.provenance_label,
  }));
  process.exit(ok ? 0 : 1);
}).catch((err) => { console.error(err); process.exit(2); });
""".replace("%PATH%", path_literal)
    result = subprocess.run(
        ["node", "--input-type=module", "-e", js],
        cwd=str(Path(__file__).resolve().parent.parent / "web" / "viewer"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"JS validator rejected Python output: rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["isRoamingGraph"] is True
    assert summary["status"] == "graph-connected"
    assert summary["room_count"] == 2
    assert summary["reachable_room_count"] == 2
    assert summary["component_count"] == 1
    assert summary["portal_count"] == 1
    assert summary["loop_count"] == 0
    assert "modeled-unverified" in summary["provenance_label"]
    assert "graph only" in summary["provenance_label"]


class TestEmitRoamingGraphManifestReadOnce:
    """The manifest is the trust root for the roaming graph: its bytes feed
    the parsed rooms/portals/bindings AND its SHA is reported as
    ``collision_manifest_sha256``. Re-reading the manifest by name for SHA
    verification or for bindings creates a check-then-reopen window where the
    parsed manifest could diverge from the verified bytes. The manifest must
    be read once through a stable descriptor; its SHA must be computed from
    those same bytes and reused for every downstream consumer.
    """

    @staticmethod
    def _minimal_manifest() -> dict:
        return {
            "input_blend_sha256": _SHA_A,
            "input_plan_sha256": _SHA_C,
            "input_build_id": "build-001",
            "input_build_report_sha256": _SHA_B,
            "reciprocal_route_module_plan_sha256": _SHA_C,
            "rooms": [
                {
                    "room_id": "room-a",
                    "label": "Room A",
                    "kind": "exterior",
                    "center_enu_m": [0.0, 0.0, 0.0],
                    "collision_proxy_sha256": _SHA_E,
                },
            ],
            "portals": [],
        }

    @staticmethod
    def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
        blob = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        path = tmp_path / "roaming-graph-manifest.json"
        path.write_bytes(blob)
        sha = hashlib.sha256(blob).hexdigest()
        (tmp_path / "roaming-graph-manifest.sha256").write_text(sha + "\n", encoding="utf-8")
        return path

    def test_load_manifest_does_not_reread_for_sha_verification(self, tmp_path, monkeypatch):
        """RED: _load_manifest must compute the manifest SHA from the same
        bytes it parses, not re-read the file by name for SHA verification."""
        path = self._write_manifest(tmp_path, self._minimal_manifest())

        sha_calls: list[Path] = []
        orig_sha256_file = emit_roaming_graph._sha256_file

        def spy(p: Path) -> str:
            sha_calls.append(p)
            return orig_sha256_file(p)

        monkeypatch.setattr(emit_roaming_graph, "_sha256_file", spy)

        manifest = emit_roaming_graph._load_manifest(path)

        # The stored SHA must match the bytes that were parsed.
        assert manifest["_manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        # The manifest must NOT have been re-read by name for SHA verification.
        assert path not in sha_calls, (
            "manifest was re-read by name for SHA verification — the parsed "
            "bytes could diverge from the verified bytes (check-then-reopen)"
        )

    def test_build_bindings_reuses_stored_manifest_sha(self, tmp_path, monkeypatch):
        """RED: _build_bindings must reuse the stored _manifest_sha256, not
        re-read the manifest file by name for collision_manifest_sha256."""
        path = self._write_manifest(tmp_path, self._minimal_manifest())
        manifest = emit_roaming_graph._load_manifest(path)

        def guard(p: Path) -> str:
            raise AssertionError(f"_build_bindings re-read manifest by name for SHA: {p}")

        monkeypatch.setattr(emit_roaming_graph, "_sha256_file", guard)

        bindings = emit_roaming_graph._build_bindings(manifest)
        assert bindings.collision_manifest_sha256 == manifest["_manifest_sha256"]


# ---------------------------------------------------------------------------
# emit_roaming_graph_manifest: _load_and_validate_build_report stable descriptor.
# ---------------------------------------------------------------------------


def _load_manifest_runtime_module(monkeypatch: pytest.MonkeyPatch):
    """Load the Blender manifest script while stubbing its Blender-only imports."""
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts/blender/emit_roaming_graph_manifest.py"
    spec = importlib.util.spec_from_file_location(
        "_test_emit_roaming_graph_manifest", script_path,
    )
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


class TestManifestBuildReportStableDescriptor:
    """RED->GREEN: _load_and_validate_build_report must read the build report
    through a single O_NOFOLLOW descriptor so the SHA and the parsed JSON come
    from the SAME bytes.

    The previous implementation read bytes via ``path.read_bytes`` (one open)
    and then computed the SHA via ``_sha256_file`` (a second open).  Between
    the two opens an attacker could swap the file: the parsed JSON came from
    the swapped file while the SHA matched the original -- a false
    cryptographic binding.
    """

    @staticmethod
    def _manifest_request(blend_dir: Path, report_bytes: bytes) -> dict:
        report_sha = hashlib.sha256(report_bytes).hexdigest()
        return {
            "input_blend_path": str(blend_dir / "scene.blend"),
            "input_build_report_sha256": report_sha,
            "input_build_id": "a" * 64,
            "input_blend_sha256": "b" * 64,
        }

    @staticmethod
    def _valid_report_bytes(build_id: str, blend_sha: str) -> bytes:
        payload = json.dumps(
            {
                "build_id": build_id,
                "artifact": {"name": "scene", "kind": "blender-scene", "sha256": blend_sha},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return (payload + "\n").encode("utf-8")

    def test_rejects_symlink_build_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runtime = _load_manifest_runtime_module(monkeypatch)
        report_bytes = self._valid_report_bytes("a" * 64, "b" * 64)
        real = tmp_path / "real-report.json"
        real.write_bytes(report_bytes)
        link = tmp_path / "reciprocal-route-build-report.json"
        try:
            os.symlink(real, link)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")

        (tmp_path / "scene.blend").write_bytes(b"")

        request = self._manifest_request(tmp_path, report_bytes)
        with pytest.raises(runtime.ManifestBuildError):
            runtime._load_and_validate_build_report(request)

    def test_does_not_use_path_read_bytes_for_build_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runtime = _load_manifest_runtime_module(monkeypatch)
        report_bytes = self._valid_report_bytes("a" * 64, "b" * 64)
        report_path = tmp_path / "reciprocal-route-build-report.json"
        report_path.write_bytes(report_bytes)
        (tmp_path / "scene.blend").write_bytes(b"")

        original_read_bytes = Path.read_bytes

        def reject_read_bytes(self, *args, **kwargs):
            if self == report_path:
                raise AssertionError(
                    "build report must not be read via a separate Path.read_bytes"
                )
            return original_read_bytes(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)

        request = self._manifest_request(tmp_path, report_bytes)
        report = runtime._load_and_validate_build_report(request)
        assert report["build_id"] == "a" * 64

    def test_sha_binds_parsed_bytes_not_a_separate_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The SHA must be computed from the SAME bytes that are parsed.

        Swapping the file between the pre-check lstat and the os.open must be
        detected even when the swapped JSON carries the SAME identity fields
        (build_id, artifact.sha256) -- the SHA must bind the full bytes, not
        just the fields the validator happens to check.
        """
        runtime = _load_manifest_runtime_module(monkeypatch)
        original_bytes = self._valid_report_bytes("a" * 64, "b" * 64)
        swapped_payload = json.loads(original_bytes.decode("utf-8"))
        swapped_payload["counts"] = {"module_mesh_objects": 999}
        swapped_bytes = (
            json.dumps(swapped_payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        report_path = tmp_path / "reciprocal-route-build-report.json"
        report_path.write_bytes(original_bytes)
        (tmp_path / "scene.blend").write_bytes(b"")

        original_open = os.open
        swap_count = 0

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swap_count
            swap_count += 1
            if swap_count == 1 and Path(path) == report_path:
                report_path.write_bytes(swapped_bytes)
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", swapping_open)

        request = self._manifest_request(tmp_path, original_bytes)
        with pytest.raises(runtime.ManifestBuildError):
            runtime._load_and_validate_build_report(request)
