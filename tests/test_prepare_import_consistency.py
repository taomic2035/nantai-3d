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

import pytest

from pipeline.splat_provenance import SplatConsistency, Verdict
from scripts.prepare_import import _check_consistency, _load_sparse_enumeration

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

        monkeypatch.setattr(
            "pipeline.splat_provenance.check_splat_against_sparse", _fake_check)
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
                verdict=Verdict.CONTRADICTED, reason="contradiction found"))
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
            lambda p, s: SplatConsistency(
                verdict=Verdict.UNKNOWN, reason="sparse file missing"))
        result = _check_consistency(ply, sparse)
        assert result is True

    def test_prints_no_conclusion_message(self, tmp_path, monkeypatch, capsys):
        ply = tmp_path / "model.ply"
        ply.write_bytes(b"fake-ply")
        sparse = tmp_path / "points3D.txt"
        sparse.write_text("# empty")

        monkeypatch.setattr(
            "pipeline.splat_provenance.check_splat_against_sparse",
            lambda p, s: SplatConsistency(
                verdict=Verdict.UNKNOWN, reason="cannot load"))
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
                verdict=Verdict.NOT_CONTRADICTED, reason="no contradiction"))
        result = _check_consistency(ply, sparse)
        assert result is True

    def test_prints_not_proof_message(self, tmp_path, monkeypatch, capsys):
        ply = tmp_path / "model.ply"
        ply.write_bytes(b"fake-ply")
        sparse = tmp_path / "points3D.txt"
        sparse.write_text("# empty")

        monkeypatch.setattr(
            "pipeline.splat_provenance.check_splat_against_sparse",
            lambda p, s: SplatConsistency(
                verdict=Verdict.NOT_CONTRADICTED, reason="ok"))
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
                    model_index=0, image_count=15, point3d_count=5000,
                ),
            ),
            selected_model_index=0,
            selection_rule="single_model",
            total_input_images=15,
        )
        sparse_dir = tmp_path / "sparse"
        sparse_dir.mkdir()
        (sparse_dir / "sparse_enumeration.json").write_text(
            enum.model_dump_json(), encoding="utf-8")

        result = _load_sparse_enumeration(sparse_dir)
        assert result is not None
        assert len(result.models) == 1
        assert result.models[0].image_count == 15
        assert result.selected_model_index == 0
