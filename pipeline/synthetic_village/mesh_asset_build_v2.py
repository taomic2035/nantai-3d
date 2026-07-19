"""Path-free identities and orchestration contracts for near-mesh v2 builds."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from pipeline.synthetic_village.canary import MaterialInputRecord
from pipeline.synthetic_village.foliage_atlas import (
    ALPHA_CUTOFF,
    FoliageAtlasSet,
    PreparedFoliageAtlasSet,
    _verify_prepared_atlas_set,
)
from pipeline.synthetic_village.glb_material_audit import ExpectedGlbMaterial
from pipeline.synthetic_village.glb_shared_texture_audit import (
    SharedTextureGlbAuditError,
    audit_shared_textured_glb,
)
from pipeline.synthetic_village.local_textured_preview import LocalBlenderIdentity
from pipeline.synthetic_village.material_bundle import (
    MaterialAlgorithmId,
    MaterialBundleError,
    canonical_material_bundle_bytes,
    load_material_bundle,
)
from pipeline.synthetic_village.mesh_asset_build import (
    ASSET_RECIPE_CONTRACTS,
    EXPECTED_ASSET_IDS,
    AssetKind,
    MeshAssetBuildError,
    _is_linklike,
    _material_input_registry,
    _read_regular_file,
    _real_directory,
    _recipes_from_registry,
    _reject_duplicate_keys,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    GLB_COORDINATE_ENCODING,
    Bounds3,
    MeshAssetBundle,
    canonical_mesh_asset_bundle_bytes,
    load_mesh_asset_bundle,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    LOD2_TRIANGLE_BANDS,
    MAX_MESH_TEXTURE_BYTES,
    MeshAssetLod2SourceV2,
    TextureBindingV2,
    TextureObjectV2,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

MESH_ASSET_BUILD_V2_SCHEMA = "nantai.synthetic-village.mesh-asset-build.v2"
MESH_ASSET_BUILD_REPORT_V2_SCHEMA = (
    "nantai.synthetic-village.mesh-asset-build-report.v2"
)

NEAR_RECIPE_IDS = {
    "fence_wood_01": "weathered-timber-fence-near-v2",
    "house_barn_01": "dark-timber-barn-near-v2",
    "house_stone_01": "fieldstone-house-near-v2",
    "house_thatch_01": "rammed-earth-thatch-house-near-v2",
    "house_wood_01": "weathered-timber-house-near-v2",
    "house_wood_02": "plaster-timber-house-near-v2",
    "stone_lamp_01": "stone-metal-lamp-near-v2",
    "stone_wall_01": "dry-stone-wall-near-v2",
    "tree_bamboo_01": "clustered-bamboo-near-v2",
    "tree_broadleaf_01": "humid-broadleaf-near-v2",
    "tree_pine_01": "layered-pine-near-v2",
}


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
    )


class ReusedMeshLodV2(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    lod: Literal[0, 1]
    glb_object_path: str = Field(min_length=1)
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1)
    triangle_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    local_enu_aabb: Bounds3
    mesh_algorithm_id: Literal["synthetic-template-mesh-v1"] = (
        "synthetic-template-mesh-v1"
    )
    recipe_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

    @model_validator(mode="after")
    def _exact_reuse_contract(self) -> ReusedMeshLodV2:
        contract = ASSET_RECIPE_CONTRACTS.get(self.asset_id)
        expected_path = f"objects/{self.glb_sha256}.glb"
        if (
            contract is None
            or self.recipe_id != contract[1]
            or self.glb_object_path != expected_path
            or self.material_slot_ids != contract[2]
        ):
            raise ValueError("reused mesh LOD does not match its exact v1 contract")
        return self


class NearMeshRecipeV2(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: AssetKind
    footprint_m: tuple[float, float, float]
    recipe_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    lod2_triangle_min: int = Field(ge=1)
    lod2_triangle_max: int = Field(ge=1)

    @model_validator(mode="after")
    def _exact_near_recipe(self) -> NearMeshRecipeV2:
        contract = ASSET_RECIPE_CONTRACTS.get(self.asset_id)
        if contract is None:
            raise ValueError("near mesh recipe asset is not registered")
        expected_kind, _v1_recipe, expected_slots = contract
        expected_band = LOD2_TRIANGLE_BANDS[expected_kind]
        if (
            self.kind != expected_kind
            or self.recipe_id != NEAR_RECIPE_IDS[self.asset_id]
            or self.material_slot_ids != expected_slots
            or (self.lod2_triangle_min, self.lod2_triangle_max)
            != expected_band
            or not all(
                math.isfinite(value) and value > 0
                for value in self.footprint_m
            )
        ):
            raise ValueError("near mesh recipe does not match its exact contract")
        return self


class MeshTextureSamplerV2(FrozenModel):
    min_filter: Literal[9987] = 9987
    mag_filter: Literal[9729] = 9729
    wrap_s: Literal[10497] = 10497
    wrap_t: Literal[10497] = 10497


class MeshAssetBuildRequestV2(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-asset-build.v2"
    ] = MESH_ASSET_BUILD_V2_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    coordinate_encoding: Literal[
        "three-east-up-negative-north"
    ] = GLB_COORDINATE_ENCODING
    source_v1_bundle_id: Sha256
    source_v1_manifest_sha256: Sha256
    material_bundle_id: Sha256
    material_bundle_manifest_sha256: Sha256
    material_algorithm_id: MaterialAlgorithmId
    material_input_registry: tuple[MaterialInputRecord, ...] = Field(
        min_length=24,
        max_length=24,
    )
    foliage_atlas_set: FoliageAtlasSet
    asset_registry_sha256: Sha256
    blender_identity: LocalBlenderIdentity
    builder_script_sha256: Sha256
    recipes: tuple[NearMeshRecipeV2, ...] = Field(min_length=11, max_length=11)
    reused_lods: tuple[ReusedMeshLodV2, ...] = Field(
        min_length=22,
        max_length=22,
    )
    lod_levels_to_build: tuple[Literal[2]] = (2,)
    alpha_cutoff: Literal[0.45] = ALPHA_CUTOFF
    sampler: MeshTextureSamplerV2 = MeshTextureSamplerV2()

    @property
    def asset_ids(self) -> tuple[str, ...]:
        return tuple(row.asset_id for row in self.recipes)

    @model_validator(mode="after")
    def _complete_path_free_identity(self) -> MeshAssetBuildRequestV2:
        if self.asset_ids != EXPECTED_ASSET_IDS:
            raise ValueError("near mesh recipes are not the exact sorted asset set")
        expected_reuse = tuple(
            (asset_id, lod)
            for asset_id in EXPECTED_ASSET_IDS
            for lod in (0, 1)
        )
        if tuple((row.asset_id, row.lod) for row in self.reused_lods) != (
            expected_reuse
        ):
            raise ValueError("near mesh v1 reuse matrix is not exact and sorted")
        material_slots = tuple(
            row.slot_id
            for row in self.material_input_registry
        )
        if (
            material_slots != tuple(sorted(material_slots))
            or len(set(material_slots)) != 24
        ):
            raise ValueError("near mesh material input registry is incomplete")
        if (
            self.foliage_atlas_set.source_material_bundle_id
            != self.material_bundle_id
        ):
            raise ValueError("near mesh foliage atlas material identity disagrees")
        digest = hashlib.sha256(
            canonical_mesh_asset_build_request_v2_bytes(
                self,
                exclude_build_id=True,
            ),
        ).hexdigest()
        if digest != self.build_id:
            raise ValueError("near mesh build ID does not match canonical inputs")
        return self


class MeshAssetBuildReportRowV2(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    lod: Literal[2]
    artifact_path: str = Field(min_length=1)
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1)
    triangle_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    local_enu_aabb: Bounds3
    texture_bindings: tuple[TextureBindingV2, ...] = ()

    @model_validator(mode="after")
    def _exact_artifact_contract(self) -> MeshAssetBuildReportRowV2:
        contract = ASSET_RECIPE_CONTRACTS.get(self.asset_id)
        expected_path = f"artifacts/{self.asset_id}/lod2.glb"
        if (
            contract is None
            or self.artifact_path != expected_path
            or PurePosixPath(self.artifact_path).as_posix()
            != self.artifact_path
            or PurePosixPath(self.artifact_path).is_absolute()
            or self.material_slot_ids != contract[2]
        ):
            raise ValueError("near mesh report artifact contract is invalid")
        lower, upper = LOD2_TRIANGLE_BANDS[contract[0]]
        if not lower <= self.triangle_count <= upper:
            raise ValueError("near mesh report triangle band is invalid")
        return self


class MeshAssetBuildReportV2(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-asset-build-report.v2"
    ] = MESH_ASSET_BUILD_REPORT_V2_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    coordinate_encoding: Literal[
        "three-east-up-negative-north"
    ] = GLB_COORDINATE_ENCODING
    blender_identity: LocalBlenderIdentity
    builder_script_sha256: Sha256
    artifacts: tuple[MeshAssetBuildReportRowV2, ...]

    @model_validator(mode="after")
    def _complete_artifact_matrix(self) -> MeshAssetBuildReportV2:
        if tuple(row.asset_id for row in self.artifacts) != EXPECTED_ASSET_IDS:
            raise ValueError(
                "near mesh report does not contain the exact sorted LOD2 artifacts",
            )
        return self


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


def canonical_mesh_asset_build_request_v2_bytes(
    request: MeshAssetBuildRequestV2,
    *,
    exclude_build_id: bool = False,
) -> bytes:
    payload = request.model_dump(mode="json")
    if exclude_build_id:
        payload.pop("build_id")
    return _canonical_json_bytes(payload)


def canonical_mesh_asset_build_report_v2_bytes(
    report: MeshAssetBuildReportV2,
) -> bytes:
    return _canonical_json_bytes(report)


def _expected_texture_bindings(
    request: MeshAssetBuildRequestV2,
    recipe: NearMeshRecipeV2,
) -> tuple[TextureBindingV2, ...]:
    material_inputs = {
        row.slot_id: row
        for row in request.material_input_registry
    }
    atlas_records = request.foliage_atlas_set.by_slot
    bindings = []
    for slot_id in recipe.material_slot_ids:
        atlas = atlas_records.get(slot_id)
        material = material_inputs[slot_id]
        for role in ("base_color", "normal", "orm"):
            sha256 = (
                getattr(atlas, role).sha256
                if atlas is not None
                else getattr(material, f"{role}_sha256")
            )
            bindings.append(
                TextureBindingV2(
                    uri=f"../textures/{sha256}.png",
                    sha256=sha256,
                    role=role,
                    colour_space=(
                        "srgb" if role == "base_color" else "non-color"
                    ),
                    material_slot_id=slot_id,
                    derivation_algorithm_id=(
                        request.foliage_atlas_set.algorithm_id
                        if atlas is not None
                        else request.material_algorithm_id
                    ),
                ),
            )
    return tuple(
        sorted(
            bindings,
            key=lambda row: (
                row.material_slot_id,
                row.role,
                row.sha256,
                row.derivation_algorithm_id,
            ),
        ),
    )


def _validate_builder_output_closure(
    staging: Path,
    report: MeshAssetBuildReportV2,
    texture_hashes: tuple[str, ...],
) -> Path:
    root = _real_directory(staging, label="near mesh builder output")
    expected_files = {
        "build-report.json",
        *(row.artifact_path for row in report.artifacts),
        *(f"textures/{sha256}.png" for sha256 in texture_hashes),
    }
    expected_directories = {
        "artifacts",
        "textures",
        *(f"artifacts/{asset_id}" for asset_id in EXPECTED_ASSET_IDS),
    }
    try:
        entries = tuple(root.rglob("*"))
    except OSError as exc:
        raise MeshAssetBuildError(
            "near mesh builder output cannot be enumerated",
        ) from exc
    if any(_is_linklike(path) for path in entries):
        raise MeshAssetBuildError(
            "near mesh builder output contains redirected entries",
        )
    actual_files = {
        path.relative_to(root).as_posix()
        for path in entries
        if path.is_file()
    }
    actual_directories = {
        path.relative_to(root).as_posix()
        for path in entries
        if path.is_dir()
    }
    if (
        actual_files != expected_files
        or actual_directories != expected_directories
        or len(entries) != len(actual_files) + len(actual_directories)
    ):
        raise MeshAssetBuildError(
            "near mesh builder output is incomplete or contains extras",
        )
    return root


def _report_sources_and_texture_objects(
    *,
    request: MeshAssetBuildRequestV2,
    report: MeshAssetBuildReportV2,
    staging: Path,
) -> tuple[
    tuple[MeshAssetLod2SourceV2, ...],
    tuple[TextureObjectV2, ...],
]:
    if (
        report.build_id != request.build_id
        or report.blender_identity != request.blender_identity
        or report.builder_script_sha256 != request.builder_script_sha256
    ):
        raise MeshAssetBuildError(
            "near mesh build report identity disagrees with its request",
        )
    recipes = {recipe.asset_id: recipe for recipe in request.recipes}
    for row in report.artifacts:
        recipe = recipes[row.asset_id]
        if row.texture_bindings != _expected_texture_bindings(
            request,
            recipe,
        ):
            raise MeshAssetBuildError(
                "near mesh report texture closure disagrees with its request",
            )
    texture_hashes = tuple(sorted({
        binding.sha256
        for row in report.artifacts
        for binding in row.texture_bindings
    }))
    root = _validate_builder_output_closure(
        staging,
        report,
        texture_hashes,
    )
    texture_objects = []
    for sha256 in texture_hashes:
        path = root / f"textures/{sha256}.png"
        payload = _read_regular_file(
            path,
            label="near mesh shared texture",
            maximum_bytes=MAX_MESH_TEXTURE_BYTES,
        )
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise MeshAssetBuildError(
                "near mesh shared texture bytes disagree with their URI",
            )
        texture_objects.append(
            TextureObjectV2(
                object_path=f"textures/{sha256}.png",
                sha256=sha256,
                bytes=len(payload),
            ),
        )
    texture_objects_tuple = tuple(texture_objects)
    material_inputs = {
        row.slot_id: row
        for row in request.material_input_registry
    }
    sources = []
    for row in report.artifacts:
        recipe = recipes[row.asset_id]
        expected_materials = tuple(
            ExpectedGlbMaterial(
                slot_id=slot_id,
                source_sha256=material_inputs[slot_id].source_sha256,
                bundle_id=request.material_bundle_id,
                algorithm_id=request.material_algorithm_id,
            )
            for slot_id in recipe.material_slot_ids
        )
        dependency_hashes = {
            binding.sha256
            for binding in row.texture_bindings
        }
        dependency_objects = tuple(
            descriptor
            for descriptor in texture_objects_tuple
            if descriptor.sha256 in dependency_hashes
        )
        artifact = root / row.artifact_path
        try:
            audit = audit_shared_textured_glb(
                artifact,
                expected_materials=expected_materials,
                texture_root=root,
                bindings=row.texture_bindings,
                objects=dependency_objects,
                kind=recipe.kind,
                footprint_m=recipe.footprint_m,
            )
        except SharedTextureGlbAuditError as exc:
            raise MeshAssetBuildError(
                "near mesh builder artifact audit failed",
            ) from exc
        bounds_agree = all(
            abs(measured - declared) <= 1e-5
            for measured, declared in zip(
                (
                    *audit.topology.aabb.min,
                    *audit.topology.aabb.max,
                ),
                (
                    *row.local_enu_aabb.min,
                    *row.local_enu_aabb.max,
                ),
                strict=True,
            )
        )
        if (
            audit.glb_sha256 != row.glb_sha256
            or audit.byte_count != row.glb_bytes
            or audit.triangle_count != row.triangle_count
            or audit.primitive_count != row.primitive_count
            or audit.slot_ids != row.material_slot_ids
            or not bounds_agree
        ):
            raise MeshAssetBuildError(
                "near mesh report differs from independent artifact evidence",
            )
        sources.append(
            MeshAssetLod2SourceV2(
                asset_id=row.asset_id,
                glb_path=artifact,
                recipe_id=recipe.recipe_id,
                texture_bindings=row.texture_bindings,
            ),
        )
    return tuple(sources), texture_objects_tuple


def _selected_builder(repo_root: Path, builder_script: Path) -> Path:
    selected = Path(builder_script)
    if not selected.is_absolute():
        selected = repo_root / selected
    selected = selected.absolute()
    try:
        selected.relative_to(repo_root)
    except ValueError as exc:
        raise MeshAssetBuildError(
            "near mesh builder script escapes the repository",
        ) from exc
    _read_regular_file(selected, label="near mesh builder script")
    return selected


def build_mesh_asset_request_v2(
    *,
    source_v1_bundle_root: Path,
    material_bundle_root: Path,
    foliage_atlas_set: PreparedFoliageAtlasSet,
    builder_script: Path,
    blender_identity: LocalBlenderIdentity,
    repo_root: Path | None = None,
) -> MeshAssetBuildRequestV2:
    """Bind every reusable and rebuilt input without paths or timestamps."""

    repo = _real_directory(
        Path(repo_root) if repo_root is not None else Path(__file__).parents[2],
        label="repository root",
    )
    source_root = _real_directory(
        Path(source_v1_bundle_root),
        label="source v1 mesh bundle",
    )
    material_root = _real_directory(
        Path(material_bundle_root),
        label="material bundle root",
    )
    builder = _selected_builder(repo, builder_script)
    registry_path = repo / "assets/registry.json"
    registry_raw = _read_regular_file(
        registry_path,
        label="mesh asset registry",
    )
    builder_raw = _read_regular_file(
        builder,
        label="near mesh builder script",
    )
    try:
        registry = json.loads(
            registry_raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(registry, dict):
            raise ValueError("asset registry root is not an object")
        source_bundle = load_mesh_asset_bundle(source_root)
        if type(source_bundle) is not MeshAssetBundle:
            raise ValueError("source mesh bundle is not schema v1")
        material_bundle = load_material_bundle(material_root)
        _verify_prepared_atlas_set(
            foliage_atlas_set.root,
            foliage_atlas_set.manifest,
        )
    except (
        MaterialBundleError,
        OSError,
        ValidationError,
        ValueError,
    ) as exc:
        raise MeshAssetBuildError(
            f"near mesh build inputs cannot be trusted: {exc}",
        ) from exc
    material_manifest_sha256 = hashlib.sha256(
        canonical_material_bundle_bytes(material_bundle),
    ).hexdigest()
    if (
        source_bundle.material_bundle_id != material_bundle.bundle_id
        or source_bundle.material_bundle_manifest_sha256
        != material_manifest_sha256
        or foliage_atlas_set.manifest.source_material_bundle_id
        != material_bundle.bundle_id
        or foliage_atlas_set.manifest.source_material_manifest_sha256
        != material_manifest_sha256
    ):
        raise MeshAssetBuildError(
            "near mesh source, material, and foliage identities disagree",
        )
    v1_recipes = _recipes_from_registry(registry)
    source_by_id = {
        record.asset_id: record
        for record in source_bundle.records
    }
    if tuple(source_by_id) != EXPECTED_ASSET_IDS:
        raise MeshAssetBuildError(
            "source v1 mesh bundle does not contain exact sorted assets",
        )
    recipes = []
    reused = []
    for recipe in v1_recipes:
        source_record = source_by_id[recipe.asset_id]
        if (
            source_record.kind != recipe.kind
            or source_record.footprint_m != recipe.footprint_m
            or source_record.lod["2"].material_slot_ids
            != recipe.material_slot_ids
        ):
            raise MeshAssetBuildError(
                "source v1 mesh asset differs from the registry contract",
            )
        lower, upper = LOD2_TRIANGLE_BANDS[recipe.kind]
        recipes.append(
            NearMeshRecipeV2(
                asset_id=recipe.asset_id,
                kind=recipe.kind,
                footprint_m=recipe.footprint_m,
                recipe_id=NEAR_RECIPE_IDS[recipe.asset_id],
                material_slot_ids=recipe.material_slot_ids,
                lod2_triangle_min=lower,
                lod2_triangle_max=upper,
            ),
        )
        for level in (0, 1):
            descriptor = source_record.lod[str(level)]
            reused.append(
                ReusedMeshLodV2(
                    asset_id=recipe.asset_id,
                    lod=level,
                    glb_object_path=descriptor.glb_object_path,
                    glb_sha256=descriptor.glb_sha256,
                    glb_bytes=descriptor.glb_bytes,
                    triangle_count=descriptor.triangle_count,
                    primitive_count=descriptor.primitive_count,
                    material_slot_ids=descriptor.material_slot_ids,
                    local_enu_aabb=descriptor.aabb,
                    recipe_id=ASSET_RECIPE_CONTRACTS[recipe.asset_id][1],
                ),
            )
    unsigned = {
        "schema_version": MESH_ASSET_BUILD_V2_SCHEMA,
        "synthetic": True,
        "verification_level": "L0",
        "coordinate_encoding": GLB_COORDINATE_ENCODING,
        "source_v1_bundle_id": source_bundle.bundle_id,
        "source_v1_manifest_sha256": hashlib.sha256(
            canonical_mesh_asset_bundle_bytes(source_bundle),
        ).hexdigest(),
        "material_bundle_id": material_bundle.bundle_id,
        "material_bundle_manifest_sha256": material_manifest_sha256,
        "material_algorithm_id": material_bundle.algorithm_id,
        "material_input_registry": _material_input_registry(material_bundle),
        "foliage_atlas_set": foliage_atlas_set.manifest,
        "asset_registry_sha256": hashlib.sha256(registry_raw).hexdigest(),
        "blender_identity": blender_identity,
        "builder_script_sha256": hashlib.sha256(builder_raw).hexdigest(),
        "recipes": tuple(recipes),
        "reused_lods": tuple(reused),
        "lod_levels_to_build": (2,),
        "alpha_cutoff": ALPHA_CUTOFF,
        "sampler": MeshTextureSamplerV2(),
    }
    build_id = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
    try:
        request = MeshAssetBuildRequestV2(
            build_id=build_id,
            **unsigned,
        )
    except ValidationError as exc:
        raise MeshAssetBuildError(
            f"near mesh build request is invalid: {exc}",
        ) from exc
    if (
        _read_regular_file(registry_path, label="mesh asset registry")
        != registry_raw
        or _read_regular_file(builder, label="near mesh builder script")
        != builder_raw
        or load_mesh_asset_bundle(source_root) != source_bundle
        or load_material_bundle(material_root) != material_bundle
    ):
        raise MeshAssetBuildError(
            "near mesh build inputs changed during request creation",
        )
    _verify_prepared_atlas_set(
        foliage_atlas_set.root,
        foliage_atlas_set.manifest,
    )
    return request
