"""Ingest registry and concurrency-snapshot tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline import studio_jobs
from pipeline.studio_jobs import (
    CommandRegistry,
    JobContractError,
    build_concurrency_snapshot,
)

VALID_PARAMS = {
    "fps": 2,
    "max_frames": 300,
    "blur_threshold": 80,
    "max_long_edge": 2560,
}


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "input/photo.jpg").write_bytes(b"photo")
    return root


def test_registry_accepts_only_ingest_and_strict_bounded_parameters(tmp_path):
    registry = CommandRegistry(_project(tmp_path))
    parsed = registry.parse("ingest", VALID_PARAMS)
    assert parsed.model_dump() == VALID_PARAMS

    with pytest.raises(JobContractError, match="unknown command"):
        registry.parse("reconstruct", {})
    with pytest.raises(ValidationError, match="extra"):
        registry.parse("ingest", {**VALID_PARAMS, "output": "elsewhere"})
    with pytest.raises(ValidationError):
        registry.parse("ingest", {**VALID_PARAMS, "fps": float("nan")})


def test_ingest_invocation_has_only_fixed_paths_argv_cwd_and_environment(tmp_path):
    root = _project(tmp_path).resolve()
    registry = CommandRegistry(root)
    invocation = registry.build_invocation(
        "run-001", registry.parse("ingest", VALID_PARAMS),
    )

    stage = root / ".nantai-studio/work/run-001/photos"
    assert invocation.argv == (
        sys.executable,
        "-m", "pipeline.ingest",
        "--input", str(root / "input"),
        "--output", str(stage),
        "--fps", "2.0",
        "--max-frames", "300",
        "--blur-threshold", "80.0",
        "--max-long-edge", "2560",
    )
    assert invocation.cwd == root
    assert invocation.input_dir == root / "input"
    assert invocation.stage_dir == stage
    assert invocation.target_dir == root / "photos"
    assert invocation.shell is False
    assert invocation.environment["PYTHONUTF8"] == "1"
    assert "NANTAI_TEST_SECRET" not in invocation.environment
    assert set(invocation.environment).issubset({
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
        "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONPATH",
    })
    assert invocation.environment["PYTHONPATH"] == str(Path(__file__).parents[1])


def test_snapshot_covers_supported_inputs_and_formal_target_only(tmp_path):
    root = _project(tmp_path)
    (root / "input/clip.mp4").write_bytes(b"video")
    (root / "input/notes.txt").write_text("ignored", encoding="utf-8")
    (root / ".nantai-studio").mkdir()
    (root / ".nantai-studio/heartbeat").write_text("one", encoding="utf-8")

    first = build_concurrency_snapshot(root)
    (root / ".nantai-studio/heartbeat").write_text("two", encoding="utf-8")
    (root / "unrelated.txt").write_text("changed", encoding="utf-8")
    second = build_concurrency_snapshot(root)

    assert first == second
    assert [item.path for item in first.inputs] == ["clip.mp4", "photo.jpg"]
    assert [item.kind for item in first.inputs] == ["video", "photo"]
    assert first.target.state == "absent"


def test_snapshot_target_tree_digest_changes_with_formal_bytes(tmp_path):
    root = _project(tmp_path)
    (root / "photos").mkdir()
    payload = root / "photos/photo.jpg"
    payload.write_bytes(b"old")
    first = build_concurrency_snapshot(root)

    payload.write_bytes(b"new")
    second = build_concurrency_snapshot(root)

    assert first.input_digest == second.input_digest
    assert first.target.state == second.target.state == "tree"
    assert first.target.digest != second.target.digest


def test_snapshot_rejects_input_mutation_during_hash(tmp_path, monkeypatch):
    root = _project(tmp_path)
    target = root / "input/photo.jpg"
    real_hash = studio_jobs.sha256_file

    def hash_then_mutate(path):
        digest = real_hash(path)
        if Path(path) == target:
            target.write_bytes(b"x" * target.stat().st_size)
        return digest

    monkeypatch.setattr(studio_jobs, "sha256_file", hash_then_mutate)
    with pytest.raises(JobContractError, match="changed while hashing"):
        build_concurrency_snapshot(root)


def test_snapshot_rejects_links_in_managed_trees(tmp_path):
    root = _project(tmp_path)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    link = root / "input/link.jpg"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")

    with pytest.raises(JobContractError, match="link|symlink"):
        build_concurrency_snapshot(root)


def test_snapshot_rejects_non_directory_formal_target(tmp_path):
    root = _project(tmp_path)
    (root / "photos").write_bytes(b"not-a-directory")
    with pytest.raises(JobContractError, match="target|directory"):
        build_concurrency_snapshot(root)
