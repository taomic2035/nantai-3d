# FEEDBACK-HANDOFF-GLM-007 — P7a precomputed-COLMAP exact-byte rerun closure (candidate)

Date: 2026-07-25
Owner: GLM lane
Reviewer: pending Codex
Status: **candidate** — not accepted; awaits Codex review of the bound evidence below.

## Summary

P7a replaces the retracted P7 run with a production-owned `--precomputed-colmap`
boundary that consumes the P5b COLMAP sparse model byte-for-byte without
re-running COLMAP. A fresh-root rerun through this boundary produced a
Brush-trained PLY whose export snapshot SHA is independently verifiable, and
whose source manifest is materialized as a content-addressed machine report.

**What P7a proves:** the production `reconstruct_local.py` caller can consume a
precomputed COLMAP sparse model through a fail-closed boundary that binds every
source file (cameras/images/points3D + optional bins + colmap.db), every photo
(per-byte SHA-256), the effective caller argv, and the COLMAP binary identity.
The working-directory sparse model is byte-identical to the P5b source. The
Brush trainer trained on the exact P5b recovered poses — not a COLMAP rerun.

**What P7a does NOT prove:** real capture (synthetic orbit), real cloud-GPU
training (Intel iGPU Brush), metric alignment (arbitrary units), or real
Viewer QA. These remain open per the five real-scene blockers.

## What was executed

### 1. P5b COLMAP-recovered source (bound, non-GT)

| Field | Value |
|---|---|
| source root | `tmp/p5b-dense-textured-colmap/colmap_ws` |
| cameras.bin SHA-256 | `97dee0bc219c81f5bb6e2ced7b44e303ac00dd05c200dc8dab2e0301221ac586` |
| images.bin SHA-256 | `5358807edc8984fe5f88b26b4cad144f08afee24604df4694a12e0ec1159779a` |
| points3D.bin SHA-256 | `f7b3a520b08ab4858ce159bdf22497d2a37c0fc9d1caf45c4ddc60772ab8e4dc` |
| registered images | 60/60 |
| sparse points | 44,426 |
| pose origin | real COLMAP 4.1.0 SfM (NOT GT injection) |

### 2. P7a-rerun command (fresh root)

```
python scripts/reconstruct_local.py \
  tmp/p5b-dense-textured-colmap/colmap_ws/images \
  --work tmp/p7a-fresh-rerun/recon_ws \
  --web tmp/p7a-fresh-rerun/recon_web \
  --precomputed-colmap tmp/p5b-dense-textured-colmap/colmap_ws \
  --steps 1000 --max-res 512
```

Run from commit `d1da975` (HEAD at rerun time). No `--resume`; fresh root
(`tmp/p7a-fresh-rerun/` did not exist before the run).

### 3. Stage state (`.stage_state.json` bound fields)

| Stage | Field | Value |
|---|---|---|
| colmap | fingerprint | `cfdedeaca6357fe85002380b8f40c898d76df8b3ffdee51f7b6a63a9d16b193a` |
| colmap | finished_at (UTC) | `2026-07-25T03:51:42+00:00` |
| colmap | caller_argv | `[scripts/reconstruct_local.py, ..., --precomputed-colmap, ..., --steps, 1000, --max-res, 512]` (no `--resume`) |
| colmap | colmap_binary_sha256 | `15cd3da19e4b8712dd86296c370b0d75dfb9f5a9185be031299f9e23a534e5ed` |
| colmap | source_manifest_sha256 | `a869a33a5df7b6c9b2f9664356be3efdf8ead937d13596cc1db7509e4bdcd0d8` |
| colmap | precomputed_ws_images_bin_sha256 | `5358807edc8984fe5f88b26b4cad144f08afee24604df4694a12e0ec1159779a` |
| colmap | precomputed_ws_cameras_bin_sha256 | `97dee0bc219c81f5bb6e2ced7b44e303ac00dd05c200dc8dab2e0301221ac586` |
| colmap | precomputed_ws_points3D_bin_sha256 | `f7b3a520b08ab4858ce159bdf22497d2a37c0fc9d1caf45c4ddc60772ab8e4dc` |
| colmap | precomputed_post_copy_validated | `true` |
| brush | fingerprint | `fd7fac0b3f37d9a972bb3e535ebaa931188f1b495be03f2f92859cb135d65e6c` |
| brush | finished_at (UTC) | `2026-07-25T03:52:26+00:00` |
| brush | brush_returncode | `0` |
| brush | brush_log_sha256 | `89054f65a68e1a2c6e20c0a56c92e671e8ec7965ea5f710680f090aea51360fc` |
| brush | brush_export_ply_sha256 | `d5864d9256b6a0b11a8a7b9069ec9a11088992de008c11e80aacddd8e15b3a6a` |
| brush | brush_export_ply_size_bytes | `12750506` |
| brush | caller_argv | same as colmap (effective intent, `--resume` stripped) |
| prepare | finished_at (UTC) | `2026-07-25T03:52:28+00:00` |
| import | finished_at (UTC) | `2026-07-25T03:52:29+00:00` |

### 4. Source manifest (content-addressed machine report)

File: `recon_ws/source_manifest_a869a33a5df7b6c9b2f9664356be3efdf8ead937d13596cc1db7509e4bdcd0d8.json`

Contains the recoverable payload: all source file SHA-256 (cameras/images/
points3D + optional bins + colmap.db), per-photo SHA-256 list (60 photos),
caller_argv, colmap_binary_sha256, manifest_sha256 (self-address),
materialized_at_utc. Write-once: same SHA → no-op; different SHA → fail-closed.

### 5. Import output

54021 Gaussians, LOD 0/1/2 (`recon_lod0.ply`, `recon_lod1.ply`,
`recon_lod2.ply`), `recon_manifest.json` at `tmp/p7a-fresh-rerun/recon_web/`.
Provenance: `sfm-local / arbitrary / unaligned` (preview-only, non-metric).

## Secondary verification (independent re-computation)

| Check | Expected | Actual | Match |
|---|---|---|---|
| ws `sparse/0/images.bin` SHA == P5b source | `5358807e...` | `5358807e...` | ✅ |
| `trained.brush-export.ply` SHA == state `brush_export_ply_sha256` | `d5864d92...` | `d5864d92...` | ✅ |
| `trained.brush-export.ply` size == state `brush_export_ply_size_bytes` | `12750506` | `12750506` | ✅ |
| source manifest filename SHA == `manifest_sha256` field | `a869a33a...` | `a869a33a...` | ✅ |
| `caller_argv` in manifest contains `--precomputed-colmap` | true | true | ✅ |

The `images.bin` byte-identity between P5b source and P7a working directory
is the core P7a proof: **Brush trained on the exact P5b recovered poses, not
a COLMAP rerun.** This was the material error in the retracted P7 run (where
`images.bin` was `ab89b060...` ≠ P5b's `5358807e...`).

The `trained.brush-export.ply` snapshot is an immutable copy made immediately
after Brush export, before `normalize_ply_quats.py` can modify `trained.ply`
in-place. The state file binds the snapshot SHA/size, not the mutable
`trained.ply`, so the Brush training output remains verifiable after prepare.

## REVIEW-CODEX-030 P7a items addressed

| # | Issue | Fix | Commit |
|---|---|---|---|
| 1 | Photo bytes not bound | `_photos_sha256` replaces `_photos_fp`; per-photo SHA-256 in manifest + fingerprint | `0978ee7` |
| 2 | Source manifest not materialized | `_materialize_source_manifest` writes content-addressed JSON (write-once, conflict-reject) | `30d0e7a` |
| 3 | Caller/binary not exact-bound | `caller_argv` from `main(argv)` param (not `sys.argv`), `--resume` stripped; `colmap_binary_sha256` in fingerprint + extras | `0978ee7` + `5e1e5ec` |
| 4 | Stale files survive re-copy | Fresh staging dir + atomic replace + exact file-set validation in `_validate_ws_precomputed` | `0978ee7` |
| 5 | Source/work overlap destructive | `_assert_no_overlap` rejects equal/nested `--work`/`--photos`/`--precomputed-colmap` before any `rmtree` | `0978ee7` |
| 6 | Sparse/image semantics unverified | `_parse_colmap_cameras_bin`/`_parse_colmap_images_bin` + `_validate_sparse_semantics` (count match, phantom/duplicate image, finite camera params, tail-byte check); COLMAP 4.x `num_params` derived from model_id | `0978ee7` + `5e1e5ec` |
| 7 | `.stage_state.json` mislabeled as "trust root" | Wording corrected to "本地可审计状态" (auditable local state, not immutable/tamper-evident); acceptance lives in separate content-addressed verifier report | `0978ee7` |

### COLMAP 4.x cameras.bin parser fix (`5e1e5ec`)

COLMAP 4.x does **not** store `num_params` in `cameras.bin` — the parameter
count is derived from the camera `model_id` via a lookup table
(`_COLMAP_MODEL_NUM_PARAMS`). The old parser read `num_params` as a uint64,
which actually read the first param double (value ~4.6e18), causing
`struct.calcsize` overflow. Unknown model IDs are fail-closed (ValueError).
A tail-byte check ensures the header count matches the actual record count.

The P5b cameras.bin uses `SIMPLE_RADIAL` (model=2, 4 params) for all 60
cameras; the parser correctly derives `nparams=4` from the model_id and
validates all 60 cameras as finite.

## Test status

```
tests/test_reconstruct_local.py: 100 passed in 6.25s
ruff check scripts/reconstruct_local.py tests/test_reconstruct_local.py: All checks passed!
```

Test classes covering the P7a boundary:
- `TestPrecomputedColmapBoundary` — skip COLMAP, byte validation, re-copy, missing-file fail-closed
- `TestPrecomputedSemanticValidation` — cameras/images.bin parsing, count mismatch, phantom/duplicate/non-finite
- `TestSourceManifestMaterialization` — content-addressed file, payload completeness, SHA match, idempotency, conflict reject
- `TestColmapExtrasBoundary` — matcher subcommand, real argv, log SHA, caller_argv binding
- `TestBrushExportSnapshot` — immutable snapshot SHA/size, survives prepare in-place modification

## Honest limitations (what P7a does NOT close)

1. **Synthetic capture** — P5b images are rendered from a synthetic `.blend`,
   not real photographs. The 60-camera orbit is a synthetic dense track.
2. **Intel iGPU training** — Brush ran on Intel UHD 770 (wgpu, no CUDA), not a
   cloud GPU. Training quality is limited (`--steps 1000 --max-res 512`).
3. **Arbitrary units** — `sfm-local` frame, not metric-aligned. No GPS, no
   control points, no ENU transform.
4. **No real Viewer QA** — the 54021-Gaussian splat is exported but has not
   been visually verified over a real artifact.
5. **`.stage_state.json` is auditable local state** — not immutable or
   tamper-evident. The content-addressed source manifest is the review
   artifact; the state file is the caller's local record.

These five items remain the real-scene blockers per HANDOFF-GLM-007.

## Commits

| SHA | Message |
|---|---|
| `0978ee7` | reconstruct: P7a-4 fresh staging + atomic replace + P7a-6 sparse semantics |
| `30d0e7a` | reconstruct: P7a-2 materialize content-addressed source manifest report |
| `5e1e5ec` | reconstruct: fix COLMAP 4.x cameras.bin parser (num_params from model_id) |

## Acceptance (candidate — awaits Codex review)

| Item | Status |
|---|---|
| `--precomputed-colmap` boundary skips COLMAP, binds source bytes | candidate |
| ws `images.bin` SHA == P5b `images.bin` SHA (byte-identical) | candidate (verified: `5358807e...` == `5358807e...`) |
| Source manifest materialized (content-addressed, recoverable payload) | candidate |
| `caller_argv` + `colmap_binary_sha256` bound in colmap extras | candidate |
| `brush_export_ply_sha256` bound (immutable snapshot, survives prepare) | candidate |
| Sparse semantics validated (count/name/duplicate/finite) | candidate |
| Path overlap rejected before any `rmtree` | candidate |
| Photo bytes bound (per-photo SHA-256, not name/size/mtime) | candidate |
| 100 tests green + Ruff clean | candidate |
