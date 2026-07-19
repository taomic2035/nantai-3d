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
    lower, upper = LOD2_TRIANGLE_BANDS[first.kind]
    assert lower <= first.planned_triangles <= upper
    assert first.planned_triangles == sum(
        row.planned_triangles
        for row in first.components
    )
    component_ids = tuple(row.component_id for row in first.components)
    assert component_ids == tuple(sorted(component_ids))
    assert len(component_ids) == len(set(component_ids))
    assert all(
        math.isfinite(value)
        for row in first.components
        for value in (
            *row.position,
            *row.scale,
            *row.rotation_degrees,
        )
    )
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
    ("asset_id", "structural_counts"),
    (
        (
            "tree_bamboo_01",
            {"trunk-or-culm": 12, "branch": 96, "leaf-card": 3_000},
        ),
        (
            "tree_broadleaf_01",
            {"trunk-or-culm": 1, "branch": 180, "leaf-card": 3_000},
        ),
        (
            "tree_pine_01",
            {"trunk-or-culm": 1, "branch": 240, "leaf-card": 3_000},
        ),
    ),
)
def test_vegetation_plans_have_structure_and_exact_leaf_cards(
    asset_id: str,
    structural_counts: dict[str, int],
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
