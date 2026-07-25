# REVIEW-CODEX-035 — P7 parser and WAL v3 release clearance

Date: 2026-07-25
Reviewer: Codex
Verdict: **accepted for Preview integration**

## Result

The held findings in `5a98ed9` and `d12e265` are closed by the current
corrective tree. The production boundary remains fail-closed and adds no trust.

Codex added RED cases and fixes for:

- independent `fx/fy` validation for all dual-focal COLMAP models;
- overflow-safe finite quaternion norms;
- one host-independent image-name grammar and normalization-collision gate;
- mandatory SHA-bound real COLMAP fixtures;
- old/new database presence tracked independently;
- first-install swap failure leaving no partial generation;
- interrupted sparse/database/image restores converging to one exact old
  generation;
- `verified`/`committed` journals revalidating destination bytes even when no
  backup remains.

## Fresh gates

```text
tests/test_reconstruct_local.py: 200 passed, 1 skipped
skip: Windows host does not grant symlink creation
Ruff: clean
diff check: clean
```

The parser/transaction mechanism is accepted for a Preview release. This does
not accept P7 as real-scene evidence: real capture, accepted real-photo SfM,
non-mock GPU 3DGS, measured alignment and real Viewer QA remain outstanding.
