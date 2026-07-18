"""Local L0 render contract for the scene-bound 180-camera production plan.

This module deliberately does not reuse the authoritative canary render
schema.  The current Apple Silicon build is a verified local preview, so its
production-camera frames remain L0 even though the synthetic topology plan is
L2.  More cameras never promote geometry or renderer trust.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import canary
from .production_journal import production_render_id
from .production_profile import (
    PRODUCTION_PROFILE_ID,
    ProductionCameraPlan,
    ProductionCameraPose,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)

LOCAL_PRODUCTION_RENDER_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-request.v1"
)
LOCAL_PRODUCTION_RENDER_REPORT_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-report.v1"
)
LOCAL_PRODUCTION_CAMERA_METADATA_SCHEMA = (
    "nantai.synthetic-village.local-production-camera-metadata.v1"
)

Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _opencv_c2w_to_blender(matrix: Matrix4) -> Matrix4:
    converted = np.asarray(matrix, dtype=float) @ np.diag([1.0, -1.0, -1.0, 1.0])
    converted[converted == 0.0] = 0.0
    return tuple(
        tuple(float(converted[row, column]) for column in range(4))
        for row in range(4)
    )


class LocalProductionRenderFrameRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.local-production-render-frame-request.v1"
    ] = LOCAL_PRODUCTION_RENDER_REQUEST_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = PRODUCTION_PROFILE_ID
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    elevated_topology_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_plan: ProductionCameraPlan
    render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    fidelity: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings: canary.RenderSettings
    camera: ProductionCameraPose
    requested_c2w_blender: Matrix4
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=130,
        max_length=130,
    )
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...] = Field(
        min_length=3,
        max_length=3,
    )
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...] = Field(
        min_length=15,
        max_length=15,
    )

    @model_validator(mode="after")
    def _validate_request(self) -> LocalProductionRenderFrameRequest:
        plan_bytes = canonical_production_plan_bytes(self.production_plan)
        if self.production_plan_sha256 != hashlib.sha256(plan_bytes).hexdigest():
            raise ValueError("production plan digest is invalid")
        if (
            self.camera_registry_sha256
            != production_camera_registry_digest(self.production_plan)
        ):
            raise ValueError("production camera registry digest is invalid")
        if (
            self.elevated_topology_sha256
            != self.production_plan.elevated_topology_sha256
        ):
            raise ValueError("elevated topology digest disagrees with production plan")
        selected = next(
            (
                row
                for row in self.production_plan.cameras
                if row.camera_id == self.camera.camera_id
            ),
            None,
        )
        if selected != self.camera:
            raise ValueError("camera does not match the immutable production plan")
        expected_blender = _opencv_c2w_to_blender(self.camera.c2w_opencv)
        if not np.allclose(
            self.requested_c2w_blender,
            expected_blender,
            atol=1e-9,
            rtol=0,
        ):
            raise ValueError("requested Blender matrix disagrees with camera pose")
        expected_object_sha256 = hashlib.sha256(
            canary._canonical_json_bytes(
                [row.model_dump(mode="json") for row in self.object_registry],
            ),
        ).hexdigest()
        if self.object_registry_sha256 != expected_object_sha256:
            raise ValueError("object registry digest is invalid")
        if tuple(row.instance_id for row in self.object_registry) != tuple(
            range(1, 131),
        ):
            raise ValueError("object registry is not the stable 130-instance contract")
        if self.auxiliary_registry != canary.AUXILIARY_REGISTRY:
            raise ValueError("auxiliary registry is not stable")
        if self.semantic_registry != canary._semantic_registry():
            raise ValueError("semantic registry is not stable")
        expected_render_id = production_render_id(
            self.production_plan,
            blender_executable_sha256=self.blender_executable_sha256,
            renderer_script_sha256=self.renderer_script_sha256,
            blend_sha256=self.blend_sha256,
            build_report_sha256=self.build_report_sha256,
            camera_registry_sha256=self.camera_registry_sha256,
        )
        if self.render_id != expected_render_id:
            raise ValueError("render ID does not bind the production inputs")
        return self


def build_local_production_frame_request(
    *,
    plan: ProductionCameraPlan,
    camera_id: str,
    build_id: str,
    blender_executable_sha256: str,
    renderer_script_sha256: str,
    blend_sha256: str,
    build_report_sha256: str,
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...],
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...],
) -> LocalProductionRenderFrameRequest:
    camera = next(
        (row for row in plan.cameras if row.camera_id == camera_id),
        None,
    )
    if camera is None:
        raise ValueError(f"camera ID is not in the production plan: {camera_id}")
    camera_registry_sha256 = production_camera_registry_digest(plan)
    object_registry_sha256 = hashlib.sha256(
        canary._canonical_json_bytes(
            [row.model_dump(mode="json") for row in object_registry],
        ),
    ).hexdigest()
    render_id = production_render_id(
        plan,
        blender_executable_sha256=blender_executable_sha256,
        renderer_script_sha256=renderer_script_sha256,
        blend_sha256=blend_sha256,
        build_report_sha256=build_report_sha256,
        camera_registry_sha256=camera_registry_sha256,
    )
    return LocalProductionRenderFrameRequest(
        production_plan_sha256=hashlib.sha256(
            canonical_production_plan_bytes(plan),
        ).hexdigest(),
        camera_registry_sha256=camera_registry_sha256,
        elevated_topology_sha256=plan.elevated_topology_sha256,
        production_plan=plan,
        render_id=render_id,
        build_id=build_id,
        blender_executable_sha256=blender_executable_sha256,
        renderer_script_sha256=renderer_script_sha256,
        blend_sha256=blend_sha256,
        build_report_sha256=build_report_sha256,
        object_registry_sha256=object_registry_sha256,
        settings=canary.RenderSettings(),
        camera=camera,
        requested_c2w_blender=_opencv_c2w_to_blender(camera.c2w_opencv),
        object_registry=object_registry,
        auxiliary_registry=auxiliary_registry,
        semantic_registry=semantic_registry,
    )


def canonical_local_production_render_request_bytes(
    request: LocalProductionRenderFrameRequest,
) -> bytes:
    return _canonical(request.model_dump(mode="json"))
