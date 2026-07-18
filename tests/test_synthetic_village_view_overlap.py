from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import scripts.synthetic_village as cli
from pipeline.synthetic_village.view_overlap import (
    MeasuredView,
    ViewOverlapError,
    audit_measured_views,
    canonical_view_overlap_audit_bytes,
    measure_directional_overlap,
    write_view_overlap_audit,
)


def _view(
    camera_id: str,
    depth_m: np.ndarray,
    *,
    c2w_opencv: np.ndarray | None = None,
) -> MeasuredView:
    height, width = depth_m.shape
    return MeasuredView(
        camera_id=camera_id,
        depth_m=depth_m,
        intrinsics={
            "width_px": width,
            "height_px": height,
            "fx": float(width),
            "fy": float(width),
            "cx": width / 2,
            "cy": height / 2,
        },
        c2w_opencv=np.eye(4) if c2w_opencv is None else c2w_opencv,
    )


def test_directional_overlap_does_not_hide_one_way_visibility() -> None:
    complete = _view("camera-ground-001", np.full((10, 10), 5.0))
    half_visible_depth = np.full((10, 10), 5.0)
    half_visible_depth[5:, :] = 0.0
    half_visible = _view("camera-ground-002", half_visible_depth)

    forward = measure_directional_overlap(complete, half_visible, sample_stride_px=1)
    reverse = measure_directional_overlap(half_visible, complete, sample_stride_px=1)

    assert forward.consistent_ratio == pytest.approx(0.5)
    assert reverse.consistent_ratio == pytest.approx(1.0)
    assert forward.sampled_point_count == 100
    assert reverse.sampled_point_count == 50


def test_audit_uses_symmetric_minimum_and_fails_below_spec_target() -> None:
    complete = _view("camera-ground-001", np.full((10, 10), 5.0))
    half_visible_depth = np.full((10, 10), 5.0)
    half_visible_depth[5:, :] = 0.0
    half_visible = _view("camera-ground-002", half_visible_depth)

    report = audit_measured_views(
        (complete, half_visible),
        source_render_id="a" * 64,
        source_journal_sha256="b" * 64,
        verification_level="L0",
        sample_stride_px=1,
        minimum_symmetric_overlap_ratio=0.65,
    )

    assert report.summary.passes is False
    assert report.summary.passing_camera_count == 0
    assert report.summary.failing_camera_ids == (
        "camera-ground-001",
        "camera-ground-002",
    )
    for row in report.cameras:
        assert row.source_to_neighbor_ratio in {0.5, 1.0}
        assert row.neighbor_to_source_ratio in {0.5, 1.0}
        assert row.symmetric_overlap_ratio == pytest.approx(0.5)
        assert row.passes_target is False


def test_audit_selects_deterministic_best_neighbor_and_passes_identical_views() -> None:
    depth = np.full((8, 8), 7.0)
    views = (
        _view("camera-ground-003", depth.copy()),
        _view("camera-ground-001", depth.copy()),
        _view("camera-ground-002", depth.copy()),
    )

    report = audit_measured_views(
        views,
        source_render_id="c" * 64,
        source_journal_sha256="d" * 64,
        verification_level="L2",
        sample_stride_px=1,
        minimum_symmetric_overlap_ratio=0.65,
    )

    assert tuple(row.camera_id for row in report.cameras) == (
        "camera-ground-001",
        "camera-ground-002",
        "camera-ground-003",
    )
    assert tuple(row.best_neighbor_camera_id for row in report.cameras) == (
        "camera-ground-002",
        "camera-ground-001",
        "camera-ground-001",
    )
    assert report.summary.passes is True
    assert report.summary.passing_camera_count == 3
    assert report.summary.minimum_best_overlap_ratio == pytest.approx(1.0)
    assert report.summary.median_best_overlap_ratio == pytest.approx(1.0)
    assert report.summary.maximum_best_overlap_ratio == pytest.approx(1.0)
    assert report.trust_effect == "none"
    assert report.overlap_semantics == (
        "symmetric-depth-visible-surface-overlap-not-feature-match-or-reconstructability"
    )


def test_measured_view_rejects_bad_depth_and_nonrigid_camera() -> None:
    with pytest.raises(ViewOverlapError, match="finite nonnegative"):
        _view("camera-ground-001", np.array([[np.nan]]))

    reflected = np.eye(4)
    reflected[0, 0] = -1
    with pytest.raises(ViewOverlapError, match="rigid"):
        _view("camera-ground-001", np.ones((2, 2)), c2w_opencv=reflected)


def test_measured_view_accepts_verified_blender_float32_rotation() -> None:
    measured_c2w = np.array(
        [
            [7.549790126404332e-8, 0.5809493064880371, -0.81393963098526, 4.0],
            [1.0, -7.526593748252708e-8, 3.903506851088423e-8, 0.0],
            [
                -3.858454178384818e-8,
                -0.81393963098526,
                -0.5809493064880371,
                71.8550033569336,
            ],
            [0.0, 0.0, 0.0, 1.0],
        ],
    )

    view = _view(
        "camera-ground-001",
        np.ones((2, 2)),
        c2w_opencv=measured_c2w,
    )

    assert view.c2w_opencv.shape == (4, 4)


def test_report_bytes_are_canonical_and_publication_starts_absent(tmp_path: Path) -> None:
    depth = np.full((4, 4), 3.0)
    report = audit_measured_views(
        (
            _view("camera-ground-001", depth),
            _view("camera-ground-002", depth),
        ),
        source_render_id="e" * 64,
        source_journal_sha256="f" * 64,
        verification_level="L0",
        sample_stride_px=1,
        minimum_symmetric_overlap_ratio=0.65,
    )

    raw = canonical_view_overlap_audit_bytes(report)
    assert raw.endswith(b"\n")
    assert json.loads(raw)["schema_version"] == "nantai.synthetic-village.view-overlap-audit.v1"

    destination = tmp_path / "view-overlap-audit.json"
    assert write_view_overlap_audit(report, destination) == destination
    assert destination.read_bytes() == raw
    with pytest.raises(ViewOverlapError, match="start absent"):
        write_view_overlap_audit(report, destination)


def test_cli_reports_failed_overlap_gate_with_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    depth = np.full((6, 6), 4.0)
    partial = depth.copy()
    partial[3:, :] = 0.0
    report = audit_measured_views(
        (
            _view("camera-ground-001", depth),
            _view("camera-ground-002", partial),
        ),
        source_render_id="1" * 64,
        source_journal_sha256="2" * 64,
        verification_level="L0",
        sample_stride_px=1,
        minimum_symmetric_overlap_ratio=0.65,
    )
    written: list[Path] = []
    monkeypatch.setattr(
        cli,
        "_audit_render_view_overlap",
        lambda: (
            lambda _root, **_kwargs: report
        ),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "_write_view_overlap_audit",
        lambda: (
            lambda _report, destination: written.append(destination) or destination
        ),
        raising=False,
    )
    destination = tmp_path / "overlap.json"

    exit_code = cli.main(
        [
            "audit-view-overlap",
            "--render-root",
            str(tmp_path),
            "--sample-stride",
            "1",
            "--report",
            str(destination),
        ],
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["passes"] is False
    assert payload["passing_camera_count"] == 0
    assert payload["camera_count"] == 2
    assert payload["failing_camera_ids"] == [
        "camera-ground-001",
        "camera-ground-002",
    ]
    assert payload["minimum_symmetric_overlap_ratio"] == 0.65
    assert payload["report_path"] == str(destination)
    assert written == [destination]
