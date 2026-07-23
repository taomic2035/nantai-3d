"""Emit registration quality CLI — focused tests.

Covers ``scripts/emit_registration_quality.py`` end-to-end via subprocess:

  1. Mock engine (no sparse dir) → quality report with training_allowed=False.
  2. COLMAP engine + sparse dir + capture manifest → training_allowed=True.
  3. Error cases: missing files, engine/sparse-dir mismatch, bad JSON.

The CLI drives ``build_registration_quality_report`` and round-trip validates
via ``validate_registration_quality``.  These tests prove the CLI wiring
(argparse → builder → validator → file write) works, NOT that the COLMAP
sparse models or photos are real.

See: handoff/REVIEW-CODEX-022-glm-registration-training-trust-contracts.md
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

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
    RegistrationQualityReport,
    derive_training_allowed,
)

_ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# Artifact helpers
# ============================================================

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


def _write_registration_json(
    path: Path, *, engine: str = "mock", registered: int = 15, total: int = 15,
) -> bytes:
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
    reg = RegistrationResult(
        schema_version=2, engine=engine, pose_frame=_local_frame(),
        world_frame=None, alignment_status=AlignmentStatus.UNALIGNED,
        sessions=[CaptureSession(session_id="s0", kind="photo_batch",
                                  source="test", images=all_images)],
        poses=poses,
    )
    reg_bytes = reg.model_dump_json(indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(reg_bytes)
    return reg_bytes


def _write_policy_json(path: Path) -> None:
    policy = RegistrationQualityPolicy(
        min_registered_count=10, min_registered_ratio=0.7,
        min_session_coverage_ratio=0.6, max_unregistered_consecutive_run=5,
        min_largest_connected_model_share=0.6,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(policy.model_dump_json(indent=2).encode("utf-8"))


def _write_colmap_sparse(sparse_dir: Path, *, images: list[str],
                          n_points: int = 500) -> None:
    """Write a minimal COLMAP sparse text model (images.txt + points3D.txt)."""
    sparse_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for idx, img in enumerate(images):
        lines.append(f"{idx + 1} 1 0 0 0 0 0 0 1 {img}\n")
        lines.append("0 0\n")
    (sparse_dir / "images.txt").write_text("".join(lines), encoding="utf-8")
    pts = "".join(f"{i + 1} 0 0 0 0 0 0 1 0 0 0\n" for i in range(n_points))
    (sparse_dir / "points3D.txt").write_text(pts, encoding="utf-8")


def _write_capture_manifest(path: Path, *, total: int = 15) -> bytes:
    from pipeline.ingest_manifest import IngestParams
    from pipeline.studio_revisions import (
        CapturePayload,
        CaptureRevisionManifest,
    )
    payloads = tuple(
        CapturePayload(
            logical_path=f"IMG_{i:04d}.jpg", sha256="a" * 64, byte_length=1024,
            source_kind="photo", source_ordinal=0,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(manifest_bytes)
    return manifest_bytes


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/emit_registration_quality.py", *args],
        cwd=_ROOT, capture_output=True, text=True)


# ============================================================
# Happy path: mock engine
# ============================================================

class TestEmitRegistrationQualityMock:
    def test_mock_engine_yields_report(self, tmp_path):
        reg_json = tmp_path / "reg" / "registration.json"
        _write_registration_json(reg_json, engine="mock",
                                   registered=15, total=15)
        policy_json = tmp_path / "reg" / "policy.json"
        _write_policy_json(policy_json)
        out = tmp_path / "report.json"

        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--output", str(out),
        )
        assert proc.returncode == 0, proc.stderr
        assert out.is_file()

        report = RegistrationQualityReport.model_validate_json(
            out.read_text(encoding="utf-8"))
        assert report.engine == "mock"
        assert report.registered_count == 15
        assert report.quality_accepted is True
        assert report.training_allowed is False  # mock never trusted

    def test_mock_with_capture_manifest_still_not_training_allowed(self, tmp_path):
        reg_json = tmp_path / "reg" / "registration.json"
        _write_registration_json(reg_json, engine="mock")
        policy_json = tmp_path / "reg" / "policy.json"
        _write_policy_json(policy_json)
        cm_json = tmp_path / "reg" / "capture_manifest.json"
        _write_capture_manifest(cm_json)
        out = tmp_path / "report.json"

        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--capture-manifest", str(cm_json),
            "--output", str(out),
        )
        assert proc.returncode == 0, proc.stderr

        report = RegistrationQualityReport.model_validate_json(
            out.read_text(encoding="utf-8"))
        assert report.engine == "mock"
        assert report.training_allowed is False
        assert report.capture_manifest_sha256 is not None


# ============================================================
# Happy path: COLMAP engine
# ============================================================

class TestEmitRegistrationQualityColmap:
    def test_colmap_engine_yields_training_allowed_true(self, tmp_path):
        total = 15
        reg_json = tmp_path / "reg" / "registration.json"
        _write_registration_json(reg_json, engine="colmap",
                                   registered=total, total=total)
        policy_json = tmp_path / "reg" / "policy.json"
        _write_policy_json(policy_json)
        cm_json = tmp_path / "reg" / "capture_manifest.json"
        _write_capture_manifest(cm_json, total=total)
        sparse_dir = tmp_path / "colmap_ws" / "sparse" / "0"
        _write_colmap_sparse(sparse_dir, images=[
            f"IMG_{i:04d}.jpg" for i in range(total)])
        out = tmp_path / "report.json"

        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--capture-manifest", str(cm_json),
            "--sparse-dir", str(sparse_dir.parent),
            "--output", str(out),
        )
        assert proc.returncode == 0, proc.stderr
        assert out.is_file()

        report = RegistrationQualityReport.model_validate_json(
            out.read_text(encoding="utf-8"))
        assert report.engine == "colmap"
        assert report.registered_count == total
        assert report.quality_accepted is True
        # training_allowed needs: non-mock + capture_manifest + no rejections
        policy = RegistrationQualityPolicy.model_validate_json(
            policy_json.read_text(encoding="utf-8"))
        assert derive_training_allowed(report, policy) is True
        assert report.training_allowed is True
        assert report.model_enumeration is not None
        assert report.model_enumeration.selected_model_index == 0
        assert report.capture_manifest_sha256 is not None

    def test_colmap_without_capture_manifest_not_training_allowed(self, tmp_path):
        """COLMAP engine but no --capture-manifest → training_allowed=False
        (capture_manifest_sha is None)."""
        total = 15
        reg_json = tmp_path / "reg" / "registration.json"
        _write_registration_json(reg_json, engine="colmap",
                                   registered=total, total=total)
        policy_json = tmp_path / "reg" / "policy.json"
        _write_policy_json(policy_json)
        sparse_dir = tmp_path / "colmap_ws" / "sparse" / "0"
        _write_colmap_sparse(sparse_dir, images=[
            f"IMG_{i:04d}.jpg" for i in range(total)])
        out = tmp_path / "report.json"

        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--sparse-dir", str(sparse_dir.parent),
            "--output", str(out),
        )
        assert proc.returncode == 0, proc.stderr

        report = RegistrationQualityReport.model_validate_json(
            out.read_text(encoding="utf-8"))
        assert report.engine == "colmap"
        assert report.capture_manifest_sha256 is None
        assert report.training_allowed is False

    def test_colmap_rejected_registration_quality(self, tmp_path):
        """COLMAP engine but only 3/15 registered → quality_accepted=False."""
        total = 15
        reg_json = tmp_path / "reg" / "registration.json"
        _write_registration_json(reg_json, engine="colmap",
                                   registered=3, total=total)
        policy_json = tmp_path / "reg" / "policy.json"
        _write_policy_json(policy_json)
        cm_json = tmp_path / "reg" / "capture_manifest.json"
        _write_capture_manifest(cm_json, total=total)
        sparse_dir = tmp_path / "colmap_ws" / "sparse" / "0"
        _write_colmap_sparse(sparse_dir, images=[
            f"IMG_{i:04d}.jpg" for i in range(3)])
        out = tmp_path / "report.json"

        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--capture-manifest", str(cm_json),
            "--sparse-dir", str(sparse_dir.parent),
            "--output", str(out),
        )
        assert proc.returncode == 0, proc.stderr

        report = RegistrationQualityReport.model_validate_json(
            out.read_text(encoding="utf-8"))
        assert report.quality_accepted is False
        assert report.training_allowed is False
        assert report.rejection_reasons  # non-empty


# ============================================================
# Error cases: fail-closed wiring
# ============================================================

class TestEmitRegistrationQualityErrors:
    def test_missing_registration_json_exits(self, tmp_path):
        policy_json = tmp_path / "policy.json"
        _write_policy_json(policy_json)
        proc = _run_cli(
            "--registration-json", str(tmp_path / "nonexistent.json"),
            "--policy", str(policy_json),
            "--output", str(tmp_path / "out.json"),
        )
        assert proc.returncode != 0
        assert "not found" in proc.stderr.lower()

    def test_missing_policy_exits(self, tmp_path):
        reg_json = tmp_path / "registration.json"
        _write_registration_json(reg_json)
        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(tmp_path / "nonexistent.json"),
            "--output", str(tmp_path / "out.json"),
        )
        assert proc.returncode != 0
        assert "not found" in proc.stderr.lower()

    def test_colmap_without_sparse_dir_exits(self, tmp_path):
        reg_json = tmp_path / "registration.json"
        _write_registration_json(reg_json, engine="colmap")
        policy_json = tmp_path / "policy.json"
        _write_policy_json(policy_json)
        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--output", str(tmp_path / "out.json"),
        )
        assert proc.returncode != 0
        assert "sparse" in proc.stderr.lower()

    def test_mock_with_sparse_dir_exits(self, tmp_path):
        """--sparse-dir not allowed for engine='mock'."""
        reg_json = tmp_path / "registration.json"
        _write_registration_json(reg_json, engine="mock")
        policy_json = tmp_path / "policy.json"
        _write_policy_json(policy_json)
        sparse_dir = tmp_path / "sparse"
        _write_colmap_sparse(sparse_dir, images=["img0.jpg"])
        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--sparse-dir", str(sparse_dir),
            "--output", str(tmp_path / "out.json"),
        )
        assert proc.returncode != 0
        assert "not allowed" in proc.stderr.lower()

    def test_bad_registration_json_exits(self, tmp_path):
        reg_json = tmp_path / "registration.json"
        reg_json.write_text("{not valid json", encoding="utf-8")
        policy_json = tmp_path / "policy.json"
        _write_policy_json(policy_json)
        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--output", str(tmp_path / "out.json"),
        )
        assert proc.returncode != 0
        assert "parse" in proc.stderr.lower()

    def test_bad_policy_json_exits(self, tmp_path):
        reg_json = tmp_path / "registration.json"
        _write_registration_json(reg_json)
        policy_json = tmp_path / "policy.json"
        policy_json.write_text("{not valid json", encoding="utf-8")
        proc = _run_cli(
            "--registration-json", str(reg_json),
            "--policy", str(policy_json),
            "--output", str(tmp_path / "out.json"),
        )
        assert proc.returncode != 0
        assert "parse" in proc.stderr.lower()
