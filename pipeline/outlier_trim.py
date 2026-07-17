"""离群高斯剔除: 显式判据 + 显式阈值 + 有损变更的可回溯记录。

**这是有损几何操作** —— 与 ``spatial_chunk`` (纯空间重打包, 不改几何) 根本不同。
``spatial_chunk._core_bounds`` 明写"分位是启发式, 不是漂浮物的定义 —— 只用于取景提示,
绝不据此丢弃几何"。本模块正是那个**真的丢几何**的地方, 故标准只能更严:

1. **阈值没有默认值**。缺省不剔除。工具作者不替用户决定丢掉他 20% 的重建 ——
   决策必须发生在用户看着**自己数据的真实数字**的那一刻 (见 ``evaluate_trim`` dry-run)。
2. **绝不把被丢的点称为"漂浮物"**。它们是"按 <判据><阈值> 判定的离群点" —— 这是
   **判据的输出**, 不是关于世界的事实。**其中必然可能包含真实几何** (薄结构、场景边缘、
   稀疏采样区)。任何输出都不许冒充真理。
3. **信任只降不升**。剔除不产生任何信任判定, 只原样搬运源判定 (与 ``spatial_chunk`` 同)。

判据实测 (2026-07, 唯一可用的真实 3DGS: Brush 实训 village-canary-2000.ply, 67878 高斯)
------------------------------------------------------------------------------------
业界常识说"漂浮物往往低不透明度或尺度异常大"。**在这份真实数据上, opacity 部分是错的**:

===================  ==========  =================================================
判据                 等预算下     丢 ~21% 的点后剩余 bounds 体积 (占全量)
                     (K=14305)
===================  ==========  =================================================
voxel_occupancy      最优         **2.85%**   (bounds 1328x877x720 → 482x439x113)
scale (max 轴)       中           10.29%      (→ 805x576x186)
opacity              **无效**     **100.00%** (→ 1328x877x700, 几乎没动)
===================  ==========  =================================================

- ``opacity`` 在这份数据上是**反向信号**: 离群点的 opacity 中位 (0.2252) 反而**高于**
  主体 (0.2126); 用"低 opacity 先丢"给点排序, AUC=0.459 —— **比抛硬币还差**。
  ``opacity>=0.2`` 丢掉 44.2% 的高斯, 而 bounds 几乎纹丝不动 → 它丢的点**遍布主体几何
  所在的空间**, 而不是离群区 (注意: 这是可验证的空间事实; 至于那些点"实际是什么",
  我们没有证据, 故不作断言 —— 见 ``DROPPED_INTERPRETATION``)。
  推测: Brush 训练自身已按 opacity 剪枝, 该信号在产物里已被用尽 (分布 p50=0.21,
  p99=0.70, 很窄)。**本模块仍实现 OpacityRule, 但默认不推荐** —— 是否有用请在你自己的
  数据上用 dry-run 的 ``bounds_volume_retained_fraction`` 自行验证 (丢了一大堆点而
  bounds 不动 = 这个判据在杀主体几何)。
- ``scale`` 有独立信号 (corr(occ, scale_max) 仅 -0.191), 与 occupancy 组合可进一步收紧
  (occ>=5 且 scale_max<=2 → 保留 75.2%, bounds 体积 1.82%), 但多一个旋钮。
- **没有客观正确的阈值**: occupancy 阈值 3→10, 保留率 87.6%→72.8%。R (voxel_size) 同样
  是个没有正确值的旋钮 (R=1 保留 69.4% / R=20 保留 98.3%)。故二者都必须由用户显式指定。

单位诚实
--------
阈值 (``voxel_size`` / ``max_scale``) **有量纲**, 其单位就是场景 ``units`` 声明的单位。
上述 canary 的 ``units == "unknown"`` —— 所以那里的 "5" **不是 5 米**, 而是"5 个未经验证的
场景单位"; 把它叫米就是无证据的声称。manifest 如实记 ``threshold_units``, units 未知时告警。

已知局限 (**必读**)
------------------
剔除记录只在 **sidecar manifest** (``<out>.ply.trim_manifest.json``) 里, **产物 ply 自身的
``nantai_meta`` 不记录本次剔除** —— 只读 ply 的下游**无法**得知它被剔过。根因: 在 ply 元数据
里记录剔除需要改 ``gaussian_scene`` 的元数据 schema, 超出本模块的改动范围。缓解: manifest
用 sha256 与产物 ply 的**实际字节**绑定 (``output.sha256``), 消费者可验证"这份 manifest 描述
的正是这个 ply"。**这仍是一个真实的 provenance 缺口**, 修复需要 schema 变更。
"""
from __future__ import annotations

import hashlib
import json
import numbers
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from pipeline.gaussian_scene import GaussianScene

TRIM_MANIFEST_SUFFIX = ".trim_manifest.json"
SCHEMA_VERSION = 1

# 被丢弃点集的唯一诚实描述: 它是判据的输出, 不是关于世界的事实。
# 刻意不使用"漂浮物"/"噪声"这类词 —— 哪怕是在否定句里: 那些词是对世界的断言, 而我们
# 只有判据的输出, 没有任何证据能证明被丢的点是什么。测试 (TestHonestWording) 机械地
# 锁死这一点: 这些词在任何输出里出现即失败。
DROPPED_INTERPRETATION = (
    '本集合是"按下列判据与阈值判定为离群"的高斯 —— 这是判据的输出, '
    '不是关于这些高斯实际是什么的事实认定。'
    '剔除是有损操作, 该集合中可能包含真实几何 (薄结构、场景边缘、稀疏采样区)。'
)


def _require_positive_finite(label: str, value: Any) -> float:
    if not isinstance(value, numbers.Real) or isinstance(value, bool):
        raise ValueError(f"{label} 必须是正的有限数, 得到 {value!r}")
    out = float(value)
    if not np.isfinite(out) or out <= 0:
        raise ValueError(f"{label} 必须是正的有限数, 得到 {value!r}")
    return out


def voxel_occupancy(xyz: np.ndarray, voxel_size: float) -> np.ndarray:
    """每个点所在体素内的点数 (含自身)。

    体素占据度是"局部有多少同伴"的直接度量: 主体几何所在体素动辄数百点, 而被优化到
    场景外的孤立高斯往往独占体素。**但这只是一个判据** —— 稀疏采样的真实几何 (远处、
    薄结构) 同样会得到低占据度, 故低占据度**不等于**噪声。
    """
    voxel_size = _require_positive_finite("voxel_size", voxel_size)
    xyz = np.asarray(xyz, dtype=np.float64)
    if len(xyz) == 0:
        return np.zeros(0, dtype=np.int64)
    keys = np.floor(xyz / voxel_size).astype(np.int64)
    _unique, inverse, counts = np.unique(
        keys, axis=0, return_inverse=True, return_counts=True)
    return counts[np.asarray(inverse).ravel()]


@dataclass(frozen=True)
class OccupancyRule:
    """按体素占据度剔除: 保留 occupancy(voxel_size) >= min_occupancy 的高斯。

    两个旋钮都没有默认值, 且都没有客观正确值 (见模块 docstring 的 R 敏感性实测)。
    ``voxel_size`` 的单位 = 场景 ``units`` 声明的单位, 不假定是米。
    """

    voxel_size: float
    min_occupancy: int

    criterion = "voxel_occupancy"

    def __post_init__(self) -> None:
        _require_positive_finite("voxel_size", self.voxel_size)
        if (not isinstance(self.min_occupancy, numbers.Integral)
                or isinstance(self.min_occupancy, bool)
                or int(self.min_occupancy) < 1):
            raise ValueError(f"min_occupancy 必须是 >=1 的整数, 得到 {self.min_occupancy!r}")

    def keep_mask(self, scene: GaussianScene) -> np.ndarray:
        return voxel_occupancy(scene.xyz, self.voxel_size) >= int(self.min_occupancy)

    def values(self, scene: GaussianScene) -> np.ndarray:
        return voxel_occupancy(scene.xyz, self.voxel_size).astype(np.float64)

    def as_json(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "voxel_size": float(self.voxel_size),
            "min_occupancy": int(self.min_occupancy),
            "keeps": "occupancy(voxel_size) >= min_occupancy",
        }

    def describe(self) -> str:
        return (f"voxel_occupancy: 保留 所在体素(边长 {self.voxel_size:g})内点数 "
                f">= {self.min_occupancy} 的高斯")


@dataclass(frozen=True)
class ScaleRule:
    """按高斯尺度剔除: 保留 max(scale 三轴) <= max_scale 的高斯。

    ``max_scale`` 单位 = 场景 ``units`` 声明的单位。实测该判据有独立信号, 但它同样会
    误杀主体里合法的大高斯 (地面/墙面/背景大片)。
    """

    max_scale: float

    criterion = "scale"

    def __post_init__(self) -> None:
        _require_positive_finite("max_scale", self.max_scale)

    def keep_mask(self, scene: GaussianScene) -> np.ndarray:
        return scene.scale.max(axis=1) <= float(self.max_scale)

    def values(self, scene: GaussianScene) -> np.ndarray:
        return scene.scale.max(axis=1)

    def as_json(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "max_scale": float(self.max_scale),
            "keeps": "max(scale_xyz) <= max_scale",
        }

    def describe(self) -> str:
        return f"scale: 保留 三轴最大 scale <= {self.max_scale:g} 的高斯"


@dataclass(frozen=True)
class OpacityRule:
    """按不透明度剔除: 保留 opacity >= min_opacity 的高斯。

    **实测警告**: 在本仓库唯一可用的真实 3DGS (Brush 实训 canary) 上, 该判据是**反向
    信号** —— 丢 44% 的高斯而 bounds 几乎不变, 即它削的是主体几何所在空间里的点, 而非
    离群区 (详见模块 docstring)。
    保留此判据是因为别的训练器产物上它可能有效, 但**请先用 dry-run 在你自己的数据上
    验证**: 若丢弃率高而 ``bounds_volume_retained_fraction`` 接近 1, 说明它在杀主体几何。
    """

    min_opacity: float

    criterion = "opacity"

    def __post_init__(self) -> None:
        if (not isinstance(self.min_opacity, numbers.Real)
                or isinstance(self.min_opacity, bool)
                or not np.isfinite(self.min_opacity)
                or not (0.0 < float(self.min_opacity) <= 1.0)):
            raise ValueError(
                f"min_opacity 必须落在 (0, 1], 得到 {self.min_opacity!r}")

    def keep_mask(self, scene: GaussianScene) -> np.ndarray:
        return scene.opacity >= float(self.min_opacity)

    def values(self, scene: GaussianScene) -> np.ndarray:
        return scene.opacity

    def as_json(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "min_opacity": float(self.min_opacity),
            "keeps": "opacity >= min_opacity",
        }

    def describe(self) -> str:
        return f"opacity: 保留 不透明度 >= {self.min_opacity:g} 的高斯"


TrimRule = OccupancyRule | ScaleRule | OpacityRule


def _extent(xyz: np.ndarray) -> list[float]:
    if len(xyz) == 0:
        return [0.0, 0.0, 0.0]
    return [float(v) for v in (xyz.max(axis=0) - xyz.min(axis=0))]


def _percentiles(values: np.ndarray) -> dict[str, float]:
    if len(values) == 0:
        return {}
    qs = np.percentile(values, [0, 5, 25, 50, 75, 95, 100])
    return {name: float(q) for name, q in
            zip(("min", "p5", "p25", "p50", "p75", "p95", "max"), qs, strict=True)}


@dataclass
class TrimReport:
    """一次剔除的**真实取舍**, 计算完毕但尚未落盘 (dry-run 的产物)。"""

    rules: list[TrimRule]
    keep_mask: np.ndarray
    input_points: int
    kept_points: int
    dropped_points: int
    input_bounds_extent: list[float]
    output_bounds_extent: list[float]
    bounds_volume_retained_fraction: float
    per_rule_dropped: list[dict[str, Any]]
    dropped_value_percentiles: list[dict[str, Any]]
    threshold_units: str
    warnings: list[str]
    written: bool = False
    output_path: Path | None = None
    manifest_path: Path | None = None

    @property
    def dropped_fraction(self) -> float:
        return self.dropped_points / self.input_points if self.input_points else 0.0

    def describe(self) -> str:
        """给人看的取舍摘要。措辞铁律见 ``DROPPED_INTERPRETATION``。"""
        lines = [
            f"输入: {self.input_points} 高斯  bounds "
            f"{'x'.join(f'{v:.1f}' for v in self.input_bounds_extent)} "
            f"(单位: {self.threshold_units})",
            "判据 (阈值由你显式指定, 本工具没有默认值):",
        ]
        lines.extend(f"  - {rule.describe()}" for rule in self.rules)
        lines.append(
            f"保留: {self.kept_points} ({self.kept_points / max(self.input_points, 1):.1%})"
            f"   丢弃: {self.dropped_points} ({self.dropped_fraction:.1%})")
        lines.append(
            f"剔除后 bounds: {'x'.join(f'{v:.1f}' for v in self.output_bounds_extent)}"
            f"   bounds 体积保留 {self.bounds_volume_retained_fraction:.2%}")
        if len(self.rules) > 1:
            lines.append("各判据单独判丢数 (可重叠):")
            lines.extend(f"  - {item['criterion']}: {item['dropped']}"
                         for item in self.per_rule_dropped)
        lines.append(
            "被丢弃点的判据取值分布 —— 看看阈值切在了哪里:")
        for item in self.dropped_value_percentiles:
            pcts = item["percentiles"]
            if pcts:
                lines.append(
                    f"  - {item['criterion']}: " +
                    " ".join(f"{k}={v:.4g}" for k, v in pcts.items()))
        lines.extend(f"[告警] {w}" for w in self.warnings)
        lines.append("诚实边界: " + DROPPED_INTERPRETATION)
        lines.append(
            "判据是否有效, 看这个数: 若丢弃率很高而 bounds 体积保留仍接近 100%, "
            "说明这个判据没在剔离群点, 而是在削主体几何。")
        return "\n".join(lines)


def _threshold_warnings(scene: GaussianScene, rules: list[TrimRule]) -> list[str]:
    warnings: list[str] = []
    dimensioned = [r for r in rules if isinstance(r, OccupancyRule | ScaleRule)]
    if dimensioned and scene.units == "unknown":
        warnings.append(
            '场景 units 声明为 unknown: 有量纲的阈值 (voxel_size / max_scale) '
            '是"未经验证的场景单位", 不是米。不要把它当米制解读。')
    if scene.frame_id is None:
        warnings.append(
            "场景未声明 frame_id: 无坐标契约可回溯, 本次剔除不改变这一点。")
    return warnings


def evaluate_trim(scene: GaussianScene, *, rules: list[TrimRule]) -> TrimReport:
    """**只算不写**: 算出这组判据/阈值的真实取舍, 供用户拍板。

    ``rules`` 为空 → 拒绝 (缺省不剔除)。多条规则取**交集保留** (任一条判丢即丢)。
    """
    if not rules:
        raise ValueError(
            "未指定判据: 剔除是有损操作, 本工具没有默认阈值, 缺省不剔除。"
            "请显式给出判据与阈值 (先看 dry-run 报告再决定)。")
    if len(scene) == 0:
        raise ValueError("空场景无可剔除")

    keep = np.ones(len(scene), dtype=bool)
    per_rule_dropped: list[dict[str, Any]] = []
    dropped_value_percentiles: list[dict[str, Any]] = []
    for rule in rules:
        mask = np.asarray(rule.keep_mask(scene), dtype=bool)
        per_rule_dropped.append(
            {"criterion": rule.criterion, "dropped": int((~mask).sum())})
        keep &= mask

    dropped = ~keep
    for rule in rules:
        dropped_value_percentiles.append({
            "criterion": rule.criterion,
            "percentiles": _percentiles(np.asarray(rule.values(scene))[dropped]),
        })

    in_extent = _extent(scene.xyz)
    out_extent = _extent(scene.xyz[keep])
    in_vol = float(np.prod(in_extent))
    out_vol = float(np.prod(out_extent))
    retained = (out_vol / in_vol) if in_vol > 0 else 0.0

    return TrimReport(
        rules=list(rules),
        keep_mask=keep,
        input_points=len(scene),
        kept_points=int(keep.sum()),
        dropped_points=int(dropped.sum()),
        input_bounds_extent=in_extent,
        output_bounds_extent=out_extent,
        bounds_volume_retained_fraction=retained,
        per_rule_dropped=per_rule_dropped,
        dropped_value_percentiles=dropped_value_percentiles,
        threshold_units=scene.units,
        warnings=_threshold_warnings(scene, rules),
    )


def _scene_digest(scene: GaussianScene) -> str:
    """输入几何的内容寻址摘要 —— 让 trim_id 绑定到"这份输入"。"""
    digest = hashlib.sha256()
    for array in (scene.xyz, scene.opacity, scene.scale):
        digest.update(np.ascontiguousarray(array, dtype=np.float64).tobytes())
    digest.update(json.dumps(
        {"frame_id": scene.frame_id, "units": scene.units,
         "applied_transform_ids": list(scene.applied_transform_ids)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    return digest.hexdigest()


def derive_trim_id(scene: GaussianScene, rules: list[TrimRule]) -> str:
    """内容寻址的剔除 id: 同一输入 + 同一判据/阈值 → 同一 id (与 xf-/ingest- 同构)。"""
    payload = {
        "input_sha256": _scene_digest(scene),
        "rules": [rule.as_json() for rule in rules],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("utf-8")
    return "trim-" + hashlib.sha256(encoded).hexdigest()[:20]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def trim_scene(
    scene: GaussianScene,
    out_path: str | Path,
    *,
    rules: list[TrimRule],
    confirm: bool = False,
    source_provenance: dict[str, Any] | None = None,
    claimed_geometry_usability: str | None = None,
    flavor: str = "3dgs",
) -> TrimReport:
    """算出取舍; **仅当 confirm=True 时**才写产物 ply + sidecar manifest。

    默认 ``confirm=False`` (dry-run): 只返回报告, 一个字节都不写 —— 让用户先看见
    自己数据上的真实代价 (丢多少、bounds 收缩多少) 再拍板。

    ``source_provenance``: 源 manifest 的信任判定 (如 ``{"geometry_usability": ...}``),
    **原样搬运**进 manifest 的 ``source``。缺席即未知, 绝不编造 (与 ``spatial_chunk`` 同)。
    ``claimed_geometry_usability``: 只用于**拒绝**"想借剔除提升信任"的调用 —— 剔除不产生
    任何信任判定, 故任何与源**不同**的声称 (哪怕看起来是"降级") 都 fail-closed。
    刻意不比较等级高低: 那需要给 preview-proxy / preview-only / metric-aligned /
    metric-unaligned 定一个全序, 而这个序并不天然存在 (metric-unaligned 与 preview-only
    孰高?)。发明这个序 = 发明信任语义, 那是 ``reconstruct._derive_geometry_usability``
    的职责, 不是剔除工具的。故这里只做一件能被机器验证的事: 与源一致才放行。
    """
    out_path = Path(out_path)
    report = evaluate_trim(scene, rules=rules)

    source_usability = (source_provenance or {}).get("geometry_usability")
    if claimed_geometry_usability is not None \
            and claimed_geometry_usability != source_usability:
        raise ValueError(
            f"剔除不提升信任等级: 源判定为 {source_usability!r}, 拒绝声称 "
            f"{claimed_geometry_usability!r}。剔除只搬运判定, 从不产生判定。")

    if not confirm:
        logger.info(
            f"dry-run (未写盘): 保留 {report.kept_points}/{report.input_points}, "
            f"丢弃 {report.dropped_points} —— 确认后才会写 {out_path}")
        return report

    if report.kept_points == 0:
        raise ValueError(
            "剔除后场景为空: 该阈值会丢掉全部高斯, 拒绝写出空产物。请放宽阈值。")
    if out_path.exists():
        raise ValueError(
            f"输出已存在, 拒绝覆盖 (剔除是有损的, 覆盖会毁掉原产物): {out_path}")

    manifest_path = out_path.parent / (out_path.name + TRIM_MANIFEST_SUFFIX)
    if manifest_path.exists():
        raise ValueError(f"剔除 manifest 已存在, 拒绝覆盖: {manifest_path}")

    trimmed = scene._subset(np.where(report.keep_mask)[0])

    # 记录随【ply 字节】走, 而不只在 sidecar manifest 里: sidecar 用 sha256 绑定了字节,
    # 但 ply 一旦被复制/改名/被 prepare_import 吃进去, sidecar 就掉队了 —— 下游会拿到
    # 一个"看起来是完整重建、实际少了一大截"的 ply 而无从得知。_subset 已继承源场景
    # 既有的 lossy_edits, 这里【追加】本次剔除 (不是覆盖: 剔了两次就该有两条)。
    trim_id = derive_trim_id(scene, rules)
    trimmed.lossy_edits = [*trimmed.lossy_edits, {
        "operation": "outlier_trim",
        "lossy": True,
        "rules": [rule.as_json() for rule in rules],
        # 阈值有量纲, 单位就是场景声明的单位 —— 绝不假定为米。
        "threshold_units": report.threshold_units,
        "points_before": report.input_points,
        "points_after": report.kept_points,
        "dropped": report.dropped_points,
        # 回溯到完整 sidecar (逐规则明细/分位分布/告警都在那里)。
        "trim_id": trim_id,
    }]
    trimmed.save_ply(out_path, flavor=flavor)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "trim_id": derive_trim_id(scene, rules),
        "created_utc": datetime.now(UTC).isoformat(),
        # 这是有损几何变更, 不是重打包 —— 让下游一眼看见。
        "lossy": True,
        "operation": "outlier_trim",
        "rules": [rule.as_json() for rule in rules],
        # 阈值的单位 = 场景声明的单位, 绝不假定为米。
        "threshold_units": report.threshold_units,
        "input": {
            "points": report.input_points,
            "bounds_extent": report.input_bounds_extent,
            "sha256": _scene_digest(scene),
        },
        "output": {
            "points": report.kept_points,
            "bounds_extent": report.output_bounds_extent,
            "bounds_volume_retained_fraction": report.bounds_volume_retained_fraction,
            "path": out_path.name,
            "sha256": _sha256_file(out_path),
        },
        "dropped": {
            "points": report.dropped_points,
            "fraction": report.dropped_fraction,
            "per_rule": report.per_rule_dropped,
            "value_percentiles": report.dropped_value_percentiles,
            "interpretation": DROPPED_INTERPRETATION,
        },
        # 如实记录源坐标契约; 剔除不改坐标, 也不产生信任判定。
        "source": {
            "frame_id": scene.frame_id,
            "units": scene.units,
            "applied_transform_ids": list(scene.applied_transform_ids),
            **(dict(source_provenance) if source_provenance else {}),
        },
        "warnings": report.warnings,
        "known_limitation": (
            "本次剔除只记录在这份 sidecar manifest 里; 产物 ply 的 nantai_meta 元数据"
            "不含剔除记录, 只读 ply 的下游无法得知它被剔过。output.sha256 与产物"
            "实际字节绑定, 可验证本 manifest 描述的正是该 ply。"),
    }
    # newline="\n": 与 registration/recon_manifest/chunks.json 惯例统一, 跨平台字节可复现。
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8", newline="\n")

    report.written = True
    report.output_path = out_path
    report.manifest_path = manifest_path
    logger.info(
        f"剔除 (有损): {report.input_points} → {report.kept_points} 高斯 "
        f"(丢 {report.dropped_points}, {report.dropped_fraction:.1%}) → {out_path}")
    return report
