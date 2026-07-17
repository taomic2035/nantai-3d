"""SfM->ENU Sim3 alignment: exact recovery, reflection guard, fail-closed gates.

These tests exercise the measured-3DGS alignment step at its public boundary.
The recurring theme is provenance safety: a degenerate, inconsistent, or
under-determined fit must never promote arbitrary SfM geometry to a metric ENU
world -- it must fail closed, leaving the registration sfm-local / UNALIGNED.
"""
from __future__ import annotations

import numpy as np
import pytest

from pipeline.alignment import (
    AlignmentError,
    align_registration,
    build_control_points,
    control_points_from_geo_anchors,
    fit_sfm_to_enu,
    load_control_points_from_ingest_gps,
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
    GeoAlignment,
    GeoAnchor,
    Handedness,
    MetricStatus,
    RegistrationResult,
    Sim3,
    Sim3AlignmentEvidence,
    TransformMethod,
    gps_to_enu,
)


def _rotation_z(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _sfm_frame(frame_id: str = "sfm-local") -> CoordinateFrame:
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


def _pose(image: str, xyz) -> CameraPose:
    return CameraPose(
        image=image,
        session_id="s0",
        quat_wxyz=[1, 0, 0, 0],
        t_xyz=list(xyz),
        intrinsics=CameraIntrinsics.from_fov(640, 480),
    )


def _registration_with_camera_centres(
    centres: dict[str, tuple[float, float, float]],
    *,
    geo_origin: GeoAnchor | None = None,
) -> RegistrationResult:
    session = CaptureSession(
        session_id="s0",
        kind="photo_batch",
        source="photos",
        images=list(centres),
    )
    return RegistrationResult(
        schema_version=2,
        engine="colmap",
        pose_frame=_sfm_frame(),
        world_frame=None,
        alignment_status=AlignmentStatus.UNALIGNED,
        geo_origin=geo_origin,
        sessions=[session],
        poses=[_pose(image, xyz) for image, xyz in centres.items()],
    )


def _resolved(src_pts, dst_pts):
    return [
        (np.asarray(s, float), np.asarray(d, float), f"cp{i}")
        for i, (s, d) in enumerate(zip(src_pts, dst_pts, strict=True))
    ]


_ORIGIN = GeoAnchor(lat=26.0, lon=119.0, alt=50.0)


class TestUmeyama:
    def test_recovers_known_sim3_exactly(self):
        rng = np.random.default_rng(7)
        src = rng.normal(size=(12, 3)) * 5.0
        scale_true = 2.5
        rotation_true = _rotation_z(np.radians(37.0))
        t_true = np.array([10.0, -4.0, 3.0])
        dst = scale_true * (src @ rotation_true.T) + t_true

        scale, rotation, t = umeyama_sim3(src, dst)

        assert np.isclose(scale, scale_true, atol=1e-9)
        assert np.allclose(rotation, rotation_true, atol=1e-9)
        assert np.allclose(t, t_true, atol=1e-8)
        # residual is essentially zero for a consistent similarity
        predicted = scale * (src @ rotation.T) + t
        assert np.allclose(predicted, dst, atol=1e-8)

    def test_reflection_is_never_produced(self):
        # A mirrored target configuration must NOT yield a reflection: the guard
        # forces det(R)=+1, and the resulting matrix is a valid proper rotation
        # that Sim3 accepts rather than rejecting.
        rng = np.random.default_rng(3)
        src = rng.normal(size=(10, 3))
        dst = src.copy()
        dst[:, 0] *= -1.0  # mirror across the x-plane (det would want -1)

        scale, rotation, t = umeyama_sim3(src, dst)

        assert np.isclose(float(np.linalg.det(rotation)), 1.0, atol=1e-9)
        # Sim3 rejects reflections; constructing it proves R is proper.
        sim3 = Sim3(
            scale=scale,
            rotation_matrix_xyz=tuple(tuple(r) for r in rotation.tolist()),
            t_xyz=tuple(t.tolist()),
        )
        assert np.isclose(float(np.linalg.det(sim3.rotation_matrix())), 1.0, atol=1e-9)


class TestFitGates:
    def _consistent_points(self, n=5, *, scale=1.7, angle=20.0, seed=1):
        rng = np.random.default_rng(seed)
        src = rng.normal(size=(n, 3)) * 4.0
        rotation = _rotation_z(np.radians(angle))
        t = np.array([5.0, 6.0, 7.0])
        dst = scale * (src @ rotation.T) + t
        return src, dst

    def test_passing_fit_returns_sim3_and_evidence(self):
        src, dst = self._consistent_points()
        sim3, evidence = fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0)

        assert isinstance(sim3, Sim3)
        assert evidence.passed is True
        assert evidence.method == "umeyama-sim3"
        assert evidence.n_control_points == len(src)
        assert np.isclose(evidence.rms_residual_m, 0.0, atol=1e-6)
        assert np.isclose(evidence.scale, 1.7, atol=1e-6)

    def test_fewer_than_three_points_fail_closed(self):
        src, dst = self._consistent_points(n=2)
        with pytest.raises(AlignmentError, match=">=3 control points"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN)

    def test_collinear_points_fail_closed(self):
        # All source points on a line: smallest singular value ~ 0.
        line = np.array([[float(i), 0.0, 0.0] for i in range(5)])
        dst = 2.0 * line + np.array([1.0, 1.0, 1.0])
        with pytest.raises(AlignmentError, match="degenerate"):
            fit_sfm_to_enu(_resolved(line, dst), _ORIGIN)

    def test_coplanar_points_fail_closed(self):
        # All source points in the z=0 plane: rank-2 configuration.
        rng = np.random.default_rng(9)
        planar = np.column_stack([rng.normal(size=6), rng.normal(size=6), np.zeros(6)])
        dst = 1.5 * planar + np.array([3.0, 0.0, -2.0])
        with pytest.raises(AlignmentError, match="degenerate"):
            fit_sfm_to_enu(_resolved(planar, dst), _ORIGIN)

    def test_high_residual_fails_closed_and_emits_no_world(self):
        # Correspondences inconsistent with ANY similarity -> large RMS.
        rng = np.random.default_rng(5)
        src = rng.normal(size=(8, 3)) * 3.0
        dst = rng.normal(size=(8, 3)) * 50.0  # unrelated targets
        with pytest.raises(AlignmentError, match="exceeds max_rms"):
            fit_sfm_to_enu(_resolved(src, dst), _ORIGIN, max_rms_m=2.0)


class TestAlignRegistration:
    def _non_collinear_centres(self):
        return {
            "a.jpg": (0.0, 0.0, 0.0),
            "b.jpg": (10.0, 0.0, 0.0),
            "c.jpg": (0.0, 10.0, 1.0),
            "d.jpg": (3.0, 4.0, 8.0),
        }

    def _control_points_from_gps(self, reg, scale, rotation, t):
        """Build GPS control points whose ENU exactly matches a known Sim3."""
        control_points = []
        for pose in reg.poses:
            sfm = np.asarray(pose.t_xyz, float)
            enu = scale * (rotation @ sfm) + t
            # Invert gps_to_enu (small-area) to synthesise a GeoAnchor.
            east, north, up = enu
            earth_r = 6378137.0
            lat = _ORIGIN.lat + np.degrees(north / earth_r)
            lon = _ORIGIN.lon + np.degrees(
                east / (earth_r * np.cos(np.radians(_ORIGIN.lat)))
            )
            alt = _ORIGIN.alt + up
            control_points.append(
                ControlPoint(label=pose.image, image=pose.image,
                             geo=GeoAnchor(lat=lat, lon=lon, alt=alt))
            )
        return control_points

    def test_gps_control_points_yield_aligned_world_enu(self):
        reg = _registration_with_camera_centres(
            self._non_collinear_centres(), geo_origin=_ORIGIN
        )
        scale, rotation, t = 1.0, _rotation_z(np.radians(15.0)), np.array([2.0, -1.0, 0.5])
        control_points = self._control_points_from_gps(reg, scale, rotation, t)

        aligned = align_registration(reg, control_points, max_rms_m=2.0)

        assert aligned.alignment_status is AlignmentStatus.ALIGNED
        world = aligned.world_frame
        assert world is not None
        assert world.frame_id == "world-enu"
        assert world.axes is AxisConvention.ENU_Z_UP
        assert world.units is CoordinateUnits.METERS
        assert world.metric_status is MetricStatus.METRIC
        assert world.geo_aligned is GeoAlignment.ALIGNED
        assert world.provenance is FrameProvenance.MEASURED

        xf = aligned.pose_to_world
        assert xf is not None
        assert xf.source_frame == "sfm-local"
        assert xf.target_frame == "world-enu"
        assert xf.method is TransformMethod.GPS_ANCHOR

        # The aligned registration must itself be a schema-valid v2 result whose
        # transform chain re-validates (source==pose_frame, target==world_frame).
        restored = RegistrationResult.model_validate_json(aligned.model_dump_json())
        assert restored.target_frame.frame_id == "world-enu"
        assert restored.target_frame.geo_aligned is GeoAlignment.ALIGNED
        assert restored.pose_to_world.transform_id == xf.transform_id

    def test_alignment_evidence_is_machine_parseable(self):
        reg = _registration_with_camera_centres(
            self._non_collinear_centres(), geo_origin=_ORIGIN
        )
        scale, rotation, t = 1.3, _rotation_z(np.radians(-22.0)), np.array([7.0, 3.0, 1.0])
        control_points = self._control_points_from_gps(reg, scale, rotation, t)

        aligned = align_registration(reg, control_points, max_rms_m=2.0)

        world_ev = Sim3AlignmentEvidence.parse(aligned.world_frame.evidence[-1])
        xf_ev = Sim3AlignmentEvidence.parse(aligned.pose_to_world.evidence[0])
        assert world_ev == xf_ev
        assert world_ev.passed is True
        assert world_ev.n_control_points == len(reg.poses)
        assert np.isclose(world_ev.scale, 1.3, atol=1e-6)
        assert np.isclose(world_ev.rms_residual_m, 0.0, atol=1e-6)

    def test_surveyed_enu_points_use_control_points_method(self):
        centres = self._non_collinear_centres()
        reg = _registration_with_camera_centres(centres)  # no geo_origin
        scale, rotation, t = 1.0, np.eye(3), np.array([0.0, 0.0, 0.0])
        control_points = [
            ControlPoint(
                label=img,
                source_xyz=xyz,
                enu_xyz=tuple((scale * (rotation @ np.asarray(xyz, float)) + t).tolist()),
            )
            for img, xyz in centres.items()
        ]
        aligned = align_registration(
            reg, control_points, geo_origin=_ORIGIN, max_rms_m=2.0
        )
        assert aligned.pose_to_world.method is TransformMethod.CONTROL_POINTS
        assert aligned.world_frame.frame_id == "world-enu"

    def test_gate_failure_raises_and_leaves_registration_unchanged(self):
        # Only two control points -> fewer-than-three gate; reg must be untouched.
        reg = _registration_with_camera_centres(
            {"a.jpg": (0.0, 0.0, 0.0), "b.jpg": (1.0, 0.0, 0.0)}, geo_origin=_ORIGIN
        )
        control_points = [
            ControlPoint(label="a.jpg", image="a.jpg",
                         geo=GeoAnchor(lat=26.0, lon=119.0, alt=50.0)),
            ControlPoint(label="b.jpg", image="b.jpg",
                         geo=GeoAnchor(lat=26.001, lon=119.0, alt=50.0)),
        ]
        with pytest.raises(AlignmentError):
            align_registration(reg, control_points)
        assert reg.world_frame is None
        assert reg.pose_to_world is None
        assert reg.alignment_status is AlignmentStatus.UNALIGNED

    def test_allow_unaligned_fallback_returns_reg_unchanged(self):
        reg = _registration_with_camera_centres(
            {"a.jpg": (0.0, 0.0, 0.0), "b.jpg": (1.0, 0.0, 0.0)}, geo_origin=_ORIGIN
        )
        control_points = [
            ControlPoint(label="a.jpg", image="a.jpg",
                         geo=GeoAnchor(lat=26.0, lon=119.0, alt=50.0)),
            ControlPoint(label="b.jpg", image="b.jpg",
                         geo=GeoAnchor(lat=26.001, lon=119.0, alt=50.0)),
        ]
        result = align_registration(
            reg, control_points, allow_unaligned_fallback=True
        )
        assert result is reg  # identical object, no partial mutation
        assert result.world_frame is None
        assert result.alignment_status is AlignmentStatus.UNALIGNED

    def test_high_residual_align_emits_no_world_frame(self):
        # Consistent geometry but a tight RMS gate the fit cannot meet.
        reg = _registration_with_camera_centres(
            self._non_collinear_centres(), geo_origin=_ORIGIN
        )
        # Targets unrelated to source -> large residual under any similarity.
        rng = np.random.default_rng(2)
        control_points = [
            ControlPoint(
                label=pose.image,
                image=pose.image,
                enu_xyz=tuple((rng.normal(size=3) * 100.0).tolist()),
            )
            for pose in reg.poses
        ]
        with pytest.raises(AlignmentError, match="exceeds max_rms"):
            align_registration(reg, control_points, max_rms_m=2.0)
        assert reg.world_frame is None
        assert reg.alignment_status is AlignmentStatus.UNALIGNED

    def test_missing_geo_origin_fails_closed(self):
        reg = _registration_with_camera_centres(self._non_collinear_centres())
        control_points = [
            ControlPoint(label=img, image=img,
                         geo=GeoAnchor(lat=26.0, lon=119.0, alt=50.0))
            for img in self._non_collinear_centres()
        ]
        with pytest.raises(AlignmentError, match="geo origin"):
            align_registration(reg, control_points)


class TestBuildControlPoints:
    def test_image_names_resolve_to_camera_centres(self):
        reg = _registration_with_camera_centres(
            {"a.jpg": (1.0, 2.0, 3.0)}, geo_origin=_ORIGIN
        )
        resolved = build_control_points(
            reg,
            [ControlPoint(label="a", image="a.jpg", enu_xyz=(0.0, 0.0, 0.0))],
            _ORIGIN,
        )
        assert np.allclose(resolved[0][0], [1.0, 2.0, 3.0])

    def test_unknown_image_fails_closed(self):
        reg = _registration_with_camera_centres({"a.jpg": (0.0, 0.0, 0.0)})
        with pytest.raises(AlignmentError, match="unknown image"):
            build_control_points(
                reg,
                [ControlPoint(label="x", image="missing.jpg", enu_xyz=(0.0, 0.0, 0.0))],
                _ORIGIN,
            )

    def test_gps_target_reduces_through_gps_to_enu(self):
        reg = _registration_with_camera_centres({"a.jpg": (0.0, 0.0, 0.0)})
        anchor = GeoAnchor(lat=26.001, lon=119.001, alt=55.0)
        resolved = build_control_points(
            reg,
            [ControlPoint(label="a", image="a.jpg", geo=anchor)],
            _ORIGIN,
        )
        assert np.allclose(resolved[0][1], gps_to_enu(anchor, _ORIGIN))


class TestAlignmentCLI:
    def test_cli_needs_geo_origin_and_writes_lf_aligned(self, tmp_path):
        # Exercises the actual `python -m pipeline.alignment` entrypoint (surveyed
        # enu_xyz control points, no GPS): --geo-origin must supply the ENU tangent
        # origin, and the written registration.json is LF (byte-reproducible root).
        import json

        from pipeline.alignment import main as align_main

        centres = {"a.jpg": (0.0, 0.0, 0.0), "b.jpg": (10.0, 0.0, 0.0),
                   "c.jpg": (0.0, 10.0, 1.0), "d.jpg": (3.0, 4.0, 8.0)}
        reg = _registration_with_camera_centres(centres)  # no geo_origin
        reg_path = tmp_path / "reg.json"
        reg_path.write_text(reg.model_dump_json(), encoding="utf-8")
        cp_path = tmp_path / "cps.json"
        cp_path.write_text(json.dumps(
            [{"label": img, "image": img, "enu_xyz": list(xyz)}
             for img, xyz in centres.items()]), encoding="utf-8")
        out_path = tmp_path / "aligned.json"

        # Without --geo-origin (and no reg.geo_origin) the fit fails closed.
        with pytest.raises(AlignmentError, match="geo origin"):
            align_main(["--registration", str(reg_path),
                        "--control-points", str(cp_path), "--out", str(out_path)])

        rc = align_main(["--registration", str(reg_path),
                         "--control-points", str(cp_path),
                         "--geo-origin", "26.0,119.0,50.0", "--out", str(out_path)])
        assert rc == 0
        raw = out_path.read_bytes()
        assert b"\r\n" not in raw
        result = RegistrationResult.model_validate_json(raw.decode("utf-8"))
        assert result.alignment_status is AlignmentStatus.ALIGNED
        assert result.target_frame.frame_id == "world-enu"


class TestControlPointsFromGeoAnchors:
    """从逐图 geo 锚点 (EXIF GPS 派生) 一键构造对齐控制点 —— 免手工逐图写 ControlPoint,
    让 GPS 标记的采集 turnkey 米制对齐; 拟合门 (>=3 点/RMS) 仍权威, 本函数只组装证据。"""

    def test_pairs_only_registered_images_sorted_and_labeled(self):
        reg = _registration_with_camera_centres(
            {"a.jpg": (0, 0, 0), "b.jpg": (10, 0, 0), "c.jpg": (0, 10, 1)})
        anchors = {
            "b.jpg": GeoAnchor(lat=26.0, lon=119.001, alt=51.0),
            "a.jpg": GeoAnchor(lat=26.0, lon=119.0, alt=50.0),
            "z.jpg": GeoAnchor(lat=26.0, lon=119.0, alt=50.0),  # 未注册 → 跳过
        }
        cps = control_points_from_geo_anchors(reg, anchors)
        assert [cp.label for cp in cps] == ["a.jpg", "b.jpg"]  # 排序, 排除未注册
        for cp in cps:
            assert cp.image == cp.label
            assert cp.geo is not None and cp.enu_xyz is None and cp.source_xyz is None
        assert cps[0].geo.alt == 50.0

    def test_empty_when_no_registered_image_has_an_anchor(self):
        reg = _registration_with_camera_centres({"a.jpg": (0, 0, 0)})
        assert control_points_from_geo_anchors(reg, {}) == []
        assert control_points_from_geo_anchors(
            reg, {"other.jpg": GeoAnchor(lat=26.0, lon=119.0, alt=50.0)}) == []

    def test_built_control_points_drive_alignment_end_to_end(self):
        # turnkey 真实路径: 逐图 geo 锚点 → 控制点 → align_registration → world-enu measured
        reg = _registration_with_camera_centres(
            self.__class__._non_collinear(), geo_origin=_ORIGIN)
        scale, rotation, t = 1.0, _rotation_z(np.radians(15.0)), np.array([2.0, -1.0, 0.5])
        earth_r = 6378137.0
        anchors = {}
        for pose in reg.poses:
            east, north, up = scale * (rotation @ np.asarray(pose.t_xyz, float)) + t
            anchors[pose.image] = GeoAnchor(
                lat=_ORIGIN.lat + np.degrees(north / earth_r),
                lon=_ORIGIN.lon + np.degrees(
                    east / (earth_r * np.cos(np.radians(_ORIGIN.lat)))),
                alt=_ORIGIN.alt + up)
        cps = control_points_from_geo_anchors(reg, anchors)
        assert len(cps) == len(reg.poses)
        aligned = align_registration(reg, cps, max_rms_m=2.0)
        assert aligned.alignment_status is AlignmentStatus.ALIGNED
        assert aligned.world_frame.frame_id == "world-enu"

    @staticmethod
    def _non_collinear():
        return {"a.jpg": (0.0, 0.0, 0.0), "b.jpg": (10.0, 0.0, 0.0),
                "c.jpg": (0.0, 10.0, 1.0), "d.jpg": (3.0, 4.0, 8.0)}


class TestFromGpsIngestManifest:
    """--from-gps: 从 ingest manifest 的逐图 EXIF GPS 一键 turnkey 米制对齐,
    免手工写 control_points.json。照片带 GPS + 已注册 → 控制点; 拟合门仍权威。"""

    @staticmethod
    def _photo(name, gps, payload):
        import hashlib

        from pipeline.ingest_manifest import FrameMapping, SourceRecord
        digest = hashlib.sha256(payload).hexdigest()
        return SourceRecord(
            source_path=name, source_sha256=digest, kind="photo", bytes=len(payload),
            gps=gps, exif_source="photo-exif",
            outputs=(FrameMapping(
                output_path=name, output_sha256=digest, output_bytes=len(payload),
                source_frame_index=None, preserves_source_bytes=True),))

    def _write_manifest(self, path, images_gps):
        from datetime import UTC, datetime

        from pipeline.ingest_manifest import IngestParams, build_manifest
        params = IngestParams(fps=2, max_frames=300, blur_threshold=0, max_long_edge=2560)
        sources = [self._photo(name, gps, f"payload-{i}".encode())
                   for i, (name, gps) in enumerate(images_gps.items())]
        path.write_text(build_manifest(
            created_utc=datetime.now(UTC), params=params, sources=sources
        ).model_dump_json(), encoding="utf-8")

    def _synth_gps(self, reg, scale, rotation, t):
        from pipeline.ingest_manifest import GpsObservation
        earth_r = 6378137.0
        out = {}
        for pose in reg.poses:
            east, north, up = scale * (rotation @ np.asarray(pose.t_xyz, float)) + t
            out[pose.image] = GpsObservation(
                lat=_ORIGIN.lat + np.degrees(north / earth_r),
                lon=_ORIGIN.lon + np.degrees(
                    east / (earth_r * np.cos(np.radians(_ORIGIN.lat)))),
                altitude_m=_ORIGIN.alt + up)
        return out

    def test_loads_control_points_and_aligns(self, tmp_path):
        reg = _registration_with_camera_centres(
            {"a.jpg": (0.0, 0.0, 0.0), "b.jpg": (10.0, 0.0, 0.0),
             "c.jpg": (0.0, 10.0, 1.0), "d.jpg": (3.0, 4.0, 8.0)}, geo_origin=_ORIGIN)
        images_gps = self._synth_gps(
            reg, 1.0, _rotation_z(np.radians(15.0)), np.array([2.0, -1.0, 0.5]))
        mpath = tmp_path / "ingest.json"
        self._write_manifest(mpath, images_gps)

        cps = load_control_points_from_ingest_gps(mpath, reg)
        assert {cp.label for cp in cps} == {"a.jpg", "b.jpg", "c.jpg", "d.jpg"}
        aligned = align_registration(reg, cps, max_rms_m=2.0)
        assert aligned.alignment_status is AlignmentStatus.ALIGNED
        assert aligned.world_frame.frame_id == "world-enu"

    def test_no_registered_gps_fails_closed(self, tmp_path):
        from pipeline.ingest_manifest import GpsObservation
        reg = _registration_with_camera_centres({"a.jpg": (0.0, 0.0, 0.0)})
        # manifest 里的图名与注册的不同 → 无匹配 → fail-closed
        mpath = tmp_path / "ingest.json"
        self._write_manifest(
            mpath, {"other.jpg": GpsObservation(lat=26.0, lon=119.0, altitude_m=50.0)})
        with pytest.raises(AlignmentError, match="GPS"):
            load_control_points_from_ingest_gps(mpath, reg)

    def test_cli_from_gps_end_to_end(self, tmp_path):
        from pipeline.alignment import main as align_main
        reg = _registration_with_camera_centres(
            {"a.jpg": (0.0, 0.0, 0.0), "b.jpg": (10.0, 0.0, 0.0),
             "c.jpg": (0.0, 10.0, 1.0), "d.jpg": (3.0, 4.0, 8.0)}, geo_origin=_ORIGIN)
        (tmp_path / "reg.json").write_text(reg.model_dump_json(), encoding="utf-8")
        self._write_manifest(
            tmp_path / "ingest.json",
            self._synth_gps(reg, 1.0, _rotation_z(np.radians(15.0)),
                            np.array([2.0, -1.0, 0.5])))
        out = tmp_path / "aligned.json"
        rc = align_main(["--registration", str(tmp_path / "reg.json"),
                         "--from-gps", str(tmp_path / "ingest.json"),
                         "--geo-origin", "26.0,119.0,50.0", "--out", str(out)])
        assert rc == 0
        result = RegistrationResult.model_validate_json(out.read_text(encoding="utf-8"))
        assert result.alignment_status is AlignmentStatus.ALIGNED
        assert result.target_frame.frame_id == "world-enu"
        # --control-points 与 --from-gps 互斥
        with pytest.raises(SystemExit):
            align_main(["--registration", str(tmp_path / "reg.json"),
                        "--control-points", "x.json", "--from-gps", "y.json",
                        "--out", str(out)])
