"""Measured cross-view depth overlap for synthetic-village training renders.

This audit answers one narrow question: for every rendered camera, is there at
least one other camera that sees enough of the same depth-visible surface?
It deliberately does not infer feature matches, SfM registration, or 3DGS
reconstructability from that evidence.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import canary

VIEW_OVERLAP_SCHEMA = "nantai.synthetic-village.view-overlap-audit.v1"
OVERLAP_SEMANTICS = (
    "symmetric-depth-visible-surface-overlap-not-feature-match-or-reconstructability"
)
DEFAULT_SAMPLE_STRIDE_PX = 16
DEFAULT_DEPTH_RELATIVE_TOLERANCE = 0.05
SPEC_MINIMUM_SYMMETRIC_OVERLAP_RATIO = 0.65
# Blender stores the measured camera matrix through float32-scale scene data.
# CameraRegistryEntry already verifies both per-entry drift (3.2e-7) and final
# rigidity at 1e-6; this consumer must not silently tighten that trusted
# producer contract and reject its canonical output.
MEASURED_ROTATION_RIGIDITY_TOLERANCE = 1e-6

_CAMERA_ID = re.compile(r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")


class ViewOverlapError(ValueError):
    """Stable public error for malformed inputs or unauditable render evidence."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


@dataclass(frozen=True)
class MeasuredView:
    """One calibrated depth image in the declared OpenCV camera convention."""

    camera_id: str
    depth_m: np.ndarray
    intrinsics: dict[str, int | float]
    c2w_opencv: np.ndarray

    def __post_init__(self) -> None:
        if not _CAMERA_ID.fullmatch(self.camera_id):
            raise ViewOverlapError(f"invalid canonical camera ID: {self.camera_id!r}")
        depth = np.asarray(self.depth_m, dtype=np.float64)
        if (
            depth.ndim != 2
            or depth.size == 0
            or not np.all(np.isfinite(depth))
            or np.any(depth < 0)
        ):
            raise ViewOverlapError("depth must be a nonempty finite nonnegative 2D array")
        if not np.any(depth > 0):
            raise ViewOverlapError("depth must contain at least one valid positive sample")
        required = {"width_px", "height_px", "fx", "fy", "cx", "cy"}
        if set(self.intrinsics) != required:
            raise ViewOverlapError("intrinsics must contain exactly width/height/fx/fy/cx/cy")
        width = self.intrinsics["width_px"]
        height = self.intrinsics["height_px"]
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
            or (height, width) != depth.shape
        ):
            raise ViewOverlapError("depth dimensions disagree with camera intrinsics")
        numeric = tuple(float(self.intrinsics[key]) for key in ("fx", "fy", "cx", "cy"))
        if not all(math.isfinite(value) for value in numeric) or numeric[0] <= 0 or numeric[1] <= 0:
            raise ViewOverlapError("camera intrinsics must be finite with positive focal lengths")
        matrix = np.asarray(self.c2w_opencv, dtype=np.float64)
        if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
            raise ViewOverlapError("camera-to-world matrix must be finite 4x4")
        if not np.array_equal(matrix[3], np.array([0.0, 0.0, 0.0, 1.0])):
            raise ViewOverlapError("camera-to-world matrix must have a rigid homogeneous row")
        rotation = matrix[:3, :3]
        if not np.allclose(
            rotation.T @ rotation,
            np.eye(3),
            atol=MEASURED_ROTATION_RIGIDITY_TOLERANCE,
            rtol=0,
        ) or not math.isclose(
            float(np.linalg.det(rotation)),
            1.0,
            abs_tol=MEASURED_ROTATION_RIGIDITY_TOLERANCE,
        ):
            raise ViewOverlapError("camera-to-world rotation must be rigid with determinant +1")
        depth = depth.copy()
        matrix = matrix.copy()
        depth.setflags(write=False)
        matrix.setflags(write=False)
        object.__setattr__(self, "depth_m", depth)
        object.__setattr__(self, "c2w_opencv", matrix)
        object.__setattr__(self, "intrinsics", dict(self.intrinsics))


class DirectionalOverlap(FrozenModel):
    source_camera_id: str
    target_camera_id: str
    sampled_point_count: int = Field(ge=1)
    target_valid_point_count: int = Field(ge=0)
    consistent_point_count: int = Field(ge=0)
    target_valid_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    consistent_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_counts(self) -> DirectionalOverlap:
        if (
            self.target_valid_point_count > self.sampled_point_count
            or self.consistent_point_count > self.target_valid_point_count
        ):
            raise ValueError("directional overlap counts are inconsistent")
        return self


class ViewOverlapParameters(FrozenModel):
    sample_stride_px: int = Field(ge=1, le=128)
    depth_relative_tolerance: float = Field(gt=0.0, le=0.25, allow_inf_nan=False)
    minimum_symmetric_overlap_ratio: float = Field(
        gt=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    symmetric_comparison: Literal["min(source-to-neighbor,neighbor-to-source)"] = (
        "min(source-to-neighbor,neighbor-to-source)"
    )
    threshold_comparison: Literal["symmetric-overlap-greater-or-equal"] = (
        "symmetric-overlap-greater-or-equal"
    )


class CameraOverlapEvidence(FrozenModel):
    camera_id: str
    best_neighbor_camera_id: str
    source_to_neighbor_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    neighbor_to_source_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    symmetric_overlap_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    source_sampled_point_count: int = Field(ge=1)
    neighbor_sampled_point_count: int = Field(ge=1)
    passes_target: bool

    @model_validator(mode="after")
    def _validate_symmetric_ratio(self) -> CameraOverlapEvidence:
        expected = min(self.source_to_neighbor_ratio, self.neighbor_to_source_ratio)
        if not math.isclose(self.symmetric_overlap_ratio, expected, abs_tol=1e-9):
            raise ValueError("symmetric overlap must be the minimum directional ratio")
        return self


class ViewOverlapSummary(FrozenModel):
    camera_count: int = Field(ge=2)
    passing_camera_count: int = Field(ge=0)
    failing_camera_ids: tuple[str, ...]
    minimum_best_overlap_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    median_best_overlap_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    maximum_best_overlap_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    passes: bool


class ViewOverlapAudit(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.view-overlap-audit.v1"] = (
        VIEW_OVERLAP_SCHEMA
    )
    source_render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[True] = True
    verification_level: Literal["L0", "L2"]
    overlap_semantics: Literal[
        "symmetric-depth-visible-surface-overlap-not-feature-match-or-reconstructability"
    ] = OVERLAP_SEMANTICS
    parameters: ViewOverlapParameters
    cameras: tuple[CameraOverlapEvidence, ...] = Field(min_length=2)
    summary: ViewOverlapSummary
    trust_effect: Literal["none"] = "none"
    limitations: tuple[str, ...] = (
        "Depth agreement measures shared rendered surfaces, not image feature matches.",
        "Passing this audit does not prove SfM registration or 3DGS quality.",
        "Synthetic L0/L2 evidence never upgrades real-world geometry provenance.",
    )

    @model_validator(mode="after")
    def _validate_summary(self) -> ViewOverlapAudit:
        identifiers = tuple(row.camera_id for row in self.cameras)
        if identifiers != tuple(sorted(identifiers)) or len(set(identifiers)) != len(identifiers):
            raise ValueError("camera overlap rows must be unique and sorted")
        if any(row.best_neighbor_camera_id not in identifiers for row in self.cameras):
            raise ValueError("best neighbor must reference another audited camera")
        if any(row.best_neighbor_camera_id == row.camera_id for row in self.cameras):
            raise ValueError("a camera cannot be its own overlap neighbor")
        values = tuple(row.symmetric_overlap_ratio for row in self.cameras)
        failing = tuple(row.camera_id for row in self.cameras if not row.passes_target)
        if (
            self.summary.camera_count != len(self.cameras)
            or self.summary.passing_camera_count
            != sum(row.passes_target for row in self.cameras)
            or self.summary.failing_camera_ids != failing
            or not math.isclose(self.summary.minimum_best_overlap_ratio, min(values), abs_tol=1e-9)
            or not math.isclose(
                self.summary.median_best_overlap_ratio,
                statistics.median(values),
                abs_tol=1e-9,
            )
            or not math.isclose(self.summary.maximum_best_overlap_ratio, max(values), abs_tol=1e-9)
            or self.summary.passes != (not failing)
        ):
            raise ValueError("view-overlap summary disagrees with camera evidence")
        return self


def _ratio(value: float) -> float:
    return round(float(value), 9)


def _backproject_sampled(view: MeasuredView, stride: int) -> np.ndarray:
    height, width = view.depth_m.shape
    rows, columns = np.meshgrid(
        np.arange(0, height, stride),
        np.arange(0, width, stride),
        indexing="ij",
    )
    ranges = view.depth_m[rows, columns]
    valid = ranges > 0
    rows = rows[valid]
    columns = columns[valid]
    ranges = ranges[valid]
    if not ranges.size:
        raise ViewOverlapError(f"camera {view.camera_id} has no sampled positive depth")
    fx = float(view.intrinsics["fx"])
    fy = float(view.intrinsics["fy"])
    cx = float(view.intrinsics["cx"])
    cy = float(view.intrinsics["cy"])
    u = columns.astype(np.float64) + 0.5
    v = rows.astype(np.float64) + 0.5
    directions = np.stack(
        ((u - cx) / fx, (v - cy) / fy, np.ones_like(u)),
        axis=1,
    )
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    points_camera = directions * ranges[:, None]
    rotation = view.c2w_opencv[:3, :3]
    center = view.c2w_opencv[:3, 3]
    return points_camera @ rotation.T + center


def measure_directional_overlap(
    source: MeasuredView,
    target: MeasuredView,
    *,
    sample_stride_px: int = DEFAULT_SAMPLE_STRIDE_PX,
    depth_relative_tolerance: float = DEFAULT_DEPTH_RELATIVE_TOLERANCE,
) -> DirectionalOverlap:
    """Measure source surface samples visible and depth-consistent in target."""

    parameters = ViewOverlapParameters(
        sample_stride_px=sample_stride_px,
        depth_relative_tolerance=depth_relative_tolerance,
        minimum_symmetric_overlap_ratio=SPEC_MINIMUM_SYMMETRIC_OVERLAP_RATIO,
    )
    points_world = _backproject_sampled(source, parameters.sample_stride_px)
    rotation = target.c2w_opencv[:3, :3]
    center = target.c2w_opencv[:3, 3]
    points_camera = (points_world - center) @ rotation
    z = points_camera[:, 2]
    predicted_range = np.linalg.norm(points_camera, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (
            float(target.intrinsics["fx"]) * points_camera[:, 0] / z
            + float(target.intrinsics["cx"])
        )
        v = (
            float(target.intrinsics["fy"]) * points_camera[:, 1] / z
            + float(target.intrinsics["cy"])
        )
    height, width = target.depth_m.shape
    in_bounds = (
        (z > 0)
        & np.isfinite(u)
        & np.isfinite(v)
        & (u >= 0)
        & (u < width)
        & (v >= 0)
        & (v < height)
    )
    sampled_target = np.zeros(len(points_world), dtype=np.float64)
    indices = np.flatnonzero(in_bounds)
    sampled_target[indices] = target.depth_m[
        v[indices].astype(np.int64),
        u[indices].astype(np.int64),
    ]
    target_valid = in_bounds & (sampled_target > 0)
    relative_error = np.full(len(points_world), np.inf, dtype=np.float64)
    relative_error[target_valid] = np.abs(
        sampled_target[target_valid] - predicted_range[target_valid]
    ) / np.maximum(predicted_range[target_valid], 1e-12)
    consistent = relative_error <= parameters.depth_relative_tolerance
    sample_count = len(points_world)
    target_count = int(np.count_nonzero(target_valid))
    consistent_count = int(np.count_nonzero(consistent))
    return DirectionalOverlap(
        source_camera_id=source.camera_id,
        target_camera_id=target.camera_id,
        sampled_point_count=sample_count,
        target_valid_point_count=target_count,
        consistent_point_count=consistent_count,
        target_valid_ratio=_ratio(target_count / sample_count),
        consistent_ratio=_ratio(consistent_count / sample_count),
    )


def audit_measured_views(
    views: tuple[MeasuredView, ...],
    *,
    source_render_id: str,
    source_journal_sha256: str,
    verification_level: Literal["L0", "L2"],
    sample_stride_px: int = DEFAULT_SAMPLE_STRIDE_PX,
    depth_relative_tolerance: float = DEFAULT_DEPTH_RELATIVE_TOLERANCE,
    minimum_symmetric_overlap_ratio: float = SPEC_MINIMUM_SYMMETRIC_OVERLAP_RATIO,
) -> ViewOverlapAudit:
    """Choose each camera's best symmetric depth-overlap neighbor."""

    parameters = ViewOverlapParameters(
        sample_stride_px=sample_stride_px,
        depth_relative_tolerance=depth_relative_tolerance,
        minimum_symmetric_overlap_ratio=minimum_symmetric_overlap_ratio,
    )
    ordered = tuple(sorted(views, key=lambda view: view.camera_id))
    if len(ordered) < 2 or len({view.camera_id for view in ordered}) != len(ordered):
        raise ViewOverlapError("view-overlap audit needs at least two unique cameras")
    directional = {
        (source.camera_id, target.camera_id): measure_directional_overlap(
            source,
            target,
            sample_stride_px=parameters.sample_stride_px,
            depth_relative_tolerance=parameters.depth_relative_tolerance,
        )
        for source in ordered
        for target in ordered
        if source.camera_id != target.camera_id
    }
    rows = []
    for source in ordered:
        candidates = []
        for target in ordered:
            if source.camera_id == target.camera_id:
                continue
            forward = directional[(source.camera_id, target.camera_id)]
            reverse = directional[(target.camera_id, source.camera_id)]
            symmetric = _ratio(min(forward.consistent_ratio, reverse.consistent_ratio))
            candidates.append((symmetric, target.camera_id, forward, reverse))
        best_ratio = max(row[0] for row in candidates)
        _, neighbor_id, forward, reverse = min(
            (row for row in candidates if row[0] == best_ratio),
            key=lambda row: row[1],
        )
        rows.append(
            CameraOverlapEvidence(
                camera_id=source.camera_id,
                best_neighbor_camera_id=neighbor_id,
                source_to_neighbor_ratio=forward.consistent_ratio,
                neighbor_to_source_ratio=reverse.consistent_ratio,
                symmetric_overlap_ratio=best_ratio,
                source_sampled_point_count=forward.sampled_point_count,
                neighbor_sampled_point_count=reverse.sampled_point_count,
                passes_target=best_ratio >= parameters.minimum_symmetric_overlap_ratio,
            )
        )
    cameras = tuple(rows)
    values = tuple(row.symmetric_overlap_ratio for row in cameras)
    failing = tuple(row.camera_id for row in cameras if not row.passes_target)
    return ViewOverlapAudit(
        source_render_id=source_render_id,
        source_journal_sha256=source_journal_sha256,
        verification_level=verification_level,
        parameters=parameters,
        cameras=cameras,
        summary=ViewOverlapSummary(
            camera_count=len(cameras),
            passing_camera_count=len(cameras) - len(failing),
            failing_camera_ids=failing,
            minimum_best_overlap_ratio=_ratio(min(values)),
            median_best_overlap_ratio=_ratio(statistics.median(values)),
            maximum_best_overlap_ratio=_ratio(max(values)),
            passes=not failing,
        ),
    )


def _load_depth_exr(path: Path) -> np.ndarray:
    try:
        import OpenEXR

        snapshot = canary._snapshot_regular_file(path)
        channels = OpenEXR.File(str(path)).channels()
        if set(channels) != {"V"}:
            raise ViewOverlapError(
                f"depth EXR must contain exactly channel V, found {sorted(channels)}",
            )
        depth = np.asarray(channels["V"].pixels, dtype=np.float64)
        canary._verify_snapshots_unchanged((snapshot,))
        return depth
    except ViewOverlapError:
        raise
    except (ImportError, OSError, RuntimeError, ValueError, canary.CanaryBuildError) as exc:
        raise ViewOverlapError(f"cannot read trusted depth EXR: {exc}") from exc


def audit_render_view_overlap(
    render_root: Path,
    *,
    sample_stride_px: int = DEFAULT_SAMPLE_STRIDE_PX,
    depth_relative_tolerance: float = DEFAULT_DEPTH_RELATIVE_TOLERANCE,
    minimum_symmetric_overlap_ratio: float = SPEC_MINIMUM_SYMMETRIC_OVERLAP_RATIO,
) -> ViewOverlapAudit:
    """Audit a fully verified formal or local 24-frame render directory."""

    try:
        root = canary._require_real_directory(Path(render_root).absolute(), label="render root")
        journal_path = root / "render-journal.json"
        journal_snapshot = canary._snapshot_regular_file(journal_path)
        journal = canary.load_render_journal(journal_path)
        if any(frame.state != "verified" for frame in journal.frames):
            raise ViewOverlapError("view overlap requires every render frame to be verified")
        views = []
        for frame in journal.frames:
            canary._verify_published_frame(root, frame)
            metadata = canary._load_camera_metadata(root / f"cameras/{frame.camera_id}.json")
            if (
                metadata.camera_id != frame.camera_id
                or metadata.render_id != journal.render_id
                or metadata.build_id != journal.build_id
                or metadata.verification_level != journal.verification_level
                or metadata.synthetic is not True
            ):
                raise ViewOverlapError("camera metadata disagrees with the verified journal")
            views.append(
                MeasuredView(
                    camera_id=frame.camera_id,
                    depth_m=_load_depth_exr(root / f"depth/{frame.camera_id}.exr"),
                    intrinsics=metadata.intrinsics,
                    c2w_opencv=np.asarray(metadata.measured_c2w_opencv, dtype=np.float64),
                )
            )
        report = audit_measured_views(
            tuple(views),
            source_render_id=journal.render_id,
            source_journal_sha256=journal.journal_sha256,
            verification_level=journal.verification_level,
            sample_stride_px=sample_stride_px,
            depth_relative_tolerance=depth_relative_tolerance,
            minimum_symmetric_overlap_ratio=minimum_symmetric_overlap_ratio,
        )
        for frame in journal.frames:
            canary._verify_published_frame(root, frame)
        canary._verify_snapshots_unchanged((journal_snapshot,))
        return report
    except ViewOverlapError:
        raise
    except (OSError, RuntimeError, ValueError, canary.CanaryBuildError) as exc:
        raise ViewOverlapError(f"render overlap audit failed safely: {exc}") from exc


def canonical_view_overlap_audit_bytes(report: ViewOverlapAudit) -> bytes:
    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (payload + "\n").encode("utf-8")


def write_view_overlap_audit(report: ViewOverlapAudit, destination: Path) -> Path:
    """Durably publish canonical evidence only to an absent regular path."""

    path = Path(destination).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_view_overlap_audit_bytes(report)
    if path.exists() or canary._is_linklike(path):
        raise ViewOverlapError("view-overlap report destination must start absent")
    try:
        canary._write_new_file(path, payload)
        return path
    except canary.CanaryBuildError as exc:
        raise ViewOverlapError(f"cannot durably publish view-overlap report: {exc}") from exc
