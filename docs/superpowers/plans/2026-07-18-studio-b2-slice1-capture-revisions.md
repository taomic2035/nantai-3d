# Studio B2 Slice 1 Capture Revisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mutable ingest-only truth boundary with a safely migrated SQLite v2 ledger, immutable `CaptureRevision` bundles, and sanitized read-only capture-revision queries while preserving the existing `photos/` compatibility projection.

**Architecture:** The ledger migrates a byte-valid v1 schema to the complete v2 table set inside one `BEGIN IMMEDIATE` transaction and refuses unknown or altered schemas. A new capture-revision module derives a private canonical manifest from the already verified ingest artifact, publishes an absent-only immutable bundle below `.nantai-studio/artifacts/capture/`, records it transactionally, and only then refreshes the non-authoritative `photos/` projection. Studio exposes sanitized summaries from SQLite; it never reads arbitrary paths or returns private manifest fields.

**Tech Stack:** Python 3.11, Pydantic v2, standard-library SQLite in WAL mode, existing Windows/NTFS write-through backend, pytest, native `http.server`.

## Global Constraints

- Work only on `main`; do not create a branch or worktree.
- Stage and commit only explicit paths; never use `git add -A` or `git commit -a`.
- End every Codex-created commit with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Treat `.nantai-studio/` as private, Git-ignored runtime state.
- Revision IDs are opaque random identifiers; SHA-256 digests remain separate content identities.
- Unknown, malformed, hash-damaged, linked, or out-of-root data fails closed.
- Existing `photos/` is a compatibility projection, never revision identity or source truth.
- Write capability remains limited to the verified Windows/NTFS path; macOS and other unverified filesystems stay read-only.
- HTTP responses expose no absolute path, original source name, EXIF value, GPS value, environment dump, or private manifest.
- No new third-party dependency is introduced.
- Each task starts with a failing test and ends with focused plus regression tests.

## Scope Boundary

This plan implements delivery slice 1 only:

1. complete SQLite v2 schema and v1-to-v2 migration;
2. immutable capture manifest and bundle publication primitives;
3. capture revision recording and sanitized read-only queries;
4. ingest integration with a non-authoritative `photos/` projection.

It does not implement `SfmBundle`, training handoffs, import descriptors, scene revisions, activation, comparison UI, cancellation, retry, pins, or garbage collection behavior. Their v2 tables are created now so those features do not require an unplanned schema rewrite.

## File Map

- Modify `pipeline/studio_ledger.py`: schema migration, v2 records, append-only capture APIs.
- Create `pipeline/studio_revisions.py`: capture manifest validation, bundle preparation, absent-only publisher, compatibility projection.
- Modify `pipeline/studio_jobs.py`: managed directories and ingest publication orchestration.
- Modify `pipeline/studio_server.py`: sanitized capture revision endpoints.
- Modify `tests/test_studio_ledger.py`: migration, fingerprint, append-only ledger tests.
- Create `tests/test_studio_capture_revisions.py`: manifest and immutable publication tests.
- Modify `tests/test_studio_publication.py`: ordering and failure-preservation tests.
- Modify `tests/test_studio_job_http.py`: sanitized endpoint and integrated ingest tests.
- Modify `README.md`: current Studio boundary and revision query documentation.

---

### Task 1: SQLite v2 Schema and Atomic Migration

**Files:**
- Modify: `pipeline/studio_ledger.py`
- Test: `tests/test_studio_ledger.py`

**Interfaces:**
- Consumes: existing schema-v1 tables and `StudioLedger.initialize()`.
- Produces: `SCHEMA_VERSION = 2`, `EXPECTED_V1_SCHEMA_FINGERPRINT`, `EXPECTED_V2_SCHEMA_FINGERPRINT`, and a v2 database containing every table named in design section 6.

- [ ] **Step 1: Write migration and fail-closed tests**

Add tests that construct a real v1 database from the frozen v1 SQL, insert a run, initialize it with the new ledger, and assert preservation plus the v2 table set:

```python
from pipeline import studio_ledger as ledger_module


def _write_v1_database(path):
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


def test_initialize_migrates_exact_v1_to_v2_without_losing_runs(tmp_path):
    database = tmp_path / ".nantai-studio/studio.db"
    _write_v1_database(database)
    old = StudioLedger(database)
    old.initialize(target_version=1)
    _create_run(old)

    StudioLedger(database).initialize()

    with StudioLedger(database).connection() as connection:
        metadata = dict(connection.execute("SELECT key,value FROM meta"))
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert metadata["schema_version"] == "2"
    assert metadata["schema_fingerprint"] == ledger_module.EXPECTED_V2_SCHEMA_FINGERPRINT
    assert StudioLedger(database).get_run("run-001").command == "ingest"
    assert {
        "capture_revisions", "sfm_bundles", "training_handoffs",
        "import_descriptors", "scene_revisions", "scene_inputs",
        "spatial_artifacts", "verification_records", "active_scene",
        "revision_pins", "revision_leases", "publication_intents", "gc_plans",
    }.issubset(tables)
```

Add separate tests proving:

- a weakened v1 fingerprint is rejected before migration;
- a declared v2 database with an altered table is rejected;
- schema version 99 remains rejected as newer;
- `PRAGMA foreign_keys` remains `1` after migration;
- the migration rolls back completely when one v2 statement is fault-injected.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_ledger.py -q
```

Expected: failures because `_SCHEMA_V1_SQL`, v2 fingerprints, `target_version`, and v2 tables do not exist.

- [ ] **Step 3: Freeze v1 SQL and define the complete v2 schema**

Rename the current `_SCHEMA_SQL` to `_SCHEMA_V1_SQL`. Define `_SCHEMA_V2_STATEMENTS` as a tuple of one-statement strings so migration stays inside the caller-owned transaction. The tables and minimum columns are:

```python
SCHEMA_VERSION = 2

_SCHEMA_V2_STATEMENTS = (
    """
    CREATE TABLE capture_revisions (
        id TEXT PRIMARY KEY CHECK(id GLOB 'capture-[0-9a-f]*'),
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        provenance TEXT NOT NULL CHECK(provenance IN ('measured','synthetic','unknown')),
        source_count INTEGER NOT NULL CHECK(source_count >= 1),
        output_count INTEGER NOT NULL CHECK(output_count >= 1),
        created_by_run TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE sfm_bundles (
        id TEXT PRIMARY KEY,
        capture_revision_id TEXT NOT NULL REFERENCES capture_revisions(id) ON DELETE RESTRICT,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        frame_id TEXT NOT NULL,
        created_by_run TEXT REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE training_handoffs (
        id TEXT PRIMARY KEY,
        capture_revision_id TEXT NOT NULL REFERENCES capture_revisions(id) ON DELETE RESTRICT,
        sfm_bundle_id TEXT NOT NULL REFERENCES sfm_bundles(id) ON DELETE RESTRICT,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        created_by_run TEXT REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE import_descriptors (
        id TEXT PRIMARY KEY,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        created_by_run TEXT REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE scene_revisions (
        id TEXT PRIMARY KEY,
        parent_revision_id TEXT REFERENCES scene_revisions(id) ON DELETE RESTRICT,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
        bundle_relpath TEXT NOT NULL UNIQUE,
        requested_engine TEXT NOT NULL,
        actual_engine TEXT NOT NULL,
        toolchain_capsule_digest TEXT NOT NULL CHECK(length(toolchain_capsule_digest)=64),
        created_by_run TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE RESTRICT,
        created_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE scene_inputs (
        scene_revision_id TEXT NOT NULL REFERENCES scene_revisions(id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        input_kind TEXT NOT NULL,
        input_id TEXT NOT NULL,
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
        PRIMARY KEY(scene_revision_id, ordinal)
    )
    """,
    """
    CREATE TABLE spatial_artifacts (
        id TEXT PRIMARY KEY,
        scene_revision_id TEXT NOT NULL REFERENCES scene_revisions(id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        format TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256)=64),
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
        scene_revision_id TEXT NOT NULL REFERENCES scene_revisions(id) ON DELETE RESTRICT,
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
        manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
        destination_relpath TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('prepared','committed','quarantined')),
        created_utc TEXT NOT NULL,
        finished_utc TEXT
    )
    """,
    """
    CREATE TABLE gc_plans (
        id TEXT PRIMARY KEY,
        observed_active_generation INTEGER NOT NULL CHECK(observed_active_generation >= 0),
        mark_set_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('planned','tombstoning','cooling','completed','aborted')),
        created_utc TEXT NOT NULL,
        finished_utc TEXT
    )
    """,
)
```

Add append-only update/delete triggers for `capture_revisions`, `sfm_bundles`, `training_handoffs`, `import_descriptors`, `scene_revisions`, `scene_inputs`, `spatial_artifacts`, and `verification_records`. `publication_intents` is a fenced lifecycle journal whose status may advance from `prepared` to one terminal value; pointer, pin, lease, intent, and GC lifecycle tables are the only mutable v2 tables.

- [ ] **Step 4: Implement exact-version initialization and migration**

Add:

```python
def _apply_statements(connection, statements):
    for statement in statements:
        connection.execute(statement)


def _migrate_v1_to_v2(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        _apply_statements(connection, _SCHEMA_V2_STATEMENTS)
        fingerprint = _schema_fingerprint(connection)
        connection.execute(
            "UPDATE meta SET value='2' WHERE key='schema_version'"
        )
        connection.execute(
            "UPDATE meta SET value=? WHERE key='schema_fingerprint'",
            (fingerprint,),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
```

`StudioLedger.initialize()` must:

1. create the complete v2 schema for an empty database;
2. validate both stored and actual v1 fingerprints before migration;
3. migrate v1 once;
4. validate the resulting v2 fingerprint;
5. reject every other version without modifying bytes.

The test-only `target_version=1` keyword may create or validate v1 but must reject a v2 database as newer. Production callers use the default and never pass it.

- [ ] **Step 5: Run focused and regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_ledger.py tests/test_studio_publication.py tests/test_studio_recovery.py -q
```

Expected: all selected tests pass and existing run/publication records behave unchanged.

- [ ] **Step 6: Commit and push the migration**

```bash
git add pipeline/studio_ledger.py tests/test_studio_ledger.py
git commit -m "feat(studio): migrate ledger to schema v2" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 2: Private Capture Manifest Contract

**Files:**
- Create: `pipeline/studio_revisions.py`
- Create: `tests/test_studio_capture_revisions.py`

**Interfaces:**
- Consumes: `IngestManifest` returned by `verify_ingest_artifact()`.
- Produces: `CaptureRevisionManifest`, `CapturePayload`, `build_capture_manifest()`, `canonical_manifest_bytes()`, `capture_manifest_digest()`.

- [ ] **Step 1: Write strict manifest tests**

Test one mixed photo/video ingest fixture and assert:

```python
manifest = build_capture_manifest(
    revision_id="capture-" + "a" * 32,
    ingest=verified_ingest,
    synthetic=True,
    created_utc=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
)
assert manifest.schema_version == 1
assert manifest.kind == "capture-revision"
assert manifest.output_count == verified_ingest.total_output_frames
assert manifest.source_count == len(verified_ingest.sources)
assert manifest.provenance == "synthetic"
assert capture_manifest_digest(manifest) == hashlib.sha256(
    canonical_manifest_bytes(manifest)
).hexdigest()
assert canonical_manifest_bytes(manifest).endswith(b"\n")
```

Also assert rejection of an invalid revision ID, duplicate logical output path, non-UTC timestamp, missing payload, NaN, and any extra field.

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_capture_revisions.py -q
```

Expected: import failure because `pipeline.studio_revisions` does not exist.

- [ ] **Step 3: Implement frozen Pydantic contracts**

Define:

```python
class CapturePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    logical_path: str
    sha256: Sha256
    byte_length: int = Field(ge=1)
    source_kind: Literal["photo", "video-frame"]
    source_ordinal: int = Field(ge=0)
    frame_index: int | None = Field(default=None, ge=0)


class CaptureRevisionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    kind: Literal["capture-revision"] = "capture-revision"
    revision_id: str = Field(pattern=r"^capture-[0-9a-f]{32}$")
    created_utc: datetime
    provenance: Literal["measured", "synthetic", "unknown"]
    synthetic: bool
    source_count: int = Field(ge=1)
    output_count: int = Field(ge=1)
    ingest_session_id: str = Field(pattern=r"^ingest-[0-9a-f]{64}$")
    ingest_manifest_sha256: Sha256
    ingest_parameters: IngestParams
    payloads: tuple[CapturePayload, ...]
```

`build_capture_manifest()` derives payloads only from verified `IngestManifest.sources[*].outputs`. It records GPS presence only in the private embedded ingest manifest; the public projection in Task 6 does not expose it.

Canonical bytes use sorted keys, ASCII JSON, finite numbers, compact separators, and exactly one LF:

```python
def canonical_manifest_bytes(manifest):
    payload = manifest.model_dump(mode="json")
    return (canonical_json(payload) + "\n").encode("ascii")
```

- [ ] **Step 4: Run tests and static checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_capture_revisions.py -q
.venv/bin/python -m ruff check pipeline/studio_revisions.py tests/test_studio_capture_revisions.py
```

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit and push the contract**

```bash
git add pipeline/studio_revisions.py tests/test_studio_capture_revisions.py
git commit -m "feat(studio): define immutable capture manifests" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 3: Append-Only Capture Records

**Files:**
- Modify: `pipeline/studio_ledger.py`
- Modify: `tests/test_studio_ledger.py`

**Interfaces:**
- Consumes: capture manifest digest, absent-only bundle relative path, a running/publishing ingest run, and a fenced publication intent.
- Produces: `CaptureRevisionRecord`, `prepare_capture_publication()`, `commit_capture_publication()`, `commit_capture_run_success()`, `get_capture_revision()`, `list_capture_revisions()`.

- [ ] **Step 1: Write record, idempotency, and tamper tests**

Create a running/publishing ingest run, prepare its intent, then assert:

```python
ledger.prepare_capture_publication(
    intent_id="capture-publication-" + "c" * 32,
    run_id="run-001",
    revision_id="capture-" + "a" * 32,
    manifest_digest="b" * 64,
    bundle_relpath=".nantai-studio/artifacts/capture/capture-" + "a" * 32,
    owner="owner-a",
    lease_generation=1,
    created_utc=_now(),
)
record = ledger.commit_capture_publication(
    intent_id="capture-publication-" + "c" * 32,
    revision_id="capture-" + "a" * 32,
    manifest_digest="b" * 64,
    bundle_relpath=".nantai-studio/artifacts/capture/capture-" + "a" * 32,
    provenance="synthetic",
    source_count=6,
    output_count=11,
    created_by_run="run-001",
    owner="owner-a",
    lease_generation=1,
    created_utc=_now(),
)
assert ledger.get_capture_revision(record.id) == record
assert ledger.list_capture_revisions() == [record]
assert ledger.get_run("run-001").status == "running"

finished = ledger.commit_capture_run_success(
    run_id="run-001",
    revision_id=record.id,
    owner="owner-a",
    lease_generation=1,
    message="Immutable capture and compatibility projection published.",
    occurred_utc=_now(),
)
assert finished.status == "succeeded"
assert finished.artifact_ids == (record.id,)
```

The same intent and revision values are idempotent during recovery. Reusing the run ID, revision ID, intent ID, or bundle path with different values raises `RevisionConflictError`. Raw SQL update/delete of `capture_revisions` raises an append-only database error. An intent cannot commit until its run is `running/publishing`, and run success cannot commit until its matching capture intent and record are committed.

- [ ] **Step 2: Run focused test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_ledger.py -q
```

Expected: missing record class and methods.

- [ ] **Step 3: Implement the immutable record API**

Add:

```python
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
```

`prepare_capture_publication()` and `commit_capture_publication()` run with `synchronous_full=True` and require the referenced run to be `running/publishing` with `command='ingest'`. The commit transaction inserts the immutable capture row and advances its matching intent to `committed`, but deliberately leaves the run active so Task 5 can finish the compatibility projection. `commit_capture_run_success()` is the only transition that stores the revision ID in `runs.artifact_ids_json` and marks that run succeeded. Each method returns an identical existing result only when every field matches. Absolute paths are rejected; `bundle_relpath` must exactly match `.nantai-studio/artifacts/capture/<revision_id>`.

- [ ] **Step 4: Run ledger and publication regressions**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_ledger.py tests/test_studio_publication.py tests/test_studio_crash_recovery.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit and push the record API**

```bash
git add pipeline/studio_ledger.py tests/test_studio_ledger.py
git commit -m "feat(studio): record immutable capture revisions" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 4: Absent-Only Capture Bundle Publisher

**Files:**
- Modify: `pipeline/studio_revisions.py`
- Modify: `tests/test_studio_capture_revisions.py`
- Modify: `tests/test_studio_publication.py`

**Interfaces:**
- Consumes: verified ingest stage, `WindowsNtfsDurabilityBackend`, held writer/publish locks.
- Produces: `CaptureBundlePublisher.prepare()`, `CaptureBundlePublisher.publish()`, `PublishedCapture`.

- [ ] **Step 1: Write publication and fault-injection tests**

Cover these exact outcomes:

- prepared bundle contains `manifest.json`, `ingest_manifest.json`, and `payload/<logical_path>`;
- all hashes match before publication;
- destination must be absent and contain no symlink/junction component;
- publication uses write-through move and flushes both parents;
- a fault before the move leaves no visible revision;
- a fault after the move but before the ledger record leaves a complete orphan that startup recovery can verify and roll forward;
- incomplete or hash-damaged orphan moves to quarantine;
- an existing immutable destination is never replaced.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_capture_revisions.py tests/test_studio_publication.py -q
```

Expected: missing publisher types and behavior.

- [ ] **Step 3: Implement bundle preparation**

Use fixed paths:

```python
work_bundle = root / ".nantai-studio/work" / run_id / "capture-bundle"
destination = root / ".nantai-studio/artifacts/capture" / revision_id
quarantine = root / ".nantai-studio/quarantine" / f"{revision_id}-{uuid.uuid4().hex}"
```

`prepare()` verifies the ingest stage, creates `payload/`, copies each declared output once, re-hashes source and copy, writes the private ingest manifest and canonical capture manifest, flushes every file and directory, and re-verifies the complete tree.

- [ ] **Step 4: Implement publication ordering**

`publish()` requires the writer lock, acquires the publish lock, checks the original concurrency snapshot, calls `prepare_capture_publication()`, write-through moves the absent bundle directory, verifies every byte at the destination, and calls `commit_capture_publication()`. The run deliberately remains `running/publishing` until Task 5 refreshes the compatibility projection. The publisher returns:

```python
@dataclass(frozen=True)
class PublishedCapture:
    revision: CaptureRevisionRecord
    destination: Path
```

The database commit occurs only after destination verification. Recovery consults the intent plus canonical manifest and never infers success from the directory name.

- [ ] **Step 5: Run publication and real-process crash tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_studio_capture_revisions.py \
  tests/test_studio_publication.py \
  tests/test_studio_crash_recovery.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit and push the publisher**

```bash
git add pipeline/studio_revisions.py tests/test_studio_capture_revisions.py tests/test_studio_publication.py
git commit -m "feat(studio): publish immutable capture bundles" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 5: Ingest Integration and Compatibility Projection

**Files:**
- Modify: `pipeline/studio_jobs.py`
- Modify: `pipeline/studio_revisions.py`
- Modify: `tests/test_studio_publication.py`
- Modify: `tests/test_studio_crash_recovery.py`

**Interfaces:**
- Consumes: `CaptureBundlePublisher` and the existing verified ingest stage.
- Produces: one immutable capture revision per successful ingest run; `photos/` remains a replaceable projection.

- [ ] **Step 1: Write integration-order tests**

Assert:

1. successful ingest creates one immutable capture row and bundle;
2. run `artifact_ids` contains the capture revision ID;
3. `photos/` bytes equal the captured payload after success;
4. failure while refreshing `photos/` does not remove or mutate the committed capture;
5. a failed ingest leaves the previous `photos/` projection unchanged;
6. two ingests preserve both immutable bundles while `photos/` reflects only the latest successful capture.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_publication.py tests/test_studio_crash_recovery.py -q
```

Expected: current ingest publishes only mutable `photos/`.

- [ ] **Step 3: Create all managed revision directories**

Extend `JobService.initialize()` to validate and create:

```python
for relative in (
    "work", "backups", "logs", "quarantine", "artifacts",
    "artifacts/capture", "artifacts/sfm", "artifacts/handoff",
    "artifacts/import", "artifacts/scene", "inbox", "tombstones", "cache",
):
    path = state_root / PurePosixPath(relative)
    if _path_exists(path) and (not path.is_dir() or _is_linklike(path)):
        raise JobContractError(f"Studio managed path is unsafe: {relative}")
    path.mkdir(exist_ok=True)
    _require_real_directory(path, label=f"Studio managed {relative}")
```

Every existing component must be a real directory below the fixed state root; links and wrong types fail closed.

- [ ] **Step 4: Publish immutable truth before refreshing the projection**

Change validating/publishing orchestration to:

```python
published = capture_publisher.publish(
    run=run,
    invocation=invocation,
    expected_snapshot=snapshot,
    synthetic=False,
    occurred_utc=self._now(),
)
projection_publisher.refresh(
    capture=published,
    expected_snapshot=snapshot,
    occurred_utc=self._now(),
)
ledger.commit_capture_run_success(
    run_id=run.id,
    revision_id=published.revision.id,
    owner=run.owner,
    lease_generation=run.lease_generation,
    message="Immutable capture and compatibility projection published.",
    occurred_utc=self._now(),
)
```

The projection copies from the immutable bundle to a fresh work directory, verifies hashes, and reuses the existing journaled `photos/` replacement. A crash after the immutable commit but before run success is recovered from the committed capture intent, resumes only the projection, and then calls `commit_capture_run_success()`. No downstream code treats `photos/` as the revision owner.

- [ ] **Step 5: Run integration and recovery tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_studio_publication.py \
  tests/test_studio_crash_recovery.py \
  tests/test_studio_recovery.py \
  tests/test_studio_writer_lock.py -q
```

Expected: all selected tests pass; existing B1 recovery remains green.

- [ ] **Step 6: Commit and push ingest integration**

```bash
git add pipeline/studio_jobs.py pipeline/studio_revisions.py \
  tests/test_studio_publication.py tests/test_studio_crash_recovery.py
git commit -m "feat(studio): retain immutable ingest revisions" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 6: Sanitized Read-Only Capture API

**Files:**
- Modify: `pipeline/studio_server.py`
- Modify: `tests/test_studio_job_http.py`

**Interfaces:**
- Consumes: `StudioLedger.list_capture_revisions()` and `get_capture_revision()`.
- Produces: `GET /api/revisions/captures` and `GET /api/revisions/captures/{id}`.

- [ ] **Step 1: Write HTTP privacy and routing tests**

Assert the collection response:

```python
assert payload == {
    "schema_version": 1,
    "items": [{
        "id": revision_id,
        "manifest_digest": "b" * 64,
        "provenance": "synthetic",
        "source_count": 6,
        "output_count": 11,
        "created_at": "2026-07-18T08:00:00.000000+00:00",
        "created_by_run": run_id,
    }],
}
```

Assert the detail route returns the same sanitized shape, unknown IDs return structured 404, malformed IDs return 404, both endpoints set `Cache-Control: no-store`, and serialized responses contain none of:

```python
for forbidden in (
    str(root), "source_path", "gps", "lat", "lon",
    "exif_datetime", "bundle_relpath", "ingest_manifest",
):
    assert forbidden not in body_text
```

- [ ] **Step 2: Run HTTP tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_job_http.py -q
```

Expected: both routes return `api_not_found`.

- [ ] **Step 3: Implement one sanitizer and two GET routes**

Add:

```python
def _capture_revision_payload(record):
    return {
        "id": record.id,
        "manifest_digest": record.manifest_digest,
        "provenance": record.provenance,
        "source_count": record.source_count,
        "output_count": record.output_count,
        "created_at": record.created_utc,
        "created_by_run": record.created_by_run,
    }
```

Serve the routes only when a valid managed ledger is available. A read-only server may initialize the ledger for bounded reads but must not create runtime state merely because a GET was requested; absent database returns an empty collection and detail 404.

- [ ] **Step 4: Run server and contract regressions**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_studio_job_http.py \
  tests/test_studio_server.py \
  tests/test_studio_capabilities.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit and push the API**

```bash
git add pipeline/studio_server.py tests/test_studio_job_http.py
git commit -m "feat(studio): expose sanitized capture revisions" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 7: Slice 1 Documentation and Full Gate

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-15-studio-b2-immutable-revisions-design.md`

**Interfaces:**
- Consumes: verified Slice 1 behavior.
- Produces: accurate public capability matrix and a durable implementation checkpoint.

- [ ] **Step 1: Update capability wording**

Document:

- ingest writes one immutable CaptureRevision under private ignored storage;
- `photos/` is a compatibility projection;
- capture list/detail APIs are sanitized and read-only;
- scene revisions, activation, cloud handoff, and comparison remain outside Slice 1;
- write mode remains Windows/NTFS-only while macOS remains a read-only inspection environment.

Update the design status to `Slice 1 implemented and verified` only after the full gate below succeeds.

- [ ] **Step 2: Run the complete repository gate**

Run:

```bash
.venv/bin/python -m pytest tests -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
.venv/bin/python -m ruff check pipeline tests
git diff --check
```

Expected: every test passes, Ruff reports no errors, and `git diff --check` reports no whitespace errors. Known platform skips remain skips and are reported by count.

- [ ] **Step 3: Verify privacy and repository scope**

Run:

```bash
git status --short
git diff --name-only
git ls-files .nantai-studio
rg -n "/Users/|[A-Za-z]:\\\\|gps|latitude|longitude" \
  README.md docs/superpowers pipeline/studio_*.py tests/test_studio*.py
```

Expected:

- `git ls-files .nantai-studio` prints nothing;
- only Slice 1 files plus pre-existing unrelated WIP are modified;
- any privacy-term match is a test assertion, schema field, or explicit documentation prohibition, not runtime private data.

- [ ] **Step 4: Commit and push the verified checkpoint**

```bash
git add README.md \
  docs/superpowers/specs/2026-07-15-studio-b2-immutable-revisions-design.md
git commit -m "docs(studio): record capture revision boundary" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

## Plan Self-Review

- Spec coverage: Slice 1 covers design sections 5, 6, 7, 9 publication primitives, 12 read-only capture routes, 13 privacy, 15 recovery, 19 L0/L1 gates, 21.1/21.2 tests, and delivery slice 1. Other sections remain explicitly outside this slice.
- Placeholder scan: the plan contains no unresolved implementation placeholder; every task names files, interfaces, test command, expected RED/GREEN result, and commit boundary.
- Type consistency: `CaptureRevisionManifest`, `CapturePayload`, `CaptureRevisionRecord`, `prepare_capture_publication()`, `commit_capture_publication()`, `commit_capture_run_success()`, `CaptureBundlePublisher`, `PublishedCapture`, and both API routes retain the same names and fields across tasks.
- Safety check: immutable bundle commit precedes compatibility projection; GET routes never open a client-supplied path; v1 migration validates its fingerprint before changing the database.
