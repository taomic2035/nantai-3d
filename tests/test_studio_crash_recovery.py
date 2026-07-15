"""Real-process crash qualification for the B1 Studio job kernel."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pipeline.ingest import ingest_all
from pipeline.ingest_manifest import verify_ingest_artifact
from pipeline.studio_jobs import (
    ArtifactPromoter,
    CommandRegistry,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
    build_concurrency_snapshot,
    is_same_process_alive,
)
from pipeline.studio_ledger import StudioLedger

HELPER = Path(__file__).parent / "helpers/studio_crash_fixture.py"
PARAMETERS = {
    "fps": 2,
    "max_frames": 300,
    "blur_threshold": 0,
    "max_long_edge": 2560,
}
CRASH_POINTS = (
    "before_target_backup_intent",
    "after_target_backup_intent",
    "before_target_backup_move",
    "after_target_backup_move",
    "before_target_backup_flush",
    "after_target_backup_flush",
    "after_target_backup_done",
    "after_stage_target_intent",
    "before_stage_target_move",
    "after_stage_target_move",
    "before_stage_target_flush",
    "after_stage_target_flush",
    "after_stage_target_done",
    "before_commit",
    "after_commit",
)
PUBLICATION_TOPOLOGIES = ("initial", "successive")


def _create_publishable_run(
    root: Path,
    ledger: StudioLedger,
    *,
    run_id: str,
    request_id: str,
    owner: str,
    photo_bytes: bytes,
):
    (root / "input/photo.jpg").write_bytes(photo_bytes)
    snapshot = build_concurrency_snapshot(root)
    registry = CommandRegistry(root)
    parameters = registry.parse("ingest", PARAMETERS)
    invocation = registry.build_invocation(run_id, parameters)
    invocation.stage_dir.parent.mkdir(parents=True)
    ingest_all(invocation.input_dir, invocation.stage_dir, blur_threshold=0)

    now = datetime.now(UTC)
    ledger.create_run(
        run_id=run_id,
        request_id=request_id,
        command="ingest",
        command_schema_version=1,
        parameters=parameters.model_dump(mode="json"),
        snapshot=snapshot.as_dict(),
        owner=owner,
        lease_generation=1,
        lease_expires_utc=now + timedelta(minutes=5),
        staging_path=f".nantai-studio/work/{run_id}/photos",
        created_utc=now,
    )
    for status, phase in (
        ("running", "executing"),
        ("running", "validating"),
        ("running", "publishing"),
    ):
        ledger.transition_run(
            run_id,
            status=status,
            phase=phase,
            owner=owner,
            lease_generation=1,
            message=phase,
            occurred_utc=now,
        )
    return snapshot, invocation


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _setup_publishable_run(tmp_path: Path, topology: str):
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    ledger = StudioLedger(root / ".nantai-studio/studio.db")
    ledger.initialize()
    (root / ".nantai-studio/backups").mkdir(exist_ok=True)
    prior_tree = None

    if topology == "successive":
        old_snapshot, old_invocation = _create_publishable_run(
            root,
            ledger,
            run_id="run-000",
            request_id="request-crash-000",
            owner="owner-old",
            photo_bytes=b"old-photo",
        )
        writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
        with writer:
            ArtifactPromoter(
                root,
                ledger=ledger,
                durability=WindowsNtfsDurabilityBackend(root),
            ).publish(
                publication_id="publication-000",
                run_id="run-000",
                owner="owner-old",
                lease_generation=1,
                expected_snapshot=old_snapshot,
                invocation=old_invocation,
                occurred_utc=datetime.now(UTC),
            )
        prior_tree = _tree_bytes(root / "photos")

    _, invocation = _create_publishable_run(
        root,
        ledger,
        run_id="run-001",
        request_id="request-crash-001",
        owner="owner-crash",
        photo_bytes=b"new-photo",
    )
    return root, invocation, prior_tree


def _start_helper(*args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(HELPER), *args],
        cwd=Path(__file__).parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _read_json_line(process: subprocess.Popen, *, timeout: float = 30) -> dict:
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue(maxsize=1)
    reader = threading.Thread(
        target=lambda: lines.put(process.stdout.readline()),
        daemon=True,
    )
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        process.kill()
        process.wait(timeout=10)
        pytest.fail("crash fixture did not report its synchronization point")
    line = lines.get_nowait()
    if not line:
        assert process.stderr is not None
        stderr = process.stderr.read()
        pytest.fail(
            f"crash fixture exited before synchronization: "
            f"exit={process.poll()} stderr={stderr}",
        )
    return json.loads(line)


def _recover_in_fresh_process(root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(HELPER), "recover", str(root)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stderr
    return json.loads(lines[-1])


def _assert_locks_recovered(root: Path) -> None:
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    publish = ProjectFileLock(root / ".nantai-studio/publish.lock", role="publish")
    with writer:
        with publish:
            pass


@pytest.mark.skipif(os.name != "nt", reason="real crash qualification is Windows/NTFS")
@pytest.mark.parametrize("topology", PUBLICATION_TOPOLOGIES)
@pytest.mark.parametrize("crash_point", CRASH_POINTS)
def test_external_publisher_kill_converges_after_fresh_restart(
    tmp_path,
    crash_point,
    topology,
):
    root, invocation, prior_tree = _setup_publishable_run(tmp_path, topology)
    readiness = WindowsNtfsDurabilityBackend(root).self_test()
    assert readiness.ready, readiness.reason
    publisher = _start_helper("publish", str(root), crash_point)
    try:
        signal = _read_json_line(publisher)
        assert signal == {"fault": crash_point, "pid": publisher.pid}
        publisher.kill()
        publisher.wait(timeout=10)
        assert publisher.returncode != 0

        first = _recover_in_fresh_process(root)
        second = _recover_in_fresh_process(root)
    finally:
        if publisher.poll() is None:
            publisher.kill()
            publisher.wait(timeout=10)

    ledger = StudioLedger(root / ".nantai-studio/studio.db")
    run = ledger.get_run("run-001")
    publications = ledger.list_publications()
    expected_prefix = ["committed"] if topology == "successive" else []
    assert first["ready"] is True
    assert first["observer_only"] is False
    assert second["ready"] is True
    for key in ("ready", "observer_only", "runs", "publications"):
        assert second[key] == first[key]
    assert not list((root / ".nantai-studio/backups").iterdir())
    _assert_locks_recovered(root)

    if crash_point == "after_commit":
        assert run.status == "succeeded"
        assert [item.status for item in publications] == [
            *expected_prefix,
            "committed",
        ]
        assert {path.name for path in (root / "photos").iterdir()} == {
            "photo.jpg",
            "ingest_manifest.json",
        }
        verify_ingest_artifact(root / "photos", input_dir=root / "input")
        assert not invocation.stage_dir.exists()
    else:
        assert run.status == "failed"
        assert run.error_code == "publish_failed"
        assert [item.status for item in publications] == [
            *expected_prefix,
            "rolled_back",
        ]
        if topology == "successive":
            assert prior_tree is not None
            assert _tree_bytes(root / "photos") == prior_tree
        else:
            assert not (root / "photos").exists()
        verify_ingest_artifact(invocation.stage_dir, input_dir=root / "input")


def _wait_until(predicate, *, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def _terminate_exact_process(pid: int, identity: str) -> None:
    if not is_same_process_alive(pid, identity):
        return
    import win32api
    import win32con

    handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
    try:
        win32api.TerminateProcess(handle, 9)
    finally:
        handle.Close()


@pytest.mark.skipif(os.name != "nt", reason="real crash qualification is Windows/NTFS")
def test_parent_kill_preserves_live_child_then_quarantines_after_exit(tmp_path):
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "input/photo.jpg").write_bytes(b"slow-child-input")
    parent = _start_helper("parent-worker", str(root))
    child_pid = None
    child_identity = None
    try:
        signal = _read_json_line(parent)
        assert signal["parent_pid"] == parent.pid
        child_pid = signal["child_pid"]
        child_identity = signal["child_start_identity"]
        run_id = signal["run_id"]
        workspace = root / ".nantai-studio/work" / run_id
        _wait_until(lambda: (workspace / "child-started.txt").exists())

        parent.kill()
        parent.wait(timeout=10)
        assert parent.returncode != 0
        assert is_same_process_alive(child_pid, child_identity)

        observer = _recover_in_fresh_process(root)
        assert observer["ready"] is False
        assert observer["observer_only"] is True
        assert observer["runs"][0]["status"] == "running"
        assert observer["runs"][0]["phase"] == "executing"
        assert workspace.is_dir()
        assert not (workspace / "child-finished.txt").exists()

        (workspace / "release-child").write_text("release", encoding="utf-8")
        _wait_until(lambda: not is_same_process_alive(child_pid, child_identity))

        converged = _recover_in_fresh_process(root)
        repeated = _recover_in_fresh_process(root)
        assert converged["ready"] is True
        assert converged["observer_only"] is False
        assert converged["runs"][0]["status"] == "failed"
        assert converged["runs"][0]["error_code"] == "interrupted"
        assert repeated["ready"] is True
        assert not workspace.exists()
        quarantined = list(
            (root / ".nantai-studio/quarantine").glob(f"{run_id}-*/child-finished.txt"),
        )
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8") == "finished"
        _assert_locks_recovered(root)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        if child_pid is not None and child_identity is not None:
            _terminate_exact_process(child_pid, child_identity)
