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
from io import BytesIO
from pathlib import Path

import numpy as np
from loguru import logger
from plyfile import PlyData, PlyElement

from pipeline.schema import ChunkLayout

VEGETATION_POINT_BUDGET = 6000
VEGETATION_MAX_INSTANCES = 12
VEGETATION_AREA_PER_TREE_M2 = 75.0

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


def _record_asset_consumption(
    report: list[dict] | None,
    registry,
    *,
    asset_id: str,
    renderer: str,
    chunk_id: str,
    instances: int,
    point_count: int,
) -> None:
    """Aggregate proof of actual renderer use; registry presence alone is not use."""
    if report is None or instances <= 0:
        return
    entry = registry.doc.assets[asset_id]
    # Never turn a declared digest into consumption evidence.  Report only the
    # digest measured from a payload that still matches the registry now.
    sha256 = registry.verified_sha256(asset_id)
    if sha256 is None:
        return
    key = (asset_id, renderer, chunk_id, entry.version, sha256)
    for row in report:
        row_key = (
            row["asset_id"],
            row["renderer"],
            row["chunk_id"],
            row["version"],
            row["sha256"],
        )
        if row_key == key:
            row["instances"] += instances
            row["point_count"] += point_count
            return
    report.append(
        {
            "asset_id": asset_id,
            "renderer": renderer,
            "chunk_id": chunk_id,
            "instances": instances,
            "point_count": point_count,
            "version": entry.version,
            "sha256": sha256,
        }
    )


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
    # 掩码为非负种子: 负象限 chunk 的 world_offset 为负会让原算术种子变负,
    # numpy SeedSequence 拒绝负数 -> 崩溃。掩码与 mock_layout._rng 惯例一致,
    # 且非负 offset (现有网格 ≤ 800, 值 ≪ 2^32) 掩码后不变 -> 字节零回归。
    rng = np.random.default_rng((x_offset * 31 + y_offset * 7 + 1) & 0xFFFFFFFF)
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
    # 噪声/scale 用 road.id 派生的本地确定性 RNG (不用进程级全局 np.random,
    # 否则同一 chunk 跨渲染发散、并发服务器互相污染)。
    noise = np.random.default_rng(_stable_seed("road-noise:" + road.id))
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
        pts['z'] = noise.uniform(0.05, 0.2, n)
        pts['r'], pts['g'], pts['b'] = color
        pts['scale'] = noise.uniform(0.8, 1.5, n)
        pts_list.append(pts)
    if not pts_list:
        return np.zeros(0, dtype=SIMPLE_DTYPE)
    return np.concatenate(pts_list)


def _emit_building(
    b,
    x_offset: int,
    y_offset: int,
    registry=None,
    consumption: list[dict] | None = None,
    chunk_id: str = "",
) -> np.ndarray:
    """建筑: 注册表有对应素材时实例化真实/GPT 泼溅, 否则合成盒子墙 + 屋顶"""
    bx, by = b.pos
    bx += x_offset
    by += y_offset

    if registry is not None:
        inst = registry.instantiate(b.asset_id, (bx, by), b.rot_z, b.scale)
        if inst is not None and len(inst) > 0:
            arr = _scene_to_simple(inst)
            _record_asset_consumption(
                consumption,
                registry,
                asset_id=b.asset_id,
                renderer="building",
                chunk_id=chunk_id,
                instances=1,
                point_count=len(arr),
            )
            return arr
    # 合成盒子路径: 扰动/scale 用 b.id 派生的本地确定性 RNG (替代进程级全局
    # np.random, 保证跨渲染字节可复现 + 并发服务器不互相污染)。
    noise = np.random.default_rng(_stable_seed("bldg-noise:" + b.id))
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
    rotation = np.array([[np.cos(rot), -np.sin(rot), 0],
                         [np.sin(rot), np.cos(rot), 0],
                         [0, 0, 1]])
    corners = corners @ rotation.T
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
        pts += noise.normal(0, 0.05, pts.shape)
        walls.append(pts)
    wall_pts_xyz = np.concatenate(walls)

    wall_dtype = [('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
                  ('r', 'u1'), ('g', 'u1'), ('b', 'u1'), ('scale', 'f4')]
    wall_arr = np.zeros(len(wall_pts_xyz), dtype=wall_dtype)
    wall_arr['x'] = wall_pts_xyz[:, 0]
    wall_arr['y'] = wall_pts_xyz[:, 1]
    wall_arr['z'] = wall_pts_xyz[:, 2]
    wall_arr['r'], wall_arr['g'], wall_arr['b'] = COLOR_BUILDING_WALL
    wall_arr['scale'] = noise.uniform(0.4, 0.8, len(wall_pts_xyz))

    # 屋顶: 在屋顶 4 角范围内生成倾斜面
    roof_top = np.array(corners[4:8])
    roof_top[:, 2] += h_roof / 2
    t = np.random.default_rng(_stable_seed(b.id + "roof"))
    u = t.uniform(0, 1, n_roof)
    v = t.uniform(0, 1, n_roof)
    roof_pts = (1 - u[:, None]) * roof_top[0][None] + \
               u[:, None] * roof_top[1][None]
    roof_pts = roof_pts + v[:, None] * (roof_top[3][None] - roof_top[0][None])
    roof_pts += noise.normal(0, 0.1, roof_pts.shape)

    roof_arr = np.zeros(n_roof, dtype=wall_dtype)
    roof_arr['x'] = roof_pts[:, 0]
    roof_arr['y'] = roof_pts[:, 1]
    roof_arr['z'] = roof_pts[:, 2]
    roof_arr['r'], roof_arr['g'], roof_arr['b'] = COLOR_BUILDING_ROOF
    roof_arr['scale'] = noise.uniform(0.5, 1.0, n_roof)

    return np.concatenate([wall_arr, roof_arr])


def _emit_proxy_vegetation(veg, x_offset: int, y_offset: int) -> np.ndarray:
    """Missing-asset fallback: deterministic green volume proxy."""
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


def _vegetation_instance_count(radius: float, density: float) -> int:
    if density <= 0:
        return 0
    estimated = int(round(np.pi * radius * radius * density / VEGETATION_AREA_PER_TREE_M2))
    return min(VEGETATION_MAX_INSTANCES, max(1, estimated))


def _emit_vegetation(
    veg,
    x_offset: int,
    y_offset: int,
    registry=None,
    consumption: list[dict] | None = None,
    chunk_id: str = "",
) -> np.ndarray:
    """Instantiate declared tree assets with deterministic placement and a point cap."""
    if registry is None:
        return _emit_proxy_vegetation(veg, x_offset, y_offset)

    available = [asset_id for asset_id in veg.asset_ids if registry.resolve(asset_id)]
    instance_count = _vegetation_instance_count(veg.radius, veg.density)
    if not available or instance_count == 0:
        return _emit_proxy_vegetation(veg, x_offset, y_offset)

    rng = np.random.default_rng(
        _stable_seed(f"{veg.id}:{x_offset}:{y_offset}:asset-vegetation")
    )
    ordered_assets = list(dict.fromkeys(available))
    rng.shuffle(ordered_assets)
    points_per_instance = max(1, VEGETATION_POINT_BUDGET // instance_count)
    cx = float(veg.center[0] + x_offset)
    cy = float(veg.center[1] + y_offset)
    layers: list[np.ndarray] = []

    for index in range(instance_count):
        asset_id = ordered_assets[index % len(ordered_assets)]
        angle = rng.uniform(0.0, 2.0 * np.pi)
        radial = veg.radius * np.sqrt(rng.uniform(0.0, 1.0))
        pos = (cx + radial * np.cos(angle), cy + radial * np.sin(angle))
        rot_z = float(rng.uniform(0.0, 360.0))
        scale = float(rng.uniform(0.85, 1.15))
        inst = registry.instantiate(asset_id, pos, rot_z, scale)
        if inst is None or len(inst) == 0:
            continue
        arr = _scene_to_simple(inst)
        if len(arr) > points_per_instance:
            order = np.argsort(-arr["scale"], kind="stable")[:points_per_instance]
            arr = arr[np.sort(order)]
        layers.append(arr)
        _record_asset_consumption(
            consumption,
            registry,
            asset_id=asset_id,
            renderer="vegetation",
            chunk_id=chunk_id,
            instances=1,
            point_count=len(arr),
        )

    if not layers:
        return _emit_proxy_vegetation(veg, x_offset, y_offset)
    return np.concatenate(layers)


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


def _emit_prop(
    p,
    x_offset: int,
    y_offset: int,
    registry=None,
    consumption: list[dict] | None = None,
    chunk_id: str = "",
) -> np.ndarray:
    """道具: 注册素材实例化, 无素材时给一个小型棕色聚簇占位"""
    px, py = p.pos
    px += x_offset
    py += y_offset

    if registry is not None:
        inst = registry.instantiate(p.asset_id, (px, py), p.rot_z, 1.0)
        if inst is not None and len(inst) > 0:
            arr = _scene_to_simple(inst)
            _record_asset_consumption(
                consumption,
                registry,
                asset_id=p.asset_id,
                renderer="prop",
                chunk_id=chunk_id,
                instances=1,
                point_count=len(arr),
            )
            return arr

    n = 40
    t = np.random.default_rng(_stable_seed(p.id))
    pts = np.zeros(n, dtype=SIMPLE_DTYPE)
    pts['x'] = px + t.normal(0, 0.4, n)
    pts['y'] = py + t.normal(0, 0.4, n)
    pts['z'] = np.abs(t.normal(0.5, 0.3, n))
    pts['r'], pts['g'], pts['b'] = (120, 90, 60)
    pts['scale'] = t.uniform(0.2, 0.5, n)
    return pts


def build_chunk_array(
    layout: ChunkLayout,
    registry=None,
    consumption: list[dict] | None = None,
) -> np.ndarray:
    """chunk layout → simple 结构化数组 (含 world offset), 供写盘/LOD 复用"""
    x_offset = layout.chunk_id.x * 200
    y_offset = layout.chunk_id.y * 200
    chunk_id = f"{layout.chunk_id.x}_{layout.chunk_id.y}"

    layers = [_emit_ground(x_offset, y_offset)]

    for road in layout.roads:
        layers.append(_emit_road(road, x_offset, y_offset))
    for b in layout.buildings:
        layers.append(
            _emit_building(
                b,
                x_offset,
                y_offset,
                registry=registry,
                consumption=consumption,
                chunk_id=chunk_id,
            )
        )
    for v in layout.vegetation:
        layers.append(
            _emit_vegetation(
                v,
                x_offset,
                y_offset,
                registry=registry,
                consumption=consumption,
                chunk_id=chunk_id,
            )
        )
    for w in layout.water:
        layers.append(_emit_water(w, x_offset, y_offset))
    for p in layout.props:
        layers.append(
            _emit_prop(
                p,
                x_offset,
                y_offset,
                registry=registry,
                consumption=consumption,
                chunk_id=chunk_id,
            )
        )

    return np.concatenate([layer for layer in layers if len(layer) > 0])


def render_chunk_to_ply(layout: ChunkLayout, out_path: Path, registry=None) -> int:
    """把单个 chunk layout 渲染为 ply (含 world offset)"""
    all_pts = build_chunk_array(layout, registry=registry)
    n = len(all_pts)

    el = PlyElement.describe(all_pts, 'vertex')
    PlyData([el], byte_order='<').write(str(out_path))
    return n


# LOD 采样比例: lod 0=远景 8%, 1=中景 30%; lod 2/None=全量 100%。
# viewer 按相机距离选级 (远粗近清); render-on-demand 端点据此省带宽。
DEFAULT_LOD_FRACTIONS = {0: 0.08, 1: 0.30}


def _lod_subset(arr: np.ndarray, frac: float) -> np.ndarray:
    """按 scale (重要性代理) 降序取 frac 比例子集。

    kind="stable": 相等 scale 的相对顺序在 numpy 实现/平台间一致, 保证 LOD 子集
    跨进程/平台字节可复现 (默认 quicksort 对等值元素顺序不定 → 跨版本可能漂移)。
    """
    k = max(1, int(len(arr) * frac))
    order = np.argsort(-arr['scale'], kind="stable")
    return arr[np.sort(order[:k])]


def render_single_chunk(
    chunk_x: int,
    chunk_y: int,
    world_seed: int = 42,
    registry=None,
    lod: int | None = None,
) -> bytes:
    """按需渲染单个 chunk → ply 字节 (纯内存, 不落盘)。

    render-on-demand 无限世界的内核: 布局由 (world_seed, chunk_x, chunk_y) 经
    MockLayoutGenerator 完全确定, 渲染确定性由本模块的 per-chunk 本地 RNG 保证,
    ply 无时间戳/无熵 —— 故相同入参跨进程字节一致, 可安全用于内容寻址缓存与
    多实例服务器。任意 (含负) 坐标均可; registry=None 走合成代理 (不触溯源写路径)。
    lod: 0/1 返回 DEFAULT_LOD_FRACTIONS 子集 (远/中景省带宽), 2 或 None 返回全量。
    """
    import numbers

    from pipeline.mock_layout import MockLayoutGenerator

    # fail-closed 类型闸: 非整数/NaN/inf 坐标给出清晰 ValueError, 而非在布局 RNG
    # 深处抛未分类 TypeError。coerce numpy 整数到 python int (路由/调度层常产出
    # numpy 整数, 且 random.Random 只认原生 int)。
    if not (isinstance(chunk_x, numbers.Integral)
            and isinstance(chunk_y, numbers.Integral)):
        raise ValueError(
            f"chunk coordinates must be integers, got ({chunk_x!r}, {chunk_y!r})")
    chunk_x, chunk_y = int(chunk_x), int(chunk_y)

    layout = MockLayoutGenerator(world_seed).generate_chunk(chunk_x, chunk_y)
    arr = build_chunk_array(layout, registry=registry)
    if lod is not None and lod in DEFAULT_LOD_FRACTIONS:
        arr = _lod_subset(arr, DEFAULT_LOD_FRACTIONS[lod])
    buf = BytesIO()
    PlyData([PlyElement.describe(arr, 'vertex')], byte_order='<').write(buf)
    return buf.getvalue()


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
    manifest = {"chunks": [], "chunk_size_m": 200, "asset_consumption": []}
    total_pts = 0
    bmin = [float("inf")] * 3       # 全局 AABB (含真实 z 跨度)
    bmax = [float("-inf")] * 3
    world_seeds: set[int] = set()

    for cx in range(x_min, x_max):
        for cy in range(y_min, y_max):
            layout_file = layouts_dir / f"chunk_{cx}_{cy}.json"
            if not layout_file.exists():
                logger.warning(f"跳过缺失 layout: {layout_file}")
                continue

            layout = ChunkLayout(**json.loads(layout_file.read_text(encoding="utf-8")))
            world_seeds.add(layout.world_seed)
            arr = build_chunk_array(
                layout,
                registry=registry,
                consumption=manifest["asset_consumption"],
            )
            n = len(arr)
            # per-chunk AABB (原生 float, 供 viewer 精确框定/垂直裁剪, 无需下载 ply)
            aabb_min = [float(arr['x'].min()), float(arr['y'].min()), float(arr['z'].min())]
            aabb_max = [float(arr['x'].max()), float(arr['y'].max()), float(arr['z'].max())]
            for i in range(3):
                bmin[i] = min(bmin[i], aabb_min[i])
                bmax[i] = max(bmax[i], aabb_max[i])
            ply_path = output_dir / f"chunk_{cx}_{cy}.ply"
            PlyData([PlyElement.describe(arr, 'vertex')],
                    byte_order='<').write(str(ply_path))

            # 低清 LOD: 按 scale (simple 格式的重要性代理) 降序取子集
            lod_files = {2: ply_path.name}
            if lod_levels:
                for level, frac in sorted(lod_levels.items()):
                    sub = _lod_subset(arr, frac)
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
                "aabb": {"min": aabb_min, "max": aabb_max},
                "building_count": len(layout.buildings),
                "road_count": len(layout.roads),
                "vegetation_count": len(layout.vegetation),
                "water_count": len(layout.water),
                "prop_count": len(layout.props),
            })
            total_pts += n
            logger.info(
                f"chunk ({cx},{cy}) → {ply_path.name}: {n} pts "
                f"({len(layout.buildings)}b/{len(layout.roads)}r/"
                f"{len(layout.vegetation)}v/{len(layout.props)}p)"
            )

    manifest["total_chunks"] = len(manifest["chunks"])
    manifest["total_points"] = total_pts
    manifest["asset_consumption"].sort(
        key=lambda row: (row["chunk_id"], row["renderer"], row["asset_id"])
    )
    # 无限网格元数据: 让 viewer 区分'越界→按需请求'与'真无内容', 用真实 z_range 取景。
    # on_demand 默认 false -> 保持现有静态网格行为不变, 服务端点上线后置 true 才开闸。
    # 单一 world_seed 才写 (混种子 -> None, 诚实, 服务端不应据此重渲不一致几何)。
    if manifest["chunks"]:
        manifest["bounds"] = {"min": bmin, "max": bmax}
        xs = [c["x"] for c in manifest["chunks"]]
        ys = [c["y"] for c in manifest["chunks"]]
        manifest["baked_extent"] = {
            "x_min": min(xs), "x_max": max(xs), "y_min": min(ys), "y_max": max(ys),
        }
    manifest["grid"] = {
        "on_demand": False,
        "url_template": "/api/world/chunk/{x}/{y}.ply",
        "world_seed": next(iter(world_seeds)) if len(world_seeds) == 1 else None,
    }
    manifest_path = output_dir / "manifest.json"
    # newline="\n": 与 trust root (recon_manifest/registration) 惯例统一, 让 world
    # manifest 跨平台字节可复现 (Windows write_text 默认会把 \n 转 \r\n)。
    manifest_path.write_text(
        json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    logger.info(f"manifest.json 已生成: {manifest_path} ({total_pts} 点总计)")
    return manifest


def _cli(argv: list[str] | None = None) -> int:
    """命令行入口: 默认全量烘焙; --single 驱动 render-on-demand 内核 (调试/移交)。

    --single 存在时不落 web/data 数据契约, 只把单块 ply 字节写到 --out ——
    与 HTTP 端点将来调用的同一内核, 字节逐位一致 (内容寻址可复核)。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m pipeline.render_chunk_to_ply",
        description="L4 Chunk Layout → 合成 3DGS ply 渲染器 (全量烘焙 / 按需单块)",
    )
    parser.add_argument(
        "--single", nargs=2, type=int, metavar=("CX", "CY"),
        help="render-on-demand 内核: 只渲染单个 (含负) chunk 坐标, 写 ply 到 --out",
    )
    parser.add_argument(
        "--lod", type=int, choices=sorted(DEFAULT_LOD_FRACTIONS), default=None,
        help=f"仅 --single: LOD 分级 {dict(DEFAULT_LOD_FRACTIONS)}, 缺省=全量",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="world_seed (默认 42, 须与预烘种子区一致)",
    )
    parser.add_argument("--out", type=Path, default=None, help="仅 --single: 输出 ply 路径")
    parser.add_argument("--layouts-dir", default="layouts", help="全量烘焙: layout JSON 目录")
    parser.add_argument("--output-dir", default="web/data", help="全量烘焙: 输出目录")
    parser.add_argument(
        "--range", nargs=4, type=int, default=(0, 3, 0, 3),
        metavar=("X_MIN", "X_MAX", "Y_MIN", "Y_MAX"),
        help="全量烘焙: chunk 半开区间 (默认 0 3 0 3 = 3x3)",
    )
    args = parser.parse_args(argv)

    if args.single is not None:
        cx, cy = args.single
        data = render_single_chunk(cx, cy, world_seed=args.seed, lod=args.lod)
        suffix = f"_lod{args.lod}" if args.lod is not None else ""
        out = args.out or Path(f"chunk_{cx}_{cy}{suffix}.ply")
        out.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        pts = PlyData.read(BytesIO(data))["vertex"].count
        print(f"render_single_chunk({cx},{cy}, seed={args.seed}, lod={args.lod}) "
              f"→ {out} ({len(data)} 字节, {pts} 点)")
        print(f"  sha256={digest}  (相同入参跨进程/平台字节一致 → 可内容寻址缓存)")
        return 0

    print("=" * 60)
    print("L4 Chunk Layout → 3DGS ply 渲染器 (全量烘焙)")
    print("=" * 60)
    manifest = render_chunkset(
        layouts_dir=args.layouts_dir,
        output_dir=args.output_dir,
        chunk_range=tuple(args.range),
    )
    print(f"\n生成 {manifest['total_chunks']} 个 ply, 共 {manifest['total_points']} 个高斯点")
    for c in manifest["chunks"]:
        print(f"  chunk ({c['x']},{c['y']}): {c['point_count']} pts, offset={c['world_offset']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
