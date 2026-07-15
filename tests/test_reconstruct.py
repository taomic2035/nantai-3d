"""端到端重建: mock 全链路 / 导入引擎 / 变清晰 (区域替换) / 视频抽帧"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
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
)
from pipeline.reconstruct import _validate_scene_history, reconstruct


def _mock_source_frame() -> CoordinateFrame:
    return CoordinateFrame(
        frame_id="mock-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.LOCAL_Z_UP,
        units=CoordinateUnits.METERS,
        metric_status=MetricStatus.METRIC,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SYNTHETIC,
        evidence=["mock-layout:v1"],
    )


def _arbitrary_source_frame() -> CoordinateFrame:
    return CoordinateFrame(
        frame_id="sfm-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SFM,
        evidence=["colmap-model:v1"],
    )


class TestMockPipeline:
    def test_end_to_end(self, photos_dir, tmp_path):
        m = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                        web_dir=tmp_path / "web", engine="mock",
                        reg_engine="mock")
        assert m["gaussian_count"] > 1000
        assert m["registration_engine"] == "mock"
        assert set(m["lod"]) == {"0", "1", "2"}
        # 会话信息: 视频 + 照片混合
        kinds = {s["kind"] for s in m["sessions"]}
        assert kinds == {"video", "photo_batch"}
        # 产物齐备且可解析
        assert (tmp_path / "recon" / "registration.json").exists()
        assert (tmp_path / "recon" / "scene_full.ply").exists()
        for f in m["lod"].values():
            assert (tmp_path / "web" / f).exists()
        manifest_on_disk = json.loads(
            (tmp_path / "web" / "recon_manifest.json").read_text())
        assert manifest_on_disk["gaussian_count"] == m["gaussian_count"]

    def test_manifest_artifacts_are_web_relative_and_integrity_described(
        self, photos_dir, tmp_path
    ):
        web_dir = tmp_path / "web"
        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "recon",
            web_dir=web_dir,
            engine="mock",
            reg_engine="mock",
        )

        assert manifest["full_3dgs"] == "recon_full.ply"
        assert (tmp_path / "recon" / "scene_full.ply").is_file()
        assert (web_dir / manifest["full_3dgs"]).is_file()

        artifacts = manifest["artifacts"]
        all_descriptors = [artifacts["full_3dgs"], *artifacts["lod"].values()]
        for descriptor in all_descriptors:
            relative = Path(descriptor["path"])
            assert not relative.is_absolute()
            assert ".." not in relative.parts
            payload = web_dir / relative
            assert payload.is_file()
            assert descriptor["bytes"] == payload.stat().st_size
            assert descriptor["sha256"] == hashlib.sha256(payload.read_bytes()).hexdigest()
            assert isinstance(descriptor["attributes"], list)
            assert "x" in descriptor["attributes"]
            assert "sh_degree" in descriptor

        full = artifacts["full_3dgs"]
        assert full["path"] == manifest["full_3dgs"]
        assert full["fidelity"] == "full-3dgs"
        assert full["sh_degree"] == 0
        assert {
            "f_dc_0", "opacity", "scale_0", "rot_0",
        }.issubset(full["attributes"])
        for level, path in manifest["lod"].items():
            preview = artifacts["lod"][level]
            assert preview["path"] == path
            assert preview["fidelity"] == "dc-point-preview"
            assert preview["sh_degree"] is None
            assert preview["attributes"] == ["x", "y", "z", "r", "g", "b", "scale"]

    def test_manifest_is_lf_byte_reproducible(self, photos_dir, tmp_path):
        # The manifest is the coordinate/provenance trust root; it must be
        # byte-identical across OSes (no Windows CRLF) so its digest is stable.
        web_dir = tmp_path / "web"
        reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                    web_dir=web_dir, engine="mock", reg_engine="mock")
        raw = (web_dir / "recon_manifest.json").read_bytes()
        assert b"\r\n" not in raw
        assert raw.endswith(b"\n")
        # Sidecar digest attests the manifest bytes (sha256sum-compatible format).
        digest, name = (web_dir / "recon_manifest.sha256").read_text(
            encoding="utf-8").split()
        assert name == "recon_manifest.json"
        assert digest == hashlib.sha256(raw).hexdigest()

    def test_consumes_pre_aligned_registration(self, photos_dir, tmp_path):
        # An aligned world-enu registration (from pipeline.alignment) flows
        # through reconstruct instead of recomputing sfm-local, so the manifest's
        # coordinate contract reports the measured metric ENU world.
        from pipeline.alignment import align_registration
        from pipeline.recon_schema import ControlPoint, GeoAnchor
        from pipeline.registration import register

        reg = register(photos_dir, tmp_path / "reg.json", engine="mock")
        origin = GeoAnchor(lat=26.0, lon=119.0, alt=50.0)
        control_points = [
            ControlPoint(label=p.image, image=p.image,
                         enu_xyz=tuple(float(v) for v in p.t_xyz))
            for p in reg.poses
        ]
        aligned = align_registration(reg, control_points, geo_origin=origin,
                                     max_rms_m=2.0)
        assert aligned.alignment_status is AlignmentStatus.ALIGNED

        manifest = reconstruct(
            photos_dir=photos_dir, out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web", engine="mock", registration=aligned,
        )
        contract = manifest["coordinate_contract"]
        assert contract["target_frame"]["frame_id"] == "world-enu"
        assert contract["target_frame"]["geo_aligned"] == "aligned"
        assert contract["alignment_status"] == "aligned"
        # registration.json persisted from the supplied reg, LF (byte-reproducible).
        raw = (tmp_path / "recon" / "registration.json").read_bytes()
        assert b"\r\n" not in raw

    def test_deterministic_across_runs(self, photos_dir, tmp_path):
        m1 = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r1",
                         web_dir=tmp_path / "w1", engine="mock",
                         reg_engine="mock")
        m2 = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r2",
                         web_dir=tmp_path / "w2", engine="mock",
                         reg_engine="mock")
        assert m1["gaussian_count"] == m2["gaussian_count"]
        s1 = GaussianScene.load_ply(tmp_path / "r1" / "scene_full.ply")
        s2 = GaussianScene.load_ply(tmp_path / "r2" / "scene_full.ply")
        assert np.allclose(s1.xyz, s2.xyz, atol=1e-4)

    def test_sessions_stitched_in_one_frame(self, photos_dir, tmp_path):
        """视频与照片两个会话拼进同一场景: 总范围应覆盖两个锚点区域"""
        m = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                        web_dir=tmp_path / "web", engine="mock",
                        reg_engine="mock")
        extent_x = m["bounds"]["max"][0] - m["bounds"]["min"][0]
        assert extent_x > 80  # 两会话网格间距 80m, 拼接后范围必然更大

    def test_lod_counts_decrease(self, photos_dir, tmp_path):
        reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                    web_dir=tmp_path / "web", engine="mock", reg_engine="mock")
        counts = [len(GaussianScene.load_ply(tmp_path / "web" / f"recon_lod{i}.ply"))
                  for i in range(3)]
        assert counts[0] < counts[1] < counts[2]


class TestImportEngine:
    def test_reconstruct_rejects_bare_splat_dict_at_api_boundary(
        self, photos_dir, tmp_path
    ):
        with pytest.raises(TypeError, match="SplatInput"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "r",
                web_dir=tmp_path / "w",
                engine="import",
                reg_engine="mock",
                splat_map={"video_vid_A": "scene.ply"},  # type: ignore[arg-type]
            )

    def test_import_aligns_by_session(self, photos_dir, tmp_path):
        rng = np.random.default_rng(2)
        ext = GaussianScene(rng.uniform(0, 10, (800, 3)),
                            rng.uniform(0, 1, (800, 3)))
        ext_ply = tmp_path / "ext.ply"
        ext.save_ply(ext_ply, flavor="3dgs")

        m = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "recon",
                        web_dir=tmp_path / "web", engine="import",
                        reg_engine="mock",
                        splat_map=[SplatInput(
                            session_id="video_vid_A",
                            path=str(ext_ply),
                            source_frame=_mock_source_frame(),
                        )],
                        dedup_voxel=0.0)
        assert m["gaussian_count"] == 800

    def test_import_into_aligned_world_is_metric_aligned(self, photos_dir, tmp_path):
        # The full measured path: a NON-synthetic sfm registration aligned to
        # world-enu, then a real external 3DGS imported through a Sim3 into that
        # ENU world. Real geometry + a measured (sfm, not mock) registration in a
        # metric geo-aligned frame with alignment evidence => the manifest reports
        # geometry_usability=metric-aligned, the first measurement-grade class.
        # (A mock registration would correctly taint this back to preview-proxy.)
        from pipeline.alignment import align_registration
        from pipeline.recon_schema import (
            CameraIntrinsics,
            CameraPose,
            ControlPoint,
            GeoAnchor,
            TransformMethod,
        )

        centres = {"a.jpg": (0.0, 0.0, 0.0), "b.jpg": (10.0, 0.0, 0.0),
                   "c.jpg": (0.0, 10.0, 1.0), "d.jpg": (3.0, 4.0, 8.0)}
        session = CaptureSession(session_id="s0", kind="photo_batch",
                                 source="photos", images=list(centres))
        reg = RegistrationResult(
            schema_version=2, engine="colmap",
            pose_frame=_arbitrary_source_frame(), world_frame=None,
            alignment_status=AlignmentStatus.UNALIGNED, sessions=[session],
            poses=[CameraPose(image=img, session_id="s0", quat_wxyz=[1, 0, 0, 0],
                              t_xyz=list(xyz),
                              intrinsics=CameraIntrinsics.from_fov(640, 480))
                   for img, xyz in centres.items()])
        origin = GeoAnchor(lat=26.0, lon=119.0, alt=50.0)
        aligned = align_registration(
            reg,
            [ControlPoint(label=img, image=img, enu_xyz=xyz)
             for img, xyz in centres.items()],
            geo_origin=origin, max_rms_m=2.0,
        )

        rng = np.random.default_rng(11)
        ext = GaussianScene(rng.uniform(0, 10, (600, 3)), rng.uniform(0, 1, (600, 3)))
        ext_ply = tmp_path / "trained.ply"
        ext.save_ply(ext_ply, flavor="3dgs")
        trainer_frame = CoordinateFrame(
            frame_id="trainer-local", handedness=Handedness.RIGHT,
            axes=AxisConvention.LOCAL_Z_UP, units=CoordinateUnits.METERS,
            metric_status=MetricStatus.METRIC, geo_aligned=GeoAlignment.UNALIGNED,
            provenance=FrameProvenance.MEASURED, evidence=["trainer export contract"])
        transform = FrameTransform(
            source_frame="trainer-local", target_frame="world-enu",
            sim3=Sim3(scale=1.0, quat_wxyz=[1.0, 0.0, 0.0, 0.0],
                      t_xyz=[0.0, 0.0, 0.0]),
            method=TransformMethod.EXTERNAL_SIM3, evidence=["control-point fit"])

        m = reconstruct(
            photos_dir=photos_dir, out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web", engine="import", registration=aligned,
            splat_map=[SplatInput(session_id=reg.sessions[0].session_id,
                                  path=str(ext_ply), source_frame=trainer_frame,
                                  transform=transform)],
            dedup_voxel=0.0)

        contract = m["coordinate_contract"]
        assert contract["target_frame"]["frame_id"] == "world-enu"
        assert contract["alignment_status"] == "aligned"
        assert m["provenance"]["synthetic"] is False
        assert m["provenance"]["geometry_usability"] == "metric-aligned"

    def test_full_local_import_flow_via_scripts(self, tmp_path):
        # Certifies the user's local step-C exactly as the manual documents it:
        # a trainer-style .ply with NON-unit quats -> scripts/normalize_ply_quats.py
        # -> scripts/prepare_import.py -> reconstruct --engine import. Real geometry
        # in an arbitrary sfm-local frame => honest preview-only (not synthetic, not
        # faked-metric). Drives the actual script CLIs (catches CLI/packaging bugs).
        import subprocess
        import sys

        from plyfile import PlyData

        root = Path(__file__).resolve().parent.parent
        rng = np.random.default_rng(21)
        ext = GaussianScene(rng.uniform(0, 5, (300, 3)), rng.uniform(0, 1, (300, 3)))
        ply = tmp_path / "trained.ply"
        ext.save_ply(ply, flavor="3dgs")
        # Simulate a trainer that exported un-normalized quaternions.
        pd = PlyData.read(str(ply), mmap=False)
        for r in ("rot_0", "rot_1", "rot_2", "rot_3"):
            pd["vertex"].data[r] = pd["vertex"].data[r] * 2.0
        PlyData([pd["vertex"]], text=False, byte_order="<").write(str(ply))

        def run(*args):
            proc = subprocess.run([sys.executable, *args], cwd=root,
                                  capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr
            return proc

        run("scripts/normalize_ply_quats.py", str(ply))
        run("scripts/prepare_import.py", str(ply), "--out-dir", str(tmp_path / "recon"))

        reg = RegistrationResult.model_validate_json(
            (tmp_path / "recon" / "registration.json").read_text(encoding="utf-8"))
        splat = SplatInput.model_validate_json(
            (tmp_path / "recon" / "splat-input.json").read_text(encoding="utf-8"))
        manifest = reconstruct(
            photos_dir=tmp_path, out_dir=tmp_path / "out", web_dir=tmp_path / "web",
            engine="import", registration=reg, splat_map=[splat], dedup_voxel=0)

        assert manifest["gaussian_count"] == 300
        assert manifest["provenance"]["synthetic"] is False
        assert manifest["provenance"]["geometry_usability"] == "preview-only"

    def test_import_rejects_simple_point_ply_as_full_3dgs(self, photos_dir, tmp_path):
        source = tmp_path / "simple.ply"
        GaussianScene(
            [[0, 0, 0]],
            [[1, 0, 0]],
            frame_id="mock-local",
            units="meters",
        ).save_ply(source, flavor="simple")

        with pytest.raises(ValueError, match="full 3DGS|simple PLY|3DGS import"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "recon",
                web_dir=tmp_path / "web",
                engine="import",
                reg_engine="mock",
                splat_map=[SplatInput(
                    session_id="video_vid_A",
                    path=str(source),
                    source_frame=_mock_source_frame(),
                )],
                dedup_voxel=0,
            )

    @pytest.mark.parametrize(
        ("source_provenance", "expected_synthetic", "expected_usability"),
        [
            (FrameProvenance.SYNTHETIC, True, "preview-proxy"),
            (FrameProvenance.UNKNOWN, False, "preview-only"),
        ],
    )
    def test_import_manifest_fails_closed_for_untrusted_source_provenance(
        self,
        tmp_path,
        monkeypatch,
        source_provenance,
        expected_synthetic,
        expected_usability,
    ):
        source_frame = CoordinateFrame(
            frame_id="shared-world",
            handedness=Handedness.RIGHT,
            axes=AxisConvention.LOCAL_Z_UP,
            units=CoordinateUnits.METERS,
            metric_status=MetricStatus.METRIC,
            geo_aligned=GeoAlignment.UNALIGNED,
            provenance=source_provenance,
            evidence=["source-provenance-evidence"],
        )
        target_frame = CoordinateFrame(
            frame_id="shared-world",
            handedness=Handedness.RIGHT,
            axes=AxisConvention.LOCAL_Z_UP,
            units=CoordinateUnits.METERS,
            metric_status=MetricStatus.METRIC,
            geo_aligned=GeoAlignment.UNALIGNED,
            provenance=FrameProvenance.MEASURED,
            evidence=["survey-control"],
        )
        session = CaptureSession(
            session_id="s0", kind="photo_batch", source="photos", images=[]
        )
        registration = RegistrationResult(
            engine="colmap",
            pose_frame=target_frame,
            alignment_status=AlignmentStatus.UNALIGNED,
            sessions=[session],
            poses=[],
        )
        monkeypatch.setattr("pipeline.reconstruct.register", lambda *args, **kwargs: registration)
        source = tmp_path / f"{source_provenance.value}.ply"
        GaussianScene(
            [[0, 0, 0]],
            [[1, 0, 0]],
            frame_id=source_frame.frame_id,
            units=source_frame.units.value,
        ).save_ply(source, flavor="3dgs")

        manifest = reconstruct(
            photos_dir=tmp_path / "photos",
            out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web",
            engine="import",
            reg_engine="colmap",
            splat_map=[SplatInput(
                session_id="s0",
                path=str(source),
                source_frame=source_frame,
            )],
            dedup_voxel=0,
        )

        assert manifest["provenance"]["synthetic"] is expected_synthetic
        assert manifest["provenance"]["geometry_usability"] == expected_usability
        assert (
            manifest["coordinate_contract"]["ancestry"][0]["source_frame"]["provenance"]
            == source_provenance.value
        )

    def test_scene_history_rejects_non_composable_sibling_transform(self):
        transform_a = FrameTransform(
            source_frame="scan-A",
            target_frame="world",
            sim3=Sim3(scale=2.0),
            method="external-sim3",
        )
        transform_b = FrameTransform(
            source_frame="scan-B",
            target_frame="world",
            sim3=Sim3(scale=3.0),
            method="external-sim3",
        )
        scene = GaussianScene(
            [[0, 0, 0]],
            [[1, 0, 0]],
            frame_id="world",
            units="meters",
            applied_transform_ids=[
                transform_b.transform_id,
                transform_a.transform_id,
            ],
        )

        with pytest.raises(ValueError, match="history.*(composable|continuous|target frame)"):
            _validate_scene_history(
                scene,
                {
                    transform_a.transform_id: transform_a,
                    transform_b.transform_id: transform_b,
                },
                label="adversarial scene",
            )

    def test_global_transform_extends_every_branch_with_topological_union(
        self, tmp_path
    ):
        transform_a = FrameTransform(
            source_frame="scan-A",
            target_frame="world",
            sim3=Sim3(scale=2.0),
            method="external-sim3",
        )
        transform_b = FrameTransform(
            source_frame="scan-B",
            target_frame="world",
            sim3=Sim3(scale=3.0),
            method="external-sim3",
        )
        transform_c = FrameTransform(
            source_frame="world",
            target_frame="published-world",
            sim3=Sim3(t_xyz=[10, 0, 0]),
            method="external-sim3",
        )
        scene = GaussianScene(
            [[0, 0, 0]],
            [[1, 0, 0]],
            frame_id="world",
            units="meters",
            applied_transform_ids=[
                transform_a.transform_id,
                transform_b.transform_id,
            ],
            applied_transform_paths=[
                [transform_a.transform_id],
                [transform_b.transform_id],
            ],
        )

        scene.apply_frame_transform(transform_c, target_units="meters")

        assert scene.applied_transform_paths == [
            [transform_a.transform_id, transform_c.transform_id],
            [transform_b.transform_id, transform_c.transform_id],
        ]
        assert scene.applied_transform_ids == [
            transform_a.transform_id,
            transform_b.transform_id,
            transform_c.transform_id,
        ]
        _validate_scene_history(
            scene,
            {
                transform_a.transform_id: transform_a,
                transform_b.transform_id: transform_b,
                transform_c.transform_id: transform_c,
            },
            label="globally transformed branched scene",
        )
        output = tmp_path / "globally-transformed.ply"
        scene.save_ply(output, flavor="3dgs")
        loaded = GaussianScene.load_ply(output)
        assert loaded.applied_transform_ids == scene.applied_transform_ids
        assert loaded.applied_transform_paths == scene.applied_transform_paths

    def test_full_artifact_reports_high_order_sh_and_extra_attributes(
        self, photos_dir, tmp_path
    ):
        scene = GaussianScene(
            [[0, 0, 0], [1, 2, 3]],
            [[0.2, 0.4, 0.6], [0.3, 0.5, 0.7]],
            sh_rest=np.arange(90, dtype=np.float64).reshape(2, 45) / 100,
            extra_properties={"confidence": np.array([0.8, 0.9], dtype=np.float32)},
            frame_id="mock-local",
            units="meters",
        )
        source = tmp_path / "degree3.ply"
        scene.save_ply(source, flavor="3dgs")

        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web",
            engine="import",
            reg_engine="mock",
            splat_map=[SplatInput(
                session_id="video_vid_A",
                path=str(source),
                source_frame=_mock_source_frame(),
            )],
            dedup_voxel=0,
        )

        full = manifest["artifacts"]["full_3dgs"]
        assert full["sh_degree"] == 3
        assert "f_rest_44" in full["attributes"]
        assert "confidence" in full["attributes"]

    def test_import_requires_splat_map(self, photos_dir, tmp_path):
        with pytest.raises(ValueError, match="splat"):
            reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r",
                        web_dir=tmp_path / "w", engine="import",
                        reg_engine="mock")

    def test_mock_rejects_splat_map_instead_of_recording_unapplied_transform(
        self, photos_dir, tmp_path
    ):
        with pytest.raises(ValueError, match="mock.*splat|splat.*import"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "r",
                web_dir=tmp_path / "w",
                engine="mock",
                reg_engine="mock",
                splat_map=[SplatInput(
                    session_id="video_vid_A",
                    path="never-read.ply",
                    source_frame=_mock_source_frame(),
                )],
            )

    def test_non_metric_target_requires_zero_dedup_voxel(
        self, photos_dir, tmp_path, monkeypatch
    ):
        from pipeline.recon_schema import (
            AlignmentStatus,
            CameraIntrinsics,
            CameraPose,
            CaptureSession,
            RegistrationResult,
        )

        source = _arbitrary_source_frame()
        session = CaptureSession(
            session_id="s0", kind="photo_batch", source="photos", images=["one.jpg"]
        )
        reg = RegistrationResult(
            engine="colmap",
            pose_frame=source,
            alignment_status=AlignmentStatus.UNALIGNED,
            sessions=[session],
            poses=[CameraPose(
                image="one.jpg",
                session_id="s0",
                quat_wxyz=[1, 0, 0, 0],
                t_xyz=[0, 0, 0],
                intrinsics=CameraIntrinsics.from_fov(32, 32),
            )],
        )
        monkeypatch.setattr("pipeline.reconstruct.register", lambda *args, **kwargs: reg)
        ply = tmp_path / "arbitrary.ply"
        GaussianScene(
            [[0, 0, 0]], [[1, 0, 0]], frame_id="sfm-local", units="arbitrary"
        ).save_ply(ply, flavor="3dgs")

        with pytest.raises(ValueError, match="dedup_voxel.*meters|non-metric"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "r",
                web_dir=tmp_path / "w",
                engine="import",
                reg_engine="colmap",
                splat_map=[SplatInput(
                    session_id="s0", path=str(ply), source_frame=source
                )],
            )

    def test_spatial_parameters_name_target_frame_units(self, photos_dir, tmp_path):
        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "r",
            web_dir=tmp_path / "w",
            engine="mock",
            reg_engine="mock",
            dedup_voxel=0.25,
        )

        assert manifest["spatial_parameters"] == {
            "frame_id": "mock-local",
            "units": "meters",
            "dedup_voxel": 0.25,
            "replace_margin": None,
        }

    def test_import_manifest_audits_only_the_applied_transform(
        self, photos_dir, tmp_path
    ):
        source_frame = _arbitrary_source_frame()
        source = tmp_path / "source.ply"
        GaussianScene(
            [[1, 2, 3]],
            [[1, 0, 0]],
            frame_id=source_frame.frame_id,
            units=source_frame.units.value,
        ).save_ply(source, flavor="3dgs")
        transform = FrameTransform(
            source_frame=source_frame.frame_id,
            target_frame="mock-local",
            sim3=Sim3(scale=2, t_xyz=[10, 0, 0]),
            method="external-sim3",
            evidence=["scale-bar:v1"],
        )

        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "r",
            web_dir=tmp_path / "w",
            engine="import",
            reg_engine="mock",
            splat_map=[SplatInput(
                session_id="video_vid_A",
                path=str(source),
                source_frame=source_frame,
                transform=transform,
            )],
            dedup_voxel=0,
        )
        contract = manifest["coordinate_contract"]

        assert contract["transform_chain"] == []
        assert [step["transform_id"] for step in contract["transform_catalog"]] == [
            transform.transform_id
        ]
        assert contract["applied_transform_ids"] == [transform.transform_id]
        assert contract["ancestry"][0]["applied_transform_ids"] == [
            transform.transform_id
        ]
        assert [
            step["transform_id"]
            for step in contract["ancestry"][0]["transform_path"]
        ] == [transform.transform_id]

    def test_sibling_import_transforms_are_separate_ancestry_paths(
        self, photos_dir, tmp_path
    ):
        source_a = _arbitrary_source_frame().model_copy(
            update={"frame_id": "scan-A"}
        )
        source_b = _arbitrary_source_frame().model_copy(
            update={"frame_id": "scan-B"}
        )
        inputs = []
        expected_ids = {}
        for session_id, source_frame, offset in (
            ("video_vid_A", source_a, 0.0),
            ("photos_batch_0", source_b, 20.0),
        ):
            path = tmp_path / f"{source_frame.frame_id}.ply"
            GaussianScene(
                [[offset, 0, 0]],
                [[1, 0, 0]],
                frame_id=source_frame.frame_id,
                units=source_frame.units.value,
            ).save_ply(path, flavor="3dgs")
            transform = FrameTransform(
                source_frame=source_frame.frame_id,
                target_frame="mock-local",
                sim3=Sim3(scale=2.0),
                method="external-sim3",
                evidence=[f"control:{session_id}"],
            )
            expected_ids[session_id] = transform.transform_id
            inputs.append(SplatInput(
                session_id=session_id,
                path=str(path),
                source_frame=source_frame,
                transform=transform,
            ))

        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web",
            engine="import",
            reg_engine="mock",
            splat_map=inputs,
            dedup_voxel=0,
        )
        contract = manifest["coordinate_contract"]
        ancestry = {
            item["session_id"]: item
            for item in contract["ancestry"]
            if item["kind"] == "import-splat"
        }

        assert contract["transform_chain"] == []
        assert {
            item["transform_id"] for item in contract["transform_catalog"]
        } == set(expected_ids.values())
        for session_id, transform_id in expected_ids.items():
            assert ancestry[session_id]["applied_transform_ids"] == [transform_id]
            assert [
                step["transform_id"]
                for step in ancestry[session_id]["transform_path"]
            ] == [transform_id]


class TestProgressiveSharpen:
    def test_base_scene_region_replaced(self, photos_dir, tmp_path):
        """可变清晰: 二次重建应替换基底场景对应区域而非简单叠加"""
        m1 = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r1",
                         web_dir=tmp_path / "w1", engine="mock",
                         reg_engine="mock")
        base_ply = tmp_path / "r1" / "scene_full.ply"
        m2 = reconstruct(photos_dir=photos_dir, out_dir=tmp_path / "r2",
                         web_dir=tmp_path / "w2", engine="mock",
                         reg_engine="mock", base_scene=base_ply)
        # 同一输入的新重建覆盖同一区域 → 总量不应翻倍
        assert m2["gaussian_count"] < m1["gaussian_count"] * 1.5

    def test_base_scene_unknown_transform_history_fails_closed(
        self, photos_dir, tmp_path
    ):
        base = GaussianScene(
            [[0, 0, 0]],
            [[1, 0, 0]],
            frame_id="mock-local",
            units="meters",
            applied_transform_ids=["xf-history-without-definition"],
        )
        base_path = tmp_path / "base.ply"
        base.save_ply(base_path, flavor="3dgs")

        with pytest.raises(ValueError, match="base.*history|transform.*definition"):
            reconstruct(
                photos_dir=photos_dir,
                out_dir=tmp_path / "r",
                web_dir=tmp_path / "w",
                engine="mock",
                reg_engine="mock",
                base_scene=base_path,
            )

    def test_bare_base_scene_without_provenance_stays_preview_only(
        self, tmp_path, monkeypatch
    ):
        measured_frame = CoordinateFrame(
            frame_id="shared-world",
            handedness=Handedness.RIGHT,
            axes=AxisConvention.LOCAL_Z_UP,
            units=CoordinateUnits.METERS,
            metric_status=MetricStatus.METRIC,
            geo_aligned=GeoAlignment.UNALIGNED,
            provenance=FrameProvenance.MEASURED,
            evidence=["survey-control:v1"],
        )
        session = CaptureSession(
            session_id="s0", kind="photo_batch", source="photos", images=[]
        )
        registration = RegistrationResult(
            engine="colmap",
            pose_frame=measured_frame,
            alignment_status=AlignmentStatus.UNALIGNED,
            sessions=[session],
            poses=[],
        )
        monkeypatch.setattr(
            "pipeline.reconstruct.register", lambda *args, **kwargs: registration
        )
        base_path = tmp_path / "uncontracted-base.ply"
        GaussianScene(
            [[0, 0, 0]],
            [[0.4, 0.5, 0.6]],
            frame_id=measured_frame.frame_id,
            units=measured_frame.units.value,
        ).save_ply(base_path, flavor="3dgs")
        source_path = tmp_path / "measured-new.ply"
        GaussianScene(
            [[10, 0, 0]],
            [[0.8, 0.3, 0.2]],
            frame_id=measured_frame.frame_id,
            units=measured_frame.units.value,
        ).save_ply(source_path, flavor="3dgs")

        manifest = reconstruct(
            photos_dir=tmp_path / "photos",
            out_dir=tmp_path / "recon",
            web_dir=tmp_path / "web",
            engine="import",
            reg_engine="colmap",
            splat_map=[SplatInput(
                session_id="s0",
                path=str(source_path),
                source_frame=measured_frame,
            )],
            base_scene=base_path,
            dedup_voxel=0,
            replace_margin=0,
        )

        assert manifest["provenance"]["synthetic"] is False
        assert manifest["provenance"]["geometry_usability"] == "preview-only"
        base_ancestry = manifest["coordinate_contract"]["ancestry"][0]
        assert base_ancestry["kind"] == "base-scene"
        assert base_ancestry["source_frame"]["provenance"] == "unknown"

    def test_branched_output_can_be_reused_as_base_scene(
        self, photos_dir, tmp_path
    ):
        inputs = []
        expected_paths = []
        for session_id, frame_id, offset in (
            ("video_vid_A", "scan-A", 0.0),
            ("photos_batch_0", "scan-B", 20.0),
        ):
            source_frame = _arbitrary_source_frame().model_copy(
                update={"frame_id": frame_id}
            )
            source_path = tmp_path / f"{frame_id}.ply"
            GaussianScene(
                [[offset, 0, 0]],
                [[0.7, 0.3, 0.2]],
                frame_id=frame_id,
                units=source_frame.units.value,
            ).save_ply(source_path, flavor="3dgs")
            transform = FrameTransform(
                source_frame=frame_id,
                target_frame="mock-local",
                sim3=Sim3(scale=2.0),
                method="external-sim3",
                evidence=[f"control:{session_id}"],
            )
            inputs.append(SplatInput(
                session_id=session_id,
                path=str(source_path),
                source_frame=source_frame,
                transform=transform,
            ))
            expected_paths.append([transform.transform_id])

        reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "r1",
            web_dir=tmp_path / "w1",
            engine="import",
            reg_engine="mock",
            splat_map=inputs,
            dedup_voxel=0,
        )
        base_path = tmp_path / "r1" / "scene_full.ply"
        loaded = GaussianScene.load_ply(base_path)
        assert loaded.applied_transform_paths == expected_paths

        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "r2",
            web_dir=tmp_path / "w2",
            engine="import",
            reg_engine="mock",
            splat_map=inputs,
            base_scene=base_path,
            dedup_voxel=0,
            replace_margin=0,
        )

        base_ancestry = manifest["coordinate_contract"]["ancestry"][0]
        assert base_ancestry["kind"] == "base-scene"
        assert [
            [step["transform_id"] for step in path]
            for path in base_ancestry["transform_paths"]
        ] == expected_paths
        assert "transform_path" not in base_ancestry

    def test_manifest_transform_chain_ids_match_applied_ids_and_ancestry(
        self, photos_dir, tmp_path
    ):
        manifest = reconstruct(
            photos_dir=photos_dir,
            out_dir=tmp_path / "r",
            web_dir=tmp_path / "w",
            engine="mock",
            reg_engine="mock",
        )
        contract = manifest["coordinate_contract"]
        chain_ids = [step["transform_id"] for step in contract["transform_chain"]]
        ancestry_ids = list(dict.fromkeys(
            transform_id
            for ancestor in contract["ancestry"]
            for transform_id in ancestor["applied_transform_ids"]
        ))

        assert chain_ids == contract["applied_transform_ids"] == ancestry_ids


class TestVideoIngest:
    def test_video_frames_extracted(self, tmp_path):
        """真实视频文件 → 抽帧 (cv2), 照片 → 复制, 混合输入"""
        cv2 = pytest.importorskip("cv2")
        from pipeline.ingest import ingest_all

        src = tmp_path / "input"
        src.mkdir()
        # 合成 2 秒 12fps 视频
        vw = cv2.VideoWriter(str(src / "clip.mp4"),
                             cv2.VideoWriter_fourcc(*"mp4v"), 12, (128, 96))
        rng = np.random.default_rng(9)
        for _ in range(24):
            vw.write(rng.integers(0, 255, (96, 128, 3), dtype=np.uint8))
        vw.release()
        # 一张照片
        from PIL import Image
        Image.fromarray(rng.integers(0, 255, (96, 128, 3), dtype=np.uint8)
                        ).save(src / "photo.jpg")

        out = tmp_path / "photos"
        result = ingest_all(src, out, fps=4, blur_threshold=0)
        assert result["total_output"] >= 5
        assert (out / "photo.jpg").exists()
        frames = list((out / "clip.mp4.frames").glob("*.jpg"))
        assert len(frames) >= 4  # 2s * 4fps ≈ 8 帧
