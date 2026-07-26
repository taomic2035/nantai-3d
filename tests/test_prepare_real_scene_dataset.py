from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import cloud.prepare_real_scene_dataset as prepare_module
from cloud.prepare_real_scene_dataset import (
    PreparedDatasetError,
    prepare_real_scene_dataset,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    HeldOutSplit,
    TrainingBundleManifest,
    TrainingBundleMember,
    TrainingImageIdentity,
    VerifiedTrainingJobBundle,
)
from pipeline.training_provenance import (
    TrainingConfig,
    TrainingInputBinding,
    TrainingRequest,
)

_T0 = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fixture_bundle(tmp_path: Path) -> VerifiedTrainingJobBundle:
    pixels = {
        "held-out.png": b"held-out-pixels",
        "train-a.png": b"train-a-pixels",
        "train-unregistered.png": b"train-unregistered-pixels",
    }
    held_out = TrainingImageIdentity(
        logical_path="held-out.png",
        sha256=_sha(pixels["held-out.png"]),
    )
    train = tuple(
        sorted(
            (
                TrainingImageIdentity(
                    logical_path=name,
                    sha256=_sha(pixels[name]),
                )
                for name in ("train-a.png", "train-unregistered.png")
            ),
            key=lambda identity: (
                identity.sha256,
                identity.logical_path,
            ),
        )
    )
    split = HeldOutSplit(
        ratio=1 / 3,
        total_count=3,
        held_out=(held_out,),
        train=train,
    )
    split_bytes = canonical_model_bytes(split)
    request = TrainingRequest(
        request_id="production-fixture",
        created_at_utc=_T0,
        input_bindings=(
            TrainingInputBinding(
                artifact_kind="held_out_split",
                artifact_sha256=_sha(split_bytes),
                artifact_path="training/held-out-split.json",
                artifact_size_bytes=len(split_bytes),
            ),
        ),
        training_config=TrainingConfig(
            trainer_name="nerfstudio-splatfacto",
            trainer_version="1.1.5",
            max_resolution=1600,
            total_steps=30_000,
            random_seed=42,
        ),
        expected_output_format="inria-3dgs-ply",
        requested_config_sha256="a" * 64,
    )
    members_by_path = {
        "capture/payload/train-a.png": pixels["train-a.png"],
        "capture/payload/train-unregistered.png":
            pixels["train-unregistered.png"],
        "evaluation/payload/held-out.png": pixels["held-out.png"],
        "sfm/sparse/0/cameras.bin": b"camera-bin",
        "sfm/sparse/0/cameras.txt": b"camera-text",
        "sfm/sparse/0/images.bin": struct.pack("<Q", 2) + b"images",
        "sfm/sparse/0/images.txt": b"images-text",
        "sfm/sparse/0/points3D.bin": b"points-bin",
        "sfm/sparse/0/points3D.txt": b"points-text",
        "training/held-out-split.json": split_bytes,
        "training/training-request.json": canonical_model_bytes(request),
    }
    members = tuple(
        TrainingBundleMember(
            path=path,
            byte_length=len(payload),
            sha256=_sha(payload),
        )
        for path, payload in sorted(members_by_path.items())
    )
    manifest = TrainingBundleManifest(
        source_sha256="1" * 64,
        dataset_receipt_sha256="2" * 64,
        capture_manifest_sha256="3" * 64,
        registration_json_sha256="4" * 64,
        registration_quality_policy_sha256="5" * 64,
        registration_quality_report_sha256="6" * 64,
        sparse_model_enumeration_sha256="7" * 64,
        selected_sparse_model_index=0,
        members=members,
    )
    archive_path = tmp_path / "training-job.zip"
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        archive.writestr(
            "bundle-manifest.json",
            canonical_model_bytes(manifest),
        )
        for path, payload in sorted(members_by_path.items()):
            archive.writestr(path, payload)
    names = tuple(sorted(
        ("bundle-manifest.json", *(member.path for member in members))
    ))
    return VerifiedTrainingJobBundle(
        path=archive_path,
        bundle_sha256=_sha(archive_path.read_bytes()),
        manifest=manifest,
        request=request,
        split=split,
        member_names=names,
    )


def _converter(
    recon_dir: Path,
    output_dir: Path,
    **kwargs,
) -> int:
    assert recon_dir == output_dir / "colmap" / "sparse" / "0"
    assert kwargs["keep_original_world_coordinate"] is True
    frames = [
        {
            "file_path": "images/train-a.png",
            "transform_matrix": [
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, 0],
                [0, 0, 0, 1],
            ],
        },
        {
            "file_path": "images/held-out.png",
            "transform_matrix": [
                [1, 0, 0, 1],
                [0, 1, 0, 0],
                [0, 0, 1, 0],
                [0, 0, 0, 1],
            ],
        },
    ]
    (output_dir / "transforms.json").write_text(
        json.dumps({"frames": frames, "ply_file_path": "sparse_pc.ply"}),
        encoding="utf-8",
    )
    (output_dir / "sparse_pc.ply").write_bytes(b"ply\nend_header\n")
    return len(frames)


def _patch_verifier(monkeypatch, bundle):
    monkeypatch.setattr(
        prepare_module,
        "verify_production_training_job_bundle",
        lambda path: bundle,
    )


def test_prepare_materializes_explicit_train_val_test_without_rerunning_sfm(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    _patch_verifier(monkeypatch, bundle)
    output = tmp_path / "prepared"

    prepared = prepare_real_scene_dataset(
        bundle.path,
        output,
        converter=_converter,
        nerfstudio_version="1.1.5",
    )

    metadata = json.loads(prepared.transforms_path.read_text("ascii"))
    assert metadata["orientation_override"] == "none"
    assert metadata["train_filenames"] == ["images/train-a.png"]
    assert metadata["val_filenames"] == ["images/held-out.png"]
    assert metadata["test_filenames"] == ["images/held-out.png"]
    assert set(metadata["train_filenames"]).isdisjoint(
        metadata["test_filenames"]
    )
    assert {
        frame["file_path"] for frame in metadata["frames"]
    } == {
        *metadata["train_filenames"],
        *metadata["test_filenames"],
    }
    assert prepared.manifest.unregistered_train_filenames == (
        "images/train-unregistered.png",
    )
    assert (output / "images" / "held-out.png").read_bytes() == (
        b"held-out-pixels"
    )
    assert (
        output / "colmap" / "sparse" / "0" / "cameras.bin"
    ).read_bytes() == b"camera-bin"
    assert prepared.manifest.training_bundle_sha256 == bundle.bundle_sha256
    assert prepared.manifest.nerfstudio_version == "1.1.5"


def test_prepare_rejects_missing_held_out_camera(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    _patch_verifier(monkeypatch, bundle)

    def missing_heldout(recon_dir, output_dir, **kwargs):
        _converter(recon_dir, output_dir, **kwargs)
        metadata = json.loads(
            (output_dir / "transforms.json").read_text("utf-8")
        )
        metadata["frames"] = metadata["frames"][:1]
        (output_dir / "transforms.json").write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
        return 1

    with pytest.raises(PreparedDatasetError, match="held-out"):
        prepare_real_scene_dataset(
            bundle.path,
            tmp_path / "prepared",
            converter=missing_heldout,
            nerfstudio_version="1.1.5",
        )


def test_prepare_rejects_duplicate_or_traversing_converter_frames(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    _patch_verifier(monkeypatch, bundle)

    def unsafe(recon_dir, output_dir, **kwargs):
        _converter(recon_dir, output_dir, **kwargs)
        metadata = json.loads(
            (output_dir / "transforms.json").read_text("utf-8")
        )
        metadata["frames"].append(
            {
                **metadata["frames"][0],
                "file_path": "../escape.png",
            }
        )
        (output_dir / "transforms.json").write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
        return 3

    with pytest.raises(PreparedDatasetError, match="frame path"):
        prepare_real_scene_dataset(
            bundle.path,
            tmp_path / "prepared",
            converter=unsafe,
            nerfstudio_version="1.1.5",
        )


def test_prepare_rejects_member_drift_even_after_outer_verification(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    _patch_verifier(monkeypatch, bundle)
    replacement = tmp_path / "replacement.zip"
    with zipfile.ZipFile(bundle.path) as source, zipfile.ZipFile(
        replacement,
        "w",
    ) as destination:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "evaluation/payload/held-out.png":
                payload = b"Held-out-pixels"
            destination.writestr(info, payload)
    replacement.replace(bundle.path)

    with pytest.raises(PreparedDatasetError, match="sha256"):
        prepare_real_scene_dataset(
            bundle.path,
            tmp_path / "prepared",
            converter=_converter,
            nerfstudio_version="1.1.5",
        )


def test_prepare_rejects_pixel_bytes_outside_split_identity(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    wrong_identity = bundle.split.held_out[0].model_copy(
        update={"sha256": "0" * 64},
    )
    wrong_split = bundle.split.model_copy(
        update={"held_out": (wrong_identity,)},
    )
    stale_verified_bundle = replace(bundle, split=wrong_split)
    _patch_verifier(monkeypatch, stale_verified_bundle)

    with pytest.raises(PreparedDatasetError, match="split identity"):
        prepare_real_scene_dataset(
            bundle.path,
            tmp_path / "prepared",
            converter=_converter,
            nerfstudio_version="1.1.5",
        )


def test_prepare_requires_pinned_version_and_absent_output(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    _patch_verifier(monkeypatch, bundle)
    with pytest.raises(PreparedDatasetError, match="1.1.5"):
        prepare_real_scene_dataset(
            bundle.path,
            tmp_path / "wrong-version",
            converter=_converter,
            nerfstudio_version="1.1.4",
        )

    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(PreparedDatasetError, match="absent"):
        prepare_real_scene_dataset(
            bundle.path,
            output,
            converter=_converter,
            nerfstudio_version="1.1.5",
        )
