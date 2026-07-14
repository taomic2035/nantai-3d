"""端到端重建: mock 全链路 / 导入引擎 / 变清晰 (区域替换) / 视频抽帧"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import (
    AxisConvention,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    FrameTransform,
    GeoAlignment,
    Handedness,
    MetricStatus,
    Sim3,
    SplatInput,
)
from pipeline.reconstruct import reconstruct


def _mock_source_frame() -> CoordinateFrame:
    return CoordinateFrame(
        frame_id="mock-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.LOCAL_Z_UP,
        units=CoordinateUnits.METERS,
        metric_status=MetricStatus.METRIC,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SYNTHETIC,
        evidence=["mock-layout:v1"],
    )


def _arbitrary_source_frame() -> CoordinateFrame:
    return CoordinateFrame(
        frame_id="sfm-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SFM,
        evidence=["colmap-model:v1"],
    )


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

    def test_manifest_artifacts_are_web_relative_and_integrity_described(
        self, photos_dir, tmp_path
    ):
        web_dir = tmp_path / "web"
        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "recon",
            web_dir=web_dir,
            engine="mock",
            reg_engine="mock",
        )

        assert manifest["full_3dgs"] == "recon_full.ply"
        assert (tmp_path / "recon" / "scene_full.ply").is_file()
        assert (web_dir / manifest["full_3dgs"]).is_file()

        artifacts = manifest["artifacts"]
        all_descriptors = [artifacts["full_3dgs"], *artifacts["lod"].values()]
        for descriptor in all_descriptors:
            relative = Path(descriptor["path"])
            assert not relative.is_absolute()
            assert ".." not in relative.parts
            payload = web_dir / relative
            assert payload.is_file()
            assert descriptor["bytes"] == payload.stat().st_size
            assert descriptor["sha256"] == hashlib.sha256(payload.read_bytes()).hexdigest()
            assert isinstance(descriptor["attributes"], list)
            assert "x" in descriptor["attributes"]
            assert "sh_degree" in descriptor

        full = artifacts["full_3dgs"]
        assert full["path"] == manifest["full_3dgs"]
        assert full["fidelity"] == "full-3dgs"
        assert full["sh_degree"] == 0
        assert {
            "f_dc_0", "opacity", "scale_0", "rot_0",
        }.issubset(full["attributes"])
        for level, path in manifest["lod"].items():
            preview = artifacts["lod"][level]
            assert preview["path"] == path
            assert preview["fidelity"] == "dc-point-preview"
            assert preview["sh_degree"] is None
            assert preview["attributes"] == ["x", "y", "z", "r", "g", "b", "scale"]

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
    def test_reconstruct_rejects_bare_splat_dict_at_api_boundary(
        self, photos_dir, tmp_path
    ):
        with pytest.raises(TypeError, match="SplatInput"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "r",
                web_dir=tmp_path / "w",
                engine="import",
                reg_engine="mock",
                splat_map={"video_vid_A": "scene.ply"},  # type: ignore[arg-type]
            )

    def test_import_aligns_by_session(self, photos_dir, tmp_path):
        rng = np.random.default_rng(2)
        ext = GaussianScene(rng.uniform(0, 10, (800, 3)),
                            rng.uniform(0, 1, (800, 3)))
        ext_ply = tmp_path / "ext.ply"
        ext.save_ply(ext_ply, flavor="3dgs")

        m = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                        web_dir=tmp_path / "web", engine="import",
                        reg_engine="mock",
                        splat_map=[SplatInput(
                            session_id="video_vid_A",
                            path=str(ext_ply),
                            source_frame=_mock_source_frame(),
                        )],
                        dedup_voxel=0.0)
        assert m["gaussian_count"] == 800

    def test_full_artifact_reports_high_order_sh_and_extra_attributes(
        self, photos_dir, tmp_path
    ):
        scene = GaussianScene(
            [[0, 0, 0], [1, 2, 3]],
            [[0.2, 0.4, 0.6], [0.3, 0.5, 0.7]],
            sh_rest=np.arange(90, dtype=np.float64).reshape(2, 45) / 100,
            extra_properties={"confidence": np.array([0.8, 0.9], dtype=np.float32)},
            frame_id="mock-local",
            units="meters",
        )
        source = tmp_path / "degree3.ply"
        scene.save_ply(source, flavor="3dgs")

        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web",
            engine="import",
            reg_engine="mock",
            splat_map=[SplatInput(
                session_id="video_vid_A",
                path=str(source),
                source_frame=_mock_source_frame(),
            )],
            dedup_voxel=0,
        )

        full = manifest["artifacts"]["full_3dgs"]
        assert full["sh_degree"] == 3
        assert "f_rest_44" in full["attributes"]
        assert "confidence" in full["attributes"]

    def test_import_requires_splat_map(self, photos_dir, tmp_path):
        with pytest.raises(ValueError, match="splat"):
            reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r",
                        web_dir=tmp_path / "w", engine="import",
                        reg_engine="mock")

    def test_mock_rejects_splat_map_instead_of_recording_unapplied_transform(
        self, photos_dir, tmp_path
    ):
        with pytest.raises(ValueError, match="mock.*splat|splat.*import"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "r",
                web_dir=tmp_path / "w",
                engine="mock",
                reg_engine="mock",
                splat_map=[SplatInput(
                    session_id="video_vid_A",
                    path="never-read.ply",
                    source_frame=_mock_source_frame(),
                )],
            )

    def test_non_metric_target_requires_zero_dedup_voxel(
        self, photos_dir, tmp_path, monkeypatch
    ):
        from pipeline.recon_schema import (
            AlignmentStatus,
            CameraIntrinsics,
            CameraPose,
            CaptureSession,
            RegistrationResult,
        )

        source = _arbitrary_source_frame()
        session = CaptureSession(
            session_id="s0", kind="photo_batch", source="photos", images=["one.jpg"]
        )
        reg = RegistrationResult(
            engine="colmap",
            pose_frame=source,
            alignment_status=AlignmentStatus.UNALIGNED,
            sessions=[session],
            poses=[CameraPose(
                image="one.jpg",
                session_id="s0",
                quat_wxyz=[1, 0, 0, 0],
                t_xyz=[0, 0, 0],
                intrinsics=CameraIntrinsics.from_fov(32, 32),
            )],
        )
        monkeypatch.setattr("pipeline.reconstruct.register", lambda *args, **kwargs: reg)
        ply = tmp_path / "arbitrary.ply"
        GaussianScene(
            [[0, 0, 0]], [[1, 0, 0]], frame_id="sfm-local", units="arbitrary"
        ).save_ply(ply, flavor="3dgs")

        with pytest.raises(ValueError, match="dedup_voxel.*meters|non-metric"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "r",
                web_dir=tmp_path / "w",
                engine="import",
                reg_engine="colmap",
                splat_map=[SplatInput(
                    session_id="s0", path=str(ply), source_frame=source
                )],
            )

    def test_spatial_parameters_name_target_frame_units(self, photos_dir, tmp_path):
        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "r",
            web_dir=tmp_path / "w",
            engine="mock",
            reg_engine="mock",
            dedup_voxel=0.25,
        )

        assert manifest["spatial_parameters"] == {
            "frame_id": "mock-local",
            "units": "meters",
            "dedup_voxel": 0.25,
            "replace_margin": None,
        }

    def test_import_manifest_audits_only_the_applied_transform(
        self, photos_dir, tmp_path
    ):
        source_frame = _arbitrary_source_frame()
        source = tmp_path / "source.ply"
        GaussianScene(
            [[1, 2, 3]],
            [[1, 0, 0]],
            frame_id=source_frame.frame_id,
            units=source_frame.units.value,
        ).save_ply(source, flavor="3dgs")
        transform = FrameTransform(
            source_frame=source_frame.frame_id,
            target_frame="mock-local",
            sim3=Sim3(scale=2, t_xyz=[10, 0, 0]),
            method="external-sim3",
            evidence=["scale-bar:v1"],
        )

        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "r",
            web_dir=tmp_path / "w",
            engine="import",
            reg_engine="mock",
            splat_map=[SplatInput(
                session_id="video_vid_A",
                path=str(source),
                source_frame=source_frame,
                transform=transform,
            )],
            dedup_voxel=0,
        )
        contract = manifest["coordinate_contract"]

        assert [step["transform_id"] for step in contract["transform_chain"]] == [
            transform.transform_id
        ]
        assert contract["applied_transform_ids"] == [transform.transform_id]
        assert contract["ancestry"][0]["applied_transform_ids"] == [
            transform.transform_id
        ]


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

    def test_base_scene_unknown_transform_history_fails_closed(
        self, photos_dir, tmp_path
    ):
        base = GaussianScene(
            [[0, 0, 0]],
            [[1, 0, 0]],
            frame_id="mock-local",
            units="meters",
            applied_transform_ids=["xf-history-without-definition"],
        )
        base_path = tmp_path / "base.ply"
        base.save_ply(base_path, flavor="3dgs")

        with pytest.raises(ValueError, match="base.*history|transform.*definition"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "r",
                web_dir=tmp_path / "w",
                engine="mock",
                reg_engine="mock",
                base_scene=base_path,
            )

    def test_manifest_transform_chain_ids_match_applied_ids_and_ancestry(
        self, photos_dir, tmp_path
    ):
        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "r",
            web_dir=tmp_path / "w",
            engine="mock",
            reg_engine="mock",
        )
        contract = manifest["coordinate_contract"]
        chain_ids = [step["transform_id"] for step in contract["transform_chain"]]
        ancestry_ids = list(dict.fromkeys(
            transform_id
            for ancestor in contract["ancestry"]
            for transform_id in ancestor["applied_transform_ids"]
        ))

        assert chain_ids == contract["applied_transform_ids"] == ancestry_ids


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
