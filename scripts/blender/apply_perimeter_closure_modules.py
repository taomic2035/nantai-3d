"""Append the measured Batch24 perimeter overlay to a verified exact-218 scene.

This file is executed by the pinned Blender runtime.  It creates exactly 48
new canonical roots and one render mesh per root.  Vegetation meshes use two
explicitly bound materials (bark and canopy); other meshes use one.  All source
images remain design-only provenance; no pixel-derived geometry or trust
promotion occurs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import bpy

REQUEST_SCHEMA = "nantai.synthetic-village.perimeter-closure-runtime-request.v1"
REPORT_SCHEMA = "nantai.synthetic-village.perimeter-closure-build-report.v1"
COLLECTION_NAME = "nv__perimeter-closure-v1"
REQUEST_NAME = "perimeter-closure-build-request.json"
REPORT_NAME = "perimeter-closure-build-report.json"
OUTPUT_NAME = "village-perimeter-closure.blend"
EXPECTED_BASE_ROOTS = 218
EXPECTED_OVERLAY_ROOTS = 48
EXPECTED_TOTAL_ROOTS = 266

MODULE_ORDER = (
    "closure-upstream",
    "closure-northeast",
    "closure-east",
    "closure-southeast",
    "closure-downstream",
    "closure-southwest",
    "closure-west",
    "closure-northwest",
)
ROLE_ORDER = (
    "terrain-contact",
    "bidirectional-corridor",
    "support-retaining",
    "drainage-water",
    "boundary-seam",
    "vegetation-enclosure",
)
ROLE_SEMANTIC_IDS = {
    "terrain-contact": 8,
    "bidirectional-corridor": 7,
    "support-retaining": 12,
    "drainage-water": 5,
    "boundary-seam": 12,
    "vegetation-enclosure": 10,
}
ROLE_GEOMETRY = {
    "terrain-contact": "terrain-bench",
    "bidirectional-corridor": "walking-corridor",
    "support-retaining": "retaining-support",
    "drainage-water": "open-drainage",
    "boundary-seam": "sector-seam",
    "vegetation-enclosure": "vegetation-cluster",
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
    "material-perimeter-bark-01": (
        "material-broadleaf-bark-01",
        "weathered-timber",
    ),
    "material-perimeter-canopy-01": (
        "material-broadleaf-canopy-01",
        "orchard-leaf",
    ),
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
VEGETATION_MATERIAL_ALIASES = (
    "material-perimeter-bark-01",
    "material-perimeter-canopy-01",
)
UV_POLICIES = frozenset(
    {
        "world-xy",
        "dominant-axis-box",
        "roof-slope",
        "object-long-axis",
        "leaf-card",
    }
)


class RuntimeBuildError(RuntimeError):
    """The request, base scene, geometry, or report is invalid."""


class GeometryResult:
    def __init__(self, assembler, evidence):
        self.assembler = assembler
        self.evidence = evidence


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeBuildError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _expect_exact_keys(payload, keys, label):
    if not isinstance(payload, dict) or set(payload) != set(keys):
        actual = set(payload) if isinstance(payload, dict) else set()
        expected = set(keys)
        raise RuntimeBuildError(
            f"{label} keys are not exact: "
            f"extra={actual - expected} missing={expected - actual}"
        )


def _canonical_bytes(payload):
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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
        and all(char in "0123456789abcdef" for char in value)
    )


def _finite_vec3(value, label):
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(
            isinstance(component, bool)
            or not isinstance(component, (int, float))
            or not math.isfinite(component)
            for component in value
        )
    ):
        raise RuntimeBuildError(f"{label} must be a finite vec3")
    return tuple(float(component) for component in value)


def _runtime_paths(argv):
    if "--" not in argv:
        raise RuntimeBuildError("missing -- separator in argv")
    args = argv[argv.index("--") + 1 :]
    if len(args) != 2:
        raise RuntimeBuildError("expected exactly two arguments after --")
    request_path = Path(args[0]).resolve()
    staging_path = Path(args[1]).resolve()
    if (
        request_path.is_symlink()
        or not request_path.is_file()
        or staging_path.is_symlink()
        or not staging_path.is_dir()
    ):
        raise RuntimeBuildError("request/staging paths are not real files/directories")
    if request_path.parent != staging_path:
        raise RuntimeBuildError("request must stay inside staging directory")
    return request_path, staging_path


def _load_request(path):
    raw = Path(path).read_bytes()
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise RuntimeBuildError("request bytes are absent or unbounded")
    try:
        request = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeBuildError(f"request is not valid JSON: {exc}") from exc
    if raw != _canonical_bytes(request):
        raise RuntimeBuildError("request bytes are not canonical JSON")
    return request


def _validate_plan(plan, request):
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version")
        != "nantai.synthetic-village.perimeter-closure-module.v1"
        or plan.get("synthetic") is not True
        or plan.get("verification_level") != "L0"
        or plan.get("geometry_usability") != "preview-only"
        or plan.get("geometry_trust") != "modeled-unverified"
        or plan.get("training_use") != "forbidden-as-multiview"
        or plan.get("coverage_use") != "forbidden"
        or plan.get("trust_effect") != "none"
        or _sha256_bytes(_canonical_bytes(plan))
        != request["perimeter_closure_plan_sha256"]
        or plan.get("batch24_manifest_sha256")
        != request["batch24_manifest_sha256"]
    ):
        raise RuntimeBuildError("perimeter-closure plan identity is invalid")
    modules = plan.get("modules")
    if (
        not isinstance(modules, list)
        or [module.get("module_id") for module in modules] != list(MODULE_ORDER)
    ):
        raise RuntimeBuildError("perimeter-closure module order is invalid")
    parts = []
    for module in modules:
        module_parts = module.get("parts")
        if (
            not isinstance(module_parts, list)
            or len(module_parts) != 6
            or [part.get("semantic_role") for part in module_parts]
            != list(ROLE_ORDER)
        ):
            raise RuntimeBuildError(
                f"perimeter-closure roles are invalid: {module.get('module_id')}"
            )
        for part in module_parts:
            role = part.get("semantic_role")
            if (
                part.get("module_id") != module.get("module_id")
                or part.get("geometry_family") != ROLE_GEOMETRY.get(role)
            ):
                raise RuntimeBuildError("closure part identity is invalid")
            _finite_vec3(part.get("center_m"), "part center")
            extent = _finite_vec3(part.get("extent_m"), "part extent")
            if any(value <= 0.0 for value in extent):
                raise RuntimeBuildError("part extent must be positive")
            for field in (
                "inner_anchor_m",
                "outer_anchor_m",
                "previous_seam_m",
                "next_seam_m",
            ):
                _finite_vec3(part.get(field), field)
            orientation = part.get("orientation_deg")
            if (
                isinstance(orientation, bool)
                or not isinstance(orientation, (int, float))
                or not math.isfinite(orientation)
            ):
                raise RuntimeBuildError("part orientation must be finite")
        parts.extend(module_parts)
    if (
        len(parts) != EXPECTED_OVERLAY_ROOTS
        or [part.get("instance_id") for part in parts] != list(range(219, 267))
        or len({part.get("part_id") for part in parts}) != EXPECTED_OVERLAY_ROOTS
    ):
        raise RuntimeBuildError("perimeter-closure parts are not exact 219..266")
    return modules, parts


def _validate_request(request):
    top_keys = {
        "schema_version",
        "build_id",
        "synthetic",
        "verification_level",
        "geometry_usability",
        "stage",
        "trust_effect",
        "base_canonical_roots",
        "overlay_canonical_roots",
        "canonical_roots",
        "base_build_id",
        "base_build_request_sha256",
        "base_build_report_sha256",
        "base_blend_sha256",
        "base_object_registry_sha256",
        "base_reciprocal_route_module_plan_sha256",
        "blender_executable_sha256",
        "runtime_script_sha256",
        "batch24_manifest_sha256",
        "perimeter_closure_plan_sha256",
        "material_bindings_sha256",
        "perimeter_closure_plan",
        "material_bindings",
        "object_registry",
        "max_terrain_support_contact_gap_m",
        "max_corridor_endpoint_gap_m",
        "max_drainage_endpoint_gap_m",
        "max_sector_seam_gap_m",
        "requested_artifact",
    }
    _expect_exact_keys(request, top_keys, "request")
    if (
        request["schema_version"] != REQUEST_SCHEMA
        or request["synthetic"] is not True
        or request["verification_level"] != "L0"
        or request["geometry_usability"] != "preview-only"
        or request["stage"] != "modeled-unverified"
        or request["trust_effect"] != "none-quality-filter-only"
        or request["base_canonical_roots"] != EXPECTED_BASE_ROOTS
        or request["overlay_canonical_roots"] != EXPECTED_OVERLAY_ROOTS
        or request["canonical_roots"] != EXPECTED_TOTAL_ROOTS
        or request["requested_artifact"] != OUTPUT_NAME
        or request["max_terrain_support_contact_gap_m"] != 0.05
        or request["max_corridor_endpoint_gap_m"] != 0.1
        or request["max_drainage_endpoint_gap_m"] != 0.1
        or request["max_sector_seam_gap_m"] != 0.2
    ):
        raise RuntimeBuildError("request provenance/count/tolerance contract is invalid")
    digest_fields = (
        "build_id",
        "base_build_id",
        "base_build_request_sha256",
        "base_build_report_sha256",
        "base_blend_sha256",
        "base_object_registry_sha256",
        "base_reciprocal_route_module_plan_sha256",
        "blender_executable_sha256",
        "runtime_script_sha256",
        "batch24_manifest_sha256",
        "perimeter_closure_plan_sha256",
        "material_bindings_sha256",
    )
    if not all(_is_sha256(request[field]) for field in digest_fields):
        raise RuntimeBuildError("request contains an invalid SHA-256")
    without_id = dict(request)
    without_id.pop("build_id")
    if request["build_id"] != _sha256_bytes(_canonical_bytes(without_id)):
        raise RuntimeBuildError("request build_id is not canonical")
    if request["runtime_script_sha256"] != _sha256_file(Path(__file__)):
        raise RuntimeBuildError("runtime script bytes disagree with request")
    _modules, parts = _validate_plan(
        request["perimeter_closure_plan"],
        request,
    )
    bindings = request["material_bindings"]
    if not isinstance(bindings, list) or len(bindings) != len(MATERIAL_BINDINGS):
        raise RuntimeBuildError("material binding registry length is invalid")
    actual_bindings = {}
    material_id_by_alias = {}
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
        material_id_by_alias[row["material_alias"]] = row["material_id"]
    if actual_bindings != MATERIAL_BINDINGS:
        raise RuntimeBuildError("material bindings do not match exact-218 table")
    if _sha256_bytes(_canonical_bytes(bindings)) != request[
        "material_bindings_sha256"
    ]:
        raise RuntimeBuildError("material binding digest disagrees")
    registry = request["object_registry"]
    if (
        not isinstance(registry, list)
        or len(registry) != EXPECTED_TOTAL_ROOTS
        or [row.get("instance_id") for row in registry]
        != list(range(1, EXPECTED_TOTAL_ROOTS + 1))
        or len({row.get("object_id") for row in registry}) != EXPECTED_TOTAL_ROOTS
    ):
        raise RuntimeBuildError("object registry is not exact instances 1..266")
    if _sha256_bytes(_canonical_bytes(registry[:EXPECTED_BASE_ROOTS])) != request[
        "base_object_registry_sha256"
    ]:
        raise RuntimeBuildError("base object registry digest disagrees")
    for part, row in zip(parts, registry[EXPECTED_BASE_ROOTS:], strict=True):
        role = part["semantic_role"]
        if (
            row.get("object_id") != part.get("part_id")
            or row.get("instance_id") != part.get("instance_id")
            or row.get("semantic_id") != ROLE_SEMANTIC_IDS[role]
            or row.get("material_id")
            != material_id_by_alias.get(part.get("material_slot_id"))
            or row.get("variant_id") is not None
        ):
            raise RuntimeBuildError("overlay object registry disagrees with plan")
    return request


class MeshAssembler:
    def __init__(self):
        self.vertices = []
        self.faces = []
        self.face_material_indices = []

    def add(self, vertices, faces, material_index=0):
        if (
            isinstance(material_index, bool)
            or not isinstance(material_index, int)
            or material_index < 0
        ):
            raise RuntimeBuildError("mesh material index must be a non-negative integer")
        offset = len(self.vertices)
        self.vertices.extend(
            tuple(float(value) for value in vertex) for vertex in vertices
        )
        new_faces = [
            tuple(offset + index for index in face) for face in faces
        ]
        self.faces.extend(new_faces)
        self.face_material_indices.extend(
            material_index for _face in new_faces
        )

    def add_box(self, center, size, yaw=0.0, material_index=0):
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
                        )
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
            material_index=material_index,
        )

    def add_ribbon(self, start, end, width, thickness, material_index=0):
        ax, ay, az = start
        bx, by, bz = end
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            raise RuntimeBuildError("ribbon endpoints must differ in XY")
        px, py = -dy / length * width / 2.0, dx / length * width / 2.0
        half_t = thickness / 2.0
        self.add(
            (
                (ax - px, ay - py, az - half_t),
                (ax + px, ay + py, az - half_t),
                (bx - px, by - py, bz - half_t),
                (bx + px, by + py, bz - half_t),
                (ax - px, ay - py, az + half_t),
                (ax + px, ay + py, az + half_t),
                (bx - px, by - py, bz + half_t),
                (bx + px, by + py, bz + half_t),
            ),
            (
                (0, 2, 3, 1),
                (4, 5, 7, 6),
                (0, 1, 5, 4),
                (2, 6, 7, 3),
                (0, 4, 6, 2),
                (1, 3, 7, 5),
            ),
            material_index=material_index,
        )

    def add_cylinder(
        self,
        center,
        radius,
        depth,
        segments=10,
        material_index=0,
    ):
        cx, cy, cz = center
        vertices = [(cx, cy, cz - depth / 2), (cx, cy, cz + depth / 2)]
        for z_value in (-depth / 2, depth / 2):
            for index in range(segments):
                angle = 2.0 * math.pi * index / segments
                vertices.append(
                    (
                        cx + radius * math.cos(angle),
                        cy + radius * math.sin(angle),
                        cz + z_value,
                    )
                )
        faces = []
        first, second = 2, 2 + segments
        for index in range(segments):
            following = (index + 1) % segments
            faces.append(
                (
                    first + index,
                    first + following,
                    second + following,
                    second + index,
                )
            )
            faces.append((0, first + following, first + index))
            faces.append((1, second + index, second + following))
        self.add(vertices, faces, material_index=material_index)

    def add_ellipsoid(
        self,
        center,
        radii,
        *,
        segments=10,
        rings=5,
        yaw=0.0,
        material_index=0,
    ):
        cx, cy, cz = (float(value) for value in center)
        rx, ry, rz = (float(value) for value in radii)
        if (
            not all(math.isfinite(value) for value in (cx, cy, cz, rx, ry, rz, yaw))
            or min(rx, ry, rz) <= 0.0
            or isinstance(segments, bool)
            or not isinstance(segments, int)
            or segments < 6
            or isinstance(rings, bool)
            or not isinstance(rings, int)
            or rings < 3
        ):
            raise RuntimeBuildError("ellipsoid parameters are invalid")
        cosine, sine = math.cos(yaw), math.sin(yaw)
        vertices = [(cx, cy, cz - rz), (cx, cy, cz + rz)]
        for ring in range(1, rings):
            latitude = -math.pi / 2.0 + math.pi * ring / rings
            radial = math.cos(latitude)
            z_value = cz + rz * math.sin(latitude)
            for segment in range(segments):
                longitude = 2.0 * math.pi * segment / segments
                local_x = rx * radial * math.cos(longitude)
                local_y = ry * radial * math.sin(longitude)
                vertices.append(
                    (
                        cx + local_x * cosine - local_y * sine,
                        cy + local_x * sine + local_y * cosine,
                        z_value,
                    )
                )
        faces = []
        first_ring = 2
        for segment in range(segments):
            following = (segment + 1) % segments
            faces.append(
                (0, first_ring + following, first_ring + segment)
            )
        for ring in range(rings - 2):
            lower = first_ring + ring * segments
            upper = lower + segments
            for segment in range(segments):
                following = (segment + 1) % segments
                faces.append(
                    (
                        lower + segment,
                        lower + following,
                        upper + following,
                        upper + segment,
                    )
                )
        last_ring = first_ring + (rings - 2) * segments
        for segment in range(segments):
            following = (segment + 1) % segments
            faces.append(
                (1, last_ring + segment, last_ring + following)
            )
        self.add(vertices, faces, material_index=material_index)

    def bounds(self):
        if not self.vertices:
            raise RuntimeBuildError("cannot measure empty geometry")
        axes = tuple(zip(*self.vertices, strict=True))
        return (
            min(axes[0]),
            min(axes[1]),
            min(axes[2]),
            max(axes[0]),
            max(axes[1]),
            max(axes[2]),
        )


def _side_line_from_part(part):
    inner = tuple(float(value) for value in part["inner_anchor_m"])
    outer = tuple(float(value) for value in part["outer_anchor_m"])
    center = tuple(float(value) for value in part["center_m"])
    midpoint = (
        (inner[0] + outer[0]) / 2.0,
        (inner[1] + outer[1]) / 2.0,
    )
    offset = (center[0] - midpoint[0], center[1] - midpoint[1])
    if math.hypot(*offset) < 3.0:
        raise RuntimeBuildError(
            f"{part['semantic_role']} side offset is below standing-eye clearance"
        )
    return (
        (inner[0] + offset[0], inner[1] + offset[1], inner[2]),
        (outer[0] + offset[0], outer[1] + offset[1], outer[2]),
    )


def _build_terrain_contact(part, _collection):
    assembler = MeshAssembler()
    inner = tuple(part["inner_anchor_m"])
    outer = tuple(part["outer_anchor_m"])
    thickness = 3.0
    buried_inner = (inner[0], inner[1], inner[2] - thickness / 2.0)
    buried_outer = (outer[0], outer[1], outer[2] - thickness / 2.0)
    assembler.add_ribbon(
        buried_inner,
        buried_outer,
        width=20.0,
        thickness=thickness,
    )
    return GeometryResult(
        assembler,
        {
            "bounds": assembler.bounds(),
            "surface_inner_m": inner,
            "surface_outer_m": outer,
        },
    )


def _build_bidirectional_corridor(part, _collection):
    assembler = MeshAssembler()
    inner = tuple(part["inner_anchor_m"])
    outer = tuple(part["outer_anchor_m"])
    assembler.add_ribbon(inner, outer, width=4.0, thickness=0.35)
    return GeometryResult(
        assembler,
        {
            "bounds": assembler.bounds(),
            "inner_endpoint_m": inner,
            "outer_endpoint_m": outer,
        },
    )


def _build_support_retaining(part, _collection):
    assembler = MeshAssembler()
    inner = tuple(part["inner_anchor_m"])
    outer = tuple(part["outer_anchor_m"])
    side_inner, side_outer = _side_line_from_part(part)
    thickness = 4.0
    buried_inner = (
        side_inner[0],
        side_inner[1],
        inner[2] - thickness / 2.0,
    )
    buried_outer = (
        side_outer[0],
        side_outer[1],
        outer[2] - thickness / 2.0,
    )
    assembler.add_ribbon(
        buried_inner,
        buried_outer,
        width=1.2,
        thickness=thickness,
    )
    return GeometryResult(
        assembler,
        {
            "bounds": assembler.bounds(),
            "side_inner_m": side_inner,
            "side_outer_m": side_outer,
        },
    )


def _build_drainage_water(part, _collection):
    assembler = MeshAssembler()
    inner = tuple(part["inner_anchor_m"])
    outer = tuple(part["outer_anchor_m"])
    side_inner, side_outer = _side_line_from_part(part)
    water_inner = (side_inner[0], side_inner[1], inner[2] - 0.18)
    water_outer = (side_outer[0], side_outer[1], outer[2] - 0.18)
    assembler.add_ribbon(inner, water_inner, width=1.4, thickness=0.12)
    assembler.add_ribbon(water_inner, water_outer, width=1.4, thickness=0.12)
    assembler.add_ribbon(water_outer, outer, width=1.4, thickness=0.12)
    return GeometryResult(
        assembler,
        {
            "bounds": assembler.bounds(),
            "inner_endpoint_m": inner,
            "outer_endpoint_m": outer,
            "side_inner_m": side_inner,
            "side_outer_m": side_outer,
        },
    )


def _build_boundary_seam(part, _collection):
    assembler = MeshAssembler()
    outer = tuple(part["outer_anchor_m"])
    previous = tuple(part["previous_seam_m"])
    following = tuple(part["next_seam_m"])
    assembler.add_ribbon(previous, outer, width=3.0, thickness=0.5)
    assembler.add_ribbon(outer, following, width=3.0, thickness=0.5)
    return GeometryResult(
        assembler,
        {
            "bounds": assembler.bounds(),
            "previous_endpoint_m": previous,
            "next_endpoint_m": following,
        },
    )


def _build_vegetation_enclosure(part, _collection):
    assembler = MeshAssembler()
    inner = tuple(float(value) for value in part["inner_anchor_m"])
    outer = tuple(float(value) for value in part["outer_anchor_m"])
    _width, _depth, height = part["extent_m"]
    dx = outer[0] - inner[0]
    dy = outer[1] - inner[1]
    route_length_xy = math.hypot(dx, dy)
    if route_length_xy <= 0.0:
        raise RuntimeBuildError("vegetation route anchors must differ")
    ux, uy = dx / route_length_xy, dy / route_length_xy
    px, py = -uy, ux
    trunk_centers = []
    crown_lobe_count = 0
    crown_trunk_overlaps_m = []
    for index, (route_fraction, side_offset_m, scale) in enumerate(
        (
            (0.68, -6.0, 0.72),
            (0.72, 6.0, 0.88),
            (0.84, -7.0, 1.0),
            (0.90, 7.0, 0.78),
        )
    ):
        x = inner[0] + dx * route_fraction + px * side_offset_m
        y = inner[1] + dy * route_fraction + py * side_offset_m
        z = inner[2] + (outer[2] - inner[2]) * route_fraction
        trunk_centers.append((x, y, z))
        trunk_height = max(2.0, height * scale * 0.45)
        assembler.add_cylinder(
            (x, y, z + trunk_height / 2.0),
            radius=0.22 + 0.03 * index,
            depth=trunk_height,
            material_index=0,
        )
        crown_height = max(3.0, height * scale * 0.34)
        crown_center_z = z + trunk_height + crown_height * 0.30
        radius_x = 1.35 + index * 0.12
        radius_y = 1.15 + index * 0.08
        radius_z = max(1.4, crown_height * 0.38)
        lowest_crown_z = float("inf")
        for lobe_index, (
            along_offset_m,
            side_lobe_offset_m,
            height_offset_m,
            lobe_scale,
        ) in enumerate(
            (
                (-0.70, 0.16, -0.10, 0.82),
                (0.72, -0.16, 0.00, 0.82),
                (0.00, 0.00, -0.18, 1.00),
                (0.05, 0.52, 0.38, 0.72),
                (-0.05, -0.48, 0.32, 0.70),
            )
        ):
            lobe_x = x + ux * along_offset_m + px * side_lobe_offset_m
            lobe_y = y + uy * along_offset_m + py * side_lobe_offset_m
            lobe_center_z = crown_center_z + height_offset_m
            lobe_radius_z = radius_z * lobe_scale
            assembler.add_ellipsoid(
                (
                    lobe_x,
                    lobe_y,
                    lobe_center_z,
                ),
                (
                    radius_x * lobe_scale,
                    radius_y * lobe_scale,
                    lobe_radius_z,
                ),
                segments=10,
                rings=5,
                yaw=math.radians(
                    part["orientation_deg"]
                    + index * 17.0
                    + lobe_index * 31.0
                ),
                material_index=1,
            )
            lowest_crown_z = min(
                lowest_crown_z,
                lobe_center_z - lobe_radius_z,
            )
            crown_lobe_count += 1
        crown_trunk_overlaps_m.append(
            z + trunk_height - lowest_crown_z
        )

    understory_centers = []
    for index, (route_fraction, side_offset_m, scale) in enumerate(
        (
            (0.58, -8.2, 0.72),
            (0.62, 8.1, 0.88),
            (0.72, -8.8, 1.00),
            (0.76, 8.7, 0.82),
            (0.82, -9.2, 0.92),
            (0.86, 9.0, 0.76),
            (0.93, -8.4, 0.84),
            (0.96, 8.3, 0.68),
        )
    ):
        x = inner[0] + dx * route_fraction + px * side_offset_m
        y = inner[1] + dy * route_fraction + py * side_offset_m
        z = inner[2] + (outer[2] - inner[2]) * route_fraction
        radius_z = 0.62 + 0.28 * scale
        understory_centers.append((x, y, z))
        assembler.add_ellipsoid(
            (x, y, z + radius_z),
            (
                0.82 + 0.22 * scale,
                0.68 + 0.18 * scale,
                radius_z,
            ),
            segments=8,
            rings=4,
            yaw=math.radians(part["orientation_deg"] + index * 23.0),
            material_index=1,
        )
    route_clearances = [
        abs(
            dy * x_m
            - dx * y_m
            + outer[0] * inner[1]
            - outer[1] * inner[0]
        )
        / route_length_xy
        for x_m, y_m, _z_m in assembler.vertices
    ]
    minimum_geometry_route_clearance_m = min(route_clearances)
    if minimum_geometry_route_clearance_m < 4.0:
        raise RuntimeBuildError(
            "vegetation geometry enters the bidirectional route clearance"
        )
    minimum_crown_trunk_overlap_m = min(crown_trunk_overlaps_m)
    if minimum_crown_trunk_overlap_m < 0.25:
        raise RuntimeBuildError("vegetation crown is detached from its trunk")
    return GeometryResult(
        assembler,
        {
            "bounds": assembler.bounds(),
            "trunk_centers_m": trunk_centers,
            "understory_centers_m": understory_centers,
            "crown_primitive": "low-poly-ellipsoid-lobes",
            "crown_lobe_count": crown_lobe_count,
            "understory_cluster_count": len(understory_centers),
            "minimum_crown_trunk_overlap_m": minimum_crown_trunk_overlap_m,
            "material_slots": VEGETATION_MATERIAL_ALIASES,
            "minimum_trunk_center_route_clearance_m": 6.0,
            "minimum_geometry_route_clearance_m": (
                minimum_geometry_route_clearance_m
            ),
            "minimum_route_clearance_m": minimum_geometry_route_clearance_m,
        },
    )


GEOMETRY_BUILDERS = {
    "terrain-contact": _build_terrain_contact,
    "bidirectional-corridor": _build_bidirectional_corridor,
    "support-retaining": _build_support_retaining,
    "drainage-water": _build_drainage_water,
    "boundary-seam": _build_boundary_seam,
    "vegetation-enclosure": _build_vegetation_enclosure,
}


def _endpoint_gap_m(a, b):
    return math.dist(tuple(float(value) for value in a), tuple(float(value) for value in b))


def _endpoint_gap_xy_m(a, b):
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _contact_gap_m(supported_bounds, terrain_bounds):
    a = tuple(float(value) for value in supported_bounds)
    b = tuple(float(value) for value in terrain_bounds)
    if len(a) != 6 or len(b) != 6 or not all(
        math.isfinite(value) for value in (*a, *b)
    ):
        raise RuntimeBuildError("contact bounds must be finite AABBs")
    dx = max(a[0] - b[3], b[0] - a[3], 0.0)
    dy = max(a[1] - b[4], b[1] - a[4], 0.0)
    dz = max(a[2] - b[5], b[2] - a[5], 0.0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _validate_sector_geometry(
    module_evidence,
    plan_module,
    *,
    max_contact_gap_m=0.05,
    max_corridor_gap_m=0.1,
    max_drainage_gap_m=0.1,
    max_seam_gap_m=0.2,
):
    try:
        contact = _contact_gap_m(
            module_evidence["support-retaining"]["bounds"],
            module_evidence["terrain-contact"]["bounds"],
        )
        corridor = max(
            _endpoint_gap_m(
                module_evidence["bidirectional-corridor"]["inner_endpoint_m"],
                plan_module["inner_anchor_m"],
            ),
            _endpoint_gap_m(
                module_evidence["bidirectional-corridor"]["outer_endpoint_m"],
                plan_module["outer_anchor_m"],
            ),
        )
        drainage = max(
            _endpoint_gap_m(
                module_evidence["drainage-water"]["inner_endpoint_m"],
                plan_module["inner_anchor_m"],
            ),
            _endpoint_gap_m(
                module_evidence["drainage-water"]["outer_endpoint_m"],
                plan_module["outer_anchor_m"],
            ),
        )
        previous = _endpoint_gap_m(
            module_evidence["boundary-seam"]["previous_endpoint_m"],
            plan_module["previous_seam_m"],
        )
        following = _endpoint_gap_m(
            module_evidence["boundary-seam"]["next_endpoint_m"],
            plan_module["next_seam_m"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeBuildError("sector geometry evidence is incomplete") from exc
    if contact > max_contact_gap_m:
        raise RuntimeBuildError(
            f"{plan_module['module_id']} terrain/support contact gap is {contact}"
        )
    if corridor > max_corridor_gap_m:
        raise RuntimeBuildError(
            f"{plan_module['module_id']} corridor endpoint gap is {corridor}"
        )
    if drainage > max_drainage_gap_m:
        raise RuntimeBuildError(
            f"{plan_module['module_id']} drainage endpoint gap is {drainage}"
        )
    if max(previous, following) > max_seam_gap_m:
        raise RuntimeBuildError(
            f"{plan_module['module_id']} sector seam gap exceeds tolerance"
        )
    return {
        "module_id": plan_module["module_id"],
        "terrain_support_contact_gap_m": round(contact, 6),
        "corridor_endpoint_gap_m": round(corridor, 6),
        "drainage_endpoint_gap_m": round(drainage, 6),
        "previous_seam_gap_m": round(previous, 6),
        "next_seam_gap_m": round(following, 6),
        "previous_seam_actual_m": tuple(
            module_evidence["boundary-seam"]["previous_endpoint_m"]
        ),
        "next_seam_actual_m": tuple(
            module_evidence["boundary-seam"]["next_endpoint_m"]
        ),
    }


def _validate_neighbor_seams(module_results, *, max_gap_m):
    if len(module_results) < 2:
        raise RuntimeBuildError("neighbor seam validation needs at least two modules")
    gaps = []
    for index, current in enumerate(module_results):
        following = module_results[(index + 1) % len(module_results)]
        gap = _endpoint_gap_m(
            current["next_seam_actual_m"],
            following["previous_seam_actual_m"],
        )
        if gap > max_gap_m:
            raise RuntimeBuildError(
                f"neighbor seam gap {current['module_id']} -> "
                f"{following['module_id']} is {gap}"
            )
        gaps.append(round(gap, 6))
    return tuple(gaps)


def _tag_root(obj, row, module, part):
    obj["nv_root"] = True
    obj["nv_stable_id"] = row["object_id"]
    obj["nv_instance_id"] = row["instance_id"]
    obj["nv_semantic_id"] = row["semantic_id"]
    obj["nv_material_id"] = row["material_id"]
    obj["nv_variant_id"] = ""
    obj["nv_stage"] = "modeled-unverified"
    obj["nv_geometry_usability"] = "preview-only"
    obj["nv_trust_effect"] = "none-quality-filter-only"
    obj["nv_module_id"] = module["module_id"]
    obj["nv_semantic_role"] = part["semantic_role"]
    obj.pass_index = row["instance_id"]


def _tag_mesh(obj, row):
    obj["nv_stable_id"] = row["object_id"]
    obj["nv_root_id"] = row["object_id"]
    obj["nv_instance_id"] = row["instance_id"]
    obj["nv_semantic_id"] = row["semantic_id"]
    obj["nv_material_id"] = row["material_id"]
    obj["nv_variant_id"] = ""
    obj["nv_stage"] = "modeled-unverified"
    obj["nv_geometry_usability"] = "preview-only"
    obj["nv_trust_effect"] = "none-quality-filter-only"
    obj.pass_index = row["instance_id"]


def _material_contract(material):
    policy = material.get("uv_policy")
    tile_m = material.get("nv_nominal_tile_m")
    color_input = material.get("nv_surface_color_input")
    if (
        policy not in UV_POLICIES
        or isinstance(tile_m, bool)
        or not isinstance(tile_m, (int, float))
        or not math.isfinite(tile_m)
        or tile_m <= 0.0
        or color_input != "nv_surface_color"
    ):
        raise RuntimeBuildError("overlay material contract is invalid")
    return policy, float(tile_m), color_input


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
                * (values[index + 1][0] - first[0])
            )
            for index in range(1, len(values) - 1)
        ),
        default=0.0,
    )


def _dominant_projection_axes(normal):
    dominant = max(range(3), key=lambda index: abs(normal[index]))
    return tuple(index for index in range(3) if index != dominant)


def _assign_material_contract(obj, materials):
    contracts = tuple(_material_contract(material) for material in materials)
    if not contracts:
        raise RuntimeBuildError("overlay face material contract is invalid")
    layer_names = {contract[2] for contract in contracts}
    if layer_names != {"nv_surface_color"}:
        raise RuntimeBuildError("overlay surface color inputs disagree")
    layer_name = "nv_surface_color"
    mesh = obj.data
    uv_layer = mesh.uv_layers.get("nv_uv0") or mesh.uv_layers.new(name="nv_uv0")
    for polygon in mesh.polygons:
        if polygon.material_index >= len(contracts):
            raise RuntimeBuildError("overlay polygon material index is invalid")
        tile_m = contracts[polygon.material_index][1]
        axes = _dominant_projection_axes(polygon.normal)
        coordinates = [mesh.vertices[index].co for index in polygon.vertices]
        values = [
            (
                float(coordinate[axes[0]]) / tile_m,
                float(coordinate[axes[1]]) / tile_m,
            )
            for coordinate in coordinates
        ]
        if _polygon_uv_area(values) <= 1e-12:
            raise RuntimeBuildError(f"overlay UV projection is degenerate: {obj.name}")
        for loop_index, uv in zip(polygon.loop_indices, values, strict=True):
            uv_layer.data[loop_index].uv = uv
    color_layer = mesh.color_attributes.get(layer_name)
    if color_layer is None:
        color_layer = mesh.color_attributes.new(
            name=layer_name,
            type="FLOAT_COLOR",
            domain="CORNER",
        )
    if len(color_layer.data) != len(mesh.loops):
        raise RuntimeBuildError("overlay surface color layer is invalid")
    mesh.color_attributes.active_color = color_layer
    color_index = tuple(mesh.color_attributes).index(color_layer)
    mesh.color_attributes.active_color_index = color_index
    mesh.color_attributes.render_color_index = color_index
    for value in color_layer.data:
        value.color = (1.0, 1.0, 1.0, 1.0)
    try:
        mesh.calc_tangents(uvmap="nv_uv0")
    except Exception as exc:
        raise RuntimeBuildError(
            f"overlay tangent generation failed: {obj.name}"
        ) from exc
    obj["nv_uv_layer"] = "nv_uv0"
    obj["nv_tangents"] = True
    obj["nv_material_contract"] = "textured-pbr-v1"


def _link_geometry(root, result, materials, row, collection):
    materials = tuple(materials)
    if (
        not materials
        or len(result.assembler.face_material_indices)
        != len(result.assembler.faces)
        or max(result.assembler.face_material_indices, default=-1)
        >= len(materials)
    ):
        raise RuntimeBuildError("overlay geometry material assignment is invalid")
    mesh = bpy.data.meshes.new(f"m__{row['object_id']}")
    mesh.from_pydata(result.assembler.vertices, [], result.assembler.faces)
    mesh.update()
    if not mesh.vertices or not mesh.polygons:
        raise RuntimeBuildError(f"overlay mesh is empty: {row['object_id']}")
    obj = bpy.data.objects.new(f"mesh__{row['object_id']}", mesh)
    collection.objects.link(obj)
    obj.parent = root
    _tag_mesh(obj, row)
    for material in materials:
        mesh.materials.append(material)
    for polygon, material_index in zip(
        mesh.polygons,
        result.assembler.face_material_indices,
        strict=True,
    ):
        polygon.material_index = material_index
    obj["nv_material_slots"] = json.dumps(
        [material.get("nv_slot_id") for material in materials],
        separators=(",", ":"),
    )
    _assign_material_contract(obj, materials)
    return obj


def _root_signature(obj):
    return (
        obj.get("nv_stable_id"),
        obj.get("nv_instance_id"),
        obj.get("nv_semantic_id"),
        obj.get("nv_material_id"),
        obj.get("nv_variant_id") or None,
    )


def _validate_base_scene(request):
    blend_path = Path(bpy.data.filepath)
    if (
        not blend_path.is_absolute()
        or blend_path.is_symlink()
        or not blend_path.is_file()
        or _sha256_file(blend_path) != request["base_blend_sha256"]
    ):
        raise RuntimeBuildError("loaded Blender scene is not the bound exact-218 artifact")
    roots = [obj for obj in bpy.data.objects if obj.get("nv_root") is True]
    if len(roots) != EXPECTED_BASE_ROOTS:
        raise RuntimeBuildError("base scene canonical root count is not 218")
    by_id = {obj.get("nv_stable_id"): obj for obj in roots}
    if len(by_id) != EXPECTED_BASE_ROOTS:
        raise RuntimeBuildError("base scene canonical IDs are not unique")
    signatures = {}
    for row in request["object_registry"][:EXPECTED_BASE_ROOTS]:
        obj = by_id.get(row["object_id"])
        if obj is None or _root_signature(obj) != (
            row["object_id"],
            row["instance_id"],
            row["semantic_id"],
            row["material_id"],
            row["variant_id"],
        ):
            raise RuntimeBuildError("base scene registry disagrees with request")
        signatures[row["object_id"]] = _root_signature(obj)
    return roots, signatures


def _build_overlay(request):
    if bpy.data.collections.get(COLLECTION_NAME) is not None:
        raise RuntimeBuildError("perimeter-closure collection already exists")
    collection = bpy.data.collections.new(COLLECTION_NAME)
    bpy.context.scene.collection.children.link(collection)
    bindings = {
        row["material_alias"]: row for row in request["material_bindings"]
    }
    registry = {
        row["object_id"]: row
        for row in request["object_registry"][EXPECTED_BASE_ROOTS:]
    }
    roots = []
    meshes = []
    measurements = []
    for module in request["perimeter_closure_plan"]["modules"]:
        evidence = {}
        for part in module["parts"]:
            row = registry[part["part_id"]]
            material_aliases = (
                VEGETATION_MATERIAL_ALIASES
                if part["semantic_role"] == "vegetation-enclosure"
                else (part["material_slot_id"],)
            )
            materials = []
            for alias in material_aliases:
                binding = bindings[alias]
                material = bpy.data.materials.get(
                    f"nv__mat-{binding['runtime_slot_id']}"
                )
                if (
                    material is None
                    or material.get("nv_slot_id")
                    != binding["runtime_slot_id"]
                ):
                    raise RuntimeBuildError(
                        f"verified runtime material is absent: "
                        f"{binding['runtime_slot_id']}"
                    )
                materials.append(material)
            root = bpy.data.objects.new(f"nv__{part['part_id']}", None)
            root.empty_display_size = 0.05
            collection.objects.link(root)
            _tag_root(root, row, module, part)
            result = GEOMETRY_BUILDERS[part["semantic_role"]](part, collection)
            mesh = _link_geometry(
                root,
                result,
                materials,
                row,
                collection,
            )
            roots.append(root)
            meshes.append(mesh)
            evidence[part["semantic_role"]] = result.evidence
        measurements.append(
            _validate_sector_geometry(
                evidence,
                module,
                max_contact_gap_m=request[
                    "max_terrain_support_contact_gap_m"
                ],
                max_corridor_gap_m=request["max_corridor_endpoint_gap_m"],
                max_drainage_gap_m=request["max_drainage_endpoint_gap_m"],
                max_seam_gap_m=request["max_sector_seam_gap_m"],
            )
        )
    _validate_neighbor_seams(
        measurements,
        max_gap_m=request["max_sector_seam_gap_m"],
    )
    return roots, meshes, measurements


def _validate_built_overlay(
    request,
    base_signatures,
    overlay_roots,
    overlay_meshes,
):
    all_roots = [obj for obj in bpy.data.objects if obj.get("nv_root") is True]
    if (
        len(all_roots) != EXPECTED_TOTAL_ROOTS
        or len(overlay_roots) != EXPECTED_OVERLAY_ROOTS
        or len(overlay_meshes) != EXPECTED_OVERLAY_ROOTS
    ):
        raise RuntimeBuildError("combined canonical root count is not exact 266")
    by_id = {obj.get("nv_stable_id"): obj for obj in all_roots}
    if set(by_id) != {
        row["object_id"] for row in request["object_registry"]
    }:
        raise RuntimeBuildError("combined canonical root registry is not exact")
    for object_id, signature in base_signatures.items():
        if _root_signature(by_id[object_id]) != signature:
            raise RuntimeBuildError("base canonical root changed during overlay build")
    bindings = {
        row["material_alias"]: row for row in request["material_bindings"]
    }
    parts_by_id = {
        part["part_id"]: part
        for module in request["perimeter_closure_plan"]["modules"]
        for part in module["parts"]
    }
    for root, mesh in zip(overlay_roots, overlay_meshes, strict=True):
        part = parts_by_id[root.get("nv_stable_id")]
        expected_aliases = (
            VEGETATION_MATERIAL_ALIASES
            if part["semantic_role"] == "vegetation-enclosure"
            else (part["material_slot_id"],)
        )
        expected_slots = tuple(
            bindings[alias]["runtime_slot_id"] for alias in expected_aliases
        )
        actual_slots = tuple(
            material.get("nv_slot_id") for material in mesh.data.materials
        )
        if (
            root.get("nv_stage") != "modeled-unverified"
            or root.get("nv_trust_effect") != "none-quality-filter-only"
            or root.get("nv_geometry_usability") != "preview-only"
            or mesh.parent is not root
            or mesh.get("nv_stable_id") != root.get("nv_stable_id")
            or mesh.get("nv_instance_id") != root.get("nv_instance_id")
            or mesh.get("nv_semantic_id") != root.get("nv_semantic_id")
            or mesh.get("nv_material_id") != root.get("nv_material_id")
            or mesh.get("nv_tangents") is not True
            or mesh.get("nv_material_contract") != "textured-pbr-v1"
            or not mesh.data.vertices
            or not mesh.data.polygons
            or actual_slots != expected_slots
            or mesh.get("nv_material_slots")
            != json.dumps(expected_slots, separators=(",", ":"))
        ):
            raise RuntimeBuildError(
                f"overlay structural evidence is invalid: "
                f"{root.get('nv_stable_id')}"
            )
        if any(
            not all(math.isfinite(value) for value in vertex.co)
            for vertex in mesh.data.vertices
        ):
            raise RuntimeBuildError("overlay mesh contains non-finite vertices")
    bpy.context.scene["nv_perimeter_closure_build"] = json.dumps(
        {
            "build_id": request["build_id"],
            "canonical_roots": EXPECTED_TOTAL_ROOTS,
            "geometry_usability": "preview-only",
            "overlay_roots": EXPECTED_OVERLAY_ROOTS,
            "stage": "modeled-unverified",
            "trust_effect": "none-quality-filter-only",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _material_counts(meshes):
    textured = valid_uv = valid_surface = 0
    for obj in meshes:
        if not obj.data.materials:
            continue
        for material in obj.data.materials:
            _material_contract(material)
        textured += 1
        uv_layer = obj.data.uv_layers.get("nv_uv0")
        if uv_layer is not None and all(
            _polygon_uv_area(
                [uv_layer.data[index].uv for index in polygon.loop_indices]
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
            valid_surface += 1
    return textured, valid_uv, valid_surface


def _write_report(request, staging_path, output_path, meshes, measurements):
    textured, valid_uv, valid_surface = _material_counts(meshes)
    if (textured, valid_uv, valid_surface) != (48, 48, 48):
        raise RuntimeBuildError("saved overlay material contracts are incomplete")
    report_measurements = [
        {
            key: value
            for key, value in measurement.items()
            if key
            in {
                "module_id",
                "terrain_support_contact_gap_m",
                "corridor_endpoint_gap_m",
                "drainage_endpoint_gap_m",
                "previous_seam_gap_m",
                "next_seam_gap_m",
            }
        }
        for measurement in measurements
    ]
    report = {
        "schema_version": REPORT_SCHEMA,
        "build_id": request["build_id"],
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none-quality-filter-only",
        "base_build_id": request["base_build_id"],
        "base_build_request_sha256": request["base_build_request_sha256"],
        "base_build_report_sha256": request["base_build_report_sha256"],
        "base_blend_sha256": request["base_blend_sha256"],
        "base_object_registry_sha256": request["base_object_registry_sha256"],
        "base_reciprocal_route_module_plan_sha256": request[
            "base_reciprocal_route_module_plan_sha256"
        ],
        "blender_executable_sha256": request["blender_executable_sha256"],
        "runtime_script_sha256": request["runtime_script_sha256"],
        "batch24_manifest_sha256": request["batch24_manifest_sha256"],
        "perimeter_closure_plan_sha256": request[
            "perimeter_closure_plan_sha256"
        ],
        "material_bindings_sha256": request["material_bindings_sha256"],
        "object_registry": request["object_registry"],
        "material_bindings": request["material_bindings"],
        "counts": {
            "base_canonical_roots": EXPECTED_BASE_ROOTS,
            "overlay_canonical_roots": EXPECTED_OVERLAY_ROOTS,
            "canonical_roots": EXPECTED_TOTAL_ROOTS,
            "overlay_mesh_objects": len(meshes),
            "textured_overlay_meshes": textured,
            "valid_uv_overlay_meshes": valid_uv,
            "valid_surface_color_overlay_meshes": valid_surface,
        },
        "validation": {
            "base_registry_preserved": True,
            "overlay_registry_exact": True,
            "finite_nonempty_overlay_meshes": True,
            "material_bindings_exact": True,
            "design_sources_provenance_only": True,
            "terrain_support_contacts_passed": True,
            "corridor_continuity_passed": True,
            "drainage_continuity_passed": True,
            "sector_seams_passed": True,
        },
        "sector_measurements": report_measurements,
        "artifact": {
            "name": OUTPUT_NAME,
            "kind": "blender-scene",
            "sha256": _sha256_file(output_path),
            "size_bytes": output_path.stat().st_size,
        },
    }
    report_path = staging_path / REPORT_NAME
    with report_path.open("xb") as stream:
        stream.write(_canonical_bytes(report))
        stream.flush()
        os.fsync(stream.fileno())


def main():
    request_path, staging_path = _runtime_paths(sys.argv)
    request = _validate_request(_load_request(request_path))
    _base_roots, base_signatures = _validate_base_scene(request)
    overlay_roots, overlay_meshes, measurements = _build_overlay(request)
    _validate_built_overlay(
        request,
        base_signatures,
        overlay_roots,
        overlay_meshes,
    )
    output_path = staging_path / OUTPUT_NAME
    if output_path.exists() or (staging_path / REPORT_NAME).exists():
        raise RuntimeBuildError("perimeter-closure outputs already exist")
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), check_existing=False)
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeBuildError("perimeter-closure Blender scene did not save")
    _write_report(
        request,
        staging_path,
        output_path,
        overlay_meshes,
        measurements,
    )
    print(
        "NANTAI_PERIMETER_CLOSURE_BUILD="
        + json.dumps(
            {
                "build_id": request["build_id"],
                "canonical_roots": EXPECTED_TOTAL_ROOTS,
                "overlay_roots": EXPECTED_OVERLAY_ROOTS,
                "stage": "modeled-unverified",
                "trust_effect": "none-quality-filter-only",
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
        print(f"NANTAI_PERIMETER_CLOSURE_ERROR {exc}", flush=True)
        raise SystemExit(2) from exc
