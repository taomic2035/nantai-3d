"""统一坐标系配准: 会话划分 / mock 确定性 / 坐标一致性 / COLMAP 解析"""
import json

import numpy as np

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
        for p1, p2 in zip(r1.poses, r2.poses):
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
            R = p.rotation_matrix()
            forward = R[:, 2]  # c2w 第三列 = 世界系中的视线方向
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
        data = json.loads(out.read_text())
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
        R_c2w = _quat_to_mat(quat)
        v = R_c2w @ np.array([1.0, 0, 0])
        assert np.allclose(v, [0, -1, 0], atol=1e-9)


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
