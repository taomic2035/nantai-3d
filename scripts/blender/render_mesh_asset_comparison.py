"""Render one fail-closed 4K v1/v2 mesh contact sheet for visual review.

This script deliberately produces no provenance upgrade.  It verifies the
bundle, GLB, and shared-texture bytes that it consumes, then records the
resulting PNG only as ``none-visual-review-only`` evidence.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import math
import os
import re
import struct
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

REPORT_SCHEMA = "nantai.synthetic-village.mesh-near-comparison.v1"
V1_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v1"
V2_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v2"
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
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GLB_PATH_PATTERN = re.compile(r"^objects/([0-9a-f]{64})\.glb$")
TEXTURE_PATH_PATTERN = re.compile(r"^textures/([0-9a-f]{64})\.png$")
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_GLB_BYTES = 64 * 1024 * 1024
MAX_TEXTURE_BYTES = 32 * 1024 * 1024
IMAGE_WIDTH = 3840
IMAGE_HEIGHT = 2160


class ComparisonRenderError(RuntimeError):
    """The visual comparison cannot be bound to exact verified inputs."""


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    signature: tuple[int, int, int, int]
    sha256: str
    byte_count: int


@dataclass(frozen=True)
class BundleInput:
    root: Path
    manifest: dict[str, object]
    snapshots: tuple[FileSnapshot, ...]
    lod2_by_asset: dict[str, dict[str, object]]


def _reject_duplicate_keys(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ComparisonRenderError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_nonfinite(value):
    raise ComparisonRenderError(f"JSON contains non-finite number: {value}")


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


def _stat_signature(stat_result):
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _real_directory(path, label):
    path = Path(path).absolute()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ComparisonRenderError(f"{label} is unavailable") from exc
    if resolved != path or path.is_symlink() or not path.is_dir():
        raise ComparisonRenderError(f"{label} is redirected")
    cursor = path
    while cursor != cursor.parent:
        if cursor.is_symlink():
            raise ComparisonRenderError(f"{label} has a redirected ancestor")
        cursor = cursor.parent
    return path


def _snapshot_regular_file(path, maximum, label, expected_sha256=None):
    path = Path(path).absolute()
    if path.is_symlink():
        raise ComparisonRenderError(f"{label} is redirected")
    try:
        before = path.stat()
        if (
            not path.is_file()
            or before.st_size <= 0
            or before.st_size > maximum
        ):
            raise ComparisonRenderError(f"{label} is not a bounded direct file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_signature(before) != _stat_signature(opened):
                raise ComparisonRenderError(f"{label} changed before hashing")
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
    except ComparisonRenderError:
        raise
    except OSError as exc:
        raise ComparisonRenderError(f"{label} cannot be read") from exc
    signature = _stat_signature(before)
    if (
        signature != _stat_signature(after_open)
        or signature != _stat_signature(after)
    ):
        raise ComparisonRenderError(f"{label} changed while hashing")
    measured_sha256 = digest.hexdigest()
    if expected_sha256 is not None and measured_sha256 != expected_sha256:
        raise ComparisonRenderError(f"{label} SHA-256 does not match manifest")
    return FileSnapshot(
        path=path,
        signature=signature,
        sha256=measured_sha256,
        byte_count=before.st_size,
    )


def _verify_snapshots_unchanged(snapshots):
    for snapshot in snapshots:
        current = _snapshot_regular_file(
            snapshot.path,
            max(snapshot.byte_count, 1),
            f"comparison input {snapshot.path.name}",
            snapshot.sha256,
        )
        if (
            current.signature != snapshot.signature
            or current.byte_count != snapshot.byte_count
        ):
            raise ComparisonRenderError(
                f"comparison input changed during render: {snapshot.path.name}",
            )


def _canonicalize_png_chunks(path):
    """Strip Blender's clock-dependent ancillary chunks without changing pixels."""

    path = Path(path).absolute()
    raw = path.read_bytes()
    signature = b"\x89PNG\r\n\x1a\n"
    if not raw.startswith(signature):
        raise ComparisonRenderError("rendered comparison is not a PNG")
    output = bytearray(signature)
    offset = len(signature)
    seen_ihdr = False
    seen_idat = False
    seen_iend = False
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise ComparisonRenderError("rendered PNG has a truncated chunk")
        byte_count = struct.unpack(">I", raw[offset : offset + 4])[0]
        end = offset + 12 + byte_count
        if end > len(raw):
            raise ComparisonRenderError("rendered PNG chunk exceeds file bounds")
        chunk_type = raw[offset + 4 : offset + 8]
        chunk_data = raw[offset + 8 : offset + 8 + byte_count]
        stored_crc = struct.unpack(">I", raw[end - 4 : end])[0]
        measured_crc = binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if stored_crc != measured_crc:
            raise ComparisonRenderError("rendered PNG chunk CRC is invalid")
        if chunk_type == b"IHDR":
            if seen_ihdr or seen_idat or byte_count != 13:
                raise ComparisonRenderError("rendered PNG IHDR ordering is invalid")
            width, height, depth, colour, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", chunk_data)
            )
            if (
                width != IMAGE_WIDTH
                or height != IMAGE_HEIGHT
                or depth != 8
                or colour != 2
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise ComparisonRenderError("rendered PNG pixel contract is invalid")
            seen_ihdr = True
            output.extend(raw[offset:end])
        elif chunk_type == b"IDAT":
            if not seen_ihdr or seen_iend:
                raise ComparisonRenderError("rendered PNG IDAT ordering is invalid")
            seen_idat = True
            output.extend(raw[offset:end])
        elif chunk_type == b"IEND":
            if (
                not seen_ihdr
                or not seen_idat
                or seen_iend
                or byte_count != 0
                or end != len(raw)
            ):
                raise ComparisonRenderError("rendered PNG IEND ordering is invalid")
            seen_iend = True
            output.extend(raw[offset:end])
        elif 65 <= chunk_type[0] <= 90:
            raise ComparisonRenderError(
                "rendered PNG contains an unsupported critical chunk",
            )
        offset = end
    if not seen_iend:
        raise ComparisonRenderError("rendered PNG is incomplete")
    with path.open("wb") as stream:
        stream.write(output)
        stream.flush()
        os.fsync(stream.fileno())


def _read_manifest(root, expected_schema):
    manifest_path = root / "manifest.json"
    snapshot = _snapshot_regular_file(
        manifest_path,
        MAX_MANIFEST_BYTES,
        "bundle manifest",
    )
    raw = manifest_path.read_bytes()
    try:
        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except ComparisonRenderError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonRenderError("bundle manifest JSON is invalid") from exc
    if raw != _canonical_bytes(manifest):
        raise ComparisonRenderError("bundle manifest is not canonical JSON")
    if not isinstance(manifest, dict):
        raise ComparisonRenderError("bundle manifest must be an object")
    if manifest.get("schema_version") != expected_schema:
        raise ComparisonRenderError("bundle manifest schema is not accepted")
    bundle_id = manifest.get("bundle_id")
    if not isinstance(bundle_id, str) or not SHA256_PATTERN.fullmatch(bundle_id):
        raise ComparisonRenderError("bundle id is invalid")
    if root.name != bundle_id:
        raise ComparisonRenderError("bundle directory does not match bundle id")
    if (
        manifest.get("coordinate_encoding") != COORDINATE_ENCODING
        or manifest.get("synthetic") is not True
        or manifest.get("real_photo_textures") is not False
        or manifest.get("verification_level") != "L0"
    ):
        raise ComparisonRenderError("bundle trust disclosure is not accepted")
    return manifest, snapshot


def _require_lod2_record(root, record):
    if not isinstance(record, dict):
        raise ComparisonRenderError("bundle record must be an object")
    asset_id = record.get("asset_id")
    lod = record.get("lod")
    if (
        not isinstance(asset_id, str)
        or not isinstance(lod, dict)
        or record.get("synthetic") is not True
        or record.get("geometry_usability") != "preview-only"
    ):
        raise ComparisonRenderError("bundle record identity or LOD is invalid")
    near = lod.get("2")
    if not isinstance(near, dict):
        raise ComparisonRenderError(f"{asset_id} has no LOD2 record")
    object_path = near.get("glb_object_path")
    glb_sha256 = near.get("glb_sha256")
    triangle_count = near.get("triangle_count")
    match = (
        GLB_PATH_PATTERN.fullmatch(object_path)
        if isinstance(object_path, str)
        else None
    )
    if (
        match is None
        or match.group(1) != glb_sha256
        or not isinstance(triangle_count, int)
        or isinstance(triangle_count, bool)
        or triangle_count <= 0
    ):
        raise ComparisonRenderError(f"{asset_id} LOD2 identity is invalid")
    path = root / object_path
    return asset_id, near, _snapshot_regular_file(
        path,
        MAX_GLB_BYTES,
        f"{asset_id} LOD2 GLB",
        glb_sha256,
    )


def _load_bundle(path, expected_schema):
    root = _real_directory(path, "comparison bundle")
    manifest, manifest_snapshot = _read_manifest(root, expected_schema)
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ComparisonRenderError("bundle records must be an array")
    lod2_by_asset = {}
    snapshots = [manifest_snapshot]
    for record in records:
        asset_id, near, snapshot = _require_lod2_record(root, record)
        if asset_id in lod2_by_asset:
            raise ComparisonRenderError(f"duplicate asset record: {asset_id}")
        lod2_by_asset[asset_id] = near
        snapshots.append(snapshot)
    if tuple(lod2_by_asset) != EXPECTED_ASSET_IDS:
        raise ComparisonRenderError("bundle asset closure is not exact")

    texture_objects = manifest.get("texture_objects", [])
    if expected_schema == V1_SCHEMA and texture_objects != []:
        raise ComparisonRenderError("v1 bundle unexpectedly declares shared textures")
    if expected_schema == V2_SCHEMA:
        if not isinstance(texture_objects, list) or not texture_objects:
            raise ComparisonRenderError("v2 shared texture closure is absent")
        seen_textures = set()
        for descriptor in texture_objects:
            if not isinstance(descriptor, dict):
                raise ComparisonRenderError("texture object must be an object")
            object_path = descriptor.get("object_path")
            sha256 = descriptor.get("sha256")
            byte_count = descriptor.get("bytes")
            match = (
                TEXTURE_PATH_PATTERN.fullmatch(object_path)
                if isinstance(object_path, str)
                else None
            )
            if (
                match is None
                or match.group(1) != sha256
                or sha256 in seen_textures
                or not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count <= 0
            ):
                raise ComparisonRenderError("texture object identity is invalid")
            snapshot = _snapshot_regular_file(
                root / object_path,
                MAX_TEXTURE_BYTES,
                "shared texture object",
                sha256,
            )
            if snapshot.byte_count != byte_count:
                raise ComparisonRenderError(
                    "shared texture byte count does not match manifest",
                )
            seen_textures.add(sha256)
            snapshots.append(snapshot)
        bound_textures = {
            binding.get("sha256")
            for near in lod2_by_asset.values()
            for binding in near.get("texture_bindings", [])
            if isinstance(binding, dict)
        }
        if bound_textures != seen_textures:
            raise ComparisonRenderError(
                "LOD2 texture bindings do not close over shared objects",
            )

    return BundleInput(
        root=root,
        manifest=manifest,
        snapshots=tuple(snapshots),
        lod2_by_asset=lod2_by_asset,
    )


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.images,
    ):
        for item in tuple(collection):
            if item.users == 0:
                collection.remove(item)


def _mesh_bounds(objects):
    points = []
    for obj in objects:
        if obj.type != "MESH" or not obj.data.polygons:
            continue
        if not obj.material_slots or any(
            slot.material is None for slot in obj.material_slots
        ):
            raise ComparisonRenderError(
                f"imported mesh has a missing material: {obj.name}",
            )
        points.extend(
            obj.matrix_world @ Vector(corner)
            for corner in obj.bound_box
        )
    if not points:
        raise ComparisonRenderError("GLB import produced no renderable mesh")
    minimum = Vector(
        tuple(min(point[axis] for point in points) for axis in range(3)),
    )
    maximum = Vector(
        tuple(max(point[axis] for point in points) for axis in range(3)),
    )
    if not all(math.isfinite(value) for value in (*minimum, *maximum)):
        raise ComparisonRenderError("imported mesh bounds are not finite")
    if any(maximum[axis] <= minimum[axis] for axis in range(3)):
        raise ComparisonRenderError("imported mesh bounds are degenerate")
    return minimum, maximum


def _import_asset(path, name):
    existing = set(bpy.data.objects)
    result = bpy.ops.import_scene.gltf(filepath=str(path))
    if "FINISHED" not in result:
        raise ComparisonRenderError(f"Blender rejected GLB import: {name}")
    imported = sorted(
        (obj for obj in bpy.data.objects if obj not in existing),
        key=lambda obj: obj.name,
    )
    if not imported:
        raise ComparisonRenderError(f"GLB import created no objects: {name}")
    bounds = _mesh_bounds(imported)
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    imported_set = set(imported)
    for obj in imported:
        if obj.parent not in imported_set:
            matrix_world = obj.matrix_world.copy()
            obj.parent = root
            obj.matrix_world = matrix_world
    return root, bounds


def _pair_transform(bounds, target_x, target_y, common_scale):
    minimum, maximum = bounds
    center = Vector(
        (
            (minimum.x + maximum.x) * 0.5,
            (minimum.y + maximum.y) * 0.5,
            minimum.z,
        ),
    )
    return (
        Matrix.Translation(Vector((target_x, target_y, 0.08)))
        @ Matrix.Rotation(math.radians(24.0), 4, "Z")
        @ Matrix.Scale(common_scale, 4)
        @ Matrix.Translation(-center)
    )


def _add_label(body, location, size, material, name):
    curve = bpy.data.curves.new(name, type="FONT")
    curve.body = body
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    curve.size = size
    curve.extrude = 0.006
    curve.materials.append(material)
    label = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(label)
    label.location = location


def _add_panel(location, scale, material, name):
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=location)
    panel = bpy.context.object
    panel.name = name
    panel.scale = scale
    panel.data.materials.append(material)


def _layout_pairs(v1, v2):
    v1_panel = _new_principled_material(
        "comparison-v1-panel",
        (0.12, 0.16, 0.22),
        0.94,
    )
    v2_panel = _new_principled_material(
        "comparison-v2-panel",
        (0.10, 0.22, 0.20),
        0.94,
    )
    text_material = _new_principled_material(
        "comparison-label",
        (0.72, 0.78, 0.82),
        0.72,
    )
    v1_text = _new_principled_material(
        "comparison-v1-label",
        (0.38, 0.64, 1.0),
        0.62,
    )
    v2_text = _new_principled_material(
        "comparison-v2-label",
        (0.18, 0.92, 0.65),
        0.62,
    )
    pair_rows = []
    for index, asset_id in enumerate(EXPECTED_ASSET_IDS):
        v1_near = v1.lod2_by_asset[asset_id]
        v2_near = v2.lod2_by_asset[asset_id]
        v1_path = v1.root / v1_near["glb_object_path"]
        v2_path = v2.root / v2_near["glb_object_path"]
        v1_root, v1_bounds = _import_asset(v1_path, f"{asset_id}:v1")
        v2_root, v2_bounds = _import_asset(v2_path, f"{asset_id}:v2")
        extents = [
            v1_bounds[1] - v1_bounds[0],
            v2_bounds[1] - v2_bounds[0],
        ]
        horizontal = max(
            max(extent.x, extent.y)
            for extent in extents
        )
        vertical = max(extent.z for extent in extents)
        common_scale = min(2.7 / horizontal, 3.8 / vertical)
        column = index % 4
        row = index // 4
        cell_x = -12.75 + column * 8.5
        cell_y = row * 7.0
        _add_panel(
            (cell_x + 1.75, cell_y, 0.018),
            (1.72, 2.85, 1.0),
            v1_panel,
            f"{asset_id}:v1-panel",
        )
        _add_panel(
            (cell_x - 1.75, cell_y, 0.018),
            (1.72, 2.85, 1.0),
            v2_panel,
            f"{asset_id}:v2-panel",
        )
        _add_label(
            asset_id,
            (cell_x, cell_y + 2.45, 0.035),
            0.27,
            text_material,
            f"{asset_id}:asset-label",
        )
        _add_label(
            "V1",
            (cell_x + 1.75, cell_y - 2.45, 0.035),
            0.38,
            v1_text,
            f"{asset_id}:v1-label",
        )
        _add_label(
            "V2",
            (cell_x - 1.75, cell_y - 2.45, 0.035),
            0.38,
            v2_text,
            f"{asset_id}:v2-label",
        )
        v1_root.matrix_world = _pair_transform(
            v1_bounds,
            cell_x + 1.75,
            cell_y,
            common_scale,
        )
        v2_root.matrix_world = _pair_transform(
            v2_bounds,
            cell_x - 1.75,
            cell_y,
            common_scale,
        )
        pair_rows.append(
            {
                "asset_id": asset_id,
                "v1_glb_sha256": v1_near["glb_sha256"],
                "v1_triangle_count": v1_near["triangle_count"],
                "v2_glb_sha256": v2_near["glb_sha256"],
                "v2_triangle_count": v2_near["triangle_count"],
            },
        )
    return pair_rows


def _new_principled_material(name, colour, roughness):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = (*colour, 1.0)
    principled.inputs["Roughness"].default_value = roughness
    return material


def _add_ground():
    material = _new_principled_material(
        "comparison-neutral-ground",
        (0.18, 0.20, 0.22),
        0.92,
    )
    bpy.ops.mesh.primitive_plane_add(
        size=2.0,
        location=(0.0, 7.0, -0.02),
    )
    ground = bpy.context.object
    ground.name = "comparison-neutral-ground"
    ground.scale = (21.0, 13.0, 1.0)
    ground.data.materials.append(material)


def _add_area_light(name, location, energy, size, colour):
    data = bpy.data.lights.new(name, type="AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    data.color = colour
    light = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(light)
    light.location = location
    direction = Vector((0.0, 7.0, 1.5)) - light.location
    light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _configure_scene(output_path):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = IMAGE_WIDTH
    scene.render.resolution_y = IMAGE_HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15
    scene.render.film_transparent = False
    scene.render.use_file_extension = True
    scene.render.use_motion_blur = False
    scene.render.dither_intensity = 0.0
    scene.render.filepath = str(output_path)
    scene.eevee.taa_render_samples = 64
    scene.eevee.use_taa_reprojection = False
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.65
    scene.view_settings.gamma = 1.0

    world = bpy.data.worlds.new("comparison-neutral-world")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.07, 0.085, 0.11, 1.0)
    background.inputs["Strength"].default_value = 0.75
    scene.world = world

    camera_data = bpy.data.cameras.new("comparison-fixed-camera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 38.5
    camera = bpy.data.objects.new("comparison-fixed-camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (0.0, -30.0, 30.0)
    target = Vector((0.0, 7.0, 1.6))
    camera.rotation_euler = (
        target - camera.location
    ).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera

    _add_area_light(
        "comparison-key",
        Vector((-16.0, -10.0, 30.0)),
        6000.0,
        13.0,
        (1.0, 0.91, 0.80),
    )
    _add_area_light(
        "comparison-fill",
        Vector((19.0, 14.0, 22.0)),
        3600.0,
        11.0,
        (0.70, 0.84, 1.0),
    )
    return camera


def _prepare_output(path, label):
    path = Path(path).absolute()
    parent = _real_directory(path.parent, f"{label} parent")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ComparisonRenderError(f"{label} is redirected or not a file")
    if path.parent != parent:
        raise ComparisonRenderError(f"{label} parent is redirected")
    return path


def _parse_arguments():
    argv = sys.argv
    arguments = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-bundle", required=True)
    parser.add_argument("--v2-bundle", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(arguments)


def _execute():
    arguments = _parse_arguments()
    v1 = _load_bundle(arguments.v1_bundle, V1_SCHEMA)
    v2 = _load_bundle(arguments.v2_bundle, V2_SCHEMA)
    if (
        v1.manifest["material_bundle_id"]
        != v2.manifest["material_bundle_id"]
        or v2.manifest.get("source_v1_bundle_id")
        != v1.manifest["bundle_id"]
    ):
        raise ComparisonRenderError("v1/v2 bundle lineage is inconsistent")
    output_path = _prepare_output(arguments.output, "comparison PNG")
    report_path = _prepare_output(arguments.report, "comparison report")
    if output_path == report_path:
        raise ComparisonRenderError("comparison output paths must be distinct")
    nonce = uuid.uuid4().hex
    temporary_output = output_path.with_name(
        f".{output_path.name}.tmp-{nonce}.png",
    )
    temporary_report = report_path.with_name(
        f".{report_path.name}.tmp-{nonce}.json",
    )
    all_snapshots = (*v1.snapshots, *v2.snapshots)
    try:
        _clear_scene()
        pair_rows = _layout_pairs(v1, v2)
        _add_ground()
        camera = _configure_scene(temporary_output)
        _verify_snapshots_unchanged(all_snapshots)
        bpy.ops.render.render(write_still=True)
        _verify_snapshots_unchanged(all_snapshots)
        _canonicalize_png_chunks(temporary_output)
        image_snapshot = _snapshot_regular_file(
            temporary_output,
            256 * 1024 * 1024,
            "rendered comparison PNG",
        )
        camera_matrix = [
            float(camera.matrix_world[row][column])
            for row in range(4)
            for column in range(4)
        ]
        if not all(math.isfinite(value) for value in camera_matrix):
            raise ComparisonRenderError("camera matrix is not finite")
        report = {
            "asset_ids": list(EXPECTED_ASSET_IDS),
            "camera_matrix": camera_matrix,
            "image_bytes": image_snapshot.byte_count,
            "image_sha256": image_snapshot.sha256,
            "pairs": pair_rows,
            "schema_version": REPORT_SCHEMA,
            "synthetic": True,
            "trust_effect": "none-visual-review-only",
            "v1_bundle_id": v1.manifest["bundle_id"],
            "v2_bundle_id": v2.manifest["bundle_id"],
        }
        report_bytes = _canonical_bytes(report)
        with temporary_report.open("xb") as stream:
            stream.write(report_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        _verify_snapshots_unchanged(all_snapshots)
        os.replace(temporary_output, output_path)
        os.replace(temporary_report, report_path)
    except Exception:
        for path in (temporary_output, temporary_report):
            try:
                if path.is_file() and not path.is_symlink():
                    path.unlink()
            except OSError:
                pass
        raise
    print(
        "NANTAI_MESH_COMPARISON_OK "
        f"v1={v1.manifest['bundle_id']} "
        f"v2={v2.manifest['bundle_id']} "
        f"assets={len(EXPECTED_ASSET_IDS)}",
        flush=True,
    )


def main():
    try:
        _execute()
    except Exception as exc:
        print(
            "NANTAI_MESH_COMPARISON_ERROR "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        raise


if __name__ == "__main__":
    main()
