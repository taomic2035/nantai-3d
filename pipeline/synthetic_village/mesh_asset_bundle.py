"""Immutable, independently audited textured mesh template bundles."""

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

from pipeline.synthetic_village.glb_material_audit import (
    ExpectedGlbMaterial,
    GlbMaterialAuditError,
    audit_textured_glb,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

MESH_ASSET_BUNDLE_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v1"
MESH_ASSET_BUNDLE_MANIFEST = "manifest.json"
MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_MESH_TEMPLATE_GLB_BYTES = 128 * 1024 * 1024


class MeshAssetBundleError(ValueError):
    """A mesh template bundle cannot be treated as verified evidence."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Bounds3(FrozenModel):
    min: tuple[float, float, float]
    max: tuple[float, float, float]

    @model_validator(mode="after")
    def _finite_ordered_bounds(self) -> Bounds3:
        if not all(math.isfinite(value) for value in (*self.min, *self.max)):
            raise ValueError("mesh template AABB must be finite")
        if any(lower > upper for lower, upper in zip(self.min, self.max, strict=True)):
            raise ValueError("mesh template AABB must be ordered")
        return self


class MeshTemplateLod(FrozenModel):
    glb_object_path: str = Field(min_length=1)
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1, le=MAX_MESH_TEMPLATE_GLB_BYTES)
    triangle_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    aabb: Bounds3

    @model_validator(mode="after")
    def _content_addressed_complete_lod(self) -> MeshTemplateLod:
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
        return self


class MeshAssetRecord(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: Literal["building", "vegetation", "prop"]
    mesh_algorithm_id: Literal["synthetic-template-mesh-v1"]
    footprint_m: tuple[float, float, float]
    lod: dict[Literal["0", "1", "2"], MeshTemplateLod]
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"

    @model_validator(mode="after")
    def _complete_asset_record(self) -> MeshAssetRecord:
        if set(self.lod) != {"0", "1", "2"}:
            raise ValueError("mesh asset must provide exact LOD 0, 1, and 2")
        if not all(
            math.isfinite(value) and value > 0
            for value in self.footprint_m
        ):
            raise ValueError("mesh asset footprint must contain three positive values")
        return self


class MeshAssetBundle(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-asset-bundle.v1"
    ] = MESH_ASSET_BUNDLE_SCHEMA
    bundle_id: Sha256
    material_bundle_id: Sha256
    material_bundle_manifest_sha256: Sha256
    synthetic: Literal[True] = True
    real_photo_textures: Literal[False] = False
    build_tool_id: str = Field(min_length=1)
    verification_level: Literal["L0", "L2"]
    material_registry: tuple[ExpectedGlbMaterial, ...] = Field(min_length=1)
    records: tuple[MeshAssetRecord, ...] = Field(min_length=1, max_length=11)

    @property
    def asset_ids(self) -> tuple[str, ...]:
        return tuple(record.asset_id for record in self.records)

    @model_validator(mode="after")
    def _complete_stable_identity(self) -> MeshAssetBundle:
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
            raise ValueError("mesh material registry disagrees with its bundle identity")
        material_slot_set = set(material_slots)
        if any(
            not set(descriptor.material_slot_ids) <= material_slot_set
            for record in self.records
            for descriptor in record.lod.values()
        ):
            raise ValueError("mesh template references an unknown material slot")
        digest = hashlib.sha256(
            canonical_mesh_asset_bundle_bytes(self, exclude_bundle_id=True),
        ).hexdigest()
        if digest != self.bundle_id:
            raise ValueError("mesh asset bundle ID does not match canonical content")
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
    text = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


def canonical_mesh_asset_bundle_bytes(
    bundle: MeshAssetBundle,
    *,
    exclude_bundle_id: bool = False,
) -> bytes:
    payload = bundle.model_dump(mode="json")
    if exclude_bundle_id:
        payload.pop("bundle_id")
    return _canonical_json_bytes(payload)


def _is_linklike(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _real_directory(root: Path) -> Path:
    path = Path(root).expanduser().absolute()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MeshAssetBundleError("mesh asset bundle root is unavailable") from exc
    if _is_linklike(path) or not path.is_dir() or resolved != path:
        raise MeshAssetBundleError(
            "mesh asset bundle root is redirected or not a real directory",
        )
    return path


def _stat_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _read_stable_file(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    path = Path(path).expanduser().absolute()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MeshAssetBundleError(f"{label} is unavailable") from exc
    if _is_linklike(path) or not path.is_file() or resolved != path:
        raise MeshAssetBundleError(f"{label} is redirected or not a real file")
    try:
        before = _stat_signature(path)
        if before[2] <= 0 or before[2] > maximum_bytes:
            raise MeshAssetBundleError(f"{label} size is outside the verification bound")
        with path.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
        after = _stat_signature(path)
    except MeshAssetBundleError:
        raise
    except OSError as exc:
        raise MeshAssetBundleError(f"{label} could not be read stably") from exc
    if before != after or len(payload) != before[2] or len(payload) > maximum_bytes:
        raise MeshAssetBundleError(f"{label} changed during verification")
    return payload


def _template_path(root: Path, descriptor: MeshTemplateLod) -> Path:
    candidate = root / descriptor.glb_object_path
    if _is_linklike(candidate):
        raise MeshAssetBundleError("mesh template is redirected")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise MeshAssetBundleError("mesh template is unavailable") from exc
    object_root = root / "objects"
    try:
        resolved.relative_to(object_root)
    except ValueError as exc:
        raise MeshAssetBundleError("mesh template escapes its bundle") from exc
    if resolved != candidate:
        raise MeshAssetBundleError("mesh template is redirected")
    return candidate


def _read_template_bytes(root: Path, descriptor: MeshTemplateLod) -> bytes:
    payload = _read_stable_file(
        _template_path(root, descriptor),
        maximum_bytes=MAX_MESH_TEMPLATE_GLB_BYTES,
        label="mesh template",
    )
    if (
        len(payload) != descriptor.glb_bytes
        or hashlib.sha256(payload).hexdigest() != descriptor.glb_sha256
    ):
        raise MeshAssetBundleError("mesh template bytes do not match their descriptor")
    return payload


def _verify_descriptor(
    root: Path,
    descriptor: MeshTemplateLod,
    *,
    expected_materials: tuple[ExpectedGlbMaterial, ...],
) -> bytes:
    payload = _read_template_bytes(root, descriptor)
    try:
        audit = audit_textured_glb(
            _template_path(root, descriptor),
            expected_materials=expected_materials,
        )
    except GlbMaterialAuditError as exc:
        raise MeshAssetBundleError(
            "mesh template material or geometry audit failed",
        ) from exc
    if (
        audit.glb_sha256 != descriptor.glb_sha256
        or audit.byte_count != descriptor.glb_bytes
        or audit.primitive_count != descriptor.primitive_count
        or audit.triangle_count != descriptor.triangle_count
        or audit.slot_ids != descriptor.material_slot_ids
    ):
        raise MeshAssetBundleError(
            "mesh template triangle, primitive, or material evidence disagrees",
        )
    return payload


def load_mesh_asset_bundle(root: Path) -> MeshAssetBundle:
    """Load and independently verify every immutable template in a bundle."""

    bundle_root = _real_directory(Path(root))
    manifest_path = bundle_root / MESH_ASSET_BUNDLE_MANIFEST
    raw = _read_stable_file(
        manifest_path,
        maximum_bytes=MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES,
        label="mesh asset bundle manifest",
    )
    try:
        bundle = MeshAssetBundle.model_validate_json(raw)
    except ValidationError as exc:
        raise MeshAssetBundleError("mesh asset bundle manifest is invalid") from exc
    if raw != canonical_mesh_asset_bundle_bytes(bundle):
        raise MeshAssetBundleError("mesh asset bundle manifest is not canonical")

    expected_by_slot = {
        material.slot_id: material for material in bundle.material_registry
    }
    verified: dict[
        tuple[str, tuple[str, ...]],
        tuple[int, int, tuple[str, ...]],
    ] = {}
    for record in bundle.records:
        for descriptor in record.lod.values():
            key = (descriptor.glb_sha256, descriptor.material_slot_ids)
            evidence = (
                descriptor.triangle_count,
                descriptor.primitive_count,
                descriptor.material_slot_ids,
            )
            if key in verified:
                if verified[key] != evidence:
                    raise MeshAssetBundleError(
                        "shared mesh template descriptors disagree",
                    )
                _read_template_bytes(bundle_root, descriptor)
                continue
            expected = tuple(
                expected_by_slot[slot_id]
                for slot_id in descriptor.material_slot_ids
            )
            _verify_descriptor(
                bundle_root,
                descriptor,
                expected_materials=expected,
            )
            verified[key] = evidence

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


def read_verified_mesh_template_glb(
    root: Path,
    *,
    bundle: MeshAssetBundle,
    asset_id: str,
    lod: int,
) -> bytes:
    """Read exact current GLB bytes only while all bundle evidence still matches."""

    if isinstance(lod, bool) or lod not in {0, 1, 2}:
        raise MeshAssetBundleError("mesh template LOD must be 0, 1, or 2")
    current = load_mesh_asset_bundle(Path(root))
    if current != bundle:
        raise MeshAssetBundleError("mesh asset bundle changed after it was selected")
    record = next(
        (candidate for candidate in current.records if candidate.asset_id == asset_id),
        None,
    )
    if record is None:
        raise MeshAssetBundleError("mesh asset is not present in the verified bundle")
    descriptor = record.lod[str(lod)]
    return _read_template_bytes(_real_directory(Path(root)), descriptor)
