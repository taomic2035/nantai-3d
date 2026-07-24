# FEEDBACK-HANDOFF-GLM-007-p4 — Real COLMAP executable rehearsal on synthetic captures

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex
Handoff: `handoff/HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md` §7 P4

## 1. What was delivered

P4 is closed as an evidence-only feedback document. The real COLMAP 4.1.0
executable was run through the repository's production registration path
(`pipeline.registration.register` with `engine="colmap"`) on an immutable
synthetic capture set. No code was added because the rehearsal exposed no
reproducible caller or fail-closed defect.

### Trust declaration

All results remain `synthetic-capture / sfm-local / arbitrary-units /
unaligned`. This rehearsal does not close real capture, accepted SfM,
cloud-GPU training, metric alignment or Viewer QA.

## 2. Rehearsal setup

### 2.1 Source capture set

- **Source build**: `0f26388f0560b520c16feb348a7902c83de29ab531cf7c77f31d2d32ab90e004`
- **Schema**: `nantai.synthetic-village.blender-build-report.v1` (non-textured
  L2 simplified-PBR canary)
- **Source `.blend` SHA-256**:
  `c6cda1163186616752961cc2475da61058dcd21ee162c5a1bec7fc38ae1d12fa`
- **Source build-report SHA-256**:
  `7cbedca367319687cb25a543e2692e5e78e3baecc0d27abf66002bbdbd99abb2`
- **Input images**: 24 PNG renders at 1024x576 (0.59 MP) from 24 canary
  cameras (bridge-001..004, courtyard-001..004, ground-001..008,
  outer-001..008)
- Every input image SHA-256 and size is recorded in the evidence JSON
- Independent image2 design references were not used

### 2.2 Tool chain

- **COLMAP binary**: `third/colmap/bin/colmap.exe`
- **COLMAP version**: `COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26 without CUDA)`
- **SIFT option group**: `Feature` (COLMAP 4.x naming)
- **GPU**: disabled (`--FeatureExtraction.use_gpu 0`,
  `--FeatureMatching.use_gpu 0`)
- **Matcher**: `exhaustive_matcher` (24 images → 276 pairs, ≤400 threshold)

### 2.3 Exact COLMAP argv

```
# Stage 1: feature extraction
colmap.exe feature_extractor \
  --database_path <ws>/colmap.db \
  --image_path <photos> \
  --ImageReader.camera_model SIMPLE_RADIAL \
  --FeatureExtraction.use_gpu 0

# Stage 2: exhaustive matching
colmap.exe exhaustive_matcher \
  --database_path <ws>/colmap.db \
  --FeatureMatching.use_gpu 0

# Stage 3: incremental mapper
colmap.exe mapper \
  --database_path <ws>/colmap.db \
  --image_path <photos> \
  --output_path <ws>/sparse
```

## 3. Results

### 3.1 check_capture preflight

- **Exit code**: 0 (report produced)
- **Verdict**: `unlikely` — "发现硬伤, 照现在这批图跑 COLMAP 大概率白等"
- **Blockers**:
  - 24/24 images flagged as blurry (100%, blur scores 0.97–76.1, threshold 80.0)
  - 24 images below 1.0 MP (all 0.59 MP)
- **Warnings**: 24 images below recommended 50–300 range
- **Honest limit**: blur is a heuristic; overlap (the primary SfM success factor)
  cannot be measured from single-image analysis

### 3.2 COLMAP registration

- **Exit status**: FAILED (RuntimeError raised by production path)
- **Elapsed**: 1.3 seconds (feature extraction + matching + mapper)
- **Error**: `colmap mapper 失败 (exit 1): No good initial image pair found →
  Failed to create any sparse model`
- **Sparse models produced**: 0
- **Registered images**: 0/24 (0%)
- **Registration JSON**: not produced (mapper raised before output)

### 3.3 COLMAP database statistics

| Metric | Value |
|---|---|
| Images in database | 24 |
| Total keypoints | 8,284 |
| Keypoints/image (min) | 0 |
| Keypoints/image (avg) | 345.2 |
| Keypoints/image (max) | 1,134 |
| Raw match pairs (attempted) | 276 (all pairs) |
| Raw matches (total) | 444 |
| Verified match pairs | 276 |
| Verified matches (total) | 247 |
| Verified matches/pair (max) | 37 |
| Sparse models | **0** |

**colmap.db SHA-256**:
`8086a6b049b773e76a4189304a5dc3f35dc80d994f871d92e8ca8a2c7f5a649a`

### 3.4 Failure root cause

The mapper repeatedly attempted to find an initial image pair, progressively
relaxing initialization constraints, but failed every time:

```
Finding good initial image pair
=> No good initial image pair found.
Discarding reconstruction due to no initial pair
=> Relaxing the initialization constraints.
[repeated 5 times across 3 relaxation levels]
Failed to create any sparse model
```

**Root cause**: the best image pair had only **37 verified feature matches**,
far below the ~100+ inliers typically needed for COLMAP initialization. The
untextured v1 canary surfaces (flat-shaded walls, roofs, terrain) provide
insufficient SIFT feature gradients.

## 4. Comparison with 2026-07-16 feasibility test

| Metric | Feasibility test (2026-07-16, `344e643c`) | P4 rehearsal (2026-07-24, `0f26388f`) |
|---|---|---|
| Build | v1, untextured white model | v1, simplified-PBR (non-textured) |
| Images | 24 | 24 |
| Resolution | 1024x576 | 1024x576 |
| Keypoints avg/frame | 345 | 345.2 |
| Keypoints max/frame | 1,134 | 1,134 |
| Verified matches max/pair | ~37 (estimated) | 37 (measured) |
| Sparse models | 0 | 0 |
| Registration rate | 0% | 0% |
| COLMAP version | 4.1.0 | 4.1.0 (same binary) |
| Execution path | manual `reconstruct_local.py` | production `pipeline.registration.register` |

The results are consistent: both builds are v1 (non-textured) and produce
the same feature statistics. The P4 rehearsal additionally proves the
**production registration path** handles the failure correctly.

## 5. Production path behaviour (no defect found)

The rehearsal confirms the production caller boundary works as designed:

1. **check_capture** correctly warned `unlikely` with specific blockers before
   COLMAP ran, but did not block execution (it is advisory, not a gate).
2. **`pipeline.registration.register(engine="colmap")`** correctly invoked
   `colmap_register()`, which ran all three COLMAP stages in sequence.
3. **`colmap_register()`** correctly raised `RuntimeError` when the mapper
   failed to produce `sparse/0`, with the full COLMAP stderr in the message.
4. **No partial result was written**: `registration.json` was not produced
   because the function raised before reaching the output stage.
5. **No trust was promoted**: the failure was fail-closed; no `sfm-local`
   frame, no poses, no coverage report was emitted.

No reproducible caller or fail-closed defect was exposed. The production
path is correct.

## 6. Honest limits (not promoted)

- **Not real-scene evidence**: this is a synthetic-capture rehearsal. It
  proves the production COLMAP caller works, not that real photos can be
  reconstructed.
- **Non-textured v1 build**: the source canary is a v1 (non-textured) build.
  Textured v2 builds may produce more SIFT features and different registration
  outcomes. This rehearsal does not predict textured-build results.
- **0% registration is consistent with the 2026-07-16 feasibility test**:
  untextured synthetic surfaces lack the texture gradients SIFT depends on.
  This was already known and documented.
- **The 24-camera wide-baseline layout** (700x500m coverage) also contributes
  to low inter-image match counts. Dense video-frame captures would improve
  overlap but still depend on texture.
- **check_capture blur scores are heuristic**: the "100% blurry" verdict
  reflects the lack of high-frequency texture content in synthetic flat-shaded
  surfaces, not actual motion blur. The tool correctly reports this as a
  blocker.
- The five real-scene evidence items in §1 of the handoff remain absent:
  1. real overlapping capture with known acquisition provenance;
  2. accepted COLMAP/SfM poses and sparse geometry;
  3. one non-mock cloud-GPU 3DGS training result;
  4. imported splat artifact with measured alignment;
  5. Viewer QA over that real artifact.

## 7. Evidence artifacts

- **Evidence JSON**:
  `.nantai-studio/p4-colmap-rehearsal/p4_rehearsal_evidence.json`
  (SHA-256 `c7c221e31ae10d52a5ea4ec9a573ec07017b577a94904d4ed461e60870d39b7a`)
- **COLMAP database**:
  `.nantai-studio/p4-colmap-rehearsal/colmap_ws/colmap.db`
  (SHA-256 `8086a6b049b773e76a4189304a5dc3f35dc80d994f871d92e8ca8a2c7f5a649a`)
- **Copied input images**: 24 PNGs in
  `.nantai-studio/p4-colmap-rehearsal/photos/`
- All artifacts are in private rehearsal workspace; nothing was committed to
  the repository tree or registry.

## 8. Next queue item

Per handoff §7: "After P4, reread
`handoff/AUDIT-2026-07-22-real-3d-scene-gap-assessment.md`. If real input is
still absent, propose and start the highest-value unowned prerequisite."

Remaining open items:
- **P2b**: base-builder mapping correction (`TERRAIN_TEXTURE_SCALE=1.0`) with
  bound before/after RGB and repeat-density evidence. The mapping change is
  applied in the working tree; the Blender before/after evidence has not been
  produced.
- After P2b: propose the next unowned prerequisite from the real-scene gap
  assessment.
