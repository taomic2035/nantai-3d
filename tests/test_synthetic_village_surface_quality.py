from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.surface_quality import (
    SurfaceQualityReport,
    path_lag_autocorrelation,
    rank_correlation,
)


def _passing_report() -> SurfaceQualityReport:
    return SurfaceQualityReport(
        source_to_derived_ssim=0.95,
        candidate_three_m_peak=0.30,
        legacy_three_m_peak=0.50,
        detail_gradient_ratio=1.25,
        macro_p05=0.93,
        macro_p95=1.05,
        anchor_spearman=0.85,
        maximum_projection_error_px=2.0,
        camera_eye_height_m=1.6,
        sampled_camera_ids=(
            "camera-ground-route-001",
            "camera-ground-route-019",
            "camera-ground-route-037",
        ),
    )


def test_path_autocorrelation_detects_three_metre_repetition() -> None:
    arc = np.arange(0.0, 60.0, 0.1)
    repeated = np.sin(2.0 * np.pi * arc / 3.0)
    varied = np.sin(2.0 * np.pi * arc / 11.0) * 0.2

    assert path_lag_autocorrelation(arc, repeated, lag_m=3.0) > 0.9
    assert abs(path_lag_autocorrelation(arc, varied, lag_m=3.0)) < 0.35


def test_rank_correlation_handles_ties_without_scipy() -> None:
    left = np.array([0, 0, 1, 2, 2, 3, 4, 4, 5, 6, 6, 7], dtype=float)
    right = left * 3.0 + 2.0
    reversed_right = (7.0 - left) * 3.0 + 2.0

    assert rank_correlation(left, right) == pytest.approx(1.0)
    assert rank_correlation(left, reversed_right) == pytest.approx(-1.0)


def test_quality_report_requires_every_numeric_gate() -> None:
    report = _passing_report()

    assert report.passes is True
    for field, value in (
        ("source_to_derived_ssim", 0.939),
        ("candidate_three_m_peak", 0.351),
        ("legacy_three_m_peak", 0.40),
        ("detail_gradient_ratio", 1.19),
        ("macro_p05", 0.941),
        ("macro_p95", 1.039),
        ("anchor_spearman", 0.79),
        ("maximum_projection_error_px", 3.01),
    ):
        assert report.model_copy(update={field: value}).passes is False


@pytest.mark.parametrize(
    ("arc", "values", "match"),
    [
        (
            np.array([0.0, 1.0, 0.5, 2.0]),
            np.array([1.0, 2.0, 3.0, 4.0]),
            "strictly increasing",
        ),
        (
            np.array([0.0, 1.0, 2.0]),
            np.array([1.0, np.nan, 3.0]),
            "non-finite",
        ),
        (
            np.array([0.0, 1.0, 2.0]),
            np.array([1.0, 2.0]),
            "mismatched",
        ),
    ],
)
def test_path_autocorrelation_rejects_unregistered_samples(
    arc: np.ndarray,
    values: np.ndarray,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        path_lag_autocorrelation(arc, values)


def test_rank_correlation_rejects_too_few_or_constant_anchors() -> None:
    with pytest.raises(ValueError, match="at least 12"):
        rank_correlation(np.arange(11), np.arange(11))
    with pytest.raises(ValueError, match="undefined"):
        rank_correlation(np.ones(12), np.arange(12))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_to_derived_ssim", float("nan")),
        ("candidate_three_m_peak", float("inf")),
        ("legacy_three_m_peak", -0.1),
        ("detail_gradient_ratio", -0.1),
        ("macro_p05", 1.11),
        ("macro_p95", 0.87),
        ("anchor_spearman", 1.1),
        ("maximum_projection_error_px", -0.1),
        (
            "sampled_camera_ids",
            (
                "camera-ground-route-001",
                "camera-ground-route-001",
                "camera-ground-route-037",
            ),
        ),
    ],
)
def test_quality_report_rejects_invalid_evidence(field: str, value: object) -> None:
    payload = _passing_report().model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        SurfaceQualityReport.model_validate(payload)


def test_quality_report_is_strict_preview_evidence() -> None:
    payload = _passing_report().model_dump()
    payload["camera_eye_height_m"] = 1.7
    with pytest.raises(ValidationError):
        SurfaceQualityReport.model_validate(payload)

    payload = _passing_report().model_dump()
    payload["geometry_usability"] = "metric-aligned"
    with pytest.raises(ValidationError):
        SurfaceQualityReport.model_validate(payload)
