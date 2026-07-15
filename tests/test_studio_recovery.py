"""Startup orphan handling and recovery fencing tests."""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pipeline import studio_jobs
from pipeline.ingest import ingest_all
from pipeline.studio_jobs import (
    CommandRegistry,
    JobService,
    ProjectFileLock,
    build_concurrency_snapshot,
)

VALID_PARAMS = {
    "fps": 2,
    "max_frames": 300,
    "blur_threshold": 0,
    "max_long_edge": 2560,
}


class PortableTestDurability:
    @staticmethod
    def flush_file(_path: Path) -> None:
        return None

    @staticmethod
    def flush_directory(_path: Path) -> None:
        return None

    @staticmethod
    def move(source: Path, destination: Path) -> None:
        source.rename(destination)

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(path)


class FailSecondMoveDurability(PortableTestDurability):
    def __init__(self):
        self.moves = 0

    def move(self, source: Path, destination: Path) -> None:
        self.moves += 1
        if self.moves == 2:
            raise OSError("injected stage publication failure")
        super().move(source, destination)


NOW = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "input/photo.jpg").write_bytes(b"recovery-photo")
    return root


def _service(root: Path) -> JobService:
    service = JobService(root, durability=PortableTestDurability())
    service.initialize()
    return service


def _create_run(service: JobService, *, phase=None):
    snapshot = build_concurrency_snapshot(service.project_root)
    params = CommandRegistry(service.project_root).parse("ingest", VALID_PARAMS)
    run = service.ledger.create_run(
        run_id="run-recovery-001",
        request_id="request-recovery-001",
        command="ingest",
        command_schema_version=1,
        parameters=params.model_dump(mode="json"),
        snapshot=snapshot.as_dict(),
        owner="dead-owner",
        lease_generation=1,
        lease_expires_utc=NOW + timedelta(seconds=30),
        staging_path=".nantai-studio/work/run-recovery-001/photos",
        created_utc=NOW,
    ).run
    (service.project_root / ".nantai-studio/work/run-recovery-001").mkdir()
    if phase is not None:
        service.ledger.transition_run(
            run.id,
            status="running",
            phase="executing",
            owner=run.owner,
            lease_generation=run.lease_generation,
            message="Executing.",
            occurred_utc=NOW,
        )
        if phase == "validating":
            service.ledger.transition_run(
                run.id,
                status="running",
                phase="validating",
                owner=run.owner,
                lease_generation=run.lease_generation,
                message="Validating.",
                occurred_utc=NOW,
            )
    return service.ledger.get_run(run.id), snapshot, params


def test_startup_stays_read_only_while_writer_lock_is_owned(tmp_path):
    root = _root(tmp_path)
    service = _service(root)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer:
        result = service.recover_startup()

    assert result.ready is False
    assert "writer lock" in result.reason


def test_stale_queued_run_is_fenced_and_retired(tmp_path):
    service = _service(_root(tmp_path))
    run, _, _ = _create_run(service)
    (service.project_root / "input/photo.jpg").write_bytes(b"changed")

    result = service.recover_startup()

    recovered = service.ledger.get_run(run.id)
    assert result.ready is True
    assert recovered.status == "failed"
    assert recovered.error_code == "stale_job"
    assert recovered.lease_generation == 2


def test_dead_executing_orphan_is_interrupted_and_workspace_quarantined(tmp_path):
    service = _service(_root(tmp_path))
    run, _, _ = _create_run(service, phase="executing")
    workspace = service.project_root / ".nantai-studio/work" / run.id
    (workspace / "partial.bin").write_bytes(b"partial")

    result = service.recover_startup()

    recovered = service.ledger.get_run(run.id)
    assert result.ready is True
    assert recovered.error_code == "interrupted"
    assert not workspace.exists()
    assert list((service.project_root / ".nantai-studio/quarantine").glob(
        f"{run.id}-*/partial.bin",
    ))


def test_live_orphan_child_forces_observer_only_without_takeover(tmp_path):
    service = _service(_root(tmp_path))
    run, _, _ = _create_run(service, phase="executing")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = studio_jobs._process_start_identity(
            child.pid, handle=getattr(child, "_handle", None),
        )
        service.ledger.record_child_process(
            run.id,
            pid=child.pid,
            start_identity=identity,
            owner=run.owner,
            lease_generation=run.lease_generation,
            occurred_utc=NOW,
        )

        result = service.recover_startup()

        unchanged = service.ledger.get_run(run.id)
        assert result.ready is False
        assert result.observer_only is True
        assert unchanged.owner == "dead-owner"
        assert unchanged.lease_generation == 1
        assert (service.project_root / ".nantai-studio/work" / run.id).exists()
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_validating_orphan_reverifies_and_publishes(tmp_path):
    service = _service(_root(tmp_path))
    run, _, params = _create_run(service, phase="validating")
    invocation = service.registry.build_invocation(run.id, params)
    ingest_all(invocation.input_dir, invocation.stage_dir, blur_threshold=0)

    result = service.recover_startup()
    recovered = service.wait(run.id, timeout=30)

    assert result.ready is True
    assert recovered.status == "succeeded"
    assert (service.project_root / "photos/photo.jpg").read_bytes() == b"recovery-photo"


def test_validating_recovery_publish_failure_rolls_back_before_terminal(tmp_path):
    root = _root(tmp_path)
    (root / "photos").mkdir()
    (root / "photos/old.jpg").write_bytes(b"old-formal")
    service = JobService(root, durability=FailSecondMoveDurability())
    service.initialize()
    run, _, params = _create_run(service, phase="validating")
    invocation = service.registry.build_invocation(run.id, params)
    ingest_all(invocation.input_dir, invocation.stage_dir, blur_threshold=0)

    result = service.recover_startup()
    recovered = service.wait(run.id, timeout=30)

    assert result.ready is True
    assert recovered.status == "failed"
    assert recovered.error_code == "publish_failed"
    assert (root / "photos/old.jpg").read_bytes() == b"old-formal"
    assert service.ledger.list_publications()[0].status == "rolled_back"
