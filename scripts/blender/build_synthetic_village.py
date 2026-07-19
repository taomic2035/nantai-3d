"""Build the deterministic synthetic-village Blender canary.

This file executes inside the pinned Blender runtime.  Keep imports limited to
the Python standard library, :mod:`bpy`, and :mod:`mathutils`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import stat
import struct
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector


class RuntimeBuildError(RuntimeError):
    """Stable, user-facing failure raised before publishing any artifact."""


MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_MATERIAL_IMAGE_BYTES = 64 * 1024 * 1024
REQUEST_SCHEMA = "nantai.synthetic-village.blender-build-request.v1"
REPORT_SCHEMA = "nantai.synthetic-village.blender-build-report.v1"
TEXTURED_REQUEST_SCHEMA = "nantai.synthetic-village.blender-build-request.v2"
TEXTURED_REPORT_SCHEMA = "nantai.synthetic-village.blender-build-report.v2"
LOCAL_TEXTURED_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-textured-preview-request.v1"
)
LOCAL_TEXTURED_REPORT_SCHEMA = (
    "nantai.synthetic-village.local-textured-preview-build-report.v1"
)
FIDELITY = "simplified-pbr-not-render-parity"
SUPPORTED_TEXTURED_ALGORITHM_IDS = {
    "mirror-sobel-orm-v1",
    "edge-feather-sobel-orm-v2",
}
UV_POLICIES = {
    "world-xy",
    "dominant-axis-box",
    "roof-slope",
    "object-long-axis",
    "leaf-card",
}
# 可选 weather 块的 schema, 必须与 pipeline/synthetic_village/weather_profile.py
# 的 WEATHER_PROFILE_SCHEMA 逐字一致。weather 缺省 -> 走 canary 原样光照。
WEATHER_PROFILE_SCHEMA = "nantai.synthetic-village.weather-profile.v1"
# build 侧固定灯光角色 (overcast-world-background 校验要求; scene-graph token, 不随天气改)。
WEATHER_LIGHT_ROLES = ("neutral-overcast-key", "neutral-sky-fill", "terrain-separation")
ELEVATED_TOPOLOGY_SCHEMA = "nantai.synthetic-village.elevated-topology.v1"
ELEVATED_COMPONENT_IDENTITIES = (
    ("elevated-switchback-stair-v1", "switchback-stair", 127),
    ("covered-timber-gallery-v1", "covered-timber-gallery", 128),
    ("terrace-ramp-junction-v1", "terrace-ramp-junction", 129),
    ("cross-level-covered-passage-v1", "cross-level-covered-passage", 130),
)
TERRAIN_TEXTURE_SCALE = 3.0
TERRAIN_TEXTURE_SLOTS = (
    "material-moss-stone-01",
    "material-packed-earth-01",
    "material-terrace-soil-01",
)
BUILDING_GEOMETRY_V1 = "front-facade-box-v1"
BUILDING_GEOMETRY_V2 = "four-sided-rural-building-v2"
BUILDING_ELEVATIONS = ("front", "left", "rear", "right")
BUILDING_VARIANTS = (
    "balanced-residence",
    "side-entry-workshop",
    "rear-service-house",
)
EXPECTED_BUILDING_VARIANT_COUNTS = {
    "balanced-residence": 21,
    "rear-service-house": 20,
    "side-entry-workshop": 29,
}
MAX_ADDED_BUILDING_FACES = 220
MAX_ADDED_VILLAGE_FACES = 15_400
MAX_BUILDING_GLTF_TRIANGLES = 720
MAX_GLTF_TRIANGLES = 100_000
MAX_TEXTURED_GLB_BYTES = 150_000_000
EXPECTED_TEXTURED_GLB_PRIMITIVES = 559
EXPECTED_BUILDING_MESH_OBJECTS = 421
MAX_SURFACE_GLTF_TRIANGLES = 125_000
MAX_SURFACE_GLB_BYTES = 160_000_000
EXPECTED_SURFACE_GLB_PRIMITIVES = 577
EXPECTED_SURFACE_DETAIL_MESH_OBJECTS = 18
LEGACY_SURFACE_PROFILE_ID = "single-scale-derived-pbr-v0"
SURFACE_PROFILE_V1 = "source-consistent-multiscale-surface-v1"
SURFACE_ALGORITHM_V1 = "source-palette-world-macro-path-detail-v1"
SURFACE_RUNTIME_NAME = "surface_realism_runtime.py"
SURFACE_MACRO_SLOTS = (
    "material-moss-stone-01",
    "material-packed-earth-01",
    "material-terrace-soil-01",
    "material-wet-stone-paving-01",
)
SURFACE_DETAIL_CAPS = {
    "stone-fragment": 128,
    "leaf-card": 384,
    "damp-patch": 72,
    "rut-run": 96,
}
SURFACE_TERRAIN_SPACING_M = 4.0
SURFACE_PATH_STEP_M = 1.0
SURFACE_PATH_LATERAL_RAILS = 6

SEMANTIC_CLASSES = (
    "background",
    "terrain",
    "support",
    "building",
    "bridge",
    "creek",
    "pond",
    "path",
    "field",
    "orchard",
    "bamboo",
    "courtyard",
    "retaining-wall",
    "prop",
    "elevated-walkway",
)
MATERIAL_FAMILIES = (
    "bamboo-stem",
    "dark-timber",
    "fieldstone",
    "orchard-leaf",
    "packed-earth",
    "pale-plaster",
    "rammed-earth",
    "shallow-water",
    "terrace-soil",
    "weathered-timber",
    "wet-stone-paving",
)
PROP_SLOT_VARIANTS = (
    ("prop-water-jar-01", "water-jar"),
    ("prop-firewood-stack-01", "firewood-stack"),
    ("prop-bamboo-basket-01", "bamboo-basket"),
    ("prop-wooden-bench-01", "wooden-bench"),
    ("prop-farming-tools-01", "farming-tools"),
    ("prop-grain-rack-01", "grain-rack"),
    ("prop-stone-trough-01", "stone-trough"),
    ("prop-handcart-01", "handcart"),
)
PROP_VARIANTS = tuple(variant for _, variant in PROP_SLOT_VARIANTS)
KEY_VIEW_SLOT_IDS = (
    "key-view-establishing-small-01",
    "key-view-establishing-expanded-01",
    "key-view-creekside-entrance-01",
    "key-view-central-courtyard-01",
    "key-view-upper-switchback-01",
    "key-view-opposite-slope-01",
    "key-view-community-hall-01",
    "key-view-orchard-terrace-01",
    "key-view-bamboo-lane-01",
    "key-view-irrigation-pond-01",
    "key-view-lower-bridge-01",
    "key-view-upper-bridge-01",
    "key-view-south-ground-route-01",
    "key-view-east-ground-route-01",
    "key-view-field-edge-01",
    "key-view-roofline-crossing-01",
)
DETAIL_SLOT_COMPONENTS = {
    "detail-timber-door-01": "timber-door",
    "detail-timber-window-01": "two-latticed-windows",
    "detail-tile-eave-01": "tiled-gabled-roof-ridge-eaves",
    "detail-roof-ridge-01": "tiled-gabled-roof-ridge-eaves",
    "detail-stone-stair-01": None,
    "detail-drainage-channel-01": None,
    "detail-retaining-corner-01": None,
    "detail-timber-balcony-01": None,
    "detail-plaster-repair-01": None,
    "detail-rammed-layer-01": None,
    "detail-courtyard-joint-01": "paving-joints",
    "detail-bridge-parapet-01": "stone-deck-parapets-piers",
}
ENVIRONMENT_SLOT_COMPONENTS = {
    "environment-stone-bridge-01": "stone-deck-parapets-piers",
    "environment-creek-bend-01": "terrain-conform-ribbon",
    "environment-irrigation-pond-01": "terrain-conform-surface",
    "environment-terrace-field-01": "terrace-field-surfaces",
    "environment-orchard-slope-01": "orchard-trunks-canopies",
    "environment-bamboo-grove-01": "bamboo-stems-leaves",
    "environment-forest-mountain-01": "upper-slope-forest",
    "environment-overcast-sky-01": "overcast-world-background",
}
KEY_VIEW_PREVIEW_ARTIFACTS = {
    "key-view-creekside-entrance-01": "preview-bridge.png",
    "key-view-central-courtyard-01": "preview-central.png",
    "key-view-upper-switchback-01": "preview-upper.png",
    "key-view-opposite-slope-01": "preview-outer.png",
}
AGGREGATE_COMPONENT_REQUIREMENTS = {
    "terrace-field-surfaces": {"terrain-conform-surface", "terrace-levees"},
    "orchard-trunks-canopies": {
        "terrain-conform-surface",
        "orchard-trunks",
        "orchard-canopies",
    },
    "bamboo-stems-leaves": {
        "terrain-conform-surface",
        "bamboo-stems",
        "bamboo-leaves",
    },
}
ARTIFACT_REQUESTS = (
    {"kind": "rgb-preview", "name": "preview-bridge.png"},
    {"kind": "rgb-preview", "name": "preview-central.png"},
    {"kind": "rgb-preview", "name": "preview-outer.png"},
    {"kind": "rgb-preview", "name": "preview-upper.png"},
    {"kind": "blender-scene", "name": "village-canary.blend"},
    {"kind": "gltf-binary", "name": "village-canary.glb"},
)

VISUAL_MATERIALS = {
    "material-aged-metal-01": ((0.16, 0.18, 0.19, 1.0), 0.52, 0.62),
    "material-bamboo-leaf-01": ((0.12, 0.31, 0.08, 1.0), 0.74, 0.0),
    "material-bamboo-stem-01": ((0.34, 0.48, 0.13, 1.0), 0.58, 0.0),
    "material-broadleaf-bark-01": ((0.20, 0.10, 0.045, 1.0), 0.91, 0.0),
    "material-broadleaf-canopy-01": ((0.08, 0.27, 0.075, 1.0), 0.82, 0.0),
    "material-clay-brick-01": ((0.47, 0.20, 0.105, 1.0), 0.83, 0.0),
    "material-creek-rock-01": ((0.30, 0.32, 0.31, 1.0), 0.88, 0.0),
    "material-dark-timber-01": ((0.105, 0.048, 0.026, 1.0), 0.78, 0.0),
    "material-dry-stone-wall-01": ((0.35, 0.34, 0.31, 1.0), 0.94, 0.0),
    "material-fieldstone-01": ((0.31, 0.30, 0.275, 1.0), 0.91, 0.0),
    "material-gray-roof-tile-01": ((0.085, 0.095, 0.105, 1.0), 0.76, 0.0),
    "material-moss-stone-01": ((0.26, 0.30, 0.22, 1.0), 0.93, 0.0),
    "material-orchard-bark-01": ((0.24, 0.13, 0.055, 1.0), 0.88, 0.0),
    "material-orchard-leaf-01": ((0.18, 0.39, 0.095, 1.0), 0.76, 0.0),
    "material-packed-earth-01": ((0.39, 0.25, 0.13, 1.0), 0.96, 0.0),
    "material-pale-plaster-01": ((0.73, 0.69, 0.57, 1.0), 0.88, 0.0),
    "material-rammed-earth-01": ((0.52, 0.30, 0.13, 1.0), 0.94, 0.0),
    "material-rice-paddy-water-01": ((0.16, 0.29, 0.25, 1.0), 0.19, 0.0),
    "material-shallow-water-01": ((0.11, 0.31, 0.35, 1.0), 0.14, 0.0),
    "material-terrace-soil-01": ((0.25, 0.15, 0.075, 1.0), 0.97, 0.0),
    "material-vegetable-leaf-01": ((0.20, 0.45, 0.10, 1.0), 0.77, 0.0),
    "material-weathered-timber-01": ((0.29, 0.17, 0.085, 1.0), 0.86, 0.0),
    "material-wet-stone-paving-01": ((0.255, 0.27, 0.27, 1.0), 0.48, 0.0),
    "material-woven-bamboo-01": ((0.52, 0.37, 0.14, 1.0), 0.83, 0.0),
}

FAMILY_TO_SLOT = {
    "bamboo-stem": "material-bamboo-stem-01",
    "dark-timber": "material-dark-timber-01",
    "fieldstone": "material-fieldstone-01",
    "orchard-leaf": "material-orchard-leaf-01",
    "packed-earth": "material-packed-earth-01",
    "pale-plaster": "material-pale-plaster-01",
    "rammed-earth": "material-rammed-earth-01",
    "shallow-water": "material-shallow-water-01",
    "terrace-soil": "material-terrace-soil-01",
    "weathered-timber": "material-weathered-timber-01",
    "wet-stone-paving": "material-wet-stone-paving-01",
}


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeBuildError(f"request contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise RuntimeBuildError(f"request contains non-finite JSON number: {value}")


def _canonical_bytes(payload):
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_finite_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _expect_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RuntimeBuildError(f"{label} has unknown or missing fields")


def _expect_list(value, length, label):
    if not isinstance(value, list) or len(value) != length:
        raise RuntimeBuildError(f"{label} must contain exactly {length} entries")


def _is_slug(value):
    return (
        isinstance(value, str)
        and re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*",
            value,
        )
        is not None
    )


def _is_evidence_id(value):
    return (
        isinstance(value, str)
        and re.fullmatch(
            r"[a-z0-9]+(?:[-.][a-z0-9]+)*",
            value,
        )
        is not None
    )


def _prop_variant(object_id):
    if not isinstance(object_id, str) or not object_id.startswith("prop-rural-"):
        return None
    try:
        index = int(object_id.rsplit("-", 1)[1])
        return PROP_VARIANTS[(index - 1) // 2]
    except (ValueError, IndexError):
        return None


def _building_variant(object_id, profile_id):
    if profile_id == BUILDING_GEOMETRY_V1:
        return None
    if profile_id != BUILDING_GEOMETRY_V2:
        raise RuntimeBuildError(f"unknown building geometry profile: {profile_id!r}")
    digest = hashlib.sha256(
        f"{BUILDING_GEOMETRY_V2}\0{object_id}".encode(),
    ).digest()
    return BUILDING_VARIANTS[digest[0] % len(BUILDING_VARIANTS)]


def _visual_slot_categories():
    categories = {}
    for category, slot_ids in (
        ("key-view", KEY_VIEW_SLOT_IDS),
        ("material", tuple(VISUAL_MATERIALS)),
        ("detail", tuple(DETAIL_SLOT_COMPONENTS)),
        ("environment", tuple(ENVIRONMENT_SLOT_COMPONENTS)),
        ("prop", tuple(slot_id for slot_id, _ in PROP_SLOT_VARIANTS)),
    ):
        for slot_id in slot_ids:
            if slot_id in categories:
                raise RuntimeBuildError("stable visual slot taxonomy contains duplicate IDs")
            categories[slot_id] = category
    if len(categories) != 68:
        raise RuntimeBuildError("stable visual slot taxonomy is not exactly 68 entries")
    return categories


def _visual_slot_evidence(scene):
    by_semantic = {}
    for semantic in SEMANTIC_CLASSES[3:]:
        by_semantic[semantic] = tuple(
            sorted(
                item["object_id"] for item in scene["objects"] if item["semantic_class"] == semantic
            ),
        )
    evidence = {slot_id: ("blender-material", (slot_id,)) for slot_id in VISUAL_MATERIALS}
    for slot_id, variant in PROP_SLOT_VARIANTS:
        evidence[slot_id] = (
            variant,
            tuple(
                object_id
                for object_id in by_semantic["prop"]
                if _prop_variant(object_id) == variant
            ),
        )
    environment_evidence = {
        "environment-stone-bridge-01": by_semantic["bridge"],
        "environment-creek-bend-01": by_semantic["creek"],
        "environment-irrigation-pond-01": by_semantic["pond"],
        "environment-terrace-field-01": by_semantic["field"],
        "environment-orchard-slope-01": by_semantic["orchard"],
        "environment-bamboo-grove-01": by_semantic["bamboo"],
        "environment-forest-mountain-01": ("aux-support-terrain-skirt",),
        "environment-overcast-sky-01": ("background-world",),
    }
    for slot_id, component_tag in ENVIRONMENT_SLOT_COMPONENTS.items():
        evidence[slot_id] = (component_tag, environment_evidence[slot_id])
    for slot_id, component_tag in DETAIL_SLOT_COMPONENTS.items():
        if component_tag is None:
            evidence[slot_id] = (None, ())
        elif slot_id == "detail-courtyard-joint-01":
            evidence[slot_id] = (component_tag, by_semantic["courtyard"])
        elif slot_id == "detail-bridge-parapet-01":
            evidence[slot_id] = (component_tag, by_semantic["bridge"])
        else:
            evidence[slot_id] = (component_tag, by_semantic["building"])
    for slot_id in KEY_VIEW_SLOT_IDS:
        artifact_name = KEY_VIEW_PREVIEW_ARTIFACTS.get(slot_id)
        evidence[slot_id] = ("preview-artifact", (artifact_name,)) if artifact_name else (None, ())
    if set(evidence) != set(_visual_slot_categories()):
        raise RuntimeBuildError("stable visual evidence taxonomy is incomplete")
    return evidence


def _signature(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _is_reparse_point(path):
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _assert_direct_path(path, label, leaf_may_be_absent=False):
    parent = path.parent
    if _is_reparse_point(path) or _is_reparse_point(parent):
        raise RuntimeBuildError(f"{label} path is redirected")
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise RuntimeBuildError(f"{label} parent is unavailable") from exc
    if os.path.normcase(str(resolved_parent)) != os.path.normcase(str(parent)):
        raise RuntimeBuildError(f"{label} path is redirected")
    if not leaf_may_be_absent:
        try:
            resolved_leaf = path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeBuildError(f"{label} path is unavailable") from exc
        if os.path.normcase(str(resolved_leaf)) != os.path.normcase(str(path)):
            raise RuntimeBuildError(f"{label} path is redirected")


def _load_request(path: Path):
    try:
        before = path.stat()
        if before.st_size <= 0 or before.st_size > MAX_REQUEST_BYTES:
            raise RuntimeBuildError("request size is invalid")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _signature(before) != _signature(opened):
                raise RuntimeBuildError("request changed before bounded read")
            raw = stream.read(MAX_REQUEST_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_REQUEST_BYTES
            or _signature(opened) != _signature(after_open)
            or _signature(before) != _signature(after)
        ):
            raise RuntimeBuildError("request changed during bounded read")
        return (
            json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            ),
            raw,
        )
    except RuntimeBuildError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeBuildError("request is not valid bounded UTF-8 JSON") from exc


def _validate_weather_block(weather):
    # weather 块的 fail-closed 校验: 未知/缺字段、schema 不符、角色不是固定三 token,
    # 或 lighting_digest 与 lighting 字节对不上 (被手改) -> 一律拒绝。
    _expect_keys(
        weather,
        ("profile_id", "schema", "lighting_digest", "lighting"),
        "weather",
    )
    if weather["schema"] != WEATHER_PROFILE_SCHEMA:
        raise RuntimeBuildError("weather schema is not the weather-profile.v1 contract")
    if not _is_slug(weather["profile_id"]):
        raise RuntimeBuildError("weather profile_id is invalid")
    if not _is_sha256(weather["lighting_digest"]):
        raise RuntimeBuildError("weather lighting_digest is invalid")
    lighting = weather["lighting"]
    _expect_keys(
        lighting,
        (
            "roles",
            "sun_energy",
            "sun_angle_deg",
            "sun_rotation_euler_deg",
            "sun_color",
            "fill_energy",
            "fill_color",
            "fill_location",
            "rim_energy",
            "rim_angle_deg",
            "rim_rotation_euler_deg",
            "world_color",
            "world_strength",
        ),
        "weather lighting",
    )
    if list(lighting["roles"]) != list(WEATHER_LIGHT_ROLES):
        raise RuntimeBuildError("weather light roles are not the frozen scene-graph tokens")
    for triple_key in ("sun_rotation_euler_deg", "sun_color", "fill_color", "world_color"):
        value = lighting[triple_key]
        if not isinstance(value, list) or len(value) != 3:
            raise RuntimeBuildError(f"weather lighting {triple_key} is not a 3-vector")
    # 内容寻址 fail-closed: 摘要必须复算等于 lighting 的 canonical 字节。
    canonical = json.dumps(
        lighting,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if _sha256_bytes(canonical) != weather["lighting_digest"]:
        raise RuntimeBuildError("weather lighting_digest does not match lighting bytes")


def _validate_elevated_topology(topology, scene, source_hashes, semantic_registry):
    """Independently validate the scene-bound topology before any staging exists."""

    _expect_keys(
        topology,
        (
            "schema_version",
            "scene_plan_id",
            "scene_plan_sha256",
            "coordinate_system",
            "synthetic",
            "verification_level",
            "geometry_trust",
            "semantic_id",
            "nodes",
            "edges",
            "components",
            "loops",
            "summary",
        ),
        "elevated_topology",
    )
    if (
        topology["schema_version"] != ELEVATED_TOPOLOGY_SCHEMA
        or topology["scene_plan_id"] != scene.get("plan_id")
        or topology["scene_plan_sha256"] != source_hashes["scene_plan_sha256"]
        or topology["coordinate_system"] != scene.get("coordinate_system")
        or topology["synthetic"] is not True
        or topology["verification_level"] != "L2"
        or topology["geometry_trust"] != "simplified-pbr-not-render-parity"
        or topology["semantic_id"] != 14
    ):
        raise RuntimeBuildError("elevated topology provenance or scene binding is invalid")
    if source_hashes["elevated_topology_sha256"] != _sha256_bytes(
        _canonical_bytes(topology),
    ):
        raise RuntimeBuildError("elevated topology digest does not match request")
    if not any(
        row == {
            "scope": "canonical-object",
            "semantic_class": "elevated-walkway",
            "semantic_id": 14,
        }
        for row in semantic_registry
    ):
        raise RuntimeBuildError("elevated topology semantic ID is not registered")

    nodes = topology["nodes"]
    edges = topology["edges"]
    components = topology["components"]
    loops = topology["loops"]
    _expect_list(nodes, 8, "elevated_topology.nodes")
    _expect_list(edges, 6, "elevated_topology.edges")
    _expect_list(components, 4, "elevated_topology.components")
    _expect_list(loops, 2, "elevated_topology.loops")
    node_ids = []
    node_positions = {}
    for node in nodes:
        _expect_keys(
            node,
            ("node_id", "position_m", "level", "ground_route_ref"),
            "elevated topology node",
        )
        position = node["position_m"]
        if (
            not _is_slug(node["node_id"])
            or not isinstance(position, list)
            or len(position) != 3
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or abs(value * 1000 - round(value * 1000)) > 1e-7
                for value in position
            )
            or node["level"] not in {"ground", "elevated"}
            or (
                node["level"] == "ground"
                and not _is_slug(node["ground_route_ref"])
            )
            or (
                node["level"] == "elevated"
                and node["ground_route_ref"] is not None
            )
        ):
            raise RuntimeBuildError("elevated topology node contract is invalid")
        node_ids.append(node["node_id"])
        node_positions[node["node_id"]] = position
    if node_ids != sorted(set(node_ids)):
        raise RuntimeBuildError("elevated topology node IDs are not unique and stable")

    edge_ids = []
    for edge in edges:
        _expect_keys(
            edge,
            (
                "edge_id",
                "component_id",
                "component_kind",
                "loop_id",
                "start_node_id",
                "end_node_id",
                "width_m",
                "centerline",
                "collision",
            ),
            "elevated topology edge",
        )
        centerline = edge["centerline"]
        collision = edge["collision"]
        _expect_keys(
            collision,
            (
                "deck_thickness_m",
                "railing_height_m",
                "covered",
                "head_clearance_m",
                "drainage_clearance_m",
            ),
            "elevated topology collision envelope",
        )
        if (
            not _is_slug(edge["edge_id"])
            or not _is_slug(edge["component_id"])
            or edge["component_kind"]
            not in {row[1] for row in ELEVATED_COMPONENT_IDENTITIES}
            or edge["loop_id"] not in {"central-loop", "upper-loop"}
            or edge["start_node_id"] not in node_positions
            or edge["end_node_id"] not in node_positions
            or not _is_finite_number(edge["width_m"])
            or edge["width_m"] < 1.8
            or not isinstance(centerline, list)
            or len(centerline) < 2
            or not isinstance(collision["covered"], bool)
            or not _is_finite_number(collision["deck_thickness_m"])
            or collision["deck_thickness_m"] <= 0
            or not _is_finite_number(collision["railing_height_m"])
            or collision["railing_height_m"] < 1.0
            or not _is_finite_number(collision["drainage_clearance_m"])
            or collision["drainage_clearance_m"] < 1.5
            or (
                collision["covered"]
                and (
                    not _is_finite_number(collision["head_clearance_m"])
                    or collision["head_clearance_m"] < 2.1
                )
            )
            or (not collision["covered"] and collision["head_clearance_m"] is not None)
        ):
            raise RuntimeBuildError("elevated topology edge contract is invalid")
        positions = []
        for point in centerline:
            _expect_keys(point, ("position_m",), "elevated topology point")
            position = point["position_m"]
            if (
                not isinstance(position, list)
                or len(position) != 3
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or abs(value * 1000 - round(value * 1000)) > 1e-7
                    for value in position
                )
            ):
                raise RuntimeBuildError("elevated topology point contract is invalid")
            positions.append(position)
        if (
            positions[0] != node_positions[edge["start_node_id"]]
            or positions[-1] != node_positions[edge["end_node_id"]]
        ):
            raise RuntimeBuildError("elevated topology edge endpoints are invalid")
        edge_ids.append(edge["edge_id"])
    if edge_ids != sorted(set(edge_ids)):
        raise RuntimeBuildError("elevated topology edge IDs are not unique and stable")

    owned_edge_ids = []
    actual_identities = []
    for component in components:
        _expect_keys(
            component,
            (
                "component_id",
                "component_kind",
                "instance_id",
                "edge_ids",
                "material_slot_ids",
            ),
            "elevated topology component",
        )
        identity = (
            component["component_id"],
            component["component_kind"],
            component["instance_id"],
        )
        if (
            not isinstance(component["edge_ids"], list)
            or not all(_is_slug(value) for value in component["edge_ids"])
            or component["edge_ids"] != sorted(set(component["edge_ids"]))
            or not isinstance(component["material_slot_ids"], list)
            or not component["material_slot_ids"]
            or not all(_is_slug(value) for value in component["material_slot_ids"])
            or any(
                value not in VISUAL_MATERIALS
                for value in component["material_slot_ids"]
            )
        ):
            raise RuntimeBuildError("elevated topology component contract is invalid")
        actual_identities.append(identity)
        owned_edge_ids.extend(component["edge_ids"])
    if tuple(actual_identities) != ELEVATED_COMPONENT_IDENTITIES:
        raise RuntimeBuildError("elevated topology component identities are invalid")
    if sorted(owned_edge_ids) != edge_ids or len(set(owned_edge_ids)) != len(
        owned_edge_ids
    ):
        raise RuntimeBuildError("elevated topology edge ownership is invalid")
    edge_by_id = {edge["edge_id"]: edge for edge in edges}
    for component in components:
        if any(
            edge_by_id[edge_id]["component_id"] != component["component_id"]
            or edge_by_id[edge_id]["component_kind"] != component["component_kind"]
            for edge_id in component["edge_ids"]
        ):
            raise RuntimeBuildError("elevated topology component metadata is inconsistent")

    expected_loop_ids = ("central-loop", "upper-loop")
    for loop, loop_id in zip(loops, expected_loop_ids, strict=True):
        _expect_keys(
            loop,
            (
                "loop_id",
                "connected",
                "ground_attachment_count",
                "edge_count",
                "edge_ids",
            ),
            "elevated topology loop",
        )
        if (
            loop["loop_id"] != loop_id
            or loop["connected"] is not True
            or loop["ground_attachment_count"] != 2
            or loop["edge_count"] != 3
            or not isinstance(loop["edge_ids"], list)
            or len(loop["edge_ids"]) != 3
            or loop["edge_ids"]
            != [
                edge["edge_id"]
                for edge in edges
                if edge["loop_id"] == loop_id
            ]
        ):
            raise RuntimeBuildError("elevated topology loop contract is invalid")
    if topology["summary"] != {
        "component_count": 4,
        "ground_attachment_count": 4,
        "loop_count": 2,
    }:
        raise RuntimeBuildError("elevated topology summary is invalid")


def _validate_surface_realism_plan(request):
    profile_id = request["surface_realism_profile_id"]
    plan = request["surface_realism_plan"]
    if profile_id != SURFACE_PROFILE_V1 or not isinstance(plan, dict):
        raise RuntimeBuildError("surface realism profile or plan is invalid")
    _expect_keys(
        plan,
        (
            "schema_version",
            "plan_sha256",
            "profile_id",
            "algorithm_id",
            "scene_seed",
            "runtime_module_sha256",
            "terrain_spacing_m",
            "terrain_period_m",
            "ground_period_m",
            "macro_palettes",
            "path_plans",
        ),
        "surface_realism_plan",
    )
    unsigned = dict(plan)
    unsigned.pop("plan_sha256")
    runtime_path = Path(__file__).with_name(SURFACE_RUNTIME_NAME)
    if (
        plan["schema_version"]
        != "nantai.synthetic-village.surface-realism-plan.v1"
        or plan["profile_id"] != SURFACE_PROFILE_V1
        or plan["algorithm_id"] != SURFACE_ALGORITHM_V1
        or plan["scene_seed"] != request["scene_plan"].get("seed")
        or plan["terrain_spacing_m"] != 4.0
        or plan["terrain_period_m"] != 20.0
        or plan["ground_period_m"] != 10.0
        or not _is_sha256(plan["plan_sha256"])
        or _sha256_bytes(_canonical_bytes(unsigned)) != plan["plan_sha256"]
        or not _is_sha256(plan["runtime_module_sha256"])
        or not runtime_path.is_file()
        or _sha256_file(runtime_path) != plan["runtime_module_sha256"]
    ):
        raise RuntimeBuildError("surface realism plan identity is invalid")

    material_inputs = request["material_input_registry"]
    _expect_list(material_inputs, 24, "material_input_registry")
    if any(
        not isinstance(row, dict)
        or "slot_id" not in row
        or "source_sha256" not in row
        for row in material_inputs
    ):
        raise RuntimeBuildError("surface material source registry is invalid")
    material_sources = {
        row["slot_id"]: row["source_sha256"]
        for row in material_inputs
    }
    palettes = plan["macro_palettes"]
    _expect_list(palettes, 4, "surface_realism_plan.macro_palettes")
    if [row.get("slot_id") for row in palettes if isinstance(row, dict)] != list(
        SURFACE_MACRO_SLOTS,
    ):
        raise RuntimeBuildError("surface realism macro palette slots are invalid")
    for palette in palettes:
        _expect_keys(
            palette,
            (
                "slot_id",
                "source_sha256",
                "quantization_denominator",
                "multipliers_q",
                "palette_sha256",
            ),
            "surface macro palette",
        )
        multipliers = palette["multipliers_q"]
        _expect_list(multipliers, 256, "surface macro palette multipliers")
        if (
            palette["source_sha256"]
            != material_sources.get(palette["slot_id"])
            or palette["quantization_denominator"] != 4096
            or not _is_sha256(palette["palette_sha256"])
            or _sha256_bytes(_canonical_bytes(multipliers))
            != palette["palette_sha256"]
            or any(
                not isinstance(row, list)
                or len(row) != 3
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 3605 <= value <= 4505
                    for value in row
                )
                for row in multipliers
            )
        ):
            raise RuntimeBuildError("surface macro palette evidence is invalid")

    path_plans = plan["path_plans"]
    _expect_list(path_plans, 6, "surface_realism_plan.path_plans")
    expected_path_ids = [
        f"path-network-{index:03d}"
        for index in range(1, 7)
    ]
    if [
        row.get("object_id")
        for row in path_plans
        if isinstance(row, dict)
    ] != expected_path_ids:
        raise RuntimeBuildError("surface path plan identities are invalid")
    detail_counts = {key: 0 for key in SURFACE_DETAIL_CAPS}
    for path_plan in path_plans:
        _expect_keys(
            path_plan,
            (
                "object_id",
                "path_length_m",
                "longitudinal_step_m",
                "lateral_rail_count",
                "details",
                "rut_runs",
            ),
            "surface path plan",
        )
        path_length = path_plan["path_length_m"]
        details = path_plan["details"]
        rut_runs = path_plan["rut_runs"]
        if (
            not _is_finite_number(path_length)
            or path_length <= 0
            or path_plan["longitudinal_step_m"] != 1.0
            or path_plan["lateral_rail_count"] != 6
            or not isinstance(details, list)
            or not isinstance(rut_runs, list)
            or not details
            or not rut_runs
        ):
            raise RuntimeBuildError("surface path plan dimensions are invalid")
        classes = set()
        for detail in details:
            _expect_keys(
                detail,
                (
                    "detail_id",
                    "detail_class",
                    "arc_length_m",
                    "side_fraction",
                    "scale",
                    "yaw_deg",
                ),
                "surface path detail",
            )
            detail_class = detail["detail_class"]
            classes.add(detail_class)
            if (
                detail_class not in {
                    "stone-fragment",
                    "leaf-card",
                    "damp-patch",
                }
                or not isinstance(detail["detail_id"], str)
                or not detail["detail_id"].startswith(
                    f"{path_plan['object_id']}:",
                )
                or not _is_finite_number(detail["arc_length_m"])
                or not 0 <= detail["arc_length_m"] <= path_length
                or not _is_finite_number(detail["side_fraction"])
                or not 0.68 <= abs(detail["side_fraction"]) <= 0.78
                or not _is_finite_number(detail["scale"])
                or not 0.65 <= detail["scale"] <= 0.90
                or not _is_finite_number(detail["yaw_deg"])
                or not 0 <= detail["yaw_deg"] < 360
            ):
                raise RuntimeBuildError("surface path detail is invalid")
            detail_counts[detail_class] += 1
        if classes != {"stone-fragment", "leaf-card", "damp-patch"}:
            raise RuntimeBuildError("surface path detail classes are incomplete")
        for rut in rut_runs:
            _expect_keys(
                rut,
                (
                    "rut_id",
                    "start_arc_length_m",
                    "length_m",
                    "depth_m",
                ),
                "surface path rut",
            )
            if (
                not isinstance(rut["rut_id"], str)
                or not rut["rut_id"].startswith(f"{path_plan['object_id']}:rut:")
                or not _is_finite_number(rut["start_arc_length_m"])
                or not _is_finite_number(rut["length_m"])
                or not _is_finite_number(rut["depth_m"])
                or not 6 <= rut["length_m"] <= 18
                or not 0.015 <= rut["depth_m"] <= 0.035
                or rut["start_arc_length_m"] < 0
                or rut["start_arc_length_m"] + rut["length_m"]
                > path_length + 1e-9
            ):
                raise RuntimeBuildError("surface path rut is invalid")
            detail_counts["rut-run"] += 1
    if any(
        count < 1 or count > SURFACE_DETAIL_CAPS[detail_class]
        for detail_class, count in detail_counts.items()
    ):
        raise RuntimeBuildError("surface path detail counts exceed their bounds")


def _load_surface_runtime(request):
    if request.get("surface_realism_profile_id") != SURFACE_PROFILE_V1:
        return None
    runtime_path = Path(__file__).with_name(SURFACE_RUNTIME_NAME)
    expected = request["surface_realism_plan"]["runtime_module_sha256"]
    if _sha256_file(runtime_path) != expected:
        raise RuntimeBuildError("surface realism runtime changed after request validation")
    spec = importlib.util.spec_from_file_location(
        "nantai_surface_realism_runtime",
        runtime_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeBuildError("surface realism runtime cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise RuntimeBuildError("surface realism runtime execution failed") from exc
    if (
        getattr(module, "PROFILE_ID", None) != SURFACE_PROFILE_V1
        or not callable(getattr(module, "sample_macro_color", None))
    ):
        raise RuntimeBuildError("surface realism runtime interface is invalid")
    return module


def _validate_request(request, raw):
    local = request.get("schema_version") == LOCAL_TEXTURED_REQUEST_SCHEMA
    textured = request.get("schema_version") in {
        TEXTURED_REQUEST_SCHEMA,
        LOCAL_TEXTURED_REQUEST_SCHEMA,
    }
    common_top_keys = (
        "schema_version",
        "preview_id" if local else "build_id",
        "synthetic",
        "verification_level",
        *(("authoritative", "release_channel") if local else ()),
        "scene_plan",
        "elevated_topology",
        "camera_plan",
        "source_hashes",
        "tool_identity",
        "object_registry",
        "auxiliary_registry",
        "semantic_registry",
        "material_registry",
        "visual_slot_registry",
        "requested_artifacts",
    )
    top_keys = (
        (
            *common_top_keys,
            "material_bundle_manifest_sha256",
            "material_bundle_id",
            "material_algorithm_id",
            "material_input_registry",
        )
        if textured
        else common_top_keys
    )
    has_building_profile = "building_geometry_profile_id" in request
    if local and not has_building_profile:
        raise RuntimeBuildError("local textured request requires a building geometry profile")
    if has_building_profile:
        if not textured:
            raise RuntimeBuildError("legacy request cannot select a building geometry profile")
        top_keys = (*top_keys, "building_geometry_profile_id")
    has_surface_profile = "surface_realism_profile_id" in request
    has_surface_plan = "surface_realism_plan" in request
    if has_surface_profile != has_surface_plan:
        raise RuntimeBuildError("surface realism profile and plan must appear together")
    if has_surface_profile:
        if not textured:
            raise RuntimeBuildError("legacy request cannot select a surface realism profile")
        top_keys = (
            *top_keys,
            "surface_realism_profile_id",
            "surface_realism_plan",
        )
    # weather 是【可选】top key: 缺席 -> canary 14 键契约原样不变; 出现 -> 天气变体,
    # 该块进入 canonical payload 故 build_id 自动按天气分叉。
    if "weather" in request and not textured:
        _expect_keys(request, (*top_keys, "weather"), "request")
        _validate_weather_block(request["weather"])
    else:
        _expect_keys(request, top_keys, "request")
    if raw != _canonical_bytes(request):
        raise RuntimeBuildError("request must be canonical JSON")
    if (
        request["schema_version"]
        not in {
            REQUEST_SCHEMA,
            TEXTURED_REQUEST_SCHEMA,
            LOCAL_TEXTURED_REQUEST_SCHEMA,
        }
        or request["synthetic"] is not True
        or request["verification_level"] != ("L0" if local else "L2")
        or (local and request["authoritative"] is not False)
        or (local and request["release_channel"] != "local-preview-only")
    ):
        raise RuntimeBuildError("request provenance contract is invalid")
    identity_key = "preview_id" if local else "build_id"
    if not _is_sha256(request[identity_key]):
        raise RuntimeBuildError("request content identity is invalid")
    without_identity = dict(request)
    without_identity.pop(identity_key)
    if _sha256_bytes(_canonical_bytes(without_identity)) != request[identity_key]:
        raise RuntimeBuildError("request content identity does not match canonical inputs")
    if has_surface_profile:
        _validate_surface_realism_plan(request)

    source_hashes = request["source_hashes"]
    source_keys = (
        "default_recipe_sha256",
        "visual_catalog_sha256",
        "visual_source_manifest_sha256",
        "scene_plan_sha256",
        "elevated_topology_sha256",
        "camera_plan_sha256",
        "tool_lock_sha256",
        "builder_script_sha256",
    )
    _expect_keys(source_hashes, source_keys, "source_hashes")
    if not all(_is_sha256(value) for value in source_hashes.values()):
        raise RuntimeBuildError("source_hashes contains an invalid SHA-256")
    if source_hashes["scene_plan_sha256"] != _sha256_bytes(
        _canonical_bytes(request["scene_plan"]),
    ):
        raise RuntimeBuildError("scene plan digest does not match request")
    if source_hashes["elevated_topology_sha256"] != _sha256_bytes(
        _canonical_bytes(request["elevated_topology"]),
    ):
        raise RuntimeBuildError("elevated topology digest does not match request")
    if source_hashes["camera_plan_sha256"] != _sha256_bytes(
        _canonical_bytes(request["camera_plan"]),
    ):
        raise RuntimeBuildError("camera plan digest does not match request")
    if source_hashes["builder_script_sha256"] != _sha256_file(Path(__file__)):
        raise RuntimeBuildError("builder script digest does not match executing script")

    tool = request["tool_identity"]
    tool_keys = (
        "tool_id",
        "version",
        "platform",
        "executable_sha256",
        "runtime_build_hash",
        "runtime_output_sha256",
        "engine",
        "view_transform",
    )
    if not local:
        tool_keys = (*tool_keys[:3], "archive_sha256", *tool_keys[3:])
    _expect_keys(
        tool,
        tool_keys,
        "tool_identity",
    )
    if (
        tool["tool_id"] != "blender"
        or tool["version"] != "4.5.11"
        or tool["platform"] != ("macos-arm64" if local else "windows-x64")
        or tool["runtime_build_hash"] != "4db51e9d1e1e"
        or tool["engine"] != "BLENDER_EEVEE_NEXT"
        or tool["view_transform"] != "AgX"
        or bpy.app.version_string != "4.5.11 LTS"
        or bpy.app.build_hash.decode("ascii") != "4db51e9d1e1e"
        or (local and sys.platform != "darwin")
        or (local and platform.machine() != "arm64")
    ):
        raise RuntimeBuildError("executing Blender identity does not match request")
    digest_keys = (
        ("executable_sha256", "runtime_output_sha256")
        if local
        else ("archive_sha256", "executable_sha256", "runtime_output_sha256")
    )
    if not all(_is_sha256(tool[key]) for key in digest_keys):
        raise RuntimeBuildError("tool_identity contains an invalid SHA-256")
    if local and _sha256_file(Path(bpy.app.binary_path)) != tool["executable_sha256"]:
        raise RuntimeBuildError("executing local Blender bytes do not match request")

    semantic_registry = request["semantic_registry"]
    _expect_list(semantic_registry, 15, "semantic_registry")
    expected_semantics = []
    for semantic_id, semantic_class in enumerate(SEMANTIC_CLASSES):
        scope = (
            "background"
            if semantic_id == 0
            else "auxiliary"
            if semantic_id < 3
            else "canonical-object"
        )
        expected_semantics.append(
            {
                "scope": scope,
                "semantic_class": semantic_class,
                "semantic_id": semantic_id,
            },
        )
    if semantic_registry != expected_semantics:
        raise RuntimeBuildError("semantic_registry is not stable v1")

    material_registry = request["material_registry"]
    _expect_list(material_registry, 11, "material_registry")
    expected_materials = [
        {"material_family": family, "material_id": index}
        for index, family in enumerate(MATERIAL_FAMILIES, 1)
    ]
    if material_registry != expected_materials:
        raise RuntimeBuildError("material_registry is not stable v1")

    scene = request["scene_plan"]
    if not isinstance(scene, dict) or scene.get("plan_id") != (
        "synthetic-mountain-village-scene-v1"
    ):
        raise RuntimeBuildError("scene_plan identity is invalid")
    _validate_elevated_topology(
        request["elevated_topology"],
        scene,
        source_hashes,
        semantic_registry,
    )
    scene_objects = scene.get("objects")
    _expect_list(scene_objects, 126, "scene_plan.objects")
    object_registry = request["object_registry"]
    _expect_list(object_registry, 130, "object_registry")
    semantic_ids = {row["semantic_class"]: row["semantic_id"] for row in semantic_registry}
    material_ids = {row["material_family"]: row["material_id"] for row in material_registry}
    expected_objects = []
    for item in scene_objects:
        identifier = item.get("object_id") if isinstance(item, dict) else None
        semantic_class = item.get("semantic_class") if isinstance(item, dict) else None
        family = item.get("material_family") if isinstance(item, dict) else None
        if (
            not isinstance(identifier, str)
            or semantic_class not in semantic_ids
            or family not in material_ids
        ):
            raise RuntimeBuildError("scene object registry source is invalid")
        variant = None
        if semantic_class == "prop":
            variant = _prop_variant(identifier)
            if variant is None:
                raise RuntimeBuildError("prop variant source is invalid")
        expected_objects.append(
            {
                "instance_id": item.get("instance_id"),
                "material_id": material_ids[family],
                "object_id": identifier,
                "semantic_id": semantic_ids[semantic_class],
                "variant_id": variant,
            },
        )
    elevated_material_family = {
        "switchback-stair": "fieldstone",
        "covered-timber-gallery": "weathered-timber",
        "terrace-ramp-junction": "fieldstone",
        "cross-level-covered-passage": "weathered-timber",
    }
    for component in request["elevated_topology"]["components"]:
        expected_objects.append(
            {
                "instance_id": component["instance_id"],
                "material_id": material_ids[
                    elevated_material_family[component["component_kind"]]
                ],
                "object_id": component["component_id"],
                "semantic_id": 14,
                "variant_id": None,
            },
        )
    if object_registry != expected_objects:
        raise RuntimeBuildError("object_registry does not match scene and elevated topology")
    expected_auxiliary = [
        {
            "auxiliary_id": "background-world",
            "blender_name": "World",
            "kind": "world",
            "semantic_id": 0,
        },
        {
            "auxiliary_id": "aux-terrain",
            "blender_name": "nv__aux-terrain",
            "kind": "mesh",
            "semantic_id": 1,
        },
        {
            "auxiliary_id": "aux-support-terrain-skirt",
            "blender_name": "nv__aux-support-terrain-skirt",
            "kind": "mesh",
            "semantic_id": 2,
        },
    ]
    if request["auxiliary_registry"] != expected_auxiliary:
        raise RuntimeBuildError("auxiliary_registry is not stable v1")

    visual_slots = request["visual_slot_registry"]
    _expect_list(visual_slots, 68, "visual_slot_registry")
    visual_keys = {
        "slot_id",
        "category",
        "usage_mode",
        "source_sha256",
        "reference_status",
        "canary_critical",
        "build_status",
        "implementation",
        "component_tag",
        "evidence_ids",
    }
    if any(not isinstance(row, dict) or set(row) != visual_keys for row in visual_slots):
        raise RuntimeBuildError("visual_slot_registry has unknown or missing fields")
    slot_ids = [row["slot_id"] for row in visual_slots]
    expected_categories = _visual_slot_categories()
    if slot_ids != sorted(expected_categories):
        raise RuntimeBuildError("visual_slot_registry order or IDs are invalid")
    expected_evidence = _visual_slot_evidence(scene)
    category_implementation = {
        "key-view": "composition-reference-v1",
        "material": "derived-pbr-material-v1" if textured else "pbr-material-v1",
        "detail": "geometry-detail-v1",
        "environment": "environment-element-v1",
        "prop": "prop-element-v1",
    }
    for row in visual_slots:
        evidence_ids = row["evidence_ids"]
        if (
            not _is_slug(row["slot_id"])
            or row["category"] != expected_categories[row["slot_id"]]
            or row["usage_mode"]
            not in {
                "design-reference-only",
                "procedural-placeholder-v1",
                *({"runtime-material-source-v1"} if textured else set()),
            }
            or row["reference_status"] not in {"verified-design-reference", "no-reference"}
            or type(row["canary_critical"]) is not bool
            or row["build_status"] not in {"instantiated", "declared-not-instantiated"}
            or row["implementation"]
            not in {
                *category_implementation.values(),
                "not-instantiated-v1",
            }
            or (row["component_tag"] is not None and not _is_slug(row["component_tag"]))
            or not isinstance(evidence_ids, list)
            or evidence_ids != sorted(set(evidence_ids))
            or not all(_is_evidence_id(value) for value in evidence_ids)
        ):
            raise RuntimeBuildError("visual slot enum or scalar field is invalid")
        if row["usage_mode"] in {
            "design-reference-only",
            "runtime-material-source-v1",
        }:
            if (
                not _is_sha256(row["source_sha256"])
                or row["reference_status"] != "verified-design-reference"
            ):
                raise RuntimeBuildError("design reference provenance is invalid")
        elif row["source_sha256"] is not None or row["reference_status"] != "no-reference":
            raise RuntimeBuildError("procedural placeholder claims a visual source")
        component_tag, expected_ids = expected_evidence[row["slot_id"]]
        if row["component_tag"] != component_tag or tuple(evidence_ids) != expected_ids:
            raise RuntimeBuildError("visual slot component evidence does not match stable v1")
        expected_status = (
            "instantiated" if component_tag is not None else "declared-not-instantiated"
        )
        expected_implementation = (
            category_implementation[row["category"]]
            if component_tag is not None
            else "not-instantiated-v1"
        )
        if (
            row["build_status"] != expected_status
            or row["implementation"] != expected_implementation
        ):
            raise RuntimeBuildError("visual slot build claim does not match stable evidence")
        if (
            not textured
            and row["canary_critical"]
            and row["build_status"] != "instantiated"
            and row["reference_status"] != "verified-design-reference"
        ):
            raise RuntimeBuildError("canary-critical visual slot is unfulfilled")
    material_slots = [row for row in visual_slots if row["category"] == "material"]
    if (
        len(material_slots) != 24
        or {row["slot_id"] for row in material_slots} != set(VISUAL_MATERIALS)
        or any(
            row["build_status"] != "instantiated"
            or row["implementation"]
            != ("derived-pbr-material-v1" if textured else "pbr-material-v1")
            or (textured and row["usage_mode"] != "runtime-material-source-v1")
            or row["component_tag"] != "blender-material"
            or row["evidence_ids"] != [row["slot_id"]]
            for row in material_slots
        )
    ):
        raise RuntimeBuildError("all 24 visual material slots must be instantiated")

    if textured:
        if (
            not _is_sha256(request["material_bundle_manifest_sha256"])
            or not _is_sha256(request["material_bundle_id"])
            or request["material_algorithm_id"] not in SUPPORTED_TEXTURED_ALGORITHM_IDS
        ):
            raise RuntimeBuildError("textured material bundle identity is invalid")
        building_profile = request.get(
            "building_geometry_profile_id",
            BUILDING_GEOMETRY_V1,
        )
        if building_profile not in {BUILDING_GEOMETRY_V1, BUILDING_GEOMETRY_V2}:
            raise RuntimeBuildError("building geometry profile is invalid")
        material_inputs = request["material_input_registry"]
        _expect_list(material_inputs, 24, "material_input_registry")
        expected_input_keys = {
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
        if any(
            not isinstance(row, dict) or set(row) != expected_input_keys
            for row in material_inputs
        ):
            raise RuntimeBuildError("material input registry fields are invalid")
        input_ids = [row["slot_id"] for row in material_inputs]
        if input_ids != sorted(VISUAL_MATERIALS):
            raise RuntimeBuildError("material input registry is not the exact sorted set")
        sources_by_slot = {
            row["slot_id"]: row["source_sha256"] for row in material_slots
        }
        for row in material_inputs:
            map_digests = (
                row["base_color_sha256"],
                row["normal_sha256"],
                row["orm_sha256"],
            )
            if (
                row["slot_id"] not in VISUAL_MATERIALS
                or not _is_sha256(row["source_sha256"])
                or not all(_is_sha256(value) for value in map_digests)
                or len(set(map_digests)) != 3
                or sources_by_slot.get(row["slot_id"]) != row["source_sha256"]
                or row["width"] != 1024
                or row["height"] != 1024
                or row["uv_policy"] not in UV_POLICIES
                or isinstance(row["nominal_tile_m"], bool)
                or not isinstance(row["nominal_tile_m"], (int, float))
                or not math.isfinite(row["nominal_tile_m"])
                or row["nominal_tile_m"] <= 0
                or isinstance(row["normal_strength"], bool)
                or not isinstance(row["normal_strength"], (int, float))
                or not math.isfinite(row["normal_strength"])
                or row["normal_strength"] <= 0
                or row["synthetic"] is not True
            ):
                raise RuntimeBuildError("material input registry row is invalid")

    if request["requested_artifacts"] != list(ARTIFACT_REQUESTS):
        raise RuntimeBuildError("requested artifact registry is not stable v1")
    camera_plan = request["camera_plan"]
    if (
        not isinstance(camera_plan, dict)
        or camera_plan.get("scene_plan_sha256") != (source_hashes["scene_plan_sha256"])
    ):
        raise RuntimeBuildError("camera plan does not reference the scene digest")
    cameras = camera_plan.get("cameras")
    _expect_list(cameras, 24, "camera_plan.cameras")
    for camera in cameras:
        matrix = camera.get("c2w_blender") if isinstance(camera, dict) else None
        if (
            not isinstance(matrix, list)
            or len(matrix) != 4
            or any(not isinstance(row, list) or len(row) != 4 for row in matrix)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for row in matrix
                for value in row
            )
        ):
            raise RuntimeBuildError("camera matrix is not finite 4x4")
    return request


class MeshAssembler:
    """Small deterministic mesh builder that avoids context-sensitive operators."""

    def __init__(self):
        self.vertices = []
        self.faces = []

    def add(self, vertices, faces):
        start = len(self.vertices)
        self.vertices.extend(tuple(float(value) for value in vertex) for vertex in vertices)
        self.faces.extend(tuple(start + index for index in face) for face in faces)

    def add_box(self, center, size, yaw=0.0):
        cx, cy, cz = center
        sx, sy, sz = size
        hx, hy, hz = sx / 2, sy / 2, sz / 2
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

    def add_cylinder(
        self,
        center,
        radius,
        height,
        segments=10,
        axis="z",
        radius_top=None,
    ):
        cx, cy, cz = center
        top_radius = radius if radius_top is None else radius_top
        vertices = []
        for end, active_radius in ((-height / 2, radius), (height / 2, top_radius)):
            for index in range(segments):
                angle = 2 * math.pi * index / segments
                u = active_radius * math.cos(angle)
                v = active_radius * math.sin(angle)
                if axis == "x":
                    vertices.append((cx + end, cy + u, cz + v))
                elif axis == "y":
                    vertices.append((cx + u, cy + end, cz + v))
                else:
                    vertices.append((cx + u, cy + v, cz + end))
        faces = []
        faces.append(tuple(reversed(range(segments))))
        faces.append(tuple(range(segments, segments * 2)))
        for index in range(segments):
            following = (index + 1) % segments
            faces.append((index, following, segments + following, segments + index))
        self.add(vertices, faces)

    def add_ellipsoid(self, center, radius, segments=10, rings=5):
        cx, cy, cz = center
        rx, ry, rz = radius
        vertices = [(cx, cy, cz - rz)]
        for ring in range(1, rings):
            latitude = -math.pi / 2 + math.pi * ring / rings
            for segment in range(segments):
                longitude = 2 * math.pi * segment / segments
                vertices.append(
                    (
                        cx + rx * math.cos(latitude) * math.cos(longitude),
                        cy + ry * math.cos(latitude) * math.sin(longitude),
                        cz + rz * math.sin(latitude),
                    ),
                )
        vertices.append((cx, cy, cz + rz))
        top = len(vertices) - 1
        faces = []
        for segment in range(segments):
            faces.append((0, 1 + (segment + 1) % segments, 1 + segment))
        for ring in range(rings - 2):
            first = 1 + ring * segments
            second = first + segments
            for segment in range(segments):
                following = (segment + 1) % segments
                faces.append(
                    (
                        first + segment,
                        first + following,
                        second + following,
                        second + segment,
                    ),
                )
        final_ring = 1 + (rings - 2) * segments
        for segment in range(segments):
            faces.append((final_ring + segment, final_ring + (segment + 1) % segments, top))
        self.add(vertices, faces)

    def add_gabled_roof(self, width, depth, eave_z, ridge_z, overhang=0.55):
        half_width = width / 2 + overhang
        half_depth = depth / 2 + overhang
        vertices = (
            (-half_width, -half_depth, eave_z),
            (half_width, -half_depth, eave_z),
            (-half_width, half_depth, eave_z),
            (half_width, half_depth, eave_z),
            (-half_width, 0.0, ridge_z),
            (half_width, 0.0, ridge_z),
        )
        self.add(
            vertices,
            (
                (0, 1, 5, 4),
                (4, 5, 3, 2),
                (0, 4, 2),
                (1, 3, 5),
                (0, 2, 3, 1),
            ),
        )


def _clear_factory_scene():
    for collection in list(bpy.data.collections):
        if collection.name != "Collection":
            bpy.data.collections.remove(collection)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for camera in list(bpy.data.cameras):
        bpy.data.cameras.remove(camera)
    for light in list(bpy.data.lights):
        bpy.data.lights.remove(light)
    for world in list(bpy.data.worlds):
        bpy.data.worlds.remove(world)


def _new_collection(name, parent=None):
    collection = bpy.data.collections.new(name)
    if parent is None:
        bpy.context.scene.collection.children.link(collection)
    else:
        parent.children.link(collection)
    return collection


def _read_stable_material(path, expected_sha256):
    try:
        before = path.stat()
        if (
            before.st_size <= 0
            or before.st_size > MAX_MATERIAL_IMAGE_BYTES
            or _is_reparse_point(path)
            or not path.is_file()
        ):
            raise RuntimeBuildError("material image is redirected or outside the size bound")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _signature(before) != _signature(opened):
                raise RuntimeBuildError("material image changed before bounded read")
            raw = stream.read(MAX_MATERIAL_IMAGE_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
    except RuntimeBuildError:
        raise
    except OSError as exc:
        raise RuntimeBuildError("material image cannot be read stably") from exc
    if (
        len(raw) != before.st_size
        or len(raw) > MAX_MATERIAL_IMAGE_BYTES
        or _signature(opened) != _signature(after_open)
        or _signature(before) != _signature(after)
        or _sha256_bytes(raw) != expected_sha256
    ):
        raise RuntimeBuildError("material image changed or failed its SHA-256 check")
    if (
        len(raw) < 33
        or raw[:8] != b"\x89PNG\r\n\x1a\n"
        or struct.unpack_from(">I", raw, 8)[0] != 13
        or raw[12:16] != b"IHDR"
    ):
        raise RuntimeBuildError("material image is not a bounded PNG")
    width, height, bit_depth, color_type = struct.unpack_from(">IIBB", raw, 16)
    if (
        width != 1024
        or height != 1024
        or bit_depth != 8
        or color_type not in {2, 6}
    ):
        raise RuntimeBuildError("material image dimensions or pixel format are invalid")


def _validate_material_directory(materials_path, request):
    if materials_path is None:
        raise RuntimeBuildError("textured request requires a material directory")
    _assert_direct_path(materials_path, "materials")
    if not materials_path.is_dir():
        raise RuntimeBuildError("material path is not a directory")
    expected_hashes = {
        digest
        for row in request["material_input_registry"]
        for digest in (
            row["base_color_sha256"],
            row["normal_sha256"],
            row["orm_sha256"],
        )
    }
    expected_names = {f"{digest}.png" for digest in expected_hashes}
    try:
        children = list(materials_path.iterdir())
    except OSError as exc:
        raise RuntimeBuildError("material directory cannot be enumerated") from exc
    if {child.name for child in children} != expected_names:
        raise RuntimeBuildError("material directory is not the exact requested map set")
    paths = {}
    for child in children:
        digest = child.stem
        if (
            child.parent != materials_path
            or child.suffix != ".png"
            or digest not in expected_hashes
            or _is_reparse_point(child)
        ):
            raise RuntimeBuildError("material directory contains a redirected map")
        _assert_direct_path(child, "material image")
        _read_stable_material(child, digest)
        paths[digest] = child
    return paths


def _load_packed_image(path, expected_sha256, *, colorspace):
    _read_stable_material(path, expected_sha256)
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
        if tuple(image.size) != (1024, 1024):
            raise RuntimeBuildError("Blender decoded unexpected material dimensions")
        image.colorspace_settings.name = colorspace
        image.name = f"nv__image-{expected_sha256}"
        image.pack()
        image.filepath = ""
        image.filepath_raw = ""
    except RuntimeBuildError:
        raise
    except Exception as exc:
        raise RuntimeBuildError("Blender could not decode and pack a material image") from exc
    _read_stable_material(path, expected_sha256)
    return image


def _create_textured_materials(request, material_paths):
    visual_registry = request["visual_slot_registry"]
    material_rows = {
        row["slot_id"]: row
        for row in visual_registry
        if row["category"] == "material"
    }
    inputs = {row["slot_id"]: row for row in request["material_input_registry"]}
    if set(material_rows) != set(VISUAL_MATERIALS) or set(inputs) != set(VISUAL_MATERIALS):
        raise RuntimeBuildError("textured material registry is not executable")
    materials = {}
    for material_index, slot_id in enumerate(sorted(VISUAL_MATERIALS), 1):
        row = inputs[slot_id]
        base_image = _load_packed_image(
            material_paths[row["base_color_sha256"]],
            row["base_color_sha256"],
            colorspace="sRGB",
        )
        normal_image = _load_packed_image(
            material_paths[row["normal_sha256"]],
            row["normal_sha256"],
            colorspace="Non-Color",
        )
        orm_image = _load_packed_image(
            material_paths[row["orm_sha256"]],
            row["orm_sha256"],
            colorspace="Non-Color",
        )
        material = bpy.data.materials.new(f"nv__mat-{slot_id}")
        material.use_nodes = True
        material.pass_index = material_index
        material["nv_slot_id"] = slot_id
        material["nv_implementation"] = "derived-pbr-material-v1"
        material["nv_synthetic"] = True
        material["slot_id"] = slot_id
        material["source_sha256"] = row["source_sha256"]
        material["bundle_id"] = request["material_bundle_id"]
        material["algorithm_id"] = request["material_algorithm_id"]
        material["synthetic"] = True
        material["uv_policy"] = row["uv_policy"]
        material["nv_nominal_tile_m"] = row["nominal_tile_m"]

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
        normal_map.name = f"nv__normal-map-{slot_id}"
        # The derived tangent-space normal bytes already contain the declared
        # material strength. Applying it again here attenuates sub-unity maps twice.
        normal_map.inputs["Strength"].default_value = 1.0
        material["nv_baked_normal_strength"] = row["normal_strength"]
        separate = nodes.new("ShaderNodeSeparateColor")
        separate.name = f"nv__orm-channels-{slot_id}"
        if request.get("surface_realism_profile_id") == SURFACE_PROFILE_V1:
            vertex_color = nodes.new("ShaderNodeVertexColor")
            vertex_color.name = f"nv__surface-color-{slot_id}"
            vertex_color.layer_name = "nv_surface_color"
            multiply_color = nodes.new("ShaderNodeMixRGB")
            multiply_color.name = f"nv__source-times-surface-{slot_id}"
            multiply_color.blend_type = "MULTIPLY"
            multiply_color.inputs[0].default_value = 1.0
            links.new(base.outputs["Color"], multiply_color.inputs[1])
            links.new(vertex_color.outputs["Color"], multiply_color.inputs[2])
            links.new(
                multiply_color.outputs["Color"],
                principled.inputs["Base Color"],
            )
            palettes = {
                palette["slot_id"]: palette
                for palette in request["surface_realism_plan"]["macro_palettes"]
            }
            material["nv_surface_realism_profile"] = SURFACE_PROFILE_V1
            material["nv_surface_color_input"] = "nv_surface_color"
            material["nv_surface_palette_sha256"] = (
                palettes[slot_id]["palette_sha256"]
                if slot_id in palettes
                else ""
            )
        else:
            links.new(base.outputs["Color"], principled.inputs["Base Color"])
        links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
        links.new(orm.outputs["Color"], separate.inputs["Color"])
        links.new(separate.outputs["Green"], principled.inputs["Roughness"])
        links.new(separate.outputs["Blue"], principled.inputs["Metallic"])
        if "Coat Weight" in principled.inputs:
            principled.inputs["Coat Weight"].default_value = 0.08
        materials[slot_id] = material
    for digest, path in material_paths.items():
        _read_stable_material(path, digest)
    return materials


def _create_materials(visual_registry):
    registry_ids = {
        row["slot_id"]
        for row in visual_registry
        if row["category"] == "material" and row["build_status"] == "instantiated"
    }
    if registry_ids != set(VISUAL_MATERIALS):
        raise RuntimeBuildError("visual material registry is not executable")
    materials = {}
    for material_index, slot_id in enumerate(sorted(VISUAL_MATERIALS), 1):
        color, roughness, metallic = VISUAL_MATERIALS[slot_id]
        material = bpy.data.materials.new(f"nv__mat-{slot_id}")
        material.use_nodes = True
        material.diffuse_color = color
        material.pass_index = material_index
        material["nv_slot_id"] = slot_id
        material["nv_implementation"] = "pbr-material-v1"
        material["nv_synthetic"] = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Roughness"].default_value = roughness
        principled.inputs["Metallic"].default_value = metallic
        if "Coat Weight" in principled.inputs:
            principled.inputs["Coat Weight"].default_value = 0.08
        materials[slot_id] = material
    _configure_terrain_material(materials["material-moss-stone-01"])
    return materials


def _configure_terrain_material(material):
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = nodes.get("Principled BSDF")
    geometry = nodes.new("ShaderNodeNewGeometry")
    geometry.name = "nv__terrain-geometry"
    separate = nodes.new("ShaderNodeSeparateXYZ")
    separate.name = "nv__terrain-height"
    height_ramp = nodes.new("ShaderNodeValToRGB")
    height_ramp.name = "nv__terrain-height-colors"
    height_ramp.color_ramp.elements[0].position = 0.05
    height_ramp.color_ramp.elements[0].color = (0.14, 0.21, 0.10, 1.0)
    height_ramp.color_ramp.elements[1].position = 0.95
    height_ramp.color_ramp.elements[1].color = (0.38, 0.34, 0.24, 1.0)
    height_ramp.color_ramp.elements.new(0.42).color = (0.22, 0.31, 0.14, 1.0)
    height_ramp.color_ramp.elements.new(0.72).color = (0.31, 0.34, 0.20, 1.0)

    height_map = nodes.new("ShaderNodeMapRange")
    height_map.name = "nv__terrain-height-normalized"
    height_map.inputs["From Min"].default_value = 0.0
    height_map.inputs["From Max"].default_value = 120.0
    height_map.inputs["To Min"].default_value = 0.0
    height_map.inputs["To Max"].default_value = 1.0
    height_map.clamp = True

    slope = nodes.new("ShaderNodeVectorMath")
    slope.name = "nv__terrain-slope"
    slope.operation = "DOT_PRODUCT"
    slope.inputs[1].default_value = (0.0, 0.0, 1.0)
    slope_mix = nodes.new("ShaderNodeMixRGB")
    slope_mix.name = "nv__terrain-slope-mix"
    slope_mix.inputs[1].default_value = (0.22, 0.23, 0.19, 1.0)

    noise = nodes.new("ShaderNodeTexNoise")
    noise.name = "nv__terrain-micro-variation"
    noise.inputs["Scale"].default_value = 0.032
    noise.inputs["Detail"].default_value = 3.0
    noise.inputs["Roughness"].default_value = 0.72
    multiply = nodes.new("ShaderNodeMixRGB")
    multiply.name = "nv__terrain-color-variation"
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 0.22

    links.new(geometry.outputs["Position"], separate.inputs["Vector"])
    links.new(separate.outputs["Z"], height_map.inputs["Value"])
    links.new(height_map.outputs["Result"], height_ramp.inputs["Fac"])
    links.new(geometry.outputs["Normal"], slope.inputs[0])
    links.new(slope.outputs["Value"], slope_mix.inputs[0])
    links.new(height_ramp.outputs["Color"], slope_mix.inputs[2])
    links.new(geometry.outputs["Position"], noise.inputs["Vector"])
    links.new(slope_mix.outputs["Color"], multiply.inputs[1])
    links.new(noise.outputs["Color"], multiply.inputs[2])
    links.new(multiply.outputs["Color"], principled.inputs["Base Color"])
    material["nv_procedural_style"] = "height-slope-noise-v1"


def _dominant_projection_axes(normal):
    dominant = max(range(3), key=lambda index: abs(normal[index]))
    return tuple(index for index in range(3) if index != dominant)


def _triangle_uv_area(values):
    first, second, third = values
    return abs(
        (second[0] - first[0]) * (third[1] - first[1])
        - (second[1] - first[1]) * (third[0] - first[0])
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
        ridge = Vector((-world_normal.y, world_normal.x, 0.0))
        if ridge.length <= 1e-8:
            ridge = Vector((1.0, 0.0, 0.0))
        ridge.normalize()
        fall = world_normal.cross(ridge)
        if fall.length <= 1e-8:
            values = project_axes(world, _dominant_projection_axes(world_normal))
        else:
            fall.normalize()
            values = [
                (
                    float(coordinate.dot(ridge)) / tile_m,
                    float(coordinate.dot(fall)) / tile_m,
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
        raise RuntimeBuildError(f"unsupported UV policy: {policy}")

    if len(values) != 3 or _triangle_uv_area(values) <= 1e-12:
        values = project_axes(world, _dominant_projection_axes(world_normal))
    if len(values) != 3 or _triangle_uv_area(values) <= 1e-12:
        raise RuntimeBuildError(f"UV projection is degenerate: {obj.name}")
    return values


def _surface_palette_by_slot(request):
    if request.get("surface_realism_profile_id") != SURFACE_PROFILE_V1:
        return {}
    return {
        palette["slot_id"]: palette
        for palette in request["surface_realism_plan"]["macro_palettes"]
    }


def _surface_object_uses_macro_color(obj):
    root_id = str(obj.get("nv_root_id", obj.get("nv_stable_id", "")))
    return (
        obj.name == "nv__aux-terrain"
        or root_id.startswith("path-network-")
        or root_id.startswith("field-")
        or root_id.startswith("courtyard-")
    )


def _apply_surface_color_attribute(obj, request, surface_runtime):
    if request.get("surface_realism_profile_id") != SURFACE_PROFILE_V1:
        return
    if surface_runtime is None:
        raise RuntimeBuildError("surface realism runtime is absent")
    mesh = obj.data
    palettes = _surface_palette_by_slot(request)
    color_layer = mesh.color_attributes.get("nv_surface_color")
    if color_layer is None:
        color_layer = mesh.color_attributes.new(
            name="nv_surface_color",
            type="FLOAT_COLOR",
            domain="CORNER",
        )
    if (
        color_layer.data_type != "FLOAT_COLOR"
        or color_layer.domain != "CORNER"
        or len(color_layer.data) != len(mesh.loops)
    ):
        raise RuntimeBuildError(f"surface color layer is invalid: {obj.name}")
    mesh.color_attributes.active_color = color_layer
    macro_object = _surface_object_uses_macro_color(obj)
    used_palettes = {}
    used_macro = False
    damp_detail = obj.get("nv_surface_detail_class") == "damp-patch"
    for polygon in mesh.polygons:
        if polygon.material_index >= len(mesh.materials):
            raise RuntimeBuildError(f"surface material index is invalid: {obj.name}")
        material = mesh.materials[polygon.material_index]
        slot_id = material.get("nv_slot_id")
        palette = palettes.get(slot_id) if macro_object else None
        for loop_index in polygon.loop_indices:
            loop = mesh.loops[loop_index]
            if palette is None:
                color = (1.0, 1.0, 1.0, 1.0)
            else:
                world = obj.matrix_world @ mesh.vertices[loop.vertex_index].co
                period_m = (
                    request["surface_realism_plan"]["terrain_period_m"]
                    if obj.name == "nv__aux-terrain"
                    else request["surface_realism_plan"]["ground_period_m"]
                )
                color = surface_runtime.sample_macro_color(
                    palette["multipliers_q"],
                    x_m=float(world.x),
                    y_m=float(world.y),
                    period_m=period_m,
                    scene_seed=request["surface_realism_plan"]["scene_seed"],
                    source_sha256=palette["source_sha256"],
                )
                if damp_detail:
                    color = tuple(
                        min(1.10, max(0.88, channel * 0.88))
                        for channel in color[:3]
                    ) + (1.0,)
                used_macro = True
                used_palettes[slot_id] = palette["palette_sha256"]
            color_layer.data[loop_index].color = color
    obj["nv_surface_realism_profile"] = SURFACE_PROFILE_V1
    obj["nv_surface_color_mode"] = (
        "damp" if used_macro and damp_detail else "macro" if used_macro else "white"
    )
    obj["nv_surface_palette_sha256"] = json.dumps(
        used_palettes,
        sort_keys=True,
        separators=(",", ":"),
    )


def _apply_textured_uvs_and_tangents(
    mesh_objects,
    request,
    surface_runtime,
):
    for obj in mesh_objects:
        if obj.type != "MESH":
            continue
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        mesh = obj.data
        edit_mesh = bmesh.new()
        try:
            edit_mesh.from_mesh(mesh)
            bmesh.ops.triangulate(edit_mesh, faces=list(edit_mesh.faces))
            edit_mesh.to_mesh(mesh)
        finally:
            edit_mesh.free()
        mesh.update(calc_edges=True)
        if len(mesh.materials) <= 0:
            raise RuntimeBuildError(f"textured mesh has no material: {obj.name}")
        tile_scale = obj.get("nv_uv_tile_scale", 1.0)
        if (
            isinstance(tile_scale, bool)
            or not isinstance(tile_scale, (int, float))
            or not math.isfinite(tile_scale)
            or tile_scale <= 0
        ):
            raise RuntimeBuildError(f"textured mesh UV scale is invalid: {obj.name}")
        uv_layer = mesh.uv_layers.get("nv_uv0") or mesh.uv_layers.new(name="nv_uv0")
        for polygon in mesh.polygons:
            if len(polygon.vertices) != 3 or polygon.material_index >= len(mesh.materials):
                raise RuntimeBuildError(f"textured mesh primitive is invalid: {obj.name}")
            material = mesh.materials[polygon.material_index]
            policy = material.get("uv_policy")
            tile_m = material.get("nv_nominal_tile_m")
            if (
                policy not in UV_POLICIES
                or isinstance(tile_m, bool)
                or not isinstance(tile_m, (int, float))
                or not math.isfinite(tile_m)
                or tile_m <= 0
            ):
                raise RuntimeBuildError(f"textured material UV metadata is invalid: {obj.name}")
            values = _project_polygon_uvs(
                obj,
                polygon,
                policy,
                float(tile_m) * float(tile_scale),
            )
            for loop_index, uv in zip(polygon.loop_indices, values, strict=True):
                uv_layer.data[loop_index].uv = uv
        _apply_surface_color_attribute(
            obj,
            request,
            surface_runtime,
        )
        try:
            mesh.calc_tangents(uvmap=uv_layer.name)
        except Exception as exc:
            raise RuntimeBuildError(f"mesh tangent generation failed: {obj.name}") from exc
        for loop in mesh.loops:
            if (
                not all(math.isfinite(value) for value in loop.tangent)
                or not math.isfinite(loop.bitangent_sign)
            ):
                raise RuntimeBuildError(f"mesh tangent evidence is non-finite: {obj.name}")
        obj["nv_uv_layer"] = uv_layer.name
        obj["nv_tangents"] = True


def _tag_object(obj, stable_id, semantic_id, instance_id, material_id, variant_id=None):
    obj["nv_stable_id"] = stable_id
    obj["nv_semantic_id"] = semantic_id
    obj["nv_instance_id"] = instance_id
    obj["nv_material_id"] = material_id
    obj["nv_variant_id"] = variant_id or ""
    obj.pass_index = instance_id


def _new_root(item, registry, collection):
    identifier = item["object_id"]
    root = bpy.data.objects.new(f"nv__{identifier}", None)
    collection.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 1.0
    root["nv_root"] = True
    root["nv_semantic_class"] = item["semantic_class"]
    root["nv_source_transform"] = json.dumps(item["transform"], sort_keys=True)
    root["nv_components"] = "[]"
    _tag_object(
        root,
        identifier,
        registry["semantic_id"],
        registry["instance_id"],
        registry["material_id"],
        registry["variant_id"],
    )
    if item["semantic_class"] in {"building", "bridge", "prop"}:
        transform = item["transform"]
        root.location = (transform["x_m"], transform["y_m"], 0.0)
        root.rotation_euler[2] = math.radians(transform["yaw_deg"])
    return root


def _link_mesh(root, part_id, assembler, material, registry, collection):
    if not assembler.vertices or not assembler.faces:
        raise RuntimeBuildError(f"mesh part is empty: {root['nv_stable_id']}:{part_id}")
    mesh = bpy.data.meshes.new(f"nv__mesh-{root['nv_stable_id']}__{part_id}")
    mesh.from_pydata(assembler.vertices, [], assembler.faces)
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(f"nv__{root['nv_stable_id']}__{part_id}", mesh)
    collection.objects.link(obj)
    obj.parent = root
    obj.data.materials.append(material)
    obj["nv_part_id"] = part_id
    obj["nv_root_id"] = root["nv_stable_id"]
    _tag_object(
        obj,
        root["nv_stable_id"],
        registry["semantic_id"],
        registry["instance_id"],
        registry["material_id"],
        registry["variant_id"],
    )
    components = json.loads(root.get("nv_components", "[]"))
    components.append(part_id)
    root["nv_components"] = json.dumps(sorted(set(components)), separators=(",", ":"))
    return obj


def _terrain_height(x_m, y_m, extent):
    t = (y_m + extent["depth_m"] / 2) / extent["depth_m"]
    interior = (
        (
            9.0 * math.sin(math.pi * (x_m + extent["width_m"] / 2) / extent["width_m"])
            + 4.0 * math.sin(2 * math.pi * (x_m + extent["width_m"] / 2) / extent["width_m"])
        )
        * 4
        * t
        * (1 - t)
    )
    return round(extent["relief_m"] * t + interior, 3)


def _assign_textured_terrain_materials(terrain_obj, materials):
    moss = materials["material-moss-stone-01"]
    if moss.get("nv_implementation") != "derived-pbr-material-v1":
        return

    mesh = terrain_obj.data
    for slot_id in TERRAIN_TEXTURE_SLOTS[1:]:
        mesh.materials.append(materials[slot_id])
    counts = {slot_id: 0 for slot_id in TERRAIN_TEXTURE_SLOTS}
    for polygon in mesh.polygons:
        center = sum(
            (mesh.vertices[index].co for index in polygon.vertices),
            Vector((0.0, 0.0, 0.0)),
        ) / len(polygon.vertices)
        macro_patch = (
            math.sin(center.x * 0.031)
            + 0.72 * math.cos(center.y * 0.027)
            + 0.38 * math.sin((center.x + center.y) * 0.017)
        )
        if abs(polygon.normal.z) < 0.965 or macro_patch > 0.92:
            slot_id = "material-moss-stone-01"
        elif macro_patch < -0.28:
            slot_id = "material-packed-earth-01"
        else:
            slot_id = "material-terrace-soil-01"
        polygon.material_index = TERRAIN_TEXTURE_SLOTS.index(slot_id)
        counts[slot_id] += 1
    if any(count <= 0 for count in counts.values()):
        raise RuntimeBuildError("textured terrain material zoning is incomplete")
    terrain_obj["nv_uv_tile_scale"] = TERRAIN_TEXTURE_SCALE
    terrain_obj["nv_terrain_material_profile"] = "slope-macro-patch-v1"
    terrain_obj["nv_terrain_material_counts"] = json.dumps(
        counts,
        sort_keys=True,
        separators=(",", ":"),
    )


def _create_terrain(request, materials, auxiliary_collection):
    extent = request["scene_plan"]["extent"]
    width, depth = extent["width_m"], extent["depth_m"]
    surface_profile = (
        request.get("surface_realism_profile_id") == SURFACE_PROFILE_V1
    )
    if surface_profile:
        columns, rows, triangle_count = _surface_terrain_contract(extent)
        spacing = SURFACE_TERRAIN_SPACING_M
    else:
        spacing = 5.0
        columns = int(width / spacing) + 1
        rows = int(depth / spacing) + 1
        triangle_count = (columns - 1) * (rows - 1) * 2
    terrain = MeshAssembler()
    for row in range(rows):
        y_m = -depth / 2 + row * spacing
        for column in range(columns):
            x_m = -width / 2 + column * spacing
            terrain.vertices.append((x_m, y_m, _terrain_height(x_m, y_m, extent)))
    for row in range(rows - 1):
        for column in range(columns - 1):
            lower = row * columns + column
            terrain.faces.append(
                (lower, lower + 1, lower + columns + 1, lower + columns),
            )
    terrain_root = bpy.data.objects.new("nv__aux-terrain-root", None)
    auxiliary_collection.objects.link(terrain_root)
    terrain_root["nv_auxiliary"] = True
    terrain_root["nv_semantic_id"] = 1
    terrain_root["nv_stable_id"] = "aux-terrain"
    terrain_registry = {
        "semantic_id": 1,
        "instance_id": 0,
        "material_id": 0,
        "variant_id": None,
    }
    terrain_obj = _link_mesh(
        terrain_root,
        "terrain-4m-grid" if surface_profile else "terrain-5m-grid",
        terrain,
        materials["material-moss-stone-01"],
        terrain_registry,
        auxiliary_collection,
    )
    terrain_obj["nv_surface_grid_columns"] = columns
    terrain_obj["nv_surface_grid_rows"] = rows
    terrain_obj["nv_surface_triangle_count"] = triangle_count
    terrain_obj.name = "nv__aux-terrain"
    terrain_obj["nv_auxiliary"] = True
    for polygon in terrain_obj.data.polygons:
        polygon.use_smooth = True
    _assign_textured_terrain_materials(terrain_obj, materials)

    skirt = MeshAssembler()
    perimeter = []
    for column in range(columns):
        perimeter.append((-width / 2 + column * spacing, -depth / 2))
    for row in range(1, rows):
        perimeter.append((width / 2, -depth / 2 + row * spacing))
    for column in range(columns - 2, -1, -1):
        perimeter.append((-width / 2 + column * spacing, depth / 2))
    for row in range(rows - 2, 0, -1):
        perimeter.append((-width / 2, -depth / 2 + row * spacing))
    bottom = -8.0
    for index, (x_m, y_m) in enumerate(perimeter):
        following = perimeter[(index + 1) % len(perimeter)]
        z_m = _terrain_height(x_m, y_m, extent)
        z_next = _terrain_height(following[0], following[1], extent)
        skirt.add(
            (
                (x_m, y_m, z_m),
                (following[0], following[1], z_next),
                (following[0], following[1], bottom),
                (x_m, y_m, bottom),
            ),
            ((0, 1, 2, 3),),
        )
    support_root = bpy.data.objects.new("nv__aux-support-root", None)
    auxiliary_collection.objects.link(support_root)
    support_root["nv_auxiliary"] = True
    support_root["nv_semantic_id"] = 2
    support_root["nv_stable_id"] = "aux-support-terrain-skirt"
    support_registry = {
        "semantic_id": 2,
        "instance_id": 0,
        "material_id": 0,
        "variant_id": None,
    }
    mountains = MeshAssembler()
    for index, center_x in enumerate((-510.0, -340.0, -170.0, 0.0, 180.0, 370.0, 555.0)):
        half_width = 125.0 + (index % 3) * 18.0
        front_y = 285.0 + (index % 2) * 18.0
        back_y = front_y + 120.0
        base_z = 105.0
        peak_z = 185.0 + (index % 4) * 18.0
        mountains.add(
            (
                (center_x - half_width, front_y, base_z),
                (center_x + half_width, front_y, base_z),
                (center_x + half_width, back_y, base_z),
                (center_x - half_width, back_y, base_z),
                (center_x, (front_y + back_y) / 2, peak_z),
            ),
            (
                (0, 1, 4),
                (1, 2, 4),
                (2, 3, 4),
                (3, 0, 4),
                (0, 3, 2, 1),
            ),
        )
    forest_trunks, forest_canopies = MeshAssembler(), MeshAssembler()
    tree_index = 0
    for row_index, y_m in enumerate((205.0, 224.0, 242.0)):
        for x_m in range(-335 + row_index * 5, 336, 14):
            if row_index == 0 and -80 < x_m < 260:
                continue
            ground = _terrain_height(x_m, y_m, extent)
            tree_height = 4.2 + (tree_index % 5) * 0.55
            forest_trunks.add_cylinder(
                (x_m, y_m, ground + tree_height * 0.30),
                0.20,
                tree_height * 0.60,
                7,
            )
            forest_canopies.add_ellipsoid(
                (x_m, y_m, ground + tree_height * 0.72),
                (1.45, 1.25, tree_height * 0.36),
                7,
                4,
            )
            tree_index += 1
    mountain_start = len(skirt.faces)
    skirt.add(mountains.vertices, mountains.faces)
    trunk_start = len(skirt.faces)
    skirt.add(forest_trunks.vertices, forest_trunks.faces)
    canopy_start = len(skirt.faces)
    skirt.add(forest_canopies.vertices, forest_canopies.faces)
    support_obj = _link_mesh(
        support_root,
        "terrain-skirt",
        skirt,
        materials["material-dry-stone-wall-01"],
        support_registry,
        auxiliary_collection,
    )
    support_obj.name = "nv__aux-support-terrain-skirt"
    support_obj["nv_auxiliary"] = True
    support_obj["nv_support_components"] = json.dumps(
        sorted(["terrain-skirt", "distant-mountains", "upper-slope-forest"]),
        separators=(",", ":"),
    )
    support_obj.data.materials.append(materials["material-moss-stone-01"])
    support_obj.data.materials.append(materials["material-broadleaf-bark-01"])
    support_obj.data.materials.append(materials["material-broadleaf-canopy-01"])
    for polygon_index, polygon in enumerate(support_obj.data.polygons):
        if polygon_index >= canopy_start:
            polygon.material_index = 3
        elif polygon_index >= trunk_start:
            polygon.material_index = 2
        elif polygon_index >= mountain_start:
            polygon.material_index = 1
    return terrain_obj, support_obj


def _ribbon(points, width, lift=0.08):
    assembler = MeshAssembler()
    for first, second in zip(points, points[1:], strict=False):
        dx = second["x_m"] - first["x_m"]
        dy = second["y_m"] - first["y_m"]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            raise RuntimeBuildError("topology contains a zero-length segment")
        nx, ny = -dy / length * width / 2, dx / length * width / 2
        assembler.add(
            (
                (first["x_m"] + nx, first["y_m"] + ny, first["z_m"] + lift),
                (first["x_m"] - nx, first["y_m"] - ny, first["z_m"] + lift),
                (second["x_m"] - nx, second["y_m"] - ny, second["z_m"] + lift),
                (second["x_m"] + nx, second["y_m"] + ny, second["z_m"] + lift),
            ),
            ((0, 1, 2, 3),),
        )
    return assembler


def _surface_terrain_contract(extent):
    width = extent["width_m"]
    depth = extent["depth_m"]
    if (
        not _is_finite_number(width)
        or not _is_finite_number(depth)
        or width <= 0
        or depth <= 0
        or width % SURFACE_TERRAIN_SPACING_M != 0
        or depth % SURFACE_TERRAIN_SPACING_M != 0
    ):
        raise RuntimeBuildError("surface terrain extent is not a complete 4 m grid")
    columns = int(width / SURFACE_TERRAIN_SPACING_M) + 1
    rows = int(depth / SURFACE_TERRAIN_SPACING_M) + 1
    return columns, rows, (columns - 1) * (rows - 1) * 2


def _resample_polyline_at_most(points, *, step_m):
    if (
        not isinstance(points, list)
        or len(points) < 2
        or not _is_finite_number(step_m)
        or step_m <= 0
    ):
        raise RuntimeBuildError("surface path resampling inputs are invalid")
    source = [
        Vector((row["x_m"], row["y_m"], row["z_m"]))
        for row in points
    ]
    cumulative = [0.0]
    for first, second in zip(source, source[1:], strict=False):
        distance = math.hypot(second.x - first.x, second.y - first.y)
        if distance <= 1e-9 or not math.isfinite(distance):
            raise RuntimeBuildError("surface path contains a zero-length segment")
        cumulative.append(cumulative[-1] + distance)
    interval_count = max(1, math.ceil(cumulative[-1] / step_m))
    sampled = []
    segment = 0
    for index in range(interval_count + 1):
        distance = cumulative[-1] * index / interval_count
        while (
            segment + 1 < len(cumulative) - 1
            and cumulative[segment + 1] < distance
        ):
            segment += 1
        span = cumulative[segment + 1] - cumulative[segment]
        fraction = (distance - cumulative[segment]) / span
        position = source[segment].lerp(source[segment + 1], fraction)
        sampled.append((position.x, position.y, position.z, distance))
    return sampled


def _segment_tangent(first, second):
    tangent = Vector((second[0] - first[0], second[1] - first[1]))
    if tangent.length <= 1e-9:
        raise RuntimeBuildError("surface path contains a zero-length planar segment")
    return tangent.normalized()


def _path_miter(centerline, index, half_width):
    if index == 0:
        outgoing = _segment_tangent(centerline[0], centerline[1])
        return Vector((-outgoing.y, outgoing.x)), half_width
    if index == len(centerline) - 1:
        incoming = _segment_tangent(centerline[-2], centerline[-1])
        return Vector((-incoming.y, incoming.x)), half_width
    incoming = _segment_tangent(centerline[index - 1], centerline[index])
    outgoing = _segment_tangent(centerline[index], centerline[index + 1])
    incoming_normal = Vector((-incoming.y, incoming.x))
    outgoing_normal = Vector((-outgoing.y, outgoing.x))
    miter = incoming_normal + outgoing_normal
    if miter.length <= 1e-9:
        return outgoing_normal, half_width
    miter.normalize()
    denominator = miter.dot(outgoing_normal)
    if denominator <= 1e-6:
        return outgoing_normal, half_width
    return miter, min(half_width / denominator, half_width * 1.5)


def _surface_rut_depth(path_plan, arc_length_m, side_fraction):
    track_distance = abs(abs(side_fraction) - 0.58)
    if track_distance >= 0.18:
        return 0.0
    track_weight = 1.0 - track_distance / 0.18
    depth = 0.0
    for run in path_plan["rut_runs"]:
        progress = arc_length_m - run["start_arc_length_m"]
        if progress < 0 or progress > run["length_m"]:
            continue
        edge_distance = min(progress, run["length_m"] - progress)
        edge_weight = min(1.0, edge_distance / 1.5)
        edge_weight = edge_weight * edge_weight * (3.0 - 2.0 * edge_weight)
        depth = max(depth, run["depth_m"] * track_weight * edge_weight)
    return depth


def _surface_detail_frame(points, width, detail, extent):
    centerline = _resample_polyline_at_most(
        points,
        step_m=SURFACE_PATH_STEP_M,
    )
    arc_length_m = detail["arc_length_m"]
    if (
        not _is_finite_number(arc_length_m)
        or arc_length_m < 0
        or arc_length_m > centerline[-1][3] + 1e-6
    ):
        raise RuntimeBuildError("surface path detail arc length is invalid")
    segment = len(centerline) - 2
    for index in range(len(centerline) - 1):
        if arc_length_m <= centerline[index + 1][3] + 1e-9:
            segment = index
            break
    first = centerline[segment]
    second = centerline[segment + 1]
    span = second[3] - first[3]
    fraction = min(1.0, max(0.0, (arc_length_m - first[3]) / span))
    x_m = first[0] + (second[0] - first[0]) * fraction
    y_m = first[1] + (second[1] - first[1]) * fraction
    tangent = _segment_tangent(first, second)
    normal = Vector((-tangent.y, tangent.x))
    offset_m = detail["side_fraction"] * width / 2.0
    x_m += normal.x * offset_m
    y_m += normal.y * offset_m
    return (
        x_m,
        y_m,
        _terrain_height(x_m, y_m, extent) + 0.115,
        tangent,
        normal,
    )


def _add_leaf_diamond(assembler, center, scale, yaw_deg):
    x_m, y_m, z_m = center
    yaw = math.radians(yaw_deg)
    cosine, sine = math.cos(yaw), math.sin(yaw)
    half_length = 0.14 * scale
    half_width = 0.055 * scale
    vertices = []
    for local_x, local_y in (
        (half_length, 0.0),
        (0.0, half_width),
        (-half_length, 0.0),
        (0.0, -half_width),
    ):
        vertices.append(
            (
                x_m + local_x * cosine - local_y * sine,
                y_m + local_x * sine + local_y * cosine,
                z_m + 0.006,
            ),
        )
    assembler.add(vertices, ((0, 1, 2), (0, 2, 3)))


def _add_irregular_damp_patch(assembler, center, scale, yaw_deg, detail_id):
    x_m, y_m, z_m = center
    digest = hashlib.sha256(detail_id.encode("utf-8")).digest()
    yaw = math.radians(yaw_deg)
    vertices = [(x_m, y_m, z_m + 0.003)]
    for index in range(8):
        angle = yaw + 2.0 * math.pi * index / 8.0
        radius_weight = 0.78 + digest[index] / 255.0 * 0.22
        radius_x = 0.31 * scale * radius_weight
        radius_y = 0.20 * scale * radius_weight
        vertices.append(
            (
                x_m + radius_x * math.cos(angle),
                y_m + radius_y * math.sin(angle),
                z_m + 0.003,
            ),
        )
    assembler.add(
        vertices,
        tuple((0, 1 + index, 1 + (index + 1) % 8) for index in range(8)),
    )


def _surface_detail_assemblers(points, width, path_plan, extent):
    assemblers = {
        "damp-patch": MeshAssembler(),
        "leaf-card": MeshAssembler(),
        "stone-fragment": MeshAssembler(),
    }
    counts = {detail_class: 0 for detail_class in assemblers}
    for detail in path_plan["details"]:
        detail_class = detail["detail_class"]
        if detail_class not in assemblers:
            raise RuntimeBuildError("surface path detail class is invalid")
        x_m, y_m, z_m, _tangent, _normal = _surface_detail_frame(
            points,
            width,
            detail,
            extent,
        )
        scale = detail["scale"]
        if detail_class == "stone-fragment":
            assemblers[detail_class].add_ellipsoid(
                (x_m, y_m, z_m + 0.08 * scale),
                (0.18 * scale, 0.12 * scale, 0.08 * scale),
                segments=7,
                rings=3,
            )
        elif detail_class == "leaf-card":
            _add_leaf_diamond(
                assemblers[detail_class],
                (x_m, y_m, z_m),
                scale,
                detail["yaw_deg"],
            )
        else:
            _add_irregular_damp_patch(
                assemblers[detail_class],
                (x_m, y_m, z_m),
                scale,
                detail["yaw_deg"],
                detail["detail_id"],
            )
        counts[detail_class] += 1
    return assemblers, counts


def _surface_path_ribbon(points, width, path_plan, extent):
    centerline = _resample_polyline_at_most(
        points,
        step_m=path_plan["longitudinal_step_m"],
    )
    rails = path_plan["lateral_rail_count"]
    if rails != SURFACE_PATH_LATERAL_RAILS:
        raise RuntimeBuildError("surface path lateral rail count is invalid")
    assembler = MeshAssembler()
    half_width = width / 2.0
    for index, point in enumerate(centerline):
        miter, miter_length = _path_miter(centerline, index, half_width)
        for rail in range(rails):
            fraction = -1.0 + 2.0 * rail / (rails - 1)
            x_m = point[0] + miter.x * miter_length * fraction
            y_m = point[1] + miter.y * miter_length * fraction
            z_m = _terrain_height(x_m, y_m, extent) + 0.10
            z_m -= _surface_rut_depth(path_plan, point[3], fraction)
            assembler.vertices.append((x_m, y_m, z_m))
    for row in range(len(centerline) - 1):
        for rail in range(rails - 1):
            lower = row * rails + rail
            assembler.faces.append(
                (lower, lower + 1, lower + rails + 1, lower + rails),
            )
    return assembler, len(centerline) - 1


def _polygon_surface(ring, extent, lift=0.08):
    points = ring[:-1] if ring and ring[0] == ring[-1] else ring
    if len(points) < 3:
        raise RuntimeBuildError("polygon topology has fewer than three points")
    assembler = MeshAssembler()
    min_x = min(point["x_m"] for point in points)
    max_x = max(point["x_m"] for point in points)
    min_y = min(point["y_m"] for point in points)
    max_y = max(point["y_m"] for point in points)
    columns = max(2, math.ceil((max_x - min_x) / 5.0) + 1)
    rows = max(2, math.ceil((max_y - min_y) / 5.0) + 1)
    for row in range(rows):
        y_m = min_y + (max_y - min_y) * row / (rows - 1)
        for column in range(columns):
            x_m = min_x + (max_x - min_x) * column / (columns - 1)
            assembler.vertices.append(
                (x_m, y_m, _terrain_height(x_m, y_m, extent) + lift),
            )
    for row in range(rows - 1):
        for column in range(columns - 1):
            lower = row * columns + column
            assembler.faces.append(
                (lower, lower + 1, lower + columns + 1, lower + columns),
            )
    return assembler


def _facade_box(
    assembler,
    elevation,
    wall_width,
    wall_depth,
    center_u,
    center_z,
    size_u,
    size_z,
    thickness,
    offset,
):
    if elevation == "front":
        center = (center_u, -wall_depth / 2 - offset, center_z)
        size = (size_u, thickness, size_z)
    elif elevation == "rear":
        center = (center_u, wall_depth / 2 + offset, center_z)
        size = (size_u, thickness, size_z)
    elif elevation == "left":
        center = (-wall_width / 2 - offset, center_u, center_z)
        size = (thickness, size_u, size_z)
    elif elevation == "right":
        center = (wall_width / 2 + offset, center_u, center_z)
        size = (thickness, size_u, size_z)
    else:
        raise RuntimeBuildError(f"unsupported building elevation: {elevation}")
    assembler.add_box(center, size)


def _facade_quad(
    assembler,
    elevation,
    wall_width,
    wall_depth,
    center_u,
    center_z,
    size_u,
    size_z,
    offset,
):
    u0, u1 = center_u - size_u / 2, center_u + size_u / 2
    z0, z1 = center_z - size_z / 2, center_z + size_z / 2
    if elevation == "front":
        y = -wall_depth / 2 - offset
        vertices = ((u0, y, z0), (u1, y, z0), (u1, y, z1), (u0, y, z1))
    elif elevation == "rear":
        y = wall_depth / 2 + offset
        vertices = ((u0, y, z0), (u0, y, z1), (u1, y, z1), (u1, y, z0))
    elif elevation == "left":
        x = -wall_width / 2 - offset
        vertices = ((x, u0, z0), (x, u0, z1), (x, u1, z1), (x, u1, z0))
    elif elevation == "right":
        x = wall_width / 2 + offset
        vertices = ((x, u0, z0), (x, u1, z0), (x, u1, z1), (x, u0, z1))
    else:
        raise RuntimeBuildError(f"unsupported building elevation: {elevation}")
    assembler.add(vertices, ((0, 1, 2, 3),))


def _add_window_assembly(
    assembler,
    elevation,
    wall_width,
    wall_depth,
    center_u,
    center_z,
    *,
    include_panel=True,
    include_muntins=True,
):
    start = len(assembler.faces)
    window_width, window_height, rail = 1.05, 1.15, 0.10
    if include_panel:
        _facade_box(
            assembler,
            elevation,
            wall_width,
            wall_depth,
            center_u,
            center_z,
            window_width,
            window_height,
            0.04,
            0.03,
        )
    frame_offset = 0.085 if include_panel else 0.22
    verticals = [
        center_u - window_width / 2 + rail / 2,
        center_u + window_width / 2 - rail / 2,
    ]
    horizontals = [
        center_z - window_height / 2 + rail / 2,
        center_z + window_height / 2 - rail / 2,
    ]
    if include_muntins:
        verticals.append(center_u)
        horizontals.append(center_z)
    for u_value in verticals:
        _facade_quad(
            assembler,
            elevation,
            wall_width,
            wall_depth,
            u_value,
            center_z,
            rail,
            window_height,
            frame_offset,
        )
    for z_value in horizontals:
        _facade_quad(
            assembler,
            elevation,
            wall_width,
            wall_depth,
            center_u,
            z_value,
            window_width,
            rail,
            frame_offset,
        )
    return len(assembler.faces) - start


def _add_door_assembly(
    assembler,
    elevation,
    wall_width,
    wall_depth,
    center_u,
    base_z,
    *,
    width=1.35,
    include_panel=True,
):
    start = len(assembler.faces)
    height, rail = 2.30, 0.10
    center_z = base_z + height / 2
    if include_panel:
        _facade_box(
            assembler,
            elevation,
            wall_width,
            wall_depth,
            center_u,
            center_z,
            width,
            height,
            0.05,
            0.035,
        )
    frame_offset = 0.095 if include_panel else 0.16
    for u_value in (
        center_u - width / 2 + rail / 2,
        center_u + width / 2 - rail / 2,
        center_u - width / 6,
        center_u + width / 6,
    ):
        _facade_quad(
            assembler,
            elevation,
            wall_width,
            wall_depth,
            u_value,
            center_z,
            rail,
            height,
            frame_offset,
        )
    for z_value in (
        base_z + rail / 2,
        base_z + height - rail / 2,
        base_z + height * 0.58,
    ):
        _facade_quad(
            assembler,
            elevation,
            wall_width,
            wall_depth,
            center_u,
            z_value,
            width,
            rail,
            frame_offset,
        )
    return len(assembler.faces) - start


def _add_sloped_roof_board(
    assembler,
    *,
    x_center,
    y_start,
    z_start,
    y_end,
    z_end,
    width_x=0.16,
    thickness=0.14,
):
    dy, dz = y_end - y_start, z_end - z_start
    length = math.hypot(dy, dz)
    if length <= 1e-9:
        raise RuntimeBuildError("roof edge board has zero length")
    px = width_x / 2
    py = -dz / length * thickness / 2
    pz = dy / length * thickness / 2
    vertices = (
        (x_center - px, y_start - py, z_start - pz),
        (x_center + px, y_start - py, z_start - pz),
        (x_center + px, y_start + py, z_start + pz),
        (x_center - px, y_start + py, z_start + pz),
        (x_center - px, y_end - py, z_end - pz),
        (x_center + px, y_end - py, z_end - pz),
        (x_center + px, y_end + py, z_end + pz),
        (x_center - px, y_end + py, z_end + pz),
    )
    assembler.add(
        vertices,
        (
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (3, 7, 4, 0),
        ),
    )


def _build_building(
    item,
    root,
    registry,
    materials,
    collection,
    building_geometry_profile,
):
    dimensions = item["dimensions"]
    width = dimensions["width_m"]
    depth = dimensions["depth_m"]
    height = dimensions["height_m"]
    base_z = item["base_z_m"]
    wall_height = max(3.2, height * 0.70)
    eave_z = base_z + wall_height
    ridge_z = base_z + height
    building_variant = _building_variant(
        item["object_id"],
        building_geometry_profile,
    )
    is_v2 = building_geometry_profile == BUILDING_GEOMETRY_V2
    added_faces = 0

    base = MeshAssembler()
    base.add_box((0.0, 0.0, base_z + 0.28), (width + 0.7, depth + 0.7, 0.56))
    if is_v2:
        before = len(base.faces)
        base.add_box(
            (0.0, 0.0, base_z + 0.42),
            (width + 0.18, depth + 0.18, 0.84),
        )
        added_faces += len(base.faces) - before
    _link_mesh(
        root,
        "stone-platform",
        base,
        materials["material-fieldstone-01"],
        registry,
        collection,
    )

    walls = MeshAssembler()
    walls.add_box((0.0, 0.0, base_z + wall_height / 2), (width, depth, wall_height))
    _link_mesh(
        root,
        "walls",
        walls,
        materials[FAMILY_TO_SLOT[item["material_family"]]],
        registry,
        collection,
    )

    roof = MeshAssembler()
    roof.add_gabled_roof(width, depth, eave_z, ridge_z)
    roof.add_cylinder((0.0, 0.0, ridge_z + 0.10), 0.13, width + 1.2, 8, axis="x")
    for y_value in (-depth / 2 - 0.44, depth / 2 + 0.44):
        roof.add_box((0.0, y_value, eave_z - 0.04), (width + 1.15, 0.16, 0.20))
    if is_v2:
        before = len(roof.faces)
        for y_value in (-depth / 2 - 0.36, depth / 2 + 0.36):
            roof.add_box(
                (0.0, y_value, eave_z - 0.17),
                (width + 1.05, 0.42, 0.10),
            )
        half_width = width / 2 + 0.56
        half_depth = depth / 2 + 0.55
        for x_value in (-half_width, half_width):
            _add_sloped_roof_board(
                roof,
                x_center=x_value,
                y_start=-half_depth,
                z_start=eave_z,
                y_end=0.0,
                z_end=ridge_z,
            )
            _add_sloped_roof_board(
                roof,
                x_center=x_value,
                y_start=0.0,
                z_start=ridge_z,
                y_end=half_depth,
                z_end=eave_z,
            )
        added_faces += len(roof.faces) - before
    _link_mesh(
        root,
        "tiled-gabled-roof-ridge-eaves",
        roof,
        materials["material-gray-roof-tile-01"],
        registry,
        collection,
    )

    timber = MeshAssembler()
    beam = 0.18
    for x_value in (-width / 2 + beam, width / 2 - beam):
        timber.add_box(
            (x_value, -depth / 2 - 0.04, base_z + wall_height / 2),
            (beam, 0.18, wall_height),
        )
    timber.add_box(
        (0.0, -depth / 2 - 0.05, eave_z - 0.25),
        (width - 0.3, 0.20, 0.20),
    )
    if is_v2:
        before = len(timber.faces)
        for x_value in (-width / 2 + beam, width / 2 - beam):
            timber.add_box(
                (x_value, depth / 2 + 0.04, base_z + wall_height / 2),
                (beam, 0.18, wall_height),
            )
        timber.add_box(
            (0.0, depth / 2 + 0.05, eave_z - 0.25),
            (width - 0.3, 0.20, 0.20),
        )
        for x_value in (-width / 2 - 0.05, width / 2 + 0.05):
            timber.add_box(
                (x_value, 0.0, eave_z - 0.25),
                (0.20, depth - 0.3, 0.20),
            )
        added_faces += len(timber.faces) - before
    _link_mesh(
        root,
        "timber-frame",
        timber,
        materials["material-dark-timber-01"],
        registry,
        collection,
    )

    door = MeshAssembler()
    door_width = min(1.45, width * 0.20)
    door.add_box(
        (0.0, -depth / 2 - 0.075, base_z + 1.15),
        (door_width, 0.14, 2.30),
    )
    if is_v2:
        added_faces += _add_door_assembly(
            door,
            "front",
            width,
            depth,
            0.0,
            base_z,
            width=door_width,
            include_panel=False,
        )
        if building_variant == "side-entry-workshop":
            added_faces += _add_door_assembly(
                door,
                "left",
                width,
                depth,
                0.0,
                base_z,
                width=door_width,
            )
        elif building_variant == "rear-service-house":
            added_faces += _add_door_assembly(
                door,
                "rear",
                width,
                depth,
                0.0,
                base_z,
                width=door_width,
            )
    _link_mesh(
        root,
        "timber-door",
        door,
        materials["material-weathered-timber-01"],
        registry,
        collection,
    )

    windows = MeshAssembler()
    window_offset = width * 0.28
    for x_value in (-window_offset, window_offset):
        windows.add_box(
            (x_value, -depth / 2 - 0.09, base_z + wall_height * 0.56),
            (1.05, 0.12, 1.15),
        )
        windows.add_box(
            (x_value, -depth / 2 - 0.17, base_z + wall_height * 0.56),
            (0.10, 0.08, 1.20),
        )
        windows.add_box(
            (x_value, -depth / 2 - 0.17, base_z + wall_height * 0.56),
            (1.10, 0.08, 0.10),
        )
    if is_v2:
        window_z = base_z + wall_height * 0.56
        for x_value in (-window_offset, window_offset):
            added_faces += _add_window_assembly(
                windows,
                "front",
                width,
                depth,
                x_value,
                window_z,
                include_panel=False,
                include_muntins=False,
            )
        if building_variant in {"balanced-residence", "side-entry-workshop"}:
            added_faces += _add_window_assembly(
                windows,
                "rear",
                width,
                depth,
                0.0,
                window_z,
            )
        if building_variant in {"balanced-residence", "rear-service-house"}:
            added_faces += _add_window_assembly(
                windows,
                "left",
                width,
                depth,
                0.0,
                window_z,
            )
        added_faces += _add_window_assembly(
            windows,
            "right",
            width,
            depth,
            0.0,
            window_z,
        )
    _link_mesh(
        root,
        "two-latticed-windows",
        windows,
        materials["material-dark-timber-01"],
        registry,
        collection,
    )

    if is_v2:
        if building_variant not in BUILDING_VARIANTS:
            raise RuntimeBuildError("v2 building variant was not derived")
        if not 1 <= added_faces <= MAX_ADDED_BUILDING_FACES:
            raise RuntimeBuildError(
                f"building geometry face budget exceeded: {item['object_id']}",
            )
        root["nv_building_geometry_profile"] = BUILDING_GEOMETRY_V2
        root["nv_building_variant"] = building_variant
        root["nv_facade_elevations"] = json.dumps(
            BUILDING_ELEVATIONS,
            separators=(",", ":"),
        )
        root["nv_added_face_count"] = added_faces

    if item.get("building_role") == "community-hall":
        porch = MeshAssembler()
        porch_depth = 2.4
        porch.add_box(
            (0.0, -depth / 2 - porch_depth / 2, base_z + 0.18),
            (width * 0.82, porch_depth, 0.36),
        )
        for x_value in (-width * 0.32, 0.0, width * 0.32):
            porch.add_box(
                (x_value, -depth / 2 - porch_depth + 0.25, base_z + 1.65),
                (0.24, 0.24, 3.30),
            )
        porch.add_box(
            (0.0, -depth / 2 - porch_depth + 0.25, base_z + 3.25),
            (width * 0.82, 0.28, 0.24),
        )
        _link_mesh(
            root,
            "community-hall-porch",
            porch,
            materials["material-dark-timber-01"],
            registry,
            collection,
        )


def _build_bridge(item, root, registry, materials, collection):
    dimensions = item["dimensions"]
    width, depth, height = (
        dimensions["width_m"],
        dimensions["depth_m"],
        dimensions["height_m"],
    )
    center_z = item["transform"]["z_m"]
    stone = MeshAssembler()
    stone.add_box((0.0, 0.0, center_z), (width, depth, height * 0.45))
    for y_value in (-depth / 2 + 0.18, depth / 2 - 0.18):
        stone.add_box(
            (0.0, y_value, center_z + height * 0.48),
            (width, 0.32, height * 0.55),
        )
    for x_value in (-width / 2 + 1.0, 0.0, width / 2 - 1.0):
        stone.add_box((x_value, 0.0, center_z - height * 0.42), (0.7, depth, height * 0.8))
    _link_mesh(
        root,
        "stone-deck-parapets-piers",
        stone,
        materials["material-fieldstone-01"],
        registry,
        collection,
    )


def _build_area_feature(item, root, registry, materials, collection, extent):
    semantic = item["semantic_class"]
    ring = item["polygon"]["ring"]
    slot = FAMILY_TO_SLOT[item["material_family"]]
    surface = _polygon_surface(ring, extent)
    surface_obj = _link_mesh(
        root,
        "terrain-conform-surface",
        surface,
        materials[slot],
        registry,
        collection,
    )
    for polygon in surface_obj.data.polygons:
        polygon.use_smooth = True
    surface_obj["nv_surface_lift_m"] = 0.08
    surface_obj["nv_terrain_error_tolerance_m"] = 0.0011

    points = ring[:-1]
    if semantic == "field":
        index = int(item["object_id"].rsplit("-", 1)[1])
        xs = [point["x_m"] for point in points]
        ys = [point["y_m"] for point in points]
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        crop = MeshAssembler()
        style = (index - 1) % 3
        if style == 0:
            inset_ring = [
                {"x_m": min_x + 1.0, "y_m": min_y + 1.0},
                {"x_m": max_x - 1.0, "y_m": min_y + 1.0},
                {"x_m": max_x - 1.0, "y_m": max_y - 1.0},
                {"x_m": min_x + 1.0, "y_m": max_y - 1.0},
                {"x_m": min_x + 1.0, "y_m": min_y + 1.0},
            ]
            water = _polygon_surface(inset_ring, extent, lift=0.16)
            crop.add(water.vertices, water.faces)
            crop_slot = "material-rice-paddy-water-01"
        else:
            row_count = 5 + index % 4
            for row_index in range(row_count):
                y_value = min_y + 2.2 + (max_y - min_y - 4.4) * row_index / max(1, row_count - 1)
                row_ribbon = _ribbon(
                    (
                        {
                            "x_m": min_x + 1.5,
                            "y_m": y_value,
                            "z_m": _terrain_height(min_x + 1.5, y_value, extent),
                        },
                        {
                            "x_m": max_x - 1.5,
                            "y_m": y_value,
                            "z_m": _terrain_height(max_x - 1.5, y_value, extent),
                        },
                    ),
                    0.48 if style == 1 else 0.28,
                    0.24,
                )
                crop.add(row_ribbon.vertices, row_ribbon.faces)
            crop_slot = "material-vegetable-leaf-01" if style == 1 else "material-packed-earth-01"
        crop_obj = _link_mesh(
            root,
            f"terrace-style-{index:02d}",
            crop,
            materials[crop_slot],
            registry,
            collection,
        )
        crop_obj["nv_field_style"] = f"terrace-style-{index:02d}"
        levees = _ribbon(ring, 0.55, 0.18)
        _link_mesh(
            root,
            "terrace-levees",
            levees,
            materials["material-packed-earth-01"],
            registry,
            collection,
        )
    elif semantic == "pond":
        border = _ribbon(ring, 0.55, 0.20)
        _link_mesh(
            root,
            "fieldstone-bank",
            border,
            materials["material-fieldstone-01"],
            registry,
            collection,
        )
    elif semantic == "courtyard":
        paving = MeshAssembler()
        center_x = sum(point["x_m"] for point in points) / len(points)
        center_y = sum(point["y_m"] for point in points) / len(points)
        for offset in (-6.0, -2.0, 2.0, 6.0):
            x_m = center_x + offset
            joint = _ribbon(
                (
                    {
                        "x_m": x_m,
                        "y_m": center_y - 8.0,
                        "z_m": _terrain_height(x_m, center_y - 8.0, extent),
                    },
                    {
                        "x_m": x_m,
                        "y_m": center_y + 8.0,
                        "z_m": _terrain_height(x_m, center_y + 8.0, extent),
                    },
                ),
                0.10,
                0.16,
            )
            paving.add(joint.vertices, joint.faces)
        _link_mesh(
            root,
            "paving-joints",
            paving,
            materials["material-moss-stone-01"],
            registry,
            collection,
        )
        amenity_stone = MeshAssembler()
        amenity_x, amenity_y = center_x + 5.8, center_y + 3.8
        amenity_z = _terrain_height(amenity_x, amenity_y, extent)
        amenity_stone.add_box(
            (amenity_x, amenity_y, amenity_z + 0.22),
            (2.1, 2.1, 0.44),
        )
        _link_mesh(
            root,
            "courtyard-planter",
            amenity_stone,
            materials["material-fieldstone-01"],
            registry,
            collection,
        )
        amenity_tree = MeshAssembler()
        amenity_tree.add_cylinder(
            (amenity_x, amenity_y, amenity_z + 1.65),
            0.22,
            2.8,
            8,
        )
        amenity_tree.add_ellipsoid(
            (amenity_x, amenity_y, amenity_z + 3.8),
            (1.7, 1.55, 1.65),
            9,
            4,
        )
        _link_mesh(
            root,
            "courtyard-shade-tree",
            amenity_tree,
            materials["material-broadleaf-canopy-01"],
            registry,
            collection,
        )
    elif semantic == "orchard":
        trunks, canopy = MeshAssembler(), MeshAssembler()
        xs = [point["x_m"] for point in points]
        ys = [point["y_m"] for point in points]
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        for row_index in range(3):
            for column_index in range(4):
                x_m = min_x + 6.0 + column_index * (max_x - min_x - 12.0) / 3
                y_m = min_y + 6.0 + row_index * (max_y - min_y - 12.0) / 2
                ground = _terrain_height(x_m, y_m, extent)
                trunks.add_cylinder((x_m, y_m, ground + 1.35), 0.22, 2.7, 8)
                canopy.add_ellipsoid((x_m, y_m, ground + 3.5), (1.7, 1.55, 1.45), 8, 4)
        _link_mesh(
            root,
            "orchard-trunks",
            trunks,
            materials["material-orchard-bark-01"],
            registry,
            collection,
        )
        _link_mesh(
            root,
            "orchard-canopies",
            canopy,
            materials["material-orchard-leaf-01"],
            registry,
            collection,
        )
    elif semantic == "bamboo":
        stems, leaves = MeshAssembler(), MeshAssembler()
        center_x = sum(point["x_m"] for point in points) / len(points)
        center_y = sum(point["y_m"] for point in points) / len(points)
        for stem_index in range(20):
            column = stem_index % 5
            row_index = stem_index // 5
            x_m = center_x - 9.0 + column * 4.5 + (row_index % 2) * 0.7
            y_m = center_y - 7.0 + row_index * 4.5
            ground = _terrain_height(x_m, y_m, extent)
            stem_height = 7.0 + (stem_index % 5) * 0.65
            stems.add_cylinder((x_m, y_m, ground + stem_height / 2), 0.11, stem_height, 7)
            leaves.add_ellipsoid(
                (x_m, y_m, ground + stem_height - 0.8),
                (0.9, 0.55, 1.4),
                6,
                3,
            )
        _link_mesh(
            root,
            "bamboo-stems",
            stems,
            materials["material-bamboo-stem-01"],
            registry,
            collection,
        )
        _link_mesh(
            root,
            "bamboo-leaves",
            leaves,
            materials["material-bamboo-leaf-01"],
            registry,
            collection,
        )


def _build_linear_feature(
    item,
    root,
    registry,
    materials,
    collection,
    extent,
    request,
):
    semantic = item["semantic_class"]
    topology = item["polyline"]
    if semantic == "retaining-wall":
        wall = MeshAssembler()
        for first, second in zip(
            topology["points"],
            topology["points"][1:],
            strict=False,
        ):
            dx, dy = second["x_m"] - first["x_m"], second["y_m"] - first["y_m"]
            length = math.hypot(dx, dy)
            wall.add_box(
                (
                    (first["x_m"] + second["x_m"]) / 2,
                    (first["y_m"] + second["y_m"]) / 2,
                    (first["z_m"] + second["z_m"]) / 2 + item["dimensions"]["height_m"] / 2,
                ),
                (length, topology["width_m"], item["dimensions"]["height_m"]),
                math.atan2(dy, dx),
            )
        _link_mesh(
            root,
            "terrain-conform-stone-wall",
            wall,
            materials["material-dry-stone-wall-01"],
            registry,
            collection,
        )
        return
    if (
        semantic == "path"
        and request.get("surface_realism_profile_id") == SURFACE_PROFILE_V1
    ):
        path_plans = {
            row["object_id"]: row
            for row in request["surface_realism_plan"]["path_plans"]
        }
        path_plan = path_plans.get(item["object_id"])
        if path_plan is None:
            raise RuntimeBuildError("surface path plan is absent")
        ribbon, interval_count = _surface_path_ribbon(
            topology["points"],
            topology["width_m"],
            path_plan,
            extent,
        )
        root["nv_surface_path_interval_count"] = interval_count
        root["nv_surface_path_triangle_count"] = (
            interval_count * (SURFACE_PATH_LATERAL_RAILS - 1) * 2
        )
        root["nv_surface_rut_run_count"] = len(path_plan["rut_runs"])
        surface_details, surface_detail_counts = _surface_detail_assemblers(
            topology["points"],
            topology["width_m"],
            path_plan,
            extent,
        )
    else:
        ribbon = _ribbon(
            topology["points"],
            topology["width_m"],
            0.13 if semantic == "creek" else 0.10,
        )
    _link_mesh(
        root,
        "terrain-conform-ribbon",
        ribbon,
        materials[FAMILY_TO_SLOT[item["material_family"]]],
        registry,
        collection,
    )
    if (
        semantic == "path"
        and request.get("surface_realism_profile_id") == SURFACE_PROFILE_V1
    ):
        detail_materials = {
            "damp-patch": "material-packed-earth-01",
            "leaf-card": "material-broadleaf-canopy-01",
            "stone-fragment": "material-creek-rock-01",
        }
        for detail_class, assembler in surface_details.items():
            detail_obj = _link_mesh(
                root,
                f"surface-{detail_class}",
                assembler,
                materials[detail_materials[detail_class]],
                registry,
                collection,
            )
            detail_obj["nv_surface_detail_class"] = detail_class
            detail_obj["nv_surface_detail_count"] = surface_detail_counts[detail_class]
    if semantic == "creek":
        rocks = MeshAssembler()
        for segment_index, point in enumerate(topology["points"][::2]):
            for side in (-1, 1):
                rocks.add_ellipsoid(
                    (
                        point["x_m"] + side * (topology["width_m"] / 2 + 0.7),
                        point["y_m"],
                        _terrain_height(
                            point["x_m"] + side * (topology["width_m"] / 2 + 0.7),
                            point["y_m"],
                            extent,
                        )
                        + 0.24,
                    ),
                    (0.55 + segment_index % 3 * 0.12, 0.42, 0.28),
                    7,
                    3,
                )
        _link_mesh(
            root,
            "creek-bank-rocks",
            rocks,
            materials["material-creek-rock-01"],
            registry,
            collection,
        )
    elif semantic == "path":
        vegetation = MeshAssembler()
        points = topology["points"]
        for index in range(0, len(points) - 1, 2):
            point, following = points[index], points[index + 1]
            dx = following["x_m"] - point["x_m"]
            dy = following["y_m"] - point["y_m"]
            length = math.hypot(dx, dy)
            nx, ny = -dy / length, dx / length
            for side in (-1.0, 1.0):
                x_m = point["x_m"] + side * nx * (topology["width_m"] / 2 + 1.0)
                y_m = point["y_m"] + side * ny * (topology["width_m"] / 2 + 1.0)
                vegetation.add_ellipsoid(
                    (x_m, y_m, _terrain_height(x_m, y_m, extent) + 0.48),
                    (0.44, 0.38, 0.58),
                    7,
                    3,
                )
        _link_mesh(
            root,
            "roadside-vegetation",
            vegetation,
            materials["material-broadleaf-canopy-01"],
            registry,
            collection,
        )


def _build_prop(item, root, registry, materials, collection):
    variant = registry["variant_id"]
    dimensions = item["dimensions"]
    width, depth, height = dimensions["width_m"], dimensions["depth_m"], dimensions["height_m"]
    base_z = item["transform"]["z_m"] - height / 2
    wood, accent = MeshAssembler(), MeshAssembler()
    wood_slot = "material-weathered-timber-01"
    accent_slot = "material-aged-metal-01"
    if variant == "water-jar":
        wood.add_cylinder((0.0, 0.0, base_z + 0.62), 0.48, 1.05, 12, radius_top=0.34)
        wood.add_cylinder((0.0, 0.0, base_z + 1.20), 0.23, 0.25, 12)
        wood_slot = "material-clay-brick-01"
    elif variant == "firewood-stack":
        for row_index in range(3):
            for column_index in range(4):
                wood.add_cylinder(
                    (
                        0.0,
                        -0.48 + column_index * 0.32,
                        base_z + 0.18 + row_index * 0.30,
                    ),
                    0.13,
                    1.45 - (column_index % 2) * 0.12,
                    8,
                    axis="x",
                )
    elif variant == "bamboo-basket":
        wood.add_cylinder((0.0, 0.0, base_z + 0.48), 0.58, 0.90, 12, radius_top=0.43)
        wood.add_box((-0.48, 0.0, base_z + 1.05), (0.10, 0.10, 0.9))
        wood.add_box((0.48, 0.0, base_z + 1.05), (0.10, 0.10, 0.9))
        wood.add_box((0.0, 0.0, base_z + 1.48), (1.0, 0.10, 0.10))
        wood_slot = "material-woven-bamboo-01"
    elif variant == "wooden-bench":
        wood.add_box((0.0, 0.0, base_z + 0.8), (width, depth * 0.55, 0.20))
        wood.add_box((0.0, depth * 0.30, base_z + 1.22), (width, 0.16, 0.75))
        for x_value in (-width * 0.38, width * 0.38):
            wood.add_box((x_value, 0.0, base_z + 0.40), (0.18, 0.40, 0.8))
    elif variant == "farming-tools":
        wood.add_cylinder((-0.22, 0.0, base_z + 0.85), 0.055, 1.65, 7)
        wood.add_cylinder((0.26, 0.0, base_z + 0.80), 0.055, 1.55, 7)
        accent.add_box((-0.22, 0.0, base_z + 0.12), (0.55, 0.12, 0.14))
        accent.add_box((0.26, 0.0, base_z + 0.09), (0.14, 0.56, 0.10))
    elif variant == "grain-rack":
        for x_value in (-width * 0.42, width * 0.42):
            for y_value in (-depth * 0.34, depth * 0.34):
                wood.add_box((x_value, y_value, base_z + 0.85), (0.12, 0.12, 1.70))
        for z_value in (base_z + 0.42, base_z + 0.90, base_z + 1.38):
            wood.add_box((0.0, 0.0, z_value), (width * 0.92, depth * 0.72, 0.10))
    elif variant == "stone-trough":
        wood_slot = "material-fieldstone-01"
        wood.add_box((0.0, 0.0, base_z + 0.18), (width, depth, 0.36))
        wood.add_box((0.0, -depth * 0.42, base_z + 0.62), (width, 0.16, 0.88))
        wood.add_box((0.0, depth * 0.42, base_z + 0.62), (width, 0.16, 0.88))
        wood.add_box((-width * 0.43, 0.0, base_z + 0.62), (0.16, depth, 0.88))
        wood.add_box((width * 0.43, 0.0, base_z + 0.62), (0.16, depth, 0.88))
    elif variant == "handcart":
        wood.add_box((0.0, 0.0, base_z + 0.9), (width * 0.75, depth * 0.75, 0.55))
        wood.add_box((width * 0.65, 0.0, base_z + 0.65), (width * 0.75, 0.12, 0.12))
        for y_value in (-depth * 0.48, depth * 0.48):
            accent.add_cylinder((0.0, y_value, base_z + 0.55), 0.48, 0.16, 12, axis="y")
    else:
        raise RuntimeBuildError(f"unsupported prop variant: {variant}")
    _link_mesh(root, f"{variant}-body", wood, materials[wood_slot], registry, collection)
    if accent.vertices:
        _link_mesh(root, f"{variant}-accent", accent, materials[accent_slot], registry, collection)


def _add_segment_prism(assembler, start, end, width, thickness, *, z_offset=0.0):
    start_vector = Vector((start[0], start[1], start[2] + z_offset))
    end_vector = Vector((end[0], end[1], end[2] + z_offset))
    horizontal = Vector(
        (
            end_vector.x - start_vector.x,
            end_vector.y - start_vector.y,
            0.0,
        ),
    )
    if horizontal.length <= 1e-6:
        raise RuntimeBuildError("elevated geometry contains a zero-length segment")
    side = Vector((-horizontal.y, horizontal.x, 0.0)).normalized() * (width / 2)
    vertical = Vector((0.0, 0.0, thickness / 2))
    vertices = (
        start_vector - side - vertical,
        start_vector + side - vertical,
        end_vector + side - vertical,
        end_vector - side - vertical,
        start_vector - side + vertical,
        start_vector + side + vertical,
        end_vector + side + vertical,
        end_vector - side + vertical,
    )
    assembler.add(
        vertices,
        (
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (3, 7, 6, 2),
            (0, 4, 7, 3),
            (1, 2, 6, 5),
        ),
    )


def _add_side_rails(assembler, start, end, width, height):
    horizontal = Vector((end[0] - start[0], end[1] - start[1], 0.0))
    if horizontal.length <= 1e-6:
        raise RuntimeBuildError("elevated rail contains a zero-length segment")
    side = Vector((-horizontal.y, horizontal.x, 0.0)).normalized() * (width / 2)
    for sign in (-1.0, 1.0):
        offset = side * sign
        rail_start = (
            start[0] + offset.x,
            start[1] + offset.y,
            start[2] + height,
        )
        rail_end = (
            end[0] + offset.x,
            end[1] + offset.y,
            end[2] + height,
        )
        _add_segment_prism(
            assembler,
            rail_start,
            rail_end,
            0.12,
            0.12,
        )
        for point in (start, end):
            assembler.add_box(
                (
                    point[0] + offset.x,
                    point[1] + offset.y,
                    point[2] + height / 2,
                ),
                (0.14, 0.14, height),
            )


def _add_supports(assembler, points, extent, deck_thickness):
    for point in points:
        terrain = _terrain_height(point[0], point[1], extent)
        bottom = terrain + 0.08
        top = point[2] - deck_thickness / 2
        height = top - bottom
        if height < 0.35:
            continue
        assembler.add_box(
            (point[0], point[1], bottom + height / 2),
            (0.30, 0.30, height),
        )


def _add_stair_treads(assembler, start, end, width):
    dx, dy = end[0] - start[0], end[1] - start[1]
    horizontal_length = math.hypot(dx, dy)
    if horizontal_length <= 1e-6:
        raise RuntimeBuildError("elevated stair contains a zero-length segment")
    step_count = max(2, math.ceil(horizontal_length / 0.85))
    yaw = math.atan2(dy, dx)
    tread_length = horizontal_length / step_count + 0.04
    for index in range(step_count):
        fraction = (index + 0.5) / step_count
        top_z = start[2] + (end[2] - start[2]) * (index + 1) / step_count
        assembler.add_box(
            (
                start[0] + dx * fraction,
                start[1] + dy * fraction,
                top_z - 0.11,
            ),
            (tread_length, width, 0.22),
            yaw,
        )


def _new_elevated_root(component, topology, registry, collection):
    identifier = component["component_id"]
    root = bpy.data.objects.new(f"nv__{identifier}", None)
    collection.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 1.0
    root["nv_root"] = True
    root["nv_semantic_class"] = "elevated-walkway"
    root["nv_component_kind"] = component["component_kind"]
    root["nv_source_transform"] = json.dumps(
        {
            "coordinate_system": topology["coordinate_system"],
            "scene_plan_sha256": topology["scene_plan_sha256"],
        },
        sort_keys=True,
    )
    root["nv_components"] = "[]"
    root["nv_walkable_edge_ids"] = json.dumps(
        component["edge_ids"],
        separators=(",", ":"),
    )
    root["nv_collision_verified"] = True
    _tag_object(
        root,
        identifier,
        registry["semantic_id"],
        registry["instance_id"],
        registry["material_id"],
        registry["variant_id"],
    )
    return root


def _build_elevated_components(request, materials, collection):
    topology = request["elevated_topology"]
    registry_by_id = {row["object_id"]: row for row in request["object_registry"]}
    edge_by_id = {edge["edge_id"]: edge for edge in topology["edges"]}
    extent = request["scene_plan"]["extent"]
    roots = []
    for component in topology["components"]:
        registry = registry_by_id[component["component_id"]]
        root = _new_elevated_root(
            component,
            topology,
            registry,
            collection,
        )
        roots.append(root)
        kind = component["component_kind"]
        edges = [edge_by_id[edge_id] for edge_id in component["edge_ids"]]
        deck = MeshAssembler()
        rails = MeshAssembler()
        supports = MeshAssembler()
        roof = MeshAssembler()
        drainage = MeshAssembler()
        support_points = {}
        for edge in edges:
            points = [tuple(point["position_m"]) for point in edge["centerline"]]
            for point in points:
                support_points[point] = point
            for start, end in zip(points, points[1:], strict=False):
                if kind == "switchback-stair":
                    _add_stair_treads(deck, start, end, edge["width_m"])
                else:
                    _add_segment_prism(
                        deck,
                        start,
                        end,
                        edge["width_m"],
                        edge["collision"]["deck_thickness_m"],
                        z_offset=-edge["collision"]["deck_thickness_m"] / 2,
                    )
                _add_side_rails(
                    rails,
                    start,
                    end,
                    edge["width_m"],
                    edge["collision"]["railing_height_m"],
                )
                if edge["collision"]["covered"]:
                    roof_height = edge["collision"]["head_clearance_m"] + 0.22
                    _add_segment_prism(
                        roof,
                        start,
                        end,
                        edge["width_m"] + 0.9,
                        0.20,
                        z_offset=roof_height,
                    )
                if kind == "terrace-ramp-junction":
                    horizontal = Vector(
                        (end[0] - start[0], end[1] - start[1], 0.0),
                    )
                    side = (
                        Vector((-horizontal.y, horizontal.x, 0.0)).normalized()
                        * (edge["width_m"] / 2 + 0.24)
                    )
                    for sign in (-1.0, 1.0):
                        _add_segment_prism(
                            drainage,
                            (
                                start[0] + side.x * sign,
                                start[1] + side.y * sign,
                                start[2] + 0.10,
                            ),
                            (
                                end[0] + side.x * sign,
                                end[1] + side.y * sign,
                                end[2] + 0.10,
                            ),
                            0.18,
                            0.20,
                        )
        _add_supports(
            supports,
            tuple(sorted(support_points)),
            extent,
            0.24,
        )

        if kind == "switchback-stair":
            deck_part = "walkable-stair-treads"
            deck_material = "material-fieldstone-01"
        elif kind == "covered-timber-gallery":
            deck_part = "walkable-timber-deck"
            deck_material = "material-weathered-timber-01"
        elif kind == "terrace-ramp-junction":
            deck_part = "walkable-ramp-deck"
            deck_material = "material-packed-earth-01"
        else:
            deck_part = "walkable-cross-level-decks"
            deck_material = "material-weathered-timber-01"
        _link_mesh(
            root,
            deck_part,
            deck,
            materials[deck_material],
            registry,
            collection,
        )
        _link_mesh(
            root,
            "collision-side-rails",
            rails,
            materials[
                "material-weathered-timber-01"
                if kind in {"covered-timber-gallery", "cross-level-covered-passage"}
                else "material-fieldstone-01"
            ],
            registry,
            collection,
        )
        _link_mesh(
            root,
            "structural-supports",
            supports,
            materials["material-fieldstone-01"],
            registry,
            collection,
        )
        if roof.vertices:
            _link_mesh(
                root,
                "covered-roof",
                roof,
                materials["material-gray-roof-tile-01"],
                registry,
                collection,
            )
        if drainage.vertices:
            _link_mesh(
                root,
                "drainage-separation",
                drainage,
                materials["material-fieldstone-01"],
                registry,
                collection,
            )
    return roots


def _build_canonical_objects(request, materials, canonical_collection):
    registry_by_id = {row["object_id"]: row for row in request["object_registry"]}
    roots = []
    for item in request["scene_plan"]["objects"]:
        registry = registry_by_id[item["object_id"]]
        root = _new_root(item, registry, canonical_collection)
        roots.append(root)
        semantic = item["semantic_class"]
        if semantic == "building":
            _build_building(
                item,
                root,
                registry,
                materials,
                canonical_collection,
                request.get("building_geometry_profile_id", BUILDING_GEOMETRY_V1),
            )
        elif semantic == "bridge":
            _build_bridge(item, root, registry, materials, canonical_collection)
        elif semantic in {"creek", "path", "retaining-wall"}:
            _build_linear_feature(
                item,
                root,
                registry,
                materials,
                canonical_collection,
                request["scene_plan"]["extent"],
                request,
            )
        elif semantic in {"pond", "field", "orchard", "bamboo", "courtyard"}:
            _build_area_feature(
                item,
                root,
                registry,
                materials,
                canonical_collection,
                request["scene_plan"]["extent"],
            )
        elif semantic == "prop":
            _build_prop(item, root, registry, materials, canonical_collection)
        else:
            raise RuntimeBuildError(f"unsupported semantic class: {semantic}")
    roots.extend(
        _build_elevated_components(
            request,
            materials,
            canonical_collection,
        ),
    )
    return roots


def _configure_scene(request, materials):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 576
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.use_file_extension = True
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    if request["schema_version"] == LOCAL_TEXTURED_REQUEST_SCHEMA:
        scene["nv_preview_id"] = request["preview_id"]
        scene["nv_authoritative"] = False
        scene["nv_release_channel"] = "local-preview-only"
    else:
        scene["nv_build_id"] = request["build_id"]
    scene["nv_fidelity"] = FIDELITY
    scene["nv_synthetic"] = True

    # 天气 = 改场景【光照本身】。缺省 (weather 缺席) -> 与 canary 逐值一致的中性阴天。
    # 有 weather 块 -> 用其【已解算】的具体数值 (色温->rgb、高度角/方位->euler 都在
    # pipeline/weather_profile.py 侧算好), builder 只做哑赋值。灯光角色恒为固定三 token。
    weather = request.get("weather")
    lighting = weather["lighting"] if weather is not None else None

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if lighting is None:
        world_rgb = (0.43, 0.55, 0.62)
        world_strength = 0.62
    else:
        world_rgb = tuple(lighting["world_color"])
        world_strength = lighting["world_strength"]
    background.inputs["Color"].default_value = (
        world_rgb[0],
        world_rgb[1],
        world_rgb[2],
        1.0,
    )
    background.inputs["Strength"].default_value = world_strength
    world["nv_auxiliary_id"] = "background-world"
    world["nv_semantic_id"] = 0
    world["nv_synthetic"] = True
    scene.world = world

    sun_role, fill_role, rim_role = (
        tuple(lighting["roles"]) if lighting is not None else WEATHER_LIGHT_ROLES
    )

    lighting_collection = _new_collection("nv__lighting")
    sun_data = bpy.data.lights.new("nv__sun-data", "SUN")
    if lighting is None:
        sun_data.energy = 2.2
        sun_data.angle = math.radians(14.0)
        sun_rotation_deg = (28.0, -18.0, -42.0)
    else:
        sun_data.energy = lighting["sun_energy"]
        sun_data.angle = math.radians(lighting["sun_angle_deg"])
        sun_data.color = tuple(lighting["sun_color"])
        sun_rotation_deg = tuple(lighting["sun_rotation_euler_deg"])
    sun = bpy.data.objects.new("nv__sun", sun_data)
    lighting_collection.objects.link(sun)
    sun.rotation_euler = (
        math.radians(sun_rotation_deg[0]),
        math.radians(sun_rotation_deg[1]),
        math.radians(sun_rotation_deg[2]),
    )
    sun["nv_role"] = sun_role

    fill_data = bpy.data.lights.new("nv__fill-data", "AREA")
    fill_data.shape = "DISK"
    fill_data.size = 90.0
    if lighting is None:
        fill_data.energy = 1400.0
        fill_location = (-80.0, -120.0, 230.0)
    else:
        fill_data.energy = lighting["fill_energy"]
        fill_data.color = tuple(lighting["fill_color"])
        fill_location = tuple(lighting["fill_location"])
    fill = bpy.data.objects.new("nv__fill", fill_data)
    lighting_collection.objects.link(fill)
    fill.location = fill_location
    fill.rotation_euler = (0.0, 0.0, 0.0)
    fill["nv_role"] = fill_role

    rim_data = bpy.data.lights.new("nv__rim-data", "SUN")
    if lighting is None:
        rim_data.energy = 0.7
        rim_data.angle = math.radians(24.0)
    else:
        rim_data.energy = lighting["rim_energy"]
        rim_data.angle = math.radians(lighting["rim_angle_deg"])
    rim = bpy.data.objects.new("nv__rim", rim_data)
    lighting_collection.objects.link(rim)
    rim.rotation_euler = (math.radians(55.0), 0.0, math.radians(125.0))
    rim["nv_role"] = rim_role
    return scene


def _create_cameras(request):
    collection = _new_collection("nv__cameras")
    camera_objects = {}
    for camera in request["camera_plan"]["cameras"]:
        camera_id = camera["camera_id"]
        data = bpy.data.cameras.new(f"nv__{camera_id}-data")
        data.type = "PERSP"
        data.sensor_fit = "HORIZONTAL"
        data.sensor_width = 36.0
        data.lens = camera["intrinsics"]["fx"] * data.sensor_width / 1024.0
        data.clip_start = 0.10
        data.clip_end = 1200.0
        data.dof.use_dof = False
        obj = bpy.data.objects.new(f"nv__{camera_id}", data)
        collection.objects.link(obj)
        obj.matrix_world = Matrix(camera["c2w_blender"])
        obj["nv_camera_id"] = camera_id
        obj["nv_category"] = camera["category"]
        obj["nv_split"] = camera["split"]
        obj["nv_c2w_blender"] = json.dumps(camera["c2w_blender"], separators=(",", ":"))
        camera_objects[camera_id] = obj
    return camera_objects


def _string_set_property(owner, key, label):
    try:
        value = json.loads(owner.get(key, "[]"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeBuildError(f"{label} component metadata is invalid") from exc
    if (
        not isinstance(value, list)
        or not all(_is_slug(item) for item in value)
        or value != sorted(set(value))
    ):
        raise RuntimeBuildError(f"{label} component metadata is invalid")
    return set(value)


def _append_string_set_property(owner, key, value):
    values = _string_set_property(owner, key, owner.name)
    values.add(value)
    owner[key] = json.dumps(sorted(values), separators=(",", ":"))


def _record_visual_evidence(owner, slot_id, component_tag):
    _append_string_set_property(owner, "nv_visual_slot_ids", slot_id)
    _append_string_set_property(owner, "nv_visual_component_tags", component_tag)


def _require_root_component(root, component_tag, slot_id):
    components = _string_set_property(root, "nv_components", root["nv_stable_id"])
    if component_tag not in components:
        raise RuntimeBuildError(
            f"visual slot evidence is absent from Blender root: {slot_id}",
        )
    matching_children = [
        child
        for child in root.children
        if child.type == "MESH" and child.get("nv_part_id") == component_tag
    ]
    if len(matching_children) != 1 or not matching_children[0].data.polygons:
        raise RuntimeBuildError(
            f"visual slot evidence mesh is absent or empty: {slot_id}",
        )


def _validate_visual_scene_evidence(request, roots, materials):
    textured = request["schema_version"] in {
        TEXTURED_REQUEST_SCHEMA,
        LOCAL_TEXTURED_REQUEST_SCHEMA,
    }
    expected_material_implementation = (
        "derived-pbr-material-v1" if textured else "pbr-material-v1"
    )
    roots_by_id = {root["nv_stable_id"]: root for root in roots}
    auxiliary_names = {
        row["auxiliary_id"]: row["blender_name"] for row in request["auxiliary_registry"]
    }
    validated_slot_ids = []
    for row in request["visual_slot_registry"]:
        if row["build_status"] != "instantiated" or row["category"] == "key-view":
            continue
        slot_id = row["slot_id"]
        component_tag = row["component_tag"]
        evidence_ids = row["evidence_ids"]
        if row["category"] == "material":
            if evidence_ids != [slot_id]:
                raise RuntimeBuildError(f"material evidence is not self-addressed: {slot_id}")
            material = materials.get(slot_id)
            principled = (
                material.node_tree.nodes.get("Principled BSDF")
                if material is not None and material.use_nodes and material.node_tree is not None
                else None
            )
            if (
                component_tag != "blender-material"
                or material is None
                or material.get("nv_slot_id") != slot_id
                or material.get("nv_implementation") != expected_material_implementation
                or principled is None
                or material.users <= 0
            ):
                raise RuntimeBuildError(f"PBR material evidence is absent: {slot_id}")
            if textured and any(
                material.get(key) is None
                for key in (
                    "slot_id",
                    "source_sha256",
                    "bundle_id",
                    "algorithm_id",
                    "synthetic",
                    "uv_policy",
                )
            ):
                raise RuntimeBuildError(f"textured material extras are absent: {slot_id}")
            _record_visual_evidence(material, slot_id, component_tag)
        elif row["category"] == "prop":
            for evidence_id in evidence_ids:
                root = roots_by_id.get(evidence_id)
                if (
                    root is None
                    or root.get("nv_semantic_class") != "prop"
                    or root.get("nv_variant_id") != component_tag
                ):
                    raise RuntimeBuildError(f"prop evidence is absent: {slot_id}")
                _require_root_component(root, f"{component_tag}-body", slot_id)
                _record_visual_evidence(root, slot_id, component_tag)
        elif row["category"] == "detail":
            for evidence_id in evidence_ids:
                root = roots_by_id.get(evidence_id)
                if root is None:
                    raise RuntimeBuildError(f"detail evidence root is absent: {slot_id}")
                _require_root_component(root, component_tag, slot_id)
                _record_visual_evidence(root, slot_id, component_tag)
        elif row["category"] == "environment":
            if component_tag in AGGREGATE_COMPONENT_REQUIREMENTS:
                required = AGGREGATE_COMPONENT_REQUIREMENTS[component_tag]
                for evidence_id in evidence_ids:
                    root = roots_by_id.get(evidence_id)
                    if root is None:
                        raise RuntimeBuildError(
                            f"environment evidence root is absent: {slot_id}",
                        )
                    components = _string_set_property(
                        root,
                        "nv_components",
                        root["nv_stable_id"],
                    )
                    if not required.issubset(components):
                        raise RuntimeBuildError(
                            f"environment aggregate evidence is incomplete: {slot_id}",
                        )
                    _append_string_set_property(root, "nv_component_tags", component_tag)
                    _record_visual_evidence(root, slot_id, component_tag)
            elif component_tag == "upper-slope-forest":
                blender_name = (
                    auxiliary_names.get(evidence_ids[0]) if len(evidence_ids) == 1 else None
                )
                support = bpy.data.objects.get(blender_name) if blender_name else None
                if (
                    support is None
                    or support.type != "MESH"
                    or component_tag
                    not in _string_set_property(
                        support,
                        "nv_support_components",
                        evidence_ids[0],
                    )
                ):
                    raise RuntimeBuildError(f"forest environment evidence is absent: {slot_id}")
                _record_visual_evidence(support, slot_id, component_tag)
            elif component_tag == "overcast-world-background":
                blender_name = (
                    auxiliary_names.get(evidence_ids[0]) if len(evidence_ids) == 1 else None
                )
                world = bpy.data.worlds.get(blender_name) if blender_name else None
                light_roles = {
                    light.get("nv_role") for light in bpy.data.objects if light.type == "LIGHT"
                }
                background = (
                    world.node_tree.nodes.get("Background")
                    if world is not None and world.use_nodes and world.node_tree is not None
                    else None
                )
                if (
                    world is None
                    or world.get("nv_auxiliary_id") != evidence_ids[0]
                    or background is None
                    or light_roles
                    != {"neutral-overcast-key", "neutral-sky-fill", "terrain-separation"}
                ):
                    raise RuntimeBuildError(f"overcast environment evidence is absent: {slot_id}")
                _record_visual_evidence(world, slot_id, component_tag)
            else:
                for evidence_id in evidence_ids:
                    root = roots_by_id.get(evidence_id)
                    if root is None:
                        raise RuntimeBuildError(
                            f"environment evidence root is absent: {slot_id}",
                        )
                    _require_root_component(root, component_tag, slot_id)
                    _record_visual_evidence(root, slot_id, component_tag)
        else:
            raise RuntimeBuildError(f"unsupported instantiated visual slot: {slot_id}")
        validated_slot_ids.append(slot_id)
    expected_slot_ids = sorted(
        row["slot_id"]
        for row in request["visual_slot_registry"]
        if row["build_status"] == "instantiated" and row["category"] != "key-view"
    )
    if sorted(validated_slot_ids) != expected_slot_ids:
        raise RuntimeBuildError("not all non-preview visual evidence was validated")
    bpy.context.scene["nv_visual_slot_evidence"] = json.dumps(
        [
            {
                "component_tag": row["component_tag"],
                "evidence_ids": row["evidence_ids"],
                "slot_id": row["slot_id"],
            }
            for row in request["visual_slot_registry"]
            if row["build_status"] == "instantiated" and row["category"] != "key-view"
        ],
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_elevated_scene_evidence(request, roots):
    topology = request["elevated_topology"]
    roots_by_id = {root["nv_stable_id"]: root for root in roots}
    required_parts = {
        "switchback-stair": {
            "walkable-stair-treads",
            "collision-side-rails",
            "structural-supports",
        },
        "covered-timber-gallery": {
            "walkable-timber-deck",
            "collision-side-rails",
            "covered-roof",
            "structural-supports",
        },
        "terrace-ramp-junction": {
            "walkable-ramp-deck",
            "collision-side-rails",
            "drainage-separation",
            "structural-supports",
        },
        "cross-level-covered-passage": {
            "walkable-cross-level-decks",
            "collision-side-rails",
            "covered-roof",
            "structural-supports",
        },
    }
    edges = {edge["edge_id"]: edge for edge in topology["edges"]}
    evidence = []
    for component in topology["components"]:
        component_id = component["component_id"]
        root = roots_by_id.get(component_id)
        children = (
            [child for child in root.children if child.type == "MESH"]
            if root is not None
            else []
        )
        parts = {child.get("nv_part_id") for child in children}
        if (
            root is None
            or root.get("nv_semantic_class") != "elevated-walkway"
            or root.get("nv_semantic_id") != topology["semantic_id"]
            or root.get("nv_instance_id") != component["instance_id"]
            or root.get("nv_component_kind") != component["component_kind"]
            or root.get("nv_collision_verified") is not True
            or _string_set_property(
                root,
                "nv_walkable_edge_ids",
                component_id,
            )
            != set(component["edge_ids"])
            or parts != required_parts[component["component_kind"]]
            or any(not child.data.polygons for child in children)
        ):
            raise RuntimeBuildError(
                f"elevated component structural evidence is invalid: {component_id}",
            )
        used_materials = {
            child.data.materials[0].get("nv_slot_id")
            for child in children
            if child.data.materials
        }
        if (
            None in used_materials
            or not used_materials.issubset(set(component["material_slot_ids"]))
        ):
            raise RuntimeBuildError(
                f"elevated component PBR materials are invalid: {component_id}",
            )
        world_vertices = [
            child.matrix_world @ vertex.co
            for child in children
            for vertex in child.data.vertices
        ]
        minimum = tuple(min(vertex[index] for vertex in world_vertices) for index in range(3))
        maximum = tuple(max(vertex[index] for vertex in world_vertices) for index in range(3))
        centerline = [
            point["position_m"]
            for edge_id in component["edge_ids"]
            for point in edges[edge_id]["centerline"]
        ]
        if any(
            point[index] < minimum[index] - 1e-6
            or point[index] > maximum[index] + 1e-6
            for point in centerline
            for index in range(3)
        ):
            raise RuntimeBuildError(
                f"elevated component bounds do not contain centerline: {component_id}",
            )
        evidence.append(
            {
                "bounds_max_m": [round(float(value), 6) for value in maximum],
                "bounds_min_m": [round(float(value), 6) for value in minimum],
                "component_id": component_id,
                "component_kind": component["component_kind"],
                "edge_ids": component["edge_ids"],
                "instance_id": component["instance_id"],
                "material_slot_ids": sorted(used_materials),
                "part_ids": sorted(parts),
                "semantic_id": topology["semantic_id"],
            },
        )
    bpy.context.scene["nv_elevated_geometry_evidence"] = json.dumps(
        evidence,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_built_scene(request, roots, materials, camera_objects):
    expected_ids = [row["object_id"] for row in request["object_registry"]]
    actual_ids = [root["nv_stable_id"] for root in roots]
    if actual_ids != expected_ids or len(set(actual_ids)) != 130:
        raise RuntimeBuildError("canonical Blender root IDs do not match request")
    if len(materials) != 24 or set(materials) != set(VISUAL_MATERIALS):
        raise RuntimeBuildError("24 visual materials were not instantiated")
    if len(camera_objects) != 24:
        raise RuntimeBuildError("24 cameras were not instantiated")
    mesh_objects = [obj for obj in bpy.data.objects if obj.type == "MESH"]
    if len(mesh_objects) < 130:
        raise RuntimeBuildError("scene has fewer than 130 mesh objects")
    bpy.context.view_layer.update()
    maximum_canonical_edge = 0.0
    maximum_canonical_aspect = 0.0
    minimum_face_area = math.inf
    maximum_surface_terrain_error = 0.0
    extent = request["scene_plan"]["extent"]
    for obj in mesh_objects:
        mesh = obj.data
        if not mesh.vertices or not mesh.polygons:
            raise RuntimeBuildError(f"mesh is empty: {obj.name}")
        if any(
            not all(math.isfinite(component) for component in vertex.co) for vertex in mesh.vertices
        ):
            raise RuntimeBuildError(f"mesh contains non-finite vertex: {obj.name}")
        is_canonical = not obj.get("nv_auxiliary", False) and not str(
            obj.get("nv_root_id", ""),
        ).startswith("aux-")
        for polygon in mesh.polygons:
            area = float(polygon.area)
            minimum_face_area = min(minimum_face_area, area)
            if not math.isfinite(area) or area <= 1e-9:
                raise RuntimeBuildError(f"mesh contains a degenerate face: {obj.name}")
            coordinates = [mesh.vertices[index].co for index in polygon.vertices]
            edge_lengths = [
                (coordinates[(index + 1) % len(coordinates)] - coordinate).length
                for index, coordinate in enumerate(coordinates)
            ]
            minimum_edge = min(edge_lengths)
            maximum_edge = max(edge_lengths)
            if minimum_edge <= 1e-6 or not math.isfinite(maximum_edge):
                raise RuntimeBuildError(f"mesh contains a degenerate edge: {obj.name}")
            if is_canonical:
                maximum_canonical_edge = max(maximum_canonical_edge, maximum_edge)
                maximum_canonical_aspect = max(
                    maximum_canonical_aspect,
                    maximum_edge / minimum_edge,
                )
                if maximum_edge > 100.0 or maximum_edge / minimum_edge > 1200.0:
                    raise RuntimeBuildError(f"canonical mesh face is unbounded: {obj.name}")
        if is_canonical:
            for vertex in mesh.vertices:
                world = obj.matrix_world @ vertex.co
                if (
                    abs(world.x) > 380.0
                    or abs(world.y) > 280.0
                    or world.z < -1.0
                    or world.z > 155.0
                ):
                    raise RuntimeBuildError(
                        f"canonical mesh exceeds the scene envelope: {obj.name}",
                    )
                if obj.get("nv_part_id") == "terrain-conform-surface":
                    terrain_error = abs(
                        world.z - _terrain_height(world.x, world.y, extent) - 0.08,
                    )
                    maximum_surface_terrain_error = max(
                        maximum_surface_terrain_error,
                        terrain_error,
                    )
                    if terrain_error > 0.0011:
                        raise RuntimeBuildError(
                            f"area surface does not conform to terrain: {obj.name}",
                        )
    for camera in request["camera_plan"]["cameras"]:
        actual = camera_objects[camera["camera_id"]].matrix_world
        expected = camera["c2w_blender"]
        for row in range(4):
            for column in range(4):
                delta = abs(actual[row][column] - expected[row][column])
                if row < 3 and column < 3:
                    allowed = 0.00000032
                elif row < 3 and column == 3:
                    allowed = min(
                        0.00004,
                        max(5e-8, abs(expected[row][column]) * 1.2e-7),
                    )
                else:
                    allowed = 5e-8
                if delta > allowed + 1e-12:
                    raise RuntimeBuildError(
                        f"camera matrix changed beyond measured tolerance: {camera['camera_id']}",
                    )
    auxiliary = {
        "World": bpy.data.worlds.get("World"),
        "nv__aux-terrain": bpy.data.objects.get("nv__aux-terrain"),
        "nv__aux-support-terrain-skirt": bpy.data.objects.get(
            "nv__aux-support-terrain-skirt",
        ),
    }
    if any(value is None for value in auxiliary.values()):
        raise RuntimeBuildError("auxiliary semantic resources are incomplete")
    auxiliary_mesh_names = {
        obj.name
        for obj in bpy.data.objects
        if obj.type == "MESH" and bool(obj.get("nv_auxiliary", False))
    }
    if auxiliary_mesh_names != {
        "nv__aux-terrain",
        "nv__aux-support-terrain-skirt",
    }:
        raise RuntimeBuildError("auxiliary semantic mesh set is not exactly stable v1")
    _validate_elevated_scene_evidence(request, roots)
    _validate_visual_scene_evidence(request, roots, materials)
    building_geometry_profile = request.get(
        "building_geometry_profile_id",
        BUILDING_GEOMETRY_V1,
    )
    if building_geometry_profile == BUILDING_GEOMETRY_V2:
        building_roots = [
            root
            for root in roots
            if root.get("nv_semantic_class") == "building"
        ]
        building_mesh_count = sum(
            child.type == "MESH"
            for root in building_roots
            for child in root.children
        )
        variant_counts = {variant: 0 for variant in BUILDING_VARIANTS}
        added_face_counts = []
        expected_elevations = json.dumps(
            BUILDING_ELEVATIONS,
            separators=(",", ":"),
        )
        for root in building_roots:
            object_id = root.get("nv_stable_id")
            expected_variant = _building_variant(object_id, building_geometry_profile)
            added_faces = root.get("nv_added_face_count")
            if (
                root.get("nv_building_geometry_profile") != BUILDING_GEOMETRY_V2
                or root.get("nv_building_variant") != expected_variant
                or root.get("nv_facade_elevations") != expected_elevations
                or isinstance(added_faces, bool)
                or not isinstance(added_faces, int)
                or not 1 <= added_faces <= MAX_ADDED_BUILDING_FACES
            ):
                raise RuntimeBuildError(
                    f"building geometry evidence is invalid: {object_id}",
                )
            variant_counts[expected_variant] += 1
            added_face_counts.append(added_faces)
        added_face_count = sum(added_face_counts)
        if (
            len(building_roots) != 70
            or building_mesh_count != EXPECTED_BUILDING_MESH_OBJECTS
            or variant_counts != EXPECTED_BUILDING_VARIANT_COUNTS
            or not 1 <= added_face_count <= MAX_ADDED_VILLAGE_FACES
        ):
            raise RuntimeBuildError("v2 building geometry aggregate evidence is invalid")
        bpy.context.scene["nv_building_geometry_evidence"] = json.dumps(
            {
                "added_face_count": added_face_count,
                "building_count": len(building_roots),
                "covered_elevations": list(BUILDING_ELEVATIONS),
                "maximum_added_faces_per_building": max(added_face_counts),
                "new_mesh_object_count": 0,
                "profile_id": BUILDING_GEOMETRY_V2,
                "variant_counts": variant_counts,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    prop_counts = {variant: 0 for variant in PROP_VARIANTS}
    for row in request["object_registry"]:
        if row["variant_id"]:
            prop_counts[row["variant_id"]] += 1
    if any(value != 2 for value in prop_counts.values()):
        raise RuntimeBuildError("prop variant count is not exactly two each")
    bpy.context.scene["nv_mesh_diagnostics"] = json.dumps(
        {
            "maximum_canonical_face_edge_m": round(maximum_canonical_edge, 6),
            "maximum_canonical_face_aspect": round(maximum_canonical_aspect, 6),
            "minimum_face_area_m2": round(minimum_face_area, 9),
            "maximum_surface_terrain_error_m": round(
                maximum_surface_terrain_error,
                9,
            ),
        },
        sort_keys=True,
    )
    return mesh_objects, prop_counts


def _preview_camera_matrix(eye, target):
    eye_vector = Vector(eye)
    forward = (Vector(target) - eye_vector).normalized()
    right = forward.cross(Vector((0.0, 0.0, 1.0))).normalized()
    up = right.cross(forward).normalized()
    return Matrix(
        (
            (right.x, up.x, -forward.x, eye_vector.x),
            (right.y, up.y, -forward.y, eye_vector.y),
            (right.z, up.z, -forward.z, eye_vector.z),
            (0.0, 0.0, 0.0, 1.0),
        ),
    )


def _render_previews(scene, camera_objects, work_dir):
    preview_views = {
        "preview-bridge.png": {
            "eye": (-92.0, -205.0, 108.0),
            "target": (-175.0, -115.0, 43.0),
            "lens_mm": 46.0,
        },
        "preview-central.png": {
            "eye": (108.0, -142.0, 140.0),
            "target": (0.0, 10.0, 71.0),
            "lens_mm": 42.0,
        },
        "preview-outer.png": {
            "eye": (330.0, -290.0, 225.0),
            "target": (0.0, 15.0, 70.0),
            "lens_mm": 32.0,
        },
        "preview-upper.png": {
            "eye": (305.0, 5.0, 175.0),
            "target": (170.0, 115.0, 94.0),
            "lens_mm": 44.0,
        },
    }
    canonical_camera = camera_objects["camera-outer-001"]
    data = bpy.data.cameras.new("nv__preview-camera-temporary-data")
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = 36.0
    data.clip_start = 1.0
    data.clip_end = 2000.0
    obj = bpy.data.objects.new("nv__preview-camera-temporary", data)
    scene.collection.objects.link(obj)
    obj["nv_preview_only"] = True
    registry = []
    try:
        for name, view in sorted(preview_views.items()):
            data.lens = view["lens_mm"]
            obj.matrix_world = _preview_camera_matrix(view["eye"], view["target"])
            obj["nv_preview_eye"] = json.dumps(view["eye"])
            obj["nv_preview_target"] = json.dumps(view["target"])
            scene.camera = obj
            scene.render.filepath = str(work_dir / name)
            bpy.ops.render.render(write_still=True)
            artifact = work_dir / name
            if not artifact.is_file() or artifact.stat().st_size <= 0:
                raise RuntimeBuildError(f"preview render did not publish: {name}")
            registry.append(
                {
                    "artifact_name": name,
                    "blender_camera_name": "nv__preview-camera-temporary",
                    "eye_xyz": list(view["eye"]),
                    "target_xyz": list(view["target"]),
                    "lens_mm": view["lens_mm"],
                    "clip_start_m": 1.0,
                    "clip_end_m": 2000.0,
                    "image_width_px": 1024,
                    "image_height_px": 576,
                },
            )
    finally:
        scene.camera = canonical_camera
        scene.render.filepath = ""
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.cameras.remove(data)
    return registry


def _validate_preview_evidence(request, preview_registry, work_dir):
    registry_by_name = {row["artifact_name"]: row for row in preview_registry}
    expected_rows = [
        row
        for row in request["visual_slot_registry"]
        if row["build_status"] == "instantiated" and row["category"] == "key-view"
    ]
    validated = []
    for row in expected_rows:
        evidence_ids = row["evidence_ids"]
        artifact_name = evidence_ids[0] if len(evidence_ids) == 1 else None
        artifact = work_dir / artifact_name if artifact_name else None
        if (
            row["component_tag"] != "preview-artifact"
            or artifact_name not in registry_by_name
            or artifact is None
            or not artifact.is_file()
            or artifact.stat().st_size <= 8
        ):
            raise RuntimeBuildError(f"preview artifact evidence is absent: {row['slot_id']}")
        with artifact.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                raise RuntimeBuildError(
                    f"preview artifact evidence is not a PNG: {row['slot_id']}",
                )
        validated.append(
            {
                "artifact_name": artifact_name,
                "slot_id": row["slot_id"],
            },
        )
    if len(validated) != 4 or {row["artifact_name"] for row in validated} != set(
        KEY_VIEW_PREVIEW_ARTIFACTS.values(),
    ):
        raise RuntimeBuildError("preview artifact evidence set is not exactly stable v1")
    bpy.context.scene["nv_preview_slot_evidence"] = json.dumps(
        sorted(validated, key=lambda row: row["slot_id"]),
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_exported_surface_colors(document, binary, accessor_index):
    accessors = document.get("accessors")
    views = document.get("bufferViews")
    if (
        not isinstance(accessors, list)
        or not isinstance(views, list)
        or isinstance(accessor_index, bool)
        or not isinstance(accessor_index, int)
        or accessor_index < 0
        or accessor_index >= len(accessors)
        or not isinstance(accessors[accessor_index], dict)
    ):
        raise RuntimeBuildError("exported surface color accessor is invalid")
    accessor = accessors[accessor_index]
    if "sparse" in accessor:
        raise RuntimeBuildError("exported surface color accessor is sparse")
    value_type = accessor.get("type")
    component_count = {"VEC3": 3, "VEC4": 4}.get(value_type)
    component_type = accessor.get("componentType")
    formats = {
        5121: ("B", 1, 255.0),
        5123: ("H", 2, 65535.0),
        5126: ("f", 4, None),
    }
    if component_count is None or component_type not in formats:
        raise RuntimeBuildError("exported surface color type is unsupported")
    normalized = accessor.get("normalized", False)
    if (
        not isinstance(normalized, bool)
        or (component_type == 5126 and normalized)
        or (component_type != 5126 and not normalized)
    ):
        raise RuntimeBuildError("exported surface color normalization is invalid")
    count = accessor.get("count")
    view_index = accessor.get("bufferView")
    accessor_offset = accessor.get("byteOffset", 0)
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or isinstance(view_index, bool)
        or not isinstance(view_index, int)
        or view_index < 0
        or view_index >= len(views)
        or not isinstance(views[view_index], dict)
        or isinstance(accessor_offset, bool)
        or not isinstance(accessor_offset, int)
        or accessor_offset < 0
    ):
        raise RuntimeBuildError("exported surface color range is invalid")
    view = views[view_index]
    view_offset = view.get("byteOffset", 0)
    view_length = view.get("byteLength")
    if (
        view.get("buffer") != 0
        or isinstance(view_offset, bool)
        or not isinstance(view_offset, int)
        or view_offset < 0
        or isinstance(view_length, bool)
        or not isinstance(view_length, int)
        or view_length <= 0
    ):
        raise RuntimeBuildError("exported surface color buffer view is invalid")
    format_code, component_bytes, denominator = formats[component_type]
    element_bytes = component_count * component_bytes
    stride = view.get("byteStride", element_bytes)
    if (
        isinstance(stride, bool)
        or not isinstance(stride, int)
        or stride < element_bytes
        or accessor_offset + stride * (count - 1) + element_bytes > view_length
        or view_offset + view_length > len(binary)
    ):
        raise RuntimeBuildError("exported surface color stride is invalid")
    unpack_format = "<" + format_code * component_count
    colors = []
    for index in range(count):
        offset = view_offset + accessor_offset + stride * index
        values = struct.unpack_from(unpack_format, binary, offset)
        if denominator is not None:
            values = tuple(value / denominator for value in values)
        color = tuple(float(value) for value in values)
        if component_count == 3:
            color += (1.0,)
        if any(not math.isfinite(value) for value in color):
            raise RuntimeBuildError("exported surface color is non-finite")
        colors.append(color)
    return tuple(colors)


def _normalize_surface_color_accessors(glb_path):
    try:
        raw = glb_path.read_bytes()
        if len(raw) < 28:
            raise RuntimeBuildError("surface GLB header is incomplete")
        magic, version, declared = struct.unpack_from("<4sII", raw, 0)
        json_length, json_kind = struct.unpack_from("<I4s", raw, 12)
        json_start = 20
        json_end = json_start + json_length
        if (
            magic != b"glTF"
            or version != 2
            or declared != len(raw)
            or json_kind != b"JSON"
            or json_length <= 0
            or json_end + 8 > len(raw)
        ):
            raise RuntimeBuildError("surface GLB header is invalid")
        binary_length, binary_kind = struct.unpack_from("<I4s", raw, json_end)
        binary_start = json_end + 8
        binary_end = binary_start + binary_length
        if binary_kind != b"BIN\0" or binary_end != len(raw):
            raise RuntimeBuildError("surface GLB binary chunk is invalid")
        document = json.loads(raw[json_start:json_end].decode("utf-8"))
    except RuntimeBuildError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, struct.error) as exc:
        raise RuntimeBuildError("surface GLB cannot be normalized") from exc
    if not isinstance(document, dict):
        raise RuntimeBuildError("surface GLB document is invalid")
    buffers = document.get("buffers")
    views = document.get("bufferViews")
    accessors = document.get("accessors")
    meshes = document.get("meshes")
    if (
        not isinstance(buffers, list)
        or len(buffers) != 1
        or not isinstance(buffers[0], dict)
        or not isinstance(views, list)
        or not isinstance(accessors, list)
        or not isinstance(meshes, list)
    ):
        raise RuntimeBuildError("surface GLB collections are invalid")
    declared_binary = buffers[0].get("byteLength")
    if (
        isinstance(declared_binary, bool)
        or not isinstance(declared_binary, int)
        or declared_binary <= 0
        or declared_binary > binary_length
        or binary_length - declared_binary > 3
        or any(raw[binary_start + declared_binary : binary_end])
    ):
        raise RuntimeBuildError("surface GLB buffer length is invalid")
    binary = bytearray(raw[binary_start : binary_start + declared_binary])
    converted = {}
    color_primitive_count = 0
    for mesh in meshes:
        primitives = mesh.get("primitives") if isinstance(mesh, dict) else None
        if not isinstance(primitives, list) or not primitives:
            raise RuntimeBuildError("surface GLB mesh primitive is invalid")
        for primitive in primitives:
            attributes = (
                primitive.get("attributes")
                if isinstance(primitive, dict)
                else None
            )
            if not isinstance(attributes, dict) or "COLOR_0" not in attributes:
                raise RuntimeBuildError("surface GLB COLOR_0 is absent")
            source_index = attributes["COLOR_0"]
            if source_index not in converted:
                colors = _decode_exported_surface_colors(
                    document,
                    bytes(binary),
                    source_index,
                )
                binary.extend(b"\0" * (-len(binary) % 4))
                offset = len(binary)
                payload = b"".join(struct.pack("<4f", *color) for color in colors)
                binary.extend(payload)
                views.append(
                    {
                        "buffer": 0,
                        "byteLength": len(payload),
                        "byteOffset": offset,
                        "target": 34962,
                    },
                )
                accessors.append(
                    {
                        "bufferView": len(views) - 1,
                        "componentType": 5126,
                        "count": len(colors),
                        "type": "VEC4",
                    },
                )
                converted[source_index] = len(accessors) - 1
            attributes["COLOR_0"] = converted[source_index]
            color_primitive_count += 1
    if color_primitive_count <= 0:
        raise RuntimeBuildError("surface GLB has no color primitives")
    buffers[0]["byteLength"] = len(binary)
    json_bytes = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    binary_bytes = bytes(binary)
    binary_bytes += b"\0" * (-len(binary_bytes) % 4)
    total_length = 12 + 8 + len(json_bytes) + 8 + len(binary_bytes)
    normalized = b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, total_length),
            struct.pack("<I4s", len(json_bytes), b"JSON"),
            json_bytes,
            struct.pack("<I4s", len(binary_bytes), b"BIN\0"),
            binary_bytes,
        ),
    )
    try:
        glb_path.write_bytes(normalized)
    except OSError as exc:
        raise RuntimeBuildError("surface GLB normalization cannot be written") from exc


def _glb_textured_counts(glb_path):
    try:
        raw = glb_path.read_bytes()
        if len(raw) < 20:
            raise RuntimeBuildError("textured GLB header is incomplete")
        magic, version, declared = struct.unpack_from("<4sII", raw, 0)
        json_length, json_kind = struct.unpack_from("<I4s", raw, 12)
        if (
            magic != b"glTF"
            or version != 2
            or declared != len(raw)
            or json_kind != b"JSON"
            or json_length <= 0
            or 20 + json_length > len(raw)
        ):
            raise RuntimeBuildError("textured GLB header is invalid")
        document = json.loads(raw[20 : 20 + json_length].decode("utf-8"))
    except RuntimeBuildError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, struct.error) as exc:
        raise RuntimeBuildError("textured GLB cannot be inspected") from exc
    meshes = document.get("meshes")
    materials = document.get("materials")
    images = document.get("images")
    textures = document.get("textures")
    accessors = document.get("accessors")
    if not all(
        isinstance(value, list)
        for value in (meshes, materials, images, textures, accessors)
    ):
        raise RuntimeBuildError("textured GLB PBR collections are absent")
    primitives = [
        primitive
        for mesh in meshes
        if isinstance(mesh, dict)
        for primitive in mesh.get("primitives", [])
        if isinstance(primitive, dict)
    ]
    uv_count = sum(
        isinstance(primitive.get("attributes"), dict)
        and "TEXCOORD_0" in primitive["attributes"]
        for primitive in primitives
    )
    tangent_count = sum(
        isinstance(primitive.get("attributes"), dict)
        and "TANGENT" in primitive["attributes"]
        for primitive in primitives
    )
    triangle_count = 0
    for primitive in primitives:
        if primitive.get("mode", 4) != 4:
            raise RuntimeBuildError("textured GLB primitive mode is not TRIANGLES")
        accessor_index = primitive.get("indices")
        if accessor_index is None:
            attributes = primitive.get("attributes")
            accessor_index = (
                attributes.get("POSITION")
                if isinstance(attributes, dict)
                else None
            )
        if (
            isinstance(accessor_index, bool)
            or not isinstance(accessor_index, int)
            or accessor_index < 0
            or accessor_index >= len(accessors)
            or not isinstance(accessors[accessor_index], dict)
        ):
            raise RuntimeBuildError("textured GLB triangle accessor is invalid")
        index_count = accessors[accessor_index].get("count")
        if (
            isinstance(index_count, bool)
            or not isinstance(index_count, int)
            or index_count <= 0
            or index_count % 3 != 0
        ):
            raise RuntimeBuildError("textured GLB triangle count is invalid")
        triangle_count += index_count // 3
    embedded_images = sum(
        isinstance(image, dict)
        and "bufferView" in image
        and "uri" not in image
        and image.get("mimeType") == "image/png"
        for image in images
    )
    expected_slots = set(VISUAL_MATERIALS)
    material_slots = {
        material.get("extras", {}).get("slot_id")
        for material in materials
        if isinstance(material, dict) and isinstance(material.get("extras"), dict)
    }
    if (
        not primitives
        or len(materials) != 24
        or material_slots != expected_slots
        or len(images) < 72
        or embedded_images != len(images)
        or len(textures) < 72
        or uv_count != len(primitives)
        or tangent_count != len(primitives)
    ):
        raise RuntimeBuildError("textured GLB lacks complete embedded PBR/UV/tangent evidence")
    return {
        "glb_primitives": len(primitives),
        "glb_triangles": triangle_count,
        "glb_embedded_images": embedded_images,
        "glb_textures": len(textures),
        "glb_uv_primitives": uv_count,
        "glb_tangent_primitives": tangent_count,
    }


def _surface_realism_evidence(request, mesh_objects):
    if request.get("surface_realism_profile_id") != SURFACE_PROFILE_V1:
        return None
    plan = request.get("surface_realism_plan")
    if not isinstance(plan, dict):
        raise RuntimeBuildError("surface realism plan is absent")
    terrain = next(
        (obj for obj in mesh_objects if obj.name == "nv__aux-terrain"),
        None,
    )
    if terrain is None:
        raise RuntimeBuildError("surface terrain evidence is absent")
    path_roots = [
        obj
        for obj in bpy.data.objects
        if obj.type == "EMPTY"
        and str(obj.get("nv_stable_id", "")).startswith("path-network-")
    ]
    detail_objects = [
        obj for obj in mesh_objects if obj.get("nv_surface_detail_class")
    ]
    if len(path_roots) != 6 or len(detail_objects) != EXPECTED_SURFACE_DETAIL_MESH_OBJECTS:
        raise RuntimeBuildError("surface path object evidence is incomplete")
    detail_counts = {detail_class: 0 for detail_class in SURFACE_DETAIL_CAPS}
    for obj in detail_objects:
        detail_class = obj.get("nv_surface_detail_class")
        detail_count = obj.get("nv_surface_detail_count")
        if (
            detail_class not in detail_counts
            or isinstance(detail_count, bool)
            or not isinstance(detail_count, int)
            or detail_count <= 0
        ):
            raise RuntimeBuildError("surface detail mesh evidence is invalid")
        detail_counts[detail_class] += detail_count
    detail_counts["rut-run"] = sum(
        int(root.get("nv_surface_rut_run_count", 0))
        for root in path_roots
    )
    all_channels = []
    colored_primitive_count = 0
    white_primitive_count = 0
    for obj in mesh_objects:
        layer = obj.data.color_attributes.get("nv_surface_color")
        if (
            layer is None
            or layer.data_type != "FLOAT_COLOR"
            or layer.domain != "CORNER"
            or len(layer.data) != len(obj.data.loops)
        ):
            raise RuntimeBuildError(f"surface color evidence is absent: {obj.name}")
        primitive_colored = {}
        for polygon in obj.data.polygons:
            colored = False
            for loop_index in polygon.loop_indices:
                color = tuple(
                    round(float(value), 6)
                    for value in layer.data[loop_index].color[:3]
                )
                if not all(math.isfinite(value) for value in color):
                    raise RuntimeBuildError("surface color evidence is non-finite")
                all_channels.extend(color)
                colored = colored or any(abs(value - 1.0) > 1e-6 for value in color)
            primitive_colored[polygon.material_index] = (
                primitive_colored.get(polygon.material_index, False) or colored
            )
        colored_primitive_count += sum(primitive_colored.values())
        white_primitive_count += sum(not value for value in primitive_colored.values())
    if not all_channels:
        raise RuntimeBuildError("surface color evidence is empty")
    return {
        "profile_id": SURFACE_PROFILE_V1,
        "algorithm_id": SURFACE_ALGORITHM_V1,
        "scene_seed": plan["scene_seed"],
        "plan_sha256": plan["plan_sha256"],
        "runtime_module_sha256": plan["runtime_module_sha256"],
        "terrain_resolution": [
            int(terrain.get("nv_surface_grid_columns", 0)),
            int(terrain.get("nv_surface_grid_rows", 0)),
        ],
        "terrain_triangle_count": int(
            terrain.get("nv_surface_triangle_count", 0),
        ),
        "path_interval_count": sum(
            int(root.get("nv_surface_path_interval_count", 0))
            for root in path_roots
        ),
        "path_triangle_count": sum(
            int(root.get("nv_surface_path_triangle_count", 0))
            for root in path_roots
        ),
        "detail_counts": detail_counts,
        "detail_mesh_object_count": len(detail_objects),
        "color_min": min(all_channels),
        "color_max": max(all_channels),
        "colored_primitive_count": colored_primitive_count,
        "white_primitive_count": white_primitive_count,
    }


def _save_scene_and_glb(work_dir, *, textured=False, surface_realism=False):
    blend_path = work_dir / "village-canary.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    if not blend_path.is_file() or blend_path.stat().st_size <= 0:
        raise RuntimeBuildError("Blender scene did not save")
    glb_path = work_dir / "village-canary.glb"
    result = bpy.ops.export_scene.gltf(
        filepath=str(glb_path),
        export_format="GLB",
        use_selection=False,
        export_cameras=True,
        export_lights=True,
        export_apply=True,
        export_extras=True,
        export_tangents=textured,
    )
    if "FINISHED" not in result or not glb_path.is_file() or glb_path.stat().st_size <= 0:
        raise RuntimeBuildError("GLB export did not finish")
    if surface_realism:
        _normalize_surface_color_accessors(glb_path)
    return _glb_textured_counts(glb_path) if textured else None


def _artifact_records(work_dir):
    records = []
    for artifact in ARTIFACT_REQUESTS:
        path = work_dir / artifact["name"]
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeBuildError(f"artifact is missing or empty: {artifact['name']}")
        with path.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        records.append(
            {
                "kind": artifact["kind"],
                "name": artifact["name"],
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            },
        )
    return records


def _build_report(
    request,
    mesh_objects,
    prop_counts,
    camera_objects,
    preview_registry,
    artifacts,
    glb_counts=None,
):
    local = request["schema_version"] == LOCAL_TEXTURED_REQUEST_SCHEMA
    textured = request["schema_version"] in {
        TEXTURED_REQUEST_SCHEMA,
        LOCAL_TEXTURED_REQUEST_SCHEMA,
    }
    semantic_ids = [row["semantic_id"] for row in request["semantic_registry"]]
    material_ids = [row["material_id"] for row in request["material_registry"]]
    critical_ok = all(
        not row["canary_critical"]
        or row["build_status"] == "instantiated"
        or row["reference_status"] == "verified-design-reference"
        for row in request["visual_slot_registry"]
    )
    report = {
        "schema_version": (
            LOCAL_TEXTURED_REPORT_SCHEMA
            if local
            else TEXTURED_REPORT_SCHEMA
            if textured
            else REPORT_SCHEMA
        ),
        "preview_id" if local else "build_id": (
            request["preview_id"] if local else request["build_id"]
        ),
        "synthetic": True,
        "verification_level": "L0" if local else "L2",
        "fidelity": FIDELITY,
        "tool_identity": request["tool_identity"],
        "source_hashes": request["source_hashes"],
        "object_registry": request["object_registry"],
        "auxiliary_registry": request["auxiliary_registry"],
        "semantic_registry": request["semantic_registry"],
        "material_registry": request["material_registry"],
        "visual_slot_registry": request["visual_slot_registry"],
        "camera_registry": _camera_report_registry(request, camera_objects),
        "preview_registry": preview_registry,
        "counts": {
            "canonical_roots": 130,
            "mesh_objects": len(mesh_objects),
            "scene_material_families": 11,
            "visual_materials": 24,
            "cameras": 24,
            "lights": len(bpy.data.lights),
            "auxiliary_semantic_objects": sum(
                obj.type == "MESH" and bool(obj.get("nv_auxiliary", False))
                for obj in bpy.data.objects
            ),
        },
        "validation": {
            "canonical_object_ids_match": True,
            "camera_matrices_within_tolerance": True,
            "finite_nonempty_meshes": True,
            "semantic_ids_unique": len(semantic_ids) == len(set(semantic_ids)),
            "material_ids_unique": len(material_ids) == len(set(material_ids)),
            "auxiliary_semantics_present": True,
            "all_visual_material_slots_built": True,
            "canary_critical_slots_fulfilled": critical_ok,
            "prop_type_counts": dict(sorted(prop_counts.items())),
        },
        "determinism": {
            "request_bytes": "canonical-json-v1",
            "scene_plan_bytes": "canonical-json-v1",
            "camera_plan_bytes": "canonical-json-v1",
            "blend_bytes": "measured-not-guaranteed",
            "glb_bytes": "measured-not-guaranteed",
            "preview_bytes": "measured-not-guaranteed",
        },
        "artifacts": artifacts,
    }
    if textured:
        if glb_counts is None:
            raise RuntimeBuildError("textured report requires measured GLB evidence")
        report["geometry_usability"] = "preview-only"
        report["material_bundle_manifest_sha256"] = request[
            "material_bundle_manifest_sha256"
        ]
        report["material_bundle_id"] = request["material_bundle_id"]
        report["material_algorithm_id"] = request["material_algorithm_id"]
        report["material_input_registry"] = request["material_input_registry"]
        if "building_geometry_profile_id" in request:
            building_profile = request["building_geometry_profile_id"]
            report["building_geometry_profile_id"] = building_profile
            if building_profile == BUILDING_GEOMETRY_V2:
                raw_evidence = bpy.context.scene.get(
                    "nv_building_geometry_evidence",
                )
                if not isinstance(raw_evidence, str):
                    raise RuntimeBuildError("v2 building geometry evidence is absent")
                try:
                    report["building_geometry"] = json.loads(raw_evidence)
                except json.JSONDecodeError as exc:
                    raise RuntimeBuildError(
                        "v2 building geometry evidence is invalid",
                    ) from exc
        if request.get("surface_realism_profile_id") == SURFACE_PROFILE_V1:
            report["surface_realism_profile_id"] = SURFACE_PROFILE_V1
            report["surface_realism"] = _surface_realism_evidence(
                request,
                mesh_objects,
            )
        report["counts"].update(glb_counts)
    if local:
        report["authoritative"] = False
        report["release_channel"] = "local-preview-only"
    return report


def _camera_report_registry(request, camera_objects):
    registry = []
    for camera in request["camera_plan"]["cameras"]:
        requested = camera["c2w_blender"]
        matrix = camera_objects[camera["camera_id"]].matrix_world
        measured = [[float(matrix[row][column]) for column in range(4)] for row in range(4)]
        translation_error = max(abs(measured[row][3] - requested[row][3]) for row in range(3))
        rotation_error = max(
            abs(measured[row][column] - requested[row][column])
            for row in range(3)
            for column in range(3)
        )
        registry.append(
            {
                "camera_id": camera["camera_id"],
                "blender_camera_name": f"nv__{camera['camera_id']}",
                "requested_c2w_blender": requested,
                "measured_c2w_blender": measured,
                "max_translation_error_m": round(translation_error, 12),
                "max_rotation_entry_error": round(rotation_error, 12),
                "translation_error_limit_m": 0.00004,
                "rotation_entry_error_limit": 0.00000032,
            },
        )
    return registry


def _execute_build(request, staging_path, materials_path=None):
    local = request["schema_version"] == LOCAL_TEXTURED_REQUEST_SCHEMA
    textured = request["schema_version"] in {
        TEXTURED_REQUEST_SCHEMA,
        LOCAL_TEXTURED_REQUEST_SCHEMA,
    }
    content_id = request["preview_id"] if local else request["build_id"]
    material_paths = (
        _validate_material_directory(materials_path, request) if textured else None
    )
    surface_runtime = _load_surface_runtime(request)
    if not textured and materials_path is not None:
        raise RuntimeBuildError("legacy request does not accept a material directory")
    if staging_path.exists():
        raise RuntimeBuildError("staging directory must be absent")
    if not staging_path.parent.is_dir():
        raise RuntimeBuildError("staging parent directory does not exist")
    work_dir = staging_path.with_name(
        f".{staging_path.name}.nvtmp-{content_id[:12]}",
    )
    if work_dir.exists():
        raise RuntimeBuildError("deterministic temporary build directory already exists")
    work_dir.mkdir()
    try:
        _clear_factory_scene()
        canonical_collection = _new_collection("nv__canonical-roots")
        auxiliary_collection = _new_collection("nv__auxiliary")
        materials = (
            _create_textured_materials(request, material_paths)
            if textured
            else _create_materials(request["visual_slot_registry"])
        )
        scene = _configure_scene(request, materials)
        _create_terrain(request, materials, auxiliary_collection)
        roots = _build_canonical_objects(
            request,
            materials,
            canonical_collection,
        )
        camera_objects = _create_cameras(request)
        scene.camera = camera_objects["camera-outer-001"]
        if textured:
            _apply_textured_uvs_and_tangents(
                [obj for obj in bpy.data.objects if obj.type == "MESH"],
                request,
                surface_runtime,
            )
        mesh_objects, prop_counts = _validate_built_scene(
            request,
            roots,
            materials,
            camera_objects,
        )
        preview_registry = _render_previews(scene, camera_objects, work_dir)
        _validate_preview_evidence(request, preview_registry, work_dir)
        if textured:
            for digest, path in material_paths.items():
                _read_stable_material(path, digest)
        glb_counts = _save_scene_and_glb(
            work_dir,
            textured=textured,
            surface_realism=(
                request.get("surface_realism_profile_id") == SURFACE_PROFILE_V1
            ),
        )
        if request.get("surface_realism_profile_id") == SURFACE_PROFILE_V1:
            glb_size = (work_dir / "village-canary.glb").stat().st_size
            if (
                glb_counts["glb_primitives"] != EXPECTED_SURFACE_GLB_PRIMITIVES
                or glb_counts["glb_triangles"] > MAX_SURFACE_GLTF_TRIANGLES
                or glb_size > MAX_SURFACE_GLB_BYTES
            ):
                raise RuntimeBuildError(
                    "surface realism exceeded the GLB primitive, triangle, or byte budget",
                )
        elif request.get("building_geometry_profile_id") == BUILDING_GEOMETRY_V2:
            glb_size = (work_dir / "village-canary.glb").stat().st_size
            if (
                glb_counts["glb_primitives"] != EXPECTED_TEXTURED_GLB_PRIMITIVES
                or glb_size > MAX_TEXTURED_GLB_BYTES
            ):
                raise RuntimeBuildError(
                    "v2 building geometry exceeded the GLB primitive or byte budget",
                )
        if textured:
            for digest, path in material_paths.items():
                _read_stable_material(path, digest)
        artifacts = _artifact_records(work_dir)
        report = _build_report(
            request,
            mesh_objects,
            prop_counts,
            camera_objects,
            preview_registry,
            artifacts,
            glb_counts,
        )
        report_path = work_dir / "build-report.json"
        with report_path.open("xb") as stream:
            stream.write(_canonical_bytes(report))
            stream.flush()
            os.fsync(stream.fileno())
        work_dir.rename(staging_path)
        print(
            f"NANTAI_BUILD_OK content_id={content_id} "
            f"roots=130 meshes={len(mesh_objects)} cameras=24 materials=24",
            flush=True,
        )
    except Exception as exc:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        if isinstance(exc, RuntimeBuildError):
            raise
        raise RuntimeBuildError(f"scene build failed: {type(exc).__name__}: {exc}") from exc


def _runtime_argv(argv: list[str]) -> tuple[Path, Path | None, Path]:
    try:
        marker = argv.index("--")
    except ValueError as exc:
        raise RuntimeBuildError("runtime arguments must follow --") from exc
    values = argv[marker + 1 :]
    legacy = (
        len(values) == 4
        and values[0] == "--request"
        and values[2] == "--staging"
    )
    textured = (
        len(values) == 6
        and values[0] == "--request"
        and values[2] == "--materials"
        and values[4] == "--staging"
    )
    if not legacy and not textured:
        raise RuntimeBuildError(
            "expected exact legacy args or --request <file> --materials <dir> --staging <dir>",
        )
    raw_request_path = Path(values[1])
    raw_materials_path = Path(values[3]) if textured else None
    raw_staging_path = Path(values[5] if textured else values[3])
    if (
        not raw_request_path.is_absolute()
        or not raw_staging_path.is_absolute()
        or (raw_materials_path is not None and not raw_materials_path.is_absolute())
    ):
        raise RuntimeBuildError("request, material, and staging paths must be absolute")
    request_path = raw_request_path.absolute()
    materials_path = (
        raw_materials_path.absolute() if raw_materials_path is not None else None
    )
    staging_path = raw_staging_path.absolute()
    if not request_path.is_file():
        raise RuntimeBuildError("request file does not exist")
    _assert_direct_path(request_path, "request")
    if materials_path is not None:
        _assert_direct_path(materials_path, "materials")
    _assert_direct_path(staging_path, "staging", leaf_may_be_absent=True)
    return request_path, materials_path, staging_path


def main() -> None:
    request_path, materials_path, staging_path = _runtime_argv(sys.argv)
    request, raw = _load_request(request_path)
    _validate_request(request, raw)
    _execute_build(request, staging_path, materials_path)


if __name__ == "__main__":
    try:
        main()
    except RuntimeBuildError as exc:
        print(f"NANTAI_BUILD_ERROR {exc}", flush=True)
        raise SystemExit(17) from None
