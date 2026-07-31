"""Neutral standard-library primitives for deterministic release archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_EOCD_FIXED_BYTES = 22
_MAXIMUM_ZIP_COMMENT_BYTES = 65_535
_ZIP64_LOCATOR_BYTES = 20
_ZIP64_EOCD_FIXED_BYTES = 56
_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_CENTRAL_DIRECTORY_FIXED_BYTES = 46


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
    maximum_path_bytes: int = 4_096
    maximum_path_components: int = 64
    maximum_central_directory_bytes: int = 128 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in (
            ("maximum_members", self.maximum_members),
            ("maximum_member_bytes", self.maximum_member_bytes),
            ("maximum_total_bytes", self.maximum_total_bytes),
            ("maximum_compression_ratio", self.maximum_compression_ratio),
            ("maximum_path_bytes", self.maximum_path_bytes),
            ("maximum_path_components", self.maximum_path_components),
            (
                "maximum_central_directory_bytes",
                self.maximum_central_directory_bytes,
            ),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ReleaseArchiveError(f"{name} must be a positive integer")


def preflight_zip_central_directory(
    stream: BinaryIO,
    *,
    limits: ArchiveLimits,
) -> None:
    """Bound declared ZIP metadata before ``ZipFile`` materializes it."""

    try:
        previous = stream.tell()
    except (AttributeError, OSError) as exc:
        raise ReleaseArchiveError(
            "release archive stream must be seekable"
        ) from exc
    failure: Exception | None = None
    try:
        stream.seek(0, os.SEEK_END)
        archive_bytes = stream.tell()
        tail_bytes = min(
            archive_bytes,
            _EOCD_FIXED_BYTES + _MAXIMUM_ZIP_COMMENT_BYTES,
        )
        stream.seek(archive_bytes - tail_bytes)
        tail = stream.read(tail_bytes)
        eocd_index = tail.rfind(_EOCD_SIGNATURE)
        if (
            eocd_index < 0
            or len(tail) - eocd_index < _EOCD_FIXED_BYTES
        ):
            raise ReleaseArchiveError(
                "release archive end record is missing"
            )
        (
            _signature,
            disk_number,
            central_disk,
            disk_entries,
            total_entries,
            central_bytes,
            central_offset,
            comment_bytes,
        ) = struct.unpack_from("<4s4H2LH", tail, eocd_index)
        if eocd_index + _EOCD_FIXED_BYTES + comment_bytes != len(tail):
            raise ReleaseArchiveError(
                "release archive end record is malformed"
            )
        if disk_number != 0 or central_disk != 0:
            raise ReleaseArchiveError(
                "multi-disk release archives are forbidden"
            )

        eocd_offset = archive_bytes - tail_bytes + eocd_index
        central_end = eocd_offset
        needs_zip64 = (
            disk_entries == 0xFFFF
            or total_entries == 0xFFFF
            or central_bytes == 0xFFFFFFFF
            or central_offset == 0xFFFFFFFF
        )
        if needs_zip64:
            locator_offset = eocd_offset - _ZIP64_LOCATOR_BYTES
            if locator_offset < 0:
                raise ReleaseArchiveError(
                    "release archive ZIP64 locator is missing"
                )
            stream.seek(locator_offset)
            locator = stream.read(_ZIP64_LOCATOR_BYTES)
            if len(locator) != _ZIP64_LOCATOR_BYTES:
                raise ReleaseArchiveError(
                    "release archive ZIP64 locator is truncated"
                )
            (
                locator_signature,
                locator_disk,
                zip64_offset,
                locator_disks,
            ) = struct.unpack("<4sLQL", locator)
            if (
                locator_signature != _ZIP64_LOCATOR_SIGNATURE
                or locator_disk != 0
                or locator_disks != 1
            ):
                raise ReleaseArchiveError(
                    "release archive ZIP64 locator is invalid"
                )
            zip64_physical_offset = (
                locator_offset - _ZIP64_EOCD_FIXED_BYTES
            )
            if (
                zip64_physical_offset < 0
                or zip64_offset > zip64_physical_offset
            ):
                raise ReleaseArchiveError(
                    "release archive ZIP64 end record is invalid"
                )
            stream.seek(zip64_physical_offset)
            zip64_record = stream.read(_ZIP64_EOCD_FIXED_BYTES)
            if len(zip64_record) != _ZIP64_EOCD_FIXED_BYTES:
                raise ReleaseArchiveError(
                    "release archive ZIP64 end record is truncated"
                )
            (
                zip64_signature,
                zip64_record_bytes,
                _creator_version,
                _required_version,
                zip64_disk,
                zip64_central_disk,
                zip64_disk_entries,
                total_entries,
                central_bytes,
                central_offset,
            ) = struct.unpack("<4sQ2H2L4Q", zip64_record)
            if (
                zip64_signature != _ZIP64_EOCD_SIGNATURE
                or zip64_record_bytes != 44
                or zip64_disk != 0
                or zip64_central_disk != 0
                or zip64_disk_entries != total_entries
            ):
                raise ReleaseArchiveError(
                    "release archive ZIP64 end record is invalid"
                )
            central_end = zip64_physical_offset
        elif disk_entries != total_entries:
            raise ReleaseArchiveError(
                "release archive member count is inconsistent"
            )

        if total_entries > limits.maximum_members:
            raise ReleaseArchiveError(
                "release archive member count exceeds its maximum"
            )
        if central_bytes > limits.maximum_central_directory_bytes:
            raise ReleaseArchiveError(
                "release archive central directory exceeds its maximum"
            )
        if central_bytes > archive_bytes:
            raise ReleaseArchiveError(
                "release archive central directory is invalid"
            )

        central_start = central_end - central_bytes
        if central_start < 0 or central_offset > central_start:
            raise ReleaseArchiveError(
                "release archive central directory is invalid"
            )
        stream.seek(central_start)
        remaining = central_bytes
        actual_entries = 0
        while remaining:
            if remaining < _CENTRAL_DIRECTORY_FIXED_BYTES:
                raise ReleaseArchiveError(
                    "release archive central directory is truncated"
                )
            header = stream.read(_CENTRAL_DIRECTORY_FIXED_BYTES)
            if len(header) != _CENTRAL_DIRECTORY_FIXED_BYTES:
                raise ReleaseArchiveError(
                    "release archive central directory is truncated"
                )
            fields = struct.unpack("<4s6H3L5H2L", header)
            if fields[0] != _CENTRAL_DIRECTORY_SIGNATURE:
                raise ReleaseArchiveError(
                    "release archive central directory record is invalid"
                )
            variable_bytes = fields[10] + fields[11] + fields[12]
            record_bytes = (
                _CENTRAL_DIRECTORY_FIXED_BYTES + variable_bytes
            )
            if record_bytes > remaining:
                raise ReleaseArchiveError(
                    "release archive central directory record is truncated"
                )
            actual_entries += 1
            if actual_entries > limits.maximum_members:
                raise ReleaseArchiveError(
                    "release archive member count exceeds its maximum"
                )
            stream.seek(variable_bytes, os.SEEK_CUR)
            remaining -= record_bytes
        if actual_entries != total_entries:
            raise ReleaseArchiveError(
                "release archive member count is inconsistent"
            )
    except ReleaseArchiveError as exc:
        failure = exc
        raise
    except (OSError, OverflowError, struct.error) as exc:
        failure = exc
        raise ReleaseArchiveError(
            "release archive central directory cannot be inspected"
        ) from exc
    finally:
        try:
            stream.seek(previous)
        except (AttributeError, OSError) as exc:
            if failure is None:
                raise ReleaseArchiveError(
                    "release archive stream position cannot be restored"
                ) from exc


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


def portable_path_identity(value: str) -> str:
    """Return one cross-platform case/Unicode collision identity."""

    normalized = unicodedata.normalize("NFC", value)
    return unicodedata.normalize("NFC", normalized.casefold())


def safe_posix_member_path(value: str) -> PurePosixPath:
    """Return one unambiguous cross-platform relative archive path."""

    if not isinstance(value, str) or not value:
        raise ReleaseArchiveError("release member path must be a non-empty string")
    if "\x00" in value or "\\" in value or ":" in value:
        raise ReleaseArchiveError("release member path contains a forbidden character")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        raise ReleaseArchiveError("release member path contains a control character")
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
        stat.S_IFMT(value.st_mode),
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
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ReleaseArchiveError("release file cannot be read") from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise ReleaseArchiveError("release file cannot be read") from exc
    try:
        with stream:
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


def stable_regular_file_bytes(
    path: Path,
    *,
    maximum_bytes: int | None = None,
) -> tuple[bytes, FileDigest]:
    """Read one stable non-link regular file and return its bytes + digest.

    Same TOCTOU-safe pattern as ``stable_regular_file_digest`` (lstat,
    O_NOFOLLOW, post-open fstat identity check, bounded read, post-read
    lstat drift check) but also returns the file bytes so callers never
    need to reopen by name after hashing.
    """

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
    payload = bytearray()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ReleaseArchiveError("release file cannot be read") from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise ReleaseArchiveError("release file cannot be read") from exc
    try:
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if _stat_signature(path_before) != _stat_signature(descriptor_before):
                raise ReleaseArchiveError("release file changed before read")
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                payload.extend(chunk)
                byte_length += len(chunk)
                if maximum_bytes is not None and byte_length > maximum_bytes:
                    raise ReleaseArchiveError(
                        "release file exceeds its maximum byte length"
                    )
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
    return bytes(payload), FileDigest(
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
    normalized_paths: set[str] = set()
    normalized_folded_paths: set[str] = set()
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
        if len(canonical.encode("utf-8")) > limits.maximum_path_bytes:
            raise ReleaseArchiveError(
                f"release archive member path exceeds its maximum: {canonical}"
            )
        if len(path.parts) > limits.maximum_path_components:
            raise ReleaseArchiveError(
                "release archive member path depth exceeds its maximum: "
                f"{canonical}"
            )
        folded = canonical.casefold()
        normalized = unicodedata.normalize("NFC", canonical)
        normalized_folded = portable_path_identity(canonical)
        if canonical in exact_paths:
            raise ReleaseArchiveError(f"duplicate release archive member: {canonical}")
        if folded in folded_paths:
            raise ReleaseArchiveError(
                f"case-fold collision in release archive: {canonical}"
            )
        if normalized in normalized_paths:
            raise ReleaseArchiveError(
                f"normalization collision in release archive: {canonical}"
            )
        if normalized_folded in normalized_folded_paths:
            raise ReleaseArchiveError(
                f"case-fold/normalization collision in release archive: {canonical}"
            )
        exact_paths.add(canonical)
        folded_paths.add(folded)
        normalized_paths.add(normalized)
        normalized_folded_paths.add(normalized_folded)
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
