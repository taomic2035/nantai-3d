"""Build eleven replaceable textured mesh templates inside verified Blender.

This entrypoint intentionally reuses the textured canary's mesh, material, UV,
and tangent primitives.  It accepts only the path-free request emitted by
``mesh_asset_build.py`` and publishes a complete 11 x 3 artifact matrix through
one absent-only staging directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import struct
import sys
import uuid
from pathlib import Path

import bpy


class MeshTemplateRuntimeError(RuntimeError):
    """The requested Blender build cannot produce trustworthy artifacts."""


REQUEST_SCHEMA = "nantai.synthetic-village.mesh-asset-build.v1"
REPORT_SCHEMA = "nantai.synthetic-village.mesh-asset-build-report.v1"
COORDINATE_ENCODING = "three-east-up-negative-north"
EXPECTED_ASSET_IDS = (
    "fence_wood_01",
    "house_barn_01",
    "house_stone_01",
    "house_thatch_01",
    "house_wood_01",
    "house_wood_02",
    "stone_lamp_01",
    "stone_wall_01",
    "tree_bamboo_01",
    "tree_broadleaf_01",
    "tree_pine_01",
)
EXPECTED_RECIPE_IDS = {
    "fence_wood_01": "weathered-timber-fence-v1",
    "house_barn_01": "dark-timber-barn-v1",
    "house_stone_01": "fieldstone-house-v1",
    "house_thatch_01": "rammed-earth-thatch-house-v1",
    "house_wood_01": "weathered-timber-house-v1",
    "house_wood_02": "plaster-timber-house-v1",
    "stone_lamp_01": "stone-metal-lamp-v1",
    "stone_wall_01": "dry-stone-wall-v1",
    "tree_bamboo_01": "clustered-bamboo-v1",
    "tree_broadleaf_01": "humid-broadleaf-v1",
    "tree_pine_01": "layered-pine-v1",
}
EXPECTED_BUDGETS = {
    "building": [100, 300, 720],
    "vegetation": [160, 500, 1200],
    "prop": [80, 240, 600],
}
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_GLB_BYTES = 128 * 1024 * 1024


def _load_shared_builder():
    path = Path(__file__).with_name("build_synthetic_village.py")
    spec = importlib.util.spec_from_file_location(
        "nantai_shared_synthetic_village_builder",
        path,
    )
    if spec is None or spec.loader is None:
        raise MeshTemplateRuntimeError("shared Blender builder cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared_builder()
MeshAssembler = shared.MeshAssembler
_create_textured_materials = shared._create_textured_materials
_apply_textured_uvs_and_tangents = shared._apply_textured_uvs_and_tangents
_build_building = shared._build_building
_link_mesh = shared._link_mesh


def _reject_duplicate_keys(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise MeshTemplateRuntimeError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_nonfinite(value):
    raise MeshTemplateRuntimeError(f"JSON contains non-finite number: {value}")


def _canonical_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _expect_exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise MeshTemplateRuntimeError(f"{label} has unknown or missing fields")


def _read_request(path):
    try:
        before = path.stat()
        if (
            not path.is_absolute()
            or not path.is_file()
            or path.is_symlink()
            or before.st_size <= 0
            or before.st_size > MAX_REQUEST_BYTES
        ):
            raise MeshTemplateRuntimeError("mesh build request is not a bounded file")
        raw = path.read_bytes()
        after = path.stat()
    except MeshTemplateRuntimeError:
        raise
    except OSError as exc:
        raise MeshTemplateRuntimeError("mesh build request cannot be read") from exc
    def signature(value):
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
    if (
        signature(before) != signature(after)
        or len(raw) != before.st_size
        or len(raw) > MAX_REQUEST_BYTES
    ):
        raise MeshTemplateRuntimeError("mesh build request changed during read")
    try:
        request = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except MeshTemplateRuntimeError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MeshTemplateRuntimeError("mesh build request is invalid JSON") from exc
    if raw != _canonical_bytes(request):
        raise MeshTemplateRuntimeError("mesh build request is not canonical")
    return request


def _validate_request(request):
    _expect_exact_keys(
        request,
        {
            "schema_version",
            "build_id",
            "synthetic",
            "verification_level",
            "coordinate_encoding",
            "asset_registry_sha256",
            "material_bundle_id",
            "material_bundle_manifest_sha256",
            "material_algorithm_id",
            "material_input_registry",
            "blender_identity",
            "builder_script_sha256",
            "recipes",
            "lod_levels",
        },
        "mesh build request",
    )
    if (
        request["schema_version"] != REQUEST_SCHEMA
        or request["synthetic"] is not True
        or request["verification_level"] != "L0"
        or request["coordinate_encoding"] != COORDINATE_ENCODING
        or request["lod_levels"] != [0, 1, 2]
        or any(
            not _is_sha256(request[key])
            for key in (
                "build_id",
                "asset_registry_sha256",
                "material_bundle_id",
                "material_bundle_manifest_sha256",
                "builder_script_sha256",
            )
        )
    ):
        raise MeshTemplateRuntimeError("mesh build request identity is invalid")
    unsigned = dict(request)
    build_id = unsigned.pop("build_id")
    if _sha256_bytes(_canonical_bytes(unsigned)) != build_id:
        raise MeshTemplateRuntimeError("mesh build ID disagrees with request bytes")
    if _sha256_file(Path(__file__).resolve()) != request["builder_script_sha256"]:
        raise MeshTemplateRuntimeError("builder script bytes disagree with request")

    identity = request["blender_identity"]
    _expect_exact_keys(
        identity,
        {
            "tool_id",
            "executable_sha256",
            "version",
            "platform",
            "runtime_build_hash",
            "runtime_output_sha256",
            "engine",
            "view_transform",
        },
        "Blender identity",
    )
    runtime_hash = bpy.app.build_hash
    if isinstance(runtime_hash, bytes):
        runtime_hash = runtime_hash.decode("ascii")
    if (
        identity["tool_id"] != "blender"
        or identity["version"] != ".".join(str(value) for value in bpy.app.version)
        or identity["runtime_build_hash"] != runtime_hash
        or identity["engine"] != "BLENDER_EEVEE_NEXT"
        or identity["view_transform"] != "AgX"
    ):
        raise MeshTemplateRuntimeError("running Blender disagrees with request identity")

    material_rows = request["material_input_registry"]
    if not isinstance(material_rows, list) or len(material_rows) != 24:
        raise MeshTemplateRuntimeError("material input registry is incomplete")
    material_ids = []
    for row in material_rows:
        _expect_exact_keys(
            row,
            {
                "slot_id",
                "source_sha256",
                "base_color_sha256",
                "normal_sha256",
                "orm_sha256",
                "width",
                "height",
                "uv_policy",
                "nominal_tile_m",
                "normal_strength",
                "synthetic",
            },
            "material input",
        )
        if (
            row["synthetic"] is not True
            or row["width"] != 1024
            or row["height"] != 1024
            or any(
                not _is_sha256(row[key])
                for key in (
                    "source_sha256",
                    "base_color_sha256",
                    "normal_sha256",
                    "orm_sha256",
                )
            )
            or len(
                {
                    row["base_color_sha256"],
                    row["normal_sha256"],
                    row["orm_sha256"],
                },
            )
            != 3
            or isinstance(row["nominal_tile_m"], bool)
            or not isinstance(row["nominal_tile_m"], (int, float))
            or not math.isfinite(row["nominal_tile_m"])
            or row["nominal_tile_m"] <= 0
            or isinstance(row["normal_strength"], bool)
            or not isinstance(row["normal_strength"], (int, float))
            or not math.isfinite(row["normal_strength"])
            or row["normal_strength"] <= 0
        ):
            raise MeshTemplateRuntimeError("material input row is invalid")
        material_ids.append(row["slot_id"])
    if material_ids != sorted(material_ids) or len(set(material_ids)) != 24:
        raise MeshTemplateRuntimeError("material input closure is not stable")

    recipes = request["recipes"]
    if not isinstance(recipes, list) or len(recipes) != 11:
        raise MeshTemplateRuntimeError("mesh recipe registry is incomplete")
    if [row.get("asset_id") for row in recipes] != list(EXPECTED_ASSET_IDS):
        raise MeshTemplateRuntimeError("mesh recipes are not the exact sorted asset set")
    material_set = set(material_ids)
    for row in recipes:
        _expect_exact_keys(
            row,
            {
                "asset_id",
                "kind",
                "footprint_m",
                "recipe_id",
                "material_slot_ids",
                "lod_triangle_budgets",
            },
            "mesh recipe",
        )
        footprint = row["footprint_m"]
        if (
            row["recipe_id"] != EXPECTED_RECIPE_IDS[row["asset_id"]]
            or row["kind"] not in EXPECTED_BUDGETS
            or row["lod_triangle_budgets"] != EXPECTED_BUDGETS[row["kind"]]
            or not isinstance(footprint, list)
            or len(footprint) != 3
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
                for value in footprint
            )
            or not isinstance(row["material_slot_ids"], list)
            or not row["material_slot_ids"]
            or row["material_slot_ids"] != sorted(row["material_slot_ids"])
            or len(set(row["material_slot_ids"])) != len(row["material_slot_ids"])
            or not set(row["material_slot_ids"]) <= material_set
        ):
            raise MeshTemplateRuntimeError("mesh recipe contract is invalid")
    return request


def _compatibility_material_request(request):
    compatible = dict(request)
    compatible["visual_slot_registry"] = [
        {"slot_id": slot_id, "category": "material"}
        for slot_id in sorted(shared.VISUAL_MATERIALS)
    ]
    return compatible


def _clear_geometry():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for collection in list(bpy.data.collections):
        if collection.name != "Collection":
            bpy.data.collections.remove(collection)


def _new_root(asset_id, kind, lod, collection):
    root = bpy.data.objects.new(f"nv__asset-{asset_id}-lod{lod}", None)
    collection.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root["nv_root"] = True
    root["nv_asset_id"] = asset_id
    root["nv_lod"] = lod
    root["nv_synthetic"] = True
    root["nv_stable_id"] = f"{asset_id}-lod{lod}"
    root["nv_semantic_class"] = kind
    root["nv_components"] = "[]"
    return root


def _registry_for(asset_id, recipe, lod):
    return {
        "semantic_id": EXPECTED_ASSET_IDS.index(asset_id) + 1,
        "instance_id": lod + 1,
        "material_id": 1,
        "variant_id": recipe["recipe_id"],
    }


def _link_part(root, part_id, assembler, slot_id, materials, registry, collection):
    return _link_mesh(
        root,
        part_id,
        assembler,
        materials[slot_id],
        registry,
        collection,
    )


def _build_simple_building(recipe, lod, root, registry, materials, collection):
    width, depth, height = recipe["footprint_m"]
    slots = recipe["material_slot_ids"]
    body_height = height * (0.58 if lod == 0 else 0.64)

    body = MeshAssembler()
    body.add_box((0.0, 0.0, body_height / 2), (width - 1.1, depth - 1.1, body_height))
    _link_part(root, "walls", body, slots[0], materials, registry, collection)

    roof = MeshAssembler()
    roof.add_gabled_roof(
        width - 1.2,
        depth - 1.2,
        body_height,
        height - 0.15,
        overhang=0.42,
    )
    _link_part(root, "roof", roof, slots[1], materials, registry, collection)

    if len(slots) == 3:
        accent = MeshAssembler()
        accent.add_box(
            (0.0, -(depth - 1.1) / 2 - 0.06, body_height * 0.48),
            (width * 0.18, 0.14, body_height * 0.68),
        )
        _link_part(root, "front-accent", accent, slots[2], materials, registry, collection)

    if lod == 1:
        trim = MeshAssembler()
        beam = 0.16
        for x_value in (-width * 0.34, width * 0.34):
            trim.add_box(
                (x_value, -(depth - 1.1) / 2 - 0.08, body_height / 2),
                (beam, 0.16, body_height * 0.88),
            )
        trim.add_box(
            (0.0, -(depth - 1.1) / 2 - 0.08, body_height * 0.88),
            (width * 0.76, 0.16, beam),
        )
        _link_part(
            root,
            "facade-trim",
            trim,
            slots[-1],
            materials,
            registry,
            collection,
        )


def _building_material_for_part(asset_id, part_id):
    if asset_id == "house_wood_01":
        return (
            "material-gray-roof-tile-01"
            if "roof" in part_id
            else "material-weathered-timber-01"
        )
    if asset_id == "house_wood_02":
        if "roof" in part_id:
            return "material-gray-roof-tile-01"
        if part_id == "walls":
            return "material-pale-plaster-01"
        return "material-dark-timber-01"
    if asset_id == "house_stone_01":
        if "roof" in part_id:
            return "material-gray-roof-tile-01"
        if part_id in {"stone-platform", "walls"}:
            return "material-fieldstone-01"
        return "material-dark-timber-01"
    if asset_id == "house_thatch_01":
        if "roof" in part_id:
            return "material-woven-bamboo-01"
        if part_id == "walls":
            return "material-rammed-earth-01"
        return "material-dark-timber-01"
    if asset_id == "house_barn_01":
        if "roof" in part_id:
            return "material-gray-roof-tile-01"
        if "door" in part_id:
            return "material-weathered-timber-01"
        return "material-dark-timber-01"
    raise MeshTemplateRuntimeError("unknown building material recipe")


def _build_detailed_building(recipe, root, registry, materials, collection):
    width, depth, height = recipe["footprint_m"]
    asset_id = recipe["asset_id"]
    item = {
        "object_id": asset_id,
        "dimensions": {
            "width_m": max(2.0, width - 1.2),
            "depth_m": max(2.0, depth - 1.2),
            "height_m": height - 0.3,
        },
        "base_z_m": 0.0,
        "material_family": "weathered-timber",
    }
    _build_building(
        item,
        root,
        registry,
        materials,
        collection,
        shared.BUILDING_GEOMETRY_V2,
        None,
    )
    for child in root.children:
        part_id = child.get("nv_part_id")
        if child.type != "MESH" or not isinstance(part_id, str):
            raise MeshTemplateRuntimeError("detailed building contains an invalid part")
        material = materials[_building_material_for_part(asset_id, part_id)]
        child.data.materials.clear()
        child.data.materials.append(material)


def _build_tree(recipe, lod, root, registry, materials, collection):
    asset_id = recipe["asset_id"]
    width, depth, height = recipe["footprint_m"]
    trunk_slot, leaf_slot = recipe["material_slot_ids"]
    if asset_id == "tree_bamboo_01":
        stem_count = (2, 5, 9)[lod]
        stem_segments = (4, 6, 8)[lod]
        stems = MeshAssembler()
        for index in range(stem_count):
            angle = 2 * math.pi * index / stem_count
            radius = width * (0.14 if lod < 2 else 0.20)
            x_value = radius * math.cos(angle)
            y_value = radius * math.sin(angle)
            stem_height = height * (0.72 + 0.025 * (index % 4))
            stems.add_cylinder(
                (x_value, y_value, stem_height / 2),
                max(0.055, width * 0.025),
                stem_height,
                segments=stem_segments,
                radius_top=max(0.045, width * 0.018),
            )
        _link_part(root, "culms", stems, trunk_slot, materials, registry, collection)

        leaves = MeshAssembler()
        leaf_count = (1, 3, 7)[lod]
        for index in range(leaf_count):
            angle = 2 * math.pi * index / leaf_count + 0.35
            leaves.add_ellipsoid(
                (
                    width * 0.24 * math.cos(angle),
                    depth * 0.24 * math.sin(angle),
                    height * (0.72 + 0.035 * (index % 3)),
                ),
                (width * 0.28, depth * 0.13, height * 0.09),
                segments=(4, 6, 8)[lod],
                rings=(2, 2, 3)[lod],
            )
        _link_part(root, "leaf-clusters", leaves, leaf_slot, materials, registry, collection)
        return

    trunk = MeshAssembler()
    trunk.add_cylinder(
        (0.0, 0.0, height * 0.31),
        width * (0.08 if asset_id == "tree_pine_01" else 0.10),
        height * 0.62,
        segments=(4, 6, 8)[lod],
        radius_top=max(0.08, width * 0.045),
    )
    _link_part(root, "trunk", trunk, trunk_slot, materials, registry, collection)

    canopy = MeshAssembler()
    canopy_count = (
        (1, 3, 5)
        if asset_id == "tree_pine_01"
        else (1, 2, 3)
    )[lod]
    for index in range(canopy_count):
        if asset_id == "tree_pine_01":
            z_value = height * (0.48 + 0.105 * index)
            scale = 1.0 - 0.12 * index
            radius = (width * 0.48 * scale, depth * 0.48 * scale, height * 0.15)
        else:
            angle = 2 * math.pi * index / canopy_count
            z_value = height * (0.67 + 0.035 * (index % 2))
            radius = (width * 0.34, depth * 0.34, height * 0.22)
        x_value = 0.0 if asset_id == "tree_pine_01" else width * 0.12 * math.cos(angle)
        y_value = 0.0 if asset_id == "tree_pine_01" else depth * 0.12 * math.sin(angle)
        canopy.add_ellipsoid(
            (x_value, y_value, z_value),
            radius,
            segments=(4, 6, 8)[lod],
            rings=(2, 3, 4)[lod],
        )
    _link_part(root, "canopy", canopy, leaf_slot, materials, registry, collection)


def _build_prop(recipe, lod, root, registry, materials, collection):
    asset_id = recipe["asset_id"]
    width, depth, height = recipe["footprint_m"]
    slots = recipe["material_slot_ids"]
    if asset_id == "fence_wood_01":
        mesh = MeshAssembler()
        post_count = (1, 2, 4)[lod]
        for index in range(post_count):
            x_value = -width * 0.42 + width * 0.84 * index / max(1, post_count - 1)
            mesh.add_box((x_value, 0.0, height / 2), (0.12, depth, height))
        rail_count = (1, 2, 3)[lod]
        for index in range(rail_count):
            mesh.add_box(
                (0.0, 0.0, height * (0.28 + 0.23 * index)),
                (width, depth * 0.72, 0.10),
            )
        _link_part(root, "fence", mesh, slots[0], materials, registry, collection)
        return
    if asset_id == "stone_wall_01":
        mesh = MeshAssembler()
        block_count = (1, 3, 8)[lod]
        for index in range(block_count):
            row = index % 2
            column = index // 2
            columns = max(1, math.ceil(block_count / 2))
            block_width = width / columns
            mesh.add_box(
                (
                    -width / 2 + block_width * (column + 0.5),
                    0.0,
                    height * (0.25 + 0.48 * row),
                ),
                (
                    block_width * 0.94,
                    depth * (0.88 if (index % 3) else 1.0),
                    height * 0.46,
                ),
            )
        _link_part(root, "masonry", mesh, slots[0], materials, registry, collection)
        return
    if asset_id == "stone_lamp_01":
        stone = MeshAssembler()
        stone.add_box((0.0, 0.0, height * 0.10), (width, depth, height * 0.20))
        if lod >= 1:
            stone.add_box(
                (0.0, 0.0, height * 0.82),
                (width * 0.62, depth * 0.62, height * 0.20),
            )
        if lod == 2:
            stone.add_box(
                (0.0, 0.0, height * 0.96),
                (width * 0.82, depth * 0.82, height * 0.09),
            )
        _link_part(root, "stone", stone, slots[1], materials, registry, collection)
        metal = MeshAssembler()
        metal.add_cylinder(
            (0.0, 0.0, height * 0.48),
            width * 0.12,
            height * 0.65,
            segments=(4, 6, 8)[lod],
            radius_top=width * 0.08,
        )
        if lod == 2:
            metal.add_box(
                (0.0, 0.0, height * 0.77),
                (width * 0.50, depth * 0.50, height * 0.08),
            )
        _link_part(root, "metal", metal, slots[0], materials, registry, collection)
        return
    raise MeshTemplateRuntimeError("unknown prop recipe")


def _build_asset(recipe, lod, materials, material_request):
    _clear_geometry()
    collection = shared._new_collection(
        f"nv__template-{recipe['asset_id']}-lod{lod}",
    )
    root = _new_root(recipe["asset_id"], recipe["kind"], lod, collection)
    registry = _registry_for(recipe["asset_id"], recipe, lod)
    if recipe["kind"] == "building":
        if lod == 2:
            _build_detailed_building(recipe, root, registry, materials, collection)
        else:
            _build_simple_building(
                recipe,
                lod,
                root,
                registry,
                materials,
                collection,
            )
    elif recipe["kind"] == "vegetation":
        _build_tree(recipe, lod, root, registry, materials, collection)
    else:
        _build_prop(recipe, lod, root, registry, materials, collection)

    mesh_objects = tuple(child for child in root.children if child.type == "MESH")
    if not mesh_objects:
        raise MeshTemplateRuntimeError("mesh template contains no geometry")
    _apply_textured_uvs_and_tangents(
        mesh_objects,
        material_request,
        None,
    )
    bpy.context.view_layer.update()
    return root, mesh_objects


def _measure_enu_bounds(mesh_objects):
    vertices = [
        obj.matrix_world @ vertex.co
        for obj in mesh_objects
        for vertex in obj.data.vertices
    ]
    if not vertices:
        raise MeshTemplateRuntimeError("mesh template bounds are empty")
    minimum = [min(float(vertex[index]) for vertex in vertices) for index in range(3)]
    maximum = [max(float(vertex[index]) for vertex in vertices) for index in range(3)]
    if not all(math.isfinite(value) for value in (*minimum, *maximum)):
        raise MeshTemplateRuntimeError("mesh template bounds are non-finite")
    return {"min": minimum, "max": maximum}


def _load_glb_json(path):
    raw = path.read_bytes()
    if len(raw) < 28 or len(raw) > MAX_GLB_BYTES:
        raise MeshTemplateRuntimeError("exported GLB size is outside its bound")
    magic, version, declared = struct.unpack_from("<4sII", raw, 0)
    json_length, json_kind = struct.unpack_from("<I4s", raw, 12)
    if (
        magic != b"glTF"
        or version != 2
        or declared != len(raw)
        or json_kind != b"JSON"
        or json_length <= 0
        or 20 + json_length + 8 > len(raw)
    ):
        raise MeshTemplateRuntimeError("exported GLB header is invalid")
    try:
        document = json.loads(
            raw[20 : 20 + json_length].decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except MeshTemplateRuntimeError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MeshTemplateRuntimeError("exported GLB JSON is invalid") from exc
    return raw, document


def _measure_glb(path, recipe, bounds):
    raw, document = _load_glb_json(path)
    accessors = document.get("accessors")
    meshes = document.get("meshes")
    materials = document.get("materials")
    if (
        not isinstance(accessors, list)
        or not isinstance(meshes, list)
        or not meshes
        or not isinstance(materials, list)
        or not materials
    ):
        raise MeshTemplateRuntimeError("exported GLB closure is incomplete")
    primitive_count = 0
    triangle_count = 0
    for mesh in meshes:
        primitives = mesh.get("primitives") if isinstance(mesh, dict) else None
        if not isinstance(primitives, list) or not primitives:
            raise MeshTemplateRuntimeError("exported GLB mesh is empty")
        for primitive in primitives:
            if (
                not isinstance(primitive, dict)
                or primitive.get("mode", 4) != 4
                or not isinstance(primitive.get("indices"), int)
            ):
                raise MeshTemplateRuntimeError("exported GLB primitive is not indexed")
            index = primitive["indices"]
            if index < 0 or index >= len(accessors):
                raise MeshTemplateRuntimeError("exported GLB index accessor is invalid")
            accessor = accessors[index]
            count = accessor.get("count") if isinstance(accessor, dict) else None
            if not isinstance(count, int) or count <= 0 or count % 3:
                raise MeshTemplateRuntimeError("exported GLB triangle count is invalid")
            primitive_count += 1
            triangle_count += count // 3
    slots = []
    for material in materials:
        extras = material.get("extras") if isinstance(material, dict) else None
        slot_id = extras.get("slot_id") if isinstance(extras, dict) else None
        if not isinstance(slot_id, str):
            raise MeshTemplateRuntimeError("exported GLB material identity is missing")
        slots.append(slot_id)
    slots = sorted(slots)
    if slots != recipe["material_slot_ids"] or len(slots) != len(set(slots)):
        raise MeshTemplateRuntimeError("exported GLB material closure disagrees with recipe")
    return {
        "asset_id": recipe["asset_id"],
        "lod": None,
        "artifact_path": None,
        "glb_sha256": _sha256_bytes(raw),
        "glb_bytes": len(raw),
        "triangle_count": triangle_count,
        "primitive_count": primitive_count,
        "material_slot_ids": slots,
        "local_enu_aabb": bounds,
    }


def _export_glb(path, root, mesh_objects):
    bpy.ops.object.select_all(action="DESELECT")
    root.select_set(True)
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]
    result = bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_extras=True,
        export_tangents=True,
        export_yup=True,
    )
    if "FINISHED" not in result or not path.is_file() or path.stat().st_size <= 0:
        raise MeshTemplateRuntimeError("GLB export did not finish")
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _write_new(path, payload):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _build(request, materials_path, staging):
    if (
        not materials_path.is_absolute()
        or not materials_path.is_dir()
        or materials_path.is_symlink()
        or not staging.is_absolute()
        or staging.exists()
        or staging.is_symlink()
        or not staging.parent.is_dir()
        or staging.parent.is_symlink()
    ):
        raise MeshTemplateRuntimeError("build paths are not direct absent-only paths")
    temporary = staging.parent / f".mesh-runtime-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)
    try:
        shared._clear_factory_scene()
        material_paths = shared._validate_material_directory(
            materials_path,
            request,
        )
        material_request = _compatibility_material_request(request)
        materials = _create_textured_materials(
            material_request,
            material_paths,
        )
        artifacts = []
        for recipe in request["recipes"]:
            asset_directory = temporary / "artifacts" / recipe["asset_id"]
            asset_directory.mkdir(parents=True)
            triangle_counts = []
            for lod in (0, 1, 2):
                root, mesh_objects = _build_asset(
                    recipe,
                    lod,
                    materials,
                    material_request,
                )
                bounds = _measure_enu_bounds(mesh_objects)
                relative = f"artifacts/{recipe['asset_id']}/lod{lod}.glb"
                target = temporary / relative
                _export_glb(target, root, mesh_objects)
                evidence = _measure_glb(target, recipe, bounds)
                evidence["lod"] = lod
                evidence["artifact_path"] = relative
                budget = recipe["lod_triangle_budgets"][lod]
                if evidence["triangle_count"] > budget:
                    raise MeshTemplateRuntimeError(
                        f"{recipe['asset_id']} LOD {lod} exceeds triangle budget",
                    )
                triangle_counts.append(evidence["triangle_count"])
                artifacts.append(evidence)
            if not triangle_counts[0] < triangle_counts[1] < triangle_counts[2]:
                raise MeshTemplateRuntimeError(
                    f"{recipe['asset_id']} LOD triangle counts are not strict",
                )

        report = {
            "schema_version": REPORT_SCHEMA,
            "build_id": request["build_id"],
            "synthetic": True,
            "verification_level": "L0",
            "coordinate_encoding": COORDINATE_ENCODING,
            "blender_identity": request["blender_identity"],
            "builder_script_sha256": request["builder_script_sha256"],
            "artifacts": artifacts,
        }
        _write_new(temporary / "build-report.json", _canonical_bytes(report))
        os.replace(temporary, staging)
        try:
            descriptor = os.open(staging.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main():
    arguments = _arguments()
    request = _validate_request(_read_request(arguments.request))
    _build(request, arguments.materials, arguments.staging)
    print(
        json.dumps(
            {
                "build_id": request["build_id"],
                "artifacts": 33,
                "report": "build-report.json",
            },
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
