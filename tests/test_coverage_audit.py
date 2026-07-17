"""覆盖审计内核的红线测试。

这些测试锁死四件事:
1. 判据建立在【掩码像素】上, 不是 journal 声明的 instance_ids;
2. 阈值语义 (min_pixels / 比较方向 / min_cameras) 必须【显式写进报告】, 消费者可自行重算;
3. 算不出来的 (组件朝向 -> 正面/反向覆盖) 必须 fail-closed 标 unknown, 绝不用方位角冒充;
4. 审计只降不升: 报告须携带 synthetic / fidelity / trust 声明。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.coverage_audit import (
    COVERAGE_AUDIT_SCHEMA,
    FRAME_PIXEL_COUNT,
    CoverageAuditError,
    CoverageAuditReport,
    CoverageThreshold,
    audit_render_coverage,
    canonical_coverage_report_bytes,
    count_qualifying_cameras,
    write_coverage_report,
)

ROOT = Path(__file__).resolve().parents[1]
REAL_BUILD = (
    ROOT
    / ".nantai-studio/synthetic-village/hybrid-v3/work/canary"
    / "0f26388f0560b520c16feb348a7902c83de29ab531cf7c77f31d2d32ab90e004"
)

requires_real_frames = pytest.mark.skipif(
    not (REAL_BUILD / "renders/render-journal.json").is_file(),
    reason="真实 24 帧掩码不在本机, 覆盖审计的实跑断言无法执行",
)


def _threshold(min_pixels: int, min_cameras: int = 3) -> CoverageThreshold:
    return CoverageThreshold(
        min_pixels=min_pixels,
        min_cameras=min_cameras,
        comparison="pixels-greater-or-equal",
    )


# --------------------------------------------------------------------------
# 1. 纯判据核心: 像素, 且比较方向显式
# --------------------------------------------------------------------------


def test_count_qualifying_cameras_uses_pixels_not_presence() -> None:
    """出现即算 (>0px) 与像素判据必须给出不同的数 —— 这是整个内核存在的理由。"""

    observations = {"camera-outer-001": 4, "camera-outer-002": 3, "camera-outer-003": 2}
    # 出现即算: 3 个相机
    assert count_qualifying_cameras(observations, _threshold(min_pixels=1)) == 3
    # 像素判据 (>=59px): 0 个相机 —— 同一份数据, 判据不同结论完全相反
    assert count_qualifying_cameras(observations, _threshold(min_pixels=59)) == 0


def test_comparison_is_inclusive_at_the_boundary() -> None:
    """比较方向必须是 >= 且【写在报告里】。实测 instance 83 在 camera-outer-001 恰好 590px,
    >=590 与 >590 的全局结论差 1 个组件 (44 vs 43) —— 这个 ±1 必须由报告自己讲清楚。"""

    assert count_qualifying_cameras({"a": 590}, _threshold(min_pixels=590, min_cameras=1)) == 1
    assert count_qualifying_cameras({"a": 589}, _threshold(min_pixels=590, min_cameras=1)) == 0


def test_threshold_declares_derived_frame_fraction() -> None:
    threshold = _threshold(min_pixels=590)
    assert threshold.frame_pixel_count == FRAME_PIXEL_COUNT == 1024 * 576
    assert threshold.comparison == "pixels-greater-or-equal"
    assert threshold.min_frame_fraction == pytest.approx(590 / 589824)


def test_threshold_rejects_unstated_comparison() -> None:
    """绝不允许出现一个没写明比较方向的阈值。"""

    with pytest.raises(ValidationError):
        CoverageThreshold(min_pixels=590, min_cameras=3, comparison="whatever")
    # 也不允许省略它。
    with pytest.raises(ValidationError):
        CoverageThreshold(min_pixels=590, min_cameras=3)


# --------------------------------------------------------------------------
# 2. 实跑真实 24 帧: 复现判据陷阱那张表
# --------------------------------------------------------------------------


@requires_real_frames
def test_criterion_table_reproduces_on_real_frames() -> None:
    """铁律 7: 拿真实 24 帧掩码实跑。

    实测 (registry 的 126 个组件, >= 比较, >=3 相机):
      >0px -> 122, >=59px -> 100, >=590px -> 44, >=5898px -> 4
    同一份数据、同一个物理事实, 这个数在 122 和 4 之间任选。
    """

    expected = {1: 122, 59: 100, 590: 44, 5898: 4}
    for min_pixels, want in expected.items():
        result = audit_render_coverage(
            build_directory=REAL_BUILD,
            threshold=_threshold(min_pixels=min_pixels),
        )
        got = result.report.summary.components_meeting_threshold
        assert got == want, f"min_pixels={min_pixels}: 期望 {want}, 实得 {got}"
        assert result.report.summary.component_count == 126


@requires_real_frames
def test_background_pixel_value_is_never_a_component() -> None:
    """掩码里的 0 是背景, 不是组件。把它算进去会让每一行都 +1 (123/101/45/5)。"""

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=1),
    )
    instance_ids = {c.instance_id for c in result.report.components}
    assert 0 not in instance_ids
    assert instance_ids == set(range(1, 127))


@requires_real_frames
def test_report_records_mask_digests_for_recomputation() -> None:
    """铁律 5: 消费者能验证『这份报告描述的正是这批帧』。"""

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    digests = {d.camera_id: d.sha256 for d in result.report.mask_digests}
    assert len(digests) == 24
    journal = json.loads((REAL_BUILD / "renders/render-journal.json").read_text("utf-8"))
    for frame in journal["frames"]:
        mask = next(a for a in frame["artifacts"] if a["kind"] == "instance-mask")
        on_disk = hashlib.sha256((REAL_BUILD / "renders" / mask["path"]).read_bytes()).hexdigest()
        assert digests[frame["camera_id"]] == mask["sha256"] == on_disk


@requires_real_frames
def test_instance_ids_crosscheck_agrees_with_pixels() -> None:
    """instance_ids 保留作交叉校验: 它忠实但粒度不够 (实测声明集合 == 掩码实算集合)。"""

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    crosscheck = result.report.instance_ids_crosscheck
    assert crosscheck.agrees is True
    assert crosscheck.declared_only == ()
    assert crosscheck.observed_only == ()


# --------------------------------------------------------------------------
# 3. 算不出来的必须 fail-closed —— 绝不用方位角冒充正反面
# --------------------------------------------------------------------------


@requires_real_frames
def test_orientation_coverage_is_unknown_and_azimuth_never_claims_facade() -> None:
    """铁律 3: object_registry 没有组件朝向。

    非 building 类实测 yaw 全是 AABB 占位 0.0 -> 『正面』对它们无定义。
    报告可以报方位角覆盖, 但【绝不许】把它包装成正反面覆盖。
    """

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    for component in result.report.components:
        assert component.orientation_coverage == "unknown"
        assert component.orientation_unknown_reason

    # 方位角字段必须自带『我不是正反面』的语义声明
    azimuths = [c.azimuth for c in result.report.components if c.azimuth is not None]
    assert azimuths
    for azimuth in azimuths:
        assert azimuth.semantics == "camera-azimuth-around-component-center-not-facade-coverage"

    # 整份报告里不许出现任何自称 front/back/facade 覆盖的字段名
    blob = canonical_coverage_report_bytes(result.report).decode("utf-8")
    payload = json.loads(blob)
    forbidden = ("front_coverage", "back_coverage", "facade_coverage", "front_back_covered")
    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in forbidden, f"报告不得声称正反面覆盖: {key}"
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)


# --------------------------------------------------------------------------
# 4. 只降不升 + fail-closed I/O
# --------------------------------------------------------------------------


@requires_real_frames
def test_audit_elevates_no_trust() -> None:
    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    report = result.report
    assert report.schema_version == COVERAGE_AUDIT_SCHEMA
    assert report.synthetic is True
    assert report.verification_level == "L2"
    assert report.fidelity == "simplified-pbr-not-render-parity"
    assert report.trust_effect == "audit-only-no-trust-elevation"


@requires_real_frames
def test_report_writes_lf_bytes_and_is_reproducible(tmp_path: Path) -> None:
    """铁律 6: LF 写出, 证据字节可复现 (耗时不进证据摘要)。"""

    first = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    second = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    assert first.report.evidence_sha256 == second.report.evidence_sha256

    destination = tmp_path / "coverage-audit.json"
    write_coverage_report(first.report, destination)
    raw = destination.read_bytes()
    assert b"\r\n" not in raw
    assert raw == canonical_coverage_report_bytes(first.report)


@requires_real_frames
def test_duration_is_reported_but_excluded_from_evidence_digest() -> None:
    """铁律 8: 如实报耗时; 但耗时不可污染证据的可复现字节。"""

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    assert result.report.audit_duration_seconds >= 0.0
    payload = json.loads(canonical_coverage_report_bytes(result.report))
    digest = hashlib.sha256(
        canonical_coverage_report_bytes(result.report, exclude_nondeterministic=True),
    ).hexdigest()
    assert payload["evidence_sha256"] == digest == result.report.evidence_sha256


def test_missing_journal_is_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(CoverageAuditError):
        audit_render_coverage(build_directory=tmp_path, threshold=_threshold(min_pixels=590))


@requires_real_frames
def test_unverified_frame_is_fail_closed_not_silently_skipped(tmp_path: Path) -> None:
    """一帧没验证过 -> 覆盖不可审计, 必须硬失败。

    静默跳过会让『少了一个相机』的覆盖报告看起来和全量一样可信 —— 那正是
    provenance-safety 要禁止的『不假装可以又不说实际问题』。
    """

    import shutil

    from pipeline.synthetic_village.canary import (
        RenderJournal,
        canonical_render_journal_bytes,
    )

    staged = tmp_path / "build"
    shutil.copytree(REAL_BUILD, staged)
    journal_path = staged / "renders/render-journal.json"
    payload = json.loads(journal_path.read_text("utf-8"))
    # 把一帧退回 planned, 并按契约剥掉它的全部证据。
    victim = payload["frames"][0]
    victim["state"] = "planned"
    victim["artifacts"] = []
    victim["runtime_report_sha256"] = None
    victim["statistics"] = None
    victim["error"] = None
    # strict=True 下 list 不会被强制成 tuple, 必须走 JSON 校验 (与 load_render_journal 同路)。
    journal = RenderJournal.model_validate_json(json.dumps(payload))
    resealed = journal.model_copy(
        update={
            "journal_sha256": hashlib.sha256(
                canonical_render_journal_bytes(journal, exclude_sha256=True),
            ).hexdigest(),
        },
    )
    journal_path.write_bytes(canonical_render_journal_bytes(resealed))

    with pytest.raises(CoverageAuditError, match="unverified"):
        audit_render_coverage(build_directory=staged, threshold=_threshold(min_pixels=590))


@requires_real_frames
def test_tampered_mask_is_fail_closed(tmp_path: Path) -> None:
    """掩码字节与 journal 声明不符 -> 硬失败, 绝不出报告。"""

    import shutil

    staged = tmp_path / "build"
    shutil.copytree(REAL_BUILD, staged)
    victim = staged / "renders/instance/camera-outer-001.png"
    raw = bytearray(victim.read_bytes())
    raw[-1] ^= 0xFF
    victim.write_bytes(bytes(raw))
    with pytest.raises(CoverageAuditError, match="instance mask"):
        audit_render_coverage(build_directory=staged, threshold=_threshold(min_pixels=590))


# --------------------------------------------------------------------------
# 5. 信任根摘要门 —— 四道全部必须有【构造篡改输入】的测试守着
#
# 这四道在真实数据上本就匹配 (EQUIVALENT), 不构造篡改输入就【永远测不到】。
# 掩码那道 (test_tampered_mask_is_fail_closed) 早有测试; 另外三道曾经裸奔:
# 换成 `pass` 后全套照绿。
# --------------------------------------------------------------------------


def _stage(tmp_path: Path) -> Path:
    import shutil

    staged = tmp_path / "build"
    shutil.copytree(REAL_BUILD, staged)
    return staged


def _reseal_journal(staged: Path, **updates: object) -> None:
    """按 canary 契约改写 render journal 并重新自摘要 —— 篡改必须是【合法且自洽】的,
    否则测试测到的是 journal 自校验, 而不是审计的摘要门。"""

    from pipeline.synthetic_village.canary import (
        RenderJournal,
        canonical_render_journal_bytes,
    )

    path = staged / "renders/render-journal.json"
    journal = RenderJournal.model_validate_json(path.read_bytes())
    moved = journal.model_copy(update=updates)
    resealed = moved.model_copy(
        update={
            "journal_sha256": hashlib.sha256(
                canonical_render_journal_bytes(moved, exclude_sha256=True),
            ).hexdigest(),
        },
    )
    path.write_bytes(canonical_render_journal_bytes(resealed))


@requires_real_frames
def test_build_id_mismatch_between_report_and_journal_is_fail_closed(tmp_path: Path) -> None:
    """build report 与 render journal 描述不同的 build -> 硬失败。

    journal 被重新自摘要过, 所以它【自身完全自洽】—— 只有审计的 build_id
    比对门能发现这次替换。
    """

    staged = _stage(tmp_path)
    _reseal_journal(staged, build_id="0" * 64)
    with pytest.raises(CoverageAuditError, match="different builds"):
        audit_render_coverage(build_directory=staged, threshold=_threshold(min_pixels=590))


@requires_real_frames
def test_object_registry_digest_mismatch_is_fail_closed(tmp_path: Path) -> None:
    """object_registry 与被渲染场景不符 -> 硬失败。

    没有这道门, 报告里 126 个组件的 object_id/semantic_class 与掩码里的
    instance_id 可以不再是同一批物体, 而报告照出、evidence_sha256 照签。
    """

    staged = _stage(tmp_path)
    _reseal_journal(staged, object_registry_sha256="1" * 64)
    with pytest.raises(CoverageAuditError, match="object registry digest"):
        audit_render_coverage(build_directory=staged, threshold=_threshold(min_pixels=590))


@requires_real_frames
def test_tampered_glb_is_fail_closed(tmp_path: Path) -> None:
    """village-canary.glb 字节与 build report 声明不符 -> 硬失败。

    glb 是【全部组件中心】的唯一来源, 组件中心又是全部方位角的输入之一。
    """

    staged = _stage(tmp_path)
    victim = staged / "village-canary.glb"
    raw = bytearray(victim.read_bytes())
    raw[-1] ^= 0xFF
    victim.write_bytes(bytes(raw))
    with pytest.raises(CoverageAuditError, match="village-canary.glb"):
        audit_render_coverage(build_directory=staged, threshold=_threshold(min_pixels=590))


def _rewrite_glb(path: Path, mutate: object) -> None:
    """按 glTF 二进制容器契约重写 JSON chunk —— 篡改必须产出一个【合法的 glb】,
    否则测试测到的是容器解析失败, 而不是摘要门。"""

    import struct

    raw = path.read_bytes()
    json_length = struct.unpack("<I", raw[12:16])[0]
    document = json.loads(raw[20 : 20 + json_length].decode("utf-8"))
    mutate(document)
    chunk = json.dumps(document, ensure_ascii=False).encode("utf-8")
    chunk += b" " * (-len(chunk) % 4)
    binary = raw[20 + json_length :]
    header = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(chunk) + len(binary))
    path.write_bytes(header + struct.pack("<II", len(chunk), 0x4E4F534A) + chunk + binary)


def _resign_build_report(staged: Path) -> None:
    """把 build-report.json 的 glb 条目对齐磁盘上的 glb, 并保持 canonical。

    这一步【模拟的正是一次 build-report 与 glb 不同步的重建】: 报告自身完全
    合法、canonical、build_id 未变, 且与 glb 互相吻合 —— 审计现有的每一道门
    都放行。只有按 journal.build_report_sha256 校验报告字节才拦得住。
    """

    from pipeline.synthetic_village.canary import (
        BuildReport,
        canonical_build_report_bytes,
    )

    path = staged / "build-report.json"
    report = BuildReport.model_validate_json(path.read_bytes())
    glb = staged / "village-canary.glb"
    raw = glb.read_bytes()
    artifacts = tuple(
        item.model_copy(
            update={"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)},
        )
        if item.name == "village-canary.glb"
        else item
        for item in report.artifacts
    )
    resigned = report.model_copy(update={"artifacts": artifacts})
    path.write_bytes(canonical_build_report_bytes(resigned))


@requires_real_frames
def test_desynchronised_build_report_is_fail_closed(tmp_path: Path) -> None:
    """build-report.json 字节与 journal 声明的 build_report_sha256 不符 -> 硬失败。

    不需要攻击者: 一次 build-report 与 glb 不同步的重建就够。把 glb 里 126 个
    node 的 x_m 整体 +1000m, 重算 glb sha 写回 build-report 并保持 canonical ——
    此时:
      * glb 与 build-report 互相吻合 -> `_load_component_centers` 那道门放行;
      * build_id 是【build request 输入】的摘要, 不是报告内容的自摘要
        -> `report.build_id != journal.build_id` 那道门【抓不到内容篡改】;
      * journal_sha256 / object_registry_sha256 / 掩码 / camera metadata 全部未动。
    结果是每个组件的中心静默平移 1000m, 方位角全盘失真, 而报告照出。
    唯一的锚是 journal.build_report_sha256, 它必须被真的用起来。
    """

    staged = _stage(tmp_path)

    def _shift(document: dict) -> None:
        for node in document["nodes"]:
            encoded = (node.get("extras") or {}).get("nv_source_transform")
            if not encoded:
                continue
            transform = json.loads(encoded)
            transform["x_m"] = transform["x_m"] + 1000.0
            node["extras"]["nv_source_transform"] = json.dumps(transform)

    _rewrite_glb(staged / "village-canary.glb", _shift)
    _resign_build_report(staged)

    # 前提校验: 这次篡改【绕过了审计现有的每一道门】, 否则本测试测的是别的门。
    from pipeline.synthetic_village.canary import load_build_report, load_render_journal

    report = load_build_report(staged / "build-report.json")
    journal = load_render_journal(staged / "renders/render-journal.json")
    assert report.build_id == journal.build_id, "build_id 未变 —— 现有的 build_id 门放行"
    glb_raw = (staged / "village-canary.glb").read_bytes()
    declared = {item.name: item.sha256 for item in report.artifacts}
    assert declared["village-canary.glb"] == hashlib.sha256(glb_raw).hexdigest(), (
        "glb 与 build-report 互相吻合 —— 现有的 glb 门放行"
    )
    on_disk = hashlib.sha256((staged / "build-report.json").read_bytes()).hexdigest()
    assert on_disk != journal.build_report_sha256, "篡改必须真的改变 build-report 字节"

    with pytest.raises(CoverageAuditError, match="build report"):
        audit_render_coverage(build_directory=staged, threshold=_threshold(min_pixels=590))


@requires_real_frames
def test_report_records_build_report_and_glb_digests_for_recomputation() -> None:
    """报告必须带 build-report 与 glb 的摘要 —— 否则消费者【无法自行复核】
    组件中心来自哪份字节。

    与 mask_digests / camera_metadata_digests 是【逐字同一个论证】: 被 journal
    (或被 journal 锚定的 build-report) 锚定了 sha256 的输入, 必须先验字节再信
    内容, 也必须把摘要落进报告供事后复算。
    """

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    report = result.report
    journal = json.loads((REAL_BUILD / "renders/render-journal.json").read_text("utf-8"))

    on_disk = hashlib.sha256((REAL_BUILD / "build-report.json").read_bytes()).hexdigest()
    assert report.build_report_sha256 == journal["build_report_sha256"] == on_disk

    build_report = json.loads((REAL_BUILD / "build-report.json").read_text("utf-8"))
    declared = {item["name"]: item["sha256"] for item in build_report["artifacts"]}
    glb_on_disk = hashlib.sha256((REAL_BUILD / "village-canary.glb").read_bytes()).hexdigest()
    assert report.glb_sha256 == declared["village-canary.glb"] == glb_on_disk


# --------------------------------------------------------------------------
# 6. camera metadata: azimuth 数字的唯一输入, 必须先验字节再信内容
# --------------------------------------------------------------------------


@requires_real_frames
def test_tampered_camera_metadata_is_fail_closed(tmp_path: Path) -> None:
    """把相机挪到村子相反侧 -> 硬失败, 绝不出报告。

    篡改后的文件【schema 合法且是 canonical 形式】, 所以 canary 的
    _load_camera_metadata 一路放行 —— 只有比对 journal 声明的 sha256 才拦得住。
    不需要攻击者: 一个陈旧/半写入/被别的 agent 重跑覆盖的 cameras/*.json
    就足以让 azimuth 全盘失真。
    """

    from pipeline.synthetic_village.canary import (
        CameraFrameMetadata,
        canonical_camera_metadata_bytes,
    )

    staged = _stage(tmp_path)
    path = staged / "renders/cameras/camera-outer-001.json"
    metadata = CameraFrameMetadata.model_validate_json(path.read_bytes())
    flipped = tuple(
        tuple(-value if column == 3 and row < 3 else value for column, value in enumerate(line))
        for row, line in enumerate(metadata.measured_c2w_blender)
    )
    moved = metadata.model_copy(update={"measured_c2w_blender": flipped})
    tampered = canonical_camera_metadata_bytes(moved)
    assert tampered != path.read_bytes(), "篡改必须真的改变字节, 否则测试什么也没测"
    path.write_bytes(tampered)

    with pytest.raises(CoverageAuditError, match="camera metadata"):
        audit_render_coverage(build_directory=staged, threshold=_threshold(min_pixels=590))


@requires_real_frames
def test_report_records_camera_metadata_digests_for_recomputation() -> None:
    """报告必须带 camera-metadata 摘要 —— 否则消费者【事后无法发现】这次替换。

    对照 mask_digests: 掩码那条链的身份是可被消费者独立复算的, 相机这条
    同样有锚可验, 就必须同样落进报告。
    """

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    digests = {d.camera_id: d for d in result.report.camera_metadata_digests}
    assert len(digests) == 24
    journal = json.loads((REAL_BUILD / "renders/render-journal.json").read_text("utf-8"))
    for frame in journal["frames"]:
        declared = next(a for a in frame["artifacts"] if a["kind"] == "camera-metadata")
        on_disk = hashlib.sha256(
            (REAL_BUILD / "renders" / declared["path"]).read_bytes(),
        ).hexdigest()
        row = digests[frame["camera_id"]]
        assert row.sha256 == declared["sha256"] == on_disk
        assert row.path == declared["path"]


# --------------------------------------------------------------------------
# 7. 承重墙: 逐组件【原始证据】本身
#
# "消费者可脱离我们重算" 是这个内核的全部卖点, 而卖点靠的是 observations
# 这一组原始数字。它们曾经零断言: 把 pixels 换成常数 590 -> 全套照绿, 而
# 消费者按报告承诺重算会拿到 122 (真值 44)。
# --------------------------------------------------------------------------


@requires_real_frames
def test_raw_observations_carry_the_real_measured_pixels() -> None:
    """逐相机像素数必须是【实测值】, 不是常数也不是派生占位。

    实测锚点: instance 83 在 camera-outer-001 恰好 590px (这正是 >=590 与 >590
    差 1 个组件的那个边界样本), 在 camera-outer-002 是 768px。
    """

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    component = next(c for c in result.report.components if c.instance_id == 83)
    pixels = {o.camera_id: o.pixels for o in component.observations}
    assert pixels["camera-outer-001"] == 590
    assert pixels["camera-outer-002"] == 768
    assert pixels["camera-outer-003"] == 353

    # 全局: 像素数不许是常数 —— 一个恒定值能让每个断言都"看起来对"
    everything = [o.pixels for c in result.report.components for o in c.observations]
    assert len(set(everything)) > 100, "实测像素数不可能只有几个取值"


@requires_real_frames
def test_observations_keep_cameras_below_the_threshold() -> None:
    """observations 必须收录【所有看见过】的相机 (pixels>=1), 无论是否达标。

    只留达标相机会静默摧毁"换个阈值自行重算"这一能力本身 —— 而这正是
    模块 docstring 承诺的东西。实测 instance 83 有 5 台相机看见它, 其中
    2 台在 590px 下不达标, 它们【必须】留在报告里。
    """

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    component = next(c for c in result.report.components if c.instance_id == 83)
    assert len(component.observations) == 5
    assert component.qualifying_camera_count == 3
    below = [o for o in component.observations if not o.meets_threshold]
    assert [o.pixels for o in below] == [353, 496]

    # 全局: 报告里必须存在不达标的原始证据, 否则重算能力是假的
    everywhere = [o for c in result.report.components for o in c.observations]
    assert any(not o.meets_threshold for o in everywhere)


@requires_real_frames
def test_consumer_can_rederive_the_summary_from_raw_evidence_alone() -> None:
    """卖点验收: 只用报告里的 observations + 一个【新】阈值, 第三方能自己重算,
    且结果与内核在该阈值下独立实跑【逐字一致】。

    这条测试是 observations 与 summary 之间的绑定: 任何一侧造假, 两边就对不上。
    """

    published = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report

    for min_pixels in (1, 59, 200, 590, 5898):
        # 消费者侧: 只看报告字段, 不碰掩码, 不碰我们的代码路径
        rederived = sum(
            1
            for component in published.components
            if sum(1 for o in component.observations if o.pixels >= min_pixels) >= 3
        )
        # 内核侧: 在同一阈值上独立实跑
        actual = audit_render_coverage(
            build_directory=REAL_BUILD,
            threshold=_threshold(min_pixels=min_pixels),
        ).report.summary.components_meeting_threshold
        assert rederived == actual, f"min_pixels={min_pixels}: 重算 {rederived} != 实跑 {actual}"


@requires_real_frames
def test_per_component_counts_and_fractions_are_derived_from_the_observations() -> None:
    """报告的每一个派生数字都必须与它自称派生自的原始证据吻合。"""

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    report = result.report
    for component in report.components:
        assert component.observed_camera_count == len(component.observations)
        expected_qualifying = sum(
            1 for o in component.observations if o.pixels >= report.threshold.min_pixels
        )
        assert component.qualifying_camera_count == expected_qualifying
        assert component.meets_threshold == (
            expected_qualifying >= report.threshold.min_cameras
        )
        for observation in component.observations:
            assert observation.meets_threshold == (
                observation.pixels >= report.threshold.min_pixels
            )
            assert observation.frame_fraction == pytest.approx(
                observation.pixels / FRAME_PIXEL_COUNT
            )

    assert report.summary.component_count == len(report.components)
    assert report.summary.components_meeting_threshold == sum(
        1 for c in report.components if c.meets_threshold
    )
    assert report.summary.components_never_observed == sum(
        1 for c in report.components if not c.observations
    )
    assert report.summary.frames_audited == len(report.mask_digests)


@requires_real_frames
def test_report_refuses_to_publish_evidence_that_contradicts_its_own_summary() -> None:
    """结构性绑定: 一份 summary 与 observations 分叉的报告【无法被构造出来】。

    这是问题 2 的根因修复。光靠断言不够 —— 断言只覆盖它想到的字段;
    这道 validator 让两条路径在结构上不可能分叉。
    """

    report = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    payload = report.model_dump(mode="json")

    # (a) summary 与 components 分叉 -> 造不出来
    lying_summary = json.loads(json.dumps(payload))
    lying_summary["summary"]["components_meeting_threshold"] += 1
    with pytest.raises(ValidationError, match="components_meeting_threshold"):
        CoverageAuditReport.model_validate_json(json.dumps(lying_summary))

    # (b) 逐组件计数与自己的 observations 分叉 -> 造不出来
    lying_count = json.loads(json.dumps(payload))
    lying_count["components"][82]["qualifying_camera_count"] += 1
    with pytest.raises(ValidationError, match="qualifying_camera_count"):
        CoverageAuditReport.model_validate_json(json.dumps(lying_count))

    # (c) 原始像素被换成常数 -> 与派生判定对不上, 造不出来
    lying_pixels = json.loads(json.dumps(payload))
    for component in lying_pixels["components"]:
        for observation in component["observations"]:
            observation["pixels"] = 590
    with pytest.raises(ValidationError):
        CoverageAuditReport.model_validate_json(json.dumps(lying_pixels))

    # 对照: 未经改动的报告必须能 round-trip —— 否则上面三条什么也没证明
    assert CoverageAuditReport.model_validate_json(json.dumps(payload)) == report


# --------------------------------------------------------------------------
# 8. 方位角: 数值语义 + 可被第三方在【任意阈值】下重算
# --------------------------------------------------------------------------


@requires_real_frames
def test_single_qualifying_camera_reports_a_full_gap_not_zero() -> None:
    """只被一台相机看到 -> max_gap = 360 (只有一个方向看过它)。

    这个数【绝不能】是 0: 0 读起来像"零空隙/全向覆盖", 语义完全反转 ——
    恰好把最差的覆盖说成最好的。实测 instance 11 在 590px 下只有 1 台合格相机。
    """

    report = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    singles = [
        c for c in report.components if c.azimuth is not None and c.qualifying_camera_count == 1
    ]
    assert singles, "真实数据里必须存在只被一台合格相机看到的组件"
    for component in singles:
        assert len(component.azimuth.qualifying_camera_azimuths_deg) == 1
        assert component.azimuth.max_gap_deg == 360.0

    # 一个合格相机都没有 -> None (不是 0, 也不是 360)
    for component in report.components:
        if component.azimuth is not None and component.qualifying_camera_count == 0:
            assert component.azimuth.max_gap_deg is None


@requires_real_frames
def test_azimuths_come_only_from_qualifying_cameras() -> None:
    """字段名是 qualifying_camera_azimuths_deg —— 混入不达标相机与字段名直接冲突。

    实测 instance 83 在 590px 下: 5 台看见, 只有 3 台达标。
    """

    report = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    component = next(c for c in report.components if c.instance_id == 83)
    assert component.observed_camera_count == 5
    assert component.qualifying_camera_count == 3
    assert len(component.azimuth.qualifying_camera_azimuths_deg) == 3

    for item in report.components:
        if item.azimuth is not None:
            assert len(item.azimuth.qualifying_camera_azimuths_deg) == (
                item.qualifying_camera_count
            )


@requires_real_frames
def test_azimuth_is_rederivable_by_a_consumer_at_any_threshold() -> None:
    """卖点验收 (方位角侧)。

    方位角是【阈值耦合】的 (只统计当前 min_pixels 下的合格相机), 所以换阈值
    它就变。以前重算它所需的两个输入 —— 相机世界中心与组件中心 —— 报告里
    【一个都没有】, 消费者只能回来重跑我们的内核, 即必须采信 (c)。
    这条测试锁死: 只用报告字段, 第三方能在任意阈值上自己算出方位角。
    """

    published = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    centers = {row.camera_id: row.center_xy_m for row in published.camera_centers}
    assert len(centers) == 24

    target = _threshold(min_pixels=200)
    actual = audit_render_coverage(build_directory=REAL_BUILD, threshold=target).report
    actual_by_id = {c.instance_id: c for c in actual.components}

    compared = 0
    for component in published.components:
        if component.azimuth is None:
            continue
        # 消费者侧: 只看 590px 那份报告的字段, 在 200px 上自行重算
        cx, cy = component.azimuth.component_center_xy_m
        rederived = sorted(
            round(
                math.degrees(
                    math.atan2(centers[o.camera_id][1] - cy, centers[o.camera_id][0] - cx),
                )
                % 360.0,
                3,
            )
            for o in component.observations
            if o.pixels >= 200
        )
        expected = actual_by_id[component.instance_id].azimuth
        assert rederived == list(expected.qualifying_camera_azimuths_deg), component.instance_id
        compared += 1
    assert compared > 100, f"只比对了 {compared} 个组件, 覆盖不足"


# --------------------------------------------------------------------------
# 9. 表面法线跨度: req 3 唯一【会随布点变好变坏】的连续量
#
# instance_ids 那条无论怎样都报 ~98% (实测 123/126), 对"背面覆盖改善了没有"
# 完全不敏感。法线跨度敏感, 且零渲染成本 —— 现有 24 帧就能算。
# --------------------------------------------------------------------------


@requires_real_frames
def test_normal_angular_spread_reproduces_on_real_frames() -> None:
    """实测锚点 (>=590px 合格相机, 24 帧真实 normal EXR):
      60 个组件有 >=2 台合格相机 -> 跨度有定义; 其余 66 个 -> unknown。
      跨度最大的是 instance 71 -> 173.784 度; 最小的是 instance 90 -> 0.288 度。
    这两个极值必须是【实测值】—— 它们是这条判据"真的在测东西"的证据。
    """

    report = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    spreads = {
        c.instance_id: c.normal_spread.observed_normal_angular_spread_deg
        for c in report.components
        if c.normal_spread.observed_normal_angular_spread_deg is not None
    }
    assert len(spreads) == 60
    assert spreads[71] == 173.784
    assert spreads[90] == 0.288
    assert max(spreads.values()) == 173.784
    assert min(spreads.values()) == 0.288
    # 连续量: 不许是几个挡位, 否则它就是个伪装成角度的分类器
    assert len(set(spreads.values())) > 50


@requires_real_frames
def test_normal_spread_is_unknown_not_zero_below_two_cameras() -> None:
    """跨度需要 >=2 台合格相机才有定义 —— 不足就 unknown, 【绝不是 0】。

    0 读起来像"实测跨度为零/只看到了同一个面", 而真相是"没测过"。这正是
    本项目最忌讳的那种谎: 把"没做"说成一个数。
    """

    report = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    unknown = [
        c for c in report.components
        if c.normal_spread.observed_normal_angular_spread_deg is None
    ]
    assert len(unknown) == 66
    for component in unknown:
        assert component.normal_spread.qualifying_camera_normal_count < 2
        # unknown 必须【说出为什么】, 不能是无声的 None
        assert component.normal_spread.unknown_reason
    for component in report.components:
        if component.normal_spread.observed_normal_angular_spread_deg is not None:
            assert component.normal_spread.qualifying_camera_normal_count >= 2
            assert component.normal_spread.unknown_reason is None


@requires_real_frames
def test_normal_spread_is_rederivable_by_a_consumer_at_any_threshold() -> None:
    """卖点验收 (法线侧): 只用报告字段, 第三方能在【任意阈值】上自行重算跨度。

    逐相机平均法线挂在 observations 上 (与 pixels 同级, 阈值无关), 所以换阈值
    重算不必回来重跑我们的内核 —— 与 azimuth 侧同一套。
    """

    published = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report

    def _angle(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))

    for min_pixels in (1, 200, 5898):
        actual = audit_render_coverage(
            build_directory=REAL_BUILD,
            threshold=_threshold(min_pixels=min_pixels),
        ).report
        actual_by_id = {c.instance_id: c for c in actual.components}
        for component in published.components:
            # 消费者侧: 只看 590px 那份报告的 observations, 在新阈值上自行重算
            vectors = [
                o.mean_unit_normal_xyz
                for o in component.observations
                if o.pixels >= min_pixels and o.mean_unit_normal_xyz is not None
            ]
            rederived = None
            if len(vectors) >= 2:
                rederived = round(
                    max(
                        _angle(vectors[i], vectors[j])
                        for i in range(len(vectors))
                        for j in range(i + 1, len(vectors))
                    ),
                    3,
                )
            expected = actual_by_id[component.instance_id].normal_spread
            assert rederived == expected.observed_normal_angular_spread_deg, (
                f"min_pixels={min_pixels} instance={component.instance_id}"
            )


@requires_real_frames
def test_tampered_normal_is_fail_closed(tmp_path: Path) -> None:
    """normal EXR 字节与 journal 声明不符 -> 硬失败, 绝不出报告。

    法线层是【全部跨度数字的唯一输入】, 而 journal 已为每帧锚定了它的 sha256 ——
    与掩码同规格: 先验字节再信内容。

    篡改必须产出一个【合法、可解码、法线仍是单位向量】的 EXR: 把法线整体反向。
    随便翻个字节是【测不到这道门的】—— 那种文件解不开, 硬失败来自解码器,
    摘要门被拆掉测试照样绿 (变异实验实测存活)。不需要攻击者: 一份陈旧的、
    或被别的 agent 重跑覆盖的 normal/*.exr 就是这个样子。
    """

    import OpenEXR

    staged = _stage(tmp_path)
    victim = staged / "renders/normal/camera-outer-001.exr"
    before = victim.read_bytes()
    with OpenEXR.File(str(victim)) as handle:
        channels = handle.channels()
        for axis in ("X", "Y", "Z"):
            channels[axis].pixels[:] = -np.asarray(channels[axis].pixels)
        handle.write(str(victim))
    assert victim.read_bytes() != before, "篡改必须真的改变字节, 否则测试什么也没测"

    # 前提校验: 这份 EXR 【完全合法】—— 解得开、形状对、法线仍是单位向量,
    # 所以除了摘要门, 没有任何东西拦得住它。
    with OpenEXR.File(str(victim)) as handle:
        planes = [np.asarray(handle.channels()[axis].pixels) for axis in ("X", "Y", "Z")]
    lengths = np.linalg.norm(np.stack(planes, axis=-1), axis=-1)
    assert planes[0].shape == (576, 1024)
    assert np.all((lengths == 0) | (np.abs(lengths - 1.0) <= 1e-3))

    with pytest.raises(CoverageAuditError, match="does not match the journal digest"):
        audit_render_coverage(build_directory=staged, threshold=_threshold(min_pixels=590))


@requires_real_frames
def test_report_records_normal_digests_for_recomputation() -> None:
    """法线层的身份必须落进报告 —— 与 mask_digests / camera_metadata_digests 同一个论证。"""

    result = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    )
    digests = {d.camera_id: d for d in result.report.normal_digests}
    assert len(digests) == 24
    journal = json.loads((REAL_BUILD / "renders/render-journal.json").read_text("utf-8"))
    for frame in journal["frames"]:
        declared = next(a for a in frame["artifacts"] if a["kind"] == "normal")
        on_disk = hashlib.sha256(
            (REAL_BUILD / "renders" / declared["path"]).read_bytes(),
        ).hexdigest()
        row = digests[frame["camera_id"]]
        assert row.sha256 == declared["sha256"] == on_disk
        assert row.path == declared["path"]


@requires_real_frames
def test_normal_spread_never_claims_to_know_which_face_is_the_front() -> None:
    """法线跨度回答的是"【看没看到不同的面】", 不是"看到了正面和背面"。

    我们【不知道】哪个面是正面 —— object_registry 里没有朝向。字段名与语义
    标签都必须让消费者【不可能】把前者误读成后者。
    """

    report = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    for component in report.components:
        assert component.orientation_coverage == "unknown"
        assert (
            component.normal_spread.semantics
            == "observed-surface-normal-angular-spread-not-facade-identity"
        )

    payload = json.loads(canonical_coverage_report_bytes(report).decode("utf-8"))
    forbidden = (
        "front_coverage",
        "back_coverage",
        "facade_coverage",
        "front_back_covered",
        "has_front_and_back_coverage",
    )

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in forbidden, f"报告不得声称正反面覆盖: {key}"
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)


def test_orientation_unknown_reason_blames_the_missing_input_not_the_evidence() -> None:
    """归因必须正确: registry 无朝向 -> "正面/背面"无定义 (这句对);
    但"这份证据上无定义"是【错的】—— 法线层能回答"看没看到不同的面"。

    "把没做说成做不到"是本项目最忌讳的措辞之一, 所以这条归因由测试钉死。
    """

    from pipeline.synthetic_village.coverage_audit import ORIENTATION_UNKNOWN_REASON

    reason = ORIENTATION_UNKNOWN_REASON
    assert "object_registry carries no component orientation" in reason
    # 不许再声称"这份证据上无定义"
    assert "undefined on this evidence" not in reason
    # 必须指明真正的原因是【缺输入】, 并指向那条确实交付了的连续量
    assert "missing input" in reason
    assert "observed_normal_angular_spread_deg" in reason


# --------------------------------------------------------------------------
# 10. 报告落盘: 原子 + 持久化 (本仓库信任根惯例)
# --------------------------------------------------------------------------


@requires_real_frames
def test_failed_write_never_destroys_the_previous_valid_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """就地覆写会同时毁掉【旧的有效报告】并留下坏文件。

    操作者第二次跑 audit-coverage 覆写同一 --report 路径, 写到一半断电/被杀 ——
    原子替换保证读者要么看到旧的完整报告, 要么看到新的完整报告。
    """

    import os as _os

    report = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    destination = tmp_path / "coverage-audit.json"
    write_coverage_report(report, destination)
    first = destination.read_bytes()
    assert first

    def _boom(src: object, dst: object) -> None:
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(CoverageAuditError, match="durably write"):
        write_coverage_report(report, destination)

    # 旧报告必须【逐字幸存】, 且不留下任何半份文件
    assert destination.read_bytes() == first
    assert list(tmp_path.glob("*.tmp")) == []
    assert [p.name for p in tmp_path.iterdir()] == ["coverage-audit.json"]


@requires_real_frames
def test_report_write_leaves_no_temporary_files(tmp_path: Path) -> None:
    report = audit_render_coverage(
        build_directory=REAL_BUILD,
        threshold=_threshold(min_pixels=590),
    ).report
    destination = tmp_path / "coverage-audit.json"
    write_coverage_report(report, destination)
    write_coverage_report(report, destination)  # 覆写同一路径
    assert destination.read_bytes() == canonical_coverage_report_bytes(report)
    assert [p.name for p in tmp_path.iterdir()] == ["coverage-audit.json"]
