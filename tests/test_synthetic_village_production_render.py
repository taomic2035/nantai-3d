from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.elevated_topology import (
    build_elevated_topology_plan,
)
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
    canonical_production_plan_bytes,
)
from pipeline.synthetic_village.production_render import (
    LOCAL_PRODUCTION_RENDER_REQUEST_SCHEMA,
    LocalProductionRenderFrameRequest,
    build_local_production_frame_request,
    canonical_local_production_render_request_bytes,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan

ROOT = Path(__file__).resolve().parents[1]


def _request(
    camera_id: str = "camera-elevated-pedestrian-001",
    *,
    build_adapter: str = "windows-textured-v2",
):
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    semantics = canary._semantic_registry()
    materials = canary._material_registry(scene)
    objects = canary._object_registry(scene, topology, semantics, materials)
    return build_local_production_frame_request(
        plan=plan,
        camera_id=camera_id,
        build_adapter=build_adapter,
        build_id="1" * 64,
        blender_executable_sha256="2" * 64,
        renderer_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        object_registry=objects,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=semantics,
        preflight_id="6" * 64,
        quality_policy_sha256="7" * 64,
    )


def test_local_production_request_is_l0_and_binds_one_plan_camera() -> None:
    request = _request()

    assert request.schema_version == LOCAL_PRODUCTION_RENDER_REQUEST_SCHEMA
    assert request.build_adapter == "windows-textured-v2"
    assert request.verification_level == "L0"
    assert request.profile_id == "synthetic-village-coverage-180-v1"
    assert request.camera.camera_id == "camera-elevated-pedestrian-001"
    assert request.camera.group_id == "elevated-pedestrian"
    assert request.requested_c2w_blender[0][3] == request.camera.position_m[0]
    assert request.requested_c2w_blender[1][3] == request.camera.position_m[1]
    assert request.requested_c2w_blender[2][3] == request.camera.position_m[2]
    assert request.production_plan_sha256 == hashlib.sha256(
        canonical_production_plan_bytes(request.production_plan),
    ).hexdigest()
    raw = canonical_local_production_render_request_bytes(request)
    assert raw.endswith(b"\n")
    assert str(Path.home()).encode() not in raw


def test_request_rejects_readdressed_camera_or_blender_matrix() -> None:
    request = _request()
    payload = json.loads(canonical_local_production_render_request_bytes(request))

    payload["camera"]["topology_ref"] = "fabricated-edge"
    with pytest.raises(ValidationError, match="camera.*plan"):
        LocalProductionRenderFrameRequest.model_validate_json(json.dumps(payload))

    payload = json.loads(canonical_local_production_render_request_bytes(request))
    payload["requested_c2w_blender"][0][3] += 1.0
    with pytest.raises(ValidationError, match="Blender matrix"):
        LocalProductionRenderFrameRequest.model_validate_json(json.dumps(payload))


def test_renderer_declares_separate_local_production_schema_and_dynamic_camera() -> None:
    source = (ROOT / "scripts/blender/render_synthetic_village.py").read_text(
        encoding="utf-8",
    )

    assert LOCAL_PRODUCTION_RENDER_REQUEST_SCHEMA in source
    assert "local-production-render-frame-report.v2" in source
    assert "local-production-camera-metadata.v2" in source
    assert "_create_production_camera" in source


def test_preflight_and_quality_context_change_frame_render_identity() -> None:
    base = _request()
    contextual = build_local_production_frame_request(
        plan=base.production_plan,
        camera_id=base.camera.camera_id,
        build_adapter=base.build_adapter,
        build_id=base.build_id,
        blender_executable_sha256=base.blender_executable_sha256,
        renderer_script_sha256=base.renderer_script_sha256,
        blend_sha256=base.blend_sha256,
        build_report_sha256=base.build_report_sha256,
        object_registry=base.object_registry,
        auxiliary_registry=base.auxiliary_registry,
        semantic_registry=base.semantic_registry,
        preflight_id="8" * 64,
        quality_policy_sha256="7" * 64,
    )
    changed_policy = build_local_production_frame_request(
        plan=base.production_plan,
        camera_id=base.camera.camera_id,
        build_adapter=base.build_adapter,
        build_id=base.build_id,
        blender_executable_sha256=base.blender_executable_sha256,
        renderer_script_sha256=base.renderer_script_sha256,
        blend_sha256=base.blend_sha256,
        build_report_sha256=base.build_report_sha256,
        object_registry=base.object_registry,
        auxiliary_registry=base.auxiliary_registry,
        semantic_registry=base.semantic_registry,
        preflight_id="8" * 64,
        quality_policy_sha256="9" * 64,
    )

    assert contextual.preflight_id == "8" * 64
    assert contextual.quality_policy_sha256 == "7" * 64
    assert contextual.render_id != base.render_id
    assert changed_policy.render_id != contextual.render_id

    payload = json.loads(
        canonical_local_production_render_request_bytes(contextual),
    )
    payload["quality_policy_sha256"] = "a" * 64
    with pytest.raises(ValidationError, match="render ID"):
        LocalProductionRenderFrameRequest.model_validate_json(
            json.dumps(payload),
        )


def test_build_adapter_is_bound_into_frame_render_identity() -> None:
    windows = _request(build_adapter="windows-textured-v2")
    local_preview = _request(build_adapter="mac-local-textured-preview-v1")

    assert windows.render_id != local_preview.render_id

    payload = json.loads(
        canonical_local_production_render_request_bytes(windows),
    )
    payload["build_adapter"] = "mac-local-textured-preview-v1"
    with pytest.raises(ValidationError, match="render ID"):
        LocalProductionRenderFrameRequest.model_validate_json(
            json.dumps(payload),
        )
