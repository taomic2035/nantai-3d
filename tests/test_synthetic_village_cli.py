"""Operator-facing synthetic-village CLI contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
