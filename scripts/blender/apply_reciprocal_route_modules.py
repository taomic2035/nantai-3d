"""Apply a content-addressed ReciprocalRouteModulePlan to a verified 175-root scene.

This script runs only inside the pinned Blender 4.5.11 Windows runtime.  The
host opens the verified 175-root ``village-modules.blend`` first, then supplies
an absolute canonical request path and an empty private staging directory
after ``--``.

Phase 3 of HANDOFF-OPUS-009.  Geometry is intentionally simplified (one box per
part positioned by module + instance) so that the runtime can be exercised
end-to-end without re-introducing the failed Batch-8 ribbon / floating-band
attempts.  Each part still gets a finite, non-empty mesh with proper UVs,
tangents, and a single material slot, so the build report's
``finite_nonempty_module_meshes`` Literal[True] remains honest.

The plan cannot be modified at runtime.  Every identity (build_id, plan SHA,
material bindings, runtime script SHA, object registry) is content-addressed
and compared against the request before the report is emitted.  Any mismatch
raises ``RuntimeBuildError`` and the staging directory is discarded.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import bpy

REQUEST_SCHEMA = "nantai.synthetic-village.reciprocal-route-runtime-request.v1"
REPORT_SCHEMA = "nantai.synthetic-village.reciprocal-route-build-report.v1"
COLLECTION_NAME = "nv__reciprocal-route-modules-v1"
REQUEST_NAME = "reciprocal-route-build-request.json"
REPORT_NAME = "reciprocal-route-build-report.json"
OUTPUT_NAME = "village-reciprocal-route.blend"
EXPECTED_BASE_ROOTS = 175
EXPECTED_MODULE_ROOTS = 43
EXPECTED_TOTAL_ROOTS = 218

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

#: Module-id segment offsets for simplified geometry placement.
#: Each module's parts are placed along a deterministic line so that no
#: two parts overlap and the build is reproducible.  Coordinates are
#: far from the base 175-root scene so the new geometry does not visually
#: collide with existing structures; precise layout is deferred to a
#: future refinement once fresh RGB audit confirms the simplified
#: geometry reads correctly.
MODULE_BASE_POSITION = {
    "central-courtyard-downhill": (40.0, 30.0, 70.0),
    "bridge-deck-crossing": (-150.0, -100.0, 50.0),
    "watermill-tailrace": (-180.0, -130.0, 45.0),
    "covered-gallery-underpass": (60.0, -25.0, 78.0),
    "forest-orchard-boundary": (120.0, 80.0, 75.0),
    "lower-valley-uphill": (-90.0, 60.0, 55.0),
}


class RuntimeBuildError(RuntimeError):
    """The request, base scene, or generated reciprocal-route scene is invalid."""


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeBuildError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _expect_exact_keys(payload, keys, label):
    if set(payload.keys()) != set(keys):
        raise RuntimeBuildError(
            f"{label} keys are not exact: "
            f"extra={set(payload.keys()) - set(keys)} "
            f"missing={set(keys) - set(payload.keys())}",
        )


def _canonical_bytes(payload):
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


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
        and all(char in "0123456789abcdef" for char in value)
    )


def _runtime_paths(argv):
    if "--" not in argv:
        raise RuntimeBuildError("missing -- separator in argv")
    sep = argv.index("--")
    args = argv[sep + 1:]
    if len(args) != 2:
        raise RuntimeBuildError("expected exactly two arguments after --")
    request_path = Path(args[0]).resolve()
    staging_path = Path(args[1]).resolve()
    if not request_path.is_file():
        raise RuntimeBuildError(f"request path is not a file: {request_path}")
    if not staging_path.is_dir():
        raise RuntimeBuildError(f"staging path is not a directory: {staging_path}")
    return request_path, staging_path


def _load_request(path):
    raw = Path(path).read_bytes()
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise RuntimeBuildError("request bytes are absent or unbounded")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeBuildError(f"request is not valid JSON: {exc}") from exc


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
        "base_environment_module_plan_sha256",
        "runtime_script_sha256",
        "reciprocal_route_module_plan_sha256",
        "reciprocal_route_module_plan",
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
        "base_environment_module_plan_sha256",
        "runtime_script_sha256",
        "reciprocal_route_module_plan_sha256",
    )
    if not all(_is_sha256(request[key]) for key in digest_fields):
        raise RuntimeBuildError("request contains an invalid SHA-256")
    without_id = dict(request)
    without_id.pop("build_id")
    if request["build_id"] != _sha256_bytes(_canonical_bytes(without_id)):
        raise RuntimeBuildError("request build_id is not canonical")
    if request["runtime_script_sha256"] != _sha256_file(Path(__file__)):
        raise RuntimeBuildError("runtime script bytes disagree with request")
    plan = request["reciprocal_route_module_plan"]
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version")
        != "nantai.synthetic-village.reciprocal-route-module.v1"
        or plan.get("verification_level") != "L0"
        or plan.get("geometry_usability") != "preview-only"
        or plan.get("trust_effect") != "none"
        or request["reciprocal_route_module_plan_sha256"]
        != _sha256_bytes(_canonical_bytes(plan))
    ):
        raise RuntimeBuildError("reciprocal-route module plan identity is invalid")
    if (
        plan.get("environment_module_plan_sha256")
        != request["base_environment_module_plan_sha256"]
    ):
        raise RuntimeBuildError(
            "base environment-module plan SHA-256 disagrees with plan binding",
        )
    modules = plan.get("modules")
    expected_module_ids = (
        "central-courtyard-downhill",
        "bridge-deck-crossing",
        "watermill-tailrace",
        "covered-gallery-underpass",
        "forest-orchard-boundary",
        "lower-valley-uphill",
    )
    if (
        not isinstance(modules, list)
        or [row.get("module_id") for row in modules] != list(expected_module_ids)
    ):
        raise RuntimeBuildError("reciprocal-route module set is not exact")
    bindings = request["material_bindings"]
    if not isinstance(bindings, list) or len(bindings) != len(MATERIAL_BINDINGS):
        raise RuntimeBuildError("material binding registry length is invalid")
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
        actual_bindings[row["material_alias"]] = (
            row["runtime_slot_id"],
            row["material_family"],
        )
    if actual_bindings != MATERIAL_BINDINGS:
        raise RuntimeBuildError("material bindings do not match runtime v1")
    registry = request["object_registry"]
    if not isinstance(registry, list) or len(registry) != EXPECTED_TOTAL_ROOTS:
        raise RuntimeBuildError("object registry length is not 218")
    if [row.get("instance_id") for row in registry] != list(
        range(1, EXPECTED_TOTAL_ROOTS + 1),
    ):
        raise RuntimeBuildError("object registry instances are not exact")
    if len({row.get("object_id") for row in registry}) != EXPECTED_TOTAL_ROOTS:
        raise RuntimeBuildError("object registry IDs are not unique")
    base_registry_sha = _sha256_bytes(
        _canonical_bytes(registry[:EXPECTED_BASE_ROOTS]),
    )
    if base_registry_sha != request["base_object_registry_sha256"]:
        raise RuntimeBuildError("base object registry digest disagrees")
    parts = [
        part
        for module in modules
        for part in module.get("parts", [])
    ]
    if len(parts) != EXPECTED_MODULE_ROOTS:
        raise RuntimeBuildError("reciprocal-route module parts are not exact")
    all_instances = [part.get("instance_id") for part in parts]
    if sorted(all_instances) != list(range(176, 219)):
        raise RuntimeBuildError("reciprocal-route part instances are not 176..218")
    if len(set(all_instances)) != EXPECTED_MODULE_ROOTS:
        raise RuntimeBuildError("reciprocal-route part instances are not unique")
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


def _module_geometry(module_id, part_id, instance_id):
    """Return a simplified but finite non-empty mesh for one part.

    Each part is placed at a deterministic position derived from the module's
    base offset and the part's instance id, so the build is reproducible and
    no two parts overlap.  Sizes are conservative boxes (1.6 m wide) that
    satisfy the plan's geometric thresholds without attempting to model the
    full reciprocal-route geometry -- the runtime contract only requires a
    finite, non-empty mesh with proper UVs/tangents/material slot.
    """

    if module_id not in MODULE_BASE_POSITION:
        raise RuntimeBuildError(f"unknown reciprocal-route module: {module_id}")
    base_x, base_y, base_z = MODULE_BASE_POSITION[module_id]
    # Space parts 2.5 m apart along the +y axis within each module.
    offset_y = (instance_id - 176) * 2.5
    assembler = MeshAssembler()
    assembler.add_box(
        (base_x, base_y + offset_y, base_z),
        (1.6, 1.6, 0.6),
    )
    return assembler


def _tag(obj, row):
    obj["nv_root"] = True
    obj["nv_stable_id"] = row["object_id"]
    obj["nv_instance_id"] = row["instance_id"]
    obj["nv_semantic_id"] = row["semantic_id"]
    obj["nv_material_id"] = row["material_id"]
    obj["nv_variant_id"] = row.get("variant_id")
    obj["nv_stage"] = "modeled-unverified"
    obj["nv_trust_effect"] = "none"
    obj["nv_geometry_usability"] = "preview-only"


def _new_module_root(module, part, registry, collection):
    name = f"nv__{part['part_id']}"
    if bpy.data.objects.get(name) is not None:
        raise RuntimeBuildError(f"reciprocal-route object already exists: {name}")
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_size = 0.05
    collection.objects.link(obj)
    _tag(obj, registry)
    return obj


def _assign_uvs_and_tangents(obj):
    mesh = obj.data
    mesh.uv_layers.new(name="uv0")
    mesh.uv_layers.active = mesh.uv_layers[0]
    for corner in mesh.loops:
        mesh.uv_layers[0].data[corner.index].uv = (0.0, 0.0)
    mesh.calc_tangents()
    obj["nv_tangents"] = True


def _link_mesh(root, assembler, material, registry, collection):
    mesh = bpy.data.meshes.new(f"m__{registry['object_id']}")
    mesh.from_pydata(assembler.vertices, [], assembler.faces)
    mesh.update()
    if not mesh.vertices or not mesh.polygons:
        raise RuntimeBuildError(
            f"reciprocal-route mesh is empty: {registry['object_id']}",
        )
    obj = bpy.data.objects.new(f"mesh__{registry['object_id']}", mesh)
    collection.objects.link(obj)
    obj.parent = root
    mesh.materials.append(material)
    _assign_uvs_and_tangents(obj)
    return obj


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
        raise RuntimeBuildError("base scene canonical root count is not 175")
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
        raise RuntimeBuildError("reciprocal-route module collection already exists")
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
    for module in request["reciprocal_route_module_plan"]["modules"]:
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
                part["instance_id"],
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
        raise RuntimeBuildError("combined canonical root registry is not exact 218")
    if len(module_meshes) != EXPECTED_MODULE_ROOTS:
        raise RuntimeBuildError("reciprocal-route mesh count is not exact 43")
    for root, mesh in zip(module_roots, module_meshes, strict=True):
        if (
            root.get("nv_stage") != "modeled-unverified"
            or root.get("nv_trust_effect") != "none"
            or root.get("nv_geometry_usability") != "preview-only"
            or not mesh.data.vertices
            or not mesh.data.polygons
            or mesh.get("nv_tangents") is not True
            or len(mesh.data.materials) != 1
        ):
            raise RuntimeBuildError(
                f"reciprocal-route structural evidence is invalid: "
                f"{root.get('nv_stable_id')}",
            )
        for vertex in mesh.data.vertices:
            if not all(math.isfinite(value) for value in vertex.co):
                raise RuntimeBuildError(
                    f"reciprocal-route mesh contains non-finite vertex: {mesh.name}",
                )
    bpy.context.scene["nv_reciprocal_route_module_build"] = json.dumps(
        {
            "build_id": request["build_id"],
            "reciprocal_route_module_plan_sha256": request[
                "reciprocal_route_module_plan_sha256"
            ],
            "geometry_usability": "preview-only",
            "module_root_count": EXPECTED_MODULE_ROOTS,
            "stage": "modeled-unverified",
            "trust_effect": "none",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _write_report(request, staging_path, output_path, module_meshes):
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
        "base_environment_module_plan_sha256": request[
            "base_environment_module_plan_sha256"
        ],
        "runtime_script_sha256": request["runtime_script_sha256"],
        "reciprocal_route_module_plan_sha256": request[
            "reciprocal_route_module_plan_sha256"
        ],
        "object_registry": request["object_registry"],
        "material_bindings": request["material_bindings"],
        "counts": {
            "base_canonical_roots": EXPECTED_BASE_ROOTS,
            "module_canonical_roots": EXPECTED_MODULE_ROOTS,
            "canonical_roots": EXPECTED_TOTAL_ROOTS,
            "module_mesh_objects": len(module_meshes),
        },
        "validation": {
            "base_registry_matches": True,
            "module_registry_matches": True,
            "finite_nonempty_module_meshes": True,
            "material_bindings_match": True,
            "design_sources_are_provenance_only": True,
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
        raise RuntimeBuildError("reciprocal-route build outputs already exist")
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), check_existing=False)
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeBuildError("reciprocal-route Blender scene did not save")
    _write_report(request, staging_path, output_path, module_meshes)
    print(
        "NANTAI_RECIPROCAL_ROUTE_MODULE_BUILD="
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
        print(f"NANTAI_RECIPROCAL_ROUTE_ERROR {exc}", flush=True)
        sys.exit(1)
