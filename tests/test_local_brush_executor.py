from __future__ import annotations

import hashlib
import stat
import struct
import subprocess
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import pipeline.local_brush_executor as local_brush_module
from pipeline.local_brush_executor import (
    LocalBrushExecutionError,
    LocalBrushExecutor,
    LocalBrushExecutorConfig,
    build_local_brush_execution_receipt,
    write_local_brush_execution_receipt,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    HeldOutSplit,
    TrainingBundleManifest,
    TrainingBundleMember,
    TrainingImageIdentity,
    VerifiedTrainingJobBundle,
)
from pipeline.training_executor import new_attempt
from pipeline.training_provenance import (
    TrainingConfig,
    TrainingInputBinding,
    TrainingRequest,
)

_ROOT = Path(__file__).resolve().parents[1]
_T0 = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _input_bytes() -> dict[str, bytes]:
    return {
        "capture/manifest.json": b'{"capture":"verified"}\n',
        "sfm/registration.json": b'{"registration":"verified"}\n',
        "sfm/registration-quality-report.json": b'{"quality":"accepted"}\n',
        "sfm/sparse/0": b'{"members":"content-addressed"}\n',
    }


def _training_request() -> TrainingRequest:
    inputs = _input_bytes()
    kinds = (
        ("capture_manifest", "capture/manifest.json"),
        ("registration_json", "sfm/registration.json"),
        (
            "registration_quality_report",
            "sfm/registration-quality-report.json",
        ),
        ("sparse_model_dir", "sfm/sparse/0"),
    )
    config_bytes = b"trainer: nerfstudio-splatfacto\n"
    return TrainingRequest(
        request_id="fixture-production-request",
        created_at_utc=_T0,
        input_bindings=tuple(
            TrainingInputBinding(
                artifact_kind=kind,
                artifact_sha256=_sha(inputs[path]),
                artifact_path=path,
                artifact_size_bytes=len(inputs[path]),
            )
            for kind, path in kinds
        ),
        training_config=TrainingConfig(
            trainer_name="nerfstudio-splatfacto",
            trainer_version="1.1.5",
            max_resolution=1024,
            total_steps=30_000,
            random_seed=42,
        ),
        expected_output_format="inria-3dgs-ply",
        requested_config_sha256=_sha(config_bytes),
    )


def _fixture_bundle(tmp_path: Path) -> VerifiedTrainingJobBundle:
    train_pixels = {
        "train-a.png": b"train-a-pixels",
        "train-b.png": b"train-b-pixels",
    }
    held_out = TrainingImageIdentity(
        logical_path="held-out.png",
        sha256="0" * 64,
    )
    train_a = TrainingImageIdentity(
        logical_path="train-a.png",
        sha256=_sha(train_pixels["train-a.png"]),
    )
    train_b = TrainingImageIdentity(
        logical_path="train-b.png",
        sha256=_sha(train_pixels["train-b.png"]),
    )
    split = HeldOutSplit(
        ratio=1 / 3,
        total_count=3,
        held_out=(held_out,),
        train=tuple(
            sorted(
                (train_a, train_b),
                key=lambda identity: (
                    identity.sha256,
                    identity.logical_path,
                ),
            )
        ),
    )
    payloads = {
        "capture/payload/train-a.png": train_pixels["train-a.png"],
        "capture/payload/train-b.png": train_pixels["train-b.png"],
        "sfm/sparse/0/cameras.bin": b"camera-model",
        "sfm/sparse/0/images.bin": struct.pack("<Q", 3) + b"all-cameras",
        "sfm/sparse/0/points3D.bin": b"sparse-points",
        "training/held-out-split.json": canonical_model_bytes(split),
        "training/training-request.json": canonical_model_bytes(
            _training_request()
        ),
    }
    members = tuple(
        TrainingBundleMember(
            path=path,
            byte_length=len(payload),
            sha256=_sha(payload),
        )
        for path, payload in sorted(payloads.items())
    )
    manifest = TrainingBundleManifest(
        source_sha256=_SHA_A,
        dataset_receipt_sha256=_SHA_B,
        capture_manifest_sha256=_SHA_C,
        registration_json_sha256=_SHA_D,
        registration_quality_policy_sha256="e" * 64,
        registration_quality_report_sha256="f" * 64,
        sparse_model_enumeration_sha256="9" * 64,
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
        for path, payload in sorted(payloads.items()):
            archive.writestr(path, payload)
    names = (
        "bundle-manifest.json",
        *(member.path for member in members),
    )
    return VerifiedTrainingJobBundle(
        path=archive_path,
        bundle_sha256=_sha(archive_path.read_bytes()),
        manifest=manifest,
        request=_training_request(),
        split=split,
        member_names=tuple(sorted(names)),
    )


def _executable(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _config(tmp_path: Path) -> LocalBrushExecutorConfig:
    return LocalBrushExecutorConfig(
        execution_root=tmp_path / "local-brush-run",
        python_executable=Path(sys.executable),
        reconstruct_script=_ROOT / "scripts" / "reconstruct_local.py",
        colmap_binary=_executable(tmp_path / "colmap", b"fake-colmap"),
        brush_binary=_executable(tmp_path / "brush_app", b"fake-brush"),
        trainer_version="0.3.0",
        total_steps=20,
        max_resolution=128,
        random_seed=17,
        gpu_name="wgpu/Metal test device",
        gpu_memory_mb=0,
        driver_version="macOS-test",
    )


class _FakeRunner:
    def __init__(
        self,
        config: LocalBrushExecutorConfig,
        *,
        reconstruct_returncode: int = 0,
        emit_receipt: bool = True,
        mutate: str | None = None,
    ):
        self.config = config
        self.reconstruct_returncode = reconstruct_returncode
        self.emit_receipt = emit_receipt
        self.mutate = mutate
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        argv = [str(item) for item in argv]
        self.calls.append((argv, kwargs))
        if len(argv) > 1 and argv[1] == "image_deleter":
            output = Path(argv[argv.index("--output_path") + 1])
            output.mkdir(parents=True, exist_ok=True)
            (output / "cameras.bin").write_bytes(b"camera-model")
            (output / "images.bin").write_bytes(
                struct.pack("<Q", 2) + b"training-cameras"
            )
            (output / "points3D.bin").write_bytes(b"training-points")
            return subprocess.CompletedProcess(argv, 0, b"deleted", b"")

        if self.reconstruct_returncode != 0:
            return subprocess.CompletedProcess(
                argv,
                self.reconstruct_returncode,
                b"",
                b"brush failed",
            )
        if not self.emit_receipt:
            return subprocess.CompletedProcess(argv, 0, b"no receipt", b"")

        workspace = Path(argv[argv.index("--work") + 1])
        receipt_path = Path(argv[argv.index("--receipt-out") + 1])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "brush.log").write_bytes(b"verified brush log\n")
        (workspace / "trained.brush-export.ply").write_bytes(
            b"ply\nformat ascii 1.0\nelement vertex 1\nend_header\n0 0 0\n"
        )
        brush_argv = [
            str(self.config.brush_binary),
            str(workspace),
            "--total-steps",
            str(self.config.total_steps),
            "--max-resolution",
            str(self.config.max_resolution),
            "--seed",
            str(self.config.random_seed),
            "--export-every",
            str(self.config.total_steps),
            "--export-path",
            str(workspace),
            "--export-name",
            "trained.ply",
        ]
        receipt = build_local_brush_execution_receipt(
            workspace=workspace,
            brush_binary=self.config.brush_binary,
            brush_argv=brush_argv,
            brush_stage_fingerprint="7" * 64,
            brush_started_at_utc=_T0,
            brush_finished_at_utc=_T0 + timedelta(seconds=3),
            returncode=0,
        )
        write_local_brush_execution_receipt(receipt_path, receipt)
        if self.mutate == "ply":
            (workspace / "trained.brush-export.ply").write_bytes(b"mutated")
        elif self.mutate == "log":
            (workspace / "brush.log").write_bytes(b"mutated")
        elif self.mutate == "config":
            (
                self.config.execution_root
                / "operator-intent-config.yml"
            ).write_bytes(b"mutated")
        return subprocess.CompletedProcess(
            argv,
            0,
            b"verified local preview",
            b"",
        )


def _patch_bundle_verification(
    monkeypatch: pytest.MonkeyPatch,
    bundle: VerifiedTrainingJobBundle,
) -> None:
    monkeypatch.setattr(
        local_brush_module,
        "verify_training_job_bundle",
        lambda path: bundle,
    )
    monkeypatch.setattr(
        local_brush_module,
        "load_training_job_input_bytes",
        lambda verified: _input_bytes(),
    )


def test_local_brush_executor_produces_verified_preview_only_result(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    config = _config(tmp_path)
    runner = _FakeRunner(config)
    _patch_bundle_verification(monkeypatch, bundle)

    result = LocalBrushExecutor(config, run_command=runner).run(bundle)

    assert result.training_request.training_config.trainer_name == "brush"
    assert result.receipt.quality_role == "preview-only"
    assert result.receipt.state == "succeeded"
    assert result.training_result.training_status.state == "completed"
    assert (
        result.training_result.gpu_environment.cuda_version
        == "not-applicable"
    )
    assert result.execution_receipt.quality_role == "preview-only"
    assert {
        path.name for path in result.precomputed_colmap_root.joinpath(
            "images"
        ).iterdir()
    } == {"train-a.png", "train-b.png"}
    assert (
        result.held_out_names_path.read_text(encoding="utf-8").splitlines()
        == ["held-out.png"]
    )
    assert runner.calls[0][0][:2] == [
        str(config.colmap_binary),
        "image_deleter",
    ]
    reconstruct_argv, reconstruct_kwargs = runner.calls[1]
    assert "--precomputed-colmap" in reconstruct_argv
    assert "--resume" in reconstruct_argv
    assert "--stop-after-brush" in reconstruct_argv
    assert reconstruct_argv[
        reconstruct_argv.index("--brush-seed") + 1
    ] == "17"
    assert reconstruct_kwargs["env"]["PATH"].split(":")[:2] == [
        str(config.brush_binary.parent),
        str(config.colmap_binary.parent),
    ]


def test_local_brush_executor_rejects_nonzero_reconstruct_exit(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    config = _config(tmp_path)
    _patch_bundle_verification(monkeypatch, bundle)

    with pytest.raises(LocalBrushExecutionError, match="code 17"):
        LocalBrushExecutor(
            config,
            run_command=_FakeRunner(config, reconstruct_returncode=17),
        ).run(bundle)
    assert (
        config.execution_root / "reconstruct.stderr.log"
    ).read_bytes() == b"brush failed"


def test_local_brush_executor_rejects_exit_zero_without_receipt_or_ply(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    config = _config(tmp_path)
    _patch_bundle_verification(monkeypatch, bundle)

    with pytest.raises(LocalBrushExecutionError, match="receipt"):
        LocalBrushExecutor(
            config,
            run_command=_FakeRunner(config, emit_receipt=False),
        ).run(bundle)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (("ply", "PLY"), ("log", "log"), ("config", "config")),
)
def test_local_brush_executor_rejects_post_execution_content_drift(
    tmp_path,
    monkeypatch,
    mutation,
    message,
):
    bundle = _fixture_bundle(tmp_path)
    config = _config(tmp_path)
    _patch_bundle_verification(monkeypatch, bundle)

    with pytest.raises(LocalBrushExecutionError, match=message):
        LocalBrushExecutor(
            config,
            run_command=_FakeRunner(config, mutate=mutation),
        ).run(bundle)


def test_local_brush_preview_receipt_cannot_become_production_gate(
    tmp_path,
    monkeypatch,
):
    bundle = _fixture_bundle(tmp_path)
    config = _config(tmp_path)
    _patch_bundle_verification(monkeypatch, bundle)
    result = LocalBrushExecutor(
        config,
        run_command=_FakeRunner(config),
    ).run(bundle)

    with pytest.raises(ValidationError, match="preview-only"):
        new_attempt(
            result.receipt.input_identity,
            attempt_id="production-claim",
            created_at_utc=_T0 + timedelta(minutes=1),
            quality_role="production",
        )
