"""Pinned Blender toolchain installation and verification tests."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

import pipeline.synthetic_village.tool_lock as tool_lock
from pipeline.synthetic_village.tool_lock import (
    LockedTool,
    ToolInstallError,
    canonical_tool_lock_bytes,
    download_locked_archive,
    install_locked_archive,
    load_tool_lock,
    verify_locked_install,
)
from scripts import setup_synthetic_tools

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "tools.lock.json"
BLENDER_SHA256 = "e11d3a8e4d4249be5a7db4a9325c1f670037d4233467c3b0bda181001efe44d3"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def _tool_for_archive(archive: Path) -> LockedTool:
    return LockedTool(
        tool_id="blender",
        version="4.5.11",
        channel="LTS",
        platform="windows-x64",
        archive_type="zip",
        download_url="https://example.test/blender.zip",
        archive_sha256=_sha256(archive),
        archive_root="blender-test",
        executable="blender.exe",
        install_dir="third/blender",
        runtime_build_hash="0123456789ab",
        runtime_build_timestamp="2026-01-02 03:04:05",
        version_output_prefix="Blender 4.5.11 LTS",
    )


def _accept_fake_runtime(_tool: LockedTool, install_root: Path) -> str:
    assert (install_root / "blender.exe").is_file()
    return (
        f"{_tool.version_output_prefix} (hash {_tool.runtime_build_hash} "
        f"built {_tool.runtime_build_timestamp})\n"
        f"{_tool.version_output_prefix}\nsynthetic test runtime"
    )


class _FakeResponse:
    def __init__(self, payload: bytes, url: str, *, content_length: int | None = None):
        self._stream = io.BytesIO(payload)
        self._url = url
        self.headers = {}
        self.read_sizes: list[int] = []
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def geturl(self) -> str:
        return self._url

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._stream.close()


class _FakeOpener:
    def __init__(self, response: _FakeResponse):
        self.response = response

    def open(self, request, *, timeout):
        assert request.full_url.startswith("https://")
        assert timeout == tool_lock.RUNTIME_TIMEOUT_SECONDS
        return self.response


def test_tracked_lock_is_canonical_https_and_exactly_pinned():
    lock = load_tool_lock(LOCK_PATH)

    assert LOCK_PATH.read_bytes() == canonical_tool_lock_bytes(lock)
    assert lock.blender.version == "4.5.11"
    assert lock.blender.platform == "windows-x64"
    assert lock.blender.download_url == (
        "https://download.blender.org/release/Blender4.5/"
        "blender-4.5.11-windows-x64.zip"
    )
    assert lock.blender.archive_sha256 == BLENDER_SHA256
    assert lock.blender.archive_root == "blender-4.5.11-windows-x64"
    assert lock.blender.executable == "blender.exe"
    assert lock.blender.install_dir == "third/blender"
    assert lock.blender.runtime_build_hash == "4db51e9d1e1e"
    assert lock.blender.runtime_build_timestamp == "2026-06-23 01:33:52"


def test_tool_lock_rejects_duplicate_keys_and_noncanonical_json(tmp_path):
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tool_lock.ToolLockError, match="canonical"):
        load_tool_lock(noncanonical)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        LOCK_PATH.read_text(encoding="utf-8").replace(
            '  "schema_version": 1',
            '  "schema_version": 1,\n  "schema_version": 1',
        ),
        encoding="utf-8",
    )
    with pytest.raises(tool_lock.ToolLockError, match="duplicate JSON key"):
        load_tool_lock(duplicate)


def test_locked_tool_rejects_http_bad_hash_and_nonportable_paths():
    valid = {
        "tool_id": "blender",
        "version": "4.5.11",
        "channel": "LTS",
        "platform": "windows-x64",
        "archive_type": "zip",
        "download_url": "https://example.test/blender.zip",
        "archive_sha256": "a" * 64,
        "archive_root": "blender-test",
        "executable": "blender.exe",
        "install_dir": "third/blender",
        "runtime_build_hash": "0123456789ab",
        "runtime_build_timestamp": "2026-01-02 03:04:05",
        "version_output_prefix": "Blender 4.5.11 LTS",
    }
    for field, value in (
        ("download_url", "http://example.test/blender.zip"),
        ("archive_sha256", "not-a-sha"),
        ("archive_root", "../blender-test"),
        ("executable", "bin/blender.exe"),
        ("install_dir", "../third/blender"),
        ("install_dir", "third/NUL.txt"),
        ("install_dir", "third/trailing. "),
    ):
        with pytest.raises(ValidationError):
            LockedTool.model_validate({**valid, field: value})


def test_install_rejects_hash_mismatch_before_extraction(tmp_path):
    archive = tmp_path / "blender.zip"
    _write_zip(archive, {"blender-test/blender.exe": b"fake executable"})
    tool = _tool_for_archive(archive)
    with archive.open("ab") as stream:
        stream.write(b"tampered")
    destination = tmp_path / "third" / "blender"

    with pytest.raises(ToolInstallError, match="SHA-256"):
        install_locked_archive(
            tool,
            archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )
    assert not destination.exists()


def test_install_rejects_zip_slip_and_symlink_members(tmp_path):
    escape_archive = tmp_path / "escape.zip"
    _write_zip(
        escape_archive,
        {
            "blender-test/blender.exe": b"fake executable",
            "../escaped.txt": b"escape",
        },
    )
    destination = tmp_path / "third" / "blender"
    with pytest.raises(ToolInstallError, match="unsafe archive path"):
        install_locked_archive(
            _tool_for_archive(escape_archive),
            escape_archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )
    assert not (tmp_path / "escaped.txt").exists()
    assert not destination.exists()


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "/absolute.txt",
        "\\absolute.txt",
        "C:\\outside.txt",
        "C:relative.txt",
        "blender-test/blender.exe:evil",
        "blender-test/trailing-space /payload.txt",
        "blender-test/trailing-dot./payload.txt",
        "blender-test/NUL.txt",
        "blender-test/COM1.bin",
        "blender-test/aux/payload.txt",
    ],
)
def test_install_rejects_absolute_windows_and_ntfs_ads_paths(tmp_path, unsafe_name):
    archive = tmp_path / "unsafe.zip"
    _write_zip(
        archive,
        {
            "blender-test/blender.exe": b"fake executable",
            unsafe_name: b"unsafe",
        },
    )
    destination = tmp_path / "third" / "blender"
    with pytest.raises(ToolInstallError, match="unsafe archive path"):
        install_locked_archive(
            _tool_for_archive(archive),
            archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )
    assert not destination.exists()


def test_install_rejects_casefold_duplicates_and_reserved_receipt(tmp_path):
    duplicate_archive = tmp_path / "duplicate.zip"
    _write_zip(
        duplicate_archive,
        {
            "blender-test/blender.exe": b"fake executable",
            "blender-test/Data.txt": b"first",
            "blender-test/data.TXT": b"second",
        },
    )
    destination = tmp_path / "third" / "blender"
    with pytest.raises(ToolInstallError, match="duplicate archive path"):
        install_locked_archive(
            _tool_for_archive(duplicate_archive),
            duplicate_archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )

    receipt_archive = tmp_path / "receipt.zip"
    _write_zip(
        receipt_archive,
        {
            "blender-test/blender.exe": b"fake executable",
            "blender-test/.nantai-tool.json": b"untrusted receipt",
        },
    )
    with pytest.raises(ToolInstallError, match="reserved path"):
        install_locked_archive(
            _tool_for_archive(receipt_archive),
            receipt_archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )

    symlink_archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink_archive, "w") as archive:
        archive.writestr("blender-test/blender.exe", b"fake executable")
        link = zipfile.ZipInfo("blender-test/redirect")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, b"../../outside")
    with pytest.raises(ToolInstallError, match="link|regular"):
        install_locked_archive(
            _tool_for_archive(symlink_archive),
            symlink_archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )
    assert not destination.exists()


def test_install_rejects_missing_executable_without_publication(tmp_path):
    archive = tmp_path / "missing-executable.zip"
    _write_zip(archive, {"blender-test/readme.txt": b"not blender"})
    destination = tmp_path / "third" / "blender"

    with pytest.raises(ToolInstallError, match="blender.exe"):
        install_locked_archive(
            _tool_for_archive(archive),
            archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )
    assert not destination.exists()


def test_install_is_verified_then_published_only_to_absent_destination(tmp_path):
    archive = tmp_path / "valid.zip"
    executable = b"fake Blender executable bytes"
    _write_zip(
        archive,
        {
            "blender-test/blender.exe": executable,
            "blender-test/data/version.txt": b"4.5.11",
        },
    )
    tool = _tool_for_archive(archive)
    destination = tmp_path / "third" / "blender"

    receipt = install_locked_archive(
        tool,
        archive,
        destination,
        runtime_verifier=_accept_fake_runtime,
    )

    assert (destination / "blender.exe").read_bytes() == executable
    assert receipt.archive_sha256 == tool.archive_sha256
    assert receipt.executable_sha256 == hashlib.sha256(executable).hexdigest()
    assert verify_locked_install(
        tool,
        destination,
        runtime_verifier=_accept_fake_runtime,
    ) == receipt

    sentinel = destination / "keep.txt"
    sentinel.write_text("do not replace", encoding="utf-8")
    with pytest.raises(ToolInstallError, match="already exists"):
        install_locked_archive(
            tool,
            archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )
    assert sentinel.read_text(encoding="utf-8") == "do not replace"


def test_publish_race_preserves_competing_destination(tmp_path, monkeypatch):
    archive = tmp_path / "valid.zip"
    _write_zip(archive, {"blender-test/blender.exe": b"fake executable"})
    tool = _tool_for_archive(archive)
    destination = tmp_path / "third" / "blender"
    original_move = tool_lock._move_directory_noreplace

    def race_move(source: Path, target: Path):
        target.mkdir()
        (target / "competitor.txt").write_text("keep", encoding="utf-8")
        return original_move(source, target)

    monkeypatch.setattr(tool_lock, "_move_directory_noreplace", race_move)
    with pytest.raises(ToolInstallError, match="already exists"):
        install_locked_archive(
            tool,
            archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )
    assert (destination / "competitor.txt").read_text(encoding="utf-8") == "keep"


def test_directory_durability_failure_never_publishes_install(tmp_path, monkeypatch):
    archive = tmp_path / "valid.zip"
    _write_zip(
        archive,
        {
            "blender-test/blender.exe": b"fake executable",
            "blender-test/data/nested.txt": b"nested durable content",
        },
    )
    destination = tmp_path / "third" / "blender"
    original_flush = tool_lock._flush_directory

    def fail_nested_directory(path: Path):
        if path.name == "data":
            raise OSError("simulated nested directory flush failure")
        return original_flush(path)

    monkeypatch.setattr(tool_lock, "_flush_directory", fail_nested_directory)
    with pytest.raises(ToolInstallError, match="filesystem failure"):
        install_locked_archive(
            _tool_for_archive(archive),
            archive,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )
    assert not destination.exists()
    assert not list(destination.parent.glob(".blender-staging-*"))


def test_verify_rejects_executable_tampering_and_runtime_identity_mismatch(tmp_path):
    archive = tmp_path / "valid.zip"
    _write_zip(archive, {"blender-test/blender.exe": b"fake executable"})
    tool = _tool_for_archive(archive)
    destination = tmp_path / "third" / "blender"
    install_locked_archive(
        tool,
        archive,
        destination,
        runtime_verifier=_accept_fake_runtime,
    )

    def wrong_runtime(_tool: LockedTool, _install_root: Path) -> str:
        return "Blender 9.9.9"

    with pytest.raises(ToolInstallError, match="runtime identity"):
        verify_locked_install(tool, destination, runtime_verifier=wrong_runtime)

    (destination / "blender.exe").write_bytes(b"tampered executable")
    with pytest.raises(ToolInstallError, match="executable SHA-256"):
        verify_locked_install(
            tool,
            destination,
            runtime_verifier=_accept_fake_runtime,
        )


def test_download_streams_exact_locked_bytes_into_content_cache(tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    _write_zip(archive, {"blender-test/blender.exe": b"fake executable" * 100})
    payload = archive.read_bytes()
    tool = _tool_for_archive(archive)
    response = _FakeResponse(payload, tool.download_url, content_length=len(payload))
    monkeypatch.setattr(
        tool_lock.urllib.request,
        "build_opener",
        lambda *_handlers: _FakeOpener(response),
    )

    cached = download_locked_archive(tool, tmp_path / "cache")

    assert cached.name == f"{tool.archive_sha256}.zip"
    assert cached.read_bytes() == payload
    assert response.read_sizes
    assert set(response.read_sizes) == {tool_lock.DOWNLOAD_CHUNK_BYTES}


def test_download_rejects_redirect_size_overflow_and_cleans_partials(tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    _write_zip(archive, {"blender-test/blender.exe": b"fake executable"})
    payload = archive.read_bytes()
    tool = _tool_for_archive(archive)
    cache = tmp_path / "cache"

    redirect = _FakeResponse(payload, "https://other.test/blender.zip")
    monkeypatch.setattr(
        tool_lock.urllib.request,
        "build_opener",
        lambda *_handlers: _FakeOpener(redirect),
    )
    with pytest.raises(ToolInstallError, match="final URL"):
        download_locked_archive(tool, cache)
    assert not list(cache.glob("*.part"))
    assert not list(cache.glob("*.zip"))

    overflow = _FakeResponse(payload, tool.download_url)
    monkeypatch.setattr(
        tool_lock.urllib.request,
        "build_opener",
        lambda *_handlers: _FakeOpener(overflow),
    )
    monkeypatch.setattr(tool_lock, "MAX_ARCHIVE_BYTES", len(payload) - 1)
    with pytest.raises(ToolInstallError, match="byte limit"):
        download_locked_archive(tool, cache)
    assert not list(cache.glob("*.part"))
    assert not list(cache.glob("*.zip"))


def test_download_never_overwrites_corrupt_existing_cache(tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    _write_zip(archive, {"blender-test/blender.exe": b"fake executable"})
    tool = _tool_for_archive(archive)
    cache = tmp_path / "cache"
    cache.mkdir()
    cached = cache / f"{tool.archive_sha256}.zip"
    cached.write_bytes(b"corrupt")

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("network must not be used for an existing cache entry")

    monkeypatch.setattr(tool_lock.urllib.request, "build_opener", forbidden_network)
    with pytest.raises(ToolInstallError, match="corrupt"):
        download_locked_archive(tool, cache)
    assert cached.read_bytes() == b"corrupt"


def test_cli_requires_exactly_one_install_mode():
    parser = setup_synthetic_tools._parser()
    assert parser.parse_args(["blender", "--download"]).download is True
    assert parser.parse_args(["blender", "--verify-only"]).verify_only is True
    assert parser.parse_args(["blender", "--archive", "local.zip"]).archive == Path(
        "local.zip"
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["blender", "--download", "--verify-only"])
