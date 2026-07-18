"""Path-free build identities for replaceable textured mesh templates."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import uuid
from dataclasses import dataclass
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

from pipeline.studio_jobs import JobContractError, ProjectFileLock
from pipeline.synthetic_village import canary
from pipeline.synthetic_village.canary import CanaryBuildError, MaterialInputRecord
from pipeline.synthetic_village.glb_material_audit import (
    ExpectedGlbMaterial,
    GlbMaterialAuditError,
    audit_textured_glb,
)
from pipeline.synthetic_village.local_textured_preview import (
    LocalBlenderIdentity,
    LocalTexturedPreviewError,
    probe_local_blender_identity,
)
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
    MeshAssetBundleResult,
    MeshAssetTemplateSource,
    load_mesh_asset_bundle,
    measure_mesh_template_enu_bounds,
    publish_mesh_asset_bundle,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
AssetKind = Literal["building", "vegetation", "prop"]

MESH_ASSET_BUILD_SCHEMA = "nantai.synthetic-village.mesh-asset-build.v1"
MESH_ASSET_BUILD_REPORT_SCHEMA = (
    "nantai.synthetic-village.mesh-asset-build-report.v1"
)
MAX_BUILD_INPUT_BYTES = 64 * 1024 * 1024
MAX_MESH_BUILD_REPORT_BYTES = 16 * 1024 * 1024
MAX_MESH_BUILD_ARTIFACT_BYTES = 128 * 1024 * 1024
DEFAULT_MESH_BUILD_TIMEOUT_SECONDS = 30 * 60

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


@dataclass(frozen=True)
class MeshAssetBuildResult:
    request: MeshAssetBuildRequest
    report: MeshAssetBuildReport
    bundle: MeshAssetBundleResult
    stdout: str
    stderr: str


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


def _read_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int = MAX_BUILD_INPUT_BYTES,
) -> bytes:
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
            or before.st_size > maximum_bytes
        ):
            raise MeshAssetBuildError(f"{label} is not a bounded regular file")
        with path.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
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
        or len(payload) > maximum_bytes
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


def load_mesh_asset_build_report(path: Path) -> MeshAssetBuildReport:
    """Load one canonical path-free builder report with duplicate-key rejection."""

    raw = _read_regular_file(
        path,
        label="mesh build report",
        maximum_bytes=MAX_MESH_BUILD_REPORT_BYTES,
    )
    try:
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        report = MeshAssetBuildReport.model_validate_json(raw)
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise MeshAssetBuildError(f"mesh build report is invalid: {exc}") from exc
    if raw != canonical_mesh_asset_build_report_bytes(report):
        raise MeshAssetBuildError("mesh build report is not canonical")
    return report


def _prepare_real_directory(path: Path, *, label: str) -> Path:
    path = Path(path).expanduser().absolute()
    cursor = path
    missing: list[Path] = []
    while not cursor.exists():
        if _is_linklike(cursor):
            raise MeshAssetBuildError(f"{label} has a redirected ancestor")
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise MeshAssetBuildError(f"{label} has no real existing ancestor")
        cursor = parent
    _real_directory(cursor, label=f"{label} ancestor")
    for directory in reversed(missing):
        try:
            directory.mkdir(exist_ok=False)
        except FileExistsError:
            pass
        _real_directory(directory, label=label)
    return _real_directory(path, label=label)


def _resolve_builder_script(repo_root: Path, builder_script: Path) -> Path:
    selected = Path(builder_script)
    if not selected.is_absolute():
        selected = repo_root / selected
    selected = selected.absolute()
    try:
        selected.relative_to(repo_root)
    except ValueError as exc:
        raise MeshAssetBuildError("mesh builder script escapes the repository") from exc
    _read_regular_file(selected, label="mesh builder script")
    return selected


def _run_blender_process(
    *,
    repo_root: Path,
    executable: Path,
    builder_script: Path,
    request_path: Path,
    material_root: Path,
    staging: Path,
    invocation_root: Path,
    timeout_seconds: int,
) -> tuple[int, str, str]:
    argv = [
        str(executable),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "17",
        "--python",
        builder_script.relative_to(repo_root).as_posix(),
        "--",
        "--request",
        str(request_path),
        "--materials",
        str(material_root),
        "--staging",
        str(staging),
    ]
    try:
        environment = canary._minimum_blender_environment(invocation_root)
        timeout_error = None
        with (
            canary._BoundedPipeCapture("stdout") as stdout,
            canary._BoundedPipeCapture("stderr") as stderr,
        ):
            try:
                completed = subprocess.run(
                    argv,
                    check=False,
                    shell=False,
                    cwd=str(repo_root),
                    env=environment,
                    timeout=timeout_seconds,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout.writer,
                    stderr=stderr.writer,
                )
            except subprocess.TimeoutExpired as exc:
                timeout_error = exc
                completed = None
        if timeout_error is not None:
            raise MeshAssetBuildError(
                f"Blender mesh build exceeded the {timeout_seconds}-second timeout",
            ) from timeout_error
        if completed is None:
            raise MeshAssetBuildError(
                "Blender mesh build returned no completion status",
            )
        return completed.returncode, stdout.text(), stderr.text()
    except MeshAssetBuildError:
        raise
    except (OSError, canary.CanaryBuildError) as exc:
        raise MeshAssetBuildError(
            f"verified Blender mesh process could not run: {exc}",
        ) from exc


def _validate_staging_entries(
    staging: Path,
    report: MeshAssetBuildReport,
) -> None:
    staging = _real_directory(staging, label="mesh builder staging")
    expected_files = {
        "build-report.json",
        *(row.artifact_path for row in report.artifacts),
    }
    expected_directories = {
        "artifacts",
        *(f"artifacts/{asset_id}" for asset_id in EXPECTED_ASSET_IDS),
    }
    try:
        entries = tuple(staging.rglob("*"))
    except OSError as exc:
        raise MeshAssetBuildError("mesh builder staging cannot be enumerated") from exc
    if any(_is_linklike(path) for path in entries):
        raise MeshAssetBuildError("mesh builder staging contains redirected output")
    actual_files = {
        path.relative_to(staging).as_posix()
        for path in entries
        if path.is_file()
    }
    actual_directories = {
        path.relative_to(staging).as_posix()
        for path in entries
        if path.is_dir()
    }
    if (
        actual_files != expected_files
        or actual_directories != expected_directories
        or len(entries) != len(actual_files) + len(actual_directories)
    ):
        raise MeshAssetBuildError(
            "mesh builder staging contains missing or unexpected outputs",
        )


def _report_sources(
    *,
    request: MeshAssetBuildRequest,
    report: MeshAssetBuildReport,
    staging: Path,
) -> tuple[MeshAssetTemplateSource, ...]:
    if (
        report.build_id != request.build_id
        or report.blender_identity != request.blender_identity
        or report.builder_script_sha256 != request.builder_script_sha256
    ):
        raise MeshAssetBuildError(
            "mesh build report identity disagrees with its request",
        )
    material_inputs = {
        row.slot_id: row for row in request.material_input_registry
    }
    recipe_by_id = {recipe.asset_id: recipe for recipe in request.recipes}
    rows_by_asset: dict[str, list[MeshAssetBuildReportRow]] = {
        asset_id: [] for asset_id in request.asset_ids
    }
    for row in report.artifacts:
        recipe = recipe_by_id[row.asset_id]
        if row.material_slot_ids != recipe.material_slot_ids:
            raise MeshAssetBuildError(
                "mesh build report material evidence disagrees with its recipe",
            )
        expected_materials = tuple(
            ExpectedGlbMaterial(
                slot_id=slot_id,
                source_sha256=material_inputs[slot_id].source_sha256,
                bundle_id=request.material_bundle_id,
                algorithm_id=request.material_algorithm_id,
            )
            for slot_id in recipe.material_slot_ids
        )
        artifact = staging / row.artifact_path
        payload = _read_regular_file(
            artifact,
            label="mesh build GLB artifact",
            maximum_bytes=MAX_MESH_BUILD_ARTIFACT_BYTES,
        )
        try:
            audit = audit_textured_glb(
                artifact,
                expected_materials=expected_materials,
            )
        except GlbMaterialAuditError as exc:
            raise MeshAssetBuildError(
                "mesh build GLB material or geometry audit failed",
            ) from exc
        measured_bounds = measure_mesh_template_enu_bounds(payload)
        if (
            audit.glb_sha256 != row.glb_sha256
            or audit.byte_count != row.glb_bytes
            or audit.triangle_count != row.triangle_count
            or audit.primitive_count != row.primitive_count
            or audit.slot_ids != row.material_slot_ids
            or any(
                abs(measured - declared) > 1e-5
                for measured, declared in zip(
                    (
                        *measured_bounds.min,
                        *measured_bounds.max,
                    ),
                    (
                        *row.local_enu_aabb.min,
                        *row.local_enu_aabb.max,
                    ),
                    strict=True,
                )
            )
        ):
            raise MeshAssetBuildError(
                "mesh build report evidence disagrees with independent GLB audit",
            )
        rows_by_asset[row.asset_id].append(row)
    return tuple(
        MeshAssetTemplateSource(
            asset_id=recipe.asset_id,
            kind=recipe.kind,
            footprint_m=recipe.footprint_m,
            lod_paths=tuple(
                staging / row.artifact_path
                for row in rows_by_asset[recipe.asset_id]
            ),
            material_slot_ids=tuple(
                row.material_slot_ids
                for row in rows_by_asset[recipe.asset_id]
            ),
        )
        for recipe in request.recipes
    )


def _cleanup_owned_directory(
    path: Path | None,
    *,
    work_root: Path,
    prefix: str,
) -> None:
    if path is None:
        return
    candidate = Path(path).absolute()
    if (
        candidate.parent != work_root
        or not candidate.name.startswith(prefix)
        or _is_linklike(candidate)
        or not candidate.exists()
        or not candidate.is_dir()
    ):
        return
    shutil.rmtree(candidate, ignore_errors=True)


def run_mesh_asset_build(
    *,
    repo_root: Path,
    material_bundle_root: Path,
    blender_executable: Path,
    builder_script: Path,
    work_root: Path,
    publication_root: Path,
    timeout_seconds: int = DEFAULT_MESH_BUILD_TIMEOUT_SECONDS,
) -> MeshAssetBuildResult:
    """Snapshot, invoke, cross-check, and publish one exact mesh-template build."""

    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 24 * 60 * 60
    ):
        raise MeshAssetBuildError(
            "mesh build timeout must be an integer from 1 to 86400 seconds",
        )
    repo_root = _real_directory(Path(repo_root), label="repository root")
    material_bundle_root = _real_directory(
        Path(material_bundle_root),
        label="material bundle root",
    )
    work_root = _prepare_real_directory(
        Path(work_root),
        label="mesh build work root",
    )
    publication_root = Path(publication_root).expanduser().absolute()
    builder_path = _resolve_builder_script(repo_root, builder_script)
    blender_path = Path(blender_executable).expanduser().absolute()
    invocation_root: Path | None = None
    staging: Path | None = None
    try:
        with ProjectFileLock(
            work_root / ".mesh-build.lock",
            role="writer",
        ):
            try:
                blender_snapshot = canary._snapshot_regular_file(blender_path)
                builder_snapshot = canary._snapshot_regular_file(builder_path)
                registry_snapshot = canary._snapshot_regular_file(
                    repo_root / "assets/registry.json",
                )
                material_snapshots = canary._collect_material_bundle_snapshots(
                    material_bundle_root,
                )
                blender_identity = probe_local_blender_identity(blender_path)
            except (
                CanaryBuildError,
                LocalTexturedPreviewError,
            ) as exc:
                raise MeshAssetBuildError(
                    f"mesh build inputs cannot be snapshotted: {exc}",
                ) from exc
            if blender_identity.executable_sha256 != blender_snapshot.sha256:
                raise MeshAssetBuildError(
                    "local Blender identity disagrees with executable bytes",
                )
            request = build_mesh_asset_request(
                repo_root=repo_root,
                material_bundle_root=material_bundle_root,
                builder_script=builder_path,
                blender_identity=blender_identity,
            )
            try:
                canary._verify_snapshots_unchanged(
                    (
                        blender_snapshot,
                        builder_snapshot,
                        registry_snapshot,
                        *material_snapshots,
                    ),
                )
            except CanaryBuildError as exc:
                raise MeshAssetBuildError(str(exc)) from exc

            nonce = uuid.uuid4().hex
            invocation_root = work_root / f".mesh-invocation-{nonce}"
            staging = work_root / f".mesh-builder-{nonce}"
            invocation_root.mkdir(exist_ok=False)
            if staging.exists() or _is_linklike(staging):
                raise MeshAssetBuildError(
                    "mesh builder staging destination must start absent",
                )
            request_path = invocation_root / "request.json"
            try:
                canary._write_new_file(
                    request_path,
                    canonical_mesh_asset_build_request_bytes(request),
                )
                request_snapshot = canary._snapshot_regular_file(request_path)
                invocation_material_snapshots = canary.snapshot_material_inputs(
                    request=request,
                    material_bundle_root=material_bundle_root,
                    invocation_root=invocation_root,
                )
            except CanaryBuildError as exc:
                raise MeshAssetBuildError(
                    f"mesh invocation snapshot failed: {exc}",
                ) from exc
            material_root = invocation_root / "material-inputs"
            immutable_snapshots = (
                blender_snapshot,
                builder_snapshot,
                registry_snapshot,
                *material_snapshots,
                request_snapshot,
                *invocation_material_snapshots,
            )
            try:
                canary._verify_snapshots_unchanged(immutable_snapshots)
            except CanaryBuildError as exc:
                raise MeshAssetBuildError(str(exc)) from exc

            returncode, stdout, stderr = _run_blender_process(
                repo_root=repo_root,
                executable=blender_path,
                builder_script=builder_path,
                request_path=request_path,
                material_root=material_root,
                staging=staging,
                invocation_root=invocation_root,
                timeout_seconds=timeout_seconds,
            )
            try:
                canary._verify_snapshots_unchanged(immutable_snapshots)
            except CanaryBuildError as exc:
                raise MeshAssetBuildError(str(exc)) from exc
            if returncode != 0:
                raise MeshAssetBuildError(
                    f"Blender mesh build failed with exit code {returncode}",
                )
            report = load_mesh_asset_build_report(staging / "build-report.json")
            _validate_staging_entries(staging, report)
            sources = _report_sources(
                request=request,
                report=report,
                staging=staging,
            )
            try:
                canary._verify_snapshots_unchanged(immutable_snapshots)
            except CanaryBuildError as exc:
                raise MeshAssetBuildError(str(exc)) from exc
        bundle = publish_mesh_asset_bundle(
            material_bundle_root=material_bundle_root,
            sources=sources,
            publication_root=publication_root,
            work_root=work_root,
            build_tool_id=f"mesh-asset-build-{request.build_id}",
            verification_level="L0",
        )
        published = load_mesh_asset_bundle(bundle.final_directory)
        if (
            published.bundle_id != bundle.bundle_id
            or len(published.records) != bundle.record_count
            or tuple(record.asset_id for record in published.records)
            != request.asset_ids
        ):
            raise MeshAssetBuildError(
                "published mesh asset bundle disagrees with the build request",
            )
        return MeshAssetBuildResult(
            request=request,
            report=report,
            bundle=bundle,
            stdout=stdout,
            stderr=stderr,
        )
    except MeshAssetBuildError:
        raise
    except JobContractError as exc:
        raise MeshAssetBuildError(f"mesh build lock is unavailable: {exc}") from exc
    except (OSError, RuntimeError, ValidationError, ValueError) as exc:
        raise MeshAssetBuildError(f"mesh build failed safely: {exc}") from exc
    finally:
        _cleanup_owned_directory(
            staging,
            work_root=work_root,
            prefix=".mesh-builder-",
        )
        _cleanup_owned_directory(
            invocation_root,
            work_root=work_root,
            prefix=".mesh-invocation-",
        )
