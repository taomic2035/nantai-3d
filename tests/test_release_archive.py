from __future__ import annotations

import hashlib
import io
import os
import stat
import struct
import warnings
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from pipeline.release_archive import (
    ArchiveLimits,
    ReleaseArchiveError,
    canonical_json_bytes,
    deterministic_zip_info,
    inspect_zip_members,
    preflight_zip_central_directory,
    safe_posix_member_path,
    stable_regular_file_bytes,
    stable_regular_file_digest,
)


def _zip_payload(*, member_count: int = 1) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for index in range(member_count):
            archive.writestr(f"payload-{index}.txt", b"payload")
    return stream.getvalue()


def _forged_eocd(
    *,
    declared_entries: int | None = None,
    declared_central_bytes: int | None = None,
    actual_entries: int = 1,
) -> io.BytesIO:
    payload = bytearray(_zip_payload(member_count=actual_entries))
    eocd = payload.rfind(b"PK\x05\x06")
    assert eocd >= 0
    if declared_entries is not None:
        payload[eocd + 8 : eocd + 10] = declared_entries.to_bytes(
            2,
            "little",
        )
        payload[eocd + 10 : eocd + 12] = declared_entries.to_bytes(
            2,
            "little",
        )
    if declared_central_bytes is not None:
        payload[eocd + 12 : eocd + 16] = (
            declared_central_bytes.to_bytes(4, "little")
        )
    return io.BytesIO(payload)


def _forged_zip64(
    *,
    declared_entries: int,
    actual_entries: int = 1,
) -> io.BytesIO:
    payload = bytearray(_zip_payload(member_count=actual_entries))
    eocd_offset = payload.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    central_bytes = int.from_bytes(
        payload[eocd_offset + 12 : eocd_offset + 16],
        "little",
    )
    central_offset = int.from_bytes(
        payload[eocd_offset + 16 : eocd_offset + 20],
        "little",
    )
    eocd = bytearray(payload[eocd_offset:])
    eocd[8:12] = b"\xff\xff\xff\xff"
    eocd[12:20] = b"\xff\xff\xff\xff\xff\xff\xff\xff"
    zip64_eocd = struct.pack(
        "<4sQ2H2L4Q",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        declared_entries,
        declared_entries,
        central_bytes,
        central_offset,
    )
    locator = struct.pack(
        "<4sLQL",
        b"PK\x06\x07",
        0,
        eocd_offset,
        1,
    )
    return io.BytesIO(
        payload[:eocd_offset] + zip64_eocd + locator + eocd
    )


def test_zip_preflight_rejects_declared_count_and_restores_position() -> None:
    stream = _forged_eocd(declared_entries=2)
    stream.seek(3)

    with pytest.raises(ReleaseArchiveError, match="member count"):
        preflight_zip_central_directory(
            stream,
            limits=ArchiveLimits(maximum_members=1),
        )

    assert stream.tell() == 3


def test_zip_preflight_rejects_declared_central_directory_bytes() -> None:
    stream = _forged_eocd(declared_central_bytes=2)

    with pytest.raises(ReleaseArchiveError, match="central directory"):
        preflight_zip_central_directory(
            stream,
            limits=ArchiveLimits(maximum_central_directory_bytes=1),
        )


def test_zip_preflight_reads_zip64_declared_count() -> None:
    stream = _forged_zip64(declared_entries=2)

    with pytest.raises(ReleaseArchiveError, match="member count"):
        preflight_zip_central_directory(
            stream,
            limits=ArchiveLimits(maximum_members=1),
        )


def test_zip_preflight_rejects_eocd_underdeclared_actual_count() -> None:
    stream = _forged_eocd(
        declared_entries=1,
        actual_entries=3,
    )

    with pytest.raises(ReleaseArchiveError, match="member count"):
        preflight_zip_central_directory(
            stream,
            limits=ArchiveLimits(maximum_members=1),
        )


def test_zip_preflight_rejects_zip64_underdeclared_actual_count() -> None:
    stream = _forged_zip64(
        declared_entries=1,
        actual_entries=3,
    )

    with pytest.raises(ReleaseArchiveError, match="member count"):
        preflight_zip_central_directory(
            stream,
            limits=ArchiveLimits(maximum_members=1),
        )


@pytest.mark.parametrize("corruption", ("signature", "length"))
def test_zip_preflight_rejects_malformed_central_record(
    corruption: str,
) -> None:
    payload = bytearray(_zip_payload())
    eocd_offset = payload.rfind(b"PK\x05\x06")
    central_offset = int.from_bytes(
        payload[eocd_offset + 16 : eocd_offset + 20],
        "little",
    )
    if corruption == "signature":
        payload[central_offset : central_offset + 4] = b"NOPE"
    else:
        payload[central_offset + 28 : central_offset + 30] = (
            0xFFFF
        ).to_bytes(2, "little")

    with pytest.raises(ReleaseArchiveError, match="central directory"):
        preflight_zip_central_directory(
            io.BytesIO(payload),
            limits=ArchiveLimits(),
        )


def test_canonical_json_bytes_are_sorted_utf8_lf_and_stable() -> None:
    payload = {"z": "山村", "a": [2, 1]}

    observed = canonical_json_bytes(payload)

    assert observed == b'{"a":[2,1],"z":"\xe5\xb1\xb1\xe6\x9d\x91"}\n'
    assert canonical_json_bytes(payload) == observed


def test_safe_posix_member_path_accepts_one_canonical_relative_path() -> None:
    observed = safe_posix_member_path("web/data/recon/scene.ply")

    assert observed == PurePosixPath("web/data/recon/scene.ply")


@pytest.mark.parametrize(
    "candidate",
    (
        "",
        "/absolute",
        "//server/share",
        "C:/drive",
        "../escape",
        "web/../escape",
        "web/./scene.ply",
        "web//scene.ply",
        "web\\scene.ply",
        "CON",
        "con.txt",
        "web/trailing.",
        "web/trailing ",
        "web/\x00name",
        "web/file:name.txt",
        "web/\x01name",
        "web/\x1f",
        "web/\x7f",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "LPT1",
        "nul.tar.gz",
        "com1.log",
        "aux.txt",
    ),
)
def test_safe_posix_member_path_rejects_ambiguous_names(candidate: str) -> None:
    with pytest.raises(ReleaseArchiveError):
        safe_posix_member_path(candidate)


def test_stable_digest_streams_in_one_mib_chunks(tmp_path) -> None:
    target = tmp_path / "scene.ply"
    payload = b"x" * (3 * 1024 * 1024 + 17)
    target.write_bytes(payload)
    observed: list[int] = []

    result = stable_regular_file_digest(
        target,
        on_read=observed.append,
    )

    assert result.byte_length == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert observed
    assert max(observed) <= 1024 * 1024


def test_stable_digest_rejects_a_file_changed_during_read(tmp_path) -> None:
    target = tmp_path / "scene.ply"
    target.write_bytes(b"x" * (2 * 1024 * 1024))
    changed = False

    def change_mtime_after_first_read(_size: int) -> None:
        nonlocal changed
        if not changed:
            changed = True
            stat_result = target.stat()
            os.utime(
                target,
                ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000),
            )

    with pytest.raises(ReleaseArchiveError, match="changed"):
        stable_regular_file_digest(
            target,
            on_read=change_mtime_after_first_read,
        )


def test_stable_digest_rejects_nonregular_and_oversized_inputs(tmp_path) -> None:
    with pytest.raises(ReleaseArchiveError, match="regular"):
        stable_regular_file_digest(tmp_path)

    target = tmp_path / "large.bin"
    target.write_bytes(b"1234")
    with pytest.raises(ReleaseArchiveError, match="maximum"):
        stable_regular_file_digest(target, maximum_bytes=3)


def test_stable_digest_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"payload")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    with pytest.raises(ReleaseArchiveError, match="link"):
        stable_regular_file_digest(link)


def test_stable_regular_file_digest_never_uses_path_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: digest must use os.open, not Path.open (check-then-reopen)."""

    target = tmp_path / "scene.ply"
    target.write_bytes(b"payload")

    opened: list[Path] = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == target:
            opened.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)

    result = stable_regular_file_digest(target)

    assert result.byte_length == 7
    assert not opened, "Path.open was called (should use os.open)"


def test_stable_regular_file_digest_rejects_open_handle_identity_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: post-open fstat identity drift must be rejected."""

    target = tmp_path / "scene.ply"
    target.write_bytes(b"payload")

    original_fstat = os.fstat
    call_count = {"n": 0}

    def drifting_fstat(fd: int):
        result = original_fstat(fd)
        call_count["n"] += 1
        if call_count["n"] == 1:
            # descriptor_before: drift inode so signature mismatch fires
            return os.stat_result(
                (
                    result.st_mode,
                    result.st_ino + 1,
                    result.st_dev,
                    result.st_nlink,
                    result.st_uid,
                    result.st_gid,
                    result.st_size,
                    result.st_atime,
                    result.st_mtime,
                    result.st_ctime,
                )
            )
        return result

    monkeypatch.setattr(os, "fstat", drifting_fstat)

    with pytest.raises(ReleaseArchiveError, match="changed before read"):
        stable_regular_file_digest(target)


def test_stable_regular_file_digest_oserror_does_not_leak_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: OSError must produce a fixed message, no absolute path."""

    target = tmp_path / "scene.ply"
    target.write_bytes(b"payload")
    absolute = str(target)

    original_os_open = os.open

    def failing_os_open(path, *args, **kwargs):
        if Path(path) == target:
            raise OSError(f"private context {absolute}")
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_os_open)

    with pytest.raises(ReleaseArchiveError, match="cannot be read") as exc:
        stable_regular_file_digest(target)

    message = str(exc.value)
    assert absolute not in message
    assert "private context" not in message


@pytest.mark.parametrize(
    ("executable", "permissions"),
    ((False, 0o644), (True, 0o755)),
)
def test_deterministic_zip_info_normalizes_metadata(
    executable: bool,
    permissions: int,
) -> None:
    info = deterministic_zip_info("web/studio/index.html", executable=executable)

    assert info.filename == "web/studio/index.html"
    assert info.date_time == (1980, 1, 1, 0, 0, 0)
    assert info.create_system == 3
    assert info.compress_type == zipfile.ZIP_DEFLATED
    assert info.external_attr >> 16 == stat.S_IFREG | permissions


def _write_archive(
    path,
    entries: tuple[tuple[str, bytes], ...],
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries:
            archive.writestr(deterministic_zip_info(name), payload)


def _write_raw_archive(
    path,
    entries: tuple[tuple[str, bytes], ...],
) -> None:
    """Write a ZIP bypassing ``safe_posix_member_path`` validation."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, payload)


def test_zip_inspection_accepts_one_bounded_canonical_root(tmp_path) -> None:
    archive_path = tmp_path / "runtime.zip"
    _write_archive(
        archive_path,
        (
            ("nantai-runtime/README.md", b"readme\n"),
            ("nantai-runtime/web/index.html", b"<h1>Nantai</h1>\n"),
        ),
    )

    with zipfile.ZipFile(archive_path) as archive:
        observed = inspect_zip_members(archive, ArchiveLimits())

    assert tuple(row.path.as_posix() for row in observed) == (
        "nantai-runtime/README.md",
        "nantai-runtime/web/index.html",
    )
    assert sum(row.byte_length for row in observed) == 23


def test_zip_inspection_rejects_exact_and_casefold_collisions(tmp_path) -> None:
    exact_path = tmp_path / "exact.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        _write_archive(
            exact_path,
            (
                ("nantai-runtime/web/app.js", b"a"),
                ("nantai-runtime/web/app.js", b"b"),
            ),
        )
    with zipfile.ZipFile(exact_path) as archive:
        with pytest.raises(ReleaseArchiveError, match="duplicate"):
            inspect_zip_members(archive, ArchiveLimits())

    casefold_path = tmp_path / "casefold.zip"
    _write_archive(
        casefold_path,
        (
            ("nantai-runtime/web/App.js", b"a"),
            ("nantai-runtime/web/app.js", b"b"),
        ),
    )
    with zipfile.ZipFile(casefold_path) as archive:
        with pytest.raises(ReleaseArchiveError, match="case-fold"):
            inspect_zip_members(archive, ArchiveLimits())


def test_zip_inspection_rejects_multiple_roots_and_expansion_limits(tmp_path) -> None:
    multiple = tmp_path / "multiple.zip"
    _write_archive(
        multiple,
        (
            ("first/a.txt", b"a"),
            ("second/b.txt", b"b"),
        ),
    )
    with zipfile.ZipFile(multiple) as archive:
        with pytest.raises(ReleaseArchiveError, match="root"):
            inspect_zip_members(archive, ArchiveLimits())

    expanded = tmp_path / "expanded.zip"
    _write_archive(
        expanded,
        (("nantai-runtime/zeros.bin", b"\0" * 10_000),),
    )
    with zipfile.ZipFile(expanded) as archive:
        with pytest.raises(ReleaseArchiveError, match="ratio"):
            inspect_zip_members(
                archive,
                ArchiveLimits(maximum_compression_ratio=2),
            )
    with zipfile.ZipFile(expanded) as archive:
        with pytest.raises(ReleaseArchiveError, match="member"):
            inspect_zip_members(
                archive,
                ArchiveLimits(maximum_member_bytes=9_999),
            )
    with zipfile.ZipFile(expanded) as archive:
        with pytest.raises(ReleaseArchiveError, match="total"):
            inspect_zip_members(
                archive,
                ArchiveLimits(maximum_total_bytes=9_999),
            )


@pytest.mark.parametrize(
    ("limits", "member"),
    (
        (ArchiveLimits(maximum_path_bytes=8), "root/ééé.bin"),
        (
            ArchiveLimits(maximum_path_components=2),
            "root/nested/file.bin",
        ),
    ),
)
def test_zip_inspection_rejects_path_budgets(
    tmp_path: Path,
    limits: ArchiveLimits,
    member: str,
) -> None:
    archive_path = tmp_path / "path-budget.zip"
    _write_archive(archive_path, ((member, b"x"),))

    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ReleaseArchiveError, match="path.*maximum"):
            inspect_zip_members(archive, limits)


class _MetadataArchive:
    def __init__(self, info: zipfile.ZipInfo):
        self._info = info

    def infolist(self) -> list[zipfile.ZipInfo]:
        return [self._info]


def _metadata_info(*, mode: int, encrypted: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo("nantai-runtime/payload.bin")
    info.create_system = 3
    info.external_attr = mode << 16
    info.file_size = 1
    info.compress_size = 1
    info.flag_bits = 1 if encrypted else 0
    return info


def test_zip_inspection_rejects_encrypted_and_nonregular_members() -> None:
    encrypted = _MetadataArchive(
        _metadata_info(mode=stat.S_IFREG | 0o644, encrypted=True)
    )
    with pytest.raises(ReleaseArchiveError, match="encrypted"):
        inspect_zip_members(encrypted, ArchiveLimits())

    symlink = _MetadataArchive(
        _metadata_info(mode=stat.S_IFLNK | 0o777)
    )
    with pytest.raises(ReleaseArchiveError, match="regular"):
        inspect_zip_members(symlink, ArchiveLimits())


def test_zip_inspection_rejects_nfc_nfd_collision(tmp_path) -> None:
    """NFC and NFD equivalents must collide under normalization."""
    nfc_name = "nantai-runtime/web/\xe9.txt"
    nfd_name = "nantai-runtime/web/e\u0301.txt"
    archive_path = tmp_path / "nfc-nfd.zip"
    _write_archive(
        archive_path,
        ((nfc_name, b"a"), (nfd_name, b"b")),
    )
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ReleaseArchiveError, match="normalization"):
            inspect_zip_members(archive, ArchiveLimits())


def test_zip_inspection_rejects_combined_casefold_normalization_collision(
    tmp_path,
) -> None:
    """Case-folding and normalization must form one collision identity."""
    upper_nfc_name = "nantai-runtime/web/\xc9.txt"
    lower_nfd_name = "nantai-runtime/web/e\u0301.txt"
    archive_path = tmp_path / "casefold-normalization.zip"
    _write_archive(
        archive_path,
        ((upper_nfc_name, b"a"), (lower_nfd_name, b"b")),
    )
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ReleaseArchiveError, match="collision"):
            inspect_zip_members(archive, ArchiveLimits())


def test_zip_inspection_rejects_illegal_child_of_valid_root(tmp_path) -> None:
    """A valid wrapper root must not exempt an illegal child path."""
    archive_path = tmp_path / "bad-child.zip"
    _write_raw_archive(
        archive_path,
        (("nantai-runtime/CON", b"x"),),
    )
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ReleaseArchiveError, match="reserved"):
            inspect_zip_members(archive, ArchiveLimits())


def test_zip_inspection_preserves_legal_utf8_names(tmp_path) -> None:
    """Legal UTF-8 filenames (NFC) must be accepted."""
    archive_path = tmp_path / "utf8.zip"
    _write_archive(
        archive_path,
        (
            ("nantai-runtime/web/\xe9.txt", b"a"),
            ("nantai-runtime/\u5c71\u6751.txt", b"b"),
        ),
    )
    with zipfile.ZipFile(archive_path) as archive:
        observed = inspect_zip_members(archive, ArchiveLimits())

    names = tuple(row.path.as_posix() for row in observed)
    assert "nantai-runtime/web/\xe9.txt" in names
    assert "nantai-runtime/\u5c71\u6751.txt" in names


# ============================================================
# RED → GREEN: stable_regular_file_bytes TOCTOU-safe read
# ============================================================


def test_stable_regular_file_bytes_returns_matching_hash_and_bytes(
    tmp_path: Path,
) -> None:
    """stable_regular_file_bytes must return bytes whose SHA-256 matches the
    returned digest, proving no reopen is needed after hashing.
    """
    payload = b"TOCTOU-safe payload content"
    source = tmp_path / "evidence.bin"
    source.write_bytes(payload)

    raw, digest = stable_regular_file_bytes(source)

    assert raw == payload
    assert digest.byte_length == len(payload)
    assert digest.sha256 == hashlib.sha256(payload).hexdigest()


def test_stable_regular_file_bytes_rejects_symlink(tmp_path: Path) -> None:
    """stable_regular_file_bytes must reject a symlinked file (O_NOFOLLOW)."""
    target = tmp_path / "real.bin"
    target.write_bytes(b"real-content")
    link = tmp_path / "link.bin"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink not supported on this platform")

    with pytest.raises(ReleaseArchiveError, match="link|regular"):
        stable_regular_file_bytes(link)
