#!/usr/bin/env python3
"""把 recon_manifest.json 翻译成人话: "我拿到的这个东西到底能不能用来测量"。

为什么: 本仓库的 provenance 极严谨, 但产物对人不友好 —— geometry_usability /
coordinate_contract / metric_evidence (含 sim3.alignment.v1=<json> 证据串) / transform_chain
这些字段普通用户根本读不出**"这玩意儿能不能量尺寸"**。读不懂的严谨等于没有严谨: 用户要么
不敢用, 要么拿 preview-only 的任意单位当米去量。本脚本只做翻译。

铁律 (本脚本存在的全部意义):
  - **只翻译, 绝不提升信任**。manifest 说 preview-only 就是 preview-only, 哪怕包围盒数字
    "看起来像米制" 也不美化。
  - **缺字段 → 报"未知", 绝不编造**。没有 sim3 证据就说残差未知, 不给一个好看的数字。
  - **矛盾时证据打败声称**。声称 metric 却带 passed:false / 无法解析的对齐证据, 或单位不是
    米 —— 一律**指出矛盾并按不可信处理** (与 pipeline/reconstruct.py 的
    _alignment_evidence_consistent 同一 fail-closed 语义; 这正是本项目修过的真实 bug)。
  - 因此本脚本**不用 pydantic 校验整个 manifest**: 输入可能是外来的/被篡改的文件, 解读器
    必须能读懂并指出它的毛病, 而不是崩在校验上。

限制 (说清楚做不到什么):
  - 只读 manifest **声称**的内容 + manifest 内部的自洽性。**不碰 PLY 字节**, 不校验
    artifacts 的 sha256, 不重算残差 —— 所以它查不出"manifest 自洽但 PLY 被换了"这类问题
    (那要另跑完整性校验)。
  - 精度只从 sim3.alignment.v1 证据串里读。生产者用别的证据 (如测量标尺) 挣得米制时,
    这里只能如实说"精度未知"。

用法:
    python scripts/inspect_recon.py web/data/recon/recon_manifest.json
    python scripts/inspect_recon.py web/data/recon/recon_manifest.json --json

退出码: 0 = 读通了; 2 = manifest 自相矛盾 (可当 CI 门用); 1 = 文件不存在/不是合法 JSON。
零依赖 GPU, 纯读 JSON。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pipeline.recon_schema import Sim3AlignmentEvidence

_SIM3_EVIDENCE_PREFIX = "sim3.alignment.v1="
_METRIC_CLAIMS = ("metric-aligned", "metric-unaligned")

# 单位标签: 绝不把未知/任意单位说成米。
_UNIT_LABEL = {"meters": "米", "arbitrary": "任意单位", "unknown": "未知单位"}


def _dict(value: Any) -> dict:
    """把可能缺失/类型错误的字段安全读成 dict (外来 manifest 不可信)。"""
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _triple(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    coords = [_number(item) for item in value]
    return None if any(c is None for c in coords) else coords  # type: ignore[return-value]


def _fmt(value: float | None) -> str:
    return "未知" if value is None else f"{value:g}"


def _unit_label(units: str) -> str:
    return _UNIT_LABEL.get(units, f"{units} (非标准单位标签)")


# ============ 各段解读 ============


def _read_geometry(manifest: dict) -> dict:
    contract = _dict(manifest.get("coordinate_contract"))
    target = _dict(contract.get("target_frame"))
    spatial = _dict(manifest.get("spatial_parameters"))
    # 单位以坐标契约为准; 契约缺席才退回 spatial_parameters; 都没有就是未知 —— 不默认米。
    units = target.get("units") or spatial.get("units") or "unknown"
    if not isinstance(units, str):
        units = "unknown"

    bounds = _dict(manifest.get("bounds"))
    lo, hi = _triple(bounds.get("min")), _triple(bounds.get("max"))
    size = [h - low for low, h in zip(lo, hi, strict=True)] if lo and hi else None

    lod = _dict(manifest.get("lod"))
    chunks_raw = _dict(manifest.get("artifacts")).get("chunks")
    chunks = None
    if isinstance(chunks_raw, dict):
        chunks = {
            "manifest": chunks_raw.get("manifest"),
            "total_chunks": chunks_raw.get("total_chunks"),
            "chunk_size_m": _number(chunks_raw.get("chunk_size_m")),
            "total_points": chunks_raw.get("total_points"),
        }

    return {
        "gaussian_count": manifest.get("gaussian_count")
        if isinstance(manifest.get("gaussian_count"), int)
        and not isinstance(manifest.get("gaussian_count"), bool)
        else None,
        "bounds_min": lo,
        "bounds_max": hi,
        "bounds_size": size,
        "units": units,
        "lod_levels": sorted(str(level) for level in lod),
        "chunks": chunks,
    }


def _read_alignment_evidence(metric_evidence: list[str]) -> tuple[list[dict], list[str]]:
    """解析 metric_evidence 里的 sim3.alignment.v1 记录。

    返回 (已解析记录, 无法解析的原因)。无法解析 = 无法验证 → 调用方按不可信处理
    (与 reconstruct._alignment_evidence_consistent 同一语义, 不是"忽略坏证据")。
    """
    records: list[dict] = []
    unparseable: list[str] = []
    for item in metric_evidence:
        if not isinstance(item, str) or not item.startswith(_SIM3_EVIDENCE_PREFIX):
            continue
        try:
            parsed = Sim3AlignmentEvidence.parse(item)
        except (ValueError, ValidationError) as exc:
            unparseable.append(f"{item[:60]}... ({type(exc).__name__})")
            continue
        records.append({
            "method": parsed.method,
            "n_control_points": parsed.n_control_points,
            "rms_residual_m": parsed.rms_residual_m,
            "max_residual_m": parsed.max_residual_m,
            "max_rms_threshold_m": parsed.max_rms_threshold_m,
            "passed": parsed.passed,
        })
    return records, unparseable


def _find_contradictions(
    declared: str | None,
    target: dict,
    alignment_status: Any,
    metric_evidence: list[str],
    records: list[dict],
    unparseable: list[str],
    synthetic: Any,
) -> list[str]:
    """列出 manifest 内部自相矛盾之处 —— 只针对**提升信任的声称** (metric-*)。

    镜像 reconstruct._derive_geometry_usability 的判据: 生产者绝不会在这些条件下发出
    metric-* 标签, 所以出现即意味着产物是外来的/被篡改的/由旧版有 bug 的代码产出。
    preview-* 声称已是最保守标签, 不需要"矛盾"降级。
    """
    if declared not in _METRIC_CLAIMS:
        return []

    issues: list[str] = []
    for reason in unparseable:
        issues.append(
            f"声称 {declared}, 但 metric_evidence 里的 sim3.alignment.v1 无法解析 → "
            f"无法验证米制主张, 按不可信处理: {reason}"
        )
    for record in records:
        if not record["passed"]:
            issues.append(
                f"声称 {declared}, 但对齐证据自报 RMS 门**未通过** "
                f"(passed=false, rms {record['rms_residual_m']:g} 米 vs 阈值 "
                f"{record['max_rms_threshold_m']:g} 米) → 诚实的生产者绝不会在门未过时发出 "
                f"{declared}; 手里的证据打败声称"
            )
    if not metric_evidence:
        issues.append(f"声称 {declared}, 但 metric_evidence 为空 → 米制主张没有任何证据支撑")
    units, metric_status = target.get("units"), target.get("metric_status")
    if units != "meters" or metric_status != "metric":
        issues.append(
            f"声称 {declared}, 但 target_frame 是 units={units!r} / "
            f"metric_status={metric_status!r} → 不是米制帧"
        )
    if synthetic is True:
        issues.append(
            f"声称 {declared}, 但 provenance.synthetic=true → 合成几何只能是 preview-proxy, "
            f"即使它的数字看起来像米制"
        )
    geo_aligned = target.get("geo_aligned")
    if declared == "metric-aligned" and not (
        geo_aligned == "aligned" and alignment_status == "aligned"
    ):
        issues.append(
            f"声称 metric-aligned, 但 target_frame.geo_aligned={geo_aligned!r} / "
            f"alignment_status={alignment_status!r} → 没有挣得地理对齐"
        )
    if declared == "metric-unaligned" and (
        geo_aligned == "aligned" or alignment_status == "aligned"
    ):
        issues.append(
            f"声称 metric-unaligned, 但 target_frame.geo_aligned={geo_aligned!r} / "
            f"alignment_status={alignment_status!r} 自称已对齐 → 标签与契约不一致"
        )
    return issues


def _describe_transform_chain(chain: list[dict], pose_id: Any, target_id: Any) -> str:
    method_names = {
        "identity": "恒等", "gps-anchor": "GPS 锚点", "synthetic-layout": "合成布局",
        "external-sim3": "外部 Sim3", "control-points": "控制点", "unknown": "未知方法",
    }
    if not chain:
        if pose_id is None and target_id is None:
            return "无变换链信息 (未知)"
        return f"无变换 (位姿帧 {pose_id} 即世界帧, 没有做过任何 Sim3 变换)"
    source = chain[0].get("source_frame", pose_id)
    target = chain[-1].get("target_frame", target_id)
    methods = "、".join(
        method_names.get(step.get("method"), str(step.get("method"))) for step in chain
    )
    return f"{source} 经 {len(chain)} 次 Sim3 变换 ({methods}) 到 {target}"


def _measurability_summary(effective: str, accuracy: dict | None, *,
                           contradicted: bool = False) -> str:
    # 被降级的产物 != 诚实的 preview-only: 我们只知道"米制主张无法验证", **不知道**尺度真是
    # 任意的。断言后者同样是没证据的编造, 只是方向相反。
    if contradicted:
        return "不能测量: 米制主张与 manifest 自带的证据矛盾, 无法验证 → 按不可信处理"
    if effective == "metric-aligned":
        if accuracy is None:
            return "真实尺度 + 地理对齐, 可测量 —— 但对齐精度未知 (见下)"
        return (
            f"真实尺度 + 地理对齐, 可测量 (对齐残差 {accuracy['rms_residual_m']:g} 米)"
        )
    if effective == "metric-unaligned":
        return "有真实米制尺度, 但没有地理方向/绝对位置"
    if effective == "preview-proxy":
        return "合成占位几何, 不是真实重建"
    if effective == "preview-only":
        return "不能测量: 尺度是任意的, 只能看"
    return "未知: manifest 没说这几何能不能用来测量"


def _upgrade_path(effective: str, *, contradicted: bool = False) -> str | None:
    if contradicted:
        return (
            "别信这份产物的米制标签: 回到源头 (原始 SfM 结果 + 控制点) 重新跑 alignment 并"
            "重新产出 manifest; 它现在的标签与证据对不上, 修不了标签只能重做"
        )
    if effective == "metric-aligned":
        return None
    if effective == "metric-unaligned":
        return (
            "想要地理方向: 提供 ≥3 个控制点 (可见地标的 ENU/GPS 坐标) 或 GPS 标记走 alignment "
            "拟合 Sim3; 通过 RMS 门后才升级为 metric-aligned"
        )
    if effective == "preview-proxy":
        return "这是合成占位几何; 只有用真实拍摄跑 SfM + 云 GPU 3DGS 训练才会得到真实几何"
    if effective == "preview-only":
        return (
            "想要可测量: 提供 ≥3 个控制点 (可见地标的已知 ENU/GPS 坐标) 走 alignment 拟合 "
            "Sim3; 只有通过 RMS 门才会升级为 metric-aligned (拟合不过就仍是 preview-only)"
        )
    return (
        "manifest 缺 provenance.geometry_usability; 用本仓库的 reconstruct 重新产出"
        "带坐标契约的 manifest (缺席即未知, 不能靠数字长相反推)"
    )


def _collect_unknowns(geometry: dict, declared: str | None, target: dict,
                      metric_evidence: list[str], accuracy: dict | None,
                      can_measure: bool) -> list[str]:
    unknowns: list[str] = []
    if geometry["gaussian_count"] is None:
        unknowns.append("高斯数未知 (manifest 缺 gaussian_count)")
    if geometry["bounds_size"] is None:
        unknowns.append("包围盒未知 (manifest 缺 bounds.min/max 或格式不对)")
    if geometry["units"] == "unknown":
        unknowns.append("单位未知 (缺 coordinate_contract.target_frame.units) —— 不假定是米")
    if not target:
        unknowns.append("坐标契约未知 (缺 coordinate_contract.target_frame)")
    if declared is None:
        unknowns.append("可测量性未知 (缺 provenance.geometry_usability)")
    if not metric_evidence:
        unknowns.append("没有任何 metric 证据 (metric_evidence 为空)")
    if can_measure and accuracy is None:
        unknowns.append(
            "对齐精度未知 (metric_evidence 里没有 sim3.alignment.v1 记录) —— "
            "米制可能靠别的证据挣得; 本工具不猜残差数字"
        )
    return unknowns


def inspect(manifest: dict) -> dict:
    """把 recon_manifest.json 的 dict 翻译成人话结论 (纯函数, 不改入参)。

    返回的 dict 全部 JSON 可序列化。declared_usability 原样搬运 manifest 的声称;
    effective_usability 是**按证据**该信的判定 —— 两者不同即意味着 manifest 自相矛盾。
    """
    contract = _dict(manifest.get("coordinate_contract"))
    provenance = _dict(manifest.get("provenance"))
    target = _dict(contract.get("target_frame"))
    pose = _dict(contract.get("pose_frame"))

    raw_evidence = contract.get("metric_evidence")
    metric_evidence = list(raw_evidence) if isinstance(raw_evidence, list) else []
    raw_chain = contract.get("transform_chain")
    chain = [step for step in raw_chain if isinstance(step, dict)] if isinstance(
        raw_chain, list) else []

    declared = provenance.get("geometry_usability")
    if not isinstance(declared, str):
        declared = None
    alignment_status = contract.get("alignment_status")

    geometry = _read_geometry(manifest)
    records, unparseable = _read_alignment_evidence(metric_evidence)
    contradictions = _find_contradictions(
        declared, target, alignment_status, metric_evidence, records, unparseable,
        provenance.get("synthetic"),
    )

    # fail-closed: 矛盾 → 降级到 preview-only; 缺声称 → unknown。绝不因"看起来像米"而升级。
    if declared is None:
        effective = "unknown"
    elif contradictions:
        effective = "preview-only"
    else:
        effective = declared
    can_measure = effective in _METRIC_CLAIMS

    # 多条对齐证据时取**最差**的 rms 作为精度上界 (保守): 能测量的精细度由最粗的那条决定。
    accuracy = max(records, key=lambda r: r["rms_residual_m"]) if records else None
    if accuracy is not None and not can_measure:
        # 不可信的对齐记录不作为"精度"呈现, 只在 notes 里说明它失败过。
        accuracy = None

    notes: list[str] = []
    for record in records:
        if not record["passed"]:
            rms, threshold = record["rms_residual_m"], record["max_rms_threshold_m"]
            # 只复述记录里**写着**的数, 不替它编"因为超阈值所以没过"的因果 —— 那个不等式
            # 可能根本不成立 (见下), 编出来就是好看但没根据的结论。
            note = (
                f"对齐拟合的 RMS 门**未通过** (证据自报 passed=false; 记录 rms {rms:g} 米, "
                f"阈值 {threshold:g} 米, {record['n_control_points']} 个控制点)"
            )
            if rms <= threshold:
                # 真实拟合器按 rms<=阈值 置 passed, 这种记录它产不出来。
                note += (
                    " —— 但记录里 rms 并未超阈值, 该证据**自身就不自洽**, 可能被改过; "
                    "无论如何, 自报没过就按没过处理"
                )
            notes.append(note)
    if len(records) > 1 and accuracy is not None:
        notes.append(f"有 {len(records)} 条对齐证据, 精度按最差的一条报 (保守)")

    if effective == "metric-aligned":
        geo_aligned: bool | None = True
    elif effective == "metric-unaligned":
        geo_aligned = False
    else:
        geo_aligned = {"aligned": True, "unaligned": False}.get(target.get("geo_aligned"))

    result = {
        "geometry": geometry,
        "measurability": {
            "declared_usability": declared,
            "effective_usability": effective,
            "can_measure": can_measure,
            "geo_aligned": geo_aligned,
            "accuracy": accuracy,
            "accuracy_known": accuracy is not None,
            "summary": _measurability_summary(
                effective, accuracy, contradicted=bool(contradictions)),
            "upgrade_path": _upgrade_path(effective, contradicted=bool(contradictions)),
            "notes": notes,
        },
        "coordinate_contract": {
            "pose_frame_id": pose.get("frame_id"),
            "target_frame_id": target.get("frame_id"),
            "units": geometry["units"],
            "geo_aligned": target.get("geo_aligned"),
            "alignment_status": alignment_status,
            "transform_chain": chain,
            "transform_summary": _describe_transform_chain(
                chain, pose.get("frame_id"), target.get("frame_id")),
        },
        "trust": {
            "evidence": metric_evidence,
            "unknowns": _collect_unknowns(
                geometry, declared, target, metric_evidence, accuracy, can_measure),
        },
        "contradictions": contradictions,
        "self_consistent": not contradictions,
    }
    result["report"] = _render_report(result)
    return result


# ============ 人话渲染 ============


def _render_report(result: dict) -> list[str]:
    geometry, measurability = result["geometry"], result["measurability"]
    contract, trust = result["coordinate_contract"], result["trust"]
    units_label = _unit_label(geometry["units"])
    lines: list[str] = ["== 这是什么 =="]

    count = geometry["gaussian_count"]
    lines.append(f"高斯数: {f'{count:,}' if count is not None else '未知'}")
    size = geometry["bounds_size"]
    if size is None:
        lines.append("包围盒: 未知 (manifest 没给 bounds)")
    else:
        dims = " x ".join(_fmt(value) for value in size)
        suffix = "" if geometry["units"] == "meters" else " —— 不是米, 别拿去量"
        lines.append(f"包围盒尺寸: {dims} {units_label}{suffix}")
    lines.append(f"LOD 层级: {', '.join(geometry['lod_levels']) or '未知/无'}")
    chunks = geometry["chunks"]
    if chunks is None:
        lines.append("分块产物: 无 (未做空间分块)")
    else:
        lines.append(
            f"分块产物: {chunks['total_chunks']} 块 ({_fmt(chunks['chunk_size_m'])}m 网格), "
            f"共 {chunks['total_points']} 点 → {chunks['manifest']}"
        )

    lines.append("")
    lines.append("== 能不能测量 (最重要) ==")
    tag = "[可测量]" if measurability["can_measure"] else "[不能测量]"
    lines.append(f"{tag} {measurability['summary']}")
    accuracy = measurability["accuracy"]
    if accuracy is not None:
        lines.append(
            f"实际精度: {accuracy['n_control_points']} 个控制点, RMS 残差 "
            f"{accuracy['rms_residual_m']:g} 米 (最大 {accuracy['max_residual_m']:g} 米, "
            f"门限 {accuracy['max_rms_threshold_m']:g} 米)"
        )
        lines.append(
            f"→ 别做比 {accuracy['rms_residual_m']:g} 米更精细的测量; 这是对齐残差, 不是渲染误差"
        )
    elif measurability["can_measure"]:
        lines.append("实际精度: 未知 (没有 sim3.alignment.v1 证据) → 不要假设它精确到厘米")
    for note in measurability["notes"]:
        lines.append(f"注意: {note}")
    if measurability["upgrade_path"]:
        lines.append(f"怎么升级: {measurability['upgrade_path']}")
    if measurability["declared_usability"] is not None:
        lines.append(f"(manifest 原文 geometry_usability={measurability['declared_usability']})")

    lines.append("")
    lines.append("== 坐标契约 ==")
    lines.append(f"位姿帧: {contract['pose_frame_id'] or '未知'} → "
                 f"世界帧: {contract['target_frame_id'] or '未知'}")
    lines.append(f"单位: {units_label} | 地理对齐: {contract['geo_aligned'] or '未知'} | "
                 f"alignment_status: {contract['alignment_status'] or '未知'}")
    lines.append(f"变换链: {contract['transform_summary']}")

    lines.append("")
    lines.append("== 可信度来源 ==")
    if trust["evidence"]:
        lines.append("有这些证据:")
        for item in trust["evidence"]:
            text = str(item)
            if text.startswith(_SIM3_EVIDENCE_PREFIX):
                text = "sim3.alignment.v1 (Sim3 对齐拟合记录, 精度见上)"
            elif len(text) > 80:
                text = text[:77] + "..."
            lines.append(f"  - {text}")
    else:
        lines.append("没有任何证据串 (metric_evidence 为空)")
    if trust["unknowns"]:
        lines.append("这些是未知 (未知就是未知, 不编造):")
        for unknown in trust["unknowns"]:
            lines.append(f"  - {unknown}")

    if result["contradictions"]:
        lines.append("")
        lines.append("== 自相矛盾: manifest 不可信 (已按不可信处理) ==")
        for issue in result["contradictions"]:
            lines.append(f"  ! {issue}")
        lines.append(
            f"→ 声称 {measurability['declared_usability']}, 但按证据只能当 "
            f"{measurability['effective_usability']} 用。这个产物可能来自外部/被篡改/旧版有 bug "
            f"的代码; 别拿它测量, 回到源头重新对齐。"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把 recon_manifest.json 翻译成人话: 这东西到底能不能用来测量")
    ap.add_argument("manifest", type=Path, help="recon_manifest.json 路径")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="输出机器可读 JSON (默认输出人话报告)")
    args = ap.parse_args(argv)

    if not args.manifest.is_file():
        raise SystemExit(f"文件不存在: {args.manifest}")
    try:
        parsed = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"不是合法 JSON: {args.manifest}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"recon_manifest 必须是一个 JSON 对象, 实际是 {type(parsed).__name__}")

    result = inspect(parsed)
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("\n".join(result["report"]))
    # 矛盾 → 非零退出, 让 CI/脚本能把它当门用 (人话报告已说明矛盾在哪)。
    return 2 if result["contradictions"] else 0


if __name__ == "__main__":
    sys.exit(main())
