from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.durable_io import (
    atomic_replace,
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
