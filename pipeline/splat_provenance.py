"""导入的 3DGS .ply 与 COLMAP sparse 点云的**几何一致性**检查。

## 这个模块能声称什么、不能声称什么

3DGS 训练器 (Brush、INRIA 原版) 以 COLMAP 的 ``sparse/0/points3D`` 作为高斯初始化，
故训练产物与该 sparse **同坐标系**，且高斯稠密覆盖 sparse 点。反过来说，一个来自
别的场景 / 别的 COLMAP run / 别的坐标系的 ply，几何上会与 sparse **对不上**。

本模块只利用这一条，语义**严格只降不升**：

- ``CONTRADICTED``  —— **强证据**说明 ply 不在这个 sparse 的坐标系里 → 调用方应 fail-closed。
- ``NOT_CONTRADICTED`` —— **不是"通过"，不是证明，不提升任何信任等级**。它只说
  "几何一致，没发现矛盾"。它**不能**证明这个 ply 是用这个 workspace 训练出来的：
  任何与 sparse 几何吻合的点云都会得到同样结论。判据是**可证伪的**，不是可证实的。
- ``UNKNOWN`` —— 拿不到/读不了证据。**不做任何声称**，绝不退化成 NOT_CONTRADICTED。

## 判据为什么是这个形式 (而不是"≥X% 的点在 Y 米内有高斯")

绝对容差判据能被**稠密噪声平凡满足**——实测 canary：200 万点的纯随机噪声，在
0.5% bbox 对角线容差下对 sparse 的覆盖率是 **100%**。点越多越容易"通过"，这是假校验。

故这里改用**密度归一化**的统计量。同密度泊松点过程的期望 median 最近邻距离有解析解：

    P(最近邻 > r) = exp(-4/3 · π · r³ · ρ)   ⇒   median_null = (ln2 / (4/3 · π · ρ))^(1/3)

``signal_ratio = median_null / median_observed`` 即"这个 ply 覆盖 sparse 点的紧密程度，
比同密度随机点云好多少倍"。它**尺度无关且密度无关**：往 ply 里灌点数不能提高它。

## 阈值的依据 (以及定不出的部分 —— 如实说)

- **矛盾侧的阈值有依据**：``signal_ratio ≈ 1`` 是 null model 的**解析锚点**，不是经验值。
  canary 实测三个密度的纯噪声给出 0.93x / 1.00x / 1.03x，与理论预测 1.0 吻合。
  ``CONTRADICTION_RATIO = 2.0`` 在锚点上留了 2 倍安全裕度：ratio ≤ 2 意味着这个 ply
  对 sparse 的覆盖**不比随机点云好多少** → 强矛盾。
- **"通过"侧的阈值定不出，所以本模块不设**。本仓库只有 **1 个** (n=1) 真实标定样本
  (合成 canary + GT COLMAP + Brush，ratio=98.96x)。真实 SfM 的 sparse 噪声更大、外点更多，
  ratio 分布未知。用 n=1 去定"≥多少算真"就是拍脑袋。故 ratio > 2 一律只报
  ``NOT_CONTRADICTED`` 并**原样附上数字**，由人看数字判断，而不是由本模块假装知道。

## 已知限制 (诚实标注)

- **只适用于"训练器直接吃了这个 workspace 并保留其坐标系"的路线** (Brush / INRIA 原版)。
  nerfstudio ``ns-process-data`` + ``splatfacto`` 会重跑 COLMAP 并对场景做
  re-center/auto-scale/orient，产物**不在**本机 sparse 的坐标系里 (canary 实测该变换下
  ratio=0.00x)。对那条路线**不应调用本检查**——它没有做出"同坐标系"的声称，无声称即无可矛盾。
- 只读 ``points3D.txt``。``.bin`` 未实现 → UNKNOWN，不猜。
- 几何一致 ≠ 同源。本模块**不是**密码学绑定；要真正的绑定需要训练器侧输出可验证的
  workspace 摘要，那不在本仓库控制范围内。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
from plyfile import PlyData

# 子采样规模：probe 点决定 median 的统计噪声，ref 上限决定最坏耗时。
# ratio 密度无关，故对 ref 子采样不改变结论 (null model 用子采样后的点数算)。
_PROBE_POINTS = 2000
_MAX_REF_POINTS = 100_000
_MIN_POINTS = 50
_SEED = 20260717  # 固定 seed：同输入必须同结论 (可审计/可复现)

# null model 的解析锚点是 1.0 (= 与同密度随机点云无异)。2.0 是其上的安全裕度。
# 依据见模块 docstring；这是**矛盾侧**阈值，不是"通过"阈值 (后者本模块不设)。
CONTRADICTION_RATIO = 2.0


class Verdict(Enum):
    CONTRADICTED = "contradicted"
    NOT_CONTRADICTED = "not-contradicted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SplatConsistency:
    """一次几何一致性检查的结果。字段是证据，不是结论。"""

    verdict: Verdict
    reason: str
    signal_ratio: float | None = None
    null_median_nn: float | None = None
    observed_median_nn: float | None = None
    coverage: float | None = None
    n_sparse: int | None = None
    n_gaussians: int | None = None

    @property
    def proves_provenance(self) -> bool:
        """永远是 False。

        没有任何 verdict 能证明 ply 来自某个 workspace —— 判据只能证伪。
        这个属性存在是为了让"想拿它当证明用"的调用点在读代码时就撞墙。
        """
        return False

    @property
    def should_fail_closed(self) -> bool:
        return self.verdict is Verdict.CONTRADICTED

    def summary(self) -> str:
        if self.signal_ratio is None:
            return f"[{self.verdict.value}] {self.reason}"
        return (
            f"[{self.verdict.value}] {self.reason} "
            f"(signal_ratio={self.signal_ratio:.2f}x, 随机点云基线=1.0x; "
            f"coverage={self.coverage:.1%}; "
            f"sparse={self.n_sparse} 点, 采样高斯={self.n_gaussians})"
        )


def _unknown(reason: str) -> SplatConsistency:
    return SplatConsistency(verdict=Verdict.UNKNOWN, reason=reason)


def load_colmap_points3d(path: str | Path) -> np.ndarray:
    """读 COLMAP ``points3D.txt`` 的 XYZ。格式不对就抛，不猜。"""
    path = Path(path)
    if path.suffix.lower() != ".txt":
        raise ValueError(
            f"只支持 points3D.txt；{path.name} 未实现 (用 colmap model_converter "
            f"--output_type TXT 转换)"
        )
    pts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4:
            raise ValueError(f"points3D.txt 行格式不符 COLMAP 约定: {line[:60]!r}")
        pts.append((float(fields[1]), float(fields[2]), float(fields[3])))
    arr = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    return arr[np.isfinite(arr).all(axis=1)]


def load_ply_xyz(path: str | Path) -> np.ndarray:
    """只取 ply 的 XYZ。

    刻意不走 ``GaussianScene.load_ply``：那里的 3DGS 契约门 (四元数归一化等) 会在
    本检查之前就拒掉一些 ply，而本检查的对象恰恰包括"来路不明"的 ply——只要能读出
    坐标就该给出几何结论。
    """
    data = PlyData.read(str(path))
    vertex = data["vertex"].data
    missing = [k for k in ("x", "y", "z") if k not in vertex.dtype.names]
    if missing:
        raise ValueError(f"ply vertex 缺少坐标属性: {missing}")
    arr = np.stack([vertex["x"], vertex["y"], vertex["z"]], axis=1).astype(np.float64)
    return arr[np.isfinite(arr).all(axis=1)]


def _null_median_nn(n: int, lo: np.ndarray, hi: np.ndarray) -> float | None:
    """同密度泊松点过程的期望 median 最近邻距离。体积退化时返回 None。"""
    extent = hi - lo
    if np.any(extent <= 0):
        return None
    volume = float(np.prod(extent))
    if not np.isfinite(volume) or volume <= 0:
        return None
    density = n / volume
    return float((np.log(2) / (4.0 / 3.0 * np.pi * density)) ** (1.0 / 3.0))


def _min_nn_distance(query: np.ndarray, ref: np.ndarray, block: int = 256) -> np.ndarray:
    """每个 query 点到最近 ref 点的距离 (分块暴力，避免 O(N·M) 的内存峰值)。"""
    out = np.empty(len(query), dtype=np.float64)
    for i in range(0, len(query), block):
        chunk = query[i:i + block]
        d = np.linalg.norm(chunk[:, None, :] - ref[None, :, :], axis=2)
        out[i:i + block] = d.min(axis=1)
    return out


def check_splat_against_sparse(
    ply_path: str | Path,
    sparse_points3d_path: str | Path,
) -> SplatConsistency:
    """检查 ply 的几何与 COLMAP sparse 点云是否**矛盾**。

    只在调用方**声称** "这个 ply 是用产出该 sparse 的 workspace 训练的" 时才有意义。
    没有这个声称就没有可矛盾的东西——见模块 docstring 的限制一节。

    返回三态；``CONTRADICTED`` 是唯一有强证据的结论，调用方应据此 fail-closed。
    """
    try:
        sparse = load_colmap_points3d(sparse_points3d_path)
    except FileNotFoundError:
        return _unknown(f"拿不到 COLMAP sparse: {sparse_points3d_path} 不存在")
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        return _unknown(f"读不了 COLMAP sparse: {exc}")

    try:
        gauss = load_ply_xyz(ply_path)
    except FileNotFoundError:
        return _unknown(f"拿不到 ply: {ply_path} 不存在")
    except Exception as exc:  # plyfile 对损坏输入抛的异常类型不受控
        return _unknown(f"读不了 ply: {type(exc).__name__}: {exc}")

    if len(sparse) < _MIN_POINTS or len(gauss) < _MIN_POINTS:
        return _unknown(
            f"点太少，统计量无意义 (sparse={len(sparse)}, 高斯={len(gauss)}, "
            f"下限={_MIN_POINTS})"
        )

    s_lo, s_hi = sparse.min(axis=0), sparse.max(axis=0)
    g_lo, g_hi = gauss.min(axis=0), gauss.max(axis=0)
    overlap_lo, overlap_hi = np.maximum(s_lo, g_lo), np.minimum(s_hi, g_hi)
    # 严格小于才是"真不相交"。某轴恰好相等是**退化**(共面场景如墙面/地面是合法的)，
    # 不是矛盾 —— 交给下面的 null model 判 UNKNOWN，绝不把退化当成强证据。
    if np.any(overlap_hi < overlap_lo):
        return SplatConsistency(
            verdict=Verdict.CONTRADICTED,
            reason=(
                f"ply 与 sparse 的 bbox 不相交 —— 不可能是同一坐标系下的同一场景 "
                f"(sparse {np.round(s_lo, 2)}..{np.round(s_hi, 2)}; "
                f"ply {np.round(g_lo, 2)}..{np.round(g_hi, 2)})"
            ),
            n_sparse=len(sparse),
            n_gaussians=len(gauss),
        )

    rng = np.random.default_rng(_SEED)
    ref = gauss
    if len(ref) > _MAX_REF_POINTS:
        # ratio 密度无关，故子采样只影响耗时，不影响结论 —— null model 同步用采样后的点数。
        ref = ref[rng.choice(len(ref), _MAX_REF_POINTS, replace=False)]
    probe = sparse
    if len(probe) > _PROBE_POINTS:
        probe = probe[rng.choice(len(probe), _PROBE_POINTS, replace=False)]

    null_median = _null_median_nn(len(ref), overlap_lo, overlap_hi)
    if null_median is None or null_median <= 0:
        return _unknown(
            "sparse/ply 的重叠包围盒体积退化 (共面或共线)，null model 无定义 —— 不做声称"
        )

    distances = _min_nn_distance(probe, ref)
    observed_median = float(np.median(distances))
    if observed_median <= 0:
        # ply 点与 sparse 点逐点重合：几何上不矛盾，但这本身可疑 (ply 可能就是 sparse
        # 的拷贝而非训练产物)。仍然只报"没发现矛盾"——本模块不负责判断"太完美"。
        ratio = float("inf")
    else:
        ratio = null_median / observed_median
    coverage = float((distances <= null_median / 10.0).mean())

    common = {
        "signal_ratio": ratio,
        "null_median_nn": null_median,
        "observed_median_nn": observed_median,
        "coverage": coverage,
        "n_sparse": len(sparse),
        "n_gaussians": len(ref),
    }

    if ratio <= CONTRADICTION_RATIO:
        return SplatConsistency(
            verdict=Verdict.CONTRADICTED,
            reason=(
                f"ply 对 sparse 点的覆盖不比同密度随机点云好多少 "
                f"(signal_ratio={ratio:.2f}x ≤ {CONTRADICTION_RATIO}x，随机基线=1.0x) —— "
                f"强证据说明这个 ply 不在该 sparse 的坐标系里 (拿错文件 / 别的场景 / "
                f"别的 COLMAP run / 训练器做过 re-center+rescale)"
            ),
            **common,
        )

    return SplatConsistency(
        verdict=Verdict.NOT_CONTRADICTED,
        reason=(
            "几何一致，没发现矛盾 —— 这**不是**通过，也不证明 ply 来自该 workspace "
            "(判据只能证伪；任何与 sparse 几何吻合的点云都得同样结论)"
        ),
        **common,
    )
