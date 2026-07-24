"""Tests for render_quality_metrics — RGB measurement gate (HANDOFF-GLM-007 §5.4)."""

from __future__ import annotations

import math

import pytest

from pipeline.synthetic_village.render_quality_metrics import (
    measure_render_quality,
)


def _solid_rgb(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Return PNG bytes for a solid-colour image."""
    from PIL import Image

    img = Image.new("RGB", (width, height), rgb)
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gradient_rgb(width: int, height: int) -> bytes:
    """Return PNG bytes for a vertical gradient (black at top, white at bottom)."""
    from PIL import Image

    img = Image.new("RGB", (width, height))
    for y in range(height):
        v = int(255 * y / max(1, height - 1))
        for x in range(width):
            img.putpixel((x, y), (v, v, v))
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# measure_render_quality
# ---------------------------------------------------------------------------


class TestMeasureRenderQuality:
    def test_solid_midgrey_returns_expected_avg(self) -> None:
        png = _solid_rgb(4, 4, (128, 128, 128))
        m = measure_render_quality(png)
        assert m.pixel_count == 16
        assert m.avg_rgb == (128.0, 128.0, 128.0)

    def test_solid_black_reports_100_percent_clipped_black(self) -> None:
        png = _solid_rgb(2, 2, (0, 0, 0))
        m = measure_render_quality(png)
        assert m.clipped_black_ratio == 1.0
        assert m.clipped_white_ratio == 0.0

    def test_solid_white_reports_100_percent_clipped_white(self) -> None:
        png = _solid_rgb(2, 2, (255, 255, 255))
        m = measure_render_quality(png)
        assert m.clipped_white_ratio == 1.0
        assert m.clipped_black_ratio == 0.0

    def test_gradient_luminance_percentiles_are_ordered(self) -> None:
        png = _gradient_rgb(4, 100)
        m = measure_render_quality(png)
        assert m.lum_p10 <= m.lum_p50 <= m.lum_p90

    def test_gradient_luminance_spans_near_full_range(self) -> None:
        png = _gradient_rgb(4, 100)
        m = measure_render_quality(png)
        assert m.lum_p10 < 30.0
        assert m.lum_p90 > 225.0

    def test_background_ratio_for_solid_colour_near_target(self) -> None:
        """A solid image whose colour matches the background target → ratio 1.0."""
        png = _solid_rgb(4, 4, (128, 128, 128))
        m = measure_render_quality(png, background_rgb=(128, 128, 128), tolerance=3)
        assert m.background_pixel_ratio == 1.0

    def test_background_ratio_zero_when_no_match(self) -> None:
        png = _solid_rgb(4, 4, (128, 128, 128))
        m = measure_render_quality(png, background_rgb=(0, 0, 0), tolerance=3)
        assert m.background_pixel_ratio == 0.0

    def test_invalid_png_bytes_fail_closed(self) -> None:
        with pytest.raises(ValueError, match="decode"):
            measure_render_quality(b"not-a-png")

    def test_empty_png_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="decode"):
            measure_render_quality(b"")

    def test_pixel_count_matches_dimensions(self) -> None:
        png = _solid_rgb(7, 3, (50, 100, 150))
        m = measure_render_quality(png)
        assert m.pixel_count == 21

    def test_avg_rgb_preserves_channel_order(self) -> None:
        png = _solid_rgb(2, 2, (50, 100, 150))
        m = measure_render_quality(png)
        assert m.avg_rgb == (50.0, 100.0, 150.0)

    def test_luminance_uses_rec601_weights(self) -> None:
        """Luminance must use Rec.601: 0.299R + 0.587G + 0.114B."""
        png = _solid_rgb(1, 1, (100, 200, 50))
        m = measure_render_quality(png)
        expected = 0.299 * 100 + 0.587 * 200 + 0.114 * 50
        assert math.isclose(m.lum_p50, expected, abs_tol=0.5)


class TestRenderQualityMetricsModel:
    def test_all_fields_are_finite_for_valid_image(self) -> None:
        png = _solid_rgb(4, 4, (128, 128, 128))
        m = measure_render_quality(png)
        assert math.isfinite(m.avg_rgb[0])
        assert math.isfinite(m.avg_rgb[1])
        assert math.isfinite(m.avg_rgb[2])
        assert math.isfinite(m.lum_p10)
        assert math.isfinite(m.lum_p50)
        assert math.isfinite(m.lum_p90)
        assert math.isfinite(m.clipped_black_ratio)
        assert math.isfinite(m.clipped_white_ratio)
        assert math.isfinite(m.background_pixel_ratio)

    def test_ratios_are_in_unit_range(self) -> None:
        png = _gradient_rgb(4, 50)
        m = measure_render_quality(png)
        assert 0.0 <= m.clipped_black_ratio <= 1.0
        assert 0.0 <= m.clipped_white_ratio <= 1.0
        assert 0.0 <= m.background_pixel_ratio <= 1.0
