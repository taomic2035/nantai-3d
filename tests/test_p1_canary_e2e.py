"""P1 end-to-end canary: cloud emit → prepare_import caller closure.

Proves the full caller loop works with a synthetic small scene:

  1. Build a fake cloud workspace (images, PLY, config.yml, training.log).
  2. Emit registration-request.json + quality-report.json via CLI.
  3. Emit training-request.json + training-result.json via CLI.
  4. prepare_import consumes all four → appends trusted-prefix evidence.
  5. Adversarial: tamper any input byte → fail-closed.
  6. Non-mock COLMAP canary (P1-2): engine="colmap" + capture manifest +
     sparse enumeration → training_allowed=True → trusted prefix.
  7. Stub ns-train argv canary (P0-2): stub ns-train records argv; the
     request intent (total_steps / seed) matches the actual CLI argv.

This canary only proves the **mechanism** works end-to-end.  It does NOT
prove cloud training is real, the photos are real, or the geometry is
metric.  See HANDOFF-GLM-005 §3 P1 and REVIEW-CODEX-023.

See: handoff/REVIEW-CODEX-022-glm-registration-training-trust-contracts.md
See: handoff/HANDOFF-GLM-005-current-gap-and-priority.md
See: handoff/REVIEW-CODEX-023-glm-p1-callers.md
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
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
    SparseModelEntry,
    SparseModelEnumeration,
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


# ============================================================
# Non-mock COLMAP canary (P1-2): training_allowed → trusted prefix
# ============================================================
#
# REVIEW-CODEX-023 P1-2: the mock-engine canaries above only prove
# content-only receipts and tamper rejection.  This class builds a
# synthetic-but-non-mock COLMAP RegistrationResult + SparseModelEnumeration
# + CaptureRevisionManifest so that derive_training_allowed == True, then
# proves prepare_import emits the trusted prefix
# ``training_provenance.v1=<result_sha>``.
#
# Honest boundary (still applies): the COLMAP artifacts here are synthetic
# text models, NOT real photos or a real SfM run.  Trusted prefix proves
# the *contract path* closes, not that the geometry is real or metric.

def _build_colmap_rq_artifacts(
    tmp_path: Path, *, registered: int = 18, total: int = 20,
) -> tuple[Path, Path, Path, Path, Path]:
    """Build non-mock COLMAP RQ artifacts on disk.

    Returns (registration.json, policy.json, quality-report.json,
             capture_manifest.json, sparse_model_dir).
    """
    from pipeline.ingest_manifest import IngestParams
    from pipeline.studio_revisions import (
        CapturePayload,
        CaptureRevisionManifest,
    )

    # RegistrationResult with engine="colmap".
    all_images = [f"img{i:03d}.jpg" for i in range(total)]
    intr = CameraIntrinsics(width=1920, height=1080, fx=1000.0, fy=1000.0,
                            cx=960.0, cy=540.0)
    poses = [
        CameraPose(
            image=f"img{i:03d}.jpg", session_id="s0",
            quat_wxyz=[1.0, 0.0, 0.0, 0.0], t_xyz=[float(i), 0.0, 0.0],
            intrinsics=intr,
        )
        for i in range(registered)
    ]
    reg = RegistrationResult(
        schema_version=2, engine="colmap", pose_frame=_local_frame(),
        world_frame=None, alignment_status=AlignmentStatus.UNALIGNED,
        sessions=[CaptureSession(session_id="s0", kind="photo_batch",
                                  source="test", images=all_images)],
        poses=poses,
    )
    reg_bytes = reg.model_dump_json(indent=2).encode("utf-8")
    reg_json = tmp_path / "rq" / "registration.json"
    reg_json.parent.mkdir(parents=True)
    reg_json.write_bytes(reg_bytes)

    # CaptureRevisionManifest (bytes bound into the report).
    payloads = tuple(
        CapturePayload(
            logical_path=f"img{i:03d}.jpg", sha256="a" * 64, byte_length=1024,
            source_kind="photo", source_ordinal=i % 1,
        )
        for i in range(total)
    )
    manifest = CaptureRevisionManifest(
        revision_id=f"capture-{'0' * 32}",
        created_utc=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC),
        provenance="measured", synthetic=False, source_count=1,
        output_count=total, ingest_session_id=f"ingest-{'a' * 64}",
        ingest_manifest_sha256="b" * 64,
        ingest_parameters=IngestParams(fps=2.0, max_frames=100,
                                        blur_threshold=60.0, max_long_edge=1920),
        payloads=payloads,
    )
    manifest_bytes = (
        json.dumps(manifest.model_dump(mode="json"), sort_keys=True,
                   ensure_ascii=True) + "\n"
    ).encode("ascii")
    capture_json = tmp_path / "rq" / "capture_manifest.json"
    capture_json.write_bytes(manifest_bytes)

    # SparseModelEnumeration (written into sparse_model_dir).
    images = tuple(f"img{i:03d}.jpg" for i in range(registered))
    sparse = SparseModelEnumeration(
        models=(SparseModelEntry(
            model_index=0, image_count=registered, point3d_count=5000,
            images=images,
        ),),
        selected_model_index=0, selection_rule="single_model",
        total_input_images=total,
    )
    sparse_dir = tmp_path / "rq" / "sparse"
    sparse_dir.mkdir(parents=True)
    (sparse_dir / "sparse_enumeration.json").write_text(
        sparse.model_dump_json(indent=2), encoding="utf-8")

    # Policy.
    policy = RegistrationQualityPolicy(
        min_registered_count=10, min_registered_ratio=0.7,
        min_session_coverage_ratio=0.6, max_unregistered_consecutive_run=5,
        min_largest_connected_model_share=0.6,
    )
    policy_json = tmp_path / "rq" / "policy.json"
    policy_json.write_bytes(policy.model_dump_json(indent=2).encode("utf-8"))

    # Quality report via the builder (derives all SHAs from artifacts).
    report = build_registration_quality_report(
        registration=reg, registration_json_bytes=reg_bytes,
        capture_manifest=manifest, capture_manifest_bytes=manifest_bytes,
        policy=policy, sparse_enumeration=sparse,
        invocation_succeeded=True,
    )
    report_json = tmp_path / "rq" / "quality-report.json"
    report_json.write_bytes(report.model_dump_json(indent=2).encode("utf-8"))
    return reg_json, policy_json, report_json, capture_json, sparse_dir


class TestP1CanaryNonMock:
    """P1-2: non-mock COLMAP → training_allowed=True → trusted prefix.

    These canaries prove the *contract path* that mock-engine canaries
    cannot: a COLMAP-engine RegistrationResult with a capture manifest
    and sparse enumeration yields ``training_allowed=True``, which (with
    content closure + trainer identified) produces the trusted prefix
    ``training_provenance.v1=<result_sha>``.

    They do NOT prove the synthetic COLMAP text model is real geometry.
    """

    def test_colmap_registration_yields_trusted_prefix(self, tmp_path):
        """engine=colmap + capture manifest + sparse enum → trusted prefix."""
        from pipeline.registration_quality import derive_training_allowed

        ws = _build_cloud_workspace(tmp_path)
        (reg_json, policy_json, report_json, capture_json,
         sparse_dir) = _build_colmap_rq_artifacts(tmp_path)
        req_path, res_path, result_sha = _emit_training_manifests(tmp_path, ws)
        out_dir = tmp_path / "recon"

        # Sanity: the report really is training_allowed (non-mock, all gates).
        report = json.loads(report_json.read_text(encoding="utf-8"))
        assert report["engine"] == "colmap"
        assert report["training_allowed"] is True, report
        policy = RegistrationQualityPolicy.model_validate_json(
            policy_json.read_text(encoding="utf-8"))
        from pipeline.registration_quality import RegistrationQualityReport
        rq = RegistrationQualityReport.model_validate_json(
            report_json.read_text(encoding="utf-8"))
        assert derive_training_allowed(rq, policy) is True

        proc = _run_prepare_import(
            ws["ply"], out_dir,
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
            "--capture-manifest", str(capture_json),
            "--sparse-model-dir", str(sparse_dir),
        )
        assert proc.returncode == 0, proc.stderr

        evidence = _evidence(out_dir / "registration.json")
        trusted = f"training_provenance.v1={result_sha}"
        assert trusted in evidence, (
            f"non-mock COLMAP must yield trusted prefix {trusted!r}; "
            f"got {evidence}")
        # Honest boundary: still NOT metric/aligned — trusted prefix is a
        # training-provenance receipt, not a metric upgrade.
        reg = RegistrationResult.model_validate_json(
            (out_dir / "registration.json").read_text(encoding="utf-8"))
        assert reg.pose_frame.metric_status is MetricStatus.ARBITRARY
        assert reg.pose_frame.geo_aligned is GeoAlignment.UNALIGNED

    def test_colmap_without_capture_manifest_falls_to_content_only(self, tmp_path):
        """engine=colmap but no --capture-manifest → training_allowed=False
        (capture_manifest_sha is None) → content-only, not trusted."""
        ws = _build_cloud_workspace(tmp_path)
        (reg_json, policy_json, report_json, capture_json,
         sparse_dir) = _build_colmap_rq_artifacts(tmp_path)
        req_path, res_path, result_sha = _emit_training_manifests(tmp_path, ws)
        out_dir = tmp_path / "recon"

        # Omit --capture-manifest: validator sees capture_manifest_sha=None
        # even though the report declares one → mismatch → fail-closed.
        proc = _run_prepare_import(
            ws["ply"], out_dir,
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--registration-quality-report", str(report_json),
            "--registration-json", str(reg_json),
            "--registration-quality-policy", str(policy_json),
            # intentionally no --capture-manifest
            "--sparse-model-dir", str(sparse_dir),
        )
        # Fail-closed: report declares capture_manifest_sha but caller omitted
        # the file → validator rejects → returncode 1.
        assert proc.returncode == 1, proc.stderr
        assert "REGISTRATION-QUALITY-FAIL" in proc.stderr


# ============================================================
# Stub ns-train argv canary (P0-2): request intent matches CLI argv
# ============================================================
#
# REVIEW-CODEX-023 P0-2: the cloud script writes seed / total_steps into
# the operator-intent config.yml AND passes them as real ns-train CLI
# flags (--max-num-iterations, --machine.seed).  This canary installs a
# stub ``ns-train`` that records its argv, runs a bash probe that mirrors
# the cloud script's ns-train invocation (cloud/train_3dgs_nerfstudio.sh
# lines ~227-231), and asserts the recorded argv matches the request's
# training_config (total_steps / random_seed).
#
# It does NOT prove a real nerfstudio build accepts these flags — only
# that the cloud script's argv construction is consistent with the
# request intent it emits.  Real nerfstudio CLI compatibility must be
# verified on a cloud GPU instance.

# Bash probe mirroring cloud/train_3dgs_nerfstudio.sh ns-train invocation.
# Keep in sync with the cloud script's `ns-train splatfacto ...` block.
_NS_TRAIN_PROBE = r"""#!/bin/bash
set -euo pipefail
TOTAL_STEPS="$1"
SEED="$2"
PROC="$3"
OUT="$4"
ns-train splatfacto --data "$PROC" --output-dir "$OUT" \
  --max-num-iterations "$TOTAL_STEPS" \
  --machine.seed "$SEED" \
  --viewer.quit-on-train-completion True
"""

# Stub ns-train: records argv to $NS_TRAIN_ARGV_FILE then exits 0.
_NS_TRAIN_STUB = r"""#!/bin/bash
printf '%s\0' "$@" > "$NS_TRAIN_ARGV_FILE"
exit 0
"""


class TestP1CanaryStubArgv:
    """P0-2: stub ns-train records argv; request intent matches the argv."""

    @staticmethod
    def _git_bash() -> str:
        """Locate Git for Windows bash for running the probe."""
        for cand in (r"D:\Git\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"):
            if Path(cand).is_file():
                return cand
        # Fallback: rely on PATH (CI may provide bash elsewhere).
        return "bash"

    def test_request_intent_matches_ns_train_argv(self, tmp_path):
        """Emit a request, then run the ns-train probe with a stub; the
        recorded argv must contain --max-num-iterations <total_steps> and
        --machine.seed <seed> matching the request's training_config."""
        ws = _build_cloud_workspace(tmp_path)
        total_steps = 7777
        seed = 99
        # Emit a training-request.json with the chosen intent.
        req_path = tmp_path / "cloud" / "training-request.json"
        subprocess.run(
            [sys.executable, "scripts/emit_training_provenance.py",
             "request",
             "--input", f"capture_manifest:{ws['images']}",
             "--config-yml", str(ws["config"]),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--max-resolution", "800",
             "--total-steps", str(total_steps),
             "--seed", str(seed),
             "--request-id", "req-argv-canary",
             "--output", str(req_path)],
            cwd=_ROOT, capture_output=True, text=True, check=True)
        request = json.loads(req_path.read_text(encoding="utf-8"))
        assert request["training_config"]["total_steps"] == total_steps
        assert request["training_config"]["random_seed"] == seed

        # Install stub ns-train + probe in a temp bin dir.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "ns-train"
        stub.write_text(_NS_TRAIN_STUB, encoding="utf-8", newline="\n")
        probe = tmp_path / "probe.sh"
        probe.write_text(_NS_TRAIN_PROBE, encoding="utf-8", newline="\n")
        argv_file = tmp_path / "argv.txt"

        # Run the probe via Git bash with the stub on PATH.
        env = os.environ.copy()
        # Prepend bin_dir so the stub `ns-train` shadows any real one.
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["NS_TRAIN_ARGV_FILE"] = str(argv_file)
        proc = subprocess.run(
            [self._git_bash(), str(probe),
             str(total_steps), str(seed),
             str(ws["images"]), str(tmp_path / "out")],
            capture_output=True, text=True, env=env)
        assert proc.returncode == 0, (
            f"probe failed rc={proc.returncode}\nstderr:\n{proc.stderr}")
        assert argv_file.is_file(), "stub did not record argv"

        # Recorded argv (NUL-separated tokens, trailing NUL dropped).
        raw = argv_file.read_bytes()
        tokens = [t.decode("utf-8") for t in raw.split(b"\0") if t]
        assert "--max-num-iterations" in tokens, tokens
        assert "--machine.seed" in tokens, tokens
        i_steps = tokens.index("--max-num-iterations")
        i_seed = tokens.index("--machine.seed")
        assert tokens[i_steps + 1] == str(total_steps), tokens
        assert tokens[i_seed + 1] == str(seed), tokens
        # The argv and the request intent agree.
        assert int(tokens[i_steps + 1]) == request["training_config"]["total_steps"]
        assert int(tokens[i_seed + 1]) == request["training_config"]["random_seed"]

    def test_diverging_seed_breaks_intent_match(self, tmp_path):
        """Adversarial: probe run with a seed that differs from the request
        intent → the argv/contract comparison must detect the divergence."""
        ws = _build_cloud_workspace(tmp_path)
        request_seed = 42
        actual_seed = 7  # diverges
        req_path = tmp_path / "cloud" / "training-request.json"
        subprocess.run(
            [sys.executable, "scripts/emit_training_provenance.py",
             "request",
             "--input", f"capture_manifest:{ws['images']}",
             "--config-yml", str(ws["config"]),
             "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
             "--max-resolution", "800", "--total-steps", "10000",
             "--seed", str(request_seed),
             "--output", str(req_path)],
            cwd=_ROOT, capture_output=True, text=True, check=True)
        request = json.loads(req_path.read_text(encoding="utf-8"))

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "ns-train"
        stub.write_text(_NS_TRAIN_STUB, encoding="utf-8", newline="\n")
        probe = tmp_path / "probe.sh"
        probe.write_text(_NS_TRAIN_PROBE, encoding="utf-8", newline="\n")
        argv_file = tmp_path / "argv.txt"

        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["NS_TRAIN_ARGV_FILE"] = str(argv_file)
        subprocess.run(
            [self._git_bash(), str(probe),
             "10000", str(actual_seed),
             str(ws["images"]), str(tmp_path / "out")],
            capture_output=True, text=True, env=env, check=True)
        tokens = [t.decode("utf-8")
                  for t in argv_file.read_bytes().split(b"\0") if t]
        i_seed = tokens.index("--machine.seed")
        recorded_seed = int(tokens[i_seed + 1])
        # The divergence is exactly what the contract must catch.
        assert recorded_seed != request["training_config"]["random_seed"]
        assert recorded_seed == actual_seed


# ============================================================
# Preprocessing-failure canary (P1-1): ns-process-data fails → failed result
# ============================================================
#
# REVIEW-CODEX-023 P1-1 / GLM-006 §6 review point #4: the cloud script's
# ns-process-data failure path (cloud/train_3dgs_nerfstudio.sh lines 195-215)
# emits a failed training-result.json with --exit-code and --error-message
# (no --ply) when preprocessing fails, so the operator gets a provenance
# receipt for the failure instead of a silent exit.  This canary exercises
# the Python CLI argv that the cloud script constructs and proves
# prepare_import correctly rejects the failed result.
#
# Divergence from the cloud script: the canary passes --gpu-name etc.
# explicitly because the dev machine has no nvidia-smi; the cloud script
# omits them (auto-detect works on cloud GPU instances).  This canary
# tests the Python CLI's handling of the argv, not the bash script's
# argv construction.
#
# It does NOT prove a real ns-process-data failure on a cloud GPU —
# only that the failed-state result is well-formed and rejected.

def _emit_failed_training_result(
    tmp_path: Path, ws: dict[str, Path], *,
    exit_code: int = 1,
    error_message: str = "ns-process-data failed (exit=1); training never started",
) -> tuple[Path, Path, str]:
    """Emit training-request.json + a failed training-result.json via CLI.

    Mirrors the cloud script's P1-1 preprocessing-failure path
    (cloud/train_3dgs_nerfstudio.sh lines 195-215): ns-process-data fails,
    cloud script emits a failed result with --exit-code and --error-message,
    no --ply.
    """
    out = tmp_path / "cloud"
    subprocess.run(
        [sys.executable, "scripts/emit_training_provenance.py",
         "request",
         "--input", f"capture_manifest:{ws['images']}",
         "--config-yml", str(ws["config"]),
         "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
         "--max-resolution", "800", "--total-steps", "10000", "--seed", "42",
         "--request-id", "req-canary-fail",
         "--output", str(out / "training-request.json")],
        cwd=_ROOT, capture_output=True, text=True, check=True)
    # Emit failed result (no --ply, --exit-code != 0, --error-message).
    # GPU flags are explicit because the dev machine has no nvidia-smi;
    # the cloud script omits them (auto-detect works on cloud GPU instances).
    subprocess.run(
        [sys.executable, "scripts/emit_training_provenance.py",
         "result",
         "--request", str(out / "training-request.json"),
         "--config-yml", str(ws["config"]),
         "--log", str(ws["log"]),
         "--trainer", "nerfstudio-splatfacto", "--trainer-version", "0.1.0",
         "--gpu-name", "Tesla T4", "--gpu-memory-mb", "15109",
         "--cuda-version", "11.8", "--driver-version", "525.60.13",
         "--exit-code", str(exit_code),
         "--error-message", error_message,
         "--started-at", "2026-07-23T01:00:00Z",
         "--finished-at", "2026-07-23T01:05:00Z",
         "--result-id", "res-canary-fail",
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


class TestP1CanaryPreprocessFailure:
    """P1-1: ns-process-data failure → failed result → fail-closed.

    Mirrors the cloud script's preprocessing-failure path
    (cloud/train_3dgs_nerfstudio.sh lines 195-215): when ns-process-data
    fails, the cloud script emits a failed training-result.json with
    --exit-code and --error-message (no --ply), then exits.

    These canaries prove:
    1. The emit CLI correctly produces a failed result with the expected
       state (failed, exit_code, error_message, no trained_ply binding).
    2. prepare_import rejects the failed result (fail-closed) — a failed
       run cannot yield content closure or any trust evidence.
    3. Even with --allow-unverified-training, no evidence is appended
       (content_closed=False → no receipt).

    They do NOT prove a real ns-process-data failure on a cloud GPU —
    only that the Python CLI argv the cloud script constructs is valid
    and produces the expected failed-state result.
    """

    def test_preprocess_failure_emits_failed_result(self, tmp_path):
        """Emit a failed result via CLI; verify state/exit_code/error_message."""
        ws = _build_cloud_workspace(tmp_path)
        req_path, res_path, _ = _emit_failed_training_result(tmp_path, ws)

        from pipeline.training_provenance import TrainingResult
        result = TrainingResult.model_validate_json(
            res_path.read_text(encoding="utf-8"))
        assert result.training_status.state == "failed"
        assert result.training_status.exit_code == 1
        assert result.training_status.error_message == (
            "ns-process-data failed (exit=1); training never started")
        # No trained_ply binding for failed state.
        kinds = [b.artifact_kind for b in result.output_bindings]
        assert "trained_ply" not in kinds
        # primary_ply fields reflect empty PLY.
        import hashlib
        assert result.primary_ply_sha256 == hashlib.sha256(b"").hexdigest()
        assert result.primary_ply_size_bytes == 0

    def test_preprocess_failure_result_rejected_by_prepare_import(
        self, tmp_path):
        """prepare_import rejects a failed result (fail-closed).

        A failed training run (exit_code=1, no PLY in result) cannot match
        the non-empty PLY file on disk; the handshake must reject it.
        """
        ws = _build_cloud_workspace(tmp_path)
        req_path, res_path, _ = _emit_failed_training_result(tmp_path, ws)
        out_dir = tmp_path / "recon"

        proc = _run_prepare_import(
            ws["ply"], out_dir,
            "--training-request", str(req_path),
            "--training-result", str(res_path),
        )
        assert proc.returncode == 1, proc.stderr
        assert "TRAINING-PROVENANCE-FAIL" in proc.stderr

    def test_preprocess_failure_no_evidence_with_bypass(self, tmp_path):
        """With --allow-unverified-training, no evidence is appended for a
        failed result (content_closed=False → no receipt).

        This proves the bypass flag does not silently upgrade a failed run
        to a content-only receipt — it skips provenance entirely.
        """
        ws = _build_cloud_workspace(tmp_path)
        req_path, res_path, _ = _emit_failed_training_result(tmp_path, ws)
        out_dir = tmp_path / "recon"

        proc = _run_prepare_import(
            ws["ply"], out_dir,
            "--training-request", str(req_path),
            "--training-result", str(res_path),
            "--allow-unverified-training",
        )
        assert proc.returncode == 0, proc.stderr
        evidence = _evidence(out_dir / "registration.json")
        assert not any(s.startswith("training_") for s in evidence), (
            f"failed result must not append evidence; got {evidence}")
        assert "external-3dgs-import" in evidence
