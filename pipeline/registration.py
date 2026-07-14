"""
统一坐标系配准: 把所有输入图像 (照片 + 视频帧) 的相机位姿解算到同一个世界坐标系

策略:
- 会话划分: photos/ 顶层照片按设备分组为 photo_batch 会话;
  photos/<视频名>/ 子目录 (ingest 抽帧输出) 各为一个 video 会话
- 引擎:
  - colmap: 检测到 colmap 可执行文件时, 全部图像联合 SfM (单一 database + mapper)
    → 照片与视频帧共享特征匹配, 天然处于同一坐标系
  - mock:   无 colmap 时的确定性降级 — 每个会话生成绕锚点的环拍位姿,
    会话锚点由 EXIF GPS (ENU) 或网格布局决定, 输出 schema 与 colmap 路径完全一致
- 锚定: 首个含 GPS 的会话锚点作为世界原点 (geo_origin), 其余会话按 ENU 偏移;
  无 GPS 会话按确定性网格排布, 保证多次运行结果一致

输出: registration.json (recon_schema.RegistrationResult)

用法:
    python -m pipeline.registration --photos photos --out recon/registration.json
"""
import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path

import numpy as np
from loguru import logger

from pipeline.recon_schema import (
    CameraIntrinsics,
    CameraPose,
    CaptureSession,
    GeoAnchor,
    RegistrationResult,
    Sim3,
    gps_to_enu,
)

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

# mock 布局参数
SESSION_GRID_SPACING = 80.0   # 无 GPS 会话间隔 (米)
ORBIT_RADIUS_VIDEO = 25.0     # 视频环拍半径
ORBIT_RADIUS_PHOTO = 20.0     # 照片环拍半径


# ============ 工具 ============
def _rotmat_to_quat_wxyz(R: np.ndarray) -> list[float]:
    """旋转矩阵 → 单位四元数 wxyz (Shepperd 法, 数值稳定)"""
    tr = np.trace(R)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z])
    return (q / np.linalg.norm(q)).tolist()


def _look_at_c2w(eye: np.ndarray, target: np.ndarray) -> np.ndarray:
    """生成 OpenCV 约定 (+Z 前, +Y 下, +X 右) 的 camera-to-world 旋转矩阵"""
    forward = target - eye
    fn = np.linalg.norm(forward)
    forward = forward / fn if fn > 1e-9 else np.array([0.0, 1.0, 0.0])
    world_up = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, world_up)
    rn = np.linalg.norm(right)
    if rn < 1e-6:  # 正对天顶/地面, 用 X 轴兜底
        right = np.array([1.0, 0.0, 0.0])
    else:
        right = right / rn
    down = np.cross(forward, right)
    return np.stack([right, down, forward], axis=1)  # 列为相机轴


def _read_gps(path: Path) -> GeoAnchor | None:
    """从照片 EXIF 读 GPS, 无则 None"""
    try:
        import exifread
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)

        def dms_to_deg(tag, ref):
            vals = tag.values
            deg = float(vals[0].num) / vals[0].den
            minute = float(vals[1].num) / vals[1].den
            sec = float(vals[2].num) / vals[2].den
            d = deg + minute / 60 + sec / 3600
            return -d if str(ref) in ("S", "W") else d

        lat_t, lon_t = tags.get("GPS GPSLatitude"), tags.get("GPS GPSLongitude")
        if not lat_t or not lon_t:
            return None
        lat = dms_to_deg(lat_t, tags.get("GPS GPSLatitudeRef", "N"))
        lon = dms_to_deg(lon_t, tags.get("GPS GPSLongitudeRef", "E"))
        alt_t = tags.get("GPS GPSAltitude")
        alt = float(alt_t.values[0].num) / alt_t.values[0].den if alt_t else 0.0
        return GeoAnchor(lat=lat, lon=lon, alt=alt)
    except Exception:
        return None


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size  # (w, h)
    except Exception:
        return (1920, 1080)


# ============ 会话划分 ============
def group_sessions(photos_dir: str | Path) -> list[CaptureSession]:
    """扫描 photos 目录 → 采集会话列表

    - 子目录 = 视频会话 (ingest 把每个视频的帧放独立子目录)
    - 顶层照片 = photo_batch 会话 (单批; GPS 锚点取有 GPS 照片的均值)
    """
    photos_dir = Path(photos_dir)
    if not photos_dir.exists():
        raise FileNotFoundError(f"photos 目录不存在: {photos_dir}")

    sessions: list[CaptureSession] = []

    # 视频会话: 每个子目录
    for sub in sorted(p for p in photos_dir.iterdir() if p.is_dir()):
        frames = sorted(f.name for f in sub.iterdir()
                        if f.suffix.lower() in PHOTO_EXTS)
        if not frames:
            continue
        sessions.append(CaptureSession(
            session_id=f"video_{sub.name}",
            kind="video",
            source=sub.name,
            images=[f"{sub.name}/{f}" for f in frames],
        ))

    # 照片会话: 顶层散图
    top_photos = sorted(f for f in photos_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in PHOTO_EXTS)
    if top_photos:
        gps_list = [g for g in (_read_gps(p) for p in top_photos) if g]
        anchor = None
        if gps_list:
            anchor = GeoAnchor(
                lat=float(np.mean([g.lat for g in gps_list])),
                lon=float(np.mean([g.lon for g in gps_list])),
                alt=float(np.mean([g.alt for g in gps_list])),
            )
        sessions.append(CaptureSession(
            session_id="photos_batch_0",
            kind="photo_batch",
            source=str(photos_dir.name),
            images=[p.name for p in top_photos],
            geo_anchor=anchor,
        ))

    if not sessions:
        raise ValueError(
            f"{photos_dir} 中没有任何可用图像 (支持: {sorted(PHOTO_EXTS)}); "
            f"先运行 python -m pipeline.ingest 处理 input/ 目录")

    logger.info(f"会话划分: {len(sessions)} 个会话, "
                f"共 {sum(len(s.images) for s in sessions)} 张图")
    return sessions


# ============ mock 配准 (确定性降级) ============
def _session_seed(session: CaptureSession) -> int:
    h = hashlib.sha1(
        (session.session_id + "|".join(session.images)).encode()).hexdigest()
    return int(h[:8], 16)


def _session_anchor_xy(sessions: list[CaptureSession]) -> dict[str, np.ndarray]:
    """确定各会话锚点的世界坐标: GPS → ENU, 无 GPS → 确定性网格"""
    origin = next((s.geo_anchor for s in sessions if s.geo_anchor), None)
    anchors: dict[str, np.ndarray] = {}
    grid_i = 0
    for s in sessions:
        if s.geo_anchor and origin:
            anchors[s.session_id] = gps_to_enu(s.geo_anchor, origin)
        else:
            # 无 GPS: 沿 X 轴网格排布, 顺序确定 (sessions 已排序)
            anchors[s.session_id] = np.array(
                [grid_i * SESSION_GRID_SPACING, 0.0, 0.0])
            grid_i += 1
    return anchors


def mock_register(photos_dir: str | Path,
                  sessions: list[CaptureSession] | None = None) -> RegistrationResult:
    """确定性 mock 配准: 每个会话绕锚点环拍, 输出统一世界坐标系位姿

    同一输入必然产生同一输出 (种子取自会话内容 hash), 保证可复现。
    """
    photos_dir = Path(photos_dir)
    sessions = sessions or group_sessions(photos_dir)
    anchors = _session_anchor_xy(sessions)
    origin = next((s.geo_anchor for s in sessions if s.geo_anchor), None)

    poses: list[CameraPose] = []
    for sess in sessions:
        center = anchors[sess.session_id]
        rng = np.random.default_rng(_session_seed(sess))
        n = len(sess.images)
        radius = ORBIT_RADIUS_VIDEO if sess.kind == "video" else ORBIT_RADIUS_PHOTO
        start_angle = rng.uniform(0, 2 * np.pi)

        for i, img in enumerate(sess.images):
            if sess.kind == "video":
                # 视频: 时间连续的平滑环拍弧线
                angle = start_angle + 2 * np.pi * i / max(n, 1)
                height = 12.0 + 6.0 * np.sin(2 * np.pi * i / max(n, 1))
            else:
                # 照片: 环上确定性散布
                angle = start_angle + 2 * np.pi * i / max(n, 1) \
                    + rng.uniform(-0.15, 0.15)
                height = rng.uniform(1.6, 8.0)
            eye = center + np.array([radius * np.cos(angle),
                                     radius * np.sin(angle), height])
            look_target = center + np.array([0.0, 0.0, 2.0])
            R = _look_at_c2w(eye, look_target)

            w, h = _image_size(photos_dir / img)
            poses.append(CameraPose(
                image=img,
                session_id=sess.session_id,
                quat_wxyz=_rotmat_to_quat_wxyz(R),
                t_xyz=eye.tolist(),
                intrinsics=CameraIntrinsics.from_fov(w, h, 60.0),
            ))

    return RegistrationResult(
        engine="mock",
        geo_origin=origin,
        sessions=sessions,
        poses=poses,
        session_to_world={s.session_id: Sim3() for s in sessions},
    )


# ============ COLMAP 联合配准 ============
def parse_colmap_images_txt(text: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """解析 COLMAP images.txt → {图像名: (quat_wxyz_c2w, t_xyz_c2w)}

    COLMAP 存 world-to-camera (qvec, tvec); 转 c2w: R_c2w = R^T, t_c2w = -R^T t
    """
    out = {}
    lines = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
    # images.txt 每图两行: 位姿行 + 2D 点行 (点行可为空, 不能按空行过滤)
    pose_lines = []
    expect_pose = True
    for line in lines:
        if expect_pose:
            if not line.strip():
                continue  # 杂散空行, 不改变期待状态
            pose_lines.append(line)
            expect_pose = False
        else:
            expect_pose = True  # 消费 2D 点行 (可为空)
    for line in pose_lines:
        parts = line.split()
        if len(parts) < 10:
            continue
        qw, qx, qy, qz = map(float, parts[1:5])
        tx, ty, tz = map(float, parts[5:8])
        name = parts[9]
        # w2c 四元数 → 旋转矩阵
        q = np.array([qw, qx, qy, qz])
        q = q / np.linalg.norm(q)
        w, x, y, z = q
        R_w2c = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ])
        R_c2w = R_w2c.T
        t_c2w = -R_c2w @ np.array([tx, ty, tz])
        out[name] = (np.array(_rotmat_to_quat_wxyz(R_c2w)), t_c2w)
    return out


def colmap_available() -> bool:
    return shutil.which("colmap") is not None


def colmap_register(photos_dir: str | Path, workspace: str | Path,
                    sessions: list[CaptureSession] | None = None) -> RegistrationResult:
    """COLMAP 联合 SfM: 所有照片 + 视频帧进同一个模型 → 坐标系天然一致

    要求系统安装 colmap。SfM 尺度不确定, 结果坐标为 SfM 系;
    若有 GPS 锚点可后续用 Sim3 对齐, 否则按场景归一化。
    """
    photos_dir = Path(photos_dir)
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    sessions = sessions or group_sessions(photos_dir)
    db = workspace / "colmap.db"
    sparse = workspace / "sparse"
    sparse.mkdir(exist_ok=True)

    def run(args: list[str]):
        logger.info(f"colmap {' '.join(args[:2])} ...")
        proc = subprocess.run(["colmap", *args], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"colmap {args[0]} 失败 (exit {proc.returncode}):\n"
                f"{proc.stderr[-2000:] if proc.stderr else proc.stdout[-2000:]}")

    run(["feature_extractor", "--database_path", str(db),
         "--image_path", str(photos_dir),
         "--ImageReader.camera_model", "SIMPLE_RADIAL"])
    # 图少时穷举匹配最稳; 图多时顺序匹配利用视频帧时间连续性
    n_images = sum(len(s.images) for s in sessions)
    matcher = "exhaustive_matcher" if n_images <= 400 else "sequential_matcher"
    run([matcher, "--database_path", str(db)])
    run(["mapper", "--database_path", str(db),
         "--image_path", str(photos_dir), "--output_path", str(sparse)])
    if not (sparse / "0").exists():
        raise RuntimeError(
            f"COLMAP mapper 未产出模型 ({sparse}/0 不存在): "
            f"图像间特征匹配不足, 无法联合配准。"
            f"建议: 增加视角重叠 / 提高抽帧率 / 或改用 --engine mock")
    run(["model_converter", "--input_path", str(sparse / "0"),
         "--output_path", str(sparse / "0"), "--output_type", "TXT"])

    images_txt = (sparse / "0" / "images.txt").read_text()
    name_to_pose = parse_colmap_images_txt(images_txt)

    img_to_session = {img: s.session_id for s in sessions for img in s.images}
    poses = []
    for name, (quat, t) in sorted(name_to_pose.items()):
        if name not in img_to_session:
            continue
        w, h = _image_size(photos_dir / name)
        poses.append(CameraPose(
            image=name, session_id=img_to_session[name],
            quat_wxyz=quat.tolist(), t_xyz=t.tolist(),
            intrinsics=CameraIntrinsics.from_fov(w, h, 60.0),
        ))
    logger.info(f"COLMAP 配准: {len(poses)}/{n_images} 张图注册成功 (联合模型, 坐标系一致)")

    origin = next((s.geo_anchor for s in sessions if s.geo_anchor), None)
    return RegistrationResult(
        engine="colmap",
        geo_origin=origin,
        sessions=sessions,
        poses=poses,
        session_to_world={s.session_id: Sim3() for s in sessions},
    )


# ============ 统一入口 ============
def register(photos_dir: str | Path, out_json: str | Path | None = None,
             engine: str = "auto",
             workspace: str | Path = "recon/colmap_ws") -> RegistrationResult:
    """配准入口: engine = auto | colmap | mock"""
    if engine == "auto":
        engine = "colmap" if colmap_available() else "mock"
        logger.info(f"配准引擎自动选择: {engine}")

    if engine == "colmap":
        result = colmap_register(photos_dir, workspace)
    elif engine == "mock":
        result = mock_register(photos_dir)
    else:
        raise ValueError(f"未知引擎: {engine}")

    if out_json:
        out_json = Path(out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        logger.info(f"配准结果已写入: {out_json} ({len(result.poses)} 个位姿)")
    return result


def main():
    parser = argparse.ArgumentParser(description="统一坐标系配准 (照片 + 视频帧)")
    parser.add_argument("--photos", default="photos", help="输入图像目录")
    parser.add_argument("--out", default="recon/registration.json", help="输出 JSON")
    parser.add_argument("--engine", default="auto",
                        choices=["auto", "colmap", "mock"])
    args = parser.parse_args()
    result = register(args.photos, args.out, args.engine)
    print(f"引擎: {result.engine} | 会话: {len(result.sessions)} | "
          f"位姿: {len(result.poses)}")


if __name__ == "__main__":
    main()
