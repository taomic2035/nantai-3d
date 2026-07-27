from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

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
