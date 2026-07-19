"""Schema-v2 identity contracts for shared-texture near mesh templates."""

from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from pipeline.synthetic_village.mesh_asset_bundle import load_mesh_asset_bundle
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    MESH_ASSET_BUNDLE_V2_SCHEMA,
    MeshAssetBundleV2,
    TextureBindingV2,
    TextureObjectV2,
    canonical_mesh_asset_bundle_v2_bytes,
)

SLOT_ID = "material-fieldstone-01"
SOURCE_V1_BUNDLE_ID = "1" * 64
MATERIAL_BUNDLE_ID = "2" * 64
MATERIAL_MANIFEST_SHA256 = "3" * 64


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


@lru_cache(maxsize=3)
def _texture_bytes(role: str) -> bytes:
    colours = {
        "base_color": (94, 122, 67, 255),
        "normal": (128, 128, 255, 255),
        "orm": (255, 192, 0, 255),
    }
    output = io.BytesIO()
    Image.new("RGBA", (1024, 1024), colours[role]).save(
        output,
        format="PNG",
        compress_level=9,
        optimize=False,
    )
    return output.getvalue()


def _binding(role: str) -> dict[str, object]:
    digest = hashlib.sha256(_texture_bytes(role)).hexdigest()
    return {
        "uri": f"../textures/{digest}.png",
        "sha256": digest,
        "role": role,
        "colour_space": "srgb" if role == "base_color" else "non-color",
        "material_slot_id": SLOT_ID,
        "derivation_algorithm_id": "material-map-v1",
        "min_filter": 9987,
        "mag_filter": 9729,
        "wrap_s": 10497,
        "wrap_t": 10497,
    }


def _unsigned_v2_payload(
    *,
    kind: str = "building",
    lod2_triangles: int = 8_000,
) -> dict[str, object]:
    bindings = [_binding(role) for role in ("base_color", "normal", "orm")]
    texture_objects = sorted(
        (
            {
                "object_path": f"textures/{row['sha256']}.png",
                "sha256": row["sha256"],
                "bytes": len(_texture_bytes(row["role"])),
                "mime_type": "image/png",
                "width": 1024,
                "height": 1024,
            }
            for row in bindings
        ),
        key=lambda row: row["object_path"],
    )
    asset_id = {
        "building": "house_wood_01",
        "vegetation": "tree_pine_01",
        "prop": "stone_lamp_01",
    }[kind]
    v1_recipe_id = {
        "building": "weathered-timber-house-v1",
        "vegetation": "layered-pine-v1",
        "prop": "stone-metal-lamp-v1",
    }[kind]
    near_recipe_id = v1_recipe_id.removesuffix("-v1") + "-near-v2"
    lod = {}
    for level, triangles in enumerate((100, 300, lod2_triangles)):
        digest = _sha(f"glb-{kind}-{level}")
        is_near = level == 2
        lod[str(level)] = {
            "glb_object_path": f"objects/{digest}.glb",
            "glb_sha256": digest,
            "glb_bytes": len(f"glb-{kind}-{level}".encode()),
            "triangle_count": triangles,
            "primitive_count": 1,
            "material_slot_ids": [SLOT_ID],
            "aabb": {
                "min": [0.0, 0.0, 0.0],
                "max": [8.0, 6.0, 6.5],
            },
            "mesh_algorithm_id": (
                "synthetic-template-mesh-near-v2"
                if is_near
                else "synthetic-template-mesh-v1"
            ),
            "recipe_id": (
                near_recipe_id
                if is_near
                else v1_recipe_id
            ),
            "texture_storage": (
                "shared-content-addressed" if is_near else "embedded"
            ),
            "texture_bindings": bindings if is_near else [],
        }
    return {
        "schema_version": MESH_ASSET_BUNDLE_V2_SCHEMA,
        "coordinate_encoding": "three-east-up-negative-north",
        "source_v1_bundle_id": SOURCE_V1_BUNDLE_ID,
        "material_bundle_id": MATERIAL_BUNDLE_ID,
        "material_bundle_manifest_sha256": MATERIAL_MANIFEST_SHA256,
        "synthetic": True,
        "real_photo_textures": False,
        "build_tool_id": "pytest-mesh-builder-near-v2",
        "verification_level": "L0",
        "texture_audit_profile": "verified-relative-content-addressed",
        "material_registry": [
            {
                "slot_id": SLOT_ID,
                "source_sha256": _sha("material-source"),
                "bundle_id": MATERIAL_BUNDLE_ID,
                "algorithm_id": "mirror-sobel-orm-v1",
            },
        ],
        "texture_objects": texture_objects,
        "records": [
            {
                "asset_id": asset_id,
                "kind": kind,
                "footprint_m": [8.0, 6.0, 6.5],
                "lod": lod,
                "synthetic": True,
                "geometry_usability": "preview-only",
            },
        ],
    }


def make_v2_bundle_fixture(
    *,
    kind: str = "building",
    lod2_triangles: int = 8_000,
) -> MeshAssetBundleV2:
    unsigned = _unsigned_v2_payload(
        kind=kind,
        lod2_triangles=lod2_triangles,
    )
    bundle_id = hashlib.sha256(
        _canonical(unsigned),
    ).hexdigest()
    return MeshAssetBundleV2.model_validate_json(
        _canonical({"bundle_id": bundle_id, **unsigned}),
    )


def _replace_bundle_id(payload: dict[str, object]) -> dict[str, object]:
    unsigned = deepcopy(payload)
    unsigned.pop("bundle_id", None)
    return {
        "bundle_id": hashlib.sha256(
            _canonical(unsigned),
        ).hexdigest(),
        **unsigned,
    }


def test_v2_requires_per_lod_algorithm_and_exact_triangle_bands() -> None:
    bundle = make_v2_bundle_fixture(kind="building", lod2_triangles=8_000)

    assert bundle.records[0].lod["0"].mesh_algorithm_id == (
        "synthetic-template-mesh-v1"
    )
    assert bundle.records[0].lod["2"].mesh_algorithm_id == (
        "synthetic-template-mesh-near-v2"
    )
    with pytest.raises(ValidationError, match="LOD2 triangle band"):
        make_v2_bundle_fixture(kind="building", lod2_triangles=7_999)


@pytest.mark.parametrize(
    ("kind", "lower", "upper"),
    (
        ("building", 8_000, 15_000),
        ("vegetation", 6_000, 12_000),
        ("prop", 1_000, 4_000),
    ),
)
def test_v2_accepts_inclusive_kind_specific_lod2_bands(
    kind: str,
    lower: int,
    upper: int,
) -> None:
    assert make_v2_bundle_fixture(
        kind=kind,
        lod2_triangles=lower,
    ).records[0].lod["2"].triangle_count == lower
    assert make_v2_bundle_fixture(
        kind=kind,
        lod2_triangles=upper,
    ).records[0].lod["2"].triangle_count == upper
    with pytest.raises(ValidationError, match="LOD2 triangle band"):
        make_v2_bundle_fixture(kind=kind, lod2_triangles=upper + 1)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda payload: payload["records"][0]["lod"]["0"].update(
                mesh_algorithm_id="synthetic-template-mesh-near-v2",
                recipe_id="weathered-timber-house-near-v2",
                texture_storage="shared-content-addressed",
                texture_bindings=[
                    _binding(role)
                    for role in ("base_color", "normal", "orm")
                ],
            ),
            "LOD0/1",
        ),
        (
            lambda payload: payload["records"][0]["lod"]["2"].update(
                mesh_algorithm_id="synthetic-template-mesh-v1",
                recipe_id="weathered-timber-house-v1",
                texture_storage="embedded",
                texture_bindings=[],
            ),
            "LOD2",
        ),
        (
            lambda payload: payload["records"][0]["lod"]["0"].update(
                texture_bindings=[_binding("base_color")],
            ),
            "embedded",
        ),
        (
            lambda payload: payload["records"][0]["lod"]["2"].update(
                texture_bindings=[],
            ),
            "shared",
        ),
    ),
)
def test_v2_rejects_lod_algorithm_or_storage_mismatch(
    mutate,
    message: str,
) -> None:
    payload = _unsigned_v2_payload()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        MeshAssetBundleV2.model_validate_json(_canonical(_replace_bundle_id(payload)))


def test_v2_rejects_duplicate_texture_objects_or_unsorted_bindings() -> None:
    payload = _unsigned_v2_payload()
    payload["texture_objects"].append(deepcopy(payload["texture_objects"][0]))
    with pytest.raises(ValidationError, match="texture objects"):
        MeshAssetBundleV2.model_validate_json(_canonical(_replace_bundle_id(payload)))

    payload = _unsigned_v2_payload()
    payload["records"][0]["lod"]["2"]["texture_bindings"].reverse()
    with pytest.raises(ValidationError, match="bindings"):
        MeshAssetBundleV2.model_validate_json(_canonical(_replace_bundle_id(payload)))


def test_v2_rejects_non_content_addressed_texture_paths_and_wrong_colour_space() -> None:
    binding = _binding("base_color")
    binding["uri"] = "../textures/readable-name.png"
    with pytest.raises(ValidationError, match="content-addressed"):
        TextureBindingV2.model_validate(binding)

    binding = _binding("normal")
    binding["colour_space"] = "srgb"
    with pytest.raises(ValidationError, match="colour space"):
        TextureBindingV2.model_validate(binding)

    digest = hashlib.sha256(_texture_bytes("orm")).hexdigest()
    with pytest.raises(ValidationError, match="content-addressed"):
        TextureObjectV2(
            object_path="textures/readable-name.png",
            sha256=digest,
            bytes=len(_texture_bytes("orm")),
        )


def test_v2_identity_binds_source_v1_bundle_id() -> None:
    bundle = make_v2_bundle_fixture()
    payload = bundle.model_dump(mode="json")
    payload["source_v1_bundle_id"] = "f" * 64

    with pytest.raises(ValidationError, match="bundle ID"):
        MeshAssetBundleV2.model_validate_json(_canonical(payload))


def test_dispatch_loads_canonical_v2_manifest(tmp_path: Path) -> None:
    bundle = make_v2_bundle_fixture()
    bundle_root = tmp_path / "mesh-bundle-v2"
    (bundle_root / "objects").mkdir(parents=True)
    (bundle_root / "textures").mkdir()
    for level in range(3):
        descriptor = bundle.records[0].lod[str(level)]
        (bundle_root / descriptor.glb_object_path).write_bytes(
            f"glb-building-{level}".encode(),
        )
    for texture in bundle.texture_objects:
        role = next(
            binding.role
            for binding in bundle.records[0].lod["2"].texture_bindings
            if binding.sha256 == texture.sha256
        )
        (bundle_root / texture.object_path).write_bytes(
            _texture_bytes(role),
        )
    manifest_bytes = canonical_mesh_asset_bundle_v2_bytes(bundle)
    (bundle_root / "manifest.json").write_bytes(manifest_bytes)

    loaded = load_mesh_asset_bundle(bundle_root)

    assert type(loaded) is MeshAssetBundleV2
    assert canonical_mesh_asset_bundle_v2_bytes(loaded) == manifest_bytes
