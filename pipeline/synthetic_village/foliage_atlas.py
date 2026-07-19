"""Deterministic shared cutout atlases derived from verified foliage PBR maps."""

from __future__ import annotations

import hashlib
import io
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import numpy as np
import PIL
from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from pipeline.synthetic_village.material_bundle import (
    DerivedMaterialBundle,
    MaterialBundleError,
    canonical_material_bundle_bytes,
    load_material_bundle,
    read_verified_material_map,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MapRole = Literal["base_color", "normal", "orm"]
ShapeId = Literal["lanceolate", "ovate-serrated", "elliptic"]

FOLIAGE_ATLAS_SCHEMA = "nantai.synthetic-village.foliage-atlas-set.v1"
FOLIAGE_ATLAS_MANIFEST = "manifest.json"
FOLIAGE_ATLAS_ALGORITHM_ID = "deterministic-foliage-cutout-v1"
ATLAS_SIZE_PX = 1024
ATLAS_GRID = 4
CELL_SIZE_PX = ATLAS_SIZE_PX // ATLAS_GRID
MASK_SUPERSAMPLE = 4
RGB_DILATION_PIXELS = 8
ALPHA_CUTOFF = 0.45
FOLIAGE_SHAPES: dict[str, tuple[ShapeId, float, float]] = {
    "material-bamboo-leaf-01": ("lanceolate", 0.20, 0.36),
    "material-broadleaf-canopy-01": ("ovate-serrated", 0.28, 0.52),
    "material-orchard-leaf-01": ("elliptic", 0.24, 0.46),
}
FOLIAGE_SLOTS = tuple(sorted(FOLIAGE_SHAPES))


class FoliageAtlasError(ValueError):
    """Foliage atlas bytes or their derivation evidence cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FoliageAtlasSourceMap(FrozenModel):
    role: MapRole
    sha256: Sha256
    bytes: int = Field(ge=1)


class FoliageAtlasCell(FrozenModel):
    cell_index: int = Field(ge=0, lt=ATLAS_GRID * ATLAS_GRID)
    crop_origin_px: tuple[int, int]
    angle_degrees: float = Field(ge=-18.0, le=18.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _valid_crop_origin(self) -> FoliageAtlasCell:
        if any(value < 0 or value >= ATLAS_SIZE_PX for value in self.crop_origin_px):
            raise ValueError("foliage atlas crop origin is outside the source map")
        return self


class FoliageAtlasObject(FrozenModel):
    role: MapRole
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1, le=32 * 1024 * 1024)
    width: Literal[1024] = ATLAS_SIZE_PX
    height: Literal[1024] = ATLAS_SIZE_PX
    media_type: Literal["image/png"] = "image/png"
    colour_space: Literal["srgb", "non-color"]
    pixel_mode: Literal["RGBA", "RGB"]

    @model_validator(mode="after")
    def _exact_object_semantics(self) -> FoliageAtlasObject:
        expected = f"textures/{self.sha256}.png"
        parsed = PurePosixPath(self.object_path)
        if (
            self.object_path != expected
            or parsed.as_posix() != self.object_path
            or parsed.is_absolute()
        ):
            raise ValueError("foliage atlas path must be content-addressed")
        expected_semantics = (
            ("srgb", "RGBA")
            if self.role == "base_color"
            else ("non-color", "RGB")
        )
        if (self.colour_space, self.pixel_mode) != expected_semantics:
            raise ValueError("foliage atlas role has invalid image semantics")
        return self


class FoliageAtlasRecord(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    shape_id: ShapeId
    coverage_min: float = Field(ge=0, le=1, allow_inf_nan=False)
    coverage_max: float = Field(ge=0, le=1, allow_inf_nan=False)
    alpha_coverage: float = Field(gt=0, lt=1, allow_inf_nan=False)
    source_maps: tuple[FoliageAtlasSourceMap, ...] = Field(
        min_length=3,
        max_length=3,
    )
    cells: tuple[FoliageAtlasCell, ...] = Field(
        min_length=ATLAS_GRID * ATLAS_GRID,
        max_length=ATLAS_GRID * ATLAS_GRID,
    )
    base_color: FoliageAtlasObject
    normal: FoliageAtlasObject
    orm: FoliageAtlasObject

    @model_validator(mode="after")
    def _exact_record_contract(self) -> FoliageAtlasRecord:
        expected_shape = FOLIAGE_SHAPES.get(self.slot_id)
        if expected_shape is None or (
            self.shape_id,
            self.coverage_min,
            self.coverage_max,
        ) != expected_shape:
            raise ValueError("foliage atlas slot shape contract is invalid")
        if not self.coverage_min <= self.alpha_coverage <= self.coverage_max:
            raise ValueError("foliage atlas alpha coverage is outside its band")
        if tuple(row.role for row in self.source_maps) != (
            "base_color",
            "normal",
            "orm",
        ):
            raise ValueError("foliage atlas source maps are incomplete or unsorted")
        if tuple(row.cell_index for row in self.cells) != tuple(
            range(ATLAS_GRID * ATLAS_GRID),
        ):
            raise ValueError("foliage atlas cell layout is incomplete or unsorted")
        if (
            self.base_color.role,
            self.normal.role,
            self.orm.role,
        ) != ("base_color", "normal", "orm"):
            raise ValueError("foliage atlas outputs have the wrong roles")
        return self


class FoliageAtlasSet(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.foliage-atlas-set.v1"
    ] = FOLIAGE_ATLAS_SCHEMA
    atlas_set_id: Sha256
    algorithm_id: Literal[
        "deterministic-foliage-cutout-v1"
    ] = FOLIAGE_ATLAS_ALGORITHM_ID
    source_material_bundle_id: Sha256
    source_material_manifest_sha256: Sha256
    pillow_version: str = Field(min_length=1)
    atlas_size_px: Literal[1024] = ATLAS_SIZE_PX
    atlas_grid: Literal[4] = ATLAS_GRID
    mask_supersample: Literal[4] = MASK_SUPERSAMPLE
    rgb_dilation_pixels: Literal[8] = RGB_DILATION_PIXELS
    alpha_cutoff: Literal[0.45] = ALPHA_CUTOFF
    synthetic: Literal[True] = True
    real_photo_textures: Literal[False] = False
    records: tuple[FoliageAtlasRecord, ...] = Field(min_length=3, max_length=3)

    @property
    def by_slot(self) -> dict[str, FoliageAtlasRecord]:
        return {record.slot_id: record for record in self.records}

    @model_validator(mode="after")
    def _complete_stable_identity(self) -> FoliageAtlasSet:
        slot_ids = tuple(record.slot_id for record in self.records)
        if slot_ids != FOLIAGE_SLOTS or len(set(slot_ids)) != len(slot_ids):
            raise ValueError("foliage atlas set must contain exact sorted slots")
        digest = hashlib.sha256(
            canonical_foliage_atlas_set_bytes(
                self,
                exclude_atlas_set_id=True,
            ),
        ).hexdigest()
        if digest != self.atlas_set_id:
            raise ValueError("foliage atlas set ID does not match canonical content")
        return self


@dataclass(frozen=True)
class PreparedFoliageAtlasSet:
    root: Path
    manifest: FoliageAtlasSet


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


def canonical_foliage_atlas_set_bytes(
    manifest: FoliageAtlasSet,
    *,
    exclude_atlas_set_id: bool = False,
) -> bytes:
    payload = manifest.model_dump(mode="json")
    if exclude_atlas_set_id:
        payload.pop("atlas_set_id")
    return _canonical_json_bytes(payload)


def _inside_leaf(
    shape: ShapeId,
    x: float | np.ndarray,
    y: float | np.ndarray,
) -> bool | np.ndarray:
    if shape == "lanceolate":
        return np.abs(y) <= np.maximum(0.0, 1.0 - np.abs(x)) ** 1.65 * 0.34
    if shape == "elliptic":
        return x * x + (y / 0.62) ** 2 <= 1.0
    radius = np.maximum(0.0, 1.0 - np.abs(x) ** 1.7)
    serration = 0.055 * np.sin((np.arctan2(y, x) + math.pi) * 18.0)
    return np.abs(y) <= np.maximum(0.0, radius * 0.58 + serration)


def _layout_for(slot_id: str) -> tuple[FoliageAtlasCell, ...]:
    cells = []
    for cell_index in range(ATLAS_GRID * ATLAS_GRID):
        digest = hashlib.sha256(f"{slot_id}:{cell_index}".encode()).digest()
        crop_x = int.from_bytes(digest[0:2], "big") % ATLAS_SIZE_PX
        crop_y = int.from_bytes(digest[2:4], "big") % ATLAS_SIZE_PX
        angle_millidegrees = int.from_bytes(digest[4:6], "big") % 36_001 - 18_000
        cells.append(
            FoliageAtlasCell(
                cell_index=cell_index,
                crop_origin_px=(crop_x, crop_y),
                angle_degrees=angle_millidegrees / 1_000.0,
            ),
        )
    return tuple(cells)


def _cell_mask(shape: ShapeId, angle_degrees: float) -> np.ndarray:
    high_size = CELL_SIZE_PX * MASK_SUPERSAMPLE
    axis = (np.arange(high_size, dtype=np.float64) + 0.5) / high_size * 2.0 - 1.0
    x, y = np.meshgrid(axis, axis)
    angle = math.radians(angle_degrees)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    rotated_x = cosine * x + sine * y
    rotated_y = -sine * x + cosine * y
    if shape == "lanceolate":
        high_mask = np.zeros_like(rotated_x, dtype=bool)
        for offset, leaf_angle_degrees in (
            (-0.40, -5.0),
            (0.0, 0.0),
            (0.40, 5.0),
        ):
            leaf_angle = math.radians(leaf_angle_degrees)
            leaf_cosine = math.cos(leaf_angle)
            leaf_sine = math.sin(leaf_angle)
            leaf_x = (
                leaf_cosine * rotated_x + leaf_sine * rotated_y
            ) / 0.92
            leaf_y = (
                -leaf_sine * rotated_x + leaf_cosine * rotated_y - offset
            ) / 0.72
            high_mask |= np.asarray(
                _inside_leaf(shape, leaf_x, leaf_y),
                dtype=bool,
            )
    elif shape == "ovate-serrated":
        normalized_x = rotated_x / 0.92
        normalized_y = rotated_y / 0.92
        high_mask = np.asarray(
            _inside_leaf(shape, normalized_x, normalized_y),
            dtype=bool,
        )
    else:
        normalized_x = rotated_x / 0.88
        normalized_y = rotated_y / 0.88
        high_mask = np.asarray(
            _inside_leaf(shape, normalized_x, normalized_y),
            dtype=bool,
        )
    encoded_mask = high_mask.astype(np.uint8) * 255
    return np.asarray(
        Image.fromarray(encoded_mask, mode="L").resize(
            (CELL_SIZE_PX, CELL_SIZE_PX),
            resample=Image.Resampling.NEAREST,
        ),
        dtype=np.uint8,
    )


def _sample_cell(
    source: np.ndarray,
    *,
    origin: tuple[int, int],
    angle_degrees: float,
) -> np.ndarray:
    axis = np.arange(CELL_SIZE_PX, dtype=np.float64) - (
        CELL_SIZE_PX - 1
    ) / 2.0
    x, y = np.meshgrid(axis, axis)
    angle = math.radians(angle_degrees)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    source_x = np.rint(origin[0] + cosine * x - sine * y).astype(np.int64)
    source_y = np.rint(origin[1] + sine * x + cosine * y).astype(np.int64)
    source_x %= ATLAS_SIZE_PX
    source_y %= ATLAS_SIZE_PX
    return source[source_y, source_x]


def _build_layout_pixels(
    source_maps: dict[MapRole, np.ndarray],
    *,
    shape: ShapeId,
    cells: tuple[FoliageAtlasCell, ...],
) -> tuple[dict[MapRole, np.ndarray], np.ndarray]:
    sampled = {
        role: np.empty((ATLAS_SIZE_PX, ATLAS_SIZE_PX, 3), dtype=np.uint8)
        for role in ("base_color", "normal", "orm")
    }
    alpha = np.empty((ATLAS_SIZE_PX, ATLAS_SIZE_PX), dtype=np.uint8)
    for cell in cells:
        row, column = divmod(cell.cell_index, ATLAS_GRID)
        y_slice = slice(row * CELL_SIZE_PX, (row + 1) * CELL_SIZE_PX)
        x_slice = slice(column * CELL_SIZE_PX, (column + 1) * CELL_SIZE_PX)
        for role, source in source_maps.items():
            sampled[role][y_slice, x_slice] = _sample_cell(
                source,
                origin=cell.crop_origin_px,
                angle_degrees=cell.angle_degrees,
            )
        alpha[y_slice, x_slice] = _cell_mask(shape, cell.angle_degrees)
    return sampled, alpha


def _shift_without_wrap(
    values: np.ndarray,
    *,
    dy: int,
    dx: int,
    fill: int | bool,
) -> np.ndarray:
    shifted = np.roll(values, shift=(dy, dx), axis=(0, 1))
    if dy > 0:
        shifted[:dy] = fill
    elif dy < 0:
        shifted[dy:] = fill
    if dx > 0:
        shifted[:, :dx] = fill
    elif dx < 0:
        shifted[:, dx:] = fill
    return shifted


def _dilate_rgb_under_alpha(
    rgb: np.ndarray,
    alpha: np.ndarray,
) -> np.ndarray:
    output = rgb.copy()
    known = alpha == 255
    neighbours = (
        (-1, 0),
        (0, -1),
        (0, 1),
        (1, 0),
        (-1, -1),
        (-1, 1),
        (1, -1),
        (1, 1),
    )
    for _distance in range(RGB_DILATION_PIXELS):
        previous_known = known.copy()
        previous_rgb = output.copy()
        for dy, dx in neighbours:
            shifted_known = _shift_without_wrap(
                previous_known,
                dy=dy,
                dx=dx,
                fill=False,
            )
            fill = ~known & shifted_known
            if not np.any(fill):
                continue
            shifted_rgb = _shift_without_wrap(
                previous_rgb,
                dy=dy,
                dx=dx,
                fill=0,
            )
            output[fill] = shifted_rgb[fill]
            known[fill] = True
    return output


def _decode_source_map(
    payload: bytes,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> np.ndarray:
    if (
        len(payload) != expected_bytes
        or hashlib.sha256(payload).hexdigest() != expected_sha256
    ):
        raise FoliageAtlasError("source material map bytes changed")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or image.size != (ATLAS_SIZE_PX, ATLAS_SIZE_PX)
            ):
                raise FoliageAtlasError(
                    "source material map must be an exact 1024 px RGB PNG",
                )
            return np.asarray(image, dtype=np.uint8).copy()
    except FoliageAtlasError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise FoliageAtlasError("source material map is not a PNG") from exc


def _png_bytes(pixels: np.ndarray, *, mode: Literal["RGBA", "RGB"]) -> bytes:
    output = io.BytesIO()
    Image.fromarray(pixels, mode=mode).save(
        output,
        format="PNG",
        compress_level=9,
        optimize=False,
    )
    return output.getvalue()


def _atlas_object(
    *,
    role: MapRole,
    payload: bytes,
) -> FoliageAtlasObject:
    digest = hashlib.sha256(payload).hexdigest()
    return FoliageAtlasObject(
        role=role,
        object_path=f"textures/{digest}.png",
        sha256=digest,
        bytes=len(payload),
        colour_space="srgb" if role == "base_color" else "non-color",
        pixel_mode="RGBA" if role == "base_color" else "RGB",
    )


def _write_object(root: Path, descriptor: FoliageAtlasObject, payload: bytes) -> None:
    path = root / descriptor.object_path
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != payload:
            raise FoliageAtlasError("foliage atlas content address conflicts")
        return
    path.write_bytes(payload)


def _read_stable_output(path: Path, *, maximum_bytes: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True) != path:
        raise FoliageAtlasError("foliage atlas output object is redirected")
    before = path.stat()
    if before.st_size <= 0 or before.st_size > maximum_bytes:
        raise FoliageAtlasError("foliage atlas output size is invalid")
    payload = path.read_bytes()
    after = path.stat()
    before_signature = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_signature = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_signature != after_signature or len(payload) != before.st_size:
        raise FoliageAtlasError("foliage atlas output changed during verification")
    return payload


def _verify_prepared_atlas_set(
    root: Path,
    manifest: FoliageAtlasSet,
) -> None:
    manifest_bytes = _read_stable_output(
        root / FOLIAGE_ATLAS_MANIFEST,
        maximum_bytes=4 * 1024 * 1024,
    )
    if manifest_bytes != canonical_foliage_atlas_set_bytes(manifest):
        raise FoliageAtlasError("foliage atlas manifest is not canonical")
    texture_root = root / "textures"
    if (
        texture_root.is_symlink()
        or not texture_root.is_dir()
        or texture_root.resolve(strict=True) != texture_root
    ):
        raise FoliageAtlasError("foliage atlas texture root is redirected")
    descriptors = {
        descriptor.object_path: descriptor
        for record in manifest.records
        for descriptor in (record.base_color, record.normal, record.orm)
    }
    entries = tuple(texture_root.iterdir())
    actual = {
        path.relative_to(root).as_posix()
        for path in entries
        if path.is_file() and not path.is_symlink()
    }
    if actual != set(descriptors) or len(entries) != len(actual):
        raise FoliageAtlasError("foliage atlas texture closure is incomplete")
    for relative, descriptor in descriptors.items():
        payload = _read_stable_output(
            root / relative,
            maximum_bytes=32 * 1024 * 1024,
        )
        if (
            len(payload) != descriptor.bytes
            or hashlib.sha256(payload).hexdigest() != descriptor.sha256
        ):
            raise FoliageAtlasError("foliage atlas texture bytes changed")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if (
                    image.format != "PNG"
                    or image.mode != descriptor.pixel_mode
                    or image.size != (descriptor.width, descriptor.height)
                ):
                    raise FoliageAtlasError(
                        "foliage atlas texture image semantics changed",
                    )
        except FoliageAtlasError:
            raise
        except (OSError, UnidentifiedImageError) as exc:
            raise FoliageAtlasError(
                "foliage atlas texture is not a valid PNG",
            ) from exc


def _validate_output_root(output_root: Path) -> Path:
    output = Path(output_root).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise FoliageAtlasError("foliage atlas output already exists")
    parent = output.parent
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise FoliageAtlasError("foliage atlas output parent is unavailable") from exc
    if parent.is_symlink() or not parent.is_dir() or resolved_parent != parent:
        raise FoliageAtlasError("foliage atlas output parent is redirected")
    return output


def _source_record_map(bundle: DerivedMaterialBundle) -> dict[str, object]:
    records = {record.slot_id: record for record in bundle.records}
    if not set(FOLIAGE_SLOTS) <= set(records):
        raise FoliageAtlasError("source material bundle lacks exact foliage slots")
    return records


def build_foliage_atlas_set(
    material_bundle_root: Path,
    output_root: Path,
) -> PreparedFoliageAtlasSet:
    """Build an absent-only path-free atlas set from verified material bytes."""

    output = _validate_output_root(output_root)
    try:
        source_bundle = load_material_bundle(material_bundle_root)
        source_records = _source_record_map(source_bundle)
    except (MaterialBundleError, OSError, ValidationError, ValueError) as exc:
        raise FoliageAtlasError(
            f"source material bundle cannot be trusted: {exc}",
        ) from exc

    created = False
    try:
        output.mkdir(exist_ok=False)
        created = True
        (output / "textures").mkdir()
        records = []
        for slot_id in FOLIAGE_SLOTS:
            source_record = source_records[slot_id]
            source_maps: dict[MapRole, np.ndarray] = {}
            source_evidence = []
            for role in ("base_color", "normal", "orm"):
                descriptor = getattr(source_record, role)
                payload = read_verified_material_map(
                    material_bundle_root,
                    bundle=source_bundle,
                    slot_id=slot_id,
                    role=role,
                )
                source_maps[role] = _decode_source_map(
                    payload,
                    expected_sha256=descriptor.sha256,
                    expected_bytes=descriptor.bytes,
                )
                source_evidence.append(
                    FoliageAtlasSourceMap(
                        role=role,
                        sha256=descriptor.sha256,
                        bytes=descriptor.bytes,
                    ),
                )
            shape, coverage_min, coverage_max = FOLIAGE_SHAPES[slot_id]
            cells = _layout_for(slot_id)
            sampled, alpha = _build_layout_pixels(
                source_maps,
                shape=shape,
                cells=cells,
            )
            if set(np.unique(alpha)) != {0, 255}:
                raise FoliageAtlasError("foliage atlas alpha must be exact binary")
            alpha_coverage = float(np.count_nonzero(alpha) / alpha.size)
            if not coverage_min <= alpha_coverage <= coverage_max:
                raise FoliageAtlasError(
                    "foliage atlas alpha coverage is outside its band",
                )
            base_rgb = _dilate_rgb_under_alpha(sampled["base_color"], alpha)
            payloads = {
                "base_color": _png_bytes(
                    np.dstack((base_rgb, alpha)),
                    mode="RGBA",
                ),
                "normal": _png_bytes(sampled["normal"], mode="RGB"),
                "orm": _png_bytes(sampled["orm"], mode="RGB"),
            }
            objects = {
                role: _atlas_object(role=role, payload=payload)
                for role, payload in payloads.items()
            }
            for role, descriptor in objects.items():
                _write_object(output, descriptor, payloads[role])
            records.append(
                FoliageAtlasRecord(
                    slot_id=slot_id,
                    shape_id=shape,
                    coverage_min=coverage_min,
                    coverage_max=coverage_max,
                    alpha_coverage=alpha_coverage,
                    source_maps=tuple(source_evidence),
                    cells=cells,
                    base_color=objects["base_color"],
                    normal=objects["normal"],
                    orm=objects["orm"],
                ),
            )
        unsigned = {
            "schema_version": FOLIAGE_ATLAS_SCHEMA,
            "algorithm_id": FOLIAGE_ATLAS_ALGORITHM_ID,
            "source_material_bundle_id": source_bundle.bundle_id,
            "source_material_manifest_sha256": hashlib.sha256(
                canonical_material_bundle_bytes(source_bundle),
            ).hexdigest(),
            "pillow_version": PIL.__version__,
            "atlas_size_px": ATLAS_SIZE_PX,
            "atlas_grid": ATLAS_GRID,
            "mask_supersample": MASK_SUPERSAMPLE,
            "rgb_dilation_pixels": RGB_DILATION_PIXELS,
            "alpha_cutoff": ALPHA_CUTOFF,
            "synthetic": True,
            "real_photo_textures": False,
            "records": tuple(records),
        }
        atlas_set_id = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
        manifest = FoliageAtlasSet(
            atlas_set_id=atlas_set_id,
            **unsigned,
        )
        manifest_bytes = canonical_foliage_atlas_set_bytes(manifest)
        (output / FOLIAGE_ATLAS_MANIFEST).write_bytes(manifest_bytes)
        reloaded = FoliageAtlasSet.model_validate_json(
            (output / FOLIAGE_ATLAS_MANIFEST).read_bytes(),
        )
        if reloaded != manifest:
            raise FoliageAtlasError("foliage atlas manifest changed after writing")
        _verify_prepared_atlas_set(output, manifest)
        return PreparedFoliageAtlasSet(root=output, manifest=manifest)
    except FoliageAtlasError:
        if created:
            shutil.rmtree(output, ignore_errors=True)
        raise
    except (
        MaterialBundleError,
        OSError,
        UnidentifiedImageError,
        ValidationError,
        ValueError,
    ) as exc:
        if created:
            shutil.rmtree(output, ignore_errors=True)
        raise FoliageAtlasError(f"foliage atlas build failed: {exc}") from exc
