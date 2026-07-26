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
    run_real_sfm,
)
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    GeoAlignment,
    Handedness,
    MetricStatus,
)
from pipeline.registration import mock_register
from pipeline.registration_quality import RegistrationQualityPolicy
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


def _policy() -> RegistrationQualityPolicy:
    return RegistrationQualityPolicy(
        min_registered_count=2,
        min_registered_ratio=1.0,
        min_session_coverage_ratio=1.0,
        max_unregistered_consecutive_run=0,
        min_largest_connected_model_share=1.0,
    )


def _write_registration(path: Path, registration) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        registration.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_sparse_model(
    workspace: Path,
    model_index: int,
    images: tuple[str, ...],
) -> None:
    model = workspace / "sparse" / str(model_index)
    model.mkdir(parents=True)
    (model / "cameras.txt").write_text(
        "1 PINHOLE 640 480 500 500 320 240\n",
        encoding="utf-8",
    )
    rows: list[str] = []
    for image_id, image in enumerate(images, start=1):
        rows.extend(
            [
                f"{image_id} 1 0 0 0 0 0 0 1 {image}",
                "0 0 -1",
            ]
        )
    (model / "images.txt").write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    (model / "points3D.txt").write_text(
        "1 0 0 0 255 255 255 0.1 1 0\n",
        encoding="utf-8",
    )


def test_sfm_rejects_mock_even_when_counts_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, workspace = _write_verified_source(tmp_path / "source")
    prepared = prepare_real_capture(source, workspace, tmp_path / "run")

    def fake_register(photos_dir, out_json, **kwargs):
        del kwargs
        registration = mock_register(photos_dir)
        _write_registration(Path(out_json), registration)
        return registration

    monkeypatch.setattr(
        "pipeline.real_scene_capture.register",
        fake_register,
    )
    result = run_real_sfm(prepared, tmp_path / "run", _policy())

    assert result.registration.engine == "mock"
    assert result.quality.quality_accepted is True
    assert result.quality.training_allowed is False
    assert result.sparse_enumeration is None


def test_sfm_accepts_only_matching_colmap_model_capture_and_poses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, workspace = _write_verified_source(tmp_path / "source")
    run_root = tmp_path / "run"
    prepared = prepare_real_capture(source, workspace, run_root)
    image_names = tuple(
        payload.logical_path for payload in prepared.capture.manifest.payloads
    )

    def fake_register(photos_dir, out_json, *, workspace, **kwargs):
        del kwargs
        registration = mock_register(photos_dir).model_copy(
            update={
                "engine": "colmap",
                "pose_frame": CoordinateFrame(
                    frame_id="sfm-local",
                    handedness=Handedness.RIGHT,
                    axes=AxisConvention.SFM_ARBITRARY,
                    units=CoordinateUnits.ARBITRARY,
                    metric_status=MetricStatus.ARBITRARY,
                    geo_aligned=GeoAlignment.UNALIGNED,
                    provenance=FrameProvenance.SFM,
                    evidence=["colmap-joint-model"],
                ),
                "alignment_status": AlignmentStatus.UNALIGNED,
            }
        )
        _write_sparse_model(Path(workspace), 0, image_names)
        _write_registration(Path(out_json), registration)
        return registration

    monkeypatch.setattr(
        "pipeline.real_scene_capture.register",
        fake_register,
    )
    result = run_real_sfm(prepared, run_root, _policy())

    assert result.registration.engine == "colmap"
    assert result.sparse_enumeration is not None
    assert result.sparse_enumeration.selected_model_index == 0
    assert result.quality.training_allowed is True


def test_sfm_blocks_when_selected_sparse_model_differs_from_poses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, workspace = _write_verified_source(tmp_path / "source")
    run_root = tmp_path / "run"
    prepared = prepare_real_capture(source, workspace, run_root)
    image_names = tuple(
        payload.logical_path for payload in prepared.capture.manifest.payloads
    )

    def fake_register(photos_dir, out_json, *, workspace, **kwargs):
        del kwargs
        registration = mock_register(photos_dir).model_copy(
            update={"engine": "colmap"}
        )
        _write_sparse_model(Path(workspace), 0, image_names[:1])
        _write_sparse_model(Path(workspace), 1, image_names)
        mismatched = registration.model_copy(
            update={"poses": registration.poses[:1]}
        )
        _write_registration(Path(out_json), mismatched)
        return mismatched

    monkeypatch.setattr(
        "pipeline.real_scene_capture.register",
        fake_register,
    )
    with pytest.raises(RealSceneCaptureError, match="selected sparse model"):
        run_real_sfm(prepared, run_root, _policy())
