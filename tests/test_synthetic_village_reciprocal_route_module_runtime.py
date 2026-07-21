"""Reciprocal-route runtime schema + bridge tests (HANDOFF-OPUS-009 Phase 2 + 3).

Phase 2 (schema-only) constructs legal payloads directly via
``model_validate_json`` round-trips and exercises the verifier's
identity-pair comparison.

Phase 3 covers ``build_reciprocal_route_runtime_request`` (the
content-addressed constructor) and ``run_reciprocal_route_build`` (the
Blender subprocess bridge with content-addressed reuse + atomic
publication).  Subprocess is mocked so tests do not depend on a real
Blender runtime.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.elevated_topology import build_elevated_topology_plan
from pipeline.synthetic_village.environment_module import (
    build_default_environment_module_plan,
    environment_module_plan_sha256,
)
from pipeline.synthetic_village.reciprocal_route_module import (
    build_default_reciprocal_route_module_plan,
    reciprocal_route_module_plan_sha256,
)
from pipeline.synthetic_village.reciprocal_route_module_runtime import (
    RECIPROCAL_ROUTE_ARTIFACT_NAME,
    RECIPROCAL_ROUTE_BUILD_ENTRIES,
    RECIPROCAL_ROUTE_BUILD_REPORT_SCHEMA,
    RECIPROCAL_ROUTE_FULL_CANONICAL_ROOTS,
    RECIPROCAL_ROUTE_MODULE_CANONICAL_ROOTS,
    RECIPROCAL_ROUTE_REPORT_NAME,
    RECIPROCAL_ROUTE_RUNTIME_SCHEMA,
    ReciprocalRouteBuildReport,
    ReciprocalRouteBuildResult,
    ReciprocalRouteMaterialBinding,
    ReciprocalRouteRuntimeError,
    ReciprocalRouteRuntimeRequest,
    build_reciprocal_route_runtime_request,
    canonical_reciprocal_route_runtime_request_bytes,
    load_reciprocal_route_build_report,
    run_reciprocal_route_build,
    verify_reciprocal_route_build_report,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan

# --------------------------------------------------------------------------- #
# Shared fixture: a fully-constructed base + plan + bindings + registry.
# --------------------------------------------------------------------------- #


def _base_build(tmp_path: Path) -> SimpleNamespace:
    """Mirror of environment-module test fixture: a verified 175-root base."""

    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    semantics = canary._semantic_registry()
    materials = canary._material_registry(scene)
    base_registry_130 = canary._object_registry(scene, topology, semantics, materials)
    env_module_plan = build_default_environment_module_plan(
        scene=scene,
        elevated_topology=topology,
    )
    # Extend the 130-root registry to 175 by reusing the env-module test
    # pattern: derive module parts from the env plan.
    from pipeline.synthetic_village.environment_module_runtime import (
        _MATERIAL_BINDING_ROWS,
        EnvironmentModuleMaterialBinding,
        _module_registry,
    )
    material_ids = {row.material_family: row.material_id for row in materials}
    bindings_v1 = tuple(
        EnvironmentModuleMaterialBinding(
            material_alias=alias,
            runtime_slot_id=runtime_slot,
            material_family=family,
            material_id=material_ids[family],
        )
        for alias, runtime_slot, family in _MATERIAL_BINDING_ROWS
    )
    module_registry_45 = _module_registry(env_module_plan, bindings_v1)
    base_registry_175 = (*base_registry_130, *module_registry_45)

    blend_path = tmp_path / "village-modules.blend"
    blend_path.write_bytes(b"verified-175-root-blend")
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"verified-blender")
    return SimpleNamespace(
        build_id="a" * 64,
        build_report_sha256="b" * 64,
        blend_sha256=_sha256_file(blend_path),
        blend_size_bytes=blend_path.stat().st_size,
        blender_executable_sha256=_sha256_file(executable),
        blend_path=blend_path,
        executable=executable,
        object_registry=base_registry_175,
        semantic_registry=semantics,
        material_registry=materials,
        env_module_plan=env_module_plan,
        scene_plan=scene,
        elevated_topology=topology,
        material_bindings=bindings_v1,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _material_bindings_reciprocal(
    base: SimpleNamespace,
) -> tuple[ReciprocalRouteMaterialBinding, ...]:
    """Re-use the base 175-root material bindings as runtime aliases."""

    return tuple(
        ReciprocalRouteMaterialBinding(
            material_alias=row.material_alias,
            runtime_slot_id=row.runtime_slot_id,
            material_family=row.material_family,
            material_id=row.material_id,
        )
        for row in base.material_bindings
    )


def _module_registry_reciprocal(
    plan,
    bindings: tuple[ReciprocalRouteMaterialBinding, ...],
):
    """Derive the 43 reciprocal-route parts from the plan + bindings."""

    material_id_by_alias = {row.material_alias: row.material_id for row in bindings}
    rows: list[canary.ObjectRegistryEntry] = []
    for module in plan.modules:
        for part in module.parts:
            try:
                material_id = material_id_by_alias[part.material_slot_id]
            except KeyError as exc:
                raise ReciprocalRouteRuntimeError(
                    "reciprocal-route plan references an unbound material alias",
                ) from exc
            rows.append(
                canary.ObjectRegistryEntry(
                    object_id=part.part_id,
                    instance_id=part.instance_id,
                    semantic_id=part.semantic_id,
                    material_id=material_id,
                    variant_id=None,
                ),
            )
    return tuple(rows)


def _build_request_payload(
    base: SimpleNamespace,
    *,
    runtime_script_sha256: str,
    plan=None,
) -> dict:
    """Construct a legal ReciprocalRouteRuntimeRequest payload (sans build_id)."""

    if plan is None:
        plan = build_default_reciprocal_route_module_plan(
            scene=base.scene_plan,
            elevated_topology=base.elevated_topology,
            environment_module_plan=base.env_module_plan,
        )
    reciprocal_bindings = _material_bindings_reciprocal(base)
    module_registry_43 = _module_registry_reciprocal(plan, reciprocal_bindings)
    full_registry_218 = (*base.object_registry, *module_registry_43)
    base_registry_sha = hashlib.sha256(
        canary._canonical_json_bytes(
            [row.model_dump(mode="json") for row in base.object_registry],
        ),
    ).hexdigest()
    payload = {
        "schema_version": RECIPROCAL_ROUTE_RUNTIME_SCHEMA,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none",
        "base_build_id": base.build_id,
        "base_build_report_sha256": base.build_report_sha256,
        "base_blend_sha256": base.blend_sha256,
        "base_blender_executable_sha256": base.blender_executable_sha256,
        "base_object_registry_sha256": base_registry_sha,
        "base_environment_module_plan_sha256": environment_module_plan_sha256(
            base.env_module_plan,
        ),
        "runtime_script_sha256": runtime_script_sha256,
        "reciprocal_route_module_plan_sha256": (
            reciprocal_route_module_plan_sha256(plan)
        ),
        "reciprocal_route_module_plan": plan,
        "material_bindings": reciprocal_bindings,
        "object_registry": full_registry_218,
        "requested_artifact": RECIPROCAL_ROUTE_ARTIFACT_NAME,
    }
    # Compute canonical build_id (excluding itself).
    payload_for_id = {
        key: (
            value.model_dump(mode="json")
            if isinstance(value, BaseModel)
            else (
                [item.model_dump(mode="json") for item in value]
                if isinstance(value, tuple) and value
                and isinstance(value[0], BaseModel)
                else value
            )
        )
        for key, value in payload.items()
    }
    build_id = hashlib.sha256(
        canary._canonical_json_bytes(payload_for_id),
    ).hexdigest()
    payload["build_id"] = build_id
    return payload


def _build_report_payload(
    runtime_request: ReciprocalRouteRuntimeRequest,
    *,
    output_path: Path,
) -> dict:
    output_sha = _sha256_file(output_path)
    return {
        "schema_version": RECIPROCAL_ROUTE_BUILD_REPORT_SCHEMA,
        "build_id": runtime_request.build_id,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none",
        "base_build_id": runtime_request.base_build_id,
        "base_build_report_sha256": runtime_request.base_build_report_sha256,
        "base_blend_sha256": runtime_request.base_blend_sha256,
        "base_environment_module_plan_sha256": (
            runtime_request.base_environment_module_plan_sha256
        ),
        "runtime_script_sha256": runtime_request.runtime_script_sha256,
        "reciprocal_route_module_plan_sha256": (
            runtime_request.reciprocal_route_module_plan_sha256
        ),
        "object_registry": runtime_request.object_registry,
        "material_bindings": runtime_request.material_bindings,
        "counts": {
            "base_canonical_roots": 175,
            "module_canonical_roots": 43,
            "canonical_roots": 218,
            "module_mesh_objects": 43,
        },
        "validation": {
            "base_registry_matches": True,
            "module_registry_matches": True,
            "finite_nonempty_module_meshes": True,
            "material_bindings_match": True,
            "design_sources_are_provenance_only": True,
        },
        "artifact": {
            "name": RECIPROCAL_ROUTE_ARTIFACT_NAME,
            "kind": "blender-scene",
            "sha256": output_sha,
            "size_bytes": output_path.stat().st_size,
        },
    }


# Re-import BaseModel for the payload-construction helper above.
from pydantic import BaseModel  # noqa: E402


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return _base_build(tmp_path_factory.mktemp("base"))


@pytest.fixture(scope="module")
def runtime_script_sha():
    """Fake runtime script SHA (the script does not exist yet)."""

    return "c" * 64


@pytest.fixture(scope="module")
def request_payload(base, runtime_script_sha):
    return _build_request_payload(
        base,
        runtime_script_sha256=runtime_script_sha,
    )


@pytest.fixture(scope="module")
def runtime_request(request_payload):
    return ReciprocalRouteRuntimeRequest.model_validate(request_payload)


@pytest.fixture
def output_path(tmp_path):
    path = tmp_path / RECIPROCAL_ROUTE_ARTIFACT_NAME
    path.write_bytes(b"reciprocal-route-build")
    return path


# --------------------------------------------------------------------------- #
# Schema constants.
# --------------------------------------------------------------------------- #


def test_schema_constants_are_locked() -> None:
    assert RECIPROCAL_ROUTE_RUNTIME_SCHEMA == (
        "nantai.synthetic-village.reciprocal-route-runtime-request.v1"
    )
    assert RECIPROCAL_ROUTE_BUILD_REPORT_SCHEMA == (
        "nantai.synthetic-village.reciprocal-route-build-report.v1"
    )
    assert RECIPROCAL_ROUTE_MODULE_CANONICAL_ROOTS == 43
    assert RECIPROCAL_ROUTE_FULL_CANONICAL_ROOTS == 218
    assert RECIPROCAL_ROUTE_ARTIFACT_NAME == "village-reciprocal-route.blend"
    assert set(RECIPROCAL_ROUTE_BUILD_ENTRIES) == {
        "reciprocal-route-build-request.json",
        "reciprocal-route-build-report.json",
        "village-reciprocal-route.blend",
    }


# --------------------------------------------------------------------------- #
# runtime_request schema.
# --------------------------------------------------------------------------- #


def test_request_validates_default_payload(runtime_request) -> None:
    assert runtime_request.schema_version == RECIPROCAL_ROUTE_RUNTIME_SCHEMA
    assert runtime_request.synthetic is True
    assert runtime_request.verification_level == "L0"
    assert runtime_request.geometry_usability == "preview-only"
    assert runtime_request.stage == "modeled-unverified"
    assert runtime_request.trust_effect == "none"
    assert len(runtime_request.object_registry) == 218
    assert tuple(row.instance_id for row in runtime_request.object_registry) == tuple(
        range(1, 219),
    )


def test_request_canonical_bytes_end_with_newline(runtime_request) -> None:
    assert canonical_reciprocal_route_runtime_request_bytes(runtime_request).endswith(b"\n")


def test_request_build_id_is_canonical(runtime_request) -> None:
    payload = runtime_request.model_dump(mode="json")
    payload.pop("build_id")
    expected = hashlib.sha256(
        canary._canonical_json_bytes(payload),
    ).hexdigest()
    assert runtime_request.build_id == expected


def _payload_to_json_dict(payload) -> dict:
    """Convert a payload containing pydantic models to a plain JSON dict."""
    from pipeline.synthetic_village import canary

    canonical = canary._canonical_json_bytes(payload)
    return json.loads(canonical.decode("utf-8"))


def test_request_rejects_tampered_module_plan_sha(request_payload) -> None:
    """Swapping the plan SHA must fail because the validator recomputes it."""
    payload = _payload_to_json_dict(request_payload)
    payload["reciprocal_route_module_plan_sha256"] = "d" * 64
    with pytest.raises(ValidationError, match="SHA-256 is not canonical"):
        ReciprocalRouteRuntimeRequest.model_validate_json(json.dumps(payload))


def test_request_rejects_tampered_object_registry(request_payload) -> None:
    """Adding a 219th registry row must fail (218 Literal-locked)."""
    payload = _payload_to_json_dict(request_payload)
    extra_row = {
        "object_id": "tampered-extra-001",
        "instance_id": 219,
        "semantic_id": 3,
        "material_id": 1,
        "variant_id": None,
    }
    payload["object_registry"] = [*payload["object_registry"], extra_row]
    with pytest.raises(ValidationError):
        ReciprocalRouteRuntimeRequest.model_validate_json(json.dumps(payload))


def test_request_rejects_tampered_module_part_material_id(request_payload) -> None:
    """If a module part's material_id in the registry disagrees with the plan
    (via the binding), validation must fail.
    """
    payload = _payload_to_json_dict(request_payload)
    # Tamper registry row 175 (first module part) to wrong material_id.
    payload["object_registry"][175]["material_id"] = (
        payload["object_registry"][175]["material_id"] + 1
    ) % 11 + 1
    with pytest.raises(ValidationError, match="material_id disagrees"):
        ReciprocalRouteRuntimeRequest.model_validate_json(json.dumps(payload))


def test_request_rejects_non_hex_build_id(request_payload) -> None:
    payload = _payload_to_json_dict(request_payload)
    payload["build_id"] = "not-a-sha"
    with pytest.raises(ValidationError):
        ReciprocalRouteRuntimeRequest.model_validate_json(json.dumps(payload))


def test_request_rejects_unknown_material_alias(request_payload) -> None:
    """A material_binding with an unknown alias pattern must fail."""
    payload = _payload_to_json_dict(request_payload)
    payload["material_bindings"][0] = {
        **payload["material_bindings"][0],
        "material_alias": "not-a-valid-alias",
    }
    with pytest.raises(ValidationError):
        ReciprocalRouteRuntimeRequest.model_validate_json(json.dumps(payload))


def test_request_rejects_tampered_base_env_module_plan_sha(request_payload) -> None:
    """The base env-module plan SHA must match the reciprocal plan's binding."""
    payload = _payload_to_json_dict(request_payload)
    payload["base_environment_module_plan_sha256"] = "e" * 64
    with pytest.raises(ValidationError, match="base environment-module plan SHA-256 disagrees"):
        ReciprocalRouteRuntimeRequest.model_validate_json(json.dumps(payload))


# --------------------------------------------------------------------------- #
# Report schema.
# --------------------------------------------------------------------------- #


def test_report_validates_default_payload(runtime_request, output_path) -> None:
    payload = _build_report_payload(runtime_request, output_path=output_path)
    report = ReciprocalRouteBuildReport.model_validate(payload)
    assert report.schema_version == RECIPROCAL_ROUTE_BUILD_REPORT_SCHEMA
    assert report.counts.base_canonical_roots == 175
    assert report.counts.module_canonical_roots == 43
    assert report.counts.canonical_roots == 218
    assert report.artifact.name == RECIPROCAL_ROUTE_ARTIFACT_NAME
    assert report.artifact.kind == "blender-scene"


def test_report_rejects_registry_with_217_rows(runtime_request, output_path) -> None:
    """Report registry must be exactly 218 rows."""
    payload = _build_report_payload(runtime_request, output_path=output_path)
    payload["object_registry"] = payload["object_registry"][:-1]
    with pytest.raises(ValidationError):
        ReciprocalRouteBuildReport.model_validate(payload)


def test_report_rejects_unknown_artifact_name(runtime_request, output_path) -> None:
    payload = _build_report_payload(runtime_request, output_path=output_path)
    payload["artifact"]["name"] = "village-tampered.blend"
    with pytest.raises(ValidationError):
        ReciprocalRouteBuildReport.model_validate(payload)


def test_report_rejects_invalid_validation_flag(runtime_request, output_path) -> None:
    payload = _build_report_payload(runtime_request, output_path=output_path)
    payload["validation"]["base_registry_matches"] = False
    with pytest.raises(ValidationError):
        ReciprocalRouteBuildReport.model_validate(payload)


# --------------------------------------------------------------------------- #
# verify_reciprocal_route_build_report
# --------------------------------------------------------------------------- #


def test_verify_passes_for_default_payload(runtime_request, output_path) -> None:
    payload = _build_report_payload(runtime_request, output_path=output_path)
    report = ReciprocalRouteBuildReport.model_validate(payload)
    verify_reciprocal_route_build_report(
        report,
        request=runtime_request,
        output_path=output_path,
    )


def test_verify_rejects_tampered_artifact_bytes(
    runtime_request, output_path,
) -> None:
    payload = _build_report_payload(runtime_request, output_path=output_path)
    report = ReciprocalRouteBuildReport.model_validate(payload)
    output_path.write_bytes(b"tampered")
    with pytest.raises(
        ReciprocalRouteRuntimeError,
        match="artifact digest or size disagrees",
    ):
        verify_reciprocal_route_build_report(
            report,
            request=runtime_request,
            output_path=output_path,
        )


def test_verify_rejects_tampered_module_plan_sha(runtime_request, output_path) -> None:
    payload = _build_report_payload(runtime_request, output_path=output_path)
    payload["reciprocal_route_module_plan_sha256"] = "f" * 64
    report = ReciprocalRouteBuildReport.model_validate(payload)
    with pytest.raises(
        ReciprocalRouteRuntimeError,
        match="identity disagrees",
    ):
        verify_reciprocal_route_build_report(
            report,
            request=runtime_request,
            output_path=output_path,
        )


def test_verify_rejects_tampered_object_registry_row(
    runtime_request, output_path,
) -> None:
    """If a module part's material_id is tampered in the report, verify
    must reject by identity pair comparison.
    """
    payload = _payload_to_json_dict(
        _build_report_payload(runtime_request, output_path=output_path),
    )
    payload["object_registry"][175]["material_id"] = (
        payload["object_registry"][175]["material_id"] % 11
    ) + 1
    report = ReciprocalRouteBuildReport.model_validate_json(json.dumps(payload))
    with pytest.raises(
        ReciprocalRouteRuntimeError,
        match="identity disagrees",
    ):
        verify_reciprocal_route_build_report(
            report,
            request=runtime_request,
            output_path=output_path,
        )


def test_verify_rejects_tampered_material_binding(
    runtime_request, output_path,
) -> None:
    payload = _payload_to_json_dict(
        _build_report_payload(runtime_request, output_path=output_path),
    )
    payload["material_bindings"][0]["material_id"] = (
        payload["material_bindings"][0]["material_id"] % 11
    ) + 1
    report = ReciprocalRouteBuildReport.model_validate_json(json.dumps(payload))
    with pytest.raises(
        ReciprocalRouteRuntimeError,
        match="identity disagrees",
    ):
        verify_reciprocal_route_build_report(
            report,
            request=runtime_request,
            output_path=output_path,
        )


# --------------------------------------------------------------------------- #
# load_reciprocal_route_build_report.
# --------------------------------------------------------------------------- #


def test_load_report_round_trips_canonical_bytes(
    runtime_request, output_path, tmp_path,
) -> None:
    import os

    payload = _build_report_payload(runtime_request, output_path=output_path)
    report = ReciprocalRouteBuildReport.model_validate(payload)
    report_path = tmp_path / "report.json"
    with report_path.open("xb") as stream:
        stream.write(canary._canonical_json_bytes(report.model_dump(mode="json")))
        stream.flush()
        os.fsync(stream.fileno())
    loaded = load_reciprocal_route_build_report(report_path)
    assert loaded == report


def test_load_report_rejects_duplicate_keys(
    runtime_request, output_path, tmp_path,
) -> None:
    payload = _build_report_payload(runtime_request, output_path=output_path)
    canonical = canary._canonical_json_bytes(payload)
    # Inject a duplicate build_id key.
    text = canonical.decode("utf-8")
    text = text.replace(
        '"build_id":',
        '"build_id": "duplicated", "build_id":',
        1,
    )
    report_path = tmp_path / "report-duplicate.json"
    report_path.write_bytes(text.encode("utf-8"))
    with pytest.raises(ReciprocalRouteRuntimeError, match="cannot be read|validation failed"):
        load_reciprocal_route_build_report(report_path)


def test_load_report_rejects_non_canonical_bytes(
    runtime_request, output_path, tmp_path,
) -> None:
    payload = _build_report_payload(runtime_request, output_path=output_path)
    canonical = canary._canonical_json_bytes(payload)
    # Mutate whitespace so bytes differ from canonical.
    text = canonical.decode("utf-8").replace("  ", " ", 1)
    report_path = tmp_path / "report-noncanonical.json"
    report_path.write_bytes(text.encode("utf-8"))
    with pytest.raises(ReciprocalRouteRuntimeError, match="not canonical JSON|validation failed"):
        load_reciprocal_route_build_report(report_path)


# --------------------------------------------------------------------------- #
# Phase 3: build_reciprocal_route_runtime_request (content-addressed constructor).
# --------------------------------------------------------------------------- #


_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_build_request_constructs_valid_request_from_verified_base(
    base: SimpleNamespace,
) -> None:
    request = build_reciprocal_route_runtime_request(
        base_build=base,
        repo_root=_REPO_ROOT,
    )
    assert request.schema_version == RECIPROCAL_ROUTE_RUNTIME_SCHEMA
    assert request.verification_level == "L0"
    assert request.geometry_usability == "preview-only"
    assert request.stage == "modeled-unverified"
    assert request.trust_effect == "none"
    assert len(request.object_registry) == RECIPROCAL_ROUTE_FULL_CANONICAL_ROOTS
    assert request.requested_artifact == RECIPROCAL_ROUTE_ARTIFACT_NAME
    # Runtime script SHA is measured from the real script on disk.
    assert request.runtime_script_sha256 == _sha256_file(
        _REPO_ROOT / "scripts/blender/apply_reciprocal_route_modules.py",
    )
    # The 43 module parts extend the 175-root base without overlap.
    instances = tuple(row.instance_id for row in request.object_registry)
    assert instances == tuple(range(1, 219))


def test_build_request_build_id_is_canonical_and_matches_validator(
    base: SimpleNamespace,
) -> None:
    """The constructor's build_id must equal the validator's recomputation."""

    request = build_reciprocal_route_runtime_request(
        base_build=base,
        repo_root=_REPO_ROOT,
    )
    # If build_id were wrong, model_validate would have rejected the
    # request.  Re-assert explicitly for documentation.
    payload = request.model_dump(mode="json")
    payload.pop("build_id")
    recomputed = hashlib.sha256(
        canary._canonical_json_bytes(payload),
    ).hexdigest()
    assert request.build_id == recomputed


def test_build_request_is_deterministic_across_calls(
    base: SimpleNamespace,
) -> None:
    left = build_reciprocal_route_runtime_request(
        base_build=base,
        repo_root=_REPO_ROOT,
    )
    right = build_reciprocal_route_runtime_request(
        base_build=base,
        repo_root=_REPO_ROOT,
    )
    assert left.build_id == right.build_id
    assert left == right


def test_build_request_rejects_registry_that_is_not_exact_175(
    tmp_path: Path,
) -> None:
    base = _base_build(tmp_path)
    # Truncate to 130 roots (the pre-env-module registry).
    truncated = SimpleNamespace(**base.__dict__)
    truncated.object_registry = base.object_registry[:130]
    with pytest.raises(
        ReciprocalRouteRuntimeError,
        match="exact 1..175",
    ):
        build_reciprocal_route_runtime_request(
            base_build=truncated,
            repo_root=_REPO_ROOT,
        )


def test_build_request_rejects_mismatched_reciprocal_route_plan(
    base: SimpleNamespace,
) -> None:
    """A plan whose scene SHA differs from the base scene must be rejected."""

    from pipeline.synthetic_village.reciprocal_route_module import (
        ReciprocalRouteModuleSummary,
    )
    # Re-use the default plan but tamper with scene_plan_sha256 so
    # verify_reciprocal_route_module_plan fails.
    good_plan = build_default_reciprocal_route_module_plan(
        scene=base.scene_plan,
        elevated_topology=base.elevated_topology,
        environment_module_plan=base.env_module_plan,
    )
    tampered = good_plan.model_copy(
        update={
            "scene_plan_sha256": "0" * 64,
            "summary": ReciprocalRouteModuleSummary(
                part_count=good_plan.summary.part_count,
            ),
        },
    )
    with pytest.raises(
        ReciprocalRouteRuntimeError,
        match="does not match verified base scene",
    ):
        build_reciprocal_route_runtime_request(
            base_build=base,
            repo_root=_REPO_ROOT,
            reciprocal_route_plan=tampered,
        )


def test_build_request_rejects_absent_runtime_script(
    base: SimpleNamespace,
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ReciprocalRouteRuntimeError,
        match="runtime script is absent",
    ):
        build_reciprocal_route_runtime_request(
            base_build=base,
            repo_root=tmp_path,
        )


# --------------------------------------------------------------------------- #
# Phase 3: run_reciprocal_route_build (Blender subprocess bridge, mocked).
# --------------------------------------------------------------------------- #


def _private_build_root() -> Path:
    """A real private directory under the repo's .nantai-studio root."""

    return (
        _REPO_ROOT
        / ".nantai-studio/synthetic-village/hybrid-v4/work/tests"
        / uuid.uuid4().hex
    )


def _fake_blender_success(request_path: Path, staging: Path) -> None:
    """Write a valid report + artifact into staging, mimicking Blender."""

    request_bytes = request_path.read_bytes()
    request = ReciprocalRouteRuntimeRequest.model_validate_json(
        request_bytes,
    )
    artifact_path = staging / RECIPROCAL_ROUTE_ARTIFACT_NAME
    artifact_path.write_bytes(b"reciprocal-route-build-artifact")
    report_path = staging / RECIPROCAL_ROUTE_REPORT_NAME
    report_payload = {
        "schema_version": RECIPROCAL_ROUTE_BUILD_REPORT_SCHEMA,
        "build_id": request.build_id,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none",
        "base_build_id": request.base_build_id,
        "base_build_report_sha256": request.base_build_report_sha256,
        "base_blend_sha256": request.base_blend_sha256,
        "base_environment_module_plan_sha256": (
            request.base_environment_module_plan_sha256
        ),
        "runtime_script_sha256": request.runtime_script_sha256,
        "reciprocal_route_module_plan_sha256": (
            request.reciprocal_route_module_plan_sha256
        ),
        "object_registry": [
            row.model_dump(mode="json")
            for row in request.object_registry
        ],
        "material_bindings": [
            row.model_dump(mode="json")
            for row in request.material_bindings
        ],
        "counts": {
            "base_canonical_roots": 175,
            "module_canonical_roots": 43,
            "canonical_roots": 218,
            "module_mesh_objects": 43,
        },
        "validation": {
            "base_registry_matches": True,
            "module_registry_matches": True,
            "finite_nonempty_module_meshes": True,
            "material_bindings_match": True,
            "design_sources_are_provenance_only": True,
        },
        "artifact": {
            "name": RECIPROCAL_ROUTE_ARTIFACT_NAME,
            "kind": "blender-scene",
            "sha256": _sha256_file(artifact_path),
            "size_bytes": artifact_path.stat().st_size,
        },
    }
    report_path.write_bytes(
        canary._canonical_json_bytes(report_payload),
    )


def _make_fake_run(request_captured: dict | None = None):
    """Return a subprocess.run replacement that writes valid outputs."""

    def _fake_run(args, **kwargs):
        # args = [exe, --background, blend, --python, script, --, request, staging]
        sep = args.index("--")
        request_path = Path(args[sep + 1])
        staging = Path(args[sep + 2])
        if request_captured is not None:
            request_captured["request_path"] = request_path
            request_captured["staging"] = staging
            request_captured["args"] = list(args)
        _fake_blender_success(request_path, staging)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="NANTAI_RECIPROCAL_ROUTE_MODULE_BUILD=ok",
            stderr="",
        )

    return _fake_run


def test_run_build_publishes_content_addressed_directory(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_root = _private_build_root()
    build_root.mkdir(parents=True)
    try:
        monkeypatch.setattr(
            subprocess, "run", _make_fake_run(),
        )
        result = run_reciprocal_route_build(
            base_build=base,
            repo_root=_REPO_ROOT,
            build_root=build_root,
        )
        assert isinstance(result, ReciprocalRouteBuildResult)
        assert result.final_directory == build_root / result.request.build_id
        assert result.final_directory.is_dir()
        # Three-file layout.
        entries = {p.name for p in result.final_directory.iterdir()}
        assert entries == set(RECIPROCAL_ROUTE_BUILD_ENTRIES)
        # Report identity matches request.
        verify_reciprocal_route_build_report(
            result.report,
            request=result.request,
            output_path=result.final_directory / RECIPROCAL_ROUTE_ARTIFACT_NAME,
        )
    finally:
        import shutil

        shutil.rmtree(build_root, ignore_errors=True)


def test_run_build_reuses_existing_content_addressed_directory(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_root = _private_build_root()
    build_root.mkdir(parents=True)
    try:
        monkeypatch.setattr(subprocess, "run", _make_fake_run())
        first = run_reciprocal_route_build(
            base_build=base,
            repo_root=_REPO_ROOT,
            build_root=build_root,
        )
        # Second call must NOT invoke Blender; reuse the existing directory.
        blender_calls = 0

        def forbidden(*args, **kwargs):
            nonlocal blender_calls
            blender_calls += 1
            raise AssertionError("Blender must not run on reuse")

        monkeypatch.setattr(subprocess, "run", forbidden)
        second = run_reciprocal_route_build(
            base_build=base,
            repo_root=_REPO_ROOT,
            build_root=build_root,
        )
        assert blender_calls == 0
        assert second.final_directory == first.final_directory
        assert second.report == first.report
        assert second.stdout == ""
        assert second.stderr == ""
    finally:
        import shutil

        shutil.rmtree(build_root, ignore_errors=True)


def test_run_build_rejects_nonzero_exit_and_cleans_staging(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_root = _private_build_root()
    build_root.mkdir(parents=True)
    try:

        def failing(args, **kwargs):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout="",
                stderr="Blender crashed",
            )

        monkeypatch.setattr(subprocess, "run", failing)
        with pytest.raises(
            ReciprocalRouteRuntimeError,
            match="Blender reciprocal-route build failed",
        ):
            run_reciprocal_route_build(
                base_build=base,
                repo_root=_REPO_ROOT,
                build_root=build_root,
            )
        # No final directory published.
        children = [p for p in build_root.iterdir()]
        assert all(p.name.startswith(".staging-") is False for p in children), (
            f"staging not cleaned: {children}"
        )
        assert not any(
            p.is_dir() and not p.name.startswith(".") for p in children
        )
    finally:
        import shutil

        shutil.rmtree(build_root, ignore_errors=True)


def test_run_build_rejects_timeout_and_cleans_staging(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_root = _private_build_root()
    build_root.mkdir(parents=True)
    try:

        def slow(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args, timeout=1)

        monkeypatch.setattr(subprocess, "run", slow)
        with pytest.raises(
            ReciprocalRouteRuntimeError,
            match="exceeded",
        ):
            run_reciprocal_route_build(
                base_build=base,
                repo_root=_REPO_ROOT,
                build_root=build_root,
                timeout_seconds=1,
            )
        # No final directory published; staging cleaned in finally.
        children = list(build_root.iterdir())
        assert not any(
            p.is_dir() and not p.name.startswith(".") for p in children
        ), f"unexpected directory: {children}"
    finally:
        import shutil

        shutil.rmtree(build_root, ignore_errors=True)


def test_run_build_rejects_tampered_report_identity(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Blender emits a report with a wrong build_id, publication must fail."""

    build_root = _private_build_root()
    build_root.mkdir(parents=True)
    try:

        def tampered(args, **kwargs):
            sep = args.index("--")
            request_path = Path(args[sep + 1])
            staging = Path(args[sep + 2])
            _fake_blender_success(request_path, staging)
            # Tamper the report's build_id after writing.
            report_path = staging / RECIPROCAL_ROUTE_REPORT_NAME
            raw = report_path.read_bytes()
            payload = json.loads(raw)
            payload["build_id"] = "f" * 64
            report_path.write_bytes(
                canary._canonical_json_bytes(payload),
            )
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr="",
            )

        monkeypatch.setattr(subprocess, "run", tampered)
        with pytest.raises(ReciprocalRouteRuntimeError):
            run_reciprocal_route_build(
                base_build=base,
                repo_root=_REPO_ROOT,
                build_root=build_root,
            )
    finally:
        import shutil

        shutil.rmtree(build_root, ignore_errors=True)
