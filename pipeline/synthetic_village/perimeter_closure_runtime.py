"""Fail-closed exact-218 -> exact-266 Blender runtime bridge.

Only the three named exact-218 inputs are consumed.  Additional evidence files
beside them are ignored, while each consumed path is required to be a regular,
non-symlink file and is rebound by content SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
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

from . import canary
from .perimeter_closure_module import (
    PERIMETER_CLOSURE_MODULE_ORDER,
    PerimeterClosurePlan,
    perimeter_closure_plan_sha256,
    verify_perimeter_closure_plan,
)
from .reciprocal_route_module_runtime import (
    RECIPROCAL_ROUTE_ARTIFACT_NAME,
    RECIPROCAL_ROUTE_REPORT_NAME,
    RECIPROCAL_ROUTE_REQUEST_NAME,
    ReciprocalRouteBuildReport,
    ReciprocalRouteMaterialBinding,
    ReciprocalRouteRuntimeError,
    ReciprocalRouteRuntimeRequest,
    load_reciprocal_route_build_report,
    load_reciprocal_route_runtime_request,
    verify_reciprocal_route_build_report,
)

PERIMETER_CLOSURE_RUNTIME_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-runtime-request.v1"
)
PERIMETER_CLOSURE_BUILD_REPORT_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-build-report.v1"
)

ROOT = Path(__file__).resolve().parents[2]
PERIMETER_CLOSURE_RUNTIME_SCRIPT = (
    ROOT / "scripts/blender/apply_perimeter_closure_modules.py"
)
PERIMETER_CLOSURE_REQUEST_NAME = "perimeter-closure-build-request.json"
PERIMETER_CLOSURE_REPORT_NAME = "perimeter-closure-build-report.json"
PERIMETER_CLOSURE_ARTIFACT_NAME = "village-perimeter-closure.blend"
PERIMETER_CLOSURE_BUILD_ENTRIES = (
    PERIMETER_CLOSURE_REQUEST_NAME,
    PERIMETER_CLOSURE_REPORT_NAME,
    PERIMETER_CLOSURE_ARTIFACT_NAME,
)
DEFAULT_PERIMETER_CLOSURE_BUILD_ROOT = (
    ROOT
    / ".nantai-studio/synthetic-village/hybrid-v4/work/perimeter-closure-builds"
)
DEFAULT_PERIMETER_CLOSURE_BUILD_TIMEOUT_SECONDS = 30 * 60

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

_ROLE_SEMANTIC_IDS = {
    "terrain-contact": 8,
    "bidirectional-corridor": 7,
    "support-retaining": 12,
    "drainage-water": 5,
    "boundary-seam": 12,
    "vegetation-enclosure": 10,
}


class PerimeterClosureRuntimeError(RuntimeError):
    """The exact-266 runtime request or measured build cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_registry_sha256(
    registry: tuple[canary.ObjectRegistryEntry, ...],
) -> str:
    return hashlib.sha256(
        canary._canonical_json_bytes(
            [row.model_dump(mode="json") for row in registry]
        )
    ).hexdigest()


def _canonical_material_bindings_sha256(
    bindings: tuple[ReciprocalRouteMaterialBinding, ...],
) -> str:
    return hashlib.sha256(
        canary._canonical_json_bytes(
            [row.model_dump(mode="json") for row in bindings]
        )
    ).hexdigest()


def _overlay_registry(
    plan: PerimeterClosurePlan,
    bindings: tuple[ReciprocalRouteMaterialBinding, ...],
) -> tuple[canary.ObjectRegistryEntry, ...]:
    material_ids = {row.material_alias: row.material_id for row in bindings}
    rows: list[canary.ObjectRegistryEntry] = []
    try:
        for module in plan.modules:
            for part in module.parts:
                rows.append(
                    canary.ObjectRegistryEntry(
                        object_id=part.part_id,
                        instance_id=part.instance_id,
                        semantic_id=_ROLE_SEMANTIC_IDS[part.semantic_role],
                        material_id=material_ids[part.material_slot_id],
                        variant_id=None,
                    )
                )
    except KeyError as exc:
        raise PerimeterClosureRuntimeError(
            "closure plan references an unbound semantic or material identity"
        ) from exc
    return tuple(rows)


class PerimeterClosureRuntimeRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.perimeter-closure-runtime-request.v1"
    ] = PERIMETER_CLOSURE_RUNTIME_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    base_canonical_roots: Literal[218] = 218
    overlay_canonical_roots: Literal[48] = 48
    canonical_roots: Literal[266] = 266

    base_build_id: Sha256
    base_build_request_sha256: Sha256
    base_build_report_sha256: Sha256
    base_blend_sha256: Sha256
    base_object_registry_sha256: Sha256
    base_reciprocal_route_module_plan_sha256: Sha256

    blender_executable_sha256: Sha256
    runtime_script_sha256: Sha256
    batch24_manifest_sha256: Sha256
    perimeter_closure_plan_sha256: Sha256
    material_bindings_sha256: Sha256
    perimeter_closure_plan: PerimeterClosurePlan
    material_bindings: tuple[ReciprocalRouteMaterialBinding, ...] = Field(
        min_length=14,
        max_length=14,
    )
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=266,
        max_length=266,
    )

    max_terrain_support_contact_gap_m: Literal[0.05] = 0.05
    max_corridor_endpoint_gap_m: Literal[0.1] = 0.1
    max_drainage_endpoint_gap_m: Literal[0.1] = 0.1
    max_sector_seam_gap_m: Literal[0.2] = 0.2
    requested_artifact: Literal["village-perimeter-closure.blend"] = (
        PERIMETER_CLOSURE_ARTIFACT_NAME
    )

    @model_validator(mode="after")
    def _identities_are_exact(self) -> PerimeterClosureRuntimeRequest:
        if self.perimeter_closure_plan_sha256 != (
            perimeter_closure_plan_sha256(self.perimeter_closure_plan)
        ):
            raise ValueError("perimeter-closure plan SHA-256 is not canonical")
        instances = tuple(row.instance_id for row in self.object_registry)
        if instances != tuple(range(1, 267)):
            raise ValueError("runtime object registry must be exact instances 1..266")
        if len({row.object_id for row in self.object_registry}) != 266:
            raise ValueError("runtime object registry IDs must be unique")
        base_registry = self.object_registry[:218]
        if _canonical_registry_sha256(base_registry) != (
            self.base_object_registry_sha256
        ):
            raise ValueError("base object registry SHA-256 disagrees")
        if self.object_registry[218:] != _overlay_registry(
            self.perimeter_closure_plan,
            self.material_bindings,
        ):
            raise ValueError("overlay object registry disagrees with closure plan")
        if _canonical_material_bindings_sha256(self.material_bindings) != (
            self.material_bindings_sha256
        ):
            raise ValueError("material bindings SHA-256 disagrees")
        payload = self.model_dump(mode="json")
        payload.pop("build_id")
        expected_build_id = hashlib.sha256(
            canary._canonical_json_bytes(payload)
        ).hexdigest()
        if self.build_id != expected_build_id:
            raise ValueError("perimeter-closure runtime build_id is not canonical")
        return self


def canonical_perimeter_closure_runtime_request_bytes(
    request: PerimeterClosureRuntimeRequest,
) -> bytes:
    return canary._canonical_json_bytes(request.model_dump(mode="json"))


def load_perimeter_closure_runtime_request(
    path: Path,
) -> PerimeterClosureRuntimeRequest:
    path = Path(path)
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > canary.MAX_BUILD_REPORT_BYTES:
            raise PerimeterClosureRuntimeError(
                "perimeter-closure runtime request bytes are absent or unbounded"
            )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        request = PerimeterClosureRuntimeRequest.model_validate_json(raw)
        if raw != canonical_perimeter_closure_runtime_request_bytes(request):
            raise PerimeterClosureRuntimeError(
                "perimeter-closure runtime request is not canonical JSON"
            )
        return request
    except PerimeterClosureRuntimeError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        ValidationError,
        canary.CanaryBuildError,
    ) as exc:
        raise PerimeterClosureRuntimeError(
            "perimeter-closure runtime request validation failed"
        ) from exc


class PerimeterClosureBuildCounts(FrozenModel):
    base_canonical_roots: Literal[218] = 218
    overlay_canonical_roots: Literal[48] = 48
    canonical_roots: Literal[266] = 266
    overlay_mesh_objects: Literal[48]
    textured_overlay_meshes: Literal[48]
    valid_uv_overlay_meshes: Literal[48]
    valid_surface_color_overlay_meshes: Literal[48]


class PerimeterClosureBuildValidation(FrozenModel):
    base_registry_preserved: Literal[True]
    overlay_registry_exact: Literal[True]
    finite_nonempty_overlay_meshes: Literal[True]
    material_bindings_exact: Literal[True]
    design_sources_provenance_only: Literal[True]
    terrain_support_contacts_passed: Literal[True]
    corridor_continuity_passed: Literal[True]
    drainage_continuity_passed: Literal[True]
    sector_seams_passed: Literal[True]


class PerimeterClosureSectorMeasurement(FrozenModel):
    module_id: Literal[
        "closure-upstream",
        "closure-northeast",
        "closure-east",
        "closure-southeast",
        "closure-downstream",
        "closure-southwest",
        "closure-west",
        "closure-northwest",
    ]
    terrain_support_contact_gap_m: float = Field(ge=0.0)
    corridor_endpoint_gap_m: float = Field(ge=0.0)
    drainage_endpoint_gap_m: float = Field(ge=0.0)
    previous_seam_gap_m: float = Field(ge=0.0)
    next_seam_gap_m: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _measurements_are_finite(self) -> PerimeterClosureSectorMeasurement:
        fields = (
            self.terrain_support_contact_gap_m,
            self.corridor_endpoint_gap_m,
            self.drainage_endpoint_gap_m,
            self.previous_seam_gap_m,
            self.next_seam_gap_m,
        )
        if not all(math.isfinite(value) for value in fields):
            raise ValueError("sector measurements must be finite")
        return self


class PerimeterClosureArtifact(FrozenModel):
    name: Literal["village-perimeter-closure.blend"]
    kind: Literal["blender-scene"]
    sha256: Sha256
    size_bytes: int = Field(gt=0, le=canary.MAX_ARTIFACT_BYTES)


class PerimeterClosureBuildReport(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.perimeter-closure-build-report.v1"
    ] = PERIMETER_CLOSURE_BUILD_REPORT_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    base_build_id: Sha256
    base_build_request_sha256: Sha256
    base_build_report_sha256: Sha256
    base_blend_sha256: Sha256
    base_object_registry_sha256: Sha256
    base_reciprocal_route_module_plan_sha256: Sha256
    blender_executable_sha256: Sha256
    runtime_script_sha256: Sha256
    batch24_manifest_sha256: Sha256
    perimeter_closure_plan_sha256: Sha256
    material_bindings_sha256: Sha256
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=266,
        max_length=266,
    )
    material_bindings: tuple[ReciprocalRouteMaterialBinding, ...] = Field(
        min_length=14,
        max_length=14,
    )
    counts: PerimeterClosureBuildCounts
    validation: PerimeterClosureBuildValidation
    sector_measurements: tuple[PerimeterClosureSectorMeasurement, ...] = Field(
        min_length=8,
        max_length=8,
    )
    artifact: PerimeterClosureArtifact

    @model_validator(mode="after")
    def _report_is_exact(self) -> PerimeterClosureBuildReport:
        if tuple(row.instance_id for row in self.object_registry) != tuple(
            range(1, 267)
        ):
            raise ValueError("build report registry must be exact instances 1..266")
        if tuple(row.module_id for row in self.sector_measurements) != (
            PERIMETER_CLOSURE_MODULE_ORDER
        ):
            raise ValueError("sector measurements must be exact and ordered")
        return self


def canonical_perimeter_closure_build_report_bytes(
    report: PerimeterClosureBuildReport,
) -> bytes:
    return canary._canonical_json_bytes(report.model_dump(mode="json"))


def load_perimeter_closure_build_report(
    path: Path,
) -> PerimeterClosureBuildReport:
    path = Path(path)
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > canary.MAX_BUILD_REPORT_BYTES:
            raise PerimeterClosureRuntimeError(
                "perimeter-closure build report bytes are absent or unbounded"
            )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        report = PerimeterClosureBuildReport.model_validate_json(raw)
        if raw != canonical_perimeter_closure_build_report_bytes(report):
            raise PerimeterClosureRuntimeError(
                "perimeter-closure build report is not canonical JSON"
            )
        return report
    except PerimeterClosureRuntimeError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        ValidationError,
        canary.CanaryBuildError,
    ) as exc:
        raise PerimeterClosureRuntimeError(
            "perimeter-closure build report validation failed"
        ) from exc


def verify_perimeter_closure_build_report(
    report: PerimeterClosureBuildReport,
    *,
    request: PerimeterClosureRuntimeRequest,
    output_path: Path,
) -> None:
    identity_pairs = (
        (report.build_id, request.build_id),
        (report.base_build_id, request.base_build_id),
        (report.base_build_request_sha256, request.base_build_request_sha256),
        (report.base_build_report_sha256, request.base_build_report_sha256),
        (report.base_blend_sha256, request.base_blend_sha256),
        (report.base_object_registry_sha256, request.base_object_registry_sha256),
        (
            report.base_reciprocal_route_module_plan_sha256,
            request.base_reciprocal_route_module_plan_sha256,
        ),
        (report.blender_executable_sha256, request.blender_executable_sha256),
        (report.runtime_script_sha256, request.runtime_script_sha256),
        (report.batch24_manifest_sha256, request.batch24_manifest_sha256),
        (
            report.perimeter_closure_plan_sha256,
            request.perimeter_closure_plan_sha256,
        ),
        (report.material_bindings_sha256, request.material_bindings_sha256),
        (report.object_registry, request.object_registry),
        (report.material_bindings, request.material_bindings),
    )
    if any(left != right for left, right in identity_pairs):
        raise PerimeterClosureRuntimeError(
            "perimeter-closure build report identity disagrees"
        )
    for measurement in report.sector_measurements:
        if measurement.terrain_support_contact_gap_m > (
            request.max_terrain_support_contact_gap_m
        ):
            raise PerimeterClosureRuntimeError(
                f"{measurement.module_id} terrain/support contact exceeds tolerance"
            )
        if measurement.corridor_endpoint_gap_m > (
            request.max_corridor_endpoint_gap_m
        ):
            raise PerimeterClosureRuntimeError(
                f"{measurement.module_id} corridor continuity exceeds tolerance"
            )
        if measurement.drainage_endpoint_gap_m > (
            request.max_drainage_endpoint_gap_m
        ):
            raise PerimeterClosureRuntimeError(
                f"{measurement.module_id} drainage continuity exceeds tolerance"
            )
        if max(
            measurement.previous_seam_gap_m,
            measurement.next_seam_gap_m,
        ) > request.max_sector_seam_gap_m:
            raise PerimeterClosureRuntimeError(
                f"{measurement.module_id} sector seam exceeds tolerance"
            )
    output_path = Path(output_path)
    try:
        size = output_path.stat().st_size
        digest = _sha256_file(output_path)
    except OSError as exc:
        raise PerimeterClosureRuntimeError(
            "perimeter-closure Blender artifact cannot be measured"
        ) from exc
    if (
        report.artifact.name != output_path.name
        or report.artifact.sha256 != digest
        or report.artifact.size_bytes != size
    ):
        raise PerimeterClosureRuntimeError(
            "perimeter-closure Blender artifact digest or size disagrees"
        )


@dataclass(frozen=True)
class _VerifiedBase:
    directory: Path
    request_path: Path
    report_path: Path
    blend_path: Path
    request: ReciprocalRouteRuntimeRequest
    report: ReciprocalRouteBuildReport


def _require_regular_file(path: Path, *, label: str) -> Path:
    try:
        if path.is_symlink() or not path.is_file():
            raise PerimeterClosureRuntimeError(f"{label} must be a regular file")
        return path
    except OSError as exc:
        raise PerimeterClosureRuntimeError(f"{label} cannot be inspected") from exc


def _load_verified_base(base_build_directory: Path) -> _VerifiedBase:
    directory = Path(base_build_directory).absolute()
    try:
        if directory.is_symlink() or not directory.is_dir():
            raise PerimeterClosureRuntimeError(
                "exact-218 base build must be a real directory"
            )
    except OSError as exc:
        raise PerimeterClosureRuntimeError(
            "exact-218 base build cannot be inspected"
        ) from exc
    request_path = _require_regular_file(
        directory / RECIPROCAL_ROUTE_REQUEST_NAME,
        label="exact-218 request",
    )
    report_path = _require_regular_file(
        directory / RECIPROCAL_ROUTE_REPORT_NAME,
        label="exact-218 report",
    )
    blend_path = _require_regular_file(
        directory / RECIPROCAL_ROUTE_ARTIFACT_NAME,
        label="exact-218 Blender artifact",
    )
    try:
        request = load_reciprocal_route_runtime_request(request_path)
        report = load_reciprocal_route_build_report(report_path)
        verify_reciprocal_route_build_report(
            report,
            request=request,
            output_path=blend_path,
        )
    except ReciprocalRouteRuntimeError as exc:
        raise PerimeterClosureRuntimeError(
            "exact-218 request/report/artifact verification failed"
        ) from exc
    if directory.name != request.build_id:
        raise PerimeterClosureRuntimeError(
            "exact-218 build directory name disagrees with build id"
        )
    if len(report.object_registry) != 218:
        raise PerimeterClosureRuntimeError(
            "exact-218 base registry must contain 218 rows"
        )
    return _VerifiedBase(
        directory=directory,
        request_path=request_path,
        report_path=report_path,
        blend_path=blend_path,
        request=request,
        report=report,
    )


def _load_batch24_manifest(path: Path) -> tuple[dict[str, object], str]:
    path = _require_regular_file(
        Path(path).absolute(),
        label="Batch24 manifest",
    )
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > canary.MAX_BUILD_REPORT_BYTES:
            raise PerimeterClosureRuntimeError(
                "Batch24 manifest bytes are absent or unbounded"
            )
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        if not isinstance(payload, dict):
            raise PerimeterClosureRuntimeError(
                "Batch24 manifest root must be an object"
            )
        return payload, hashlib.sha256(raw).hexdigest()
    except PerimeterClosureRuntimeError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        canary.CanaryBuildError,
    ) as exc:
        raise PerimeterClosureRuntimeError(
            "Batch24 manifest validation failed"
        ) from exc


def build_perimeter_closure_runtime_request(
    *,
    base_build_directory: Path,
    plan: PerimeterClosurePlan,
    batch24_manifest_path: Path,
    blender_executable: Path,
    repo_root: Path = ROOT,
) -> PerimeterClosureRuntimeRequest:
    """Construct a content-addressed exact-266 request from explicit inputs."""

    base = _load_verified_base(base_build_directory)
    manifest, manifest_sha256 = _load_batch24_manifest(batch24_manifest_path)
    if manifest_sha256 != plan.batch24_manifest_sha256:
        raise PerimeterClosureRuntimeError(
            "Batch24 manifest bytes disagree with closure plan"
        )
    try:
        verify_perimeter_closure_plan(plan, batch24_manifest=manifest)
    except ValueError as exc:
        raise PerimeterClosureRuntimeError(
            "closure plan disagrees with Batch24 manifest"
        ) from exc

    executable = _require_regular_file(
        Path(blender_executable).absolute(),
        label="Blender executable",
    )
    executable_sha256 = _sha256_file(executable)
    if executable_sha256 != base.request.base_blender_executable_sha256:
        raise PerimeterClosureRuntimeError(
            "Blender executable disagrees with exact-218 runtime identity"
        )
    script_path = _require_regular_file(
        Path(repo_root).absolute()
        / "scripts/blender/apply_perimeter_closure_modules.py",
        label="perimeter-closure runtime script",
    )
    base_registry = tuple(base.report.object_registry)
    bindings = tuple(base.report.material_bindings)
    object_registry = (*base_registry, *_overlay_registry(plan, bindings))
    payload = {
        "schema_version": PERIMETER_CLOSURE_RUNTIME_SCHEMA,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none-quality-filter-only",
        "base_canonical_roots": 218,
        "overlay_canonical_roots": 48,
        "canonical_roots": 266,
        "base_build_id": base.request.build_id,
        "base_build_request_sha256": _sha256_file(base.request_path),
        "base_build_report_sha256": _sha256_file(base.report_path),
        "base_blend_sha256": _sha256_file(base.blend_path),
        "base_object_registry_sha256": _canonical_registry_sha256(base_registry),
        "base_reciprocal_route_module_plan_sha256": (
            base.request.reciprocal_route_module_plan_sha256
        ),
        "blender_executable_sha256": executable_sha256,
        "runtime_script_sha256": _sha256_file(script_path),
        "batch24_manifest_sha256": manifest_sha256,
        "perimeter_closure_plan_sha256": perimeter_closure_plan_sha256(plan),
        "material_bindings_sha256": _canonical_material_bindings_sha256(
            bindings
        ),
        "perimeter_closure_plan": plan,
        "material_bindings": bindings,
        "object_registry": object_registry,
        "max_terrain_support_contact_gap_m": 0.05,
        "max_corridor_endpoint_gap_m": 0.1,
        "max_drainage_endpoint_gap_m": 0.1,
        "max_sector_seam_gap_m": 0.2,
        "requested_artifact": PERIMETER_CLOSURE_ARTIFACT_NAME,
    }
    payload_for_id = {
        key: (
            value.model_dump(mode="json")
            if isinstance(value, BaseModel)
            else [
                item.model_dump(mode="json")
                if isinstance(item, BaseModel)
                else item
                for item in value
            ]
            if isinstance(value, tuple)
            else value
        )
        for key, value in payload.items()
    }
    build_id = hashlib.sha256(
        canary._canonical_json_bytes(payload_for_id)
    ).hexdigest()
    try:
        return PerimeterClosureRuntimeRequest(build_id=build_id, **payload)
    except (ValidationError, ValueError) as exc:
        raise PerimeterClosureRuntimeError(
            "perimeter-closure runtime request construction failed"
        ) from exc


@dataclass(frozen=True)
class PerimeterClosureBuildResult:
    final_directory: Path
    request: PerimeterClosureRuntimeRequest
    report: PerimeterClosureBuildReport
    stdout: str
    stderr: str


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise PerimeterClosureRuntimeError(
            f"cannot write private perimeter-closure input: {path.name}"
        ) from exc


def _verify_exact_output_layout(directory: Path) -> None:
    try:
        entries = tuple(directory.iterdir())
    except OSError as exc:
        raise PerimeterClosureRuntimeError(
            "perimeter-closure build directory cannot be inspected"
        ) from exc
    if (
        {entry.name for entry in entries} != set(PERIMETER_CLOSURE_BUILD_ENTRIES)
        or any(entry.is_symlink() or not entry.is_file() for entry in entries)
    ):
        raise PerimeterClosureRuntimeError(
            "perimeter-closure build is not the exact three-file set"
        )


def _remove_private_staging(staging: Path, *, parent: Path) -> None:
    try:
        if (
            staging.parent != parent
            or not staging.name.startswith(".staging-")
            or staging.is_symlink()
        ):
            raise PerimeterClosureRuntimeError(
                "refusing to clean an unverified perimeter-closure staging path"
            )
        if staging.exists():
            shutil.rmtree(staging)
    except PerimeterClosureRuntimeError:
        raise
    except OSError as exc:
        raise PerimeterClosureRuntimeError(
            "perimeter-closure staging cleanup failed"
        ) from exc


def _verify_existing_build(
    *,
    directory: Path,
    request: PerimeterClosureRuntimeRequest,
) -> PerimeterClosureBuildReport:
    _verify_exact_output_layout(directory)
    if (directory / PERIMETER_CLOSURE_REQUEST_NAME).read_bytes() != (
        canonical_perimeter_closure_runtime_request_bytes(request)
    ):
        raise PerimeterClosureRuntimeError(
            "existing perimeter-closure request bytes disagree"
        )
    report = load_perimeter_closure_build_report(
        directory / PERIMETER_CLOSURE_REPORT_NAME
    )
    verify_perimeter_closure_build_report(
        report,
        request=request,
        output_path=directory / PERIMETER_CLOSURE_ARTIFACT_NAME,
    )
    return report


def _verify_runner_inputs(
    request: PerimeterClosureRuntimeRequest,
    *,
    base_build_directory: Path,
    blender_executable: Path,
    repo_root: Path,
) -> tuple[_VerifiedBase, Path, Path]:
    base = _load_verified_base(base_build_directory)
    expected_pairs = (
        (base.request.build_id, request.base_build_id),
        (_sha256_file(base.request_path), request.base_build_request_sha256),
        (_sha256_file(base.report_path), request.base_build_report_sha256),
        (_sha256_file(base.blend_path), request.base_blend_sha256),
        (
            _canonical_registry_sha256(tuple(base.report.object_registry)),
            request.base_object_registry_sha256,
        ),
    )
    if any(left != right for left, right in expected_pairs):
        raise PerimeterClosureRuntimeError(
            "runner exact-218 inputs disagree with request"
        )
    executable = _require_regular_file(
        Path(blender_executable).absolute(),
        label="Blender executable",
    )
    script = _require_regular_file(
        Path(repo_root).absolute()
        / "scripts/blender/apply_perimeter_closure_modules.py",
        label="perimeter-closure runtime script",
    )
    if (
        _sha256_file(executable) != request.blender_executable_sha256
        or _sha256_file(script) != request.runtime_script_sha256
    ):
        raise PerimeterClosureRuntimeError(
            "runner executable or script identity disagrees with request"
        )
    return base, executable, script


def run_perimeter_closure_build(
    request: PerimeterClosureRuntimeRequest,
    *,
    base_build_directory: Path,
    blender_executable: Path,
    repo_root: Path = ROOT,
    build_root: Path = DEFAULT_PERIMETER_CLOSURE_BUILD_ROOT,
    timeout_seconds: int = DEFAULT_PERIMETER_CLOSURE_BUILD_TIMEOUT_SECONDS,
) -> PerimeterClosureBuildResult:
    """Run pinned Blender and atomically publish an exact three-file build."""

    if timeout_seconds <= 0:
        raise PerimeterClosureRuntimeError(
            "perimeter-closure build timeout must be positive"
        )
    repo_root = Path(repo_root).absolute()
    base, executable, script = _verify_runner_inputs(
        request,
        base_build_directory=base_build_directory,
        blender_executable=blender_executable,
        repo_root=repo_root,
    )
    private_root = repo_root / ".nantai-studio"
    build_root = Path(build_root).absolute()
    try:
        private_root.mkdir(parents=True, exist_ok=True)
        if private_root.is_symlink() or not private_root.is_dir():
            raise PerimeterClosureRuntimeError(
                "private project root must be a real directory"
            )
        build_root.mkdir(parents=True, exist_ok=True)
        if build_root.is_symlink() or not build_root.is_dir():
            raise PerimeterClosureRuntimeError(
                "perimeter-closure build root must be a real directory"
            )
        build_root.relative_to(private_root)
    except (OSError, ValueError) as exc:
        raise PerimeterClosureRuntimeError(
            "perimeter-closure build root must stay under repo .nantai-studio"
        ) from exc

    final_directory = build_root / request.build_id
    if final_directory.exists():
        if final_directory.is_symlink() or not final_directory.is_dir():
            raise PerimeterClosureRuntimeError(
                "existing perimeter-closure build path is not a real directory"
            )
        report = _verify_existing_build(
            directory=final_directory,
            request=request,
        )
        return PerimeterClosureBuildResult(
            final_directory=final_directory,
            request=request,
            report=report,
            stdout="",
            stderr="",
        )

    staging = build_root / (
        f".staging-{request.build_id[:12]}-{uuid.uuid4().hex[:12]}"
    )
    try:
        staging.mkdir()
        request_path = staging / PERIMETER_CLOSURE_REQUEST_NAME
        _write_exclusive(
            request_path,
            canonical_perimeter_closure_runtime_request_bytes(request),
        )
        snapshots = (
            canary._snapshot_regular_file(executable),
            canary._snapshot_regular_file(base.request_path),
            canary._snapshot_regular_file(base.report_path),
            canary._snapshot_regular_file(base.blend_path),
            canary._snapshot_regular_file(script),
            canary._snapshot_regular_file(request_path),
        )
        completed = subprocess.run(
            [
                str(executable),
                "--background",
                str(base.blend_path),
                "--python",
                str(script),
                "--",
                str(request_path),
                str(staging),
            ],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        stdout = completed.stdout[-canary.MAX_PROCESS_LOG_BYTES :]
        stderr = completed.stderr[-canary.MAX_PROCESS_LOG_BYTES :]
        if completed.returncode != 0:
            detail = (stderr or stdout).strip()
            raise PerimeterClosureRuntimeError(
                "verified Blender perimeter-closure build failed"
                + (f": {detail[-2000:]}" if detail else "")
            )
        canary._verify_snapshots_unchanged(snapshots)
        _verify_exact_output_layout(staging)
        report = load_perimeter_closure_build_report(
            staging / PERIMETER_CLOSURE_REPORT_NAME
        )
        verify_perimeter_closure_build_report(
            report,
            request=request,
            output_path=staging / PERIMETER_CLOSURE_ARTIFACT_NAME,
        )
        try:
            staging.rename(final_directory)
        except OSError as exc:
            if final_directory.is_dir() and not final_directory.is_symlink():
                report = _verify_existing_build(
                    directory=final_directory,
                    request=request,
                )
                _remove_private_staging(staging, parent=build_root)
            else:
                raise PerimeterClosureRuntimeError(
                    "perimeter-closure build publication failed"
                ) from exc
        return PerimeterClosureBuildResult(
            final_directory=final_directory,
            request=request,
            report=report,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        raise PerimeterClosureRuntimeError(
            f"Blender perimeter-closure build exceeded {timeout_seconds} seconds"
        ) from exc
    finally:
        if staging.exists():
            _remove_private_staging(staging, parent=build_root)


__all__ = [
    "DEFAULT_PERIMETER_CLOSURE_BUILD_ROOT",
    "DEFAULT_PERIMETER_CLOSURE_BUILD_TIMEOUT_SECONDS",
    "PERIMETER_CLOSURE_ARTIFACT_NAME",
    "PERIMETER_CLOSURE_BUILD_ENTRIES",
    "PERIMETER_CLOSURE_BUILD_REPORT_SCHEMA",
    "PERIMETER_CLOSURE_REPORT_NAME",
    "PERIMETER_CLOSURE_REQUEST_NAME",
    "PERIMETER_CLOSURE_RUNTIME_SCHEMA",
    "PERIMETER_CLOSURE_RUNTIME_SCRIPT",
    "PerimeterClosureArtifact",
    "PerimeterClosureBuildCounts",
    "PerimeterClosureBuildReport",
    "PerimeterClosureBuildResult",
    "PerimeterClosureBuildValidation",
    "PerimeterClosureRuntimeError",
    "PerimeterClosureRuntimeRequest",
    "PerimeterClosureSectorMeasurement",
    "build_perimeter_closure_runtime_request",
    "canonical_perimeter_closure_build_report_bytes",
    "canonical_perimeter_closure_runtime_request_bytes",
    "load_perimeter_closure_build_report",
    "load_perimeter_closure_runtime_request",
    "run_perimeter_closure_build",
    "verify_perimeter_closure_build_report",
]
