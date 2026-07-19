"""Build deterministic high-detail LOD2 assets with shared PBR textures."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import uuid
from collections import Counter
from pathlib import Path

import bpy


class NearMeshRuntimeError(RuntimeError):
    """The v2 builder cannot produce trustworthy near-mesh artifacts."""


REQUEST_SCHEMA = "nantai.synthetic-village.mesh-asset-build.v2"
REPORT_SCHEMA = "nantai.synthetic-village.mesh-asset-build-report.v2"
PLAN_SCHEMA = "nantai.synthetic-village.near-geometry-plan.v1"
PLAN_ALGORITHM = "deterministic-semantic-near-geometry-v1"
COORDINATE_ENCODING = "three-east-up-negative-north"
FOLIAGE_ALGORITHM = "deterministic-foliage-cutout-v1"
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_TEXTURE_BYTES = 32 * 1024 * 1024
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
EXPECTED_KINDS = {
    "fence_wood_01": "prop",
    "house_barn_01": "building",
    "house_stone_01": "building",
    "house_thatch_01": "building",
    "house_wood_01": "building",
    "house_wood_02": "building",
    "stone_lamp_01": "prop",
    "stone_wall_01": "prop",
    "tree_bamboo_01": "vegetation",
    "tree_broadleaf_01": "vegetation",
    "tree_pine_01": "vegetation",
}
EXPECTED_RECIPE_IDS = {
    "fence_wood_01": "weathered-timber-fence-near-v2",
    "house_barn_01": "dark-timber-barn-near-v2",
    "house_stone_01": "fieldstone-house-near-v2",
    "house_thatch_01": "rammed-earth-thatch-house-near-v2",
    "house_wood_01": "weathered-timber-house-near-v2",
    "house_wood_02": "plaster-timber-house-near-v2",
    "stone_lamp_01": "stone-metal-lamp-near-v2",
    "stone_wall_01": "dry-stone-wall-near-v2",
    "tree_bamboo_01": "clustered-bamboo-near-v2",
    "tree_broadleaf_01": "humid-broadleaf-near-v2",
    "tree_pine_01": "layered-pine-near-v2",
}
TRIANGLE_BANDS = {
    "building": (8_000, 15_000),
    "vegetation": (6_000, 12_000),
    "prop": (1_000, 4_000),
}
PRIMITIVE_BUILDERS = {
    "box": "_box_geometry",
    "bevelled-box": "_bevelled_box_geometry",
    "cylinder": "_prism_geometry",
    "roof-tile": "_roof_grid_geometry",
    "thatch-strip": "_roof_grid_geometry",
    "branch": "_prism_geometry",
    "leaf-card": "_leaf_card_geometry",
    "stone-block": "_bevelled_box_geometry",
    "frame": "_box_geometry",
}


def _reject_duplicate_keys(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise NearMeshRuntimeError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_nonfinite(value):
    raise NearMeshRuntimeError(f"JSON contains non-finite number: {value}")


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


def _finite_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _expect_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise NearMeshRuntimeError(f"{label} has unknown or missing fields")


def _read_bounded(path, maximum, label):
    try:
        before = path.stat()
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or before.st_size <= 0
            or before.st_size > maximum
        ):
            raise NearMeshRuntimeError(f"{label} is not a bounded direct file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
            ) != (
                before.st_dev,
                before.st_ino,
                before.st_size,
            ):
                raise NearMeshRuntimeError(f"{label} changed before read")
            raw = stream.read(maximum + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
    except NearMeshRuntimeError:
        raise
    except OSError as exc:
        raise NearMeshRuntimeError(f"{label} cannot be read") from exc
    signature = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    if (
        len(raw) != before.st_size
        or signature
        != (
            after_open.st_dev,
            after_open.st_ino,
            after_open.st_size,
            after_open.st_mtime_ns,
        )
        or signature
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
    ):
        raise NearMeshRuntimeError(f"{label} changed during read")
    return raw


def _read_request(path):
    raw = _read_bounded(path, MAX_REQUEST_BYTES, "near mesh request")
    try:
        request = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except NearMeshRuntimeError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NearMeshRuntimeError("near mesh request JSON is invalid") from exc
    if raw != _canonical_bytes(request):
        raise NearMeshRuntimeError("near mesh request must be canonical JSON")
    return request


def _validate_component(component, material_slots):
    _expect_keys(
        component,
        (
            "component_id",
            "part_class",
            "primitive",
            "material_slot_id",
            "position",
            "scale",
            "rotation_degrees",
            "planned_triangles",
            "parent_id",
            "elevation",
        ),
        "near geometry component",
    )
    transforms = (
        component["position"],
        component["scale"],
        component["rotation_degrees"],
    )
    if (
        not isinstance(component["component_id"], str)
        or not isinstance(component["part_class"], str)
        or component["primitive"] not in PRIMITIVE_BUILDERS
        or component["material_slot_id"] not in material_slots
        or any(
            not isinstance(row, list)
            or len(row) != 3
            or any(not _finite_number(value) for value in row)
            for row in transforms
        )
        or any(value <= 0 for value in component["scale"])
        or isinstance(component["planned_triangles"], bool)
        or not isinstance(component["planned_triangles"], int)
        or component["planned_triangles"] < 2
        or (
            component["parent_id"] is not None
            and not isinstance(component["parent_id"], str)
        )
        or component["elevation"]
        not in {None, "east", "north", "south", "west"}
    ):
        raise NearMeshRuntimeError("near geometry component is invalid")


def build_near_geometry_plan(plan, recipe):
    """Validate and consume one exact canonical host-authored geometry plan."""

    _expect_keys(
        plan,
        (
            "schema_version",
            "plan_id",
            "algorithm_id",
            "asset_id",
            "kind",
            "footprint_m",
            "recipe_id",
            "material_slot_ids",
            "aabb",
            "covered_elevations",
            "detail_counts",
            "planned_triangles",
            "components",
            "synthetic",
            "geometry_usability",
        ),
        "near geometry plan",
    )
    components = plan["components"]
    if (
        plan["schema_version"] != PLAN_SCHEMA
        or plan["algorithm_id"] != PLAN_ALGORITHM
        or plan["asset_id"] != recipe["asset_id"]
        or plan["kind"] != recipe["kind"]
        or plan["footprint_m"] != recipe["footprint_m"]
        or plan["recipe_id"] != recipe["recipe_id"]
        or plan["material_slot_ids"] != recipe["material_slot_ids"]
        or plan["synthetic"] is not True
        or plan["geometry_usability"] != "preview-only"
        or not _is_sha256(plan["plan_id"])
        or not isinstance(components, list)
        or not components
    ):
        raise NearMeshRuntimeError("near geometry plan identity is invalid")
    unsigned = dict(plan)
    unsigned.pop("plan_id")
    if _sha256_bytes(_canonical_bytes(unsigned)) != plan["plan_id"]:
        raise NearMeshRuntimeError("near geometry plan ID is invalid")
    for component in components:
        _validate_component(component, set(plan["material_slot_ids"]))
    component_ids = [row["component_id"] for row in components]
    if (
        component_ids != sorted(component_ids)
        or len(component_ids) != len(set(component_ids))
        or any(
            row["parent_id"] is not None
            and row["parent_id"] not in set(component_ids)
            for row in components
        )
        or set(row["material_slot_id"] for row in components)
        != set(plan["material_slot_ids"])
    ):
        raise NearMeshRuntimeError(
            "near geometry component closure is invalid",
        )
    triangles = sum(row["planned_triangles"] for row in components)
    lower, upper = TRIANGLE_BANDS[plan["kind"]]
    if (
        triangles != plan["planned_triangles"]
        or not lower <= triangles <= upper
        or triangles < recipe["lod2_triangle_min"]
        or triangles > recipe["lod2_triangle_max"]
    ):
        raise NearMeshRuntimeError("near geometry triangle band is invalid")
    classes = Counter(row["part_class"] for row in components)
    if plan["kind"] == "building":
        if (
            set(
                (
                    "foundation",
                    "wall",
                    "roof-shell",
                    "roof-detail",
                    "eave",
                    "door-opening",
                    "window-opening",
                    "frame",
                ),
            )
            - set(classes)
            or classes["roof-detail"] != 576
            or classes["window-opening"] < 6
            or classes["door-opening"] < 2
            or plan["covered_elevations"]
            != ["east", "north", "south", "west"]
        ):
            raise NearMeshRuntimeError(
                "near building semantic detail is incomplete",
            )
    elif plan["kind"] == "vegetation":
        expected_branches = {
            "tree_bamboo_01": 96,
            "tree_broadleaf_01": 180,
            "tree_pine_01": 240,
        }[plan["asset_id"]]
        if (
            classes["leaf-card"] != 3_000
            or classes["branch"] != expected_branches
            or classes["trunk-or-culm"]
            != (12 if plan["asset_id"] == "tree_bamboo_01" else 1)
        ):
            raise NearMeshRuntimeError(
                "near vegetation semantic detail is incomplete",
            )
    else:
        expected = {
            "fence_wood_01": {"post": 12, "rail": 22, "brace": 10},
            "stone_lamp_01": {
                "bevelled-part": 48,
                "cage-member": 12,
            },
            "stone_wall_01": {
                "stone-block": 96,
                "cap-stone": 18,
            },
        }[plan["asset_id"]]
        if any(classes[key] != value for key, value in expected.items()):
            raise NearMeshRuntimeError(
                "near prop semantic detail is incomplete",
            )
    return plan


def _validate_request(request):
    _expect_keys(
        request,
        (
            "schema_version",
            "build_id",
            "synthetic",
            "verification_level",
            "coordinate_encoding",
            "source_v1_bundle_id",
            "source_v1_manifest_sha256",
            "material_bundle_id",
            "material_bundle_manifest_sha256",
            "material_algorithm_id",
            "material_input_registry",
            "foliage_atlas_set",
            "asset_registry_sha256",
            "blender_identity",
            "builder_script_sha256",
            "recipes",
            "geometry_plans",
            "reused_lods",
            "lod_levels_to_build",
            "alpha_cutoff",
            "sampler",
        ),
        "near mesh request",
    )
    if (
        request["schema_version"] != REQUEST_SCHEMA
        or request["synthetic"] is not True
        or request["verification_level"] != "L0"
        or request["coordinate_encoding"] != COORDINATE_ENCODING
        or request["lod_levels_to_build"] != [2]
        or request["alpha_cutoff"] != 0.45
        or request["sampler"]
        != {
            "mag_filter": 9729,
            "min_filter": 9987,
            "wrap_s": 10497,
            "wrap_t": 10497,
        }
        or not _is_sha256(request["build_id"])
        or not _is_sha256(request["builder_script_sha256"])
        or request["builder_script_sha256"] != _sha256_file(Path(__file__))
    ):
        raise NearMeshRuntimeError("near mesh request scalar contract is invalid")
    unsigned = dict(request)
    unsigned.pop("build_id")
    if _sha256_bytes(_canonical_bytes(unsigned)) != request["build_id"]:
        raise NearMeshRuntimeError("near mesh build ID is invalid")
    materials = request["material_input_registry"]
    material_keys = {
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
    }
    if (
        not isinstance(materials, list)
        or len(materials) != 24
        or [row.get("slot_id") for row in materials]
        != sorted(row.get("slot_id") for row in materials)
        or any(
            not isinstance(row, dict)
            or set(row) != material_keys
            or row["synthetic"] is not True
            or row["width"] != 1024
            or row["height"] != 1024
            or not all(
                _is_sha256(row[key])
                for key in (
                    "source_sha256",
                    "base_color_sha256",
                    "normal_sha256",
                    "orm_sha256",
                )
            )
            for row in materials
        )
    ):
        raise NearMeshRuntimeError(
            "near mesh material input registry is invalid",
        )
    recipes = request["recipes"]
    plans = request["geometry_plans"]
    if (
        not isinstance(recipes, list)
        or not isinstance(plans, list)
        or len(recipes) != 11
        or len(plans) != 11
        or [row.get("asset_id") for row in recipes]
        != list(EXPECTED_ASSET_IDS)
        or [row.get("asset_id") for row in plans]
        != list(EXPECTED_ASSET_IDS)
    ):
        raise NearMeshRuntimeError(
            "near mesh recipe and geometry closure is invalid",
        )
    for recipe, plan in zip(recipes, plans, strict=True):
        _expect_keys(
            recipe,
            (
                "asset_id",
                "kind",
                "footprint_m",
                "recipe_id",
                "material_slot_ids",
                "lod2_triangle_min",
                "lod2_triangle_max",
            ),
            "near mesh recipe",
        )
        if (
            recipe["kind"] != EXPECTED_KINDS[recipe["asset_id"]]
            or recipe["recipe_id"]
            != EXPECTED_RECIPE_IDS[recipe["asset_id"]]
            or recipe["material_slot_ids"]
            != sorted(recipe["material_slot_ids"])
            or (
                recipe["lod2_triangle_min"],
                recipe["lod2_triangle_max"],
            )
            != TRIANGLE_BANDS[recipe["kind"]]
        ):
            raise NearMeshRuntimeError("near mesh recipe is invalid")
        build_near_geometry_plan(plan, recipe)
    reuse = request["reused_lods"]
    if (
        not isinstance(reuse, list)
        or len(reuse) != 22
        or [(row.get("asset_id"), row.get("lod")) for row in reuse]
        != [
            (asset_id, lod)
            for asset_id in EXPECTED_ASSET_IDS
            for lod in (0, 1)
        ]
    ):
        raise NearMeshRuntimeError("near mesh v1 reuse closure is invalid")
    atlas = request["foliage_atlas_set"]
    if (
        not isinstance(atlas, dict)
        or atlas.get("algorithm_id") != FOLIAGE_ALGORITHM
        or atlas.get("alpha_cutoff") != 0.45
        or atlas.get("synthetic") is not True
        or atlas.get("real_photo_textures") is not False
        or not isinstance(atlas.get("records"), list)
        or [row.get("slot_id") for row in atlas["records"]]
        != [
            "material-bamboo-leaf-01",
            "material-broadleaf-canopy-01",
            "material-orchard-leaf-01",
        ]
    ):
        raise NearMeshRuntimeError("near mesh foliage atlas is invalid")
    return request


def _bindings_for(request, recipe):
    material_by_slot = {
        row["slot_id"]: row
        for row in request["material_input_registry"]
    }
    atlas_by_slot = {
        row["slot_id"]: row
        for row in request["foliage_atlas_set"]["records"]
    }
    bindings = []
    for slot_id in recipe["material_slot_ids"]:
        material = material_by_slot[slot_id]
        atlas = atlas_by_slot.get(slot_id)
        for role in ("base_color", "normal", "orm"):
            digest = (
                atlas[role]["sha256"]
                if atlas is not None
                else material[f"{role}_sha256"]
            )
            bindings.append(
                {
                    "uri": f"../textures/{digest}.png",
                    "sha256": digest,
                    "role": role,
                    "colour_space": (
                        "srgb" if role == "base_color" else "non-color"
                    ),
                    "material_slot_id": slot_id,
                    "derivation_algorithm_id": (
                        FOLIAGE_ALGORITHM
                        if atlas is not None
                        else request["material_algorithm_id"]
                    ),
                    "min_filter": 9987,
                    "mag_filter": 9729,
                    "wrap_s": 10497,
                    "wrap_t": 10497,
                },
            )
    return sorted(
        bindings,
        key=lambda row: (
            row["material_slot_id"],
            row["role"],
            row["sha256"],
            row["derivation_algorithm_id"],
        ),
    )


def _source_path_for_binding(request, binding, material_root, atlas_root):
    if binding["derivation_algorithm_id"] == FOLIAGE_ALGORITHM:
        path = atlas_root / "textures" / f"{binding['sha256']}.png"
    else:
        path = material_root / f"{binding['sha256']}.png"
    raw = _read_bounded(path, MAX_TEXTURE_BYTES, "near mesh texture input")
    if (
        _sha256_bytes(raw) != binding["sha256"]
        or len(raw) < 33
        or raw[:8] != b"\x89PNG\r\n\x1a\n"
        or struct.unpack_from(">I", raw, 8)[0] != 13
        or raw[12:16] != b"IHDR"
        or struct.unpack_from(">II", raw, 16) != (1024, 1024)
    ):
        raise NearMeshRuntimeError(
            "near mesh texture input differs from its binding",
        )
    return path, raw


def _copy_texture_closure(
    request,
    material_root,
    atlas_root,
    output_root,
):
    bindings = {
        row["sha256"]: row
        for recipe in request["recipes"]
        for row in _bindings_for(request, recipe)
    }
    sources = {}
    for digest, binding in sorted(bindings.items()):
        path, raw = _source_path_for_binding(
            request,
            binding,
            material_root,
            atlas_root,
        )
        target = output_root / "textures" / f"{digest}.png"
        with target.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if _sha256_file(target) != digest:
            raise NearMeshRuntimeError(
                "near mesh copied texture changed",
            )
        sources[digest] = path
    return sources


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.images,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for item in list(collection):
            collection.remove(item)


def _box_geometry(triangles):
    vertices = [
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    ]
    quads = (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    )
    if triangles == 12:
        faces = []
        for a, b, c, d in quads:
            faces.extend(((a, b, c), (a, c, d)))
        return vertices, faces
    if triangles != 24:
        raise NearMeshRuntimeError("box triangle contract is unsupported")
    faces = []
    for quad in quads:
        center = tuple(
            sum(vertices[index][axis] for index in quad) / 4
            for axis in range(3)
        )
        center_index = len(vertices)
        vertices.append(center)
        for index in range(4):
            faces.append(
                (
                    quad[index],
                    quad[(index + 1) % 4],
                    center_index,
                ),
            )
    return vertices, faces


def _bevelled_box_geometry(component):
    if component["planned_triangles"] != 28:
        raise NearMeshRuntimeError(
            "bevelled box triangle contract is unsupported",
        )
    digest = hashlib.sha256(
        component["component_id"].encode("utf-8"),
    ).digest()
    bevel_fraction = 0.065 + digest[0] / 255 * 0.025
    bevel_width = min(component["scale"]) * bevel_fraction
    bevel_x = min(0.10, bevel_width / component["scale"][0])
    bevel_y = min(0.10, bevel_width / component["scale"][1])
    cross_section = (
        (-0.5 + bevel_x, -0.5),
        (0.5 - bevel_x, -0.5),
        (0.5, -0.5 + bevel_y),
        (0.5, 0.5 - bevel_y),
        (0.5 - bevel_x, 0.5),
        (-0.5 + bevel_x, 0.5),
        (-0.5, 0.5 - bevel_y),
        (-0.5, -0.5 + bevel_y),
    )
    vertices = [
        (x, y, z)
        for z in (-0.5, 0.5)
        for x, y in cross_section
    ]
    faces = []
    for index in range(8):
        following = (index + 1) % 8
        faces.extend(
            (
                (index, following, 8 + following),
                (index, 8 + following, 8 + index),
            ),
        )
    for index in range(1, 7):
        faces.append((0, index + 1, index))
        faces.append((8, 8 + index, 8 + index + 1))
    if len(faces) != component["planned_triangles"]:
        raise NearMeshRuntimeError(
            "bevelled box realization differs from its plan",
        )
    return vertices, faces


def _prism_geometry(triangles, *, axis):
    segments = (triangles + 4) // 4
    if segments < 3 or 4 * segments - 4 != triangles:
        raise NearMeshRuntimeError("prism triangle contract is unsupported")
    vertices = []
    for end in (-0.5, 0.5):
        for index in range(segments):
            angle = 2 * math.pi * index / segments
            radial = (0.5 * math.cos(angle), 0.5 * math.sin(angle))
            vertices.append(
                (end, radial[0], radial[1])
                if axis == "x"
                else (radial[0], radial[1], end),
            )
    faces = []
    for index in range(segments):
        following = (index + 1) % segments
        faces.extend(
            (
                (index, following, segments + following),
                (index, segments + following, segments + index),
            ),
        )
    for index in range(1, segments - 1):
        faces.append((0, index + 1, index))
        faces.append(
            (
                segments,
                segments + index,
                segments + index + 1,
            ),
        )
    return vertices, faces


def _roof_grid_geometry(triangles, *, thatch):
    if triangles != 12:
        raise NearMeshRuntimeError("roof detail triangle contract is unsupported")
    vertices = []
    for row in range(4):
        y = -0.5 + row / 3
        for column in range(3):
            x = -0.5 + column / 2
            curve = (0.07 if not thatch else 0.035) * (1.0 - (2 * x) ** 2)
            ripple = (
                ((row + column) % 2) * 0.015
                if thatch
                else 0.0
            )
            vertices.append((x, y, curve + ripple))
    faces = []
    for row in range(3):
        for column in range(2):
            a = row * 3 + column
            b = a + 1
            d = (row + 1) * 3 + column
            c = d + 1
            faces.extend(((a, b, c), (a, c, d)))
    return vertices, faces


def _leaf_card_geometry(triangles):
    if triangles != 2:
        raise NearMeshRuntimeError("leaf-card triangle contract is unsupported")
    return (
        [
            (-0.5, -0.5, 0.0),
            (0.5, -0.5, 0.0),
            (0.5, 0.5, 0.0),
            (-0.5, 0.5, 0.0),
        ],
        [(0, 1, 2), (0, 2, 3)],
    )


def _opening_plane_geometry(component):
    thinnest_axis = min(
        range(3),
        key=lambda index: component["scale"][index],
    )
    if thinnest_axis == 0:
        vertices = [
            (0.0, -0.5, -0.5),
            (0.0, 0.5, -0.5),
            (0.0, 0.5, 0.5),
            (0.0, -0.5, 0.5),
        ]
    elif thinnest_axis == 1:
        vertices = [
            (-0.5, 0.0, -0.5),
            (0.5, 0.0, -0.5),
            (0.5, 0.0, 0.5),
            (-0.5, 0.0, 0.5),
        ]
    else:
        vertices = [
            (-0.5, -0.5, 0.0),
            (0.5, -0.5, 0.0),
            (0.5, 0.5, 0.0),
            (-0.5, 0.5, 0.0),
        ]
    return vertices, [(0, 1, 2), (0, 2, 3)]


def _component_geometry(component):
    primitive = component["primitive"]
    triangles = component["planned_triangles"]
    if primitive in {"box", "frame"}:
        if triangles == 2:
            return _opening_plane_geometry(component)
        return _box_geometry(triangles)
    if primitive in {"bevelled-box", "stone-block"}:
        return _bevelled_box_geometry(component)
    if primitive in {"cylinder", "branch"}:
        return _prism_geometry(
            triangles,
            axis="x" if primitive == "branch" else "z",
        )
    if primitive in {"roof-tile", "thatch-strip"}:
        return _roof_grid_geometry(
            triangles,
            thatch=primitive == "thatch-strip",
        )
    if primitive == "leaf-card":
        return _leaf_card_geometry(triangles)
    raise NearMeshRuntimeError("unknown near geometry primitive")


def _leaf_atlas_uv(component, vertex_index):
    try:
        ordinal = int(component["component_id"].rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise NearMeshRuntimeError(
            "leaf component ID has no atlas ordinal",
        ) from exc
    atlas_grid = 4
    cell_index = ordinal % (atlas_grid * atlas_grid)
    column = cell_index % atlas_grid
    row = cell_index // atlas_grid
    inset = 2.0 / 1024.0
    u0 = column / atlas_grid + inset
    v0 = row / atlas_grid + inset
    u1 = (column + 1) / atlas_grid - inset
    v1 = (row + 1) / atlas_grid - inset
    return (
        (u0, v0),
        (u1, v0),
        (u1, v1),
        (u0, v1),
    )[vertex_index]


def _projected_component_uv(
    component,
    material,
    polygon,
    vertex,
):
    if component["primitive"] == "leaf-card":
        return _leaf_atlas_uv(component, vertex.index)
    dominant_axis = max(
        range(3),
        key=lambda axis: abs(polygon.normal[axis]),
    )
    axes = {
        0: (1, 2),
        1: (0, 2),
        2: (0, 1),
    }[dominant_axis]
    nominal_tile_m = float(material["nominal_tile_m"])
    if not math.isfinite(nominal_tile_m) or nominal_tile_m <= 0:
        raise NearMeshRuntimeError("material tile scale is invalid")
    return tuple(
        (float(vertex.co[axis]) + 0.5)
        * component["scale"][axis]
        / nominal_tile_m
        for axis in axes
    )


def _load_image(path, digest, colours):
    image = bpy.data.images.load(str(path), check_existing=False)
    if tuple(image.size) != (1024, 1024):
        raise NearMeshRuntimeError("Blender decoded wrong texture dimensions")
    image.name = f"nv__{colours}-{digest}"
    image.colorspace_settings.name = colours
    return image


def _create_materials(request, recipe, bindings, texture_sources):
    material_inputs = {
        row["slot_id"]: row
        for row in request["material_input_registry"]
    }
    binding_by_role = {
        (row["material_slot_id"], row["role"]): row
        for row in bindings
    }
    materials = {}
    for slot_id in recipe["material_slot_ids"]:
        row = material_inputs[slot_id]
        base_binding = binding_by_role[(slot_id, "base_color")]
        normal_binding = binding_by_role[(slot_id, "normal")]
        orm_binding = binding_by_role[(slot_id, "orm")]
        base_image = _load_image(
            texture_sources[base_binding["sha256"]],
            base_binding["sha256"],
            "sRGB",
        )
        normal_image = _load_image(
            texture_sources[normal_binding["sha256"]],
            normal_binding["sha256"],
            "Non-Color",
        )
        orm_image = _load_image(
            texture_sources[orm_binding["sha256"]],
            orm_binding["sha256"],
            "Non-Color",
        )
        material = bpy.data.materials.new(f"nv__mat-{slot_id}")
        material.use_nodes = True
        material["slot_id"] = slot_id
        material["source_sha256"] = row["source_sha256"]
        material["bundle_id"] = request["material_bundle_id"]
        material["algorithm_id"] = request["material_algorithm_id"]
        material["synthetic"] = True
        material["uv_policy"] = (
            "leaf-card"
            if base_binding["derivation_algorithm_id"]
            == FOLIAGE_ALGORITHM
            else row["uv_policy"]
        )
        material["nominal_tile_m"] = row["nominal_tile_m"]
        material["nv_component"] = "near-mesh-v2"
        material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        principled = nodes.get("Principled BSDF")
        base = nodes.new("ShaderNodeTexImage")
        base.name = f"nv__base-color-{slot_id}"
        base.image = base_image
        normal = nodes.new("ShaderNodeTexImage")
        normal.name = f"nv__normal-{slot_id}"
        normal.image = normal_image
        orm = nodes.new("ShaderNodeTexImage")
        orm.name = f"nv__orm-{slot_id}"
        orm.image = orm_image
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.inputs["Strength"].default_value = 1.0
        separate = nodes.new("ShaderNodeSeparateColor")
        links.new(base.outputs["Color"], principled.inputs["Base Color"])
        links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
        links.new(orm.outputs["Color"], separate.inputs["Color"])
        links.new(separate.outputs["Green"], principled.inputs["Roughness"])
        links.new(separate.outputs["Blue"], principled.inputs["Metallic"])
        foliage = (
            base_binding["derivation_algorithm_id"] == FOLIAGE_ALGORITHM
        )
        if foliage:
            links.new(base.outputs["Alpha"], principled.inputs["Alpha"])
            if hasattr(material, "surface_render_method"):
                material.surface_render_method = "DITHERED"
            if hasattr(material, "use_transparency_overlap"):
                material.use_transparency_overlap = False
            material["nv_gltf_alpha_mode"] = "MASK"
            material["nv_gltf_alpha_cutoff"] = 0.45
            material["nv_gltf_double_sided"] = True
        materials[slot_id] = material
    return materials


def _create_component_object(component, material, collection):
    vertices, faces = _component_geometry(component)
    mesh = bpy.data.meshes.new(f"nv__mesh-{component['component_id']}")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update(calc_edges=True)
    uv_layer = mesh.uv_layers.new(name="nv_uv0")
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex = mesh.vertices[
                mesh.loops[loop_index].vertex_index
            ]
            uv = _projected_component_uv(
                component,
                material,
                polygon,
                vertex,
            )
            uv_layer.data[loop_index].uv = uv
    try:
        mesh.calc_tangents(uvmap=uv_layer.name)
    except Exception as exc:
        raise NearMeshRuntimeError(
            f"near mesh tangent generation failed: {component['component_id']}",
        ) from exc
    obj = bpy.data.objects.new(
        f"nv__part-{component['component_id']}",
        mesh,
    )
    collection.objects.link(obj)
    obj.location = component["position"]
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = tuple(
        math.radians(value)
        for value in component["rotation_degrees"]
    )
    obj.scale = component["scale"]
    obj["nv_component_id"] = component["component_id"]
    obj["nv_part_class"] = component["part_class"]
    obj["nv_parent_id"] = component["parent_id"] or ""
    obj["nv_asset_id"] = component["component_id"].split(":", 1)[0]
    return obj


def _measure_bounds(objects):
    bpy.context.view_layer.update()
    points = [
        obj.matrix_world @ vertex.co
        for obj in objects
        for vertex in obj.data.vertices
    ]
    if not points:
        raise NearMeshRuntimeError("near mesh has no measurable vertices")
    minimum = tuple(min(float(row[index]) for row in points) for index in range(3))
    maximum = tuple(max(float(row[index]) for row in points) for index in range(3))
    if not all(math.isfinite(value) for value in (*minimum, *maximum)):
        raise NearMeshRuntimeError("near mesh bounds are non-finite")
    return {"min": list(minimum), "max": list(maximum)}


def _verify_bounds(bounds, footprint, asset_id):
    tolerance = 1e-5
    if (
        abs(bounds["min"][0]) > footprint[0] / 2 + tolerance
        or abs(bounds["max"][0]) > footprint[0] / 2 + tolerance
        or abs(bounds["min"][1]) > footprint[1] / 2 + tolerance
        or abs(bounds["max"][1]) > footprint[1] / 2 + tolerance
        or abs(bounds["min"][2]) > tolerance
        or bounds["max"][2] > footprint[2] + tolerance
    ):
        raise NearMeshRuntimeError(
            "near mesh realized geometry exceeds its footprint: "
            f"{asset_id} bounds={bounds} footprint={footprint}",
        )


def _export_separate(path, root, objects):
    bpy.ops.object.select_all(action="DESELECT")
    root.select_set(True)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    previous_debug_value = bpy.app.debug_value
    try:
        # The glTF add-on emits one INFO line per object. A near vegetation
        # asset has thousands of semantic parts, so retain warnings/errors
        # while preventing successful exports from overflowing bounded logs.
        bpy.app.debug_value = 1
        result = bpy.ops.export_scene.gltf(
            filepath=str(path),
            export_format="GLTF_SEPARATE",
            use_selection=True,
            export_apply=True,
            export_extras=True,
            export_tangents=True,
            export_yup=True,
        )
    finally:
        bpy.app.debug_value = previous_debug_value
    if "FINISHED" not in result or not path.is_file():
        raise NearMeshRuntimeError("near mesh separate glTF export failed")


def _load_exported_json(path):
    raw = _read_bounded(path, 128 * 1024 * 1024, "exported glTF JSON")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except NearMeshRuntimeError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NearMeshRuntimeError("exported glTF JSON is invalid") from exc


def _role_texture_indices(material):
    pbr = material.get("pbrMetallicRoughness")
    if not isinstance(pbr, dict):
        raise NearMeshRuntimeError("exported material PBR block is absent")
    try:
        return {
            "base_color": pbr["baseColorTexture"]["index"],
            "normal": material["normalTexture"]["index"],
            "orm": pbr["metallicRoughnessTexture"]["index"],
        }
    except (KeyError, TypeError) as exc:
        raise NearMeshRuntimeError(
            "exported material texture roles are incomplete",
        ) from exc


def _pack_glb(document, binary):
    document_raw = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    document_raw += b" " * (-len(document_raw) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(document_raw) + 8 + len(binary)
    return b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, total),
            struct.pack("<I4s", len(document_raw), b"JSON"),
            document_raw,
            struct.pack("<I4s", len(binary), b"BIN\0"),
            binary,
        ),
    )


def _pack_external_texture_glb(
    gltf_path,
    target,
    bindings,
):
    document = _load_exported_json(gltf_path)
    buffers = document.get("buffers")
    images = document.get("images")
    textures = document.get("textures")
    materials = document.get("materials")
    if (
        not isinstance(buffers, list)
        or len(buffers) != 1
        or not isinstance(buffers[0], dict)
        or not isinstance(buffers[0].get("uri"), str)
        or not isinstance(images, list)
        or not images
        or not isinstance(textures, list)
        or not textures
        or not isinstance(materials, list)
        or not materials
    ):
        raise NearMeshRuntimeError(
            "exported separate glTF closure is invalid",
        )
    buffer_path = gltf_path.parent / buffers[0]["uri"]
    if buffer_path.parent != gltf_path.parent:
        raise NearMeshRuntimeError("exported binary path escapes scratch root")
    binary = _read_bounded(
        buffer_path,
        128 * 1024 * 1024,
        "exported glTF binary",
    )
    if buffers[0].get("byteLength") != len(binary):
        raise NearMeshRuntimeError(
            "exported glTF binary length is invalid",
        )
    binding_by_role = {
        (row["material_slot_id"], row["role"]): row
        for row in bindings
    }
    used_roles = set()
    used_images = set()
    for material in materials:
        extras = material.get("extras")
        if not isinstance(extras, dict):
            raise NearMeshRuntimeError(
                "exported material identity is absent",
            )
        slot_id = extras.get("slot_id")
        indices = _role_texture_indices(material)
        for role, texture_index in indices.items():
            if (
                isinstance(texture_index, bool)
                or not isinstance(texture_index, int)
                or texture_index < 0
                or texture_index >= len(textures)
                or not isinstance(textures[texture_index], dict)
            ):
                raise NearMeshRuntimeError(
                    "exported texture index is invalid",
                )
            image_index = textures[texture_index].get("source")
            if (
                isinstance(image_index, bool)
                or not isinstance(image_index, int)
                or image_index < 0
                or image_index >= len(images)
            ):
                raise NearMeshRuntimeError(
                    "exported image index is invalid",
                )
            binding = binding_by_role.get((slot_id, role))
            if binding is None:
                raise NearMeshRuntimeError(
                    "exported texture role is not requested",
                )
            images[image_index] = {
                "mimeType": "image/png",
                "name": f"nv__{slot_id}-{role}",
                "uri": binding["uri"],
            }
            used_images.add(image_index)
            used_roles.add((slot_id, role))
        foliage = any(
            row["material_slot_id"] == slot_id
            and row["derivation_algorithm_id"] == FOLIAGE_ALGORITHM
            for row in bindings
        )
        if foliage:
            material["alphaMode"] = "MASK"
            material["alphaCutoff"] = 0.45
            material["doubleSided"] = True
        else:
            material["alphaMode"] = "OPAQUE"
            material.pop("alphaCutoff", None)
            material.pop("doubleSided", None)
    if (
        used_roles != set(binding_by_role)
        or used_images != set(range(len(images)))
    ):
        raise NearMeshRuntimeError(
            "exported image closure is incomplete or extra",
        )
    samplers = document.setdefault("samplers", [{}])
    if not isinstance(samplers, list) or not samplers:
        raise NearMeshRuntimeError("exported sampler closure is invalid")
    for sampler in samplers:
        if not isinstance(sampler, dict):
            raise NearMeshRuntimeError("exported sampler is invalid")
        sampler.update(
            {
                "magFilter": 9729,
                "minFilter": 9987,
                "wrapS": 10497,
                "wrapT": 10497,
            },
        )
    for texture in textures:
        sampler = texture.get("sampler", 0)
        if (
            isinstance(sampler, bool)
            or not isinstance(sampler, int)
            or sampler < 0
            or sampler >= len(samplers)
        ):
            texture["sampler"] = 0
    buffers[0] = {"byteLength": len(binary)}
    payload = _pack_glb(document, binary)
    with target.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return document, payload


def _measure_document(document):
    accessors = document.get("accessors")
    meshes = document.get("meshes")
    materials = document.get("materials")
    if (
        not isinstance(accessors, list)
        or not isinstance(meshes, list)
        or not isinstance(materials, list)
    ):
        raise NearMeshRuntimeError("packed GLB evidence is incomplete")
    triangles = 0
    primitives = 0
    for mesh in meshes:
        rows = mesh.get("primitives") if isinstance(mesh, dict) else None
        if not isinstance(rows, list) or not rows:
            raise NearMeshRuntimeError("packed GLB mesh is empty")
        for primitive in rows:
            index = primitive.get("indices")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 0
                or index >= len(accessors)
                or not isinstance(accessors[index], dict)
                or not isinstance(accessors[index].get("count"), int)
                or accessors[index]["count"] % 3
            ):
                raise NearMeshRuntimeError(
                    "packed GLB index evidence is invalid",
                )
            triangles += accessors[index]["count"] // 3
            primitives += 1
    slots = sorted(
        material["extras"]["slot_id"]
        for material in materials
    )
    return triangles, primitives, slots


def _write_new(path, payload):
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _build_asset(
    request,
    recipe,
    plan,
    bindings,
    texture_sources,
    scratch,
    target,
):
    _clear_scene()
    collection = bpy.data.collections.new(
        f"nv__near-{recipe['asset_id']}",
    )
    bpy.context.scene.collection.children.link(collection)
    root = bpy.data.objects.new(
        f"nv__asset-{recipe['asset_id']}-lod2",
        None,
    )
    collection.objects.link(root)
    root["nv_asset_id"] = recipe["asset_id"]
    root["nv_lod"] = 2
    root["nv_recipe_id"] = recipe["recipe_id"]
    root["nv_plan_id"] = plan["plan_id"]
    root["nv_synthetic"] = True
    materials = _create_materials(
        request,
        recipe,
        bindings,
        texture_sources,
    )
    objects = []
    for component in plan["components"]:
        obj = _create_component_object(
            component,
            materials[component["material_slot_id"]],
            collection,
        )
        obj.parent = root
        objects.append(obj)
    bounds = _measure_bounds(objects)
    _verify_bounds(
        bounds,
        recipe["footprint_m"],
        recipe["asset_id"],
    )
    gltf_path = scratch / f"{recipe['asset_id']}.gltf"
    _export_separate(gltf_path, root, objects)
    document, payload = _pack_external_texture_glb(
        gltf_path,
        target,
        bindings,
    )
    triangle_count, primitive_count, slots = _measure_document(document)
    if (
        triangle_count != plan["planned_triangles"]
        or slots != recipe["material_slot_ids"]
    ):
        raise NearMeshRuntimeError(
            "packed GLB differs from its semantic geometry plan",
        )
    return {
        "asset_id": recipe["asset_id"],
        "lod": 2,
        "artifact_path": (
            f"artifacts/{recipe['asset_id']}/lod2.glb"
        ),
        "glb_sha256": _sha256_bytes(payload),
        "glb_bytes": len(payload),
        "triangle_count": triangle_count,
        "primitive_count": primitive_count,
        "material_slot_ids": slots,
        "local_enu_aabb": bounds,
        "texture_bindings": bindings,
    }


def _validate_directories(
    request_path,
    material_root,
    atlas_root,
    output_root,
    report_path,
):
    if (
        not request_path.is_absolute()
        or not material_root.is_absolute()
        or not material_root.is_dir()
        or material_root.is_symlink()
        or not atlas_root.is_absolute()
        or not atlas_root.is_dir()
        or atlas_root.is_symlink()
        or not output_root.is_absolute()
        or output_root.exists()
        or output_root.is_symlink()
        or not output_root.parent.is_dir()
        or output_root.parent.is_symlink()
        or report_path != output_root / "build-report.json"
    ):
        raise NearMeshRuntimeError(
            "near mesh build paths are not exact direct paths",
        )


def _build(
    request,
    material_root,
    atlas_root,
    output_root,
    report_path,
):
    temporary = output_root.parent / f".near-v2-runtime-{uuid.uuid4().hex}"
    scratch = output_root.parent / f".near-v2-scratch-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)
    scratch.mkdir(mode=0o700)
    try:
        (temporary / "artifacts").mkdir()
        (temporary / "textures").mkdir()
        texture_sources = _copy_texture_closure(
            request,
            material_root,
            atlas_root,
            temporary,
        )
        rows = []
        for recipe, plan in zip(
            request["recipes"],
            request["geometry_plans"],
            strict=True,
        ):
            asset_root = temporary / "artifacts" / recipe["asset_id"]
            asset_root.mkdir()
            rows.append(
                _build_asset(
                    request,
                    recipe,
                    plan,
                    _bindings_for(request, recipe),
                    texture_sources,
                    scratch,
                    asset_root / "lod2.glb",
                ),
            )
            for path in tuple(scratch.iterdir()):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
        report = {
            "schema_version": REPORT_SCHEMA,
            "build_id": request["build_id"],
            "synthetic": True,
            "verification_level": "L0",
            "coordinate_encoding": COORDINATE_ENCODING,
            "blender_identity": request["blender_identity"],
            "builder_script_sha256": request["builder_script_sha256"],
            "artifacts": rows,
        }
        _write_new(
            temporary / "build-report.json",
            _canonical_bytes(report),
        )
        os.replace(temporary, output_root)
        temporary = None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)


def _arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--material-root", type=Path, required=True)
    parser.add_argument("--atlas-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main():
    arguments = _arguments()
    request_path = arguments.request.absolute()
    material_root = arguments.material_root.absolute()
    atlas_root = arguments.atlas_root.absolute()
    output_root = arguments.output_root.absolute()
    report_path = arguments.report.absolute()
    _validate_directories(
        request_path,
        material_root,
        atlas_root,
        output_root,
        report_path,
    )
    request = _validate_request(_read_request(request_path))
    _build(
        request,
        material_root,
        atlas_root,
        output_root,
        report_path,
    )
    print(
        json.dumps(
            {
                "artifacts": 11,
                "build_id": request["build_id"],
                "report": "build-report.json",
            },
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
