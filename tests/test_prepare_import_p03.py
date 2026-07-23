"""prepare_import.py P0.3 CLI integration tests — hardened three-tier evidence.

Tests the full CLI path: cloud emits manifests → prepare_import verifies
content closure + registration quality → appends the correct evidence tier.

Adversarial tests prove fail-closed on tampered PLY / config / log / input /
quality report, and reject asymmetric argument combinations.

See: handoff/REVIEW-CODEX-022-glm-registration-training-trust-contracts.md
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
from pipeline.training_provenance import result_canonical_sha256

_ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# Workspace helpers
# ============================================================

def _build_cloud_workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path, int]:
    """Build a fake cloud workspace: images dir, PLY, config.yml, training.log."""
    rng = np.random.default_rng(23)
    n = 120
    ply = tmp_path / "cloud" / "export" / "point_cloud.ply"
    ply.parent.mkdir(parents=True)
    scene = GaussianScene(
        xyz=rng.uniform(0, 5, (n, 3)),
        rgb=rng.uniform(0, 1, (n, 3)),
        sh_rest=rng.uniform(-0.1, 0.1, (n, 45)),
    )
    scene.save_ply(ply, flavor="3dgs")

    images = tmp_path / "cloud" / "photos"
    images.mkdir(parents=True)
    (images / "IMG_0001.jpg").write_bytes(b"fake-jpeg-1")
    (images / "IMG_0002.jpg").write_bytes(b"fake-jpeg-2")

    config = tmp_path / "cloud" / "outputs" / "config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("trainer: splatfacto\nmax_res: 800\nsteps: 10000\n",
                      encoding="utf-8")
    log = tmp_path / "cloud" / "training.log"
    log.write_text("step 0 loss 0.5\nstep 9999 loss 0.01\nDONE\n",
                   encoding="utf-8")
    return images, ply, config, log, n


def _emit_manifests(
    tmp_path: Path, images: Path, ply: Path, config: Path, log: Path,
) -> Path:
    """Emit training-request.json + training-result.json via CLI."""
    out = tmp_path / "cloud"
    subprocess.run(
        [sys.executable, "scripts/emit_training_provenance.py",
         "request",
         "--input", f"capture_manifest:{images}",
         "--config-yml", str(config),
         "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
         "--max-resolution", "800", "--total-steps", "10000", "--seed", "42",
         "--request-id", "req-p03-001",
         "--output", str(out / "training-request.json")],
        cwd=_ROOT, capture_output=True, text=True, check=True)
    subprocess.run(
        [sys.executable, "scripts/emit_training_provenance.py",
         "result",
         "--request", str(out / "training-request.json"),
         "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
         "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
         "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
         "--cuda-version", "11.8", "--driver-version", "525.60.13",
         "--result-id", "res-p03-001",
         "--started-at", "2026-07-23T01:00:00Z",
         "--output", str(out / "training-result.json")],
        cwd=_ROOT, capture_output=True, text=True, check=True)
    return out


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


def _make_intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(width=1920, height=1080, fx=1000.0, fy=1000.0,
                            cx=960.0, cy=540.0)


def _build_registration_result(
    *, registered: int, total: int, session_id: str = "s0",
) -> RegistrationResult:
    """Build a minimal mock-engine RegistrationResult."""
    all_images = [f"img{i:03d}.jpg" for i in range(total)]
    poses = [
        CameraPose(
            image=f"img{i:03d}.jpg",
            session_id=session_id,
            quat_wxyz=[1.0, 0.0, 0.0, 0.0],
            t_xyz=[float(i), 0.0, 0.0],
            intrinsics=_make_intrinsics(),
        )
        for i in range(registered)
    ]
    return RegistrationResult(
        schema_version=2,
        engine="mock",
        pose_frame=_local_frame(),
        world_frame=None,
        alignment_status=AlignmentStatus.UNALIGNED,
        sessions=[CaptureSession(
            session_id=session_id, kind="photo_batch", source="test",
            images=all_images,
        )],
        poses=poses,
    )


def _write_registration_quality_artifacts(
    tmp_path: Path, *, registered: int = 15, total: int = 20,
) -> tuple[Path, Path, Path]:
    """Build registration.json + policy.json + quality-report.json on disk.

    Returns (reg_json, policy_json, report_json).
    """
    reg = _build_registration_result(registered=registered, total=total)
    reg_json = tmp_path / "rq" / "registration.json"
    reg_json.parent.mkdir(parents=True)
    reg_bytes = reg.model_dump_json(indent=2).encode("utf-8")
    reg_json.write_bytes(reg_bytes)

    policy = RegistrationQualityPolicy(
        min_registered_count=10,
        min_registered_ratio=0.7,
        min_session_coverage_ratio=0.6,
        max_unregistered_consecutive_run=5,
        min_largest_connected_model_share=0.6,
    )
    policy_json = tmp_path / "rq" / "policy.json"
    policy_json.write_bytes(policy.model_dump_json(indent=2).encode("utf-8"))

    report = build_registration_quality_report(
        registration=reg,
        registration_json_bytes=reg_bytes,
        policy=policy,
        invocation_succeeded=True,
    )
    report_json = tmp_path / "rq" / "quality-report.json"
    report_json.write_bytes(report.model_dump_json(indent=2).encode("utf-8"))
    return reg_json, policy_json, report_json


def _run_prepare_import(ply: Path, out_dir: Path, *extra: str,
                        ) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/prepare_import.py", str(ply),
         "--out-dir", str(out_dir), *extra],
        cwd=_ROOT, capture_output=True, text=True)


def _read_evidence(reg_path: Path) -> tuple[str, ...]:
    reg = RegistrationResult.model_validate_json(
        reg_path.read_text(encoding="utf-8"))
    return reg.pose_frame.evidence


# ============================================================
# Argument symmetry tests
# ============================================================

class TestArgSymmetry:
    def test_training_result_without_request_rejected(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"))
        assert prep.returncode != 0
        assert "配对" in prep.stderr or "pair" in prep.stderr.lower()

    def test_training_request_without_result_rejected(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-request", str(out / "training-request.json"))
        assert prep.returncode != 0
        assert "配对" in prep.stderr or "pair" in prep.stderr.lower()

    def test_registration_quality_without_training_pair_rejected(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        reg_json, policy_json, report_json = _write_registration_quality_artifacts(
            tmp_path)
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json))
        assert prep.returncode != 0
        assert "training-result" in prep.stderr or "training" in prep.stderr.lower()

    def test_partial_registration_quality_args_rejected(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        reg_json, policy_json, report_json = _write_registration_quality_artifacts(
            tmp_path)
        # Only --registration-quality-report, missing the other two.
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"),
            "--registration-quality-report", str(report_json))
        assert prep.returncode != 0
        assert "同时" in prep.stderr or "together" in prep.stderr.lower()


# ============================================================
# Content closure (tampering) tests
# ============================================================

class TestContentClosureTampering:
    def test_tampered_ply_fail_closed(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        # Tamper PLY bytes after emitting the result.
        with ply.open("ab") as f:
            f.write(b"TAMPER")
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"))
        assert prep.returncode != 0

    def test_tampered_config_fail_closed(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        config.write_text("tampered: true\n", encoding="utf-8")
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"))
        assert prep.returncode != 0

    def test_tampered_log_fail_closed(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        log.write_text("TAMPERED LOG\n", encoding="utf-8")
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"))
        assert prep.returncode != 0

    def test_tampered_input_fail_closed(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        # Tamper one of the input images.
        (images / "IMG_0001.jpg").write_bytes(b"tampered-input")
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"))
        assert prep.returncode != 0

    def test_allow_unverified_bypasses_fail_closed(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        with ply.open("ab") as f:
            f.write(b"TAMPER")
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"),
            "--allow-unverified-training")
        assert prep.returncode == 0, prep.stderr[:500]
        evidence = _read_evidence(tmp_path / "recon" / "registration.json")
        # No training evidence should be present.
        assert not any(e.startswith("training_") for e in evidence), \
            f"unverified bypass should produce no training evidence: {evidence}"


# ============================================================
# Three-tier evidence tests
# ============================================================

class TestThreeTierEvidence:
    def test_content_only_receipt_without_registration_quality(self, tmp_path):
        """Training pair only → content-only receipt (NOT trusted prefix)."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"))
        assert prep.returncode == 0, prep.stderr[:500]
        evidence = _read_evidence(tmp_path / "recon" / "registration.json")

        from pipeline.training_provenance import TrainingResult
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))
        result_sha = result_canonical_sha256(result)

        expected = f"training_content_closed.v1={result_sha}"
        assert expected in evidence, f"missing {expected!r}; got {evidence}"
        trusted = f"training_provenance.v1={result_sha}"
        assert trusted not in evidence, \
            f"trusted prefix without registration quality: {evidence}"

    def test_mock_registration_never_yields_trusted_prefix(self, tmp_path):
        """Accepted mock coverage is not permission to trust a training run."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        reg_json, policy_json, report_json = _write_registration_quality_artifacts(
            tmp_path, registered=15, total=20)
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json))
        assert prep.returncode == 0, prep.stderr[:500]
        evidence = _read_evidence(tmp_path / "recon" / "registration.json")

        from pipeline.training_provenance import TrainingResult
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))
        result_sha = result_canonical_sha256(result)

        content_only = f"training_content_closed.v1={result_sha}"
        assert content_only in evidence, \
            f"mock registration must stay content-only: {evidence}"
        trusted = f"training_provenance.v1={result_sha}"
        assert trusted not in evidence, \
            f"mock registration must never yield trusted evidence: {evidence}"

    def test_content_only_when_registration_quality_rejected(self, tmp_path):
        """Registration quality NOT accepted → content-only receipt."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        # Build a registration with only 3/20 registered — below policy.
        reg_json, policy_json, report_json = _write_registration_quality_artifacts(
            tmp_path, registered=3, total=20)
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json))
        assert prep.returncode == 0, prep.stderr[:500]
        evidence = _read_evidence(tmp_path / "recon" / "registration.json")

        from pipeline.training_provenance import TrainingResult
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))
        result_sha = result_canonical_sha256(result)

        # Quality rejected → content-only, not trusted.
        expected = f"training_content_closed.v1={result_sha}"
        assert expected in evidence, f"missing {expected!r}; got {evidence}"
        trusted = f"training_provenance.v1={result_sha}"
        assert trusted not in evidence, \
            f"trusted prefix with rejected quality: {evidence}"

    def test_tampered_quality_report_fail_closed(self, tmp_path):
        """Tampered quality report → fail-closed.

        Build a registration that FAILS the policy (3/20 registered), so the
        derived quality_accepted is False.  Then tamper quality_accepted to
        True — the validator must catch the mismatch.
        """
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = _emit_manifests(tmp_path, images, ply, config, log)
        reg_json, policy_json, report_json = _write_registration_quality_artifacts(
            tmp_path, registered=3, total=20)
        # Tamper the report: flip quality_accepted from False to True.
        report_data = json.loads(report_json.read_text(encoding="utf-8"))
        report_data["quality_accepted"] = True  # lie — derived is False
        report_json.write_text(
            json.dumps(report_data, sort_keys=True), encoding="utf-8")
        prep = _run_prepare_import(
            ply, tmp_path / "recon",
            "--training-result", str(out / "training-result.json"),
            "--training-request", str(out / "training-request.json"),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json))
        assert prep.returncode != 0

    def test_no_evidence_without_training_args(self, tmp_path):
        """No training args → no training evidence at all."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        prep = _run_prepare_import(ply, tmp_path / "recon")
        assert prep.returncode == 0, prep.stderr[:500]
        evidence = _read_evidence(tmp_path / "recon" / "registration.json")
        assert not any(e.startswith("training_") for e in evidence), \
            f"training evidence without training args: {evidence}"
