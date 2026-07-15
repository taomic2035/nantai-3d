"""External-process crash fixture for Studio publication and orphan recovery."""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from pipeline.studio_jobs import (  # noqa: E402
    ArtifactPromoter,
    CommandRegistry,
    ConcurrencySnapshot,
    JobInvocation,
    JobService,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
)
from pipeline.studio_ledger import StudioLedger  # noqa: E402


def _emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True), flush=True)


def publish_until_killed(root: Path, crash_point: str) -> int:
    ledger = StudioLedger(root / ".nantai-studio/studio.db")
    run = ledger.get_run("run-001")
    snapshot = ConcurrencySnapshot.from_dict(run.snapshot)
    registry = CommandRegistry(root)
    parameters = registry.parse(run.command, run.parameters)
    invocation = registry.build_invocation(run.id, parameters)
    durability = WindowsNtfsDurabilityBackend(root)
    readiness = durability.self_test()
    if not readiness.ready:
        _emit({"error": readiness.reason})
        return 3

    def stop_at(point: str) -> None:
        if point != crash_point:
            return
        _emit({"fault": point, "pid": os_pid()})
        threading.Event().wait()

    promoter = ArtifactPromoter(
        root,
        ledger=ledger,
        durability=durability,
        fault_injector=stop_at,
    )
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    with writer:
        promoter.publish(
            publication_id="publication-001",
            run_id=run.id,
            owner=run.owner,
            lease_generation=run.lease_generation,
            expected_snapshot=snapshot,
            invocation=invocation,
            occurred_utc=datetime.now(UTC),
        )
    _emit({"error": f"fault point was not reached: {crash_point}"})
    return 4


def os_pid() -> int:
    import os

    return os.getpid()


def recover(root: Path) -> int:
    service = JobService(root)
    service.initialize()
    readiness = service.durability.self_test()
    if not readiness.ready:
        _emit({"error": readiness.reason})
        return 3
    result = service.recover_startup()
    runs = service.ledger.list_runs()
    publications = service.ledger.list_publications()
    _emit({
        "ready": result.ready,
        "observer_only": result.observer_only,
        "reason": result.reason,
        "runs": [
            {
                "id": run.id,
                "status": run.status,
                "phase": run.phase,
                "error_code": run.error_code,
                "child_pid": run.child_pid,
                "child_start_identity": run.child_start_identity,
            }
            for run in runs
        ],
        "publications": [item.status for item in publications],
    })
    return 0


class SlowChildRegistry(CommandRegistry):
    def build_invocation(self, run_id, parameters) -> JobInvocation:
        base = super().build_invocation(run_id, parameters)
        return replace(
            base,
            argv=(
                sys.executable,
                str(Path(__file__).resolve()),
                "slow-child",
                str(base.stage_dir.parent),
            ),
        )


def parent_worker(root: Path) -> int:
    service = JobService(
        root,
        registry=SlowChildRegistry(root),
        heartbeat_interval=0.05,
    )
    service.initialize()
    created = service.submit(
        command="ingest",
        parameters={
            "fps": 2,
            "max_frames": 300,
            "blur_threshold": 0,
            "max_long_edge": 2560,
        },
        request_id="request-parent-crash-001",
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = service.ledger.get_run(created.run.id)
        if run.child_pid is not None and run.child_start_identity:
            _emit({
                "run_id": run.id,
                "child_pid": run.child_pid,
                "child_start_identity": run.child_start_identity,
                "parent_pid": os_pid(),
            })
            threading.Event().wait()
        time.sleep(0.02)
    _emit({"error": "slow child identity was not persisted"})
    return 5


def slow_child(workspace: Path) -> int:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "child-started.txt").write_text("started", encoding="utf-8")
    release = workspace / "release-child"
    while not release.exists():
        time.sleep(0.02)
    (workspace / "child-finished.txt").write_text("finished", encoding="utf-8")
    return 0


def main() -> int:
    mode = sys.argv[1]
    if mode == "publish":
        return publish_until_killed(Path(sys.argv[2]), sys.argv[3])
    if mode == "recover":
        return recover(Path(sys.argv[2]))
    if mode == "parent-worker":
        return parent_worker(Path(sys.argv[2]))
    if mode == "slow-child":
        return slow_child(Path(sys.argv[2]))
    raise ValueError(mode)


if __name__ == "__main__":
    raise SystemExit(main())
