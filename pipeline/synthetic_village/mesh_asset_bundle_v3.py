"""Immutable dual-profile mesh manifest built from an exact H2 v2 bundle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .glb_ktx2_variant import (
    geometry_fingerprint_glb,
    rewrite_glb_for_ktx2,
)
from .h3_material_sources import H3_HERO_SLOTS
from .material_bundle_v2 import H2_PROFILE_ID, H3_PROFILE_ID, MaterialBundleV2
from .mesh_asset_bundle import GLB_COORDINATE_ENCODING, Bounds3
from .mesh_asset_bundle_v2 import (
    MeshAssetBundleV2,
    MeshTemplateLodV2,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TextureRole = Literal["base_color", "normal", "orm"]
MediaType = Literal["image/png", "image/ktx2"]
Transfer = Literal["srgb", "linear"]

MESH_ASSET_BUNDLE_V3_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v3"
ACCEPTED_H2_MESH_BUNDLE_ID = "866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6"


class MeshAssetBundleV3Error(ValueError):
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
