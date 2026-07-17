#!/usr/bin/env python3
"""扁平化 3DGS PLY 的球谐 (SH): 丢弃高阶 f_rest_*, 只保留 DC (f_dc) 视角无关基色。

为什么需要: 本仓库的坐标对齐会把 sfm-local 场景经 Sim3 (含**旋转**) 变换到米制 ENU
世界。高阶 SH 编码视角相关外观 (高光/反射), 其正确旋转 (Wigner-D) 本版**未实现**, 故
canonical loader/transform 对含高阶 SH 的场景施加非恒等旋转时**故意 fail-closed 阻断**
(绝不施加错误的 SH 旋转产生错误颜色)。nerfstudio splatfacto 等训练器输出带 SH 的 3DGS,
若要对其做**米制/地理对齐**, 先用本工具扁平化 SH。

诚实代价: 丢失视角相关高光, 保留正确的视角无关基色 —— 远好于错误的 SH 旋转。基本漫游
(preview-only sfm-local, 不含旋转) 无需本步; 仅米制/地理对齐 (含旋转) 路径需要。

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
