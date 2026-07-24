"""TDD contract for the exact-266 sixteen-camera closure audit."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import canary
from pipeline.synthetic_village import perimeter_closure_module as closure_module
from pipeline.synthetic_village.perimeter_closure_audit import (
    PERIMETER_CLOSURE_AUDIT_CAMERA_ORDER,
    PerimeterClosureAuditPlan,
    PerimeterClosureCameraMetadata,
    PerimeterClosureRenderFrameReport,
    PerimeterClosureRenderStatistics,
    build_perimeter_closure_audit_plan,
    build_perimeter_closure_clearance_report,
    build_perimeter_closure_clearance_request,
    build_perimeter_closure_render_frame_request,
    canonical_perimeter_closure_audit_plan_bytes,
    canonical_perimeter_closure_camera_metadata_bytes,
    canonical_perimeter_closure_clearance_report_bytes,
    canonical_perimeter_closure_clearance_request_bytes,
    canonical_perimeter_closure_render_report_bytes,
    canonical_perimeter_closure_render_request_bytes,
    load_perimeter_closure_camera_metadata,
    load_perimeter_closure_render_report,
    measure_perimeter_closure_visibility,
    perimeter_closure_audit_plan_sha256,
    perimeter_closure_object_registry_sha256,
    perimeter_closure_renderer_capability_sha256,
    verify_perimeter_closure_audit_plan,
    verify_perimeter_closure_camera_metadata,
    verify_perimeter_closure_clearance_report,
    verify_perimeter_closure_render_frame,
)
from pipeline.synthetic_village.perimeter_closure_module import (
    PERIMETER_CLOSURE_MODULE_ORDER,
    PerimeterClosurePlan,
    build_default_perimeter_closure_plan,
    perimeter_closure_plan_sha256,
)
from pipeline.synthetic_village.production_preflight import (
    PRODUCTION_CLEARANCE_SAMPLE_POINTS,
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    ProductionClearanceRayEvidence,
    production_clearance_policy_sha256,
)
from pipeline.synthetic_village.production_quality_gates import (
    candidate_synthetic_village_frame_quality_policy_v2,
    production_frame_quality_policy_v2_sha256,
)
from pipeline.synthetic_village.production_render import (
    LocalProductionQualityPolicy,
    ProductionArtifactRecord,
    ProductionFrameLayerStatistics,
    expected_production_artifacts,
    local_production_quality_policy_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
BLENDER_AUDIT_SCRIPT = (
    ROOT / "scripts/blender/render_perimeter_closure_audit.py"
)


def _batch24_manifest() -> dict[str, Any]:
    assets = []
    for sector, sources in closure_module._BATCH24_SOURCES.items():
        for kind, (file_name, sha256) in sources.items():
            assets.append(
                {
                    "file": file_name,
                    "kind": kind,
                    "sector": sector,
                    "sha256": sha256,
                }
            )
    return {
        "schema_version": 1,
        "batch_id": closure_module.BATCH24_BATCH_ID,
        "asset_count": 16,
        "prompt_count": 16,
        "trust": {
            "synthetic": True,
            "stage": "design-only",
            "camera_calibration": "unknown",
            "geometry_consistency": "not-verified",
            "metric_scale": "unknown",
            "real_photo_texture": False,
            "training_use": "forbidden-as-multiview",
            "coverage_use": "forbidden",
            "trust_effect": "none",
        },
        "assets": assets,
    }


@pytest.fixture
def closure_plan() -> PerimeterClosurePlan:
    return build_default_perimeter_closure_plan(
        batch24_manifest=_batch24_manifest(),
        batch24_manifest_sha256="a" * 64,
        production_plan_sha256="b" * 64,
        topology_plan_sha256="c" * 64,
        terrain_height_at=lambda x, y: round(0.01 * x - 0.005 * y, 3),
    )


def _audit_terrain(x_m: float, y_m: float) -> float:
    return round(7.0 + 0.02 * x_m + 0.005 * y_m, 3)


def _build(closure_plan: PerimeterClosurePlan) -> PerimeterClosureAuditPlan:
    registry = _registry()
    return build_perimeter_closure_audit_plan(
        perimeter_closure_plan=closure_plan,
        exact_build_id="d" * 64,
        exact_build_report_sha256="e" * 64,
        exact_blend_sha256="f" * 64,
        object_registry_sha256=perimeter_closure_object_registry_sha256(
            registry
        ),
        terrain_height_at=_audit_terrain,
    )


def _registry() -> tuple[canary.ObjectRegistryEntry, ...]:
    return tuple(
        canary.ObjectRegistryEntry(
            object_id=f"audit-object-{instance_id:03d}",
            instance_id=instance_id,
            semantic_id=3,
            material_id=1,
            variant_id=None,
        )
        for instance_id in range(1, 267)
    )


def _clearance_policy() -> ProductionClearancePolicy:
    return ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )


def _local_quality_policy() -> LocalProductionQualityPolicy:
    return LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.25)


def _post_render_policy():
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


def _clearance_evidence(
    plan: PerimeterClosureAuditPlan,
) -> tuple[ProductionCameraClearanceEvidence, ...]:
    rays = tuple(
        ProductionClearanceRayEvidence(
            sample_x=sample_x,
            sample_y=sample_y,
            hit=False,
        )
        for sample_x, sample_y in PRODUCTION_CLEARANCE_SAMPLE_POINTS
    )
    return tuple(
        ProductionCameraClearanceEvidence(
            camera_id=camera.camera_id,
            rays=rays,
        )
        for camera in plan.cameras
    )


def _render_request(
    plan: PerimeterClosureAuditPlan,
):
    registry = _registry()
    clearance_request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        object_registry=registry,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    clearance_report = build_perimeter_closure_clearance_report(
        request=clearance_request,
        evidence=_clearance_evidence(plan),
    )
    return build_perimeter_closure_render_frame_request(
        plan=plan,
        audit_camera_id=plan.cameras[0].audit_camera_id,
        blender_executable_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        engine_script_sha256="4" * 64,
        object_registry=registry,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        clearance_report=clearance_report,
        local_quality_policy=_local_quality_policy(),
        post_render_policy=_post_render_policy(),
    )


@pytest.fixture(scope="module")
def blender_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_test_render_perimeter_closure_audit",
        BLENDER_AUDIT_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    previous = sys.modules.get("bpy")
    sys.modules["bpy"] = SimpleNamespace()
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = previous


def _payload(plan: PerimeterClosureAuditPlan) -> dict[str, Any]:
    return copy.deepcopy(plan.model_dump(mode="python"))


def test_plan_materializes_exact_sixteen_bidirectional_cameras(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)

    assert len(plan.cameras) == 16
    assert tuple(camera.audit_camera_id for camera in plan.cameras) == (
        PERIMETER_CLOSURE_AUDIT_CAMERA_ORDER
    )
    assert {camera.direction for camera in plan.cameras} == {
        "inward",
        "outward",
    }
    assert all(camera.eye_height_m == 1.6 for camera in plan.cameras)
    assert all(
        camera.source_plan_sha256
        == perimeter_closure_plan_sha256(closure_plan)
        for camera in plan.cameras
    )

    for module_index, module in enumerate(closure_plan.modules):
        inward, outward = plan.cameras[module_index * 2 : module_index * 2 + 2]
        inner_ground = _audit_terrain(
            module.inner_anchor_m[0],
            module.inner_anchor_m[1],
        )
        outer_ground = _audit_terrain(
            module.outer_anchor_m[0],
            module.outer_anchor_m[1],
        )
        assert inward.position_m == (
            module.outer_anchor_m[0],
            module.outer_anchor_m[1],
            round(outer_ground + 1.6, 3),
        )
        assert inward.look_at_m == (
            module.inner_anchor_m[0],
            module.inner_anchor_m[1],
            round(inner_ground + 1.6, 3),
        )
        assert outward.position_m == inward.look_at_m
        assert outward.look_at_m == inward.position_m
        assert inward.position_terrain_z_m == outer_ground
        assert outward.position_terrain_z_m == inner_ground


def test_plan_binds_current_targets_and_neighbor_seams(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    seam_ids = tuple(module.parts[4].instance_id for module in closure_plan.modules)

    for module_index, module in enumerate(closure_plan.modules):
        expected_targets = tuple(part.instance_id for part in module.parts)
        expected_seams = (
            seam_ids[module_index],
            seam_ids[(module_index + 1) % len(seam_ids)],
        )
        for camera in plan.cameras[module_index * 2 : module_index * 2 + 2]:
            assert camera.required_target_instance_ids == expected_targets
            assert camera.required_seam_instance_ids == expected_seams


def test_plan_is_canonical_and_content_addressed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    payload = canonical_perimeter_closure_audit_plan_bytes(plan)

    assert payload.endswith(b"\n")
    assert perimeter_closure_audit_plan_sha256(plan) == hashlib.sha256(
        payload
    ).hexdigest()
    verify_perimeter_closure_audit_plan(
        plan,
        perimeter_closure_plan=closure_plan,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("exact_build_id", "2" * 64, "build"),
        ("exact_build_report_sha256", "3" * 64, "report"),
        ("exact_blend_sha256", "4" * 64, "blend"),
        ("object_registry_sha256", "5" * 64, "registry"),
    ),
)
def test_external_expected_identity_drift_fails_closed(
    closure_plan: PerimeterClosurePlan,
    field: str,
    value: str,
    message: str,
) -> None:
    plan = _build(closure_plan)
    expected = {
        "exact_build_id": plan.exact_build_id,
        "exact_build_report_sha256": plan.exact_build_report_sha256,
        "exact_blend_sha256": plan.exact_blend_sha256,
        "object_registry_sha256": plan.object_registry_sha256,
    }
    expected[field] = value

    with pytest.raises(ValueError, match=message):
        verify_perimeter_closure_audit_plan(
            plan,
            perimeter_closure_plan=closure_plan,
            **expected,
        )


def test_duplicate_or_reordered_camera_fails_closed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(_build(closure_plan))
    cameras = list(payload["cameras"])
    cameras[1] = copy.deepcopy(cameras[0])
    payload["cameras"] = tuple(cameras)

    with pytest.raises(ValidationError, match="camera"):
        PerimeterClosureAuditPlan.model_validate(payload)


def test_same_position_reversal_fails_closed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(_build(closure_plan))
    payload["cameras"][1]["position_m"] = copy.deepcopy(
        payload["cameras"][0]["position_m"]
    )
    payload["cameras"][1]["position_terrain_z_m"] = payload["cameras"][0][
        "position_terrain_z_m"
    ]

    with pytest.raises(ValidationError, match="anchor|position|pair"):
        PerimeterClosureAuditPlan.model_validate(payload)


def test_floating_camera_fails_closed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(_build(closure_plan))
    position = payload["cameras"][0]["position_m"]
    payload["cameras"][0]["position_m"] = (
        position[0],
        position[1],
        position[2] + 0.5,
    )

    with pytest.raises(ValidationError, match="eye height|terrain"):
        PerimeterClosureAuditPlan.model_validate(payload)


@pytest.mark.parametrize("bad_height", (math.nan, math.inf, -math.inf))
def test_non_finite_terrain_sample_fails_closed(
    closure_plan: PerimeterClosurePlan,
    bad_height: float,
) -> None:
    with pytest.raises(ValueError, match="terrain"):
        build_perimeter_closure_audit_plan(
            perimeter_closure_plan=closure_plan,
            exact_build_id="d" * 64,
            exact_build_report_sha256="e" * 64,
            exact_blend_sha256="f" * 64,
            object_registry_sha256="1" * 64,
            terrain_height_at=lambda _x, _y: bad_height,
        )


def test_promoted_trust_fails_closed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(_build(closure_plan))
    payload["geometry_usability"] = "metric-aligned"

    with pytest.raises(ValidationError):
        PerimeterClosureAuditPlan.model_validate(payload)


def test_module_order_remains_the_canonical_eight() -> None:
    assert PERIMETER_CLOSURE_MODULE_ORDER == (
        "closure-upstream",
        "closure-northeast",
        "closure-east",
        "closure-southeast",
        "closure-downstream",
        "closure-southwest",
        "closure-west",
        "closure-northwest",
    )


def test_clearance_request_binds_all_sixteen_exact266_cameras(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    registry = _registry()
    policy = _clearance_policy()

    request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        object_registry=registry,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=policy,
    )

    assert request.audit_plan_sha256 == perimeter_closure_audit_plan_sha256(
        plan
    )
    assert request.selected_camera_ids == tuple(
        camera.camera_id for camera in plan.cameras
    )
    assert tuple(row.instance_id for row in request.object_registry) == tuple(
        range(1, 267)
    )
    assert request.object_registry_sha256 == (
        perimeter_closure_object_registry_sha256(registry)
    )
    assert request.policy_sha256 == production_clearance_policy_sha256(policy)
    assert canonical_perimeter_closure_clearance_request_bytes(request).endswith(
        b"\n"
    )
    assert len(request.preflight_id) == 64


def test_clearance_request_rejects_registry_drift(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)

    with pytest.raises(ValueError, match="registry"):
        build_perimeter_closure_clearance_request(
            plan=plan,
            blender_executable_sha256="2" * 64,
            audit_script_sha256="3" * 64,
            object_registry=_registry()[:-1],
            auxiliary_registry=canary.AUXILIARY_REGISTRY,
            semantic_registry=canary._semantic_registry(),
            policy=_clearance_policy(),
        )


def test_clearance_report_round_trip_binds_raw_measurements(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    report = build_perimeter_closure_clearance_report(
        request=request,
        evidence=_clearance_evidence(plan),
    )

    assert len(report.evidence) == len(report.decisions) == 16
    assert all(decision.passes for decision in report.decisions)
    verify_perimeter_closure_clearance_report(report, request=request)
    assert canonical_perimeter_closure_clearance_report_bytes(
        report
    ).endswith(b"\n")


def test_clearance_report_rejects_decision_from_another_camera(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    report = build_perimeter_closure_clearance_report(
        request=request,
        evidence=_clearance_evidence(plan),
    )
    decisions = list(report.decisions)
    decisions[0] = decisions[0].model_copy(
        update={"camera_id": decisions[1].camera_id}
    )
    mutated = report.model_copy(update={"decisions": tuple(decisions)})

    with pytest.raises(ValueError, match="decision|camera"):
        verify_perimeter_closure_clearance_report(mutated, request=request)


def test_render_request_binds_clearance_six_layers_and_quality_policies(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    registry = _registry()
    clearance_request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        object_registry=registry,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    clearance_report = build_perimeter_closure_clearance_report(
        request=clearance_request,
        evidence=_clearance_evidence(plan),
    )
    local_policy = _local_quality_policy()
    post_policy = _post_render_policy()
    camera = plan.cameras[0]

    request = build_perimeter_closure_render_frame_request(
        plan=plan,
        audit_camera_id=camera.audit_camera_id,
        blender_executable_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        engine_script_sha256="4" * 64,
        object_registry=registry,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        clearance_report=clearance_report,
        local_quality_policy=local_policy,
        post_render_policy=post_policy,
    )

    assert request.camera == camera
    assert request.clearance_decision.camera_id == camera.camera_id
    assert request.clearance_decision.passes
    assert request.required_target_instance_ids == (
        camera.required_target_instance_ids
    )
    assert request.required_seam_instance_ids == (
        camera.required_seam_instance_ids
    )
    assert request.renderer_capability_sha256 == (
        perimeter_closure_renderer_capability_sha256()
    )
    assert request.local_quality_policy_sha256 == (
        local_production_quality_policy_sha256(local_policy)
    )
    assert request.post_render_policy_sha256 == (
        production_frame_quality_policy_v2_sha256(post_policy)
    )
    assert canonical_perimeter_closure_render_request_bytes(request).endswith(
        b"\n"
    )


def test_render_request_rejects_failed_or_wrong_camera_clearance(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    registry = _registry()
    clearance_request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256="3" * 64,
        object_registry=registry,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    evidence = list(_clearance_evidence(plan))
    near_rays = list(evidence[0].rays)
    for index in range(5):
        ray = near_rays[10 + index]
        near_rays[10 + index] = ray.model_copy(
            update={
                "hit": True,
                "distance_m": 0.5,
                "object_name": "blocking-wall",
                "stable_id": "blocking-wall",
                "part_id": "mesh",
                "semantic_id": 3,
            }
        )
    evidence[0] = evidence[0].model_copy(update={"rays": tuple(near_rays)})
    clearance_report = build_perimeter_closure_clearance_report(
        request=clearance_request,
        evidence=tuple(evidence),
    )

    with pytest.raises(ValueError, match="clearance"):
        build_perimeter_closure_render_frame_request(
            plan=plan,
            audit_camera_id=plan.cameras[0].audit_camera_id,
            blender_executable_sha256="2" * 64,
            audit_script_sha256="3" * 64,
            engine_script_sha256="4" * 64,
            object_registry=registry,
            auxiliary_registry=canary.AUXILIARY_REGISTRY,
            semantic_registry=canary._semantic_registry(),
            clearance_report=clearance_report,
            local_quality_policy=_local_quality_policy(),
            post_render_policy=_post_render_policy(),
        )


def test_exact266_render_statistics_accepts_registered_overlay_ids() -> None:
    statistics = PerimeterClosureRenderStatistics(
        depth_min_m=0.0,
        depth_max_m=10.0,
        depth_background_pixels=1,
        depth_max_range_error_m=0.0,
        normal_max_unit_error=0.0,
        instance_ids=(0, 218, 219, 266),
        semantic_ids=(0, 3, 14),
    )

    assert statistics.instance_ids[-1] == 266
    with pytest.raises(ValidationError, match="0 through 266"):
        PerimeterClosureRenderStatistics.model_validate(
            {
                **statistics.model_dump(mode="python"),
                "instance_ids": (0, 267),
            }
        )


def test_visibility_measurement_requires_all_targets_and_both_seams(
    closure_plan: PerimeterClosurePlan,
) -> None:
    request = _render_request(_build(closure_plan))
    observed = tuple(
        sorted(
            {
                0,
                *request.required_target_instance_ids[:-2],
                *request.required_seam_instance_ids,
            }
        )
    )
    statistics = PerimeterClosureRenderStatistics(
        depth_min_m=0.0,
        depth_max_m=10.0,
        depth_background_pixels=1,
        depth_max_range_error_m=0.0,
        normal_max_unit_error=0.0,
        instance_ids=observed,
        semantic_ids=(0, 3),
    )

    measurement = measure_perimeter_closure_visibility(
        request=request,
        statistics=statistics,
    )

    assert measurement.visible_target_instance_ids == (
        request.required_target_instance_ids[:-1]
    )
    assert measurement.missing_target_instance_ids == (
        request.required_target_instance_ids[-1:]
    )
    assert not measurement.target_visibility_passed
    assert measurement.visible_seam_instance_ids == (
        request.required_seam_instance_ids
    )
    assert measurement.missing_seam_instance_ids == ()
    assert measurement.seam_visibility_passed
    assert measurement.trust_effect == "none-quality-filter-only"


def test_exact266_render_report_is_content_and_artifact_bound(
    closure_plan: PerimeterClosurePlan,
    tmp_path: Path,
) -> None:
    request = _render_request(_build(closure_plan))
    artifacts: list[ProductionArtifactRecord] = []
    for index, (kind, portable_path) in enumerate(
        expected_production_artifacts(request.camera.camera_id),
        start=1,
    ):
        artifact_path = tmp_path / portable_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(f"closure-artifact-{index}".encode())
        artifacts.append(
            ProductionArtifactRecord(
                kind=kind,
                path=portable_path,
                sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                size_bytes=artifact_path.stat().st_size,
            )
        )
    payload = {
        "schema_version": (
            "nantai.synthetic-village.local-production-render-frame-report.v4"
        ),
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
                request.settings.model_dump(mode="json")
            )
        ).hexdigest(),
        "artifacts": tuple(artifacts),
        "statistics": PerimeterClosureRenderStatistics(
            depth_min_m=0.0,
            depth_max_m=10.0,
            depth_background_pixels=1,
            depth_max_range_error_m=0.0,
            normal_max_unit_error=0.0,
            instance_ids=(
                0,
                *request.required_target_instance_ids,
                *tuple(
                    value
                    for value in request.required_seam_instance_ids
                    if value not in request.required_target_instance_ids
                ),
            ),
            semantic_ids=(0, 3),
        ),
        "layer_statistics": ProductionFrameLayerStatistics(
            camera_id=request.camera.camera_id,
            upper_pixel_count=1024 * 288,
            valid_depth_pixel_count=500000,
            valid_normal_pixel_count=500000,
            registered_instance_pixel_count=500000,
            valid_semantic_pixel_count=500000,
            sky_pixel_count=89824,
            upper_ground_pixel_count=10000,
            near_depth_pixel_count=0,
            dominant_near_instance_pixel_count=0,
            dominant_upper_instance_id=(
                request.required_target_instance_ids[0]
            ),
            dominant_upper_instance_pixel_count=10000,
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
        "profile_id": "synthetic-village-coverage-180-v1",
        "production_plan_sha256": request.audit_plan_sha256,
        "camera_registry_sha256": request.camera_registry_sha256,
        "elevated_topology_sha256": (
            request.audit_plan.perimeter_closure_plan.topology_plan_sha256
        ),
        "group_id": request.camera.group_id,
        "topology_ref": request.camera.topology_ref,
        "preflight_id": request.preflight_id,
        "quality_policy_sha256": request.local_quality_policy_sha256,
        "post_render_policy_sha256": request.post_render_policy_sha256,
    }
    unsigned = PerimeterClosureRenderFrameReport.model_construct(**payload)
    payload["content_sha256"] = hashlib.sha256(
        canonical_perimeter_closure_render_report_bytes(
            unsigned,
            exclude_sha256=True,
        )
    ).hexdigest()
    report = PerimeterClosureRenderFrameReport.model_validate(payload)
    report_path = tmp_path / "frame-report.json"
    report_path.write_bytes(
        canonical_perimeter_closure_render_report_bytes(report)
    )

    loaded = load_perimeter_closure_render_report(report_path)
    verify_perimeter_closure_render_frame(
        loaded,
        request=request,
        frame_root=tmp_path,
    )

    (tmp_path / artifacts[0].path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact"):
        verify_perimeter_closure_render_frame(
            loaded,
            request=request,
            frame_root=tmp_path,
        )


def test_exact266_camera_metadata_round_trip_binds_measured_pose(
    closure_plan: PerimeterClosurePlan,
    tmp_path: Path,
) -> None:
    request = _render_request(_build(closure_plan))
    payload = {
        "schema_version": (
            "nantai.synthetic-village.local-production-camera-metadata.v4"
        ),
        "build_id": request.build_id,
        "render_id": request.render_id,
        "synthetic": True,
        "verification_level": "L0",
        "blender_executable_sha256": request.blender_executable_sha256,
        "camera_id": request.camera.camera_id,
        "image_width_px": 1024,
        "image_height_px": 576,
        "coordinate_system": "opencv-c2w-right-down-forward-meters",
        "pixel_origin": "top-left",
        "pixel_center_offset": (0.5, 0.5),
        "depth_encoding": "euclidean-camera-center-range-m",
        "depth_units": "m",
        "depth_invalid_value_m": 0.0,
        "normal_encoding": "world-space-unit-vector",
        "normal_axes": "blender-right-handed-z-up",
        "normal_background_xyz": (0.0, 0.0, 0.0),
        "clip_start_m": 0.1,
        "clip_end_m": 1200.0,
        "depth_channel_layout": "V-float32-zip",
        "normal_channel_layout": "X,Y,Z-float32-zip",
        "instance_pixel_type": "uint16-grayscale-png",
        "semantic_pixel_type": "uint8-grayscale-png",
        "settings_sha256": hashlib.sha256(
            canary._canonical_json_bytes(  # noqa: SLF001
                request.settings.model_dump(mode="json")
            )
        ).hexdigest(),
        "intrinsics": request.camera.intrinsics,
        "requested_c2w_opencv": request.camera.c2w_opencv,
        "requested_c2w_blender": request.requested_c2w_blender,
        "measured_c2w_opencv": request.camera.c2w_opencv,
        "measured_c2w_blender": request.requested_c2w_blender,
        "object_registry_sha256": request.object_registry_sha256,
        "semantic_registry": request.semantic_registry,
        "profile_id": "synthetic-village-coverage-180-v1",
        "production_plan_sha256": request.audit_plan_sha256,
        "camera_registry_sha256": request.camera_registry_sha256,
        "elevated_topology_sha256": (
            request.audit_plan.perimeter_closure_plan.topology_plan_sha256
        ),
        "group_id": request.camera.group_id,
        "topology_ref": request.camera.topology_ref,
        "arc_length_m": request.camera.arc_length_m,
        "audit_only": request.camera.audit_only,
        "disclosure": request.camera.disclosure,
        "preflight_id": request.preflight_id,
        "quality_policy_sha256": request.local_quality_policy_sha256,
        "post_render_policy_sha256": request.post_render_policy_sha256,
    }
    metadata = PerimeterClosureCameraMetadata.model_validate(payload)
    metadata_path = tmp_path / "camera.json"
    metadata_path.write_bytes(
        canonical_perimeter_closure_camera_metadata_bytes(metadata)
    )

    loaded = load_perimeter_closure_camera_metadata(metadata_path)
    verify_perimeter_closure_camera_metadata(loaded, request=request)

    measured = [list(row) for row in loaded.measured_c2w_opencv]
    measured[0][3] += 1.0
    changed = loaded.model_copy(
        update={"measured_c2w_opencv": tuple(tuple(row) for row in measured)}
    )
    with pytest.raises(ValueError, match="metadata"):
        verify_perimeter_closure_camera_metadata(changed, request=request)


def _scene_lineage(plan: PerimeterClosureAuditPlan) -> dict[str, str]:
    return {
        "nv_perimeter_closure_build": json.dumps(
            {
                "build_id": plan.exact_build_id,
                "canonical_roots": 266,
                "geometry_usability": "preview-only",
                "overlay_roots": 48,
                "stage": "modeled-unverified",
                "trust_effect": "none-quality-filter-only",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    }


def test_blender_adapter_literal_locks_exact266_and_dual_modes(
    blender_adapter: ModuleType,
) -> None:
    assert blender_adapter.CLEARANCE_REQUEST_SCHEMA == (
        "nantai.synthetic-village.perimeter-closure-clearance-request.v1"
    )
    assert blender_adapter.CLEARANCE_REPORT_SCHEMA == (
        "nantai.synthetic-village.perimeter-closure-clearance-report.v1"
    )
    assert blender_adapter.RENDER_REQUEST_SCHEMA == (
        "nantai.synthetic-village.perimeter-closure-render-frame-request.v1"
    )
    assert blender_adapter.EXPECTED_INSTANCE_IDS == list(range(1, 267))
    assert blender_adapter.EXPECTED_CAMERA_IDS == [
        f"camera-audit-overview-{index:03d}" for index in range(1, 17)
    ]
    assert blender_adapter._runtime_mode_args(
        ["blender", "--", "--mode", "preflight", "--request", "a", "--output", "b"]
    ) == ("preflight", Path("a"), Path("b"))
    assert blender_adapter._runtime_mode_args(
        ["blender", "--", "--mode", "render", "--request", "a", "--output", "b"]
    ) == ("render", Path("a"), Path("b"))


def test_blender_adapter_validates_clearance_boundary_before_engine(
    blender_adapter: ModuleType,
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    script_sha = hashlib.sha256(BLENDER_AUDIT_SCRIPT.read_bytes()).hexdigest()
    request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    payload = request.model_dump(mode="json")

    blender_adapter._validate_clearance_boundary(
        payload,
        scene=_scene_lineage(plan),
        script_path=BLENDER_AUDIT_SCRIPT,
    )

    payload["object_registry"][-1]["instance_id"] = 999
    with pytest.raises(blender_adapter.RuntimeAuditError, match="1..266"):
        blender_adapter._validate_clearance_boundary(
            payload,
            scene=_scene_lineage(plan),
            script_path=BLENDER_AUDIT_SCRIPT,
        )


def test_blender_adapter_validates_render_boundary_before_engine(
    blender_adapter: ModuleType,
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    script_sha = hashlib.sha256(BLENDER_AUDIT_SCRIPT.read_bytes()).hexdigest()
    clearance_request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    clearance_report = build_perimeter_closure_clearance_report(
        request=clearance_request,
        evidence=_clearance_evidence(plan),
    )
    request = build_perimeter_closure_render_frame_request(
        plan=plan,
        audit_camera_id=plan.cameras[0].audit_camera_id,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        engine_script_sha256="4" * 64,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        clearance_report=clearance_report,
        local_quality_policy=_local_quality_policy(),
        post_render_policy=_post_render_policy(),
    )

    blender_adapter._validate_render_boundary(
        request.model_dump(mode="json"),
        scene=_scene_lineage(plan),
        script_path=BLENDER_AUDIT_SCRIPT,
    )

    changed = request.model_dump(mode="json")
    changed["renderer_capability_sha256"] = "9" * 64
    with pytest.raises(blender_adapter.RuntimeAuditError, match="capability"):
        blender_adapter._validate_render_boundary(
            changed,
            scene=_scene_lineage(plan),
            script_path=BLENDER_AUDIT_SCRIPT,
        )


def test_blender_adapter_builds_exact_host_clearance_report_payload(
    blender_adapter: ModuleType,
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    script_sha = hashlib.sha256(BLENDER_AUDIT_SCRIPT.read_bytes()).hexdigest()
    request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    request_payload = request.model_dump(mode="json")
    raw = canonical_perimeter_closure_clearance_request_bytes(request)
    evidence = _clearance_evidence(plan)
    host_report = build_perimeter_closure_clearance_report(
        request=request,
        evidence=evidence,
    )
    measured = tuple(
        (
            evidence_row.model_dump(mode="json"),
            decision.model_dump(mode="json"),
        )
        for evidence_row, decision in zip(
            host_report.evidence,
            host_report.decisions,
            strict=True,
        )
    )

    payload = blender_adapter._build_clearance_report_payload(
        request_payload,
        raw,
        measured,
    )

    assert payload == host_report.model_dump(mode="json")
    assert hashlib.sha256(raw).hexdigest() == payload["request_sha256"]


def test_blender_adapter_translates_only_bound_fields_to_frozen_renderer(
    blender_adapter: ModuleType,
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    script_sha = hashlib.sha256(BLENDER_AUDIT_SCRIPT.read_bytes()).hexdigest()
    clearance_request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    clearance_report = build_perimeter_closure_clearance_report(
        request=clearance_request,
        evidence=_clearance_evidence(plan),
    )
    request = build_perimeter_closure_render_frame_request(
        plan=plan,
        audit_camera_id=plan.cameras[0].audit_camera_id,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        engine_script_sha256="4" * 64,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        clearance_report=clearance_report,
        local_quality_policy=_local_quality_policy(),
        post_render_policy=_post_render_policy(),
    )

    internal = blender_adapter._to_engine_render_request(
        request.model_dump(mode="json")
    )

    assert internal["schema_version"] == (
        "nantai.synthetic-village.perimeter-closure-render-frame-request.v1"
    )
    assert internal["profile_id"] == "synthetic-village-coverage-180-v1"
    assert internal["production_plan"] == request.audit_plan.model_dump(
        mode="json"
    )
    assert internal["production_plan_sha256"] == request.audit_plan_sha256
    assert internal["renderer_script_sha256"] == request.audit_script_sha256
    assert internal["quality_policy_sha256"] == (
        request.local_quality_policy_sha256
    )
    assert internal["build_adapter"] == "windows-textured-v2"
    assert tuple(
        row["instance_id"] for row in internal["object_registry"]
    ) == tuple(range(1, 267))
    for removed in (
        "audit_plan",
        "audit_plan_sha256",
        "audit_camera_id",
        "audit_script_sha256",
        "engine_script_sha256",
        "perimeter_closure_plan_sha256",
        "clearance_report_sha256",
        "clearance_policy_sha256",
        "clearance_decision",
        "renderer_capability_sha256",
        "local_quality_policy",
        "local_quality_policy_sha256",
        "required_target_instance_ids",
        "required_seam_instance_ids",
        "geometry_usability",
        "stage",
        "trust_effect",
    ):
        assert removed not in internal


def test_blender_adapter_measures_all_sixteen_cameras_in_plan_order(
    blender_adapter: ModuleType,
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    script_sha = hashlib.sha256(BLENDER_AUDIT_SCRIPT.read_bytes()).hexdigest()
    request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    measured_ids: list[str] = []
    policy_calls: list[dict[str, Any]] = []

    def measure(camera, _request, depsgraph):
        assert depsgraph == "depsgraph"
        measured_ids.append(camera["camera_id"])
        return ({"camera_id": camera["camera_id"]}, {"passes": True})

    engine = SimpleNamespace(
        _validate_policy=lambda policy: policy_calls.append(policy),
        _measure_camera=measure,
    )
    context = SimpleNamespace(
        view_layer=SimpleNamespace(update=lambda: None),
        evaluated_depsgraph_get=lambda: "depsgraph",
    )

    measured = blender_adapter._measure_clearance(
        request.model_dump(mode="json"),
        engine,
        context,
    )

    assert policy_calls == [request.policy.model_dump(mode="json")]
    assert measured_ids == list(request.selected_camera_ids)
    assert len(measured) == 16


def test_blender_adapter_prepares_frozen_engine_for_exact266_only(
    blender_adapter: ModuleType,
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    script_sha = hashlib.sha256(BLENDER_AUDIT_SCRIPT.read_bytes()).hexdigest()
    clearance_request = build_perimeter_closure_clearance_request(
        plan=plan,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        policy=_clearance_policy(),
    )
    clearance_report = build_perimeter_closure_clearance_report(
        request=clearance_request,
        evidence=_clearance_evidence(plan),
    )
    request = build_perimeter_closure_render_frame_request(
        plan=plan,
        audit_camera_id=plan.cameras[0].audit_camera_id,
        blender_executable_sha256="2" * 64,
        audit_script_sha256=script_sha,
        engine_script_sha256="4" * 64,
        object_registry=_registry(),
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),
        clearance_report=clearance_report,
        local_quality_policy=_local_quality_policy(),
        post_render_policy=_post_render_policy(),
    )
    internal = blender_adapter._to_engine_render_request(
        request.model_dump(mode="json")
    )

    class FakeEngineError(RuntimeError):
        pass

    engine = SimpleNamespace(RuntimeRenderError=FakeEngineError)
    prepared = blender_adapter._prepare_render_engine(engine)

    assert prepared is engine
    assert engine.LOCAL_PRODUCTION_REQUEST_SCHEMA == (
        "nantai.synthetic-village.perimeter-closure-render-frame-request.v1"
    )
    assert engine.LOCAL_PRODUCTION_REPORT_SCHEMA == (
        "nantai.synthetic-village.local-production-render-frame-report.v4"
    )
    assert engine.LOCAL_PRODUCTION_CAMERA_SCHEMA == (
        "nantai.synthetic-village.local-production-camera-metadata.v4"
    )
    engine._validate_object_registry_contract(internal["object_registry"])
    engine._validate_production_camera_request(internal)

    changed_registry = copy.deepcopy(internal["object_registry"])
    changed_registry[-1]["instance_id"] = 999
    with pytest.raises(FakeEngineError, match="1 through 266"):
        engine._validate_object_registry_contract(changed_registry)

    changed_request = copy.deepcopy(internal)
    changed_request["camera"]["camera_id"] = "camera-audit-overview-016"
    with pytest.raises(FakeEngineError, match="immutable plan"):
        engine._validate_production_camera_request(changed_request)


def test_blender_adapter_loads_only_content_bound_frozen_module(
    blender_adapter: ModuleType,
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "frozen_engine.py"
    module_path.write_text("VALUE = 7\n", encoding="utf-8")
    expected_sha = hashlib.sha256(module_path.read_bytes()).hexdigest()

    loaded = blender_adapter._load_frozen_module(
        module_path,
        expected_sha,
        "test_bound_engine",
    )

    assert loaded.VALUE == 7
    with pytest.raises(blender_adapter.RuntimeAuditError, match="digest"):
        blender_adapter._load_frozen_module(
            module_path,
            "0" * 64,
            "test_rejected_engine",
        )


def test_blender_adapter_validates_actual_blender_and_blend_bytes(
    blender_adapter: ModuleType,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "blender.exe"
    blend = tmp_path / "scene.blend"
    executable.write_bytes(b"blender")
    blend.write_bytes(b"blend")
    request = {
        "blender_executable_sha256": hashlib.sha256(
            executable.read_bytes()
        ).hexdigest(),
        "blend_sha256": hashlib.sha256(blend.read_bytes()).hexdigest(),
    }
    bpy_module = SimpleNamespace(
        app=SimpleNamespace(
            binary_path=str(executable),
            version_string="4.5.11 LTS",
            build_hash=b"4db51e9d1e1e",
        ),
        data=SimpleNamespace(filepath=str(blend)),
        context=SimpleNamespace(
            scene={
                "nv_synthetic": True,
                "nv_fidelity": "simplified-pbr-not-render-parity",
            }
        ),
    )

    blender_adapter._validate_runtime_identity(request, bpy_module)

    changed = dict(request)
    changed["blend_sha256"] = "0" * 64
    with pytest.raises(blender_adapter.RuntimeAuditError, match="Blender file"):
        blender_adapter._validate_runtime_identity(changed, bpy_module)


def test_blender_adapter_writes_clearance_report_once_and_canonically(
    blender_adapter: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "clearance-report.json"
    payload = {"schema_version": "test", "value": 1}

    blender_adapter._write_clearance_report(output, payload)

    assert output.read_bytes() == blender_adapter._canonical_bytes(payload)
    with pytest.raises(blender_adapter.RuntimeAuditError, match="already exists"):
        blender_adapter._write_clearance_report(output, payload)


def test_blender_adapter_normalizes_only_exact_hidden_topology_proxies(
    blender_adapter: ModuleType,
) -> None:
    proxies = [
        SimpleNamespace(
            type="MESH",
            hide_render=True,
            hide_viewport=False,
            pass_index=0,
            get=lambda key, default=None, index=index: {
                "nv_proxy_topology": True,
                "nv_stable_id": f"topology-proxy-{index}",
                "nv_root": False,
                "nv_stage": "modeled-unverified",
                "nv_trust_effect": "none",
                "nv_geometry_usability": "preview-only",
            }.get(key, default),
        )
        for index in range(6)
    ]

    blender_adapter._prepare_topology_proxies(proxies)

    assert all(proxy.hide_viewport for proxy in proxies)
    proxies[0].hide_render = False
    with pytest.raises(
        blender_adapter.RuntimeAuditError,
        match="topology proxy",
    ):
        blender_adapter._prepare_topology_proxies(proxies)
