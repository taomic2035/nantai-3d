"""Content-addressed Blender runtime bridge for EnvironmentModulePlan."""

from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
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


def _load_blender_runtime(monkeypatch: pytest.MonkeyPatch):
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/blender/apply_environment_modules.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_test_apply_environment_modules_batch21",
        script,
    )
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


@pytest.mark.parametrize(
    "policy",
    [
        "world-xy",
        "dominant-axis-box",
        "roof-slope",
        "object-long-axis",
        "leaf-card",
    ],
)
def test_environment_material_contract_accepts_bound_uv_metadata(
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
) -> None:
    runtime = _load_blender_runtime(monkeypatch)
    material = {
        "uv_policy": policy,
        "nv_nominal_tile_m": 0.8,
        "nv_surface_color_input": "nv_surface_color",
    }

    assert runtime._material_contract(material) == (
        policy,
        0.8,
        "nv_surface_color",
    )


@pytest.mark.parametrize(
    "material",
    [
        {},
        {
            "uv_policy": "unknown",
            "nv_nominal_tile_m": 1.0,
            "nv_surface_color_input": "nv_surface_color",
        },
        {
            "uv_policy": "world-xy",
            "nv_nominal_tile_m": 0.0,
            "nv_surface_color_input": "nv_surface_color",
        },
        {
            "uv_policy": "world-xy",
            "nv_nominal_tile_m": math.nan,
            "nv_surface_color_input": "nv_surface_color",
        },
        {
            "uv_policy": "world-xy",
            "nv_nominal_tile_m": 1.0,
            "nv_surface_color_input": "wrong",
        },
    ],
)
def test_environment_material_contract_rejects_unbound_metadata(
    monkeypatch: pytest.MonkeyPatch,
    material: dict[str, object],
) -> None:
    runtime = _load_blender_runtime(monkeypatch)

    with pytest.raises(runtime.RuntimeBuildError, match="material contract"):
        runtime._material_contract(material)


def test_environment_runtime_no_longer_uses_modulo_uv_proxy() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/blender/apply_environment_modules.py"
    ).read_text(encoding="utf-8")

    assert "% 1.0" not in source
    assert 'name="nv_uv0"' in source
    assert 'name="nv_surface_color"' in source


def test_waterwheel_geometry_uses_plan_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving only the canonical anchor translates every wheel vertex."""

    runtime = _load_blender_runtime(monkeypatch)
    original = runtime._bridge_geometry(
        "waterwheel-wheel-001",
        {"waterwheel_assembly_anchor_m": [-185.2, -115.0, 43.15]},
    )
    moved = runtime._bridge_geometry(
        "waterwheel-wheel-001",
        {"waterwheel_assembly_anchor_m": [-175.2, -95.0, 73.15]},
    )

    assert len(original.vertices) == len(moved.vertices)
    assert len(original.faces) == len(moved.faces)
    for before, after in zip(original.vertices, moved.vertices, strict=True):
        assert tuple(after[axis] - before[axis] for axis in range(3)) == pytest.approx(
            (10.0, 20.0, 30.0),
        )


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
            "textured_module_meshes": 45,
            "valid_uv_module_meshes": 45,
            "valid_surface_color_module_meshes": 45,
        },
        "validation": {
            "base_registry_matches": True,
            "module_registry_matches": True,
            "finite_nonempty_module_meshes": True,
            "material_bindings_match": True,
            "design_sources_are_provenance_only": True,
            "uv_contracts_match": True,
            "surface_color_contracts_match": True,
        },
        "artifact": {
            "name": "village-modules.blend",
            "kind": "blender-scene",
            "sha256": output_sha,
            "size_bytes": output_path.stat().st_size,
        },
    }


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("counts", "textured_module_meshes"),
        ("counts", "valid_uv_module_meshes"),
        ("counts", "valid_surface_color_module_meshes"),
        ("validation", "uv_contracts_match"),
        ("validation", "surface_color_contracts_match"),
    ],
)
def test_report_requires_material_contract_evidence(
    tmp_path: Path,
    section: str,
    field: str,
) -> None:
    request = build_environment_module_runtime_request(
        base_build=_base_build(tmp_path),
        repo_root=Path(__file__).resolve().parents[1],
    )
    output = tmp_path / "village-modules.blend"
    output.write_bytes(b"module-build")
    payload = _report_payload(request, output)
    del payload[section][field]

    with pytest.raises(ValidationError):
        EnvironmentModuleBuildReport.model_validate(payload)


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


def test_environment_module_runtime_request_roundtrip_canonical_bytes(
    tmp_path: Path,
) -> None:
    """REVIEW-CODEX-020 §3 gate 1+5: canonical bytes survive model_validate_json round-trip.

    The ``facade_orientation_deg`` field added to ``ObjectRegistryEntry`` uses
    ``exclude_if=lambda value: value is None`` so that ``None`` (the default
    for all current entries) is absent from ``model_dump(mode="json")``.
    Historical 175-entry request bytes and digests must remain reproducible.
    """

    base = _base_build(tmp_path)
    request = build_environment_module_runtime_request(
        base_build=base,
        repo_root=Path(__file__).resolve().parents[1],
    )

    # Every entry has facade_orientation_deg=None (not yet populated)
    assert all(row.facade_orientation_deg is None for row in request.object_registry)

    original_bytes = canonical_environment_module_runtime_request_bytes(request)
    reloaded = type(request).model_validate_json(original_bytes)
    recomputed_bytes = canonical_environment_module_runtime_request_bytes(reloaded)

    assert recomputed_bytes == original_bytes, (
        "canonical bytes changed after round-trip — historical provenance break"
    )
    assert reloaded.build_id == request.build_id
    assert reloaded.base_object_registry_sha256 == request.base_object_registry_sha256


_EXISTING_ARTIFACT = (
    Path(__file__).resolve().parents[1]
    / ".nantai-studio/synthetic-village/hybrid-v4/work/environment-modules/"
    "61f70a6c1abfc861e76564220a147027d5f99c86f907295ba7598a8bc68ffca5/"
    "module-build-request.json"
)


@pytest.mark.skipif(
    not _EXISTING_ARTIFACT.exists(),
    reason="private artifact not available in this environment",
)
def test_existing_module_build_request_artifact_remains_canonical() -> None:
    """REVIEW-CODEX-020 §3 gate 1: on-disk artifact bytes == recomputed canonical bytes."""

    from pipeline.synthetic_village.environment_module_runtime import (
        EnvironmentModuleRuntimeRequest,
        canonical_environment_module_runtime_request_bytes,
    )

    raw = _EXISTING_ARTIFACT.read_bytes()
    request = EnvironmentModuleRuntimeRequest.model_validate_json(raw)
    recomputed = canonical_environment_module_runtime_request_bytes(request)
    assert recomputed == raw, (
        "on-disk module-build-request.json no longer matches canonical bytes — "
        "facade_orientation_deg field broke historical provenance"
    )
