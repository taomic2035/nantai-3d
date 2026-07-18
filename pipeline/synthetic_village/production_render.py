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
from .camera_plan import CameraIntrinsics
from .production_journal import (
    DEFAULT_RENDER_TIMEOUT_SECONDS,
    ProductionArtifactRecord,
    expected_production_artifacts,
    production_render_id,
)
from .production_profile import (
    PRODUCTION_PROFILE_ID,
    ProductionCameraPlan,
    ProductionCameraPose,
    ProductionProfileError,
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
LOCAL_PRODUCTION_RENDER_JOURNAL_SCHEMA = (
    "nantai.synthetic-village.local-production-render-journal.v1"
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


class LocalProductionQualityPolicy(FrozenModel):
    """Operator-selected training suitability threshold, never a trust upgrade."""

    minimum_valid_pixel_ratio: float = Field(
        gt=0.0,
        le=1.0,
        allow_inf_nan=False,
    )


class LocalProductionFrameQuality(FrozenModel):
    total_pixel_count: Literal[589824] = 1024 * 576
    background_pixel_count: int = Field(ge=0, le=1024 * 576)
    valid_pixel_count: int = Field(ge=0, le=1024 * 576)
    valid_pixel_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    minimum_valid_pixel_ratio: float = Field(
        gt=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    passes: bool
    trust_effect: Literal["none-quality-filter-only"] = "none-quality-filter-only"

    @model_validator(mode="after")
    def _validate_measurement(self) -> LocalProductionFrameQuality:
        if self.valid_pixel_count != self.total_pixel_count - self.background_pixel_count:
            raise ValueError("valid pixel count disagrees with the measured background")
        expected_ratio = round(self.valid_pixel_count / self.total_pixel_count, 6)
        if self.valid_pixel_ratio != expected_ratio:
            raise ValueError("valid pixel ratio is not the canonical measured ratio")
        if self.passes != (
            self.valid_pixel_ratio >= self.minimum_valid_pixel_ratio
        ):
            raise ValueError("quality decision disagrees with the declared threshold")
        return self


def evaluate_local_production_frame_quality(
    statistics: canary.RenderStatistics,
    *,
    policy: LocalProductionQualityPolicy,
) -> LocalProductionFrameQuality:
    total = 1024 * 576
    valid = total - statistics.depth_background_pixels
    ratio = round(valid / total, 6)
    return LocalProductionFrameQuality(
        background_pixel_count=statistics.depth_background_pixels,
        valid_pixel_count=valid,
        valid_pixel_ratio=ratio,
        minimum_valid_pixel_ratio=policy.minimum_valid_pixel_ratio,
        passes=ratio >= policy.minimum_valid_pixel_ratio,
    )


class LocalProductionFrameRecord(FrozenModel):
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    state: Literal[
        "planned",
        "rendering",
        "verified",
        "rejected",
        "failed",
        "timed-out",
    ]
    artifacts: tuple[ProductionArtifactRecord, ...] = ()
    runtime_report_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    statistics: canary.RenderStatistics | None = None
    quality: LocalProductionFrameQuality | None = None
    wall_clock_seconds: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
    )
    timeout_limit_seconds: int = Field(ge=1, le=86400)
    error: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _validate_state(self) -> LocalProductionFrameRecord:
        if self.state in {"verified", "rejected"}:
            if tuple((row.kind, row.path) for row in self.artifacts) != (
                expected_production_artifacts(self.camera_id)
            ):
                raise ValueError("completed frame does not have the exact six-file contract")
            if (
                self.runtime_report_sha256 is None
                or self.statistics is None
                or self.quality is None
                or self.wall_clock_seconds is None
            ):
                raise ValueError("completed frame lacks runtime or quality evidence")
            if self.error is not None:
                raise ValueError("completed frame cannot carry an execution error")
            if self.state == "verified" and not self.quality.passes:
                raise ValueError("verified frame does not pass its quality threshold")
            if self.state == "rejected" and self.quality.passes:
                raise ValueError("rejected frame passes its quality threshold")
        elif self.state in {"failed", "timed-out"}:
            if (
                self.artifacts
                or self.runtime_report_sha256 is not None
                or self.statistics is not None
                or self.quality is not None
            ):
                raise ValueError("failed frame cannot publish verified evidence")
            if self.error is None or self.wall_clock_seconds is None:
                raise ValueError("failed frame must report its error and real duration")
            if (
                self.state == "timed-out"
                and self.wall_clock_seconds < self.timeout_limit_seconds
            ):
                raise ValueError("timed-out frame ran less than its declared timeout")
        elif (
            self.artifacts
            or self.runtime_report_sha256 is not None
            or self.statistics is not None
            or self.quality is not None
            or self.wall_clock_seconds is not None
            or self.error is not None
        ):
            raise ValueError("unfinished frame cannot claim output evidence")
        return self


class LocalProductionRenderJournal(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.local-production-render-journal.v1"
    ] = LOCAL_PRODUCTION_RENDER_JOURNAL_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = PRODUCTION_PROFILE_ID
    render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    elevated_topology_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    quality_policy: LocalProductionQualityPolicy
    frames: tuple[LocalProductionFrameRecord, ...] = Field(
        min_length=180,
        max_length=180,
    )

    @model_validator(mode="after")
    def _validate_frames(self) -> LocalProductionRenderJournal:
        camera_ids = tuple(row.camera_id for row in self.frames)
        if len(set(camera_ids)) != 180:
            raise ValueError("local production journal camera IDs must be unique")
        return self


def canonical_local_production_render_journal_bytes(
    journal: LocalProductionRenderJournal,
) -> bytes:
    return _canonical(journal.model_dump(mode="json"))


def compute_local_production_journal_sha256(
    journal: LocalProductionRenderJournal,
) -> str:
    payload = journal.model_dump(mode="json")
    payload.pop("journal_sha256", None)
    return hashlib.sha256(_canonical(payload)).hexdigest()


def new_local_production_render_journal(
    request: LocalProductionRenderFrameRequest,
    *,
    quality_policy: LocalProductionQualityPolicy,
    timeout_limit_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> LocalProductionRenderJournal:
    journal = LocalProductionRenderJournal(
        render_id=request.render_id,
        journal_sha256="0" * 64,
        build_id=request.build_id,
        production_plan_sha256=request.production_plan_sha256,
        camera_registry_sha256=request.camera_registry_sha256,
        elevated_topology_sha256=request.elevated_topology_sha256,
        blender_executable_sha256=request.blender_executable_sha256,
        renderer_script_sha256=request.renderer_script_sha256,
        blend_sha256=request.blend_sha256,
        build_report_sha256=request.build_report_sha256,
        object_registry_sha256=request.object_registry_sha256,
        quality_policy=quality_policy,
        frames=tuple(
            LocalProductionFrameRecord(
                camera_id=row.camera_id,
                state="planned",
                timeout_limit_seconds=timeout_limit_seconds,
            )
            for row in request.production_plan.cameras
        ),
    )
    return journal.model_copy(
        update={
            "journal_sha256": compute_local_production_journal_sha256(journal),
        },
    )


def transition_local_production_frame(
    journal: LocalProductionRenderJournal,
    camera_id: str,
    **updates: object,
) -> LocalProductionRenderJournal:
    if camera_id not in {row.camera_id for row in journal.frames}:
        raise ProductionProfileError(
            f"camera ID is not in the local production journal: {camera_id}",
        )
    moved = journal.model_copy(
        update={
            "frames": tuple(
                row.model_copy(update=updates) if row.camera_id == camera_id else row
                for row in journal.frames
            ),
        },
    )
    revalidated = LocalProductionRenderJournal.model_validate_json(
        canonical_local_production_render_journal_bytes(moved),
    )
    return revalidated.model_copy(
        update={
            "journal_sha256": compute_local_production_journal_sha256(
                revalidated,
            ),
        },
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


class LocalProductionRenderFrameReport(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.local-production-render-frame-report.v1"
    ] = LOCAL_PRODUCTION_RENDER_REPORT_SCHEMA
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    fidelity: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    image_width_px: Literal[1024]
    image_height_px: Literal[576]
    depth_encoding: Literal["euclidean-camera-center-range-m"]
    normal_encoding: Literal["world-space-unit-vector"]
    depth_channel_layout: Literal["V-float32-zip"]
    normal_channel_layout: Literal["X,Y,Z-float32-zip"]
    instance_pixel_type: Literal["uint16-grayscale-png"]
    semantic_pixel_type: Literal["uint8-grayscale-png"]
    settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: tuple[ProductionArtifactRecord, ...] = Field(
        min_length=6,
        max_length=6,
    )
    statistics: canary.RenderStatistics
    validation: canary.RenderValidation
    profile_id: Literal["synthetic-village-coverage-180-v1"] = PRODUCTION_PROFILE_ID
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    elevated_topology_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    group_id: str = Field(min_length=1)
    topology_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_artifacts(self) -> LocalProductionRenderFrameReport:
        if tuple((row.kind, row.path) for row in self.artifacts) != (
            expected_production_artifacts(self.camera_id)
        ):
            raise ValueError("frame report does not have the exact six-file contract")
        return self


class LocalProductionCameraMetadata(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.local-production-camera-metadata.v1"
    ] = LOCAL_PRODUCTION_CAMERA_METADATA_SCHEMA
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    image_width_px: Literal[1024]
    image_height_px: Literal[576]
    coordinate_system: Literal["opencv-c2w-right-down-forward-meters"]
    pixel_origin: Literal["top-left"]
    pixel_center_offset: tuple[Literal[0.5], Literal[0.5]]
    depth_encoding: Literal["euclidean-camera-center-range-m"]
    depth_units: Literal["m"]
    depth_invalid_value_m: Literal[0.0]
    normal_encoding: Literal["world-space-unit-vector"]
    normal_axes: Literal["blender-right-handed-z-up"]
    normal_background_xyz: tuple[Literal[0.0], Literal[0.0], Literal[0.0]]
    clip_start_m: Literal[0.1]
    clip_end_m: Literal[1200.0]
    depth_channel_layout: Literal["V-float32-zip"]
    normal_channel_layout: Literal["X,Y,Z-float32-zip"]
    instance_pixel_type: Literal["uint16-grayscale-png"]
    semantic_pixel_type: Literal["uint8-grayscale-png"]
    settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intrinsics: CameraIntrinsics
    requested_c2w_opencv: Matrix4
    requested_c2w_blender: Matrix4
    measured_c2w_opencv: Matrix4
    measured_c2w_blender: Matrix4
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...] = Field(
        min_length=15,
        max_length=15,
    )
    profile_id: Literal["synthetic-village-coverage-180-v1"] = PRODUCTION_PROFILE_ID
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    elevated_topology_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    group_id: str = Field(min_length=1)
    topology_ref: str = Field(min_length=1)
    arc_length_m: float | None = Field(default=None, allow_inf_nan=False)
    audit_only: bool
    disclosure: str = Field(min_length=10)


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


def canonical_local_production_render_report_bytes(
    report: LocalProductionRenderFrameReport,
    *,
    exclude_sha256: bool = False,
) -> bytes:
    exclude = {"content_sha256"} if exclude_sha256 else None
    return _canonical(report.model_dump(mode="json", exclude=exclude))


def canonical_local_production_camera_metadata_bytes(
    metadata: LocalProductionCameraMetadata,
) -> bytes:
    return _canonical(metadata.model_dump(mode="json"))
