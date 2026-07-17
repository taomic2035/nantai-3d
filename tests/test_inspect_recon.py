"""重建产物解读器的契约测试。

核心断言不是"输出好看", 而是**解读器只翻译、绝不提升信任**:
manifest 说 preview-only 就必须读作不可测量; 缺字段必须读作未知 (不编造);
声称 metric 却带 passed:false 证据的自相矛盾 manifest 必须被指出并按不可信处理。
"""
from __future__ import annotations

import json

import pytest

from pipeline.recon_schema import Sim3AlignmentEvidence
from pipeline.reconstruct import _alignment_evidence_consistent
from scripts.inspect_recon import inspect

# ============ 构造 manifest 的最小脚手架 ============


def _frame(
    frame_id: str,
    units: str,
    metric_status: str,
    geo_aligned: str,
    provenance: str = "sfm",
    evidence: tuple[str, ...] = (),
) -> dict:
    return {
        "frame_id": frame_id,
        "handedness": "right",
        "axes": "enu-z-up" if geo_aligned == "aligned" else "sfm-arbitrary",
        "units": units,
        "metric_status": metric_status,
        "geo_aligned": geo_aligned,
        "provenance": provenance,
        "evidence": list(evidence),
    }


def _sim3_evidence(*, passed: bool = True, rms: float = 0.087, n: int = 5) -> str:
    """用真实生产者 (Sim3AlignmentEvidence) 造证据串, 保证解读器对齐真实字节形状。"""
    return Sim3AlignmentEvidence(
        method="umeyama-sim3",
        n_control_points=n,
        scale=1.02,
        rms_residual_m=rms,
        max_residual_m=rms * 2,
        per_point_residual_m=tuple([rms] * n),
        source_singular_values=(10.0, 8.0, 3.0),
        min_span_ratio=0.3,
        max_rms_threshold_m=0.25,
        geo_origin={"lat": 26.0, "lon": 119.0, "alt": 50.0},
        control_point_labels=tuple(f"cp{i}" for i in range(n)),
        passed=passed,
    ).to_evidence()


def _manifest(
    *,
    usability: str | None = "preview-only",
    target_frame: dict | None = None,
    pose_frame: dict | None = None,
    metric_evidence: list[str] | None = None,
    alignment_status: str = "unaligned",
    transform_chain: list[dict] | None = None,
    synthetic: bool = False,
    chunks: dict | None = None,
) -> dict:
    target = target_frame or _frame("sfm-local", "arbitrary", "arbitrary", "unaligned")
    manifest = {
        "schema_version": 2,
        "engine": "import",
        "gaussian_count": 68432,
        "bounds": {"min": [-10.0, -20.0, -1.0], "max": [30.0, 25.0, 9.0]},
        "spatial_parameters": {"frame_id": target["frame_id"], "units": target["units"]},
        "lod": {"0": "recon_lod0.ply", "1": "recon_lod1.ply", "2": "recon_lod2.ply"},
        "artifacts": {
            "full_3dgs": {"path": "recon_full.ply", "fidelity": "full-3dgs"},
            "lod": {"0": {"path": "recon_lod0.ply"}},
            **({"chunks": chunks} if chunks else {}),
        },
        "coordinate_contract": {
            "pose_frame": pose_frame or _frame("sfm-local", "arbitrary", "arbitrary", "unaligned"),
            "target_frame": target,
            "alignment_status": alignment_status,
            "metric_evidence": metric_evidence if metric_evidence is not None else [],
            "transform_chain": transform_chain or [],
        },
        "provenance": {"synthetic": synthetic},
    }
    if usability is not None:
        manifest["provenance"]["geometry_usability"] = usability
    return manifest


def _metric_aligned_manifest(metric_evidence: list[str] | None = None) -> dict:
    return _manifest(
        usability="metric-aligned",
        target_frame=_frame("world-enu", "meters", "metric", "aligned", provenance="measured"),
        alignment_status="aligned",
        metric_evidence=(
            metric_evidence if metric_evidence is not None else [_sim3_evidence(passed=True)]
        ),
        transform_chain=[{
            "transform_id": "xf-abc123",
            "source_frame": "sfm-local",
            "target_frame": "world-enu",
            "method": "control-points",
        }],
    )


def _report_text(result: dict) -> str:
    return "\n".join(result["report"])


# ============ 四种 geometry_usability ============


def test_metric_aligned_reports_measurable_with_actual_accuracy():
    """metric-aligned: 说"可测量"的同时**必须**报出实际残差, 否则用户会过度测量。"""
    result = inspect(_metric_aligned_manifest())

    assert result["measurability"]["can_measure"] is True
    assert result["measurability"]["effective_usability"] == "metric-aligned"
    assert result["measurability"]["accuracy_known"] is True
    accuracy = result["measurability"]["accuracy"]
    assert accuracy["rms_residual_m"] == pytest.approx(0.087)
    assert accuracy["n_control_points"] == 5
    assert accuracy["max_rms_threshold_m"] == pytest.approx(0.25)
    assert result["contradictions"] == []
    assert result["self_consistent"] is True
    text = _report_text(result)
    assert "0.087" in text and "可测量" in text


def test_metric_aligned_without_sim3_evidence_says_accuracy_unknown():
    """无 sim3 证据 → 精度未知; 绝不编造一个残差数字。"""
    result = inspect(_metric_aligned_manifest(metric_evidence=["survey-scale-bar"]))

    assert result["measurability"]["can_measure"] is True
    assert result["measurability"]["accuracy"] is None
    assert result["measurability"]["accuracy_known"] is False
    assert result["contradictions"] == []
    assert "未知" in _report_text(result)


def test_metric_unaligned_has_scale_but_no_geo_direction():
    result = inspect(_manifest(
        usability="metric-unaligned",
        target_frame=_frame("scaled-local", "meters", "metric", "unaligned",
                            provenance="measured"),
        alignment_status="unaligned",
        metric_evidence=["survey-scale-bar"],
    ))

    assert result["measurability"]["can_measure"] is True
    assert result["measurability"]["geo_aligned"] is False
    assert result["measurability"]["effective_usability"] == "metric-unaligned"
    assert result["contradictions"] == []
    text = _report_text(result)
    assert "尺度" in text and "地理" in text


def test_preview_only_refuses_measurement_and_offers_upgrade_path():
    result = inspect(_manifest(usability="preview-only"))

    assert result["measurability"]["can_measure"] is False
    assert result["measurability"]["accuracy"] is None
    upgrade = result["measurability"]["upgrade_path"]
    assert upgrade is not None and "控制点" in upgrade
    text = _report_text(result)
    assert "不能测量" in text


def test_preview_proxy_is_flagged_as_synthetic_not_real_reconstruction():
    result = inspect(_manifest(
        usability="preview-proxy",
        target_frame=_frame("synthetic-local", "arbitrary", "arbitrary", "unaligned",
                            provenance="synthetic"),
        synthetic=True,
    ))

    assert result["measurability"]["can_measure"] is False
    assert result["measurability"]["effective_usability"] == "preview-proxy"
    text = _report_text(result)
    assert "合成" in text and "不是真实重建" in text


# ============ 自相矛盾: 声称 metric 但证据不支持 ============


def test_metric_claim_with_failed_gate_evidence_is_reported_and_distrusted():
    """本项目修过的真实 bug: 手里的证据 passed:false 必须打败 enum 声称。"""
    manifest = _metric_aligned_manifest(metric_evidence=[_sim3_evidence(passed=False)])

    result = inspect(manifest)

    assert result["contradictions"], "声称 metric-aligned 却带 passed:false 证据, 必须指出矛盾"
    assert result["self_consistent"] is False
    # 声称原样保留 (不篡改), 但生效判定降级为不可信。
    assert result["measurability"]["declared_usability"] == "metric-aligned"
    assert result["measurability"]["effective_usability"] == "preview-only"
    assert result["measurability"]["can_measure"] is False
    text = _report_text(result)
    assert "矛盾" in text


def test_unparseable_sim3_evidence_under_metric_claim_fails_closed():
    """证据无法解析 = 无法验证 → 与生产者 _alignment_evidence_consistent 同样 fail-closed。"""
    manifest = _metric_aligned_manifest(metric_evidence=["sim3.alignment.v1={not json"])

    result = inspect(manifest)

    assert result["contradictions"]
    assert result["measurability"]["can_measure"] is False
    assert result["measurability"]["effective_usability"] == "preview-only"


def test_metric_claim_with_arbitrary_units_is_contradiction():
    """声称 metric-aligned 但 target_frame 单位是 arbitrary → 不可信。"""
    manifest = _manifest(
        usability="metric-aligned",
        target_frame=_frame("world-enu", "arbitrary", "arbitrary", "unaligned"),
        alignment_status="aligned",
        metric_evidence=["some-evidence"],
    )

    result = inspect(manifest)

    assert result["contradictions"]
    assert result["measurability"]["can_measure"] is False


def test_synthetic_geometry_claiming_metric_is_contradiction():
    manifest = _manifest(
        usability="metric-aligned",
        target_frame=_frame("world-enu", "meters", "metric", "aligned", provenance="synthetic"),
        alignment_status="aligned",
        metric_evidence=[_sim3_evidence(passed=True)],
        synthetic=True,
    )

    result = inspect(manifest)

    assert result["contradictions"]
    assert result["measurability"]["can_measure"] is False


def test_note_never_invents_a_threshold_breach_that_the_numbers_deny():
    """passed=false 但 rms 未超阈值时, 绝不能编造 "rms > 阈值" 这个没根据的因果故事。

    真实拟合器按 rms<=阈值 置 passed, 所以这种记录自身就不自洽 (八成被改过) —— 如实说
    "记录不自洽", 而不是替它编一个说得通的理由。
    """
    manifest = _manifest(usability="preview-only",
                         metric_evidence=[_sim3_evidence(passed=False, rms=0.087)])

    result = inspect(manifest)
    text = _report_text(result)

    assert "> 阈值" not in text, "rms 0.087 并不大于阈值 0.25, 不许编造门被突破"
    assert "不自洽" in text, "应指出该证据记录自身不自洽 (rms 没超却标 passed=false)"


def test_contradicted_manifest_does_not_assert_scale_is_arbitrary():
    """矛盾 → "米制无法验证"; 不等于"尺度是任意的"。后者同样是没证据的断言。"""
    manifest = _metric_aligned_manifest(metric_evidence=[_sim3_evidence(passed=False)])

    result = inspect(manifest)

    summary = result["measurability"]["summary"]
    assert "任意" not in summary, "没有证据表明尺度是任意的; 只知道米制主张无法验证"
    assert "无法验证" in summary
    assert result["measurability"]["can_measure"] is False
    # 升级路径也不该把它当成诚实的 preview-only 来劝"去做对齐"。
    assert "重新" in (result["measurability"]["upgrade_path"] or "")


def test_failed_gate_evidence_under_preview_only_is_not_a_contradiction():
    """生产者诚实地因门未过而标 preview-only —— 这是自洽的, 不该报矛盾。"""
    manifest = _manifest(usability="preview-only",
                         metric_evidence=[_sim3_evidence(passed=False)])

    result = inspect(manifest)

    assert result["contradictions"] == []
    assert result["self_consistent"] is True
    assert result["measurability"]["can_measure"] is False
    # 但仍应解释"对齐尝试过、没通过", 这是有用的诚实信息。
    assert "未通过" in _report_text(result)


# ============ 缺字段 → 未知, 不编造 ============


def test_missing_geometry_usability_reads_as_unknown_not_measurable():
    result = inspect(_manifest(usability=None))

    assert result["measurability"]["declared_usability"] is None
    assert result["measurability"]["effective_usability"] == "unknown"
    assert result["measurability"]["can_measure"] is False
    assert "未知" in _report_text(result)


def test_empty_manifest_reports_unknown_everywhere_without_crashing():
    result = inspect({})

    assert result["geometry"]["gaussian_count"] is None
    assert result["geometry"]["bounds_size"] is None
    assert result["geometry"]["units"] == "unknown"
    assert result["measurability"]["can_measure"] is False
    assert result["measurability"]["effective_usability"] == "unknown"
    assert result["coordinate_contract"]["target_frame_id"] is None
    assert result["trust"]["evidence"] == []
    assert result["trust"]["unknowns"], "缺字段必须显式列为未知项"
    assert "未知" in _report_text(result)


def test_units_are_never_assumed_to_be_meters():
    """arbitrary 单位的包围盒绝不能被读成米。"""
    result = inspect(_manifest(usability="preview-only"))

    assert result["geometry"]["units"] == "arbitrary"
    assert result["geometry"]["bounds_size"] == pytest.approx([40.0, 45.0, 10.0])
    text = _report_text(result)
    assert "任意单位" in text
    assert "40.0 米" not in text and "40 米" not in text


def test_inspect_does_not_mutate_input_manifest():
    manifest = _metric_aligned_manifest()
    before = json.dumps(manifest, sort_keys=True)

    inspect(manifest)

    assert json.dumps(manifest, sort_keys=True) == before


# ============ 坐标契约 / 变换链 讲人话 ============


def test_transform_chain_is_described_in_plain_language():
    result = inspect(_metric_aligned_manifest())

    summary = result["coordinate_contract"]["transform_summary"]
    assert "sfm-local" in summary and "world-enu" in summary and "1" in summary
    assert result["coordinate_contract"]["alignment_status"] == "aligned"


def test_empty_transform_chain_says_pose_frame_is_world_frame():
    result = inspect(_manifest(usability="preview-only"))

    assert "无变换" in result["coordinate_contract"]["transform_summary"]


def test_evidence_strings_are_listed_as_trust_sources():
    result = inspect(_metric_aligned_manifest(
        metric_evidence=["survey-scale-bar", _sim3_evidence(passed=True)]))

    assert "survey-scale-bar" in result["trust"]["evidence"]


# ============ 分块产物 ============


def test_chunks_artifact_is_reported_when_present():
    result = inspect(_manifest(usability="preview-only", chunks={
        "manifest": "chunks/chunks.json",
        "chunk_size_m": 25.0,
        "total_chunks": 12,
        "total_points": 68432,
    }))

    chunks = result["geometry"]["chunks"]
    assert chunks["total_chunks"] == 12
    assert chunks["chunk_size_m"] == pytest.approx(25.0)
    assert "12" in _report_text(result)


def test_chunks_absent_reads_as_none_not_zero():
    result = inspect(_manifest(usability="preview-only"))

    assert result["geometry"]["chunks"] is None


def test_lod_levels_are_listed():
    result = inspect(_manifest(usability="preview-only"))

    assert result["geometry"]["lod_levels"] == ["0", "1", "2"]


# ============ JSON 可序列化 / CLI ============


@pytest.mark.parametrize("usability", [
    "metric-aligned", "metric-unaligned", "preview-only", "preview-proxy", None,
])
def test_result_is_json_serializable_for_every_usability(usability):
    manifest = (
        _metric_aligned_manifest() if usability == "metric-aligned"
        else _manifest(usability=usability)
    )

    encoded = json.dumps(inspect(manifest), ensure_ascii=False)

    assert json.loads(encoded)["measurability"]["declared_usability"] == usability


def _write(tmp_path, manifest, name="recon_manifest.json"):
    path = tmp_path / name
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8", newline="\n")
    return path


def test_cli_prints_human_report_and_exits_zero(tmp_path, capsys):
    from scripts.inspect_recon import main

    path = _write(tmp_path, _metric_aligned_manifest())
    code = main([str(path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "可测量" in out and "0.087" in out


def test_cli_json_flag_emits_parseable_json(tmp_path, capsys):
    from scripts.inspect_recon import main

    path = _write(tmp_path, _metric_aligned_manifest())
    code = main([str(path), "--json"])

    assert code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["measurability"]["can_measure"] is True


def test_cli_exits_nonzero_on_self_contradictory_manifest(tmp_path, capsys):
    from scripts.inspect_recon import main

    path = _write(tmp_path, _metric_aligned_manifest(
        metric_evidence=[_sim3_evidence(passed=False)]))
    code = main([str(path)])

    assert code == 2, "矛盾 manifest 必须以非零码退出, 便于 CI 当门用"
    assert "矛盾" in capsys.readouterr().out


def test_cli_missing_file_explains_itself(tmp_path):
    from scripts.inspect_recon import main

    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path / "nope.json")])

    assert "不存在" in str(excinfo.value)


def test_cli_invalid_json_explains_itself(tmp_path):
    from scripts.inspect_recon import main

    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8", newline="\n")

    with pytest.raises(SystemExit) as excinfo:
        main([str(path)])

    assert "JSON" in str(excinfo.value)


def test_cli_rejects_non_object_manifest(tmp_path):
    from scripts.inspect_recon import main

    path = _write(tmp_path, ["not", "an", "object"])

    with pytest.raises(SystemExit) as excinfo:
        main([str(path)])

    assert "对象" in str(excinfo.value)


class TestNoRuleDriftFromProducer:
    """漂移守卫: 解读器的矛盾判定必须与【生产者】的规则始终一致。

    判据在两处存在 (reconstruct._alignment_evidence_consistent 是权威规则;
    解读器为了给出人话原因而镜像了它)。镜像的代价是漂移: 生产者改判据时, 解读器会
    悄悄给出【与产出侧不一致】的信任读数 —— 在本项目里那等于读数撒谎。
    此测锁死两者对"这份对齐证据可不可信"的判断永远相同; 谁改了规则而没同步, 这里就红。
    """

    @staticmethod
    def _evidence(passed: bool, rms: float = 0.1) -> str:
        return Sim3AlignmentEvidence(
            method="umeyama-sim3", n_control_points=5, scale=1.0,
            rms_residual_m=rms, max_residual_m=rms,
            per_point_residual_m=(rms,) * 5,
            source_singular_values=(10.0, 8.0, 5.0), min_span_ratio=0.5,
            max_rms_threshold_m=2.0, geo_origin={"lat": 26.0, "lon": 119.0, "alt": 50.0},
            control_point_labels=("a", "b", "c", "d", "e"), passed=passed,
        ).to_evidence()

    def _reader_trusts_evidence(self, metric_evidence: list[str]) -> bool:
        """解读器视角: 这份 metric-aligned 声称有没有被证据推翻。"""
        manifest = {
            "gaussian_count": 10,
            "provenance": {"geometry_usability": "metric-aligned", "synthetic": False},
            "coordinate_contract": {
                "target_frame": {"frame_id": "world-enu", "units": "meters",
                                 "metric_status": "metric", "geo_aligned": "aligned"},
                "pose_frame": {"frame_id": "sfm-local"},
                "alignment_status": "aligned",
                "metric_evidence": metric_evidence,
                "transform_chain": [],
            },
        }
        return not inspect(manifest)["contradictions"]

    @pytest.mark.parametrize("metric_evidence", [
        [_evidence.__func__(True)],                       # 门通过 → 两侧都该信
        [_evidence.__func__(False)],                      # 门未过 → 两侧都该不信
        ["sim3.alignment.v1=not-json"],                   # 无法解析 → 两侧都该不信
        [_evidence.__func__(True), _evidence.__func__(False)],  # 混杂 → 两侧都该不信
        ["survey-scale-bars:v1"],                         # 非 sim3 证据 → 两侧都不否决
    ])
    def test_reader_verdict_matches_producer_rule(self, metric_evidence):
        producer_trusts = _alignment_evidence_consistent(metric_evidence)
        assert self._reader_trusts_evidence(metric_evidence) is producer_trusts, (
            "解读器与生产者对同一份证据的信任判断分叉了 —— 规则漂移。"
            "改了 reconstruct._alignment_evidence_consistent 就必须同步 "
            "scripts/inspect_recon.py 的 _find_contradictions"
        )
