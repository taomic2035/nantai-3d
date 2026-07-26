from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from pipeline.real_dataset import HfDatasetSource
from pipeline.real_scene_operations import RealScenePipelineOperations
from pipeline.real_scene_runner import (
    RealSceneRunner,
    RealSceneRunOptions,
    RealSceneSourceIdentity,
)
from pipeline.training_executor import ExecutorJobRef, ExecutorObservation


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


def test_hf_fetch_receipt_binds_downloaded_payload_bytes(
    tmp_path,
    monkeypatch,
):
    def fake_fetch(source, stage_root):
        del source
        (stage_root / "dataset/poster/images").mkdir(parents=True)
        (stage_root / "dataset/poster/images/frame.png").write_bytes(
            b"image"
        )
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
        output.path.endswith("dataset/poster/images/frame.png")
        for output in receipt.outputs
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
    assert "registration-quality-report.json" in receipt_path.read_text(
        encoding="ascii"
    )


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
            (self.config.execution_root / "trained.ply").write_bytes(
                b"ply"
            )
            return SimpleNamespace(
                receipt=SimpleNamespace(quality_role="preview-only")
            )

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
    stage_root = (
        tmp_path / "workspace/stages/train-production/attempt-one"
    )
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
    job = ExecutorJobRef(
        executor_kind="remote-shell-nerfstudio",
        job_id="job-one",
        attempt_id="attempt-one",
        submitted_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
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
    assert any(
        path.name == "remote-job.private.json"
        for path in execution.evidence_artifacts
    )


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
    training_root = (
        workspace / "stages/train-preview/attempt-train"
    )
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
