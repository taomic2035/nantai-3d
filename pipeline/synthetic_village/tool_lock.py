"""Strict, absent-only installation of the pinned synthetic-village toolchain."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
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

from pipeline.studio_jobs import (
    JobContractError,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
)

ROOT = Path(__file__).resolve().parents[2]
TOOL_LOCK_PATH = ROOT / "tools.lock.json"
DEFAULT_TOOL_CACHE = ROOT / ".nantai-studio/cache/tools"
RECEIPT_NAME = ".nantai-tool.json"
MAX_LOCK_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_MEMBER_BYTES = 4 * 1024 * 1024 * 1024
MAX_UNPACKED_BYTES = 16 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 10_000
MAX_RUNTIME_OUTPUT_BYTES = 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
RUNTIME_TIMEOUT_SECONDS = 120
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RuntimeVerifier = Callable[["LockedTool", Path], str]
WINDOWS_RESERVED_NAMES = frozenset({
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})


class ToolLockError(ValueError):
    """The tracked tool lock is malformed or cannot be trusted."""


class ToolInstallError(RuntimeError):
    """A locked tool cannot be verified or published safely."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _unsafe_windows_component(part: str) -> bool:
    device_stem = part.split(".", 1)[0].casefold()
    return (
        part.rstrip(" .") != part
        or device_stem in WINDOWS_RESERVED_NAMES
        or any(ord(character) < 32 or character in '<>:"|?*' for character in part)
    )


def _portable_relative_path(value: str, *, one_component: bool = False) -> str:
    parsed = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or parsed.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or parsed.as_posix() != value
        or any(
            part in {"", ".", ".."} or _unsafe_windows_component(part)
            for part in parsed.parts
        )
        or (one_component and len(parsed.parts) != 1)
    ):
        raise ValueError("path must be a portable relative POSIX path")
    return value


class LockedTool(FrozenModel):
    tool_id: Literal["blender"]
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    channel: Literal["LTS"]
    platform: Literal["windows-x64"]
    archive_type: Literal["zip"]
    download_url: str = Field(min_length=1)
    archive_sha256: Sha256
    archive_root: str = Field(min_length=1)
    executable: str = Field(min_length=1)
    install_dir: str = Field(min_length=1)
    runtime_build_hash: str = Field(pattern=r"^[0-9a-f]{12,64}$")
    runtime_build_timestamp: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
    )
    version_output_prefix: str = Field(min_length=1)

    @field_validator("download_url")
    @classmethod
    def _https_url_without_ambient_authority(cls, value: str) -> str:
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("download_url must be a credential-free HTTPS URL")
        return value

    @field_validator("archive_root", "executable")
    @classmethod
    def _single_component_path(cls, value: str) -> str:
        return _portable_relative_path(value, one_component=True)

    @field_validator("install_dir")
    @classmethod
    def _portable_install_path(cls, value: str) -> str:
        return _portable_relative_path(value)


class ToolLock(FrozenModel):
    schema_version: Literal[1] = 1
    blender: LockedTool

    @model_validator(mode="after")
    def _exact_blender_pin(self) -> ToolLock:
        expected = {
            "tool_id": "blender",
            "version": "4.5.11",
            "channel": "LTS",
            "platform": "windows-x64",
            "archive_type": "zip",
            "download_url": (
                "https://download.blender.org/release/Blender4.5/"
                "blender-4.5.11-windows-x64.zip"
            ),
            "archive_sha256": (
                "e11d3a8e4d4249be5a7db4a9325c1f670037d4233467c3b0bda181001efe44d3"
            ),
            "archive_root": "blender-4.5.11-windows-x64",
            "executable": "blender.exe",
            "install_dir": "third/blender",
            "runtime_build_hash": "4db51e9d1e1e",
            "runtime_build_timestamp": "2026-06-23 01:33:52",
            "version_output_prefix": "Blender 4.5.11 LTS",
        }
        if self.blender.model_dump(mode="json") != expected:
            raise ValueError("Blender lock does not match the approved exact pin")
        return self


class ToolInstallReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    tool_id: Literal["blender"]
    version: str
    platform: Literal["windows-x64"]
    archive_sha256: Sha256
    executable: str
    executable_sha256: Sha256
    runtime_output: str = Field(min_length=1, max_length=MAX_RUNTIME_OUTPUT_BYTES)


def _canonical_model_bytes(model: BaseModel) -> bytes:
    text = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


def canonical_tool_lock_bytes(lock: ToolLock) -> bytes:
    return _canonical_model_bytes(lock)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ToolLockError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_bounded_json(path: Path, *, maximum_bytes: int) -> tuple[bytes, object]:
    try:
        expected_size = path.stat().st_size
        if expected_size <= 0 or expected_size > maximum_bytes:
            raise ToolLockError(f"JSON input size is invalid: {path.name}")
        with path.open("rb") as stream:
            raw = stream.read(maximum_bytes + 1)
    except OSError as exc:
        raise ToolLockError(f"cannot read JSON input: {path.name}") from exc
    if len(raw) != expected_size or len(raw) > maximum_bytes:
        raise ToolLockError(f"JSON input changed during bounded read: {path.name}")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolLockError(f"invalid UTF-8 JSON input: {path.name}") from exc
    return raw, payload


def load_tool_lock(path: Path = TOOL_LOCK_PATH) -> ToolLock:
    raw, payload = _read_bounded_json(Path(path), maximum_bytes=MAX_LOCK_BYTES)
    try:
        lock = ToolLock.model_validate(payload)
    except ValidationError as exc:
        raise ToolLockError(f"tool lock validation failed: {exc}") from exc
    if raw != canonical_tool_lock_bytes(lock):
        raise ToolLockError("tool lock must be canonical JSON")
    return lock


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _require_real_directory(path: Path, *, label: str) -> Path:
    path = Path(path).absolute()
    if _is_linklike(path):
        raise ToolInstallError(f"{label} must not be a symlink or junction")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ToolInstallError(f"{label} must be an existing real directory") from exc
    if not path.is_dir() or not _same_path(resolved, path):
        raise ToolInstallError(f"{label} must be a real directory without redirected ancestors")
    return path


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


def _prepare_real_directory(raw_path: Path) -> Path:
    path = Path(raw_path).absolute()
    cursor = path
    missing: list[Path] = []
    while not cursor.exists():
        if _is_linklike(cursor):
            raise ToolInstallError("directory ancestor must not be a symlink or junction")
        missing.append(cursor)
        if cursor.parent == cursor:
            raise ToolInstallError("directory has no real existing ancestor")
        cursor = cursor.parent
    _require_real_directory(cursor, label="directory ancestor")
    for directory in reversed(missing):
        try:
            directory.mkdir(exist_ok=False)
            _flush_directory(directory.parent)
        except FileExistsError:
            pass
        _require_real_directory(directory, label="created directory")
    return _require_real_directory(path, label="directory")


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
    )


def _hash_open_stream(stream, *, maximum_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    for chunk in iter(lambda: stream.read(DOWNLOAD_CHUNK_BYTES), b""):
        total += len(chunk)
        if total > maximum_bytes:
            raise ToolInstallError("archive exceeds the byte limit")
        digest.update(chunk)
    return digest.hexdigest(), total


def _archive_member_path(info: zipfile.ZipInfo, tool: LockedTool) -> PurePosixPath:
    name = info.filename
    raw_parts = name.split("/")
    if info.is_dir() and raw_parts and raw_parts[-1] == "":
        raw_parts.pop()
    windows = PureWindowsPath(name)
    if (
        not raw_parts
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or windows.is_absolute()
        or bool(windows.drive)
        or any(
            part in {"", ".", ".."} or _unsafe_windows_component(part)
            for part in raw_parts
        )
        or raw_parts[0] != tool.archive_root
    ):
        raise ToolInstallError(f"unsafe archive path: {name!r}")
    return PurePosixPath(*raw_parts)


def _validated_members(
    archive: zipfile.ZipFile,
    tool: LockedTool,
) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    members = archive.infolist()
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise ToolInstallError("archive member count is invalid")
    validated: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    entries: dict[str, bool] = {}
    unpacked = 0
    for info in members:
        relative = _archive_member_path(info, tool)
        key = relative.as_posix().casefold()
        if key in entries:
            raise ToolInstallError(f"duplicate archive path after normalization: {info.filename}")
        if info.flag_bits & 0x1:
            raise ToolInstallError(f"encrypted archive member is forbidden: {info.filename}")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ToolInstallError(f"unsupported archive compression: {info.filename}")
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        is_directory = info.is_dir()
        allowed_type = {0, stat.S_IFDIR} if is_directory else {0, stat.S_IFREG}
        if file_type not in allowed_type or (info.external_attr & 0x400):
            raise ToolInstallError(f"archive member must be a regular file: {info.filename}")
        if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
            raise ToolInstallError(f"archive member exceeds the byte limit: {info.filename}")
        unpacked += info.file_size
        if unpacked > MAX_UNPACKED_BYTES:
            raise ToolInstallError("archive exceeds the unpacked byte limit")
        if (
            (info.file_size > 0 and info.compress_size == 0)
            or (
                info.compress_size > 0
                and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
            )
        ):
            raise ToolInstallError(f"archive compression ratio is unsafe: {info.filename}")
        for parent in relative.parents:
            if parent == PurePosixPath("."):
                continue
            parent_entry = entries.get(parent.as_posix().casefold())
            if parent_entry is False:
                raise ToolInstallError(f"archive path crosses a file: {info.filename}")
        if not is_directory and any(
            existing.startswith(f"{key}/") for existing in entries
        ):
            raise ToolInstallError(f"archive file conflicts with a directory: {info.filename}")
        entries[key] = is_directory
        validated.append((info, relative))
    expected_executable = f"{tool.archive_root}/{tool.executable}".casefold()
    if entries.get(expected_executable) is not False:
        raise ToolInstallError(f"archive is missing {tool.executable}")
    reserved_receipt = f"{tool.archive_root}/{RECEIPT_NAME}".casefold()
    if reserved_receipt in entries:
        raise ToolInstallError(f"archive contains reserved path: {RECEIPT_NAME}")
    return validated


def _extract_members(
    archive: zipfile.ZipFile,
    members: list[tuple[zipfile.ZipInfo, PurePosixPath]],
    staging: Path,
) -> None:
    total = 0
    for info, relative in members:
        destination = staging.joinpath(*relative.parts)
        if info.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with archive.open(info, "r") as input_stream, destination.open("xb") as output:
                while True:
                    chunk = input_stream.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    total += len(chunk)
                    if written > info.file_size or total > MAX_UNPACKED_BYTES:
                        raise ToolInstallError(
                            f"archive member expanded beyond its declaration: {info.filename}",
                        )
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        except (OSError, EOFError, zipfile.BadZipFile) as exc:
            raise ToolInstallError(f"cannot extract archive member: {info.filename}") from exc
        if written != info.file_size:
            raise ToolInstallError(f"archive member size mismatch: {info.filename}")


def _flush_extracted_tree(staging: Path) -> None:
    directories: list[Path] = []
    for root, child_directories, filenames in os.walk(
        staging,
        topdown=True,
        followlinks=False,
    ):
        directory = Path(root)
        if _is_linklike(directory) or not directory.is_dir():
            raise ToolInstallError("extracted tree contains a redirected directory")
        directories.append(directory)
        for name in child_directories:
            child = directory / name
            if _is_linklike(child) or not child.is_dir():
                raise ToolInstallError("extracted tree contains a redirected directory")
        for name in filenames:
            child = directory / name
            if _is_linklike(child) or not child.is_file():
                raise ToolInstallError("extracted tree contains a non-regular file")
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        _flush_directory(directory)


def _validate_runtime_output(tool: LockedTool, output: str) -> str:
    normalized = output.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if not normalized or len(normalized.encode("utf-8")) > MAX_RUNTIME_OUTPUT_BYTES:
        raise ToolInstallError("Blender runtime output is empty or exceeds the byte limit")
    lines = normalized.split("\n")
    expected_first_line = (
        f"{tool.version_output_prefix} (hash {tool.runtime_build_hash} "
        f"built {tool.runtime_build_timestamp})"
    )
    if (
        len(lines) < 2
        or lines[0] != expected_first_line
        or lines[1] != tool.version_output_prefix
    ):
        actual = lines[0] if lines else ""
        raise ToolInstallError(
            "Blender runtime identity mismatch; expected exact first line "
            f"{expected_first_line!r}, got {actual!r}",
        )
    return normalized


def run_blender_version(tool: LockedTool, install_root: Path) -> str:
    install_root = _require_real_directory(install_root, label="Blender install")
    executable = install_root / tool.executable
    if _is_linklike(executable) or not executable.is_file():
        raise ToolInstallError("Blender executable must be a regular non-link file")
    before = executable.stat()
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    argv = [
        str(executable),
        "--background",
        "--factory-startup",
        "--version",
    ]
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                argv,
                cwd=install_root,
                env=environment,
                shell=False,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise ToolInstallError("Blender runtime identity check could not start") from exc
        try:
            returncode = process.wait(timeout=RUNTIME_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise ToolInstallError("Blender runtime identity check timed out") from exc
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_bytes = stdout_file.read(MAX_RUNTIME_OUTPUT_BYTES + 1)
        stderr_bytes = stderr_file.read(MAX_RUNTIME_OUTPUT_BYTES + 1)
    if (
        len(stdout_bytes) > MAX_RUNTIME_OUTPUT_BYTES
        or len(stderr_bytes) > MAX_RUNTIME_OUTPUT_BYTES
    ):
        raise ToolInstallError("Blender runtime output exceeds the byte limit")
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    after = executable.stat()
    if _stat_signature(before) != _stat_signature(after):
        raise ToolInstallError("Blender executable changed during identity verification")
    if returncode != 0:
        detail = (stderr or stdout)[:4096].strip()
        raise ToolInstallError(
            f"Blender runtime identity check failed with {returncode}: {detail}",
        )
    return _validate_runtime_output(tool, stdout)


def _write_receipt(path: Path, receipt: ToolInstallReceipt) -> None:
    payload = _canonical_model_bytes(receipt)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _load_receipt(path: Path) -> ToolInstallReceipt:
    try:
        raw, payload = _read_bounded_json(path, maximum_bytes=MAX_RECEIPT_BYTES)
    except ToolLockError as exc:
        raise ToolInstallError(f"tool receipt cannot be trusted: {exc}") from exc
    try:
        receipt = ToolInstallReceipt.model_validate(payload)
    except ValidationError as exc:
        raise ToolInstallError(f"tool receipt validation failed: {exc}") from exc
    if raw != _canonical_model_bytes(receipt):
        raise ToolInstallError("tool receipt must be canonical JSON")
    return receipt


def _move_directory_noreplace(source: Path, destination: Path) -> None:
    if destination.exists() or _is_linklike(destination):
        raise ToolInstallError(f"install destination already exists: {destination.name}")
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
            _flush_directory(destination.parent)
        else:  # best-effort fallback for non-target development hosts
            if destination.exists() or _is_linklike(destination):
                raise FileExistsError(errno.EEXIST, "destination exists", destination)
            os.rename(source, destination)
            _flush_directory(destination.parent)
    except (JobContractError, OSError) as exc:
        if destination.exists() or _is_linklike(destination):
            raise ToolInstallError(
                f"install destination already exists: {destination.name}",
            ) from exc
        raise ToolInstallError(f"cannot publish locked tool: {exc}") from exc


def _cleanup_owned_staging(staging: Path, expected_parent: Path) -> None:
    if not staging.exists() and not _is_linklike(staging):
        return
    if (
        staging.parent != expected_parent
        or not staging.name.startswith(".blender-staging-")
        or _is_linklike(staging)
        or not staging.is_dir()
    ):
        return
    try:
        shutil.rmtree(staging)
        _flush_directory(expected_parent)
    except OSError:
        # A verified owned staging tree may be left for manual inspection; never
        # turn a successful absent-only publication into a false failure here.
        return


def _sha256_regular_file(path: Path, *, maximum_bytes: int) -> str:
    if _is_linklike(path) or not path.is_file():
        raise ToolInstallError(f"expected a regular non-link file: {path.name}")
    before = path.stat()
    if before.st_size <= 0 or before.st_size > maximum_bytes:
        raise ToolInstallError(f"file size is invalid: {path.name}")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _stat_signature(before) != _stat_signature(opened):
            raise ToolInstallError(f"file changed before hashing: {path.name}")
        digest, size = _hash_open_stream(stream, maximum_bytes=maximum_bytes)
        after_open = os.fstat(stream.fileno())
    after = path.stat()
    if (
        size != before.st_size
        or _stat_signature(opened) != _stat_signature(after_open)
        or _stat_signature(before) != _stat_signature(after)
    ):
        raise ToolInstallError(f"file changed while hashing: {path.name}")
    return digest


def install_locked_archive(
    tool: LockedTool,
    archive_path: Path,
    destination: Path,
    *,
    runtime_verifier: RuntimeVerifier | None = None,
) -> ToolInstallReceipt:
    """Verify, stage, runtime-check, and atomically publish an absent tool directory."""

    runtime_verifier = runtime_verifier or run_blender_version
    archive_path = Path(archive_path).absolute()
    destination = Path(destination).absolute()
    parent = _prepare_real_directory(destination.parent)
    staging = parent / f".blender-staging-{uuid.uuid4().hex}"
    try:
        with ProjectFileLock(parent / ".blender-install.lock", role="writer"):
            if destination.exists() or _is_linklike(destination):
                raise ToolInstallError(
                    f"install destination already exists: {destination.name}",
                )
            if _is_linklike(archive_path) or not archive_path.is_file():
                raise ToolInstallError("archive must be a regular non-link file")
            before = archive_path.stat()
            if before.st_size <= 0 or before.st_size > MAX_ARCHIVE_BYTES:
                raise ToolInstallError("archive size is invalid")
            staging.mkdir(exist_ok=False)
            with archive_path.open("rb") as archive_stream:
                opened = os.fstat(archive_stream.fileno())
                if _stat_signature(before) != _stat_signature(opened):
                    raise ToolInstallError("archive changed before verification")
                digest, size = _hash_open_stream(
                    archive_stream,
                    maximum_bytes=MAX_ARCHIVE_BYTES,
                )
                if size != before.st_size or digest != tool.archive_sha256:
                    raise ToolInstallError("archive SHA-256 does not match the tool lock")
                after_hash = os.fstat(archive_stream.fileno())
                if _stat_signature(opened) != _stat_signature(after_hash):
                    raise ToolInstallError("archive changed while hashing")
                archive_stream.seek(0)
                try:
                    with zipfile.ZipFile(archive_stream, "r") as archive:
                        members = _validated_members(archive, tool)
                        _extract_members(archive, members, staging)
                        _flush_extracted_tree(staging)
                except zipfile.BadZipFile as exc:
                    raise ToolInstallError("archive is not a valid ZIP file") from exc
                after_extract = os.fstat(archive_stream.fileno())
                if _stat_signature(opened) != _stat_signature(after_extract):
                    raise ToolInstallError("archive changed during extraction")
            after = archive_path.stat()
            if _stat_signature(before) != _stat_signature(after):
                raise ToolInstallError("archive path changed during installation")

            staged_root = _require_real_directory(
                staging / tool.archive_root,
                label="staged Blender root",
            )
            executable = staged_root / tool.executable
            if _is_linklike(executable) or not executable.is_file():
                raise ToolInstallError(f"archive is missing {tool.executable}")
            executable_sha256 = _sha256_regular_file(
                executable,
                maximum_bytes=MAX_MEMBER_BYTES,
            )
            runtime_output = _validate_runtime_output(
                tool,
                runtime_verifier(tool, staged_root),
            )
            receipt = ToolInstallReceipt(
                tool_id=tool.tool_id,
                version=tool.version,
                platform=tool.platform,
                archive_sha256=tool.archive_sha256,
                executable=tool.executable,
                executable_sha256=executable_sha256,
                runtime_output=runtime_output,
            )
            _write_receipt(staged_root / RECEIPT_NAME, receipt)
            _flush_file(executable)
            _flush_directory(staged_root)
            _move_directory_noreplace(staged_root, destination)
            return receipt
    except JobContractError as exc:
        raise ToolInstallError(f"tool install lock is unavailable: {exc}") from exc
    except OSError as exc:
        raise ToolInstallError(f"tool installation filesystem failure: {exc}") from exc
    finally:
        _cleanup_owned_staging(staging, parent)


def verify_locked_install(
    tool: LockedTool,
    destination: Path,
    *,
    runtime_verifier: RuntimeVerifier | None = None,
) -> ToolInstallReceipt:
    runtime_verifier = runtime_verifier or run_blender_version
    destination = _require_real_directory(destination, label="Blender install")
    receipt = _load_receipt(destination / RECEIPT_NAME)
    expected_identity = (
        receipt.tool_id == tool.tool_id
        and receipt.version == tool.version
        and receipt.platform == tool.platform
        and receipt.archive_sha256 == tool.archive_sha256
        and receipt.executable == tool.executable
    )
    if not expected_identity:
        raise ToolInstallError("tool receipt does not match the tracked lock")
    executable = destination / tool.executable
    if _is_linklike(executable) or not executable.is_file():
        raise ToolInstallError("Blender executable must be a regular non-link file")
    executable_hash = _sha256_regular_file(
        executable,
        maximum_bytes=MAX_MEMBER_BYTES,
    )
    if executable_hash != receipt.executable_sha256:
        raise ToolInstallError("Blender executable SHA-256 does not match its receipt")
    output = _validate_runtime_output(tool, runtime_verifier(tool, destination))
    if output != receipt.runtime_output:
        raise ToolInstallError("Blender runtime output changed since installation")
    return receipt


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise ToolInstallError(f"tool download redirects are forbidden: {newurl}")


def _publish_cache_file(temporary: Path, destination: Path) -> None:
    if destination.exists() or _is_linklike(destination):
        raise ToolInstallError("tool cache entry already exists")
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise ToolInstallError("tool cache entry already exists") from exc
    _flush_directory(destination.parent)


def _download_locked_archive_unlocked(
    tool: LockedTool,
    cache_root: Path = DEFAULT_TOOL_CACHE,
) -> Path:
    """Stream the exact locked URL into an immutable content-addressed cache entry."""

    cache_root = _prepare_real_directory(cache_root)
    destination = cache_root / f"{tool.archive_sha256}.zip"
    if destination.exists() or _is_linklike(destination):
        digest = _sha256_regular_file(
            destination,
            maximum_bytes=MAX_ARCHIVE_BYTES,
        )
        if digest != tool.archive_sha256:
            raise ToolInstallError("existing tool cache entry is corrupt")
        return destination

    temporary = cache_root / f".{tool.archive_sha256}.{uuid.uuid4().hex}.part"
    request = urllib.request.Request(
        tool.download_url,
        headers={"User-Agent": "nantai-synthetic-village-tool-installer/1"},
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        try:
            response = opener.open(request, timeout=RUNTIME_TIMEOUT_SECONDS)
        except (urllib.error.URLError, OSError) as exc:
            raise ToolInstallError(f"locked tool download failed: {exc}") from exc
        with response, temporary.open("xb") as output:
            if response.geturl() != tool.download_url:
                raise ToolInstallError("tool download final URL does not match the lock")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError as exc:
                    raise ToolInstallError("tool download Content-Length is invalid") from exc
                if declared_length <= 0 or declared_length > MAX_ARCHIVE_BYTES:
                    raise ToolInstallError("tool download exceeds the byte limit")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ToolInstallError("tool download exceeds the byte limit")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if content_length is not None and total != declared_length:
            raise ToolInstallError("tool download length does not match Content-Length")
        if total <= 0 or digest.hexdigest() != tool.archive_sha256:
            raise ToolInstallError("downloaded archive SHA-256 does not match the tool lock")
        _publish_cache_file(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def download_locked_archive(
    tool: LockedTool,
    cache_root: Path = DEFAULT_TOOL_CACHE,
) -> Path:
    cache_root = _prepare_real_directory(cache_root)
    try:
        with ProjectFileLock(cache_root / ".tool-cache.lock", role="writer"):
            return _download_locked_archive_unlocked(tool, cache_root)
    except ToolInstallError:
        raise
    except JobContractError as exc:
        raise ToolInstallError(f"tool cache lock is unavailable: {exc}") from exc
    except OSError as exc:
        raise ToolInstallError(f"tool download filesystem failure: {exc}") from exc
