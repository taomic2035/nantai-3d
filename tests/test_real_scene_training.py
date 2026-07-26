from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.ingest_manifest import IngestParams
from pipeline.real_scene_capture import PreparedRealCapture
from pipeline.real_scene_training import (
    RealSceneTrainingError,
    build_held_out_split,
)
from pipeline.studio_revisions import (
    CapturePayload,
    CaptureRevisionManifest,
    PreparedCaptureBundle,
    canonical_manifest_bytes,
)


def _capture(
    root: Path,
    *,
    count: int = 100,
    reverse_payloads: bool = False,
) -> PreparedRealCapture:
    payloads = [
        CapturePayload(
            logical_path=f"frame_{index:05d}.png",
            sha256=hashlib.sha256(f"image-{index}".encode()).hexdigest(),
            byte_length=100 + index,
            source_kind="photo",
            source_ordinal=index,
        )
        for index in range(count)
    ]
    if reverse_payloads:
        payloads.reverse()
    manifest = CaptureRevisionManifest(
        revision_id="capture-" + "a" * 32,
        created_utc=datetime(2026, 7, 26, tzinfo=UTC),
        provenance="measured",
        synthetic=False,
        source_count=count,
        output_count=count,
        ingest_session_id="ingest-" + "b" * 64,
        ingest_manifest_sha256="c" * 64,
        ingest_parameters=IngestParams(
            fps=2.0,
            max_frames=500,
            blur_threshold=40.0,
            max_long_edge=4096,
        ),
        payloads=tuple(payloads),
    )
    manifest_bytes = canonical_manifest_bytes(manifest)
    return PreparedRealCapture(
        source_sha256="d" * 64,
        dataset_receipt_sha256="e" * 64,
        selected_paths=tuple(payload.logical_path for payload in payloads),
        capture=PreparedCaptureBundle(
            manifest=manifest,
            manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
            bundle=root,
        ),
    )


def test_canary_split_is_exact_disjoint_and_content_ordered(tmp_path):
    capture = _capture(tmp_path / "one")
    reversed_capture = _capture(
        tmp_path / "two",
        reverse_payloads=True,
    )

    split = build_held_out_split(capture, ratio=0.10)

    assert len(split.train) == 90
    assert len(split.held_out) == 10
    assert not set(split.train) & set(split.held_out)
    assert split == build_held_out_split(reversed_capture, ratio=0.10)
    ordered = sorted(
        capture.capture_manifest.payloads,
        key=lambda payload: (payload.sha256, payload.logical_path),
    )
    assert tuple(identity.logical_path for identity in split.held_out) == tuple(
        payload.logical_path for payload in ordered[:10]
    )


def test_split_uses_round_half_up(tmp_path):
    split = build_held_out_split(_capture(tmp_path, count=5), ratio=0.5)

    assert len(split.held_out) == 3
    assert len(split.train) == 2


@pytest.mark.parametrize("ratio", [0.0, 1.0, -0.1, 1.1])
def test_split_rejects_non_partitioning_ratio(tmp_path, ratio):
    with pytest.raises(RealSceneTrainingError, match="ratio"):
        build_held_out_split(_capture(tmp_path), ratio=ratio)


def test_split_rejects_duplicate_content_identity(tmp_path):
    honest = _capture(tmp_path)
    duplicate = honest.capture_manifest.payloads[0]
    corrupt_manifest = honest.capture_manifest.model_copy(
        update={
            "source_count": 2,
            "output_count": 2,
            "payloads": (duplicate, duplicate),
        },
    )
    corrupt_capture = PreparedRealCapture(
        source_sha256=honest.source_sha256,
        dataset_receipt_sha256=honest.dataset_receipt_sha256,
        selected_paths=(duplicate.logical_path, duplicate.logical_path),
        capture=PreparedCaptureBundle(
            manifest=corrupt_manifest,
            manifest_digest=honest.capture.manifest_digest,
            bundle=honest.capture.bundle,
        ),
    )

    with pytest.raises(RealSceneTrainingError, match="duplicate"):
        build_held_out_split(corrupt_capture, ratio=0.5)
