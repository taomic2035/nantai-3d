"""
GPT 交付物自动验收: handoff/feedback 协作闭环的机器校验环节

流程 (见 handoff/README.md):
  1. 我方发出 handoff/HANDOFF-xxx.md (素材规格)
  2. GPT 按规格生成交付目录 (manifest.json + *.ply)
  3. 本脚本验收 → 生成 handoff/FEEDBACK-xxx.md (逐项 PASS/FAIL + 整改意见)
  4. 全部 PASS 后 --register 一键导入素材注册表 (origin=gpt-mock)

用法:
    python -m pipeline.validate_handoff deliverable/ --feedback-dir handoff
    python -m pipeline.validate_handoff deliverable/ --register  # 验收通过即导入
"""
import argparse
import json
from pathlib import Path
from typing import Literal

import numpy as np
from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from pipeline.gaussian_scene import GaussianScene

# 验收阈值
MIN_GAUSSIANS = 200
MAX_GAUSSIANS = 500_000
FOOTPRINT_TOLERANCE = 0.5      # 实际尺寸与声明 footprint 允许 ±50%
GROUND_Z_TOLERANCE = 1.0       # 最低点距 z=0 允许偏差 (米)
MIN_COLOR_STD = 0.01           # 颜色标准差下限 (拒绝纯色废料)
SCALE_RANGE = (0.003, 2.0)     # 高斯尺寸中位数合理区间 (米)


class DeliverableItem(BaseModel):
    # kind 与 assets.AssetEntry.kind 同枚举, 保证验收通过后 --register 不会再校验失败
    asset_id: str = Field(min_length=1)
    kind: Literal["building", "vegetation", "prop", "ground", "other"] = "other"
    ply: str
    footprint_m: list[float] | None = None


class DeliverableManifest(BaseModel):
    handoff_id: str
    items: list[DeliverableItem] = Field(min_length=1)


def check_item(item: DeliverableItem, base_dir: Path) -> list[str]:
    """单个素材的全部检查, 返回问题列表 (空 = PASS)"""
    problems: list[str] = []
    ply_path = (base_dir / item.ply).resolve()
    # 防路径穿越: manifest 里的 ply 必须落在交付目录内
    if not ply_path.is_relative_to(base_dir.resolve()):
        return [f"ply 路径越出交付目录: {item.ply}"]
    if not ply_path.exists():
        return [f"ply 文件缺失: {item.ply}"]

    try:
        scene = GaussianScene.load_ply(ply_path)
    except Exception as e:
        return [f"ply 解析失败: {e}"]

    n = len(scene)
    if not (MIN_GAUSSIANS <= n <= MAX_GAUSSIANS):
        problems.append(f"高斯数量 {n} 超出区间 [{MIN_GAUSSIANS}, {MAX_GAUSSIANS}]")
    if n == 0:
        return problems

    lo, hi = scene.bounds()
    size = hi - lo

    # 坐标约定: 地面 z≈0
    if abs(lo[2]) > GROUND_Z_TOLERANCE:
        problems.append(f"最低点 z={lo[2]:.2f}m, 应贴近 0 (约定: 地面 z=0)")

    # 声明尺寸 vs 实际
    if item.footprint_m and len(item.footprint_m) >= 2:
        fw, fd = item.footprint_m[0], item.footprint_m[1]
        for label, actual, declared in (("宽", size[0], fw), ("深", size[1], fd)):
            if declared > 0 and not (
                    declared * (1 - FOOTPRINT_TOLERANCE)
                    <= actual <= declared * (1 + FOOTPRINT_TOLERANCE)):
                problems.append(
                    f"{label} {actual:.1f}m 偏离声明 {declared:.1f}m 超过 ±50%")

    # 颜色非退化
    if float(scene.rgb.std()) < MIN_COLOR_STD:
        problems.append(f"颜色退化 (std={scene.rgb.std():.4f}), 疑似纯色占位")

    # 高斯尺寸合理
    med_scale = float(np.median(scene.scale))
    if not (SCALE_RANGE[0] <= med_scale <= SCALE_RANGE[1]):
        problems.append(
            f"高斯尺寸中位数 {med_scale:.4f}m 超出合理区间 {SCALE_RANGE}")

    # 不透明度非全透明
    if float(scene.opacity.mean()) < 0.05:
        problems.append(f"平均不透明度 {scene.opacity.mean():.3f} 过低")

    return problems


def validate(deliverable_dir: str | Path,
             feedback_dir: str | Path = "handoff",
             do_register: bool = False,
             assets_dir: str | Path = "assets") -> dict:
    """验收交付目录, 生成 FEEDBACK 文档, 返回结果 dict"""
    deliverable_dir = Path(deliverable_dir)
    feedback_dir = Path(feedback_dir)
    manifest_path = deliverable_dir / "manifest.json"

    results: dict[str, list[str]] = {}
    fatal: str | None = None
    manifest: DeliverableManifest | None = None

    if not manifest_path.exists():
        fatal = f"manifest.json 缺失: {manifest_path}"
    else:
        try:
            manifest = DeliverableManifest(
                **json.loads(manifest_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError) as e:
            fatal = f"manifest.json 不符合 schema: {e}"

    if manifest:
        for item in manifest.items:
            results[item.asset_id] = check_item(item, deliverable_dir)

    n_pass = sum(1 for v in results.values() if not v)
    n_total = len(results)
    all_pass = fatal is None and n_total > 0 and n_pass == n_total
    handoff_id = manifest.handoff_id if manifest else deliverable_dir.name

    # 生成 FEEDBACK 文档
    lines = [
        f"# FEEDBACK — {handoff_id}",
        "",
        f"**验收结果: {'✅ 全部通过' if all_pass else '❌ 未通过'}"
        f" ({n_pass}/{n_total})**",
        "",
    ]
    if fatal:
        lines += ["## 致命问题", "", f"- {fatal}", ""]
    if results:
        lines += ["## 逐项结果", "",
                  "| asset_id | 结果 | 问题 |", "|---|---|---|"]
        for aid, problems in results.items():
            status = "PASS" if not problems else "FAIL"
            lines.append(f"| {aid} | {status} | {'; '.join(problems) or '—'} |")
        lines.append("")
    if not all_pass:
        lines += ["## 整改要求", "",
                  "- 修复上表 FAIL 项后重新交付整个目录 (含 manifest.json)",
                  "- 规格以对应 HANDOFF 文档为准, 阈值见 pipeline/validate_handoff.py 顶部常量",
                  ""]
    else:
        lines += ["## 后续动作", "",
                  f"- 导入注册表: `python -m pipeline.validate_handoff "
                  f"{deliverable_dir} --register`",
                  ""]

    feedback_dir.mkdir(parents=True, exist_ok=True)
    feedback_path = feedback_dir / f"FEEDBACK-{handoff_id}.md"
    feedback_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"验收 {'PASS' if all_pass else 'FAIL'} ({n_pass}/{n_total}) "
                f"→ {feedback_path}")

    # 验收通过后导入注册表
    registered = []
    if all_pass and do_register and manifest:
        from pipeline.assets import AssetRegistry
        reg = AssetRegistry(assets_dir)
        for item in manifest.items:
            reg.register(item.asset_id, deliverable_dir / item.ply,
                         kind=item.kind, origin="gpt-mock",
                         footprint_m=item.footprint_m)
            registered.append(item.asset_id)
        logger.info(f"已导入 {len(registered)} 个素材到 {assets_dir}/")

    return {
        "handoff_id": handoff_id,
        "all_pass": all_pass,
        "n_pass": n_pass,
        "n_total": n_total,
        "fatal": fatal,
        "results": results,
        "feedback_file": str(feedback_path),
        "registered": registered,
    }


def main():
    parser = argparse.ArgumentParser(description="GPT 交付物自动验收")
    parser.add_argument("deliverable", help="交付目录 (含 manifest.json)")
    parser.add_argument("--feedback-dir", default="handoff",
                        help="FEEDBACK 输出目录")
    parser.add_argument("--register", action="store_true",
                        help="验收通过后导入素材注册表")
    parser.add_argument("--assets-dir", default="assets")
    args = parser.parse_args()

    r = validate(args.deliverable, args.feedback_dir,
                 do_register=args.register, assets_dir=args.assets_dir)
    print(f"\n验收: {'PASS' if r['all_pass'] else 'FAIL'} "
          f"({r['n_pass']}/{r['n_total']})")
    print(f"反馈文档: {r['feedback_file']}")
    if r["registered"]:
        print(f"已导入素材: {', '.join(r['registered'])}")
    raise SystemExit(0 if r["all_pass"] else 1)


if __name__ == "__main__":
    main()
