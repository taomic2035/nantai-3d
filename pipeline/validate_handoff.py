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
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Literal

import numpy as np
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from pipeline.assets import ASSET_ID_PATTERN
from pipeline.durable_io import _is_linklike, first_linklike_path
from pipeline.gaussian_scene import GaussianScene

# 验收阈值
MIN_GAUSSIANS = 200
MAX_GAUSSIANS = 500_000
FOOTPRINT_TOLERANCE = 0.5  # 实际尺寸与声明 footprint 允许 ±50%
GROUND_Z_TOLERANCE = 1.0  # 最低点距 z=0 允许偏差 (米)
MIN_COLOR_STD = 0.01  # 颜色标准差下限 (拒绝纯色废料)
SCALE_RANGE = (0.003, 2.0)  # 高斯尺寸中位数合理区间 (米)


class DeliverableItem(BaseModel):
    # kind 与 assets.AssetEntry.kind 同枚举, 保证验收通过后 --register 不会再校验失败
    asset_id: str = Field(pattern=ASSET_ID_PATTERN)
    kind: Literal["building", "vegetation", "prop", "ground", "other"] = "other"
    ply: str = Field(min_length=1)
    footprint_m: tuple[float, float, float] | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("footprint_m")
    @classmethod
    def validate_footprint(cls, value):
        if value is None:
            return value
        values = np.asarray(value, dtype=np.float64)
        if not np.all(np.isfinite(values)) or np.any(values <= 0):
            raise ValueError("footprint_m 必须是三个有限正数")
        return tuple(float(item) for item in values)


class GeneratorInfo(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    script_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class AssetCoordinateSystem(BaseModel):
    """The only registerable asset-local coordinate convention in v2."""

    units: Literal["meters"]
    axes: Literal["local-z-up"]


class DeliverableManifest(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    handoff_id: str
    coordinate_system: AssetCoordinateSystem | None = None
    generator: GeneratorInfo | None = None
    items: list[DeliverableItem] = Field(min_length=1)

    @model_validator(mode="after")
    def require_v2_integrity_fields(self):
        if self.schema_version >= 2:
            if self.coordinate_system is None:
                raise ValueError(
                    "schema_version 2 requires coordinate_system with "
                    "units=meters and axes=local-z-up"
                )
            if self.generator is None:
                raise ValueError("schema_version 2 requires generator metadata")
            missing = [item.asset_id for item in self.items if not item.sha256]
            if missing:
                raise ValueError(
                    "schema_version 2 requires sha256 for every item; missing: "
                    + ", ".join(missing)
                )
            missing_footprints = [item.asset_id for item in self.items if item.footprint_m is None]
            if missing_footprints:
                raise ValueError(
                    "schema_version 2 requires footprint_m for every item; missing: "
                    + ", ".join(missing_footprints)
                )
        ids = [item.asset_id for item in self.items]
        duplicates = sorted({asset_id for asset_id in ids if ids.count(asset_id) > 1})
        if duplicates:
            raise ValueError("重复 asset_id: " + ", ".join(duplicates))
        return self


class _HandoffIntegrityError(Exception):
    """Raised when a deliverable file cannot be securely hashed."""


def _cross_surface_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int]:
    """Identity stable across lstat and fstat on Windows/POSIX."""

    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
    )


def _sha256_file(path: Path) -> str:
    """Hash *path* via a single descriptor with ancestor and swap checks.

    Rejects symlink/junction/reparse-point ancestors, leaf links, and file
    swaps before/during the hash.  Returns the hex digest.
    """
    descriptor = -1
    try:
        redirected = first_linklike_path(Path(path.absolute().anchor), path)
        before = path.lstat()
    except OSError as exc:
        raise _HandoffIntegrityError("file cannot be inspected") from exc
    except ValueError as exc:
        raise _HandoffIntegrityError("file cannot be inspected") from exc
    if (
        redirected is not None
        or _is_linklike(path, observed=before)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise _HandoffIntegrityError("file is not a regular non-link file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _HandoffIntegrityError("file cannot be opened") from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise _HandoffIntegrityError("file cannot be opened") from exc
    try:
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if _cross_surface_signature(descriptor_before) != _cross_surface_signature(before):
                raise _HandoffIntegrityError("file changed before hash")
            digest = hashlib.sha256()
            while True:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
            descriptor_after = os.fstat(stream.fileno())
        after = path.lstat()
    except _HandoffIntegrityError:
        raise
    except OSError as exc:
        raise _HandoffIntegrityError("file cannot be hashed") from exc
    if (
        before.st_mode != after.st_mode
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or descriptor_before.st_mode != descriptor_after.st_mode
        or descriptor_before.st_dev != descriptor_after.st_dev
        or descriptor_before.st_ino != descriptor_after.st_ino
        or descriptor_before.st_size != descriptor_after.st_size
        or descriptor_before.st_mtime_ns != descriptor_after.st_mtime_ns
    ):
        raise _HandoffIntegrityError("file changed while being hashed")
    return digest.hexdigest()


_MAX_HANDOFF_FILE_BYTES = 16 * 1024 * 1024


def _read_stable_bytes(
    path: Path,
    *,
    label: str,
    max_bytes: int = _MAX_HANDOFF_FILE_BYTES,
) -> bytes:
    """Read a trust-critical file via a single secure descriptor.

    The check-then-reopen pattern (``is_file`` then ``read_text``) leaves a
    TOCTOU window where the file can be swapped between validation and
    reading. This helper binds a single descriptor from ``os.open`` with
    ``O_NOFOLLOW`` for the entire read and rechecks file identity before
    and after reading to close that window. Ancestor reparse points are
    rejected via ``first_linklike_path``.
    """
    descriptor = -1
    try:
        redirected = first_linklike_path(Path(path.absolute().anchor), path)
        before = path.lstat()
    except OSError as exc:
        raise _HandoffIntegrityError(f"{label} cannot be inspected") from exc
    except ValueError as exc:
        raise _HandoffIntegrityError(f"{label} cannot be inspected") from exc
    if (
        redirected is not None
        or _is_linklike(path, observed=before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size > max_bytes
    ):
        raise _HandoffIntegrityError(f"{label} is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _HandoffIntegrityError(f"{label} cannot be opened") from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise _HandoffIntegrityError(f"{label} cannot be opened") from exc
    payload = bytearray()
    try:
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if _cross_surface_signature(descriptor_before) != _cross_surface_signature(before):
                raise _HandoffIntegrityError(f"{label} changed before read")
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise _HandoffIntegrityError(f"{label} exceeds byte limit")
            descriptor_after = os.fstat(stream.fileno())
        after = path.lstat()
    except _HandoffIntegrityError:
        raise
    except OSError as exc:
        raise _HandoffIntegrityError(f"{label} cannot be read") from exc
    if (
        _cross_surface_signature(before) != _cross_surface_signature(after)
        or _cross_surface_signature(descriptor_before) != _cross_surface_signature(descriptor_after)
        or len(payload) != before.st_size
    ):
        raise _HandoffIntegrityError(f"{label} changed while being read")
    return bytes(payload)


def check_item(item: DeliverableItem, base_dir: Path) -> list[str]:
    """单个素材的全部检查, 返回问题列表 (空 = PASS)"""
    problems: list[str] = []
    ply_path = base_dir / item.ply
    # 防路径穿越: manifest 里的 ply 必须落在交付目录内 (resolve 仅用于穿越检测)
    if not ply_path.resolve().is_relative_to(base_dir.resolve()):
        return [f"ply 路径越出交付目录: {item.ply}"]
    if not ply_path.exists():
        return [f"ply 文件缺失: {item.ply}"]
    if item.sha256:
        try:
            actual_sha = _sha256_file(ply_path.absolute())
        except _HandoffIntegrityError:
            return [f"ply 完整性校验失败: {item.ply}"]
        if actual_sha != item.sha256:
            return [f"SHA-256 不匹配: manifest={item.sha256}, actual={actual_sha}"]

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
                <= actual
                <= declared * (1 + FOOTPRINT_TOLERANCE)
            ):
                problems.append(f"{label} {actual:.1f}m 偏离声明 {declared:.1f}m 超过 ±50%")

    # 颜色非退化
    if float(scene.rgb.std()) < MIN_COLOR_STD:
        problems.append(f"颜色退化 (std={scene.rgb.std():.4f}), 疑似纯色占位")

    # 高斯尺寸合理
    med_scale = float(np.median(scene.scale))
    if not (SCALE_RANGE[0] <= med_scale <= SCALE_RANGE[1]):
        problems.append(f"高斯尺寸中位数 {med_scale:.4f}m 超出合理区间 {SCALE_RANGE}")

    # 不透明度非全透明
    if float(scene.opacity.mean()) < 0.05:
        problems.append(f"平均不透明度 {scene.opacity.mean():.3f} 过低")

    return problems


def validate(
    deliverable_dir: str | Path,
    feedback_dir: str | Path = "handoff",
    do_register: bool = False,
    assets_dir: str | Path = "assets",
) -> dict:
    """验收交付目录, 生成 FEEDBACK 文档, 返回结果 dict"""
    deliverable_dir = Path(deliverable_dir)
    feedback_dir = Path(feedback_dir)
    manifest_path = deliverable_dir / "manifest.json"

    results: dict[str, list[str]] = {}
    fatal: str | None = None
    manifest: DeliverableManifest | None = None

    if not manifest_path.exists():
        fatal = "manifest.json 缺失"
    else:
        try:
            manifest_bytes = _read_stable_bytes(manifest_path, label="manifest.json")
            manifest = DeliverableManifest(**json.loads(manifest_bytes.decode("utf-8")))
        except (json.JSONDecodeError, ValidationError) as e:
            fatal = f"manifest.json 不符合 schema: {e}"
        except _HandoffIntegrityError as e:
            fatal = f"manifest.json 无法安全读取: {e}"

    if manifest:
        for item in manifest.items:
            results[item.asset_id] = check_item(item, deliverable_dir)
        if do_register and manifest.schema_version < 2:
            fatal = (
                f"schema_version {manifest.schema_version} 缺少明确 meters/local-z-up "
                "与内容哈希，只允许验收，不允许注册"
            )

    n_pass = sum(1 for v in results.values() if not v)
    n_total = len(results)
    all_pass = fatal is None and n_total > 0 and n_pass == n_total
    handoff_id = manifest.handoff_id if manifest else deliverable_dir.name

    # 生成 FEEDBACK 文档
    lines = [
        f"# FEEDBACK — {handoff_id}",
        "",
        f"**验收结果: {'✅ 全部通过' if all_pass else '❌ 未通过'} ({n_pass}/{n_total})**",
        "",
    ]
    if fatal:
        lines += ["## 致命问题", "", f"- {fatal}", ""]
    if results:
        lines += ["## 逐项结果", "", "| asset_id | 结果 | 问题 |", "|---|---|---|"]
        for aid, problems in results.items():
            status = "PASS" if not problems else "FAIL"
            lines.append(f"| {aid} | {status} | {'; '.join(problems) or '—'} |")
        lines.append("")
    if not all_pass:
        lines += [
            "## 整改要求",
            "",
            "- 修复上表 FAIL 项后重新交付整个目录 (含 manifest.json)",
            "- 规格以对应 HANDOFF 文档为准, 阈值见 pipeline/validate_handoff.py 顶部常量",
            "",
        ]
    else:
        lines += [
            "## 后续动作",
            "",
            f"- 导入注册表: `python -m pipeline.validate_handoff {deliverable_dir} --register`",
            "",
        ]

    feedback_dir.mkdir(parents=True, exist_ok=True)
    feedback_path = feedback_dir / f"FEEDBACK-{handoff_id}.md"
    manual_tail = ""
    manual_marker = "## 人工备注"
    if feedback_path.exists():
        try:
            previous = _read_stable_bytes(feedback_path, label="FEEDBACK").decode("utf-8")
            marker_at = previous.find(manual_marker)
            if marker_at >= 0:
                manual_tail = previous[marker_at:].rstrip()
        except _HandoffIntegrityError:
            # 既存 FEEDBACK 不可信时, 丢弃 manual tail 重写
            manual_tail = ""
    feedback_text = "\n".join(lines).rstrip() + "\n"
    if manual_tail:
        feedback_text += "\n" + manual_tail + "\n"
    feedback_path.write_text(feedback_text, encoding="utf-8")
    logger.info(f"验收 {'PASS' if all_pass else 'FAIL'} ({n_pass}/{n_total}) → {feedback_path}")

    # 验收通过后导入注册表
    registered = []
    if all_pass and do_register and manifest:
        from pipeline.assets import AssetRegistry

        reg = AssetRegistry(assets_dir)
        # origin 诚实反映来源: --register 只落 schema_version>=2 的正式交付 (v1 在上方
        # fatal, v2 必带 generator + sha256 整体校验), 是真实交付素材 → "real", 而非占位
        # "gpt-mock" (后者是 seed_registry 的合成占位路径)。generator 详细身份保存在
        # deliverable manifest / 回执里可审计; origin 枚举 (synthetic/gpt-mock/real) 只记来源类别。
        origin = "real" if manifest.generator is not None else "gpt-mock"
        for item in manifest.items:
            reg.register(
                item.asset_id,
                deliverable_dir / item.ply,
                kind=item.kind,
                origin=origin,
                footprint_m=item.footprint_m,
            )
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
    parser.add_argument("--feedback-dir", default="handoff", help="FEEDBACK 输出目录")
    parser.add_argument("--register", action="store_true", help="验收通过后导入素材注册表")
    parser.add_argument("--assets-dir", default="assets")
    args = parser.parse_args()

    r = validate(
        args.deliverable, args.feedback_dir, do_register=args.register, assets_dir=args.assets_dir
    )
    print(f"\n验收: {'PASS' if r['all_pass'] else 'FAIL'} ({r['n_pass']}/{r['n_total']})")
    print(f"反馈文档: {r['feedback_file']}")
    if r["registered"]:
        print(f"已导入素材: {', '.join(r['registered'])}")
    raise SystemExit(0 if r["all_pass"] else 1)


if __name__ == "__main__":
    main()
