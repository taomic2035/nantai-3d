from __future__ import annotations

import hashlib
import json
import stat
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.real_scene_capture as real_scene_capture
from pipeline.real_dataset import (
    CaptureRightsReceipt,
    DatasetLock,
    DatasetLockEntry,
    DatasetReceipt,
    DatasetReceiptEntry,
    HfDatasetSource,
    LocalCaptureSource,
    canonical_model_bytes,
)
from pipeline.real_dataset_fetch import DatasetDownloadError
from pipeline.real_scene_capture import (
    RealSceneCaptureError,
    prepare_local_capture,
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


def _local_rights() -> CaptureRightsReceipt:
    return CaptureRightsReceipt(
        schema="nantai.capture-rights-receipt.v1",
        dataset_id="private-courtyard",
        operator="Nantai operator",
        capture_scope="courtyard acceptance capture",
        effective_date=date(2026, 7, 26),
        processing_purposes=("3d-reconstruction",),
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )


def _local_source(rights: CaptureRightsReceipt) -> LocalCaptureSource:
    return LocalCaptureSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id=rights.dataset_id,
        role="production-acceptance",
        source_kind="local-capture",
        rights_receipt_sha256=hashlib.sha256(
            canonical_model_bytes(rights)
        ).hexdigest(),
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )


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

    with pytest.raises(RealSceneCaptureError, match="dataset verification failed"):
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


def test_local_capture_binds_private_media_and_rights_without_paths(
    tmp_path: Path,
) -> None:
    media = tmp_path / "private-media"
    (media / "session-a").mkdir(parents=True)
    (media / "session-a" / "frame-a.png").write_bytes(b"image-a")
    (media / "frame-b.jpg").write_bytes(b"image-b")
    rights = _local_rights()
    source = _local_source(rights)

    prepared = prepare_local_capture(
        source,
        media,
        rights,
        tmp_path / "run",
    )

    assert prepared.selected_paths == (
        "frame-b.jpg",
        "session-a/frame-a.png",
    )
    assert prepared.capture.manifest.synthetic is False
    assert prepared.source_sha256 == hashlib.sha256(
        canonical_model_bytes(source)
    ).hexdigest()
    assert prepared.dataset_receipt_sha256 == (
        prepared.capture.manifest.ingest_manifest_sha256
    )
    portable_evidence = (
        prepared.capture.bundle / "manifest.json"
    ).read_text(encoding="utf-8")
    assert str(media) not in portable_evidence


def test_local_capture_rejects_unbound_rights_before_ingest(
    tmp_path: Path,
) -> None:
    media = tmp_path / "private-media"
    media.mkdir()
    (media / "frame.png").write_bytes(b"image")
    rights = _local_rights()
    mismatched = rights.model_copy(update={"operator": "Other operator"})

    with pytest.raises(RealSceneCaptureError, match="rights"):
        prepare_local_capture(
            _local_source(rights),
            media,
            mismatched,
            tmp_path / "run",
        )

    assert not (tmp_path / "run" / "capture").exists()


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
                    evidence=[
                        "colmap-joint-model",
                        "colmap.runtime.v1="
                        + json.dumps(
                            {
                                "binary_name": "colmap",
                                "binary_sha256": "a" * 64,
                                "engine_version": "COLMAP 4.1.0",
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ],
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


def test_sfm_quality_report_binds_exact_colmap_version(
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
                    evidence=[
                        "colmap-joint-model",
                        "colmap.runtime.v1="
                        + json.dumps(
                            {
                                "binary_name": "colmap",
                                "binary_sha256": "a" * 64,
                                "engine_version": "COLMAP 4.1.0",
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ],
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
    monkeypatch.setattr(
        "pipeline.real_scene_capture.colmap_version",
        lambda: pytest.fail(
            "run_real_sfm must not re-probe COLMAP after registration"
        ),
        raising=False,
    )

    result = run_real_sfm(prepared, run_root, _policy())

    assert result.quality.engine_version == "COLMAP 4.1.0"


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


def _stat_with_reparse(observed):
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_ctime_ns=observed.st_ctime_ns,
        st_file_attributes=getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ),
    )


def test_source_media_hash_rejects_descriptor_reparse_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "capture.bin"
    source.write_bytes(b"capture")
    real_fstat = real_scene_capture.os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        observed = real_fstat(descriptor)
        calls += 1
        if calls == 2:
            return _stat_with_reparse(observed)
        return observed

    monkeypatch.setattr(real_scene_capture.os, "fstat", drifting_fstat)

    with pytest.raises(RealSceneCaptureError, match="changed while hashing"):
        real_scene_capture._sha256_file(source)


def test_source_media_hash_rejects_early_eof(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "capture.bin"
    source.write_bytes(b"capture")
    real_fdopen = real_scene_capture.os.fdopen

    class EarlyEofStream:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self._read_once = False

        def fileno(self):
            return self._wrapped.fileno()

        def read(self, size=-1):
            if self._read_once:
                return b""
            self._read_once = True
            return self._wrapped.read(min(size, 1))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return self._wrapped.__exit__(exc_type, exc, traceback)

    def early_fdopen(descriptor, *args, **kwargs):
        return EarlyEofStream(real_fdopen(descriptor, *args, **kwargs))

    monkeypatch.setattr(real_scene_capture.os, "fdopen", early_fdopen)

    with pytest.raises(RealSceneCaptureError, match="changed while hashing"):
        real_scene_capture._sha256_file(source)


def test_source_media_hash_hides_operating_system_error_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "capture.bin"
    source.write_bytes(b"capture")
    private_detail = r"D:\private-capture\secret-token"

    def fail_open(*args, **kwargs):
        del args, kwargs
        raise OSError(private_detail)

    monkeypatch.setattr(real_scene_capture.os, "open", fail_open)

    with pytest.raises(RealSceneCaptureError) as captured:
        real_scene_capture._sha256_file(source)

    assert str(captured.value) == "source media cannot be read"
    assert private_detail not in str(captured.value)


def test_sha256_file_rejects_path_reparse_point(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: a path-level reparse point must be rejected before open."""
    source = tmp_path / "capture.bin"
    source.write_bytes(b"capture")
    original_lstat = Path.lstat

    def reparse_lstat(path):
        observed = original_lstat(path)
        return _stat_with_reparse(observed) if path == source else observed

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    with pytest.raises(
        RealSceneCaptureError,
        match="source media is not a regular file",
    ):
        real_scene_capture._sha256_file(source)


def test_sha256_file_never_uses_path_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "capture.bin"
    payload = b"capture"
    source.write_bytes(payload)

    def reject_path_open(*args, **kwargs):
        del args, kwargs
        pytest.fail("_sha256_file must use its controlled descriptor")

    monkeypatch.setattr(Path, "open", reject_path_open)

    measured, sha256 = real_scene_capture._sha256_file(source)

    assert measured == len(payload)
    assert sha256 == hashlib.sha256(payload).hexdigest()


# ============================================================
# RED → GREEN: stable manifest read boundary
# ============================================================


def _stat_with_reparse(observed):
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_ctime_ns=observed.st_ctime_ns,
        st_file_attributes=getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ),
    )


def test_stable_read_bytes_rejects_oversized_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b"x" * 100)
    original_lstat = Path.lstat

    def oversized_lstat(path):
        observed = original_lstat(path)
        if path == evidence:
            return SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_mode=observed.st_mode,
                st_size=real_scene_capture._MAX_MANIFEST_BYTES + 1,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
                st_file_attributes=getattr(observed, "st_file_attributes", 0),
            )
        return observed

    monkeypatch.setattr(Path, "lstat", oversized_lstat)
    with pytest.raises(RealSceneCaptureError, match="bounded regular file"):
        real_scene_capture._stable_read_bytes(
            evidence,
            label="test manifest",
        )


def test_stable_read_bytes_rejects_descriptor_after_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import os as os_module

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')
    original_fstat = os_module.fstat
    calls = 0

    def drifting_fstat(fd):
        nonlocal calls
        calls += 1
        observed = original_fstat(fd)
        return _stat_with_reparse(observed) if calls == 2 else observed

    monkeypatch.setattr(real_scene_capture.os, "fstat", drifting_fstat)
    with pytest.raises(
        RealSceneCaptureError, match="changed while being read"
    ):
        real_scene_capture._stable_read_bytes(
            evidence,
            label="test manifest",
        )
    assert calls == 2


def test_stable_read_bytes_rejects_path_after_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')
    original_lstat = Path.lstat
    evidence_calls = [0]

    def swapping_lstat(self):
        observed = original_lstat(self)
        if self == evidence:
            evidence_calls[0] += 1
            # first_linklike_path calls lstat once (call 1),
            # before = path.lstat() is call 2,
            # after = path.lstat() is call 3 — swap here.
            if evidence_calls[0] >= 3:
                return SimpleNamespace(
                    st_dev=observed.st_dev,
                    st_ino=observed.st_ino + 1,
                    st_mode=observed.st_mode,
                    st_size=observed.st_size,
                    st_mtime_ns=observed.st_mtime_ns,
                    st_ctime_ns=observed.st_ctime_ns,
                    st_file_attributes=getattr(
                        observed, "st_file_attributes", 0
                    ),
                )
        return observed

    monkeypatch.setattr(Path, "lstat", swapping_lstat)
    with pytest.raises(
        RealSceneCaptureError, match="changed while being read"
    ):
        real_scene_capture._stable_read_bytes(
            evidence,
            label="test manifest",
        )


def test_stable_read_bytes_rejects_symlink(tmp_path: Path) -> None:
    import os as os_module

    if not hasattr(os_module, "symlink"):
        pytest.skip("symlinks are unavailable")
    target = tmp_path / "real.json"
    target.write_bytes(b'{"valid":true}')
    link = tmp_path / "manifest.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    with pytest.raises(
        RealSceneCaptureError, match="bounded regular file"
    ):
        real_scene_capture._stable_read_bytes(
            link,
            label="test manifest",
        )


def test_stable_read_bytes_oserror_does_not_leak_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')

    def raising_open(path, flags):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(real_scene_capture.os, "open", raising_open)
    with pytest.raises(
        RealSceneCaptureError, match="cannot be read"
    ) as exc_info:
        real_scene_capture._stable_read_bytes(
            evidence,
            label="test manifest",
        )
    assert str(tmp_path) not in str(exc_info.value)


def test_stable_read_bytes_never_uses_path_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    evidence = tmp_path / "manifest.json"
    payload = b'{"valid":true}'
    evidence.write_bytes(payload)

    def reject_path_open(*args, **kwargs):
        del args, kwargs
        pytest.fail(
            "_stable_read_bytes must use its controlled descriptor"
        )

    monkeypatch.setattr(Path, "open", reject_path_open)

    result = real_scene_capture._stable_read_bytes(
        evidence,
        label="test manifest",
    )
    assert result == payload


# ============================================================
# RED → GREEN: ancestor reparse / junction bypass
# ============================================================


def test_sha256_file_rejects_ancestor_reparse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _sha256_file must reject a reparse-point ancestor.

    Without first_linklike_path, a junction in the parent chain would
    redirect the leaf lstat to an untrusted tree.
    """

    source = tmp_path / "capture.bin"
    source.write_bytes(b"capture")

    sentinel = tmp_path / "ancestor-reparse"

    def fake_first_linklike_path(root, leaf):
        return sentinel

    monkeypatch.setattr(
        real_scene_capture,
        "first_linklike_path",
        fake_first_linklike_path,
    )

    with pytest.raises(
        RealSceneCaptureError,
        match="source media|redirected|unsafe",
    ):
        real_scene_capture._sha256_file(source)


def test_stable_read_bytes_rejects_ancestor_reparse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _stable_read_bytes must reject a reparse-point ancestor."""

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')

    sentinel = tmp_path / "ancestor-reparse"

    def fake_first_linklike_path(root, leaf):
        return sentinel

    monkeypatch.setattr(
        real_scene_capture,
        "first_linklike_path",
        fake_first_linklike_path,
    )

    with pytest.raises(
        RealSceneCaptureError,
        match="bounded regular file|redirected|unsafe",
    ):
        real_scene_capture._stable_read_bytes(
            evidence,
            label="test manifest",
        )


# ============================================================
# RED → GREEN: dataset download error privacy
# ============================================================


def test_prepare_real_capture_hides_dataset_download_error_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """DatasetDownloadError text must not leak into the top-level message.

    verify_hf_dataset failures may include remote URLs, repository paths or
    HTTP error text.  prepare_real_capture must surface a fixed label and
    keep the original exception only in the chained cause for local debugging.
    """
    source, workspace = _write_verified_source(tmp_path / "source")
    private_detail = "https://internal.example.com/secret-repo/abc-token"

    def fail_verify(source_arg, root):
        del source_arg, root
        raise DatasetDownloadError(private_detail)

    monkeypatch.setattr(
        real_scene_capture,
        "verify_hf_dataset",
        fail_verify,
    )

    with pytest.raises(RealSceneCaptureError) as exc_info:
        prepare_real_capture(source, workspace, tmp_path / "run")

    assert private_detail not in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
    assert private_detail in str(exc_info.value.__cause__)


# ============================================================
# RED → GREEN: _write_model_json no-replace publication
# ============================================================


def test_write_model_json_rejects_existing_output(tmp_path: Path) -> None:
    """RED->GREEN: _write_model_json must fail closed when output exists.

    Without no-replace publication, a pre-occupied or symlinked output could
    silently redirect the quality report write (TOCTOU).
    """
    from pydantic import BaseModel

    class _StubModel(BaseModel):
        value: int = 42

    out = tmp_path / "quality-report.json"
    out.write_text('{"pre-occupied": true}', encoding="utf-8")

    with pytest.raises(
        RealSceneCaptureError,
        match="already exists",
    ):
        real_scene_capture._write_model_json(out, _StubModel())


def test_stable_copy_rejects_existing_destination(tmp_path: Path) -> None:
    """RED->GREEN: _stable_copy must fail closed when destination exists.

    Without no-replace publication, a pre-occupied or symlinked destination
    could silently redirect the copy (TOCTOU).
    """
    source = tmp_path / "source.bin"
    payload = b"source-media-bytes"
    source.write_bytes(payload)
    expected_sha = hashlib.sha256(payload).hexdigest()

    dest = tmp_path / "output.bin"
    dest.write_text("pre-occupied", encoding="utf-8")

    with pytest.raises(
        RealSceneCaptureError,
        match="already exists",
    ):
        real_scene_capture._stable_copy(
            source,
            dest,
            expected_bytes=len(payload),
            expected_sha256=expected_sha,
        )


def test_stable_copy_does_not_reopen_source_by_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _stable_copy must not reopen the source by name.

    ``Path.open`` follows symlinks, so reopening the source by name after
    the initial lstat creates a TOCTOU window.  The copy must read from a
    descriptor opened with ``O_NOFOLLOW`` (matching ``_sha256_file``),
    not from ``Path.open``.
    """
    source = tmp_path / "source.bin"
    payload = b"source-media-bytes"
    source.write_bytes(payload)
    expected_sha = hashlib.sha256(payload).hexdigest()

    dest = tmp_path / "output" / "copy.bin"
    original_open = Path.open

    def fail_path_open(self, *args, **kwargs):
        if self == source:
            pytest.fail(
                "_stable_copy reopened source via Path.open (follows symlinks)"
            )
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_path_open)

    real_scene_capture._stable_copy(
        source,
        dest,
        expected_bytes=len(payload),
        expected_sha256=expected_sha,
    )
