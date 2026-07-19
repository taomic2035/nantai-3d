"""Immutable v2 mesh-template identity with shared texture dependencies."""

from __future__ import annotations

import hashlib
import io
import math
import stat
import threading
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import (
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from pipeline.synthetic_village.glb_material_audit import ExpectedGlbMaterial
from pipeline.synthetic_village.mesh_asset_bundle import (
    GLB_COORDINATE_ENCODING,
    MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES,
    MAX_MESH_TEMPLATE_GLB_BYTES,
    MESH_ASSET_BUNDLE_MANIFEST,
    Bounds3,
    FrozenModel,
    MeshAssetBundleError,
    _canonical_json_bytes,
    _is_linklike,
    _read_stable_file,
    _real_directory,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

MESH_ASSET_BUNDLE_V2_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v2"
MESH_NEAR_ALGORITHM_ID = "synthetic-template-mesh-near-v2"
MESH_V1_ALGORITHM_ID = "synthetic-template-mesh-v1"
MAX_MESH_TEXTURE_BYTES = 32 * 1024 * 1024
LOD2_TRIANGLE_BANDS = {
    "building": (8_000, 15_000),
    "vegetation": (6_000, 12_000),
    "prop": (1_000, 4_000),
}


class TextureObjectV2(FrozenModel):
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1, le=MAX_MESH_TEXTURE_BYTES)
    mime_type: Literal["image/png"] = "image/png"
    width: Literal[1024] = 1024
    height: Literal[1024] = 1024

    @model_validator(mode="after")
    def _content_addressed_png(self) -> TextureObjectV2:
        expected = f"textures/{self.sha256}.png"
        parsed = PurePosixPath(self.object_path)
        if (
            self.object_path != expected
            or parsed.as_posix() != self.object_path
            or parsed.is_absolute()
        ):
            raise ValueError("mesh texture object path must be content-addressed")
        return self


class TextureBindingV2(FrozenModel):
    uri: str = Field(min_length=1)
    sha256: Sha256
    role: Literal["base_color", "normal", "orm"]
    colour_space: Literal["srgb", "non-color"]
    material_slot_id: str = Field(min_length=1)
    derivation_algorithm_id: str = Field(min_length=1)
    min_filter: Literal[9987] = 9987
    mag_filter: Literal[9729] = 9729
    wrap_s: Literal[10497] = 10497
    wrap_t: Literal[10497] = 10497

    @model_validator(mode="after")
    def _exact_texture_semantics(self) -> TextureBindingV2:
        expected = f"../textures/{self.sha256}.png"
        parsed = PurePosixPath(self.uri)
        if (
            self.uri != expected
            or parsed.as_posix() != self.uri
            or parsed.is_absolute()
        ):
            raise ValueError("mesh texture binding URI must be content-addressed")
        expected_colour_space = (
            "srgb" if self.role == "base_color" else "non-color"
        )
        if self.colour_space != expected_colour_space:
            raise ValueError("mesh texture role has the wrong colour space")
        return self


def _binding_sort_key(
    binding: TextureBindingV2,
) -> tuple[str, str, str, str]:
    return (
        binding.material_slot_id,
        binding.role,
        binding.sha256,
        binding.derivation_algorithm_id,
    )


class MeshTemplateLodV2(FrozenModel):
    glb_object_path: str = Field(min_length=1)
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1, le=MAX_MESH_TEMPLATE_GLB_BYTES)
    triangle_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    aabb: Bounds3
    mesh_algorithm_id: Literal[
        "synthetic-template-mesh-v1",
        "synthetic-template-mesh-near-v2",
    ]
    recipe_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    texture_storage: Literal["embedded", "shared-content-addressed"]
    texture_bindings: tuple[TextureBindingV2, ...] = ()

    @model_validator(mode="after")
    def _content_addressed_complete_lod(self) -> MeshTemplateLodV2:
        expected = f"objects/{self.glb_sha256}.glb"
        parsed = PurePosixPath(self.glb_object_path)
        if (
            self.glb_object_path != expected
            or parsed.as_posix() != self.glb_object_path
            or parsed.is_absolute()
        ):
            raise ValueError("mesh template path must be a content-addressed GLB")
        if (
            tuple(sorted(self.material_slot_ids)) != self.material_slot_ids
            or len(set(self.material_slot_ids)) != len(self.material_slot_ids)
        ):
            raise ValueError("mesh template material slots must be sorted and unique")
        if (
            tuple(sorted(self.texture_bindings, key=_binding_sort_key))
            != self.texture_bindings
            or len(set(_binding_sort_key(row) for row in self.texture_bindings))
            != len(self.texture_bindings)
        ):
            raise ValueError("mesh template texture bindings must be sorted and unique")
        if self.texture_storage == "embedded":
            if self.texture_bindings:
                raise ValueError("embedded mesh template cannot declare texture bindings")
            if self.mesh_algorithm_id != MESH_V1_ALGORITHM_ID:
                raise ValueError("embedded mesh template must retain the v1 algorithm")
        else:
            if not self.texture_bindings:
                raise ValueError("shared mesh template requires texture bindings")
            if self.mesh_algorithm_id != MESH_NEAR_ALGORITHM_ID:
                raise ValueError("shared mesh template must use the near-v2 algorithm")
            binding_slots = {row.material_slot_id for row in self.texture_bindings}
            if binding_slots != set(self.material_slot_ids):
                raise ValueError(
                    "shared mesh template texture bindings must cover exact material slots",
                )
            for slot_id in self.material_slot_ids:
                roles = {
                    row.role
                    for row in self.texture_bindings
                    if row.material_slot_id == slot_id
                }
                if roles != {"base_color", "normal", "orm"}:
                    raise ValueError(
                        "shared mesh template material requires exact texture roles",
                    )
        return self


class MeshAssetRecordV2(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: Literal["building", "vegetation", "prop"]
    footprint_m: tuple[float, float, float]
    lod: dict[Literal["0", "1", "2"], MeshTemplateLodV2]
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"

    @model_validator(mode="after")
    def _complete_near_asset_record(self) -> MeshAssetRecordV2:
        from pipeline.synthetic_village.mesh_asset_build import (
            ASSET_RECIPE_CONTRACTS,
        )

        if set(self.lod) != {"0", "1", "2"}:
            raise ValueError("mesh asset must provide exact LOD 0, 1, and 2")
        contract = ASSET_RECIPE_CONTRACTS.get(self.asset_id)
        if contract is None or contract[0] != self.kind:
            raise ValueError("mesh asset is not an exact registered recipe")
        expected_v1_recipe = contract[1]
        expected_near_recipe = (
            expected_v1_recipe.removesuffix("-v1") + "-near-v2"
        )
        if not all(
            math.isfinite(value) and value > 0
            for value in self.footprint_m
        ):
            raise ValueError("mesh asset footprint must contain three positive values")
        triangles = [self.lod[str(level)].triangle_count for level in (0, 1, 2)]
        if not triangles[0] < triangles[1] < triangles[2]:
            raise ValueError("mesh asset LOD triangles must increase strictly")
        for level in (0, 1):
            descriptor = self.lod[str(level)]
            if (
                descriptor.mesh_algorithm_id != MESH_V1_ALGORITHM_ID
                or descriptor.texture_storage != "embedded"
                or descriptor.recipe_id != expected_v1_recipe
            ):
                raise ValueError("mesh asset LOD0/1 must retain exact v1 semantics")
        near = self.lod["2"]
        if (
            near.mesh_algorithm_id != MESH_NEAR_ALGORITHM_ID
            or near.texture_storage != "shared-content-addressed"
            or near.recipe_id != expected_near_recipe
        ):
            raise ValueError("mesh asset LOD2 must use exact near-v2 semantics")
        lower, upper = LOD2_TRIANGLE_BANDS[self.kind]
        if not lower <= near.triangle_count <= upper:
            raise ValueError("mesh asset LOD2 triangle band is violated")
        return self


class MeshAssetBundleV2(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-asset-bundle.v2"
    ] = MESH_ASSET_BUNDLE_V2_SCHEMA
    bundle_id: Sha256
    coordinate_encoding: Literal[
        "three-east-up-negative-north"
    ] = GLB_COORDINATE_ENCODING
    source_v1_bundle_id: Sha256
    material_bundle_id: Sha256
    material_bundle_manifest_sha256: Sha256
    synthetic: Literal[True] = True
    real_photo_textures: Literal[False] = False
    build_tool_id: str = Field(min_length=1)
    verification_level: Literal["L0", "L2"]
    texture_audit_profile: Literal[
        "verified-relative-content-addressed"
    ] = "verified-relative-content-addressed"
    material_registry: tuple[ExpectedGlbMaterial, ...] = Field(min_length=1)
    texture_objects: tuple[TextureObjectV2, ...] = Field(min_length=1)
    records: tuple[MeshAssetRecordV2, ...] = Field(min_length=1, max_length=11)

    @property
    def asset_ids(self) -> tuple[str, ...]:
        return tuple(record.asset_id for record in self.records)

    @model_validator(mode="after")
    def _complete_stable_identity(self) -> MeshAssetBundleV2:
        if (
            self.asset_ids != tuple(sorted(self.asset_ids))
            or len(set(self.asset_ids)) != len(self.asset_ids)
        ):
            raise ValueError("mesh asset IDs must be sorted and unique")
        material_slots = tuple(row.slot_id for row in self.material_registry)
        if (
            material_slots != tuple(sorted(material_slots))
            or len(set(material_slots)) != len(material_slots)
        ):
            raise ValueError("mesh material registry must be sorted and unique")
        if any(
            row.bundle_id != self.material_bundle_id
            for row in self.material_registry
        ):
            raise ValueError("mesh material registry disagrees with bundle identity")
        registered_materials = set(material_slots)
        if any(
            not set(descriptor.material_slot_ids) <= registered_materials
            for record in self.records
            for descriptor in record.lod.values()
        ):
            raise ValueError("mesh template references an unknown material slot")
        texture_paths = tuple(row.object_path for row in self.texture_objects)
        if (
            texture_paths != tuple(sorted(texture_paths))
            or len(set(texture_paths)) != len(texture_paths)
            or len({row.sha256 for row in self.texture_objects})
            != len(self.texture_objects)
        ):
            raise ValueError("mesh texture objects must be sorted and unique")
        declared_texture_hashes = {row.sha256 for row in self.texture_objects}
        referenced_texture_hashes = {
            binding.sha256
            for record in self.records
            for binding in record.lod["2"].texture_bindings
        }
        if referenced_texture_hashes != declared_texture_hashes:
            raise ValueError("mesh texture objects must equal the exact binding closure")
        digest = hashlib.sha256(
            canonical_mesh_asset_bundle_v2_bytes(
                self,
                exclude_bundle_id=True,
            ),
        ).hexdigest()
        if digest != self.bundle_id:
            raise ValueError("mesh asset bundle ID does not match canonical content")
        return self


def canonical_mesh_asset_bundle_v2_bytes(
    bundle: MeshAssetBundleV2,
    *,
    exclude_bundle_id: bool = False,
) -> bytes:
    payload = bundle.model_dump(mode="json")
    if exclude_bundle_id:
        payload.pop("bundle_id")
    return _canonical_json_bytes(payload)


def _bundle_file_path(root: Path, relative: str, *, directory: str) -> Path:
    candidate = root / relative
    if _is_linklike(candidate):
        raise MeshAssetBundleError("mesh asset bundle object is redirected")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise MeshAssetBundleError("mesh asset bundle object is unavailable") from exc
    object_root = root / directory
    try:
        resolved.relative_to(object_root)
    except ValueError as exc:
        raise MeshAssetBundleError("mesh asset bundle object escapes its closure") from exc
    if resolved != candidate:
        raise MeshAssetBundleError("mesh asset bundle object is redirected")
    return candidate


def _verify_directory_closure(
    root: Path,
    *,
    directory: str,
    expected: set[str],
) -> None:
    object_root = _real_directory(root / directory)
    try:
        entries = tuple(object_root.iterdir())
    except OSError as exc:
        raise MeshAssetBundleError(
            "mesh asset bundle object set is unavailable",
        ) from exc
    if any(_is_linklike(path) for path in entries):
        raise MeshAssetBundleError(
            "mesh asset bundle object set contains a redirected entry",
        )
    actual = {
        path.relative_to(root).as_posix()
        for path in entries
        if path.is_file()
    }
    if actual != expected or len(entries) != len(actual):
        raise MeshAssetBundleError(
            "mesh asset bundle object set is incomplete or unexpected",
        )


def _verify_png(payload: bytes, descriptor: TextureObjectV2) -> None:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG" or image.size != (
                descriptor.width,
                descriptor.height,
            ):
                raise MeshAssetBundleError(
                    "mesh texture bytes disagree with PNG dimensions",
                )
    except MeshAssetBundleError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise MeshAssetBundleError("mesh texture object is not a valid PNG") from exc


def _verify_mesh_asset_bundle_v2(root: Path) -> MeshAssetBundleV2:
    """Verify canonical v2 identity and its exact immutable file closure."""

    bundle_root = _real_directory(Path(root))
    manifest_path = bundle_root / MESH_ASSET_BUNDLE_MANIFEST
    raw = _read_stable_file(
        manifest_path,
        maximum_bytes=MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES,
        label="mesh asset bundle manifest",
    )
    try:
        bundle = MeshAssetBundleV2.model_validate_json(raw)
    except ValidationError as exc:
        raise MeshAssetBundleError("mesh asset bundle manifest is invalid") from exc
    if raw != canonical_mesh_asset_bundle_v2_bytes(bundle):
        raise MeshAssetBundleError("mesh asset bundle manifest is not canonical")

    expected_glbs = {
        descriptor.glb_object_path
        for record in bundle.records
        for descriptor in record.lod.values()
    }
    expected_textures = {row.object_path for row in bundle.texture_objects}
    _verify_directory_closure(
        bundle_root,
        directory="objects",
        expected=expected_glbs,
    )
    _verify_directory_closure(
        bundle_root,
        directory="textures",
        expected=expected_textures,
    )
    for record in bundle.records:
        for descriptor in record.lod.values():
            payload = _read_stable_file(
                _bundle_file_path(
                    bundle_root,
                    descriptor.glb_object_path,
                    directory="objects",
                ),
                maximum_bytes=MAX_MESH_TEMPLATE_GLB_BYTES,
                label="mesh template",
            )
            if (
                len(payload) != descriptor.glb_bytes
                or hashlib.sha256(payload).hexdigest() != descriptor.glb_sha256
            ):
                raise MeshAssetBundleError(
                    "mesh template bytes do not match their descriptor",
                )
    for descriptor in bundle.texture_objects:
        payload = _read_stable_file(
            _bundle_file_path(
                bundle_root,
                descriptor.object_path,
                directory="textures",
            ),
            maximum_bytes=MAX_MESH_TEXTURE_BYTES,
            label="mesh texture object",
        )
        if (
            len(payload) != descriptor.bytes
            or hashlib.sha256(payload).hexdigest() != descriptor.sha256
        ):
            raise MeshAssetBundleError(
                "mesh texture bytes do not match their descriptor",
            )
        _verify_png(payload, descriptor)
    final_raw = _read_stable_file(
        manifest_path,
        maximum_bytes=MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES,
        label="mesh asset bundle manifest",
    )
    if final_raw != raw:
        raise MeshAssetBundleError(
            "mesh asset bundle manifest changed during verification",
        )
    return bundle


def _mesh_asset_bundle_v2_stat_signature(
    root: Path,
    bundle: MeshAssetBundleV2,
) -> tuple[tuple[str, int, int, int, int, int, int], ...]:
    paths = [
        (MESH_ASSET_BUNDLE_MANIFEST, False),
        ("objects", True),
        ("textures", True),
        *(
            (relative, False)
            for relative in sorted({
                descriptor.glb_object_path
                for record in bundle.records
                for descriptor in record.lod.values()
            })
        ),
        *((row.object_path, False) for row in bundle.texture_objects),
    ]
    rows = []
    for relative, expected_directory in paths:
        path = root / relative
        if _is_linklike(path):
            raise MeshAssetBundleError("mesh asset bundle snapshot contains a redirect")
        try:
            metadata = path.stat()
        except OSError as exc:
            raise MeshAssetBundleError(
                "mesh asset bundle snapshot is unavailable",
            ) from exc
        if (
            expected_directory
            and not stat.S_ISDIR(metadata.st_mode)
        ) or (
            not expected_directory
            and not stat.S_ISREG(metadata.st_mode)
        ):
            raise MeshAssetBundleError("mesh asset bundle snapshot type changed")
        rows.append(
            (
                relative,
                metadata.st_mode,
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            ),
        )
    return tuple(rows)


_MESH_ASSET_BUNDLE_V2_CACHE_LOCK = threading.RLock()
_MESH_ASSET_BUNDLE_V2_CACHE: dict[
    Path,
    tuple[
        MeshAssetBundleV2,
        tuple[tuple[str, int, int, int, int, int, int], ...],
    ],
] = {}


def load_mesh_asset_bundle_v2(root: Path) -> MeshAssetBundleV2:
    """Load a v2 bundle, reusing only an unchanged filesystem snapshot."""

    bundle_root = _real_directory(Path(root))
    with _MESH_ASSET_BUNDLE_V2_CACHE_LOCK:
        cached = _MESH_ASSET_BUNDLE_V2_CACHE.get(bundle_root)
        if cached is not None:
            bundle, signature = cached
            try:
                current_signature = _mesh_asset_bundle_v2_stat_signature(
                    bundle_root,
                    bundle,
                )
            except MeshAssetBundleError:
                _MESH_ASSET_BUNDLE_V2_CACHE.pop(bundle_root, None)
            else:
                if current_signature == signature:
                    return bundle
                _MESH_ASSET_BUNDLE_V2_CACHE.pop(bundle_root, None)
        bundle = _verify_mesh_asset_bundle_v2(bundle_root)
        signature = _mesh_asset_bundle_v2_stat_signature(bundle_root, bundle)
        _MESH_ASSET_BUNDLE_V2_CACHE[bundle_root] = (bundle, signature)
        return bundle
