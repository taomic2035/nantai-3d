"""Shared deterministic terrain contract for mesh and Gaussian world paths."""

from __future__ import annotations

import importlib.util
import math
import subprocess
import sys
import types
from pathlib import Path

import pytest

from pipeline.synthetic_village.infinite_terrain import (
    _CREEK_BED_MAX_DEPTH_M,
    TERRAIN_ALGORITHM_ID,
    TERRAIN_MATERIAL_PROFILE_ID,
    TERRAIN_MATERIAL_SLOTS,
    apply_creek_bed_cut,
    creek_bed_depth_m,
    is_in_creek_bed_volume,
    is_in_creek_channel,
    point_to_polyline_distance_m,
    terrain_height_m,
    terrain_macro_tint,
    terrain_material_slot,
)


def _load_blender_creek_math():
    """Load the Blender-local creek-cut math duplicate with bpy mocked out.

    ``build_synthetic_village.py`` imports :mod:`bpy`, :mod:`bmesh` and
    :mod:`mathutils` at module top.  These are unavailable outside the pinned
    Blender runtime, so we inject lightweight stand-ins into ``sys.modules``
    and load the module via ``importlib``.  Only the pure-Python creek-cut
    functions are consumed; no bpy-dependent code is exercised.
    """
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "blender" / "build_synthetic_village.py"
    saved = {}
    stubs = {}
    for name in ("bpy", "bmesh", "mathutils"):
        stubs[name] = types.ModuleType(name)
        saved[name] = sys.modules.get(name)
        sys.modules[name] = stubs[name]
    # mathutils needs Matrix and Vector attributes (referenced at import time).
    stubs["mathutils"].Matrix = type("Matrix", (), {})
    stubs["mathutils"].Vector = type("Vector", (), {})
    try:
        spec = importlib.util.spec_from_file_location(
            "_nantai_blender_creek_math_probe", script
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return module


def test_terrain_identity_is_explicit_and_height_is_meaningful() -> None:
    assert (
        TERRAIN_ALGORITHM_ID
        == "synthetic-multiscale-relief-slope-macro-patch-v2"
    )
    assert TERRAIN_MATERIAL_PROFILE_ID == "slope-macro-patch-v1"

    heights = [
        terrain_height_m(x, y, world_seed=42)
        for x in range(-600, 601, 40)
        for y in range(-600, 601, 40)
    ]

    assert max(heights) - min(heights) > 4.0
    assert min(heights) >= -6.0
    assert max(heights) <= 6.0


def test_same_world_coordinate_is_exact_across_chunk_boundaries() -> None:
    shared_edge = [
        (200.0, float(y), terrain_height_m(200.0, y, world_seed=42))
        for y in range(0, 201, 25)
    ]
    east_chunk_view = [
        (200.0, float(y), terrain_height_m(200.0, y, world_seed=42))
        for y in range(0, 201, 25)
    ]

    assert east_chunk_view == shared_edge
    assert len({row[2] for row in shared_edge}) > 1


def test_macro_tint_is_gentle_deterministic_and_nonconstant() -> None:
    first = [
        terrain_macro_tint(x, y, world_seed=42)
        for x in range(-800, 801, 100)
        for y in range(-800, 801, 100)
    ]
    second = [
        terrain_macro_tint(x, y, world_seed=42)
        for x in range(-800, 801, 100)
        for y in range(-800, 801, 100)
    ]

    assert first == second
    assert min(first) >= 0.90
    assert max(first) <= 1.10
    assert max(first) - min(first) > 0.08


def test_material_zones_use_all_approved_slots_without_chunk_restarts() -> None:
    expected = {
        "material-moss-stone-01",
        "material-packed-earth-01",
        "material-terrace-soil-01",
    }
    assert set(TERRAIN_MATERIAL_SLOTS) == expected

    slots = {
        terrain_material_slot(x, y, world_seed=42)
        for x in range(-600, 601, 25)
        for y in range(-600, 601, 25)
    }
    west_edge = [
        terrain_material_slot(200.0, y, world_seed=42)
        for y in range(0, 201, 25)
    ]
    east_edge = [
        terrain_material_slot(200.0, y, world_seed=42)
        for y in range(0, 201, 25)
    ]

    assert slots == expected
    assert east_edge == west_edge


def test_terrain_samples_are_stable_across_processes() -> None:
    root = Path(__file__).resolve().parent.parent
    code = (
        "import hashlib,json;"
        "from pipeline.synthetic_village.infinite_terrain import "
        "terrain_height_m,terrain_macro_tint,terrain_material_slot;"
        "samples=[(terrain_height_m(x,y,world_seed=7),"
        "terrain_macro_tint(x,y,world_seed=7),"
        "terrain_material_slot(x,y,world_seed=7)) "
        "for x,y in [(-200.0,-25.0),(0.0,0.0),(200.0,425.0)]];"
        "print(hashlib.sha256(json.dumps(samples,separators=(',',':'))"
        ".encode()).hexdigest())"
    )

    def run() -> str:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    assert run() == run()


# ============================================================
# Creek-bed cut: terrain depression along creek polyline
# ============================================================


def test_point_to_polyline_distance_on_segment() -> None:
    """Distance from a point to a 2-point polyline."""
    d = point_to_polyline_distance_m(0.0, 0.0, ((-10.0, 0.0), (10.0, 0.0)))
    assert abs(d - 0.0) < 1e-9  # point is on the segment


def test_point_to_polyline_distance_off_segment() -> None:
    """Distance from a point 5m above the segment."""
    d = point_to_polyline_distance_m(0.0, 5.0, ((-10.0, 0.0), (10.0, 0.0)))
    assert abs(d - 5.0) < 1e-9


def test_point_to_polyline_distance_past_endpoint() -> None:
    """Distance to the nearest endpoint when projection is past segment end."""
    d = point_to_polyline_distance_m(15.0, 4.0, ((-10.0, 0.0), (10.0, 0.0)))
    assert abs(d - (5.0**2 + 4.0**2) ** 0.5) < 1e-9


def test_point_to_polyline_distance_multi_segment() -> None:
    """Nearest distance across a multi-segment polyline."""
    d = point_to_polyline_distance_m(
        0.0, 3.0, ((-10.0, 0.0), (0.0, 0.0), (10.0, 0.0)))
    assert abs(d - 3.0) < 1e-9


def test_creek_bed_depth_zero_outside_bank_margin() -> None:
    """No cut beyond bank margin."""
    assert creek_bed_depth_m(distance_m=10.0, creek_half_width_m=4.0,
                             bank_margin_m=2.0) == 0.0


def test_creek_bed_depth_max_at_center() -> None:
    """Deepest cut at creek center."""
    d = creek_bed_depth_m(distance_m=0.0, creek_half_width_m=4.0,
                          bank_margin_m=2.0)
    assert d > 0.0


def test_creek_bed_depth_decreases_with_distance() -> None:
    """Cut is constant within creek half-width, then tapers to zero."""
    d0 = creek_bed_depth_m(0.0, 4.0, 2.0)
    d2 = creek_bed_depth_m(2.0, 4.0, 2.0)
    d4 = creek_bed_depth_m(4.0, 4.0, 2.0)   # creek edge, t=1.0 (still max)
    d45 = creek_bed_depth_m(4.5, 4.0, 2.0)  # mid-taper, t=0.75
    d5 = creek_bed_depth_m(5.0, 4.0, 2.0)  # mid-taper, t=0.5
    d6 = creek_bed_depth_m(6.0, 4.0, 2.0)  # bank edge, t=0.0
    # Within creek half-width: constant max depth.
    assert d0 == d2 == d4 == _CREEK_BED_MAX_DEPTH_M
    # Taper: mid < max, decreasing, bank edge == 0.
    assert d45 < d4
    assert d5 < d45
    assert d5 > 0.0
    assert d6 == 0.0


def test_apply_creek_bed_cut_lowers_terrain_on_creek() -> None:
    """Terrain on creek path is lower than unmodified terrain."""
    creek_points = ((-100.0, 0.0), (100.0, 0.0))
    base = terrain_height_m(0.0, 0.0, world_seed=42)
    cut = apply_creek_bed_cut(base, 0.0, 0.0, creek_points,
                              creek_half_width_m=4.0, bank_margin_m=2.0)
    assert cut < base


def test_apply_creek_bed_cut_preserves_terrain_far_from_creek() -> None:
    """Terrain far from creek is unchanged."""
    creek_points = ((-100.0, 0.0), (100.0, 0.0))
    base = terrain_height_m(0.0, 200.0, world_seed=42)
    cut = apply_creek_bed_cut(base, 0.0, 200.0, creek_points,
                              creek_half_width_m=4.0, bank_margin_m=2.0)
    assert cut == base


def test_apply_creek_bed_cut_is_deterministic() -> None:
    """Same input produces same output."""
    creek_points = ((-100.0, 0.0), (100.0, 0.0))
    base = terrain_height_m(0.0, 0.0, world_seed=42)
    c1 = apply_creek_bed_cut(base, 0.0, 0.0, creek_points, 4.0, 2.0)
    c2 = apply_creek_bed_cut(base, 0.0, 0.0, creek_points, 4.0, 2.0)
    assert c1 == c2


# ============================================================
# Creek channel and creek-bed volume checks
# ============================================================


def test_is_in_creek_channel_true_at_center() -> None:
    """Point on creek centreline is in the channel."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    assert is_in_creek_channel(0.0, 0.0, creek, creek_half_width_m=4.0)


def test_is_in_creek_channel_true_within_half_width() -> None:
    """Point 3m from centre (within 4m half-width) is in the channel."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    assert is_in_creek_channel(0.0, 3.0, creek, creek_half_width_m=4.0)


def test_is_in_creek_channel_false_at_half_width() -> None:
    """Point exactly at half-width is NOT in the channel (strict <)."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    assert not is_in_creek_channel(0.0, 4.0, creek, creek_half_width_m=4.0)


def test_is_in_creek_channel_false_outside() -> None:
    """Point 10m from centre is not in the channel."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    assert not is_in_creek_channel(0.0, 10.0, creek, creek_half_width_m=4.0)


def test_is_in_creek_bed_volume_true_below_bank() -> None:
    """Point between cut floor and bank top is in the volume."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    base_z = 10.0
    # At centre: cut_depth = _CREEK_BED_MAX_DEPTH_M = 1.2
    # cut_floor = 10.0 - 1.2 = 8.8
    # Point at z=9.0 is between 8.8 and 10.0
    assert is_in_creek_bed_volume(
        0.0, 0.0, 9.0, creek, 4.0, 2.0, base_z
    )


def test_is_in_creek_bed_volume_true_at_floor() -> None:
    """Point exactly at cut floor is in the volume (inclusive)."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    base_z = 10.0
    floor_z = base_z - _CREEK_BED_MAX_DEPTH_M
    assert is_in_creek_bed_volume(
        0.0, 0.0, floor_z, creek, 4.0, 2.0, base_z
    )


def test_is_in_creek_bed_volume_false_at_bank_top() -> None:
    """Point at bank surface is NOT in the volume (exclusive upper)."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    base_z = 10.0
    assert not is_in_creek_bed_volume(
        0.0, 0.0, base_z, creek, 4.0, 2.0, base_z
    )


def test_is_in_creek_bed_volume_false_below_floor() -> None:
    """Point below the cut floor is NOT in the volume."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    base_z = 10.0
    floor_z = base_z - _CREEK_BED_MAX_DEPTH_M
    assert not is_in_creek_bed_volume(
        0.0, 0.0, floor_z - 0.1, creek, 4.0, 2.0, base_z
    )


def test_is_in_creek_bed_volume_false_outside_bank() -> None:
    """Point outside bank margin is not in the volume regardless of z."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    base_z = 10.0
    assert not is_in_creek_bed_volume(
        0.0, 20.0, 9.0, creek, 4.0, 2.0, base_z
    )


# ============================================================
# P0-1: analytic creek-cut math == Blender-local duplicate
# (HANDOFF-GLM-007 §3.1)
# ============================================================


@pytest.fixture(scope="module")
def blender_creek_math():
    return _load_blender_creek_math()


def test_blender_local_creek_depth_matches_analytic_at_key_points(
    blender_creek_math,
) -> None:
    """Blender-local ``_creek_bed_depth_m`` == analytic ``creek_bed_depth_m``
    at centreline, bank edge, taper midpoint, endpoints and degenerate
    segments (HANDOFF-GLM-007 §3.1)."""
    hw, bm = 4.0, 2.0
    cases = [
        0.0,   # centreline (max depth)
        2.0,   # within half-width (still max)
        4.0,   # creek edge, taper start (t == 1.0)
        4.5,   # taper midpoint
        5.0,   # taper midpoint
        6.0,   # bank edge (zero)
        10.0,  # well beyond
        -1.0,  # negative distance (treated as within half-width)
    ]
    for dist in cases:
        analytic = creek_bed_depth_m(dist, hw, bm)
        blender = blender_creek_math._creek_bed_depth_m(dist, hw, bm)
        assert analytic == blender, (
            f"creek depth mismatch at distance={dist}: "
            f"analytic={analytic} blender={blender}"
        )


def test_blender_local_polyline_distance_matches_analytic(
    blender_creek_math,
) -> None:
    """Blender-local ``_point_to_polyline_distance_m`` == analytic
    ``point_to_polyline_distance_m`` on, off, past endpoint, multi-segment
    and degenerate-segment polylines (HANDOFF-GLM-007 §3.1)."""
    polyline = ((-10.0, 0.0), (0.0, 0.0), (10.0, 0.0))
    degenerate = ((0.0, 0.0), (0.0, 0.0))  # zero-length segment
    queries = [
        (0.0, 0.0, polyline),    # on segment
        (0.0, 5.0, polyline),    # off segment
        (15.0, 4.0, polyline),  # past endpoint
        (3.0, 3.0, polyline),    # near middle segment
        (0.0, 0.0, degenerate),  # degenerate segment
        (5.0, 0.0, degenerate),
    ]
    for x, y, pts in queries:
        analytic = point_to_polyline_distance_m(x, y, pts)
        blender = blender_creek_math._point_to_polyline_distance_m(x, y, pts)
        assert analytic == blender, (
            f"polyline distance mismatch at ({x},{y}): "
            f"analytic={analytic} blender={blender}"
        )


def test_blender_local_creek_cut_depth_matches_apply(
    blender_creek_math,
) -> None:
    """Blender-local ``_creek_bed_cut_depth`` == analytic ``creek_bed_depth_m``
    composition for the same query point.  The base terrain functions differ
    (Blender uses ``extent`` dict, infinite_terrain uses ``world_seed``), so
    only the cut-depth component is compared (HANDOFF-GLM-007 §3.1)."""
    creek = ((-100.0, 0.0), (100.0, 0.0))
    creek_polylines = (
        {
            "points_xy": creek,
            "half_width_m": 4.0,
            "bank_margin_m": 2.0,
        },
    )
    for x, y in [(0.0, 0.0), (0.0, 3.0), (0.0, 5.0), (0.0, 10.0), (50.0, 0.0)]:
        analytic_dist = point_to_polyline_distance_m(x, y, creek)
        analytic_depth = creek_bed_depth_m(analytic_dist, 4.0, 2.0)
        blender_depth = blender_creek_math._creek_bed_cut_depth(
            x, y, creek_polylines
        )
        assert abs(analytic_depth - blender_depth) < 1e-9, (
            f"cut depth mismatch at ({x},{y}): "
            f"analytic={analytic_depth} blender={blender_depth}"
        )


# ============================================================
# P0-2: parameter fail-closed
# (HANDOFF-GLM-007 §3.2)
# ============================================================


def test_creek_bed_depth_rejects_non_finite_distance() -> None:
    with pytest.raises(ValueError, match="distance.*finite"):
        creek_bed_depth_m(float("nan"), 4.0, 2.0)
    with pytest.raises(ValueError, match="distance.*finite"):
        creek_bed_depth_m(float("inf"), 4.0, 2.0)


def test_creek_bed_depth_rejects_nonpositive_half_width() -> None:
    with pytest.raises(ValueError, match="half_width.*positive"):
        creek_bed_depth_m(0.0, 0.0, 2.0)
    with pytest.raises(ValueError, match="half_width.*positive"):
        creek_bed_depth_m(0.0, -4.0, 2.0)


def test_creek_bed_depth_rejects_nonpositive_bank_margin() -> None:
    with pytest.raises(ValueError, match="bank_margin.*positive"):
        creek_bed_depth_m(0.0, 4.0, 0.0)
    with pytest.raises(ValueError, match="bank_margin.*positive"):
        creek_bed_depth_m(0.0, 4.0, -2.0)


def test_point_to_polyline_rejects_non_finite_coords() -> None:
    creek = ((-10.0, 0.0), (10.0, 0.0))
    with pytest.raises(ValueError, match="coordinates.*finite"):
        point_to_polyline_distance_m(float("nan"), 0.0, creek)
    with pytest.raises(ValueError, match="coordinates.*finite"):
        point_to_polyline_distance_m(0.0, float("inf"), creek)


def test_point_to_polyline_rejects_fewer_than_two_points() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        point_to_polyline_distance_m(0.0, 0.0, ())
    with pytest.raises(ValueError, match="at least 2"):
        point_to_polyline_distance_m(0.0, 0.0, ((0.0, 0.0),))


def test_blender_local_creek_math_rejects_same_invalid_inputs(
    blender_creek_math,
) -> None:
    """Blender-local duplicate must fail-closed on the same invalid inputs
    as the analytic source (HANDOFF-GLM-007 §3.2)."""
    with pytest.raises(ValueError, match="distance.*finite"):
        blender_creek_math._creek_bed_depth_m(float("nan"), 4.0, 2.0)
    with pytest.raises(ValueError, match="half_width.*positive"):
        blender_creek_math._creek_bed_depth_m(0.0, 0.0, 2.0)
    with pytest.raises(ValueError, match="bank_margin.*positive"):
        blender_creek_math._creek_bed_depth_m(0.0, 4.0, 0.0)
    with pytest.raises(ValueError, match="coordinates.*finite"):
        blender_creek_math._point_to_polyline_distance_m(
            float("inf"), 0.0, ((0.0, 0.0), (1.0, 0.0))
        )
    with pytest.raises(ValueError, match="at least 2"):
        blender_creek_math._point_to_polyline_distance_m(0.0, 0.0, ())


# ============================================================
# P0-3: building skirts and bridge foundations produce no
# inverted or zero-height boxes (HANDOFF-GLM-007 §3.3)
# ============================================================


def test_building_skirt_box_returns_none_for_template_path(
    blender_creek_math,
) -> None:
    """Mesh-asset template builds (no transform/extent) skip the skirt."""
    assert blender_creek_math._building_skirt_box(0.0, 6.0, 5.0, None, None) is None


def test_building_skirt_box_returns_none_when_terrain_above_base(
    blender_creek_math,
) -> None:
    """No skirt when terrain is at or above the platform base."""
    extent = {"width_m": 700.0, "depth_m": 500.0, "relief_m": 120.0}
    # terrain minimum is 0 (at y=-depth/2), so base_z=-1 is below all terrain.
    transform = {"x_m": 0.0, "y_m": 0.0, "yaw_deg": 0.0}
    assert blender_creek_math._building_skirt_box(
        -1.0, 6.0, 5.0, transform, extent
    ) is None


def test_building_skirt_box_height_is_positive_and_center_between(
    blender_creek_math,
) -> None:
    """Skirt height > 0 and center_z between min_terrain and base_z."""
    from pipeline.synthetic_village.scene_plan import build_scene_plan

    scene = build_scene_plan()
    extent = {
        "width_m": scene.extent.width_m,
        "depth_m": scene.extent.depth_m,
        "relief_m": scene.extent.relief_m,
    }
    buildings = [o for o in scene.objects if o.semantic_class == "building"]
    assert buildings, "scene must contain buildings"
    for obj in buildings:
        transform = {
            "x_m": obj.transform.x_m,
            "y_m": obj.transform.y_m,
            "yaw_deg": obj.transform.yaw_deg,
        }
        base_z = obj.base_z_m
        width = obj.dimensions.width_m
        depth = obj.dimensions.depth_m
        box = blender_creek_math._building_skirt_box(
            base_z, width, depth, transform, extent
        )
        if box is None:
            continue
        center, size = box
        skirt_height = size[2]
        assert skirt_height > 1e-6, (
            f"building {obj.object_id} skirt height must be positive, "
            f"got {skirt_height}"
        )
        # center_z must lie between min_terrain and base_z (no inversion).
        center_z = center[2]
        assert center_z <= base_z + 1e-9, (
            f"building {obj.object_id} skirt center above base_z"
        )
        assert center_z >= base_z - skirt_height - 1e-9, (
            f"building {obj.object_id} skirt center below terrain floor"
        )


def test_bridge_foundation_box_height_is_positive_and_center_between(
    blender_creek_math,
) -> None:
    """Bridge foundation height > 0 and center_z between terrain and pier."""
    from pipeline.synthetic_village.scene_plan import build_scene_plan

    scene = build_scene_plan()
    extent = {
        "width_m": scene.extent.width_m,
        "depth_m": scene.extent.depth_m,
        "relief_m": scene.extent.relief_m,
    }
    creek_polylines = blender_creek_math._extract_creek_polylines(
        {"scene_plan": {"objects": [
            {
                "semantic_class": o.semantic_class,
                "polyline": (
                    {"points": [{"x_m": p.x_m, "y_m": p.y_m} for p in o.polyline.points],
                     "width_m": o.polyline.width_m}
                    if o.polyline else None
                ),
            }
            for o in scene.objects
        ]}}
    )
    bridges = [o for o in scene.objects if o.semantic_class == "bridge"]
    assert bridges, "scene must contain bridges"
    for obj in bridges:
        transform = {
            "x_m": obj.transform.x_m,
            "y_m": obj.transform.y_m,
            "yaw_deg": obj.transform.yaw_deg,
        }
        center_z = obj.transform.z_m
        width = obj.dimensions.width_m
        depth = obj.dimensions.depth_m
        height = obj.dimensions.height_m
        pier_bottom_z = center_z - height * 0.82
        yaw_rad = math.radians(obj.transform.yaw_deg)
        cos_yaw, sin_yaw = math.cos(yaw_rad), math.sin(yaw_rad)
        for x_value in (-width / 2 + 1.0, 0.0, width / 2 - 1.0):
            box = blender_creek_math._bridge_foundation_box(
                pier_bottom_z, x_value, depth, cos_yaw, sin_yaw,
                transform, extent, creek_polylines,
            )
            if box is None:
                continue
            c, size = box
            foundation_height = size[2]
            assert foundation_height > 1e-6, (
                f"bridge {obj.object_id} pier x={x_value} "
                f"foundation height must be positive, got {foundation_height}"
            )
            # center_z must lie between terrain and pier_bottom (no inversion).
            center_z_box = c[2]
            assert center_z_box <= pier_bottom_z + 1e-9, (
                f"bridge {obj.object_id} foundation center above pier bottom"
            )
            assert center_z_box >= pier_bottom_z - foundation_height - 1e-9, (
                f"bridge {obj.object_id} foundation center below terrain floor"
            )


# ============================================================
# P0-6: contact gap measurement on the canonical scene
# (HANDOFF-GLM-007 §3.6: measured contact gaps, not screenshots)
# ============================================================


def test_contact_gap_measurement_on_canonical_scene(
    blender_creek_math,
) -> None:
    """Measure how much floating-platform contact gap the new skirt and
    foundation boxes close on the canonical scene plan.

    This is the analytical half of P0-6 evidence: it counts the contact
    boxes the Blender builder will emit, the maximum gap height closed
    and the total fill volume.  The real Blender smoke build is exercised
    by ``test_runtime_builds_and_reports_the_complete_canary`` in
    ``tests/test_synthetic_village_blender_runtime.py``.
    """
    from pipeline.synthetic_village.scene_plan import build_scene_plan

    scene = build_scene_plan()
    extent = {
        "width_m": scene.extent.width_m,
        "depth_m": scene.extent.depth_m,
        "relief_m": scene.extent.relief_m,
    }
    creek_polylines = blender_creek_math._extract_creek_polylines(
        {"scene_plan": {"objects": [
            {
                "semantic_class": o.semantic_class,
                "polyline": (
                    {"points": [{"x_m": p.x_m, "y_m": p.y_m} for p in o.polyline.points],
                     "width_m": o.polyline.width_m}
                    if o.polyline else None
                ),
            }
            for o in scene.objects
        ]}}
    )

    building_skirt_count = 0
    building_skirt_max_height_m = 0.0
    building_skirt_total_volume_m3 = 0.0
    buildings_total = 0
    for obj in scene.objects:
        if obj.semantic_class != "building":
            continue
        buildings_total += 1
        transform = {
            "x_m": obj.transform.x_m,
            "y_m": obj.transform.y_m,
            "yaw_deg": obj.transform.yaw_deg,
        }
        box = blender_creek_math._building_skirt_box(
            obj.base_z_m,
            obj.dimensions.width_m,
            obj.dimensions.depth_m,
            transform,
            extent,
        )
        if box is None:
            continue
        building_skirt_count += 1
        _, size = box
        height = size[2]
        volume = size[0] * size[1] * size[2]
        building_skirt_max_height_m = max(building_skirt_max_height_m, height)
        building_skirt_total_volume_m3 += volume

    bridge_foundation_count = 0
    bridge_foundation_max_height_m = 0.0
    bridge_foundation_total_volume_m3 = 0.0
    bridge_piers_total = 0
    for obj in scene.objects:
        if obj.semantic_class != "bridge":
            continue
        transform = {
            "x_m": obj.transform.x_m,
            "y_m": obj.transform.y_m,
            "yaw_deg": obj.transform.yaw_deg,
        }
        center_z = obj.transform.z_m
        width = obj.dimensions.width_m
        depth = obj.dimensions.depth_m
        height = obj.dimensions.height_m
        pier_bottom_z = center_z - height * 0.82
        yaw_rad = math.radians(obj.transform.yaw_deg)
        cos_yaw, sin_yaw = math.cos(yaw_rad), math.sin(yaw_rad)
        for x_value in (-width / 2 + 1.0, 0.0, width / 2 - 1.0):
            bridge_piers_total += 1
            box = blender_creek_math._bridge_foundation_box(
                pier_bottom_z, x_value, depth, cos_yaw, sin_yaw,
                transform, extent, creek_polylines,
            )
            if box is None:
                continue
            bridge_foundation_count += 1
            _, size = box
            h = size[2]
            volume = size[0] * size[1] * size[2]
            bridge_foundation_max_height_m = max(bridge_foundation_max_height_m, h)
            bridge_foundation_total_volume_m3 += volume

    # The canonical scene has sloped terrain; without these boxes at least
    # one building platform and one bridge pier would float.  This is the
    # measured contact gap that the new geometry closes.
    assert buildings_total > 0, "scene must contain buildings"
    assert bridge_piers_total > 0, "scene must contain bridge piers"
    assert building_skirt_count > 0, (
        "expected at least one building skirt on the canonical scene plan; "
        "terrain relief should produce at least one floating platform"
    )
    assert bridge_foundation_count > 0, (
        "expected at least one bridge pier foundation on the canonical scene "
        "plan; the creek-bed cut should produce at least one floating pier"
    )
    assert building_skirt_max_height_m > 0.05, (
        f"building skirt max height {building_skirt_max_height_m:.3f} m is too "
        f"small to be meaningful contact gap closure"
    )
    assert bridge_foundation_max_height_m > 0.05, (
        f"bridge foundation max height {bridge_foundation_max_height_m:.3f} m "
        f"is too small to be meaningful contact gap closure"
    )
    # The measurement is intentionally reported via the assertion messages
    # so a failure surfaces the numbers without a separate print().
    total_volume = (
        building_skirt_total_volume_m3 + bridge_foundation_total_volume_m3
    )
    assert total_volume > 0.1, (
        f"total contact fill volume {total_volume:.3f} m^3 is too small; "
        f"skirts={building_skirt_count} (max_h={building_skirt_max_height_m:.3f} m, "
        f"vol={building_skirt_total_volume_m3:.3f} m^3), "
        f"foundations={bridge_foundation_count} (max_h={bridge_foundation_max_height_m:.3f} m, "
        f"vol={bridge_foundation_total_volume_m3:.3f} m^3)"
    )
