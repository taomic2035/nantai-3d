# Studio Milestone B1 verification record

Date: 2026-07-15

Host: Windows, Python 3.13, local NTFS project volume

## Result

The ingest-only Studio write path is ready on the verified Windows/NTFS host.
It remains fail-closed by default and becomes writable only when the server is
started with `--enable-jobs` and all startup durability, lock, ledger, registry,
and recovery probes succeed.

The success boundary is deliberately narrow: a succeeded run proves that the
fixed `input/` ingest command produced a strictly verified staging tree and that
those exact bytes were durably published to `photos/`. It does not prove real
capture provenance, metric alignment, reconstruction quality, or 3DGS fidelity.

## Fresh automated gates

Run from the repository root after the external crash-qualification campaign
and final HTTP transport fix:

```text
python -m pytest -q -rs
453 passed, 15 skipped in 60.64s

node --test web/studio/*.test.mjs
63 passed, 0 failed

node --test web/viewer/*.test.mjs
32 passed, 0 failed

python -m ruff check pipeline/studio_jobs.py pipeline/studio_ledger.py \
  pipeline/studio_server.py tests/test_studio_ledger.py \
  tests/test_studio_writer_lock.py tests/test_studio_jobs.py \
  tests/test_studio_process.py tests/test_studio_publication.py \
  tests/test_studio_recovery.py tests/test_studio_job_service.py \
  tests/test_studio_job_http.py tests/test_studio_crash_recovery.py tests/helpers
All checks passed!

git diff --check
passed
```

The repository-wide Ruff command is not green because of five pre-existing,
out-of-scope findings in `handoff/deliverables/HANDOFF-002/scripts/generate.py`
and `tests/test_studio_capabilities.py`. No B1 file is involved in those
findings.

The oversized-body regression was also exercised twenty consecutive times with
a real 2 MB POST. All twenty returned the stable 413 contract. This specifically
guards the Windows socket-reset failure caused by closing a connection while its
rejected request body was still unread.

The early write-rejection matrix was exercised twenty consecutive times with a
real 60 KB POST for invalid Host, Origin, token, and content type. All eighty
requests returned their stable structured 400/403 contract after the bounded
body was drained; no Windows connection abort recurred.

Raw-socket regressions also declared a 64 KB body for an invalid Host and a
one-trillion-byte body for an otherwise authorized request, then sent no body.
Both returned their structured 400/413 response within the 0.5 second drain
deadline, created no run, and closed instead of waiting indefinitely.

## Process, publication, and recovery evidence

- Real child processes prove cross-process writer-lock exclusion and lock release
  after the owner is killed.
- A real ingest subprocess runs through staging, strict verification, publication,
  ledger terminal commit, and formal `photos/` byte verification.
- Process tests cover simultaneous stdout/stderr draining, invalid UTF-8,
  truncation, redaction across read boundaries, bounded rotation, nonzero exit,
  and `shell=False`.
- Windows durability tests exercise the real NTFS self-test operations and prove
  every staged file is flushed before the success point of no return.
- Publication tests cover invalid staging, changed input/target snapshots,
  pre/post-commit recovery, successive committed generations, rollback after a
  move failure, and fail-closed link/junction replacement paths where host
  privileges permit creation.
- Startup recovery covers stale queued jobs, dead executing jobs with workspace
  quarantine, observer-only handling for a still-live child identity, validating
  resume, publication rollback, and committed-journal re-verification.
- A separate publisher process was terminated without Python unwinding at all 15
  fault boundaries surrounding journal intent/done writes, write-through moves,
  directory flushes, and the success point of no return. The campaign covered
  both initial publication with no formal target and successive publication
  after a real prior committed generation. The first fresh recovery converged to
  either no formal target or the exact prior committed generation plus a
  rolled-back journal, or to the exact new committed generation after
  `after_commit`. A second fresh recovery proved that result idempotent; no mixed
  generation remained and both project locks were reacquirable.
- A real worker parent was terminated after persisting its slow child PID and
  Windows creation-time identity. A fresh process stayed observer-only while the
  exact child remained alive and preserved its workspace. After the child exited,
  the next fresh process marked the run `interrupted`, quarantined the complete
  workspace, and recovered both locks.
- An independent Opus P1b architecture review reported PASS after the committed
  journal-generation, staging flush, containment, heartbeat, and recovery fixes;
  no Critical or Important findings remained.

## Browser evidence

The enabled-ingest flow was exercised in the in-app browser against a disposable
local project using generic, replaceable synthetic input copied from an existing
contact-sheet fixture. The test did not modify the real project artifacts.

Observed behavior:

1. Studio reported the local write capability as ready.
2. Sources exposed the capability-gated `处理输入素材` action even when sources
   were already present.
3. The confirmation displayed fixed input/staging/target paths, bounded defaults
   (`fps=2`, `max_frames=300`, `blur_threshold=80`, `long_edge=2560`), replacement
   impact, and the explicit absence of cancellation.
4. Submission moved from queued through executing, validating, publishing, and
   committed; nine cursor events were visible in the drawer.
5. The terminal event refreshed the project snapshot exactly once and the source
   frame count changed to one.

Browser QA found and fixed two integration-only defects before this record: an
unbound timer callback causing `Illegal invocation`, and an ingest action that
was unreachable when the Sources list was non-empty.

## Explicit skips and non-claims

- Fourteen Windows symlink/junction adversarial cases were skipped because the
  current account lacks `SeCreateSymbolicLinkPrivilege` (`WinError 1314`). The
  production path gates remain fail-closed; these skipped fixtures are not
  described as executed evidence.
- One POSIX `/proc` process-start identity test was skipped on Windows.
- POSIX write mode is intentionally unavailable in B1. Windows results are not
  used to claim POSIX `fsync` or crash durability.
- Windows/NTFS external-process crash qualification now covers every B1
  publication fault boundary and the surviving-child parent-crash path. This is
  evidence for the fixed single-target ingest kernel only; it is not evidence for
  future multi-target SceneRevision publication or cancellation semantics.
- Browser QA covered the successful enabled-ingest path. Keyboard-only submit,
  browser refresh persistence during an active run, and an injected browser
  failure preserving an old artifact remain follow-up browser scenarios.
- B1 does not expose cancel, retry, arbitrary commands, paths, environment, file
  upload, reconstruction, world generation, or asset validation writes.

## Repository hygiene

All work stayed on the sole `main` branch. No additional branch or worktree was
created. Alignment, reconstruction, Gaussian, and HANDOFF changes were not staged
as part of the two B1 implementation checkpoints.
