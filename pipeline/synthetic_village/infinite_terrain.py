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
