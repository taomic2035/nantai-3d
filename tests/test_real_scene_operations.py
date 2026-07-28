from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.real_scene_operations as operations_module
from pipeline.durable_io import DurableIOError
from pipeline.real_dataset import HfDatasetSource
from pipeline.real_scene_operations import RealScenePipelineOperations
from pipeline.real_scene_runner import (
    RealSceneRunner,
    RealSceneRunOptions,
    RealSceneSourceIdentity,
    StageExecution,
)
from pipeline.remote_shell_executor import (
    RemoteContainerLifecycleReceipt,
    RemoteResultBundleError,
    RemoteShellExecutionError,
    RemoteShellJobRef,
    canonical_container_lifecycle_bytes,
    canonical_remote_shell_job_ref_bytes,
    compute_container_lifecycle_sha256,
    compute_workspace_identity_sha256,
    publish_remote_container_lifecycle_receipt,
    publish_remote_shell_job_ref,
)
from pipeline.training_executor import ExecutorObservation


def _source() -> HfDatasetSource:
    return HfDatasetSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="poster",
        role="internal-canary",
        source_kind="hf-dataset",
        repository="owner/repo",
        repository_revision="4" * 40,
        subtree="poster",
        capture_subtree="poster/images",
        declared_file_count=1,
        declared_total_bytes=5,
        license_status="not-declared",
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )


def _runner(tmp_path: Path, operations) -> RealSceneRunner:
    return RealSceneRunner(
        source=RealSceneSourceIdentity(
            dataset_id="poster",
            role="internal-canary",
            source_sha256="a" * 64,
        ),
        workspace_base=tmp_path / "real-scene",
        operations=operations,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_regular_files_inspects_windows_long_path_members(tmp_path):
    """Stage evidence enumeration must preserve normal semantic paths."""
    desired_root_length = 210
    padding = desired_root_length - len(str(tmp_path.resolve())) - 1
    assert 1 <= padding <= 255
    root = tmp_path / ("w" * padding)
    root.mkdir()
    report = root / ("source_manifest_" + "a" * 64 + ".json")
    assert len(str(report.resolve())) > 260
    extended_report = Path("\\\\?\\" + str(report.resolve()))
    extended_report.write_bytes(b"evidence")

    assert operations_module._regular_files(root) == (report,)


def _remote_lifecycle(job: RemoteShellJobRef, *, container_id="a" * 64):
    workspace_sha = compute_workspace_identity_sha256(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        workspace_path=job.remote_job_path,
    )
    provisional = RemoteContainerLifecycleReceipt.model_construct(
        job_id=job.job_id,
        attempt_id=job.attempt_id,
        request_sha256=job.request_sha256,
        training_bundle_sha256=job.training_bundle_sha256,
        runtime_policy_sha256=job.runtime_policy_sha256,
        workspace_identity_sha256=workspace_sha,
        container_identity="registry.example/nantai@sha256:" + "c" * 64,
        container_id=container_id,
        transition="container-created-identity-verified",
        receipt_sha256="0" * 64,
    )
    return RemoteContainerLifecycleReceipt(
        **{
            **provisional.model_dump(exclude={"receipt_sha256"}),
            "receipt_sha256": compute_container_lifecycle_sha256(
                provisional
            ),
        }
    )


def _production_fixture(tmp_path, monkeypatch):
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
            remote_config_path=tmp_path / "remote.json",
            remote_poll_interval_seconds=0.001,
            remote_timeout_seconds=1,
        ),
    )
    stage_root = tmp_path / "workspace/stages/train-production/attempt-one"
    stage_root.mkdir(parents=True)
    bundle = stage_root / "training-bundle.zip"
    bundle.write_bytes(b"bundle")
    prepared = SimpleNamespace(path=bundle)
    monkeypatch.setattr(
        operations,
        "_build_training_bundle",
        lambda *_args, **_kwargs: prepared,
    )
    config = SimpleNamespace(
        container_identity="image@sha256:" + "a" * 64,
        container_runtime="docker",
        expected_host_key_fingerprint="SHA256:" + "A" * 43,
    )
    monkeypatch.setattr(operations, "_remote_config", lambda: config)
    job = RemoteShellJobRef(
        job_id="job-one",
        attempt_id="attempt-one",
        submitted_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
        request_sha256="b" * 64,
        training_bundle_sha256="c" * 64,
        runtime_policy_sha256="e" * 64,
        config_identity_sha256="d" * 64,
        remote_job_path="/srv/nantai-jobs/job-one/attempt-one",
    )
    return operations, stage_root, prepared, config, job


def test_train_production_preserves_drifted_public_config_and_blocks_executor(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, _prepared, _config, _job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    public_config = stage_root / "remote-executor-public-config.json"
    drifted = b'{"tampered":true}\n'
    public_config.write_bytes(drifted)
    constructor_calls = {"n": 0}

    class UnexpectedRemoteExecutor:
        def __init__(self, measured):
            del measured
            constructor_calls["n"] += 1
            raise RemoteShellExecutionError("executor must not be constructed")

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        UnexpectedRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"
    assert "remote executor preflight failed" in execution.reason
    assert public_config.read_bytes() == drifted
    assert constructor_calls["n"] == 0


def test_train_production_rejects_symlinked_public_config_without_overwrite(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, _prepared, _config, _job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    victim = tmp_path / "outside-stage.json"
    victim.write_bytes(b"preserve-me\n")
    public_config = stage_root / "remote-executor-public-config.json"
    try:
        public_config.symlink_to(victim)
    except OSError:
        pytest.skip("filesystem does not permit symlink creation")
    constructor_calls = {"n": 0}

    class UnexpectedRemoteExecutor:
        def __init__(self, measured):
            del measured
            constructor_calls["n"] += 1
            raise RemoteShellExecutionError("executor must not be constructed")

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        UnexpectedRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"
    assert "remote executor preflight failed" in execution.reason
    assert victim.read_bytes() == b"preserve-me\n"
    assert public_config.is_symlink()
    assert constructor_calls["n"] == 0


def test_train_production_reuses_exact_public_config_without_rewriting(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, _prepared, _config, _job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    public_config = stage_root / "remote-executor-public-config.json"
    expected = (
        '{"container_identity":"image@sha256:'
        + "a" * 64
        + '","container_runtime":"docker",'
        '"expected_host_key_fingerprint":"SHA256:'
        + "A" * 43
        + '"}\n'
    ).encode("ascii")
    public_config.write_bytes(expected)
    calls = {"construct": 0, "close": 0}

    class MatchingRemoteExecutor:
        def __init__(self, measured):
            del measured
            calls["construct"] += 1

        def prepare(self, measured):
            del measured
            raise RemoteShellExecutionError("focused test stop")

        def close(self):
            calls["close"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        MatchingRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"
    assert public_config.read_bytes() == expected
    assert calls == {"construct": 1, "close": 1}


def test_private_model_replace_does_not_modify_hardlink_alias(
    tmp_path,
):
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
        ),
    )
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    outside = tmp_path / "outside-stage.json"
    original = b'{"outside":"preserve"}\n'
    outside.write_bytes(original)
    destination = stage_root / "remote-observation.private.json"
    try:
        os.link(outside, destination)
    except OSError:
        pytest.skip("filesystem does not permit hard-link creation")
    observation = ExecutorObservation(
        state="running",
        observed_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
    )

    operations._write_private_model(destination, observation)

    assert outside.read_bytes() == original
    assert destination.read_bytes() == (
        operations_module.canonical_model_bytes(observation)
    )
    assert destination.stat().st_ino != outside.stat().st_ino


def test_hf_fetch_receipt_binds_downloaded_payload_bytes(
    tmp_path,
    monkeypatch,
):
    def fake_fetch(source, stage_root):
        del source
        (stage_root / "dataset/poster/images").mkdir(parents=True)
        (stage_root / "dataset/poster/images/frame.png").write_bytes(b"image")
        (stage_root / "dataset-lock.json").write_bytes(b"lock\n")
        (stage_root / "dataset-policy.json").write_bytes(b"policy\n")
        (stage_root / "dataset-receipt.json").write_bytes(b"receipt\n")
        return SimpleNamespace()

    monkeypatch.setattr(
        "pipeline.real_scene_operations.fetch_hf_dataset",
        fake_fetch,
    )
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
        ),
    )
    runner = _runner(tmp_path, operations)

    receipt = runner.run("fetch")

    assert any(
        output.path.endswith("dataset/poster/images/frame.png") for output in receipt.outputs
    )


def test_rejected_sfm_retains_quality_report_and_stops(
    tmp_path,
    monkeypatch,
):
    def fake_fetch(source, stage_root):
        del source
        (stage_root / "dataset").mkdir(parents=True)
        (stage_root / "dataset/frame.png").write_bytes(b"image")
        return SimpleNamespace()

    def fake_prepare(source, source_root, stage_root):
        del source, source_root
        (stage_root / "capture").mkdir(parents=True)
        (stage_root / "capture/manifest.json").write_bytes(b"capture\n")
        return SimpleNamespace(
            source_sha256="a" * 64,
            dataset_receipt_sha256="b" * 64,
            capture=SimpleNamespace(manifest_digest="c" * 64),
        )

    def fake_sfm(capture, stage_root, policy):
        del capture, policy
        report = stage_root / "sfm/registration-quality-report.json"
        report.parent.mkdir(parents=True)
        report.write_bytes(b'{"training_allowed":false}\n')
        return SimpleNamespace(
            quality=SimpleNamespace(
                training_allowed=False,
                rejection_reasons=("registered ratio below policy",),
            )
        )

    monkeypatch.setattr(
        "pipeline.real_scene_operations.fetch_hf_dataset",
        fake_fetch,
    )
    monkeypatch.setattr(
        "pipeline.real_scene_operations.prepare_real_capture",
        fake_prepare,
    )
    monkeypatch.setattr(
        "pipeline.real_scene_operations.run_real_sfm",
        fake_sfm,
    )
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
        ),
    )
    runner = _runner(tmp_path, operations)

    runner.run("fetch")
    try:
        runner.run("sfm")
    except ValueError as exc:
        assert "registered ratio" in str(exc)
    else:
        raise AssertionError("rejected SfM did not block")

    receipt_path = next((runner.receipt_root / "sfm").glob("*.json"))
    assert "registration-quality-report.json" in receipt_path.read_text(encoding="ascii")


def test_preview_training_stage_uses_preview_only_executor(
    tmp_path,
    monkeypatch,
):
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
        ),
    )
    stage_root = tmp_path / "workspace/stages/train-preview/attempt-one"
    stage_root.mkdir(parents=True)
    bundle = stage_root / "training-bundle.zip"
    bundle.write_bytes(b"bundle")
    monkeypatch.setattr(
        operations,
        "_build_training_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(path=bundle),
    )
    monkeypatch.setattr(
        "pipeline.real_scene_operations._local_brush_config",
        lambda root: SimpleNamespace(execution_root=root / "local-brush"),
    )

    class FakeLocalExecutor:
        def __init__(self, config):
            self.config = config

        def run(self, verified):
            assert verified.path == bundle
            self.config.execution_root.mkdir()
            (self.config.execution_root / "trained.ply").write_bytes(b"ply")
            return SimpleNamespace(receipt=SimpleNamespace(quality_role="preview-only"))

    monkeypatch.setattr(
        "pipeline.real_scene_operations.LocalBrushExecutor",
        FakeLocalExecutor,
    )

    execution = operations.execute(
        "train-preview",
        stage_root,
        (),
    )

    assert execution.state == "completed"
    assert any(path.name == "trained.ply" for path in execution.artifacts)


def test_unreachable_remote_training_stays_unknown_with_evidence(
    tmp_path,
    monkeypatch,
):
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
            remote_config_path=tmp_path / "remote.json",
            remote_poll_interval_seconds=0.001,
            remote_timeout_seconds=1,
        ),
    )
    stage_root = tmp_path / "workspace/stages/train-production/attempt-one"
    stage_root.mkdir(parents=True)
    bundle = stage_root / "training-bundle.zip"
    bundle.write_bytes(b"bundle")
    monkeypatch.setattr(
        operations,
        "_build_training_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(path=bundle),
    )
    monkeypatch.setattr(
        operations,
        "_remote_config",
        lambda: SimpleNamespace(
            container_identity="image@sha256:" + "a" * 64,
            container_runtime="docker",
            expected_host_key_fingerprint="SHA256:" + "A" * 43,
        ),
    )
    job = RemoteShellJobRef(
        job_id="job-one",
        attempt_id="attempt-one",
        submitted_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
        request_sha256="b" * 64,
        training_bundle_sha256="c" * 64,
        runtime_policy_sha256="e" * 64,
        config_identity_sha256="d" * 64,
        remote_job_path="/srv/nantai-jobs/job-one/attempt-one",
    )
    monotonic = iter((0.0, 2.0))
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: next(monotonic),
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )

    class FakeRemoteExecutor:
        def __init__(self, config):
            del config

        def prepare(self, verified):
            return verified

        def submit(self, prepared):
            assert prepared.path == bundle
            return job

        def poll(self, submitted):
            assert submitted == job
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
            )

        def bound_lifecycle_receipt(self, submitted):
            assert submitted == job
            raise RemoteShellExecutionError("lifecycle not bound")

        def close(self):
            pass

    monkeypatch.setattr(
        "pipeline.real_scene_operations.RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute(
        "train-production",
        stage_root,
        (),
    )

    assert execution.state == "unknown"
    assert "no success was inferred" in execution.reason
    assert any(path.name == "remote-job.private.json" for path in execution.evidence_artifacts)


def test_existing_remote_job_is_restored_without_resubmit(
    tmp_path,
    monkeypatch,
):
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
            remote_config_path=tmp_path / "remote.json",
            remote_poll_interval_seconds=0.001,
            remote_timeout_seconds=1,
        ),
    )
    stage_root = (
        tmp_path
        / "workspace/stages/train-production/attempt-one"
    )
    stage_root.mkdir(parents=True)
    bundle = stage_root / "training-bundle.zip"
    bundle.write_bytes(b"bundle")
    prepared = SimpleNamespace(path=bundle)
    monkeypatch.setattr(
        operations,
        "_build_training_bundle",
        lambda *_args, **_kwargs: prepared,
    )
    config = SimpleNamespace(
        container_identity="image@sha256:" + "a" * 64,
        container_runtime="docker",
        expected_host_key_fingerprint="SHA256:" + "A" * 43,
    )
    monkeypatch.setattr(
        operations,
        "_remote_config",
        lambda: config,
    )
    job = RemoteShellJobRef(
        job_id="job-one",
        attempt_id="attempt-one",
        submitted_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
        request_sha256="b" * 64,
        training_bundle_sha256="c" * 64,
        runtime_policy_sha256="e" * 64,
        config_identity_sha256="d" * 64,
        remote_job_path="/srv/nantai-jobs/job-one/attempt-one",
    )
    (stage_root / "remote-job.private.json").write_bytes(
        canonical_remote_shell_job_ref_bytes(job)
    )
    lifecycle = _remote_lifecycle(job)
    (stage_root / "remote-container-lifecycle.private.json").write_bytes(
        canonical_container_lifecycle_bytes(lifecycle)
    )
    calls = {"restore": 0, "submit": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured_config):
            assert measured_config == config

        def prepare(self, verified):
            assert verified == prepared
            return prepared

        def restore(
            self,
            measured_prepared,
            measured_job,
            *,
            expected_lifecycle,
        ):
            assert measured_prepared == prepared
            assert measured_job == job
            assert expected_lifecycle == lifecycle
            calls["restore"] += 1
            return measured_job

        def submit(self, _prepared):
            calls["submit"] += 1
            raise AssertionError("restore must not resubmit")

        def poll(self, measured_job):
            assert measured_job == job
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
            )

        def bound_lifecycle_receipt(self, measured_job):
            assert measured_job == job
            return lifecycle

        def close(self):
            pass

    monkeypatch.setattr(
        "pipeline.real_scene_operations.RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute(
        "train-production",
        stage_root,
        (),
    )

    assert execution.state == "unknown"
    assert calls == {"restore": 1, "submit": 0}


def test_initial_lifecycle_binding_is_persisted_before_fetch(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    calls = {"fetch": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            assert measured == config

        def prepare(self, measured):
            assert measured == prepared
            return prepared

        def submit(self, measured):
            assert measured == prepared
            return job

        def poll(self, measured):
            assert measured == job
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            assert measured == job
            return lifecycle

        def fetch(self, measured, _destination):
            assert measured == job
            calls["fetch"] += 1
            path = (
                stage_root
                / "remote-container-lifecycle.private.json"
            )
            assert path.read_bytes() == (
                canonical_container_lifecycle_bytes(lifecycle)
            )
            raise RemoteShellExecutionError("stop after ordering check")

        def close(self):
            pass

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert calls["fetch"] == 1


def test_first_unbound_poll_retries_then_persists_bound_lifecycle(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    calls = {"poll": 0, "fetch": 0}
    monotonic = iter((0.0, 0.1, 0.1))
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: next(monotonic),
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )

    class FakeRemoteExecutor:
        def __init__(self, measured):
            assert measured == config

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            assert measured == prepared
            return job

        def poll(self, measured):
            assert measured == job
            calls["poll"] += 1
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            assert measured == job
            if calls["poll"] == 1:
                raise RemoteShellExecutionError("not bound yet")
            return lifecycle

        def fetch(self, measured, _destination):
            assert measured == job
            calls["fetch"] += 1
            path = (
                stage_root
                / "remote-container-lifecycle.private.json"
            )
            assert path.read_bytes() == (
                canonical_container_lifecycle_bytes(lifecycle)
            )
            raise RemoteShellExecutionError("stop after ordering check")

        def close(self):
            pass

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert calls == {"poll": 2, "fetch": 1}


def test_remote_job_without_lifecycle_fails_before_remote_methods(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    (stage_root / "remote-job.private.json").write_bytes(
        canonical_remote_shell_job_ref_bytes(job)
    )
    calls = {"submit": 0, "restore": 0, "poll": 0, "fetch": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            assert measured == config

        def prepare(self, measured):
            assert measured == prepared
            return prepared

        def close(self):
            pass

        def __getattr__(self, name):
            if name in calls:
                def called(*_args, **_kwargs):
                    calls[name] += 1
                    raise AssertionError(f"{name} must not be called")
                return called
            raise AttributeError(name)

    monkeypatch.setattr(operations_module, "RemoteShellExecutor", FakeRemoteExecutor)

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state in {"blocked", "unknown"}
    assert calls == {"submit": 0, "restore": 0, "poll": 0, "fetch": 0}


def test_remote_lifecycle_without_job_is_unknown_without_remote_methods(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    (
        stage_root / "remote-container-lifecycle.private.json"
    ).write_bytes(canonical_container_lifecycle_bytes(lifecycle))
    calls = {"submit": 0, "restore": 0, "poll": 0, "fetch": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            assert measured == config

        def prepare(self, measured):
            assert measured == prepared
            return prepared

        def close(self):
            pass

        def __getattr__(self, name):
            if name in calls:
                def called(*_args, **_kwargs):
                    calls[name] += 1
                    raise AssertionError(f"{name} must not be called")
                return called
            raise AttributeError(name)

    monkeypatch.setattr(operations_module, "RemoteShellExecutor", FakeRemoteExecutor)

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert calls == {"submit": 0, "restore": 0, "poll": 0, "fetch": 0}


def test_malformed_remote_lifecycle_blocks_restore(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    (stage_root / "remote-job.private.json").write_bytes(
        canonical_remote_shell_job_ref_bytes(job)
    )
    (
        stage_root / "remote-container-lifecycle.private.json"
    ).write_bytes(b'{"schema":"bad","schema":"duplicate"}\n')

    class FakeRemoteExecutor:
        def __init__(self, measured):
            assert measured == config

        def prepare(self, measured):
            assert measured == prepared
            return prepared

        def restore(self, *_args, **_kwargs):
            raise AssertionError("malformed lifecycle must block restore")

        def close(self):
            pass

    monkeypatch.setattr(operations_module, "RemoteShellExecutor", FakeRemoteExecutor)

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"


def test_symlink_remote_lifecycle_blocks_restore(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    (stage_root / "remote-job.private.json").write_bytes(
        canonical_remote_shell_job_ref_bytes(job)
    )
    target = tmp_path / "lifecycle.json"
    target.write_bytes(
        canonical_container_lifecycle_bytes(_remote_lifecycle(job))
    )
    lifecycle_path = (
        stage_root / "remote-container-lifecycle.private.json"
    )
    try:
        lifecycle_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    class FakeRemoteExecutor:
        def __init__(self, measured):
            assert measured == config

        def prepare(self, measured):
            assert measured == prepared
            return prepared

        def restore(self, *_args, **_kwargs):
            raise AssertionError("symlink lifecycle must block restore")

        def close(self):
            pass

    monkeypatch.setattr(operations_module, "RemoteShellExecutor", FakeRemoteExecutor)

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"


@pytest.mark.parametrize(
    "fault",
    ["competing-destination", "published-false", "published-true"],
)
def test_lifecycle_publication_error_blocks_fetch(
    tmp_path,
    monkeypatch,
    fault,
):
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    existing = b"competing lifecycle\n"
    calls = {"publish": 0, "fetch": 0}
    from pipeline.durable_io import publish_file_noreplace as real_publish

    class FakeRemoteExecutor:
        def __init__(self, measured):
            assert measured == config

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            assert measured == prepared
            return job

        def poll(self, measured):
            assert measured == job
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            assert measured == job
            return lifecycle

        def fetch(self, *_args):
            calls["fetch"] += 1
            raise AssertionError("publication error must block fetch")

        def close(self):
            pass

    def fail_publish(source, destination):
        destination = Path(destination)
        if destination.name != "remote-container-lifecycle.private.json":
            return real_publish(source, destination)
        calls["publish"] += 1
        if fault == "competing-destination":
            destination.write_bytes(existing)
            raise FileExistsError("competing writer won")
        if fault == "published-true":
            os.link(source, destination)
            raise DurableIOError(
                "published but sync failed",
                published=True,
            )
        raise DurableIOError(
            "publication failed before namespace change",
            published=False,
        )

    monkeypatch.setattr(operations_module, "RemoteShellExecutor", FakeRemoteExecutor)
    monkeypatch.setattr(
        "pipeline.durable_io.publish_file_noreplace",
        fail_publish,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert calls == {"publish": 1, "fetch": 0}
    lifecycle_path = (
        stage_root / "remote-container-lifecycle.private.json"
    )
    if fault == "competing-destination":
        assert lifecycle_path.read_bytes() == existing
        assert "cannot be published" in execution.reason
    elif fault == "published-true":
        assert lifecycle_path.read_bytes() == (
            canonical_container_lifecycle_bytes(lifecycle)
        )
        assert "published but durability is unconfirmed" in execution.reason
    else:
        assert not lifecycle_path.exists()
        assert "not published" in execution.reason
    assert not tuple(stage_root.glob(".*.staging"))


def test_persisted_lifecycle_mismatch_blocks_terminal_fetch(
    tmp_path,
    monkeypatch,
):
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    persisted = _remote_lifecycle(job)
    remote = _remote_lifecycle(job, container_id="f" * 64)
    (stage_root / "remote-job.private.json").write_bytes(
        canonical_remote_shell_job_ref_bytes(job)
    )
    (
        stage_root / "remote-container-lifecycle.private.json"
    ).write_bytes(canonical_container_lifecycle_bytes(persisted))
    calls = {"restore": 0, "fetch": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            assert measured == config

        def prepare(self, measured):
            return measured

        def restore(self, measured, measured_job, *, expected_lifecycle):
            assert measured == prepared
            assert measured_job == job
            assert expected_lifecycle == persisted
            calls["restore"] += 1
            return job

        def poll(self, measured):
            assert measured == job
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            assert measured == job
            return remote

        def fetch(self, *_args):
            calls["fetch"] += 1
            raise AssertionError("mismatch must block fetch")

        def close(self):
            pass

    monkeypatch.setattr(operations_module, "RemoteShellExecutor", FakeRemoteExecutor)

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert calls == {"restore": 1, "fetch": 0}


def test_import_stage_invokes_content_closed_scene_adapter(
    tmp_path,
    monkeypatch,
):
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
        ),
    )
    workspace = tmp_path / "workspace"
    training_root = workspace / "stages/train-preview/attempt-train"
    training_root.mkdir(parents=True)
    stage_root = workspace / "stages/import/attempt-import"
    calls = []

    def fake_import(training, output, **kwargs):
        calls.append((training, output, kwargs))
        output.mkdir(parents=True)
        (output / "import-receipt.json").write_bytes(b"receipt\n")
        return SimpleNamespace(alignment_rms_m=None)

    monkeypatch.setattr(
        "pipeline.real_scene_operations.import_real_scene",
        fake_import,
    )
    execution = operations.execute(
        "import",
        stage_root,
        (
            SimpleNamespace(
                stage="train-preview",
                attempt_id="attempt-train",
            ),
        ),
    )

    assert execution.state == "completed"
    assert execution.alignment_rms_m is None
    assert execution.artifacts == (stage_root / "import-receipt.json",)
    assert calls == [
        (
            training_root,
            stage_root,
            {
                "source_role": "internal-canary",
                "control_points_path": None,
                "geo_origin": None,
                "chunk_size": 50.0,
            },
        )
    ]


def test_accept_stage_publishes_content_addressed_aggregate(
    tmp_path,
    monkeypatch,
):
    class BootstrapOperations:
        def execute(self, stage, stage_root, prerequisite_receipts):
            del prerequisite_receipts
            stage_root.mkdir(parents=True)
            files = []
            if stage == "fetch":
                paths = (
                    "dataset-lock.json",
                    "dataset-receipt.json",
                    "dataset/frame.jpg",
                )
            elif stage == "sfm":
                paths = (
                    "capture/bundle/manifest.json",
                    "prepared-capture-evidence.json",
                    "registration-quality-policy.json",
                    "sfm/registration.json",
                    "sfm/registration-quality-report.json",
                )
            elif stage == "train-production":
                paths = (
                    "training-bundle/training-job.zip",
                    "remote-result/render-evaluation/policy.json",
                    "remote-result/render-evaluation/report.json",
                )
            elif stage == "import":
                paths = ("import-receipt.json",)
            else:
                raise AssertionError(stage)
            for relative in paths:
                path = stage_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"{stage}:{relative}\n".encode("ascii"))
                files.append(path)
            return StageExecution(
                state="completed",
                artifacts=tuple(files),
                alignment_rms_m=(0.0 if stage == "import" else None),
            )

    bootstrap = _runner(tmp_path, BootstrapOperations())
    bootstrap.run("fetch")
    bootstrap.run("sfm")
    bootstrap.run("train-production")
    bootstrap.run("import")
    workspace = bootstrap.workspace
    evidence = workspace / "evidence"
    evidence.mkdir()
    external = []
    for name in (
        "viewer-performance-policy.json",
        "viewer-performance-report.json",
        "human-review-policy.json",
        "human-visual-review.json",
    ):
        path = evidence / name
        path.write_bytes(f"{name}\n".encode("ascii"))
        external.append(path)

    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
        ),
    )
    published = workspace / "accepted-report.json"
    pointer_calls = []

    def fake_publish(report, root):
        assert root == workspace
        assert report.source_role == "internal-canary"
        assert report.training_root.path.startswith("stages/train-production/")
        assert report.render_root.path.endswith("/remote-result")
        published.write_bytes(b"accepted\n")
        return published, SimpleNamespace(
            canary_accepted=True,
            production_release_allowed=False,
            reasons=(),
        )

    monkeypatch.setattr(
        "pipeline.real_scene_operations.publish_real_scene_acceptance",
        fake_publish,
    )
    monkeypatch.setattr(
        "pipeline.real_scene_operations.publish_real_scene_acceptance_pointer",
        lambda report_path, root: (
            pointer_calls.append((report_path, root))
            or root / "latest-acceptance.json"
        ),
    )
    monkeypatch.setattr(
        operations,
        "_acceptance_external_files",
        lambda *_args: tuple(external),
    )
    bootstrap.operations = operations

    receipt = bootstrap.run("accept")

    assert receipt.status == "completed"
    assert any(output.path.endswith("accepted-report.json") for output in receipt.outputs)
    assert pointer_calls == [
        (
            published,
            tmp_path / "real-scene",
        )
    ]


# ---------------------------------------------------------------------------
# H1: deadline bounds and explicit executor close
# ---------------------------------------------------------------------------


def test_remote_poll_sleep_never_overshoots_deadline(
    tmp_path,
    monkeypatch,
):
    """Each poll sleep must be min(interval, max(0, deadline - monotonic()))."""
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
            remote_config_path=tmp_path / "remote.json",
            remote_poll_interval_seconds=5.0,
            remote_timeout_seconds=10.0,
        ),
    )
    stage_root = tmp_path / "workspace/stages/train-production/attempt-one"
    stage_root.mkdir(parents=True)
    bundle = stage_root / "training-bundle.zip"
    bundle.write_bytes(b"bundle")
    prepared = SimpleNamespace(path=bundle)
    monkeypatch.setattr(
        operations,
        "_build_training_bundle",
        lambda *_args, **_kwargs: prepared,
    )
    config = SimpleNamespace(
        container_identity="image@sha256:" + "a" * 64,
        container_runtime="docker",
        expected_host_key_fingerprint="SHA256:" + "A" * 43,
    )
    monkeypatch.setattr(operations, "_remote_config", lambda: config)
    job = RemoteShellJobRef(
        job_id="job-one",
        attempt_id="attempt-one",
        submitted_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
        request_sha256="b" * 64,
        training_bundle_sha256="c" * 64,
        runtime_policy_sha256="e" * 64,
        config_identity_sha256="d" * 64,
        remote_job_path="/srv/nantai-jobs/job-one/attempt-one",
    )
    lifecycle = _remote_lifecycle(job)
    monotonic_values = iter(
        [
            0.0,
            0.0,
            0.0,
            7.0,
            7.0,
        ]
    )
    sleeps: list[float] = []

    def fake_monotonic():
        return next(monotonic_values)

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(operations_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(operations_module.time, "sleep", fake_sleep)

    poll_count = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured
            self.close_calls = 0

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            return job

        def poll(self, measured):
            del measured
            poll_count["n"] += 1
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            del measured
            if poll_count["n"] <= 2:
                raise RemoteShellExecutionError("not bound yet")
            return lifecycle

        def fetch(self, measured, destination):
            del measured, destination
            raise RemoteShellExecutionError("stop")

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert len(sleeps) == 2
    assert sleeps[0] == 5.0
    assert sleeps[1] == 3.0


def test_train_production_closes_remote_executor_on_success(
    tmp_path,
    monkeypatch,
):
    """Executor must be closed exactly once on success path."""
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )
    monkeypatch.setattr(
        operations,
        "_write_private_model",
        lambda *_args, **_kwargs: None,
    )

    close_calls = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            return job

        def poll(self, measured):
            del measured
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            del measured
            return lifecycle

        def fetch(self, measured, destination):
            del measured, destination
            return SimpleNamespace(
                state="succeeded",
                quality_role="production",
            )

        def close(self):
            close_calls["n"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "completed"
    assert close_calls["n"] == 1


def test_train_production_closes_remote_executor_on_failure(
    tmp_path,
    monkeypatch,
):
    """Executor must be closed exactly once on failed training path."""
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )

    close_calls = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            return job

        def poll(self, measured):
            del measured
            return ExecutorObservation(
                state="failed",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=1,
                stdout_sha256="a" * 64,
                stderr_sha256="b" * 64,
            )

        def bound_lifecycle_receipt(self, measured):
            del measured
            return lifecycle

        def close(self):
            close_calls["n"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"
    assert "failed with exit code 1" in execution.reason
    assert close_calls["n"] == 1


def test_train_production_closes_remote_executor_on_exception(
    tmp_path,
    monkeypatch,
):
    """Executor must be closed exactly once even if poll raises."""
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )

    close_calls = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            return job

        def poll(self, measured):
            del measured
            raise OSError("transport gone")

        def bound_lifecycle_receipt(self, measured):
            del measured
            return lifecycle

        def close(self):
            close_calls["n"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert close_calls["n"] == 1


# ---------------------------------------------------------------------------
# H1 Codex review addendum: prepare must be inside try/finally, sleep(0)
# forbidden when remaining is zero, and close must be parametrised across
# every return path.
# ---------------------------------------------------------------------------


def _deadline_operations(tmp_path, *, interval=5.0, timeout=10.0):
    operations = RealScenePipelineOperations(
        source=_source(),
        options=RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary",
            remote_config_path=tmp_path / "remote.json",
            remote_poll_interval_seconds=interval,
            remote_timeout_seconds=timeout,
        ),
    )
    stage_root = tmp_path / "workspace/stages/train-production/attempt-one"
    stage_root.mkdir(parents=True)
    bundle = stage_root / "training-bundle.zip"
    bundle.write_bytes(b"bundle")
    prepared = SimpleNamespace(path=bundle)
    return operations, stage_root, prepared


def _deadline_config(monkeypatch, operations):
    config = SimpleNamespace(
        container_identity="image@sha256:" + "a" * 64,
        container_runtime="docker",
        expected_host_key_fingerprint="SHA256:" + "A" * 43,
    )
    monkeypatch.setattr(operations, "_remote_config", lambda: config)
    return config


def _deadline_job():
    return RemoteShellJobRef(
        job_id="job-one",
        attempt_id="attempt-one",
        submitted_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
        request_sha256="b" * 64,
        training_bundle_sha256="c" * 64,
        runtime_policy_sha256="e" * 64,
        config_identity_sha256="d" * 64,
        remote_job_path="/srv/nantai-jobs/job-one/attempt-one",
    )


def test_prepare_failure_closes_executor(
    tmp_path,
    monkeypatch,
):
    """prepare() raising must still close executor (addendum #1)."""
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )

    close_calls = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured

        def prepare(self, measured):
            raise RemoteShellExecutionError("prepare failed")

        def close(self):
            close_calls["n"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"
    assert "remote executor preflight failed" in execution.reason
    assert close_calls["n"] == 1


def test_submit_failure_closes_executor(
    tmp_path,
    monkeypatch,
):
    """submit() raising must still close executor exactly once."""
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )

    close_calls = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            raise RemoteShellExecutionError("submit failed")

        def close(self):
            close_calls["n"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert "remote submission state is unknown" in execution.reason
    assert close_calls["n"] == 1


def test_restore_failure_closes_executor(
    tmp_path,
    monkeypatch,
):
    """restore() raising on existing job/lifecycle must close executor."""
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    publish_remote_shell_job_ref(job, stage_root / "remote-job.private.json")
    publish_remote_container_lifecycle_receipt(
        lifecycle,
        stage_root / "remote-container-lifecycle.private.json",
    )
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )

    close_calls = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured

        def prepare(self, measured):
            return measured

        def restore(self, measured, job_ref, expected_lifecycle=None):
            del measured, job_ref, expected_lifecycle
            raise RemoteShellExecutionError("restore mismatch")

        def close(self):
            close_calls["n"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"
    assert "remote recovery evidence is invalid" in execution.reason
    assert close_calls["n"] == 1


def test_fetch_result_bundle_error_closes_executor(
    tmp_path,
    monkeypatch,
):
    """fetch() raising RemoteResultBundleError returns blocked and closes."""
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )
    monkeypatch.setattr(
        operations,
        "_write_private_model",
        lambda *_args, **_kwargs: None,
    )

    close_calls = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            return job

        def poll(self, measured):
            del measured
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            del measured
            return lifecycle

        def fetch(self, measured, destination):
            del measured, destination
            raise RemoteResultBundleError("bundle invalid")

        def close(self):
            close_calls["n"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "blocked"
    assert "remote result failed validation" in execution.reason
    assert close_calls["n"] == 1


def test_fetch_transport_error_closes_executor(
    tmp_path,
    monkeypatch,
):
    """fetch() raising OSError returns unknown and closes executor."""
    operations, stage_root, prepared, config, job = _production_fixture(
        tmp_path,
        monkeypatch,
    )
    lifecycle = _remote_lifecycle(job)
    monkeypatch.setattr(
        operations_module.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        operations_module.time,
        "sleep",
        lambda _seconds: None,
    )
    monkeypatch.setattr(
        operations,
        "_write_private_model",
        lambda *_args, **_kwargs: None,
    )

    close_calls = {"n": 0}

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            return job

        def poll(self, measured):
            del measured
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            del measured
            return lifecycle

        def fetch(self, measured, destination):
            del measured, destination
            raise OSError("transport gone")

        def close(self):
            close_calls["n"] += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert "remote result closure is unknown" in execution.reason
    assert close_calls["n"] == 1


def test_remote_poll_deadline_crossed_in_lifecycle_binding_does_not_sleep_zero(
    tmp_path,
    monkeypatch,
):
    """Deadline crossed between >= check and remaining calc must not sleep(0).

    Second monotonic call (for remaining) crosses the deadline; the contract
    requires returning timeout/unknown without sleeping and without polling
    again.
    """
    operations, stage_root, prepared = _deadline_operations(tmp_path)
    _deadline_config(monkeypatch, operations)
    job = _deadline_job()
    monkeypatch.setattr(
        operations,
        "_build_training_bundle",
        lambda *_args, **_kwargs: prepared,
    )

    # deadline = 0.0 + 10.0 = 10.0
    # poll #1 returns unknown; bound_lifecycle raises.
    # >= check: 9.0 < 10.0 (not passed).
    # remaining calc: 11.0 > deadline → remaining = max(0, 10-11) = 0.
    # Current bug: calls sleep(0) and continues to poll #2.
    # Fix: remaining <= 0 must return timeout/unknown.
    monotonic_values = iter([0.0, 9.0, 11.0, 11.0, 11.0])
    sleeps: list[float] = []
    poll_count = {"n": 0}

    def fake_monotonic():
        return next(monotonic_values)

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(operations_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(operations_module.time, "sleep", fake_sleep)

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured
            self.close_calls = 0

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            return job

        def poll(self, measured):
            del measured
            poll_count["n"] += 1
            return ExecutorObservation(
                state="unknown",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
                exit_code=0,
            )

        def bound_lifecycle_receipt(self, measured):
            del measured
            raise RemoteShellExecutionError("not bound yet")

        def fetch(self, measured, destination):
            del measured, destination
            raise RemoteShellExecutionError("stop")

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert 0 not in sleeps
    assert poll_count["n"] == 1


def test_remote_poll_deadline_crossed_in_main_loop_does_not_sleep_zero(
    tmp_path,
    monkeypatch,
):
    """Main-loop sleep must not call sleep(0) when remaining drops to zero.

    After a running observation with lifecycle bound, the main loop computes
    remaining; if monotonic crossed the deadline between the >= check and
    the remaining calc, the implementation must return timeout/unknown
    instead of sleeping zero.
    """
    operations, stage_root, prepared = _deadline_operations(tmp_path)
    _deadline_config(monkeypatch, operations)
    job = _deadline_job()
    lifecycle = _remote_lifecycle(job)
    monkeypatch.setattr(
        operations,
        "_build_training_bundle",
        lambda *_args, **_kwargs: prepared,
    )

    # deadline = 0.0 + 10.0 = 10.0
    # poll #1: running; lifecycle bound; observation.state == "running".
    # main loop >= check: 9.0 < 10.0 (not passed).
    # remaining calc: 11.0 > deadline → remaining = 0.
    # Current bug: sleep(0) and continue.
    monotonic_values = iter([0.0, 9.0, 11.0, 11.0, 11.0])
    sleeps: list[float] = []
    poll_count = {"n": 0}

    def fake_monotonic():
        return next(monotonic_values)

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(operations_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(operations_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(
        operations,
        "_write_private_model",
        lambda *_args, **_kwargs: None,
    )

    class FakeRemoteExecutor:
        def __init__(self, measured):
            del measured
            self.close_calls = 0

        def prepare(self, measured):
            return measured

        def submit(self, measured):
            return job

        def poll(self, measured):
            del measured
            poll_count["n"] += 1
            return ExecutorObservation(
                state="running",
                observed_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
            )

        def bound_lifecycle_receipt(self, measured):
            del measured
            return lifecycle

        def fetch(self, measured, destination):
            del measured, destination
            raise RemoteShellExecutionError("stop")

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(
        operations_module,
        "RemoteShellExecutor",
        FakeRemoteExecutor,
    )

    execution = operations.execute("train-production", stage_root, ())

    assert execution.state == "unknown"
    assert 0 not in sleeps
    assert poll_count["n"] == 1
