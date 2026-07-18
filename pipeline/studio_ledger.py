"""SQLite source of truth for the local Studio job kernel.

The ledger owns the run state machine, append-only events, request
idempotency, lease fencing, and the publication point of no return.  It does
not start processes or mutate project artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5_000
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled"})
RUNNING_PHASES = ("executing", "validating", "publishing")
_SQLITE_WRITE_DEPTH: ContextVar[int] = ContextVar("studio_sqlite_write_depth", default=0)
_CAPTURE_REVISION_RE = re.compile(r"^capture-[0-9a-f]{32}$")
_CAPTURE_PUBLICATION_RE = re.compile(r"^capture-publication-[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LedgerError(RuntimeError):
    """Base class for durable Studio ledger failures."""


class LedgerSchemaError(LedgerError):
    """The database schema cannot be safely opened by this version."""


class RequestConflictError(LedgerError):
    """A request ID was reused with a different canonical payload."""


class ActiveRunConflictError(LedgerError):
    """The project already has an active writer run."""


class TransitionError(LedgerError):
    """A requested run state transition is not legal."""


class FencingError(LedgerError):
    """A stale or foreign worker attempted to mutate a run."""


class RevisionConflictError(LedgerError):
    """An immutable revision identity was reused with different evidence."""


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically and reject NaN/Infinity."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be finite JSON data") from exc


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("ledger timestamps must be timezone-aware UTC")
    return value.isoformat(timespec="microseconds")


def _validate_capture_identity(
    *,
    revision_id: str,
    manifest_digest: str,
    bundle_relpath: str,
) -> None:
    if _CAPTURE_REVISION_RE.fullmatch(revision_id) is None:
        raise ValueError("capture revision ID is invalid")
    if _SHA256_RE.fullmatch(manifest_digest) is None:
        raise ValueError("capture manifest digest is invalid")
    expected_bundle = f".nantai-studio/artifacts/capture/{revision_id}"
    if bundle_relpath != expected_bundle:
        raise ValueError("capture bundle path is not the fixed managed path")


def _validate_capture_publication_id(intent_id: str) -> None:
    if _CAPTURE_PUBLICATION_RE.fullmatch(intent_id) is None:
        raise ValueError("capture publication intent ID is invalid")


def sqlite_write_transaction_active() -> bool:
    """Return whether this execution context currently owns a write tx."""

    return _SQLITE_WRITE_DEPTH.get() > 0


@dataclass(frozen=True)
class RunRecord:
    id: str
    command: str
    command_schema_version: int
    status: str
    phase: str | None
    parameters: dict[str, Any]
    snapshot: dict[str, Any]
    owner: str
    lease_generation: int
    lease_expires_utc: str
    staging_path: str
    created_utc: str
    updated_utc: str
    error_code: str | None
    error_message: str | None
    artifact_ids: tuple[str, ...]
    child_pid: int | None
    child_start_identity: str | None


@dataclass(frozen=True)
class EventRecord:
    cursor: int
    run_id: str
    seq: int
    phase: str | None
    progress: float | None
    level: str
    code: str | None
    message: str
    created_utc: str


@dataclass(frozen=True)
class CreateRunResult:
    run: RunRecord
    created: bool


@dataclass(frozen=True)
class CaptureRevisionRecord:
    id: str
    manifest_digest: str
    bundle_relpath: str
    provenance: str
    source_count: int
    output_count: int
    created_by_run: str
    created_utc: str


@dataclass(frozen=True)
class CapturePublicationIntentRecord:
    id: str
    run_id: str
    revision_id: str
    manifest_digest: str
    bundle_relpath: str
    status: str
    created_utc: str
    finished_utc: str | None


@dataclass(frozen=True)
class PublicationTargetRecord:
    publication_id: str
    ordinal: int
    target_path: str
    stage_path: str
    backup_path: str
    had_old: bool
    target_backup_intent: bool
    target_backup_done: bool
    stage_target_intent: bool
    stage_target_done: bool


@dataclass(frozen=True)
class PublicationRecord:
    journal_order: int
    id: str
    run_id: str
    status: str
    manifest: dict[str, Any]
    targets: tuple[PublicationTargetRecord, ...]


_SCHEMA_V1_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    command_schema_version INTEGER NOT NULL CHECK(command_schema_version >= 1),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','canceled')),
    phase TEXT CHECK(phase IS NULL OR phase IN ('executing','validating','publishing')),
    retry_of TEXT REFERENCES runs(id),
    parameters_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL CHECK(length(snapshot_digest) = 64),
    owner TEXT NOT NULL,
    lease_generation INTEGER NOT NULL CHECK(lease_generation >= 1),
    lease_expires_utc TEXT NOT NULL,
    staging_path TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    started_utc TEXT,
    updated_utc TEXT NOT NULL,
    finished_utc TEXT,
    exit_code INTEGER,
    error_code TEXT,
    error_message TEXT,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    child_pid INTEGER,
    child_start_identity TEXT,
    CHECK(
        (status = 'queued' AND phase IS NULL) OR
        (status = 'running' AND phase IS NOT NULL) OR
        (status IN ('succeeded','failed','canceled'))
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active
ON runs((1)) WHERE status IN ('queued','running');

CREATE TABLE IF NOT EXISTS events (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    seq INTEGER NOT NULL CHECK(seq >= 1),
    phase TEXT CHECK(phase IS NULL OR phase IN ('executing','validating','publishing')),
    progress REAL CHECK(progress IS NULL OR (progress >= 0 AND progress <= 1)),
    level TEXT NOT NULL CHECK(level IN ('info','warning','error')),
    code TEXT,
    message TEXT NOT NULL CHECK(length(message) > 0),
    created_utc TEXT NOT NULL,
    UNIQUE(run_id, seq)
);

CREATE TRIGGER IF NOT EXISTS events_are_append_only_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_are_append_only_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TABLE IF NOT EXISTS request_dedup (
    request_id TEXT PRIMARY KEY,
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE RESTRICT,
    created_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publications (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('prepared','committed','rolled_back')),
    manifest_json TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    committed_utc TEXT
);

CREATE TABLE IF NOT EXISTS publication_targets (
    publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    target_path TEXT NOT NULL,
    stage_path TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    had_old INTEGER NOT NULL CHECK(had_old IN (0,1)),
    target_backup_intent INTEGER NOT NULL DEFAULT 0 CHECK(target_backup_intent IN (0,1)),
    target_backup_done INTEGER NOT NULL DEFAULT 0 CHECK(target_backup_done IN (0,1)),
    stage_target_intent INTEGER NOT NULL DEFAULT 0 CHECK(stage_target_intent IN (0,1)),
    stage_target_done INTEGER NOT NULL DEFAULT 0 CHECK(stage_target_done IN (0,1)),
    PRIMARY KEY(publication_id, ordinal)
);
"""

_SCHEMA_V2_TABLE_STATEMENTS = (
    """
    CREATE TABLE capture_revisions (
        id TEXT PRIMARY KEY
            CHECK(
                length(id) = 40
                AND substr(id, 1, 8) = 'capture-'
                AND substr(id, 9) NOT GLOB '*[^0-9a-f]*'
            ),
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest) = 64),
        bundle_relpath TEXT NOT NULL UNIQUE
            CHECK(bundle_relpath = '.nantai-studio/artifacts/capture/' || id),
        provenance TEXT NOT NULL
            CHECK(provenance IN ('measured','synthetic','unknown')),
        source_count INTEGER NOT NULL CHECK(source_count >= 1),
        output_count INTEGER NOT NULL CHECK(output_count >= 1),
        created_by_run TEXT NOT NULL UNIQUE
            REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE sfm_bundles (
        id TEXT PRIMARY KEY,
        capture_revision_id TEXT NOT NULL
            REFERENCES capture_revisions(id) ON DELETE RESTRICT,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest) = 64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        frame_id TEXT NOT NULL,
        created_by_run TEXT REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE training_handoffs (
        id TEXT PRIMARY KEY,
        capture_revision_id TEXT NOT NULL
            REFERENCES capture_revisions(id) ON DELETE RESTRICT,
        sfm_bundle_id TEXT NOT NULL
            REFERENCES sfm_bundles(id) ON DELETE RESTRICT,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest) = 64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        created_by_run TEXT REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE import_descriptors (
        id TEXT PRIMARY KEY,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest) = 64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        created_by_run TEXT REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE scene_revisions (
        id TEXT PRIMARY KEY,
        parent_revision_id TEXT
            REFERENCES scene_revisions(id) ON DELETE RESTRICT,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest) = 64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        requested_engine TEXT NOT NULL,
        actual_engine TEXT NOT NULL,
        toolchain_capsule_digest TEXT NOT NULL
            CHECK(length(toolchain_capsule_digest) = 64),
        created_by_run TEXT NOT NULL UNIQUE
            REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE scene_inputs (
        scene_revision_id TEXT NOT NULL
            REFERENCES scene_revisions(id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        input_kind TEXT NOT NULL,
        input_id TEXT NOT NULL,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest) = 64),
        PRIMARY KEY(scene_revision_id, ordinal)
    )
    """,
    """
    CREATE TABLE spatial_artifacts (
        id TEXT PRIMARY KEY,
        scene_revision_id TEXT NOT NULL
            REFERENCES scene_revisions(id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        format TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        byte_length INTEGER NOT NULL CHECK(byte_length >= 1),
        descriptor_json TEXT NOT NULL,
        UNIQUE(scene_revision_id, ordinal)
    )
    """,
    """
    CREATE TABLE verification_records (
        id TEXT PRIMARY KEY,
        subject_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        level TEXT NOT NULL CHECK(level IN ('L0','L1','L2','L3','L4','L5')),
        passed INTEGER NOT NULL CHECK(passed IN (0,1)),
        evidence_json TEXT NOT NULL,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE active_scene (
        project_id TEXT PRIMARY KEY,
        scene_revision_id TEXT NOT NULL
            REFERENCES scene_revisions(id) ON DELETE RESTRICT,
        generation INTEGER NOT NULL CHECK(generation >= 1),
        updated_event_id TEXT NOT NULL,
        updated_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE revision_pins (
        id TEXT PRIMARY KEY,
        subject_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        released_utc TEXT
    )
    """,
    """
    CREATE TABLE revision_leases (
        id TEXT PRIMARY KEY,
        subject_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        owner TEXT NOT NULL,
        expires_utc TEXT NOT NULL,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE publication_intents (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
        subject_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest) = 64),
        destination_relpath TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL
            CHECK(status IN ('prepared','committed','quarantined')),
        created_utc TEXT NOT NULL,
        finished_utc TEXT
    )
    """,
    """
    CREATE TABLE gc_plans (
        id TEXT PRIMARY KEY,
        observed_active_generation INTEGER NOT NULL
            CHECK(observed_active_generation >= 0),
        mark_set_json TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(
                status IN (
                    'planned','tombstoning','cooling','completed','aborted'
                )
            ),
        created_utc TEXT NOT NULL,
        finished_utc TEXT
    )
    """,
)

_APPEND_ONLY_V2_TABLES = (
    "capture_revisions",
    "sfm_bundles",
    "training_handoffs",
    "import_descriptors",
    "scene_revisions",
    "scene_inputs",
    "spatial_artifacts",
    "verification_records",
)

_SCHEMA_V2_TRIGGER_STATEMENTS = tuple(
    statement
    for table in _APPEND_ONLY_V2_TABLES
    for statement in (
        f"""
        CREATE TRIGGER {table}_append_only_update
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} is append-only');
        END
        """,
        f"""
        CREATE TRIGGER {table}_append_only_delete
        BEFORE DELETE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} is append-only');
        END
        """,
    )
)

_SCHEMA_V2_STATEMENTS = (
    *_SCHEMA_V2_TABLE_STATEMENTS,
    *_SCHEMA_V2_TRIGGER_STATEMENTS,
)


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type,name,tbl_name,sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type,name
        """,
    ).fetchall()
    payload = [tuple(row) for row in rows]
    return hashlib.sha256(canonical_json(payload).encode("ascii")).hexdigest()


def _apply_sql_script(connection: sqlite3.Connection, script: str) -> None:
    """Execute a trusted SQL script without sqlite3.executescript auto-commits."""

    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                connection.execute(statement)
            pending = ""
    if pending.strip():
        raise LedgerSchemaError("trusted ledger schema SQL is incomplete")


def _apply_statements(
    connection: sqlite3.Connection,
    statements: Sequence[str],
) -> None:
    for statement in statements:
        connection.execute(statement)


def _expected_schema_fingerprint(version: int) -> str:
    connection = sqlite3.connect(":memory:")
    try:
        _apply_sql_script(connection, _SCHEMA_V1_SQL)
        if version == 2:
            _apply_statements(connection, _SCHEMA_V2_STATEMENTS)
        elif version != 1:
            raise ValueError(f"unsupported schema fingerprint version: {version}")
        return _schema_fingerprint(connection)
    finally:
        connection.close()


EXPECTED_V1_SCHEMA_FINGERPRINT = _expected_schema_fingerprint(1)
EXPECTED_V2_SCHEMA_FINGERPRINT = _expected_schema_fingerprint(2)
# Compatibility name for callers that only need the current schema identity.
EXPECTED_SCHEMA_FINGERPRINT = EXPECTED_V2_SCHEMA_FINGERPRINT


def _create_v2_schema(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        _apply_sql_script(connection, _SCHEMA_V1_SQL)
        _apply_statements(connection, _SCHEMA_V2_STATEMENTS)
        actual = _schema_fingerprint(connection)
        if actual != EXPECTED_V2_SCHEMA_FINGERPRINT:
            raise LedgerSchemaError(
                "new ledger schema fingerprint does not match schema v2",
            )
        connection.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            (
                ("schema_version", str(SCHEMA_VERSION)),
                ("schema_fingerprint", EXPECTED_V2_SCHEMA_FINGERPRINT),
            ),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        metadata = dict(connection.execute(
            "SELECT key,value FROM meta WHERE key IN "
            "('schema_version','schema_fingerprint')",
        ).fetchall())
        if (
            metadata.get("schema_version") != "1"
            or metadata.get("schema_fingerprint")
            != EXPECTED_V1_SCHEMA_FINGERPRINT
            or _schema_fingerprint(connection) != EXPECTED_V1_SCHEMA_FINGERPRINT
        ):
            raise LedgerSchemaError(
                "ledger schema fingerprint does not match schema v1",
            )
        _apply_statements(connection, _SCHEMA_V2_STATEMENTS)
        if _schema_fingerprint(connection) != EXPECTED_V2_SCHEMA_FINGERPRINT:
            raise LedgerSchemaError(
                "migrated ledger schema fingerprint does not match schema v2",
            )
        version_update = connection.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'",
            (str(SCHEMA_VERSION),),
        )
        fingerprint_update = connection.execute(
            "UPDATE meta SET value=? WHERE key='schema_fingerprint'",
            (EXPECTED_V2_SCHEMA_FINGERPRINT,),
        )
        if version_update.rowcount != 1 or fingerprint_update.rowcount != 1:
            raise LedgerSchemaError("ledger schema metadata is incomplete")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


class StudioLedger:
    """Versioned SQLite ledger with fenced transactional mutations."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Open one configured autocommit connection for bounded reads."""

        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self, *, synchronous_full: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        depth_token = _SQLITE_WRITE_DEPTH.set(_SQLITE_WRITE_DEPTH.get() + 1)
        try:
            if synchronous_full:
                connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            _SQLITE_WRITE_DEPTH.reset(depth_token)
            connection.close()

    def initialize(self) -> None:
        """Create schema v2, migrate exact v1, or reject incompatible state."""

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            user_objects = connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'",
            ).fetchall()
            if not user_objects:
                _create_v2_schema(connection)
                return

            has_meta = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'",
            ).fetchone()
            if not has_meta:
                raise LedgerSchemaError(
                    "nonempty ledger database is missing schema metadata",
                )
            try:
                metadata = dict(connection.execute(
                    "SELECT key,value FROM meta WHERE key IN "
                    "('schema_version','schema_fingerprint')",
                ).fetchall())
                version = int(metadata.get("schema_version", ""))
            except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
                raise LedgerSchemaError("ledger schema version is invalid") from exc

            if version > SCHEMA_VERSION:
                raise LedgerSchemaError(
                    f"ledger schema {version!r} is newer; expected {SCHEMA_VERSION}",
                )
            if version == 1:
                if (
                    metadata.get("schema_fingerprint")
                    != EXPECTED_V1_SCHEMA_FINGERPRINT
                    or _schema_fingerprint(connection)
                    != EXPECTED_V1_SCHEMA_FINGERPRINT
                ):
                    raise LedgerSchemaError(
                        "ledger schema fingerprint does not match schema v1",
                    )
                _migrate_v1_to_v2(connection)
                return
            if version != SCHEMA_VERSION:
                raise LedgerSchemaError(
                    f"ledger schema {version!r} is unsupported; "
                    f"expected {SCHEMA_VERSION}",
                )
            if (
                metadata.get("schema_fingerprint")
                != EXPECTED_V2_SCHEMA_FINGERPRINT
                or _schema_fingerprint(connection)
                != EXPECTED_V2_SCHEMA_FINGERPRINT
            ):
                raise LedgerSchemaError(
                    "ledger schema fingerprint does not match schema v2",
                )

    @staticmethod
    def _payload_digest(
        *, command: str, command_schema_version: int, parameters: Mapping[str, Any],
    ) -> str:
        payload = canonical_json({
            "command": command,
            "command_schema_version": command_schema_version,
            "parameters": parameters,
        }).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _snapshot_digest(snapshot_json: str) -> str:
        return hashlib.sha256(snapshot_json.encode("ascii")).hexdigest()

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            command=row["command"],
            command_schema_version=row["command_schema_version"],
            status=row["status"],
            phase=row["phase"],
            parameters=json.loads(row["parameters_json"]),
            snapshot=json.loads(row["snapshot_json"]),
            owner=row["owner"],
            lease_generation=row["lease_generation"],
            lease_expires_utc=row["lease_expires_utc"],
            staging_path=row["staging_path"],
            created_utc=row["created_utc"],
            updated_utc=row["updated_utc"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            artifact_ids=tuple(json.loads(row["artifact_ids_json"])),
            child_pid=row["child_pid"],
            child_start_identity=row["child_start_identity"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            cursor=row["cursor"],
            run_id=row["run_id"],
            seq=row["seq"],
            phase=row["phase"],
            progress=row["progress"],
            level=row["level"],
            code=row["code"],
            message=row["message"],
            created_utc=row["created_utc"],
        )

    @staticmethod
    def _capture_from_row(row: sqlite3.Row) -> CaptureRevisionRecord:
        return CaptureRevisionRecord(
            id=row["id"],
            manifest_digest=row["manifest_digest"],
            bundle_relpath=row["bundle_relpath"],
            provenance=row["provenance"],
            source_count=row["source_count"],
            output_count=row["output_count"],
            created_by_run=row["created_by_run"],
            created_utc=row["created_utc"],
        )

    @staticmethod
    def _capture_intent_from_row(
        row: sqlite3.Row,
    ) -> CapturePublicationIntentRecord:
        return CapturePublicationIntentRecord(
            id=row["id"],
            run_id=row["run_id"],
            revision_id=row["subject_id"],
            manifest_digest=row["manifest_digest"],
            bundle_relpath=row["destination_relpath"],
            status=row["status"],
            created_utc=row["created_utc"],
            finished_utc=row["finished_utc"],
        )

    @staticmethod
    def _next_seq(connection: sqlite3.Connection, run_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row[0])

    @classmethod
    def _append_event(
        cls,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        phase: str | None,
        progress: float | None,
        level: str,
        code: str | None,
        message: str,
        occurred_utc: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(run_id,seq,phase,progress,level,code,message,created_utc)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                cls._next_seq(connection, run_id),
                phase,
                progress,
                level,
                code,
                message,
                occurred_utc,
            ),
        )

    def create_run(
        self,
        *,
        run_id: str,
        request_id: str,
        command: str,
        command_schema_version: int,
        parameters: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        owner: str,
        lease_generation: int,
        lease_expires_utc: datetime,
        staging_path: str,
        created_utc: datetime,
    ) -> CreateRunResult:
        parameters_json = canonical_json(parameters)
        snapshot_json = canonical_json(snapshot)
        payload_digest = self._payload_digest(
            command=command,
            command_schema_version=command_schema_version,
            parameters=parameters,
        )
        created_text = _utc_text(created_utc)
        lease_text = _utc_text(lease_expires_utc)

        with self._transaction() as connection:
            duplicate = connection.execute(
                "SELECT payload_digest,run_id FROM request_dedup WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if duplicate:
                if duplicate["payload_digest"] != payload_digest:
                    raise RequestConflictError(
                        "request ID was reused with a different payload",
                    )
                row = connection.execute(
                    "SELECT * FROM runs WHERE id=?", (duplicate["run_id"],),
                ).fetchone()
                return CreateRunResult(self._run_from_row(row), created=False)
            try:
                connection.execute(
                    """
                    INSERT INTO runs(
                        id,command,command_schema_version,status,phase,
                        parameters_json,snapshot_json,snapshot_digest,
                        owner,lease_generation,lease_expires_utc,staging_path,
                        created_utc,updated_utc
                    ) VALUES(?,?,?,'queued',NULL,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        command,
                        command_schema_version,
                        parameters_json,
                        snapshot_json,
                        self._snapshot_digest(snapshot_json),
                        owner,
                        lease_generation,
                        lease_text,
                        staging_path,
                        created_text,
                        created_text,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                active = connection.execute(
                    "SELECT id FROM runs WHERE status IN ('queued','running') LIMIT 1",
                ).fetchone()
                if active:
                    raise ActiveRunConflictError(
                        f"active run already exists: {active['id']}",
                    ) from exc
                raise
            connection.execute(
                "INSERT INTO request_dedup VALUES(?,?,?,?)",
                (request_id, payload_digest, run_id, created_text),
            )
            self._append_event(
                connection,
                run_id=run_id,
                phase=None,
                progress=0,
                level="info",
                code=None,
                message="Job queued.",
                occurred_utc=created_text,
            )
            row = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            return CreateRunResult(self._run_from_row(row), created=True)

    def get_run(self, run_id: str) -> RunRecord:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_from_row(row)

    def find_request(
        self,
        request_id: str,
        *,
        command: str,
        command_schema_version: int,
        parameters: Mapping[str, Any],
    ) -> RunRecord | None:
        """Return an idempotent request match without taking a write lock."""

        expected = self._payload_digest(
            command=command,
            command_schema_version=command_schema_version,
            parameters=parameters,
        )
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT request_dedup.payload_digest,runs.*
                FROM request_dedup
                JOIN runs ON runs.id=request_dedup.run_id
                WHERE request_dedup.request_id=?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        if row["payload_digest"] != expected:
            raise RequestConflictError(
                "request ID was reused with a different payload",
            )
        return self._run_from_row(row)

    def list_runs(self, *, limit: int = 1_000) -> list[RunRecord]:
        if not 1 <= limit <= 10_000:
            raise ValueError("invalid run limit")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_utc DESC,id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def prepare_capture_publication(
        self,
        *,
        intent_id: str,
        run_id: str,
        revision_id: str,
        manifest_digest: str,
        bundle_relpath: str,
        owner: str,
        lease_generation: int,
        created_utc: datetime,
    ) -> None:
        """Journal an absent-only capture destination before its durable move."""

        _validate_capture_publication_id(intent_id)
        _validate_capture_identity(
            revision_id=revision_id,
            manifest_digest=manifest_digest,
            bundle_relpath=bundle_relpath,
        )
        created_text = _utc_text(created_utc)
        expected = {
            "run_id": run_id,
            "subject_kind": "capture",
            "subject_id": revision_id,
            "manifest_digest": manifest_digest,
            "destination_relpath": bundle_relpath,
            "created_utc": created_text,
        }
        with self._transaction(synchronous_full=True) as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            self._require_fence(
                run,
                owner=owner,
                lease_generation=lease_generation,
            )
            if (
                run["command"] != "ingest"
                or run["status"] != "running"
                or run["phase"] != "publishing"
            ):
                raise TransitionError(
                    "capture publication requires a running/publishing ingest",
                )
            existing = connection.execute(
                "SELECT * FROM publication_intents WHERE id=?",
                (intent_id,),
            ).fetchone()
            if existing is not None:
                if any(existing[key] != value for key, value in expected.items()):
                    raise RevisionConflictError(
                        "capture publication intent conflicts with immutable evidence",
                    )
                return
            try:
                connection.execute(
                    """
                    INSERT INTO publication_intents(
                        id,run_id,subject_kind,subject_id,manifest_digest,
                        destination_relpath,status,created_utc,finished_utc
                    ) VALUES(?,?,?,?,?,?,'prepared',?,NULL)
                    """,
                    (
                        intent_id,
                        run_id,
                        "capture",
                        revision_id,
                        manifest_digest,
                        bundle_relpath,
                        created_text,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RevisionConflictError(
                    "capture publication destination or identity already exists",
                ) from exc

    def commit_capture_publication(
        self,
        *,
        intent_id: str,
        revision_id: str,
        manifest_digest: str,
        bundle_relpath: str,
        provenance: Literal["measured", "synthetic", "unknown"],
        source_count: int,
        output_count: int,
        created_by_run: str,
        owner: str,
        lease_generation: int,
        created_utc: datetime,
    ) -> CaptureRevisionRecord:
        """Commit a verified, already durable capture bundle to SQLite truth."""

        _validate_capture_publication_id(intent_id)
        _validate_capture_identity(
            revision_id=revision_id,
            manifest_digest=manifest_digest,
            bundle_relpath=bundle_relpath,
        )
        if provenance not in {"measured", "synthetic", "unknown"}:
            raise ValueError("capture provenance is invalid")
        if (
            type(source_count) is not int
            or source_count < 1
            or type(output_count) is not int
            or output_count < 1
        ):
            raise ValueError("capture source and output counts must be positive")
        created_text = _utc_text(created_utc)
        expected_record = CaptureRevisionRecord(
            id=revision_id,
            manifest_digest=manifest_digest,
            bundle_relpath=bundle_relpath,
            provenance=provenance,
            source_count=source_count,
            output_count=output_count,
            created_by_run=created_by_run,
            created_utc=created_text,
        )
        with self._transaction(synchronous_full=True) as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?",
                (created_by_run,),
            ).fetchone()
            if run is None:
                raise KeyError(created_by_run)
            self._require_fence(
                run,
                owner=owner,
                lease_generation=lease_generation,
            )
            if (
                run["command"] != "ingest"
                or run["status"] != "running"
                or run["phase"] != "publishing"
            ):
                raise TransitionError(
                    "capture commit requires a running/publishing ingest",
                )
            intent = connection.execute(
                "SELECT * FROM publication_intents WHERE id=?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                raise TransitionError("capture publication intent is missing")
            if (
                intent["run_id"] != created_by_run
                or intent["subject_kind"] != "capture"
                or intent["subject_id"] != revision_id
                or intent["manifest_digest"] != manifest_digest
                or intent["destination_relpath"] != bundle_relpath
            ):
                raise RevisionConflictError(
                    "capture publication intent conflicts with commit evidence",
                )
            existing = connection.execute(
                "SELECT * FROM capture_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()
            if existing is not None:
                record = self._capture_from_row(existing)
                if record != expected_record or intent["status"] != "committed":
                    raise RevisionConflictError(
                        "capture revision conflicts with immutable evidence",
                    )
                return record
            if intent["status"] != "prepared":
                raise TransitionError(
                    "capture publication intent is not prepared",
                )
            try:
                connection.execute(
                    """
                    INSERT INTO capture_revisions(
                        id,manifest_digest,bundle_relpath,provenance,
                        source_count,output_count,created_by_run,created_utc
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        revision_id,
                        manifest_digest,
                        bundle_relpath,
                        provenance,
                        source_count,
                        output_count,
                        created_by_run,
                        created_text,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RevisionConflictError(
                    "capture revision run, path, or identity already exists",
                ) from exc
            updated = connection.execute(
                """
                UPDATE publication_intents
                SET status='committed',finished_utc=?
                WHERE id=? AND status='prepared'
                """,
                (created_text, intent_id),
            )
            if updated.rowcount != 1:
                raise FencingError(
                    "capture publication intent changed before commit",
                )
            row = connection.execute(
                "SELECT * FROM capture_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()
            return self._capture_from_row(row)

    def commit_capture_run_success(
        self,
        *,
        run_id: str,
        revision_id: str,
        owner: str,
        lease_generation: int,
        message: str,
        occurred_utc: datetime,
    ) -> RunRecord:
        """Finish an ingest only after its capture and projection are complete."""

        if _CAPTURE_REVISION_RE.fullmatch(revision_id) is None:
            raise ValueError("capture revision ID is invalid")
        if not message or len(message) > 4_096:
            raise ValueError("capture success message must be bounded")
        occurred_text = _utc_text(occurred_utc)
        artifact_ids_json = canonical_json([revision_id])
        with self._transaction(synchronous_full=True) as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            self._require_fence(
                run,
                owner=owner,
                lease_generation=lease_generation,
            )
            if run["status"] == "succeeded":
                if (
                    run["command"] != "ingest"
                    or run["artifact_ids_json"] != artifact_ids_json
                ):
                    raise RevisionConflictError(
                        "succeeded run references different capture evidence",
                    )
                return self._run_from_row(run)
            if (
                run["command"] != "ingest"
                or run["status"] != "running"
                or run["phase"] != "publishing"
            ):
                raise TransitionError(
                    "capture success requires a running/publishing ingest",
                )
            capture = connection.execute(
                """
                SELECT id FROM capture_revisions
                WHERE id=? AND created_by_run=?
                """,
                (revision_id, run_id),
            ).fetchone()
            intent = connection.execute(
                """
                SELECT id FROM publication_intents
                WHERE run_id=? AND subject_kind='capture' AND subject_id=?
                  AND status='committed'
                """,
                (run_id, revision_id),
            ).fetchone()
            if capture is None or intent is None:
                raise TransitionError(
                    "capture revision and committed intent are required",
                )
            result = connection.execute(
                """
                UPDATE runs
                SET status='succeeded',updated_utc=?,finished_utc=?,
                    artifact_ids_json=?
                WHERE id=? AND status='running' AND phase='publishing'
                  AND owner=? AND lease_generation=?
                """,
                (
                    occurred_text,
                    occurred_text,
                    artifact_ids_json,
                    run_id,
                    owner,
                    lease_generation,
                ),
            )
            if result.rowcount != 1:
                raise FencingError(
                    "run changed before capture publication success",
                )
            self._append_event(
                connection,
                run_id=run_id,
                phase="publishing",
                progress=1,
                level="info",
                code=None,
                message=message,
                occurred_utc=occurred_text,
            )
            updated = connection.execute(
                "SELECT * FROM runs WHERE id=?",
                (run_id,),
            ).fetchone()
            return self._run_from_row(updated)

    def get_capture_revision(self, revision_id: str) -> CaptureRevisionRecord:
        if _CAPTURE_REVISION_RE.fullmatch(revision_id) is None:
            raise KeyError(revision_id)
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM capture_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()
        if row is None:
            raise KeyError(revision_id)
        return self._capture_from_row(row)

    def list_capture_revisions(
        self,
        *,
        limit: int = 1_000,
    ) -> list[CaptureRevisionRecord]:
        if not 1 <= limit <= 10_000:
            raise ValueError("invalid capture revision limit")
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM capture_revisions
                ORDER BY created_utc DESC,id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._capture_from_row(row) for row in rows]

    def get_capture_publication_intent(
        self,
        intent_id: str,
    ) -> CapturePublicationIntentRecord:
        _validate_capture_publication_id(intent_id)
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM publication_intents
                WHERE id=? AND subject_kind='capture'
                """,
                (intent_id,),
            ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return self._capture_intent_from_row(row)

    def record_child_process(
        self,
        run_id: str,
        *,
        pid: int,
        start_identity: str,
        owner: str,
        lease_generation: int,
        occurred_utc: datetime,
    ) -> RunRecord:
        if pid <= 0 or not start_identity:
            raise ValueError("child process identity is required")
        occurred_text = _utc_text(occurred_utc)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            self._require_fence(
                row, owner=owner, lease_generation=lease_generation,
            )
            if row["status"] != "running" or row["phase"] != "executing":
                raise TransitionError("child identity requires running/executing")
            result = connection.execute(
                """
                UPDATE runs SET child_pid=?,child_start_identity=?,updated_utc=?
                WHERE id=? AND owner=? AND lease_generation=?
                  AND status='running' AND phase='executing'
                """,
                (
                    pid,
                    start_identity,
                    occurred_text,
                    run_id,
                    owner,
                    lease_generation,
                ),
            )
            if result.rowcount != 1:
                raise FencingError("run changed while recording child identity")
            updated = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            return self._run_from_row(updated)

    def append_worker_event(
        self,
        run_id: str,
        *,
        owner: str,
        lease_generation: int,
        message: str,
        occurred_utc: datetime,
        level: Literal["info", "warning", "error"] = "info",
        code: str | None = None,
    ) -> EventRecord:
        if not message or len(message) > 4_096:
            raise ValueError("worker event message must be bounded")
        occurred_text = _utc_text(occurred_utc)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            self._require_fence(
                row, owner=owner, lease_generation=lease_generation,
            )
            if row["status"] != "running":
                raise TransitionError("worker events require an active run")
            self._append_event(
                connection,
                run_id=run_id,
                phase=row["phase"],
                progress=None,
                level=level,
                code=code,
                message=message,
                occurred_utc=occurred_text,
            )
            event = connection.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            return self._event_from_row(event)

    def take_over_for_recovery(
        self,
        run_id: str,
        *,
        owner: str,
        lease_generation: int,
        lease_expires_utc: datetime,
        occurred_utc: datetime,
    ) -> RunRecord:
        """Fence an orphan only after the caller has acquired writer.lock."""

        if not owner or lease_generation < 2:
            raise ValueError("recovery ownership requires a new fencing generation")
        occurred_text = _utc_text(occurred_utc)
        lease_text = _utc_text(lease_expires_utc)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] not in ACTIVE_STATUSES:
                raise TransitionError("only an active run can be recovered")
            if lease_generation <= row["lease_generation"]:
                raise FencingError("recovery generation must advance")
            result = connection.execute(
                """
                UPDATE runs
                SET owner=?,lease_generation=?,lease_expires_utc=?,updated_utc=?
                WHERE id=? AND owner=? AND lease_generation=?
                  AND status IN ('queued','running')
                """,
                (
                    owner,
                    lease_generation,
                    lease_text,
                    occurred_text,
                    run_id,
                    row["owner"],
                    row["lease_generation"],
                ),
            )
            if result.rowcount != 1:
                raise FencingError("orphan changed during recovery takeover")
            self._append_event(
                connection,
                run_id=run_id,
                phase=row["phase"],
                progress=None,
                level="warning",
                code="recovery_takeover",
                message="Startup recovery fenced the orphaned worker.",
                occurred_utc=occurred_text,
            )
            updated = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            return self._run_from_row(updated)

    def renew_lease(
        self,
        run_id: str,
        *,
        owner: str,
        lease_generation: int,
        lease_expires_utc: datetime,
        occurred_utc: datetime,
    ) -> RunRecord:
        """Advance an active worker lease under the existing fencing token."""

        occurred_text = _utc_text(occurred_utc)
        lease_text = _utc_text(lease_expires_utc)
        if lease_expires_utc <= occurred_utc:
            raise ValueError("renewed lease must expire in the future")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            self._require_fence(
                row, owner=owner, lease_generation=lease_generation,
            )
            if row["status"] not in ACTIVE_STATUSES:
                raise TransitionError("only an active run lease can be renewed")
            result = connection.execute(
                """
                UPDATE runs SET lease_expires_utc=?,updated_utc=?
                WHERE id=? AND owner=? AND lease_generation=?
                  AND status IN ('queued','running')
                """,
                (
                    lease_text,
                    occurred_text,
                    run_id,
                    owner,
                    lease_generation,
                ),
            )
            if result.rowcount != 1:
                raise FencingError("run changed during lease renewal")
            updated = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            return self._run_from_row(updated)

    def list_events(self, *, cursor: int = 0, limit: int = 1_000) -> list[EventRecord]:
        if cursor < 0 or not 1 <= limit <= 10_000:
            raise ValueError("invalid event cursor or limit")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE cursor>? ORDER BY cursor LIMIT ?",
                (cursor, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def _require_fence(
        self,
        row: sqlite3.Row,
        *,
        owner: str,
        lease_generation: int,
    ) -> None:
        if row["owner"] != owner or row["lease_generation"] != lease_generation:
            raise FencingError("worker fencing token does not match the run")

    def transition_run(
        self,
        run_id: str,
        *,
        status: Literal["queued", "running", "succeeded", "failed", "canceled"],
        phase: Literal["executing", "validating", "publishing"] | None,
        owner: str,
        lease_generation: int,
        message: str,
        occurred_utc: datetime,
        progress: float | None = None,
        level: Literal["info", "warning", "error"] = "info",
        error_code: str | None = None,
        recovery: bool = False,
    ) -> RunRecord:
        occurred_text = _utc_text(occurred_utc)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            self._require_fence(
                row, owner=owner, lease_generation=lease_generation,
            )
            old_status = row["status"]
            old_phase = row["phase"]
            if old_status in TERMINAL_STATUSES:
                raise TransitionError("terminal run cannot transition")
            if status == "succeeded":
                raise TransitionError("success requires a committed publication")

            legal = False
            next_phase = phase
            if old_status == "queued":
                legal = status == "running" and phase == "executing"
                if status == "failed":
                    legal = recovery and phase is None and error_code == "stale_job"
                    if not legal:
                        raise TransitionError(
                            "queued failure is reserved for stale_job recovery",
                        )
            elif old_status == "running":
                if status == "failed" and error_code:
                    if phase not in {None, old_phase}:
                        raise TransitionError(
                            "failed run must preserve its current phase",
                        )
                    legal = True
                    next_phase = old_phase
                elif status == "running":
                    expected = {
                        "executing": "validating",
                        "validating": "publishing",
                    }.get(old_phase)
                    legal = phase == expected
            if not legal:
                raise TransitionError(
                    f"illegal transition {old_status}/{old_phase} -> {status}/{phase}",
                )

            started = occurred_text if old_status == "queued" and status == "running" else None
            finished = occurred_text if status == "failed" else None
            result = connection.execute(
                """
                UPDATE runs
                SET status=?,phase=?,updated_utc=?,
                    started_utc=COALESCE(started_utc,?),
                    finished_utc=COALESCE(?,finished_utc),
                    error_code=?,error_message=?
                WHERE id=? AND owner=? AND lease_generation=?
                  AND status=? AND phase IS ?
                """,
                (
                    status,
                    next_phase,
                    occurred_text,
                    started,
                    finished,
                    error_code,
                    message if status == "failed" else None,
                    run_id,
                    owner,
                    lease_generation,
                    old_status,
                    old_phase,
                ),
            )
            if result.rowcount != 1:
                raise FencingError("run changed while applying fenced transition")
            self._append_event(
                connection,
                run_id=run_id,
                phase=next_phase,
                progress=progress,
                level="error" if status == "failed" else level,
                code=error_code,
                message=message,
                occurred_utc=occurred_text,
            )
            updated = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            return self._run_from_row(updated)

    def prepare_publication(
        self,
        *,
        publication_id: str,
        run_id: str,
        manifest: Mapping[str, Any],
        targets: Sequence[Mapping[str, Any]],
        owner: str,
        lease_generation: int,
        created_utc: datetime,
    ) -> None:
        if not targets:
            raise ValueError("publication requires at least one target")
        identity = [
            (target.get("target"), target.get("stage"), target.get("backup"))
            for target in targets
        ]
        if any(not all(item) for item in identity):
            raise ValueError("publication target paths are required")
        if any(
            len(values) != len(set(values))
            for values in zip(*identity, strict=True)
        ):
            raise ValueError("publication target, stage, and backup paths must be unique")
        created_text = _utc_text(created_utc)
        with self._transaction(synchronous_full=True) as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            self._require_fence(
                run, owner=owner, lease_generation=lease_generation,
            )
            if run["status"] != "running" or run["phase"] != "publishing":
                raise TransitionError("publication requires running/publishing")
            connection.execute(
                "INSERT INTO publications VALUES(?,?, 'prepared', ?, ?, NULL)",
                (publication_id, run_id, canonical_json(manifest), created_text),
            )
            for ordinal, target in enumerate(targets):
                connection.execute(
                    """
                    INSERT INTO publication_targets(
                        publication_id,ordinal,target_path,stage_path,backup_path,had_old
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        publication_id,
                        ordinal,
                        target["target"],
                        target["stage"],
                        target["backup"],
                        int(bool(target["had_old"])),
                    ),
                )

    def record_publication_step(
        self,
        *,
        publication_id: str,
        ordinal: int,
        step: Literal[
            "target_backup_intent",
            "target_backup_done",
            "stage_target_intent",
            "stage_target_done",
        ],
        run_id: str,
        owner: str,
        lease_generation: int,
    ) -> None:
        prerequisites = {
            "target_backup_intent": None,
            "target_backup_done": "target_backup_intent",
            "stage_target_intent": "target_backup_done",
            "stage_target_done": "stage_target_intent",
        }
        with self._transaction(synchronous_full=True) as connection:
            row = connection.execute(
                """
                SELECT target.*,publication.status AS publication_status,
                       publication.run_id,
                       run.owner,run.lease_generation,run.status AS run_status,
                       run.phase AS run_phase
                FROM publication_targets AS target
                JOIN publications AS publication ON publication.id=target.publication_id
                JOIN runs AS run ON run.id=publication.run_id
                WHERE target.publication_id=? AND target.ordinal=?
                  AND publication.run_id=?
                """,
                (publication_id, ordinal, run_id),
            ).fetchone()
            if row is None:
                raise KeyError((publication_id, ordinal))
            if row["publication_status"] != "prepared":
                raise TransitionError("committed publication journal cannot change")
            self._require_fence(
                row, owner=owner, lease_generation=lease_generation,
            )
            if row["run_status"] != "running" or row["run_phase"] != "publishing":
                raise TransitionError("journal update requires running/publishing")
            required = prerequisites[step]
            if required is not None and row[required] != 1:
                raise TransitionError(
                    f"publication step {step} requires {required}",
                )
            if row[step] == 1:
                return
            connection.execute(
                f"UPDATE publication_targets SET {step}=1 "  # noqa: S608 - enum whitelist
                "WHERE publication_id=? AND ordinal=?",
                (publication_id, ordinal),
            )

    def commit_publication_success(
        self,
        *,
        publication_id: str,
        run_id: str,
        artifact_ids: Sequence[str],
        owner: str,
        lease_generation: int,
        message: str,
        occurred_utc: datetime,
    ) -> RunRecord:
        if (
            not artifact_ids
            or any(not isinstance(item, str) or not item for item in artifact_ids)
            or len(set(artifact_ids)) != len(artifact_ids)
        ):
            raise ValueError("publication requires nonempty unique artifact IDs")
        occurred_text = _utc_text(occurred_utc)
        artifact_ids_json = canonical_json(list(artifact_ids))
        with self._transaction(synchronous_full=True) as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            publication = connection.execute(
                "SELECT * FROM publications WHERE id=? AND run_id=?",
                (publication_id, run_id),
            ).fetchone()
            target_evidence = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(
                         target_backup_intent AND target_backup_done
                         AND stage_target_intent AND stage_target_done
                       ) AS completed
                FROM publication_targets WHERE publication_id=?
                """,
                (publication_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            self._require_fence(
                run, owner=owner, lease_generation=lease_generation,
            )
            if (
                run["status"] != "running"
                or run["phase"] != "publishing"
                or publication is None
                or publication["status"] != "prepared"
            ):
                raise TransitionError(
                    "success requires a prepared publication and publishing run",
                )
            if (
                target_evidence["total"] < 1
                or target_evidence["completed"] != target_evidence["total"]
            ):
                raise TransitionError(
                    "publication journal is incomplete; success is forbidden",
                )
            connection.execute(
                "UPDATE publications SET status='committed',committed_utc=? WHERE id=?",
                (occurred_text, publication_id),
            )
            result = connection.execute(
                """
                UPDATE runs
                SET status='succeeded',updated_utc=?,finished_utc=?,artifact_ids_json=?
                WHERE id=? AND status='running' AND phase='publishing'
                  AND owner=? AND lease_generation=?
                """,
                (
                    occurred_text,
                    occurred_text,
                    artifact_ids_json,
                    run_id,
                    owner,
                    lease_generation,
                ),
            )
            if result.rowcount != 1:
                raise FencingError("run changed before publication commit")
            self._append_event(
                connection,
                run_id=run_id,
                phase="publishing",
                progress=1,
                level="info",
                code=None,
                message=message,
                occurred_utc=occurred_text,
            )
            updated = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            return self._run_from_row(updated)

    def list_publications(self) -> list[PublicationRecord]:
        with self.connection() as connection:
            publications = connection.execute(
                "SELECT rowid AS journal_order,* FROM publications ORDER BY rowid",
            ).fetchall()
            records = []
            for publication in publications:
                targets = connection.execute(
                    """
                    SELECT * FROM publication_targets
                    WHERE publication_id=? ORDER BY ordinal
                    """,
                    (publication["id"],),
                ).fetchall()
                records.append(PublicationRecord(
                    journal_order=publication["journal_order"],
                    id=publication["id"],
                    run_id=publication["run_id"],
                    status=publication["status"],
                    manifest=json.loads(publication["manifest_json"]),
                    targets=tuple(PublicationTargetRecord(
                        publication_id=target["publication_id"],
                        ordinal=target["ordinal"],
                        target_path=target["target_path"],
                        stage_path=target["stage_path"],
                        backup_path=target["backup_path"],
                        had_old=bool(target["had_old"]),
                        target_backup_intent=bool(target["target_backup_intent"]),
                        target_backup_done=bool(target["target_backup_done"]),
                        stage_target_intent=bool(target["stage_target_intent"]),
                        stage_target_done=bool(target["stage_target_done"]),
                    ) for target in targets),
                ))
        return records

    def mark_publication_rolled_back(
        self,
        *,
        publication_id: str,
        run_id: str,
        owner: str,
        lease_generation: int,
        message: str,
        occurred_utc: datetime,
    ) -> RunRecord:
        occurred_text = _utc_text(occurred_utc)
        with self._transaction(synchronous_full=True) as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            publication = connection.execute(
                "SELECT * FROM publications WHERE id=? AND run_id=?",
                (publication_id, run_id),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            self._require_fence(
                run, owner=owner, lease_generation=lease_generation,
            )
            if (
                publication is None
                or publication["status"] != "prepared"
                or run["status"] != "running"
                or run["phase"] != "publishing"
            ):
                raise TransitionError(
                    "rollback requires a prepared publication and publishing run",
                )
            connection.execute(
                "UPDATE publications SET status='rolled_back' WHERE id=?",
                (publication_id,),
            )
            result = connection.execute(
                """
                UPDATE runs
                SET status='failed',updated_utc=?,finished_utc=?,
                    error_code='publish_failed',error_message=?
                WHERE id=? AND status='running' AND phase='publishing'
                  AND owner=? AND lease_generation=?
                """,
                (
                    occurred_text,
                    occurred_text,
                    message,
                    run_id,
                    owner,
                    lease_generation,
                ),
            )
            if result.rowcount != 1:
                raise FencingError("run changed before publication rollback")
            self._append_event(
                connection,
                run_id=run_id,
                phase="publishing",
                progress=None,
                level="error",
                code="publish_failed",
                message=message,
                occurred_utc=occurred_text,
            )
            updated = connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            return self._run_from_row(updated)
