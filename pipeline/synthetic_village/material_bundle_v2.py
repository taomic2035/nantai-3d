"""Immutable H3 KTX2 material profile with an exact H2 PNG fallback.

This module only binds already-verified evidence.  It does not infer texture
quality or promote the synthetic H3 sources beyond preview-only/L0.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .h3_material_sources import H3_HERO_SLOTS
from .ktx2_toolchain import H3Ktx2Pack, KtxTextureDescriptor
from .material_bundle import (
    MATERIAL_PARAMETERS,
    DerivedMaterialBundle,
    MaterialMapDescriptor,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TextureRole = Literal["base_color", "normal", "orm"]
MediaType = Literal["image/png", "image/ktx2"]
Transfer = Literal["srgb", "linear"]

MATERIAL_BUNDLE_V2_SCHEMA = "nantai.synthetic-village.derived-material-bundle.v2"
H3_PROFILE_ID = "h3-ai-ktx2-4k"
H2_PROFILE_ID = "h2-png-1k-fallback"
ACCEPTED_H2_MATERIAL_BUNDLE_ID = "b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13"


class MaterialBundleV2Error(ValueError):
    """The dual-profile material closure cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProfileTextureDescriptor(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    role: TextureRole
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    media_type: MediaType
    transfer: Transfer

    @model_validator(mode="after")
    def _role_and_object_are_exact(self) -> ProfileTextureDescriptor:
        suffix = ".ktx2" if self.media_type == "image/ktx2" else ".png"
        expected = f"objects/{self.sha256}{suffix}"
        parsed = PurePosixPath(self.object_path)
        if (
            self.object_path != expected
            or parsed.as_posix() != self.object_path
            or parsed.is_absolute()
        ):
            raise ValueError(
                "profile texture path must match its content address and media type",
            )
        expected_transfer = "srgb" if self.role == "base_color" else "linear"
        if self.transfer != expected_transfer:
            raise ValueError("profile texture transfer disagrees with its role")
        return self


class MaterialProfile(FrozenModel):
    profile_id: Literal[
        "h3-ai-ktx2-4k",
        "h2-png-1k-fallback",
    ]
    replacement_slots: tuple[str, ...]
    textures: tuple[ProfileTextureDescriptor, ...] = Field(
        min_length=72,
        max_length=72,
    )

    @model_validator(mode="after")
    def _profile_is_complete(self) -> MaterialProfile:
        expected_keys = tuple(
            (slot_id, role)
            for slot_id in sorted(MATERIAL_PARAMETERS)
            for role in ("base_color", "normal", "orm")
        )
        actual_keys = tuple((texture.slot_id, texture.role) for texture in self.textures)
        if actual_keys != expected_keys:
            raise ValueError(
                "material profile must contain the exact ordered 24-slot closure",
            )

        if self.profile_id == H2_PROFILE_ID:
            if self.replacement_slots or any(
                texture.media_type != "image/png" for texture in self.textures
            ):
                raise ValueError("H2 profile must be the complete PNG fallback")
            return self

        if self.replacement_slots != H3_HERO_SLOTS:
            raise ValueError("H3 profile must replace the exact eight hero slots")
        for texture in self.textures:
            expected_media = "image/ktx2" if texture.slot_id in H3_HERO_SLOTS else "image/png"
            if texture.media_type != expected_media:
                raise ValueError(
                    "H3 profile must use KTX2 only for the eight hero slots",
                )
        return self


class MaterialBundleV2(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.derived-material-bundle.v2"] = (
        MATERIAL_BUNDLE_V2_SCHEMA
    )
    bundle_id: Sha256
    synthetic: Literal[True] = True
    ai_generated: Literal[True] = True
    real_photo_textures: Literal[False] = False
    geometry_usability: Literal["preview-only"] = "preview-only"
    metric_alignment: Literal[False] = False
    verification_level: Literal["L0"] = "L0"
    source_pack_id: Sha256
    authored_pack_id: Sha256
    ktx2_pack_id: Sha256
    fallback_bundle_id: Sha256
    profiles: dict[str, MaterialProfile]

    @model_validator(mode="after")
    def _identity_and_profiles_are_exact(self) -> MaterialBundleV2:
        if self.fallback_bundle_id != ACCEPTED_H2_MATERIAL_BUNDLE_ID:
            raise ValueError("fallback bundle is not the accepted H2 bundle")
        if set(self.profiles) != {H3_PROFILE_ID, H2_PROFILE_ID}:
            raise ValueError("material bundle must contain exact H3 and H2 profiles")
        if self.profiles[H3_PROFILE_ID].profile_id != H3_PROFILE_ID:
            raise ValueError("H3 profile key and identity disagree")
        if self.profiles[H2_PROFILE_ID].profile_id != H2_PROFILE_ID:
            raise ValueError("H2 profile key and identity disagree")
        digest = hashlib.sha256(
            canonical_material_bundle_v2_bytes(
                self,
                exclude_bundle_id=True,
            ),
        ).hexdigest()
        if digest != self.bundle_id:
            raise ValueError("material bundle v2 ID disagrees with canonical bytes")
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


def canonical_material_bundle_v2_bytes(
    bundle: MaterialBundleV2,
    *,
    exclude_bundle_id: bool = False,
) -> bytes:
    payload = bundle.model_dump(mode="json")
    if exclude_bundle_id:
        payload.pop("bundle_id")
    return _canonical_json_bytes(payload)


def _h2_texture(
    *,
    slot_id: str,
    role: TextureRole,
    descriptor: MaterialMapDescriptor,
) -> ProfileTextureDescriptor:
    return ProfileTextureDescriptor(
        slot_id=slot_id,
        role=role,
        object_path=descriptor.object_path,
        sha256=descriptor.sha256,
        bytes=descriptor.bytes,
        width=descriptor.width,
        height=descriptor.height,
        media_type="image/png",
        transfer="srgb" if role == "base_color" else "linear",
    )


def _h3_texture(
    *,
    slot_id: str,
    role: TextureRole,
    descriptor: KtxTextureDescriptor,
) -> ProfileTextureDescriptor:
    return ProfileTextureDescriptor(
        slot_id=slot_id,
        role=role,
        object_path=descriptor.object_path,
        sha256=descriptor.sha256,
        bytes=descriptor.bytes,
        width=descriptor.width,
        height=descriptor.height,
        media_type=descriptor.media_type,
        transfer=descriptor.transfer,
    )


def compose_material_bundle_v2(
    h2_bundle: DerivedMaterialBundle,
    h3_pack: H3Ktx2Pack,
) -> MaterialBundleV2:
    """Bind verified H3 KTX2 records and the accepted H2 PNG closure."""

    if h2_bundle.bundle_id != ACCEPTED_H2_MATERIAL_BUNDLE_ID:
        raise MaterialBundleV2Error(
            "material bundle v2 requires the accepted H2 fallback bundle",
        )
    h3_truth = (
        getattr(h3_pack, "synthetic", None),
        getattr(h3_pack, "ai_generated", None),
        getattr(h3_pack, "real_photo_textures", None),
        getattr(h3_pack, "geometry_usability", None),
        getattr(h3_pack, "metric_alignment", None),
        getattr(h3_pack, "verification_level", None),
    )
    if h3_truth != (True, True, False, "preview-only", False, "L0"):
        raise MaterialBundleV2Error(
            "H3 pack provenance must remain synthetic preview-only L0",
        )
    h3_records = tuple(getattr(h3_pack, "records", ()))
    if tuple(record.slot_id for record in h3_records) != H3_HERO_SLOTS:
        raise MaterialBundleV2Error(
            "H3 pack must contain the exact ordered eight hero slots",
        )

    h2_by_slot = {record.slot_id: record for record in h2_bundle.records}
    if tuple(sorted(h2_by_slot)) != tuple(sorted(MATERIAL_PARAMETERS)):
        raise MaterialBundleV2Error(
            "H2 bundle must contain the exact 24 material slots",
        )
    h3_by_slot = {record.slot_id: record for record in h3_records}
    roles: tuple[TextureRole, ...] = ("base_color", "normal", "orm")

    h2_textures = tuple(
        _h2_texture(
            slot_id=slot_id,
            role=role,
            descriptor=getattr(h2_by_slot[slot_id], role),
        )
        for slot_id in sorted(MATERIAL_PARAMETERS)
        for role in roles
    )
    h3_textures = tuple(
        (
            _h3_texture(
                slot_id=slot_id,
                role=role,
                descriptor=getattr(h3_by_slot[slot_id], role),
            )
            if slot_id in h3_by_slot
            else _h2_texture(
                slot_id=slot_id,
                role=role,
                descriptor=getattr(h2_by_slot[slot_id], role),
            )
        )
        for slot_id in sorted(MATERIAL_PARAMETERS)
        for role in roles
    )

    profiles = {
        H3_PROFILE_ID: MaterialProfile(
            profile_id=H3_PROFILE_ID,
            replacement_slots=H3_HERO_SLOTS,
            textures=h3_textures,
        ),
        H2_PROFILE_ID: MaterialProfile(
            profile_id=H2_PROFILE_ID,
            replacement_slots=(),
            textures=h2_textures,
        ),
    }
    payload = {
        "schema_version": MATERIAL_BUNDLE_V2_SCHEMA,
        "synthetic": True,
        "ai_generated": True,
        "real_photo_textures": False,
        "geometry_usability": "preview-only",
        "metric_alignment": False,
        "verification_level": "L0",
        "source_pack_id": h3_pack.source_pack_id,
        "authored_pack_id": h3_pack.authored_pack_id,
        "ktx2_pack_id": h3_pack.pack_id,
        "fallback_bundle_id": h2_bundle.bundle_id,
        "profiles": {key: profile.model_dump(mode="json") for key, profile in profiles.items()},
    }
    bundle_id = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return MaterialBundleV2(
        bundle_id=bundle_id,
        source_pack_id=h3_pack.source_pack_id,
        authored_pack_id=h3_pack.authored_pack_id,
        ktx2_pack_id=h3_pack.pack_id,
        fallback_bundle_id=h2_bundle.bundle_id,
        profiles=profiles,
    )
