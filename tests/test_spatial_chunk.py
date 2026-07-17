"""大重建场景的空间分块: 无损分块 / 流式 manifest / provenance 不变。

一个真实重建 (COLMAP + 云 GPU 3DGS) 是【单个】可能上百万高斯的 .ply, viewer 只能整块
加载 —— 大场景下载慢、无空间裁剪。空间分块把它切成按 XY 网格的 chunk + LOD + 流式
manifest, 让 viewer 只载相机附近的块。

铁律: 分块是【纯空间重打包】—— 不改几何、不改坐标、不改 provenance。每个高斯恰好落入
一个块 (无损、不重复); 分块产物绝不比源场景声称更多信任。
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from pipeline.gaussian_scene import GaussianScene
from pipeline.spatial_chunk import partition_scene_to_chunks


def _scene(n=800, span=200.0, seed=3, **kw):
    rng = np.random.default_rng(seed)
    xyz = np.column_stack([
        rng.uniform(-span / 2, span / 2, n),
        rng.uniform(-span / 2, span / 2, n),
        rng.uniform(0, 12, n),
    ])
    rgb = np.clip(rng.uniform(0, 1, (n, 3)), 0, 1)
    return GaussianScene(xyz, rgb, rng.uniform(0.3, 1.0, n),
                         rng.uniform(0.05, 0.4, (n, 3)), **kw)


class TestPartition:
    def test_partition_is_lossless_and_spatially_binned(self, tmp_path):
        """铁律: 每个高斯恰好落一个块 (总数守恒), 且落在自己 bin 的 XY 范围内。"""
        scene = _scene()
        manifest = partition_scene_to_chunks(scene, tmp_path, chunk_size_m=50.0)

        assert manifest["total_points"] == len(scene)
        assert sum(c["point_count"] for c in manifest["chunks"]) == len(scene)
        assert manifest["total_chunks"] == len(manifest["chunks"])
        assert all(c["point_count"] > 0 for c in manifest["chunks"]), "空块不应写出"

        seen = 0
        for chunk in manifest["chunks"]:
            loaded = GaussianScene.load_ply(tmp_path / chunk["ply_file"])
            seen += len(loaded)
            lo_x, lo_y = chunk["x"] * 50.0, chunk["y"] * 50.0
            assert np.all(loaded.xyz[:, 0] >= lo_x) and np.all(loaded.xyz[:, 0] < lo_x + 50.0)
            assert np.all(loaded.xyz[:, 1] >= lo_y) and np.all(loaded.xyz[:, 1] < lo_y + 50.0)
        assert seen == len(scene), "重载全部块须恰好还原总高斯数 (无损/不重复)"

    def test_coordinates_stay_absolute_and_unmodified(self, tmp_path):
        """纯重打包: 坐标绝不被平移/改动 —— 重载所有块的点集 == 原场景点集
        (逐轴排序比对, 容差仅为 ply 的 float32 往返)。"""
        scene = _scene(n=300, span=120.0, seed=9)
        manifest = partition_scene_to_chunks(scene, tmp_path, chunk_size_m=40.0)
        rejoined = np.concatenate([
            GaussianScene.load_ply(tmp_path / c["ply_file"]).xyz
            for c in manifest["chunks"]
        ])
        assert len(rejoined) == len(scene)
        for axis in range(3):
            assert np.allclose(np.sort(rejoined[:, axis]),
                               np.sort(scene.xyz[:, axis]), atol=1e-3)

    def test_manifest_carries_bounds_aabb_extent_and_source_frame(self, tmp_path):
        """manifest 须让 viewer 无需下载 ply 即可裁剪/取景, 且如实带源 frame 契约。"""
        scene = _scene(frame_id="world-enu", units="meters")
        manifest = partition_scene_to_chunks(scene, tmp_path, chunk_size_m=50.0)

        assert manifest["kind"] == "spatial-chunks"
        assert manifest["chunk_size_m"] == 50.0
        b = manifest["bounds"]
        assert len(b["min"]) == 3 and len(b["max"]) == 3
        assert b["max"][2] > b["min"][2]
        assert np.isclose(b["min"][0], scene.xyz[:, 0].min(), atol=1e-3)
        assert np.isclose(b["max"][1], scene.xyz[:, 1].max(), atol=1e-3)

        ext = manifest["extent"]
        xs = [c["x"] for c in manifest["chunks"]]
        assert ext == {"x_min": min(xs), "x_max": max(xs),
                       "y_min": min(c["y"] for c in manifest["chunks"]),
                       "y_max": max(c["y"] for c in manifest["chunks"])}
        for chunk in manifest["chunks"]:
            aabb = chunk["aabb"]
            assert len(aabb["min"]) == 3 and aabb["max"][0] >= aabb["min"][0]
            assert isinstance(aabb["min"][0], float)  # 原生 float, json 可序列化

        # 如实带源坐标契约 (分块绝不改 frame/units)
        assert manifest["source"]["frame_id"] == "world-enu"
        assert manifest["source"]["units"] == "meters"
        json.dumps(manifest)

    def test_lod_levels_are_progressively_sparser(self, tmp_path):
        """每块出 LOD: lod0 < lod1 < 全量(lod2), viewer 按距离选级省带宽。"""
        scene = _scene(n=2000)
        manifest = partition_scene_to_chunks(
            scene, tmp_path, chunk_size_m=100.0, lod_fractions={0: 0.1, 1: 0.4})
        chunk = max(manifest["chunks"], key=lambda c: c["point_count"])
        full = len(GaussianScene.load_ply(tmp_path / chunk["lod"]["2"]))
        lod0 = len(GaussianScene.load_ply(tmp_path / chunk["lod"]["0"]))
        lod1 = len(GaussianScene.load_ply(tmp_path / chunk["lod"]["1"]))
        assert lod0 < lod1 < full
        assert chunk["lod"]["2"] == chunk["ply_file"]

    def test_partition_preserves_provenance_and_never_upgrades_it(self, tmp_path):
        """分块是纯空间重打包: 每块继承源的 frame/units/transform 历史, 绝不提升信任。"""
        scene = _scene(
            n=200, frame_id="sfm-local", units="arbitrary",
            applied_transform_ids=["xf-abc"],
            applied_transform_paths=[["xf-abc"]],
        )
        manifest = partition_scene_to_chunks(scene, tmp_path, chunk_size_m=60.0)
        assert manifest["source"]["frame_id"] == "sfm-local"
        assert manifest["source"]["units"] == "arbitrary"
        assert manifest["source"]["applied_transform_ids"] == ["xf-abc"]
        for chunk in manifest["chunks"]:
            loaded = GaussianScene.load_ply(tmp_path / chunk["ply_file"])
            assert loaded.frame_id == "sfm-local"
            assert loaded.units == "arbitrary"
            assert list(loaded.applied_transform_ids) == ["xf-abc"]

    def test_manifest_written_lf_and_deterministic(self, tmp_path):
        """manifest 跨平台 LF 字节可复现 (与 trust root 惯例一致); 分块确定。"""
        scene = _scene(n=400)
        first = partition_scene_to_chunks(scene, tmp_path / "a", chunk_size_m=50.0)
        second = partition_scene_to_chunks(scene, tmp_path / "b", chunk_size_m=50.0)
        assert first == second, "同一场景分块须确定"
        raw = (tmp_path / "a" / "chunks.json").read_bytes()
        assert b"\r\n" not in raw
        assert json.loads(raw.decode("utf-8"))["total_points"] == len(scene)

    def test_rejects_non_positive_chunk_size(self, tmp_path):
        scene = _scene(n=50)
        for bad in (0.0, -10.0, float("nan")):
            with pytest.raises(ValueError, match="chunk_size_m"):
                partition_scene_to_chunks(scene, tmp_path, chunk_size_m=bad)

    def test_empty_scene_fails_closed(self, tmp_path):
        with pytest.raises(ValueError, match="empty"):
            partition_scene_to_chunks(
                GaussianScene(np.zeros((0, 3)), np.zeros((0, 3))), tmp_path,
                chunk_size_m=50.0)
