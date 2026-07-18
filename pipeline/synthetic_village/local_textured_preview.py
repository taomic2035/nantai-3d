"""Fail-closed local-only contract for non-authoritative textured previews."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from pipeline.studio_jobs import JobContractError, ProjectFileLock
from pipeline.synthetic_village import canary
from pipeline.synthetic_village.building_geometry import (
    BUILDING_GEOMETRY_V1,
    BUILDING_GEOMETRY_V2,
    BuildingGeometryEvidence,
    BuildingGeometryProfileId,
)
from pipeline.synthetic_village.camera_plan import (
    CameraPlan,
    build_camera_plan,
    canonical_camera_plan_bytes,
)
from pipeline.synthetic_village.defaults import canonical_json_bytes, load_default_recipe
from pipeline.synthetic_village.glb_material_audit import (
    ExpectedBuildingGeometry,
    ExpectedGlbMaterial,
    GlbBuildingGeometryEvidence,
    GlbMaterialAudit,
    audit_textured_glb,
)
from pipeline.synthetic_village.material_bundle import (
    MATERIAL_BUNDLE_MANIFEST,
    MaterialAlgorithmId,
    MaterialBundleError,
    _move_directory_noreplace,
    load_material_bundle,
)
from pipeline.synthetic_village.scene_plan import (
    ScenePlan,
    build_scene_plan,
    canonical_scene_plan_bytes,
)
from pipeline.synthetic_village.visual_sources import (
    VISUAL_MANIFEST_NAME,
    canonical_manifest_bytes,
    load_visual_source_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
LOCAL_REQUEST_SCHEMA = "nantai.synthetic-village.local-textured-preview-request.v1"
LOCAL_RELEASE_CHANNEL = "local-preview-only"
LOCAL_LIMITATIONS = (
    "not-real-place",
    "not-measured-geometry",
    "not-completed-trained-reconstruction",
    "no-real-photo-textures",
    "local-preview-only",
)
DEFAULT_LOCAL_BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
DEFAULT_LOCAL_WORK_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/local-preview-work"
)
DEFAULT_LOCAL_PUBLICATION_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/local-previews"
)
DEFAULT_LOCAL_TRAINING_BUILD_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/local-training-builds"
)
DEFAULT_LOCAL_TRAINING_RENDER_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/local-training-renders"
)
LOCAL_TRAINING_BUILD_ENTRIES = (
    "build-report.json",
    "glb-material-audit.json",
    "manifest.json",
    "preview-bridge.png",
    "preview-central.png",
    "preview-outer.png",
    "preview-upper.png",
    "village-canary.blend",
    "village-canary.glb",
)
MAX_RUNTIME_OUTPUT_BYTES = 64 * 1024

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class LocalTexturedPreviewError(RuntimeError):
    """Local preview evidence cannot be derived or verified safely."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LocalBlenderIdentity(FrozenModel):
    tool_id: Literal["blender"] = "blender"
    executable_sha256: Sha256
    version: Literal["4.5.11"]
    platform: Literal["macos-arm64"]
    runtime_build_hash: Literal["4db51e9d1e1e"]
    runtime_output_sha256: Sha256
    engine: Literal["BLENDER_EEVEE_NEXT"] = "BLENDER_EEVEE_NEXT"
    view_transform: Literal["AgX"] = "AgX"


class LocalTexturedPreviewRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.local-textured-preview-request.v1"
    ] = LOCAL_REQUEST_SCHEMA
    preview_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    authoritative: Literal[False] = False
    release_channel: Literal["local-preview-only"] = LOCAL_RELEASE_CHANNEL
    tool_identity: LocalBlenderIdentity
    scene_plan: ScenePlan
    camera_plan: CameraPlan
    source_hashes: canary.SourceHashes
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=126,
        max_length=126,
    )
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...] = Field(
        min_length=3,
        max_length=3,
    )
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...] = Field(
        min_length=15,
        max_length=15,
    )
    material_registry: tuple[canary.MaterialRegistryEntry, ...] = Field(
        min_length=11,
        max_length=11,
    )
    visual_slot_registry: tuple[canary.TexturedVisualSlotRegistryEntry, ...] = Field(
        min_length=68,
        max_length=68,
    )
    requested_artifacts: tuple[canary.ArtifactRequest, ...] = Field(
        min_length=6,
        max_length=6,
    )
    material_bundle_manifest_sha256: Sha256
    material_bundle_id: Sha256
    material_algorithm_id: MaterialAlgorithmId
    building_geometry_profile_id: BuildingGeometryProfileId = BUILDING_GEOMETRY_V1
    material_input_registry: tuple[canary.MaterialInputRecord, ...] = Field(
        min_length=24,
        max_length=24,
    )

    @model_validator(mode="after")
    def _validate_local_request(self) -> LocalTexturedPreviewRequest:
        canary._validate_common_request_contract(self)
        canary._validate_visual_build_evidence(
            self,
            implementation_by_category={
                "key-view": "composition-reference-v1",
                "material": "derived-pbr-material-v1",
                "detail": "geometry-detail-v1",
                "environment": "environment-element-v1",
                "prop": "prop-element-v1",
            },
        )
        input_ids = tuple(row.slot_id for row in self.material_input_registry)
        if input_ids != tuple(sorted(canary.VISUAL_MATERIAL_SLOT_IDS)):
            raise ValueError("local material inputs are not the exact sorted 24-slot set")
        inputs = {row.slot_id: row for row in self.material_input_registry}
        material_slots = [
            row for row in self.visual_slot_registry if row.category == "material"
        ]
        if len(material_slots) != 24 or any(
            row.source_sha256 != inputs[row.slot_id].source_sha256
            for row in material_slots
        ):
            raise ValueError("local material slots do not match verified bundle sources")
        expected = hashlib.sha256(
            canonical_local_textured_preview_request_bytes(
                self,
                exclude_preview_id=True,
            ),
        ).hexdigest()
        if self.preview_id != expected:
            raise ValueError("preview_id does not match canonical local request inputs")
        return self


class LocalTexturedBuildReport(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.local-textured-preview-build-report.v1"
    ] = "nantai.synthetic-village.local-textured-preview-build-report.v1"
    preview_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    authoritative: Literal[False] = False
    release_channel: Literal["local-preview-only"] = LOCAL_RELEASE_CHANNEL
    fidelity: Literal[
        "simplified-pbr-not-render-parity"
    ] = "simplified-pbr-not-render-parity"
    geometry_usability: Literal["preview-only"] = "preview-only"
    tool_identity: LocalBlenderIdentity
    source_hashes: canary.SourceHashes
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=126,
        max_length=126,
    )
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...] = Field(
        min_length=3,
        max_length=3,
    )
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...] = Field(
        min_length=15,
        max_length=15,
    )
    material_registry: tuple[canary.MaterialRegistryEntry, ...] = Field(
        min_length=11,
        max_length=11,
    )
    visual_slot_registry: tuple[canary.TexturedVisualSlotRegistryEntry, ...] = Field(
        min_length=68,
        max_length=68,
    )
    material_bundle_manifest_sha256: Sha256
    material_bundle_id: Sha256
    material_algorithm_id: MaterialAlgorithmId = "mirror-sobel-orm-v1"
    building_geometry_profile_id: BuildingGeometryProfileId = BUILDING_GEOMETRY_V1
    building_geometry: BuildingGeometryEvidence | None = None
    material_input_registry: tuple[canary.MaterialInputRecord, ...] = Field(
        min_length=24,
        max_length=24,
    )
    camera_registry: tuple[canary.CameraRegistryEntry, ...] = Field(
        min_length=24,
        max_length=24,
    )
    preview_registry: tuple[canary.PreviewCameraRecord, ...] = Field(
        min_length=4,
        max_length=4,
    )
    counts: canary.TexturedBuildCounts
    validation: canary.TexturedBuildValidation
    determinism: canary.BuildDeterminism
    artifacts: tuple[canary.ArtifactRecord, ...] = Field(
        min_length=6,
        max_length=6,
    )

    @model_validator(mode="after")
    def _validate_complete_local_report(self) -> LocalTexturedBuildReport:
        if (
            self.semantic_registry != canary._semantic_registry()
            or self.auxiliary_registry != canary.AUXILIARY_REGISTRY
        ):
            raise ValueError("local build semantic registries are not stable")
        expected_materials = tuple(
            canary.MaterialRegistryEntry(material_family=family, material_id=index)
            for index, family in enumerate(canary.MATERIAL_FAMILIES, start=1)
        )
        if self.material_registry != expected_materials:
            raise ValueError("local build material registry is not stable")
        if (
            tuple(row.instance_id for row in self.object_registry)
            != tuple(range(1, 127))
            or len({row.object_id for row in self.object_registry}) != 126
        ):
            raise ValueError("local build object registry is incomplete")
        slot_ids = tuple(row.slot_id for row in self.visual_slot_registry)
        if slot_ids != tuple(sorted(slot_ids)) or len(set(slot_ids)) != 68:
            raise ValueError("local build visual registry is incomplete")
        material_slots = [
            row for row in self.visual_slot_registry if row.category == "material"
        ]
        input_ids = tuple(row.slot_id for row in self.material_input_registry)
        if (
            len(material_slots) != 24
            or input_ids != tuple(sorted(canary.VISUAL_MATERIAL_SLOT_IDS))
            or any(
                row.source_sha256
                != self.material_input_registry[input_ids.index(row.slot_id)].source_sha256
                for row in material_slots
            )
        ):
            raise ValueError("local build material evidence is incomplete")
        if len({row.camera_id for row in self.camera_registry}) != 24:
            raise ValueError("local build camera registry is not unique")
        if tuple(row.artifact_name for row in self.preview_registry) != (
            "preview-bridge.png",
            "preview-central.png",
            "preview-outer.png",
            "preview-upper.png",
        ):
            raise ValueError("local build preview registry is not stable")
        if tuple((row.name, row.kind) for row in self.artifacts) != tuple(
            (row.name, row.kind) for row in canary.ARTIFACT_REQUESTS
        ):
            raise ValueError("local build artifact registry is not exact")
        if self.building_geometry_profile_id == BUILDING_GEOMETRY_V2:
            if self.building_geometry is None:
                raise ValueError("local v2 building geometry requires measured evidence")
        elif self.building_geometry is not None:
            raise ValueError("local v1 building geometry cannot claim v2 evidence")
        return self


class LocalTexturedPreviewManifest(FrozenModel):
    schema_version: Literal[2] = 2
    preview_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    authoritative: Literal[False] = False
    release_channel: Literal["local-preview-only"] = LOCAL_RELEASE_CHANNEL
    geometry_usability: Literal["preview-only"] = "preview-only"
    material_fidelity: Literal["synthetic-derived-pbr"] = "synthetic-derived-pbr"
    synthetic_pbr_textures: Literal[True] = True
    real_photo_textures: Literal[False] = False
    dynamic_mesh_relighting: Literal[True] = True
    splat_relighting: Literal[False] = False
    model_url: str = Field(
        pattern=r"^/api/local-textured-preview/[0-9a-f]{64}/village-canary\.glb$",
    )
    glb_sha256: Sha256
    glb_bytes: int = Field(gt=0, le=2 * 1024 * 1024 * 1024)
    build_report_sha256: Sha256
    audit_sha256: Sha256
    material_bundle_id: Sha256
    limitations: tuple[str, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def _validate_manifest_identity(self) -> LocalTexturedPreviewManifest:
        expected_url = (
            f"/api/local-textured-preview/{self.preview_id}/village-canary.glb"
        )
        if self.model_url != expected_url:
            raise ValueError("local preview model URL does not match preview identity")
        if self.limitations != LOCAL_LIMITATIONS:
            raise ValueError("local preview limitations are incomplete or reordered")
        return self


class HistoricalLocalGlbMaterialAudit(FrozenModel):
    """Pre-triangle-count audit bytes retained by existing local publications."""

    glb_sha256: Sha256
    byte_count: int = Field(ge=1)
    mesh_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_count: int = Field(ge=1)
    texture_count: int = Field(ge=3)
    embedded_image_count: int = Field(ge=3)
    textured_primitive_count: int = Field(ge=1)
    uv_primitive_count: int = Field(ge=1)
    tangent_primitive_count: int = Field(ge=1)
    external_uri_count: Literal[0] = 0
    slot_ids: tuple[str, ...] = Field(min_length=1)
    building_geometry: GlbBuildingGeometryEvidence | None = None


def _canonical_bytes(value: object) -> bytes:
    return canary._canonical_json_bytes(value)


def canonical_local_textured_preview_request_bytes(
    request: LocalTexturedPreviewRequest,
    *,
    exclude_preview_id: bool = False,
) -> bytes:
    exclude = {"preview_id"} if exclude_preview_id else None
    payload = request.model_dump(mode="json", exclude=exclude)
    if "building_geometry_profile_id" not in request.model_fields_set:
        payload.pop("building_geometry_profile_id")
    return _canonical_bytes(payload)


def canonical_local_textured_preview_manifest_bytes(
    manifest: LocalTexturedPreviewManifest,
) -> bytes:
    return _canonical_bytes(manifest.model_dump(mode="json"))


def canonical_local_textured_build_report_bytes(
    report: LocalTexturedBuildReport,
) -> bytes:
    payload = report.model_dump(mode="json", by_alias=True)
    if "material_algorithm_id" not in report.model_fields_set:
        payload.pop("material_algorithm_id")
    if "building_geometry_profile_id" not in report.model_fields_set:
        payload.pop("building_geometry_profile_id")
    if "building_geometry" not in report.model_fields_set:
        payload.pop("building_geometry")
    return _canonical_bytes(payload)


def canonical_local_glb_audit_bytes(audit: GlbMaterialAudit) -> bytes:
    payload = audit.model_dump(mode="json")
    if "building_geometry" not in audit.model_fields_set:
        payload.pop("building_geometry")
    return _canonical_bytes(payload)


def _canonical_historical_local_glb_audit_bytes(
    audit: HistoricalLocalGlbMaterialAudit,
) -> bytes:
    payload = audit.model_dump(mode="json")
    if "building_geometry" not in audit.model_fields_set:
        payload.pop("building_geometry")
    return _canonical_bytes(payload)


def _read_stable_file(path: Path, *, maximum_bytes: int, label: str) -> bytes:
    path = Path(path).absolute()
    if canary._is_linklike(path) or not path.is_file():
        raise LocalTexturedPreviewError(f"{label} is missing or redirected")
    try:
        before = canary._stat_signature(path.stat())
        if before[2] <= 0 or before[2] > maximum_bytes:
            raise LocalTexturedPreviewError(f"{label} size is invalid")
        with path.open("rb") as stream:
            opened = canary._stat_signature(os.fstat(stream.fileno()))
            if opened != before:
                raise LocalTexturedPreviewError(f"{label} changed before bounded read")
            payload = stream.read(maximum_bytes + 1)
            after_open = canary._stat_signature(os.fstat(stream.fileno()))
        after = canary._stat_signature(path.stat())
    except LocalTexturedPreviewError:
        raise
    except OSError as exc:
        raise LocalTexturedPreviewError(f"{label} cannot be read safely") from exc
    if (
        opened != after_open
        or before != after
        or len(payload) != before[2]
        or len(payload) > maximum_bytes
    ):
        raise LocalTexturedPreviewError(f"{label} changed during bounded read")
    return payload


def verify_stored_local_glb_audit(
    path: Path,
    *,
    measured_audit: GlbMaterialAudit,
) -> GlbMaterialAudit:
    """Verify current or historical stored evidence against a fresh GLB audit."""

    raw = _read_stable_file(
        path,
        maximum_bytes=canary.MAX_BUILD_REPORT_BYTES,
        label="local GLB audit",
    )
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        if not isinstance(payload, dict):
            raise LocalTexturedPreviewError("local GLB audit is not an object")
        if "triangle_count" in payload:
            stored = GlbMaterialAudit.model_validate_json(raw)
            if raw != canonical_local_glb_audit_bytes(stored):
                raise LocalTexturedPreviewError(
                    "local GLB audit is not canonical JSON",
                )
            if stored != measured_audit:
                raise LocalTexturedPreviewError(
                    "local GLB audit does not match current GLB bytes",
                )
            return measured_audit

        historical = HistoricalLocalGlbMaterialAudit.model_validate_json(raw)
        if raw != _canonical_historical_local_glb_audit_bytes(historical):
            raise LocalTexturedPreviewError(
                "historical local GLB audit is not canonical JSON",
            )
        measured_payload = measured_audit.model_dump(mode="json")
        measured_payload.pop("triangle_count")
        historical_payload = historical.model_dump(mode="json")
        if "building_geometry" not in historical.model_fields_set:
            measured_payload.pop("building_geometry")
            historical_payload.pop("building_geometry")
        if historical_payload != measured_payload:
            raise LocalTexturedPreviewError(
                "historical local GLB audit does not match current GLB bytes",
            )
        return measured_audit
    except LocalTexturedPreviewError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise LocalTexturedPreviewError(f"local GLB audit is invalid: {exc}") from exc


def probe_local_blender_identity(
    executable: Path = DEFAULT_LOCAL_BLENDER,
) -> LocalBlenderIdentity:
    """Measure the exact local Blender binary and its bounded version output."""

    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise LocalTexturedPreviewError(
            "local textured preview requires macOS Apple Silicon",
        )
    executable = Path(executable).absolute()
    try:
        snapshot = canary._snapshot_regular_file(executable)
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
        )
        if (
            completed.returncode != 0
            or completed.stderr
            or not completed.stdout
            or len(completed.stdout) > MAX_RUNTIME_OUTPUT_BYTES
        ):
            raise LocalTexturedPreviewError(
                "local Blender version probe returned invalid bounded output",
            )
        output = completed.stdout.decode("utf-8", errors="strict")
    except LocalTexturedPreviewError:
        raise
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise LocalTexturedPreviewError("local Blender cannot be probed safely") from exc
    if (
        not output.startswith("Blender 4.5.11 LTS\n")
        or "\tbuild hash: 4db51e9d1e1e\n" not in output
        or "\tbuild platform: Darwin\n" not in output
        or canary._snapshot_regular_file(executable) != snapshot
    ):
        raise LocalTexturedPreviewError(
            "local Blender output does not match the measured Mac preview contract",
        )
    return LocalBlenderIdentity(
        executable_sha256=snapshot.sha256,
        version="4.5.11",
        platform="macos-arm64",
        runtime_build_hash="4db51e9d1e1e",
        runtime_output_sha256=hashlib.sha256(completed.stdout).hexdigest(),
    )


def load_local_textured_build_report(path: Path) -> LocalTexturedBuildReport:
    raw = _read_stable_file(
        path,
        maximum_bytes=canary.MAX_BUILD_REPORT_BYTES,
        label="local textured build report",
    )
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        if canary._contains_private_path(parsed):
            raise LocalTexturedPreviewError(
                "local textured build report contains a private path",
            )
        report = LocalTexturedBuildReport.model_validate_json(raw)
    except LocalTexturedPreviewError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise LocalTexturedPreviewError(
            f"local textured build report is invalid: {exc}",
        ) from exc
    if raw != canonical_local_textured_build_report_bytes(report):
        raise LocalTexturedPreviewError(
            "local textured build report is not canonical JSON",
        )
    return report


def verify_local_textured_build_report(
    report: LocalTexturedBuildReport,
    *,
    request: LocalTexturedPreviewRequest,
    staging: Path,
) -> None:
    try:
        staging = canary._require_real_directory(
            staging,
            label="local textured build staging",
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    for label in (
        "preview_id",
        "tool_identity",
        "source_hashes",
        "object_registry",
        "auxiliary_registry",
        "semantic_registry",
        "material_registry",
        "visual_slot_registry",
        "material_bundle_manifest_sha256",
        "material_bundle_id",
        "material_algorithm_id",
        "building_geometry_profile_id",
        "material_input_registry",
    ):
        if getattr(report, label) != getattr(request, label):
            raise LocalTexturedPreviewError(
                f"local build report {label.replace('_', ' ')} was tampered",
            )
    for reported, requested in zip(
        report.camera_registry,
        request.camera_plan.cameras,
        strict=True,
    ):
        if (
            reported.camera_id != requested.camera_id
            or reported.blender_camera_name != f"nv__{requested.camera_id}"
            or reported.requested_c2w_blender != requested.c2w_blender
        ):
            raise LocalTexturedPreviewError("local build camera registry was tampered")
    for artifact in report.artifacts:
        try:
            digest, size = canary._sha256_stable_artifact(staging / artifact.name)
        except canary.CanaryBuildError as exc:
            raise LocalTexturedPreviewError(str(exc)) from exc
        if digest != artifact.sha256 or size != artifact.size_bytes:
            raise LocalTexturedPreviewError(
                f"local build artifact changed: {artifact.name}",
            )


def _expected_glb_materials(
    request: LocalTexturedPreviewRequest,
) -> tuple[ExpectedGlbMaterial, ...]:
    return tuple(
        ExpectedGlbMaterial(
            slot_id=row.slot_id,
            source_sha256=row.source_sha256,
            bundle_id=request.material_bundle_id,
            algorithm_id=request.material_algorithm_id,
        )
        for row in request.material_input_registry
    )


def _expected_building_geometry(
    report: LocalTexturedBuildReport,
) -> ExpectedBuildingGeometry | None:
    if report.building_geometry_profile_id == BUILDING_GEOMETRY_V1:
        return None
    evidence = report.building_geometry
    if evidence is None:  # pragma: no cover - rejected by the report model
        raise LocalTexturedPreviewError(
            "local v2 build report has no building geometry evidence",
        )
    building_semantics = tuple(
        row.semantic_id
        for row in report.semantic_registry
        if row.semantic_class == "building"
    )
    if len(building_semantics) != 1:
        raise LocalTexturedPreviewError(
            "local build report has no unique building semantic identity",
        )
    building_ids = tuple(
        row.object_id
        for row in report.object_registry
        if row.semantic_id == building_semantics[0]
    )
    canonical_building_ids = tuple(
        row.object_id
        for row in build_scene_plan().objects
        if row.semantic_class == "building"
    )
    if building_ids != canonical_building_ids:
        raise LocalTexturedPreviewError(
            "local build report building IDs are not the canonical scene set",
        )
    try:
        return ExpectedBuildingGeometry(
            profile_id=BUILDING_GEOMETRY_V2,
            expected_building_ids=building_ids,
            variant_counts=evidence.variant_counts,
            expected_added_face_count=evidence.added_face_count,
            expected_maximum_added_faces_per_building=(
                evidence.maximum_added_faces_per_building
            ),
            expected_primitive_count=report.counts.glb_primitives,
        )
    except ValidationError as exc:
        raise LocalTexturedPreviewError(
            f"local building geometry expectation is invalid: {exc}",
        ) from exc


def _verify_building_geometry_audit_agreement(
    report: LocalTexturedBuildReport,
    audit: GlbMaterialAudit,
) -> None:
    evidence = report.building_geometry
    measured = audit.building_geometry
    if report.building_geometry_profile_id == BUILDING_GEOMETRY_V1:
        if measured is not None:
            raise LocalTexturedPreviewError(
                "local v1 preview cannot claim v2 GLB geometry evidence",
            )
        return
    if (
        evidence is None
        or measured is None
        or measured.profile_id != evidence.profile_id
        or measured.building_count != evidence.building_count
        or measured.covered_elevations != evidence.covered_elevations
        or measured.variant_counts != evidence.variant_counts
        or measured.builder_measured_added_face_count != evidence.added_face_count
        or measured.builder_measured_maximum_added_faces_per_building
        != evidence.maximum_added_faces_per_building
    ):
        raise LocalTexturedPreviewError(
            "local GLB building geometry audit disagrees with the build report",
        )


def load_local_textured_preview_manifest(path: Path) -> LocalTexturedPreviewManifest:
    raw = _read_stable_file(
        path,
        maximum_bytes=canary.MAX_BUILD_REPORT_BYTES,
        label="local preview manifest",
    )
    try:
        manifest = LocalTexturedPreviewManifest.model_validate_json(raw)
    except ValidationError as exc:
        raise LocalTexturedPreviewError(f"local preview manifest is invalid: {exc}") from exc
    if raw != canonical_local_textured_preview_manifest_bytes(manifest):
        raise LocalTexturedPreviewError("local preview manifest is not canonical JSON")
    return manifest


def read_verified_local_textured_preview_glb(
    path: Path,
    *,
    manifest: LocalTexturedPreviewManifest,
) -> bytes:
    payload = _read_stable_file(
        path,
        maximum_bytes=2 * 1024 * 1024 * 1024,
        label="local textured preview GLB",
    )
    if (
        len(payload) != manifest.glb_bytes
        or hashlib.sha256(payload).hexdigest() != manifest.glb_sha256
    ):
        raise LocalTexturedPreviewError(
            "local textured preview GLB does not match its manifest",
        )
    return payload


def verify_local_textured_preview_directory(
    directory: Path,
    *,
    expected_preview_id: str | None = None,
) -> tuple[
    LocalTexturedPreviewManifest,
    LocalTexturedBuildReport,
    GlbMaterialAudit,
]:
    """Revalidate the exact four-file private publication from actual bytes."""

    try:
        directory = canary._require_real_directory(
            directory,
            label="local textured preview directory",
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    expected_names = {
        "manifest.json",
        "village-canary.glb",
        "build-report.json",
        "glb-material-audit.json",
    }
    entries = tuple(directory.iterdir())
    if {entry.name for entry in entries} != expected_names or any(
        canary._is_linklike(entry) or not entry.is_file() for entry in entries
    ):
        raise LocalTexturedPreviewError(
            "local preview publication is not the exact four-file set",
        )
    manifest = load_local_textured_preview_manifest(directory / "manifest.json")
    if expected_preview_id is not None and manifest.preview_id != expected_preview_id:
        raise LocalTexturedPreviewError("local preview identity does not match its directory")
    report = load_local_textured_build_report(directory / "build-report.json")
    try:
        measured_audit = audit_textured_glb(
            directory / "village-canary.glb",
            _expected_glb_materials(report),
            expected_building_geometry=_expected_building_geometry(report),
        )
    except ValueError as exc:
        raise LocalTexturedPreviewError(f"local GLB audit failed: {exc}") from exc
    audit = verify_stored_local_glb_audit(
        directory / "glb-material-audit.json",
        measured_audit=measured_audit,
    )
    _verify_building_geometry_audit_agreement(report, measured_audit)
    try:
        glb_sha256, glb_bytes = canary._sha256_stable_artifact(
            directory / "village-canary.glb",
        )
        report_sha256, _ = canary._sha256_stable_artifact(
            directory / "build-report.json",
        )
        audit_sha256, _ = canary._sha256_stable_artifact(
            directory / "glb-material-audit.json",
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    if (
        manifest.preview_id != report.preview_id
        or manifest.material_bundle_id != report.material_bundle_id
        or manifest.glb_sha256 != glb_sha256
        or manifest.glb_bytes != glb_bytes
        or manifest.build_report_sha256 != report_sha256
        or manifest.audit_sha256 != audit_sha256
        or audit.glb_sha256 != glb_sha256
    ):
        raise LocalTexturedPreviewError("local preview publication digests disagree")
    return manifest, report, audit


def verify_local_textured_training_build_layout(
    directory: Path,
    *,
    expected_report_sha256: str | None = None,
) -> Path:
    """Verify the exact file layout and report content identity of a build snapshot."""

    try:
        directory = canary._require_real_directory(
            Path(directory).absolute(),
            label="local textured training build directory",
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    report_sha256 = directory.name
    if (
        len(report_sha256) != 64
        or any(character not in "0123456789abcdef" for character in report_sha256)
        or (
            expected_report_sha256 is not None
            and report_sha256 != expected_report_sha256
        )
    ):
        raise LocalTexturedPreviewError(
            "local textured training build identity does not match its directory",
        )
    entries = tuple(directory.iterdir())
    if {entry.name for entry in entries} != set(LOCAL_TRAINING_BUILD_ENTRIES) or any(
        canary._is_linklike(entry) or not entry.is_file() for entry in entries
    ):
        raise LocalTexturedPreviewError(
            "local textured training build is not the exact nine-file set",
        )
    try:
        measured_report_sha256, _ = canary._sha256_stable_artifact(
            directory / "build-report.json",
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    if measured_report_sha256 != report_sha256:
        raise LocalTexturedPreviewError(
            "local textured training build report digest disagrees with its identity",
        )
    return directory


def verify_local_textured_training_build_directory(
    directory: Path,
    *,
    request: LocalTexturedPreviewRequest,
) -> tuple[
    LocalTexturedBuildReport,
    GlbMaterialAudit,
    LocalTexturedPreviewManifest,
]:
    """Revalidate a complete training build from its current private bytes."""

    directory = verify_local_textured_training_build_layout(directory)
    report = load_local_textured_build_report(directory / "build-report.json")
    verify_local_textured_build_report(
        report,
        request=request,
        staging=directory,
    )
    try:
        measured_audit = audit_textured_glb(
            directory / "village-canary.glb",
            _expected_glb_materials(request),
            expected_building_geometry=_expected_building_geometry(report),
        )
    except ValueError as exc:
        raise LocalTexturedPreviewError(
            f"local training GLB audit failed: {exc}",
        ) from exc
    audit = verify_stored_local_glb_audit(
        directory / "glb-material-audit.json",
        measured_audit=measured_audit,
    )
    _verify_building_geometry_audit_agreement(report, audit)
    try:
        report_sha256, _ = canary._sha256_stable_artifact(
            directory / "build-report.json",
        )
        audit_sha256, _ = canary._sha256_stable_artifact(
            directory / "glb-material-audit.json",
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    expected_manifest = build_local_textured_preview_manifest(
        request=request,
        glb_sha256=audit.glb_sha256,
        glb_bytes=audit.byte_count,
        build_report_sha256=report_sha256,
        audit_sha256=audit_sha256,
    )
    manifest = load_local_textured_preview_manifest(directory / "manifest.json")
    if manifest != expected_manifest:
        raise LocalTexturedPreviewError(
            "local textured training build manifest disagrees with current bytes",
        )
    return report, audit, manifest


def _publish_local_textured_training_build(
    *,
    staging: Path,
    training_root: Path,
    build_report_sha256: str,
) -> Path:
    """Copy one fully verified nine-file staging tree into immutable private storage."""

    try:
        staging = canary._require_real_directory(
            Path(staging).absolute(),
            label="local textured training source staging",
        )
        training_root = canary._require_real_directory(
            Path(training_root).absolute(),
            label="local textured training build root",
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    entries = tuple(staging.iterdir())
    if {entry.name for entry in entries} != set(LOCAL_TRAINING_BUILD_ENTRIES) or any(
        canary._is_linklike(entry) or not entry.is_file() for entry in entries
    ):
        raise LocalTexturedPreviewError(
            "local textured training source is not the exact nine-file set",
        )
    snapshots = {
        entry.name: canary._snapshot_regular_file(entry) for entry in entries
    }
    if snapshots["build-report.json"].sha256 != build_report_sha256:
        raise LocalTexturedPreviewError(
            "local textured training source report digest is invalid",
        )

    final_directory = training_root / build_report_sha256
    if final_directory.exists() or canary._is_linklike(final_directory):
        verified = verify_local_textured_training_build_layout(
            final_directory,
            expected_report_sha256=build_report_sha256,
        )
        for name, source_snapshot in snapshots.items():
            destination_snapshot = canary._snapshot_regular_file(verified / name)
            if (
                destination_snapshot.sha256 != source_snapshot.sha256
                or destination_snapshot.signature[2] != source_snapshot.signature[2]
            ):
                raise LocalTexturedPreviewError(
                    "existing local textured training build bytes disagree",
                )
        return verified

    publication_staging = training_root / f".training-staging-{uuid.uuid4().hex}"
    try:
        publication_staging.mkdir(exist_ok=False)
        canary._flush_directory(training_root)
        for name in LOCAL_TRAINING_BUILD_ENTRIES:
            source = staging / name
            destination = publication_staging / name
            shutil.copyfile(source, destination)
            canary._flush_file(destination)
            source_after = canary._snapshot_regular_file(source)
            copied = canary._snapshot_regular_file(destination)
            expected = snapshots[name]
            if (
                source_after != expected
                or copied.sha256 != expected.sha256
                or copied.signature[2] != expected.signature[2]
            ):
                raise LocalTexturedPreviewError(
                    "local textured training build changed during snapshot copy",
                )
        canary._flush_directory(publication_staging)
        try:
            _move_directory_noreplace(publication_staging, final_directory)
        except MaterialBundleError as exc:
            raise LocalTexturedPreviewError(str(exc)) from exc
        return verify_local_textured_training_build_layout(
            final_directory,
            expected_report_sha256=build_report_sha256,
        )
    finally:
        if publication_staging.exists() and not canary._is_linklike(
            publication_staging,
        ):
            canary._cleanup_owned_directory(
                publication_staging,
                work_root=training_root,
                expected_name=publication_staging.name,
            )


@dataclass(frozen=True)
class LocalTexturedPreviewResult:
    final_directory: Path
    request: LocalTexturedPreviewRequest
    manifest: LocalTexturedPreviewManifest
    report: LocalTexturedBuildReport
    audit: GlbMaterialAudit
    stdout: str
    stderr: str
    reused: bool
    training_build_directory: Path | None = None


@dataclass(frozen=True)
class LocalTexturedRenderResult:
    render_root: Path
    journal_path: Path
    render_id: str
    rendered_count: int
    reused_count: int
    stdout: str
    stderr: str


def build_local_textured_preview_request(
    *,
    material_bundle_root: Path,
    tool_identity: LocalBlenderIdentity,
    repo_root: Path = ROOT,
    scene_plan: ScenePlan | None = None,
    camera_plan: CameraPlan | None = None,
    visual_pack_root: Path | None = None,
) -> LocalTexturedPreviewRequest:
    """Build path-free L0 inputs without consulting the Windows tool receipt."""

    repo_root = Path(repo_root).absolute()
    active_scene = scene_plan or build_scene_plan()
    active_camera = camera_plan or build_camera_plan(active_scene)
    recipe_path = repo_root / "assets/default-resources/synthetic-mountain-village-v1.json"
    lock_path = repo_root / "tools.lock.json"
    builder_script = repo_root / "scripts/blender/build_synthetic_village.py"
    pack_root = Path(
        visual_pack_root
        or repo_root / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources",
    ).absolute()
    bundle_root = Path(material_bundle_root).absolute()
    try:
        bundle = load_material_bundle(bundle_root)
    except MaterialBundleError as exc:
        raise LocalTexturedPreviewError(
            f"material bundle cannot be trusted: {exc}",
        ) from exc
    inputs = canary._material_input_registry(bundle)
    recipe = load_default_recipe(recipe_path)
    if active_scene.recipe_id != recipe.recipe_id:
        raise LocalTexturedPreviewError("scene plan does not reference the tracked recipe")
    try:
        slots, catalog, manifest = canary._textured_visual_slot_registry(
            repo_root,
            pack_root,
            inputs,
            active_scene,
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    source_manifest_sha256 = hashlib.sha256(
        canonical_manifest_bytes(manifest),
    ).hexdigest()
    sources = {
        record.slot_id: record.sha256
        for record in manifest.records
        if record.category == "material"
    }
    if (
        bundle.source_pack_id != manifest.pack_id
        or bundle.source_manifest_sha256 != source_manifest_sha256
        or sources != {row.slot_id: row.source_sha256 for row in inputs}
    ):
        raise LocalTexturedPreviewError(
            "material bundle does not match the selected visual source pack",
        )
    semantics = canary._semantic_registry()
    materials = canary._material_registry(active_scene)
    objects = canary._object_registry(active_scene, semantics, materials)
    source_hashes = canary.SourceHashes(
        default_recipe_sha256=hashlib.sha256(canonical_json_bytes(recipe)).hexdigest(),
        visual_catalog_sha256=hashlib.sha256(canonical_json_bytes(catalog)).hexdigest(),
        visual_source_manifest_sha256=source_manifest_sha256,
        scene_plan_sha256=hashlib.sha256(
            canonical_scene_plan_bytes(active_scene),
        ).hexdigest(),
        camera_plan_sha256=hashlib.sha256(
            canonical_camera_plan_bytes(active_camera),
        ).hexdigest(),
        tool_lock_sha256=canary._sha256_file(lock_path),
        builder_script_sha256=canary._sha256_file(builder_script),
    )
    payload = {
        "schema_version": LOCAL_REQUEST_SCHEMA,
        "synthetic": True,
        "verification_level": "L0",
        "authoritative": False,
        "release_channel": LOCAL_RELEASE_CHANNEL,
        "tool_identity": tool_identity,
        "scene_plan": active_scene,
        "camera_plan": active_camera,
        "source_hashes": source_hashes,
        "object_registry": objects,
        "auxiliary_registry": canary.AUXILIARY_REGISTRY,
        "semantic_registry": semantics,
        "material_registry": materials,
        "visual_slot_registry": slots,
        "requested_artifacts": canary.ARTIFACT_REQUESTS,
        "material_bundle_manifest_sha256": canary._sha256_file(
            bundle_root / MATERIAL_BUNDLE_MANIFEST,
        ),
        "material_bundle_id": bundle.bundle_id,
        "material_algorithm_id": bundle.algorithm_id,
        "building_geometry_profile_id": BUILDING_GEOMETRY_V2,
        "material_input_registry": inputs,
    }
    preview_id = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return LocalTexturedPreviewRequest(preview_id=preview_id, **payload)


def build_local_textured_preview_manifest(
    *,
    request: LocalTexturedPreviewRequest,
    glb_sha256: str,
    glb_bytes: int,
    build_report_sha256: str,
    audit_sha256: str,
) -> LocalTexturedPreviewManifest:
    """Describe only measured local bytes and immutable truth limitations."""

    return LocalTexturedPreviewManifest(
        preview_id=request.preview_id,
        model_url=(
            f"/api/local-textured-preview/{request.preview_id}/village-canary.glb"
        ),
        glb_sha256=glb_sha256,
        glb_bytes=glb_bytes,
        build_report_sha256=build_report_sha256,
        audit_sha256=audit_sha256,
        material_bundle_id=request.material_bundle_id,
        limitations=LOCAL_LIMITATIONS,
    )


def _collect_local_input_snapshots(
    *,
    repo_root: Path,
    visual_pack_root: Path,
    material_bundle_root: Path,
    executable: Path,
):
    manifest_path = visual_pack_root / VISUAL_MANIFEST_NAME
    manifest = load_visual_source_manifest(manifest_path)
    paths = {
        repo_root / "assets/default-resources/synthetic-mountain-village-v1.json",
        repo_root
        / "assets/default-resources/synthetic-mountain-village-visual-slots-v1.json",
        repo_root / "tools.lock.json",
        repo_root / "scripts/blender/build_synthetic_village.py",
        manifest_path,
        executable,
        *(visual_pack_root / row.object_path for row in manifest.records),
    }
    try:
        source_snapshots = tuple(
            canary._snapshot_regular_file(path) for path in sorted(paths)
        )
        bundle_snapshots = canary._collect_material_bundle_snapshots(
            material_bundle_root,
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    return (*source_snapshots, *bundle_snapshots)


def _prepare_local_roots(
    *,
    repo_root: Path,
    work_root: Path | None,
    publication_root: Path | None,
) -> tuple[Path, Path]:
    try:
        active_work_root = canary._prepare_private_work_root(
            repo_root,
            (
                Path(work_root).absolute()
                if work_root is not None
                else repo_root
                / ".nantai-studio/synthetic-village/hybrid-v3/local-preview-work"
            ),
        )
        selected_publication = (
            Path(publication_root).absolute()
            if publication_root is not None
            else repo_root
            / ".nantai-studio/synthetic-village/hybrid-v3/local-previews"
        )
        private_root = repo_root / ".nantai-studio"
        selected_publication.relative_to(private_root)
        canary._ensure_real_directory_tree(private_root, repo_root=repo_root)
        active_publication_root = canary._ensure_real_directory_tree(
            selected_publication,
            repo_root=repo_root,
        )
    except (ValueError, canary.CanaryBuildError) as exc:
        raise LocalTexturedPreviewError(
            "local preview roots must be real directories below .nantai-studio",
        ) from exc
    return active_work_root, active_publication_root


def _prepare_training_build_root(
    *,
    repo_root: Path,
    training_build_root: Path | None,
) -> Path | None:
    if training_build_root is None:
        return None
    try:
        selected = Path(training_build_root).absolute()
        private_root = repo_root / ".nantai-studio"
        selected.relative_to(private_root)
        canary._ensure_real_directory_tree(private_root, repo_root=repo_root)
        return canary._ensure_real_directory_tree(selected, repo_root=repo_root)
    except (ValueError, canary.CanaryBuildError) as exc:
        raise LocalTexturedPreviewError(
            "local training build root must be a real directory below .nantai-studio",
        ) from exc


def run_local_textured_preview(
    *,
    material_bundle_root: Path,
    repo_root: Path = ROOT,
    visual_pack_root: Path | None = None,
    executable: Path = DEFAULT_LOCAL_BLENDER,
    work_root: Path | None = None,
    publication_root: Path | None = None,
    training_build_root: Path | None = None,
    timeout_seconds: int = 60 * 60,
) -> LocalTexturedPreviewResult:
    """Build, audit, prune, and absent-publish one truthful Mac L0 preview.

    Supplying ``training_build_root`` intentionally forces a fresh Blender build
    and retains its complete nine-file evidence set even when the four-file
    Viewer publication already exists.
    """

    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 24 * 60 * 60
    ):
        raise LocalTexturedPreviewError(
            "local preview timeout must be an integer from 1 to 86400 seconds",
        )
    try:
        repo_root = canary._require_real_directory(
            Path(repo_root).absolute(),
            label="repository root",
        )
        pack_root = canary._require_real_directory(
            Path(
                visual_pack_root
                or repo_root
                / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources",
            ).absolute(),
            label="visual source pack",
        )
        bundle_root = canary._require_real_directory(
            Path(material_bundle_root).absolute(),
            label="material bundle root",
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    executable = Path(executable).absolute()
    active_work_root, active_publication_root = _prepare_local_roots(
        repo_root=repo_root,
        work_root=work_root,
        publication_root=publication_root,
    )
    active_training_root = _prepare_training_build_root(
        repo_root=repo_root,
        training_build_root=training_build_root,
    )
    invocation_root: Path | None = None
    staging: Path | None = None
    try:
        with ProjectFileLock(
            active_work_root / ".local-textured-preview.lock",
            role="writer",
        ):
            identity = probe_local_blender_identity(executable)
            snapshots = _collect_local_input_snapshots(
                repo_root=repo_root,
                visual_pack_root=pack_root,
                material_bundle_root=bundle_root,
                executable=executable,
            )
            scene = build_scene_plan()
            request = build_local_textured_preview_request(
                repo_root=repo_root,
                scene_plan=scene,
                camera_plan=build_camera_plan(scene),
                visual_pack_root=pack_root,
                material_bundle_root=bundle_root,
                tool_identity=identity,
            )
            try:
                canary._verify_snapshots_unchanged(snapshots)
            except canary.CanaryBuildError as exc:
                raise LocalTexturedPreviewError(str(exc)) from exc
            final_directory = active_publication_root / request.preview_id
            existing_publication: tuple[
                LocalTexturedPreviewManifest,
                LocalTexturedBuildReport,
                GlbMaterialAudit,
            ] | None = None
            if final_directory.exists() or canary._is_linklike(final_directory):
                manifest, report, audit = verify_local_textured_preview_directory(
                    final_directory,
                    expected_preview_id=request.preview_id,
                )
                if active_training_root is None:
                    return LocalTexturedPreviewResult(
                        final_directory=final_directory,
                        request=request,
                        manifest=manifest,
                        report=report,
                        audit=audit,
                        stdout="",
                        stderr="",
                        reused=True,
                    )
                existing_publication = (manifest, report, audit)

            nonce = uuid.uuid4().hex
            invocation_root = active_work_root / f".local-invocation-{nonce}"
            staging = active_work_root / f".local-staging-{nonce}"
            invocation_root.mkdir(exist_ok=False)
            canary._flush_directory(active_work_root)
            request_path = invocation_root / "build-request.json"
            canary._write_new_file(
                request_path,
                canonical_local_textured_preview_request_bytes(request),
            )
            request_snapshot = canary._snapshot_regular_file(request_path)
            try:
                material_snapshots = canary.snapshot_material_inputs(
                    request=request,  # type: ignore[arg-type]
                    material_bundle_root=bundle_root,
                    invocation_root=invocation_root,
                )
            except canary.CanaryBuildError as exc:
                raise LocalTexturedPreviewError(str(exc)) from exc
            material_root = invocation_root / "material-inputs"
            try:
                returncode, stdout, stderr = canary._run_textured_blender_process(
                    repo_root=repo_root,
                    executable=executable,
                    request_path=request_path,
                    material_root=material_root,
                    staging=staging,
                    invocation_root=invocation_root,
                    timeout_seconds=timeout_seconds,
                )
            except canary.CanaryBuildError as exc:
                raise LocalTexturedPreviewError(str(exc)) from exc
            immutable_inputs = (
                *snapshots,
                request_snapshot,
                *material_snapshots,
            )
            try:
                canary._verify_snapshots_unchanged(immutable_inputs)
            except canary.CanaryBuildError as exc:
                raise LocalTexturedPreviewError(str(exc)) from exc
            if returncode != 0:
                raise LocalTexturedPreviewError(
                    f"local Blender build failed with exit code {returncode}: "
                    f"{(stderr or stdout)[-4000:]}",
                )
            try:
                canary._validate_staging_entries(staging)
            except canary.CanaryBuildError as exc:
                raise LocalTexturedPreviewError(str(exc)) from exc
            report = load_local_textured_build_report(staging / "build-report.json")
            verify_local_textured_build_report(
                report,
                request=request,
                staging=staging,
            )
            try:
                audit = audit_textured_glb(
                    staging / "village-canary.glb",
                    _expected_glb_materials(request),
                    expected_building_geometry=_expected_building_geometry(report),
                )
            except ValueError as exc:
                raise LocalTexturedPreviewError(
                    f"local GLB material audit failed: {exc}",
                ) from exc
            _verify_building_geometry_audit_agreement(report, audit)
            glb_record = next(
                row for row in report.artifacts if row.name == "village-canary.glb"
            )
            if (
                audit.glb_sha256 != glb_record.sha256
                or audit.byte_count != glb_record.size_bytes
            ):
                raise LocalTexturedPreviewError(
                    "local GLB audit disagrees with the build report",
                )
            try:
                canary._durably_flush_verified_staging(staging)
                canary._verify_snapshots_unchanged(immutable_inputs)
            except canary.CanaryBuildError as exc:
                raise LocalTexturedPreviewError(str(exc)) from exc
            verify_local_textured_build_report(
                report,
                request=request,
                staging=staging,
            )

            audit_bytes = canonical_local_glb_audit_bytes(audit)
            audit_path = staging / "glb-material-audit.json"
            canary._write_new_file(audit_path, audit_bytes)
            build_report_sha256, _ = canary._sha256_stable_artifact(
                staging / "build-report.json",
            )
            manifest = build_local_textured_preview_manifest(
                request=request,
                glb_sha256=audit.glb_sha256,
                glb_bytes=audit.byte_count,
                build_report_sha256=build_report_sha256,
                audit_sha256=hashlib.sha256(audit_bytes).hexdigest(),
            )
            canary._write_new_file(
                staging / "manifest.json",
                canonical_local_textured_preview_manifest_bytes(manifest),
            )
            training_build_directory = None
            if active_training_root is not None:
                training_build_directory = (
                    _publish_local_textured_training_build(
                        staging=staging,
                        training_root=active_training_root,
                        build_report_sha256=build_report_sha256,
                    )
                )
                verify_local_textured_training_build_directory(
                    training_build_directory,
                    request=request,
                )
            if existing_publication is not None:
                existing_manifest, existing_report, existing_audit = (
                    existing_publication
                )
                return LocalTexturedPreviewResult(
                    final_directory=final_directory,
                    request=request,
                    manifest=existing_manifest,
                    report=existing_report,
                    audit=existing_audit,
                    stdout=stdout,
                    stderr=stderr,
                    reused=True,
                    training_build_directory=training_build_directory,
                )
            for name in (
                "preview-bridge.png",
                "preview-central.png",
                "preview-outer.png",
                "preview-upper.png",
                "village-canary.blend",
            ):
                (staging / name).unlink()
            canary._flush_directory(staging)
            verified_manifest, verified_report, verified_audit = (
                verify_local_textured_preview_directory(
                    staging,
                    expected_preview_id=request.preview_id,
                )
            )
            for path in sorted(staging.iterdir(), key=lambda item: item.name):
                canary._flush_file(path)
            canary._flush_directory(staging)
            try:
                _move_directory_noreplace(staging, final_directory)
            except MaterialBundleError as exc:
                raise LocalTexturedPreviewError(str(exc)) from exc
            staging = None
            return LocalTexturedPreviewResult(
                final_directory=final_directory,
                request=request,
                manifest=verified_manifest,
                report=verified_report,
                audit=verified_audit,
                stdout=stdout,
                stderr=stderr,
                reused=False,
                training_build_directory=training_build_directory,
            )
    except LocalTexturedPreviewError:
        raise
    except JobContractError as exc:
        raise LocalTexturedPreviewError(
            f"local preview build lock is unavailable: {exc}",
        ) from exc
    except (OSError, RuntimeError, ValueError, ValidationError) as exc:
        raise LocalTexturedPreviewError(
            f"local textured preview failed safely: {exc}",
        ) from exc
    finally:
        if staging is not None:
            canary._cleanup_owned_directory(
                staging,
                work_root=active_work_root,
                expected_name=staging.name,
            )
        if invocation_root is not None:
            canary._cleanup_owned_directory(
                invocation_root,
                work_root=active_work_root,
                expected_name=invocation_root.name,
            )


def run_local_textured_training_render(
    *,
    training_build_directory: Path,
    material_bundle_root: Path,
    repo_root: Path = ROOT,
    visual_pack_root: Path | None = None,
    executable: Path = DEFAULT_LOCAL_BLENDER,
    render_root: Path | None = None,
    camera_ids: tuple[str, ...] | None = None,
    timeout_seconds: int = canary.DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> LocalTexturedRenderResult:
    """Resume strict six-layer L0 renders from one verified local PBR build."""

    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 24 * 60 * 60
    ):
        raise LocalTexturedPreviewError(
            "local render timeout must be an integer from 1 to 86400 seconds",
        )
    try:
        selected_ids = canary._normalize_render_camera_ids(camera_ids)
        repo_root = canary._require_real_directory(
            Path(repo_root).absolute(),
            label="repository root",
        )
        training_build_directory = canary._require_real_directory(
            Path(training_build_directory).absolute(),
            label="local textured training build directory",
        )
        private_root = repo_root / ".nantai-studio"
        training_build_directory.relative_to(private_root)
        bundle_root = canary._require_real_directory(
            Path(material_bundle_root).absolute(),
            label="material bundle root",
        )
        pack_root = canary._require_real_directory(
            Path(
                visual_pack_root
                or repo_root
                / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources",
            ).absolute(),
            label="visual source pack",
        )
    except (ValueError, canary.CanaryBuildError) as exc:
        raise LocalTexturedPreviewError(
            "local render inputs must be real private project paths",
        ) from exc

    executable = Path(executable).absolute()
    identity = probe_local_blender_identity(executable)
    scene = build_scene_plan()
    request = build_local_textured_preview_request(
        repo_root=repo_root,
        scene_plan=scene,
        camera_plan=build_camera_plan(scene),
        visual_pack_root=pack_root,
        material_bundle_root=bundle_root,
        tool_identity=identity,
    )
    report, _audit, _manifest = verify_local_textured_training_build_directory(
        training_build_directory,
        request=request,
    )
    try:
        executable_snapshot = canary._snapshot_regular_file(executable)
        renderer_snapshot = canary._snapshot_regular_file(
            repo_root / "scripts/blender/render_synthetic_village.py",
        )
        report_snapshot = canary._snapshot_regular_file(
            training_build_directory / "build-report.json",
        )
        build_snapshots = tuple(
            canary._snapshot_regular_file(training_build_directory / name)
            for name in LOCAL_TRAINING_BUILD_ENTRIES
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    if (
        executable_snapshot.sha256 != report.tool_identity.executable_sha256
        or report_snapshot.sha256 != training_build_directory.name
    ):
        raise LocalTexturedPreviewError(
            "local render runtime or build report identity disagrees",
        )
    blend_record = next(
        row for row in report.artifacts if row.name == "village-canary.blend"
    )
    blend_path = training_build_directory / blend_record.name
    blend_snapshot = next(
        row for row in build_snapshots if row.path == blend_path
    )
    if (
        blend_snapshot.sha256 != blend_record.sha256
        or blend_snapshot.signature[2] != blend_record.size_bytes
    ):
        raise LocalTexturedPreviewError(
            "local render Blender scene disagrees with build evidence",
        )
    immutable_snapshots = (
        executable_snapshot,
        renderer_snapshot,
        *build_snapshots,
    )
    object_registry_sha256 = hashlib.sha256(
        canary._canonical_json_bytes(
            [row.model_dump(mode="json") for row in report.object_registry],
        ),
    ).hexdigest()
    settings = canary.RenderSettings()
    render_identity = {
        "schema_version": canary.LOCAL_RENDER_JOURNAL_SCHEMA,
        "build_id": request.preview_id,
        "verification_level": "L0",
        "blender_executable_sha256": executable_snapshot.sha256,
        "renderer_script_sha256": renderer_snapshot.sha256,
        "blend_sha256": blend_snapshot.sha256,
        "build_report_sha256": report_snapshot.sha256,
        "object_registry_sha256": object_registry_sha256,
        "settings": settings,
        "camera_ids": canary.RENDER_CAMERA_IDS,
        "camera_plan_sha256": report.source_hashes.camera_plan_sha256,
    }
    render_id = hashlib.sha256(
        canary._canonical_json_bytes(render_identity),
    ).hexdigest()
    try:
        selected_render_root = (
            Path(render_root).absolute()
            if render_root is not None
            else DEFAULT_LOCAL_TRAINING_RENDER_ROOT
            / report_snapshot.sha256
            / render_id
        )
        selected_render_root.relative_to(private_root)
        canary._ensure_real_directory_tree(private_root, repo_root=repo_root)
        selected_render_root = canary._ensure_real_directory_tree(
            selected_render_root,
            repo_root=repo_root,
        )
    except (ValueError, canary.CanaryBuildError) as exc:
        raise LocalTexturedPreviewError(
            "local render root must be a real directory below .nantai-studio",
        ) from exc

    journal_path = selected_render_root / "render-journal.json"
    rendered_count = 0
    reused_count = 0
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    try:
        with ProjectFileLock(
            selected_render_root / ".local-render.lock",
            role="writer",
        ):
            if journal_path.exists() or canary._is_linklike(journal_path):
                journal = canary.load_render_journal(journal_path)
                immutable = (
                    journal.schema_version,
                    journal.verification_level,
                    journal.render_id,
                    journal.build_id,
                    journal.blender_executable_sha256,
                    journal.renderer_script_sha256,
                    journal.blend_sha256,
                    journal.build_report_sha256,
                    journal.object_registry_sha256,
                    journal.settings,
                )
                expected = (
                    canary.LOCAL_RENDER_JOURNAL_SCHEMA,
                    "L0",
                    render_id,
                    request.preview_id,
                    executable_snapshot.sha256,
                    renderer_snapshot.sha256,
                    blend_snapshot.sha256,
                    report_snapshot.sha256,
                    object_registry_sha256,
                    settings,
                )
                if immutable != expected:
                    raise LocalTexturedPreviewError(
                        "existing local render journal belongs to different inputs",
                    )
            else:
                journal = canary._seal_render_journal(
                    canary.RenderJournal(
                        schema_version=canary.LOCAL_RENDER_JOURNAL_SCHEMA,
                        render_id=render_id,
                        journal_sha256="0" * 64,
                        build_id=request.preview_id,
                        verification_level="L0",
                        blender_executable_sha256=executable_snapshot.sha256,
                        renderer_script_sha256=renderer_snapshot.sha256,
                        blend_sha256=blend_snapshot.sha256,
                        build_report_sha256=report_snapshot.sha256,
                        object_registry_sha256=object_registry_sha256,
                        settings=settings,
                        frames=tuple(
                            canary.RenderFrameRecord(
                                camera_id=camera_id,
                                state="planned",
                            )
                            for camera_id in canary.RENDER_CAMERA_IDS
                        ),
                    ),
                )
                canary._write_render_journal(journal_path, journal)

            cameras = {
                row.camera_id: row for row in request.camera_plan.cameras
            }
            measured = {
                row.camera_id: row.measured_c2w_blender
                for row in report.camera_registry
            }
            for camera_id in selected_ids:
                stage: Literal["prepare", "invoke", "validate", "publish"] = (
                    "prepare"
                )
                nonce = uuid.uuid4().hex[:12]
                temporary_root = selected_render_root.parent
                invocation_root = temporary_root / f".lri-{nonce}"
                staging = temporary_root / f".lrs-{nonce}"
                runtime_work = staging.with_name(
                    f".{staging.name}.tmp-{render_id[:12]}",
                )
                try:
                    frame = next(
                        row for row in journal.frames if row.camera_id == camera_id
                    )
                    if frame.state == "verified":
                        try:
                            canary._verify_published_frame(
                                selected_render_root,
                                frame,
                            )
                            reused_count += 1
                            continue
                        except canary.CanaryBuildError:
                            canary._quarantine_frame_outputs(
                                selected_render_root,
                                camera_id,
                            )
                    elif canary._frame_has_any_output(
                        selected_render_root,
                        camera_id,
                    ):
                        canary._quarantine_frame_outputs(
                            selected_render_root,
                            camera_id,
                        )
                    journal = canary._replace_frame_record(
                        journal,
                        camera_id,
                        canary.RenderFrameRecord(
                            camera_id=camera_id,
                            state="rendering",
                        ),
                    )
                    canary._write_render_journal(journal_path, journal)

                    invocation_root.mkdir(exist_ok=False)
                    canary._flush_directory(temporary_root)
                    frame_request = canary.RenderFrameRequest(
                        schema_version=canary.LOCAL_RENDER_REQUEST_SCHEMA,
                        render_id=render_id,
                        build_id=request.preview_id,
                        verification_level="L0",
                        blender_executable_sha256=executable_snapshot.sha256,
                        renderer_script_sha256=renderer_snapshot.sha256,
                        blend_sha256=blend_snapshot.sha256,
                        build_report_sha256=report_snapshot.sha256,
                        object_registry_sha256=object_registry_sha256,
                        settings=settings,
                        camera=cameras[camera_id],
                        measured_c2w_blender=measured[camera_id],
                        object_registry=report.object_registry,
                        auxiliary_registry=report.auxiliary_registry,
                        semantic_registry=report.semantic_registry,
                    )
                    request_path = invocation_root / "render-request.json"
                    canary._write_new_file(
                        request_path,
                        canary.canonical_render_request_bytes(frame_request),
                    )
                    request_snapshot = canary._snapshot_regular_file(request_path)
                    stage = "invoke"
                    try:
                        returncode, stdout, stderr = (
                            canary._run_blender_render_process(
                                repo_root=repo_root,
                                executable=executable_snapshot.path,
                                blend_path=blend_path,
                                request_path=request_path,
                                staging=staging,
                                invocation_root=invocation_root,
                                timeout_seconds=timeout_seconds,
                            )
                        )
                    finally:
                        canary._verify_snapshots_unchanged(
                            (*immutable_snapshots, request_snapshot),
                        )
                    stdout_parts.append(stdout)
                    stderr_parts.append(stderr)
                    if returncode != 0:
                        runtime_error = next(
                            (
                                line.strip()
                                for line in reversed(
                                    (stdout + "\n" + stderr).splitlines(),
                                )
                                if line.strip().startswith(
                                    "NANTAI_RENDER_ERROR ",
                                )
                            ),
                            "",
                        )
                        suffix = (
                            f": {canary._sanitize_render_error(RuntimeError(runtime_error))}"
                            if runtime_error
                            else ""
                        )
                        raise LocalTexturedPreviewError(
                            "local Blender render failed with exit code "
                            f"{returncode}{suffix}",
                        )
                    stage = "validate"
                    frame_report, frame_report_sha256 = (
                        canary._validate_frame_staging(staging, frame_request)
                    )
                    canary._durably_flush_frame_staging(staging)
                    stage = "publish"
                    canary._publish_frame(
                        staging,
                        selected_render_root,
                        frame_report,
                    )
                    verified = canary.RenderFrameRecord(
                        camera_id=camera_id,
                        state="verified",
                        artifacts=frame_report.artifacts,
                        runtime_report_sha256=frame_report_sha256,
                        statistics=frame_report.statistics,
                    )
                    canary._verify_published_frame(
                        selected_render_root,
                        verified,
                    )
                    canary._verify_snapshots_unchanged(immutable_snapshots)
                    journal = canary._replace_frame_record(
                        journal,
                        camera_id,
                        verified,
                    )
                    canary._write_render_journal(journal_path, journal)
                    rendered_count += 1
                except Exception as exc:
                    error = (
                        exc
                        if isinstance(
                            exc,
                            (LocalTexturedPreviewError, canary.CanaryBuildError),
                        )
                        else LocalTexturedPreviewError(str(exc))
                    )
                    failed = canary.RenderFrameRecord(
                        camera_id=camera_id,
                        state="failed",
                        error=canary.RenderFailure(
                            stage=stage,
                            message=canary._sanitize_render_error(error),
                        ),
                    )
                    journal = canary._replace_frame_record(
                        journal,
                        camera_id,
                        failed,
                    )
                    canary._write_render_journal(journal_path, journal)
                    if error is exc:
                        raise
                    raise error from exc
                finally:
                    for owned in (runtime_work, staging, invocation_root):
                        canary._cleanup_owned_directory(
                            owned,
                            work_root=temporary_root,
                            expected_name=owned.name,
                        )
    except LocalTexturedPreviewError:
        raise
    except (canary.CanaryBuildError, JobContractError) as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    return LocalTexturedRenderResult(
        render_root=selected_render_root,
        journal_path=journal_path,
        render_id=render_id,
        rendered_count=rendered_count,
        reused_count=reused_count,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )
