"""对抗审查后补充的行为测试: GPS 锚定 / 非恒等 Sim3 对齐 / chunk LOD 子集 / 防御性检查"""
import json

import numpy as np
import pytest

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
    FrameTransform,
    GeoAlignment,
    GeoAnchor,
    Handedness,
    MetricStatus,
    RegistrationResult,
    Sim3,
    SplatInput,
)
from pipeline.registration import _session_anchor_xy, group_sessions, mock_register


class TestGpsAnchoring:
    def _sessions(self):
        origin = GeoAnchor(lat=26.0, lon=119.0, alt=50)
        north_100m = GeoAnchor(lat=26.0 + 100 / 111319.49, lon=119.0, alt=50)
        return [
            CaptureSession(session_id="a", kind="photo_batch", source="a",
                           images=["a1.jpg"], geo_anchor=origin),
            CaptureSession(session_id="b", kind="photo_batch", source="b",
                           images=["b1.jpg"], geo_anchor=north_100m),
            CaptureSession(session_id="c", kind="video", source="c",
                           images=["c/1.jpg"]),  # 无 GPS
        ]

    def test_gps_sessions_anchored_by_enu(self):
        anchors = _session_anchor_xy(self._sessions())
        # 首个 GPS 会话为原点, 第二个在正北 100m
        assert np.allclose(anchors["a"], [0, 0, 0], atol=0.1)
        assert abs(anchors["b"][1] - 100) < 0.5 and abs(anchors["b"][0]) < 0.5

    def test_mixed_gps_and_grid(self):
        anchors = _session_anchor_xy(self._sessions())
        # 无 GPS 会话仍有确定性网格锚点, 与 GPS 会话共存于同一世界系
        assert "c" in anchors and anchors["c"].shape == (3,)

    def test_mock_register_uses_gps_origin(self, photos_dir):
        sessions = group_sessions(photos_dir)
        sessions[0] = sessions[0].model_copy(
            update={"geo_anchor": GeoAnchor(lat=26.0, lon=119.0, alt=50)})
        reg = mock_register(photos_dir, sessions=sessions)
        assert reg.geo_origin is not None and reg.geo_origin.lat == 26.0


class TestNonIdentitySim3Import:
    def test_import_applies_explicit_frame_transform(self, tmp_path):
        from pipeline.reconstruct import import_session_splats

        rng = np.random.default_rng(4)
        local = GaussianScene(rng.uniform(0, 10, (300, 3)),
                              rng.uniform(0, 1, (300, 3)))
        local_xyz = local.xyz.copy()
        ply = tmp_path / "local.ply"
        local.save_ply(ply, flavor="3dgs")

        sess = CaptureSession(session_id="s0", kind="video", source="v",
                              images=["v/1.jpg"])
        pose = CameraPose(image="v/1.jpg", session_id="s0",
                          quat_wxyz=[1, 0, 0, 0], t_xyz=[0, 0, 10],
                          intrinsics=CameraIntrinsics.from_fov(640, 480))
        sim3 = Sim3(scale=2.0, t_xyz=[100, -50, 0])
        target = CoordinateFrame(
            frame_id="mock-local",
            handedness=Handedness.RIGHT,
            axes=AxisConvention.LOCAL_Z_UP,
            units=CoordinateUnits.METERS,
            metric_status=MetricStatus.METRIC,
            geo_aligned=GeoAlignment.UNALIGNED,
            provenance=FrameProvenance.SYNTHETIC,
            evidence=["test synthetic metric frame"],
        )
        reg = RegistrationResult(
            schema_version=2,
            engine="mock",
            pose_frame=target,
            alignment_status=AlignmentStatus.SYNTHETIC,
            sessions=[sess],
            poses=[pose],
        )
        transform = FrameTransform(
            source_frame="session-s0-local",
            target_frame=target.frame_id,
            sim3=sim3,
            method="external-sim3",
            evidence=["test control points"],
        )
        splat = SplatInput(
            session_id="s0",
            path=str(ply),
            frame_id="session-s0-local",
            transform=transform,
        )

        scenes = import_session_splats([splat], reg)
        assert len(scenes) == 1
        assert np.allclose(scenes[0].xyz, local_xyz * 2 + [100, -50, 0], atol=1e-3)


class TestChunksetLod:
    def test_lod_files_are_topk_by_scale(self, tmp_path):
        from pipeline.mock_layout import MockLayoutGenerator
        from pipeline.render_chunk_to_ply import render_chunkset

        layouts = tmp_path / "layouts"
        layouts.mkdir()
        gen = MockLayoutGenerator(world_seed=1)
        layout = gen.generate_chunk(0, 0)
        (layouts / "chunk_0_0.json").write_text(layout.model_dump_json())

        out = tmp_path / "web"
        manifest = render_chunkset(layouts_dir=layouts, output_dir=out,
                                   chunk_range=(0, 1, 0, 1), assets_dir=None)
        entry = manifest["chunks"][0]
        full = GaussianScene.load_ply(out / entry["ply_file"])
        lod0 = GaussianScene.load_ply(out / entry["lod"]["0"])
        n = len(full)
        assert len(lod0) == max(1, int(n * 0.08))
        # lod0 的 scale 应为全量前 k 大 (simple 格式重要性代理)
        thresh = np.sort(full.scale.mean(axis=1))[-len(lod0)]
        assert np.all(lod0.scale.mean(axis=1) >= thresh - 1e-5)
        assert entry["lod"]["2"] == entry["ply_file"]


class TestDefensiveChecks:
    def test_empty_photos_dir_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ValueError, match="没有任何可用图像"):
            group_sessions(empty)

    def test_path_traversal_rejected(self, tmp_path):
        from pipeline.validate_handoff import validate

        rng = np.random.default_rng(1)
        outside = tmp_path / "outside.ply"
        GaussianScene(rng.uniform(0, 5, (500, 3)),
                      rng.uniform(0, 1, (500, 3))).save_ply(outside, flavor="3dgs")
        d = tmp_path / "deliv"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "handoff_id": "HANDOFF-EVIL",
            "items": [{"asset_id": "x", "kind": "prop",
                       "ply": "../outside.ply"}],
        }))
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"]
        assert any("越出交付目录" in p for p in r["results"]["x"])

    def test_invalid_kind_fails_at_manifest(self, tmp_path):
        from pipeline.validate_handoff import validate

        d = tmp_path / "deliv"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "handoff_id": "HANDOFF-K",
            "items": [{"asset_id": "x", "kind": "spaceship", "ply": "x.ply"}],
        }))
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"] and r["fatal"]  # schema 阶段即拒绝

    def test_empty_road_segments_no_crash(self):
        from pipeline.render_chunk_to_ply import _emit_road
        from pipeline.schema import Road

        # 全部线段 < 0.1m → 空结果而非 IndexError
        road = Road(id="r0", type="main", width=4.0,
                    points=[[0, 0], [0.01, 0.01]])
        arr = _emit_road(road, 0, 0, world_seed=42)
        assert len(arr) == 0
