from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pipeline.real_dataset import HfDatasetSource
from pipeline.real_scene_operations import RealScenePipelineOperations
from pipeline.real_scene_runner import (
    RealSceneRunner,
    RealSceneRunOptions,
    RealSceneSourceIdentity,
)


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
        return SimpleNamespace()

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
