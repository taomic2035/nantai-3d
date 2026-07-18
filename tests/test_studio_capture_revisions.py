"""Strict private CaptureRevision manifest contracts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pipeline.ingest import ingest_all
from pipeline.ingest_manifest import (
    FrameMapping,
    IngestParams,
    SourceRecord,
    build_manifest,
)


def _verified_ingest():
    params = IngestParams(
        fps=2,
        max_frames=300,
        blur_threshold=80,
        max_long_edge=2560,
    )
    photo = SourceRecord(
        source_path="photo.jpg",
        source_sha256="a" * 64,
        kind="photo",
        bytes=101,
        outputs=(
            FrameMapping(
                output_path="photo.jpg",
                output_sha256="a" * 64,
                output_bytes=101,
                source_frame_index=None,
                preserves_source_bytes=True,
            ),
        ),
    )
    video = SourceRecord(
        source_path="orbit.mp4",
        source_sha256="b" * 64,
        kind="video",
        bytes=4096,
        source_fps=30,
        duration_s=2,
        outputs=(
            FrameMapping(
                output_path="orbit.mp4.frames/frame_000000.jpg",
                output_sha256="c" * 64,
                output_bytes=211,
                source_frame_index=0,
                preserves_source_bytes=False,
            ),
            FrameMapping(
                output_path="orbit.mp4.frames/frame_000001.jpg",
                output_sha256="d" * 64,
                output_bytes=223,
                source_frame_index=15,
                preserves_source_bytes=False,
            ),
        ),
    )
    return build_manifest(
        created_utc=datetime(2026, 7, 18, 7, 59, tzinfo=UTC),
        params=params,
        sources=(photo, video),
    )


def _capture_manifest():
    from pipeline.studio_revisions import build_capture_manifest

    return build_capture_manifest(
        revision_id="capture-" + "1" * 32,
        ingest=_verified_ingest(),
        ingest_manifest_sha256="e" * 64,
        synthetic=True,
        created_utc=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
    )


def test_build_capture_manifest_is_canonical_and_preserves_mixed_evidence():
    from pipeline.studio_revisions import (
        canonical_manifest_bytes,
        capture_manifest_digest,
    )

    manifest = _capture_manifest()
    payload = canonical_manifest_bytes(manifest)

    assert manifest.schema_version == 1
    assert manifest.kind == "capture-revision"
    assert manifest.revision_id == "capture-" + "1" * 32
    assert manifest.synthetic is True
    assert manifest.provenance == "synthetic"
    assert manifest.source_count == 2
    assert manifest.output_count == 3
    assert manifest.ingest_session_id.startswith("ingest-")
    assert manifest.ingest_manifest_sha256 == "e" * 64
    assert [item.source_kind for item in manifest.payloads] == [
        "photo",
        "video-frame",
        "video-frame",
    ]
    assert [item.source_ordinal for item in manifest.payloads] == [0, 1, 1]
    assert [item.frame_index for item in manifest.payloads] == [None, 0, 15]
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    assert capture_manifest_digest(manifest) == hashlib.sha256(payload).hexdigest()


def test_real_capture_is_measured_without_changing_ingest_evidence():
    from pipeline.studio_revisions import build_capture_manifest

    manifest = build_capture_manifest(
        revision_id="capture-" + "2" * 32,
        ingest=_verified_ingest(),
        ingest_manifest_sha256="f" * 64,
        synthetic=False,
        created_utc=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
    )

    assert manifest.synthetic is False
    assert manifest.provenance == "measured"
    assert manifest.ingest_manifest_sha256 == "f" * 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision_id", "capture-not-hex"),
        ("created_utc", datetime(2026, 7, 18, 8, 0)),
        ("ingest_manifest_sha256", "unknown"),
        ("payloads", ()),
    ],
)
def test_capture_manifest_rejects_invalid_trust_fields(field, value):
    from pipeline.studio_revisions import CaptureRevisionManifest

    raw = _capture_manifest().model_dump(mode="python")
    raw[field] = value

    with pytest.raises(ValidationError):
        CaptureRevisionManifest.model_validate(raw)


def test_capture_manifest_rejects_extra_fields_and_duplicate_payload_paths():
    from pipeline.studio_revisions import CaptureRevisionManifest

    raw = _capture_manifest().model_dump(mode="python")
    raw["unexpected"] = True
    with pytest.raises(ValidationError, match="extra"):
        CaptureRevisionManifest.model_validate(raw)

    duplicate = _capture_manifest().model_dump(mode="python")
    duplicate["payloads"][1]["logical_path"] = duplicate["payloads"][0][
        "logical_path"
    ]
    with pytest.raises(ValidationError, match="unique"):
        CaptureRevisionManifest.model_validate(duplicate)


@pytest.mark.parametrize(
    "logical_path",
    (
        "../outside.jpg",
        "/absolute.jpg",
        r"folder\windows.jpg",
        "folder//double.jpg",
        "C:/drive.jpg",
    ),
)
def test_capture_payload_paths_are_portable_and_relative(logical_path):
    from pipeline.studio_revisions import CapturePayload

    with pytest.raises(ValidationError, match="portable"):
        CapturePayload(
            logical_path=logical_path,
            sha256="a" * 64,
            byte_length=1,
            source_kind="photo",
            source_ordinal=0,
            frame_index=None,
        )


def test_capture_payload_kind_and_frame_index_must_agree():
    from pipeline.studio_revisions import CapturePayload

    with pytest.raises(ValidationError, match="frame"):
        CapturePayload(
            logical_path="photo.jpg",
            sha256="a" * 64,
            byte_length=1,
            source_kind="photo",
            source_ordinal=0,
            frame_index=3,
        )
    with pytest.raises(ValidationError, match="frame"):
        CapturePayload(
            logical_path="frame.jpg",
            sha256="a" * 64,
            byte_length=1,
            source_kind="video-frame",
            source_ordinal=0,
            frame_index=None,
        )


def _real_ingest_stage(tmp_path):
    root = tmp_path / "project"
    input_dir = root / "input"
    stage_dir = root / ".nantai-studio/work/run-001/photos"
    input_dir.mkdir(parents=True)
    (input_dir / "photo.jpg").write_bytes(b"capture-photo")
    ingest_all(input_dir, stage_dir, blur_threshold=0)
    return input_dir, stage_dir


def test_prepare_and_verify_capture_bundle_preserves_exact_private_evidence(
    tmp_path,
):
    from pipeline.studio_revisions import (
        prepare_capture_bundle,
        verify_capture_bundle,
    )

    input_dir, stage_dir = _real_ingest_stage(tmp_path)
    bundle = stage_dir.parent / "capture-bundle"
    prepared = prepare_capture_bundle(
        stage_dir=stage_dir,
        input_dir=input_dir,
        bundle_dir=bundle,
        revision_id="capture-" + "3" * 32,
        synthetic=False,
        created_utc=datetime(2026, 7, 18, 8, 1, tzinfo=UTC),
    )

    assert {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    } == {
        "manifest.json",
        "ingest_manifest.json",
        "payload/photo.jpg",
    }
    assert (bundle / "ingest_manifest.json").read_bytes() == (
        stage_dir / "ingest_manifest.json"
    ).read_bytes()
    assert (bundle / "payload/photo.jpg").read_bytes() == b"capture-photo"
    assert prepared == verify_capture_bundle(bundle)
    assert prepared.manifest_digest == hashlib.sha256(
        (bundle / "manifest.json").read_bytes(),
    ).hexdigest()
    assert prepared.manifest.ingest_manifest_sha256 == hashlib.sha256(
        (bundle / "ingest_manifest.json").read_bytes(),
    ).hexdigest()


@pytest.mark.parametrize("damage", ("payload", "extra"))
def test_verify_capture_bundle_rejects_changed_or_undeclared_bytes(
    tmp_path,
    damage,
):
    from pipeline.studio_revisions import (
        CaptureBundleError,
        prepare_capture_bundle,
        verify_capture_bundle,
    )

    input_dir, stage_dir = _real_ingest_stage(tmp_path)
    bundle = stage_dir.parent / "capture-bundle"
    prepare_capture_bundle(
        stage_dir=stage_dir,
        input_dir=input_dir,
        bundle_dir=bundle,
        revision_id="capture-" + "4" * 32,
        synthetic=False,
        created_utc=datetime(2026, 7, 18, 8, 1, tzinfo=UTC),
    )
    if damage == "payload":
        (bundle / "payload/photo.jpg").write_bytes(b"changed")
    else:
        (bundle / "undeclared.txt").write_text("extra", encoding="utf-8")

    with pytest.raises(CaptureBundleError, match="hash|size|undeclared"):
        verify_capture_bundle(bundle)
