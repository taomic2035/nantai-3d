from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.durable_io as durable_io
from pipeline.durable_io import (
    DurableIOError,
    atomic_replace,
    publish_directory_noreplace,
    publish_file_noreplace,
    write_file_atomic,
)


def test_atomic_replace_publishes_complete_new_bytes(tmp_path: Path) -> None:
    source = tmp_path / "candidate.tmp"
    destination = tmp_path / "journal.json"
    source.write_bytes(b"new canonical bytes\n")
    destination.write_bytes(b"old canonical bytes\n")

    atomic_replace(source, destination)

    assert destination.read_bytes() == b"new canonical bytes\n"
    assert not source.exists()


def test_publish_file_noreplace_refuses_to_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "candidate.tmp"
    destination = tmp_path / "result.zip"
    source.write_bytes(b"candidate")
    destination.write_bytes(b"existing")

    with pytest.raises(OSError):
        publish_file_noreplace(source, destination)

    assert source.read_bytes() == b"candidate"
    assert destination.read_bytes() == b"existing"


def test_publish_directory_noreplace_moves_complete_tree(tmp_path: Path) -> None:
    source = tmp_path / ".bundle.staging"
    destination = tmp_path / "bundle"
    source.mkdir()
    (source / "training-job.zip").write_bytes(b"complete")

    publish_directory_noreplace(source, destination)

    assert (destination / "training-job.zip").read_bytes() == b"complete"
    assert not source.exists()


def test_publish_directory_noreplace_rejects_junction_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / ".bundle.staging"
    destination = tmp_path / "bundle"
    source.mkdir()
    (source / "training-job.zip").write_bytes(b"complete")
    original = getattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == source or original(self),
        raising=False,
    )

    with pytest.raises(
        DurableIOError,
        match="real directory",
    ):
        publish_directory_noreplace(source, destination)

    assert source.is_dir()
    assert not destination.exists()


def test_linklike_detects_reparse_attribute_without_is_junction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "reparse-directory"
    source.mkdir()
    observed = source.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == source:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=getattr(
                    stat,
                    "FILE_ATTRIBUTE_REPARSE_POINT",
                    0x400,
                ),
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    assert durable_io._is_linklike(source) is True


def test_linklike_propagates_lstat_error_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "unreadable"
    source.mkdir()
    original_lstat = Path.lstat

    def denied_lstat(path: Path):
        if path == source:
            raise PermissionError("lstat denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    with pytest.raises(PermissionError, match="lstat denied"):
        durable_io._is_linklike(source)


def test_path_chain_checks_root_and_each_existing_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protected_root = tmp_path / "protected"
    ancestor = protected_root / "one"
    leaf = ancestor / "two" / "payload.bin"
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(b"payload")
    observed = ancestor.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == ancestor:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=getattr(
                    stat,
                    "FILE_ATTRIBUTE_REPARSE_POINT",
                    0x400,
                ),
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    assert durable_io.first_linklike_path(protected_root, leaf) == ancestor


def test_path_chain_checks_protected_root_itself(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protected_root = tmp_path / "protected"
    protected_root.mkdir()
    observed = protected_root.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == protected_root:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=getattr(
                    stat,
                    "FILE_ATTRIBUTE_REPARSE_POINT",
                    0x400,
                ),
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    assert (
        durable_io.first_linklike_path(protected_root, protected_root)
        == protected_root
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_directory_identity_recheck_rejects_parent_swap_to_junction(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    identity = durable_io.capture_real_directory_identity(parent)
    original = tmp_path / "parent-original"
    parent.rename(original)
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(parent), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        original.rename(parent)
        pytest.skip(f"junction creation unavailable: {created.stderr}")
    try:
        assert (
            durable_io.matches_real_directory_identity(parent, identity)
            is False
        )
    finally:
        removed = subprocess.run(
            ["cmd", "/c", "rmdir", str(parent)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert removed.returncode == 0, removed.stderr
        original.rename(parent)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_linklike_detects_real_windows_junction_without_is_junction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "junction-target"
    target.mkdir()
    junction = tmp_path / "junction"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr}")
    try:
        monkeypatch.setattr(
            Path,
            "is_junction",
            lambda _path: False,
            raising=False,
        )

        assert durable_io._is_linklike(junction) is True
    finally:
        removed = subprocess.run(
            ["cmd", "/c", "rmdir", str(junction)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert removed.returncode == 0, removed.stderr


def test_write_file_atomic_publishes_payload_and_overwrites_existing(
    tmp_path: Path,
) -> None:
    """GREEN: write_file_atomic must publish exact bytes, replacing any prior file."""
    destination = tmp_path / "trust_root.json"
    destination.write_bytes(b"old bytes\n")

    write_file_atomic(destination, b"new canonical bytes\n")

    assert destination.read_bytes() == b"new canonical bytes\n"


def test_write_file_atomic_does_not_follow_symlink_redirect(
    tmp_path: Path,
) -> None:
    """RED->GREEN: write_file_atomic must replace a pre-placed symlink, not write through it.

    Path.write_bytes follows symlinks, so a TOCTOU attacker could redirect the
    write to an arbitrary location.  write_file_atomic stages + fsync +
    atomic_replace, which replaces the symlink itself.
    """
    destination = tmp_path / "trust_root.json"
    attacker = tmp_path / "attacker.json"
    attacker.write_bytes(b"attacker-original\n")
    try:
        destination.symlink_to(attacker)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    write_file_atomic(destination, b"real trust root bytes\n")

    assert not destination.is_symlink(), (
        "write_file_atomic wrote through the symlink instead of replacing it"
    )
    assert destination.read_bytes() == b"real trust root bytes\n"
    assert attacker.read_bytes() == b"attacker-original\n", (
        "write_file_atomic wrote through the symlink redirect"
    )
