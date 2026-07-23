"""prepare_import training provenance handshake integration.

Drives the actual ``scripts/prepare_import.py`` CLI to verify that
``--training-result`` / ``--training-request`` content-closure verification
either appends the ``training_content_closed.v1=<sha>`` evidence string (on
success, P0.3 hardened) or fails closed (on mismatch).  The evidence string
must NOT change ``metric_status`` or ``geo_aligned`` — the PLY stays
``sfm-local`` / ``preview-only``.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import UTC, datetime
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
    TrainingRequest,
    build_training_result,
    result_canonical_sha256,
)

_ROOT = Path(__file__).resolve().parent.parent


def _build_ply(tmp_path: Path) -> Path:
    """A small valid 3DGS PLY whose bytes are stable for SHA computation."""
    rng = np.random.default_rng(11)
    ply = tmp_path / "trained.ply"
    GaussianScene(
        rng.uniform(0, 5, (80, 3)), rng.uniform(0, 1, (80, 3))
    ).save_ply(ply, flavor="3dgs")
    return ply


def _build_manifests(
    ply: Path, tmp_path: Path, *, exit_code: int = 0,
    actual_ply_bytes: bytes | None = None,
    error_message: str | None = None,
) -> tuple[Path, Path, str]:
    """Build a matched training-request.json + training-result.json for ``ply``.

    Creates real config.yml, training.log, and capture_manifest.json at the
    paths referenced by the bindings, so prepare_import.py can re-read bytes
    and verify content closure.

    Returns (request_path, result_path, result_sha).
    """
    # Create real input/output files at absolute paths.
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir(exist_ok=True)
    capture_manifest = photos_dir / "capture_manifest.json"
    capture_manifest.write_text('{"version": 1}', encoding="utf-8")

    config = tmp_path / "config.yml"
    config.write_text("trainer: splatfacto\nmax_res: 800\nsteps: 10000\n",
                      encoding="utf-8")

    log = tmp_path / "training.log"
    log.write_text("step 0 loss 0.5\nstep 9999 loss 0.01\nDONE\n",
                   encoding="utf-8")

    capture_bytes = capture_manifest.read_bytes()
    config_bytes = config.read_bytes()
    log_bytes = log.read_bytes()
    if actual_ply_bytes is None:
        actual_ply_bytes = ply.read_bytes()

    capture_sha = hashlib.sha256(capture_bytes).hexdigest()
    config_sha = hashlib.sha256(config_bytes).hexdigest()

    request = TrainingRequest(
        request_id="req-canary-001",
        created_at_utc=datetime(2026, 7, 23, 0, 0, 0, tzinfo=UTC),
        input_bindings=(
            TrainingInputBinding(
                artifact_kind="capture_manifest",
                artifact_sha256=capture_sha,
                artifact_path=str(capture_manifest),
                artifact_size_bytes=len(capture_bytes),
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
        requested_config_sha256=config_sha,
    )

    result = build_training_result(
        request=request,
        result_id="res-canary-001",
        started_at_utc=datetime(2026, 7, 23, 1, 0, 0, tzinfo=UTC),
        finished_at_utc=datetime(2026, 7, 23, 2, 30, 0, tzinfo=UTC),
        actual_trainer_name="nerfstudio-splatfacto",
        actual_trainer_version="0.1.0",
        actual_config_bytes=config_bytes,
        actual_ply_bytes=actual_ply_bytes,
        actual_log_bytes=log_bytes,
        input_bytes_by_path={str(capture_manifest): capture_bytes},
        gpu_environment=GpuEnvironment(
            gpu_name="Tesla T4",
            gpu_memory_mb=15109,
            cuda_version="11.8",
            driver_version="525.60.13",
        ),
        exit_code=exit_code,
        error_message=error_message,
        actual_ply_path=str(ply),
        actual_config_path=str(config),
        actual_log_path=str(log),
        gaussian_count=80,
        sh_degree=3,
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
        # P0.3: without registration quality, only content-only receipt.
        expected = f"training_content_closed.v1={result_sha}"
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
        assert not any(s.startswith("training_") for s in evidence), (
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
        # A failed training run (exit_code=1, empty PLY) cannot match the
        # non-empty PLY file on disk; the handshake must reject it.
        ply = _build_ply(tmp_path)
        req_path, res_path, _ = _build_manifests(
            ply, tmp_path, exit_code=1, actual_ply_bytes=b"",
            error_message="OOM")

        proc = _run(ply, tmp_path / "recon",
                    "--training-result", str(res_path),
                    "--training-request", str(req_path))
        assert proc.returncode == 1, proc.stderr
        assert "TRAINING-PROVENANCE-FAIL" in proc.stderr

    def test_synthetic_combined_with_training_evidence(self, tmp_path):
        # --synthetic + --training-result must both apply: SYNTHETIC provenance
        # AND the content-only receipt (only-degrade rule preserved).
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
        # P0.3: content-only receipt (not trusted prefix without RQ).
        expected = f"training_content_closed.v1={result_sha}"
        assert expected in reg.pose_frame.evidence
        # source_frame must remain byte-identical to registration frame.
        assert splat.source_frame == reg.pose_frame
