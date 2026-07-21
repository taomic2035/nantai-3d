"""Fail-closed Blender runtime bridge for ``ReciprocalRouteModulePlan``.

This module is the additive runtime counterpart of
``reciprocal_route_module.py``: it extends one verified 175-root
environment-module build with the 43 stable reciprocal-route instances
declared by ``ReciprocalRouteModulePlan``, producing a private
``modeled-unverified`` Blender scene of 218 canonical roots.

Schema only (HANDOFF-OPUS-009 Phase 2):
  * ``ReciprocalRouteRuntimeRequest`` -- the canonical request that the
    future ``scripts/blender/apply_reciprocal_route_modules.py`` will
    consume.  It is built from a verified 175-root environment-module
    build + ``ReciprocalRouteModulePlan``.
  * ``ReciprocalRouteBuildReport`` -- the canonical report that the
    future Blender runtime script will emit.  Carries identity pairs
    bound to the request, plus the measured Blender artifact bytes.
  * ``verify_reciprocal_route_build_report`` -- 8 identity pair
    comparison + measured bytes recomputation.

Out of scope (deferred to Phase 3, after Blender runtime script exists):
  * ``build_reciprocal_route_runtime_request`` -- needs
    ``apply_reciprocal_route_modules.py`` SHA on disk.
  * ``run_reciprocal_route_build`` -- needs Blender subprocess + the
    runtime script.

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
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from . import canary
from .reciprocal_route_module import (
    ReciprocalRouteModulePlan,
    reciprocal_route_module_plan_sha256,
)

RECIPROCAL_ROUTE_RUNTIME_SCHEMA = (
    "nantai.synthetic-village.reciprocal-route-runtime-request.v1"
)
RECIPROCAL_ROUTE_BUILD_REPORT_SCHEMA = (
    "nantai.synthetic-village.reciprocal-route-build-report.v1"
)

#: Future runtime script.  Its SHA will be measured by the deferred
#: ``build_reciprocal_route_runtime_request`` once the script exists.
RECIPROCAL_ROUTE_RUNTIME_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/blender/apply_reciprocal_route_modules.py"
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
    Path(__file__).resolve().parents[2]
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
    # reciprocal-route parts reuse them.
    material_bindings: tuple[
        ReciprocalRouteMaterialBinding, ...
    ] = Field(min_length=1)

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
    ] = Field(min_length=1)

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
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, canary.CanaryBuildError) as exc:
        raise ReciprocalRouteRuntimeError(
            "reciprocal-route build report validation failed",
        ) from exc


# --------------------------------------------------------------------------- #
# Phase 3 (deferred -- needs scripts/blender/apply_reciprocal_route_modules.py)
# --------------------------------------------------------------------------- #
#
# The following functions are intentionally NOT implemented in this phase.
# They will be added once Codex (or Opus post-§3) writes the Blender
# runtime script:
#
#   build_reciprocal_route_runtime_request(
#       *, base_build, repo_root=ROOT,
#       reciprocal_route_plan=None,
#   ) -> ReciprocalRouteRuntimeRequest
#
#   run_reciprocal_route_build(
#       *, base_build, repo_root=ROOT,
#       build_root=DEFAULT_RECIPROCAL_ROUTE_BUILD_ROOT,
#       reciprocal_route_plan=None,
#       timeout_seconds=DEFAULT_RECIPROCAL_ROUTE_BUILD_TIMEOUT_SECONDS,
#   ) -> ReciprocalRouteBuildResult
#
# Until then, callers can use the schema + verifier directly:
#
#   request = ReciprocalRouteRuntimeRequest.model_validate(payload)
#   report = load_reciprocal_route_build_report(path)
#   verify_reciprocal_route_build_report(report, request=request, output_path=...)
#
# The schema layer is testable on its own via model_validate_json
# round-trips (see tests/test_synthetic_village_reciprocal_route_module_runtime.py).
