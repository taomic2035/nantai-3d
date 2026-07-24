#!/usr/bin/env python3
"""对 ``recon_manifest.json`` 声明的每个产物做字节级完整性校验。

为什么: ``scripts/inspect_recon.py`` 故意**不碰 PLY 字节** (它只在 manifest
声称 + 内部自洽性层面翻译"能不能量")。所以"manifest 自洽但 PLY 被换了"这类
篡改它能漏掉。本脚本是那个缺口的补丁: 实际重算每个 ``artifacts.*`` 的 SHA-256
和字节数, 走 ``chunks.json`` 的每个 chunk PLY, 拒绝符号链接/路径逃逸/重复路径/
重复 JSON key, 并复述 manifest 内部矛盾 (与 inspect_recon 同一 fail-closed 规则)。

铁律 (与 pipeline.reconstruction_artifact_integrity 一致):
  - **绝不提升信任**。``preview-only`` 即便每个字节都对也仍是 ``preview-only``;
    ``metric-aligned`` 仍是 ``metric-aligned`` (不升为 "verified metric-aligned")。
    字节校验和坐标信任是两件事。
  - **篡改 fail-closed**。符号链接 manifest/产物、路径逃逸、文件缺失、SHA 漂移、
    重复路径、重复 JSON key、chunk 数不符、bounds 不一致 —— 全部报告, 无一静默。
  - **只读不写**。本脚本不修改 manifest, 不动任何产物。

限制 (明说):
  - ``chunks.json`` 当前 schema 没有 per-chunk SHA (只有 manifest 级
    ``source.recon_manifest_sha256`` 证明整体)。所以本脚本对 chunk PLY 只能验证
    "存在/不是符号链接/路径在 chunks 目录内/计数和 bounds 自洽", 不能验字节篡改;
    该缺口在 ``ChunksReport.per_chunk_sha_verified=False`` 显式标出。
  - 不重算 Sim3 残差, 不重跑 COLMAP。米制矛盾仅靠解析
    ``sim3.alignment.v1=<json>`` 证据串 (与 inspect_recon 同源规则)。

用法:
    python scripts/verify_recon_artifacts.py web/data/recon/recon_manifest.json
    python scripts/verify_recon_artifacts.py web/data/recon/recon_manifest.json --json

退出码 (与 inspect_recon 一致, 可当 CI 门):
    0 = 全部产物 SHA+字节匹配, 无路径安全问题, 无矛盾, 无 chunks 异常
    2 = 发现任何 mismatch / 路径安全违规 / chunks 异常 / 矛盾
    (文件不存在/不是合法 JSON/symlink manifest 等致命错误经 SystemExit 抛出,
     shell 看到 exit code 1)

零依赖 GPU, 纯读 JSON + 计算文件 SHA-256。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.reconstruction_artifact_integrity import (
    ArtifactMismatch,
    ArtifactUnknown,
    ArtifactVerification,
    ChunksReport,
    IntegrityReport,
    PathSafetyViolation,
    verify_recon_artifacts,
)

# 1 MiB reads are a good trade-off for SHA-256 on spinning disks and SSDs.
_SHA256_READ_CHUNK = 1 << 20


def _format_bytes(n: int) -> str:
    """Human-readable byte size (binary units, no fake precision)."""
    if n < 1024:
        return f"{n} B"
    units = ["KiB", "MiB", "GiB", "TiB"]
    size = float(n)
    for unit in units:
        size /= 1024.0
        if size < 1024.0:
            return f"{size:.2f} {unit}"
    return f"{size:.2f} PiB"


def _truncate_sha(sha: str) -> str:
    """Show first 12 + last 8 hex chars for readability; full SHA in --json."""
    if len(sha) != 64:
        return sha
    return f"{sha[:12]}…{sha[-8:]}"


def _render_verified(verified: list[ArtifactVerification]) -> list[str]:
    lines: list[str] = []
    if not verified:
        lines.append("  (无)")
        return lines
    for v in verified:
        lines.append(
            f"  ✓ {v.artifact_key:<10} {v.kind:<14} {v.fidelity:<20} "
            f"{_format_bytes(v.actual_bytes)}  sha={_truncate_sha(v.actual_sha256)}"
        )
    return lines


def _render_mismatch(mismatches: list[ArtifactMismatch]) -> list[str]:
    lines: list[str] = []
    if not mismatches:
        return lines
    lines.append("== 不匹配 (manifest 声称的 SHA/字节与实际文件对不上) ==")
    for m in mismatches:
        lines.append(f"  ✗ {m.artifact_key}  路径: {m.path}")
        if not m.sha256_match:
            lines.append(
                f"      SHA-256 漂移: 声称 {_truncate_sha(m.declared_sha256)} "
                f"vs 实际 {_truncate_sha(m.actual_sha256)}"
            )
        if not m.size_match:
            lines.append(
                f"      字节数漂移: 声称 {m.declared_bytes} vs 实际 {m.actual_bytes}"
            )
        lines.append("      → 文件可能被篡改/替换; 别信这份产物, 回到源头重产出")
    return lines


def _render_unknown(unknowns: list[ArtifactUnknown]) -> list[str]:
    lines: list[str] = []
    if not unknowns:
        return lines
    lines.append("== 无法校验 (manifest 没给 SHA 或格式不对) ==")
    for u in unknowns:
        lines.append(f"  ? {u.artifact_key}  路径: {u.path}")
        lines.append(f"      原因: {u.reason}")
    return lines


def _render_path_safety(
    violations: list[PathSafetyViolation],
) -> list[str]:
    lines: list[str] = []
    if not violations:
        return lines
    lines.append("== 路径安全违规 (符号链接/路径逃逸/文件缺失) ==")
    for v in violations:
        lines.append(f"  ! {v.artifact_key}  路径: {v.path}")
        lines.append(f"      原因: {v.reason}")
    return lines


def _render_chunks(chunks: ChunksReport | None) -> list[str]:
    if chunks is None:
        return []
    lines: list[str] = ["== 分块产物 (chunks.json) =="]
    lines.append(f"  分块清单: {chunks.chunks_manifest_path}")
    lines.append(f"  分块总数: {chunks.total_chunks}")
    if not chunks.total_chunks_matches_len:
        lines.append(
            "  ! total_chunks 与 chunks 数组长度不一致"
            f" (declared={chunks.total_chunks}, matches_len=False)"
        )
    lines.append(f"  总点数: {chunks.total_points}")
    if not chunks.total_points_matches_sum:
        lines.append(
            "  ! total_points 与各分块 point_count 之和对不上"
            f" (summed={chunks.total_points}, matches_sum=False)"
        )
    lines.append(
        f"  bounds 与 AABB 一致: {chunks.bounds_consistent_with_aabbs}"
    )
    lines.append(f"  已验证存在的分块文件: {chunks.verified_chunk_files}")
    if chunks.missing_chunk_files:
        lines.append(f"  ! 缺失的分块文件 ({len(chunks.missing_chunk_files)}):")
        for fname in chunks.missing_chunk_files:
            lines.append(f"      - {fname}")
    if chunks.duplicate_chunk_paths:
        lines.append(
            f"  ! 不同分块引用了同一文件 ({len(chunks.duplicate_chunk_paths)}):"
        )
        for fname in chunks.duplicate_chunk_paths:
            lines.append(f"      - {fname}")
    if chunks.extra_unbound_chunk_files:
        lines.append(
            f"  ! chunks 目录存在未被任何分块引用的 PLY "
            f"({len(chunks.extra_unbound_chunk_files)}):"
        )
        for fname in chunks.extra_unbound_chunk_files:
            lines.append(f"      - {fname}")
    # 铁律: 明示这个缺口, 不让"已验证存在"被误读成"字节已校验"
    lines.append(
        "  注意: chunks.json schema 无 per-chunk SHA; "
        f"per_chunk_sha_verified={chunks.per_chunk_sha_verified}"
    )
    return lines


def _render_duplicates(
    duplicate_paths: list[str],
    duplicate_json_keys: list[str],
) -> list[str]:
    lines: list[str] = []
    if duplicate_paths:
        lines.append("== 重复路径 (多个 artifact 指向同一文件) ==")
        for p in duplicate_paths:
            lines.append(f"  ! {p}")
    if duplicate_json_keys:
        lines.append("== 重复 JSON key (manifest 里同一对象有重复键) ==")
        for k in duplicate_json_keys:
            lines.append(f"  ! {k}")
    return lines


def _render_contradictions(contradictions: list[str]) -> list[str]:
    if not contradictions:
        return []
    lines: list[str] = ["== manifest 自相矛盾 (信任不会提升) =="]
    for c in contradictions:
        lines.append(f"  ! {c}")
    return lines


def _render_report(report: IntegrityReport) -> list[str]:
    lines: list[str] = []
    lines.append("== 重建产物完整性校验 ==")
    lines.append(f"manifest: {report.manifest_path}")
    if report.schema_version is not None:
        lines.append(f"schema_version: {report.schema_version}")
    if report.engine is not None:
        lines.append(f"engine: {report.engine}")
    if report.geometry_usability is not None:
        lines.append(f"geometry_usability: {report.geometry_usability}")
    lines.append("")

    lines.append("== 已验证产物 (SHA-256 + 字节数都对得上) ==")
    lines.extend(_render_verified(report.verified))

    if report.mismatch:
        lines.append("")
        lines.extend(_render_mismatch(report.mismatch))
    if report.unknown:
        lines.append("")
        lines.extend(_render_unknown(report.unknown))
    if report.path_safety_violations:
        lines.append("")
        lines.extend(_render_path_safety(report.path_safety_violations))
    if report.chunks_report is not None:
        lines.append("")
        lines.extend(_render_chunks(report.chunks_report))
    extra_dup = _render_duplicates(
        report.duplicate_paths, report.duplicate_json_keys
    )
    if extra_dup:
        lines.append("")
        lines.extend(extra_dup)
    if report.contradictions:
        lines.append("")
        lines.extend(_render_contradictions(report.contradictions))

    lines.append("")
    lines.append("== 信任保留 ==")
    lines.append(
        f"trust_preserved={report.trust_preserved} "
        "(字节校验只确认文件未被替换; 不提升 preview-only / metric / 训练信任)"
    )
    return lines


def _has_problems(report: IntegrityReport) -> bool:
    """Return True if any finding warrants CI exit code 2."""
    if report.mismatch:
        return True
    if report.path_safety_violations:
        return True
    if report.duplicate_paths:
        return True
    if report.duplicate_json_keys:
        return True
    if report.contradictions:
        return True
    chunks = report.chunks_report
    if chunks is not None:
        if not chunks.total_chunks_matches_len:
            return True
        if not chunks.total_points_matches_sum:
            return True
        if not chunks.bounds_consistent_with_aabbs:
            return True
        if chunks.missing_chunk_files:
            return True
        if chunks.duplicate_chunk_paths:
            return True
        if chunks.extra_unbound_chunk_files:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "对 recon_manifest.json 声明的每个产物做字节级完整性校验 "
            "(补 inspect_recon 不碰 PLY 字节的缺口)"
        )
    )
    ap.add_argument(
        "manifest", type=Path, help="recon_manifest.json 路径"
    )
    ap.add_argument(
        "--json", action="store_true", dest="as_json",
        help="输出机器可读 JSON (默认输出人话报告)",
    )
    args = ap.parse_args(argv)

    try:
        report = verify_recon_artifacts(args.manifest)
    except FileNotFoundError as exc:
        raise SystemExit(f"文件不存在: {exc}") from exc
    except ValueError as exc:
        # symlink manifest / 非 dict / 等 fail-closed 拒绝
        raise SystemExit(f"manifest 不可校验: {exc}") from exc

    if args.as_json:
        print(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))
    else:
        print("\n".join(_render_report(report)))

    # 与 inspect_recon 一致: 发现问题 → 退出码 2, 可当 CI 门用。
    return 2 if _has_problems(report) else 0


if __name__ == "__main__":
    sys.exit(main())
