"""生产档逐帧 durable render journal —— 【独立 schema】, 不与 canary 混用。

为什么必须独立 (实测结论, 不是设计偏好):
  给 canary 的 RenderFrameRecord 加【任何】字段 (哪怕带默认值的可选字段),
  现有 24 帧真实 journal 会【立刻失效】—— FrozenModel 是 extra='forbid',
  canonical bytes 会多出该键, 于是 raw != canonical_bytes 触发
  'render journal must be canonical JSON', 且 journal_sha256 不再匹配。
  所以"逐帧记录真实耗时"与"不破坏 canary journal"只能靠【独立 schema】共存。

耗时与内容寻址的分工 (关键):
  * render_id  = 内容寻址, 【绝不】包含任何耗时 —— 同样的输入必须得到同样的
    render_id, 而耗时不可能可复现。
  * journal_sha256 = 这次【实际运行】的自摘要, 【包含】耗时 —— 它记录的是
    "这一次真的跑了多久", 本就不该跨运行复现。
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .production_profile import (
    PRODUCTION_JOURNAL_SCHEMA,
    PRODUCTION_PROFILE_ID,
    ProductionCameraPlan,
    ProductionProfileError,
)

# canary 的单帧超时机制已存在 (canary.py:2362 subprocess timeout), 缺的只是
# "如实报告实际耗时"。生产档沿用同一个默认限额。
DEFAULT_RENDER_TIMEOUT_SECONDS = 15 * 60

_HEX_CHARS = frozenset("0123456789abcdef")


def _require_64_hex_sha(value: str, field_name: str) -> None:
    """Fail-closed 校验: 可选 SHA 绑定键必须是严格 64-hex SHA-256。

    防止 caller 把目录名 / build_id / engine 名等非 SHA 字符串当 SHA 绑定进
    canonical identity —— 那会让 render_id 内容寻址失效。
    """
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in _HEX_CHARS for c in value)
    ):
        raise ProductionProfileError(
            f"{field_name} must be a 64-hex-char SHA-256 string",
        )

FrameState = Literal["planned", "rendering", "verified", "failed", "timed-out"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProductionArtifactRecord(FrozenModel):
    kind: Literal["rgb", "depth", "normal", "instance-mask", "semantic-mask", "camera-metadata"]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)


def expected_production_artifacts(camera_id: str) -> tuple[tuple[str, str], ...]:
    """一帧的【六文件契约】: (kind, path) 逐元组绑定到 camera_id。

    与 canary._expected_render_artifacts 【同一套】布局 (同样的 kind 顺序、同样的
    目录、同样的扩展名) —— 生产档只换相机 ID 命名空间, 不换渲染产物的契约。
    覆盖审计按 renders/<path> 读盘, 所以布局分叉会让审计直接读不到东西。
    """

    return (
        ("rgb", f"rgb/{camera_id}.png"),
        ("depth", f"depth/{camera_id}.exr"),
        ("normal", f"normal/{camera_id}.exr"),
        ("instance-mask", f"instance/{camera_id}.png"),
        ("semantic-mask", f"semantic/{camera_id}.png"),
        ("camera-metadata", f"cameras/{camera_id}.json"),
    )


class ProductionFrameRecord(FrozenModel):
    """逐帧记录。耗时【如实报告】: 没跑过就是 None, 绝不填 0 冒充。"""

    camera_id: str = Field(min_length=1)
    state: FrameState
    artifacts: tuple[ProductionArtifactRecord, ...] = ()
    # 真实墙钟耗时 —— 宿主侧 time.monotonic() 包住 blender 子进程整个周期
    # (启动 + 载入 blend + 渲染 + 脚本内校验)。【无法】拆分出纯渲染时间:
    # 那需要在 renderer script 内计时, 会改 renderer_script_sha256 -> render_id 变。
    wall_clock_seconds: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    timeout_limit_seconds: int = Field(ge=1, le=86400)
    error: str | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> ProductionFrameRecord:
        if self.state == "verified":
            # 只数个数是【实质降级】: 六个产物可以全是 rgb, 也可以是别的相机的。
            # 逐 (kind, path) 元组比对 —— 与 canary._validate_artifact_contract 同强度。
            if tuple((item.kind, item.path) for item in self.artifacts) != (
                expected_production_artifacts(self.camera_id)
            ):
                raise ValueError("render frame artifacts are not the exact six-file contract")
            if self.error is not None:
                raise ValueError("verified frame must not carry an error")
            if self.wall_clock_seconds is None:
                raise ValueError("verified frame must report its real wall-clock duration")
        if self.state in {"failed", "timed-out"}:
            if self.error is None:
                raise ValueError("failed frame must carry an error")
            if self.artifacts:
                raise ValueError("failed frame must not carry artifacts")
            if self.wall_clock_seconds is None:
                raise ValueError("failed frame must report how long it actually ran")
        if self.state in {"planned", "rendering"}:
            if self.artifacts or self.error is not None:
                raise ValueError("unfinished frame must not declare evidence")
            if self.wall_clock_seconds is not None:
                raise ValueError("unfinished frame must not declare a duration")
        if self.state == "timed-out" and self.wall_clock_seconds is not None:
            # 超时帧必须【同时】报出实际耗时和限额 —— 只报限额是现状的缺陷
            if self.wall_clock_seconds < self.timeout_limit_seconds:
                raise ValueError("timed-out frame must have run at least the timeout limit")
        return self


class ProductionRenderJournal(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.production-render-journal.v1"] = (
        PRODUCTION_JOURNAL_SCHEMA
    )
    profile_id: Literal["synthetic-village-coverage-180-v1"] = PRODUCTION_PROFILE_ID
    render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # 铁律 5: 相机增多绝不提升 geometry trust
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    verification_level: Literal["L2"] = "L2"
    frames: tuple[ProductionFrameRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_journal(self) -> ProductionRenderJournal:
        ids = [frame.camera_id for frame in self.frames]
        if len(ids) != len(set(ids)):
            raise ValueError("journal frames must have unique camera IDs")
        return self

    @property
    def total_wall_clock_seconds(self) -> float:
        """已【真实测量到】的总耗时。未跑的帧不计 —— 绝不外推冒充测量值。"""
        return sum(
            frame.wall_clock_seconds
            for frame in self.frames
            if frame.wall_clock_seconds is not None
        )

    @property
    def measured_frame_count(self) -> int:
        return sum(1 for frame in self.frames if frame.wall_clock_seconds is not None)


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def canonical_production_journal_bytes(journal: ProductionRenderJournal) -> bytes:
    return _canonical(journal.model_dump(mode="json"))


def compute_journal_sha256(journal: ProductionRenderJournal) -> str:
    """自摘要: 排除 journal_sha256 自身。【包含】耗时 —— 这是一次真实运行的记录。"""
    payload = journal.model_dump(mode="json")
    payload.pop("journal_sha256", None)
    return hashlib.sha256(_canonical(payload)).hexdigest()


def production_render_id(
    plan: ProductionCameraPlan,
    *,
    blender_executable_sha256: str,
    renderer_script_sha256: str,
    blend_sha256: str,
    build_report_sha256: str,
    camera_registry_sha256: str,
    preflight_id: str | None = None,
    quality_policy_sha256: str | None = None,
    post_render_policy_sha256: str | None = None,
    repose_search_sha256: str | None = None,
    build_adapter: str | None = None,
    environment_module_build_report_sha256: str | None = None,
) -> str:
    """内容寻址的 render_id。

    schema_version 是被 hash 的 payload 的第一个键 —— 因为它与 canary 的
    RENDER_JOURNAL_SCHEMA 不同, 生产档的 render_id 【自动】与 canary 分叉,
    不需要改 canary 一个字节。

    【绝不】包含耗时: 耗时不可复现, 放进内容寻址会让同样的输入得到不同的
    render_id。

    ``repose_search_sha256`` 是可选的 Task 5 §3 caller 绑定键: 如果一次
    render 是从 ``build_reposed_plan`` 重建的 plan 跑出来的, caller 应该
    把 ``ReplacementPoseSearch.search_sha256`` 传进来, 让 render_id 自动
    内容绑定到产生这个 plan 的 repose search。这样下游可以验证: 这次
    render 真的是从某个特定的 repose search 派生的, 不是被替换的 plan
    跑出来的伪造 render。

    ``environment_module_build_report_sha256`` 是可选的 Task 5 §3 caller
    绑定键: 当 §3 caller 把 175-root ``EnvironmentModuleBuildReport`` 的
    实测 SHA 传进来时, render_id 自动内容绑定到该 module build。这样下游可以
    验证: 这次 render 真的是从某个特定的 175-root module scene 派生的,
    不是被替换的 base blend 跑出来的伪造 render。

    严格 fail-closed (所有可选 SHA 绑定键一律校验):
      * ``preflight_id`` / ``quality_policy_sha256`` /
        ``post_render_policy_sha256`` / ``repose_search_sha256`` /
        ``environment_module_build_report_sha256`` 必须是严格 64-hex SHA-256
        —— 防止 caller 把目录名 / build_id / engine 名等非 SHA 字符串当 SHA
        绑定。``production_render_id`` 是公开 API, 即便 caller 端 Pydantic
        schema 已校验, 函数自身也独立 fail-closed。
      * 只能绑定【实测】SHA, 不能从任何元数据推断 —— provenance safety 要求
        可信度只从机器可验证字段推导。

    绑定是【可选】的: 当所有可选绑定键均为 None 时, render_id 与既有行为
    完全相同, 既有 journal 不受影响。
    """
    if environment_module_build_report_sha256 is not None:
        _require_64_hex_sha(
            environment_module_build_report_sha256,
            "environment_module_build_report_sha256",
        )
    payload = {
        "schema_version": PRODUCTION_JOURNAL_SCHEMA,
        "profile_id": plan.profile_id,
        "blend_sha256": blend_sha256,
        "blender_executable_sha256": blender_executable_sha256,
        "build_report_sha256": build_report_sha256,
        "camera_registry_sha256": camera_registry_sha256,
        "renderer_script_sha256": renderer_script_sha256,
        "scene_plan_sha256": plan.scene_plan_sha256,
        "camera_ids": [camera.camera_id for camera in plan.cameras],
    }
    if preflight_id is not None:
        _require_64_hex_sha(preflight_id, "preflight_id")
        payload["preflight_id"] = preflight_id
    if quality_policy_sha256 is not None:
        _require_64_hex_sha(quality_policy_sha256, "quality_policy_sha256")
        payload["quality_policy_sha256"] = quality_policy_sha256
    if post_render_policy_sha256 is not None:
        _require_64_hex_sha(
            post_render_policy_sha256,
            "post_render_policy_sha256",
        )
        payload["post_render_policy_sha256"] = post_render_policy_sha256
    if repose_search_sha256 is not None:
        _require_64_hex_sha(repose_search_sha256, "repose_search_sha256")
        payload["repose_search_sha256"] = repose_search_sha256
    if build_adapter is not None:
        payload["build_adapter"] = build_adapter
    if environment_module_build_report_sha256 is not None:
        payload["environment_module_build_report_sha256"] = (
            environment_module_build_report_sha256
        )
    return hashlib.sha256(_canonical(payload)).hexdigest()


def revalidate_journal(journal: ProductionRenderJournal) -> ProductionRenderJournal:
    """强制让 journal 重新过一遍全部 validator。

    为什么必须有这个: pydantic 的 model_copy(update=...) 【不做校验】——
    用它可以造出 state='verified' 却没有耗时/产物的非法 journal, 状态机
    形同虚设。任何状态跃迁都必须经由这里 (或 transition_frame) 落地。
    """
    return ProductionRenderJournal.model_validate_json(
        canonical_production_journal_bytes(journal)
    )


def transition_frame(
    journal: ProductionRenderJournal,
    camera_id: str,
    **updates: object,
) -> ProductionRenderJournal:
    """迁移单帧状态并【重新校验】, 同时刷新 journal_sha256。"""
    known = {frame.camera_id for frame in journal.frames}
    if camera_id not in known:
        raise ProductionProfileError(f"camera ID is not in this journal: {camera_id}")
    frames = tuple(
        frame.model_copy(update=updates) if frame.camera_id == camera_id else frame
        for frame in journal.frames
    )
    moved = revalidate_journal(journal.model_copy(update={"frames": frames}))
    return moved.model_copy(update={"journal_sha256": compute_journal_sha256(moved)})


def frames_needing_render(journal: ProductionRenderJournal) -> tuple[str, ...]:
    """只补【未验证】帧。verified 只是进入复验的门票, 不构成信任 ——
    真正的信任由调用方对磁盘字节重算 sha256 挣回 (沿用 canary 的语义)。
    """
    return tuple(frame.camera_id for frame in journal.frames if frame.state != "verified")


def new_production_journal(
    plan: ProductionCameraPlan,
    *,
    render_id: str,
    camera_registry_sha256: str,
    camera_ids: tuple[str, ...] | None = None,
    timeout_limit_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> ProductionRenderJournal:
    known = {camera.camera_id for camera in plan.cameras}
    if camera_ids is None:
        selected = tuple(camera.camera_id for camera in plan.cameras)
    else:
        selected = camera_ids
    unknown = [camera_id for camera_id in selected if camera_id not in known]
    if unknown:
        raise ProductionProfileError(f"camera IDs are not in the production plan: {unknown}")
    if not selected:
        raise ProductionProfileError("a journal must cover at least one camera")
    journal = ProductionRenderJournal(
        render_id=render_id,
        journal_sha256="0" * 64,
        camera_registry_sha256=camera_registry_sha256,
        scene_plan_sha256=plan.scene_plan_sha256,
        frames=tuple(
            ProductionFrameRecord(
                camera_id=camera_id,
                state="planned",
                timeout_limit_seconds=timeout_limit_seconds,
            )
            for camera_id in selected
        ),
    )
    return journal.model_copy(update={"journal_sha256": compute_journal_sha256(journal)})


def extrapolate_total_seconds(
    journal: ProductionRenderJournal, *, target_frame_count: int
) -> dict[str, object]:
    """从【小批次实测】外推总耗时。

    这是【外推不是承诺】: 已测样本是"已知会成功的那些相机", 未见过的视角
    (几何更密集/可见实例更多) 耗时分布未知, 用成功样本外推在统计上不成立。
    返回值显式携带该免责声明, 调用方无法在不看见它的情况下拿到数字。
    """
    measured = [
        frame.wall_clock_seconds
        for frame in journal.frames
        if frame.wall_clock_seconds is not None and frame.state == "verified"
    ]
    if not measured:
        raise ProductionProfileError(
            "cannot extrapolate without at least one measured verified frame"
        )
    mean = sum(measured) / len(measured)
    return {
        "measured_frame_count": len(measured),
        "measured_total_seconds": round(sum(measured), 3),
        "measured_mean_seconds": round(mean, 3),
        "measured_min_seconds": round(min(measured), 3),
        "measured_max_seconds": round(max(measured), 3),
        "target_frame_count": target_frame_count,
        "extrapolated_total_seconds": round(mean * target_frame_count, 3),
        "basis": "arithmetic-mean-of-measured-verified-frames",
        "disclaimer": (
            "extrapolation-not-a-promise: measured frames are the cameras already known "
            "to succeed; unseen production viewpoints may sit closer to dense geometry "
            "and cost more. Do not treat this as a schedule commitment."
        ),
    }
