"""
高斯泼溅场景 (3DGS) 核心操作

能力:
- 读写两种 PLY 格式:
  - simple: x,y,z,r,g,b,scale (本项目 viewer / render_chunk_to_ply 使用)
  - 3dgs:   x,y,z,f_dc_0..2,opacity,scale_0..2,rot_0..3 (标准 3DGS 训练输出)
- 带 frame id 的 Sim3 变换 (缩放/旋转/平移) → 显式对齐到目标坐标系
- 场景拼接 merge + 体素去重 (多次重建 / 图视频混合结果无缝合并)
- 区域替换 replace_region (新的更清晰重建覆盖旧区域 → "可变清晰")
- LOD 分级导出 (按重要性裁剪 → 远处粗、近处清)

内部表示 (统一线性域):
- xyz: (N,3) float64, 位于 ``frame_id`` 声明的坐标系；单位由 ``units`` 单独声明
- rgb: (N,3) float64, [0,1]
- opacity: (N,) float64, [0,1]
- scale: (N,3) float64, 线性米 (3dgs ply 中的 log 域在 IO 时转换)
- rot: (N,4) float64, 单位四元数 wxyz
"""
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger
from plyfile import PlyData, PlyElement

from pipeline.recon_schema import Sim3

if TYPE_CHECKING:
    from pipeline.recon_schema import FrameTransform

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
    """一组 3D 高斯及其可验证坐标/属性元数据。

    ``rgb`` 是用于 proxy/调试显示的裁剪颜色；``sh_dc`` 与 ``sh_rest`` 保存原始球谐
    系数，写回标准 3DGS PLY 时绝不从裁剪后的 RGB 反推已加载的系数。
    """

    def __init__(
        self,
        xyz,
        rgb,
        opacity=None,
        scale=None,
        rot=None,
        *,
        sh_dc=None,
        sh_rest=None,
        normals=None,
        extra_properties: dict[str, np.ndarray] | None = None,
        frame_id: str | None = None,
        units: str = "unknown",
        applied_transform_ids: list[str] | tuple[str, ...] | None = None,
    ):
        n = len(xyz)
        self.xyz = np.asarray(xyz, dtype=np.float64).reshape(n, 3)
        self._require_finite("xyz", self.xyz)
        display_rgb = np.asarray(rgb, dtype=np.float64).reshape(n, 3)
        self._require_finite("rgb", display_rgb)
        if sh_dc is None:
            self.sh_dc = (display_rgb - 0.5) / SH_C0
        else:
            self.sh_dc = np.asarray(sh_dc, dtype=np.float64).reshape(n, 3)
            self._require_finite("sh_dc", self.sh_dc)
            display_rgb = self.sh_dc * SH_C0 + 0.5
        self.rgb = np.clip(display_rgb, 0, 1)

        if sh_rest is None:
            self.sh_rest = np.zeros((n, 0), dtype=np.float64)
        else:
            rest = np.asarray(sh_rest, dtype=np.float64)
            if rest.ndim == 2 and rest.shape[0] == n:
                self.sh_rest = rest.copy()
            else:
                self.sh_rest = rest.reshape(n, -1)
        self._require_finite("sh_rest", self.sh_rest)
        if self.sh_rest.shape[1] % 3:
            raise ValueError("f_rest 属性数必须是 RGB 三通道的整数倍")
        n_coeffs = self.sh_rest.shape[1] // 3 + 1
        degree = int(round(np.sqrt(n_coeffs))) - 1
        if (degree + 1) ** 2 != n_coeffs:
            raise ValueError(f"f_rest 属性数不能构成完整 SH degree: {self.sh_rest.shape[1]}")
        self.sh_degree = degree

        self.normals = (np.zeros((n, 3), dtype=np.float64) if normals is None
                        else np.asarray(normals, dtype=np.float64).reshape(n, 3))
        self._require_finite("normals", self.normals)
        self.extra_properties: dict[str, np.ndarray] = {}
        for name, values in (extra_properties or {}).items():
            arr = np.asarray(values)
            if arr.ndim != 1 or len(arr) != n:
                raise ValueError(f"额外 PLY 属性必须是一维且长度为 {n}: {name}")
            if np.issubdtype(arr.dtype, np.number):
                self._require_finite(f"extra property {name}", arr)
            self.extra_properties[name] = arr.copy()

        self.opacity = (np.ones(n) if opacity is None
                        else np.clip(np.asarray(opacity, dtype=np.float64).reshape(n), 0, 1))
        self._require_finite("opacity", self.opacity)
        if scale is None:
            scale = np.full((n, 3), 0.05)
        scale = np.asarray(scale, dtype=np.float64)
        if scale.ndim == 1:
            scale = np.repeat(scale[:, None], 3, axis=1)
        self.scale = scale.reshape(n, 3)
        self._require_finite("scale", self.scale)
        if np.any(self.scale <= 0):
            raise ValueError("scale values must be strictly positive")
        if rot is None:
            rot = np.tile([1.0, 0.0, 0.0, 0.0], (n, 1))
        rot = np.asarray(rot, dtype=np.float64).reshape(n, 4)
        self._require_finite("quaternion", rot)
        norms = np.linalg.norm(rot, axis=1, keepdims=True)
        if np.any(norms < 1e-12):
            raise ValueError("四元数 quaternion norm must be non-zero")
        self.rot = rot / norms
        self.frame_id = frame_id
        self.units = units
        self.applied_transform_ids = list(applied_transform_ids or [])

    def __len__(self) -> int:
        return len(self.xyz)

    @staticmethod
    def _require_finite(label: str, values: np.ndarray) -> None:
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{label} values must be finite")

    # ============ IO ============
    @classmethod
    def load_ply(
        cls,
        path: str | Path,
        *,
        require_3dgs: bool = False,
    ) -> "GaussianScene":
        """加载 ply, 自动识别 simple / 3dgs 格式"""
        ply = PlyData.read(str(path))
        v = ply['vertex'].data
        names = v.dtype.names
        if names is None:
            raise ValueError("PLY vertex 没有属性")
        for name in names:
            values = np.asarray(v[name])
            if np.issubdtype(values.dtype, np.number) and not np.all(np.isfinite(values)):
                raise ValueError(f"PLY property {name} values must be finite")

        metadata: dict[str, Any] = {}
        for comment in ply.comments:
            if comment.startswith("nantai_meta="):
                try:
                    metadata = json.loads(comment.split("=", 1)[1])
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError(f"损坏的 nantai_meta PLY comment: {exc}") from exc
                break

        xyz = np.stack([v['x'], v['y'], v['z']], axis=1).astype(np.float64)
        n = len(xyz)
        normals = (np.stack([v['nx'], v['ny'], v['nz']], axis=1).astype(np.float64)
                   if all(name in names for name in ("nx", "ny", "nz"))
                   else np.zeros((n, 3), dtype=np.float64))

        name_set = set(names)
        has_3dgs_coefficients = any(
            name.startswith(("f_dc_", "f_rest_")) for name in names
        )
        if has_3dgs_coefficients:  # 标准 3DGS 格式
            required = {
                "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
                "scale_0", "scale_1", "scale_2",
                "rot_0", "rot_1", "rot_2", "rot_3",
            }
            missing = sorted(required - name_set)
            if missing:
                raise ValueError(
                    "3DGS PLY 缺少 required properties: " + ", ".join(missing)
                )
            f_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1).astype(np.float64)
            rgb = np.clip(f_dc * SH_C0 + 0.5, 0, 1)
            rest_names = sorted(
                (name for name in names if name.startswith("f_rest_")),
                key=lambda name: int(name.rsplit("_", 1)[1]),
            )
            expected_rest_names = [
                f"f_rest_{index}" for index in range(len(rest_names))
            ]
            if rest_names != expected_rest_names:
                raise ValueError(
                    "f_rest properties must use contiguous indices starting at zero"
                )
            sh_rest = (np.stack([v[name] for name in rest_names], axis=1).astype(np.float64)
                       if rest_names else np.zeros((n, 0), dtype=np.float64))
            opacity = 1.0 / (1.0 + np.exp(-v['opacity'].astype(np.float64)))
            scale = np.exp(np.stack(
                [v['scale_0'], v['scale_1'], v['scale_2']], axis=1).astype(np.float64))
            rot = np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']],
                           axis=1).astype(np.float64)
            quat_norms = np.linalg.norm(rot, axis=1)
            if np.any(quat_norms < 1e-12):
                raise ValueError("四元数 quaternion norm must be non-zero")
            if not np.allclose(quat_norms, 1.0, rtol=1e-3, atol=1e-4):
                raise ValueError("四元数 quaternion must be unit length")
            known = {
                "x", "y", "z", "nx", "ny", "nz",
                "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
                "scale_0", "scale_1", "scale_2",
                "rot_0", "rot_1", "rot_2", "rot_3",
                *rest_names,
            }
            extras = {name: np.asarray(v[name]).copy() for name in names if name not in known}
            return cls(
                xyz, rgb, opacity, scale, rot,
                sh_dc=f_dc,
                sh_rest=sh_rest,
                normals=normals,
                extra_properties=extras,
                frame_id=metadata.get("frame_id"),
                units=metadata.get("units", "unknown"),
                applied_transform_ids=metadata.get("applied_transform_ids", []),
            )

        if 'r' in names:  # simple 格式 (本项目合成 ply)
            if require_3dgs:
                raise ValueError(
                    "3DGS import requires a full 3DGS PLY; simple PLY is preview-only"
                )
            rgb = np.stack([v['r'], v['g'], v['b']], axis=1).astype(np.float64) / 255.0
            scale = (v['scale'].astype(np.float64) if 'scale' in names
                     else np.full(n, 0.05))
            known = {
                "x", "y", "z", "nx", "ny", "nz", "r", "g", "b", "scale"
            }
            extras = {name: np.asarray(v[name]).copy() for name in names if name not in known}
            return cls(
                xyz, rgb, None, scale, None,
                normals=normals,
                extra_properties=extras,
                frame_id=metadata.get("frame_id"),
                units=metadata.get("units", "unknown"),
                applied_transform_ids=metadata.get("applied_transform_ids", []),
            )

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
                     ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4')]
            props.extend((f'f_rest_{i}', 'f4') for i in range(self.sh_rest.shape[1]))
            props.extend([('opacity', 'f4'),
                     ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
                     ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4')])
            for name, values in self.extra_properties.items():
                if name in {p[0] for p in props}:
                    raise ValueError(f"额外 PLY 属性与标准字段冲突: {name}")
                props.append((name, np.asarray(values).dtype.str))
            arr = np.zeros(n, dtype=props)
            arr['x'], arr['y'], arr['z'] = self.xyz.T.astype(np.float32)
            arr['nx'], arr['ny'], arr['nz'] = self.normals.T.astype(np.float32)
            arr['f_dc_0'], arr['f_dc_1'], arr['f_dc_2'] = self.sh_dc.T.astype(np.float32)
            for i in range(self.sh_rest.shape[1]):
                arr[f'f_rest_{i}'] = self.sh_rest[:, i].astype(np.float32)
            op = np.clip(self.opacity, 1e-6, 1 - 1e-6)
            arr['opacity'] = np.log(op / (1 - op)).astype(np.float32)
            log_scale = np.log(np.clip(self.scale, 1e-9, None))
            arr['scale_0'], arr['scale_1'], arr['scale_2'] = log_scale.T.astype(np.float32)
            arr['rot_0'], arr['rot_1'], arr['rot_2'], arr['rot_3'] = \
                self.rot.T.astype(np.float32)
            for name, values in self.extra_properties.items():
                arr[name] = values
        else:
            raise ValueError(f"未知 flavor: {flavor}")

        metadata = {
            "schema_version": 1,
            "frame_id": self.frame_id,
            "units": self.units,
            "applied_transform_ids": self.applied_transform_ids,
        }
        comment = "nantai_meta=" + json.dumps(
            metadata, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        el = PlyElement.describe(arr, 'vertex')
        PlyData([el], byte_order='<', comments=[comment]).write(str(path))

    # ============ 几何操作 ============
    def transform(self, sim3: Sim3) -> "GaussianScene":
        """应用未命名的低阶相似变换。

        该入口保留给每次都从磁盘新加载的素材实例化；重建 frame 对齐必须使用
        :meth:`apply_frame_transform`，由 transform id 保证 exactly-once。
        """
        self._validate_safe_rotation(sim3)
        self._apply_sim3(sim3)
        return self

    def _validate_safe_rotation(self, sim3: Sim3) -> None:
        rotation = sim3.rotation_matrix()
        if self.sh_degree > 0 and not np.allclose(rotation, np.eye(3), atol=1e-10):
            raise ValueError(
                "rotation 会改变高阶 SH 球谐基；当前版本未实现可靠 SH rotation，已阻断")

    @staticmethod
    def _require_float32_representable(label: str, values: np.ndarray) -> None:
        if not np.all(np.isfinite(values)):
            raise ValueError(f"transformed {label} values must be finite")
        float32 = np.finfo(np.float32)
        magnitude = np.abs(values)
        overflows = magnitude > float32.max
        underflows = (magnitude > 0) & (magnitude < float32.smallest_subnormal)
        if np.any(overflows | underflows):
            raise ValueError(
                f"transformed {label} values must be representable as float32"
            )

    def _apply_sim3(self, sim3: Sim3) -> None:
        rotation = sim3.rotation_matrix()
        with np.errstate(over="ignore", invalid="ignore"):
            xyz = sim3.scale * (self.xyz @ rotation.T) + np.array(sim3.t_xyz)
            scale = self.scale * sim3.scale
            normals = self.normals @ rotation.T
            q = np.array(sim3.quat_wxyz, dtype=np.float64)
            q = q / np.linalg.norm(q)
            rot = _quat_multiply(q, self.rot)
        norms = np.linalg.norm(rot, axis=1, keepdims=True)
        norms[norms < 1e-12] = 1.0
        rot = rot / norms

        for label, values in (
            ("xyz", xyz),
            ("scale", scale),
            ("normals", normals),
            ("quaternion", rot),
        ):
            self._require_float32_representable(label, values)

        # Commit only after every derived array passes validation.  This keeps
        # geometry and frame/history metadata unchanged on arithmetic failure.
        self.xyz = xyz
        self.scale = scale
        self.normals = normals
        self.rot = rot

    def apply_frame_transform(
        self,
        transform: "FrameTransform",
        *,
        target_units: str | None = None,
    ) -> "GaussianScene":
        """带 frame 与稳定 id 的原子变换；同一 transform 只能应用一次。"""
        transform_id = transform.transform_id
        if transform_id in self.applied_transform_ids:
            raise ValueError(f"transform 已应用，拒绝重复: {transform_id}")
        if self.frame_id != transform.source_frame:
            raise ValueError(
                f"transform source frame 不匹配: scene={self.frame_id}, "
                f"expected={transform.source_frame}")
        self._validate_safe_rotation(transform.sim3)
        self._apply_sim3(transform.sim3)
        self.frame_id = transform.target_frame
        if target_units is not None:
            self.units = getattr(target_units, "value", target_units)
        self.applied_transform_ids.append(transform_id)
        return self

    @classmethod
    def merge(cls, scenes: list["GaussianScene"],
              dedup_voxel: float = 0.0) -> "GaussianScene":
        """拼接多个场景 (必须处于同一显式 frame/units)

        dedup_voxel > 0 时按体素去重: 重叠区域保留重要性最高的高斯,
        用于多次采集 / 图+视频混合重建的重叠消除。
        """
        scenes = [s for s in scenes if len(s) > 0]
        if not scenes:
            return cls(np.zeros((0, 3)), np.zeros((0, 3)))
        first = scenes[0]
        expected_extra = {
            name: (values.dtype.str, values.shape[1:])
            for name, values in first.extra_properties.items()
        }
        for scene in scenes[1:]:
            actual_extra = {
                name: (values.dtype.str, values.shape[1:])
                for name, values in scene.extra_properties.items()
            }
            if (scene.frame_id != first.frame_id or scene.units != first.units):
                raise ValueError(
                    "不能 merge 不同坐标 frame/units 的 GaussianScene: "
                    f"{first.frame_id}/{first.units} vs {scene.frame_id}/{scene.units}")
            if scene.sh_rest.shape[1] != first.sh_rest.shape[1] \
                    or actual_extra != expected_extra:
                raise ValueError("不能 merge 不兼容的 3DGS 属性 schema")
        transform_ids = list(dict.fromkeys(
            tid for scene in scenes for tid in scene.applied_transform_ids))
        merged = cls(
            np.concatenate([s.xyz for s in scenes]),
            np.concatenate([s.rgb for s in scenes]),
            np.concatenate([s.opacity for s in scenes]),
            np.concatenate([s.scale for s in scenes]),
            np.concatenate([s.rot for s in scenes]),
            sh_dc=np.concatenate([s.sh_dc for s in scenes]),
            sh_rest=np.concatenate([s.sh_rest for s in scenes]),
            normals=np.concatenate([s.normals for s in scenes]),
            extra_properties={
                name: np.concatenate([s.extra_properties[name] for s in scenes])
                for name in first.extra_properties
            },
            frame_id=first.frame_id,
            units=first.units,
            applied_transform_ids=transform_ids,
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
        return GaussianScene(
            self.xyz[idx], self.rgb[idx], self.opacity[idx], self.scale[idx], self.rot[idx],
            sh_dc=self.sh_dc[idx],
            sh_rest=self.sh_rest[idx],
            normals=self.normals[idx],
            extra_properties={name: values[idx] for name, values in self.extra_properties.items()},
            frame_id=self.frame_id,
            units=self.units,
            applied_transform_ids=self.applied_transform_ids,
        )

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
