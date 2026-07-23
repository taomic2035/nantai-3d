"""Apply a content-addressed EnvironmentModulePlan to a verified base scene.

This script runs only inside the pinned Blender 4.5.11 Windows runtime.  The
host opens the verified base ``.blend`` first, then supplies an absolute
canonical request path and an empty private staging directory after ``--``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import bpy

REQUEST_SCHEMA = "nantai.synthetic-village.environment-module-runtime-request.v1"
REPORT_SCHEMA = "nantai.synthetic-village.environment-module-build-report.v1"
COLLECTION_NAME = "nv__environment-modules-v1"
REQUEST_NAME = "module-build-request.json"
REPORT_NAME = "module-build-report.json"
OUTPUT_NAME = "village-modules.blend"
EXPECTED_BASE_ROOTS = 130
EXPECTED_MODULE_ROOTS = 45
EXPECTED_TOTAL_ROOTS = 175
UV_POLICIES = frozenset(
    {
        "world-xy",
        "dominant-axis-box",
        "roof-slope",
        "object-long-axis",
        "leaf-card",
    },
)

SEMANTIC_CLASS_BY_ID = {
    3: "building",
    4: "bridge",
    5: "creek",
    6: "pond",
    7: "path",
    8: "field",
    9: "orchard",
    10: "bamboo",
    11: "courtyard",
    12: "retaining-wall",
    13: "prop",
    14: "elevated-walkway",
}

MATERIAL_BINDINGS = {
    "material-courtyard-drain-01": (
        "material-shallow-water-01",
        "shallow-water",
    ),
    "material-courtyard-flagstone-01": (
        "material-wet-stone-paving-01",
        "wet-stone-paving",
    ),
    "material-courtyard-stone-01": (
        "material-fieldstone-01",
        "fieldstone",
    ),
    "material-courtyard-tile-01": (
        "material-gray-roof-tile-01",
        "dark-timber",
    ),
    "material-courtyard-timber-01": (
        "material-weathered-timber-01",
        "weathered-timber",
    ),
    "material-creek-stone-01": ("material-creek-rock-01", "fieldstone"),
    "material-service-iron-01": ("material-aged-metal-01", "dark-timber"),
    "material-service-stone-01": (
        "material-wet-stone-paving-01",
        "wet-stone-paving",
    ),
    "material-service-tile-01": (
        "material-gray-roof-tile-01",
        "dark-timber",
    ),
    "material-service-timber-01": (
        "material-weathered-timber-01",
        "weathered-timber",
    ),
    "material-stone-block-01": ("material-moss-stone-01", "fieldstone"),
    "material-water-01": ("material-shallow-water-01", "shallow-water"),
    "material-waterwheel-iron-01": (
        "material-aged-metal-01",
        "dark-timber",
    ),
    "material-waterwheel-wood-01": (
        "material-weathered-timber-01",
        "weathered-timber",
    ),
}


class RuntimeBuildError(RuntimeError):
    """The request, base scene, or generated module scene is invalid."""


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeBuildError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise RuntimeBuildError(f"non-finite JSON number: {value}")


def _canonical_bytes(payload):
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _expect_exact_keys(payload, keys, label):
    if not isinstance(payload, dict) or set(payload) != set(keys):
        raise RuntimeBuildError(f"{label} keys are not exact")


def _runtime_paths(argv):
    if "--" not in argv:
        raise RuntimeBuildError("runtime arguments must follow --")
    values = argv[argv.index("--") + 1 :]
    if len(values) != 2:
        raise RuntimeBuildError("runtime requires request and staging paths")
    request_path = Path(values[0])
    staging_path = Path(values[1])
    if not request_path.is_absolute() or not staging_path.is_absolute():
        raise RuntimeBuildError("request and staging paths must be absolute")
    request_path = request_path.absolute()
    staging_path = staging_path.absolute()
    if (
        not request_path.is_file()
        or not staging_path.is_dir()
        or request_path.parent != staging_path
        or request_path.name != REQUEST_NAME
    ):
        raise RuntimeBuildError("runtime staging layout is invalid")
    if request_path.is_symlink() or staging_path.is_symlink():
        raise RuntimeBuildError("runtime paths cannot be links")
    return request_path, staging_path


def _load_request(path):
    raw = path.read_bytes()
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise RuntimeBuildError("request bytes are absent or unbounded")
    try:
        request = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeBuildError("request JSON is invalid") from exc
    if raw != _canonical_bytes(request):
        raise RuntimeBuildError("request must use canonical JSON bytes")
    return request


def _validate_request(request):
    top_keys = {
        "schema_version",
        "build_id",
        "synthetic",
        "verification_level",
        "geometry_usability",
        "stage",
        "trust_effect",
        "base_build_id",
        "base_build_report_sha256",
        "base_blend_sha256",
        "base_blender_executable_sha256",
        "base_object_registry_sha256",
        "runtime_script_sha256",
        "environment_module_plan_sha256",
        "environment_module_plan",
        "material_bindings",
        "object_registry",
        "requested_artifact",
    }
    _expect_exact_keys(request, top_keys, "request")
    if (
        request["schema_version"] != REQUEST_SCHEMA
        or request["synthetic"] is not True
        or request["verification_level"] != "L0"
        or request["geometry_usability"] != "preview-only"
        or request["stage"] != "modeled-unverified"
        or request["trust_effect"] != "none"
        or request["requested_artifact"] != OUTPUT_NAME
    ):
        raise RuntimeBuildError("request provenance contract is invalid")
    digest_fields = (
        "build_id",
        "base_build_id",
        "base_build_report_sha256",
        "base_blend_sha256",
        "base_blender_executable_sha256",
        "base_object_registry_sha256",
        "runtime_script_sha256",
        "environment_module_plan_sha256",
    )
    if not all(_is_sha256(request[key]) for key in digest_fields):
        raise RuntimeBuildError("request contains an invalid SHA-256")
    without_id = dict(request)
    without_id.pop("build_id")
    if request["build_id"] != _sha256_bytes(_canonical_bytes(without_id)):
        raise RuntimeBuildError("request build_id is not canonical")
    if request["runtime_script_sha256"] != _sha256_file(Path(__file__)):
        raise RuntimeBuildError("runtime script bytes disagree with request")
    plan = request["environment_module_plan"]
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version")
        != "nantai.synthetic-village.environment-module.v1"
        or plan.get("verification_level") != "L0"
        or plan.get("geometry_usability") != "preview-only"
        or plan.get("trust_effect") != "none"
        or request["environment_module_plan_sha256"]
        != _sha256_bytes(_canonical_bytes(plan))
    ):
        raise RuntimeBuildError("environment module plan identity is invalid")
    modules = plan.get("modules")
    if (
        not isinstance(modules, list)
        or [row.get("module_id") for row in modules]
        != [
            "central-courtyard",
            "lower-bridge-waterwheel",
            "rear-service-courtyard",
        ]
    ):
        raise RuntimeBuildError("environment module set is not exact")
    bindings = request["material_bindings"]
    if not isinstance(bindings, list) or len(bindings) != len(MATERIAL_BINDINGS):
        raise RuntimeBuildError("material binding registry length is invalid")
    material_id_by_family = {}
    actual_bindings = {}
    for row in bindings:
        _expect_exact_keys(
            row,
            (
                "material_alias",
                "runtime_slot_id",
                "material_family",
                "material_id",
            ),
            "material binding",
        )
        material_id_by_family.setdefault(row["material_family"], row["material_id"])
        actual_bindings[row["material_alias"]] = (
            row["runtime_slot_id"],
            row["material_family"],
        )
    if actual_bindings != MATERIAL_BINDINGS:
        raise RuntimeBuildError("material bindings do not match runtime v1")
    registry = request["object_registry"]
    if not isinstance(registry, list) or len(registry) != EXPECTED_TOTAL_ROOTS:
        raise RuntimeBuildError("object registry length is not 175")
    if [row.get("instance_id") for row in registry] != list(
        range(1, EXPECTED_TOTAL_ROOTS + 1),
    ):
        raise RuntimeBuildError("object registry instances are not exact")
    if len({row.get("object_id") for row in registry}) != EXPECTED_TOTAL_ROOTS:
        raise RuntimeBuildError("object registry IDs are not unique")
    base_registry_sha = _sha256_bytes(_canonical_bytes(registry[:EXPECTED_BASE_ROOTS]))
    if base_registry_sha != request["base_object_registry_sha256"]:
        raise RuntimeBuildError("base object registry digest disagrees")
    parts = [
        part
        for module in modules
        for part in module.get("parts", [])
    ]
    if len(parts) != EXPECTED_MODULE_ROOTS:
        raise RuntimeBuildError("environment module parts are not exact")
    for part, registry_row in zip(
        parts,
        registry[EXPECTED_BASE_ROOTS:],
        strict=True,
    ):
        binding = next(
            (
                row
                for row in bindings
                if row["material_alias"] == part.get("material_slot_id")
            ),
            None,
        )
        if (
            binding is None
            or registry_row.get("object_id") != part.get("part_id")
            or registry_row.get("instance_id") != part.get("instance_id")
            or registry_row.get("semantic_id") != part.get("semantic_id")
            or registry_row.get("material_id") != binding.get("material_id")
            or registry_row.get("variant_id") is not None
        ):
            raise RuntimeBuildError("module registry disagrees with plan")
    return request


class MeshAssembler:
    def __init__(self):
        self.vertices = []
        self.faces = []

    def add(self, vertices, faces):
        offset = len(self.vertices)
        self.vertices.extend(tuple(float(value) for value in row) for row in vertices)
        self.faces.extend(tuple(offset + index for index in face) for face in faces)

    def add_box(self, center, size, yaw=0.0):
        cx, cy, cz = center
        sx, sy, sz = size
        hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
        cosine, sine = math.cos(yaw), math.sin(yaw)
        vertices = []
        for z_value in (-hz, hz):
            for y_value in (-hy, hy):
                for x_value in (-hx, hx):
                    vertices.append(
                        (
                            cx + x_value * cosine - y_value * sine,
                            cy + x_value * sine + y_value * cosine,
                            cz + z_value,
                        ),
                    )
        self.add(
            vertices,
            (
                (0, 1, 3, 2),
                (4, 6, 7, 5),
                (0, 4, 5, 1),
                (2, 3, 7, 6),
                (0, 2, 6, 4),
                (1, 5, 7, 3),
            ),
        )

    def add_cylinder(self, center, radius, height, segments=16, axis="z"):
        cx, cy, cz = center
        vertices = []
        for end in (-height / 2.0, height / 2.0):
            for index in range(segments):
                angle = 2.0 * math.pi * index / segments
                u = radius * math.cos(angle)
                v = radius * math.sin(angle)
                if axis == "x":
                    vertices.append((cx + end, cy + u, cz + v))
                elif axis == "y":
                    vertices.append((cx + u, cy + end, cz + v))
                else:
                    vertices.append((cx + u, cy + v, cz + end))
        bottom_center = len(vertices)
        vertices.append(
            (cx - height / 2.0, cy, cz)
            if axis == "x"
            else (cx, cy - height / 2.0, cz)
            if axis == "y"
            else (cx, cy, cz - height / 2.0),
        )
        top_center = len(vertices)
        vertices.append(
            (cx + height / 2.0, cy, cz)
            if axis == "x"
            else (cx, cy + height / 2.0, cz)
            if axis == "y"
            else (cx, cy, cz + height / 2.0),
        )
        faces = []
        for index in range(segments):
            following = (index + 1) % segments
            faces.append((index, following, segments + following, segments + index))
            faces.append((bottom_center, following, index))
            faces.append((top_center, segments + index, segments + following))
        self.add(vertices, faces)

    def add_quad(self, corners):
        self.add(corners, ((0, 1, 2, 3),))

    def add_arch_ring(
        self,
        center,
        outer_radius,
        inner_radius,
        depth,
        segments=32,
    ):
        center_x, center_y, base_z = center
        vertices = []
        for index in range(segments + 1):
            theta = math.pi * index / segments
            y_outer = center_y + outer_radius * math.cos(theta)
            z_outer = base_z + outer_radius * math.sin(theta)
            y_inner = center_y + inner_radius * math.cos(theta)
            z_inner = base_z + inner_radius * math.sin(theta)
            vertices.extend(
                (
                    (center_x - depth / 2.0, y_outer, z_outer),
                    (center_x + depth / 2.0, y_outer, z_outer),
                    (center_x - depth / 2.0, y_inner, z_inner),
                    (center_x + depth / 2.0, y_inner, z_inner),
                ),
            )
        faces = []
        for index in range(segments):
            a = index * 4
            b = (index + 1) * 4
            faces.extend(
                (
                    (a, b, b + 2, a + 2),
                    (a + 1, a + 3, b + 3, b + 1),
                    (a, a + 1, b + 1, b),
                    (a + 2, b + 2, b + 3, a + 3),
                ),
            )
        last = segments * 4
        faces.extend(((0, 2, 3, 1), (last, last + 1, last + 3, last + 2)))
        self.add(vertices, faces)

    def add_ring(self, center, radius, tube, depth, segments=24):
        cx, cy, cz = center
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            next_angle = 2.0 * math.pi * (index + 1) / segments
            x0 = cx + radius * math.cos(angle)
            z0 = cz + radius * math.sin(angle)
            x1 = cx + radius * math.cos(next_angle)
            z1 = cz + radius * math.sin(next_angle)
            dx, dz = x1 - x0, z1 - z0
            middle = ((x0 + x1) / 2.0, cy, (z0 + z1) / 2.0)
            # Segment boxes stay axis-aligned in elevation; the dense ring is
            # a visual L0 proxy and never collision evidence.
            self.add_box(
                middle,
                (max(tube, abs(dx) + tube), depth, max(tube, abs(dz) + tube)),
            )


def _tag(obj, row):
    obj["nv_stable_id"] = row["object_id"]
    obj["nv_semantic_id"] = row["semantic_id"]
    obj["nv_instance_id"] = row["instance_id"]
    obj["nv_material_id"] = row["material_id"]
    obj["nv_variant_id"] = row["variant_id"] or ""
    obj.pass_index = row["instance_id"]


def _new_module_root(module, part, registry, collection):
    root = bpy.data.objects.new(f"nv__{part['part_id']}", None)
    collection.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.8
    root["nv_root"] = True
    root["nv_semantic_class"] = SEMANTIC_CLASS_BY_ID[part["semantic_id"]]
    root["nv_module_id"] = module["module_id"]
    root["nv_recipe_version"] = module["recipe_version"]
    root["nv_design_source_sha256"] = module["design_source_sha256"]
    root["nv_geometry_usability"] = "preview-only"
    root["nv_stage"] = "modeled-unverified"
    root["nv_trust_effect"] = "none"
    root["nv_components"] = "[]"
    _tag(root, registry)
    return root


def _material_contract(material):
    policy = material.get("uv_policy")
    tile_m = material.get("nv_nominal_tile_m")
    color_input = material.get("nv_surface_color_input")
    if (
        policy not in UV_POLICIES
        or isinstance(tile_m, bool)
        or not isinstance(tile_m, (int, float))
        or not math.isfinite(tile_m)
        or tile_m <= 0
        or color_input != "nv_surface_color"
    ):
        raise RuntimeBuildError("module material contract is invalid")
    return policy, float(tile_m), color_input


def _dominant_projection_axes(normal):
    dominant = max(range(3), key=lambda index: abs(normal[index]))
    return tuple(index for index in range(3) if index != dominant)


def _polygon_uv_area(values):
    if len(values) < 3:
        return 0.0
    first = values[0]
    return max(
        (
            abs(
                (values[index][0] - first[0])
                * (values[index + 1][1] - first[1])
                - (values[index][1] - first[1])
                * (values[index + 1][0] - first[0]),
            )
            for index in range(1, len(values) - 1)
        ),
        default=0.0,
    )


def _project_polygon_uvs(obj, polygon, policy, tile_m):
    mesh = obj.data
    local = [mesh.vertices[index].co.copy() for index in polygon.vertices]
    world = [obj.matrix_world @ coordinate for coordinate in local]
    normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    world_normal = (normal_matrix @ polygon.normal).normalized()

    def project_axes(coordinates, axes):
        return [
            (float(coordinate[axes[0]]) / tile_m, float(coordinate[axes[1]]) / tile_m)
            for coordinate in coordinates
        ]

    if policy == "world-xy":
        values = project_axes(world, (0, 1))
    elif policy == "dominant-axis-box":
        values = project_axes(world, _dominant_projection_axes(world_normal))
    elif policy == "roof-slope":
        ridge = (-float(world_normal[1]), float(world_normal[0]), 0.0)
        ridge_length = math.sqrt(sum(value * value for value in ridge))
        if ridge_length <= 1e-8:
            ridge = (1.0, 0.0, 0.0)
        else:
            ridge = tuple(value / ridge_length for value in ridge)
        fall = (
            float(world_normal[1]) * ridge[2] - float(world_normal[2]) * ridge[1],
            float(world_normal[2]) * ridge[0] - float(world_normal[0]) * ridge[2],
            float(world_normal[0]) * ridge[1] - float(world_normal[1]) * ridge[0],
        )
        fall_length = math.sqrt(sum(value * value for value in fall))
        if fall_length <= 1e-8:
            values = project_axes(world, _dominant_projection_axes(world_normal))
        else:
            fall = tuple(value / fall_length for value in fall)
            values = [
                (
                    sum(float(coordinate[index]) * ridge[index] for index in range(3))
                    / tile_m,
                    sum(float(coordinate[index]) * fall[index] for index in range(3))
                    / tile_m,
                )
                for coordinate in world
            ]
    elif policy == "object-long-axis":
        spans = [
            max(vertex.co[index] for vertex in mesh.vertices)
            - min(vertex.co[index] for vertex in mesh.vertices)
            for index in range(3)
        ]
        long_axis = max(range(3), key=lambda index: spans[index])
        remaining = [index for index in range(3) if index != long_axis]
        second_axis = max(remaining, key=lambda index: spans[index])
        values = project_axes(local, (long_axis, second_axis))
    elif policy == "leaf-card":
        values = project_axes(local, _dominant_projection_axes(polygon.normal))
    else:
        raise RuntimeBuildError(f"unsupported module UV policy: {policy}")

    if _polygon_uv_area(values) <= 1e-12:
        values = project_axes(world, _dominant_projection_axes(world_normal))
    if _polygon_uv_area(values) <= 1e-12:
        raise RuntimeBuildError(f"module UV projection is degenerate: {obj.name}")
    return values


def _assign_projected_uvs(obj, policy, tile_m):
    mesh = obj.data
    layer = mesh.uv_layers.get("nv_uv0") or mesh.uv_layers.new(name="nv_uv0")
    for polygon in mesh.polygons:
        values = _project_polygon_uvs(obj, polygon, policy, tile_m)
        for loop_index, uv in zip(polygon.loop_indices, values, strict=True):
            layer.data[loop_index].uv = uv


def _ensure_white_surface_color(obj, layer_name):
    if layer_name != "nv_surface_color":
        raise RuntimeBuildError("module surface color layer name is invalid")
    mesh = obj.data
    layer = mesh.color_attributes.get(layer_name)
    if layer is None:
        layer = mesh.color_attributes.new(
            name="nv_surface_color",
            type="FLOAT_COLOR",
            domain="CORNER",
        )
    if (
        layer.data_type != "FLOAT_COLOR"
        or layer.domain != "CORNER"
        or len(layer.data) != len(mesh.loops)
    ):
        raise RuntimeBuildError(
            f"module surface color contract is invalid: {obj.name}",
        )
    mesh.color_attributes.active_color = layer
    index = tuple(mesh.color_attributes).index(layer)
    mesh.color_attributes.active_color_index = index
    mesh.color_attributes.render_color_index = index
    for value in layer.data:
        value.color = (1.0, 1.0, 1.0, 1.0)
    obj["nv_surface_color_mode"] = "white"


def _assign_material_contract(obj, material):
    policy, tile_m, color_input = _material_contract(material)
    _assign_projected_uvs(obj, policy, tile_m)
    _ensure_white_surface_color(obj, color_input)
    try:
        obj.data.calc_tangents(uvmap="nv_uv0")
    except Exception as exc:
        raise RuntimeBuildError(f"module tangent generation failed: {obj.name}") from exc
    obj["nv_uv_layer"] = "nv_uv0"
    obj["nv_tangents"] = True
    obj["nv_material_contract"] = "textured-pbr-v1"


def _link_mesh(root, assembler, material, registry, collection):
    if not assembler.vertices or not assembler.faces:
        raise RuntimeBuildError(f"module mesh is empty: {root['nv_stable_id']}")
    mesh = bpy.data.meshes.new(f"nv__mesh-{root['nv_stable_id']}__geometry")
    mesh.from_pydata(assembler.vertices, [], assembler.faces)
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(f"nv__{root['nv_stable_id']}__geometry", mesh)
    collection.objects.link(obj)
    obj.parent = root
    obj.data.materials.append(material)
    obj["nv_part_id"] = "module-geometry"
    obj["nv_root_id"] = root["nv_stable_id"]
    _tag(obj, registry)
    _assign_material_contract(obj, material)
    root["nv_components"] = '["module-geometry"]'
    return obj


def _central_geometry(part_id):
    mesh = MeshAssembler()
    if part_id == "courtyard-paving-001":
        mesh.add_box((0.0, 15.0, 72.62), (20.0, 14.0, 0.22))
    elif part_id == "courtyard-gallery-deck-001":
        mesh.add_box((0.0, 22.4, 74.35), (17.5, 3.0, 0.28))
    elif part_id == "courtyard-gallery-roof-001":
        mesh.add_box((0.0, 22.4, 77.18), (18.4, 4.0, 0.24))
    elif part_id == "courtyard-stair-run-001":
        for index in range(6):
            mesh.add_box(
                (0.0, 6.2 + index * 0.52, 70.40 + index * 0.24),
                (2.6, 0.72, 0.24),
            )
    elif part_id == "courtyard-ramp-run-001":
        for index in range(8):
            mesh.add_box(
                (7.8, 8.0 + index * 0.75, 70.75 + index * 0.14),
                (3.2, 0.86, 0.20),
            )
    elif part_id == "courtyard-drainage-channel-001":
        mesh.add_box((0.0, 8.7, 71.05), (12.0, 0.55, 0.10))
    elif part_id == "courtyard-segment-wall-001":
        mesh.add_box((-9.2, 15.0, 73.15), (0.55, 12.0, 1.25))
    elif part_id == "courtyard-segment-wall-002":
        mesh.add_box((9.2, 15.0, 73.15), (0.55, 12.0, 1.25))
    elif part_id in {"courtyard-workshed-001", "courtyard-workshed-002"}:
        center_x = -5.8 if part_id.endswith("001") else 5.8
        for x_value in (center_x - 2.1, center_x + 2.1):
            for y_value in (16.5, 20.0):
                mesh.add_box((x_value, y_value, 75.65), (0.28, 0.28, 4.2))
        mesh.add_box((center_x, 18.25, 77.85), (5.2, 4.5, 0.24))
    elif part_id in {"courtyard-workbench-001", "courtyard-workbench-002"}:
        center_x = -5.2 if part_id.endswith("001") else 5.2
        for offset in (-0.9, 0.9):
            mesh.add_box((center_x + offset, 17.8, 73.25), (1.5, 0.7, 0.76))
    elif part_id in {
        "courtyard-replaceable-prop-001",
        "courtyard-replaceable-prop-002",
    }:
        center_x = -3.2 if part_id.endswith("001") else 3.2
        for index in range(3):
            mesh.add_cylinder(
                (center_x + index * 0.85, 12.2, 73.0),
                0.30 + index * 0.04,
                0.75 + index * 0.12,
                12,
            )
    elif part_id == "courtyard-curb-edge-001":
        mesh.add_box((0.0, 9.25, 71.22), (12.5, 0.28, 0.38))
    else:
        raise RuntimeBuildError(f"unknown central module part: {part_id}")
    return mesh


def _waterwheel_anchor(recipe):
    if not isinstance(recipe, dict):
        raise RuntimeBuildError("lower-bridge recipe is invalid")
    anchor = recipe.get("waterwheel_assembly_anchor_m")
    if (
        not isinstance(anchor, list)
        or len(anchor) != 3
        or not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in anchor
        )
    ):
        raise RuntimeBuildError("waterwheel assembly anchor is invalid")
    return tuple(float(value) for value in anchor)


def _bridge_geometry(part_id, recipe):
    mesh = MeshAssembler()
    if part_id == "bridge-arch-001":
        mesh.add_arch_ring((-175.0, -115.0, 39.0), 5.4, 3.8, 4.0)
    elif part_id == "bridge-abutment-001":
        mesh.add_box((-175.0, -120.2, 42.0), (4.8, 3.0, 6.0))
    elif part_id == "bridge-abutment-002":
        mesh.add_box((-175.0, -109.8, 42.0), (4.8, 3.0, 6.0))
    elif part_id == "bridge-deck-slabs-001":
        mesh.add_box((-175.0, -115.0, 45.0), (5.2, 13.0, 0.55))
    elif part_id == "bridge-parapet-001":
        mesh.add_box((-177.45, -115.0, 45.85), (0.38, 13.0, 1.4))
    elif part_id == "bridge-parapet-002":
        mesh.add_box((-172.55, -115.0, 45.85), (0.38, 13.0, 1.4))
    elif part_id == "creek-bed-cut-001":
        mesh.add_box((-175.0, -115.0, 38.55), (13.0, 28.0, 0.35))
    elif part_id == "creek-bank-stone-001":
        mesh.add_box((-169.0, -115.0, 39.25), (1.2, 28.0, 1.6))
        mesh.add_box((-181.0, -115.0, 39.25), (1.2, 28.0, 1.6))
    elif part_id == "creek-water-surface-001":
        mesh.add_box((-175.0, -115.0, 38.92), (10.5, 28.0, 0.10))
    elif part_id == "waterwheel-wheel-001":
        anchor_x, anchor_y, anchor_z = _waterwheel_anchor(recipe)
        mesh.add_ring((anchor_x, anchor_y, anchor_z), 3.05, 0.28, 0.34)
        for index in range(8):
            angle = index * math.pi / 4.0
            mesh.add_box(
                (
                    anchor_x + math.cos(angle) * 1.45,
                    anchor_y,
                    anchor_z + math.sin(angle) * 1.45,
                ),
                (
                    max(0.24, abs(math.cos(angle)) * 3.0),
                    0.22,
                    max(0.24, abs(math.sin(angle)) * 3.0),
                ),
            )
    elif part_id == "waterwheel-axle-001":
        anchor_x, anchor_y, anchor_z = _waterwheel_anchor(recipe)
        mesh.add_cylinder((anchor_x, anchor_y, anchor_z), 0.38, 3.0, 20, axis="y")
    elif part_id == "waterwheel-bracket-001":
        anchor_x, anchor_y, anchor_z = _waterwheel_anchor(recipe)
        mesh.add_box(
            (anchor_x - 2.2, anchor_y - 1.0, anchor_z - 1.75),
            (0.45, 0.45, 4.0),
        )
        mesh.add_box(
            (anchor_x + 2.2, anchor_y - 1.0, anchor_z - 1.75),
            (0.45, 0.45, 4.0),
        )
        mesh.add_box(
            (anchor_x, anchor_y - 1.0, anchor_z - 3.65),
            (5.0, 0.55, 0.45),
        )
    elif part_id == "waterwheel-millrace-001":
        anchor_x, anchor_y, anchor_z = _waterwheel_anchor(recipe)
        mesh.add_box(
            (anchor_x - 3.8, anchor_y, anchor_z + 2.9),
            (7.0, 1.3, 0.18),
            0.08,
        )
        mesh.add_box(
            (anchor_x - 3.8, anchor_y - 0.72, anchor_z + 3.23),
            (7.0, 0.18, 0.65),
            0.08,
        )
        mesh.add_box(
            (anchor_x - 3.8, anchor_y + 0.72, anchor_z + 3.23),
            (7.0, 0.18, 0.65),
            0.08,
        )
    elif part_id == "waterwheel-spill-001":
        anchor_x, anchor_y, anchor_z = _waterwheel_anchor(recipe)
        mesh.add_box(
            (anchor_x - 0.2, anchor_y, anchor_z + 2.85),
            (1.0, 1.0, 0.22),
        )
        mesh.add_box(
            (anchor_x - 0.2, anchor_y, anchor_z + 1.65),
            (0.55, 0.75, 2.2),
        )
    elif part_id == "waterwheel-tailwater-001":
        anchor_x, anchor_y, anchor_z = _waterwheel_anchor(recipe)
        mesh.add_box(
            (anchor_x + 1.0, anchor_y, anchor_z - 4.1),
            (5.0, 2.0, 0.12),
        )
    else:
        raise RuntimeBuildError(f"unknown bridge module part: {part_id}")
    return mesh


def _service_geometry(part_id):
    mesh = MeshAssembler()
    if part_id == "service-paving-001":
        mesh.add_quad(
            (
                (1.0, 41.5, 78.63),
                (9.0, 41.5, 78.35),
                (9.0, 48.0, 79.89),
                (1.0, 48.0, 80.18),
            ),
        )
    elif part_id == "service-back-wall-001":
        mesh.add_box((5.0, 48.1, 81.2), (8.0, 0.45, 3.0))
    elif part_id == "service-side-wall-001":
        mesh.add_box((1.25, 44.8, 80.2), (0.45, 6.2, 1.3), -0.025)
    elif part_id == "service-side-wall-002":
        mesh.add_box((8.75, 44.8, 80.2), (0.45, 6.2, 1.3), -0.025)
    elif part_id == "service-door-assembly-001":
        mesh.add_box((5.0, 47.8, 80.7), (1.2, 0.20, 2.2))
    elif part_id == "service-window-assembly-001":
        mesh.add_box((7.0, 47.78, 81.3), (1.4, 0.18, 1.1))
    elif part_id == "service-eaves-001":
        mesh.add_box((5.0, 46.2, 83.55), (8.2, 4.2, 0.24))
    elif part_id == "service-gutter-001":
        mesh.add_cylinder((5.0, 44.15, 83.3), 0.14, 8.2, 12, axis="x")
    elif part_id == "service-drain-outlet-001":
        mesh.add_cylinder((8.9, 43.0, 79.4), 0.22, 1.0, 12, axis="x")
    elif part_id == "service-access-deck-001":
        mesh.add_box((5.0, 40.8, 79.0), (4.5, 2.2, 0.28))
    elif part_id in {"service-shed-001", "service-shed-002"}:
        center_x = 3.0 if part_id.endswith("001") else 7.0
        for x_value in (center_x - 1.25, center_x + 1.25):
            for y_value in (42.0, 45.0):
                mesh.add_box((x_value, y_value, 81.1), (0.25, 0.25, 3.8))
        mesh.add_box((center_x, 43.5, 83.1), (3.4, 4.0, 0.22))
    elif part_id == "service-storage-rack-001":
        for index in range(4):
            mesh.add_box((2.2 + index * 1.45, 46.4, 80.8), (1.1, 0.55, 1.7))
    elif part_id == "service-wood-pile-001":
        for index in range(3):
            mesh.add_box((2.3 + index * 1.1, 42.2, 79.7), (0.85, 0.70, 1.35))
    elif part_id == "service-wash-basin-001":
        for x_value in (6.3, 7.7):
            mesh.add_cylinder((x_value, 42.4, 79.25), 0.62, 0.45, 24)
    else:
        raise RuntimeBuildError(f"unknown service module part: {part_id}")
    return mesh


def _module_geometry(module_id, part_id, recipe):
    if module_id == "central-courtyard":
        return _central_geometry(part_id)
    if module_id == "lower-bridge-waterwheel":
        return _bridge_geometry(part_id, recipe)
    if module_id == "rear-service-courtyard":
        return _service_geometry(part_id)
    raise RuntimeBuildError(f"unknown environment module: {module_id}")


def _validate_base_scene(request):
    blend_path = Path(bpy.data.filepath)
    if (
        not blend_path.is_absolute()
        or not blend_path.is_file()
        or blend_path.is_symlink()
        or _sha256_file(blend_path) != request["base_blend_sha256"]
    ):
        raise RuntimeBuildError("loaded Blender scene is not the bound base artifact")
    roots = [
        obj
        for obj in bpy.data.objects
        if obj.get("nv_root") is True
    ]
    if len(roots) != EXPECTED_BASE_ROOTS:
        raise RuntimeBuildError("base scene canonical root count is not 130")
    by_id = {obj.get("nv_stable_id"): obj for obj in roots}
    if len(by_id) != EXPECTED_BASE_ROOTS:
        raise RuntimeBuildError("base scene canonical IDs are not unique")
    for row in request["object_registry"][:EXPECTED_BASE_ROOTS]:
        obj = by_id.get(row["object_id"])
        if (
            obj is None
            or obj.get("nv_instance_id") != row["instance_id"]
            or obj.get("nv_semantic_id") != row["semantic_id"]
            or obj.get("nv_material_id") != row["material_id"]
            or (obj.get("nv_variant_id") or None) != row["variant_id"]
        ):
            raise RuntimeBuildError("base scene registry disagrees with request")
    return roots


def _build_modules(request):
    if bpy.data.collections.get(COLLECTION_NAME) is not None:
        raise RuntimeBuildError("environment module collection already exists")
    collection = bpy.data.collections.new(COLLECTION_NAME)
    bpy.context.scene.collection.children.link(collection)
    bindings = {
        row["material_alias"]: row
        for row in request["material_bindings"]
    }
    registry = {
        row["object_id"]: row
        for row in request["object_registry"][EXPECTED_BASE_ROOTS:]
    }
    roots = []
    meshes = []
    for module in request["environment_module_plan"]["modules"]:
        for part in module["parts"]:
            row = registry[part["part_id"]]
            binding = bindings[part["material_slot_id"]]
            material = bpy.data.materials.get(
                f"nv__mat-{binding['runtime_slot_id']}",
            )
            if material is None or material.get("nv_slot_id") != binding["runtime_slot_id"]:
                raise RuntimeBuildError(
                    f"verified runtime material is absent: {binding['runtime_slot_id']}",
                )
            root = _new_module_root(module, part, row, collection)
            assembler = _module_geometry(
                module["module_id"],
                part["part_id"],
                module["recipe"],
            )
            mesh = _link_mesh(
                root,
                assembler,
                material,
                row,
                collection,
            )
            roots.append(root)
            meshes.append(mesh)
    return roots, meshes


def _validate_built_modules(request, base_roots, module_roots, module_meshes):
    all_roots = [obj for obj in bpy.data.objects if obj.get("nv_root") is True]
    expected_ids = [row["object_id"] for row in request["object_registry"]]
    actual_by_id = {obj.get("nv_stable_id"): obj for obj in all_roots}
    if (
        len(base_roots) != EXPECTED_BASE_ROOTS
        or len(module_roots) != EXPECTED_MODULE_ROOTS
        or len(all_roots) != EXPECTED_TOTAL_ROOTS
        or len(actual_by_id) != EXPECTED_TOTAL_ROOTS
        or set(actual_by_id) != set(expected_ids)
    ):
        raise RuntimeBuildError("combined canonical root registry is not exact 175")
    if len(module_meshes) != EXPECTED_MODULE_ROOTS:
        raise RuntimeBuildError("module mesh count is not exact 45")
    for root, mesh in zip(module_roots, module_meshes, strict=True):
        if (
            root.get("nv_stage") != "modeled-unverified"
            or root.get("nv_trust_effect") != "none"
            or root.get("nv_geometry_usability") != "preview-only"
            or not mesh.data.vertices
            or not mesh.data.polygons
            or mesh.get("nv_tangents") is not True
            or mesh.get("nv_material_contract") != "textured-pbr-v1"
            or len(mesh.data.materials) != 1
        ):
            raise RuntimeBuildError(
                f"module structural evidence is invalid: {root.get('nv_stable_id')}",
            )
        for vertex in mesh.data.vertices:
            if not all(math.isfinite(value) for value in vertex.co):
                raise RuntimeBuildError(
                    f"module mesh contains non-finite vertex: {mesh.name}",
                )
    bpy.context.scene["nv_environment_module_build"] = json.dumps(
        {
            "build_id": request["build_id"],
            "environment_module_plan_sha256": request[
                "environment_module_plan_sha256"
            ],
            "geometry_usability": "preview-only",
            "module_root_count": EXPECTED_MODULE_ROOTS,
            "stage": "modeled-unverified",
            "trust_effect": "none",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _module_material_contract_counts(module_meshes):
    textured = 0
    valid_uv = 0
    valid_surface_color = 0
    for obj in module_meshes:
        if len(obj.data.materials) != 1:
            continue
        _material_contract(obj.data.materials[0])
        textured += 1
        uv_layer = obj.data.uv_layers.get("nv_uv0")
        if uv_layer is not None and all(
            _polygon_uv_area(
                [uv_layer.data[index].uv for index in polygon.loop_indices],
            )
            > 1e-12
            for polygon in obj.data.polygons
        ):
            valid_uv += 1
        color_layer = obj.data.color_attributes.get("nv_surface_color")
        if (
            color_layer is not None
            and color_layer.data_type == "FLOAT_COLOR"
            and color_layer.domain == "CORNER"
            and len(color_layer.data) == len(obj.data.loops)
            and all(
                tuple(float(channel) for channel in value.color)
                == (1.0, 1.0, 1.0, 1.0)
                for value in color_layer.data
            )
        ):
            valid_surface_color += 1
    return textured, valid_uv, valid_surface_color


def _write_report(request, staging_path, output_path, module_meshes):
    textured, valid_uv, valid_surface_color = _module_material_contract_counts(
        module_meshes,
    )
    if (textured, valid_uv, valid_surface_color) != (
        EXPECTED_MODULE_ROOTS,
        EXPECTED_MODULE_ROOTS,
        EXPECTED_MODULE_ROOTS,
    ):
        raise RuntimeBuildError("saved module material contracts are incomplete")
    artifact = {
        "kind": "blender-scene",
        "name": OUTPUT_NAME,
        "sha256": _sha256_file(output_path),
        "size_bytes": output_path.stat().st_size,
    }
    report = {
        "schema_version": REPORT_SCHEMA,
        "build_id": request["build_id"],
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none",
        "base_build_id": request["base_build_id"],
        "base_build_report_sha256": request["base_build_report_sha256"],
        "base_blend_sha256": request["base_blend_sha256"],
        "environment_module_plan_sha256": request[
            "environment_module_plan_sha256"
        ],
        "runtime_script_sha256": request["runtime_script_sha256"],
        "object_registry": request["object_registry"],
        "material_bindings": request["material_bindings"],
        "counts": {
            "base_canonical_roots": EXPECTED_BASE_ROOTS,
            "module_canonical_roots": EXPECTED_MODULE_ROOTS,
            "canonical_roots": EXPECTED_TOTAL_ROOTS,
            "module_mesh_objects": len(module_meshes),
            "textured_module_meshes": textured,
            "valid_uv_module_meshes": valid_uv,
            "valid_surface_color_module_meshes": valid_surface_color,
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
        "artifact": artifact,
    }
    report_path = staging_path / REPORT_NAME
    with report_path.open("xb") as stream:
        stream.write(_canonical_bytes(report))
        stream.flush()
        os.fsync(stream.fileno())


def main():
    request_path, staging_path = _runtime_paths(sys.argv)
    request = _validate_request(_load_request(request_path))
    base_roots = _validate_base_scene(request)
    module_roots, module_meshes = _build_modules(request)
    _validate_built_modules(
        request,
        base_roots,
        module_roots,
        module_meshes,
    )
    output_path = staging_path / OUTPUT_NAME
    if output_path.exists() or (staging_path / REPORT_NAME).exists():
        raise RuntimeBuildError("module build outputs already exist")
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), check_existing=False)
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeBuildError("module Blender scene did not save")
    _write_report(request, staging_path, output_path, module_meshes)
    print(
        "NANTAI_ENVIRONMENT_MODULE_BUILD="
        + json.dumps(
            {
                "build_id": request["build_id"],
                "canonical_roots": EXPECTED_TOTAL_ROOTS,
                "module_roots": EXPECTED_MODULE_ROOTS,
                "stage": "modeled-unverified",
                "trust_effect": "none",
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimeBuildError as exc:
        print(f"NANTAI_ENVIRONMENT_MODULE_ERROR {exc}", flush=True)
        raise SystemExit(17) from None
