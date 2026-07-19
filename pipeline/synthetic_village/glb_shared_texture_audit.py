"""Fail-closed audit for GLBs with verified shared content-addressed PNGs."""

from __future__ import annotations

import copy
import hashlib
import io
import math
import re
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from pipeline.synthetic_village.foliage_atlas import (
    FOLIAGE_ATLAS_ALGORITHM_ID,
    FOLIAGE_SHAPES,
)
from pipeline.synthetic_village.glb_material_audit import (
    MAX_GLB_BYTES,
    ExpectedGlbMaterial,
    GlbMaterialAuditError,
    _AccessorEvidence,
    _load_glb_bytes,
    _read_stable_file,
    _required_list,
    _validate_accessors,
    _validate_buffer_views,
    audit_textured_glb_bytes,
)
from pipeline.synthetic_village.mesh_asset_bundle import Bounds3
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    MAX_MESH_TEXTURE_BYTES,
    TextureBindingV2,
    TextureObjectV2,
)

_SHARED_TEXTURE_URI = re.compile(r"^\.\./textures/([0-9a-f]{64})\.png$")
_INDEX_FORMATS = {5121: "B", 5123: "H", 5125: "I"}
_FOOTPRINT_TOLERANCE_M = 1e-5


class SharedTextureGlbAuditError(ValueError):
    """A shared-texture GLB or dependency closure cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SharedTextureDependencyEvidence(FrozenModel):
    uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=1)


class MeshTopologyEvidence(FrozenModel):
    vertex_count: int = Field(ge=3)
    triangle_count: int = Field(ge=1)
    used_mesh_count: int = Field(ge=1)
    aabb: Bounds3


class FoliageAlphaRecord(FrozenModel):
    material_slot_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alpha_coverage: float = Field(gt=0, lt=1, allow_inf_nan=False)


class FoliageAlphaEvidence(FrozenModel):
    records: tuple[FoliageAlphaRecord, ...] = Field(min_length=1)


class SharedTextureGlbAudit(FrozenModel):
    glb_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=1)
    mesh_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    triangle_count: int = Field(ge=1)
    material_count: int = Field(ge=1)
    texture_count: int = Field(ge=3)
    slot_ids: tuple[str, ...] = Field(min_length=1)
    dependencies: tuple[SharedTextureDependencyEvidence, ...] = Field(
        min_length=3,
    )
    topology: MeshTopologyEvidence
    foliage_alpha: FoliageAlphaEvidence | None = None


def _pack_glb(document: dict[str, object], binary: bytes) -> bytes:
    import json

    document_bytes = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    document_bytes += b" " * (-len(document_bytes) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(document_bytes) + 8 + len(binary)
    return b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, total),
            struct.pack("<I4s", len(document_bytes), b"JSON"),
            document_bytes,
            struct.pack("<I4s", len(binary), b"BIN\0"),
            binary,
        ),
    )


def hydrate_shared_texture_glb(
    original: bytes,
    dependencies: Mapping[str, bytes],
) -> bytes:
    """Replace the exact relative image closure with verified in-memory bytes."""

    try:
        if type(original) is not bytes or not isinstance(dependencies, Mapping):
            raise SharedTextureGlbAuditError(
                "shared GLB hydration requires exact bytes and a dependency mapping",
            )
        _raw, document, binary = _load_glb_bytes(original)
        document = copy.deepcopy(document)
        buffers = _required_list(document, "buffers")
        if (
            len(buffers) != 1
            or not isinstance(buffers[0], dict)
            or "uri" in buffers[0]
        ):
            raise SharedTextureGlbAuditError(
                "shared GLB geometry buffer must remain embedded",
            )
        declared_length = buffers[0].get("byteLength")
        if (
            isinstance(declared_length, bool)
            or not isinstance(declared_length, int)
            or declared_length <= 0
            or declared_length > len(binary)
        ):
            raise SharedTextureGlbAuditError(
                "shared GLB embedded buffer length is invalid",
            )
        images = _required_list(document, "images")
        image_uris = []
        for image in images:
            if (
                not isinstance(image, dict)
                or set(image) - {"name", "uri", "mimeType", "extras"}
                or image.get("mimeType") != "image/png"
                or type(image.get("uri")) is not str
                or "bufferView" in image
            ):
                raise SharedTextureGlbAuditError(
                    "shared GLB image URI declaration is invalid",
                )
            uri = image["uri"]
            match = _SHARED_TEXTURE_URI.fullmatch(uri)
            if match is None:
                raise SharedTextureGlbAuditError(
                    "shared GLB image URI is not the exact allowed shape",
                )
            image_uris.append(uri)
        if len(image_uris) != len(set(image_uris)):
            raise SharedTextureGlbAuditError(
                "shared GLB image URI closure contains duplicates",
            )
        if set(image_uris) != set(dependencies):
            raise SharedTextureGlbAuditError(
                "shared GLB image URI dependency closure is incomplete or extra",
            )
        verified: dict[str, bytes] = {}
        for uri, payload in dependencies.items():
            match = _SHARED_TEXTURE_URI.fullmatch(uri)
            if match is None or type(payload) is not bytes:
                raise SharedTextureGlbAuditError(
                    "shared GLB hydration dependency is invalid",
                )
            if hashlib.sha256(payload).hexdigest() != match.group(1):
                raise SharedTextureGlbAuditError(
                    "shared GLB hydration dependency hash is invalid",
                )
            verified[uri] = payload

        views = _required_list(document, "bufferViews")
        hydrated_binary = bytearray(binary[:declared_length])
        for image, uri in zip(images, image_uris, strict=True):
            hydrated_binary.extend(b"\0" * (-len(hydrated_binary) % 4))
            offset = len(hydrated_binary)
            payload = verified[uri]
            hydrated_binary.extend(payload)
            views.append(
                {
                    "buffer": 0,
                    "byteOffset": offset,
                    "byteLength": len(payload),
                },
            )
            image.pop("uri")
            image["bufferView"] = len(views) - 1
        buffers[0]["byteLength"] = len(hydrated_binary)
        return _pack_glb(document, bytes(hydrated_binary))
    except SharedTextureGlbAuditError:
        raise
    except GlbMaterialAuditError as exc:
        raise SharedTextureGlbAuditError(
            f"shared GLB hydration failed: {exc}",
        ) from exc
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise SharedTextureGlbAuditError(
            f"shared GLB hydration failed: {exc}",
        ) from exc


def _binding_sort_key(
    row: TextureBindingV2,
) -> tuple[str, str, str, str]:
    return (
        row.material_slot_id,
        row.role,
        row.sha256,
        row.derivation_algorithm_id,
    )


def _real_texture_path(root: Path, descriptor: TextureObjectV2) -> Path:
    root = Path(root).expanduser().absolute()
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise SharedTextureGlbAuditError(
            "shared texture root is unavailable",
        ) from exc
    if root.is_symlink() or not root.is_dir() or resolved_root != root:
        raise SharedTextureGlbAuditError("shared texture root is redirected")
    path = root / descriptor.object_path
    if path.is_symlink() or not path.is_file():
        raise SharedTextureGlbAuditError("shared texture is redirected")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SharedTextureGlbAuditError("shared texture is unavailable") from exc
    try:
        resolved.relative_to(root / "textures")
    except ValueError as exc:
        raise SharedTextureGlbAuditError(
            "shared texture escapes its exact root",
        ) from exc
    if resolved != path:
        raise SharedTextureGlbAuditError("shared texture is redirected")
    return path


def _read_exact_texture_closure(
    texture_root: Path,
    bindings: tuple[TextureBindingV2, ...],
    objects: tuple[TextureObjectV2, ...],
) -> tuple[
    dict[str, bytes],
    tuple[SharedTextureDependencyEvidence, ...],
]:
    if (
        not bindings
        or tuple(sorted(bindings, key=_binding_sort_key)) != bindings
        or len({_binding_sort_key(row) for row in bindings}) != len(bindings)
    ):
        raise SharedTextureGlbAuditError(
            "shared texture binding closure is unsorted or duplicate",
        )
    if (
        not objects
        or tuple(sorted(objects, key=lambda row: row.object_path)) != objects
        or len({row.sha256 for row in objects}) != len(objects)
    ):
        raise SharedTextureGlbAuditError(
            "shared texture object closure is unsorted or duplicate",
        )
    object_by_hash = {row.sha256: row for row in objects}
    if set(object_by_hash) != {row.sha256 for row in bindings}:
        raise SharedTextureGlbAuditError(
            "shared texture object and binding closure disagree",
        )
    payload_by_hash = {}
    evidence = []
    for descriptor in objects:
        try:
            payload = _read_stable_file(
                _real_texture_path(texture_root, descriptor),
                maximum_bytes=MAX_MESH_TEXTURE_BYTES,
            )
        except GlbMaterialAuditError as exc:
            raise SharedTextureGlbAuditError(
                f"shared texture cannot be read stably: {exc}",
            ) from exc
        if (
            len(payload) != descriptor.bytes
            or hashlib.sha256(payload).hexdigest() != descriptor.sha256
        ):
            raise SharedTextureGlbAuditError(
                "shared texture bytes disagree with their descriptor",
            )
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if (
                    image.format != "PNG"
                    or image.size != (descriptor.width, descriptor.height)
                ):
                    raise SharedTextureGlbAuditError(
                        "shared texture PNG dimensions or MIME are invalid",
                    )
        except SharedTextureGlbAuditError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise SharedTextureGlbAuditError(
                "shared texture is not a valid PNG",
            ) from exc
        payload_by_hash[descriptor.sha256] = payload
        evidence.append(
            SharedTextureDependencyEvidence(
                uri=f"../textures/{descriptor.sha256}.png",
                sha256=descriptor.sha256,
                bytes=descriptor.bytes,
            ),
        )
    dependencies = {
        binding.uri: payload_by_hash[binding.sha256]
        for binding in bindings
    }
    return dependencies, tuple(sorted(evidence, key=lambda row: row.uri))


def _texture_index(
    container: dict[str, object],
    key: str,
    *,
    texture_count: int,
) -> int:
    value = container.get(key)
    if not isinstance(value, dict):
        raise SharedTextureGlbAuditError(
            f"shared GLB material is missing {key}",
        )
    index = value.get("index")
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= texture_count
    ):
        raise SharedTextureGlbAuditError(
            f"shared GLB material {key} is invalid",
        )
    return index


def _verify_binding_semantics(
    document: dict[str, object],
    bindings: tuple[TextureBindingV2, ...],
) -> None:
    images = _required_list(document, "images")
    textures = _required_list(document, "textures")
    materials = _required_list(document, "materials")
    image_uris = []
    for image in images:
        if not isinstance(image, dict) or type(image.get("uri")) is not str:
            raise SharedTextureGlbAuditError(
                "shared GLB material image URI is invalid",
            )
        image_uris.append(image["uri"])
    texture_sources = []
    for texture in textures:
        if not isinstance(texture, dict):
            raise SharedTextureGlbAuditError("shared GLB texture is invalid")
        source = texture.get("source")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source < 0
            or source >= len(image_uris)
        ):
            raise SharedTextureGlbAuditError(
                "shared GLB texture image source is invalid",
            )
        texture_sources.append(source)
    binding_by_role = {
        (row.material_slot_id, row.role): row
        for row in bindings
    }
    if len(binding_by_role) != len(bindings):
        raise SharedTextureGlbAuditError(
            "shared texture binding closure has duplicate material roles",
        )
    used = set()
    for material in materials:
        if not isinstance(material, dict) or not isinstance(
            material.get("extras"),
            dict,
        ):
            raise SharedTextureGlbAuditError(
                "shared GLB material identity is missing",
            )
        slot_id = material["extras"].get("slot_id")
        if type(slot_id) is not str:
            raise SharedTextureGlbAuditError(
                "shared GLB material slot is invalid",
            )
        pbr = material.get("pbrMetallicRoughness")
        if not isinstance(pbr, dict):
            raise SharedTextureGlbAuditError(
                "shared GLB material PBR block is missing",
            )
        indices = {
            "base_color": _texture_index(
                pbr,
                "baseColorTexture",
                texture_count=len(textures),
            ),
            "normal": _texture_index(
                material,
                "normalTexture",
                texture_count=len(textures),
            ),
            "orm": _texture_index(
                pbr,
                "metallicRoughnessTexture",
                texture_count=len(textures),
            ),
        }
        for role, texture_index in indices.items():
            key = (slot_id, role)
            binding = binding_by_role.get(key)
            actual_uri = image_uris[texture_sources[texture_index]]
            if binding is None or binding.uri != actual_uri:
                raise SharedTextureGlbAuditError(
                    "shared GLB material texture binding closure disagrees",
                )
            used.add(key)
    if used != set(binding_by_role):
        raise SharedTextureGlbAuditError(
            "shared GLB contains an extra texture binding",
        )


def _decode_positions(
    accessor: _AccessorEvidence,
    *,
    binary: bytes,
    view_ranges: list[tuple[int, int]],
) -> tuple[tuple[float, float, float], ...]:
    if (
        accessor.component_type != 5126
        or accessor.value_type != "VEC3"
        or accessor.normalized
    ):
        raise SharedTextureGlbAuditError(
            "shared GLB POSITION accessor is invalid",
        )
    stride = accessor.byte_stride or 12
    view_offset, view_length = view_ranges[accessor.view_index]
    start = view_offset + accessor.byte_offset
    if accessor.byte_offset + stride * (accessor.count - 1) + 12 > view_length:
        raise SharedTextureGlbAuditError(
            "shared GLB POSITION accessor exceeds its view",
        )
    rows = tuple(
        struct.unpack_from("<3f", binary, start + index * stride)
        for index in range(accessor.count)
    )
    if any(not math.isfinite(value) for row in rows for value in row):
        raise SharedTextureGlbAuditError(
            "shared GLB contains a non-finite vertex",
        )
    return rows


def _decode_indices(
    accessor: _AccessorEvidence,
    *,
    binary: bytes,
    view_ranges: list[tuple[int, int]],
) -> tuple[int, ...]:
    if (
        accessor.component_type not in _INDEX_FORMATS
        or accessor.value_type != "SCALAR"
        or accessor.normalized
        or accessor.count % 3
    ):
        raise SharedTextureGlbAuditError(
            "shared GLB requires an indexed triangle accessor",
        )
    width = {5121: 1, 5123: 2, 5125: 4}[accessor.component_type]
    stride = accessor.byte_stride or width
    view_offset, view_length = view_ranges[accessor.view_index]
    start = view_offset + accessor.byte_offset
    if accessor.byte_offset + stride * (accessor.count - 1) + width > view_length:
        raise SharedTextureGlbAuditError(
            "shared GLB index accessor exceeds its view",
        )
    pattern = f"<{_INDEX_FORMATS[accessor.component_type]}"
    return tuple(
        struct.unpack_from(pattern, binary, start + index * stride)[0]
        for index in range(accessor.count)
    )


def _node_matrix(node: dict[str, object]) -> np.ndarray:
    if "matrix" in node:
        if any(key in node for key in ("translation", "rotation", "scale")):
            raise SharedTextureGlbAuditError(
                "shared GLB node mixes matrix and TRS transforms",
            )
        raw = node["matrix"]
        if (
            not isinstance(raw, list)
            or len(raw) != 16
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw)
        ):
            raise SharedTextureGlbAuditError("shared GLB node matrix is invalid")
        matrix = np.asarray(raw, dtype=np.float64).reshape((4, 4), order="F")
    else:
        translation = node.get("translation", [0.0, 0.0, 0.0])
        rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
        scale = node.get("scale", [1.0, 1.0, 1.0])
        if (
            not isinstance(translation, list)
            or len(translation) != 3
            or not isinstance(rotation, list)
            or len(rotation) != 4
            or not isinstance(scale, list)
            or len(scale) != 3
        ):
            raise SharedTextureGlbAuditError("shared GLB node TRS is invalid")
        values = (*translation, *rotation, *scale)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise SharedTextureGlbAuditError("shared GLB node TRS is non-finite")
        x, y, z, w = (float(value) for value in rotation)
        norm = math.sqrt(x * x + y * y + z * z + w * w)
        if norm <= 1e-12:
            raise SharedTextureGlbAuditError(
                "shared GLB node quaternion is degenerate",
            )
        x, y, z, w = (value / norm for value in (x, y, z, w))
        rotation_matrix = np.array(
            (
                (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
                (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
                (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
            ),
            dtype=np.float64,
        )
        matrix = np.eye(4, dtype=np.float64)
        matrix[:3, :3] = rotation_matrix @ np.diag(np.asarray(scale, dtype=np.float64))
        matrix[:3, 3] = np.asarray(translation, dtype=np.float64)
    if (
        not np.isfinite(matrix).all()
        or abs(float(np.linalg.det(matrix[:3, :3]))) <= 1e-12
    ):
        raise SharedTextureGlbAuditError(
            "shared GLB node transform is non-finite or degenerate",
        )
    return matrix


def _reachable_node_matrices(
    document: dict[str, object],
) -> dict[int, np.ndarray]:
    nodes = _required_list(document, "nodes")
    scenes = _required_list(document, "scenes")
    scene_index = document.get("scene", 0)
    if (
        isinstance(scene_index, bool)
        or not isinstance(scene_index, int)
        or scene_index < 0
        or scene_index >= len(scenes)
        or not isinstance(scenes[scene_index], dict)
    ):
        raise SharedTextureGlbAuditError("shared GLB active scene is invalid")
    roots = scenes[scene_index].get("nodes")
    if not isinstance(roots, list) or not roots:
        raise SharedTextureGlbAuditError("shared GLB active scene has no roots")
    world_by_node: dict[int, np.ndarray] = {}
    active: set[int] = set()

    def visit(index: int, parent: np.ndarray) -> None:
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= len(nodes)
            or not isinstance(nodes[index], dict)
        ):
            raise SharedTextureGlbAuditError("shared GLB scene node is invalid")
        if index in active:
            raise SharedTextureGlbAuditError("shared GLB node graph has a cycle")
        if index in world_by_node:
            raise SharedTextureGlbAuditError(
                "shared GLB node graph has repeated ownership",
            )
        active.add(index)
        node = nodes[index]
        world = parent @ _node_matrix(node)
        world_by_node[index] = world
        children = node.get("children", [])
        if not isinstance(children, list):
            raise SharedTextureGlbAuditError(
                "shared GLB node children are invalid",
            )
        for child in children:
            visit(child, world)
        active.remove(index)

    identity = np.eye(4, dtype=np.float64)
    for root in roots:
        visit(root, identity)
    return world_by_node


def _audit_mesh_topology(
    original: bytes,
    *,
    footprint_m: tuple[float, float, float],
) -> MeshTopologyEvidence:
    _raw, document, binary = _load_glb_bytes(original)
    view_ranges = _validate_buffer_views(document, binary)
    accessors = _validate_accessors(document, view_ranges)
    meshes = _required_list(document, "meshes")
    position_rows: dict[int, tuple[tuple[float, float, float], ...]] = {}
    mesh_position_accessors: dict[int, set[int]] = {}
    triangle_count = 0
    vertex_count = 0
    for mesh_index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict) or not isinstance(mesh.get("primitives"), list):
            raise SharedTextureGlbAuditError("shared GLB mesh is invalid")
        face_keys = set()
        position_indices = set()
        for primitive in mesh["primitives"]:
            if not isinstance(primitive, dict) or primitive.get("mode", 4) != 4:
                raise SharedTextureGlbAuditError(
                    "shared GLB requires triangle primitives",
                )
            attributes = primitive.get("attributes")
            if not isinstance(attributes, dict):
                raise SharedTextureGlbAuditError(
                    "shared GLB primitive attributes are invalid",
                )
            position_index = attributes.get("POSITION")
            index_index = primitive.get("indices")
            if (
                isinstance(position_index, bool)
                or not isinstance(position_index, int)
                or position_index < 0
                or position_index >= len(accessors)
                or isinstance(index_index, bool)
                or not isinstance(index_index, int)
                or index_index < 0
                or index_index >= len(accessors)
            ):
                raise SharedTextureGlbAuditError(
                    "shared GLB requires indexed triangle primitives",
                )
            positions = position_rows.setdefault(
                position_index,
                _decode_positions(
                    accessors[position_index],
                    binary=binary,
                    view_ranges=view_ranges,
                ),
            )
            indices = _decode_indices(
                accessors[index_index],
                binary=binary,
                view_ranges=view_ranges,
            )
            if any(index >= len(positions) for index in indices):
                raise SharedTextureGlbAuditError(
                    "shared GLB triangle index exceeds its vertices",
                )
            for offset in range(0, len(indices), 3):
                face = indices[offset : offset + 3]
                coordinates = tuple(
                    tuple(float(value) for value in positions[index])
                    for index in face
                )
                first, second, third = (
                    np.asarray(row, dtype=np.float64)
                    for row in coordinates
                )
                if float(np.linalg.norm(np.cross(second - first, third - first))) <= 1e-12:
                    raise SharedTextureGlbAuditError(
                        "shared GLB contains a degenerate triangle",
                    )
                face_key = tuple(sorted(coordinates))
                if face_key in face_keys:
                    raise SharedTextureGlbAuditError(
                        "shared GLB contains a duplicate face",
                    )
                face_keys.add(face_key)
                triangle_count += 1
            position_indices.add(position_index)
        mesh_position_accessors[mesh_index] = position_indices
        vertex_count += sum(len(position_rows[index]) for index in position_indices)

    nodes = _required_list(document, "nodes")
    world_by_node = _reachable_node_matrices(document)
    used_meshes = set()
    transformed_points = []
    for node_index, world in world_by_node.items():
        node = nodes[node_index]
        mesh_index = node.get("mesh")
        if mesh_index is None:
            continue
        if (
            isinstance(mesh_index, bool)
            or not isinstance(mesh_index, int)
            or mesh_index < 0
            or mesh_index >= len(meshes)
        ):
            raise SharedTextureGlbAuditError("shared GLB node mesh is invalid")
        used_meshes.add(mesh_index)
        for position_index in mesh_position_accessors[mesh_index]:
            points = np.asarray(position_rows[position_index], dtype=np.float64)
            homogeneous = np.column_stack((points, np.ones(len(points))))
            transformed = (world @ homogeneous.T).T[:, :3]
            transformed_points.append(transformed)
    if used_meshes != set(range(len(meshes))):
        raise SharedTextureGlbAuditError("shared GLB contains an unused mesh")
    if not transformed_points:
        raise SharedTextureGlbAuditError("shared GLB scene has no geometry")
    points = np.concatenate(transformed_points, axis=0)
    if not np.isfinite(points).all():
        raise SharedTextureGlbAuditError(
            "shared GLB transformed vertices are non-finite",
        )
    gltf_min = points.min(axis=0)
    gltf_max = points.max(axis=0)
    aabb = Bounds3(
        min=(
            float(gltf_min[0]),
            float(-gltf_max[2]),
            float(gltf_min[1]),
        ),
        max=(
            float(gltf_max[0]),
            float(-gltf_min[2]),
            float(gltf_max[1]),
        ),
    )
    if (
        len(footprint_m) != 3
        or any(not math.isfinite(value) or value <= 0 for value in footprint_m)
    ):
        raise SharedTextureGlbAuditError("shared GLB footprint is invalid")
    half_east = footprint_m[0] / 2.0 + _FOOTPRINT_TOLERANCE_M
    half_north = footprint_m[1] / 2.0 + _FOOTPRINT_TOLERANCE_M
    if (
        abs(aabb.min[0]) > half_east
        or abs(aabb.max[0]) > half_east
        or abs(aabb.min[1]) > half_north
        or abs(aabb.max[1]) > half_north
        or abs(aabb.min[2]) > _FOOTPRINT_TOLERANCE_M
        or aabb.max[2] > footprint_m[2] + _FOOTPRINT_TOLERANCE_M
    ):
        raise SharedTextureGlbAuditError(
            "shared GLB geometry exceeds its footprint or ground plane",
        )
    return MeshTopologyEvidence(
        vertex_count=vertex_count,
        triangle_count=triangle_count,
        used_mesh_count=len(used_meshes),
        aabb=aabb,
    )


def _audit_foliage_alpha(
    document: dict[str, object],
    *,
    bindings: tuple[TextureBindingV2, ...],
    dependencies: Mapping[str, bytes],
) -> FoliageAlphaEvidence:
    materials = _required_list(document, "materials")
    material_by_slot = {}
    for material in materials:
        if not isinstance(material, dict) or not isinstance(
            material.get("extras"),
            dict,
        ):
            raise SharedTextureGlbAuditError(
                "foliage material identity is missing",
            )
        slot_id = material["extras"].get("slot_id")
        if type(slot_id) is not str:
            raise SharedTextureGlbAuditError("foliage material slot is invalid")
        material_by_slot[slot_id] = material
    binding_by_role = {
        (row.material_slot_id, row.role): row
        for row in bindings
    }
    foliage_slots = sorted({
        row.material_slot_id
        for row in bindings
        if row.derivation_algorithm_id == FOLIAGE_ATLAS_ALGORITHM_ID
    })
    if not foliage_slots:
        raise SharedTextureGlbAuditError(
            "vegetation GLB has no verified foliage alpha material",
        )
    records = []
    for slot_id in foliage_slots:
        material = material_by_slot.get(slot_id)
        if material is None:
            raise SharedTextureGlbAuditError(
                "foliage binding has no material",
            )
        if (
            material.get("alphaMode") != "MASK"
            or material.get("alphaCutoff") != 0.45
        ):
            raise SharedTextureGlbAuditError(
                "foliage material must use exact MASK alpha cutoff",
            )
        if material.get("doubleSided") is not True:
            raise SharedTextureGlbAuditError(
                "foliage material must be double-sided",
            )
        if material["extras"].get("uv_policy") != "leaf-card":
            raise SharedTextureGlbAuditError(
                "foliage material must use leaf-card UVs",
            )
        role_bindings = {
            role: binding_by_role.get((slot_id, role))
            for role in ("base_color", "normal", "orm")
        }
        if any(
            row is None
            or row.derivation_algorithm_id != FOLIAGE_ATLAS_ALGORITHM_ID
            for row in role_bindings.values()
        ):
            raise SharedTextureGlbAuditError(
                "foliage material derivation closure is incomplete",
            )
        base = role_bindings["base_color"]
        try:
            with Image.open(io.BytesIO(dependencies[base.uri])) as image:
                image.load()
                if image.mode != "RGBA" or image.size != (1024, 1024):
                    raise SharedTextureGlbAuditError(
                        "foliage base colour has no exact alpha channel",
                    )
                alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
        except SharedTextureGlbAuditError:
            raise
        except (KeyError, OSError, UnidentifiedImageError, ValueError) as exc:
            raise SharedTextureGlbAuditError(
                "foliage alpha texture is invalid",
            ) from exc
        if set(np.unique(alpha)) != {0, 255}:
            raise SharedTextureGlbAuditError(
                "foliage alpha must be non-uniform exact binary",
            )
        coverage = float(np.count_nonzero(alpha) / alpha.size)
        shape_contract = FOLIAGE_SHAPES.get(slot_id)
        lower, upper = (
            (shape_contract[1], shape_contract[2])
            if shape_contract is not None
            else (0.20, 0.55)
        )
        if not lower <= coverage <= upper:
            raise SharedTextureGlbAuditError(
                "foliage alpha coverage is outside its verified band",
            )
        records.append(
            FoliageAlphaRecord(
                material_slot_id=slot_id,
                sha256=base.sha256,
                alpha_coverage=coverage,
            ),
        )
    for slot_id, material in material_by_slot.items():
        if (
            slot_id not in foliage_slots
            and material.get("alphaMode", "OPAQUE") not in {"OPAQUE", None}
        ):
            raise SharedTextureGlbAuditError(
                "non-foliage material cannot enable alpha",
            )
    return FoliageAlphaEvidence(records=tuple(records))


def audit_shared_textured_glb(
    path: Path,
    *,
    expected_materials: tuple[ExpectedGlbMaterial, ...],
    texture_root: Path,
    bindings: tuple[TextureBindingV2, ...],
    objects: tuple[TextureObjectV2, ...],
    kind: Literal["building", "vegetation", "prop"],
    footprint_m: tuple[float, float, float],
) -> SharedTextureGlbAudit:
    """Audit original GLB identity against an exact verified in-memory closure."""

    try:
        if kind not in {"building", "vegetation", "prop"}:
            raise SharedTextureGlbAuditError(
                "shared GLB asset kind is unsupported",
            )
        try:
            original = _read_stable_file(
                Path(path),
                maximum_bytes=MAX_GLB_BYTES,
            )
        except GlbMaterialAuditError as exc:
            raise SharedTextureGlbAuditError(
                f"shared GLB cannot be read stably: {exc}",
            ) from exc
        dependencies, dependency_evidence = _read_exact_texture_closure(
            texture_root,
            bindings,
            objects,
        )
        _raw, document, _binary = _load_glb_bytes(original)
        hydrated = hydrate_shared_texture_glb(original, dependencies)
        _verify_binding_semantics(document, bindings)
        core = audit_textured_glb_bytes(
            hydrated,
            expected_materials=expected_materials,
        )
        topology = _audit_mesh_topology(
            original,
            footprint_m=footprint_m,
        )
        if topology.triangle_count != core.triangle_count:
            raise SharedTextureGlbAuditError(
                "shared GLB topology triangle evidence disagrees",
            )
        foliage_alpha = (
            _audit_foliage_alpha(
                document,
                bindings=bindings,
                dependencies=dependencies,
            )
            if kind == "vegetation"
            else None
        )
        if kind != "vegetation" and any(
            row.derivation_algorithm_id == FOLIAGE_ATLAS_ALGORITHM_ID
            for row in bindings
        ):
            raise SharedTextureGlbAuditError(
                "non-vegetation GLB cannot claim foliage atlas derivation",
            )
        return SharedTextureGlbAudit(
            glb_sha256=hashlib.sha256(original).hexdigest(),
            byte_count=len(original),
            mesh_count=core.mesh_count,
            primitive_count=core.primitive_count,
            triangle_count=core.triangle_count,
            material_count=core.material_count,
            texture_count=core.texture_count,
            slot_ids=core.slot_ids,
            dependencies=dependency_evidence,
            topology=topology,
            foliage_alpha=foliage_alpha,
        )
    except SharedTextureGlbAuditError:
        raise
    except GlbMaterialAuditError as exc:
        raise SharedTextureGlbAuditError(
            f"shared GLB core audit failed: {exc}",
        ) from exc
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise SharedTextureGlbAuditError(
            f"shared GLB audit failed: {exc}",
        ) from exc
