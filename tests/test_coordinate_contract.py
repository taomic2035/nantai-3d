"""Coordinate-frame and reconstruction provenance contracts.

These tests intentionally exercise the public boundary instead of inferring a
world frame from an engine name.  Unknown or unaligned inputs may still be
previewed, but they must never be labelled as geo-aligned metric geometry.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraIntrinsics,
    CameraPose,
    CaptureSession,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    FrameTransform,
    GeoAlignment,
    Handedness,
    MetricStatus,
    RegistrationResult,
    Sim3,
    SplatInput,
    TransformMethod,
)
from pipeline.reconstruct import (
    _apply_splat_transform,
    _parse_splat_args,
    import_session_splats,
    reconstruct,
)
from pipeline.registration import colmap_register, mock_register


def _session(session_id: str = "s0", *, anchor=None) -> CaptureSession:
    return CaptureSession(
        session_id=session_id,
        kind="photo_batch",
        source="photos",
        images=["one.jpg"],
        geo_anchor=anchor,
    )


def _metric_frame(frame_id: str = "world") -> CoordinateFrame:
    return CoordinateFrame(
        frame_id=frame_id,
        handedness=Handedness.RIGHT,
        axes=AxisConvention.LOCAL_Z_UP,
        units=CoordinateUnits.METERS,
        metric_status=MetricStatus.METRIC,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.MEASURED,
        evidence=["survey-scale"],
    )


def _arbitrary_frame(frame_id: str = "scan-local") -> CoordinateFrame:
    return CoordinateFrame(
        frame_id=frame_id,
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SFM,
        evidence=["source-frame-contract:v1"],
    )


def _registration(frame: CoordinateFrame) -> RegistrationResult:
    session = _session()
    pose = CameraPose(
        image="one.jpg",
        session_id=session.session_id,
        quat_wxyz=[1, 0, 0, 0],
        t_xyz=[0, 0, 0],
        intrinsics=CameraIntrinsics.from_fov(640, 480),
    )
    return RegistrationResult(
        schema_version=2,
        engine="mock",
        pose_frame=frame,
        world_frame=None,
        alignment_status=AlignmentStatus.SYNTHETIC,
        sessions=[session],
        poses=[pose],
    )


class TestCoordinateFrame:
    def test_fields_remain_typed_enums(self):
        frame = CoordinateFrame(
            frame_id="synthetic-local",
            handedness="right",
            axes="local-z-up",
            units="meters",
            metric_status="metric",
            geo_aligned="unaligned",
            provenance="synthetic",
        )
        assert isinstance(frame.handedness, Handedness)
        assert isinstance(frame.axes, AxisConvention)
        assert isinstance(frame.units, CoordinateUnits)
        assert isinstance(frame.metric_status, MetricStatus)
        assert isinstance(frame.geo_aligned, GeoAlignment)
        assert isinstance(frame.provenance, FrameProvenance)

    def test_left_handed_and_inconsistent_metric_claims_are_rejected(self):
        with pytest.raises(ValidationError, match="right-handed"):
            CoordinateFrame(
                frame_id="bad",
                handedness="left",
                axes="local-z-up",
                units="meters",
                metric_status="metric",
                geo_aligned="unaligned",
                provenance="measured",
            )
        with pytest.raises(ValidationError, match="metric.*meters"):
            CoordinateFrame(
                frame_id="bad-scale",
                handedness="right",
                axes="sfm-arbitrary",
                units="arbitrary",
                metric_status="metric",
                geo_aligned="unaligned",
                provenance="sfm",
            )
        with pytest.raises(ValidationError, match="unknown handedness"):
            CoordinateFrame(
                frame_id="partly-known",
                handedness="unknown",
                axes="local-z-up",
                units="meters",
                metric_status="metric",
                geo_aligned="unaligned",
                provenance="synthetic",
            )

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: CameraPose(
                image="a.jpg", session_id="s", quat_wxyz=[0, 0, 0, 0],
                t_xyz=[0, 0, 0], intrinsics=CameraIntrinsics.from_fov(10, 10),
            ),
            lambda: CameraPose(
                image="a.jpg", session_id="s", quat_wxyz=[np.nan, 0, 0, 0],
                t_xyz=[0, 0, 0], intrinsics=CameraIntrinsics.from_fov(10, 10),
            ),
            lambda: CameraPose(
                image="a.jpg", session_id="s", quat_wxyz=[1, 0, 0, 0],
                t_xyz=[0, np.inf, 0], intrinsics=CameraIntrinsics.from_fov(10, 10),
            ),
            lambda: Sim3(quat_wxyz=[0, 0, 0, 0]),
            lambda: Sim3(scale=np.inf),
        ],
    )
    def test_non_finite_values_and_zero_quaternions_are_rejected(self, factory):
        with pytest.raises((ValidationError, ValueError)):
            factory()

    @pytest.mark.parametrize(
        "rotation",
        [
            [[1, 0, 0], [0, 2, 0], [0, 0, 1]],  # non-orthogonal
            [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],  # reflection, det=-1
        ],
    )
    def test_non_orthogonal_or_reflected_rotation_is_rejected(self, rotation):
        with pytest.raises(ValidationError, match="rotation"):
            Sim3(rotation_matrix_xyz=rotation)


class TestRegistrationFrameClaims:
    def test_mock_without_gps_is_synthetic_metric_local(self, tmp_path):
        session = _session()
        result = mock_register(tmp_path, sessions=[session])
        assert result.pose_frame.frame_id == "mock-local"
        assert result.pose_frame.units is CoordinateUnits.METERS
        assert result.pose_frame.metric_status is MetricStatus.METRIC
        assert result.pose_frame.axes is AxisConvention.LOCAL_Z_UP
        assert result.pose_frame.geo_aligned is GeoAlignment.UNALIGNED
        assert result.pose_frame.provenance is FrameProvenance.SYNTHETIC
        assert result.alignment_status is AlignmentStatus.SYNTHETIC

    def test_mock_with_gps_is_synthetic_enu_not_measured_reconstruction(self, tmp_path):
        from pipeline.recon_schema import GeoAnchor

        session = _session(anchor=GeoAnchor(lat=26.0, lon=119.0, alt=50.0))
        result = mock_register(tmp_path, sessions=[session])
        assert result.pose_frame.frame_id == "mock-enu"
        assert result.pose_frame.axes is AxisConvention.ENU_Z_UP
        assert result.pose_frame.geo_aligned is GeoAlignment.ALIGNED
        assert result.pose_frame.provenance is FrameProvenance.SYNTHETIC
        assert result.alignment_status is AlignmentStatus.SYNTHETIC

    def test_colmap_with_one_gps_anchor_stays_arbitrary_and_unaligned(
        self, tmp_path, monkeypatch
    ):
        from pipeline.recon_schema import GeoAnchor

        photos = tmp_path / "photos"
        photos.mkdir()
        workspace = tmp_path / "colmap"
        session = _session(anchor=GeoAnchor(lat=26.0, lon=119.0, alt=50.0))

        def fake_run(args, capture_output, text, timeout=None, **kwargs):
            command = args[1]
            if command == "mapper":
                (workspace / "sparse" / "0").mkdir(parents=True)
            if command == "model_converter":
                (workspace / "sparse" / "0" / "images.txt").write_text(
                    "1 1 0 0 0 0 0 0 1 one.jpg\n\n", encoding="utf-8"
                )
                (workspace / "sparse" / "0" / "cameras.txt").write_text(
                    "1 PINHOLE 1920 1080 1000 1000 960 540\n", encoding="utf-8"
                )
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr("pipeline.registration.subprocess.run", fake_run)
        result = colmap_register(photos, workspace, sessions=[session])

        assert result.geo_origin == session.geo_anchor
        assert result.pose_frame.frame_id == "sfm-local"
        assert result.pose_frame.axes is AxisConvention.SFM_ARBITRARY
        assert result.pose_frame.units is CoordinateUnits.ARBITRARY
        assert result.pose_frame.metric_status is MetricStatus.ARBITRARY
        assert result.pose_frame.geo_aligned is GeoAlignment.UNALIGNED
        assert result.pose_frame.provenance is FrameProvenance.SFM
        assert result.world_frame is None
        assert result.pose_to_world is None
        assert result.alignment_status is AlignmentStatus.UNALIGNED

    def test_unreachable_world_frame_is_rejected(self):
        pose_frame = _metric_frame("pose-local")
        world_frame = _metric_frame("world")
        session = _session()
        pose = CameraPose(
            image="one.jpg",
            session_id="s0",
            quat_wxyz=[1, 0, 0, 0],
            t_xyz=[0, 0, 0],
            intrinsics=CameraIntrinsics.from_fov(640, 480),
        )
        with pytest.raises(ValidationError, match="world_frame.*pose_to_world"):
            RegistrationResult(
                schema_version=2,
                engine="mock",
                pose_frame=pose_frame,
                world_frame=world_frame,
                alignment_status="synthetic",
                sessions=[session],
                poses=[pose],
            )


class TestFrameTransform:
    def test_id_is_content_derived_and_stable(self):
        kwargs = dict(
            source_frame="local",
            target_frame="world",
            sim3=Sim3(scale=2, t_xyz=[1, 2, 3]),
            method=TransformMethod.EXTERNAL_SIM3,
            evidence=["control-points:v1"],
        )
        first = FrameTransform(**kwargs)
        second = FrameTransform(**kwargs)
        assert first.transform_id == second.transform_id
        assert first.transform_id.startswith("xf-")
        with pytest.raises(ValidationError, match="content-derived"):
            FrameTransform(transform_id="xf-forged", **kwargs)

    def test_content_used_by_stable_id_is_immutable(self):
        transform = FrameTransform(
            source_frame="local",
            target_frame="world",
            sim3=Sim3(scale=2),
            method="external-sim3",
            evidence=["control-points:v1"],
        )
        stable_id = transform.transform_id
        with pytest.raises(ValidationError):
            transform.sim3.scale = 3
        with pytest.raises(AttributeError):
            transform.evidence.append("late mutation")
        assert transform.transform_id == stable_id

    def test_geometric_id_ignores_evidence_and_quaternion_sign(self):
        first = FrameTransform(
            source_frame="local",
            target_frame="world",
            sim3=Sim3(quat_wxyz=[0.5, 0.5, 0.5, 0.5], t_xyz=[1, 2, 3]),
            method="external-sim3",
            evidence=["control-points:first"],
        )
        equivalent = FrameTransform(
            source_frame="local",
            target_frame="world",
            sim3=Sim3(quat_wxyz=[-0.5, -0.5, -0.5, -0.5], t_xyz=[1, 2, 3]),
            method="gps-anchor",
            evidence=["different audit evidence"],
        )

        assert first.transform_id == equivalent.transform_id

    @pytest.mark.parametrize("translation", ([1, 0, 0], [1e-14, 0, 0]))
    def test_non_identity_same_frame_transform_is_rejected(self, translation):
        with pytest.raises(ValidationError, match="same-frame|same frame"):
            FrameTransform(
                source_frame="world",
                target_frame="world",
                sim3=Sim3(t_xyz=translation),
                method="external-sim3",
                evidence=["invalid relabel"],
            )

    def test_splat_input_carries_complete_source_frame(self):
        source = _arbitrary_frame()
        item = SplatInput(session_id="s0", path="a.ply", source_frame=source)

        assert item.source_frame == source
        assert item.frame_id == source.frame_id

    def test_splat_transform_source_must_match_declared_frame(self):
        transform = FrameTransform(
            source_frame="local-a",
            target_frame="world",
            sim3=Sim3(),
            method="external-sim3",
            evidence=["unit-test"],
        )
        with pytest.raises(ValidationError, match="source_frame"):
            SplatInput(
                session_id="s0",
                path="a.ply",
                source_frame=_arbitrary_frame("local-b"),
                transform=transform,
            )

    def test_same_transform_cannot_be_applied_twice_and_failure_is_atomic(self):
        frame = _metric_frame()
        transform = FrameTransform(
            source_frame="local",
            target_frame=frame.frame_id,
            sim3=Sim3(scale=2, t_xyz=[10, 0, 0]),
            method="external-sim3",
            evidence=["unit-test"],
        )
        splat = SplatInput(
            session_id="s0",
            path="unused.ply",
            source_frame=_arbitrary_frame("local"),
            transform=transform,
        )
        scene = GaussianScene([[1, 2, 3]], [[0.1, 0.2, 0.3]])
        _apply_splat_transform(scene, splat, frame)
        once = scene.xyz.copy()
        assert np.allclose(once, [[12, 4, 6]])
        with pytest.raises(ValueError, match="已应用|already applied"):
            _apply_splat_transform(scene, splat, frame)
        assert np.array_equal(scene.xyz, once)


class TestSplatImportBoundary:
    def test_bare_dict_is_rejected_instead_of_guessing_a_frame(self):
        reg = _registration(_metric_frame())
        with pytest.raises(TypeError, match="SplatInput"):
            import_session_splats({"s0": "unknown.ply"}, reg)

    def test_unaligned_input_cannot_merge_into_target_frame(self, tmp_path):
        ply = tmp_path / "local.ply"
        GaussianScene([[0, 0, 0]], [[1, 0, 0]]).save_ply(ply, flavor="3dgs")
        reg = _registration(_metric_frame())
        item = SplatInput(
            session_id="s0",
            path=str(ply),
            source_frame=_arbitrary_frame("other-local"),
        )
        with pytest.raises(ValueError, match="requires a FrameTransform"):
            import_session_splats([item], reg)

    def test_explicitly_aligned_input_is_imported(self, tmp_path):
        ply = tmp_path / "local.ply"
        GaussianScene([[1, 2, 3]], [[1, 0, 0]]).save_ply(ply, flavor="3dgs")
        target = _metric_frame()
        reg = _registration(target)
        transform = FrameTransform(
            source_frame="scan-local",
            target_frame=target.frame_id,
            sim3=Sim3(scale=2, t_xyz=[10, 0, 0]),
            method="external-sim3",
            evidence=["control-points:v1"],
        )
        item = SplatInput(
            session_id="s0",
            path=str(ply),
            source_frame=_arbitrary_frame("scan-local"),
            transform=transform,
        )
        scenes = import_session_splats([item], reg)
        assert len(scenes) == 1
        assert np.allclose(scenes[0].xyz, [[12, 4, 6]], atol=1e-4)

    def test_ply_frame_and_units_must_match_declared_source(self, tmp_path):
        ply = tmp_path / "wrong-units.ply"
        GaussianScene(
            [[1, 2, 3]],
            [[1, 0, 0]],
            frame_id="scan-local",
            units="meters",
        ).save_ply(ply, flavor="3dgs")
        source = _arbitrary_frame("scan-local")
        transform = FrameTransform(
            source_frame=source.frame_id,
            target_frame="world",
            sim3=Sim3(scale=2),
            method="external-sim3",
            evidence=["scale-control:v1"],
        )

        with pytest.raises(ValueError, match="PLY units|source.*units"):
            import_session_splats(
                [SplatInput(
                    session_id="s0",
                    path=str(ply),
                    source_frame=source,
                    transform=transform,
                )],
                _registration(_metric_frame()),
            )

    def test_identity_cannot_relabel_arbitrary_source_as_meters(self):
        scene = GaussianScene(
            [[1, 2, 3]],
            [[1, 0, 0]],
            frame_id="shared-name",
            units="arbitrary",
        )
        item = SplatInput(
            session_id="s0",
            path="unused.ply",
            source_frame=_arbitrary_frame("shared-name"),
        )

        with pytest.raises(ValueError, match="coordinate contract|units"):
            _apply_splat_transform(scene, item, _metric_frame("shared-name"))

    def test_legacy_frame_id_is_conservative_and_cannot_noop_upgrade_units(self):
        item = SplatInput(session_id="s0", path="unused.ply", frame_id="legacy-local")
        assert item.source_frame.units is CoordinateUnits.UNKNOWN
        assert item.source_frame.metric_status is MetricStatus.UNKNOWN

        scene = GaussianScene(
            [[0, 0, 0]], [[1, 0, 0]], frame_id="legacy-local", units="unknown"
        )
        with pytest.raises(ValueError, match="legacy|coordinate contract|units"):
            _apply_splat_transform(scene, item, _metric_frame("legacy-local"))

    def test_cli_full_contract_json_is_supported(self, tmp_path):
        source = _arbitrary_frame()
        spec_path = tmp_path / "splat.json"
        spec_path.write_text(
            SplatInput(
                session_id="s0", path="scene.ply", source_frame=source
            ).model_dump_json(),
            encoding="utf-8",
        )

        parsed = _parse_splat_args([str(spec_path)])

        assert parsed[0].source_frame == source


class TestManifestCoordinateProvenance:
    def test_mock_manifest_labels_proxy_and_synthetic_metric_frame(self, photos_dir, tmp_path):
        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web",
            engine="mock",
            reg_engine="mock",
        )
        contract = manifest["coordinate_contract"]
        provenance = manifest["provenance"]
        assert manifest["schema_version"] == 2
        assert contract["target_frame"]["units"] == "meters"
        assert contract["target_frame"]["provenance"] == "synthetic"
        assert contract["alignment_status"] == "synthetic"
        assert contract["metric_evidence"]
        assert contract["transform_chain"] == []
        assert contract["applied_transform_ids"] == []
        assert provenance == {
            "requested_reconstruction_engine": "mock",
            "actual_reconstruction_engine": "mock-proxy",
            "requested_registration_engine": "mock",
            "actual_registration_engine": "mock",
            "synthetic": True,
            "geometry_usability": "preview-proxy",
            "artifact_fidelity": {
                "full_3dgs": "full-3dgs",
                "lod_preview": "dc-point-preview",
            },
            "render_fidelity": "dc-point-preview",
        }

    @pytest.mark.parametrize(
        ("frame", "alignment_status", "expected"),
        [
            (
                CoordinateFrame(
                    frame_id="village-enu",
                    handedness="right",
                    axes="enu-z-up",
                    units="meters",
                    metric_status="metric",
                    geo_aligned="aligned",
                    provenance="measured",
                    evidence=["survey-control:v1"],
                ),
                AlignmentStatus.ALIGNED,
                "metric-aligned",
            ),
            (
                CoordinateFrame(
                    frame_id="survey-local",
                    handedness="right",
                    axes="local-z-up",
                    units="meters",
                    metric_status="metric",
                    geo_aligned="unaligned",
                    provenance="measured",
                    evidence=["scale-bars:v1"],
                ),
                AlignmentStatus.UNALIGNED,
                "metric-unaligned",
            ),
            (
                CoordinateFrame(
                    frame_id="sfm-local",
                    handedness="right",
                    axes="sfm-arbitrary",
                    units="arbitrary",
                    metric_status="arbitrary",
                    geo_aligned="unaligned",
                    provenance="sfm",
                    evidence=["colmap-joint-model"],
                ),
                AlignmentStatus.UNALIGNED,
                "preview-only",
            ),
            (
                CoordinateFrame(
                    frame_id="unproven-metric",
                    handedness="right",
                    axes="enu-z-up",
                    units="meters",
                    metric_status="metric",
                    geo_aligned="aligned",
                    provenance="measured",
                    evidence=[],
                ),
                AlignmentStatus.ALIGNED,
                "preview-only",
            ),
            (
                CoordinateFrame(
                    frame_id="synthetic-enu",
                    handedness="right",
                    axes="enu-z-up",
                    units="meters",
                    metric_status="metric",
                    geo_aligned="aligned",
                    provenance="synthetic",
                    evidence=["synthetic-layout:v1"],
                ),
                AlignmentStatus.SYNTHETIC,
                "preview-proxy",
            ),
        ],
    )
    def test_geometry_usability_is_derived_from_coordinate_evidence_not_engine_name(
        self, photos_dir, tmp_path, monkeypatch, frame, alignment_status, expected
    ):
        session = _session("s0")
        pose = CameraPose(
            image="one.jpg",
            session_id="s0",
            quat_wxyz=[1, 0, 0, 0],
            t_xyz=[0, 0, 0],
            intrinsics=CameraIntrinsics.from_fov(640, 480),
        )
        registration = RegistrationResult(
            schema_version=2,
            engine="colmap",
            pose_frame=frame,
            alignment_status=alignment_status,
            sessions=[session],
            poses=[pose],
        )
        monkeypatch.setattr(
            "pipeline.reconstruct.register", lambda *args, **kwargs: registration
        )
        ply_path = tmp_path / f"{frame.frame_id}.ply"
        GaussianScene(
            [[0, 0, 0]],
            [[0.1, 0.2, 0.3]],
            frame_id=frame.frame_id,
            units=frame.units.value,
        ).save_ply(ply_path, flavor="3dgs")

        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web",
            engine="import",
            reg_engine="colmap",
            splat_map=[SplatInput(
                session_id="s0",
                path=str(ply_path),
                source_frame=frame,
            )],
            dedup_voxel=0,
        )

        assert manifest["provenance"]["geometry_usability"] == expected

    def test_bare_colmap_manifest_never_claims_world_meters(
        self, photos_dir, tmp_path, monkeypatch
    ):
        sessions = [_session("s0")]
        poses = [
            CameraPose(
                image="one.jpg",
                session_id="s0",
                quat_wxyz=[1, 0, 0, 0],
                t_xyz=[0, 0, 0],
                intrinsics=CameraIntrinsics.from_fov(640, 480),
            )
        ]
        sfm = CoordinateFrame(
            frame_id="sfm-local",
            handedness="right",
            axes="sfm-arbitrary",
            units="arbitrary",
            metric_status="arbitrary",
            geo_aligned="unaligned",
            provenance="sfm",
            evidence=["colmap-joint-model"],
        )
        reg = RegistrationResult(
            schema_version=2,
            engine="colmap",
            pose_frame=sfm,
            alignment_status="unaligned",
            sessions=sessions,
            poses=poses,
        )
        monkeypatch.setattr("pipeline.reconstruct.register", lambda *args, **kwargs: reg)
        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web",
            engine="mock",
            reg_engine="colmap",
            dedup_voxel=0,
        )
        target = manifest["coordinate_contract"]["target_frame"]
        assert target["frame_id"] == "sfm-local"
        assert target["units"] == "arbitrary"
        assert target["metric_status"] == "arbitrary"
        assert manifest["coordinate_contract"]["alignment_status"] == "unaligned"
        assert "world-meter" not in str(manifest).lower()


class TestLegacyV1Migration:
    def test_missing_v1_frame_is_preserved_as_unknown_not_guessed_metric(self):
        session = _session()
        pose = CameraPose(
            image="one.jpg", session_id="s0", quat_wxyz=[1, 0, 0, 0],
            t_xyz=[0, 0, 0], intrinsics=CameraIntrinsics.from_fov(640, 480),
        )
        legacy = RegistrationResult.model_validate(
            {
                "schema_version": 1,
                "engine": "colmap",
                "world_convention": "ENU, Z-up, meters, origin=first-anchor",
                "sessions": [session.model_dump()],
                "poses": [pose.model_dump()],
                "session_to_world": {"s0": Sim3().model_dump()},
            }
        )
        assert legacy.pose_frame.frame_id == "legacy-unknown"
        assert legacy.pose_frame.handedness is Handedness.UNKNOWN
        assert legacy.pose_frame.units is CoordinateUnits.UNKNOWN
        assert legacy.pose_frame.metric_status is MetricStatus.UNKNOWN
        assert legacy.pose_frame.geo_aligned is GeoAlignment.UNKNOWN
        assert legacy.alignment_status is AlignmentStatus.UNKNOWN
        assert legacy.world_frame is None
        assert legacy.pose_to_world is None
