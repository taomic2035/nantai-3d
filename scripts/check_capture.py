#!/usr/bin/env python3
"""采集预检: 跑 COLMAP 之前先看看这批照片值不值得等几小时。

**为什么**: 手册 §4 的实测是 ~300 张无序照片在本机 CPU 上要 2–5+ 小时, 而"若重叠不足,
mapper 可能只注册部分图或不产模型"。白等 5 小时拿到空结果是真实存在的坑。本脚本用单图
就能拿到的证据 (张数/清晰度/分辨率/EXIF) 提前预警, 几秒钟出结果。

**这只是启发式预检, 不能替代真跑 COLMAP。** 真正决定成败的**重叠度**是图与图之间的关系,
单图分析测不出来 —— 报告里会明说。通过预检不保证能重建, 未通过也不保证一定失败。

用法:
    python scripts/check_capture.py photos/
    python scripts/check_capture.py photos/ --json > precheck.json
    python scripts/check_capture.py photos/ --blur-threshold 60

退出码: 0 = 已出报告 (无论 verdict 好坏); 2 = 没法分析 (目录不存在/没有图片), fail-closed。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.capture_quality import (  # noqa: E402
    DEFAULT_BLUR_THRESHOLD,
    CaptureQualityError,
    analyze_capture,
)

_VERDICT_LABEL = {
    "likely": "likely  —— 没发现明显硬伤",
    "risky": "risky   —— 有问题, 建议先处理再跑",
    "unlikely": "unlikely —— 大概率白等, 请先补救",
}


def _format_minutes(minutes: float) -> str:
    if minutes < 1:
        return "<1 分钟"  # 不要把 0.1 分钟印成 "0 分钟", 看起来像没算
    if minutes < 90:
        return f"{minutes:.0f} 分钟"
    return f"{minutes / 60:.1f} 小时"


def _print_human(report: dict) -> None:
    count = report["count"]
    blur = report["blur"]
    resolution = report["resolution"]
    exif = report["exif"]
    estimate = report["colmap_estimate"]
    verdict = report["verdict"]

    print(f"采集预检: {report['photos_dir']}")
    print("=" * 68)

    low, high = count["recommended_range"]
    print(f"\n[数量] {count['images']} 张图 (手册建议 {low}~{high} 张)")
    if count["unreadable"]:
        print(f"       其中 {count['unreadable']} 张无法解码")
    print(f"       格式: {', '.join(f'{k} x{v}' for k, v in count['by_suffix'].items())}")

    print("\n[模糊度]", end=" ")
    if blur["available"]:
        print(
            f"{blur['blurry_count']}/{blur['scored']} 张低于阈值 {blur['threshold']} "
            f"({blur['blurry_ratio']:.0%})  [后端: {blur['backend']}]"
        )
        print(
            f"       Laplacian 方差: 最低 {blur['min']} / p10 {blur['p10']} / "
            f"中位 {blur['median']} / 最高 {blur['max']}"
        )
        if blur["blurry_files"]:
            listed = ", ".join(blur["blurry_files"])
            suffix = " ..." if blur["blurry_files_truncated"] else ""
            print(f"       模糊图: {listed}{suffix}")
        print(f"       注意: {blur['threshold_note']}")
    else:
        print(f"已跳过\n       {blur['skipped_reason']}")

    print("\n[分辨率]", end=" ")
    if resolution["available"]:
        print(
            f"中位 {resolution['median_megapixels']}MP "
            f"(最低 {resolution['min_megapixels']} / 最高 {resolution['max_megapixels']})"
        )
        if resolution["below_min_count"]:
            print(
                f"       {resolution['below_min_count']} 张低于 "
                f"{resolution['min_megapixels_threshold']}MP —— 特征点可能不足"
            )
        if resolution["oversized_count"]:
            print(f"       {resolution['oversized_count']} 张 >12MP —— COLMAP 会更慢")
    else:
        print(f"已跳过\n       {resolution['skipped_reason']}")

    print(
        f"\n[EXIF] {exif['with_datetime']}/{count['images']} 张有拍摄时间, "
        f"{exif['with_gps']}/{count['images']} 张有 GPS"
    )
    print(f"       {exif['gps_note']}")

    print(
        f"\n[COLMAP 粗估] 建议匹配器: {estimate['matcher_recommended']} "
        f"({estimate['pairs']} 个图对)"
    )
    print(
        f"       预计耗时: {_format_minutes(estimate['minutes_low'])} ~ "
        f"{_format_minutes(estimate['minutes_high'])} "
        f"(特征提取 {_format_minutes(estimate['extract_minutes_low'])} + "
        f"匹配 {_format_minutes(estimate['match_minutes_low'])} 起)"
    )
    if estimate["ordering_evidence"]:
        alternative = estimate["alternative"]
        print(
            f"       顺序证据: {', '.join(estimate['ordering_evidence'])} → 可用 sequential; "
            f"若改用 exhaustive 约 {_format_minutes(alternative['minutes_low'])} ~ "
            f"{_format_minutes(alternative['minutes_high'])}"
        )
    if estimate["small_batch_caution"]:
        print(f"       {estimate['small_batch_caution']}")
    print(f"       {estimate['note']}")

    print("\n" + "=" * 68)
    print(f"[结论] {_VERDICT_LABEL.get(verdict['level'], verdict['level'])}")
    print(f"       {verdict['meaning']}")
    if verdict["reasons"]:
        print("\n  发现的问题:")
        for reason in verdict["reasons"]:
            print(f"    - {reason}")
    if verdict["remedies"]:
        print("\n  建议:")
        for remedy in verdict["remedies"]:
            print(f"    - {remedy}")

    print("\n  这个预检测不到什么 (请一起读, 别把 likely 当成功保证):")
    for limit in report["honesty"]["limits"]:
        print(f"    - {limit}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="采集预检 (启发式): 跑 COLMAP 前先判断这批照片值不值得等",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("photos_dir", type=Path, help="照片目录 (递归扫描)")
    parser.add_argument("--json", action="store_true", help="输出机读 JSON")
    parser.add_argument(
        "--blur-threshold",
        type=float,
        default=DEFAULT_BLUR_THRESHOLD,
        help=f"Laplacian 方差阈值 (启发式, 默认 {DEFAULT_BLUR_THRESHOLD}, 与 ingest 抽帧一致)",
    )
    args = parser.parse_args(argv)

    try:
        report = analyze_capture(args.photos_dir, blur_threshold=args.blur_threshold)
    except CaptureQualityError as exc:
        # 诚实的失败: 说清为什么 + 给出路, 而不是丢个晦涩错误
        print(f"[预检无法进行] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
