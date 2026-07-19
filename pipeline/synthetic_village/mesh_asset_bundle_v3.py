"""Immutable dual-profile mesh manifest built from an exact H2 v2 bundle."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Annotated, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from pipeline.studio_jobs import JobContractError, ProjectFileLock

from .glb_ktx2_variant import (
    geometry_fingerprint_glb,
    rewrite_glb_for_ktx2,
)
from .h3_material_sources import H3_HERO_SLOTS
from .ktx2_toolchain import (
    KTX_MAX_BYTES,
    KtxToolchainError,
    audit_ktx2_bytes,
)
from .material_bundle_v2 import H2_PROFILE_ID, H3_PROFILE_ID, MaterialBundleV2
from .mesh_asset_bundle import (
    GLB_COORDINATE_ENCODING,
    MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES,
    MAX_MESH_TEMPLATE_GLB_BYTES,
    MESH_ASSET_BUNDLE_MANIFEST,
    Bounds3,
    MeshAssetBundleError,
    MeshAssetBundleResult,
    _cleanup_mesh_staging,
    _flush_directory,
    _flush_file,
    _is_linklike,
    _move_mesh_directory_noreplace,
    _prepare_real_directory,
    _read_stable_file,
    _real_directory,
    read_verified_mesh_template_glb,
)
from .mesh_asset_bundle_v2 import (
    MAX_MESH_TEXTURE_BYTES,
    MeshAssetBundleV2,
    MeshTemplateLodV2,
    canonical_mesh_asset_bundle_v2_bytes,
    load_mesh_asset_bundle_v2,
    read_verified_mesh_texture,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TextureRole = Literal["base_color", "normal", "orm"]
MediaType = Literal["image/png", "image/ktx2"]
Transfer = Literal["srgb", "linear"]

MESH_ASSET_BUNDLE_V3_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v3"
ACCEPTED_H2_MESH_BUNDLE_ID = "866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6"


class MeshAssetBundleV3Error(MeshAssetBundleError):
    """A dual-profile mesh manifest cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MeshTextureObjectV3(FrozenModel):
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    media_type: MediaType

    @model_validator(mode="after")
    def _content_addressed_texture(self) -> MeshTextureObjectV3:
        suffix = ".ktx2" if self.media_type == "image/ktx2" else ".png"
        expected = f"textures/{self.sha256}{suffix}"
        parsed = PurePosixPath(self.object_path)
        if (
            self.object_path != expected
            or parsed.as_posix() != self.object_path
            or parsed.is_absolute()
        ):
            raise ValueError("mesh v3 texture object path is not content-addressed")
        return self


class MeshTextureBindingV3(FrozenModel):
    uri: str = Field(min_length=1)
    sha256: Sha256
    role: TextureRole
    material_slot_id: str = Field(
        pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    media_type: MediaType
    transfer: Transfer

    @model_validator(mode="after")
    def _portable_role_exact_binding(self) -> MeshTextureBindingV3:
        suffix = ".ktx2" if self.media_type == "image/ktx2" else ".png"
        expected = f"../textures/{self.sha256}{suffix}"
        parsed = PurePosixPath(self.uri)
        if self.uri != expected or parsed.as_posix() != self.uri or parsed.is_absolute():
            raise ValueError("mesh v3 texture binding URI is unsafe")
        expected_transfer = "srgb" if self.role == "base_color" else "linear"
        if self.transfer != expected_transfer:
            raise ValueError("mesh v3 texture binding transfer is role-inexact")
        return self


def _binding_key(
    binding: MeshTextureBindingV3,
) -> tuple[str, str, str, str]:
    return (
        binding.material_slot_id,
        binding.role,
        binding.media_type,
        binding.sha256,
    )


class MeshVariantV3(FrozenModel):
    profile_id: Literal[
        "h3-ai-ktx2-4k",
        "h2-png-1k-fallback",
    ]
    glb_object_path: str = Field(min_length=1)
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1)
    geometry_fingerprint: Sha256
    texture_bindings: tuple[MeshTextureBindingV3, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _variant_closure_is_exact(self) -> MeshVariantV3:
        expected = f"objects/{self.glb_sha256}.glb"
        parsed = PurePosixPath(self.glb_object_path)
        if (
            self.glb_object_path != expected
            or parsed.as_posix() != self.glb_object_path
            or parsed.is_absolute()
        ):
            raise ValueError("mesh v3 GLB path is not content-addressed")
        if tuple(sorted(self.texture_bindings, key=_binding_key)) != self.texture_bindings or len(
            {_binding_key(row) for row in self.texture_bindings}
        ) != len(self.texture_bindings):
            raise ValueError("mesh v3 texture bindings must be sorted and unique")
        png_keys = {
            (row.material_slot_id, row.role)
            for row in self.texture_bindings
            if row.media_type == "image/png"
        }
        ktx_keys = {
            (row.material_slot_id, row.role)
            for row in self.texture_bindings
            if row.media_type == "image/ktx2"
        }
        if self.profile_id == H2_PROFILE_ID:
            if ktx_keys:
                raise ValueError("H2 mesh variant cannot reference KTX2")
        elif not ktx_keys <= png_keys:
            raise ValueError("H3 KTX2 bindings require exact PNG fallbacks")
        return self


class MeshTemplateLod2V3(FrozenModel):
    triangle_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    aabb: Bounds3
    mesh_algorithm_id: Literal["synthetic-template-mesh-near-v2"] = (
        "synthetic-template-mesh-near-v2"
    )
    recipe_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    geometry_fingerprint: Sha256
    variants: dict[str, MeshVariantV3]

    @model_validator(mode="after")
    def _dual_variants_match_geometry(self) -> MeshTemplateLod2V3:
        if set(self.variants) != {H3_PROFILE_ID, H2_PROFILE_ID}:
            raise ValueError("mesh v3 LOD2 requires exact H3 and H2 variants")
        for profile_id, variant in self.variants.items():
            if (
                variant.profile_id != profile_id
                or variant.geometry_fingerprint != self.geometry_fingerprint
            ):
                raise ValueError("mesh v3 variant identity or geometry disagrees")
        if tuple(sorted(self.material_slot_ids)) != self.material_slot_ids or len(
            set(self.material_slot_ids)
        ) != len(self.material_slot_ids):
            raise ValueError("mesh v3 material slots must be sorted and unique")
        for variant in self.variants.values():
            binding_slots = {binding.material_slot_id for binding in variant.texture_bindings}
            if binding_slots != set(self.material_slot_ids):
                raise ValueError("mesh v3 variant material closure is incomplete")
        return self


class MeshAssetRecordV3(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: Literal["building", "vegetation", "prop"]
    footprint_m: tuple[float, float, float]
    lod: dict[
        Literal["0", "1", "2"],
        MeshTemplateLodV2 | MeshTemplateLod2V3,
    ]
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"

    @model_validator(mode="after")
    def _lod_types_are_exact(self) -> MeshAssetRecordV3:
        if set(self.lod) != {"0", "1", "2"}:
            raise ValueError("mesh v3 asset requires LOD 0, 1, and 2")
        if (
            type(self.lod["0"]) is not MeshTemplateLodV2
            or type(self.lod["1"]) is not MeshTemplateLodV2
            or type(self.lod["2"]) is not MeshTemplateLod2V3
        ):
            raise ValueError("mesh v3 asset LOD descriptor types are invalid")
        return self


class MeshAssetBundleV3(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.mesh-asset-bundle.v3"] = (
        MESH_ASSET_BUNDLE_V3_SCHEMA
    )
    bundle_id: Sha256
    coordinate_encoding: Literal["three-east-up-negative-north"] = GLB_COORDINATE_ENCODING
    source_v2_bundle_id: Sha256
    material_bundle_v2_id: Sha256
    fallback_material_bundle_id: Sha256
    synthetic: Literal[True] = True
    ai_generated: Literal[True] = True
    real_photo_textures: Literal[False] = False
    geometry_usability: Literal["preview-only"] = "preview-only"
    metric_alignment: Literal[False] = False
    verification_level: Literal["L0"] = "L0"
    texture_objects: tuple[MeshTextureObjectV3, ...] = Field(min_length=1)
    records: tuple[MeshAssetRecordV3, ...] = Field(min_length=1, max_length=11)

    @model_validator(mode="after")
    def _bundle_closure_and_identity_are_exact(self) -> MeshAssetBundleV3:
        if self.source_v2_bundle_id != ACCEPTED_H2_MESH_BUNDLE_ID:
            raise ValueError("mesh v3 source is not the accepted H2 bundle")
        asset_ids = tuple(record.asset_id for record in self.records)
        if asset_ids != tuple(sorted(asset_ids)) or len(set(asset_ids)) != len(
            asset_ids,
        ):
            raise ValueError("mesh v3 asset IDs must be sorted and unique")
        object_paths = tuple(row.object_path for row in self.texture_objects)
        if object_paths != tuple(sorted(object_paths)) or len(set(object_paths)) != len(
            object_paths
        ):
            raise ValueError("mesh v3 texture objects must be sorted and unique")
        declared = {(row.sha256, row.media_type) for row in self.texture_objects}
        referenced = {
            (binding.sha256, binding.media_type)
            for record in self.records
            for variant in record.lod["2"].variants.values()
            for binding in variant.texture_bindings
        }
        if declared != referenced:
            raise ValueError("mesh v3 texture object closure is incomplete or extra")
        digest = hashlib.sha256(
            canonical_mesh_asset_bundle_v3_bytes(
                self,
                exclude_bundle_id=True,
            ),
        ).hexdigest()
        if digest != self.bundle_id:
            raise ValueError("mesh v3 bundle ID disagrees with canonical bytes")
        return self


@dataclass(frozen=True)
class PreparedMeshAssetBundleV3:
    staging_root: Path
    manifest: MeshAssetBundleV3


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_mesh_asset_bundle_v3_bytes(
    bundle: MeshAssetBundleV3,
    *,
    exclude_bundle_id: bool = False,
) -> bytes:
    payload = bundle.model_dump(mode="json")
    if exclude_bundle_id:
        payload.pop("bundle_id")
    return _canonical_json_bytes(payload)


def _png_binding(binding: object) -> MeshTextureBindingV3:
    return MeshTextureBindingV3(
        uri=binding.uri,
        sha256=binding.sha256,
        role=binding.role,
        material_slot_id=binding.material_slot_id,
        media_type="image/png",
        transfer="srgb" if binding.role == "base_color" else "linear",
    )


def _ktx_binding(
    *,
    slot_id: str,
    descriptor: object,
) -> MeshTextureBindingV3:
    return MeshTextureBindingV3(
        uri=f"../textures/{descriptor.sha256}.ktx2",
        sha256=descriptor.sha256,
        role=descriptor.role,
        material_slot_id=slot_id,
        media_type="image/ktx2",
        transfer=descriptor.transfer,
    )


def compose_mesh_asset_bundle_v3(
    h2_bundle: MeshAssetBundleV2,
    fallback_glbs: Mapping[str, bytes],
    material_bundle_v2: MaterialBundleV2,
) -> MeshAssetBundleV3:
    """Bind exact H2 meshes to profile-aware H3/H2 LOD2 variants."""

    if h2_bundle.bundle_id != ACCEPTED_H2_MESH_BUNDLE_ID:
        raise MeshAssetBundleV3Error(
            "mesh v3 requires the accepted H2 mesh bundle",
        )
    material_truth = (
        getattr(material_bundle_v2, "synthetic", None),
        getattr(material_bundle_v2, "ai_generated", None),
        getattr(material_bundle_v2, "real_photo_textures", None),
        getattr(material_bundle_v2, "geometry_usability", None),
        getattr(material_bundle_v2, "metric_alignment", None),
        getattr(material_bundle_v2, "verification_level", None),
    )
    if material_truth != (True, True, False, "preview-only", False, "L0"):
        raise MeshAssetBundleV3Error(
            "mesh v3 material provenance must remain synthetic preview-only L0",
        )
    if material_bundle_v2.fallback_bundle_id != h2_bundle.material_bundle_id:
        raise MeshAssetBundleV3Error(
            "mesh v3 material fallback disagrees with the H2 mesh bundle",
        )
    if set(material_bundle_v2.profiles) != {
        H3_PROFILE_ID,
        H2_PROFILE_ID,
    }:
        raise MeshAssetBundleV3Error(
            "mesh v3 material profiles are incomplete",
        )

    h3_descriptors = {
        (row.slot_id, row.role): row
        for row in material_bundle_v2.profiles[H3_PROFILE_ID].textures
        if row.media_type == "image/ktx2"
    }
    expected_h3 = {
        (slot_id, role) for slot_id in H3_HERO_SLOTS for role in ("base_color", "normal", "orm")
    }
    if set(h3_descriptors) != expected_h3:
        raise MeshAssetBundleV3Error(
            "mesh v3 requires the complete eight-slot H3 KTX2 closure",
        )

    asset_ids = tuple(record.asset_id for record in h2_bundle.records)
    if set(fallback_glbs) != set(asset_ids):
        raise MeshAssetBundleV3Error(
            "mesh v3 fallback GLB closure disagrees with H2 assets",
        )

    used_ktx: dict[str, object] = {}
    records = []
    for source_record in h2_bundle.records:
        source_near = source_record.lod["2"]
        fallback = fallback_glbs[source_record.asset_id]
        if (
            type(fallback) is not bytes
            or len(fallback) != source_near.glb_bytes
            or hashlib.sha256(fallback).hexdigest() != source_near.glb_sha256
        ):
            raise MeshAssetBundleV3Error(
                "mesh v3 fallback GLB bytes or SHA disagree with H2",
            )
        replacements = {}
        png_bindings = tuple(_png_binding(binding) for binding in source_near.texture_bindings)
        ktx_bindings = []
        for source_binding in source_near.texture_bindings:
            key = (
                source_binding.material_slot_id,
                source_binding.role,
            )
            descriptor = h3_descriptors.get(key)
            if descriptor is None:
                continue
            replacements[source_binding.uri] = descriptor
            used_ktx[descriptor.sha256] = descriptor
            ktx_bindings.append(
                _ktx_binding(
                    slot_id=source_binding.material_slot_id,
                    descriptor=descriptor,
                ),
            )
        primary = rewrite_glb_for_ktx2(fallback, replacements) if replacements else fallback
        fingerprint = geometry_fingerprint_glb(fallback)
        if geometry_fingerprint_glb(primary) != fingerprint:
            raise MeshAssetBundleV3Error(
                "mesh v3 H3 and H2 geometry fingerprints disagree",
            )

        fallback_variant = MeshVariantV3(
            profile_id=H2_PROFILE_ID,
            glb_object_path=source_near.glb_object_path,
            glb_sha256=source_near.glb_sha256,
            glb_bytes=source_near.glb_bytes,
            geometry_fingerprint=fingerprint,
            texture_bindings=tuple(
                sorted(png_bindings, key=_binding_key),
            ),
        )
        primary_sha = hashlib.sha256(primary).hexdigest()
        primary_variant = MeshVariantV3(
            profile_id=H3_PROFILE_ID,
            glb_object_path=f"objects/{primary_sha}.glb",
            glb_sha256=primary_sha,
            glb_bytes=len(primary),
            geometry_fingerprint=fingerprint,
            texture_bindings=tuple(
                sorted(
                    (*png_bindings, *ktx_bindings),
                    key=_binding_key,
                ),
            ),
        )
        lod2 = MeshTemplateLod2V3(
            triangle_count=source_near.triangle_count,
            primitive_count=source_near.primitive_count,
            material_slot_ids=source_near.material_slot_ids,
            aabb=source_near.aabb,
            recipe_id=source_near.recipe_id,
            geometry_fingerprint=fingerprint,
            variants={
                H3_PROFILE_ID: primary_variant,
                H2_PROFILE_ID: fallback_variant,
            },
        )
        record = MeshAssetRecordV3(
            asset_id=source_record.asset_id,
            kind=source_record.kind,
            footprint_m=source_record.footprint_m,
            lod={
                "0": source_record.lod["0"],
                "1": source_record.lod["1"],
                "2": lod2,
            },
        )
        if record.lod["0"] != source_record.lod["0"] or record.lod["1"] != source_record.lod["1"]:
            raise MeshAssetBundleV3Error(
                "mesh v3 changed exact H2 LOD0/1 descriptors",
            )
        records.append(record)

    texture_objects = [
        MeshTextureObjectV3(
            object_path=row.object_path,
            sha256=row.sha256,
            bytes=row.bytes,
            width=row.width,
            height=row.height,
            media_type="image/png",
        )
        for row in h2_bundle.texture_objects
    ]
    texture_objects.extend(
        MeshTextureObjectV3(
            object_path=f"textures/{descriptor.sha256}.ktx2",
            sha256=descriptor.sha256,
            bytes=descriptor.bytes,
            width=descriptor.width,
            height=descriptor.height,
            media_type="image/ktx2",
        )
        for descriptor in used_ktx.values()
    )
    texture_objects = sorted(
        texture_objects,
        key=lambda row: row.object_path,
    )
    payload = {
        "schema_version": MESH_ASSET_BUNDLE_V3_SCHEMA,
        "coordinate_encoding": GLB_COORDINATE_ENCODING,
        "source_v2_bundle_id": h2_bundle.bundle_id,
        "material_bundle_v2_id": material_bundle_v2.bundle_id,
        "fallback_material_bundle_id": h2_bundle.material_bundle_id,
        "synthetic": True,
        "ai_generated": True,
        "real_photo_textures": False,
        "geometry_usability": "preview-only",
        "metric_alignment": False,
        "verification_level": "L0",
        "texture_objects": [row.model_dump(mode="json") for row in texture_objects],
        "records": [row.model_dump(mode="json") for row in records],
    }
    bundle_id = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return MeshAssetBundleV3(
        bundle_id=bundle_id,
        source_v2_bundle_id=h2_bundle.bundle_id,
        material_bundle_v2_id=material_bundle_v2.bundle_id,
        fallback_material_bundle_id=h2_bundle.material_bundle_id,
        texture_objects=tuple(texture_objects),
        records=tuple(records),
    )


def _real_file(
    root: Path,
    relative: str,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    bundle_root = _real_directory(Path(root))
    candidate = bundle_root / relative
    if _is_linklike(candidate):
        raise MeshAssetBundleV3Error(f"{label} is redirected")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(bundle_root)
    except (OSError, ValueError) as exc:
        raise MeshAssetBundleV3Error(f"{label} is unavailable or escapes") from exc
    if resolved != candidate or not candidate.is_file():
        raise MeshAssetBundleV3Error(f"{label} is redirected or not a file")
    try:
        return _read_stable_file(
            candidate,
            maximum_bytes=maximum_bytes,
            label=label,
        )
    except (MeshAssetBundleError, OSError) as exc:
        raise MeshAssetBundleV3Error(f"{label} cannot be read stably") from exc


def _write_exact_file(root: Path, relative: str, payload: bytes) -> None:
    path = root / relative
    if path.exists() or _is_linklike(path):
        if (
            not path.is_file()
            or _read_stable_file(
                path,
                maximum_bytes=max(
                    MAX_MESH_TEMPLATE_GLB_BYTES,
                    KTX_MAX_BYTES,
                ),
                label="mesh v3 staging object",
            )
            != payload
        ):
            raise MeshAssetBundleV3Error(
                "mesh v3 staging object conflicts with its content address",
            )
        return
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except OSError as exc:
        raise MeshAssetBundleV3Error(
            "mesh v3 staging object cannot be written",
        ) from exc


def _h3_descriptor_map(
    material_bundle_v2: MaterialBundleV2,
) -> dict[tuple[str, str], object]:
    return {
        (row.slot_id, row.role): row
        for row in material_bundle_v2.profiles[H3_PROFILE_ID].textures
        if row.media_type == "image/ktx2"
    }


def _replacement_mapping(
    texture_bindings: tuple[object, ...],
    h3_descriptors: Mapping[tuple[str, str], object],
) -> dict[str, object]:
    return {
        binding.uri: h3_descriptors[(binding.material_slot_id, binding.role)]
        for binding in texture_bindings
        if (binding.material_slot_id, binding.role) in h3_descriptors
    }


def _audit_ktx_payload(
    payload: bytes,
    *,
    roles: set[str],
    transfers: set[str],
) -> None:
    if len(transfers) != 1 or not roles:
        raise MeshAssetBundleV3Error(
            "mesh v3 KTX2 binding semantics conflict",
        )
    transfer = next(iter(transfers))
    codecs = ("uastc",) if roles != {"orm"} else ("uastc", "etc1s")
    failures = []
    for codec in codecs:
        try:
            audit_ktx2_bytes(
                payload,
                expected_transfer=transfer,
                expected_codec=codec,
            )
        except KtxToolchainError as exc:
            failures.append(exc)
        else:
            return
    raise MeshAssetBundleV3Error(
        "mesh v3 KTX2 bytes fail structural audit",
    ) from failures[-1]


def prepare_mesh_asset_bundle_v3(
    *,
    source_v2_bundle_root: Path,
    material_bundle_v2: MaterialBundleV2,
    ktx2_root: Path,
    staging_root: Path,
) -> PreparedMeshAssetBundleV3:
    """Prepare a complete v3 directory without publishing it."""

    staging = Path(staging_root).expanduser().absolute()
    if staging.exists() or _is_linklike(staging):
        raise MeshAssetBundleV3Error("mesh v3 staging root must start absent")
    _real_directory(staging.parent)
    try:
        source = load_mesh_asset_bundle_v2(source_v2_bundle_root)
        fallback_glbs = {
            record.asset_id: read_verified_mesh_template_glb(
                source_v2_bundle_root,
                bundle=source,
                asset_id=record.asset_id,
                lod=2,
            )
            for record in source.records
        }
        manifest = compose_mesh_asset_bundle_v3(
            source,
            fallback_glbs,
            material_bundle_v2,
        )
        h3_descriptors = _h3_descriptor_map(material_bundle_v2)

        staging.mkdir(exist_ok=False)
        (staging / "objects").mkdir()
        (staging / "textures").mkdir()
        for record in source.records:
            for level in (0, 1):
                descriptor = record.lod[str(level)]
                payload = read_verified_mesh_template_glb(
                    source_v2_bundle_root,
                    bundle=source,
                    asset_id=record.asset_id,
                    lod=level,
                )
                _write_exact_file(
                    staging,
                    descriptor.glb_object_path,
                    payload,
                )
            fallback = fallback_glbs[record.asset_id]
            _write_exact_file(
                staging,
                record.lod["2"].glb_object_path,
                fallback,
            )
            replacements = _replacement_mapping(
                record.lod["2"].texture_bindings,
                h3_descriptors,
            )
            primary = rewrite_glb_for_ktx2(fallback, replacements) if replacements else fallback
            primary_descriptor = (
                next(row for row in manifest.records if row.asset_id == record.asset_id)
                .lod["2"]
                .variants[H3_PROFILE_ID]
            )
            if (
                len(primary) != primary_descriptor.glb_bytes
                or hashlib.sha256(primary).hexdigest() != primary_descriptor.glb_sha256
            ):
                raise MeshAssetBundleV3Error(
                    "mesh v3 primary GLB changed during preparation",
                )
            _write_exact_file(
                staging,
                primary_descriptor.glb_object_path,
                primary,
            )

        for descriptor in source.texture_objects:
            payload = read_verified_mesh_texture(
                source_v2_bundle_root,
                bundle=source,
                sha256=descriptor.sha256,
            )
            _write_exact_file(staging, descriptor.object_path, payload)

        used_ktx = {
            row.sha256 for row in manifest.texture_objects if row.media_type == "image/ktx2"
        }
        descriptors_by_sha = {row.sha256: row for row in h3_descriptors.values()}
        for sha256 in sorted(used_ktx):
            descriptor = descriptors_by_sha[sha256]
            payload = _real_file(
                ktx2_root,
                descriptor.object_path,
                maximum_bytes=KTX_MAX_BYTES,
                label="mesh v3 source KTX2",
            )
            if (
                len(payload) != descriptor.bytes
                or hashlib.sha256(payload).hexdigest() != descriptor.sha256
            ):
                raise MeshAssetBundleV3Error(
                    "mesh v3 source KTX2 bytes disagree",
                )
            roles = {row.role for row in h3_descriptors.values() if row.sha256 == sha256}
            transfers = {row.transfer for row in h3_descriptors.values() if row.sha256 == sha256}
            _audit_ktx_payload(
                payload,
                roles=roles,
                transfers=transfers,
            )
            _write_exact_file(
                staging,
                f"textures/{sha256}.ktx2",
                payload,
            )

        (staging / MESH_ASSET_BUNDLE_MANIFEST).write_bytes(
            canonical_mesh_asset_bundle_v3_bytes(manifest),
        )
        if load_mesh_asset_bundle_v3(staging) != manifest:
            raise MeshAssetBundleV3Error(
                "prepared mesh v3 bundle changed during verification",
            )
        return PreparedMeshAssetBundleV3(
            staging_root=staging,
            manifest=manifest,
        )
    except MeshAssetBundleV3Error:
        if staging.is_symlink():
            staging.unlink(missing_ok=True)
        elif staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    except (
        KeyError,
        OSError,
        StopIteration,
        TypeError,
        ValidationError,
        ValueError,
    ) as exc:
        if staging.is_symlink():
            staging.unlink(missing_ok=True)
        elif staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise MeshAssetBundleV3Error(
            f"mesh v3 preparation failed: {exc}",
        ) from exc


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
        raise MeshAssetBundleV3Error(
            "mesh v3 object directory is unavailable",
        ) from exc
    if any(_is_linklike(path) for path in entries):
        raise MeshAssetBundleV3Error(
            "mesh v3 object closure contains a redirect",
        )
    actual = {path.relative_to(root).as_posix() for path in entries if path.is_file()}
    if actual != expected or len(entries) != len(actual):
        raise MeshAssetBundleV3Error(
            "mesh v3 object closure is incomplete or unexpected",
        )


def _variant_replacements(
    fallback: MeshVariantV3,
    primary: MeshVariantV3,
) -> dict[str, object]:
    fallback_png = {(row.material_slot_id, row.role): row for row in fallback.texture_bindings}
    primary_png = {
        (row.material_slot_id, row.role): row
        for row in primary.texture_bindings
        if row.media_type == "image/png"
    }
    if primary_png != fallback_png:
        raise MeshAssetBundleV3Error(
            "mesh v3 primary PNG fallback closure changed",
        )
    replacements = {}
    for binding in primary.texture_bindings:
        if binding.media_type != "image/ktx2":
            continue
        key = (binding.material_slot_id, binding.role)
        fallback_binding = fallback_png.get(key)
        if fallback_binding is None:
            raise MeshAssetBundleV3Error(
                "mesh v3 KTX2 binding has no PNG fallback",
            )
        replacements[fallback_binding.uri] = SimpleNamespace(
            role=binding.role,
            object_path=f"objects/{binding.sha256}.ktx2",
            sha256=binding.sha256,
            media_type="image/ktx2",
            transfer=binding.transfer,
        )
    return replacements


def load_mesh_asset_bundle_v3(root: Path) -> MeshAssetBundleV3:
    """Independently verify one complete immutable v3 directory."""

    bundle_root = _real_directory(Path(root))
    raw = _real_file(
        bundle_root,
        MESH_ASSET_BUNDLE_MANIFEST,
        maximum_bytes=MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES,
        label="mesh v3 manifest",
    )
    try:
        bundle = MeshAssetBundleV3.model_validate_json(raw)
    except ValidationError as exc:
        raise MeshAssetBundleV3Error("mesh v3 manifest is invalid") from exc
    if raw != canonical_mesh_asset_bundle_v3_bytes(bundle):
        raise MeshAssetBundleV3Error("mesh v3 manifest is not canonical")

    expected_glbs = {
        descriptor.glb_object_path
        for record in bundle.records
        for descriptor in (
            record.lod["0"],
            record.lod["1"],
            *record.lod["2"].variants.values(),
        )
    }
    expected_textures = {descriptor.object_path for descriptor in bundle.texture_objects}
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

    glb_payloads = {}
    for relative in expected_glbs:
        payload = _real_file(
            bundle_root,
            relative,
            maximum_bytes=MAX_MESH_TEMPLATE_GLB_BYTES,
            label="mesh v3 GLB object",
        )
        glb_payloads[relative] = payload

    for record in bundle.records:
        for level in ("0", "1"):
            descriptor = record.lod[level]
            payload = glb_payloads[descriptor.glb_object_path]
            if (
                len(payload) != descriptor.glb_bytes
                or hashlib.sha256(payload).hexdigest() != descriptor.glb_sha256
            ):
                raise MeshAssetBundleV3Error(
                    "mesh v3 reused GLB bytes disagree",
                )
        near = record.lod["2"]
        fallback = near.variants[H2_PROFILE_ID]
        primary = near.variants[H3_PROFILE_ID]
        fallback_payload = glb_payloads[fallback.glb_object_path]
        primary_payload = glb_payloads[primary.glb_object_path]
        for descriptor, payload in (
            (fallback, fallback_payload),
            (primary, primary_payload),
        ):
            if (
                len(payload) != descriptor.glb_bytes
                or hashlib.sha256(payload).hexdigest() != descriptor.glb_sha256
                or geometry_fingerprint_glb(payload) != descriptor.geometry_fingerprint
            ):
                raise MeshAssetBundleV3Error(
                    "mesh v3 GLB bytes or geometry disagree",
                )
        replacements = _variant_replacements(fallback, primary)
        regenerated = (
            rewrite_glb_for_ktx2(fallback_payload, replacements)
            if replacements
            else fallback_payload
        )
        if regenerated != primary_payload:
            raise MeshAssetBundleV3Error(
                "mesh v3 primary GLB is not the exact KTX2 rewrite",
            )

    texture_payloads = {}
    for descriptor in bundle.texture_objects:
        maximum = KTX_MAX_BYTES if descriptor.media_type == "image/ktx2" else MAX_MESH_TEXTURE_BYTES
        payload = _real_file(
            bundle_root,
            descriptor.object_path,
            maximum_bytes=maximum,
            label="mesh v3 texture object",
        )
        if (
            len(payload) != descriptor.bytes
            or hashlib.sha256(payload).hexdigest() != descriptor.sha256
        ):
            raise MeshAssetBundleV3Error(
                "mesh v3 texture bytes disagree",
            )
        texture_payloads[(descriptor.sha256, descriptor.media_type)] = payload
        if descriptor.media_type == "image/png":
            try:
                with Image.open(io.BytesIO(payload)) as image:
                    image.verify()
                with Image.open(io.BytesIO(payload)) as image:
                    if image.format != "PNG" or image.size != (
                        descriptor.width,
                        descriptor.height,
                    ):
                        raise MeshAssetBundleV3Error(
                            "mesh v3 PNG dimensions disagree",
                        )
            except MeshAssetBundleV3Error:
                raise
            except (OSError, UnidentifiedImageError) as exc:
                raise MeshAssetBundleV3Error(
                    "mesh v3 texture is not a valid PNG",
                ) from exc

    ktx_semantics: dict[str, tuple[set[str], set[str]]] = {}
    for record in bundle.records:
        primary = record.lod["2"].variants[H3_PROFILE_ID]
        for binding in primary.texture_bindings:
            if binding.media_type != "image/ktx2":
                continue
            roles, transfers = ktx_semantics.setdefault(
                binding.sha256,
                (set(), set()),
            )
            roles.add(binding.role)
            transfers.add(binding.transfer)
    for sha256, (roles, transfers) in ktx_semantics.items():
        _audit_ktx_payload(
            texture_payloads[(sha256, "image/ktx2")],
            roles=roles,
            transfers=transfers,
        )
    return bundle


def read_verified_mesh_variant_glb(
    root: Path,
    *,
    bundle: MeshAssetBundleV3,
    asset_id: str,
    profile_id: Literal[
        "h3-ai-ktx2-4k",
        "h2-png-1k-fallback",
    ],
) -> bytes:
    """Read one profile GLB only while the complete bundle still verifies."""

    if profile_id not in {H3_PROFILE_ID, H2_PROFILE_ID}:
        raise MeshAssetBundleV3Error("mesh v3 profile is unsupported")
    current = load_mesh_asset_bundle_v3(root)
    if current != bundle:
        raise MeshAssetBundleV3Error(
            "mesh v3 bundle changed after selection",
        )
    record = next(
        (row for row in current.records if row.asset_id == asset_id),
        None,
    )
    if record is None:
        raise MeshAssetBundleV3Error("mesh v3 asset is absent")
    descriptor = record.lod["2"].variants[profile_id]
    return _real_file(
        Path(root),
        descriptor.glb_object_path,
        maximum_bytes=MAX_MESH_TEMPLATE_GLB_BYTES,
        label="mesh v3 selected GLB",
    )


def read_verified_mesh_texture_v3(
    root: Path,
    *,
    bundle: MeshAssetBundleV3,
    sha256: str,
    media_type: MediaType,
) -> bytes:
    """Read one texture only while the complete bundle still verifies."""

    current = load_mesh_asset_bundle_v3(root)
    if current != bundle:
        raise MeshAssetBundleV3Error(
            "mesh v3 bundle changed after selection",
        )
    descriptor = next(
        (
            row
            for row in current.texture_objects
            if row.sha256 == sha256 and row.media_type == media_type
        ),
        None,
    )
    if descriptor is None:
        raise MeshAssetBundleV3Error("mesh v3 texture is absent")
    return _real_file(
        Path(root),
        descriptor.object_path,
        maximum_bytes=(KTX_MAX_BYTES if media_type == "image/ktx2" else MAX_MESH_TEXTURE_BYTES),
        label="mesh v3 selected texture",
    )


def _durably_flush_mesh_bundle_v3(staging: Path) -> None:
    manifest = load_mesh_asset_bundle_v3(staging)
    object_paths = {
        descriptor.glb_object_path
        for record in manifest.records
        for descriptor in (
            record.lod["0"],
            record.lod["1"],
            *record.lod["2"].variants.values(),
        )
    }
    for relative in sorted(object_paths):
        _flush_file(staging / relative)
    for descriptor in manifest.texture_objects:
        _flush_file(staging / descriptor.object_path)
    _flush_file(staging / MESH_ASSET_BUNDLE_MANIFEST)
    _flush_directory(staging / "objects")
    _flush_directory(staging / "textures")
    _flush_directory(staging)
    if load_mesh_asset_bundle_v3(staging) != manifest:
        raise MeshAssetBundleV3Error(
            "mesh v3 changed during durability flush",
        )


def publish_mesh_asset_bundle_v3(
    *,
    source_v2_bundle_root: Path,
    material_bundle_v2: MaterialBundleV2,
    ktx2_root: Path,
    publication_root: Path,
    work_root: Path,
) -> MeshAssetBundleResult:
    """Prepare and atomically publish an immutable v3 bundle only if absent."""

    staging: Path | None = None
    try:
        source_root = _real_directory(Path(source_v2_bundle_root))
        ktx_root = _real_directory(Path(ktx2_root))
        publication = _prepare_real_directory(
            Path(publication_root),
            label="mesh v3 publication root",
        )
        work = _prepare_real_directory(
            Path(work_root),
            label="mesh v3 work root",
        )
        with ProjectFileLock(
            work / ".mesh-asset-bundle-v3.lock",
            role="writer",
        ):
            source_before = load_mesh_asset_bundle_v2(source_root)
            source_bytes = canonical_mesh_asset_bundle_v2_bytes(
                source_before,
            )
            staging = work / f".mesh-v3-{uuid.uuid4().hex}"
            prepared = prepare_mesh_asset_bundle_v3(
                source_v2_bundle_root=source_root,
                material_bundle_v2=material_bundle_v2,
                ktx2_root=ktx_root,
                staging_root=staging,
            )
            source_after = load_mesh_asset_bundle_v2(source_root)
            if (
                canonical_mesh_asset_bundle_v2_bytes(source_after) != source_bytes
                or prepared.manifest.source_v2_bundle_id != source_before.bundle_id
            ):
                raise MeshAssetBundleV3Error(
                    "mesh v3 source changed during publication",
                )

            destination = publication / prepared.manifest.bundle_id
            if destination.exists() or _is_linklike(destination):
                existing = load_mesh_asset_bundle_v3(destination)
                if existing != prepared.manifest:
                    raise MeshAssetBundleV3Error(
                        "existing mesh v3 conflicts with content identity",
                    )
                _cleanup_mesh_staging(staging, work_root=work)
                staging = None
                return MeshAssetBundleResult(
                    bundle_id=existing.bundle_id,
                    final_directory=destination,
                    record_count=len(existing.records),
                    reused=True,
                )

            _durably_flush_mesh_bundle_v3(staging)
            _move_mesh_directory_noreplace(staging, destination)
            staging = None
            published = load_mesh_asset_bundle_v3(destination)
            if published != prepared.manifest:
                raise MeshAssetBundleV3Error(
                    "published mesh v3 changed during atomic move",
                )
            return MeshAssetBundleResult(
                bundle_id=published.bundle_id,
                final_directory=destination,
                record_count=len(published.records),
                reused=False,
            )
    except MeshAssetBundleV3Error:
        raise
    except (
        JobContractError,
        MeshAssetBundleError,
        OSError,
        ValidationError,
        ValueError,
    ) as exc:
        raise MeshAssetBundleV3Error(
            f"mesh v3 publication failed: {exc}",
        ) from exc
    finally:
        if staging is not None:
            try:
                _cleanup_mesh_staging(staging, work_root=staging.parent)
            except (MeshAssetBundleError, OSError):
                pass
