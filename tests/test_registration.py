"""统一坐标系配准: 会话划分 / mock 确定性 / 坐标一致性 / COLMAP 解析"""
import json
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

import pipeline.registration as registration_module
from pipeline.recon_schema import GeoAnchor, RegistrationResult, gps_to_enu
from pipeline.registration import (
    group_sessions,
    mock_register,
    parse_colmap_images_txt,
    register,
)


class TestGroupSessions:
    def test_video_and_photo_sessions(self, photos_dir):
        sessions = group_sessions(photos_dir)
        kinds = {s.session_id: s.kind for s in sessions}
        assert kinds == {"video_vid_A": "video", "photos_batch_0": "photo_batch"}
        by_id = {s.session_id: s for s in sessions}
        assert len(by_id["video_vid_A"].images) == 8
        assert len(by_id["photos_batch_0"].images) == 4
        # 视频帧路径带子目录前缀
        assert all(i.startswith("vid_A/") for i in by_id["video_vid_A"].images)

    def test_frames_ordered(self, photos_dir):
        sessions = group_sessions(photos_dir)
        vid = next(s for s in sessions if s.kind == "video")
        assert vid.images == sorted(vid.images)


class TestMockRegistration:
    def test_all_images_get_poses(self, photos_dir):
        reg = mock_register(photos_dir)
        assert len(reg.poses) == 12
        assert {p.session_id for p in reg.poses} == \
               {s.session_id for s in reg.sessions}

    def test_deterministic(self, photos_dir):
        r1 = mock_register(photos_dir)
        r2 = mock_register(photos_dir)
        for p1, p2 in zip(r1.poses, r2.poses, strict=True):
            assert p1.image == p2.image
            assert np.allclose(p1.t_xyz, p2.t_xyz)
            assert np.allclose(p1.quat_wxyz, p2.quat_wxyz)

    def test_sessions_share_world_frame(self, photos_dir):
        """不同会话 (照片批次 vs 视频) 的位姿处于同一坐标系:
        锚点按网格分离, 但都在世界系中 (间距 = SESSION_GRID_SPACING)"""
        reg = mock_register(photos_dir)
        video_pos = np.array([p.t_xyz for p in reg.poses
                              if p.session_id == "video_vid_A"])
        photo_pos = np.array([p.t_xyz for p in reg.poses
                              if p.session_id == "photos_batch_0"])
        v_center = video_pos[:, :2].mean(axis=0)
        p_center = photo_pos[:, :2].mean(axis=0)
        dist = np.linalg.norm(v_center - p_center)
        assert 40 < dist < 120  # 网格间距 80m ± 环拍偏差

    def test_unit_quaternions(self, photos_dir):
        reg = mock_register(photos_dir)
        for p in reg.poses:
            assert abs(np.linalg.norm(p.quat_wxyz) - 1.0) < 1e-6

    def test_cameras_look_at_session_center(self, photos_dir):
        """OpenCV 约定下 +Z 是视线方向, 应大致指向会话锚点"""
        reg = mock_register(photos_dir)
        for p in reg.poses[:4]:
            rotation = p.rotation_matrix()
            forward = rotation[:, 2]  # c2w 第三列 = 世界系中的视线方向
            eye = np.array(p.t_xyz)
            sess_poses = np.array([q.t_xyz for q in reg.poses
                                   if q.session_id == p.session_id])
            center = sess_poses.mean(axis=0)
            center[2] = 2.0
            # 沿视线前进后应比原位置更接近会话中心
            closer = eye + forward * np.linalg.norm(eye - center) * 0.9
            assert np.linalg.norm(closer - center) < np.linalg.norm(eye - center)

    def test_register_writes_json(self, photos_dir, tmp_path):
        out = tmp_path / "reg.json"
        register(photos_dir, out, engine="mock")
        raw = out.read_bytes()
        # Trust root must be byte-reproducible across OSes (LF, no Windows CRLF).
        assert b"\r\n" not in raw
        data = json.loads(raw.decode("utf-8"))
        parsed = RegistrationResult(**data)
        assert parsed.engine == "mock"
        assert len(parsed.poses) == 12


def _quat_to_mat(q):
    w, x, y, z = np.array(q) / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


class TestColmapParser:
    def test_common_camera_models_preserve_calibrated_intrinsics(self):
        cameras = registration_module.parse_colmap_cameras_txt("\n".join([
            "# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]",
            "1 SIMPLE_PINHOLE 1000 800 700 500 400",
            "2 PINHOLE 1000 800 710 720 501 399",
            "3 SIMPLE_RADIAL 640 480 500 320 240 -0.01",
            "4 RADIAL 640 480 501 321 241 -0.01 0.001",
            "5 OPENCV 1920 1080 1500 1490 960 540 -0.1 0.01 0.001 -0.002",
        ]))

        assert cameras[1].intrinsics.model_dump() == {
            "width": 1000, "height": 800,
            "fx": 700.0, "fy": 700.0, "cx": 500.0, "cy": 400.0,
        }
        assert cameras[2].intrinsics.model_dump() == {
            "width": 1000, "height": 800,
            "fx": 710.0, "fy": 720.0, "cx": 501.0, "cy": 399.0,
        }
        assert cameras[3].distortion_parameters == {"k": -0.01}
        assert cameras[4].distortion_parameters == {"k1": -0.01, "k2": 0.001}
        assert cameras[5].distortion_parameters == {
            "k1": -0.1, "k2": 0.01, "p1": 0.001, "p2": -0.002,
        }

    def test_images_parser_retains_camera_id(self):
        txt = "\n".join([
            "1 1 0 0 0 1 2 3 7 folder/image one.jpg",
            "",
        ])
        records = registration_module.parse_colmap_image_records(txt)
        assert records["folder/image one.jpg"].camera_id == 7
        assert np.allclose(records["folder/image one.jpg"].t_xyz_c2w, [-1, -2, -3])

    def test_unknown_camera_model_fails_closed(self):
        with pytest.raises(ValueError, match="不支持的 COLMAP camera model.*MYSTERY"):
            registration_module.parse_colmap_cameras_txt(
                "1 MYSTERY 640 480 500 320 240"
            )

    def test_malformed_camera_parameter_count_fails_closed(self):
        with pytest.raises(ValueError, match="OPENCV.*需要 8 个参数.*实际 7"):
            registration_module.parse_colmap_cameras_txt(
                "1 OPENCV 640 480 500 500 320 240 0.1 0.01 0.001"
            )

    def test_w2c_to_c2w_conversion(self):
        # 恒等旋转 + tvec (1,2,3): c2w 平移应为 (-1,-2,-3)
        txt = "\n".join([
            "# comment line",
            "1 1 0 0 0 1 2 3 1 img_a.jpg",
            "",  # 2D 点行 (空)
            "2 1 0 0 0 0 0 0 1 img_b.jpg",
            "0 0 0",
        ])
        out = parse_colmap_images_txt(txt)
        assert set(out) == {"img_a.jpg", "img_b.jpg"}
        quat, t = out["img_a.jpg"]
        assert np.allclose(t, [-1, -2, -3], atol=1e-9)
        assert np.allclose(np.abs(quat), [1, 0, 0, 0], atol=1e-9)

    def test_rotation_inverted(self):
        # 90° 绕 Z 的 w2c → c2w 应为 -90°
        half = np.pi / 4
        qw, qz = np.cos(half), np.sin(half)
        txt = f"1 {qw} 0 0 {qz} 0 0 0 1 img.jpg\n\n"
        out = parse_colmap_images_txt(txt)
        quat, _ = out["img.jpg"]
        rotation_c2w = _quat_to_mat(quat)
        v = rotation_c2w @ np.array([1.0, 0, 0])
        assert np.allclose(v, [0, -1, 0], atol=1e-9)


def _write_colmap_model(workspace, cameras: str, images: str):
    model = workspace / "sparse" / "0"
    model.mkdir(parents=True)
    (model / "cameras.txt").write_text(cameras, encoding="utf-8")
    (model / "images.txt").write_text(images, encoding="utf-8")


def _stub_colmap_commands(monkeypatch):
    monkeypatch.setattr(
        registration_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )


class TestColmapRegistrationEvidence:
    def test_multi_camera_intrinsics_and_partial_coverage_are_auditable(
        self, photos_dir, tmp_path, monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        _write_colmap_model(
            workspace,
            cameras="\n".join([
                "1 PINHOLE 1000 800 710 720 501 399",
                "2 SIMPLE_RADIAL 640 480 500 320 240 -0.01",
            ]),
            images="\n".join([
                "1 1 0 0 0 0 0 0 1 IMG_000.jpg",
                "",
                "2 1 0 0 0 1 2 3 2 vid_A/vid_A_frame_000000.jpg",
                "",
            ]),
        )
        _stub_colmap_commands(monkeypatch)

        result = registration_module.colmap_register(photos_dir, workspace)
        poses = {pose.image: pose for pose in result.poses}
        assert poses["IMG_000.jpg"].intrinsics.model_dump() == {
            "width": 1000, "height": 800,
            "fx": 710.0, "fy": 720.0, "cx": 501.0, "cy": 399.0,
        }
        assert poses["vid_A/vid_A_frame_000000.jpg"].intrinsics.model_dump() == {
            "width": 640, "height": 480,
            "fx": 500.0, "fy": 500.0, "cx": 320.0, "cy": 240.0,
        }

        coverage_entry = next(
            item for item in result.pose_frame.evidence
            if item.startswith("colmap.registration.coverage.v1=")
        )
        coverage = json.loads(coverage_entry.split("=", 1)[1])
        assert coverage["registered_images"] == 2
        assert coverage["total_input_images"] == 12
        assert coverage["complete"] is False
        assert coverage["sessions"]["photos_batch_0"]["registered"] == 1
        assert coverage["sessions"]["photos_batch_0"]["total"] == 4
        assert coverage["sessions"]["video_vid_A"]["registered"] == 1
        assert coverage["sessions"]["video_vid_A"]["total"] == 8
        assert "IMG_001.jpg" in coverage["unregistered_images"]

        camera_entries = [
            json.loads(item.split("=", 1)[1])
            for item in result.pose_frame.evidence
            if item.startswith("colmap.camera.v1=")
        ]
        assert camera_entries == [
            {
                "camera_id": 1,
                "distortion_parameters": {},
                "height": 800,
                "model": "PINHOLE",
                "params": [710.0, 720.0, 501.0, 399.0],
                "pinhole_intrinsics_lossless": True,
                "width": 1000,
            },
            {
                "camera_id": 2,
                "distortion_parameters": {"k": -0.01},
                "height": 480,
                "model": "SIMPLE_RADIAL",
                "params": [500.0, 320.0, 240.0, -0.01],
                "pinhole_intrinsics_lossless": False,
                "width": 640,
            },
        ]

    def test_registered_image_with_missing_camera_fails_closed(
        self, photos_dir, tmp_path, monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        _write_colmap_model(
            workspace,
            cameras="1 PINHOLE 1000 800 710 720 501 399\n",
            images="1 1 0 0 0 0 0 0 99 IMG_000.jpg\n\n",
        )
        _stub_colmap_commands(monkeypatch)

        with pytest.raises(ValueError, match="IMG_000.jpg.*CAMERA_ID=99.*cameras.txt"):
            registration_module.colmap_register(photos_dir, workspace)

    def test_per_image_camera_calibration_survives_json_roundtrip(
        self, photos_dir, tmp_path, monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        _write_colmap_model(
            workspace,
            cameras="\n".join([
                "1 SIMPLE_RADIAL 640 480 500 320 240 -0.1",
                "2 SIMPLE_RADIAL 640 480 500 320 240 0.2",
            ]),
            images="\n".join([
                "1 1 0 0 0 0 0 0 1 IMG_000.jpg",
                "",
                "2 1 0 0 0 1 2 3 2 vid_A/vid_A_frame_000000.jpg",
                "",
            ]),
        )
        _stub_colmap_commands(monkeypatch)

        result = registration_module.colmap_register(photos_dir, workspace)
        restored = RegistrationResult.model_validate_json(result.model_dump_json())
        poses = {pose.image: pose for pose in restored.poses}

        assert poses["IMG_000.jpg"].camera_id == 1
        assert poses["IMG_000.jpg"].camera_model == "SIMPLE_RADIAL"
        assert poses["IMG_000.jpg"].camera_params == (
            500.0, 320.0, 240.0, -0.1,
        )
        assert poses["vid_A/vid_A_frame_000000.jpg"].camera_id == 2
        assert poses["vid_A/vid_A_frame_000000.jpg"].camera_model == "SIMPLE_RADIAL"
        assert poses["vid_A/vid_A_frame_000000.jpg"].camera_params == (
            500.0, 320.0, 240.0, 0.2,
        )


class TestColmapSubprocessTimeout:
    """colmap 子进程须有界: 卡死 (headless/集显 OpenGL SIFT 停滞、matcher 病态
    输入、I/O 挂起) 不能让整条管线永久 hang 且不抛错。超时 → RuntimeError (fail-closed,
    与 returncode!=0 分支同构), 而非无信号阻塞或原样上抛 TimeoutExpired。"""

    def test_stage_hang_raises_runtimeerror_not_indefinite_block(
        self, photos_dir, tmp_path, monkeypatch,
    ):
        workspace = tmp_path / "colmap"

        def fake_run(args, capture_output=True, text=True, timeout=None, **kwargs):
            if "-h" in args:  # sift 命名探测: 放行, 返回帮助文本
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout or 1)

        monkeypatch.setattr(registration_module.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="超时|timeout|timed out"):
            registration_module.colmap_register(photos_dir, workspace)

    def test_heavy_stages_pass_bounded_timeout_by_default(
        self, photos_dir, tmp_path, monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        _write_colmap_model(
            workspace,
            cameras="1 PINHOLE 1000 800 710 720 501 399\n",
            images="1 1 0 0 0 0 0 0 1 IMG_000.jpg\n\n",
        )
        seen = []

        def fake_run(args, capture_output=True, text=True, timeout=None, **kwargs):
            if "-h" not in args:  # 只记重活阶段, 跳过 sift 探测
                seen.append((args[1], timeout))
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(registration_module.subprocess, "run", fake_run)
        registration_module.colmap_register(photos_dir, workspace)

        stages = {name for name, _ in seen}
        assert {"feature_extractor", "exhaustive_matcher", "mapper"} <= stages
        assert seen, "重活阶段应被调用"
        assert all(t is not None and t > 0 for _, t in seen), \
            "每个重活阶段都须带有界 (非 None) 超时"


class TestGpsEnu:
    def test_north_offset(self):
        origin = GeoAnchor(lat=26.0, lon=119.0, alt=50)
        north = GeoAnchor(lat=26.0 + 100 / 111319.49, lon=119.0, alt=50)
        enu = gps_to_enu(north, origin)
        assert abs(enu[0]) < 0.1 and abs(enu[1] - 100) < 0.1

    def test_east_offset_scales_with_latitude(self):
        origin = GeoAnchor(lat=60.0, lon=10.0, alt=0)
        east = GeoAnchor(lat=60.0, lon=10.001, alt=0)
        enu = gps_to_enu(east, origin)
        # 纬度 60° 时东西向缩短为 cos(60°)=0.5
        expected = np.radians(0.001) * 6378137.0 * 0.5
        assert abs(enu[0] - expected) < 0.5
