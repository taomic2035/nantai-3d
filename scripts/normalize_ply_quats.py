#!/usr/bin/env python3
"""归一化 3DGS PLY 的四元数 (rot_0..rot_3) 为单位长度。

外部训练器 (nerfstudio ns-export / INRIA 3DGS) 有时导出**未归一化**的旋转四元数。
本仓库的 canonical loader (pipeline/gaussian_scene.py load_ply) 对导入的 3DGS PLY
**故意 fail-closed 拒绝非单位四元数**（Studio 也复用这一语义校验），所以导入前需先
把 rot 归一化。归一化是无损的——四元数缩放不改变它表示的旋转。

用法:
    python scripts/normalize_ply_quats.py trained/point_cloud.ply                 # 原地写回
    python scripts/normalize_ply_quats.py trained/point_cloud.ply -o fixed.ply    # 写到新文件

仅依赖 numpy + plyfile (本仓库已装)。零 GPU。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

_ROT = ("rot_0", "rot_1", "rot_2", "rot_3")


def normalize_quaternions(src: Path, dst: Path) -> int:
    """把 src 的 vertex.rot_0..3 归一化后写到 dst；返回被修正的高斯数量。"""
    # mmap=False: keep the file unmapped so an in-place write (dst == src) does
    # not hit Windows' "cannot reopen a memory-mapped file for writing" (EINVAL).
    ply = PlyData.read(str(src), mmap=False)
    if "vertex" not in ply:
        raise SystemExit(f"不是有效的 PLY (缺 vertex element): {src}")
    vertex = ply["vertex"]
    names = {p.name for p in vertex.properties}
    missing = [r for r in _ROT if r not in names]
    if missing:
        raise SystemExit(
            f"PLY 不含四元数属性 {missing}；这不是 3DGS PLY 或字段命名不同: {src}"
        )
    data = vertex.data
    quat = np.stack([data[r].astype(np.float64) for r in _ROT], axis=1)  # (N,4)
    norms = np.linalg.norm(quat, axis=1, keepdims=True)
    zero = norms[:, 0] < 1e-12
    if np.any(zero):
        raise SystemExit(
            f"{int(zero.sum())} 个四元数范数为 0（无法归一化，PLY 可能损坏）: {src}"
        )
    off = ~np.isclose(norms[:, 0], 1.0, rtol=1e-3, atol=1e-4)
    unit = quat / norms
    for i, r in enumerate(_ROT):
        data[r] = unit[:, i].astype(data[r].dtype)
    # 保留其余属性与二进制小端布局，整块写回。
    out = PlyElement.describe(data, "vertex")
    PlyData([out], text=False, byte_order="<").write(str(dst))
    return int(off.sum())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="归一化 3DGS PLY 的四元数为单位长度")
    ap.add_argument("ply", type=Path, help="输入 3DGS PLY")
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="输出路径 (默认原地写回)")
    args = ap.parse_args(argv)
    if not args.ply.is_file():
        raise SystemExit(f"文件不存在: {args.ply}")
    dst = args.out or args.ply
    fixed = normalize_quaternions(args.ply, dst)
    print(f"[OK] 归一化 {fixed} 个非单位四元数 → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
