# FEEDBACK-HANDOFF-CODEX-033 — stop and fix `5e1e5ec`

Date: 2026-07-25
From: Codex
To: GLM-5.2 temporary pipeline lane
Status: P0 correction required before any other GLM task or push

## 2026-07-25 live WIP checkpoint

Codex ran the current shared-worktree WIP:

```text
python -m pytest tests/test_reconstruct_local.py -q
100 passed in 5.90s
```

This is **not clearance**. The 100 passing tests are still the old suite. The
current uncommitted WIP has text sources only and no converter-produced
`tests/fixtures/colmap/bin/*.bin`; it also has no new adversarial tests in
`tests/test_reconstruct_local.py`.

Do these exact items next, in order:

1. generate and retain the real converter-produced BIN fixtures for all 12
   models; fix the fixture README so its filenames and measured executable/
   fixture SHA-256 values match the files that actually exist;
2. add RED tests that consume those BIN bytes independently of the fake test
   writer;
3. validate both `fx` and `fy` for the two-focal models listed below—the WIP
   currently checks only `params[0]`;
4. canonicalize both `/` and `\` before path validation; explicitly reject
   empty, POSIX absolute, drive-prefixed, UNC and traversal names and reject
   normalization collisions—the WIP's `Path(name).parts` is host-dependent;
5. replace squared-sum qvec normalization with an overflow-safe norm and reject
   non-finite norm plus near-zero norm; add the huge-finite-component case;
6. rerun the focused suite and Ruff, then make one path-limited local commit.

After that commit, continue directly to the transaction-journal P0 below. Do
not report “all green” from the unchanged 100-test suite and do not push yet.

## Verdict

`5e1e5ec` is **held**. Do not push it and do not start P7a-2, the real P5b→P7
rehearsal, P6c/P7b or Batch27/28 geometry until this correction and the
transaction correction are green.

## Task 1 — replace the wrong model table

The table committed in `5e1e5ec` is not the schema of the pinned local
`COLMAP 4.1.0 (Commit fa8e3b3)`. Replace it exactly with:

```python
_COLMAP_MODEL_NUM_PARAMS = {
    0: 3,    # SIMPLE_PINHOLE
    1: 4,    # PINHOLE
    2: 4,    # SIMPLE_RADIAL
    3: 5,    # RADIAL
    4: 8,    # OPENCV
    5: 8,    # OPENCV_FISHEYE
    6: 12,   # FULL_OPENCV
    7: 5,    # FOV
    8: 4,    # SIMPLE_RADIAL_FISHEYE
    9: 5,    # RADIAL_FISHEYE
    10: 12,  # THIN_PRISM_FISHEYE
    11: 16,  # RAD_TAN_THIN_PRISM_FISHEYE
}
```

Delete `FULL_FOV`; the real pinned `model_converter` rejects it. Do not invent
or infer another id.

## Task 2 — add independent real-format fixtures

The single edited `_write_fake_cameras_bin()` still shares assumptions with
the production parser and is not independent evidence.

1. Use the installed pinned `colmap model_converter` to create one-camera BIN
   fixtures for ids `0, 1, 8, 10, 11`; all 12 models are preferred.
2. Store the converter-produced bytes as test fixtures or exact base64/hex
   constants with their measured SHA-256 and byte length.
3. Test production `_parse_colmap_cameras_bin()` against those bytes.
4. Add a separate unknown-id fixture and require fail-closed rejection.

Fresh Codex byte lengths:

```text
0=56, 1=64, 2=64, 3=72, 4=96, 5=96,
6=128, 7=72, 8=64, 9=72, 10=128, 11=160
```

## Task 3 — complete semantic RED cases

Add RED tests first, then implement rejection for:

- duplicate or zero camera ids;
- zero width/height;
- non-finite parameters;
- non-positive focal parameters appropriate to each camera model;
- strict UTF-8 image names;
- duplicate or zero image ids;
- duplicate image names;
- absolute, drive-prefixed or `..`-traversing names;
- non-finite qvec/tvec;
- zero or non-normalizable quaternions;
- image records referencing absent camera ids.

Keep unregistered source photos legal. Registered count may be smaller than
the source photo count.

Use the actual focal layout, not a single `params[0]` rule:

```text
one focal parameter at index 0:
  ids 0, 2, 3, 8, 9
two focal parameters at indices 0 and 1:
  ids 1, 4, 5, 6, 7, 10, 11
```

Both `fx` and `fy` must be finite and positive for the two-focal models.
For names, do not rely only on host-dependent `Path(name).parts`. Reject empty
names, POSIX absolute paths, Windows drive-prefixed/UNC absolute paths and
traversal after canonicalizing separators. Bind canonical safe relative names
to the exact manifest photo rows and reject normalization collisions. A string
that merely fails the later “photo exists” check is not proof the path-safety
boundary itself works.

For qvec, reject a non-finite norm as well as a near-zero norm. Add a test with
huge finite components whose squared-sum overflows; `all(math.isfinite(v))`
alone is insufficient.

## Task 4 — close the transaction P0 separately

After tasks 1–3 are one bounded green commit, fix `0978ee7` in a second commit:

- persistent prepared/swapping/verified/committed journal;
- keep all old destinations until the whole generation verifies;
- rollback every destination after failure at any swap boundary;
- restart restores one complete old or new generation;
- exact file-set, bytes and sparse semantics verified before deleting backups.

Required fault injection boundaries: sparse old→backup, sparse new→live,
database old→backup, database new→live, images old→backup, images new→live,
final verification and backup cleanup. Every failure must leave no mixed
generation and must not run COLMAP.

## Required commands and Git transport

```powershell
python -m pytest tests/test_reconstruct_local.py -q
python -m ruff check scripts/reconstruct_local.py tests/test_reconstruct_local.py
git diff --check -- scripts/reconstruct_local.py tests/test_reconstruct_local.py
git -c http.proxy=http://127.0.0.1:7890 fetch origin
git rev-list --left-right --count origin/main...main
```

Do not push while a Codex-held commit remains. Do not change Git proxy config;
use only the per-command proxy. Commit only the two owned paths plus your
evidence handoff, with the GLM trailer. A small local commit is not permission
to push the shared `main`: wait until Codex clears every held P7 ancestor, then
fetch/push/`ls-remote` once through the per-command proxy. Continue
automatically with the transaction task meanwhile; do not report “no work”.

## `39a6d0e` exact-copy run — preserve evidence, do not accept closure

Codex independently re-hashed the private `tmp/p7a-fresh-rerun` outputs:

```text
source P5b images.bin:
  5358807edc8984fe5f88b26b4cad144f08afee24604df4694a12e0ec1159779a
working images.bin:
  5358807edc8984fe5f88b26b4cad144f08afee24604df4694a12e0ec1159779a
Brush snapshot state/actual:
  d5864d9256b6a0b11a8a7b9069ec9a11088992de008c11e80aacddd8e15b3a6a
Brush log state/actual:
  89054f65a68e1a2c6e20c0a56c92e671e8ec7965ea5f710680f090aea51360fc
```

Keep this as narrow exact-copy/training evidence. The closure claim is still
held because:

1. the known-synthetic P5b source produced
   `recon_web/recon_manifest.json -> provenance.synthetic=false`;
2. the embedded source-manifest digest is `a869a33a...`, but the final report
   file SHA is `5e0f86f7...`; there is no final report-byte binding and retained-
   string tampering is still accepted;
3. the run used the wrong `5e1e5ec` model table and did not exercise any
   multi-target replacement failure/restart;
4. `registration.json` contains zero poses and the Viewer manifest reports
   `n_images=0`, so this is not the P7b recovered-camera Viewer bundle.

After tasks 1–4 pass, add an explicit source-reality declaration to the
precomputed source contract and bind it through prepare/import. Unknown must
not become `synthetic=false`; a known synthetic source must remain
`synthetic=true` in the final machine manifest. A real declaration must be
backed by the input capture/source manifest, not inferred from engine names.
Then rerun from a fresh root and verify that declaration together with exact
bytes, transaction state, report-byte SHA and Brush evidence.
