"""端到端重建: mock 全链路 / 导入引擎 / 变清晰 (区域替换) / 视频抽帧"""
import json

import numpy as np
import pytest

from pipeline.gaussian_scene import GaussianScene
from pipeline.reconstruct import reconstruct


class TestMockPipeline:
    def test_end_to_end(self, photos_dir, tmp_path):
        m = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                        web_dir=tmp_path / "web", engine="mock",
                        reg_engine="mock")
        assert m["gaussian_count"] > 1000
        assert m["registration_engine"] == "mock"
        assert set(m["lod"]) == {"0", "1", "2"}
        # 会话信息: 视频 + 照片混合
        kinds = {s["kind"] for s in m["sessions"]}
        assert kinds == {"video", "photo_batch"}
        # 产物齐备且可解析
        assert (tmp_path / "recon" / "registration.json").exists()
        assert (tmp_path / "recon" / "scene_full.ply").exists()
        for f in m["lod"].values():
            assert (tmp_path / "web" / f).exists()
        manifest_on_disk = json.loads(
            (tmp_path / "web" / "recon_manifest.json").read_text())
        assert manifest_on_disk["gaussian_count"] == m["gaussian_count"]

    def test_deterministic_across_runs(self, photos_dir, tmp_path):
        m1 = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r1",
                         web_dir=tmp_path / "w1", engine="mock",
                         reg_engine="mock")
        m2 = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r2",
                         web_dir=tmp_path / "w2", engine="mock",
                         reg_engine="mock")
        assert m1["gaussian_count"] == m2["gaussian_count"]
        s1 = GaussianScene.load_ply(tmp_path / "r1" / "scene_full.ply")
        s2 = GaussianScene.load_ply(tmp_path / "r2" / "scene_full.ply")
        assert np.allclose(s1.xyz, s2.xyz, atol=1e-4)

    def test_sessions_stitched_in_one_frame(self, photos_dir, tmp_path):
        """视频与照片两个会话拼进同一场景: 总范围应覆盖两个锚点区域"""
        m = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                        web_dir=tmp_path / "web", engine="mock",
                        reg_engine="mock")
        extent_x = m["bounds"]["max"][0] - m["bounds"]["min"][0]
        assert extent_x > 80  # 两会话网格间距 80m, 拼接后范围必然更大

    def test_lod_counts_decrease(self, photos_dir, tmp_path):
        reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                    web_dir=tmp_path / "web", engine="mock", reg_engine="mock")
        counts = [len(GaussianScene.load_ply(tmp_path / "web" / f"recon_lod{i}.ply"))
                  for i in range(3)]
        assert counts[0] < counts[1] < counts[2]


class TestImportEngine:
    def test_import_aligns_by_session(self, photos_dir, tmp_path):
        rng = np.random.default_rng(2)
        ext = GaussianScene(rng.uniform(0, 10, (800, 3)),
                            rng.uniform(0, 1, (800, 3)))
        ext_ply = tmp_path / "ext.ply"
        ext.save_ply(ext_ply, flavor="3dgs")

        m = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                        web_dir=tmp_path / "web", engine="import",
                        reg_engine="mock",
                        splat_map={"video_vid_A": str(ext_ply)},
                        dedup_voxel=0.0)
        assert m["gaussian_count"] == 800

    def test_import_requires_splat_map(self, photos_dir, tmp_path):
        with pytest.raises(ValueError, match="splat"):
            reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r",
                        web_dir=tmp_path / "w", engine="import",
                        reg_engine="mock")


class TestProgressiveSharpen:
    def test_base_scene_region_replaced(self, photos_dir, tmp_path):
        """可变清晰: 二次重建应替换基底场景对应区域而非简单叠加"""
        m1 = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r1",
                         web_dir=tmp_path / "w1", engine="mock",
                         reg_engine="mock")
        base_ply = tmp_path / "r1" / "scene_full.ply"
        m2 = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r2",
                         web_dir=tmp_path / "w2", engine="mock",
                         reg_engine="mock", base_scene=base_ply)
        # 同一输入的新重建覆盖同一区域 → 总量不应翻倍
        assert m2["gaussian_count"] < m1["gaussian_count"] * 1.5


class TestVideoIngest:
    def test_video_frames_extracted(self, tmp_path):
        """真实视频文件 → 抽帧 (cv2), 照片 → 复制, 混合输入"""
        cv2 = pytest.importorskip("cv2")
        from pipeline.ingest import ingest_all

        src = tmp_path / "input"
        src.mkdir()
        # 合成 2 秒 12fps 视频
        vw = cv2.VideoWriter(str(src / "clip.mp4"),
                             cv2.VideoWriter_fourcc(*"mp4v"), 12, (128, 96))
        rng = np.random.default_rng(9)
        for _ in range(24):
            vw.write(rng.integers(0, 255, (96, 128, 3), dtype=np.uint8))
        vw.release()
        # 一张照片
        from PIL import Image
        Image.fromarray(rng.integers(0, 255, (96, 128, 3), dtype=np.uint8)
                        ).save(src / "photo.jpg")

        out = tmp_path / "photos"
        result = ingest_all(src, out, fps=4, blur_threshold=0)
        assert result["total_output"] >= 5
        assert (out / "photo.jpg").exists()
        frames = list((out / "clip").glob("*.jpg"))
        assert len(frames) >= 4  # 2s * 4fps ≈ 8 帧
