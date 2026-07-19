"""Fail-closed verification for immutable textured mesh template bundles."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import pipeline.synthetic_village.mesh_asset_bundle as mesh_asset_bundle
from pipeline.synthetic_village.material_bundle import prepare_material_bundle
from pipeline.synthetic_village.mesh_asset_bundle import (
    MESH_ASSET_BUNDLE_SCHEMA,
    MeshAssetBundle,
    MeshAssetBundleError,
    MeshAssetTemplateSource,
    canonical_mesh_asset_bundle_bytes,
    load_mesh_asset_bundle,
    measure_mesh_template_enu_bounds,
    prepare_mesh_asset_bundle,
    publish_mesh_asset_bundle,
    read_verified_mesh_template_glb,
)
from tests.synthetic_material_fixtures import write_material_visual_pack

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


def _glb_payload(
    *,
    triangle_count: int = 1,
    node_translation: tuple[float, float, float] | None = None,
    source_sha256: str = SOURCE_SHA256,
    material_bundle_id: str = MATERIAL_BUNDLE_ID,
    material_algorithm_id: str = "mirror-sobel-orm-v1",
) -> bytes:
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
    index_view = append(
        struct.pack("<3H", 0, 1, 2) * triangle_count,
        target=34963,
    )
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
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0, "texCoord": 0},
                    "metallicRoughnessTexture": {"index": 2, "texCoord": 0},
                },
                "normalTexture": {"index": 1, "texCoord": 0},
                "extras": {
                    "slot_id": SLOT_ID,
                    "source_sha256": source_sha256,
                    "bundle_id": material_bundle_id,
                    "algorithm_id": material_algorithm_id,
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
        "nodes": [
            {
                "mesh": 0,
                **(
                    {"translation": list(node_translation)}
                    if node_translation is not None
                    else {}
                ),
            },
        ],
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
    triangle_count: int | None = None,
    triangle_counts: tuple[int, int, int] = (1, 2, 3),
    asset_id: str = "house_wood_01",
    kind: str = "building",
    material_bundle_id: str = MATERIAL_BUNDLE_ID,
    gltf_node_translation: tuple[float, float, float] | None = None,
    declared_aabb: dict[str, list[float]] | None = None,
    coordinate_encoding: str = "three-east-up-negative-north",
) -> tuple[Path, bytes]:
    bundle_root = root / "mesh-bundle"
    object_root = bundle_root / "objects"
    object_root.mkdir(parents=True)
    lod = {}
    expected_glb = b""
    for level, measured_triangles in enumerate(triangle_counts):
        glb = _glb_payload(
            triangle_count=measured_triangles,
            node_translation=gltf_node_translation,
        )
        digest = hashlib.sha256(glb).hexdigest()
        object_path = f"objects/{digest}.glb"
        (bundle_root / object_path).write_bytes(glb)
        lod[str(level)] = {
            "glb_object_path": object_path,
            "glb_sha256": digest,
            "glb_bytes": len(glb),
            "triangle_count": (
                triangle_count
                if level == 2 and triangle_count is not None
                else measured_triangles
            ),
            "primitive_count": 1,
            "material_slot_ids": [SLOT_ID],
            "aabb": declared_aabb or {
                "min": [0.0, 0.0, 0.0],
                "max": [1.0, 0.0, 1.0],
            },
        }
        if level == 2:
            expected_glb = glb
    payload = {
        "schema_version": MESH_ASSET_BUNDLE_SCHEMA,
        "bundle_id": "0" * 64,
        "coordinate_encoding": coordinate_encoding,
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
                "asset_id": asset_id,
                "kind": kind,
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
    return bundle_root, expected_glb


def test_bundle_identity_and_exact_template_read(tmp_path: Path) -> None:
    bundle_root, expected_glb = write_mesh_bundle_fixture(tmp_path)
    manifest_bytes = (bundle_root / "manifest.json").read_bytes()

    bundle = load_mesh_asset_bundle(bundle_root)

    assert type(bundle) is MeshAssetBundle
    assert canonical_mesh_asset_bundle_bytes(bundle) == manifest_bytes
    assert bundle.schema_version == MESH_ASSET_BUNDLE_SCHEMA
    assert bundle.coordinate_encoding == "three-east-up-negative-north"
    assert bundle.asset_ids == ("house_wood_01",)
    descriptor = bundle.records[0].lod["2"]
    assert descriptor.triangle_count == 3
    assert read_verified_mesh_template_glb(
        bundle_root,
        bundle=bundle,
        asset_id="house_wood_01",
        lod=2,
    ) == expected_glb
    assert hashlib.sha256(
        canonical_mesh_asset_bundle_bytes(bundle, exclude_bundle_id=True),
    ).hexdigest() == bundle.bundle_id


def test_bundle_dispatch_rejects_unknown_schema(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path)
    manifest_path = bundle_root / "manifest.json"
    payload = json.loads(manifest_path.read_bytes())
    payload["schema_version"] = "nantai.synthetic-village.mesh-asset-bundle.v3"
    manifest_path.write_bytes(_canonical(payload))

    with pytest.raises(MeshAssetBundleError, match="unsupported"):
        load_mesh_asset_bundle(bundle_root)


def test_v1_bundle_rejects_v2_only_fields(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path)
    manifest_path = bundle_root / "manifest.json"
    payload = json.loads(manifest_path.read_bytes())
    payload["source_v1_bundle_id"] = "1" * 64
    manifest_path.write_bytes(_canonical(payload))

    with pytest.raises(MeshAssetBundleError, match="manifest"):
        load_mesh_asset_bundle(bundle_root)


def test_bundle_rejects_tampered_template_bytes(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path)
    object_path = next((bundle_root / "objects").glob("*.glb"))
    object_path.write_bytes(object_path.read_bytes() + b"\0")

    with pytest.raises(MeshAssetBundleError, match="template"):
        load_mesh_asset_bundle(bundle_root)


def test_exact_read_rejects_template_changed_after_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path)
    bundle = load_mesh_asset_bundle(bundle_root)
    verification_calls = 0
    original_verify = mesh_asset_bundle._verify_mesh_asset_bundle

    def count_verification(root: Path):
        nonlocal verification_calls
        verification_calls += 1
        return original_verify(root)

    monkeypatch.setattr(
        mesh_asset_bundle,
        "_verify_mesh_asset_bundle",
        count_verification,
    )
    assert load_mesh_asset_bundle(bundle_root) == bundle
    assert verification_calls == 0
    object_path = next((bundle_root / "objects").glob("*.glb"))
    object_path.write_bytes(object_path.read_bytes() + b"\0")

    with pytest.raises(MeshAssetBundleError, match="template"):
        read_verified_mesh_template_glb(
            bundle_root,
            bundle=bundle,
            asset_id="house_wood_01",
            lod=2,
        )
    assert verification_calls == 1


def test_bundle_rejects_triangle_count_not_measured_from_glb(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(tmp_path, triangle_count=4)

    with pytest.raises(MeshAssetBundleError, match="triangle"):
        load_mesh_asset_bundle(bundle_root)


@pytest.mark.parametrize(
    ("asset_id", "kind", "limit"),
    [
        ("house_wood_01", "building", 720),
        ("tree_pine_01", "vegetation", 1200),
        ("stone_lamp_01", "prop", 600),
    ],
)
def test_bundle_rejects_kind_lod_triangle_budget_overrun(
    tmp_path: Path,
    asset_id: str,
    kind: str,
    limit: int,
) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(
        tmp_path,
        asset_id=asset_id,
        kind=kind,
        triangle_counts=(1, 2, limit + 1),
    )

    with pytest.raises(MeshAssetBundleError, match="manifest"):
        load_mesh_asset_bundle(bundle_root)


def test_bundle_rejects_non_increasing_lod_triangles(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(
        tmp_path,
        triangle_counts=(1, 1, 3),
    )

    with pytest.raises(MeshAssetBundleError, match="manifest"):
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


def test_bundle_measures_transformed_glb_bounds_in_enu(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(
        tmp_path,
        gltf_node_translation=(10.0, 3.0, -20.0),
        declared_aabb={
            "min": [10.0, 20.0, 3.0],
            "max": [11.0, 20.0, 4.0],
        },
    )

    bundle = load_mesh_asset_bundle(bundle_root)

    assert bundle.records[0].lod["2"].aabb.model_dump() == {
        "min": (10.0, 20.0, 3.0),
        "max": (11.0, 20.0, 4.0),
    }


def test_bundle_rejects_declared_bounds_not_measured_from_glb(
    tmp_path: Path,
) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(
        tmp_path,
        declared_aabb={
            "min": [0.0, 0.0, 0.0],
            "max": [2.0, 0.0, 1.0],
        },
    )

    with pytest.raises(MeshAssetBundleError, match="bounds"):
        load_mesh_asset_bundle(bundle_root)


def test_bundle_rejects_unsupported_coordinate_encoding(tmp_path: Path) -> None:
    bundle_root, _glb = write_mesh_bundle_fixture(
        tmp_path,
        coordinate_encoding="unknown-coordinate-encoding",
    )

    with pytest.raises(MeshAssetBundleError, match="manifest"):
        load_mesh_asset_bundle(bundle_root)


def test_measure_bounds_rejects_nonfinite_scene_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_scene = SimpleNamespace(
        geometry={"mesh": object()},
        bounds=((0.0, 0.0, 0.0), (float("nan"), 1.0, 1.0)),
    )
    monkeypatch.setattr(
        "pipeline.synthetic_village.mesh_asset_bundle.trimesh.load_scene",
        lambda *args, **kwargs: fake_scene,
    )

    with pytest.raises(MeshAssetBundleError, match="bounds"):
        measure_mesh_template_enu_bounds(_glb_payload())


@pytest.fixture(scope="module")
def material_bundle_fixture(
    tmp_path_factory: pytest.TempPathFactory,
):
    root = tmp_path_factory.mktemp("mesh-material-bundle")
    visual_root = write_material_visual_pack(root / "visual")
    return prepare_material_bundle(
        visual_pack_root=visual_root,
        staging_root=root / "material-bundle",
    )


def _write_mesh_template_source(
    root: Path,
    *,
    material_bundle,
) -> MeshAssetTemplateSource:
    fieldstone = next(
        record
        for record in material_bundle.manifest.records
        if record.slot_id == SLOT_ID
    )
    paths = []
    for level, triangle_count in enumerate((1, 2, 3)):
        payload = _glb_payload(
            triangle_count=triangle_count,
            source_sha256=fieldstone.source_sha256,
            material_bundle_id=material_bundle.manifest.bundle_id,
            material_algorithm_id=material_bundle.manifest.algorithm_id,
        )
        path = root / f"house-lod{level}.glb"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        paths.append(path)
    return MeshAssetTemplateSource(
        asset_id="house_wood_01",
        kind="building",
        footprint_m=(8.0, 6.0, 6.5),
        lod_paths=tuple(paths),
        material_slot_ids=((SLOT_ID,), (SLOT_ID,), (SLOT_ID,)),
    )


def _mesh_staging_directories(work_root: Path) -> list[Path]:
    return [
        path
        for path in work_root.glob(".mesh-*")
        if path.name != ".mesh-asset-bundle.lock"
    ]


def test_prepare_mesh_bundle_is_path_free_and_independently_verified(
    material_bundle_fixture,
    tmp_path: Path,
) -> None:
    source = _write_mesh_template_source(
        tmp_path / "builder-output",
        material_bundle=material_bundle_fixture,
    )

    prepared = prepare_mesh_asset_bundle(
        material_bundle_root=material_bundle_fixture.staging_root,
        sources=(source,),
        staging_root=tmp_path / "staging",
        build_tool_id="pytest-mesh-builder-v1",
    )

    assert load_mesh_asset_bundle(prepared.staging_root) == prepared.manifest
    assert prepared.manifest.material_bundle_id == (
        material_bundle_fixture.manifest.bundle_id
    )
    assert tuple(
        material.slot_id
        for material in prepared.manifest.material_registry
    ) == tuple(
        record.slot_id
        for record in material_bundle_fixture.manifest.records
    )
    manifest_bytes = canonical_mesh_asset_bundle_bytes(prepared.manifest)
    assert str(tmp_path).encode() not in manifest_bytes
    assert {path.name for path in (prepared.staging_root / "objects").iterdir()} == {
        f"{descriptor.glb_sha256}.glb"
        for descriptor in prepared.manifest.records[0].lod.values()
    }


def test_prepared_mesh_bundle_rejects_unexpected_object(
    material_bundle_fixture,
    tmp_path: Path,
) -> None:
    source = _write_mesh_template_source(
        tmp_path / "builder-output",
        material_bundle=material_bundle_fixture,
    )
    prepared = prepare_mesh_asset_bundle(
        material_bundle_root=material_bundle_fixture.staging_root,
        sources=(source,),
        staging_root=tmp_path / "staging",
        build_tool_id="pytest-mesh-builder-v1",
    )
    shutil.copyfile(
        source.lod_paths[0],
        prepared.staging_root / "objects/unexpected.glb",
    )

    with pytest.raises(MeshAssetBundleError, match="object set"):
        load_mesh_asset_bundle(prepared.staging_root)


def test_publish_mesh_bundle_is_absent_only_and_rejects_tampering(
    material_bundle_fixture,
    tmp_path: Path,
) -> None:
    source = _write_mesh_template_source(
        tmp_path / "builder-output",
        material_bundle=material_bundle_fixture,
    )
    publication_root = tmp_path / "published"
    work_root = tmp_path / "work"

    first = publish_mesh_asset_bundle(
        material_bundle_root=material_bundle_fixture.staging_root,
        sources=(source,),
        publication_root=publication_root,
        work_root=work_root,
        build_tool_id="pytest-mesh-builder-v1",
    )
    second = publish_mesh_asset_bundle(
        material_bundle_root=material_bundle_fixture.staging_root,
        sources=(source,),
        publication_root=publication_root,
        work_root=work_root,
        build_tool_id="pytest-mesh-builder-v1",
    )

    assert first.reused is False
    assert second.reused is True
    assert second.final_directory == first.final_directory
    assert not _mesh_staging_directories(work_root)

    descriptor = load_mesh_asset_bundle(first.final_directory).records[0].lod["2"]
    target = first.final_directory / descriptor.glb_object_path
    target.write_bytes(target.read_bytes() + b"\0")

    with pytest.raises(MeshAssetBundleError, match="template"):
        publish_mesh_asset_bundle(
            material_bundle_root=material_bundle_fixture.staging_root,
            sources=(source,),
            publication_root=publication_root,
            work_root=work_root,
            build_tool_id="pytest-mesh-builder-v1",
        )
    assert not _mesh_staging_directories(work_root)
