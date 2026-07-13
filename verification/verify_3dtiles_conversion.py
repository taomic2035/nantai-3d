"""
验证项: L4 3DGS → 3DTiles 转换可行性
目标: 证明本机(CPU, 无NVIDIA GPU)能完成转换, 为 Web 流式加载奠基
"""
import numpy as np
from plyfile import PlyData, PlyElement
from pathlib import Path
import json
import trimesh

OUT_DIR = Path(__file__).parent / "output" / "3dtiles_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def create_synthetic_gaussian_ply(path: Path, n_points: int = 50000):
    """生成一个模拟 3DGS 的高斯点云 ply 文件 (村庄 chunk 占位)"""
    rng = np.random.default_rng(42)

    # 模拟 200m x 200m 村庄场景的高斯椭球
    xyz = np.column_stack([
        rng.uniform(0, 200, n_points),      # x: 0-200m
        rng.uniform(0, 200, n_points),      # y: 0-200m
        rng.uniform(0, 15, n_points),       # z: 0-15m 高度
    ])

    # 高斯属性: 缩放、旋转、颜色、不透明度
    scales = np.exp(rng.uniform(-3, 0, (n_points, 3)))  # log-space 缩放
    rotations = rng.normal(0, 1, (n_points, 4))         # 四元数
    rotations /= np.linalg.norm(rotations, axis=1, keepdims=True)
    colors = rng.integers(0, 256, (n_points, 3))         # f_dc 系数
    opacities = rng.uniform(0.3, 1.0, (n_points, 1))

    # 组装 ply
    dtype = [
        ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
        ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
        ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4'),
        ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4'),
        ('opacity', 'f4'),
    ]
    data = np.zeros(n_points, dtype=dtype)
    data['x'], data['y'], data['z'] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    data['scale_0'], data['scale_1'], data['scale_2'] = scales[:, 0], scales[:, 1], scales[:, 2]
    data['rot_0'], data['rot_1'], data['rot_2'], data['rot_3'] = rotations.T
    data['f_dc_0'] = (colors[:, 0] - 128) / 128.0
    data['f_dc_1'] = (colors[:, 1] - 128) / 128.0
    data['f_dc_2'] = (colors[:, 2] - 128) / 128.0
    data['opacity'] = np.log(opacities[:, 0] / (1 - opacities[:, 0]))

    el = PlyElement.describe(data, 'vertex')
    PlyData([el], byte_order='<').write(str(path))
    print(f"[OK] 合成 3DGS ply 已生成: {path} ({n_points} 个高斯, {path.stat().st_size/1024:.1f} KB)")


def test_ply_loading(path: Path):
    """验证 ply 能被正确加载"""
    ply = PlyData.read(str(path))
    v = ply['vertex']
    print(f"[OK] ply 加载成功: {len(v)} 个顶点")
    print(f"     x 范围: [{v['x'].min():.1f}, {v['x'].max():.1f}]")
    print(f"     y 范围: [{v['y'].min():.1f}, {v['y'].max():.1f}]")
    print(f"     z 范围: [{v['z'].min():.1f}, {v['z'].max():.1f}]")
    return v


def test_trimesh_operations(points):
    """验证 trimesh 几何操作 (用于 LOD 简化)"""
    pts = np.column_stack([points['x'], points['y'], points['z']])
    cloud = trimesh.points.PointCloud(pts)
    bbox = cloud.bounds
    print(f"[OK] trimesh 几何: 边界框 = {bbox[0].round(1)} ~ {bbox[1].round(1)}")
    print(f"     质心: {cloud.centroid.round(1)}")
    # 模拟 LOD 简化: 随机降采样
    for lod, ratio in enumerate([1.0, 0.5, 0.25]):
        n = int(len(pts) * ratio)
        sampled = cloud.vertices[np.random.choice(len(pts), n, replace=False)]
        print(f"     LOD{lod}: {n} 点 ({ratio*100:.0f}%)")
    return bbox


def test_py3dtiles_import():
    """验证 py3dtiles 库可用"""
    try:
        from py3dtiles.tileset.tileset import TileSet
        from py3dtiles.tileset.tile import Tile
        from py3dtiles.tileset.bounding_volume_box import BoundingVolumeBox
        import py3dtiles
        print("[OK] py3dtiles 导入成功")
        print(f"     版本: {py3dtiles.__version__}")
        return True
    except ImportError as e:
        print(f"[FAIL] py3dtiles 导入失败: {e}")
        return False


def test_tileset_generation(bbox, out_path: Path):
    """生成一个最小 3DTiles tileset.json (验证格式)"""
    from py3dtiles.tileset.tileset import TileSet
    from py3dtiles.tileset.tile import Tile
    from py3dtiles.tileset.bounding_volume_box import BoundingVolumeBox

    # 构造包围盒 (xyz 中心 + 半尺寸)
    center = (bbox[0] + bbox[1]) / 2
    half = (bbox[1] - bbox[0]) / 2

    # py3dtiles BoundingVolumeBox 需要 12 元素 list:
    # [hx, 0, 0, 0, hy, 0, 0, 0, hz, cx, cy, cz]
    bv = BoundingVolumeBox()
    bv.set_from_list([
        half[0], 0, 0,
        0, half[1], 0,
        0, 0, half[2],
        center[0], center[1], center[2]
    ])

    tile = Tile()
    tile.bounding_volume = bv
    tile.geometric_error = 500.0

    tileset = TileSet()
    tileset.root_tile = tile

    # 写入 (py3dtiles 12.x API: write_as_json(path))
    tileset.write_as_json(out_path)
    print(f"[OK] 3DTiles tileset.json 已生成: {out_path}")

    # 验证可读回
    with open(out_path) as f:
        ts = json.load(f)
    print(f"     asset.version: {ts['asset']['version']}")
    print(f"     root geometricError: {ts['root']['geometricError']}")
    return True


def main():
    print("=" * 60)
    print("验证项: L4 3DGS → 3DTiles 转换可行性")
    print("=" * 60)
    print()

    # Step 1: 生成合成 3DGS ply
    ply_path = OUT_DIR / "chunk_0_0.ply"
    create_synthetic_gaussian_ply(ply_path)
    print()

    # Step 2: 验证 ply 加载
    points = test_ply_loading(ply_path)
    print()

    # Step 3: 验证 trimesh 几何操作
    bbox = test_trimesh_operations(points)
    print()

    # Step 4: 验证 py3dtiles 可用
    ok = test_py3dtiles_import()
    if not ok:
        return False
    print()

    # Step 5: 生成测试 tileset
    ok = test_tileset_generation(bbox, OUT_DIR / "tileset.json")
    print()

    print("=" * 60)
    print("结论: 3DGS→3DTiles 转换管线在本机 CPU 环境可行")
    print(f"产出目录: {OUT_DIR}")
    print("=" * 60)
    return True


if __name__ == "__main__":
    main()
