"""P1 end-to-end canary: cloud emit → prepare_import caller closure.

Proves the full caller loop works with a synthetic small scene:

  1. Build a fake cloud workspace (images, PLY, config.yml, training.log).
  2. Emit registration-request.json + quality-report.json via CLI.
  3. Emit training-request.json + training-result.json via CLI.
  4. prepare_import consumes all four → appends trusted-prefix evidence.
  5. Adversarial: tamper any input byte → fail-closed.

This canary only proves the **mechanism** works end-to-end.  It does NOT
prove cloud training is real, the photos are real, or the geometry is
metric.  See HANDOFF-GLM-005 §3 P1.

See: handoff/REVIEW-CODEX-022-glm-registration-training-trust-contracts.md
See: handoff/HANDOFF-GLM-005-current-gap-and-priority.md
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraIntrinsics,
    CameraPose,
    CaptureSession,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    GeoAlignment,
    Handedness,
    MetricStatus,
    RegistrationResult,
)
from pipeline.registration_quality import (
    RegistrationQualityPolicy,
    build_registration_quality_report,
)

_ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# Workspace helpers
# ============================================================

def _build_cloud_workspace(tmp_path: Path) -> dict[str, Path]:
    """Build a fake cloud workspace: images dir, PLY, config.yml, training.log."""
    rng = np.random.default_rng(31)
    n = 60
    ply = tmp_path / "cloud" / "export" / "point_cloud.ply"
    ply.parent.mkdir(parents=True)
    GaussianScene(
        xyz=rng.uniform(0, 5, (n, 3)),
        rgb=rng.uniform(0, 1, (n, 3)),
    ).save_ply(ply, flavor="3dgs")

    images = tmp_path / "cloud" / "photos"
    images.mkdir(parents=True)
    for i in range(15):
        (images / f"IMG_{i:04d}.jpg").write_bytes(f"fake-jpeg-{i}".encode())

    config = tmp_path / "cloud" / "outputs" / "config.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "trainer: splatfacto\nmax_res: 800\nsteps: 10000\n",
        encoding="utf-8")
    log = tmp_path / "cloud" / "training.log"
    log.write_text("step 0 loss 0.5\nstep 9999 loss 0.01\nDONE\n",
                   encoding="utf-8")
    return {"images": images, "ply": ply, "config": config, "log": log}


def _local_frame() -> CoordinateFrame:
    return CoordinateFrame(
        frame_id="sfm-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SFM,
    )


def _build_registration_result(
    *, registered: int = 15, total: int = 15,
) -> RegistrationResult:
    """Build a mock-engine RegistrationResult (no COLMAP sparse dir needed)."""
    all_images = [f"IMG_{i:04d}.jpg" for i in range(total)]
    intr = CameraIntrinsics(width=1920, height=1080, fx=1000.0, fy=1000.0,
                            cx=960.0, cy=540.0)
    poses = [
        CameraPose(
            image=f"IMG_{i:04d}.jpg", session_id="s0",
            quat_wxyz=[1.0, 0.0, 0.0, 0.0], t_xyz=[float(i), 0.0, 0.0],
            intrinsics=intr,
        )
        for i in range(registered)
    ]
    return RegistrationResult(
        schema_version=2, engine="mock", pose_frame=_local_frame(),
        world_frame=None, alignment_status=AlignmentStatus.UNALIGNED,
        sessions=[CaptureSession(session_id="s0", kind="photo_batch",
                                 source="test", images=all_images)],
        poses=poses,
    )


def _write_rq_artifacts(tmp_path: Path, *, registered: int = 15,
                        total: int = 15,
                        ) -> tuple[Path, Path, Path]:
    """Write registration.json + policy.json + quality-report.json to disk."""
    reg = _build_registration_result(registered=registered, total=total)
    reg_json = tmp_path / "rq" / "registration.json"
    reg_json.parent.mkdir(parents=True)
    reg_bytes = reg.model_dump_json(indent=2).encode("utf-8")
    reg_json.write_bytes(reg_bytes)

    policy = RegistrationQualityPolicy(
        min_registered_count=10, min_registered_ratio=0.7,
        min_session_coverage_ratio=0.6, max_unregistered_consecutive_run=5,
        min_largest_connected_model_share=0.6,
    )
    policy_json = tmp_path / "rq" / "policy.json"
    policy_json.write_bytes(policy.model_dump_json(indent=2).encode("utf-8"))

    report = build_registration_quality_report(
        registration=reg, registration_json_bytes=reg_bytes,
        policy=policy, invocation_succeeded=True,
    )
    report_json = tmp_path / "rq" / "quality-report.json"
    report_json.write_bytes(report.model_dump_json(indent=2).encode("utf-8"))
    return reg_json, policy_json, report_json


def _emit_training_manifests(
    tmp_path: Path, ws: dict[str, Path],
) -> tuple[Path, Path, str]:
    """Emit training-request.json + training-result.json via CLI."""
    out = tmp_path / "cloud"
    subprocess.run(
        [sys.executable, "scripts/emit_training_provenance.py",
         "request",
         "--input", f"capture_manifest:{ws['images']}",
         "--config-yml", str(ws["config"]),
         "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
         "--max-resolution", "800", "--total-steps", "10000", "--seed", "42",
         "--request-id", "req-canary-001",
         "--output", str(out / "training-request.json")],
        cwd=_ROOT, capture_output=True, text=True, check=True)
    subprocess.run(
        [sys.executable, "scripts/emit_training_provenance.py",
         "result",
         "--request", str(out / "training-request.json"),
         "--ply", str(ws["ply"]), "--config-yml", str(ws["config"]),
         "--log", str(ws["log"]),
         "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
         "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
         "--cuda-version", "11.8", "--driver-version", "525.60.13",
         "--result-id", "res-canary-001",
         "--started-at", "2026-07-23T01:00:00Z",
         "--finished-at", "2026-07-23T02:30:00Z",
         "--output", str(out / "training-result.json")],
        cwd=_ROOT, capture_output=True, text=True, check=True)

    from pipeline.training_provenance import (
        TrainingResult,
        result_canonical_sha256,
    )
    result = TrainingResult.model_validate_json(
        (out / "training-result.json").read_text(encoding="utf-8"))
    return (out / "training-request.json",
            out / "training-result.json",
            result_canonical_sha256(result))


def _run_prepare_import(
    ply: Path, out_dir: Path, *extra: str,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/prepare_import.py", str(ply),
         "--out-dir", str(out_dir), *extra],
        cwd=_ROOT, capture_output=True, text=True)


def _evidence(reg_path: Path) -> tuple[str, ...]:
    reg = RegistrationResult.model_validate_json(
        reg_path.read_text(encoding="utf-8"))
    return reg.pose_frame.evidence


# ============================================================
# End-to-end canary: full caller closure
# ============================================================

class TestP1CanaryE2E:
    """Prove cloud emit → prepare_import → trusted evidence end-to-end."""

    def test_mock_registration_with_accepted_quality_yields_content_only(
        self, tmp_path,
    ):
        """Full path: emit RQ + training → prepare_import → content-only.

        Mock engine: quality_accepted=True but training_allowed=False (mock
        can't be trusted for training).  So even with accepted RQ, the
        evidence is content-only receipt, not trusted prefix.
        """
        ws = _build_cloud_workspace(tmp_path)
        reg_json, policy_json, report_json = _write_rq_artifacts(tmp_path)
        req_path, res_path, result_sha = _emit_training_manifests(tmp_path, ws)
        out_dir = tmp_path / "recon"

        proc = _run_prepare_import(
            ws["ply"], out_dir,
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
        )
        assert proc.returncode == 0, proc.stderr

        evidence = _evidence(out_dir / "registration.json")
        # Mock engine: training_allowed=False → content-only, not trusted.
        expected = f"training_content_closed.v1={result_sha}"
        assert expected in evidence, (
            f"missing content-only receipt {expected!r}; got {evidence}")
        trusted = f"training_provenance.v1={result_sha}"
        assert trusted not in evidence, (
            f"mock registration must never yield trusted prefix: {evidence}")
        # Base evidence still present.
        assert "external-3dgs-import" in evidence
        # Honest boundary: no metric/aligned upgrade.
        reg = RegistrationResult.model_validate_json(
            (out_dir / "registration.json").read_text(encoding="utf-8"))
        assert reg.pose_frame.metric_status is MetricStatus.ARBITRARY
        assert reg.pose_frame.geo_aligned is GeoAlignment.UNALIGNED

    def test_content_only_receipt_without_registration_quality(self, tmp_path):
        """Without RQ, only content-only receipt is appended (not trusted)."""
        ws = _build_cloud_workspace(tmp_path)
        req_path, res_path, result_sha = _emit_training_manifests(tmp_path, ws)
        out_dir = tmp_path / "recon"

        proc = _run_prepare_import(
            ws["ply"], out_dir,
            "--training-request", str(req_path),
            "--training-result", str(res_path),
        )
        assert proc.returncode == 0, proc.stderr

        evidence = _evidence(out_dir / "registration.json")
        expected = f"training_content_closed.v1={result_sha}"
        assert expected in evidence, (
            f"missing content-only receipt {expected!r}; got {evidence}")
        # Trusted prefix must NOT be present.
        trusted = f"training_provenance.v1={result_sha}"
        assert trusted not in evidence, (
            f"trusted prefix without RQ: {evidence}")

    def test_rejected_registration_quality_yields_content_only(self, tmp_path):
        """RQ present but rejected (too few registered) → content-only."""
        ws = _build_cloud_workspace(tmp_path)
        # 3/15 registered → fails policy (min 10, ratio 0.7).
        reg_json, policy_json, report_json = _write_rq_artifacts(
            tmp_path, registered=3, total=15)
        req_path, res_path, result_sha = _emit_training_manifests(tmp_path, ws)
        out_dir = tmp_path / "recon"

        proc = _run_prepare_import(
            ws["ply"], out_dir,
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
        )
        assert proc.returncode == 0, proc.stderr

        evidence = _evidence(out_dir / "registration.json")
        # quality_accepted=False → content-only receipt, not trusted.
        expected = f"training_content_closed.v1={result_sha}"
        assert expected in evidence, (
            f"missing content-only receipt {expected!r}; got {evidence}")
        trusted = f"training_provenance.v1={result_sha}"
        assert trusted not in evidence, (
            f"trusted prefix with rejected RQ: {evidence}")


# ============================================================
# Adversarial: tamper any input → fail-closed
# ============================================================

class TestP1CanaryAdversarial:
    """Tamper any input byte → prepare_import must fail-closed."""

    def test_tampered_ply_fails_closed(self, tmp_path):
        ws = _build_cloud_workspace(tmp_path)
        reg_json, policy_json, report_json = _write_rq_artifacts(tmp_path)
        req_path, res_path, _ = _emit_training_manifests(tmp_path, ws)
        # Tamper PLY bytes after manifests were bound.
        ws["ply"].write_bytes(ws["ply"].read_bytes() + b"\x00TAMPER")

        proc = _run_prepare_import(
            ws["ply"], tmp_path / "recon",
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
        )
        assert proc.returncode == 1, proc.stderr
        assert "TRAINING-PROVENANCE-FAIL" in proc.stderr

    def test_tampered_config_fails_closed(self, tmp_path):
        ws = _build_cloud_workspace(tmp_path)
        reg_json, policy_json, report_json = _write_rq_artifacts(tmp_path)
        req_path, res_path, _ = _emit_training_manifests(tmp_path, ws)
        # Tamper config bytes after manifests were bound.
        ws["config"].write_bytes(b"tampered\n")

        proc = _run_prepare_import(
            ws["ply"], tmp_path / "recon",
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
        )
        assert proc.returncode == 1, proc.stderr
        assert "TRAINING-PROVENANCE-FAIL" in proc.stderr

    def test_tampered_log_fails_closed(self, tmp_path):
        ws = _build_cloud_workspace(tmp_path)
        reg_json, policy_json, report_json = _write_rq_artifacts(tmp_path)
        req_path, res_path, _ = _emit_training_manifests(tmp_path, ws)
        # Tamper log bytes.
        ws["log"].write_bytes(b"tampered log\n")

        proc = _run_prepare_import(
            ws["ply"], tmp_path / "recon",
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
        )
        assert proc.returncode == 1, proc.stderr
        assert "TRAINING-PROVENANCE-FAIL" in proc.stderr

    def test_tampered_quality_report_fails_closed(self, tmp_path):
        ws = _build_cloud_workspace(tmp_path)
        reg_json, policy_json, report_json = _write_rq_artifacts(tmp_path)
        req_path, res_path, _ = _emit_training_manifests(tmp_path, ws)
        # Tamper quality report: flip quality_accepted.
        report_data = json.loads(report_json.read_text(encoding="utf-8"))
        report_data["quality_accepted"] = not report_data["quality_accepted"]
        report_json.write_text(
            json.dumps(report_data, indent=2), encoding="utf-8")

        proc = _run_prepare_import(
            ws["ply"], tmp_path / "recon",
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
        )
        assert proc.returncode == 1, proc.stderr

    def test_tampered_registration_json_fails_closed(self, tmp_path):
        ws = _build_cloud_workspace(tmp_path)
        reg_json, policy_json, report_json = _write_rq_artifacts(tmp_path)
        req_path, res_path, _ = _emit_training_manifests(tmp_path, ws)
        # Tamper registration.json: add an extra pose.
        reg_data = json.loads(reg_json.read_text(encoding="utf-8"))
        reg_data["poses"].append(reg_data["poses"][0])
        reg_json.write_text(
            json.dumps(reg_data, indent=2), encoding="utf-8")

        proc = _run_prepare_import(
            ws["ply"], tmp_path / "recon",
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
        )
        assert proc.returncode == 1, proc.stderr

    def test_tampered_input_images_fails_closed(self, tmp_path):
        """Tamper the capture_manifest input (images dir) after binding."""
        ws = _build_cloud_workspace(tmp_path)
        reg_json, policy_json, report_json = _write_rq_artifacts(tmp_path)
        req_path, res_path, _ = _emit_training_manifests(tmp_path, ws)
        # Tamper an input image after manifests were bound.
        (ws["images"] / "IMG_0000.jpg").write_bytes(b"tampered")

        proc = _run_prepare_import(
            ws["ply"], tmp_path / "recon",
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
        )
        assert proc.returncode == 1, proc.stderr
        assert "TRAINING-PROVENANCE-FAIL" in proc.stderr
