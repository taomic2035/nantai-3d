from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import pipeline.training_executor as training_executor_module
from pipeline.durable_io import DurableIOError
from pipeline.training_executor import (
    ExecutorInputIdentity,
    ExecutorObservation,
    RealSceneJournal,
    TrainingExecutorError,
    advance_attempt,
    create_or_load_real_scene_journal,
    load_real_scene_journal,
    new_attempt,
    normalize_poll_result,
    resume_decision,
    retry_attempt,
    write_real_scene_journal,
)

_T0 = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)
_T2 = _T0 + timedelta(minutes=2)
_T3 = _T0 + timedelta(minutes=3)
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64


def _inputs(**updates) -> ExecutorInputIdentity:
    values = {
        "executor_kind": "remote-shell-nerfstudio",
        "request_sha256": _SHA_A,
        "dataset_receipt_sha256": _SHA_B,
        "training_config_sha256": _SHA_C,
        "trainer_name": "nerfstudio-splatfacto",
        "trainer_version": "1.1.5",
        "job_id": "job-001",
    }
    values.update(updates)
    return ExecutorInputIdentity(**values)


def _attempt():
    return new_attempt(
        _inputs(),
        attempt_id="attempt-001",
        created_at_utc=_T0,
        quality_role="production",
    )


def _observation(
    state: str,
    observed_at: datetime,
) -> ExecutorObservation:
    if state == "running":
        return ExecutorObservation(
            state="running",
            observed_at_utc=observed_at,
        )
    if state == "succeeded":
        return ExecutorObservation(
            state="succeeded",
            observed_at_utc=observed_at,
            exit_code=0,
            stdout_sha256=_SHA_A,
            stderr_sha256=_SHA_B,
            result_bundle_sha256=_SHA_D,
        )
    if state == "failed":
        return ExecutorObservation(
            state="failed",
            observed_at_utc=observed_at,
            exit_code=17,
            stdout_sha256=_SHA_A,
            stderr_sha256=_SHA_B,
        )
    if state == "unknown":
        return ExecutorObservation(
            state="unknown",
            observed_at_utc=observed_at,
        )
    raise AssertionError(state)


def test_lost_remote_job_is_unknown_not_failed():
    observation = normalize_poll_result(
        exit_code=None,
        reachable=False,
        observed_at_utc=_T1,
    )

    assert observation.state == "unknown"
    assert observation.exit_code is None


def test_exit_zero_is_not_success_until_result_is_locally_verified():
    unverified = normalize_poll_result(
        exit_code=0,
        reachable=True,
        observed_at_utc=_T1,
        outputs_verified=False,
    )
    verified = normalize_poll_result(
        exit_code=0,
        reachable=True,
        observed_at_utc=_T1,
        outputs_verified=True,
        stdout_sha256=_SHA_A,
        stderr_sha256=_SHA_B,
        result_bundle_sha256=_SHA_D,
    )

    assert unverified.state == "unknown"
    assert verified.state == "succeeded"


def test_known_nonzero_exit_is_failed_with_bounded_empty_log_hashes():
    observation = normalize_poll_result(
        exit_code=23,
        reachable=True,
        observed_at_utc=_T1,
    )

    assert observation.state == "failed"
    assert observation.exit_code == 23
    assert observation.stdout_sha256 == hashlib.sha256(b"").hexdigest()
    assert observation.stderr_sha256 == hashlib.sha256(b"").hexdigest()


def test_local_brush_attempt_cannot_claim_production_quality():
    inputs = ExecutorInputIdentity(
        executor_kind="local-brush",
        request_sha256=_SHA_A,
        dataset_receipt_sha256=_SHA_B,
        training_config_sha256=_SHA_C,
        trainer_name="brush",
        trainer_version="0.3.0",
        job_id="local-job-001",
    )

    with pytest.raises(ValidationError, match="preview-only"):
        new_attempt(
            inputs,
            attempt_id="attempt-local-001",
            created_at_utc=_T0,
            quality_role="production",
        )


def test_allowed_state_transitions_preserve_observation_history():
    initial = _attempt()
    running = advance_attempt(initial, _observation("running", _T1))
    unknown = advance_attempt(running, _observation("unknown", _T2))
    recovered = advance_attempt(unknown, _observation("running", _T3))
    succeeded = advance_attempt(
        recovered,
        _observation("succeeded", _T3 + timedelta(minutes=1)),
    )

    assert succeeded.state == "succeeded"
    assert tuple(item.state for item in succeeded.observations) == (
        "not-started",
        "running",
        "unknown",
        "running",
        "succeeded",
    )
    assert succeeded.result_bundle_sha256 == _SHA_D


@pytest.mark.parametrize("first_state", ["running", "unknown", "failed"])
def test_not_started_accepts_first_authoritative_observation(first_state):
    advanced = advance_attempt(
        _attempt(),
        _observation(first_state, _T1),
    )

    assert advanced.state == first_state
    assert tuple(item.state for item in advanced.observations) == (
        "not-started",
        first_state,
    )


def test_not_started_rejects_unverified_direct_success():
    with pytest.raises(TrainingExecutorError, match="transition"):
        advance_attempt(
            _attempt(),
            _observation("succeeded", _T1),
        )


def test_running_can_fail_but_failed_attempt_cannot_be_rewritten():
    running = advance_attempt(_attempt(), _observation("running", _T1))
    failed = advance_attempt(running, _observation("failed", _T2))

    assert failed.state == "failed"
    with pytest.raises(TrainingExecutorError, match="transition"):
        advance_attempt(failed, _observation("succeeded", _T3))


@pytest.mark.parametrize("terminal_state", ["succeeded", "failed"])
def test_unknown_attempt_can_recover_to_an_observed_terminal_state(
    terminal_state,
):
    running = advance_attempt(_attempt(), _observation("running", _T1))
    unknown = advance_attempt(running, _observation("unknown", _T2))
    terminal = advance_attempt(
        unknown,
        _observation(terminal_state, _T3),
    )

    assert terminal.state == terminal_state


def test_resume_requires_every_identity_to_match():
    running = advance_attempt(_attempt(), _observation("running", _T1))
    unknown = advance_attempt(running, _observation("unknown", _T2))

    assert resume_decision(running, _inputs()) == "reuse"
    assert resume_decision(unknown, _inputs()) == "block-unknown"
    failed = advance_attempt(running, _observation("failed", _T2))
    succeeded = advance_attempt(running, _observation("succeeded", _T2))
    assert resume_decision(failed, _inputs()) == "retry"
    assert resume_decision(succeeded, _inputs()) == "reuse"
    assert (
        resume_decision(
            running,
            _inputs(training_config_sha256="f" * 64),
        )
        == "retry"
    )
    assert (
        resume_decision(running, _inputs(job_id="different-job"))
        == "retry"
    )


def test_retry_creates_a_new_attempt_and_retains_failed_evidence():
    running = advance_attempt(_attempt(), _observation("running", _T1))
    failed = advance_attempt(running, _observation("failed", _T2))

    retried = retry_attempt(
        failed,
        _inputs(),
        attempt_id="attempt-002",
        created_at_utc=_T3,
    )

    assert retried.attempt_id == "attempt-002"
    assert retried.state == "not-started"
    assert failed.state == "failed"
    with pytest.raises(TrainingExecutorError, match="new attempt_id"):
        retry_attempt(
            failed,
            _inputs(),
            attempt_id=failed.attempt_id,
            created_at_utc=_T3,
        )


def test_receipt_schema_has_no_secret_or_private_host_fields():
    attempt = _attempt()
    with pytest.raises(ValidationError, match="private_hostname"):
        attempt.__class__(
            **attempt.model_dump(),
            private_hostname="gpu.internal.example",
        )
    with pytest.raises(ValidationError, match="raw_stdout"):
        attempt.__class__(
            **attempt.model_dump(),
            raw_stdout="secret token",
        )


def test_journal_create_update_and_load_are_canonical(tmp_path):
    path = tmp_path / "real-scene-journal.json"
    initial_attempt = _attempt()
    initial = RealSceneJournal(
        run_id="real-scene-canary-001",
        created_at_utc=_T0,
        attempts=(initial_attempt,),
    )

    assert create_or_load_real_scene_journal(path, initial) == initial
    assert create_or_load_real_scene_journal(path, initial) == initial
    assert path.read_bytes().endswith(b"\n")
    assert b"\r\n" not in path.read_bytes()

    running = advance_attempt(
        initial_attempt,
        _observation("running", _T1),
    )
    updated = initial.model_copy(update={"attempts": (running,)})
    write_real_scene_journal(path, previous=initial, updated=updated)

    assert load_real_scene_journal(path) == updated


def _stat_with_reparse(observed):
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_file_attributes=getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ),
    )


def _journal_fixture(path):
    journal = RealSceneJournal(
        run_id="real-scene-canary-001",
        created_at_utc=_T0,
        attempts=(_attempt(),),
    )
    create_or_load_real_scene_journal(path, journal)
    return journal


def test_journal_read_rejects_path_reparse_point(
    tmp_path,
    monkeypatch,
):
    path = (tmp_path / "real-scene-journal.json").absolute()
    _journal_fixture(path)
    original_lstat = Path.lstat

    def reparse_lstat(candidate):
        observed = original_lstat(candidate)
        return (
            _stat_with_reparse(observed)
            if candidate.absolute() == path
            else observed
        )

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    with pytest.raises(
        TrainingExecutorError,
        match="real-scene journal is missing or link-like",
    ):
        load_real_scene_journal(path)


def test_journal_read_rejects_descriptor_reparse_drift(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "real-scene-journal.json"
    _journal_fixture(path)
    original_fstat = os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        calls += 1
        observed = original_fstat(descriptor)
        return _stat_with_reparse(observed) if calls == 2 else observed

    monkeypatch.setattr(
        training_executor_module.os,
        "fstat",
        drifting_fstat,
    )

    with pytest.raises(
        TrainingExecutorError,
        match="real-scene journal changed while being read",
    ):
        load_real_scene_journal(path)

    assert calls == 2


def test_journal_read_open_error_hides_private_details(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "real-scene-journal.json"
    _journal_fixture(path)
    private_detail = r"D:\private-capture\secret-token"

    def fail_open(*_args, **_kwargs):
        raise OSError(private_detail)

    monkeypatch.setattr(
        training_executor_module.os,
        "open",
        fail_open,
    )

    with pytest.raises(TrainingExecutorError) as captured:
        load_real_scene_journal(path)

    assert str(captured.value) == "real-scene journal cannot be read"
    assert private_detail not in str(captured.value)


def test_journal_refuses_mismatch_corruption_and_stale_previous(tmp_path):
    path = tmp_path / "real-scene-journal.json"
    initial_attempt = _attempt()
    initial = RealSceneJournal(
        run_id="real-scene-canary-001",
        created_at_utc=_T0,
        attempts=(initial_attempt,),
    )
    create_or_load_real_scene_journal(path, initial)

    mismatch = initial.model_copy(update={"run_id": "other-run"})
    with pytest.raises(TrainingExecutorError, match="matching"):
        create_or_load_real_scene_journal(path, mismatch)

    running = advance_attempt(
        initial_attempt,
        _observation("running", _T1),
    )
    updated = initial.model_copy(update={"attempts": (running,)})
    write_real_scene_journal(path, previous=initial, updated=updated)
    with pytest.raises(TrainingExecutorError, match="stale"):
        write_real_scene_journal(
            path,
            previous=initial,
            updated=updated,
        )

    path.write_bytes(b'{"broken":true}\n')
    before = path.read_bytes()
    with pytest.raises(TrainingExecutorError, match="journal"):
        write_real_scene_journal(
            path,
            previous=updated,
            updated=updated,
        )
    assert path.read_bytes() == before


def test_journal_revalidates_updated_model_before_replace(tmp_path):
    path = tmp_path / "real-scene-journal.json"
    initial_attempt = _attempt()
    initial = RealSceneJournal(
        run_id="real-scene-canary-001",
        created_at_utc=_T0,
        attempts=(initial_attempt,),
    )
    create_or_load_real_scene_journal(path, initial)
    before = path.read_bytes()
    running = advance_attempt(
        initial_attempt,
        _observation("running", _T1),
    )
    corrupt_running = running.model_copy(update={"state": "succeeded"})
    corrupt_updated = initial.model_copy(
        update={"attempts": (corrupt_running,)},
    )

    with pytest.raises(TrainingExecutorError, match="updated journal"):
        write_real_scene_journal(
            path,
            previous=initial,
            updated=corrupt_updated,
        )

    assert path.read_bytes() == before


def test_journal_blocks_duplicate_retry_while_attempt_is_unknown(tmp_path):
    path = tmp_path / "real-scene-journal.json"
    initial_attempt = _attempt()
    running = advance_attempt(
        initial_attempt,
        _observation("running", _T1),
    )
    unknown = advance_attempt(running, _observation("unknown", _T2))
    previous = RealSceneJournal(
        run_id="real-scene-canary-001",
        created_at_utc=_T0,
        attempts=(unknown,),
    )
    create_or_load_real_scene_journal(path, previous)
    duplicate = new_attempt(
        _inputs(),
        attempt_id="attempt-002",
        created_at_utc=_T3,
        quality_role="production",
    )
    updated = previous.model_copy(
        update={"attempts": (*previous.attempts, duplicate)},
    )

    with pytest.raises(TrainingExecutorError, match="append-only"):
        write_real_scene_journal(
            path,
            previous=previous,
            updated=updated,
        )


def test_atomic_replace_failure_preserves_previous_journal(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "real-scene-journal.json"
    initial_attempt = _attempt()
    initial = RealSceneJournal(
        run_id="real-scene-canary-001",
        created_at_utc=_T0,
        attempts=(initial_attempt,),
    )
    create_or_load_real_scene_journal(path, initial)
    before = path.read_bytes()
    running = advance_attempt(
        initial_attempt,
        _observation("running", _T1),
    )
    updated = initial.model_copy(update={"attempts": (running,)})

    def fail_replace(source, destination):
        del source, destination
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "pipeline.durable_io.atomic_replace",
        fail_replace,
    )
    with pytest.raises(TrainingExecutorError, match="atomic write"):
        write_real_scene_journal(
            path,
            previous=initial,
            updated=updated,
        )

    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_stale_journal_candidate_never_overrides_valid_journal(tmp_path):
    path = tmp_path / "real-scene-journal.json"
    initial_attempt = _attempt()
    initial = RealSceneJournal(
        run_id="real-scene-canary-001",
        created_at_utc=_T0,
        attempts=(initial_attempt,),
    )
    create_or_load_real_scene_journal(path, initial)
    before = path.read_bytes()
    stale = tmp_path / ".real-scene-journal.json.stale.tmp"
    stale.write_bytes(b'{"untrusted":true}\n')

    loaded = create_or_load_real_scene_journal(path, initial)

    assert loaded == initial
    assert path.read_bytes() == before
    assert stale.read_bytes() == b'{"untrusted":true}\n'


def test_journal_reports_published_when_directory_sync_is_unconfirmed(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "real-scene-journal.json"
    initial_attempt = _attempt()
    initial = RealSceneJournal(
        run_id="real-scene-canary-001",
        created_at_utc=_T0,
        attempts=(initial_attempt,),
    )
    create_or_load_real_scene_journal(path, initial)
    running = advance_attempt(
        initial_attempt,
        _observation("running", _T1),
    )
    updated = initial.model_copy(update={"attempts": (running,)})

    def publish_then_fail(source, destination):
        source.replace(destination)
        raise DurableIOError(
            "simulated directory sync failure",
            published=True,
        )

    monkeypatch.setattr(
        "pipeline.durable_io.atomic_replace",
        publish_then_fail,
    )

    with pytest.raises(
        TrainingExecutorError,
        match="published but durability is unconfirmed",
    ):
        write_real_scene_journal(
            path,
            previous=initial,
            updated=updated,
        )

    assert load_real_scene_journal(path) == updated
