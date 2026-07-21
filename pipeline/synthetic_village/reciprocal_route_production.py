"""Additive exact-218 production caller contracts for reciprocal-route builds.

This module is intentionally separate from the frozen 130-root production caller.
It never promotes the synthetic scene beyond its existing L0, preview-only trust.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import canary
from .production_journal import (
    ProductionArtifactRecord,
    expected_production_artifacts,
    production_render_id,
)
from .production_preflight import (
    ProductionCameraClearanceDecision,
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    evaluate_production_camera_clearance,
    production_clearance_policy_sha256,
)
from .production_profile import (
    PRODUCTION_PROFILE_ID,
    ProductionCameraPlan,
    ProductionCameraPose,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)
from .production_quality_gates import (
    ProductionFrameEvidenceBinding,
    ProductionFrameLayerStatistics,
    ProductionFrameQualityPolicyV2,
    build_production_frame_quality_report_v2,
    build_production_frame_quality_request_v2,
    canonical_production_frame_quality_report_v2_bytes,
    canonical_production_frame_quality_request_v2_bytes,
    production_frame_quality_policy_v2_sha256,
    verify_production_frame_quality_report_v2,
)
from .production_render import (
    LocalProductionCameraMetadata,
    LocalProductionFrameQuality,
    LocalProductionQualityPolicy,
    LocalProductionRenderFrameReport,
    evaluate_local_production_frame_quality,
    local_production_quality_policy_sha256,
)
from .reciprocal_route_module_runtime import (
    ReciprocalRouteRuntimeRequest,
    load_reciprocal_route_build_report,
    verify_reciprocal_route_build_report,
)

RECIPROCAL_RENDER_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-request.v5"
)
RECIPROCAL_RENDER_REPORT_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-report.v4"
)
RECIPROCAL_CAMERA_METADATA_SCHEMA = (
    "nantai.synthetic-village.local-production-camera-metadata.v4"
)
RECIPROCAL_CLEARANCE_REQUEST_SCHEMA = (
    "nantai.synthetic-village.reciprocal-production-clearance-request.v1"
)
RECIPROCAL_CLEARANCE_REPORT_SCHEMA = (
    "nantai.synthetic-village.reciprocal-production-clearance-report.v1"
)
RECIPROCAL_BUILD_ADAPTER = "windows-reciprocal-route-v1"

Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReciprocalProductionError(RuntimeError):
    """Raised when exact reciprocal-route production evidence disagrees."""


@dataclass(frozen=True)
class VerifiedReciprocalProductionBuild:
    """Measured identity chain for one verified reciprocal-route build."""

    build_id: str
    report_path: Path
    report_sha256: str
    blend_path: Path
    blend_sha256: str
    environment_module_build_report_sha256: str
    reciprocal_route_module_plan_sha256: str
    object_registry: tuple[canary.ObjectRegistryEntry, ...]


@dataclass(frozen=True)
class ReciprocalProductionCameraResult:
    """One atomically published, quality-accepted camera evidence bundle."""

    render_id: str
    camera_id: str
    frame_root: Path
    preflight_request_sha256: str
    preflight_report_sha256: str
    render_request_sha256: str
    render_report_sha256: str
    journal_sha256: str
    quality_request_sha256: str
    quality_report_sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _opencv_c2w_to_blender(matrix: Matrix4) -> Matrix4:
    converted = np.asarray(matrix, dtype=float) @ np.diag(
        [1.0, -1.0, -1.0, 1.0],
    )
    converted[converted == 0.0] = 0.0
    return tuple(
        tuple(float(converted[row, column]) for column in range(4))
        for row in range(4)
    )


def _preflight_id_from_payload(payload: dict[str, object]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def require_exact_reciprocal_object_registry(
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
) -> None:
    """Require the canonical reciprocal-route instance segment 1..218."""

    if tuple(row.instance_id for row in object_registry) != tuple(range(1, 219)):
        raise ReciprocalProductionError(
            "reciprocal-route object registry is not exact 1..218",
        )


def reciprocal_object_registry_sha256(
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
) -> str:
    """Hash one exact registry after enforcing its complete instance sequence."""

    require_exact_reciprocal_object_registry(object_registry)
    return hashlib.sha256(
        canary._canonical_json_bytes(  # noqa: SLF001
            [row.model_dump(mode="json") for row in object_registry],
        ),
    ).hexdigest()


class ReciprocalProductionClearanceRequest(FrozenModel):
    """Fresh ray-probe request bound to one exact-218 Blender scene."""

    schema_version: Literal[
        "nantai.synthetic-village.reciprocal-production-clearance-request.v1"
    ] = RECIPROCAL_CLEARANCE_REQUEST_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = (
        PRODUCTION_PROFILE_ID
    )
    production_plan: ProductionCameraPlan
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_camera_ids: tuple[str, ...] = Field(min_length=1, max_length=180)
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_module_build_report_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    reciprocal_route_module_plan_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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
    policy: ProductionClearancePolicy
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _validate_request(self) -> ReciprocalProductionClearanceRequest:
        expected_plan_sha256 = hashlib.sha256(
            canonical_production_plan_bytes(self.production_plan),
        ).hexdigest()
        if self.production_plan_sha256 != expected_plan_sha256:
            raise ValueError("production plan digest is invalid")
        if self.camera_registry_sha256 != production_camera_registry_digest(
            self.production_plan,
        ):
            raise ValueError("production camera registry digest is invalid")
        all_camera_ids = tuple(
            row.camera_id for row in self.production_plan.cameras
        )
        selected = set(self.selected_camera_ids)
        expected_selected = tuple(
            camera_id for camera_id in all_camera_ids if camera_id in selected
        )
        if (
            len(selected) != len(self.selected_camera_ids)
            or self.selected_camera_ids != expected_selected
        ):
            raise ValueError(
                "selected camera IDs must be a unique plan-ordered subset",
            )
        try:
            expected_registry_sha256 = reciprocal_object_registry_sha256(
                self.object_registry,
            )
        except ReciprocalProductionError as exc:
            raise ValueError(str(exc)) from exc
        if self.object_registry_sha256 != expected_registry_sha256:
            raise ValueError("object registry digest is invalid")
        if self.auxiliary_registry != canary.AUXILIARY_REGISTRY:
            raise ValueError("auxiliary registry is not stable")
        if self.semantic_registry != canary._semantic_registry():  # noqa: SLF001
            raise ValueError("semantic registry is not stable")
        if self.policy_sha256 != production_clearance_policy_sha256(
            self.policy,
        ):
            raise ValueError("clearance policy digest is invalid")
        payload = self.model_dump(mode="json", exclude={"preflight_id"})
        if self.preflight_id != _preflight_id_from_payload(payload):
            raise ValueError("preflight ID does not bind the request inputs")
        return self


def build_reciprocal_production_clearance_request(
    *,
    plan: ProductionCameraPlan,
    selected_camera_ids: tuple[str, ...],
    build_id: str,
    blender_executable_sha256: str,
    preflight_script_sha256: str,
    blend_sha256: str,
    build_report_sha256: str,
    environment_module_build_report_sha256: str,
    reciprocal_route_module_plan_sha256: str,
    object_registry: tuple[canary.ObjectRegistryEntry, ...],
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...],
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...],
    policy: ProductionClearancePolicy,
) -> ReciprocalProductionClearanceRequest:
    """Build and content-address one exact-218 preflight request."""

    payload: dict[str, object] = {
        "schema_version": RECIPROCAL_CLEARANCE_REQUEST_SCHEMA,
        "profile_id": PRODUCTION_PROFILE_ID,
        "production_plan": plan.model_dump(mode="json"),
        "production_plan_sha256": hashlib.sha256(
            canonical_production_plan_bytes(plan),
        ).hexdigest(),
        "camera_registry_sha256": production_camera_registry_digest(plan),
        "selected_camera_ids": list(selected_camera_ids),
        "build_id": build_id,
        "blender_executable_sha256": blender_executable_sha256,
        "preflight_script_sha256": preflight_script_sha256,
        "blend_sha256": blend_sha256,
        "build_report_sha256": build_report_sha256,
        "environment_module_build_report_sha256": (
            environment_module_build_report_sha256
        ),
        "reciprocal_route_module_plan_sha256": (
            reciprocal_route_module_plan_sha256
        ),
        "object_registry_sha256": reciprocal_object_registry_sha256(
            object_registry,
        ),
        "object_registry": [
            row.model_dump(mode="json") for row in object_registry
        ],
        "auxiliary_registry": [
            row.model_dump(mode="json") for row in auxiliary_registry
        ],
        "semantic_registry": [
            row.model_dump(mode="json") for row in semantic_registry
        ],
        "policy": policy.model_dump(mode="json"),
        "policy_sha256": production_clearance_policy_sha256(policy),
        "synthetic": True,
        "geometry_trust": "simplified-pbr-not-render-parity",
        "trust_effect": "none-quality-filter-only",
    }
    payload["preflight_id"] = _preflight_id_from_payload(payload)
    return ReciprocalProductionClearanceRequest.model_validate_json(
        _canonical(payload),
    )


def canonical_reciprocal_production_clearance_request_bytes(
    request: ReciprocalProductionClearanceRequest,
) -> bytes:
    """Serialize one exact-218 preflight request as canonical JSON."""

    return _canonical(request.model_dump(mode="json"))


def reciprocal_production_clearance_request_sha256(
    request: ReciprocalProductionClearanceRequest,
) -> str:
    """Hash the complete canonical exact-218 preflight request."""

    return hashlib.sha256(
        canonical_reciprocal_production_clearance_request_bytes(request),
    ).hexdigest()


class ReciprocalProductionClearanceReport(FrozenModel):
    """Measured ray evidence bound to one exact reciprocal-route request."""

    schema_version: Literal[
        "nantai.synthetic-village.reciprocal-production-clearance-report.v1"
    ] = RECIPROCAL_CLEARANCE_REPORT_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = (
        PRODUCTION_PROFILE_ID
    )
    preflight_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_module_build_report_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    reciprocal_route_module_plan_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: tuple[ProductionCameraClearanceEvidence, ...] = Field(
        min_length=1,
        max_length=180,
    )
    decisions: tuple[ProductionCameraClearanceDecision, ...] = Field(
        min_length=1,
        max_length=180,
    )
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )


def build_reciprocal_production_clearance_report(
    request: ReciprocalProductionClearanceRequest,
    *,
    evidence: tuple[ProductionCameraClearanceEvidence, ...],
) -> ReciprocalProductionClearanceReport:
    """Build a report from raw rays using only the bound request policy."""

    if tuple(row.camera_id for row in evidence) != request.selected_camera_ids:
        raise ReciprocalProductionError(
            "clearance evidence camera set disagrees with request",
        )
    return ReciprocalProductionClearanceReport(
        preflight_id=request.preflight_id,
        request_sha256=reciprocal_production_clearance_request_sha256(request),
        production_plan_sha256=request.production_plan_sha256,
        camera_registry_sha256=request.camera_registry_sha256,
        build_id=request.build_id,
        blender_executable_sha256=request.blender_executable_sha256,
        preflight_script_sha256=request.preflight_script_sha256,
        blend_sha256=request.blend_sha256,
        build_report_sha256=request.build_report_sha256,
        environment_module_build_report_sha256=(
            request.environment_module_build_report_sha256
        ),
        reciprocal_route_module_plan_sha256=(
            request.reciprocal_route_module_plan_sha256
        ),
        object_registry_sha256=request.object_registry_sha256,
        policy_sha256=request.policy_sha256,
        evidence=evidence,
        decisions=tuple(
            evaluate_production_camera_clearance(row, policy=request.policy)
            for row in evidence
        ),
    )


def canonical_reciprocal_production_clearance_report_bytes(
    report: ReciprocalProductionClearanceReport,
) -> bytes:
    """Serialize one reciprocal clearance report as canonical JSON."""

    return _canonical(report.model_dump(mode="json"))


def load_reciprocal_production_clearance_report(
    path: Path,
) -> ReciprocalProductionClearanceReport:
    """Load one bounded canonical reciprocal clearance report."""

    try:
        raw = canary._read_stable_metadata(  # noqa: SLF001
            Path(path),
            label="reciprocal production clearance report",
        )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,  # noqa: SLF001
        )
        report = ReciprocalProductionClearanceReport.model_validate_json(raw)
        if raw != canonical_reciprocal_production_clearance_report_bytes(report):
            raise ReciprocalProductionError(
                "reciprocal clearance report is not canonical JSON",
            )
        return report
    except ReciprocalProductionError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        canary.CanaryBuildError,
    ) as exc:
        raise ReciprocalProductionError(
            f"reciprocal clearance report validation failed: {exc}",
        ) from exc


def verify_reciprocal_production_clearance_report(
    report: ReciprocalProductionClearanceReport,
    *,
    request: ReciprocalProductionClearanceRequest,
) -> None:
    """Cross-check runtime evidence against every immutable request identity."""

    identity_pairs = (
        (report.preflight_id, request.preflight_id),
        (
            report.request_sha256,
            reciprocal_production_clearance_request_sha256(request),
        ),
        (report.production_plan_sha256, request.production_plan_sha256),
        (report.camera_registry_sha256, request.camera_registry_sha256),
        (report.build_id, request.build_id),
        (
            report.blender_executable_sha256,
            request.blender_executable_sha256,
        ),
        (report.preflight_script_sha256, request.preflight_script_sha256),
        (report.blend_sha256, request.blend_sha256),
        (report.build_report_sha256, request.build_report_sha256),
        (
            report.environment_module_build_report_sha256,
            request.environment_module_build_report_sha256,
        ),
        (
            report.reciprocal_route_module_plan_sha256,
            request.reciprocal_route_module_plan_sha256,
        ),
        (report.object_registry_sha256, request.object_registry_sha256),
        (report.policy_sha256, request.policy_sha256),
    )
    if any(left != right for left, right in identity_pairs):
        raise ReciprocalProductionError(
            "reciprocal clearance report identity disagrees with request",
        )
    if tuple(row.camera_id for row in report.evidence) != (
        request.selected_camera_ids
    ) or tuple(row.camera_id for row in report.decisions) != (
        request.selected_camera_ids
    ):
        raise ReciprocalProductionError(
            "reciprocal clearance report camera set disagrees",
        )
    expected_decisions = tuple(
        evaluate_production_camera_clearance(row, policy=request.policy)
        for row in report.evidence
    )
    if report.decisions != expected_decisions:
        raise ReciprocalProductionError(
            "reciprocal clearance decisions disagree with measured evidence",
        )


class ReciprocalProductionRenderFrameRequest(FrozenModel):
    """One exact-218 reciprocal-route production-camera render request."""

    schema_version: Literal[
        "nantai.synthetic-village.local-production-render-frame-request.v5"
    ] = RECIPROCAL_RENDER_REQUEST_SCHEMA
    profile_id: Literal["synthetic-village-coverage-180-v1"] = (
        PRODUCTION_PROFILE_ID
    )
    production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    elevated_topology_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_plan: ProductionCameraPlan
    render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_adapter: Literal["windows-reciprocal-route-v1"] = (
        RECIPROCAL_BUILD_ADAPTER
    )
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
    environment_module_build_report_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    reciprocal_route_module_plan_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    post_render_policy: ProductionFrameQualityPolicyV2
    post_render_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings: canary.RenderSettings
    camera: ProductionCameraPose
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
    def _validate_request(self) -> ReciprocalProductionRenderFrameRequest:
        if self.production_plan_sha256 != hashlib.sha256(
            canonical_production_plan_bytes(self.production_plan),
        ).hexdigest():
            raise ValueError("production plan digest is invalid")
        if self.camera_registry_sha256 != production_camera_registry_digest(
            self.production_plan,
        ):
            raise ValueError("production camera registry digest is invalid")
        if (
            self.elevated_topology_sha256
            != self.production_plan.elevated_topology_sha256
        ):
            raise ValueError(
                "elevated topology digest disagrees with production plan",
            )
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
        if not np.allclose(
            self.requested_c2w_blender,
            _opencv_c2w_to_blender(self.camera.c2w_opencv),
            atol=1e-9,
            rtol=0,
        ):
            raise ValueError("requested Blender matrix disagrees with camera pose")
        try:
            expected_registry_sha256 = reciprocal_object_registry_sha256(
                self.object_registry,
            )
        except ReciprocalProductionError as exc:
            raise ValueError(str(exc)) from exc
        if self.object_registry_sha256 != expected_registry_sha256:
            raise ValueError("object registry digest is invalid")
        if self.auxiliary_registry != canary.AUXILIARY_REGISTRY:
            raise ValueError("auxiliary registry is not stable")
        if self.semantic_registry != canary._semantic_registry():  # noqa: SLF001
            raise ValueError("semantic registry is not stable")
        expected_policy_sha256 = production_frame_quality_policy_v2_sha256(
            self.post_render_policy,
        )
        if self.post_render_policy_sha256 != expected_policy_sha256:
            raise ValueError("post-render policy digest is invalid")
        expected_render_id = production_render_id(
            self.production_plan,
            blender_executable_sha256=self.blender_executable_sha256,
            renderer_script_sha256=self.renderer_script_sha256,
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
        if self.render_id != expected_render_id:
            raise ValueError("render ID does not bind the production inputs")
        return self


class ReciprocalRenderStatistics(FrozenModel):
    """Layer summary whose registered instance range includes 0..218."""

    depth_min_m: float = Field(ge=0.0, le=1200.0, allow_inf_nan=False)
    depth_max_m: float = Field(gt=0.0, le=2000.0, allow_inf_nan=False)
    depth_background_pixels: int = Field(ge=0, le=1024 * 576)
    depth_max_range_error_m: float = Field(
        ge=0.0,
        le=0.01,
        allow_inf_nan=False,
    )
    normal_max_unit_error: float = Field(
        ge=0.0,
        le=0.001,
        allow_inf_nan=False,
    )
    instance_ids: tuple[int, ...] = Field(min_length=1)
    semantic_ids: tuple[int, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_ids(self) -> ReciprocalRenderStatistics:
        if self.instance_ids != tuple(sorted(set(self.instance_ids))) or any(
            value < 0 or value > 218 for value in self.instance_ids
        ):
            raise ValueError(
                "observed instance IDs must be unique stable IDs from 0 through 218",
            )
        if self.semantic_ids != tuple(sorted(set(self.semantic_ids))) or any(
            value < 0 or value > 14 for value in self.semantic_ids
        ):
            raise ValueError(
                "observed semantic IDs must be unique stable IDs from 0 through 14",
            )
        if self.depth_max_m < self.depth_min_m:
            raise ValueError("depth statistics are inverted")
        return self


class ReciprocalProductionRenderFrameReport(
    LocalProductionRenderFrameReport,
):
    """Six measured layers emitted by the additive exact-218 renderer."""

    schema_version: Literal[
        "nantai.synthetic-village.local-production-render-frame-report.v4"
    ] = RECIPROCAL_RENDER_REPORT_SCHEMA
    statistics: ReciprocalRenderStatistics


class ReciprocalProductionCameraMetadata(LocalProductionCameraMetadata):
    """Measured camera metadata emitted by the additive v5 renderer."""

    schema_version: Literal[
        "nantai.synthetic-village.local-production-camera-metadata.v4"
    ] = RECIPROCAL_CAMERA_METADATA_SCHEMA


def canonical_reciprocal_production_camera_metadata_bytes(
    metadata: ReciprocalProductionCameraMetadata,
) -> bytes:
    """Serialize measured v4 camera metadata as canonical JSON."""

    return _canonical(metadata.model_dump(mode="json"))


def load_reciprocal_production_camera_metadata(
    path: Path,
) -> ReciprocalProductionCameraMetadata:
    """Load one canonical measured-camera sidecar."""

    try:
        raw = canary._read_stable_metadata(  # noqa: SLF001
            Path(path),
            label="reciprocal production camera metadata",
        )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,  # noqa: SLF001
        )
        metadata = ReciprocalProductionCameraMetadata.model_validate_json(raw)
        if raw != canonical_reciprocal_production_camera_metadata_bytes(
            metadata,
        ):
            raise ReciprocalProductionError(
                "reciprocal camera metadata is not canonical JSON",
            )
        return metadata
    except ReciprocalProductionError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        canary.CanaryBuildError,
    ) as exc:
        raise ReciprocalProductionError(
            f"reciprocal camera metadata validation failed: {exc}",
        ) from exc


def verify_reciprocal_production_camera_metadata(
    metadata: ReciprocalProductionCameraMetadata,
    *,
    request: ReciprocalProductionRenderFrameRequest,
) -> None:
    """Cross-check measured camera identity and pose against the request."""

    expected_settings_sha256 = hashlib.sha256(
        canary._canonical_json_bytes(  # noqa: SLF001
            request.settings.model_dump(mode="json"),
        ),
    ).hexdigest()
    camera = request.camera
    immutable = (
        metadata.build_id,
        metadata.render_id,
        metadata.blender_executable_sha256,
        metadata.camera_id,
        metadata.settings_sha256,
        metadata.intrinsics,
        metadata.requested_c2w_opencv,
        metadata.requested_c2w_blender,
        metadata.object_registry_sha256,
        metadata.semantic_registry,
        metadata.profile_id,
        metadata.production_plan_sha256,
        metadata.camera_registry_sha256,
        metadata.elevated_topology_sha256,
        metadata.group_id,
        metadata.topology_ref,
        metadata.arc_length_m,
        metadata.audit_only,
        metadata.disclosure,
        metadata.preflight_id,
        metadata.quality_policy_sha256,
        metadata.post_render_policy_sha256,
    )
    expected = (
        request.build_id,
        request.render_id,
        request.blender_executable_sha256,
        camera.camera_id,
        expected_settings_sha256,
        camera.intrinsics,
        camera.c2w_opencv,
        request.requested_c2w_blender,
        request.object_registry_sha256,
        request.semantic_registry,
        request.profile_id,
        request.production_plan_sha256,
        request.camera_registry_sha256,
        request.elevated_topology_sha256,
        camera.group_id,
        camera.topology_ref,
        camera.arc_length_m,
        camera.audit_only,
        camera.disclosure,
        request.preflight_id,
        request.quality_policy_sha256,
        request.post_render_policy_sha256,
    )
    if immutable != expected or not np.allclose(
        metadata.measured_c2w_opencv,
        camera.c2w_opencv,
        atol=4e-5,
        rtol=0,
    ) or not np.allclose(
        metadata.measured_c2w_blender,
        request.requested_c2w_blender,
        atol=4e-5,
        rtol=0,
    ):
        raise ReciprocalProductionError(
            "reciprocal camera metadata disagrees with render request",
        )


RECIPROCAL_CAMERA_JOURNAL_SCHEMA = (
    "nantai.synthetic-village.reciprocal-production-camera-journal.v1"
)


class ReciprocalProductionCameraJournal(FrozenModel):
    """Durable L0 journal for the first independently testable camera slice."""

    schema_version: Literal[
        "nantai.synthetic-village.reciprocal-production-camera-journal.v1"
    ] = RECIPROCAL_CAMERA_JOURNAL_SCHEMA
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_module_build_report_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    reciprocal_route_module_plan_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    preflight_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    artifacts: tuple[ProductionArtifactRecord, ...] = Field(
        min_length=6,
        max_length=6,
    )
    statistics: ReciprocalRenderStatistics
    layer_statistics: ProductionFrameLayerStatistics
    clearance_decision: ProductionCameraClearanceDecision
    local_quality: LocalProductionFrameQuality
    preflight_wall_clock_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    render_wall_clock_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _validate_journal(self) -> ReciprocalProductionCameraJournal:
        if tuple((row.kind, row.path) for row in self.artifacts) != (
            expected_production_artifacts(self.camera_id)
        ):
            raise ValueError("camera journal artifact contract is invalid")
        if (
            self.layer_statistics.camera_id != self.camera_id
            or self.clearance_decision.camera_id != self.camera_id
            or not self.clearance_decision.passes
            or not self.local_quality.passes
        ):
            raise ValueError("camera journal contains rejected or foreign evidence")
        payload = self.model_dump(mode="json", exclude={"journal_sha256"})
        if self.journal_sha256 != hashlib.sha256(_canonical(payload)).hexdigest():
            raise ValueError("camera journal SHA-256 is invalid")
        return self


def canonical_reciprocal_production_camera_journal_bytes(
    journal: ReciprocalProductionCameraJournal,
) -> bytes:
    return _canonical(journal.model_dump(mode="json"))


def _build_reciprocal_camera_journal(
    *,
    request: ReciprocalProductionRenderFrameRequest,
    preflight_request_sha256: str,
    preflight_report_sha256: str,
    render_request_sha256: str,
    render_report_sha256: str,
    report: ReciprocalProductionRenderFrameReport,
    decision: ProductionCameraClearanceDecision,
    local_quality: LocalProductionFrameQuality,
    preflight_wall_clock_seconds: float,
    render_wall_clock_seconds: float,
) -> ReciprocalProductionCameraJournal:
    payload = {
        "schema_version": RECIPROCAL_CAMERA_JOURNAL_SCHEMA,
        "render_id": request.render_id,
        "build_id": request.build_id,
        "build_report_sha256": request.build_report_sha256,
        "environment_module_build_report_sha256": (
            request.environment_module_build_report_sha256
        ),
        "reciprocal_route_module_plan_sha256": (
            request.reciprocal_route_module_plan_sha256
        ),
        "preflight_id": request.preflight_id,
        "preflight_request_sha256": preflight_request_sha256,
        "preflight_report_sha256": preflight_report_sha256,
        "render_request_sha256": render_request_sha256,
        "render_report_sha256": render_report_sha256,
        "camera_id": request.camera.camera_id,
        "artifacts": report.artifacts,
        "statistics": report.statistics,
        "layer_statistics": report.layer_statistics,
        "clearance_decision": decision,
        "local_quality": local_quality,
        "preflight_wall_clock_seconds": preflight_wall_clock_seconds,
        "render_wall_clock_seconds": render_wall_clock_seconds,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_trust": "simplified-pbr-not-render-parity",
        "trust_effect": "none-quality-filter-only",
    }
    unsigned = ReciprocalProductionCameraJournal.model_construct(
        journal_sha256="0" * 64,
        **payload,
    )
    digest = hashlib.sha256(
        _canonical(
            unsigned.model_dump(mode="json", exclude={"journal_sha256"}),
        ),
    ).hexdigest()
    return ReciprocalProductionCameraJournal(
        journal_sha256=digest,
        **payload,
    )


def canonical_reciprocal_production_render_report_bytes(
    report: ReciprocalProductionRenderFrameReport,
    *,
    exclude_sha256: bool = False,
) -> bytes:
    """Serialize one v4 frame report with optional self-digest exclusion."""

    exclude = {"content_sha256"} if exclude_sha256 else None
    return _canonical(report.model_dump(mode="json", exclude=exclude))


def load_reciprocal_production_render_report(
    path: Path,
) -> ReciprocalProductionRenderFrameReport:
    """Load canonical report bytes and recompute the report self-digest."""

    try:
        raw = canary._read_stable_metadata(  # noqa: SLF001
            Path(path),
            label="reciprocal production frame report",
        )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,  # noqa: SLF001
        )
        report = ReciprocalProductionRenderFrameReport.model_validate_json(raw)
        if raw != canonical_reciprocal_production_render_report_bytes(report):
            raise ReciprocalProductionError(
                "reciprocal production frame report is not canonical JSON",
            )
        expected = hashlib.sha256(
            canonical_reciprocal_production_render_report_bytes(
                report,
                exclude_sha256=True,
            ),
        ).hexdigest()
        if report.content_sha256 != expected:
            raise ReciprocalProductionError(
                "reciprocal production frame report SHA-256 is invalid",
            )
        return report
    except ReciprocalProductionError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        canary.CanaryBuildError,
    ) as exc:
        raise ReciprocalProductionError(
            f"reciprocal production frame report validation failed: {exc}",
        ) from exc


def verify_reciprocal_production_render_frame(
    report: ReciprocalProductionRenderFrameReport,
    *,
    request: ReciprocalProductionRenderFrameRequest,
    frame_root: Path,
) -> None:
    """Verify runtime identities and all six measured artifact bytes."""

    expected_settings_sha256 = hashlib.sha256(
        canary._canonical_json_bytes(  # noqa: SLF001
            request.settings.model_dump(mode="json"),
        ),
    ).hexdigest()
    identity_pairs = (
        (report.build_id, request.build_id),
        (report.render_id, request.render_id),
        (
            report.blender_executable_sha256,
            request.blender_executable_sha256,
        ),
        (report.camera_id, request.camera.camera_id),
        (report.settings_sha256, expected_settings_sha256),
        (report.production_plan_sha256, request.production_plan_sha256),
        (report.camera_registry_sha256, request.camera_registry_sha256),
        (report.elevated_topology_sha256, request.elevated_topology_sha256),
        (report.group_id, request.camera.group_id),
        (report.topology_ref, request.camera.topology_ref),
        (report.preflight_id, request.preflight_id),
        (report.quality_policy_sha256, request.quality_policy_sha256),
        (
            report.post_render_policy_sha256,
            request.post_render_policy_sha256,
        ),
    )
    if any(left != right for left, right in identity_pairs):
        raise ReciprocalProductionError(
            "reciprocal production frame report identity disagrees",
        )
    frame_root = Path(frame_root).resolve(strict=True)
    for artifact in report.artifacts:
        artifact_path = frame_root / Path(artifact.path)
        try:
            resolved = artifact_path.resolve(strict=True)
            resolved.relative_to(frame_root)
        except (OSError, ValueError) as exc:
            raise ReciprocalProductionError(
                f"reciprocal render artifact path is invalid: {artifact.path}",
            ) from exc
        if canary._is_linklike(resolved):  # noqa: SLF001
            raise ReciprocalProductionError(
                f"reciprocal render artifact is redirected: {artifact.path}",
            )
        if (
            resolved.stat().st_size != artifact.size_bytes
            or _sha256_file(resolved) != artifact.sha256
        ):
            raise ReciprocalProductionError(
                f"reciprocal render artifact digest disagrees: {artifact.path}",
            )


def _remove_private_staging(path: Path, *, parent: Path) -> None:
    """Remove only a proven direct child staging directory."""

    path = Path(path).absolute()
    parent = Path(parent).resolve(strict=True)
    if path.parent.resolve(strict=True) != parent or not path.name.startswith(
        ".staging-",
    ):
        raise ReciprocalProductionError(
            "refusing to remove unverified reciprocal staging directory",
        )
    if canary._is_linklike(path):  # noqa: SLF001
        raise ReciprocalProductionError(
            "reciprocal staging directory is redirected",
        )
    shutil.rmtree(path)


def run_reciprocal_production_camera(
    *,
    verified_build: VerifiedReciprocalProductionBuild,
    plan: ProductionCameraPlan,
    camera_id: str,
    blender_executable: Path,
    output_root: Path,
    clearance_policy: ProductionClearancePolicy,
    quality_policy: LocalProductionQualityPolicy,
    post_render_policy: ProductionFrameQualityPolicyV2,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = (
        subprocess.run
    ),
    timeout_seconds: int = 1800,
) -> ReciprocalProductionCameraResult:
    """Preflight, render, verify, quality-check and atomically publish one frame."""

    if timeout_seconds <= 0:
        raise ReciprocalProductionError("runner timeout must be positive")
    blender_executable = Path(blender_executable).resolve(strict=True)
    output_root = Path(output_root).absolute()
    output_root.mkdir(parents=True, exist_ok=True)
    output_root = output_root.resolve(strict=True)
    if canary._is_linklike(output_root):  # noqa: SLF001
        raise ReciprocalProductionError("reciprocal render root is redirected")
    repo_root = Path(__file__).resolve().parents[2]
    preflight_script = (
        repo_root / "scripts/blender/preflight_reciprocal_route_cameras.py"
    ).resolve(strict=True)
    if (
        _sha256_file(verified_build.report_path)
        != verified_build.report_sha256
        or _sha256_file(verified_build.blend_path)
        != verified_build.blend_sha256
    ):
        raise ReciprocalProductionError(
            "verified reciprocal build changed before preflight",
        )
    request = build_reciprocal_production_clearance_request(
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
        request_path = staging / "preflight-request.json"
        report_path = staging / "preflight-report.json"
        canary._write_new_file(  # noqa: SLF001
            request_path,
            canonical_reciprocal_production_clearance_request_bytes(request),
        )
        snapshots = (
            canary._snapshot_regular_file(blender_executable),  # noqa: SLF001
            canary._snapshot_regular_file(verified_build.blend_path),  # noqa: SLF001
            canary._snapshot_regular_file(verified_build.report_path),  # noqa: SLF001
            canary._snapshot_regular_file(preflight_script),  # noqa: SLF001
            canary._snapshot_regular_file(request_path),  # noqa: SLF001
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
                    str(request_path),
                    "--report",
                    str(report_path),
                ],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReciprocalProductionError(
                f"reciprocal preflight exceeded {timeout_seconds} seconds",
            ) from exc
        preflight_wall_clock_seconds = time.monotonic() - started
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ReciprocalProductionError(
                "reciprocal Blender preflight failed"
                + (f": {detail[-2000:]}" if detail else ""),
            )
        canary._verify_snapshots_unchanged(snapshots)  # noqa: SLF001
        report = load_reciprocal_production_clearance_report(report_path)
        verify_reciprocal_production_clearance_report(
            report,
            request=request,
        )
        decision = report.decisions[0]
        if not decision.passes:
            raise ReciprocalProductionError(
                f"preflight rejected camera: {camera_id}",
            )
        preflight_request_sha256 = hashlib.sha256(
            canonical_reciprocal_production_clearance_request_bytes(request),
        ).hexdigest()
        preflight_report_sha256 = _sha256_file(report_path)
        renderer_script = (
            repo_root / "scripts/blender/render_reciprocal_route_production.py"
        ).resolve(strict=True)
        render_request = build_reciprocal_production_frame_request(
            plan=plan,
            camera_id=camera_id,
            build_id=verified_build.build_id,
            blender_executable_sha256=_sha256_file(blender_executable),
            renderer_script_sha256=_sha256_file(renderer_script),
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
            preflight_id=request.preflight_id,
            quality_policy_sha256=local_production_quality_policy_sha256(
                quality_policy,
            ),
            post_render_policy=post_render_policy,
        )
        render_request_bytes = (
            canonical_reciprocal_production_render_request_bytes(
                render_request,
            )
        )
        render_request_sha256 = hashlib.sha256(
            render_request_bytes,
        ).hexdigest()
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
                f"reciprocal render exceeded {timeout_seconds} seconds",
            ) from exc
        render_wall_clock_seconds = time.monotonic() - render_started
        if rendered.returncode != 0:
            detail = (rendered.stderr or rendered.stdout).strip()
            raise ReciprocalProductionError(
                "reciprocal Blender render failed"
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
                f"post-render quality rejected camera: {camera_id}",
            )
        evidence_root = frame_output / "evidence"
        evidence_root.mkdir()
        evidence_payloads = {
            "preflight-request.json": (
                canonical_reciprocal_production_clearance_request_bytes(
                    request,
                )
            ),
            "preflight-report.json": (
                canonical_reciprocal_production_clearance_report_bytes(report)
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
                "reciprocal final render parent is redirected",
            )
        final = final_parent / camera_id
        if final.exists() or canary._is_linklike(final):  # noqa: SLF001
            raise ReciprocalProductionError(
                "reciprocal final camera directory already exists",
            )
        frame_output.rename(final)
        canary._flush_directory(final_parent)  # noqa: SLF001
        quality_request_bytes = evidence_payloads["quality-request.json"]
        quality_report_bytes = evidence_payloads["quality-report.json"]
        return ReciprocalProductionCameraResult(
            render_id=render_request.render_id,
            camera_id=camera_id,
            frame_root=final,
            preflight_request_sha256=preflight_request_sha256,
            preflight_report_sha256=preflight_report_sha256,
            render_request_sha256=render_request_sha256,
            render_report_sha256=render_report_sha256,
            journal_sha256=journal.journal_sha256,
            quality_request_sha256=hashlib.sha256(
                quality_request_bytes,
            ).hexdigest(),
            quality_report_sha256=hashlib.sha256(
                quality_report_bytes,
            ).hexdigest(),
        )
    finally:
        if staging.exists():
            _remove_private_staging(staging, parent=output_root)


def build_reciprocal_production_frame_request(
    *,
    plan: ProductionCameraPlan,
    camera_id: str,
    build_id: str,
    blender_executable_sha256: str,
    renderer_script_sha256: str,
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
) -> ReciprocalProductionRenderFrameRequest:
    """Build one content-addressed exact-218 render request."""

    camera = next(
        (row for row in plan.cameras if row.camera_id == camera_id),
        None,
    )
    if camera is None:
        raise ReciprocalProductionError(
            f"camera ID is not in the production plan: {camera_id}",
        )
    object_registry_sha256 = reciprocal_object_registry_sha256(object_registry)
    camera_registry_sha256 = production_camera_registry_digest(plan)
    post_render_policy_sha256 = production_frame_quality_policy_v2_sha256(
        post_render_policy,
    )
    render_id = production_render_id(
        plan,
        blender_executable_sha256=blender_executable_sha256,
        renderer_script_sha256=renderer_script_sha256,
        blend_sha256=blend_sha256,
        build_report_sha256=build_report_sha256,
        camera_registry_sha256=camera_registry_sha256,
        preflight_id=preflight_id,
        quality_policy_sha256=quality_policy_sha256,
        post_render_policy_sha256=post_render_policy_sha256,
        build_adapter=RECIPROCAL_BUILD_ADAPTER,
        environment_module_build_report_sha256=(
            environment_module_build_report_sha256
        ),
    )
    return ReciprocalProductionRenderFrameRequest(
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
        environment_module_build_report_sha256=(
            environment_module_build_report_sha256
        ),
        reciprocal_route_module_plan_sha256=(
            reciprocal_route_module_plan_sha256
        ),
        object_registry_sha256=object_registry_sha256,
        preflight_id=preflight_id,
        quality_policy_sha256=quality_policy_sha256,
        post_render_policy=post_render_policy,
        post_render_policy_sha256=post_render_policy_sha256,
        settings=canary.RenderSettings(),
        camera=camera,
        requested_c2w_blender=_opencv_c2w_to_blender(camera.c2w_opencv),
        object_registry=object_registry,
        auxiliary_registry=auxiliary_registry,
        semantic_registry=semantic_registry,
    )


def canonical_reciprocal_production_render_request_bytes(
    request: ReciprocalProductionRenderFrameRequest,
) -> bytes:
    """Serialize one v5 request as stable canonical JSON bytes."""

    return _canonical(request.model_dump(mode="json"))


def verify_reciprocal_production_build(
    *,
    report_path: Path,
    runtime_request: ReciprocalRouteRuntimeRequest,
) -> VerifiedReciprocalProductionBuild:
    """Verify the report/request/artifact chain and return measured identities."""

    report_path = Path(report_path).resolve(strict=True)
    report = load_reciprocal_route_build_report(report_path)
    blend_path = report_path.parent / report.artifact.name
    verify_reciprocal_route_build_report(
        report,
        request=runtime_request,
        output_path=blend_path,
    )
    require_exact_reciprocal_object_registry(report.object_registry)
    return VerifiedReciprocalProductionBuild(
        build_id=report.build_id,
        report_path=report_path,
        report_sha256=_sha256_file(report_path),
        blend_path=blend_path.resolve(strict=True),
        blend_sha256=report.artifact.sha256,
        environment_module_build_report_sha256=(
            report.base_build_report_sha256
        ),
        reciprocal_route_module_plan_sha256=(
            report.reciprocal_route_module_plan_sha256
        ),
        object_registry=report.object_registry,
    )
