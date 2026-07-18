"""Fail-closed verification for immutable textured mesh template bundles."""

from __future__ import annotations

import hashlib
import io
import json
import struct
from pathlib import Path

import pytest
from PIL import Image

from pipeline.synthetic_village.mesh_asset_bundle import (
    MESH_ASSET_BUNDLE_SCHEMA,
    MeshAssetBundleError,
    canonical_mesh_asset_bundle_bytes,
    load_mesh_asset_bundle,
    read_verified_mesh_template_glb,
)

SLOT_ID = "material-fieldstone-01"
SOURCE_SHA256 = "1" * 64
MATERIAL_BUNDLE_ID = "2" * 64
MATERIAL_MANIFEST_SHA256 = "3" * 64


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(
        output,
        format="PNG",
        compress_level=9,
        optimize=False,
    )
    return output.getvalue()


def _glb_payload() -> bytes:
    binary = bytearray()
    views: list[dict[str, int]] = []

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
        views.append(view)
        return len(views) - 1

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
    index_view = append(struct.pack("<3H", 0, 1, 2), target=34963)
    base_view = append(_png_bytes((127, 91, 63)))
    normal_image_view = append(_png_bytes((128, 128, 255)))
    orm_view = append(_png_bytes((255, 192, 0)))
    document = {
        "asset": {"generator": "pytest-mesh-template", "version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
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
                "count": 3,
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
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0, "texCoord": 0},
                    "metallicRoughnessTexture": {"index": 2, "texCoord": 0},
                },
                "normalTexture": {"index": 1, "texCoord": 0},
                "extras": {
                    "slot_id": SLOT_ID,
                    "source_sha256": SOURCE_SHA256,
                    "bundle_id": MATERIAL_BUNDLE_ID,
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
    document_bytes = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    document_bytes += b" " * (-len(document_bytes) % 4)
    binary.extend(b"\0" * (-len(binary) % 4))
    total = 12 + 8 + len(document_bytes) + 8 + len(binary)
    return b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, total),
            struct.pack("<I4s", len(document_bytes), b"JSON"),
            document_bytes,
            struct.pack("<I4s", len(binary), b"BIN\0"),
            bytes(binary),
        ),
    )


def write_mesh_bundle_fixture(
    root: Path,
    *,
    triangle_count: int = 1,
    material_bundle_id: str = MATERIAL_BUNDLE_ID,
) -> tuple[Path, bytes]:
    bundle_root = root / "mesh-bundle"
    object_root = bundle_root / "objects"
    object_root.mkdir(parents=True)
    glb = _glb_payload()
    digest = hashlib.sha256(glb).hexdigest()
    object_path = f"objects/{digest}.glb"
    (bundle_root / object_path).write_bytes(glb)
    lod = {
        str(level): {
            "glb_object_path": object_path,
            "glb_sha256": digest,
            "glb_bytes": len(glb),
            "triangle_count": triangle_count,
            "primitive_count": 1,
            "material_slot_ids": [SLOT_ID],
            "aabb": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 0.0]},
        }
        for level in range(3)
    }
    payload = {
        "schema_version": MESH_ASSET_BUNDLE_SCHEMA,
        "bundle_id": "0" * 64,
        "material_bundle_id": material_bundle_id,
        "material_bundle_manifest_sha256": MATERIAL_MANIFEST_SHA256,
        "synthetic": True,
        "real_photo_textures": False,
        "build_tool_id": "pytest-mesh-template-v1",
        "verification_level": "L0",
        "material_registry": [
            {
                "slot_id": SLOT_ID,
                "source_sha256": SOURCE_SHA256,
                "bundle_id": material_bundle_id,
                "algorithm_id": "mirror-sobel-orm-v1",
            },
        ],
        "records": [
            {
                "asset_id": "house_wood_01",
                "kind": "building",
                "mesh_algorithm_id": "synthetic-template-mesh-v1",
                "footprint_m": [8.0, 6.0, 6.5],
                "lod": lod,
                "synthetic": True,
                "geometry_usability": "preview-only",
            },
        ],
    }
    identity_payload = dict(payload)
    identity_payload.pop("bundle_id")
    payload["bundle_id"] = hashlib.sha256(_canonical(identity_payload)).hexdigest()
    (bundle_root / "manifest.json").write_bytes(_canonical(payload))
    return bundle_root, glb


def test_bundle_identity_and_exact_template_read(tmp_path: Path) -> None:
    bundle_root, expected_glb = write_mesh_bundle_fixture(tmp_path)

    bundle = load_mesh_asset_bundle(bundle_root)

    assert bundle.schema_version == MESH_ASSET_BUNDLE_SCHEMA
    assert bundle.asset_ids == ("house_wood_01",)
    descriptor = bundle.records[0].lod["2"]
    assert descriptor.triangle_count == 1
    assert read_verified_mesh_template_glb(
        bundle_root,
        bundle=bundle,
        asset_id="house_wood_01",
        lod=2,
    ) == expected_glb
    assert hashlib.sha256(
        canonical_mesh_asset_bundle_bytes(bundle, exclude_bundle_id=True),
    ).hexdigest() == bundle.bundle_id


def test_bundle_rejects_tampered_template_bytes(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path)
    object_path = next((bundle_root / "objects").glob("*.glb"))
    object_path.write_bytes(object_path.read_bytes() + b"\0")

    with pytest.raises(MeshAssetBundleError, match="template"):
        load_mesh_asset_bundle(bundle_root)


def test_exact_read_rejects_template_changed_after_load(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path)
    bundle = load_mesh_asset_bundle(bundle_root)
    object_path = next((bundle_root / "objects").glob("*.glb"))
    object_path.write_bytes(object_path.read_bytes() + b"\0")

    with pytest.raises(MeshAssetBundleError, match="template"):
        read_verified_mesh_template_glb(
            bundle_root,
            bundle=bundle,
            asset_id="house_wood_01",
            lod=2,
        )


def test_bundle_rejects_triangle_count_not_measured_from_glb(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path, triangle_count=2)

    with pytest.raises(MeshAssetBundleError, match="triangle"):
        load_mesh_asset_bundle(bundle_root)


def test_bundle_rejects_material_bundle_identity_disagreement(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(
        tmp_path,
        material_bundle_id="4" * 64,
    )

    with pytest.raises(MeshAssetBundleError, match="material"):
        load_mesh_asset_bundle(bundle_root)


def test_bundle_rejects_redirected_template(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path)
    object_path = next((bundle_root / "objects").glob("*.glb"))
    target = tmp_path / "redirect-target.glb"
    object_path.rename(target)
    object_path.symlink_to(target)

    with pytest.raises(MeshAssetBundleError, match="redirected"):
        load_mesh_asset_bundle(bundle_root)


def test_bundle_rejects_unknown_asset_or_lod(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path)
    bundle = load_mesh_asset_bundle(bundle_root)

    with pytest.raises(MeshAssetBundleError, match="asset"):
        read_verified_mesh_template_glb(
            bundle_root,
            bundle=bundle,
            asset_id="missing_asset",
            lod=2,
        )
    with pytest.raises(MeshAssetBundleError, match="LOD"):
        read_verified_mesh_template_glb(
            bundle_root,
            bundle=bundle,
            asset_id="house_wood_01",
            lod=4,
        )
