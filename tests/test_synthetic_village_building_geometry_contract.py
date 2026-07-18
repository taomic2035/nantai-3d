from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from pipeline.synthetic_village.building_geometry import (
    BUILDING_ELEVATIONS,
    BUILDING_GEOMETRY_V1,
    BUILDING_GEOMETRY_V2,
    BUILDING_VARIANTS,
    building_variant,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/blender/build_synthetic_village.py"


def _source_and_tree() -> tuple[str, ast.Module]:
    source = BUILDER.read_text("utf-8")
    return source, ast.parse(source)


def _literal_assignment(tree: ast.Module, name: str) -> object:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"standalone builder is missing {name}")


def _standalone_variant_function(tree: ast.Module):
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_building_variant"
        ),
        None,
    )
    assert function is not None, "standalone builder is missing _building_variant"
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)

    class RuntimeBuildError(RuntimeError):
        pass

    namespace = {
        "BUILDING_GEOMETRY_V1": BUILDING_GEOMETRY_V1,
        "BUILDING_GEOMETRY_V2": BUILDING_GEOMETRY_V2,
        "BUILDING_VARIANTS": BUILDING_VARIANTS,
        "RuntimeBuildError": RuntimeBuildError,
        "hashlib": hashlib,
    }
    exec(compile(module, str(BUILDER), "exec"), namespace)
    return namespace["_building_variant"]


def test_standalone_builder_geometry_identity_matches_host_contract() -> None:
    source, tree = _source_and_tree()

    assert _literal_assignment(tree, "BUILDING_GEOMETRY_V1") == BUILDING_GEOMETRY_V1
    assert _literal_assignment(tree, "BUILDING_GEOMETRY_V2") == BUILDING_GEOMETRY_V2
    assert _literal_assignment(tree, "BUILDING_ELEVATIONS") == BUILDING_ELEVATIONS
    assert _literal_assignment(tree, "BUILDING_VARIANTS") == BUILDING_VARIANTS
    assert _literal_assignment(tree, "MAX_ADDED_BUILDING_FACES") == 220
    assert _literal_assignment(tree, "MAX_ADDED_VILLAGE_FACES") == 15_400
    assert _literal_assignment(tree, "MAX_BUILDING_GLTF_TRIANGLES") == 720
    assert _literal_assignment(tree, "MAX_GLTF_TRIANGLES") == 100_000
    assert _literal_assignment(tree, "MAX_TEXTURED_GLB_BYTES") == 150_000_000
    assert re.search(r"(?<!_)hash\s*\(", source) is None

    standalone = _standalone_variant_function(tree)
    building_ids = (
        row.object_id
        for row in build_scene_plan().objects
        if row.semantic_class == "building"
    )
    for object_id in building_ids:
        assert standalone(object_id, BUILDING_GEOMETRY_V2) == building_variant(
            object_id,
            BUILDING_GEOMETRY_V2,
        )
    assert standalone("building-central-001", BUILDING_GEOMETRY_V1) is None


def test_standalone_builder_declares_four_side_geometry_and_evidence_paths() -> None:
    source, _tree = _source_and_tree()

    for required in (
        "building_geometry_profile_id",
        "_facade_box",
        "_facade_quad",
        "_add_window_assembly",
        "_add_door_assembly",
        "nv_building_geometry_profile",
        "nv_building_variant",
        "nv_facade_elevations",
        "nv_added_face_count",
        "nv_building_geometry_evidence",
    ):
        assert required in source
