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

from pipeline.synthetic_village.mesh_asset_bundle import (
    MeshAssetBundleError,
    load_mesh_asset_bundle,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    MESH_ASSET_BUNDLE_V2_SCHEMA,
    MeshAssetBundleV2,
    MeshAssetLod2SourceV2,
    TextureBindingV2,
    TextureObjectV2,
    canonical_mesh_asset_bundle_v2_bytes,
    prepare_mesh_asset_bundle_v2,
    publish_mesh_asset_bundle_v2,
    read_verified_mesh_texture,
)
from tests.test_glb_shared_texture_audit import (
    _fixture as write_shared_texture_glb_fixture,
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
    prepared, _objects = _prepare_real_v2_fixture(tmp_path)
    manifest_bytes = canonical_mesh_asset_bundle_v2_bytes(prepared.manifest)

    loaded = load_mesh_asset_bundle(prepared.staging_root)

    assert type(loaded) is MeshAssetBundleV2
    assert canonical_mesh_asset_bundle_v2_bytes(loaded) == manifest_bytes


def _grid_geometry(
    *,
    rows: int = 20,
    columns: int = 25,
) -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[int, ...],
]:
    positions = tuple(
        (
            -1.0 + 2.0 * column / columns,
            0.0,
            -1.0 + 2.0 * row / rows,
        )
        for row in range(rows + 1)
        for column in range(columns + 1)
    )
    indices = []
    stride = columns + 1
    for row in range(rows):
        for column in range(columns):
            lower_left = row * stride + column
            lower_right = lower_left + 1
            upper_left = lower_left + stride
            upper_right = upper_left + 1
            indices.extend(
                (
                    lower_left,
                    lower_right,
                    upper_right,
                    lower_left,
                    upper_right,
                    upper_left,
                ),
            )
    return positions, tuple(indices)


def _prepare_real_v2_fixture(tmp_path: Path):
    from tests.test_mesh_asset_bundle import write_mesh_bundle_fixture

    source_v1_root, _glb = write_mesh_bundle_fixture(
        tmp_path / "source-v1",
        asset_id="stone_lamp_01",
        kind="prop",
        triangle_counts=(1, 2, 3),
    )
    positions, indices = _grid_geometry()
    (
        near_glb,
        _payload,
        _document,
        bindings,
        objects,
        _expected,
    ) = write_shared_texture_glb_fixture(
        tmp_path / "near",
        kind="prop",
        material_algorithm_id="mirror-sobel-orm-v1",
        positions=positions,
        indices=indices,
    )
    prepared = prepare_mesh_asset_bundle_v2(
        source_v1_bundle_root=source_v1_root,
        lod2_sources=(
            MeshAssetLod2SourceV2(
                asset_id="stone_lamp_01",
                glb_path=near_glb,
                recipe_id="stone-metal-lamp-near-v2",
                texture_bindings=bindings,
            ),
        ),
        texture_root=tmp_path / "near/texture-root",
        texture_objects=objects,
        staging_root=tmp_path / "staging-v2",
        build_tool_id="pytest-near-builder-v2",
    )
    return prepared, objects


def test_v2_preparation_reuses_v1_lod01_and_reads_exact_texture(
    tmp_path: Path,
) -> None:
    prepared, objects = _prepare_real_v2_fixture(tmp_path)
    bundle = prepared.manifest

    assert bundle.source_v1_bundle_id == load_mesh_asset_bundle(
        tmp_path / "source-v1/mesh-bundle",
    ).bundle_id
    assert bundle.records[0].lod["0"].mesh_algorithm_id == (
        "synthetic-template-mesh-v1"
    )
    source = load_mesh_asset_bundle(tmp_path / "source-v1/mesh-bundle")
    assert tuple(
        bundle.records[0].lod[str(level)].glb_sha256
        for level in (0, 1)
    ) == tuple(
        source.records[0].lod[str(level)].glb_sha256
        for level in (0, 1)
    )
    assert bundle.records[0].lod["2"].triangle_count == 1_000
    descriptor = objects[0]
    payload = read_verified_mesh_texture(
        prepared.staging_root,
        bundle=bundle,
        sha256=descriptor.sha256,
    )
    assert hashlib.sha256(payload).hexdigest() == descriptor.sha256


def test_v2_publication_is_absent_only_and_reuses_exact_identity(
    tmp_path: Path,
) -> None:
    from tests.test_mesh_asset_bundle import write_mesh_bundle_fixture

    source_v1_root, _glb = write_mesh_bundle_fixture(
        tmp_path / "source-v1",
        asset_id="stone_lamp_01",
        kind="prop",
        triangle_counts=(1, 2, 3),
    )
    positions, indices = _grid_geometry()
    near_glb, _payload, _document, bindings, objects, _expected = (
        write_shared_texture_glb_fixture(
            tmp_path / "near",
            kind="prop",
            material_algorithm_id="mirror-sobel-orm-v1",
            positions=positions,
            indices=indices,
        )
    )
    kwargs = {
        "source_v1_bundle_root": source_v1_root,
        "lod2_sources": (
            MeshAssetLod2SourceV2(
                asset_id="stone_lamp_01",
                glb_path=near_glb,
                recipe_id="stone-metal-lamp-near-v2",
                texture_bindings=bindings,
            ),
        ),
        "texture_root": tmp_path / "near/texture-root",
        "texture_objects": objects,
        "publication_root": tmp_path / "published",
        "work_root": tmp_path / "work",
        "build_tool_id": "pytest-near-builder-v2",
    }

    first = publish_mesh_asset_bundle_v2(**kwargs)
    second = publish_mesh_asset_bundle_v2(**kwargs)

    assert first.reused is False
    assert second.reused is True
    assert second.bundle_id == first.bundle_id
    assert load_mesh_asset_bundle(first.final_directory).bundle_id == first.bundle_id
    texture = load_mesh_asset_bundle(first.final_directory).texture_objects[0]
    target = first.final_directory / texture.object_path
    target.write_bytes(target.read_bytes() + b"\0")
    with pytest.raises(MeshAssetBundleError, match="texture"):
        publish_mesh_asset_bundle_v2(**kwargs)
