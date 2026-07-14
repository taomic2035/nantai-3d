"""确定性村庄 mock 素材生成器 (HANDOFF-001 素材的可移植真相源)。

背景 (P0#8 可移植性): `assets/*.ply` 与 `handoff/deliverables/` 均被 git 忽略,
若只提交 `assets/registry.json`, fresh clone 会得到指向空文件的悬空 registry。
本模块把生成逻辑放进 tracked 位置, 让 `make assets` 能在任意机器上确定性重建
11 个素材 ply 并写回 registry (含 sha256 自校验)。

这些是刻意的程序化 proxy 素材: 用真实的 3DGS ply / 注册表 / chunk 实例化 /
区域替换 / viewer 全链路, 但足够便宜以本地随时重建。真实重建产物用
`AssetRegistry.replace(..., origin="real")` 覆盖即可 (布局 JSON 不变)。

用法:
    python -m pipeline.mock_assets                     # 重建 assets/ 并写 registry.json
    python -m pipeline.mock_assets --out DIR
        # 生成 handoff 交付目录 (*.ply + manifest.json)
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pipeline.assets import AssetEntry, AssetRegistry, RegistryDoc, sha256_file
from pipeline.gaussian_scene import GaussianScene

HANDOFF_ID = "HANDOFF-001"


def _seed(name: str) -> int:
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:16], 16)


@dataclass(frozen=True)
class AssetSpec:
    asset_id: str
    kind: str
    footprint_m: tuple[float, float, float]
    build: Callable[[Builder], None]


class Builder:
    def __init__(self, name: str):
        self.rng = np.random.default_rng(_seed(name))
        self.xyz_parts: list[np.ndarray] = []
        self.rgb_parts: list[np.ndarray] = []
        self.opacity_parts: list[np.ndarray] = []
        self.scale_parts: list[np.ndarray] = []

    def add(
        self,
        points: np.ndarray,
        color: tuple[float, float, float],
        *,
        scale: tuple[float, float] = (0.035, 0.11),
        opacity: tuple[float, float] = (0.72, 0.98),
        noise: float = 0.035,
        weathering: float = 0.08,
    ) -> None:
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if len(points) == 0:
            return
        base = np.asarray(color, dtype=np.float64)
        phase = np.sin(
            points[:, 0] * 1.17
            + points[:, 1] * 0.73
            + points[:, 2] * 0.41
            + self.rng.uniform(0.0, 2.0 * np.pi)
        )
        rgb = base[None, :] * (1.0 + weathering * phase[:, None])
        rgb += self.rng.normal(0.0, noise, (len(points), 3))
        rgb = np.clip(rgb, 0.015, 0.985)

        s = self.rng.uniform(scale[0], scale[1], len(points))
        anisotropy = self.rng.uniform(0.78, 1.22, (len(points), 3))
        scales = s[:, None] * anisotropy

        self.xyz_parts.append(points)
        self.rgb_parts.append(rgb)
        self.opacity_parts.append(self.rng.uniform(*opacity, len(points)))
        self.scale_parts.append(scales)

    def box(
        self,
        center: tuple[float, float, float],
        size: tuple[float, float, float],
        n: int,
        color: tuple[float, float, float],
        **kwargs,
    ) -> None:
        c = np.asarray(center, dtype=np.float64)
        sx, sy, sz = size
        areas = np.array([sy * sz, sy * sz, sx * sz, sx * sz, sx * sy, sx * sy])
        counts = self.rng.multinomial(n, areas / areas.sum())
        pieces: list[np.ndarray] = []
        for face, count in enumerate(counts):
            if count == 0:
                continue
            p = self.rng.uniform(-0.5, 0.5, (count, 3)) * [sx, sy, sz] + c
            axis = face // 2
            sign = -1.0 if face % 2 == 0 else 1.0
            p[:, axis] = c[axis] + sign * size[axis] / 2.0
            pieces.append(p)
        self.add(np.concatenate(pieces), color, **kwargs)

    def tube(
        self,
        p0: tuple[float, float, float] | np.ndarray,
        p1: tuple[float, float, float] | np.ndarray,
        radius: float,
        n: int,
        color: tuple[float, float, float],
        **kwargs,
    ) -> None:
        a = np.asarray(p0, dtype=np.float64)
        b = np.asarray(p1, dtype=np.float64)
        axis = b - a
        length = float(np.linalg.norm(axis))
        if length < 1e-9:
            return
        w = axis / length
        helper = np.array([0.0, 0.0, 1.0]) if abs(w[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        u = np.cross(w, helper)
        u /= np.linalg.norm(u)
        v = np.cross(w, u)
        t = self.rng.uniform(0.0, 1.0, n)
        theta = self.rng.uniform(0.0, 2.0 * np.pi, n)
        r = radius * self.rng.uniform(0.82, 1.08, n)
        points = (
            a
            + t[:, None] * axis
            + r[:, None] * (np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v)
        )
        self.add(points, color, **kwargs)

    def ellipsoid(
        self,
        center: tuple[float, float, float],
        radii: tuple[float, float, float],
        n: int,
        color: tuple[float, float, float],
        *,
        shell_bias: float = 0.45,
        **kwargs,
    ) -> None:
        dirs = self.rng.normal(size=(n, 3))
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
        radial = shell_bias + (1.0 - shell_bias) * self.rng.random(n) ** (1.0 / 3.0)
        points = np.asarray(center) + dirs * radial[:, None] * np.asarray(radii)
        self.add(points, color, **kwargs)

    def cone(
        self,
        center_xy: tuple[float, float],
        z0: float,
        height: float,
        radius: float,
        n: int,
        color: tuple[float, float, float],
        **kwargs,
    ) -> None:
        zf = self.rng.random(n)
        local_r = radius * (1.0 - zf) * self.rng.uniform(0.65, 1.08, n)
        theta = self.rng.uniform(0.0, 2.0 * np.pi, n)
        points = np.column_stack(
            [
                center_xy[0] + local_r * np.cos(theta),
                center_xy[1] + local_r * np.sin(theta),
                z0 + zf * height,
            ]
        )
        self.add(points, color, **kwargs)

    def gable_roof(
        self,
        width: float,
        depth: float,
        eave_z: float,
        ridge_z: float,
        n: int,
        color: tuple[float, float, float],
        *,
        eave: float = 0.45,
        thickness: float = 0.12,
        **kwargs,
    ) -> None:
        half = width / 2.0 + eave
        side = self.rng.choice(np.array([-1.0, 1.0]), size=n)
        run = self.rng.random(n)
        x = side * run * half
        y = self.rng.uniform(-depth / 2.0 - eave, depth / 2.0 + eave, n)
        z = ridge_z - run * (ridge_z - eave_z)
        z += self.rng.normal(0.0, thickness, n)
        self.add(np.column_stack([x, y, z]), color, **kwargs)

    def finish(self) -> GaussianScene:
        xyz = np.concatenate(self.xyz_parts)
        rgb = np.concatenate(self.rgb_parts)
        opacity = np.concatenate(self.opacity_parts)
        scale = np.concatenate(self.scale_parts)
        lo = xyz.min(axis=0)
        hi = xyz.max(axis=0)
        xyz[:, 0] -= (lo[0] + hi[0]) / 2.0
        xyz[:, 1] -= (lo[1] + hi[1]) / 2.0
        xyz[:, 2] -= lo[2]
        return GaussianScene(xyz, rgb, opacity, scale)


WOOD = (0.48, 0.27, 0.13)
DARK_WOOD = (0.20, 0.105, 0.055)
RED_TILE = (0.55, 0.145, 0.075)
GREY_TILE = (0.20, 0.22, 0.22)
STONE = (0.40, 0.44, 0.44)
DARK_STONE = (0.24, 0.27, 0.27)


def _facade_opening(
    b: Builder,
    x: float,
    y: float,
    z: float,
    w: float,
    h: float,
    n: int,
    color: tuple[float, float, float],
) -> None:
    b.box((x, y, z), (w, 0.10, h), n, color, scale=(0.025, 0.065), noise=0.02)


def house_wood_01(b: Builder) -> None:
    b.box((0, 0, 2.0), (8.0, 6.0, 4.0), 6100, WOOD, weathering=0.14)
    b.gable_roof(8.0, 6.0, 3.85, 6.5, 3900, RED_TILE, scale=(0.04, 0.13), weathering=0.12)
    _facade_opening(b, 0, -3.055, 1.25, 1.35, 2.5, 650, DARK_WOOD)
    for x in (-2.45, 2.45):
        _facade_opening(b, x, -3.06, 2.25, 1.25, 1.25, 420, (0.10, 0.17, 0.17))
        b.box((x, -3.13, 2.25), (1.45, 0.07, 0.09), 120, DARK_WOOD)
    b.box((0, -3.15, 0.16), (2.2, 0.8, 0.32), 300, (0.46, 0.42, 0.34))


def house_wood_02(b: Builder) -> None:
    b.box((0, 0, 2.1), (10.0, 7.0, 4.2), 6500, (0.67, 0.61, 0.50), weathering=0.12)
    # Exposed timber frame.
    for x in (-4.4, -2.2, 0.0, 2.2, 4.4):
        b.box((x, -3.56, 2.1), (0.18, 0.14, 4.15), 230, DARK_WOOD)
    b.box((0, -3.58, 3.65), (9.0, 0.15, 0.18), 300, DARK_WOOD)
    b.gable_roof(10.0, 7.0, 4.0, 7.0, 4300, GREY_TILE, scale=(0.04, 0.14))
    # Porch slab, posts and canopy.
    b.box((0, -4.25, 0.18), (6.8, 1.45, 0.36), 700, (0.35, 0.29, 0.22))
    for x in (-3.0, -1.0, 1.0, 3.0):
        b.tube((x, -4.7, 0.35), (x, -4.7, 3.45), 0.13, 300, WOOD)
    b.box((0, -4.65, 3.50), (7.0, 1.15, 0.22), 550, GREY_TILE)
    _facade_opening(b, 0, -3.57, 1.35, 1.5, 2.7, 520, DARK_WOOD)
    for x in (-2.6, 2.6):
        _facade_opening(b, x, -3.58, 2.0, 1.35, 1.2, 300, (0.10, 0.16, 0.16))


def house_stone_01(b: Builder) -> None:
    # Block-by-block facade gives the requested visible masonry variation.
    rows, cols = 7, 12
    for row in range(rows):
        z = 0.35 + row * 0.58
        offset = 0.34 if row % 2 else 0.0
        for col in range(cols):
            x = -4.1 + col * 0.74 + offset
            if x > 4.15:
                continue
            shade = 0.34 + 0.11 * b.rng.random()
            b.box(
                (x, -3.51, z),
                (0.68, 0.45, 0.50),
                62,
                (shade, shade * 1.03, shade * 1.04),
                noise=0.025,
            )
    b.box((0, 0, 2.0), (9.0, 7.0, 4.0), 5600, STONE, weathering=0.16)
    b.gable_roof(9.0, 7.0, 3.9, 6.5, 4000, DARK_STONE, scale=(0.04, 0.14))
    _facade_opening(b, 0, -3.76, 1.25, 1.45, 2.5, 500, (0.13, 0.12, 0.10))
    for x in (-2.6, 2.6):
        _facade_opening(b, x, -3.75, 2.15, 1.15, 1.05, 330, (0.11, 0.17, 0.18))
    for i in range(3):
        b.box((0, -3.9 - i * 0.30, 0.10 + i * 0.12), (2.6 + i * 0.35, 0.45, 0.20), 260, DARK_STONE)


def house_thatch_01(b: Builder) -> None:
    b.box((0, 0, 1.85), (7.0, 6.0, 3.7), 5100, (0.61, 0.43, 0.22), weathering=0.17)
    # Three slightly offset layers create a thick, ragged thatch silhouette.
    roof_layers = (
        (0.0, 7.0, 6.0, 3000),
        (-0.18, 7.35, 6.25, 1600),
        (0.14, 6.7, 5.9, 1200),
    )
    for dz, width, depth, count in roof_layers:
        b.gable_roof(width, depth, 3.45 + dz, 6.0 + dz, count, (0.65, 0.48, 0.20),
                     eave=0.55, thickness=0.18, scale=(0.055, 0.17), weathering=0.18)
    _facade_opening(b, 0, -3.06, 1.20, 1.25, 2.4, 500, (0.20, 0.105, 0.045))
    for x in (-2.05, 2.05):
        _facade_opening(b, x, -3.07, 1.95, 0.95, 0.95, 300, (0.12, 0.15, 0.13))


def house_barn_01(b: Builder) -> None:
    b.box((0, 0, 2.7), (12.0, 8.0, 5.4), 7800, (0.42, 0.10, 0.075), weathering=0.19)
    b.gable_roof(12.0, 8.0, 5.15, 8.0, 4700, (0.10, 0.11, 0.115), scale=(0.05, 0.16))
    # Barn-board seams and braces.
    for x in np.linspace(-5.5, 5.5, 12):
        b.box((float(x), -4.06, 2.7), (0.10, 0.10, 5.3), 120, DARK_WOOD)
    _facade_opening(b, 0, -4.10, 2.25, 4.1, 4.5, 1300, (0.075, 0.055, 0.045))
    b.tube((-2.0, -4.18, 0.25), (2.0, -4.18, 4.5), 0.13, 350, (0.31, 0.16, 0.08))
    b.tube((2.0, -4.18, 0.25), (-2.0, -4.18, 4.5), 0.13, 350, (0.31, 0.16, 0.08))


def tree_pine_01(b: Builder) -> None:
    b.tube((0, 0, 0), (0.05, -0.02, 7.3), 0.28, 1250, (0.30, 0.16, 0.075), scale=(0.025, 0.09))
    layers = [
        (2.2, 3.3, 2.1, 1250, (0.08, 0.25, 0.11)),
        (3.8, 3.2, 1.75, 1200, (0.07, 0.30, 0.13)),
        (5.3, 2.8, 1.35, 1100, (0.06, 0.27, 0.10)),
        (6.6, 2.4, 0.95, 950, (0.09, 0.34, 0.14)),
    ]
    for z0, height, radius, n, color in layers:
        b.cone((0, 0), z0, height, radius, n, color, scale=(0.035, 0.13), weathering=0.13)
    # Root flare ensures an obvious grounded silhouette.
    for theta in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        b.tube(
            (0, 0, 0.18),
            (0.85 * np.cos(theta), 0.85 * np.sin(theta), 0),
            0.09,
            75,
            (0.26, 0.14, 0.06),
        )


def tree_broadleaf_01(b: Builder) -> None:
    b.tube((0, 0, 0), (0.0, 0.0, 4.5), 0.38, 1300, (0.32, 0.17, 0.075), scale=(0.025, 0.095))
    branch_tips = [(-2.1, -0.5, 5.7), (2.2, -0.3, 5.8), (-1.0, 1.8, 6.1), (1.1, 1.7, 6.3)]
    for tip in branch_tips:
        b.tube((0, 0, 3.6), tip, 0.18, 390, (0.30, 0.16, 0.07), scale=(0.02, 0.08))
    crowns = [
        ((-1.8, -0.4, 6.1), (2.0, 1.9, 1.65), 1150, (0.13, 0.39, 0.15)),
        ((1.7, -0.4, 6.2), (2.1, 1.9, 1.7), 1150, (0.10, 0.45, 0.16)),
        ((-0.7, 1.35, 6.5), (2.1, 1.8, 1.55), 1050, (0.16, 0.48, 0.18)),
        ((1.0, 1.2, 6.6), (2.0, 1.9, 1.5), 1050, (0.09, 0.36, 0.12)),
        ((0.0, 0.2, 7.2), (2.25, 2.0, 1.45), 1200, (0.14, 0.43, 0.14)),
    ]
    for center, radii, n, color in crowns:
        b.ellipsoid(center, radii, n, color, scale=(0.035, 0.15), weathering=0.16)


def tree_bamboo_01(b: Builder) -> None:
    stems = [
        (-0.95, -0.45, 8.8, 0.075), (-0.55, 0.55, 9.6, 0.08),
        (-0.10, -0.20, 10.0, 0.085), (0.40, 0.45, 9.2, 0.075),
        (0.85, -0.40, 8.5, 0.07), (0.95, 0.65, 9.0, 0.07),
        (-0.95, 0.75, 8.2, 0.065), (0.25, -0.90, 8.7, 0.07),
    ]
    for i, (x, y, h, radius) in enumerate(stems):
        lean = b.rng.normal(0, 0.18, 2)
        b.tube((x, y, 0), (x + lean[0], y + lean[1], h), radius, 360,
               (0.20, 0.48, 0.20), scale=(0.014, 0.045), noise=0.025)
        # Dark joints make the bamboo readable at medium LOD.
        for z in np.arange(0.8, h, 0.85):
            b.ellipsoid((x + lean[0] * z / h, y + lean[1] * z / h, z),
                        (radius * 1.5, radius * 1.5, 0.035), 22,
                        (0.12, 0.32, 0.13), scale=(0.012, 0.035))
        for j in range(4):
            angle = (i * 0.9 + j * 1.7) % (2 * np.pi)
            z = h - 0.5 - j * 0.45
            center = (x + 0.52 * np.cos(angle), y + 0.52 * np.sin(angle), z)
            b.ellipsoid(center, (0.75, 0.26, 0.16), 145, (0.12, 0.43, 0.16),
                        shell_bias=0.25, scale=(0.018, 0.06), weathering=0.14)


def stone_wall_01(b: Builder) -> None:
    rows = [(0.24, 0.48, 8), (0.68, 0.40, 7), (1.04, 0.32, 8)]
    for z, height, count in rows:
        widths = b.rng.uniform(0.38, 0.62, count)
        widths *= 4.0 / widths.sum()
        x = -2.0
        for width in widths:
            cx = x + width / 2.0
            depth = b.rng.uniform(0.38, 0.52)
            shade = b.rng.uniform(0.31, 0.50)
            b.box((cx, b.rng.uniform(-0.04, 0.04), z),
                  (float(width * 0.96), depth, height * 0.9), 145,
                  (shade, shade * 1.02, shade * 1.03), scale=(0.025, 0.085), weathering=0.16)
            x += width
    b.box((0, 0, 0.04), (4.0, 0.5, 0.08), 250, DARK_STONE, scale=(0.025, 0.07))


def stone_lamp_01(b: Builder) -> None:
    b.box((0, 0, 0.12), (0.78, 0.78, 0.24), 420, STONE, scale=(0.018, 0.06))
    b.box((0, 0, 0.34), (0.52, 0.52, 0.20), 260, DARK_STONE, scale=(0.018, 0.055))
    b.tube((0, 0, 0.40), (0, 0, 1.22), 0.13, 560, STONE, scale=(0.015, 0.05))
    b.box((0, 0, 1.34), (0.62, 0.62, 0.38), 560, (0.35, 0.39, 0.39), scale=(0.016, 0.055))
    # Four dark openings around the lamp chamber.
    for x, y, sx, sy in ((0, -0.325, 0.32, 0.04), (0, 0.325, 0.32, 0.04),
                         (-0.325, 0, 0.04, 0.32), (0.325, 0, 0.04, 0.32)):
        b.box((x, y, 1.36), (sx, sy, 0.22), 105, (0.08, 0.10, 0.095), scale=(0.012, 0.04))
    b.box((0, 0, 1.60), (0.80, 0.80, 0.16), 430, STONE, scale=(0.02, 0.065))
    b.box((0, 0, 1.78), (0.48, 0.48, 0.22), 350, DARK_STONE, scale=(0.018, 0.06))
    b.ellipsoid((0, 0, 1.96), (0.12, 0.12, 0.12), 180, STONE, scale=(0.014, 0.045))


def fence_wood_01(b: Builder) -> None:
    for y, z in ((0.0, 0.38), (0.0, 0.82)):
        b.box((0, y, z), (3.0, 0.16, 0.16), 520, WOOD, scale=(0.018, 0.065), weathering=0.18)
    for x in np.linspace(-1.42, 1.42, 7):
        h = b.rng.uniform(0.92, 1.10)
        b.box((float(x), 0, h / 2.0), (0.18, 0.20, h), 260, DARK_WOOD,
              scale=(0.017, 0.06), weathering=0.20)
        # Small pointed cap.
        b.cone((float(x), 0.0), h - 0.02, 0.14, 0.15, 60, DARK_WOOD,
               scale=(0.012, 0.045))


SPECS: list[AssetSpec] = [
    AssetSpec("house_wood_01", "building", (8.0, 6.0, 6.5), house_wood_01),
    AssetSpec("house_wood_02", "building", (10.0, 7.0, 7.0), house_wood_02),
    AssetSpec("house_stone_01", "building", (9.0, 7.0, 6.5), house_stone_01),
    AssetSpec("house_thatch_01", "building", (7.0, 6.0, 6.0), house_thatch_01),
    AssetSpec("house_barn_01", "building", (12.0, 8.0, 8.0), house_barn_01),
    AssetSpec("tree_pine_01", "vegetation", (4.0, 4.0, 9.0), tree_pine_01),
    AssetSpec("tree_broadleaf_01", "vegetation", (7.0, 7.0, 8.0), tree_broadleaf_01),
    AssetSpec("tree_bamboo_01", "vegetation", (3.0, 3.0, 10.0), tree_bamboo_01),
    AssetSpec("stone_wall_01", "prop", (4.0, 0.5, 1.2), stone_wall_01),
    AssetSpec("stone_lamp_01", "prop", (0.8, 0.8, 2.0), stone_lamp_01),
    AssetSpec("fence_wood_01", "prop", (3.0, 0.2, 1.1), fence_wood_01),
]


def build_scene(spec: AssetSpec) -> GaussianScene:
    """确定性构建单个素材 (局部坐标, Z 上, 地面 z=0)。"""
    builder = Builder(spec.asset_id)
    spec.build(builder)
    return builder.finish()


def seed_registry(assets_dir: str | Path = "assets",
                  origin: str = "gpt-mock") -> AssetRegistry:
    """确定性重建全部素材 ply 到 assets_dir, 并写出 registry.json (含 sha256)。

    整体重写 registry (不走 register/replace 增量), 保证任意机器上幂等且版本恒为 v1。
    fresh clone 只需 `make assets` 即可从 tracked 生成器还原素材 + 悬空 registry。
    """
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    reg = AssetRegistry(assets_dir)
    reg.doc = RegistryDoc()
    for spec in SPECS:
        scene = build_scene(spec)
        ply_name = f"{spec.asset_id}_v1.ply"
        scene.save_ply(assets_dir / ply_name, flavor="3dgs")
        reg.doc.assets[spec.asset_id] = AssetEntry(
            kind=spec.kind, ply=ply_name, version=1, origin=origin,
            footprint_m=list(spec.footprint_m),
            sha256=sha256_file(assets_dir / ply_name),
        )
    reg.save()
    return reg


def write_deliverable(out_dir: str | Path) -> Path:
    """生成 handoff 风格交付目录 (<id>.ply + manifest.json), 供 validate_handoff 验收演练。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for spec in SPECS:
        scene = build_scene(spec)
        scene.save_ply(out_dir / f"{spec.asset_id}.ply", flavor="3dgs")
        items.append({
            "asset_id": spec.asset_id, "kind": spec.kind,
            "ply": f"{spec.asset_id}.ply", "footprint_m": list(spec.footprint_m),
        })
    manifest = {"handoff_id": HANDOFF_ID, "items": items}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_dir / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="确定性村庄 mock 素材生成器")
    parser.add_argument("--assets-dir", default="assets", help="素材注册表目录")
    parser.add_argument("--out", default=None,
                        help="改为生成 handoff 交付目录 (含 manifest.json) 到该路径")
    args = parser.parse_args()

    if args.out:
        manifest = write_deliverable(args.out)
        print(f"交付目录: {manifest.parent} ({len(SPECS)} items)")
        return

    reg = seed_registry(args.assets_dir)
    for aid, entry in reg.doc.assets.items():
        scene = GaussianScene.load_ply(reg.assets_dir / entry.ply)
        print(f"{aid:20s} {len(scene):6d} gaussians  sha={entry.sha256[:12]}")
    problems = reg.verify()
    if problems:
        raise SystemExit(f"素材自校验失败: {problems}")
    print(f"[OK] 重建并注册 {len(reg.doc.assets)} 个素材 → {reg.registry_path}")


if __name__ == "__main__":
    main()
