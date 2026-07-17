"""SfM-arbitrary -> ENU-metric Sim3 alignment (the measured 3DGS path).

An SfM reconstruction (e.g. COLMAP) lives in an arbitrary, non-metric frame.
This module fits a similarity transform (Umeyama, 1991) from >=4 non-coplanar
control points -- surveyed ENU points, GPS anchors, or (cross-batch) camera
centres shared with an already-aligned reconstruction -- to promote a
registration into the metric ENU world the schema already defines, **without
ever silently promoting arbitrary geometry to metres**.

Every gate is fail-closed.  If there are fewer than four finite control points,
if the source configuration is degenerate (collinear/coplanar), if the fitted
scale is non-positive, or if the RMS residual exceeds ``max_rms_m``, no world
frame is produced: the registration stays sfm-local / UNALIGNED.  A proper
rotation (det=+1) is forced, so a reflection is never emitted as a rotation.

跨批次 (整村漫游: 村子塞不进一次重建, 必须分批再缝起来) 有【两条路, 别混】:

``merge_for_preview`` —— **preview-only, 不声称米制**。把 B 缝进 A 的【任意 frame】,
    判据是无量纲的 relative_rms。**漫游只需要各批次落在同一个坐标系里, 不需要米制**,
    故这条路不需要下面那个标定, 今天就能用。产物 units=arbitrary / UNALIGNED, 下游
    自动判 preview-only: 诚实地【可漫游、不可测量】。

``align_to_reference`` —— **米制**。一切以 A 的 ENU 世界为中枢, 【绝不做 B->A】: 因为
    ``align_registration`` 会为任何 ``world_frame_id`` 硬编码 axes=enu-z-up / metric /
    aligned, 对一个纯任意 frame 那就是伪造米制声称。这条路【今天默认 raise】—— 地基
    未测: 真实南台照片跑两次 COLMAP 的共享中心噪声无人测过, 而所有误差预算随它线性
    缩放 (见 ``load_shared_noise_calibration``)。**它 fail-closed 不影响漫游**。

The fit is recorded as ``Sim3AlignmentEvidence`` and serialised onto both the
``FrameTransform.evidence`` and the measured world frame's ``evidence`` via the
``sim3.alignment.v1=<json>`` convention, so downstream audit code can re-derive
the residuals and see the gate outcome.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraPose,
    ControlPoint,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    FrameTransform,
    GeoAlignment,
    GeoAnchor,
    Handedness,
    MetricStatus,
    PreviewMergeEvidence,
    RegistrationResult,
    Sim3,
    Sim3AlignmentEvidence,
    TransformMethod,
    gps_to_enu,
)

# 最小源奇异值的绝对下限, 好让"共面/共线到数值精度"的构型在相对 floor
# (``min_span_ratio`` * s1) 舍入到零时也被拒。
#
# **单位是【源 frame 的单位】, 不是米** —— 旧名 ``_ABS_SPAN_FLOOR_M`` 与旧注释
# "(metres)" 是【错的】: 它比的是 ``_source_span(src)`` 的奇异值, 而 src 来自
# ``pose.t_xyz``, 即 ``reg.pose_frame`` 的坐标 —— 那个 frame 的 units 是【ARBITRARY】。
# SfM 的 gauge 任意 (COLMAP 常把双视图基线取单位长), 所以这个 1e-6 在不同 gauge 下
# 对应的物理尺度可以差几个数量级。
#
# 保留它、也保留这个数值, 理由是它的方向是 fail-closed: 门只会【误拒】gauge 极小的
# 退化构型, 不会误放。但名字必须停止撒谎 —— 一个自称米的常量会让下一个人以为这里
# 有物理依据可比, 而【没有】。
_ABS_SPAN_FLOOR_SOURCE_UNITS = 1e-6

# ``min_span_ratio`` 的默认值 1e-3 【没有标定出处】—— 它是从合成实验反推的一个数,
# 不是从真实采集标定的。诚实后果: 它只挡得住【精确】退化。实测近共线航带
# (s3/s1=3.1e-3, 只比 floor 高 3 倍) 照样放行, 而 100m 外真实误差 4.00m。所以
# 【不要】把"共线风险已由 s3 门覆盖"当成事实 —— 真正的挡板只有这个 floor 的绝对
# 位置, 而这个位置是编的。标定它需要真实南台采集的实测, 那个实测还没做。
# 见 tests/test_cross_batch_alignment.py::test_holdout_is_blind_to_collinear_degeneracy。

# 一个 Sim3 有 7 个自由度; 每个点给 3 个方程。3 个点【去心后秩恒 <=2】(s3 恒为 0),
# 所以共面/共线守卫对 n=3 永远不可能放行 —— 旧契约写的 ">=3" 是死代码。>=4 是
# 唯一有可能非共面的点数, 故契约按事实写成 >=4。
_MIN_CONTROL_POINTS = 4

# 派生靶标 (跨批次) 模式下的【有效】控制点下限。
#
# 【8 没有标定出处 —— 别把它当标定值读】。它此前自称"theory 实测 k>=8 进误差平台",
# 而那个实测【不在仓库里】: 全仓 grep '1.49'/'45.13'/'theory' 只命中本文件自己, 即
# 一个 fail-closed 门的全部依据不可追溯 (而同文件对 min_span_ratio 却诚实写了"这个
# 位置是编的" —— 双标)。本仓库重跑了那个量, 结论见
# docs/verification/2026-07-17-derived-mode-control-point-floor.md:
#   - **"误差在 k>=8 进平台"不成立**: 凸包内真实误差随 k 平滑按 ~1/sqrt(k) 下降,
#     k=8 时仍比 k=20 差 1.73 倍, 全程没有拐点。
#   - **走平的是【留出统计量】本身** (~1.8mm, 数值确实对得上), 但它走平是因为收敛到
#     噪声地板 (1.49mm) —— 那是【数据的性质】, 不是对齐质量的性质。原话把估计量的
#     平台读成了误差的平台。
#   - "k<8 时留出的方差压过被测误差"【没有 8 这个拐点】: 离散度 4.7(k=5) -> 3.3(k=8)
#     -> 2.8(k=16) 平滑下降, 且留出在【所有】k 上都是个很吵的估计量。
#
# 所以 8 是【政策选择】: k<8 时拟合最弱且留出估计量最不可靠, 两件事都真, 但都没有在
# 8 处发生相变 —— 换 7 或 9 同样说得通。保留它是因为方向是 fail-closed (只会误拒),
# 【不是】因为它标定过。要标定它需要真实南台采集跑两次 COLMAP 的实测, 那个实测还没做。
# 载荷事实由 TestDerivedFloorHasNoErrorPlateau 钉住。
_MIN_EFFECTIVE_CONTROL_POINTS = 8

# 上线前置门: 两次【真实】COLMAP 重建的共享相机中心噪声实测记录。这个数没测出来
# 之前跨批次对齐不许上线 —— 所有误差预算随它线性缩放。
_SHARED_NOISE_CALIBRATION_PATH = (
    Path(__file__).resolve().parent.parent / "calibration" / "colmap_shared_noise.json"
)

_MEASURE_SCRIPT = "scripts/measure_colmap_shared_noise.py"

# 标定记录的米制基准串前缀。构造点 (measure 脚本) 与复核点 (本模块) 必须逐字对得上,
# 故只有这一个出处。
_METRIC_BASIS_PREFIX = "reference-pose-to-world:"


class AlignmentError(ValueError):
    """Raised when a Sim3 alignment gate fails; no world frame is emitted."""


class SharedNoiseCalibration(BaseModel):
    """两次真实 COLMAP 重建之间共享相机中心的实测噪声标定记录。

    这是跨批次对齐【整个方案的地基】。现有的 1.49mm 来自 Blender 渲的 45m / 24 张 /
    emission-only 高频程序纹理场景 —— SfM 的【最优情况】, 且【该测量本身不在仓库里、
    不可复核】(见 docs/verification/2026-07-17-derived-mode-control-point-floor.md)。
    真实照片有视角相关高光、曝光变化、运动模糊、夯土墙/瓦屋面的重复自相似纹理、植被
    摆动, 噪声必然更高但高多少【无人知】。所以这里不接受任何默认值: 没有记录 =
    fail-closed。

    **``*_m`` 字段【真是米】, 靠的是 ``metric_basis``**: 这批残差量在参考批 A 的
    【米制 ENU 世界】里, 不是 COLMAP 的 pose_frame。SfM 的 gauge 是任意的 (双视图
    基线常取单位长), 直读 ``pose.t_xyz`` 量出来的数随 gauge 浮动任意个数量级 ——
    把那种数叫"米"正是本仓库存在的理由要挡的事。故记录必须自带它所在的米制世界
    (``reference_world_frame_id``) 与该世界的凭据 (``metric_basis``, 内容寻址的
    ``transform_id``), 任何消费者可拿 A 复核。产出见
    ``scripts/measure_colmap_shared_noise.py``, 它拒绝未对齐的 A。

    ``relative_rms`` (= rms/跨度, 无量纲) 是唯一【不随任何 gauge 浮动】的判据, 故
    一并入记录: 若它与 *_m 讲的故事不一致, 说明米制基准本身有问题。

    本模块只校验记录【存在、自洽、且绑对了参考批】, 不从它反推任何阈值 —— 标定不
    出来的阈值就不编。"绑对了"由 ``load_shared_noise_calibration`` 拿【本次的参考批】
    实地复核 (见那里的诚实限制: 它挡不住什么)。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_version: Literal[2]
    measured_on: str = Field(min_length=1)
    source: str = Field(min_length=1)
    n_shared_images: int
    # 这批 *_m 数字所在的米制世界, 及它凭什么是米 (参考批 pose_to_world 的内容寻址
    # transform_id)。没有这两个字段, *_m 就只是三个字符的自称。
    reference_world_frame_id: str = Field(min_length=1)
    metric_basis: str = Field(min_length=1)
    shared_centre_rms_m: float = Field(ge=0)
    shared_centre_max_m: float = Field(ge=0)
    scene_extent_m: float = Field(gt=0)
    relative_rms: float = Field(ge=0)
    residual_distance_corr: float
    affine_rms_m: float = Field(ge=0)

    @field_validator("shared_centre_rms_m", "shared_centre_max_m", "scene_extent_m",
                     "relative_rms", "residual_distance_corr", "affine_rms_m")
    @classmethod
    def _finite(cls, value: float) -> float:
        value = float(value)
        if not np.isfinite(value):
            raise ValueError("标定记录的数值必须有限")
        return value

    @field_validator("metric_basis")
    @classmethod
    def _basis_is_well_formed(cls, value: str) -> str:
        """【只是形状检查】—— 它证明不了这批 *_m 是米, 别拿它当那个证明。

        这道校验器原本的错误信息断言"只能来自参考批那次对齐的机器可验证证据", 而它
        实际只做了 ``startswith`` —— 于是手写一个
        'reference-pose-to-world:xf-DOES-NOT-EXIST' 就过, 任意 gauge 的数字照样以
        record_version=2 载入并放行米制门。真正的复核在
        ``_verify_calibration_binds_to_reference``: 那里拿【本次的参考批】比对
        transform_id 与 world frame。本校验器只保证串的形状能被那里解析。
        """
        transform_id = value.removeprefix(_METRIC_BASIS_PREFIX)
        if transform_id == value or not transform_id:
            raise ValueError(
                f"metric_basis 必须形如 '{_METRIC_BASIS_PREFIX}<transform_id>', 得到 "
                f"{value!r}: 这批 *_m 声称量在某次对齐定义的米制世界里, 那就必须指名"
                "道姓地指出是【哪一次】对齐 (内容寻址的 transform_id)"
            )
        return value

    @property
    def basis_transform_id(self) -> str:
        """``metric_basis`` 指名的那次对齐的 transform_id (形状已由校验器保证)。"""
        return self.metric_basis.removeprefix(_METRIC_BASIS_PREFIX)

    @field_validator("n_shared_images")
    @classmethod
    def _enough_shared(cls, value: int) -> int:
        if value < _MIN_EFFECTIVE_CONTROL_POINTS:
            raise ValueError(
                f"标定记录只有 {value} 张共享影像 (<{_MIN_EFFECTIVE_CONTROL_POINTS}), "
                "不足以标定共享中心噪声"
            )
        return value


def _verify_calibration_binds_to_reference(
    record: SharedNoiseCalibration,
    aligned_ref: RegistrationResult,
    resolved: Path,
) -> None:
    """记录声称的米制基准必须【就是本次的参考批】; 对不上即 fail-closed。

    记录里的 ``*_m`` 只在【某一次具体对齐】所定义的米制世界里才是米。若那次对齐不是
    本次用的参考批, 这些数字就不描述本次的任何东西 —— 操作者拿它做的 go/no-go 是
    用一把别的尺量的。
    """
    if aligned_ref.pose_to_world is None or aligned_ref.world_frame is None:
        raise AlignmentError(
            "参考批未对齐 (缺 pose_to_world/world_frame): 标定记录的米制基准无从复核, "
            "而未对齐批次的位姿本就是 SfM 任意坐标; fail-closed"
        )
    expected_basis = _METRIC_BASIS_PREFIX + aligned_ref.pose_to_world.transform_id
    if record.metric_basis != expected_basis:
        raise AlignmentError(
            f"标定记录 {resolved} 的 metric_basis={record.metric_basis!r} 指的不是本次"
            f"参考批那次对齐 (期望 {expected_basis!r})。记录里的 *_m 只在它指名的那次"
            "对齐所定义的米制世界里才是米 —— 换了参考批, 那些数字就不描述本次的任何"
            "东西。常见成因: 参考批重新对齐过 (sim3 变了 -> transform_id 变了) 而记录"
            f"还是旧的; 或记录来自另一对批次。请对【本次的参考批】重跑 {_MEASURE_SCRIPT}; "
            "fail-closed"
        )
    if record.reference_world_frame_id != aligned_ref.world_frame.frame_id:
        raise AlignmentError(
            f"标定记录 {resolved} 的 reference_world_frame_id="
            f"{record.reference_world_frame_id!r} 与参考批的世界 "
            f"{aligned_ref.world_frame.frame_id!r} 对不上: 记录说它量在另一个世界里; "
            "fail-closed"
        )


def load_shared_noise_calibration(
    path: str | Path | None = None,
    *,
    aligned_ref: RegistrationResult,
) -> SharedNoiseCalibration:
    """载入共享噪声标定记录; 缺失/不自洽/【没绑在 aligned_ref 上】即 fail-closed。

    这是【阻断性】前置门, 不是建议。没有真实南台两批 COLMAP 的实测噪声, 跨批次
    对齐给出的每个米制数字都没有地基。

    ``aligned_ref`` 是【必填】的, 因为这个记录的 ``*_m`` 只在某一次具体对齐定义的
    米制世界里才是米 —— 不拿着那次对齐, "是不是米"就无从谈起。做成可选 = 下一个人
    会忘, 而这个洞正是这么来的: 老实现只 ``startswith('reference-pose-to-world:')``,
    从不解析 transform_id、从不比对 world frame, 于是手写
    ``metric_basis='reference-pose-to-world:xf-DOES-NOT-EXIST'`` 就能让一批任意 gauge
    的数字 (实跑: 0.000677m / 0.652m, 真实是 0.0709m / 65.24m) 载入并放行米制门。

    **这道门挡什么** (说准, 别夸大):
      - 记录绑在【别的对齐】上: 换批次、换项目、参考批重对齐后的陈旧记录, 以及凭空
        捏造的 transform_id。transform_id 内容寻址, 编不出一个恰好等于 A 的。

    **它【挡不住】什么 (诚实限制)**: 手里握着参考批的人, 照着它算出真 transform_id,
    再手写一份假的 ``*_m``。这与"手写一份 enu_xyz 控制点 JSON 冒充实测"同属一类 ——
    机器无从分辨, 那是【操作者的测量声称】, 是这条路径的信任根本身 (见
    ``_check_derived_targets_are_declared`` 的同款限制)。本门把记录【绑定】到具体的
    参考批, 它不是、也做不到防伪。别把它当防伪来读。
    """
    resolved = Path(path) if path is not None else _SHARED_NOISE_CALIBRATION_PATH
    if not resolved.is_file():
        raise AlignmentError(
            f"跨批次对齐缺少共享噪声标定记录 ({resolved}): 在拿到两批【真实南台 COLMAP】"
            f"重建的共享相机中心噪声实测值之前, 该路径必须 fail-closed —— 所有误差预算"
            f"随该噪声线性缩放, 未标定就上线等于给未知误差盖米制章。请先跑 "
            f"{_MEASURE_SCRIPT} 产出该记录。"
            f"\n提示: 若你要的是【整村漫游】而不是测量, 走 merge_for_preview —— 漫游只"
            f"需要各批次落在同一个坐标系里, 不需要米制, 故它不需要本标定。"
        )
    try:
        record = SharedNoiseCalibration.model_validate_json(
            resolved.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - 任何不自洽都必须 fail-closed
        raise AlignmentError(
            f"共享噪声标定记录 {resolved} 无法解析或不自洽: {exc}"
        ) from exc
    _verify_calibration_binds_to_reference(record, aligned_ref, resolved)
    return record


def umeyama_sim3(
    src: np.ndarray, dst: np.ndarray, with_scale: bool = True
) -> tuple[float, np.ndarray, np.ndarray]:
    """Closed-form least-squares similarity fit ``dst ~= scale * R @ src + t``.

    Returns ``(scale, R, t)`` where ``R`` is a proper rotation (det=+1).  A
    reflection is prevented by flipping the sign of the last singular direction
    when ``det(U) * det(Vt) < 0`` -- the standard Umeyama reflection guard.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise AlignmentError("umeyama_sim3 requires matching (N, 3) point arrays")
    n = src.shape[0]
    mu_s = src.mean(axis=0)
    mu_d = dst.mean(axis=0)
    src_c = src - mu_s
    dst_c = dst - mu_d
    cov = (dst_c.T @ src_c) / n
    u, d, vt = np.linalg.svd(cov)
    s = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        s[2, 2] = -1.0  # reflection guard: force a proper rotation
    rotation = u @ s @ vt
    if with_scale:
        var_src = (src_c ** 2).sum() / n
        if var_src <= 0:
            raise AlignmentError("source points are coincident; scale is undefined")
        scale = float(np.trace(np.diag(d) @ s) / var_src)
    else:
        scale = 1.0
    translation = mu_d - scale * (rotation @ mu_s)
    return scale, rotation, translation


def _residuals(
    src: np.ndarray, dst: np.ndarray, scale: float, rotation: np.ndarray, t: np.ndarray
) -> tuple[np.ndarray, float, float]:
    """Per-point Euclidean residuals plus their RMS and max, in metres."""
    predicted = scale * (src @ rotation.T) + t
    per_point = np.linalg.norm(dst - predicted, axis=1)
    rms = float(np.sqrt((per_point ** 2).mean()))
    max_residual = float(per_point.max())
    return per_point, rms, max_residual


def _source_span(src: np.ndarray) -> np.ndarray:
    """Singular values (descending) of the centred source points."""
    centred = src - src.mean(axis=0)
    return np.linalg.svd(centred, compute_uv=False)


def _n_effective(points: np.ndarray, radius_m: float) -> int:
    """按米制聚类半径去重后的【有效】点数 (贪心, 输入序决定, 故确定)。

    实测过的 fail-open: 5 个位置各重复 6 次 -> 证据报 n_control_points=30、
    rms=7.5e-15、ACCEPTED。30 行只是 5 个约束, 重复采样不增加可辨识性。点数门作用在
    这个数上: 派生模式下限 8 (标定过), 非派生模式下限 4 (既有的 Sim3 契约)。
    半径由调用方按物理声明 (米), 本模块不编一个。

    **诚实限制**: ``cluster_radius_m`` 为 None 时本函数【不被调用】, 证据里
    n_effective 记 None (= 该门未运行, 不是该门通过)。此时只有精确重复的靶标挡得住
    (那个不需要半径)。近似重复 (例如 GPS 读数抖动几厘米) 在没有物理半径声明的情况下
    【认不出来】—— 定不出有依据的默认半径, 就不编一个。想让这道门开, 传 cluster_radius_m。
    """
    representatives: list[np.ndarray] = []
    for point in points:
        if not any(
            float(np.linalg.norm(point - rep)) <= radius_m for rep in representatives
        ):
            representatives.append(point)
    return len(representatives)


def _holdout_residuals(
    src: np.ndarray, dst: np.ndarray, *, k: int, min_span_ratio: float
) -> tuple[np.ndarray, int]:
    """留出验证 (leave-k-out): 用【没参与拟合】的点量对齐误差, 单位米。

    **它挡什么**: 过拟合。拟合会把噪声吸进 Sim3 的 7 个自由度, 所以 fit rms 系统性
    偏乐观, 点越少越乐观。本仓库实测 n=8 / sigma=0.10m 时 fit 0.1404m 而 held-out
    0.2338m (差 1.67 倍), 见 ``TestUpstreamTermIsNotTheOptimisticFitRms``。留出点
    没参与拟合, 所以它量的是【没被吸收掉的】那部分误差, 而 fit rms 不是。

    **但它【不是】真实误差的上界 —— 别把它当保守估计用**: 实测 6/6 构型下留出/真实
    误差的比值 0.963~1.717 (见 ``TestHoldoutFoldDegeneracy``), seed=12 那一组【低报】
    4%。它与真实误差【同量级、在跟踪】, 仅此而已。本模块任何一处都不许再断言它
    "稳定高于真实误差(保守方向)" —— 那句话被模块自己的测试证伪过。

    **它挡不住什么 (实测证伪了原设计的断言)**: 近共线/近共面退化。原设计称"共线时
    留出点必然爆" —— 不成立。留出点与训练点【同处一个退化子空间】, 绕线轴的旋转歧义
    不移动线上的点, 故留出点对它同样失明。实测 60m 细长航带 (s3/s1=3.1e-3, 高于
    floor): fit 0.0268m、held-out 0.0323m 都很漂亮, 而 100m 外真实误差 4.00m。
    见 tests/test_cross_batch_alignment.py::test_holdout_is_blind_to_collinear_degeneracy。

    **别把留出残差当"真实对齐误差"**: 上面同一个实测里, 留出 0.0323m 而 100m 外真实
    误差 4.00m —— 差两个数量级。留出点只在控制点凸包【内】、且只在【可辨识的方向上】
    验证; 它量的是"拟合有没有把噪声吸进 7 个自由度", 不是"这次对齐对不对"。凸包外的
    外推没有任何门守得住 (见 ``align_to_reference`` 的诚实限制)。

    **训练折退化门判 s2 (共线) 而不是 s3 (共面)**, 依据是实测而非直觉:
      - 共面【不是】Sim3 的退化。Sim3 只有 7 个自由度, 一个平面的点把它定到只剩
        【反射】这一个【离散】歧义, 而 ``umeyama_sim3`` 的 det=+1 守卫已经消掉了反射。
        实测精确共面 (s3/s1=0): sigma=0 时【精确】复原, 出平面 30m 处误差随噪声线性
        走 (sigma=0.02 -> 0.029m, sigma=0.1 -> 0.144m), 【无放大】。
      - 共线【是】真退化: 绕线轴的旋转是【连续】歧义, 没有守卫消得掉。实测 sigma=0
        时离线 30m 处误差就已经 16.33m 且不随噪声变 —— 纯结构性。
    原实现在这里判 s3, 实测【过度拒绝】: 无人机定高航带 + 一张离面点 (全集 s3/s1=0.17,
    是 floor 的 170 倍, 主 s3 门健康放行) 被 6/6 全拒, 而放行后留出 rms 与真实误差
    【同量级】(实测 6/6 比值 0.963~1.717)。**注意这【不是】保守方向**: seed=12 的比值
    0.963 —— 留出 0.0761m 而出平面 30m 处真实误差 0.0790m, 留出【低报】了 4%。放行这个
    构型的依据是"留出残差在跟踪真实误差", 不是"留出残差是上界"。别把它当上界用。
    原归因"约束由个别点独撑 -> 粗差被吸收"也被实测证伪: 那个离面孤点
    上的 5m 粗差让 fit 0.071->1.368m、max 4.459m、留出 1.582m 三个数一起爆, 现有的
    rms/max/留出门看得一清二楚。见 TestHoldoutFoldDegeneracy。

    只有走 ENU 中枢, 留出残差【真是米】, 才能合法与 ``max_rms_m`` 物理预算比。
    """
    n = len(src)
    indices = np.arange(n)
    residuals: list[float] = []
    folds = 0
    for start in range(0, n, k):
        test = indices[start:start + k]
        train = np.setdiff1d(indices, test)
        if len(train) < _MIN_CONTROL_POINTS:
            raise AlignmentError(
                f"留出验证的训练折只剩 {len(train)} 点 (<{_MIN_CONTROL_POINTS}), "
                "无法验证; fail-closed"
            )
        train_src, train_dst = src[train], dst[train]
        singular = _source_span(train_src)
        floor = max(min_span_ratio * float(singular[0]), _ABS_SPAN_FLOOR_SOURCE_UNITS)
        # 判 s2 (共线) 而【不是】s3 (共面) —— 见本函数 docstring 的实测依据: 共面对
        # Sim3 不是退化 (反射是唯一歧义, 已被 umeyama 的 det=+1 守卫消掉), 共线才是
        # (绕线轴的连续旋转歧义, 无守卫消得掉)。
        if singular[0] <= 0 or float(singular[1]) < floor:
            raise AlignmentError(
                "留出验证的训练折退化 (共线): 去掉这一折后剩下的控制点落在一条线上, "
                "绕该线轴的旋转是【连续】歧义 —— 这一折的拟合本身不可辨识, 它的留出"
                "残差因此没有意义, 量不出对齐误差; fail-closed"
            )
        scale, rotation, translation = umeyama_sim3(train_src, train_dst)
        if not np.isfinite(scale) or scale <= 0:
            raise AlignmentError("留出验证的训练折拟合出非正尺度; fail-closed")
        per_point, _, _ = _residuals(
            src[test], dst[test], scale, rotation, translation
        )
        residuals.extend(per_point.tolist())
        folds += 1
    return np.asarray(residuals, dtype=np.float64), folds


def control_points_from_shared_images(
    aligned_ref: RegistrationResult,
    reg_b: RegistrationResult,
    images: list[str] | None = None,
) -> list[ControlPoint]:
    """把两批 SfM 重建【共享的影像】配成控制点, 靶标取参考批 A 的【世界】相机中心。

    两次独立 SfM 重建同一刚性场景, 其规范自由度【恰是一个相似变换 Sim3】(尺度+旋转
    +平移) —— 这是几何事实。但"两次重建之差【实际上】就只是一个 Sim3"是【经验问题】,
    而它只在一个作用域里被测过:

        COLMAP 4.1.0 两次独立重建, 输入是 **Blender 渲染的合成图** (24 张 / 45m 尺度 /
        emission-only / 高频程序纹理) —— 即 SfM 的【最优情况】。残差 1.49mm / 45.13m
        = 6.8e-05。见 ``docs/verification/2026-07-17-derived-mode-control-point-floor.md``。

    **别把这句读成"真实照片上确认过"**: COLMAP 这个软件是真的, 喂给它的照片是合成的。
    真实照片有视角相关高光、曝光漂移、运动模糊、夯土墙/瓦屋面的重复自相似纹理、植被
    摆动 —— 噪声必然更高, 高多少【无人知】。且【村庄尺度的 BA drift 完全未测, 那是最
    可能推翻本方案的一条】: 45m 上成立不蕴含几百米的长走廊采集上成立。故共享影像的
    相机中心【在合成最优情况下】是近乎精确的对应; 真实南台上是不是, 没测过。

    **必须先过 ``aligned_ref.pose_to_world.sim3``**: ``RegistrationResult.poses``
    在 ``pose_frame`` 里【不在 world frame 里】, 直接读 ``t_xyz`` 会把 A 的 SfM 任意
    坐标当成 ``enu_xyz`` 静默污染 —— 那正是本仓库存在的理由要挡的事。

    走 ENU 中枢而【不做 B->A】: 实测 ``align_registration(world_frame_id='sfm-local-A')``
    仍会吐 axes=enu-z-up / units=meters / metric / aligned, 即为一个纯任意 frame
    伪造米制+geo 声称。故本函数只产 ``enu_xyz`` 靶标, 不提供任何 B->A 入口。

    注意: 产出的靶标是【派生】的, 不是物理测量。误差【会复合】(B 的总误差 >= A 的
    锚定误差 + B 的拟合误差), 且 B 挣得的不是 A 的米制信任本身。请走
    ``align_to_reference``, 它强制留出验证 / n_effective / 误差复合三道门并记录
    ``control_target_provenance``; 直接把本函数的输出喂 ``align_registration``
    会绕过这些门。
    """
    if (
        aligned_ref.alignment_status is not AlignmentStatus.ALIGNED
        or aligned_ref.pose_to_world is None
        or aligned_ref.world_frame is None
    ):
        raise AlignmentError(
            "参考批未对齐 (需要 alignment_status=ALIGNED 且有 pose_to_world/world_frame): "
            "未对齐批次的位姿是 SfM 任意坐标, 拿它当米制靶标就是凭空造米"
        )
    world = aligned_ref.world_frame
    if not (
        world.units is CoordinateUnits.METERS
        and world.metric_status is MetricStatus.METRIC
        and world.geo_aligned is GeoAlignment.ALIGNED
        and world.axes is AxisConvention.ENU_Z_UP
    ):
        raise AlignmentError(
            f"参考批的 world_frame {world.frame_id!r} 不是米制 ENU "
            f"(units={world.units}, metric_status={world.metric_status}, "
            f"geo_aligned={world.geo_aligned}, axes={world.axes}): 它的位姿不是米, "
            "不能当 enu_xyz 靶标"
        )

    ref_poses = {pose.image: pose for pose in aligned_ref.poses}
    b_images = {pose.image for pose in reg_b.poses}
    shared = sorted(set(ref_poses) & b_images)
    if images is not None:
        requested = set(images)
        missing = sorted(requested - set(shared))
        if missing:
            raise AlignmentError(
                f"请求的共享影像未同时出现在两批重建里: {missing}"
            )
        shared = [image for image in shared if image in requested]

    sim3 = aligned_ref.pose_to_world.sim3
    control_points: list[ControlPoint] = []
    for image in shared:
        local = np.asarray([ref_poses[image].t_xyz], dtype=np.float64)
        enu = sim3.apply(local)[0]
        if not np.all(np.isfinite(enu)):
            raise AlignmentError(
                f"共享影像 {image!r} 的参考世界坐标非有限; fail-closed"
            )
        control_points.append(
            ControlPoint(
                label=image,
                image=image,
                enu_xyz=tuple(enu.tolist()),
                # 派生性跟着靶标走: 使用处 (align_registration) 据此强制派生模式的
                # 三道门, 洗白旁路因而在【使用处】被机器可验证地挡住, 而不是靠本
                # docstring 劝阻。
                derived_from_alignment=aligned_ref.pose_to_world.transform_id,
            )
        )
    return control_points


def build_control_points(
    reg: RegistrationResult,
    control_points: list[ControlPoint],
    origin: GeoAnchor | None,
) -> list[tuple[np.ndarray, np.ndarray, str]]:
    """Resolve ``ControlPoint`` specs into ``(sfm_xyz, enu_xyz, label)`` triples.

    ``image`` sources resolve to that pose's camera centre (``CameraPose.t_xyz``)
    in ``reg.pose_frame``; ``geo`` targets reduce through ``gps_to_enu`` against
    ``origin``.  Anything unresolved fails closed.
    """
    poses_by_image: dict[str, CameraPose] = {p.image: p for p in reg.poses}
    resolved: list[tuple[np.ndarray, np.ndarray, str]] = []
    for cp in control_points:
        if cp.source_xyz is not None:
            sfm = np.asarray(cp.source_xyz, dtype=np.float64)
        else:
            pose = poses_by_image.get(cp.image)
            if pose is None:
                raise AlignmentError(
                    f"control point {cp.label!r} references unknown image {cp.image!r}"
                )
            sfm = np.asarray(pose.t_xyz, dtype=np.float64)
        if cp.enu_xyz is not None:
            enu = np.asarray(cp.enu_xyz, dtype=np.float64)
        else:
            if origin is None:
                raise AlignmentError(
                    f"control point {cp.label!r} uses a GPS anchor but no geo origin"
                    " is available"
                )
            enu = np.asarray(gps_to_enu(cp.geo, origin), dtype=np.float64)
        if not (np.all(np.isfinite(sfm)) and np.all(np.isfinite(enu))):
            raise AlignmentError(
                f"control point {cp.label!r} resolved to non-finite coordinates"
            )
        resolved.append((sfm, enu, cp.label))
    return resolved


def control_points_from_geo_anchors(
    reg: RegistrationResult,
    image_anchors: dict[str, GeoAnchor],
) -> list[ControlPoint]:
    """把逐图 geo 锚点 (通常自 EXIF GPS 派生) 配对成对齐控制点, 直接喂 align_registration。

    让 GPS 标记的采集免手工逐图写 ControlPoint 即可 turnkey 米制对齐: 每张【既注册
    (在 ``reg.poses`` 有位姿) 又有锚点】的图 → 一个 ``ControlPoint(image=..., geo=...)``,
    source 侧解析为该位姿的相机中心, target 侧经 ``gps_to_enu`` 归约。未注册或无锚点的
    图静默排除 (无对应即无证据)。按 image 排序, 输出确定。

    本函数只【组装证据】, 绝不提升信任: 拟合门 (>=3 点、退化守卫、RMS 阈值) 仍由
    ``fit_sfm_to_enu`` / ``align_registration`` 权威裁决, 证据不足/不一致照样 fail-closed。
    ``GpsObservation`` (ingest EXIF) 可平凡转 ``GeoAnchor(lat, lon, alt=altitude_m or 0.0)``。
    """
    registered = {pose.image for pose in reg.poses}
    return [
        ControlPoint(label=image, image=image, geo=image_anchors[image])
        for image in sorted(image_anchors)
        if image in registered
    ]


def fit_sfm_to_enu(
    control_points: list[tuple[np.ndarray, np.ndarray, str]],
    geo_origin: GeoAnchor,
    *,
    max_rms_m: float = 2.0,
    min_span_ratio: float = 1e-3,
    control_target_provenance: str | None = None,
    upstream_alignment_rms_m: float | None = None,
    cluster_radius_m: float | None = None,
    holdout_k: int = 1,
) -> tuple[Sim3, Sim3AlignmentEvidence]:
    """Fit a gated Sim3 from resolved control points; fail closed on any gate.

    Gates (all fail-closed): >=4 non-coplanar finite control points, non-degenerate
    source span, fitted ``scale > 0``, and ``rms_residual <= max_rms_m``.  Returns
    the ``(Sim3, Sim3AlignmentEvidence)`` only when every gate passes; otherwise
    raises ``AlignmentError`` and emits nothing.

    **派生靶标模式**: 传 ``control_target_provenance`` 即声明"这批 ``enu_xyz`` 不是
    物理测量, 而是从另一次对齐【派生】的" (跨批次对齐)。该模式下三道门【强制】开启,
    没有关闭开关 —— 因为触发它们的条件正是风险本身:

    1. 留出验证: 留出点残差 (米) 必须 <= ``max_rms_m``。它挡的是【过拟合】(fit rms
       把噪声吸进 7 个自由度故偏乐观)。它【挡不住】近共线退化, 而 s3 门也只挡得住
       【精确】退化 —— 实测近共线航带 (s3/s1=3.1e-3, 高于 1e-3 floor) 两道门全放行,
       而 100m 外真实误差 4.00m。别以为这两道门之间有兜底: 【没有】。
    2. ``n_effective`` >= 8: 按 ``cluster_radius_m`` 去重后的有效点数, 不是 ``len()``。
    3. 误差复合: ``upstream_alignment_rms_m + holdout_rms`` 必须 <= ``max_rms_m``。
       取【和】而非平方和 —— 求和更保守, 不引入误差独立性假设。

    该模式下 ``upstream_alignment_rms_m`` 与 ``cluster_radius_m`` 必须显式给出:
    缺一个就意味着对应的门无法判定, 故 fail-closed 而不是当作 0/无穷。
    """
    derived = control_target_provenance is not None
    if derived:
        if upstream_alignment_rms_m is None:
            raise AlignmentError(
                "派生靶标模式必须给出 upstream_alignment_rms_m: 靶标源自上游对齐, "
                "本批总误差 >= 上游锚定误差 + 本批拟合误差。上游误差未知 = 总误差"
                "未知, 不能假装它是 0"
            )
        if not np.isfinite(upstream_alignment_rms_m) or upstream_alignment_rms_m < 0:
            raise AlignmentError(
                f"upstream_alignment_rms_m 必须有限且非负, 得到 {upstream_alignment_rms_m}"
            )
        if cluster_radius_m is None:
            raise AlignmentError(
                "派生靶标模式必须给出聚类半径 cluster_radius_m (米): 聚簇的控制点会"
                "高报约束强度 (实测 5 个位置重复 6 次 -> 证据报 30 点、rms=7.5e-15、"
                "ACCEPTED)。本模块不替调用方编一个半径"
            )
        if not np.isfinite(cluster_radius_m) or cluster_radius_m <= 0:
            raise AlignmentError(
                f"cluster_radius_m 必须有限且为正, 得到 {cluster_radius_m}"
            )

    # holdout_k 的守卫【与模式无关】: 非派生模式也会算留出 (只记录不裁决), 而
    # holdout_k=0 会让 range(0, n, 0) 抛裸 ValueError —— 调用方的 except AlignmentError
    # 接不住它, allow_unaligned_fallback 也兜不住。
    if holdout_k < 1:
        raise AlignmentError(f"holdout_k 必须 >=1, 得到 {holdout_k}")

    if len(control_points) < _MIN_CONTROL_POINTS:
        raise AlignmentError(
            f"need >={_MIN_CONTROL_POINTS} non-coplanar control points to fit a Sim3, "
            f"got {len(control_points)}. "
            f"({_MIN_CONTROL_POINTS - 1} 点去心后秩恒 <=2, 即恒共面: 最小奇异值恒为 0, "
            "共面守卫必然拒绝。所以 >=4 且非共面才是真实契约。)"
        )
    src = np.array([cp[0] for cp in control_points], dtype=np.float64)
    dst = np.array([cp[1] for cp in control_points], dtype=np.float64)
    labels = tuple(cp[2] for cp in control_points)
    if not (np.all(np.isfinite(src)) and np.all(np.isfinite(dst))):
        raise AlignmentError("non-finite control points")

    singular_values = _source_span(src)
    span_floor = max(min_span_ratio * float(singular_values[0]), _ABS_SPAN_FLOOR_SOURCE_UNITS)
    if singular_values[0] <= 0 or float(singular_values[2]) < span_floor:
        raise AlignmentError(
            "degenerate control-point span (collinear/coplanar): "
            f"singular values {singular_values.tolist()} below floor {span_floor:g}"
        )

    # 精确重复的靶标: 不需要编任何半径就能认出来, 故【所有模式】都挡。真实触发是
    # --from-gps 的一次连拍复用同一个 EXIF GPS 定位读数 -> dst 逐簇精确重合。
    n_distinct = len(np.unique(dst, axis=0))
    if n_distinct < _MIN_CONTROL_POINTS:
        raise AlignmentError(
            f"{len(control_points)} 个控制点只落在 {n_distinct} 个【互不相同】的靶标"
            f"位置上 (<{_MIN_CONTROL_POINTS}): 重复采样同一位置不增加约束, 只会把点数"
            "撑大, 而近零的 RMS 恰恰是'RMS 与对齐正确性脱钩'本身。常见成因: 一次连拍"
            "内 EXIF GPS 复用了同一个定位读数; fail-closed"
        )

    n_effective: int | None = None
    if cluster_radius_m is not None:
        n_effective = _n_effective(dst, cluster_radius_m)
        # 派生模式用更严的 8 (【政策选择, 非标定值】—— 见 _MIN_EFFECTIVE_CONTROL_POINTS
        # 的实测复核); 非派生模式用既有的 >=4 契约 —— 只是把它作用在【有效】点数上
        # 而不是 len()。
        floor = _MIN_EFFECTIVE_CONTROL_POINTS if derived else _MIN_CONTROL_POINTS
        if n_effective < floor:
            reason = (
                "派生靶标的误差会复合, 故这条路要求更密的共享影像 (>=8)。注意 8 是"
                "【政策选择不是标定值】: 实测凸包内误差随点数平滑改善、并不在 8 处"
                "进平台, 见 docs/verification/2026-07-17-derived-mode-control-point-floor.md"
                if derived else
                f"Sim3 需要 >={_MIN_CONTROL_POINTS} 个非共面控制点, 这个契约作用在"
                "【有效】点数上才有意义"
            )
            raise AlignmentError(
                f"n_effective={n_effective} 个有效控制点 (<{floor}), 原始 "
                f"{len(control_points)} 个点在 {cluster_radius_m}m 半径内聚成 "
                f"{n_effective} 簇: 重复采样同一位置不增加约束, 只会把点数撑大。{reason}"
            )

    scale, rotation, translation = umeyama_sim3(src, dst)
    if not np.isfinite(scale) or scale <= 0:
        raise AlignmentError(f"non-positive or non-finite scale: {scale}")

    per_point, rms, max_residual = _residuals(src, dst, scale, rotation, translation)

    holdout_rms: float | None = None
    holdout_max: float | None = None
    holdout_folds: int | None = None
    failure: str | None = None
    # 留出【算得动就算, 不管派生与否】: 下游的误差复合门要拿这个数当上游项。非派生
    # 对齐 (实测控制点 / GPS 锚, 即绝大多数 A) 原本不算留出, 于是复合门只能回落到
    # fit rms —— 而留出门存在的全部理由就是不信 fit rms (它把噪声吸进 7 个自由度故
    # 系统性偏乐观, 实测同一组 8 点 fit 0.1404m vs 留出 0.2338m, 低报 40%)。拿 fit
    # rms 当上游预算是 fail-open 方向, 且与留出门自相矛盾。
    #
    # 但【只有派生模式拿它当门】: 非派生模式没有留出门是 HEAD 的既有契约, 收紧它会
    # 凭空拒掉一批 HEAD 接受的对齐, 不在本次授权范围。故非派生模式只记录不裁决。
    if derived or len(control_points) - holdout_k >= _MIN_CONTROL_POINTS:
        try:
            holdout, holdout_folds = _holdout_residuals(
                src, dst, k=holdout_k, min_span_ratio=min_span_ratio
            )
        except AlignmentError:
            if derived:
                raise  # 派生模式: 留出是【门】, 算不出就 fail-closed
            # 非派生模式: 留出只是记录。算不出 -> 记 None (= 该门未运行), 下游复合
            # 只能回落到偏乐观的 fit rms —— 与 HEAD 同, 不是新的 fail-open。
            holdout, holdout_folds = None, None
        if holdout is not None:
            holdout_rms = float(np.sqrt((holdout ** 2).mean()))
            holdout_max = float(holdout.max())
    if derived:
        if not np.isfinite(holdout_rms):
            failure = "留出验证残差非有限; fail-closed"
        elif holdout_rms > max_rms_m:
            failure = (
                f"held-out (留出验证) rms {holdout_rms:.4f}m 超过 max_rms {max_rms_m}m "
                f"—— 尽管拟合 rms 只有 {rms:.4g}m。fit rms 把噪声吸进了 Sim3 的 7 个"
                "自由度, 故系统性偏乐观; 留出点没参与拟合, 量到的是【控制点凸包内】的"
                "真实拟合误差。注意反过来【不成立】: 留出 rms 达标不代表对齐正确 ——"
                "它对共线/共面退化与凸包外的外推同样失明 (实测留出 0.03m 而 100m 外"
                "真实误差 4.00m)。拒绝盖米制章"
            )
        else:
            compound = float(upstream_alignment_rms_m) + holdout_rms
            if compound > max_rms_m:
                failure = (
                    f"误差复合门: 上游对齐 rms {upstream_alignment_rms_m:.4f}m + 本批"
                    f"留出 rms {holdout_rms:.4f}m = {compound:.4f}m 超过 max_rms "
                    f"{max_rms_m}m (compound error exceeds budget)。靶标派生自上游"
                    "对齐, 故误差会累加 —— 两项各自单独达标不代表总误差达标"
                )
    if failure is None and rms > max_rms_m:
        failure = (
            f"rms_residual {rms:.3f}m exceeds max_rms {max_rms_m}m; "
            "refusing to emit an aligned world frame"
        )
    passed = failure is None

    # Sim3 itself re-validates orthogonality and rejects reflections; building it
    # here means a bad rotation fails closed before any evidence is trusted.
    sim3 = Sim3(
        scale=scale,
        rotation_matrix_xyz=tuple(tuple(row) for row in rotation.tolist()),
        t_xyz=tuple(translation.tolist()),
    )
    evidence = Sim3AlignmentEvidence(
        method="umeyama-sim3",
        n_control_points=len(control_points),
        scale=scale,
        rms_residual_m=rms,
        max_residual_m=max_residual,
        per_point_residual_m=tuple(per_point.tolist()),
        source_singular_values=tuple(float(v) for v in singular_values.tolist()),
        min_span_ratio=min_span_ratio,
        max_rms_threshold_m=max_rms_m,
        geo_origin={
            "lat": geo_origin.lat,
            "lon": geo_origin.lon,
            "alt": geo_origin.alt,
        },
        control_point_labels=labels,
        passed=passed,
        n_effective_control_points=n_effective,
        holdout_rms_m=holdout_rms,
        holdout_max_m=holdout_max,
        holdout_folds=holdout_folds,
        upstream_alignment_rms_m=upstream_alignment_rms_m,
        control_target_provenance=control_target_provenance,
    )
    if not passed:
        raise AlignmentError(failure)
    return sim3, evidence


def _derived_provenance(transform_id: str) -> str:
    """派生靶标的 provenance 串的唯一构造点 (标记 <-> 声明必须逐字对得上)。"""
    return f"derived-from-alignment:{transform_id}"


def _check_derived_targets_are_declared(
    control_points: list[ControlPoint], declared: str | None
) -> None:
    """靶标自带的派生标记必须与调用方声明的 provenance 相符; 否则 fail-closed。

    这堵的是实测过的【洗白旁路】: ``control_points_from_shared_images`` 的输出直接
    喂 ``align_registration``, provenance 默认 None -> derived=False -> 留出 /
    n_effective / 误差复合三门全部静默跳过 -> ACCEPTED, 而产出的证据与一次真实实测
    控制点对齐【逐字段不可区分】。随后把这个洗白的 B 当参考批喂【认可入口】
    align_to_reference, 下游 C 就继承 upstream≈0 而实际继承了 A 的锚定误差 ——
    误差复合门 (整个设计的绑定约束) 在唯一认可入口里被打穿。

    诚实限制: 本门只挡【标记存在却不声明】。手写一份 enu_xyz 控制点 JSON 冒充实测,
    机器无从分辨 —— 那时 enu_xyz 是操作者的物理测量声称, 是这条路径的信任根本身。
    """
    sources = {cp.derived_from_alignment for cp in control_points
               if cp.derived_from_alignment is not None}
    if not sources:
        return
    if declared is None:
        raise AlignmentError(
            f"控制点自带派生标记 (derived_from_alignment={sorted(sources)}): 这批 "
            "enu_xyz 是从另一次对齐【派生】的, 不是物理测量, 误差会复合。必须走 "
            "align_to_reference (它强制留出验证 / n_effective / 误差复合三道门并从"
            "参考批证据里读出上游误差); 直接喂 align_registration 会绕过这三道门, "
            "产出与实测对齐不可区分的证据; fail-closed"
        )
    expected = {_derived_provenance(t) for t in sources}
    if len(expected) > 1:
        raise AlignmentError(
            f"控制点混了多个派生来源 {sorted(sources)}: 单个 provenance 串表达不了"
            "多来源的误差复合; fail-closed"
        )
    if declared not in expected:
        raise AlignmentError(
            f"声明的 control_target_provenance={declared!r} 与靶标自带的派生标记 "
            f"{sorted(expected)!r} 对不上: 派生来源只能来自靶标本身的机器可验证标记, "
            "不能由调用方自报; fail-closed"
        )


def align_registration(
    reg: RegistrationResult,
    control_points: list[ControlPoint],
    *,
    geo_origin: GeoAnchor | None = None,
    world_frame_id: str = "world-enu",
    max_rms_m: float = 2.0,
    min_span_ratio: float = 1e-3,
    method: TransformMethod | None = None,
    allow_unaligned_fallback: bool = False,
    control_target_provenance: str | None = None,
    upstream_alignment_rms_m: float | None = None,
    cluster_radius_m: float | None = None,
) -> RegistrationResult:
    """Return an ALIGNED copy of ``reg`` in ``world-enu``, or fail closed.

    On success the returned registration carries a measured ``world-enu``
    ``world_frame``, a ``pose_to_world`` ``FrameTransform`` (both bearing the
    ``sim3.alignment.v1`` evidence), and ``alignment_status=ALIGNED``.  On any
    gate failure it raises ``AlignmentError`` -- or, when
    ``allow_unaligned_fallback`` is True, returns ``reg`` **unchanged** (still
    sfm-local / UNALIGNED, never partially mutated).  Arbitrary geometry is never
    silently promoted to metres.

    带 ``derived_from_alignment`` 标记的控制点 (即靶标派生自另一次对齐, 见
    ``control_points_from_shared_images``) 【必须】声明与之相符的
    ``control_target_provenance``, 否则 fail-closed —— 派生模式的三道门不许被绕过。
    正常用法是走 ``align_to_reference``, 它自己填这些参数。
    """
    _check_derived_targets_are_declared(control_points, control_target_provenance)
    origin = geo_origin if geo_origin is not None else reg.geo_origin
    if origin is None:
        raise AlignmentError(
            "a geo origin is required to define the world-enu frame "
            "(pass geo_origin or set reg.geo_origin)"
        )
    try:
        resolved = build_control_points(reg, control_points, origin)
        sim3, evidence = fit_sfm_to_enu(
            resolved,
            origin,
            max_rms_m=max_rms_m,
            min_span_ratio=min_span_ratio,
            control_target_provenance=control_target_provenance,
            upstream_alignment_rms_m=upstream_alignment_rms_m,
            cluster_radius_m=cluster_radius_m,
        )
    except AlignmentError:
        if allow_unaligned_fallback:
            return reg  # unchanged: still sfm-local / UNALIGNED (atomic)
        raise

    evidence_str = evidence.to_evidence()
    world_frame = CoordinateFrame(
        frame_id=world_frame_id,
        handedness=Handedness.RIGHT,
        axes=AxisConvention.ENU_Z_UP,
        units=CoordinateUnits.METERS,
        metric_status=MetricStatus.METRIC,
        geo_aligned=GeoAlignment.ALIGNED,
        provenance=FrameProvenance.MEASURED,
        evidence=(
            "sfm-to-enu-sim3-alignment",
            f"geo-origin:{origin.lat},{origin.lon},{origin.alt}",
            evidence_str,
        ),
    )
    if method is None:
        # GPS-derived targets => GPS_ANCHOR; explicit surveyed ENU => CONTROL_POINTS.
        used_gps = any(cp.geo is not None for cp in control_points)
        method = (
            TransformMethod.GPS_ANCHOR if used_gps else TransformMethod.CONTROL_POINTS
        )
    transform = FrameTransform(
        source_frame=reg.pose_frame.frame_id,
        target_frame=world_frame_id,
        sim3=sim3,
        method=method,
        evidence=(evidence_str,),
    )
    return reg.model_copy(
        update={
            "world_frame": world_frame,
            "pose_to_world": transform,
            "alignment_status": AlignmentStatus.ALIGNED,
        }
    )


def _effective_rms_m(evidence: Sim3AlignmentEvidence) -> float:
    """一次对齐的【总】误差 (米), 含它自己的上游 —— 让误差沿对齐链累加。

    若参考批本身就是派生对齐的 (A->B->C 链), 它的 ``rms_residual_m`` 只是它这一环
    的拟合残差, 不含它继承的上游误差。取【留出残差 (若有) + 它的上游】才是这一环
    对下游而言的真实误差。求和不假设独立性, 保守。

    优先取留出残差是刻意的: fit rms 把噪声吸进 Sim3 的 7 个自由度, 故系统性偏乐观
    (实测同一组 8 点 fit 0.1404m vs 留出 0.2338m, 低报 40%)。留出门存在的全部理由
    就是不信 fit rms, 复合门自然不能转头拿 fit rms 当上游预算 —— 那是 fail-open 方向。

    **诚实限制**: 留出算不动时 (n<=4, 或折退化) 只能回落到偏乐观的 fit rms。那不是
    "够好", 是【定不出更好的数】—— 此时下游的米制声称比它看起来的更松。
    """
    own = (
        evidence.holdout_rms_m
        if evidence.holdout_rms_m is not None
        else evidence.rms_residual_m
    )
    return float(own) + float(evidence.upstream_alignment_rms_m or 0.0)


def _reference_alignment_evidence(
    aligned_ref: RegistrationResult,
) -> Sim3AlignmentEvidence:
    """从参考批的 ``pose_to_world`` 里【读出】它的对齐证据; 无证据即 fail-closed。

    刻意不接受调用方自报上游误差: 上游误差是下游米制声称的一部分, 必须来自机器可
    验证的证据, 不能来自"看着像"。
    """
    for item in aligned_ref.pose_to_world.evidence:
        try:
            evidence = Sim3AlignmentEvidence.parse(item)
        except ValueError:
            continue
        if not evidence.passed:
            raise AlignmentError(
                "参考批的 sim3.alignment.v1 证据自报 passed=False: 它本身没通过门, "
                "不能当米制靶标的来源"
            )
        return evidence
    raise AlignmentError(
        "参考批的 pose_to_world 没有可解析的 sim3.alignment.v1 证据: 拿不到上游对齐"
        "误差就无法复合误差, 也就无法判定本批的米制声称是否在预算内; fail-closed"
    )


def align_to_reference(
    aligned_ref: RegistrationResult,
    reg_b: RegistrationResult,
    *,
    max_rms_m: float,
    cluster_radius_m: float,
    images: list[str] | None = None,
    min_span_ratio: float = 1e-3,
    calibration_path: str | Path | None = None,
) -> RegistrationResult:
    """把批次 B 对齐进【参考批 A 的 ENU 世界】, 靠两批共享的影像; 否则 fail closed。

    这是跨批次对齐的【唯一】认可入口 —— 整村漫游要靠它把塞不进一次重建的村子分批
    缝起来。它做的事: 取共享影像在 A 世界里的米制相机中心当靶标 -> Umeyama Sim3 ->
    B 进同一个 ENU 世界。误差预算 ``max_rms_m`` 与聚类半径 ``cluster_radius_m``
    【必须由调用方按物理声明】, 无默认值: 本模块编不出这两个数, 就不编。

    与 ``align_registration`` 的区别在于三道【强制】门 (见 ``fit_sfm_to_enu`` 的派生
    靶标模式) 外加上线前置标定门, 且上游误差与靶标来源都从 A 的证据里【读出】而非
    由调用方自报 —— 故本函数刻意不暴露 ``control_target_provenance`` /
    ``upstream_alignment_rms_m`` / ``world_frame_id`` 参数。

    如实说明的限制 (别藏):
    - **地基未测**: 真实南台照片跑两次 COLMAP 的共享相机中心噪声无人测过。所有误差
      数字随它线性缩放。故未标定时本函数 raise (见 ``load_shared_noise_calibration``)。
    - **村庄尺度 BA drift 完全未测, 这是最可能推翻本方案的一条**: 只在 45m 尺度确认
      了"残差是无结构噪声, Sim3 就是全部故事"。整村是几百米上千张的长走廊采集, 两批
      【各自】带自己的累积 drift -> 差异可能不再是一个全局 Sim3, 而是 Sim3 + 低频
      warp。而【正是 drift 才迫使你分批】。若这条不成立, 需要分段对齐而非一个全局 Sim3。
    - **外推没有任何门守得住**: 留出验证只在控制点凸包【内】验证, 但被对齐的几何全在
      重叠带【外】。实测近共面构型下 105 个变换全过 2.0m RMS 门却在 100m 外造成 18m
      歧义。诚实结论: 整村漫游【远离缝合带处的几何不可用于测量】, 而现有二值
      MetricStatus 表达不了"缝合处米制、远离处衰减"。
    - **绝对精度天花板是 A 的锚定**: A 若靠消费级 GPS (3-10m), 全村绝对精度就是米级,
      B 对得再准也超不过。
    - **全集共面的采集 (定高飞行/等高平走且无任何离面影像) 会被拒**: 主 s3 共面门
      原样保留的代价 (放宽 = 提升信任, 故不动)。诚实补充: 实测表明共面对 Sim3 【不是】
      退化 (见 ``_holdout_residuals`` 的实测依据), 所以这道拒绝是【保守政策】而非
      几何必然 —— 它挡的是"没有离面观测时无法交叉验证出平面方向"这个风险。只要采集
      里有【一张】离面影像 (仰拍/塔顶/爬高), 全集就不共面, 这道门即放行。
    - **留出折的退化门只挡【共线】折, 不挡共面折**: 依据是实测 (共面折的拟合精确可辨识,
      共线折有连续歧义)。原实现判 s3 会把"定高航带 + 一张离面点"这类最常见采集 6/6
      全拒, 而实测那些对齐是对的。
    - **B 挣得的不是 A 的米制信任**: 严格说 B 挣得的是"坐标与 A 一致 + 对一批派生靶标
      的拟合"。派生性由 ``control_target_provenance`` 记录, 消费者必须自己复合误差。
    - **下游仍有已实测的 fail-open 会绕过本函数的门** (reconstruct.py 的证据池化 /
      metric_evidence 存在性检查 / geometry_usability 单标量), 本次范围外。
    """
    if aligned_ref.pose_to_world is None or aligned_ref.world_frame is None:
        raise AlignmentError("参考批未对齐: 缺 pose_to_world/world_frame")
    # 标定门在参考批的完整性检查【之后】: 复核记录绑没绑对参考批, 前提是先有参考批
    # 的 pose_to_world 可绑。
    load_shared_noise_calibration(calibration_path, aligned_ref=aligned_ref)

    if reg_b.pose_frame.frame_id == aligned_ref.pose_frame.frame_id:
        raise AlignmentError(
            f"两批的 pose_frame 同为 {reg_b.pose_frame.frame_id!r}: 两次独立重建各有"
            "自己的任意 frame, 同名意味着调用方把它们搞混了; fail-closed"
        )
    if reg_b.pose_frame.frame_id == aligned_ref.world_frame.frame_id:
        raise AlignmentError(
            f"批次 B 的 pose_frame 与参考世界同名 ({reg_b.pose_frame.frame_id!r}): "
            "B 是未对齐的 SfM 任意 frame, 不能自称就是 ENU 世界"
        )

    upstream = _reference_alignment_evidence(aligned_ref)
    origin = aligned_ref.geo_origin
    if origin is None:
        raise AlignmentError(
            "参考批没有 geo_origin: ENU 中枢需要切平面原点才能定义世界; fail-closed"
        )

    control_points = control_points_from_shared_images(aligned_ref, reg_b, images)
    # 靶标来源可溯: transform_id 内容寻址, 任何消费者可拿 A 复核这批 enu_xyz 是
    # 【派生】的而非物理测量的 —— 而不是靠调用方/文件名自称。
    provenance = _derived_provenance(aligned_ref.pose_to_world.transform_id)
    return align_registration(
        reg_b,
        control_points,
        geo_origin=origin,
        world_frame_id=aligned_ref.world_frame.frame_id,
        max_rms_m=max_rms_m,
        min_span_ratio=min_span_ratio,
        control_target_provenance=provenance,
        upstream_alignment_rms_m=_effective_rms_m(upstream),
        cluster_radius_m=cluster_radius_m,
    )


def _shared_pose_centres(
    reg_a: RegistrationResult,
    reg_b: RegistrationResult,
    images: list[str] | None,
) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]]:
    """共享影像的相机中心, 两侧都留在【各自的 pose_frame】里 (都是任意单位)。

    这【不是】``control_points_from_shared_images`` 的复制: 那个函数刻意只产 ENU 米制
    靶标 (要求 A 已对齐), 因为它服务的是米制路。这里两侧都是任意 gauge —— preview
    要的恰恰是"别碰米", 所以它连 A 的 pose_to_world 都不看。
    """
    a_poses = {pose.image: pose for pose in reg_a.poses}
    b_poses = {pose.image: pose for pose in reg_b.poses}
    shared = sorted(set(a_poses) & set(b_poses))
    if images is not None:
        requested = set(images)
        missing = sorted(requested - set(shared))
        if missing:
            raise AlignmentError(
                f"请求的共享影像未同时出现在两批重建里: {missing}"
            )
        shared = [image for image in shared if image in requested]
    src = np.array([b_poses[i].t_xyz for i in shared], dtype=np.float64)
    dst = np.array([a_poses[i].t_xyz for i in shared], dtype=np.float64)
    if len(shared) and not (np.all(np.isfinite(src)) and np.all(np.isfinite(dst))):
        raise AlignmentError("共享影像的相机中心含非有限坐标; fail-closed")
    return src, dst, tuple(shared)


def merge_for_preview(
    reg_a: RegistrationResult,
    reg_b: RegistrationResult,
    *,
    max_relative_rms: float,
    images: list[str] | None = None,
    min_span_ratio: float = 1e-3,
    holdout_k: int = 1,
) -> RegistrationResult:
    """把 B 缝进【A 的 pose_frame】以便整村漫游 —— preview-only, 【绝不声称米制】。

    **为什么存在**: 整村塞不进一次 COLMAP 重建, 必须分批再缝起来。而【漫游不需要
    米制】—— 它只需要各批次落在【同一个坐标系】里。米制跨批次对齐 (``align_to_reference``)
    今天不可用: 地基未测 (真实南台照片跑两次 COLMAP 的共享相机中心噪声无人测过, 所有
    误差预算随它线性缩放)。那道 fail-closed 是对的, 但它【不该连坐漫游】: 把 B 搬进 A
    的任意 frame 不产生任何米制声称, 故它不需要那个标定。

    **产物诚实地不可测量**: 目标 frame 原样继承 A 的 ``pose_frame`` 契约
    (units=arbitrary / metric_status=arbitrary / geo_aligned=unaligned),
    ``alignment_status`` 保持 UNALIGNED。下游 ``reconstruct._derive_geometry_usability``
    因此自动判 preview-only —— 不靠本函数自称 (由
    ``TestPreviewOnlyMerge::test_preview_merge_is_classified_preview_only_downstream``
    走真实消费者的分类器钉住)。证据串前缀 ``sim3.preview-merge.v1=``
    与米制的 ``sim3.alignment.v1=`` 【互不可解析】, 所以 preview 产物无法被拿去当
    ``align_to_reference`` 的参考批洗白成米制上游。

    **判据是无量纲的 ``relative_rms``** (= rms / 靶标跨度), 不是米: A 的 gauge 是任意
    的 (COLMAP 常把双视图基线取单位长), 拿米制预算去比一批任意单位的残差正是本仓库
    存在的理由要挡的事。``max_relative_rms`` 【无默认值】—— 本模块编不出这个数就不编
    (与 ``align_to_reference`` 的 ``max_rms_m`` / ``cluster_radius_m`` 同规矩)。参考
    标尺: 两次真实 COLMAP 重建的相对残差, 合成最优情况下实测 6.8e-05 (见
    ``docs/verification/2026-07-17-derived-mode-control-point-floor.md`` 的作用域限定),
    真实照片必然更高但高多少无人知。

    **A 不需要已对齐**: 落进 A 的任意 frame 就够漫游了。要求 A 已对齐等于把一个米制
    前置门强加给唯一不声称米的路。

    如实说明的限制 (与米制路【同样成立】, 少一个米制声称不会让几何变准):
    - **村庄尺度 BA drift 完全未测, 这是最可能推翻本方案的一条**: 只在 45m 尺度确认了
      "两次重建之差就是一个 Sim3"。整村是几百米上千张的长走廊采集, 两批【各自】带自己
      的累积 drift -> 差异可能不再是一个全局 Sim3, 而是 Sim3 + 低频 warp。而【正是
      drift 才迫使你分批】。若这条不成立, 缝合带外会看到错位, 需要分段对齐。
    - **外推没有任何门守得住**: 留出验证只在共享影像的凸包【内】验证, 而被缝起来的
      几何全在重叠带【外】。实测近共线构型下留出 0.0323 而 100m 外真实误差是它的
      两个数量级倍。对漫游而言这意味着: 远离缝合带处【可能看得见错位】, 而现有的门
      看不见它。preview 只保证"缝合带附近对得上", 不保证全局。
    - **一致 != 正确**: 两次重建可能以同样方式误匹配重复纹理 (夯土墙/瓦屋面) 而
      【同时错】, 缝得严丝合缝却都不是真的。
    """
    if not np.isfinite(max_relative_rms) or max_relative_rms <= 0:
        raise AlignmentError(
            f"max_relative_rms 必须有限且为正, 得到 {max_relative_rms}"
        )
    if holdout_k < 1:
        raise AlignmentError(f"holdout_k 必须 >=1, 得到 {holdout_k}")
    if reg_b.pose_frame.frame_id == reg_a.pose_frame.frame_id:
        raise AlignmentError(
            f"两批的 pose_frame 同为 {reg_b.pose_frame.frame_id!r}: 两次独立重建各有"
            "自己的任意 frame, 同名意味着调用方把它们搞混了; fail-closed"
        )

    src, dst, labels = _shared_pose_centres(reg_a, reg_b, images)
    if len(labels) < _MIN_CONTROL_POINTS:
        raise AlignmentError(
            f"两批只共享 {len(labels)} 张影像 (<{_MIN_CONTROL_POINTS}): 一个 Sim3 有 7 个"
            f"自由度, {_MIN_CONTROL_POINTS - 1} 点去心后秩恒 <=2 (恒共面), 共面守卫必然"
            "拒绝。缝合带必须【非共面】且足够密"
        )

    singular = _source_span(src)
    span_floor = max(min_span_ratio * float(singular[0]), _ABS_SPAN_FLOOR_SOURCE_UNITS)
    if singular[0] <= 0 or float(singular[2]) < span_floor:
        raise AlignmentError(
            "degenerate control-point span (collinear/coplanar): "
            f"singular values {singular.tolist()} below floor {span_floor:g}"
        )
    n_distinct = len(np.unique(dst, axis=0))
    if n_distinct < _MIN_CONTROL_POINTS:
        raise AlignmentError(
            f"{len(labels)} 张共享影像只落在 {n_distinct} 个【互不相同】的中心上 "
            f"(<{_MIN_CONTROL_POINTS}): 重复位置不增加约束; fail-closed"
        )

    # 靶标跨度: 归一化的分母。它与 rms 同在 A 的 gauge 里, 故商无量纲。
    extent = float(np.linalg.norm(dst.max(axis=0) - dst.min(axis=0)))
    if not np.isfinite(extent) or extent <= 0:
        raise AlignmentError(
            "共享影像在参考批里的跨度为零: 无法把残差无量纲化, 也就无从判定; fail-closed"
        )

    scale, rotation, translation = umeyama_sim3(src, dst)
    if not np.isfinite(scale) or scale <= 0:
        raise AlignmentError(f"non-positive or non-finite scale: {scale}")
    _, rms, max_residual = _residuals(src, dst, scale, rotation, translation)

    holdout_relative_rms: float | None = None
    holdout_folds: int | None = None
    if len(labels) - holdout_k >= _MIN_CONTROL_POINTS:
        try:
            holdout, holdout_folds = _holdout_residuals(
                src, dst, k=holdout_k, min_span_ratio=min_span_ratio
            )
        except AlignmentError:
            # 折退化 -> 该门【未运行】, 记 None。preview 不拿它当门: 拿它当门就得给
            # 它编一个阈值, 而留出残差与真实误差的关系【实测就不是保守的】(比值
            # 0.963~1.717, 见 docs/verification/2026-07-17-derived-mode-control-point-floor.md)。
            holdout, holdout_folds = None, None
        if holdout is not None:
            holdout_relative_rms = float(np.sqrt((holdout ** 2).mean())) / extent

    relative_rms = rms / extent
    relative_max = max_residual / extent
    passed = relative_rms <= max_relative_rms
    evidence = PreviewMergeEvidence(
        method="umeyama-sim3",
        n_control_points=len(labels),
        scale=scale,
        rms_residual_source_units=rms,
        max_residual_source_units=max_residual,
        target_extent_source_units=extent,
        relative_rms=relative_rms,
        relative_max=relative_max,
        max_relative_rms_threshold=max_relative_rms,
        source_singular_values=tuple(float(v) for v in singular.tolist()),
        min_span_ratio=min_span_ratio,
        control_point_labels=labels,
        passed=passed,
        holdout_relative_rms=holdout_relative_rms,
        holdout_folds=holdout_folds,
        reference_pose_frame_id=reg_a.pose_frame.frame_id,
    )
    if not passed:
        raise AlignmentError(
            f"preview 合并的 relative_rms {relative_rms:.4g} 超过声明的预算 "
            f"{max_relative_rms:.4g} (无量纲 = rms/跨度): 两批的共享相机中心不由一个"
            "Sim3 相关到这个精度。可能成因: 重叠带太小/近退化、两批之一有 BA drift、"
            "或重复纹理让某一批误匹配。拒绝缝合"
        )

    sim3 = Sim3(
        scale=scale,
        rotation_matrix_xyz=tuple(tuple(row) for row in rotation.tolist()),
        t_xyz=tuple(translation.tolist()),
    )
    evidence_str = evidence.to_evidence()
    # 目标 frame 【原样继承 A 的 pose_frame 契约】: 任意单位、非 geo。刻意不走
    # align_registration —— 它会为任何 world_frame_id 硬编码 enu-z-up/metric/aligned,
    # 对一个纯任意 frame 那就是伪造米制声称。
    target_frame = reg_a.pose_frame.model_copy(update={
        "evidence": (*reg_a.pose_frame.evidence, evidence_str),
    })
    transform = FrameTransform(
        source_frame=reg_b.pose_frame.frame_id,
        target_frame=reg_a.pose_frame.frame_id,
        sim3=sim3,
        method=TransformMethod.CONTROL_POINTS,
        evidence=(evidence_str,),
    )
    return reg_b.model_copy(
        update={
            "world_frame": target_frame,
            "pose_to_world": transform,
            # 【仍是 UNALIGNED】: B 落进了 A 的任意 frame, 那不是"对齐到米制世界"。
            "alignment_status": AlignmentStatus.UNALIGNED,
        }
    )


def load_control_points_json(path: str | Path) -> list[ControlPoint]:
    """Load a JSON array of control-point specs into validated ``ControlPoint``s."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise AlignmentError("control-points JSON must be a list of objects")
    return [ControlPoint.model_validate(item) for item in raw]


def load_control_points_from_ingest_gps(
    manifest_path: str | Path,
    reg: RegistrationResult,
) -> list[ControlPoint]:
    """Turnkey GPS 对齐: 从 ingest manifest 的逐图 EXIF GPS 构造对齐控制点。

    只有【既注册 (在 ``reg.poses``) 又有 GPS】的图成为控制点 (照片带 GPS; 视频帧无
    EXIF GPS 故天然排除)。图名以 manifest 的 ``output_path`` 匹配 registration 的
    ``pose.image``; 不匹配者静默排除 (无对应即无证据)。控制点 <3 时 align_registration
    的门会 fail-closed 并给出清晰错误。``GpsObservation`` 无高度时 alt 记 0。
    """
    from pipeline.ingest_manifest import IngestManifest

    manifest = IngestManifest.model_validate_json(
        Path(manifest_path).read_text(encoding="utf-8"))
    anchors: dict[str, GeoAnchor] = {}
    for src in manifest.sources:
        if src.gps is None:
            continue
        anchor = GeoAnchor(lat=src.gps.lat, lon=src.gps.lon,
                           alt=src.gps.altitude_m if src.gps.altitude_m is not None else 0.0)
        for out in src.outputs:
            anchors[str(out.output_path)] = anchor
    control_points = control_points_from_geo_anchors(reg, anchors)
    if not control_points:
        raise AlignmentError(
            "--from-gps 未找到【既注册又带 EXIF GPS】的图: 确认照片含 GPS 且已被配准, "
            "或改用 --control-points 手工提供")
    return control_points


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fit an SfM->ENU Sim3 and write an aligned registration.json"
    )
    parser.add_argument("--registration", required=True,
                        help="path to a RegistrationResult JSON (sfm-local)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--control-points",
                        help="path to a control-point spec JSON list")
    source.add_argument("--from-gps", metavar="INGEST_MANIFEST",
                        help="derive control points from per-image EXIF GPS in an "
                             "ingest manifest (turnkey for GPS-tagged captures); "
                             "pairs each registered image with its GPS anchor")
    parser.add_argument("--max-rms", type=float, default=2.0,
                        help="RMS residual gate in metres (default 2.0)")
    parser.add_argument("--min-span-ratio", type=float, default=1e-3,
                        help="relative degeneracy floor for the source span")
    parser.add_argument("--out", required=True,
                        help="output path for the aligned registration.json")
    parser.add_argument("--geo-origin", default=None, metavar="LAT,LON,ALT",
                        help="ENU tangent origin lat,lon,alt; supplies/overrides "
                             "registration.geo_origin (required if neither has one)")
    args = parser.parse_args(argv)

    reg = RegistrationResult.model_validate_json(
        Path(args.registration).read_text(encoding="utf-8")
    )
    geo_origin = None
    if args.geo_origin:
        try:
            lat, lon, alt = (float(v) for v in args.geo_origin.split(","))
        except ValueError as exc:
            raise AlignmentError("--geo-origin must be LAT,LON,ALT") from exc
        geo_origin = GeoAnchor(lat=lat, lon=lon, alt=alt)
    if args.from_gps:
        control_points = load_control_points_from_ingest_gps(args.from_gps, reg)
    else:
        control_points = load_control_points_json(args.control_points)
    try:
        aligned = align_registration(
            reg,
            control_points,
            geo_origin=geo_origin,
            max_rms_m=args.max_rms,
            min_span_ratio=args.min_span_ratio,
        )
    except AlignmentError as exc:
        # 自解释失败: 消费级 GPS 撞 RMS 门是最常见的困惑点, 别只丢一句 exceeds max_rms。
        if args.from_gps and "max_rms" in str(exc):
            raise AlignmentError(
                f"{exc}\n"
                f"提示: --from-gps 用的是消费级 EXIF GPS (精度约 3~10m)。GPS 噪声无法被相似"
                f"变换解释, 残差 ≈ 噪声量级, 故常超默认 --max-rms 2.0 —— 这是【正确】的 "
                f"fail-closed (拒绝为噪声数据盖米制章)。出路: (1) 放宽 --max-rms 5~10, 但"
                f"对齐精度不会好于 GPS 本身, 只在米级可信, 别做厘米级测量; (2) 要高精度改用"
                f"实测控制点 --control-points (enu_xyz, 全站仪/RTK) 走 sub-metre 路; "
                f"(3) RTK 无人机 (~2~5cm) 则 GPS 路径本就够好。实际残差见证据串 "
                f"sim3.alignment.v1 的 rms_residual_m。"
            ) from exc
        raise
    # LF: registration.json is a trust root; keep it byte-reproducible across OSes.
    Path(args.out).write_text(aligned.model_dump_json(indent=2) + "\n",
                              encoding="utf-8", newline="\n")
    print(f"[OK] aligned registration written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
