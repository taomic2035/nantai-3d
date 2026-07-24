"""Render quality metrics — RGB measurement gate for synthetic village renders.

HANDOFF-GLM-007 §5.4: a pure-function measurement module that computes
luminance percentiles, clipped-black/white ratios and background pixel
ratio from PNG bytes.  It does NOT promote geometry/trust level; it only
reports measurable RGB statistics so that before/after renders can be
compared objectively.

Usage::

    from pipeline.synthetic_village.render_quality_metrics import (
        measure_render_quality,
    )

    metrics = measure_render_quality(
        png_bytes,
        background_rgb=(0, 0, 0),
        tolerance=3,
    )

All functions are pure (no bpy dependency) and fail-closed on invalid input.
"""

from __future__ import annotations

import io
import statistics

from pydantic import BaseModel, ConfigDict

#: Rec.601 luminance weights (same as Blender's default RGB→Luma).
_LUM_R: float = 0.299
_LUM_G: float = 0.587
_LUM_B: float = 0.114

#: Clipped-black threshold: all three channels below this = clipped black.
_CLIPPED_BLACK_THRESHOLD: int = 7

#: Clipped-white threshold: all three channels above this = clipped white.
_CLIPPED_WHITE_THRESHOLD: int = 248

#: Default tolerance for background pixel matching (per channel, 0-255).
_DEFAULT_BACKGROUND_TOLERANCE: int = 3


class RenderQualityMetrics(BaseModel):
    """Measured RGB statistics for a single rendered image.

    All ratios are in [0, 1].  All luminance values are in 0-255 (Rec.601).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pixel_count: int
    avg_rgb: tuple[float, float, float]
    lum_p10: float
    lum_p50: float
    lum_p90: float
    clipped_black_ratio: float
    clipped_white_ratio: float
    background_pixel_ratio: float


def measure_render_quality(
    png_bytes: bytes,
    *,
    background_rgb: tuple[int, int, int] | None = None,
    tolerance: int = _DEFAULT_BACKGROUND_TOLERANCE,
) -> RenderQualityMetrics:
    """Measure render quality statistics from PNG bytes.

    Args:
        png_bytes: Raw PNG file bytes.
        background_rgb: Optional (R, G, B) in 0-255 to compute the ratio of
            pixels matching the background colour.  When None,
            background_pixel_ratio is 0.0.
        tolerance: Per-channel tolerance for background matching (default 3).

    Returns:
        RenderQualityMetrics with all fields populated.

    Raises:
        ValueError: If the PNG bytes cannot be decoded or contain no pixels.
    """
    if not png_bytes:
        raise ValueError("decode: empty png bytes")

    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("decode: Pillow is required") from exc

    try:
        img = Image.open(io.BytesIO(png_bytes))
        img = img.convert("RGB")
        img.load()
    except Exception as exc:
        raise ValueError(f"decode: {exc}") from exc

    width, height = img.size
    count = width * height
    if count == 0:
        raise ValueError("decode: image has zero pixels")

    raw = img.tobytes()
    # raw is bytes: R,G,B,R,G,B,... each in 0-255.

    total_r = 0.0
    total_g = 0.0
    total_b = 0.0
    black_count = 0
    white_count = 0
    bg_count = 0
    luminances: list[float] = []

    bg_target = background_rgb
    tol = abs(tolerance)

    for i in range(0, len(raw), 3):
        r = raw[i]
        g = raw[i + 1]
        b = raw[i + 2]

        total_r += r
        total_g += g
        total_b += b

        is_black = (
            r < _CLIPPED_BLACK_THRESHOLD
            and g < _CLIPPED_BLACK_THRESHOLD
            and b < _CLIPPED_BLACK_THRESHOLD
        )
        if is_black:
            black_count += 1

        is_white = (
            r > _CLIPPED_WHITE_THRESHOLD
            and g > _CLIPPED_WHITE_THRESHOLD
            and b > _CLIPPED_WHITE_THRESHOLD
        )
        if is_white:
            white_count += 1

        if bg_target is not None:
            if (
                abs(r - bg_target[0]) <= tol
                and abs(g - bg_target[1]) <= tol
                and abs(b - bg_target[2]) <= tol
            ):
                bg_count += 1

        luminances.append(_LUM_R * r + _LUM_G * g + _LUM_B * b)

    avg_r = total_r / count
    avg_g = total_g / count
    avg_b = total_b / count

    if count >= 10:
        lum_p10 = float(statistics.quantiles(luminances, n=10)[0])
        lum_p90 = float(statistics.quantiles(luminances, n=10)[8])
    else:
        lum_p10 = min(luminances)
        lum_p90 = max(luminances)
    lum_p50 = float(statistics.median(luminances))

    clipped_black_ratio = black_count / count
    clipped_white_ratio = white_count / count
    background_pixel_ratio = bg_count / count if bg_target is not None else 0.0

    return RenderQualityMetrics(
        pixel_count=count,
        avg_rgb=(avg_r, avg_g, avg_b),
        lum_p10=lum_p10,
        lum_p50=lum_p50,
        lum_p90=lum_p90,
        clipped_black_ratio=clipped_black_ratio,
        clipped_white_ratio=clipped_white_ratio,
        background_pixel_ratio=background_pixel_ratio,
    )
