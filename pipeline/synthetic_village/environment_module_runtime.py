"""Fail-closed Blender runtime bridge for ``EnvironmentModulePlan``.

The bridge is deliberately additive.  It consumes one already verified
Windows textured build, appends the 45 stable module instances declared by
``EnvironmentModulePlan``, and produces a private ``modeled-unverified``
Blender scene.  It does not mutate or reinterpret the base schema-v2 report.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from . import canary
from .environment_module import (
    EnvironmentModuleError,
    EnvironmentModulePlan,
    build_default_environment_module_plan,
    environment_module_plan_sha256,
    verify_environment_module_plan,
)

ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_MODULE_RUNTIME_SCHEMA = (
    "nantai.synthetic-village.environment-module-runtime-request.v1"
)
ENVIRONMENT_MODULE_BUILD_REPORT_SCHEMA = (
    "nantai.synthetic-village.environment-module-build-report.v1"
)
ENVIRONMENT_MODULE_RUNTIME_SCRIPT = (
    ROOT / "scripts/blender/apply_environment_modules.py"
)
ENVIRONMENT_MODULE_REQUEST_NAME = "module-build-request.json"
ENVIRONMENT_MODULE_REPORT_NAME = "module-build-report.json"
ENVIRONMENT_MODULE_ARTIFACT_NAME = "village-modules.blend"
ENVIRONMENT_MODULE_BUILD_ENTRIES = (
    ENVIRONMENT_MODULE_REQUEST_NAME,
    ENVIRONMENT_MODULE_REPORT_NAME,
    ENVIRONMENT_MODULE_ARTIFACT_NAME,
)
DEFAULT_ENVIRONMENT_MODULE_BUILD_ROOT = (
    ROOT
    / ".nantai-studio/synthetic-village/hybrid-v4/work/environment-modules"
)
DEFAULT_ENVIRONMENT_MODULE_BUILD_TIMEOUT_SECONDS = 20 * 60

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MaterialAlias = Annotated[
    str,
    StringConstraints(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$"),
]


class EnvironmentModuleRuntimeError(RuntimeError):
    """The module runtime request or measured build cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EnvironmentModuleMaterialBinding(FrozenModel):
    """One explicit plan material alias mapped to an existing verified slot."""

    material_alias: MaterialAlias
    runtime_slot_id: MaterialAlias
    material_family: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    material_id: int = Field(ge=1, le=11)


# This table is a versioned runtime contract, never a filename inference.
# ``material_id`` remains the coarse 11-family root identity used by the base
# renderer, while ``runtime_slot_id`` selects one exact existing PBR material.
_MATERIAL_BINDING_ROWS = (
    (
        "material-courtyard-drain-01",
        "material-shallow-water-01",
        "shallow-water",
    ),
    (
        "material-courtyard-flagstone-01",
        "material-wet-stone-paving-01",
        "wet-stone-paving",
    ),
    (
        "material-courtyard-stone-01",
        "material-fieldstone-01",
        "fieldstone",
    ),
    (
        "material-courtyard-tile-01",
        "material-gray-roof-tile-01",
        "dark-timber",
    ),
    (
        "material-courtyard-timber-01",
        "material-weathered-timber-01",
        "weathered-timber",
    ),
    (
        "material-creek-stone-01",
        "material-creek-rock-01",
        "fieldstone",
    ),
    ("material-service-iron-01", "material-aged-metal-01", "dark-timber"),
    (
        "material-service-stone-01",
        "material-wet-stone-paving-01",
        "wet-stone-paving",
    ),
    (
        "material-service-tile-01",
        "material-gray-roof-tile-01",
        "dark-timber",
    ),
    (
        "material-service-timber-01",
        "material-weathered-timber-01",
        "weathered-timber",
    ),
    ("material-stone-block-01", "material-moss-stone-01", "fieldstone"),
    ("material-water-01", "material-shallow-water-01", "shallow-water"),
    (
        "material-waterwheel-iron-01",
        "material-aged-metal-01",
        "dark-timber",
    ),
    (
        "material-waterwheel-wood-01",
        "material-weathered-timber-01",
        "weathered-timber",
    ),
)


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
            [row.model_dump(mode="json") for row in registry],
        ),
    ).hexdigest()


def _material_bindings(
    material_registry: tuple[canary.MaterialRegistryEntry, ...],
) -> tuple[EnvironmentModuleMaterialBinding, ...]:
    material_ids = {
        row.material_family: row.material_id
        for row in material_registry
    }
    try:
        return tuple(
            EnvironmentModuleMaterialBinding(
                material_alias=alias,
                runtime_slot_id=runtime_slot,
                material_family=family,
                material_id=material_ids[family],
            )
            for alias, runtime_slot, family in _MATERIAL_BINDING_ROWS
        )
    except KeyError as exc:
        raise EnvironmentModuleRuntimeError(
            "verified base material registry cannot satisfy module bindings",
        ) from exc


def _module_registry(
    plan: EnvironmentModulePlan,
    bindings: tuple[EnvironmentModuleMaterialBinding, ...],
) -> tuple[canary.ObjectRegistryEntry, ...]:
    by_alias = {
        row.material_alias: row
        for row in bindings
    }
    rows: list[canary.ObjectRegistryEntry] = []
    try:
        for module in plan.modules:
            for part in module.parts:
                rows.append(
                    canary.ObjectRegistryEntry(
                        object_id=part.part_id,
                        instance_id=part.instance_id,
                        semantic_id=part.semantic_id,
                        material_id=by_alias[part.material_slot_id].material_id,
                        variant_id=None,
                    ),
                )
    except KeyError as exc:
        raise EnvironmentModuleRuntimeError(
            "environment module plan references an unbound material alias",
        ) from exc
    return tuple(rows)


class EnvironmentModuleRuntimeRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.environment-module-runtime-request.v1"
    ] = ENVIRONMENT_MODULE_RUNTIME_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none"] = "none"
    base_build_id: Sha256
    base_build_report_sha256: Sha256
    base_blend_sha256: Sha256
    base_blender_executable_sha256: Sha256
    base_object_registry_sha256: Sha256
    runtime_script_sha256: Sha256
    environment_module_plan_sha256: Sha256
    environment_module_plan: EnvironmentModulePlan
    material_bindings: tuple[EnvironmentModuleMaterialBinding, ...] = Field(
        min_length=len(_MATERIAL_BINDING_ROWS),
        max_length=len(_MATERIAL_BINDING_ROWS),
    )
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=175,
        max_length=175,
    )
    requested_artifact: Literal["village-modules.blend"] = "village-modules.blend"

    @model_validator(mode="after")
    def _identities_are_exact(self) -> EnvironmentModuleRuntimeRequest:
        if (
            self.environment_module_plan_sha256
            != environment_module_plan_sha256(self.environment_module_plan)
        ):
            raise ValueError("environment module plan SHA-256 is not canonical")
        expected_bindings = tuple(
            EnvironmentModuleMaterialBinding(
                material_alias=alias,
                runtime_slot_id=runtime_slot,
                material_family=family,
                material_id={
                    row.material_family: row.material_id
                    for row in self.material_bindings
                }[family],
            )
            for alias, runtime_slot, family in _MATERIAL_BINDING_ROWS
        )
        if self.material_bindings != expected_bindings:
            raise ValueError("environment module material bindings are not exact")
        instances = tuple(row.instance_id for row in self.object_registry)
        if instances != tuple(range(1, 176)):
            raise ValueError("runtime object registry must be exact instances 1..175")
        if len({row.object_id for row in self.object_registry}) != 175:
            raise ValueError("runtime object registry IDs must be unique")
        if (
            self.base_object_registry_sha256
            != _canonical_registry_sha256(self.object_registry[:130])
        ):
            raise ValueError("base object registry digest is not canonical")
        expected_modules = _module_registry(
            self.environment_module_plan,
            self.material_bindings,
        )
        if self.object_registry[130:] != expected_modules:
            raise ValueError("module object registry does not match the plan")
        payload = self.model_dump(mode="json")
        payload.pop("build_id")
        expected_build_id = hashlib.sha256(
            canary._canonical_json_bytes(payload),
        ).hexdigest()
        if self.build_id != expected_build_id:
            raise ValueError("module runtime build_id is not canonical")
        return self


def canonical_environment_module_runtime_request_bytes(
    request: EnvironmentModuleRuntimeRequest,
) -> bytes:
    return canary._canonical_json_bytes(request.model_dump(mode="json"))


def build_environment_module_runtime_request(
    *,
    base_build: object,
    repo_root: Path = ROOT,
    environment_module_plan: EnvironmentModulePlan | None = None,
) -> EnvironmentModuleRuntimeRequest:
    """Bind one verified base build to the exact 45-part module extension."""

    repo_root = Path(repo_root).absolute()
    script_path = repo_root / "scripts/blender/apply_environment_modules.py"
    if not script_path.is_file():
        raise EnvironmentModuleRuntimeError("module Blender runtime script is absent")
    try:
        base_registry = tuple(base_build.object_registry)
        instances = tuple(row.instance_id for row in base_registry)
        if instances != tuple(range(1, 131)) or len(base_registry) != 130:
            raise EnvironmentModuleRuntimeError(
                "verified base object registry must be exact 1..130",
            )
        scene = base_build.request.scene_plan
        topology = base_build.request.elevated_topology
        plan = environment_module_plan or build_default_environment_module_plan(
            scene=scene,
            elevated_topology=topology,
        )
        try:
            verify_environment_module_plan(
                plan,
                scene=scene,
                elevated_topology=topology,
            )
        except EnvironmentModuleError as exc:
            raise EnvironmentModuleRuntimeError(
                "environment module plan does not match verified base scene",
            ) from exc
        report = getattr(base_build, "report", None)
        material_registry = getattr(base_build, "material_registry", None)
        if material_registry is None and report is not None:
            material_registry = report.material_registry
        bindings = _material_bindings(tuple(material_registry))
        registry = (*base_registry, *_module_registry(plan, bindings))
        payload = {
            "schema_version": ENVIRONMENT_MODULE_RUNTIME_SCHEMA,
            "synthetic": True,
            "verification_level": "L0",
            "geometry_usability": "preview-only",
            "stage": "modeled-unverified",
            "trust_effect": "none",
            "base_build_id": base_build.build_id,
            "base_build_report_sha256": base_build.build_report_sha256,
            "base_blend_sha256": base_build.blend_sha256,
            "base_blender_executable_sha256": (
                base_build.blender_executable_sha256
            ),
            "base_object_registry_sha256": _canonical_registry_sha256(
                base_registry,
            ),
            "runtime_script_sha256": _sha256_file(script_path),
            "environment_module_plan_sha256": environment_module_plan_sha256(
                plan,
            ),
            "environment_module_plan": plan,
            "material_bindings": bindings,
            "object_registry": registry,
            "requested_artifact": "village-modules.blend",
        }
    except EnvironmentModuleRuntimeError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise EnvironmentModuleRuntimeError(
            "verified base build cannot satisfy the module runtime contract",
        ) from exc
    build_id = hashlib.sha256(
        canary._canonical_json_bytes(
            {
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
            },
        ),
    ).hexdigest()
    return EnvironmentModuleRuntimeRequest(build_id=build_id, **payload)


class EnvironmentModuleBuildCounts(FrozenModel):
    base_canonical_roots: Literal[130] = 130
    module_canonical_roots: Literal[45] = 45
    canonical_roots: Literal[175] = 175
    module_mesh_objects: int = Field(ge=45)


class EnvironmentModuleBuildValidation(FrozenModel):
    base_registry_matches: Literal[True]
    module_registry_matches: Literal[True]
    finite_nonempty_module_meshes: Literal[True]
    material_bindings_match: Literal[True]
    design_sources_are_provenance_only: Literal[True]


class EnvironmentModuleArtifact(FrozenModel):
    name: Literal["village-modules.blend"]
    kind: Literal["blender-scene"]
    sha256: Sha256
    size_bytes: int = Field(gt=0, le=canary.MAX_ARTIFACT_BYTES)


class EnvironmentModuleBuildReport(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.environment-module-build-report.v1"
    ] = ENVIRONMENT_MODULE_BUILD_REPORT_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none"] = "none"
    base_build_id: Sha256
    base_build_report_sha256: Sha256
    base_blend_sha256: Sha256
    environment_module_plan_sha256: Sha256
    runtime_script_sha256: Sha256
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=175,
        max_length=175,
    )
    material_bindings: tuple[EnvironmentModuleMaterialBinding, ...] = Field(
        min_length=len(_MATERIAL_BINDING_ROWS),
        max_length=len(_MATERIAL_BINDING_ROWS),
    )
    counts: EnvironmentModuleBuildCounts
    validation: EnvironmentModuleBuildValidation
    artifact: EnvironmentModuleArtifact

    @model_validator(mode="after")
    def _registry_is_complete(self) -> EnvironmentModuleBuildReport:
        if tuple(row.instance_id for row in self.object_registry) != tuple(
            range(1, 176),
        ):
            raise ValueError("module build report registry is not exact 1..175")
        return self


def verify_environment_module_build_report(
    report: EnvironmentModuleBuildReport,
    *,
    request: EnvironmentModuleRuntimeRequest,
    output_path: Path,
) -> None:
    """Recompute request/report identities and the measured Blender bytes."""

    identity_pairs = (
        (report.build_id, request.build_id),
        (report.base_build_id, request.base_build_id),
        (
            report.base_build_report_sha256,
            request.base_build_report_sha256,
        ),
        (report.base_blend_sha256, request.base_blend_sha256),
        (
            report.environment_module_plan_sha256,
            request.environment_module_plan_sha256,
        ),
        (report.runtime_script_sha256, request.runtime_script_sha256),
        (report.object_registry, request.object_registry),
        (report.material_bindings, request.material_bindings),
    )
    if any(left != right for left, right in identity_pairs):
        raise EnvironmentModuleRuntimeError("module build report identity disagrees")
    output_path = Path(output_path)
    try:
        size = output_path.stat().st_size
        digest = _sha256_file(output_path)
    except OSError as exc:
        raise EnvironmentModuleRuntimeError(
            "module Blender artifact cannot be measured",
        ) from exc
    if (
        report.artifact.name != output_path.name
        or report.artifact.sha256 != digest
        or report.artifact.size_bytes != size
    ):
        raise EnvironmentModuleRuntimeError(
            "module Blender artifact digest or size disagrees",
        )


def load_environment_module_build_report(
    path: Path,
) -> EnvironmentModuleBuildReport:
    """Load one bounded canonical report while rejecting duplicate JSON keys."""

    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EnvironmentModuleRuntimeError(
            "module build report cannot be read",
        ) from exc
    if not raw or len(raw) > canary.MAX_BUILD_REPORT_BYTES:
        raise EnvironmentModuleRuntimeError(
            "module build report bytes are absent or unbounded",
        )
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        if raw != canary._canonical_json_bytes(parsed):
            raise EnvironmentModuleRuntimeError(
                "module build report is not canonical JSON",
            )
        return EnvironmentModuleBuildReport.model_validate_json(raw)
    except EnvironmentModuleRuntimeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EnvironmentModuleRuntimeError(
            "module build report validation failed",
        ) from exc


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise EnvironmentModuleRuntimeError(
            f"cannot write private module build input: {path.name}",
        ) from exc


def _verify_exact_build_layout(directory: Path) -> None:
    try:
        entries = tuple(directory.iterdir())
    except OSError as exc:
        raise EnvironmentModuleRuntimeError(
            "module build directory cannot be inspected",
        ) from exc
    if (
        {entry.name for entry in entries}
        != set(ENVIRONMENT_MODULE_BUILD_ENTRIES)
        or any(entry.is_symlink() or not entry.is_file() for entry in entries)
    ):
        raise EnvironmentModuleRuntimeError(
            "module build directory is not the exact three-file set",
        )


def _remove_private_staging(staging: Path, *, parent: Path) -> None:
    """Remove only a direct, generated staging child after path verification."""

    try:
        if (
            staging.parent != parent
            or not staging.name.startswith(".staging-")
            or staging.is_symlink()
        ):
            raise EnvironmentModuleRuntimeError(
                "refusing to clean an unverified module staging path",
            )
        if staging.exists():
            shutil.rmtree(staging)
    except EnvironmentModuleRuntimeError:
        raise
    except OSError as exc:
        raise EnvironmentModuleRuntimeError(
            "module staging cleanup failed",
        ) from exc


@dataclass(frozen=True)
class EnvironmentModuleBuildResult:
    final_directory: Path
    request: EnvironmentModuleRuntimeRequest
    report: EnvironmentModuleBuildReport
    stdout: str
    stderr: str


def _verify_existing_build(
    *,
    directory: Path,
    request: EnvironmentModuleRuntimeRequest,
) -> EnvironmentModuleBuildReport:
    _verify_exact_build_layout(directory)
    request_bytes = (directory / ENVIRONMENT_MODULE_REQUEST_NAME).read_bytes()
    if request_bytes != canonical_environment_module_runtime_request_bytes(request):
        raise EnvironmentModuleRuntimeError(
            "existing module build request bytes disagree",
        )
    report = load_environment_module_build_report(
        directory / ENVIRONMENT_MODULE_REPORT_NAME,
    )
    verify_environment_module_build_report(
        report,
        request=request,
        output_path=directory / ENVIRONMENT_MODULE_ARTIFACT_NAME,
    )
    return report


def run_environment_module_build(
    *,
    base_build: object,
    repo_root: Path = ROOT,
    build_root: Path = DEFAULT_ENVIRONMENT_MODULE_BUILD_ROOT,
    environment_module_plan: EnvironmentModulePlan | None = None,
    timeout_seconds: int = DEFAULT_ENVIRONMENT_MODULE_BUILD_TIMEOUT_SECONDS,
) -> EnvironmentModuleBuildResult:
    """Run one real pinned-Blender module build and publish it atomically."""

    repo_root = Path(repo_root).absolute()
    build_root = Path(build_root).absolute()
    if timeout_seconds <= 0:
        raise EnvironmentModuleRuntimeError("module build timeout must be positive")
    request = build_environment_module_runtime_request(
        base_build=base_build,
        repo_root=repo_root,
        environment_module_plan=environment_module_plan,
    )
    try:
        private_root = canary._require_real_directory(
            repo_root / ".nantai-studio",
            label="private project root",
        )
        build_root.mkdir(parents=True, exist_ok=True)
        build_root = canary._require_real_directory(
            build_root,
            label="environment module build root",
        )
        build_root.relative_to(private_root)
    except (OSError, ValueError, canary.CanaryBuildError) as exc:
        raise EnvironmentModuleRuntimeError(
            "module build root must be a real private project directory",
        ) from exc

    final_directory = build_root / request.build_id
    if final_directory.exists():
        if final_directory.is_symlink() or not final_directory.is_dir():
            raise EnvironmentModuleRuntimeError(
                "existing module build path is not a real directory",
            )
        report = _verify_existing_build(
            directory=final_directory,
            request=request,
        )
        return EnvironmentModuleBuildResult(
            final_directory=final_directory,
            request=request,
            report=report,
            stdout="",
            stderr="",
        )

    staging = build_root / f".staging-{request.build_id[:12]}-{uuid.uuid4().hex[:12]}"
    try:
        staging.mkdir()
        request_path = staging / ENVIRONMENT_MODULE_REQUEST_NAME
        _write_exclusive(
            request_path,
            canonical_environment_module_runtime_request_bytes(request),
        )
        executable = Path(base_build.executable).absolute()
        blend_path = Path(base_build.blend_path).absolute()
        script_path = repo_root / "scripts/blender/apply_environment_modules.py"
        snapshots = (
            canary._snapshot_regular_file(executable),
            canary._snapshot_regular_file(blend_path),
            canary._snapshot_regular_file(script_path),
            canary._snapshot_regular_file(request_path),
        )
        completed = subprocess.run(
            [
                str(executable),
                "--background",
                str(blend_path),
                "--python",
                str(script_path),
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
            raise EnvironmentModuleRuntimeError(
                "verified Blender module build failed"
                + (f": {detail[-2000:]}" if detail else ""),
            )
        canary._verify_snapshots_unchanged(snapshots)
        _verify_exact_build_layout(staging)
        report = load_environment_module_build_report(
            staging / ENVIRONMENT_MODULE_REPORT_NAME,
        )
        verify_environment_module_build_report(
            report,
            request=request,
            output_path=staging / ENVIRONMENT_MODULE_ARTIFACT_NAME,
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
                raise EnvironmentModuleRuntimeError(
                    "module build publication failed",
                ) from exc
        return EnvironmentModuleBuildResult(
            final_directory=final_directory,
            request=request,
            report=report,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        raise EnvironmentModuleRuntimeError(
            f"Blender module build exceeded {timeout_seconds} seconds",
        ) from exc
    finally:
        if staging.exists():
            _remove_private_staging(staging, parent=build_root)
