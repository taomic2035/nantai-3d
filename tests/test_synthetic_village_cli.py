"""Operator-facing synthetic-village CLI contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.synthetic_village.environment_module_runtime import (
    EnvironmentModuleArtifact,
    EnvironmentModuleBuildCounts,
)
from pipeline.synthetic_village.material_bundle import MaterialBundleResult
from pipeline.synthetic_village.mesh_asset_bundle import MeshAssetBundleResult
from scripts import synthetic_village as synthetic_village_cli


def test_import_h3_material_sources_prints_bounded_truth_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []
    final_root = tmp_path / "published" / ("a" * 64)
    manifest = SimpleNamespace(
        schema_version="nantai.h3-ai-material-source-pack.v1",
        source_pack_id="a" * 64,
        synthetic=True,
        ai_generated=True,
        real_photo_textures=False,
        records=tuple(range(8)),
    )

    def prepare_h3_source_pack(selection_receipt: Path, output_root: Path):
        calls.append((selection_receipt, output_root))
        return SimpleNamespace(root=final_root, manifest=manifest)

    monkeypatch.setattr(
        synthetic_village_cli,
        "_prepare_h3_source_pack",
        lambda: prepare_h3_source_pack,
    )

    assert synthetic_village_cli.main(
        [
            "import-h3-material-sources",
            "--selection-receipt",
            str(tmp_path / "selection.json"),
            "--output-root",
            str(tmp_path / "published"),
        ],
    ) == 0

    assert calls == [
        (tmp_path / "selection.json", tmp_path / "published"),
    ]
    assert json.loads(capsys.readouterr().out) == {
        "ai_generated": True,
        "output_root": str(final_root),
        "real_photo_textures": False,
        "record_count": 8,
        "schema_version": "nantai.h3-ai-material-source-pack.v1",
        "source_pack_id": "a" * 64,
        "synthetic": True,
    }


def test_author_h3_materials_prints_bounded_truth_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []
    final_root = tmp_path / "authored" / ("b" * 64)
    manifest = SimpleNamespace(
        schema_version="nantai.h3-authored-material-pack.v1",
        pack_id="b" * 64,
        source_pack_id="a" * 64,
        synthetic=True,
        ai_generated=True,
        real_photo_textures=False,
        records=tuple(range(8)),
    )

    def build_h3_authored_material_pack(
        source_pack_root: Path,
        output_root: Path,
    ):
        calls.append((source_pack_root, output_root))
        return SimpleNamespace(root=final_root, manifest=manifest)

    monkeypatch.setattr(
        synthetic_village_cli,
        "_build_h3_authored_material_pack",
        lambda: build_h3_authored_material_pack,
    )

    assert synthetic_village_cli.main(
        [
            "author-h3-materials",
            "--source-pack-root",
            str(tmp_path / "sources"),
            "--output-root",
            str(tmp_path / "authored"),
        ],
    ) == 0

    assert calls == [
        (tmp_path / "sources", tmp_path / "authored"),
    ]
    assert json.loads(capsys.readouterr().out) == {
        "ai_generated": True,
        "output_root": str(final_root),
        "pack_id": "b" * 64,
        "real_photo_textures": False,
        "record_count": 8,
        "schema_version": "nantai.h3-authored-material-pack.v1",
        "source_pack_id": "a" * 64,
        "synthetic": True,
    }


def test_build_h3_ktx2_prints_bounded_truth_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []
    final_root = tmp_path / "ktx2" / ("c" * 64)
    manifest = SimpleNamespace(
        schema_version="nantai.h3-ktx2-pack.v1",
        pack_id="c" * 64,
        source_pack_id="a" * 64,
        authored_pack_id="b" * 64,
        synthetic=True,
        ai_generated=True,
        real_photo_textures=False,
        tool_version="4.4.2",
        records=tuple(range(8)),
    )

    def compile_pack(authored_root, output_root, *, receipt_path):
        calls.append((authored_root, output_root, receipt_path))
        return SimpleNamespace(root=final_root, manifest=manifest)

    monkeypatch.setattr(
        synthetic_village_cli,
        "_compile_h3_ktx2_pack",
        lambda: compile_pack,
    )
    assert synthetic_village_cli.main(
        [
            "build-h3-ktx2",
            "--authored-root",
            str(tmp_path / "authored"),
            "--tool-receipt",
            str(tmp_path / "receipt.json"),
            "--output-root",
            str(tmp_path / "ktx2"),
        ],
    ) == 0
    assert calls == [(
        tmp_path / "authored",
        tmp_path / "ktx2",
        tmp_path / "receipt.json",
    )]
    assert json.loads(capsys.readouterr().out) == {
        "ai_generated": True,
        "authored_pack_id": "b" * 64,
        "output_root": str(final_root),
        "pack_id": "c" * 64,
        "real_photo_textures": False,
        "record_count": 8,
        "schema_version": "nantai.h3-ktx2-pack.v1",
        "source_pack_id": "a" * 64,
        "synthetic": True,
        "texture_count": 24,
        "tool_version": "4.4.2",
    }


def test_build_materials_prints_one_stable_json_object(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    final_directory = tmp_path / "published" / ("a" * 64)
    calls = []

    def publish_material_bundle(**kwargs):
        calls.append(kwargs)
        return MaterialBundleResult(
            bundle_id="a" * 64,
            final_directory=final_directory,
            record_count=24,
            reused=False,
        )

    monkeypatch.setattr(
        synthetic_village_cli,
        "_publish_material_bundle",
        lambda: publish_material_bundle,
    )

    assert synthetic_village_cli.main(
        [
            "build-materials",
            "--visual-pack-root",
            str(tmp_path / "visual"),
            "--publication-root",
            str(tmp_path / "published"),
            "--work-root",
            str(tmp_path / "work"),
        ],
    ) == 0

    assert calls == [
        {
            "visual_pack_root": tmp_path / "visual",
            "publication_root": tmp_path / "published",
            "work_root": tmp_path / "work",
        },
    ]
    assert json.loads(capsys.readouterr().out) == {
        "bundle_id": "a" * 64,
        "final_directory": str(final_directory),
        "record_count": 24,
        "reused": False,
    }


def test_build_near_mesh_assets_prints_honest_stable_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    final_directory = tmp_path / "published" / ("d" * 64)
    calls = []

    def run_mesh_asset_build_v2(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            request=SimpleNamespace(
                build_id="c" * 64,
                synthetic=True,
                verification_level="L0",
                reused_lods=tuple(range(22)),
            ),
            report=SimpleNamespace(artifacts=tuple(range(11))),
            bundle=MeshAssetBundleResult(
                bundle_id="d" * 64,
                final_directory=final_directory,
                record_count=11,
                reused=False,
            ),
        )

    monkeypatch.setattr(
        synthetic_village_cli,
        "_run_near_mesh_asset_build",
        lambda: run_mesh_asset_build_v2,
    )

    assert synthetic_village_cli.main(
        [
            "build-near-mesh-assets",
            "--source-v1-bundle-root",
            str(tmp_path / "v1"),
            "--material-bundle-root",
            str(tmp_path / "materials"),
            "--blender",
            str(tmp_path / "Blender"),
            "--work-root",
            str(tmp_path / "work"),
            "--publication-root",
            str(tmp_path / "published"),
            "--timeout-seconds",
            "123",
        ],
    ) == 0

    assert calls == [
        {
            "repo_root": synthetic_village_cli.ROOT,
            "source_v1_bundle_root": tmp_path / "v1",
            "material_bundle_root": tmp_path / "materials",
            "blender_executable": tmp_path / "Blender",
            "builder_script": (
                synthetic_village_cli.ROOT
                / "scripts/blender/build_mesh_asset_bundle_v2.py"
            ),
            "work_root": tmp_path / "work",
            "publication_root": tmp_path / "published",
            "timeout_seconds": 123,
        },
    ]
    assert json.loads(capsys.readouterr().out) == {
        "build_id": "c" * 64,
        "bundle_id": "d" * 64,
        "bundle_root": str(final_directory),
        "lod2_asset_count": 11,
        "reused_lod_count": 22,
        "synthetic": True,
        "verification_level": "L0",
    }


def test_build_environment_modules_prints_bounded_truth_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """The build-environment-modules CLI surfaces the fail-closed module
    build contract without re-deriving any evidence at the UI layer."""

    verify_calls = []
    run_calls = []
    verified_build = SimpleNamespace(directory=tmp_path / "verified-v2")
    counts = EnvironmentModuleBuildCounts(module_mesh_objects=47)
    artifact = EnvironmentModuleArtifact(
        name="village-modules.blend",
        kind="blender-scene",
        sha256="e" * 64,
        size_bytes=4096,
    )
    report = SimpleNamespace(counts=counts, artifact=artifact)
    request = SimpleNamespace(
        build_id="f" * 64,
        base_build_id="a" * 64,
        environment_module_plan_sha256="b" * 64,
    )
    result = SimpleNamespace(
        final_directory=tmp_path / "modules" / ("f" * 64),
        request=request,
        report=report,
    )

    def verify_windows_production_build(
        *,
        directory: Path,
        material_bundle_root: Path,
        repo_root: Path,
        visual_pack_root: Path | None,
        surface_realism_profile_id: str,
    ):
        verify_calls.append(
            {
                "directory": directory,
                "material_bundle_root": material_bundle_root,
                "repo_root": repo_root,
                "visual_pack_root": visual_pack_root,
                "surface_realism_profile_id": surface_realism_profile_id,
            },
        )
        return verified_build

    def run_environment_module_build(
        *,
        base_build: object,
        repo_root: Path,
        build_root: Path | None,
        timeout_seconds: int,
    ):
        run_calls.append(
            {
                "base_build": base_build,
                "repo_root": repo_root,
                "build_root": build_root,
                "timeout_seconds": timeout_seconds,
            },
        )
        return result

    monkeypatch.setattr(
        synthetic_village_cli,
        "_verify_windows_production_build",
        lambda: verify_windows_production_build,
    )
    monkeypatch.setattr(
        synthetic_village_cli,
        "_run_environment_module_build",
        lambda: run_environment_module_build,
    )

    assert synthetic_village_cli.main(
        [
            "build-environment-modules",
            "--verified-v2-build",
            str(tmp_path / "verified-v2"),
            "--material-bundle-root",
            str(tmp_path / "materials"),
            "--surface-realism-profile",
            "source-consistent-multiscale-surface-v1",
            "--visual-pack-root",
            str(tmp_path / "visual"),
            "--build-root",
            str(tmp_path / "modules"),
            "--timeout-seconds",
            "600",
        ],
    ) == 0

    assert verify_calls == [
        {
            "directory": tmp_path / "verified-v2",
            "material_bundle_root": tmp_path / "materials",
            "repo_root": synthetic_village_cli.ROOT,
            "visual_pack_root": tmp_path / "visual",
            "surface_realism_profile_id": "source-consistent-multiscale-surface-v1",
        },
    ]
    assert run_calls == [
        {
            "base_build": verified_build,
            "repo_root": synthetic_village_cli.ROOT,
            "build_root": tmp_path / "modules",
            "timeout_seconds": 600,
        },
    ]
    assert json.loads(capsys.readouterr().out) == {
        "build_adapter": "environment-module-runtime-v1",
        "build_id": "f" * 64,
        "final_directory": str(tmp_path / "modules" / ("f" * 64)),
        "base_build_id": "a" * 64,
        "environment_module_plan_sha256": "b" * 64,
        "artifact_name": "village-modules.blend",
        "artifact_sha256": "e" * 64,
        "artifact_size_bytes": 4096,
        "counts": {
            "base_canonical_roots": 130,
            "module_canonical_roots": 45,
            "canonical_roots": 175,
            "module_mesh_objects": 47,
        },
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none",
    }


def test_build_environment_modules_requires_explicit_operator_inputs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """build-environment-modules fails closed when required flags are missing."""

    def _no_call(*args, **kwargs):
        raise AssertionError("verify_windows_production_build must not be called")

    def _no_run(*args, **kwargs):
        raise AssertionError("run_environment_module_build must not be called")

    monkeypatch.setattr(
        synthetic_village_cli,
        "_verify_windows_production_build",
        lambda: _no_call,
    )
    monkeypatch.setattr(
        synthetic_village_cli,
        "_run_environment_module_build",
        lambda: _no_run,
    )

    with pytest.raises(SystemExit):
        synthetic_village_cli.main(
            [
                "build-environment-modules",
                "--verified-v2-build",
                str(tmp_path / "verified-v2"),
                # missing --material-bundle-root and --surface-realism-profile
            ],
        )


def test_build_environment_modules_uses_runtime_default_build_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    verified_build = SimpleNamespace(directory=tmp_path / "verified-v2")
    result = SimpleNamespace(
        final_directory=tmp_path / "default-modules" / ("f" * 64),
        request=SimpleNamespace(
            build_id="f" * 64,
            base_build_id="a" * 64,
            environment_module_plan_sha256="b" * 64,
        ),
        report=SimpleNamespace(
            counts=EnvironmentModuleBuildCounts(module_mesh_objects=47),
            artifact=EnvironmentModuleArtifact(
                name="village-modules.blend",
                kind="blender-scene",
                sha256="e" * 64,
                size_bytes=4096,
            ),
        ),
    )

    monkeypatch.setattr(
        synthetic_village_cli,
        "_verify_windows_production_build",
        lambda: lambda **_kwargs: verified_build,
    )

    def run_environment_module_build(
        *,
        base_build: object,
        repo_root: Path,
        timeout_seconds: int,
    ):
        assert base_build is verified_build
        assert repo_root == synthetic_village_cli.ROOT
        assert timeout_seconds == 1200
        return result

    monkeypatch.setattr(
        synthetic_village_cli,
        "_run_environment_module_build",
        lambda: run_environment_module_build,
    )

    assert synthetic_village_cli.main(
        [
            "build-environment-modules",
            "--verified-v2-build",
            str(tmp_path / "verified-v2"),
            "--material-bundle-root",
            str(tmp_path / "materials"),
            "--surface-realism-profile",
            "source-consistent-multiscale-surface-v1",
        ],
    ) == 0


def test_render_reciprocal_production_runs_resumable_six_role_batch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    runtime_request = object()
    verified_build = object()
    source_plan = object()
    policy = object()
    run_calls = []
    build_root = tmp_path / "reciprocal-build"
    output_root = (
        tmp_path
        / ".nantai-studio/sv-prod-win/reciprocal-production-batches/batch-001"
    )

    monkeypatch.setattr(
        synthetic_village_cli,
        "_load_reciprocal_route_runtime_request",
        lambda: lambda path: (
            runtime_request
            if path == build_root / "reciprocal-route-build-request.json"
            else None
        ),
    )

    expected_runtime_request = runtime_request

    def verify_build(*, report_path, runtime_request: object):
        assert report_path == build_root / "reciprocal-route-build-report.json"
        assert runtime_request is expected_runtime_request
        return verified_build

    monkeypatch.setattr(
        synthetic_village_cli,
        "_verify_reciprocal_production_build",
        lambda: verify_build,
    )
    monkeypatch.setattr(
        synthetic_village_cli,
        "_import_production_profile",
        lambda: (lambda: source_plan, None, None, None),
    )
    monkeypatch.setattr(
        synthetic_village_cli,
        "_load_post_render_policy",
        lambda path: policy,
    )

    def run_batch(**kwargs):
        run_calls.append(kwargs)
        return SimpleNamespace(
            batch_id="a" * 64,
            batch_root=output_root,
            journal_path=output_root / "batch-journal.json",
            accepted_count=5,
            failed_count=1,
            reused_count=0,
        )

    monkeypatch.setattr(
        synthetic_village_cli,
        "_run_reciprocal_production_batch",
        lambda: run_batch,
    )
    roles = (
        "central-courtyard-downhill",
        "bridge-deck-crossing",
        "watermill-tailrace",
        "covered-gallery-underpass",
        "forest-orchard-boundary",
        "lower-valley-uphill",
    )
    arguments = [
        "render-reciprocal-production",
        "--reciprocal-build",
        str(build_root),
        "--blender",
        str(tmp_path / "blender.exe"),
        "--post-render-policy",
        str(tmp_path / "policy.json"),
        "--min-valid-pixel-ratio",
        "0.05",
        "--clearance-near-distance-m",
        "2.0",
        "--min-upper-middle-near-hits",
        "5",
        "--output-root",
        str(output_root),
        "--timeout-seconds",
        "900",
    ]
    for index, role in enumerate(roles):
        camera_id = (
            "camera-ground-route-010"
            if index % 2 == 0
            else "camera-ground-route-039"
        )
        arguments.extend(("--target", f"{role}={camera_id}"))

    assert synthetic_village_cli.main(arguments) == 0

    assert len(run_calls) == 1
    call = run_calls[0]
    assert call["verified_build"] is verified_build
    assert call["source_plan"] is source_plan
    assert call["blender_executable"] == tmp_path / "blender.exe"
    assert call["output_root"] == output_root
    assert call["clearance_policy"].near_distance_m == 2.0
    assert call["clearance_policy"].minimum_upper_middle_near_hit_count == 5
    assert call["quality_policy"].minimum_valid_pixel_ratio == 0.05
    assert call["post_render_policy"] is policy
    assert call["timeout_seconds"] == 900
    assert tuple(row.role_module_id for row in call["targets"]) == roles
    assert json.loads(capsys.readouterr().out) == {
        "accepted_count": 5,
        "batch_id": "a" * 64,
        "batch_root": str(output_root),
        "failed_count": 1,
        "journal_path": str(output_root / "batch-journal.json"),
        "reused_count": 0,
        "synthetic": True,
        "trust_effect": "none-quality-filter-only",
        "verification_level": "L0",
    }
