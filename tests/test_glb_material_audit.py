"""Independent structural audit for embedded textured GLB artifacts."""

from __future__ import annotations

import copy
import io
import json
import struct
from pathlib import Path

import pytest
from PIL import Image

from pipeline.synthetic_village.building_geometry import (
    BUILDING_ELEVATIONS,
    BUILDING_GEOMETRY_V2,
    building_variant,
    expected_variant_counts,
)
from pipeline.synthetic_village.glb_material_audit import (
    ExpectedBuildingGeometry,
    ExpectedGlbMaterial,
    GlbMaterialAuditError,
    audit_textured_glb,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan

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


def _document_and_binary(
    *,
    triangle_count: int = 1,
    extra_triangle_count: int | None = None,
) -> tuple[dict, bytes]:
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
    index_view = append(
        struct.pack("<3H", 0, 1, 2) * triangle_count,
        target=34963,
    )
    extra_index_view = (
        append(
            struct.pack("<3H", 0, 1, 2) * extra_triangle_count,
            target=34963,
        )
        if extra_triangle_count is not None
        else None
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
            {
                "bufferView": index_view,
                "componentType": 5123,
                "count": triangle_count * 3,
                "type": "SCALAR",
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
                        "indices": 4,
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
    if extra_index_view is not None and extra_triangle_count is not None:
        document["accessors"].append(
            {
                "bufferView": extra_index_view,
                "componentType": 5123,
                "count": extra_triangle_count * 3,
                "type": "SCALAR",
            },
        )
        document["meshes"].append(
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 0,
                            "NORMAL": 1,
                            "TEXCOORD_0": 2,
                            "TANGENT": 3,
                        },
                        "indices": 5,
                        "material": 0,
                        "mode": 4,
                    },
                ],
            },
        )
        document["nodes"].append({"mesh": 1})
        document["scenes"][0]["nodes"].append(1)
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


def _building_ids() -> tuple[str, ...]:
    return tuple(
        row.object_id
        for row in build_scene_plan().objects
        if row.semantic_class == "building"
    )


def _expected_geometry(
    *,
    primitive_count: int = 1,
) -> ExpectedBuildingGeometry:
    building_ids = _building_ids()
    return ExpectedBuildingGeometry(
        profile_id=BUILDING_GEOMETRY_V2,
        expected_building_ids=building_ids,
        variant_counts=expected_variant_counts(
            building_ids,
            BUILDING_GEOMETRY_V2,
        ),
        expected_added_face_count=700,
        expected_maximum_added_faces_per_building=10,
        expected_primitive_count=primitive_count,
    )


def _v2_document_and_binary(
    *,
    triangle_count: int = 1,
    extra_triangle_count: int | None = None,
) -> tuple[dict, bytes]:
    document, binary = _document_and_binary(
        triangle_count=triangle_count,
        extra_triangle_count=extra_triangle_count,
    )
    roots = []
    nodes = []
    for object_id in _building_ids():
        child_index = len(nodes)
        nodes.append({"mesh": 0})
        root_index = len(nodes)
        nodes.append(
            {
                "children": [child_index],
                "extras": {
                    "nv_added_face_count": 10,
                    "nv_building_geometry_profile": BUILDING_GEOMETRY_V2,
                    "nv_building_variant": building_variant(
                        object_id,
                        BUILDING_GEOMETRY_V2,
                    ),
                    "nv_facade_elevations": json.dumps(
                        BUILDING_ELEVATIONS,
                        separators=(",", ":"),
                    ),
                    "nv_root": True,
                    "nv_semantic_class": "building",
                    "nv_stable_id": object_id,
                },
                "name": f"nv__{object_id}",
            },
        )
        roots.append(root_index)
    if extra_triangle_count is not None:
        nodes.append({"mesh": 1})
        roots.append(len(nodes) - 1)
    document["nodes"] = nodes
    document["scenes"] = [{"nodes": roots}]
    document["scene"] = 0
    return document, binary


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
    assert audit.triangle_count == 1
    assert audit.textured_primitive_count == 1
    assert audit.uv_primitive_count == 1
    assert audit.tangent_primitive_count == 1
    assert audit.embedded_image_count == 3
    assert audit.texture_count == 3
    assert audit.external_uri_count == 0
    assert audit.slot_ids == (SLOT_ID,)
    assert len(audit.glb_sha256) == 64
    assert audit.building_geometry is None


def test_audit_accepts_v2_building_geometry_from_glb_nodes_and_indices(
    tmp_path: Path,
) -> None:
    document, binary = _v2_document_and_binary()
    glb_path = tmp_path / "textured-v2.glb"
    glb_path.write_bytes(_glb(document, binary))

    audit = audit_textured_glb(
        glb_path,
        expected_materials=_expected(),
        expected_building_geometry=_expected_geometry(),
    )

    assert audit.building_geometry is not None
    assert audit.building_geometry.building_count == 70
    assert audit.building_geometry.covered_elevations == BUILDING_ELEVATIONS
    assert audit.building_geometry.variant_counts == {
        "balanced-residence": 21,
        "rear-service-house": 20,
        "side-entry-workshop": 29,
    }
    assert audit.building_geometry.builder_measured_added_face_count == 700
    assert (
        audit.building_geometry.builder_measured_maximum_added_faces_per_building
        == 10
    )
    assert audit.building_geometry.maximum_triangles_per_building == 1
    assert audit.building_geometry.total_triangle_count == 1


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("elevation", "elevations"),
        ("variant", "variant"),
        ("root", "root"),
        ("face-count", "added-face"),
        ("primitive-count", "primitive count"),
    ],
)
def test_audit_rejects_tampered_v2_building_geometry(
    case: str,
    message: str,
    tmp_path: Path,
) -> None:
    document, binary = _v2_document_and_binary()
    first_root = document["nodes"][1]
    extras = first_root["extras"]
    expectation = _expected_geometry()
    if case == "elevation":
        extras["nv_facade_elevations"] = '["front","rear","right"]'
    elif case == "variant":
        expected = extras["nv_building_variant"]
        extras["nv_building_variant"] = (
            "rear-service-house"
            if expected != "rear-service-house"
            else "balanced-residence"
        )
    elif case == "root":
        extras.pop("nv_root")
    elif case == "face-count":
        extras["nv_added_face_count"] = 11
    elif case == "primitive-count":
        expectation = _expected_geometry(primitive_count=2)
    else:  # pragma: no cover - parametrization is closed
        raise AssertionError(case)
    glb_path = tmp_path / f"textured-v2-{case}.glb"
    glb_path.write_bytes(_glb(document, binary))

    with pytest.raises(GlbMaterialAuditError, match=message):
        audit_textured_glb(
            glb_path,
            expected_materials=_expected(),
            expected_building_geometry=expectation,
        )


def test_audit_rejects_v2_building_triangle_budget_overrun(
    tmp_path: Path,
) -> None:
    document, binary = _v2_document_and_binary(triangle_count=721)
    glb_path = tmp_path / "textured-v2-building-budget.glb"
    glb_path.write_bytes(_glb(document, binary))

    with pytest.raises(GlbMaterialAuditError, match="building triangle budget"):
        audit_textured_glb(
            glb_path,
            expected_materials=_expected(),
            expected_building_geometry=_expected_geometry(),
        )


def test_audit_rejects_v2_total_triangle_budget_overrun(
    tmp_path: Path,
) -> None:
    document, binary = _v2_document_and_binary(extra_triangle_count=100_001)
    glb_path = tmp_path / "textured-v2-total-budget.glb"
    glb_path.write_bytes(_glb(document, binary))

    with pytest.raises(GlbMaterialAuditError, match="total triangle budget"):
        audit_textured_glb(
            glb_path,
            expected_materials=_expected(),
            expected_building_geometry=_expected_geometry(primitive_count=2),
        )


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
