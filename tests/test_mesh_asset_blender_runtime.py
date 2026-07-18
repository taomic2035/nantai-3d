"""Real Blender gate for the eleven replaceable textured mesh templates."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.synthetic_village.mesh_asset_build import run_mesh_asset_build
from pipeline.synthetic_village.mesh_asset_bundle import load_mesh_asset_bundle
from tests.synthetic_material_fixtures import publish_material_fixture

ROOT = Path(__file__).resolve().parents[1]
BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
BUILDER = ROOT / "scripts/blender/build_mesh_asset_bundle.py"


def test_builder_source_uses_proven_textured_canary_primitives() -> None:
    source = BUILDER.read_text(encoding="utf-8")

    for token in (
        "_create_textured_materials",
        "_apply_textured_uvs_and_tangents",
        "_build_building",
        "MeshAssembler",
        "_link_mesh",
        "export_tangents=True",
        "export_yup=True",
        "nv_asset_id",
        "nv_lod",
        "build-report.json",
    ):
        assert token in source


@pytest.mark.skipif(not BLENDER.is_file(), reason="local Mac Blender is absent")
def test_real_blender_builds_exact_33_verified_templates(tmp_path: Path) -> None:
    _visual_root, material_bundle = publish_material_fixture(
        tmp_path / "materials",
    )

    result = run_mesh_asset_build(
        repo_root=ROOT,
        material_bundle_root=material_bundle.final_directory,
        blender_executable=BLENDER,
        builder_script=BUILDER,
        work_root=tmp_path / "work",
        publication_root=tmp_path / "published",
        timeout_seconds=30 * 60,
    )

    bundle = load_mesh_asset_bundle(result.bundle.final_directory)
    assert len(result.report.artifacts) == 33
    assert len(bundle.records) == 11
    assert result.bundle.reused is False
    for record in bundle.records:
        triangles = tuple(
            record.lod[str(level)].triangle_count
            for level in (0, 1, 2)
        )
        assert triangles[0] < triangles[1] < triangles[2]
