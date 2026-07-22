"""Pinned KTX 4.4.2 evidence and independent KTX2 structure auditing.

The official validator is necessary but not sufficient: runtime bundles also
pass the small parser in this module so dimensions, mip closure, transfer
function, and universal codec cannot be inferred from filenames or commands.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from skimage.metrics import structural_similarity

from .h3_material_authoring import (
    load_h3_authored_material_pack,
    read_verified_h3_authored_map,
)
from .h3_material_sources import (
    H3_HERO_SLOTS,
    H3MaterialSourceError,
    _prepare_real_directory,
    _read_stable_bytes,
    _require_real_directory,
)

KTX_TOOL_VERSION = "4.4.2"
KTX_DARWIN_ARM64_ASSET = "KTX-Software-4.4.2-Darwin-arm64.pkg"
KTX_DARWIN_ARM64_URL = (
    "https://github.com/KhronosGroup/KTX-Software/releases/download/"
    "v4.4.2/KTX-Software-4.4.2-Darwin-arm64.pkg"
)
KTX_DARWIN_ARM64_SHA256 = (
    "500bd8f9d63358c3f3a0d83b724c8574436a72c37dc0e4bad90ec1ca38032c3c"
)
KTX_WINDOWS_X64_ASSET = "KTX-Software-4.4.2-Windows-x64.exe"
KTX_WINDOWS_X64_URL = (
    "https://github.com/KhronosGroup/KTX-Software/releases/download/"
    "v4.4.2/KTX-Software-4.4.2-Windows-x64.exe"
)
KTX_WINDOWS_X64_SHA256 = (
    "1f323b0fec19794f5e6c0425a61d4b1da396872a10be862d105f4f4b2d2957fe"
)
KTX_WINDOWS_SIGNER_SUBJECT = (
    "CN=The Khronos Group Inc, O=The Khronos Group Inc, L=Beaverton, "
    "S=Oregon, C=US, SERIALNUMBER=2568818, OID.2.5.4.15=Private "
    "Organization, OID.1.3.6.1.4.1.311.60.2.1.2=California, "
    "OID.1.3.6.1.4.1.311.60.2.1.3=US"
)
KTX_WINDOWS_SIGNER_THUMBPRINT = "CA07F94EBD7402F3F563FE5C3DF71DF1B88C1B06"
KTX2_MAGIC = b"\xabKTX 20\xbb\r\n\x1a\n"
KTX_LEVEL_DIMENSIONS = (
    4096,
    2048,
    1024,
    512,
    256,
    128,
    64,
    32,
    16,
    8,
    4,
    2,
    1,
)
KTX_RECEIPT_SCHEMA = "nantai.ktx-tool-receipt.v1"
KTX_WINDOWS_RECEIPT_SCHEMA = "nantai.ktx-windows-tool-receipt.v1"
H3_KTX2_PACK_SCHEMA = "nantai.h3-ktx2-pack.v1"
H3_KTX2_PACK_MANIFEST = "manifest.json"
KTX_TEXTURE_CACHE_SCHEMA = "nantai.ktx-texture-cache-entry.v1"
KTX_TEXTURE_CACHE_MANIFEST = "descriptor.json"
KTX_RECEIPT_NAME = "receipt.json"
KTX_MAX_BYTES = 512 * 1024 * 1024
KTX_MAX_PROCESS_OUTPUT = 1024 * 1024
KTX_PROCESS_TIMEOUT_SECONDS = 120
KTX_COMPILE_TIMEOUT_SECONDS = 6 * 60 * 60
KTX_DF_MODEL_ETC1S = 163
KTX_DF_MODEL_UASTC = 166
KTX_DF_TRANSFER_LINEAR = 1
KTX_DF_TRANSFER_SRGB = 2
KTX_SS_BASIS_LZ = 1
KTX_SS_ZSTD = 2
KTX_BASE_COLOUR_MIN_SSIM = 0.97
KTX_NORMAL_MIN_MEAN_COSINE = 0.98
KTX_NORMAL_MIN_P01_COSINE = 0.90
KTX_ORM_MAX_CHANNEL_ERROR = 12 / 255
KTX_PACKAGE_SIGNER = "Developer ID Installer: The Khronos Group, Inc. (TD2656HYNK)"
KTX_TEAM_ID = "TD2656HYNK"
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TextureRole = Literal["base_color", "normal", "orm"]
Transfer = Literal["srgb", "linear"]
Codec = Literal["uastc", "etc1s"]


class KtxToolchainError(RuntimeError):
    """Pinned KTX evidence or a KTX2 object cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _portable_relative_path(value: str) -> str:
    parsed = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError("path must be a portable relative POSIX path")
    return value


class KtxToolFile(FrozenModel):
    relative_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1, le=KTX_MAX_BYTES)

    @field_validator("relative_path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        return _portable_relative_path(value)


class KtxToolBinary(KtxToolFile):
    version_output: str = Field(min_length=1, max_length=4096)
    codesign_valid: Literal[True] = True


class KtxToolReceipt(FrozenModel):
    schema_version: Literal[
        "nantai.ktx-tool-receipt.v1"
    ] = KTX_RECEIPT_SCHEMA
    version: Literal["4.4.2"] = KTX_TOOL_VERSION
    platform: Literal["darwin-arm64"] = "darwin-arm64"
    package_asset: Literal[
        "KTX-Software-4.4.2-Darwin-arm64.pkg"
    ] = KTX_DARWIN_ARM64_ASSET
    package_url: Literal[
        "https://github.com/KhronosGroup/KTX-Software/releases/download/"
        "v4.4.2/KTX-Software-4.4.2-Darwin-arm64.pkg"
    ] = KTX_DARWIN_ARM64_URL
    package_sha256: Literal[
        "500bd8f9d63358c3f3a0d83b724c8574436a72c37dc0e4bad90ec1ca38032c3c"
    ] = KTX_DARWIN_ARM64_SHA256
    package_file: KtxToolFile
    package_signature_status: Literal["trusted"] = "trusted"
    package_notarization_status: Literal["trusted"] = "trusted"
    package_signer: Literal[
        "Developer ID Installer: The Khronos Group, Inc. (TD2656HYNK)"
    ] = KTX_PACKAGE_SIGNER
    package_team_id: Literal["TD2656HYNK"] = KTX_TEAM_ID
    installation_scope: Literal[
        "project-private-extracted-signed-pkg"
    ] = "project-private-extracted-signed-pkg"
    system_installed: Literal[False] = False
    toktx: KtxToolBinary
    ktx: KtxToolBinary
    library: KtxToolFile
    license: KtxToolFile

    @model_validator(mode="after")
    def _paths_are_exact(self) -> KtxToolReceipt:
        paths = (
            self.package_file.relative_path,
            self.toktx.relative_path,
            self.ktx.relative_path,
            self.library.relative_path,
            self.license.relative_path,
        )
        if paths != (
            f"downloads/{KTX_DARWIN_ARM64_ASSET}",
            "runtime/bin/toktx",
            "runtime/bin/ktx",
            "runtime/lib/libktx.4.4.2.dylib",
            "runtime/licenses/License.rtf",
        ):
            raise ValueError("KTX receipt runtime paths are not exact")
        if self.package_file.sha256 != self.package_sha256:
            raise ValueError("KTX receipt package file SHA disagrees with pin")
        if "4.4.2" not in self.toktx.version_output:
            raise ValueError("toktx receipt version output is not 4.4.2")
        if "4.4.2" not in self.ktx.version_output:
            raise ValueError("ktx receipt version output is not 4.4.2")
        return self


class WindowsKtxToolBinary(KtxToolFile):
    version_output: str | None = Field(default=None, min_length=1, max_length=4096)
    authenticode_status: Literal["Valid"] = "Valid"
    signer_subject: Literal[
        "CN=The Khronos Group Inc, O=The Khronos Group Inc, L=Beaverton, "
        "S=Oregon, C=US, SERIALNUMBER=2568818, OID.2.5.4.15=Private "
        "Organization, OID.1.3.6.1.4.1.311.60.2.1.2=California, "
        "OID.1.3.6.1.4.1.311.60.2.1.3=US"
    ] = KTX_WINDOWS_SIGNER_SUBJECT
    signer_thumbprint: Literal[
        "CA07F94EBD7402F3F563FE5C3DF71DF1B88C1B06"
    ] = KTX_WINDOWS_SIGNER_THUMBPRINT


class WindowsKtxToolReceipt(FrozenModel):
    schema_version: Literal[
        "nantai.ktx-windows-tool-receipt.v1"
    ] = KTX_WINDOWS_RECEIPT_SCHEMA
    version: Literal["4.4.2"] = KTX_TOOL_VERSION
    platform: Literal["windows-x64"] = "windows-x64"
    package_asset: Literal[
        "KTX-Software-4.4.2-Windows-x64.exe"
    ] = KTX_WINDOWS_X64_ASSET
    package_url: Literal[
        "https://github.com/KhronosGroup/KTX-Software/releases/download/"
        "v4.4.2/KTX-Software-4.4.2-Windows-x64.exe"
    ] = KTX_WINDOWS_X64_URL
    package_sha256: Literal[
        "1f323b0fec19794f5e6c0425a61d4b1da396872a10be862d105f4f4b2d2957fe"
    ] = KTX_WINDOWS_X64_SHA256
    package_file: KtxToolFile
    package_signature_status: Literal["trusted"] = "trusted"
    package_signer_subject: Literal[
        "CN=The Khronos Group Inc, O=The Khronos Group Inc, L=Beaverton, "
        "S=Oregon, C=US, SERIALNUMBER=2568818, OID.2.5.4.15=Private "
        "Organization, OID.1.3.6.1.4.1.311.60.2.1.2=California, "
        "OID.1.3.6.1.4.1.311.60.2.1.3=US"
    ] = KTX_WINDOWS_SIGNER_SUBJECT
    package_signer_thumbprint: Literal[
        "CA07F94EBD7402F3F563FE5C3DF71DF1B88C1B06"
    ] = KTX_WINDOWS_SIGNER_THUMBPRINT
    installation_scope: Literal[
        "project-private-installed-signed-exe"
    ] = "project-private-installed-signed-exe"
    system_installed: Literal[False] = False
    toktx: WindowsKtxToolBinary
    ktx: WindowsKtxToolBinary
    library: WindowsKtxToolBinary
    license: KtxToolFile

    @model_validator(mode="after")
    def _paths_are_exact(self) -> WindowsKtxToolReceipt:
        paths = (
            self.package_file.relative_path,
            self.toktx.relative_path,
            self.ktx.relative_path,
            self.library.relative_path,
            self.license.relative_path,
        )
        if paths != (
            f"downloads/{KTX_WINDOWS_X64_ASSET}",
            "bin/toktx.exe",
            "bin/ktx.exe",
            "bin/ktx.dll",
            "share/doc/KTX-Software/html/license.html",
        ):
            raise ValueError("Windows KTX receipt runtime paths are not exact")
        if self.package_file.sha256 != self.package_sha256:
            raise ValueError("Windows KTX receipt package file SHA disagrees with pin")
        if (
            self.toktx.version_output is None
            or "4.4.2" not in self.toktx.version_output
        ):
            raise ValueError("Windows toktx receipt version output is not 4.4.2")
        if self.ktx.version_output is None or "4.4.2" not in self.ktx.version_output:
            raise ValueError("Windows ktx receipt version output is not 4.4.2")
        if self.library.version_output is not None:
            raise ValueError(
                "Windows KTX library must not claim version command output"
            )
        return self


AnyKtxToolReceipt = KtxToolReceipt | WindowsKtxToolReceipt


class KtxBinaryAudit(FrozenModel):
    sha256: Sha256
    bytes: int = Field(ge=1, le=KTX_MAX_BYTES)
    width: Literal[4096] = 4096
    height: Literal[4096] = 4096
    level_count: Literal[13] = 13
    level_dimensions: tuple[int, ...] = KTX_LEVEL_DIMENSIONS
    transfer: Transfer
    codec: Codec
    media_type: Literal["image/ktx2"] = "image/ktx2"


class KtxDecodedQuality(FrozenModel):
    role: TextureRole
    base_colour_ssim: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    normal_mean_cosine: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    normal_p01_cosine: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    orm_max_channel_error: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    passed: Literal[True] = True

    @model_validator(mode="after")
    def _only_role_metrics_are_present(self) -> KtxDecodedQuality:
        present = {
            "base_color": self.base_colour_ssim is not None,
            "normal": (
                self.normal_mean_cosine is not None
                and self.normal_p01_cosine is not None
            ),
            "orm": self.orm_max_channel_error is not None,
        }
        if not present[self.role] or sum(present.values()) != 1:
            raise ValueError("decoded quality metrics do not match texture role")
        return self


class KtxTextureDescriptor(FrozenModel):
    role: TextureRole
    source_sha256: Sha256
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1, le=KTX_MAX_BYTES)
    width: Literal[4096] = 4096
    height: Literal[4096] = 4096
    level_dimensions: tuple[int, ...] = KTX_LEVEL_DIMENSIONS
    media_type: Literal["image/ktx2"] = "image/ktx2"
    transfer: Transfer
    codec: Codec
    tool_version: Literal["4.4.2"] = KTX_TOOL_VERSION
    toktx_sha256: Sha256
    command_options: tuple[str, ...]
    official_validation: Literal[True] = True
    repeat_build_byte_equal: Literal[True] = True
    orm_etc1s_fallback: bool
    quality: KtxDecodedQuality

    @model_validator(mode="after")
    def _descriptor_is_role_exact(self) -> KtxTextureDescriptor:
        if self.object_path != f"objects/{self.sha256}.ktx2":
            raise ValueError("KTX2 object path must match its content address")
        if self.level_dimensions != KTX_LEVEL_DIMENSIONS:
            raise ValueError("KTX2 descriptor mip dimensions are incomplete")
        expected_transfer = "srgb" if self.role == "base_color" else "linear"
        if self.transfer != expected_transfer or self.quality.role != self.role:
            raise ValueError("KTX2 descriptor role semantics disagree")
        if self.role != "orm" and self.codec != "uastc":
            raise ValueError("base colour and normal KTX2 must use UASTC")
        if self.orm_etc1s_fallback and (
            self.role != "orm" or self.codec != "uastc"
        ):
            raise ValueError("ORM ETC1S fallback evidence is inconsistent")
        return self


class KtxTextureCacheEntry(FrozenModel):
    schema_version: Literal[
        "nantai.ktx-texture-cache-entry.v1"
    ] = KTX_TEXTURE_CACHE_SCHEMA
    cache_key: Sha256
    source_sha256: Sha256
    role: TextureRole
    package_sha256: Sha256
    toktx_sha256: Sha256
    ktx_sha256: Sha256
    command_options: tuple[str, ...]
    descriptor: KtxTextureDescriptor

    @model_validator(mode="after")
    def _identity_is_closed(self) -> KtxTextureCacheEntry:
        descriptor = self.descriptor
        if (
            descriptor.source_sha256 != self.source_sha256
            or descriptor.role != self.role
            or descriptor.toktx_sha256 != self.toktx_sha256
            or descriptor.command_options != self.command_options
        ):
            raise ValueError("KTX2 cache descriptor identity disagrees")
        expected = hashlib.sha256(
            _canonical_ktx_texture_cache_identity_bytes(
                source_sha256=self.source_sha256,
                role=self.role,
                package_sha256=self.package_sha256,
                toktx_sha256=self.toktx_sha256,
                ktx_sha256=self.ktx_sha256,
                command_options=self.command_options,
            ),
        ).hexdigest()
        if self.cache_key != expected:
            raise ValueError("KTX2 cache key disagrees with its identity")
        return self


class H3Ktx2MaterialRecord(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    base_color: KtxTextureDescriptor
    normal: KtxTextureDescriptor
    orm: KtxTextureDescriptor

    @model_validator(mode="after")
    def _roles_are_exact(self) -> H3Ktx2MaterialRecord:
        if (
            self.base_color.role,
            self.normal.role,
            self.orm.role,
        ) != ("base_color", "normal", "orm"):
            raise ValueError("H3 KTX2 material roles are incomplete")
        return self


class H3Ktx2Pack(FrozenModel):
    schema_version: Literal["nantai.h3-ktx2-pack.v1"] = H3_KTX2_PACK_SCHEMA
    pack_id: Sha256
    source_pack_id: Sha256
    authored_pack_id: Sha256
    synthetic: Literal[True] = True
    ai_generated: Literal[True] = True
    real_photo_textures: Literal[False] = False
    geometry_usability: Literal["preview-only"] = "preview-only"
    metric_alignment: Literal[False] = False
    verification_level: Literal["L0"] = "L0"
    tool_version: Literal["4.4.2"] = KTX_TOOL_VERSION
    package_sha256: Sha256
    toktx_sha256: Sha256
    ktx_sha256: Sha256
    records: tuple[H3Ktx2MaterialRecord, ...]

    @model_validator(mode="after")
    def _pack_is_complete_and_content_addressed(self) -> H3Ktx2Pack:
        if tuple(record.slot_id for record in self.records) != H3_HERO_SLOTS:
            raise ValueError("H3 KTX2 pack must contain the exact eight slots")
        expected = hashlib.sha256(
            canonical_h3_ktx2_pack_bytes(self, exclude_pack_id=True),
        ).hexdigest()
        if self.pack_id != expected:
            raise ValueError("H3 KTX2 pack ID disagrees with canonical bytes")
        return self


@dataclass(frozen=True)
class PreparedH3Ktx2Pack:
    root: Path
    manifest: H3Ktx2Pack


def _canonical_json_bytes(value: BaseModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_ktx_tool_receipt_bytes(receipt: AnyKtxToolReceipt) -> bytes:
    return _canonical_json_bytes(receipt)


def canonical_h3_ktx2_pack_bytes(
    pack: H3Ktx2Pack,
    *,
    exclude_pack_id: bool = False,
) -> bytes:
    payload = pack.model_dump(mode="json")
    if exclude_pack_id:
        payload.pop("pack_id")
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_ktx_texture_cache_identity_bytes(
    *,
    source_sha256: str,
    role: TextureRole,
    package_sha256: str,
    toktx_sha256: str,
    ktx_sha256: str,
    command_options: tuple[str, ...],
) -> bytes:
    return (
        json.dumps(
            {
                "command_options": command_options,
                "ktx_sha256": ktx_sha256,
                "package_sha256": package_sha256,
                "role": role,
                "source_sha256": source_sha256,
                "toktx_sha256": toktx_sha256,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_ktx_texture_cache_entry_bytes(
    entry: KtxTextureCacheEntry,
) -> bytes:
    return _canonical_json_bytes(entry)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                if size > KTX_MAX_BYTES:
                    raise KtxToolchainError(f"KTX file is too large: {path.name}")
    except OSError as exc:
        raise KtxToolchainError(f"KTX file cannot be read: {path.name}") from exc
    if size < 1:
        raise KtxToolchainError(f"KTX file is empty: {path.name}")
    return digest.hexdigest(), size


def _run_bounded(
    command: tuple[str, ...],
    *,
    environment: dict[str, str] | None = None,
    timeout: int = KTX_PROCESS_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise KtxToolchainError(
            f"KTX process could not run: {Path(command[0]).name}",
        ) from exc
    output_bytes = len(completed.stdout.encode("utf-8")) + len(
        completed.stderr.encode("utf-8"),
    )
    if output_bytes > KTX_MAX_PROCESS_OUTPUT:
        raise KtxToolchainError("KTX process output exceeded the bounded limit")
    return completed


def _require_success(
    command: tuple[str, ...],
    *,
    environment: dict[str, str] | None = None,
    label: str,
    timeout: int = KTX_PROCESS_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    completed = _run_bounded(command, environment=environment, timeout=timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:4096]
        raise KtxToolchainError(f"{label} failed: {detail}")
    return completed


def toktx_command(
    executable: Path,
    *,
    role: TextureRole,
    source: Path,
    output: Path,
    force_uastc: bool = False,
) -> tuple[str, ...]:
    """Construct the frozen role-aware KTX 4.4.2 command without a shell."""

    if role not in {"base_color", "normal", "orm"}:
        raise KtxToolchainError(f"unknown KTX texture role: {role}")
    transfer = "srgb" if role == "base_color" else "linear"
    primaries = "srgb" if role == "base_color" else "none"
    if role == "orm" and not force_uastc:
        encoding = (
            "--encode",
            "etc1s",
            "--clevel",
            "5",
            "--qlevel",
            "255",
        )
    else:
        encoding = (
            "--encode",
            "uastc",
            "--uastc_quality",
            "4",
            "--zcmp",
            "18",
        )
    return (
        str(executable),
        "--t2",
        *encoding,
        "--genmipmap",
        "--assign_oetf",
        transfer,
        "--assign_primaries",
        primaries,
        str(output),
        str(source),
    )


def validation_command(
    executable: Path,
    source: Path,
) -> tuple[str, ...]:
    return (
        str(executable),
        "validate",
        "--format",
        "mini-json",
        "--warnings-as-errors",
        "--gltf-basisu",
        str(source),
    )


def extract_command(
    executable: Path,
    *,
    source: Path,
    output: Path,
) -> tuple[str, ...]:
    return (
        str(executable),
        "extract",
        "--transcode",
        "rgba8",
        "--level",
        "0",
        str(source),
        str(output),
    )


def _decode_quality_png(
    payload: bytes,
    *,
    label: str,
    allow_palette: bool = False,
) -> np.ndarray:
    if len(payload) < 1 or len(payload) > 128 * 1024 * 1024:
        raise KtxToolchainError(f"{label} PNG byte length is invalid")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.format != "PNG" or image.size != (4096, 4096):
                raise KtxToolchainError(
                    f"{label} must be an exact 4096 by 4096 PNG",
                )
            palette_is_safe = (
                allow_palette
                and image.mode == "P"
                and "transparency" not in image.info
            )
            if image.mode not in {"RGB", "RGBA"} and not palette_is_safe:
                raise KtxToolchainError(f"{label} must decode as RGB or RGBA")
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
    except KtxToolchainError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise KtxToolchainError(f"{label} is not a valid PNG") from exc


def _tiled_base_colour_ssim(
    reference: np.ndarray,
    decoded: np.ndarray,
) -> float:
    scores = []
    tile_size = 512
    for y in range(0, 4096, tile_size):
        for x in range(0, 4096, tile_size):
            scores.append(
                structural_similarity(
                    reference[y : y + tile_size, x : x + tile_size],
                    decoded[y : y + tile_size, x : x + tile_size],
                    channel_axis=2,
                    data_range=255,
                ),
            )
    return float(np.mean(np.asarray(scores, dtype=np.float64)))


def _normal_cosines(
    reference: np.ndarray,
    decoded: np.ndarray,
) -> np.ndarray:
    values = []
    for start in range(0, 4096, 256):
        reference_rows = (
            reference[start : start + 256].astype(np.float32) / 127.5 - 1.0
        )
        decoded_rows = (
            decoded[start : start + 256].astype(np.float32) / 127.5 - 1.0
        )
        reference_rows /= np.maximum(
            np.linalg.norm(reference_rows, axis=2, keepdims=True),
            1e-12,
        )
        decoded_rows /= np.maximum(
            np.linalg.norm(decoded_rows, axis=2, keepdims=True),
            1e-12,
        )
        values.append(
            np.sum(reference_rows * decoded_rows, axis=2).reshape(-1),
        )
    return np.concatenate(values)


def measure_decoded_quality(
    reference_payload: bytes,
    decoded_payload: bytes,
    *,
    role: TextureRole,
) -> KtxDecodedQuality:
    """Measure and enforce frozen decoded-quality gates for one level-0 PNG."""

    if role not in {"base_color", "normal", "orm"}:
        raise KtxToolchainError(f"unknown decoded texture role: {role}")
    reference = _decode_quality_png(reference_payload, label="reference")
    decoded = _decode_quality_png(
        decoded_payload,
        label="decoded KTX level",
        allow_palette=True,
    )
    if role == "base_color":
        score = _tiled_base_colour_ssim(reference, decoded)
        if score < KTX_BASE_COLOUR_MIN_SSIM:
            raise KtxToolchainError(
                "base_color decoded quality failed the SSIM gate",
            )
        return KtxDecodedQuality(
            role=role,
            base_colour_ssim=round(score, 8),
        )
    if role == "normal":
        cosines = _normal_cosines(reference, decoded)
        mean = float(np.mean(cosines, dtype=np.float64))
        percentile = float(np.quantile(cosines, 0.01))
        if (
            mean < KTX_NORMAL_MIN_MEAN_COSINE
            or percentile < KTX_NORMAL_MIN_P01_COSINE
        ):
            raise KtxToolchainError(
                "normal decoded quality failed the cosine gates",
            )
        return KtxDecodedQuality(
            role=role,
            normal_mean_cosine=round(mean, 8),
            normal_p01_cosine=round(percentile, 8),
        )
    maximum_error = float(
        np.max(
            np.abs(
                reference.astype(np.int16) - decoded.astype(np.int16),
            ),
        ),
    ) / 255.0
    if maximum_error > KTX_ORM_MAX_CHANNEL_ERROR:
        raise KtxToolchainError(
            "orm decoded quality failed the channel-error gate",
        )
    return KtxDecodedQuality(
        role=role,
        orm_max_channel_error=round(maximum_error, 8),
    )


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_compile_runner(
    command: tuple[str, ...],
    *,
    environment: dict[str, str],
    label: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return _require_success(
        command,
        environment=environment,
        label=label,
        timeout=timeout,
    )


def _read_compilation_output(path: Path, *, label: str) -> bytes:
    try:
        expected = path.stat().st_size
        if expected < 1 or expected > KTX_MAX_BYTES:
            raise KtxToolchainError(f"{label} byte length is invalid")
        payload = path.read_bytes()
    except KtxToolchainError:
        raise
    except OSError as exc:
        raise KtxToolchainError(f"{label} cannot be read") from exc
    if len(payload) != expected:
        raise KtxToolchainError(f"{label} changed during bounded read")
    return payload


def _require_official_validation(completed: subprocess.CompletedProcess[str]) -> None:
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise KtxToolchainError(
            "official KTX validator returned invalid JSON",
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("valid") is not True
        or payload.get("messages") != []
    ):
        raise KtxToolchainError(
            "official KTX validator reported messages or invalid output",
        )


def _compile_texture_codec(
    source: Path,
    *,
    role: TextureRole,
    codec: Codec,
    work_root: Path,
    tool_root: Path,
    receipt: KtxToolReceipt,
    environment: dict[str, str],
    runner: ProcessRunner,
) -> tuple[bytes, KtxBinaryAudit, KtxDecodedQuality, tuple[str, ...]]:
    output = work_root / "texture.ktx2"
    decoded = work_root / "decoded.png"
    command = toktx_command(
        tool_root / receipt.toktx.relative_path,
        role=role,
        source=source,
        output=output,
        force_uastc=codec == "uastc" and role == "orm",
    )
    runner(
        command,
        environment=environment,
        label=f"{role} {codec} compilation",
        timeout=KTX_COMPILE_TIMEOUT_SECONDS,
    )
    payload = _read_compilation_output(output, label=f"{role} KTX2 output")
    transfer: Transfer = "srgb" if role == "base_color" else "linear"
    audit = audit_ktx2_bytes(
        payload,
        expected_transfer=transfer,
        expected_codec=codec,
    )
    validation = runner(
        validation_command(
            tool_root / receipt.ktx.relative_path,
            output,
        ),
        environment=environment,
        label=f"{role} official KTX validation",
        timeout=KTX_PROCESS_TIMEOUT_SECONDS,
    )
    _require_official_validation(validation)
    runner(
        extract_command(
            tool_root / receipt.ktx.relative_path,
            source=output,
            output=decoded,
        ),
        environment=environment,
        label=f"{role} level-zero extraction",
        timeout=KTX_PROCESS_TIMEOUT_SECONDS,
    )
    quality = measure_decoded_quality(
        _read_compilation_output(source, label=f"{role} PNG source"),
        _read_compilation_output(decoded, label=f"{role} decoded PNG"),
        role=role,
    )
    return payload, audit, quality, command[1:-2]


def _compile_texture_attempt(
    source: Path,
    *,
    role: TextureRole,
    force_uastc: bool,
    work_root: Path,
    tool_root: Path,
    receipt: KtxToolReceipt,
    environment: dict[str, str],
    runner: ProcessRunner,
) -> tuple[
    bytes,
    KtxBinaryAudit,
    KtxDecodedQuality,
    tuple[str, ...],
    bool,
]:
    codec: Codec = (
        "uastc" if role != "orm" or force_uastc else "etc1s"
    )
    try:
        payload, audit, quality, options = _compile_texture_codec(
            source,
            role=role,
            codec=codec,
            work_root=work_root,
            tool_root=tool_root,
            receipt=receipt,
            environment=environment,
            runner=runner,
        )
        return payload, audit, quality, options, force_uastc
    except KtxToolchainError as exc:
        if role != "orm" or codec != "etc1s" or "decoded quality" not in str(exc):
            raise
    for path in (work_root / "texture.ktx2", work_root / "decoded.png"):
        if path.exists():
            path.unlink()
    payload, audit, quality, options = _compile_texture_codec(
        source,
        role=role,
        codec="uastc",
        work_root=work_root,
        tool_root=tool_root,
        receipt=receipt,
        environment=environment,
        runner=runner,
    )
    return payload, audit, quality, options, True


def _expected_command_options(
    role: TextureRole,
    *,
    force_uastc: bool,
) -> tuple[str, ...]:
    command = toktx_command(
        Path("toktx"),
        role=role,
        source=Path("source.png"),
        output=Path("texture.ktx2"),
        force_uastc=force_uastc,
    )
    return command[1:-2]


def _texture_cache_key(
    *,
    source_sha256: str,
    role: TextureRole,
    receipt: AnyKtxToolReceipt,
    command_options: tuple[str, ...],
) -> str:
    return hashlib.sha256(
        _canonical_ktx_texture_cache_identity_bytes(
            source_sha256=source_sha256,
            role=role,
            package_sha256=receipt.package_sha256,
            toktx_sha256=receipt.toktx.sha256,
            ktx_sha256=receipt.ktx.sha256,
            command_options=command_options,
        ),
    ).hexdigest()


def _publish_ktx2_object(
    output_root: Path,
    *,
    descriptor: KtxTextureDescriptor,
    payload: bytes,
) -> None:
    destination = output_root / descriptor.object_path
    try:
        _prepare_real_directory(
            destination.parent,
            label="KTX2 object directory",
        )
    except H3MaterialSourceError as exc:
        raise KtxToolchainError(
            f"KTX2 object directory cannot be trusted: {exc}",
        ) from exc
    if destination.exists():
        if _read_compilation_output(
            destination,
            label=f"{descriptor.role} published KTX2",
        ) != payload:
            raise KtxToolchainError(
                f"{descriptor.role} content-addressed KTX2 conflicts on disk",
            )
        return
    with destination.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _load_verified_texture_cache_entry(
    entry_root: Path,
    *,
    source: Path,
    source_sha256: str,
    role: TextureRole,
    tool_root: Path,
    receipt: AnyKtxToolReceipt,
    environment: dict[str, str],
    runner: ProcessRunner,
) -> tuple[KtxTextureDescriptor, bytes]:
    try:
        _require_real_directory(entry_root, label="KTX2 texture cache entry")
        raw = _read_stable_bytes(
            entry_root / KTX_TEXTURE_CACHE_MANIFEST,
            maximum_bytes=1024 * 1024,
            label="KTX2 texture cache descriptor",
        )
        entry = KtxTextureCacheEntry.model_validate_json(raw)
        if raw != canonical_ktx_texture_cache_entry_bytes(entry):
            raise KtxToolchainError(
                "KTX2 texture cache descriptor is not canonical JSON",
            )
        if entry.cache_key != entry_root.name:
            raise KtxToolchainError("KTX2 texture cache directory identity disagrees")
        if (
            entry.source_sha256 != source_sha256
            or entry.role != role
            or entry.package_sha256 != receipt.package_sha256
            or entry.toktx_sha256 != receipt.toktx.sha256
            or entry.ktx_sha256 != receipt.ktx.sha256
        ):
            raise KtxToolchainError("KTX2 texture cache evidence disagrees")
        object_path = entry_root / f"{entry.descriptor.sha256}.ktx2"
        expected_closure = tuple(
            sorted(
                (
                    KTX_TEXTURE_CACHE_MANIFEST,
                    object_path.name,
                ),
            ),
        )
        if _directory_closure(entry_root) != expected_closure:
            raise KtxToolchainError("KTX2 texture cache closure disagrees")
        payload = _read_stable_bytes(
            object_path,
            maximum_bytes=KTX_MAX_BYTES,
            label="KTX2 texture cache object",
        )
        descriptor = entry.descriptor
        if (
            len(payload) != descriptor.bytes
            or hashlib.sha256(payload).hexdigest() != descriptor.sha256
        ):
            raise KtxToolchainError("KTX2 texture cache object identity disagrees")
        audit = audit_ktx2_bytes(
            payload,
            expected_transfer=descriptor.transfer,
            expected_codec=descriptor.codec,
        )
        if audit.sha256 != descriptor.sha256 or audit.bytes != descriptor.bytes:
            raise KtxToolchainError("KTX2 texture cache structural audit disagrees")
        validation = runner(
            validation_command(tool_root / receipt.ktx.relative_path, object_path),
            environment=environment,
            label=f"{role} cached official KTX validation",
            timeout=KTX_PROCESS_TIMEOUT_SECONDS,
        )
        _require_official_validation(validation)
        with tempfile.TemporaryDirectory(prefix=".ktx-cache-verify.") as temporary:
            decoded = Path(temporary) / "decoded.png"
            runner(
                extract_command(
                    tool_root / receipt.ktx.relative_path,
                    source=object_path,
                    output=decoded,
                ),
                environment=environment,
                label=f"{role} cached level-zero extraction",
                timeout=KTX_PROCESS_TIMEOUT_SECONDS,
            )
            quality = measure_decoded_quality(
                _read_compilation_output(source, label=f"{role} PNG source"),
                _read_compilation_output(
                    decoded,
                    label=f"{role} cached decoded PNG",
                ),
                role=role,
            )
        if quality != descriptor.quality:
            raise KtxToolchainError("KTX2 texture cache decoded quality disagrees")
        return descriptor, payload
    except KtxToolchainError:
        raise
    except (H3MaterialSourceError, OSError, ValidationError, ValueError) as exc:
        raise KtxToolchainError(
            f"KTX2 texture cache entry cannot be trusted: {exc}",
        ) from exc


def _publish_texture_cache_entry(
    cache_root: Path,
    *,
    descriptor: KtxTextureDescriptor,
    payload: bytes,
    receipt: AnyKtxToolReceipt,
) -> None:
    cache_key = _texture_cache_key(
        source_sha256=descriptor.source_sha256,
        role=descriptor.role,
        receipt=receipt,
        command_options=descriptor.command_options,
    )
    entry = KtxTextureCacheEntry(
        cache_key=cache_key,
        source_sha256=descriptor.source_sha256,
        role=descriptor.role,
        package_sha256=receipt.package_sha256,
        toktx_sha256=receipt.toktx.sha256,
        ktx_sha256=receipt.ktx.sha256,
        command_options=descriptor.command_options,
        descriptor=descriptor,
    )
    final_root = cache_root / cache_key
    if final_root.exists():
        raw = _read_stable_bytes(
            final_root / KTX_TEXTURE_CACHE_MANIFEST,
            maximum_bytes=1024 * 1024,
            label="existing KTX2 texture cache descriptor",
        )
        if raw != canonical_ktx_texture_cache_entry_bytes(entry):
            raise KtxToolchainError("existing KTX2 texture cache entry conflicts")
        existing = _read_stable_bytes(
            final_root / f"{descriptor.sha256}.ktx2",
            maximum_bytes=KTX_MAX_BYTES,
            label="existing KTX2 texture cache object",
        )
        if existing != payload:
            raise KtxToolchainError("existing KTX2 texture cache object conflicts")
        return
    with tempfile.TemporaryDirectory(
        prefix=".ktx-cache-entry.",
        dir=cache_root,
    ) as temporary:
        staging = Path(temporary) / "entry"
        staging.mkdir()
        object_path = staging / f"{descriptor.sha256}.ktx2"
        object_path.write_bytes(payload)
        (staging / KTX_TEXTURE_CACHE_MANIFEST).write_bytes(
            canonical_ktx_texture_cache_entry_bytes(entry),
        )
        os.rename(staging, final_root)


def compile_verified_ktx2_texture(
    source: Path,
    *,
    role: TextureRole,
    tool_root: Path,
    receipt: AnyKtxToolReceipt,
    output_root: Path,
    cache_root: Path | None = None,
    runner: ProcessRunner = _default_compile_runner,
) -> KtxTextureDescriptor:
    """Compile twice, validate, quality-check, and publish one KTX2 object."""

    source = Path(source).expanduser().absolute()
    tool_root = Path(tool_root).expanduser().absolute()
    output_root = Path(output_root).expanduser().absolute()
    source_sha256, _ = _sha256_file(source)
    try:
        output_root = _prepare_real_directory(
            output_root,
            label="KTX2 texture publication root",
        )
    except H3MaterialSourceError as exc:
        raise KtxToolchainError(
            f"KTX2 publication root cannot be trusted: {exc}",
        ) from exc
    prepared_cache_root = None
    if cache_root is not None:
        try:
            prepared_cache_root = _prepare_real_directory(
                Path(cache_root).expanduser().absolute(),
                label="KTX2 texture cache root",
            )
        except H3MaterialSourceError as exc:
            raise KtxToolchainError(
                f"KTX2 texture cache root cannot be trusted: {exc}",
            ) from exc
    environment = _runtime_environment(tool_root, receipt)
    if prepared_cache_root is not None:
        option_sets = [_expected_command_options(role, force_uastc=False)]
        if role == "orm":
            option_sets.append(_expected_command_options(role, force_uastc=True))
        for command_options in option_sets:
            cache_key = _texture_cache_key(
                source_sha256=source_sha256,
                role=role,
                receipt=receipt,
                command_options=command_options,
            )
            entry_root = prepared_cache_root / cache_key
            if not entry_root.exists():
                continue
            descriptor, payload = _load_verified_texture_cache_entry(
                entry_root,
                source=source,
                source_sha256=source_sha256,
                role=role,
                tool_root=tool_root,
                receipt=receipt,
                environment=environment,
                runner=runner,
            )
            _publish_ktx2_object(
                output_root,
                descriptor=descriptor,
                payload=payload,
            )
            return descriptor
    with tempfile.TemporaryDirectory(
        prefix=".ktx-texture.",
        dir=output_root,
    ) as temporary:
        temporary_root = Path(temporary)
        first_root = temporary_root / "repeat-1"
        second_root = temporary_root / "repeat-2"
        first_root.mkdir()
        second_root.mkdir()
        first = _compile_texture_attempt(
            source,
            role=role,
            force_uastc=False,
            work_root=first_root,
            tool_root=tool_root,
            receipt=receipt,
            environment=environment,
            runner=runner,
        )
        second = _compile_texture_attempt(
            source,
            role=role,
            force_uastc=first[4],
            work_root=second_root,
            tool_root=tool_root,
            receipt=receipt,
            environment=environment,
            runner=runner,
        )
        if first[:4] != second[:4]:
            raise KtxToolchainError(
                f"{role} repeat compilation is not byte-identical",
            )
        payload, audit, quality, options, fallback = first
        object_path = f"objects/{audit.sha256}.ktx2"
    descriptor = KtxTextureDescriptor(
        role=role,
        source_sha256=source_sha256,
        object_path=object_path,
        sha256=audit.sha256,
        bytes=audit.bytes,
        transfer=audit.transfer,
        codec=audit.codec,
        toktx_sha256=receipt.toktx.sha256,
        command_options=options,
        orm_etc1s_fallback=fallback,
        quality=quality,
    )
    _publish_ktx2_object(
        output_root,
        descriptor=descriptor,
        payload=payload,
    )
    if prepared_cache_root is not None:
        _publish_texture_cache_entry(
            prepared_cache_root,
            descriptor=descriptor,
            payload=payload,
            receipt=receipt,
        )
    return descriptor


def _pack_descriptors(
    pack: H3Ktx2Pack,
) -> tuple[KtxTextureDescriptor, ...]:
    return tuple(
        descriptor
        for record in pack.records
        for descriptor in (record.base_color, record.normal, record.orm)
    )


def _directory_closure(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*")),
    )


def load_h3_ktx2_pack(root: Path) -> H3Ktx2Pack:
    root = Path(root).expanduser().absolute()
    try:
        _require_real_directory(root, label="H3 KTX2 pack")
        _require_real_directory(root / "objects", label="H3 KTX2 objects")
        raw = _read_stable_bytes(
            root / H3_KTX2_PACK_MANIFEST,
            maximum_bytes=4 * 1024 * 1024,
            label="H3 KTX2 manifest",
        )
        pack = H3Ktx2Pack.model_validate_json(raw)
        if raw != canonical_h3_ktx2_pack_bytes(pack):
            raise KtxToolchainError("H3 KTX2 manifest is not canonical JSON")
        descriptors = _pack_descriptors(pack)
        expected = {
            H3_KTX2_PACK_MANIFEST,
            "objects",
            *(descriptor.object_path for descriptor in descriptors),
        }
        if _directory_closure(root) != tuple(sorted(expected)):
            raise KtxToolchainError(
                "H3 KTX2 pack directory closure disagrees with manifest",
            )
        verified: set[str] = set()
        for descriptor in descriptors:
            if descriptor.object_path in verified:
                continue
            verified.add(descriptor.object_path)
            payload = _read_stable_bytes(
                root / descriptor.object_path,
                maximum_bytes=KTX_MAX_BYTES,
                label=f"H3 KTX2 object {descriptor.sha256}",
            )
            if (
                len(payload) != descriptor.bytes
                or hashlib.sha256(payload).hexdigest() != descriptor.sha256
            ):
                raise KtxToolchainError("H3 KTX2 object identity disagrees")
            audit = audit_ktx2_bytes(
                payload,
                expected_transfer=descriptor.transfer,
                expected_codec=descriptor.codec,
            )
            if (
                audit.sha256 != descriptor.sha256
                or audit.bytes != descriptor.bytes
            ):
                raise KtxToolchainError("H3 KTX2 object audit disagrees")
        return pack
    except KtxToolchainError:
        raise
    except (H3MaterialSourceError, OSError, ValidationError, ValueError) as exc:
        raise KtxToolchainError(f"H3 KTX2 pack cannot be trusted: {exc}") from exc


TextureCompiler = Callable[..., KtxTextureDescriptor]


def compile_h3_ktx2_pack(
    authored_root: Path,
    output_root: Path,
    *,
    receipt_path: Path,
    texture_compiler: TextureCompiler = compile_verified_ktx2_texture,
    runner: ProcessRunner = _default_compile_runner,
) -> PreparedH3Ktx2Pack:
    """Compile and atomically publish the exact 8-slot, 24-texture H3 pack."""

    authored_root = Path(authored_root).expanduser().absolute()
    authored = load_h3_authored_material_pack(authored_root)
    receipt_path = Path(receipt_path).expanduser().absolute()
    receipt = load_ktx_tool_receipt(receipt_path)
    tool_root = receipt_path.parent
    try:
        publication_root = _prepare_real_directory(
            output_root,
            label="H3 KTX2 publication root",
        )
    except H3MaterialSourceError as exc:
        raise KtxToolchainError(
            f"H3 KTX2 publication root cannot be trusted: {exc}",
        ) from exc
    for candidate in sorted(publication_root.iterdir()):
        if not candidate.is_dir() or len(candidate.name) != 64:
            continue
        try:
            existing = load_h3_ktx2_pack(candidate)
        except KtxToolchainError:
            continue
        if (
            existing.source_pack_id == authored.source_pack_id
            and
            existing.authored_pack_id == authored.pack_id
            and existing.package_sha256 == receipt.package_sha256
            and existing.toktx_sha256 == receipt.toktx.sha256
            and existing.ktx_sha256 == receipt.ktx.sha256
        ):
            return PreparedH3Ktx2Pack(root=candidate, manifest=existing)
    try:
        cache_root = _prepare_real_directory(
            publication_root / ".texture-cache",
            label="H3 KTX2 texture cache root",
        )
    except H3MaterialSourceError as exc:
        raise KtxToolchainError(
            f"H3 KTX2 texture cache root cannot be trusted: {exc}",
        ) from exc
    with tempfile.TemporaryDirectory(
        prefix=".h3-ktx2-pack.",
        dir=publication_root,
    ) as temporary:
        temporary_root = Path(temporary)
        pack_root = temporary_root / "pack"
        inputs = temporary_root / "inputs"
        pack_root.mkdir()
        inputs.mkdir()
        records = []
        for source_record in authored.records:
            descriptors = {}
            for role in ("base_color", "normal", "orm"):
                payload = read_verified_h3_authored_map(
                    authored_root,
                    pack=authored,
                    slot_id=source_record.slot_id,
                    role=role,
                )
                source = inputs / f"{source_record.slot_id}-{role}.png"
                source.write_bytes(payload)
                descriptors[role] = texture_compiler(
                    source,
                    role=role,
                    tool_root=tool_root,
                    receipt=receipt,
                    output_root=pack_root,
                    cache_root=cache_root,
                    runner=runner,
                )
            records.append(
                H3Ktx2MaterialRecord(
                    slot_id=source_record.slot_id,
                    **descriptors,
                ),
            )
        identity = {
            "schema_version": H3_KTX2_PACK_SCHEMA,
            "source_pack_id": authored.source_pack_id,
            "authored_pack_id": authored.pack_id,
            "synthetic": True,
            "ai_generated": True,
            "real_photo_textures": False,
            "geometry_usability": "preview-only",
            "metric_alignment": False,
            "verification_level": "L0",
            "tool_version": KTX_TOOL_VERSION,
            "package_sha256": receipt.package_sha256,
            "toktx_sha256": receipt.toktx.sha256,
            "ktx_sha256": receipt.ktx.sha256,
            "records": tuple(records),
        }
        pack_id = hashlib.sha256(
            (
                json.dumps(
                    {
                        key: (
                            [item.model_dump(mode="json") for item in value]
                            if key == "records"
                            else value
                        )
                        for key, value in identity.items()
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8"),
        ).hexdigest()
        pack = H3Ktx2Pack(pack_id=pack_id, **identity)
        manifest = pack_root / H3_KTX2_PACK_MANIFEST
        manifest.write_bytes(canonical_h3_ktx2_pack_bytes(pack))
        if load_h3_ktx2_pack(pack_root) != pack:
            raise KtxToolchainError("staged H3 KTX2 pack verification drifted")
        final_root = publication_root / pack.pack_id
        if final_root.exists():
            verified = load_h3_ktx2_pack(final_root)
            if verified != pack:
                raise KtxToolchainError(
                    "existing H3 KTX2 identity has different evidence",
                )
            return PreparedH3Ktx2Pack(root=final_root, manifest=verified)
        os.rename(pack_root, final_root)
        verified = load_h3_ktx2_pack(final_root)
        if verified != pack:
            raise KtxToolchainError("published H3 KTX2 pack verification drifted")
        return PreparedH3Ktx2Pack(root=final_root, manifest=verified)


def _level_ranges(
    payload: bytes,
    *,
    level_count: int,
    supercompression: int,
) -> tuple[tuple[int, int], ...]:
    ranges = []
    for index in range(level_count):
        offset, length, uncompressed = struct.unpack_from(
            "<QQQ",
            payload,
            80 + index * 24,
        )
        if (
            length < 1
            or (
                uncompressed != 0
                if supercompression == KTX_SS_BASIS_LZ
                else uncompressed < 1
            )
            or offset < 80 + level_count * 24
            or offset > len(payload)
            or length > len(payload) - offset
        ):
            raise KtxToolchainError("KTX2 mip level range is invalid")
        ranges.append((offset, offset + length))
    ordered = sorted(ranges)
    if any(
        first[1] > second[0]
        for first, second in zip(ordered, ordered[1:], strict=False)
    ):
        raise KtxToolchainError("KTX2 mip level ranges overlap")
    return tuple(ranges)


def audit_ktx2_bytes(
    payload: bytes,
    *,
    expected_transfer: Transfer,
    expected_codec: Codec,
) -> KtxBinaryAudit:
    """Independently parse the fixed KTX2 header, level index, and basic DFD."""

    if len(payload) < 80 or len(payload) > KTX_MAX_BYTES:
        raise KtxToolchainError("KTX2 byte length is invalid")
    if payload[:12] != KTX2_MAGIC:
        raise KtxToolchainError("KTX2 identifier is invalid")
    (
        vk_format,
        type_size,
        width,
        height,
        depth,
        layer_count,
        face_count,
        level_count,
        supercompression,
        dfd_offset,
        dfd_length,
        kvd_offset,
        kvd_length,
        sgd_offset,
        sgd_length,
    ) = struct.unpack_from("<13I2Q", payload, 12)
    del kvd_offset, kvd_length, sgd_offset, sgd_length
    if width != 4096 or height != 4096:
        raise KtxToolchainError("KTX2 dimensions must be exactly 4096 by 4096")
    if level_count != len(KTX_LEVEL_DIMENSIONS):
        raise KtxToolchainError("KTX2 must contain exactly 13 mip levels")
    if (
        vk_format != 0
        or type_size != 1
        or depth != 0
        or layer_count != 0
        or face_count != 1
    ):
        raise KtxToolchainError("KTX2 universal 2D texture header is invalid")
    minimum_index_end = 80 + level_count * 24
    if (
        dfd_offset < minimum_index_end
        or dfd_length < 28
        or dfd_offset > len(payload)
        or dfd_length > len(payload) - dfd_offset
    ):
        raise KtxToolchainError("KTX2 DFD range is invalid")
    if struct.unpack_from("<I", payload, dfd_offset)[0] != dfd_length:
        raise KtxToolchainError("KTX2 DFD length disagrees with its header")
    colour_model = payload[dfd_offset + 12]
    transfer_value = payload[dfd_offset + 14]
    transfer_map = {
        KTX_DF_TRANSFER_LINEAR: "linear",
        KTX_DF_TRANSFER_SRGB: "srgb",
    }
    codec_map = {
        KTX_DF_MODEL_UASTC: ("uastc", KTX_SS_ZSTD),
        KTX_DF_MODEL_ETC1S: ("etc1s", KTX_SS_BASIS_LZ),
    }
    transfer = transfer_map.get(transfer_value)
    if transfer != expected_transfer:
        raise KtxToolchainError("KTX2 DFD transfer function is invalid")
    codec_evidence = codec_map.get(colour_model)
    if codec_evidence is None or codec_evidence[0] != expected_codec:
        raise KtxToolchainError("KTX2 DFD codec is invalid")
    if supercompression != codec_evidence[1]:
        raise KtxToolchainError("KTX2 codec and supercompression disagree")
    _level_ranges(
        payload,
        level_count=level_count,
        supercompression=supercompression,
    )
    return KtxBinaryAudit(
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        transfer=transfer,
        codec=codec_evidence[0],
    )


def _file_evidence(root: Path, relative_path: str) -> KtxToolFile:
    digest, size = _sha256_file(root / relative_path)
    return KtxToolFile(
        relative_path=relative_path,
        sha256=digest,
        bytes=size,
    )


def _binary_evidence(
    root: Path,
    relative_path: str,
    *,
    version_args: tuple[str, ...],
    environment: dict[str, str],
) -> KtxToolBinary:
    path = root / relative_path
    digest, size = _sha256_file(path)
    _require_success(
        ("codesign", "--verify", "--deep", "--strict", str(path)),
        label=f"{path.name} codesign verification",
    )
    probe = _require_success(
        (str(path), *version_args),
        environment=environment,
        label=f"{path.name} version probe",
    )
    output = (probe.stdout + probe.stderr).strip()
    if KTX_TOOL_VERSION not in output:
        raise KtxToolchainError(f"{path.name} version is not {KTX_TOOL_VERSION}")
    return KtxToolBinary(
        relative_path=relative_path,
        sha256=digest,
        bytes=size,
        version_output=output,
        codesign_valid=True,
    )


def _runtime_environment(
    root: Path,
    receipt: AnyKtxToolReceipt | None = None,
) -> dict[str, str]:
    if receipt is None or receipt.platform == "darwin-arm64":
        return {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(Path.home()),
            "LANG": "C",
            "LC_ALL": "C",
            "DYLD_LIBRARY_PATH": str((root / "runtime/lib").absolute()),
        }
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    temporary = os.environ.get("TEMP") or os.environ.get("TMP")
    if not system_root or not temporary:
        raise KtxToolchainError(
            "Windows KTX runtime requires measured SystemRoot and TEMP",
        )
    tool_bin = (root / "bin").absolute()
    system32 = Path(system_root) / "System32"
    return {
        "PATH": f"{tool_bin};{system32}",
        "SystemRoot": system_root,
        "TEMP": temporary,
        "TMP": temporary,
        "LANG": "C",
        "LC_ALL": "C",
    }


def _copy_private_runtime(expanded: Path, destination: Path) -> None:
    sources = {
        "runtime/bin/toktx": (
            "KTX-Software-4.4.2-Darwin-arm64-tools.pkg/"
            "Payload/usr/local/bin/toktx"
        ),
        "runtime/bin/ktx": (
            "KTX-Software-4.4.2-Darwin-arm64-tools.pkg/"
            "Payload/usr/local/bin/ktx"
        ),
        "runtime/lib/libktx.4.4.2.dylib": (
            "KTX-Software-4.4.2-Darwin-arm64-library.pkg/"
            "Payload/usr/local/lib/libktx.4.4.2.dylib"
        ),
        "runtime/licenses/License.rtf": "Resources/License.rtf",
    }
    for relative, source_relative in sources.items():
        source = expanded / source_relative
        if not source.is_file() or source.is_symlink():
            raise KtxToolchainError(
                f"signed KTX package member is absent: {source_relative}",
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    library_dir = destination / "runtime/lib"
    os.symlink("libktx.4.4.2.dylib", library_dir / "libktx.4.dylib")
    os.symlink("libktx.4.dylib", library_dir / "libktx.dylib")


def _require_same_private_runtime(first: Path, second: Path) -> None:
    files = (
        "runtime/bin/toktx",
        "runtime/bin/ktx",
        "runtime/lib/libktx.4.4.2.dylib",
        "runtime/licenses/License.rtf",
    )
    for relative in files:
        first_digest, first_size = _sha256_file(first / relative)
        second_digest, second_size = _sha256_file(second / relative)
        if (first_digest, first_size) != (second_digest, second_size):
            raise KtxToolchainError(
                f"existing KTX runtime disagrees with signed package: {relative}",
            )
    links = (
        "runtime/lib/libktx.4.dylib",
        "runtime/lib/libktx.dylib",
    )
    for relative in links:
        first_link = first / relative
        second_link = second / relative
        if (
            not first_link.is_symlink()
            or not second_link.is_symlink()
            or os.readlink(first_link) != os.readlink(second_link)
        ):
            raise KtxToolchainError(
                f"existing KTX runtime link disagrees with signed package: {relative}",
            )


WindowsSignatureRunner = Callable[[Path], dict[str, str]]


def _powershell_authenticode_signature(path: Path) -> dict[str, str]:
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise KtxToolchainError(
            "SystemRoot is required for Authenticode verification"
        )
    powershell = (
        Path(system_root)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    script = (
        "$signature = Get-AuthenticodeSignature -LiteralPath $env:NANTAI_AUTHENTICODE_PATH; "
        "[ordered]@{status=[string]$signature.Status; "
        "signer_subject=[string]$signature.SignerCertificate.Subject; "
        "signer_thumbprint=[string]$signature.SignerCertificate.Thumbprint} "
        "| ConvertTo-Json -Compress"
    )
    result = _require_success(
        (
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ),
        environment={
            **os.environ,
            "NANTAI_AUTHENTICODE_PATH": str(path),
        },
        label=f"{path.name} Authenticode verification",
    )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise KtxToolchainError(
            f"{path.name} Authenticode evidence is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise KtxToolchainError(
            f"{path.name} Authenticode evidence root must be an object"
        )
    return payload


def _validate_windows_signature(
    path: Path,
    signature_runner: WindowsSignatureRunner,
) -> None:
    payload = signature_runner(path)
    if not isinstance(payload, dict) or set(payload) != {
        "status",
        "signer_subject",
        "signer_thumbprint",
    }:
        raise KtxToolchainError(
            f"{path.name} Authenticode evidence fields are not exact"
        )
    if payload["status"] != "Valid":
        raise KtxToolchainError(
            f"{path.name} Authenticode status is not Valid"
        )
    if payload["signer_subject"] != KTX_WINDOWS_SIGNER_SUBJECT:
        raise KtxToolchainError(
            f"{path.name} Authenticode signer is not trusted"
        )
    if payload["signer_thumbprint"] != KTX_WINDOWS_SIGNER_THUMBPRINT:
        raise KtxToolchainError(
            f"{path.name} Authenticode certificate thumbprint is not trusted"
        )


def _windows_runtime_file(root: Path, relative_path: str) -> Path:
    path = root / relative_path
    if path.is_symlink() or not path.is_file():
        raise KtxToolchainError(
            f"Windows KTX runtime file is missing: {relative_path}"
        )
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise KtxToolchainError(
            f"Windows KTX runtime path escapes root: {relative_path}"
        ) from exc
    return path


def _windows_binary_evidence(
    root: Path,
    relative_path: str,
    *,
    version_args: tuple[str, ...] | None,
    signature_runner: WindowsSignatureRunner,
) -> WindowsKtxToolBinary:
    path = _windows_runtime_file(root, relative_path)
    before = _sha256_file(path)
    _validate_windows_signature(path, signature_runner)
    version_output = None
    if version_args is not None:
        probe = _require_success(
            (str(path), *version_args),
            environment=dict(os.environ),
            label=f"{path.name} version probe",
        )
        version_output = (probe.stdout + probe.stderr).strip()
        if KTX_TOOL_VERSION not in version_output:
            raise KtxToolchainError(
                f"{path.name} version is not {KTX_TOOL_VERSION}"
            )
    after = _sha256_file(path)
    if before != after:
        raise KtxToolchainError(f"{path.name} changed during verification")
    return WindowsKtxToolBinary(
        relative_path=relative_path,
        sha256=before[0],
        bytes=before[1],
        version_output=version_output,
    )


def prepare_private_windows_ktx_runtime(
    package: Path,
    installed_root: Path,
    *,
    signature_runner: WindowsSignatureRunner = _powershell_authenticode_signature,
) -> WindowsKtxToolReceipt:
    """Adopt and verify the signed project-private Windows x64 runtime."""

    package = Path(package).expanduser().absolute()
    installed_root = Path(installed_root).expanduser().absolute()
    package_before = _sha256_file(package)
    if package_before[0] != KTX_WINDOWS_X64_SHA256:
        raise KtxToolchainError(
            "KTX package SHA-256 is not the approved Windows pin"
        )
    _validate_windows_signature(package, signature_runner)
    if _sha256_file(package) != package_before:
        raise KtxToolchainError(
            "KTX Windows package changed during verification"
        )

    downloads = installed_root / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    copied_package = downloads / KTX_WINDOWS_X64_ASSET
    if copied_package.exists():
        copied_evidence = _sha256_file(copied_package)
        if copied_evidence != package_before:
            raise KtxToolchainError(
                "existing copied KTX Windows package disagrees with "
                "approved package"
            )
    else:
        temporary = downloads / f".{KTX_WINDOWS_X64_ASSET}.partial"
        if temporary.exists():
            raise KtxToolchainError(
                "stale KTX Windows package staging file exists"
            )
        shutil.copyfile(package, temporary)
        if _sha256_file(temporary) != package_before:
            raise KtxToolchainError(
                "copied KTX Windows package bytes changed"
            )
        os.replace(temporary, copied_package)
    _validate_windows_signature(copied_package, signature_runner)

    receipt = WindowsKtxToolReceipt(
        package_file=_file_evidence(
            installed_root,
            f"downloads/{KTX_WINDOWS_X64_ASSET}",
        ),
        toktx=_windows_binary_evidence(
            installed_root,
            "bin/toktx.exe",
            version_args=("--version",),
            signature_runner=signature_runner,
        ),
        ktx=_windows_binary_evidence(
            installed_root,
            "bin/ktx.exe",
            version_args=("--version",),
            signature_runner=signature_runner,
        ),
        library=_windows_binary_evidence(
            installed_root,
            "bin/ktx.dll",
            version_args=None,
            signature_runner=signature_runner,
        ),
        license=_file_evidence(
            installed_root,
            "share/doc/KTX-Software/html/license.html",
        ),
    )
    receipt_path = installed_root / KTX_RECEIPT_NAME
    canonical = canonical_ktx_tool_receipt_bytes(receipt)
    if receipt_path.exists():
        if receipt_path.read_bytes() != canonical:
            raise KtxToolchainError(
                "existing Windows KTX receipt disagrees with runtime"
            )
    else:
        with receipt_path.open("xb") as stream:
            stream.write(canonical)
            stream.flush()
            os.fsync(stream.fileno())
    return receipt


def prepare_private_ktx_runtime(
    package: Path,
    output_root: Path,
) -> KtxToolReceipt:
    """Verify a signed package, extract a private runtime, and write a receipt."""

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise KtxToolchainError("KTX 4.4.2 private install requires Darwin arm64")
    package = Path(package).expanduser().absolute()
    output_root = Path(output_root).expanduser().absolute()
    digest, _ = _sha256_file(package)
    if digest != KTX_DARWIN_ARM64_SHA256:
        raise KtxToolchainError("KTX package SHA-256 is not the approved pin")
    signature = _require_success(
        ("pkgutil", "--check-signature", str(package)),
        label="KTX package signature verification",
    )
    signature_output = signature.stdout + signature.stderr
    if (
        "Status: signed by a developer certificate issued by Apple" not in (
            signature_output
        )
        or "Notarization: trusted by the Apple notary service" not in (
            signature_output
        )
        or KTX_PACKAGE_SIGNER not in signature_output
    ):
        raise KtxToolchainError("KTX package signature evidence is not trusted")
    output_root.mkdir(parents=True, exist_ok=True)
    runtime = output_root / "runtime"
    with tempfile.TemporaryDirectory(
        prefix=".ktx-expand.",
        dir=output_root,
    ) as temporary:
        temporary_root = Path(temporary)
        expanded = temporary_root / "expanded"
        _require_success(
            (
                "pkgutil",
                "--expand-full",
                str(package),
                str(expanded),
            ),
            label="KTX package expansion",
        )
        staged = temporary_root / "staged"
        _copy_private_runtime(expanded, staged)
        if runtime.exists():
            _require_same_private_runtime(output_root, staged)
        else:
            os.rename(staged / "runtime", runtime)
    environment = _runtime_environment(output_root)
    receipt = KtxToolReceipt(
        package_file=_file_evidence(
            output_root,
            f"downloads/{KTX_DARWIN_ARM64_ASSET}",
        ),
        toktx=_binary_evidence(
            output_root,
            "runtime/bin/toktx",
            version_args=("--version",),
            environment=environment,
        ),
        ktx=_binary_evidence(
            output_root,
            "runtime/bin/ktx",
            version_args=("--version",),
            environment=environment,
        ),
        library=_file_evidence(
            output_root,
            "runtime/lib/libktx.4.4.2.dylib",
        ),
        license=_file_evidence(
            output_root,
            "runtime/licenses/License.rtf",
        ),
    )
    receipt_path = output_root / KTX_RECEIPT_NAME
    canonical = canonical_ktx_tool_receipt_bytes(receipt)
    if receipt_path.exists():
        if receipt_path.read_bytes() != canonical:
            raise KtxToolchainError("existing KTX receipt disagrees with runtime")
    else:
        with receipt_path.open("xb") as stream:
            stream.write(canonical)
            stream.flush()
            os.fsync(stream.fileno())
    return receipt


def load_ktx_tool_receipt(
    path: Path,
    *,
    windows_signature_runner: WindowsSignatureRunner = (
        _powershell_authenticode_signature
    ),
) -> AnyKtxToolReceipt:
    path = Path(path).expanduser().absolute()
    try:
        raw = path.read_bytes()
        if len(raw) < 1 or len(raw) > KTX_MAX_PROCESS_OUTPUT:
            raise KtxToolchainError("KTX receipt byte length is invalid")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise KtxToolchainError("KTX receipt root must be an object")
        receipt_platform = payload.get("platform")
        if receipt_platform == "darwin-arm64":
            receipt = KtxToolReceipt.model_validate_json(raw)
        elif receipt_platform == "windows-x64":
            receipt = WindowsKtxToolReceipt.model_validate_json(raw)
        else:
            raise KtxToolchainError(
                f"unsupported KTX receipt platform: {receipt_platform}"
            )
    except KtxToolchainError:
        raise
    except (OSError, ValidationError, ValueError) as exc:
        raise KtxToolchainError(f"KTX receipt cannot be trusted: {exc}") from exc
    if raw != canonical_ktx_tool_receipt_bytes(receipt):
        raise KtxToolchainError("KTX receipt is not canonical JSON")
    if isinstance(receipt, WindowsKtxToolReceipt):
        return prepare_private_windows_ktx_runtime(
            path.parent / receipt.package_file.relative_path,
            path.parent,
            signature_runner=windows_signature_runner,
        )
    root = path.parent
    environment = _runtime_environment(root, receipt)
    actual_toktx = _binary_evidence(
        root,
        receipt.toktx.relative_path,
        version_args=("--version",),
        environment=environment,
    )
    actual_ktx = _binary_evidence(
        root,
        receipt.ktx.relative_path,
        version_args=("--version",),
        environment=environment,
    )
    actual_library = _file_evidence(root, receipt.library.relative_path)
    actual_license = _file_evidence(root, receipt.license.relative_path)
    actual_package = _file_evidence(root, receipt.package_file.relative_path)
    if (
        actual_package != receipt.package_file
        or actual_toktx != receipt.toktx
        or actual_ktx != receipt.ktx
        or actual_library != receipt.library
        or actual_license != receipt.license
    ):
        raise KtxToolchainError("KTX receipt disagrees with runtime bytes")
    return receipt
