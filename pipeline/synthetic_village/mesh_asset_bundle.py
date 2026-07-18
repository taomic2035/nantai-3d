"""Immutable, independently audited textured mesh template bundles."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import io
import json
import math
import os
import shutil
import stat
import sys
import threading
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import numpy as np
import trimesh
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from pipeline.studio_jobs import (
    JobContractError,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
)
from pipeline.synthetic_village.glb_material_audit import (
    ExpectedGlbMaterial,
    GlbMaterialAuditError,
    audit_textured_glb,
)
from pipeline.synthetic_village.material_bundle import (
    MaterialBundleError,
    canonical_material_bundle_bytes,
    load_material_bundle,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

MESH_ASSET_BUNDLE_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v1"
MESH_ASSET_BUNDLE_MANIFEST = "manifest.json"
MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_MESH_TEMPLATE_GLB_BYTES = 128 * 1024 * 1024
GLB_COORDINATE_ENCODING = "three-east-up-negative-north"
MESH_TRIANGLE_BUDGETS = {
    "building": {0: 100, 1: 300, 2: 720},
    "vegetation": {0: 160, 1: 500, 2: 1200},
    "prop": {0: 80, 1: 240, 2: 600},
}
MESH_BOUNDS_TOLERANCE_M = 1e-5


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
        triangles = [
            self.lod[str(level)].triangle_count
            for level in (0, 1, 2)
        ]
        if not triangles[0] < triangles[1] < triangles[2]:
            raise ValueError("mesh asset LOD triangles must increase strictly")
        if any(
            self.lod[str(level)].triangle_count
            > MESH_TRIANGLE_BUDGETS[self.kind][level]
            for level in (0, 1, 2)
        ):
            raise ValueError("mesh asset exceeds its kind/LOD triangle budget")
        return self


class MeshAssetBundle(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-asset-bundle.v1"
    ] = MESH_ASSET_BUNDLE_SCHEMA
    bundle_id: Sha256
    coordinate_encoding: Literal[
        "three-east-up-negative-north"
    ] = GLB_COORDINATE_ENCODING
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


@dataclass(frozen=True)
class MeshAssetTemplateSource:
    """Path-bearing builder output kept outside the canonical bundle manifest."""

    asset_id: str
    kind: Literal["building", "vegetation", "prop"]
    footprint_m: tuple[float, float, float]
    lod_paths: tuple[Path, Path, Path]
    material_slot_ids: tuple[
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]


@dataclass(frozen=True)
class PreparedMeshAssetBundle:
    staging_root: Path
    manifest: MeshAssetBundle


@dataclass(frozen=True)
class MeshAssetBundleResult:
    bundle_id: str
    final_directory: Path
    record_count: int
    reused: bool


_MESH_ASSET_BUNDLE_CACHE_LOCK = threading.RLock()
_MESH_ASSET_BUNDLE_CACHE: dict[
    Path,
    tuple[
        MeshAssetBundle,
        tuple[tuple[str, int, int, int, int, int, int], ...],
    ],
] = {}


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


def measure_mesh_template_enu_bounds(payload: bytes) -> Bounds3:
    """Measure transformed GLB scene bounds and convert them to ENU Z-up."""

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            scene = trimesh.load_scene(
                file_obj=io.BytesIO(payload),
                file_type="glb",
                resolver=None,
                allow_remote=False,
            )
            bounds = np.asarray(scene.bounds, dtype=np.float64)
    except Exception as exc:
        raise MeshAssetBundleError(
            "mesh template bounds could not be measured",
        ) from exc
    dangerous_warning_tokens = (
        "fail",
        "invalid",
        "skip",
        "unable",
        "unsupported",
    )
    if any(
        any(token in str(item.message).lower() for token in dangerous_warning_tokens)
        for item in caught
    ):
        raise MeshAssetBundleError(
            "mesh template bounds audit skipped or rejected geometry",
        )
    if not scene.geometry or bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise MeshAssetBundleError(
            "mesh template bounds are empty or non-finite",
        )
    gltf_min, gltf_max = bounds
    return Bounds3(
        min=(
            float(gltf_min[0]),
            float(-gltf_max[2]),
            float(gltf_min[1]),
        ),
        max=(
            float(gltf_max[0]),
            float(-gltf_min[2]),
            float(gltf_max[1]),
        ),
    )


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
    measured_bounds = measure_mesh_template_enu_bounds(payload)
    if not (
        np.allclose(
            measured_bounds.min,
            descriptor.aabb.min,
            atol=MESH_BOUNDS_TOLERANCE_M,
            rtol=0.0,
        )
        and np.allclose(
            measured_bounds.max,
            descriptor.aabb.max,
            atol=MESH_BOUNDS_TOLERANCE_M,
            rtol=0.0,
        )
    ):
        raise MeshAssetBundleError(
            "mesh template bounds disagree with measured GLB geometry",
        )
    return payload


def _verify_mesh_asset_bundle(root: Path) -> MeshAssetBundle:
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

    expected_objects = {
        descriptor.glb_object_path
        for record in bundle.records
        for descriptor in record.lod.values()
    }
    object_root = _real_directory(bundle_root / "objects")
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
    actual_objects = {
        path.relative_to(bundle_root).as_posix()
        for path in entries
        if path.is_file()
    }
    if actual_objects != expected_objects or len(entries) != len(actual_objects):
        raise MeshAssetBundleError(
            "mesh asset bundle object set is incomplete or unexpected",
        )

    expected_by_slot = {
        material.slot_id: material for material in bundle.material_registry
    }
    verified: dict[
        tuple[str, tuple[str, ...]],
        tuple[
            int,
            int,
            int,
            tuple[str, ...],
            tuple[float, float, float],
            tuple[float, float, float],
        ],
    ] = {}
    for record in bundle.records:
        for descriptor in record.lod.values():
            key = (descriptor.glb_sha256, descriptor.material_slot_ids)
            evidence = (
                descriptor.glb_bytes,
                descriptor.triangle_count,
                descriptor.primitive_count,
                descriptor.material_slot_ids,
                descriptor.aabb.min,
                descriptor.aabb.max,
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


def _mesh_asset_bundle_stat_signature(
    root: Path,
    bundle: MeshAssetBundle,
) -> tuple[tuple[str, int, int, int, int, int, int], ...]:
    object_paths = sorted({
        descriptor.glb_object_path
        for record in bundle.records
        for descriptor in record.lod.values()
    })
    rows = []
    for relative, expected_directory in (
        (MESH_ASSET_BUNDLE_MANIFEST, False),
        ("objects", True),
        *((object_path, False) for object_path in object_paths),
    ):
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
        rows.append((
            relative,
            metadata.st_mode,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ))
    return tuple(rows)


def load_mesh_asset_bundle(root: Path) -> MeshAssetBundle:
    """Load verified templates, reusing only an unchanged filesystem snapshot."""

    bundle_root = _real_directory(Path(root))
    with _MESH_ASSET_BUNDLE_CACHE_LOCK:
        cached = _MESH_ASSET_BUNDLE_CACHE.get(bundle_root)
        if cached is not None:
            bundle, signature = cached
            try:
                current_signature = _mesh_asset_bundle_stat_signature(
                    bundle_root,
                    bundle,
                )
            except MeshAssetBundleError:
                _MESH_ASSET_BUNDLE_CACHE.pop(bundle_root, None)
            else:
                if current_signature == signature:
                    return bundle
                _MESH_ASSET_BUNDLE_CACHE.pop(bundle_root, None)
        bundle = _verify_mesh_asset_bundle(bundle_root)
        signature = _mesh_asset_bundle_stat_signature(bundle_root, bundle)
        _MESH_ASSET_BUNDLE_CACHE[bundle_root] = (bundle, signature)
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


def _prepare_real_directory(raw_path: Path, *, label: str) -> Path:
    path = Path(raw_path).expanduser().absolute()
    cursor = path
    missing: list[Path] = []
    while not cursor.exists():
        if _is_linklike(cursor):
            raise MeshAssetBundleError(f"{label} has a redirected ancestor")
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise MeshAssetBundleError(f"{label} has no real existing ancestor")
        cursor = parent
    _real_directory(cursor)
    for directory in reversed(missing):
        try:
            directory.mkdir(exist_ok=False)
            _flush_directory(directory.parent)
        except FileExistsError:
            pass
        _real_directory(directory)
    return _real_directory(path)


def _flush_file(path: Path) -> None:
    if os.name == "nt":
        WindowsNtfsDurabilityBackend.flush_file(path)
        return
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _flush_directory(path: Path) -> None:
    if os.name == "nt":
        WindowsNtfsDurabilityBackend.flush_directory(path)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_mesh_object(root: Path, payload: bytes) -> Path:
    digest = hashlib.sha256(payload).hexdigest()
    path = root / f"objects/{digest}.glb"
    if path.exists() or _is_linklike(path):
        current = _read_stable_file(
            path,
            maximum_bytes=MAX_MESH_TEMPLATE_GLB_BYTES,
            label="mesh staging object",
        )
        if current != payload:
            raise MeshAssetBundleError(
                "mesh staging object conflicts with its content address",
            )
        return path
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except OSError as exc:
        raise MeshAssetBundleError("mesh staging object could not be written") from exc
    return path


def _expected_materials(
    material_bundle,
    slot_ids: tuple[str, ...],
) -> tuple[ExpectedGlbMaterial, ...]:
    if (
        not slot_ids
        or slot_ids != tuple(sorted(slot_ids))
        or len(set(slot_ids)) != len(slot_ids)
    ):
        raise MeshAssetBundleError(
            "mesh template material slots must be nonempty, sorted, and unique",
        )
    records = {record.slot_id: record for record in material_bundle.records}
    try:
        return tuple(
            ExpectedGlbMaterial(
                slot_id=slot_id,
                source_sha256=records[slot_id].source_sha256,
                bundle_id=material_bundle.bundle_id,
                algorithm_id=material_bundle.algorithm_id,
            )
            for slot_id in slot_ids
        )
    except KeyError as exc:
        raise MeshAssetBundleError(
            "mesh template references an unknown material slot",
        ) from exc


def prepare_mesh_asset_bundle(
    *,
    material_bundle_root: Path,
    sources: tuple[MeshAssetTemplateSource, ...],
    staging_root: Path,
    build_tool_id: str,
    verification_level: Literal["L0", "L2"] = "L0",
) -> PreparedMeshAssetBundle:
    """Copy builder outputs into a path-free bundle after independent audits."""

    staging_root = Path(staging_root).expanduser().absolute()
    if staging_root.exists() or _is_linklike(staging_root):
        raise MeshAssetBundleError("mesh staging root must start absent")
    _real_directory(staging_root.parent)
    try:
        material_bundle = load_material_bundle(material_bundle_root)
        if not sources or len(sources) > 11:
            raise MeshAssetBundleError(
                "mesh bundle sources must contain between one and eleven assets",
            )
        asset_ids = tuple(source.asset_id for source in sources)
        if len(set(asset_ids)) != len(asset_ids):
            raise MeshAssetBundleError("mesh bundle source asset IDs must be unique")

        staging_root.mkdir(exist_ok=False)
        (staging_root / "objects").mkdir()
        records = []
        for source in sorted(sources, key=lambda item: item.asset_id):
            if len(source.lod_paths) != 3 or len(source.material_slot_ids) != 3:
                raise MeshAssetBundleError(
                    "mesh source must provide exact LOD 0, 1, and 2 inputs",
                )
            lod = {}
            for level, (source_path, slot_ids) in enumerate(
                zip(
                    source.lod_paths,
                    source.material_slot_ids,
                    strict=True,
                ),
            ):
                expected = _expected_materials(material_bundle, slot_ids)
                payload = _read_stable_file(
                    Path(source_path),
                    maximum_bytes=MAX_MESH_TEMPLATE_GLB_BYTES,
                    label="mesh builder output",
                )
                object_path = _write_mesh_object(staging_root, payload)
                try:
                    audit = audit_textured_glb(
                        object_path,
                        expected_materials=expected,
                    )
                except GlbMaterialAuditError as exc:
                    raise MeshAssetBundleError(
                        "mesh builder output material or geometry audit failed",
                    ) from exc
                bounds = measure_mesh_template_enu_bounds(payload)
                lod[str(level)] = MeshTemplateLod(
                    glb_object_path=object_path.relative_to(
                        staging_root,
                    ).as_posix(),
                    glb_sha256=audit.glb_sha256,
                    glb_bytes=audit.byte_count,
                    triangle_count=audit.triangle_count,
                    primitive_count=audit.primitive_count,
                    material_slot_ids=audit.slot_ids,
                    aabb=bounds,
                )
            records.append(
                MeshAssetRecord(
                    asset_id=source.asset_id,
                    kind=source.kind,
                    mesh_algorithm_id="synthetic-template-mesh-v1",
                    footprint_m=source.footprint_m,
                    lod=lod,
                ),
            )
        material_manifest_sha256 = hashlib.sha256(
            canonical_material_bundle_bytes(material_bundle),
        ).hexdigest()
        unsigned = {
            "schema_version": MESH_ASSET_BUNDLE_SCHEMA,
            "coordinate_encoding": GLB_COORDINATE_ENCODING,
            "material_bundle_id": material_bundle.bundle_id,
            "material_bundle_manifest_sha256": material_manifest_sha256,
            "synthetic": True,
            "real_photo_textures": False,
            "build_tool_id": build_tool_id,
            "verification_level": verification_level,
            "material_registry": tuple(
                ExpectedGlbMaterial(
                    slot_id=record.slot_id,
                    source_sha256=record.source_sha256,
                    bundle_id=material_bundle.bundle_id,
                    algorithm_id=material_bundle.algorithm_id,
                )
                for record in material_bundle.records
            ),
            "records": tuple(records),
        }
        bundle_id = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
        manifest = MeshAssetBundle(bundle_id=bundle_id, **unsigned)
        (staging_root / MESH_ASSET_BUNDLE_MANIFEST).write_bytes(
            canonical_mesh_asset_bundle_bytes(manifest),
        )
        if load_mesh_asset_bundle(staging_root) != manifest:
            raise MeshAssetBundleError(
                "prepared mesh asset bundle changed during verification",
            )
        return PreparedMeshAssetBundle(
            staging_root=staging_root,
            manifest=manifest,
        )
    except MeshAssetBundleError:
        if staging_root.is_symlink():
            staging_root.unlink(missing_ok=True)
        elif staging_root.exists() and staging_root.is_dir():
            shutil.rmtree(staging_root, ignore_errors=True)
        raise
    except (MaterialBundleError, OSError, ValidationError, ValueError) as exc:
        if staging_root.is_symlink():
            staging_root.unlink(missing_ok=True)
        elif staging_root.exists() and staging_root.is_dir():
            shutil.rmtree(staging_root, ignore_errors=True)
        raise MeshAssetBundleError(
            f"mesh asset bundle preparation failed: {exc}",
        ) from exc


def _durably_flush_mesh_bundle(staging: Path) -> None:
    manifest = load_mesh_asset_bundle(staging)
    object_paths = {
        descriptor.glb_object_path
        for record in manifest.records
        for descriptor in record.lod.values()
    }
    for object_path in sorted(object_paths):
        _flush_file(staging / object_path)
    _flush_file(staging / MESH_ASSET_BUNDLE_MANIFEST)
    _flush_directory(staging / "objects")
    _flush_directory(staging)
    if load_mesh_asset_bundle(staging) != manifest:
        raise MeshAssetBundleError(
            "mesh asset bundle changed during durability flush",
        )


def _move_mesh_directory_noreplace(source: Path, destination: Path) -> None:
    if destination.exists() or _is_linklike(destination):
        raise MeshAssetBundleError(
            f"mesh asset bundle destination already exists: {destination.name}",
        )
    moved = False
    try:
        if os.name == "nt":
            WindowsNtfsDurabilityBackend.move(source, destination)
        elif sys.platform.startswith("linux"):
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100,
                os.fsencode(source),
                -100,
                os.fsencode(destination),
                1,
            )
            if result != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(destination))
        elif sys.platform == "darwin":
            libc = ctypes.CDLL(None, use_errno=True)
            renamex_np = libc.renamex_np
            renamex_np.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renamex_np.restype = ctypes.c_int
            result = renamex_np(
                os.fsencode(source),
                os.fsencode(destination),
                0x00000004,
            )
            if result != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(destination))
        else:  # pragma: no cover - supported publication hosts are explicit
            if destination.exists() or _is_linklike(destination):
                raise FileExistsError(errno.EEXIST, "destination exists", destination)
            os.rename(source, destination)
        moved = True
        _flush_directory(source.parent)
        if destination.parent != source.parent:
            _flush_directory(destination.parent)
    except (JobContractError, OSError) as exc:
        if moved:
            raise MeshAssetBundleError(
                "mesh asset bundle moved but parent durability flush failed; "
                "retry to verify and reuse it",
            ) from exc
        if destination.exists() or _is_linklike(destination):
            raise MeshAssetBundleError(
                f"mesh asset bundle destination already exists: {destination.name}",
            ) from exc
        raise MeshAssetBundleError(
            f"cannot publish mesh asset bundle: {exc}",
        ) from exc


def _cleanup_mesh_staging(staging: Path, *, work_root: Path) -> None:
    if staging.parent != work_root or not staging.name.startswith(".mesh-"):
        raise MeshAssetBundleError(
            "refusing to clean an unowned mesh staging path",
        )
    if _is_linklike(staging):
        staging.unlink(missing_ok=True)
    elif staging.exists():
        if not staging.is_dir():
            raise MeshAssetBundleError(
                "mesh staging path became irregular",
            )
        shutil.rmtree(staging)


def publish_mesh_asset_bundle(
    *,
    material_bundle_root: Path,
    sources: tuple[MeshAssetTemplateSource, ...],
    publication_root: Path,
    work_root: Path,
    build_tool_id: str,
    verification_level: Literal["L0", "L2"] = "L0",
) -> MeshAssetBundleResult:
    """Prepare and durably publish an immutable bundle only while absent."""

    staging: Path | None = None
    try:
        material_bundle_root = _real_directory(Path(material_bundle_root))
        publication_root = _prepare_real_directory(
            Path(publication_root),
            label="mesh publication root",
        )
        work_root = _prepare_real_directory(
            Path(work_root),
            label="mesh work root",
        )
        with ProjectFileLock(
            work_root / ".mesh-asset-bundle.lock",
            role="writer",
        ):
            material_before = load_material_bundle(material_bundle_root)
            material_before_bytes = canonical_material_bundle_bytes(
                material_before,
            )
            staging = work_root / f".mesh-{uuid.uuid4().hex}"
            prepared = prepare_mesh_asset_bundle(
                material_bundle_root=material_bundle_root,
                sources=sources,
                staging_root=staging,
                build_tool_id=build_tool_id,
                verification_level=verification_level,
            )
            material_after = load_material_bundle(material_bundle_root)
            if (
                canonical_material_bundle_bytes(material_after)
                != material_before_bytes
                or prepared.manifest.material_bundle_id
                != material_before.bundle_id
            ):
                raise MeshAssetBundleError(
                    "material bundle changed during mesh publication",
                )
            verified = load_mesh_asset_bundle(staging)
            if verified != prepared.manifest:
                raise MeshAssetBundleError(
                    "prepared mesh asset bundle identity changed",
                )
            destination = publication_root / prepared.manifest.bundle_id
            if destination.exists() or _is_linklike(destination):
                existing = load_mesh_asset_bundle(destination)
                if existing != prepared.manifest:
                    raise MeshAssetBundleError(
                        "existing mesh asset bundle does not match its content identity",
                    )
                _cleanup_mesh_staging(staging, work_root=work_root)
                staging = None
                return MeshAssetBundleResult(
                    bundle_id=existing.bundle_id,
                    final_directory=destination,
                    record_count=len(existing.records),
                    reused=True,
                )
            _durably_flush_mesh_bundle(staging)
            _move_mesh_directory_noreplace(staging, destination)
            staging = None
            published = load_mesh_asset_bundle(destination)
            if published != prepared.manifest:
                raise MeshAssetBundleError(
                    "published mesh asset bundle changed during atomic move",
                )
            return MeshAssetBundleResult(
                bundle_id=published.bundle_id,
                final_directory=destination,
                record_count=len(published.records),
                reused=False,
            )
    except MeshAssetBundleError:
        raise
    except (JobContractError, MaterialBundleError, OSError, ValidationError) as exc:
        raise MeshAssetBundleError(
            f"mesh asset bundle publication filesystem failure: {exc}",
        ) from exc
    finally:
        if staging is not None:
            try:
                _cleanup_mesh_staging(staging, work_root=staging.parent)
            except (MeshAssetBundleError, OSError):
                pass
