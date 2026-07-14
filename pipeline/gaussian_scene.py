"""
高斯泼溅场景 (3DGS) 核心操作

能力:
- 读写两种 PLY 格式:
  - simple: x,y,z,r,g,b,scale (本项目 viewer / render_chunk_to_ply 使用)
  - 3dgs:   x,y,z,f_dc_0..2,opacity,scale_0..2,rot_0..3 (标准 3DGS 训练输出)
- Sim3 变换 (缩放/旋转/平移) → 把局部重建对齐到统一世界坐标系
- 场景拼接 merge + 体素去重 (多次重建 / 图视频混合结果无缝合并)
- 区域替换 replace_region (新的更清晰重建覆盖旧区域 → "可变清晰")
- LOD 分级导出 (按重要性裁剪 → 远处粗、近处清)

内部表示 (统一线性域):
- xyz: (N,3) float64 世界坐标米
- rgb: (N,3) float64, [0,1]
- opacity: (N,) float64, [0,1]
- scale: (N,3) float64, 线性米 (3dgs ply 中的 log 域在 IO 时转换)
- rot: (N,4) float64, 单位四元数 wxyz
"""
from pathlib import Path

import numpy as np
from loguru import logger
from plyfile import PlyData, PlyElement

from pipeline.recon_schema import Sim3

# 球谐 0 阶系数 (3DGS 颜色 <-> f_dc 转换)
SH_C0 = 0.28209479177387814

SIMPLE_DTYPE = [
    ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
    ('r', 'u1'), ('g', 'u1'), ('b', 'u1'),
    ('scale', 'f4'),
]


def _quat_multiply(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    """批量四元数乘法 (Hamilton 积), q1: (4,) 或 (N,4), q2: (N,4), 均为 wxyz"""
    q1 = np.atleast_2d(q1)
    w1, x1, y1, z1 = q1[..., 0], q1[..., 1], q1[..., 2], q1[..., 3]
    w2, x2, y2, z2 = q2[..., 0], q2[..., 1], q2[..., 2], q2[..., 3]
    return np.stack([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 + y1 * w2 + z1 * x2 - x1 * z2,
        w1 * z2 + z1 * w2 + x1 * y2 - y1 * x2,
    ], axis=-1)


class GaussianScene:
    """一组 3D 高斯的容器, 所有坐标处于统一世界坐标系"""

    def __init__(self, xyz, rgb, opacity=None, scale=None, rot=None):
        n = len(xyz)
        self.xyz = np.asarray(xyz, dtype=np.float64).reshape(n, 3)
        self.rgb = np.clip(np.asarray(rgb, dtype=np.float64).reshape(n, 3), 0, 1)
        self.opacity = (np.ones(n) if opacity is None
                        else np.clip(np.asarray(opacity, dtype=np.float64).reshape(n), 0, 1))
        if scale is None:
            scale = np.full((n, 3), 0.05)
        scale = np.asarray(scale, dtype=np.float64)
        if scale.ndim == 1:
            scale = np.repeat(scale[:, None], 3, axis=1)
        self.scale = scale.reshape(n, 3)
        if rot is None:
            rot = np.tile([1.0, 0.0, 0.0, 0.0], (n, 1))
        rot = np.asarray(rot, dtype=np.float64).reshape(n, 4)
        norms = np.linalg.norm(rot, axis=1, keepdims=True)
        norms[norms < 1e-12] = 1.0
        self.rot = rot / norms

    def __len__(self) -> int:
        return len(self.xyz)

    # ============ IO ============
    @classmethod
    def load_ply(cls, path: str | Path) -> "GaussianScene":
        """加载 ply, 自动识别 simple / 3dgs 格式"""
        ply = PlyData.read(str(path))
        v = ply['vertex'].data
        names = v.dtype.names

        xyz = np.stack([v['x'], v['y'], v['z']], axis=1).astype(np.float64)
        n = len(xyz)

        if 'f_dc_0' in names:  # 标准 3DGS 格式
            f_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1).astype(np.float64)
            rgb = np.clip(f_dc * SH_C0 + 0.5, 0, 1)
            if 'opacity' in names:
                opacity = 1.0 / (1.0 + np.exp(-v['opacity'].astype(np.float64)))
            else:
                opacity = np.ones(n)
            if 'scale_0' in names:
                scale = np.exp(np.stack(
                    [v['scale_0'], v['scale_1'], v['scale_2']], axis=1).astype(np.float64))
            else:
                scale = np.full((n, 3), 0.05)
            if 'rot_0' in names:
                rot = np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']],
                               axis=1).astype(np.float64)
            else:
                rot = None
            return cls(xyz, rgb, opacity, scale, rot)

        if 'r' in names:  # simple 格式 (本项目合成 ply)
            rgb = np.stack([v['r'], v['g'], v['b']], axis=1).astype(np.float64) / 255.0
            scale = (v['scale'].astype(np.float64) if 'scale' in names
                     else np.full(n, 0.05))
            return cls(xyz, rgb, None, scale, None)

        raise ValueError(f"无法识别的 ply 顶点属性: {names}")

    def save_ply(self, path: str | Path, flavor: str = "simple") -> None:
        """保存 ply
        flavor="simple": 兼容现有 Web viewer (颜色量化 u8, scale 取三轴均值)
        flavor="3dgs":   标准 3DGS 属性 (f_dc / logit opacity / log scale / quat)
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        n = len(self)

        if flavor == "simple":
            arr = np.zeros(n, dtype=SIMPLE_DTYPE)
            arr['x'], arr['y'], arr['z'] = self.xyz.T.astype(np.float32)
            rgb_u8 = np.clip(self.rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
            arr['r'], arr['g'], arr['b'] = rgb_u8.T
            arr['scale'] = self.scale.mean(axis=1).astype(np.float32)
        elif flavor == "3dgs":
            props = [('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
                     ('nx', 'f4'), ('ny', 'f4'), ('nz', 'f4'),
                     ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4'),
                     ('opacity', 'f4'),
                     ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
                     ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4')]
            arr = np.zeros(n, dtype=props)
            arr['x'], arr['y'], arr['z'] = self.xyz.T.astype(np.float32)
            f_dc = (self.rgb - 0.5) / SH_C0
            arr['f_dc_0'], arr['f_dc_1'], arr['f_dc_2'] = f_dc.T.astype(np.float32)
            op = np.clip(self.opacity, 1e-6, 1 - 1e-6)
            arr['opacity'] = np.log(op / (1 - op)).astype(np.float32)
            log_scale = np.log(np.clip(self.scale, 1e-9, None))
            arr['scale_0'], arr['scale_1'], arr['scale_2'] = log_scale.T.astype(np.float32)
            arr['rot_0'], arr['rot_1'], arr['rot_2'], arr['rot_3'] = \
                self.rot.T.astype(np.float32)
        else:
            raise ValueError(f"未知 flavor: {flavor}")

        el = PlyElement.describe(arr, 'vertex')
        PlyData([el], byte_order='<').write(str(path))

    # ============ 几何操作 ============
    def transform(self, sim3: Sim3) -> "GaussianScene":
        """应用相似变换 (原地), 返回 self 以便链式调用"""
        R = sim3.rotation_matrix()
        self.xyz = sim3.scale * (self.xyz @ R.T) + np.array(sim3.t_xyz)
        self.scale = self.scale * sim3.scale
        q = np.array(sim3.quat_wxyz, dtype=np.float64)
        q = q / np.linalg.norm(q)
        self.rot = _quat_multiply(q, self.rot)
        norms = np.linalg.norm(self.rot, axis=1, keepdims=True)
        norms[norms < 1e-12] = 1.0
        self.rot = self.rot / norms
        return self

    @classmethod
    def merge(cls, scenes: list["GaussianScene"],
              dedup_voxel: float = 0.0) -> "GaussianScene":
        """拼接多个场景 (均已处于统一世界坐标系)

        dedup_voxel > 0 时按体素去重: 重叠区域保留重要性最高的高斯,
        用于多次采集 / 图+视频混合重建的重叠消除。
        """
        scenes = [s for s in scenes if len(s) > 0]
        if not scenes:
            return cls(np.zeros((0, 3)), np.zeros((0, 3)))
        merged = cls(
            np.concatenate([s.xyz for s in scenes]),
            np.concatenate([s.rgb for s in scenes]),
            np.concatenate([s.opacity for s in scenes]),
            np.concatenate([s.scale for s in scenes]),
            np.concatenate([s.rot for s in scenes]),
        )
        if dedup_voxel > 0:
            merged = merged.deduplicate(dedup_voxel)
        return merged

    def deduplicate(self, voxel: float) -> "GaussianScene":
        """体素去重: 每个 voxel 只保留重要性最高的一个高斯"""
        if len(self) == 0:
            return self
        keys = np.floor(self.xyz / voxel).astype(np.int64)
        order = np.argsort(-self.importance())  # 重要性降序, 先到先得
        _, first_idx = np.unique(keys[order], axis=0, return_index=True)
        keep = np.sort(order[first_idx])
        return self._subset(keep)

    def importance(self) -> np.ndarray:
        """重要性评分: 不透明度 x 等效尺寸 (大而不透明的高斯承载轮廓)"""
        return self.opacity * self.scale.mean(axis=1)

    def crop_aabb(self, min_xy, max_xy) -> "GaussianScene":
        """按 XY 包围盒裁剪 (Z 不限), 用于按 chunk 切分"""
        m = ((self.xyz[:, 0] >= min_xy[0]) & (self.xyz[:, 0] < max_xy[0]) &
             (self.xyz[:, 1] >= min_xy[1]) & (self.xyz[:, 1] < max_xy[1]))
        return self._subset(np.where(m)[0])

    def replace_region(self, new: "GaussianScene",
                       margin: float = 0.0) -> "GaussianScene":
        """区域替换: 用更清晰的新重建覆盖旧场景中对应 XY 区域

        旧场景中落在 new 的 XY 包围盒 (外扩 margin) 内的高斯被剔除,
        然后拼入 new → 实现"补拍变清晰"。返回新场景。
        """
        if len(new) == 0:
            return self
        lo = new.xyz[:, :2].min(axis=0) - margin
        hi = new.xyz[:, :2].max(axis=0) + margin
        inside = ((self.xyz[:, 0] >= lo[0]) & (self.xyz[:, 0] < hi[0]) &
                  (self.xyz[:, 1] >= lo[1]) & (self.xyz[:, 1] < hi[1]))
        kept = self._subset(np.where(~inside)[0])
        return GaussianScene.merge([kept, new])

    def _subset(self, idx: np.ndarray) -> "GaussianScene":
        return GaussianScene(self.xyz[idx], self.rgb[idx], self.opacity[idx],
                             self.scale[idx], self.rot[idx])

    # ============ LOD / 可变清晰 ============
    def to_quality(self, fraction: float) -> "GaussianScene":
        """按重要性保留 fraction 比例的高斯 (0 < fraction <= 1)"""
        if fraction >= 1.0 or len(self) == 0:
            return self
        k = max(1, int(len(self) * fraction))
        idx = np.argsort(-self.importance())[:k]
        return self._subset(np.sort(idx))

    def export_lod(self, out_dir: str | Path, stem: str,
                   levels: dict[int, float] | None = None,
                   flavor: str = "simple") -> dict[int, str]:
        """分级导出 LOD ply, 返回 {level: 文件名}
        默认 3 级: 0=8% (远景), 1=30% (中景), 2=100% (近景全清晰度)
        """
        levels = levels or {0: 0.08, 1: 0.30, 2: 1.0}
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for level, frac in sorted(levels.items()):
            sub = self.to_quality(frac)
            fname = f"{stem}_lod{level}.ply"
            sub.save_ply(out_dir / fname, flavor=flavor)
            files[level] = fname
            logger.debug(f"LOD{level} ({frac:.0%}): {len(sub)} 高斯 → {fname}")
        return files

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        if len(self) == 0:
            z = np.zeros(3)
            return z, z
        return self.xyz.min(axis=0), self.xyz.max(axis=0)


if __name__ == "__main__":
    # 自验证: roundtrip + 变换 + 拼接 + LOD
    rng = np.random.default_rng(0)
    s = GaussianScene(rng.uniform(0, 10, (500, 3)), rng.uniform(0, 1, (500, 3)),
                      rng.uniform(0.2, 1, 500), rng.uniform(0.01, 0.5, (500, 3)))
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for flavor in ("simple", "3dgs"):
            p = Path(d) / f"t_{flavor}.ply"
            s.save_ply(p, flavor=flavor)
            s2 = GaussianScene.load_ply(p)
            assert len(s2) == 500
            assert np.allclose(s2.xyz, s.xyz, atol=1e-3), flavor
    moved = GaussianScene(s.xyz.copy(), s.rgb).transform(
        Sim3(scale=2.0, t_xyz=[100, 0, 0]))
    assert np.allclose(moved.xyz, s.xyz * 2 + [100, 0, 0])
    m = GaussianScene.merge([s, moved])
    assert len(m) == 1000
    lo = s.to_quality(0.1)
    assert len(lo) == 50
    print("[OK] gaussian_scene 自验证通过")
