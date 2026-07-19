"""Registered pedestrian surface-quality metrics without trust promotion."""

from __future__ import annotations

import re
from typing import Literal, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

CAMERA_ID_PATTERN = re.compile(r"^camera-ground-route-\d{3}$")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_metric_vectors(
    left: np.ndarray,
    right: np.ndarray,
    *,
    minimum_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    left_array = np.asarray(left, dtype=np.float64)
    right_array = np.asarray(right, dtype=np.float64)
    if left_array.ndim != 1 or right_array.ndim != 1:
        raise ValueError("metric vectors must be one-dimensional")
    if left_array.shape != right_array.shape:
        raise ValueError("metric vectors are mismatched")
    if left_array.size < minimum_size:
        raise ValueError(f"metric vectors require at least {minimum_size} samples")
    if not np.isfinite(left_array).all() or not np.isfinite(right_array).all():
        raise ValueError("metric vectors contain non-finite values")
    return left_array, right_array


def path_lag_autocorrelation(
    arc_length_m: np.ndarray,
    luminance: np.ndarray,
    *,
    lag_m: float = 3.0,
    bin_m: float = 0.10,
    detrend_window_m: float = 10.0,
) -> float:
    """Measure a registered path signal's correlation at a world-space lag."""

    arc, values = _validate_metric_vectors(
        arc_length_m,
        luminance,
        minimum_size=3,
    )
    if np.any(np.diff(arc) <= 0.0):
        raise ValueError("registered path arc length must be strictly increasing")
    if (
        not np.isfinite(lag_m)
        or not np.isfinite(bin_m)
        or not np.isfinite(detrend_window_m)
        or lag_m <= 0.0
        or bin_m <= 0.0
        or detrend_window_m <= 0.0
    ):
        raise ValueError("path lag parameters must be finite and positive")
    grid = np.arange(arc[0], arc[-1] + bin_m * 0.5, bin_m)
    if grid.size < 3:
        raise ValueError("registered path coverage is too short")
    regular = np.interp(grid, arc, values)
    window_bins = max(3, round(detrend_window_m / bin_m))
    kernel = np.ones(window_bins, dtype=np.float64) / window_bins
    trend = np.convolve(regular, kernel, mode="same")
    detrended = regular - trend
    lag_bins = round(lag_m / bin_m)
    if lag_bins <= 0 or lag_bins >= detrended.size - 1:
        raise ValueError("requested lag is outside registered path coverage")
    left_signal = detrended[:-lag_bins]
    right_signal = detrended[lag_bins:]
    if np.std(left_signal) <= 0.0 or np.std(right_signal) <= 0.0:
        raise ValueError("path correlation is undefined")
    correlation = np.corrcoef(
        left_signal,
        right_signal,
    )[0, 1]
    if not np.isfinite(correlation):
        raise ValueError("path correlation is undefined")
    return float(correlation)


def rank_correlation(left: np.ndarray, right: np.ndarray) -> float:
    """Compute Spearman correlation with deterministic average ranks."""

    left_array, right_array = _validate_metric_vectors(
        left,
        right,
        minimum_size=12,
    )

    def average_ranks(values: np.ndarray) -> np.ndarray:
        order = np.argsort(values, kind="mergesort")
        ranks = np.empty(values.size, dtype=np.float64)
        start = 0
        while start < values.size:
            stop = start + 1
            while (
                stop < values.size
                and values[order[stop]] == values[order[start]]
            ):
                stop += 1
            ranks[order[start:stop]] = (start + stop - 1) / 2.0
            start = stop
        return ranks

    left_ranks = average_ranks(left_array)
    right_ranks = average_ranks(right_array)
    if np.std(left_ranks) <= 0.0 or np.std(right_ranks) <= 0.0:
        raise ValueError("rank correlation is undefined")
    correlation = np.corrcoef(left_ranks, right_ranks)[0, 1]
    if not np.isfinite(correlation):
        raise ValueError("rank correlation is undefined")
    return float(correlation)


class SurfaceQualityReport(FrozenModel):
    """Measured L0 quality evidence; never a reconstruction trust signal."""

    source_to_derived_ssim: float = Field(
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    candidate_three_m_peak: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    legacy_three_m_peak: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    detail_gradient_ratio: float = Field(
        ge=0.0,
        allow_inf_nan=False,
    )
    macro_p05: float = Field(
        ge=0.88,
        le=1.10,
        allow_inf_nan=False,
    )
    macro_p95: float = Field(
        ge=0.88,
        le=1.10,
        allow_inf_nan=False,
    )
    anchor_spearman: float = Field(
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    maximum_projection_error_px: float = Field(
        ge=0.0,
        allow_inf_nan=False,
    )
    camera_eye_height_m: Literal[1.6]
    sampled_camera_ids: tuple[str, str, str]

    @model_validator(mode="after")
    def _registered_evidence_is_complete(self) -> Self:
        if (
            self.macro_p05 > self.macro_p95
            or len(set(self.sampled_camera_ids)) != 3
            or tuple(sorted(self.sampled_camera_ids)) != self.sampled_camera_ids
            or any(
                CAMERA_ID_PATTERN.fullmatch(camera_id) is None
                for camera_id in self.sampled_camera_ids
            )
        ):
            raise ValueError("surface quality evidence is unregistered or inconsistent")
        return self

    @property
    def passes(self) -> bool:
        return (
            self.source_to_derived_ssim >= 0.94
            and self.candidate_three_m_peak <= 0.35
            and self.candidate_three_m_peak
            <= self.legacy_three_m_peak * 0.70
            and self.detail_gradient_ratio >= 1.20
            and self.macro_p05 <= 0.94
            and self.macro_p95 >= 1.04
            and self.anchor_spearman >= 0.80
            and self.maximum_projection_error_px <= 3.0
            and self.camera_eye_height_m == 1.6
        )
