"""prepare_import.py _check_consistency + _load_sparse_enumerance unit tests.

Tests the three-state splat-vs-sparse consistency check (CONTRADICTED /
UNKNOWN / NOT_CONTRADICTED) and the sparse enumeration loader.  These are
internal helpers of ``scripts/prepare_import.py`` that are not exercised by
the P0.3 or training CLI integration tests.

Provenance safety contract:
- CONTRADICTED -> fail-closed (return False)
- UNKNOWN -> pass but print "no conclusion" (NOT a pass)
- NOT_CONTRADICTED -> pass but print "not proof" (NOT a pass)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.splat_provenance import SplatConsistency, Verdict
from scripts.prepare_import import (
    _check_consistency,
    _load_sparse_enumeration,
    _stable_read_bytes,
    _validate_registration_quality,
)

# ============================================================
# _check_consistency: three-state verdict
# ============================================================


class TestCheckConsistencyContradicted:
    """CONTRADICTED verdict must fail-closed."""

    def test_returns_false(self, tmp_path, monkeypatch, capsys):
        ply = tmp_path / "model.ply"
        ply.write_bytes(b"fake-ply")
        sparse = tmp_path / "points3D.txt"
        sparse.write_text("# empty")

        def _fake_check(p, s):
            return SplatConsistency(
                verdict=Verdict.CONTRADICTED,
                reason="splat points contradict sparse geometry",
            )

        monkeypatch.setattr("pipeline.splat_provenance.check_splat_against_sparse", _fake_check)
        result = _check_consistency(ply, sparse)
        assert result is False

    def test_prints_fail_closed_to_stderr(self, tmp_path, monkeypatch, capsys):
        ply = tmp_path / "model.ply"
        ply.write_bytes(b"fake-ply")
        sparse = tmp_path / "points3D.txt"
        sparse.write_text("# empty")

        monkeypatch.setattr(
            "pipeline.splat_provenance.check_splat_against_sparse",
            lambda p, s: SplatConsistency(
                verdict=Verdict.CONTRADICTED, reason="contradiction found"
            ),
        )
        _check_consistency(ply, sparse)
        captured = capsys.readouterr()
        assert "[FAIL-CLOSED]" in captured.err
        assert "拒绝" in captured.err


class TestCheckConsistencyUnknown:
    """UNKNOWN verdict must pass but explicitly state no conclusion."""

    def test_returns_true(self, tmp_path, monkeypatch):
        ply = tmp_path / "model.ply"
        ply.write_bytes(b"fake-ply")
        sparse = tmp_path / "points3D.txt"
        sparse.write_text("# empty")

        monkeypatch.setattr(
            "pipeline.splat_provenance.check_splat_against_sparse",
            lambda p, s: SplatConsistency(verdict=Verdict.UNKNOWN, reason="sparse file missing"),
        )
        result = _check_consistency(ply, sparse)
        assert result is True

    def test_prints_no_conclusion_message(self, tmp_path, monkeypatch, capsys):
        ply = tmp_path / "model.ply"
        ply.write_bytes(b"fake-ply")
        sparse = tmp_path / "points3D.txt"
        sparse.write_text("# empty")

        monkeypatch.setattr(
            "pipeline.splat_provenance.check_splat_against_sparse",
            lambda p, s: SplatConsistency(verdict=Verdict.UNKNOWN, reason="cannot load"),
        )
        _check_consistency(ply, sparse)
        captured = capsys.readouterr()
        assert "[UNKNOWN]" in captured.out
        assert "没有任何结论" in captured.out


class TestCheckConsistencyNotContradicted:
    """NOT_CONTRADICTED verdict must pass but explicitly say it's not proof."""

    def test_returns_true(self, tmp_path, monkeypatch):
        ply = tmp_path / "model.ply"
        ply.write_bytes(b"fake-ply")
        sparse = tmp_path / "points3D.txt"
        sparse.write_text("# empty")

        monkeypatch.setattr(
            "pipeline.splat_provenance.check_splat_against_sparse",
            lambda p, s: SplatConsistency(
                verdict=Verdict.NOT_CONTRADICTED, reason="no contradiction"
            ),
        )
        result = _check_consistency(ply, sparse)
        assert result is True

    def test_prints_not_proof_message(self, tmp_path, monkeypatch, capsys):
        ply = tmp_path / "model.ply"
        ply.write_bytes(b"fake-ply")
        sparse = tmp_path / "points3D.txt"
        sparse.write_text("# empty")

        monkeypatch.setattr(
            "pipeline.splat_provenance.check_splat_against_sparse",
            lambda p, s: SplatConsistency(verdict=Verdict.NOT_CONTRADICTED, reason="ok"),
        )
        _check_consistency(ply, sparse)
        captured = capsys.readouterr()
        assert "[未发现矛盾]" in captured.out
        assert "**不是**通过" in captured.out


# ============================================================
# _load_sparse_enumeration: file-based loader
# ============================================================


class TestLoadSparseEnumeration:
    """_load_sparse_enumeration reads sparse_enumeration.json or fails closed."""

    def test_raises_file_not_found_when_json_missing(self, tmp_path):
        sparse_dir = tmp_path / "sparse"
        sparse_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="sparse_enumeration.json"):
            _load_sparse_enumeration(sparse_dir)

    def test_loads_valid_enumeration_json(self, tmp_path):
        from pipeline.registration_quality import (
            SparseModelEntry,
            SparseModelEnumeration,
        )

        enum = SparseModelEnumeration(
            models=(
                SparseModelEntry(
                    model_index=0,
                    image_count=15,
                    point3d_count=5000,
                ),
            ),
            selected_model_index=0,
            selection_rule="single_model",
            total_input_images=15,
        )
        sparse_dir = tmp_path / "sparse"
        sparse_dir.mkdir()
        (sparse_dir / "sparse_enumeration.json").write_text(
            enum.model_dump_json(), encoding="utf-8"
        )

        result = _load_sparse_enumeration(sparse_dir)
        assert result is not None
        assert len(result.models) == 1
        assert result.models[0].image_count == 15
        assert result.selected_model_index == 0


# ============================================================
# _validate_registration_quality: SHA binding (no check-then-reopen)
# ============================================================


def _build_rq_artifacts(tmp_path):
    """Build registration.json + policy.json + quality-report.json on disk."""
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

    intrinsics = CameraIntrinsics(
        width=1920,
        height=1080,
        fx=1000.0,
        fy=1000.0,
        cx=960.0,
        cy=540.0,
    )
    reg = RegistrationResult(
        schema_version=2,
        engine="mock",
        pose_frame=CoordinateFrame(
            frame_id="sfm-local",
            handedness=Handedness.RIGHT,
            axes=AxisConvention.SFM_ARBITRARY,
            units=CoordinateUnits.ARBITRARY,
            metric_status=MetricStatus.ARBITRARY,
            geo_aligned=GeoAlignment.UNALIGNED,
            provenance=FrameProvenance.SFM,
        ),
        world_frame=None,
        alignment_status=AlignmentStatus.UNALIGNED,
        sessions=[
            CaptureSession(
                session_id="s0",
                kind="photo_batch",
                source="test",
                images=["img000.jpg"],
            )
        ],
        poses=[
            CameraPose(
                image="img000.jpg",
                session_id="s0",
                quat_wxyz=[1.0, 0.0, 0.0, 0.0],
                t_xyz=[0.0, 0.0, 0.0],
                intrinsics=intrinsics,
            )
        ],
    )
    reg_json = tmp_path / "rq" / "registration.json"
    reg_json.parent.mkdir(parents=True)
    reg_bytes = reg.model_dump_json(indent=2).encode("utf-8")
    reg_json.write_bytes(reg_bytes)

    policy = RegistrationQualityPolicy(
        min_registered_count=1,
        min_registered_ratio=0.1,
        min_session_coverage_ratio=0.1,
        max_unregistered_consecutive_run=5,
        min_largest_connected_model_share=0.1,
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


class TestValidateRegistrationQualityShaBinding:
    """_validate_registration_quality must not check-then-reopen the report.

    The report is read once for model_validate_json and again for SHA-256.
    This creates a TOCTOU window where the file can be swapped between reads,
    causing the SHA to not match the validated bytes.
    """

    def test_report_read_exactly_once_not_check_then_reopen(
        self,
        tmp_path,
        monkeypatch,
    ):
        """RED: registration_quality_report must be read exactly once."""
        import scripts.prepare_import as prepare_import

        reg_json, policy_json, report_json = _build_rq_artifacts(tmp_path)

        read_calls: list[str] = []
        original_stable_read = prepare_import._stable_read_bytes

        def counting_stable_read(path):
            if path == report_json:
                read_calls.append("stable_read_bytes")
            return original_stable_read(path)

        monkeypatch.setattr(prepare_import, "_stable_read_bytes", counting_stable_read)

        validated, _, _, _ = _validate_registration_quality(
            report_json,
            reg_json,
            policy_json,
            None,
            None,
        )

        assert validated is True
        assert len(read_calls) == 1, (
            f"registration_quality_report read {len(read_calls)} times: "
            f"{read_calls}; expected exactly 1 (no check-then-reopen)"
        )

    def test_report_sha_matches_validated_bytes(self, tmp_path):
        """SHA must equal the bytes that were actually validated."""
        import hashlib

        reg_json, policy_json, report_json = _build_rq_artifacts(tmp_path)
        report_bytes = report_json.read_bytes()

        validated, _, _, report_sha = _validate_registration_quality(
            report_json,
            reg_json,
            policy_json,
            None,
            None,
        )

        assert validated is True
        expected_sha = hashlib.sha256(report_bytes).hexdigest()
        assert report_sha == expected_sha, "report SHA must match the bytes on disk"


# ============================================================
# _stable_read_bytes: provenance input trust anchor
# ============================================================


def test_stable_read_bytes_rejects_non_regular_inputs(tmp_path: Path) -> None:
    """RED->GREEN: provenance input reads must refuse non-regular files.

    ``_stable_read_bytes`` is the trust anchor for PLY / registration.json /
    quality-report / policy / capture-manifest bytes hashed into the import
    contract. It must reject missing paths and directories (and symlinks,
    covered by the next test) so a redirected input cannot be hashed as if it
    were the intended regular file.
    """
    missing = tmp_path / "missing.bin"
    with pytest.raises(OSError):
        _stable_read_bytes(missing)

    directory = tmp_path / "dir"
    directory.mkdir()
    with pytest.raises(OSError):
        _stable_read_bytes(directory)

    regular = tmp_path / "regular.bin"
    regular.write_bytes(b"provenance-bytes")
    assert _stable_read_bytes(regular) == b"provenance-bytes"


def test_stable_read_bytes_rejects_symlinked_input(tmp_path: Path) -> None:
    """RED->GREEN: a symlinked provenance input must not be followed.

    A symlinked PLY/registration/policy would otherwise have its target hashed
    into the import contract. ``O_NOFOLLOW`` + ``lstat`` rejects the symlink at
    the literal path.
    """
    target = tmp_path / "real.ply"
    target.write_bytes(b"ply-bytes")
    link = tmp_path / "splat.ply"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    with pytest.raises(OSError):
        _stable_read_bytes(link)
