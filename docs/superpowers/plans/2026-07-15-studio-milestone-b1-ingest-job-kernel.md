# Studio Milestone B1 Ingest Job Kernel Implementation Plan

> **Execution requirement:** follow this plan task by task with TDD and fresh
> verification. Keep every commit on the sole `main` branch and include the
> required Codex co-author trailer.

**Goal:** Deliver one end-to-end, ingest-only local job path whose SQLite
ledger, cross-process writer lease, isolated staging, crash-recoverable
publication, secure loopback HTTP contract, and minimal Studio controls are
safe enough to become the first real read-write capability.

**Architecture:** Keep `pipeline.studio_server` as an HTTP/static composition
root. Put durable state in `pipeline.studio_ledger` and execution/publication in
`pipeline.studio_jobs`. Consume the already committed B0 ingest verifier as a
hard publication prerequisite. The default server remains read-only. B1 never
touches alignment, reconstruction, Gaussian, world, or AssetRegistry code.

**Runtime:** Python 3.11+, SQLite WAL, Pydantic v2, standard-library subprocess,
pywin32 311+ for Windows locking/process identity/NTFS write-through durability,
browser ES modules, pytest, Node test runner.

---

## Fixed scope and truth boundary

- New backend modules: `pipeline/studio_ledger.py`, `pipeline/studio_jobs.py`.
- Server integration: `pipeline/studio_server.py` only for startup readiness,
  capability composition, request validation, routing, and error mapping.
- Minimal local UI integration: capability-token handling, ingest parameter
  confirmation, job submission, cursor polling, and terminal snapshot refresh.
- The only executable command is `ingest`; its fixed input is `input/`, fixed
  staging target is `.nantai-studio/work/<run-id>/photos/`, and fixed formal
  target is `photos/`.
- B1 does not implement cancel/retry, `canceled`, process-group termination,
  Windows Job Objects, reconstruct/world/validate-assets, file upload, arbitrary
  shell/path/environment input, or a separate daemon.
- A succeeded job proves verified byte publication. It does not promote
  synthetic/proxy inputs to measured geometry, metric alignment, or full 3DGS.
- Preserve and never stage the existing alignment/recon/Gaussian/HANDOFF WIP.

## State and lock contracts

Normal execution:

```text
queued
  -> running/executing
  -> running/validating
  -> running/publishing
  -> succeeded
```

Failure execution is `running/* -> failed`. Recovery may additionally perform
`queued -> failed(error_code=stale_job)` after current schema, capability, and
snapshot revalidation fails. Terminal states are irreversible. Every state or
phase change and its event append occur in one SQLite transaction. Only the
publication point-of-no-return transaction may write `succeeded`.

There are two legal acquisition sequences:

```text
submit:              writer.lock -> short BEGIN IMMEDIATE
publish/recovery:    writer.lock -> publish.lock -> short SQLite transaction
```

No file lock may ever be acquired while a SQLite write transaction is open.

The writer lock handle lives until validation, publication, and terminal ledger
commit finish. Lease generation is a fencing token; heartbeat/state writes must
match run, owner, and generation. An expired lease is never stolen while the OS
writer lock remains held.

---

### Task 1: Build the append-only SQLite ledger

**Files:**
- Create: `pipeline/studio_ledger.py`
- Create: `tests/test_studio_ledger.py`

- [ ] Write RED tests for schema creation, unknown-newer-schema rejection,
  WAL/foreign-key/busy-timeout configuration, canonical parameter/snapshot
  JSON, and one active-run partial unique index.
- [ ] Write RED tests for every allowed transition, every illegal/terminal
  rollback, the recovery-only queued failure, and compare-and-swap fencing by
  owner plus lease generation.
- [ ] Write RED tests proving status/phase and event are one transaction,
  `events.cursor` and per-run sequence are monotonic, and triggers reject event
  update/delete.
- [ ] Write RED tests for request idempotency: identical canonical payload
  returns the original run; a reused request ID with different payload is a
  conflict.
- [ ] Implement schema-v1 tables: `meta`, `runs`, `events`, `request_dedup`,
  `publications`, and `publication_targets`. Provide typed ledger methods, not
  caller-supplied SQL fragments.
- [ ] Verify `python -m pytest -q tests/test_studio_ledger.py` and focused ruff.

### Task 2: Add cross-process locks and command snapshots

**Files:**
- Create: `pipeline/studio_jobs.py`
- Create: `tests/test_studio_writer_lock.py`
- Create: `tests/test_studio_jobs.py`

- [ ] Write RED two-process tests for non-blocking `writer.lock` contention,
  crash release, active-lock no-takeover, and independent `publish.lock`.
- [ ] Add lock-order instrumentation tests proving submit and publication use
  only the two approved sequences and no code waits for a file lock inside a
  SQLite transaction.
- [ ] Implement a private `ProjectFileLock`: POSIX `fcntl.flock`; Windows
  pywin32 `LockFileEx`; deterministic acquire/release and handle ownership.
- [ ] Define immutable `ConcurrencySnapshot` evidence for the supported
  `input/` source manifest plus formal `photos/` absent/tree-manifest digest.
  Reject links, junctions, non-regular files, scan errors, and input mutation
  during hashing. Exclude `.nantai-studio/` and unrelated paths by construction.
- [ ] Write RED registry tests for unknown command/field rejection, all
  `IngestParams` bounds, fixed input/stage/target/cwd, exact argv,
  `shell=False`, and a minimal inherited environment allowlist.
- [ ] Implement an ingest-only `CommandRegistry` that reuses `IngestParams`
  and `verify_ingest_artifact`; no dynamic module, executable, path, or
  environment selection is accepted.
- [ ] Verify the lock tests in real child processes and registry/snapshot tests.

### Task 3: Implement bounded subprocess execution

**Files:**
- Modify: `pipeline/studio_jobs.py`
- Create: `tests/test_studio_process.py`
- Create: `tests/helpers/studio_process_fixture.py`

- [ ] Write RED helper-process tests for success, nonzero exit, simultaneous
  stdout/stderr draining, invalid UTF-8 replacement, long-line truncation,
  secret redaction, and bounded log rotation.
- [ ] Implement the B1 `ProcessController` with argv arrays, fixed project cwd,
  `shell=False`, minimal environment, concurrent readers, structured bounded
  events, and a complete raw-log file that rotates by configured size.
- [ ] Persist child PID plus an OS process-start identity immediately after
  spawn. Provide a read-only liveness probe that distinguishes a reused PID
  from the original child.
- [ ] Do not implement cancellation. Normal server shutdown waits for the
  non-daemon worker. Capability must advertise `cancel:false` and `retry:false`.
- [ ] Verify process tests and prove no shell string or arbitrary environment
  reaches the child.

### Task 4: Implement durable single-target publication and recovery

**Files:**
- Modify: `pipeline/studio_jobs.py`
- Modify: `pyproject.toml`
- Create: `tests/test_studio_publication.py`
- Create: `tests/test_studio_recovery.py`
- Create: `tests/helpers/studio_crash_fixture.py`

- [ ] Write RED tests proving invalid/extra/missing staged bytes and changed
  input/target snapshots never modify formal `photos/`.
- [ ] Implement a generic publication manifest and target journal even though
  B1 registers only one target. Persist `had_old`, stage, target, backup,
  target-to-backup intent/done, and stage-to-target intent/done.
- [ ] Add `pywin32>=311` as an explicit Windows optional extra, runtime version
  and symbol checks, and a clean-environment dependency test. A missing or old
  dependency must degrade startup to read-only rather than raise an import-time
  error.
- [ ] Implement the B1 `WindowsNtfsDurabilityBackend` with Win32
  `FlushFileBuffers` and write-through move/replace. Startup self-test must
  verify local NTFS and every required operation. B1 deliberately leaves POSIX
  write capability read-only until a later milestone supplies real POSIX
  killed-process/restart evidence; Windows results are never used to enable it.
- [ ] Fix every publication path: formal target `photos/`; stage
  `.nantai-studio/work/<run-id>/photos`; backup
  `.nantai-studio/backups/<publication-id>/photos`. Immediately before every
  intent and rename, recheck realpath containment, expected absent/directory
  type, and absence of symlinks/junctions/non-regular entries.
- [ ] Add adversarial race tests that replace stage, target, backup, or an
  ancestor with a symlink/junction between snapshot and each rename. Publication
  must fail closed without touching an out-of-root path.
- [ ] Before each rename persist intent with SQLite `synchronous=FULL`; after
  rename flush relevant parents, then persist done.
- [ ] After formal target re-verification, use one SQLite transaction as the
  sole point of no return: publication committed, run succeeded, artifact IDs,
  and terminal event.
- [ ] Add fault injection before/after every intent, rename, flush, done, and
  point-of-no-return edge. Uncommitted recovery rolls back in reverse order;
  committed recovery only rolls forward/reverifies/garbage-collects backups.
- [ ] Run real killed-process/restart tests on this Windows/NTFS host. Mocked
  flush tests are supplemental and are never described as durability proof.

### Task 5: Compose the fenced ingest JobService

**Files:**
- Modify: `pipeline/studio_jobs.py`
- Create: `tests/test_studio_job_service.py`

- [ ] Write RED tests for submit through every phase to success, child failure,
  validation failure, concurrent input/target change, request idempotency, and
  second-writer conflict.
- [ ] Submit sequence: read-only dedup check; non-blocking writer lock;
  `BEGIN IMMEDIATE`; repeat dedup/active/snapshot checks; insert queued run and
  first event; transfer the held lock handle to one non-daemon worker.
- [ ] Worker sequence: fenced claim, heartbeat, isolated workspace, process,
  validation, publish lock, snapshot recheck, durable publication, terminal
  transaction, cleanup, then writer-lock release.
- [ ] Startup recovery obtains the writer lock before handling orphaned active
  runs or publications. It then acquires `publish.lock` and resolves every
  publication journal before changing a validating/publishing run: uncommitted
  journals roll back and end `failed/publish_failed`; committed journals roll
  forward and keep or restore `succeeded`. A publishing run is never first
  rewritten to `interrupted`.
- [ ] A queued run executes only after current command schema, capability, and
  full snapshot revalidation; otherwise it becomes `stale_job`. A validating
  run with no journal re-verifies its stable staging tree before resuming or
  terminates with a stable validation error without publishing.
- [ ] For an orphaned executing run, compare persisted PID/start identity. If
  the original child is alive, recovery enters observer-only mode: keep write
  capability disabled, do not clean or reuse its workspace, and wait for exit.
  Only after confirmed exit may the run become `interrupted` and its workspace
  be quarantined. Add a real slow-child, killed-parent, restart test.
- [ ] Ensure every failure produces a stable public error code and bounded
  diagnostic event without traceback, environment dump, or arbitrary file data.

### Task 6: Expose write mode through a secure loopback HTTP contract

**Files:**
- Modify: `pipeline/studio_server.py`
- Create: `tests/test_studio_job_http.py`

- [ ] Preserve all existing default read-only tests and HTTP 405 behavior when
  `--enable-jobs` is absent.
- [ ] Write RED startup tests: jobs requested on non-loopback, ledger/lock/work
  failure, registry failure, recovery incomplete, durability self-test failure,
  non-NTFS Windows volume, unsupported POSIX host, or a still-live orphan child
  all remain read-only with visible reasons.
- [ ] Generate one startup-scoped `secrets.token_urlsafe(32)` token only after
  readiness succeeds. Derive canonical numeric origin from the actual bound
  socket, never the CLI hostname or request Host.
- [ ] Write RED request tests for exact canonical Host and Origin, token,
  JSON Content-Type, body limit, request ID, unknown fields, unknown commands,
  DNS-rebinding hosts, and stable error mappings.
- [ ] Implement `POST /api/jobs`, `GET /api/runs?cursor=N`, and
  `GET /api/runs/{id}`. B1 cancel/retry routes return stable `unsupported`.
- [ ] In write mode, SQLite is the sole run truth for `/api/runs` and
  `/api/project.active_run`; never merge `.nantai-studio/runs.json` with it.
- [ ] Advertise only ingest as enabled with cancel/retry false. All other
  commands stay disabled with milestone reasons. Use `Cache-Control: no-store`.

### Task 7: Add minimal honest Studio job controls

**Files:**
- Modify: `web/studio/local-adapter.mjs`
- Modify: `web/studio/local-adapter.test.mjs`
- Create: `web/studio/job-controller.mjs`
- Create: `web/studio/job-controller.test.mjs`
- Create: `web/studio/job-forms.mjs`
- Create: `web/studio/job-forms.test.mjs`
- Modify: `web/studio/app.js`
- Modify: `web/studio/index.html`
- Modify: `web/studio/styles.css`

- [ ] Local adapter retains only the latest capability token, attaches it and
  a fresh client request ID to writes, accepts cursor/detail reads, and drops
  write authorization on any capability refresh failure.
- [ ] Add an ingest-only confirmation model showing bounded parameters, fixed
  input, staging behavior, formal `photos/` impact, and cancel-unavailable
  wording. No free command/path/environment text field exists.
- [ ] Add lightweight cursor polling: 1 s active, 5 s idle, 15 s hidden, capped
  network backoff; dedupe by cursor; refresh `/api/project` and Viewer artifact
  exactly once after a newly observed terminal event.
- [ ] Submission selects Sources, opens the existing drawer, focuses the new
  run, and visibly labels real local execution. Mock remains simulated and can
  never reuse the local token path.
- [ ] Keep full generic multi-command forms, cancel/retry controls, and the
  expanded diagnostic drawer for Milestone C.

### Task 8: Independent review, gates, and staged commits

**Files:**
- Create: `docs/verification/2026-07-15-studio-milestone-b1.md`

- [ ] Ask the Opus architecture role to adversarially review ledger fencing,
  crash recovery, Windows durability, HTTP origin/token handling, and the
  single source of run truth. Resolve every Critical/Important finding.
- [ ] Run fresh Python, Studio, Viewer, ruff, and whitespace gates.
- [ ] Run real two-process writer contention, real ingest subprocess, and real
  killed-process/restart recovery on Windows/NTFS; state every capability skip.
- [ ] Verify on a simulated/available POSIX host that B1 remains explicitly
  read-only; schedule POSIX fsync plus real kill/restart enablement as a later
  evidence-bearing milestone.
- [ ] Use the in-app browser for read-only regression and enabled-ingest flows,
  including keyboard confirmation, live progress, refresh persistence, failure,
  and old-artifact preservation.
- [ ] Record exact evidence and the B1 trust boundary. Do not claim POSIX crash
  durability from Windows evidence.
- [ ] Before every commit, inspect the exact staged file list and leave all
  alignment/recon/Gaussian/HANDOFF WIP unstaged.

## Commit checkpoints

All commits include exactly:

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

Planned checkpoints on the sole `main` branch:

1. `docs: plan ingest job kernel`
2. `feat: add durable ingest job kernel`
3. `feat: connect Studio to ingest jobs`

Do not push, create a PR, create a worktree, or create another branch without a
new user instruction.
