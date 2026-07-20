"""Render one verified synthetic-village camera inside pinned Blender 4.5.11.

The runtime uses Blender's bundled :mod:`OpenImageIO` to write and reopen the
exact float32 EXR channel contract before any frame can be published.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import sys
import zlib
from array import array
from pathlib import Path

import bpy
import OpenImageIO as oiio  # noqa: N813
from mathutils import Matrix


class RuntimeRenderError(RuntimeError):
    """Stable render failure raised before frame publication."""


REQUEST_SCHEMA = "nantai.synthetic-village.render-frame-request.v1"
REPORT_SCHEMA = "nantai.synthetic-village.render-frame-report.v1"
CAMERA_SCHEMA = "nantai.synthetic-village.camera-metadata.v1"
LOCAL_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-textured-render-frame-request.v1"
)
LOCAL_REPORT_SCHEMA = (
    "nantai.synthetic-village.local-textured-render-frame-report.v1"
)
LOCAL_CAMERA_SCHEMA = (
    "nantai.synthetic-village.local-textured-camera-metadata.v1"
)
LOCAL_PRODUCTION_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-request.v3"
)
LOCAL_PRODUCTION_REPORT_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-report.v2"
)
LOCAL_PRODUCTION_CAMERA_SCHEMA = (
    "nantai.synthetic-village.local-production-camera-metadata.v2"
)
DEPTH_ENCODING = "euclidean-camera-center-range-m"
NORMAL_ENCODING = "world-space-unit-vector"
MAX_REQUEST_BYTES = 16 * 1024 * 1024
WIDTH = 1024
HEIGHT = 576
PIXELS = WIDTH * HEIGHT
EXR_MAGIC = b"\x76\x2f\x31\x01"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CANONICAL_EXR_DATETIME = "1970:01:01 00:00:00"

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


def _canonical_bytes(payload):
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8",
    )


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeRenderError(f"request contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise RuntimeRenderError(f"request contains non-finite JSON number: {value}")


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _expect_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RuntimeRenderError(f"{label} has unknown or missing fields")


def _expect_list(value, length, label):
    if not isinstance(value, list) or len(value) != length:
        raise RuntimeRenderError(f"{label} must contain exactly {length} entries")


def _is_reparse_point(path):
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _assert_direct_path(path, label, leaf_may_be_absent=False):
    if _is_reparse_point(path) or _is_reparse_point(path.parent):
        raise RuntimeRenderError(f"{label} path is redirected")
    try:
        resolved_parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise RuntimeRenderError(f"{label} parent is unavailable") from exc
    if os.path.normcase(str(resolved_parent)) != os.path.normcase(str(path.parent)):
        raise RuntimeRenderError(f"{label} path is redirected")
    if not leaf_may_be_absent:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeRenderError(f"{label} path is unavailable") from exc
        if os.path.normcase(str(resolved)) != os.path.normcase(str(path)):
            raise RuntimeRenderError(f"{label} path is redirected")


def _signature(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _runtime_argv(argv):
    try:
        marker = argv.index("--")
    except ValueError as exc:
        raise RuntimeRenderError("runtime arguments must follow --") from exc
    values = argv[marker + 1 :]
    if len(values) != 4 or values[0] != "--request" or values[2] != "--staging":
        raise RuntimeRenderError("expected exactly --request <file> --staging <directory>")
    raw_request = Path(values[1])
    raw_staging = Path(values[3])
    if not raw_request.is_absolute() or not raw_staging.is_absolute():
        raise RuntimeRenderError("request and staging paths must be absolute")
    request_path = raw_request.absolute()
    staging_path = raw_staging.absolute()
    if not request_path.is_file():
        raise RuntimeRenderError("request file does not exist")
    _assert_direct_path(request_path, "request")
    _assert_direct_path(staging_path, "staging", leaf_may_be_absent=True)
    return request_path, staging_path


def _load_request(path):
    try:
        before = path.stat()
        if before.st_size <= 0 or before.st_size > MAX_REQUEST_BYTES:
            raise RuntimeRenderError("request size is invalid")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _signature(before) != _signature(opened):
                raise RuntimeRenderError("request changed before bounded read")
            raw = stream.read(MAX_REQUEST_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_REQUEST_BYTES
            or _signature(opened) != _signature(after_open)
            or _signature(before) != _signature(after)
        ):
            raise RuntimeRenderError("request changed during bounded read")
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
        if raw != _canonical_bytes(parsed):
            raise RuntimeRenderError("request must be canonical JSON")
        return parsed
    except RuntimeRenderError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeRenderError("request is not valid bounded UTF-8 JSON") from exc


def _validate_matrix(value, label):
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(row, list) or len(row) != 4 for row in value)
        or any(
            isinstance(component, bool)
            or not isinstance(component, (int, float))
            or not math.isfinite(component)
            for row in value
            for component in row
        )
    ):
        raise RuntimeRenderError(f"{label} must be a finite 4x4 matrix")


def _blender_c2w_to_opencv(matrix):
    """Convert Blender right/up/back camera axes to OpenCV right/down/forward."""

    converted = []
    for row in matrix:
        converted_row = []
        for column_index, component in enumerate(row):
            value = float(component)
            if column_index in {1, 2}:
                value = -value
            if value == 0.0:
                value = 0.0
            converted_row.append(value)
        converted.append(converted_row)
    return converted


def _validate_object_registry_contract(object_registry):
    _expect_list(object_registry, 130, "object_registry")
    expected_instances = list(range(1, 131))
    actual_instances = [row.get("instance_id") for row in object_registry if isinstance(row, dict)]
    if actual_instances != expected_instances:
        raise RuntimeRenderError("object registry instance IDs are not stable 1 through 130")
    stable_ids = []
    for row in object_registry:
        _expect_keys(
            row,
            ("object_id", "instance_id", "semantic_id", "material_id", "variant_id"),
            "object registry row",
        )
        if (
            not isinstance(row["object_id"], str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["object_id"]) is None
            or isinstance(row["semantic_id"], bool)
            or not isinstance(row["semantic_id"], int)
            or not 3 <= row["semantic_id"] < len(SEMANTIC_CLASSES)
            or isinstance(row["material_id"], bool)
            or not isinstance(row["material_id"], int)
            or not 1 <= row["material_id"] <= 255
        ):
            raise RuntimeRenderError("object registry row is invalid")
        stable_ids.append(row["object_id"])
    if len(set(stable_ids)) != 130:
        raise RuntimeRenderError("object registry stable IDs are not unique")


def _validate_auxiliary_registry_contract(auxiliary):
    _expect_list(auxiliary, 3, "auxiliary_registry")
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
    if auxiliary != expected_auxiliary:
        raise RuntimeRenderError("auxiliary registry is not stable v1")


def _is_production_request(request):
    return request.get("schema_version") == LOCAL_PRODUCTION_REQUEST_SCHEMA


def _request_blender_matrix(request):
    return (
        request["requested_c2w_blender"]
        if _is_production_request(request)
        else request["measured_c2w_blender"]
    )


def _production_camera_pattern():
    return (
        r"camera-(?:ground-route|elevated-pedestrian|perimeter-inward"
        r"|environment-corridor|audit-overview)-[0-9]{3}"
    )


def _validate_rigid_matrix(matrix, label):
    _validate_matrix(matrix, label)
    if any(abs(float(matrix[3][index]) - expected) > 1e-9 for index, expected in enumerate(
        (0.0, 0.0, 0.0, 1.0),
    )):
        raise RuntimeRenderError(f"{label} has an invalid homogeneous row")
    rotation = [[float(matrix[row][column]) for column in range(3)] for row in range(3)]
    for left in range(3):
        for right in range(3):
            dot = sum(rotation[row][left] * rotation[row][right] for row in range(3))
            expected = 1.0 if left == right else 0.0
            if abs(dot - expected) > 1e-6:
                raise RuntimeRenderError(f"{label} rotation is not orthonormal")
    determinant = (
        rotation[0][0]
        * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1]
        * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2]
        * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
    )
    if abs(determinant - 1.0) > 1e-6:
        raise RuntimeRenderError(f"{label} determinant is not +1")


def _production_registry_digest(plan):
    payload = {
        "profile_id": plan["profile_id"],
        "plan_schema": plan["plan_schema"],
        "scene_plan_sha256": plan["scene_plan_sha256"],
        "elevated_topology_sha256": plan["elevated_topology_sha256"],
        "cameras": [
            {
                "camera_id": camera["camera_id"],
                "group_id": camera["group_id"],
                "topology_ref": camera["topology_ref"],
                "c2w_opencv": camera["c2w_opencv"],
            }
            for camera in plan["cameras"]
        ],
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _validate_production_camera_request(request):
    plan = request["production_plan"]
    if (
        not isinstance(plan, dict)
        or plan.get("profile_id") != "synthetic-village-coverage-180-v1"
        or plan.get("camera_count") != 180
        or plan.get("declared_target_count") != 180
        or plan.get("complete") is not True
        or plan.get("unplaced_groups") != []
        or plan.get("geometry_trust") != "simplified-pbr-not-render-parity"
        or plan.get("verification_level") != "L2"
        or plan.get("elevated_topology_sha256")
        != request["elevated_topology_sha256"]
    ):
        raise RuntimeRenderError("production plan summary is invalid")
    if hashlib.sha256(_canonical_bytes(plan)).hexdigest() != request[
        "production_plan_sha256"
    ]:
        raise RuntimeRenderError("production plan digest is invalid")
    cameras = plan.get("cameras")
    if (
        not isinstance(cameras, list)
        or len(cameras) != 180
        or len({row.get("camera_id") for row in cameras if isinstance(row, dict)}) != 180
    ):
        raise RuntimeRenderError("production camera registry is incomplete")
    if _production_registry_digest(plan) != request["camera_registry_sha256"]:
        raise RuntimeRenderError("production camera registry digest is invalid")
    camera = request["camera"]
    selected = next(
        (
            row
            for row in cameras
            if isinstance(row, dict) and row.get("camera_id") == camera.get("camera_id")
        ),
        None,
    )
    if selected != camera:
        raise RuntimeRenderError("production camera does not match the immutable plan")
    _expect_keys(
        camera,
        (
            "camera_id",
            "group_id",
            "sequence_index",
            "topology_ref",
            "arc_length_m",
            "position_m",
            "look_at_m",
            "eye_height_m",
            "fov_x_deg",
            "intrinsics",
            "c2w_opencv",
            "audit_only",
            "disclosure",
        ),
        "production camera",
    )
    if (
        re.fullmatch(_production_camera_pattern(), camera["camera_id"]) is None
        or not camera["camera_id"].startswith(f"camera-{camera['group_id']}-")
        or not isinstance(camera["sequence_index"], int)
        or isinstance(camera["sequence_index"], bool)
        or not 1 <= camera["sequence_index"] <= 180
        or not isinstance(camera["topology_ref"], str)
        or not camera["topology_ref"]
        or not isinstance(camera["position_m"], list)
        or len(camera["position_m"]) != 3
        or not isinstance(camera["look_at_m"], list)
        or len(camera["look_at_m"]) != 3
        or not isinstance(camera["eye_height_m"], (int, float))
        or isinstance(camera["eye_height_m"], bool)
        or not 0.0 < camera["eye_height_m"] < 200.0
    ):
        raise RuntimeRenderError("production camera identity or position is invalid")
    _validate_rigid_matrix(camera["c2w_opencv"], "production OpenCV camera matrix")
    _validate_rigid_matrix(
        request["requested_c2w_blender"],
        "requested production Blender camera matrix",
    )
    expected_blender = [
        [
            float(camera["c2w_opencv"][row][column])
            * (-1.0 if column in {1, 2} else 1.0)
            for column in range(4)
        ]
        for row in range(4)
    ]
    if _matrix_error(request["requested_c2w_blender"], expected_blender) > 1e-6:
        raise RuntimeRenderError("production Blender matrix disagrees with OpenCV pose")
    if any(
        abs(float(camera["position_m"][axis]) - float(camera["c2w_opencv"][axis][3]))
        > 1e-6
        for axis in range(3)
    ):
        raise RuntimeRenderError("production camera position disagrees with its matrix")


def _validate_request(request):
    production = _is_production_request(request)
    common_keys = (
        "schema_version",
        "render_id",
        "build_id",
        "synthetic",
        "verification_level",
        "fidelity",
        "blender_executable_sha256",
        "renderer_script_sha256",
        "blend_sha256",
        "build_report_sha256",
        "object_registry_sha256",
        "settings",
        "camera",
        "object_registry",
        "auxiliary_registry",
        "semantic_registry",
    )
    _expect_keys(
        request,
        (
            *common_keys,
            *(
                (
                    "profile_id",
                    "production_plan_sha256",
                    "camera_registry_sha256",
                    "elevated_topology_sha256",
                    "production_plan",
                    "requested_c2w_blender",
                    "build_adapter",
                    "preflight_id",
                    "quality_policy_sha256",
                )
                if production
                else ("measured_c2w_blender",)
            ),
        ),
        "request",
    )
    local = request["schema_version"] in {
        LOCAL_REQUEST_SCHEMA,
        LOCAL_PRODUCTION_REQUEST_SCHEMA,
    }
    if (
        request["schema_version"]
        not in {
            REQUEST_SCHEMA,
            LOCAL_REQUEST_SCHEMA,
            LOCAL_PRODUCTION_REQUEST_SCHEMA,
        }
        or request["synthetic"] is not True
        or request["verification_level"] != ("L0" if local else "L2")
        or request["fidelity"] != "simplified-pbr-not-render-parity"
    ):
        raise RuntimeRenderError("request provenance contract is invalid")
    for key in (
        "render_id",
        "build_id",
        "blender_executable_sha256",
        "renderer_script_sha256",
        "blend_sha256",
        "build_report_sha256",
        "object_registry_sha256",
        *(
            (
                "production_plan_sha256",
                "camera_registry_sha256",
                "elevated_topology_sha256",
                "preflight_id",
                "quality_policy_sha256",
            )
            if production
            else ()
        ),
    ):
        if not _is_sha256(request[key]):
            raise RuntimeRenderError(f"request {key} is not a SHA-256")
    if request["renderer_script_sha256"] != _sha256_file(Path(__file__)):
        raise RuntimeRenderError("renderer script digest does not match executing script")
    executable_path = Path(bpy.app.binary_path).absolute()
    if (
        not executable_path.is_file()
        or _sha256_file(executable_path) != request["blender_executable_sha256"]
    ):
        raise RuntimeRenderError("executing Blender binary does not match the immutable digest")
    blend_path = Path(bpy.data.filepath).absolute()
    if not blend_path.is_file() or _sha256_file(blend_path) != request["blend_sha256"]:
        raise RuntimeRenderError("loaded Blender file does not match the immutable input digest")
    if (
        bpy.app.version_string != "4.5.11 LTS"
        or bpy.app.build_hash.decode("ascii") != "4db51e9d1e1e"
    ):
        raise RuntimeRenderError("executing Blender identity is not pinned 4.5.11 LTS")
    scene = bpy.context.scene
    if production:
        if request["build_adapter"] == "mac-local-textured-preview-v1":
            if (
                scene.get("nv_preview_id") != request["build_id"]
                or scene.get("nv_authoritative") is not False
                or scene.get("nv_release_channel") != "local-preview-only"
            ):
                raise RuntimeRenderError(
                    "loaded local Blender scene provenance does not match request",
                )
        elif request["build_adapter"] == "windows-textured-v2":
            if (
                scene.get("nv_build_id") != request["build_id"]
                or scene.get("nv_preview_id") is not None
                or scene.get("nv_authoritative") is not None
                or scene.get("nv_release_channel") is not None
            ):
                raise RuntimeRenderError(
                    "loaded Windows Blender scene provenance does not match request",
                )
        else:
            raise RuntimeRenderError(
                "production build adapter is not explicitly supported",
            )
    elif local:
        if (
            scene.get("nv_preview_id") != request["build_id"]
            or scene.get("nv_authoritative") is not False
            or scene.get("nv_release_channel") != "local-preview-only"
        ):
            raise RuntimeRenderError(
                "loaded local Blender scene provenance does not match request",
            )
    elif scene.get("nv_build_id") != request["build_id"]:
        raise RuntimeRenderError("loaded Blender scene build ID does not match request")
    if scene.get("nv_fidelity") != request["fidelity"] or scene.get("nv_synthetic") is not True:
        raise RuntimeRenderError("loaded Blender scene provenance is invalid")

    settings = request["settings"]
    expected_settings = {
        "engine": "BLENDER_EEVEE_NEXT",
        "data_engine": "CYCLES",
        "image_width_px": WIDTH,
        "image_height_px": HEIGHT,
        "render_samples": 64,
        "rgb_render_threads": 1,
        "data_render_samples": 1,
        "deterministic_seed": 20260715,
        "view_transform": "AgX",
        "look": "AgX - Medium High Contrast",
        "exposure": 0.0,
        "gamma": 1.0,
        "dither_intensity": 0.0,
        "depth_of_field": "disabled-deep-focus",
        "motion_blur": False,
        "depth_encoding": DEPTH_ENCODING,
        "normal_encoding": NORMAL_ENCODING,
        "instance_encoding": "uint16-png-direct-id",
        "semantic_encoding": "uint8-png-direct-id",
        "depth_channel_layout": "V-float32-zip",
        "normal_channel_layout": "X,Y,Z-float32-zip",
        "instance_pixel_type": "uint16-grayscale-png",
        "semantic_pixel_type": "uint8-grayscale-png",
    }
    if settings != expected_settings:
        raise RuntimeRenderError("render settings are not the exact render v1 contract")

    camera = request["camera"]
    if production:
        if request["profile_id"] != "synthetic-village-coverage-180-v1":
            raise RuntimeRenderError("production profile ID is invalid")
        _validate_production_camera_request(request)
    else:
        _expect_keys(
            camera,
            (
                "camera_id",
                "sequence_index",
                "category",
                "split",
                "source_anchor_ids",
                "fov_x_deg",
                "intrinsics",
                "look_at_target",
                "c2w_opencv",
                "c2w_blender",
                "visible_building_ids",
                "placement_attempts",
            ),
            "camera",
        )
        if (
            re.fullmatch(
                r"camera-(?:outer|ground|courtyard|bridge)-[0-9]{3}",
                camera["camera_id"],
            )
            is None
        ):
            raise RuntimeRenderError("camera ID is not canonical")
    _expect_keys(
        camera["intrinsics"],
        ("width_px", "height_px", "fx", "fy", "cx", "cy"),
        "camera intrinsics",
    )
    intrinsics = camera["intrinsics"]
    if (
        intrinsics["width_px"] != WIDTH
        or intrinsics["height_px"] != HEIGHT
        or intrinsics["cx"] != 512.0
        or intrinsics["cy"] != 288.0
        or not all(
            isinstance(intrinsics[key], (int, float))
            and not isinstance(intrinsics[key], bool)
            and math.isfinite(intrinsics[key])
            and intrinsics[key] > 0
            for key in ("fx", "fy")
        )
    ):
        raise RuntimeRenderError("camera intrinsics are invalid")
    _validate_matrix(camera["c2w_opencv"], "OpenCV camera matrix")
    if not production:
        _validate_matrix(camera["c2w_blender"], "Blender camera matrix")
        _validate_matrix(
            request["measured_c2w_blender"],
            "measured Blender camera matrix",
        )

    object_registry = request["object_registry"]
    _validate_object_registry_contract(object_registry)
    expected_registry_sha = hashlib.sha256(_canonical_bytes(object_registry)).hexdigest()
    if expected_registry_sha != request["object_registry_sha256"]:
        raise RuntimeRenderError("object registry digest is invalid")

    auxiliary = request["auxiliary_registry"]
    _validate_auxiliary_registry_contract(auxiliary)
    semantics = request["semantic_registry"]
    _expect_list(semantics, len(SEMANTIC_CLASSES), "semantic_registry")
    expected_semantics = [
        {
            "scope": (
                "background" if index == 0 else "auxiliary" if index < 3 else "canonical-object"
            ),
            "semantic_class": semantic_class,
            "semantic_id": index,
        }
        for index, semantic_class in enumerate(SEMANTIC_CLASSES)
    ]
    if semantics != expected_semantics:
        raise RuntimeRenderError("semantic registry is not stable v1")
    return request


def _matrix_error(actual, expected):
    return max(
        abs(float(actual[row][column]) - expected[row][column])
        for row in range(4)
        for column in range(4)
    )


def _matrix_within_float32_tolerance(actual, expected):
    translation_errors = []
    for row in range(4):
        for column in range(4):
            requested = float(expected[row][column])
            delta = abs(float(actual[row][column]) - requested)
            if row < 3 and column < 3:
                allowed = 0.00000032
            elif row < 3 and column == 3:
                allowed = max(5e-8, abs(requested) * 1.2e-7)
                translation_errors.append(delta)
            else:
                allowed = 5e-8
            if delta > allowed + 1e-12:
                return False
    return max(translation_errors) <= 0.00004 + 1e-12


def _layer_collection_renders_object(layer_collection, user_collections, ancestors_visible=True):
    collection = layer_collection.collection
    visible = (
        ancestors_visible
        and not layer_collection.exclude
        and not layer_collection.hide_viewport
        and not collection.hide_viewport
        and not collection.hide_render
    )
    if visible and collection in user_collections:
        return True
    return any(
        _layer_collection_renders_object(child, user_collections, visible)
        for child in layer_collection.children
    )


def _is_render_visible(obj):
    view_layer = bpy.context.view_layer
    try:
        object_visible = obj.visible_get(view_layer=view_layer) and not obj.hide_get(
            view_layer=view_layer,
        )
    except RuntimeError:
        return False
    return (
        not obj.hide_render
        and object_visible
        and _layer_collection_renders_object(
            view_layer.layer_collection,
            set(obj.users_collection),
        )
    )


def _validate_registry_mesh_coverage(object_registry, auxiliary_registry):
    """Validate exact canonical and auxiliary mesh identity before rendering."""

    _validate_object_registry_contract(object_registry)
    _validate_auxiliary_registry_contract(auxiliary_registry)
    registry = {row["object_id"]: row for row in object_registry}
    covered_stable_ids = set()
    auxiliary_meshes = []
    bpy.context.view_layer.update()
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.get("nv_auxiliary", False):
            auxiliary_meshes.append(obj)
            continue
        stable_id = obj.get("nv_stable_id")
        row = registry.get(stable_id)
        expected_root = bpy.data.objects.get(f"nv__{stable_id}") if row is not None else None
        expected_variant = row["variant_id"] or "" if row is not None else None
        if (
            row is None
            or expected_root is None
            or expected_root.type != "EMPTY"
            or expected_root.get("nv_root") is not True
            or expected_root.get("nv_stable_id") != stable_id
            or expected_root.get("nv_instance_id") != row["instance_id"]
            or expected_root.get("nv_semantic_id") != row["semantic_id"]
            or expected_root.get("nv_material_id") != row["material_id"]
            or expected_root.get("nv_variant_id") != expected_variant
            or expected_root.pass_index != row["instance_id"]
            or obj.get("nv_root_id") != stable_id
            or obj.parent is not expected_root
            or obj.get("nv_instance_id") != row["instance_id"]
            or obj.get("nv_semantic_id") != row["semantic_id"]
            or obj.get("nv_material_id") != row["material_id"]
            or obj.get("nv_variant_id") != expected_variant
            or obj.pass_index != row["instance_id"]
        ):
            raise RuntimeRenderError("canonical mesh render tags do not match object registry")
        if not _is_render_visible(obj):
            raise RuntimeRenderError("canonical mesh is hidden from the active render view layer")
        covered_stable_ids.add(stable_id)
    expected_stable_ids = set(registry)
    if covered_stable_ids != expected_stable_ids:
        missing = sorted(expected_stable_ids - covered_stable_ids)
        raise RuntimeRenderError(
            "loaded scene lacks canonical mesh coverage for IDs: " + ", ".join(missing[:8]),
        )

    mesh_rows = [row for row in auxiliary_registry if row["kind"] == "mesh"]
    if {obj.name for obj in auxiliary_meshes} != {row["blender_name"] for row in mesh_rows}:
        raise RuntimeRenderError("auxiliary semantic mesh set is not exactly stable v1")
    for row in mesh_rows:
        obj = bpy.data.objects.get(row["blender_name"])
        if (
            obj is None
            or obj.type != "MESH"
            or obj.get("nv_auxiliary") is not True
            or obj.get("nv_stable_id") != row["auxiliary_id"]
            or obj.get("nv_root_id") != row["auxiliary_id"]
            or obj.get("nv_semantic_id") != row["semantic_id"]
            or obj.get("nv_instance_id") != 0
            or obj.get("nv_material_id") != 0
            or obj.get("nv_variant_id") != ""
            or obj.pass_index != 0
            or not _is_render_visible(obj)
        ):
            raise RuntimeRenderError("auxiliary semantic mesh identity or visibility is invalid")

    world_row = next(row for row in auxiliary_registry if row["kind"] == "world")
    world = bpy.data.worlds.get(world_row["blender_name"])
    if (
        world is None
        or bpy.context.scene.world is not world
        or world.get("nv_auxiliary_id") != world_row["auxiliary_id"]
        or world.get("nv_semantic_id") != world_row["semantic_id"]
    ):
        raise RuntimeRenderError("auxiliary world identity is invalid")


def _validate_camera_data(camera_obj, camera):
    data = camera_obj.data
    intrinsics = camera["intrinsics"]
    expected_lens = float(intrinsics["fx"]) * 36.0 / WIDTH
    expected_fov_x = math.degrees(
        2.0 * math.atan(WIDTH / (2.0 * float(intrinsics["fx"]))),
    )
    if (
        data.type != "PERSP"
        or data.sensor_fit != "HORIZONTAL"
        or abs(float(data.sensor_width) - 36.0) > 1e-7
        or abs(float(data.lens) - expected_lens) > 2e-6
        or abs(float(data.shift_x)) > 1e-7
        or abs(float(data.shift_y)) > 1e-7
        or abs(float(data.clip_start) - 0.1) > 1e-7
        or abs(float(data.clip_end) - 1200.0) > 1e-7
        or data.dof.use_dof
        or abs(float(intrinsics["fx"]) - float(intrinsics["fy"])) > 1e-9
        or abs(float(camera["fov_x_deg"]) - expected_fov_x) > 1e-7
    ):
        raise RuntimeRenderError("loaded camera optics do not match request intrinsics")


def _create_production_camera(request):
    camera = request["camera"]
    object_name = f"nv__{camera['camera_id']}"
    if bpy.data.objects.get(object_name) is not None:
        raise RuntimeRenderError("production camera name collides with loaded scene")
    camera_data = bpy.data.cameras.new(f"{object_name}-data")
    camera_obj = bpy.data.objects.new(object_name, camera_data)
    bpy.context.scene.collection.objects.link(camera_obj)
    camera_obj["nv_camera_id"] = camera["camera_id"]
    camera_obj["nv_production_profile_id"] = request["profile_id"]
    camera_obj["nv_topology_ref"] = camera["topology_ref"]
    camera_obj["nv_production_plan_sha256"] = request["production_plan_sha256"]
    intrinsics = camera["intrinsics"]
    camera_data.type = "PERSP"
    camera_data.sensor_fit = "HORIZONTAL"
    camera_data.sensor_width = 36.0
    camera_data.lens = float(intrinsics["fx"]) * 36.0 / WIDTH
    camera_data.shift_x = 0.0
    camera_data.shift_y = 0.0
    camera_data.clip_start = 0.1
    camera_data.clip_end = 1200.0
    camera_data.dof.use_dof = False
    camera_obj.matrix_world = Matrix(request["requested_c2w_blender"])
    bpy.context.view_layer.update()
    return camera_obj


def _validate_scene_and_prepare_indices(request):
    camera = request["camera"]
    production = _is_production_request(request)
    camera_obj = (
        _create_production_camera(request)
        if production
        else bpy.data.objects.get(f"nv__{camera['camera_id']}")
    )
    if camera_obj is None or camera_obj.type != "CAMERA":
        raise RuntimeRenderError("requested camera is absent from the loaded scene")
    if camera_obj.get("nv_camera_id") != camera["camera_id"]:
        raise RuntimeRenderError("requested camera metadata is invalid")
    _validate_camera_data(camera_obj, camera)
    expected_matrix = _request_blender_matrix(request)
    if not _matrix_within_float32_tolerance(camera_obj.matrix_world, expected_matrix):
        raise RuntimeRenderError("render camera matrix does not match immutable request evidence")

    _validate_registry_mesh_coverage(
        request["object_registry"],
        request["auxiliary_registry"],
    )
    terrain = bpy.data.objects.get("nv__aux-terrain")
    support = bpy.data.objects.get("nv__aux-support-terrain-skirt")
    if (
        terrain is None
        or support is None
        or terrain.type != "MESH"
        or support.type != "MESH"
        or terrain.get("nv_semantic_id") != 1
        or support.get("nv_semantic_id") != 2
    ):
        raise RuntimeRenderError("auxiliary semantic meshes are missing or invalid")
    terrain.pass_index = 127
    support.pass_index = 128
    bpy.context.view_layer.update()
    return camera_obj


def _file_output(
    nodes,
    links,
    render_layers,
    socket_name,
    base_path,
    prefix,
    file_format,
    mode,
    depth,
    *,
    raw_data=False,
):
    node = nodes.new("CompositorNodeOutputFile")
    node.base_path = str(base_path)
    node.file_slots[0].path = prefix
    node.format.file_format = file_format
    node.format.color_mode = mode
    node.format.color_depth = depth
    if file_format == "OPEN_EXR":
        node.format.exr_codec = "ZIP"
    if raw_data:
        node.save_as_render = False
        node.format.color_management = "OVERRIDE"
        node.format.view_settings.view_transform = "Raw"
        node.format.view_settings.look = "None"
        node.format.view_settings.exposure = 0.0
        node.format.view_settings.gamma = 1.0
    if socket_name not in render_layers.outputs:
        raise RuntimeRenderError(f"render pass socket is unavailable: {socket_name}")
    links.new(render_layers.outputs[socket_name], node.inputs[0])
    return node


def _configure_common_render(request, camera_obj):
    scene = bpy.context.scene
    settings = request["settings"]
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.use_file_extension = True
    scene.render.film_transparent = False
    scene.render.use_motion_blur = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = settings["view_transform"]
    scene.view_settings.look = settings["look"]
    scene.view_settings.exposure = settings["exposure"]
    scene.view_settings.gamma = settings["gamma"]
    scene.render.dither_intensity = settings["dither_intensity"]
    scene.camera = camera_obj
    camera_obj.data.dof.use_dof = False
    camera_obj.data.clip_start = 0.1
    camera_obj.data.clip_end = 1200.0
    scene.frame_set(1)
    return scene


def _configure_rgb_render(request, camera_obj, pass_root):
    scene = _configure_common_render(request, camera_obj)
    settings = request["settings"]
    scene.render.engine = settings["engine"]
    scene.render.threads_mode = "FIXED"
    scene.render.threads = settings["rgb_render_threads"]
    scene.eevee.taa_render_samples = settings["render_samples"]
    scene.eevee.use_taa_reprojection = False
    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()
    render_layers = tree.nodes.new("CompositorNodeRLayers")
    render_layers.layer = bpy.context.view_layer.name
    _file_output(
        tree.nodes,
        tree.links,
        render_layers,
        "Image",
        pass_root,
        "rgb-",
        "PNG",
        "RGB",
        "8",
    )


def _inject_data_aovs():
    view_layer = bpy.context.view_layer
    while len(view_layer.aovs):
        view_layer.aovs.remove(view_layer.aovs[0])
    for name in ("InstanceID", "SemanticID"):
        aov = view_layer.aovs.add()
        aov.name = name
        aov.type = "VALUE"
    for material in bpy.data.materials:
        if not material.use_nodes or material.node_tree is None or material.users <= 0:
            continue
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        for suffix, property_name, aov_name in (
            ("instance", "nv_instance_id", "InstanceID"),
            ("semantic", "nv_semantic_id", "SemanticID"),
        ):
            attribute = nodes.new("ShaderNodeAttribute")
            attribute.name = f"nv__render-{suffix}-attribute"
            attribute.attribute_type = "OBJECT"
            attribute.attribute_name = property_name
            output = nodes.new("ShaderNodeOutputAOV")
            output.name = f"nv__render-{suffix}-aov"
            output.aov_name = aov_name
            links.new(attribute.outputs["Fac"], output.inputs["Value"])
    view_layer.update_render_passes()


def _configure_data_render(request, camera_obj, pass_root):
    scene = _configure_common_render(request, camera_obj)
    settings = request["settings"]
    scene.render.engine = settings["data_engine"]
    scene.cycles.device = "CPU"
    scene.cycles.samples = settings["data_render_samples"]
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    scene.cycles.seed = settings["deterministic_seed"]
    scene.cycles.use_animated_seed = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    _inject_data_aovs()

    view_layer = bpy.context.view_layer
    view_layer.use_pass_z = True
    view_layer.use_pass_normal = True
    view_layer.use_pass_position = True
    view_layer.use_pass_object_index = True
    view_layer.pass_alpha_threshold = 0.0
    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()
    render_layers = tree.nodes.new("CompositorNodeRLayers")
    render_layers.layer = view_layer.name
    view_layer.update_render_passes()
    _file_output(
        tree.nodes,
        tree.links,
        render_layers,
        "Depth",
        pass_root,
        "z-",
        "OPEN_EXR",
        "BW",
        "32",
        raw_data=True,
    )
    _file_output(
        tree.nodes,
        tree.links,
        render_layers,
        "Normal",
        pass_root,
        "normal-",
        "OPEN_EXR",
        "RGB",
        "32",
        raw_data=True,
    )
    _file_output(
        tree.nodes,
        tree.links,
        render_layers,
        "Position",
        pass_root,
        "position-",
        "OPEN_EXR",
        "RGB",
        "32",
        raw_data=True,
    )
    _file_output(
        tree.nodes,
        tree.links,
        render_layers,
        "InstanceID",
        pass_root,
        "instance-aov-",
        "OPEN_EXR",
        "BW",
        "32",
        raw_data=True,
    )
    _file_output(
        tree.nodes,
        tree.links,
        render_layers,
        "SemanticID",
        pass_root,
        "semantic-aov-",
        "OPEN_EXR",
        "BW",
        "32",
        raw_data=True,
    )


def _find_output(pass_root, prefix, suffix):
    matches = sorted(pass_root.glob(f"{prefix}*{suffix}"))
    if len(matches) != 1 or not matches[0].is_file() or matches[0].stat().st_size <= 0:
        raise RuntimeRenderError(f"render pass {prefix} did not produce one output")
    return matches[0]


def _load_pixels(path, label):
    image = bpy.data.images.load(str(path), check_existing=False)
    if tuple(image.size) != (WIDTH, HEIGHT):
        bpy.data.images.remove(image)
        raise RuntimeRenderError(f"{label} dimensions are not 1024x576")
    pixels = list(image.pixels[:])
    if len(pixels) != PIXELS * 4:
        bpy.data.images.remove(image)
        raise RuntimeRenderError(f"{label} pixel buffer is incomplete")
    return image, pixels


def _write_float_exr(path, pixels, channel_names, label):
    channels = len(channel_names)
    if len(pixels) != PIXELS * channels:
        raise RuntimeRenderError(f"{label} pixel payload has the wrong channel count")
    spec = oiio.ImageSpec(WIDTH, HEIGHT, channels, oiio.FLOAT)
    spec.channelnames = channel_names
    spec.attribute("compression", "zip")
    spec.attribute("DateTime", CANONICAL_EXR_DATETIME)
    output = oiio.ImageOutput.create(str(path))
    if output is None:
        raise RuntimeRenderError(f"cannot create {label} OpenEXR writer")
    try:
        if not output.open(str(path), spec) or not output.write_image(pixels):
            raise RuntimeRenderError(f"cannot write {label} OpenEXR")
    finally:
        output.close()
    if not path.is_file() or path.stat().st_size <= 4:
        raise RuntimeRenderError(f"{label} OpenEXR did not save")
    with path.open("rb+") as stream:
        if stream.read(4) != EXR_MAGIC:
            raise RuntimeRenderError(f"{label} output is not OpenEXR")
        stream.flush()
        os.fsync(stream.fileno())
    image_input = oiio.ImageInput.open(str(path))
    if image_input is None:
        raise RuntimeRenderError(f"cannot decode saved {label} OpenEXR")
    try:
        decoded_spec = image_input.spec()
        decoded = image_input.read_image(oiio.FLOAT)
    finally:
        image_input.close()
    if (
        decoded is None
        or decoded_spec.width != WIDTH
        or decoded_spec.height != HEIGHT
        or decoded_spec.nchannels != channels
        or tuple(decoded_spec.channelnames) != tuple(channel_names)
        or str(decoded_spec.format) != "float"
        or decoded_spec.get_string_attribute("compression") != "zip"
        or decoded_spec.get_string_attribute("DateTime") != CANONICAL_EXR_DATETIME
        or decoded.shape != (HEIGHT, WIDTH, channels)
    ):
        raise RuntimeRenderError(f"saved {label} OpenEXR channel contract is invalid")
    flat = decoded.reshape(-1)
    if any(not math.isfinite(float(value)) for value in flat):
        raise RuntimeRenderError(f"saved {label} OpenEXR contains a non-finite value")
    _validate_no_private_metadata(path, label)
    return flat


def _depth_output(request, axial_path, position_path, destination):
    axial_image, axial_pixels = _load_pixels(axial_path, "axial depth")
    position_image, position_pixels = _load_pixels(position_path, "position")
    intrinsics = request["camera"]["intrinsics"]
    eye = [_request_blender_matrix(request)[row][3] for row in range(3)]
    output = array("f", [0.0]) * PIXELS
    minimum = math.inf
    maximum = 0.0
    background = 0
    maximum_error = 0.0
    try:
        for pixel_index in range(PIXELS):
            offset = pixel_index * 4
            axial = float(axial_pixels[offset])
            if not math.isfinite(axial):
                raise RuntimeRenderError("axial depth pass contains a non-finite value")
            if axial <= 0.0 or axial >= 1200.0:
                value = 0.0
                background += 1
            else:
                x_px = pixel_index % WIDTH
                y_bottom = pixel_index // WIDTH
                u_px = x_px + 0.5
                v_px = HEIGHT - y_bottom - 0.5
                x = (u_px - intrinsics["cx"]) / intrinsics["fx"]
                y = (v_px - intrinsics["cy"]) / intrinsics["fy"]
                value = axial * math.sqrt(1.0 + x * x + y * y)
                if not math.isfinite(value) or value <= 0.0:
                    raise RuntimeRenderError("converted camera range is not finite and positive")
                world = position_pixels[offset : offset + 3]
                if not all(math.isfinite(component) for component in world):
                    raise RuntimeRenderError("position pass contains a non-finite value")
                measured = math.sqrt(sum((world[index] - eye[index]) ** 2 for index in range(3)))
                maximum_error = max(maximum_error, abs(measured - value))
                minimum = min(minimum, value)
                maximum = max(maximum, value)
            target_index = (HEIGHT - 1 - (pixel_index // WIDTH)) * WIDTH + (pixel_index % WIDTH)
            output[target_index] = value
    finally:
        bpy.data.images.remove(axial_image)
        bpy.data.images.remove(position_image)
    if not math.isfinite(minimum) or maximum <= 0.0:
        raise RuntimeRenderError("depth pass contains no visible finite surface")
    if maximum_error > 0.01:
        raise RuntimeRenderError(
            f"camera range conversion disagrees with Position pass by {maximum_error:.9f} m",
        )
    decoded = _write_float_exr(destination, output, ("V",), "depth")
    if any(float(value) < 0.0 for value in decoded):
        raise RuntimeRenderError("saved depth output contains a negative value")
    return minimum, maximum, background, maximum_error, decoded


def _normal_output(source, destination):
    source_image, source_pixels = _load_pixels(source, "normal")
    output = array("f", [0.0]) * (PIXELS * 3)
    maximum_error = 0.0
    visible = 0
    try:
        for pixel_index in range(PIXELS):
            offset = pixel_index * 4
            vector = source_pixels[offset : offset + 3]
            if not all(math.isfinite(component) for component in vector):
                raise RuntimeRenderError("normal pass contains a non-finite vector")
            length = math.sqrt(sum(component * component for component in vector))
            if length <= 1e-8:
                normalized = (0.0, 0.0, 0.0)
            else:
                normalized = tuple(component / length for component in vector)
                visible += 1
                normalized_length = math.sqrt(
                    sum(component * component for component in normalized),
                )
                maximum_error = max(maximum_error, abs(normalized_length - 1.0))
            target_index = (
                (HEIGHT - 1 - (pixel_index // WIDTH)) * WIDTH + (pixel_index % WIDTH)
            ) * 3
            output[target_index : target_index + 3] = array("f", normalized)
    finally:
        bpy.data.images.remove(source_image)
    if visible == 0 or maximum_error > 1e-6:
        raise RuntimeRenderError("normal output has no finite unit world-space vectors")
    decoded = _write_float_exr(destination, output, ("X", "Y", "Z"), "normal")
    decoded_maximum_error = 0.0
    for pixel_index in range(PIXELS):
        vector = decoded[pixel_index * 3 : pixel_index * 3 + 3]
        length = math.sqrt(sum(float(component) ** 2 for component in vector))
        if length > 1e-8:
            decoded_maximum_error = max(decoded_maximum_error, abs(length - 1.0))
    if decoded_maximum_error > 0.001:
        raise RuntimeRenderError("saved normal output contains a non-unit vector")
    return max(maximum_error, decoded_maximum_error), decoded


def _png_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _read_png_chunks(path, label):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeRenderError(f"cannot read saved {label} PNG") from exc
    if not raw.startswith(PNG_SIGNATURE):
        raise RuntimeRenderError(f"saved {label} output is not PNG")
    chunks = []
    offset = len(PNG_SIGNATURE)
    while offset < len(raw):
        if len(raw) - offset < 12:
            raise RuntimeRenderError(f"saved {label} PNG chunk is truncated")
        length = struct.unpack_from(">I", raw, offset)[0]
        kind = raw[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        crc_end = payload_end + 4
        if crc_end > len(raw):
            raise RuntimeRenderError(f"saved {label} PNG chunk is truncated")
        payload = raw[payload_start:payload_end]
        stored_crc = struct.unpack_from(">I", raw, payload_end)[0]
        expected_crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        if stored_crc != expected_crc:
            raise RuntimeRenderError(f"saved {label} PNG CRC is invalid")
        chunks.append((kind, payload))
        offset = crc_end
    if offset != len(raw):
        raise RuntimeRenderError(f"saved {label} PNG has trailing bytes")
    return chunks


def _decode_canonical_png(path, bit_depth, color_type, channels, label):
    chunks = _read_png_chunks(path, label)
    if [kind for kind, _payload in chunks] != [b"IHDR", b"IDAT", b"IEND"]:
        raise RuntimeRenderError(f"saved {label} PNG chunks are not canonical")
    header, compressed, end = (payload for _kind, payload in chunks)
    if len(header) != 13 or end:
        raise RuntimeRenderError(f"saved {label} PNG structure is invalid")
    width, height, actual_depth, actual_color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    if (
        width != WIDTH
        or height != HEIGHT
        or actual_depth != bit_depth
        or actual_color != color_type
        or compression != 0
        or filtering != 0
        or interlace != 0
        or channels != {0: 1, 2: 3}.get(color_type)
    ):
        raise RuntimeRenderError(f"saved {label} PNG image contract is invalid")
    bytes_per_sample = bit_depth // 8
    row_bytes = WIDTH * channels * bytes_per_sample
    expected_size = HEIGHT * (row_bytes + 1)
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(compressed, expected_size + 1)
        decoded += decompressor.flush()
    except zlib.error as exc:
        raise RuntimeRenderError(f"saved {label} PNG compressed payload is invalid") from exc
    if (
        len(decoded) != expected_size
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        raise RuntimeRenderError(f"saved {label} PNG decoded size is invalid")
    pixel_bytes = bytearray()
    for row in range(HEIGHT):
        start = row * (row_bytes + 1)
        if decoded[start] != 0:
            raise RuntimeRenderError(f"saved {label} PNG uses a non-canonical row filter")
        pixel_bytes.extend(decoded[start + 1 : start + 1 + row_bytes])
    if color_type == 2:
        return bytes(pixel_bytes)
    if bit_depth == 8:
        return list(pixel_bytes)
    return [
        struct.unpack_from(">H", pixel_bytes, offset)[0] for offset in range(0, len(pixel_bytes), 2)
    ]


def _write_canonical_png(path, rows, *, bit_depth, color_type):
    compressed = zlib.compress(rows, level=6)
    header = struct.pack(
        ">IIBBBBB",
        WIDTH,
        HEIGHT,
        bit_depth,
        color_type,
        0,
        0,
        0,
    )
    payload = (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_grayscale_png(path, values, bit_depth):
    if len(values) != PIXELS or bit_depth not in {8, 16}:
        raise RuntimeRenderError("integer mask payload is invalid")
    bytes_per_sample = bit_depth // 8
    rows = bytearray()
    for row in range(HEIGHT):
        scanline = bytearray(1 + WIDTH * bytes_per_sample)
        for column in range(WIDTH):
            value = values[row * WIDTH + column]
            if value < 0 or value >= 1 << bit_depth:
                raise RuntimeRenderError("integer mask value exceeds PNG bit depth")
            if bit_depth == 8:
                scanline[1 + column] = value
            else:
                struct.pack_into(">H", scanline, 1 + column * 2, value)
        rows.extend(scanline)
    _write_canonical_png(path, bytes(rows), bit_depth=bit_depth, color_type=0)
    decoded = _decode_canonical_png(path, bit_depth, 0, 1, "integer mask")
    if decoded != values:
        raise RuntimeRenderError("saved integer mask PNG pixels do not match the source IDs")
    _validate_no_private_metadata(path, "integer mask")
    return decoded


def _write_rgb_png(path, pixels):
    pixels = bytes(pixels)
    if len(pixels) != PIXELS * 3:
        raise RuntimeRenderError("RGB pixel payload has the wrong channel count")
    rows = bytearray()
    row_bytes = WIDTH * 3
    for row in range(HEIGHT):
        rows.append(0)
        start = row * row_bytes
        rows.extend(pixels[start : start + row_bytes])
    _write_canonical_png(path, bytes(rows), bit_depth=8, color_type=2)
    decoded = _decode_canonical_png(path, 8, 2, 3, "RGB")
    if decoded != pixels:
        raise RuntimeRenderError("saved RGB PNG pixels do not match the decoded render")
    _validate_no_private_metadata(path, "RGB")
    return decoded


def _decode_rgb_png(path):
    image_input = oiio.ImageInput.open(str(path))
    if image_input is None:
        raise RuntimeRenderError("cannot decode rendered RGB PNG")
    try:
        spec = image_input.spec()
        decoded = image_input.read_image(oiio.UINT8)
    finally:
        image_input.close()
    if (
        decoded is None
        or spec.width != WIDTH
        or spec.height != HEIGHT
        or spec.nchannels != 3
        or str(spec.format) != "uint8"
        or decoded.shape != (HEIGHT, WIDTH, 3)
    ):
        raise RuntimeRenderError("rendered RGB PNG pixel contract is invalid")
    return decoded.tobytes()


def _has_private_path_fragment(value):
    normalized = str(value).lower().replace("\\", "/")
    return (
        ".nantai-studio" in normalized
        or "/users/" in normalized
        or "/home/" in normalized
        or "/appdata/" in normalized
        or "/temp/" in normalized
        or "/tmp/" in normalized
        or re.search(r"(?:^|[^a-z])[a-z]:/", normalized) is not None
    )


def _validate_no_private_metadata(path, label):
    metadata_values = []
    if path.suffix.lower() == ".png":
        for kind, payload in _read_png_chunks(path, label):
            if kind not in {b"IHDR", b"IDAT", b"IEND"}:
                metadata_values.append(kind.decode("latin-1", "replace"))
                metadata_values.append(payload.decode("latin-1", "replace"))
    elif path.suffix.lower() == ".exr":
        image_input = oiio.ImageInput.open(str(path))
        if image_input is None:
            raise RuntimeRenderError(f"cannot inspect saved {label} OpenEXR metadata")
        try:
            for attribute in image_input.spec().extra_attribs:
                metadata_values.extend((attribute.name, str(attribute.value)))
        finally:
            image_input.close()
    if any(_has_private_path_fragment(value) for value in metadata_values):
        raise RuntimeRenderError(f"saved {label} metadata exposes a private filesystem path")


def _mask_outputs(request, instance_aov_path, semantic_aov_path, instance_path, semantic_path):
    instance_image, instance_pixels = _load_pixels(instance_aov_path, "instance AOV")
    semantic_image, semantic_pixels = _load_pixels(semantic_aov_path, "semantic AOV")
    semantic_by_instance = {
        row["instance_id"]: row["semantic_id"] for row in request["object_registry"]
    }
    instances = [0] * PIXELS
    semantics = [0] * PIXELS
    observed_instances = set()
    observed_semantics = set()
    try:
        for pixel_index in range(PIXELS):
            instance_value = float(instance_pixels[pixel_index * 4])
            semantic_value = float(semantic_pixels[pixel_index * 4])
            if (
                not math.isfinite(instance_value)
                or not math.isfinite(semantic_value)
                or abs(instance_value - round(instance_value)) > 1e-4
                or abs(semantic_value - round(semantic_value)) > 1e-4
            ):
                raise RuntimeRenderError("integer ID AOV contains a non-integer value")
            instance_id = int(round(instance_value))
            semantic_id = int(round(semantic_value))
            if instance_id == 0:
                if semantic_id not in {0, 1, 2}:
                    raise RuntimeRenderError("background/auxiliary AOV semantic ID is invalid")
            elif instance_id not in semantic_by_instance:
                raise RuntimeRenderError(f"instance AOV contains unregistered ID {instance_id}")
            elif semantic_by_instance[instance_id] != semantic_id:
                raise RuntimeRenderError("semantic AOV does not match the object registry")
            target_index = (HEIGHT - 1 - (pixel_index // WIDTH)) * WIDTH + (pixel_index % WIDTH)
            instances[target_index] = instance_id
            semantics[target_index] = semantic_id
            observed_instances.add(instance_id)
            observed_semantics.add(semantic_id)
    finally:
        bpy.data.images.remove(instance_image)
        bpy.data.images.remove(semantic_image)
    decoded_instances = _write_grayscale_png(instance_path, instances, 16)
    decoded_semantics = _write_grayscale_png(semantic_path, semantics, 8)
    return (
        sorted(observed_instances),
        sorted(observed_semantics),
        decoded_instances,
        decoded_semantics,
    )


def _validate_cross_layer_pixels(depth, normals, instances, semantics):
    if not (
        len(depth) == PIXELS
        and len(normals) == PIXELS * 3
        and len(instances) == PIXELS
        and len(semantics) == PIXELS
    ):
        raise RuntimeRenderError("decoded layer buffers do not share one pixel grid")
    for pixel_index in range(PIXELS):
        depth_value = float(depth[pixel_index])
        normal = normals[pixel_index * 3 : pixel_index * 3 + 3]
        normal_length = math.sqrt(sum(float(component) ** 2 for component in normal))
        instance_id = instances[pixel_index]
        semantic_id = semantics[pixel_index]
        if semantic_id == 0:
            if depth_value != 0.0 or normal_length > 1e-8 or instance_id != 0:
                raise RuntimeRenderError(
                    "background pixel does not encode zero depth/normal/instance",
                )
        else:
            if depth_value <= 0.0 or abs(normal_length - 1.0) > 0.001:
                raise RuntimeRenderError(
                    "visible semantic pixel lacks positive depth and unit normal",
                )
        if instance_id > 0 and not 3 <= semantic_id < len(SEMANTIC_CLASSES):
            raise RuntimeRenderError("canonical instance pixel has a non-canonical semantic ID")


def _production_layer_counts(
    depth,
    normals,
    instances,
    semantics,
    *,
    policy,
    object_registry,
    semantic_registry,
):
    """Measure raw integer counts from already decoded production buffers."""

    if not (
        len(depth) == PIXELS
        and len(normals) == PIXELS * 3
        and len(instances) == PIXELS
        and len(semantics) == PIXELS
    ):
        raise RuntimeRenderError(
            "production layer buffers do not share one pixel grid",
        )
    expected_policy_keys = {
        "near_depth_m",
        "upper_region_end_row_exclusive",
        "ground_semantic_ids",
        "sky_semantic_id",
    }
    if (
        not isinstance(policy, dict)
        or set(policy) != expected_policy_keys
        or not isinstance(policy["near_depth_m"], (int, float))
        or isinstance(policy["near_depth_m"], bool)
        or not math.isfinite(float(policy["near_depth_m"]))
        or float(policy["near_depth_m"]) <= 0.0
        or not isinstance(policy["upper_region_end_row_exclusive"], int)
        or isinstance(policy["upper_region_end_row_exclusive"], bool)
        or not 1 <= policy["upper_region_end_row_exclusive"] <= HEIGHT
        or not isinstance(policy["ground_semantic_ids"], list)
        or not policy["ground_semantic_ids"]
        or any(type(row) is not int for row in policy["ground_semantic_ids"])
        or policy["ground_semantic_ids"]
        != sorted(set(policy["ground_semantic_ids"]))
        or type(policy["sky_semantic_id"]) is not int
    ):
        raise RuntimeRenderError(
            "production layer statistics policy is invalid",
        )
    semantic_ids = {
        row.get("semantic_id")
        for row in semantic_registry
        if isinstance(row, dict)
    }
    if (
        len(semantic_ids) != len(semantic_registry)
        or policy["sky_semantic_id"] not in semantic_ids
        or not set(policy["ground_semantic_ids"]) <= semantic_ids
    ):
        raise RuntimeRenderError(
            "production layer policy references an unregistered semantic ID",
        )
    semantic_by_instance = {
        row.get("instance_id"): row.get("semantic_id")
        for row in object_registry
        if isinstance(row, dict)
    }
    if (
        len(semantic_by_instance) != len(object_registry)
        or None in semantic_by_instance
        or any(
            type(instance_id) is not int
            or instance_id <= 0
            or semantic_id not in semantic_ids
            for instance_id, semantic_id in semantic_by_instance.items()
        )
    ):
        raise RuntimeRenderError(
            "production layer object registry is invalid",
        )

    upper_rows = policy["upper_region_end_row_exclusive"]
    upper_pixels = WIDTH * upper_rows
    near_depth_m = float(policy["near_depth_m"])
    sky_semantic_id = policy["sky_semantic_id"]
    ground_semantic_ids = set(policy["ground_semantic_ids"])
    counts = {
        "total_pixel_count": PIXELS,
        "upper_pixel_count": upper_pixels,
        "valid_depth_pixel_count": 0,
        "valid_normal_pixel_count": 0,
        "registered_instance_pixel_count": 0,
        "valid_semantic_pixel_count": 0,
        "sky_pixel_count": 0,
        "upper_ground_pixel_count": 0,
        "near_depth_pixel_count": 0,
    }
    near_instances = {}
    upper_instances = {}
    for pixel_index in range(PIXELS):
        depth_value = float(depth[pixel_index])
        normal = normals[pixel_index * 3 : pixel_index * 3 + 3]
        normal_length = math.sqrt(
            sum(float(component) ** 2 for component in normal),
        )
        instance_id = instances[pixel_index]
        semantic_id = semantics[pixel_index]
        if (
            type(instance_id) is not int
            or type(semantic_id) is not int
            or semantic_id not in semantic_ids
        ):
            raise RuntimeRenderError(
                "production layer contains an unregistered semantic ID",
            )
        if instance_id > 0:
            if instance_id not in semantic_by_instance:
                raise RuntimeRenderError(
                    f"production layer contains unregistered instance ID {instance_id}",
                )
            if semantic_by_instance[instance_id] != semantic_id:
                raise RuntimeRenderError(
                    "production layer instance and semantic registries disagree",
                )
            counts["registered_instance_pixel_count"] += 1
        elif instance_id != 0:
            raise RuntimeRenderError(
                "production layer contains a negative instance ID",
            )
        if math.isfinite(depth_value) and depth_value > 0.0:
            counts["valid_depth_pixel_count"] += 1
            if depth_value < near_depth_m:
                counts["near_depth_pixel_count"] += 1
                if instance_id > 0:
                    near_instances[instance_id] = (
                        near_instances.get(instance_id, 0) + 1
                    )
        if math.isfinite(normal_length) and abs(normal_length - 1.0) <= 0.001:
            counts["valid_normal_pixel_count"] += 1
        if semantic_id == sky_semantic_id:
            counts["sky_pixel_count"] += 1
        else:
            counts["valid_semantic_pixel_count"] += 1
        if pixel_index // WIDTH < upper_rows:
            if semantic_id in ground_semantic_ids:
                counts["upper_ground_pixel_count"] += 1
            if instance_id > 0:
                upper_instances[instance_id] = (
                    upper_instances.get(instance_id, 0) + 1
                )

    def dominant(rows):
        if not rows:
            return None, 0
        return min(rows.items(), key=lambda row: (-row[1], row[0]))

    near_id, near_count = dominant(near_instances)
    upper_id, upper_count = dominant(upper_instances)
    counts.update(
        {
            "dominant_near_instance_id": near_id,
            "dominant_near_instance_pixel_count": near_count,
            "dominant_upper_instance_id": upper_id,
            "dominant_upper_instance_pixel_count": upper_count,
        },
    )
    return counts


def _matrix_payload(matrix):
    return [[float(matrix[row][column]) for column in range(4)] for row in range(4)]


def _write_camera_metadata(request, path, measured_c2w_blender):
    camera = request["camera"]
    production = _is_production_request(request)
    if not _matrix_within_float32_tolerance(
        measured_c2w_blender,
        _request_blender_matrix(request),
    ):
        raise RuntimeRenderError("rendered camera pose diverged from immutable request")
    payload = {
        "schema_version": (
            LOCAL_PRODUCTION_CAMERA_SCHEMA
            if production
            else (
                LOCAL_CAMERA_SCHEMA
                if request["schema_version"] == LOCAL_REQUEST_SCHEMA
                else CAMERA_SCHEMA
            )
        ),
        "build_id": request["build_id"],
        "render_id": request["render_id"],
        "synthetic": True,
        "verification_level": request["verification_level"],
        "blender_executable_sha256": request["blender_executable_sha256"],
        "camera_id": camera["camera_id"],
        "image_width_px": WIDTH,
        "image_height_px": HEIGHT,
        "coordinate_system": "opencv-c2w-right-down-forward-meters",
        "pixel_origin": "top-left",
        "pixel_center_offset": [0.5, 0.5],
        "depth_encoding": DEPTH_ENCODING,
        "depth_units": "m",
        "depth_invalid_value_m": 0.0,
        "normal_encoding": NORMAL_ENCODING,
        "normal_axes": "blender-right-handed-z-up",
        "normal_background_xyz": [0.0, 0.0, 0.0],
        "clip_start_m": 0.1,
        "clip_end_m": 1200.0,
        "depth_channel_layout": "V-float32-zip",
        "normal_channel_layout": "X,Y,Z-float32-zip",
        "instance_pixel_type": "uint16-grayscale-png",
        "semantic_pixel_type": "uint8-grayscale-png",
        "settings_sha256": hashlib.sha256(_canonical_bytes(request["settings"])).hexdigest(),
        "intrinsics": camera["intrinsics"],
        "requested_c2w_opencv": camera["c2w_opencv"],
        "requested_c2w_blender": _request_blender_matrix(request),
        "measured_c2w_opencv": _blender_c2w_to_opencv(measured_c2w_blender),
        "measured_c2w_blender": measured_c2w_blender,
        "object_registry_sha256": request["object_registry_sha256"],
        "semantic_registry": request["semantic_registry"],
    }
    if production:
        payload.update(
            {
                "profile_id": request["profile_id"],
                "production_plan_sha256": request["production_plan_sha256"],
                "camera_registry_sha256": request["camera_registry_sha256"],
                "elevated_topology_sha256": request["elevated_topology_sha256"],
                "group_id": camera["group_id"],
                "topology_ref": camera["topology_ref"],
                "arc_length_m": camera["arc_length_m"],
                "audit_only": camera["audit_only"],
                "disclosure": camera["disclosure"],
                "preflight_id": request["preflight_id"],
                "quality_policy_sha256": request[
                    "quality_policy_sha256"
                ],
            },
        )
    else:
        payload.update(
            {
                "category": camera["category"],
                "split": camera["split"],
            },
        )
    with path.open("xb") as stream:
        stream.write(_canonical_bytes(payload))
        stream.flush()
        os.fsync(stream.fileno())


def _artifact_records(work_dir, camera_id):
    rows = []
    contract = (
        ("rgb", f"rgb/{camera_id}.png"),
        ("depth", f"depth/{camera_id}.exr"),
        ("normal", f"normal/{camera_id}.exr"),
        ("instance-mask", f"instance/{camera_id}.png"),
        ("semantic-mask", f"semantic/{camera_id}.png"),
        ("camera-metadata", f"cameras/{camera_id}.json"),
    )
    for kind, portable_path in contract:
        path = work_dir / Path(portable_path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeRenderError(f"render artifact is missing: {portable_path}")
        rows.append(
            {
                "kind": kind,
                "path": portable_path,
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            },
        )
    return rows


def _execute_render(request, staging_path):
    if staging_path.exists() or _is_reparse_point(staging_path):
        raise RuntimeRenderError("staging directory must be absent")
    work_dir = staging_path.with_name(f".{staging_path.name}.tmp-{request['render_id'][:12]}")
    if work_dir.exists() or _is_reparse_point(work_dir):
        raise RuntimeRenderError("private temporary frame directory already exists")
    work_dir.mkdir()
    try:
        camera_id = request["camera"]["camera_id"]
        for layer in ("rgb", "depth", "normal", "instance", "semantic", "cameras"):
            (work_dir / layer).mkdir()
        pass_root = work_dir / "p"
        pass_root.mkdir()
        camera_obj = _validate_scene_and_prepare_indices(request)
        _configure_rgb_render(request, camera_obj, pass_root)
        bpy.ops.render.render(write_still=False)
        rgb_temp = _find_output(pass_root, "rgb-", ".png")
        rgb_path = work_dir / f"rgb/{camera_id}.png"
        rgb_pixels = _decode_rgb_png(rgb_temp)
        _write_rgb_png(rgb_path, rgb_pixels)
        rgb_temp.unlink()

        _configure_data_render(request, camera_obj, pass_root)
        bpy.ops.render.render(write_still=False)
        axial_temp = _find_output(pass_root, "z-", ".exr")
        normal_temp = _find_output(pass_root, "normal-", ".exr")
        position_temp = _find_output(pass_root, "position-", ".exr")
        instance_temp = _find_output(pass_root, "instance-aov-", ".exr")
        semantic_temp = _find_output(pass_root, "semantic-aov-", ".exr")

        depth_min, depth_max, background, depth_error, depth_pixels = _depth_output(
            request,
            axial_temp,
            position_temp,
            work_dir / f"depth/{camera_id}.exr",
        )
        normal_error, normal_pixels = _normal_output(
            normal_temp,
            work_dir / f"normal/{camera_id}.exr",
        )
        instance_ids, semantic_ids, instance_pixels, semantic_pixels = _mask_outputs(
            request,
            instance_temp,
            semantic_temp,
            work_dir / f"instance/{camera_id}.png",
            work_dir / f"semantic/{camera_id}.png",
        )
        _validate_cross_layer_pixels(
            depth_pixels,
            normal_pixels,
            instance_pixels,
            semantic_pixels,
        )
        measured_c2w_blender = _matrix_payload(camera_obj.matrix_world)
        _write_camera_metadata(
            request,
            work_dir / f"cameras/{camera_id}.json",
            measured_c2w_blender,
        )
        shutil.rmtree(pass_root)

        artifacts = _artifact_records(work_dir, camera_id)
        production = _is_production_request(request)
        report = {
            "schema_version": (
                LOCAL_PRODUCTION_REPORT_SCHEMA
                if production
                else (
                    LOCAL_REPORT_SCHEMA
                    if request["schema_version"] == LOCAL_REQUEST_SCHEMA
                    else REPORT_SCHEMA
                )
            ),
            "build_id": request["build_id"],
            "render_id": request["render_id"],
            "synthetic": True,
            "verification_level": request["verification_level"],
            "fidelity": "simplified-pbr-not-render-parity",
            "blender_executable_sha256": request["blender_executable_sha256"],
            "camera_id": camera_id,
            "image_width_px": WIDTH,
            "image_height_px": HEIGHT,
            "depth_encoding": DEPTH_ENCODING,
            "normal_encoding": NORMAL_ENCODING,
            "depth_channel_layout": "V-float32-zip",
            "normal_channel_layout": "X,Y,Z-float32-zip",
            "instance_pixel_type": "uint16-grayscale-png",
            "semantic_pixel_type": "uint8-grayscale-png",
            "settings_sha256": hashlib.sha256(
                _canonical_bytes(request["settings"]),
            ).hexdigest(),
            "artifacts": artifacts,
            "statistics": {
                "depth_min_m": round(depth_min, 9),
                "depth_max_m": round(depth_max, 9),
                "depth_background_pixels": background,
                "depth_max_range_error_m": round(depth_error, 9),
                "normal_max_unit_error": round(normal_error, 9),
                "instance_ids": instance_ids,
                "semantic_ids": semantic_ids,
            },
            "validation": {
                "dimensions_match": True,
                "depth_finite_nonnegative": True,
                "depth_camera_range_consistent": True,
                "normal_finite_unit_world_space": True,
                "instance_ids_registered": True,
                "semantic_ids_registered": True,
                "camera_metadata_matches": True,
            },
        }
        if production:
            report.update(
                {
                    "profile_id": request["profile_id"],
                    "production_plan_sha256": request["production_plan_sha256"],
                    "camera_registry_sha256": request["camera_registry_sha256"],
                    "elevated_topology_sha256": request["elevated_topology_sha256"],
                    "group_id": request["camera"]["group_id"],
                    "topology_ref": request["camera"]["topology_ref"],
                    "preflight_id": request["preflight_id"],
                    "quality_policy_sha256": request[
                        "quality_policy_sha256"
                    ],
                },
            )
        report["content_sha256"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
        report_path = work_dir / "frame-report.json"
        with report_path.open("xb") as stream:
            stream.write(_canonical_bytes(report))
            stream.flush()
            os.fsync(stream.fileno())
        work_dir.rename(staging_path)
        print(
            f"NANTAI_RENDER_OK render_id={request['render_id']} camera_id={camera_id} files=6",
            flush=True,
        )
    except Exception as exc:
        if work_dir.exists() and not _is_reparse_point(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        if isinstance(exc, RuntimeRenderError):
            raise
        raise RuntimeRenderError(f"frame render failed: {type(exc).__name__}: {exc}") from exc


def main() -> None:
    request_path, staging_path = _runtime_argv(sys.argv)
    request = _validate_request(_load_request(request_path))
    _execute_render(request, staging_path)


if __name__ == "__main__":
    try:
        main()
    except RuntimeRenderError as exc:
        print(f"NANTAI_RENDER_ERROR {exc}", flush=True)
        raise SystemExit(17) from None
