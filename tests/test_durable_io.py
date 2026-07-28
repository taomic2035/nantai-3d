from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.durable_io as durable_io
from pipeline.durable_io import (
    BoundPathCleanup,
    DurableIOError,
    atomic_replace,
    bind_directory,
    bound_temporary_directory,
    publish_directory_noreplace,
    publish_file_noreplace,
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


@pytest.mark.parametrize(
    "name",
    ("", ".", "..", "a/b", r"a\b", "C:", "a:b"),
)
def test_bound_directory_rejects_non_component_names(
    tmp_path: Path,
    name: str,
) -> None:
    with bind_directory(tmp_path) as parent:
        with pytest.raises(DurableIOError, match="single path component"):
            parent.create_directory(name)


def test_bound_directory_creates_and_publishes_file_without_replace(
    tmp_path: Path,
) -> None:
    with bind_directory(tmp_path) as parent:
        with parent.create_file(".payload.partial") as candidate:
            candidate.stream.write(b"bound payload\n")
            candidate.flush()
            candidate.publish_noreplace(parent, "payload.bin")

    assert (tmp_path / "payload.bin").read_bytes() == b"bound payload\n"
    assert not (tmp_path / ".payload.partial").exists()


def test_bound_file_publication_refuses_existing_destination(
    tmp_path: Path,
) -> None:
    (tmp_path / "payload.bin").write_bytes(b"existing\n")
    with bind_directory(tmp_path) as parent:
        with parent.create_file(".payload.partial") as candidate:
            candidate.stream.write(b"candidate\n")
            candidate.flush()
            with pytest.raises(OSError):
                candidate.publish_noreplace(parent, "payload.bin")

    assert (tmp_path / "payload.bin").read_bytes() == b"existing\n"
    assert (tmp_path / ".payload.partial").read_bytes() == b"candidate\n"


def test_bound_directory_creates_nested_tree_and_removes_it(
    tmp_path: Path,
) -> None:
    with bind_directory(tmp_path) as parent:
        with parent.create_directory(".private") as private:
            with private.create_directory("nested") as nested:
                with nested.create_file("payload.bin") as payload:
                    payload.stream.write(b"private")
                    payload.flush()
        assert (
            parent.remove_tree(".private")
            is BoundPathCleanup.REMOVED
        )

    assert not (tmp_path / ".private").exists()


def test_bound_directory_publishes_directory_without_replace(
    tmp_path: Path,
) -> None:
    with bind_directory(tmp_path) as parent:
        staging = parent.create_directory(".bundle.staging")
        with staging:
            with staging.create_file("payload.bin") as payload:
                payload.stream.write(b"complete")
                payload.flush()
            staging.publish_noreplace(parent, "bundle")

    assert (tmp_path / "bundle" / "payload.bin").read_bytes() == b"complete"
    assert not (tmp_path / ".bundle.staging").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows lease contract")
def test_windows_bound_directory_lease_blocks_parent_rename(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    with bind_directory(parent):
        with pytest.raises(OSError) as raised:
            parent.rename(tmp_path / "moved")
        assert getattr(raised.value, "winerror", None) in {5, 32}

    parent.rename(tmp_path / "moved")


@pytest.mark.skipif(os.name != "nt", reason="Windows handle rename contract")
def test_windows_bound_file_publish_collision_keeps_both_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "existing.bin").write_bytes(b"existing")
    with bind_directory(tmp_path) as parent:
        with parent.create_file("candidate.bin") as candidate:
            candidate.stream.write(b"candidate")
            candidate.flush()
            with pytest.raises(OSError) as raised:
                candidate.publish_noreplace(parent, "existing.bin")
            assert getattr(raised.value, "winerror", None) == 183

    assert (tmp_path / "candidate.bin").read_bytes() == b"candidate"
    assert (tmp_path / "existing.bin").read_bytes() == b"existing"


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir-fd contract")
def test_posix_bound_operation_survives_lexical_parent_swap(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    original = tmp_path / "original-parent"
    with bind_directory(parent) as bound:
        parent.rename(original)
        parent.mkdir()
        (parent / "sentinel.bin").write_bytes(b"decoy")
        with bound.create_file("payload.bin") as payload:
            payload.stream.write(b"bound")
            payload.flush()

    assert (original / "payload.bin").read_bytes() == b"bound"
    assert (parent / "sentinel.bin").read_bytes() == b"decoy"
    assert not (parent / "payload.bin").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX rmtree capability contract")
def test_posix_cleanup_capability_gap_retains_tree_without_path_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "payload.bin").write_bytes(b"retain")
    monkeypatch.setattr(
        durable_io.shutil.rmtree,
        "avoids_symlink_attacks",
        False,
    )

    with bind_directory(tmp_path) as parent:
        outcome = parent.remove_tree("private")

    assert outcome is BoundPathCleanup.RETAINED
    assert (private / "payload.bin").read_bytes() == b"retain"


def test_bound_temporary_directory_is_cleaned_through_bound_parent(
    tmp_path: Path,
) -> None:
    with bound_temporary_directory(
        prefix="private-",
        parent=tmp_path,
    ) as temporary:
        path = temporary.path
        with temporary.create_file("payload.bin") as payload:
            payload.stream.write(b"private")
            payload.flush()
        assert path.is_dir()

    assert not path.exists()


def test_bound_directory_reopens_existing_file_for_publication(
    tmp_path: Path,
) -> None:
    with bind_directory(tmp_path) as parent:
        with parent.create_file("candidate.bin") as candidate:
            candidate.stream.write(b"candidate")
            candidate.flush()
        with parent.open_file("candidate.bin") as candidate:
            candidate.publish_noreplace(parent, "published.bin")

    assert (tmp_path / "published.bin").read_bytes() == b"candidate"
    assert not (tmp_path / "candidate.bin").exists()


@pytest.mark.parametrize(
    "module_name",
    (
        "production_release_verifier.py",
        "production_release_assets.py",
        "production_release_privacy.py",
        "production_release_builder.py",
    ),
)
def test_production_mutation_modules_have_no_path_authority_fallbacks(
    module_name: str,
) -> None:
    source = (
        Path(__file__).parents[1] / "pipeline" / module_name
    ).read_text(encoding="utf-8")
    forbidden = (
        "matches_real_directory_identity",
        "capture_real_directory_identity",
        "shutil.rmtree",
        ".unlink(",
        ".mkdir(",
        "os.replace(",
        "os.link(",
        "TemporaryDirectory",
    )

    assert not {
        token for token in forbidden if token in source
    }, f"{module_name} contains a path-authority mutation fallback"
