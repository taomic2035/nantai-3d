"""Fail-closed tests for the additive exact-218 production caller."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.elevated_topology import (
    build_elevated_topology_plan,
)
from pipeline.synthetic_village.production_journal import (
    ProductionArtifactRecord,
    expected_production_artifacts,
    production_render_id,
)
from pipeline.synthetic_village.production_preflight import (
    PRODUCTION_CLEARANCE_SAMPLE_POINTS,
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    ProductionClearanceRayEvidence,
)
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
)
from pipeline.synthetic_village.production_quality_gates import (
    ProductionFrameLayerStatistics,
    ProductionFrameQualityPolicyV2,
    candidate_synthetic_village_frame_quality_policy_v2,
    production_frame_quality_policy_v2_sha256,
)
from pipeline.synthetic_village.production_render import (
    LocalProductionQualityPolicy,
)
from pipeline.synthetic_village.reciprocal_route_production import (
    RECIPROCAL_BUILD_ADAPTER,
    RECIPROCAL_CAMERA_METADATA_SCHEMA,
    RECIPROCAL_CLEARANCE_REPORT_SCHEMA,
    RECIPROCAL_CLEARANCE_REQUEST_SCHEMA,
    RECIPROCAL_RENDER_REPORT_SCHEMA,
    RECIPROCAL_RENDER_REQUEST_SCHEMA,
    ReciprocalProductionCameraJournal,
    ReciprocalProductionCameraMetadata,
    ReciprocalProductionCameraResult,
    ReciprocalProductionClearanceReport,
    ReciprocalProductionClearanceRequest,
    ReciprocalProductionError,
    ReciprocalProductionRenderFrameReport,
    ReciprocalProductionRenderFrameRequest,
    ReciprocalRenderStatistics,
    VerifiedReciprocalProductionBuild,
    build_reciprocal_production_clearance_report,
    build_reciprocal_production_clearance_request,
    build_reciprocal_production_frame_request,
    canonical_reciprocal_production_camera_metadata_bytes,
    canonical_reciprocal_production_clearance_report_bytes,
    canonical_reciprocal_production_clearance_request_bytes,
    canonical_reciprocal_production_render_report_bytes,
    canonical_reciprocal_production_render_request_bytes,
    load_reciprocal_production_render_report,
    reciprocal_object_registry_sha256,
    require_exact_reciprocal_object_registry,
    require_reciprocal_visible_instances,
    run_reciprocal_production_camera,
    verify_reciprocal_production_build,
    verify_reciprocal_production_clearance_report,
    verify_reciprocal_production_render_frame,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


def _registry(count: int) -> tuple[canary.ObjectRegistryEntry, ...]:
    return tuple(
        canary.ObjectRegistryEntry(
            object_id=f"test-object-{instance_id:03d}",
            instance_id=instance_id,
            semantic_id=3,
            material_id=1,
            variant_id=None,
        )
        for instance_id in range(1, count + 1)
    )


def _post_render_policy() -> ProductionFrameQualityPolicyV2:
    return candidate_synthetic_village_frame_quality_policy_v2(
        minimum_valid_depth_pixel_ratio=0.0,
        minimum_valid_normal_pixel_ratio=0.0,
        minimum_valid_semantic_pixel_ratio=0.0,
        maximum_sky_pixel_ratio=1.0,
        maximum_upper_ground_pixel_ratio=1.0,
        maximum_near_depth_pixel_ratio=1.0,
        maximum_near_instance_dominance_ratio=1.0,
        maximum_upper_instance_dominance_ratio=1.0,
        near_depth_m=2.0,
        upper_region_end_row_exclusive=288,
        ground_semantic_ids=(1,),
    )


def test_required_role_instances_must_all_be_visible() -> None:
    statistics = ReciprocalRenderStatistics(
        depth_min_m=0.0,
        depth_max_m=10.0,
        depth_background_pixels=1,
        depth_max_range_error_m=0.0,
        normal_max_unit_error=0.0,
        instance_ids=(0, 176, 177, 178),
        semantic_ids=(0, 7),
    )

    require_reciprocal_visible_instances(
        statistics,
        required_visible_instance_ids=(176, 177, 178),
    )
    with pytest.raises(ReciprocalProductionError, match="not visible"):
        require_reciprocal_visible_instances(
            statistics,
            required_visible_instance_ids=(176, 179),
        )


@pytest.mark.parametrize(
    "required",
    ((), (176, 176), (0,), (219,), (177, 176)),
)
def test_required_role_instance_contract_is_exact(required: tuple[int, ...]) -> None:
    statistics = ReciprocalRenderStatistics(
        depth_min_m=0.0,
        depth_max_m=10.0,
        depth_background_pixels=1,
        depth_max_range_error_m=0.0,
        normal_max_unit_error=0.0,
        instance_ids=(0, 176, 177),
        semantic_ids=(0, 7),
    )

    with pytest.raises(ReciprocalProductionError, match="required visible"):
        require_reciprocal_visible_instances(
            statistics,
            required_visible_instance_ids=required,
        )


def _clearance_policy() -> ProductionClearancePolicy:
    return ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )


def test_exact_reciprocal_registry_is_content_addressed() -> None:
    registry = _registry(218)

    require_exact_reciprocal_object_registry(registry)

    assert reciprocal_object_registry_sha256(registry) == hashlib.sha256(
        canary._canonical_json_bytes(  # noqa: SLF001
            [row.model_dump(mode="json") for row in registry],
        ),
    ).hexdigest()


@pytest.mark.parametrize("count", (130, 175, 217, 219))
def test_reciprocal_registry_rejects_non_218_counts(count: int) -> None:
    with pytest.raises(
        ReciprocalProductionError,
        match=r"exact 1\.\.218",
    ):
        require_exact_reciprocal_object_registry(_registry(count))


def test_reciprocal_registry_rejects_duplicate_instance_id() -> None:
    registry = list(_registry(218))
    registry[-1] = registry[-1].model_copy(update={"instance_id": 217})

    with pytest.raises(
        ReciprocalProductionError,
        match=r"exact 1\.\.218",
    ):
        require_exact_reciprocal_object_registry(tuple(registry))


def test_verified_build_uses_measured_report_bytes_and_report_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "reciprocal-route-build-report.json"
    report_path.write_bytes(b"canonical-report-bytes\n")
    blend_path = tmp_path / "village-reciprocal-route.blend"
    blend_path.write_bytes(b"measured-blend")
    registry = _registry(218)
    report = SimpleNamespace(
        build_id="a" * 64,
        base_build_report_sha256="b" * 64,
        reciprocal_route_module_plan_sha256="c" * 64,
        object_registry=registry,
        artifact=SimpleNamespace(
            name=blend_path.name,
            sha256=hashlib.sha256(blend_path.read_bytes()).hexdigest(),
        ),
    )
    calls: list[tuple[object, object, Path]] = []

    monkeypatch.setattr(
        "pipeline.synthetic_village.reciprocal_route_production."
        "load_reciprocal_route_build_report",
        lambda path: report,
    )
    monkeypatch.setattr(
        "pipeline.synthetic_village.reciprocal_route_production."
        "verify_reciprocal_route_build_report",
        lambda loaded, *, request, output_path: calls.append(
            (loaded, request, output_path),
        ),
    )
    runtime_request = object()

    verified = verify_reciprocal_production_build(
        report_path=report_path,
        runtime_request=runtime_request,
    )

    assert calls == [(report, runtime_request, blend_path)]
    assert verified.report_sha256 == hashlib.sha256(
        report_path.read_bytes(),
    ).hexdigest()
    assert verified.blend_sha256 == report.artifact.sha256
    assert verified.environment_module_build_report_sha256 == "b" * 64
    assert verified.reciprocal_route_module_plan_sha256 == "c" * 64
    assert verified.object_registry == registry


def test_frame_request_binds_exact_218_registry_and_transitive_report() -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    policy = _post_render_policy()

    request = build_reciprocal_production_frame_request(
        plan=plan,
        camera_id="camera-ground-route-011",
        build_id="1" * 64,
        blender_executable_sha256="2" * 64,
        renderer_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=_registry(218),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),  # noqa: SLF001
        preflight_id="8" * 64,
        quality_policy_sha256="9" * 64,
        post_render_policy=policy,
    )

    assert request.schema_version == RECIPROCAL_RENDER_REQUEST_SCHEMA
    assert request.build_adapter == RECIPROCAL_BUILD_ADAPTER
    assert request.object_registry_sha256 == reciprocal_object_registry_sha256(
        request.object_registry,
    )
    assert request.render_id == production_render_id(
        plan,
        blender_executable_sha256="2" * 64,
        renderer_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        camera_registry_sha256=request.camera_registry_sha256,
        preflight_id="8" * 64,
        quality_policy_sha256="9" * 64,
        post_render_policy_sha256=(
            production_frame_quality_policy_v2_sha256(policy)
        ),
        build_adapter=RECIPROCAL_BUILD_ADAPTER,
        environment_module_build_report_sha256="6" * 64,
    )
    assert canonical_reciprocal_production_render_request_bytes(request).endswith(
        b"\n",
    )


def test_frame_request_rejects_changed_transitive_report_sha() -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    request = build_reciprocal_production_frame_request(
        plan=plan,
        camera_id="camera-ground-route-011",
        build_id="1" * 64,
        blender_executable_sha256="2" * 64,
        renderer_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=_registry(218),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),  # noqa: SLF001
        preflight_id="8" * 64,
        quality_policy_sha256="9" * 64,
        post_render_policy=_post_render_policy(),
    )
    payload = request.model_dump(mode="json")
    payload["environment_module_build_report_sha256"] = "a" * 64

    with pytest.raises(ValueError, match="render ID"):
        ReciprocalProductionRenderFrameRequest.model_validate_json(
            json.dumps(payload),
        )


def test_clearance_request_binds_exact_218_build_lineage() -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)

    request = build_reciprocal_production_clearance_request(
        plan=plan,
        selected_camera_ids=("camera-ground-route-011",),
        build_id="1" * 64,
        blender_executable_sha256="2" * 64,
        preflight_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=_registry(218),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),  # noqa: SLF001
        policy=_clearance_policy(),
    )

    assert request.schema_version == RECIPROCAL_CLEARANCE_REQUEST_SCHEMA
    assert request.environment_module_build_report_sha256 == "6" * 64
    assert request.reciprocal_route_module_plan_sha256 == "7" * 64
    assert canonical_reciprocal_production_clearance_request_bytes(
        request,
    ).endswith(b"\n")

    payload = request.model_dump(mode="json")
    payload["environment_module_build_report_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="preflight ID"):
        ReciprocalProductionClearanceRequest.model_validate_json(
            json.dumps(payload),
        )


def test_clearance_report_round_trip_verifies_request_lineage() -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    request = build_reciprocal_production_clearance_request(
        plan=plan,
        selected_camera_ids=("camera-ground-route-011",),
        build_id="1" * 64,
        blender_executable_sha256="2" * 64,
        preflight_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=_registry(218),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),  # noqa: SLF001
        policy=_clearance_policy(),
    )
    evidence = ProductionCameraClearanceEvidence(
        camera_id="camera-ground-route-011",
        rays=tuple(
            ProductionClearanceRayEvidence(
                sample_x=sample_x,
                sample_y=sample_y,
                hit=False,
            )
            for sample_x, sample_y in PRODUCTION_CLEARANCE_SAMPLE_POINTS
        ),
    )

    report = build_reciprocal_production_clearance_report(
        request,
        evidence=(evidence,),
    )

    assert report.schema_version == RECIPROCAL_CLEARANCE_REPORT_SCHEMA
    assert report.environment_module_build_report_sha256 == "6" * 64
    assert report.reciprocal_route_module_plan_sha256 == "7" * 64
    verify_reciprocal_production_clearance_report(report, request=request)
    assert ReciprocalProductionClearanceReport.model_validate_json(
        canonical_reciprocal_production_clearance_report_bytes(report),
    ) == report


def test_clearance_report_rejects_changed_lineage() -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    request = build_reciprocal_production_clearance_request(
        plan=plan,
        selected_camera_ids=("camera-ground-route-011",),
        build_id="1" * 64,
        blender_executable_sha256="2" * 64,
        preflight_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=_registry(218),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),  # noqa: SLF001
        policy=_clearance_policy(),
    )
    evidence = ProductionCameraClearanceEvidence(
        camera_id="camera-ground-route-011",
        rays=tuple(
            ProductionClearanceRayEvidence(
                sample_x=sample_x,
                sample_y=sample_y,
                hit=False,
            )
            for sample_x, sample_y in PRODUCTION_CLEARANCE_SAMPLE_POINTS
        ),
    )
    report = build_reciprocal_production_clearance_report(
        request,
        evidence=(evidence,),
    ).model_copy(
        update={"environment_module_build_report_sha256": "a" * 64},
    )

    with pytest.raises(ReciprocalProductionError, match="identity disagrees"):
        verify_reciprocal_production_clearance_report(report, request=request)


def test_reciprocal_render_output_schemas_are_additive() -> None:
    assert (
        ReciprocalProductionRenderFrameReport.model_fields[
            "schema_version"
        ].default
        == RECIPROCAL_RENDER_REPORT_SCHEMA
    )
    assert (
        ReciprocalProductionCameraMetadata.model_fields[
            "schema_version"
        ].default
        == RECIPROCAL_CAMERA_METADATA_SCHEMA
    )


def test_reciprocal_render_statistics_accept_instance_218() -> None:
    statistics = ReciprocalRenderStatistics(
        depth_min_m=0.0,
        depth_max_m=10.0,
        depth_background_pixels=1,
        depth_max_range_error_m=0.0,
        normal_max_unit_error=0.0,
        instance_ids=(0, 176, 218),
        semantic_ids=(0, 3, 7),
    )

    assert statistics.instance_ids == (0, 176, 218)

    with pytest.raises(ValueError, match="0 through 130"):
        canary.RenderStatistics.model_validate(
            statistics.model_dump(mode="python"),
        )


def test_render_report_loader_and_artifact_verifier(
    tmp_path: Path,
) -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    request = build_reciprocal_production_frame_request(
        plan=plan,
        camera_id="camera-ground-route-011",
        build_id="1" * 64,
        blender_executable_sha256="2" * 64,
        renderer_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=_registry(218),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),  # noqa: SLF001
        preflight_id="8" * 64,
        quality_policy_sha256="9" * 64,
        post_render_policy=_post_render_policy(),
    )
    artifacts = []
    for index, (kind, portable_path) in enumerate(
        expected_production_artifacts(request.camera.camera_id),
        start=1,
    ):
        path = tmp_path / portable_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"artifact-{index}".encode())
        artifacts.append(
            ProductionArtifactRecord(
                kind=kind,
                path=portable_path,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                size_bytes=path.stat().st_size,
            ),
        )
    payload = {
        "schema_version": RECIPROCAL_RENDER_REPORT_SCHEMA,
        "build_id": request.build_id,
        "render_id": request.render_id,
        "content_sha256": "0" * 64,
        "synthetic": True,
        "verification_level": "L0",
        "fidelity": "simplified-pbr-not-render-parity",
        "blender_executable_sha256": request.blender_executable_sha256,
        "camera_id": request.camera.camera_id,
        "image_width_px": 1024,
        "image_height_px": 576,
        "depth_encoding": "euclidean-camera-center-range-m",
        "normal_encoding": "world-space-unit-vector",
        "depth_channel_layout": "V-float32-zip",
        "normal_channel_layout": "X,Y,Z-float32-zip",
        "instance_pixel_type": "uint16-grayscale-png",
        "semantic_pixel_type": "uint8-grayscale-png",
        "settings_sha256": hashlib.sha256(
            canary._canonical_json_bytes(  # noqa: SLF001
                request.settings.model_dump(mode="json"),
            ),
        ).hexdigest(),
        "artifacts": tuple(artifacts),
        "statistics": ReciprocalRenderStatistics(
            depth_min_m=0.0,
            depth_max_m=10.0,
            depth_background_pixels=1,
            depth_max_range_error_m=0.0,
            normal_max_unit_error=0.0,
            instance_ids=(0, 218),
            semantic_ids=(0, 3),
        ),
        "layer_statistics": ProductionFrameLayerStatistics(
            camera_id=request.camera.camera_id,
            upper_pixel_count=1024 * 288,
            valid_depth_pixel_count=100,
            valid_normal_pixel_count=100,
            registered_instance_pixel_count=100,
            valid_semantic_pixel_count=100,
            sky_pixel_count=(1024 * 576) - 100,
            upper_ground_pixel_count=0,
            near_depth_pixel_count=0,
            dominant_near_instance_pixel_count=0,
            dominant_upper_instance_pixel_count=0,
        ),
        "validation": canary.RenderValidation(
            dimensions_match=True,
            depth_finite_nonnegative=True,
            depth_camera_range_consistent=True,
            normal_finite_unit_world_space=True,
            instance_ids_registered=True,
            semantic_ids_registered=True,
            camera_metadata_matches=True,
        ),
        "profile_id": request.profile_id,
        "production_plan_sha256": request.production_plan_sha256,
        "camera_registry_sha256": request.camera_registry_sha256,
        "elevated_topology_sha256": request.elevated_topology_sha256,
        "group_id": request.camera.group_id,
        "topology_ref": request.camera.topology_ref,
        "preflight_id": request.preflight_id,
        "quality_policy_sha256": request.quality_policy_sha256,
        "post_render_policy_sha256": request.post_render_policy_sha256,
    }
    unsigned = ReciprocalProductionRenderFrameReport.model_validate(payload)
    payload["content_sha256"] = hashlib.sha256(
        canonical_reciprocal_production_render_report_bytes(
            unsigned,
            exclude_sha256=True,
        ),
    ).hexdigest()
    report = ReciprocalProductionRenderFrameReport.model_validate(payload)
    report_path = tmp_path / "frame-report.json"
    report_path.write_bytes(
        canonical_reciprocal_production_render_report_bytes(report),
    )

    loaded = load_reciprocal_production_render_report(report_path)
    verify_reciprocal_production_render_frame(
        loaded,
        request=request,
        frame_root=tmp_path,
    )

    (tmp_path / artifacts[0].path).write_bytes(b"tampered")
    with pytest.raises(ReciprocalProductionError, match="artifact"):
        verify_reciprocal_production_render_frame(
            loaded,
            request=request,
            frame_root=tmp_path,
        )


def test_runner_does_not_render_or_publish_rejected_preflight(
    tmp_path: Path,
) -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    blend_path = tmp_path / "village-reciprocal-route.blend"
    blend_path.write_bytes(b"verified-blend")
    report_path = tmp_path / "reciprocal-route-build-report.json"
    report_path.write_bytes(b"verified-report\n")
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"verified-blender")
    verified_build = VerifiedReciprocalProductionBuild(
        build_id="1" * 64,
        report_path=report_path,
        report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        blend_path=blend_path,
        blend_sha256=hashlib.sha256(blend_path.read_bytes()).hexdigest(),
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=_registry(218),
    )
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        call = [str(value) for value in args]
        calls.append(call)
        assert "preflight_reciprocal_route_cameras.py" in " ".join(call)
        request_path = Path(call[call.index("--request") + 1])
        output_path = Path(call[call.index("--report") + 1])
        request = ReciprocalProductionClearanceRequest.model_validate_json(
            request_path.read_bytes(),
        )
        evidence = ProductionCameraClearanceEvidence(
            camera_id="camera-ground-route-011",
            rays=tuple(
                ProductionClearanceRayEvidence(
                    sample_x=sample_x,
                    sample_y=sample_y,
                    hit=True,
                    distance_m=1.0,
                    object_name="near-wall",
                    stable_id="test-object-001",
                    semantic_id=3,
                )
                for sample_x, sample_y in PRODUCTION_CLEARANCE_SAMPLE_POINTS
            ),
        )
        output_path.write_bytes(
            canonical_reciprocal_production_clearance_report_bytes(
                build_reciprocal_production_clearance_report(
                    request,
                    evidence=(evidence,),
                ),
            ),
        )
        return subprocess.CompletedProcess(call, 0, "preflight", "")

    output_root = tmp_path / "renders"
    with pytest.raises(ReciprocalProductionError, match="preflight rejected"):
        run_reciprocal_production_camera(
            verified_build=verified_build,
            plan=plan,
            camera_id="camera-ground-route-011",
            blender_executable=executable,
            output_root=output_root,
            clearance_policy=_clearance_policy(),
            quality_policy=LocalProductionQualityPolicy(
                minimum_valid_pixel_ratio=0.05,
            ),
            post_render_policy=_post_render_policy(),
            required_visible_instance_ids=(218,),
            process_runner=fake_run,
            timeout_seconds=30,
        )

    assert len(calls) == 1
    assert not any(output_root.glob("[0-9a-f]*"))


def _write_fake_successful_frame(
    request: ReciprocalProductionRenderFrameRequest,
    frame_root: Path,
) -> None:
    camera_id = request.camera.camera_id
    for directory in ("rgb", "depth", "normal", "instance", "semantic", "cameras"):
        (frame_root / directory).mkdir(parents=True, exist_ok=True)
    for index, (_kind, portable_path) in enumerate(
        expected_production_artifacts(camera_id)[:5],
        start=1,
    ):
        (frame_root / portable_path).write_bytes(
            f"rendered-layer-{index}".encode(),
        )
    metadata = ReciprocalProductionCameraMetadata(
        build_id=request.build_id,
        render_id=request.render_id,
        blender_executable_sha256=request.blender_executable_sha256,
        camera_id=camera_id,
        image_width_px=1024,
        image_height_px=576,
        coordinate_system="opencv-c2w-right-down-forward-meters",
        pixel_origin="top-left",
        pixel_center_offset=(0.5, 0.5),
        depth_encoding="euclidean-camera-center-range-m",
        depth_units="m",
        depth_invalid_value_m=0.0,
        normal_encoding="world-space-unit-vector",
        normal_axes="blender-right-handed-z-up",
        normal_background_xyz=(0.0, 0.0, 0.0),
        clip_start_m=0.1,
        clip_end_m=1200.0,
        depth_channel_layout="V-float32-zip",
        normal_channel_layout="X,Y,Z-float32-zip",
        instance_pixel_type="uint16-grayscale-png",
        semantic_pixel_type="uint8-grayscale-png",
        settings_sha256=hashlib.sha256(
            canary._canonical_json_bytes(  # noqa: SLF001
                request.settings.model_dump(mode="json"),
            ),
        ).hexdigest(),
        intrinsics=request.camera.intrinsics,
        requested_c2w_opencv=request.camera.c2w_opencv,
        requested_c2w_blender=request.requested_c2w_blender,
        measured_c2w_opencv=request.camera.c2w_opencv,
        measured_c2w_blender=request.requested_c2w_blender,
        object_registry_sha256=request.object_registry_sha256,
        semantic_registry=request.semantic_registry,
        profile_id=request.profile_id,
        production_plan_sha256=request.production_plan_sha256,
        camera_registry_sha256=request.camera_registry_sha256,
        elevated_topology_sha256=request.elevated_topology_sha256,
        group_id=request.camera.group_id,
        topology_ref=request.camera.topology_ref,
        arc_length_m=request.camera.arc_length_m,
        audit_only=request.camera.audit_only,
        disclosure=request.camera.disclosure,
        preflight_id=request.preflight_id,
        quality_policy_sha256=request.quality_policy_sha256,
        post_render_policy_sha256=request.post_render_policy_sha256,
    )
    metadata_path = frame_root / f"cameras/{camera_id}.json"
    metadata_path.write_bytes(
        canonical_reciprocal_production_camera_metadata_bytes(metadata),
    )
    artifacts = tuple(
        ProductionArtifactRecord(
            kind=kind,
            path=portable_path,
            sha256=hashlib.sha256(
                (frame_root / portable_path).read_bytes(),
            ).hexdigest(),
            size_bytes=(frame_root / portable_path).stat().st_size,
        )
        for kind, portable_path in expected_production_artifacts(camera_id)
    )
    payload = {
        "schema_version": RECIPROCAL_RENDER_REPORT_SCHEMA,
        "build_id": request.build_id,
        "render_id": request.render_id,
        "content_sha256": "0" * 64,
        "synthetic": True,
        "verification_level": "L0",
        "fidelity": "simplified-pbr-not-render-parity",
        "blender_executable_sha256": request.blender_executable_sha256,
        "camera_id": camera_id,
        "image_width_px": 1024,
        "image_height_px": 576,
        "depth_encoding": "euclidean-camera-center-range-m",
        "normal_encoding": "world-space-unit-vector",
        "depth_channel_layout": "V-float32-zip",
        "normal_channel_layout": "X,Y,Z-float32-zip",
        "instance_pixel_type": "uint16-grayscale-png",
        "semantic_pixel_type": "uint8-grayscale-png",
        "settings_sha256": metadata.settings_sha256,
        "artifacts": artifacts,
        "statistics": ReciprocalRenderStatistics(
            depth_min_m=0.0,
            depth_max_m=10.0,
            depth_background_pixels=1,
            depth_max_range_error_m=0.0,
            normal_max_unit_error=0.0,
            instance_ids=(0, 218),
            semantic_ids=(0, 3),
        ),
        "layer_statistics": ProductionFrameLayerStatistics(
            camera_id=camera_id,
            upper_pixel_count=1024 * 288,
            valid_depth_pixel_count=(1024 * 576) - 1,
            valid_normal_pixel_count=(1024 * 576) - 1,
            registered_instance_pixel_count=100,
            valid_semantic_pixel_count=100,
            sky_pixel_count=(1024 * 576) - 100,
            upper_ground_pixel_count=0,
            near_depth_pixel_count=0,
            dominant_near_instance_pixel_count=0,
            dominant_upper_instance_pixel_count=0,
        ),
        "validation": canary.RenderValidation(
            dimensions_match=True,
            depth_finite_nonnegative=True,
            depth_camera_range_consistent=True,
            normal_finite_unit_world_space=True,
            instance_ids_registered=True,
            semantic_ids_registered=True,
            camera_metadata_matches=True,
        ),
        "profile_id": request.profile_id,
        "production_plan_sha256": request.production_plan_sha256,
        "camera_registry_sha256": request.camera_registry_sha256,
        "elevated_topology_sha256": request.elevated_topology_sha256,
        "group_id": request.camera.group_id,
        "topology_ref": request.camera.topology_ref,
        "preflight_id": request.preflight_id,
        "quality_policy_sha256": request.quality_policy_sha256,
        "post_render_policy_sha256": request.post_render_policy_sha256,
    }
    unsigned = ReciprocalProductionRenderFrameReport.model_validate(payload)
    payload["content_sha256"] = hashlib.sha256(
        canonical_reciprocal_production_render_report_bytes(
            unsigned,
            exclude_sha256=True,
        ),
    ).hexdigest()
    report = ReciprocalProductionRenderFrameReport.model_validate(payload)
    (frame_root / "frame-report.json").write_bytes(
        canonical_reciprocal_production_render_report_bytes(report),
    )


def test_runner_publishes_verified_one_camera_bundle(tmp_path: Path) -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    blend_path = tmp_path / "village-reciprocal-route.blend"
    blend_path.write_bytes(b"verified-blend")
    report_path = tmp_path / "reciprocal-route-build-report.json"
    report_path.write_bytes(b"verified-report\n")
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"verified-blender")
    verified_build = VerifiedReciprocalProductionBuild(
        build_id="1" * 64,
        report_path=report_path,
        report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        blend_path=blend_path,
        blend_sha256=hashlib.sha256(blend_path.read_bytes()).hexdigest(),
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=_registry(218),
    )
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        call = [str(value) for value in args]
        calls.append(call)
        request_path = Path(call[call.index("--request") + 1])
        if "--report" in call:
            request = ReciprocalProductionClearanceRequest.model_validate_json(
                request_path.read_bytes(),
            )
            evidence = ProductionCameraClearanceEvidence(
                camera_id="camera-ground-route-011",
                rays=tuple(
                    ProductionClearanceRayEvidence(
                        sample_x=sample_x,
                        sample_y=sample_y,
                        hit=False,
                    )
                    for sample_x, sample_y in PRODUCTION_CLEARANCE_SAMPLE_POINTS
                ),
            )
            Path(call[call.index("--report") + 1]).write_bytes(
                canonical_reciprocal_production_clearance_report_bytes(
                    build_reciprocal_production_clearance_report(
                        request,
                        evidence=(evidence,),
                    ),
                ),
            )
        else:
            request = ReciprocalProductionRenderFrameRequest.model_validate_json(
                request_path.read_bytes(),
            )
            frame_root = Path(call[call.index("--staging") + 1])
            frame_root.mkdir()
            _write_fake_successful_frame(request, frame_root)
        return subprocess.CompletedProcess(call, 0, "ok", "")

    result = run_reciprocal_production_camera(
        verified_build=verified_build,
        plan=plan,
        camera_id="camera-ground-route-011",
        blender_executable=executable,
        output_root=tmp_path / "renders",
        clearance_policy=_clearance_policy(),
        quality_policy=LocalProductionQualityPolicy(
            minimum_valid_pixel_ratio=0.05,
        ),
        post_render_policy=_post_render_policy(),
        required_visible_instance_ids=(218,),
        process_runner=fake_run,
        timeout_seconds=30,
    )

    assert isinstance(result, ReciprocalProductionCameraResult)
    assert len(calls) == 2
    assert result.frame_root.is_dir()
    assert (result.frame_root / "frame-report.json").is_file()
    assert (result.frame_root / "evidence/quality-request.json").is_file()
    assert (result.frame_root / "evidence/quality-report.json").is_file()
    assert (result.frame_root / "evidence/journal.json").is_file()

    journal_path = result.frame_root / "evidence/journal.json"
    forged = json.loads(journal_path.read_text(encoding="utf-8"))
    forged["artifacts"][0]["path"] = "rgb/foreign-camera.png"
    unsigned = {key: value for key, value in forged.items() if key != "journal_sha256"}
    forged["journal_sha256"] = hashlib.sha256(
        (json.dumps(unsigned, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8",
        ),
    ).hexdigest()
    with pytest.raises(ValueError, match="artifact contract"):
        ReciprocalProductionCameraJournal.model_validate_json(
            json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True),
        )
