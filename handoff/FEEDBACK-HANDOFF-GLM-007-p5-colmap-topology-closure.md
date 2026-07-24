# FEEDBACK-HANDOFF-GLM-007-p5 — COLMAP topology-overfit and failure-envelope rehearsal

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex
Handoff: `handoff/HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md` §8 P5

## 1. What was delivered

P5 is closed as an evidence-only feedback document. The real COLMAP 4.1.0
executable was run through the repository's production registration path
(`pipeline.registration.register` with `engine="colmap"`) on two materially
different capture topologies derived from the same immutable Blender build.
No code was added because the rehearsal exposed no reproducible caller or
fail-closed defect.

### Trust declaration

All results remain `synthetic-capture / sfm-local / arbitrary-units /
unaligned`. This rehearsal does not close real capture, accepted SfM,
cloud-GPU training, metric alignment or Viewer QA.

## 2. Rehearsal setup

### 2.1 Source capture set (shared with P4)

- **Source build**: `0f26388f0560b520c16feb348a7902c83de29ab531cf7c77f31d2d32ab90e004`
- **Source `.blend` SHA-256**:
  `c6cda1163186616752961cc2475da61058dcd21ee162c5a1bec7fc38ae1d12fa`
- **Source build-report SHA-256**:
  `7cbedca367319687cb25a543e2692e5e78e3baecc0d27abf66002bbdbd99abb2`
- Images reused from P4's immutable photos directory (same SHAs).

### 2.2 Tool chain

- **COLMAP binary**: `third/colmap/bin/colmap.exe`
- **COLMAP version**: `COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26 without CUDA)`
- **GPU**: disabled (`--FeatureExtraction.use_gpu 0`,
  `--FeatureMatching.use_gpu 0`)

### 2.3 Subset 1 — Orbit topology (8 outer cameras)

- **Cameras**: `camera-outer-001..008` (pure circular orbit around the village)
- **Differs from P4**: P4 used a mixed 24-camera set (bridge×4, courtyard×4,
  ground×8, outer×8); this subset uses only the 8 outer orbit cameras.
- **Matcher**: `exhaustive_matcher` (8 images → 28 pairs)
- Every input image SHA-256 and size is recorded in the evidence JSON.

### 2.4 Subset 2 — Adversarial subset (4 cameras, deliberately weak overlap)

- **Cameras**: `camera-bridge-001`, `camera-bridge-003`, `camera-outer-001`,
  `camera-outer-005`
- **Design**: bridge-001 and bridge-003 face opposite directions;
  outer-001 and outer-005 are on opposite sides of the village.
  Minimal shared viewpoints and only 4 images → designed to fail honestly.
- **Matcher**: `exhaustive_matcher` (4 images → 6 pairs)
- Every input image SHA-256 and size is recorded in the evidence JSON.

## 3. Results

### 3.1 Subset 1 — Orbit (8 outer cameras)

| Metric | Value |
|---|---|
| check_capture verdict | `unlikely` |
| check_capture blockers | only 8 images; 8/8 blurry (100%); 8 below 1.0 MP |
| COLMAP exit status | FAILED (RuntimeError raised by production path) |
| Elapsed | 1.2 seconds |
| Failure stage | mapper — "No good initial image pair found" |
| Sparse models produced | 0 |
| Registered images | 0/8 (0%) |
| Registration JSON | not produced (mapper raised before output) |
| colmap.db SHA-256 | `9e13f8a771c8b94f14911de067b3c9363ec1840d2d4b747f17283b53b918fd85` |

### 3.2 Subset 2 — Adversarial (4 cameras, weak overlap)

| Metric | Value |
|---|---|
| check_capture verdict | `unlikely` |
| check_capture blockers | only 4 images; 4/4 blurry (100%); 4 below 1.0 MP |
| COLMAP exit status | FAILED (RuntimeError raised by production path) |
| Elapsed | 1.1 seconds |
| Failure stage | **matching** — "No images with matches" (0 verified matches) |
| Sparse models produced | 0 |
| Registered images | 0/4 (0%) |
| Registration JSON | not produced (mapper raised before output) |
| colmap.db SHA-256 | `8058da73525b268ebb228d856b7c1f5778535f6316cf8aef624a9ac53643eec8` |

### 3.3 Key finding: different failure stages across topologies

| Topology | Images | Pairs | Failure stage | Failure detail |
|---|---|---|---|---|
| P4 mixed | 24 | 276 | mapper | 37 verified matches max/pair, but no initial pair met inlier threshold |
| P5 orbit | 8 | 28 | mapper | "No good initial image pair found" (same stage as P4) |
| P5 adversarial | 4 | 6 | **matching** | "No images with matches" (0 verified matches — failed earlier than P4/orbit) |

The adversarial subset exposed an **earlier failure stage** than P4 or the
orbit subset: COLMAP's exhaustive matcher produced zero verified matches across
all 6 pairs, so the mapper could not even attempt to find an initial image
pair. This is a materially different failure mode from P4's "had matches but
too few inliers for initialization."

This confirms the failure envelope spans both the matching and mapper stages
depending on capture topology and image count.

## 4. Comparison with P4

| Metric | P4 (24 mixed) | P5 orbit (8 outer) | P5 adversarial (4 weak) |
|---|---|---|---|
| Topology | mixed route + orbit | pure orbit | adversarial weak overlap |
| Images | 24 | 8 | 4 |
| Match pairs attempted | 276 | 28 | 6 |
| Failure stage | mapper | mapper | **matching** |
| Sparse models | 0 | 0 | 0 |
| Registration rate | 0% | 0% | 0% |
| Elapsed | 1.3 s | 1.2 s | 1.1 s |
| COLMAP version | 4.1.0 | 4.1.0 | 4.1.0 |
| Production path | `register(engine="colmap")` | same | same |

The results are consistent with the P4 root cause: untextured v1 canary
surfaces lack SIFT feature gradients, so no topology produces enough matches
for registration. The adversarial subset additionally proves that with only
4 cameras and minimal shared viewpoints, the matcher itself produces zero
matches — an earlier and more decisive failure.

## 5. Production path behaviour (no defect found)

The rehearsal confirms the production caller boundary works correctly across
all three topologies:

1. **check_capture** correctly warned `unlikely` with specific blockers before
   COLMAP ran for both subsets, including the additional "only N images" blocker
   that P4 (24 images) did not trigger.
2. **`pipeline.registration.register(engine="colmap")`** correctly invoked
   `colmap_register()` for both subsets, running all three COLMAP stages.
3. **`colmap_register()`** correctly raised `RuntimeError` when the mapper
   failed to produce `sparse/0`, with the full COLMAP stderr in the message.
4. **No partial result was written**: `registration.json` was not produced
   for either subset because the function raised before reaching output.
5. **No trust was promoted**: both failures were fail-closed; no `sfm-local`
   frame, no poses, no coverage report was emitted.

No reproducible caller or fail-closed defect was exposed across the three
topologies. The production path is correct.

## 6. Honest limits (not promoted)

- **Not real-scene evidence**: this is a synthetic-capture rehearsal. It
  proves the production COLMAP caller handles different topologies, not that
  real photos can be reconstructed.
- **Non-textured v1 build**: the source canary is a v1 (non-textured) build.
  Textured v2 builds may produce more SIFT features and different registration
  outcomes. This rehearsal does not predict textured-build results.
- **0% registration is consistent with P4 and the 2026-07-16 feasibility
  test**: untextured synthetic surfaces lack the texture gradients SIFT
  depends on. This was already known and documented.
- **Small image counts**: both subsets use fewer than 20 images, which
  check_capture correctly flags as a blocker ("手册: <20 张基本无望重建").
- **check_capture blur scores are heuristic**: the "100% blurry" verdict
  reflects the lack of high-frequency texture content in synthetic flat-shaded
  surfaces, not actual motion blur.
- The five real-scene evidence items in §1 of the handoff remain absent:
  1. real overlapping capture with known acquisition provenance;
  2. accepted COLMAP/SfM poses and sparse geometry;
  3. one non-mock cloud-GPU 3DGS training result;
  4. imported splat artifact with measured alignment;
  5. Viewer QA over that real artifact.

## 7. Evidence artifacts

- **Evidence JSON**:
  `.nantai-studio/p5-colmap-topology/p5_topology_evidence.json`
  (SHA-256 `9ed976aa8ef95c54ddc4d57b3bc8474d44b79e94ea20af7b0f17d5f1e90edb55`)
- **Orbit COLMAP database**:
  `.nantai-studio/p5-colmap-topology/orbit/workspace/colmap.db`
  (SHA-256 `9e13f8a771c8b94f14911de067b3c9363ec1840d2d4b747f17283b53b918fd85`)
- **Adversarial COLMAP database**:
  `.nantai-studio/p5-colmap-topology/adversarial/workspace/colmap.db`
  (SHA-256 `8058da73525b268ebb228d856b7c1f5778535f6316cf8aef624a9ac53643eec8`)
- **Copied input images**: 8 PNGs in `orbit/photos/`, 4 PNGs in
  `adversarial/photos/`
- All artifacts are in private rehearsal workspace; nothing was committed to
  the repository tree or registry.

## 8. Next queue item

Per handoff §9: "After the evidence document is committed, continue directly
to P6."

P6 is the real video-extraction boundary rehearsal. It exercises the user's
eventual "one long video" entry path without waiting for the real video:

1. Encode or select one immutable synthetic ordered video from a bound Blender
   capture sequence.
2. Exercise the actual local FFmpeg/video path used by
   `scripts/reconstruct_local.py`.
3. Prove the extracted ordered set selects the production sequential matcher
   path and then reaches the same real COLMAP boundary as P4.

Remaining real-scene blockers (unchanged):
1. real overlapping capture with known acquisition provenance;
2. accepted COLMAP/SfM poses and sparse geometry;
3. one non-mock cloud-GPU 3DGS training result;
4. imported splat artifact with measured alignment;
5. Viewer QA over that real artifact.
