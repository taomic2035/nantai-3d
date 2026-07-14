"""
端到端重建 CLI: 照片 + 视频 → 统一坐标系 → 高斯泼溅 → 拼接 → LOD 导出

流程:
  1. 配准    registration.register (colmap 联合 SfM 或确定性 mock)
  2. 泼溅    每会话生成 3DGS 场景:
             - engine=mock:   由位姿 + 输入图像调色板合成代理泼溅 (本机无 GPU 可跑通全链路)
             - engine=import: 导入外部训练好的 3DGS ply (云端 gsplat/nerfstudio 训练产物),
                              按 session_to_world Sim3 对齐到世界坐标系
  3. 拼接    GaussianScene.merge + 体素去重 (图/视频/多会话重叠区消融)
  4. 变清晰  --base-scene 提供旧场景时, 新重建 replace_region 覆盖对应区域 (补拍增清)
  5. 导出    LOD 三级 ply (可变清晰) + recon_manifest.json → Web viewer 直接加载

用法:
    # 全 mock 链路 (无 GPU / 无 colmap)
    python -m pipeline.reconstruct --photos photos --engine mock

    # 导入云端训练的 3DGS
    python -m pipeline.reconstruct --engine import --splat video_DJI_0001=trained/dji.ply

    # 补拍变清晰: 用新重建覆盖旧场景对应区域
    python -m pipeline.reconstruct --engine mock --base-scene recon/scene_full.ply
"""
import argparse
import json
from pathlib import Path

import numpy as np
from loguru import logger

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import CaptureSession, RegistrationResult
from pipeline.registration import register

DEFAULT_OUT_DIR = "recon"
DEFAULT_WEB_DIR = "web/data/recon"


# ============ mock 泼溅合成 ============
def _sample_palette(photos_dir: Path, images: list[str],
                    max_samples: int = 5) -> np.ndarray:
    """从输入图像抽取调色板 (Nx3, [0,1]) — mock 泼溅颜色与真实素材相关"""
    colors = []
    step = max(1, len(images) // max_samples)
    for img in images[::step][:max_samples]:
        p = photos_dir / img
        try:
            from PIL import Image
            with Image.open(p) as im:
                small = np.asarray(im.convert("RGB").resize((8, 8))) / 255.0
            colors.append(small.reshape(-1, 3))
        except Exception:
            continue
    if not colors:
        return np.array([[0.5, 0.55, 0.4]])
    return np.concatenate(colors)


def synth_session_splat(session: CaptureSession, reg: RegistrationResult,
                        photos_dir: Path) -> GaussianScene:
    """由配准位姿 + 图像调色板合成一个会话的代理泼溅场景

    覆盖度与输入量挂钩: 帧越多 (视频多角度) → 高斯越多、越完整,
    模拟"多角度采集提升重建完整度"的真实行为。
    """
    poses = reg.poses_by_session(session.session_id)
    if not poses:
        return GaussianScene(np.zeros((0, 3)), np.zeros((0, 3)))

    cam_pos = np.array([p.t_xyz for p in poses])
    center = cam_pos.mean(axis=0)
    center[2] = 0.0  # 场景中心落地
    radius = float(np.median(np.linalg.norm(cam_pos[:, :2] - center[:2], axis=1)))
    radius = max(radius, 5.0)

    import hashlib
    seed = int(hashlib.sha1(session.session_id.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    palette = _sample_palette(photos_dir, session.images)

    n_imgs = len(session.images)
    n_ground = min(1500 + 120 * n_imgs, 12000)
    n_struct = min(800 + 60 * n_imgs, 6000)
    n_scatter = min(400 + 30 * n_imgs, 3000)

    def pick_colors(n, jitter=0.05):
        idx = rng.integers(0, len(palette), n)
        return np.clip(palette[idx] + rng.normal(0, jitter, (n, 3)), 0, 1)

    # 地面盘
    ang = rng.uniform(0, 2 * np.pi, n_ground)
    rad = radius * np.sqrt(rng.uniform(0, 1, n_ground))
    ground = np.stack([center[0] + rad * np.cos(ang),
                       center[1] + rad * np.sin(ang),
                       rng.uniform(0, 0.3, n_ground)], axis=1)

    # 中央结构 (盒状聚簇)
    w, d, h = 8.0, 6.0, 5.0
    struct = np.stack([
        center[0] + rng.uniform(-w / 2, w / 2, n_struct),
        center[1] + rng.uniform(-d / 2, d / 2, n_struct),
        rng.uniform(0, h, n_struct)], axis=1)

    # 周边散布簇
    n_clusters = max(2, n_imgs // 10)
    scatter_list = []
    for _ in range(n_clusters):
        c_ang = rng.uniform(0, 2 * np.pi)
        c_rad = rng.uniform(radius * 0.3, radius * 0.9)
        c = center + np.array([c_rad * np.cos(c_ang), c_rad * np.sin(c_ang), 0])
        k = n_scatter // n_clusters
        scatter_list.append(c + rng.normal(0, 1.5, (k, 3)) * [1, 1, 0.8]
                            + [0, 0, 1.5])
    scatter = np.concatenate(scatter_list) if scatter_list else np.zeros((0, 3))
    if len(scatter):
        scatter[:, 2] = np.abs(scatter[:, 2])

    xyz = np.concatenate([ground, struct, scatter])
    n = len(xyz)
    rgb = np.concatenate([pick_colors(n_ground),
                          pick_colors(n_struct),
                          pick_colors(len(scatter))])
    opacity = rng.uniform(0.55, 1.0, n)
    scale = np.exp(rng.normal(np.log(0.12), 0.4, (n, 3)))
    return GaussianScene(xyz, rgb, opacity, scale)


# ============ import 泼溅 ============
def import_session_splats(splat_map: dict[str, str],
                          reg: RegistrationResult) -> list[GaussianScene]:
    """导入外部训练的 3DGS ply, 按 session_to_world 对齐到世界系"""
    scenes = []
    for sid, ply_path in splat_map.items():
        if sid not in {s.session_id for s in reg.sessions}:
            logger.warning(f"--splat 指定了未知会话 {sid}, 跳过")
            continue
        scene = GaussianScene.load_ply(ply_path)
        sim3 = reg.session_to_world.get(sid)
        if sim3 is not None:
            scene.transform(sim3)
        logger.info(f"导入泼溅: {sid} ← {ply_path} ({len(scene)} 高斯)")
        scenes.append(scene)
    return scenes


# ============ 主流程 ============
def reconstruct(photos_dir: str | Path = "photos",
                out_dir: str | Path = DEFAULT_OUT_DIR,
                web_dir: str | Path = DEFAULT_WEB_DIR,
                engine: str = "mock",
                reg_engine: str = "auto",
                splat_map: dict[str, str] | None = None,
                base_scene: str | Path | None = None,
                dedup_voxel: float = 0.10,
                replace_margin: float = 2.0) -> dict:
    """端到端重建, 返回 manifest dict"""
    photos_dir = Path(photos_dir)
    out_dir = Path(out_dir)
    web_dir = Path(web_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 配准 → 统一坐标系
    reg = register(photos_dir, out_dir / "registration.json", engine=reg_engine)

    # 2. 每会话泼溅
    if engine == "import":
        if not splat_map:
            raise ValueError("engine=import 需要 --splat session_id=ply 映射")
        scenes = import_session_splats(splat_map, reg)
    elif engine == "mock":
        scenes = []
        for sess in reg.sessions:
            s = synth_session_splat(sess, reg, photos_dir)
            logger.info(f"mock 泼溅: {sess.session_id} ({sess.kind}, "
                        f"{len(sess.images)} 图) → {len(s)} 高斯")
            scenes.append(s)
    else:
        raise ValueError(f"未知泼溅引擎: {engine}")

    # 3. 拼接 (统一坐标系下直接 merge, 重叠区体素去重)
    merged = GaussianScene.merge(scenes, dedup_voxel=dedup_voxel)
    logger.info(f"拼接完成: {len(scenes)} 个会话场景 → {len(merged)} 高斯 "
                f"(dedup_voxel={dedup_voxel}m)")

    # 4. 可变清晰: 基底场景区域替换 (补拍的新重建覆盖旧区域)
    if base_scene:
        base = GaussianScene.load_ply(base_scene)
        before = len(base)
        merged = base.replace_region(merged, margin=replace_margin)
        logger.info(f"区域替换: 基底 {before} 高斯 + 新重建 → {len(merged)} 高斯")

    # 5. 导出: 全量 3dgs ply + LOD simple ply + manifest
    full_path = out_dir / "scene_full.ply"
    merged.save_ply(full_path, flavor="3dgs")
    lod_files = merged.export_lod(web_dir, "recon", flavor="simple")

    lo, hi = merged.bounds()
    manifest = {
        "schema_version": 1,
        "engine": engine,
        "registration_engine": reg.engine,
        "gaussian_count": len(merged),
        "bounds": {"min": lo.tolist(), "max": hi.tolist()},
        "lod": {str(k): v for k, v in lod_files.items()},
        "full_3dgs": str(full_path),
        "sessions": [
            {"session_id": s.session_id, "kind": s.kind,
             "n_images": len(s.images)} for s in reg.sessions
        ],
    }
    manifest_path = web_dir / "recon_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    logger.info(f"重建完成: {len(merged)} 高斯 | LOD {list(lod_files)} | "
                f"manifest → {manifest_path}")
    return manifest


def _parse_splat_args(pairs: list[str]) -> dict[str, str]:
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--splat 格式应为 session_id=path.ply: {pair}")
        sid, path = pair.split("=", 1)
        out[sid] = path
    return out


def main():
    parser = argparse.ArgumentParser(
        description="端到端重建: 照片+视频 → 统一坐标系 → 高斯泼溅 → LOD",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--photos", default="photos", help="输入图像目录")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help="重建输出目录")
    parser.add_argument("--web", default=DEFAULT_WEB_DIR, help="Web 数据输出目录")
    parser.add_argument("--engine", default="mock", choices=["mock", "import"],
                        help="泼溅引擎 (mock=本机代理, import=导入云端训练 ply)")
    parser.add_argument("--reg-engine", default="auto",
                        choices=["auto", "colmap", "mock"], help="配准引擎")
    parser.add_argument("--splat", action="append", default=[],
                        metavar="SESSION=PLY", help="导入泼溅映射, 可多次")
    parser.add_argument("--base-scene", default=None,
                        help="基底场景 ply (新重建将替换其对应区域 → 变清晰)")
    parser.add_argument("--dedup-voxel", type=float, default=0.10,
                        help="拼接去重体素 (米, 0 关闭)")
    parser.add_argument("--replace-margin", type=float, default=2.0,
                        help="区域替换外扩边距 (米, 配合 --base-scene)")
    args = parser.parse_args()

    manifest = reconstruct(
        photos_dir=args.photos, out_dir=args.out, web_dir=args.web,
        engine=args.engine, reg_engine=args.reg_engine,
        splat_map=_parse_splat_args(args.splat) or None,
        base_scene=args.base_scene, dedup_voxel=args.dedup_voxel,
        replace_margin=args.replace_margin,
    )
    print(f"\n重建完成: {manifest['gaussian_count']} 高斯")
    print(f"  LOD: {manifest['lod']}")
    print(f"  查看: cd web && python -m http.server 8000")


if __name__ == "__main__":
    main()
