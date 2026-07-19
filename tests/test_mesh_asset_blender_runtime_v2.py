"""Real Blender and source gates for high-detail near-mesh v2 assets."""

from __future__ import annotations

import ast
import os
import struct
from pathlib import Path

import pytest

from pipeline.synthetic_village.glb_material_audit import _load_glb_bytes
from pipeline.synthetic_village.mesh_asset_build import EXPECTED_ASSET_IDS
from pipeline.synthetic_village.mesh_asset_build_v2 import (
    run_mesh_asset_build_v2,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    MeshAssetBundle,
    load_mesh_asset_bundle,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    LOD2_TRIANGLE_BANDS,
    MeshAssetBundleV2,
)

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/blender/build_mesh_asset_bundle_v2.py"
BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
PRIVATE_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3"
)
SOURCE_V1 = (
    PRIVATE_ROOT
    / "mesh-asset-bundles"
    / "2fbf8692ca8b1442c72177dc1954fb81959933bafd46623c1817002fc732c3e8"
)
MATERIALS = (
    PRIVATE_ROOT
    / "material-bundles"
    / "b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13"
)
VEGETATION_MATERIALS = {
    "tree_bamboo_01": (
        "material-bamboo-leaf-01",
        "material-bamboo-stem-01",
    ),
    "tree_broadleaf_01": (
        "material-broadleaf-canopy-01",
        "material-broadleaf-bark-01",
    ),
    "tree_pine_01": (
        "material-orchard-leaf-01",
        "material-orchard-bark-01",
    ),
}


def _read_vec2_accessor(
    document: dict[str, object],
    binary: bytes,
    accessor_index: int,
) -> tuple[tuple[float, float], ...]:
    accessors = document["accessors"]
    buffer_views = document["bufferViews"]
    accessor = accessors[accessor_index]
    assert accessor["componentType"] == 5126
    assert accessor["type"] == "VEC2"
    view = buffer_views[accessor["bufferView"]]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", 8)
    return tuple(
        struct.unpack_from("<2f", binary, offset + index * stride)
        for index in range(accessor["count"])
    )


def _assert_real_leaf_materials_and_atlas_cells(
    bundle: MeshAssetBundleV2,
    bundle_root: Path,
) -> None:
    for record in bundle.records:
        if record.kind != "vegetation":
            continue
        foliage_slot, structure_slot = VEGETATION_MATERIALS[
            record.asset_id
        ]
        path = bundle_root / record.lod["2"].glb_object_path
        _, document, binary = _load_glb_bytes(path.read_bytes())
        materials = document["materials"]
        slot_by_material = {
            index: material["extras"]["slot_id"]
            for index, material in enumerate(materials)
        }
        leaf_count = 0
        for mesh in document["meshes"]:
            name = mesh["name"]
            primitive = mesh["primitives"][0]
            slot_id = slot_by_material[primitive["material"]]
            if ":leaf-card:" not in name:
                assert slot_id == structure_slot
                continue
            leaf_count += 1
            assert slot_id == foliage_slot
            values = _read_vec2_accessor(
                document,
                binary,
                primitive["attributes"]["TEXCOORD_0"],
            )
            minimum = tuple(min(row[axis] for row in values) for axis in range(2))
            maximum = tuple(max(row[axis] for row in values) for axis in range(2))
            for lower, upper in zip(minimum, maximum, strict=True):
                assert 0.0 <= lower < upper <= 1.0
                assert int(lower * 4) == int(upper * 4)
        assert leaf_count == 3_000


def test_v2_builder_source_exposes_exact_near_runtime_contract() -> None:
    source = BUILDER.read_text(encoding="utf-8")

    for token in (
        "nantai.synthetic-village.mesh-asset-build.v2",
        "nantai.synthetic-village.mesh-asset-build-report.v2",
        "build_near_geometry_plan",
        "PRIMITIVE_BUILDERS",
        '"box"',
        '"bevelled-box"',
        '"cylinder"',
        '"roof-tile"',
        '"thatch-strip"',
        '"branch"',
        '"leaf-card"',
        '"stone-block"',
        '"frame"',
        "_bevelled_box_geometry",
        "_leaf_atlas_uv",
        "bevel_fraction",
        "GLTF_SEPARATE",
        "bpy.app.debug_value = 1",
        "bpy.app.debug_value = previous_debug_value",
        "_pack_external_texture_glb",
        'alphaMode"] = "MASK"',
        'alphaCutoff"] = 0.45',
        'doubleSided"] = True',
        "export_tangents=True",
        "export_extras=True",
        "--material-root",
        "--atlas-root",
        "--output-root",
        "--report",
    ):
        assert token in source

    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "random" not in imported
    assert "time" not in imported


def test_real_blender_builds_identical_complete_near_bundle_twice() -> None:
    if os.environ.get("NANTAI_RUN_REAL_BLENDER_V2") != "1":
        pytest.skip("set NANTAI_RUN_REAL_BLENDER_V2=1 for the real Blender gate")
    missing = [
        path
        for path in (BLENDER, SOURCE_V1, MATERIALS)
        if not path.exists()
    ]
    if missing:
        pytest.skip(f"real Blender gate inputs are absent: {missing}")

    source = load_mesh_asset_bundle(SOURCE_V1)
    assert type(source) is MeshAssetBundle

    arguments = {
        "repo_root": ROOT,
        "source_v1_bundle_root": SOURCE_V1,
        "material_bundle_root": MATERIALS,
        "blender_executable": BLENDER,
        "builder_script": BUILDER,
        "work_root": PRIVATE_ROOT / "mesh-near-v2-integration-work",
        "publication_root": PRIVATE_ROOT / "mesh-asset-bundles",
        "timeout_seconds": 3_600,
    }
    first = run_mesh_asset_build_v2(**arguments)
    first_manifest = (
        first.bundle.final_directory / "manifest.json"
    ).read_bytes()
    second = run_mesh_asset_build_v2(**arguments)
    second_manifest = (
        second.bundle.final_directory / "manifest.json"
    ).read_bytes()

    assert first.request.build_id == second.request.build_id
    assert first.bundle.bundle_id == second.bundle.bundle_id
    assert first_manifest == second_manifest
    assert second.bundle.reused is True
    assert first.stderr == second.stderr == ""

    bundle = load_mesh_asset_bundle(first.bundle.final_directory)
    assert type(bundle) is MeshAssetBundleV2
    assert bundle.asset_ids == EXPECTED_ASSET_IDS
    assert len(bundle.records) == 11
    _assert_real_leaf_materials_and_atlas_cells(
        bundle,
        first.bundle.final_directory,
    )
    source_records = {row.asset_id: row for row in source.records}
    for record in bundle.records:
        source_record = source_records[record.asset_id]
        for level in ("0", "1"):
            assert record.lod[level].glb_sha256 == (
                source_record.lod[level].glb_sha256
            )
        near = record.lod["2"]
        lower, upper = LOD2_TRIANGLE_BANDS[record.kind]
        assert lower <= near.triangle_count <= upper
        assert near.texture_storage == "shared-content-addressed"
        if record.kind == "vegetation":
            foliage = {
                row
                for row in near.texture_bindings
                if row.derivation_algorithm_id
                == "deterministic-foliage-cutout-v1"
            }
            assert {row.role for row in foliage} == {
                "base_color",
                "normal",
                "orm",
            }
    assert b"/Users/" not in first_manifest
