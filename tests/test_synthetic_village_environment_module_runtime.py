"""Content-addressed Blender runtime bridge for EnvironmentModulePlan."""

from __future__ import annotations

import hashlib
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
from pipeline.synthetic_village.environment_module_runtime import (
    ENVIRONMENT_MODULE_RUNTIME_SCHEMA,
    EnvironmentModuleBuildReport,
    EnvironmentModuleRuntimeError,
    build_environment_module_runtime_request,
    canonical_environment_module_runtime_request_bytes,
    verify_environment_module_build_report,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _base_build(tmp_path: Path) -> SimpleNamespace:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    semantics = canary._semantic_registry()
    materials = canary._material_registry(scene)
    registry = canary._object_registry(scene, topology, semantics, materials)
    blend_path = tmp_path / "village-canary.blend"
    blend_path.write_bytes(b"verified-base-blend")
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"verified-blender")
    request = SimpleNamespace(
        scene_plan=scene,
        elevated_topology=topology,
    )
    return SimpleNamespace(
        build_id="a" * 64,
        build_report_sha256="b" * 64,
        blend_sha256=_sha(blend_path.read_bytes()),
        blend_size_bytes=blend_path.stat().st_size,
        blender_executable_sha256=_sha(executable.read_bytes()),
        blend_path=blend_path,
        executable=executable,
        object_registry=registry,
        semantic_registry=semantics,
        material_registry=materials,
        request=request,
    )


def test_request_extends_verified_registry_to_exact_175_instances(
    tmp_path: Path,
) -> None:
    base = _base_build(tmp_path)

    request = build_environment_module_runtime_request(
        base_build=base,
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert request.schema_version == ENVIRONMENT_MODULE_RUNTIME_SCHEMA
    assert request.verification_level == "L0"
    assert request.geometry_usability == "preview-only"
    assert request.stage == "modeled-unverified"
    assert request.trust_effect == "none"
    assert len(request.object_registry) == 175
    assert tuple(row.instance_id for row in request.object_registry) == tuple(
        range(1, 176),
    )
    assert request.object_registry[:130] == base.object_registry
    assert tuple(row.object_id for row in request.object_registry[130:]) == tuple(
        part.part_id
        for module in request.environment_module_plan.modules
        for part in module.parts
    )


def test_request_binds_exact_plan_base_blend_script_and_executable(
    tmp_path: Path,
) -> None:
    base = _base_build(tmp_path)
    scene = base.request.scene_plan
    topology = base.request.elevated_topology
    plan = build_default_environment_module_plan(
        scene=scene,
        elevated_topology=topology,
    )

    request = build_environment_module_runtime_request(
        base_build=base,
        environment_module_plan=plan,
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert request.base_build_id == base.build_id
    assert request.base_build_report_sha256 == base.build_report_sha256
    assert request.base_blend_sha256 == base.blend_sha256
    assert request.base_blender_executable_sha256 == base.blender_executable_sha256
    assert request.environment_module_plan_sha256 == environment_module_plan_sha256(
        plan,
    )
    assert request.runtime_script_sha256 == _sha(
        (
            Path(__file__).resolve().parents[1]
            / "scripts/blender/apply_environment_modules.py"
        ).read_bytes(),
    )


def test_request_identity_is_deterministic_and_cross_process_safe(
    tmp_path: Path,
) -> None:
    base = _base_build(tmp_path)

    left = build_environment_module_runtime_request(
        base_build=base,
        repo_root=Path(__file__).resolve().parents[1],
    )
    right = build_environment_module_runtime_request(
        base_build=base,
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert left == right
    assert canonical_environment_module_runtime_request_bytes(left).endswith(b"\n")
    payload = left.model_dump(mode="json")
    payload.pop("build_id")
    assert left.build_id == _sha(
        canary._canonical_json_bytes(payload),
    )


def test_request_rejects_non_contiguous_base_registry(tmp_path: Path) -> None:
    base = _base_build(tmp_path)
    base.object_registry = base.object_registry[:-1]

    with pytest.raises(
        EnvironmentModuleRuntimeError,
        match="exact 1..130",
    ):
        build_environment_module_runtime_request(
            base_build=base,
            repo_root=Path(__file__).resolve().parents[1],
        )


def test_request_rejects_plan_bound_to_different_scene(tmp_path: Path) -> None:
    base = _base_build(tmp_path)
    other_scene = base.request.scene_plan.model_copy(update={"seed": 43})
    plan = build_default_environment_module_plan(
        scene=other_scene,
        elevated_topology=build_elevated_topology_plan(other_scene),
    )

    with pytest.raises(
        EnvironmentModuleRuntimeError,
        match="does not match verified base scene",
    ):
        build_environment_module_runtime_request(
            base_build=base,
            environment_module_plan=plan,
            repo_root=Path(__file__).resolve().parents[1],
        )


def test_request_rejects_unknown_material_binding(tmp_path: Path) -> None:
    request = build_environment_module_runtime_request(
        base_build=_base_build(tmp_path),
        repo_root=Path(__file__).resolve().parents[1],
    )
    payload = request.model_dump(mode="json")
    payload["material_bindings"][0]["runtime_slot_id"] = "material-unknown-01"

    with pytest.raises(ValidationError):
        type(request).model_validate(payload)


def _report_payload(request, output_path: Path) -> dict[str, object]:
    output_sha = _sha(output_path.read_bytes())
    return {
        "schema_version": "nantai.synthetic-village.environment-module-build-report.v1",
        "build_id": request.build_id,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none",
        "base_build_id": request.base_build_id,
        "base_build_report_sha256": request.base_build_report_sha256,
        "base_blend_sha256": request.base_blend_sha256,
        "environment_module_plan_sha256": request.environment_module_plan_sha256,
        "runtime_script_sha256": request.runtime_script_sha256,
        "object_registry": request.object_registry,
        "material_bindings": request.material_bindings,
        "counts": {
            "base_canonical_roots": 130,
            "module_canonical_roots": 45,
            "canonical_roots": 175,
            "module_mesh_objects": 45,
        },
        "validation": {
            "base_registry_matches": True,
            "module_registry_matches": True,
            "finite_nonempty_module_meshes": True,
            "material_bindings_match": True,
            "design_sources_are_provenance_only": True,
        },
        "artifact": {
            "name": "village-modules.blend",
            "kind": "blender-scene",
            "sha256": output_sha,
            "size_bytes": output_path.stat().st_size,
        },
    }


def test_report_verifier_recomputes_output_bytes(tmp_path: Path) -> None:
    request = build_environment_module_runtime_request(
        base_build=_base_build(tmp_path),
        repo_root=Path(__file__).resolve().parents[1],
    )
    output = tmp_path / "village-modules.blend"
    output.write_bytes(b"module-build")
    report = EnvironmentModuleBuildReport.model_validate(
        _report_payload(request, output),
    )

    verify_environment_module_build_report(
        report,
        request=request,
        output_path=output,
    )

    output.write_bytes(b"tampered")
    with pytest.raises(
        EnvironmentModuleRuntimeError,
        match="artifact digest or size",
    ):
        verify_environment_module_build_report(
            report,
            request=request,
            output_path=output,
        )


def test_report_rejects_identity_substitution(tmp_path: Path) -> None:
    request = build_environment_module_runtime_request(
        base_build=_base_build(tmp_path),
        repo_root=Path(__file__).resolve().parents[1],
    )
    output = tmp_path / "village-modules.blend"
    output.write_bytes(b"module-build")
    payload = _report_payload(request, output)
    payload["environment_module_plan_sha256"] = "c" * 64
    report = EnvironmentModuleBuildReport.model_validate(payload)

    with pytest.raises(
        EnvironmentModuleRuntimeError,
        match="report identity",
    ):
        verify_environment_module_build_report(
            report,
            request=request,
            output_path=output,
        )


def test_report_rejects_tampered_module_object_registry(tmp_path: Path) -> None:
    """Swap one module part's material_id in the report's object_registry.

    The schema's own ``_registry_is_complete`` only checks instance_id range;
    it does not cross-check material_bindings against module part material_id.
    But ``verify_environment_module_build_report`` must reject any byte that
    disagrees with the request's canonical object_registry, so a tampered
    material_id surfaces as an identity mismatch at verification time.
    """

    request = build_environment_module_runtime_request(
        base_build=_base_build(tmp_path),
        repo_root=Path(__file__).resolve().parents[1],
    )
    output = tmp_path / "village-modules.blend"
    output.write_bytes(b"module-build")
    payload = _report_payload(request, output)
    tampered_registry = list(payload["object_registry"])
    original_row = tampered_registry[130]
    tampered_registry[130] = original_row.model_copy(
        update={"material_id": (original_row.material_id % 11) + 1},
    )
    payload["object_registry"] = tuple(tampered_registry)
    report = EnvironmentModuleBuildReport.model_validate(payload)

    with pytest.raises(
        EnvironmentModuleRuntimeError,
        match="report identity",
    ):
        verify_environment_module_build_report(
            report,
            request=request,
            output_path=output,
        )


def test_report_rejects_tampered_material_binding(tmp_path: Path) -> None:
    """Swap one material_binding's material_id in the report.

    The report schema does not re-derive ``material_bindings`` from the
    environment_module_plan (it only has the plan's sha256, not the plan
    itself).  But the verifier compares ``report.material_bindings`` against
    ``request.material_bindings`` as an identity pair, so a single swapped
    material_id must surface as a mismatch.
    """

    request = build_environment_module_runtime_request(
        base_build=_base_build(tmp_path),
        repo_root=Path(__file__).resolve().parents[1],
    )
    output = tmp_path / "village-modules.blend"
    output.write_bytes(b"module-build")
    payload = _report_payload(request, output)
    tampered_bindings = list(payload["material_bindings"])
    first = tampered_bindings[0]
    tampered_bindings[0] = first.model_copy(
        update={"material_id": (first.material_id % 11) + 1},
    )
    payload["material_bindings"] = tuple(tampered_bindings)
    report = EnvironmentModuleBuildReport.model_validate(payload)

    with pytest.raises(
        EnvironmentModuleRuntimeError,
        match="report identity",
    ):
        verify_environment_module_build_report(
            report,
            request=request,
            output_path=output,
        )


def test_report_rejects_tampered_base_blend_sha(tmp_path: Path) -> None:
    """Swap base_blend_sha256 to a different 64-hex string.

    ``base_blend_sha256`` is only a 64-hex string at the schema level; the
    schema cannot know whether it matches the real base build.  The verifier
    compares it against ``request.base_blend_sha256``, so any substitution
    must surface as a report identity mismatch.
    """

    request = build_environment_module_runtime_request(
        base_build=_base_build(tmp_path),
        repo_root=Path(__file__).resolve().parents[1],
    )
    output = tmp_path / "village-modules.blend"
    output.write_bytes(b"module-build")
    payload = _report_payload(request, output)
    payload["base_blend_sha256"] = "d" * 64
    report = EnvironmentModuleBuildReport.model_validate(payload)

    with pytest.raises(
        EnvironmentModuleRuntimeError,
        match="report identity",
    ):
        verify_environment_module_build_report(
            report,
            request=request,
            output_path=output,
        )
