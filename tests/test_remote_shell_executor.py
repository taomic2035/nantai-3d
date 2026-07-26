from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import pipeline.remote_shell_executor as remote_module
from pipeline.real_dataset import canonical_model_bytes
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
