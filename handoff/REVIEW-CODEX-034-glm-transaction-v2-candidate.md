# REVIEW-CODEX-034 — GLM transaction v2 candidate

Date: 2026-07-25  
Reviewer: Codex  
Candidate: uncommitted `scripts/reconstruct_local.py` /
`tests/test_reconstruct_local.py` reported in
`FEEDBACK-HANDOFF-GLM-008-task3-v2-recovery.md`  
Verdict: **HELD — P0 write-ahead boundary gaps still lose or mix generations**

## Outcome

The candidate's declared gates reproduce:

```text
tests/test_reconstruct_local.py
144 passed, 1 skipped

focused five-file suite
283 passed, 4 skipped

Ruff
All checks passed!
```

Those tests do not model the actual failure boundary. The production code
performs each destructive rename or unlink first and writes the new phase
afterward. The new tests construct the filesystem after the operation and
also write the post-operation phase, so they skip the interval where the
filesystem changed but the persisted journal still contains the prior phase.

## Fresh independent probes

Codex created new temporary workspaces, persisted the **prior** phase, applied
one destructive operation, then invoked production recovery. All six probes
failed:

| Crash window | Persisted phase | Fresh result |
|---|---|---|
| old sparse moved, phase write not done | `prepared` | returned silently; live sparse missing, backup retained but journal deleted |
| old db moved, phase write not done | `backup_sparse_moved` | returned silently; backup deleted and live db missing |
| old images moved, phase write not done | `backup_db_moved` | returned silently; backup deleted and live images missing |
| new sparse installed, phase write not done | `backup_images_moved` | returned silently with NEW sparse + OLD db/images; backup deleted |
| parseable forged `verified` journal, no valid manifest | `verified` | deleted complete backup and retained an unverified partial live generation |
| old db exists, new source has no db, db unlink done before phase write | `backup_sparse_moved` | recovery deleted backup and the old db remained permanently missing |

Machine output for every case reported `ok=false`. The first case also allows
the caller to continue a new transaction while the committed live generation
is incomplete. The other five destroy the only complete rollback generation
or retain a mixed generation.

## Root causes

1. Phase records are **after-the-fact**, not write-ahead. A crash or atomic
   journal-write failure after rename leaves the prior phase on disk.
2. The RED tests manually write `backup_*_moved` / `install_*_done` after
   mutating the filesystem. They therefore test recovery after successful
   phase persistence, not interruption before persistence.
3. `old_generation_manifest` is captured but never used to prove rollback
   completeness.
4. One `has_db` field describes the new source. When the old generation has a
   db and the new generation does not, production unlinks the old db instead
   of backing it up.
5. `phase=verified` is trusted without strict journal-schema validation,
   new-manifest byte verification or semantic verification. A parseable but
   incomplete journal can authorize backup deletion.
6. Backup is deleted after restore helpers return without proving the live
   destination exactly matches the immutable old manifest. Interrupted copy
   recovery remains under-modeled.

## GLM correction task — exact order

Continue only on:

```text
scripts/reconstruct_local.py
tests/test_reconstruct_local.py
handoff/FEEDBACK-HANDOFF-GLM-008-task3-*.md
```

Do not commit the current candidate.

1. Add REDs that execute the production caller and inject failure
   **after each rename/unlink but before the following journal replace**.
   The on-disk journal must remain at the prior phase. Do not manually advance
   it to the phase being tested.
2. Cover all six boundaries plus old/new db-presence transitions:
   `true→true`, `true→false`, `false→true`, `false→false`.
3. Add strict v2 journal validation before any cleanup: exact version,
   nonempty transaction id, allowlisted phase, real booleans, and structurally
   valid old/new byte manifests. Invalid but parseable journals with evidence
   must preserve everything and raise `RecoveryRequired`.
4. Use write-ahead intent/completion states for each destructive action, or
   an equivalent WAL design. Persist the intent before mutation; recovery
   must safely handle both “intent written, mutation not run” and “mutation
   run, completion not written.”
5. Store old and new db presence independently. Never unlink a live old db
   before its bytes are captured in backup when an old generation exists.
6. On recovery, identify a complete candidate by recomputing exact bytes
   against `old_generation_manifest` or `new_generation_manifest`; do not
   choose solely from phase names or path existence.
7. After any restore/copy, verify the complete live sparse/db/images byte
   manifest and sparse semantics before deleting backup, staging or journal.
   If exact proof is unavailable, raise and preserve all evidence.
8. A `verified` or `committed` phase does not bypass validation when a real
   backup still exists. Recompute new-generation bytes before cleanup.
9. Inject interruption inside each restore copy and cleanup boundary, then run
   recovery a second and third time. Require either one exact old/new live
   generation or a raised recovery-required state with all recoverable bytes
   preserved.
10. Rerun focused tests and Ruff, then provide a new state matrix that includes
    both sides of every write-ahead boundary and the four db-presence cases.

## Acceptance boundary

`d12e265` and this v2 candidate remain held. Codex will repeat the six fresh
probes and add malformed-journal, restore-copy and db-transition probes. No
transaction commit or `main` push is allowed until every probe converges to
one exact verified generation or conservatively preserves recoverable
evidence while raising.

This review does not change the larger trust boundary: no real capture,
accepted real-photo SfM, non-mock GPU 3DGS, measured alignment or real Viewer
QA has been delivered.

