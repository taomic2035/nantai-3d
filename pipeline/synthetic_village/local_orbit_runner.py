"""Fail-closed exact-218 local waterwheel orbit evidence.

This module owns the audit-only eight-frame aggregate.  It never treats the
synthetic design references or rendered orbit as calibrated multiview input.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import canary
from .local_orbit_audit import (
    LocalOrbitAuditPlan,
    local_orbit_plan_sha256,
    materialize_local_orbit_render_plan,
)
from .production_journal import production_render_id
from .production_preflight import ProductionClearancePolicy
from .production_profile import (
    PRODUCTION_PROFILE_ID,
    ProductionCameraPlan,
    ProductionCameraPose,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)
from .production_quality_gates import (
    ProductionFrameEvidenceBinding,
    ProductionFrameQualityPolicyV2,
    build_production_frame_quality_report_v2,
    build_production_frame_quality_request_v2,
    canonical_production_frame_quality_report_v2_bytes,
    canonical_production_frame_quality_request_v2_bytes,
    production_frame_quality_policy_v2_sha256,
    verify_production_frame_quality_report_v2,
)
from .production_render import (
    LocalProductionQualityPolicy,
    evaluate_local_production_frame_quality,
    local_production_quality_policy_sha256,
)
from .reciprocal_route_production import (
    ReciprocalProductionError,
    ReciprocalProductionRenderFrameReport,
    VerifiedReciprocalProductionBuild,
    _build_reciprocal_camera_journal,
    _post_render_rejection_message,
    _remove_private_staging,
    build_reciprocal_production_clearance_request,
    canonical_reciprocal_production_camera_journal_bytes,
    canonical_reciprocal_production_clearance_report_bytes,
    canonical_reciprocal_production_clearance_request_bytes,
    load_reciprocal_production_camera_metadata,
    load_reciprocal_production_clearance_report,
    load_reciprocal_production_render_report,
    reciprocal_object_registry_sha256,
    verify_reciprocal_production_camera_metadata,
    verify_reciprocal_production_clearance_report,
    verify_reciprocal_production_render_frame,
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_EXPECTED_ORBIT_CAMERA_IDS = tuple(
    f"audit-waterwheel-az{azimuth:03d}" for azimuth in range(0, 360, 45)
)
_EXPECTED_MATERIALIZED_CAMERA_IDS = tuple(
    f"camera-audit-overview-{index:03d}" for index in range(1, 9)
)
_EXPECTED_AZIMUTHS = tuple(range(0, 360, 45))
WATERWHEEL_ASSEMBLY_INSTANCE_IDS = (155, 156, 157, 158, 159, 160)
LOCAL_ORBIT_RENDER_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-orbit-render-frame-request.v1"
)
LOCAL_ORBIT_BUILD_ADAPTER = "windows-reciprocal-route-local-orbit-v1"

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
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_local_orbit_build_bindings(
    *,
    plan: LocalOrbitAuditPlan,
    verified_build: VerifiedReciprocalProductionBuild,
    verified_environment_module_plan_sha256: str,
) -> None:
    """Reject a local-orbit plan that is not bound to current exact bytes."""

    try:
        report_sha256 = _sha256_file(verified_build.report_path)
        blend_sha256 = _sha256_file(verified_build.blend_path)
    except OSError as exc:
        raise ReciprocalProductionError(
            "local orbit exact-build binding files cannot be measured",
        ) from exc
    if (
        plan.exact_build_id != verified_build.build_id
        or plan.exact_blend_sha256 != verified_build.blend_sha256
        or plan.exact_blend_sha256 != blend_sha256
        or verified_build.report_sha256 != report_sha256
        or plan.environment_module_plan_sha256
        != verified_environment_module_plan_sha256
    ):
        raise ReciprocalProductionError(
            "local orbit plan binding disagrees with the verified exact build",
        )


def decode_local_orbit_instance_mask(
    path: Path,
    *,
    expected_sha256: str,
    registered_instance_ids: tuple[int, ...],
) -> tuple[LocalOrbitInstancePixelCount, ...]:
    """Verify and decode one uint16 instance mask into canonical counts."""

    path = Path(path)
    try:
        actual_sha256 = _sha256_file(path)
    except OSError as exc:
        raise ReciprocalProductionError(
            "local orbit instance mask cannot be measured",
        ) from exc
    if actual_sha256 != expected_sha256:
        raise ReciprocalProductionError("local orbit instance mask SHA disagrees")
    if registered_instance_ids != tuple(sorted(set(registered_instance_ids))):
        raise ReciprocalProductionError(
            "local orbit registered instance IDs are not canonical",
        )
    allowed = {0, *registered_instance_ids}
    try:
        with Image.open(path) as image:
            pixels = np.asarray(image, dtype=np.uint16)
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ReciprocalProductionError(
            "local orbit instance mask is not a readable uint16 image",
        ) from exc
    if pixels.ndim != 2 or pixels.size == 0:
        raise ReciprocalProductionError(
            "local orbit instance mask must be non-empty grayscale",
        )
    values, counts = np.unique(pixels, return_counts=True)
    observed = tuple(int(value) for value in values)
    unregistered = tuple(value for value in observed if value not in allowed)
    if unregistered:
        raise ReciprocalProductionError(
            "local orbit instance mask contains unregistered values: "
            + ", ".join(str(value) for value in unregistered),
        )
    return tuple(
        LocalOrbitInstancePixelCount(
            instance_id=int(value),
            pixel_count=int(count),
        )
        for value, count in zip(values, counts, strict=True)
    )


def _opencv_c2w_to_blender(matrix: Matrix4) -> Matrix4:
    converted = np.asarray(matrix, dtype=float) @ np.diag(
        [1.0, -1.0, -1.0, 1.0],
    )
    converted[converted == 0.0] = 0.0
    return tuple(
        tuple(float(converted[row, column]) for column in range(4))
        for row in range(4)
    )


def _local_orbit_frame_render_id(
    *,
    base_render_id: str,
    local_orbit_plan_digest: str,
    orbit_camera_id: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "base_render_id": base_render_id,
                "local_orbit_plan_sha256": local_orbit_plan_digest,
                "orbit_camera_id": orbit_camera_id,
            },
        ),
    ).hexdigest()


class LocalOrbitRenderFrameRequest(FrozenModel):
    """One exact-218 audit-only render request bound to an orbit camera."""

    schema_version: Literal[
        "nantai.synthetic-village.local-orbit-render-frame-request.v1"
    ] = LOCAL_ORBIT_RENDER_REQUEST_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = (
        PRODUCTION_PROFILE_ID
    )
    production_plan_sha256: Sha256
    camera_registry_sha256: Sha256
    elevated_topology_sha256: Sha256
    production_plan: ProductionCameraPlan
    source_production_plan_sha256: Sha256
    source_camera_registry_sha256: Sha256
    source_production_plan: ProductionCameraPlan
    local_orbit_plan_sha256: Sha256
    local_orbit_plan: LocalOrbitAuditPlan
    orbit_camera_id: str = Field(
        pattern=r"^audit-waterwheel-az(?:000|045|090|135|180|225|270|315)$",
    )
    render_id: Sha256
    build_adapter: Literal["windows-reciprocal-route-local-orbit-v1"] = (
        LOCAL_ORBIT_BUILD_ADAPTER
    )
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    fidelity: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    blender_executable_sha256: Sha256
    renderer_script_sha256: Sha256
    engine_script_sha256: Sha256
    blend_sha256: Sha256
    build_report_sha256: Sha256
    environment_module_build_report_sha256: Sha256
    reciprocal_route_module_plan_sha256: Sha256
    object_registry_sha256: Sha256
    preflight_id: Sha256
    quality_policy_sha256: Sha256
    post_render_policy: ProductionFrameQualityPolicyV2
    post_render_policy_sha256: Sha256
    settings: canary.RenderSettings
    camera: ProductionCameraPose
    required_visible_instance_ids: tuple[int, ...]
    requested_c2w_blender: Matrix4
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=218,
        max_length=218,
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
    def _request_is_fully_bound(self) -> LocalOrbitRenderFrameRequest:
        source_plan_sha = hashlib.sha256(
            canonical_production_plan_bytes(self.source_production_plan),
        ).hexdigest()
        if (
            self.source_production_plan_sha256 != source_plan_sha
            or self.source_camera_registry_sha256
            != production_camera_registry_digest(self.source_production_plan)
        ):
            raise ValueError("local orbit source production plan identity is invalid")
        if (
            self.local_orbit_plan_sha256
            != local_orbit_plan_sha256(self.local_orbit_plan)
            or self.local_orbit_plan.source_production_plan_sha256
            != self.source_production_plan_sha256
        ):
            raise ValueError("local orbit plan identity is invalid")
        expected_plan = materialize_local_orbit_render_plan(
            self.source_production_plan,
            self.local_orbit_plan,
        )
        if expected_plan != self.production_plan:
            raise ValueError("production plan is not the exact local orbit plan")
        if (
            self.production_plan_sha256
            != hashlib.sha256(
                canonical_production_plan_bytes(self.production_plan),
            ).hexdigest()
            or self.camera_registry_sha256
            != production_camera_registry_digest(self.production_plan)
            or self.elevated_topology_sha256
            != self.production_plan.elevated_topology_sha256
        ):
            raise ValueError("local orbit production plan identity is invalid")
        orbit = next(
            (
                row
                for row in self.local_orbit_plan.cameras
                if row.orbit_camera_id == self.orbit_camera_id
            ),
            None,
        )
        if orbit is None or orbit.materialized_camera_id != self.camera.camera_id:
            raise ValueError("local orbit camera mapping is invalid")
        selected = next(
            (
                row
                for row in self.production_plan.cameras
                if row.camera_id == self.camera.camera_id
            ),
            None,
        )
        if selected != self.camera:
            raise ValueError("local orbit camera is not in the immutable plan")
        if (
            self.local_orbit_plan.exact_build_id != self.build_id
            or self.local_orbit_plan.exact_blend_sha256 != self.blend_sha256
        ):
            raise ValueError("local orbit exact build identity is invalid")
        if self.required_visible_instance_ids != WATERWHEEL_ASSEMBLY_INSTANCE_IDS:
            raise ValueError("local orbit required instance IDs are not exact")
        if (
            reciprocal_object_registry_sha256(self.object_registry)
            != self.object_registry_sha256
            or self.auxiliary_registry != canary.AUXILIARY_REGISTRY
            or self.semantic_registry != canary._semantic_registry()  # noqa: SLF001
        ):
            raise ValueError("local orbit registry identity is invalid")
        if self.post_render_policy_sha256 != (
            production_frame_quality_policy_v2_sha256(self.post_render_policy)
        ):
            raise ValueError("local orbit post-render policy identity is invalid")
        if not np.allclose(
            self.requested_c2w_blender,
            _opencv_c2w_to_blender(self.camera.c2w_opencv),
            atol=1e-9,
            rtol=0,
        ):
            raise ValueError("local orbit Blender matrix is invalid")
        base_render_id = production_render_id(
            self.production_plan,
            blender_executable_sha256=self.blender_executable_sha256,
            renderer_script_sha256=self.renderer_script_sha256,
            engine_script_sha256=self.engine_script_sha256,
            blend_sha256=self.blend_sha256,
            build_report_sha256=self.build_report_sha256,
            camera_registry_sha256=self.camera_registry_sha256,
            preflight_id=self.preflight_id,
            quality_policy_sha256=self.quality_policy_sha256,
            post_render_policy_sha256=self.post_render_policy_sha256,
            build_adapter=self.build_adapter,
            environment_module_build_report_sha256=(
                self.environment_module_build_report_sha256
            ),
        )
        if self.render_id != _local_orbit_frame_render_id(
            base_render_id=base_render_id,
            local_orbit_plan_digest=self.local_orbit_plan_sha256,
            orbit_camera_id=self.orbit_camera_id,
        ):
            raise ValueError("local orbit render ID does not bind the frame")
        return self


def build_local_orbit_render_frame_request(
    *,
    plan: ProductionCameraPlan,
    source_plan: ProductionCameraPlan,
    local_orbit_plan: LocalOrbitAuditPlan,
    camera_id: str,
    build_id: str,
    blender_executable_sha256: str,
    renderer_script_sha256: str,
    engine_script_sha256: str,
    blend_sha256: str,
    build_report_sha256: str,
    environment_module_build_report_sha256: str,
    reciprocal_route_module_plan_sha256: str,
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...],
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...],
    preflight_id: str,
    quality_policy_sha256: str,
    post_render_policy: ProductionFrameQualityPolicyV2,
) -> LocalOrbitRenderFrameRequest:
    """Build one content-addressed exact-218 local-orbit request."""

    camera = next((row for row in plan.cameras if row.camera_id == camera_id), None)
    orbit = next(
        (
            row
            for row in local_orbit_plan.cameras
            if row.materialized_camera_id == camera_id
        ),
        None,
    )
    if camera is None or orbit is None:
        raise ReciprocalProductionError(
            f"camera is not one of the eight local orbit cameras: {camera_id}",
        )
    production_plan_sha256 = hashlib.sha256(
        canonical_production_plan_bytes(plan),
    ).hexdigest()
    camera_registry_sha256 = production_camera_registry_digest(plan)
    source_production_plan_sha256 = hashlib.sha256(
        canonical_production_plan_bytes(source_plan),
    ).hexdigest()
    local_plan_sha256 = local_orbit_plan_sha256(local_orbit_plan)
    post_render_policy_sha256 = production_frame_quality_policy_v2_sha256(
        post_render_policy,
    )
    base_render_id = production_render_id(
        plan,
        blender_executable_sha256=blender_executable_sha256,
        renderer_script_sha256=renderer_script_sha256,
        engine_script_sha256=engine_script_sha256,
        blend_sha256=blend_sha256,
        build_report_sha256=build_report_sha256,
        camera_registry_sha256=camera_registry_sha256,
        preflight_id=preflight_id,
        quality_policy_sha256=quality_policy_sha256,
        post_render_policy_sha256=post_render_policy_sha256,
        build_adapter=LOCAL_ORBIT_BUILD_ADAPTER,
        environment_module_build_report_sha256=(
            environment_module_build_report_sha256
        ),
    )
    return LocalOrbitRenderFrameRequest(
        production_plan_sha256=production_plan_sha256,
        camera_registry_sha256=camera_registry_sha256,
        elevated_topology_sha256=plan.elevated_topology_sha256,
        production_plan=plan,
        source_production_plan_sha256=source_production_plan_sha256,
        source_camera_registry_sha256=production_camera_registry_digest(source_plan),
        source_production_plan=source_plan,
        local_orbit_plan_sha256=local_plan_sha256,
        local_orbit_plan=local_orbit_plan,
        orbit_camera_id=orbit.orbit_camera_id,
        render_id=_local_orbit_frame_render_id(
            base_render_id=base_render_id,
            local_orbit_plan_digest=local_plan_sha256,
            orbit_camera_id=orbit.orbit_camera_id,
        ),
        build_id=build_id,
        blender_executable_sha256=blender_executable_sha256,
        renderer_script_sha256=renderer_script_sha256,
        engine_script_sha256=engine_script_sha256,
        blend_sha256=blend_sha256,
        build_report_sha256=build_report_sha256,
        environment_module_build_report_sha256=(
            environment_module_build_report_sha256
        ),
        reciprocal_route_module_plan_sha256=(
            reciprocal_route_module_plan_sha256
        ),
        object_registry_sha256=reciprocal_object_registry_sha256(object_registry),
        preflight_id=preflight_id,
        quality_policy_sha256=quality_policy_sha256,
        post_render_policy=post_render_policy,
        post_render_policy_sha256=post_render_policy_sha256,
        settings=canary.RenderSettings(),
        camera=camera,
        required_visible_instance_ids=WATERWHEEL_ASSEMBLY_INSTANCE_IDS,
        requested_c2w_blender=_opencv_c2w_to_blender(camera.c2w_opencv),
        object_registry=object_registry,
        auxiliary_registry=auxiliary_registry,
        semantic_registry=semantic_registry,
    )


def canonical_local_orbit_render_request_bytes(
    request: LocalOrbitRenderFrameRequest,
) -> bytes:
    return _canonical(request.model_dump(mode="json"))


class LocalOrbitInstancePixelCount(FrozenModel):
    """One measured instance value and its exact mask pixel count."""

    instance_id: int = Field(ge=0, le=218)
    pixel_count: int = Field(ge=0, le=1024 * 576)


class LocalOrbitFrameEvidence(FrozenModel):
    """Content identities and decoded instance evidence for one orbit frame."""

    orbit_camera_id: str = Field(
        pattern=r"^audit-waterwheel-az(?:000|045|090|135|180|225|270|315)$",
    )
    materialized_camera_id: str = Field(
        pattern=r"^camera-audit-overview-00[1-8]$",
    )
    azimuth_deg: int = Field(ge=0, lt=360, multiple_of=45)
    render_id: Sha256
    frame_report_sha256: Sha256
    instance_mask_sha256: Sha256
    rgb_sha256: Sha256
    depth_sha256: Sha256
    normal_sha256: Sha256
    semantic_sha256: Sha256
    camera_metadata_sha256: Sha256
    instance_pixel_counts: tuple[LocalOrbitInstancePixelCount, ...] = Field(
        min_length=1,
        max_length=219,
    )

    @model_validator(mode="after")
    def _counts_are_exact_and_registered(self) -> LocalOrbitFrameEvidence:
        instance_ids = tuple(row.instance_id for row in self.instance_pixel_counts)
        if instance_ids != tuple(sorted(set(instance_ids))):
            raise ValueError(
                "local orbit instance pixel counts must be unique and sorted",
            )
        return self

    @property
    def assembly_visible(self) -> bool:
        counts = {
            row.instance_id: row.pixel_count for row in self.instance_pixel_counts
        }
        return any(
            counts.get(instance_id, 0) > 0
            for instance_id in WATERWHEEL_ASSEMBLY_INSTANCE_IDS
        )

    @property
    def wheel_visible(self) -> bool:
        return any(
            row.instance_id == WATERWHEEL_ASSEMBLY_INSTANCE_IDS[0]
            and row.pixel_count > 0
            for row in self.instance_pixel_counts
        )


class LocalOrbitAuditReport(FrozenModel):
    """Canonical L0 result for the eight translated waterwheel views."""

    schema_version: Literal[
        "nantai.synthetic-village.local-orbit-audit-report.v1"
    ] = "nantai.synthetic-village.local-orbit-audit-report.v1"
    report_sha256: Sha256
    local_orbit_plan_sha256: Sha256
    exact_build_id: Sha256
    exact_blend_sha256: Sha256
    build_report_sha256: Sha256
    environment_module_build_report_sha256: Sha256
    reciprocal_route_module_plan_sha256: Sha256
    required_instance_ids: tuple[int, ...] = WATERWHEEL_ASSEMBLY_INSTANCE_IDS
    frames: tuple[LocalOrbitFrameEvidence, ...] = Field(
        min_length=8,
        max_length=8,
    )
    azimuth_bins_passed: Literal[8] = 8
    accepted_frame_count: Literal[8] = 8
    assembly_visible_frame_count: int = Field(ge=7, le=8)
    occluded_assembly_camera_ids: tuple[str, ...] = Field(max_length=1)
    wheel_visible_frame_count: int = Field(ge=0, le=8)
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    training_use: Literal["forbidden-as-multiview"] = (
        "forbidden-as-multiview"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _evidence_is_exact_and_content_addressed(self) -> LocalOrbitAuditReport:
        if self.required_instance_ids != WATERWHEEL_ASSEMBLY_INSTANCE_IDS:
            raise ValueError("local orbit required instance IDs are not exact")
        if tuple(row.orbit_camera_id for row in self.frames) != (
            _EXPECTED_ORBIT_CAMERA_IDS
        ):
            raise ValueError("local orbit report camera IDs are not exact")
        if tuple(row.materialized_camera_id for row in self.frames) != (
            _EXPECTED_MATERIALIZED_CAMERA_IDS
        ):
            raise ValueError("local orbit report materialized cameras are not exact")
        if tuple(row.azimuth_deg for row in self.frames) != _EXPECTED_AZIMUTHS:
            raise ValueError("local orbit report azimuth bins are not exact")
        render_ids = tuple(row.render_id for row in self.frames)
        if len(set(render_ids)) != len(render_ids):
            raise ValueError("local orbit frame render IDs must be unique")
        assembly_count = sum(row.assembly_visible for row in self.frames)
        occluded = tuple(
            row.orbit_camera_id for row in self.frames if not row.assembly_visible
        )
        if len(occluded) > 1:
            raise ValueError(
                "waterwheel assembly may be occluded in at most one frame",
            )
        if (
            self.assembly_visible_frame_count != assembly_count
            or self.occluded_assembly_camera_ids != occluded
        ):
            raise ValueError("waterwheel assembly visibility summary disagrees")
        wheel_count = sum(row.wheel_visible for row in self.frames)
        if wheel_count < 6:
            raise ValueError("waterwheel wheel must be visible in at least six frames")
        if self.wheel_visible_frame_count != wheel_count:
            raise ValueError("waterwheel visible-frame count disagrees")
        payload = self.model_dump(mode="json", exclude={"report_sha256"})
        if self.report_sha256 != hashlib.sha256(_canonical(payload)).hexdigest():
            raise ValueError("local orbit audit report SHA-256 is invalid")
        return self


def canonical_local_orbit_audit_report_bytes(
    report: LocalOrbitAuditReport,
) -> bytes:
    """Serialize one canonical local-orbit report."""

    return _canonical(report.model_dump(mode="json"))


def build_local_orbit_audit_report(
    *,
    plan: LocalOrbitAuditPlan,
    build_report_sha256: str,
    environment_module_build_report_sha256: str,
    reciprocal_route_module_plan_sha256: str,
    frames: tuple[LocalOrbitFrameEvidence, ...],
) -> LocalOrbitAuditReport:
    """Build and self-address one accepted eight-frame audit report."""

    missing_assembly = tuple(
        row.orbit_camera_id for row in frames if not row.assembly_visible
    )
    if len(missing_assembly) > 1:
        raise ReciprocalProductionError(
            "waterwheel assembly is absent from more than one local orbit frame: "
            + ", ".join(missing_assembly),
        )

    payload = {
        "schema_version": "nantai.synthetic-village.local-orbit-audit-report.v1",
        "local_orbit_plan_sha256": local_orbit_plan_sha256(plan),
        "exact_build_id": plan.exact_build_id,
        "exact_blend_sha256": plan.exact_blend_sha256,
        "build_report_sha256": build_report_sha256,
        "environment_module_build_report_sha256": (
            environment_module_build_report_sha256
        ),
        "reciprocal_route_module_plan_sha256": (
            reciprocal_route_module_plan_sha256
        ),
        "required_instance_ids": WATERWHEEL_ASSEMBLY_INSTANCE_IDS,
        "frames": frames,
        "azimuth_bins_passed": 8,
        "accepted_frame_count": 8,
        "assembly_visible_frame_count": sum(row.assembly_visible for row in frames),
        "occluded_assembly_camera_ids": missing_assembly,
        "wheel_visible_frame_count": sum(row.wheel_visible for row in frames),
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "training_use": "forbidden-as-multiview",
        "trust_effect": "none-quality-filter-only",
    }
    unsigned = LocalOrbitAuditReport.model_construct(
        report_sha256="0" * 64,
        **payload,
    )
    digest = hashlib.sha256(
        _canonical(unsigned.model_dump(mode="json", exclude={"report_sha256"})),
    ).hexdigest()
    return LocalOrbitAuditReport(report_sha256=digest, **payload)


@dataclass(frozen=True)
class LocalOrbitFrameRunResult:
    """One fully verified frame retained inside the private outer staging."""

    frame_root: Path
    request: LocalOrbitRenderFrameRequest
    report: ReciprocalProductionRenderFrameReport
    render_report_sha256: str


@dataclass(frozen=True)
class LocalOrbitAuditResult:
    """Atomically published eight-frame local-orbit result."""

    report: LocalOrbitAuditReport
    audit_root: Path
    report_path: Path


def _remove_local_orbit_staging(
    path: Path,
    *,
    parent: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Retry only transient Windows directory races on proven staging paths."""

    for attempt in range(5):
        try:
            _remove_private_staging(path, parent=parent)
            return
        except OSError as original:
            if os.name == "nt":
                extended = Path("\\\\?\\" + str(Path(path).absolute()))
                try:
                    shutil.rmtree(extended)
                    return
                except OSError:
                    pass
            if attempt == 4:
                raise original
            sleep(0.05 * (2**attempt))


def _run_exact_build_frame(
    *,
    verified_build: VerifiedReciprocalProductionBuild,
    plan: ProductionCameraPlan,
    source_plan: ProductionCameraPlan,
    local_orbit_plan: LocalOrbitAuditPlan,
    camera_id: str,
    required_visible_instance_ids: tuple[int, ...],
    blender_executable: Path,
    output_root: Path,
    clearance_policy: ProductionClearancePolicy,
    quality_policy: LocalProductionQualityPolicy,
    post_render_policy: ProductionFrameQualityPolicyV2,
    process_runner: Callable[..., subprocess.CompletedProcess[str]],
    timeout_seconds: int,
) -> LocalOrbitFrameRunResult:
    """Run the same exact-build preflight/six-layer/quality chain for one orbit."""

    if timeout_seconds <= 0:
        raise ReciprocalProductionError("local orbit runner timeout must be positive")
    if required_visible_instance_ids != WATERWHEEL_ASSEMBLY_INSTANCE_IDS:
        raise ReciprocalProductionError(
            "local orbit required visible instance IDs are not exact",
        )
    blender_executable = Path(blender_executable).resolve(strict=True)
    output_root = Path(output_root).resolve(strict=True)
    repo_root = Path(__file__).resolve().parents[2]
    preflight_script = (
        repo_root / "scripts/blender/preflight_reciprocal_route_cameras.py"
    ).resolve(strict=True)
    renderer_script = (
        repo_root / "scripts/blender/render_local_orbit.py"
    ).resolve(strict=True)
    engine_script = (
        repo_root / "scripts/blender/render_synthetic_village.py"
    ).resolve(strict=True)
    if (
        _sha256_file(verified_build.report_path) != verified_build.report_sha256
        or _sha256_file(verified_build.blend_path) != verified_build.blend_sha256
    ):
        raise ReciprocalProductionError(
            "verified reciprocal build changed before local orbit preflight",
        )
    preflight_request = build_reciprocal_production_clearance_request(
        plan=plan,
        selected_camera_ids=(camera_id,),
        build_id=verified_build.build_id,
        blender_executable_sha256=_sha256_file(blender_executable),
        preflight_script_sha256=_sha256_file(preflight_script),
        blend_sha256=verified_build.blend_sha256,
        build_report_sha256=verified_build.report_sha256,
        environment_module_build_report_sha256=(
            verified_build.environment_module_build_report_sha256
        ),
        reciprocal_route_module_plan_sha256=(
            verified_build.reciprocal_route_module_plan_sha256
        ),
        object_registry=verified_build.object_registry,
        auxiliary_registry=canary.AUXILIARY_REGISTRY,
        semantic_registry=canary._semantic_registry(),  # noqa: SLF001
        policy=clearance_policy,
    )
    staging = output_root / f".staging-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        preflight_request_path = staging / "preflight-request.json"
        preflight_report_path = staging / "preflight-report.json"
        preflight_request_bytes = (
            canonical_reciprocal_production_clearance_request_bytes(
                preflight_request,
            )
        )
        canary._write_new_file(  # noqa: SLF001
            preflight_request_path,
            preflight_request_bytes,
        )
        snapshots = (
            canary._snapshot_regular_file(blender_executable),  # noqa: SLF001
            canary._snapshot_regular_file(verified_build.blend_path),  # noqa: SLF001
            canary._snapshot_regular_file(verified_build.report_path),  # noqa: SLF001
            canary._snapshot_regular_file(preflight_script),  # noqa: SLF001
            canary._snapshot_regular_file(preflight_request_path),  # noqa: SLF001
        )
        started = time.monotonic()
        try:
            completed = process_runner(
                [
                    str(blender_executable),
                    "--background",
                    str(verified_build.blend_path),
                    "--python",
                    str(preflight_script),
                    "--",
                    "--request",
                    str(preflight_request_path),
                    "--report",
                    str(preflight_report_path),
                ],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReciprocalProductionError(
                f"local orbit preflight exceeded {timeout_seconds} seconds",
            ) from exc
        preflight_wall_clock_seconds = time.monotonic() - started
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ReciprocalProductionError(
                "local orbit Blender preflight failed"
                + (f": {detail[-2000:]}" if detail else ""),
            )
        canary._verify_snapshots_unchanged(snapshots)  # noqa: SLF001
        preflight_report = load_reciprocal_production_clearance_report(
            preflight_report_path,
        )
        verify_reciprocal_production_clearance_report(
            preflight_report,
            request=preflight_request,
        )
        decision = preflight_report.decisions[0]
        if not decision.passes:
            raise ReciprocalProductionError(
                f"preflight rejected camera: {camera_id}",
            )
        preflight_request_sha256 = hashlib.sha256(
            preflight_request_bytes,
        ).hexdigest()
        preflight_report_sha256 = _sha256_file(preflight_report_path)
        render_request = build_local_orbit_render_frame_request(
            plan=plan,
            source_plan=source_plan,
            local_orbit_plan=local_orbit_plan,
            camera_id=camera_id,
            build_id=verified_build.build_id,
            blender_executable_sha256=_sha256_file(blender_executable),
            renderer_script_sha256=_sha256_file(renderer_script),
            engine_script_sha256=_sha256_file(engine_script),
            blend_sha256=verified_build.blend_sha256,
            build_report_sha256=verified_build.report_sha256,
            environment_module_build_report_sha256=(
                verified_build.environment_module_build_report_sha256
            ),
            reciprocal_route_module_plan_sha256=(
                verified_build.reciprocal_route_module_plan_sha256
            ),
            object_registry=verified_build.object_registry,
            auxiliary_registry=canary.AUXILIARY_REGISTRY,
            semantic_registry=canary._semantic_registry(),  # noqa: SLF001
            preflight_id=preflight_request.preflight_id,
            quality_policy_sha256=local_production_quality_policy_sha256(
                quality_policy,
            ),
            post_render_policy=post_render_policy,
        )
        render_request_bytes = canonical_local_orbit_render_request_bytes(
            render_request,
        )
        render_request_sha256 = hashlib.sha256(render_request_bytes).hexdigest()
        render_request_path = staging / "render-request.json"
        canary._write_new_file(  # noqa: SLF001
            render_request_path,
            render_request_bytes,
        )
        frame_output = staging / "frame"
        render_snapshots = (
            canary._snapshot_regular_file(blender_executable),  # noqa: SLF001
            canary._snapshot_regular_file(verified_build.blend_path),  # noqa: SLF001
            canary._snapshot_regular_file(verified_build.report_path),  # noqa: SLF001
            canary._snapshot_regular_file(renderer_script),  # noqa: SLF001
            canary._snapshot_regular_file(engine_script),  # noqa: SLF001
            canary._snapshot_regular_file(render_request_path),  # noqa: SLF001
        )
        render_started = time.monotonic()
        try:
            rendered = process_runner(
                [
                    str(blender_executable),
                    "--background",
                    str(verified_build.blend_path),
                    "--python",
                    str(renderer_script),
                    "--",
                    "--request",
                    str(render_request_path),
                    "--staging",
                    str(frame_output),
                ],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReciprocalProductionError(
                f"local orbit render exceeded {timeout_seconds} seconds",
            ) from exc
        render_wall_clock_seconds = time.monotonic() - render_started
        if rendered.returncode != 0:
            detail = (rendered.stderr or rendered.stdout).strip()
            raise ReciprocalProductionError(
                "local orbit Blender render failed"
                + (f": {detail[-2000:]}" if detail else ""),
            )
        canary._verify_snapshots_unchanged(render_snapshots)  # noqa: SLF001
        frame_report_path = frame_output / "frame-report.json"
        frame_report = load_reciprocal_production_render_report(
            frame_report_path,
        )
        verify_reciprocal_production_render_frame(
            frame_report,
            request=render_request,
            frame_root=frame_output,
        )
        metadata = load_reciprocal_production_camera_metadata(
            frame_output / f"cameras/{camera_id}.json",
        )
        verify_reciprocal_production_camera_metadata(
            metadata,
            request=render_request,
        )
        local_quality = evaluate_local_production_frame_quality(
            frame_report.statistics,
            policy=quality_policy,
        )
        if not local_quality.passes:
            raise ReciprocalProductionError(
                f"local quality rejected camera: {camera_id}",
            )
        render_report_sha256 = _sha256_file(frame_report_path)
        journal = _build_reciprocal_camera_journal(
            request=render_request,
            preflight_request_sha256=preflight_request_sha256,
            preflight_report_sha256=preflight_report_sha256,
            render_request_sha256=render_request_sha256,
            render_report_sha256=render_report_sha256,
            report=frame_report,
            decision=decision,
            local_quality=local_quality,
            preflight_wall_clock_seconds=preflight_wall_clock_seconds,
            render_wall_clock_seconds=render_wall_clock_seconds,
        )
        quality_request = build_production_frame_quality_request_v2(
            plan=plan,
            selected_camera_ids=(camera_id,),
            build_id=render_request.build_id,
            render_id=render_request.render_id,
            blender_executable_sha256=(
                render_request.blender_executable_sha256
            ),
            renderer_script_sha256=render_request.renderer_script_sha256,
            blend_sha256=render_request.blend_sha256,
            build_report_sha256=render_request.build_report_sha256,
            object_registry=render_request.object_registry,
            semantic_registry=render_request.semantic_registry,
            journal_sha256=journal.journal_sha256,
            frames=(
                ProductionFrameEvidenceBinding(
                    camera_id=camera_id,
                    runtime_report_sha256=render_report_sha256,
                    artifacts=frame_report.artifacts,
                ),
            ),
            policy=post_render_policy,
        )
        quality_report = build_production_frame_quality_report_v2(
            quality_request,
            statistics=(frame_report.layer_statistics,),
        )
        verify_production_frame_quality_report_v2(
            quality_report,
            request=quality_request,
        )
        if quality_report.rejected_camera_ids:
            raise ReciprocalProductionError(
                _post_render_rejection_message(quality_report, camera_id),
            )
        evidence_root = frame_output / "evidence"
        evidence_root.mkdir()
        evidence_payloads = {
            "preflight-request.json": preflight_request_bytes,
            "preflight-report.json": (
                canonical_reciprocal_production_clearance_report_bytes(
                    preflight_report,
                )
            ),
            "render-request.json": render_request_bytes,
            "journal.json": (
                canonical_reciprocal_production_camera_journal_bytes(journal)
            ),
            "quality-request.json": (
                canonical_production_frame_quality_request_v2_bytes(
                    quality_request,
                )
            ),
            "quality-report.json": (
                canonical_production_frame_quality_report_v2_bytes(
                    quality_report,
                )
            ),
        }
        for name, payload in evidence_payloads.items():
            canary._write_new_file(evidence_root / name, payload)  # noqa: SLF001
        final_parent = output_root / render_request.render_id
        final_parent.mkdir(exist_ok=True)
        if canary._is_linklike(final_parent):  # noqa: SLF001
            raise ReciprocalProductionError(
                "local orbit final render parent is redirected",
            )
        final = final_parent / camera_id
        if final.exists() or canary._is_linklike(final):  # noqa: SLF001
            raise ReciprocalProductionError(
                "local orbit final camera directory already exists",
            )
        frame_output.rename(final)
        canary._flush_directory(final_parent)  # noqa: SLF001
        return LocalOrbitFrameRunResult(
            frame_root=final,
            request=render_request,
            report=frame_report,
            render_report_sha256=render_report_sha256,
        )
    finally:
        if staging.exists():
            _remove_local_orbit_staging(staging, parent=output_root)


def _artifact_by_kind(
    report: ReciprocalProductionRenderFrameReport,
    kind: str,
):
    artifact = next((row for row in report.artifacts if row.kind == kind), None)
    if artifact is None:
        raise ReciprocalProductionError(
            f"local orbit frame is missing required artifact: {kind}",
        )
    return artifact


def run_local_orbit_audit(
    *,
    verified_build: VerifiedReciprocalProductionBuild,
    source_plan: ProductionCameraPlan,
    local_orbit_plan: LocalOrbitAuditPlan,
    verified_environment_module_plan_sha256: str,
    blender_executable: Path,
    output_root: Path,
    clearance_policy: ProductionClearancePolicy,
    quality_policy: LocalProductionQualityPolicy,
    post_render_policy: ProductionFrameQualityPolicyV2,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout_seconds: int = 1800,
) -> LocalOrbitAuditResult:
    """Run and atomically publish all eight exact-build orbit frames."""

    validate_local_orbit_build_bindings(
        plan=local_orbit_plan,
        verified_build=verified_build,
        verified_environment_module_plan_sha256=(
            verified_environment_module_plan_sha256
        ),
    )
    plan = materialize_local_orbit_render_plan(source_plan, local_orbit_plan)
    camera_ids = tuple(row.materialized_camera_id for row in local_orbit_plan.cameras)
    if camera_ids != _EXPECTED_MATERIALIZED_CAMERA_IDS:
        raise ReciprocalProductionError("local orbit selected camera set is not exact")
    output_root = Path(output_root).absolute()
    output_root.mkdir(parents=True, exist_ok=True)
    output_root = output_root.resolve(strict=True)
    if canary._is_linklike(output_root):  # noqa: SLF001
        raise ReciprocalProductionError("local orbit output root is redirected")
    outer_staging = output_root / f".staging-{uuid.uuid4().hex}"
    outer_staging.mkdir()
    try:
        frames_root = outer_staging / "frames"
        frames_root.mkdir()
        frame_runs = tuple(
            _run_exact_build_frame(
                verified_build=verified_build,
                plan=plan,
                source_plan=source_plan,
                local_orbit_plan=local_orbit_plan,
                camera_id=camera_id,
                required_visible_instance_ids=WATERWHEEL_ASSEMBLY_INSTANCE_IDS,
                blender_executable=blender_executable,
                output_root=frames_root,
                clearance_policy=clearance_policy,
                quality_policy=quality_policy,
                post_render_policy=post_render_policy,
                process_runner=process_runner,
                timeout_seconds=timeout_seconds,
            )
            for camera_id in camera_ids
        )
        registered_instance_ids = tuple(
            row.instance_id for row in verified_build.object_registry
        )
        frame_evidence = []
        for orbit_camera, frame_run in zip(
            local_orbit_plan.cameras,
            frame_runs,
            strict=True,
        ):
            artifacts = {
                kind: _artifact_by_kind(frame_run.report, kind)
                for kind in (
                    "rgb",
                    "depth",
                    "normal",
                    "instance-mask",
                    "semantic-mask",
                    "camera-metadata",
                )
            }
            instance_artifact = artifacts["instance-mask"]
            counts = decode_local_orbit_instance_mask(
                frame_run.frame_root / instance_artifact.path,
                expected_sha256=instance_artifact.sha256,
                registered_instance_ids=registered_instance_ids,
            )
            frame_evidence.append(
                LocalOrbitFrameEvidence(
                    orbit_camera_id=orbit_camera.orbit_camera_id,
                    materialized_camera_id=orbit_camera.materialized_camera_id,
                    azimuth_deg=orbit_camera.azimuth_deg,
                    render_id=frame_run.request.render_id,
                    frame_report_sha256=frame_run.render_report_sha256,
                    instance_mask_sha256=instance_artifact.sha256,
                    rgb_sha256=artifacts["rgb"].sha256,
                    depth_sha256=artifacts["depth"].sha256,
                    normal_sha256=artifacts["normal"].sha256,
                    semantic_sha256=artifacts["semantic-mask"].sha256,
                    camera_metadata_sha256=(
                        artifacts["camera-metadata"].sha256
                    ),
                    instance_pixel_counts=counts,
                ),
            )
        report = build_local_orbit_audit_report(
            plan=local_orbit_plan,
            build_report_sha256=verified_build.report_sha256,
            environment_module_build_report_sha256=(
                verified_build.environment_module_build_report_sha256
            ),
            reciprocal_route_module_plan_sha256=(
                verified_build.reciprocal_route_module_plan_sha256
            ),
            frames=tuple(frame_evidence),
        )
        report_path = outer_staging / "local-orbit-audit-report.json"
        canary._write_new_file(  # noqa: SLF001
            report_path,
            canonical_local_orbit_audit_report_bytes(report),
        )
        final = output_root / report.report_sha256
        if final.exists() or canary._is_linklike(final):  # noqa: SLF001
            raise ReciprocalProductionError(
                "local orbit audit result already exists",
            )
        outer_staging.rename(final)
        canary._flush_directory(output_root)  # noqa: SLF001
        return LocalOrbitAuditResult(
            report=report,
            audit_root=final,
            report_path=final / report_path.name,
        )
    finally:
        if outer_staging.exists():
            _remove_local_orbit_staging(outer_staging, parent=output_root)
