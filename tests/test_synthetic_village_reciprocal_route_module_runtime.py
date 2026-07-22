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
import importlib.util
import json
import math
import subprocess
import sys
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
    load_reciprocal_route_runtime_request,
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


def test_runtime_request_loader_requires_canonical_bounded_bytes(
    tmp_path: Path,
) -> None:
    request = build_reciprocal_route_runtime_request(
        base_build=_base_build(tmp_path),
    )
    path = tmp_path / "reciprocal-route-build-request.json"
    canonical = canonical_reciprocal_route_runtime_request_bytes(request)
    path.write_bytes(canonical)

    assert load_reciprocal_route_runtime_request(path) == request

    path.write_bytes(canonical.rstrip(b"\n"))
    with pytest.raises(ReciprocalRouteRuntimeError, match="runtime request"):
        load_reciprocal_route_runtime_request(path)


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


def test_blender_runtime_accepts_canonical_host_request(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Blender-side validator must accept the host's canonical request."""

    request = build_reciprocal_route_runtime_request(
        base_build=base,
        repo_root=_REPO_ROOT,
    )
    script_path = _REPO_ROOT / "scripts/blender/apply_reciprocal_route_modules.py"
    spec = importlib.util.spec_from_file_location(
        "_test_apply_reciprocal_route_modules",
        script_path,
    )
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)

    payload = request.model_dump(mode="json")
    assert runtime._validate_request(payload) == payload


def test_blender_runtime_tags_module_root_and_mesh_for_six_layer_rendering(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every new root and mesh must satisfy the frozen renderer tag contract."""

    request = build_reciprocal_route_runtime_request(
        base_build=base,
        repo_root=_REPO_ROOT,
    )
    script_path = _REPO_ROOT / "scripts/blender/apply_reciprocal_route_modules.py"
    spec = importlib.util.spec_from_file_location(
        "_test_apply_reciprocal_route_render_tags",
        script_path,
    )
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)

    class FakeObject(dict):
        pass_index = 0

    row = request.object_registry[175].model_dump(mode="json")
    root = FakeObject()
    mesh = FakeObject()
    runtime._tag(root, row)
    runtime._tag_render_mesh(mesh, row)

    assert root["nv_root"] is True
    assert root["nv_variant_id"] == ""
    assert root.pass_index == row["instance_id"]
    assert mesh["nv_stable_id"] == row["object_id"]
    assert mesh["nv_root_id"] == row["object_id"]
    assert mesh["nv_instance_id"] == row["instance_id"]
    assert mesh["nv_semantic_id"] == row["semantic_id"]
    assert mesh["nv_material_id"] == row["material_id"]
    assert mesh["nv_variant_id"] == ""
    assert mesh.pass_index == row["instance_id"]


# --------------------------------------------------------------------------- #
# Phase 4.3 amendments: 5-panel passage geometry + topology proxy mesh.
#
# These tests exercise the runtime-script functions that respond to the
# three route geometry problems exposed by the Phase 4 probe
# (FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md §"待处理"):
#   * clearance_min_m ≈ 0.3 m (CRITICAL) -> _module_geometry now emits
#     a 5-panel passage (floor / ceiling / left / right wall) so the
#     upward ray hits the ceiling underside at ~2.475 m.
#   * topology_ref object has no mesh (HIGH) -> _build_topology_proxies
#     emits one 1.5 m box per module placed ``_TOPOLOGY_PROXY_OFFSET_Y_M``
#     metres in -y direction from the module's first part center (Phase
#     4.3 amendment: originally placed at look_at_m 25 m away, which made
#     every module fail the probe's 2.0 m attachment threshold),
#     tagged ``nv_proxy_topology=True`` and parented to the module's
#     first part root.  Proxies do NOT carry nv_root and do NOT enter
#     the 218-root canonical registry.
#   * bridge / watermill intersect aux-terrain (MEDIUM) -> bridge z
#     50 -> 55 and watermill z 45 -> 52 lifts the modules above the
#     aux-terrain whose terrain_height_m peaks at ~53.27 / ~48.64 m.
# --------------------------------------------------------------------------- #


def _load_runtime_module(monkeypatch: pytest.MonkeyPatch):
    """Load apply_reciprocal_route_modules.py with bpy stubbed out."""

    script_path = _REPO_ROOT / "scripts/blender/apply_reciprocal_route_modules.py"
    spec = importlib.util.spec_from_file_location(
        "_test_apply_reciprocal_route_modules_phase43",
        script_path,
    )
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


def _load_probe_runtime_module(monkeypatch: pytest.MonkeyPatch):
    """Load the Blender probe script while stubbing its Blender-only imports."""

    script_path = _REPO_ROOT / "scripts/blender/probe_reciprocal_route_modules.py"
    spec = importlib.util.spec_from_file_location(
        "_test_probe_reciprocal_route_modules_phase43",
        script_path,
    )
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "mathutils", SimpleNamespace(Vector=object))
    monkeypatch.setitem(
        sys.modules,
        "mathutils.bvhtree",
        SimpleNamespace(BVHTree=object),
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


def test_junction_vegetation_clearance_targets_derive_from_bound_plan(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opening must come from the bound side-attachment layout.

    No Blender-side coordinate whitelist may invent a second geometry truth.
    """

    runtime = _load_runtime_module(monkeypatch)
    plan = build_default_reciprocal_route_module_plan(
        scene=base.scene_plan,
        elevated_topology=base.elevated_topology,
        environment_module_plan=base.env_module_plan,
    )

    targets = runtime._junction_vegetation_clearance_targets(
        plan.model_dump(mode="json"),
    )

    assert targets == (
        {
            "module_id": "covered-gallery-underpass",
            "part_id": "gallery-branch-attachment-side-001",
            "topology_ref": "path-network-003",
            "aabb_xy_m": pytest.approx((56.2, 57.8, 43.7, 46.3)),
        },
    )


def test_junction_vegetation_component_filter_selects_only_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnected roadside plants outside the opening must survive."""

    runtime = _load_runtime_module(monkeypatch)
    vertices = (
        (-0.4, -0.3, 0.0),
        (0.4, -0.3, 0.0),
        (0.0, 0.4, 0.0),
        (4.7, -0.3, 0.0),
        (5.3, -0.3, 0.0),
        (5.0, 0.4, 0.0),
        (9.6, -0.3, 0.0),
        (10.4, -0.3, 0.0),
        (10.0, 0.4, 0.0),
    )
    faces = ((0, 1, 2), (3, 4, 5), (6, 7, 8))

    selected = runtime._vegetation_components_overlapping_xy(
        vertices,
        faces,
        (4.5, 5.5, -0.5, 0.5),
    )

    assert selected == ((3, 4, 5),)


def test_junction_vegetation_clearance_rejects_missing_bound_module(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed plan cannot silently skip the promised junction opening."""

    runtime = _load_runtime_module(monkeypatch)
    plan = build_default_reciprocal_route_module_plan(
        scene=base.scene_plan,
        elevated_topology=base.elevated_topology,
        environment_module_plan=base.env_module_plan,
    ).model_dump(mode="json")
    plan["modules"] = [
        module
        for module in plan["modules"]
        if module["module_id"] != "covered-gallery-underpass"
    ]

    with pytest.raises(
        runtime.RuntimeBuildError,
        match="junction vegetation module is absent",
    ):
        runtime._junction_vegetation_clearance_targets(plan)


def test_topology_proxy_id_for_module_uses_module_specific_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy stable_id must be ``{topology_ref}::{module_id}`` so the
    v1 EMPTY root's ``{topology_ref}`` stable_id is not collided with."""

    runtime = _load_runtime_module(monkeypatch)
    assert runtime._topology_proxy_id_for_module(
        "bridge-deck-crossing", "path-network-001",
    ) == "path-network-001::bridge-deck-crossing"
    assert runtime._topology_proxy_id_for_module(
        "watermill-tailrace", "path-network-001",
    ) == "path-network-001::watermill-tailrace"


def test_topology_proxy_targets_use_module_attachment_topology(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy refs follow module attachment, not camera placement topology."""

    runtime = _load_runtime_module(monkeypatch)
    plan = build_default_reciprocal_route_module_plan(
        scene=base.scene_plan,
        elevated_topology=base.elevated_topology,
        environment_module_plan=base.env_module_plan,
    )
    plan_dict = plan.model_dump(mode="json")
    targets = runtime._topology_proxy_targets(plan_dict)
    assert len(targets) == 6
    assert {
        module_id: topology_ref
        for module_id, topology_ref, _look_at_m in targets
    } == {
        "central-courtyard-downhill": "path-network-003",
        "bridge-deck-crossing": "path-network-001",
        "watermill-tailrace": "path-network-001",
        "covered-gallery-underpass": "path-network-005",
        "forest-orchard-boundary": "path-network-002",
        "lower-valley-uphill": "path-network-001",
    }
    for module_id, topology_ref, look_at_m in targets:
        assert isinstance(module_id, str) and module_id
        assert isinstance(topology_ref, str) and topology_ref.startswith(
            "path-network-",
        )
        assert isinstance(look_at_m, tuple) and len(look_at_m) == 3
        assert all(
            isinstance(v, float) and math.isfinite(v) for v in look_at_m
        )


def test_topology_proxy_targets_rejects_missing_role_camera_candidates(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_runtime_module(monkeypatch)
    plan = build_default_reciprocal_route_module_plan(
        scene=base.scene_plan,
        elevated_topology=base.elevated_topology,
        environment_module_plan=base.env_module_plan,
    )
    plan_dict = plan.model_dump(mode="json")
    plan_dict["role_camera_candidates"] = []
    with pytest.raises(
        runtime.RuntimeBuildError,
        match="not exactly six entries",
    ):
        runtime._topology_proxy_targets(plan_dict)


def test_topology_proxy_targets_rejects_invalid_look_at_m(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_runtime_module(monkeypatch)
    plan = build_default_reciprocal_route_module_plan(
        scene=base.scene_plan,
        elevated_topology=base.elevated_topology,
        environment_module_plan=base.env_module_plan,
    )
    plan_dict = plan.model_dump(mode="json")
    # Tamper with one candidate's look_at_m.
    plan_dict["role_camera_candidates"][0]["look_at_m"] = [
        float("nan"),
        0.0,
        0.0,
    ]
    with pytest.raises(
        runtime.RuntimeBuildError,
        match="invalid look_at_m",
    ):
        runtime._topology_proxy_targets(plan_dict)


def test_topology_proxy_targets_rejects_duplicate_module_id(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_runtime_module(monkeypatch)
    plan = build_default_reciprocal_route_module_plan(
        scene=base.scene_plan,
        elevated_topology=base.elevated_topology,
        environment_module_plan=base.env_module_plan,
    )
    plan_dict = plan.model_dump(mode="json")
    # Duplicate the first candidate's role_module_id into the second.
    plan_dict["role_camera_candidates"][1]["role_module_id"] = (
        plan_dict["role_camera_candidates"][0]["role_module_id"]
    )
    with pytest.raises(
        runtime.RuntimeBuildError,
        match="duplicated",
    ):
        runtime._topology_proxy_targets(plan_dict)


def test_tag_topology_proxy_does_not_set_nv_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy must NOT carry nv_root=True (otherwise the 218-root
    canonical registry count breaks)."""

    runtime = _load_runtime_module(monkeypatch)

    class FakeObject(dict):
        pass

    proxy = FakeObject()
    runtime._tag_topology_proxy(
        proxy,
        "bridge-deck-crossing",
        "path-network-001",
    )
    assert proxy.get("nv_root") is None
    assert proxy["nv_proxy_topology"] is True
    assert proxy["nv_stable_id"] == (
        "path-network-001::bridge-deck-crossing"
    )
    assert proxy["nv_proxy_module_id"] == "bridge-deck-crossing"
    assert proxy["nv_proxy_topology_ref"] == "path-network-001"
    # Low-trust literals must match the module-mesh contract.
    assert proxy["nv_stage"] == "modeled-unverified"
    assert proxy["nv_trust_effect"] == "none"
    assert proxy["nv_geometry_usability"] == "preview-only"
    assert proxy.hide_render is True
    assert proxy.hide_viewport is False


def _geometry_part(
    geometry_family: str,
    semantic_id: int,
    *,
    orientation_deg: float = 0.0,
) -> dict:
    return {
        "part_id": f"test-{geometry_family}",
        "semantic_id": semantic_id,
        "geometry_family": geometry_family,
        "part_layout": {
            "center_m": [0.0, 0.0, 0.0],
            "extent_m": [1.6, 2.6, 2.5],
            "orientation_deg": orientation_deg,
        },
    }


def test_module_geometry_open_path_has_no_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open route keeps measurable low curbs but never invents a roof."""

    runtime = _load_runtime_module(monkeypatch)
    assembler = runtime._module_geometry(_geometry_part("open-path", 7))

    assert len(assembler.vertices) == 24
    assert len(assembler.faces) == 18
    z_values = [v[2] for v in assembler.vertices]
    assert min(z_values) < 0.0
    assert max(z_values) < 0.5


def test_module_geometry_covered_passage_retains_finite_roof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an explicitly building-semantic passage gets full sides and roof."""

    runtime = _load_runtime_module(monkeypatch)
    assembler = runtime._module_geometry(_geometry_part("covered-passage", 3))

    assert len(assembler.vertices) == 32
    assert len(assembler.faces) == 24
    assert max(vertex[2] for vertex in assembler.vertices) >= 2.55


def test_module_geometry_bridge_deck_is_open_overhead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_runtime_module(monkeypatch)
    assembler = runtime._module_geometry(_geometry_part("bridge-deck", 4))

    assert max(vertex[2] for vertex in assembler.vertices) < 1.0


def test_module_geometry_families_do_not_serialize_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All authored blockout families must produce a distinct mesh payload."""

    runtime = _load_runtime_module(monkeypatch)
    family_semantics = {
        "open-path": 7,
        "covered-passage": 3,
        "bridge-deck": 4,
        "building-shell": 3,
        "structural-frame": 3,
        "drainage-channel": 5,
        "retaining-structure": 12,
        "guard-rail": 13,
        "service-prop": 13,
        "vegetation-band": 13,
    }
    payloads = set()
    for family, semantic_id in family_semantics.items():
        assembler = runtime._module_geometry(_geometry_part(family, semantic_id))
        payloads.add((tuple(assembler.vertices), tuple(assembler.faces)))

    assert len(payloads) == len(family_semantics)


def test_building_shell_is_a_view_through_portal_not_a_back_wall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shell before later parts must not occlude the full role sequence."""

    runtime = _load_runtime_module(monkeypatch)
    assembler = runtime._module_geometry(_geometry_part("building-shell", 3))
    boxes = [assembler.vertices[index:index + 8] for index in range(0, len(assembler.vertices), 8)]
    for box in boxes:
        x_extent = max(v[0] for v in box) - min(v[0] for v in box)
        y_extent = max(v[1] for v in box) - min(v[1] for v in box)
        z_extent = max(v[2] for v in box) - min(v[2] for v in box)
        assert not (x_extent > 1.5 and y_extent < 0.2 and z_extent > 2.0)


def test_guard_rail_has_low_semantic_base_for_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thin rails retain a low prop-semantic base visible at route distance."""

    runtime = _load_runtime_module(monkeypatch)
    assembler = runtime._module_geometry(_geometry_part("guard-rail", 13))

    assert len(assembler.vertices) == 96
    x_values = [vertex[0] for vertex in assembler.vertices]
    assert min(x_values) < -0.6
    assert max(x_values) > 0.6
    for base in (assembler.vertices[40:48], assembler.vertices[-8:]):
        assert max(vertex[2] for vertex in base) <= 0.12
        assert min(vertex[2] for vertex in base) < 0.0


@pytest.mark.parametrize(
    ("part", "message"),
    [
        ({"semantic_id": 7, "part_layout": {}}, "geometry_family"),
        (
            {
                "geometry_family": "covered-passage",
                "semantic_id": 7,
                "part_layout": {},
            },
            "incompatible",
        ),
        (
            {
                "geometry_family": "mystery",
                "semantic_id": 7,
                "part_layout": {},
            },
            "geometry_family",
        ),
    ],
)
def test_module_geometry_rejects_invalid_classification(
    monkeypatch: pytest.MonkeyPatch,
    part: dict,
    message: str,
) -> None:
    runtime = _load_runtime_module(monkeypatch)
    with pytest.raises(runtime.RuntimeBuildError, match=message):
        runtime._module_geometry(part)


def test_module_geometry_rotates_open_path_curbs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yaw rotates both each curb box and its local-x centre offset.

    A 270-degree passage runs along world +x, so its walls must straddle
    world y.  Rotating only the boxes leaves their centres on world x and
    makes the clearance probe hit the floor/terrain instead of the walls.
    """

    runtime = _load_runtime_module(monkeypatch)
    part = _geometry_part("open-path", 7, orientation_deg=270.0)
    part["part_layout"]["center_m"] = [30.0, 40.0, 77.0]
    assembler = runtime._module_geometry(part)
    left_curb = assembler.vertices[8:16]
    right_curb = assembler.vertices[16:24]
    left_center = tuple(sum(vertex[axis] for vertex in left_curb) / 8 for axis in range(3))
    right_center = tuple(sum(vertex[axis] for vertex in right_curb) / 8 for axis in range(3))

    assert left_center[:2] == pytest.approx((30.0, 40.75))
    assert right_center[:2] == pytest.approx((30.0, 39.25))


def test_probe_selects_only_explicit_route_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_probe_runtime_module(monkeypatch)
    parts = [
        {"part_id": "a", "instance_id": 1, "geometry_family": "open-path"},
        {"part_id": "b", "instance_id": 2, "geometry_family": "guard-rail"},
        {"part_id": "c", "instance_id": 3, "geometry_family": "covered-passage"},
        {"part_id": "d", "instance_id": 4, "geometry_family": "bridge-deck"},
        {"part_id": "e", "instance_id": 5, "geometry_family": "drainage-channel"},
    ]

    selected = runtime._route_parts(parts)

    assert [part["part_id"] for part in selected] == ["a", "c", "d"]


def test_probe_rejects_missing_geometry_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_probe_runtime_module(monkeypatch)
    with pytest.raises(runtime.ProbeBuildError, match="geometry_family"):
        runtime._route_parts([{"part_id": "legacy", "instance_id": 1}])


def test_every_default_module_has_real_route_probe_parts(
    base: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_probe_runtime_module(monkeypatch)
    plan = build_default_reciprocal_route_module_plan(
        scene=base.scene_plan,
        elevated_topology=base.elevated_topology,
        environment_module_plan=base.env_module_plan,
    )

    counts = {
        module.module_id: len(
            runtime._route_parts(
                [part.model_dump(mode="json") for part in module.parts],
            ),
        )
        for module in plan.modules
    }

    assert counts == {
        "central-courtyard-downhill": 5,
        "bridge-deck-crossing": 5,
        "watermill-tailrace": 3,
        "covered-gallery-underpass": 5,
        "forest-orchard-boundary": 5,
        "lower-valley-uphill": 4,
    }


def test_probe_requires_finite_clearance_for_covered_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_probe_runtime_module(monkeypatch)

    assert runtime._clearance_failures(
        [
            ("open-path", None),
            ("bridge-deck", None),
            ("covered-passage", 2.5),
        ],
    ) == []
    assert runtime._clearance_failures(
        [("covered-passage", None)],
    ) == ["covered route upward ray missed (finite roof unavailable)"]
    assert runtime._clearance_failures(
        [("covered-passage", 2.0)],
    ) == ["clearance_min_m=2.000 < 2.400"]


def test_topology_proxy_geometry_places_box_at_center(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy box is centred on the given center (Phase 4.3 amendment:
    the center is computed by ``_topology_proxy_center``, no longer the
    role candidate's look_at_m)."""

    runtime = _load_runtime_module(monkeypatch)
    center = (-150.0, -125.0, 53.0)
    assembler = runtime._topology_proxy_geometry(center)
    # One box = 8 vertices, 6 faces.
    assert len(assembler.vertices) == 8
    assert len(assembler.faces) == 6
    # The box extends ±0.75 m around the center.
    x_values = [v[0] for v in assembler.vertices]
    y_values = [v[1] for v in assembler.vertices]
    z_values = [v[2] for v in assembler.vertices]
    assert min(x_values) == -150.75
    assert max(x_values) == -149.25
    assert min(y_values) == -125.75
    assert max(y_values) == -124.25
    assert min(z_values) == 52.25
    assert max(z_values) == 53.75


def test_topology_proxy_center_offset_y_m_constant_is_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_TOPOLOGY_PROXY_OFFSET_Y_M`` must equal 2.5 m so the proxy sits
    2.5 m in -y direction from the first part center.  Changing this
    constant changes ``runtime_script_sha256`` and therefore
    ``build_id``."""

    runtime = _load_runtime_module(monkeypatch)
    assert runtime._TOPOLOGY_PROXY_OFFSET_Y_M == 2.5


def test_topology_proxy_center_places_proxy_in_negative_y_from_first_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4.3 amendment (FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md
    §"待处理" item: "topology attachment distance wrong"): the proxy
    must be placed ``_TOPOLOGY_PROXY_OFFSET_Y_M`` metres in -y direction
    from the module's first part center, not at the role camera's
    look_at_m 25 m away.  The previous placement (at look_at_m) made
    every module fail the probe's 2.0 m attachment threshold because
    the camera lookahead is 25 m.  The new placement puts the proxy
    2.5 m away (closest surface at 1.75 m), within the threshold.

    The -y direction is chosen because module parts extend in +y
    (instance_id increases -> y increases by
    ``_DEFAULT_PART_SPACING_Y_M``), so -y is always "away from parts"
    and the proxy does not overlap any module mesh.
    """

    runtime = _load_runtime_module(monkeypatch)
    first_part_center = (40.0, 30.0, 70.0)
    proxy_center = runtime._topology_proxy_center(first_part_center)
    assert proxy_center == (40.0, 27.5, 70.0)


def test_topology_proxy_center_closest_surface_distance_within_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closest surface point on the proxy (its +y face) is at
    distance ``_TOPOLOGY_PROXY_OFFSET_Y_M - 0.75 = 1.75 m`` from the
    first part center, which must be <=
    ``MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M = 2.0 m`` so the probe
    reports ``passed=True``."""

    runtime = _load_runtime_module(monkeypatch)
    from pipeline.synthetic_village.reciprocal_route_probe import (
        MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M,
    )
    offset_y = runtime._TOPOLOGY_PROXY_OFFSET_Y_M
    proxy_half_extent = 0.75  # _DEFAULT_TOPOLOGY_PROXY_EXTENT_M[1] / 2
    closest_surface_distance = offset_y - proxy_half_extent
    assert closest_surface_distance <= MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M, (
        f"closest surface distance {closest_surface_distance} m > "
        f"MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M "
        f"{MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M} m: probe would fail"
    )


def test_default_part_extent_passes_standing_eye_clearance() -> None:
    """The Phase 4.3 default extent (1.6, 1.6, 2.5) combined with the
    4-panel passage geometry and ``_PASSAGE_RAY_SAFE_GAP_M`` must give
    an upward clearance >= 2.4 m (the MIN_ROUTE_CLEARANCE_M threshold).
    Upward ray from (cx, cy, cz) hits ceiling underside at z = cz + sz +
    gap, so the upward clearance distance is sz + gap."""

    from pipeline.synthetic_village.reciprocal_route_module import (
        _DEFAULT_PART_EXTENT_M,
    )
    from pipeline.synthetic_village.reciprocal_route_probe import (
        MIN_ROUTE_CLEARANCE_M,
    )
    sx, sy, sz = _DEFAULT_PART_EXTENT_M
    # Upward ray from part center (cz) hits the ceiling's underside at
    # z = cz + sz + gap.  The upward clearance distance is sz + gap.
    # The runtime script reads _PASSAGE_RAY_SAFE_GAP_M from its own
    # constants; this test mirrors the constant value to assert the
    # design invariant.  If the constant changes, the test must too.
    ray_safe_gap = 0.001
    upward_clearance = sz + ray_safe_gap
    assert upward_clearance >= MIN_ROUTE_CLEARANCE_M, (
        f"upward clearance {upward_clearance} m < "
        f"MIN_ROUTE_CLEARANCE_M {MIN_ROUTE_CLEARANCE_M} m"
    )


def test_default_part_extent_passes_standing_eye_clear_width() -> None:
    """The inner width (sx - 2 * wall_thickness) must be >=
    MIN_ROUTE_CLEAR_WIDTH_M = 1.2 m."""

    from pipeline.synthetic_village.reciprocal_route_module import (
        _DEFAULT_PART_EXTENT_M,
    )
    from pipeline.synthetic_village.reciprocal_route_probe import (
        MIN_ROUTE_CLEAR_WIDTH_M,
    )
    sx, _sy, _sz = _DEFAULT_PART_EXTENT_M
    # Wall thickness is 0.1 m on each side.
    inner_width = sx - 0.2
    assert inner_width >= MIN_ROUTE_CLEAR_WIDTH_M, (
        f"inner width {inner_width} m < "
        f"MIN_ROUTE_CLEAR_WIDTH_M {MIN_ROUTE_CLEAR_WIDTH_M} m"
    )


def test_default_part_extent_y_covers_spacing_to_avoid_perpendicular_ray_miss() -> None:
    """Phase 4.3 (FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md §"待处理"
    item: "perpendicular ray missed"): the probe interpolates 5 samples
    along the polyline through part centers spaced ``_DEFAULT_PART_SPACING_Y_M``
    apart.  Each sample casts perpendicular rays along x to measure the
    inner width between left and right walls.  If ``extent_y <
    spacing_y``, adjacent parts' walls do not overlap in y and samples
    that fall in the gap (between part centers) hit nothing -- recorded
    as ``left_clear_m=None, right_clear_m=None`` and the module route
    probe fails with ``perpendicular ray missed``.

    Enforcing ``extent_y >= spacing_y`` guarantees every sample position
    along the polyline is inside some wall's y range, so the
    perpendicular ray always hits a wall.
    """

    from pipeline.synthetic_village.reciprocal_route_module import (
        _DEFAULT_PART_EXTENT_M,
        _DEFAULT_PART_SPACING_Y_M,
    )
    _sx, extent_y, _sz = _DEFAULT_PART_EXTENT_M
    assert extent_y >= _DEFAULT_PART_SPACING_Y_M, (
        f"extent_y {extent_y} m < spacing_y {_DEFAULT_PART_SPACING_Y_M} m: "
        f"adjacent parts' walls do not overlap in y, so perpendicular "
        f"ray casts from samples between part centers miss and the "
        f"module route probe fails with 'perpendicular ray missed'."
    )


@pytest.mark.parametrize(
    "module_id",
    (
        "bridge-deck-crossing",
        "watermill-tailrace",
        "forest-orchard-boundary",
    ),
)
def test_batch20_role_floor_is_derived_from_authored_terrain_peak(
    module_id: str,
) -> None:
    """Batch 20 roles clear the terrain peak at their exact authored XYs."""

    from pipeline.synthetic_village.reciprocal_route_module import (
        _BATCH20_ROLE_PART_LAYOUT_XY_ORIENTATION,
        _NONCENTRAL_FLOOR_CLEARANCE_M,
        _flat_module_floor_z,
    )
    from pipeline.synthetic_village.scene_plan import terrain_height_m

    peak = max(
        terrain_height_m(x, y)
        for x, y, _orientation in (
            _BATCH20_ROLE_PART_LAYOUT_XY_ORIENTATION[module_id].values()
        )
    )
    assert _flat_module_floor_z(module_id) == pytest.approx(
        round(peak + _NONCENTRAL_FLOOR_CLEARANCE_M, 3),
    )


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
