"""离群高斯剔除: 显式阈值 / 默认 dry-run / 有损变更的 provenance 记录

这些测试锁的是**护栏**, 不只是算法: 没有阈值不许剔、没有确认不许写、
写了就必须留下"丢了什么、按什么规则丢的"的可回溯记录。
"""
import json

import numpy as np
import pytest

from pipeline.gaussian_scene import GaussianScene
from pipeline.outlier_trim import (
    TRIM_MANIFEST_SUFFIX,
    OccupancyRule,
    OpacityRule,
    ScaleRule,
    evaluate_trim,
    trim_scene,
    voxel_occupancy,
)


@pytest.fixture
def clustered_scene():
    """一个稠密团 (400 点, 半径 1) + 20 个远处孤立点 —— 构造已知的离群结构。

    远处点每个独占体素 (间隔 50), 稠密团的点挤在同几个体素里 → occupancy 判据
    的真值是确定的: 稠密团 occupancy 远大于 1, 孤立点 occupancy == 1。
    """
    rng = np.random.default_rng(3)
    core = rng.normal(0, 0.3, (400, 3))
    far = np.stack([np.arange(20) * 50.0 + 100.0] * 3, axis=1)
    xyz = np.concatenate([core, far])
    n = len(xyz)
    return GaussianScene(
        xyz=xyz,
        rgb=np.full((n, 3), 0.5),
        opacity=np.full(n, 0.5),
        scale=np.full((n, 3), 0.05),
        frame_id="sfm-local",
        units="unknown",
    )


class TestVoxelOccupancy:
    def test_counts_points_sharing_a_voxel(self):
        # 3 点同体素 + 1 点独占 → [3,3,3,1]
        xyz = np.array([[0.1, 0.1, 0.1], [0.2, 0.2, 0.2], [0.3, 0.3, 0.3],
                        [99.0, 99.0, 99.0]])
        occ = voxel_occupancy(xyz, 1.0)
        assert list(occ) == [3, 3, 3, 1]

    def test_voxel_size_must_be_positive(self):
        with pytest.raises(ValueError, match="voxel_size"):
            voxel_occupancy(np.zeros((3, 3)), 0.0)


class TestThresholdsAreExplicit:
    """铁律 1: 阈值必须显式给, 没有默认值 —— 缺省不许剔除。"""

    def test_rules_have_no_default_thresholds(self):
        # 省略阈值必须是构造错误, 而不是悄悄用某个"合理"默认值
        with pytest.raises(TypeError):
            OccupancyRule()
        with pytest.raises(TypeError):
            OccupancyRule(voxel_size=5.0)   # 少一个旋钮也不行
        with pytest.raises(TypeError):
            ScaleRule()
        with pytest.raises(TypeError):
            OpacityRule()

    def test_no_rules_means_no_trim(self, clustered_scene):
        # 空规则不是"用默认判据剔一下", 而是拒绝
        with pytest.raises(ValueError, match="未指定判据"):
            evaluate_trim(clustered_scene, rules=[])

    def test_thresholds_must_be_finite_and_positive(self):
        with pytest.raises(ValueError, match="voxel_size"):
            OccupancyRule(voxel_size=0.0, min_occupancy=5)
        with pytest.raises(ValueError, match="min_occupancy"):
            OccupancyRule(voxel_size=5.0, min_occupancy=0)
        with pytest.raises(ValueError, match="max_scale"):
            ScaleRule(max_scale=float("inf"))
        with pytest.raises(ValueError, match="min_opacity"):
            OpacityRule(min_opacity=1.5)


class TestCriteria:
    def test_occupancy_keeps_dense_drops_isolated(self, clustered_scene):
        report = evaluate_trim(
            clustered_scene, rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)])
        assert report.kept_points == 400
        assert report.dropped_points == 20
        # 剩余 bounds 必须真的收缩 (原 bounds 被 20 个远点撑到 ~1000)
        assert max(report.output_bounds_extent) < 5.0

    def test_scale_rule_drops_oversized(self):
        scale = np.full((10, 3), 0.05)
        scale[0] = 30.0
        s = GaussianScene(xyz=np.zeros((10, 3)), rgb=np.full((10, 3), 0.5),
                          scale=scale)
        report = evaluate_trim(s, rules=[ScaleRule(max_scale=1.0)])
        assert report.dropped_points == 1
        assert not report.keep_mask[0]

    def test_opacity_rule_drops_faint(self):
        op = np.full(10, 0.8)
        op[3] = 0.01
        s = GaussianScene(xyz=np.zeros((10, 3)), rgb=np.full((10, 3), 0.5), opacity=op)
        report = evaluate_trim(s, rules=[OpacityRule(min_opacity=0.1)])
        assert report.dropped_points == 1
        assert not report.keep_mask[3]

    def test_multiple_rules_are_anded(self, clustered_scene):
        # 两条规则同时生效 = 交集保留 (任一条判丢即丢)
        both = evaluate_trim(clustered_scene, rules=[
            OccupancyRule(voxel_size=5.0, min_occupancy=2),
            ScaleRule(max_scale=0.01),   # 全场 scale=0.05 → 这条丢光
        ])
        assert both.kept_points == 0


class TestDryRunByDefault:
    """铁律 2: 默认 dry-run, 必须显式确认才真的写。"""

    def test_dry_run_writes_nothing(self, clustered_scene, tmp_path):
        out = tmp_path / "trimmed.ply"
        report = trim_scene(
            clustered_scene, out,
            rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)])
        assert not out.exists()
        assert not report.written
        assert report.dropped_points == 20   # 但真实取舍必须已经算出来

    def test_confirm_writes_ply_and_manifest(self, clustered_scene, tmp_path):
        out = tmp_path / "trimmed.ply"
        report = trim_scene(
            clustered_scene, out,
            rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)],
            confirm=True)
        assert out.exists()
        assert report.written
        assert len(GaussianScene.load_ply(out)) == 400

    def test_refuses_to_write_an_empty_result(self, clustered_scene, tmp_path):
        # 剔光 = 灾难性结果, fail-closed 而不是写一个 0 点的 ply
        with pytest.raises(ValueError, match="剔除后场景为空"):
            trim_scene(clustered_scene, tmp_path / "e.ply",
                       rules=[ScaleRule(max_scale=0.001)], confirm=True)

    def test_refuses_to_overwrite_existing_output(self, clustered_scene, tmp_path):
        out = tmp_path / "t.ply"
        out.write_bytes(b"existing")
        with pytest.raises(ValueError, match="已存在"):
            trim_scene(clustered_scene, out,
                       rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)],
                       confirm=True)


class TestProvenance:
    """铁律 3: 有损几何变更必须留下可回溯记录。"""

    def _manifest(self, scene, tmp_path, **kw):
        out = tmp_path / "trimmed.ply"
        report = trim_scene(
            scene, out, rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)],
            confirm=True, **kw)
        return report, json.loads(
            (out.parent / (out.name + TRIM_MANIFEST_SUFFIX)).read_text(encoding="utf-8"))

    def test_manifest_records_the_loss_and_the_rule(self, clustered_scene, tmp_path):
        _report, m = self._manifest(clustered_scene, tmp_path)
        assert m["lossy"] is True
        assert m["dropped"]["points"] == 20
        assert m["input"]["points"] == 420
        assert m["output"]["points"] == 400
        rule = m["rules"][0]
        assert rule["criterion"] == "voxel_occupancy"
        assert rule["voxel_size"] == 5.0
        assert rule["min_occupancy"] == 2

    def test_manifest_binds_to_the_actual_output_bytes(self, clustered_scene, tmp_path):
        import hashlib
        _report, m = self._manifest(clustered_scene, tmp_path)
        actual = hashlib.sha256((tmp_path / "trimmed.ply").read_bytes()).hexdigest()
        assert m["output"]["sha256"] == actual

    def test_manifest_carries_source_coordinate_contract(self, clustered_scene, tmp_path):
        _report, m = self._manifest(clustered_scene, tmp_path)
        assert m["source"]["frame_id"] == "sfm-local"
        assert m["source"]["units"] == "unknown"

    def test_trim_id_is_content_addressed_and_stable(self, clustered_scene, tmp_path):
        _r1, m1 = self._manifest(clustered_scene, tmp_path / "a")
        _r2, m2 = self._manifest(clustered_scene, tmp_path / "b")
        assert m1["trim_id"] == m2["trim_id"]
        assert m1["trim_id"].startswith("trim-")
        # 换阈值 → 换 id
        out = tmp_path / "c" / "t.ply"
        trim_scene(clustered_scene, out,
                   rules=[OccupancyRule(voxel_size=5.0, min_occupancy=3)], confirm=True)
        other = json.loads((out.parent / (out.name + TRIM_MANIFEST_SUFFIX))
                           .read_text(encoding="utf-8"))
        assert other["trim_id"] != m1["trim_id"]

    def test_manifest_is_written_with_lf(self, clustered_scene, tmp_path):
        self._manifest(clustered_scene, tmp_path)
        raw = (tmp_path / ("trimmed.ply" + TRIM_MANIFEST_SUFFIX)).read_bytes()
        assert b"\r\n" not in raw

    def test_output_scene_keeps_frame_and_transform_history(self, clustered_scene, tmp_path):
        out = tmp_path / "t.ply"
        trim_scene(clustered_scene, out,
                   rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)], confirm=True)
        loaded = GaussianScene.load_ply(out)
        assert loaded.frame_id == "sfm-local"
        assert loaded.units == "unknown"

    def test_threshold_units_follow_the_scene_not_assumed_metres(self, clustered_scene,
                                                                 tmp_path):
        # 场景 units=unknown → voxel_size=5 是"5 个未知单位", 绝不能记成 5 米
        _report, m = self._manifest(clustered_scene, tmp_path)
        assert m["threshold_units"] == "unknown"
        assert any("unknown" in w for w in m["warnings"])


class TestTrustNeverRises:
    """铁律 4: 剔除绝不提升信任等级。"""

    def test_source_usability_is_carried_verbatim(self, clustered_scene, tmp_path):
        out = tmp_path / "t.ply"
        trim_scene(clustered_scene, out,
                   rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)], confirm=True,
                   source_provenance={"geometry_usability": "preview-only"})
        m = json.loads((tmp_path / ("t.ply" + TRIM_MANIFEST_SUFFIX))
                       .read_text(encoding="utf-8"))
        assert m["source"]["geometry_usability"] == "preview-only"

    def test_absent_usability_stays_absent(self, clustered_scene, tmp_path):
        # 缺席即未知: 绝不编造一个信任等级
        out = tmp_path / "t.ply"
        trim_scene(clustered_scene, out,
                   rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)], confirm=True)
        m = json.loads((tmp_path / ("t.ply" + TRIM_MANIFEST_SUFFIX))
                       .read_text(encoding="utf-8"))
        assert "geometry_usability" not in m["source"]

    def test_trim_cannot_be_asked_to_upgrade_trust(self, clustered_scene, tmp_path):
        # 源只挣到 preview-only, 却想给剔除产物盖上 metric-aligned → 必须 fail-closed
        with pytest.raises(ValueError, match="不提升信任"):
            trim_scene(clustered_scene, tmp_path / "t.ply",
                       rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)],
                       confirm=True,
                       source_provenance={"geometry_usability": "preview-only"},
                       claimed_geometry_usability="metric-aligned")

    def test_restating_the_source_judgement_is_allowed(self, clustered_scene, tmp_path):
        # 原样复述源判定不是"提升", 是搬运 —— 允许
        report = trim_scene(clustered_scene, tmp_path / "t.ply",
                            rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)],
                            confirm=True,
                            source_provenance={"geometry_usability": "preview-only"},
                            claimed_geometry_usability="preview-only")
        assert report.written

    def test_upgrade_from_absent_source_judgement_is_refused(self, clustered_scene,
                                                             tmp_path):
        # 源没有任何判定 → 凭空声称任何等级都是无中生有
        with pytest.raises(ValueError, match="不提升信任"):
            trim_scene(clustered_scene, tmp_path / "t.ply",
                       rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)],
                       confirm=True,
                       claimed_geometry_usability="preview-only")


class TestHonestWording:
    """铁律 5: 绝不把被丢的点称为"漂浮物" —— 那是判据的结果, 不是事实。"""

    def test_manifest_never_calls_dropped_points_floaters(self, clustered_scene, tmp_path):
        out = tmp_path / "t.ply"
        trim_scene(clustered_scene, out,
                   rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)], confirm=True)
        raw = (tmp_path / ("t.ply" + TRIM_MANIFEST_SUFFIX)).read_text(encoding="utf-8")
        for claim in ("漂浮物", "floater", "noise", "噪声"):
            assert claim not in raw

    def test_report_text_attributes_the_drop_to_the_rule(self, clustered_scene):
        report = evaluate_trim(
            clustered_scene, rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)])
        text = report.describe()
        assert "漂浮物" not in text
        assert "voxel_occupancy" in text
        # 必须点明可能含真实几何
        assert "真实几何" in text

    def test_manifest_states_the_dropped_set_may_contain_real_geometry(
            self, clustered_scene, tmp_path):
        out = tmp_path / "t.ply"
        trim_scene(clustered_scene, out,
                   rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)], confirm=True)
        m = json.loads((tmp_path / ("t.ply" + TRIM_MANIFEST_SUFFIX))
                       .read_text(encoding="utf-8"))
        assert "真实几何" in m["dropped"]["interpretation"]


class TestBadCriterionIsExposed:
    """判据好不好, 让用户在自己数据上看见, 而不是靠工具作者的直觉。

    诊断用的是**中立**指标: 丢弃率 vs bounds 体积收缩。它不需要任何"哪些点是真几何"
    的标签 (那种标签我们没有, 假装有就是撒谎), 只陈述事实。判读方式:
    丢了一大堆点而 bounds 纹丝不动 → 这个判据没在剔离群点, 而是在削主体几何。
    """

    def test_useless_criterion_shows_up_as_no_bounds_shrink(self):
        # 构造: 判据丢的全是稠密主体里的点, 远处离群点一个没碰
        # → 丢弃率高 (50%) 但 bounds 体积保留 ~100% —— 报告必须让这一点可见
        rng = np.random.default_rng(5)
        core = rng.normal(0, 1.0, (200, 3))
        far = np.array([[500.0, 500.0, 500.0], [-500.0, -500.0, -500.0]])
        xyz = np.concatenate([core, far])
        op = np.full(202, 0.9)
        op[:100] = 0.05          # 只削主体, 不碰远点
        s = GaussianScene(xyz=xyz, rgb=np.full((202, 3), 0.5), opacity=op)
        report = evaluate_trim(s, rules=[OpacityRule(min_opacity=0.5)])
        assert report.dropped_points == 100
        assert report.dropped_fraction > 0.49
        # 丢了一半的点, bounds 却几乎没动 → 判据无效的证据
        assert report.bounds_volume_retained_fraction > 0.99

    def test_effective_criterion_shows_up_as_real_bounds_shrink(self, clustered_scene):
        report = evaluate_trim(
            clustered_scene, rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)])
        # 只丢 4.8% 的点, bounds 体积却塌到近乎 0 → 判据有效的证据
        assert report.dropped_fraction < 0.05
        assert report.bounds_volume_retained_fraction < 0.001


class TestLossyEditIsRecordedInPlyBytes:
    """剔除记录必须在【产物 ply 自己的字节】里, 不能只在 sidecar manifest。

    sidecar 用 sha256 绑定字节没错, 但 ply 被复制/改名/被 prepare_import 吃进去之后,
    sidecar 就掉队了 —— 下游拿到一个"看起来是完整重建、实际少了 21%"的 ply, 无从得知。
    """

    def test_trimmed_ply_carries_the_edit_in_its_own_metadata(
            self, clustered_scene, tmp_path):
        out = tmp_path / "t.ply"
        trim_scene(clustered_scene, out,
                   rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)], confirm=True)

        # 只读 ply (完全不看 sidecar), 也必须知道它被剔过
        reloaded = GaussianScene.load_ply(out)
        assert len(reloaded.lossy_edits) == 1, "只读 ply 的下游必须能得知它被剔过"
        edit = reloaded.lossy_edits[0]
        assert edit["operation"] == "outlier_trim"
        assert edit["lossy"] is True
        assert edit["points_before"] == len(clustered_scene)
        assert edit["points_after"] == len(reloaded)
        assert edit["dropped"] == len(clustered_scene) - len(reloaded)
        # 判据与阈值要能回溯, 否则"被剔过"是个无法解释的事实
        assert "voxel_occupancy" in json.dumps(edit, ensure_ascii=False)
        # 单位诚实: 阈值有量纲, 绝不假定为米
        assert edit["threshold_units"] == clustered_scene.units
        # 可回溯到完整 sidecar
        assert edit["trim_id"]

    def test_untrimmed_ply_records_nothing(self, clustered_scene, tmp_path):
        """没剔过就不该凭空多出记录 (空列表 == 没有记录)。"""
        p = tmp_path / "plain.ply"
        clustered_scene.save_ply(p, flavor="3dgs")
        assert GaussianScene.load_ply(p).lossy_edits == []

    def test_dry_run_does_not_mutate_the_input_scene(self, clustered_scene):
        """dry-run 一个字节都不写, 也绝不能污染内存里的输入场景。"""
        before = list(clustered_scene.lossy_edits)
        evaluate_trim(clustered_scene,
                      rules=[OccupancyRule(voxel_size=5.0, min_occupancy=2)])
        assert clustered_scene.lossy_edits == before
