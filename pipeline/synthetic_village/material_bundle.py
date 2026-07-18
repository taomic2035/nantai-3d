"""Deterministic derived PBR maps for replaceable synthetic visual sources."""

from __future__ import annotations

import hashlib
import io
import json
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import numpy as np
import PIL
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from .defaults import load_default_visual_slots
from .visual_sources import (
    VISUAL_MANIFEST_NAME,
    VisualSourceError,
    canonical_manifest_bytes,
    load_visual_source_manifest,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
UvPolicy = Literal[
    "world-xy",
    "dominant-axis-box",
    "roof-slope",
    "object-long-axis",
    "leaf-card",
]

MATERIAL_BUNDLE_SCHEMA = "nantai.synthetic-village.derived-material-bundle.v1"
MATERIAL_BUNDLE_MANIFEST = "manifest.json"
ALGORITHM_ID = "mirror-sobel-orm-v1"
MAP_SIZE = 1024
MAX_MATERIAL_BUNDLE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_DERIVED_MAP_BYTES = 64 * 1024 * 1024


class MaterialBundleError(ValueError):
    """A derived material bundle cannot be prepared or trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


@dataclass(frozen=True)
class MaterialParameters:
    uv_policy: UvPolicy
    nominal_tile_m: float
    normal_strength: float
    roughness_center: float
    metallic: float


def _parameters(
    uv_policy: UvPolicy,
    nominal_tile_m: float,
    normal_strength: float,
    roughness_center: float,
    metallic: float,
) -> MaterialParameters:
    return MaterialParameters(
        uv_policy=uv_policy,
        nominal_tile_m=nominal_tile_m,
        normal_strength=normal_strength,
        roughness_center=roughness_center,
        metallic=metallic,
    )


MATERIAL_PARAMETERS: dict[str, MaterialParameters] = {
    "material-aged-metal-01": _parameters("dominant-axis-box", 0.8, 0.70, 0.52, 0.62),
    "material-bamboo-leaf-01": _parameters("leaf-card", 0.35, 0.55, 0.74, 0.0),
    "material-bamboo-stem-01": _parameters("object-long-axis", 0.6, 0.65, 0.58, 0.0),
    "material-broadleaf-bark-01": _parameters("object-long-axis", 1.4, 0.85, 0.91, 0.0),
    "material-broadleaf-canopy-01": _parameters("leaf-card", 0.9, 0.50, 0.82, 0.0),
    "material-clay-brick-01": _parameters("dominant-axis-box", 1.2, 0.80, 0.83, 0.0),
    "material-creek-rock-01": _parameters("world-xy", 2.5, 0.90, 0.88, 0.0),
    "material-dark-timber-01": _parameters("object-long-axis", 1.6, 0.80, 0.78, 0.0),
    "material-dry-stone-wall-01": _parameters("dominant-axis-box", 3.0, 1.00, 0.94, 0.0),
    "material-fieldstone-01": _parameters("dominant-axis-box", 2.5, 1.00, 0.91, 0.0),
    "material-gray-roof-tile-01": _parameters("roof-slope", 3.0, 0.90, 0.76, 0.0),
    "material-moss-stone-01": _parameters("dominant-axis-box", 2.5, 0.95, 0.93, 0.0),
    "material-orchard-bark-01": _parameters("object-long-axis", 1.2, 0.85, 0.88, 0.0),
    "material-orchard-leaf-01": _parameters("leaf-card", 0.6, 0.50, 0.76, 0.0),
    "material-packed-earth-01": _parameters("world-xy", 3.0, 0.70, 0.96, 0.0),
    "material-pale-plaster-01": _parameters("dominant-axis-box", 3.5, 0.55, 0.88, 0.0),
    "material-rammed-earth-01": _parameters("dominant-axis-box", 3.5, 0.85, 0.94, 0.0),
    "material-rice-paddy-water-01": _parameters("world-xy", 6.0, 0.25, 0.19, 0.0),
    "material-shallow-water-01": _parameters("world-xy", 5.0, 0.22, 0.14, 0.0),
    "material-terrace-soil-01": _parameters("world-xy", 4.0, 0.75, 0.97, 0.0),
    "material-vegetable-leaf-01": _parameters("leaf-card", 0.45, 0.55, 0.77, 0.0),
    "material-weathered-timber-01": _parameters("object-long-axis", 1.8, 0.85, 0.86, 0.0),
    "material-wet-stone-paving-01": _parameters("world-xy", 2.5, 0.80, 0.48, 0.0),
    "material-woven-bamboo-01": _parameters("object-long-axis", 1.2, 0.70, 0.83, 0.0),
}


class MaterialMapDescriptor(FrozenModel):
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1, le=MAX_DERIVED_MAP_BYTES)
    width: Literal[1024] = MAP_SIZE
    height: Literal[1024] = MAP_SIZE
    media_type: Literal["image/png"] = "image/png"
    color_space: Literal["srgb", "non-color"]

    @model_validator(mode="after")
    def _content_addressed_path(self) -> MaterialMapDescriptor:
        expected = f"objects/{self.sha256}.png"
        parsed = PurePosixPath(self.object_path)
        if (
            self.object_path != expected
            or parsed.as_posix() != self.object_path
            or parsed.is_absolute()
        ):
            raise ValueError("material map path must be a content-addressed PNG")
        return self


class DerivedMaterialRecord(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_sha256: Sha256
    source_width: int = Field(ge=1)
    source_height: int = Field(ge=1)
    base_color: MaterialMapDescriptor
    normal: MaterialMapDescriptor
    orm: MaterialMapDescriptor
    uv_policy: UvPolicy
    nominal_tile_m: float = Field(gt=0, allow_inf_nan=False)
    normal_strength: float = Field(gt=0, allow_inf_nan=False)
    roughness_center: float = Field(ge=0, le=1, allow_inf_nan=False)
    metallic: float = Field(ge=0, le=1, allow_inf_nan=False)
    replacement_contract_sha256: Sha256
    synthetic: Literal[True] = True

    @model_validator(mode="after")
    def _map_roles_are_exact(self) -> DerivedMaterialRecord:
        if (
            self.base_color.color_space != "srgb"
            or self.normal.color_space != "non-color"
            or self.orm.color_space != "non-color"
        ):
            raise ValueError("material map color-space roles are invalid")
        return self


class DerivedMaterialBundle(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.derived-material-bundle.v1"
    ] = MATERIAL_BUNDLE_SCHEMA
    bundle_id: Sha256
    synthetic: Literal[True] = True
    source_pack_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_manifest_sha256: Sha256
    algorithm_id: Literal["mirror-sobel-orm-v1"] = ALGORITHM_ID
    python_version: str = Field(min_length=1)
    pillow_version: str = Field(min_length=1)
    module_sha256: Sha256
    records: tuple[DerivedMaterialRecord, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def _complete_stable_identity(self) -> DerivedMaterialBundle:
        slot_ids = [record.slot_id for record in self.records]
        expected = sorted(MATERIAL_PARAMETERS)
        if slot_ids != expected or len(set(slot_ids)) != 24:
            raise ValueError("material bundle must contain the exact sorted 24-slot contract")
        digest = hashlib.sha256(
            canonical_material_bundle_bytes(self, exclude_bundle_id=True),
        ).hexdigest()
        if digest != self.bundle_id:
            raise ValueError("material bundle ID does not match canonical content")
        return self


@dataclass(frozen=True)
class PreparedMaterialBundle:
    staging_root: Path
    manifest: DerivedMaterialBundle

    def open_map(self, descriptor: MaterialMapDescriptor) -> Image.Image:
        path = self.staging_root / descriptor.object_path
        with Image.open(path) as image:
            image.load()
            return image.copy()


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    value = _jsonable(value)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def canonical_material_bundle_bytes(
    manifest: DerivedMaterialBundle,
    *,
    exclude_bundle_id: bool = False,
) -> bytes:
    payload = manifest.model_dump(mode="json")
    if exclude_bundle_id:
        payload.pop("bundle_id")
    return _canonical_json_bytes(payload)


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(
        output,
        format="PNG",
        compress_level=9,
        optimize=False,
    )
    return output.getvalue()


def _mirror_tile(image: Image.Image) -> Image.Image:
    square = ImageOps.fit(
        ImageOps.exif_transpose(image).convert("RGB"),
        (MAP_SIZE, MAP_SIZE),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    mosaic = Image.new("RGB", (MAP_SIZE * 2, MAP_SIZE * 2))
    mosaic.paste(square, (0, 0))
    mosaic.paste(ImageOps.mirror(square), (MAP_SIZE, 0))
    mosaic.paste(ImageOps.flip(square), (0, MAP_SIZE))
    mosaic.paste(
        ImageOps.flip(ImageOps.mirror(square)),
        (MAP_SIZE, MAP_SIZE),
    )
    offset = MAP_SIZE // 2
    return mosaic.crop((offset, offset, offset + MAP_SIZE, offset + MAP_SIZE))


def _luminance(rgb: np.ndarray) -> np.ndarray:
    values = rgb.astype(np.uint16)
    return (
        values[..., 0] * 54
        + values[..., 1] * 183
        + values[..., 2] * 19
        + 128
    ) >> 8


def _sobel(luminance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = luminance.astype(np.int32)
    top_left = np.roll(np.roll(values, 1, axis=0), 1, axis=1)
    top = np.roll(values, 1, axis=0)
    top_right = np.roll(np.roll(values, 1, axis=0), -1, axis=1)
    left = np.roll(values, 1, axis=1)
    right = np.roll(values, -1, axis=1)
    bottom_left = np.roll(np.roll(values, -1, axis=0), 1, axis=1)
    bottom = np.roll(values, -1, axis=0)
    bottom_right = np.roll(np.roll(values, -1, axis=0), -1, axis=1)
    gradient_x = (
        -top_left
        + top_right
        - 2 * left
        + 2 * right
        - bottom_left
        + bottom_right
    )
    gradient_y = (
        -top_left
        - 2 * top
        - top_right
        + bottom_left
        + 2 * bottom
        + bottom_right
    )
    return gradient_x, gradient_y


def _normal_map(luminance: np.ndarray, strength: float) -> Image.Image:
    gradient_x, gradient_y = _sobel(luminance)
    vectors = np.stack(
        (
            -gradient_x.astype(np.float64) * strength,
            -gradient_y.astype(np.float64) * strength,
            np.full_like(gradient_x, 255, dtype=np.float64),
        ),
        axis=2,
    )
    lengths = np.linalg.norm(vectors, axis=2, keepdims=True)
    vectors /= lengths
    encoded = np.rint((vectors * 0.5 + 0.5) * 255.0)
    return Image.fromarray(np.clip(encoded, 0, 255).astype(np.uint8), mode="RGB")


def _orm_map(
    luminance: np.ndarray,
    *,
    roughness_center: float,
    metallic: float,
) -> Image.Image:
    values = luminance.astype(np.float64)
    local_average = (
        np.roll(values, 1, axis=0)
        + np.roll(values, -1, axis=0)
        + np.roll(values, 1, axis=1)
        + np.roll(values, -1, axis=1)
    ) / 4.0
    contrast = np.abs(values - local_average)
    occlusion = np.clip(np.rint(255.0 - contrast * 2.0), 0, 255)
    roughness = np.clip(
        np.rint(roughness_center * 255.0 + (values - local_average) * 0.25),
        0,
        255,
    )
    metallic_channel = np.full(values.shape, round(metallic * 255.0))
    encoded = np.stack((occlusion, roughness, metallic_channel), axis=2)
    return Image.fromarray(encoded.astype(np.uint8), mode="RGB")


def _map_descriptor(
    *,
    payload: bytes,
    color_space: Literal["srgb", "non-color"],
) -> MaterialMapDescriptor:
    digest = hashlib.sha256(payload).hexdigest()
    return MaterialMapDescriptor(
        object_path=f"objects/{digest}.png",
        sha256=digest,
        bytes=len(payload),
        color_space=color_space,
    )


def _write_object(root: Path, descriptor: MaterialMapDescriptor, payload: bytes) -> None:
    path = root / descriptor.object_path
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise MaterialBundleError("derived object conflicts with its content address")
        return
    path.write_bytes(payload)


def _material_slot_contracts() -> dict[str, str]:
    return {
        slot.slot_id: slot.replacement_contract
        for slot in load_default_visual_slots().slots
        if slot.category == "material"
    }


def _source_image(record, visual_pack_root: Path) -> Image.Image:
    path = visual_pack_root / record.object_path
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise MaterialBundleError(f"material source cannot be read: {record.slot_id}") from exc
    if len(payload) != record.bytes or hashlib.sha256(payload).hexdigest() != record.sha256:
        raise MaterialBundleError(f"material source bytes do not match: {record.slot_id}")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.size != (record.width, record.height):
                raise MaterialBundleError(
                    f"material source dimensions do not match: {record.slot_id}",
                )
            return image.copy()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise MaterialBundleError(f"material source cannot be decoded: {record.slot_id}") from exc


def prepare_material_bundle(
    *,
    visual_pack_root: Path,
    staging_root: Path,
) -> PreparedMaterialBundle:
    """Derive one complete path-free material bundle in an absent staging root."""

    visual_pack_root = Path(visual_pack_root).absolute()
    staging_root = Path(staging_root).absolute()
    if staging_root.exists() or staging_root.is_symlink():
        raise MaterialBundleError("material staging root must start absent")
    try:
        source_manifest = load_visual_source_manifest(
            visual_pack_root / VISUAL_MANIFEST_NAME,
        )
        source_records = {
            record.slot_id: record
            for record in source_manifest.records
            if record.category == "material"
        }
        expected_slots = set(MATERIAL_PARAMETERS)
        if set(source_records) != expected_slots or len(source_records) != 24:
            raise MaterialBundleError("visual pack must contain the exact 24 material sources")
        contracts = _material_slot_contracts()
        if set(contracts) != expected_slots:
            raise MaterialBundleError("tracked catalog does not match the 24 material contract")

        staging_root.mkdir(parents=True, exist_ok=False)
        object_root = staging_root / "objects"
        object_root.mkdir()
        derived_records = []
        for slot_id in sorted(expected_slots):
            source_record = source_records[slot_id]
            parameters = MATERIAL_PARAMETERS[slot_id]
            tiled = _mirror_tile(_source_image(source_record, visual_pack_root))
            luminance = _luminance(np.asarray(tiled, dtype=np.uint8))
            base_payload = _png_bytes(tiled)
            normal_payload = _png_bytes(
                _normal_map(luminance, parameters.normal_strength),
            )
            orm_payload = _png_bytes(
                _orm_map(
                    luminance,
                    roughness_center=parameters.roughness_center,
                    metallic=parameters.metallic,
                ),
            )
            base_descriptor = _map_descriptor(
                payload=base_payload,
                color_space="srgb",
            )
            normal_descriptor = _map_descriptor(
                payload=normal_payload,
                color_space="non-color",
            )
            orm_descriptor = _map_descriptor(
                payload=orm_payload,
                color_space="non-color",
            )
            _write_object(staging_root, base_descriptor, base_payload)
            _write_object(staging_root, normal_descriptor, normal_payload)
            _write_object(staging_root, orm_descriptor, orm_payload)
            derived_records.append(
                DerivedMaterialRecord(
                    slot_id=slot_id,
                    source_sha256=source_record.sha256,
                    source_width=source_record.width,
                    source_height=source_record.height,
                    base_color=base_descriptor,
                    normal=normal_descriptor,
                    orm=orm_descriptor,
                    uv_policy=parameters.uv_policy,
                    nominal_tile_m=parameters.nominal_tile_m,
                    normal_strength=parameters.normal_strength,
                    roughness_center=parameters.roughness_center,
                    metallic=parameters.metallic,
                    replacement_contract_sha256=hashlib.sha256(
                        contracts[slot_id].encode("utf-8"),
                    ).hexdigest(),
                ),
            )
        payload = {
            "schema_version": MATERIAL_BUNDLE_SCHEMA,
            "synthetic": True,
            "source_pack_id": source_manifest.pack_id,
            "source_manifest_sha256": hashlib.sha256(
                canonical_manifest_bytes(source_manifest),
            ).hexdigest(),
            "algorithm_id": ALGORITHM_ID,
            "python_version": platform.python_version(),
            "pillow_version": PIL.__version__,
            "module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "records": tuple(derived_records),
        }
        bundle_id = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
        manifest = DerivedMaterialBundle(bundle_id=bundle_id, **payload)
        (staging_root / MATERIAL_BUNDLE_MANIFEST).write_bytes(
            canonical_material_bundle_bytes(manifest),
        )
        verify_prepared_material_bundle(staging_root)
        return PreparedMaterialBundle(staging_root=staging_root, manifest=manifest)
    except MaterialBundleError:
        if staging_root.exists() and staging_root.is_dir():
            shutil.rmtree(staging_root, ignore_errors=True)
        raise
    except (OSError, VisualSourceError, ValidationError, ValueError) as exc:
        if staging_root.exists() and staging_root.is_dir():
            shutil.rmtree(staging_root, ignore_errors=True)
        raise MaterialBundleError(f"material bundle preparation failed: {exc}") from exc


def verify_prepared_material_bundle(root: Path) -> DerivedMaterialBundle:
    """Verify canonical manifest bytes and every declared derived PNG."""

    root = Path(root).absolute()
    try:
        if root.is_symlink() or not root.is_dir():
            raise MaterialBundleError("material bundle root is not a real directory")
        manifest_path = root / MATERIAL_BUNDLE_MANIFEST
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise MaterialBundleError("material bundle manifest is missing or redirected")
        raw = manifest_path.read_bytes()
        if not raw or len(raw) > MAX_MATERIAL_BUNDLE_MANIFEST_BYTES:
            raise MaterialBundleError("material bundle manifest size is invalid")
        manifest = DerivedMaterialBundle.model_validate_json(raw)
        if raw != canonical_material_bundle_bytes(manifest):
            raise MaterialBundleError("material bundle manifest is not canonical JSON")
        expected_objects = {
            descriptor.object_path
            for record in manifest.records
            for descriptor in (record.base_color, record.normal, record.orm)
        }
        object_root = root / "objects"
        if object_root.is_symlink() or not object_root.is_dir():
            raise MaterialBundleError("material bundle object root is missing or redirected")
        actual_objects = {
            path.relative_to(root).as_posix()
            for path in object_root.iterdir()
            if path.is_file() and not path.is_symlink()
        }
        if actual_objects != expected_objects or len(list(object_root.iterdir())) != len(
            actual_objects
        ):
            raise MaterialBundleError("material bundle object set is incomplete or unexpected")
        descriptors = {
            descriptor.object_path: descriptor
            for record in manifest.records
            for descriptor in (record.base_color, record.normal, record.orm)
        }
        for object_path, descriptor in descriptors.items():
            path = root / object_path
            payload = path.read_bytes()
            if (
                len(payload) != descriptor.bytes
                or len(payload) > MAX_DERIVED_MAP_BYTES
                or hashlib.sha256(payload).hexdigest() != descriptor.sha256
            ):
                raise MaterialBundleError(
                    f"derived material object does not match: {object_path}",
                )
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if image.format != "PNG" or image.mode != "RGB" or image.size != (
                    MAP_SIZE,
                    MAP_SIZE,
                ):
                    raise MaterialBundleError(
                        f"derived material object format is invalid: {object_path}",
                    )
        return manifest
    except MaterialBundleError:
        raise
    except (OSError, UnidentifiedImageError, ValidationError, ValueError) as exc:
        raise MaterialBundleError(f"material bundle verification failed: {exc}") from exc
