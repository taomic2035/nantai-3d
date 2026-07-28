"""K1: production v2 fetch end-to-end fake transport matrix.

Six RED tests proving the v2 fetch flow binds the archive's measurement to
the executor's job identity, the lifecycle receipt's workspace/container
identity, and the config's remote target/container identity — and that
collision, sync-unknown, and identity-swap paths never advance the receipt
to succeeded or overwrite the destination.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.remote_shell_executor as remote_module
from pipeline.production_runtime_evidence import (
    ProductionRuntimeMeasurement,
    canonical_production_runtime_decision_bytes,
    canonical_production_runtime_measurement_bytes,
    canonical_production_runtime_policy_bytes,
    decide_production_runtime,
    load_production_runtime_measurement_bytes,
)
from pipeline.remote_shell_executor import (
    RemoteContainerLifecycleReceipt,
    RemoteResultBundleError,
    RemoteShellExecutionError,
    RemoteShellExecutor,
    RemoteShellExecutorConfig,
    RemoteShellStatus,
    build_production_remote_result_bundle,
    canonical_remote_shell_job_ref_bytes,
    compute_container_lifecycle_sha256,
    compute_workspace_identity_sha256,
)

_T0 = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
_CONTAINER = "registry.example/nantai@sha256:" + "c" * 64
_REMOTE_TARGET_SHA256 = "b" * 64
_CONTAINER_INSTANCE_ID = "1" * 64


# ---------------------------------------------------------------------------
# Helper loading from sibling test modules
# ---------------------------------------------------------------------------


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).resolve().parent / filename,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rsi = _load_module("_rsi_helpers", "test_real_scene_import.py")
_write_production_training_stage = _rsi._write_production_training_stage
_write_production_closure_evidence = _rsi._write_production_closure_evidence

_rse = _load_module("_rse_helpers", "test_remote_shell_executor.py")
_Runner = _rse._Runner
_rse_fingerprint = _rse._fingerprint
_rse_executable = _rse._executable
_rse_protect_private_key = _rse._protect_private_key
_lifecycle_response = _rse._lifecycle_response
_status_response = _rse._status_response

_rpe = _load_module("_rpe_helpers", "test_production_runtime_evidence.py")
_rpe_measurement = _rpe._measurement
_rpe_policy = _rpe._policy


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Config / policy / lifecycle / status helpers
# ---------------------------------------------------------------------------


def _v2_config(tmp_path: Path) -> RemoteShellExecutorConfig:
    """Config whose identity fields match the production fixture measurement."""
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
    _rse_protect_private_key(private_key)
    measurement = _rpe_measurement()
    policy = _rpe_policy(measurement)
    runtime_policy_path = tmp_path / "production-runtime-policy.json"
    runtime_policy_path.write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )
    return RemoteShellExecutorConfig(
        ssh_binary=_rse_executable(tmp_path / "ssh"),
        scp_binary=_rse_executable(tmp_path / "scp"),
        private_key_path=private_key,
        known_hosts_path=known_hosts,
        expected_host_key_fingerprint=_rse_fingerprint(key_blob),
        ssh_target="gpu-prod",
        known_host="remote.example",
        port=2222,
        remote_root="/srv/nantai-jobs",
        remote_repo_root="/srv/nantai-3d",
        container_identity=_CONTAINER,
        remote_worker_python="/usr/bin/python3",
        expected_worker_python_sha256="f" * 64,
        expected_worker_sha256=_sha(b"worker"),
        expected_worker_version="1.0.0",
        expected_checker_config_sha256="e" * 64,
        remote_target_sha256=_REMOTE_TARGET_SHA256,
        runtime_policy_path=runtime_policy_path,
        expected_runtime_policy_sha256=policy.content_sha256,
    )


def _v2_lifecycle_receipt(
    job,
    *,
    container_id: str = _CONTAINER_INSTANCE_ID,
    workspace_identity_sha256: str | None = None,
    container_identity: str = _CONTAINER,
) -> RemoteContainerLifecycleReceipt:
    """Create a content-addressed lifecycle receipt bound to the job."""
    workspace_sha = (
        workspace_identity_sha256
        or compute_workspace_identity_sha256(
            job_id=job.job_id,
            attempt_id=job.attempt_id,
            workspace_path=job.remote_job_path,
        )
    )
    provisional = RemoteContainerLifecycleReceipt.model_construct(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        workspace_identity_sha256=workspace_sha,
        container_identity=container_identity,
        container_id=container_id,
        transition="container-created-identity-verified",
        receipt_sha256="0" * 64,
    )
    digest = compute_container_lifecycle_sha256(provisional)
    return RemoteContainerLifecycleReceipt(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        workspace_identity_sha256=workspace_sha,
        container_identity=container_identity,
        container_id=container_id,
        transition="container-created-identity-verified",
        receipt_sha256=digest,
    )


def _v2_succeeded_status(
    job,
    *,
    archive_sha256: str,
    archive_size: int,
) -> RemoteShellStatus:
    return RemoteShellStatus(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        state="succeeded",
        updated_at_utc=_T0,
        exit_code=0,
        stdout_sha256=_sha(b"stdout"),
        stderr_sha256=_sha(b"stderr"),
        result_bundle_sha256=archive_sha256,
        result_bundle_size_bytes=archive_size,
    )


# ---------------------------------------------------------------------------
# Full v2 scenario
# ---------------------------------------------------------------------------


def _v2_scenario(
    tmp_path: Path,
    monkeypatch,
    *,
    durable_job_ref_sha256: str | None = None,
) -> SimpleNamespace:
    """Build a complete v2 fetch scenario.

    By default the measurement's durable_job_ref_sha256 and
    workspace_identity_sha256 are rewritten to match the submitted job.
    Pass ``durable_job_ref_sha256`` to override with a wrong value
    (for the cross-job-rejection test).
    """
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
        include_production_closure=False,
    )
    _write_production_closure_evidence(training_root, fixture)

    # Make the bundle a real zip so _load_held_out_source_bytes can reopen it.
    bundle_path = fixture.verified_bundle.path
    with zipfile.ZipFile(bundle_path, "w") as archive:
        for identity in fixture.split.held_out:
            payload = fixture.evaluation_bytes[identity.logical_path]
            archive.writestr(
                f"evaluation/payload/{identity.logical_path}",
                payload,
            )

    config = _v2_config(tmp_path)

    monkeypatch.setattr(
        remote_module,
        "verify_production_training_job_bundle",
        lambda path: fixture.verified_bundle,
    )
    monkeypatch.setattr(
        remote_module,
        "load_training_job_input_bytes",
        lambda bundle: fixture.input_bytes,
    )

    runner = _Runner()
    executor = RemoteShellExecutor(
        config,
        run_command=runner,
        now=lambda: _T0,
    )
    prepared = executor.prepare(fixture.verified_bundle)
    job = executor.submit(prepared)

    correct_durable = hashlib.sha256(
        canonical_remote_shell_job_ref_bytes(job)
    ).hexdigest()
    correct_workspace = compute_workspace_identity_sha256(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        workspace_path=job.remote_job_path,
    )
    actual_durable = (
        durable_job_ref_sha256
        if durable_job_ref_sha256 is not None
        else correct_durable
    )

    result_root = training_root / "remote-result"
    original = load_production_runtime_measurement_bytes(
        (result_root / "production-runtime" / "measurement.json").read_bytes()
    )
    corrected = ProductionRuntimeMeasurement.create(
        observed_at_utc=original.observed_at_utc,
        exact_commit=original.exact_commit,
        remote_target_sha256=original.remote_target_sha256,
        durable_job_ref_sha256=actual_durable,
        workspace_identity_sha256=correct_workspace,
        environment=original.environment,
        executables=original.executables,
        gpu=original.gpu,
        training_cli=original.training_cli,
        probes=original.probes,
    )
    policy = _rpe_policy(corrected)
    decision = decide_production_runtime(corrected, policy)
    (result_root / "production-runtime" / "measurement.json").write_bytes(
        canonical_production_runtime_measurement_bytes(corrected)
    )
    (result_root / "production-runtime" / "policy.json").write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )
    (result_root / "production-runtime" / "decision.json").write_bytes(
        canonical_production_runtime_decision_bytes(decision)
    )

    for relative in (
        "result-bundle-manifest.json",
        "result-bundle.zip",
        "render-evaluation/decision.json",
        "production-training-closure.json",
    ):
        (result_root / relative).unlink()

    archive = training_root / "result-bundle.zip"
    built = build_production_remote_result_bundle(
        result_root=result_root,
        output_path=archive,
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        container_instance_id=corrected.environment.container_instance_id,
        container_identity=corrected.environment.observed_container_identity,
        remote_target_sha256=corrected.remote_target_sha256,
        durable_job_ref_sha256=corrected.durable_job_ref_sha256,
        workspace_identity_sha256=corrected.workspace_identity_sha256,
    )

    lifecycle = _v2_lifecycle_receipt(
        job,
        container_id=corrected.environment.container_instance_id,
        workspace_identity_sha256=corrected.workspace_identity_sha256,
    )
    status = _v2_succeeded_status(
        job,
        archive_sha256=built.bundle_sha256,
        archive_size=built.byte_length,
    )

    return SimpleNamespace(
        executor=executor,
        runner=runner,
        job=job,
        fixture=fixture,
        archive=archive,
        archive_sha256=built.bundle_sha256,
        archive_size=built.byte_length,
        measurement=corrected,
        lifecycle=lifecycle,
        status=status,
        result_root=result_root,
        training_root=training_root,
        workspace_identity_sha256=correct_workspace,
        durable_job_ref_sha256=correct_durable,
    )


# ---------------------------------------------------------------------------
# Test 1: success — eight import contract files materialized
# ---------------------------------------------------------------------------

_EIGHT_CONTRACT_FILES = (
    "result-bundle-manifest.json",
    "production-runtime/measurement.json",
    "production-runtime/policy.json",
    "production-runtime/decision.json",
    "render-evaluation/policy.json",
    "render-evaluation/report.json",
    "render-evaluation/decision.json",
    "production-training-closure.json",
)


def test_fetch_v2_materializes_exact_eight_import_contract_files(
    tmp_path,
    monkeypatch,
):
    scenario = _v2_scenario(tmp_path, monkeypatch)
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    assert scenario.executor.poll(scenario.job).state == "unknown"

    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    destination = tmp_path / "published-result"
    receipt = scenario.executor.fetch(scenario.job, destination)

    assert receipt.state == "succeeded"
    assert receipt.result_bundle_sha256 == scenario.archive_sha256
    assert receipt.quality_role == "production"
    for relative in _EIGHT_CONTRACT_FILES:
        assert (destination / relative).is_file(), (
            f"missing contract file {relative}"
        )
    assert (
        destination / "production-runtime" / "measurement.json"
    ).read_bytes() == canonical_production_runtime_measurement_bytes(
        scenario.measurement
    )


# ---------------------------------------------------------------------------
# Test 2: success — render decision and closure derived after verification
# ---------------------------------------------------------------------------


def test_fetch_v2_streams_large_ply_without_archive_read(
    tmp_path,
    monkeypatch,
):
    """The real fetch caller must activate bounded streaming for large PLY."""
    scenario = _v2_scenario(tmp_path, monkeypatch)
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    read_members: list[str] = []
    original_read = zipfile.ZipFile.read

    def tracking_read(self, name, *args, **kwargs):
        member_name = name if isinstance(name, str) else name.filename
        read_members.append(member_name)
        return original_read(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", tracking_read)
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    destination = tmp_path / "published-streamed-result"

    receipt = scenario.executor.fetch(scenario.job, destination)

    assert receipt.state == "succeeded"
    assert "point_cloud.ply" not in read_members
    assert (destination / "point_cloud.ply").read_bytes() == (
        scenario.result_root / "point_cloud.ply"
    ).read_bytes()


def test_fetch_v2_rejects_streamed_ply_drift_before_publication(
    tmp_path,
    monkeypatch,
):
    scenario = _v2_scenario(tmp_path, monkeypatch)
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    original_verify = scenario.executor._verify_result_semantics

    def verify_then_tamper(verified, context):
        closure = original_verify(verified, context)
        verified.large_member_paths["point_cloud.ply"].write_bytes(
            b"tampered-after-verification"
        )
        return closure

    monkeypatch.setattr(
        scenario.executor,
        "_verify_result_semantics",
        verify_then_tamper,
    )
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    destination = tmp_path / "published-tampered-result"

    with pytest.raises(
        RemoteResultBundleError,
        match="changed before publication",
    ):
        scenario.executor.fetch(scenario.job, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".*.staging"))


def test_fetch_v2_derives_render_decision_and_closure_after_archive_verification(
    tmp_path,
    monkeypatch,
):
    scenario = _v2_scenario(tmp_path, monkeypatch)
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    destination = tmp_path / "published-result"
    receipt = scenario.executor.fetch(scenario.job, destination)

    assert receipt.state == "succeeded"
    decision = json.loads(
        (destination / "render-evaluation" / "decision.json").read_text()
    )
    assert decision["accepted"] is True
    closure = json.loads(
        (destination / "production-training-closure.json").read_text()
    )
    assert closure["job_id"] == scenario.job.job_id
    assert closure["attempt_id"] == scenario.job.attempt_id
    assert closure["result_bundle_archive_sha256"] == (
        scenario.archive_sha256
    )
    manifest = json.loads(
        (destination / "result-bundle-manifest.json").read_text()
    )
    assert manifest["schema"] == "nantai.remote-result-bundle.v2"
    assert manifest["job_id"] == scenario.job.job_id
    assert manifest["attempt_id"] == scenario.job.attempt_id


# ---------------------------------------------------------------------------
# Test 3: cross-job durable_job_ref binding rejected
# ---------------------------------------------------------------------------


def test_fetch_v2_rejects_cross_job_durable_job_ref_binding(
    tmp_path,
    monkeypatch,
):
    scenario = _v2_scenario(
        tmp_path,
        monkeypatch,
        durable_job_ref_sha256="d" * 64,
    )
    assert scenario.measurement.durable_job_ref_sha256 != (
        scenario.durable_job_ref_sha256
    )
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    destination = tmp_path / "result"
    with pytest.raises(RemoteResultBundleError):
        scenario.executor.fetch(scenario.job, destination)
    assert not destination.exists()
    context = scenario.executor._context(scenario.job)
    assert context.receipt.state == "unknown"


# ---------------------------------------------------------------------------
# Test 4: lifecycle container or workspace swap rejected
# ---------------------------------------------------------------------------


def test_fetch_v2_rejects_lifecycle_container_or_workspace_swap(
    tmp_path,
    monkeypatch,
):
    scenario = _v2_scenario(tmp_path, monkeypatch)
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.executor.poll(scenario.job)

    swapped = _v2_lifecycle_receipt(
        scenario.job,
        container_id="2" * 64,
        workspace_identity_sha256=scenario.workspace_identity_sha256,
    )
    scenario.runner.responses.append(_lifecycle_response(swapped))
    scenario.runner.download_source = scenario.archive
    destination = tmp_path / "result"
    with pytest.raises(
        RemoteShellExecutionError,
        match="container swap|identity differs",
    ):
        scenario.executor.fetch(scenario.job, destination)
    assert not destination.exists()
    context = scenario.executor._context(scenario.job)
    assert context.receipt.state == "unknown"


# ---------------------------------------------------------------------------
# Test 5: status archive sha or size swap rejected
# ---------------------------------------------------------------------------


def test_fetch_v2_rejects_status_archive_sha_or_size_swap(
    tmp_path,
    monkeypatch,
):
    scenario = _v2_scenario(tmp_path, monkeypatch)
    wrong_status = _v2_succeeded_status(
        scenario.job,
        archive_sha256="a" * 64,
        archive_size=scenario.archive_size,
    )
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(wrong_status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    destination = tmp_path / "result"
    with pytest.raises(
        RemoteResultBundleError,
        match="differs from remote status",
    ):
        scenario.executor.fetch(scenario.job, destination)
    assert not destination.exists()
    context = scenario.executor._context(scenario.job)
    assert context.receipt.state == "unknown"


# ---------------------------------------------------------------------------
# Test 6: collision or sync-unknown never returns succeeded
# ---------------------------------------------------------------------------


def test_fetch_v2_collision_or_sync_unknown_never_returns_succeeded(
    tmp_path,
    monkeypatch,
):
    scenario = _v2_scenario(tmp_path, monkeypatch)
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    # Collision: destination already exists — fetch must raise and must not
    # overwrite the existing directory.
    destination = tmp_path / "existing-result"
    destination.mkdir()
    (destination / "sentinel.txt").write_bytes(b"original")
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    with pytest.raises(
        RemoteShellExecutionError,
        match="boundary must be absent",
    ):
        scenario.executor.fetch(scenario.job, destination)
    assert (destination / "sentinel.txt").read_bytes() == b"original"
    context = scenario.executor._context(scenario.job)
    assert context.receipt.state == "unknown"

    # Sync unknown: a fresh job that was never polled has no succeeded
    # status — fetch must raise and must not create the destination.
    fresh = _v2_scenario(tmp_path / "fresh", monkeypatch)
    destination2 = tmp_path / "result-unknown"
    with pytest.raises(
        RemoteShellExecutionError,
        match="succeeded remote status",
    ):
        fresh.executor.fetch(fresh.job, destination2)
    assert not destination2.exists()
