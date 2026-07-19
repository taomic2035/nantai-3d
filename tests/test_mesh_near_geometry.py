"""Deterministic semantic plans for high-detail near LOD2 geometry."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from pipeline.synthetic_village.mesh_asset_build import (
    ASSET_RECIPE_CONTRACTS,
    EXPECTED_ASSET_IDS,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    LOD2_TRIANGLE_BANDS,
)
from pipeline.synthetic_village.mesh_near_geometry import (
    NearGeometryPlanError,
    build_near_geometry_plan,
    canonical_near_geometry_plan_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "assets/registry.json").read_bytes())
FOOTPRINTS = {
    asset_id: tuple(REGISTRY["assets"][asset_id]["footprint_m"])
    for asset_id in EXPECTED_ASSET_IDS
}


def _component_relative_bounds(
    component: object,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
]:
    scale = component.scale
    rotation_degrees = component.rotation_degrees
    rx, ry, rz = (math.radians(value) for value in rotation_degrees)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    matrix = (
        (
            cz * cy,
            cz * sy * sx - sz * cx,
            cz * sy * cx + sz * sx,
        ),
        (
            sz * cy,
            sz * sy * sx + cz * cx,
            sz * sy * cx - cz * sx,
        ),
        (-sy, cy * sx, cy * cx),
    )
    if component.primitive in {"cylinder", "branch"}:
        triangles = component.planned_triangles
        segments = (triangles + 4) // 4
        vertices = tuple(
            (
                (end, 0.5 * math.cos(angle), 0.5 * math.sin(angle))
                if component.primitive == "branch"
                else (
                    0.5 * math.cos(angle),
                    0.5 * math.sin(angle),
                    end,
                )
            )
            for end in (-0.5, 0.5)
            for angle in (
                2 * math.pi * index / segments
                for index in range(segments)
            )
        )
        transformed = tuple(
            tuple(
                sum(
                    matrix[axis][source_axis]
                    * vertex[source_axis]
                    * scale[source_axis]
                    for source_axis in range(3)
                )
                for axis in range(3)
            )
            for vertex in vertices
        )
        return (
            tuple(
                min(vertex[axis] for vertex in transformed)
                for axis in range(3)
            ),
            tuple(
                max(vertex[axis] for vertex in transformed)
                for axis in range(3)
            ),
        )
    extents = tuple(
        sum(
            abs(matrix[axis][source_axis])
            * scale[source_axis]
            / 2
            for source_axis in range(3)
        )
        for axis in range(3)
    )
    return tuple(-value for value in extents), extents


@pytest.mark.parametrize("asset_id", EXPECTED_ASSET_IDS)
def test_near_plan_is_deterministic_complete_and_inside_footprint(
    asset_id: str,
) -> None:
    footprint = FOOTPRINTS[asset_id]
    first = build_near_geometry_plan(asset_id, footprint)
    second = build_near_geometry_plan(asset_id, footprint)

    assert canonical_near_geometry_plan_bytes(first) == (
        canonical_near_geometry_plan_bytes(second)
    )
    assert first.aabb.min == (-footprint[0] / 2, -footprint[1] / 2, 0.0)
    assert first.aabb.max == (
        footprint[0] / 2,
        footprint[1] / 2,
        footprint[2],
    )
    component_min_z = min(
        row.position[2] + _component_relative_bounds(row)[0][2]
        for row in first.components
    )
    assert component_min_z == pytest.approx(0.0, abs=1e-9)
    lower, upper = LOD2_TRIANGLE_BANDS[first.kind]
    assert lower <= first.planned_triangles <= upper
    assert first.planned_triangles == sum(
        row.planned_triangles
        for row in first.components
    )
    component_ids = tuple(row.component_id for row in first.components)
    assert component_ids == tuple(sorted(component_ids))
    assert len(component_ids) == len(set(component_ids))
    assert {
        row.material_slot_id for row in first.components
    } == set(first.material_slot_ids)
    assert all(
        math.isfinite(value)
        for row in first.components
        for value in (
            *row.position,
            *row.scale,
            *row.rotation_degrees,
        )
    )
    for row in first.components:
        relative_min, relative_max = _component_relative_bounds(row)
        minimum = tuple(
            row.position[axis] + relative_min[axis]
            for axis in range(3)
        )
        maximum = tuple(
            row.position[axis] + relative_max[axis]
            for axis in range(3)
        )
        assert minimum[0] >= -footprint[0] / 2 - 1e-9
        assert maximum[0] <= footprint[0] / 2 + 1e-9
        assert minimum[1] >= -footprint[1] / 2 - 1e-9
        assert maximum[1] <= footprint[1] / 2 + 1e-9
        assert minimum[2] >= -1e-9
        assert maximum[2] <= footprint[2] + 1e-9
    assert b"/Users/" not in canonical_near_geometry_plan_bytes(first)


@pytest.mark.parametrize(
    ("asset_id", "asset_specific", "wall_material"),
    (
        (
            "house_barn_01",
            "barn-door",
            "material-dark-timber-01",
        ),
        (
            "house_stone_01",
            "quoin",
            "material-fieldstone-01",
        ),
        (
            "house_thatch_01",
            "thatch-fringe",
            "material-rammed-earth-01",
        ),
        (
            "house_wood_01",
            "board-seam",
            "material-weathered-timber-01",
        ),
        (
            "house_wood_02",
            "brace",
            "material-pale-plaster-01",
        ),
    ),
)
def test_building_plans_cover_all_elevations_and_visible_construction(
    asset_id: str,
    asset_specific: str,
    wall_material: str,
) -> None:
    plan = build_near_geometry_plan(asset_id, FOOTPRINTS[asset_id])
    classes = {row.part_class for row in plan.components}

    assert {
        "foundation",
        "wall",
        "roof-shell",
        "roof-detail",
        "eave",
        "door-opening",
        "window-opening",
        "frame",
        asset_specific,
    } <= classes
    assert plan.covered_elevations == ("east", "north", "south", "west")
    assert plan.detail_counts["roof_tile_columns"] == 24
    assert plan.detail_counts["roof_tile_rows"] == 12
    assert plan.detail_counts["roof_detail_count"] == 576
    assert plan.detail_counts["window_count"] >= 6
    assert plan.detail_counts["door_count"] >= 2
    assert plan.detail_counts["frame_members_per_opening"] == 4
    assert {
        row.material_slot_id
        for row in plan.components
        if row.part_class == "wall"
    } == {wall_material}
    for elevation in plan.covered_elevations:
        assert any(
            row.elevation == elevation
            and row.part_class
            in {
                "door-opening",
                "window-opening",
                "frame",
                "brace",
                "barn-door",
            }
            for row in plan.components
        )


@pytest.mark.parametrize(
    (
        "asset_id",
        "structural_counts",
        "foliage_material",
        "structure_material",
    ),
    (
        (
            "tree_bamboo_01",
            {"trunk-or-culm": 12, "branch": 96, "leaf-card": 3_000},
            "material-bamboo-leaf-01",
            "material-bamboo-stem-01",
        ),
        (
            "tree_broadleaf_01",
            {"trunk-or-culm": 1, "branch": 180, "leaf-card": 3_000},
            "material-broadleaf-canopy-01",
            "material-broadleaf-bark-01",
        ),
        (
            "tree_pine_01",
            {"trunk-or-culm": 1, "branch": 240, "leaf-card": 3_000},
            "material-orchard-leaf-01",
            "material-orchard-bark-01",
        ),
    ),
)
def test_vegetation_plans_have_structure_and_exact_leaf_cards(
    asset_id: str,
    structural_counts: dict[str, int],
    foliage_material: str,
    structure_material: str,
) -> None:
    plan = build_near_geometry_plan(asset_id, FOOTPRINTS[asset_id])

    for part_class, count in structural_counts.items():
        assert sum(
            row.part_class == part_class
            for row in plan.components
        ) == count
    assert all(
        row.primitive == "leaf-card"
        for row in plan.components
        if row.part_class == "leaf-card"
    )
    assert "canopy-blob" not in {
        row.part_class for row in plan.components
    }
    assert {
        row.material_slot_id
        for row in plan.components
        if row.part_class == "leaf-card"
    } == {foliage_material}
    assert {
        row.material_slot_id
        for row in plan.components
        if row.part_class != "leaf-card"
    } == {structure_material}
    if asset_id == "tree_bamboo_01":
        assert any(
            row.part_class == "culm-node"
            for row in plan.components
        )


@pytest.mark.parametrize(
    ("asset_id", "expected"),
    (
        (
            "fence_wood_01",
            {"post": 12, "rail": 22, "brace": 10},
        ),
        (
            "stone_lamp_01",
            {"bevelled-part": 48, "cage-member": 12},
        ),
        (
            "stone_wall_01",
            {"stone-block": 96, "cap-stone": 18},
        ),
    ),
)
def test_prop_plans_have_exact_silhouette_detail(
    asset_id: str,
    expected: dict[str, int],
) -> None:
    plan = build_near_geometry_plan(asset_id, FOOTPRINTS[asset_id])

    for part_class, count in expected.items():
        assert sum(
            row.part_class == part_class
            for row in plan.components
        ) == count


@pytest.mark.parametrize("asset_id", EXPECTED_ASSET_IDS)
def test_near_plans_reserve_real_bevel_topology(asset_id: str) -> None:
    plan = build_near_geometry_plan(asset_id, FOOTPRINTS[asset_id])

    assert all(
        row.planned_triangles == 28
        for row in plan.components
        if row.primitive in {"bevelled-box", "stone-block"}
    )


def test_plan_rejects_unknown_asset_and_registry_footprint_drift() -> None:
    with pytest.raises(NearGeometryPlanError, match="registered"):
        build_near_geometry_plan("house_unknown_01", (8.0, 6.0, 6.5))
    with pytest.raises(NearGeometryPlanError, match="footprint"):
        build_near_geometry_plan(
            "house_wood_01",
            (8.1, 6.0, 6.5),
        )


def test_all_expected_assets_remain_the_exact_recipe_closure() -> None:
    assert tuple(sorted(ASSET_RECIPE_CONTRACTS)) == EXPECTED_ASSET_IDS
    assert tuple(sorted(FOOTPRINTS)) == EXPECTED_ASSET_IDS
