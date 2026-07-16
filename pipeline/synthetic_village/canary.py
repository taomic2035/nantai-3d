"""Fail-closed host contract for the private Blender village canary."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
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

from pipeline.studio_jobs import (
    JobContractError,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
)
from pipeline.synthetic_village.camera_plan import (
    CameraPlan,
    CameraPose,
    Matrix4,
    build_camera_plan,
    canonical_camera_plan_bytes,
)
from pipeline.synthetic_village.contracts import SlotCategory
from pipeline.synthetic_village.defaults import (
    canonical_json_bytes,
    load_default_recipe,
    load_default_visual_slots,
)
from pipeline.synthetic_village.scene_plan import (
    SEMANTIC_ORDER,
    ScenePlan,
    build_scene_plan,
    canonical_scene_plan_bytes,
)
from pipeline.synthetic_village.tool_lock import (
    ToolInstallReceipt,
    load_tool_lock,
    verify_locked_install,
)
from pipeline.synthetic_village.visual_sources import (
    VISUAL_MANIFEST_NAME,
    canonical_manifest_bytes,
    load_visual_source_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
BUILD_REQUEST_SCHEMA = "nantai.synthetic-village.blender-build-request.v1"
BUILD_REPORT_SCHEMA = "nantai.synthetic-village.blender-build-report.v1"
RENDER_REQUEST_SCHEMA = "nantai.synthetic-village.render-frame-request.v1"
RENDER_FRAME_REPORT_SCHEMA = "nantai.synthetic-village.render-frame-report.v1"
RENDER_JOURNAL_SCHEMA = "nantai.synthetic-village.render-journal.v1"
CAMERA_METADATA_SCHEMA = "nantai.synthetic-village.camera-metadata.v1"
DEPTH_ENCODING = "euclidean-camera-center-range-m"
NORMAL_ENCODING = "world-space-unit-vector"
JOURNAL_REPLACE_ATTEMPTS = 3
WINDOWS_SHARING_VIOLATIONS = frozenset({5, 32, 33})

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
EvidenceId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$"),
]
SemanticClass = Literal[
    "background",
    "terrain",
    "support",
    "building",
    "bridge",
    "creek",
    "pond",
    "path",
    "field",
    "orchard",
    "bamboo",
    "courtyard",
    "retaining-wall",
    "prop",
]
SemanticScope = Literal["background", "auxiliary", "canonical-object"]
VisualUsageMode = Literal["design-reference-only", "procedural-placeholder-v1"]
VisualBuildStatus = Literal[
    "instantiated",
    "declared-not-instantiated",
]
VisualReferenceStatus = Literal["verified-design-reference", "no-reference"]
VisualImplementation = Literal[
    "composition-reference-v1",
    "pbr-material-v1",
    "geometry-detail-v1",
    "environment-element-v1",
    "prop-element-v1",
    "not-instantiated-v1",
]

MATERIAL_FAMILIES = (
    "bamboo-stem",
    "dark-timber",
    "fieldstone",
    "orchard-leaf",
    "packed-earth",
    "pale-plaster",
    "rammed-earth",
    "shallow-water",
    "terrace-soil",
    "weathered-timber",
    "wet-stone-paving",
)
PROP_SLOT_VARIANTS = (
    ("prop-water-jar-01", "water-jar"),
    ("prop-firewood-stack-01", "firewood-stack"),
    ("prop-bamboo-basket-01", "bamboo-basket"),
    ("prop-wooden-bench-01", "wooden-bench"),
    ("prop-farming-tools-01", "farming-tools"),
    ("prop-grain-rack-01", "grain-rack"),
    ("prop-stone-trough-01", "stone-trough"),
    ("prop-handcart-01", "handcart"),
)
PROP_VARIANTS = tuple(variant for _, variant in PROP_SLOT_VARIANTS)

KEY_VIEW_SLOT_IDS = (
    "key-view-establishing-small-01",
    "key-view-establishing-expanded-01",
    "key-view-creekside-entrance-01",
    "key-view-central-courtyard-01",
    "key-view-upper-switchback-01",
    "key-view-opposite-slope-01",
    "key-view-community-hall-01",
    "key-view-orchard-terrace-01",
    "key-view-bamboo-lane-01",
    "key-view-irrigation-pond-01",
    "key-view-lower-bridge-01",
    "key-view-upper-bridge-01",
    "key-view-south-ground-route-01",
    "key-view-east-ground-route-01",
    "key-view-field-edge-01",
    "key-view-roofline-crossing-01",
)
VISUAL_MATERIAL_SLOT_IDS = (
    "material-rammed-earth-01",
    "material-pale-plaster-01",
    "material-gray-roof-tile-01",
    "material-fieldstone-01",
    "material-dark-timber-01",
    "material-weathered-timber-01",
    "material-wet-stone-paving-01",
    "material-dry-stone-wall-01",
    "material-clay-brick-01",
    "material-moss-stone-01",
    "material-packed-earth-01",
    "material-terrace-soil-01",
    "material-rice-paddy-water-01",
    "material-vegetable-leaf-01",
    "material-bamboo-stem-01",
    "material-bamboo-leaf-01",
    "material-broadleaf-bark-01",
    "material-broadleaf-canopy-01",
    "material-orchard-bark-01",
    "material-orchard-leaf-01",
    "material-creek-rock-01",
    "material-shallow-water-01",
    "material-aged-metal-01",
    "material-woven-bamboo-01",
)
DETAIL_SLOT_COMPONENTS = {
    "detail-timber-door-01": "timber-door",
    "detail-timber-window-01": "two-latticed-windows",
    "detail-tile-eave-01": "tiled-gabled-roof-ridge-eaves",
    "detail-roof-ridge-01": "tiled-gabled-roof-ridge-eaves",
    "detail-stone-stair-01": None,
    "detail-drainage-channel-01": None,
    "detail-retaining-corner-01": None,
    "detail-timber-balcony-01": None,
    "detail-plaster-repair-01": None,
    "detail-rammed-layer-01": None,
    "detail-courtyard-joint-01": "paving-joints",
    "detail-bridge-parapet-01": "stone-deck-parapets-piers",
}
ENVIRONMENT_SLOT_COMPONENTS = {
    "environment-stone-bridge-01": "stone-deck-parapets-piers",
    "environment-creek-bend-01": "terrain-conform-ribbon",
    "environment-irrigation-pond-01": "terrain-conform-surface",
    "environment-terrace-field-01": "terrace-field-surfaces",
    "environment-orchard-slope-01": "orchard-trunks-canopies",
    "environment-bamboo-grove-01": "bamboo-stems-leaves",
    "environment-forest-mountain-01": "upper-slope-forest",
    "environment-overcast-sky-01": "overcast-world-background",
}
KEY_VIEW_PREVIEW_ARTIFACTS = {
    "key-view-creekside-entrance-01": "preview-bridge.png",
    "key-view-central-courtyard-01": "preview-central.png",
    "key-view-upper-switchback-01": "preview-upper.png",
    "key-view-opposite-slope-01": "preview-outer.png",
}
MAX_BUILD_REPORT_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024
MAX_PROCESS_LOG_BYTES = 1024 * 1024
DEFAULT_BUILD_TIMEOUT_SECONDS = 30 * 60
DEFAULT_RENDER_TIMEOUT_SECONDS = 15 * 60
MAX_RENDER_METADATA_BYTES = 16 * 1024 * 1024

RENDER_CAMERA_IDS = tuple(
    [*(f"camera-outer-{index:03d}" for index in range(1, 9))]
    + [*(f"camera-ground-{index:03d}" for index in range(1, 9))]
    + [*(f"camera-courtyard-{index:03d}" for index in range(1, 5))]
    + [*(f"camera-bridge-{index:03d}" for index in range(1, 5))]
)


class CanaryBuildError(RuntimeError):
    """Stable public failure for canary preparation, execution, or verification."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceHashes(FrozenModel):
    default_recipe_sha256: Sha256
    visual_catalog_sha256: Sha256
    visual_source_manifest_sha256: Sha256
    scene_plan_sha256: Sha256
    camera_plan_sha256: Sha256
    tool_lock_sha256: Sha256
    builder_script_sha256: Sha256


class ToolIdentity(FrozenModel):
    tool_id: Literal["blender"]
    version: Literal["4.5.11"]
    platform: Literal["windows-x64"]
    archive_sha256: Sha256
    executable_sha256: Sha256
    runtime_build_hash: Literal["4db51e9d1e1e"]
    runtime_output_sha256: Sha256
    engine: Literal["BLENDER_EEVEE_NEXT"] = "BLENDER_EEVEE_NEXT"
    view_transform: Literal["AgX"] = "AgX"


class SemanticRegistryEntry(FrozenModel):
    semantic_class: SemanticClass
    semantic_id: int = Field(ge=0, le=255)
    scope: SemanticScope


class MaterialRegistryEntry(FrozenModel):
    material_family: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    material_id: int = Field(ge=1, le=255)


class ObjectRegistryEntry(FrozenModel):
    object_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    instance_id: int = Field(ge=1, le=65535)
    semantic_id: int = Field(ge=3, le=255)
    material_id: int = Field(ge=1, le=255)
    variant_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )


class AuxiliaryRegistryEntry(FrozenModel):
    auxiliary_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    blender_name: str = Field(pattern=r"^(?:World|nv__[a-z0-9]+(?:-[a-z0-9]+)*)$")
    semantic_id: int = Field(ge=0, le=2)
    kind: Literal["world", "mesh"]


AUXILIARY_REGISTRY = (
    AuxiliaryRegistryEntry(
        auxiliary_id="background-world",
        blender_name="World",
        semantic_id=0,
        kind="world",
    ),
    AuxiliaryRegistryEntry(
        auxiliary_id="aux-terrain",
        blender_name="nv__aux-terrain",
        semantic_id=1,
        kind="mesh",
    ),
    AuxiliaryRegistryEntry(
        auxiliary_id="aux-support-terrain-skirt",
        blender_name="nv__aux-support-terrain-skirt",
        semantic_id=2,
        kind="mesh",
    ),
)


class VisualSlotRegistryEntry(FrozenModel):
    slot_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    category: SlotCategory
    usage_mode: VisualUsageMode
    source_sha256: Sha256 | None
    reference_status: VisualReferenceStatus
    canary_critical: bool
    build_status: VisualBuildStatus
    implementation: VisualImplementation
    component_tag: str | None = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    evidence_ids: tuple[EvidenceId, ...]

    @model_validator(mode="after")
    def _validate_provenance_and_build_usage(self) -> VisualSlotRegistryEntry:
        if self.usage_mode == "design-reference-only" and self.source_sha256 is None:
            raise ValueError("design-reference-only slots require a verified source SHA-256")
        if self.usage_mode == "procedural-placeholder-v1" and self.source_sha256 is not None:
            raise ValueError("procedural-placeholder-v1 slots must not claim a source SHA-256")
        expected_reference = (
            "verified-design-reference"
            if self.usage_mode == "design-reference-only"
            else "no-reference"
        )
        if self.reference_status != expected_reference:
            raise ValueError("visual reference status must match verified source provenance")
        if self.build_status == "declared-not-instantiated" and (
            self.implementation != "not-instantiated-v1"
        ):
            raise ValueError("declared slots require the not-instantiated-v1 implementation")
        if self.build_status == "declared-not-instantiated" and (
            self.component_tag is not None or self.evidence_ids
        ):
            raise ValueError("declared slots must not claim component evidence")
        implementation_by_category = {
            "key-view": "composition-reference-v1",
            "material": "pbr-material-v1",
            "detail": "geometry-detail-v1",
            "environment": "environment-element-v1",
            "prop": "prop-element-v1",
        }
        if self.build_status == "instantiated" and (
            self.implementation != implementation_by_category[self.category]
        ):
            raise ValueError("instantiated slot implementation must match its category")
        if self.build_status == "instantiated" and (
            self.component_tag is None or not self.evidence_ids
        ):
            raise ValueError("instantiated slots require component evidence")
        if self.evidence_ids != tuple(sorted(set(self.evidence_ids))):
            raise ValueError("visual slot evidence IDs must be unique and sorted")
        if self.category == "material" and (
            self.build_status != "instantiated" or self.implementation != "pbr-material-v1"
        ):
            raise ValueError("all visual material slots require instantiated PBR records")
        if (
            self.canary_critical
            and self.build_status == "declared-not-instantiated"
            and self.reference_status != "verified-design-reference"
        ):
            raise ValueError("canary-critical slots require a build or verified design reference")
        return self


class ArtifactRequest(FrozenModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*\.(?:blend|glb|png)$")
    kind: Literal["blender-scene", "gltf-binary", "rgb-preview"]


ARTIFACT_REQUESTS = (
    ArtifactRequest(name="preview-bridge.png", kind="rgb-preview"),
    ArtifactRequest(name="preview-central.png", kind="rgb-preview"),
    ArtifactRequest(name="preview-outer.png", kind="rgb-preview"),
    ArtifactRequest(name="preview-upper.png", kind="rgb-preview"),
    ArtifactRequest(name="village-canary.blend", kind="blender-scene"),
    ArtifactRequest(name="village-canary.glb", kind="gltf-binary"),
)


class BuildRequest(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.blender-build-request.v1"] = (
        BUILD_REQUEST_SCHEMA
    )
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L2"] = "L2"
    scene_plan: ScenePlan
    camera_plan: CameraPlan
    source_hashes: SourceHashes
    tool_identity: ToolIdentity
    object_registry: tuple[ObjectRegistryEntry, ...] = Field(min_length=126, max_length=126)
    auxiliary_registry: tuple[AuxiliaryRegistryEntry, ...] = Field(
        min_length=3,
        max_length=3,
    )
    semantic_registry: tuple[SemanticRegistryEntry, ...] = Field(min_length=14, max_length=14)
    material_registry: tuple[MaterialRegistryEntry, ...] = Field(min_length=11, max_length=11)
    visual_slot_registry: tuple[VisualSlotRegistryEntry, ...] = Field(
        min_length=68,
        max_length=68,
    )
    requested_artifacts: tuple[ArtifactRequest, ...] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _validate_registry_and_content_addresses(self) -> BuildRequest:
        expected_scene_sha = hashlib.sha256(
            canonical_scene_plan_bytes(self.scene_plan),
        ).hexdigest()
        expected_camera_sha = hashlib.sha256(
            canonical_camera_plan_bytes(self.camera_plan),
        ).hexdigest()
        if self.source_hashes.scene_plan_sha256 != expected_scene_sha:
            raise ValueError("scene plan SHA-256 is not canonical")
        if self.source_hashes.camera_plan_sha256 != expected_camera_sha:
            raise ValueError("camera plan SHA-256 is not canonical")
        expected_semantics = _semantic_registry()
        if self.semantic_registry != expected_semantics:
            raise ValueError("semantic registry is not the stable v1 taxonomy")
        expected_materials = _material_registry(self.scene_plan)
        if self.material_registry != expected_materials:
            raise ValueError("material registry is not the stable v1 mapping")
        if self.object_registry != _object_registry(
            self.scene_plan,
            expected_semantics,
            expected_materials,
        ):
            raise ValueError("object registry does not match the canonical scene")
        if self.auxiliary_registry != AUXILIARY_REGISTRY:
            raise ValueError("auxiliary registry is not the stable v1 mapping")
        if self.requested_artifacts != ARTIFACT_REQUESTS:
            raise ValueError("requested artifact registry is not the exact v1 set")
        slot_ids = tuple(item.slot_id for item in self.visual_slot_registry)
        expected_categories = _expected_visual_slot_categories()
        if slot_ids != tuple(sorted(expected_categories)):
            raise ValueError("visual slot registry must be the exact sorted v1 taxonomy")
        implementation_by_category = {
            "key-view": "composition-reference-v1",
            "material": "pbr-material-v1",
            "detail": "geometry-detail-v1",
            "environment": "environment-element-v1",
            "prop": "prop-element-v1",
        }
        for item in self.visual_slot_registry:
            if item.category != expected_categories[item.slot_id]:
                raise ValueError("visual slot category does not match the stable v1 taxonomy")
            component_tag, evidence_ids = _visual_slot_build_evidence(
                item.slot_id,
                self.scene_plan,
            )
            if component_tag is not None and not evidence_ids:
                raise ValueError("visual slot component has no canonical scene evidence")
            expected_status = "instantiated" if evidence_ids else "declared-not-instantiated"
            expected_implementation = (
                implementation_by_category[item.category] if evidence_ids else "not-instantiated-v1"
            )
            if (
                item.component_tag != component_tag
                or item.evidence_ids != evidence_ids
                or item.build_status != expected_status
                or item.implementation != expected_implementation
            ):
                raise ValueError("visual slot build evidence does not match the canonical scene")
        material_slots = [item for item in self.visual_slot_registry if item.category == "material"]
        if len(material_slots) != 24:
            raise ValueError("all 24 visual material slots require build records")
        expected_build_id = hashlib.sha256(
            canonical_build_request_bytes(self, exclude_build_id=True),
        ).hexdigest()
        if self.build_id != expected_build_id:
            raise ValueError("build_id does not match the canonical request inputs")
        return self


class CameraRegistryEntry(FrozenModel):
    camera_id: str = Field(pattern=r"^camera-(?:outer|ground|courtyard|bridge)-\d{3}$")
    blender_camera_name: str = Field(
        pattern=r"^nv__camera-(?:outer|ground|courtyard|bridge)-\d{3}$",
    )
    requested_c2w_blender: Matrix4
    measured_c2w_blender: Matrix4
    max_translation_error_m: float = Field(ge=0, allow_inf_nan=False)
    max_rotation_entry_error: float = Field(ge=0, allow_inf_nan=False)
    translation_error_limit_m: Literal[0.00004] = 0.00004
    rotation_entry_error_limit: Literal[0.00000032] = 0.00000032

    @model_validator(mode="after")
    def _validate_measured_matrix(self) -> CameraRegistryEntry:
        if self.blender_camera_name != f"nv__{self.camera_id}":
            raise ValueError("Blender camera name must derive from the canonical camera ID")
        values = (
            *self.requested_c2w_blender,
            *self.measured_c2w_blender,
        )
        if not all(math.isfinite(value) for row in values for value in row):
            raise ValueError("camera registry matrices must be finite")
        rotation_errors = []
        translation_errors = []
        for row in range(4):
            for column in range(4):
                requested = self.requested_c2w_blender[row][column]
                measured = self.measured_c2w_blender[row][column]
                delta = abs(measured - requested)
                if row < 3 and column < 3:
                    allowed = self.rotation_entry_error_limit
                elif row < 3 and column == 3:
                    allowed = max(5e-8, abs(requested) * 1.2e-7)
                else:
                    allowed = 5e-8
                if delta > allowed + 1e-12:
                    raise ValueError("measured camera matrix exceeds float32 scale tolerance")
                if row < 3 and column < 3:
                    rotation_errors.append(delta)
                elif row < 3 and column == 3:
                    translation_errors.append(delta)
        maximum_translation = max(translation_errors)
        maximum_rotation = max(rotation_errors)
        if maximum_translation > self.translation_error_limit_m + 1e-12:
            raise ValueError("measured camera translation exceeds its global tolerance")
        if maximum_rotation > self.rotation_entry_error_limit + 1e-12:
            raise ValueError("measured camera rotation exceeds its global tolerance")
        if self.max_translation_error_m != round(maximum_translation, 12):
            raise ValueError("reported maximum translation error was not measured")
        if self.max_rotation_entry_error != round(maximum_rotation, 12):
            raise ValueError("reported maximum rotation entry error was not measured")
        measured = self.measured_c2w_blender
        for left in range(3):
            for right in range(3):
                dot = sum(measured[row][left] * measured[row][right] for row in range(3))
                target = 1.0 if left == right else 0.0
                if abs(dot - target) > 1e-6:
                    raise ValueError("measured camera rotation is not rigid")
        determinant = (
            measured[0][0] * (measured[1][1] * measured[2][2] - measured[1][2] * measured[2][1])
            - measured[0][1] * (measured[1][0] * measured[2][2] - measured[1][2] * measured[2][0])
            + measured[0][2] * (measured[1][0] * measured[2][1] - measured[1][1] * measured[2][0])
        )
        if determinant <= 0.0 or abs(determinant - 1.0) > 1e-6:
            raise ValueError("measured camera rotation must remain rigid and right-handed")
        return self


class PreviewCameraRecord(FrozenModel):
    artifact_name: Literal[
        "preview-bridge.png",
        "preview-central.png",
        "preview-outer.png",
        "preview-upper.png",
    ]
    blender_camera_name: Literal["nv__preview-camera-temporary"]
    eye_xyz: tuple[float, float, float]
    target_xyz: tuple[float, float, float]
    lens_mm: float = Field(ge=15.0, le=100.0, allow_inf_nan=False)
    clip_start_m: Literal[1.0]
    clip_end_m: Literal[2000.0]
    image_width_px: Literal[1024]
    image_height_px: Literal[576]

    @model_validator(mode="after")
    def _validate_preview_view(self) -> PreviewCameraRecord:
        if not all(math.isfinite(value) for value in (*self.eye_xyz, *self.target_xyz)):
            raise ValueError("preview camera coordinates must be finite")
        distance = math.sqrt(
            sum(
                (eye - target) ** 2
                for eye, target in zip(self.eye_xyz, self.target_xyz, strict=True)
            )
        )
        if distance < 1.0:
            raise ValueError("preview camera eye and target are degenerate")
        if any(abs(value) > 2000.0 for value in (*self.eye_xyz, *self.target_xyz)):
            raise ValueError("preview camera coordinates exceed the bounded scene envelope")
        return self


class ArtifactRecord(FrozenModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*\.(?:blend|glb|png)$")
    kind: Literal["blender-scene", "gltf-binary", "rgb-preview"]
    sha256: Sha256
    size_bytes: int = Field(gt=0, le=MAX_ARTIFACT_BYTES)


class BuildCounts(FrozenModel):
    canonical_roots: Literal[126]
    mesh_objects: int = Field(ge=126)
    scene_material_families: Literal[11]
    visual_materials: Literal[24]
    cameras: Literal[24]
    lights: int = Field(ge=1)
    auxiliary_semantic_objects: Literal[2]


class PropTypeCounts(FrozenModel):
    water_jar: Literal[2] = Field(alias="water-jar")
    firewood_stack: Literal[2] = Field(alias="firewood-stack")
    bamboo_basket: Literal[2] = Field(alias="bamboo-basket")
    wooden_bench: Literal[2] = Field(alias="wooden-bench")
    farming_tools: Literal[2] = Field(alias="farming-tools")
    grain_rack: Literal[2] = Field(alias="grain-rack")
    stone_trough: Literal[2] = Field(alias="stone-trough")
    handcart: Literal[2]


class BuildValidation(FrozenModel):
    canonical_object_ids_match: Literal[True]
    camera_matrices_within_tolerance: Literal[True]
    finite_nonempty_meshes: Literal[True]
    semantic_ids_unique: Literal[True]
    material_ids_unique: Literal[True]
    auxiliary_semantics_present: Literal[True]
    all_visual_material_slots_built: Literal[True]
    canary_critical_slots_fulfilled: Literal[True]
    prop_type_counts: PropTypeCounts


class BuildDeterminism(FrozenModel):
    request_bytes: Literal["canonical-json-v1"]
    scene_plan_bytes: Literal["canonical-json-v1"]
    camera_plan_bytes: Literal["canonical-json-v1"]
    blend_bytes: Literal["measured-not-guaranteed"]
    glb_bytes: Literal["measured-not-guaranteed"]
    preview_bytes: Literal["measured-not-guaranteed"]


class BuildReport(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.blender-build-report.v1"] = (
        BUILD_REPORT_SCHEMA
    )
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L2"] = "L2"
    fidelity: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
    tool_identity: ToolIdentity
    source_hashes: SourceHashes
    object_registry: tuple[ObjectRegistryEntry, ...] = Field(min_length=126, max_length=126)
    auxiliary_registry: tuple[AuxiliaryRegistryEntry, ...] = Field(
        min_length=3,
        max_length=3,
    )
    semantic_registry: tuple[SemanticRegistryEntry, ...] = Field(min_length=14, max_length=14)
    material_registry: tuple[MaterialRegistryEntry, ...] = Field(min_length=11, max_length=11)
    visual_slot_registry: tuple[VisualSlotRegistryEntry, ...] = Field(
        min_length=68,
        max_length=68,
    )
    camera_registry: tuple[CameraRegistryEntry, ...] = Field(min_length=24, max_length=24)
    preview_registry: tuple[PreviewCameraRecord, ...] = Field(min_length=4, max_length=4)
    counts: BuildCounts
    validation: BuildValidation
    determinism: BuildDeterminism
    artifacts: tuple[ArtifactRecord, ...] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _validate_complete_report(self) -> BuildReport:
        if self.semantic_registry != _semantic_registry():
            raise ValueError("report semantic registry is not the stable v1 taxonomy")
        if self.auxiliary_registry != AUXILIARY_REGISTRY:
            raise ValueError("report auxiliary registry is not the stable v1 mapping")
        expected_materials = tuple(
            MaterialRegistryEntry(material_family=family, material_id=index)
            for index, family in enumerate(MATERIAL_FAMILIES, start=1)
        )
        if self.material_registry != expected_materials:
            raise ValueError("report material registry is not the stable v1 mapping")
        instance_ids = tuple(item.instance_id for item in self.object_registry)
        object_ids = tuple(item.object_id for item in self.object_registry)
        if instance_ids != tuple(range(1, 127)) or len(set(object_ids)) != 126:
            raise ValueError("report object registry must contain 126 stable instances")
        if any(item.variant_id != _prop_variant(item.object_id) for item in self.object_registry):
            raise ValueError("report prop variants do not match the stable v1 mapping")
        slot_ids = tuple(item.slot_id for item in self.visual_slot_registry)
        if slot_ids != tuple(sorted(slot_ids)) or len(set(slot_ids)) != 68:
            raise ValueError("report visual slot registry is incomplete or unstable")
        if sum(item.category == "material" for item in self.visual_slot_registry) != 24:
            raise ValueError("report must contain all 24 visual material build records")
        camera_ids = tuple(item.camera_id for item in self.camera_registry)
        if len(set(camera_ids)) != 24:
            raise ValueError("report camera registry IDs must be unique")
        expected_previews = (
            "preview-bridge.png",
            "preview-central.png",
            "preview-outer.png",
            "preview-upper.png",
        )
        if tuple(item.artifact_name for item in self.preview_registry) != expected_previews:
            raise ValueError("report preview registry is not the stable sorted v1 set")
        artifact_contract = tuple((item.name, item.kind) for item in ARTIFACT_REQUESTS)
        if tuple((item.name, item.kind) for item in self.artifacts) != artifact_contract:
            raise ValueError("report artifact registry is not the exact sorted v1 set")
        return self


class RenderSettings(FrozenModel):
    engine: Literal["BLENDER_EEVEE_NEXT"] = "BLENDER_EEVEE_NEXT"
    data_engine: Literal["CYCLES"] = "CYCLES"
    image_width_px: Literal[1024] = 1024
    image_height_px: Literal[576] = 576
    render_samples: Literal[64] = 64
    rgb_render_threads: Literal[1] = 1
    data_render_samples: Literal[1] = 1
    deterministic_seed: Literal[20260715] = 20260715
    view_transform: Literal["AgX"] = "AgX"
    look: Literal["AgX - Medium High Contrast"] = "AgX - Medium High Contrast"
    exposure: Literal[0.0] = 0.0
    gamma: Literal[1.0] = 1.0
    dither_intensity: Literal[0.0] = 0.0
    depth_of_field: Literal["disabled-deep-focus"] = "disabled-deep-focus"
    motion_blur: Literal[False] = False
    depth_encoding: Literal["euclidean-camera-center-range-m"] = DEPTH_ENCODING
    normal_encoding: Literal["world-space-unit-vector"] = NORMAL_ENCODING
    instance_encoding: Literal["uint16-png-direct-id"] = "uint16-png-direct-id"
    semantic_encoding: Literal["uint8-png-direct-id"] = "uint8-png-direct-id"
    depth_channel_layout: Literal["V-float32-zip"] = "V-float32-zip"
    normal_channel_layout: Literal["X,Y,Z-float32-zip"] = "X,Y,Z-float32-zip"
    instance_pixel_type: Literal["uint16-grayscale-png"] = "uint16-grayscale-png"
    semantic_pixel_type: Literal["uint8-grayscale-png"] = "uint8-grayscale-png"


class RenderFrameRequest(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.render-frame-request.v1"] = (
        RENDER_REQUEST_SCHEMA
    )
    render_id: Sha256
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L2"] = "L2"
    fidelity: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
    blender_executable_sha256: Sha256
    renderer_script_sha256: Sha256
    blend_sha256: Sha256
    build_report_sha256: Sha256
    object_registry_sha256: Sha256
    settings: RenderSettings
    camera: CameraPose
    measured_c2w_blender: Matrix4
    object_registry: tuple[ObjectRegistryEntry, ...] = Field(min_length=126, max_length=126)
    auxiliary_registry: tuple[AuxiliaryRegistryEntry, ...] = Field(min_length=3, max_length=3)
    semantic_registry: tuple[SemanticRegistryEntry, ...] = Field(min_length=14, max_length=14)

    @model_validator(mode="after")
    def _validate_render_request(self) -> RenderFrameRequest:
        if self.camera.camera_id not in RENDER_CAMERA_IDS:
            raise ValueError("render request camera is not canonical")
        if tuple(item.instance_id for item in self.object_registry) != tuple(range(1, 127)):
            raise ValueError("render request instance registry is not stable v1")
        if self.semantic_registry != _semantic_registry():
            raise ValueError("render request semantic registry is not stable v1")
        expected_registry_sha = hashlib.sha256(
            _canonical_json_bytes(
                [item.model_dump(mode="json") for item in self.object_registry],
            ),
        ).hexdigest()
        if self.object_registry_sha256 != expected_registry_sha:
            raise ValueError("render request object registry digest is invalid")
        return self


RenderArtifactKind = Literal[
    "rgb",
    "depth",
    "normal",
    "instance-mask",
    "semantic-mask",
    "camera-metadata",
]


class RenderArtifactRecord(FrozenModel):
    kind: RenderArtifactKind
    path: str = Field(
        pattern=(
            r"^(?:rgb|depth|normal|instance|semantic|cameras)/"
            r"camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}\."
            r"(?:png|exr|json)$"
        ),
    )
    sha256: Sha256
    size_bytes: int = Field(gt=0, le=MAX_ARTIFACT_BYTES)


class RenderStatistics(FrozenModel):
    depth_min_m: float = Field(ge=0.0, le=1200.0, allow_inf_nan=False)
    depth_max_m: float = Field(gt=0.0, le=2000.0, allow_inf_nan=False)
    depth_background_pixels: int = Field(ge=0, le=1024 * 576)
    depth_max_range_error_m: float = Field(ge=0.0, le=0.01, allow_inf_nan=False)
    normal_max_unit_error: float = Field(ge=0.0, le=0.001, allow_inf_nan=False)
    instance_ids: tuple[int, ...] = Field(min_length=1)
    semantic_ids: tuple[int, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_ids(self) -> RenderStatistics:
        if self.instance_ids != tuple(sorted(set(self.instance_ids))) or any(
            value < 0 or value > 126 for value in self.instance_ids
        ):
            raise ValueError("observed instance IDs must be unique stable IDs from 0 through 126")
        if self.semantic_ids != tuple(sorted(set(self.semantic_ids))) or any(
            value < 0 or value > 13 for value in self.semantic_ids
        ):
            raise ValueError("observed semantic IDs must be unique stable IDs from 0 through 13")
        if self.depth_max_m < self.depth_min_m:
            raise ValueError("depth statistics are inverted")
        return self


class RenderValidation(FrozenModel):
    dimensions_match: Literal[True]
    depth_finite_nonnegative: Literal[True]
    depth_camera_range_consistent: Literal[True]
    normal_finite_unit_world_space: Literal[True]
    instance_ids_registered: Literal[True]
    semantic_ids_registered: Literal[True]
    camera_metadata_matches: Literal[True]


class RenderFrameReport(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.render-frame-report.v1"] = (
        RENDER_FRAME_REPORT_SCHEMA
    )
    build_id: Sha256
    render_id: Sha256
    content_sha256: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L2"] = "L2"
    fidelity: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
    blender_executable_sha256: Sha256
    camera_id: str = Field(pattern=r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")
    image_width_px: Literal[1024]
    image_height_px: Literal[576]
    depth_encoding: Literal["euclidean-camera-center-range-m"]
    normal_encoding: Literal["world-space-unit-vector"]
    depth_channel_layout: Literal["V-float32-zip"]
    normal_channel_layout: Literal["X,Y,Z-float32-zip"]
    instance_pixel_type: Literal["uint16-grayscale-png"]
    semantic_pixel_type: Literal["uint8-grayscale-png"]
    settings_sha256: Sha256
    artifacts: tuple[RenderArtifactRecord, ...] = Field(min_length=6, max_length=6)
    statistics: RenderStatistics
    validation: RenderValidation

    @model_validator(mode="after")
    def _validate_artifact_contract(self) -> RenderFrameReport:
        if tuple((item.kind, item.path) for item in self.artifacts) != tuple(
            _expected_render_artifacts(self.camera_id)
        ):
            raise ValueError("render frame artifacts are not the exact six-file contract")
        return self


class CameraFrameMetadata(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.camera-metadata.v1"] = CAMERA_METADATA_SCHEMA
    build_id: Sha256
    render_id: Sha256
    synthetic: Literal[True]
    verification_level: Literal["L2"]
    blender_executable_sha256: Sha256
    camera_id: str = Field(pattern=r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")
    category: Literal["outer", "ground", "courtyard", "bridge"]
    split: Literal["train", "val", "test"]
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
    settings_sha256: Sha256
    intrinsics: dict[str, int | float]
    requested_c2w_opencv: Matrix4
    requested_c2w_blender: Matrix4
    measured_c2w_opencv: Matrix4
    measured_c2w_blender: Matrix4
    object_registry_sha256: Sha256
    semantic_registry: tuple[SemanticRegistryEntry, ...] = Field(min_length=14, max_length=14)


class RenderFailure(FrozenModel):
    stage: Literal["prepare", "invoke", "validate", "publish"]
    message: str = Field(min_length=1, max_length=512)


class RenderFrameRecord(FrozenModel):
    camera_id: str = Field(pattern=r"^camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}$")
    state: Literal["planned", "rendering", "verified", "failed"]
    artifacts: tuple[RenderArtifactRecord, ...] = Field(default=(), max_length=6)
    runtime_report_sha256: Sha256 | None = None
    statistics: RenderStatistics | None = None
    error: RenderFailure | None = None

    @model_validator(mode="after")
    def _validate_state_payload(self) -> RenderFrameRecord:
        if self.state == "verified":
            if (
                len(self.artifacts) != 6
                or self.runtime_report_sha256 is None
                or self.statistics is None
            ):
                raise ValueError("verified frame requires six artifacts and runtime evidence")
            if self.error is not None:
                raise ValueError("verified frame cannot retain a failure")
        elif self.state == "failed":
            if (
                self.artifacts
                or self.runtime_report_sha256 is not None
                or self.statistics is not None
            ):
                raise ValueError("failed frame cannot publish verified artifacts")
            if self.error is None:
                raise ValueError("failed frame requires an explicit stage and error")
        elif (
            self.artifacts
            or self.runtime_report_sha256 is not None
            or self.statistics is not None
            or self.error is not None
        ):
            raise ValueError("planned/rendering frame cannot claim output evidence")
        return self


class RenderJournal(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.render-journal.v1"] = RENDER_JOURNAL_SCHEMA
    render_id: Sha256
    journal_sha256: Sha256
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L2"] = "L2"
    fidelity: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
    blender_executable_sha256: Sha256
    renderer_script_sha256: Sha256
    blend_sha256: Sha256
    build_report_sha256: Sha256
    object_registry_sha256: Sha256
    settings: RenderSettings
    frames: tuple[RenderFrameRecord, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def _validate_frame_order(self) -> RenderJournal:
        if tuple(item.camera_id for item in self.frames) != RENDER_CAMERA_IDS:
            raise ValueError("render journal camera registry is not stable v1")
        return self


def _canonical_json_bytes(payload: object) -> bytes:
    text = json.dumps(
        payload,
        default=lambda value: value.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


def canonical_build_request_bytes(
    request: BuildRequest,
    *,
    exclude_build_id: bool = False,
) -> bytes:
    exclude = {"build_id"} if exclude_build_id else None
    return _canonical_json_bytes(request.model_dump(mode="json", exclude=exclude))


def canonical_build_report_bytes(report: BuildReport) -> bytes:
    return _canonical_json_bytes(report.model_dump(mode="json", by_alias=True))


def canonical_render_request_bytes(request: RenderFrameRequest) -> bytes:
    return _canonical_json_bytes(request.model_dump(mode="json"))


def canonical_render_frame_report_bytes(
    report: RenderFrameReport,
    *,
    exclude_sha256: bool = False,
) -> bytes:
    exclude = {"content_sha256"} if exclude_sha256 else None
    return _canonical_json_bytes(report.model_dump(mode="json", exclude=exclude))


def canonical_camera_metadata_bytes(metadata: CameraFrameMetadata) -> bytes:
    return _canonical_json_bytes(metadata.model_dump(mode="json"))


def canonical_render_journal_bytes(
    journal: RenderJournal,
    *,
    exclude_sha256: bool = False,
) -> bytes:
    exclude = {"journal_sha256"} if exclude_sha256 else None
    return _canonical_json_bytes(journal.model_dump(mode="json", exclude=exclude))


def _expected_render_artifacts(camera_id: str) -> tuple[tuple[str, str], ...]:
    return (
        ("rgb", f"rgb/{camera_id}.png"),
        ("depth", f"depth/{camera_id}.exr"),
        ("normal", f"normal/{camera_id}.exr"),
        ("instance-mask", f"instance/{camera_id}.png"),
        ("semantic-mask", f"semantic/{camera_id}.png"),
        ("camera-metadata", f"cameras/{camera_id}.json"),
    )


def _axial_depth_to_euclidean_range_m(
    axial_depth_m: float,
    *,
    u_px: float,
    v_px: float,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
) -> float:
    """Convert Blender's linear forward-axis Z pass to camera-center range."""

    values = (axial_depth_m, u_px, v_px, fx, fy, cx, cy)
    if not all(math.isfinite(value) for value in values) or axial_depth_m < 0 or fx <= 0 or fy <= 0:
        raise CanaryBuildError("depth conversion inputs must be finite with positive focal lengths")
    x = (u_px - cx) / fx
    y = (v_px - cy) / fy
    return axial_depth_m * math.sqrt(1.0 + x * x + y * y)


def _blender_c2w_to_opencv(matrix: Matrix4) -> Matrix4:
    converted = []
    for row in matrix:
        converted_row = []
        for column, component in enumerate(row):
            value = -component if column in {1, 2} else component
            converted_row.append(0.0 if value == 0.0 else value)
        converted.append(tuple(converted_row))
    return tuple(converted)  # type: ignore[return-value]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CanaryBuildError(f"build report contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
    )


def _require_real_directory(path: Path, *, label: str) -> Path:
    path = Path(path).absolute()
    if _is_linklike(path):
        raise CanaryBuildError(f"{label} must not be a symlink or junction")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CanaryBuildError(f"{label} is not a real directory") from exc
    if not path.is_dir() or not _same_path(path, resolved):
        raise CanaryBuildError(f"{label} has a redirected ancestor")
    return path


def _contains_private_path(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_private_path(key) or _contains_private_path(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_path(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:/", normalized)
        or ".nantai-studio" in normalized.casefold()
    ):
        return True
    usernames = {
        candidate.casefold()
        for candidate in (
            os.environ.get("USERNAME"),
            os.environ.get("USER"),
            Path.home().name,
        )
        if candidate and len(candidate) >= 3
    }
    tokens = {token.casefold() for token in re.split(r"[^A-Za-z0-9_.-]+", value) if token}
    return bool(usernames & tokens)


def load_build_report(path: Path) -> BuildReport:
    """Load a bounded, canonical report through a stable, non-redirected path."""

    path = Path(path).absolute()
    try:
        parent = _require_real_directory(path.parent, label="build report directory")
        if _is_linklike(path) or path.parent != parent:
            raise CanaryBuildError("build report path has a redirected leaf or parent")
        before = path.stat()
        if before.st_size <= 0 or before.st_size > MAX_BUILD_REPORT_BYTES:
            raise CanaryBuildError("build report size is invalid")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_signature(before) != _stat_signature(opened):
                raise CanaryBuildError("build report changed before bounded read")
            raw = stream.read(MAX_BUILD_REPORT_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_BUILD_REPORT_BYTES
            or _stat_signature(opened) != _stat_signature(after_open)
            or _stat_signature(before) != _stat_signature(after)
        ):
            raise CanaryBuildError("build report changed during bounded read")
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        if _contains_private_path(parsed):
            raise CanaryBuildError("build report contains a private path or username")
        report = BuildReport.model_validate_json(raw)
        if raw != canonical_build_report_bytes(report):
            raise CanaryBuildError("build report must be canonical JSON")
        return report
    except CanaryBuildError:
        raise
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise CanaryBuildError(f"build report validation failed: {exc}") from exc


def _sha256_stable_artifact(path: Path) -> tuple[str, int]:
    if _is_linklike(path) or not path.is_file():
        raise CanaryBuildError(f"artifact is missing or redirected: {path.name}")
    before = path.stat()
    if before.st_size <= 0 or before.st_size > MAX_ARTIFACT_BYTES:
        raise CanaryBuildError(f"artifact size is invalid: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _stat_signature(before) != _stat_signature(opened):
            raise CanaryBuildError(f"artifact changed before hashing: {path.name}")
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
        after_open = os.fstat(stream.fileno())
    after = path.stat()
    if _stat_signature(opened) != _stat_signature(after_open) or _stat_signature(
        before
    ) != _stat_signature(after):
        raise CanaryBuildError(f"artifact changed while hashing: {path.name}")
    return digest.hexdigest(), before.st_size


def verify_build_report(
    report: BuildReport,
    *,
    request: BuildRequest,
    staging: Path,
) -> None:
    """Verify a runtime report against the exact request and staged bytes."""

    staging = _require_real_directory(staging, label="build staging directory")
    if report.build_id != request.build_id:
        raise CanaryBuildError("build report build_id does not match its request")
    if report.tool_identity != request.tool_identity:
        raise CanaryBuildError("build report tool identity does not match its request")
    if report.source_hashes != request.source_hashes:
        raise CanaryBuildError("build report source hashes do not match its request")
    for label in (
        "object_registry",
        "auxiliary_registry",
        "semantic_registry",
        "material_registry",
        "visual_slot_registry",
    ):
        if getattr(report, label) != getattr(request, label):
            raise CanaryBuildError(f"build report {label.replace('_', ' ')} was tampered")
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
            raise CanaryBuildError("build report requested camera registry was tampered")
    for artifact in report.artifacts:
        artifact_path = staging / artifact.name
        if artifact_path.parent != staging:
            raise CanaryBuildError("artifact path escapes the build staging directory")
        digest, size = _sha256_stable_artifact(artifact_path)
        if digest != artifact.sha256 or size != artifact.size_bytes:
            raise CanaryBuildError(f"artifact digest or size mismatch: {artifact.name}")


def _semantic_registry() -> tuple[SemanticRegistryEntry, ...]:
    rows: list[SemanticRegistryEntry] = [
        SemanticRegistryEntry(
            semantic_class="background",
            semantic_id=0,
            scope="background",
        ),
        SemanticRegistryEntry(
            semantic_class="terrain",
            semantic_id=1,
            scope="auxiliary",
        ),
        SemanticRegistryEntry(
            semantic_class="support",
            semantic_id=2,
            scope="auxiliary",
        ),
    ]
    rows.extend(
        SemanticRegistryEntry(
            semantic_class=semantic_class,
            semantic_id=semantic_id,
            scope="canonical-object",
        )
        for semantic_id, semantic_class in enumerate(SEMANTIC_ORDER, start=3)
    )
    return tuple(rows)


def _material_registry(scene_plan: ScenePlan) -> tuple[MaterialRegistryEntry, ...]:
    actual = tuple(sorted({item.material_family for item in scene_plan.objects}))
    if actual != MATERIAL_FAMILIES:
        raise CanaryBuildError("scene material families are not the stable v1 set")
    return tuple(
        MaterialRegistryEntry(material_family=family, material_id=index)
        for index, family in enumerate(actual, start=1)
    )


def _prop_variant(object_id: str) -> str | None:
    if not object_id.startswith("prop-rural-"):
        return None
    index = int(object_id.rsplit("-", 1)[1])
    return PROP_VARIANTS[(index - 1) // 2]


def _expected_visual_slot_categories() -> dict[str, SlotCategory]:
    rows: dict[str, SlotCategory] = {}
    for category, slot_ids in (
        ("key-view", KEY_VIEW_SLOT_IDS),
        ("material", VISUAL_MATERIAL_SLOT_IDS),
        ("detail", tuple(DETAIL_SLOT_COMPONENTS)),
        ("environment", tuple(ENVIRONMENT_SLOT_COMPONENTS)),
        ("prop", tuple(slot_id for slot_id, _ in PROP_SLOT_VARIANTS)),
    ):
        rows.update(dict.fromkeys(slot_ids, category))
    if len(rows) != 68:
        raise CanaryBuildError("stable visual slot taxonomy must contain exactly 68 IDs")
    return rows


def _visual_slot_build_evidence(
    slot_id: str,
    scene_plan: ScenePlan,
) -> tuple[str | None, tuple[str, ...]]:
    """Derive an exact, path-free claim from the canonical scene and artifact contract."""

    by_semantic: dict[str, tuple[str, ...]] = {}
    for semantic in SEMANTIC_ORDER:
        by_semantic[semantic] = tuple(
            sorted(
                item.object_id for item in scene_plan.objects if item.semantic_class == semantic
            ),
        )

    if slot_id in VISUAL_MATERIAL_SLOT_IDS:
        return "blender-material", (slot_id,)

    prop_variants = dict(PROP_SLOT_VARIANTS)
    if variant := prop_variants.get(slot_id):
        evidence = tuple(
            object_id for object_id in by_semantic["prop"] if _prop_variant(object_id) == variant
        )
        return variant, evidence

    if slot_id == "environment-stone-bridge-01":
        evidence = by_semantic["bridge"]
    elif slot_id == "environment-creek-bend-01":
        evidence = by_semantic["creek"]
    elif slot_id == "environment-irrigation-pond-01":
        evidence = by_semantic["pond"]
    elif slot_id == "environment-terrace-field-01":
        evidence = by_semantic["field"]
    elif slot_id == "environment-orchard-slope-01":
        evidence = by_semantic["orchard"]
    elif slot_id == "environment-bamboo-grove-01":
        evidence = by_semantic["bamboo"]
    elif slot_id == "environment-forest-mountain-01":
        evidence = ("aux-support-terrain-skirt",)
    elif slot_id == "environment-overcast-sky-01":
        evidence = ("background-world",)
    else:
        evidence = ()
    if slot_id in ENVIRONMENT_SLOT_COMPONENTS:
        return ENVIRONMENT_SLOT_COMPONENTS[slot_id], evidence

    detail_component = DETAIL_SLOT_COMPONENTS.get(slot_id)
    if detail_component is not None:
        detail_evidence = (
            by_semantic["courtyard"]
            if slot_id == "detail-courtyard-joint-01"
            else by_semantic["bridge"]
            if slot_id == "detail-bridge-parapet-01"
            else by_semantic["building"]
        )
        return detail_component, detail_evidence

    if artifact_name := KEY_VIEW_PREVIEW_ARTIFACTS.get(slot_id):
        return "preview-artifact", (artifact_name,)
    return None, ()


def _object_registry(
    scene_plan: ScenePlan,
    semantic_registry: tuple[SemanticRegistryEntry, ...],
    material_registry: tuple[MaterialRegistryEntry, ...],
) -> tuple[ObjectRegistryEntry, ...]:
    semantics = {item.semantic_class: item.semantic_id for item in semantic_registry}
    materials = {item.material_family: item.material_id for item in material_registry}
    return tuple(
        ObjectRegistryEntry(
            object_id=item.object_id,
            instance_id=item.instance_id,
            semantic_id=semantics[item.semantic_class],
            material_id=materials[item.material_family],
            variant_id=_prop_variant(item.object_id),
        )
        for item in scene_plan.objects
    )


def _visual_slot_registry(
    repo_root: Path,
    visual_pack_root: Path,
    scene_plan: ScenePlan | None = None,
):
    catalog_path = (
        repo_root / "assets/default-resources/synthetic-mountain-village-visual-slots-v1.json"
    )
    catalog = load_default_visual_slots(catalog_path)
    manifest = load_visual_source_manifest(visual_pack_root / VISUAL_MANIFEST_NAME)
    sources = {record.slot_id: record.sha256 for record in manifest.records}
    catalog_ids = {slot.slot_id for slot in catalog.slots}
    if not set(sources).issubset(catalog_ids):
        raise CanaryBuildError("visual source manifest references an unknown slot")
    implementation_by_category = {
        "key-view": "composition-reference-v1",
        "material": "pbr-material-v1",
        "detail": "geometry-detail-v1",
        "environment": "environment-element-v1",
        "prop": "prop-element-v1",
    }
    active_scene = scene_plan or build_scene_plan()
    rows = []
    for slot in sorted(catalog.slots, key=lambda item: item.slot_id):
        source_sha256 = sources.get(slot.slot_id)
        if source_sha256 is not None:
            usage_mode = "design-reference-only"
            reference_status = "verified-design-reference"
        else:
            usage_mode = "procedural-placeholder-v1"
            reference_status = "no-reference"
        component_tag, evidence_ids = _visual_slot_build_evidence(
            slot.slot_id,
            active_scene,
        )
        if evidence_ids:
            build_status = "instantiated"
            implementation = implementation_by_category[slot.category]
        else:
            build_status = "declared-not-instantiated"
            implementation = "not-instantiated-v1"
        rows.append(
            VisualSlotRegistryEntry(
                slot_id=slot.slot_id,
                category=slot.category,
                usage_mode=usage_mode,
                source_sha256=source_sha256,
                reference_status=reference_status,
                canary_critical=slot.canary_critical,
                build_status=build_status,
                implementation=implementation,
                component_tag=component_tag,
                evidence_ids=evidence_ids,
            ),
        )
    return tuple(rows), catalog, manifest


def _tool_identity(receipt: ToolInstallReceipt, *, runtime_build_hash: str) -> ToolIdentity:
    return ToolIdentity(
        tool_id="blender",
        version="4.5.11",
        platform="windows-x64",
        archive_sha256=receipt.archive_sha256,
        executable_sha256=receipt.executable_sha256,
        runtime_build_hash=runtime_build_hash,
        runtime_output_sha256=hashlib.sha256(receipt.runtime_output.encode("utf-8")).hexdigest(),
    )


def build_canary_request(
    *,
    repo_root: Path = ROOT,
    scene_plan: ScenePlan | None = None,
    camera_plan: CameraPlan | None = None,
    visual_pack_root: Path | None = None,
) -> BuildRequest:
    """Build the path-free, content-addressed Blender request from verified inputs."""

    repo_root = Path(repo_root).absolute()
    active_scene = scene_plan or build_scene_plan()
    active_camera = camera_plan or build_camera_plan(active_scene)
    recipe_path = repo_root / "assets/default-resources/synthetic-mountain-village-v1.json"
    lock_path = repo_root / "tools.lock.json"
    builder_script = repo_root / "scripts/blender/build_synthetic_village.py"
    pack_root = visual_pack_root or (
        repo_root / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"
    )

    recipe = load_default_recipe(recipe_path)
    if active_scene.recipe_id != recipe.recipe_id:
        raise CanaryBuildError("scene plan does not reference the tracked recipe")
    slots, catalog, manifest = _visual_slot_registry(
        repo_root,
        Path(pack_root).absolute(),
        active_scene,
    )
    lock = load_tool_lock(lock_path)
    receipt = verify_locked_install(
        lock.blender,
        repo_root / Path(lock.blender.install_dir),
    )
    semantics = _semantic_registry()
    materials = _material_registry(active_scene)
    objects = _object_registry(active_scene, semantics, materials)
    source_hashes = SourceHashes(
        default_recipe_sha256=hashlib.sha256(canonical_json_bytes(recipe)).hexdigest(),
        visual_catalog_sha256=hashlib.sha256(canonical_json_bytes(catalog)).hexdigest(),
        visual_source_manifest_sha256=hashlib.sha256(
            canonical_manifest_bytes(manifest),
        ).hexdigest(),
        scene_plan_sha256=hashlib.sha256(
            canonical_scene_plan_bytes(active_scene),
        ).hexdigest(),
        camera_plan_sha256=hashlib.sha256(
            canonical_camera_plan_bytes(active_camera),
        ).hexdigest(),
        tool_lock_sha256=_sha256_file(lock_path),
        builder_script_sha256=_sha256_file(builder_script),
    )
    payload = {
        "schema_version": BUILD_REQUEST_SCHEMA,
        "synthetic": True,
        "verification_level": "L2",
        "scene_plan": active_scene,
        "camera_plan": active_camera,
        "source_hashes": source_hashes,
        "tool_identity": _tool_identity(
            receipt,
            runtime_build_hash=lock.blender.runtime_build_hash,
        ),
        "object_registry": objects,
        "auxiliary_registry": AUXILIARY_REGISTRY,
        "semantic_registry": semantics,
        "material_registry": materials,
        "visual_slot_registry": slots,
        "requested_artifacts": ARTIFACT_REQUESTS,
    }
    build_id = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return BuildRequest(build_id=build_id, **payload)


@dataclass(frozen=True)
class CanaryBuildResult:
    final_directory: Path
    report: BuildReport
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    signature: tuple[int, int, int, int]
    sha256: str


def _flush_file(path: Path) -> None:
    if os.name == "nt":
        WindowsNtfsDurabilityBackend.flush_file(path)
        return
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _flush_directory(path: Path) -> None:
    if os.name == "nt":
        WindowsNtfsDurabilityBackend.flush_directory(path)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_real_directory_tree(target: Path, *, repo_root: Path) -> Path:
    target = Path(target).absolute()
    repo_root = _require_real_directory(
        Path(repo_root).absolute(),
        label="managed root",
    )
    try:
        relative = target.relative_to(repo_root)
    except ValueError as exc:
        raise CanaryBuildError("private work path must remain inside the repository") from exc
    cursor = repo_root
    for component in relative.parts:
        cursor = cursor / component
        if cursor.exists() or _is_linklike(cursor):
            _require_real_directory(cursor, label="private work directory")
            continue
        try:
            cursor.mkdir(exist_ok=False)
            _flush_directory(cursor.parent)
        except FileExistsError:
            pass
        _require_real_directory(cursor, label="private work directory")
    return _require_real_directory(target, label="private work directory")


def _prepare_private_work_root(repo_root: Path, work_root: Path | None) -> Path:
    repo_root = _require_real_directory(repo_root, label="repository root")
    private_root = repo_root / ".nantai-studio"
    selected = (
        Path(work_root).absolute()
        if work_root is not None
        else (repo_root / ".nantai-studio/synthetic-village/hybrid-v3/work/canary")
    )
    try:
        selected.relative_to(private_root)
    except ValueError as exc:
        raise CanaryBuildError("canary work root must remain below .nantai-studio") from exc
    _ensure_real_directory_tree(private_root, repo_root=repo_root)
    return _ensure_real_directory_tree(selected, repo_root=repo_root)


def _snapshot_regular_file(path: Path) -> _FileSnapshot:
    path = Path(path).absolute()
    _require_real_directory(path.parent, label="canary input directory")
    if _is_linklike(path) or not path.is_file():
        raise CanaryBuildError(f"canary input is missing or redirected: {path.name}")
    before = path.stat()
    if before.st_size <= 0 or before.st_size > MAX_ARTIFACT_BYTES:
        raise CanaryBuildError(f"canary input size is invalid: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _stat_signature(before) != _stat_signature(opened):
            raise CanaryBuildError(f"canary input changed before hashing: {path.name}")
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
        after_open = os.fstat(stream.fileno())
    after = path.stat()
    if _stat_signature(opened) != _stat_signature(after_open) or _stat_signature(
        before
    ) != _stat_signature(after):
        raise CanaryBuildError(f"canary input changed while hashing: {path.name}")
    return _FileSnapshot(
        path=path,
        signature=_stat_signature(before),
        sha256=digest.hexdigest(),
    )


def _collect_input_snapshots(repo_root: Path, visual_pack_root: Path) -> tuple[_FileSnapshot, ...]:
    manifest_path = visual_pack_root / VISUAL_MANIFEST_NAME
    manifest = load_visual_source_manifest(manifest_path)
    paths = [
        repo_root / "assets/default-resources/synthetic-mountain-village-v1.json",
        repo_root / "assets/default-resources/synthetic-mountain-village-visual-slots-v1.json",
        repo_root / "tools.lock.json",
        repo_root / "scripts/blender/build_synthetic_village.py",
        repo_root / "third/blender/.nantai-tool.json",
        repo_root / "third/blender/blender.exe",
        manifest_path,
        *(visual_pack_root / record.object_path for record in manifest.records),
    ]
    return tuple(_snapshot_regular_file(path) for path in paths)


def _verify_snapshots_unchanged(snapshots: tuple[_FileSnapshot, ...]) -> None:
    for expected in snapshots:
        actual = _snapshot_regular_file(expected.path)
        if actual.signature != expected.signature or actual.sha256 != expected.sha256:
            raise CanaryBuildError(f"canary input changed during build: {expected.path.name}")


def _write_new_file(path: Path, payload: bytes) -> None:
    _require_real_directory(path.parent, label="request directory")
    if path.exists() or _is_linklike(path):
        raise CanaryBuildError(f"refusing to replace existing file: {path.name}")
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _flush_directory(path.parent)
    except OSError as exc:
        raise CanaryBuildError(f"cannot write private request: {exc}") from exc


class _BoundedPipeCapture:
    """Continuously drain a child pipe while retaining only a bounded prefix."""

    def __init__(self, label: str):
        self.label = label
        self._reader_fd, writer_fd = os.pipe()
        self.writer = os.fdopen(writer_fd, "wb", buffering=0)
        self._payload = bytearray()
        self._overflow = False
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def _drain(self) -> None:
        try:
            while chunk := os.read(self._reader_fd, 64 * 1024):
                remaining = MAX_PROCESS_LOG_BYTES + 1 - len(self._payload)
                if remaining > 0:
                    self._payload.extend(chunk[:remaining])
                if len(self._payload) > MAX_PROCESS_LOG_BYTES:
                    self._overflow = True
        finally:
            os.close(self._reader_fd)

    def __enter__(self) -> _BoundedPipeCapture:
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.writer.close()
        self._thread.join()

    def text(self) -> str:
        if self._overflow:
            raise CanaryBuildError(
                f"Blender {self.label} log exceeds the bounded capture limit",
            )
        return bytes(self._payload).decode("utf-8", errors="replace")


def _minimum_blender_environment(invocation_root: Path) -> dict[str, str]:
    environment = {
        "TEMP": str(invocation_root / "temp"),
        "TMP": str(invocation_root / "temp"),
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "BLENDER_USER_CONFIG": str(invocation_root / "blender-user-config"),
        "BLENDER_USER_SCRIPTS": str(invocation_root / "blender-user-scripts"),
        "BLENDER_USER_DATAFILES": str(invocation_root / "blender-user-datafiles"),
    }
    for key in ("SYSTEMROOT", "WINDIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    for directory in (
        invocation_root / "temp",
        invocation_root / "blender-user-config",
        invocation_root / "blender-user-scripts",
        invocation_root / "blender-user-datafiles",
    ):
        directory.mkdir(exist_ok=False)
    return environment


def _run_blender_process(
    *,
    repo_root: Path,
    executable: Path,
    request_path: Path,
    staging: Path,
    invocation_root: Path,
    timeout_seconds: int,
) -> tuple[int, str, str]:
    argv = [
        str(executable),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "17",
        "--python",
        "scripts/blender/build_synthetic_village.py",
        "--",
        "--request",
        str(request_path),
        "--staging",
        str(staging),
    ]
    environment = _minimum_blender_environment(invocation_root)
    try:
        timeout_error = None
        with (
            _BoundedPipeCapture("stdout") as stdout,
            _BoundedPipeCapture(
                "stderr",
            ) as stderr,
        ):
            try:
                completed = subprocess.run(
                    argv,
                    check=False,
                    shell=False,
                    cwd=str(repo_root),
                    env=environment,
                    timeout=timeout_seconds,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout.writer,
                    stderr=stderr.writer,
                )
            except subprocess.TimeoutExpired as exc:
                timeout_error = exc
                completed = None
        if timeout_error is not None:
            raise CanaryBuildError(
                f"Blender build exceeded the {timeout_seconds}-second timeout",
            ) from timeout_error
        if completed is None:
            raise CanaryBuildError("Blender process did not return a completion status")
        return completed.returncode, stdout.text(), stderr.text()
    except CanaryBuildError:
        raise
    except OSError as exc:
        raise CanaryBuildError(f"cannot launch verified Blender runtime: {exc}") from exc


def _validate_staging_entries(staging: Path) -> None:
    expected = {"build-report.json", *(item.name for item in ARTIFACT_REQUESTS)}
    actual = {item.name for item in staging.iterdir()}
    if actual != expected:
        raise CanaryBuildError("build staging contains missing or unregistered outputs")
    for path in staging.iterdir():
        if _is_linklike(path) or not path.is_file():
            raise CanaryBuildError(f"build staging output is redirected or irregular: {path.name}")


def _durably_flush_verified_staging(staging: Path) -> None:
    for path in sorted(staging.iterdir(), key=lambda item: item.name):
        _flush_file(path)
    _flush_directory(staging)


def _move_directory_noreplace(source: Path, destination: Path) -> None:
    if destination.exists() or _is_linklike(destination):
        raise CanaryBuildError(f"canary destination already exists: {destination.name}")
    try:
        if os.name == "nt":
            WindowsNtfsDurabilityBackend.move(source, destination)
        elif sys.platform.startswith("linux"):
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100,
                os.fsencode(source),
                -100,
                os.fsencode(destination),
                1,
            )
            if result != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(destination))
            _flush_directory(destination.parent)
        else:
            if destination.exists() or _is_linklike(destination):
                raise FileExistsError(errno.EEXIST, "destination exists", destination)
            os.rename(source, destination)
            _flush_directory(destination.parent)
    except (JobContractError, OSError) as exc:
        if destination.exists() or _is_linklike(destination):
            raise CanaryBuildError(
                f"canary destination already exists: {destination.name}",
            ) from exc
        raise CanaryBuildError(f"cannot publish verified canary: {exc}") from exc


def _cleanup_owned_directory(
    path: Path,
    *,
    work_root: Path,
    expected_name: str,
) -> None:
    try:
        real_work_root = _require_real_directory(work_root, label="cleanup work root")
        candidate = Path(path).absolute()
        if (
            not expected_name
            or Path(expected_name).name != expected_name
            or not _same_path(candidate, real_work_root / expected_name)
            or not candidate.exists()
            or _is_linklike(candidate)
            or not candidate.is_dir()
        ):
            return
        shutil.rmtree(candidate)
        _flush_directory(real_work_root)
    except (CanaryBuildError, OSError, RuntimeError):
        return


def run_canary_build(
    *,
    repo_root: Path = ROOT,
    visual_pack_root: Path | None = None,
    work_root: Path | None = None,
    timeout_seconds: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
) -> CanaryBuildResult:
    """Build, verify, and absent-publish one private Blender canary directory."""

    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 24 * 60 * 60
    ):
        raise CanaryBuildError("build timeout must be an integer from 1 to 86400 seconds")
    repo_root = _require_real_directory(Path(repo_root).absolute(), label="repository root")
    pack_root = (
        Path(visual_pack_root).absolute()
        if visual_pack_root is not None
        else (repo_root / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources")
    )
    pack_root = _require_real_directory(pack_root, label="visual source pack")
    active_work_root = _prepare_private_work_root(repo_root, work_root)
    invocation_root: Path | None = None
    staging: Path | None = None
    try:
        with ProjectFileLock(active_work_root / ".canary-build.lock", role="writer"):
            snapshots = _collect_input_snapshots(repo_root, pack_root)
            scene = build_scene_plan()
            request = build_canary_request(
                repo_root=repo_root,
                scene_plan=scene,
                camera_plan=build_camera_plan(scene),
                visual_pack_root=pack_root,
            )
            _verify_snapshots_unchanged(snapshots)
            final_directory = active_work_root / request.build_id
            if final_directory.exists() or _is_linklike(final_directory):
                raise CanaryBuildError(
                    f"canary destination already exists: {final_directory.name}",
                )

            nonce = uuid.uuid4().hex
            invocation_root = active_work_root / f".invocation-{nonce}"
            staging = active_work_root / f".staging-{nonce}"
            invocation_root.mkdir(exist_ok=False)
            _flush_directory(active_work_root)
            _require_real_directory(invocation_root, label="build invocation directory")
            if staging.exists() or _is_linklike(staging):
                raise CanaryBuildError("runtime staging destination must start absent")
            request_path = invocation_root / "build-request.json"
            _write_new_file(request_path, canonical_build_request_bytes(request))
            request_snapshot = _snapshot_regular_file(request_path)

            returncode, stdout, stderr = _run_blender_process(
                repo_root=repo_root,
                executable=repo_root / "third/blender/blender.exe",
                request_path=request_path,
                staging=staging,
                invocation_root=invocation_root,
                timeout_seconds=timeout_seconds,
            )
            _verify_snapshots_unchanged((*snapshots, request_snapshot))
            if returncode != 0:
                raise CanaryBuildError(
                    f"Blender build failed with exit code {returncode}",
                )
            report_path = staging / "build-report.json"
            if not report_path.is_file() or _is_linklike(report_path):
                raise CanaryBuildError("Blender build completed without a trusted build report")
            report = load_build_report(report_path)
            verify_build_report(report, request=request, staging=staging)
            _validate_staging_entries(staging)
            _durably_flush_verified_staging(staging)
            verify_build_report(report, request=request, staging=staging)
            _move_directory_noreplace(staging, final_directory)
            staging = None
            return CanaryBuildResult(
                final_directory=final_directory,
                report=report,
                stdout=stdout,
                stderr=stderr,
            )
    except CanaryBuildError:
        raise
    except JobContractError as exc:
        raise CanaryBuildError(f"canary build lock is unavailable: {exc}") from exc
    except (OSError, ValueError, ValidationError) as exc:
        raise CanaryBuildError(f"canary build failed safely: {exc}") from exc
    finally:
        if staging is not None:
            _cleanup_owned_directory(
                staging,
                work_root=active_work_root,
                expected_name=staging.name,
            )
        if invocation_root is not None:
            _cleanup_owned_directory(
                invocation_root,
                work_root=active_work_root,
                expected_name=invocation_root.name,
            )


def _read_stable_metadata(path: Path, *, label: str) -> bytes:
    path = Path(path).absolute()
    parent = _require_real_directory(path.parent, label=f"{label} directory")
    if path.parent != parent or _is_linklike(path) or not path.is_file():
        raise CanaryBuildError(f"{label} is missing or redirected")
    before = path.stat()
    if before.st_size <= 0 or before.st_size > MAX_RENDER_METADATA_BYTES:
        raise CanaryBuildError(f"{label} size is invalid")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _stat_signature(before) != _stat_signature(opened):
            raise CanaryBuildError(f"{label} changed before bounded read")
        raw = stream.read(MAX_RENDER_METADATA_BYTES + 1)
        after_open = os.fstat(stream.fileno())
    after = path.stat()
    if (
        len(raw) != before.st_size
        or len(raw) > MAX_RENDER_METADATA_BYTES
        or _stat_signature(opened) != _stat_signature(after_open)
        or _stat_signature(before) != _stat_signature(after)
    ):
        raise CanaryBuildError(f"{label} changed during bounded read")
    return raw


def load_render_journal(path: Path) -> RenderJournal:
    try:
        raw = _read_stable_metadata(path, label="render journal")
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        if _contains_private_path(parsed):
            raise CanaryBuildError("render journal contains a private path or username")
        journal = RenderJournal.model_validate_json(raw)
        if raw != canonical_render_journal_bytes(journal):
            raise CanaryBuildError("render journal must be canonical JSON")
        digest = hashlib.sha256(
            canonical_render_journal_bytes(journal, exclude_sha256=True),
        ).hexdigest()
        if journal.journal_sha256 != digest:
            raise CanaryBuildError("render journal SHA-256 is invalid")
        return journal
    except CanaryBuildError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise CanaryBuildError(f"render journal validation failed: {exc}") from exc


def _load_frame_report(path: Path) -> RenderFrameReport:
    try:
        raw = _read_stable_metadata(path, label="render frame report")
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        if _contains_private_path(parsed):
            raise CanaryBuildError("render frame report contains a private path or username")
        report = RenderFrameReport.model_validate_json(raw)
        if raw != canonical_render_frame_report_bytes(report):
            raise CanaryBuildError("render frame report must be canonical JSON")
        expected = hashlib.sha256(
            canonical_render_frame_report_bytes(report, exclude_sha256=True),
        ).hexdigest()
        if report.content_sha256 != expected:
            raise CanaryBuildError("render frame report SHA-256 is invalid")
        return report
    except CanaryBuildError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise CanaryBuildError(f"render frame report validation failed: {exc}") from exc


def _load_camera_metadata(path: Path) -> CameraFrameMetadata:
    try:
        raw = _read_stable_metadata(path, label="camera metadata")
        json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        metadata = CameraFrameMetadata.model_validate_json(raw)
        if raw != canonical_camera_metadata_bytes(metadata):
            raise CanaryBuildError("camera metadata must be canonical JSON")
        return metadata
    except CanaryBuildError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise CanaryBuildError(f"camera metadata validation failed: {exc}") from exc


def _seal_render_journal(journal: RenderJournal) -> RenderJournal:
    digest = hashlib.sha256(
        canonical_render_journal_bytes(journal, exclude_sha256=True),
    ).hexdigest()
    return journal.model_copy(update={"journal_sha256": digest})


def _is_retryable_windows_replace_error(exc: OSError) -> bool:
    """Return whether *exc* is a bounded Windows sharing/access collision."""

    error_code = getattr(exc, "winerror", None)
    if error_code is None and os.name == "nt":
        error_code = exc.errno
    return os.name == "nt" and error_code in WINDOWS_SHARING_VIOLATIONS


def _write_render_journal(path: Path, journal: RenderJournal) -> None:
    parent = _require_real_directory(path.parent, label="render root")
    if path.parent != parent or _is_linklike(path):
        raise CanaryBuildError("render journal path is redirected")
    sealed = _seal_render_journal(journal)
    temporary = parent / f".render-journal-{uuid.uuid4().hex}.tmp"
    try:
        _write_new_file(temporary, canonical_render_journal_bytes(sealed))
        for attempt in range(1, JOURNAL_REPLACE_ATTEMPTS + 1):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                if (
                    not _is_retryable_windows_replace_error(exc)
                    or attempt == JOURNAL_REPLACE_ATTEMPTS
                ):
                    raise
                time.sleep(0.025 * attempt)
        _flush_file(path)
        _flush_directory(parent)
    except OSError as exc:
        raise CanaryBuildError(f"cannot durably update render journal: {exc}") from exc
    finally:
        try:
            if temporary.exists() and not _is_linklike(temporary):
                temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _replace_frame_record(
    journal: RenderJournal,
    camera_id: str,
    record: RenderFrameRecord,
) -> RenderJournal:
    return _seal_render_journal(
        journal.model_copy(
            update={
                "frames": tuple(
                    record if item.camera_id == camera_id else item for item in journal.frames
                ),
            },
        ),
    )


def _render_artifact_paths(render_root: Path, camera_id: str) -> tuple[Path, ...]:
    return tuple(render_root / Path(path) for _, path in _expected_render_artifacts(camera_id))


def _preflight_render_artifact_paths(render_root: Path, camera_id: str) -> tuple[Path, ...]:
    render_root = _require_real_directory(render_root, label="render root")
    paths = _render_artifact_paths(render_root, camera_id)
    for layer in {path.parent for path in paths}:
        if layer.parent != render_root:
            raise CanaryBuildError("render artifact layer is not a direct child of render root")
        if not layer.exists() and not _is_linklike(layer):
            continue
        validated = _require_real_directory(layer, label="render artifact layer")
        if validated != layer or validated.parent != render_root:
            raise CanaryBuildError("render artifact layer is redirected")
    return paths


def _frame_has_any_output(render_root: Path, camera_id: str) -> bool:
    return any(
        path.exists() or _is_linklike(path)
        for path in _preflight_render_artifact_paths(render_root, camera_id)
    )


def _quarantine_frame_outputs(render_root: Path, camera_id: str) -> None:
    paths = _preflight_render_artifact_paths(render_root, camera_id)
    sources = []
    for source in paths:
        if not source.exists() and not _is_linklike(source):
            continue
        if _is_linklike(source) or not source.is_file():
            raise CanaryBuildError("render output is redirected or irregular")
        sources.append(source)
    if not sources:
        return
    quarantine_root = _ensure_real_directory_tree(
        render_root / ".q",
        repo_root=render_root,
    )
    nonce = uuid.uuid4().hex[:12]
    staging = quarantine_root / f".q-{nonce}"
    destination = quarantine_root / f"{camera_id}-{nonce}"
    staging.mkdir(exist_ok=False)
    _flush_directory(quarantine_root)
    try:
        for source in sources:
            layer = staging / source.parent.name
            layer.mkdir(exist_ok=False)
            _flush_directory(staging)
            _move_directory_noreplace(source, layer / source.name)
        _flush_directory(staging)
        _move_directory_noreplace(staging, destination)
    except Exception:
        if staging.exists() and not _is_linklike(staging):
            try:
                _flush_directory(staging)
            except Exception:
                pass
        raise


def _png_contract(path: Path, *, kind: str) -> None:
    with path.open("rb") as stream:
        header = stream.read(33)
    if len(header) < 33 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise CanaryBuildError(f"rendered {kind} output is not a PNG")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", header[16:26])
    expected = {
        "rgb": (8, 2),
        "instance-mask": (16, 0),
        "semantic-mask": (8, 0),
    }[kind]
    if (width, height) != (1024, 576) or (bit_depth, color_type) != expected:
        raise CanaryBuildError(f"rendered {kind} PNG contract is invalid")


def _validate_frame_artifact_file(path: Path, *, kind: str) -> tuple[str, int]:
    digest, size = _sha256_stable_artifact(path)
    if kind in {"rgb", "instance-mask", "semantic-mask"}:
        _png_contract(path, kind=kind)
    elif kind in {"depth", "normal"}:
        with path.open("rb") as stream:
            if stream.read(4) != b"\x76\x2f\x31\x01":
                raise CanaryBuildError(f"rendered {kind} output is not OpenEXR")
    return digest, size


def _validate_camera_metadata(
    metadata: CameraFrameMetadata,
    request: RenderFrameRequest,
) -> None:
    camera = request.camera
    if (
        metadata.build_id != request.build_id
        or metadata.render_id != request.render_id
        or metadata.blender_executable_sha256 != request.blender_executable_sha256
        or metadata.camera_id != camera.camera_id
        or metadata.category != camera.category
        or metadata.split != camera.split
        or metadata.intrinsics != camera.intrinsics.model_dump(mode="json")
        or metadata.requested_c2w_opencv != camera.c2w_opencv
        or metadata.requested_c2w_blender != camera.c2w_blender
        or metadata.measured_c2w_blender != request.measured_c2w_blender
        or metadata.measured_c2w_opencv != _blender_c2w_to_opencv(request.measured_c2w_blender)
        or metadata.object_registry_sha256 != request.object_registry_sha256
        or metadata.semantic_registry != request.semantic_registry
        or metadata.settings_sha256
        != hashlib.sha256(
            _canonical_json_bytes(request.settings.model_dump(mode="json")),
        ).hexdigest()
    ):
        raise CanaryBuildError("camera metadata does not match the immutable render request")


def _validate_frame_staging(
    staging: Path,
    request: RenderFrameRequest,
) -> tuple[RenderFrameReport, str]:
    staging = _require_real_directory(staging, label="render frame staging")
    report_path = staging / "frame-report.json"
    if not report_path.is_file() or _is_linklike(report_path):
        raise CanaryBuildError("render completed without a trusted frame report")
    report = _load_frame_report(report_path)
    if (
        report.build_id != request.build_id
        or report.render_id != request.render_id
        or report.camera_id != request.camera.camera_id
        or report.blender_executable_sha256 != request.blender_executable_sha256
    ):
        raise CanaryBuildError("render frame report does not match its immutable request")
    expected_settings_sha256 = hashlib.sha256(
        _canonical_json_bytes(request.settings.model_dump(mode="json")),
    ).hexdigest()
    if report.settings_sha256 != expected_settings_sha256:
        raise CanaryBuildError("render frame report settings fingerprint is invalid")
    expected_entries = {
        "frame-report.json",
        *(path.split("/", 1)[0] for _, path in _expected_render_artifacts(report.camera_id)),
    }
    if {path.name for path in staging.iterdir()} != expected_entries:
        raise CanaryBuildError("render frame staging is incomplete or contains unregistered output")
    for artifact, (kind, portable_path) in zip(
        report.artifacts,
        _expected_render_artifacts(report.camera_id),
        strict=True,
    ):
        path = staging / Path(portable_path)
        if path.parent.parent != staging or _is_linklike(path.parent):
            raise CanaryBuildError("render artifact path is redirected")
        digest, size = _validate_frame_artifact_file(path, kind=kind)
        if artifact.sha256 != digest or artifact.size_bytes != size:
            raise CanaryBuildError("render frame artifact digest or size mismatch")
    camera_path = staging / f"cameras/{report.camera_id}.json"
    _validate_camera_metadata(_load_camera_metadata(camera_path), request)
    return report, _sha256_file(report_path)


def _verify_published_frame(
    render_root: Path,
    frame: RenderFrameRecord,
) -> None:
    if frame.state != "verified" or len(frame.artifacts) != 6:
        raise CanaryBuildError("render journal frame is not verified")
    paths = _preflight_render_artifact_paths(render_root, frame.camera_id)
    for artifact, (kind, portable_path), path in zip(
        frame.artifacts,
        _expected_render_artifacts(frame.camera_id),
        paths,
        strict=True,
    ):
        if artifact.kind != kind or artifact.path != portable_path:
            raise CanaryBuildError("render journal artifact contract is invalid")
        digest, size = _validate_frame_artifact_file(path, kind=kind)
        if digest != artifact.sha256 or size != artifact.size_bytes:
            raise CanaryBuildError("verified render frame hash mismatch")


def _durably_flush_frame_staging(staging: Path) -> None:
    for path in sorted(staging.rglob("*"), key=lambda item: str(item)):
        if _is_linklike(path):
            raise CanaryBuildError("render frame staging contains a redirected entry")
        if path.is_file():
            _flush_file(path)
    for path in sorted(
        (item for item in staging.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        _flush_directory(path)
    _flush_directory(staging)


def _publish_frame(staging: Path, render_root: Path, report: RenderFrameReport) -> None:
    for artifact in report.artifacts:
        source = staging / Path(artifact.path)
        destination = render_root / Path(artifact.path)
        _ensure_real_directory_tree(destination.parent, repo_root=render_root)
        if destination.exists() or _is_linklike(destination):
            raise CanaryBuildError("render frame destination must start absent")
        _move_directory_noreplace(source, destination)
        _flush_file(destination)
        _flush_directory(destination.parent)
    _flush_directory(render_root)


def _sanitize_render_error(exc: BaseException) -> str:
    message = re.sub(r"[A-Za-z]:[\\/][^\r\n]*", "<private-path>", str(exc)).strip()
    message = message.replace(".nantai-studio", "<private>")
    return (message or type(exc).__name__)[:512]


def _run_blender_render_process(
    *,
    repo_root: Path,
    executable: Path,
    blend_path: Path,
    request_path: Path,
    staging: Path,
    invocation_root: Path,
    timeout_seconds: int,
) -> tuple[int, str, str]:
    argv = [
        str(executable),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "17",
        str(blend_path),
        "--python",
        "scripts/blender/render_synthetic_village.py",
        "--",
        "--request",
        str(request_path),
        "--staging",
        str(staging),
    ]
    environment = _minimum_blender_environment(invocation_root)
    try:
        timeout_error = None
        with _BoundedPipeCapture("stdout") as stdout, _BoundedPipeCapture("stderr") as stderr:
            try:
                completed = subprocess.run(
                    argv,
                    check=False,
                    shell=False,
                    cwd=str(repo_root),
                    env=environment,
                    timeout=timeout_seconds,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout.writer,
                    stderr=stderr.writer,
                )
            except subprocess.TimeoutExpired as exc:
                timeout_error = exc
                completed = None
        if timeout_error is not None:
            raise CanaryBuildError(
                f"Blender render exceeded the {timeout_seconds}-second timeout",
            ) from timeout_error
        if completed is None:
            raise CanaryBuildError("Blender render did not return a completion status")
        return completed.returncode, stdout.text(), stderr.text()
    except CanaryBuildError:
        raise
    except OSError as exc:
        raise CanaryBuildError(f"cannot launch verified Blender renderer: {exc}") from exc


@dataclass(frozen=True)
class CanaryRenderResult:
    render_root: Path
    journal_path: Path
    render_id: str
    rendered_count: int
    reused_count: int
    stdout: str
    stderr: str


def _render_id_payload(
    *,
    report: BuildReport,
    blender_executable_sha256: str,
    renderer_script_sha256: str,
    blend_sha256: str,
    build_report_sha256: str,
    object_registry_sha256: str,
    settings: RenderSettings,
) -> dict[str, object]:
    return {
        "schema_version": RENDER_JOURNAL_SCHEMA,
        "build_id": report.build_id,
        "blender_executable_sha256": blender_executable_sha256,
        "renderer_script_sha256": renderer_script_sha256,
        "blend_sha256": blend_sha256,
        "build_report_sha256": build_report_sha256,
        "object_registry_sha256": object_registry_sha256,
        "settings": settings,
        "camera_ids": RENDER_CAMERA_IDS,
        "camera_plan_sha256": report.source_hashes.camera_plan_sha256,
    }


def _normalize_render_camera_ids(camera_ids: object) -> tuple[str, ...]:
    if camera_ids is None:
        return RENDER_CAMERA_IDS
    if (
        type(camera_ids) is not tuple
        or not camera_ids
        or any(type(item) is not str for item in camera_ids)
        or len(set(camera_ids)) != len(camera_ids)
        or any(item not in RENDER_CAMERA_IDS for item in camera_ids)
    ):
        raise CanaryBuildError("selected render camera IDs must be a unique canonical tuple")
    selected = set(camera_ids)
    return tuple(item for item in RENDER_CAMERA_IDS if item in selected)


def run_canary_render(
    *,
    repo_root: Path = ROOT,
    build_directory: Path | None = None,
    camera_ids: tuple[str, ...] | None = None,
    timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> CanaryRenderResult:
    """Resume, verify, and fail-closed publish the strict six-layer canary frames."""

    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 24 * 60 * 60
    ):
        raise CanaryBuildError("render timeout must be an integer from 1 to 86400 seconds")
    selected_ids = _normalize_render_camera_ids(camera_ids)
    repo_root = _require_real_directory(Path(repo_root).absolute(), label="repository root")
    scene = build_scene_plan()
    build_request = build_canary_request(
        repo_root=repo_root,
        scene_plan=scene,
        camera_plan=build_camera_plan(scene),
        visual_pack_root=repo_root / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources",
    )
    selected_build = (
        Path(build_directory).absolute()
        if build_directory is not None
        else repo_root
        / ".nantai-studio/synthetic-village/hybrid-v3/work/canary"
        / build_request.build_id
    )
    selected_build = _require_real_directory(selected_build, label="canary build directory")
    private_root = repo_root / ".nantai-studio"
    try:
        selected_build.relative_to(private_root)
    except ValueError as exc:
        raise CanaryBuildError("canary build directory must remain private") from exc
    if selected_build.name != build_request.build_id:
        raise CanaryBuildError("canary build directory name does not match the canonical build ID")
    report_path = selected_build / "build-report.json"
    report = load_build_report(report_path)
    verify_build_report(report, request=build_request, staging=selected_build)

    renderer_script = repo_root / "scripts/blender/render_synthetic_village.py"
    blender_executable = repo_root / "third/blender/blender.exe"
    blender_executable_snapshot = _snapshot_regular_file(blender_executable)
    if blender_executable_snapshot.sha256 != report.tool_identity.executable_sha256:
        raise CanaryBuildError("Blender executable digest does not match verified tool evidence")
    renderer_snapshot = _snapshot_regular_file(renderer_script)
    report_snapshot = _snapshot_regular_file(report_path)
    blend_record = next(item for item in report.artifacts if item.name == "village-canary.blend")
    blend_path = selected_build / blend_record.name
    blend_snapshot = _snapshot_regular_file(blend_path)
    if blend_snapshot.sha256 != blend_record.sha256:
        raise CanaryBuildError("formal canary blend input hash does not match its build report")
    build_snapshots = tuple(
        _snapshot_regular_file(selected_build / item.name) for item in report.artifacts
    )
    immutable_snapshots = (
        blender_executable_snapshot,
        renderer_snapshot,
        report_snapshot,
        *build_snapshots,
    )
    object_registry_sha256 = hashlib.sha256(
        _canonical_json_bytes(
            [item.model_dump(mode="json") for item in report.object_registry],
        ),
    ).hexdigest()
    settings = RenderSettings()
    render_id = hashlib.sha256(
        _canonical_json_bytes(
            _render_id_payload(
                report=report,
                blender_executable_sha256=blender_executable_snapshot.sha256,
                renderer_script_sha256=renderer_snapshot.sha256,
                blend_sha256=blend_snapshot.sha256,
                build_report_sha256=report_snapshot.sha256,
                object_registry_sha256=object_registry_sha256,
                settings=settings,
            ),
        ),
    ).hexdigest()
    render_root = _ensure_real_directory_tree(selected_build / "renders", repo_root=repo_root)
    journal_path = render_root / "render-journal.json"
    rendered_count = 0
    reused_count = 0
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    with ProjectFileLock(render_root / ".canary-render.lock", role="writer"):
        if journal_path.exists() or _is_linklike(journal_path):
            journal = load_render_journal(journal_path)
            immutable = (
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
                render_id,
                report.build_id,
                blender_executable_snapshot.sha256,
                renderer_snapshot.sha256,
                blend_snapshot.sha256,
                report_snapshot.sha256,
                object_registry_sha256,
                settings,
            )
            if immutable != expected:
                raise CanaryBuildError(
                    "existing render journal belongs to different immutable inputs",
                )
        else:
            journal = _seal_render_journal(
                RenderJournal(
                    render_id=render_id,
                    journal_sha256="0" * 64,
                    build_id=report.build_id,
                    blender_executable_sha256=blender_executable_snapshot.sha256,
                    renderer_script_sha256=renderer_snapshot.sha256,
                    blend_sha256=blend_snapshot.sha256,
                    build_report_sha256=report_snapshot.sha256,
                    object_registry_sha256=object_registry_sha256,
                    settings=settings,
                    frames=tuple(
                        RenderFrameRecord(camera_id=camera_id, state="planned")
                        for camera_id in RENDER_CAMERA_IDS
                    ),
                ),
            )
            _write_render_journal(journal_path, journal)

        cameras = {item.camera_id: item for item in build_request.camera_plan.cameras}
        measured = {item.camera_id: item.measured_c2w_blender for item in report.camera_registry}
        for camera_id in selected_ids:
            stage: Literal["prepare", "invoke", "validate", "publish"] = "prepare"
            nonce = uuid.uuid4().hex[:12]
            temporary_root = selected_build.parent
            invocation_root = temporary_root / f".ri-{nonce}"
            staging = temporary_root / f".rs-{nonce}"
            runtime_work = staging.with_name(f".{staging.name}.tmp-{render_id[:12]}")
            try:
                frame = next(item for item in journal.frames if item.camera_id == camera_id)
                if frame.state == "verified":
                    try:
                        _verify_published_frame(render_root, frame)
                        reused_count += 1
                        continue
                    except CanaryBuildError:
                        _quarantine_frame_outputs(render_root, camera_id)
                        frame = RenderFrameRecord(camera_id=camera_id, state="rendering")
                        journal = _replace_frame_record(journal, camera_id, frame)
                        _write_render_journal(journal_path, journal)
                else:
                    has_output = _frame_has_any_output(render_root, camera_id)
                    if has_output:
                        _quarantine_frame_outputs(render_root, camera_id)
                    frame = RenderFrameRecord(camera_id=camera_id, state="rendering")
                    journal = _replace_frame_record(journal, camera_id, frame)
                    _write_render_journal(journal_path, journal)

                invocation_root.mkdir(exist_ok=False)
                _flush_directory(temporary_root)
                request = RenderFrameRequest(
                    render_id=render_id,
                    build_id=report.build_id,
                    blender_executable_sha256=blender_executable_snapshot.sha256,
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
                _write_new_file(request_path, canonical_render_request_bytes(request))
                request_snapshot = _snapshot_regular_file(request_path)
                stage = "invoke"
                try:
                    returncode, stdout, stderr = _run_blender_render_process(
                        repo_root=repo_root,
                        executable=blender_executable_snapshot.path,
                        blend_path=blend_path,
                        request_path=request_path,
                        staging=staging,
                        invocation_root=invocation_root,
                        timeout_seconds=timeout_seconds,
                    )
                finally:
                    _verify_snapshots_unchanged((*immutable_snapshots, request_snapshot))
                stdout_parts.append(stdout)
                stderr_parts.append(stderr)
                if returncode != 0:
                    runtime_error = next(
                        (
                            line.strip()
                            for line in reversed((stdout + "\n" + stderr).splitlines())
                            if line.strip().startswith("NANTAI_RENDER_ERROR ")
                        ),
                        "",
                    )
                    suffix = (
                        f": {_sanitize_render_error(RuntimeError(runtime_error))}"
                        if runtime_error
                        else ""
                    )
                    raise CanaryBuildError(
                        f"Blender render failed with exit code {returncode}{suffix}",
                    )
                stage = "validate"
                frame_report, report_sha256 = _validate_frame_staging(staging, request)
                _durably_flush_frame_staging(staging)
                stage = "publish"
                _publish_frame(staging, render_root, frame_report)
                verified = RenderFrameRecord(
                    camera_id=camera_id,
                    state="verified",
                    artifacts=frame_report.artifacts,
                    runtime_report_sha256=report_sha256,
                    statistics=frame_report.statistics,
                )
                _verify_published_frame(render_root, verified)
                _verify_snapshots_unchanged(immutable_snapshots)
                journal = _replace_frame_record(journal, camera_id, verified)
                _write_render_journal(journal_path, journal)
                rendered_count += 1
            except Exception as exc:
                error = exc if isinstance(exc, CanaryBuildError) else CanaryBuildError(str(exc))
                failed = RenderFrameRecord(
                    camera_id=camera_id,
                    state="failed",
                    error=RenderFailure(stage=stage, message=_sanitize_render_error(error)),
                )
                journal = _replace_frame_record(journal, camera_id, failed)
                _write_render_journal(journal_path, journal)
                if error is exc:
                    raise
                raise error from exc
            finally:
                _cleanup_owned_directory(
                    runtime_work,
                    work_root=temporary_root,
                    expected_name=runtime_work.name,
                )
                _cleanup_owned_directory(
                    staging,
                    work_root=temporary_root,
                    expected_name=staging.name,
                )
                _cleanup_owned_directory(
                    invocation_root,
                    work_root=temporary_root,
                    expected_name=invocation_root.name,
                )
    return CanaryRenderResult(
        render_root=render_root,
        journal_path=journal_path,
        render_id=render_id,
        rendered_count=rendered_count,
        reused_count=reused_count,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )
