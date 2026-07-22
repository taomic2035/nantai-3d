#!/usr/bin/env python3
"""扁平化 3DGS PLY 的球谐 (SH): 丢弃高阶 f_rest_*, 只保留 DC (f_dc) 视角无关基色。

用途: ``pipeline/spherical_harmonics.py`` 已实现 degree 0–3 的 Wigner-D SH 旋转,
含高阶 SH 的场景可直接经非恒等 Sim3 旋转对齐到 ENU, **无需** 先扁平化。本工具
保留为**有损降级**选项, 适用于:
  - 下游消费者只接受 DC (如某些 viewer/trainer)
  - 减小 PLY 体积 (丢弃 f_rest_* 属性)
  - 不需要视角相关高光的简化漫游

诚实代价: 丢失视角相关高光, 保留正确的视角无关基色。旋转后 DC 恒等 (degree-0
SH 是常数), 故 flatten 后的 PLY 仍可安全旋转。

用法 (通常排在 normalize_ply_quats 之后、prepare_import 之前):
    python scripts/flatten_ply_sh.py trained/point_cloud.ply               # 原地写回
    python scripts/flatten_ply_sh.py trained/point_cloud.ply -o flat.ply   # 写到新文件

仅依赖 numpy + plyfile (本仓库已装)。零 GPU。纯结构操作, 与四元数归一化顺序无关。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


def flatten_sh(src: Path, dst: Path) -> int:
    """丢弃 src 的 vertex.f_rest_* 属性后写到 dst; 返回被丢弃的 f_rest 属性数。"""
    # mmap=False: 让原地写回 (dst == src) 不撞 Windows 的 "cannot reopen a
    # memory-mapped file for writing" (EINVAL)。
    ply = PlyData.read(str(src), mmap=False)
    if "vertex" not in ply:
        raise SystemExit(f"不是有效的 PLY (缺 vertex element): {src}")
    vertex = ply["vertex"]
    names = [p.name for p in vertex.properties]
    rest = [n for n in names if n.startswith("f_rest_")]
    if "f_dc_0" not in names:
        raise SystemExit(
            f"PLY 不含 f_dc (不是 3DGS PLY 或字段命名不同): {src}"
        )
    if not rest:
        print(f"[OK] 已是 degree-0 (无 f_rest), 无需扁平化: {src}")
    keep = [n for n in names if not n.startswith("f_rest_")]
    data = vertex.data
    # 构造仅含保留字段的连续结构化数组 (字段列表切片可能是带偏移的视图, 显式重建更稳)。
    kept = np.empty(len(data), dtype=[(n, data.dtype[n]) for n in keep])
    for n in keep:
        kept[n] = data[n]
    out = PlyElement.describe(kept, "vertex")
    PlyData([out], text=False, byte_order="<").write(str(dst))
    return len(rest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="扁平化 3DGS PLY 的球谐: 丢高阶 f_rest_*, 保 DC (视角无关基色)")
    ap.add_argument("ply", type=Path, help="输入 3DGS PLY")
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="输出路径 (默认原地写回)")
    args = ap.parse_args(argv)
    if not args.ply.is_file():
        raise SystemExit(f"文件不存在: {args.ply}")
    dst = args.out or args.ply
    dropped = flatten_sh(args.ply, dst)
    print(f"[OK] 丢弃 {dropped} 个 f_rest 属性 (高阶 SH), 保留 DC 基色 → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
