"""emit_training_provenance.py — emit manifests then validate round-trip.

Verifies the cloud-side helper produces byte-exact canonical manifests that
``validate_training_provenance`` accepts and that ``prepare_import`` consumes.
Drives the actual CLI (catches packaging / arg bugs) and avoids nvidia-smi by
passing all GPU fields explicitly.

The hardened P0.2 contract requires actual PLY / config / log / input bytes
to be supplied to the validator, so these tests gather the same bytes the
emit script read and feed them back through ``validate_training_provenance``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from pipeline.gaussian_scene import GaussianScene
from pipeline.training_provenance import (
    TrainingRequest,
    TrainingResult,
    derive_training_trust,
    request_canonical_sha256,
    result_canonical_sha256,
    validate_training_provenance,
)

_ROOT = Path(__file__).resolve().parent.parent


def _run(*args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, "scripts/emit_training_provenance.py", *args],
        cwd=_ROOT, capture_output=True, text=True)
    return proc


def _build_cloud_workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path, int]:
    """Build a fake cloud workspace: images dir, PLY, config.yml, training.log."""
    rng = np.random.default_rng(23)
    n = 120
    ply = tmp_path / "cloud" / "export" / "point_cloud.ply"
    ply.parent.mkdir(parents=True)
    # degree-3 3DGS PLY so the header parser sees 45 f_rest props.
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


def _gather_input_bytes(request: TrainingRequest) -> dict[str, bytes]:
    """Re-read input artefact bytes from their declared paths.

    For directories, emit_training_provenance writes the deterministic
    manifest bytes (relpath\\0size\\0sha\\n) so the validator can
    re-derive the same sha/size.  We reproduce that here.
    """
    out: dict[str, bytes] = {}
    for binding in request.input_bindings:
        p = Path(binding.artifact_path)
        if p.is_dir():
            files = sorted(f for f in p.rglob("*") if f.is_file())
            parts: list[bytes] = []
            for f in files:
                rel = str(f.relative_to(p)).replace("\\", "/")
                import hashlib as _hl
                sha = _hl.sha256(f.read_bytes()).hexdigest()
                size = f.stat().st_size
                parts.append(f"{rel}\0{size}\0{sha}\n".encode())
            out[binding.artifact_path] = b"".join(parts)
        else:
            out[binding.artifact_path] = p.read_bytes()
    return out


class TestEmitTrainingProvenance:
    def test_request_result_roundtrip_validates(self, tmp_path):
        images, ply, config, log, n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        req_proc = _run(
            "request",
            "--input", f"capture_manifest:{images}",
            "--config-yml", str(config),
            "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
            "--max-resolution", "800", "--total-steps", "10000", "--seed", "42",
            "--request-id", "req-rt-001",
            "--output", str(out / "training-request.json"))
        assert req_proc.returncode == 0, req_proc.stderr
        assert "canonical_sha256=" in req_proc.stdout

        res_proc = _run(
            "result",
            "--request", str(out / "training-request.json"),
            "--ply", str(ply),
            "--config-yml", str(config),
            "--log", str(log),
            "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
            "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
            "--cuda-version", "11.8", "--driver-version", "525.60.13",
            "--result-id", "res-rt-001",
            "--started-at", "2026-07-23T01:00:00Z",
            "--output", str(out / "training-result.json"))
        assert res_proc.returncode == 0, res_proc.stderr
        assert f"gaussians={n}" in res_proc.stdout
        assert "sh_degree=3" in res_proc.stdout
        assert "state=completed" in res_proc.stdout

        # Load + validate byte-exact.
        request = TrainingRequest.model_validate_json(
            (out / "training-request.json").read_text(encoding="utf-8"))
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))
        ply_bytes = ply.read_bytes()
        config_bytes = config.read_bytes()
        log_bytes = log.read_bytes()
        input_bytes = _gather_input_bytes(request)

        # No exception == content closure verified.
        validate_training_provenance(
            result, request,
            actual_ply_bytes=ply_bytes,
            actual_config_bytes=config_bytes,
            actual_log_bytes=log_bytes,
            input_bytes_by_path=input_bytes,
        )

        trust = derive_training_trust(
            result, request,
            actual_ply_bytes=ply_bytes,
            actual_config_bytes=config_bytes,
            actual_log_bytes=log_bytes,
            input_bytes_by_path=input_bytes,
            registration_quality_passed=False)
        assert trust.content_closed is True
        # is_trustworthy stays False because registration_quality_passed=False
        # (prepare_import doesn't run the SfM gate) — honest.
        assert trust.is_trustworthy is False
        assert trust.trainer_identified is True
        assert trust.seed_recorded is True
        assert trust.log_bound is True
        assert trust.environment_captured is True

        # request_canonical_sha256 in result matches request.
        assert result.request_canonical_sha256 == request_canonical_sha256(request)
        # result sha is content-addressed.
        assert result_canonical_sha256(result)

    def test_request_binds_directory_content_deterministically(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "r1"
        out2 = tmp_path / "r2"

        for o in (out, out2):
            p = _run("request",
                     "--input", f"capture_manifest:{images}",
                     "--config-yml", str(config),
                     "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1",
                     "--seed", "1", "--output", str(o / "training-request.json"))
            assert p.returncode == 0, p.stderr

        # Same inputs -> identical input binding SHA (deterministic dir hash).
        r1 = TrainingRequest.model_validate_json(
            (out / "training-request.json").read_text(encoding="utf-8"))
        r2 = TrainingRequest.model_validate_json(
            (out2 / "training-request.json").read_text(encoding="utf-8"))
        assert r1.input_bindings[0].artifact_sha256 == r2.input_bindings[0].artifact_sha256
        # requested_config_sha256 also deterministic for identical config bytes.
        assert r1.requested_config_sha256 == r2.requested_config_sha256

    def test_ply_mismatch_in_result_is_rejected_by_validator(self, tmp_path):
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        _run("result",
             "--request", str(out / "training-request.json"),
             "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
             "--cuda-version", "11.8", "--driver-version", "525.60.13",
             "--output", str(out / "training-result.json"))

        request = TrainingRequest.model_validate_json(
            (out / "training-request.json").read_text(encoding="utf-8"))
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))

        # Tamper the PLY bytes the validator sees.
        with pytest.raises(ValueError, match="primary_ply_sha256 mismatch|PLY bytes mismatch"):
            validate_training_provenance(
                result, request,
                actual_ply_bytes=ply.read_bytes() + b"\x00",
                actual_config_bytes=config.read_bytes(),
                actual_log_bytes=log.read_bytes(),
                input_bytes_by_path=_gather_input_bytes(request),
            )

    def test_config_drift_in_result_is_rejected_by_validator(self, tmp_path):
        """The result binds actual config bytes; if the validator is fed
        different config bytes, closure must fail."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        _run("result",
             "--request", str(out / "training-request.json"),
             "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
             "--cuda-version", "11.8", "--driver-version", "525.60.13",
             "--output", str(out / "training-result.json"))

        request = TrainingRequest.model_validate_json(
            (out / "training-request.json").read_text(encoding="utf-8"))
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))

        # Tamper config bytes the validator sees.
        tampered = config.read_bytes() + b"\n# tampered\n"
        with pytest.raises(ValueError, match="actual_config_sha256 mismatch"):
            validate_training_provenance(
                result, request,
                actual_ply_bytes=ply.read_bytes(),
                actual_config_bytes=tampered,
                actual_log_bytes=log.read_bytes(),
                input_bytes_by_path=_gather_input_bytes(request),
            )

    def test_log_drift_in_result_is_rejected_by_validator(self, tmp_path):
        """The result binds actual log bytes; if the validator is fed different
        log bytes, closure must fail."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        _run("result",
             "--request", str(out / "training-request.json"),
             "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
             "--cuda-version", "11.8", "--driver-version", "525.60.13",
             "--output", str(out / "training-result.json"))

        request = TrainingRequest.model_validate_json(
            (out / "training-request.json").read_text(encoding="utf-8"))
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))

        tampered = log.read_bytes() + b"extra line\n"
        with pytest.raises(ValueError, match="training_log_sha256 mismatch"):
            validate_training_provenance(
                result, request,
                actual_ply_bytes=ply.read_bytes(),
                actual_config_bytes=config.read_bytes(),
                actual_log_bytes=tampered,
                input_bytes_by_path=_gather_input_bytes(request),
            )

    def test_failed_exit_with_nonempty_ply_is_rejected_by_emit(self, tmp_path):
        """A failed trainer (exit_code != 0) cannot produce a non-empty PLY.
        The emit script must refuse rather than produce an inconsistent result.
        """
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        proc = _run("result",
                    "--request", str(out / "training-request.json"),
                    "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
                    "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
                    "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
                    "--cuda-version", "11.8", "--driver-version", "525.60.13",
                    "--exit-code", "1", "--error-message", "trainer crashed")
        assert proc.returncode != 0
        assert "non-empty" in proc.stderr or "non-empty" in proc.stdout

    def test_failed_run_with_empty_ply_validates_as_failed(self, tmp_path):
        """exit_code != 0 with a zero-byte PLY placeholder emits a failed
        result that validates as content-closed (failed)."""
        images, _ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"
        # Empty PLY placeholder.
        empty_ply = out / "empty.ply"
        empty_ply.parent.mkdir(parents=True, exist_ok=True)
        empty_ply.write_bytes(b"")

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        proc = _run("result",
                    "--request", str(out / "training-request.json"),
                    "--ply", str(empty_ply), "--config-yml", str(config),
                    "--log", str(log),
                    "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
                    "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
                    "--cuda-version", "11.8", "--driver-version", "525.60.13",
                    "--exit-code", "1", "--error-message", "trainer crashed",
                    "--output", str(out / "training-result.json"))
        assert proc.returncode == 0, proc.stderr
        assert "state=failed" in proc.stdout

        request = TrainingRequest.model_validate_json(
            (out / "training-request.json").read_text(encoding="utf-8"))
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))
        validate_training_provenance(
            result, request,
            actual_ply_bytes=b"",
            actual_config_bytes=config.read_bytes(),
            actual_log_bytes=log.read_bytes(),
            input_bytes_by_path=_gather_input_bytes(request),
        )
        assert result.training_status.state == "failed"
        trust = derive_training_trust(
            result, request,
            actual_ply_bytes=b"",
            actual_config_bytes=config.read_bytes(),
            actual_log_bytes=log.read_bytes(),
            input_bytes_by_path=_gather_input_bytes(request),
            registration_quality_passed=True)
        # failed run -> content_closed False (state != completed)
        assert trust.content_closed is False
        assert trust.is_trustworthy is False

    def test_request_requires_config_yml(self, tmp_path):
        """--config-yml is required for request (requested_config_sha256)."""
        images, _ply, _config, _log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"
        proc = _run("request",
                    "--input", f"capture_manifest:{images}",
                    # no --config-yml
                    "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
                    "--seed", "42", "--output", str(out / "training-request.json"))
        assert proc.returncode != 0
        assert "config" in proc.stderr.lower() or "config" in proc.stdout.lower()

    def test_trainer_drift_without_reason_rejected(self, tmp_path):
        """Actual trainer differs from request but no --trainer-drift-reason
        -> emit succeeds but validator rejects (default policy denies drift)."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        # Result uses brush trainer instead of nerfstudio-splatfacto.
        proc = _run("result",
                    "--request", str(out / "training-request.json"),
                    "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
                    "--trainer", "brush", "--trainer-version", "brush 0.3.0",
                    "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
                    "--cuda-version", "11.8", "--driver-version", "525.60.13",
                    "--output", str(out / "training-result.json"))
        assert proc.returncode == 0, proc.stderr  # emit builds the result

        request = TrainingRequest.model_validate_json(
            (out / "training-request.json").read_text(encoding="utf-8"))
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))
        with pytest.raises(ValueError, match="trainer drift"):
            validate_training_provenance(
                result, request,
                actual_ply_bytes=ply.read_bytes(),
                actual_config_bytes=config.read_bytes(),
                actual_log_bytes=log.read_bytes(),
                input_bytes_by_path=_gather_input_bytes(request),
            )

    def test_trainer_drift_with_reason_validates_with_policy(self, tmp_path):
        """Actual trainer differs from request, --trainer-drift-reason supplied,
        policy allows drift -> validator accepts."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        proc = _run("result",
                    "--request", str(out / "training-request.json"),
                    "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
                    "--trainer", "brush", "--trainer-version", "brush 0.3.0",
                    "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
                    "--cuda-version", "11.8", "--driver-version", "525.60.13",
                    "--trainer-drift-reason", "cloud image upgraded to brush",
                    "--output", str(out / "training-result.json"))
        assert proc.returncode == 0, proc.stderr

        request = TrainingRequest.model_validate_json(
            (out / "training-request.json").read_text(encoding="utf-8"))
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))
        from pipeline.training_provenance import TrainingDriftPolicy
        validate_training_provenance(
            result, request,
            actual_ply_bytes=ply.read_bytes(),
            actual_config_bytes=config.read_bytes(),
            actual_log_bytes=log.read_bytes(),
            input_bytes_by_path=_gather_input_bytes(request),
            policy=TrainingDriftPolicy(allow_trainer_drift=True),
        )
        # trainer_identified stays False even though closure is valid.
        trust = derive_training_trust(
            result, request,
            actual_ply_bytes=ply.read_bytes(),
            actual_config_bytes=config.read_bytes(),
            actual_log_bytes=log.read_bytes(),
            input_bytes_by_path=_gather_input_bytes(request),
            registration_quality_passed=True,
            policy=TrainingDriftPolicy(allow_trainer_drift=True),
        )
        assert trust.content_closed is True
        assert trust.trainer_identified is False
        assert trust.is_trustworthy is False  # drift -> not trustworthy

    def test_drift_reason_with_matching_trainer_rejected_by_emit(self, tmp_path):
        """--trainer-drift-reason supplied but actual trainer == requested
        trainer -> emit refuses (no drift occurred)."""
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        proc = _run("result",
                    "--request", str(out / "training-request.json"),
                    "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
                    "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
                    "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
                    "--cuda-version", "11.8", "--driver-version", "525.60.13",
                    "--trainer-drift-reason", "should not be here")
        assert proc.returncode != 0
        assert "no drift" in proc.stderr.lower() or "no drift" in proc.stdout.lower()

    def test_emitted_manifests_flow_through_prepare_import(self, tmp_path):
        # Full chain: cloud emits manifests -> prepare_import verifies content
        # closure and appends content-only receipt (NOT trusted prefix —
        # registration quality is NOT provided so is_trustworthy stays False).
        # P0.3 hardened: trusted prefix requires registration quality args too.
        images, ply, config, log, _n = _build_cloud_workspace(tmp_path)
        out = tmp_path / "cloud"

        _run("request",
             "--input", f"capture_manifest:{images}",
             "--config-yml", str(config),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--seed", "42", "--output", str(out / "training-request.json"))
        _run("result",
             "--request", str(out / "training-request.json"),
             "--ply", str(ply), "--config-yml", str(config), "--log", str(log),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
             "--cuda-version", "11.8", "--driver-version", "525.60.13",
             "--started-at", "2026-07-23T01:00:00Z",
             "--output", str(out / "training-result.json"))

        from pipeline.recon_schema import RegistrationResult
        recon_dir = tmp_path / "recon"
        prep = subprocess.run(
            [sys.executable, "scripts/prepare_import.py", str(ply),
             "--out-dir", str(recon_dir),
             "--training-result", str(out / "training-result.json"),
             "--training-request", str(out / "training-request.json")],
            cwd=_ROOT, capture_output=True, text=True)
        assert prep.returncode == 0, \
            f"prepare_import.py failed: {prep.stderr[:500]}"

        reg = RegistrationResult.model_validate_json(
            (recon_dir / "registration.json").read_text(encoding="utf-8"))
        evidence = reg.pose_frame.evidence
        result = TrainingResult.model_validate_json(
            (out / "training-result.json").read_text(encoding="utf-8"))
        result_sha = result_canonical_sha256(result)
        # Without registration quality args, only content-only receipt is added.
        expected = f"training_content_closed.v1={result_sha}"
        assert expected in evidence, f"missing {expected!r}; got {evidence}"
        # Trusted prefix must NOT be present (is_trustworthy=False without RQ).
        trusted = f"training_provenance.v1={result_sha}"
        assert trusted not in evidence, \
            f"trusted prefix present without registration quality: {evidence}"
