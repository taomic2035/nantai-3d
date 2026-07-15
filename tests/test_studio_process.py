"""Real child-process tests for bounded Studio execution and logs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pipeline.studio_jobs import (
    JobInvocation,
    ProcessController,
    ProcessExecutionError,
    is_same_process_alive,
)

HELPER = Path(__file__).parent / "helpers/studio_process_fixture.py"


def _invocation(tmp_path: Path, mode: str, *arguments: str) -> JobInvocation:
    return JobInvocation(
        argv=(sys.executable, str(HELPER), mode, *arguments),
        cwd=Path(__file__).parents[1],
        environment={
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}
        },
        input_dir=tmp_path / "input",
        stage_dir=tmp_path / "stage",
        target_dir=tmp_path / "target",
    )


def test_process_controller_drains_both_streams_and_records_identity(tmp_path):
    events = []
    spawned = []
    controller = ProcessController(event_line_limit=4_096)

    result = controller.run(
        _invocation(tmp_path, "success"),
        log_dir=tmp_path / "logs",
        on_event=events.append,
        on_spawn=lambda pid, identity: spawned.append((pid, identity)),
    )

    assert result.exit_code == 0
    assert spawned == [(result.pid, result.start_identity)]
    assert result.start_identity
    assert any(
        event.stream == "stdout" and "hello from stdout" in event.message
        for event in events
    )
    assert any(
        event.stream == "stderr" and "hello from stderr" in event.message
        for event in events
    )
    assert is_same_process_alive(result.pid, result.start_identity) is False


def test_nonzero_exit_is_returned_without_being_relabelled_success(tmp_path):
    result = ProcessController().run(
        _invocation(tmp_path, "failure"),
        log_dir=tmp_path / "logs",
    )
    assert result.exit_code == 7


def test_large_simultaneous_output_does_not_deadlock(tmp_path):
    events = []
    result = ProcessController(event_line_limit=512).run(
        _invocation(tmp_path, "flood"),
        log_dir=tmp_path / "logs",
        on_event=events.append,
    )
    assert result.exit_code == 0
    assert any(event.stream == "stdout" for event in events)
    assert any(event.stream == "stderr" for event in events)


def test_invalid_utf8_is_replaced_in_events_and_logs(tmp_path):
    events = []
    result = ProcessController().run(
        _invocation(tmp_path, "invalid-utf8"),
        log_dir=tmp_path / "logs",
        on_event=events.append,
    )
    payload = result.log_path.read_text(encoding="utf-8")
    assert "before-�-after" in payload
    assert any("before-�-after" in event.message for event in events)


def test_long_line_is_one_truncated_event_and_secret_is_redacted(tmp_path):
    secret = "test-secret-value"
    events = []
    result = ProcessController(event_line_limit=128).run(
        _invocation(tmp_path, "long-secret", secret),
        log_dir=tmp_path / "logs",
        redactions=(secret,),
        on_event=events.append,
    )

    stdout_events = [event for event in events if event.stream == "stdout"]
    assert len(stdout_events) == 1
    assert stdout_events[0].truncated is True
    assert len(stdout_events[0].message) < 180
    assert secret not in stdout_events[0].message
    assert secret not in result.log_path.read_text(encoding="utf-8")
    assert "[REDACTED]" in result.log_path.read_text(encoding="utf-8")


def test_secret_split_across_read_chunks_is_never_logged(tmp_path):
    secret = "boundary-secret-value"
    result = ProcessController().run(
        _invocation(tmp_path, "split-secret", secret),
        log_dir=tmp_path / "logs",
        redactions=(secret,),
    )
    combined_logs = "".join(
        path.read_text(encoding="utf-8")
        for path in sorted(result.log_path.parent.glob("process.log*"))
    )
    assert secret not in combined_logs
    assert "[REDACTED]" in combined_logs


def test_logs_rotate_at_a_bounded_size(tmp_path):
    controller = ProcessController(log_rotate_bytes=4_096, log_backups=3)
    result = controller.run(
        _invocation(tmp_path, "flood"),
        log_dir=tmp_path / "logs",
    )

    logs = sorted((tmp_path / "logs").glob("process.log*"))
    assert result.log_path in logs
    assert 2 <= len(logs) <= 4
    assert all(path.stat().st_size <= 4_096 for path in logs)


def test_process_controller_always_passes_shell_false(tmp_path, monkeypatch):
    import subprocess

    real_popen = subprocess.Popen
    observed = []

    def checked_popen(*args, **kwargs):
        observed.append(kwargs.get("shell"))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", checked_popen)
    ProcessController().run(
        _invocation(tmp_path, "success"),
        log_dir=tmp_path / "logs",
    )
    assert observed == [False]


def test_event_callback_failure_still_drains_the_child(tmp_path):
    def broken_callback(_event):
        raise RuntimeError("observer failed")

    with pytest.raises(ProcessExecutionError, match="drained"):
        ProcessController().run(
            _invocation(tmp_path, "flood"),
            log_dir=tmp_path / "logs",
            on_event=broken_callback,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX process identity uses /proc")
def test_wrong_start_identity_does_not_match_a_live_pid():
    assert is_same_process_alive(os.getpid(), "wrong-identity") is False
