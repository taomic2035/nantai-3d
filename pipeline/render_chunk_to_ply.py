"""
L4 Layout → 合成 3DGS ply 渲染器
读取 chunk layout JSON, 输出可在 Web 端渲染的 ply 文件

特性:
- 建筑: 盒子形高斯聚簇 (棕色墙 + 红色屋顶)
- 道路: 灰色平铺条带
- 植被: 绿色球形聚簇
- 水系: 蓝色平铺条带
- 地面: 稀疏草色基础层
- 跨 chunk 偏移: ply 内坐标已含 world_offset (chunk_x*200, chunk_y*200)
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from loguru import logger
from plyfile import PlyData, PlyElement

from pipeline.schema import ChunkLayout

SIMPLE_DTYPE = [
    ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
    ('r', 'u1'), ('g', 'u1'), ('b', 'u1'),
    ('scale', 'f4'),
]


def _stable_seed(s: str) -> int:
    """跨进程稳定的字符串种子 (内建 hash 有随机盐, 不可用于确定性生成)"""
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


def _scene_to_simple(scene) -> np.ndarray:
    """GaussianScene → simple 结构化数组 (与本渲染器输出同构)"""
    arr = np.zeros(len(scene), dtype=SIMPLE_DTYPE)
    arr['x'], arr['y'], arr['z'] = scene.xyz.T.astype(np.float32)
    rgb_u8 = np.clip(scene.rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    arr['r'], arr['g'], arr['b'] = rgb_u8.T
    arr['scale'] = scene.scale.mean(axis=1).astype(np.float32)
    return arr


# 颜色 (RGB 0-255)
COLOR_GROUND = (90, 130, 60)     # 草绿
COLOR_ROAD_MAIN = (80, 80, 80)  # 深灰
COLOR_ROAD_TRAIL = (140, 110, 80)  # 土黄
COLOR_BUILDING_WALL = (170, 130, 90)  # 木色
COLOR_BUILDING_ROOF = (140, 50, 40)   # 红屋顶
COLOR_TREE = (40, 110, 35)       # 深绿
COLOR_WATER = (60, 110, 180)     # 蓝色


def _emit_ground(x_offset: int, y_offset: int, n: int = 4000) -> np.ndarray:
    """地面基础层 (草色稀疏点)"""
    rng = np.random.default_rng(x_offset * 31 + y_offset * 7 + 1)
    pts = np.zeros(n, dtype=[
        ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
        ('r', 'u1'), ('g', 'u1'), ('b', 'u1'),
        ('scale', 'f4'),
    ])
    pts['x'] = x_offset + rng.uniform(0, 200, n)
    pts['y'] = y_offset + rng.uniform(0, 200, n)
    pts['z'] = rng.uniform(0, 0.3, n)  # 贴地
    pts['r'], pts['g'], pts['b'] = COLOR_GROUND
    pts['scale'] = rng.uniform(0.5, 1.2, n)
    return pts


def _emit_road(road, x_offset: int, y_offset: int) -> np.ndarray:
    """道路: 沿 points 连线生成平铺高斯"""
    pts_list = []
    w = road.width
    color = COLOR_ROAD_MAIN if road.type == "main" else COLOR_ROAD_TRAIL
    n_per_seg = 200 if road.type == "main" else 80

    for i in range(len(road.points) - 1):
        p0 = np.array(road.points[i], dtype=float)
        p1 = np.array(road.points[i + 1], dtype=float)
        seg = p1 - p0
        seg_len = np.linalg.norm(seg)
        if seg_len < 0.1:
            continue
        seg_dir = seg / seg_len
        perp = np.array([-seg_dir[1], seg_dir[0]])

        t = np.random.default_rng(_stable_seed(road.id + str(i)))
        u = t.uniform(0, 1, n_per_seg)
        v = t.uniform(-w / 2, w / 2, n_per_seg)
        base = p0[None] + u[:, None] * seg[None]
        offset = v[:, None] * perp[None]
        pos = base + offset

        n = len(pos)
        pts = np.zeros(n, dtype=[
            ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
            ('r', 'u1'), ('g', 'u1'), ('b', 'u1'),
            ('scale', 'f4'),
        ])
        pts['x'] = x_offset + pos[:, 0]
        pts['y'] = y_offset + pos[:, 1]
        pts['z'] = np.random.uniform(0.05, 0.2, n)
        pts['r'], pts['g'], pts['b'] = color
        pts['scale'] = np.random.uniform(0.8, 1.5, n)
        pts_list.append(pts)
    if not pts_list:
        return np.zeros(0, dtype=SIMPLE_DTYPE)
    return np.concatenate(pts_list)


def _emit_building(b, x_offset: int, y_offset: int, registry=None) -> np.ndarray:
    """建筑: 注册表有对应素材时实例化真实/GPT 泼溅, 否则合成盒子墙 + 屋顶"""
    bx, by = b.pos
    bx += x_offset
    by += y_offset

    if registry is not None:
        inst = registry.instantiate(b.asset_id, (bx, by), b.rot_z, b.scale)
        if inst is not None and len(inst) > 0:
            return _scene_to_simple(inst)
    rot = np.radians(b.rot_z)
    s = b.scale
    w = 8.0 * s   # 默认 8m 宽
    d = 6.0 * s   # 默认 6m 深
    h_wall = 3.5 * s
    h_roof = 2.5 * s
    n_wall = 200
    n_roof = 100

    # 生成盒子 8 角点 (4 底 + 4 顶)
    corners = np.array([
        [-w/2, -d/2, 0], [w/2, -d/2, 0],
        [w/2, d/2, 0], [-w/2, d/2, 0],
        [-w/2, -d/2, h_wall], [w/2, -d/2, h_wall],
        [w/2, d/2, h_wall], [-w/2, d/2, h_wall],
    ])
    # 绕 Z 旋转
    R = np.array([[np.cos(rot), -np.sin(rot), 0],
                  [np.sin(rot), np.cos(rot), 0],
                  [0, 0, 1]])
    corners = corners @ R.T
    corners[:, 0] += bx
    corners[:, 1] += by

    # 墙面: 4 面各采样点
    walls = []
    wall_edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
                  (0, 4), (1, 5), (2, 6), (3, 7)]
    for ia, ib in wall_edges:
        p0 = corners[ia]
        p1 = corners[ib]
        t = np.random.default_rng(_stable_seed(b.id + str(ia)))
        u = t.uniform(0, 1, n_wall // 4)
        pts = p0[None] + u[:, None] * (p1 - p0)[None]
        # 加点扰动
        pts += np.random.normal(0, 0.05, pts.shape)
        walls.append(pts)
    wall_pts_xyz = np.concatenate(walls)

    wall_dtype = [('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
                  ('r', 'u1'), ('g', 'u1'), ('b', 'u1'), ('scale', 'f4')]
    wall_arr = np.zeros(len(wall_pts_xyz), dtype=wall_dtype)
    wall_arr['x'] = wall_pts_xyz[:, 0]
    wall_arr['y'] = wall_pts_xyz[:, 1]
    wall_arr['z'] = wall_pts_xyz[:, 2]
    wall_arr['r'], wall_arr['g'], wall_arr['b'] = COLOR_BUILDING_WALL
    wall_arr['scale'] = np.random.uniform(0.4, 0.8, len(wall_pts_xyz))

    # 屋顶: 在屋顶 4 角范围内生成倾斜面
    roof_top = np.array(corners[4:8])
    roof_top[:, 2] += h_roof / 2
    t = np.random.default_rng(_stable_seed(b.id + "roof"))
    u = t.uniform(0, 1, n_roof)
    v = t.uniform(0, 1, n_roof)
    roof_pts = (1 - u[:, None]) * roof_top[0][None] + \
               u[:, None] * roof_top[1][None]
    roof_pts = roof_pts + v[:, None] * (roof_top[3][None] - roof_top[0][None])
    roof_pts += np.random.normal(0, 0.1, roof_pts.shape)

    roof_arr = np.zeros(n_roof, dtype=wall_dtype)
    roof_arr['x'] = roof_pts[:, 0]
    roof_arr['y'] = roof_pts[:, 1]
    roof_arr['z'] = roof_pts[:, 2]
    roof_arr['r'], roof_arr['g'], roof_arr['b'] = COLOR_BUILDING_ROOF
    roof_arr['scale'] = np.random.uniform(0.5, 1.0, n_roof)

    return np.concatenate([wall_arr, roof_arr])


def _emit_vegetation(veg, x_offset: int, y_offset: int) -> np.ndarray:
    """植被: 球形聚簇"""
    cx, cy = veg.center
    cx += x_offset
    cy += y_offset
    r = veg.radius
    n = int(50 * veg.density * r)

    t = np.random.default_rng(_stable_seed(veg.id))
    theta = t.uniform(0, 2 * np.pi, n)
    phi = t.uniform(0, np.pi, n)
    rad = r * np.cbrt(t.uniform(0, 1, n))  # 体积均匀分布

    pts = np.zeros(n, dtype=[
        ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
        ('r', 'u1'), ('g', 'u1'), ('b', 'u1'), ('scale', 'f4'),
    ])
    pts['x'] = cx + rad * np.sin(phi) * np.cos(theta)
    pts['y'] = cy + rad * np.sin(phi) * np.sin(theta)
    pts['z'] = 1.5 + rad * np.cos(phi)  # 树高起步 1.5m
    pts['r'], pts['g'], pts['b'] = COLOR_TREE
    pts['scale'] = t.uniform(0.6, 1.4, n)
    return pts


def _emit_water(water, x_offset: int, y_offset: int) -> np.ndarray:
    """水系: 沿 points 生成蓝色平铺"""
    pts_list = []
    w = water.width
    n_per_seg = 300
    for i in range(len(water.points) - 1):
        p0 = np.array(water.points[i], dtype=float)
        p1 = np.array(water.points[i + 1], dtype=float)
        seg = p1 - p0
        seg_len = np.linalg.norm(seg)
        if seg_len < 0.1:
            continue
        seg_dir = seg / seg_len
        perp = np.array([-seg_dir[1], seg_dir[0]])

        t = np.random.default_rng(_stable_seed(water.id + str(i)))
        u = t.uniform(0, 1, n_per_seg)
        v = t.uniform(-w / 2, w / 2, n_per_seg)
        base = p0[None] + u[:, None] * seg[None]
        offset = v[:, None] * perp[None]
        pos = base + offset

        n = len(pos)
        pts = np.zeros(n, dtype=[
            ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
            ('r', 'u1'), ('g', 'u1'), ('b', 'u1'), ('scale', 'f4'),
        ])
        pts['x'] = x_offset + pos[:, 0]
        pts['y'] = y_offset + pos[:, 1]
        pts['z'] = 0.1
        pts['r'], pts['g'], pts['b'] = COLOR_WATER
        pts['scale'] = t.uniform(0.8, 1.5, n)
        pts_list.append(pts)
    if not pts_list:
        return np.zeros(0, dtype=[
            ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
            ('r', 'u1'), ('g', 'u1'), ('b', 'u1'), ('scale', 'f4'),
        ])
    return np.concatenate(pts_list)


def _emit_prop(p, x_offset: int, y_offset: int, registry=None) -> np.ndarray:
    """道具: 注册素材实例化, 无素材时给一个小型棕色聚簇占位"""
    px, py = p.pos
    px += x_offset
    py += y_offset

    if registry is not None:
        inst = registry.instantiate(p.asset_id, (px, py), p.rot_z, 1.0)
        if inst is not None and len(inst) > 0:
            return _scene_to_simple(inst)

    n = 40
    t = np.random.default_rng(_stable_seed(p.id))
    pts = np.zeros(n, dtype=SIMPLE_DTYPE)
    pts['x'] = px + t.normal(0, 0.4, n)
    pts['y'] = py + t.normal(0, 0.4, n)
    pts['z'] = np.abs(t.normal(0.5, 0.3, n))
    pts['r'], pts['g'], pts['b'] = (120, 90, 60)
    pts['scale'] = t.uniform(0.2, 0.5, n)
    return pts


def build_chunk_array(layout: ChunkLayout, registry=None) -> np.ndarray:
    """chunk layout → simple 结构化数组 (含 world offset), 供写盘/LOD 复用"""
    x_offset = layout.chunk_id.x * 200
    y_offset = layout.chunk_id.y * 200

    layers = [_emit_ground(x_offset, y_offset)]

    for road in layout.roads:
        layers.append(_emit_road(road, x_offset, y_offset))
    for b in layout.buildings:
        layers.append(_emit_building(b, x_offset, y_offset, registry=registry))
    for v in layout.vegetation:
        layers.append(_emit_vegetation(v, x_offset, y_offset))
    for w in layout.water:
        layers.append(_emit_water(w, x_offset, y_offset))
    for p in layout.props:
        layers.append(_emit_prop(p, x_offset, y_offset, registry=registry))

    return np.concatenate([l for l in layers if len(l) > 0])


def render_chunk_to_ply(layout: ChunkLayout, out_path: Path, registry=None) -> int:
    """把单个 chunk layout 渲染为 ply (含 world offset)"""
    all_pts = build_chunk_array(layout, registry=registry)
    n = len(all_pts)

    el = PlyElement.describe(all_pts, 'vertex')
    PlyData([el], byte_order='<').write(str(out_path))
    return n


def render_chunkset(
    layouts_dir: str | Path = "layouts",
    output_dir: str | Path = "web/data",
    chunk_range: tuple[int, int, int, int] = (0, 3, 0, 3),
    assets_dir: str | Path | None = "assets",
    lod_levels: dict[int, float] | None = None,
) -> dict:
    """批量渲染 chunkset → ply + manifest.json
    chunk_range: (x_min, x_max, y_min, y_max) (左闭右开)
    assets_dir: 素材注册表目录 (存在 registry.json 时建筑/道具用注册素材实例化)
    lod_levels: 每 chunk 额外导出的低清级别 (默认 {0: 0.08, 1: 0.30}),
                lod2 即全量 ply_file, viewer 按距离选级 → 远粗近清
    """
    layouts_dir = Path(layouts_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    lod_levels = lod_levels if lod_levels is not None else {0: 0.08, 1: 0.30}

    registry = None
    if assets_dir is not None:
        reg_file = Path(assets_dir) / "registry.json"
        if reg_file.exists():
            from pipeline.assets import AssetRegistry
            registry = AssetRegistry(assets_dir)
            logger.info(f"素材注册表启用: {reg_file} "
                        f"({len(registry.doc.assets)} 个素材)")

    x_min, x_max, y_min, y_max = chunk_range
    manifest = {"chunks": [], "chunk_size_m": 200}
    total_pts = 0

    for cx in range(x_min, x_max):
        for cy in range(y_min, y_max):
            layout_file = layouts_dir / f"chunk_{cx}_{cy}.json"
            if not layout_file.exists():
                logger.warning(f"跳过缺失 layout: {layout_file}")
                continue

            layout = ChunkLayout(**json.loads(layout_file.read_text(encoding="utf-8")))
            arr = build_chunk_array(layout, registry=registry)
            n = len(arr)
            ply_path = output_dir / f"chunk_{cx}_{cy}.ply"
            PlyData([PlyElement.describe(arr, 'vertex')],
                    byte_order='<').write(str(ply_path))

            # 低清 LOD: 按 scale (simple 格式的重要性代理) 降序取子集
            lod_files = {2: ply_path.name}
            if lod_levels:
                order = np.argsort(-arr['scale'])
                for level, frac in sorted(lod_levels.items()):
                    k = max(1, int(n * frac))
                    sub = arr[np.sort(order[:k])]
                    lod_name = f"chunk_{cx}_{cy}_lod{level}.ply"
                    PlyData([PlyElement.describe(sub, 'vertex')],
                            byte_order='<').write(str(output_dir / lod_name))
                    lod_files[level] = lod_name

            manifest["chunks"].append({
                "id": f"{cx}_{cy}",
                "x": cx,
                "y": cy,
                "world_offset": [cx * 200, cy * 200],
                "ply_file": ply_path.name,
                "lod": {str(k): v for k, v in sorted(lod_files.items())},
                "point_count": n,
                "building_count": len(layout.buildings),
                "road_count": len(layout.roads),
                "vegetation_count": len(layout.vegetation),
                "water_count": len(layout.water),
            })
            total_pts += n
            logger.info(
                f"chunk ({cx},{cy}) → {ply_path.name}: {n} pts "
                f"({len(layout.buildings)}b/{len(layout.roads)}r/{len(layout.vegetation)}v)"
            )

    manifest["total_chunks"] = len(manifest["chunks"])
    manifest["total_points"] = total_pts
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info(f"manifest.json 已生成: {manifest_path} ({total_pts} 点总计)")
    return manifest


if __name__ == "__main__":
    print("=" * 60)
    print("L4 Chunk Layout → 3DGS ply 渲染器")
    print("=" * 60)
    manifest = render_chunkset(
        layouts_dir="layouts",
        output_dir="web/data",
        chunk_range=(0, 3, 0, 3),  # 3x3 = 9 chunks
    )
    print(f"\n生成 {manifest['total_chunks']} 个 ply, 共 {manifest['total_points']} 个高斯点")
    for c in manifest["chunks"]:
        print(f"  chunk ({c['x']},{c['y']}): {c['point_count']} pts, offset={c['world_offset']}")
