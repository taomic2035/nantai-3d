"""Pure deterministic surface-colour runtime shared by host Python and Blender."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

PROFILE_ID = "source-consistent-multiscale-surface-v1"
FIXED_DENOMINATOR = 4096
MIN_MULTIPLIER_Q = round(0.88 * FIXED_DENOMINATOR)
MAX_MULTIPLIER_Q = round(1.10 * FIXED_DENOMINATOR)


def _digest(*parts: object) -> bytes:
    payload = "\0".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).digest()


def _palette_index(
    lattice_x: int,
    lattice_y: int,
    *,
    scene_seed: int,
    source_sha256: str,
) -> int:
    return int.from_bytes(
        _digest(
            PROFILE_ID,
            source_sha256,
            scene_seed,
            lattice_x,
            lattice_y,
        )[:2],
        "big",
    ) % 256


def _smoothstep(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def _quantize(value: float) -> float:
    bounded = min(1.10, max(0.88, value))
    return round(bounded * FIXED_DENOMINATOR) / FIXED_DENOMINATOR


def _validate_inputs(
    palette_q: Sequence[Sequence[int]],
    *,
    x_m: float,
    y_m: float,
    period_m: float,
    scene_seed: int,
    source_sha256: str,
) -> None:
    valid_sha = (
        isinstance(source_sha256, str)
        and len(source_sha256) == 64
        and all(character in "0123456789abcdef" for character in source_sha256)
    )
    if (
        len(palette_q) != 256
        or isinstance(scene_seed, bool)
        or not isinstance(scene_seed, int)
        or not valid_sha
        or not all(math.isfinite(value) for value in (x_m, y_m, period_m))
        or period_m <= 0
    ):
        raise ValueError("surface macro sampler inputs are invalid")
    for row in palette_q:
        if (
            len(row) != 3
            or any(isinstance(value, bool) or not isinstance(value, int) for value in row)
            or any(
                value < MIN_MULTIPLIER_Q or value > MAX_MULTIPLIER_Q
                for value in row
            )
        ):
            raise ValueError("surface macro sampler palette is invalid")


def sample_macro_color(
    palette_q: Sequence[Sequence[int]],
    *,
    x_m: float,
    y_m: float,
    period_m: float,
    scene_seed: int,
    source_sha256: str,
) -> tuple[float, float, float, float]:
    """Sample a source-bound macro multiplier in absolute metre coordinates."""

    _validate_inputs(
        palette_q,
        x_m=x_m,
        y_m=y_m,
        period_m=period_m,
        scene_seed=scene_seed,
        source_sha256=source_sha256,
    )
    lattice_x = math.floor(x_m / period_m)
    lattice_y = math.floor(y_m / period_m)
    u = _smoothstep(x_m / period_m - lattice_x)
    v = _smoothstep(y_m / period_m - lattice_y)
    rows = []
    for delta_y in (0, 1):
        for delta_x in (0, 1):
            rows.append(
                palette_q[
                    _palette_index(
                        lattice_x + delta_x,
                        lattice_y + delta_y,
                        scene_seed=scene_seed,
                        source_sha256=source_sha256,
                    )
                ],
            )
    channels = []
    for channel in range(3):
        low = rows[0][channel] * (1.0 - u) + rows[1][channel] * u
        high = rows[2][channel] * (1.0 - u) + rows[3][channel] * u
        channels.append(
            _quantize(
                (low * (1.0 - v) + high * v) / FIXED_DENOMINATOR,
            ),
        )
    return channels[0], channels[1], channels[2], 1.0
