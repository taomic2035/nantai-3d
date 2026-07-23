"""Batch 22 exact-218 local waterwheel orbit caller."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.elevated_topology import build_elevated_topology_plan
from pipeline.synthetic_village.environment_module import (
    build_default_environment_module_plan,
    environment_module_plan_sha256,
)
from pipeline.synthetic_village.local_orbit_audit import (
    LocalOrbitAuditPlan,
    build_waterwheel_local_orbit_plan,
    local_orbit_plan_sha256,
    materialize_local_orbit_render_plan,
)
from pipeline.synthetic_village.local_orbit_runner import (
    LocalOrbitAuditReport,
    LocalOrbitFrameEvidence,
    LocalOrbitInstancePixelCount,
    LocalOrbitRenderFrameRequest,
    _remove_local_orbit_staging,
    build_local_orbit_audit_report,
    build_local_orbit_render_frame_request,
    canonical_local_orbit_audit_report_bytes,
    decode_local_orbit_instance_mask,
    run_local_orbit_audit,
    validate_local_orbit_build_bindings,
)
from pipeline.synthetic_village.production_journal import (
    ProductionArtifactRecord,
    expected_production_artifacts,
)
from pipeline.synthetic_village.production_preflight import (
    PRODUCTION_CLEARANCE_SAMPLE_POINTS,
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    ProductionClearanceRayEvidence,
)
from pipeline.synthetic_village.production_profile import build_production_camera_plan
from pipeline.synthetic_village.production_quality_gates import (
    ProductionFrameLayerStatistics,
    ProductionFrameQualityPolicyV2,
    candidate_synthetic_village_frame_quality_policy_v2,
)
from pipeline.synthetic_village.production_render import LocalProductionQualityPolicy
from pipeline.synthetic_village.reciprocal_route_production import (
    RECIPROCAL_RENDER_REPORT_SCHEMA,
    ReciprocalProductionCameraMetadata,
    ReciprocalProductionClearanceRequest,
    ReciprocalProductionError,
    ReciprocalProductionRenderFrameReport,
    ReciprocalRenderStatistics,
    VerifiedReciprocalProductionBuild,
    build_reciprocal_production_clearance_report,
    canonical_reciprocal_production_camera_metadata_bytes,
    canonical_reciprocal_production_clearance_report_bytes,
    canonical_reciprocal_production_render_report_bytes,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


def _plan() -> LocalOrbitAuditPlan:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    source_plan = build_production_camera_plan(scene, topology)
    environment_plan = build_default_environment_module_plan(
        scene=scene,
        elevated_topology=topology,
    )
    return build_waterwheel_local_orbit_plan(
        source_plan=source_plan,
        environment_module_plan_sha256=environment_module_plan_sha256(
            environment_plan,
        ),
        exact_build_id="b" * 64,
        exact_blend_sha256="c" * 64,
        anchor_m=(-185.2, -115.0, 43.15),
    )


def _source_plan():
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    return build_production_camera_plan(scene, topology)


def _registry() -> tuple[canary.ObjectRegistryEntry, ...]:
    return tuple(
        canary.ObjectRegistryEntry(
            object_id=f"test-object-{instance_id:03d}",
            instance_id=instance_id,
            semantic_id=3,
            material_id=1,
            variant_id=None,
        )
        for instance_id in range(1, 219)
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


def _frame(
    plan: LocalOrbitAuditPlan,
    index: int,
    *,
    wheel_pixels: int = 100,
    render_id: str | None = None,
) -> LocalOrbitFrameEvidence:
    camera = plan.cameras[index]
    return LocalOrbitFrameEvidence(
        orbit_camera_id=camera.orbit_camera_id,
        materialized_camera_id=camera.materialized_camera_id,
        azimuth_deg=camera.azimuth_deg,
        render_id=render_id or f"{index + 1:064x}",
        frame_report_sha256=f"{index + 11:064x}",
        instance_mask_sha256=f"{index + 21:064x}",
        rgb_sha256=f"{index + 31:064x}",
        depth_sha256=f"{index + 41:064x}",
        normal_sha256=f"{index + 51:064x}",
        semantic_sha256=f"{index + 61:064x}",
        camera_metadata_sha256=f"{index + 71:064x}",
        instance_pixel_counts=(
            LocalOrbitInstancePixelCount(instance_id=0, pixel_count=1000),
            LocalOrbitInstancePixelCount(
                instance_id=155,
                pixel_count=wheel_pixels,
            ),
            LocalOrbitInstancePixelCount(instance_id=156, pixel_count=50),
        ),
    )


def _report(
    *,
    wheel_visible_frames: int = 8,
    duplicate_render_id: bool = False,
) -> LocalOrbitAuditReport:
    plan = _plan()
    frames = tuple(
        _frame(
            plan,
            index,
            wheel_pixels=100 if index < wheel_visible_frames else 0,
            render_id=("f" * 64 if duplicate_render_id else None),
        )
        for index in range(8)
    )
    return build_local_orbit_audit_report(
        plan=plan,
        build_report_sha256="d" * 64,
        environment_module_build_report_sha256="e" * 64,
        reciprocal_route_module_plan_sha256="a" * 64,
        frames=frames,
    )


def test_report_binds_exact_eight_accepted_azimuths_and_visibility() -> None:
    report = _report(wheel_visible_frames=6)

    assert report.local_orbit_plan_sha256 == local_orbit_plan_sha256(_plan())
    assert report.azimuth_bins_passed == 8
    assert report.accepted_frame_count == 8
    assert report.assembly_visible_frame_count == 8
    assert report.wheel_visible_frame_count == 6
    assert report.required_instance_ids == (155, 156, 157, 158, 159, 160)
    assert report.training_use == "forbidden-as-multiview"
    assert report.trust_effect == "none-quality-filter-only"
    assert canonical_local_orbit_audit_report_bytes(report).endswith(b"\n")
    payload = report.model_dump(mode="json", exclude={"report_sha256"})
    assert report.report_sha256 == hashlib.sha256(
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8",
        ),
    ).hexdigest()


def test_report_rejects_wheel_visibility_below_six() -> None:
    with pytest.raises(ValidationError, match="wheel.*six"):
        _report(wheel_visible_frames=5)


def test_report_rejects_duplicate_render_id() -> None:
    with pytest.raises(ValidationError, match="render IDs"):
        _report(duplicate_render_id=True)


def test_report_rejects_missing_azimuth() -> None:
    report = _report()
    payload = report.model_dump(mode="json")
    payload["frames"] = payload["frames"][:-1]
    payload["accepted_frame_count"] = 7
    payload["azimuth_bins_passed"] = 7
    payload["assembly_visible_frame_count"] = 7
    payload["wheel_visible_frame_count"] = 7

    with pytest.raises(ValidationError):
        LocalOrbitAuditReport.model_validate_json(json.dumps(payload))


def test_frame_rejects_unregistered_instance_value() -> None:
    plan = _plan()
    payload = _frame(plan, 0).model_dump(mode="json")
    payload["instance_pixel_counts"].append(
        {"instance_id": 219, "pixel_count": 1},
    )

    with pytest.raises(ValidationError):
        LocalOrbitFrameEvidence.model_validate_json(json.dumps(payload))


def _verified_build(tmp_path: Path) -> VerifiedReciprocalProductionBuild:
    blend_path = tmp_path / "village-reciprocal-route.blend"
    blend_path.write_bytes(b"exact-218-blend")
    report_path = tmp_path / "reciprocal-route-build-report.json"
    report_path.write_bytes(b"exact-218-report\n")
    return VerifiedReciprocalProductionBuild(
        build_id="b" * 64,
        report_path=report_path,
        report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        blend_path=blend_path,
        blend_sha256=hashlib.sha256(blend_path.read_bytes()).hexdigest(),
        environment_module_build_report_sha256="e" * 64,
        reciprocal_route_module_plan_sha256="a" * 64,
        object_registry=_registry(),
        role_camera_candidates=(),
    )


@pytest.mark.parametrize(
    "field",
    ["exact_build_id", "exact_blend_sha256", "environment_module_plan_sha256"],
)
def test_build_binding_rejects_any_plan_identity_drift(
    tmp_path: Path,
    field: str,
) -> None:
    verified = _verified_build(tmp_path)
    plan = _plan().model_copy(
        update={
            "exact_blend_sha256": verified.blend_sha256,
            "environment_module_plan_sha256": "9" * 64,
        },
    )
    if field == "exact_build_id":
        plan = plan.model_copy(update={field: "0" * 64})
    elif field == "exact_blend_sha256":
        plan = plan.model_copy(update={field: "0" * 64})
    else:
        plan = plan.model_copy(update={field: "0" * 64})

    with pytest.raises(ReciprocalProductionError, match="local orbit.*binding"):
        validate_local_orbit_build_bindings(
            plan=plan,
            verified_build=verified,
            verified_environment_module_plan_sha256="9" * 64,
        )


def test_instance_mask_decoder_binds_sha_and_registered_values(tmp_path: Path) -> None:
    mask_path = tmp_path / "instance.png"
    pixels = np.array([[0, 155], [156, 155]], dtype=np.uint16)
    Image.fromarray(pixels).save(mask_path)
    digest = hashlib.sha256(mask_path.read_bytes()).hexdigest()

    counts = decode_local_orbit_instance_mask(
        mask_path,
        expected_sha256=digest,
        registered_instance_ids=tuple(range(1, 219)),
    )

    assert counts == (
        LocalOrbitInstancePixelCount(instance_id=0, pixel_count=1),
        LocalOrbitInstancePixelCount(instance_id=155, pixel_count=2),
        LocalOrbitInstancePixelCount(instance_id=156, pixel_count=1),
    )
    with pytest.raises(ReciprocalProductionError, match="mask SHA"):
        decode_local_orbit_instance_mask(
            mask_path,
            expected_sha256="0" * 64,
            registered_instance_ids=tuple(range(1, 219)),
        )


def test_instance_mask_decoder_rejects_unregistered_value(tmp_path: Path) -> None:
    mask_path = tmp_path / "instance.png"
    Image.fromarray(np.array([[219]], dtype=np.uint16)).save(mask_path)

    with pytest.raises(ReciprocalProductionError, match="unregistered"):
        decode_local_orbit_instance_mask(
            mask_path,
            expected_sha256=hashlib.sha256(mask_path.read_bytes()).hexdigest(),
            registered_instance_ids=tuple(range(1, 219)),
        )


def test_render_request_binds_plan_build_camera_and_unique_frame_id() -> None:
    source_plan = _source_plan()
    orbit_plan = _plan()
    derived_plan = materialize_local_orbit_render_plan(source_plan, orbit_plan)
    common = {
        "plan": derived_plan,
        "source_plan": source_plan,
        "local_orbit_plan": orbit_plan,
        "build_id": orbit_plan.exact_build_id,
        "blender_executable_sha256": "1" * 64,
        "renderer_script_sha256": "2" * 64,
        "engine_script_sha256": "3" * 64,
        "blend_sha256": orbit_plan.exact_blend_sha256,
        "build_report_sha256": "4" * 64,
        "environment_module_build_report_sha256": "5" * 64,
        "reciprocal_route_module_plan_sha256": "6" * 64,
        "object_registry": _registry(),
        "auxiliary_registry": canary.AUXILIARY_REGISTRY,
        "semantic_registry": canary._semantic_registry(),  # noqa: SLF001
        "preflight_id": "7" * 64,
        "quality_policy_sha256": "8" * 64,
        "post_render_policy": _post_render_policy(),
    }

    first = build_local_orbit_render_frame_request(
        camera_id="camera-audit-overview-001",
        **common,
    )
    second = build_local_orbit_render_frame_request(
        camera_id="camera-audit-overview-002",
        **common,
    )

    assert first.local_orbit_plan_sha256 == local_orbit_plan_sha256(orbit_plan)
    assert first.orbit_camera_id == "audit-waterwheel-az000"
    assert first.required_visible_instance_ids == (155, 156, 157, 158, 159, 160)
    assert first.production_plan == derived_plan
    assert first.source_production_plan == source_plan
    assert first.render_id != second.render_id


def _clearance_policy() -> ProductionClearancePolicy:
    return ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )


def _write_fake_local_orbit_frame(
    request: LocalOrbitRenderFrameRequest,
    frame_root: Path,
) -> None:
    camera_id = request.camera.camera_id
    for directory in ("rgb", "depth", "normal", "instance", "semantic", "cameras"):
        (frame_root / directory).mkdir(parents=True, exist_ok=True)
    artifacts_contract = expected_production_artifacts(camera_id)
    for index, (kind, portable_path) in enumerate(artifacts_contract[:5], start=1):
        target = frame_root / portable_path
        if kind == "instance-mask":
            orbit_index = int(camera_id.rsplit("-", 1)[1])
            pixels = np.zeros((576, 1024), dtype=np.uint16)
            pixels[:, :512] = 155 if orbit_index <= 6 else 156
            Image.fromarray(pixels).save(target)
        else:
            target.write_bytes(f"rendered-layer-{index}".encode())
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
        for kind, portable_path in artifacts_contract
    )
    observed_instances = (0, 155) if int(camera_id[-3:]) <= 6 else (0, 156)
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
            instance_ids=observed_instances,
            semantic_ids=(0, 3),
        ),
        "layer_statistics": ProductionFrameLayerStatistics(
            camera_id=camera_id,
            upper_pixel_count=1024 * 288,
            valid_depth_pixel_count=(1024 * 576) - 1,
            valid_normal_pixel_count=(1024 * 576) - 1,
            registered_instance_pixel_count=1024 * 288,
            valid_semantic_pixel_count=1024 * 288,
            sky_pixel_count=1024 * 288,
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


def test_runner_executes_preflight_then_render_and_publishes_only_eight_green(
    tmp_path: Path,
) -> None:
    verified = _verified_build(tmp_path)
    source_plan = _source_plan()
    orbit_plan = _plan().model_copy(
        update={
            "exact_build_id": verified.build_id,
            "exact_blend_sha256": verified.blend_sha256,
            "environment_module_plan_sha256": "9" * 64,
        },
    )
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"verified-blender")
    calls: list[tuple[str, str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        call = [str(value) for value in args]
        request_path = Path(call[call.index("--request") + 1])
        if "--report" in call:
            request = ReciprocalProductionClearanceRequest.model_validate_json(
                request_path.read_bytes(),
            )
            camera_id = request.selected_camera_ids[0]
            calls.append((camera_id, "preflight"))
            evidence = ProductionCameraClearanceEvidence(
                camera_id=camera_id,
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
            request = LocalOrbitRenderFrameRequest.model_validate_json(
                request_path.read_bytes(),
            )
            calls.append((request.camera.camera_id, "render"))
            frame_root = Path(call[call.index("--staging") + 1])
            frame_root.mkdir()
            _write_fake_local_orbit_frame(request, frame_root)
        return subprocess.CompletedProcess(call, 0, "ok", "")

    result = run_local_orbit_audit(
        verified_build=verified,
        source_plan=source_plan,
        local_orbit_plan=orbit_plan,
        verified_environment_module_plan_sha256="9" * 64,
        blender_executable=executable,
        output_root=tmp_path / "orbit-output",
        clearance_policy=_clearance_policy(),
        quality_policy=LocalProductionQualityPolicy(
            minimum_valid_pixel_ratio=0.05,
        ),
        post_render_policy=_post_render_policy(),
        process_runner=fake_run,
        timeout_seconds=30,
    )

    expected_ids = tuple(
        f"camera-audit-overview-{index:03d}" for index in range(1, 9)
    )
    assert tuple(camera_id for camera_id, stage in calls if stage == "preflight") == (
        expected_ids
    )
    assert tuple(camera_id for camera_id, stage in calls if stage == "render") == (
        expected_ids
    )
    for index in range(0, len(calls), 2):
        assert calls[index][1] == "preflight"
        assert calls[index + 1][1] == "render"
        assert calls[index][0] == calls[index + 1][0]
    assert result.report.accepted_frame_count == 8
    assert result.report.assembly_visible_frame_count == 8
    assert result.report.wheel_visible_frame_count == 6
    assert result.report_path.is_file()
    assert result.audit_root.is_dir()
    assert not any((tmp_path / "orbit-output").glob(".staging-*"))


def test_runner_removes_outer_staging_when_any_render_fails(tmp_path: Path) -> None:
    verified = _verified_build(tmp_path)
    source_plan = _source_plan()
    orbit_plan = _plan().model_copy(
        update={
            "exact_build_id": verified.build_id,
            "exact_blend_sha256": verified.blend_sha256,
            "environment_module_plan_sha256": "9" * 64,
        },
    )
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"verified-blender")
    output_root = tmp_path / "failed-orbit-output"

    def fail_first_render(args, **kwargs):
        del kwargs
        call = [str(value) for value in args]
        request_path = Path(call[call.index("--request") + 1])
        if "--report" in call:
            request = ReciprocalProductionClearanceRequest.model_validate_json(
                request_path.read_bytes(),
            )
            camera_id = request.selected_camera_ids[0]
            evidence = ProductionCameraClearanceEvidence(
                camera_id=camera_id,
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
            return subprocess.CompletedProcess(call, 0, "ok", "")
        return subprocess.CompletedProcess(call, 17, "", "synthetic failure")

    with pytest.raises(ReciprocalProductionError, match="Blender render failed"):
        run_local_orbit_audit(
            verified_build=verified,
            source_plan=source_plan,
            local_orbit_plan=orbit_plan,
            verified_environment_module_plan_sha256="9" * 64,
            blender_executable=executable,
            output_root=output_root,
            clearance_policy=_clearance_policy(),
            quality_policy=LocalProductionQualityPolicy(
                minimum_valid_pixel_ratio=0.05,
            ),
            post_render_policy=_post_render_policy(),
            process_runner=fail_first_render,
            timeout_seconds=30,
        )

    assert output_root.is_dir()
    assert tuple(output_root.iterdir()) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path cleanup")
def test_staging_cleanup_uses_windows_extended_path_after_long_path_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    staging = tmp_path / ".staging-transient"
    staging.mkdir()
    attempts = []
    extended = []

    def long_path_failure(path: Path, *, parent: Path) -> None:
        attempts.append((path, parent))
        raise OSError(145, "directory not empty")

    def extended_remove(path: Path) -> None:
        extended.append(str(path))
        staging.rmdir()

    monkeypatch.setattr(
        "pipeline.synthetic_village.local_orbit_runner._remove_private_staging",
        long_path_failure,
    )
    monkeypatch.setattr(
        "pipeline.synthetic_village.local_orbit_runner.shutil.rmtree",
        extended_remove,
    )

    _remove_local_orbit_staging(
        staging,
        parent=tmp_path,
        sleep=lambda _: None,
    )

    assert len(attempts) == 1
    assert extended == ["\\\\?\\" + str(staging.absolute())]
    assert not staging.exists()


def test_blender_adapter_validates_local_orbit_boundary_before_engine() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/blender/render_local_orbit.py"
    ).read_text(encoding="utf-8")

    assert "LOCAL_ORBIT_RENDER_REQUEST_SCHEMA" in source
    assert "_validate_local_orbit_boundary" in source
    assert 'request["local_orbit_plan_sha256"]' in source
    assert 'request["source_production_plan_sha256"]' in source
    assert 'request["required_visible_instance_ids"]' in source
    assert 'request["orbit_camera_id"]' in source
    assert "EXPECTED_INSTANCE_IDS = list(range(1, 219))" in source
    assert "WATERWHEEL_ASSEMBLY_INSTANCE_IDS = list(range(155, 161))" in source
    assert "LOCAL_ORBIT_ROTATION_ENTRY_ERROR_LIMIT = 0.0000004" in source
    assert "engine._matrix_within_float32_tolerance = (" in source


def test_blender_adapter_removes_only_bound_local_fields_for_frozen_engine() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/blender/render_local_orbit.py"
    ).read_text(encoding="utf-8")

    for field in (
        "environment_module_build_report_sha256",
        "reciprocal_route_module_plan_sha256",
        "engine_script_sha256",
        "required_visible_instance_ids",
        "source_camera_registry_sha256",
        "source_production_plan",
        "source_production_plan_sha256",
        "local_orbit_plan",
        "local_orbit_plan_sha256",
        "orbit_camera_id",
    ):
        assert f'internal.pop("{field}")' in source
    assert 'internal["build_adapter"] = "windows-textured-v2"' in source
