"""Fail-closed tests for the additive exact-218 production caller."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.elevated_topology import (
    build_elevated_topology_plan,
)
from pipeline.synthetic_village.production_journal import production_render_id
from pipeline.synthetic_village.production_preflight import (
    ProductionClearancePolicy,
)
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
)
from pipeline.synthetic_village.production_quality_gates import (
    ProductionFrameQualityPolicyV2,
    candidate_synthetic_village_frame_quality_policy_v2,
    production_frame_quality_policy_v2_sha256,
)
from pipeline.synthetic_village.reciprocal_route_production import (
    RECIPROCAL_BUILD_ADAPTER,
    RECIPROCAL_CLEARANCE_REQUEST_SCHEMA,
    RECIPROCAL_RENDER_REQUEST_SCHEMA,
    ReciprocalProductionClearanceRequest,
    ReciprocalProductionError,
    ReciprocalProductionRenderFrameRequest,
    build_reciprocal_production_clearance_request,
    build_reciprocal_production_frame_request,
    canonical_reciprocal_production_clearance_request_bytes,
    canonical_reciprocal_production_render_request_bytes,
    reciprocal_object_registry_sha256,
    require_exact_reciprocal_object_registry,
    verify_reciprocal_production_build,
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
