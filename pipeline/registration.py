"""
带显式坐标契约的配准: 把照片与视频帧解算到一个有名称、可审计的共同 frame

策略:
- 会话划分: photos/ 顶层照片按设备分组为 photo_batch 会话;
  photos/<视频名>/ 子目录 (ingest 抽帧输出) 各为一个 video 会话
- 引擎:
  - colmap: 检测到 colmap 可执行文件时, 全部图像联合 SfM (单一 database + mapper)
    → 照片与视频帧共享同一 SfM-local frame，但尺度任意、未地理对齐
  - mock:   无 colmap 时的确定性降级 — 每个会话生成绕锚点的环拍位姿,
    会话锚点由 EXIF GPS (ENU) 或网格布局决定，始终标 synthetic provenance
- GPS: mock 可用首个 GPS 锚点定义 synthetic ENU；COLMAP 仅有 GPS origin 时不会被
  自动升级为 ENU/米制，必须有显式 Sim3 对齐证据

输出: registration.json (recon_schema.RegistrationResult)

用法:
    python -m pipeline.registration --photos photos --out recon/registration.json
"""
import argparse
import functools
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger

from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraIntrinsics,
    CameraPose,
    CaptureSession,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    GeoAlignment,
    GeoAnchor,
    Handedness,
    MetricStatus,
    RegistrationResult,
    gps_to_enu,
)

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

# mock 布局参数
SESSION_GRID_SPACING = 80.0   # 无 GPS 会话间隔 (米)
ORBIT_RADIUS_VIDEO = 25.0     # 视频环拍半径
ORBIT_RADIUS_PHOTO = 20.0     # 照片环拍半径


_COLMAP_CAMERA_PARAM_NAMES = {
    "SIMPLE_PINHOLE": ("f", "cx", "cy"),
    "PINHOLE": ("fx", "fy", "cx", "cy"),
    "SIMPLE_RADIAL": ("f", "cx", "cy", "k"),
    "RADIAL": ("f", "cx", "cy", "k1", "k2"),
    "OPENCV": ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2"),
}


@dataclass(frozen=True)
class ColmapCamera:
    """A validated ``cameras.txt`` record and its pinhole projection subset."""

    camera_id: int
    model: str
    width: int
    height: int
    params: tuple[float, ...]
    intrinsics: CameraIntrinsics
    distortion_parameters: dict[str, float]

    @property
    def pinhole_intrinsics_lossless(self) -> bool:
        return not self.distortion_parameters

    def evidence_payload(self) -> dict[str, object]:
        """Return the original COLMAP calibration in a JSON-serializable form."""
        return {
            "camera_id": self.camera_id,
            "distortion_parameters": self.distortion_parameters,
            "height": self.height,
            "model": self.model,
            "params": list(self.params),
            "pinhole_intrinsics_lossless": self.pinhole_intrinsics_lossless,
            "width": self.width,
        }


def parse_colmap_cameras_txt(text: str) -> dict[int, ColmapCamera]:
    """Parse common COLMAP camera models without inventing a field of view.

    ``CameraIntrinsics`` can represent the pinhole fields only.  Any distortion
    coefficients remain attached to :class:`ColmapCamera` for machine-auditable
    evidence in the registration result.
    """
    cameras: dict[int, ColmapCamera] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            raise ValueError(f"cameras.txt 第 {line_number} 行格式无效: {raw_line}")
        camera_id = int(parts[0])
        model = parts[1].upper()
        if model not in _COLMAP_CAMERA_PARAM_NAMES:
            raise ValueError(f"不支持的 COLMAP camera model: {model}")
        width, height = int(parts[2]), int(parts[3])
        params = tuple(float(value) for value in parts[4:])
        param_names = _COLMAP_CAMERA_PARAM_NAMES[model]
        if len(params) != len(param_names):
            raise ValueError(
                f"COLMAP {model} 需要 {len(param_names)} 个参数, 实际 {len(params)}"
            )
        if not np.all(np.isfinite(params)):
            raise ValueError(f"COLMAP CAMERA_ID={camera_id} 参数必须有限")
        if camera_id in cameras:
            raise ValueError(f"cameras.txt CAMERA_ID={camera_id} 重复")

        values = dict(zip(param_names, params, strict=True))
        fx = values.get("fx", values.get("f"))
        fy = values.get("fy", values.get("f"))
        intrinsics = CameraIntrinsics(
            width=width,
            height=height,
            fx=fx,
            fy=fy,
            cx=values["cx"],
            cy=values["cy"],
        )
        cameras[camera_id] = ColmapCamera(
            camera_id=camera_id,
            model=model,
            width=width,
            height=height,
            params=params,
            intrinsics=intrinsics,
            distortion_parameters={
                name: values[name]
                for name in param_names
                if name not in {"f", "fx", "fy", "cx", "cy"}
            },
        )
    return cameras


# ============ 工具 ============
def _rotmat_to_quat_wxyz(rotation: np.ndarray) -> list[float]:
    """旋转矩阵 → 单位四元数 wxyz (Shepperd 法, 数值稳定)"""
    tr = np.trace(rotation)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (rotation[2, 1] - rotation[1, 2]) / s
        y = (rotation[0, 2] - rotation[2, 0]) / s
        z = (rotation[1, 0] - rotation[0, 1]) / s
    elif rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        s = np.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2
        w = (rotation[2, 1] - rotation[1, 2]) / s
        x = 0.25 * s
        y = (rotation[0, 1] + rotation[1, 0]) / s
        z = (rotation[0, 2] + rotation[2, 0]) / s
    elif rotation[1, 1] > rotation[2, 2]:
        s = np.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2
        w = (rotation[0, 2] - rotation[2, 0]) / s
        x = (rotation[0, 1] + rotation[1, 0]) / s
        y = 0.25 * s
        z = (rotation[1, 2] + rotation[2, 1]) / s
    else:
        s = np.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2
        w = (rotation[1, 0] - rotation[0, 1]) / s
        x = (rotation[0, 2] + rotation[2, 0]) / s
        y = (rotation[1, 2] + rotation[2, 1]) / s
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
    """确定 mock frame 中的会话锚点: GPS → ENU, 无 GPS → 确定性网格。"""
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
    """确定性 mock 配准: 每个会话绕锚点环拍，输出 synthetic metric 位姿。

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
            rotation = _look_at_c2w(eye, look_target)

            w, h = _image_size(photos_dir / img)
            poses.append(CameraPose(
                image=img,
                session_id=sess.session_id,
                quat_wxyz=_rotmat_to_quat_wxyz(rotation),
                t_xyz=eye.tolist(),
                intrinsics=CameraIntrinsics.from_fov(w, h, 60.0),
            ))

    if origin is None:
        pose_frame = CoordinateFrame(
            frame_id="mock-local",
            handedness=Handedness.RIGHT,
            axes=AxisConvention.LOCAL_Z_UP,
            units=CoordinateUnits.METERS,
            metric_status=MetricStatus.METRIC,
            geo_aligned=GeoAlignment.UNALIGNED,
            provenance=FrameProvenance.SYNTHETIC,
            evidence=[
                "configured-distances-in-meters",
                "deterministic-synthetic-session-layout",
            ],
        )
    else:
        pose_frame = CoordinateFrame(
            frame_id="mock-enu",
            handedness=Handedness.RIGHT,
            axes=AxisConvention.ENU_Z_UP,
            units=CoordinateUnits.METERS,
            metric_status=MetricStatus.METRIC,
            geo_aligned=GeoAlignment.ALIGNED,
            provenance=FrameProvenance.SYNTHETIC,
            evidence=[
                "gps-origin",
                "configured-distances-in-meters",
                "deterministic-synthetic-camera-layout",
            ],
        )

    return RegistrationResult(
        schema_version=2,
        engine="mock",
        pose_frame=pose_frame,
        alignment_status=AlignmentStatus.SYNTHETIC,
        geo_origin=origin,
        sessions=sessions,
        poses=poses,
    )


# ============ COLMAP 联合配准 ============
@dataclass(frozen=True)
class ColmapImageRecord:
    """A registered image with its source camera identity preserved."""

    image_id: int
    camera_id: int
    name: str
    quat_wxyz_c2w: np.ndarray
    t_xyz_c2w: np.ndarray


def parse_colmap_image_records(text: str) -> dict[str, ColmapImageRecord]:
    """Parse ``images.txt`` while retaining each record's ``CAMERA_ID``.

    COLMAP 存 world-to-camera (qvec, tvec); 转 c2w: R_c2w = R^T, t_c2w = -R^T t
    """
    out: dict[str, ColmapImageRecord] = {}
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
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
            raise ValueError(f"images.txt 位姿行格式无效: {line}")
        image_id = int(parts[0])
        qw, qx, qy, qz = map(float, parts[1:5])
        tx, ty, tz = map(float, parts[5:8])
        camera_id = int(parts[8])
        name = " ".join(parts[9:])
        # w2c 四元数 → 旋转矩阵
        q = np.array([qw, qx, qy, qz])
        norm = np.linalg.norm(q)
        if not np.all(np.isfinite(q)) or not np.isfinite(norm) or norm < 1e-8:
            raise ValueError(f"images.txt IMAGE_ID={image_id} 四元数无效")
        q = q / norm
        w, x, y, z = q
        rotation_w2c = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ])
        rotation_c2w = rotation_w2c.T
        t_c2w = -rotation_c2w @ np.array([tx, ty, tz])
        if not np.all(np.isfinite(t_c2w)):
            raise ValueError(f"images.txt IMAGE_ID={image_id} 平移无效")
        if name in out:
            raise ValueError(f"images.txt 图像名重复: {name}")
        out[name] = ColmapImageRecord(
            image_id=image_id,
            camera_id=camera_id,
            name=name,
            quat_wxyz_c2w=np.array(_rotmat_to_quat_wxyz(rotation_c2w)),
            t_xyz_c2w=t_c2w,
        )
    return out


def parse_colmap_images_txt(text: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Compatibility view of poses parsed from COLMAP ``images.txt``."""
    return {
        name: (record.quat_wxyz_c2w, record.t_xyz_c2w)
        for name, record in parse_colmap_image_records(text).items()
    }


def colmap_available() -> bool:
    return shutil.which("colmap") is not None


@functools.lru_cache(maxsize=1)
def _colmap_sift_group() -> str:
    """COLMAP use_gpu 选项组前缀：'Feature'(现行) 或 'Sift'(旧版)。

    COLMAP 把 ``--SiftExtraction/--SiftMatching`` 改名为
    ``--FeatureExtraction/--FeatureMatching``；不同 build 命名不同（实测 4.1.0
    的 nocuda 版已是 Feature*）。探测已安装 build 的帮助文本，两种命名都适配，
    避免 'unrecognised option' 直接失败。
    """
    try:
        out = subprocess.run(["colmap", "feature_extractor", "-h"],
                             capture_output=True, text=True, timeout=30)
        text = (out.stdout or "") + (out.stderr or "")
        if "SiftExtraction.use_gpu" in text and "FeatureExtraction.use_gpu" not in text:
            return "Sift"
    except (OSError, subprocess.SubprocessError):
        pass
    return "Feature"


def colmap_register(photos_dir: str | Path, workspace: str | Path,
                    sessions: list[CaptureSession] | None = None,
                    use_gpu: bool = False,
                    stage_timeout_s: float | None = 3600.0) -> RegistrationResult:
    """COLMAP 联合 SfM: 所有照片 + 视频帧进入同一个 SfM-local frame。

    要求系统安装 colmap。SfM 尺度与地理方向均不确定；GPS 锚点仅作为后续
    显式 Sim3 对齐的候选证据，本函数不会据此猜测米制世界 frame。

    ``use_gpu`` 默认 False：SIFT 走 CPU，在任意机器上可靠（尤其无 NVIDIA/CUDA
    或 headless 时——GPU SIFT 走 OpenGL，集显/后台易失败）。有可用 GPU 时可
    显式开启以提速。仅稀疏 SfM（本函数唯一用到的阶段）是 CPU 可行的；dense/MVS
    需 CUDA，本函数从不调用。

    ``stage_timeout_s`` 默认 3600（每阶段 1 小时，对文档化的开发/canary 小场景
    极宽松）：给 feature_extractor / matcher / mapper / model_converter 每个子进程
    加上界，避免 colmap 卡死（headless/集显 OpenGL SIFT 停滞、matcher 病态输入、
    I/O 挂起）时管线永久 hang 且不抛错。超时按 fail-closed 抛 ``RuntimeError``
    （与非零返回码同构）。大规模云端跑可调大；``None`` 显式关闭上界（不推荐）。
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
        try:
            proc = subprocess.run(["colmap", *args], capture_output=True,
                                  text=True, timeout=stage_timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"colmap {args[0]} 超时 (>{stage_timeout_s}s): 子进程无进展 "
                f"(headless/集显 OpenGL SIFT 停滞、matcher 病态输入或 I/O 挂起)。"
                f"排查后可加大 stage_timeout_s 或改用 --engine mock") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"colmap {args[0]} 失败 (exit {proc.returncode}):\n"
                f"{proc.stderr[-2000:] if proc.stderr else proc.stdout[-2000:]}")

    grp = _colmap_sift_group()  # 'Feature'(现行) / 'Sift'(旧版) — 两种 COLMAP 命名都适配
    gpu_flag = "1" if use_gpu else "0"
    run(["feature_extractor", "--database_path", str(db),
         "--image_path", str(photos_dir),
         "--ImageReader.camera_model", "SIMPLE_RADIAL",
         f"--{grp}Extraction.use_gpu", gpu_flag])
    # 图少时穷举匹配最稳; 图多时顺序匹配利用视频帧时间连续性
    n_images = sum(len(s.images) for s in sessions)
    matcher = "exhaustive_matcher" if n_images <= 400 else "sequential_matcher"
    run([matcher, "--database_path", str(db),
         f"--{grp}Matching.use_gpu", gpu_flag])
    run(["mapper", "--database_path", str(db),
         "--image_path", str(photos_dir), "--output_path", str(sparse)])
    if not (sparse / "0").exists():
        raise RuntimeError(
            f"COLMAP mapper 未产出模型 ({sparse}/0 不存在): "
            f"图像间特征匹配不足, 无法联合配准。"
            f"建议: 增加视角重叠 / 提高抽帧率 / 或改用 --engine mock")
    run(["model_converter", "--input_path", str(sparse / "0"),
         "--output_path", str(sparse / "0"), "--output_type", "TXT"])

    model_dir = sparse / "0"
    images_txt = (model_dir / "images.txt").read_text(encoding="utf-8")
    cameras_txt = (model_dir / "cameras.txt").read_text(encoding="utf-8")
    image_records = parse_colmap_image_records(images_txt)
    cameras = parse_colmap_cameras_txt(cameras_txt)

    img_to_session = {img: s.session_id for s in sessions for img in s.images}
    poses = []
    used_camera_ids: set[int] = set()
    for name, record in sorted(image_records.items()):
        if name not in img_to_session:
            continue
        camera = cameras.get(record.camera_id)
        if camera is None:
            raise ValueError(
                f"COLMAP 图像 {name} 引用 CAMERA_ID={record.camera_id}, "
                "但 cameras.txt 中不存在该相机"
            )
        used_camera_ids.add(record.camera_id)
        poses.append(CameraPose(
            image=name, session_id=img_to_session[name],
            quat_wxyz=record.quat_wxyz_c2w.tolist(),
            t_xyz=record.t_xyz_c2w.tolist(),
            intrinsics=camera.intrinsics.model_copy(deep=True),
            camera_id=camera.camera_id,
            camera_model=camera.model,
            camera_params=camera.params,
        ))
    logger.info(f"COLMAP 配准: {len(poses)}/{n_images} 张图注册成功 (联合模型, 坐标系一致)")

    registered_images = {pose.image for pose in poses}
    all_input_images = sorted(img_to_session)
    coverage = {
        "complete": len(registered_images) == n_images,
        "registered_images": len(registered_images),
        "sessions": {
            session.session_id: {
                "registered": sum(image in registered_images for image in session.images),
                "total": len(session.images),
                "unregistered_images": sorted(
                    image for image in session.images if image not in registered_images
                ),
            }
            for session in sessions
        },
        "total_input_images": n_images,
        "unregistered_images": [
            image for image in all_input_images if image not in registered_images
        ],
    }
    if not coverage["complete"]:
        logger.warning(
            f"COLMAP 仅完成部分配准: {len(registered_images)}/{n_images}; "
            "完整覆盖证据已写入 registration.json"
        )

    origin = next((s.geo_anchor for s in sessions if s.geo_anchor), None)
    camera_evidence = [
        "colmap.camera.v1="
        + json.dumps(
            cameras[camera_id].evidence_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        for camera_id in sorted(used_camera_ids)
    ]
    coverage_evidence = "colmap.registration.coverage.v1=" + json.dumps(
        coverage,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    pose_frame = CoordinateFrame(
        frame_id="sfm-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SFM,
        evidence=[
            "colmap-joint-model",
            "colmap-intrinsics-source:cameras.txt",
            "no-sim3-alignment-evidence",
            coverage_evidence,
            *camera_evidence,
        ],
    )
    return RegistrationResult(
        schema_version=2,
        engine="colmap",
        pose_frame=pose_frame,
        alignment_status=AlignmentStatus.UNALIGNED,
        geo_origin=origin,
        sessions=sessions,
        poses=poses,
    )


# ============ 统一入口 ============
def register(photos_dir: str | Path, out_json: str | Path | None = None,
             engine: str = "auto",
             workspace: str | Path = "recon/colmap_ws",
             colmap_use_gpu: bool = False) -> RegistrationResult:
    """配准入口: engine = auto | colmap | mock (colmap_use_gpu 默认 CPU, 可靠优先)"""
    if engine == "auto":
        engine = "colmap" if colmap_available() else "mock"
        logger.info(f"配准引擎自动选择: {engine}")

    if engine == "colmap":
        result = colmap_register(photos_dir, workspace, use_gpu=colmap_use_gpu)
    elif engine == "mock":
        result = mock_register(photos_dir)
    else:
        raise ValueError(f"未知引擎: {engine}")

    if out_json:
        out_json = Path(out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        # LF (not Windows CRLF): registration.json is a coordinate trust root;
        # byte-reproducibility keeps its digest stable across OSes. Consumers
        # read it via json.loads, so line endings are semantically neutral.
        out_json.write_text(result.model_dump_json(indent=2) + "\n",
                            encoding="utf-8", newline="\n")
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
