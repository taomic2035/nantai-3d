"""大重建场景的空间分块: 单个巨型 3DGS → 可流式的 per-chunk ply + LOD + manifest。

**为什么**: 一次真实重建 (COLMAP 位姿 + 云 GPU 3DGS 训练) 产出【单个】可能上百万高斯的
`.ply`。viewer 只能整块加载: 大场景下载慢、无空间裁剪 —— 站在村东也得载完整个村。
本模块按 XY 网格把它切成 chunk + LOD, 让 viewer 只载相机附近的块 (与合成村庄的分块
流式路径同构), 使大真实重建也能 360° 任意坐标漫游。

**铁律 —— 纯空间重打包**: 不改几何、不改坐标 (块内高斯保持源 frame 的绝对坐标)、
不改 provenance。每个高斯恰好落入一个块 (半开区间 [lo, hi) 分箱 → 无损、不重复)。
分块产物**绝不比源场景声称更多信任**: 每块继承源的 frame_id/units/transform 历史,
manifest 如实记录源坐标契约。想要 metric-aligned, 得在源场景那步挣到 (见 alignment)。
"""
from __future__ import annotations

import json
import math
import numbers
from pathlib import Path

import numpy as np
from loguru import logger

from pipeline.gaussian_scene import GaussianScene

DEFAULT_CHUNK_SIZE_M = 50.0
# 与 render_chunk_to_ply.DEFAULT_LOD_FRACTIONS 同语义: 0=远景, 1=中景, 2/缺省=全量。
DEFAULT_LOD_FRACTIONS: dict[int, float] = {0: 0.08, 1: 0.30}
MANIFEST_NAME = "chunks.json"


def _require_positive_size(chunk_size_m: float) -> float:
    if not isinstance(chunk_size_m, numbers.Real) or isinstance(chunk_size_m, bool):
        raise ValueError(f"chunk_size_m must be a positive number, got {chunk_size_m!r}")
    value = float(chunk_size_m)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"chunk_size_m must be finite and > 0, got {chunk_size_m!r}")
    return value


def partition_scene_to_chunks(
    scene: GaussianScene,
    out_dir: str | Path,
    chunk_size_m: float = DEFAULT_CHUNK_SIZE_M,
    lod_fractions: dict[int, float] | None = None,
    source_provenance: dict | None = None,
) -> dict:
    """把一个大场景按 XY 网格空间分块 → per-chunk ply + LOD + 流式 manifest。

    分箱用半开区间 ``[cx*size, (cx+1)*size)`` (复用 ``GaussianScene.crop_aabb`` 同语义),
    故每个高斯恰好落一个块: 无损、不重复。块内坐标保持**绝对**(源 frame 坐标, 不平移),
    viewer 按 per-chunk ``aabb`` 裁剪/取景, 无需下载 ply。

    返回 manifest dict (同时以 LF 写到 ``out_dir/chunks.json``, 跨平台字节可复现)。
    provenance: 每块继承源 scene 的 frame_id/units/applied_transform_ids (``crop_aabb``
    经 ``_subset`` 保留), manifest 的 ``source`` 如实记录源契约 —— 分块绝不提升信任。

    ``source_provenance``: 源 recon manifest 的信任判定 (如
    ``{"geometry_usability": ..., "recon_manifest_sha256": ...}``) 并入 ``source``, 让消费者
    能诚实标注 preview-only / metric-aligned 并回溯到挣得该判定的 manifest。**缺席即未知**:
    不提供就不写该字段, 绝不猜测/编造信任等级 (分块只搬运判定, 从不产生判定)。
    """
    chunk_size_m = _require_positive_size(chunk_size_m)
    if len(scene) == 0:
        raise ValueError("cannot partition an empty scene")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fractions = dict(DEFAULT_LOD_FRACTIONS if lod_fractions is None else lod_fractions)

    keys = np.floor(scene.xyz[:, :2] / chunk_size_m).astype(np.int64)
    # 排序 → 分块顺序确定 (manifest 可复现)
    cells = sorted({(int(kx), int(ky)) for kx, ky in keys})

    chunks: list[dict] = []
    total_points = 0
    bmin = [float("inf")] * 3
    bmax = [float("-inf")] * 3

    for cx, cy in cells:
        lo = (cx * chunk_size_m, cy * chunk_size_m)
        hi = (lo[0] + chunk_size_m, lo[1] + chunk_size_m)
        sub = scene.crop_aabb(lo, hi)
        if len(sub) == 0:      # 分箱与 crop 边界一致时不该发生; 防御性跳过空块
            continue
        name = f"chunk_{cx}_{cy}.ply"
        sub.save_ply(out_dir / name, flavor="3dgs")

        lod_files = {2: name}   # lod2 == 全量, 与合成村庄 manifest 同约定
        for level, frac in sorted(fractions.items()):
            lod_name = f"chunk_{cx}_{cy}_lod{level}.ply"
            sub.to_quality(frac).save_ply(out_dir / lod_name, flavor="3dgs")
            lod_files[level] = lod_name

        aabb_min = [float(sub.xyz[:, i].min()) for i in range(3)]
        aabb_max = [float(sub.xyz[:, i].max()) for i in range(3)]
        for i in range(3):
            bmin[i] = min(bmin[i], aabb_min[i])
            bmax[i] = max(bmax[i], aabb_max[i])

        chunks.append({
            "id": f"{cx}_{cy}",
            "x": cx,
            "y": cy,
            "ply_file": name,
            "lod": {str(k): v for k, v in sorted(lod_files.items())},
            "point_count": len(sub),
            "aabb": {"min": aabb_min, "max": aabb_max},
        })
        total_points += len(sub)

    manifest = {
        "schema_version": 1,
        "kind": "spatial-chunks",
        "chunk_size_m": chunk_size_m,
        "chunks": chunks,
        # 各 LOD 的实际比例 (含 lod2=1.0 全量): 只给文件名的话, 消费者不知道 lod0 是 8%
        # 还是别的密度, 无法按相机距离正确选级。声明出来, 语义不用猜。
        "lod_fractions": {
            **{str(level): float(frac) for level, frac in sorted(fractions.items())},
            "2": 1.0,
        },
        "total_chunks": len(chunks),
        "total_points": total_points,
        "bounds": {"min": bmin, "max": bmax},
        "extent": {
            "x_min": min(c["x"] for c in chunks),
            "x_max": max(c["x"] for c in chunks),
            "y_min": min(c["y"] for c in chunks),
            "y_max": max(c["y"] for c in chunks),
        },
        # 如实记录源坐标契约: 分块是纯重打包, 信任不增不减。
        "source": {
            "frame_id": scene.frame_id,
            "units": scene.units,
            "applied_transform_ids": list(scene.applied_transform_ids),
            # 源 manifest 的信任判定 (geometry_usability + 内容寻址 sha) 若提供则并入,
            # 让消费者能诚实标注并回溯; 缺席即未知, 绝不编造。
            **(dict(source_provenance) if source_provenance else {}),
        },
    }
    # newline="\n": 与 trust root (registration/recon_manifest/world manifest) 惯例统一,
    # 让 manifest 跨平台字节可复现 (Windows write_text 默认把 \n 转 \r\n)。
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    logger.info(
        f"空间分块: {total_points} 高斯 → {len(chunks)} 块 "
        f"({chunk_size_m:g}m 网格) → {out_dir/MANIFEST_NAME}")
    return manifest
