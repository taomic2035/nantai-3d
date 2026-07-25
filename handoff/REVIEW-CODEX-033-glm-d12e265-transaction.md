# REVIEW-CODEX-033 — GLM `d12e265` transaction audit

Date: 2026-07-25
Reviewer: Codex
Commit: `d12e265`
Verdict: **HELD — P0 data-loss recovery gaps**

## Outcome

The bounded test file and Ruff are green:

```text
python -m pytest tests/test_reconstruct_local.py -q
119 passed in 8.69s

python -m ruff check scripts/reconstruct_local.py tests/test_reconstruct_local.py
All checks passed!
```

That proves only the modeled paths. It does not prove the six filesystem
rename boundaries, journal-write boundaries or rollback restart safety.

Codex ran fresh temporary-directory probes against the committed production
functions. All three probes reproduced loss of the only old sparse payload:

```text
case: journal state=prepared, old sparse already moved to backup
before: backup=true, old_sparse_in_backup=true, live_sparse=false
after:  backup=false, old_sparse_in_backup=false, live_sparse=false

case: truncated/corrupt journal, backup is the only good generation
before: backup=true, old_sparse_in_backup=true, live_sparse=true
after:  backup=false, old_sparse_in_backup=false, live_sparse=true

case: journal missing, backup is the only good generation
before: backup=true, old_sparse_in_backup=true, live_sparse=true
after:  backup=false, old_sparse_in_backup=false, live_sparse=true
```

The first path leaves `sparse` missing while old `db/images` remain. The other
two can retain partial new live data after permanently deleting the old
generation. This violates fail-closed and “failed run preserves the last
verified destination.”

## Root cause

The journal has one coarse `state`, but the transaction has at least six
independent destructive rename boundaries:

```text
old sparse -> backup
old db     -> backup
old images -> backup
new sparse -> live
new db     -> live
new images -> live
```

`state=swapping` is written only after all three old targets have been moved.
A crash during those moves leaves `state=prepared`; recovery assumes no move
happened and deletes the backup. `_write_txn_journal()` itself uses direct
`write_text`, so interruption can truncate the journal; corrupt-journal
recovery also deletes the backup. With no journal, recovery again deletes the
backup without proving which generation is complete.

The same ambiguity exists if `_restore_backup()` is interrupted. It moves
three targets without per-target progress, so a second recovery cannot tell
whether a missing backup member means “old target did not exist,” “not moved
yet,” or “already restored.”

## Additional findings

1. Initial install has no old backup. If a new→live swap fails after one or two
   targets, `_restore_backup()` does not remove every newly installed target;
   a partial first generation can remain.
2. The recovery parser does not reject unknown state, wrong version, missing
   fields or unsafe target metadata. Unknown state falls through to cleanup.
3. `_write_txn_journal()` is neither temp-write + replace nor durability
   ordered; recovery therefore must treat missing/truncated state as
   indeterminate, never as permission to delete evidence.
4. `_verify_destination_post_swap()` claims byte verification but receives no
   expected hashes. It checks the sparse filename set and semantics, plus only
   existence of database/images. A byte-altered db or image can pass.
5. Test image snapshots compare only top-level filenames. Same names with
   changed bytes, sizes or nested set are not detected.
6. Backup renames occur outside the `try` that rolls back swap failures. An
   `OSError` during old→backup propagates with `state=prepared`, triggering the
   first reproduced loss on restart.

## GLM current task — exact RED order

Own only:

```text
scripts/reconstruct_local.py
tests/test_reconstruct_local.py
handoff/FEEDBACK-HANDOFF-GLM-008-task3-*.md
```

Do not start the source report, P5b→P7 rehearsal, geometry work, Studio/Viewer,
`web/data/` or exact-266 paths. Implement one new correction commit in this
order:

1. Add recursive destination snapshots with exact relative path, type, byte
   size and SHA-256 for sparse, database and every image. Same-name changed
   bytes must fail.
2. Add RED for initial install interrupted after each new→live boundary.
   Recovery must leave either no generation or one complete verified
   generation, never a partial install.
3. Add RED after each old→backup boundary. Include a crash before the first
   `swapping` journal write. Recovery must restore the complete old generation.
4. Add RED for journal temp-write failure, replace failure, truncated JSON,
   missing file, wrong version, unknown state and retained old state. None may
   delete the only complete backup.
5. Add RED for interruption after each restore-backup rename, followed by a
   second and third recovery. Recovery must be idempotent.
6. Add RED for byte mutation after swap in `cameras.bin`, `colmap.db` and one
   image. Post-swap verification must compare an exact expected byte manifest,
   not only names/existence/semantics.
7. Add RED for unexpected extra nested image files and missing/extra sparse
   members. Exact sets must be recursive and normalized.
8. Only after all REDs fail for the intended production reason, implement:
   atomic journal temp-write + same-directory replace; a unique transaction
   id; immutable old/new exact-byte manifests; explicit per-target backup,
   install and restore phases; and recovery that validates candidate
   generations before choosing restore/commit.
9. Never delete staging/backup/journal until one live generation has passed
   the expected exact-byte manifest plus semantic validation. If state is
   ambiguous or both generations fail, stop with a recovery-required error and
   preserve all evidence.
10. Run the whole focused file and Ruff. Handoff the restart-state matrix,
    exact commands/counts and content SHAs with `Status: candidate` and
    `Reviewer: pending Codex`.

## Acceptance boundary

Codex will independently inject interruption at all six destructive rename
boundaries, journal write/replace, every rollback rename and post-swap byte
mutation. `d12e265` remains held until those probes leave exactly one complete
verified generation or conservatively preserve all recoverable evidence.

No current transaction result changes the larger trust boundary: real
overlapping capture, accepted real-photo SfM, non-mock cloud-GPU 3DGS,
measured alignment and real Viewer QA remain external gates.
