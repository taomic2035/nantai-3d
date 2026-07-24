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
from pipeline.spatial_chunk import partition_scene_to_chunks, verify_chunks_integrity


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

    def test_manifest_declares_core_bounds_so_bounds_cannot_mislead_framing(self, tmp_path):
        """真实 3DGS 必带漂浮物: 少数高斯被优化到场景外几百米。实测一个 Brush 实训重建
        (67878 高斯): Z 向 90% 分位只有 52.6m, 而真实 bounds 是 720m —— 被噪声撑大 13 倍。
        viewer 若按 bounds 取景, 相机会停在几百米外对着空气。

        故 manifest 须【另报】一个 core_bounds 供取景。铁律: bounds 仍是全量真相 (绝不
        缩水), core_bounds 是【附加】信息, 不是替代品。
        """
        rng = np.random.default_rng(11)
        core = np.column_stack([rng.uniform(-30, 30, 2000), rng.uniform(-30, 30, 2000),
                                rng.uniform(0, 8, 2000)])
        floaters = np.array([[400.0, 300.0, 250.0], [-380.0, -290.0, -240.0]])
        xyz = np.vstack([core, floaters])
        scene = GaussianScene(xyz, np.full((len(xyz), 3), 0.5),
                              np.full(len(xyz), 0.8), np.full((len(xyz), 3), 0.1))
        manifest = partition_scene_to_chunks(scene, tmp_path, chunk_size_m=50.0)

        # bounds 仍是【全量真相】: 漂浮物也在内, 绝不因为难看就砍掉
        assert np.isclose(manifest["bounds"]["max"][2], 250.0, atol=1e-3)
        assert np.isclose(manifest["bounds"]["min"][2], -240.0, atol=1e-3)

        cb = manifest["core_bounds"]
        core_size = np.array(cb["max"]) - np.array(cb["min"])
        full_size = (np.array(manifest["bounds"]["max"])
                     - np.array(manifest["bounds"]["min"]))
        assert np.all(core_size < full_size / 5), \
            "core_bounds 须真的收紧到主体几何, 否则对取景毫无用处"
        assert core_size[2] < 20.0, "Z 向漂浮物须被排除在 core_bounds 外"

    def test_core_bounds_coverage_is_measured_not_assumed(self, tmp_path):
        """core_bounds 报的覆盖率必须是【实测数】(真去数盒内有几个点), 不是从分位数
        推算的假设。逐轴取分位盒后, 三轴联合覆盖【严格小于】单轴分位 —— 拿 0.995 当
        联合覆盖率报出去就是过度声称。本项目宁可报实测事实, 不报"应该是多少"。
        """
        scene = _scene(n=1000, span=200.0, seed=17)
        manifest = partition_scene_to_chunks(scene, tmp_path, chunk_size_m=50.0)
        cb = manifest["core_bounds"]

        lo, hi = np.array(cb["min"]), np.array(cb["max"])
        actual = int(np.sum(np.all((scene.xyz >= lo) & (scene.xyz <= hi), axis=1)))
        assert cb["contains_points"] == actual, "报的是实测点数, 不是推算"
        assert np.isclose(cb["contains_fraction"], actual / len(scene))
        assert "axis_percentile" in cb, "造盒判据须自述, 消费者不用猜 core 是怎么来的"
        json.dumps(manifest)

    def test_degenerate_scene_core_bounds_does_not_crash_or_lie(self, tmp_path):
        """所有点重合 / 点数极少时, 分位数退化 —— core_bounds 须仍是有效盒且不谎报覆盖。"""
        xyz = np.tile([5.0, 5.0, 1.0], (4, 1))
        scene = GaussianScene(xyz, np.full((4, 3), 0.5), np.full(4, 0.8),
                              np.full((4, 3), 0.1))
        manifest = partition_scene_to_chunks(scene, tmp_path, chunk_size_m=50.0)
        cb = manifest["core_bounds"]
        assert cb["contains_points"] == 4 and cb["contains_fraction"] == 1.0
        assert np.all(np.array(cb["max"]) >= np.array(cb["min"]))

    def test_manifest_declares_lod_fractions_not_just_filenames(self, tmp_path):
        """manifest 须声明各 LOD 的【比例】而非只给文件名 —— 否则消费者不知道 lod0 是
        8% 还是别的密度, 无法据此按距离正确选级 (早先审计标记的同类潜在缺口)。"""
        manifest = partition_scene_to_chunks(
            _scene(n=500), tmp_path, chunk_size_m=100.0,
            lod_fractions={0: 0.1, 1: 0.4})
        assert manifest["lod_fractions"] == {"0": 0.1, "1": 0.4, "2": 1.0}, \
            "含 lod2=1.0 全量, 让消费者无需猜测任何一级的密度语义"
        # 声明的比例须与实际产出一致
        chunk = max(manifest["chunks"], key=lambda c: c["point_count"])
        full = len(GaussianScene.load_ply(tmp_path / chunk["lod"]["2"]))
        lod0 = len(GaussianScene.load_ply(tmp_path / chunk["lod"]["0"]))
        assert abs(lod0 / full - 0.1) < 0.05, "声明的 lod0 比例须反映实际密度"

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

    def test_source_provenance_is_carried_with_content_addressed_link(self, tmp_path):
        """分块产物须带源的信任判定 + 内容寻址链接, 否则消费者无法诚实标注
        (preview-only vs metric-aligned) —— 重打包不该丢信任等级。"""
        scene = _scene(n=200, frame_id="world-enu", units="meters")
        manifest = partition_scene_to_chunks(
            scene, tmp_path, chunk_size_m=60.0,
            source_provenance={
                "geometry_usability": "metric-aligned",
                "recon_manifest_sha256": "a" * 64,
            })
        src = manifest["source"]
        assert src["geometry_usability"] == "metric-aligned"
        assert src["recon_manifest_sha256"] == "a" * 64
        assert src["frame_id"] == "world-enu"      # 原有字段不被覆盖
        assert json.loads(
            (tmp_path / "chunks.json").read_text(encoding="utf-8")
        )["source"]["geometry_usability"] == "metric-aligned"

    def test_source_provenance_absent_stays_absent_not_guessed(self, tmp_path):
        """未提供源判定时, 绝不猜测/编造信任等级 (缺席即未知)。"""
        manifest = partition_scene_to_chunks(
            _scene(n=100), tmp_path, chunk_size_m=60.0)
        assert "geometry_usability" not in manifest["source"]
        assert "recon_manifest_sha256" not in manifest["source"]

    def test_cli_embeds_recon_manifest_provenance(self, tmp_path):
        """CLI --recon-manifest: 把源 manifest 的判定 + 其内容 sha 记入 chunks.json。"""
        import hashlib

        import scripts.chunk_reconstruction as cr
        scene = _scene(n=150, frame_id="world-enu", units="meters")
        ply = tmp_path / "recon.ply"
        scene.save_ply(ply, flavor="3dgs")
        recon_manifest = tmp_path / "recon_manifest.json"
        recon_manifest.write_text(json.dumps(
            {"provenance": {"geometry_usability": "preview-only", "synthetic": False}}),
            encoding="utf-8")

        rc = cr.main([str(ply), "--out-dir", str(tmp_path / "out"),
                      "--chunk-size-m", "80", "--recon-manifest", str(recon_manifest)])
        assert rc == 0
        out = json.loads(
            (tmp_path / "out" / "chunks.json").read_text(encoding="utf-8"))
        # 源是 preview-only → 分块产物照样 preview-only (绝不因分块升级)
        assert out["source"]["geometry_usability"] == "preview-only"
        assert out["source"]["recon_manifest_sha256"] == hashlib.sha256(
            recon_manifest.read_bytes()).hexdigest()

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


class TestChunkPayloadSHA:
    """P3: 每个 streamed chunk 和 LOD payload 须绑定 SHA-256 + size_bytes。

    消费者 (viewer / 跨 worker 缓存 / 下游工具) 拿到 chunk_*.ply 后无法验证字节完整性
    —— 只能验证源 recon_manifest.json 的 sha。绑定逐 chunk SHA 让字节级完整性校验成为可能。
    """

    def test_each_chunk_has_sha256_and_size_bytes(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0)
        for chunk in manifest["chunks"]:
            assert "sha256" in chunk, "每个 chunk 须绑定 ply_file 的 sha256"
            assert "size_bytes" in chunk, "每个 chunk 须绑定 ply_file 的 size_bytes"
            assert isinstance(chunk["sha256"], str)
            assert len(chunk["sha256"]) == 64, "sha256 须是 64 字符 hex"
            assert isinstance(chunk["size_bytes"], int)
            assert chunk["size_bytes"] > 0

    def test_sha256_matches_actual_file_bytes(self, tmp_path):
        import hashlib
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0)
        for chunk in manifest["chunks"]:
            ply_path = tmp_path / chunk["ply_file"]
            actual_sha = hashlib.sha256(ply_path.read_bytes()).hexdigest()
            actual_size = ply_path.stat().st_size
            assert chunk["sha256"] == actual_sha, "声明 sha 须匹配实际 ply 字节"
            assert chunk["size_bytes"] == actual_size, "声明 size 须匹配实际文件大小"

    def test_payloads_covers_all_lod_levels(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=500), tmp_path, chunk_size_m=100.0,
            lod_fractions={0: 0.1, 1: 0.4})
        for chunk in manifest["chunks"]:
            payloads = chunk["payloads"]
            # 须含 lod0, lod1, lod2 (full) 三级
            assert set(payloads.keys()) == {"0", "1", "2"}
            for _level, payload in payloads.items():
                assert "file" in payload
                assert "sha256" in payload
                assert "size_bytes" in payload
                assert len(payload["sha256"]) == 64
                assert payload["size_bytes"] > 0

    def test_payload_sha_matches_lod_files(self, tmp_path):
        import hashlib
        manifest = partition_scene_to_chunks(
            _scene(n=500), tmp_path, chunk_size_m=100.0,
            lod_fractions={0: 0.1, 1: 0.4})
        for chunk in manifest["chunks"]:
            for _level, payload in chunk["payloads"].items():
                ply_path = tmp_path / payload["file"]
                actual_sha = hashlib.sha256(ply_path.read_bytes()).hexdigest()
                assert payload["sha256"] == actual_sha
                assert payload["size_bytes"] == ply_path.stat().st_size

    def test_payload_full_matches_chunk_sha256(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0)
        for chunk in manifest["chunks"]:
            assert chunk["payloads"]["2"]["sha256"] == chunk["sha256"]
            assert chunk["payloads"]["2"]["size_bytes"] == chunk["size_bytes"]
            assert chunk["payloads"]["2"]["file"] == chunk["ply_file"]

    def test_sha256_is_deterministic_for_same_scene(self, tmp_path):
        scene = _scene(n=400, seed=7)
        first = partition_scene_to_chunks(scene, tmp_path / "a", chunk_size_m=50.0)
        second = partition_scene_to_chunks(scene, tmp_path / "b", chunk_size_m=50.0)
        assert len(first["chunks"]) == len(second["chunks"])
        for c1, c2 in zip(first["chunks"], second["chunks"], strict=False):
            assert c1["sha256"] == c2["sha256"], "同一场景的 chunk sha 须确定"
            assert c1["size_bytes"] == c2["size_bytes"]

    def test_sha_does_not_promote_trust(self, tmp_path):
        """绑定 SHA 是完整性校验, 不提升几何信任等级。"""
        manifest = partition_scene_to_chunks(
            _scene(n=200), tmp_path, chunk_size_m=60.0,
            source_provenance={"geometry_usability": "preview-only"})
        assert manifest["source"]["geometry_usability"] == "preview-only"
        for chunk in manifest["chunks"]:
            assert "sha256" in chunk  # SHA 存在
        # 但 SHA 不改变信任等级: 仍是 preview-only

    def test_verify_chunks_integrity_passes_for_valid_manifest(self, tmp_path):
        partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0)
        report = verify_chunks_integrity(tmp_path)
        assert report["valid"] is True
        assert report["per_chunk_sha_verified"] is True
        assert report["verified_payloads"] > 0
        assert report["mismatches"] == []

    def test_verify_chunks_integrity_detects_tampered_file(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0)
        # 篡改一个 PLY 文件
        chunk = manifest["chunks"][0]
        ply_path = tmp_path / chunk["ply_file"]
        original = ply_path.read_bytes()
        tampered = original + b"\x00" * 100
        ply_path.write_bytes(tampered)
        report = verify_chunks_integrity(tmp_path)
        assert report["valid"] is False
        assert len(report["mismatches"]) > 0
        mismatch = report["mismatches"][0]
        assert "chunk_id" in mismatch
        assert mismatch["declared_sha256"] != mismatch["actual_sha256"]

    def test_verify_chunks_integrity_detects_missing_file(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0)
        # 删除一个 PLY 文件
        chunk = manifest["chunks"][0]
        (tmp_path / chunk["ply_file"]).unlink()
        report = verify_chunks_integrity(tmp_path)
        assert report["valid"] is False
        assert any("missing" in str(m).lower() or "not found" in str(m).lower()
                      for m in report["mismatches"])

    def test_verify_chunks_integrity_detects_size_mismatch(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0)
        # 篡改文件大小但不改 SHA (理论上很难, 但测试 size_bytes 检查路径)
        chunk = manifest["chunks"][0]
        ply_path = tmp_path / chunk["ply_file"]
        original = ply_path.read_bytes()
        # 截断文件: sha 和 size 都会变
        ply_path.write_bytes(original[:len(original) // 2])
        report = verify_chunks_integrity(tmp_path)
        assert report["valid"] is False

    def test_verify_returns_dict_with_human_readable_summary(self, tmp_path):
        partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0)
        report = verify_chunks_integrity(tmp_path)
        assert "total_chunks" in report
        assert "verified_payloads" in report
        assert "mismatches" in report
        assert isinstance(report["total_chunks"], int)
        assert isinstance(report["verified_payloads"], int)

    def test_legacy_manifest_is_readable_but_integrity_is_unknown(
        self,
        tmp_path,
    ):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0
        )
        manifest.pop("integrity")
        for chunk in manifest["chunks"]:
            chunk.pop("sha256")
            chunk.pop("size_bytes")
            chunk.pop("payloads")
        (tmp_path / "chunks.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        report = verify_chunks_integrity(tmp_path)

        assert report["valid"] is True
        assert report["per_chunk_sha_verified"] is None
        assert report["verified_payloads"] == 0

    def test_new_manifest_missing_payload_row_fails_closed(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0
        )
        manifest["chunks"][0]["payloads"].pop("1")
        (tmp_path / "chunks.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        report = verify_chunks_integrity(tmp_path)

        assert report["valid"] is False
        assert report["per_chunk_sha_verified"] is False
        assert any(
            "integrity" in row["reason"] or "payload" in row["reason"]
            for row in report["mismatches"]
        )

    def test_payload_path_must_match_lod_and_stay_inside_root(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0
        )
        manifest["chunks"][0]["payloads"]["0"]["file"] = "../outside.ply"
        (tmp_path / "chunks.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        report = verify_chunks_integrity(tmp_path)

        assert report["valid"] is False
        assert any(
            "path" in row["reason"] or "lod" in row["reason"]
            for row in report["mismatches"]
        )

    def test_duplicate_lod_payload_paths_fail_closed(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0
        )
        chunk = manifest["chunks"][0]
        chunk["lod"]["1"] = chunk["lod"]["0"]
        chunk["payloads"]["1"] = dict(chunk["payloads"]["0"])
        (tmp_path / "chunks.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        report = verify_chunks_integrity(tmp_path)

        assert report["valid"] is False
        assert any(
            "duplicate payload path" in row["reason"]
            for row in report["mismatches"]
        )

    def test_redirected_payload_path_fails_closed(self, tmp_path):
        manifest = partition_scene_to_chunks(
            _scene(n=300), tmp_path, chunk_size_m=80.0
        )
        chunk = manifest["chunks"][0]
        original = tmp_path / chunk["payloads"]["0"]["file"]
        alias = tmp_path / "redirected-lod0.ply"
        try:
            alias.symlink_to(original)
        except OSError:
            pytest.skip("symlink creation is unavailable")
        chunk["lod"]["0"] = alias.name
        chunk["payloads"]["0"]["file"] = alias.name
        (tmp_path / "chunks.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        report = verify_chunks_integrity(tmp_path)

        assert report["valid"] is False
        assert any(
            "redirected" in row["reason"]
            for row in report["mismatches"]
        )

    def test_manifest_bytes_are_canonical_across_output_roots(self, tmp_path):
        scene = _scene(n=300, seed=19)
        partition_scene_to_chunks(scene, tmp_path / "a", chunk_size_m=80.0)
        partition_scene_to_chunks(scene, tmp_path / "b", chunk_size_m=80.0)

        first = (tmp_path / "a/chunks.json").read_bytes()
        second = (tmp_path / "b/chunks.json").read_bytes()

        assert first == second
        assert first.endswith(b"\n")
        assert b"\r\n" not in first
        assert first == (
            json.dumps(
                json.loads(first),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    def test_source_provenance_cannot_override_coordinate_contract(
        self,
        tmp_path,
    ):
        with pytest.raises(ValueError, match="source provenance"):
            partition_scene_to_chunks(
                _scene(n=300),
                tmp_path,
                chunk_size_m=80.0,
                source_provenance={"frame_id": "forged-frame"},
            )
