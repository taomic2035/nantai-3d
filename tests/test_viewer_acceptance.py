from __future__ import annotations

import math

import pytest

from pipeline.viewer_acceptance import (
    ViewerAcceptanceError,
    ViewerPerformancePolicy,
    ViewerPerformanceReport,
    ViewerPoseMeasurement,
    ViewerRuntimeIdentity,
    derive_viewer_decision,
)

POSES = ("pose-" + "a" * 64, "pose-" + "b" * 64, "pose-" + "c" * 64)


def _policy() -> ViewerPerformancePolicy:
    return ViewerPerformancePolicy(
        required_pose_ids=POSES,
        viewport_width=1280,
        viewport_height=720,
        warmup_frame_count=120,
        measured_frame_count=600,
        maximum_interactive_ms=15_000.0,
        maximum_p50_frame_ms=33.34,
        maximum_p95_frame_ms=50.0,
        maximum_worst_frame_ms=250.0,
    )


def _report(
    *,
    representation="full-3dgs",
    warmup_count=120,
    measured_count=600,
    duration=33.34,
    interactive_ms=15_000.0,
) -> ViewerPerformanceReport:
    poses = tuple(
        ViewerPoseMeasurement(
            pose_id=pose_id,
            representation=representation,
            interactive_ms=interactive_ms,
            warmup_frame_ms=(duration,) * warmup_count,
            measured_frame_ms=(duration,) * measured_count,
            timed_out=False,
            sample_overflow=False,
        )
        for pose_id in POSES
    )
    return ViewerPerformanceReport(
        source_role="production-acceptance",
        scene_manifest_sha256="d" * 64,
        viewport_width=1280,
        viewport_height=720,
        http_cache="empty",
        runtime=ViewerRuntimeIdentity(
            browser_name="chromium",
            browser_version="130.0.0",
            playwright_version="1.55.0",
            operating_system="macOS arm64",
            gpu_vendor="Apple",
            gpu_renderer="Apple M-series",
            webgl_version="WebGL 2.0",
        ),
        poses=poses,
        console_errors=(),
        unhandled_rejections=(),
    )


def test_exact_viewer_thresholds_pass():
    decision = derive_viewer_decision(_policy(), _report())

    assert decision.accepted is True
    assert decision.failed_gates == ()
    assert decision.pose_count == 3
    assert decision.maximum_observed_p95_frame_ms == 33.34


def test_point_fallback_cannot_pass_full_3dgs_gate():
    report = _report(representation="dc-point-preview")

    decision = derive_viewer_decision(_policy(), report)

    assert decision.accepted is False
    assert any("full-3dgs" in gate for gate in decision.failed_gates)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("viewport_width", 1279, "viewport"),
        ("viewport_height", 719, "viewport"),
        ("http_cache", "warm", "cache"),
        ("console_errors", ("shader compile failed",), "console"),
        ("unhandled_rejections", ("chunk rejected",), "rejection"),
    ],
)
def test_runtime_contract_drift_is_rejected(field, value, message):
    report = _report().model_copy(update={field: value})

    decision = derive_viewer_decision(_policy(), report)

    assert decision.accepted is False
    assert any(message in gate for gate in decision.failed_gates)


def test_fewer_than_600_measured_frames_cannot_pass():
    decision = derive_viewer_decision(
        _policy(),
        _report(measured_count=599),
    )

    assert decision.accepted is False
    assert any("600 measured" in gate for gate in decision.failed_gates)


def test_warmup_count_drift_cannot_pass():
    decision = derive_viewer_decision(
        _policy(),
        _report(warmup_count=119),
    )

    assert decision.accepted is False
    assert any("120 warmup" in gate for gate in decision.failed_gates)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("timed_out", "timeout"),
        ("sample_overflow", "overflow"),
    ],
)
def test_probe_failure_flags_cannot_pass(field, message):
    report = _report()
    pose = report.poses[0].model_copy(update={field: True})
    damaged = report.model_copy(
        update={"poses": (pose, *report.poses[1:])}
    )

    decision = derive_viewer_decision(_policy(), damaged)

    assert decision.accepted is False
    assert any(message in gate for gate in decision.failed_gates)


@pytest.mark.parametrize(
    ("duration", "message"),
    [
        (50.01, "p50"),
        (250.01, "worst"),
    ],
)
def test_frame_time_thresholds_are_inclusive(duration, message):
    decision = derive_viewer_decision(
        _policy(),
        _report(duration=duration),
    )

    assert decision.accepted is False
    assert any(message in gate for gate in decision.failed_gates)


def test_nearest_rank_p95_is_rederived_from_raw_frames():
    report = _report(duration=10.0)
    samples = (10.0,) * 569 + (50.0,) * 31
    pose = report.poses[0].model_copy(
        update={"measured_frame_ms": samples}
    )
    boundary = report.model_copy(
        update={"poses": (pose, *report.poses[1:])}
    )

    assert derive_viewer_decision(_policy(), boundary).accepted is True

    failed_pose = pose.model_copy(
        update={
            "measured_frame_ms":
                (10.0,) * 569 + (50.01,) * 31,
        }
    )
    failed = report.model_copy(
        update={"poses": (failed_pose, *report.poses[1:])}
    )
    decision = derive_viewer_decision(_policy(), failed)
    assert decision.accepted is False
    assert any("p95" in gate for gate in decision.failed_gates)


def test_wrong_or_duplicate_pose_set_is_rejected():
    report = _report()
    duplicate = report.model_copy(
        update={"poses": (report.poses[0], report.poses[0], report.poses[2])}
    )

    with pytest.raises(ViewerAcceptanceError, match="duplicate"):
        derive_viewer_decision(_policy(), duplicate)

    missing = report.model_copy(update={"poses": report.poses[:2]})
    decision = derive_viewer_decision(_policy(), missing)
    assert decision.accepted is False
    assert any("pose set" in gate for gate in decision.failed_gates)


def test_non_finite_or_nonpositive_frame_sample_is_invalid():
    report = _report()
    for invalid in (math.nan, math.inf, 0.0, -1.0):
        pose = report.poses[0].model_copy(
            update={
                "measured_frame_ms":
                    (invalid, *report.poses[0].measured_frame_ms[1:]),
            }
        )
        damaged = report.model_copy(
            update={"poses": (pose, *report.poses[1:])}
        )
        with pytest.raises(ViewerAcceptanceError, match="invalid"):
            derive_viewer_decision(_policy(), damaged)


def test_report_authored_acceptance_boolean_is_forbidden():
    report = _report().model_copy(update={"accepted": True})

    with pytest.raises(ViewerAcceptanceError, match="authored"):
        derive_viewer_decision(_policy(), report)
