from __future__ import annotations

import base64
import hashlib
import shutil
import struct
import subprocess
import zlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import pipeline.remote_shell_executor as remote_module
from pipeline.durable_io import DurableIOError
from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    HeldOutSplit,
    TrainingImageIdentity,
    held_out_split_canonical_bytes,
)
from pipeline.remote_shell_executor import (
    RemoteResultBundleError,
    RemoteResultBundleManifest,
    RemoteResultBundleMember,
    RemoteShellExecutionError,
    RemoteShellExecutor,
    RemoteShellExecutorConfig,
    RemoteShellStatus,
    build_remote_result_bundle,
    canonical_remote_result_manifest_bytes,
    canonical_remote_status_bytes,
    verify_remote_result_bundle,
)
from pipeline.render_evaluation import (
    RenderCameraRecord,
    RenderEvaluationPolicy,
    RenderEvaluationProtocol,
    RenderEvaluationReport,
    RenderFrameMetric,
    canonical_render_evaluation_bytes,
    render_artifact_stem,
    render_evaluation_sha256,
)
from pipeline.training_provenance import (
    GpuEnvironment,
    TrainingConfig,
    TrainingInputBinding,
    TrainingRequest,
    build_training_result,
    request_canonical_sha256,
)

_T0 = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _request() -> TrainingRequest:
    payload = b"split\n"
    return TrainingRequest(
        request_id="remote-production",
        created_at_utc=_T0,
        input_bindings=(
            TrainingInputBinding(
                artifact_kind="held_out_split",
                artifact_sha256=_sha(payload),
                artifact_path="training/held-out-split.json",
                artifact_size_bytes=len(payload),
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
        requested_config_sha256=_sha(b"config\n"),
    )


def _bundle(tmp_path):
    path = tmp_path / "training-job.zip"
    path.write_bytes(b"verified-production-bundle")
    return SimpleNamespace(
        path=path,
        bundle_sha256=_sha(path.read_bytes()),
        manifest=SimpleNamespace(dataset_receipt_sha256="b" * 64),
        request=_request(),
    )


def _fingerprint(blob: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha256(blob).digest())
    return "SHA256:" + encoded.decode("ascii").rstrip("=")


def _executable(path: Path) -> Path:
    path.write_bytes(b"fake executable")
    path.chmod(0o755)
    return path


def _config(tmp_path) -> RemoteShellExecutorConfig:
    key_blob = b"operator-owned-test-host-key"
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        "remote.example ssh-ed25519 "
        + base64.b64encode(key_blob).decode("ascii")
        + "\n",
        encoding="ascii",
    )
    private_key = tmp_path / "id_ed25519"
    private_key.write_bytes(b"private-key")
    private_key.chmod(0o600)
    return RemoteShellExecutorConfig(
        ssh_binary=_executable(tmp_path / "ssh"),
        scp_binary=_executable(tmp_path / "scp"),
        private_key_path=private_key,
        known_hosts_path=known_hosts,
        expected_host_key_fingerprint=_fingerprint(key_blob),
        ssh_target="gpu-prod",
        known_host="remote.example",
        port=2222,
        remote_root="/srv/nantai-jobs",
        remote_repo_root="/srv/nantai-3d",
        container_identity=(
            "registry.example/nantai@sha256:" + ("c" * 64)
        ),
    )


class _Runner:
    def __init__(self):
        self.calls = []
        self.responses: list[subprocess.CompletedProcess] = []
        self.download_source: Path | None = None

    def __call__(self, argv, **kwargs):
        self.calls.append((tuple(str(item) for item in argv), kwargs))
        if (
            self.download_source is not None
            and str(argv[0]).endswith("scp")
            and any(
                "result-bundle.zip" in str(item)
                for item in argv
            )
        ):
            shutil.copyfile(self.download_source, Path(argv[-1]))
        if self.responses:
            return self.responses.pop(0)
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def _prepared_executor(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(
        remote_module,
        "verify_production_training_job_bundle",
        lambda path: bundle,
    )
    runner = _Runner()
    executor = RemoteShellExecutor(
        _config(tmp_path),
        run_command=runner,
        now=lambda: _T0,
    )
    prepared = executor.prepare(bundle)
    return executor, runner, prepared


def test_submit_uses_strict_host_key_no_shell_and_redacts_key(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)

    job = executor.submit(prepared)

    assert job.executor_kind == "remote-shell-nerfstudio"
    assert len(runner.calls) == 3
    for argv, kwargs in runner.calls:
        assert kwargs["shell"] is False
        assert "StrictHostKeyChecking=yes" in argv
        assert "UserKnownHostsFile=" + str(
            executor.config.known_hosts_path
        ) in argv
    audit = "\n".join(" ".join(argv) for argv in executor.command_audit)
    assert str(executor.config.private_key_path) not in audit
    assert "<redacted-private-key>" in audit


def test_unreachable_poll_returns_unknown(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    runner.responses.append(
        subprocess.CompletedProcess([], 255, b"", b"network unreachable"),
    )

    observation = executor.poll(job)

    assert observation.state == "unknown"
    assert observation.result_bundle_sha256 is None


def test_poll_rejects_changed_job_identity(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    changed = RemoteShellStatus(
        job_id="changed-job",
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        state="running",
        updated_at_utc=_T0,
    )
    runner.responses.append(
        subprocess.CompletedProcess(
            [],
            0,
            canonical_remote_status_bytes(changed),
            b"",
        ),
    )

    with pytest.raises(RemoteShellExecutionError, match="identity"):
        executor.poll(job)


def test_config_rejects_controls_and_host_fingerprint_mismatch(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(ValidationError):
        config.model_copy(
            update={"ssh_target": "gpu-prod\nProxyCommand=bad"},
        ).model_validate(
            config.model_copy(
                update={"ssh_target": "gpu-prod\nProxyCommand=bad"},
            ).model_dump()
        )

    bad = config.model_copy(
        update={"expected_host_key_fingerprint": "SHA256:" + ("A" * 43)},
    )
    with pytest.raises(RemoteShellExecutionError, match="fingerprint"):
        RemoteShellExecutor(bad)


def _write_result_archive(
    path: Path,
    *,
    request_sha256: str,
    extra_member: tuple[str, bytes] | None = None,
) -> None:
    members_by_path = {
        "container-identity.txt": b"container\n",
        "dataparser_transforms.json": b"{}\n",
        "operator-intent-config.yml": b"config\n",
        "point_cloud.ply": b"ply\n",
        "training-request.json": b"{}\n",
        "training-result.json": b"{}\n",
        "training.log": b"log\n",
        "worker.stderr.log": b"",
        "worker.stdout.log": b"container completed\n",
    }
    members = tuple(
        RemoteResultBundleMember(
            path=name,
            byte_length=len(payload),
            sha256=_sha(payload),
        )
        for name, payload in sorted(members_by_path.items())
    )
    manifest = RemoteResultBundleManifest(
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256=request_sha256,
        training_bundle_sha256="d" * 64,
        container_identity=(
            "registry.example/nantai@sha256:" + ("c" * 64)
        ),
        members=members,
    )
    import zipfile

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(members_by_path.items()):
            archive.writestr(name, payload)
        if extra_member is not None:
            archive.writestr(*extra_member)


def test_result_bundle_rejects_traversal_size_and_request_drift(tmp_path):
    expected_request_sha = request_canonical_sha256(_request())
    traversal = tmp_path / "traversal.zip"
    _write_result_archive(
        traversal,
        request_sha256=expected_request_sha,
        extra_member=("../escape", b"bad"),
    )
    with pytest.raises(RemoteResultBundleError, match="portable"):
        verify_remote_result_bundle(
            traversal,
            expected_job_id="job-expected",
            expected_attempt_id="attempt-expected",
            expected_request_sha256=expected_request_sha,
            expected_training_bundle_sha256="d" * 64,
            expected_container_identity=(
                "registry.example/nantai@sha256:" + ("c" * 64)
            ),
        )


def test_result_bundle_builder_is_deterministic_and_verifiable(tmp_path):
    request_sha = request_canonical_sha256(_request())
    result_root = tmp_path / "result"
    result_root.mkdir()
    members = {
        "container-identity.txt": (
            "registry.example/nantai@sha256:" + ("c" * 64) + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": (
            b'{"scale":1.0,"transform":'
            b'[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n'
        ),
        "operator-intent-config.yml": b"config\n",
        "point_cloud.ply": b"ply\n",
        "training-request.json": b"{}\n",
        "training-result.json": b"{}\n",
        "training.log": b"log\n",
        "worker.stderr.log": b"",
        "worker.stdout.log": b"container completed\n",
    }
    for name, payload in members.items():
        (result_root / name).write_bytes(payload)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    kwargs = {
        "result_root": result_root,
        "job_id": "job-expected",
        "attempt_id": "attempt-expected",
        "request_sha256": request_sha,
        "training_bundle_sha256": "d" * 64,
        "container_identity": (
            "registry.example/nantai@sha256:" + ("c" * 64)
        ),
    }

    one = build_remote_result_bundle(output_path=first, **kwargs)
    two = build_remote_result_bundle(output_path=second, **kwargs)

    assert one.bundle_sha256 == two.bundle_sha256
    assert first.read_bytes() == second.read_bytes()
    verified = verify_remote_result_bundle(
        first,
        expected_job_id="job-expected",
        expected_attempt_id="attempt-expected",
        expected_request_sha256=request_sha,
        expected_training_bundle_sha256="d" * 64,
        expected_container_identity=(
            "registry.example/nantai@sha256:" + ("c" * 64)
        ),
    )
    assert verified.member_bytes == members
    expected_request_sha = request_sha

    oversized = tmp_path / "oversized.zip"
    _write_result_archive(
        oversized,
        request_sha256=expected_request_sha,
    )
    with pytest.raises(RemoteResultBundleError, match="size"):
        verify_remote_result_bundle(
            oversized,
            expected_job_id="job-expected",
            expected_attempt_id="attempt-expected",
            expected_request_sha256=expected_request_sha,
            expected_training_bundle_sha256="d" * 64,
            expected_container_identity=(
                "registry.example/nantai@sha256:" + ("c" * 64)
            ),
            max_archive_bytes=8,
        )

    drifted = tmp_path / "drifted.zip"
    _write_result_archive(
        drifted,
        request_sha256="e" * 64,
    )
    with pytest.raises(RemoteResultBundleError, match="request"):
        verify_remote_result_bundle(
            drifted,
            expected_job_id="job-expected",
            expected_attempt_id="attempt-expected",
            expected_request_sha256=expected_request_sha,
            expected_training_bundle_sha256="d" * 64,
            expected_container_identity=(
                "registry.example/nantai@sha256:" + ("c" * 64)
            ),
        )


def test_result_bundle_sync_failure_never_exposes_final(
    tmp_path,
    monkeypatch,
):
    request_sha = request_canonical_sha256(_request())
    result_root = tmp_path / "result"
    result_root.mkdir()
    members = {
        "container-identity.txt": (
            "registry.example/nantai@sha256:" + ("c" * 64) + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": (
            b'{"scale":1.0,"transform":'
            b'[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n'
        ),
        "operator-intent-config.yml": b"config\n",
        "point_cloud.ply": b"ply\n",
        "training-request.json": b"{}\n",
        "training-result.json": b"{}\n",
        "training.log": b"log\n",
        "worker.stderr.log": b"",
        "worker.stdout.log": b"container completed\n",
    }
    for name, payload in members.items():
        (result_root / name).write_bytes(payload)
    output = tmp_path / "result.zip"
    flushed: list[Path] = []

    def fail_flush(path):
        flushed.append(Path(path))
        assert Path(path) != output
        raise OSError("simulated durable flush failure")

    monkeypatch.setattr("pipeline.durable_io.flush_file", fail_flush)

    with pytest.raises(RemoteResultBundleError, match="cannot be written"):
        build_remote_result_bundle(
            result_root=result_root,
            output_path=output,
            job_id="job-expected",
            attempt_id="attempt-expected",
            request_sha256=request_sha,
            training_bundle_sha256="d" * 64,
            container_identity=(
                "registry.example/nantai@sha256:" + ("c" * 64)
            ),
        )

    assert flushed
    assert not output.exists()
    assert tuple(tmp_path.glob(".result.zip.*.staging")) == ()


def test_result_bundle_publish_failure_does_not_promote_stale_staging(
    tmp_path,
    monkeypatch,
):
    request_sha = request_canonical_sha256(_request())
    result_root = tmp_path / "result"
    result_root.mkdir()
    members = {
        "container-identity.txt": (
            "registry.example/nantai@sha256:" + ("c" * 64) + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": (
            b'{"scale":1.0,"transform":'
            b'[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n'
        ),
        "operator-intent-config.yml": b"config\n",
        "point_cloud.ply": b"ply\n",
        "training-request.json": b"{}\n",
        "training-result.json": b"{}\n",
        "training.log": b"log\n",
        "worker.stderr.log": b"",
        "worker.stdout.log": b"container completed\n",
    }
    for name, payload in members.items():
        (result_root / name).write_bytes(payload)
    output = tmp_path / "result.zip"
    stale = tmp_path / ".result.zip.stale.staging"
    stale.write_bytes(b"untrusted")

    def fail_publish(source, destination):
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(
        "pipeline.durable_io.publish_file_noreplace",
        fail_publish,
    )

    with pytest.raises(RemoteResultBundleError, match="cannot be written"):
        build_remote_result_bundle(
            result_root=result_root,
            output_path=output,
            job_id="job-expected",
            attempt_id="attempt-expected",
            request_sha256=request_sha,
            training_bundle_sha256="d" * 64,
            container_identity=(
                "registry.example/nantai@sha256:" + ("c" * 64)
            ),
        )

    assert not output.exists()
    assert stale.read_bytes() == b"untrusted"
    assert tuple(tmp_path.glob(".result.zip.*.staging")) == (stale,)


def test_result_bundle_reports_published_when_sync_is_unconfirmed(
    tmp_path,
    monkeypatch,
):
    request_sha = request_canonical_sha256(_request())
    result_root = tmp_path / "result"
    result_root.mkdir()
    members = {
        "container-identity.txt": (
            "registry.example/nantai@sha256:" + ("c" * 64) + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": (
            b'{"scale":1.0,"transform":'
            b'[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n'
        ),
        "operator-intent-config.yml": b"config\n",
        "point_cloud.ply": b"ply\n",
        "training-request.json": b"{}\n",
        "training-result.json": b"{}\n",
        "training.log": b"log\n",
        "worker.stderr.log": b"",
        "worker.stdout.log": b"container completed\n",
    }
    for name, payload in members.items():
        (result_root / name).write_bytes(payload)
    output = tmp_path / "result.zip"

    def publish_then_fail(source, destination):
        source.replace(destination)
        raise DurableIOError(
            "simulated directory sync failure",
            published=True,
        )

    monkeypatch.setattr(
        "pipeline.durable_io.publish_file_noreplace",
        publish_then_fail,
    )

    with pytest.raises(
        RemoteResultBundleError,
        match="published but durability is unconfirmed",
    ):
        build_remote_result_bundle(
            result_root=result_root,
            output_path=output,
            job_id="job-expected",
            attempt_id="attempt-expected",
            request_sha256=request_sha,
            training_bundle_sha256="d" * 64,
            container_identity=(
                "registry.example/nantai@sha256:" + ("c" * 64)
            ),
        )

    verify_remote_result_bundle(
        output,
        expected_job_id="job-expected",
        expected_attempt_id="attempt-expected",
        expected_request_sha256=request_sha,
        expected_training_bundle_sha256="d" * 64,
        expected_container_identity=(
            "registry.example/nantai@sha256:" + ("c" * 64)
        ),
    )


def _evaluation_png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    width, height = 800, 600
    rows = b"".join(
        b"\x00" + bytes([row % 251]) * (width * 3)
        for row in range(height)
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


def _write_evaluation_members(
    result_root: Path,
) -> tuple[dict[str, bytes], bytes, bytes]:
    protocol = RenderEvaluationProtocol(
        width=800,
        height=600,
        crop_mode="center-crop",
        colour_space="srgb",
        alpha_handling="reject",
        mask_handling="none",
        ssim_window_size=11,
        ssim_sigma=1.5,
        ssim_data_range=1.0,
        lpips_backbone="alex",
    )
    container = "registry.example/nantai@sha256:" + "c" * 64
    source = b"source"
    identity = TrainingImageIdentity(
        logical_path="eval.jpg",
        sha256=_sha(source),
    )
    split = HeldOutSplit(
        ratio=0.5,
        total_count=2,
        held_out=(identity,),
        train=(
            TrainingImageIdentity(
                logical_path="train.jpg",
                sha256="f" * 64,
            ),
        ),
    )
    split_bytes = held_out_split_canonical_bytes(split)
    transforms = b'{"test_filenames":["images/eval.jpg"]}\n'
    policy = RenderEvaluationPolicy(
        held_out_split_sha256=_sha(split_bytes),
        transforms_sha256=_sha(transforms),
        evaluator_container_digest=container,
        protocol=protocol,
        minimum_mean_psnr=24.0,
        minimum_mean_ssim=0.8,
        maximum_mean_lpips=0.25,
        minimum_worst_psnr=18.0,
    )
    stem = render_artifact_stem("eval.jpg")
    render = _evaluation_png()
    camera = canonical_render_evaluation_bytes(
        RenderCameraRecord(
            frame_id="eval.jpg",
            source_path="prepared/images/eval.jpg",
            source_sha256=_sha(source),
            transforms_sha256=_sha(transforms),
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
        )
    )
    trainer_config = b"method_name: splatfacto\n"
    frame = RenderFrameMetric(
        frame_id="eval.jpg",
        source_path="prepared/images/eval.jpg",
        source_byte_length=len(source),
        source_sha256=_sha(source),
        render_path=f"result/render-evaluation/renders/{stem}.png",
        render_byte_length=len(render),
        render_sha256=_sha(render),
        camera_path=f"result/render-evaluation/cameras/{stem}.json",
        camera_byte_length=len(camera),
        camera_sha256=_sha(camera),
        psnr=25.0,
        ssim=0.85,
        lpips=0.2,
    )
    report = RenderEvaluationReport(
        evaluation_id="eval-production",
        policy_sha256=render_evaluation_sha256(policy),
        held_out_split_sha256=policy.held_out_split_sha256,
        evaluator_container_digest=container,
        protocol=protocol,
        frames=(frame,),
        trainer_config_sha256=_sha(trainer_config),
        mean_psnr=25.0,
        mean_ssim=0.85,
        mean_lpips=0.2,
        worst_psnr=25.0,
    )
    members = {
        "render-evaluation/policy.json":
            canonical_render_evaluation_bytes(policy),
        "render-evaluation/report.json":
            canonical_render_evaluation_bytes(report),
        "render-evaluation/trainer-config.yml": trainer_config,
        "render-evaluation/transforms.json": transforms,
        f"render-evaluation/renders/{stem}.png": render,
        f"render-evaluation/cameras/{stem}.json": camera,
    }
    for name, payload in members.items():
        path = result_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return members, split_bytes, source


def test_result_bundle_binds_declared_evaluation_tree(tmp_path):
    request_sha = request_canonical_sha256(_request())
    result_root = tmp_path / "result"
    result_root.mkdir()
    core = {
        "container-identity.txt": (
            "registry.example/nantai@sha256:" + ("c" * 64) + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": (
            b'{"scale":1.0,"transform":'
            b'[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n'
        ),
        "operator-intent-config.yml": b"config\n",
        "point_cloud.ply": b"ply\n",
        "training-request.json": b"{}\n",
        "training-result.json": b"{}\n",
        "training.log": b"log\n",
        "worker.stderr.log": b"",
        "worker.stdout.log": b"container completed\n",
    }
    for name, payload in core.items():
        (result_root / name).write_bytes(payload)
    evaluation, _split_bytes, _source = _write_evaluation_members(
        result_root
    )

    verified = build_remote_result_bundle(
        result_root=result_root,
        output_path=tmp_path / "result.zip",
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256=request_sha,
        training_bundle_sha256="d" * 64,
        container_identity=(
            "registry.example/nantai@sha256:" + ("c" * 64)
        ),
    )

    assert verified.member_bytes == {**core, **evaluation}

    (result_root / "render-evaluation/extra.txt").write_text(
        "extra\n",
        encoding="ascii",
    )
    with pytest.raises(RemoteResultBundleError, match="evaluation"):
        build_remote_result_bundle(
            result_root=result_root,
            output_path=tmp_path / "extra.zip",
            job_id="job-expected",
            attempt_id="attempt-expected",
            request_sha256=request_sha,
            training_bundle_sha256="d" * 64,
            container_identity=(
                "registry.example/nantai@sha256:" + ("c" * 64)
            ),
        )


def test_downloaded_evaluation_reopens_every_bound_byte(
    tmp_path,
    monkeypatch,
):
    request_sha = request_canonical_sha256(_request())
    result_root = tmp_path / "result"
    result_root.mkdir()
    core = {
        "container-identity.txt": (
            "registry.example/nantai@sha256:" + ("c" * 64) + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": (
            b'{"scale":1.0,"transform":'
            b'[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n'
        ),
        "operator-intent-config.yml": b"config\n",
        "point_cloud.ply": b"ply\n",
        "training-request.json": b"{}\n",
        "training-result.json": b"{}\n",
        "training.log": b"log\n",
        "worker.stderr.log": b"",
        "worker.stdout.log": b"",
    }
    for name, payload in core.items():
        (result_root / name).write_bytes(payload)
    _evaluation, split_bytes, source = _write_evaluation_members(
        result_root
    )
    verified = build_remote_result_bundle(
        result_root=result_root,
        output_path=tmp_path / "result.zip",
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256=request_sha,
        training_bundle_sha256="d" * 64,
        container_identity=(
            "registry.example/nantai@sha256:" + ("c" * 64)
        ),
    )
    monkeypatch.setattr(
        remote_module,
        "_load_held_out_source_bytes",
        lambda bundle, split: {"eval.jpg": source},
    )

    remote_module._validate_downloaded_evaluation(
        verified,
        SimpleNamespace(),
        split_bytes,
    )

    tampered = dict(verified.member_bytes)
    camera_name = next(
        name
        for name in tampered
        if name.startswith("render-evaluation/cameras/")
    )
    tampered[camera_name] += b"tamper"
    with pytest.raises(RemoteResultBundleError, match="evaluation"):
        remote_module._validate_downloaded_evaluation(
            SimpleNamespace(member_bytes=tampered),
            SimpleNamespace(),
            split_bytes,
        )


def test_fetch_only_succeeds_after_local_provenance_closure(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    request = prepared.bundle.request
    config_bytes = b"config\n"
    ply_bytes = b"ply\n"
    log_bytes = b"training complete\n"
    transform_bytes = (
        b'{"scale":1.0,"transform":'
        b'[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n'
    )
    input_bytes = {"training/held-out-split.json": b"split\n"}
    result = build_training_result(
        request=request,
        result_id="remote-closed",
        started_at_utc=_T0,
        finished_at_utc=_T0,
        actual_trainer_name="nerfstudio-splatfacto",
        actual_trainer_version="1.1.5",
        actual_config_bytes=config_bytes,
        actual_ply_bytes=ply_bytes,
        actual_log_bytes=log_bytes,
        dataparser_transform_bytes=transform_bytes,
        input_bytes_by_path=input_bytes,
        gpu_environment=GpuEnvironment(
            gpu_name="NVIDIA T4",
            gpu_memory_mb=15109,
            cuda_version="12.1",
            driver_version="535.0",
        ),
        exit_code=0,
    )
    result_root = tmp_path / "remote-result"
    result_root.mkdir()
    container = executor.config.container_identity
    payloads = {
        "container-identity.txt": (container + "\n").encode("ascii"),
        "dataparser_transforms.json": transform_bytes,
        "operator-intent-config.yml": config_bytes,
        "point_cloud.ply": ply_bytes,
        "training-request.json": canonical_model_bytes(request),
        "training-result.json": canonical_model_bytes(result),
        "training.log": log_bytes,
        "worker.stderr.log": b"",
        "worker.stdout.log": b"",
    }
    for name, payload in payloads.items():
        (result_root / name).write_bytes(payload)
    archive = tmp_path / "remote-result.zip"
    verified = build_remote_result_bundle(
        result_root=result_root,
        output_path=archive,
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        container_identity=container,
    )
    empty_sha = _sha(b"")
    status = RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        state="succeeded",
        updated_at_utc=_T0,
        exit_code=0,
        stdout_sha256=empty_sha,
        stderr_sha256=empty_sha,
        result_bundle_sha256=verified.bundle_sha256,
        result_bundle_size_bytes=verified.byte_length,
    )
    runner.responses.append(
        subprocess.CompletedProcess(
            [],
            0,
            canonical_remote_status_bytes(status),
            b"",
        )
    )
    runner.download_source = archive
    monkeypatch.setattr(
        remote_module,
        "load_training_job_input_bytes",
        lambda bundle: input_bytes,
    )

    assert executor.poll(job).state == "unknown"
    destination = tmp_path / "published-result"
    receipt = executor.fetch(job, destination)

    assert receipt.state == "succeeded"
    assert receipt.quality_role == "production"
    assert receipt.result_bundle_sha256 == verified.bundle_sha256
    assert (destination / "point_cloud.ply").read_bytes() == ply_bytes
    assert (
        destination / "dataparser_transforms.json"
    ).read_bytes() == transform_bytes
