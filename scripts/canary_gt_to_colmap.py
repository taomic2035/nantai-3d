#!/usr/bin/env python3
"""合成村庄 canary 渲染 → COLMAP 文本数据集（GT 相机直训 3DGS，绕过 COLMAP SfM）。

消费 codex 的 `nantai.synthetic-village.camera-metadata.v1` 相机契约：读取 renders/
下的 cameras/*.json (measured_c2w_opencv + 内参) 与 depth/*.exr (相机中心欧氏距离,
通道 'V')，产出 Brush/3DGS 训练器可直接吃的 COLMAP 布局:

    <out>/images/*.png + <out>/sparse/0/{cameras,images,points3D}.txt

GT 深度反投影生成带色初始化点云 (--stride 控制密度)。写盘前做三道自校验，
任何坐标约定错误在此 fail-closed，而不是训练完才发现:
  1. c2w 旋转必须刚性 (正交 + det=+1)；
  2. 四元数往返重建必须复原旋转矩阵；
  3. 跨相机深度一致性: A 相机反投影点投到 B 相机，与 B 的 GT 深度中位相对误差 ≤5%。

为什么存在: 无纹理白模渲染 COLMAP 注册率 0%（docs/verification/
2026-07-16-canary-colmap-feasibility.md），但合成数据本就有 GT 位姿——此路线不依赖
纹理。诚实边界: 输出仍走 prepare_import 的 sfm-local 契约（preview-only，非米制声明），
尽管数值上就是米制 Blender 世界系；要 MEASURED 米制需走 pipeline.alignment 控制点路线。

用法:
    python scripts/canary_gt_to_colmap.py <renders目录> <输出目录> [--stride 16]

依赖: numpy, opencv-python (venv 已有) + OpenEXR>=3.2 (读 'V' 通道; pip install OpenEXR)。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

EXPECTED_COORD = "opencv-c2w-right-down-forward-meters"
DEPTH_REL_TOL = 0.05  # 跨相机深度中位相对误差上限


def _require_openexr():
    try:
        import OpenEXR
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时的诚实报错
        raise SystemExit(
            "需要 OpenEXR 绑定读取 canary 深度 (通道 'V', cv2 不识别): pip install OpenEXR"
        ) from exc
    return OpenEXR


def rotmat_to_quat(rot: np.ndarray) -> np.ndarray:
    """R(3x3) → 四元数 [w,x,y,z] (Hamilton, COLMAP 约定)。"""
    t = np.trace(rot)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w = 0.25 * s
        x = (rot[2, 1] - rot[1, 2]) / s
        y = (rot[0, 2] - rot[2, 0]) / s
        z = (rot[1, 0] - rot[0, 1]) / s
    elif rot[0, 0] > rot[1, 1] and rot[0, 0] > rot[2, 2]:
        s = np.sqrt(1.0 + rot[0, 0] - rot[1, 1] - rot[2, 2]) * 2
        w = (rot[2, 1] - rot[1, 2]) / s
        x = 0.25 * s
        y = (rot[0, 1] + rot[1, 0]) / s
        z = (rot[0, 2] + rot[2, 0]) / s
    elif rot[1, 1] > rot[2, 2]:
        s = np.sqrt(1.0 + rot[1, 1] - rot[0, 0] - rot[2, 2]) * 2
        w = (rot[0, 2] - rot[2, 0]) / s
        x = (rot[0, 1] + rot[1, 0]) / s
        y = 0.25 * s
        z = (rot[1, 2] + rot[2, 1]) / s
    else:
        s = np.sqrt(1.0 + rot[2, 2] - rot[0, 0] - rot[1, 1]) * 2
        w = (rot[1, 0] - rot[0, 1]) / s
        x = (rot[0, 2] + rot[2, 0]) / s
        y = (rot[1, 2] + rot[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z])
    return q / np.linalg.norm(q)


def quat_to_rotmat(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def load_depth(path: Path) -> np.ndarray:
    channels = _require_openexr().File(str(path)).channels()
    if "V" not in channels:
        raise SystemExit(f"深度 EXR 无 V 通道: {path} (有: {list(channels)})")
    return np.asarray(channels["V"].pixels, dtype=np.float64)


def backproject(depth: np.ndarray, intr: dict, c2w: np.ndarray, stride: int):
    """欧氏距离深度 → 世界点。返回 (Nx3 world, Nx2 pixel[u,v])。"""
    fx, fy, cx, cy = intr["fx"], intr["fy"], intr["cx"], intr["cy"]
    h, w = depth.shape
    jj, ii = np.meshgrid(np.arange(0, h, stride), np.arange(0, w, stride), indexing="ij")
    rng = depth[jj, ii]
    ok = rng > 0  # depth_invalid_value_m = 0
    jj, ii, rng = jj[ok], ii[ok], rng[ok]
    u, v = ii + 0.5, jj + 0.5  # pixel_center_offset [0.5,0.5], origin top-left
    dirs = np.stack([(u - cx) / fx, (v - cy) / fy, np.ones_like(u)], axis=1)
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    p_cam = dirs * rng[:, None]
    rot, center = c2w[:3, :3], c2w[:3, 3]
    return p_cam @ rot.T + center, np.stack([u, v], axis=1)


def project(pts_w: np.ndarray, intr: dict, c2w: np.ndarray):
    """世界点 → (Nx2 pixel, N 欧氏距离, N z)。OpenCV 约定。"""
    rot, center = c2w[:3, :3], c2w[:3, 3]
    p_cam = (pts_w - center) @ rot
    z = p_cam[:, 2]
    u = intr["fx"] * p_cam[:, 0] / z + intr["cx"]
    v = intr["fy"] * p_cam[:, 1] / z + intr["cy"]
    return np.stack([u, v], axis=1), np.linalg.norm(p_cam, axis=1), z


def check_cross_camera_depth(metas: list[dict], renders: Path, stride: int) -> None:
    """相邻两对相机互投影，GT 深度中位相对误差超限 = 坐标约定有误，拒绝产出。"""
    pairs = [(metas[0], metas[1]), (metas[-1], metas[-2])]
    for a, b in pairs:
        m_a = np.array(a["measured_c2w_opencv"])
        m_b = np.array(b["measured_c2w_opencv"])
        pts, _ = backproject(
            load_depth(renders / "depth" / f"{a['camera_id']}.exr"), a["intrinsics"], m_a, stride
        )
        uv, rng_pred, z = project(pts, b["intrinsics"], m_b)
        depth_b = load_depth(renders / "depth" / f"{b['camera_id']}.exr")
        h, w = depth_b.shape
        inb = (z > 0) & (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
        if inb.sum() < 50:
            print(f"[skip] {a['camera_id']}->{b['camera_id']}: 视野重叠不足 ({inb.sum()} 点)")
            continue
        sampled = depth_b[uv[inb, 1].astype(int), uv[inb, 0].astype(int)]
        valid = sampled > 0
        rel = np.abs(sampled[valid] - rng_pred[inb][valid]) / rng_pred[inb][valid]
        med = float(np.median(rel))
        print(f"[check] {a['camera_id']}->{b['camera_id']}: {int(valid.sum())} 点, "
              f"深度中位相对误差 {med:.4f}")
        if med > DEPTH_REL_TOL:
            raise SystemExit(f"跨相机深度不一致 ({med:.3f} > {DEPTH_REL_TOL}) — 坐标约定可能有误")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="canary GT 相机 → COLMAP 文本数据集 (Brush 可训)")
    ap.add_argument("renders", type=Path, help="canary renders 目录 (含 cameras/ rgb/ depth/)")
    ap.add_argument("out", type=Path, help="输出数据集目录 (建议仓库树外)")
    ap.add_argument("--stride", type=int, default=16, help="深度反投影采样步长 px (默认 16)")
    args = ap.parse_args(argv)

    cam_files = sorted((args.renders / "cameras").glob("*.json"))
    if not cam_files:
        raise SystemExit(f"无相机 JSON: {args.renders / 'cameras'}")
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in cam_files]

    # 校验: 坐标系声明 + R 刚性 + 四元数往返; 内参按值去重 (FOV 55/65/75 三档)
    cam_ids: dict[str, int] = {}
    for m in metas:
        cam_ids.setdefault(json.dumps(m["intrinsics"], sort_keys=True), len(cam_ids) + 1)
        if m.get("coordinate_system") != EXPECTED_COORD:
            raise SystemExit(
                f"坐标系非预期: {m['camera_id']}: {m.get('coordinate_system')} != {EXPECTED_COORD}"
            )
        rot = np.array(m["measured_c2w_opencv"])[:3, :3]
        rigid = np.allclose(rot @ rot.T, np.eye(3), atol=1e-5)
        if not rigid or abs(np.linalg.det(rot) - 1) > 1e-5:
            raise SystemExit(f"R 非刚性: {m['camera_id']}")
        q = rotmat_to_quat(rot.T)
        if not np.allclose(quat_to_rotmat(q), rot.T, atol=1e-6):
            raise SystemExit(f"四元数往返失败: {m['camera_id']}")
    print(f"[ok] {len(metas)} 相机 / {len(cam_ids)} 组内参: R 刚性, 四元数往返通过")

    check_cross_camera_depth(metas, args.renders, args.stride)

    img_dir = args.out / "images"
    sparse = args.out / "sparse" / "0"
    img_dir.mkdir(parents=True, exist_ok=True)
    sparse.mkdir(parents=True, exist_ok=True)

    cam_lines = ["# Camera list: CAMERA_ID MODEL WIDTH HEIGHT PARAMS[]"]
    for key, cam_id in sorted(cam_ids.items(), key=lambda kv: kv[1]):
        k = json.loads(key)
        cam_lines.append(
            f"{cam_id} PINHOLE {k['width_px']} {k['height_px']} "
            f"{k['fx']} {k['fy']} {k['cx']} {k['cy']}"
        )
    (sparse / "cameras.txt").write_text("\n".join(cam_lines) + "\n", encoding="utf-8")

    img_lines = ["# Image list: IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME"]
    pts_lines = ["# 3D point list: POINT3D_ID X Y Z R G B ERROR TRACK[]"]
    pid = 0
    for idx, m in enumerate(metas, start=1):
        cid = m["camera_id"]
        c2w = np.array(m["measured_c2w_opencv"])
        rot, center = c2w[:3, :3], c2w[:3, 3]
        q = rotmat_to_quat(rot.T)
        t = -rot.T @ center
        name = f"{cid}.png"
        shutil.copy2(args.renders / "rgb" / name, img_dir / name)
        cam_id = cam_ids[json.dumps(m["intrinsics"], sort_keys=True)]
        img_lines.append(" ".join(map(str, [idx, *q, *t, cam_id, name])))
        img_lines.append("")  # 空观测行
        depth = load_depth(args.renders / "depth" / f"{cid}.exr")
        rgb = cv2.cvtColor(cv2.imread(str(args.renders / "rgb" / name)), cv2.COLOR_BGR2RGB)
        pts, uvs = backproject(depth, m["intrinsics"], c2w, args.stride)
        cols = rgb[uvs[:, 1].astype(int), uvs[:, 0].astype(int)]
        for p, c in zip(pts, cols, strict=True):
            pid += 1
            pts_lines.append(f"{pid} {p[0]:.4f} {p[1]:.4f} {p[2]:.4f} {c[0]} {c[1]} {c[2]} 0")
    (sparse / "images.txt").write_text("\n".join(img_lines) + "\n", encoding="utf-8")
    (sparse / "points3D.txt").write_text("\n".join(pts_lines) + "\n", encoding="utf-8")
    print(f"[ok] 数据集: {len(metas)} 影像, {pid} 初始化点 → {args.out}")
    print("下一步 (Brush 训练 + 导入):")
    print(f"  third/brush/brush_app.exe {args.out} --total-steps 2000 --max-resolution 1024 "
          f"--export-every 2000 --export-path <out> --export-name trained.ply")
    print("  python scripts/normalize_ply_quats.py <out>/trained.ply")
    print("  python scripts/prepare_import.py <out>/trained.ply --out-dir <out>  # 按其提示导入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
