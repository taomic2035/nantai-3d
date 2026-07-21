"""Fail-closed Blender runtime bridge for ``ReciprocalRouteModulePlan``.

This module is the additive runtime counterpart of
``reciprocal_route_module.py``: it extends one verified 175-root
environment-module build with the 43 stable reciprocal-route instances
declared by ``ReciprocalRouteModulePlan``, producing a private
``modeled-unverified`` Blender scene of 218 canonical roots.

Layered schema + bridge (HANDOFF-OPUS-009 Phase 2 + Phase 3):
  * ``ReciprocalRouteRuntimeRequest`` -- the canonical request consumed
    by ``scripts/blender/apply_reciprocal_route_modules.py``.  Built
    from a verified 175-root environment-module build +
    ``ReciprocalRouteModulePlan``.
  * ``ReciprocalRouteBuildReport`` -- the canonical report emitted by
    the Blender runtime script.  Carries identity pairs bound to the
    request, plus the measured Blender artifact bytes.
  * ``verify_reciprocal_route_build_report`` -- 9 identity pair
    comparisons + measured bytes recomputation.
  * ``build_reciprocal_route_runtime_request`` -- constructs the
    content-addressed request from a verified 175-root env-module build
    (Phase 3).
  * ``run_reciprocal_route_build`` -- runs the pinned Blender subprocess,
    content-addressed reuse, atomic publication (Phase 3).

Provenance contract (TDD-locked, additive-only):
  * ``synthetic=true``, ``geometry_usability=preview-only``,
    ``verification_level=L0``, ``stage=modeled-unverified``,
    ``trust_effect=none``.
  * ``ReciprocalRouteModulePlan`` v1 instance segment (176..218) is
    appended to the verified base registry segment (1..175), producing
    a 218-root scene with no overlap and no gaps.
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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from . import canary
from .environment_module import environment_module_plan_sha256
from .environment_module_runtime import _MATERIAL_BINDING_ROWS
from .reciprocal_route_module import (
    ReciprocalRouteError,
    ReciprocalRouteModulePlan,
    build_default_reciprocal_route_module_plan,
    reciprocal_route_module_plan_sha256,
    verify_reciprocal_route_module_plan,
)

RECIPROCAL_ROUTE_RUNTIME_SCHEMA = (
    "nantai.synthetic-village.reciprocal-route-runtime-request.v1"
)
RECIPROCAL_ROUTE_BUILD_REPORT_SCHEMA = (
    "nantai.synthetic-village.reciprocal-route-build-report.v1"
)

ROOT = Path(__file__).resolve().parents[2]

#: Runtime script measured by ``build_reciprocal_route_runtime_request``.
RECIPROCAL_ROUTE_RUNTIME_SCRIPT = (
    ROOT / "scripts/blender/apply_reciprocal_route_modules.py"
)
RECIPROCAL_ROUTE_REQUEST_NAME = "reciprocal-route-build-request.json"
RECIPROCAL_ROUTE_REPORT_NAME = "reciprocal-route-build-report.json"
RECIPROCAL_ROUTE_ARTIFACT_NAME = "village-reciprocal-route.blend"
RECIPROCAL_ROUTE_BUILD_ENTRIES = (
    RECIPROCAL_ROUTE_REQUEST_NAME,
    RECIPROCAL_ROUTE_REPORT_NAME,
    RECIPROCAL_ROUTE_ARTIFACT_NAME,
)
DEFAULT_RECIPROCAL_ROUTE_BUILD_ROOT = (
    ROOT
    / ".nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules"
)
DEFAULT_RECIPROCAL_ROUTE_BUILD_TIMEOUT_SECONDS = 20 * 60

#: The 43 new parts declared by ReciprocalRouteModulePlan v1.  These
#: extend the 175-root base scene to 218 roots.  Literal-locked so the
#: report cannot lie about the part count.
RECIPROCAL_ROUTE_MODULE_CANONICAL_ROOTS = 43
RECIPROCAL_ROUTE_FULL_CANONICAL_ROOTS = 218

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MaterialAlias = Annotated[
    str,
    StringConstraints(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$"),
]


class ReciprocalRouteRuntimeError(RuntimeError):
    """The reciprocal-route runtime request or measured build cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReciprocalRouteMaterialBinding(FrozenModel):
    """One explicit plan material alias mapped to an existing verified slot.

    Mirrors ``EnvironmentModuleMaterialBinding``: the binding is a
    versioned runtime contract, never a filename inference.
    """

    material_alias: MaterialAlias
    runtime_slot_id: MaterialAlias
    material_family: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    material_id: int = Field(ge=1, le=11)


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


class ReciprocalRouteRuntimeRequest(FrozenModel):
    """Canonical request consumed by ``apply_reciprocal_route_modules.py``.

    Built from one verified 175-root environment-module build +
    ``ReciprocalRouteModulePlan`` v1.  The request is fully
    content-addressed: ``build_id`` is the SHA-256 of the canonical
    payload (excluding itself), so any field tampering changes
    ``build_id``.
    """

    schema_version: Literal[
        "nantai.synthetic-village.reciprocal-route-runtime-request.v1"
    ] = RECIPROCAL_ROUTE_RUNTIME_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none"] = "none"

    # Verified base 175-root environment-module build identity.
    base_build_id: Sha256
    base_build_report_sha256: Sha256
    base_blend_sha256: Sha256
    base_blender_executable_sha256: Sha256
    base_object_registry_sha256: Sha256
    base_environment_module_plan_sha256: Sha256

    # Runtime script that will execute the build.
    runtime_script_sha256: Sha256

    # Reciprocal-route plan identity.
    reciprocal_route_module_plan_sha256: Sha256
    reciprocal_route_module_plan: ReciprocalRouteModulePlan

    # Material bindings inherited from the base 175-root scene.  These
    # are the same aliases the environment-module runtime locked; the
    # reciprocal-route parts reuse them.  Length is hard-locked to the
    # 14-row alias table so a non-default plan cannot bypass the binding
    # table by passing fewer slots.
    material_bindings: tuple[
        ReciprocalRouteMaterialBinding, ...
    ] = Field(
        min_length=len(_MATERIAL_BINDING_ROWS),
        max_length=len(_MATERIAL_BINDING_ROWS),
    )

    # Full 218-root object registry.  Indices 0..174 must equal the
    # verified base registry; indices 175..217 must equal the
    # ReciprocalRouteModulePlan-declared parts.
    object_registry: tuple[
        canary.ObjectRegistryEntry, ...
    ] = Field(min_length=218, max_length=218)

    requested_artifact: Literal["village-reciprocal-route.blend"] = (
        "village-reciprocal-route.blend"
    )

    @model_validator(mode="after")
    def _identities_are_exact(self) -> ReciprocalRouteRuntimeRequest:
        # 1. ReciprocalRouteModulePlan SHA must match the embedded plan.
        if (
            self.reciprocal_route_module_plan_sha256
            != reciprocal_route_module_plan_sha256(
                self.reciprocal_route_module_plan,
            )
        ):
            raise ValueError(
                "reciprocal-route module plan SHA-256 is not canonical",
            )

        # 2. Object registry must be the exact 218 instance segment.
        instances = tuple(row.instance_id for row in self.object_registry)
        if instances != tuple(range(1, 219)):
            raise ValueError(
                "runtime object registry must be exact instances 1..218",
            )

        # 3. Object IDs must be unique.
        if len({row.object_id for row in self.object_registry}) != 218:
            raise ValueError(
                "runtime object registry IDs must be unique",
            )

        # 4. Base registry digest must match the first 175 entries.
        if (
            self.base_object_registry_sha256
            != _canonical_registry_sha256(self.object_registry[:175])
        ):
            raise ValueError(
                "base object registry digest is not canonical",
            )

        # 5. Base environment-module plan SHA must match the embedded plan's
        #    ``environment_module_plan_sha256`` field (transitive binding).
        if (
            self.base_environment_module_plan_sha256
            != self.reciprocal_route_module_plan.environment_module_plan_sha256
        ):
            raise ValueError(
                "base environment-module plan SHA-256 disagrees with "
                "the reciprocal-route plan's binding",
            )

        # 6. Module-declared parts (indices 175..217) must equal the
        #    plan-derived registry.  Each part's (object_id, instance_id,
        #    semantic_id, material_id) must match.
        plan_part_by_instance: dict[int, object] = {}
        for module in self.reciprocal_route_module_plan.modules:
            for part in module.parts:
                plan_part_by_instance[part.instance_id] = part
        material_id_by_alias = {
            row.material_alias: row.material_id
            for row in self.material_bindings
        }
        for registry_row in self.object_registry[175:]:
            part = plan_part_by_instance.get(registry_row.instance_id)
            if part is None:
                raise ValueError(
                    f"runtime object registry instance "
                    f"{registry_row.instance_id} is not in the plan",
                )
            if registry_row.object_id != part.part_id:
                raise ValueError(
                    f"runtime object registry instance "
                    f"{registry_row.instance_id} object_id disagrees "
                    f"with the plan",
                )
            if registry_row.semantic_id != part.semantic_id:
                raise ValueError(
                    f"runtime object registry instance "
                    f"{registry_row.instance_id} semantic_id disagrees "
                    f"with the plan",
                )
            try:
                expected_material_id = material_id_by_alias[
                    part.material_slot_id
                ]
            except KeyError as exc:
                raise ValueError(
                    "reciprocal-route plan references an unbound "
                    "material alias",
                ) from exc
            if registry_row.material_id != expected_material_id:
                raise ValueError(
                    f"runtime object registry instance "
                    f"{registry_row.instance_id} material_id disagrees "
                    f"with the plan",
                )

        # 7. build_id must be the canonical payload digest (excluding itself).
        payload = self.model_dump(mode="json")
        payload.pop("build_id")
        expected_build_id = hashlib.sha256(
            canary._canonical_json_bytes(payload),
        ).hexdigest()
        if self.build_id != expected_build_id:
            raise ValueError(
                "reciprocal-route runtime build_id is not canonical",
            )
        return self


def canonical_reciprocal_route_runtime_request_bytes(
    request: ReciprocalRouteRuntimeRequest,
) -> bytes:
    return canary._canonical_json_bytes(request.model_dump(mode="json"))


class ReciprocalRouteBuildCounts(FrozenModel):
    """Count summary for the reciprocal-route build report.

    ``base_canonical_roots`` is the verified 175-root base scene count
    (Literal-locked).  ``module_canonical_roots`` is the new
    ReciprocalRouteModulePlan v1 part count (Literal-locked at 43).
    ``canonical_roots`` is the additive total (Literal-locked at 218).
    """

    base_canonical_roots: Literal[175] = 175
    module_canonical_roots: Literal[43] = 43
    canonical_roots: Literal[218] = 218
    module_mesh_objects: int = Field(ge=43)


class ReciprocalRouteBuildValidation(FrozenModel):
    """Per-rule validation summary for the reciprocal-route build report.

    All five rules are ``Literal[True]`` -- the runtime script must
    pass each one or refuse to emit a report.
    """

    base_registry_matches: Literal[True]
    module_registry_matches: Literal[True]
    finite_nonempty_module_meshes: Literal[True]
    material_bindings_match: Literal[True]
    design_sources_are_provenance_only: Literal[True]


class ReciprocalRouteArtifact(FrozenModel):
    """Measured Blender artifact emitted by the runtime script."""

    name: Literal["village-reciprocal-route.blend"]
    kind: Literal["blender-scene"]
    sha256: Sha256
    size_bytes: int = Field(gt=0, le=canary.MAX_ARTIFACT_BYTES)


class ReciprocalRouteBuildReport(FrozenModel):
    """Canonical build report emitted by the runtime script.

    The report carries identity pairs bound to the request, so the
    verifier can recompute and compare.  Like the environment-module
    report, this schema self-validates field-level self-consistency;
    cross-request identity comparison is owned by
    ``verify_reciprocal_route_build_report``.
    """

    schema_version: Literal[
        "nantai.synthetic-village.reciprocal-route-build-report.v1"
    ] = RECIPROCAL_ROUTE_BUILD_REPORT_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    stage: Literal["modeled-unverified"] = "modeled-unverified"
    trust_effect: Literal["none"] = "none"

    # Base 175-root build identity.
    base_build_id: Sha256
    base_build_report_sha256: Sha256
    base_blend_sha256: Sha256
    base_environment_module_plan_sha256: Sha256

    # Runtime identity.
    runtime_script_sha256: Sha256
    reciprocal_route_module_plan_sha256: Sha256

    # Full 218-root registry + bindings (mirrors the request).
    object_registry: tuple[
        canary.ObjectRegistryEntry, ...
    ] = Field(min_length=218, max_length=218)
    material_bindings: tuple[
        ReciprocalRouteMaterialBinding, ...
    ] = Field(
        min_length=len(_MATERIAL_BINDING_ROWS),
        max_length=len(_MATERIAL_BINDING_ROWS),
    )

    counts: ReciprocalRouteBuildCounts
    validation: ReciprocalRouteBuildValidation
    artifact: ReciprocalRouteArtifact

    @model_validator(mode="after")
    def _registry_is_complete(self) -> ReciprocalRouteBuildReport:
        if tuple(row.instance_id for row in self.object_registry) != tuple(
            range(1, 219),
        ):
            raise ValueError(
                "reciprocal-route build report registry is not exact 1..218",
            )
        return self


def verify_reciprocal_route_build_report(
    report: ReciprocalRouteBuildReport,
    *,
    request: ReciprocalRouteRuntimeRequest,
    output_path: Path,
) -> None:
    """Recompute request/report identities and the measured Blender bytes.

    Performs 9 identity pair comparisons + 1 measured-bytes recomputation.
    Raises ``ReciprocalRouteRuntimeError`` on any mismatch.
    """

    identity_pairs = (
        (report.build_id, request.build_id),
        (report.base_build_id, request.base_build_id),
        (report.base_build_report_sha256, request.base_build_report_sha256),
        (report.base_blend_sha256, request.base_blend_sha256),
        (
            report.base_environment_module_plan_sha256,
            request.base_environment_module_plan_sha256,
        ),
        (report.runtime_script_sha256, request.runtime_script_sha256),
        (
            report.reciprocal_route_module_plan_sha256,
            request.reciprocal_route_module_plan_sha256,
        ),
        (report.object_registry, request.object_registry),
        (report.material_bindings, request.material_bindings),
    )
    if any(left != right for left, right in identity_pairs):
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build report identity disagrees",
        )
    output_path = Path(output_path)
    try:
        size = output_path.stat().st_size
        digest = _sha256_file(output_path)
    except OSError as exc:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route Blender artifact cannot be measured",
        ) from exc
    if (
        report.artifact.name != output_path.name
        or report.artifact.sha256 != digest
        or report.artifact.size_bytes != size
    ):
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route Blender artifact digest or size disagrees",
        )


def load_reciprocal_route_build_report(
    path: Path,
) -> ReciprocalRouteBuildReport:
    """Load one bounded canonical report while rejecting duplicate JSON keys."""

    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build report cannot be read",
        ) from exc
    if not raw or len(raw) > canary.MAX_BUILD_REPORT_BYTES:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build report bytes are absent or unbounded",
        )
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        if raw != canary._canonical_json_bytes(parsed):
            raise ReciprocalRouteRuntimeError(
                "reciprocal-route build report is not canonical JSON",
            )
        return ReciprocalRouteBuildReport.model_validate_json(raw)
    except ReciprocalRouteRuntimeError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        ValidationError,
        canary.CanaryBuildError,
    ) as exc:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build report validation failed",
        ) from exc


# --------------------------------------------------------------------------- #
# Phase 3: content-addressed Blender runtime bridge.
# --------------------------------------------------------------------------- #


def _convert_material_bindings(
    env_module_bindings: tuple,
) -> tuple[ReciprocalRouteMaterialBinding, ...]:
    """Re-emit the 14 env-module bindings as reciprocal-route bindings.

    The alias/slot/family/id table is identical; only the pydantic type
    differs so the reciprocal-route request cannot accidentally carry an
    ``EnvironmentModuleMaterialBinding`` instance.
    """

    return tuple(
        ReciprocalRouteMaterialBinding(
            material_alias=row.material_alias,
            runtime_slot_id=row.runtime_slot_id,
            material_family=row.material_family,
            material_id=row.material_id,
        )
        for row in env_module_bindings
    )


def _module_registry(
    plan: ReciprocalRouteModulePlan,
    bindings: tuple[ReciprocalRouteMaterialBinding, ...],
) -> tuple[canary.ObjectRegistryEntry, ...]:
    """Derive the 43 reciprocal-route ``ObjectRegistryEntry`` rows."""

    by_alias = {row.material_alias: row for row in bindings}
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
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route plan references an unbound material alias",
        ) from exc
    return tuple(rows)


def build_reciprocal_route_runtime_request(
    *,
    base_build: object,
    repo_root: Path = ROOT,
    reciprocal_route_plan: ReciprocalRouteModulePlan | None = None,
) -> ReciprocalRouteRuntimeRequest:
    """Bind one verified 175-root env-module build to the 43-part extension.

    ``base_build`` is duck-typed and mirrors the env-module runtime's
    ``base_build`` contract.  It must expose:

      * ``object_registry`` -- 175 verified ``ObjectRegistryEntry`` rows
        (instances 1..175, no duplicates).
      * ``build_id`` / ``build_report_sha256`` / ``blend_sha256`` /
        ``blender_executable_sha256`` -- the 175-root env-module build
        identity (the build_report_sha256 is the SHA of the env-module
        report FILE, not the build_id).
      * ``environment_module_plan`` -- the verified env-module plan
        (used both for the transitive SHA binding and for building the
        default reciprocal-route plan).
      * ``material_bindings`` -- the 14-row env-module binding table.
      * ``scene_plan`` / ``elevated_topology`` -- required only when
        ``reciprocal_route_plan is None`` (default plan construction).
      * ``executable`` / ``blend_path`` -- required only by
        ``run_reciprocal_route_build`` (the Blender subprocess).
    """

    repo_root = Path(repo_root).absolute()
    script_path = (
        repo_root / "scripts/blender/apply_reciprocal_route_modules.py"
    )
    if not script_path.is_file():
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route Blender runtime script is absent",
        )
    try:
        base_registry = tuple(base_build.object_registry)
        instances = tuple(row.instance_id for row in base_registry)
        if (
            instances != tuple(range(1, 176))
            or len(base_registry) != 175
        ):
            raise ReciprocalRouteRuntimeError(
                "verified base object registry must be exact 1..175",
            )
        env_module_plan = getattr(
            base_build,
            "environment_module_plan",
            None,
        )
        if env_module_plan is None:
            # Tolerate the env-module fixture's ``env_module_plan`` alias.
            env_module_plan = base_build.env_module_plan
        if reciprocal_route_plan is None:
            scene = base_build.scene_plan
            topology = base_build.elevated_topology
            plan = build_default_reciprocal_route_module_plan(
                scene=scene,
                elevated_topology=topology,
                environment_module_plan=env_module_plan,
            )
        else:
            plan = reciprocal_route_plan
            try:
                verify_reciprocal_route_module_plan(
                    plan,
                    scene=base_build.scene_plan,
                    elevated_topology=base_build.elevated_topology,
                    environment_module_plan=env_module_plan,
                )
            except ReciprocalRouteError as exc:
                raise ReciprocalRouteRuntimeError(
                    "reciprocal-route plan does not match verified base scene",
                ) from exc
        env_bindings = tuple(base_build.material_bindings)
        bindings = _convert_material_bindings(env_bindings)
        module_registry = _module_registry(plan, bindings)
        registry = (*base_registry, *module_registry)
        payload = {
            "schema_version": RECIPROCAL_ROUTE_RUNTIME_SCHEMA,
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
            "base_environment_module_plan_sha256": (
                environment_module_plan_sha256(env_module_plan)
            ),
            "runtime_script_sha256": _sha256_file(script_path),
            "reciprocal_route_module_plan_sha256": (
                reciprocal_route_module_plan_sha256(plan)
            ),
            "reciprocal_route_module_plan": plan,
            "material_bindings": bindings,
            "object_registry": registry,
            "requested_artifact": RECIPROCAL_ROUTE_ARTIFACT_NAME,
        }
    except ReciprocalRouteRuntimeError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise ReciprocalRouteRuntimeError(
            "verified env-module build cannot satisfy "
            "the reciprocal-route runtime contract",
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
    return ReciprocalRouteRuntimeRequest(build_id=build_id, **payload)


@dataclass(frozen=True)
class ReciprocalRouteBuildResult:
    """Result of one reciprocal-route Blender build run."""

    final_directory: Path
    request: ReciprocalRouteRuntimeRequest
    report: ReciprocalRouteBuildReport
    stdout: str
    stderr: str


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ReciprocalRouteRuntimeError(
            f"cannot write private reciprocal-route build input: {path.name}",
        ) from exc


def _verify_exact_build_layout(directory: Path) -> None:
    try:
        entries = tuple(directory.iterdir())
    except OSError as exc:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build directory cannot be inspected",
        ) from exc
    if (
        {entry.name for entry in entries}
        != set(RECIPROCAL_ROUTE_BUILD_ENTRIES)
        or any(
            entry.is_symlink() or not entry.is_file()
            for entry in entries
        )
    ):
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build directory is not the exact three-file set",
        )


def _remove_private_staging(staging: Path, *, parent: Path) -> None:
    """Remove only a direct, generated staging child after path verification."""

    try:
        if (
            staging.parent != parent
            or not staging.name.startswith(".staging-")
            or staging.is_symlink()
        ):
            raise ReciprocalRouteRuntimeError(
                "refusing to clean an unverified reciprocal-route staging path",
            )
        if staging.exists():
            shutil.rmtree(staging)
    except ReciprocalRouteRuntimeError:
        raise
    except OSError as exc:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route staging cleanup failed",
        ) from exc


def _verify_existing_build(
    *,
    directory: Path,
    request: ReciprocalRouteRuntimeRequest,
) -> ReciprocalRouteBuildReport:
    _verify_exact_build_layout(directory)
    request_bytes = (
        directory / RECIPROCAL_ROUTE_REQUEST_NAME
    ).read_bytes()
    if (
        request_bytes
        != canonical_reciprocal_route_runtime_request_bytes(request)
    ):
        raise ReciprocalRouteRuntimeError(
            "existing reciprocal-route build request bytes disagree",
        )
    report = load_reciprocal_route_build_report(
        directory / RECIPROCAL_ROUTE_REPORT_NAME,
    )
    verify_reciprocal_route_build_report(
        report,
        request=request,
        output_path=directory / RECIPROCAL_ROUTE_ARTIFACT_NAME,
    )
    return report


def run_reciprocal_route_build(
    *,
    base_build: object,
    repo_root: Path = ROOT,
    build_root: Path = DEFAULT_RECIPROCAL_ROUTE_BUILD_ROOT,
    reciprocal_route_plan: ReciprocalRouteModulePlan | None = None,
    timeout_seconds: int = DEFAULT_RECIPROCAL_ROUTE_BUILD_TIMEOUT_SECONDS,
) -> ReciprocalRouteBuildResult:
    """Run one real pinned-Blender reciprocal-route build and publish it atomically.

    Mirrors ``run_environment_module_build``: content-addressed reuse,
    private staging with fsync, snapshot-verified inputs, atomic rename
    to a content-addressed final directory.
    """

    repo_root = Path(repo_root).absolute()
    build_root = Path(build_root).absolute()
    if timeout_seconds <= 0:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build timeout must be positive",
        )
    request = build_reciprocal_route_runtime_request(
        base_build=base_build,
        repo_root=repo_root,
        reciprocal_route_plan=reciprocal_route_plan,
    )
    try:
        private_root = canary._require_real_directory(
            repo_root / ".nantai-studio",
            label="private project root",
        )
        build_root.mkdir(parents=True, exist_ok=True)
        build_root = canary._require_real_directory(
            build_root,
            label="reciprocal-route build root",
        )
        build_root.relative_to(private_root)
    except (OSError, ValueError, canary.CanaryBuildError) as exc:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build root must be a real private project directory",
        ) from exc

    final_directory = build_root / request.build_id
    if final_directory.exists():
        if (
            final_directory.is_symlink()
            or not final_directory.is_dir()
        ):
            raise ReciprocalRouteRuntimeError(
                "existing reciprocal-route build path is not a real directory",
            )
        report = _verify_existing_build(
            directory=final_directory,
            request=request,
        )
        return ReciprocalRouteBuildResult(
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
        request_path = staging / RECIPROCAL_ROUTE_REQUEST_NAME
        _write_exclusive(
            request_path,
            canonical_reciprocal_route_runtime_request_bytes(request),
        )
        executable = Path(base_build.executable).absolute()
        blend_path = Path(base_build.blend_path).absolute()
        script_path = (
            repo_root / "scripts/blender/apply_reciprocal_route_modules.py"
        )
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
        stdout = completed.stdout[-canary.MAX_PROCESS_LOG_BYTES:]
        stderr = completed.stderr[-canary.MAX_PROCESS_LOG_BYTES:]
        if completed.returncode != 0:
            detail = (stderr or stdout).strip()
            raise ReciprocalRouteRuntimeError(
                "verified Blender reciprocal-route build failed"
                + (f": {detail[-2000:]}" if detail else ""),
            )
        canary._verify_snapshots_unchanged(snapshots)
        _verify_exact_build_layout(staging)
        report = load_reciprocal_route_build_report(
            staging / RECIPROCAL_ROUTE_REPORT_NAME,
        )
        verify_reciprocal_route_build_report(
            report,
            request=request,
            output_path=staging / RECIPROCAL_ROUTE_ARTIFACT_NAME,
        )
        try:
            staging.rename(final_directory)
        except OSError as exc:
            if (
                final_directory.is_dir()
                and not final_directory.is_symlink()
            ):
                report = _verify_existing_build(
                    directory=final_directory,
                    request=request,
                )
                _remove_private_staging(staging, parent=build_root)
            else:
                raise ReciprocalRouteRuntimeError(
                    "reciprocal-route build publication failed",
                ) from exc
        return ReciprocalRouteBuildResult(
            final_directory=final_directory,
            request=request,
            report=report,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReciprocalRouteRuntimeError(
            f"Blender reciprocal-route build exceeded "
            f"{timeout_seconds} seconds",
        ) from exc
    finally:
        if staging.exists():
            _remove_private_staging(staging, parent=build_root)
