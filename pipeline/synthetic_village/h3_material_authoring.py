"""Deterministic 4096 authored masters and heuristic PBR maps for H3.

The selected AI output remains the native source.  This module derives a
separate seamless 4096 authored master and PBR maps; it never claims that the
source was native 4K or that normal/roughness values are measured.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import numpy as np
import PIL
import skimage
from PIL import Image, ImageFilter, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from skimage.metrics import structural_similarity

from pipeline.studio_jobs import JobContractError, ProjectFileLock

from .defaults import load_default_visual_slots
from .h3_material_sources import (
    H3_HERO_SLOTS,
    H3MaterialSourceError,
    H3MaterialSourcePack,
    _flush_directory,
    _flush_file,
    _is_linklike,
    _prepare_real_directory,
    _read_stable_bytes,
    _require_real_directory,
    load_h3_source_pack,
)
from .material_bundle import MATERIAL_PARAMETERS, UvPolicy

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

H3_AUTHORED_PACK_SCHEMA = "nantai.h3-authored-material-pack.v1"
H3_AUTHORING_ALGORITHM_ID = "sha-quilt-seam-pbr-v1"
H3_AUTHORED_PACK_MANIFEST = "manifest.json"
H3_MASTER_SIZE = 4096
H3_PATCH_SIZE = 768
H3_PATCH_OVERLAP = 128
H3_EDGE_BAND = 192
H3_MACRO_VARIATION_LIMIT = 0.04
H3_MIN_FULL_SOURCE_SSIM = 0.90
H3_MIN_INTERIOR_SOURCE_SSIM = 0.94
H3_MAX_MEAN_RGB_DELTA = 0.01
H3_MIP_DIMENSIONS = tuple(
    (size, size)
    for size in (4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1)
)
MAX_H3_AUTHORED_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_H3_AUTHORED_MAP_BYTES = 128 * 1024 * 1024


class H3AuthoredMaterialError(ValueError):
    """An H3 authored material pack cannot be built or trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class H3AuthoredMapDescriptor(FrozenModel):
    role: Literal["master", "base_color", "normal", "orm"]
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1, le=MAX_H3_AUTHORED_MAP_BYTES)
    width: Literal[4096] = H3_MASTER_SIZE
    height: Literal[4096] = H3_MASTER_SIZE
    mode: Literal["RGB"] = "RGB"
    media_type: Literal["image/png"] = "image/png"
    colour_space: Literal["srgb", "linear"]

    @model_validator(mode="after")
    def _path_and_role_are_exact(self) -> H3AuthoredMapDescriptor:
        parsed = PurePosixPath(self.object_path)
        if (
            self.object_path != f"objects/{self.sha256}.png"
            or parsed.as_posix() != self.object_path
            or parsed.is_absolute()
        ):
            raise ValueError(
                "authored map path must be a content-addressed PNG",
            )
        if self.role in {"master", "base_color"}:
            if self.colour_space != "srgb":
                raise ValueError("authored colour maps must be sRGB")
        elif self.colour_space != "linear":
            raise ValueError("authored data maps must be linear")
        return self


class H3ReplacementContract(FrozenModel):
    uv_policy: UvPolicy
    nominal_tile_m: float = Field(gt=0, allow_inf_nan=False)
    alpha_mode: Literal["OPAQUE"] = "OPAQUE"
    normal_strength: float = Field(gt=0, allow_inf_nan=False)
    roughness_center: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    metallic: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    replacement_contract_sha256: Sha256


class H3AuthoredMaterialRecord(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_sha256: Sha256
    source_width: int = Field(ge=1024)
    source_height: int = Field(ge=1024)
    source_colour_mode: Literal["RGB", "RGBA"]
    master: H3AuthoredMapDescriptor
    base_color: H3AuthoredMapDescriptor
    normal: H3AuthoredMapDescriptor
    orm: H3AuthoredMapDescriptor
    mip_dimensions: tuple[tuple[int, int], ...]
    seam_discontinuity: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    full_source_ssim: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    interior_source_ssim: float = Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    mean_rgb_delta: float = Field(
        ge=0.0,
        le=H3_MAX_MEAN_RGB_DELTA,
        allow_inf_nan=False,
    )
    material_measurement: Literal["none"] = "none"
    normal_derivation: Literal[
        "synthetic-image-gradient"
    ] = "synthetic-image-gradient"
    roughness_derivation: Literal[
        "synthetic-luminance-statistics"
    ] = "synthetic-luminance-statistics"
    metalness_policy: Literal[
        "slot-constant-or-zero"
    ] = "slot-constant-or-zero"
    replacement: H3ReplacementContract

    @model_validator(mode="after")
    def _record_contract_is_exact(self) -> H3AuthoredMaterialRecord:
        if self.mip_dimensions != H3_MIP_DIMENSIONS:
            raise ValueError("authored mip dimensions must cover 4096 through 1")
        if (
            self.master.role != "master"
            or self.base_color.role != "base_color"
            or self.normal.role != "normal"
            or self.orm.role != "orm"
        ):
            raise ValueError("authored map descriptor roles are invalid")
        if self.master.sha256 != self.base_color.sha256:
            raise ValueError(
                "the authored master and base colour must share exact bytes",
            )
        return self


class H3AuthoredMaterialPack(FrozenModel):
    schema_version: Literal[
        "nantai.h3-authored-material-pack.v1"
    ] = H3_AUTHORED_PACK_SCHEMA
    pack_id: Sha256
    source_pack_id: Sha256
    synthetic: Literal[True] = True
    ai_generated: Literal[True] = True
    real_photo_textures: Literal[False] = False
    geometry_usability: Literal["preview-only"] = "preview-only"
    metric_alignment: Literal[False] = False
    verification_level: Literal["L0"] = "L0"
    algorithm_id: Literal[
        "sha-quilt-seam-pbr-v1"
    ] = H3_AUTHORING_ALGORITHM_ID
    module_sha256: Sha256
    python_version: str = Field(min_length=1)
    pillow_version: str = Field(min_length=1)
    numpy_version: str = Field(min_length=1)
    skimage_version: str = Field(min_length=1)
    master_size: Literal[4096] = H3_MASTER_SIZE
    patch_size: Literal[768] = H3_PATCH_SIZE
    patch_overlap: Literal[128] = H3_PATCH_OVERLAP
    edge_band: Literal[192] = H3_EDGE_BAND
    macro_variation_limit: Literal[0.04] = H3_MACRO_VARIATION_LIMIT
    minimum_full_source_ssim: Literal[0.9] = H3_MIN_FULL_SOURCE_SSIM
    minimum_interior_source_ssim: Literal[0.94] = H3_MIN_INTERIOR_SOURCE_SSIM
    maximum_mean_rgb_delta: Literal[0.01] = H3_MAX_MEAN_RGB_DELTA
    records: tuple[H3AuthoredMaterialRecord, ...]

    @model_validator(mode="after")
    def _pack_is_complete_and_content_addressed(
        self,
    ) -> H3AuthoredMaterialPack:
        if tuple(record.slot_id for record in self.records) != H3_HERO_SLOTS:
            raise ValueError(
                "authored pack must contain the exact ordered H3 hero slots",
            )
        expected = hashlib.sha256(
            canonical_h3_authored_pack_bytes(
                self,
                exclude_pack_id=True,
            ),
        ).hexdigest()
        if self.pack_id != expected:
            raise ValueError(
                "authored pack ID disagrees with canonical bytes",
            )
        return self


@dataclass(frozen=True)
class PreparedH3AuthoredMaterialPack:
    root: Path
    manifest: H3AuthoredMaterialPack


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in sorted(value.items())}
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
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_h3_authored_pack_bytes(
    pack: H3AuthoredMaterialPack,
    *,
    exclude_pack_id: bool = False,
) -> bytes:
    payload = pack.model_dump(mode="json")
    if exclude_pack_id:
        payload.pop("pack_id")
    return _canonical_json_bytes(payload)


def _positions(output_size: int, patch_size: int, overlap: int) -> tuple[int, ...]:
    stride = patch_size - overlap
    values = [0]
    while values[-1] + patch_size < output_size:
        next_value = min(values[-1] + stride, output_size - patch_size)
        if next_value == values[-1]:
            break
        values.append(next_value)
    return tuple(values)


def _flatten_low_frequency_illumination(pixels: np.ndarray) -> np.ndarray:
    image = Image.fromarray(pixels, mode="RGB")
    radius = max(2.0, min(image.size) / 18.0)
    low = np.asarray(
        image.filter(ImageFilter.GaussianBlur(radius=radius)),
        dtype=np.float32,
    )
    values = pixels.astype(np.float32)
    target = low.reshape(-1, 3).mean(
        axis=0,
        dtype=np.float64,
    ).astype(np.float32)
    correction = np.clip(target - low, -20.4, 20.4)
    return np.clip(values + correction, 0.0, 255.0)


def _candidate_origins(
    rng: np.random.Generator,
    *,
    source_width: int,
    source_height: int,
    patch_size: int,
    count: int = 12,
) -> tuple[tuple[int, int], ...]:
    max_x = source_width - patch_size
    max_y = source_height - patch_size
    return tuple(
        (
            int(rng.integers(0, max_x + 1)),
            int(rng.integers(0, max_y + 1)),
        )
        for _ in range(count)
    )


def _srgb_to_linear(values: np.ndarray) -> np.ndarray:
    """Decode 0..255 sRGB samples into deterministic linear-light floats."""

    normalized = np.asarray(values, dtype=np.float32) / 255.0
    return np.where(
        normalized <= 0.04045,
        normalized / 12.92,
        np.power((normalized + 0.055) / 1.055, 2.4),
    ).astype(np.float32, copy=False)


def _minimum_error_path(cost: np.ndarray) -> np.ndarray:
    """Return one deterministic top-to-bottom minimum-error seam."""

    values = np.asarray(cost, dtype=np.float64)
    if (
        values.ndim != 2
        or values.shape[0] < 1
        or values.shape[1] < 1
        or not bool(np.isfinite(values).all())
    ):
        raise H3AuthoredMaterialError("minimum-error seam cost is invalid")
    height, width = values.shape
    cumulative = values.copy()
    parents = np.zeros((height, width), dtype=np.int32)
    for row in range(1, height):
        previous = cumulative[row - 1]
        for column in range(width):
            start = max(0, column - 1)
            stop = min(width, column + 2)
            predecessor = start + int(np.argmin(previous[start:stop]))
            cumulative[row, column] += previous[predecessor]
            parents[row, column] = predecessor
    path = np.empty(height, dtype=np.int32)
    path[-1] = int(np.argmin(cumulative[-1]))
    for row in range(height - 1, 0, -1):
        path[row - 1] = parents[row, path[row]]
    return path


def _overlap_energy(
    region: np.ndarray,
    patch: np.ndarray,
    covered: np.ndarray,
) -> float:
    sample = covered[::8, ::8]
    if not bool(sample.any()):
        return 0.0
    difference = _srgb_to_linear(region[::8, ::8]) - _srgb_to_linear(
        patch[::8, ::8],
    )
    return float(np.square(difference[sample]).mean())


def _blend_patch(
    region: np.ndarray,
    patch: np.ndarray,
    covered: np.ndarray,
    *,
    x: int,
    y: int,
    overlap: int,
) -> None:
    if not bool(covered.any()):
        region[:] = patch
        covered[:] = True
        return
    height, width = covered.shape
    cut_mask = np.ones((height, width), dtype=bool)
    if x > 0:
        band_width = min(overlap, width)
        difference = _srgb_to_linear(
            region[:, :band_width],
        ) - _srgb_to_linear(
            patch[:, :band_width],
        )
        cost = np.square(difference).mean(axis=2)
        seam = _minimum_error_path(cost)
        columns = np.arange(band_width, dtype=np.int32)[None, :]
        cut_mask[:, :band_width] &= columns >= seam[:, None]
    if y > 0:
        band_height = min(overlap, height)
        difference = _srgb_to_linear(
            region[:band_height],
        ) - _srgb_to_linear(
            patch[:band_height],
        )
        cost = np.square(difference).mean(axis=2).T
        seam = _minimum_error_path(cost)
        rows = np.arange(band_height, dtype=np.int32)[:, None]
        cut_mask[:band_height] &= rows >= seam[None, :]
    use_patch = np.logical_or(~covered, cut_mask)
    region[use_patch] = patch[use_patch]
    covered[:] = True


def _enforce_opposite_edges(
    pixels: np.ndarray,
    *,
    edge_band: int,
) -> np.ndarray:
    output = pixels.astype(np.float32, copy=True)
    for offset in range(edge_band):
        progress = offset / max(1, edge_band - 1)
        keep = progress * progress * (3.0 - 2.0 * progress)
        left = output[:, offset].copy()
        right = output[:, -1 - offset].copy()
        midpoint = (left + right) * 0.5
        output[:, offset] = midpoint * (1.0 - keep) + left * keep
        output[:, -1 - offset] = midpoint * (1.0 - keep) + right * keep
    for offset in range(edge_band):
        progress = offset / max(1, edge_band - 1)
        keep = progress * progress * (3.0 - 2.0 * progress)
        top = output[offset].copy()
        bottom = output[-1 - offset].copy()
        midpoint = (top + bottom) * 0.5
        output[offset] = midpoint * (1.0 - keep) + top * keep
        output[-1 - offset] = midpoint * (1.0 - keep) + bottom * keep
    result = np.clip(np.rint(output), 0, 255).astype(np.uint8)
    result[:, -1] = result[:, 0]
    result[-1] = result[0]
    return result


def _author_master(
    source: Image.Image,
    *,
    source_sha256: str,
    output_size: int = H3_MASTER_SIZE,
    patch_size: int = H3_PATCH_SIZE,
    overlap: int = H3_PATCH_OVERLAP,
    edge_band: int = H3_EDGE_BAND,
) -> Image.Image:
    """Quilt one deterministic seamless RGB master from captured source bytes."""

    if (
        output_size < 64
        or patch_size <= overlap
        or patch_size > output_size
        or edge_band <= 0
        or edge_band * 2 >= output_size
    ):
        raise H3AuthoredMaterialError("authoring dimensions are invalid")
    source_rgb = source.convert("RGB")
    if min(source_rgb.size) != output_size:
        scale = output_size / min(source_rgb.size)
        source_rgb = source_rgb.resize(
            (
                max(output_size, round(source_rgb.width * scale)),
                max(output_size, round(source_rgb.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
    source_pixels = np.asarray(source_rgb, dtype=np.uint8)
    if all(int(np.ptp(source_pixels[..., channel])) == 0 for channel in range(3)):
        colour = source_pixels[0, 0]
        constant = np.broadcast_to(
            colour,
            (output_size, output_size, 3),
        ).copy()
        return Image.fromarray(constant, mode="RGB")

    corrected = _flatten_low_frequency_illumination(source_pixels)
    canvas = np.zeros(
        (output_size, output_size, 3),
        dtype=np.float32,
    )
    covered = np.zeros((output_size, output_size), dtype=bool)
    rng = np.random.default_rng(int(source_sha256[:16], 16))
    positions = _positions(output_size, patch_size, overlap)
    source_offset_x = max(0, (corrected.shape[1] - output_size) // 2)
    source_offset_y = max(0, (corrected.shape[0] - output_size) // 2)
    for y in positions:
        for x in positions:
            region = canvas[y : y + patch_size, x : x + patch_size]
            region_covered = covered[
                y : y + patch_size,
                x : x + patch_size,
            ]
            reference_origin = (
                min(source_offset_x + x, corrected.shape[1] - patch_size),
                min(source_offset_y + y, corrected.shape[0] - patch_size),
            )
            random_origins = _candidate_origins(
                rng,
                source_width=corrected.shape[1],
                source_height=corrected.shape[0],
                patch_size=patch_size,
            )
            candidates = (
                reference_origin,
                *(origin for origin in random_origins if origin != reference_origin),
            )
            scored = []
            for source_x, source_y in candidates:
                patch = corrected[
                    source_y : source_y + patch_size,
                    source_x : source_x + patch_size,
                ]
                scored.append(
                    (
                        _overlap_energy(region, patch, region_covered),
                        0 if (source_x, source_y) == reference_origin else 1,
                        source_y,
                        source_x,
                        patch,
                    ),
                )
            _, _, _, _, selected = min(
                scored,
                key=lambda item: (item[0], item[1], item[2], item[3]),
            )
            _blend_patch(
                region,
                selected,
                region_covered,
                x=x,
                y=y,
                overlap=overlap,
            )

    macro_small = source_rgb.convert("L").resize(
        (32, 32),
        Image.Resampling.BOX,
    )
    macro = np.asarray(
        macro_small.resize(
            (output_size, output_size),
            Image.Resampling.BICUBIC,
        ),
        dtype=np.float32,
    )
    macro -= float(macro.mean())
    denominator = max(float(np.max(np.abs(macro))), 1.0)
    variation = (
        macro / denominator * H3_MACRO_VARIATION_LIMIT
    )[..., None]
    canvas *= 1.0 + variation
    seamless = _enforce_opposite_edges(
        canvas,
        edge_band=edge_band,
    )
    return Image.fromarray(seamless, mode="RGB")


def _periodic_normal_map(
    base: np.ndarray,
    *,
    strength: float,
) -> Image.Image:
    rgb = base.astype(np.float32)
    luminance = (
        rgb[..., 0] * 0.2126
        + rgb[..., 1] * 0.7152
        + rgb[..., 2] * 0.0722
    ) / 255.0
    gradient_x = (
        np.roll(luminance, -1, axis=1)
        - np.roll(luminance, 1, axis=1)
    ) * (0.5 * strength)
    gradient_y = (
        np.roll(luminance, -1, axis=0)
        - np.roll(luminance, 1, axis=0)
    ) * (0.5 * strength)
    length = np.sqrt(gradient_x * gradient_x + gradient_y * gradient_y + 1.0)
    encoded = np.empty(base.shape, dtype=np.uint8)
    encoded[..., 0] = np.clip(
        np.rint((-gradient_x / length * 0.5 + 0.5) * 255.0),
        0,
        255,
    ).astype(np.uint8)
    encoded[..., 1] = np.clip(
        np.rint((-gradient_y / length * 0.5 + 0.5) * 255.0),
        0,
        255,
    ).astype(np.uint8)
    encoded[..., 2] = np.clip(
        np.rint((1.0 / length * 0.5 + 0.5) * 255.0),
        0,
        255,
    ).astype(np.uint8)
    encoded[:, -1] = encoded[:, 0]
    encoded[-1] = encoded[0]
    return Image.fromarray(encoded, mode="RGB")


def _periodic_orm_map(
    base: np.ndarray,
    *,
    roughness_center: float,
    metallic: float,
) -> Image.Image:
    rgb = base.astype(np.float32)
    luminance = (
        rgb[..., 0] * 0.2126
        + rgb[..., 1] * 0.7152
        + rgb[..., 2] * 0.0722
    )
    local_average = (
        np.roll(luminance, 1, axis=0)
        + np.roll(luminance, -1, axis=0)
        + np.roll(luminance, 1, axis=1)
        + np.roll(luminance, -1, axis=1)
    ) * 0.25
    contrast = np.abs(luminance - local_average)
    encoded = np.empty(base.shape, dtype=np.uint8)
    encoded[..., 0] = np.clip(
        np.rint(255.0 - contrast * 2.0),
        0,
        255,
    ).astype(np.uint8)
    encoded[..., 1] = np.clip(
        np.rint(
            roughness_center * 255.0
            + (luminance - local_average) * 0.25,
        ),
        0,
        255,
    ).astype(np.uint8)
    encoded[..., 2] = round(metallic * 255.0)
    encoded[:, -1] = encoded[:, 0]
    encoded[-1] = encoded[0]
    return Image.fromarray(encoded, mode="RGB")


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(
        output,
        format="PNG",
        compress_level=9,
        optimize=False,
    )
    return output.getvalue()


def _descriptor(
    payload: bytes,
    *,
    role: Literal["master", "base_color", "normal", "orm"],
    colour_space: Literal["srgb", "linear"],
) -> H3AuthoredMapDescriptor:
    digest = hashlib.sha256(payload).hexdigest()
    return H3AuthoredMapDescriptor(
        role=role,
        object_path=f"objects/{digest}.png",
        sha256=digest,
        bytes=len(payload),
        colour_space=colour_space,
    )


def _source_similarity(
    source: Image.Image,
    master: Image.Image,
) -> tuple[float, float]:
    comparison_size = 384
    source_pixels = np.asarray(
        source.convert("RGB").resize(
            (comparison_size, comparison_size),
            Image.Resampling.LANCZOS,
        ),
        dtype=np.uint8,
    )
    master_pixels = np.asarray(
        master.resize(
            (comparison_size, comparison_size),
            Image.Resampling.LANCZOS,
        ),
        dtype=np.uint8,
    )
    full = structural_similarity(
        source_pixels,
        master_pixels,
        channel_axis=2,
        data_range=255,
    )
    margin = comparison_size // 8
    interior = structural_similarity(
        source_pixels[margin:-margin, margin:-margin],
        master_pixels[margin:-margin, margin:-margin],
        channel_axis=2,
        data_range=255,
    )
    return (
        round(float(np.clip(full, 0.0, 1.0)), 8),
        round(float(np.clip(interior, 0.0, 1.0)), 8),
    )


def _mean_rgb_delta(
    source: Image.Image,
    master: Image.Image,
    *,
    comparison_size: int = 512,
) -> float:
    source_pixels = np.asarray(
        source.convert("RGB").resize(
            (comparison_size, comparison_size),
            Image.Resampling.LANCZOS,
        ),
        dtype=np.uint8,
    )
    master_pixels = np.asarray(
        master.resize(
            (comparison_size, comparison_size),
            Image.Resampling.LANCZOS,
        ),
        dtype=np.uint8,
    )
    source_mean = source_pixels.reshape(-1, 3).mean(
        axis=0,
        dtype=np.float64,
    )
    master_mean = master_pixels.reshape(-1, 3).mean(
        axis=0,
        dtype=np.float64,
    )
    return round(
        float(np.max(np.abs(master_mean - source_mean)) / 255.0),
        8,
    )


def _verify_source_preservation(
    full_ssim: float,
    interior_ssim: float,
    mean_rgb_delta: float,
) -> None:
    if (
        full_ssim < H3_MIN_FULL_SOURCE_SSIM
        or interior_ssim < H3_MIN_INTERIOR_SOURCE_SSIM
        or mean_rgb_delta > H3_MAX_MEAN_RGB_DELTA
    ):
        raise H3AuthoredMaterialError(
            "authored master fails the frozen source-preservation thresholds",
        )


def _seam_discontinuity(pixels: np.ndarray) -> float:
    horizontal = np.abs(
        pixels[:, 0].astype(np.int16) - pixels[:, -1].astype(np.int16),
    )
    vertical = np.abs(
        pixels[0].astype(np.int16) - pixels[-1].astype(np.int16),
    )
    return round(
        (float(horizontal.mean()) + float(vertical.mean())) / (2.0 * 255.0),
        8,
    )


def _replacement_contracts() -> dict[str, str]:
    return {
        slot.slot_id: slot.replacement_contract
        for slot in load_default_visual_slots().slots
        if slot.slot_id in H3_HERO_SLOTS
    }


def _write_object(root: Path, descriptor: H3AuthoredMapDescriptor, payload: bytes) -> None:
    path = root / descriptor.object_path
    if path.exists():
        current = _read_stable_bytes(
            path,
            maximum_bytes=MAX_H3_AUTHORED_MAP_BYTES,
            label="staged authored object",
        )
        if current != payload:
            raise H3AuthoredMaterialError(
                "staged authored object conflicts with its content address",
            )
        return
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _decode_source(
    root: Path,
    pack: H3MaterialSourcePack,
    slot_id: str,
) -> tuple[Image.Image, object]:
    record = next(row for row in pack.records if row.slot_id == slot_id)
    descriptor = record.native_source
    payload = _read_stable_bytes(
        root / descriptor.object_path,
        maximum_bytes=128 * 1024 * 1024,
        label=f"{slot_id} selected source",
    )
    if (
        len(payload) != descriptor.bytes
        or hashlib.sha256(payload).hexdigest() != descriptor.sha256
    ):
        raise H3AuthoredMaterialError(
            f"{slot_id} selected source SHA-256 or length disagrees",
        )
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.size != (descriptor.width, descriptor.height):
                raise H3AuthoredMaterialError(
                    f"{slot_id} selected source dimensions disagree",
                )
            return image.copy(), record
    except (OSError, UnidentifiedImageError) as exc:
        raise H3AuthoredMaterialError(
            f"{slot_id} selected source cannot be decoded",
        ) from exc


def _build_record(
    *,
    source_root: Path,
    source_pack: H3MaterialSourcePack,
    slot_id: str,
    object_root: Path,
    contracts: dict[str, str],
) -> H3AuthoredMaterialRecord:
    source, source_record = _decode_source(
        source_root,
        source_pack,
        slot_id,
    )
    parameters = MATERIAL_PARAMETERS[slot_id]
    master = _author_master(
        source,
        source_sha256=source_record.native_source.sha256,
    )
    base_pixels = np.asarray(master, dtype=np.uint8)
    base_payload = _png_bytes(master)
    normal_payload = _png_bytes(
        _periodic_normal_map(
            base_pixels,
            strength=parameters.normal_strength,
        ),
    )
    orm_payload = _png_bytes(
        _periodic_orm_map(
            base_pixels,
            roughness_center=parameters.roughness_center,
            metallic=parameters.metallic,
        ),
    )
    master_descriptor = _descriptor(
        base_payload,
        role="master",
        colour_space="srgb",
    )
    base_descriptor = _descriptor(
        base_payload,
        role="base_color",
        colour_space="srgb",
    )
    normal_descriptor = _descriptor(
        normal_payload,
        role="normal",
        colour_space="linear",
    )
    orm_descriptor = _descriptor(
        orm_payload,
        role="orm",
        colour_space="linear",
    )
    for descriptor, payload in (
        (master_descriptor, base_payload),
        (normal_descriptor, normal_payload),
        (orm_descriptor, orm_payload),
    ):
        _write_object(object_root.parent, descriptor, payload)
    full_ssim, interior_ssim = _source_similarity(source, master)
    mean_rgb_delta = _mean_rgb_delta(source, master)
    _verify_source_preservation(
        full_ssim,
        interior_ssim,
        mean_rgb_delta,
    )
    return H3AuthoredMaterialRecord(
        slot_id=slot_id,
        source_sha256=source_record.native_source.sha256,
        source_width=source_record.native_source.width,
        source_height=source_record.native_source.height,
        source_colour_mode=source_record.native_source.mode,
        master=master_descriptor,
        base_color=base_descriptor,
        normal=normal_descriptor,
        orm=orm_descriptor,
        mip_dimensions=H3_MIP_DIMENSIONS,
        seam_discontinuity=_seam_discontinuity(base_pixels),
        full_source_ssim=full_ssim,
        interior_source_ssim=interior_ssim,
        mean_rgb_delta=mean_rgb_delta,
        replacement=H3ReplacementContract(
            uv_policy=parameters.uv_policy,
            nominal_tile_m=parameters.nominal_tile_m,
            alpha_mode="OPAQUE",
            normal_strength=parameters.normal_strength,
            roughness_center=parameters.roughness_center,
            metallic=parameters.metallic,
            replacement_contract_sha256=hashlib.sha256(
                contracts[slot_id].encode("utf-8"),
            ).hexdigest(),
        ),
    )


def _directory_closure(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*")),
    )


def _map_descriptors(
    pack: H3AuthoredMaterialPack,
) -> tuple[H3AuthoredMapDescriptor, ...]:
    return tuple(
        descriptor
        for record in pack.records
        for descriptor in (
            record.master,
            record.base_color,
            record.normal,
            record.orm,
        )
    )


def load_h3_authored_material_pack(root: Path) -> H3AuthoredMaterialPack:
    root = Path(root).expanduser().absolute()
    try:
        _require_real_directory(root, label="H3 authored pack")
        _require_real_directory(
            root / "objects",
            label="H3 authored object directory",
        )
        raw = _read_stable_bytes(
            root / H3_AUTHORED_PACK_MANIFEST,
            maximum_bytes=MAX_H3_AUTHORED_MANIFEST_BYTES,
            label="H3 authored manifest",
        )
        pack = H3AuthoredMaterialPack.model_validate_json(raw)
        if raw != canonical_h3_authored_pack_bytes(pack):
            raise H3AuthoredMaterialError(
                "H3 authored manifest is not canonical JSON",
            )
        descriptors = _map_descriptors(pack)
        expected_paths = {
            H3_AUTHORED_PACK_MANIFEST,
            "objects",
            *(descriptor.object_path for descriptor in descriptors),
        }
        if _directory_closure(root) != tuple(sorted(expected_paths)):
            raise H3AuthoredMaterialError(
                "H3 authored directory closure disagrees with manifest",
            )
        verified_paths: set[str] = set()
        for descriptor in descriptors:
            if descriptor.object_path in verified_paths:
                continue
            verified_paths.add(descriptor.object_path)
            payload = _read_stable_bytes(
                root / descriptor.object_path,
                maximum_bytes=MAX_H3_AUTHORED_MAP_BYTES,
                label=f"H3 authored object {descriptor.object_path}",
            )
            if hashlib.sha256(payload).hexdigest() != descriptor.sha256:
                raise H3AuthoredMaterialError(
                    f"H3 authored object SHA-256 disagrees: {descriptor.object_path}",
                )
            if len(payload) != descriptor.bytes:
                raise H3AuthoredMaterialError(
                    f"H3 authored object byte count disagrees: {descriptor.object_path}",
                )
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if (
                    image.format != "PNG"
                    or image.mode != "RGB"
                    or image.size != (H3_MASTER_SIZE, H3_MASTER_SIZE)
                    or getattr(image, "text", {})
                ):
                    raise H3AuthoredMaterialError(
                        f"H3 authored object format is invalid: {descriptor.object_path}",
                    )
        return pack
    except H3AuthoredMaterialError:
        raise
    except (
        H3MaterialSourceError,
        OSError,
        UnidentifiedImageError,
        ValidationError,
        ValueError,
    ) as exc:
        raise H3AuthoredMaterialError(
            f"H3 authored material verification failed: {exc}",
        ) from exc


def read_verified_h3_authored_map(
    root: Path,
    *,
    pack: H3AuthoredMaterialPack,
    slot_id: str,
    role: Literal["master", "base_color", "normal", "orm"],
) -> bytes:
    verified = load_h3_authored_material_pack(root)
    if verified != pack:
        raise H3AuthoredMaterialError(
            "provided H3 authored evidence disagrees with disk",
        )
    records = [record for record in verified.records if record.slot_id == slot_id]
    if len(records) != 1:
        raise H3AuthoredMaterialError(
            f"H3 authored slot is absent or ambiguous: {slot_id}",
        )
    descriptor = getattr(records[0], role)
    payload = _read_stable_bytes(
        Path(root).expanduser().absolute() / descriptor.object_path,
        maximum_bytes=MAX_H3_AUTHORED_MAP_BYTES,
        label=f"{slot_id} {role}",
    )
    if (
        hashlib.sha256(payload).hexdigest() != descriptor.sha256
        or len(payload) != descriptor.bytes
    ):
        raise H3AuthoredMaterialError(
            f"{slot_id} {role} changed during verified read",
        )
    return payload


def _current_module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _find_existing_pack(
    output_root: Path,
    *,
    source_pack_id: str,
    module_sha256: str,
) -> PreparedH3AuthoredMaterialPack | None:
    for candidate in sorted(output_root.iterdir(), key=lambda path: path.name):
        if (
            not candidate.is_dir()
            or _is_linklike(candidate)
            or len(candidate.name) != 64
        ):
            continue
        try:
            pack = load_h3_authored_material_pack(candidate)
        except H3AuthoredMaterialError:
            continue
        if (
            pack.source_pack_id == source_pack_id
            and pack.module_sha256 == module_sha256
            and pack.algorithm_id == H3_AUTHORING_ALGORITHM_ID
        ):
            return PreparedH3AuthoredMaterialPack(
                root=candidate,
                manifest=pack,
            )
    return None


def build_h3_authored_material_pack(
    source_pack_root: Path,
    output_root: Path,
) -> PreparedH3AuthoredMaterialPack:
    """Build, verify, and atomically publish one H3 authored material pack."""

    source_root = Path(source_pack_root).expanduser().absolute()
    try:
        source_pack = load_h3_source_pack(source_root)
        contracts = _replacement_contracts()
        if len(contracts) != len(H3_HERO_SLOTS) or set(contracts) != set(
            H3_HERO_SLOTS,
        ):
            raise H3AuthoredMaterialError(
                "tracked replacement contracts do not match H3 hero slots",
            )
        if any(slot_id not in MATERIAL_PARAMETERS for slot_id in H3_HERO_SLOTS):
            raise H3AuthoredMaterialError(
                "H3 hero material parameters are incomplete",
            )
        publication_root = _prepare_real_directory(
            output_root,
            label="H3 authored publication root",
        )
        module_sha256 = _current_module_sha256()
        with ProjectFileLock(
            publication_root / ".h3-authored-pack.lock",
            role="writer",
        ):
            existing = _find_existing_pack(
                publication_root,
                source_pack_id=source_pack.source_pack_id,
                module_sha256=module_sha256,
            )
            if existing is not None:
                return existing
            staging = publication_root / f".build.{uuid.uuid4().hex}.tmp"
            try:
                staging.mkdir(exist_ok=False)
                object_root = staging / "objects"
                object_root.mkdir(exist_ok=False)
                records = tuple(
                    _build_record(
                        source_root=source_root,
                        source_pack=source_pack,
                        slot_id=slot_id,
                        object_root=object_root,
                        contracts=contracts,
                    )
                    for slot_id in H3_HERO_SLOTS
                )
                payload = {
                    "schema_version": H3_AUTHORED_PACK_SCHEMA,
                    "source_pack_id": source_pack.source_pack_id,
                    "synthetic": True,
                    "ai_generated": True,
                    "real_photo_textures": False,
                    "geometry_usability": "preview-only",
                    "metric_alignment": False,
                    "verification_level": "L0",
                    "algorithm_id": H3_AUTHORING_ALGORITHM_ID,
                    "module_sha256": module_sha256,
                    "python_version": platform.python_version(),
                    "pillow_version": PIL.__version__,
                    "numpy_version": np.__version__,
                    "skimage_version": skimage.__version__,
                    "master_size": H3_MASTER_SIZE,
                    "patch_size": H3_PATCH_SIZE,
                    "patch_overlap": H3_PATCH_OVERLAP,
                    "edge_band": H3_EDGE_BAND,
                    "macro_variation_limit": H3_MACRO_VARIATION_LIMIT,
                    "minimum_full_source_ssim": H3_MIN_FULL_SOURCE_SSIM,
                    "minimum_interior_source_ssim": (
                        H3_MIN_INTERIOR_SOURCE_SSIM
                    ),
                    "maximum_mean_rgb_delta": H3_MAX_MEAN_RGB_DELTA,
                    "records": records,
                }
                pack_id = hashlib.sha256(
                    _canonical_json_bytes(payload),
                ).hexdigest()
                pack = H3AuthoredMaterialPack(
                    pack_id=pack_id,
                    **payload,
                )
                manifest_path = staging / H3_AUTHORED_PACK_MANIFEST
                with manifest_path.open("xb") as stream:
                    stream.write(canonical_h3_authored_pack_bytes(pack))
                    stream.flush()
                    os.fsync(stream.fileno())
                for object_path in object_root.iterdir():
                    _flush_file(object_path)
                _flush_directory(object_root)
                _flush_directory(staging)
                if load_h3_authored_material_pack(staging) != pack:
                    raise H3AuthoredMaterialError(
                        "staged H3 authored pack changed during verification",
                    )
                final_root = publication_root / pack.pack_id
                if final_root.exists() or _is_linklike(final_root):
                    verified = load_h3_authored_material_pack(final_root)
                    if verified != pack:
                        raise H3AuthoredMaterialError(
                            "existing H3 authored identity has different evidence",
                        )
                    return PreparedH3AuthoredMaterialPack(
                        root=final_root,
                        manifest=verified,
                    )
                os.rename(staging, final_root)
                _flush_directory(publication_root)
                verified = load_h3_authored_material_pack(final_root)
                if verified != pack:
                    raise H3AuthoredMaterialError(
                        "published H3 authored pack changed after rename",
                    )
                return PreparedH3AuthoredMaterialPack(
                    root=final_root,
                    manifest=verified,
                )
            finally:
                if staging.exists() and not _is_linklike(staging):
                    shutil.rmtree(staging)
    except H3AuthoredMaterialError:
        raise
    except (H3MaterialSourceError, JobContractError) as exc:
        raise H3AuthoredMaterialError(
            f"H3 authored material input is unavailable: {exc}",
        ) from exc
    except (OSError, ValidationError, ValueError) as exc:
        raise H3AuthoredMaterialError(
            f"H3 authored material build failed: {exc}",
        ) from exc
