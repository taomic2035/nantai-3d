"""prepare_import training provenance handshake integration.

Drives the actual ``scripts/prepare_import.py`` CLI to verify that
``--training-result`` / ``--training-request`` content-closure verification
either appends the ``training_provenance.v1=<sha>`` evidence string (on success)
or fails closed (on mismatch).  The evidence string must NOT change
``metric_status`` or ``geo_aligned`` — the PLY stays ``sfm-local`` / ``preview-only``.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import numpy as np

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import (
    GeoAlignment,
    MetricStatus,
    RegistrationResult,
    SplatInput,
)
from pipeline.training_provenance import (
    GpuEnvironment,
    TrainingConfig,
    TrainingInputBinding,
    TrainingOutputBinding,
    TrainingRequest,
    TrainingResult,
    TrainingStatus,
    request_canonical_sha256,
    result_canonical_sha256,
)

_ROOT = Path(__file__).resolve().parent.parent
_CAPTURE_SHA = "a" * 64
_CONFIG_SHA = "b" * 64
_LOG_SHA = "c" * 64


def _build_ply(tmp_path: Path) -> Path:
    """A small valid 3DGS PLY whose bytes are stable for SHA computation."""
    rng = np.random.default_rng(11)
    ply = tmp_path / "trained.ply"
    GaussianScene(
        rng.uniform(0, 5, (80, 3)), rng.uniform(0, 1, (80, 3))
    ).save_ply(ply, flavor="3dgs")
    return ply


def _build_manifests(ply: Path, tmp_path: Path) -> tuple[Path, Path, str]:
    """Build a matched training-request.json + training-result.json for ``ply``.

    Returns (request_path, result_path, result_sha).
    """
    ply_bytes = ply.read_bytes()
    ply_sha = hashlib.sha256(ply_bytes).hexdigest()

    request = TrainingRequest(
        request_id="req-canary-001",
        created_at_utc_iso="2026-07-23T00:00:00Z",
        input_bindings=(
            TrainingInputBinding(
                artifact_kind="capture_manifest",
                artifact_sha256=_CAPTURE_SHA,
                artifact_path="photos/capture_manifest.json",
                artifact_size_bytes=128,
            ),
        ),
        training_config=TrainingConfig(
            trainer_name="nerfstudio-splatfacto",
            trainer_version="0.1.0",
            max_resolution=800,
            total_steps=10000,
            random_seed=42,
        ),
        expected_output_format="inria-3dgs-ply",
    )

    result = TrainingResult(
        request_canonical_sha256=request_canonical_sha256(request),
        result_id="res-canary-001",
        started_at_utc_iso="2026-07-23T01:00:00Z",
        finished_at_utc_iso="2026-07-23T02:30:00Z",
        actual_input_shas=(_CAPTURE_SHA,),
        actual_trainer_name="nerfstudio-splatfacto",
        actual_trainer_version="0.1.0",
        actual_config_sha256=_CONFIG_SHA,
        gpu_environment=GpuEnvironment(
            gpu_name="Tesla T4",
            gpu_memory_mb=15109,
            cuda_version="11.8",
            driver_version="525.60.13",
        ),
        output_bindings=(
            TrainingOutputBinding(
                artifact_kind="trained_ply",
                artifact_sha256=ply_sha,
                artifact_path="export/point_cloud.ply",
                artifact_size_bytes=len(ply_bytes),
                gaussian_count=80,
                sh_degree=3,
            ),
        ),
        primary_ply_sha256=ply_sha,
        training_status=TrainingStatus(state="completed", exit_code=0),
        training_log_sha256=_LOG_SHA,
    )

    req_path = tmp_path / "training-request.json"
    res_path = tmp_path / "training-result.json"
    req_path.write_text(request.model_dump_json(indent=2), encoding="utf-8")
    res_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return req_path, res_path, result_canonical_sha256(result)


def _run(ply: Path, out_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/prepare_import.py", str(ply),
         "--out-dir", str(out_dir), *extra],
        cwd=_ROOT, capture_output=True, text=True)


def _evidence(reg_path: Path) -> tuple[str, ...]:
    reg = RegistrationResult.model_validate_json(
        reg_path.read_text(encoding="utf-8"))
    return reg.pose_frame.evidence


class TestTrainingProvenanceIntegration:
    def test_success_appends_evidence_without_metric_upgrade(self, tmp_path):
        ply = _build_ply(tmp_path)
        req_path, res_path, result_sha = _build_manifests(ply, tmp_path)
        out_dir = tmp_path / "recon"

        proc = _run(ply, out_dir,
                    "--training-result", str(res_path),
                    "--training-request", str(req_path))
        assert proc.returncode == 0, proc.stderr

        evidence = _evidence(out_dir / "registration.json")
        expected = f"training_provenance.v1={result_sha}"
        assert expected in evidence, f"missing evidence {expected!r}; got {evidence}"

        # Honest boundary: evidence must NOT smuggle a metric / aligned upgrade.
        reg = RegistrationResult.model_validate_json(
            (out_dir / "registration.json").read_text(encoding="utf-8"))
        frame = reg.pose_frame
        assert frame.frame_id == "sfm-local"
        assert frame.metric_status is MetricStatus.ARBITRARY
        assert frame.geo_aligned is GeoAlignment.UNALIGNED
        # Base evidence still present alongside the new provenance tag.
        assert "external-3dgs-import" in evidence

    def test_ply_mismatch_fails_closed(self, tmp_path):
        ply = _build_ply(tmp_path)
        req_path, res_path, _ = _build_manifests(ply, tmp_path)
        # Tamper with PLY bytes AFTER manifests were bound to the original SHA.
        ply.write_bytes(ply.read_bytes() + b"\x00TAMPER")

        proc = _run(ply, tmp_path / "recon",
                    "--training-result", str(res_path),
                    "--training-request", str(req_path))
        assert proc.returncode == 1, proc.stderr
        assert "TRAINING-PROVENANCE-FAIL" in proc.stderr

    def test_allow_unverified_training_bypasses_but_no_evidence(self, tmp_path):
        ply = _build_ply(tmp_path)
        req_path, res_path, _ = _build_manifests(ply, tmp_path)
        ply.write_bytes(ply.read_bytes() + b"\x00TAMPER")
        out_dir = tmp_path / "recon"

        proc = _run(ply, out_dir,
                    "--training-result", str(res_path),
                    "--training-request", str(req_path),
                    "--allow-unverified-training")
        assert proc.returncode == 0, proc.stderr
        assert "allow-unverified-training" in proc.stderr

        evidence = _evidence(out_dir / "registration.json")
        assert not any(s.startswith("training_provenance.v1=") for s in evidence), (
            f"--allow-unverified-training must NOT append evidence; got {evidence}"
        )
        assert "external-3dgs-import" in evidence

    def test_training_result_without_request_errors(self, tmp_path):
        ply = _build_ply(tmp_path)
        _req_path, res_path, _ = _build_manifests(ply, tmp_path)

        proc = _run(ply, tmp_path / "recon",
                    "--training-result", str(res_path))
        assert proc.returncode != 0
        assert "training-request" in (proc.stderr + proc.stdout)

    def test_failed_training_run_fails_closed(self, tmp_path):
        # A failed training run cannot produce a valid PLY; the handshake must
        # reject it even though the manifest is internally consistent.
        ply = _build_ply(tmp_path)
        req_path, res_path, _ = _build_manifests(ply, tmp_path)

        # Rewrite result as a FAILED run whose primary_ply_sha256 is the empty
        # sentinel — but the actual PLY bytes are non-empty, so content closure
        # breaks (PLY bytes mismatch).
        import json

        result_doc = json.loads(res_path.read_text(encoding="utf-8"))
        empty_sha = hashlib.sha256(b"").hexdigest()
        result_doc["primary_ply_sha256"] = empty_sha
        result_doc["output_bindings"] = []
        result_doc["training_status"] = {
            "state": "failed", "exit_code": 1, "error_message": "OOM",
        }
        res_path.write_text(
            json.dumps(result_doc, indent=2), encoding="utf-8")

        proc = _run(ply, tmp_path / "recon",
                    "--training-result", str(res_path),
                    "--training-request", str(req_path))
        assert proc.returncode == 1, proc.stderr
        assert "TRAINING-PROVENANCE-FAIL" in proc.stderr

    def test_synthetic_combined_with_training_evidence(self, tmp_path):
        # --synthetic + --training-result must both apply: SYNTHETIC provenance
        # AND the training_provenance evidence tag (only-degrade rule preserved).
        ply = _build_ply(tmp_path)
        req_path, res_path, result_sha = _build_manifests(ply, tmp_path)
        out_dir = tmp_path / "recon"

        proc = _run(ply, out_dir,
                    "--synthetic",
                    "--training-result", str(res_path),
                    "--training-request", str(req_path))
        assert proc.returncode == 0, proc.stderr

        reg = RegistrationResult.model_validate_json(
            (out_dir / "registration.json").read_text(encoding="utf-8"))
        splat = SplatInput.model_validate_json(
            (out_dir / "splat-input.json").read_text(encoding="utf-8"))
        assert reg.pose_frame.frame_id == "synthetic-local"
        assert "synthetic-source-declared" in reg.pose_frame.evidence
        assert f"training_provenance.v1={result_sha}" in reg.pose_frame.evidence
        # source_frame must remain byte-identical to registration frame.
        assert splat.source_frame == reg.pose_frame
