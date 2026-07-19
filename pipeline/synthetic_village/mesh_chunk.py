"""Deterministic, path-free textured mesh chunk manifests."""

from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from pipeline.mock_layout import MockLayoutGenerator
from pipeline.synthetic_village.infinite_terrain import (
    TERRAIN_ALGORITHM_ID,
    TERRAIN_MATERIAL_SLOTS,
    terrain_height_m,
    terrain_macro_tint,
    terrain_material_slot,
)
from pipeline.synthetic_village.material_bundle import (
    DerivedMaterialBundle,
    DerivedMaterialRecord,
    UvPolicy,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    Bounds3,
    MeshAssetBundle,
    MeshAssetBundleAny,
    MeshAssetRecord,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    MeshAssetBundleV2,
    MeshAssetRecordV2,
    MeshTemplateLodV2,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MeshAssetRecordAny = MeshAssetRecord | MeshAssetRecordV2

MESH_CHUNK_SCHEMA = "nantai.synthetic-village.mesh-chunk.v1"
MESH_CHUNK_RUNTIME_SCHEMA = "nantai.synthetic-village.mesh-chunk-runtime.v1"
MESH_CHUNK_RUNTIME_V2_SCHEMA = (
    "nantai.synthetic-village.mesh-chunk-runtime.v2"
)
LAYOUT_ALGORITHM_ID = "mock-layout-v1"
RENDERER_CAPABILITY = "synthetic-textured-mesh-grid"
CHUNK_SIZE_M = 200
MAX_SAFE_INTEGER = 2**53 - 1
TERRAIN_RESOLUTION = {0: 41, 1: 41, 2: 41}
VEGETATION_LIMIT = {0: 2, 1: 5, 2: 12}

TERRAIN_MATERIAL_SLOT = "material-terrace-soil-01"
ROAD_MATERIAL_SLOTS = {
    "main": "material-wet-stone-paving-01",
    "trail": "material-packed-earth-01",
    "path": "material-packed-earth-01",
}
WATER_MATERIAL_SLOT = "material-shallow-water-01"


class MeshChunkError(ValueError):
    """A mesh chunk cannot be derived from the declared evidence."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MeshChunkId(FrozenModel):
    x: int
    y: int


class TerrainVertex(FrozenModel):
    x: float
    y: float
    z: float
    world_u: float
    world_v: float
    macro_tint: float = Field(ge=0.9, le=1.1, allow_inf_nan=False)


class TerrainGrid(FrozenModel):
    algorithm_id: Literal[
        "synthetic-multiscale-relief-slope-macro-patch-v2"
    ] = TERRAIN_ALGORITHM_ID
    resolution: Literal[41]
    material_slot_id: Literal[
        "material-terrace-soil-01"
    ] = TERRAIN_MATERIAL_SLOT
    material_slot_ids: tuple[
        Literal[
            "material-moss-stone-01",
            "material-packed-earth-01",
            "material-terrace-soil-01",
        ],
        ...,
    ] = Field(min_length=1600, max_length=1600)
    vertices: tuple[TerrainVertex, ...] = Field(
        min_length=1681,
        max_length=1681,
    )

    @model_validator(mode="after")
    def _complete_grid(self) -> TerrainGrid:
        if len(self.vertices) != self.resolution**2:
            raise ValueError("terrain grid vertex count does not match its resolution")
        if len(self.material_slot_ids) != (self.resolution - 1) ** 2:
            raise ValueError(
                "terrain material count does not match its cell count",
            )
        if not set(self.material_slot_ids) <= set(TERRAIN_MATERIAL_SLOTS):
            raise ValueError("terrain material is outside the approved profile")
        return self


class Ribbon(FrozenModel):
    ribbon_id: str = Field(min_length=1)
    kind: Literal["road", "water"]
    feature_type: str = Field(min_length=1)
    width: float = Field(gt=0, allow_inf_nan=False)
    z_offset: float = Field(ge=0, allow_inf_nan=False)
    material_slot_id: str = Field(
        pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    points: tuple[tuple[float, float, float], ...] = Field(min_length=2)


class MeshInstance(FrozenModel):
    instance_id: str = Field(min_length=1)
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: Literal["building", "vegetation", "prop"]
    local_position: tuple[float, float, float]
    rotation_z_degrees: float = Field(ge=0, lt=360, allow_inf_nan=False)
    scale: float = Field(gt=0, le=3, allow_inf_nan=False)
    template_lod: Literal[0, 1, 2]


class MeshChunkManifest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-chunk.v1"
    ] = MESH_CHUNK_SCHEMA
    content_key: Sha256
    renderer_capability: Literal[
        "synthetic-textured-mesh-grid"
    ] = RENDERER_CAPABILITY
    world_seed: int
    chunk_id: MeshChunkId
    chunk_size_m: Literal[200] = CHUNK_SIZE_M
    world_offset: tuple[float, float, float]
    layout_algorithm_id: Literal["mock-layout-v1"] = LAYOUT_ALGORITHM_ID
    layout_sha256: Sha256
    terrain_algorithm_id: Literal[
        "synthetic-multiscale-relief-slope-macro-patch-v2"
    ] = TERRAIN_ALGORITHM_ID
    mesh_asset_bundle_id: Sha256
    material_bundle_id: Sha256
    selected_lod: Literal[0, 1, 2]
    terrain: TerrainGrid
    roads: tuple[Ribbon, ...]
    water: tuple[Ribbon, ...]
    instances: tuple[MeshInstance, ...]
    aabb: Bounds3
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"
    coordinate_confidence: Literal["synthetic-layout"] = "synthetic-layout"
    metric_alignment: Literal[False] = False
    real_photo_textures: Literal[False] = False

    @model_validator(mode="after")
    def _stable_complete_manifest(self) -> MeshChunkManifest:
        _require_safe_int(self.world_seed, label="world seed")
        _require_safe_int(self.chunk_id.x, label="chunk X")
        _require_safe_int(self.chunk_id.y, label="chunk Y")
        instance_ids = tuple(row.instance_id for row in self.instances)
        if (
            instance_ids != tuple(sorted(instance_ids))
            or len(instance_ids) != len(set(instance_ids))
        ):
            raise ValueError("mesh chunk instance IDs must be sorted and unique")
        expected = hashlib.sha256(
            canonical_mesh_chunk_bytes(self, exclude_content_key=True),
        ).hexdigest()
        if self.content_key != expected:
            raise ValueError("mesh chunk content key does not match canonical content")
        return self


class MeshAssetRuntimeUrl(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    lod: Literal[0, 1, 2]
    url: str = Field(min_length=1)
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1)

    @model_validator(mode="after")
    def _exact_same_origin_route(self) -> MeshAssetRuntimeUrl:
        suffix = f"/{self.asset_id}/lod{self.lod}.glb"
        if (
            not self.url.startswith("/api/world/mesh-assets/")
            or not self.url.endswith(suffix)
            or "\\" in self.url
            or "?" in self.url
            or "#" in self.url
        ):
            raise ValueError("mesh asset runtime URL is not an exact same-origin route")
        return self


class MeshTextureRuntimeUrl(FrozenModel):
    url: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1)
    role: Literal["base_color", "normal", "orm"]
    colour_space: Literal["srgb", "non-color"]
    material_slot_id: str = Field(
        pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    derivation_algorithm_id: str = Field(min_length=1)
    min_filter: Literal[9987] = 9987
    mag_filter: Literal[9729] = 9729
    wrap_s: Literal[10497] = 10497
    wrap_t: Literal[10497] = 10497

    @model_validator(mode="after")
    def _exact_texture_route_and_semantics(
        self,
    ) -> MeshTextureRuntimeUrl:
        parts = self.url.split("/")
        if (
            len(parts) != 7
            or parts[:4] != ["", "api", "world", "mesh-assets"]
            or len(parts[4]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in parts[4]
            )
            or parts[5] != "textures"
            or parts[6] != f"{self.sha256}.png"
            or "\\" in self.url
            or "?" in self.url
            or "#" in self.url
        ):
            raise ValueError(
                "mesh texture runtime URL is not an exact same-origin route",
            )
        expected_colour_space = (
            "srgb" if self.role == "base_color" else "non-color"
        )
        if self.colour_space != expected_colour_space:
            raise ValueError(
                "mesh texture runtime colour space is invalid",
            )
        return self


class MeshAssetRuntimeUrlV2(MeshAssetRuntimeUrl):
    texture_dependencies: tuple[MeshTextureRuntimeUrl, ...]

    @model_validator(mode="after")
    def _exact_dependency_closure(self) -> MeshAssetRuntimeUrlV2:
        order = tuple(
            (
                row.sha256,
                row.role,
                row.material_slot_id,
            )
            for row in self.texture_dependencies
        )
        semantic_keys = tuple(
            (row.material_slot_id, row.role)
            for row in self.texture_dependencies
        )
        if order != tuple(sorted(order)):
            raise ValueError(
                "mesh texture dependencies must be sorted",
            )
        if len(set(semantic_keys)) != len(semantic_keys):
            raise ValueError(
                "mesh texture dependencies contain duplicate semantics",
            )
        if self.lod in {0, 1} and self.texture_dependencies:
            raise ValueError(
                "embedded mesh LOD cannot declare texture dependencies",
            )
        if self.lod == 2:
            if not self.texture_dependencies:
                raise ValueError(
                    "mesh LOD2 requires texture dependencies",
                )
            slots = {
                row.material_slot_id
                for row in self.texture_dependencies
            }
            if any(
                {
                    row.role
                    for row in self.texture_dependencies
                    if row.material_slot_id == slot_id
                }
                != {"base_color", "normal", "orm"}
                for slot_id in slots
            ):
                raise ValueError(
                    "mesh LOD2 texture dependency roles are incomplete",
                )
        return self


class MaterialMapRuntimeUrl(FrozenModel):
    role: Literal["base_color", "normal", "orm"]
    url: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1)
    color_space: Literal["srgb", "non-color"]

    @model_validator(mode="after")
    def _exact_same_origin_route(self) -> MaterialMapRuntimeUrl:
        suffix = f"/{self.role}.png"
        if (
            not self.url.startswith("/api/world/material-maps/")
            or not self.url.endswith(suffix)
            or "\\" in self.url
            or "?" in self.url
            or "#" in self.url
        ):
            raise ValueError("material map runtime URL is not an exact same-origin route")
        if (
            (self.role == "base_color" and self.color_space != "srgb")
            or (self.role != "base_color" and self.color_space != "non-color")
        ):
            raise ValueError("material map runtime color space is invalid")
        return self


class SurfaceMaterialRuntime(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    uv_policy: UvPolicy
    nominal_tile_m: float = Field(gt=0, allow_inf_nan=False)
    normal_strength: float = Field(gt=0, allow_inf_nan=False)
    roughness_center: float = Field(ge=0, le=1, allow_inf_nan=False)
    metallic: float = Field(ge=0, le=1, allow_inf_nan=False)
    base_color: MaterialMapRuntimeUrl
    normal: MaterialMapRuntimeUrl
    orm: MaterialMapRuntimeUrl

    @model_validator(mode="after")
    def _exact_map_roles(self) -> SurfaceMaterialRuntime:
        if (
            self.base_color.role != "base_color"
            or self.normal.role != "normal"
            or self.orm.role != "orm"
        ):
            raise ValueError("surface material runtime map roles are invalid")
        return self


class MeshChunkRuntimeManifest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-chunk-runtime.v1"
    ] = MESH_CHUNK_RUNTIME_SCHEMA
    chunk: MeshChunkManifest
    asset_urls: tuple[MeshAssetRuntimeUrl, ...]
    surface_materials: tuple[SurfaceMaterialRuntime, ...]


class MeshChunkRuntimeManifestV2(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-chunk-runtime.v2"
    ] = MESH_CHUNK_RUNTIME_V2_SCHEMA
    chunk: MeshChunkManifest
    asset_urls: tuple[MeshAssetRuntimeUrlV2, ...]
    surface_materials: tuple[SurfaceMaterialRuntime, ...]

    @model_validator(mode="after")
    def _version_paired_runtime(self) -> MeshChunkRuntimeManifestV2:
        asset_ids = tuple(row.asset_id for row in self.asset_urls)
        required_ids = tuple(sorted({
            instance.asset_id
            for instance in self.chunk.instances
        }))
        if (
            asset_ids != required_ids
            or len(set(asset_ids)) != len(asset_ids)
        ):
            raise ValueError(
                "mesh runtime v2 asset closure is incomplete or unsorted",
            )
        bundle_id = self.chunk.mesh_asset_bundle_id
        for asset in self.asset_urls:
            if (
                asset.lod != self.chunk.selected_lod
                or asset.url
                != (
                    f"/api/world/mesh-assets/{bundle_id}/"
                    f"{asset.asset_id}/lod{asset.lod}.glb"
                )
            ):
                raise ValueError(
                    "mesh runtime v2 asset route or LOD is invalid",
                )
            if any(
                dependency.url.split("/")[4] != bundle_id
                for dependency in asset.texture_dependencies
            ):
                raise ValueError(
                    "mesh runtime v2 texture bundle identity disagrees",
                )
        surface_slots = tuple(
            row.slot_id for row in self.surface_materials
        )
        required_surface_slots = tuple(sorted({
            self.chunk.terrain.material_slot_id,
            *self.chunk.terrain.material_slot_ids,
            *(
                ribbon.material_slot_id
                for ribbon in self.chunk.roads
            ),
            *(
                ribbon.material_slot_id
                for ribbon in self.chunk.water
            ),
        }))
        if (
            surface_slots != required_surface_slots
            or len(set(surface_slots)) != len(surface_slots)
        ):
            raise ValueError(
                "mesh runtime v2 surface closure is incomplete or unsorted",
            )
        return self


MeshChunkRuntimeManifestAny = (
    MeshChunkRuntimeManifest | MeshChunkRuntimeManifestV2
)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_mesh_chunk_bytes(
    manifest: MeshChunkManifest,
    *,
    exclude_content_key: bool = False,
) -> bytes:
    payload = manifest.model_dump(mode="json")
    if exclude_content_key:
        payload.pop("content_key")
    return _canonical_json_bytes(payload)


def canonical_mesh_chunk_runtime_bytes(
    manifest: MeshChunkRuntimeManifestAny,
) -> bytes:
    return _canonical_json_bytes(manifest)


def _require_safe_int(value: object, *, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or abs(value) > MAX_SAFE_INTEGER
    ):
        raise MeshChunkError(f"{label} must be a safe integer")
    return value


def _stable_seed(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")


def _layout_bytes(layout: BaseModel) -> bytes:
    return _canonical_json_bytes(layout.model_dump(mode="json"))


def _terrain_grid(
    chunk_x: int,
    chunk_y: int,
    world_seed: int,
    lod: int,
) -> TerrainGrid:
    resolution = TERRAIN_RESOLUTION[lod]
    step = CHUNK_SIZE_M / (resolution - 1)
    vertices = []
    for y_index in range(resolution):
        local_y = y_index * step
        for x_index in range(resolution):
            local_x = x_index * step
            world_u = float(chunk_x * CHUNK_SIZE_M + local_x)
            world_v = float(chunk_y * CHUNK_SIZE_M + local_y)
            vertices.append(
                TerrainVertex(
                    x=float(local_x),
                    y=float(local_y),
                    z=terrain_height_m(
                        world_u,
                        world_v,
                        world_seed=world_seed,
                    ),
                    world_u=world_u,
                    world_v=world_v,
                    macro_tint=terrain_macro_tint(
                        world_u,
                        world_v,
                        world_seed=world_seed,
                    ),
                ),
            )
    return TerrainGrid(
        resolution=resolution,
        material_slot_ids=tuple(
            terrain_material_slot(
                chunk_x * CHUNK_SIZE_M + (x_index + 0.5) * step,
                chunk_y * CHUNK_SIZE_M + (y_index + 0.5) * step,
                world_seed=world_seed,
            )
            for y_index in range(resolution - 1)
            for x_index in range(resolution - 1)
        ),
        vertices=tuple(vertices),
    )


def _road_ribbons(layout) -> tuple[Ribbon, ...]:
    world_x = layout.chunk_id.x * CHUNK_SIZE_M
    world_y = layout.chunk_id.y * CHUNK_SIZE_M
    return tuple(
        Ribbon(
            ribbon_id=road.id,
            kind="road",
            feature_type=road.type,
            width=float(road.width),
            z_offset=0.04,
            material_slot_id=ROAD_MATERIAL_SLOTS[road.type],
            points=tuple(
                (
                    float(point[0]),
                    float(point[1]),
                    terrain_height_m(
                        world_x + point[0],
                        world_y + point[1],
                        world_seed=layout.world_seed,
                    )
                    + 0.04,
                )
                for point in road.points
            ),
        )
        for road in sorted(layout.roads, key=lambda row: row.id)
    )


def _water_ribbons(layout) -> tuple[Ribbon, ...]:
    world_x = layout.chunk_id.x * CHUNK_SIZE_M
    world_y = layout.chunk_id.y * CHUNK_SIZE_M
    return tuple(
        Ribbon(
            ribbon_id=feature.id,
            kind="water",
            feature_type=feature.type,
            width=float(feature.width),
            z_offset=0.02,
            material_slot_id=WATER_MATERIAL_SLOT,
            points=tuple(
                (
                    float(point[0]),
                    float(point[1]),
                    terrain_height_m(
                        world_x + point[0],
                        world_y + point[1],
                        world_seed=layout.world_seed,
                    )
                    + 0.02,
                )
                for point in feature.points
            ),
        )
        for feature in sorted(layout.water, key=lambda row: row.id)
    )


def _asset_record(
    records: dict[str, MeshAssetRecordAny],
    asset_id: str,
    *,
    expected_kind: str,
) -> MeshAssetRecordAny:
    record = records.get(asset_id)
    if record is None:
        raise MeshChunkError(
            f"layout asset {asset_id!r} is absent from the mesh asset bundle",
        )
    if record.kind != expected_kind:
        raise MeshChunkError(
            f"layout asset {asset_id!r} kind disagrees with the mesh bundle",
        )
    return record


def _mesh_instances(
    layout,
    bundle: MeshAssetBundleAny,
    lod: int,
) -> tuple[MeshInstance, ...]:
    records = {record.asset_id: record for record in bundle.records}
    instances: list[MeshInstance] = []
    world_x = layout.chunk_id.x * CHUNK_SIZE_M
    world_y = layout.chunk_id.y * CHUNK_SIZE_M
    for building in layout.buildings:
        _asset_record(records, building.asset_id, expected_kind="building")
        instances.append(
            MeshInstance(
                instance_id=f"building:{building.id}",
                asset_id=building.asset_id,
                kind="building",
                local_position=(
                    float(building.pos[0]),
                    float(building.pos[1]),
                    terrain_height_m(
                        world_x + building.pos[0],
                        world_y + building.pos[1],
                        world_seed=layout.world_seed,
                    ),
                ),
                rotation_z_degrees=float(building.rot_z % 360),
                scale=float(building.scale),
                template_lod=lod,
            ),
        )
    for prop in layout.props:
        _asset_record(records, prop.asset_id, expected_kind="prop")
        instances.append(
            MeshInstance(
                instance_id=f"prop:{prop.id}",
                asset_id=prop.asset_id,
                kind="prop",
                local_position=(
                    float(prop.pos[0]),
                    float(prop.pos[1]),
                    terrain_height_m(
                        world_x + prop.pos[0],
                        world_y + prop.pos[1],
                        world_seed=layout.world_seed,
                    ),
                ),
                rotation_z_degrees=float(prop.rot_z % 360),
                scale=1.0,
                template_lod=lod,
            ),
        )
    for cluster in layout.vegetation:
        available = tuple(sorted(cluster.asset_ids))
        for asset_id in available:
            _asset_record(records, asset_id, expected_kind="vegetation")
        count = max(1, round(VEGETATION_LIMIT[lod] * cluster.density))
        rng = random.Random(
            _stable_seed(
                f"mesh-vegetation-v1:{layout.world_seed}:"
                f"{layout.chunk_id.x}:{layout.chunk_id.y}:{cluster.id}",
            ),
        )
        for index in range(count):
            asset_id = available[index % len(available)]
            radius = cluster.radius * math.sqrt(rng.random())
            angle = rng.random() * math.tau
            local_x = min(
                CHUNK_SIZE_M,
                max(0.0, cluster.center[0] + radius * math.cos(angle)),
            )
            local_y = min(
                CHUNK_SIZE_M,
                max(0.0, cluster.center[1] + radius * math.sin(angle)),
            )
            local_x = round(float(local_x), 6)
            local_y = round(float(local_y), 6)
            instances.append(
                MeshInstance(
                    instance_id=f"vegetation:{cluster.id}:{index:02d}",
                    asset_id=asset_id,
                    kind="vegetation",
                    local_position=(
                        local_x,
                        local_y,
                        terrain_height_m(
                            world_x + local_x,
                            world_y + local_y,
                            world_seed=layout.world_seed,
                        ),
                    ),
                    rotation_z_degrees=round(rng.random() * 359.999999, 6),
                    scale=round(0.8 + rng.random() * 0.4, 6),
                    template_lod=lod,
                ),
            )
    return tuple(sorted(instances, key=lambda row: row.instance_id))


def _expand_bounds(
    lower: list[float],
    upper: list[float],
    point: tuple[float, float, float],
) -> None:
    for index, value in enumerate(point):
        lower[index] = min(lower[index], value)
        upper[index] = max(upper[index], value)


def _instance_bounds(
    instance: MeshInstance,
    record: MeshAssetRecordAny,
    world_offset: tuple[float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    bounds = record.lod[str(instance.template_lod)].aabb
    angle = math.radians(instance.rotation_z_degrees)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    corners = []
    for x in (bounds.min[0], bounds.max[0]):
        for y in (bounds.min[1], bounds.max[1]):
            for z in (bounds.min[2], bounds.max[2]):
                scaled_x = x * instance.scale
                scaled_y = y * instance.scale
                rotated_x = scaled_x * cosine - scaled_y * sine
                rotated_y = scaled_x * sine + scaled_y * cosine
                corners.append(
                    (
                        world_offset[0] + instance.local_position[0] + rotated_x,
                        world_offset[1] + instance.local_position[1] + rotated_y,
                        world_offset[2]
                        + instance.local_position[2]
                        + z * instance.scale,
                    ),
                )
    return tuple(corners)


def _chunk_bounds(
    *,
    world_offset: tuple[float, float, float],
    terrain: TerrainGrid,
    roads: tuple[Ribbon, ...],
    water: tuple[Ribbon, ...],
    instances: tuple[MeshInstance, ...],
    bundle: MeshAssetBundleAny,
) -> Bounds3:
    lower = [world_offset[0], world_offset[1], float("inf")]
    upper = [
        world_offset[0] + CHUNK_SIZE_M,
        world_offset[1] + CHUNK_SIZE_M,
        float("-inf"),
    ]
    for vertex in terrain.vertices:
        _expand_bounds(
            lower,
            upper,
            (
                world_offset[0] + vertex.x,
                world_offset[1] + vertex.y,
                world_offset[2] + vertex.z,
            ),
        )
    for ribbon in (*roads, *water):
        half_width = ribbon.width / 2
        for x, y, z in ribbon.points:
            _expand_bounds(
                lower,
                upper,
                (
                    world_offset[0] + x - half_width,
                    world_offset[1] + y - half_width,
                    world_offset[2] + z,
                ),
            )
            _expand_bounds(
                lower,
                upper,
                (
                    world_offset[0] + x + half_width,
                    world_offset[1] + y + half_width,
                    world_offset[2] + z,
                ),
            )
    records = {record.asset_id: record for record in bundle.records}
    for instance in instances:
        for corner in _instance_bounds(
            instance,
            records[instance.asset_id],
            world_offset,
        ):
            _expand_bounds(lower, upper, corner)
    return Bounds3(
        min=tuple(float(round(value, 6)) for value in lower),
        max=tuple(float(round(value, 6)) for value in upper),
    )


def mesh_chunk_content_key(payload: dict[str, object]) -> str:
    canonical = dict(payload)
    canonical.pop("content_key", None)
    return hashlib.sha256(_canonical_json_bytes(canonical)).hexdigest()


def build_mesh_chunk_manifest(
    chunk_x: int,
    chunk_y: int,
    *,
    world_seed: int,
    bundle: MeshAssetBundleAny,
    lod: int,
) -> MeshChunkManifest:
    """Derive one canonical mesh chunk from the shared deterministic layout."""

    chunk_x = _require_safe_int(chunk_x, label="chunk X")
    chunk_y = _require_safe_int(chunk_y, label="chunk Y")
    world_seed = _require_safe_int(world_seed, label="world seed")
    if isinstance(lod, bool) or lod not in {0, 1, 2}:
        raise MeshChunkError("mesh chunk LOD must be 0, 1, or 2")

    layout = MockLayoutGenerator(world_seed).generate_chunk(chunk_x, chunk_y)
    terrain = _terrain_grid(chunk_x, chunk_y, world_seed, lod)
    roads = _road_ribbons(layout)
    water = _water_ribbons(layout)
    instances = _mesh_instances(layout, bundle, lod)
    required_material_slots = {
        terrain.material_slot_id,
        *terrain.material_slot_ids,
        *(ribbon.material_slot_id for ribbon in (*roads, *water)),
    }
    available_material_slots = {
        material.slot_id for material in bundle.material_registry
    }
    if not required_material_slots <= available_material_slots:
        raise MeshChunkError(
            "mesh chunk surface material is absent from the material bundle",
        )
    world_offset = (
        float(chunk_x * CHUNK_SIZE_M),
        float(chunk_y * CHUNK_SIZE_M),
        0.0,
    )
    payload: dict[str, object] = {
        "schema_version": MESH_CHUNK_SCHEMA,
        "content_key": "0" * 64,
        "renderer_capability": RENDERER_CAPABILITY,
        "world_seed": world_seed,
        "chunk_id": {"x": chunk_x, "y": chunk_y},
        "chunk_size_m": CHUNK_SIZE_M,
        "world_offset": world_offset,
        "layout_algorithm_id": LAYOUT_ALGORITHM_ID,
        "layout_sha256": hashlib.sha256(_layout_bytes(layout)).hexdigest(),
        "terrain_algorithm_id": TERRAIN_ALGORITHM_ID,
        "mesh_asset_bundle_id": bundle.bundle_id,
        "material_bundle_id": bundle.material_bundle_id,
        "selected_lod": lod,
        "terrain": terrain,
        "roads": roads,
        "water": water,
        "instances": instances,
        "aabb": _chunk_bounds(
            world_offset=world_offset,
            terrain=terrain,
            roads=roads,
            water=water,
            instances=instances,
            bundle=bundle,
        ),
        "synthetic": True,
        "geometry_usability": "preview-only",
        "coordinate_confidence": "synthetic-layout",
        "metric_alignment": False,
        "real_photo_textures": False,
    }
    payload["content_key"] = mesh_chunk_content_key(payload)
    return MeshChunkManifest.model_validate(payload)


def project_mesh_chunk_runtime(
    chunk: MeshChunkManifest,
    *,
    bundle: MeshAssetBundleAny,
    material_bundle: DerivedMaterialBundle,
) -> MeshChunkRuntimeManifestAny:
    """Add exact runtime routes without changing the canonical chunk identity."""

    if (
        chunk.mesh_asset_bundle_id != bundle.bundle_id
        or chunk.material_bundle_id != bundle.material_bundle_id
        or material_bundle.bundle_id != bundle.material_bundle_id
    ):
        raise MeshChunkError("mesh chunk, asset, and material bundle identities disagree")
    records = {record.asset_id: record for record in bundle.records}
    rows: list[MeshAssetRuntimeUrl | MeshAssetRuntimeUrlV2] = []
    texture_objects = (
        {
            row.sha256: row
            for row in bundle.texture_objects
        }
        if type(bundle) is MeshAssetBundleV2
        else {}
    )
    for asset_id in sorted({instance.asset_id for instance in chunk.instances}):
        record = records.get(asset_id)
        if record is None:
            raise MeshChunkError("mesh chunk references an unavailable runtime asset")
        descriptor = record.lod[str(chunk.selected_lod)]
        route = (
            f"/api/world/mesh-assets/{bundle.bundle_id}/"
            f"{asset_id}/lod{chunk.selected_lod}.glb"
        )
        if type(bundle) is MeshAssetBundle:
            rows.append(
                MeshAssetRuntimeUrl(
                    asset_id=asset_id,
                    lod=chunk.selected_lod,
                    url=route,
                    glb_sha256=descriptor.glb_sha256,
                    glb_bytes=descriptor.glb_bytes,
                ),
            )
        elif type(bundle) is MeshAssetBundleV2:
            if type(descriptor) is not MeshTemplateLodV2:
                raise MeshChunkError(
                    "mesh runtime v2 descriptor type disagrees",
                )
            dependencies = []
            for binding in sorted(
                descriptor.texture_bindings,
                key=lambda row: (
                    row.sha256,
                    row.role,
                    row.material_slot_id,
                ),
            ):
                texture = texture_objects.get(binding.sha256)
                if (
                    texture is None
                    or texture.object_path
                    != f"textures/{binding.sha256}.png"
                ):
                    raise MeshChunkError(
                        "mesh runtime v2 texture object is unavailable",
                    )
                dependencies.append(
                    MeshTextureRuntimeUrl(
                        url=(
                            f"/api/world/mesh-assets/{bundle.bundle_id}/"
                            f"textures/{binding.sha256}.png"
                        ),
                        sha256=binding.sha256,
                        bytes=texture.bytes,
                        role=binding.role,
                        colour_space=binding.colour_space,
                        material_slot_id=binding.material_slot_id,
                        derivation_algorithm_id=(
                            binding.derivation_algorithm_id
                        ),
                        min_filter=binding.min_filter,
                        mag_filter=binding.mag_filter,
                        wrap_s=binding.wrap_s,
                        wrap_t=binding.wrap_t,
                    ),
                )
            rows.append(
                MeshAssetRuntimeUrlV2(
                    asset_id=asset_id,
                    lod=chunk.selected_lod,
                    url=route,
                    glb_sha256=descriptor.glb_sha256,
                    glb_bytes=descriptor.glb_bytes,
                    texture_dependencies=tuple(dependencies),
                ),
            )
        else:
            raise MeshChunkError(
                "mesh runtime bundle schema is unsupported",
            )
    material_records = {
        record.slot_id: record
        for record in material_bundle.records
    }
    registered_materials = {
        record.slot_id: record
        for record in bundle.material_registry
    }
    surface_slots = sorted({
        chunk.terrain.material_slot_id,
        *chunk.terrain.material_slot_ids,
        *(ribbon.material_slot_id for ribbon in chunk.roads),
        *(ribbon.material_slot_id for ribbon in chunk.water),
    })
    surfaces = []
    for slot_id in surface_slots:
        record = material_records.get(slot_id)
        registered = registered_materials.get(slot_id)
        if (
            record is None
            or registered is None
            or record.source_sha256 != registered.source_sha256
        ):
            raise MeshChunkError(
                "mesh surface material is unavailable or disagrees with verified evidence",
            )
        surfaces.append(_surface_material_runtime(record, material_bundle.bundle_id))
    if type(bundle) is MeshAssetBundle:
        return MeshChunkRuntimeManifest(
            chunk=chunk,
            asset_urls=tuple(rows),
            surface_materials=tuple(surfaces),
        )
    if type(bundle) is MeshAssetBundleV2:
        return MeshChunkRuntimeManifestV2(
            chunk=chunk,
            asset_urls=tuple(rows),
            surface_materials=tuple(surfaces),
        )
    raise MeshChunkError("mesh runtime bundle schema is unsupported")


def _surface_material_runtime(
    record: DerivedMaterialRecord,
    material_bundle_id: str,
) -> SurfaceMaterialRuntime:
    def map_runtime(
        role: Literal["base_color", "normal", "orm"],
    ) -> MaterialMapRuntimeUrl:
        descriptor = getattr(record, role)
        return MaterialMapRuntimeUrl(
            role=role,
            url=(
                f"/api/world/material-maps/{material_bundle_id}/"
                f"{record.slot_id}/{role}.png"
            ),
            sha256=descriptor.sha256,
            bytes=descriptor.bytes,
            color_space=descriptor.color_space,
        )

    return SurfaceMaterialRuntime(
        slot_id=record.slot_id,
        uv_policy=record.uv_policy,
        nominal_tile_m=record.nominal_tile_m,
        normal_strength=record.normal_strength,
        roughness_center=record.roughness_center,
        metallic=record.metallic,
        base_color=map_runtime("base_color"),
        normal=map_runtime("normal"),
        orm=map_runtime("orm"),
    )
