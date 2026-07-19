"""Pinned KTX 4.4.2 evidence and independent KTX2 structure auditing.

The official validator is necessary but not sufficient: runtime bundles also
pass the small parser in this module so dimensions, mip closure, transfer
function, and universal codec cannot be inferred from filenames or commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
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
KTX_RECEIPT_NAME = "receipt.json"
KTX_MAX_BYTES = 512 * 1024 * 1024
KTX_MAX_PROCESS_OUTPUT = 1024 * 1024
KTX_PROCESS_TIMEOUT_SECONDS = 120
KTX_DF_MODEL_ETC1S = 163
KTX_DF_MODEL_UASTC = 166
KTX_DF_TRANSFER_LINEAR = 1
KTX_DF_TRANSFER_SRGB = 2
KTX_SS_BASIS_LZ = 1
KTX_SS_ZSTD = 2
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


def canonical_ktx_tool_receipt_bytes(receipt: KtxToolReceipt) -> bytes:
    return _canonical_json_bytes(receipt)


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
        str(output),
        str(source),
    )


def _level_ranges(
    payload: bytes,
    *,
    level_count: int,
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
            or uncompressed < 1
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
    _level_ranges(payload, level_count=level_count)
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


def _runtime_environment(root: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(Path.home()),
        "LANG": "C",
        "LC_ALL": "C",
        "DYLD_LIBRARY_PATH": str((root / "runtime/lib").absolute()),
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


def load_ktx_tool_receipt(path: Path) -> KtxToolReceipt:
    path = Path(path).expanduser().absolute()
    try:
        raw = path.read_bytes()
        if len(raw) < 1 or len(raw) > KTX_MAX_PROCESS_OUTPUT:
            raise KtxToolchainError("KTX receipt byte length is invalid")
        receipt = KtxToolReceipt.model_validate_json(raw)
    except KtxToolchainError:
        raise
    except (OSError, ValidationError, ValueError) as exc:
        raise KtxToolchainError(f"KTX receipt cannot be trusted: {exc}") from exc
    if raw != canonical_ktx_tool_receipt_bytes(receipt):
        raise KtxToolchainError("KTX receipt is not canonical JSON")
    root = path.parent
    environment = _runtime_environment(root)
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
