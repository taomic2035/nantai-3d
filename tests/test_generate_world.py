"""generate_world CLI + GLM 异步布局生成路径的测试。

已覆盖 (test_render_on_demand.py): _grid_range / generate_layouts_mock。
本文件覆盖未测试的:
- generate_layouts_glm: --use-glm 异步路径 (无 key 时降级 mock, 仍须正确产出布局)
- main() CLI: --no-ply / --center / 默认参数入口 (subprocess, 不真跑 ply 渲染)
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.generate_world import generate_layouts_glm, generate_layouts_mock


class TestGenerateLayoutsGlm:
    """无 ZHIPU_API_KEY 时 GLMLayoutGenerator 降级为 mock, 异步路径仍须正确产出布局."""

    def test_glm_mock_fallback_produces_layouts(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        stats = asyncio.run(generate_layouts_glm(2, 42, tmp_path / "glm_layouts"))
        assert stats["chunks"] == 4
        for cx in range(2):
            for cy in range(2):
                assert (tmp_path / "glm_layouts" / f"chunk_{cx}_{cy}.json").exists()

    def test_glm_mock_fallback_center_negative_quadrant(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        stats = asyncio.run(generate_layouts_glm(3, 7, tmp_path / "glm_center", center=True))
        assert stats["chunks"] == 9
        # center=True 时含负象限 (size=3 -> 范围 (-1, 2) 即 -1,0,1)
        assert (tmp_path / "glm_center" / "chunk_-1_-1.json").exists()
        assert (tmp_path / "glm_center" / "chunk_1_1.json").exists()

    def test_glm_mock_stats_match_mock_generator(self, tmp_path, monkeypatch):
        """GLM mock 降级路径产出的布局应与直接 MockLayoutGenerator 一致."""
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        glm_stats = asyncio.run(generate_layouts_glm(2, 42, tmp_path / "glm"))
        mock_stats = generate_layouts_mock(2, 42, tmp_path / "mock")
        assert glm_stats == mock_stats

    def test_glm_mock_layout_bytes_match_mock_generator(self, tmp_path, monkeypatch):
        """GLM 降级路径写出的 JSON 字节须与 mock 直写一致 (确定性)."""
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        asyncio.run(generate_layouts_glm(1, 42, tmp_path / "glm"))
        generate_layouts_mock(1, 42, tmp_path / "mock")
        glm_bytes = (tmp_path / "glm" / "chunk_0_0.json").read_bytes()
        mock_bytes = (tmp_path / "mock" / "chunk_0_0.json").read_bytes()
        assert glm_bytes == mock_bytes


class TestMainCli:
    """main() CLI 入口测试 (subprocess, --no-ply 不真跑渲染)."""

    @pytest.fixture
    def repo_root(self):
        return Path(__file__).resolve().parent.parent

    def _run(self, repo_root, *args, cwd=None):
        proc = subprocess.run(
            [sys.executable, "-m", "pipeline.generate_world", *args],
            cwd=cwd or repo_root,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    def test_no_ply_center_produces_negative_quadrant(self, tmp_path, repo_root):
        """--no-ply --center --size 2 只产 layouts 含负象限, 不渲 ply."""
        layouts = tmp_path / "layouts"
        out = self._run(
            repo_root,
            "--no-ply",
            "--size",
            "2",
            "--center",
            "--layouts-dir",
            str(layouts),
        )
        assert "跳过" in out
        # center + size=2 -> 范围 (-1, 1) 即 -1, 0
        assert (layouts / "chunk_-1_-1.json").exists()
        assert (layouts / "chunk_0_0.json").exists()
        assert not (layouts / "chunk_1_0.json").exists()

    def test_default_uses_mock_generator(self, tmp_path, repo_root):
        """默认 (无 --use-glm) 用 Mock, --no-ply 产 layouts."""
        layouts = tmp_path / "def_layouts"
        out = self._run(
            repo_root,
            "--no-ply",
            "--size",
            "1",
            "--layouts-dir",
            str(layouts),
        )
        assert "Mock" in out
        assert (layouts / "chunk_0_0.json").exists()

    def test_seed_affects_layout(self, tmp_path, repo_root):
        """不同 seed 产出不同布局 (确定性但 seed 敏感)."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        self._run(repo_root, "--no-ply", "--size", "1", "--seed", "42", "--layouts-dir", str(a))
        self._run(repo_root, "--no-ply", "--size", "1", "--seed", "999", "--layouts-dir", str(b))
        content_a = (a / "chunk_0_0.json").read_text(encoding="utf-8")
        content_b = (b / "chunk_0_0.json").read_text(encoding="utf-8")
        assert content_a != content_b

    def test_layouts_are_valid_chunk_layout(self, tmp_path, repo_root):
        """CLI 产出的 layout JSON 须能被 ChunkLayout schema 解析."""
        from pipeline.schema import ChunkLayout

        layouts = tmp_path / "schema_layouts"
        self._run(repo_root, "--no-ply", "--size", "1", "--layouts-dir", str(layouts))
        data = json.loads((layouts / "chunk_0_0.json").read_text(encoding="utf-8"))
        layout = ChunkLayout(**data)
        assert layout.chunk_id.x == 0
        assert layout.chunk_id.y == 0
        assert layout.world_seed == 42
