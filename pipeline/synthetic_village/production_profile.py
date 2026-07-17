"""生产档 (180 相机) camera profile —— 与 canary 24 帧【完全隔离】。

设计约束 (来自 HANDOFF-OPUS-005):
  * canary 的 24 相机契约是快速门禁, 放大它就等于拆掉它 -> 本模块【只新增】,
    绝不 import-and-mutate camera_plan 的 24 帧约束。
  * 相机中心沿【真实可行走拓扑】布置, 不是按一个圆平均撒点。没有拓扑来源的
    分组 -> fail-closed, 绝不用几何撒点冒充。
  * 相机增多【绝不】提升 geometry trust。

诚实边界 (实测, 见模块级常量 _ELEVATED_PEDESTRIAN_REASON):
  场景里【不存在】抬升人行拓扑 —— "detail-stone-stair-01" 与
  "detail-timber-balcony-01" 在 canary.DETAIL_SLOT_COMPONENTS 里都映射到 None,
  即它们只是贴图槽位, 【没有对应几何】。因此 elevated-pedestrian 这一组
  (48 相机) 无法诚实交付, 本模块对它 fail-closed。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .camera_plan import (
    CameraIntrinsics,
    _intrinsics,
    _look_at_c2w,
    _q3,
    _scene_digest,
)
from .canary import DETAIL_SLOT_COMPONENTS
from .scene_plan import ScenePlan, build_scene_plan, terrain_height_m

#: req 1「180 个 pose 全部【有限】」: 几何字段必须显式拒绝 NaN/Inf。
#:
#: 为什么不复用 camera_plan.Matrix4: 那个类型是 canary 共用的, 收紧它会改动
#: canary 契约 (铁律 5)。所以生产档在【本模块内】定义自己的有限类型。
#:
#: 为什么必须有: 契约是给【反序列化】兜底的 —— canonical_production_plan_bytes
#: 走 json.dumps, Python json 会写出裸 NaN/Infinity 并能被读回, 于是一份被
#: 篡改或被下游工具写坏的 plan JSON 可以带着 NaN 位姿通过 model_validate_json。
_Finite = Annotated[float, Field(allow_inf_nan=False)]
FiniteVector3 = tuple[_Finite, _Finite, _Finite]
_FiniteRow4 = tuple[_Finite, _Finite, _Finite, _Finite]
FiniteMatrix4 = tuple[_FiniteRow4, _FiniteRow4, _FiniteRow4, _FiniteRow4]

PRODUCTION_PROFILE_ID = "synthetic-village-coverage-180-v1"
PRODUCTION_PLAN_SCHEMA = "nantai.synthetic-village.production-camera-plan.v1"
# journal schema 与 canary 不同 -> render_id 自动分叉 (canary.py:2405 payload 首键)
PRODUCTION_JOURNAL_SCHEMA = "nantai.synthetic-village.production-render-journal.v1"
TARGET_CAMERA_COUNT = 180

EYE_HEIGHT_M = 1.6
ROUTE_LOOKAHEAD_M = 25.0
CORRIDOR_LOOKAHEAD_M = 45.0
PERIMETER_MARGIN_M = 35.0
PERIMETER_EYE_HEIGHT_M = 6.0
OVERVIEW_ALTITUDE_M = 190.0

#: ground-route 相邻机位的最大 3D 间距。**这个常量是被 _validate_route_spacing
#: 真的读取的** —— 改它会改变布点的接受/拒绝行为, 不是装饰。
#:
#: 【名字为什么只说 ground-route】: 它只约束 ground-route。实测
#: environment-corridor 的相邻间距是 51.0-51.7 m, 远超 30 —— 把这道门套到
#: corridor 上, 真实数据当场 fail-closed; 而"为了让它过去"把 30 调到 52
#: 就是放宽门来凑过关。corridor 该不该有间距上限、上限是多少, 我【定不出来】
#: (见 pose_separation_evidence: 如实报分布, 不编阈值)。
#: 原名 MAX_ROUTE_CAMERA_SPACING_M 暗示它管所有 route, 与事实不符, 故改名。
MAX_GROUND_ROUTE_CAMERA_SPACING_M = 30.0

CameraGroupId = Literal[
    "ground-route",
    "elevated-pedestrian",
    "perimeter-inward",
    "environment-corridor",
    "audit-overview",
]

#: 抬升人行拓扑【本该】来自的槽位。理由必须点名它们, 且它们必须真的没几何。
ELEVATED_PEDESTRIAN_SLOTS: tuple[str, ...] = (
    "detail-stone-stair-01",
    "detail-timber-balcony-01",
)


def _elevated_pedestrian_reason() -> str:
    """从【实时证据】生成拒绝布点的理由, 而不是手写一段可能过期的话。

    以前这是一个手写常量, 唯一的约束是 min_length=20 —— 把整段实证换成
    "not placed for unspecified reasons xxxxxxxxxxxx" 全套照绿, 消费者拿到的
    "机器可读理由"不含任何机器可读信息。

    现在理由由 DETAIL_SLOT_COMPONENTS 的真实取值【派生】: 它点名的每个槽位
    都是查表查出来的, 编不出来也过不了期。
    """

    mapped = ", ".join(
        f"{slot}={DETAIL_SLOT_COMPONENTS.get(slot)!r}" for slot in ELEVATED_PEDESTRIAN_SLOTS
    )
    return (
        "scene plan exposes no elevated pedestrian topology: canary."
        f"DETAIL_SLOT_COMPONENTS maps these slots to None ({mapped}), i.e. they are "
        "texture slots with no geometry, and no walkway/terrace-top polyline exists. "
        "Placing cameras at an arbitrary height above the ground route would fabricate "
        "a pedestrian viewpoint that the scene does not contain."
    )


def _assert_elevated_pedestrian_is_really_absent() -> None:
    """拒绝 48 台相机是【重大主张】, 所以它必须持续挣得。

    一旦这些槽位长出几何, "场景里没有抬升人行拓扑"就成了假话 —— 此时继续
    拿一句陈旧理由拒绝布点, 与 fail-closed 恰好相反。宁可硬失败让人来看。
    """

    grounded = {
        slot: DETAIL_SLOT_COMPONENTS.get(slot)
        for slot in ELEVATED_PEDESTRIAN_SLOTS
        if DETAIL_SLOT_COMPONENTS.get(slot) is not None
    }
    if grounded:
        raise ProductionProfileError(
            "elevated pedestrian topology is no longer absent: these slots now carry "
            f"geometry {grounded} — the stale unplaced-group reason must not be reused; "
            "re-derive the elevated-pedestrian placement instead of refusing 48 cameras",
        )


class ProductionProfileError(ValueError):
    """生产档 profile 的稳定公开错误。"""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


@dataclass(frozen=True)
class UnavailableTopology:
    """某分组【没有】可信拓扑来源 —— 携带机器可读的理由, 绝不静默降级。"""

    group_id: str
    reason: str


@dataclass(frozen=True)
class PolylineTopologySource:
    """一条真实的、按弧长可参数化的折线拓扑。"""

    group_id: str
    topology_ref: str
    points: tuple[tuple[float, float], ...]
    half_width_m: float

    @property
    def length_m(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.points, self.points[1:], strict=False))


@dataclass(frozen=True)
class HullTopologySource:
    """由真实建筑足迹凸包外扩得到的周边拓扑 (不是一个圆)。"""

    group_id: str
    topology_ref: str
    hull: tuple[tuple[float, float], ...]


class GroupSpec(FrozenModel):
    group_id: CameraGroupId
    target_count: int = Field(ge=1)
    topology_kind: Literal["path-network", "creek-corridor", "building-hull", "overview", "absent"]
    disclosure: str = Field(min_length=10)


GROUP_SPECS: tuple[GroupSpec, ...] = (
    GroupSpec(
        group_id="ground-route",
        target_count=72,
        topology_kind="path-network",
        disclosure="pedestrian-eye-height-on-village-path-network",
    ),
    GroupSpec(
        group_id="elevated-pedestrian",
        target_count=48,
        topology_kind="absent",
        disclosure="no-elevated-pedestrian-topology-in-scene",
    ),
    GroupSpec(
        group_id="perimeter-inward",
        target_count=32,
        topology_kind="building-hull",
        disclosure="inward-facing-ring-on-building-footprint-hull",
    ),
    GroupSpec(
        group_id="environment-corridor",
        target_count=16,
        topology_kind="creek-corridor",
        disclosure="pedestrian-eye-height-along-creek-corridor",
    ),
    GroupSpec(
        group_id="audit-overview",
        target_count=12,
        topology_kind="overview",
        disclosure="audit-only-aerial-overview-not-a-pedestrian-viewpoint",
    ),
)


class ProductionCameraPose(FrozenModel):
    camera_id: str = Field(
        pattern=(
            r"^camera-(?:ground-route|elevated-pedestrian|perimeter-inward"
            r"|environment-corridor|audit-overview)-[0-9]{3}$"
        )
    )
    group_id: CameraGroupId
    sequence_index: int = Field(ge=1, le=TARGET_CAMERA_COUNT)
    topology_ref: str = Field(min_length=1)
    arc_length_m: float | None = Field(default=None, allow_inf_nan=False)
    position_m: FiniteVector3
    look_at_m: FiniteVector3
    eye_height_m: float = Field(gt=0, allow_inf_nan=False)
    fov_x_deg: float = Field(gt=0, lt=180, allow_inf_nan=False)
    intrinsics: CameraIntrinsics
    c2w_opencv: FiniteMatrix4
    audit_only: bool
    disclosure: str = Field(min_length=10)

    @model_validator(mode="after")
    def _validate(self) -> ProductionCameraPose:
        if not self.camera_id.startswith(f"camera-{self.group_id}-"):
            raise ValueError("camera ID prefix must match its group")
        if (self.group_id == "audit-overview") != self.audit_only:
            raise ValueError("audit_only must be set for exactly the audit-overview group")
        return self


class UnplacedGroup(FrozenModel):
    group_id: CameraGroupId
    camera_count: int = Field(ge=1)
    reason: str = Field(min_length=20)


class GroupCoverage(FrozenModel):
    group_id: CameraGroupId
    camera_count: int = Field(ge=1)
    topology_ref_count: int = Field(ge=1)


class UndeliveredRequirement(FrozenModel):
    """一条【没做到】的需求, 随 plan 一起落盘。

    为什么必须进 plan 而不是只写在交付报告里: 交付报告是给人读的散文, 而
    下游 (Codex) 是照着 plan 的机器可读字段安排后续工作的。一条没做的需求
    如果只在散文里缺席, 读者会默认它落地了 —— req 5 与 req 6 就是这样悄悄
    消失的。

    绝不用标量冒充: 一个 `"route_loops": 0` 读起来像"检测过, 结果是 0",
    而真相是"根本没检测"。所以这里【只有】status + reason, 没有假数字。
    """

    requirement_id: str = Field(min_length=1)
    #: not-implemented        = 没写这段代码
    #: structurally-unreachable = 代码写了也没用, 前提已被别的 fail-closed 废掉
    status: Literal["not-implemented", "structurally-unreachable"]
    reason: str = Field(min_length=20)


class ProductionCameraPlan(FrozenModel):
    schema_version: Literal[1] = 1
    plan_schema: Literal["nantai.synthetic-village.production-camera-plan.v1"] = (
        PRODUCTION_PLAN_SCHEMA
    )
    profile_id: Literal["synthetic-village-coverage-180-v1"] = PRODUCTION_PROFILE_ID
    journal_schema: Literal["nantai.synthetic-village.production-render-journal.v1"] = (
        PRODUCTION_JOURNAL_SCHEMA
    )
    scene_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_system: Literal["opencv-c2w-right-down-forward-meters"] = (
        "opencv-c2w-right-down-forward-meters"
    )
    # 铁律 5: 相机增多绝不提升 geometry trust
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    verification_level: Literal["L2"] = "L2"
    declared_target_count: Literal[180] = TARGET_CAMERA_COUNT
    camera_count: int = Field(ge=0)
    complete: bool
    cameras: tuple[ProductionCameraPose, ...]
    group_coverage: tuple[GroupCoverage, ...]
    unplaced_groups: tuple[UnplacedGroup, ...]
    #: 本轮【没做到】的需求, 逐条机器可读。空元组的含义是"全部交付", 所以它
    #: 不能被默认成空 —— 必须由 builder 显式给出。
    undelivered_requirements: tuple[UndeliveredRequirement, ...]

    @model_validator(mode="after")
    def _validate_plan(self) -> ProductionCameraPlan:
        if self.camera_count != len(self.cameras):
            raise ValueError("camera_count must equal the number of placed cameras")
        identifiers = [camera.camera_id for camera in self.cameras]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("camera IDs must be unique")
        # req 1 的另一半:「无重复中心」。两台相机占同一个世界中心 = 退化基线,
        # 对 COLMAP 只有害处。以前【只查 camera_id 去重】, 中心重复毫无约束。
        centres = [camera.position_m for camera in self.cameras]
        if len(centres) != len(set(centres)):
            duplicates = sorted({c for c in centres if centres.count(c) > 1})
            raise ValueError(f"camera centres must be unique, duplicated: {duplicates}")
        if tuple(c.sequence_index for c in self.cameras) != tuple(
            range(1, len(self.cameras) + 1)
        ):
            raise ValueError("camera sequence indices must be dense and ordered from 1")
        placed = self.camera_count + sum(row.camera_count for row in self.unplaced_groups)
        if placed != TARGET_CAMERA_COUNT:
            raise ValueError("placed plus unplaced cameras must equal the declared target")
        # fail-closed: 只有真的放满 180 才允许声称 complete
        if self.complete != (self.camera_count == TARGET_CAMERA_COUNT):
            raise ValueError("complete must be true only when the full target is placed")
        # 【这道门是不可达的】—— 如实标注, 不假装它在守什么。
        # 推导: complete=True ==> (上一道) camera_count == 180
        #       ==> (placed 那道) sum(unplaced_groups.camera_count) == 0
        #       ==> 而 UnplacedGroup.camera_count 的契约是 ge=1
        #       ==> unplaced_groups 只能是空元组 ==> 本条永远不成立。
        # 变异实验证实: 把它换成 `pass`, 全套 44 测照绿 —— 因为【构造不出】
        # 能触发它的输入, 不是因为没人测。保留它是纵深防御 (若日后有人调换
        # 上面几道的顺序、或放宽 UnplacedGroup.camera_count, 它会重新变得可达),
        # 但它【没有】也不可能有测试守着。
        if self.complete and self.unplaced_groups:
            raise ValueError("a complete plan must not declare unplaced groups")
        return self


def _path_segments(scene: ScenePlan) -> list[PolylineTopologySource]:
    sources: list[PolylineTopologySource] = []
    paths = [item for item in scene.objects if item.semantic_class == "path"]
    for item in sorted(paths, key=lambda o: o.polyline.segment_index):
        sources.append(
            PolylineTopologySource(
                group_id="ground-route",
                topology_ref=item.object_id,
                points=tuple((p.x_m, p.y_m) for p in item.polyline.points),
                half_width_m=item.polyline.width_m / 2,
            )
        )
    return sources


def _creek_source(scene: ScenePlan) -> PolylineTopologySource:
    creek = next(item for item in scene.objects if item.semantic_class == "creek")
    return PolylineTopologySource(
        group_id="environment-corridor",
        topology_ref=creek.object_id,
        points=tuple((p.x_m, p.y_m) for p in creek.polyline.points),
        half_width_m=creek.polyline.width_m / 2,
    )


def _convex_hull(points: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    ordered = sorted(set(points))
    if len(ordered) < 3:
        raise ProductionProfileError("building footprint hull requires at least three points")

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


def _building_hull_source(scene: ScenePlan) -> HullTopologySource:
    centres = [
        (item.transform.x_m, item.transform.y_m)
        for item in scene.objects
        if item.semantic_class == "building"
    ]
    return HullTopologySource(
        group_id="perimeter-inward",
        topology_ref="building-footprint-hull",
        hull=_convex_hull(centres),
    )


def resolve_topology_sources(
    scene: ScenePlan | None = None,
) -> dict[str, object]:
    """把每个分组解析到【真实拓扑来源】; 没有来源的返回 UnavailableTopology。"""
    scene = scene or build_scene_plan()
    _assert_elevated_pedestrian_is_really_absent()
    return {
        "ground-route": _path_segments(scene),
        "elevated-pedestrian": UnavailableTopology(
            group_id="elevated-pedestrian", reason=_elevated_pedestrian_reason()
        ),
        "perimeter-inward": _building_hull_source(scene),
        "environment-corridor": _creek_source(scene),
        "audit-overview": "overview",
    }


def _resample_polyline(
    source: PolylineTopologySource, count: int
) -> list[tuple[float, tuple[float, float], tuple[float, float]]]:
    """按【弧长】等距取样, 返回 (arc_length, point, unit_tangent)。"""
    points = source.points
    cumulative = [0.0]
    for a, b in zip(points, points[1:], strict=False):
        cumulative.append(cumulative[-1] + math.dist(a, b))
    total = cumulative[-1]
    if total <= 0.0:
        raise ProductionProfileError(f"degenerate polyline topology: {source.topology_ref}")

    samples = []
    for index in range(count):
        # 半格偏移: 避免样本落在端点上, 且相邻段之间不重复
        target = total * (index + 0.5) / count
        position = min(
            range(len(cumulative) - 1),
            key=lambda i: abs(cumulative[i] - target) if cumulative[i] <= target else math.inf,
        )
        for i in range(len(cumulative) - 1):
            if cumulative[i] <= target <= cumulative[i + 1]:
                position = i
                break
        start, end = points[position], points[position + 1]
        span = cumulative[position + 1] - cumulative[position]
        t = 0.0 if span <= 0 else (target - cumulative[position]) / span
        point = (start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1]))
        tangent_x, tangent_y = end[0] - start[0], end[1] - start[1]
        norm = math.hypot(tangent_x, tangent_y) or 1.0
        samples.append((target, point, (tangent_x / norm, tangent_y / norm)))
    return samples


def _point_along(
    source: PolylineTopologySource, arc_length: float
) -> tuple[float, float]:
    points = source.points
    cumulative = [0.0]
    for a, b in zip(points, points[1:], strict=False):
        cumulative.append(cumulative[-1] + math.dist(a, b))
    total = cumulative[-1]
    target = max(0.0, min(total, arc_length))
    for i in range(len(cumulative) - 1):
        if cumulative[i] <= target <= cumulative[i + 1]:
            span = cumulative[i + 1] - cumulative[i]
            t = 0.0 if span <= 0 else (target - cumulative[i]) / span
            start, end = points[i], points[i + 1]
            return (start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1]))
    return points[-1]


def _allocate(sources: list[PolylineTopologySource], total: int) -> list[int]:
    """按弧长比例分配相机数 (最大余数法), 保证总和精确等于 total。"""
    lengths = [source.length_m for source in sources]
    overall = sum(lengths)
    raw = [total * length / overall for length in lengths]
    counts = [int(math.floor(value)) for value in raw]
    remainder = total - sum(counts)
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - counts[i], lengths[i]), reverse=True)
    for i in order[:remainder]:
        counts[i] += 1
    return counts


def _pose(
    camera_id: str,
    group_id: str,
    sequence_index: int,
    topology_ref: str,
    arc_length_m: float | None,
    position: tuple[float, float, float],
    look_at: tuple[float, float, float],
    eye_height_m: float,
    fov_x_deg: float,
    disclosure: str,
) -> ProductionCameraPose:
    position_q = (_q3(position[0]), _q3(position[1]), _q3(position[2]))
    look_q = (_q3(look_at[0]), _q3(look_at[1]), _q3(look_at[2]))
    matrix = _look_at_c2w(np.array(position_q, dtype=float), np.array(look_q, dtype=float))
    return ProductionCameraPose(
        camera_id=camera_id,
        group_id=group_id,
        sequence_index=sequence_index,
        topology_ref=topology_ref,
        arc_length_m=None if arc_length_m is None else _q3(arc_length_m),
        position_m=position_q,
        look_at_m=look_q,
        eye_height_m=_q3(eye_height_m),
        fov_x_deg=fov_x_deg,
        intrinsics=_intrinsics(fov_x_deg),
        c2w_opencv=matrix,
        audit_only=group_id == "audit-overview",
        disclosure=disclosure,
    )


def _place_route_group(
    sources: list[PolylineTopologySource],
    total: int,
    scene: ScenePlan,
    group_id: str,
    fov_x_deg: float,
    lookahead_m: float,
    disclosure: str,
    start_index: int,
) -> list[ProductionCameraPose]:
    counts = _allocate(sources, total)
    poses: list[ProductionCameraPose] = []
    index = start_index
    number = 1
    for source, count in zip(sources, counts, strict=True):
        if count <= 0:
            continue
        for arc_length, point, tangent in _resample_polyline(source, count):
            z = terrain_height_m(point[0], point[1], scene.extent) + EYE_HEIGHT_M
            ahead = _point_along(source, arc_length + lookahead_m)
            if math.dist(point, ahead) < 1.0:
                ahead = (point[0] + tangent[0] * lookahead_m, point[1] + tangent[1] * lookahead_m)
            look_z = terrain_height_m(ahead[0], ahead[1], scene.extent) + EYE_HEIGHT_M
            poses.append(
                _pose(
                    camera_id=f"camera-{group_id}-{number:03d}",
                    group_id=group_id,
                    sequence_index=index,
                    topology_ref=source.topology_ref,
                    arc_length_m=arc_length,
                    position=(point[0], point[1], z),
                    look_at=(ahead[0], ahead[1], look_z),
                    eye_height_m=EYE_HEIGHT_M,
                    fov_x_deg=fov_x_deg,
                    disclosure=disclosure,
                )
            )
            index += 1
            number += 1
    return poses


def _place_perimeter(
    source: HullTopologySource, total: int, scene: ScenePlan, start_index: int
) -> list[ProductionCameraPose]:
    hull = source.hull
    centre = (
        sum(p[0] for p in hull) / len(hull),
        sum(p[1] for p in hull) / len(hull),
    )
    ring = PolylineTopologySource(
        group_id="perimeter-inward",
        topology_ref=source.topology_ref,
        points=(*hull, hull[0]),
        half_width_m=0.0,
    )
    poses: list[ProductionCameraPose] = []
    for number, (arc_length, point, _tangent) in enumerate(
        _resample_polyline(ring, total), start=1
    ):
        outward_x, outward_y = point[0] - centre[0], point[1] - centre[1]
        norm = math.hypot(outward_x, outward_y) or 1.0
        x = point[0] + outward_x / norm * PERIMETER_MARGIN_M
        y = point[1] + outward_y / norm * PERIMETER_MARGIN_M
        half_width, half_depth = scene.extent.width_m / 2, scene.extent.depth_m / 2
        x = max(-half_width + 1.0, min(half_width - 1.0, x))
        y = max(-half_depth + 1.0, min(half_depth - 1.0, y))
        z = terrain_height_m(x, y, scene.extent) + PERIMETER_EYE_HEIGHT_M
        target_z = terrain_height_m(centre[0], centre[1], scene.extent) + EYE_HEIGHT_M
        poses.append(
            _pose(
                camera_id=f"camera-perimeter-inward-{number:03d}",
                group_id="perimeter-inward",
                sequence_index=start_index + number - 1,
                topology_ref=source.topology_ref,
                arc_length_m=arc_length,
                position=(x, y, z),
                look_at=(centre[0], centre[1], target_z),
                eye_height_m=PERIMETER_EYE_HEIGHT_M,
                fov_x_deg=75.0,
                disclosure="inward-facing-ring-on-building-footprint-hull",
            )
        )
    return poses


def _place_audit_overview(
    total: int, scene: ScenePlan, start_index: int
) -> list[ProductionCameraPose]:
    """审计俯瞰 —— 【显式标记】, 不冒充地面漫游视角。"""
    poses: list[ProductionCameraPose] = []
    radius = min(scene.extent.width_m, scene.extent.depth_m) / 2 * 0.8
    for number in range(1, total + 1):
        angle = 2 * math.pi * (number - 1) / total
        x = math.cos(angle) * radius
        y = math.sin(angle) * radius
        z = terrain_height_m(0.0, 0.0, scene.extent) + OVERVIEW_ALTITUDE_M
        poses.append(
            _pose(
                camera_id=f"camera-audit-overview-{number:03d}",
                group_id="audit-overview",
                sequence_index=start_index + number - 1,
                topology_ref="scene-extent-overview-ring",
                arc_length_m=None,
                position=(x, y, z),
                look_at=(0.0, 0.0, terrain_height_m(0.0, 0.0, scene.extent)),
                eye_height_m=OVERVIEW_ALTITUDE_M,
                fov_x_deg=75.0,
                disclosure="audit-only-aerial-overview-not-a-pedestrian-viewpoint",
            )
        )
    return poses


#: 本轮【没做到】的需求 —— 逐条说出来, 而不是让读者以为它们落地了。
UNDELIVERED_REQUIREMENT_IDS: tuple[str, ...] = (
    "req-3-front-back-facade-coverage",
    "req-5-pose-quality-fail-closed",
    "req-6-route-loop-closure",
)


def _undelivered_requirements() -> tuple[UndeliveredRequirement, ...]:
    return (
        UndeliveredRequirement(
            requirement_id="req-3-front-back-facade-coverage",
            status="not-implemented",
            reason=(
                "req 3 asks that every instantiated building / bridge / courtyard / "
                "environment component have both front and reverse facade coverage. No "
                "front/back determination is implemented anywhere: object_registry carries "
                "no per-component orientation, so no facade can be named 'front', and "
                "adding orientation to the build was not attempted. This is a missing "
                "input, not a limit of the evidence -- do not read it as 'impossible'. "
                "What is delivered, on the canary's 24 frames only, is "
                "observed_normal_angular_spread_deg: a per-component continuous quantity "
                "recomputed from the journal-anchored normal layer that measures whether "
                "distinct surfaces were observed. It does not identify which surface is "
                "the front, so it does not satisfy req 3, and no threshold is declared on "
                "it. This 180-camera profile renders no frames at all, so even that "
                "evidence does not exist here."
            ),
        ),
        UndeliveredRequirement(
            requirement_id="req-5-pose-quality-fail-closed",
            status="not-implemented",
            reason=(
                "near-duplicate pose / isolated camera / bad frame / low valid-pixel "
                "ratio detection is not implemented: _validate_plan checks only camera "
                "ID uniqueness, centre uniqueness and dense sequence indices, never pose "
                "quality. Bad frames and valid-pixel ratio are undefined before rendering "
                "(no renderer exists for this profile). For near-duplicate poses the raw "
                "distribution is published by pose_separation_evidence(), but no "
                "fail-closed threshold is declared because none is defensible on this "
                "evidence — see that function's disclaimer."
            ),
        ),
        UndeliveredRequirement(
            requirement_id="req-6-route-loop-closure",
            status="structurally-unreachable",
            reason=(
                "req 6 asks ground-route and elevated-pedestrian to jointly form at least "
                "two closed loops for COLMAP loop closure. The elevated-pedestrian group "
                "(48 cameras) is unplaced because the scene contains no elevated pedestrian "
                "geometry, so the requirement cannot be met by rendering more frames — the "
                "fail-closed decision on that group structurally voids it. No route_loops "
                "quantity is computed or claimed anywhere in this profile."
            ),
        ),
    )


def pose_separation_evidence(plan: ProductionCameraPlan) -> dict[str, object]:
    """全部相机两两中心距离的【实测分布】—— 证据, 不是判据。

    req 5 要求对"近重复 pose"fail-closed。**我定不出"多近算近重复"**:
    它取决于 COLMAP 的基线/视差需求与场景尺度, 这份证据里没有任何东西能
    支撑某一个具体数字。所以这里【只报分布, 不设阈值】, 并显式声明没有阈值 ——
    挑一个数假装它是判据, 比不做更糟, 因为下游会以为这条已经被守住了。

    实测最近一对约 3.5 m (ground-route-004 与 environment-corridor-002 在
    溪流与路网交汇处), 由消费者自行决定它是否构成退化基线。
    """

    cameras = plan.cameras
    pairs: list[tuple[float, str, str]] = []
    for index, left in enumerate(cameras):
        for right in cameras[index + 1 :]:
            pairs.append((math.dist(left.position_m, right.position_m), left.camera_id,
                          right.camera_id))
    if not pairs:
        raise ProductionProfileError("pose separation needs at least two cameras")
    pairs.sort()
    distances = [row[0] for row in pairs]
    return {
        "pair_count": len(pairs),
        "nearest_pair_m": round(distances[0], 3),
        "nearest_pair": (pairs[0][1], pairs[0][2]),
        "closest_ten_m": [round(value, 3) for value in distances[:10]],
        "median_pair_m": round(distances[len(distances) // 2], 3),
        "farthest_pair_m": round(distances[-1], 3),
        "threshold": None,
        "disclaimer": (
            "no-threshold-declared: this is a measured distribution, not a criterion. "
            "'How close is a near-duplicate pose' is not derivable from this evidence, "
            "so req 5 is reported as not-implemented rather than being backed by an "
            "invented number. Do not read the absence of a flag as a pass."
        ),
    }


def _validate_route_spacing(cameras: list[ProductionCameraPose]) -> None:
    """ground-route 相邻机位间距必须真的落在声明的上限内 —— fail-closed。

    这道门的存在意义是把 MAX_GROUND_ROUTE_CAMERA_SPACING_M 与它声称约束的
    东西【连起来】: 以前那个常量全仓库无任何引用, 调它代码行为完全不变。
    """

    by_route: dict[str, list[ProductionCameraPose]] = {}
    for camera in cameras:
        if camera.group_id == "ground-route":
            by_route.setdefault(camera.topology_ref, []).append(camera)
    for topology_ref, rows in by_route.items():
        ordered = sorted(rows, key=lambda c: c.arc_length_m or 0.0)
        for left, right in zip(ordered, ordered[1:], strict=False):
            gap = math.dist(left.position_m, right.position_m)
            if gap > MAX_GROUND_ROUTE_CAMERA_SPACING_M:
                raise ProductionProfileError(
                    f"ground-route camera spacing exceeds the declared maximum on "
                    f"{topology_ref}: {left.camera_id} -> {right.camera_id} is "
                    f"{gap:.3f} m > {MAX_GROUND_ROUTE_CAMERA_SPACING_M} m",
                )


def build_production_camera_plan(scene: ScenePlan | None = None) -> ProductionCameraPlan:
    """构造生产档相机计划。

    fail-closed: 没有真实拓扑来源的分组【不放相机】, 并在 unplaced_groups 里
    带机器可读理由。plan.camera_count 永远等于【真的放下的】相机数, 绝不
    声称 180。
    """
    scene = scene or build_scene_plan()
    resolved = resolve_topology_sources(scene)

    cameras: list[ProductionCameraPose] = []
    unplaced: list[UnplacedGroup] = []

    for spec in GROUP_SPECS:
        source = resolved[spec.group_id]
        if isinstance(source, UnavailableTopology):
            unplaced.append(
                UnplacedGroup(
                    group_id=spec.group_id,
                    camera_count=spec.target_count,
                    reason=source.reason,
                )
            )
            continue
        start = len(cameras) + 1
        if spec.group_id == "ground-route":
            cameras.extend(
                _place_route_group(
                    source,
                    spec.target_count,
                    scene,
                    "ground-route",
                    65.0,
                    ROUTE_LOOKAHEAD_M,
                    spec.disclosure,
                    start,
                )
            )
        elif spec.group_id == "environment-corridor":
            cameras.extend(
                _place_route_group(
                    [source],
                    spec.target_count,
                    scene,
                    "environment-corridor",
                    65.0,
                    CORRIDOR_LOOKAHEAD_M,
                    spec.disclosure,
                    start,
                )
            )
        elif spec.group_id == "perimeter-inward":
            cameras.extend(_place_perimeter(source, spec.target_count, scene, start))
        elif spec.group_id == "audit-overview":
            cameras.extend(_place_audit_overview(spec.target_count, scene, start))

    _validate_route_spacing(cameras)

    coverage = []
    for spec in GROUP_SPECS:
        rows = [camera for camera in cameras if camera.group_id == spec.group_id]
        if rows:
            coverage.append(
                GroupCoverage(
                    group_id=spec.group_id,
                    camera_count=len(rows),
                    topology_ref_count=len({camera.topology_ref for camera in rows}),
                )
            )

    return ProductionCameraPlan(
        scene_plan_sha256=_scene_digest(scene),
        camera_count=len(cameras),
        complete=len(cameras) == TARGET_CAMERA_COUNT,
        cameras=tuple(cameras),
        group_coverage=tuple(coverage),
        unplaced_groups=tuple(unplaced),
        undelivered_requirements=_undelivered_requirements(),
    )


def canonical_production_plan_bytes(plan: ProductionCameraPlan) -> bytes:
    payload = plan.model_dump(mode="json")
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def production_camera_registry_digest(plan: ProductionCameraPlan) -> str:
    """独立于 canary 的相机 registry 摘要 (绑定 profile + 相机 ID + 位姿)。"""
    payload = {
        "profile_id": plan.profile_id,
        "plan_schema": plan.plan_schema,
        "scene_plan_sha256": plan.scene_plan_sha256,
        "cameras": [
            {
                "camera_id": camera.camera_id,
                "group_id": camera.group_id,
                "c2w_opencv": camera.c2w_opencv,
            }
            for camera in plan.cameras
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def production_batch_slice(
    plan: ProductionCameraPlan, *, batch_index: int, batch_count: int
) -> tuple[str, ...]:
    """稳定 batch 切片: 按 sequence_index 轮转分配, 每台相机【恰好】属于一个批次。

    轮转 (而非连续块) 保证每个批次都跨分组/跨路段 —— 单批次失败不会整片丢掉
    某一条路线。
    """
    if batch_count < 1:
        raise ProductionProfileError("batch_count must be at least 1")
    if not 0 <= batch_index < batch_count:
        raise ProductionProfileError("batch_index is outside the declared batch range")
    return tuple(
        camera.camera_id
        for position, camera in enumerate(plan.cameras)
        if position % batch_count == batch_index
    )
