"""TDD contract for the exact-218 -> exact-266 Blender runtime bridge."""

from __future__ import annotations

import copy
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import canary
from pipeline.synthetic_village import perimeter_closure_module as closure_module
from pipeline.synthetic_village.elevated_topology import build_elevated_topology_plan
from pipeline.synthetic_village.environment_module import (
    build_default_environment_module_plan,
)
from pipeline.synthetic_village.environment_module_runtime import (
    _MATERIAL_BINDING_ROWS,
    EnvironmentModuleMaterialBinding,
    _module_registry,
)
from pipeline.synthetic_village.perimeter_closure_module import (
    PerimeterClosurePlan,
    build_default_perimeter_closure_plan,
    perimeter_closure_plan_sha256,
)
from pipeline.synthetic_village.perimeter_closure_runtime import (
    PERIMETER_CLOSURE_ARTIFACT_NAME,
    PERIMETER_CLOSURE_BUILD_ENTRIES,
    PerimeterClosureArtifact,
    PerimeterClosureBuildCounts,
    PerimeterClosureBuildReport,
    PerimeterClosureBuildValidation,
    PerimeterClosureRuntimeError,
    PerimeterClosureSectorMeasurement,
    build_perimeter_closure_runtime_request,
    canonical_perimeter_closure_runtime_request_bytes,
    load_perimeter_closure_build_report,
    run_perimeter_closure_build,
    verify_perimeter_closure_build_report,
)
from pipeline.synthetic_village.reciprocal_route_module_runtime import (
    RECIPROCAL_ROUTE_ARTIFACT_NAME,
    RECIPROCAL_ROUTE_REPORT_NAME,
    RECIPROCAL_ROUTE_REQUEST_NAME,
    ReciprocalRouteBuildReport,
    build_reciprocal_route_runtime_request,
    canonical_reciprocal_route_runtime_request_bytes,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan

ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base_175(tmp_path: Path) -> SimpleNamespace:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    semantics = canary._semantic_registry()
    materials = canary._material_registry(scene)
    registry_130 = canary._object_registry(scene, topology, semantics, materials)
    environment_plan = build_default_environment_module_plan(
        scene=scene,
        elevated_topology=topology,
    )
    material_ids = {row.material_family: row.material_id for row in materials}
    bindings = tuple(
        EnvironmentModuleMaterialBinding(
            material_alias=alias,
            runtime_slot_id=runtime_slot,
            material_family=family,
            material_id=material_ids[family],
        )
        for alias, runtime_slot, family in _MATERIAL_BINDING_ROWS
    )
    registry_175 = (*registry_130, *_module_registry(environment_plan, bindings))
    blend_path = tmp_path / "village-modules.blend"
    blend_path.write_bytes(b"verified-175-root-blend")
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"verified-blender")
    return SimpleNamespace(
        build_id="a" * 64,
        build_report_sha256="b" * 64,
        blend_sha256=_sha256_file(blend_path),
        blender_executable_sha256=_sha256_file(executable),
        blend_path=blend_path,
        executable=executable,
        object_registry=registry_175,
        env_module_plan=environment_plan,
        scene_plan=scene,
        elevated_topology=topology,
        material_bindings=bindings,
    )


def _write_exact218_build(tmp_path: Path) -> tuple[Path, SimpleNamespace]:
    base = _base_175(tmp_path)
    request = build_reciprocal_route_runtime_request(
        base_build=base,
        repo_root=ROOT,
    )
    build_dir = tmp_path / request.build_id
    build_dir.mkdir()
    artifact_path = build_dir / RECIPROCAL_ROUTE_ARTIFACT_NAME
    artifact_path.write_bytes(b"verified-exact-218-blend")
    report = ReciprocalRouteBuildReport.model_validate(
        {
            "build_id": request.build_id,
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
            "object_registry": request.object_registry,
            "material_bindings": request.material_bindings,
            "counts": {
                "module_mesh_objects": 43,
                "textured_module_meshes": 43,
                "valid_uv_module_meshes": 43,
                "valid_surface_color_module_meshes": 43,
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
                "name": RECIPROCAL_ROUTE_ARTIFACT_NAME,
                "kind": "blender-scene",
                "sha256": _sha256_file(artifact_path),
                "size_bytes": artifact_path.stat().st_size,
            },
        }
    )
    (build_dir / RECIPROCAL_ROUTE_REQUEST_NAME).write_bytes(
        canonical_reciprocal_route_runtime_request_bytes(request)
    )
    (build_dir / RECIPROCAL_ROUTE_REPORT_NAME).write_bytes(
        canary._canonical_json_bytes(report.model_dump(mode="json"))
    )
    return build_dir, base


def _batch24_manifest() -> dict[str, Any]:
    assets = []
    for sector, sources in closure_module._BATCH24_SOURCES.items():
        for kind, (file_name, sha256) in sources.items():
            assets.append(
                {
                    "file": file_name,
                    "kind": kind,
                    "sector": sector,
                    "sha256": sha256,
                }
            )
    return {
        "schema_version": 1,
        "batch_id": closure_module.BATCH24_BATCH_ID,
        "asset_count": 16,
        "prompt_count": 16,
        "trust": {
            "synthetic": True,
            "stage": "design-only",
            "camera_calibration": "unknown",
            "geometry_consistency": "not-verified",
            "metric_scale": "unknown",
            "real_photo_texture": False,
            "training_use": "forbidden-as-multiview",
            "coverage_use": "forbidden",
            "trust_effect": "none",
        },
        "assets": sorted(assets, key=lambda row: row["file"]),
    }


@pytest.fixture
def prepared(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, PerimeterClosurePlan]:
    base_build_dir, base = _write_exact218_build(tmp_path)
    manifest_path = tmp_path / "batch24-manifest.json"
    manifest_path.write_bytes(canary._canonical_json_bytes(_batch24_manifest()))
    plan = build_default_perimeter_closure_plan(
        batch24_manifest=_batch24_manifest(),
        batch24_manifest_sha256=_sha256_file(manifest_path),
        production_plan_sha256="c" * 64,
        topology_plan_sha256="d" * 64,
        terrain_height_at=lambda x, y: round(0.015 * x - 0.01 * y, 3),
    )
    repo_root = tmp_path / "repo"
    runtime_script = repo_root / "scripts/blender/apply_perimeter_closure_modules.py"
    runtime_script.parent.mkdir(parents=True)
    runtime_script.write_bytes(b"# measured runtime placeholder for contract tests\n")
    return base_build_dir, manifest_path, base.executable, repo_root, plan


def _request(prepared):
    base_dir, manifest_path, executable, repo_root, plan = prepared
    return build_perimeter_closure_runtime_request(
        base_build_directory=base_dir,
        plan=plan,
        batch24_manifest_path=manifest_path,
        blender_executable=executable,
        repo_root=repo_root,
    )


def _report_for(request, output_path: Path) -> PerimeterClosureBuildReport:
    measurements = tuple(
        PerimeterClosureSectorMeasurement(
            module_id=module.module_id,
            terrain_support_contact_gap_m=0.0,
            corridor_endpoint_gap_m=0.0,
            drainage_endpoint_gap_m=0.0,
            previous_seam_gap_m=0.0,
            next_seam_gap_m=0.0,
        )
        for module in request.perimeter_closure_plan.modules
    )
    return PerimeterClosureBuildReport(
        build_id=request.build_id,
        base_build_id=request.base_build_id,
        base_build_request_sha256=request.base_build_request_sha256,
        base_build_report_sha256=request.base_build_report_sha256,
        base_blend_sha256=request.base_blend_sha256,
        base_object_registry_sha256=request.base_object_registry_sha256,
        base_reciprocal_route_module_plan_sha256=(
            request.base_reciprocal_route_module_plan_sha256
        ),
        blender_executable_sha256=request.blender_executable_sha256,
        runtime_script_sha256=request.runtime_script_sha256,
        batch24_manifest_sha256=request.batch24_manifest_sha256,
        perimeter_closure_plan_sha256=request.perimeter_closure_plan_sha256,
        material_bindings_sha256=request.material_bindings_sha256,
        object_registry=request.object_registry,
        material_bindings=request.material_bindings,
        counts=PerimeterClosureBuildCounts(
            overlay_mesh_objects=48,
            textured_overlay_meshes=48,
            valid_uv_overlay_meshes=48,
            valid_surface_color_overlay_meshes=48,
        ),
        validation=PerimeterClosureBuildValidation(
            base_registry_preserved=True,
            overlay_registry_exact=True,
            finite_nonempty_overlay_meshes=True,
            material_bindings_exact=True,
            design_sources_provenance_only=True,
            terrain_support_contacts_passed=True,
            corridor_continuity_passed=True,
            drainage_continuity_passed=True,
            sector_seams_passed=True,
        ),
        sector_measurements=measurements,
        artifact=PerimeterClosureArtifact(
            name=PERIMETER_CLOSURE_ARTIFACT_NAME,
            kind="blender-scene",
            sha256=_sha256_file(output_path),
            size_bytes=output_path.stat().st_size,
        ),
    )


def test_request_binds_exact218_inputs_and_exact266_registry(prepared) -> None:
    request = _request(prepared)
    assert request.base_canonical_roots == 218
    assert request.overlay_canonical_roots == 48
    assert request.canonical_roots == 266
    assert request.object_registry[0].instance_id == 1
    assert request.object_registry[-1].instance_id == 266
    assert request.perimeter_closure_plan_sha256 == (
        perimeter_closure_plan_sha256(request.perimeter_closure_plan)
    )
    assert request.base_build_id == prepared[0].name
    assert request.batch24_manifest_sha256 == _sha256_file(prepared[1])
    assert request.blender_executable_sha256 == _sha256_file(prepared[2])


def test_request_bytes_and_build_id_are_deterministic(prepared) -> None:
    left = _request(prepared)
    right = _request(prepared)
    assert left == right
    assert canonical_perimeter_closure_runtime_request_bytes(left) == (
        canonical_perimeter_closure_runtime_request_bytes(right)
    )
    payload = left.model_dump(mode="json")
    payload.pop("build_id")
    assert left.build_id == hashlib.sha256(
        canary._canonical_json_bytes(payload)
    ).hexdigest()


def test_request_rejects_tampered_base_manifest_or_executable(prepared) -> None:
    base_dir, manifest_path, executable, repo_root, plan = prepared
    for path, payload in (
        (base_dir / RECIPROCAL_ROUTE_REQUEST_NAME, b"{}"),
        (manifest_path, b"{}"),
        (executable, b"different-blender"),
    ):
        original = path.read_bytes()
        path.write_bytes(payload)
        with pytest.raises(PerimeterClosureRuntimeError):
            build_perimeter_closure_runtime_request(
                base_build_directory=base_dir,
                plan=plan,
                batch24_manifest_path=manifest_path,
                blender_executable=executable,
                repo_root=repo_root,
            )
        path.write_bytes(original)


def test_report_verifier_recomputes_all_bound_identities_and_artifact(
    prepared,
    tmp_path: Path,
) -> None:
    request = _request(prepared)
    output_path = tmp_path / PERIMETER_CLOSURE_ARTIFACT_NAME
    output_path.write_bytes(b"verified-exact-266")
    report = _report_for(request, output_path)
    verify_perimeter_closure_build_report(
        report,
        request=request,
        output_path=output_path,
    )
    for field in (
        "base_build_request_sha256",
        "base_build_report_sha256",
        "base_blend_sha256",
        "base_object_registry_sha256",
        "runtime_script_sha256",
        "batch24_manifest_sha256",
        "perimeter_closure_plan_sha256",
        "material_bindings_sha256",
    ):
        with pytest.raises(PerimeterClosureRuntimeError):
            verify_perimeter_closure_build_report(
                report.model_copy(update={field: "0" * 64}),
                request=request,
                output_path=output_path,
            )
    output_path.write_bytes(b"tampered")
    with pytest.raises(PerimeterClosureRuntimeError):
        verify_perimeter_closure_build_report(
            report,
            request=request,
            output_path=output_path,
        )


def test_report_model_rejects_counts_registry_or_gate_mutation(
    prepared,
    tmp_path: Path,
) -> None:
    request = _request(prepared)
    output_path = tmp_path / PERIMETER_CLOSURE_ARTIFACT_NAME
    output_path.write_bytes(b"verified-exact-266")
    payload = _report_for(request, output_path).model_dump(mode="json")

    bad_count = copy.deepcopy(payload)
    bad_count["counts"]["canonical_roots"] = 265
    with pytest.raises(ValidationError):
        PerimeterClosureBuildReport.model_validate(bad_count)

    bad_registry = copy.deepcopy(payload)
    bad_registry["object_registry"][-1]["instance_id"] = 265
    with pytest.raises(ValidationError):
        PerimeterClosureBuildReport.model_validate(bad_registry)

    bad_gate = copy.deepcopy(payload)
    bad_gate["validation"]["sector_seams_passed"] = False
    with pytest.raises(ValidationError):
        PerimeterClosureBuildReport.model_validate(bad_gate)


def test_report_loader_rejects_duplicate_or_noncanonical_json(
    prepared,
    tmp_path: Path,
) -> None:
    request = _request(prepared)
    output_path = tmp_path / PERIMETER_CLOSURE_ARTIFACT_NAME
    output_path.write_bytes(b"verified-exact-266")
    payload = _report_for(request, output_path).model_dump(mode="json")
    canonical = canary._canonical_json_bytes(payload)
    report_path = tmp_path / "report.json"
    report_path.write_bytes(canonical)
    assert load_perimeter_closure_build_report(report_path).build_id == request.build_id

    text = canonical.decode("utf-8").replace(
        '"build_id":',
        '"build_id": "duplicated", "build_id":',
        1,
    )
    report_path.write_text(text, encoding="utf-8")
    with pytest.raises(PerimeterClosureRuntimeError):
        load_perimeter_closure_build_report(report_path)

    report_path.write_bytes(canonical.rstrip(b"\n"))
    with pytest.raises(PerimeterClosureRuntimeError):
        load_perimeter_closure_build_report(report_path)


def test_runner_publishes_exact_three_files_and_reuses_verified_build(
    prepared,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(prepared)
    base_dir, _manifest, executable, repo_root, _plan = prepared
    calls = 0

    def fake_run(argv, **_kwargs):
        nonlocal calls
        calls += 1
        request_path = Path(argv[-2])
        output_dir = Path(argv[-1])
        assert request_path.read_bytes() == (
            canonical_perimeter_closure_runtime_request_bytes(request)
        )
        artifact_path = output_dir / PERIMETER_CLOSURE_ARTIFACT_NAME
        artifact_path.write_bytes(b"verified-exact-266")
        report = _report_for(request, artifact_path)
        (output_dir / "perimeter-closure-build-report.json").write_bytes(
            canary._canonical_json_bytes(report.model_dump(mode="json"))
        )
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(
        "pipeline.synthetic_village.perimeter_closure_runtime.subprocess.run",
        fake_run,
    )
    build_root = repo_root / ".nantai-studio/work/perimeter"
    result = run_perimeter_closure_build(
        request,
        base_build_directory=base_dir,
        blender_executable=executable,
        repo_root=repo_root,
        build_root=build_root,
    )
    assert result.final_directory.name == request.build_id
    assert {path.name for path in result.final_directory.iterdir()} == set(
        PERIMETER_CLOSURE_BUILD_ENTRIES
    )
    reused = run_perimeter_closure_build(
        request,
        base_build_directory=base_dir,
        blender_executable=executable,
        repo_root=repo_root,
        build_root=build_root,
    )
    assert reused.report == result.report
    assert calls == 1
