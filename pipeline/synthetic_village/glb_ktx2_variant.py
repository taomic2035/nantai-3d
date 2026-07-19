"""Fail-closed KTX2 rewrites for shared-texture GLB mesh variants."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping

import numpy as np

from .glb_material_audit import (
    COMPONENT_BYTES,
    TYPE_COMPONENTS,
    GlbMaterialAuditError,
    _AccessorEvidence,
    _load_glb_bytes,
    _required_list,
    _validate_accessors,
    _validate_buffer_views,
)
from .glb_shared_texture_audit import _reachable_node_matrices
from .ktx2_toolchain import KtxTextureDescriptor

_SHARED_PNG_URI = re.compile(r"^\.\./textures/([0-9a-f]{64})\.png$")
_GEOMETRY_ATTRIBUTES = ("POSITION", "NORMAL", "TANGENT", "TEXCOORD_0")
_KHR_TEXTURE_BASISU = "KHR_texture_basisu"


class GlbKtx2VariantError(ValueError):
    """A GLB KTX2 variant cannot be rewritten or verified safely."""


def canonical_glb_bytes(
    document: dict[str, object],
    binary_chunk: bytes,
) -> bytes:
    """Serialize a deterministic two-chunk GLB without changing binary bytes."""

    document_bytes = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    document_bytes += b" " * (-len(document_bytes) % 4)
    binary_chunk += b"\0" * (-len(binary_chunk) % 4)
    total = 12 + 8 + len(document_bytes) + 8 + len(binary_chunk)
    return b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, total),
            struct.pack("<I4s", len(document_bytes), b"JSON"),
            document_bytes,
            struct.pack("<I4s", len(binary_chunk), b"BIN\0"),
            binary_chunk,
        ),
    )


def _accessor_content_digest(
    evidence: _AccessorEvidence,
    *,
    binary: bytes,
    view_ranges: list[tuple[int, int]],
) -> str:
    component_bytes = COMPONENT_BYTES[evidence.component_type]
    element_bytes = component_bytes * TYPE_COMPONENTS[evidence.value_type]
    stride = evidence.byte_stride or element_bytes
    view_offset, _view_length = view_ranges[evidence.view_index]
    start = view_offset + evidence.byte_offset
    hasher = hashlib.sha256()
    hasher.update(
        json.dumps(
            {
                "component_type": evidence.component_type,
                "count": evidence.count,
                "normalized": evidence.normalized,
                "value_type": evidence.value_type,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    for index in range(evidence.count):
        offset = start + index * stride
        hasher.update(binary[offset : offset + element_bytes])
    return hasher.hexdigest()


def _index(value: object, *, count: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value >= count:
        raise GlbKtx2VariantError(f"{label} is invalid")
    return value


def _position_rows(
    evidence: _AccessorEvidence,
    *,
    binary: bytes,
    view_ranges: list[tuple[int, int]],
) -> np.ndarray:
    if evidence.component_type != 5126 or evidence.value_type != "VEC3" or evidence.normalized:
        raise GlbKtx2VariantError("GLB POSITION accessor must be unnormalized FLOAT VEC3")
    stride = evidence.byte_stride or 12
    view_offset, _view_length = view_ranges[evidence.view_index]
    start = view_offset + evidence.byte_offset
    rows = np.asarray(
        [
            struct.unpack_from("<3f", binary, start + index * stride)
            for index in range(evidence.count)
        ],
        dtype=np.float64,
    )
    if not np.isfinite(rows).all():
        raise GlbKtx2VariantError("GLB POSITION accessor contains non-finite values")
    return rows


def _geometry_payload(
    document: dict[str, object],
    binary: bytes,
) -> dict[str, object]:
    view_ranges = _validate_buffer_views(document, binary)
    accessors = _validate_accessors(document, view_ranges)
    meshes = _required_list(document, "meshes")
    materials = _required_list(document, "materials")

    accessor_digests: dict[int, str] = {}
    position_rows: dict[int, np.ndarray] = {}
    mesh_positions: dict[int, set[int]] = {}
    mesh_payloads = []
    for mesh_index, raw_mesh in enumerate(meshes):
        if not isinstance(raw_mesh, dict):
            raise GlbKtx2VariantError("GLB mesh is invalid")
        raw_primitives = raw_mesh.get("primitives")
        if not isinstance(raw_primitives, list) or not raw_primitives:
            raise GlbKtx2VariantError("GLB mesh primitives are invalid")
        primitive_payloads = []
        used_positions: set[int] = set()
        for primitive_index, raw_primitive in enumerate(raw_primitives):
            if not isinstance(raw_primitive, dict) or raw_primitive.get("mode", 4) != 4:
                raise GlbKtx2VariantError("GLB requires triangle primitives")
            attributes = raw_primitive.get("attributes")
            if not isinstance(attributes, dict) or any(
                semantic not in attributes for semantic in _GEOMETRY_ATTRIBUTES
            ):
                raise GlbKtx2VariantError(
                    "GLB primitive geometry attributes are incomplete",
                )
            attribute_payload = {}
            for semantic in _GEOMETRY_ATTRIBUTES:
                accessor_index = _index(
                    attributes[semantic],
                    count=len(accessors),
                    label=f"GLB {semantic} accessor",
                )
                accessor_digests.setdefault(
                    accessor_index,
                    _accessor_content_digest(
                        accessors[accessor_index],
                        binary=binary,
                        view_ranges=view_ranges,
                    ),
                )
                attribute_payload[semantic] = accessor_digests[accessor_index]
                if semantic == "POSITION":
                    used_positions.add(accessor_index)
                    position_rows.setdefault(
                        accessor_index,
                        _position_rows(
                            accessors[accessor_index],
                            binary=binary,
                            view_ranges=view_ranges,
                        ),
                    )
            index_accessor = _index(
                raw_primitive.get("indices"),
                count=len(accessors),
                label="GLB index accessor",
            )
            accessor_digests.setdefault(
                index_accessor,
                _accessor_content_digest(
                    accessors[index_accessor],
                    binary=binary,
                    view_ranges=view_ranges,
                ),
            )
            material_index = _index(
                raw_primitive.get("material"),
                count=len(materials),
                label="GLB primitive material",
            )
            material = materials[material_index]
            if not isinstance(material, dict) or not isinstance(
                material.get("extras"),
                dict,
            ):
                raise GlbKtx2VariantError("GLB material slot identity is missing")
            slot_id = material["extras"].get("slot_id")
            if not isinstance(slot_id, str) or not slot_id.startswith("material-"):
                raise GlbKtx2VariantError("GLB material slot identity is invalid")
            primitive_payloads.append(
                {
                    "attributes": attribute_payload,
                    "indices": accessor_digests[index_accessor],
                    "material_slot_id": slot_id,
                    "mesh_index": mesh_index,
                    "mode": 4,
                    "primitive_index": primitive_index,
                },
            )
        mesh_positions[mesh_index] = used_positions
        mesh_payloads.append(primitive_payloads)

    nodes = _required_list(document, "nodes")
    world_by_node = _reachable_node_matrices(document)
    node_payloads = []
    transformed_points = []
    for node_index, world in sorted(world_by_node.items()):
        raw_node = nodes[node_index]
        if not isinstance(raw_node, dict):
            raise GlbKtx2VariantError("GLB node is invalid")
        mesh_index = raw_node.get("mesh")
        if mesh_index is not None:
            mesh_index = _index(
                mesh_index,
                count=len(meshes),
                label="GLB node mesh",
            )
            for accessor_index in sorted(mesh_positions[mesh_index]):
                points = position_rows[accessor_index]
                homogeneous = np.column_stack((points, np.ones(len(points))))
                transformed_points.append((world @ homogeneous.T).T[:, :3])
        children = raw_node.get("children", [])
        if not isinstance(children, list):
            raise GlbKtx2VariantError("GLB node children are invalid")
        node_payloads.append(
            {
                "children": children,
                "mesh": mesh_index,
                "node_index": node_index,
                "world_matrix": [float(value) for value in world.flatten()],
            },
        )
    if not transformed_points:
        raise GlbKtx2VariantError("GLB active scene contains no geometry")
    points = np.concatenate(transformed_points, axis=0)
    if not np.isfinite(points).all():
        raise GlbKtx2VariantError("GLB transformed geometry is non-finite")
    minimum = [float(value) for value in points.min(axis=0)]
    maximum = [float(value) for value in points.max(axis=0)]
    if any(not math.isfinite(value) for value in (*minimum, *maximum)):
        raise GlbKtx2VariantError("GLB geometry bounds are non-finite")
    return {
        "active_scene": document.get("scene", 0),
        "bounds_gltf": {"max": maximum, "min": minimum},
        "meshes": mesh_payloads,
        "nodes": node_payloads,
    }


def geometry_fingerprint_glb(glb_bytes: bytes) -> str:
    """Hash geometry, topology, slot assignment, bounds, and node transforms."""

    try:
        if type(glb_bytes) is not bytes:
            raise GlbKtx2VariantError("geometry fingerprint requires exact GLB bytes")
        _raw, document, binary = _load_glb_bytes(glb_bytes)
        payload = _geometry_payload(document, binary)
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
    except GlbKtx2VariantError:
        raise
    except GlbMaterialAuditError as exc:
        raise GlbKtx2VariantError(
            f"GLB geometry fingerprint failed: {exc}",
        ) from exc
    except (KeyError, TypeError, ValueError, struct.error) as exc:
        raise GlbKtx2VariantError(
            f"GLB geometry fingerprint failed: {exc}",
        ) from exc


def _add_used_extension(
    document: dict[str, object],
    key: str,
) -> None:
    existing = document.get(key, [])
    if not isinstance(existing, list) or any(not isinstance(value, str) for value in existing):
        raise GlbKtx2VariantError(f"GLB {key} declaration is invalid")
    if len(set(existing)) != len(existing):
        raise GlbKtx2VariantError(f"GLB {key} declaration contains duplicates")
    document[key] = sorted({*existing, _KHR_TEXTURE_BASISU})


def rewrite_glb_for_ktx2(
    fallback_glb: bytes,
    replacements: Mapping[str, KtxTextureDescriptor],
) -> bytes:
    """Add KTX2 BasisU alternatives for an exact non-empty PNG subset."""

    try:
        if type(fallback_glb) is not bytes or not isinstance(replacements, Mapping):
            raise GlbKtx2VariantError(
                "KTX2 rewrite requires exact GLB bytes and a replacement mapping",
            )
        _raw, parsed_document, binary = _load_glb_bytes(fallback_glb)
        document = copy.deepcopy(parsed_document)
        view_ranges = _validate_buffer_views(document, binary)
        _validate_accessors(document, view_ranges)

        images = _required_list(document, "images")
        source_uris = []
        for image in images:
            if (
                not isinstance(image, dict)
                or image.get("mimeType") != "image/png"
                or type(image.get("uri")) is not str
                or "bufferView" in image
            ):
                raise GlbKtx2VariantError(
                    "GLB image must be an external shared PNG URI",
                )
            uri = image["uri"]
            if _SHARED_PNG_URI.fullmatch(uri) is None:
                raise GlbKtx2VariantError("GLB image URI is unsafe or unsupported")
            source_uris.append(uri)
        if len(source_uris) != len(set(source_uris)):
            raise GlbKtx2VariantError("GLB image URI closure contains duplicates")
        if not replacements or not set(replacements) <= set(source_uris):
            raise GlbKtx2VariantError(
                "GLB image and KTX2 replacement closure disagree",
            )

        textures = _required_list(document, "textures")
        texture_sources = []
        for texture in textures:
            if not isinstance(texture, dict):
                raise GlbKtx2VariantError("GLB texture declaration is invalid")
            source = _index(
                texture.get("source"),
                count=len(images),
                label="GLB texture source",
            )
            extensions = texture.get("extensions", {})
            if not isinstance(extensions, dict) or _KHR_TEXTURE_BASISU in extensions:
                raise GlbKtx2VariantError(
                    "GLB texture already has an invalid BasisU declaration",
                )
            texture_sources.append(source)
        if set(texture_sources) != set(range(len(images))):
            raise GlbKtx2VariantError(
                "GLB texture closure does not reference every image",
            )

        roles_by_image: dict[int, str] = {}
        materials = _required_list(document, "materials")
        for material in materials:
            if not isinstance(material, dict) or not isinstance(
                material.get("pbrMetallicRoughness"),
                dict,
            ):
                raise GlbKtx2VariantError("GLB material PBR declaration is invalid")
            pbr = material["pbrMetallicRoughness"]
            role_bindings = (
                ("base_color", pbr.get("baseColorTexture")),
                ("normal", material.get("normalTexture")),
                ("orm", pbr.get("metallicRoughnessTexture")),
            )
            for role, binding in role_bindings:
                if not isinstance(binding, dict):
                    raise GlbKtx2VariantError(
                        "GLB material texture role closure is incomplete",
                    )
                texture_index = _index(
                    binding.get("index"),
                    count=len(textures),
                    label=f"GLB {role} texture",
                )
                image_index = texture_sources[texture_index]
                previous = roles_by_image.setdefault(image_index, role)
                if previous != role:
                    raise GlbKtx2VariantError(
                        "GLB image is assigned conflicting texture roles",
                    )
        if set(roles_by_image) != set(range(len(images))):
            raise GlbKtx2VariantError(
                "GLB material texture role closure is incomplete",
            )

        ktx_image_indices = {}
        for image_index, source_uri in enumerate(source_uris):
            if source_uri not in replacements:
                continue
            descriptor = replacements[source_uri]
            if (
                descriptor.media_type != "image/ktx2"
                or descriptor.object_path != f"objects/{descriptor.sha256}.ktx2"
                or descriptor.role != roles_by_image[image_index]
                or descriptor.transfer != ("srgb" if descriptor.role == "base_color" else "linear")
            ):
                raise GlbKtx2VariantError(
                    "KTX2 replacement descriptor role or identity is invalid",
                )
            ktx_image_indices[source_uri] = len(images)
            images.append(
                {
                    "uri": f"../textures/{descriptor.sha256}.ktx2",
                    "mimeType": "image/ktx2",
                },
            )
        for texture, source in zip(textures, texture_sources, strict=True):
            source_uri = source_uris[source]
            if source_uri not in ktx_image_indices:
                continue
            extensions = texture.get("extensions", {})
            extensions[_KHR_TEXTURE_BASISU] = {
                "source": ktx_image_indices[source_uri],
            }
            texture["extensions"] = extensions

        _add_used_extension(document, "extensionsUsed")

        primary = canonical_glb_bytes(document, binary)
        if geometry_fingerprint_glb(primary) != geometry_fingerprint_glb(
            fallback_glb,
        ):
            raise GlbKtx2VariantError(
                "KTX2 rewrite changed the GLB geometry fingerprint",
            )
        return primary
    except GlbKtx2VariantError:
        raise
    except GlbMaterialAuditError as exc:
        raise GlbKtx2VariantError(f"KTX2 GLB rewrite failed: {exc}") from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise GlbKtx2VariantError(f"KTX2 GLB rewrite failed: {exc}") from exc
