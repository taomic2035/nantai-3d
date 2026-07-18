"""Independent structural audit for embedded textured GLB artifacts."""

from __future__ import annotations

import copy
import io
import json
import struct
from pathlib import Path

import pytest
from PIL import Image

from pipeline.synthetic_village.glb_material_audit import (
    ExpectedGlbMaterial,
    GlbMaterialAuditError,
    audit_textured_glb,
)

SLOT_ID = "material-fieldstone-01"
SOURCE_SHA256 = "1" * 64
BUNDLE_ID = "2" * 64


def _glb(document: dict, binary: bytes) -> bytes:
    json_bytes = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, total),
            struct.pack("<I4s", len(json_bytes), b"JSON"),
            json_bytes,
            struct.pack("<I4s", len(binary), b"BIN\0"),
            binary,
        ),
    )


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(
        output,
        format="PNG",
        compress_level=9,
        optimize=False,
    )
    return output.getvalue()


def _document_and_binary() -> tuple[dict, bytes]:
    binary = bytearray()
    buffer_views = []

    def append(payload: bytes, *, target: int | None = None) -> int:
        binary.extend(b"\0" * (-len(binary) % 4))
        offset = len(binary)
        binary.extend(payload)
        view = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(payload),
        }
        if target is not None:
            view["target"] = target
        buffer_views.append(view)
        return len(buffer_views) - 1

    position_view = append(
        struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0),
        target=34962,
    )
    normal_view = append(
        struct.pack("<9f", 0, 0, 1, 0, 0, 1, 0, 0, 1),
        target=34962,
    )
    uv_view = append(
        struct.pack("<6f", 0, 0, 1, 0, 0, 1),
        target=34962,
    )
    tangent_view = append(
        struct.pack("<12f", 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1),
        target=34962,
    )
    base_view = append(_png_bytes((127, 91, 63)))
    normal_image_view = append(_png_bytes((128, 128, 255)))
    orm_view = append(_png_bytes((255, 192, 0)))

    document = {
        "asset": {"generator": "pytest-handcrafted", "version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": buffer_views,
        "accessors": [
            {
                "bufferView": position_view,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
            },
            {
                "bufferView": normal_view,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
            },
            {
                "bufferView": uv_view,
                "componentType": 5126,
                "count": 3,
                "type": "VEC2",
            },
            {
                "bufferView": tangent_view,
                "componentType": 5126,
                "count": 3,
                "type": "VEC4",
            },
        ],
        "images": [
            {"bufferView": base_view, "mimeType": "image/png"},
            {"bufferView": normal_image_view, "mimeType": "image/png"},
            {"bufferView": orm_view, "mimeType": "image/png"},
        ],
        "textures": [{"source": 0}, {"source": 1}, {"source": 2}],
        "materials": [
            {
                "name": SLOT_ID,
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0, "texCoord": 0},
                    "metallicRoughnessTexture": {"index": 2, "texCoord": 0},
                },
                "normalTexture": {"index": 1, "texCoord": 0},
                "extras": {
                    "slot_id": SLOT_ID,
                    "source_sha256": SOURCE_SHA256,
                    "bundle_id": BUNDLE_ID,
                    "algorithm_id": "mirror-sobel-orm-v1",
                    "synthetic": True,
                    "uv_policy": "dominant-axis-box",
                },
            },
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 0,
                            "NORMAL": 1,
                            "TEXCOORD_0": 2,
                            "TANGENT": 3,
                        },
                        "material": 0,
                        "mode": 4,
                    },
                ],
            },
        ],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    return document, bytes(binary)


def _expected() -> tuple[ExpectedGlbMaterial, ...]:
    return (
        ExpectedGlbMaterial(
            slot_id=SLOT_ID,
            source_sha256=SOURCE_SHA256,
            bundle_id=BUNDLE_ID,
            algorithm_id="mirror-sobel-orm-v1",
        ),
    )


def test_audit_accepts_embedded_pbr_material_with_uv_and_tangent(
    tmp_path: Path,
) -> None:
    document, binary = _document_and_binary()
    glb_path = tmp_path / "textured.glb"
    glb_path.write_bytes(_glb(document, binary))

    audit = audit_textured_glb(glb_path, expected_materials=_expected())

    assert audit.material_count == 1
    assert audit.mesh_count == 1
    assert audit.primitive_count == 1
    assert audit.textured_primitive_count == 1
    assert audit.uv_primitive_count == 1
    assert audit.tangent_primitive_count == 1
    assert audit.embedded_image_count == 3
    assert audit.texture_count == 3
    assert audit.external_uri_count == 0
    assert audit.slot_ids == (SLOT_ID,)
    assert len(audit.glb_sha256) == 64


def _mutate(document: dict, case: str) -> None:
    primitive = document["meshes"][0]["primitives"][0]
    material = document["materials"][0]
    if case in {"TEXCOORD_0", "TANGENT"}:
        primitive["attributes"].pop(case)
    elif case == "baseColorTexture":
        material["pbrMetallicRoughness"].pop(case)
    elif case == "normalTexture":
        material.pop(case)
    elif case == "metallicRoughnessTexture":
        material["pbrMetallicRoughness"].pop(case)
    elif case == "image-bufferView":
        material_image = document["images"][0]
        material_image.pop("bufferView")
    elif case == "material-extras":
        material["extras"].pop("source_sha256")
    elif case == "external-image-uri":
        material_image = document["images"][0]
        material_image.pop("bufferView")
        material_image["uri"] = "base-color.png"
    elif case == "external-buffer-uri":
        document["buffers"][0]["uri"] = "geometry.bin"
    elif case == "bufferView-range":
        document["bufferViews"][0]["byteLength"] = document["buffers"][0]["byteLength"] + 1
    elif case == "accessor-range":
        document["accessors"][0]["count"] = 999
    else:  # pragma: no cover - parametrization is closed
        raise AssertionError(case)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("TEXCOORD_0", "TEXCOORD_0"),
        ("TANGENT", "TANGENT"),
        ("baseColorTexture", "base-color"),
        ("normalTexture", "normal"),
        ("metallicRoughnessTexture", "metallic-roughness"),
        ("image-bufferView", "embedded"),
        ("material-extras", "extras"),
        ("external-image-uri", "external URI"),
        ("external-buffer-uri", "external URI"),
        ("bufferView-range", "buffer view"),
        ("accessor-range", "accessor"),
    ],
)
def test_audit_rejects_incomplete_or_external_material_evidence(
    case: str,
    message: str,
    tmp_path: Path,
) -> None:
    document, binary = _document_and_binary()
    mutated = copy.deepcopy(document)
    _mutate(mutated, case)
    glb_path = tmp_path / f"{case}.glb"
    glb_path.write_bytes(_glb(mutated, binary))

    with pytest.raises(GlbMaterialAuditError, match=message):
        audit_textured_glb(glb_path, expected_materials=_expected())


def test_audit_rejects_material_identity_not_in_expected_closure(
    tmp_path: Path,
) -> None:
    document, binary = _document_and_binary()
    document["materials"][0]["extras"]["source_sha256"] = "3" * 64
    glb_path = tmp_path / "wrong-source.glb"
    glb_path.write_bytes(_glb(document, binary))

    with pytest.raises(GlbMaterialAuditError, match="expected material identity"):
        audit_textured_glb(glb_path, expected_materials=_expected())


def test_audit_rejects_malformed_glb_length(tmp_path: Path) -> None:
    document, binary = _document_and_binary()
    payload = bytearray(_glb(document, binary))
    struct.pack_into("<I", payload, 8, len(payload) + 4)
    glb_path = tmp_path / "wrong-length.glb"
    glb_path.write_bytes(payload)

    with pytest.raises(GlbMaterialAuditError, match="length or version"):
        audit_textured_glb(glb_path, expected_materials=_expected())
