from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import traceback
import zlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import pipeline.remote_shell_executor as remote_module
from pipeline.durable_io import DurableIOError
from pipeline.production_runtime_evidence import (
    ProductionRuntimePolicy,
    canonical_production_runtime_policy_bytes,
    training_cli_schema_sha256,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    HeldOutSplit,
    TrainingImageIdentity,
    held_out_split_canonical_bytes,
)
from pipeline.remote_shell_executor import (
    RemoteContainerLifecycleReceipt,
    RemoteResultBundleError,
    RemoteResultBundleManifest,
    RemoteResultBundleMember,
    RemoteShellExecutionError,
    RemoteShellExecutor,
    RemoteShellExecutorConfig,
    RemoteShellJobRef,
    RemoteShellPreflightReport,
    RemoteShellStatus,
    build_remote_result_bundle,
    canonical_container_lifecycle_bytes,
    canonical_remote_result_manifest_bytes,
    canonical_remote_shell_job_ref_bytes,
    canonical_remote_shell_preflight_bytes,
    canonical_remote_status_bytes,
    compute_container_lifecycle_sha256,
    compute_workspace_identity_sha256,
    load_container_lifecycle_receipt,
    run_remote_shell_preflight,
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
from pipeline.training_executor import TrainingExecutorError
from pipeline.training_provenance import (
    GpuEnvironment,
    TrainingConfig,
    TrainingInputBinding,
    TrainingRequest,
    build_training_result,
    request_canonical_sha256,
)

_T0 = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
_T1 = datetime(2026, 7, 26, 12, 1, tzinfo=UTC)


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


def _protect_private_key(
    path: Path,
    *,
    extra_allowed_sid: str | None = None,
    null_dacl: bool = False,
) -> None:
    if os.name != "nt":
        path.chmod(0o600)
        return
    import win32api
    import win32con
    import win32security

    dacl = None
    if not null_dacl:
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32con.TOKEN_QUERY,
        )
        current_sid = win32security.GetTokenInformation(
            token,
            win32security.TokenUser,
        )[0]
        allowed = [
            current_sid,
            win32security.ConvertStringSidToSid("S-1-5-18"),
            win32security.ConvertStringSidToSid("S-1-5-32-544"),
        ]
        if extra_allowed_sid is not None:
            allowed.append(
                win32security.ConvertStringSidToSid(extra_allowed_sid)
            )
        dacl = win32security.ACL()
        for sid in allowed:
            dacl.AddAccessAllowedAce(
                win32security.ACL_REVISION,
                win32con.GENERIC_ALL,
                sid,
            )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        (
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION
        ),
        None,
        None,
        dacl,
        None,
    )


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
    _protect_private_key(private_key)
    container_identity = (
        "registry.example/nantai@sha256:" + ("c" * 64)
    )
    policy = _runtime_policy(container_identity)
    runtime_policy_path = tmp_path / "production-runtime-policy.json"
    runtime_policy_path.write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )
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
        container_identity=container_identity,
        expected_worker_sha256="d" * 64,
        expected_worker_version="1.0.0",
        expected_checker_config_sha256="e" * 64,
        remote_target_sha256="2" * 64,
        runtime_policy_path=runtime_policy_path,
        expected_runtime_policy_sha256=policy.content_sha256,
    )


def _runtime_policy(
    container_identity: str,
) -> ProductionRuntimePolicy:
    options = (
        "--auto-scale-poses",
        "--center-method",
        "--orientation-method",
        "--scale-factor",
    )
    return ProductionRuntimePolicy.create(
        expected_exact_commit="1" * 40,
        expected_remote_target_sha256="2" * 64,
        expected_probe_set_sha256="3" * 64,
        expected_container_identity=container_identity,
        expected_gpu_uuid="GPU-12345678-1234-1234-1234-123456789abc",
        min_gpu_memory_mib=16_384,
        expected_cuda_runtime_version="12.4",
        expected_python_version="3.11.9",
        expected_nerfstudio_version="1.1.5",
        expected_training_cli_schema_sha256=(
            training_cli_schema_sha256(
                trainer_name="nerfstudio-splatfacto",
                observed_options=options,
            )
        ),
        required_training_cli_options=options,
        expected_checker_sha256="4" * 64,
        expected_container_runtime_sha256="5" * 64,
        expected_nvidia_smi_sha256="6" * 64,
        expected_python_sha256="7" * 64,
        expected_training_cli_sha256="8" * 64,
        expected_worker_sha256="d" * 64,
    )


def _config_with_runtime_policy(
    tmp_path,
    *,
    expected_sha256: str | None = None,
) -> tuple[RemoteShellExecutorConfig, ProductionRuntimePolicy]:
    base = _config(tmp_path)
    policy = _runtime_policy(base.container_identity)
    policy_path = tmp_path / "production-runtime-policy.json"
    policy_path.write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )
    fields = base.model_dump()
    fields.update(
        {
            "runtime_policy_path": policy_path,
            "expected_runtime_policy_sha256": (
                expected_sha256 or policy.content_sha256
            ),
        }
    )
    return RemoteShellExecutorConfig.model_validate(fields), policy


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


def _readiness_response(
    config: RemoteShellExecutorConfig,
    **updates,
) -> subprocess.CompletedProcess:
    payload = {
        "schema": "nantai.remote-readiness-evidence.v1",
        "checker_version": "nantai.remote-readiness-checker.v1",
        "checker_config_sha256": "e" * 64,
        "container_runtime": config.container_runtime,
        "container_runtime_version": "Docker version 28.0.0",
        "container_identity": config.container_identity,
        "worker_sha256": config.expected_worker_sha256,
        "worker_version": config.expected_worker_version,
        **updates,
    }
    canonical = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    return subprocess.CompletedProcess([], 0, canonical, b"")


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
    assert len(runner.calls) == 4
    for argv, kwargs in runner.calls:
        assert kwargs["shell"] is False
        assert argv[argv.index("-F") + 1] == os.devnull
        assert "StrictHostKeyChecking=yes" in argv
        assert f"GlobalKnownHostsFile={os.devnull}" in argv
        assert "UserKnownHostsFile=" + str(
            executor.config.known_hosts_path
        ) in argv
    audit = "\n".join(" ".join(argv) for argv in executor.command_audit)
    assert str(executor.config.private_key_path) not in audit
    assert "<redacted-private-key>" in audit


def test_submit_uploads_bound_runtime_policy_before_worker_start(
    tmp_path,
    monkeypatch,
):
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(
        remote_module,
        "verify_production_training_job_bundle",
        lambda path: bundle,
    )
    config, policy = _config_with_runtime_policy(tmp_path)
    runner = _Runner()
    executor = RemoteShellExecutor(
        config,
        run_command=runner,
        now=lambda: _T0,
    )

    job = executor.submit(executor.prepare(bundle))

    assert job.runtime_policy_sha256 == policy.content_sha256
    assert len(runner.calls) == 4
    init_argv = runner.calls[0][0]
    assert "--runtime-policy-sha256" in init_argv[-1]
    assert policy.content_sha256 in init_argv[-1]
    assert "--remote-target-sha256" in init_argv[-1]
    assert policy.expected_remote_target_sha256 in init_argv[-1]
    assert "--durable-job-ref-sha256" in init_argv[-1]
    assert hashlib.sha256(
        canonical_remote_shell_job_ref_bytes(job)
    ).hexdigest() in init_argv[-1]
    policy_upload = runner.calls[1][0]
    bundle_upload = runner.calls[2][0]
    start_argv = runner.calls[3][0]
    assert str(config.runtime_policy_path) in policy_upload
    assert "production-runtime-policy.json" in policy_upload[-1]
    assert str(bundle.path) in bundle_upload
    assert "training-job.zip" in bundle_upload[-1]
    assert " start " in f" {start_argv[-1]} "


def test_executor_rejects_runtime_policy_sha_mismatch_before_transport(
    tmp_path,
):
    config, _policy = _config_with_runtime_policy(
        tmp_path,
        expected_sha256="f" * 64,
    )
    runner = _Runner()

    with pytest.raises(
        RemoteShellExecutionError,
        match="runtime policy",
    ):
        RemoteShellExecutor(config, run_command=runner)

    assert runner.calls == []


def test_executor_rejects_runtime_policy_remote_target_mismatch(
    tmp_path,
):
    config, _policy = _config_with_runtime_policy(tmp_path)
    config = config.model_copy(
        update={"remote_target_sha256": "9" * 64}
    )
    runner = _Runner()

    with pytest.raises(
        RemoteShellExecutionError,
        match="runtime policy",
    ):
        RemoteShellExecutor(config, run_command=runner)

    assert runner.calls == []


def test_submit_revalidates_runtime_policy_before_transport(
    tmp_path,
    monkeypatch,
):
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(
        remote_module,
        "verify_production_training_job_bundle",
        lambda path: bundle,
    )
    config, _policy = _config_with_runtime_policy(tmp_path)
    runner = _Runner()
    executor = RemoteShellExecutor(config, run_command=runner)
    prepared = executor.prepare(bundle)
    config.runtime_policy_path.write_bytes(b"{}\n")

    with pytest.raises(
        RemoteShellExecutionError,
        match="runtime policy",
    ):
        executor.submit(prepared)

    assert runner.calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL policy")
@pytest.mark.parametrize(
    "sid",
    [
        "S-1-1-0",
        "S-1-5-11",
        "S-1-5-32-545",
        "S-1-5-21-111-222-333-9999",
    ],
)
def test_windows_private_key_rejects_unapproved_allow_ace(tmp_path, sid):
    config = _config(tmp_path)
    _protect_private_key(
        config.private_key_path,
        extra_allowed_sid=sid,
    )

    with pytest.raises(RemoteShellExecutionError, match="non-owner|ACL"):
        RemoteShellExecutor(config)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL policy")
def test_windows_private_key_rejects_null_dacl(tmp_path):
    config = _config(tmp_path)
    _protect_private_key(config.private_key_path, null_dacl=True)

    with pytest.raises(RemoteShellExecutionError, match="no DACL"):
        RemoteShellExecutor(config)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode policy")
def test_posix_private_key_rejects_group_readable_mode(tmp_path):
    config = _config(tmp_path)
    config.private_key_path.chmod(0o640)

    with pytest.raises(RemoteShellExecutionError, match="too broad"):
        RemoteShellExecutor(config)


def test_transport_exception_chain_does_not_leak_private_key(
    tmp_path,
):
    config = _config(tmp_path)

    def fail(argv, **kwargs):
        del kwargs
        raise subprocess.TimeoutExpired(argv, timeout=1)

    executor = RemoteShellExecutor(config, run_command=fail)
    argv = [
        str(config.ssh_binary),
        *executor._common_options(scp=False),
        "--",
        config.ssh_target,
        "true",
    ]

    with pytest.raises(RemoteShellExecutionError) as caught:
        executor._invoke(argv, phase="probe")

    rendered = "".join(
        traceback.format_exception(caught.value)
    )
    assert str(config.private_key_path) not in rendered
    assert "<redacted-private-key>" in " ".join(
        executor.command_audit[-1]
    )


def test_private_key_constructor_failure_does_not_leak_path(tmp_path):
    config = _config(tmp_path)
    config.private_key_path.unlink()

    with pytest.raises(RemoteShellExecutionError) as caught:
        RemoteShellExecutor(config)

    rendered = "".join(
        traceback.format_exception(caught.value)
    )
    assert str(config.private_key_path) not in rendered


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing")
def test_windows_private_key_guard_blocks_replace_until_close(tmp_path):
    config = _config(tmp_path)
    executor = RemoteShellExecutor(config)
    replacement = tmp_path / "replacement-key"
    replacement.write_bytes(b"replacement")
    _protect_private_key(replacement)

    try:
        with pytest.raises(PermissionError):
            replacement.replace(config.private_key_path)
    finally:
        executor.close()

    replacement.replace(config.private_key_path)
    assert config.private_key_path.read_bytes() == b"replacement"


def test_closed_executor_cannot_invoke_transport(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    executor.close()

    with pytest.raises(RemoteShellExecutionError, match="closed"):
        executor.submit(prepared)

    assert runner.calls == []


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
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="running",
        updated_at_utc=_T0,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
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

    with pytest.raises(
        RemoteResultBundleError,
        match="cannot be written",
    ) as captured:
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

    assert captured.value.published is None
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
        raise DurableIOError(
            "simulated pre-publication failure",
            published=False,
        )

    monkeypatch.setattr(
        "pipeline.durable_io.publish_file_noreplace",
        fail_publish,
    )

    with pytest.raises(
        RemoteResultBundleError,
        match="cannot be written",
    ) as captured:
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

    assert captured.value.published is False
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
    ) as captured:
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

    assert captured.value.published is True
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
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="succeeded",
        updated_at_utc=_T0,
        exit_code=0,
        stdout_sha256=empty_sha,
        stderr_sha256=empty_sha,
        result_bundle_sha256=verified.bundle_sha256,
        result_bundle_size_bytes=verified.byte_length,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
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
    context = executor._context(job)
    assert context.receipt.state == "unknown"
    assert tuple(
        observation.state for observation in context.receipt.observations
    ) == ("not-started", "unknown")
    runner.responses.append(
        _lifecycle_response(_lifecycle_receipt(job)),
    )
    destination = tmp_path / "published-result"
    receipt = executor.fetch(job, destination)

    assert receipt.state == "succeeded"
    assert tuple(observation.state for observation in receipt.observations) == (
        "not-started",
        "unknown",
        "succeeded",
    )
    assert receipt.quality_role == "production"
    assert receipt.result_bundle_sha256 == verified.bundle_sha256
    assert (destination / "point_cloud.ply").read_bytes() == ply_bytes
    assert (
        destination / "dataparser_transforms.json"
    ).read_bytes() == transform_bytes


def test_ssh_options_use_platform_null_device(tmp_path, monkeypatch):
    import os

    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    executor.submit(prepared)

    for argv, _kwargs in runner.calls:
        assert os.devnull in argv
        assert f"GlobalKnownHostsFile={os.devnull}" in argv
        if os.name == "nt":
            assert "/dev/null" not in argv


@pytest.mark.skipif(
    __import__("os").name == "nt",
    reason="POSIX mode bits are not meaningful on Windows",
)
def test_private_key_accepts_owner_only_posix_permissions(tmp_path):
    from pipeline.remote_shell_executor import _assert_private_key_protected

    private_key = tmp_path / "id_strict"
    private_key.write_bytes(b"private-key")
    private_key.chmod(0o600)

    _assert_private_key_protected(private_key)


@pytest.mark.skipif(
    __import__("os").name != "nt",
    reason="ACL check is Windows-only",
)
def test_private_key_windows_fail_closed_without_pywin32(
    tmp_path,
    monkeypatch,
):
    import sys

    private_key = tmp_path / "id_no_pywin32"
    private_key.write_bytes(b"private-key")

    original = sys.modules.get("win32security")
    monkeypatch.setitem(sys.modules, "win32security", None)

    try:
        with pytest.raises(
            RemoteShellExecutionError,
            match="fail-closed",
        ):
            from pipeline.remote_shell_executor import (
                _assert_private_key_protected,
            )

            _assert_private_key_protected(private_key)
    finally:
        if original is not None:
            sys.modules["win32security"] = original


# ---------------------------------------------------------------------------
# P1-3B: checksum / content drift drills
# ---------------------------------------------------------------------------


_BASE_MEMBERS_BY_PATH = {
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


def _build_tamper_archive(
    path: Path,
    *,
    members_by_path: dict[str, bytes] | None = None,
    job_id: str = "job-expected",
    attempt_id: str = "attempt-expected",
    request_sha256: str = "a" * 64,
    training_bundle_sha256: str = "d" * 64,
    container_identity: str = (
        "registry.example/nantai@sha256:" + ("c" * 64)
    ),
    manifest_overrides: dict | None = None,
) -> bytes:
    """Build a result archive with optional member/manifest tampering.

    Returns the archive bytes so callers can write additional mutations.
    """
    import zipfile

    members_by_path = dict(members_by_path or _BASE_MEMBERS_BY_PATH)
    members = tuple(
        RemoteResultBundleMember(
            path=name,
            byte_length=len(payload),
            sha256=_sha(payload),
        )
        for name, payload in sorted(members_by_path.items())
    )
    manifest_kwargs = {
        "job_id": job_id,
        "attempt_id": attempt_id,
        "request_sha256": request_sha256,
        "training_bundle_sha256": training_bundle_sha256,
        "container_identity": container_identity,
        "members": members,
    }
    if manifest_overrides:
        manifest_kwargs.update(manifest_overrides)
    manifest = RemoteResultBundleManifest(**manifest_kwargs)

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(members_by_path.items()):
            archive.writestr(name, payload)
    return path.read_bytes()


def _verify_base_archive(path: Path, **overrides):
    kwargs = {
        "expected_job_id": "job-expected",
        "expected_attempt_id": "attempt-expected",
        "expected_request_sha256": "a" * 64,
        "expected_training_bundle_sha256": "d" * 64,
        "expected_container_identity": (
            "registry.example/nantai@sha256:" + ("c" * 64)
        ),
    }
    kwargs.update(overrides)
    return verify_remote_result_bundle(path, **kwargs)


def test_result_bundle_rejects_member_content_drift(tmp_path):
    """Member bytes drift from manifest sha256 must fail closed.

    Build a correct archive, then rebuild with the SAME manifest but
    different ``training.log`` bytes so the manifest's sha256/size no
    longer matches the actual member content.
    """
    import zipfile

    base_path = tmp_path / "base.zip"
    _build_tamper_archive(base_path)
    good = _verify_base_archive(base_path)
    assert good.member_bytes["training.log"] == b"log\n"

    tampered_path = tmp_path / "tampered.zip"
    tampered_members_by_path = {
        **_BASE_MEMBERS_BY_PATH,
        "training.log": b"tampered-log\n",
    }
    with zipfile.ZipFile(
        tampered_path,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        with zipfile.ZipFile(base_path, "r") as original:
            archive.writestr(
                "result-bundle-manifest.json",
                original.read("result-bundle-manifest.json"),
            )
        for name, payload in sorted(tampered_members_by_path.items()):
            archive.writestr(name, payload)

    with pytest.raises(RemoteResultBundleError, match="sha256/size mismatch"):
        _verify_base_archive(tampered_path)


def test_result_bundle_rejects_manifest_member_sha_drift(tmp_path):
    """Manifest member sha256 lying about actual bytes must fail closed."""
    import zipfile

    path = tmp_path / "archive.zip"
    _build_tamper_archive(path)

    lying_members = (
        RemoteResultBundleMember(
            path="container-identity.txt",
            byte_length=len(_BASE_MEMBERS_BY_PATH["container-identity.txt"]),
            sha256="0" * 64,
        ),
    ) + tuple(
        RemoteResultBundleMember(
            path=name,
            byte_length=len(payload),
            sha256=_sha(payload),
        )
        for name, payload in sorted(_BASE_MEMBERS_BY_PATH.items())
        if name != "container-identity.txt"
    )
    manifest = RemoteResultBundleManifest(
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256="a" * 64,
        training_bundle_sha256="d" * 64,
        container_identity="registry.example/nantai@sha256:" + ("c" * 64),
        members=lying_members,
    )
    path.write_bytes(b"")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(_BASE_MEMBERS_BY_PATH.items()):
            archive.writestr(name, payload)
    with pytest.raises(RemoteResultBundleError, match="sha256/size mismatch"):
        _verify_base_archive(path)


def test_result_bundle_rejects_container_identity_drift(tmp_path):
    path = tmp_path / "archive.zip"
    tampered_container = (
        "registry.example/nantai@sha256:" + ("f" * 64)
    )
    _build_tamper_archive(
        path,
        container_identity=tampered_container,
        members_by_path={
            **_BASE_MEMBERS_BY_PATH,
            "container-identity.txt": (
                tampered_container + "\n"
            ).encode("ascii"),
        },
    )
    with pytest.raises(RemoteResultBundleError, match="container"):
        _verify_base_archive(path)


def test_result_bundle_rejects_container_member_bytes_drift(tmp_path):
    path = tmp_path / "archive.zip"
    _build_tamper_archive(path)

    import zipfile

    members_by_path = {
        **_BASE_MEMBERS_BY_PATH,
        "container-identity.txt": (
            "registry.example/nantai@sha256:" + ("f" * 64) + "\n"
        ).encode("ascii"),
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
        request_sha256="a" * 64,
        training_bundle_sha256="d" * 64,
        container_identity="registry.example/nantai@sha256:" + ("c" * 64),
        members=members,
    )
    path.write_bytes(b"")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(members_by_path.items()):
            archive.writestr(name, payload)
    with pytest.raises(
        RemoteResultBundleError,
        match="container identity bytes mismatch",
    ):
        _verify_base_archive(path)


def test_result_bundle_rejects_job_id_drift(tmp_path):
    path = tmp_path / "archive.zip"
    _build_tamper_archive(path, job_id="job-tampered")
    with pytest.raises(RemoteResultBundleError, match="job"):
        _verify_base_archive(path)


def test_result_bundle_rejects_attempt_id_drift(tmp_path):
    path = tmp_path / "archive.zip"
    _build_tamper_archive(path, attempt_id="attempt-tampered")
    with pytest.raises(RemoteResultBundleError, match="attempt"):
        _verify_base_archive(path)


def test_result_bundle_rejects_training_bundle_sha_drift(tmp_path):
    path = tmp_path / "archive.zip"
    _build_tamper_archive(path, training_bundle_sha256="e" * 64)
    with pytest.raises(RemoteResultBundleError, match="training bundle"):
        _verify_base_archive(path)


def test_poll_rejects_status_with_drifted_request_sha(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    drifted = RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256="0" * 64,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="running",
        updated_at_utc=_T0,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(drifted))
    with pytest.raises(RemoteShellExecutionError, match="identity"):
        executor.poll(job)


def test_poll_rejects_status_with_drifted_training_bundle_sha(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    drifted = RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256="0" * 64,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="running",
        updated_at_utc=_T0,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(drifted))
    with pytest.raises(RemoteShellExecutionError, match="identity"):
        executor.poll(job)


def test_poll_rejects_status_with_drifted_runtime_policy_sha(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    drifted = RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256="0" * 64,
        state="running",
        updated_at_utc=_T0,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(drifted))

    with pytest.raises(RemoteShellExecutionError, match="identity"):
        executor.poll(job)


def test_poll_rejects_status_with_drifted_job_id(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    drifted = RemoteShellStatus(
        job_id="job-tampered",
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="running",
        updated_at_utc=_T0,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(drifted))
    with pytest.raises(RemoteShellExecutionError, match="identity"):
        executor.poll(job)


def test_poll_rejects_non_canonical_status_json(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    non_canonical = (
        canonical_remote_status_bytes(
            _running_status(job),
        )
        .replace(b",", b", ", 1)
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(
        subprocess.CompletedProcess([], 0, non_canonical, b""),
    )
    with pytest.raises(RemoteShellExecutionError, match="canonical"):
        executor.poll(job)


def test_poll_rejects_status_with_duplicate_json_keys(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    raw = canonical_remote_status_bytes(_running_status(job)).decode("ascii")
    duplicate = raw.replace(
        '"job_id":',
        '"job_id":"job-expected","job_id":',
        1,
    ).encode("ascii")
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(
        subprocess.CompletedProcess([], 0, duplicate, b""),
    )
    with pytest.raises(RemoteShellExecutionError, match="invalid"):
        executor.poll(job)


# ---------------------------------------------------------------------------
# P1-3A: submit/poll/fetch state drills
# ---------------------------------------------------------------------------


def _running_status(job: RemoteShellJobRef, *, updated_at: datetime = _T0):
    return RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="running",
        updated_at_utc=updated_at,
    )


def _failed_status(job: RemoteShellJobRef, *, updated_at: datetime = _T0):
    return RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="failed",
        updated_at_utc=updated_at,
        exit_code=1,
        stdout_sha256=_sha(b"remote-stdout"),
        stderr_sha256=_sha(b"remote-stderr"),
    )


def _succeeded_status(
    job: RemoteShellJobRef,
    *,
    updated_at: datetime = _T0,
):
    empty_sha = _sha(b"")
    return RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="succeeded",
        updated_at_utc=updated_at,
        exit_code=0,
        stdout_sha256=empty_sha,
        stderr_sha256=empty_sha,
        result_bundle_sha256="a" * 64,
        result_bundle_size_bytes=1,
    )


def _status_response(status: RemoteShellStatus):
    return subprocess.CompletedProcess(
        [],
        0,
        canonical_remote_status_bytes(status),
        b"",
    )


_DEFAULT_CONTAINER_ID = "a" * 64


def _lifecycle_receipt(
    job: RemoteShellJobRef,
    *,
    container_id: str = _DEFAULT_CONTAINER_ID,
    container_identity: str | None = None,
    request_sha256: str | None = None,
):
    workspace_sha = compute_workspace_identity_sha256(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        workspace_path=job.remote_job_path,
    )
    identity = container_identity or (
        "registry.example/nantai@sha256:" + "c" * 64
    )
    request_sha = request_sha256 or job.request_sha256
    provisional = RemoteContainerLifecycleReceipt.model_construct(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=request_sha,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        workspace_identity_sha256=workspace_sha,
        container_identity=identity,
        container_id=container_id,
        transition="container-created-identity-verified",
        receipt_sha256="0" * 64,
    )
    digest = compute_container_lifecycle_sha256(provisional)
    return RemoteContainerLifecycleReceipt(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=request_sha,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        workspace_identity_sha256=workspace_sha,
        container_identity=identity,
        container_id=container_id,
        transition="container-created-identity-verified",
        receipt_sha256=digest,
    )


def _lifecycle_response(receipt: RemoteContainerLifecycleReceipt):
    return subprocess.CompletedProcess(
        [],
        0,
        canonical_container_lifecycle_bytes(receipt),
        b"",
    )


def test_submit_keeps_receipt_not_started_until_authoritative_poll(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    context = executor._context(job)
    assert context.receipt.state == "not-started"
    assert context.receipt.observations[0].state == "not-started"
    assert len(context.receipt.observations) == 1
    assert len(runner.calls) == 4


def test_restore_attaches_existing_job_without_transport(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(
        tmp_path,
        monkeypatch,
    )
    job = executor.submit(prepared)
    resumed_runner = _Runner()
    resumed = RemoteShellExecutor(
        executor.config,
        run_command=resumed_runner,
        now=lambda: _T0,
    )

    lifecycle = _lifecycle_receipt(job)
    resumed_runner.responses.append(_lifecycle_response(lifecycle))
    restored = resumed.restore(
        prepared,
        job,
        expected_lifecycle=lifecycle,
    )

    assert restored == job
    assert len(resumed_runner.calls) == 1
    restored_context = resumed._context(job)
    assert restored_context.receipt.state == "not-started"
    assert len(restored_context.receipt.observations) == 1
    resumed_runner.responses.append(
        _lifecycle_response(lifecycle),
    )
    resumed_runner.responses.append(
        _status_response(_running_status(job)),
    )
    observation = resumed.poll(job)
    assert observation.state == "running"
    assert len(resumed_runner.calls) == 3


def test_restore_rejects_job_replay_under_different_config(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(
        tmp_path,
        monkeypatch,
    )
    job = executor.submit(prepared)
    changed_config = executor.config.model_copy(
        update={"expected_worker_version": "1.0.1"},
    )
    resumed_runner = _Runner()
    resumed = RemoteShellExecutor(
        changed_config,
        run_command=resumed_runner,
        now=lambda: _T0,
    )

    with pytest.raises(
        RemoteShellExecutionError,
        match="config identity",
    ):
        resumed.restore(
            prepared,
            job,
            expected_lifecycle=_lifecycle_receipt(job),
        )

    assert resumed_runner.calls == []


def test_remote_job_ref_loader_is_canonical_and_duplicate_safe(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(
        tmp_path,
        monkeypatch,
    )
    job = executor.submit(prepared)
    path = tmp_path / "remote-job.private.json"
    canonical = remote_module.canonical_remote_shell_job_ref_bytes(
        job,
    )
    path.write_bytes(canonical)

    assert remote_module.load_remote_shell_job_ref(path) == job

    path.write_bytes(
        canonical.replace(
            b"{",
            b'{"job_id":"duplicate",',
            1,
        )
    )
    with pytest.raises(
        RemoteShellExecutionError,
        match="duplicate|canonical",
    ):
        remote_module.load_remote_shell_job_ref(path)


def test_remote_job_ref_publication_flush_failure_leaves_no_final(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(
        tmp_path,
        monkeypatch,
    )
    job = executor.submit(prepared)
    output = tmp_path / "private" / "remote-job.json"

    def fail_flush(_path):
        raise OSError("simulated flush failure")

    monkeypatch.setattr(
        "pipeline.durable_io.flush_file",
        fail_flush,
    )
    with pytest.raises(
        RemoteShellExecutionError,
        match="cannot be published",
    ):
        remote_module.publish_remote_shell_job_ref(
            job,
            output,
        )

    assert not output.exists()
    assert not tuple(output.parent.glob(".*.staging"))


def test_remote_lifecycle_private_record_round_trip(tmp_path, monkeypatch):
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    receipt = _lifecycle_receipt(job)
    path = tmp_path / "remote-container-lifecycle.private.json"

    published = remote_module.publish_remote_container_lifecycle_receipt(
        receipt,
        path,
    )

    assert published == path
    assert path.read_bytes() == canonical_container_lifecycle_bytes(receipt)
    assert (
        remote_module.load_remote_container_lifecycle_receipt(path)
        == receipt
    )


def test_remote_lifecycle_private_record_is_no_replace(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    receipt = _lifecycle_receipt(job)
    path = tmp_path / "remote-container-lifecycle.private.json"
    path.write_bytes(b"existing\n")

    with pytest.raises(RemoteShellExecutionError, match="replace"):
        remote_module.publish_remote_container_lifecycle_receipt(
            receipt,
            path,
        )

    assert path.read_bytes() == b"existing\n"


def test_remote_lifecycle_private_record_rejects_malformed(
    tmp_path,
):
    path = tmp_path / "remote-container-lifecycle.private.json"
    path.write_bytes(b'{"schema":"bad","schema":"duplicate"}\n')

    with pytest.raises(
        RemoteShellExecutionError,
        match="invalid|duplicate",
    ):
        remote_module.load_remote_container_lifecycle_receipt(path)


def test_remote_lifecycle_private_record_rejects_symlink(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    target = tmp_path / "target.json"
    target.write_bytes(
        canonical_container_lifecycle_bytes(_lifecycle_receipt(job))
    )
    path = tmp_path / "remote-container-lifecycle.private.json"
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(RemoteShellExecutionError, match="regular"):
        remote_module.load_remote_container_lifecycle_receipt(path)


def test_remote_lifecycle_loader_rejects_fd_identity_swap(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    original_receipt = _lifecycle_receipt(job)
    swapped_receipt = _lifecycle_receipt(job, container_id="b" * 64)
    path = tmp_path / "remote-container-lifecycle.private.json"
    alternate = tmp_path / "alternate-valid-lifecycle.json"
    path.write_bytes(canonical_container_lifecycle_bytes(original_receipt))
    alternate.write_bytes(canonical_container_lifecycle_bytes(swapped_receipt))
    real_open = os.open

    def redirect_open(candidate, flags, mode=0o777):
        selected = alternate if Path(candidate) == path else candidate
        return real_open(selected, flags, mode)

    monkeypatch.setattr(remote_module.os, "open", redirect_open)

    with pytest.raises(
        RemoteShellExecutionError,
        match="changed while read|cannot be read",
    ):
        remote_module.load_remote_container_lifecycle_receipt(path)


def test_poll_running_returns_running_observation(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    runner.responses.append(
        _lifecycle_response(_lifecycle_receipt(job)),
    )
    runner.responses.append(
        _status_response(_running_status(job)),
    )
    observation = executor.poll(job)

    assert observation.state == "running"
    assert observation.exit_code is None
    assert observation.result_bundle_sha256 is None


def test_poll_nonzero_exit_returns_unknown(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    runner.responses.append(
        subprocess.CompletedProcess([], 255, b"", b"network unreachable"),
    )
    observation = executor.poll(job)

    assert observation.state == "unknown"
    assert observation.exit_code is None
    assert observation.result_bundle_sha256 is None


def test_poll_timeout_returns_unknown(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    def timeout(argv, **kwargs):
        del argv, kwargs
        raise subprocess.TimeoutExpired(["ssh"], timeout=1)

    executor._run_command = timeout
    observation = executor.poll(job)

    assert observation.state == "unknown"
    assert observation.exit_code is None
    assert observation.result_bundle_sha256 is None


def test_poll_disconnect_after_running_returns_unknown(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    runner.responses.append(
        _lifecycle_response(_lifecycle_receipt(job)),
    )
    runner.responses.append(
        _status_response(_running_status(job)),
    )
    first = executor.poll(job)
    assert first.state == "running"

    runner.responses.append(
        _lifecycle_response(_lifecycle_receipt(job)),
    )
    runner.responses.append(
        subprocess.CompletedProcess([], 255, b"", b"connection lost"),
    )
    second = executor.poll(job)
    assert second.state == "unknown"

    from pipeline.training_executor import _ALLOWED_TRANSITIONS

    assert "unknown" in _ALLOWED_TRANSITIONS["running"]


def test_poll_remote_failed_returns_failed_observation(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    failed = _failed_status(job)
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(failed))
    observation = executor.poll(job)

    assert observation.state == "failed"
    assert observation.exit_code == 1
    assert observation.stdout_sha256 == _sha(b"remote-stdout")
    assert observation.stderr_sha256 == _sha(b"remote-stderr")
    assert observation.result_bundle_sha256 is None


def test_poll_succeeded_returns_unknown_until_verified(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    empty_sha = _sha(b"")
    succeeded = RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="succeeded",
        updated_at_utc=_T0,
        exit_code=0,
        stdout_sha256=empty_sha,
        stderr_sha256=empty_sha,
        result_bundle_sha256=("a" * 64),
        result_bundle_size_bytes=1,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(succeeded))
    observation = executor.poll(job)

    assert observation.state == "unknown"
    assert observation.exit_code == 0
    assert observation.stdout_sha256 == empty_sha
    assert observation.stderr_sha256 == empty_sha


def test_fetch_without_succeeded_status_raises(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    runner.responses.append(
        _lifecycle_response(_lifecycle_receipt(job)),
    )
    runner.responses.append(
        _status_response(_running_status(job)),
    )
    executor.poll(job)

    destination = tmp_path / "result"
    with pytest.raises(RemoteShellExecutionError, match="succeeded"):
        executor.fetch(job, destination)

    assert not destination.exists()


def test_fetch_interrupted_cleans_staging(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    empty_sha = _sha(b"")
    succeeded = RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="succeeded",
        updated_at_utc=_T0,
        exit_code=0,
        stdout_sha256=empty_sha,
        stderr_sha256=empty_sha,
        result_bundle_sha256=("a" * 64),
        result_bundle_size_bytes=1,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(succeeded))
    executor.poll(job)

    runner.responses.append(
        _lifecycle_response(_lifecycle_receipt(job)),
    )
    runner.responses.append(
        subprocess.CompletedProcess([], 1, b"", b"download interrupted"),
    )
    destination = tmp_path / "published-result"
    with pytest.raises(RemoteShellExecutionError, match="download"):
        executor.fetch(job, destination)

    assert not destination.exists()
    staging = list(destination.parent.glob(".published-result.*.staging"))
    assert staging == []


# ---------------------------------------------------------------------------
# P1-2: Credential-free remote-shell preflight
# ---------------------------------------------------------------------------


class TestRemoteShellPreflight:
    def test_strict_remote_config_loader_rejects_duplicate_keys(
        self,
        tmp_path,
    ):
        config = _config(tmp_path)
        path = tmp_path / "remote-config.json"
        canonical = remote_module._canonical_model_bytes(config)
        path.write_bytes(canonical)

        assert (
            remote_module.load_remote_shell_executor_config(path)
            == config
        )

        duplicate = canonical.replace(
            b"{",
            b'{"port":2222,',
            1,
        )
        path.write_bytes(duplicate)
        with pytest.raises(
            RemoteShellExecutionError,
            match="duplicate|canonical",
        ):
            remote_module.load_remote_shell_executor_config(path)

    def test_preflight_publication_flush_failure_leaves_no_final(
        self,
        tmp_path,
        monkeypatch,
    ):
        report = run_remote_shell_preflight(
            _config(tmp_path),
            probe_remote=False,
            now=lambda: _T0,
        )
        output = tmp_path / "private" / "preflight.json"

        def fail_flush(_path):
            raise OSError("simulated flush failure")

        monkeypatch.setattr(
            "pipeline.durable_io.flush_file",
            fail_flush,
        )

        with pytest.raises(
            RemoteShellExecutionError,
            match="cannot be published",
        ):
            remote_module.publish_remote_shell_preflight(
                report,
                output,
            )

        assert not output.exists()
        assert not tuple(output.parent.glob(".*.staging"))

    def test_preflight_publication_is_immutable(self, tmp_path):
        report = run_remote_shell_preflight(
            _config(tmp_path),
            probe_remote=False,
            now=lambda: _T0,
        )
        output = tmp_path / "private" / "preflight.json"

        published = remote_module.publish_remote_shell_preflight(
            report,
            output,
        )

        assert published == output
        assert output.read_bytes() == (
            canonical_remote_shell_preflight_bytes(report)
        )
        with pytest.raises(
            RemoteShellExecutionError,
            match="cannot replace",
        ):
            remote_module.publish_remote_shell_preflight(
                report,
                output,
            )

    def test_preflight_from_path_rejects_config_replacement(
        self,
        tmp_path,
    ):
        config = _config(tmp_path)
        config_path = tmp_path / "remote-config.json"
        payload = remote_module._canonical_model_bytes(config)
        config_path.write_bytes(payload)
        replaced = False

        def replace_config(argv, **kwargs):
            nonlocal replaced
            del argv, kwargs
            if not replaced:
                replacement = tmp_path / "replacement.json"
                replacement.write_bytes(payload)
                os.replace(replacement, config_path)
                replaced = True
            return _readiness_response(config)

        report = remote_module.run_remote_shell_preflight_from_path(
            config_path,
            run_command=replace_config,
            now=lambda: _T0,
        )

        assert report.status == "failed"
        assert report.failure_code == "local-transport-drift"

    def test_remote_probe_is_required_for_ready(self, tmp_path):
        config = _config(tmp_path)
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        assert report.status == "failed"
        assert report.ssh_binary_found
        assert report.scp_binary_found
        assert report.private_key_protection_verified
        assert report.known_hosts_verified
        assert report.container_runtime_verified is None
        assert report.worker_binary_verified is None
        assert report.failure_reason == "remote probe is required for readiness"

    def test_canonical_bytes_round_trip(self, tmp_path):
        config = _config(tmp_path)
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        canonical = canonical_remote_shell_preflight_bytes(report)
        revalidated = RemoteShellPreflightReport.model_validate_json(
            canonical,
        )
        assert canonical == canonical_remote_shell_preflight_bytes(
            revalidated,
        )

    def test_report_identity_binds_config_and_local_input_bytes(
        self,
        tmp_path,
    ):
        config = _config(tmp_path)
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        payload = report.model_dump(by_alias=True)

        assert payload["config_identity_sha256"] == _sha(
            remote_module._canonical_model_bytes(config),
        )
        for field in (
            "ssh_binary_sha256",
            "scp_binary_sha256",
            "private_key_sha256",
            "known_hosts_sha256",
        ):
            assert payload[field] is not None
            assert len(payload[field]) == 64
        assert payload["report_id"] == (
            "remote-preflight-" + payload["content_sha256"]
        )

        payload["known_host"] = "replayed.example"
        with pytest.raises(ValidationError, match="content|report"):
            RemoteShellPreflightReport.model_validate(payload)

    def test_report_binds_target_identity(self, tmp_path):
        config = _config(tmp_path)
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        assert report.container_identity == config.container_identity
        assert (
            report.expected_host_key_fingerprint
            == config.expected_host_key_fingerprint
        )
        assert report.known_host == config.known_host
        assert report.port == config.port
        assert report.remote_root == config.remote_root
        assert report.remote_repo_root == config.remote_repo_root
        assert report.container_runtime == config.container_runtime

    def test_ssh_binary_missing_returns_blocked(self, tmp_path):
        config = _config(tmp_path)
        config.ssh_binary.unlink()
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        assert report.status == "blocked-external-input"
        assert not report.ssh_binary_found
        assert "ssh binary is missing" in report.failure_reason
        assert (
            report.model_dump().get("failure_code")
            == "ssh-binary-missing"
        )

    def test_failed_local_check_is_not_downgraded_by_missing_input(
        self,
        tmp_path,
    ):
        config = _config(tmp_path)
        config.ssh_binary.unlink()
        bad = config.model_copy(
            update={
                "expected_host_key_fingerprint": "SHA256:" + ("A" * 43),
            },
        )

        report = run_remote_shell_preflight(
            bad,
            probe_remote=False,
            now=lambda: _T0,
        )

        assert report.status == "failed"
        assert (
            report.model_dump().get("failure_code")
            == "known-hosts-invalid"
        )

    def test_scp_binary_missing_returns_blocked(self, tmp_path):
        config = _config(tmp_path)
        config.scp_binary.unlink()
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        assert report.status == "blocked-external-input"
        assert not report.scp_binary_found
        assert "scp binary is missing" in report.failure_reason

    def test_private_key_missing_returns_blocked(self, tmp_path):
        config = _config(tmp_path)
        config.private_key_path.unlink()
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        assert report.status == "blocked-external-input"
        assert not report.private_key_protection_verified
        assert "SSH private key is missing" in report.failure_reason

    def test_known_hosts_missing_returns_blocked(self, tmp_path):
        config = _config(tmp_path)
        config.known_hosts_path.unlink()
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        assert report.status == "blocked-external-input"
        assert not report.known_hosts_verified
        assert "known-hosts file is missing" in report.failure_reason

    def test_runtime_policy_missing_blocks_before_remote_probe(
        self,
        tmp_path,
    ):
        config = _config(tmp_path)
        config.runtime_policy_path.unlink()
        runner = _Runner()

        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=runner,
            now=lambda: _T0,
        )

        assert report.status == "blocked-external-input"
        assert report.failure_code == "runtime-policy-missing"
        assert report.failure_reason == (
            "production runtime policy is missing"
        )
        assert runner.calls == []

    def test_invalid_runtime_policy_fails_before_remote_probe(
        self,
        tmp_path,
    ):
        config = _config(tmp_path)
        config.runtime_policy_path.write_bytes(b"{}\n")
        runner = _Runner()

        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=runner,
            now=lambda: _T0,
        )

        assert report.status == "failed"
        assert report.failure_code == "runtime-policy-invalid"
        assert report.failure_reason == (
            "production runtime policy is invalid or does not match config"
        )
        assert runner.calls == []

    def test_runtime_policy_binding_mismatch_fails_before_remote_probe(
        self,
        tmp_path,
    ):
        config = _config(tmp_path).model_copy(
            update={"expected_runtime_policy_sha256": "0" * 64},
        )
        runner = _Runner()

        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=runner,
            now=lambda: _T0,
        )

        assert report.status == "failed"
        assert report.failure_code == "runtime-policy-invalid"
        assert runner.calls == []

    def test_fingerprint_mismatch_returns_failed(self, tmp_path):
        config = _config(tmp_path)
        bad = config.model_copy(
            update={
                "expected_host_key_fingerprint": "SHA256:" + ("A" * 43),
            },
        )
        report = run_remote_shell_preflight(
            bad,
            probe_remote=False,
            now=lambda: _T0,
        )
        assert report.status == "failed"
        assert not report.known_hosts_verified
        assert "fingerprint" in report.failure_reason

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode policy")
    def test_private_key_wrong_permissions_returns_failed(self, tmp_path):
        config = _config(tmp_path)
        config.private_key_path.chmod(0o640)
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        assert report.status == "failed"
        assert not report.private_key_protection_verified
        assert "too broad" in report.failure_reason

    def test_probe_remote_success_returns_ready(self, tmp_path):
        config = _config(tmp_path)
        runner = _Runner()
        runner.responses.append(_readiness_response(config))
        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=runner,
            now=lambda: _T0,
        )
        assert report.status == "ready"
        assert report.container_runtime_verified is True
        assert report.container_image_verified is True
        assert report.worker_binary_verified is True
        assert report.checker_config_sha256 == "e" * 64
        assert report.measured_container_identity == config.container_identity
        assert report.worker_binary_sha256 == "d" * 64
        assert report.worker_version == "1.0.0"
        assert report.failure_reason is None
        assert len(runner.calls) == 1
        assert runner.calls[0][0][-1] == (
            "nantai-remote-readiness-checker"
        )

    def test_remote_probe_rejects_local_input_drift(self, tmp_path):
        config = _config(tmp_path)

        def mutate_known_hosts(argv, **kwargs):
            del argv, kwargs
            config.known_hosts_path.write_bytes(
                config.known_hosts_path.read_bytes() + b"# drift\n"
            )
            return _readiness_response(config)

        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=mutate_known_hosts,
            now=lambda: _T0,
        )

        assert report.status == "failed"
        assert report.failure_code == "local-transport-drift"

    def test_remote_probe_rejects_runtime_policy_drift(self, tmp_path):
        config = _config(tmp_path)

        def mutate_runtime_policy(argv, **kwargs):
            del argv, kwargs
            config.runtime_policy_path.write_bytes(b"{}\n")
            return _readiness_response(config)

        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=mutate_runtime_policy,
            now=lambda: _T0,
        )

        assert report.status == "failed"
        assert report.failure_code == "local-transport-drift"

    def test_probe_remote_unreachable_returns_blocked(self, tmp_path):
        config = _config(tmp_path)

        def fail_transport(*argv, **kwargs):
            del argv, kwargs
            raise subprocess.TimeoutExpired(["ssh"], timeout=1)

        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=fail_transport,
            now=lambda: _T0,
        )
        assert report.status == "blocked-external-input"
        assert report.container_runtime_verified is None
        assert report.worker_binary_verified is None
        assert "could not reach the target" in report.failure_reason

    def test_probe_remote_bad_return_code_returns_failed(self, tmp_path):
        config = _config(tmp_path)
        runner = _Runner()
        runner.responses.append(
            subprocess.CompletedProcess([], 1, b"", b"not found"),
        )
        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=runner,
            now=lambda: _T0,
        )
        assert report.status == "failed"
        assert report.container_runtime_verified is False
        assert report.container_image_verified is False
        assert report.worker_binary_verified is False
        assert report.failure_code == "remote-checker-invalid"
        assert report.failure_reason == (
            "remote readiness checker response is invalid"
        )
        assert len(runner.calls) == 1

    def test_probe_remote_rejects_checker_control_characters(
        self,
        tmp_path,
    ):
        config = _config(tmp_path)
        runner = _Runner()
        runner.responses.append(
            _readiness_response(
                config,
                container_runtime_version="Docker\ninjected",
            )
        )

        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=runner,
            now=lambda: _T0,
        )

        assert report.status == "failed"
        assert report.failure_code == "remote-checker-invalid"
        assert report.container_runtime_version is None

    def test_probe_remote_skipped_when_local_checks_fail(self, tmp_path):
        config = _config(tmp_path)
        config.ssh_binary.unlink()
        runner = _Runner()
        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=runner,
            now=lambda: _T0,
        )
        assert report.status == "blocked-external-input"
        assert report.container_runtime_verified is None
        assert report.worker_binary_verified is None
        assert len(runner.calls) == 0

    def test_report_does_not_leak_private_key_path_ready(self, tmp_path):
        config = _config(tmp_path)
        private_key_str = str(config.private_key_path)
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        report_json = canonical_remote_shell_preflight_bytes(
            report,
        ).decode("ascii")
        assert private_key_str not in report_json

    def test_report_does_not_leak_private_key_path_blocked(self, tmp_path):
        config = _config(tmp_path)
        private_key_str = str(config.private_key_path)
        config.private_key_path.unlink()
        report = run_remote_shell_preflight(
            config,
            probe_remote=False,
            now=lambda: _T0,
        )
        report_json = canonical_remote_shell_preflight_bytes(
            report,
        ).decode("ascii")
        assert private_key_str not in report_json
        assert "SSH private key is missing" in report.failure_reason

    def test_report_does_not_leak_private_key_path_with_remote_probe(
        self,
        tmp_path,
    ):
        config = _config(tmp_path)
        private_key_str = str(config.private_key_path)
        runner = _Runner()
        command_audit: list[tuple[str, ...]] = []
        report = run_remote_shell_preflight(
            config,
            probe_remote=True,
            run_command=runner,
            now=lambda: _T0,
            command_audit=command_audit,
        )
        report_json = canonical_remote_shell_preflight_bytes(
            report,
        ).decode("ascii")
        assert private_key_str not in report_json
        assert command_audit, "remote probe must record redacted argv"
        audit_text = "\n".join(" ".join(argv) for argv in command_audit)
        assert private_key_str not in audit_text
        assert str(config.known_hosts_path) not in audit_text
        assert str(config.ssh_binary) not in audit_text
        assert str(config.scp_binary) not in audit_text
        assert config.ssh_target not in audit_text
        assert config.known_host not in audit_text
        assert "<redacted-private-key>" in audit_text
        assert "<redacted-known-hosts>" in audit_text
        assert "<redacted-ssh-target>" in audit_text

    def test_report_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            RemoteShellPreflightReport.model_validate(
                {
                    "schema": "nantai.remote-shell-preflight.v1",
                    "status": "ready",
                    "checked_at_utc": _T0.isoformat(),
                    "container_identity": (
                        "registry.example/nantai@sha256:" + ("c" * 64)
                    ),
                    "expected_host_key_fingerprint": _fingerprint(b"key"),
                    "known_host": "remote.example",
                    "port": 2222,
                    "remote_root": "/srv/nantai-jobs",
                    "remote_repo_root": "/srv/nantai-3d",
                    "container_runtime": "docker",
                    "ssh_binary_found": True,
                    "scp_binary_found": True,
                    "private_key_protection_verified": True,
                    "known_hosts_verified": True,
                    "extra_secret_field": "should be rejected",
                }
            )

    def test_ready_report_rejects_failure_reason(self):
        with pytest.raises(ValidationError):
            RemoteShellPreflightReport(
                status="ready",
                checked_at_utc=_T0,
                container_identity=(
                    "registry.example/nantai@sha256:" + ("c" * 64)
                ),
                expected_host_key_fingerprint=_fingerprint(b"key"),
                known_host="remote.example",
                port=2222,
                remote_root="/srv/nantai-jobs",
                remote_repo_root="/srv/nantai-3d",
                container_runtime="docker",
                ssh_binary_found=True,
                scp_binary_found=True,
                private_key_protection_verified=True,
                known_hosts_verified=True,
                failure_reason="should not be here",
            )

    def test_blocked_report_requires_failure_reason(self):
        with pytest.raises(ValidationError):
            RemoteShellPreflightReport(
                status="blocked-external-input",
                checked_at_utc=_T0,
                container_identity=(
                    "registry.example/nantai@sha256:" + ("c" * 64)
                ),
                expected_host_key_fingerprint=_fingerprint(b"key"),
                known_host="remote.example",
                port=2222,
                remote_root="/srv/nantai-jobs",
                remote_repo_root="/srv/nantai-3d",
                container_runtime="docker",
                ssh_binary_found=False,
                scp_binary_found=True,
                private_key_protection_verified=True,
                known_hosts_verified=True,
                failure_reason=None,
            )

    def test_ready_report_requires_all_local_checks_passed(self):
        with pytest.raises(ValidationError):
            RemoteShellPreflightReport(
                status="ready",
                checked_at_utc=_T0,
                container_identity=(
                    "registry.example/nantai@sha256:" + ("c" * 64)
                ),
                expected_host_key_fingerprint=_fingerprint(b"key"),
                known_host="remote.example",
                port=2222,
                remote_root="/srv/nantai-jobs",
                remote_repo_root="/srv/nantai-3d",
                container_runtime="docker",
                ssh_binary_found=True,
                scp_binary_found=False,
                private_key_protection_verified=True,
                known_hosts_verified=True,
                failure_reason=None,
            )


# ---------------------------------------------------------------------------
# E1: fresh-container lifecycle receipt caller binding
# ---------------------------------------------------------------------------


def test_poll_does_not_report_running_before_lifecycle_is_bound(
    tmp_path,
    monkeypatch,
):
    """Poll must return unknown when lifecycle receipt cannot be reached."""
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    runner.responses.append(
        subprocess.CompletedProcess([], 255, b"", b"lifecycle unreachable"),
    )
    runner.responses.append(_status_response(_running_status(job)))
    observation = executor.poll(job)

    assert observation.state == "unknown"
    assert observation.exit_code is None
    context = executor._context(job)
    assert context.bound_lifecycle_receipt is None
    assert context.receipt.state == "unknown"
    assert tuple(
        item.state for item in context.receipt.observations
    ) == ("not-started", "unknown")
    assert len(runner.responses) == 1


def test_poll_binds_lifecycle_on_first_running_observation(tmp_path, monkeypatch):
    """First running poll must fetch and bind the lifecycle container_id."""
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    receipt = _lifecycle_receipt(job)
    runner.responses.append(_lifecycle_response(receipt))
    runner.responses.append(_status_response(_running_status(job)))
    observation = executor.poll(job)

    assert observation.state == "running"
    context = executor._context(job)
    assert context.bound_lifecycle_receipt == receipt


def test_poll_revalidates_same_container_on_subsequent_poll(
    tmp_path,
    monkeypatch,
):
    """Subsequent running polls must re-fetch and confirm same container_id."""
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(_running_status(job)))
    first = executor.poll(job)
    assert first.state == "running"

    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(_running_status(job)))
    second = executor.poll(job)
    assert second.state == "running"


def test_poll_rejects_lifecycle_with_wrong_attempt(tmp_path, monkeypatch):
    """Lifecycle receipt with mismatched attempt_id must be rejected."""
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    base = _lifecycle_receipt(job)
    wrong_attempt = RemoteContainerLifecycleReceipt(
        job_id=base.job_id,
        attempt_id="attempt-other",
        request_sha256=base.request_sha256,
        training_bundle_sha256=base.training_bundle_sha256,
        runtime_policy_sha256=base.runtime_policy_sha256,
        workspace_identity_sha256=base.workspace_identity_sha256,
        container_identity=base.container_identity,
        container_id=base.container_id,
        transition="container-created-identity-verified",
        receipt_sha256=compute_container_lifecycle_sha256(
            base.model_copy(update={"attempt_id": "attempt-other"})
        ),
    )
    runner.responses.append(_lifecycle_response(wrong_attempt))
    runner.responses.append(_status_response(_running_status(job)))
    with pytest.raises(
        RemoteShellExecutionError,
        match="lifecycle identity differs",
    ):
        executor.poll(job)


def test_poll_rejects_lifecycle_container_swap(tmp_path, monkeypatch):
    """After binding, a container_id change must be rejected."""
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(_running_status(job)))
    executor.poll(job)

    swapped = _lifecycle_receipt(job, container_id="b" * 64)
    runner.responses.append(_lifecycle_response(swapped))
    runner.responses.append(_status_response(_running_status(job)))
    with pytest.raises(
        RemoteShellExecutionError,
        match="container swap",
    ):
        executor.poll(job)


def test_restore_requires_durable_lifecycle_receipt(tmp_path, monkeypatch):
    """Restore must fail when lifecycle receipt is unreachable."""
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    resumed_runner = _Runner()
    resumed = RemoteShellExecutor(
        executor.config,
        run_command=resumed_runner,
        now=lambda: _T0,
    )

    resumed_runner.responses.append(
        subprocess.CompletedProcess([], 255, b"", b"unreachable"),
    )
    with pytest.raises(
        RemoteShellExecutionError,
        match="durable lifecycle receipt",
    ):
        resumed.restore(
            prepared,
            job,
            expected_lifecycle=_lifecycle_receipt(job),
        )


def test_restore_rejects_lifecycle_identity_mismatch(
    tmp_path,
    monkeypatch,
):
    """Restore must reject lifecycle with wrong container identity."""
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    resumed_runner = _Runner()
    resumed = RemoteShellExecutor(
        executor.config,
        run_command=resumed_runner,
        now=lambda: _T0,
    )

    wrong_identity = _lifecycle_receipt(
        job,
        container_identity="registry.example/other@sha256:" + "d" * 64,
    )
    resumed_runner.responses.append(
        _lifecycle_response(wrong_identity),
    )
    with pytest.raises(
        RemoteShellExecutionError,
        match="lifecycle identity differs",
    ):
        resumed.restore(
            prepared,
            job,
            expected_lifecycle=wrong_identity,
        )


def test_restore_binds_lifecycle_container_id(tmp_path, monkeypatch):
    """Restore must bind the lifecycle container_id after verification."""
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    resumed_runner = _Runner()
    resumed = RemoteShellExecutor(
        executor.config,
        run_command=resumed_runner,
        now=lambda: _T0,
    )

    receipt = _lifecycle_receipt(job)
    resumed_runner.responses.append(_lifecycle_response(receipt))
    resumed.restore(
        prepared,
        job,
        expected_lifecycle=receipt,
    )

    key = (job.job_id, job.attempt_id)
    assert resumed._jobs[key].bound_lifecycle_receipt == receipt


def test_fetch_rejects_lifecycle_container_swap(tmp_path, monkeypatch):
    """Fetch must reject when lifecycle container_id changes after binding."""
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)

    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(_running_status(job)))
    executor.poll(job)

    empty_sha = _sha(b"")
    succeeded = RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="succeeded",
        updated_at_utc=_T0,
        exit_code=0,
        stdout_sha256=empty_sha,
        stderr_sha256=empty_sha,
        result_bundle_sha256=("a" * 64),
        result_bundle_size_bytes=1,
    )
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    runner.responses.append(_status_response(succeeded))
    executor.poll(job)

    swapped = _lifecycle_receipt(job, container_id="b" * 64)
    runner.responses.append(_lifecycle_response(swapped))
    destination = tmp_path / "result"
    with pytest.raises(
        RemoteShellExecutionError,
        match="container swap",
    ):
        executor.fetch(job, destination)


def test_lifecycle_receipt_is_content_addressed_and_round_trips():
    """Receipt SHA must be content-addressed and survive canonical round-trip."""
    job = RemoteShellJobRef(
        job_id="job-1",
        attempt_id="attempt-1",
        submitted_at_utc=_T0,
        request_sha256="a" * 64,
        training_bundle_sha256="b" * 64,
        runtime_policy_sha256="c" * 64,
        config_identity_sha256="e" * 64,
        remote_job_path="/srv/nantai-jobs/job-1/attempt-1",
    )
    receipt = _lifecycle_receipt(job)
    raw = canonical_container_lifecycle_bytes(receipt)
    loaded = load_container_lifecycle_receipt(raw)
    assert loaded == receipt
    assert loaded.receipt_sha256 == compute_container_lifecycle_sha256(
        receipt
    )


# ---------------------------------------------------------------------------
# E1b: authoritative caller lifecycle closure
# ---------------------------------------------------------------------------


def test_poll_fetches_lifecycle_before_status(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    lifecycle = _lifecycle_receipt(job)
    runner.responses.extend(
        [
            _lifecycle_response(lifecycle),
            _status_response(_running_status(job, updated_at=_T1)),
        ]
    )

    observation = executor.poll(job)

    assert observation.state == "running"
    commands = [argv[-1] for argv, _kwargs in runner.calls[-2:]]
    assert " lifecycle " in f" {commands[0]} "
    assert " status " in f" {commands[1]} "
    assert executor.bound_lifecycle_receipt(job) == lifecycle
    context = executor._context(job)
    assert context.receipt.state == "running"
    assert context.receipt.last_observed_at_utc == _T1
    assert tuple(
        item.state for item in context.receipt.observations
    ) == ("not-started", "running")


def test_poll_status_transport_unknown_advances_authoritative_history(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    lifecycle = _lifecycle_receipt(job)
    runner.responses.extend(
        [
            _lifecycle_response(lifecycle),
            subprocess.CompletedProcess([], 255, b"", b"status unreachable"),
        ]
    )

    observation = executor.poll(job)

    assert observation.state == "unknown"
    context = executor._context(job)
    assert context.bound_lifecycle_receipt == lifecycle
    assert context.last_status is None
    assert tuple(
        item.state for item in context.receipt.observations
    ) == ("not-started", "unknown")


@pytest.mark.parametrize(
    ("terminal_state", "expected_state"),
    [("failed", "failed"), ("succeeded", "unknown")],
)
def test_poll_terminal_first_requires_lifecycle(
    tmp_path,
    monkeypatch,
    terminal_state,
    expected_state,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    lifecycle = _lifecycle_receipt(job)
    status = (
        _failed_status(job)
        if terminal_state == "failed"
        else _succeeded_status(job)
    )
    runner.responses.extend(
        [_lifecycle_response(lifecycle), _status_response(status)]
    )

    observation = executor.poll(job)

    assert observation.state == expected_state
    assert executor.bound_lifecycle_receipt(job) == lifecycle
    context = executor._context(job)
    expected_history = (
        ("not-started", "failed")
        if terminal_state == "failed"
        else ("not-started", "unknown")
    )
    assert tuple(
        item.state for item in context.receipt.observations
    ) == expected_history
    assert "running" not in expected_history
    commands = [argv[-1] for argv, _kwargs in runner.calls[-2:]]
    assert " lifecycle " in f" {commands[0]} "
    assert " status " in f" {commands[1]} "


@pytest.mark.parametrize(
    ("next_state", "expected_state"),
    [
        ("running", "running"),
        ("failed", "failed"),
        ("succeeded", "unknown"),
    ],
)
def test_poll_revalidates_full_lifecycle_on_every_state(
    tmp_path,
    monkeypatch,
    next_state,
    expected_state,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    lifecycle = _lifecycle_receipt(job)
    runner.responses.extend(
        [
            _lifecycle_response(lifecycle),
            _status_response(_running_status(job)),
        ]
    )
    assert executor.poll(job).state == "running"

    status = {
        "running": _running_status(job),
        "failed": _failed_status(job),
        "succeeded": _succeeded_status(job),
    }[next_state]
    runner.responses.extend(
        [_lifecycle_response(lifecycle), _status_response(status)]
    )

    assert executor.poll(job).state == expected_state
    commands = [argv[-1] for argv, _kwargs in runner.calls]
    lifecycle_calls = [
        command for command in commands if " lifecycle " in f" {command} "
    ]
    status_calls = [
        command for command in commands if " status " in f" {command} "
    ]
    assert len(lifecycle_calls) == 2
    assert len(status_calls) == 2


def test_poll_rejects_same_container_changed_receipt(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    lifecycle = _lifecycle_receipt(job)
    runner.responses.extend(
        [
            _lifecycle_response(lifecycle),
            _status_response(_running_status(job)),
        ]
    )
    assert executor.poll(job).state == "running"

    changed = _lifecycle_receipt(job, request_sha256="f" * 64)
    assert changed.container_id == lifecycle.container_id
    assert changed != lifecycle
    runner.responses.extend(
        [
            _lifecycle_response(changed),
            _status_response(_running_status(job)),
        ]
    )

    with pytest.raises(RemoteShellExecutionError, match="receipt changed"):
        executor.poll(job)
    assert len(runner.responses) == 1


def test_fetch_requires_prebound_lifecycle(tmp_path, monkeypatch):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    context = executor._context(job)
    context.last_status = _succeeded_status(job)
    runner.responses.append(_lifecycle_response(_lifecycle_receipt(job)))
    calls_before_fetch = len(runner.calls)

    with pytest.raises(RemoteShellExecutionError, match="prebound lifecycle"):
        executor.fetch(job, tmp_path / "result")

    assert len(runner.calls) == calls_before_fetch
    with pytest.raises(RemoteShellExecutionError, match="not bound"):
        executor.bound_lifecycle_receipt(job)


def test_fetch_rejects_same_container_changed_receipt(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    lifecycle = _lifecycle_receipt(job)
    runner.responses.extend(
        [
            _lifecycle_response(lifecycle),
            _status_response(_running_status(job)),
        ]
    )
    assert executor.poll(job).state == "running"
    executor._context(job).last_status = _succeeded_status(job)

    changed = _lifecycle_receipt(job, request_sha256="f" * 64)
    assert changed.container_id == lifecycle.container_id
    runner.responses.append(_lifecycle_response(changed))

    with pytest.raises(RemoteShellExecutionError, match="receipt changed"):
        executor.fetch(job, tmp_path / "result")
    commands = [argv[-1] for argv, _kwargs in runner.calls]
    assert not any(
        str(executor.config.scp_binary) == argv[0]
        for argv, _kwargs in runner.calls[-1:]
    )
    assert " lifecycle " in f" {commands[-1]} "


def test_restore_requires_expected_lifecycle_receipt(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    resumed_runner = _Runner()
    resumed = RemoteShellExecutor(
        executor.config,
        run_command=resumed_runner,
        now=lambda: _T0,
    )

    with pytest.raises(TypeError, match="expected_lifecycle"):
        resumed.restore(prepared, job)


def test_restore_rejects_changed_valid_receipt_without_submission_side_effects(
    tmp_path,
    monkeypatch,
):
    executor, _runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    expected = _lifecycle_receipt(job)
    changed = _lifecycle_receipt(job, request_sha256="f" * 64)
    assert changed.container_id == expected.container_id
    resumed_runner = _Runner()
    resumed_runner.responses.append(_lifecycle_response(changed))
    resumed = RemoteShellExecutor(
        executor.config,
        run_command=resumed_runner,
        now=lambda: _T0,
    )

    with pytest.raises(RemoteShellExecutionError, match="receipt changed"):
        resumed.restore(
            prepared,
            job,
            expected_lifecycle=expected,
        )

    assert len(resumed_runner.calls) == 1
    argv, _kwargs = resumed_runner.calls[0]
    assert str(executor.config.ssh_binary) == argv[0]
    command = argv[-1]
    assert " lifecycle " in f" {command} "
    for forbidden in (" init ", " start ", " create ", " status "):
        assert forbidden not in f" {command} "


def test_poll_malformed_lifecycle_fails_closed_without_status_or_binding(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    malformed = subprocess.CompletedProcess(
        [],
        0,
        b'{ "schema": "nantai.remote-container-lifecycle.v1" }\n',
        b"",
    )
    runner.responses.extend(
        [malformed, _status_response(_running_status(job))]
    )

    with pytest.raises(
        RemoteShellExecutionError,
        match="lifecycle receipt validation",
    ):
        executor.poll(job)

    context = executor._context(job)
    assert context.bound_lifecycle_receipt is None
    assert context.last_status is None
    assert context.receipt.state == "not-started"
    assert len(context.receipt.observations) == 1
    assert len(runner.responses) == 1


def test_poll_rejected_observation_does_not_publish_status_or_enable_fetch(
    tmp_path,
    monkeypatch,
):
    executor, runner, prepared = _prepared_executor(tmp_path, monkeypatch)
    job = executor.submit(prepared)
    lifecycle = _lifecycle_receipt(job)
    running = _running_status(job, updated_at=_T1)
    runner.responses.extend(
        [_lifecycle_response(lifecycle), _status_response(running)]
    )
    assert executor.poll(job).state == "running"
    context = executor._context(job)
    receipt_before_rejection = context.receipt
    assert context.last_status == running

    regressed = _succeeded_status(job, updated_at=_T0)
    runner.responses.extend(
        [_lifecycle_response(lifecycle), _status_response(regressed)]
    )
    with pytest.raises(TrainingExecutorError, match="precedes"):
        executor.poll(job)

    assert context.last_status == running
    assert context.receipt == receipt_before_rejection
    calls_before_fetch = len(runner.calls)
    with pytest.raises(RemoteShellExecutionError, match="succeeded"):
        executor.fetch(job, tmp_path / "result")
    assert len(runner.calls) == calls_before_fetch
