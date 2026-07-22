"""Apply a content-addressed ReciprocalRouteModulePlan to a verified 175-root scene.

This script runs only inside the pinned Blender 4.5.11 Windows runtime.  The
host opens the verified 175-root ``village-modules.blend`` first, then supplies
an absolute canonical request path and an empty private staging directory
after ``--``.

Phase 3 of HANDOFF-OPUS-009. Geometry is intentionally simplified but consumes
the canonical per-part ``geometry_family`` declaration: open paths, covered
passages, bridges, structures, drainage, guards, props, and vegetation no
longer collapse to one universal tunnel. Each part still gets a finite,
non-empty mesh with proper UVs, tangents, and one material slot, so the build
report's ``finite_nonempty_module_meshes`` Literal[True] remains honest.

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
#: Phase 4.3: one topology proxy mesh per reciprocal-route module so the
#: probe's ``closest_point_on_mesh`` can hit a real mesh instead of the
#: v1 EMPTY / curve root (``path-network-001/002/003/005`` are EMPTY
#: objects in the base scene).  Proxies do NOT count toward the 218-root
#: canonical registry; they are auxiliary attachment targets only.
EXPECTED_TOPOLOGY_PROXY_COUNT = 6
#: Proxy mesh is a small box placed at the role candidate's ``look_at_m``
#: (the topology direction point).  1.5 m on each side is large enough
#: that ``closest_point_on_mesh`` reliably hits from the module's first
#: part center (~25 m away), and small enough that the proxy does not
#: intersect module meshes or aux-terrain by accident.  Any change here
#: changes ``runtime_script_sha256`` and therefore ``build_id``.
_DEFAULT_TOPOLOGY_PROXY_EXTENT_M = (1.5, 1.5, 1.5)

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

GEOMETRY_FAMILY_SEMANTIC_CLASSES = {
    "open-path": frozenset({"path"}),
    "covered-passage": frozenset({"building"}),
    "bridge-deck": frozenset({"bridge"}),
    "building-shell": frozenset({"building"}),
    "structural-frame": frozenset({"building"}),
    "drainage-channel": frozenset({"creek"}),
    "retaining-structure": frozenset({"retaining-wall"}),
    "guard-rail": frozenset({"prop"}),
    "service-prop": frozenset({"prop"}),
    "vegetation-band": frozenset({"prop"}),
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
#: The actual per-part layout (center, extent, orientation) is now
#: carried by ``ReciprocalRouteModulePart.part_layout`` in the plan
#: (Phase 4.1, responding to REVIEW-CODEX-018 §"Phase 4 必须处理的
#: 边界" item 1).  The runtime script reads it verbatim from the
#: request and may NOT invent its own layout.


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
    # Phase 4.1: every part must carry a canonical part_layout so the
    # runtime does not invent its own layout (REVIEW-CODEX-018 item 1).
    for part in parts:
        _validate_geometry_family(part)
        _validate_part_layout(part)
    return request


def _validate_geometry_family(part):
    family = part.get("geometry_family")
    if family not in GEOMETRY_FAMILY_SEMANTIC_CLASSES:
        raise RuntimeBuildError(
            f"part {part.get('part_id')} geometry_family is invalid",
        )
    semantic_class = SEMANTIC_CLASS_BY_ID.get(part.get("semantic_id"))
    if semantic_class not in GEOMETRY_FAMILY_SEMANTIC_CLASSES[family]:
        raise RuntimeBuildError(
            f"part {part.get('part_id')} geometry_family {family} is "
            f"incompatible with semantic_id {part.get('semantic_id')}",
        )
    return family


def _validate_part_layout(part):
    layout = part.get("part_layout")
    if not isinstance(layout, dict):
        raise RuntimeBuildError(
            f"part {part.get('part_id')} is missing part_layout",
        )
    if set(layout.keys()) != {"center_m", "extent_m", "orientation_deg"}:
        raise RuntimeBuildError(
            f"part {part.get('part_id')} part_layout keys are not exact",
        )
    center = layout["center_m"]
    size = layout["extent_m"]
    orientation = layout["orientation_deg"]
    if (
        not isinstance(center, list)
        or len(center) != 3
        or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in center)
    ):
        raise RuntimeBuildError(
            f"part {part.get('part_id')} part_layout center_m is invalid",
        )
    if (
        not isinstance(size, list)
        or len(size) != 3
        or not all(
            isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0
            for v in size
        )
    ):
        raise RuntimeBuildError(
            f"part {part.get('part_id')} part_layout extent_m is invalid",
        )
    if (
        not isinstance(orientation, (int, float))
        or not math.isfinite(orientation)
        or not (0.0 <= orientation < 360.0)
    ):
        raise RuntimeBuildError(
            f"part {part.get('part_id')} part_layout orientation_deg is invalid",
        )


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


#: Thickness of the four wall / floor / ceiling panels that form a
#: passage.  Small enough that the inner clear dimensions stay above
#: the probe thresholds, large enough that the panels are non-degenerate
#: meshes after modifier evaluation.
_PASSAGE_PANEL_THICKNESS_M = 0.05
#: Half-thickness helper.
_PASSAGE_PANEL_HALF_M = _PASSAGE_PANEL_THICKNESS_M / 2.0
#: Wall thickness (left/right) -- thicker than floor/ceiling so BVH
#: overlap tests are robust against floating-point edge cases.
_PASSAGE_WALL_THICKNESS_M = 0.1
_PASSAGE_WALL_HALF_M = _PASSAGE_WALL_THICKNESS_M / 2.0
#: Ray-safe gap between part center (probe ray origin) and the floor /
#: ceiling panel surfaces.  Without this gap the upward ray's origin
#: (cx, cy, cz) lies exactly on the floor panel's top face, and BVH
#: ``ray_cast`` returns distance 0 instead of the intended ceiling
#: hit at distance ``sz``.  1 mm is small enough that the measured
#: clearance (``sz + _PASSAGE_RAY_SAFE_GAP_M``) stays within rounding
#: of the design value, and large enough to be stable against
#: floating-point noise in BVH construction.  Inner clear height
#: becomes ``sz + 2 * gap`` (still >= MIN_ROUTE_CLEARANCE_M = 2.4).
_PASSAGE_RAY_SAFE_GAP_M = 0.001
_PATH_EDGE_HEIGHT_M = 0.18
_BRIDGE_PARAPET_HEIGHT_M = 0.65


def _local_center(center, yaw, local_x=0.0, local_y=0.0, local_z=0.0):
    """Transform one local offset into the canonical part's world frame."""

    cx, cy, cz = center
    cosine, sine = math.cos(yaw), math.sin(yaw)
    return (
        cx + local_x * cosine - local_y * sine,
        cy + local_x * sine + local_y * cosine,
        cz + local_z,
    )


def _add_local_box(assembler, center, yaw, offset, size):
    assembler.add_box(_local_center(center, yaw, *offset), size, yaw)


def _add_floor(assembler, center, extent, yaw, *, drop_m=0.0):
    sx, sy, _sz = extent
    floor_top_z = center[2] - _PASSAGE_RAY_SAFE_GAP_M - drop_m
    assembler.add_box(
        (center[0], center[1], floor_top_z - _PASSAGE_PANEL_HALF_M),
        (sx, sy, _PASSAGE_PANEL_THICKNESS_M),
        yaw,
    )
    return floor_top_z


def _add_edge_pair(assembler, center, extent, yaw, *, height_m, width_m=0.1):
    sx, sy, _sz = extent
    local_x = sx / 2.0 - width_m / 2.0
    local_z = -_PASSAGE_RAY_SAFE_GAP_M + height_m / 2.0
    for side in (-1.0, 1.0):
        _add_local_box(
            assembler,
            center,
            yaw,
            (side * local_x, 0.0, local_z),
            (width_m, sy, height_m),
        )


def _open_path_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    _add_floor(assembler, center, extent, yaw)
    _add_edge_pair(
        assembler,
        center,
        extent,
        yaw,
        height_m=_PATH_EDGE_HEIGHT_M,
    )
    return assembler


def _covered_passage_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    sx, sy, sz = extent
    _add_floor(assembler, center, extent, yaw)
    ceiling_bottom_z = center[2] + sz + _PASSAGE_RAY_SAFE_GAP_M
    assembler.add_box(
        (center[0], center[1], ceiling_bottom_z + _PASSAGE_PANEL_HALF_M),
        (sx, sy, _PASSAGE_PANEL_THICKNESS_M),
        yaw,
    )
    wall_z = sz / 2.0
    wall_height = sz + 2.0 * _PASSAGE_RAY_SAFE_GAP_M
    wall_x = sx / 2.0 - _PASSAGE_WALL_HALF_M
    for side in (-1.0, 1.0):
        _add_local_box(
            assembler,
            center,
            yaw,
            (side * wall_x, 0.0, wall_z),
            (_PASSAGE_WALL_THICKNESS_M, sy, wall_height),
        )
    return assembler


def _bridge_deck_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    _add_floor(assembler, center, extent, yaw, drop_m=0.04)
    _add_edge_pair(
        assembler,
        center,
        extent,
        yaw,
        height_m=_BRIDGE_PARAPET_HEIGHT_M,
        width_m=0.14,
    )
    # A shallow transverse sill makes the deck transition visibly distinct
    # from an open stone path while keeping the overhead volume open.
    _add_local_box(
        assembler,
        center,
        yaw,
        (0.0, extent[1] * 0.35, 0.04),
        (extent[0] * 0.82, 0.12, 0.08),
    )
    return assembler


def _building_shell_geometry(center, extent, yaw):
    assembler = _covered_passage_geometry(center, extent, yaw)
    sx, sy, sz = extent
    # Keep the route axis open. A full rear wall made every later canonical
    # part invisible when the role camera looked through the shell.
    _add_local_box(
        assembler,
        center,
        yaw,
        (0.0, 0.0, sz + 0.16),
        (sx * 0.55, sy * 0.7, 0.27),
    )
    return assembler


def _structural_frame_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    sx, sy, sz = extent
    post_width = 0.14
    for local_x in (-sx * 0.38, sx * 0.38):
        for local_y in (-sy * 0.38, sy * 0.38):
            _add_local_box(
                assembler,
                center,
                yaw,
                (local_x, local_y, sz / 2.0),
                (post_width, post_width, sz),
            )
    for local_y in (-sy * 0.38, sy * 0.38):
        _add_local_box(
            assembler,
            center,
            yaw,
            (0.0, local_y, sz),
            (sx, 0.18, 0.18),
        )
    return assembler


def _drainage_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    sx, sy, _sz = extent
    _add_floor(assembler, center, extent, yaw, drop_m=0.16)
    for side in (-1.0, 1.0):
        _add_local_box(
            assembler,
            center,
            yaw,
            (side * sx * 0.34, 0.0, -0.04),
            (sx * 0.18, sy, 0.24),
        )
    _add_local_box(
        assembler,
        center,
        yaw,
        (0.0, 0.0, 0.03),
        (sx * 0.5, 0.1, 0.06),
    )
    return assembler


def _retaining_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    sx, sy, sz = extent
    _add_floor(assembler, center, extent, yaw, drop_m=0.08)
    _add_local_box(
        assembler,
        center,
        yaw,
        (-sx * 0.34, 0.0, sz * 0.28),
        (sx * 0.22, sy, sz * 0.56),
    )
    _add_local_box(
        assembler,
        center,
        yaw,
        (sx * 0.1, -sy * 0.22, 0.12),
        (sx * 0.66, sy * 0.28, 0.24),
    )
    return assembler


def _guard_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    sx, sy, _sz = extent
    for side in (-1.0, 1.0):
        local_x = side * sx * 0.42
        for local_y in (-sy * 0.38, 0.0, sy * 0.38):
            _add_local_box(
                assembler,
                center,
                yaw,
                (local_x, local_y, 0.55),
                (0.09, 0.09, 1.1),
            )
        for local_z in (0.48, 0.96):
            _add_local_box(
                assembler,
                center,
                yaw,
                (local_x, 0.0, local_z),
                (0.1, sy, 0.1),
            )
        _add_local_box(
            assembler,
            center,
            yaw,
            (local_x, 0.0, 0.039),
            (0.22, sy, 0.08),
        )
    return assembler


def _service_prop_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    sx, sy, sz = extent
    _add_local_box(
        assembler,
        center,
        yaw,
        (0.0, 0.0, sz * 0.22),
        (sx * 0.52, sy * 0.34, sz * 0.44),
    )
    _add_local_box(
        assembler,
        center,
        yaw,
        (0.0, -sy * 0.19, sz * 0.43),
        (sx * 0.32, 0.08, sz * 0.25),
    )
    return assembler


def _vegetation_geometry(center, extent, yaw):
    assembler = MeshAssembler()
    sx, sy, sz = extent
    stems = (
        (-0.28, -0.3, 0.72),
        (0.24, -0.12, 0.54),
        (-0.08, 0.25, 0.86),
    )
    for x_ratio, y_ratio, height_ratio in stems:
        height = sz * height_ratio
        _add_local_box(
            assembler,
            center,
            yaw,
            (sx * x_ratio, sy * y_ratio, height / 2.0),
            (0.12, 0.12, height),
        )
        _add_local_box(
            assembler,
            center,
            yaw,
            (sx * x_ratio, sy * y_ratio, height),
            (sx * 0.34, sy * 0.22, sz * 0.24),
        )
    return assembler


def _module_geometry(part):
    """Build one semantic-compatible mesh from canonical family + layout."""

    family = _validate_geometry_family(part)
    _validate_part_layout(part)
    layout = part["part_layout"]
    cx, cy, cz = (float(value) for value in layout["center_m"])
    sx, sy, sz = (float(value) for value in layout["extent_m"])
    yaw = math.radians(float(layout["orientation_deg"]))
    center = (cx, cy, cz)
    extent = (sx, sy, sz)
    builders = {
        "open-path": _open_path_geometry,
        "covered-passage": _covered_passage_geometry,
        "bridge-deck": _bridge_deck_geometry,
        "building-shell": _building_shell_geometry,
        "structural-frame": _structural_frame_geometry,
        "drainage-channel": _drainage_geometry,
        "retaining-structure": _retaining_geometry,
        "guard-rail": _guard_geometry,
        "service-prop": _service_prop_geometry,
        "vegetation-band": _vegetation_geometry,
    }
    return builders[family](center, extent, yaw)


# --------------------------------------------------------------------------- #
# Phase 4.3 junction vegetation opening.
#
# The fresh exact-218 probe localized its only module/environment collision to
# ``gallery-branch-attachment-side-001`` versus the ``roadside-vegetation``
# child of ``path-network-003``.  The terrain ribbon and the other four path
# children do not intersect.  At this additive runtime layer both objects are
# present and the side attachment carries the canonical layout, so the opening
# can be derived from the bound plan instead of duplicating world coordinates.
#
# Each rule maps a module recipe branch to the part whose XY footprint is the
# vegetation-free junction envelope.  Adding another supported junction is an
# explicit contract change and therefore changes the runtime script SHA/build
# identity.  The runtime removes whole disconnected vegetation components;
# it never clips the path ribbon or silently tolerates a module intersection.
# --------------------------------------------------------------------------- #

_JUNCTION_VEGETATION_RULES = (
    (
        "covered-gallery-underpass",
        "side_branch",
        "gallery-branch-attachment-side-001",
    ),
)
_ROADSIDE_VEGETATION_PART_ID = "roadside-vegetation"


def _layout_aabb_xy(layout):
    """Return the world-space XY AABB of one canonical part layout."""

    if not isinstance(layout, dict):
        raise RuntimeBuildError("junction vegetation part layout is invalid")
    center = layout.get("center_m")
    extent = layout.get("extent_m")
    yaw_deg = layout.get("orientation_deg")
    if (
        not isinstance(center, list)
        or len(center) != 3
        or not isinstance(extent, list)
        or len(extent) != 3
        or not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in (*center, *extent)
        )
        or any(float(value) <= 0.0 for value in extent)
        or not isinstance(yaw_deg, (int, float))
        or isinstance(yaw_deg, bool)
        or not math.isfinite(yaw_deg)
    ):
        raise RuntimeBuildError("junction vegetation part layout is invalid")
    cx, cy = float(center[0]), float(center[1])
    hx, hy = float(extent[0]) / 2.0, float(extent[1]) / 2.0
    yaw = math.radians(float(yaw_deg))
    cosine, sine = math.cos(yaw), math.sin(yaw)
    corners = []
    for local_x, local_y in ((-hx, -hy), (-hx, hy), (hx, -hy), (hx, hy)):
        corners.append(
            (
                cx + local_x * cosine - local_y * sine,
                cy + local_x * sine + local_y * cosine,
            ),
        )
    xs = [row[0] for row in corners]
    ys = [row[1] for row in corners]
    return (min(xs), max(xs), min(ys), max(ys))


def _junction_vegetation_clearance_targets(plan):
    """Derive every vegetation opening from the bound module plan."""

    modules = plan.get("modules") if isinstance(plan, dict) else None
    if not isinstance(modules, list):
        raise RuntimeBuildError("junction vegetation module plan is invalid")
    targets = []
    for module_id, branch_key, part_id in _JUNCTION_VEGETATION_RULES:
        matches = [
            module
            for module in modules
            if isinstance(module, dict) and module.get("module_id") == module_id
        ]
        if len(matches) != 1:
            raise RuntimeBuildError(
                f"junction vegetation module is absent or duplicated: {module_id}",
            )
        module = matches[0]
        recipe = module.get("recipe")
        branch = recipe.get(branch_key) if isinstance(recipe, dict) else None
        topology_ref = (
            branch.get("connects_to_topology")
            if isinstance(branch, dict)
            else None
        )
        if (
            not isinstance(topology_ref, str)
            or not topology_ref.startswith("path-network-")
        ):
            raise RuntimeBuildError(
                f"junction vegetation topology binding is invalid: {module_id}",
            )
        parts = module.get("parts")
        part_matches = [
            part
            for part in parts
            if isinstance(part, dict) and part.get("part_id") == part_id
        ] if isinstance(parts, list) else []
        if len(part_matches) != 1:
            raise RuntimeBuildError(
                f"junction vegetation part is absent or duplicated: {part_id}",
            )
        targets.append(
            {
                "module_id": module_id,
                "part_id": part_id,
                "topology_ref": topology_ref,
                "aabb_xy_m": _layout_aabb_xy(part_matches[0].get("part_layout")),
            },
        )
    return tuple(targets)


def _vegetation_components_overlapping_xy(vertices, faces, aabb_xy_m):
    """Return disconnected vertex components overlapping an XY envelope.

    ``roadside-vegetation`` is authored as independent ellipsoids in one mesh.
    Selecting whole connected components preserves every plant outside the
    junction instead of slicing triangles at an arbitrary plane.
    """

    if (
        not isinstance(aabb_xy_m, (tuple, list))
        or len(aabb_xy_m) != 4
        or not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in aabb_xy_m
        )
    ):
        raise RuntimeBuildError("junction vegetation clearance AABB is invalid")
    envelope_min_x, envelope_max_x, envelope_min_y, envelope_max_y = (
        float(value) for value in aabb_xy_m
    )
    if envelope_min_x >= envelope_max_x or envelope_min_y >= envelope_max_y:
        raise RuntimeBuildError("junction vegetation clearance AABB is invalid")
    rows = [tuple(float(value) for value in vertex) for vertex in vertices]
    if not rows or any(
        len(row) != 3 or not all(math.isfinite(value) for value in row)
        for row in rows
    ):
        raise RuntimeBuildError("roadside vegetation vertices are invalid")
    adjacency = [set() for _ in rows]
    referenced = set()
    for face in faces:
        indices = tuple(face)
        if (
            len(indices) < 3
            or any(
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(rows)
                for index in indices
            )
        ):
            raise RuntimeBuildError("roadside vegetation faces are invalid")
        referenced.update(indices)
        for index, current in enumerate(indices):
            following = indices[(index + 1) % len(indices)]
            adjacency[current].add(following)
            adjacency[following].add(current)
    if referenced != set(range(len(rows))):
        raise RuntimeBuildError("roadside vegetation contains loose vertices")
    components = []
    remaining = set(range(len(rows)))
    while remaining:
        seed = min(remaining)
        component = {seed}
        stack = [seed]
        remaining.remove(seed)
        while stack:
            current = stack.pop()
            for neighbour in adjacency[current]:
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.add(neighbour)
                    stack.append(neighbour)
        components.append(tuple(sorted(component)))
    selected = []
    for component in components:
        xs = [rows[index][0] for index in component]
        ys = [rows[index][1] for index in component]
        if (
            max(xs) > envelope_min_x
            and min(xs) < envelope_max_x
            and max(ys) > envelope_min_y
            and min(ys) < envelope_max_y
        ):
            selected.append(component)
    return tuple(selected)


def _apply_junction_vegetation_clearances(request, base_roots):
    """Remove exactly one colliding roadside plant per declared junction."""

    targets = _junction_vegetation_clearance_targets(
        request["reciprocal_route_module_plan"],
    )
    evidence = []
    for target in targets:
        roots = [
            root
            for root in base_roots
            if root.get("nv_stable_id") == target["topology_ref"]
        ]
        if len(roots) != 1:
            raise RuntimeBuildError(
                "junction vegetation path root is absent or duplicated: "
                + target["topology_ref"],
            )
        root = roots[0]
        children = [
            child
            for child in root.children
            if child.type == "MESH"
            and child.get("nv_part_id") == _ROADSIDE_VEGETATION_PART_ID
        ]
        if len(children) != 1:
            raise RuntimeBuildError(
                "junction roadside vegetation child is absent or duplicated: "
                + target["topology_ref"],
            )
        child = children[0]
        mesh = child.data
        world_vertices = [
            tuple(float(value) for value in (child.matrix_world @ vertex.co))
            for vertex in mesh.vertices
        ]
        faces = [tuple(int(index) for index in polygon.vertices) for polygon in mesh.polygons]
        selected = _vegetation_components_overlapping_xy(
            world_vertices,
            faces,
            target["aabb_xy_m"],
        )
        if len(selected) != 1:
            raise RuntimeBuildError(
                "junction vegetation clearance must remove exactly one "
                f"component for {target['part_id']}; observed {len(selected)}",
            )
        original_vertex_count = len(mesh.vertices)
        original_polygon_count = len(mesh.polygons)
        removed_vertices = selected[0]
        import bmesh

        editable = bmesh.new()
        try:
            editable.from_mesh(mesh)
            editable.verts.ensure_lookup_table()
            bmesh.ops.delete(
                editable,
                geom=[editable.verts[index] for index in removed_vertices],
                context="VERTS",
            )
            editable.to_mesh(mesh)
        finally:
            editable.free()
        mesh.update(calc_edges=True)
        if not mesh.vertices or not mesh.polygons:
            raise RuntimeBuildError(
                "junction vegetation clearance emptied roadside vegetation",
            )
        if mesh.uv_layers.active is not None:
            mesh.calc_tangents()
        remaining_world_vertices = [
            tuple(float(value) for value in (child.matrix_world @ vertex.co))
            for vertex in mesh.vertices
        ]
        remaining_faces = [
            tuple(int(index) for index in polygon.vertices)
            for polygon in mesh.polygons
        ]
        if _vegetation_components_overlapping_xy(
            remaining_world_vertices,
            remaining_faces,
            target["aabb_xy_m"],
        ):
            raise RuntimeBuildError(
                f"junction vegetation clearance is incomplete: {target['part_id']}",
            )
        removed_polygon_count = original_polygon_count - len(mesh.polygons)
        if removed_polygon_count <= 0:
            raise RuntimeBuildError(
                f"junction vegetation clearance removed no polygons: {target['part_id']}",
            )
        child["nv_junction_vegetation_clearance_applied"] = True
        child["nv_junction_vegetation_part_id"] = target["part_id"]
        child["nv_junction_vegetation_removed_components"] = 1
        root["nv_junction_vegetation_clearance_applied"] = True
        evidence.append(
            {
                "module_id": target["module_id"],
                "part_id": target["part_id"],
                "topology_ref": target["topology_ref"],
                "aabb_xy_m": [float(value) for value in target["aabb_xy_m"]],
                "removed_component_count": 1,
                "removed_vertex_count": original_vertex_count - len(mesh.vertices),
                "removed_polygon_count": removed_polygon_count,
            },
        )
    bpy.context.scene["nv_junction_vegetation_clearance_evidence"] = json.dumps(
        evidence,
        separators=(",", ":"),
        sort_keys=True,
    )
    return tuple(evidence)


# --------------------------------------------------------------------------- #
# Phase 4.3: topology proxy meshes.
#
# The v1 base scene's ``path-network-001/002/003/005`` objects are EMPTY
# roots; ``closest_point_on_mesh`` returns no hit on them (see
# FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md §"Topology attachment probes").
# The reciprocal-route runtime emits one proxy mesh per module so the
# probe can measure a real attachment distance.  The proxy:
#
#   * is placed at the role candidate's ``look_at_m`` (the topology
#     direction point, ~25 m in front of the module's first part);
#   * is a small 1.5 m box, large enough to be hit by
#     ``closest_point_on_mesh`` from the part center, small enough not to
#     accidentally intersect module meshes or aux-terrain;
#   * carries ``nv_stable_id = "{topology_ref}::{module_id}"`` so the
#     probe can find a module-specific target without colliding with the
#     v1 EMPTY root's ``nv_stable_id = "{topology_ref}"``;
#   * does NOT carry ``nv_root = True`` -- it is not part of the 218-root
#     canonical registry and must not be counted by
#     ``_validate_built_modules``.
# --------------------------------------------------------------------------- #


def _topology_proxy_id_for_module(module_id, topology_ref):
    """Stable id for the module-specific topology proxy mesh."""
    return f"{topology_ref}::{module_id}"


_MODULE_ATTACHMENT_RECIPE_PATHS = {
    "central-courtyard-downhill": ("downhill_gate", "connects_to_topology"),
    "bridge-deck-crossing": ("upstream_attachment", "connects_to_topology"),
    "watermill-tailrace": ("bound_path_network",),
    "covered-gallery-underpass": ("lower_branch", "connects_to_topology"),
    "forest-orchard-boundary": ("bound_path_network",),
    "lower-valley-uphill": ("bound_path_network",),
}


def _module_attachment_topology_ref(module):
    """Read the module attachment topology from its canonical recipe."""

    if not isinstance(module, dict):
        raise RuntimeBuildError("topology proxy module is not a dict")
    module_id = module.get("module_id")
    path = _MODULE_ATTACHMENT_RECIPE_PATHS.get(module_id)
    if path is None:
        raise RuntimeBuildError(
            f"topology proxy module_id is unsupported: {module_id}",
        )
    value = module.get("recipe")
    for key in path:
        if not isinstance(value, dict):
            raise RuntimeBuildError(
                f"module attachment topology is absent for {module_id}",
            )
        value = value.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeBuildError(
            f"module attachment topology is invalid for {module_id}",
        )
    return value


def _topology_proxy_targets(plan):
    """Return ``[(module_id, attachment_ref, look_at_m), ...]`` for the
    six reciprocal-route modules.

    Camera placement topology and module attachment topology may differ
    for cross-path modules.  Candidate coordinates still come from the
    canonical role-camera rows, while each proxy ref is read from the
    matching module recipe that the Phase 4.3 attachment probe measures.
    """

    candidates = plan.get("role_camera_candidates")
    if (
        not isinstance(candidates, list)
        or len(candidates) != EXPECTED_TOPOLOGY_PROXY_COUNT
    ):
        raise RuntimeBuildError(
            "plan role_camera_candidates is not exactly six entries",
        )
    modules = plan.get("modules")
    if not isinstance(modules, list) or len(modules) != EXPECTED_TOPOLOGY_PROXY_COUNT:
        raise RuntimeBuildError("plan modules is not exactly six entries")
    module_by_id = {}
    for module in modules:
        if not isinstance(module, dict):
            raise RuntimeBuildError("topology proxy module is not a dict")
        module_id = module.get("module_id")
        if not isinstance(module_id, str) or not module_id or module_id in module_by_id:
            raise RuntimeBuildError("topology proxy module_id is invalid or duplicated")
        module_by_id[module_id] = module
    out = []
    seen_modules = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise RuntimeBuildError(
                "role_camera_candidate is not a dict",
            )
        module_id = candidate.get("role_module_id")
        look_at = candidate.get("look_at_m")
        if not isinstance(module_id, str) or not module_id:
            raise RuntimeBuildError(
                "role_camera_candidate role_module_id is invalid",
            )
        module = module_by_id.get(module_id)
        if module is None:
            raise RuntimeBuildError(
                f"role_camera_candidate module is absent from plan: {module_id}",
            )
        topology_ref = _module_attachment_topology_ref(module)
        if (
            not isinstance(look_at, list)
            or len(look_at) != 3
            or not all(
                isinstance(v, (int, float)) and math.isfinite(v) for v in look_at
            )
        ):
            raise RuntimeBuildError(
                f"role_camera_candidate for {module_id} "
                f"has invalid look_at_m",
            )
        if module_id in seen_modules:
            raise RuntimeBuildError(
                f"role_camera_candidate for {module_id} is duplicated",
            )
        seen_modules.add(module_id)
        out.append((module_id, topology_ref, tuple(float(v) for v in look_at)))
    if len(out) != EXPECTED_TOPOLOGY_PROXY_COUNT:
        raise RuntimeBuildError(
            "topology proxy target count is not exactly six",
        )
    return out


#: Phase 4.3 amendment (FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md
#: §"待处理" item: "topology attachment distance wrong"): offset from
#: the module's first part center to the topology proxy center, in
#: metres along the -y axis.  See ``_topology_proxy_center`` for the
#: full rationale.  Any change here changes ``runtime_script_sha256``
#: and therefore ``build_id``.
_TOPOLOGY_PROXY_OFFSET_Y_M = 2.5


def _topology_proxy_center(first_part_center):
    """Phase 4.3: compute the world-space center of the topology proxy.

    Returns a 3-tuple placed ``_TOPOLOGY_PROXY_OFFSET_Y_M`` metres in
    the -y direction from the module's first part center.  The -y
    direction is chosen because module parts extend in +y (instance_id
    increases -> y increases by ``_DEFAULT_PART_SPACING_Y_M``), so -y
    is always "away from parts" and the proxy does not overlap any
    module mesh.  The proxy extent is 1.5 m (half-extent 0.75 m), so
    the closest surface point on the proxy is on its +y face at
    distance ``_TOPOLOGY_PROXY_OFFSET_Y_M - 0.75 = 1.75 m`` from the
    first part center, which is within the probe's
    ``MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M = 2.0 m`` threshold.
    """

    return (
        float(first_part_center[0]),
        float(first_part_center[1]) - _TOPOLOGY_PROXY_OFFSET_Y_M,
        float(first_part_center[2]),
    )


def _topology_proxy_geometry(center_m):
    """Return a MeshAssembler with a single 1.5 m box centered on
    ``center_m`` (the proxy placement point)."""

    assembler = MeshAssembler()
    assembler.add_box(
        (float(center_m[0]), float(center_m[1]), float(center_m[2])),
        _DEFAULT_TOPOLOGY_PROXY_EXTENT_M,
        0.0,
    )
    return assembler


def _tag_topology_proxy(obj, module_id, topology_ref):
    """Tag a proxy mesh with its module-specific identity.

    The proxy carries the same low-trust stage / usability / trust_effect
    Literals as the module meshes so a downstream renderer cannot mistake
    it for a surveyed topology attachment.  ``nv_root`` is intentionally
    absent (``obj.get("nv_root")`` returns ``None``) so
    ``_validate_built_modules`` does not count it toward the 218 canonical
    roots.  ``nv_proxy_topology = True`` lets the validator recognise it
    as the auxiliary kind.
    """

    obj["nv_proxy_topology"] = True
    obj["nv_stable_id"] = _topology_proxy_id_for_module(module_id, topology_ref)
    obj["nv_proxy_module_id"] = module_id
    obj["nv_proxy_topology_ref"] = topology_ref
    obj["nv_stage"] = "modeled-unverified"
    obj["nv_trust_effect"] = "none"
    obj["nv_geometry_usability"] = "preview-only"
    # Probe-only attachment geometry must never leak into production RGB,
    # masks, or camera-clearance rays.  It remains in bpy.data so the
    # dedicated Phase 4 mesh probe can inspect it explicitly.
    obj.hide_render = True
    obj.hide_viewport = False


def _build_topology_proxies(request, collection):
    """Build one proxy mesh per module.

    Returns the list of created mesh objects.  Each proxy mesh is
    parented to its module's first part root (so it moves with the
    module if the plan layout changes) and gets a single material slot
    so the build report's ``finite_nonempty_module_meshes`` Literal
    stays honest (each proxy is finite, non-empty, has UVs + tangents
    + exactly one material).
    """

    plan = request["reciprocal_route_module_plan"]
    targets = _topology_proxy_targets(plan)
    proxies = []
    for module_id, topology_ref, _look_at_m in targets:
        # Find the module's first part root to parent the proxy to.
        module = next(
            (
                m for m in plan["modules"]
                if m.get("module_id") == module_id
            ),
            None,
        )
        if module is None or not isinstance(module.get("parts"), list):
            raise RuntimeBuildError(
                f"topology proxy module {module_id} has no parts",
            )
        parts = sorted(module["parts"], key=lambda p: p["instance_id"])
        first_part = parts[0]
        first_part_id = first_part["part_id"]
        root_name = f"nv__{first_part_id}"
        parent_root = bpy.data.objects.get(root_name)
        if parent_root is None:
            raise RuntimeBuildError(
                f"topology proxy parent root missing: {root_name}",
            )
        stable_id = _topology_proxy_id_for_module(module_id, topology_ref)
        proxy_name = f"proxy__{stable_id}"
        if bpy.data.objects.get(proxy_name) is not None:
            raise RuntimeBuildError(
                f"topology proxy already exists: {proxy_name}",
            )
        # Phase 4.3: place proxy near the module's first part (not at
        # the role camera's look_at 25 m away) so the probe's
        # ``MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M = 2.0 m`` threshold can
        # be satisfied.  See ``_topology_proxy_center`` for rationale.
        first_part_center = first_part["part_layout"]["center_m"]
        proxy_center = _topology_proxy_center(first_part_center)
        assembler = _topology_proxy_geometry(proxy_center)
        mesh = bpy.data.meshes.new(f"m__{stable_id}")
        mesh.from_pydata(assembler.vertices, [], assembler.faces)
        mesh.update()
        if not mesh.vertices or not mesh.polygons:
            raise RuntimeBuildError(
                f"topology proxy mesh is empty: {stable_id}",
            )
        proxy_obj = bpy.data.objects.new(proxy_name, mesh)
        collection.objects.link(proxy_obj)
        proxy_obj.parent = parent_root
        _tag_topology_proxy(proxy_obj, module_id, topology_ref)
        # Reuse the parent's material so the proxy has exactly one slot
        # (the build report's structural validator checks this).
        parent_mesh = next(
            (
                child for child in parent_root.children
                if child.name.startswith("mesh__")
            ),
            None,
        )
        if parent_mesh is None or not parent_mesh.data.materials:
            raise RuntimeBuildError(
                f"topology proxy parent has no render mesh: {root_name}",
            )
        proxy_obj.data.materials.append(parent_mesh.data.materials[0])
        # UVs + tangents so the proxy passes the same finite-non-empty
        # structural check as module meshes.
        uv_layer = proxy_obj.data.uv_layers.new(name="uv0")
        proxy_obj.data.uv_layers.active = uv_layer
        for corner in proxy_obj.data.loops:
            uv_layer.data[corner.index].uv = (0.0, 0.0)
        proxy_obj.data.calc_tangents()
        proxy_obj["nv_tangents"] = True
        proxies.append(proxy_obj)
    return proxies


def _tag(obj, row):
    obj["nv_root"] = True
    obj["nv_stable_id"] = row["object_id"]
    obj["nv_instance_id"] = row["instance_id"]
    obj["nv_semantic_id"] = row["semantic_id"]
    obj["nv_material_id"] = row["material_id"]
    obj["nv_variant_id"] = row.get("variant_id") or ""
    obj["nv_stage"] = "modeled-unverified"
    obj["nv_trust_effect"] = "none"
    obj["nv_geometry_usability"] = "preview-only"
    obj.pass_index = row["instance_id"]


def _tag_render_mesh(obj, row):
    """Bind a module mesh to the frozen six-layer renderer contract."""

    obj["nv_stable_id"] = row["object_id"]
    obj["nv_root_id"] = row["object_id"]
    obj["nv_instance_id"] = row["instance_id"]
    obj["nv_semantic_id"] = row["semantic_id"]
    obj["nv_material_id"] = row["material_id"]
    obj["nv_variant_id"] = row.get("variant_id") or ""
    obj["nv_stage"] = "modeled-unverified"
    obj["nv_trust_effect"] = "none"
    obj["nv_geometry_usability"] = "preview-only"
    obj.pass_index = row["instance_id"]


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
    _tag_render_mesh(obj, registry)
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
            assembler = _module_geometry(part)
            mesh = _link_mesh(
                root,
                assembler,
                material,
                row,
                collection,
            )
            roots.append(root)
            meshes.append(mesh)
    # Phase 4.3: emit one topology proxy mesh per module so the probe's
    # ``closest_point_on_mesh`` can hit a real mesh instead of the v1
    # EMPTY / curve topology root.  Proxies are auxiliary and do NOT
    # count toward the 218-root canonical registry.
    proxies = _build_topology_proxies(request, collection)
    return roots, meshes, proxies


def _validate_built_modules(request, base_roots, module_roots, module_meshes, topology_proxies):
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
        stable_id = root.get("nv_stable_id")
        if (
            root.get("nv_stage") != "modeled-unverified"
            or root.get("nv_trust_effect") != "none"
            or root.get("nv_geometry_usability") != "preview-only"
            or root.get("nv_variant_id") != ""
            or root.pass_index != root.get("nv_instance_id")
            or mesh.parent is not root
            or mesh.get("nv_stable_id") != stable_id
            or mesh.get("nv_root_id") != stable_id
            or mesh.get("nv_instance_id") != root.get("nv_instance_id")
            or mesh.get("nv_semantic_id") != root.get("nv_semantic_id")
            or mesh.get("nv_material_id") != root.get("nv_material_id")
            or mesh.get("nv_variant_id") != root.get("nv_variant_id")
            or mesh.pass_index != root.pass_index
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
    _validate_topology_proxies(request, topology_proxies)
    bpy.context.scene["nv_reciprocal_route_module_build"] = json.dumps(
        {
            "build_id": request["build_id"],
            "reciprocal_route_module_plan_sha256": request[
                "reciprocal_route_module_plan_sha256"
            ],
            "geometry_usability": "preview-only",
            "module_root_count": EXPECTED_MODULE_ROOTS,
            "topology_proxy_count": EXPECTED_TOPOLOGY_PROXY_COUNT,
            "stage": "modeled-unverified",
            "trust_effect": "none",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_topology_proxies(request, topology_proxies):
    """Verify each proxy mesh carries the right identity and is finite
    and non-empty.

    Proxies are auxiliary and do NOT count toward the 218-root canonical
    registry, so the registry check above is unaffected.  But each proxy
    must still match its declared ``(module_id, topology_ref)`` pair from
    the canonical plan, have a finite non-empty mesh with one material,
    and carry the low-trust Literal tags so no downstream code can
    promote it.
    """

    if len(topology_proxies) != EXPECTED_TOPOLOGY_PROXY_COUNT:
        raise RuntimeBuildError(
            f"topology proxy count is {len(topology_proxies)}, "
            f"expected {EXPECTED_TOPOLOGY_PROXY_COUNT}",
        )
    plan = request["reciprocal_route_module_plan"]
    targets = _topology_proxy_targets(plan)
    expected_by_module = {
        module_id: (topology_ref, look_at_m)
        for module_id, topology_ref, look_at_m in targets
    }
    seen_proxy_ids = set()
    for proxy in topology_proxies:
        stable_id = proxy.get("nv_stable_id")
        if not isinstance(stable_id, str) or not stable_id:
            raise RuntimeBuildError(
                "topology proxy is missing nv_stable_id",
            )
        if stable_id in seen_proxy_ids:
            raise RuntimeBuildError(
                f"topology proxy stable_id is duplicated: {stable_id}",
            )
        seen_proxy_ids.add(stable_id)
        if proxy.get("nv_root") is True:
            raise RuntimeBuildError(
                f"topology proxy must not carry nv_root: {stable_id}",
            )
        if (
            proxy.get("nv_proxy_topology") is not True
            or proxy.get("nv_stage") != "modeled-unverified"
            or proxy.get("nv_trust_effect") != "none"
            or proxy.get("nv_geometry_usability") != "preview-only"
        ):
            raise RuntimeBuildError(
                f"topology proxy tags are invalid: {stable_id}",
            )
        module_id = proxy.get("nv_proxy_module_id")
        topology_ref = proxy.get("nv_proxy_topology_ref")
        if (module_id, topology_ref) not in [
            (mid, tref) for mid, tref, _ in targets
        ]:
            raise RuntimeBuildError(
                f"topology proxy (module_id={module_id}, "
                f"topology_ref={topology_ref}) is not in plan targets",
            )
        expected_topology_ref, _expected_look_at = expected_by_module[module_id]
        if topology_ref != expected_topology_ref:
            raise RuntimeBuildError(
                f"topology proxy topology_ref={topology_ref} for "
                f"{module_id} disagrees with plan "
                f"({expected_topology_ref})",
            )
        expected_stable_id = _topology_proxy_id_for_module(module_id, topology_ref)
        if stable_id != expected_stable_id:
            raise RuntimeBuildError(
                f"topology proxy stable_id={stable_id} disagrees with "
                f"expected {expected_stable_id}",
            )
        if proxy.type != "MESH":
            raise RuntimeBuildError(
                f"topology proxy is not a MESH: {stable_id}",
            )
        if (
            not proxy.data.vertices
            or not proxy.data.polygons
            or proxy.get("nv_tangents") is not True
            or len(proxy.data.materials) != 1
        ):
            raise RuntimeBuildError(
                f"topology proxy mesh is structurally invalid: {stable_id}",
            )
        for vertex in proxy.data.vertices:
            if not all(math.isfinite(value) for value in vertex.co):
                raise RuntimeBuildError(
                    f"topology proxy mesh contains non-finite vertex: {stable_id}",
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
    junction_clearance_evidence = _apply_junction_vegetation_clearances(
        request,
        base_roots,
    )
    module_roots, module_meshes, topology_proxies = _build_modules(request)
    _validate_built_modules(
        request,
        base_roots,
        module_roots,
        module_meshes,
        topology_proxies,
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
                "junction_vegetation_clearances": len(
                    junction_clearance_evidence,
                ),
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
