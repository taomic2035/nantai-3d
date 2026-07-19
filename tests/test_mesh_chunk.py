"""Deterministic textured mesh chunk manifests from the shared mock layout."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from pipeline.mock_layout import DEFAULT_ASSETS
from pipeline.synthetic_village.infinite_terrain import (
    TERRAIN_ALGORITHM_ID,
    TERRAIN_MATERIAL_SLOTS,
    terrain_height_m,
    terrain_macro_tint,
    terrain_material_slot,
)
from pipeline.synthetic_village.material_bundle import (
    MATERIAL_PARAMETERS,
    DerivedMaterialBundle,
    DerivedMaterialRecord,
    MaterialMapDescriptor,
)
from pipeline.synthetic_village.mesh_asset_build import (
    ASSET_RECIPE_CONTRACTS,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    MESH_ASSET_BUNDLE_SCHEMA,
    MeshAssetBundle,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    MESH_ASSET_BUNDLE_V2_SCHEMA,
    MeshAssetBundleV2,
)
from pipeline.synthetic_village.mesh_chunk import (
    MeshChunkError,
    MeshChunkManifest,
    MeshChunkRuntimeManifest,
    MeshChunkRuntimeManifestV2,
    build_mesh_chunk_manifest,
    canonical_mesh_chunk_bytes,
    canonical_mesh_chunk_runtime_bytes,
    project_mesh_chunk_runtime,
)

MATERIAL_SLOTS = (
    "material-fieldstone-01",
    "material-moss-stone-01",
    "material-packed-earth-01",
    "material-shallow-water-01",
    "material-terrace-soil-01",
    "material-wet-stone-paving-01",
)
FOOTPRINTS = {
    "house_wood_01": (8.0, 6.0, 6.5),
    "house_wood_02": (10.0, 7.0, 7.0),
    "house_stone_01": (9.0, 7.0, 6.5),
    "house_thatch_01": (7.0, 6.0, 6.0),
    "house_barn_01": (12.0, 8.0, 8.0),
    "tree_pine_01": (4.0, 4.0, 9.0),
    "tree_broadleaf_01": (7.0, 7.0, 8.0),
    "tree_bamboo_01": (3.0, 3.0, 10.0),
    "stone_wall_01": (4.0, 0.5, 1.2),
    "stone_lamp_01": (0.8, 0.8, 2.0),
    "fence_wood_01": (3.0, 0.2, 1.1),
}


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _asset_kind(asset_id: str) -> str:
    if asset_id in DEFAULT_ASSETS["houses"]:
        return "building"
    if asset_id in DEFAULT_ASSETS["trees"]:
        return "vegetation"
    return "prop"


def _bundle(
    *,
    material_bundle_id: str = "2" * 64,
    material_bundle_manifest_sha256: str = "3" * 64,
    material_algorithm_id: str = "mirror-sobel-orm-v1",
    asset_ids: tuple[str, ...] | None = None,
    lod_templates: tuple[
        tuple[str, int, int],
        tuple[str, int, int],
        tuple[str, int, int],
    ] | None = None,
    descriptor_aabb: dict[str, list[float]] | None = None,
) -> MeshAssetBundle:
    asset_ids = asset_ids or tuple(sorted(FOOTPRINTS))
    lod_templates = lod_templates or (
        ("a" * 64, 1024, 12),
        ("b" * 64, 2048, 24),
        ("c" * 64, 4096, 48),
    )
    material_registry = [
        {
            "slot_id": slot_id,
            "source_sha256": (
                "1" * 64
                if slot_id == "material-fieldstone-01"
                else f"{index:064x}"
            ),
            "bundle_id": material_bundle_id,
            "algorithm_id": material_algorithm_id,
        }
        for index, slot_id in enumerate(MATERIAL_SLOTS, start=1)
    ]
    records = []
    for asset_id in sorted(asset_ids):
        width, depth, height = FOOTPRINTS[asset_id]
        lod = {}
        for level, (glb_sha256, glb_bytes, triangle_count) in enumerate(
            lod_templates,
        ):
            lod[str(level)] = {
                "glb_object_path": f"objects/{glb_sha256}.glb",
                "glb_sha256": glb_sha256,
                "glb_bytes": glb_bytes,
                "triangle_count": triangle_count,
                "primitive_count": 1,
                "material_slot_ids": ["material-fieldstone-01"],
                "aabb": descriptor_aabb or {
                    "min": [-width / 2, -depth / 2, 0.0],
                    "max": [width / 2, depth / 2, height],
                },
            }
        records.append(
            {
                "asset_id": asset_id,
                "kind": _asset_kind(asset_id),
                "mesh_algorithm_id": "synthetic-template-mesh-v1",
                "footprint_m": [width, depth, height],
                "lod": lod,
                "synthetic": True,
                "geometry_usability": "preview-only",
            },
        )
    payload = {
        "schema_version": MESH_ASSET_BUNDLE_SCHEMA,
        "bundle_id": "0" * 64,
        "coordinate_encoding": "three-east-up-negative-north",
        "material_bundle_id": material_bundle_id,
        "material_bundle_manifest_sha256": material_bundle_manifest_sha256,
        "synthetic": True,
        "real_photo_textures": False,
        "build_tool_id": "pytest-mesh-template-v1",
        "verification_level": "L0",
        "material_registry": material_registry,
        "records": records,
    }
    identity = dict(payload)
    identity.pop("bundle_id")
    payload["bundle_id"] = hashlib.sha256(_canonical(identity)).hexdigest()
    return MeshAssetBundle.model_validate_json(_canonical(payload))


def _surface_material_bundle(bundle: MeshAssetBundle) -> DerivedMaterialBundle:
    sources = {row.slot_id: row.source_sha256 for row in bundle.material_registry}
    records = []
    for slot_id in (
        "material-moss-stone-01",
        "material-packed-earth-01",
        "material-shallow-water-01",
        "material-terrace-soil-01",
        "material-wet-stone-paving-01",
    ):
        parameters = MATERIAL_PARAMETERS[slot_id]
        descriptors = {}
        for role, color_space in (
            ("base_color", "srgb"),
            ("normal", "non-color"),
            ("orm", "non-color"),
        ):
            digest = hashlib.sha256(f"{slot_id}:{role}".encode()).hexdigest()
            descriptors[role] = MaterialMapDescriptor(
                object_path=f"objects/{digest}.png",
                sha256=digest,
                bytes=1024,
                color_space=color_space,
            )
        records.append(
            DerivedMaterialRecord(
                slot_id=slot_id,
                source_sha256=sources[slot_id],
                source_width=12,
                source_height=8,
                uv_policy=parameters.uv_policy,
                nominal_tile_m=parameters.nominal_tile_m,
                normal_strength=parameters.normal_strength,
                roughness_center=parameters.roughness_center,
                metallic=parameters.metallic,
                replacement_contract_sha256="9" * 64,
                **descriptors,
            ),
        )
    return DerivedMaterialBundle.model_construct(
        bundle_id=bundle.material_bundle_id,
        records=tuple(records),
    )


def _bundle_v2() -> MeshAssetBundleV2:
    source = _bundle()
    texture_bindings = []
    texture_objects = []
    for role, colour_space in (
        ("base_color", "srgb"),
        ("normal", "non-color"),
        ("orm", "non-color"),
    ):
        digest = hashlib.sha256(f"mesh-v2:{role}".encode()).hexdigest()
        texture_bindings.append(
            {
                "uri": f"../textures/{digest}.png",
                "sha256": digest,
                "role": role,
                "colour_space": colour_space,
                "material_slot_id": "material-fieldstone-01",
                "derivation_algorithm_id": "mirror-sobel-orm-v1",
                "min_filter": 9987,
                "mag_filter": 9729,
                "wrap_s": 10497,
                "wrap_t": 10497,
            },
        )
        texture_objects.append(
            {
                "object_path": f"textures/{digest}.png",
                "sha256": digest,
                "bytes": 1024,
                "mime_type": "image/png",
                "width": 1024,
                "height": 1024,
            },
        )
    records = []
    for source_record in source.records:
        _, recipe_id, _ = ASSET_RECIPE_CONTRACTS[
            source_record.asset_id
        ]
        lod = {}
        for level in ("0", "1", "2"):
            descriptor = source_record.lod[level].model_dump(mode="json")
            is_near = level == "2"
            descriptor.update(
                {
                    "triangle_count": (
                        {
                            "building": 8_000,
                            "vegetation": 6_000,
                            "prop": 1_000,
                        }[source_record.kind]
                        if is_near
                        else descriptor["triangle_count"]
                    ),
                    "mesh_algorithm_id": (
                        "synthetic-template-mesh-near-v2"
                        if is_near
                        else "synthetic-template-mesh-v1"
                    ),
                    "recipe_id": (
                        recipe_id.removesuffix("-v1") + "-near-v2"
                        if is_near
                        else recipe_id
                    ),
                    "texture_storage": (
                        "shared-content-addressed"
                        if is_near
                        else "embedded"
                    ),
                    "texture_bindings": (
                        texture_bindings if is_near else []
                    ),
                },
            )
            lod[level] = descriptor
        records.append(
            {
                "asset_id": source_record.asset_id,
                "kind": source_record.kind,
                "footprint_m": source_record.footprint_m,
                "lod": lod,
                "synthetic": True,
                "geometry_usability": "preview-only",
            },
        )
    unsigned = {
        "schema_version": MESH_ASSET_BUNDLE_V2_SCHEMA,
        "coordinate_encoding": "three-east-up-negative-north",
        "source_v1_bundle_id": source.bundle_id,
        "material_bundle_id": source.material_bundle_id,
        "material_bundle_manifest_sha256": (
            source.material_bundle_manifest_sha256
        ),
        "synthetic": True,
        "real_photo_textures": False,
        "build_tool_id": "pytest-mesh-template-v2",
        "verification_level": "L0",
        "texture_audit_profile": "verified-relative-content-addressed",
        "material_registry": [
            row.model_dump(mode="json")
            for row in source.material_registry
        ],
        "texture_objects": sorted(
            texture_objects,
            key=lambda row: row["object_path"],
        ),
        "records": records,
    }
    return MeshAssetBundleV2.model_validate_json(
        _canonical(
            {
                "bundle_id": hashlib.sha256(
                    _canonical(unsigned),
                ).hexdigest(),
                **unsigned,
            },
        ),
    )


def test_negative_chunk_is_deterministic_and_path_free() -> None:
    bundle = _bundle()

    first = build_mesh_chunk_manifest(-2, 3, world_seed=42, bundle=bundle, lod=1)
    second = build_mesh_chunk_manifest(-2, 3, world_seed=42, bundle=bundle, lod=1)

    assert canonical_mesh_chunk_bytes(first) == canonical_mesh_chunk_bytes(second)
    assert first.chunk_id.model_dump() == {"x": -2, "y": 3}
    assert first.world_offset == (-400.0, 600.0, 0.0)
    assert first.synthetic is True
    assert first.geometry_usability == "preview-only"
    assert first.coordinate_confidence == "synthetic-layout"
    assert first.metric_alignment is False
    assert first.real_photo_textures is False
    assert b"/api/" not in canonical_mesh_chunk_bytes(first)
    assert {item.asset_id for item in first.instances} <= set(bundle.asset_ids)
    assert len(first.content_key) == 64


def test_instances_are_stably_sorted_unique_and_kind_checked() -> None:
    manifest = build_mesh_chunk_manifest(
        0,
        0,
        world_seed=42,
        bundle=_bundle(),
        lod=2,
    )

    instance_ids = tuple(item.instance_id for item in manifest.instances)
    assert instance_ids == tuple(sorted(instance_ids))
    assert len(instance_ids) == len(set(instance_ids))
    expected_kinds = {
        record.asset_id: record.kind for record in _bundle().records
    }
    assert all(
        item.kind == expected_kinds[item.asset_id]
        for item in manifest.instances
    )


def test_adjacent_chunks_share_exact_world_anchored_terrain_edge() -> None:
    bundle = _bundle()
    west = build_mesh_chunk_manifest(0, 0, world_seed=42, bundle=bundle, lod=2)
    east = build_mesh_chunk_manifest(1, 0, world_seed=42, bundle=bundle, lod=2)
    resolution = west.terrain.resolution
    west_edge = west.terrain.vertices[resolution - 1 :: resolution]
    east_edge = east.terrain.vertices[0::resolution]

    assert tuple((row.world_u, row.world_v, row.z) for row in west_edge) == tuple(
        (row.world_u, row.world_v, row.z) for row in east_edge
    )
    assert {row.world_u for row in west_edge} == {200.0}
    assert len({row.z for row in west.terrain.vertices}) > 1


def test_relief_is_shared_by_terrain_ribbons_instances_and_bounds() -> None:
    bundle = _bundle()
    manifests = [
        build_mesh_chunk_manifest(1, -1, world_seed=42, bundle=bundle, lod=lod)
        for lod in (0, 1, 2)
    ]

    assert {manifest.terrain.resolution for manifest in manifests} == {41}
    manifest = manifests[-1]
    assert manifest.terrain_algorithm_id == TERRAIN_ALGORITHM_ID
    assert manifest.terrain.algorithm_id == TERRAIN_ALGORITHM_ID

    for vertex in manifest.terrain.vertices:
        assert vertex.z == terrain_height_m(
            vertex.world_u,
            vertex.world_v,
            world_seed=manifest.world_seed,
        )
        assert vertex.macro_tint == terrain_macro_tint(
            vertex.world_u,
            vertex.world_v,
            world_seed=manifest.world_seed,
        )

    world_x, world_y, _ = manifest.world_offset
    for ribbon in (*manifest.roads, *manifest.water):
        for local_x, local_y, local_z in ribbon.points:
            assert local_z == pytest.approx(
                terrain_height_m(
                    world_x + local_x,
                    world_y + local_y,
                    world_seed=manifest.world_seed,
                )
                + ribbon.z_offset,
            )
    for instance in manifest.instances:
        local_x, local_y, local_z = instance.local_position
        assert local_z == terrain_height_m(
            world_x + local_x,
            world_y + local_y,
            world_seed=manifest.world_seed,
        )

    terrain_z = [vertex.z for vertex in manifest.terrain.vertices]
    assert manifest.aabb.min[2] <= min(terrain_z)
    assert manifest.aabb.max[2] >= max(terrain_z)


def test_terrain_cells_bind_verified_material_zones_in_world_space() -> None:
    bundle = _bundle()
    west = build_mesh_chunk_manifest(0, 0, world_seed=42, bundle=bundle, lod=2)
    east = build_mesh_chunk_manifest(1, 0, world_seed=42, bundle=bundle, lod=2)

    assert len(west.terrain.material_slot_ids) == 1600
    assert set(west.terrain.material_slot_ids) == set(TERRAIN_MATERIAL_SLOTS)
    for manifest in (west, east):
        resolution = manifest.terrain.resolution
        step = manifest.chunk_size_m / (resolution - 1)
        expected = tuple(
            terrain_material_slot(
                manifest.world_offset[0] + (column + 0.5) * step,
                manifest.world_offset[1] + (row + 0.5) * step,
                world_seed=manifest.world_seed,
            )
            for row in range(resolution - 1)
            for column in range(resolution - 1)
        )
        assert manifest.terrain.material_slot_ids == expected

    runtime = project_mesh_chunk_runtime(
        west,
        bundle=bundle,
        material_bundle=_surface_material_bundle(bundle),
    )
    assert {
        material.slot_id for material in runtime.surface_materials
    } >= set(TERRAIN_MATERIAL_SLOTS)


def test_bundle_or_material_replacement_changes_chunk_identity() -> None:
    first = build_mesh_chunk_manifest(
        1,
        -1,
        world_seed=42,
        bundle=_bundle(material_bundle_id="2" * 64),
        lod=1,
    )
    replacement = build_mesh_chunk_manifest(
        1,
        -1,
        world_seed=42,
        bundle=_bundle(material_bundle_id="4" * 64),
        lod=1,
    )

    assert replacement.mesh_asset_bundle_id != first.mesh_asset_bundle_id
    assert replacement.material_bundle_id != first.material_bundle_id
    assert replacement.content_key != first.content_key


def test_runtime_projection_uses_only_exact_same_origin_asset_routes() -> None:
    bundle = _bundle()
    chunk = build_mesh_chunk_manifest(
        -1,
        2,
        world_seed=42,
        bundle=bundle,
        lod=0,
    )

    runtime = project_mesh_chunk_runtime(
        chunk,
        bundle=bundle,
        material_bundle=_surface_material_bundle(bundle),
    )

    assert runtime.chunk is chunk
    assert runtime.asset_urls
    assert tuple(row.asset_id for row in runtime.asset_urls) == tuple(
        sorted({instance.asset_id for instance in chunk.instances})
    )
    assert all(
        row.url
        == (
            f"/api/world/mesh-assets/{bundle.bundle_id}/"
            f"{row.asset_id}/lod0.glb"
        )
        for row in runtime.asset_urls
    )
    expected_slots = tuple(sorted({
        chunk.terrain.material_slot_id,
        *chunk.terrain.material_slot_ids,
        *(ribbon.material_slot_id for ribbon in chunk.roads),
        *(ribbon.material_slot_id for ribbon in chunk.water),
    }))
    assert tuple(row.slot_id for row in runtime.surface_materials) == expected_slots
    for material in runtime.surface_materials:
        assert material.nominal_tile_m == MATERIAL_PARAMETERS[
            material.slot_id
        ].nominal_tile_m
        for role in ("base_color", "normal", "orm"):
            descriptor = getattr(material, role)
            assert descriptor.url == (
                f"/api/world/material-maps/{bundle.material_bundle_id}/"
                f"{material.slot_id}/{role}.png"
            )


def test_v1_runtime_bytes_remain_exact() -> None:
    bundle = _bundle()
    chunk = build_mesh_chunk_manifest(
        0,
        0,
        world_seed=42,
        bundle=bundle,
        lod=2,
    )
    runtime = project_mesh_chunk_runtime(
        chunk,
        bundle=bundle,
        material_bundle=_surface_material_bundle(bundle),
    )

    raw = canonical_mesh_chunk_runtime_bytes(runtime)
    reloaded = MeshChunkRuntimeManifest.model_validate_json(raw)

    assert type(runtime) is MeshChunkRuntimeManifest
    assert canonical_mesh_chunk_runtime_bytes(reloaded) == raw


@pytest.mark.parametrize("lod", (0, 1, 2))
def test_v2_runtime_projects_exact_lod_texture_closure(lod: int) -> None:
    bundle = _bundle_v2()
    chunk = build_mesh_chunk_manifest(
        0,
        0,
        world_seed=42,
        bundle=bundle,
        lod=lod,
    )
    runtime = project_mesh_chunk_runtime(
        chunk,
        bundle=bundle,
        material_bundle=_surface_material_bundle(bundle),
    )

    assert type(runtime) is MeshChunkRuntimeManifestV2
    assert runtime.schema_version == (
        "nantai.synthetic-village.mesh-chunk-runtime.v2"
    )
    objects = {row.sha256: row for row in bundle.texture_objects}
    records = {row.asset_id: row for row in bundle.records}
    for asset in runtime.asset_urls:
        descriptor = records[asset.asset_id].lod[str(lod)]
        expected = tuple(
            (
                binding.sha256,
                binding.role,
                binding.material_slot_id,
                objects[binding.sha256].bytes,
            )
            for binding in sorted(
                descriptor.texture_bindings,
                key=lambda row: (
                    row.sha256,
                    row.role,
                    row.material_slot_id,
                ),
            )
        )
        assert tuple(
            (
                row.sha256,
                row.role,
                row.material_slot_id,
                row.bytes,
            )
            for row in asset.texture_dependencies
        ) == expected
        assert all(
            row.url
            == (
                f"/api/world/mesh-assets/{bundle.bundle_id}/"
                f"textures/{row.sha256}.png"
            )
            for row in asset.texture_dependencies
        )
        assert bool(asset.texture_dependencies) is (lod == 2)
    raw = canonical_mesh_chunk_runtime_bytes(runtime)
    reloaded = MeshChunkRuntimeManifestV2.model_validate_json(raw)
    assert canonical_mesh_chunk_runtime_bytes(reloaded) == raw


def test_v2_runtime_rejects_mutated_dependency_closures() -> None:
    bundle = _bundle_v2()
    chunk = build_mesh_chunk_manifest(
        0,
        0,
        world_seed=42,
        bundle=bundle,
        lod=2,
    )
    runtime = project_mesh_chunk_runtime(
        chunk,
        bundle=bundle,
        material_bundle=_surface_material_bundle(bundle),
    )
    payload = runtime.model_dump(mode="json")
    payload["asset_urls"][0]["texture_dependencies"].reverse()

    with pytest.raises(ValidationError, match="sorted"):
        MeshChunkRuntimeManifestV2.model_validate_json(
            _canonical(payload),
        )

    payload = runtime.model_dump(mode="json")
    payload["asset_urls"][0]["texture_dependencies"] = []
    with pytest.raises(ValidationError, match="LOD2"):
        MeshChunkRuntimeManifestV2.model_validate_json(
            _canonical(payload),
        )

    payload = runtime.model_dump(mode="json")
    payload["asset_urls"][0]["texture_dependencies"][0]["url"] = (
        "/api/world/mesh-assets/"
        + "f" * 64
        + "/textures/"
        + payload["asset_urls"][0]["texture_dependencies"][0]["sha256"]
        + ".png"
    )
    with pytest.raises(ValidationError, match="bundle"):
        MeshChunkRuntimeManifestV2.model_validate_json(
            _canonical(payload),
        )

    payload = runtime.model_dump(mode="json")
    del payload["asset_urls"][0]["texture_dependencies"][0]
    with pytest.raises(ValidationError, match="roles"):
        MeshChunkRuntimeManifestV2.model_validate_json(
            _canonical(payload),
        )

    payload = runtime.model_dump(mode="json")
    duplicate = payload["asset_urls"][0]["texture_dependencies"][0]
    payload["asset_urls"][0]["texture_dependencies"].append(duplicate)
    payload["asset_urls"][0]["texture_dependencies"].sort(
        key=lambda row: (
            row["sha256"],
            row["role"],
            row["material_slot_id"],
        ),
    )
    with pytest.raises(ValidationError, match="duplicate"):
        MeshChunkRuntimeManifestV2.model_validate_json(
            _canonical(payload),
        )

    payload = runtime.model_dump(mode="json")
    base_color = next(
        row
        for row in payload["asset_urls"][0]["texture_dependencies"]
        if row["role"] == "base_color"
    )
    base_color["colour_space"] = "non-color"
    with pytest.raises(ValidationError, match="colour space"):
        MeshChunkRuntimeManifestV2.model_validate_json(
            _canonical(payload),
        )

    with pytest.raises(ValidationError):
        MeshChunkRuntimeManifest.model_validate_json(
            canonical_mesh_chunk_runtime_bytes(runtime),
        )

    payload = runtime.model_dump(mode="json")
    payload["surface_materials"].reverse()
    with pytest.raises(ValidationError, match="surface closure"):
        MeshChunkRuntimeManifestV2.model_validate_json(
            _canonical(payload),
        )


def test_v2_runtime_rejects_missing_texture_object_evidence() -> None:
    bundle = _bundle_v2()
    chunk = build_mesh_chunk_manifest(
        0,
        0,
        world_seed=42,
        bundle=bundle,
        lod=2,
    )
    incomplete = bundle.model_copy(
        update={"texture_objects": bundle.texture_objects[:-1]},
    )

    with pytest.raises(MeshChunkError, match="texture object"):
        project_mesh_chunk_runtime(
            chunk,
            bundle=incomplete,
            material_bundle=_surface_material_bundle(bundle),
        )


def test_layout_asset_missing_from_bundle_fails_closed() -> None:
    partial = _bundle(asset_ids=("house_wood_01",))

    with pytest.raises(MeshChunkError, match="asset"):
        build_mesh_chunk_manifest(
            0,
            0,
            world_seed=42,
            bundle=partial,
            lod=2,
        )


@pytest.mark.parametrize(
    ("chunk_x", "chunk_y", "world_seed", "lod"),
    [
        (True, 0, 42, 2),
        (0.5, 0, 42, 2),
        (0, "1", 42, 2),
        (0, 0, True, 2),
        (0, 0, 42, 3),
        (2**53, 0, 42, 2),
    ],
)
def test_scheduler_inputs_must_be_safe_integers(
    chunk_x: object,
    chunk_y: object,
    world_seed: object,
    lod: object,
) -> None:
    with pytest.raises((MeshChunkError, ValidationError)):
        build_mesh_chunk_manifest(
            chunk_x,  # type: ignore[arg-type]
            chunk_y,  # type: ignore[arg-type]
            world_seed=world_seed,  # type: ignore[arg-type]
            bundle=_bundle(),
            lod=lod,  # type: ignore[arg-type]
        )


def test_truth_fields_reject_manifest_upgrade() -> None:
    manifest = build_mesh_chunk_manifest(
        0,
        0,
        world_seed=42,
        bundle=_bundle(),
        lod=2,
    )
    payload = manifest.model_dump(mode="json")
    payload["geometry_usability"] = "metric-aligned"

    with pytest.raises(ValidationError):
        MeshChunkManifest.model_validate(payload)
