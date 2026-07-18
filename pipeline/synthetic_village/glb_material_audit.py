"""Independent fail-closed audit of embedded PBR material evidence in a GLB."""

from __future__ import annotations

import hashlib
import io
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
UvPolicy = Literal[
    "world-xy",
    "dominant-axis-box",
    "roof-slope",
    "object-long-axis",
    "leaf-card",
]

MAX_GLB_BYTES = 512 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
COMPONENT_BYTES = {
    5120: 1,
    5121: 1,
    5122: 2,
    5123: 2,
    5125: 4,
    5126: 4,
}
TYPE_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


class GlbMaterialAuditError(ValueError):
    """A GLB cannot prove the required embedded PBR material contract."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExpectedGlbMaterial(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_sha256: Sha256
    bundle_id: Sha256
    algorithm_id: Literal["mirror-sobel-orm-v1"]


class GlbMaterialAudit(FrozenModel):
    glb_sha256: Sha256
    byte_count: int = Field(ge=1)
    mesh_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_count: int = Field(ge=1)
    texture_count: int = Field(ge=3)
    embedded_image_count: int = Field(ge=3)
    textured_primitive_count: int = Field(ge=1)
    uv_primitive_count: int = Field(ge=1)
    tangent_primitive_count: int = Field(ge=1)
    external_uri_count: Literal[0] = 0
    slot_ids: tuple[str, ...] = Field(min_length=1)


@dataclass(frozen=True)
class _AccessorEvidence:
    count: int
    component_type: int
    value_type: str


def _is_linklike(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _stat_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _read_stable_file(path: Path, *, maximum_bytes: int) -> bytes:
    path = Path(path).expanduser().absolute()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GlbMaterialAuditError("GLB path is not a real file") from exc
    if _is_linklike(path) or not path.is_file() or resolved != path:
        raise GlbMaterialAuditError("GLB path is redirected or not a real file")
    try:
        before = _stat_signature(path)
        if before[2] <= 0 or before[2] > maximum_bytes:
            raise GlbMaterialAuditError("GLB file size is outside the audit bound")
        with path.open("rb") as stream:
            raw = stream.read(maximum_bytes + 1)
        after = _stat_signature(path)
    except GlbMaterialAuditError:
        raise
    except OSError as exc:
        raise GlbMaterialAuditError("GLB file cannot be read stably") from exc
    if before != after or len(raw) != before[2] or len(raw) > maximum_bytes:
        raise GlbMaterialAuditError("GLB file changed during bounded read")
    return raw


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GlbMaterialAuditError(f"GLB JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise GlbMaterialAuditError(f"GLB JSON contains non-finite number: {value}")


def _load_glb(path: Path) -> tuple[bytes, dict[str, object], bytes]:
    raw = _read_stable_file(path, maximum_bytes=MAX_GLB_BYTES)
    if len(raw) < 28:
        raise GlbMaterialAuditError("GLB header is invalid")
    try:
        magic, version, declared = struct.unpack_from("<4sII", raw, 0)
    except struct.error as exc:  # pragma: no cover - guarded by the length check
        raise GlbMaterialAuditError("GLB header is invalid") from exc
    if magic != b"glTF" or version != 2 or declared != len(raw):
        raise GlbMaterialAuditError("GLB length or version is invalid")

    json_length, json_kind = struct.unpack_from("<I4s", raw, 12)
    json_start = 20
    json_end = json_start + json_length
    if (
        json_kind != b"JSON"
        or json_length <= 0
        or json_length % 4
        or json_end + 8 > len(raw)
    ):
        raise GlbMaterialAuditError("GLB JSON chunk is invalid")
    try:
        document = json.loads(
            raw[json_start:json_end].decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except GlbMaterialAuditError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GlbMaterialAuditError("GLB JSON chunk is invalid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise GlbMaterialAuditError("GLB JSON document must be an object")

    binary_length, binary_kind = struct.unpack_from("<I4s", raw, json_end)
    binary_start = json_end + 8
    binary_end = binary_start + binary_length
    if (
        binary_kind != b"BIN\0"
        or binary_length % 4
        or binary_end != len(raw)
    ):
        raise GlbMaterialAuditError("GLB binary chunk length is invalid")
    return raw, document, raw[binary_start:binary_end]


def _required_list(document: dict[str, object], key: str) -> list[object]:
    value = document.get(key)
    if not isinstance(value, list) or not value:
        raise GlbMaterialAuditError(f"GLB {key} must be a non-empty list")
    return value


def _nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GlbMaterialAuditError(f"{label} must be a non-negative integer")
    return value


def _validate_buffer_views(
    document: dict[str, object],
    binary: bytes,
) -> list[tuple[int, int]]:
    buffers = _required_list(document, "buffers")
    if len(buffers) != 1 or not isinstance(buffers[0], dict):
        raise GlbMaterialAuditError("GLB must declare exactly one embedded binary buffer")
    buffer = buffers[0]
    if "uri" in buffer:
        raise GlbMaterialAuditError("GLB contains an external URI")
    declared_length = _nonnegative_int(
        buffer.get("byteLength"),
        label="GLB buffer byteLength",
    )
    padding = len(binary) - declared_length
    if padding < 0 or padding > 3 or any(binary[declared_length:]):
        raise GlbMaterialAuditError("GLB buffer length does not match the binary chunk")

    views = _required_list(document, "bufferViews")
    ranges = []
    for index, raw_view in enumerate(views):
        if not isinstance(raw_view, dict):
            raise GlbMaterialAuditError(f"GLB buffer view {index} is not an object")
        if raw_view.get("buffer") != 0:
            raise GlbMaterialAuditError(f"GLB buffer view {index} targets another buffer")
        offset = _nonnegative_int(
            raw_view.get("byteOffset", 0),
            label=f"GLB buffer view {index} byteOffset",
        )
        length = _nonnegative_int(
            raw_view.get("byteLength"),
            label=f"GLB buffer view {index} byteLength",
        )
        if length == 0 or offset + length > declared_length:
            raise GlbMaterialAuditError(f"GLB buffer view {index} exceeds the binary buffer")
        stride = raw_view.get("byteStride")
        if stride is not None:
            stride = _nonnegative_int(
                stride,
                label=f"GLB buffer view {index} byteStride",
            )
            if stride < 4 or stride > 252 or stride % 4:
                raise GlbMaterialAuditError(
                    f"GLB buffer view {index} byteStride is invalid",
                )
        ranges.append((offset, length))
    return ranges


def _validate_accessors(
    document: dict[str, object],
    view_ranges: list[tuple[int, int]],
) -> list[_AccessorEvidence]:
    accessors = _required_list(document, "accessors")
    views = _required_list(document, "bufferViews")
    evidence = []
    for index, raw_accessor in enumerate(accessors):
        if not isinstance(raw_accessor, dict) or "sparse" in raw_accessor:
            raise GlbMaterialAuditError(f"GLB accessor {index} is unsupported or invalid")
        view_index = _nonnegative_int(
            raw_accessor.get("bufferView"),
            label=f"GLB accessor {index} bufferView",
        )
        if view_index >= len(view_ranges):
            raise GlbMaterialAuditError(f"GLB accessor {index} buffer view is out of range")
        component_type = raw_accessor.get("componentType")
        value_type = raw_accessor.get("type")
        if component_type not in COMPONENT_BYTES or value_type not in TYPE_COMPONENTS:
            raise GlbMaterialAuditError(f"GLB accessor {index} type is invalid")
        count = _nonnegative_int(
            raw_accessor.get("count"),
            label=f"GLB accessor {index} count",
        )
        if count == 0:
            raise GlbMaterialAuditError(f"GLB accessor {index} count must be positive")
        byte_offset = _nonnegative_int(
            raw_accessor.get("byteOffset", 0),
            label=f"GLB accessor {index} byteOffset",
        )
        component_bytes = COMPONENT_BYTES[component_type]
        element_bytes = component_bytes * TYPE_COMPONENTS[value_type]
        raw_view = views[view_index]
        if not isinstance(raw_view, dict):  # pragma: no cover - checked above
            raise GlbMaterialAuditError(f"GLB buffer view {view_index} is invalid")
        stride = raw_view.get("byteStride", element_bytes)
        if (
            not isinstance(stride, int)
            or isinstance(stride, bool)
            or stride < element_bytes
            or stride % component_bytes
            or byte_offset % component_bytes
        ):
            raise GlbMaterialAuditError(f"GLB accessor {index} alignment is invalid")
        required = byte_offset + stride * (count - 1) + element_bytes
        if required > view_ranges[view_index][1]:
            raise GlbMaterialAuditError(f"GLB accessor {index} exceeds its buffer view")
        evidence.append(
            _AccessorEvidence(
                count=count,
                component_type=component_type,
                value_type=value_type,
            ),
        )
    return evidence


def _validate_images(
    document: dict[str, object],
    binary: bytes,
    view_ranges: list[tuple[int, int]],
) -> int:
    images = _required_list(document, "images")
    for index, raw_image in enumerate(images):
        if not isinstance(raw_image, dict):
            raise GlbMaterialAuditError(f"GLB image {index} is not an object")
        if "uri" in raw_image:
            raise GlbMaterialAuditError("GLB contains an external URI")
        view_index = raw_image.get("bufferView")
        if (
            isinstance(view_index, bool)
            or not isinstance(view_index, int)
            or view_index < 0
            or view_index >= len(view_ranges)
            or raw_image.get("mimeType") != "image/png"
        ):
            raise GlbMaterialAuditError(
                f"GLB image {index} is not an embedded PNG buffer view",
            )
        offset, length = view_ranges[view_index]
        payload = binary[offset : offset + length]
        if not payload.startswith(PNG_SIGNATURE):
            raise GlbMaterialAuditError(f"GLB image {index} is not PNG bytes")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if image.format != "PNG" or image.width <= 0 or image.height <= 0:
                    raise GlbMaterialAuditError(f"GLB image {index} PNG is invalid")
        except GlbMaterialAuditError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise GlbMaterialAuditError(f"GLB image {index} PNG is invalid") from exc
    return len(images)


def _validate_textures(document: dict[str, object], *, image_count: int) -> list[int]:
    textures = _required_list(document, "textures")
    sources = []
    for index, raw_texture in enumerate(textures):
        if not isinstance(raw_texture, dict):
            raise GlbMaterialAuditError(f"GLB texture {index} is not an object")
        source = raw_texture.get("source")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source < 0
            or source >= image_count
        ):
            raise GlbMaterialAuditError(f"GLB texture {index} image source is invalid")
        sources.append(source)
    return sources


def _texture_index(
    container: dict[str, object],
    key: str,
    *,
    label: str,
    texture_count: int,
) -> int:
    texture_info = container.get(key)
    if not isinstance(texture_info, dict):
        raise GlbMaterialAuditError(f"GLB material is missing its {label} texture")
    index = texture_info.get("index")
    tex_coord = texture_info.get("texCoord", 0)
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= texture_count
        or tex_coord != 0
    ):
        raise GlbMaterialAuditError(f"GLB material {label} texture binding is invalid")
    return index


def _validate_materials(
    document: dict[str, object],
    *,
    expected_materials: tuple[ExpectedGlbMaterial, ...],
    texture_sources: list[int],
) -> tuple[list[str], set[int]]:
    materials = _required_list(document, "materials")
    expected_by_slot = {material.slot_id: material for material in expected_materials}
    if len(expected_by_slot) != len(expected_materials) or not expected_by_slot:
        raise GlbMaterialAuditError("expected material identities must be non-empty and unique")
    actual_slots = []
    textured_materials = set()
    for index, raw_material in enumerate(materials):
        if not isinstance(raw_material, dict):
            raise GlbMaterialAuditError(f"GLB material {index} is not an object")
        extras = raw_material.get("extras")
        if not isinstance(extras, dict):
            raise GlbMaterialAuditError(f"GLB material {index} extras are missing")
        required_extras = {
            "slot_id",
            "source_sha256",
            "bundle_id",
            "algorithm_id",
            "synthetic",
            "uv_policy",
        }
        if not required_extras.issubset(extras):
            raise GlbMaterialAuditError(f"GLB material {index} extras are incomplete")
        if extras.get("synthetic") is not True or extras.get("uv_policy") not in {
            "world-xy",
            "dominant-axis-box",
            "roof-slope",
            "object-long-axis",
            "leaf-card",
        }:
            raise GlbMaterialAuditError(f"GLB material {index} extras are invalid")
        try:
            actual_identity = ExpectedGlbMaterial.model_validate(
                {
                    "slot_id": extras["slot_id"],
                    "source_sha256": extras["source_sha256"],
                    "bundle_id": extras["bundle_id"],
                    "algorithm_id": extras["algorithm_id"],
                },
            )
        except ValidationError as exc:
            raise GlbMaterialAuditError(
                f"GLB material {index} extras identity is invalid",
            ) from exc
        if expected_by_slot.get(actual_identity.slot_id) != actual_identity:
            raise GlbMaterialAuditError(
                f"GLB material {index} does not match its expected material identity",
            )

        pbr = raw_material.get("pbrMetallicRoughness")
        if not isinstance(pbr, dict):
            raise GlbMaterialAuditError(f"GLB material {index} PBR block is missing")
        base = _texture_index(
            pbr,
            "baseColorTexture",
            label="base-color",
            texture_count=len(texture_sources),
        )
        normal = _texture_index(
            raw_material,
            "normalTexture",
            label="normal",
            texture_count=len(texture_sources),
        )
        orm = _texture_index(
            pbr,
            "metallicRoughnessTexture",
            label="metallic-roughness",
            texture_count=len(texture_sources),
        )
        if len({base, normal, orm}) != 3 or len(
            {texture_sources[base], texture_sources[normal], texture_sources[orm]},
        ) != 3:
            raise GlbMaterialAuditError(
                f"GLB material {index} PBR roles must bind three distinct embedded images",
            )
        actual_slots.append(actual_identity.slot_id)
        textured_materials.add(index)
    if (
        len(materials) != len(expected_materials)
        or set(actual_slots) != set(expected_by_slot)
        or len(actual_slots) != len(set(actual_slots))
    ):
        raise GlbMaterialAuditError("GLB material set does not match the expected closure")
    return actual_slots, textured_materials


def _attribute(
    attributes: dict[str, object],
    name: str,
    *,
    accessors: list[_AccessorEvidence],
) -> tuple[int, _AccessorEvidence]:
    index = attributes.get(name)
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= len(accessors)
    ):
        raise GlbMaterialAuditError(f"GLB mesh primitive requires {name}")
    return index, accessors[index]


def _validate_meshes(
    document: dict[str, object],
    *,
    accessors: list[_AccessorEvidence],
    material_count: int,
    textured_materials: set[int],
) -> tuple[int, int, int, set[int]]:
    meshes = _required_list(document, "meshes")
    primitive_count = 0
    uv_count = 0
    tangent_count = 0
    used_materials = set()
    for mesh_index, raw_mesh in enumerate(meshes):
        if not isinstance(raw_mesh, dict):
            raise GlbMaterialAuditError(f"GLB mesh {mesh_index} is not an object")
        primitives = raw_mesh.get("primitives")
        if not isinstance(primitives, list) or not primitives:
            raise GlbMaterialAuditError(f"GLB mesh {mesh_index} has no primitives")
        for raw_primitive in primitives:
            if not isinstance(raw_primitive, dict):
                raise GlbMaterialAuditError("GLB mesh primitive is not an object")
            material = raw_primitive.get("material")
            if (
                isinstance(material, bool)
                or not isinstance(material, int)
                or material < 0
                or material >= material_count
            ):
                raise GlbMaterialAuditError("GLB mesh primitive has no valid material")
            attributes = raw_primitive.get("attributes")
            if not isinstance(attributes, dict):
                raise GlbMaterialAuditError("GLB mesh primitive attributes are missing")
            _, position = _attribute(attributes, "POSITION", accessors=accessors)
            _, normal = _attribute(attributes, "NORMAL", accessors=accessors)
            _, uv = _attribute(attributes, "TEXCOORD_0", accessors=accessors)
            _, tangent = _attribute(attributes, "TANGENT", accessors=accessors)
            if (
                (position.component_type, position.value_type) != (5126, "VEC3")
                or (normal.component_type, normal.value_type) != (5126, "VEC3")
                or (uv.component_type, uv.value_type) != (5126, "VEC2")
                or (tangent.component_type, tangent.value_type) != (5126, "VEC4")
                or len({position.count, normal.count, uv.count, tangent.count}) != 1
            ):
                raise GlbMaterialAuditError(
                    "GLB mesh primitive vertex accessor contract is invalid",
                )
            if material not in textured_materials:
                raise GlbMaterialAuditError("GLB mesh primitive material is not textured")
            primitive_count += 1
            uv_count += 1
            tangent_count += 1
            used_materials.add(material)
    return primitive_count, uv_count, tangent_count, used_materials


def audit_textured_glb(
    path: Path,
    expected_materials: tuple[ExpectedGlbMaterial, ...],
) -> GlbMaterialAudit:
    """Audit actual GLB bytes without trusting filenames or build-report claims."""

    try:
        expected_materials = tuple(
            material
            if isinstance(material, ExpectedGlbMaterial)
            else ExpectedGlbMaterial.model_validate(material)
            for material in expected_materials
        )
        raw, document, binary = _load_glb(Path(path))
        external_uri_count = sum(
            1
            for collection_name in ("buffers", "images")
            for item in document.get(collection_name, [])
            if isinstance(item, dict) and "uri" in item
        )
        if external_uri_count:
            raise GlbMaterialAuditError("GLB contains an external URI")
        view_ranges = _validate_buffer_views(document, binary)
        accessors = _validate_accessors(document, view_ranges)
        image_count = _validate_images(document, binary, view_ranges)
        texture_sources = _validate_textures(document, image_count=image_count)
        slot_ids, textured_materials = _validate_materials(
            document,
            expected_materials=expected_materials,
            texture_sources=texture_sources,
        )
        materials = _required_list(document, "materials")
        meshes = _required_list(document, "meshes")
        primitive_count, uv_count, tangent_count, used_materials = _validate_meshes(
            document,
            accessors=accessors,
            material_count=len(materials),
            textured_materials=textured_materials,
        )
        if used_materials != textured_materials:
            raise GlbMaterialAuditError("GLB contains an expected material with no primitive")
        return GlbMaterialAudit(
            glb_sha256=hashlib.sha256(raw).hexdigest(),
            byte_count=len(raw),
            mesh_count=len(meshes),
            primitive_count=primitive_count,
            material_count=len(materials),
            texture_count=len(texture_sources),
            embedded_image_count=image_count,
            textured_primitive_count=primitive_count,
            uv_primitive_count=uv_count,
            tangent_primitive_count=tangent_count,
            external_uri_count=0,
            slot_ids=tuple(sorted(slot_ids)),
        )
    except GlbMaterialAuditError:
        raise
    except (OSError, TypeError, ValueError, ValidationError) as exc:
        raise GlbMaterialAuditError(f"GLB material audit failed: {exc}") from exc
