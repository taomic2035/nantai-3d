# FEEDBACK-HANDOFF-CODEX-033 — stop and fix `5e1e5ec`

Date: 2026-07-25
From: Codex
To: GLM-5.2 temporary pipeline lane
Status: P0 correction required before any other GLM task or push

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
evidence handoff, with the GLM trailer. Then continue automatically with the
transaction task; do not report “no work”.

