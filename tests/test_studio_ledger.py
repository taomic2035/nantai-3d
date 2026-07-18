"""Durable ledger and state-machine tests for the Studio job kernel."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from pipeline import studio_ledger as ledger_module
from pipeline.studio_ledger import (
    ActiveRunConflictError,
    FencingError,
    LedgerSchemaError,
    RequestConflictError,
    StudioLedger,
    TransitionError,
    canonical_json,
)


def _now() -> datetime:
    return datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _ledger(tmp_path) -> StudioLedger:
    ledger = StudioLedger(tmp_path / ".nantai-studio/studio.db")
    ledger.initialize()
    return ledger


def _write_v1_database(path) -> StudioLedger:
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.executescript(ledger_module._SCHEMA_V1_SQL)
        fingerprint = ledger_module._schema_fingerprint(connection)
        connection.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            (
                ("schema_version", "1"),
                ("schema_fingerprint", fingerprint),
            ),
        )
    return StudioLedger(path)


def _create_run(
    ledger: StudioLedger,
    *,
    run_id: str = "run-001",
    request_id: str = "request-001",
    owner: str = "owner-a",
    generation: int = 1,
    parameters: dict | None = None,
):
    return ledger.create_run(
        run_id=run_id,
        request_id=request_id,
        command="ingest",
        command_schema_version=1,
        parameters=parameters or {
            "fps": 2,
            "max_frames": 300,
            "blur_threshold": 80,
            "max_long_edge": 2560,
        },
        snapshot={"input_digest": "a" * 64, "targets": {"photos": "absent"}},
        owner=owner,
        lease_generation=generation,
        lease_expires_utc=_now() + timedelta(seconds=30),
        staging_path=".nantai-studio/work/run-001/photos",
        created_utc=_now(),
    )


def _advance_to_publishing(ledger: StudioLedger) -> None:
    for status, phase, message in (
        ("running", "executing", "Started."),
        ("running", "validating", "Validated."),
        ("running", "publishing", "Publishing."),
    ):
        ledger.transition_run(
            "run-001",
            status=status,
            phase=phase,
            owner="owner-a",
            lease_generation=1,
            message=message,
            occurred_utc=_now(),
        )


def test_initialize_configures_sqlite_and_expected_tables(tmp_path):
    ledger = _ledger(tmp_path)

    with ledger.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] >= 5_000
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )
        }
        metadata = dict(connection.execute(
            "SELECT key,value FROM meta",
        ).fetchall())

    assert {
        "meta", "runs", "events", "request_dedup",
        "publications", "publication_targets",
        "capture_revisions", "sfm_bundles", "training_handoffs",
        "import_descriptors", "scene_revisions", "scene_inputs",
        "spatial_artifacts", "verification_records", "active_scene",
        "revision_pins", "revision_leases", "publication_intents",
        "gc_plans",
    }.issubset(tables)
    assert metadata == {
        "schema_version": "2",
        "schema_fingerprint": ledger_module.EXPECTED_V2_SCHEMA_FINGERPRINT,
    }


def test_initialize_migrates_exact_v1_to_v2_without_losing_runs(tmp_path):
    database = tmp_path / ".nantai-studio/studio.db"
    old = _write_v1_database(database)
    _create_run(old)

    migrated = StudioLedger(database)
    migrated.initialize()

    with migrated.connection() as connection:
        metadata = dict(connection.execute(
            "SELECT key,value FROM meta",
        ).fetchall())
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )
        }
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    assert metadata == {
        "schema_version": "2",
        "schema_fingerprint": ledger_module.EXPECTED_V2_SCHEMA_FINGERPRINT,
    }
    assert migrated.get_run("run-001").command == "ingest"
    assert {
        "capture_revisions", "sfm_bundles", "training_handoffs",
        "import_descriptors", "scene_revisions", "scene_inputs",
        "spatial_artifacts", "verification_records", "active_scene",
        "revision_pins", "revision_leases", "publication_intents",
        "gc_plans",
    }.issubset(tables)


def test_migration_rolls_back_every_v2_statement_on_failure(
    tmp_path, monkeypatch,
):
    database = tmp_path / ".nantai-studio/studio.db"
    _write_v1_database(database)
    original = ledger_module._apply_statements

    def fail_after_one_statement(connection, statements):
        connection.execute(statements[0])
        raise sqlite3.OperationalError("injected migration failure")

    monkeypatch.setattr(
        ledger_module,
        "_apply_statements",
        fail_after_one_statement,
    )
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        StudioLedger(database).initialize()
    monkeypatch.setattr(ledger_module, "_apply_statements", original)

    with sqlite3.connect(database) as connection:
        metadata = dict(connection.execute(
            "SELECT key,value FROM meta",
        ).fetchall())
        capture_table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='capture_revisions'",
        ).fetchone()

    assert metadata == {
        "schema_version": "1",
        "schema_fingerprint": ledger_module.EXPECTED_V1_SCHEMA_FINGERPRINT,
    }
    assert capture_table is None


def test_declared_v2_with_weakened_schema_fails_closed(tmp_path):
    database = tmp_path / ".nantai-studio/studio.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        )
        connection.executemany(
            "INSERT INTO meta VALUES (?,?)",
            (
                ("schema_version", "2"),
                (
                    "schema_fingerprint",
                    ledger_module.EXPECTED_V2_SCHEMA_FINGERPRINT,
                ),
            ),
        )
        connection.execute(
            "CREATE TABLE capture_revisions (id TEXT PRIMARY KEY)",
        )

    with pytest.raises(LedgerSchemaError, match="fingerprint|schema"):
        StudioLedger(database).initialize()


def test_capture_publication_commits_revision_before_run_success(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    _advance_to_publishing(ledger)
    revision_id = "capture-" + "a" * 32
    intent_id = "capture-publication-" + "b" * 32
    bundle = f".nantai-studio/artifacts/capture/{revision_id}"

    ledger.prepare_capture_publication(
        intent_id=intent_id,
        run_id="run-001",
        revision_id=revision_id,
        manifest_digest="c" * 64,
        bundle_relpath=bundle,
        owner="owner-a",
        lease_generation=1,
        created_utc=_now(),
    )
    record = ledger.commit_capture_publication(
        intent_id=intent_id,
        revision_id=revision_id,
        manifest_digest="c" * 64,
        bundle_relpath=bundle,
        provenance="synthetic",
        source_count=6,
        output_count=11,
        created_by_run="run-001",
        owner="owner-a",
        lease_generation=1,
        created_utc=_now(),
    )

    assert record.id == revision_id
    assert record.bundle_relpath == bundle
    assert ledger.get_capture_revision(revision_id) == record
    assert ledger.list_capture_revisions() == [record]
    assert ledger.get_run("run-001").status == "running"

    finished = ledger.commit_capture_run_success(
        run_id="run-001",
        revision_id=revision_id,
        owner="owner-a",
        lease_generation=1,
        message="Immutable capture and compatibility projection published.",
        occurred_utc=_now(),
    )

    assert finished.status == "succeeded"
    assert finished.artifact_ids == (revision_id,)
    assert ledger.commit_capture_run_success(
        run_id="run-001",
        revision_id=revision_id,
        owner="owner-a",
        lease_generation=1,
        message="Immutable capture and compatibility projection published.",
        occurred_utc=_now(),
    ) == finished


def test_capture_publication_recovery_calls_are_idempotent(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    _advance_to_publishing(ledger)
    revision_id = "capture-" + "d" * 32
    intent_id = "capture-publication-" + "e" * 32
    bundle = f".nantai-studio/artifacts/capture/{revision_id}"
    prepare = {
        "intent_id": intent_id,
        "run_id": "run-001",
        "revision_id": revision_id,
        "manifest_digest": "f" * 64,
        "bundle_relpath": bundle,
        "owner": "owner-a",
        "lease_generation": 1,
        "created_utc": _now(),
    }
    commit = {
        "intent_id": intent_id,
        "revision_id": revision_id,
        "manifest_digest": "f" * 64,
        "bundle_relpath": bundle,
        "provenance": "measured",
        "source_count": 2,
        "output_count": 3,
        "created_by_run": "run-001",
        "owner": "owner-a",
        "lease_generation": 1,
        "created_utc": _now(),
    }

    ledger.prepare_capture_publication(**prepare)
    ledger.prepare_capture_publication(**prepare)
    first = ledger.commit_capture_publication(**commit)
    second = ledger.commit_capture_publication(**commit)

    assert second == first
    with ledger.connection() as connection:
        assert connection.execute(
            "SELECT status FROM publication_intents WHERE id=?",
            (intent_id,),
        ).fetchone()[0] == "committed"
        assert connection.execute(
            "SELECT COUNT(*) FROM capture_revisions",
        ).fetchone()[0] == 1


def test_capture_publication_rejects_conflicts_and_unsafe_identity(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    _advance_to_publishing(ledger)
    revision_id = "capture-" + "1" * 32
    intent_id = "capture-publication-" + "2" * 32
    bundle = f".nantai-studio/artifacts/capture/{revision_id}"
    values = {
        "intent_id": intent_id,
        "run_id": "run-001",
        "revision_id": revision_id,
        "manifest_digest": "3" * 64,
        "bundle_relpath": bundle,
        "owner": "owner-a",
        "lease_generation": 1,
        "created_utc": _now(),
    }
    ledger.prepare_capture_publication(**values)

    conflict = dict(values)
    conflict["manifest_digest"] = "4" * 64
    with pytest.raises(ledger_module.RevisionConflictError, match="intent"):
        ledger.prepare_capture_publication(**conflict)

    unsafe = dict(values)
    unsafe["intent_id"] = "capture-publication-" + "5" * 32
    unsafe["bundle_relpath"] = "/absolute/capture"
    with pytest.raises(ValueError, match="bundle|path"):
        ledger.prepare_capture_publication(**unsafe)

    with pytest.raises(FencingError):
        ledger.commit_capture_publication(
            intent_id=intent_id,
            revision_id=revision_id,
            manifest_digest="3" * 64,
            bundle_relpath=bundle,
            provenance="synthetic",
            source_count=1,
            output_count=1,
            created_by_run="run-001",
            owner="other-owner",
            lease_generation=1,
            created_utc=_now(),
        )


def test_capture_success_requires_a_committed_matching_revision(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    _advance_to_publishing(ledger)

    with pytest.raises(TransitionError, match="capture|revision|intent"):
        ledger.commit_capture_run_success(
            run_id="run-001",
            revision_id="capture-" + "6" * 32,
            owner="owner-a",
            lease_generation=1,
            message="Forged success.",
            occurred_utc=_now(),
        )

    assert ledger.get_run("run-001").status == "running"


def test_capture_revision_rows_are_append_only(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    _advance_to_publishing(ledger)
    revision_id = "capture-" + "7" * 32
    intent_id = "capture-publication-" + "8" * 32
    bundle = f".nantai-studio/artifacts/capture/{revision_id}"
    ledger.prepare_capture_publication(
        intent_id=intent_id,
        run_id="run-001",
        revision_id=revision_id,
        manifest_digest="9" * 64,
        bundle_relpath=bundle,
        owner="owner-a",
        lease_generation=1,
        created_utc=_now(),
    )
    ledger.commit_capture_publication(
        intent_id=intent_id,
        revision_id=revision_id,
        manifest_digest="9" * 64,
        bundle_relpath=bundle,
        provenance="synthetic",
        source_count=1,
        output_count=1,
        created_by_run="run-001",
        owner="owner-a",
        lease_generation=1,
        created_utc=_now(),
    )

    with ledger.connection() as connection:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(
                "UPDATE capture_revisions SET source_count=2 WHERE id=?",
                (revision_id,),
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(
                "DELETE FROM capture_revisions WHERE id=?",
                (revision_id,),
            )


def test_unknown_newer_schema_fails_closed(tmp_path):
    database = tmp_path / ".nantai-studio/studio.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO meta VALUES ('schema_version', '99')")

    with pytest.raises(LedgerSchemaError, match="newer|99"):
        StudioLedger(database).initialize()


def test_declared_v1_with_weakened_schema_fails_closed(tmp_path):
    database = tmp_path / ".nantai-studio/studio.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        connection.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, status TEXT)")

    with pytest.raises(LedgerSchemaError, match="fingerprint|schema"):
        StudioLedger(database).initialize()


def test_nonempty_database_without_schema_metadata_fails_closed(tmp_path):
    database = tmp_path / ".nantai-studio/studio.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")

    with pytest.raises(LedgerSchemaError, match="metadata|empty"):
        StudioLedger(database).initialize()


def test_canonical_json_is_stable_and_rejects_non_finite_values():
    assert canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'
    with pytest.raises(ValueError, match="finite|JSON"):
        canonical_json({"bad": float("nan")})


def test_create_run_is_idempotent_for_the_same_request_payload(tmp_path):
    ledger = _ledger(tmp_path)
    first = _create_run(ledger)
    duplicate = _create_run(ledger, run_id="run-ignored")

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.run.id == first.run.id == "run-001"
    assert [event.message for event in ledger.list_events()] == ["Job queued."]


def test_reused_request_id_with_different_payload_is_a_conflict(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    with pytest.raises(RequestConflictError, match="request"):
        _create_run(
            ledger,
            run_id="run-002",
            parameters={
                "fps": 5,
                "max_frames": 300,
                "blur_threshold": 80,
                "max_long_edge": 2560,
            },
        )


def test_partial_unique_index_rejects_a_second_active_run(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    with pytest.raises(ActiveRunConflictError, match="active"):
        _create_run(ledger, run_id="run-002", request_id="request-002")


def test_normal_state_machine_and_event_cursor_are_monotonic(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    ledger.transition_run(
        "run-001", status="running", phase="executing",
        owner="owner-a", lease_generation=1,
        message="Process started.", occurred_utc=_now(),
    )
    ledger.transition_run(
        "run-001", status="running", phase="validating",
        owner="owner-a", lease_generation=1,
        message="Validating staged artifact.", occurred_utc=_now(),
    )
    ledger.transition_run(
        "run-001", status="running", phase="publishing",
        owner="owner-a", lease_generation=1,
        message="Publishing verified artifact.", occurred_utc=_now(),
    )
    ledger.prepare_publication(
        publication_id="publication-001",
        run_id="run-001",
        manifest={"files": []},
        targets=[{
            "target": "photos",
            "stage": ".nantai-studio/work/run-001/photos",
            "backup": ".nantai-studio/backups/publication-001/photos",
            "had_old": False,
        }],
        owner="owner-a",
        lease_generation=1,
        created_utc=_now(),
    )
    for step in (
        "target_backup_intent", "target_backup_done",
        "stage_target_intent", "stage_target_done",
    ):
        ledger.record_publication_step(
            publication_id="publication-001",
            ordinal=0,
            step=step,
            run_id="run-001",
            owner="owner-a",
            lease_generation=1,
        )
    ledger.commit_publication_success(
        publication_id="publication-001",
        run_id="run-001",
        artifact_ids=["ingest-" + "f" * 64],
        owner="owner-a",
        lease_generation=1,
        message="Artifact published.",
        occurred_utc=_now(),
    )

    run = ledger.get_run("run-001")
    events = ledger.list_events()
    assert run.status == "succeeded"
    assert run.phase == "publishing"
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert [event.cursor for event in events] == sorted(
        event.cursor for event in events
    )
    assert ledger.list_events(cursor=events[2].cursor)[0].cursor == events[3].cursor


def test_publication_requires_nonempty_unique_targets(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    ledger.transition_run(
        "run-001", status="running", phase="executing",
        owner="owner-a", lease_generation=1,
        message="Started.", occurred_utc=_now(),
    )
    ledger.transition_run(
        "run-001", status="running", phase="validating",
        owner="owner-a", lease_generation=1,
        message="Validated.", occurred_utc=_now(),
    )
    ledger.transition_run(
        "run-001", status="running", phase="publishing",
        owner="owner-a", lease_generation=1,
        message="Publishing.", occurred_utc=_now(),
    )

    with pytest.raises(ValueError, match="target"):
        ledger.prepare_publication(
            publication_id="empty",
            run_id="run-001",
            manifest={"files": []},
            targets=[],
            owner="owner-a",
            lease_generation=1,
            created_utc=_now(),
        )
    duplicate = {
        "target": "photos",
        "stage": ".nantai-studio/work/run-001/photos",
        "backup": ".nantai-studio/backups/duplicate/photos",
        "had_old": False,
    }
    with pytest.raises(ValueError, match="unique"):
        ledger.prepare_publication(
            publication_id="duplicate",
            run_id="run-001",
            manifest={"files": []},
            targets=[duplicate, {
                **duplicate,
                "stage": ".nantai-studio/work/run-002/photos",
                "backup": ".nantai-studio/backups/other/photos",
            }],
            owner="owner-a",
            lease_generation=1,
            created_utc=_now(),
        )


def test_publication_cannot_commit_without_journal_evidence_or_artifacts(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    for status, phase, message in (
        ("running", "executing", "Started."),
        ("running", "validating", "Validated."),
        ("running", "publishing", "Publishing."),
    ):
        ledger.transition_run(
            "run-001", status=status, phase=phase,
            owner="owner-a", lease_generation=1,
            message=message, occurred_utc=_now(),
        )
    ledger.prepare_publication(
        publication_id="publication-001",
        run_id="run-001",
        manifest={"files": ["photo.jpg"]},
        targets=[{
            "target": "photos",
            "stage": ".nantai-studio/work/run-001/photos",
            "backup": ".nantai-studio/backups/publication-001/photos",
            "had_old": False,
        }],
        owner="owner-a",
        lease_generation=1,
        created_utc=_now(),
    )

    with pytest.raises(TransitionError, match="journal"):
        ledger.commit_publication_success(
            publication_id="publication-001", run_id="run-001",
            artifact_ids=["ingest-" + "f" * 64],
            owner="owner-a", lease_generation=1,
            message="Forged success.", occurred_utc=_now(),
        )
    for step in (
        "target_backup_intent", "target_backup_done",
        "stage_target_intent", "stage_target_done",
    ):
        ledger.record_publication_step(
            publication_id="publication-001", ordinal=0, step=step,
            run_id="run-001", owner="owner-a", lease_generation=1,
        )
    with pytest.raises(ValueError, match="artifact"):
        ledger.commit_publication_success(
            publication_id="publication-001", run_id="run-001",
            artifact_ids=[], owner="owner-a", lease_generation=1,
            message="Still forged.", occurred_utc=_now(),
        )

    assert ledger.get_run("run-001").status == "running"


def test_publication_journal_steps_are_fenced(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    for status, phase in (
        ("running", "executing"),
        ("running", "validating"),
        ("running", "publishing"),
    ):
        ledger.transition_run(
            "run-001", status=status, phase=phase,
            owner="owner-a", lease_generation=1,
            message=phase, occurred_utc=_now(),
        )
    ledger.prepare_publication(
        publication_id="publication-001", run_id="run-001",
        manifest={"files": ["photo.jpg"]},
        targets=[{
            "target": "photos",
            "stage": ".nantai-studio/work/run-001/photos",
            "backup": ".nantai-studio/backups/publication-001/photos",
            "had_old": False,
        }],
        owner="owner-a", lease_generation=1, created_utc=_now(),
    )

    for owner, generation in (("owner-b", 1), ("owner-a", 2)):
        with pytest.raises(FencingError):
            ledger.record_publication_step(
                publication_id="publication-001", ordinal=0,
                step="target_backup_intent", run_id="run-001",
                owner=owner, lease_generation=generation,
            )

    with ledger.connection() as connection:
        value = connection.execute(
            "SELECT target_backup_intent FROM publication_targets",
        ).fetchone()[0]
    assert value == 0


@pytest.mark.parametrize(
    ("status", "phase", "error_code"),
    [
        ("succeeded", "publishing", None),
        ("running", "publishing", None),
        ("failed", None, "job_failed"),
    ],
)
def test_illegal_or_terminal_transitions_are_rejected(
    tmp_path, status, phase, error_code,
):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    if status == "running" and phase == "publishing":
        with pytest.raises(TransitionError):
            ledger.transition_run(
                "run-001", status=status, phase=phase,
                owner="owner-a", lease_generation=1,
                message="Illegal jump.", occurred_utc=_now(),
            )
        return
    if status == "failed":
        with pytest.raises(TransitionError, match="stale_job"):
            ledger.transition_run(
                "run-001", status=status, phase=phase,
                owner="owner-a", lease_generation=1,
                error_code=error_code, message="Illegal queued failure.",
                occurred_utc=_now(),
            )
        return
    with pytest.raises(TransitionError, match="publication"):
        ledger.transition_run(
            "run-001", status=status, phase=phase,
            owner="owner-a", lease_generation=1,
            message="Illegal success.", occurred_utc=_now(),
        )


def test_recovery_can_fail_a_queued_run_only_as_stale(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    ledger.transition_run(
        "run-001", status="failed", phase=None,
        owner="owner-a", lease_generation=1,
        error_code="stale_job", message="Queued snapshot is stale.",
        occurred_utc=_now(), recovery=True,
    )

    assert ledger.get_run("run-001").error_code == "stale_job"


def test_running_run_can_fail_and_release_the_active_slot(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    ledger.transition_run(
        "run-001", status="running", phase="executing",
        owner="owner-a", lease_generation=1,
        message="Process started.", occurred_utc=_now(),
    )

    failed = ledger.transition_run(
        "run-001", status="failed", phase=None,
        owner="owner-a", lease_generation=1,
        error_code="job_failed", message="Child exited nonzero.",
        occurred_utc=_now(),
    )
    replacement = _create_run(
        ledger, run_id="run-002", request_id="request-002",
    )

    assert failed.status == "failed"
    assert failed.phase == "executing"
    assert replacement.created is True


def test_running_failure_cannot_forge_a_later_phase(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    ledger.transition_run(
        "run-001", status="running", phase="executing",
        owner="owner-a", lease_generation=1,
        message="Started.", occurred_utc=_now(),
    )
    with pytest.raises(TransitionError, match="phase"):
        ledger.transition_run(
            "run-001", status="failed", phase="publishing",
            owner="owner-a", lease_generation=1,
            error_code="job_failed", message="Forged phase.",
            occurred_utc=_now(),
        )


def test_terminal_run_cannot_be_reopened(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)
    ledger.transition_run(
        "run-001", status="failed", phase=None,
        owner="owner-a", lease_generation=1,
        error_code="stale_job", message="Stale.",
        occurred_utc=_now(), recovery=True,
    )

    with pytest.raises(TransitionError, match="terminal"):
        ledger.transition_run(
            "run-001", status="running", phase="executing",
            owner="owner-a", lease_generation=1,
            message="Reopen.", occurred_utc=_now(),
        )


def test_wrong_owner_or_generation_is_fenced(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    for owner, generation in [("owner-b", 1), ("owner-a", 2)]:
        with pytest.raises(FencingError, match="fenc"):
            ledger.transition_run(
                "run-001", status="running", phase="executing",
                owner=owner, lease_generation=generation,
                message="Should be fenced.", occurred_utc=_now(),
            )


def test_state_and_event_append_rollback_together(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    with pytest.raises(sqlite3.IntegrityError):
        ledger.transition_run(
            "run-001", status="running", phase="executing",
            owner="owner-a", lease_generation=1,
            message="Invalid progress.", progress=2.0, occurred_utc=_now(),
        )

    assert ledger.get_run("run-001").status == "queued"
    assert len(ledger.list_events()) == 1


def test_events_are_append_only_even_through_raw_sql(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    with ledger.connection() as connection:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("UPDATE events SET message='forged' WHERE cursor=1")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM events WHERE cursor=1")


def test_parameters_and_snapshot_are_stored_as_canonical_json(tmp_path):
    ledger = _ledger(tmp_path)
    _create_run(ledger)

    with ledger.connection() as connection:
        row = connection.execute(
            "SELECT parameters_json, snapshot_json FROM runs WHERE id='run-001'",
        ).fetchone()

    assert row[0] == canonical_json(json.loads(row[0]))
    assert row[1] == canonical_json(json.loads(row[1]))
