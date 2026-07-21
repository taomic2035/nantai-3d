"""Additive exact-218 production caller contracts for reciprocal-route builds.

This module is intentionally separate from the frozen 130-root production caller.
It never promotes the synthetic scene beyond its existing L0, preview-only trust.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import canary
from .production_journal import production_render_id
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
    ProductionFrameQualityPolicyV2,
    production_frame_quality_policy_v2_sha256,
)
from .reciprocal_route_module_runtime import (
    ReciprocalRouteRuntimeRequest,
    load_reciprocal_route_build_report,
    verify_reciprocal_route_build_report,
)

RECIPROCAL_RENDER_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-request.v5"
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
