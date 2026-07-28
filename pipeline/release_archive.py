"""Neutral standard-library primitives for deterministic release archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class ReleaseArchiveError(ValueError):
    """Raised when a release path, file or archive is unsafe."""


@dataclass(frozen=True)
class FileDigest:
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class ArchiveLimits:
    maximum_members: int = 20_000
    maximum_member_bytes: int = 8 * 1024 * 1024 * 1024
    maximum_total_bytes: int = 32 * 1024 * 1024 * 1024
    maximum_compression_ratio: int = 1_000

    def __post_init__(self) -> None:
        for name, value in (
            ("maximum_members", self.maximum_members),
            ("maximum_member_bytes", self.maximum_member_bytes),
            ("maximum_total_bytes", self.maximum_total_bytes),
            ("maximum_compression_ratio", self.maximum_compression_ratio),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ReleaseArchiveError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class InspectedZipMember:
    path: PurePosixPath
    byte_length: int
    compressed_length: int
    unix_mode: int


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON with LF and one trailing newline."""

    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def safe_posix_member_path(value: str) -> PurePosixPath:
    """Return one unambiguous cross-platform relative archive path."""

    if not isinstance(value, str) or not value:
        raise ReleaseArchiveError("release member path must be a non-empty string")
    if "\x00" in value or "\\" in value:
        raise ReleaseArchiveError("release member path contains a forbidden character")
    if value.startswith(("/", "//")) or _DRIVE_PREFIX.match(value):
        raise ReleaseArchiveError("release member path must be relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReleaseArchiveError("release member path contains an unsafe component")
    for part in parts:
        if part.endswith((".", " ")):
            raise ReleaseArchiveError("release member path has an ambiguous suffix")
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise ReleaseArchiveError("release member path uses a Windows reserved name")
    path = PurePosixPath(*parts)
    if path.is_absolute() or path.as_posix() != value:
        raise ReleaseArchiveError("release member path is not canonical POSIX")
    return path


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def stable_regular_file_digest(
    path: Path,
    *,
    maximum_bytes: int | None = None,
    on_read: Callable[[int], None] | None = None,
) -> FileDigest:
    """Stream and hash one stable non-link regular file."""

    source = Path(path)
    if (
        maximum_bytes is not None
        and (
            isinstance(maximum_bytes, bool)
            or not isinstance(maximum_bytes, int)
            or maximum_bytes < 0
        )
    ):
        raise ReleaseArchiveError("file maximum must be a non-negative integer")
    try:
        path_before = source.lstat()
    except OSError as exc:
        raise ReleaseArchiveError("release file is unavailable") from exc
    if stat.S_ISLNK(path_before.st_mode):
        raise ReleaseArchiveError("release file must not be a link")
    if not stat.S_ISREG(path_before.st_mode):
        raise ReleaseArchiveError("release file must be regular")
    if maximum_bytes is not None and path_before.st_size > maximum_bytes:
        raise ReleaseArchiveError("release file exceeds its maximum byte length")

    digest = hashlib.sha256()
    byte_length = 0
    try:
        with source.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            if _stat_signature(path_before) != _stat_signature(descriptor_before):
                raise ReleaseArchiveError("release file changed before read")
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                byte_length += len(chunk)
                if maximum_bytes is not None and byte_length > maximum_bytes:
                    raise ReleaseArchiveError(
                        "release file exceeds its maximum byte length"
                    )
                if on_read is not None:
                    on_read(len(chunk))
            descriptor_after = os.fstat(stream.fileno())
        path_after = source.lstat()
    except ReleaseArchiveError:
        raise
    except OSError as exc:
        raise ReleaseArchiveError("release file cannot be read") from exc

    expected = _stat_signature(path_before)
    if (
        expected != _stat_signature(descriptor_after)
        or expected != _stat_signature(path_after)
        or byte_length != path_before.st_size
    ):
        raise ReleaseArchiveError("release file changed during read")
    return FileDigest(
        byte_length=byte_length,
        sha256=digest.hexdigest(),
    )


def deterministic_zip_info(
    path: str,
    *,
    executable: bool = False,
) -> zipfile.ZipInfo:
    """Return normalized metadata for one deterministic regular ZIP member."""

    relative = safe_posix_member_path(path).as_posix()
    info = zipfile.ZipInfo(relative, ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    permissions = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | permissions) << 16
    return info


def inspect_zip_members(
    archive: zipfile.ZipFile,
    limits: ArchiveLimits,
) -> tuple[InspectedZipMember, ...]:
    """Validate a ZIP central directory before any extraction."""

    infos = archive.infolist()
    if not infos:
        raise ReleaseArchiveError("release archive is empty")
    if len(infos) > limits.maximum_members:
        raise ReleaseArchiveError("release archive member count exceeds its maximum")

    observed: list[InspectedZipMember] = []
    exact_paths: set[str] = set()
    folded_paths: set[str] = set()
    roots: set[str] = set()
    total_bytes = 0
    regular_count = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ReleaseArchiveError("encrypted release archive member is forbidden")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ReleaseArchiveError("release archive compression method is unsupported")
        is_directory = info.is_dir()
        raw_path = info.filename[:-1] if is_directory else info.filename
        path = safe_posix_member_path(raw_path)
        canonical = path.as_posix()
        folded = canonical.casefold()
        if canonical in exact_paths:
            raise ReleaseArchiveError(f"duplicate release archive member: {canonical}")
        if folded in folded_paths:
            raise ReleaseArchiveError(
                f"case-fold collision in release archive: {canonical}"
            )
        exact_paths.add(canonical)
        folded_paths.add(folded)
        roots.add(path.parts[0])

        unix_mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(unix_mode)
        if is_directory:
            if file_type != stat.S_IFDIR or info.file_size != 0:
                raise ReleaseArchiveError(
                    f"release archive directory metadata is invalid: {canonical}"
                )
        else:
            regular_count += 1
            if file_type != stat.S_IFREG:
                raise ReleaseArchiveError(
                    f"release archive member must be regular: {canonical}"
                )
        if info.file_size < 0 or info.compress_size < 0:
            raise ReleaseArchiveError("release archive member length is invalid")
        if info.file_size > limits.maximum_member_bytes:
            raise ReleaseArchiveError(
                f"release archive member exceeds its maximum: {canonical}"
            )
        total_bytes += info.file_size
        if total_bytes > limits.maximum_total_bytes:
            raise ReleaseArchiveError("release archive total size exceeds its maximum")
        if (
            info.file_size > 0
            and (
                info.compress_size == 0
                or info.file_size
                > info.compress_size * limits.maximum_compression_ratio
            )
        ):
            raise ReleaseArchiveError(
                f"release archive compression ratio exceeds its maximum: {canonical}"
            )
        observed.append(
            InspectedZipMember(
                path=path,
                byte_length=info.file_size,
                compressed_length=info.compress_size,
                unix_mode=unix_mode,
            )
        )

    if len(roots) != 1:
        raise ReleaseArchiveError("release archive must contain exactly one root")
    if regular_count == 0:
        raise ReleaseArchiveError("release archive contains no regular files")
    return tuple(sorted(observed, key=lambda row: row.path.as_posix()))
