"""Deterministic synthetic relief shared by mesh and Gaussian world output.

This module describes synthetic presentation geometry only.  Its output does
not add measured coordinates or upgrade reconstruction provenance.
"""

from __future__ import annotations

import math

TERRAIN_ALGORITHM_ID = "synthetic-multiscale-relief-slope-macro-patch-v2"
TERRAIN_MATERIAL_PROFILE_ID = "slope-macro-patch-v1"
TERRAIN_MATERIAL_SLOTS = (
    "material-moss-stone-01",
    "material-packed-earth-01",
    "material-terrace-soil-01",
)

_MASK_64 = (1 << 64) - 1
_SALT_HEIGHT_MACRO = 0xE17A1465
_SALT_HEIGHT_DETAIL = 0xA5B35705
_SALT_TINT = 0xC2B2AE35


def _mix64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & _MASK_64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK_64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK_64
    return value ^ (value >> 31)


def _lattice_value(ix: int, iy: int, world_seed: int, salt: int) -> float:
    value = int(world_seed) & _MASK_64
    value ^= ((ix & _MASK_64) * 0xD6E8FEB86659FD93) & _MASK_64
    value ^= ((iy & _MASK_64) * 0xA5A3564E27F8862F) & _MASK_64
    value ^= salt
    unit = (_mix64(value) >> 11) / float((1 << 53) - 1)
    return unit * 2.0 - 1.0


def _smoothstep(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def _value_noise(
    world_x: float,
    world_y: float,
    *,
    world_seed: int,
    scale_m: float,
    salt: int,
) -> float:
    scaled_x = float(world_x) / scale_m
    scaled_y = float(world_y) / scale_m
    x0 = math.floor(scaled_x)
    y0 = math.floor(scaled_y)
    tx = _smoothstep(scaled_x - x0)
    ty = _smoothstep(scaled_y - y0)

    southwest = _lattice_value(x0, y0, world_seed, salt)
    southeast = _lattice_value(x0 + 1, y0, world_seed, salt)
    northwest = _lattice_value(x0, y0 + 1, world_seed, salt)
    northeast = _lattice_value(x0 + 1, y0 + 1, world_seed, salt)
    south = southwest + (southeast - southwest) * tx
    north = northwest + (northeast - northwest) * tx
    return south + (north - south) * ty


def terrain_height_m(
    world_x: float,
    world_y: float,
    *,
    world_seed: int,
) -> float:
    """Return bounded synthetic terrain height in metres at a world position."""

    macro = _value_noise(
        world_x,
        world_y,
        world_seed=world_seed,
        scale_m=320.0,
        salt=_SALT_HEIGHT_MACRO,
    )
    detail = _value_noise(
        world_x,
        world_y,
        world_seed=world_seed,
        scale_m=80.0,
        salt=_SALT_HEIGHT_DETAIL,
    )
    return macro * 4.5 + detail * 1.2


def terrain_macro_tint(
    world_x: float,
    world_y: float,
    *,
    world_seed: int,
) -> float:
    """Return a gentle multiplier used to break large-area texture repetition."""

    noise = _value_noise(
        world_x,
        world_y,
        world_seed=world_seed,
        scale_m=420.0,
        salt=_SALT_TINT,
    )
    return 1.0 + noise * 0.1


def terrain_material_slot(
    world_x: float,
    world_y: float,
    *,
    world_seed: int,
) -> str:
    """Port the approved Blender terrain zoning to absolute world space."""

    x = float(world_x)
    y = float(world_y)
    gradient_x = (
        terrain_height_m(x + 1.0, y, world_seed=world_seed)
        - terrain_height_m(x - 1.0, y, world_seed=world_seed)
    ) / 2.0
    gradient_y = (
        terrain_height_m(x, y + 1.0, world_seed=world_seed)
        - terrain_height_m(x, y - 1.0, world_seed=world_seed)
    ) / 2.0
    normal_z = round(
        1.0 / math.sqrt(1.0 + gradient_x**2 + gradient_y**2),
        6,
    )
    macro_patch = round(
        math.sin(x * 0.031)
        + 0.72 * math.cos(y * 0.027)
        + 0.38 * math.sin((x + y) * 0.017),
        6,
    )
    if normal_z < 0.965 or macro_patch > 0.92:
        return "material-moss-stone-01"
    if macro_patch < -0.28:
        return "material-packed-earth-01"
    return "material-terrace-soil-01"


# --------------------------------------------------------------------------- #
# Creek-bed cut: deterministic terrain depression along a creek polyline.
#
# The base ``terrain_height_m`` function is pure noise and does not know
# where the creek flows.  Without a cut, the creek water ribbon lies on top
# of an unmodified terrain surface, producing the "flat ribbon through
# terrain" defect documented in HANDOFF-GLM-005 §P2 #2.
#
# ``apply_creek_bed_cut`` lowers the terrain within the creek corridor so
# that ``water_z <= bank_z <= terrain_z`` holds at every cross-section,
# matching the ``CreekCrossSectionSpec`` invariant in ``environment_module``.
#
# This function is pure and deterministic: same (x, y, creek_points) always
# produces the same cut depth.  It does **not** upgrade provenance — the
# output is still ``synthetic / preview-only``.
# --------------------------------------------------------------------------- #

_CREEK_BED_MAX_DEPTH_M = 1.2  #: deepest cut below base terrain (water_z - 0.4)


def point_to_polyline_distance_m(
    x: float,
    y: float,
    points: tuple[tuple[float, float], ...],
) -> float:
    """Return the minimum Euclidean distance from (x, y) to a polyline."""

    if len(points) < 2:
        raise ValueError("polyline must have at least 2 points")
    best = float("inf")
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        dx = x1 - x0
        dy = y1 - y0
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-18:
            # Degenerate segment — treat as a point.
            d = math.hypot(x - x0, y - y0)
        else:
            t = ((x - x0) * dx + (y - y0) * dy) / seg_len_sq
            t = max(0.0, min(1.0, t))
            proj_x = x0 + t * dx
            proj_y = y0 + t * dy
            d = math.hypot(x - proj_x, y - proj_y)
        if d < best:
            best = d
    return best


def creek_bed_depth_m(
    distance_m: float,
    creek_half_width_m: float,
    bank_margin_m: float,
) -> float:
    """Return how many metres to lower terrain at a given distance from creek centre.

    The cut is deepest at the creek centre (``_CREEK_BED_MAX_DEPTH_M``),
    stays constant within the creek half-width, then linearly tapers to zero
    across ``bank_margin_m``.  Beyond ``creek_half_width + bank_margin`` the
    cut is zero.
    """

    bank_edge = creek_half_width_m + bank_margin_m
    if distance_m >= bank_edge:
        return 0.0
    if distance_m < creek_half_width_m:
        return _CREEK_BED_MAX_DEPTH_M
    # Linear taper from creek_half_width to bank_edge.
    t = (bank_edge - distance_m) / bank_margin_m
    return _CREEK_BED_MAX_DEPTH_M * t


def apply_creek_bed_cut(
    base_height_m: float,
    x: float,
    y: float,
    creek_polyline_xy: tuple[tuple[float, float], ...],
    creek_half_width_m: float,
    bank_margin_m: float,
) -> float:
    """Return terrain height with creek-bed cut applied.

    Parameters
    ----------
    base_height_m:
        The unmodified terrain height at (x, y) from ``terrain_height_m``.
    x, y:
        World coordinates of the sample point.
    creek_polyline_xy:
        Sequence of (x, y) tuples tracing the creek centre line.
    creek_half_width_m:
        Half the creek width (e.g. 4.0 for an 8 m wide creek).
    bank_margin_m:
        How far beyond the creek banks the cut tapers to zero.
    """

    distance = point_to_polyline_distance_m(x, y, creek_polyline_xy)
    depth = creek_bed_depth_m(distance, creek_half_width_m, bank_margin_m)
    if depth <= 0.0:
        return base_height_m
    return base_height_m - depth


def is_in_creek_channel(
    x: float,
    y: float,
    creek_polyline_xy: tuple[tuple[float, float], ...],
    creek_half_width_m: float,
) -> bool:
    """Return True if (x, y) is within the creek water-surface channel.

    This is a 2D footprint check: the point's horizontal distance to the
    nearest creek segment is less than ``creek_half_width_m``.  Use this to
    reject camera placements or walkable nodes that would sit on the water
    surface.
    """

    distance = point_to_polyline_distance_m(x, y, creek_polyline_xy)
    return distance < creek_half_width_m


def is_in_creek_bed_volume(
    x: float,
    y: float,
    z: float,
    creek_polyline_xy: tuple[tuple[float, float], ...],
    creek_half_width_m: float,
    bank_margin_m: float,
    base_terrain_z: float,
) -> bool:
    """Return True if (x, y, z) is inside the creek-bed cut volume.

    The creek-bed volume is the region between the cut floor
    (``base_terrain_z - cut_depth``) and the bank top (``base_terrain_z``)
    where the cut depth is positive.  A point at the bank surface
    (``z == base_terrain_z``) is NOT inside the volume; a point below the
    bank surface but above the cut floor IS inside.
    """

    distance = point_to_polyline_distance_m(x, y, creek_polyline_xy)
    bank_edge = creek_half_width_m + bank_margin_m
    if distance >= bank_edge:
        return False
    cut_depth = creek_bed_depth_m(distance, creek_half_width_m, bank_margin_m)
    if cut_depth <= 0.0:
        return False
    cut_floor_z = base_terrain_z - cut_depth
    return z >= cut_floor_z and z < base_terrain_z
