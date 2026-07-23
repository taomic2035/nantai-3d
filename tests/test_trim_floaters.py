"""CLI tests for scripts/trim_floaters.py.

``test_outlier_trim.py`` covers the ``pipeline.outlier_trim`` library
(honest guards: no default thresholds, dry-run by default, lossy provenance).
These tests cover the **CLI entry point** that users actually invoke — the
argparse wiring, the ``build_rules`` translation, and the dry-run / confirm /
sweep paths that the library tests do not exercise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pytest

# Make ``scripts/`` importable.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.gaussian_scene import GaussianScene  # noqa: E402
from pipeline.outlier_trim import (  # noqa: E402
    TRIM_MANIFEST_SUFFIX,
    OccupancyRule,
    OpacityRule,
    ScaleRule,
)
from scripts.trim_floaters import build_rules, main  # noqa: E402


def _ns(**kw) -> argparse.Namespace:
    """Build a Namespace with all build_rules fields defaulted to None."""
    defaults = dict(min_occupancy=None, voxel_size=None,
                    max_scale=None, min_opacity=None)
    defaults.update(kw)
    return argparse.Namespace(**defaults)


@pytest.fixture
def clustered_ply(tmp_path: Path) -> Path:
    """A small PLY with a dense core (400 pts) + 20 isolated outliers.

    Saved to disk because the CLI loads via ``GaussianScene.load_ply``.
    Occupancy with voxel_size=5 cleanly separates core (kept) from outliers
    (dropped), mirroring ``test_outlier_trim.py::clustered_scene``.
    """
    rng = np.random.default_rng(3)
    core = rng.normal(0, 0.3, (400, 3))
    far = np.stack([np.arange(20) * 50.0 + 100.0] * 3, axis=1)
    xyz = np.concatenate([core, far])
    n = len(xyz)
    scene = GaussianScene(
        xyz=xyz,
        rgb=np.full((n, 3), 0.5),
        opacity=np.full(n, 0.5),
        scale=np.full((n, 3), 0.05),
        frame_id="sfm-local",
        units="unknown",
    )
    ply = tmp_path / "input.ply"
    scene.save_ply(ply, flavor="3dgs")
    return ply


class TestBuildRules:
    def test_no_thresholds_yields_empty_list(self):
        assert build_rules(_ns()) == []

    def test_occupancy_requires_voxel_size(self):
        with pytest.raises(SystemExit, match="--voxel-size"):
            build_rules(_ns(min_occupancy=5))

    def test_occupancy_with_voxel_size(self):
        rules = build_rules(_ns(min_occupancy=5, voxel_size=2.0))
        assert len(rules) == 1
        assert isinstance(rules[0], OccupancyRule)
        assert rules[0].voxel_size == 2.0
        assert rules[0].min_occupancy == 5

    def test_scale_rule(self):
        rules = build_rules(_ns(max_scale=1.0))
        assert len(rules) == 1
        assert isinstance(rules[0], ScaleRule)

    def test_opacity_rule(self):
        rules = build_rules(_ns(min_opacity=0.1))
        assert len(rules) == 1
        assert isinstance(rules[0], OpacityRule)

    def test_multiple_rules_are_collected(self):
        rules = build_rules(_ns(min_occupancy=3, voxel_size=5.0,
                                 max_scale=1.0, min_opacity=0.1))
        assert len(rules) == 3


class TestMainErrors:
    def test_nonexistent_input_exits(self, tmp_path):
        with pytest.raises(SystemExit, match="输入不存在"):
            main([str(tmp_path / "nope.ply"), "--min-occupancy", "5",
                  "--voxel-size", "5"])

    def test_no_rules_exits(self, clustered_ply):
        with pytest.raises(SystemExit, match="未指定任何判据"):
            main([str(clustered_ply), "-o", "out.ply"])

    def test_sweep_without_voxel_size_exits(self, clustered_ply):
        with pytest.raises(SystemExit, match="--voxel-size"):
            main([str(clustered_ply), "--sweep"])


class TestMainDryRun:
    def test_dry_run_without_output_reports_but_writes_nothing(
            self, clustered_ply, tmp_path, capsys):
        rc = main([str(clustered_ply),
                   "--min-occupancy", "2", "--voxel-size", "5"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dry-run" in out.lower()
        assert "未写任何文件" in out
        # No output PLY or manifest written (input.ply is the fixture, not output)
        assert not list(tmp_path.glob("*.trim_manifest.json"))

    def test_dry_run_with_output_reports_but_does_not_write(
            self, clustered_ply, tmp_path, capsys):
        out_ply = tmp_path / "trimmed.ply"
        rc = main([str(clustered_ply), "-o", str(out_ply),
                   "--min-occupancy", "2", "--voxel-size", "5"])
        assert rc == 0
        assert not out_ply.exists()
        manifest = tmp_path / (out_ply.name + TRIM_MANIFEST_SUFFIX)
        assert not manifest.exists()
        captured = capsys.readouterr().out
        assert "dry-run" in captured.lower()


class TestMainConfirm:
    def test_confirm_writes_ply_and_manifest(
            self, clustered_ply, tmp_path, capsys):
        out_ply = tmp_path / "trimmed.ply"
        rc = main([str(clustered_ply), "-o", str(out_ply),
                   "--min-occupancy", "2", "--voxel-size", "5",
                   "--confirm"])
        assert rc == 0
        assert out_ply.exists()
        manifest = tmp_path / (out_ply.name + TRIM_MANIFEST_SUFFIX)
        assert manifest.exists()
        captured = capsys.readouterr().out
        assert "已写出" in captured

    def test_confirm_drops_outliers_keeps_core(
            self, clustered_ply, tmp_path):
        out_ply = tmp_path / "trimmed.ply"
        main([str(clustered_ply), "-o", str(out_ply),
              "--min-occupancy", "2", "--voxel-size", "5",
              "--confirm"])
        reloaded = GaussianScene.load_ply(out_ply)
        # 400 core points kept, 20 outliers dropped
        assert len(reloaded) == 400

    def test_confirm_refuses_overwrite(self, clustered_ply, tmp_path):
        out_ply = tmp_path / "trimmed.ply"
        out_ply.write_bytes(b"existing")
        with pytest.raises((SystemExit, ValueError), match="已存在"):
            main([str(clustered_ply), "-o", str(out_ply),
                  "--min-occupancy", "2", "--voxel-size", "5",
                  "--confirm"])


class TestMainSweep:
    def test_sweep_scans_thresholds_and_writes_nothing(
            self, clustered_ply, tmp_path, capsys):
        rc = main([str(clustered_ply), "--sweep", "--voxel-size", "5"])
        assert rc == 0
        out = capsys.readouterr().out
        # Sweep table has threshold values
        for t in ("1", "2", "3", "5", "10", "20", "50"):
            assert t in out
        # Sweep never writes any output or manifest
        assert not list(tmp_path.glob("*.trim_manifest.json"))

    def test_sweep_reports_input_bounds_and_units(
            self, clustered_ply, capsys):
        main([str(clustered_ply), "--sweep", "--voxel-size", "5"])
        out = capsys.readouterr().out
        assert "高斯" in out
        assert "unknown" in out  # units from the scene
