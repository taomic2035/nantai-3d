# Studio Milestone B0 Verification

Date: 2026-07-15
Scope: strict ingest schema, deterministic fresh staging, stable source evidence,
and byte-level artifact verification before Milestone B publication work.

## Contract evidence

- Schema version, tool identity, numeric bounds, UTC timestamps, lowercase
  SHA-256 values, positive sizes, and photo/video field combinations fail
  closed under Pydantic validation.
- Source and output paths are relative POSIX paths that also reject Windows
  device names, invalid characters, trailing spaces/dots, and case-folded
  collisions.
- `kind` is bound to a supported media suffix. Photos keep their exact source
  path and bytes; videos use `<source>.frames/frame_XXXXXX.jpg`, strictly
  increasing sampled source indices, and the declared `max_frames` limit.
- The session ID covers the complete immutable parameter, source, output,
  timing, EXIF, and GPS declaration rather than source hashes alone.
- Ingest snapshots all supported source files before and after processing,
  rejects additions/removals/mutations, requires fresh real output storage,
  and writes the success manifest atomically only after processing succeeds.
- The verifier requires exact source/output file-set equality, rejects links,
  junctions, and non-regular files, bounds manifest reads, and checks file
  identity and timestamps around hashing to detect in-verification mutation.

## Test-driven and review evidence

- Initial adversarial RED run: **17 failed, 65 passed, 2 skipped**, covering
  deterministic mapping, sampling limits, hash-time mutation, altitude
  direction, and Windows portability.
- Final review RED run: **5 failed**, covering media kind/suffix mismatch and
  empty or malformed EXIF datetime evidence.
- Focused ingest contract: `python -m pytest -q tests/test_ingest_manifest.py`
  -> **88 passed, 2 skipped**.
- Reconstruction compatibility: `python -m pytest -q tests/test_reconstruct.py`
  -> **25 passed**.
- Focused lint: `python -m ruff check pipeline/ingest.py
  pipeline/ingest_manifest.py tests/test_ingest_manifest.py
  tests/test_reconstruct.py` -> passed.
- Full Python gate: `python -m pytest -q -rs` -> **340 passed, 10
  skipped**.
- Studio JavaScript gate: `node --test web/studio/*.test.mjs` -> **55
  passed**.
- Viewer JavaScript gate: `node --test web/viewer/*.test.mjs` -> **32
  passed**.
- `git diff --check` -> no whitespace errors; only notices for unrelated
  pending CRLF-normalization files.
- The Opus architecture role performed three adversarial reviews. Its findings
  drove closure of deterministic mapping, source-set races, traversal and link
  handling, portable path rules, verifier mutation detection, media-kind
  binding, and truthful EXIF/GPS constraints.
- Its final focused confirmation reported **PASS**, with no remaining Critical
  or Important findings.

The two focused skips require Windows symlink privileges unavailable on this
host. They are capability skips, not evidence that the equivalent POSIX cases
passed.

## Trust and milestone boundary

B0 verifies that declared sources and outputs are bound to exact on-disk bytes
and that the declaration is internally consistent. EXIF/GPS observations,
video timing, source-frame indices, and JPEG media semantics remain
hash-bound producer assertions; the standalone verifier does not independently
decode the source media and re-derive those semantic claims.

B0 creates no HTTP write route, subprocess job service, cancellation/retry
engine, publication swap, or durable job ledger. Verification detects a file
that changes during its read, but it does not reserve or lock the artifact
after the verifier returns. B1 must preserve this verifier and hold its
single-writer lease or equivalent filesystem snapshot through the later
publication boundary.
