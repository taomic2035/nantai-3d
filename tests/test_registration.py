"""统一坐标系配准: 会话划分 / mock 确定性 / 坐标一致性 / COLMAP 解析"""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import pipeline.registration as registration_module
from pipeline.recon_schema import (
    CameraIntrinsics,
    CameraPose,
    GeoAnchor,
    RegistrationResult,
    gps_to_enu,
)
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
    def test_camera_pose_quaternion_validation_is_exactly_idempotent(self):
        pose = CameraPose(
            image="frame_00088.png",
            session_id="photos_batch_0",
            quat_wxyz=[
                0.5836519348461496,
                0.023308089471694358,
                -0.7520642199095301,
                -0.30529749597301914,
            ],
            t_xyz=[1.0, 2.0, 3.0],
            intrinsics=CameraIntrinsics(
                width=640,
                height=480,
                fx=500.0,
                fy=500.0,
                cx=320.0,
                cy=240.0,
            ),
        )

        reparsed = CameraPose.model_validate_json(pose.model_dump_json())

        assert reparsed == pose

    def test_all_images_get_poses(self, photos_dir):
        reg = mock_register(photos_dir)
        assert len(reg.poses) == 12
        assert {p.session_id for p in reg.poses} == {s.session_id for s in reg.sessions}

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
        video_pos = np.array([p.t_xyz for p in reg.poses if p.session_id == "video_vid_A"])
        photo_pos = np.array([p.t_xyz for p in reg.poses if p.session_id == "photos_batch_0"])
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
            sess_poses = np.array([q.t_xyz for q in reg.poses if q.session_id == p.session_id])
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
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


class TestColmapParser:
    def test_common_camera_models_preserve_calibrated_intrinsics(self):
        cameras = registration_module.parse_colmap_cameras_txt(
            "\n".join(
                [
                    "# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]",
                    "1 SIMPLE_PINHOLE 1000 800 700 500 400",
                    "2 PINHOLE 1000 800 710 720 501 399",
                    "3 SIMPLE_RADIAL 640 480 500 320 240 -0.01",
                    "4 RADIAL 640 480 501 321 241 -0.01 0.001",
                    "5 OPENCV 1920 1080 1500 1490 960 540 -0.1 0.01 0.001 -0.002",
                ]
            )
        )

        assert cameras[1].intrinsics.model_dump() == {
            "width": 1000,
            "height": 800,
            "fx": 700.0,
            "fy": 700.0,
            "cx": 500.0,
            "cy": 400.0,
        }
        assert cameras[2].intrinsics.model_dump() == {
            "width": 1000,
            "height": 800,
            "fx": 710.0,
            "fy": 720.0,
            "cx": 501.0,
            "cy": 399.0,
        }
        assert cameras[3].distortion_parameters == {"k": -0.01}
        assert cameras[4].distortion_parameters == {"k1": -0.01, "k2": 0.001}
        assert cameras[5].distortion_parameters == {
            "k1": -0.1,
            "k2": 0.01,
            "p1": 0.001,
            "p2": -0.002,
        }

    def test_images_parser_retains_camera_id(self):
        txt = "\n".join(
            [
                "1 1 0 0 0 1 2 3 7 folder/image one.jpg",
                "",
            ]
        )
        records = registration_module.parse_colmap_image_records(txt)
        assert records["folder/image one.jpg"].camera_id == 7
        assert np.allclose(records["folder/image one.jpg"].t_xyz_c2w, [-1, -2, -3])

    def test_unknown_camera_model_fails_closed(self):
        with pytest.raises(ValueError, match="不支持的 COLMAP camera model.*MYSTERY"):
            registration_module.parse_colmap_cameras_txt("1 MYSTERY 640 480 500 320 240")

    def test_malformed_camera_parameter_count_fails_closed(self):
        with pytest.raises(ValueError, match="OPENCV.*需要 8 个参数.*实际 7"):
            registration_module.parse_colmap_cameras_txt(
                "1 OPENCV 640 480 500 500 320 240 0.1 0.01 0.001"
            )

    def test_w2c_to_c2w_conversion(self):
        # 恒等旋转 + tvec (1,2,3): c2w 平移应为 (-1,-2,-3)
        txt = "\n".join(
            [
                "# comment line",
                "1 1 0 0 0 1 2 3 1 img_a.jpg",
                "",  # 2D 点行 (空)
                "2 1 0 0 0 0 0 0 1 img_b.jpg",
                "0 0 0",
            ]
        )
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


def _write_colmap_model(workspace, cameras: str, images: str, model_index: int = 0):
    model = workspace / "sparse" / str(model_index)
    model.mkdir(parents=True)
    (model / "cameras.txt").write_text(cameras, encoding="utf-8")
    if images.endswith("\n") and not images.endswith("\n\n"):
        images += "\n"
    (model / "images.txt").write_text(images, encoding="utf-8")
    (model / "points3D.txt").write_text("", encoding="utf-8")


def _stub_colmap_commands(monkeypatch):
    monkeypatch.setattr(
        registration_module,
        "_find_colmap_binary",
        lambda: "colmap",
    )
    monkeypatch.setattr(
        registration_module,
        "_sha256_colmap_binary",
        lambda _: "a" * 64,
    )
    monkeypatch.setattr(
        registration_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stderr="COLMAP 4.1.0\n" if "-h" in args[0] else "",
            stdout="",
        ),
    )


class TestColmapRegistrationEvidence:
    def test_runtime_version_and_commands_use_one_resolved_binary(
        self,
        photos_dir,
        tmp_path,
        monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        _write_colmap_model(
            workspace,
            cameras="1 PINHOLE 1000 800 710 720 501 399\n",
            images="1 1 0 0 0 0 0 0 1 IMG_000.jpg\n\n",
        )
        first_path = tmp_path / "colmap-a"
        second_path = tmp_path / "colmap-b"
        first_path.write_bytes(b"first-colmap-binary")
        second_path.write_bytes(b"second-colmap-binary")
        first_binary = str(first_path)
        second_binary = str(second_path)
        resolved = iter((first_binary, second_binary))
        monkeypatch.setattr(
            registration_module,
            "_find_colmap_binary",
            lambda: next(resolved),
        )
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            del kwargs
            calls.append(args)
            if args[1:] == ["feature_extractor", "-h"]:
                return SimpleNamespace(
                    returncode=0,
                    stderr="COLMAP 4.1.0 (Commit measured)\n",
                    stdout="",
                )
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(registration_module.subprocess, "run", fake_run)

        result = registration_module.colmap_register(photos_dir, workspace)

        assert calls
        assert {call[0] for call in calls} == {first_binary}
        runtime_entries = [
            item for item in result.pose_frame.evidence if item.startswith("colmap.runtime.v1=")
        ]
        assert len(runtime_entries) == 1
        runtime = json.loads(runtime_entries[0].split("=", 1)[1])
        assert runtime == {
            "binary_name": "colmap-a",
            "binary_sha256": hashlib.sha256(b"first-colmap-binary").hexdigest(),
            "engine_version": "COLMAP 4.1.0",
        }

    def test_registration_rejects_colmap_binary_changed_during_run(
        self,
        photos_dir,
        tmp_path,
        monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        _write_colmap_model(
            workspace,
            cameras="1 PINHOLE 1000 800 710 720 501 399\n",
            images="1 1 0 0 0 0 0 0 1 IMG_000.jpg\n\n",
        )
        binary = tmp_path / "colmap-bin"
        binary.write_bytes(b"fake-colmap")
        monkeypatch.setattr(
            registration_module,
            "_find_colmap_binary",
            lambda: str(binary),
        )
        measured = iter(("a" * 64, "b" * 64))
        monkeypatch.setattr(
            registration_module,
            "_sha256_colmap_binary",
            lambda _: next(measured),
            raising=False,
        )

        def fake_run(args, **kwargs):
            del kwargs
            if args[1:] == ["feature_extractor", "-h"]:
                return SimpleNamespace(
                    returncode=0,
                    stderr="COLMAP 4.1.0\n",
                    stdout="",
                )
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(registration_module.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="binary.*changed|changed.*binary"):
            registration_module.colmap_register(photos_dir, workspace)

    def test_largest_sparse_model_drives_registration_and_evidence(
        self,
        photos_dir,
        tmp_path,
        monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        cameras = "1 PINHOLE 1000 800 710 720 501 399\n"
        _write_colmap_model(
            workspace,
            cameras=cameras,
            images="1 1 0 0 0 0 0 0 1 IMG_000.jpg\n0 0 -1\n",
            model_index=0,
        )
        _write_colmap_model(
            workspace,
            cameras=cameras,
            images="\n".join(
                [
                    "1 1 0 0 0 0 0 0 1 IMG_001.jpg",
                    "0 0 -1",
                    "2 1 0 0 0 1 2 3 1 vid_A/vid_A_frame_000000.jpg",
                    "0 0 -1",
                ]
            ),
            model_index=1,
        )
        calls: list[list[str]] = []
        monkeypatch.setattr(
            registration_module,
            "_find_colmap_binary",
            lambda: "colmap",
        )
        monkeypatch.setattr(
            registration_module,
            "_sha256_colmap_binary",
            lambda _: "a" * 64,
        )

        def fake_run(args, **kwargs):
            del kwargs
            calls.append(args)
            return SimpleNamespace(
                returncode=0,
                stderr="COLMAP 4.1.0\n" if "-h" in args else "",
                stdout="",
            )

        monkeypatch.setattr(registration_module.subprocess, "run", fake_run)

        result = registration_module.colmap_register(photos_dir, workspace)

        assert {pose.image for pose in result.poses} == {
            "IMG_001.jpg",
            "vid_A/vid_A_frame_000000.jpg",
        }
        converted = {
            Path(call[call.index("--input_path") + 1]).name
            for call in calls
            if len(call) > 1 and call[1] == "model_converter"
        }
        assert converted == {"0", "1"}
        selection = next(
            item
            for item in result.pose_frame.evidence
            if item.startswith("colmap.sparse-model-selection.v1=")
        )
        payload = json.loads(selection.split("=", 1)[1])
        assert payload["selected_model_index"] == 1
        assert payload["selection_rule"] == "largest_image_count"
        assert len(payload["enumeration_sha256"]) == 64

    def test_multi_camera_intrinsics_and_partial_coverage_are_auditable(
        self,
        photos_dir,
        tmp_path,
        monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        _write_colmap_model(
            workspace,
            cameras="\n".join(
                [
                    "1 PINHOLE 1000 800 710 720 501 399",
                    "2 SIMPLE_RADIAL 640 480 500 320 240 -0.01",
                ]
            ),
            images="\n".join(
                [
                    "1 1 0 0 0 0 0 0 1 IMG_000.jpg",
                    "",
                    "2 1 0 0 0 1 2 3 2 vid_A/vid_A_frame_000000.jpg",
                    "",
                ]
            ),
        )
        _stub_colmap_commands(monkeypatch)

        result = registration_module.colmap_register(photos_dir, workspace)
        poses = {pose.image: pose for pose in result.poses}
        assert poses["IMG_000.jpg"].intrinsics.model_dump() == {
            "width": 1000,
            "height": 800,
            "fx": 710.0,
            "fy": 720.0,
            "cx": 501.0,
            "cy": 399.0,
        }
        assert poses["vid_A/vid_A_frame_000000.jpg"].intrinsics.model_dump() == {
            "width": 640,
            "height": 480,
            "fx": 500.0,
            "fy": 500.0,
            "cx": 320.0,
            "cy": 240.0,
        }

        coverage_entry = next(
            item
            for item in result.pose_frame.evidence
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
        self,
        photos_dir,
        tmp_path,
        monkeypatch,
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
        self,
        photos_dir,
        tmp_path,
        monkeypatch,
    ):
        workspace = tmp_path / "colmap"
        _write_colmap_model(
            workspace,
            cameras="\n".join(
                [
                    "1 SIMPLE_RADIAL 640 480 500 320 240 -0.1",
                    "2 SIMPLE_RADIAL 640 480 500 320 240 0.2",
                ]
            ),
            images="\n".join(
                [
                    "1 1 0 0 0 0 0 0 1 IMG_000.jpg",
                    "",
                    "2 1 0 0 0 1 2 3 2 vid_A/vid_A_frame_000000.jpg",
                    "",
                ]
            ),
        )
        _stub_colmap_commands(monkeypatch)

        result = registration_module.colmap_register(photos_dir, workspace)
        restored = RegistrationResult.model_validate_json(result.model_dump_json())
        poses = {pose.image: pose for pose in restored.poses}

        assert poses["IMG_000.jpg"].camera_id == 1
        assert poses["IMG_000.jpg"].camera_model == "SIMPLE_RADIAL"
        assert poses["IMG_000.jpg"].camera_params == (
            500.0,
            320.0,
            240.0,
            -0.1,
        )
        assert poses["vid_A/vid_A_frame_000000.jpg"].camera_id == 2
        assert poses["vid_A/vid_A_frame_000000.jpg"].camera_model == "SIMPLE_RADIAL"
        assert poses["vid_A/vid_A_frame_000000.jpg"].camera_params == (
            500.0,
            320.0,
            240.0,
            0.2,
        )


class TestColmapSubprocessTimeout:
    """colmap 子进程须有界: 卡死 (headless/集显 OpenGL SIFT 停滞、matcher 病态
    输入、I/O 挂起) 不能让整条管线永久 hang 且不抛错。超时 → RuntimeError (fail-closed,
    与 returncode!=0 分支同构), 而非无信号阻塞或原样上抛 TimeoutExpired。"""

    def test_stage_hang_raises_runtimeerror_not_indefinite_block(
        self,
        photos_dir,
        tmp_path,
        monkeypatch,
    ):
        workspace = tmp_path / "colmap"

        def fake_run(args, capture_output=True, text=True, timeout=None, **kwargs):
            if "-h" in args:  # sift 命名探测: 放行, 返回帮助文本
                return SimpleNamespace(
                    returncode=0,
                    stderr="COLMAP 4.1.0\n",
                    stdout="",
                )
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout or 1)

        monkeypatch.setattr(
            registration_module,
            "_find_colmap_binary",
            lambda: "colmap",
        )
        monkeypatch.setattr(
            registration_module,
            "_sha256_colmap_binary",
            lambda _: "a" * 64,
        )
        monkeypatch.setattr(registration_module.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="超时|timeout|timed out"):
            registration_module.colmap_register(photos_dir, workspace)

    def test_heavy_stages_pass_bounded_timeout_by_default(
        self,
        photos_dir,
        tmp_path,
        monkeypatch,
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
            return SimpleNamespace(
                returncode=0,
                stderr="COLMAP 4.1.0\n" if "-h" in args else "",
                stdout="",
            )

        monkeypatch.setattr(
            registration_module,
            "_find_colmap_binary",
            lambda: "colmap",
        )
        monkeypatch.setattr(
            registration_module,
            "_sha256_colmap_binary",
            lambda _: "a" * 64,
        )
        monkeypatch.setattr(registration_module.subprocess, "run", fake_run)
        registration_module.colmap_register(photos_dir, workspace)

        stages = {name for name, _ in seen}
        assert {"feature_extractor", "exhaustive_matcher", "mapper"} <= stages
        assert seen, "重活阶段应被调用"
        assert all(t is not None and t > 0 for _, t in seen), (
            "每个重活阶段都须带有界 (非 None) 超时"
        )


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


class TestFindColmapBinary:
    """_find_colmap_binary resolves COLMAP without requiring PATH manipulation.

    FEEDBACK-HANDOFF-OPUS-011 §1 identified that registration.py relied on
    shutil.which("colmap") only, missing the bundled third/colmap/bin/ install.
    The fix (commit d6743c8) mirrors scripts/doctor.py _find_binary. These
    tests are hermetic — they do not depend on a real COLMAP install.
    """

    def test_finds_bundled_exe_in_third_colmap_bin(self, tmp_path, monkeypatch):
        import pipeline.registration as reg

        fake_root = tmp_path
        colmap_dir = fake_root / "third" / "colmap" / "bin"
        colmap_dir.mkdir(parents=True)
        exe = colmap_dir / "colmap.exe"
        exe.write_bytes(b"fake")
        monkeypatch.setattr(reg, "_REPO_ROOT", fake_root)
        monkeypatch.setattr(reg.shutil, "which", lambda _: None)

        result = reg._find_colmap_binary()
        assert result is not None
        assert Path(result).name == "colmap.exe"

    def test_finds_bundled_binary_without_bin_subdir(self, tmp_path, monkeypatch):
        import pipeline.registration as reg

        fake_root = tmp_path
        colmap_dir = fake_root / "third" / "colmap"
        colmap_dir.mkdir(parents=True)
        exe = colmap_dir / "colmap.exe"
        exe.write_bytes(b"fake")
        monkeypatch.setattr(reg, "_REPO_ROOT", fake_root)
        monkeypatch.setattr(reg.shutil, "which", lambda _: None)

        result = reg._find_colmap_binary()
        assert result is not None
        assert Path(result).name == "colmap.exe"

    def test_falls_back_to_path_when_third_absent(self, tmp_path, monkeypatch):
        import pipeline.registration as reg

        monkeypatch.setattr(reg, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(reg.shutil, "which", lambda _: "/usr/bin/colmap")

        result = reg._find_colmap_binary()
        assert result == "/usr/bin/colmap"

    def test_returns_none_when_not_found_anywhere(self, tmp_path, monkeypatch):
        import pipeline.registration as reg

        monkeypatch.setattr(reg, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(reg.shutil, "which", lambda _: None)

        assert reg._find_colmap_binary() is None
        assert reg.colmap_available() is False

    def test_version_probe_binds_the_active_colmap_banner(self, monkeypatch):
        import pipeline.registration as reg

        monkeypatch.setattr(reg, "_find_colmap_binary", lambda: "/tools/colmap")
        monkeypatch.setattr(
            reg.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="",
                stderr=("COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26 without CUDA)\n"),
            ),
        )
        assert reg.colmap_version() == "COLMAP 4.1.0"

    def test_version_probe_rejects_an_unidentified_binary(self, monkeypatch):
        import pipeline.registration as reg

        monkeypatch.setattr(reg, "_find_colmap_binary", lambda: "/tools/colmap")
        monkeypatch.setattr(
            reg.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="feature extractor help",
                stderr="",
            ),
        )
        with pytest.raises(RuntimeError, match="version"):
            reg.colmap_version()

    def test_version_probe_does_not_cache_a_replaced_binary(self, monkeypatch):
        import pipeline.registration as reg

        versions = iter(("COLMAP 4.1.0\n", "COLMAP 4.2.0\n"))
        monkeypatch.setattr(
            reg.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="",
                stderr=next(versions),
            ),
        )
        assert reg.colmap_version("/tools/colmap") == "COLMAP 4.1.0"
        assert reg.colmap_version("/tools/colmap") == "COLMAP 4.2.0"

    def test_colmap_register_raises_clear_error_when_binary_missing(
        self,
        tmp_path,
        monkeypatch,
    ):
        import pipeline.registration as reg

        photos = tmp_path / "photos"
        photos.mkdir()
        (photos / "IMG_0001.jpg").write_bytes(b"fake")
        workspace = tmp_path / "colmap"

        monkeypatch.setattr(reg, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(reg.shutil, "which", lambda _: None)

        with pytest.raises(RuntimeError, match="colmap not found"):
            reg.colmap_register(photos, workspace)


class TestColmapBinaryReaderIntegrity:
    """Security boundary tests for _sha256_colmap_binary.

    The COLMAP binary hash binds runtime evidence to one specific executable.
    A check-then-reopen (lstat then Path.open) would let an attacker swap the
    binary between validation and reading. The secure pattern uses a single
    descriptor from os.open+O_NOFOLLOW and rechecks file identity before and
    after hashing.
    """

    def test_sha256_colmap_binary_rejects_ancestor_reparse(
        self,
        tmp_path,
        monkeypatch,
    ):
        import pipeline.registration as reg
        from pipeline.registration import _sha256_colmap_binary

        target = tmp_path / "colmap.bin"
        target.write_bytes(b"colmap-executable\n")
        sentinel = tmp_path / "ancestor-reparse"
        original = reg.first_linklike_path

        def fake_first_linklike_path(root, leaf):
            if Path(leaf) == target:
                return sentinel
            return original(root, leaf)

        monkeypatch.setattr(reg, "first_linklike_path", fake_first_linklike_path)
        with pytest.raises(
            RuntimeError,
            match="regular non-link file|cannot be inspected",
        ):
            _sha256_colmap_binary(target)

    def test_sha256_colmap_binary_rejects_path_swap_before_open(
        self,
        tmp_path,
        monkeypatch,
    ):
        from pipeline.registration import _sha256_colmap_binary

        target = tmp_path / "colmap.bin"
        target.write_bytes(b"original-binary\n")
        swap_count = 0
        original_open = os.open

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swap_count
            swap_count += 1
            if swap_count == 1:
                # Swap the file bytes between lstat and os.open so the
                # fstat-after-open identity check fires.
                target.write_bytes(b"swapped-binary-content\n")
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", swapping_open)
        with pytest.raises(RuntimeError, match="changed before hash|cannot be"):
            _sha256_colmap_binary(target)

    def test_sha256_colmap_binary_rejects_swap_during_read(
        self,
        tmp_path,
        monkeypatch,
    ):
        import pipeline.registration as reg
        from pipeline.registration import _sha256_colmap_binary

        target = tmp_path / "colmap.bin"
        target.write_bytes(b"colmap-binary-payload\n")
        # Stub first_linklike_path so the only lstat calls on `target` are
        # the implementation's own before/after rechecks.
        monkeypatch.setattr(reg, "first_linklike_path", lambda root, leaf: None)
        original_lstat = Path.lstat
        swap_state = {"target_lstat_calls": 0}

        def swapping_lstat(self):
            if self == target:
                swap_state["target_lstat_calls"] += 1
                # Call #1 = before-open lstat, call #2 = post-read lstat.
                if swap_state["target_lstat_calls"] == 2:
                    target.write_bytes(b"swapped-after-read\n")
            return original_lstat(self)

        monkeypatch.setattr(Path, "lstat", swapping_lstat)
        with pytest.raises(RuntimeError, match="changed while being hashed"):
            _sha256_colmap_binary(target)

    def test_sha256_colmap_binary_hashes_match_stable_read(self, tmp_path):
        from pipeline.registration import _sha256_colmap_binary

        target = tmp_path / "colmap.bin"
        payload = b"stable-colmap-bytes\n"
        target.write_bytes(payload)
        digest = _sha256_colmap_binary(target)
        assert digest == hashlib.sha256(payload).hexdigest()

    def test_sha256_colmap_binary_rejects_symlink_target(
        self,
        tmp_path,
        monkeypatch,
    ):
        from pipeline.registration import _sha256_colmap_binary

        real = tmp_path / "real-colmap.bin"
        real.write_bytes(b"real-binary\n")
        link = tmp_path / "colmap-link.bin"
        try:
            link.symlink_to(real)
        except OSError:
            pytest.skip("symlink creation is unavailable")
        with pytest.raises(RuntimeError, match="regular non-link file"):
            _sha256_colmap_binary(link)
