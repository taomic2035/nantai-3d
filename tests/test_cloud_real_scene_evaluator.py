from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import cloud.evaluate_real_scene as evaluator_module
from cloud.evaluate_real_scene import (
    EvaluatedFrame,
    RealSceneEvaluatorError,
    build_render_evaluation_policy,
    evaluate_real_scene,
)
from pipeline.real_scene_training import (
    HeldOutSplit,
    TrainingImageIdentity,
    held_out_split_canonical_bytes,
)
from pipeline.render_evaluation import (
    RenderCameraRecord,
    render_artifact_stem,
    validate_render_evaluation,
)


def test_evaluator_link_check_rejects_windows_reparse_attribute() -> None:
    candidate = SimpleNamespace(
        is_symlink=lambda: False,
        is_junction=lambda: False,
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_file_attributes=0x400,
        ),
    )

    assert evaluator_module._is_linklike(candidate)


def test_evaluator_stat_signature_binds_windows_reparse_attribute() -> None:
    common = {
        "st_dev": 1,
        "st_ino": 2,
        "st_mode": stat.S_IFREG | 0o600,
        "st_size": 3,
        "st_mtime_ns": 4,
    }

    assert evaluator_module._stat_signature(
        SimpleNamespace(**common, st_file_attributes=0)
    ) != evaluator_module._stat_signature(
        SimpleNamespace(**common, st_file_attributes=0x400)
    )


def test_evaluator_regular_read_rejects_descriptor_after_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "transforms.json"
    target.write_bytes(b"payload\n")
    original_fstat = os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        observed = original_fstat(descriptor)
        calls += 1
        if calls != 2:
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_mode=observed.st_mode,
            st_size=observed.st_size + 1,
            st_mtime_ns=observed.st_mtime_ns,
            st_file_attributes=getattr(
                observed,
                "st_file_attributes",
                0,
            ),
        )

    monkeypatch.setattr(evaluator_module.os, "fstat", drifting_fstat)

    with pytest.raises(
        RealSceneEvaluatorError,
        match="changed while being read",
    ):
        evaluator_module._read_regular(
            target,
            label="transforms.json",
            max_bytes=1024,
        )


def _png(width: int = 800, height: int = 600) -> bytes:
    rows = b"".join(
        b"\x00" + bytes([row % 251]) * (width * 3)
        for row in range(height)
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
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _write(path: Path, payload: bytes) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _scene(tmp_path):
    root = tmp_path / "run"
    payloads = {
        "a.jpg": b"image-a",
        "b.jpg": b"image-b",
        "c.jpg": b"image-c",
        "d.jpg": b"image-d",
    }
    identities = tuple(
        sorted(
            (
                TrainingImageIdentity(
                    logical_path=name,
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
                for name, payload in payloads.items()
            ),
            key=lambda item: (item.sha256, item.logical_path),
        )
    )
    split = HeldOutSplit(
        ratio=0.5,
        total_count=4,
        held_out=identities[:2],
        train=identities[2:],
    )
    split_bytes = _write(
        root / "prepared/evidence/held-out-split.json",
        held_out_split_canonical_bytes(split),
    )
    transforms = (
        json.dumps(
            {
                "test_filenames": [
                    f"images/{item.logical_path}"
                    for item in split.held_out
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    _write(
        root / "prepared/transforms.json",
        transforms,
    )
    for identity in identities:
        _write(
            root / "prepared/images" / identity.logical_path,
            payloads[identity.logical_path],
        )
    config = _write(
        root / "outputs/scene/splatfacto/run/config.yml",
        b"method_name: splatfacto\n",
    )
    digest = "nantai/nerfstudio@sha256:" + "a" * 64
    policy = build_render_evaluation_policy(
        root,
        evaluator_container_digest=digest,
        expected_split_sha256=hashlib.sha256(split_bytes).hexdigest(),
    )
    return (
        root,
        split,
        Path(root / "outputs/scene/splatfacto/run/config.yml"),
        config,
        policy,
    )


class _Backend:
    def __init__(self, root: Path, split: HeldOutSplit):
        self.root = root
        self.split = split
        self.override: tuple[str, ...] | None = None
        self.mutate_config: Path | None = None
        self.bad_png = False

    def evaluate(self, *, config_path, prepared_root, protocol):
        assert prepared_root == self.root / "prepared"
        if self.mutate_config is not None:
            self.mutate_config.write_bytes(b"changed\n")
        frame_ids = self.override or tuple(
            item.logical_path for item in self.split.held_out
        )
        frames = []
        for frame_id in frame_ids:
            source = self.root / "prepared/images" / frame_id
            source_bytes = source.read_bytes()
            frames.append(
                EvaluatedFrame(
                    frame_id=frame_id,
                    render_png_bytes=(
                        b"not-png" if self.bad_png else _png()
                    ),
                    camera=RenderCameraRecord(
                        frame_id=frame_id,
                        source_path=f"prepared/images/{frame_id}",
                        source_sha256=hashlib.sha256(
                            source_bytes
                        ).hexdigest(),
                        transforms_sha256=hashlib.sha256(
                            (
                                self.root / "prepared/transforms.json"
                            ).read_bytes()
                        ).hexdigest(),
                        camera_model="perspective",
                        source_width=1600,
                        source_height=1200,
                        fx=900.0,
                        fy=900.0,
                        cx=800.0,
                        cy=600.0,
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
                    ),
                    psnr=25.0,
                    ssim=0.85,
                    lpips=0.20,
                )
            )
        return tuple(frames)


def test_evaluator_writes_exact_held_out_artifacts_and_report(tmp_path):
    root, split, config_path, config_bytes, policy = _scene(tmp_path)
    backend = _Backend(root, split)

    report = evaluate_real_scene(
        config_path,
        root,
        policy,
        backend=backend,
        evaluation_id="eval-production",
    )

    result = root / "result/render-evaluation"
    assert report.trainer_config_sha256 == hashlib.sha256(
        config_bytes
    ).hexdigest()
    assert tuple(frame.frame_id for frame in report.frames) == tuple(
        item.logical_path for item in split.held_out
    )
    assert set(path.name for path in (result / "renders").iterdir()) == {
        f"{render_artifact_stem(item.logical_path)}.png"
        for item in split.held_out
    }
    assert b'"accepted"' not in (
        result / "report.json"
    ).read_bytes()
    assert validate_render_evaluation(policy, report, root).accepted is True


@pytest.mark.parametrize("mode", ["missing", "duplicate", "training"])
def test_evaluator_rejects_nonexact_backend_frame_set(tmp_path, mode):
    root, split, config_path, _config_bytes, policy = _scene(tmp_path)
    backend = _Backend(root, split)
    held_out = tuple(item.logical_path for item in split.held_out)
    if mode == "missing":
        backend.override = held_out[:1]
    elif mode == "duplicate":
        backend.override = (held_out[0], held_out[0])
    else:
        backend.override = (held_out[0], split.train[0].logical_path)

    with pytest.raises(RealSceneEvaluatorError, match="held-out"):
        evaluate_real_scene(
            config_path,
            root,
            policy,
            backend=backend,
            evaluation_id="eval-production",
        )

    assert not (root / "result/render-evaluation").exists()


def test_evaluator_rechecks_trainer_config_after_backend(tmp_path):
    root, split, config_path, _config_bytes, policy = _scene(tmp_path)
    backend = _Backend(root, split)
    backend.mutate_config = config_path

    with pytest.raises(RealSceneEvaluatorError, match="trainer config"):
        evaluate_real_scene(
            config_path,
            root,
            policy,
            backend=backend,
            evaluation_id="eval-production",
        )

    assert not (root / "result/render-evaluation").exists()


def test_evaluator_does_not_publish_invalid_render_bytes(tmp_path):
    root, split, config_path, _config_bytes, policy = _scene(tmp_path)
    backend = _Backend(root, split)
    backend.bad_png = True

    with pytest.raises(RealSceneEvaluatorError, match="RGB PNG"):
        evaluate_real_scene(
            config_path,
            root,
            policy,
            backend=backend,
            evaluation_id="eval-production",
        )

    assert not (root / "result/render-evaluation").exists()


def test_policy_builder_rejects_wrong_expected_split_sha(tmp_path):
    root, _split, _config_path, _config_bytes, policy = _scene(tmp_path)

    with pytest.raises(RealSceneEvaluatorError, match="split"):
        build_render_evaluation_policy(
            root,
            evaluator_container_digest=policy.evaluator_container_digest,
            expected_split_sha256="f" * 64,
        )


def test_evaluator_rejects_existing_or_linklike_output(tmp_path):
    root, split, config_path, _config_bytes, policy = _scene(tmp_path)
    output = root / "result/render-evaluation"
    output.mkdir(parents=True)

    with pytest.raises(RealSceneEvaluatorError, match="absent"):
        evaluate_real_scene(
            config_path,
            root,
            policy,
            backend=_Backend(root, split),
            evaluation_id="eval-production",
        )


def test_unexpected_backend_failure_is_structured_and_unpublished(
    tmp_path,
):
    root, _split, config_path, _config_bytes, policy = _scene(tmp_path)

    class BrokenBackend:
        def evaluate(self, **kwargs):
            del kwargs
            raise RuntimeError("private backend detail")

    with pytest.raises(
        RealSceneEvaluatorError,
        match="backend failed: RuntimeError",
    ):
        evaluate_real_scene(
            config_path,
            root,
            policy,
            backend=BrokenBackend(),
            evaluation_id="eval-production",
        )

    assert not (root / "result/render-evaluation").exists()
