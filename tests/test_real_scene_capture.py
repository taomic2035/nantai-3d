from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pipeline.real_dataset import (
    DatasetLock,
    DatasetLockEntry,
    DatasetReceipt,
    DatasetReceiptEntry,
    HfDatasetSource,
    canonical_model_bytes,
)
from pipeline.real_scene_capture import (
    RealSceneCaptureError,
    prepare_real_capture,
)
from pipeline.studio_revisions import canonical_manifest_bytes

_REVISION = "4" * 40


def _write_verified_source(
    root: Path,
    *,
    include_nested_capture: bool = False,
) -> tuple[HfDatasetSource, Path]:
    files = {
        "poster/images/frame_a.png": b"image-a",
        "poster/images/frame_b.png": b"image-b",
        "poster/images_2/frame_a.png": b"derived-image",
        "poster/transforms.json": b"{}",
    }
    if include_nested_capture:
        files["poster/images/nested/frame_c.png"] = b"nested-image"

    source = HfDatasetSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="poster",
        role="internal-canary",
        source_kind="hf-dataset",
        repository="owner/repo",
        repository_revision=_REVISION,
        subtree="poster",
        capture_subtree="poster/images",
        declared_file_count=len(files),
        declared_total_bytes=sum(len(payload) for payload in files.values()),
        license_status="not-declared",
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    dataset_root = root / "dataset"
    lock_entries: list[DatasetLockEntry] = []
    receipt_entries: list[DatasetReceiptEntry] = []
    for relative_path, payload in sorted(files.items()):
        target = dataset_root.joinpath(*relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        lock_entries.append(
            DatasetLockEntry(
                relative_path=relative_path,
                expected_bytes=len(payload),
                server_identity=f"lfs-sha256:{digest}",
            )
        )
        receipt_entries.append(
            DatasetReceiptEntry(
                relative_path=relative_path,
                expected_bytes=len(payload),
                server_identity=f"lfs-sha256:{digest}",
                actual_bytes=len(payload),
                actual_sha256=digest,
            )
        )

    source_sha = hashlib.sha256(canonical_model_bytes(source)).hexdigest()
    lock = DatasetLock(
        schema="nantai.dataset-lock.v1",
        source_sha256=source_sha,
        repository=source.repository,
        repository_revision=source.repository_revision,
        entries=tuple(lock_entries),
    )
    receipt = DatasetReceipt(
        schema="nantai.dataset-receipt.v1",
        source_sha256=source_sha,
        lock_sha256=hashlib.sha256(canonical_model_bytes(lock)).hexdigest(),
        entries=tuple(receipt_entries),
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "dataset-lock.json").write_bytes(canonical_model_bytes(lock))
    (root / "dataset-receipt.json").write_bytes(canonical_model_bytes(receipt))
    (root / "dataset-policy.json").write_bytes(canonical_model_bytes(source))
    return source, root


def test_canary_selects_only_direct_original_images(tmp_path: Path) -> None:
    source, workspace = _write_verified_source(tmp_path / "source")
    prepared = prepare_real_capture(
        source,
        workspace,
        tmp_path / "run",
    )

    assert prepared.selected_paths == (
        "poster/images/frame_a.png",
        "poster/images/frame_b.png",
    )
    assert prepared.capture.manifest.output_count == 2
    assert prepared.capture.manifest.synthetic is False
    assert {
        payload.logical_path for payload in prepared.capture.manifest.payloads
    } == {"frame_a.png", "frame_b.png"}
    assert not any(
        "images_2" in path for path in prepared.selected_paths
    )


def test_capture_revision_is_derived_from_exact_ingest_manifest(
    tmp_path: Path,
) -> None:
    source, workspace = _write_verified_source(tmp_path / "source")
    prepared = prepare_real_capture(source, workspace, tmp_path / "run")
    ingest_bytes = (
        prepared.capture.bundle / "ingest_manifest.json"
    ).read_bytes()
    expected_revision = (
        "capture-" + hashlib.sha256(ingest_bytes).hexdigest()[:32]
    )

    assert prepared.capture.manifest.revision_id == expected_revision
    assert prepared.capture.manifest_digest == hashlib.sha256(
        canonical_manifest_bytes(prepared.capture.manifest)
    ).hexdigest()


def test_capture_rejects_nested_members_in_capture_subtree(
    tmp_path: Path,
) -> None:
    source, workspace = _write_verified_source(
        tmp_path / "source",
        include_nested_capture=True,
    )
    with pytest.raises(RealSceneCaptureError, match="nested"):
        prepare_real_capture(source, workspace, tmp_path / "run")


def test_capture_revalidates_live_source_before_materializing(
    tmp_path: Path,
) -> None:
    source, workspace = _write_verified_source(tmp_path / "source")
    target = workspace / "dataset/poster/images/frame_a.png"
    target.write_bytes(b"tampered")

    with pytest.raises(RealSceneCaptureError, match="sha256|length"):
        prepare_real_capture(source, workspace, tmp_path / "run")
    assert not (tmp_path / "run/capture/ingest").exists()


def test_capture_requires_absent_output_boundary(tmp_path: Path) -> None:
    source, workspace = _write_verified_source(tmp_path / "source")
    run_root = tmp_path / "run"
    occupied = run_root / "capture"
    occupied.mkdir(parents=True)
    (occupied / "foreign.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(RealSceneCaptureError, match="absent"):
        prepare_real_capture(source, workspace, run_root)
    assert (occupied / "foreign.txt").read_text(encoding="utf-8") == (
        "do not overwrite"
    )
