# FEEDBACK-HANDOFF-GLM-008 — Task 2 closure: real COLMAP fixture + camera model table + sparse semantic validation

Date: 2026-07-25
Owner: GLM lane
Reviewer: pending Codex
Status: candidate — awaits Codex review; not pushed (held per §1 until P0 probes pass)

## Scope

HANDOFF-GLM-008 §2 "finish P7a-6 correctly" — the parser, camera-model table,
real-COLMAP-format fixture, and sparse-semantic validation work. This is the
bounded correction to held commit `0978ee7`, applied as a new commit on top
of it (not a force-push rewrite of history).

## What was done

### 1. COLMAP 4.1.0 camera-model table corrected

`scripts/reconstruct_local.py::_COLMAP_MODEL_NUM_PARAMS` now matches the
authoritative map Codex measured with the pinned local
`COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26 without CUDA)`:

```text
model_id  model_name                        num_params
0         SIMPLE_PINHOLE                    3
1         PINHOLE                           4
2         SIMPLE_RADIAL                      4
3         RADIAL                             5
4         OPENCV                             8
5         OPENCV_FISHEYE                     8
6         FULL_OPENCV                        12
7         FOV                                5
8         SIMPLE_RADIAL_FISHEYE              4
9         RADIAL_FISHEYE                     5
10        THIN_PRISM_FISHEYE                 12
11        RAD_TAN_THIN_PRISM_FISHEYE         16
```

`FULL_FOV` is **not** in this table — it was rejected by the real
`model_converter` and must not be invented as model id 8. Unknown model ids
raise `ValueError("model=99 ... 不在已知 COLMAP 模型表")` instead of
inferring parameter count from remaining bytes.

### 2. Real COLMAP binary fixture committed

`tests/fixtures/colmap/`:

- `text/cameras.txt`, `text/images.txt`, `text/points3D.txt` — hand-written
  from the COLMAP 4.1.0 `BaseCameraModel::CameraName` list and the
  parameter counts above. Independent of the production parser's sibling
  test writers (`_write_fake_cameras_bin` / `_write_fake_images_bin`).
- `bin/cameras.bin` (984 bytes) — byte-exact output of
  `colmap model_converter --input_path text --output_path bin --output_type BIN`
  on the cameras.txt above. Covers all 12 accepted model ids 0..11, one
  camera per model, with positive focal (1024), centered principal point,
  zero distortion, and FOV omega=1.0 (the only value COLMAP's
  `VerifyParams()` accepts alongside the centered principal point).
- `bin/images.bin` (1016 bytes) — 12 images, one bound to each camera id
  1..12. Identity quaternion `(1,0,0,0)`, finite translations.
- `bin/points3D.bin` (8 bytes) — empty 3D point list (header only).
- `bin/frames.bin`, `bin/rigs.bin` — COLMAP's own optional outputs.
- `README.md` — regeneration command, generation host, provenance notes.

A parser that agreed with its sibling fake writer but disagreed with the
official COLMAP binary format would fail the tests below.

### 3. Sparse semantic validation enhanced

`_validate_sparse_semantics(sparse_0, photos)` now rejects:

- `cameras.bin`: zero/duplicate `camera_id`, zero `width`/`height`,
  non-finite params, non-positive focal (params[0]).
- `images.bin`: zero/duplicate `image_id`, duplicate `image_name`,
  absolute/traversing names, non-finite `qvec`/`tvec`, near-zero
  (unnormalizable) quaternions, references to absent `camera_id`.
- Cross-file: every `image_name` must have a matching file under `photos/`
  (no phantom images).
- Does **not** require `num_reg_images == len(photos)` — COLMAP normally
  drops unregistered images, which is an algorithm result not an error.

### 4. Strict UTF-8 for image_name

`_parse_colmap_images_bin` now decodes `image_name` with `decode("utf-8")`
(strict), not `errors="replace"`. Non-UTF-8 names raise `ValueError`.

### 5. Source-side semantic validation

`_build_precomputed_manifest` calls `_validate_sparse_semantics` at the
source (before any copy), so a bad source is rejected before Brush sees
its first byte. After copy, `_validate_ws_precomputed` re-verifies bytes
and the copy step re-runs `_validate_sparse_semantics` to catch
copy-time corruption.

## Acceptance against HANDOFF-GLM-008 §2 RED-to-green items

| Required | Evidence |
|---|---|
| 1. Bind parser to COLMAP schema; reject unknown model ids | `_COLMAP_MODEL_NUM_PARAMS` table; `ValueError("model=99 ...")` in `_parse_colmap_cameras_bin`; `test_unknown_model_id_rejected_with_hand_crafted_bytes` |
| 2. Add at least one real-COLMAP or independently-derived fixture | `tests/fixtures/colmap/bin/cameras.bin` produced by `colmap model_converter` on hand-written text sources; covers all 12 models |
| 3. Reject duplicate/zero camera ids, zero dimensions, non-finite params, non-positive focal | `_validate_sparse_semantics` cameras block; existing `TestPrecomputedSemanticValidation` cases |
| 4. Parse images.bin with strict UTF-8; reject duplicate/zero image ids, duplicate names, absolute/traversing names, non-finite qvec/tvec, near-zero/non-normalizable quaternions, absent camera_id refs | `_parse_colmap_images_bin` strict UTF-8 + `_validate_sparse_semantics` images block |
| 5. Normalize only safe relative image paths; bind every registered name to exact per-photo SHA | `_validate_sparse_semantics` rejects absolute/traversal; per-photo SHA already bound via `_photos_sha256` in `_build_precomputed_manifest` |
| 6. Keep COLMAP behavior that some source photos may remain unregistered | `_validate_sparse_semantics` explicitly does not require `num_reg_images == len(photos)` (docstring + test) |
| 7. Run full focused suite + Ruff; report exact commands and counts | See "Test evidence" below |

## Test evidence

Commands and counts:

```text
.venv\Scripts\python.exe -m pytest tests/test_reconstruct_local.py -q
→ 105 passed in 5.61s

.venv\Scripts\python.exe -m ruff check scripts/reconstruct_local.py tests/test_reconstruct_local.py
→ All checks passed!
```

New tests added (5):

- `TestRealColmapFixture::test_parse_real_cameras_bin_all_twelve_models`
- `TestRealColmapFixture::test_parse_real_images_bin_and_cross_reference_cameras`
- `TestRealColmapFixture::test_real_fixture_passes_semantic_validation`
- `TestRealColmapFixture::test_unknown_model_id_rejected_with_hand_crafted_bytes`
- `TestRealColmapFixture::test_real_cameras_bin_byte_size_matches_model_table`

The 100 prior tests remain green; no behavior change to existing tests
other than the tuple→list assertion correction in
`test_parse_real_images_bin_and_cross_reference_cameras` (parser returns
list, not tuple).

## Real COLMAP fixture regeneration (reproducibility)

```powershell
$colmap = "third\colmap\bin\colmap.exe"
Remove-Item -Recurse -Force "tests\fixtures\colmap\bin" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "tests\fixtures\colmap\bin" | Out-Null
& $colmap model_converter `
  --input_path tests\fixtures\colmap\text `
  --output_path tests\fixtures\colmap\bin `
  --output_type BIN
```

The committed `.bin` files are the byte-exact output of this command on
the committed `.txt` sources. Do not hand-edit the `.bin` files.

## Trust claims

- No `accepted:true`, no `Reviewer: Codex` self-attribution.
- The fixture covers all 12 accepted model ids; it does **not** prove that
  every COLMAP version accepts these (only 4.1.0 fa8e3b3 was tested).
- The semantic validator's "image_name ↔ photos" binding only checks file
  existence; per-photo SHA-256 binding is in `_photos_sha256` (P7a-1),
  not in the semantic validator itself.
- This closure is for Task 2 only; Task 3 (transactional replacement)
  and Task 4 (auditable source report with canonical-payload SHA + report-
  byte SHA separation) remain open.

## Files owned in this commit

- `scripts/reconstruct_local.py`
- `tests/test_reconstruct_local.py`
- `tests/fixtures/colmap/README.md`
- `tests/fixtures/colmap/text/cameras.txt`
- `tests/fixtures/colmap/text/images.txt`
- `tests/fixtures/colmap/text/points3D.txt`
- `tests/fixtures/colmap/bin/cameras.bin`
- `tests/fixtures/colmap/bin/images.bin`
- `tests/fixtures/colmap/bin/points3D.bin`
- `tests/fixtures/colmap/bin/frames.bin`
- `tests/fixtures/colmap/bin/rigs.bin`
- `handoff/FEEDBACK-HANDOFF-GLM-008-task2-colmap-real-fixture-and-semantic-validation.md`

## Next task

HANDOFF-GLM-008 §3: transactional replacement of `_copy_precomputed_to_ws`
with fresh staging + journal + rollback + restart recovery. RED tests first.
