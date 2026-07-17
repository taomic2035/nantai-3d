"""跨批次 (两次独立 SfM) 对齐: 共享影像 -> ENU 中枢, 逐条守 fail-closed 门。

主题仍是 provenance-safety。两次独立 COLMAP 重建之间的规范自由度【恰是一个 Sim3】,
所以共享影像的相机中心给出近乎精确的对应。但"拟合 RMS≈0"【不等于】对齐正确 ——
RMS 只量控制点【处】的残差, 对可辨识性失明。这里的每个测试都对应一个已实测的
fail-open: 门必须挡住它, 而不是被漂亮的 rms 骗过去。

关键: 靶标 (enu_xyz) 是从参考批 A 的已对齐位姿【派生】的, 不是物理测量。派生模式下
留出验证 / n_effective / 误差复合 三道门【强制】开启, 且靶标来源必须机器可溯。
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from pipeline.alignment import (
    AlignmentError,
    SharedNoiseCalibration,
    align_registration,
    align_to_reference,
    control_points_from_shared_images,
    fit_sfm_to_enu,
    load_shared_noise_calibration,
    merge_for_preview,
    umeyama_sim3,
)
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraIntrinsics,
    CameraPose,
    CaptureSession,
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
)

_ORIGIN = GeoAnchor(lat=30.0, lon=120.0, alt=10.0)
_INTR = CameraIntrinsics.from_fov(640, 480, 60.0)


# --------------------------------------------------------------------------
# 构造: 一个真实 ENU 世界, 两个各自任意的 SfM frame (A / B) 共享一部分影像
# --------------------------------------------------------------------------
def _rotation(axis: str, deg: float) -> np.ndarray:
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    if axis == "z":
        return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    if axis == "y":
        return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _sfm_frame(frame_id: str) -> CoordinateFrame:
    return CoordinateFrame(
        frame_id=frame_id,
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SFM,
        evidence=["colmap-joint-model"],
    )


def _enu_frame(frame_id: str = "world-enu") -> CoordinateFrame:
    return CoordinateFrame(
        frame_id=frame_id,
        handedness=Handedness.RIGHT,
        axes=AxisConvention.ENU_Z_UP,
        units=CoordinateUnits.METERS,
        metric_status=MetricStatus.METRIC,
        geo_aligned=GeoAlignment.ALIGNED,
        provenance=FrameProvenance.MEASURED,
        evidence=["surveyed"],
    )


def _world_centres(n: int = 12, seed: int = 7) -> dict[str, np.ndarray]:
    """一批在 ENU 里【非共面】散开的相机中心 (米)。"""
    rng = np.random.default_rng(seed)
    return {
        f"img_{i:03d}.jpg": rng.uniform(-20.0, 20.0, size=3) + np.array([0, 0, 5.0])
        for i in range(n)
    }


def _poses_in_frame(
    world: dict[str, np.ndarray], sim3_world_from_frame: Sim3, session: str
) -> list[CameraPose]:
    """把 ENU 中心反投到某个任意 frame: frame_xyz = S^-1(enu_xyz)。"""
    rot = sim3_world_from_frame.rotation_matrix()
    scale = sim3_world_from_frame.scale
    t = np.asarray(sim3_world_from_frame.t_xyz)
    poses = []
    for image, enu in world.items():
        local = (rot.T @ (np.asarray(enu) - t)) / scale
        poses.append(
            CameraPose(
                image=image,
                session_id=session,
                quat_wxyz=[1.0, 0.0, 0.0, 0.0],
                t_xyz=local.tolist(),
                intrinsics=_INTR,
            )
        )
    return poses


def _sim3(scale: float, rot: np.ndarray, t: list[float]) -> Sim3:
    return Sim3(
        scale=scale,
        rotation_matrix_xyz=tuple(tuple(row) for row in rot.tolist()),
        t_xyz=tuple(t),
    )


_S_A = _sim3(1.7, _rotation("z", 24.0), [5.0, -3.0, 2.0])
_S_B = _sim3(0.42, _rotation("y", 61.0) @ _rotation("z", -15.0), [-11.0, 7.0, 1.5])


def _registration(frame_id: str, poses: list[CameraPose], session: str) -> RegistrationResult:
    return RegistrationResult(
        engine="colmap",
        pose_frame=_sfm_frame(frame_id),
        alignment_status=AlignmentStatus.UNALIGNED,
        geo_origin=_ORIGIN,
        sessions=[CaptureSession(
            session_id=session, kind="photo_batch", source="nantai",
            images=[p.image for p in poses])],
        poses=poses,
    )


def _aligned_reference(
    world: dict[str, np.ndarray], *, rms_m: float = 0.0, seed: int = 3
) -> RegistrationResult:
    """批次 A: 在 sfm-local-A 里, 用【实测 ENU 控制点】对齐到 world-enu。

    ``rms_m`` > 0 时给靶标注入噪声, 用来测误差复合门。
    """
    reg = _registration("sfm-local-A", _poses_in_frame(world, _S_A, "A"), "A")
    rng = np.random.default_rng(seed)
    cps = []
    for image, enu in world.items():
        target = np.asarray(enu, dtype=float)
        if rms_m > 0:
            noise = rng.normal(size=3)
            noise *= rms_m * np.sqrt(3.0) / np.linalg.norm(noise)
            target = target + noise
        cps.append(ControlPoint(label=image, image=image, enu_xyz=tuple(target)))
    return align_registration(reg, cps, geo_origin=_ORIGIN, max_rms_m=10.0)


def _reg_b(world: dict[str, np.ndarray], images: list[str] | None = None):
    subset = world if images is None else {k: world[k] for k in images}
    return _registration("sfm-local-B", _poses_in_frame(subset, _S_B, "B"), "B")


# 一份【实测】共享相机中心噪声标定记录的非绑定字段。*_m 是米, 因为它们量在
# metric_basis 指的那次对齐所定义的米制 ENU 世界里 —— 不是 COLMAP 的任意 gauge。
# 【绑定字段】(metric_basis / reference_world_frame_id) 不在这里: 它们必须逐个测试
# 从【真实的参考批】上取, 见 _calibration_record_for。写死一个 'xf-deadbeef' 正是
# 本轮堵掉的洞 —— 那种记录声称量在一个查无此人的世界里, 却照样开了米制门。
_CALIBRATION_FIELDS = {
    "record_version": 2,
    "measured_on": "2026-07-17",
    "source": "nantai batch-01 / batch-02 real COLMAP 4.1.0",
    "n_shared_images": 24,
    "shared_centre_rms_m": 0.0015,
    "shared_centre_max_m": 0.004,
    "scene_extent_m": 45.0,
    "relative_rms": 0.0015 / 45.0,
    "residual_distance_corr": 0.184,
    "affine_rms_m": 0.0013,
}


def _calibration_record_for(ref: RegistrationResult, **overrides) -> dict:
    """一份【绑定在 ref 那次对齐上】的标定记录 dict。

    绑定字段来自 ref 本身: 记录的 *_m 声称量在 ref 的米制世界里, 那就必须指名道姓
    地指向 ref 的 pose_to_world (内容寻址的 transform_id) 与它的 world frame。
    """
    return {
        **_CALIBRATION_FIELDS,
        "reference_world_frame_id": ref.world_frame.frame_id,
        "metric_basis": f"reference-pose-to-world:{ref.pose_to_world.transform_id}",
        **overrides,
    }


def _write_calibration(path, ref: RegistrationResult, **overrides):
    path.write_text(json.dumps(_calibration_record_for(ref, **overrides)),
                    encoding="utf-8", newline="\n")
    return path


@pytest.fixture()
def calibration_for(tmp_path):
    """工厂: 产一份绑定到【调用方指定的参考批】的标定记录。"""
    counter = iter(range(1000))
    def make(ref: RegistrationResult, **overrides):
        path = tmp_path / f"colmap_shared_noise_{next(counter)}.json"
        return _write_calibration(path, ref, **overrides)
    return make


@pytest.fixture()
def calibration(calibration_for):
    """一份【实测】共享相机中心噪声标定记录 (上线前置门的钥匙)。

    绑定到最常用的那个参考批 (_aligned_reference(_world_centres(n=12)), 确定构造 ->
    transform_id 稳定)。参考批不是这一个的测试必须自己走 calibration_for。
    """
    return calibration_for(_aligned_reference(_world_centres(n=12)))


def _resolved(src, dst):
    return [(np.asarray(s, float), np.asarray(d, float), f"cp{i}")
            for i, (s, d) in enumerate(zip(src, dst, strict=True))]


# --------------------------------------------------------------------------
# 门 1: 靶标必须过 A 的 pose_to_world, 不能把 SfM 任意坐标当 enu_xyz
# --------------------------------------------------------------------------
class TestSharedImageTargets:
    def test_targets_go_through_pose_to_world_not_raw_t_xyz(self):
        world = _world_centres()
        ref = _aligned_reference(world)
        reg_b = _reg_b(world)

        cps = control_points_from_shared_images(ref, reg_b)

        assert len(cps) == len(world)
        by_label = {cp.label: cp for cp in cps}
        for image, enu in world.items():
            got = np.asarray(by_label[image].enu_xyz)
            # 靶标 == A 的【世界】中心 (米), 不是 A 的 pose_frame 原始坐标。
            assert np.allclose(got, enu, atol=1e-6)
            raw = np.asarray(
                next(p.t_xyz for p in ref.poses if p.image == image), dtype=float)
            assert not np.allclose(got, raw, atol=1e-3)
            assert by_label[image].geo is None  # 走 ENU 中枢, 不冒充 GPS 锚

    def test_only_shared_images_become_control_points(self):
        world = _world_centres()
        shared = sorted(world)[:9]
        reg_b = _reg_b(world, images=shared)
        cps = control_points_from_shared_images(_aligned_reference(world), reg_b)
        assert sorted(cp.label for cp in cps) == shared

    def test_unaligned_reference_fails_closed(self):
        world = _world_centres()
        reg_a = _registration("sfm-local-A", _poses_in_frame(world, _S_A, "A"), "A")
        with pytest.raises(AlignmentError, match="未对齐|ALIGNED"):
            control_points_from_shared_images(reg_a, _reg_b(world))

    def test_non_metric_reference_world_fails_closed(self):
        """参考批的 world_frame 若不是米制 ENU, 它的位姿就不是米 —— 不许当靶标。"""
        world = _world_centres()
        poses = _poses_in_frame(world, _S_A, "A")
        bogus_world = _sfm_frame("pseudo-world")
        reg = RegistrationResult(
            engine="colmap",
            pose_frame=_sfm_frame("sfm-local-A"),
            world_frame=bogus_world,
            alignment_status=AlignmentStatus.ALIGNED,
            pose_to_world=FrameTransform(
                source_frame="sfm-local-A", target_frame="pseudo-world",
                sim3=_S_A, method=TransformMethod.CONTROL_POINTS, evidence=()),
            geo_origin=_ORIGIN,
            sessions=[CaptureSession(session_id="A", kind="photo_batch",
                                     source="nantai", images=list(world))],
            poses=poses,
        )
        with pytest.raises(AlignmentError, match="米制|metric"):
            control_points_from_shared_images(reg, _reg_b(world))


# --------------------------------------------------------------------------
# 门 2: N=3 / 共面 —— 契约必须说实话
# --------------------------------------------------------------------------
class TestPointCountContract:
    def test_three_good_triangle_rejected_and_message_is_honest(self):
        # 一个"漂亮"的三角形: 不共线, 拟合完美 —— 但 3 点去心秩<=2, s3 恒为 0。
        src = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]])
        dst = 2.0 * src + np.array([1.0, 2.0, 3.0])
        with pytest.raises(AlignmentError) as exc:
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN)
        message = str(exc.value)
        assert ">=4" in message
        # 旧契约说 ">=3 control points" 是永不可达的死代码, 不许再这么说。
        assert ">=3 control points" not in message

    def test_four_non_coplanar_points_pass(self):
        src = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0],
                        [0.0, 10.0, 0.0], [0.0, 0.0, 10.0]])
        dst = 2.0 * src + np.array([1.0, 2.0, 3.0])
        sim3, evidence = fit_sfm_to_enu(_resolved(src, dst), _ORIGIN)
        assert evidence.passed is True
        assert np.isclose(sim3.scale, 2.0)

    def test_four_coplanar_points_still_rejected(self):
        src = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0],
                        [0.0, 10.0, 0.0], [10.0, 10.0, 0.0]])
        dst = 2.0 * src + np.array([1.0, 2.0, 3.0])
        with pytest.raises(AlignmentError, match="degenerate"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN)


# --------------------------------------------------------------------------
# 门 3: 留出验证 (载荷门) —— 挡住"fit rms 漂亮但对齐是错的"
# --------------------------------------------------------------------------
class TestHoldoutGate:
    def test_derived_targets_require_upstream_and_cluster_radius(self):
        world = _world_centres()
        src = np.array([_poses_in_frame(world, _S_B, "B")[i].t_xyz
                        for i in range(len(world))])
        dst = np.array(list(world.values()))
        with pytest.raises(AlignmentError, match="upstream"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           cluster_radius_m=0.5)
        with pytest.raises(AlignmentError, match="聚类半径|cluster"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0)

    def test_fewer_than_eight_effective_points_rejected_in_derived_mode(self):
        world = _world_centres(n=6)
        src = np.array([p.t_xyz for p in _poses_in_frame(world, _S_B, "B")])
        dst = np.array(list(world.values()))
        with pytest.raises(AlignmentError, match="n_effective"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)

    def test_holdout_catches_overfitting_that_fit_rms_hides(self, calibration):
        """载荷门的【真实】作用域: 拟合把噪声吸进 7 个自由度, fit rms 因此偏乐观。

        实测 (n=8, 各轴 sigma=0.10m): fit rms 0.1404m 而 held-out 0.2338m —— 差 1.67 倍。
        把预算设在两者之间 (0.18m), fit rms 门【放行】而留出门【拒绝】。留出点没参与
        拟合, 所以量到的是真误差。(同一现象在 3 点构型上更极端 —— fit 1.7e-4 vs
        held-out 7.0mm —— 但那个数出自一个【仓库外、不可复核】的测量, 故不作依据;
        这里的 1.67 倍是本仓库自己跑出来的。)
        """
        rng = np.random.default_rng(249)
        src = rng.uniform(-20.0, 20.0, size=(8, 3))
        dst = (1.3 * (src @ _rotation("z", 12.0).T) + np.array([2.0, 1.0, 0.5])
               + rng.normal(scale=0.10, size=(8, 3)))

        # 前提: fit rms 自己是过门的 —— 否则测的就不是留出门。
        _, fit_only = fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=0.18)
        assert fit_only.passed is True
        assert fit_only.rms_residual_m < 0.18

        # 必须【由留出门】拒绝, 且报的就是留出门。上游取 0 是刻意的: 此时误差复合门
        # 退化成 (0 + holdout), 与留出门等价 —— 若不钉死措辞, 留出门被摘掉后复合门会
        # 兜住结果, 测试照样绿而门实际没了 (变异测试 MUTANT-2 实测如此)。
        with pytest.raises(AlignmentError, match="held-out") as exc:
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=0.18,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)
        assert "复合" not in str(exc.value)

    def test_holdout_is_blind_to_collinear_degeneracy(self, calibration):
        """【实测证伪 + 已知盲区】留出验证【挡不住】共线退化, s3 门才挡得住。

        设计原本断言"共线时留出点必然爆" —— 实测【不成立】。原因是结构性的: 留出点
        与训练点【同处一个退化子空间】(都在那条线上), 绕线轴的旋转歧义【不移动线上的
        点】, 所以留出点对这个歧义同样失明。

        实测构型 (seed=3, 60m 细长航带, y/z 抖动 0.1m):
            s3/s1 = 3.10e-03 (【高于】1e-3 floor, span 门放行)
            fit rms  = 0.0268m   held-out rms = 0.0323m  (仅 1.2 倍, 两者都很漂亮)
            但距控制带 100m 处的真实误差 = 4.00m
        三个门 (fit rms / held-out / s3 floor) 【全部放行】, 而外推误差是米级。

        本测试把这个盲区【钉住】: 它断言的不是"门работает", 而是"门在这里无话可说"。
        真正的挡板只有 s3 floor 的绝对位置, 而 1e-3 这个数【是从合成实验反推的, 不是
        标定出来的】。诚实结论: 远离缝合带的几何不可用于测量。见 align_to_reference
        docstring 的"外推没有任何门守得住"。
        """
        rng = np.random.default_rng(3)
        n = 12
        src = np.column_stack([
            np.linspace(0.0, 60.0, n),
            rng.normal(scale=0.1, size=n),
            rng.normal(scale=0.1, size=n),
        ])
        rot = _rotation("z", 12.0)
        dst = (1.3 * (src @ rot.T) + np.array([2.0, 1.0, 0.5])
               + rng.normal(scale=0.02, size=(n, 3)))

        singular = np.linalg.svd(src - src.mean(axis=0), compute_uv=False)
        assert singular[2] / singular[0] > 1e-3  # span 门放行

        # 门放行了 —— 这是【记录事实】, 不是庆祝。
        sim3, evidence = fit_sfm_to_enu(
            _resolved(src, dst), _ORIGIN, max_rms_m=0.05,
            control_target_provenance="derived-from-alignment:xf-x",
            upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)
        assert evidence.passed is True
        assert evidence.holdout_rms_m < 0.05
        assert evidence.holdout_rms_m / evidence.rms_residual_m < 2.0  # 没有"必然爆"

        # 而 100m 外的真实误差是米级 —— 没有任何门看见它。
        probe = np.column_stack([np.linspace(0.0, 60.0, 20),
                                 np.full(20, 100.0), np.full(20, 50.0)])
        truth = 1.3 * (probe @ rot.T) + np.array([2.0, 1.0, 0.5])
        error = np.linalg.norm(truth - sim3.apply(probe), axis=1).mean()
        assert error > 1.0, "盲区若消失说明门变强了, 请重测并更新 honest limits"

    def test_twelve_real_shared_images_pass(self, calibration):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        aligned = align_to_reference(
            ref, _reg_b(world), max_rms_m=2.0, cluster_radius_m=0.5,
            calibration_path=calibration)
        assert aligned.alignment_status is AlignmentStatus.ALIGNED
        evidence = Sim3AlignmentEvidence.parse(aligned.pose_to_world.evidence[0])
        assert evidence.passed is True
        assert evidence.holdout_folds == 12
        assert evidence.holdout_rms_m is not None
        assert evidence.holdout_rms_m < 1e-6
        assert evidence.n_effective_control_points == 12

    def test_collinear_shared_strip_rejected_by_span_gate_not_holdout(self, calibration):
        """沿一条直线航带取共享影像 -> REJECT。挡住它的是 s3 门, 【不是】留出门。

        设计原本把这道题记在留出门名下; 实测归因错了 (见
        test_holdout_is_blind_to_collinear_degeneracy)。这里断言真实的挡板身份 ——
        错误归因会让人以后误以为放宽 s3 门还有留出门兜底, 而实际【没有】。
        """
        line = np.array([[float(i) * 3.0, 0.0, 0.0] for i in range(12)])
        dst = 2.0 * line + np.array([1.0, 1.0, 1.0])
        with pytest.raises(AlignmentError, match="degenerate") as exc:
            fit_sfm_to_enu(_resolved(line, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)
        assert "held-out" not in str(exc.value)


# --------------------------------------------------------------------------
# 门 4: 聚簇控制点高报约束强度
# --------------------------------------------------------------------------
class TestEffectiveCount:
    def test_five_clusters_of_six_rejected(self, calibration):
        base = np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.0], [0.0, 30.0, 0.0],
                         [0.0, 0.0, 30.0], [30.0, 30.0, 30.0]])
        rng = np.random.default_rng(5)
        src = np.repeat(base, 6, axis=0) + rng.normal(scale=0.01, size=(30, 3))
        dst = 2.0 * src + np.array([1.0, 2.0, 3.0])
        with pytest.raises(AlignmentError, match="n_effective"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0, cluster_radius_m=1.0)

    def test_evidence_records_both_raw_and_effective_counts(self, calibration):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        aligned = align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                                     cluster_radius_m=0.5,
                                     calibration_path=calibration)
        evidence = Sim3AlignmentEvidence.parse(aligned.pose_to_world.evidence[0])
        assert evidence.n_control_points == 12
        assert evidence.n_effective_control_points == 12


# --------------------------------------------------------------------------
# 门 5: 误差复合 —— B 的总误差 >= A 的锚定误差 + B 的拟合误差
# --------------------------------------------------------------------------
class TestErrorCompounding:
    def test_upstream_plus_holdout_gate(self):
        world = _world_centres(n=12)
        src = np.array([p.t_xyz for p in _poses_in_frame(world, _S_B, "B")])
        rng = np.random.default_rng(9)
        # B 自身留出误差 ~0.8m; A 的锚定误差 1.5m; 阈值 2.0m。各自单独都过, 和不过。
        dst = np.array(list(world.values())) + rng.normal(scale=0.8, size=(12, 3))
        with pytest.raises(AlignmentError, match="复合|compound"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=1.5, cluster_radius_m=0.5)

    def test_upstream_rms_is_read_from_reference_evidence_not_caller(
            self, calibration_for):
        world = _world_centres(n=12)
        ref = _aligned_reference(world, rms_m=1.5)
        upstream = Sim3AlignmentEvidence.parse(ref.pose_to_world.evidence[0])
        assert upstream.rms_residual_m > 1.0
        with pytest.raises(AlignmentError, match="复合|compound"):
            align_to_reference(ref, _reg_b(world), max_rms_m=1.0,
                               cluster_radius_m=0.5,
                               calibration_path=calibration_for(ref))

    def test_evidence_carries_upstream_rms(self, calibration_for):
        """B 记的上游必须是 A 的【留出】残差, 不是 A 的 fit rms。

        本测试原本断言的是 == upstream.rms_residual_m, 即把 fit rms 当上游 —— 那正是
        它要防的 fail-open: fit rms 把噪声吸进 7 个自由度故系统性偏乐观 (这里实测
        A 的 fit 0.2823m vs 留出 0.4658m, 低报 39%), 拿它当上游预算等于给 B 多松了
        0.18m 它没挣得的额度。留出门存在的全部理由就是不信 fit rms。
        """
        world = _world_centres(n=12)
        ref = _aligned_reference(world, rms_m=0.3)
        aligned = align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                                     cluster_radius_m=0.5,
                                     calibration_path=calibration_for(ref))
        evidence = Sim3AlignmentEvidence.parse(aligned.pose_to_world.evidence[0])
        upstream = Sim3AlignmentEvidence.parse(ref.pose_to_world.evidence[0])
        assert upstream.holdout_rms_m > upstream.rms_residual_m  # fit rms 偏乐观
        assert evidence.upstream_alignment_rms_m == pytest.approx(
            upstream.holdout_rms_m)
        assert evidence.upstream_alignment_rms_m > upstream.rms_residual_m


# --------------------------------------------------------------------------
# 门 6: 靶标来源可溯 —— 不许靠调用方自称
# --------------------------------------------------------------------------
class TestTargetProvenance:
    def test_provenance_names_the_reference_transform_id(self, calibration):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        aligned = align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                                     cluster_radius_m=0.5,
                                     calibration_path=calibration)
        evidence = Sim3AlignmentEvidence.parse(aligned.pose_to_world.evidence[0])
        expected = f"derived-from-alignment:{ref.pose_to_world.transform_id}"
        assert evidence.control_target_provenance == expected
        # transform_id 是内容寻址的: 任何消费者可拿 A 复核这批靶标是派生而非实测。
        assert ref.pose_to_world.transform_id in aligned.pose_to_world.evidence[0]

    def test_caller_cannot_forge_provenance_through_align_to_reference(self, calibration):
        import inspect
        params = inspect.signature(align_to_reference).parameters
        assert "control_target_provenance" not in params
        assert "upstream_alignment_rms_m" not in params

    def test_reference_without_parsable_evidence_fails_closed(self, calibration):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        stripped = ref.model_copy(update={
            "pose_to_world": FrameTransform(
                source_frame=ref.pose_to_world.source_frame,
                target_frame=ref.pose_to_world.target_frame,
                sim3=ref.pose_to_world.sim3,
                method=ref.pose_to_world.method,
                evidence=("hand-wavy-note",),
            )})
        with pytest.raises(AlignmentError, match="sim3.alignment.v1"):
            align_to_reference(stripped, _reg_b(world), max_rms_m=2.0,
                               cluster_radius_m=0.5, calibration_path=calibration)


# --------------------------------------------------------------------------
# 门 7: 绝不走 B->A (否则 align_registration 会为任意 frame 伪造米制+geo 声称)
# --------------------------------------------------------------------------
class TestEnuHubOnly:
    def test_target_frame_is_the_reference_enu_world(self, calibration):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        aligned = align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                                     cluster_radius_m=0.5,
                                     calibration_path=calibration)
        assert aligned.pose_to_world.target_frame == ref.world_frame.frame_id
        assert aligned.pose_to_world.source_frame == "sfm-local-B"
        assert aligned.world_frame.units is CoordinateUnits.METERS
        assert aligned.world_frame.metric_status is MetricStatus.METRIC
        # B 的位姿被搬进 A 的世界: 同一影像在两批里落到同一个 ENU 点。
        by_image = {p.image: p for p in aligned.poses}
        for image, enu in world.items():
            centre = aligned.pose_to_world.sim3.apply(
                np.asarray([by_image[image].t_xyz]))[0]
            assert np.allclose(centre, enu, atol=1e-6)

    def test_no_arbitrary_frame_can_be_the_world_frame_id(self, calibration):
        import inspect
        assert "world_frame_id" not in inspect.signature(align_to_reference).parameters

    def test_reference_and_b_sharing_a_frame_id_fails_closed(self, calibration):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        same = _registration("sfm-local-A", _poses_in_frame(world, _S_B, "B"), "B")
        with pytest.raises(AlignmentError, match="frame"):
            align_to_reference(ref, same, max_rms_m=2.0, cluster_radius_m=0.5,
                               calibration_path=calibration)


# --------------------------------------------------------------------------
# 门 8: 上线前置门 (阻断性) —— 没有真实 COLMAP 共享噪声实测就不许上线
# --------------------------------------------------------------------------
class TestCalibrationPrerequisite:
    def test_missing_calibration_fails_closed(self, tmp_path):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        with pytest.raises(AlignmentError, match="measure_colmap_shared_noise"):
            align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                               cluster_radius_m=0.5,
                               calibration_path=tmp_path / "nope.json")

    def test_calibration_from_too_few_shared_images_rejected(self, calibration_for):
        ref = _aligned_reference(_world_centres(n=12))
        path = calibration_for(ref, n_shared_images=3)
        with pytest.raises(AlignmentError):
            load_shared_noise_calibration(path, aligned_ref=ref)

    def test_v1_record_rejected_because_its_metres_were_arbitrary_units(self, tmp_path):
        """v1 记录的 *_m 量的是 COLMAP 任意 gauge —— 绝不许它静默当米通过。

        实测: 同一个真实 7.09cm 的批间不一致, 三种 gauge 下 v1 报 0.000677 /
        0.067697 / 6.769743 "m"。这种记录没有米制基准可言, 只能作废重测。
        """
        ref = _aligned_reference(_world_centres(n=12))
        v1 = {k: v for k, v in _calibration_record_for(ref).items()
              if k not in ("reference_world_frame_id", "metric_basis", "relative_rms")}
        v1["record_version"] = 1
        path = tmp_path / "v1.json"
        path.write_text(json.dumps(v1), encoding="utf-8", newline="\n")
        with pytest.raises(AlignmentError):
            load_shared_noise_calibration(path, aligned_ref=ref)

    def test_calibration_without_a_metric_basis_is_rejected(self, calibration_for):
        """没有米制基准的 *_m 只是三个字符的自称; fail-closed。"""
        ref = _aligned_reference(_world_centres(n=12))
        path = calibration_for(ref, metric_basis="trust-me-its-metres")
        with pytest.raises(AlignmentError, match="metric_basis"):
            load_shared_noise_calibration(path, aligned_ref=ref)

    def test_valid_calibration_loads(self, calibration):
        record = load_shared_noise_calibration(
            calibration, aligned_ref=_aligned_reference(_world_centres(n=12)))
        assert record.shared_centre_rms_m == pytest.approx(0.0015)
        assert record.n_shared_images == 24


# --------------------------------------------------------------------------
# 门 8b: 标定记录必须【指名道姓地绑在本次的参考批上】—— 名字不是证据
# --------------------------------------------------------------------------
class TestCalibrationIsBoundToTheActualReference:
    """实跑过的洗白 (本轮堵掉的洞): ``metric_basis`` 只被 ``startswith
    ('reference-pose-to-world:')`` 检查过, 而 ``load_shared_noise_calibration``
    【不解析 transform_id、不比对 reference_world_frame_id】。于是:

        手写 metric_basis='reference-pose-to-world:xf-DOES-NOT-EXIST'
             + reference_world_frame_id='world-enu'
        -> 那批任意 gauge 的数字 (shared_centre_rms_m=0.000677, scene_extent_m=0.652;
           真实是 0.0709m / 65.24m) 以 record_version=2 【载入成功】
        -> align_to_reference 放行。
        实跑输出: 'LOADED OK: 2 0.000677 0.652 | basis: reference-pose-to-world:xf-DOES-NOT-EXIST'

    而记录自己的 docstring 断言"任何消费者可拿 A 复核" —— 【没有任何消费者复核】。
    消费入口 ``align_to_reference`` 本来就拿着 A, 复核所需的一切它都有。

    **这道门挡的与挡不住的 (别混)**: 它挡【记录没绑在本次参考批上】—— 换批、换项目、
    A 重新对齐后的陈旧记录、以及上面那种凭空捏造的 transform_id。它【挡不住】手里
    握着 A 的人照着 A 算出真 transform_id 再手写假 *_m 数字 —— 那与手写一份 enu_xyz
    冒充实测同属一类, 是操作者的测量声称, 见 load_shared_noise_calibration 的诚实限制。
    """

    def test_forged_basis_naming_a_nonexistent_transform_is_rejected(
            self, calibration_for):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        path = calibration_for(
            ref, metric_basis="reference-pose-to-world:xf-DOES-NOT-EXIST")
        with pytest.raises(AlignmentError, match="metric_basis|transform_id"):
            align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                               cluster_radius_m=0.5, calibration_path=path)

    def test_calibration_bound_to_a_different_alignment_is_rejected(
            self, calibration_for):
        """记录量在【另一次对齐】的世界里 -> 它的 *_m 不描述本次参考批, 数字无意义。

        真实成因: A 用更好的控制点重新对齐了一次 (sim3 变了 -> transform_id 变了),
        而标定记录还是老的那份。老记录的米是【老的 A 的尺】量的, 拿它给新 A 开门等于
        用一把已经作废的尺做 go/no-go。
        """
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        other = _aligned_reference(world, rms_m=1.5)  # 另一次对齐 -> 另一个 transform_id
        assert other.pose_to_world.transform_id != ref.pose_to_world.transform_id
        path = calibration_for(other)
        with pytest.raises(AlignmentError, match="metric_basis|transform_id"):
            align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                               cluster_radius_m=0.5, calibration_path=path)

    def test_world_frame_id_mismatch_is_rejected(self, calibration_for):
        """reference_world_frame_id 对不上 = 记录说它量在另一个世界里。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        path = calibration_for(ref, reference_world_frame_id="some-other-world")
        with pytest.raises(AlignmentError, match="reference_world_frame_id|世界"):
            align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                               cluster_radius_m=0.5, calibration_path=path)

    def test_a_correctly_bound_record_still_opens_the_gate(self, calibration_for):
        """只降不升: 真的绑对了的记录必须照常放行, 否则这道门就是在拒真。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        aligned = align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                                     cluster_radius_m=0.5,
                                     calibration_path=calibration_for(ref))
        assert aligned.alignment_status is AlignmentStatus.ALIGNED

    def test_consumer_cannot_load_without_bringing_the_reference(self):
        """复核所需的 A 必须是【必填】—— 可选就等于下一个人会忘, 这个洞就是这么来的。"""
        import inspect
        sig = inspect.signature(load_shared_noise_calibration)
        assert "aligned_ref" in sig.parameters
        assert sig.parameters["aligned_ref"].default is inspect.Parameter.empty


# --------------------------------------------------------------------------
# 门 9: 标定脚本量的必须【真是米】—— 它是上线前置门的钥匙
# --------------------------------------------------------------------------
class TestSharedNoiseMeasurementIsMetric:
    """标定记录的 *_m 字段必须是米, 不能是 COLMAP 的任意 gauge。

    这是全仓最核心的禁忌: 绝不把任意单位叫米。该记录是【上线前置门的钥匙】——
    操作者拿它跟 max_rms_m 的物理预算比来做 go/no-go。若它随 COLMAP 的任意 gauge
    浮动, 那把钥匙本身就是伪造的。
    """

    def test_measurement_is_invariant_to_the_sfm_gauge(self):
        """同一个物理事实, 两种 COLMAP gauge -> 必须报同一个米数。

        实测过的 fail-open: 旧实现直读 pose_frame 的 t_xyz (任意单位), 同一个真实
        7.09cm 的批间不一致在三种 gauge 下报 0.000677/0.067697/6.769743 "m" —— 差
        4 个数量级。
        """
        from scripts.measure_colmap_shared_noise import measure

        world = _world_centres(n=12)
        records = []
        for gauge in (1.7, 170.0):
            # A 的 pose_frame gauge 任意变 100 倍, 但 A 已对齐到【米制 ENU 世界】。
            s_a = _sim3(gauge, _rotation("z", 24.0), [5.0, -3.0, 2.0])
            reg_a = _registration("sfm-local-A", _poses_in_frame(world, s_a, "A"), "A")
            cps = [ControlPoint(label=i, image=i, enu_xyz=tuple(e))
                   for i, e in world.items()]
            ref = align_registration(reg_a, cps, geo_origin=_ORIGIN, max_rms_m=10.0)
            records.append(measure(ref, _reg_b(world)))

        # 米是米: gauge 变 100 倍, 报出来的米数【不变】。
        assert records[0]["scene_extent_m"] == pytest.approx(
            records[1]["scene_extent_m"], rel=1e-9)
        assert records[0]["shared_centre_rms_m"] == pytest.approx(
            records[1]["shared_centre_rms_m"], abs=1e-9)
        # 且跨度确实是真实世界的米数 (world_centres 在 ±20m 里散开)。
        truth = float(np.linalg.norm(
            np.max(list(world.values()), axis=0) - np.min(list(world.values()), axis=0)))
        assert records[0]["scene_extent_m"] == pytest.approx(truth, rel=1e-6)

    def test_unaligned_reference_fails_closed(self):
        """A 未对齐 -> 它的 t_xyz 是任意单位, 量不出米。必须 fail-closed 而非上报。"""
        from scripts.measure_colmap_shared_noise import measure

        world = _world_centres(n=12)
        reg_a = _registration("sfm-local-A", _poses_in_frame(world, _S_A, "A"), "A")
        with pytest.raises(AlignmentError, match="未对齐|ALIGNED"):
            measure(reg_a, _reg_b(world))

    def test_record_pins_the_metric_basis_and_is_loadable(self):
        """记录必须自带"这批数字是在哪个米制世界里量的", 否则无法复核。"""
        from scripts.measure_colmap_shared_noise import measure

        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        record = measure(ref, _reg_b(world))
        assert record["reference_world_frame_id"] == ref.world_frame.frame_id
        assert record["metric_basis"] == (
            f"reference-pose-to-world:{ref.pose_to_world.transform_id}")
        # 尺度无关量必须【进】记录 —— 它是唯一不随 gauge 浮动的判据。
        assert "relative_rms" in record
        loaded = SharedNoiseCalibration.model_validate(
            {k: v for k, v in record.items() if not k.startswith("_")})
        assert loaded.reference_world_frame_id == ref.world_frame.frame_id


# --------------------------------------------------------------------------
# 门 10: 派生性【随靶标走】—— 洗白旁路必须在使用处被挡住
# --------------------------------------------------------------------------
class TestDerivednessTravelsWithTheTargets:
    """public 的 control_points_from_shared_images + public 的 align_registration
    曾组成一条洗白旁路: control_target_provenance 默认 None -> derived=False ->
    留出/n_effective/复合三门全跳过 -> ACCEPTED, 且产出的证据与一次【真实实测控制点
    对齐】逐字段不可区分。再把洗白的 B 当参考批喂【认可入口】align_to_reference,
    复合门就在唯一认可入口里被打穿。

    根因: 派生性在 control_points_from_shared_images 返回的那一刻就丢了 ——
    ControlPoint 不带派生标记, 靠 docstring 劝阻 != fail-closed。
    """

    def test_derived_control_points_carry_a_machine_readable_marker(self):
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        cps = control_points_from_shared_images(ref, _reg_b(world))
        expected = ref.pose_to_world.transform_id
        assert all(cp.derived_from_alignment == expected for cp in cps)
        # 标记必须活过 JSON 往返 (消费者从磁盘加载的路径)。
        assert ControlPoint.model_validate_json(
            cps[0].model_dump_json()).derived_from_alignment == expected

    def test_bypass_through_align_registration_fails_closed(self, calibration):
        """实跑过的洗白: 靶标派生自 A, 却不声明派生 -> 三门全跳过 -> ACCEPTED。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world, rms_m=1.5)
        reg_b = _reg_b(world)
        cps = control_points_from_shared_images(ref, reg_b)

        with pytest.raises(AlignmentError, match="派生|derived"):
            align_registration(reg_b, cps, geo_origin=_ORIGIN,
                               world_frame_id=ref.world_frame.frame_id,
                               max_rms_m=1.0)

    def test_bypass_cannot_launder_a_reference_for_the_sanctioned_entry(
            self, calibration):
        """关键放大: 洗白的 B 当参考批喂认可入口, C 会继承 upstream≈0 而实际继承 A 的 1.5m。

        实跑过: 'C via align_to_reference @budget 0.05m: aligned | upstream
        recorded = 6.69e-15' —— C 声称在 5cm 预算内, 而它的世界继承了 A 的 1.5m
        锚定误差。堵住旁路 = 这个放大链的第一环就不成立。
        """
        world = _world_centres(n=12)
        ref = _aligned_reference(world, rms_m=1.5)
        reg_b = _reg_b(world)
        cps = control_points_from_shared_images(ref, reg_b)
        with pytest.raises(AlignmentError):
            align_registration(reg_b, cps, geo_origin=_ORIGIN,
                               world_frame_id=ref.world_frame.frame_id,
                               max_rms_m=1.0)

    def test_declared_provenance_must_name_the_actual_source_alignment(self):
        """自报的 provenance 必须与靶标自带的标记【对得上】, 不能随便编一个。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        reg_b = _reg_b(world)
        cps = control_points_from_shared_images(ref, reg_b)
        with pytest.raises(AlignmentError, match="派生|derived"):
            align_registration(reg_b, cps, geo_origin=_ORIGIN,
                               world_frame_id=ref.world_frame.frame_id,
                               max_rms_m=2.0,
                               control_target_provenance="derived-from-alignment:xf-bogus",
                               upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)

    def test_surveyed_control_points_are_unaffected(self):
        """实测控制点不带标记 -> 老路径原样работает (不许误伤)。"""
        world = _world_centres(n=12)
        reg = _registration("sfm-local-A", _poses_in_frame(world, _S_A, "A"), "A")
        cps = [ControlPoint(label=i, image=i, enu_xyz=tuple(e))
               for i, e in world.items()]
        assert all(cp.derived_from_alignment is None for cp in cps)
        aligned = align_registration(reg, cps, geo_origin=_ORIGIN, max_rms_m=2.0)
        assert aligned.alignment_status is AlignmentStatus.ALIGNED


# --------------------------------------------------------------------------
# 门 11: 误差沿对齐链累加 (A->B->C) —— 整村漫游按定义就是多跳缝合
# --------------------------------------------------------------------------
_S_C = _sim3(2.9, _rotation("x", 37.0) @ _rotation("z", 80.0), [3.0, 12.0, -4.0])


def _reg_c(world: dict[str, np.ndarray]):
    return _registration("sfm-local-C", _poses_in_frame(world, _S_C, "C"), "C")


class TestChainedUpstreamCompounding:
    """A->B->C: B 的证据里 rms_residual_m 只是【它这一环】的拟合残差, 不含它从 A
    继承的锚定误差。若 C 只看 B 的这一环, C 就会对一个真实继承了 A 全部误差的世界
    声称"我在预算内" —— 给未知误差盖米制章。

    实测过的变异: _effective_rms_m 丢掉 upstream 项后, C ACCEPTED 且证据自报
    upstream_alignment_rms_m=0.0000, 而 52 个测试全绿 (链式分支从未被构造过 ——
    所有 align_to_reference 测试的参考批都是【非派生】的 A)。
    """

    def _chain(self, calibration_for, world):
        """A (rms 1.0m) -> B。返回的 reg_b 随后当【C 的参考批】用。

        注意每一跳各需一份【绑在那一跳的参考批上】的标定记录: 记录的 *_m 只在它指名
        的那次对齐的世界里才是米, 而 A 的世界与 B 的世界是两次不同的对齐。
        """
        ref = _aligned_reference(world, rms_m=1.0)
        upstream_a = Sim3AlignmentEvidence.parse(ref.pose_to_world.evidence[0])
        assert upstream_a.rms_residual_m > 0.5  # A 的锚定误差是真的
        reg_b = align_to_reference(ref, _reg_b(world), max_rms_m=2.0,
                                   cluster_radius_m=0.5,
                                   calibration_path=calibration_for(ref))
        return ref, reg_b, upstream_a

    def test_chained_batch_inherits_the_whole_upstream_chain(self, calibration_for):
        world = _world_centres(n=12)
        _, reg_b, upstream_a = self._chain(calibration_for, world)
        ev_b = Sim3AlignmentEvidence.parse(reg_b.pose_to_world.evidence[0])

        # C 派生自 B, 但 B 的世界继承了 A 的锚定误差 -> C 必须背上整条链。
        aligned_c = align_to_reference(reg_b, _reg_c(world), max_rms_m=2.0,
                                       cluster_radius_m=0.5,
                                       calibration_path=calibration_for(reg_b))
        ev_c = Sim3AlignmentEvidence.parse(aligned_c.pose_to_world.evidence[0])
        expected = ev_b.holdout_rms_m + ev_b.upstream_alignment_rms_m
        assert ev_c.upstream_alignment_rms_m == pytest.approx(expected)
        # 关键: C 记的上游【不是】B 这一环的拟合残差 (那个几乎是 0), 而是整条链。
        assert ev_c.upstream_alignment_rms_m > 0.5
        assert ev_b.holdout_rms_m < 1e-6

    def test_chained_upstream_is_gated_not_just_recorded(self, calibration_for):
        """C 的预算 0.5m < 从 A 继承的 ~1m -> 必须 REJECT, 且报的是复合门。"""
        world = _world_centres(n=12)
        _, reg_b, _ = self._chain(calibration_for, world)
        with pytest.raises(AlignmentError, match="复合|compound") as exc:
            align_to_reference(reg_b, _reg_c(world), max_rms_m=0.5,
                               cluster_radius_m=0.5,
                               calibration_path=calibration_for(reg_b))
        # 报的上游必须是整条链的数, 不能是 0。
        assert "上游对齐 rms 0.0000m" not in str(exc.value)


# --------------------------------------------------------------------------
# 门 12: 自报没过门的参考批不能当米制靶标源 (信任边界上对外部 JSON 的校验)
# --------------------------------------------------------------------------
class TestReferenceMustHavePassed:
    def test_reference_evidence_reporting_passed_false_is_rejected(self, calibration):
        """align_registration 自己不产出 passed=False 的证据, 但 align_to_reference
        的入参是从【外部 JSON】反序列化来的 —— 这道门正是信任边界上的校验, 不能靠
        "我们自己不会写出这种文件"来免测。
        """
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        good = Sim3AlignmentEvidence.parse(ref.pose_to_world.evidence[0])
        failed = Sim3AlignmentEvidence.model_validate(
            {**good.model_dump(mode="json"), "passed": False})
        tampered = ref.model_copy(update={
            "pose_to_world": FrameTransform(
                source_frame=ref.pose_to_world.source_frame,
                target_frame=ref.pose_to_world.target_frame,
                sim3=ref.pose_to_world.sim3,
                method=ref.pose_to_world.method,
                evidence=(failed.to_evidence(),),
            )})
        # 走真实消费者的路径: 经 JSON 往返再喂进去。
        reloaded = RegistrationResult.model_validate_json(tampered.model_dump_json())
        with pytest.raises(AlignmentError, match="passed=False"):
            align_to_reference(reloaded, _reg_b(world), max_rms_m=2.0,
                               cluster_radius_m=0.5, calibration_path=calibration)


# --------------------------------------------------------------------------
# 门 13: 留出折的退化门 —— 挡【共线】折 (真退化), 不挡【共面】折 (实测不退化)
# --------------------------------------------------------------------------
class TestHoldoutFoldDegeneracy:
    """这道门原本用 s3 (共面) 判退化, 实测【过度拒绝】一整类健康的真实采集。

    实测依据 (见 test_coplanar_train_fold_is_not_degenerate_for_a_sim3):
      - 共面【不是】Sim3 的退化: Sim3 只有 7 个自由度, 一个平面的点把它定到只剩
        【反射】这一个【离散】歧义, 而 umeyama 的 det=+1 守卫已经消掉了反射。实测
        精确共面 (s3/s1=0) 在 sigma=0 时【精确】复原变换, 出平面 30m 处误差随噪声
        线性走 (sigma=0.02 -> 0.029m; sigma=0.1 -> 0.144m), 【无放大】。
      - 共线【是】真退化: 绕线轴的旋转是【连续】歧义, 没有守卫消得掉。实测 sigma=0
        时离线 30m 处误差就已经 16.33m, 且不随噪声变 —— 纯结构性。
    故本门判的是 s2 (共线), 不是 s3 (共面)。
    """

    def _plane_plus_one(self, seed: int, sigma: float):
        """无人机定高航带 (11 点在 z=0 平面散布 40m) + 1 张离面 8m 的仰拍/塔顶点。

        这是最常见的真实采集形态之一。全集 s3/s1≈0.17 —— 是 1e-3 floor 的 170 倍,
        主 s3 门【健康放行】。但去掉那个离面点后训练折就共面了。
        """
        rng = np.random.default_rng(seed)
        src = np.vstack([
            np.column_stack([rng.uniform(-20, 20, 11), rng.uniform(-20, 20, 11),
                             np.zeros(11)]),
            np.array([[0.0, 0.0, 8.0]]),
        ])
        rot = _rotation("z", 11.5)
        dst = (1.3 * (src @ rot.T) + np.array([2.0, 1.0, 0.5])
               + rng.normal(scale=sigma, size=(12, 3)))
        return src, dst, rot

    def test_coplanar_train_fold_is_not_degenerate_for_a_sim3(self):
        """把"共面折不退化 / 共线折才退化"的实测依据钉住 —— 这是本门判 s2 的全部理由。

        直接测 ``umeyama_sim3``, 因为训练折做的就是它。(主 s3 门【故意】不放行共面
        【全集】—— 那是 HEAD 的保守政策选择, 本测试不碰; 这里测的是训练折的拟合本身
        有没有可辨识性。)

        若这条哪天不成立了 (例如反射守卫被改动), 这里必须变红, 否则 s2 门就是在
        无依据地放行共面折。
        """
        rot = _rotation("z", 17.0) @ _rotation("x", 23.0)
        probe = np.column_stack([np.linspace(-20, 20, 25), np.full(25, 10.0),
                                 np.full(25, 30.0)])
        truth_probe = 1.3 * (probe @ rot.T) + np.array([2.0, 1.0, 0.5])

        def fit_and_probe(src, sigma, seed):
            truth = 1.3 * (src @ rot.T) + np.array([2.0, 1.0, 0.5])
            dst = truth + np.random.default_rng(seed).normal(
                scale=sigma, size=src.shape)
            scale, rotation, t = umeyama_sim3(src, dst)
            pred = scale * (probe @ rotation.T) + t
            return float(np.linalg.norm(truth_probe - pred, axis=1).mean())

        rng = np.random.default_rng(1)
        coplanar = np.column_stack([rng.uniform(-20, 20, 12),
                                    rng.uniform(-20, 20, 12), np.zeros(12)])
        assert np.linalg.svd(coplanar - coplanar.mean(axis=0),
                             compute_uv=False)[2] == pytest.approx(0.0, abs=1e-12)
        # 共面 + 零噪声 -> 【精确】复原 (反射是唯一歧义, det=+1 守卫已消掉它)。
        assert fit_and_probe(coplanar, 0.0, 2) < 1e-9
        # 共面 + 噪声 -> 出平面误差随噪声【线性】走, 无放大。
        assert fit_and_probe(coplanar, 0.02, 2) < 0.1
        assert fit_and_probe(coplanar, 0.10, 2) < 0.5

        # 对照: 共线是【真】退化 —— 零噪声下误差就已经是米级, 且不随噪声变 (结构性)。
        collinear = np.column_stack([np.linspace(-20, 20, 12), np.zeros(12),
                                     np.zeros(12)])
        assert fit_and_probe(collinear, 0.0, 2) > 1.0
        assert fit_and_probe(collinear, 0.02, 2) > 1.0

    def test_collinear_train_fold_is_rejected(self):
        """共线折【是】真退化 (绕线轴的连续歧义) -> 必须 fail-closed。

        构型: 10 点共线 + 2 点离线离面 (全集非共面, 主 s3 门放行)。holdout_k=2 时
        那一折恰好把两个离线点一起留出 -> 训练折只剩 10 个共线点。
        注: k=1 时这个分支【不可达】—— n-1 点共线意味着全集至多共面, 主 s3 门先拒。
        """
        rng = np.random.default_rng(4)
        line = np.column_stack([np.linspace(-30, 30, 10), np.zeros(10), np.zeros(10)])
        src = np.vstack([line, np.array([[0.0, 9.0, 0.0], [0.0, 0.0, 9.0]])])
        dst = (1.3 * (src @ _rotation("z", 11.5).T) + np.array([2.0, 1.0, 0.5])
               + rng.normal(scale=0.01, size=(12, 3)))
        singular = np.linalg.svd(src - src.mean(axis=0), compute_uv=False)
        assert singular[2] / singular[0] > 1e-3, "前提: 全集必须过主 s3 门"

        with pytest.raises(AlignmentError, match="共线") as exc:
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0, cluster_radius_m=0.5,
                           holdout_k=2)
        assert "留出" in str(exc.value)

    def test_fixed_altitude_strip_with_one_off_plane_shot_is_accepted(self):
        """定高航带 + 一张离面点: 全集 s3/s1≈0.17 (健康), 必须放行。旧 s3 折门 6/6 全拒。

        留出 rms 与真实误差【同量级】(实测 6/6 比值 0.96 ~ 1.72), 即这个构型下留出
        残差确实在量对齐误差, 拟合是对的。

        **但它【不是】严格保守的**: 实测 seed=12 的比值 0.963 —— 留出 0.0761m 而出平面
        30m 处真实误差 0.0790m, 留出【低报】了 4%。故这里只断言"同量级/在跟踪", 不断言
        "留出 >= 真实误差"。留出残差本来就只在控制点凸包内、且只在可辨识方向上验证。
        """
        for seed in (11, 12, 13):
            for sigma in (0.05, 0.20):
                src, dst, rot = self._plane_plus_one(seed, sigma)
                singular = np.linalg.svd(src - src.mean(axis=0), compute_uv=False)
                assert singular[2] / singular[0] > 0.1  # 主 s3 门远远放行

                sim3, ev = fit_sfm_to_enu(
                    _resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                    control_target_provenance="derived-from-alignment:xf-x",
                    upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)
                assert ev.passed is True
                assert ev.holdout_folds == 12

                # 留出 rms 与真实误差【同量级】即可 —— 断言的是"它在跟踪", 不是
                # "它是上界"。实测 6/6 比值 0.963~1.717, seed=12 低报 4%, 故这里
                # 【不能】断言 ratio>=1 (本类 docstring 与 alignment.py 的实测依据同)。
                probe = np.column_stack([np.linspace(-20, 20, 20), np.full(20, 10.0),
                                         np.full(20, 30.0)])
                truth = 1.3 * (probe @ rot.T) + np.array([2.0, 1.0, 0.5])
                true_err = float(
                    np.linalg.norm(truth - sim3.apply(probe), axis=1).mean())
                ratio = ev.holdout_rms_m / true_err
                assert 0.5 < ratio < 3.0, (
                    f"seed={seed} sigma={sigma}: 留出 {ev.holdout_rms_m} 与真实误差 "
                    f"{true_err} 脱钩 (比值 {ratio}) —— 放行这个构型的依据是"
                    "'留出残差在跟踪真实误差', 依据没了就不该放行")
                # 对齐本身是对的: 拒绝它没有任何实测依据。
                assert true_err < 0.5 * sigma * 10

    def test_single_off_plane_point_blunder_is_loud_not_silent(self):
        """旧门的归因是"约束由个别点独撑" -> 粗差会被吸收。实测【证伪】。

        7 个自由度被另外 11 个点重重超定, 所以那个离面孤点上的 5m 粗差【吸收不掉】:
        实测 fit 0.071->1.368m、max 4.459m、留出 1.582m —— 三个数一起爆, 现有的
        rms/max/留出门看得一清二楚。故"单点支撑"本身不构成拒绝整个构型的理由。
        """
        src, dst, _ = self._plane_plus_one(11, 0.05)
        _, clean = fit_sfm_to_enu(
            _resolved(src, dst), _ORIGIN, max_rms_m=2.0,
            control_target_provenance="derived-from-alignment:xf-x",
            upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)
        assert clean.rms_residual_m < 0.15

        dst[-1, 2] += 5.0  # 粗差只打在那【一个】离面孤点上
        with pytest.raises(AlignmentError, match="held-out|rms"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=1.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)


# --------------------------------------------------------------------------
# 门 14: 点数门必须作用在【有效】点数上 —— 非派生 (生产) 路径也不例外
# --------------------------------------------------------------------------
class TestEffectiveCountOnTheProductionPath:
    """_n_effective 的 docstring 断言"点数门必须作用在这个数上", 但实现只在
    ``derived`` 模式开门。align_registration 与 CLI (--control-points / --from-gps)
    走的都是【非派生】模式 —— 即这是唯一的生产路径, 那道门在那儿从不开。
    """

    def _clustered(self, n_repeat: int = 6):
        """5 个位置各重复采样 n_repeat 次 (照抄模块 docstring 记录的那个 fail-open)。"""
        base = np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.0], [0.0, 30.0, 0.0],
                         [0.0, 0.0, 30.0], [30.0, 30.0, 30.0]])
        rng = np.random.default_rng(5)
        src = np.repeat(base, n_repeat, axis=0) + rng.normal(
            scale=0.01, size=(5 * n_repeat, 3))
        dst = 2.0 * src + np.array([1.0, 2.0, 3.0])
        return src, dst

    def _burst_gps(self, jitter: float):
        """真实的 --from-gps 触发形态: 30 张图从 30 个【互不相同】的位置拍 (SfM 看得
        见), 但 EXIF GPS 只给出 3 个定位读数 (连拍内复用同一个 fix)。

        关键: 退化在【靶标侧】。src 散开且非共面, 主 s3 门 (只看 src) 因此健康放行 ——
        所以 s3 门在这里给不了任何兜底, 必须由靶标侧的点数门挡。
        """
        rng = np.random.default_rng(5)
        src = rng.uniform(-20.0, 20.0, size=(30, 3))
        fixes = np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.0], [0.0, 30.0, 0.0]])
        dst = np.repeat(fixes, 10, axis=0)
        if jitter:
            dst = dst + rng.normal(scale=jitter, size=(30, 3))
        return src, dst

    def test_non_derived_gate_fires_on_the_effective_count_not_len(self):
        """靶标只有 3 个有效位置: len()=30 过得了 >=4 契约, 有效点数 3 过不了。

        门作用在 3 上而不是 30 上 —— 这正是 _n_effective 的 docstring 断言、而实现
        原本只在派生模式兑现的那件事 (align_registration 与 CLI 走的都是非派生模式,
        即这是唯一的生产路径)。
        """
        src, dst = self._burst_gps(jitter=0.02)  # GPS 在簇内抖几厘米 -> 不精确重合
        assert len(np.unique(dst, axis=0)) == 30  # 精确重复门看不见它
        singular = np.linalg.svd(src - src.mean(axis=0), compute_uv=False)
        assert singular[2] / singular[0] > 1e-3  # 前提: 主 s3 门放行 (它只看 src)

        with pytest.raises(AlignmentError, match="n_effective"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=1e9,
                           cluster_radius_m=1.0)

    def test_five_clusters_are_accepted_but_the_evidence_says_five_not_thirty(self):
        """5 个有效位置 = 15 个方程 vs 7 个自由度 —— 按既有 >=4 契约它【就是】够的。

        所以不能拒 (拒 = 编一个没依据的更严阈值)。真正要修的是证据别把 30 说成约束
        强度: n_effective 必须【出现在证据里】, 让消费者看见 5, 而不是只看见
        "30 个控制点 + 3e-14 米残差"就以为约束极强。
        """
        src, dst = self._clustered()
        _, ev = fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                               cluster_radius_m=1.0)
        assert ev.passed is True
        assert ev.n_control_points == 30
        assert ev.n_effective_control_points == 5
        assert ev.rms_residual_m < 1e-9  # 近零 RMS != 约束强

    def test_exactly_duplicated_targets_rejected_without_any_invented_radius(self):
        """--from-gps 的真实触发: 一次连拍内 EXIF GPS 复用同一个定位读数 -> dst 逐簇
        【精确】重合。精确重复不需要编任何半径就能认出来, 故【默认路径】也挡得住 ——
        这是 cluster_radius_m=None 时唯一挡得住的东西 (近似重复挡不住, 见 docstring)。
        """
        src, dst = self._burst_gps(jitter=0.0)  # 逐簇【精确】复用同一个 fix
        with pytest.raises(AlignmentError, match="互不相同"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=1e9)

    def test_non_derived_floor_is_the_existing_four_point_contract(self):
        """非派生模式的有效点数门 = 既有的 >=4 契约, 不是派生模式的 8。

        8 是【派生】模式的政策选择 (不是标定值 —— 见 TestDerivedFloorHasNoErrorPlateau
        与 docs/verification/2026-07-17-derived-mode-control-point-floor.md); 拿一个连
        派生路都没标定过的数去卡实测控制点路径更没有依据。所以这里 6 个有效点必须
        【放行】, 3 个必须拒。
        """
        base = np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.0], [0.0, 30.0, 0.0],
                         [0.0, 0.0, 30.0], [30.0, 30.0, 30.0], [30.0, 30.0, 0.0]])
        src = np.repeat(base, 3, axis=0) + np.random.default_rng(5).normal(
            scale=0.01, size=(18, 3))
        dst = 2.0 * src + np.array([1.0, 2.0, 3.0])
        _, ev = fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                               cluster_radius_m=1.0)
        assert ev.passed is True
        assert ev.n_effective_control_points == 6
        assert ev.n_control_points == 18  # 两个数都写进证据供复核

    def test_derived_mode_still_requires_eight(self):
        """不许因为放宽了非派生门就把派生门也放宽了。"""
        world = _world_centres(n=6)
        src = np.array([p.t_xyz for p in _poses_in_frame(world, _S_B, "B")])
        dst = np.array(list(world.values()))
        with pytest.raises(AlignmentError, match="n_effective"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)


# --------------------------------------------------------------------------
# 门 15: 复合门的【上游项】不能用 fit rms —— 本模块自己证明了它偏乐观
# --------------------------------------------------------------------------
class TestUpstreamTermIsNotTheOptimisticFitRms:
    """留出门存在的【全部理由】就是不信 fit rms (它把噪声吸进 7 个自由度故偏乐观),
    而复合门转头就拿 fit rms 当上游预算 —— 两处自相矛盾, 且偏乐观 = fail-open 方向。

    根因: 非派生对齐 (即绝大多数 A: 实测控制点 / GPS 锚) 原本【不算】留出, 所以
    _effective_rms_m 只能回落到 rms_residual_m。修法: 算得动就算, 记进证据。
    """

    def _noisy_reference(self):
        """本仓库 test_holdout_catches_overfitting_that_fit_rms_hides 的同一构型:
        8 个实测控制点, 各轴真实噪声 sigma=0.10m -> fit 0.1404 而留出 0.2338。"""
        rng = np.random.default_rng(249)
        src = rng.uniform(-20.0, 20.0, size=(8, 3))
        dst = (1.3 * (src @ _rotation("z", 12.0).T) + np.array([2.0, 1.0, 0.5])
               + rng.normal(scale=0.10, size=(8, 3)))
        return src, dst

    def test_non_derived_alignment_records_its_holdout(self):
        """实测控制点对齐也要记留出 —— 否则下游只能拿被低报 40% 的 fit rms 复合。"""
        src, dst = self._noisy_reference()
        _, ev = fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0)
        assert ev.rms_residual_m == pytest.approx(0.1404, abs=5e-4)
        assert ev.holdout_rms_m == pytest.approx(0.2338, abs=5e-4)
        # 但非派生模式【不】拿留出当门 (那是 HEAD 的既有契约, 收紧它会凭空拒掉
        # 一批 HEAD 接受的对齐 —— 不在本次授权范围)。只记录, 不裁决。
        assert ev.passed is True

    def test_compound_gate_uses_the_holdout_not_the_fit_rms(self):
        """A 的真实锚定误差 0.2338 而非 0.1404 —— 复合门必须用前者。

        实跑过的差额: 预算 0.30m、B 自身留出 0.16m 时, 用 fit rms 算 0.1404+0.16=0.30
        恰好【放行】; 用诚实的上游 0.2338+0.16=0.39 应当【拒绝】。B 于是拿到一个它
        没挣得的 0.30m 米制声称。
        """
        from pipeline.alignment import _effective_rms_m

        src, dst = self._noisy_reference()
        _, ev = fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0)
        assert _effective_rms_m(ev) == pytest.approx(0.2338, abs=5e-4)
        assert _effective_rms_m(ev) > ev.rms_residual_m  # 不许回落到乐观的 fit rms

    def test_four_point_alignment_cannot_hold_out_and_says_so(self):
        """诚实限制: n=4 时留出折只剩 3 点 (<4), 算不了留出 -> 记 None。

        此时下游复合仍只能拿 fit rms —— 这是【定不出更好的数】, 不是假装它够好。
        None 的含义是"该门未运行", 不是"该门通过"。
        """
        src = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0],
                        [0.0, 10.0, 0.0], [0.0, 0.0, 10.0]])
        dst = 2.0 * src + np.array([1.0, 2.0, 3.0])
        _, ev = fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0)
        assert ev.passed is True
        assert ev.holdout_rms_m is None


# --------------------------------------------------------------------------
# 门 16: 输入校验 / fail-closed 门 —— 代码本来就对, 缺的是守着它们的测试
# --------------------------------------------------------------------------
class TestInputValidationGates:
    """这些门逐个注释掉后 52 个测试全部保持绿 —— 任何一次重构/手误破坏它们, 全套
    测试无声通过, 缺陷直达生产。这里逐条钉住。
    """

    def _derived(self, **kwargs):
        world = _world_centres(n=12)
        src = np.array([p.t_xyz for p in _poses_in_frame(world, _S_B, "B")])
        dst = np.array(list(world.values()))
        base = dict(max_rms_m=2.0,
                    control_target_provenance="derived-from-alignment:xf-x",
                    upstream_alignment_rms_m=0.0, cluster_radius_m=0.5)
        return fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, **{**base, **kwargs})

    def test_negative_upstream_rms_rejected(self):
        """传 -5.0 会让复合门变【宽松】(compound = -5 + holdout): 上游误差只能增预算。"""
        with pytest.raises(AlignmentError, match="upstream_alignment_rms_m"):
            self._derived(upstream_alignment_rms_m=-5.0)

    def test_non_finite_upstream_rms_rejected(self):
        with pytest.raises(AlignmentError, match="upstream_alignment_rms_m"):
            self._derived(upstream_alignment_rms_m=float("nan"))

    def test_non_positive_cluster_radius_rejected(self):
        """半径 <=0 会让 _n_effective 退化 (每个点自成一簇 -> 门形同虚设)。"""
        for bad in (0.0, -1.0, float("inf")):
            with pytest.raises(AlignmentError, match="cluster_radius_m"):
                self._derived(cluster_radius_m=bad)

    def test_holdout_k_below_one_rejected(self):
        for bad in (0, -3):
            with pytest.raises(AlignmentError, match="holdout_k"):
                self._derived(holdout_k=bad)

    def test_holdout_train_fold_too_small_rejected(self):
        """留出折吃掉太多点 -> 训练折 <4, 无法验证; fail-closed 而不是跳过。"""
        world = _world_centres(n=10)
        src = np.array([p.t_xyz for p in _poses_in_frame(world, _S_B, "B")])
        dst = np.array(list(world.values()))
        with pytest.raises(AlignmentError, match="训练折"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                           control_target_provenance="derived-from-alignment:xf-x",
                           upstream_alignment_rms_m=0.0, cluster_radius_m=0.5,
                           holdout_k=7)

    def test_non_finite_shared_image_world_coordinate_fails_closed(self):
        """A 的 pose_to_world 把某张共享影像映到非有限坐标 -> 不能当靶标。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        broken = ref.poses[0].model_copy(update={"t_xyz": (1e308, 1e308, 1e308)})
        ref = ref.model_copy(update={"poses": [broken, *ref.poses[1:]]})
        with pytest.raises(AlignmentError, match="非有限"):
            control_points_from_shared_images(ref, _reg_b(world))

    def test_requested_image_not_shared_fails_closed(self):
        """images= 请求的影像未同时出现在两批里 -> fail-closed (typo 不能静默丢点)。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        reg_b = _reg_b(world, images=sorted(world)[:9])
        with pytest.raises(AlignmentError, match="未同时出现"):
            control_points_from_shared_images(
                ref, reg_b, images=["img_000.jpg", "img_999_typo.jpg"])

    def test_images_subset_filter_selects_exactly_the_requested_shared_images(self):
        """images= 形参整个零测试覆盖。它功能正常, 但没有任何测试锁住这个行为。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        wanted = sorted(world)[:9]
        cps = control_points_from_shared_images(ref, _reg_b(world), images=wanted)
        assert sorted(cp.label for cp in cps) == wanted

    def test_calibration_with_non_finite_values_rejected(self, tmp_path):
        """NaN / inf 标定记录绝不能被接受为信任根。

        注意这里【必须】测 inf 和 residual_distance_corr, 否则测的不是 _finite 校验器:
          - NaN 撞 ``Field(ge=0)`` 就已经被拒 (NaN>=0 为 False), 与 _finite 无关;
          - **inf 过得了 ``ge=0``** (inf>=0 为 True), 只有 _finite 拦得住;
          - ``residual_distance_corr`` 【没有】任何 ge/gt 约束, 只有 _finite 拦得住。
        变异测试实测: 只测 NaN 时把 _finite 整个删掉, 全套测试照样全绿。
        """
        ref = _aligned_reference(_world_centres(n=12))
        bad = [("shared_centre_rms_m", float("inf")),
               ("scene_extent_m", float("inf")),
               ("affine_rms_m", float("inf")),
               ("relative_rms", float("inf")),
               ("residual_distance_corr", float("nan")),
               ("residual_distance_corr", float("inf"))]
        for field, value in bad:
            path = tmp_path / f"{field}_{value}.json"
            _write_calibration(path, ref, **{field: value})
            with pytest.raises(AlignmentError), open(path, encoding="utf-8"):
                load_shared_noise_calibration(path, aligned_ref=ref)


# --------------------------------------------------------------------------
# 门 17: 标定脚本的产出必须【真的能被载入】—— 否则前置门根本不可满足
# --------------------------------------------------------------------------
class TestCalibrationRoundTrips:
    def test_the_script_output_loads_through_the_gate_it_is_the_key_to(self, tmp_path):
        """实跑发现的断链: 脚本按文档跑完写出记录, load_shared_noise_calibration 却
        因为 extra='forbid' 撞上 '_' 前缀的人读字段而拒绝 —— 于是 align_to_reference
        【永远】报"无法解析或不自洽"。整个上线前置门按它自己的文档流程【不可满足】。

        没人发现是因为这个脚本一个测试都没有 (实现者自己的话: "输出无人见过")。
        产出与消费必须共用同一个 schema, 否则"能不能载入"就得靠人肉记得。
        """
        from scripts.measure_colmap_shared_noise import main

        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        (tmp_path / "a.json").write_text(ref.model_dump_json(), encoding="utf-8")
        (tmp_path / "b.json").write_text(_reg_b(world).model_dump_json(),
                                         encoding="utf-8")
        out = tmp_path / "cal.json"
        assert main(["--registration-a", str(tmp_path / "a.json"),
                     "--registration-b", str(tmp_path / "b.json"),
                     "--out", str(out)]) == 0

        # 脚本产出的记录必须【绑得住它自己量的那个参考批】—— 否则前置门仍不可满足。
        record = load_shared_noise_calibration(out, aligned_ref=ref)
        assert record.reference_world_frame_id == ref.world_frame.frame_id
        assert record.scene_extent_m > 1.0  # 真米: 这个世界跨几十米

        # 信任根按字节可复现 (LF, 跨平台)。
        assert b"\r\n" not in out.read_bytes()


# --------------------------------------------------------------------------
# 依据复核: _MIN_EFFECTIVE_CONTROL_POINTS=8 到底有没有"误差平台"
# --------------------------------------------------------------------------
class TestDerivedFloorHasNoErrorPlateau:
    """把 docs/verification/2026-07-17-derived-mode-control-point-floor.md 的两条
    载荷结论钉住, 免得它退化成一份没人复核的静态文字。

    背景: 派生模式下限 8 曾自称"标定过的 (theory 实测 k>=8 进误差平台)", 而那个实测
    【不在仓库里】(全仓 grep '1.49'/'45.13'/'theory' 只命中 alignment.py 自己)。
    本仓库重跑该量后的订正:
      - 【误差没有平台】: 凸包内真实误差随 k 平滑按 ~1/sqrt(k) 降, k=8 仍比 k=20 差
        1.73 倍。
      - 【走平的是留出统计量】(~1.8mm), 因为它收敛到噪声地板 —— 那是数据的性质,
        不是对齐质量的性质。原话把估计量的平台读成了误差的平台。
    故 8 是【政策选择】不是标定值。这两条若哪天不成立了, 这里必须变红 (它们是那份
    记录、以及 8 这个数的诚实措辞的全部依据)。
    """

    _EXTENT = 45.0
    _SIGMA = 0.00149 / np.sqrt(3.0)  # 3D rms 1.49mm; 该数是【输入假设】不是本仓结论

    def _sweep(self, k: int, trials: int, seed: int):
        """k 个控制点 -> (凸包内真实误差, 留出 rms) 的中位数, 单位米。"""
        from pipeline.alignment import _holdout_residuals

        rng = np.random.default_rng(seed)
        rot = _rotation("z", 24.0) @ _rotation("x", 17.0)
        scale_true, t_true = 1.7, np.array([5.0, -3.0, 2.0])
        errors, holdouts = [], []
        for _ in range(trials):
            src = rng.uniform(-self._EXTENT / 2, self._EXTENT / 2, size=(k, 3))
            singular = np.linalg.svd(src - src.mean(axis=0), compute_uv=False)
            if singular[0] <= 0 or singular[2] / singular[0] < 1e-3:
                continue  # 退化抽样: 主 s3 门本来就会拒, 不该计入
            dst = (scale_true * (src @ rot.T) + t_true
                   + rng.normal(scale=self._SIGMA, size=(k, 3)))
            scale, rotation, t = umeyama_sim3(src, dst)
            holdout, _ = _holdout_residuals(src, dst, k=1, min_span_ratio=1e-3)
            holdouts.append(float(np.sqrt((holdout ** 2).mean())))
            # 真实误差只在控制点凸包【内】量 —— 凸包外的外推没有任何门守得住。
            probe = rng.uniform(-self._EXTENT / 2, self._EXTENT / 2, size=(200, 3))
            truth = scale_true * (probe @ rot.T) + t_true
            errors.append(float(np.linalg.norm(
                truth - (scale * (probe @ rotation.T) + t), axis=1).mean()))
        return float(np.median(errors)), float(np.median(holdouts))

    def test_true_error_keeps_improving_past_eight_so_eight_is_not_a_plateau(self):
        """k=8 的真实误差【显著劣于】k=16 -> "k>=8 进误差平台"不成立。"""
        err8, _ = self._sweep(8, trials=200, seed=1008)
        err16, _ = self._sweep(16, trials=200, seed=1016)
        assert err8 > 1.3 * err16, (
            f"k=8 真实误差 {err8:.6f}m vs k=16 {err16:.6f}m: 若这两个数接近, 说明"
            "误差【真的】在 8 处进平台, 那份验证记录的订正就错了 —— 请重测并更新 "
            "docs/verification/2026-07-17-derived-mode-control-point-floor.md")

    def test_the_holdout_statistic_is_what_plateaus_not_the_error(self):
        """留出统计量在 k=8..16 基本持平 (收敛到噪声地板), 而真实误差同期显著改善。

        这正是原始断言的来源与它的误读: ~1.8mm 的平台【是真的】, 但那是估计量的地板
        (输入噪声 1.49mm), 不是对齐质量的地板。
        """
        err8, ho8 = self._sweep(8, trials=200, seed=1008)
        err16, ho16 = self._sweep(16, trials=200, seed=1016)
        assert ho8 == pytest.approx(ho16, rel=0.25), (
            f"留出 rms k=8 {ho8:.6f} vs k=16 {ho16:.6f}: 平台没了")
        # 同一区间里真实误差【改善了】—— 两个量讲的不是一个故事。
        assert err16 < 0.8 * err8
        # 且留出统计量确实停在噪声地板附近 (3D rms 1.49mm), 不是停在真实误差上。
        assert ho16 > 3.0 * err16


# --------------------------------------------------------------------------
# 门 18: preview-only 跨批次合并 —— 今天唯一交付得了整村漫游的路
# --------------------------------------------------------------------------
class TestPreviewOnlyMerge:
    """**漫游不需要米制**, 只需要各批次落在【同一个坐标系】里。

    米制跨批次对齐今天不可用 (地基未测: 真实南台照片跑两次 COLMAP 的共享中心噪声
    无人测过, 所有误差预算随它线性缩放)。但那道 fail-closed 【不该连坐 preview】——
    把 B 搬进 A 的任意 frame 不产生任何米制声称, 故它不需要那个标定。

    preview 产物的每一处都必须【诚实地不可测量】: units=arbitrary、
    metric_status=arbitrary、geo_aligned=unaligned、alignment_status=UNALIGNED。
    判据也必须是【无量纲】的 relative_rms —— A 的 gauge 是任意的, 拿米制预算去比
    一批任意单位的残差正是本仓库存在的理由要挡的事。
    """

    def test_preview_merge_needs_no_metric_calibration(self):
        """核心断言: 没有任何标定记录时, preview 合并【照样交付】。

        米制路此时 fail-closed (那是对的, 地基未测), 但 preview 不许被它连坐。
        """
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        reg_b = _reg_b(world)

        # 米制路: 没标定 -> fail-closed。
        with pytest.raises(AlignmentError, match="measure_colmap_shared_noise"):
            align_to_reference(ref, reg_b, max_rms_m=2.0, cluster_radius_m=0.5)

        # preview 路: 同样的两批, 照常交付 —— 不碰标定, 因为它不声称米。
        merged = merge_for_preview(ref, reg_b, max_relative_rms=1e-3)
        assert merged.pose_to_world is not None

    def test_preview_merge_never_claims_metric_or_aligned(self):
        """preview 产物绝不许自称 metric/aligned/geo-aligned —— 它就是 preview-only。"""
        world = _world_centres(n=12)
        merged = merge_for_preview(_aligned_reference(world), _reg_b(world),
                                   max_relative_rms=1e-3)
        assert merged.alignment_status is AlignmentStatus.UNALIGNED
        target = merged.target_frame
        assert target.units is CoordinateUnits.ARBITRARY
        assert target.metric_status is MetricStatus.ARBITRARY
        assert target.geo_aligned is GeoAlignment.UNALIGNED
        assert target.axes is not AxisConvention.ENU_Z_UP

    def test_preview_merge_is_classified_preview_only_downstream(self):
        """下游 (reconstruct 的 geometry_usability) 必须自动判 preview-only。

        这道断言走的是真实消费者的分类器, 不是本模块自说自话。
        """
        from pipeline.reconstruct import _derive_geometry_usability

        world = _world_centres(n=12)
        merged = merge_for_preview(_aligned_reference(world), _reg_b(world),
                                   max_relative_rms=1e-3)
        assert _derive_geometry_usability(
            merged.target_frame, merged.alignment_status,
            list(merged.pose_to_world.evidence), synthetic=False) == "preview-only"

    def test_preview_merge_actually_puts_both_batches_in_one_frame(self):
        """漫游的【全部要求】: 同一张影像在两批里落到同一个点 (A 的 frame 里)。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        merged = merge_for_preview(ref, _reg_b(world), max_relative_rms=1e-3)

        a_by_image = {p.image: p for p in ref.poses}
        b_by_image = {p.image: p for p in merged.poses}
        for image in world:
            landed = merged.pose_to_world.sim3.apply(
                np.asarray([b_by_image[image].t_xyz]))[0]
            # 靶标是 A 的 pose_frame 坐标 (任意单位), 不是 A 的 ENU 世界坐标。
            assert np.allclose(landed, a_by_image[image].t_xyz, atol=1e-6)

    def test_preview_gate_is_dimensionless_not_metres(self):
        """判据必须是 relative_rms (无量纲)。同一物理事实, A 的 gauge 变 100 倍,
        判定与证据里的 relative_rms 必须【不变】—— 否则那是拿任意单位当尺。

        **B 必须带真实噪声**: 变异测试 MUTANT-4 实测, 拟合精确 (rms~1e-15) 时把
        ``relative_rms = rms / extent`` 改成 ``= rms`` 【测试照样全绿】—— 两边都约等于
        0, 除不除以跨度都一样, 断言空转。残差非零, 这个测试才在测东西。
        """
        world = _world_centres(n=12)
        rng = np.random.default_rng(23)
        reg_b = _reg_b(world)
        reg_b = reg_b.model_copy(update={"poses": [
            p.model_copy(update={
                "t_xyz": (np.asarray(p.t_xyz)
                          + rng.normal(scale=0.05, size=3)).tolist()})
            for p in reg_b.poses]})

        relatives, raws = [], []
        for gauge in (1.7, 170.0):
            s_a = _sim3(gauge, _rotation("z", 24.0), [5.0, -3.0, 2.0])
            reg_a = _registration("sfm-local-A", _poses_in_frame(world, s_a, "A"), "A")
            cps = [ControlPoint(label=i, image=i, enu_xyz=tuple(e))
                   for i, e in world.items()]
            ref = align_registration(reg_a, cps, geo_origin=_ORIGIN, max_rms_m=10.0)
            merged = merge_for_preview(ref, reg_b, max_relative_rms=1e-2)
            ev = PreviewMergeEvidence.parse(merged.pose_to_world.evidence[0])
            relatives.append(ev.relative_rms)
            raws.append(ev.rms_residual_source_units)

        # 前提: 原始残差【确实】随 gauge 浮动 100 倍 (否则下面那条断言又是空的)。
        assert raws[0] > 1e-6
        assert raws[1] == pytest.approx(raws[0] / 100.0, rel=1e-6)
        # 而无量纲判据【不动】—— 这才是米制路的 max_rms_m 给不了的东西。
        assert relatives[0] == pytest.approx(relatives[1], rel=1e-9)

    def test_preview_evidence_has_no_metre_denominated_field(self):
        """证据里【一个 *_m 字段都不许有】: 这批数字量在 A 的任意 gauge 里。"""
        world = _world_centres(n=12)
        merged = merge_for_preview(_aligned_reference(world), _reg_b(world),
                                   max_relative_rms=1e-3)
        ev = PreviewMergeEvidence.parse(merged.pose_to_world.evidence[0])
        metre_named = [f for f in ev.model_dump() if f.endswith("_m")]
        assert metre_named == [], f"preview 证据不许有米制字段: {metre_named}"

    def test_preview_merge_cannot_launder_itself_into_the_metric_path(self):
        """关键: preview 合并的 B 【不能】被当参考批喂进米制入口。

        否则 preview 就成了洗白旁路: 无标定造出一个"世界", 再拿它当 A 给 C 盖米制章。
        """
        world = _world_centres(n=12)
        merged = merge_for_preview(_aligned_reference(world), _reg_b(world),
                                   max_relative_rms=1e-3)
        with pytest.raises(AlignmentError):
            align_to_reference(merged, _reg_c(world), max_rms_m=2.0,
                               cluster_radius_m=0.5)

    def test_preview_evidence_is_not_parsable_as_metric_alignment_evidence(self):
        """两种证据串必须【互不可解析】—— 共用前缀就等于共用信任。"""
        world = _world_centres(n=12)
        merged = merge_for_preview(_aligned_reference(world), _reg_b(world),
                                   max_relative_rms=1e-3)
        for item in merged.pose_to_world.evidence:
            with pytest.raises(ValueError):
                Sim3AlignmentEvidence.parse(item)

    def test_preview_still_fails_closed_on_degenerate_span(self):
        """preview 不声称米, 但 Sim3 的【可辨识性】是几何事实, 不随声称放松。

        共享影像沿一条直线 (细长航带的重叠带) -> 绕线轴的旋转是【连续】歧义, 缝出来
        的 B 在离线处可以任意错位。少一个米制声称不会让这件事变得可接受。
        """
        world = {f"img_{i:03d}.jpg": np.array([float(i) * 3.0, 0.0, 0.0])
                 for i in range(12)}
        # A 用不着已对齐 (preview 不声称米), 正好也绕开 align_registration 的共面门。
        reg_a = _registration("sfm-local-A", _poses_in_frame(world, _S_A, "A"), "A")
        with pytest.raises(AlignmentError, match="degenerate|共线|退化"):
            merge_for_preview(reg_a, _reg_b(world), max_relative_rms=1e-3)

    def test_preview_rejects_a_fit_worse_than_the_declared_relative_budget(self):
        """判据是【调用方按物理声明】的, 本模块编不出一个 relative_rms 阈值就不编。"""
        world = _world_centres(n=12)
        ref = _aligned_reference(world)
        reg_b = _reg_b(world)
        # 给 B 的位姿注入噪声 -> 两批不再由一个精确 Sim3 相关。
        rng = np.random.default_rng(17)
        noisy = [p.model_copy(update={
            "t_xyz": (np.asarray(p.t_xyz) + rng.normal(scale=0.5, size=3)).tolist()})
            for p in reg_b.poses]
        reg_b = reg_b.model_copy(update={"poses": noisy})
        with pytest.raises(AlignmentError, match="relative_rms"):
            merge_for_preview(ref, reg_b, max_relative_rms=1e-6)

    def test_preview_requires_an_explicit_relative_budget(self):
        """无默认值: 编不出的数就不编 (与 align_to_reference 的 max_rms_m 同规矩)。"""
        import inspect
        params = inspect.signature(merge_for_preview).parameters
        assert params["max_relative_rms"].default is inspect.Parameter.empty
        # 且绝不暴露任何米制/geo 参数 —— preview 没有米可言。
        assert "max_rms_m" not in params
        assert "geo_origin" not in params

    def test_preview_reference_need_not_be_aligned_at_all(self):
        """preview 的参考批【不需要】已对齐: 落进 A 的任意 frame 就够漫游了。

        这是与米制路的根本区别 —— 米制路必须要求 A 已对齐 (否则凭空造米), preview
        不要求, 因为它不声称米。要求它 = 把一个不存在的前置门强加给唯一能用的路。
        """
        world = _world_centres(n=12)
        unaligned_a = _registration("sfm-local-A",
                                    _poses_in_frame(world, _S_A, "A"), "A")
        assert unaligned_a.alignment_status is AlignmentStatus.UNALIGNED
        merged = merge_for_preview(unaligned_a, _reg_b(world), max_relative_rms=1e-3)
        assert merged.alignment_status is AlignmentStatus.UNALIGNED
        assert merged.target_frame.units is CoordinateUnits.ARBITRARY


class TestHoldoutKGuardAppliesEverywhere:
    def test_bad_holdout_k_fails_closed_in_non_derived_mode_too(self):
        """holdout_k 的守卫原本只在派生模式里跑 —— 而非派生模式现在也算留出了。

        holdout_k=0 -> range(0, n, 0) -> 裸 ValueError (不是 AlignmentError), 调用方
        的 except AlignmentError 接不住, allow_unaligned_fallback 也兜不住。守卫必须
        与"哪个模式用得到它"无关。
        """
        rng = np.random.default_rng(11)
        src = rng.uniform(-20.0, 20.0, size=(8, 3))
        dst = 2.0 * src + np.array([1.0, 2.0, 3.0])
        for bad in (0, -1):
            with pytest.raises(AlignmentError, match="holdout_k"):
                fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0,
                               holdout_k=bad)
