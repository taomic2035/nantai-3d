"""Immutable H3 KTX2 material profile with an exact H2 PNG fallback.

This module only binds already-verified evidence.  It does not infer texture
quality or promote the synthetic H3 sources beyond preview-only/L0.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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

from .h3_material_sources import H3_HERO_SLOTS
from .ktx2_toolchain import (
    KTX_MAX_BYTES,
    H3Ktx2Pack,
    KtxTextureDescriptor,
    KtxToolchainError,
    audit_ktx2_bytes,
    load_h3_ktx2_pack,
)
from .material_bundle import (
    MATERIAL_PARAMETERS,
    MAX_DERIVED_MAP_BYTES,
    DerivedMaterialBundle,
    MaterialBundleError,
    MaterialMapDescriptor,
    _flush_directory,
    _flush_file,
    _is_linklike,
    _move_directory_noreplace,
    _prepare_real_directory,
    _read_stable_file,
    _require_real_directory,
    load_material_bundle,
    read_verified_material_map,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TextureRole = Literal["base_color", "normal", "orm"]
MediaType = Literal["image/png", "image/ktx2"]
Transfer = Literal["srgb", "linear"]

MATERIAL_BUNDLE_V2_SCHEMA = "nantai.synthetic-village.derived-material-bundle.v2"
MATERIAL_BUNDLE_V2_MANIFEST = "manifest.json"
MAX_MATERIAL_BUNDLE_V2_MANIFEST_BYTES = 8 * 1024 * 1024
H3_PROFILE_ID = "h3-ai-ktx2-4k"
H2_PROFILE_ID = "h2-png-1k-fallback"
ACCEPTED_H2_MATERIAL_BUNDLE_ID = "b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13"


class MaterialBundleV2Error(MaterialBundleError):
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


@dataclass(frozen=True)
class PreparedMaterialBundleV2:
    staging_root: Path
    manifest: MaterialBundleV2


@dataclass(frozen=True)
class MaterialBundleV2Result:
    bundle_id: str
    final_directory: Path
    texture_object_count: int
    reused: bool


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


def _profile_texture_objects(
    bundle: MaterialBundleV2,
) -> dict[str, tuple[ProfileTextureDescriptor, ...]]:
    grouped: dict[str, list[ProfileTextureDescriptor]] = {}
    for profile in bundle.profiles.values():
        for descriptor in profile.textures:
            grouped.setdefault(descriptor.object_path, []).append(
                descriptor,
            )
    return {
        object_path: tuple(descriptors)
        for object_path, descriptors in grouped.items()
    }


def _assert_source_closure(
    bundle: MaterialBundleV2,
    *,
    h2_bundle: DerivedMaterialBundle,
    h3_pack: H3Ktx2Pack,
) -> None:
    if (
        bundle.fallback_bundle_id != h2_bundle.bundle_id
        or bundle.ktx2_pack_id != h3_pack.pack_id
        or bundle.source_pack_id != h3_pack.source_pack_id
        or bundle.authored_pack_id != h3_pack.authored_pack_id
    ):
        raise MaterialBundleV2Error(
            "material bundle v2 source identities disagree",
        )
    h2_descriptors = {
        (record.slot_id, role): getattr(record, role)
        for record in h2_bundle.records
        for role in ("base_color", "normal", "orm")
    }
    h3_descriptors = {
        (record.slot_id, role): getattr(record, role)
        for record in h3_pack.records
        for role in ("base_color", "normal", "orm")
    }
    for profile in bundle.profiles.values():
        for descriptor in profile.textures:
            key = (descriptor.slot_id, descriptor.role)
            if descriptor.media_type == "image/png":
                source = h2_descriptors.get(key)
                expected_transfer = (
                    "srgb"
                    if source is not None
                    and source.color_space == "srgb"
                    else "linear"
                )
            else:
                source = h3_descriptors.get(key)
                expected_transfer = (
                    source.transfer if source is not None else None
                )
            if (
                source is None
                or source.sha256 != descriptor.sha256
                or source.bytes != descriptor.bytes
                or source.width != descriptor.width
                or source.height != descriptor.height
                or expected_transfer != descriptor.transfer
            ):
                raise MaterialBundleV2Error(
                    "material bundle v2 descriptor disagrees with verified sources",
                )


def prepare_material_bundle_v2(
    *,
    h2_bundle_root: Path,
    ktx2_root: Path,
    bundle: MaterialBundleV2,
    staging_root: Path,
) -> PreparedMaterialBundleV2:
    """Copy the exact dual-profile closure into one absent verified directory."""

    staging_root = Path(staging_root).expanduser().absolute()
    if staging_root.exists() or _is_linklike(staging_root):
        raise MaterialBundleV2Error(
            "material bundle v2 staging root must start absent",
        )
    try:
        h2_root = _require_real_directory(
            Path(h2_bundle_root),
            label="H2 material bundle root",
        )
        ktx_root = _require_real_directory(
            Path(ktx2_root),
            label="H3 KTX2 pack root",
        )
        h2_bundle = load_material_bundle(h2_root)
        h3_pack = load_h3_ktx2_pack(ktx_root)
        _assert_source_closure(
            bundle,
            h2_bundle=h2_bundle,
            h3_pack=h3_pack,
        )
        h3_descriptors = {
            (record.slot_id, role): getattr(record, role)
            for record in h3_pack.records
            for role in ("base_color", "normal", "orm")
        }

        staging_root.mkdir(parents=True, exist_ok=False)
        object_root = staging_root / "objects"
        object_root.mkdir()
        payloads: dict[str, bytes] = {}
        for object_path, descriptors in sorted(
            _profile_texture_objects(bundle).items(),
        ):
            descriptor = descriptors[0]
            if descriptor.media_type == "image/png":
                payload = read_verified_material_map(
                    h2_root,
                    bundle=h2_bundle,
                    slot_id=descriptor.slot_id,
                    role=descriptor.role,
                )
            else:
                source = h3_descriptors[
                    (descriptor.slot_id, descriptor.role)
                ]
                payload = _read_stable_file(
                    ktx_root / source.object_path,
                    maximum_bytes=KTX_MAX_BYTES,
                    label="H3 KTX2 profile object",
                )
            if (
                len(payload) != descriptor.bytes
                or hashlib.sha256(payload).hexdigest()
                != descriptor.sha256
            ):
                raise MaterialBundleV2Error(
                    "material bundle v2 source object identity disagrees",
                )
            previous = payloads.setdefault(object_path, payload)
            if previous != payload:
                raise MaterialBundleV2Error(
                    "material bundle v2 object path has conflicting bytes",
                )
            (staging_root / object_path).write_bytes(payload)
        (staging_root / MATERIAL_BUNDLE_V2_MANIFEST).write_bytes(
            canonical_material_bundle_v2_bytes(bundle),
        )
        verified = load_material_bundle_v2(staging_root)
        if verified != bundle:
            raise MaterialBundleV2Error(
                "prepared material bundle v2 identity changed",
            )
        return PreparedMaterialBundleV2(
            staging_root=staging_root,
            manifest=verified,
        )
    except MaterialBundleV2Error:
        if staging_root.is_dir() and not _is_linklike(staging_root):
            shutil.rmtree(staging_root, ignore_errors=True)
        raise
    except (
        KtxToolchainError,
        OSError,
        ValidationError,
        ValueError,
    ) as exc:
        if staging_root.is_dir() and not _is_linklike(staging_root):
            shutil.rmtree(staging_root, ignore_errors=True)
        raise MaterialBundleV2Error(
            f"material bundle v2 preparation failed: {exc}",
        ) from exc


def _audit_profile_texture(
    payload: bytes,
    descriptor: ProfileTextureDescriptor,
) -> None:
    if descriptor.media_type == "image/png":
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or image.size
                != (descriptor.width, descriptor.height)
            ):
                raise MaterialBundleV2Error(
                    "material bundle v2 PNG object format disagrees",
                )
        return
    codecs = ("uastc", "etc1s") if descriptor.role == "orm" else ("uastc",)
    for codec in codecs:
        try:
            audit = audit_ktx2_bytes(
                payload,
                expected_transfer=descriptor.transfer,
                expected_codec=codec,
            )
        except KtxToolchainError:
            continue
        if (
            audit.sha256 == descriptor.sha256
            and audit.bytes == descriptor.bytes
            and audit.width == descriptor.width
            and audit.height == descriptor.height
        ):
            return
    raise MaterialBundleV2Error(
        "material bundle v2 KTX2 object format disagrees",
    )


def load_material_bundle_v2(root: Path) -> MaterialBundleV2:
    """Verify canonical manifest bytes and the complete dual-profile closure."""

    try:
        root = _require_real_directory(
            Path(root),
            label="material bundle v2 root",
        )
        root_entries = tuple(root.iterdir())
        if (
            {path.name for path in root_entries}
            != {MATERIAL_BUNDLE_V2_MANIFEST, "objects"}
            or len(root_entries) != 2
            or any(_is_linklike(path) for path in root_entries)
        ):
            raise MaterialBundleV2Error(
                "material bundle v2 root closure is incomplete or extra",
            )
        object_root = _require_real_directory(
            root / "objects",
            label="material bundle v2 objects",
        )
        raw = _read_stable_file(
            root / MATERIAL_BUNDLE_V2_MANIFEST,
            maximum_bytes=MAX_MATERIAL_BUNDLE_V2_MANIFEST_BYTES,
            label="material bundle v2 manifest",
        )
        bundle = MaterialBundleV2.model_validate_json(raw)
        if raw != canonical_material_bundle_v2_bytes(bundle):
            raise MaterialBundleV2Error(
                "material bundle v2 manifest is not canonical JSON",
            )
        objects = _profile_texture_objects(bundle)
        entries = tuple(object_root.iterdir())
        actual = {
            path.relative_to(root).as_posix()
            for path in entries
            if path.is_file() and not _is_linklike(path)
        }
        if (
            actual != set(objects)
            or len(entries) != len(actual)
        ):
            raise MaterialBundleV2Error(
                "material bundle v2 object closure is incomplete or extra",
            )
        for object_path, descriptors in objects.items():
            maximum = (
                KTX_MAX_BYTES
                if descriptors[0].media_type == "image/ktx2"
                else MAX_DERIVED_MAP_BYTES
            )
            payload = _read_stable_file(
                root / object_path,
                maximum_bytes=maximum,
                label="material bundle v2 object",
            )
            for descriptor in descriptors:
                if (
                    len(payload) != descriptor.bytes
                    or hashlib.sha256(payload).hexdigest()
                    != descriptor.sha256
                ):
                    raise MaterialBundleV2Error(
                        "material bundle v2 object identity disagrees",
                    )
                _audit_profile_texture(payload, descriptor)
        return bundle
    except MaterialBundleV2Error:
        raise
    except (
        KtxToolchainError,
        OSError,
        UnidentifiedImageError,
        ValidationError,
        ValueError,
    ) as exc:
        raise MaterialBundleV2Error(
            f"material bundle v2 cannot be trusted: {exc}",
        ) from exc


def read_verified_material_texture_v2(
    root: Path,
    *,
    bundle: MaterialBundleV2,
    profile_id: Literal[
        "h3-ai-ktx2-4k",
        "h2-png-1k-fallback",
    ],
    sha256: str,
    media_type: MediaType,
) -> bytes:
    """Read one object only when it belongs to the selected verified profile."""

    if profile_id not in {H3_PROFILE_ID, H2_PROFILE_ID}:
        raise MaterialBundleV2Error(
            "material bundle v2 profile is unsupported",
        )
    current = load_material_bundle_v2(root)
    if current != bundle:
        raise MaterialBundleV2Error(
            "material bundle v2 changed after selection",
        )
    descriptor = next(
        (
            row
            for row in current.profiles[profile_id].textures
            if row.sha256 == sha256
            and row.media_type == media_type
        ),
        None,
    )
    if descriptor is None:
        raise MaterialBundleV2Error(
            "material texture is absent from the selected profile",
        )
    payload = _read_stable_file(
        Path(root) / descriptor.object_path,
        maximum_bytes=(
            KTX_MAX_BYTES
            if media_type == "image/ktx2"
            else MAX_DERIVED_MAP_BYTES
        ),
        label="selected material bundle v2 object",
    )
    if (
        len(payload) != descriptor.bytes
        or hashlib.sha256(payload).hexdigest()
        != descriptor.sha256
    ):
        raise MaterialBundleV2Error(
            "selected material bundle v2 object identity disagrees",
        )
    return payload


def _durably_flush_material_bundle_v2(staging: Path) -> None:
    bundle = load_material_bundle_v2(staging)
    for object_path in sorted(_profile_texture_objects(bundle)):
        _flush_file(staging / object_path)
    _flush_file(staging / MATERIAL_BUNDLE_V2_MANIFEST)
    _flush_directory(staging / "objects")
    _flush_directory(staging)
    if load_material_bundle_v2(staging) != bundle:
        raise MaterialBundleV2Error(
            "material bundle v2 changed during durability flush",
        )


def _cleanup_material_v2_staging(
    staging: Path,
    *,
    work_root: Path,
) -> None:
    if (
        staging.parent != work_root
        or not staging.name.startswith(".material-v2-")
    ):
        raise MaterialBundleV2Error(
            "refusing to clean an unowned material v2 staging path",
        )
    if _is_linklike(staging):
        staging.unlink(missing_ok=True)
    elif staging.exists():
        if not staging.is_dir():
            raise MaterialBundleV2Error(
                "material v2 staging path became irregular",
            )
        shutil.rmtree(staging)


def publish_material_bundle_v2(
    *,
    h2_bundle_root: Path,
    ktx2_root: Path,
    bundle: MaterialBundleV2,
    publication_root: Path,
    work_root: Path,
) -> MaterialBundleV2Result:
    """Prepare and atomically publish an immutable dual-profile closure."""

    staging: Path | None = None
    try:
        h2_root = _require_real_directory(
            Path(h2_bundle_root),
            label="H2 material bundle root",
        )
        ktx_root = _require_real_directory(
            Path(ktx2_root),
            label="H3 KTX2 pack root",
        )
        publication = _prepare_real_directory(
            Path(publication_root),
            label="material v2 publication root",
        )
        work = _prepare_real_directory(
            Path(work_root),
            label="material v2 work root",
        )
        with ProjectFileLock(
            work / ".material-bundle-v2.lock",
            role="writer",
        ):
            h2_before = load_material_bundle(h2_root)
            h3_before = load_h3_ktx2_pack(ktx_root)
            _assert_source_closure(
                bundle,
                h2_bundle=h2_before,
                h3_pack=h3_before,
            )
            staging = work / f".material-v2-{uuid.uuid4().hex}"
            prepared = prepare_material_bundle_v2(
                h2_bundle_root=h2_root,
                ktx2_root=ktx_root,
                bundle=bundle,
                staging_root=staging,
            )
            h2_after = load_material_bundle(h2_root)
            h3_after = load_h3_ktx2_pack(ktx_root)
            if h2_after != h2_before or h3_after != h3_before:
                raise MaterialBundleV2Error(
                    "material bundle v2 sources changed during publication",
                )
            destination = publication / bundle.bundle_id
            if destination.exists() or _is_linklike(destination):
                existing = load_material_bundle_v2(destination)
                if existing != bundle:
                    raise MaterialBundleV2Error(
                        "existing material v2 identity has different evidence",
                    )
                _cleanup_material_v2_staging(
                    staging,
                    work_root=work,
                )
                staging = None
                return MaterialBundleV2Result(
                    bundle_id=existing.bundle_id,
                    final_directory=destination,
                    texture_object_count=len(
                        _profile_texture_objects(existing),
                    ),
                    reused=True,
                )
            _durably_flush_material_bundle_v2(staging)
            _move_directory_noreplace(staging, destination)
            staging = None
            published = load_material_bundle_v2(destination)
            if published != prepared.manifest:
                raise MaterialBundleV2Error(
                    "published material bundle v2 identity changed",
                )
            return MaterialBundleV2Result(
                bundle_id=published.bundle_id,
                final_directory=destination,
                texture_object_count=len(
                    _profile_texture_objects(published),
                ),
                reused=False,
            )
    except MaterialBundleV2Error:
        raise
    except (
        JobContractError,
        KtxToolchainError,
        OSError,
        ValidationError,
        ValueError,
    ) as exc:
        raise MaterialBundleV2Error(
            f"material bundle v2 publication failed: {exc}",
        ) from exc
    finally:
        if staging is not None:
            try:
                _cleanup_material_v2_staging(
                    staging,
                    work_root=staging.parent,
                )
            except (MaterialBundleV2Error, OSError):
                pass
