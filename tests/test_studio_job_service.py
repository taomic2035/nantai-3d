"""End-to-end tests for the fenced ingest-only JobService."""

from __future__ import annotations

import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from pipeline.studio_jobs import (
    JobContractError,
    JobService,
    ProcessController,
    ProcessResult,
    WriterBusyError,
)

VALID_PARAMS = {
    "fps": 2,
    "max_frames": 300,
    "blur_threshold": 0,
    "max_long_edge": 2560,
}


class PortableTestDurability:
    """Filesystem adapter for service logic; real NTFS evidence has separate tests."""

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


def _project(tmp_path: Path, *, old_target: bool = False) -> Path:
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "input/photo.jpg").write_bytes(b"new-photo")
    if old_target:
        (root / "photos").mkdir()
        (root / "photos/old.jpg").write_bytes(b"old-photo")
    return root


def _service(root: Path, *, controller=None) -> JobService:
    service = JobService(
        root,
        durability=PortableTestDurability(),
        process_controller=controller or ProcessController(),
    )
    service.initialize()
    return service


def test_submit_runs_real_ingest_through_verified_publication(tmp_path):
    root = _project(tmp_path, old_target=True)
    service = _service(root)

    submitted = service.submit(
        command="ingest",
        parameters=VALID_PARAMS,
        request_id="request-real-001",
    )
    run = service.wait(submitted.run.id, timeout=30)

    assert submitted.created is True
    assert run.status == "succeeded"
    assert run.artifact_ids[0].startswith("ingest-")
    assert (root / "photos/photo.jpg").read_bytes() == b"new-photo"
    assert not (root / "photos/old.jpg").exists()
    assert [event.phase for event in service.ledger.list_events()][-3:] == [
        "validating", "publishing", "publishing",
    ]


class ExitSevenController:
    def run(self, invocation, *, log_dir, **_kwargs):
        log_path = Path(log_dir) / "process.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("failed safely", encoding="utf-8")
        return ProcessResult(321, "test-process", 7, log_path)


def test_child_failure_is_terminal_with_stable_public_error(tmp_path):
    root = _project(tmp_path, old_target=True)
    service = _service(root, controller=ExitSevenController())

    run = service.wait(service.submit(
        command="ingest", parameters=VALID_PARAMS, request_id="request-fail-001",
    ).run.id)

    assert run.status == "failed"
    assert run.error_code == "process_failed"
    assert "exit code 7" in run.error_message
    assert (root / "photos/old.jpg").read_bytes() == b"old-photo"


class EmptySuccessController:
    def run(self, invocation, *, log_dir, on_spawn=None, **_kwargs):
        if on_spawn:
            on_spawn(322, "test-process")
        log_path = Path(log_dir) / "process.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()
        return ProcessResult(322, "test-process", 0, log_path)


def test_missing_staging_artifact_fails_validation_without_touching_formal(tmp_path):
    root = _project(tmp_path, old_target=True)
    service = _service(root, controller=EmptySuccessController())

    run = service.wait(service.submit(
        command="ingest", parameters=VALID_PARAMS, request_id="request-empty-001",
    ).run.id)

    assert run.status == "failed"
    assert run.error_code == "validation_failed"
    assert (root / "photos/old.jpg").read_bytes() == b"old-photo"


class MutatingController:
    def __init__(self):
        self.real = ProcessController()

    def run(self, invocation, **kwargs):
        result = self.real.run(invocation, **kwargs)
        (invocation.input_dir / "photo.jpg").write_bytes(b"changed-after-ingest")
        return result


def test_concurrent_input_change_fails_closed_and_preserves_old_target(tmp_path):
    root = _project(tmp_path, old_target=True)
    service = _service(root, controller=MutatingController())

    run = service.wait(service.submit(
        command="ingest", parameters=VALID_PARAMS, request_id="request-race-001",
    ).run.id)

    assert run.status == "failed"
    assert run.error_code in {"validation_failed", "concurrent_change"}
    assert (root / "photos/old.jpg").read_bytes() == b"old-photo"


def test_request_idempotency_returns_original_terminal_run(tmp_path):
    root = _project(tmp_path)
    service = _service(root)
    first = service.submit(
        command="ingest", parameters=VALID_PARAMS, request_id="request-dedup-001",
    )
    service.wait(first.run.id, timeout=30)

    repeated = service.submit(
        command="ingest", parameters=VALID_PARAMS, request_id="request-dedup-001",
    )

    assert repeated.created is False
    assert repeated.run.id == first.run.id


class BlockingController:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self, invocation, *, log_dir, **_kwargs):
        self.entered.set()
        assert self.release.wait(10)
        return EmptySuccessController().run(invocation, log_dir=log_dir)


def test_second_writer_is_rejected_without_creating_a_run(tmp_path):
    root = _project(tmp_path)
    controller = BlockingController()
    service = _service(root, controller=controller)
    first = service.submit(
        command="ingest", parameters=VALID_PARAMS, request_id="request-one-001",
    )
    assert controller.entered.wait(10)

    with pytest.raises(WriterBusyError):
        service.submit(
            command="ingest", parameters=VALID_PARAMS, request_id="request-two-001",
        )

    controller.release.set()
    service.wait(first.run.id)
    with service.ledger.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_active_worker_renews_its_durable_lease(tmp_path):
    root = _project(tmp_path)
    controller = BlockingController()
    service = JobService(
        root,
        durability=PortableTestDurability(),
        process_controller=controller,
        heartbeat_interval=0.02,
    )
    service.initialize()
    submitted = service.submit(
        command="ingest",
        parameters=VALID_PARAMS,
        request_id="request-heartbeat-001",
    )
    assert controller.entered.wait(10)
    initial = datetime.fromisoformat(submitted.run.lease_expires_utc)
    refreshed = initial
    deadline = time.monotonic() + 2
    while refreshed <= initial and time.monotonic() < deadline:
        time.sleep(0.03)
        refreshed = datetime.fromisoformat(
            service.ledger.get_run(submitted.run.id).lease_expires_utc,
        )

    controller.release.set()
    service.wait(submitted.run.id)
    assert refreshed > initial


def test_initialize_rejects_linked_state_root_before_creating_database(tmp_path):
    root = _project(tmp_path)
    outside = tmp_path / "outside-state"
    outside.mkdir()
    try:
        os.symlink(outside, root / ".nantai-studio", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    with pytest.raises(JobContractError, match="state root|symlink|junction"):
        JobService(root, durability=PortableTestDurability()).initialize()

    assert not (outside / "studio.db").exists()
