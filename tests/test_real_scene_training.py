from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.ingest_manifest import IngestParams
from pipeline.real_scene_capture import PreparedRealCapture, RealSfmResult
from pipeline.real_scene_training import (
    RealSceneTrainingError,
    build_held_out_split,
    build_training_job_bundle,
    verify_training_job_bundle,
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
from pipeline.registration_quality import (
    RegistrationQualityPolicy,
    build_registration_quality_report,
    enumerate_sparse_models,
)
from pipeline.studio_revisions import (
    CapturePayload,
    CaptureRevisionManifest,
    PreparedCaptureBundle,
    canonical_manifest_bytes,
)
from pipeline.training_provenance import TrainingConfig


def _capture(
    root: Path,
    *,
    count: int = 100,
    reverse_payloads: bool = False,
) -> PreparedRealCapture:
    payload_and_bytes = [
        (
            CapturePayload(
                logical_path=f"frame_{index:05d}.png",
                sha256=hashlib.sha256(
                    f"image-{index}".encode()
                ).hexdigest(),
                byte_length=len(f"image-{index}".encode()),
                source_kind="photo",
                source_ordinal=index,
            ),
            f"image-{index}".encode(),
        )
        for index in range(count)
    ]
    if reverse_payloads:
        payload_and_bytes.reverse()
    payloads = [item[0] for item in payload_and_bytes]
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
    payload_root = root / "payload"
    payload_root.mkdir(parents=True)
    for payload, data in payload_and_bytes:
        (payload_root / payload.logical_path).write_bytes(data)
    (root / "manifest.json").write_bytes(manifest_bytes)
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


def _write_sparse_model(
    sparse_root: Path,
    image_names: tuple[str, ...],
) -> None:
    model = sparse_root / "0"
    model.mkdir(parents=True)
    (model / "cameras.txt").write_text(
        "1 PINHOLE 640 480 500 500 320 240\n",
        encoding="utf-8",
    )
    image_rows: list[str] = []
    for image_id, image_name in enumerate(image_names, start=1):
        image_rows.extend(
            [
                f"{image_id} 1 0 0 0 0 0 0 1 {image_name}",
                "0 0 -1",
            ]
        )
    (model / "images.txt").write_text(
        "\n".join(image_rows) + "\n",
        encoding="utf-8",
    )
    (model / "points3D.txt").write_text(
        "1 0 0 0 255 255 255 0.1 1 0\n",
        encoding="utf-8",
    )
    (model / "cameras.bin").write_bytes(b"camera-bin")
    (model / "images.bin").write_bytes(b"images-bin")
    (model / "points3D.bin").write_bytes(b"points-bin")


def _sfm(
    capture: PreparedRealCapture,
    root: Path,
) -> tuple[RealSfmResult, RegistrationQualityPolicy]:
    sfm_root = root / "sfm"
    colmap_root = sfm_root / "colmap"
    image_names = tuple(
        payload.logical_path for payload in capture.capture_manifest.payloads
    )
    _write_sparse_model(colmap_root / "sparse", image_names)
    registration = mock_register(capture.payload_root).model_copy(
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
        },
    )
    registration_path = sfm_root / "registration.json"
    registration_path.parent.mkdir(parents=True, exist_ok=True)
    registration_bytes = (
        registration.model_dump_json(indent=2) + "\n"
    ).encode()
    registration_path.write_bytes(registration_bytes)
    enumeration = enumerate_sparse_models(
        colmap_root / "sparse",
        capture.capture_manifest.output_count,
    )
    count = capture.capture_manifest.output_count
    policy = RegistrationQualityPolicy(
        min_registered_count=count,
        min_registered_ratio=1.0,
        min_session_coverage_ratio=1.0,
        max_unregistered_consecutive_run=0,
        min_largest_connected_model_share=1.0,
    )
    capture_bytes = (capture.capture.bundle / "manifest.json").read_bytes()
    quality = build_registration_quality_report(
        registration=registration,
        registration_json_bytes=registration_bytes,
        capture_manifest=capture.capture_manifest,
        capture_manifest_bytes=capture_bytes,
        policy=policy,
        sparse_enumeration=enumeration,
        invocation_succeeded=True,
        engine_version="COLMAP 4.1.0",
    )
    quality_path = sfm_root / "registration-quality-report.json"
    quality_bytes = (quality.model_dump_json(indent=2) + "\n").encode()
    quality_path.write_bytes(quality_bytes)
    return (
        RealSfmResult(
            registration=registration,
            registration_path=registration_path,
            registration_sha256=hashlib.sha256(
                registration_bytes
            ).hexdigest(),
            sparse_enumeration=enumeration,
            quality=quality,
            quality_path=quality_path,
            quality_sha256=hashlib.sha256(quality_bytes).hexdigest(),
        ),
        policy,
    )


def _training_config() -> TrainingConfig:
    return TrainingConfig(
        trainer_name="nerfstudio-splatfacto",
        trainer_version="1.1.5",
        max_resolution=1600,
        total_steps=30_000,
        export_every=5_000,
        random_seed=42,
        extra_config=(
            ("auto_scale_poses", "false"),
            ("center_method", "none"),
            ("orientation_method", "none"),
            ("scale_factor", "1.0"),
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


def test_bundle_is_byte_identical_across_roots_and_excludes_held_out_pixels(
    tmp_path,
):
    capture = _capture(tmp_path / "capture", count=10)
    sfm, policy = _sfm(capture, tmp_path / "run")

    one = build_training_job_bundle(
        capture,
        sfm,
        _training_config(),
        tmp_path / "one",
        policy=policy,
    )
    two = build_training_job_bundle(
        capture,
        sfm,
        _training_config(),
        tmp_path / "two",
        policy=policy,
    )
    verified = verify_training_job_bundle(one.path)

    assert one.bundle_sha256 == two.bundle_sha256
    assert one.path.read_bytes() == two.path.read_bytes()
    assert verified.bundle_sha256 == one.bundle_sha256
    with zipfile.ZipFile(one.path) as archive:
        names = set(archive.namelist())
    assert names == set(verified.member_names)
    assert all(
        f"capture/payload/{identity.logical_path}" in names
        for identity in verified.split.train
    )
    assert all(
        f"capture/payload/{identity.logical_path}" not in names
        for identity in verified.split.held_out
    )


def test_bundle_rejects_mock_or_rejected_sfm(tmp_path):
    capture = _capture(tmp_path / "capture", count=10)
    sfm, policy = _sfm(capture, tmp_path / "run")
    mock_sfm = replace(
        sfm,
        registration=sfm.registration.model_copy(update={"engine": "mock"}),
    )
    rejected_sfm = replace(
        sfm,
        quality=sfm.quality.model_copy(update={"training_allowed": False}),
    )

    with pytest.raises(RealSceneTrainingError, match="COLMAP"):
        build_training_job_bundle(
            capture,
            mock_sfm,
            _training_config(),
            tmp_path / "mock",
            policy=policy,
        )
    with pytest.raises(RealSceneTrainingError, match="training"):
        build_training_job_bundle(
            capture,
            rejected_sfm,
            _training_config(),
            tmp_path / "rejected",
            policy=policy,
        )


def test_bundle_revalidates_capture_and_report_bytes(tmp_path):
    capture = _capture(tmp_path / "capture", count=10)
    sfm, policy = _sfm(capture, tmp_path / "run")
    manifest_path = capture.capture.bundle / "manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    with pytest.raises(RealSceneTrainingError, match="capture manifest"):
        build_training_job_bundle(
            capture,
            sfm,
            _training_config(),
            tmp_path / "capture-drift",
            policy=policy,
        )

    manifest_path.write_bytes(canonical_manifest_bytes(capture.capture_manifest))
    drifted_sfm = replace(sfm, quality_sha256="0" * 64)
    with pytest.raises(RealSceneTrainingError, match="quality"):
        build_training_job_bundle(
            capture,
            drifted_sfm,
            _training_config(),
            tmp_path / "quality-drift",
            policy=policy,
        )


def test_bundle_requires_absent_non_link_output_boundary(tmp_path):
    capture = _capture(tmp_path / "capture", count=10)
    sfm, policy = _sfm(capture, tmp_path / "run")
    target = tmp_path / "target"
    target.mkdir()
    output = tmp_path / "output"
    output.symlink_to(target, target_is_directory=True)

    with pytest.raises(RealSceneTrainingError, match="output"):
        build_training_job_bundle(
            capture,
            sfm,
            _training_config(),
            output,
            policy=policy,
        )


@pytest.mark.parametrize(
    "member_name",
    [
        "../escape",
        "/absolute",
        "a\\b",
        "C:/escape",
        "CON",
        "a//b",
    ],
)
def test_verifier_rejects_unsafe_archive_member_names(tmp_path, member_name):
    archive_path = tmp_path / "unsafe.zip"
    info = zipfile.ZipInfo(member_name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, b"unsafe")

    with pytest.raises(RealSceneTrainingError, match="member"):
        verify_training_job_bundle(archive_path)


def test_verifier_rejects_duplicate_or_symlink_members(tmp_path):
    duplicate_path = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(duplicate_path, "w") as archive:
            archive.writestr("bundle-manifest.json", b"one")
            archive.writestr("bundle-manifest.json", b"two")
    with pytest.raises(RealSceneTrainingError, match="duplicate"):
        verify_training_job_bundle(duplicate_path)

    symlink_path = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("capture/payload/link.png")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symlink_path, "w") as archive:
        archive.writestr(link, b"target")
    with pytest.raises(RealSceneTrainingError, match="symlink"):
        verify_training_job_bundle(symlink_path)


def test_verifier_rejects_modified_member_bytes(tmp_path):
    capture = _capture(tmp_path / "capture", count=10)
    sfm, policy = _sfm(capture, tmp_path / "run")
    bundle = build_training_job_bundle(
        capture,
        sfm,
        _training_config(),
        tmp_path / "bundle",
        policy=policy,
    )
    replacement = tmp_path / "replacement.zip"
    with zipfile.ZipFile(bundle.path) as source, zipfile.ZipFile(
        replacement,
        "w",
    ) as destination:
        for info in source.infolist():
            data = source.read(info)
            if info.filename.startswith("capture/payload/"):
                data += b"tampered"
            destination.writestr(info, data)
    os.replace(replacement, bundle.path)

    with pytest.raises(RealSceneTrainingError, match="sha256|length"):
        verify_training_job_bundle(bundle.path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("orientation_method", "up"),
        ("center_method", "poses"),
        ("auto_scale_poses", "true"),
        ("scale_factor", "0.5"),
    ],
)
def test_bundle_rejects_pose_changing_dataparser_config(
    tmp_path,
    key,
    value,
):
    capture = _capture(tmp_path / "capture", count=10)
    sfm, policy = _sfm(capture, tmp_path / "run")
    extras = dict(_training_config().extra_config)
    extras[key] = value
    config = _training_config().model_copy(
        update={"extra_config": tuple(sorted(extras.items()))},
    )

    with pytest.raises(RealSceneTrainingError, match=key):
        build_training_job_bundle(
            capture,
            sfm,
            config,
            tmp_path / "bundle",
            policy=policy,
        )
