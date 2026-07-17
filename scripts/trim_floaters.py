#!/usr/bin/env python3
"""按显式判据剔除 3DGS 离群高斯。**默认 dry-run: 只报数字, 不写盘。**

为什么需要: 真实 3DGS 训练会把少数高斯优化到场景外几百米。实测一个 Brush 实训重建
(67878 高斯): Z 向 90% 分位仅 52.6, 真实 bounds 却达 720 —— 被极少数远点撑大 13 倍。
后果: 分块器切出 256 块而中位数每块仅 12 点 (79% 的块 <=100 点), viewer 白拉几百个
噪声块; 按 bounds 取景则相机停在几百米外对着空气。

**但这是有损操作**: 丢掉的高斯里**可能包含真实几何** (薄结构、场景边缘、稀疏采样区)。
故本工具:
  1. **没有默认阈值** —— 不给判据就什么都不剔。工具不替你决定丢掉你 20% 的重建。
  2. **默认 dry-run** —— 先让你看见自己数据上的真实代价, 加 --confirm 才真的写。
  3. 写出时留下 sidecar manifest, 如实记录丢了多少、按什么规则丢的 (有损可回溯)。

判据怎么选 (实测见 pipeline/outlier_trim.py 模块 docstring):
  - occupancy 在真实数据上最有效; opacity 在那份数据上是**反向信号** (丢 44% 的高斯而
    bounds 几乎不动 = 它在削主体几何), 用前务必先 dry-run 验证。
  - 没有客观正确的阈值: occupancy 阈值 3→10, 保留率 87.6%→72.8%。自己看数字拍板。

用法:
    # 1) 先 dry-run 看取舍 (不写盘)
    python scripts/trim_floaters.py in.ply -o out.ply --min-occupancy 5 --voxel-size 5
    # 2) 看完数字满意了再落盘
    python scripts/trim_floaters.py in.ply -o out.ply --min-occupancy 5 --voxel-size 5 --confirm

    # 扫描多个阈值, 帮你选 (纯 dry-run)
    python scripts/trim_floaters.py in.ply --sweep --voxel-size 5

仅依赖 numpy + plyfile (本仓库已装)。零 GPU。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许以 `python scripts/trim_floaters.py` 直接运行 (与 flatten_ply_sh 等同构)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.gaussian_scene import GaussianScene  # noqa: E402
from pipeline.outlier_trim import (  # noqa: E402
    OccupancyRule,
    OpacityRule,
    ScaleRule,
    evaluate_trim,
    trim_scene,
    voxel_occupancy,
)


def build_rules(args: argparse.Namespace) -> list:
    """只把**显式给出**的阈值变成规则。一个都没给 → 空列表 (调用方拒绝)。"""
    rules: list = []
    if args.min_occupancy is not None:
        if args.voxel_size is None:
            raise SystemExit(
                "--min-occupancy 需要同时显式给出 --voxel-size: 占据度的含义取决于体素\n"
                "边长, 没有正确的默认值 (实测 R=1 保留 69.4% / R=20 保留 98.3%)。")
        rules.append(OccupancyRule(voxel_size=args.voxel_size,
                                   min_occupancy=args.min_occupancy))
    if args.max_scale is not None:
        rules.append(ScaleRule(max_scale=args.max_scale))
    if args.min_opacity is not None:
        rules.append(OpacityRule(min_opacity=args.min_opacity))
    return rules


def run_sweep(scene: GaussianScene, voxel_size: float) -> None:
    """扫描 occupancy 阈值, 把取舍摆出来 —— 供你选阈值, 不写任何东西。"""
    if voxel_size is None:
        raise SystemExit("--sweep 需要显式给出 --voxel-size (体素边长没有正确默认值)")
    occ = voxel_occupancy(scene.xyz, voxel_size)
    total = len(scene)
    full_extent = scene.xyz.max(axis=0) - scene.xyz.min(axis=0)
    full_vol = float(full_extent.prod())
    print(f"输入: {total} 高斯   bounds "
          f"{'x'.join(f'{v:.1f}' for v in full_extent)}   单位: {scene.units}")
    print(f"体素边长 R = {voxel_size:g} (单位同上)\n")
    print(f"{'阈值':>6}{'保留':>10}{'保留率':>9}{'丢弃':>8}"
          f"{'剩余 bounds':>26}{'体积保留':>10}")
    for threshold in (1, 2, 3, 5, 10, 20, 50):
        keep = occ >= threshold
        kept = int(keep.sum())
        if kept == 0:
            print(f"{threshold:>6}{0:>10}{'0.0%':>9}{total:>8}"
                  f"{'(全部剔光)':>26}{'-':>10}")
            continue
        pts = scene.xyz[keep]
        ext = pts.max(axis=0) - pts.min(axis=0)
        vol = float(ext.prod())
        print(f"{threshold:>6}{kept:>10}{kept / total:>8.1%}{total - kept:>8}"
              f"{'x'.join(f'{v:.1f}' for v in ext):>26}"
              f"{(vol / full_vol if full_vol else 0):>9.2%}")
    print("\n没有客观正确的阈值 —— 上面每一行都是一个真实的取舍, 由你拍板。")
    print("判据是否有效看'体积保留': 丢弃率高而体积保留仍接近 100% = 判据在削主体几何。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="按显式判据剔除 3DGS 离群高斯 (默认 dry-run, 不写盘)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="输入 3DGS ply")
    parser.add_argument("-o", "--output", type=Path,
                        help="输出 ply (不指定则只能 dry-run)")
    # 三个阈值一律无默认值 (default=None): 不给就不剔。
    parser.add_argument("--voxel-size", type=float, default=None,
                        help="占据度判据的体素边长, 单位 = 场景声明的 units (非默认米)")
    parser.add_argument("--min-occupancy", type=int, default=None,
                        help="保留所在体素内点数 >= 该值的高斯 (需配合 --voxel-size)")
    parser.add_argument("--max-scale", type=float, default=None,
                        help="保留三轴最大 scale <= 该值的高斯, 单位 = 场景 units")
    parser.add_argument("--min-opacity", type=float, default=None,
                        help="保留 opacity >= 该值的高斯 (实测在真实数据上常为反向信号, "
                             "务必先 dry-run 看 bounds 体积是否真的收缩)")
    parser.add_argument("--sweep", action="store_true",
                        help="只扫描 occupancy 阈值列出取舍, 供选阈值 (绝不写盘)")
    parser.add_argument("--confirm", action="store_true",
                        help="确认落盘。缺省为 dry-run —— 只报数字, 一个字节都不写")
    parser.add_argument("--flavor", choices=("3dgs", "simple"), default="3dgs",
                        help="输出 ply 格式 (默认 3dgs, 保真)")
    args = parser.parse_args(argv)

    if not args.input.exists():
        raise SystemExit(f"输入不存在: {args.input}")
    scene = GaussianScene.load_ply(args.input)

    if args.sweep:
        run_sweep(scene, args.voxel_size)
        return 0

    rules = build_rules(args)
    if not rules:
        raise SystemExit(
            "未指定任何判据 —— 缺省不剔除 (剔除是有损操作, 本工具没有默认阈值)。\n"
            "先跑 --sweep 看看你的数据上各阈值的真实取舍, 再显式选一个:\n"
            f"  python scripts/trim_floaters.py {args.input} --sweep --voxel-size 5")

    if args.output is None:
        # 无输出路径 → 只能 dry-run; 用一个占位路径算报告, 绝不写。
        report = evaluate_trim(scene, rules=rules)
        print(report.describe())
        print("\n[dry-run] 未写任何文件。要落盘请给 -o/--output 并加 --confirm。")
        return 0

    report = trim_scene(scene, args.output, rules=rules, confirm=args.confirm,
                        flavor=args.flavor)
    print(report.describe())
    if report.written:
        print(f"\n[已写出] {report.output_path}")
        print(f"[剔除记录] {report.manifest_path}")
        print("注意: 剔除记录只在上面这份 sidecar manifest 里 —— 产物 ply 自身的元数据"
              "不含剔除记录, 只读 ply 的下游无法得知它被剔过。")
    else:
        print(f"\n[dry-run] 未写任何文件。以上是 {args.output} 若落盘的真实取舍。")
        print("确认要丢掉上面这些高斯? 加 --confirm 重跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
