from __future__ import annotations

import hashlib
import math
import stat
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.render_evaluation as render_evaluation
from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    HeldOutSplit,
    TrainingImageIdentity,
    held_out_split_canonical_bytes,
)
from pipeline.render_evaluation import (
    RenderCameraRecord,
    RenderEvaluationError,
    RenderEvaluationPolicy,
    RenderEvaluationProtocol,
    RenderEvaluationReport,
    RenderFrameMetric,
    canonical_render_evaluation_bytes,
    render_artifact_stem,
    render_evaluation_sha256,
    validate_render_evaluation,
)
from scripts.validate_render_evaluation import main as validate_main


def _png(width: int, height: int, *, colour_type: int = 2) -> bytes:
    channels = 3 if colour_type == 2 else 4
    rows = b"".join(
        b"\x00" + bytes([index % 251]) * (width * channels)
        for index in range(height)
    )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _write(path: Path, payload: bytes) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _identity(logical_path: str, payload: bytes) -> TrainingImageIdentity:
    return TrainingImageIdentity(
        logical_path=logical_path,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _fixture(tmp_path):
    root = tmp_path / "run"
    payloads = {
        "frame-a.jpg": b"held-out-a",
        "frame-b.jpg": b"held-out-b",
        "frame-c.jpg": b"training-c",
        "frame-d.jpg": b"training-d",
    }
    ordered = tuple(
        sorted(
            (
                _identity(logical_path, payload)
                for logical_path, payload in payloads.items()
            ),
            key=lambda item: (item.sha256, item.logical_path),
        )
    )
    split = HeldOutSplit(
        ratio=0.5,
        total_count=4,
        held_out=ordered[:2],
        train=ordered[2:],
    )
    split_bytes = held_out_split_canonical_bytes(split)
    _write(
        root / "prepared/evidence/held-out-split.json",
        split_bytes,
    )
    transforms_bytes = b'{"test_filenames":["bound-by-camera-records"]}\n'
    _write(root / "prepared/transforms.json", transforms_bytes)
    trainer_config_bytes = b"method_name: splatfacto\n"
    _write(
        root / "result/render-evaluation/trainer-config.yml",
        trainer_config_bytes,
    )
    for identity in (*split.held_out, *split.train):
        _write(
            root / "prepared/images" / identity.logical_path,
            payloads[identity.logical_path],
        )

    protocol = RenderEvaluationProtocol(
        width=4,
        height=3,
        crop_mode="center-crop",
        colour_space="srgb",
        alpha_handling="reject",
        mask_handling="none",
        ssim_window_size=11,
        ssim_sigma=1.5,
        ssim_data_range=1.0,
        lpips_backbone="alex",
    )
    digest = "nantai/nerfstudio@sha256:" + "a" * 64
    policy = RenderEvaluationPolicy(
        held_out_split_sha256=hashlib.sha256(split_bytes).hexdigest(),
        transforms_sha256=hashlib.sha256(transforms_bytes).hexdigest(),
        evaluator_container_digest=digest,
        protocol=protocol,
        minimum_mean_psnr=24.0,
        minimum_mean_ssim=0.80,
        maximum_mean_lpips=0.25,
        minimum_worst_psnr=18.0,
    )
    frames = []
    frame_values = (
        (18.0, 0.80, 0.25),
        (30.0, 0.80, 0.25),
    )
    for identity, (psnr, ssim, lpips) in zip(
        split.held_out,
        frame_values,
        strict=True,
    ):
        stem = render_artifact_stem(identity.logical_path)
        render_path = f"result/render-evaluation/renders/{stem}.png"
        camera_path = f"result/render-evaluation/cameras/{stem}.json"
        render_bytes = _write(root / render_path, _png(4, 3))
        camera = RenderCameraRecord(
            frame_id=identity.logical_path,
            source_path=f"prepared/images/{identity.logical_path}",
            source_sha256=identity.sha256,
            transforms_sha256=policy.transforms_sha256,
            camera_model="perspective",
            source_width=8,
            source_height=6,
            fx=4.0,
            fy=4.0,
            cx=2.0,
            cy=1.5,
            camera_to_world=(
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
            ),
        )
        camera_bytes = _write(
            root / camera_path,
            canonical_render_evaluation_bytes(camera),
        )
        source_bytes = payloads[identity.logical_path]
        frames.append(
            RenderFrameMetric(
                frame_id=identity.logical_path,
                source_path=f"prepared/images/{identity.logical_path}",
                source_byte_length=len(source_bytes),
                source_sha256=identity.sha256,
                render_path=render_path,
                render_byte_length=len(render_bytes),
                render_sha256=hashlib.sha256(render_bytes).hexdigest(),
                camera_path=camera_path,
                camera_byte_length=len(camera_bytes),
                camera_sha256=hashlib.sha256(camera_bytes).hexdigest(),
                psnr=psnr,
                ssim=ssim,
                lpips=lpips,
            )
        )
    report = RenderEvaluationReport(
        evaluation_id="eval-canary",
        policy_sha256=render_evaluation_sha256(policy),
        held_out_split_sha256=policy.held_out_split_sha256,
        evaluator_container_digest=digest,
        protocol=protocol,
        frames=tuple(frames),
        trainer_config_sha256=hashlib.sha256(
            trainer_config_bytes
        ).hexdigest(),
        mean_psnr=24.0,
        mean_ssim=0.80,
        mean_lpips=0.25,
        worst_psnr=18.0,
    )
    return root, split, policy, report


def test_exact_metric_thresholds_pass(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)

    decision = validate_render_evaluation(policy, report, root)

    assert decision.accepted is True
    assert decision.failed_thresholds == ()
    assert decision.mean_psnr == 24.0
    assert decision.worst_psnr == 18.0


def test_report_boolean_cannot_override_bad_frame(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    frames = (
        report.frames[0].model_copy(update={"psnr": 17.99}),
        report.frames[1].model_copy(update={"psnr": 42.01}),
    )
    dishonest = report.model_copy(
        update={
            "frames": frames,
            "mean_psnr": 30.0,
            "worst_psnr": 17.99,
            "accepted": True,
        }
    )

    with pytest.raises(RenderEvaluationError, match="decision"):
        validate_render_evaluation(policy, dishonest, root)


def test_missing_held_out_frame_is_rejected(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    damaged = report.model_copy(
        update={
            "frames": report.frames[:1],
            "mean_psnr": report.frames[0].psnr,
            "mean_ssim": report.frames[0].ssim,
            "mean_lpips": report.frames[0].lpips,
            "worst_psnr": report.frames[0].psnr,
        }
    )

    with pytest.raises(RenderEvaluationError, match="held-out"):
        validate_render_evaluation(policy, damaged, root)


def test_duplicate_frame_is_rejected(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    duplicate = report.model_copy(
        update={"frames": (report.frames[0], report.frames[0])}
    )

    with pytest.raises(RenderEvaluationError, match="duplicate"):
        validate_render_evaluation(policy, duplicate, root)


def test_training_frame_cannot_enter_evaluation(tmp_path):
    root, split, policy, report = _fixture(tmp_path)
    training = split.train[0]
    replacement = report.frames[0].model_copy(
        update={
            "frame_id": training.logical_path,
            "source_path": f"prepared/images/{training.logical_path}",
            "source_byte_length": len(
                (root / "prepared/images" / training.logical_path).read_bytes()
            ),
            "source_sha256": training.sha256,
        }
    )
    damaged = report.model_copy(
        update={"frames": (replacement, report.frames[1])}
    )

    with pytest.raises(RenderEvaluationError, match="held-out"):
        validate_render_evaluation(policy, damaged, root)


@pytest.mark.parametrize("kind", ["source", "render", "camera"])
def test_artifact_byte_drift_is_rejected(tmp_path, kind):
    root, _split, policy, report = _fixture(tmp_path)
    frame = report.frames[0]
    path = {
        "source": frame.source_path,
        "render": frame.render_path,
        "camera": frame.camera_path,
    }[kind]
    (root / path).write_bytes((root / path).read_bytes() + b"tamper")

    with pytest.raises(RenderEvaluationError, match=kind):
        validate_render_evaluation(policy, report, root)


def test_camera_record_must_bind_the_held_out_source(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    frame = report.frames[0]
    camera_path = root / frame.camera_path
    camera = RenderCameraRecord.model_validate_json(camera_path.read_bytes())
    damaged = camera.model_copy(update={"source_sha256": "f" * 64})
    payload = canonical_render_evaluation_bytes(damaged)
    camera_path.write_bytes(payload)
    rebound_frame = frame.model_copy(
        update={
            "camera_byte_length": len(payload),
            "camera_sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    rebound_report = report.model_copy(
        update={"frames": (rebound_frame, report.frames[1])}
    )

    with pytest.raises(RenderEvaluationError, match="camera"):
        validate_render_evaluation(policy, rebound_report, root)


def test_evaluator_digest_drift_is_rejected(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    damaged = report.model_copy(
        update={
            "evaluator_container_digest":
                "nantai/nerfstudio@sha256:" + "b" * 64,
        }
    )

    with pytest.raises(RenderEvaluationError, match="evaluator"):
        validate_render_evaluation(policy, damaged, root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("width", 5),
        ("crop_mode", "letterbox"),
        ("colour_space", "linear-srgb"),
        ("alpha_handling", "composite-black"),
        ("mask_handling", "alpha"),
        ("ssim_window_size", 9),
        ("lpips_backbone", "vgg"),
    ],
)
def test_evaluation_protocol_drift_is_rejected(
    tmp_path,
    field,
    value,
):
    root, _split, policy, report = _fixture(tmp_path)
    changed_protocol = report.protocol.model_copy(update={field: value})
    damaged = report.model_copy(update={"protocol": changed_protocol})

    with pytest.raises(RenderEvaluationError, match="protocol"):
        validate_render_evaluation(policy, damaged, root)


def test_non_finite_frame_metric_is_rejected(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    frame = report.frames[0].model_copy(update={"psnr": math.nan})
    damaged = report.model_copy(
        update={"frames": (frame, report.frames[1])}
    )

    with pytest.raises(RenderEvaluationError, match="invalid"):
        validate_render_evaluation(policy, damaged, root)


def test_reported_aggregate_drift_is_rejected(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    damaged = report.model_copy(update={"mean_psnr": 99.0})

    with pytest.raises(RenderEvaluationError, match="aggregate"):
        validate_render_evaluation(policy, damaged, root)


def test_render_must_be_lossless_rgb_png_at_policy_resolution(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    frame = report.frames[0]
    payload = _png(4, 3, colour_type=6)
    (root / frame.render_path).write_bytes(payload)
    rebound = frame.model_copy(
        update={
            "render_byte_length": len(payload),
            "render_sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    rebound_report = report.model_copy(
        update={"frames": (rebound, report.frames[1])}
    )

    with pytest.raises(RenderEvaluationError, match="RGB PNG"):
        validate_render_evaluation(policy, rebound_report, root)


def test_split_policy_sha_drift_is_rejected(tmp_path):
    root, _split, policy, report = _fixture(tmp_path)
    changed = policy.model_copy(
        update={"held_out_split_sha256": "f" * 64}
    )
    damaged_report = report.model_copy(
        update={"policy_sha256": render_evaluation_sha256(changed)}
    )

    with pytest.raises(RenderEvaluationError, match="split"):
        validate_render_evaluation(changed, damaged_report, root)


def test_models_serialize_as_canonical_lf_json(tmp_path):
    _root, _split, policy, report = _fixture(tmp_path)

    payload = canonical_render_evaluation_bytes(policy)
    report_payload = canonical_render_evaluation_bytes(report)

    assert payload == canonical_model_bytes(policy)
    assert payload.endswith(b"\n")
    assert b'"accepted"' not in report_payload


def _write_cli_documents(root, policy, report):
    policy_path = root / "policy.json"
    report_path = root / "report.json"
    _write(
        policy_path,
        canonical_render_evaluation_bytes(policy),
    )
    _write(
        report_path,
        canonical_render_evaluation_bytes(report),
    )
    return policy_path, report_path


def test_validator_cli_accepts_and_prints_exact_document_shas(
    tmp_path,
    capsys,
):
    root, _split, policy, report = _fixture(tmp_path)
    policy_path, report_path = _write_cli_documents(
        root,
        policy,
        report,
    )

    exit_code = validate_main(
        [
            str(policy_path),
            str(report_path),
            "--root",
            str(root),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert f"policy_sha256={render_evaluation_sha256(policy)}" in output
    assert f"report_sha256={render_evaluation_sha256(report)}" in output
    assert "accepted=True" in output


def test_validator_cli_returns_two_and_lists_failed_thresholds(
    tmp_path,
    capsys,
):
    root, _split, policy, report = _fixture(tmp_path)
    frames = tuple(
        frame.model_copy(update={"psnr": 17.0})
        for frame in report.frames
    )
    rejected = report.model_copy(
        update={
            "frames": frames,
            "mean_psnr": 17.0,
            "worst_psnr": 17.0,
        }
    )
    policy_path, report_path = _write_cli_documents(
        root,
        policy,
        rejected,
    )

    exit_code = validate_main(
        [
            str(policy_path),
            str(report_path),
            "--root",
            str(root),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "accepted=False" in output
    assert "mean_psnr" in output
    assert "worst_psnr" in output


def test_validator_cli_returns_two_for_byte_tamper(tmp_path, capsys):
    root, _split, policy, report = _fixture(tmp_path)
    policy_path, report_path = _write_cli_documents(
        root,
        policy,
        report,
    )
    (root / report.frames[0].render_path).write_bytes(b"tamper")

    exit_code = validate_main(
        [
            str(policy_path),
            str(report_path),
            "--root",
            str(root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "render frame" in captured.err
    assert "policy_sha256=" in captured.out
    assert "report_sha256=" in captured.out


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


def test_stable_render_read_rejects_descriptor_reparse_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "render.png"
    artifact.write_bytes(b"render")
    real_fstat = render_evaluation.os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        observed = real_fstat(descriptor)
        calls += 1
        if calls == 2:
            return _stat_with_reparse(observed)
        return observed

    monkeypatch.setattr(render_evaluation.os, "fstat", drifting_fstat)

    with pytest.raises(RenderEvaluationError, match="changed while being read"):
        render_evaluation._read_stable(
            tmp_path,
            "render.png",
            label="render frame",
            max_bytes=32,
        )


def test_stable_render_read_uses_observed_reparse_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "render.png"
    artifact.write_bytes(b"render")
    real_lstat = Path.lstat
    target_calls = 0

    def drifting_lstat(path):
        nonlocal target_calls
        observed = real_lstat(path)
        if Path(path) == artifact:
            target_calls += 1
            if target_calls == 2:
                return _stat_with_reparse(observed)
        return observed

    monkeypatch.setattr(Path, "lstat", drifting_lstat)

    with pytest.raises(RenderEvaluationError, match="not a regular file"):
        render_evaluation._read_stable(
            tmp_path,
            "render.png",
            label="render frame",
            max_bytes=32,
        )
