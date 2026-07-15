"""Real-process tests for the Studio writer and publication locks."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pipeline.studio_jobs import LockOrderError, ProjectFileLock
from pipeline.studio_ledger import StudioLedger

HELPER = Path(__file__).parent / "helpers/studio_lock_fixture.py"


def _locked_child(path: Path) -> subprocess.Popen:
    child = subprocess.Popen(
        [sys.executable, str(HELPER), str(path), "writer"],
        cwd=Path(__file__).parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    assert child.stdout is not None
    assert child.stdout.readline().decode().strip() == "acquired"
    return child


def test_writer_lock_is_exclusive_across_real_processes(tmp_path):
    path = tmp_path / ".nantai-studio/writer.lock"
    child = _locked_child(path)
    contender = ProjectFileLock(path, role="writer")
    try:
        assert contender.acquire(blocking=False) is False
    finally:
        assert child.stdin is not None
        child.stdin.write(b"x")
        child.stdin.flush()
        child.wait(timeout=10)
    assert contender.acquire(blocking=False) is True
    contender.release()


def test_writer_lock_is_released_when_the_owner_process_is_killed(tmp_path):
    path = tmp_path / ".nantai-studio/writer.lock"
    child = _locked_child(path)
    child.kill()
    child.wait(timeout=10)

    recovered = ProjectFileLock(path, role="writer")
    assert recovered.acquire(blocking=False) is True
    recovered.release()


def test_publish_lock_requires_the_writer_lock_and_lifo_release(tmp_path):
    writer = ProjectFileLock(tmp_path / "writer.lock", role="writer")
    publish = ProjectFileLock(tmp_path / "publish.lock", role="publish")

    with pytest.raises(LockOrderError, match="writer"):
        publish.acquire(blocking=False)

    with writer:
        with publish:
            with pytest.raises(LockOrderError, match="order|release"):
                writer.release()


def test_file_lock_cannot_be_acquired_inside_a_sqlite_write_transaction(tmp_path):
    ledger = StudioLedger(tmp_path / ".nantai-studio/studio.db")
    ledger.initialize()
    lock = ProjectFileLock(tmp_path / ".nantai-studio/writer.lock", role="writer")

    with ledger._transaction():  # private by design: this is lock-discipline proof
        with pytest.raises(LockOrderError, match="SQLite"):
            lock.acquire(blocking=False)


def test_writer_lock_handle_can_transfer_to_the_worker_thread(tmp_path):
    path = tmp_path / ".nantai-studio/writer.lock"
    lock = ProjectFileLock(path, role="writer")
    assert lock.acquire(blocking=False) is True
    errors = []

    def worker_release():
        try:
            lock.release()
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    worker = threading.Thread(target=worker_release)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []
    contender = ProjectFileLock(path, role="writer")
    assert contender.acquire(blocking=False) is True
    contender.release()
