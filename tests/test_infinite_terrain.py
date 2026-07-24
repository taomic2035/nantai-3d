"""Shared deterministic terrain contract for mesh and Gaussian world paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
