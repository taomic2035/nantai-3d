"""RED tests for bounded-memory streaming artifact digests (I1).

These tests prove that large import artifacts are hashed in bounded chunks
(<= 1 MiB) with lstat/open/fstat before + fstat/path lstat after checks,
rejecting mid-read identity, size, mtime, symlink, and non-regular drift.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

import pipeline.real_scene_import as import_module
from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_import import (
    ImportArtifactBinding,
    RealSceneImportError,
    _artifact_bindings,
    _read_regular_bytes,
    import_real_scene,
    validate_real_scene_import_receipt,
)

_ONE_MIB = 1024 * 1024


def _load_helper_module():
    spec = importlib.util.spec_from_file_location(
        "_rsi_streaming_helpers",
        Path(__file__).resolve().parent / "test_real_scene_import.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_helpers = _load_helper_module()
_write_preview_training_stage = _helpers._write_preview_training_stage


def test_import_artifact_bindings_hash_large_ply_in_bounded_chunks(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "import"
    (root / "inputs").mkdir(parents=True)
    (root / "contracts").mkdir(parents=True)
    (root / "web" / "chunks").mkdir(parents=True)

    large_payload = b"\x00" * (3 * _ONE_MIB + 1)
    (root / "inputs/source.ply").write_bytes(large_payload)
    (root / "inputs/normalized.ply").write_bytes(b"normalized")
    (root / "contracts/registration.json").write_bytes(b"{}")
    (root / "contracts/splat-input.json").write_bytes(b"{}")
    (root / "web/recon_manifest.json").write_bytes(b"{}")
    (root / "web/chunks/chunks.json").write_bytes(b"{}")
    (root / "import-integrity.json").write_bytes(b"{}")

    read_sizes: list[int] = []
    original_read = os.read

    def tracking_read(fd, n):
        read_sizes.append(n)
        return original_read(fd, n)

    monkeypatch.setattr(os, "read", tracking_read)

    bindings = _artifact_bindings(root)

    assert all(size <= _ONE_MIB for size in read_sizes)
    assert any(size >= _ONE_MIB for size in read_sizes)

    source_binding = next(b for b in bindings if b.path == "inputs/source.ply")
    assert source_binding.byte_length == len(large_payload)
    assert source_binding.sha256 == hashlib.sha256(large_payload).hexdigest()


def test_receipt_revalidation_hashes_large_bound_ply_in_bounded_chunks(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_preview_training_stage(training_root)
    monkeypatch.setattr(
        import_module,
        "verify_training_job_bundle",
        lambda path: fixture.verified_bundle,
    )
    monkeypatch.setattr(
        import_module,
        "load_training_job_input_bytes",
        lambda bundle: fixture.input_bytes,
    )

    output_root = tmp_path / "import"
    receipt = import_real_scene(
        training_root,
        output_root,
        source_role="internal-canary",
        chunk_size=2.0,
    )

    large_payload = b"\x00" * (3 * _ONE_MIB + 1)
    source_ply_path = output_root / receipt.source_ply_path
    source_ply_path.write_bytes(large_payload)

    new_binding = ImportArtifactBinding(
        path=receipt.source_ply_path,
        byte_length=len(large_payload),
        sha256=hashlib.sha256(large_payload).hexdigest(),
    )
    updated_artifacts = tuple(
        new_binding if binding.path == receipt.source_ply_path else binding
        for binding in receipt.artifacts
    )
    updated_receipt = receipt.model_copy(update={"artifacts": updated_artifacts})
    receipt_path = output_root / "import-receipt.json"
    receipt_path.write_bytes(canonical_model_bytes(updated_receipt))

    read_sizes: list[int] = []
    original_read = os.read

    def tracking_read(fd, n):
        read_sizes.append(n)
        return original_read(fd, n)

    monkeypatch.setattr(os, "read", tracking_read)

    validate_real_scene_import_receipt(receipt_path, output_root)

    assert all(size <= _ONE_MIB for size in read_sizes)
    assert any(size >= _ONE_MIB for size in read_sizes)


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows os.open lacks FILE_SHARE_DELETE; path-lstat-after "
    "drift is covered by the size/mtime test and by POSIX CI.",
)
def test_streaming_artifact_digest_rejects_mid_read_identity_change(
    tmp_path,
    monkeypatch,
):
    from pipeline.real_scene_import import _stream_regular_digest

    large_file = tmp_path / "large.bin"
    large_payload = b"\x00" * (3 * _ONE_MIB + 1)
    large_file.write_bytes(large_payload)

    original_read = os.read
    call_count = {"n": 0}

    def intercepting_read(fd, n):
        result = original_read(fd, n)
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Unlink and recreate the path so the open fd keeps the original
            # inode but the path lstat after read observes a different
            # inode/identity.  Requires FILE_SHARE_DELETE on the open handle.
            os.unlink(large_file)
            large_file.write_bytes(b"\x01" * (3 * _ONE_MIB + 1))
        return result

    monkeypatch.setattr(os, "read", intercepting_read)

    with pytest.raises(RealSceneImportError, match="changed while being read"):
        _stream_regular_digest(large_file, label="test artifact")


def test_streaming_artifact_digest_rejects_mid_read_size_or_mtime_change(
    tmp_path,
    monkeypatch,
):
    from pipeline.real_scene_import import _stream_regular_digest

    large_file = tmp_path / "large.bin"
    large_payload = b"\x00" * (3 * _ONE_MIB + 1)
    large_file.write_bytes(large_payload)

    original_read = os.read
    call_count = {"n": 0}

    def intercepting_read(fd, n):
        result = original_read(fd, n)
        call_count["n"] += 1
        if call_count["n"] == 1:
            with open(large_file, "ab") as handle:
                handle.write(b"\x00" * 1024)
        return result

    monkeypatch.setattr(os, "read", intercepting_read)

    with pytest.raises(RealSceneImportError, match="changed while being read"):
        _stream_regular_digest(large_file, label="test artifact")


def test_streaming_artifact_digest_rejects_linklike_or_nonregular_member(tmp_path):
    from pipeline.real_scene_import import _stream_regular_digest

    target = tmp_path / "target.bin"
    target.write_bytes(b"data")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("symlink creation requires privilege on Windows")
        raise

    with pytest.raises(RealSceneImportError, match="missing or link-like"):
        _stream_regular_digest(link, label="symlink artifact")

    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises(RealSceneImportError, match="missing or link-like"):
        _stream_regular_digest(directory, label="directory artifact")


def test_streaming_artifact_digest_preserves_canonical_receipt_bytes(tmp_path):
    from pipeline.real_scene_import import _stream_regular_digest

    payload = b"\x00" * (2 * _ONE_MIB + 123)
    path = tmp_path / "artifact.bin"
    path.write_bytes(payload)

    raw_bytes = _read_regular_bytes(path, label="test artifact")
    streaming_length, streaming_sha = _stream_regular_digest(
        path,
        label="test artifact",
    )

    assert streaming_length == len(raw_bytes)
    assert streaming_sha == hashlib.sha256(raw_bytes).hexdigest()
