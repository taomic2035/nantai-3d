from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import stat
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.viewer_acceptance as viewer_acceptance_module
from pipeline.viewer_acceptance import (
    StableViewerExecutableObservation,
    ViewerAcceptanceError,
    ViewerCameraSetV2,
    ViewerCaptureArtifactBinding,
    ViewerExecutableSnapshot,
    ViewerPerformancePolicy,
    ViewerPerformanceReport,
    ViewerPerformanceReportV2,
    ViewerPoseMeasurement,
    ViewerRuntimeIdentity,
    ViewerScreenshotBinding,
    build_viewer_performance_report_v2,
    canonical_viewer_performance_policy_bytes,
    canonical_viewer_performance_report_bytes,
    derive_viewer_decision,
    load_viewer_performance_report_bytes,
    verify_viewer_capture_report,
    viewer_camera_pose_id,
)

SCENE_MANIFEST_BYTES = b'{"scene":"real"}\n'
IMPORT_RECEIPT_SHA256 = "e" * 64
ALIGNED_REGISTRATION_SHA256 = "f" * 64


def _camera_set_bytes(
    offset: int = 0,
    *,
    version: int = 2,
    scene_manifest_sha256: str | None = None,
    import_receipt_sha256: str = IMPORT_RECEIPT_SHA256,
    aligned_registration_sha256: str = ALIGNED_REGISTRATION_SHA256,
) -> tuple[bytes, tuple[str, ...]]:
    def numeric_projection(value):
        if (
            isinstance(value, bool)
            or value is None
            or isinstance(value, str)
        ):
            return value
        if isinstance(value, (int, float)):
            return {"$f64": struct.pack(">d", float(value)).hex()}
        if isinstance(value, list):
            return [numeric_projection(item) for item in value]
        return {
            key: numeric_projection(item)
            for key, item in value.items()
        }

    rows = []
    for index in range(3):
        payload = {
            "schema": "nantai.viewer-camera-pose.v1",
            "position": {
                "east": offset + index * 2,
                "north": index * 3,
                "up": 2,
            },
            "look_at": {
                "east": offset + index * 2 + 1,
                "north": index * 3 + 1,
                "up": 2,
            },
        }
        canonical_pose = json.dumps(
            numeric_projection(payload),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        rows.append(
            {
                "pose_id": (
                    "pose-" + hashlib.sha256(canonical_pose).hexdigest()
                ),
                **payload,
            }
        )
    if version == 1:
        camera_set = {
            "schema": "nantai.viewer-camera-set.v1",
            "poses": rows,
        }
    else:
        camera_set = {
            "schema": "nantai.viewer-camera-set.v2",
            "source_role": "production-acceptance",
            "selection_strategy": "registered-camera-maximin-v1",
            "scene_manifest_sha256": (
                scene_manifest_sha256
                or hashlib.sha256(SCENE_MANIFEST_BYTES).hexdigest()
            ),
            "import_receipt_sha256": import_receipt_sha256,
            "aligned_registration_sha256": aligned_registration_sha256,
            "poses": rows,
        }
    payload = (
        json.dumps(
            camera_set,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    return payload, tuple(row["pose_id"] for row in rows)


CAMERA_SET_BYTES, POSES = _camera_set_bytes()


def test_camera_pose_id_matches_cross_language_numeric_golden():
    pose = {
        "schema": "nantai.viewer-camera-pose.v1",
        "position": {"east": 1.0, "north": -2, "up": 1.5},
        "look_at": {"east": 0, "north": 10.0, "up": 1},
    }

    assert viewer_camera_pose_id(pose) == (
        "pose-6488fe6fd0d6111852489a7fa16ca9be"
        "0554f46562c43dfc0a7edfde65f36a51"
    )


def _png(width: int = 4, height: int = 3) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    scanline = b"\x00" + b"\x20\x40\x60" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(scanline * height))
        + chunk(b"IEND", b"")
    )


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


def _capture_binding(root, relative: str, payload: bytes) -> ViewerCaptureArtifactBinding:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return ViewerCaptureArtifactBinding(
        path=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
    )


def _stable_executable(role: str, label: str) -> StableViewerExecutableObservation:
    payload = label.encode("ascii")
    snapshot = ViewerExecutableSnapshot(
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        device_id="1",
        file_id="2",
        mtime_ns="3",
        mode=0o100755,
        executable=True,
    )
    return StableViewerExecutableObservation(
        role=role,
        before=snapshot,
        after=snapshot,
    )


def _report_v2(tmp_path) -> ViewerPerformanceReportV2:
    policy = _policy()
    report = _report()
    artifacts = {
        "scene_manifest": _capture_binding(
            tmp_path,
            "imported/manifest.json",
            SCENE_MANIFEST_BYTES,
        ),
        "viewer_policy": _capture_binding(
            tmp_path,
            "viewer/policy.json",
            canonical_viewer_performance_policy_bytes(policy),
        ),
        "camera_set": _capture_binding(
            tmp_path,
            "viewer/cameras.json",
            CAMERA_SET_BYTES,
        ),
        "capture_script": _capture_binding(
            tmp_path,
            "viewer/capture_viewer_acceptance.mjs",
            b"export const capture = true;\n",
        ),
        "probe_module": _capture_binding(
            tmp_path,
            "viewer/acceptance-probe.mjs",
            b"export const probe = true;\n",
        ),
        "playwright_package": _capture_binding(
            tmp_path,
            "viewer/playwright-package.json",
            b'{"name":"playwright","version":"1.55.0"}\n',
        ),
    }
    screenshots = []
    for index, pose_id in enumerate(POSES):
        payload = _png(width=4 + index, height=3)
        relative = f"viewer/screenshots/{pose_id}.png"
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        screenshots.append(
            ViewerScreenshotBinding(
                pose_id=pose_id,
                path=relative,
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_length=len(payload),
            )
        )
    return build_viewer_performance_report_v2(
        source_role="production-acceptance",
        scene_manifest_sha256=artifacts["scene_manifest"].sha256,
        viewport_width=report.viewport_width,
        viewport_height=report.viewport_height,
        http_cache=report.http_cache,
        runtime=report.runtime,
        poses=report.poses,
        console_errors=report.console_errors,
        unhandled_rejections=report.unhandled_rejections,
        scene_manifest=artifacts["scene_manifest"],
        viewer_policy=artifacts["viewer_policy"],
        camera_set=artifacts["camera_set"],
        capture_script=artifacts["capture_script"],
        probe_module=artifacts["probe_module"],
        playwright_package=artifacts["playwright_package"],
        node_executable=_stable_executable("node", "node"),
        browser_executable=_stable_executable("browser", "chromium"),
        screenshots=tuple(screenshots),
    )


def test_v2_capture_report_is_content_addressed_and_reopens_every_bound_file(
    tmp_path,
):
    report = _report_v2(tmp_path)
    payload = canonical_viewer_performance_report_bytes(report)

    assert report.schema_id == "nantai.viewer-performance-report.v2"
    assert report.report_id == f"viewer-capture-{report.content_sha256}"
    assert load_viewer_performance_report_bytes(payload) == report
    camera_set = verify_viewer_capture_report(_policy(), report, tmp_path)
    assert isinstance(camera_set, ViewerCameraSetV2)
    assert camera_set.import_receipt_sha256 == IMPORT_RECEIPT_SHA256
    assert (
        camera_set.aligned_registration_sha256
        == ALIGNED_REGISTRATION_SHA256
    )


def test_production_v2_capture_rejects_legacy_unproven_camera_set(tmp_path):
    report = _report_v2(tmp_path)
    legacy_bytes, legacy_pose_ids = _camera_set_bytes(version=1)
    assert legacy_pose_ids == POSES
    legacy_binding = _capture_binding(
        tmp_path,
        report.camera_set.path,
        legacy_bytes,
    )
    fields = {
        field: getattr(report, field)
        for field in ViewerPerformanceReportV2.model_fields
        if field not in {"report_id", "content_sha256"}
    }
    fields["camera_set"] = legacy_binding
    resigned = build_viewer_performance_report_v2(**fields)

    with pytest.raises(
        ViewerAcceptanceError,
        match="production.*camera set|camera set.*v2",
    ):
        verify_viewer_capture_report(_policy(), resigned, tmp_path)


def test_production_v2_capture_rejects_camera_set_for_another_scene(tmp_path):
    report = _report_v2(tmp_path)
    changed_bytes, changed_pose_ids = _camera_set_bytes(
        scene_manifest_sha256="a" * 64,
    )
    assert changed_pose_ids == POSES
    changed_binding = _capture_binding(
        tmp_path,
        report.camera_set.path,
        changed_bytes,
    )
    fields = {
        field: getattr(report, field)
        for field in ViewerPerformanceReportV2.model_fields
        if field not in {"report_id", "content_sha256"}
    }
    fields["camera_set"] = changed_binding
    resigned = build_viewer_performance_report_v2(**fields)

    with pytest.raises(
        ViewerAcceptanceError,
        match="camera set.*scene manifest",
    ):
        verify_viewer_capture_report(_policy(), resigned, tmp_path)


def test_v2_capture_report_rejects_executable_toctou(tmp_path):
    report = _report_v2(tmp_path)
    changed = report.browser_executable.after.model_copy(
        update={"file_id": "999"}
    )

    with pytest.raises(ValueError, match="browser.*changed"):
        ViewerPerformanceReportV2.model_validate(
            {
                **report.model_dump(by_alias=True),
                "browser_executable": {
                    **report.browser_executable.model_dump(),
                    "after": changed.model_dump(),
                },
            }
        )


def test_v2_capture_report_rejects_bound_screenshot_tamper(tmp_path):
    report = _report_v2(tmp_path)
    (tmp_path / report.screenshots[0].path).write_bytes(b"tampered")

    with pytest.raises(ViewerAcceptanceError, match="screenshot.*SHA|changed"):
        verify_viewer_capture_report(_policy(), report, tmp_path)


def test_v2_capture_report_rejects_resigned_camera_set_pose_drift(tmp_path):
    report = _report_v2(tmp_path)
    changed_bytes, changed_pose_ids = _camera_set_bytes(offset=100)
    assert changed_pose_ids != POSES
    camera_set = _capture_binding(
        tmp_path,
        report.camera_set.path,
        changed_bytes,
    )
    fields = {
        field: getattr(report, field)
        for field in ViewerPerformanceReportV2.model_fields
        if field not in {"report_id", "content_sha256"}
    }
    fields["camera_set"] = camera_set
    resigned = build_viewer_performance_report_v2(**fields)

    with pytest.raises(
        ViewerAcceptanceError,
        match="camera set.*pose|pose.*camera set",
    ):
        verify_viewer_capture_report(_policy(), resigned, tmp_path)


def test_production_v1_report_cannot_satisfy_v2_capture_verifier(tmp_path):
    with pytest.raises(ViewerAcceptanceError, match="v2"):
        verify_viewer_capture_report(_policy(), _report(), tmp_path)


def test_v2_capture_loader_rejects_noncanonical_or_duplicate_json(tmp_path):
    report = _report_v2(tmp_path)
    payload = canonical_viewer_performance_report_bytes(report)
    duplicate = payload.replace(
        b'"schema":"nantai.viewer-performance-report.v2"',
        (
            b'"schema":"nantai.viewer-performance-report.v2",'
            b'"schema":"nantai.viewer-performance-report.v2"'
        ),
        1,
    )

    with pytest.raises(ViewerAcceptanceError, match="duplicate"):
        load_viewer_performance_report_bytes(duplicate)
    with pytest.raises(ViewerAcceptanceError, match="canonical"):
        load_viewer_performance_report_bytes(payload + b" ")


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


def test_never_interactive_timeout_keeps_time_unknown_and_fails():
    report = _report()
    pose = report.poses[0].model_copy(
        update={
            "representation": "unavailable",
            "interactive_ms": None,
            "warmup_frame_ms": (),
            "measured_frame_ms": (),
            "timed_out": True,
        }
    )
    timed_out = report.model_copy(
        update={"poses": (pose, *report.poses[1:])}
    )

    decision = derive_viewer_decision(_policy(), timed_out)

    assert decision.accepted is False
    assert any(
        "interactive time unavailable" in gate
        for gate in decision.failed_gates
    )
    assert decision.maximum_observed_interactive_ms == 15_000.0


def test_missing_interactive_time_without_timeout_is_invalid():
    report = _report()
    pose = report.poses[0].model_copy(update={"interactive_ms": None})
    damaged = report.model_copy(
        update={"poses": (pose, *report.poses[1:])}
    )

    with pytest.raises(ViewerAcceptanceError, match="invalid"):
        derive_viewer_decision(_policy(), damaged)


@pytest.mark.parametrize("renderer", ["unknown", "masked", "WebKit WebGL"])
def test_unidentified_gpu_renderer_cannot_pass(renderer):
    report = _report()
    runtime = report.runtime.model_copy(update={"gpu_renderer": renderer})

    decision = derive_viewer_decision(
        _policy(),
        report.model_copy(update={"runtime": runtime}),
    )

    assert decision.accepted is False
    assert any(
        "GPU renderer identity" in gate
        for gate in decision.failed_gates
    )


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


def test_cli_rederives_decision_and_returns_two_for_rejection(
    tmp_path,
    capsys,
):
    from pipeline.viewer_acceptance import main

    policy_path = tmp_path / "policy.json"
    report_path = tmp_path / "report.json"
    decision_path = tmp_path / "decision.json"
    policy_path.write_bytes(
        canonical_viewer_performance_policy_bytes(_policy())
    )
    report_path.write_bytes(
        canonical_viewer_performance_report_bytes(
            _report(representation="dc-point-preview")
        )
    )

    exit_code = main(
        [
            "--policy",
            str(policy_path),
            "--report",
            str(report_path),
            "--decision",
            str(decision_path),
        ]
    )

    assert exit_code == 2
    assert '"accepted":false' in decision_path.read_text(encoding="utf-8")
    assert "REJECTED" in capsys.readouterr().out


def test_cli_v2_requires_and_reopens_capture_evidence_root(
    tmp_path,
    capsys,
):
    from pipeline.viewer_acceptance import main

    report = _report_v2(tmp_path)
    policy_path = tmp_path / report.viewer_policy.path
    report_path = tmp_path / "viewer/report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(
        canonical_viewer_performance_report_bytes(report)
    )

    assert main(
        [
            "--policy",
            str(policy_path),
            "--report",
            str(report_path),
        ]
    ) == 2
    assert "evidence root" in capsys.readouterr().out

    decision_path = tmp_path / "viewer/decision.json"
    assert main(
        [
            "--policy",
            str(policy_path),
            "--report",
            str(report_path),
            "--evidence-root",
            str(tmp_path),
            "--decision",
            str(decision_path),
        ]
    ) == 0
    assert '"accepted":true' in decision_path.read_text(
        encoding="ascii"
    )


# ============================================================
# RED → GREEN: CLI evidence read and decision write boundary
# ============================================================


def _stat_with_reparse(observed):
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_ctime_ns=observed.st_ctime_ns,
        st_file_attributes=getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ),
    )


def test_read_evidence_bytes_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b"x" * 100)
    original_lstat = Path.lstat

    def oversized_lstat(path):
        observed = original_lstat(path)
        if path == evidence:
            return SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_mode=observed.st_mode,
                st_size=viewer_acceptance_module._MAX_CLI_EVIDENCE_BYTES + 1,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
                st_file_attributes=getattr(observed, "st_file_attributes", 0),
            )
        return observed

    monkeypatch.setattr(Path, "lstat", oversized_lstat)
    with pytest.raises(ViewerAcceptanceError, match="bounded regular file"):
        viewer_acceptance_module._read_evidence_bytes(
            evidence,
            label="test",
        )


def test_read_evidence_bytes_rejects_descriptor_after_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b'{"valid":true}')
    original_fstat = os.fstat
    calls = 0

    def drifting_fstat(fd):
        nonlocal calls
        calls += 1
        observed = original_fstat(fd)
        return _stat_with_reparse(observed) if calls == 2 else observed

    monkeypatch.setattr(
        viewer_acceptance_module.os, "fstat", drifting_fstat
    )
    with pytest.raises(
        ViewerAcceptanceError, match="changed while being read"
    ):
        viewer_acceptance_module._read_evidence_bytes(
            evidence,
            label="test",
        )
    assert calls == 2


def test_read_evidence_bytes_rejects_path_after_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b'{"valid":true}')
    original_lstat = Path.lstat
    evidence_calls = [0]

    def swapping_lstat(self):
        observed = original_lstat(self)
        if self == evidence:
            evidence_calls[0] += 1
            if evidence_calls[0] >= 2:
                return SimpleNamespace(
                    st_dev=observed.st_dev,
                    st_ino=observed.st_ino + 1,
                    st_mode=observed.st_mode,
                    st_size=observed.st_size,
                    st_mtime_ns=observed.st_mtime_ns,
                    st_ctime_ns=observed.st_ctime_ns,
                    st_file_attributes=getattr(
                        observed, "st_file_attributes", 0
                    ),
                )
        return observed

    monkeypatch.setattr(Path, "lstat", swapping_lstat)
    with pytest.raises(
        ViewerAcceptanceError,
        match="changed (before read|while being read)",
    ):
        viewer_acceptance_module._read_evidence_bytes(
            evidence,
            label="test",
        )


def test_read_evidence_bytes_rejects_symlink(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    target = tmp_path / "real.json"
    target.write_bytes(b'{"valid":true}')
    link = tmp_path / "policy.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    with pytest.raises(
        ViewerAcceptanceError, match="bounded regular file"
    ):
        viewer_acceptance_module._read_evidence_bytes(
            link,
            label="test",
        )


def test_read_evidence_bytes_oserror_does_not_leak_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b'{"valid":true}')

    def raising_open(path, flags):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(
        viewer_acceptance_module.os, "open", raising_open
    )
    with pytest.raises(ViewerAcceptanceError, match="cannot be read") as exc_info:
        viewer_acceptance_module._read_evidence_bytes(
            evidence,
            label="test",
        )
    assert str(tmp_path) not in str(exc_info.value)


def test_write_decision_noreplace_rejects_existing_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "decision.json"
    destination.write_bytes(b'{"old":true}')

    with pytest.raises(
        ViewerAcceptanceError, match="already exists"
    ):
        viewer_acceptance_module._write_decision_noreplace(
            destination,
            b'{"accepted":true}\n',
        )


def test_write_decision_noreplace_rejects_symlink_destination(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    target = tmp_path / "real.json"
    target.write_bytes(b'{"old":true}')
    link = tmp_path / "decision.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(
        ViewerAcceptanceError, match="already exists"
    ):
        viewer_acceptance_module._write_decision_noreplace(
            link,
            b'{"accepted":true}\n',
        )


def test_write_decision_noreplace_publishes_atomic_payload(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "decision.json"
    payload = b'{"accepted":true}\n'

    viewer_acceptance_module._write_decision_noreplace(
        destination,
        payload,
    )

    assert destination.read_bytes() == payload
    # No staging file left behind
    staging_files = list(tmp_path.glob(".decision.json.*.staging"))
    assert staging_files == []


def test_write_decision_noreplace_rejects_parent_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "decision.json"

    monkeypatch.setattr(
        viewer_acceptance_module,
        "matches_real_directory_identity",
        lambda path, expected: False,
    )

    with pytest.raises(
        ViewerAcceptanceError, match="parent changed before write"
    ):
        viewer_acceptance_module._write_decision_noreplace(
            destination,
            b'{"accepted":true}\n',
        )
    assert not destination.exists()


def test_read_evidence_bytes_rejects_ancestor_reparse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """An ancestor reparse point must be rejected before reading."""
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b'{"valid":true}')
    sentinel = tmp_path / "ancestor-reparse"

    def fake_first_linklike_path(root, leaf):
        return sentinel

    monkeypatch.setattr(
        viewer_acceptance_module,
        "first_linklike_path",
        fake_first_linklike_path,
        raising=False,
    )
    with pytest.raises(
        ViewerAcceptanceError,
        match="bounded regular file",
    ):
        viewer_acceptance_module._read_evidence_bytes(
            evidence,
            label="test",
        )


def test_write_decision_re_verifies_parent_identity_after_open() -> None:
    """RED->GREEN: parent identity must be re-verified AFTER os.open(staging).

    ``matches_real_directory_identity`` checks the parent by path (lstat)
    before ``os.open(staging)`` opens by path.  ``O_NOFOLLOW`` only protects
    the final component — ancestor symlinks are followed, so a parent swap
    between the identity check and the open redirects the staging file.  A
    post-open re-verification is required to close this TOCTOU.
    """
    source = inspect.getsource(
        viewer_acceptance_module._write_decision_noreplace
    )
    open_index = source.index("staging_fd = os.open(")
    after_open = source[open_index:]
    assert "matches_real_directory_identity" in after_open, (
        "parent directory identity must be re-verified AFTER os.open(staging) "
        "to close the TOCTOU between path-based identity check and path-based open"
    )


def test_stable_bound_file_tolerates_cross_surface_mode_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: fstat/lstat st_mode permission bits may differ on Windows.

    ``_stable_bound_file`` compares full ``st_mode`` across surfaces (fstat vs
    lstat).  On Windows, permission bits can legitimately differ between
    path-surface and descriptor-surface stat for the same file.  The
    cross-surface comparison must use only the file type (``S_IFMT``), not
    the full ``st_mode``, matching the pattern in ``_read_evidence_bytes``.
    """
    payload = b'{"artifact":true}\n'
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_bytes(payload)

    binding = ViewerCaptureArtifactBinding(
        path="artifact.json",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
    )

    original_fstat = os.fstat

    def mode_drifting_fstat(fd):
        observed = original_fstat(fd)
        # Flip permission bits but keep file type (S_IFMT) identical
        new_mode = (observed.st_mode & ~0o777) | 0o600
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_mode=new_mode,
            st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns,
            st_ctime_ns=observed.st_ctime_ns,
            st_file_attributes=getattr(observed, "st_file_attributes", 0),
        )

    monkeypatch.setattr(
        viewer_acceptance_module.os, "fstat", mode_drifting_fstat
    )

    result = viewer_acceptance_module._stable_bound_file(
        tmp_path,
        binding,
        label="test artifact",
    )
    assert result == payload
