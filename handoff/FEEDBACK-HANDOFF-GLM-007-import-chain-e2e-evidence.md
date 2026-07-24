# FEEDBACK-HANDOFF-GLM-007 — End-to-end synthetic import chain rehearsal

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex
Handoff: `handoff/HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md`

## 1. What was delivered

A bug fix in `scripts/canary_gt_to_colmap.py` plus a full end-to-end rehearsal
of the synthetic reconstruction import chain: canary GT renders → COLMAP text
dataset → Brush training → normalize → prepare_import → chunk_reconstruction →
verify_chunks_integrity.

This closes the machine-proven chunk/import integrity gap by exercising P3's
SHA binding and P1's artifact verifier against real trained data — not just
unit tests.

### Owned paths (this commit)

```text
scripts/canary_gt_to_colmap.py          (bug fix: c2w field name)
tests/test_canary_gt_to_colmap.py       (3 new tests for field resolution)
handoff/FEEDBACK-HANDOFF-GLM-007-import-chain-e2e-evidence.md  (this file)
```

No Codex-owned path was touched.

## 2. Bug fix: canary_gt_to_colmap.py field name

The script expected `measured_c2w_opencv` in camera metadata, but the actual
`nantai.synthetic-village.camera-metadata.v1` schema uses `c2w_opencv`. No
existing render set on disk used the `measured_` prefix, so the script could
not run against any real canary render.

Fix: added `_c2w_opencv(meta)` helper that checks for `measured_c2w_opencv`
first (test fixtures) and falls back to `c2w_opencv` (production v1 schema).
All 4 call sites updated. Docstring corrected.

Tests: 3 new tests in `TestC2wFieldResolution` covering both field names and
missing-field fail-closed. All 52 tests pass (49 existing + 3 new).

## 3. End-to-end chain results

### 3.1 Canary GT → COLMAP dataset

- Source: `tmp/render-canary-pre-format-f6b05e76/` (build_id `344e643c...`)
- 24 cameras, 3 intrinsics groups, all self-checks passed:
  - R rigidity: all 24 cameras
  - Quaternion round-trip: all 24 cameras
  - Cross-camera depth consistency: median relative error 0.0011 / 0.0008
- Output: 24 images + COLMAP text (cameras/images/points3D)
- 49,308 initialization points from GT depth backprojection

| File | SHA-256 |
|---|---|
| cameras.txt | `efcfe78b5c9e99fbf5ed1b86349c409c996af7f333c6568179ebc3ce12af4402` |
| images.txt | `c2a9c401d639430efd1151cf51a0ab459392c628c3d9f8e81381a047760a1ac3` |
| points3D.txt | `2823349517ba8949dd40f6dd54f52fbb7673c405b87ef81d9db4ec9538a07cc7` |

### 3.2 Brush training

- Executable: `third/brush/brush_app.exe` 0.3.0 (wgpu on Intel UHD 770)
- Args: `--total-steps 2000 --max-resolution 1024 --export-every 2000`
- Duration: 283 seconds (~4.7 minutes)
- Final splat count: 68,446
- Trained PLY SHA-256: `476d07fed73bae816f128d77cd1effb462cfbb4b5aef4282c63d4985ea3c903a`
- Trained PLY size: 16,154,806 bytes (~16 MB)

### 3.3 Normalize + prepare_import

- `normalize_ply_quats`: 0 non-unit quaternions (already normalized). SHA
  changed slightly (metadata normalization): `b5e3ce24...`
- `prepare_import --synthetic`: produced `registration.json` + `splat-input.json`
- Registration correctly declares:
  - `engine=external`
  - `frame_id=synthetic-local`
  - `metric_status=arbitrary`
  - `provenance=synthetic`
  - Trust: **not promoted** (preview-proxy, arbitrary units, unaligned)

### 3.4 Chunk reconstruction (P3 SHA binding e2e)

- 68,446 gaussians → 259 chunks (50m grid)
- `verify_chunks_integrity` result:
  - `valid: true`
  - `per_chunk_sha_verified: true`
  - `verified_payloads: 777` (259 × 3 LOD levels)
  - `total_payloads: 777`
  - `mismatches: []`

This proves P3's SHA binding works against a real trained PLY, not just
unit-test fixtures. Every chunk and every LOD payload has a correct SHA-256
and byte size.

### 3.5 Existing manifest verification

- `verify_recon_artifacts` on `web/data/recon/recon_manifest.json`: exit 0
  - All 4 artifacts (full_3dgs + 3 LODs) verified
  - `trust_preserved=True`
- `inspect_recon`: exit 0
  - Correctly reports: "合成占位几何, 不是真实重建"
  - `geometry_usability=preview-proxy`
  - 68,432 gaussians, arbitrary units

## 4. What was NOT done

- `reconstruct --engine import` was NOT run because it writes to `web/data/`
  which is a Codex-owned WIP path. The import contracts (`registration.json`
  + `splat-input.json`) are ready for Codex to run the final import step.
- The trained PLY is NOT a real reconstruction. It is a synthetic
  Brush-on-canary-GT trial-tier artifact from Intel iGPU, not a CUDA cloud
  training result.

## 5. Honest limits

- **Not real-scene evidence**: this is a synthetic import chain rehearsal.
  The five real-scene evidence items in handoff §1 remain absent.
- **Not CUDA training**: Brush ran on Intel UHD 770 iGPU, not NVIDIA CUDA.
  The trained PLY is a limited small-scene trial, not production-quality 3DGS.
- **GT-pose injection, not real SfM**: `canary_gt_to_colmap.py` bypasses
  COLMAP SfM by injecting known camera poses. This is NOT a substitute for
  the real COLMAP rehearsal in handoff §7 (P4) or §8 (P5).
- **Trust unchanged**: all artifacts remain `synthetic / preview-proxy /
  arbitrary-units / unaligned`. No geometry, metric alignment, or training
  trust was promoted.
- **Import not run**: `web/data/recon/` was not modified. The existing
  manifest is a prior synthetic import, not the result of this rehearsal.

## 6. Next queue item

Per handoff §8 (P5), the next task is the COLMAP topology-overfit rehearsal:
run real COLMAP on a second immutable Blender capture topology that differs
materially from P4, plus an adversarial subset with weak overlap.

This import-chain rehearsal was a bonus item (closing the machine-proven
chunk/import integrity gap) completed while transitioning to the formal P5.
