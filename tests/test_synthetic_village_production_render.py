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


def _request(camera_id: str = "camera-elevated-pedestrian-001"):
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    plan = build_production_camera_plan(scene, topology)
    semantics = canary._semantic_registry()
    materials = canary._material_registry(scene)
    objects = canary._object_registry(scene, topology, semantics, materials)
    return build_local_production_frame_request(
        plan=plan,
        camera_id=camera_id,
        build_id="1" * 64,
        blender_executable_sha256="2" * 64,
        renderer_script_sha256="3" * 64,
        blend_sha256="4" * 64,
        build_report_sha256="5" * 64,
        object_registry=objects,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=semantics,
    )


def test_local_production_request_is_l0_and_binds_one_plan_camera() -> None:
    request = _request()

    assert request.schema_version == LOCAL_PRODUCTION_RENDER_REQUEST_SCHEMA
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
    assert "local-production-render-frame-report.v1" in source
    assert "local-production-camera-metadata.v1" in source
    assert "_create_production_camera" in source
