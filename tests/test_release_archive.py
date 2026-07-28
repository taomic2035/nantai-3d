from __future__ import annotations

import hashlib
import os
import stat
import warnings
import zipfile
from pathlib import PurePosixPath

import pytest

from pipeline.release_archive import (
    ArchiveLimits,
    ReleaseArchiveError,
    canonical_json_bytes,
    deterministic_zip_info,
    inspect_zip_members,
    safe_posix_member_path,
    stable_regular_file_digest,
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
