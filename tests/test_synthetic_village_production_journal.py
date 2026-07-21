"""生产档 journal 的契约测试 —— 耗时如实报告, 且绝不污染内容寻址。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.production_journal import (
    DEFAULT_RENDER_TIMEOUT_SECONDS,
    ProductionArtifactRecord,
    ProductionFrameRecord,
    canonical_production_journal_bytes,
    compute_journal_sha256,
    expected_production_artifacts,
    extrapolate_total_seconds,
    frames_needing_render,
    new_production_journal,
    production_render_id,
    revalidate_journal,
    transition_frame,
)
from pipeline.synthetic_village.production_profile import (
    ProductionProfileError,
    build_production_camera_plan,
    production_camera_registry_digest,
)

DIGEST = "a" * 64


@pytest.fixture(scope="module")
def plan():
    return build_production_camera_plan()


@pytest.fixture(scope="module")
def render_id(plan):
    return production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
    )


def _journal(plan, render_id, camera_ids=None):
    return new_production_journal(
        plan,
        render_id=render_id,
        camera_registry_sha256=production_camera_registry_digest(plan),
        camera_ids=camera_ids,
    )


# --------------------------------------------------------------------------
# 铁律 3: 逐帧记录【真实】墙钟耗时
# --------------------------------------------------------------------------


def _six_artifacts(camera_id: str = "camera-ground-route-001") -> tuple[
    ProductionArtifactRecord, ...
]:
    """按【六文件契约】生成一帧的合法产物 —— kind 与 path 都绑定到 camera_id。"""

    return tuple(
        ProductionArtifactRecord(kind=kind, path=path, sha256="b" * 64, size_bytes=10)
        for kind, path in expected_production_artifacts(camera_id)
    )


def test_verified_frame_cannot_omit_its_real_duration() -> None:
    """'verified 但不知道跑了多久' 是不允许的状态。

    必须给满 6 个产物, 否则先撞上产物契约, 测不到耗时这条规则。
    """
    with pytest.raises(ValidationError, match="wall-clock"):
        ProductionFrameRecord(
            camera_id="camera-ground-route-001",
            state="verified",
            artifacts=_six_artifacts(),
            wall_clock_seconds=None,
            timeout_limit_seconds=DEFAULT_RENDER_TIMEOUT_SECONDS,
        )


def test_failed_frame_must_report_how_long_it_actually_ran() -> None:
    with pytest.raises(ValidationError, match="how long it actually ran"):
        ProductionFrameRecord(
            camera_id="camera-ground-route-001",
            state="failed",
            error="blender exited non-zero",
            wall_clock_seconds=None,
            timeout_limit_seconds=DEFAULT_RENDER_TIMEOUT_SECONDS,
        )


def test_unfinished_frame_must_not_invent_a_duration() -> None:
    """没跑过就【没有】耗时 —— 绝不填 0 冒充测量值。"""
    with pytest.raises(ValidationError, match="must not declare a duration"):
        ProductionFrameRecord(
            camera_id="camera-ground-route-001",
            state="planned",
            wall_clock_seconds=0.0,
            timeout_limit_seconds=DEFAULT_RENDER_TIMEOUT_SECONDS,
        )


def test_planned_frames_have_no_duration_and_no_evidence(plan, render_id) -> None:
    journal = _journal(plan, render_id)
    for frame in journal.frames:
        assert frame.state == "planned"
        assert frame.wall_clock_seconds is None
        assert frame.artifacts == ()
    assert journal.measured_frame_count == 0
    assert journal.total_wall_clock_seconds == 0.0


def test_timed_out_frame_reports_actual_duration_not_only_the_limit() -> None:
    """现状的缺陷是只说限额不说实际跑了多久 —— 这里必须两者都有。"""
    frame = ProductionFrameRecord(
        camera_id="camera-ground-route-001",
        state="timed-out",
        error="Blender render exceeded the 900-second timeout",
        wall_clock_seconds=901.4,
        timeout_limit_seconds=900,
    )
    assert frame.wall_clock_seconds == 901.4
    assert frame.timeout_limit_seconds == 900

    with pytest.raises(ValidationError, match="at least the timeout limit"):
        ProductionFrameRecord(
            camera_id="camera-ground-route-001",
            state="timed-out",
            error="Blender render exceeded the 900-second timeout",
            wall_clock_seconds=12.0,
            timeout_limit_seconds=900,
        )


# --------------------------------------------------------------------------
# 耗时绝不污染内容寻址
# --------------------------------------------------------------------------


def test_render_id_is_independent_of_timing(plan, render_id) -> None:
    """同样的输入必须给同样的 render_id, 无论跑了多久。"""
    again = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
    )
    assert render_id == again


def test_render_id_payload_contains_no_timing_key(plan, render_id) -> None:
    import inspect

    from pipeline.synthetic_village import production_journal

    source = inspect.getsource(production_journal.production_render_id)
    body = source.split('payload = {')[1].split('}')[0]
    for banned in ("wall_clock", "duration", "seconds", "timeout", "elapsed"):
        assert banned not in body, f"render_id payload 不得包含耗时: {banned}"


def test_render_id_diverges_from_canary_via_schema_version(plan) -> None:
    from pipeline.synthetic_village import canary
    from pipeline.synthetic_village.production_journal import PRODUCTION_JOURNAL_SCHEMA

    assert PRODUCTION_JOURNAL_SCHEMA != canary.RENDER_JOURNAL_SCHEMA


def test_render_id_binds_repose_search_sha_when_provided(plan, render_id) -> None:
    """render_id must change when repose_search_sha256 is added.

    Task 5 §3 callers use build_reposed_plan to rebuild a plan from a
    ReplacementPoseSearch. The render_id for that rebuilt plan must
    bind to the search that produced it, so a downstream verifier can
    confirm "this render came from this specific repose search, not
    from a fabricated plan that looks similar".
    """
    search_sha = "c" * 64
    bound_render_id = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        repose_search_sha256=search_sha,
    )
    # Bound render_id differs from the unbound one.
    assert bound_render_id != render_id


def test_render_id_ignores_repose_search_sha_when_none(plan, render_id) -> None:
    """render_id must be identical when repose_search_sha256 is None.

    Optional binding means existing journals are unaffected: callers
    that do not pass repose_search_sha256 get exactly the same render_id
    they got before this parameter existed.
    """
    again = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        repose_search_sha256=None,
    )
    assert again == render_id


def test_render_id_changes_when_repose_search_sha_changes(plan) -> None:
    """Two different repose searches must yield two different render_ids.

    Otherwise a caller could swap the search SHA between calling
    build_reposed_plan and the render step, and the render_id would
    not detect the substitution.
    """
    base = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        repose_search_sha256="d" * 64,
    )
    swapped = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        repose_search_sha256="e" * 64,
    )
    assert base != swapped


def test_journal_sha256_covers_the_recorded_durations(plan, render_id) -> None:
    """journal 自摘要【包含】耗时 —— 它记录的是这一次真的跑了多久。

    两份 journal 【只有耗时不同】, 其余字节完全相同 —— 摘要必须随之改变。
    (变异 J8 暴露: 若同时改 state/error, 摘要会因别的字段而变, 这条规则
    就测不到了。)
    """
    journal = _journal(plan, render_id, camera_ids=("camera-ground-route-001",))
    assert journal.journal_sha256 == compute_journal_sha256(journal)

    def verified_with(seconds: float):
        frame = journal.frames[0].model_copy(
            update={
                "state": "verified",
                "wall_clock_seconds": seconds,
                "artifacts": _six_artifacts(),
            }
        )
        return journal.model_copy(update={"frames": (frame,)})

    fast, slow = verified_with(11.5), verified_with(12.5)
    assert fast.model_dump(mode="json")["frames"][0]["state"] == "verified"
    assert compute_journal_sha256(fast) != compute_journal_sha256(slow)


def test_journal_bytes_are_canonical(plan, render_id) -> None:
    journal = _journal(plan, render_id, camera_ids=("camera-ground-route-001",))
    assert canonical_production_journal_bytes(journal).endswith(b"\n")


def test_model_copy_bypasses_validation_so_transitions_must_revalidate(
    plan, render_id
) -> None:
    """pydantic 的 model_copy(update=) 【不校验】—— 状态机必须靠重新校验兜住。"""
    journal = _journal(plan, render_id, camera_ids=("camera-ground-route-001",))
    # 用 model_copy 可以造出 'verified 但没耗时没产物' 的非法帧, 不报错
    smuggled = journal.model_copy(
        update={"frames": (journal.frames[0].model_copy(update={"state": "verified"}),)}
    )
    assert smuggled.frames[0].wall_clock_seconds is None  # 非法却存在
    # 重新校验必须把它拦下 (先撞上六文件契约, 两条都是状态机在起作用)
    with pytest.raises(ValidationError, match="six-file contract"):
        revalidate_journal(smuggled)


def test_transition_frame_revalidates_and_refreshes_the_digest(plan, render_id) -> None:
    journal = _journal(plan, render_id, camera_ids=("camera-ground-route-001",))
    moved = transition_frame(
        journal,
        "camera-ground-route-001",
        state="verified",
        wall_clock_seconds=11.5,
        artifacts=_six_artifacts(),
    )
    assert moved.frames[0].state == "verified"
    assert moved.frames[0].wall_clock_seconds == 11.5
    assert moved.journal_sha256 == compute_journal_sha256(moved)
    assert moved.journal_sha256 != journal.journal_sha256

    with pytest.raises(ValidationError, match="six-file contract"):
        transition_frame(journal, "camera-ground-route-001", state="verified")

    # 只补耗时、仍不给产物 -> 必须撞上耗时这条规则
    with pytest.raises(ValidationError, match="wall-clock"):
        transition_frame(
            journal,
            "camera-ground-route-001",
            state="verified",
            artifacts=_six_artifacts(),
        )


def test_transition_frame_rejects_unknown_camera(plan, render_id) -> None:
    journal = _journal(plan, render_id, camera_ids=("camera-ground-route-001",))
    with pytest.raises(ProductionProfileError, match="not in this journal"):
        transition_frame(journal, "camera-ground-route-099", state="rendering")


# --------------------------------------------------------------------------
# 铁律 4: 只补未验证帧 / 子集
# --------------------------------------------------------------------------


def test_only_unverified_frames_are_rerendered(plan, render_id) -> None:
    journal = _journal(
        plan, render_id, camera_ids=("camera-ground-route-001", "camera-ground-route-002")
    )
    verified = journal.frames[0].model_copy(
        update={
            "state": "verified",
            "wall_clock_seconds": 11.5,
            "artifacts": _six_artifacts(),
        }
    )
    resumed = journal.model_copy(update={"frames": (verified, journal.frames[1])})
    assert frames_needing_render(resumed) == ("camera-ground-route-002",)


def test_journal_rejects_camera_ids_outside_the_plan(plan, render_id) -> None:
    with pytest.raises(ProductionProfileError, match="not in the production plan"):
        _journal(plan, render_id, camera_ids=("camera-outer-001",))


def test_journal_subset_matches_a_batch_slice(plan, render_id) -> None:
    from pipeline.synthetic_village.production_profile import production_batch_slice

    batch = production_batch_slice(plan, batch_index=0, batch_count=8)
    journal = _journal(plan, render_id, camera_ids=batch)
    assert tuple(f.camera_id for f in journal.frames) == batch


# --------------------------------------------------------------------------
# 铁律 6: 外推必须自带"这是外推不是承诺"
# --------------------------------------------------------------------------


def test_extrapolation_refuses_without_measurements(plan, render_id) -> None:
    journal = _journal(plan, render_id, camera_ids=("camera-ground-route-001",))
    with pytest.raises(ProductionProfileError, match="at least one measured"):
        extrapolate_total_seconds(journal, target_frame_count=180)


def test_extrapolation_carries_its_disclaimer(plan, render_id) -> None:
    journal = _journal(plan, render_id, camera_ids=("camera-ground-route-001",))
    verified = journal.frames[0].model_copy(
        update={
            "state": "verified",
            "wall_clock_seconds": 12.0,
            "artifacts": _six_artifacts(),
        }
    )
    measured = journal.model_copy(update={"frames": (verified,)})
    report = extrapolate_total_seconds(measured, target_frame_count=180)
    assert report["extrapolated_total_seconds"] == 12.0 * 180
    assert report["measured_frame_count"] == 1
    assert "extrapolation-not-a-promise" in report["disclaimer"]


# --------------------------------------------------------------------------
# req 7: 六文件契约 —— 必须与 canary 【同等强度】(逐 (kind, path) 元组比对)
#
# 只数 len(artifacts)==6 是相对既有契约的【实质降级】: 六个产物可以全是 rgb,
# 也可以是【别的相机】的产物。后果不是理论上的 —— req 4/5 的像素审计要读
# instance/semantic/depth 三层, 只数个数会让审计读到别的相机的掩码, 并把它
# 当成本机位的覆盖证据。
# --------------------------------------------------------------------------


def _frame(**overrides: object) -> ProductionFrameRecord:
    payload: dict[str, object] = {
        "camera_id": "camera-ground-route-001",
        "state": "verified",
        "artifacts": _six_artifacts(),
        "wall_clock_seconds": 11.5,
        "timeout_limit_seconds": DEFAULT_RENDER_TIMEOUT_SECONDS,
    }
    payload.update(overrides)
    return ProductionFrameRecord(**payload)


def test_verified_frame_rejects_six_artifacts_of_the_same_kind() -> None:
    """六个产物【全是 rgb】曾经被接受 —— depth/normal/instance/semantic/
    camera-metadata 全部缺失, 而 journal 照样落成 verified。"""

    six_rgb = tuple(
        ProductionArtifactRecord(
            kind="rgb",
            path=f"rgb/camera-ground-route-001-{index}.png",
            sha256="b" * 64,
            size_bytes=10,
        )
        for index in range(6)
    )
    with pytest.raises(ValidationError, match="six-file contract"):
        _frame(artifacts=six_rgb)


def test_verified_frame_rejects_a_missing_kind_even_when_the_count_is_six() -> None:
    """少一种 kind、多一个别的 —— 个数仍是 6, 但契约必须拒绝。"""

    artifacts = list(_six_artifacts())
    artifacts[3] = ProductionArtifactRecord(
        kind="rgb",
        path="rgb/camera-ground-route-001.png",
        sha256="b" * 64,
        size_bytes=10,
    )
    assert len(artifacts) == 6
    kinds = {item.kind for item in artifacts}
    assert "instance-mask" not in kinds
    with pytest.raises(ValidationError, match="six-file contract"):
        _frame(artifacts=tuple(artifacts))


def test_verified_frame_rejects_another_cameras_artifacts() -> None:
    """path 必须绑定到 camera_id。

    渲染器把 camera B 的产物写进 camera A 的帧, 以前【毫无约束】—— 覆盖审计
    会把 B 的掩码当成 A 机位的覆盖证据。
    """

    with pytest.raises(ValidationError, match="six-file contract"):
        _frame(artifacts=_six_artifacts("camera-ground-route-002"))


def test_production_six_file_contract_is_as_strong_as_the_canary_one() -> None:
    """与 canary 同等强度: 逐 (kind, path) 元组比对, 不是数个数。

    canary 对同一情形抛 'render frame artifacts are not the exact six-file
    contract' —— 生产档不得比它弱。
    """

    from pipeline.synthetic_village.canary import _expected_render_artifacts

    canary_kinds = tuple(kind for kind, _path in _expected_render_artifacts("camera-outer-001"))
    production_kinds = tuple(
        kind for kind, _path in expected_production_artifacts("camera-ground-route-001")
    )
    assert production_kinds == canary_kinds

    # 目录布局也必须一致 —— 覆盖审计按 renders/<path> 读盘
    canary_dirs = tuple(
        path.split("/")[0] for _kind, path in _expected_render_artifacts("camera-outer-001")
    )
    production_dirs = tuple(
        path.split("/")[0]
        for _kind, path in expected_production_artifacts("camera-ground-route-001")
    )
    assert production_dirs == canary_dirs


def test_expected_artifacts_bind_every_path_to_its_camera() -> None:
    for camera_id in ("camera-ground-route-001", "camera-audit-overview-012"):
        for _kind, path in expected_production_artifacts(camera_id):
            assert camera_id in path, path


# --------------------------------------------------------------------------
# 帧状态机: 5 道门是【唯一防线】(实测无 pydantic 兜底), 以前零测试
# --------------------------------------------------------------------------


def test_verified_frame_cannot_carry_zero_artifacts() -> None:
    """一个只落了 0 个产物就崩溃的帧, 绝不允许标成 verified ——
    否则 frames_needing_render 不会重跑它, 覆盖审计把它当完整帧。"""

    with pytest.raises(ValidationError, match="six-file contract"):
        _frame(artifacts=())


def test_verified_frame_cannot_carry_an_error() -> None:
    with pytest.raises(ValidationError, match="must not carry an error"):
        _frame(error="blender exited non-zero")


def test_failed_frame_must_carry_an_error() -> None:
    with pytest.raises(ValidationError, match="failed frame must carry an error"):
        _frame(state="failed", artifacts=(), error=None, wall_clock_seconds=3.0)


def test_failed_frame_must_not_carry_artifacts() -> None:
    with pytest.raises(ValidationError, match="must not carry artifacts"):
        _frame(state="failed", error="blender crashed", wall_clock_seconds=3.0)


def test_planned_frame_must_not_declare_evidence() -> None:
    with pytest.raises(ValidationError, match="must not declare evidence"):
        _frame(state="planned", wall_clock_seconds=None)


# --------------------------------------------------------------------------
# resume 语义: failed / rendering / timed-out 三种失败态以前【从未被测】
#
# 把 `!= verified` 改成 `== planned` 后全套照绿 —— 而那会让一个 failed 或
# timed-out 的帧【永远不被重跑】, 流程却认为全部渲染已完成。这正是模块
# docstring 声称要防的"静默跳过"。
# --------------------------------------------------------------------------


def test_every_unverified_state_is_rerendered_not_only_planned(plan, render_id) -> None:
    journal = _journal(
        plan,
        render_id,
        camera_ids=tuple(f"camera-ground-route-{index:03d}" for index in range(1, 6)),
    )
    planned, rendering, failed, timed_out, verified = journal.frames
    frames = (
        planned,
        rendering.model_copy(update={"state": "rendering"}),
        failed.model_copy(
            update={"state": "failed", "error": "blender crashed", "wall_clock_seconds": 3.0},
        ),
        timed_out.model_copy(
            update={
                "state": "timed-out",
                "error": "exceeded the timeout",
                "wall_clock_seconds": float(DEFAULT_RENDER_TIMEOUT_SECONDS + 1),
            },
        ),
        verified.model_copy(
            update={
                "state": "verified",
                "wall_clock_seconds": 11.5,
                "artifacts": _six_artifacts("camera-ground-route-005"),
            },
        ),
    )
    resumed = revalidate_journal(journal.model_copy(update={"frames": frames}))
    assert frames_needing_render(resumed) == (
        "camera-ground-route-001",
        "camera-ground-route-002",
        "camera-ground-route-003",
        "camera-ground-route-004",
    )
    # verified 是唯一【不】重跑的状态
    assert "camera-ground-route-005" not in frames_needing_render(resumed)


# --------------------------------------------------------------------------- #
# environment_module_build_report_sha256 可选绑定 (Task 5 §3 caller 预留)
# --------------------------------------------------------------------------- #
# 与 repose_search_sha256 同样是可选绑定键: 当 §3 caller 把 175-root
# EnvironmentModuleBuildReport 的实测 SHA 传进来时, render_id 自动内容绑定
# 到该 module build; 不传时既有 journal 完全不受影响。
#
# 严格约束 (与 repose_search_sha256 一致):
#   * 只接受 64-hex SHA-256, 非法值 fail-closed
#   * 进入 canonical identity payload, 任一位变化改变 render_id
#   * 只能绑定【实测 build report SHA】, 不能从目录名/build_id/engine 名推断


def test_render_id_ignores_environment_module_build_report_sha_when_none(
    plan,
    render_id,
) -> None:
    """未提供 environment_module_build_report_sha256 时 render_id 完全不变。

    可选绑定意味着既有 journal / 既有 caller 完全不受影响。
    """
    again = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256=None,
    )
    assert again == render_id


def test_render_id_rejects_non_hex_environment_module_build_report_sha(
    plan,
) -> None:
    """非法 SHA (非 64-hex) 必须被拒绝, 不能进入 canonical payload。

    防止 caller 把目录名 / build_id / engine 名等非 SHA 字符串当 SHA 绑定。
    """
    with pytest.raises(ProductionProfileError):
        production_render_id(
            plan,
            blender_executable_sha256=DIGEST,
            renderer_script_sha256=DIGEST,
            blend_sha256=DIGEST,
            build_report_sha256=DIGEST,
            camera_registry_sha256=production_camera_registry_digest(plan),
            environment_module_build_report_sha256="not-a-sha",
        )


def test_render_id_rejects_short_environment_module_build_report_sha(
    plan,
) -> None:
    """长度不足的 SHA 必须被拒绝。"""
    with pytest.raises(ProductionProfileError):
        production_render_id(
            plan,
            blender_executable_sha256=DIGEST,
            renderer_script_sha256=DIGEST,
            blend_sha256=DIGEST,
            build_report_sha256=DIGEST,
            camera_registry_sha256=production_camera_registry_digest(plan),
            environment_module_build_report_sha256="a" * 63,
        )


def test_render_id_changes_when_environment_module_build_report_sha_changes(
    plan,
) -> None:
    """两位不同的 module build report SHA 必须产生不同的 render_id。

    否则 caller 可以在 build 与 render 之间偷换 module build report,
    而 render_id 无法检测。
    """
    base = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256="b" * 64,
    )
    swapped = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256="c" * 64,
    )
    assert base != swapped


def test_render_id_environment_module_binding_is_deterministic_across_processes(
    plan,
) -> None:
    """相同输入必须产生相同 render_id (跨进程 / 跨调用确定性)。

    内容寻址的基本契约: 同样的输入永远得到同样的 render_id。
    """
    left = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256="d" * 64,
    )
    right = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256="d" * 64,
    )
    assert left == right


def test_render_id_binds_environment_module_and_repose_simultaneously(plan) -> None:
    """environment_module_build_report_sha256 与 repose_search_sha256 必须同时绑定。

    当 §3 caller 同时使用 repose (重建 plan) 和 module build (175-root scene)
    时, 两个 SHA 必须同时进入 canonical identity, 缺一不可。
    """
    only_module = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256="e" * 64,
        repose_search_sha256=None,
    )
    only_repose = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256=None,
        repose_search_sha256="f" * 64,
    )
    both = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256="e" * 64,
        repose_search_sha256="f" * 64,
    )
    # 两者同时绑定的 render_id 必须与只绑一个的不同
    assert both != only_module
    assert both != only_repose
    # 两个 SHA 都影响 canonical identity —— 篡改任一位必须改变 render_id。
    # 注意: 必须用合法 hex 字符 (0-9a-f), 否则 _require_64_hex_sha 会先拒绝。
    tampered_module = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256="0" * 64,
        repose_search_sha256="f" * 64,
    )
    tampered_repose = production_render_id(
        plan,
        blender_executable_sha256=DIGEST,
        renderer_script_sha256=DIGEST,
        blend_sha256=DIGEST,
        build_report_sha256=DIGEST,
        camera_registry_sha256=production_camera_registry_digest(plan),
        environment_module_build_report_sha256="e" * 64,
        repose_search_sha256="1" * 64,
    )
    assert tampered_module != both
    assert tampered_repose != both


# --------------------------------------------------------------------------- #
# 其它可选 SHA 绑定键的 fail-closed 一致性
# --------------------------------------------------------------------------- #
# `production_render_id` 是公开 API (被 production_render / production_repose /
# tests 多处导入)。即使 caller 端 Pydantic schema 已校验 64-hex, 函数自身也
# 必须 fail-closed —— 否则绕过 schema 直接调用就会让非 SHA 字符串静默进入
# canonical payload, 破坏内容寻址。
#
# 这与 `environment_module_build_report_sha256` / `post_render_policy_sha256`
# 已有的 `_require_64_hex_sha` 校验保持一致。

def test_render_id_rejects_non_hex_preflight_id(plan) -> None:
    """preflight_id 必须是 64-hex SHA-256。

    字段名表明它是 SHA-256 (production_preflight.py:229 也有 pattern 校验),
    production_render_id 自身必须独立校验, 不依赖 caller。
    """
    with pytest.raises(ProductionProfileError):
        production_render_id(
            plan,
            blender_executable_sha256=DIGEST,
            renderer_script_sha256=DIGEST,
            blend_sha256=DIGEST,
            build_report_sha256=DIGEST,
            camera_registry_sha256=production_camera_registry_digest(plan),
            preflight_id="not-a-sha",
        )


def test_render_id_rejects_non_hex_quality_policy_sha(plan) -> None:
    """quality_policy_sha256 必须是 64-hex SHA-256。"""
    with pytest.raises(ProductionProfileError):
        production_render_id(
            plan,
            blender_executable_sha256=DIGEST,
            renderer_script_sha256=DIGEST,
            blend_sha256=DIGEST,
            build_report_sha256=DIGEST,
            camera_registry_sha256=production_camera_registry_digest(plan),
            quality_policy_sha256="z" * 64,
        )


def test_render_id_rejects_non_hex_repose_search_sha(plan) -> None:
    """repose_search_sha256 必须是 64-hex SHA-256。

    与 environment_module_build_report_sha256 一致: 字段名声明为 SHA-256,
    函数自身必须 fail-closed。
    """
    with pytest.raises(ProductionProfileError):
        production_render_id(
            plan,
            blender_executable_sha256=DIGEST,
            renderer_script_sha256=DIGEST,
            blend_sha256=DIGEST,
            build_report_sha256=DIGEST,
            camera_registry_sha256=production_camera_registry_digest(plan),
            repose_search_sha256="0" * 63,
        )
