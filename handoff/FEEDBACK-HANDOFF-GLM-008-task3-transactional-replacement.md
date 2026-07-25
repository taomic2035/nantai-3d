# FEEDBACK-HANDOFF-GLM-008 — Task 3 closure: transactional three-target replacement

Date: 2026-07-25
Owner: GLM lane
Reviewer: pending Codex
Status: candidate — awaits Codex review; not pushed (held per §1 until P0 probes pass)

## Scope

HANDOFF-GLM-008 §3 "close P7a stale-file and exact-set gap" — the
transactional three-target replacement for `--precomputed-colmap`.
This is the bounded correction to held commit `0978ee7`, applied as a
new commit on top of it.

## Problem

REVIEW-CODEX-030 P0 (commit `0978ee7` held): the previous
`_copy_precomputed_to_ws` performed three independent atomic renames
(`_atomic_replace_dir` / `_atomic_replace_file`) for sparse/0,
colmap.db and images/. Codex injected a failure into the database
replacement after the sparse directory swap and measured:

```text
sparse_after_failure = NEW
db_after_failure = OLD
images_after_failure = OLD
mixed_generation = true
```

It also deleted `*.old` on the next startup without first deciding
whether an interrupted transaction needed rollback.

## What was done

Replaced the three independent renames with a transaction journal
(`prepared → swapping → verified → committed`) plus backup, full
rollback, and restart recovery.

### New functions in `scripts/reconstruct_local.py`

| Function | Role |
|---|---|
| `_recover_precomputed_transaction(ws)` | Called at the top of `_copy_precomputed_to_ws` and safe to call independently. Inspects the journal state and restores a coherent destination. |
| `_restore_backup(ws, backup)` | Moves backup `{sparse_0, colmap_db, images}` back to destination, overwriting any partial new content. |
| `_swap_sparse(staging, dst)` | Atomic rename staging sparse/0 → destination. |
| `_swap_db(staging_db, dst_db, has_db)` | Swap staging colmap.db → destination; remove stale dst db if source has none. |
| `_swap_images(staging_img, dst_img)` | Atomic rename staging images/ → destination. |
| `_verify_destination_post_swap(ws, expected, has_db)` | Post-swap exact-file-set + semantic verification. |
| `_write_txn_journal(path, journal)` | Write journal JSON (sort_keys, LF). |

### Removed

- `_atomic_replace_dir` and `_atomic_replace_file` — superseded by the
  transactional swap functions. They were only used inside
  `_copy_precomputed_to_ws`.

### Transaction flow

```
0. _recover_precomputed_transaction(ws)   ← restart recovery
1. fresh staging: copy source files → ws/.staging_precomputed/
   journal state = "prepared"
2. staging semantic validation            ← fail here: no swap, no backup
3. backup: move dst {sparse/0, colmap.db, images} → ws/.precomputed_backup/
4. journal state = "swapping"
   _swap_sparse → _swap_db → _swap_images  ← fail here: full rollback from backup
5. journal state = "verified"
   _verify_destination_post_swap          ← fail here: full rollback from backup
6. journal state = "committed"
   cleanup staging + backup + journal
```

### Restart recovery decision matrix

| Journal state | Action |
|---|---|
| No journal | noop (remove any stray staging/backup) |
| Corrupt (not JSON) | conservative cleanup; destination untouched |
| `prepared` | clean up staging + backup + journal (no swap happened) |
| `swapping` / `verified` | restore backup → destination, then cleanup |
| `committed` | clean up staging + backup + journal (swap completed) |

## Test evidence

`tests/test_reconstruct_local.py::TestPrecomputedTransactionReplacement`
— 14 tests, all GREEN:

```text
test_stale_optional_files_removed_when_source_drops_them   PASSED
test_missing_optional_files_when_source_never_had_them     PASSED
test_absent_source_db_removes_stale_destination_db         PASSED
test_interrupted_staging_copy_rolls_back                   PASSED
test_failure_after_sparse_swap_rolls_back_all_three        PASSED
test_failure_after_db_swap_rolls_back_all_three            PASSED
test_failure_during_sparse_swap_rolls_back                 PASSED
test_validation_failure_in_staging_rolls_back              PASSED
test_post_swap_validation_failure_rolls_back               PASSED
test_restart_recovery_restores_committed_generation        PASSED
test_restart_recovery_with_no_journal_is_noop              PASSED
test_restart_recovery_with_corrupt_journal_cleans_up       PASSED
test_failed_run_preserves_last_verified_destination        PASSED
test_no_colmap_runs_in_any_failure_path                    PASSED
```

### Full focused suite

```text
python -m pytest tests/test_reconstruct_local.py -q
119 passed in 7.75s
```

### Ruff

```text
python -m ruff check scripts/reconstruct_local.py tests/test_reconstruct_local.py
All checks passed!
```

## Properties verified

- A failed run leaves the last verified destination intact (snapshot
  equality before/after injected failure at every swap step).
- No COLMAP subprocess runs in any failure path (precomputed branch
  skips COLMAP entirely).
- Stale optional files (frames.bin / rigs.bin / project.ini) are removed
  when source drops them.
- Stale colmap.db is removed when source has no db.
- Missing optional files when source never had them are not created.
- Restart recovery restores the committed generation from backup when
  journal state = "swapping".
- Corrupt journal triggers conservative cleanup without touching the
  destination.
- No staging / backup / journal leftover after any path (success or
  failure).

## Boundaries (honest)

- The transaction journal is written to `ws/.precomputed_txn.json`
  (plain JSON, not tamper-evident). An attacker with write access to
  the work directory could forge a journal. This is the same trust
  boundary as `.stage_state.json` — the work directory is not an
  immutable handoff artifact.
- `_restore_backup` uses `rename` (atomic on the same filesystem).
  If staging/backup/destination are on different filesystems, rename
  would fail — but all three live under `ws/`, so this is not a
  concern in practice.
- `except (SystemExit, Exception)` catches both injected test failures
  and real OSError during swap. KeyboardInterrupt (also BaseException)
  is NOT caught — it will abort without rollback. This is acceptable:
  KeyboardInterrupt is a user-initiated abort, not a swap failure, and
  the restart recovery will handle it on the next run.

## Files changed

- `scripts/reconstruct_local.py` — replaced `_atomic_replace_dir` /
  `_atomic_replace_file` / `_copy_precomputed_to_ws` with
  transactional implementation (7 new functions).
- `tests/test_reconstruct_local.py` — fixed 3 Ruff F841 (unused
  `precomp` in restart-recovery tests). All 14 RED tests now GREEN.
