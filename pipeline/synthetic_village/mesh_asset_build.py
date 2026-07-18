"""Path-free build identities for replaceable textured mesh templates."""

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
from pipeline.synthetic_village.local_textured_preview import LocalBlenderIdentity
from pipeline.synthetic_village.material_bundle import (
    MATERIAL_BUNDLE_MANIFEST,
    MaterialAlgorithmId,
    MaterialBundleError,
    canonical_material_bundle_bytes,
    load_material_bundle,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    GLB_COORDINATE_ENCODING,
    MESH_TRIANGLE_BUDGETS,
    Bounds3,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
AssetKind = Literal["building", "vegetation", "prop"]

MESH_ASSET_BUILD_SCHEMA = "nantai.synthetic-village.mesh-asset-build.v1"
MESH_ASSET_BUILD_REPORT_SCHEMA = (
    "nantai.synthetic-village.mesh-asset-build-report.v1"
)
MAX_BUILD_INPUT_BYTES = 64 * 1024 * 1024

ASSET_RECIPE_CONTRACTS: dict[
    str,
    tuple[AssetKind, str, tuple[str, ...]],
] = {
    "fence_wood_01": (
        "prop",
        "weathered-timber-fence-v1",
        ("material-weathered-timber-01",),
    ),
    "house_barn_01": (
        "building",
        "dark-timber-barn-v1",
        (
            "material-dark-timber-01",
            "material-gray-roof-tile-01",
            "material-weathered-timber-01",
        ),
    ),
    "house_stone_01": (
        "building",
        "fieldstone-house-v1",
        (
            "material-dark-timber-01",
            "material-fieldstone-01",
            "material-gray-roof-tile-01",
        ),
    ),
    "house_thatch_01": (
        "building",
        "rammed-earth-thatch-house-v1",
        (
            "material-dark-timber-01",
            "material-rammed-earth-01",
            "material-woven-bamboo-01",
        ),
    ),
    "house_wood_01": (
        "building",
        "weathered-timber-house-v1",
        (
            "material-gray-roof-tile-01",
            "material-weathered-timber-01",
        ),
    ),
    "house_wood_02": (
        "building",
        "plaster-timber-house-v1",
        (
            "material-dark-timber-01",
            "material-gray-roof-tile-01",
            "material-pale-plaster-01",
        ),
    ),
    "stone_lamp_01": (
        "prop",
        "stone-metal-lamp-v1",
        (
            "material-aged-metal-01",
            "material-fieldstone-01",
        ),
    ),
    "stone_wall_01": (
        "prop",
        "dry-stone-wall-v1",
        ("material-dry-stone-wall-01",),
    ),
    "tree_bamboo_01": (
        "vegetation",
        "clustered-bamboo-v1",
        (
            "material-bamboo-leaf-01",
            "material-bamboo-stem-01",
        ),
    ),
    "tree_broadleaf_01": (
        "vegetation",
        "humid-broadleaf-v1",
        (
            "material-broadleaf-bark-01",
            "material-broadleaf-canopy-01",
        ),
    ),
    "tree_pine_01": (
        "vegetation",
        "layered-pine-v1",
        (
            "material-orchard-bark-01",
            "material-orchard-leaf-01",
        ),
    ),
}
EXPECTED_ASSET_IDS = tuple(sorted(ASSET_RECIPE_CONTRACTS))


class MeshAssetBuildError(RuntimeError):
    """Mesh-template build identity or evidence cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MeshAssetRecipe(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: AssetKind
    footprint_m: tuple[float, float, float]
    recipe_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    lod_triangle_budgets: tuple[int, int, int]

    @model_validator(mode="after")
    def _exact_recipe_contract(self) -> MeshAssetRecipe:
        if self.asset_id not in ASSET_RECIPE_CONTRACTS:
            raise ValueError("mesh asset recipe ID is not registered")
        expected_kind, expected_recipe_id, expected_slots = (
            ASSET_RECIPE_CONTRACTS[self.asset_id]
        )
        if (
            self.kind != expected_kind
            or self.recipe_id != expected_recipe_id
            or self.material_slot_ids != expected_slots
            or self.lod_triangle_budgets
            != tuple(
                MESH_TRIANGLE_BUDGETS[self.kind][level]
                for level in (0, 1, 2)
            )
        ):
            raise ValueError("mesh asset recipe does not match its exact contract")
        if not all(
            math.isfinite(value) and value > 0
            for value in self.footprint_m
        ):
            raise ValueError("mesh asset recipe footprint must be finite and positive")
        return self


class MeshAssetBuildRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-asset-build.v1"
    ] = MESH_ASSET_BUILD_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    coordinate_encoding: Literal[
        "three-east-up-negative-north"
    ] = GLB_COORDINATE_ENCODING
    asset_registry_sha256: Sha256
    material_bundle_id: Sha256
    material_bundle_manifest_sha256: Sha256
    material_algorithm_id: MaterialAlgorithmId
    material_input_registry: tuple[MaterialInputRecord, ...] = Field(
        min_length=24,
        max_length=24,
    )
    blender_identity: LocalBlenderIdentity
    builder_script_sha256: Sha256
    recipes: tuple[MeshAssetRecipe, ...] = Field(min_length=11, max_length=11)
    lod_levels: tuple[Literal[0], Literal[1], Literal[2]] = (0, 1, 2)

    @property
    def asset_ids(self) -> tuple[str, ...]:
        return tuple(recipe.asset_id for recipe in self.recipes)

    @model_validator(mode="after")
    def _complete_path_free_request(self) -> MeshAssetBuildRequest:
        if self.asset_ids != EXPECTED_ASSET_IDS:
            raise ValueError("mesh build recipes are not the exact sorted asset set")
        material_ids = tuple(row.slot_id for row in self.material_input_registry)
        if (
            material_ids != tuple(sorted(material_ids))
            or len(set(material_ids)) != 24
        ):
            raise ValueError(
                "mesh build material inputs are not the exact sorted 24-slot set",
            )
        registered_materials = set(material_ids)
        if any(
            not set(recipe.material_slot_ids) <= registered_materials
            for recipe in self.recipes
        ):
            raise ValueError(
                "mesh asset recipe references an unregistered material input",
            )
        expected_id = hashlib.sha256(
            canonical_mesh_asset_build_request_bytes(
                self,
                exclude_build_id=True,
            ),
        ).hexdigest()
        if self.build_id != expected_id:
            raise ValueError("mesh build ID does not match canonical request inputs")
        return self


class MeshAssetBuildReportRow(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    lod: Literal[0, 1, 2]
    artifact_path: str = Field(min_length=1)
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1)
    triangle_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    local_enu_aabb: Bounds3

    @model_validator(mode="after")
    def _canonical_artifact(self) -> MeshAssetBuildReportRow:
        if self.asset_id not in ASSET_RECIPE_CONTRACTS:
            raise ValueError("mesh build artifact asset is not registered")
        expected = f"artifacts/{self.asset_id}/lod{self.lod}.glb"
        parsed = PurePosixPath(self.artifact_path)
        if (
            self.artifact_path != expected
            or parsed.as_posix() != self.artifact_path
            or parsed.is_absolute()
        ):
            raise ValueError("mesh build artifact path is not canonical")
        if (
            self.material_slot_ids != tuple(sorted(self.material_slot_ids))
            or len(set(self.material_slot_ids)) != len(self.material_slot_ids)
        ):
            raise ValueError(
                "mesh build artifact material slots must be sorted and unique",
            )
        return self


class MeshAssetBuildReport(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-asset-build-report.v1"
    ] = MESH_ASSET_BUILD_REPORT_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    coordinate_encoding: Literal[
        "three-east-up-negative-north"
    ] = GLB_COORDINATE_ENCODING
    blender_identity: LocalBlenderIdentity
    builder_script_sha256: Sha256
    artifacts: tuple[MeshAssetBuildReportRow, ...] = Field(
        min_length=33,
        max_length=33,
    )

    @model_validator(mode="after")
    def _complete_artifact_matrix(self) -> MeshAssetBuildReport:
        actual = tuple((row.asset_id, row.lod) for row in self.artifacts)
        expected = tuple(
            (asset_id, lod)
            for asset_id in EXPECTED_ASSET_IDS
            for lod in (0, 1, 2)
        )
        if actual != expected:
            raise ValueError(
                "mesh build report does not contain the exact sorted 33 artifacts",
            )
        for asset_id in EXPECTED_ASSET_IDS:
            triangles = tuple(
                row.triangle_count
                for row in self.artifacts
                if row.asset_id == asset_id
            )
            if not triangles[0] < triangles[1] < triangles[2]:
                raise ValueError(
                    "mesh build report LOD triangles must increase strictly",
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


def canonical_mesh_asset_build_request_bytes(
    request: MeshAssetBuildRequest,
    *,
    exclude_build_id: bool = False,
) -> bytes:
    payload = request.model_dump(mode="json")
    if exclude_build_id:
        payload.pop("build_id")
    return _canonical_json_bytes(payload)


def canonical_mesh_asset_build_report_bytes(
    report: MeshAssetBuildReport,
) -> bytes:
    return _canonical_json_bytes(report)


def _is_linklike(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _real_directory(path: Path, *, label: str) -> Path:
    path = Path(path).expanduser().absolute()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MeshAssetBuildError(f"{label} is unavailable") from exc
    if _is_linklike(path) or not path.is_dir() or resolved != path:
        raise MeshAssetBuildError(f"{label} is redirected or not a real directory")
    return path


def _read_regular_file(path: Path, *, label: str) -> bytes:
    path = Path(path).expanduser().absolute()
    _real_directory(path.parent, label=f"{label} directory")
    try:
        resolved = path.resolve(strict=True)
        before = path.stat()
        if (
            _is_linklike(path)
            or not path.is_file()
            or resolved != path
            or before.st_size <= 0
            or before.st_size > MAX_BUILD_INPUT_BYTES
        ):
            raise MeshAssetBuildError(f"{label} is not a bounded regular file")
        with path.open("rb") as stream:
            payload = stream.read(MAX_BUILD_INPUT_BYTES + 1)
        after = path.stat()
    except MeshAssetBuildError:
        raise
    except OSError as exc:
        raise MeshAssetBuildError(f"{label} cannot be read stably") from exc
    signatures = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ), (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if (
        signatures[0] != signatures[1]
        or len(payload) != before.st_size
        or len(payload) > MAX_BUILD_INPUT_BYTES
    ):
        raise MeshAssetBuildError(f"{label} changed during bounded read")
    return payload


def _reject_duplicate_keys(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _material_input_registry(bundle) -> tuple[MaterialInputRecord, ...]:
    return tuple(
        MaterialInputRecord(
            slot_id=record.slot_id,
            source_sha256=record.source_sha256,
            base_color_sha256=record.base_color.sha256,
            normal_sha256=record.normal.sha256,
            orm_sha256=record.orm.sha256,
            width=record.base_color.width,
            height=record.base_color.height,
            uv_policy=record.uv_policy,
            nominal_tile_m=record.nominal_tile_m,
            normal_strength=record.normal_strength,
        )
        for record in bundle.records
    )


def _recipes_from_registry(registry: dict[str, object]) -> tuple[MeshAssetRecipe, ...]:
    if set(registry) != {"schema_version", "assets"} or registry.get(
        "schema_version",
    ) != 2:
        raise MeshAssetBuildError(
            "asset registry wrapper does not match schema version 2",
        )
    assets = registry.get("assets")
    if not isinstance(assets, dict) or tuple(sorted(assets)) != EXPECTED_ASSET_IDS:
        raise MeshAssetBuildError(
            "asset registry does not contain the exact mesh-template asset set",
        )
    recipes = []
    try:
        for asset_id in EXPECTED_ASSET_IDS:
            row = assets[asset_id]
            if not isinstance(row, dict):
                raise ValueError("asset registry row is not an object")
            kind, recipe_id, slots = ASSET_RECIPE_CONTRACTS[asset_id]
            if row.get("kind") != kind:
                raise ValueError("asset registry kind disagrees with recipe")
            recipes.append(
                MeshAssetRecipe(
                    asset_id=asset_id,
                    kind=kind,
                    footprint_m=tuple(row["footprint_m"]),
                    recipe_id=recipe_id,
                    material_slot_ids=slots,
                    lod_triangle_budgets=tuple(
                        MESH_TRIANGLE_BUDGETS[kind][level]
                        for level in (0, 1, 2)
                    ),
                ),
            )
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise MeshAssetBuildError(
            f"asset registry cannot define mesh recipes: {exc}",
        ) from exc
    return tuple(recipes)


def build_mesh_asset_request(
    *,
    repo_root: Path,
    material_bundle_root: Path,
    builder_script: Path,
    blender_identity: LocalBlenderIdentity,
) -> MeshAssetBuildRequest:
    """Bind exact material, registry, builder, and Blender evidence without paths."""

    repo_root = _real_directory(Path(repo_root), label="repository root")
    registry_path = repo_root / "assets/registry.json"
    selected_builder = Path(builder_script)
    if not selected_builder.is_absolute():
        selected_builder = repo_root / selected_builder
    selected_builder = selected_builder.absolute()
    try:
        selected_builder.relative_to(repo_root)
    except ValueError as exc:
        raise MeshAssetBuildError("mesh builder script escapes the repository") from exc

    registry_raw = _read_regular_file(
        registry_path,
        label="mesh asset registry",
    )
    builder_raw = _read_regular_file(
        selected_builder,
        label="mesh builder script",
    )
    try:
        registry = json.loads(
            registry_raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(registry, dict):
            raise ValueError("asset registry root is not an object")
        material_bundle = load_material_bundle(material_bundle_root)
    except (
        MaterialBundleError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise MeshAssetBuildError(
            f"mesh build inputs cannot be trusted: {exc}",
        ) from exc

    unsigned = {
        "schema_version": MESH_ASSET_BUILD_SCHEMA,
        "synthetic": True,
        "verification_level": "L0",
        "coordinate_encoding": GLB_COORDINATE_ENCODING,
        "asset_registry_sha256": hashlib.sha256(registry_raw).hexdigest(),
        "material_bundle_id": material_bundle.bundle_id,
        "material_bundle_manifest_sha256": hashlib.sha256(
            canonical_material_bundle_bytes(material_bundle),
        ).hexdigest(),
        "material_algorithm_id": material_bundle.algorithm_id,
        "material_input_registry": _material_input_registry(material_bundle),
        "blender_identity": blender_identity,
        "builder_script_sha256": hashlib.sha256(builder_raw).hexdigest(),
        "recipes": _recipes_from_registry(registry),
        "lod_levels": (0, 1, 2),
    }
    build_id = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
    try:
        request = MeshAssetBuildRequest(build_id=build_id, **unsigned)
    except ValidationError as exc:
        raise MeshAssetBuildError(
            f"mesh build request is invalid: {exc}",
        ) from exc

    if (
        _read_regular_file(registry_path, label="mesh asset registry")
        != registry_raw
        or _read_regular_file(selected_builder, label="mesh builder script")
        != builder_raw
    ):
        raise MeshAssetBuildError("mesh build inputs changed during request creation")
    try:
        current_material_bundle = load_material_bundle(material_bundle_root)
    except MaterialBundleError as exc:
        raise MeshAssetBuildError(
            "material bundle changed during request creation",
        ) from exc
    if current_material_bundle != material_bundle:
        raise MeshAssetBuildError(
            "material bundle changed during request creation",
        )
    manifest_raw = _read_regular_file(
        Path(material_bundle_root) / MATERIAL_BUNDLE_MANIFEST,
        label="material bundle manifest",
    )
    if hashlib.sha256(manifest_raw).hexdigest() != (
        request.material_bundle_manifest_sha256
    ):
        raise MeshAssetBuildError(
            "material bundle manifest changed during request creation",
        )
    return request
