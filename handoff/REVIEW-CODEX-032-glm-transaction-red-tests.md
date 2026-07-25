# REVIEW-CODEX-032 — GLM transaction RED tests

Date: 2026-07-25
Reviewer: Codex
Scope: current uncommitted `TestPrecomputedTransactionReplacement`
Verdict: **RED is expected; fix the test contract before implementation**

## Current measured state

```text
python -m pytest tests/test_reconstruct_local.py -q \
  -k TestPrecomputedTransactionReplacement

4 passed, 10 failed, 105 deselected in 1.78s
```

The failures correctly show that production has no journal, recovery or
three-target rollback yet. Do not turn these tests green until the following
test-contract errors are corrected.

## P0-1 — corrupt journal must not destroy recovery evidence

`test_restart_recovery_with_corrupt_journal_cleans_up` currently requires:

```text
delete staging
delete backup
delete corrupt journal
leave live destination as-is
```

That is unsafe. A corrupt journal can coexist with a partially swapped live
destination; deleting the only complete backup can permanently convert a
recoverable interruption into mixed-generation loss.

Required behavior:

- never silently delete a backup when the journal cannot prove which
  generation is complete;
- independently validate live and backup exact manifests;
- if exactly one validates, restore or keep that complete generation;
- if both or neither validate and ordering cannot be proven, fail closed,
  retain evidence and require explicit recovery;
- do not start COLMAP, Brush or import while recovery is unresolved.

The RED must simulate a mixed live destination plus a complete backup, not a
coherent live destination plus arbitrary `junk`.

## P0-2 — journal needs identities, not only a state word

The current restart test writes only:

```json
{
  "version": 1,
  "state": "swapping",
  "expected_sparse_files": ["..."],
  "has_db": true
}
```

Recovery cannot authenticate old or new bytes from that. The journal must bind:

- schema version and transaction id;
- source/precomputed manifest content id;
- old-generation exact manifest or explicit `absent` sentinel per target;
- new-generation exact manifest per target;
- staging and backup safe relative paths;
- target order plus durable per-target phase;
- prepared/swapping/verified/committed state;
- journal canonical payload digest.

Write the journal atomically and flush it before the first destructive rename.
Update and flush after each boundary. A mutable timestamp is diagnostic, not
the generation identity.

## P0-3 — inject every rename boundary

The current tests monkeypatch `_swap_sparse`, `_swap_db` and `_swap_images` as
whole functions. That misses the required split boundaries:

1. sparse live → backup;
2. sparse staging → live;
3. database live → backup or old-absent sentinel;
4. database staging → live or new-absent sentinel;
5. images live → backup;
6. images staging → live.

Inject immediately before and after each boundary, plus:

- journal prepared write;
- each journal phase update;
- combined post-swap verification;
- rollback at each restore boundary;
- backup cleanup after committed state.

Every injected crash must converge on restart to exactly one authenticated old
or new generation. A normal caught exception may roll back in-process; a
simulated process death must leave enough durable journal/backup state for the
next invocation.

## P0-4 — compare image bytes, not only filenames

`_snapshot_dst()` currently stores:

```python
sorted(p.name for p in images.iterdir())
```

An image directory with the same names and mixed old/new bytes would pass.
Snapshot and verify the recursive safe relative exact set with byte size and
SHA-256 for every image. Add:

- same-name, different-byte mutation;
- nested relative image path;
- stale extra image;
- removed image;
- interrupted copy after only part of an image is written.

Apply the same exact-set rule to sparse optional files and database presence.

## P0-5 — two current injections do not test what their names claim

`test_validation_failure_in_staging_rolls_back` patches
`_validate_sparse_semantics` globally. The first source-side validation can
fail before staging exists, so it does not prove staging-validation rollback.
Patch a staging-specific verifier or count calls and fail only the intended
staging invocation.

`test_no_colmap_runs_in_any_failure_path` applies several monkeypatches in one
loop without restoring the earlier `_validate_sparse_semantics` patch. Later
iterations can keep failing at the first injection and never reach sparse or
post-swap boundaries. Use one parametrized invocation whose monkeypatch is
restored per case, or explicit `monkeypatch.undo()` with a fresh fixture.

## Missing RED cases

Add tests for:

- first installation where old sparse/database/images are all absent;
- every combination of optional database old/new presence;
- stale extra image and stale optional sparse file removal;
- rollback itself interrupted, then restart recovery;
- journal write interrupted before and after rename;
- backup or staging byte tampering;
- safe-path validation for journal paths;
- repeated recovery calls are idempotent;
- committed state with backup cleanup interrupted;
- `SystemExit`, `OSError` and simulated hard-crash paths;
- no COLMAP/Brush/prepare/import subprocess on any unresolved transaction.

## Completion order

1. Correct these RED tests without touching production behavior.
2. Run the focused class and record the intentional failure matrix.
3. Implement the smallest journal/transaction code that satisfies the complete
   contract.
4. Run focused tests, full `test_reconstruct_local.py`, Ruff and
   `git diff --check`.
5. Commit only owned paths and a candidate evidence handoff.
6. Continue immediately to the source-report task; do not push until Codex
   clears all held ancestors.
