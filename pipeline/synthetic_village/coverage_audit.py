"""覆盖审计内核: 从真实实例掩码【像素】上重算逐组件逐相机的观察证据。

设计原则 (与 chunks.json 的 lod_fractions / core_bounds.axis_percentile 同一套):
**语义写出来, 消费者不用猜也不用信我们。**

具体到本模块:

* 判据建立在 16 位实例掩码的【像素】上, 不是 journal 声明的 `instance_ids`。
  `instance_ids` 是忠实的但粒度不够 (它只说『出现过』), 这里把它降级为交叉校验。
* 报告不产生任何『合格/不合格』的既成事实, 而是同时给出三样东西:
  (a) 逐组件逐相机的【实测像素数与画面占比】原始证据;
  (b) 【显式声明的阈值】(min_pixels / 比较方向 / min_cameras / 画面总像素);
  (c) 该阈值下的判定。
  任何人换个阈值能拿 (a) 自己重算, 不必采信 (c)。
* 阈值【不由本模块发明】: 它是必填参数, 没有默认值。同一份数据在不同阈值下,
  『满足 >=3 相机观察』的组件数实测在 122 和 4 之间任选 —— 这个数不稳是物理事实,
  不是可以靠挑一个默认值掩盖的问题。
* 审计只降不升 (`trust_effect = audit-only-no-trust-elevation`):
  相机再多、覆盖再好, 都不提升 geometry trust。产物是合成的, 保真度是
  simplified-pbr-not-render-parity, 审计改变不了这两件事。

【本模块【不】声称能算的东西 —— 见 orientation_coverage】
`ObjectRegistryEntry.facade_orientation_deg` 字段已存在但尚未从
`SceneObject.transform.yaw_deg` 填充, 因此『哪个立面是正面』无从谈起,
『每个组件至少有正面和反向覆盖』(req 3 的字面要求) 一律 fail-closed 标 `unknown`。
**这是缺一个 wiring 步骤 (字段未填充), 不是这份证据的能力上限** —— 两者必须分清。

报告确实会给出两样【相关但不等价】的连续量, 各自带着显式语义标签, 目的就是让
消费者【不可能】把它们误读成正反面覆盖:

* `qualifying_camera_azimuths_deg` / `max_gap_deg`
  (`camera-azimuth-around-component-center-not-facade-coverage`):
  相机机位分散在组件周围的哪些方向 —— 完全不涉及哪个立面被看到。
* `observed_normal_angular_spread_deg`
  (`observed-surface-normal-angular-spread-not-facade-identity`):
  逐相机实测的【表面法线】方向的最大夹角 —— 它回答的是
  『**看没看到不同的面**』, 而【不是】『看到了正面和背面』。
  哪个面是"正面"我们不知道, 这条判据也不假装知道。

**方位角分散 ≠ 正反面覆盖; 法线跨度 ≠ 正反面覆盖。本模块绝不把任何一个包装成后者。**
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import numpy as np
import OpenEXR
from PIL import Image, UnidentifiedImageError
from pydantic import Field, ValidationError, model_validator

from pipeline.synthetic_village.canary import (
    RENDER_CAMERA_IDS,
    CanaryBuildError,
    FrozenModel,
    Sha256,
    _canonical_json_bytes,
    _flush_directory,
    _flush_file,
    _load_camera_metadata,
    canonical_build_report_bytes,
    load_build_report,
    load_render_journal,
)

COVERAGE_AUDIT_SCHEMA = "nantai.synthetic-village.coverage-audit.v1"
FRAME_WIDTH_PX = 1024
FRAME_HEIGHT_PX = 576
FRAME_PIXEL_COUNT = FRAME_WIDTH_PX * FRAME_HEIGHT_PX
GLB_NAME = "village-canary.glb"
BUILD_REPORT_NAME = "build-report.json"

#: 掩码里的 0 = 【空实例】: 不属于任何注册组件的像素 (天空 + terrain 等 auxiliary)。
#: terrain 是 auxiliary (semantic_id=1), 按 ObjectRegistryEntry.semantic_id >= 3 的契约
#: 根本不在 126 个 instance 里, 所以它在实例掩码里也落成 0。
#: 实测 journal 的 statistics.instance_ids 在 24/24 帧里【都声明了 0】——
#: 把它当成组件会让每一档判定都虚增 1 (122/100/44/4 -> 123/101/45/5)。
NULL_INSTANCE_ID = 0

#: `object_registry` 只有 instance_id/object_id/semantic_id/material_id/variant_id,
#: 没有任何 transform 或朝向 —— 所以【无法给任何立面起名叫"正面"】。
#:
#: 归因必须精确, 这里曾经写错过: 旧措辞是"front/back facade coverage is undefined
#: on this evidence", 那是【把"没做"说成"做不到"】—— 本项目最忌讳的措辞之一。
#: 真相是: 缺的是 registry 里的朝向 (一个【可以补上】的输入), 而这份证据本身
#: 并非无话可说 —— 被 journal 锚定 sha256 的 normal 层能实测"看没看到不同的面",
#: 那条连续量就是 observed_normal_angular_spread_deg, 已经交付。
ORIENTATION_UNKNOWN_REASON = (
    "ObjectRegistryEntry.facade_orientation_deg exists but is not yet populated "
    "from SceneObject.transform.yaw_deg during registry construction, so every entry "
    "still carries None and no facade can be named 'front' or 'back'; this is a "
    "missing wiring step, not a limit of this evidence. The journal-anchored normal "
    "layer does measure whether distinct surfaces were observed, and that continuous "
    "quantity is published as observed_normal_angular_spread_deg -- it does not "
    "identify which surface is the front"
)

#: 方位角字段的语义标签。存在的唯一目的就是让消费者【不可能】把它误读成正反面覆盖。
AZIMUTH_SEMANTICS = "camera-azimuth-around-component-center-not-facade-coverage"

#: 法线跨度字段的语义标签。同上, 且要顶住一个更强的诱惑: 跨度 174 度看起来
#: 很像"看到了正反两面", 但我们【不知道】哪个面是正面, 只知道两个观察方向上
#: 的表面朝向差了 174 度。
NORMAL_SPREAD_SEMANTICS = "observed-surface-normal-angular-spread-not-facade-identity"

#: 法线的来源与编码。落进报告, 让消费者不必读源码就知道这些向量是什么。
NORMAL_SOURCE = "renders/normal/<camera_id>.exr:X,Y,Z-world-space-unit-vector"

#: 跨度需要【两个方向】才有定义。合格相机不足 2 台 -> unknown, 【绝不是 0】:
#: 0 读起来像"实测跨度为零 / 只看到同一个面", 而真相是"没测过"。
NORMAL_SPREAD_UNKNOWN_REASON = (
    "an angular spread needs at least two qualifying cameras to be defined; "
    "this component has fewer at the declared threshold -- not measured, not zero"
)

#: 单位长度容差。canary 契约已声明 `normal_finite_unit_world_space=True`
#: (RenderValidation), 所以掩码内出现非单位法线意味着契约被破坏 -> fail-closed。
#:
#: 这【不是判据阈值】, 它不决定任何覆盖结论, 只判断字节是否还满足自己声明的契约。
#: 实测 24/24 帧、6,496,887 个前景像素: 偏离 1 的最大值是 4.5e-8, 所以 1e-3
#: 有五个数量级的余量。显式声明它, 是因为任何与 1 的比较都必须有容差, 而藏起来
#: 的容差就是编的阈值。
NORMAL_UNIT_LENGTH_TOLERANCE = 1e-3


class CoverageAuditError(RuntimeError):
    """覆盖审计的稳定公开失败 —— 证据不足或不自洽时一律硬失败, 绝不出报告。"""


# --------------------------------------------------------------------------
# 阈值: 必填、显式、自带派生占比
# --------------------------------------------------------------------------


class CoverageThreshold(FrozenModel):
    """一个【完全自描述】的判据。

    报告里带着它, 消费者读报告就知道 122/100/44/4 里的这个数是怎么来的,
    不必去读本模块的源码, 也不必相信我们。
    """

    min_pixels: int = Field(ge=1, le=FRAME_PIXEL_COUNT)
    min_cameras: int = Field(ge=1, le=len(RENDER_CAMERA_IDS))
    #: 比较方向【必须】写明。实测 instance 83 在 camera-outer-001 恰好 590px,
    #: >=590 与 >590 的全局结论差 1 个组件 (44 vs 43) —— 这个 ±1 只能靠写明来消除。
    comparison: Literal["pixels-greater-or-equal"]
    frame_pixel_count: Literal[589824] = FRAME_PIXEL_COUNT
    min_frame_fraction: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _derive_frame_fraction(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("min_pixels"), int):
            derived = dict(data)
            derived["min_frame_fraction"] = data["min_pixels"] / FRAME_PIXEL_COUNT
            return derived
        return data


#: 比较方向的【唯一】实现表。报告里的 `comparison` 字符串必须【真的】决定
#: 比较行为, 否则它就只是装饰: 报告会自称一套、实算另一套, 而没有任何测试
#: 能发现。这张表让"报告怎么写"与"代码怎么算"是同一个事实。
_COMPARISONS: dict[str, object] = {
    "pixels-greater-or-equal": lambda pixels, min_pixels: pixels >= min_pixels,
}


def _meets(pixels: int, threshold: CoverageThreshold) -> bool:
    """单个观察是否达标 —— 比较方向【由报告声明的 comparison 决定】。"""

    try:
        compare = _COMPARISONS[threshold.comparison]
    except KeyError as exc:  # pragma: no cover - Literal 已在契约层挡住
        raise CoverageAuditError(
            f"threshold declares a comparison with no implementation: {threshold.comparison}",
        ) from exc
    return bool(compare(pixels, threshold.min_pixels))


def count_qualifying_cameras(
    observations: Mapping[str, int],
    threshold: CoverageThreshold,
) -> int:
    """在【像素】上数合格相机。

    这是整个内核的承重点: 判据是像素数与 min_pixels 的比较, 不是『出现在
    instance_ids 里』。比较方向取自 threshold.comparison —— 报告里写的那个值。
    """

    return sum(1 for pixels in observations.values() if _meets(pixels, threshold))


# --------------------------------------------------------------------------
# 报告模型
# --------------------------------------------------------------------------


class EvidenceDigest(FrozenModel):
    """审计所读【某个字节产物】的身份 —— 消费者据此验证『这份报告描述的正是这批帧』。

    掩码、camera metadata 与 normal 层都用它: 三者都被 journal 逐帧锚定了 sha256,
    因此都必须先验字节再信内容, 也都必须把摘要落进报告供事后复算。

    这条规则对【被 journal 锚定的每一个审计输入】一视同仁, build-report 也不例外
    (见 CoverageAuditReport.build_report_sha256) —— 半条链比没有链更危险, 因为它
    看起来像有链。
    """

    camera_id: str = Field(pattern=r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")
    path: str
    sha256: Sha256


class CameraCenter(FrozenModel):
    """相机世界中心的平面投影 (blender 右手 z-up 的 x/y)。

    落进报告的理由: 它是方位角的两个输入之一。不落, 消费者就【只能回来重跑
    我们的内核】才能换阈值重算方位角 —— 那正是本模块声称要消灭的依赖。
    """

    camera_id: str = Field(pattern=r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")
    #: 来源: 逐帧 camera-metadata 的 measured_c2w_blender 平移列, 字节经 journal 摘要校验。
    center_source: Literal["renders/cameras/<camera_id>.json:measured_c2w_blender"] = (
        "renders/cameras/<camera_id>.json:measured_c2w_blender"
    )
    center_xy_m: tuple[float, float]


class CameraObservation(FrozenModel):
    """一个组件在一个相机里的【原始证据】。判定是派生的, 证据是第一性的。"""

    camera_id: str = Field(pattern=r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")
    pixels: int = Field(ge=1, le=FRAME_PIXEL_COUNT)
    frame_fraction: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    meets_threshold: bool
    #: 该组件在该帧【全部掩码像素】的世界法线均值, 归一化后舍入到 9 位。
    #: 它挂在【每一个】observation 上 (包括不达标的那些) 而不是只挂合格的,
    #: 理由与 pixels 完全相同: 它是【阈值无关的原始证据】, 消费者据此能在任意
    #: 阈值下自行重算 observed_normal_angular_spread_deg, 不必回来重跑内核。
    #: None = 均值恰为零向量, 方向无定义 (绝不猜一个)。
    mean_unit_normal_xyz: tuple[float, float, float] | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_frame_fraction(cls, data: object) -> object:
        """占比【只能】从 pixels 派生 —— 与 CoverageThreshold.min_frame_fraction 同一套。

        不给调用方留下让 frame_fraction 与 pixels 分叉的机会。

        `mean_unit_normal_xyz` 为什么要在这里从 list 转成 tuple: 一个 mode="before"
        的 validator 一旦返回 dict, 后面的字段校验就【从 JSON 模式掉回 python 模式】,
        而 FrozenModel 是 strict=True —— python 模式下 JSON 数组不再被接受成 tuple。
        于是 `CoverageAuditReport.model_validate_json(<自己刚 dump 的字节>)` 会炸在
        这个字段上, 报告【无法 round-trip】, 消费者也就无法把报告读回模型来复核。
        (ComponentAzimuth 的 tuple 没这个问题, 因为那个模型没有 before validator。)
        """

        if not isinstance(data, dict):
            return data
        derived = dict(data)
        if isinstance(data.get("pixels"), int):
            derived["frame_fraction"] = data["pixels"] / FRAME_PIXEL_COUNT
        if isinstance(data.get("mean_unit_normal_xyz"), list):
            derived["mean_unit_normal_xyz"] = tuple(data["mean_unit_normal_xyz"])
        return derived


class ComponentAzimuth(FrozenModel):
    """相机绕组件中心的方位角分布。

    **这【不是】正反面覆盖。** 它只说明『观察机位分散在组件周围的哪些方向』,
    完全不涉及组件的哪个立面被看到 —— 后者需要组件朝向, 而证据链里没有。
    `semantics` 字段随报告一起落盘, 就是为了让这句话跟着数据走。

    **这些角度是【阈值耦合】的**: 它们只统计当前 min_pixels 下的合格相机, 所以
    换阈值它们就会变。为了让消费者不必采信这两个数, 报告同时落下重算所需的
    全部输入 —— `component_center_xy_m` (这里) 与 `CoverageAuditReport.camera_centers`。
    """

    semantics: Literal[
        "camera-azimuth-around-component-center-not-facade-coverage"
    ] = AZIMUTH_SEMANTICS
    center_source: Literal["village-canary.glb:extras.nv_source_transform"]
    #: 组件平面中心。落它的唯一目的: 让方位角可以被第三方在【任意阈值】下自行重算。
    component_center_xy_m: tuple[float, float]
    #: 【随 min_pixels 变化】—— 只含当前阈值下的合格相机。
    qualifying_camera_azimuths_deg: tuple[float, ...]
    #: 合格相机方位角的最大空隙。360.0 表示只有一个合格相机; None 表示一个都没有。
    #: 同样【随 min_pixels 变化】。
    max_gap_deg: float | None = Field(default=None, ge=0.0, le=360.0)


class ComponentNormalSpread(FrozenModel):
    """逐相机实测【表面法线】方向的角度跨度 —— req 3 唯一会随布点变好变坏的连续量。

    **这【不是】正反面覆盖。** 它回答的是"这些机位看到的表面朝向差了多少度",
    即『**看没看到不同的面**』; 它【不说】哪个面是正面 —— 我们不知道, 见
    ORIENTATION_UNKNOWN_REASON。字段名刻意【不叫】has_front_and_back_coverage,
    因为那个名字暗示我们知道什么是"正面"。

    为什么这条判据值得存在: `instance_ids` 那条无论布点好坏都报 ~98%
    (实测 123/126), 对"背面覆盖改善了没有"完全不敏感; 法线跨度敏感。
    且它【零渲染成本】—— 现有 24 帧的 normal 层就能算。

    **不设阈值。** 多少度算"看到了足够不同的面"不是我们能定的, 所以这里
    【只报连续量, 不下判定】。报告里没有任何 pass/fail 派生自它。

    **这个数是【阈值耦合】的**: 只统计当前 min_pixels 下的合格相机, 换阈值就变。
    重算它所需的全部输入都在 CameraObservation.mean_unit_normal_xyz 里。
    """

    semantics: Literal["observed-surface-normal-angular-spread-not-facade-identity"] = (
        NORMAL_SPREAD_SEMANTICS
    )
    normal_source: Literal["renders/normal/<camera_id>.exr:X,Y,Z-world-space-unit-vector"] = (
        NORMAL_SOURCE
    )
    #: 进入两两夹角计算的方向数 = 【既达标、又给出了法线方向】的相机数。
    qualifying_camera_normal_count: int = Field(ge=0)
    #: 合格相机的逐帧平均法线【两两夹角的最大值】。None = unknown (不足 2 个方向)。
    observed_normal_angular_spread_deg: float | None = Field(default=None, ge=0.0, le=180.0)
    #: 跨度为 None 时【必须】说明为什么 —— unknown 不能是无声的。
    unknown_reason: str | None = None

    @model_validator(mode="after")
    def _unknown_must_be_explained(self) -> ComponentNormalSpread:
        has_spread = self.observed_normal_angular_spread_deg is not None
        if has_spread == (self.unknown_reason is not None):
            raise ValueError(
                "normal spread must be either a measured angle or an explained unknown, "
                "never both and never neither",
            )
        return self


class ComponentCoverage(FrozenModel):
    object_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    instance_id: int = Field(ge=1, le=65535)
    semantic_class: str
    #: 所有【看见过】该组件的相机 (pixels >= 1) 的原始证据, 无论是否达标。
    observations: tuple[CameraObservation, ...]
    observed_camera_count: int = Field(ge=0)
    qualifying_camera_count: int = Field(ge=0)
    meets_threshold: bool
    azimuth: ComponentAzimuth | None = None
    #: 【不是】正反面覆盖 —— 是"看没看到不同的面"。见 ComponentNormalSpread。
    normal_spread: ComponentNormalSpread
    #: 恒为 unknown —— 见 ORIENTATION_UNKNOWN_REASON。
    orientation_coverage: Literal["unknown"] = "unknown"
    orientation_unknown_reason: str = Field(min_length=1)


class InstanceIdsCrosscheck(FrozenModel):
    """journal 声明的 instance_ids vs 掩码实算集合 (逐帧逐 ID 配对比较)。

    journal 是忠实的但粒度不够 (只有『出现过』, 没有像素数)。这里不用它下判定,
    只用它交叉校验掩码没被换掉。

    注意: 两侧都【剔除了 NULL_INSTANCE_ID (0)】。实测 24/24 帧的 statistics.instance_ids
    都声明了 0, 但 0 是『不属于任何注册组件的像素』(天空 + terrain), 不是第 127 个组件。
    """

    agrees: bool
    declared_only: tuple[str, ...]
    observed_only: tuple[str, ...]


class CoverageSummary(FrozenModel):
    component_count: int = Field(ge=0)
    components_meeting_threshold: int = Field(ge=0)
    components_never_observed: int = Field(ge=0)
    frames_audited: int = Field(ge=0)


class CoverageAuditReport(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.coverage-audit.v1"] = COVERAGE_AUDIT_SCHEMA
    evidence_sha256: Sha256
    #: 审计不产生任何信任提升 —— 相机再多也不改变这三行。
    synthetic: Literal[True] = True
    verification_level: Literal["L2"] = "L2"
    fidelity: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
    trust_effect: Literal["audit-only-no-trust-elevation"] = "audit-only-no-trust-elevation"
    render_id: Sha256
    build_id: Sha256
    journal_sha256: Sha256
    object_registry_sha256: Sha256
    #: build-report.json 的【字节】摘要, 已按 journal.build_report_sha256 验过。
    #: 落它的理由与 mask_digests 逐字相同: build-report 是 glb 的唯一锚点, 而
    #: 组件中心又只来自 glb —— 不落, 消费者拿到报告就【无法复核组件中心来自
    #: 哪份字节】。注意 build_id 顶替不了它: build_id 是 build request 【输入】
    #: 的摘要, 不随报告内容变化。
    build_report_sha256: Sha256
    #: village-canary.glb 的字节摘要 —— 全部 component_center_xy_m 的唯一来源。
    #: None = glb 读不到, 此时【所有】azimuth 都是 None (见下面的结构性绑定)。
    glb_sha256: Sha256 | None = None
    threshold: CoverageThreshold
    mask_digests: tuple[EvidenceDigest, ...]
    #: 全部 azimuth 数字的唯一输入的身份。没有它, 消费者【无法事后发现】
    #: cameras/*.json 被换过 —— 而掩码那条链一直是可复算的。
    camera_metadata_digests: tuple[EvidenceDigest, ...]
    #: 全部 mean_unit_normal_xyz (进而全部跨度) 的唯一输入的身份。同一个论证。
    normal_digests: tuple[EvidenceDigest, ...]
    #: 法线模长与 1 的比较容差。【不是判据阈值】—— 它不决定任何覆盖结论,
    #: 只判断字节是否还满足 journal 自己声明的 normal_finite_unit_world_space。
    normal_unit_length_tolerance: float = Field(
        default=NORMAL_UNIT_LENGTH_TOLERANCE,
        gt=0.0,
        allow_inf_nan=False,
    )
    #: 方位角的另一个输入。落它是为了让 azimuth 与像素侧一样可被第三方重算。
    camera_centers: tuple[CameraCenter, ...]
    instance_ids_crosscheck: InstanceIdsCrosscheck
    components: tuple[ComponentCoverage, ...]
    summary: CoverageSummary
    #: 如实报耗时。它【不进】evidence_sha256 —— 证据字节必须可复现, 耗时不可能可复现。
    audit_duration_seconds: float = Field(ge=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _summary_and_counts_must_follow_from_the_raw_evidence(self) -> CoverageAuditReport:
        """让【原始证据】与【派生判定】在结构上不可能分叉。

        这个内核的全部卖点是"消费者可脱离我们重算": 报告承诺 summary 是
        observations 在声明阈值下的派生量。以前这是两条【各自独立】的代码路径,
        没有任何东西把它们绑在一起 —— 把逐相机 pixels 换成常数, 报告的 summary
        照旧写 44, 而消费者按报告承诺重算会拿到 122, 两边自相矛盾却无人发现。

        所以这里不是"多加几条断言", 而是: 一份自相矛盾的报告【无法被构造出来】。
        任何一侧被改动, 都必须让另一侧同步说谎才能通过 —— 而另一侧有真实数据
        断言钉着。
        """

        for component in self.components:
            if component.observed_camera_count != len(component.observations):
                raise ValueError(
                    f"component {component.instance_id}: observed_camera_count does not "
                    "match its own observations",
                )
            qualifying = 0
            for observation in component.observations:
                meets = _meets(observation.pixels, self.threshold)
                if observation.meets_threshold != meets:
                    raise ValueError(
                        f"component {component.instance_id}: observation "
                        f"{observation.camera_id} contradicts the declared threshold",
                    )
                if observation.frame_fraction != observation.pixels / FRAME_PIXEL_COUNT:
                    raise ValueError(
                        f"component {component.instance_id}: observation "
                        f"{observation.camera_id} frame_fraction is not derived from pixels",
                    )
                qualifying += int(meets)
            if component.qualifying_camera_count != qualifying:
                raise ValueError(
                    f"component {component.instance_id}: qualifying_camera_count does not "
                    "follow from its own observations at the declared threshold",
                )
            if component.meets_threshold != (qualifying >= self.threshold.min_cameras):
                raise ValueError(
                    f"component {component.instance_id}: meets_threshold does not follow "
                    "from its own qualifying camera count",
                )
            # 法线跨度与它自称派生自的原始证据绑死 —— 与 summary <- observations
            # 同一套结构性修法: 一份"跨度与自己的 mean_unit_normal_xyz 分叉"的
            # 报告【构造不出来】, 而不是靠断言逐个字段去追。
            directions = tuple(
                observation.mean_unit_normal_xyz
                for observation in component.observations
                if _meets(observation.pixels, self.threshold)
                and observation.mean_unit_normal_xyz is not None
            )
            if component.normal_spread.qualifying_camera_normal_count != len(directions):
                raise ValueError(
                    f"component {component.instance_id}: qualifying_camera_normal_count does "
                    "not follow from its own observations at the declared threshold",
                )
            if component.normal_spread.observed_normal_angular_spread_deg != (
                _max_pairwise_normal_angle_deg(directions)
            ):
                raise ValueError(
                    f"component {component.instance_id}: observed_normal_angular_spread_deg "
                    "does not follow from its own per-camera normals at the declared threshold",
                )

        if self.summary.component_count != len(self.components):
            raise ValueError("summary component_count does not match the reported components")
        if self.summary.components_meeting_threshold != sum(
            1 for component in self.components if component.meets_threshold
        ):
            raise ValueError(
                "summary components_meeting_threshold does not follow from the components",
            )
        if self.summary.components_never_observed != sum(
            1 for component in self.components if not component.observations
        ):
            raise ValueError(
                "summary components_never_observed does not follow from the components",
            )
        if self.summary.frames_audited != len(self.mask_digests):
            raise ValueError("summary frames_audited does not match the audited mask digests")
        # 组件中心【只能】来自 glb, 所以一份"有方位角却说不出 glb 是哪份字节"的
        # 报告是自相矛盾的 —— 让它构造不出来, 而不是靠约定。
        if self.glb_sha256 is None and any(item.azimuth is not None for item in self.components):
            raise ValueError(
                "components carry azimuths derived from component centres, but the report "
                "names no glb bytes they could have come from",
            )
        return self


class CoverageAuditResult(FrozenModel):
    report: CoverageAuditReport
    build_directory: Path


def canonical_coverage_report_bytes(
    report: CoverageAuditReport,
    *,
    exclude_nondeterministic: bool = False,
) -> bytes:
    """规范字节。`exclude_nondeterministic` 同时排除自摘要与耗时。"""

    exclude = {"evidence_sha256", "audit_duration_seconds"} if exclude_nondeterministic else None
    return _canonical_json_bytes(report.model_dump(mode="json", exclude=exclude))


def write_coverage_report(report: CoverageAuditReport, destination: Path) -> Path:
    """LF + 【原子 + 持久化】落盘, 字节等于 canonical_coverage_report_bytes。

    审计报告是 provenance-safety 的证据产物, 所以沿用本仓库信任根惯例
    (canary 的 _write_render_journal 同款): 临时文件 -> fsync -> os.replace -> fsync。

    就地覆写为什么不行 (实际失败, 不是洁癖): 操作者第二次跑 audit-coverage
    覆写同一 --report 路径, 写到一半断电/进程被杀 —— 上一份【有效】报告已被
    截断销毁, 磁盘上只剩坏文件, 无法回退。且 studio 侧有实时消费者
    (f6e992c auto-load coverage audit report), 就地覆写期间读取会拿到截断 JSON。
    原子替换让读者要么看到旧的完整报告, 要么看到新的完整报告, 绝不会看到半份。
    """

    destination = Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_coverage_report_bytes(report)
    temporary = destination.parent / f".{destination.name}-{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _flush_file(destination)
        _flush_directory(destination.parent)
    except OSError as exc:
        raise CoverageAuditError(f"cannot durably write the coverage report: {exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


# --------------------------------------------------------------------------
# 证据读取
# --------------------------------------------------------------------------


def _read_instance_mask(path: Path, *, expected_sha256: str, camera_id: str) -> np.ndarray:
    """读取一帧实例掩码, 并先按 journal 声明的 sha256 挣回信任。"""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CoverageAuditError(f"instance mask unreadable for {camera_id}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise CoverageAuditError(
            f"instance mask for {camera_id} does not match the journal digest",
        )
    try:
        with Image.open(path) as image:
            image.load()
            if image.mode != "I;16":
                raise CoverageAuditError(
                    f"instance mask for {camera_id} is not 16-bit grayscale: {image.mode}",
                )
            array = np.array(image)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise CoverageAuditError(f"instance mask for {camera_id} is undecodable: {exc}") from exc
    if array.dtype != np.uint16 or array.shape != (FRAME_HEIGHT_PX, FRAME_WIDTH_PX):
        raise CoverageAuditError(
            f"instance mask for {camera_id} violates the 1024x576 uint16 contract",
        )
    return array


def _read_normal_map(path: Path, *, expected_sha256: str, camera_id: str) -> np.ndarray:
    """读取一帧世界空间法线 (X,Y,Z float32 EXR), 先按 journal 声明的 sha256 挣回信任。

    与 `_read_instance_mask` 同规格, 理由也同一个: 法线层是【全部跨度数字的
    唯一输入】, 而 journal 已为每帧锚定了它的 sha256 —— 先验字节再信内容。
    """

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CoverageAuditError(f"normal map unreadable for {camera_id}: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise CoverageAuditError(f"normal map for {camera_id} does not match the journal digest")
    try:
        channels = OpenEXR.File(str(path)).channels()
        planes = [np.asarray(channels[name].pixels, dtype=np.float64) for name in ("X", "Y", "Z")]
    except (OSError, RuntimeError, KeyError, ValueError) as exc:
        raise CoverageAuditError(f"normal map for {camera_id} is undecodable: {exc}") from exc
    array = np.stack(planes, axis=-1)
    if array.shape != (FRAME_HEIGHT_PX, FRAME_WIDTH_PX, 3):
        raise CoverageAuditError(
            f"normal map for {camera_id} violates the 1024x576 X,Y,Z contract",
        )
    return array


def _component_mean_normals(
    mask: np.ndarray,
    normals: np.ndarray,
    *,
    camera_id: str,
) -> dict[int, tuple[float, float, float]]:
    """逐组件的【平均表面法线】(单位向量), 只取该组件在实例掩码里的像素。

    『有意义的像素』由【实例掩码】定义, 不由法线模长定义 —— 这点实测很要紧:
    terrain 与天空的法线【也是单位向量】(24/24 帧共 8,000,000+ 个这样的像素),
    模长筛子根本挡不住它们; 挡住它们的是"掩码值 == 该组件的 instance_id"。
    背景法线为 0 只是个附带事实, 不是选择条件。

    掩码内出现非单位法线 -> 契约 (`normal_finite_unit_world_space`) 被破坏 ->
    fail-closed, 绝不静默丢弃。实测 24/24 帧一个都没有。

    平均向量恰好为零向量时方向【无定义】, 该相机不贡献方向 (不是贡献一个猜的)。
    """

    flat_mask = mask.ravel().astype(np.int64)
    lengths = np.linalg.norm(normals, axis=-1)
    foreground = flat_mask != NULL_INSTANCE_ID
    if np.any(np.abs(lengths.ravel()[foreground] - 1.0) > NORMAL_UNIT_LENGTH_TOLERANCE):
        raise CoverageAuditError(
            f"normal map for {camera_id} carries non-unit normals inside the instance mask, "
            "violating the render journal's normal_finite_unit_world_space contract",
        )
    size = int(flat_mask.max()) + 1
    counts = np.bincount(flat_mask, minlength=size)
    sums = np.stack(
        [
            np.bincount(flat_mask, weights=normals[..., axis].ravel(), minlength=size)
            for axis in range(3)
        ],
        axis=-1,
    )
    means: dict[int, tuple[float, float, float]] = {}
    for instance_id in range(1, size):
        count = int(counts[instance_id])
        if count == 0:
            continue
        vector = sums[instance_id] / count
        length = float(np.linalg.norm(vector))
        if length == 0.0:
            continue
        # 【先舍入, 再据此算跨度】: 报告里落的就是这些舍入后的向量, 跨度必须
        # 从它们算出来, 否则消费者按报告字段重算会与我们的数对不上 —— 那正是
        # 本模块声称要消灭的"只能采信我们"。
        means[instance_id] = tuple(round(float(value / length), 9) for value in vector)
    return means


def _max_pairwise_normal_angle_deg(
    vectors: tuple[tuple[float, float, float], ...],
) -> float | None:
    """一组方向向量【两两夹角的最大值】。少于 2 个 -> None (无定义, 不是 0)。"""

    if len(vectors) < 2:
        return None
    widest = 0.0
    for index, first in enumerate(vectors):
        for second in vectors[index + 1 :]:
            dot = sum(left * right for left, right in zip(first, second, strict=True))
            norms = math.sqrt(sum(value * value for value in first)) * math.sqrt(
                sum(value * value for value in second),
            )
            if norms == 0.0:
                continue
            widest = max(widest, math.degrees(math.acos(max(-1.0, min(1.0, dot / norms)))))
    return round(widest, 3)


def _load_component_centers(build_directory: Path, build_report_artifacts: Mapping[str, str]) -> (
    tuple[dict[int, tuple[float, float]], str | None]
):
    """从 village-canary.glb 的 extras.nv_source_transform 取组件平面中心。

    glb 的 sha256 被 build-report.json 的 artifacts 锚定 —— 先验字节再信内容。
    取不到就返回空表 (方位角一律标 None), 绝不猜。

    同时回传【实际读到的 glb 摘要】: 它是全部 component_center_xy_m 的唯一来源,
    必须随报告落盘, 否则消费者拿到报告【无法自行复核组件中心来自哪份字节】。
    """

    path = build_directory / GLB_NAME
    expected = build_report_artifacts.get(GLB_NAME)
    if expected is None or not path.is_file():
        return {}, None
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected:
        raise CoverageAuditError("village-canary.glb does not match the build report digest")
    try:
        if raw[:4] != b"glTF" or len(raw) < 20:
            raise CoverageAuditError("village-canary.glb is not a binary glTF container")
        chunk_length = struct.unpack("<I", raw[12:16])[0]
        document = json.loads(raw[20 : 20 + chunk_length].decode("utf-8"))
    except (struct.error, UnicodeError, json.JSONDecodeError) as exc:
        raise CoverageAuditError(f"village-canary.glb JSON chunk is unreadable: {exc}") from exc

    centers: dict[int, tuple[float, float]] = {}
    for node in document.get("nodes", ()):
        extras = node.get("extras") or {}
        encoded = extras.get("nv_source_transform")
        instance_id = extras.get("nv_instance_id")
        if not encoded or not isinstance(instance_id, int):
            continue
        try:
            transform = json.loads(encoded)
            centers[instance_id] = (float(transform["x_m"]), float(transform["y_m"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise CoverageAuditError(
                f"village-canary.glb has an unreadable nv_source_transform: {exc}",
            ) from exc
    return centers, digest


def _camera_centers(
    render_root: Path,
    declared: Mapping[str, EvidenceDigest],
) -> dict[str, tuple[float, float]]:
    """相机世界中心 (blender 右手 z-up), 取自逐帧 camera-metadata 的 c2w 平移列。

    **先验字节再信内容** —— 与掩码 (:_read_instance_mask) 和 glb
    (:_load_component_centers) 同一个标准, 不是可选项:
    camera metadata 是【全部 azimuth 数字的唯一输入】, 而 journal 已经为每帧
    锚定了它的 sha256。canary 的 _load_camera_metadata 只校验 schema + canonical
    形式, 【从不】比对 journal digest —— 一个 schema 合法、canonical 形式正确、
    但内容与 journal 矛盾的 cameras/*.json 能一路放行, 让 azimuth 全盘失真。

    不需要攻击者: 一个陈旧/半写入/被别的 agent 重跑覆盖的文件就足够。
    """

    centers: dict[str, tuple[float, float]] = {}
    for camera_id in RENDER_CAMERA_IDS:
        record = declared.get(camera_id)
        if record is None:
            raise CoverageAuditError(
                f"verified frame {camera_id} declares no camera metadata artifact",
            )
        path = render_root / record.path
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CoverageAuditError(
                f"camera metadata unreadable for {camera_id}: {exc}",
            ) from exc
        if hashlib.sha256(raw).hexdigest() != record.sha256:
            raise CoverageAuditError(
                f"camera metadata for {camera_id} does not match the journal digest",
            )
        try:
            metadata = _load_camera_metadata(path)
        except CanaryBuildError as exc:
            raise CoverageAuditError(
                f"camera metadata for {camera_id} is not loadable: {exc}",
            ) from exc
        if metadata.camera_id != camera_id:
            raise CoverageAuditError(
                f"camera metadata at {record.path} describes {metadata.camera_id}, not {camera_id}",
            )
        matrix = metadata.measured_c2w_blender
        centers[camera_id] = (float(matrix[0][3]), float(matrix[1][3]))
    return centers


def _max_azimuth_gap(azimuths: tuple[float, ...]) -> float | None:
    """合格相机方位角的最大空隙。一个相机 -> 360; 空 -> None。"""

    if not azimuths:
        return None
    if len(azimuths) == 1:
        return 360.0
    ordered = sorted(azimuths)
    gaps = [second - first for first, second in zip(ordered, ordered[1:], strict=False)]
    gaps.append(360.0 - ordered[-1] + ordered[0])
    return round(max(gaps), 3)


# --------------------------------------------------------------------------
# 内核
# --------------------------------------------------------------------------


def audit_render_coverage(
    *,
    build_directory: Path,
    threshold: CoverageThreshold,
) -> CoverageAuditResult:
    """在真实掩码像素上重算覆盖证据。

    阈值是【必填】的, 本模块不提供默认值 —— 『多少像素才算看见』不是我们能定的。
    """

    started = time.monotonic()
    build_directory = Path(build_directory).absolute()
    render_root = build_directory / "renders"
    try:
        journal = load_render_journal(render_root / "render-journal.json")
        report = load_build_report(build_directory / BUILD_REPORT_NAME)
    except CanaryBuildError as exc:
        raise CoverageAuditError(f"render evidence is not loadable: {exc}") from exc

    if report.build_id != journal.build_id:
        raise CoverageAuditError("build report and render journal describe different builds")

    # build-report.json 的【字节】必须先挣回信任, 再信它的内容 —— 与掩码 / camera
    # metadata / glb 同一个标准。
    #
    # 上面那道 build_id 门【抓不到内容篡改】: build_id 是 build request 【输入】的
    # 摘要 (canary.py `_validate_complete_request`), 不是报告内容的自摘要。改掉
    # report.artifacts 里 glb 的 sha256 并换上一个匹配的 glb, build_id 逐字不变,
    # 那道门照常放行, 而 report.artifacts 正是 glb (全部组件中心的唯一来源) 的
    # 唯一锚点 —— 它自己却没有锚。journal.build_report_sha256 就是它缺的那个锚。
    #
    # 不需要攻击者: 一次 build-report 与 glb 不同步的重建就足以让每个组件的中心
    # 静默平移, 方位角全盘失真, 而报告照出。
    #
    # 这里【不重读磁盘】: load_build_report 已经断言过
    # `raw == canonical_build_report_bytes(report)`, 所以下面这串字节就是它读到的
    # 那串字节。重读反而会引入一个 TOCTOU 窗口, 复算是结构性可靠的。
    build_report_sha256 = hashlib.sha256(canonical_build_report_bytes(report)).hexdigest()
    if build_report_sha256 != journal.build_report_sha256:
        raise CoverageAuditError("build report bytes do not match the render journal digest")

    registry_digest = hashlib.sha256(
        _canonical_json_bytes([entry.model_dump(mode="json") for entry in report.object_registry]),
    ).hexdigest()
    if registry_digest != journal.object_registry_sha256:
        raise CoverageAuditError("object registry digest does not match the render journal")

    semantic_by_id = {entry.semantic_id: entry.semantic_class for entry in report.semantic_registry}

    # 只审计 verified 帧; 未验证的帧没有产物可读, 直接硬失败而不是静默跳过。
    unverified = tuple(frame.camera_id for frame in journal.frames if frame.state != "verified")
    if unverified:
        raise CoverageAuditError(
            f"render journal has unverified frames, coverage is not auditable: {unverified}",
        )

    pixels_by_instance: dict[int, dict[str, int]] = {}
    normals_by_instance: dict[int, dict[str, tuple[float, float, float]]] = {}
    declared_pairs: set[str] = set()
    observed_pairs: set[str] = set()
    mask_digests: list[EvidenceDigest] = []
    normal_digests: list[EvidenceDigest] = []
    camera_metadata_digests: dict[str, EvidenceDigest] = {}

    for frame in journal.frames:
        mask_record = next(
            (item for item in frame.artifacts if item.kind == "instance-mask"),
            None,
        )
        if mask_record is None:
            raise CoverageAuditError(f"verified frame {frame.camera_id} has no instance mask")
        metadata_record = next(
            (item for item in frame.artifacts if item.kind == "camera-metadata"),
            None,
        )
        if metadata_record is None:
            raise CoverageAuditError(f"verified frame {frame.camera_id} has no camera metadata")
        normal_record = next(
            (item for item in frame.artifacts if item.kind == "normal"),
            None,
        )
        if normal_record is None:
            raise CoverageAuditError(f"verified frame {frame.camera_id} has no normal map")
        camera_metadata_digests[frame.camera_id] = EvidenceDigest(
            camera_id=frame.camera_id,
            path=metadata_record.path,
            sha256=metadata_record.sha256,
        )
        array = _read_instance_mask(
            render_root / mask_record.path,
            expected_sha256=mask_record.sha256,
            camera_id=frame.camera_id,
        )
        mask_digests.append(
            EvidenceDigest(
                camera_id=frame.camera_id,
                path=mask_record.path,
                sha256=mask_record.sha256,
            ),
        )
        normal_map = _read_normal_map(
            render_root / normal_record.path,
            expected_sha256=normal_record.sha256,
            camera_id=frame.camera_id,
        )
        normal_digests.append(
            EvidenceDigest(
                camera_id=frame.camera_id,
                path=normal_record.path,
                sha256=normal_record.sha256,
            ),
        )
        for instance_id, direction in _component_mean_normals(
            array,
            normal_map,
            camera_id=frame.camera_id,
        ).items():
            normals_by_instance.setdefault(instance_id, {})[frame.camera_id] = direction
        values, counts = np.unique(array, return_counts=True)
        for value, count in zip(values.tolist(), counts.tolist(), strict=True):
            if value == NULL_INSTANCE_ID:
                continue
            pixels_by_instance.setdefault(value, {})[frame.camera_id] = int(count)
            observed_pairs.add(f"{frame.camera_id}:{value}")
        if frame.statistics is not None:
            for declared in frame.statistics.instance_ids:
                # 与实算侧对称地剔除空实例, 否则交叉校验会被 0 恒定拉红。
                if declared == NULL_INSTANCE_ID:
                    continue
                declared_pairs.add(f"{frame.camera_id}:{declared}")

    crosscheck = InstanceIdsCrosscheck(
        agrees=declared_pairs == observed_pairs,
        declared_only=tuple(sorted(declared_pairs - observed_pairs)),
        observed_only=tuple(sorted(observed_pairs - declared_pairs)),
    )

    build_artifacts = {item.name: item.sha256 for item in report.artifacts}
    centers, glb_sha256 = _load_component_centers(build_directory, build_artifacts)
    camera_centers = _camera_centers(render_root, camera_metadata_digests) if centers else {}

    components: list[ComponentCoverage] = []
    for entry in sorted(report.object_registry, key=lambda item: item.instance_id):
        observed = pixels_by_instance.get(entry.instance_id, {})
        directions = normals_by_instance.get(entry.instance_id, {})
        # 【原始证据】先落地: 收录所有看见过该组件的相机 (pixels >= 1), 无论达标与否。
        # 不达标的那些【必须】留下 —— 否则"换个阈值自行重算"这一能力本身就没了。
        observations = tuple(
            CameraObservation(
                camera_id=camera_id,
                pixels=observed[camera_id],
                meets_threshold=_meets(observed[camera_id], threshold),
                mean_unit_normal_xyz=directions.get(camera_id),
            )
            for camera_id in RENDER_CAMERA_IDS
            if camera_id in observed
        )
        # 判定【从 observations 派生】, 不另开一条读 observed 的独立路径。
        # 这是结构性的: 两条路径存在就一定会分叉, 而分叉正是报告自相矛盾的根因。
        qualifying = count_qualifying_cameras(
            {item.camera_id: item.pixels for item in observations},
            threshold,
        )
        # 跨度【只从 observations 上那些舍入后的向量算】—— 与消费者手里的输入
        # 逐字相同, 所以他们重算必定得到同一个数。
        qualifying_normals = tuple(
            item.mean_unit_normal_xyz
            for item in observations
            if item.meets_threshold and item.mean_unit_normal_xyz is not None
        )
        spread = _max_pairwise_normal_angle_deg(qualifying_normals)
        normal_spread = ComponentNormalSpread(
            qualifying_camera_normal_count=len(qualifying_normals),
            observed_normal_angular_spread_deg=spread,
            unknown_reason=None if spread is not None else NORMAL_SPREAD_UNKNOWN_REASON,
        )
        azimuth = None
        center = centers.get(entry.instance_id)
        if center is not None:
            angles = tuple(
                round(
                    math.degrees(
                        math.atan2(
                            camera_centers[item.camera_id][1] - center[1],
                            camera_centers[item.camera_id][0] - center[0],
                        ),
                    )
                    % 360.0,
                    3,
                )
                for item in observations
                if item.meets_threshold and item.camera_id in camera_centers
            )
            azimuth = ComponentAzimuth(
                center_source="village-canary.glb:extras.nv_source_transform",
                component_center_xy_m=(float(center[0]), float(center[1])),
                qualifying_camera_azimuths_deg=tuple(sorted(angles)),
                max_gap_deg=_max_azimuth_gap(angles),
            )
        components.append(
            ComponentCoverage(
                object_id=entry.object_id,
                instance_id=entry.instance_id,
                semantic_class=semantic_by_id.get(entry.semantic_id, "unknown"),
                observations=observations,
                observed_camera_count=len(observations),
                qualifying_camera_count=qualifying,
                meets_threshold=qualifying >= threshold.min_cameras,
                azimuth=azimuth,
                normal_spread=normal_spread,
                orientation_unknown_reason=ORIENTATION_UNKNOWN_REASON,
            ),
        )

    summary = CoverageSummary(
        component_count=len(components),
        components_meeting_threshold=sum(1 for item in components if item.meets_threshold),
        components_never_observed=sum(1 for item in components if not item.observations),
        frames_audited=len(mask_digests),
    )
    duration = time.monotonic() - started
    try:
        draft = CoverageAuditReport(
            evidence_sha256="0" * 64,
            render_id=journal.render_id,
            build_id=journal.build_id,
            journal_sha256=journal.journal_sha256,
            object_registry_sha256=journal.object_registry_sha256,
            build_report_sha256=build_report_sha256,
            glb_sha256=glb_sha256,
            threshold=threshold,
            mask_digests=tuple(mask_digests),
            camera_metadata_digests=tuple(
                camera_metadata_digests[camera_id]
                for camera_id in RENDER_CAMERA_IDS
                if camera_id in camera_metadata_digests
            ),
            normal_digests=tuple(normal_digests),
            camera_centers=tuple(
                CameraCenter(camera_id=camera_id, center_xy_m=center)
                for camera_id, center in sorted(camera_centers.items())
            ),
            instance_ids_crosscheck=crosscheck,
            components=tuple(components),
            summary=summary,
            audit_duration_seconds=round(max(duration, 0.0), 6),
        )
        sealed = draft.model_copy(
            update={
                "evidence_sha256": hashlib.sha256(
                    canonical_coverage_report_bytes(draft, exclude_nondeterministic=True),
                ).hexdigest(),
            },
        )
    except ValidationError as exc:
        raise CoverageAuditError(f"coverage audit report is not well-formed: {exc}") from exc
    return CoverageAuditResult(report=sealed, build_directory=build_directory)
