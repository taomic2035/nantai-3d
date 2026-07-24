# FEEDBACK-HANDOFF-GLM-007-p6 — Real video-extraction boundary rehearsal

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex
Handoff: `handoff/HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md` §9 P6

## 1. What was delivered

P6 is closed as an evidence-only feedback document. The real video-extraction
boundary was exercised end-to-end: a synthetic ordered video was created from
bound Blender capture frames, then run through the actual `pipeline.ingest`
cv2 extraction path and COLMAP `sequential_matcher` path used by
`scripts/reconstruct_local.py`. No code was added because the rehearsal
exposed no reproducible caller or fail-closed defect.

### Trust declaration

All results remain `synthetic-capture / sfm-local / arbitrary-units /
unaligned`. This rehearsal does not close real capture, accepted SfM,
cloud-GPU training, metric alignment or Viewer QA.

## 2. Rehearsal setup

### 2.1 Source capture set (shared with P4/P5)

- **Source build**: `0f26388f0560b520c16feb348a7902c83de29ab531cf7c77f31d2d32ab90e004`
- **Source `.blend` SHA-256**:
  `c6cda1163186616752961cc2475da61058dcd21ee162c5a1bec7fc38ae1d12fa`
- **Source build-report SHA-256**:
  `7cbedca367319687cb25a543e2692e5e78e3baecc0d27abf66002bbdbd99abb2`
- 8 ground-camera images (`camera-ground-001..008`) from P4's immutable
  photos directory were used as video source frames.
- Every source frame SHA-256 and size is recorded in the evidence JSON.

### 2.2 Tool chain

- **FFmpeg version**: `ffmpeg version 6.1.1` (backend for cv2 VideoCapture/Writer)
- **Video creation**: cv2.VideoWriter (mp4v codec, 2.0 fps, 1024×576)
- **Frame extraction**: `pipeline.ingest` (cv2.VideoCapture, JPEG quality 92)
- **COLMAP binary**: `third/colmap/bin/colmap.exe`
- **COLMAP version**: `COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26 without CUDA)`
- **GPU**: disabled (`--FeatureExtraction.use_gpu 0`, `--FeatureMatching.use_gpu 0`)
- **Blur filter**: disabled (`--blur-threshold 0`) — synthetic untextured
  surfaces have low Laplacian variance; the filter would skip all frames
  and prevent testing the extraction path itself.

### 2.3 Synthetic video

| Property | Value |
|---|---|
| Path | `.nantai-studio/p6-video-rehearsal/synthetic_route.mp4` |
| SHA-256 | `cf374de1ee0be27a68a6c88442171000e9be625f09d43592eb14f3b83c79737e` |
| Size | 87,584 bytes (85.5 KiB) |
| Container | mov,mp4,m4a,3gp,3g2,mj2 |
| Codec | mpeg4 |
| Resolution | 1024×576 |
| FPS | 2.0 |
| Duration | 4.0 seconds |
| Source frames | 8 PNG images (camera-ground-001..008) |

### 2.4 Exact extraction argv

```
python -m pipeline.ingest \
  --input <p6>/video_input \
  --output <p6>/extracted_frames \
  --fps 2.0 \
  --max-frames 20 \
  --blur-threshold 0
```

This is the exact path used by `scripts/reconstruct_local.py` lines 379-380
when the input is a video file.

## 3. Results

### 3.1 Frame extraction (pipeline.ingest)

| Metric | Value |
|---|---|
| Ingest exit code | 0 (success) |
| Elapsed | 0.3 seconds |
| Source video | 1 file |
| Extracted frames | 8 JPEGs (`frame_000000.jpg` .. `frame_000007.jpg`) |
| Output location | `extracted_frames/synthetic_route.mp4.frames/` |
| Manifest | `extracted_frames/ingest_manifest.json` |

Every extracted frame SHA-256 and size is recorded in the evidence JSON.
The extraction path correctly:
1. Detected the video file in the input directory
2. Used cv2.VideoCapture to decode frames
3. Applied the `fps=2.0` and `max_frames=20` parameters (all 8 source
   frames extracted since 8 < 20)
4. Wrote JPEG frames with quality 92
5. Produced an `ingest_manifest.json`

### 3.2 COLMAP with sequential_matcher

| Metric | Value |
|---|---|
| Matcher selected | `sequential_matcher` (ordered=True from video extraction) |
| Matcher selection logic | `reconstruct_local.py` line 404: `sequential_matcher if (ordered or n > 400) else exhaustive_matcher` |
| Images | 8 |
| Sequential pairs | 7 (1-2, 2-3, ..., 7-8) |
| feature_extractor | exit 0 (success) — 63-328 SIFT features/frame |
| sequential_matcher | exit 0 (success) — processed 8/8 images |
| mapper | **exit 1 (FAILED)** — "No images with matches" |
| Sparse models | 0 |
| colmap.db SHA-256 | `b50ff466be2f90304da798b1483ad96a1f2b7754ee5515857e4680fcd2ecb866` |
| Elapsed (total) | 0.6 seconds |

### 3.3 COLMAP failure root cause

The `sequential_matcher` successfully ran but produced **0 verified matches**
across all 7 sequential pairs. The mapper then reported "No images with
matches" and failed to create any sparse model.

This is the same failure mode as the P5 adversarial subset (4 cameras,
exhaustive matcher, 0 matches) and is consistent with the root cause
identified in P4: untextured v1 canary surfaces lack SIFT feature gradients
sufficient for inter-image matching.

### 3.4 Sequential matcher proof

The rehearsal proves the video extraction path correctly selects
`sequential_matcher` (not `exhaustive_matcher`) because:

1. `pipeline.ingest` extracts frames in ordered naming
   (`frame_000000.jpg`, `frame_000001.jpg`, ...)
2. `reconstruct_local.py` sets `ordered=True` when the input is a video
3. The matcher selection logic `sequential_matcher if (ordered or n > 400)`
   evaluates to `True` for video-derived frames
4. The `sequential_matcher` binary was invoked (not `exhaustive_matcher`)
5. COLMAP's sequential pairing logic generated 7 adjacent pairs (1-2...7-8)
   instead of the 28 pairs that exhaustive would generate for 8 images

## 4. Cross-topology failure envelope comparison

| Topology | Images | Matcher | Pairs | Failure stage | Verified matches |
|---|---|---|---|---|---|
| P4 mixed | 24 | exhaustive | 276 | mapper | 37 max/pair (insufficient) |
| P5 orbit | 8 | exhaustive | 28 | mapper | insufficient for initial pair |
| P5 adversarial | 4 | exhaustive | 6 | **matching** | 0 |
| P6 video | 8 | **sequential** | 7 | **mapper** | 0 (from sequential pairs) |

The P6 video path adds a fourth data point to the failure envelope:
sequential matching on 8 ordered synthetic frames produces 0 verified
matches, failing at the mapper stage with "No images with matches."

## 5. Production path behaviour (no defect found)

The rehearsal confirms the video-extraction caller boundary works correctly:

1. **`pipeline.ingest`** correctly detected the video file, used
   `cv2.VideoCapture` to decode frames, and wrote ordered JPEG frames.
2. **`reconstruct_local.py`'s matcher selection** correctly chose
   `sequential_matcher` based on `ordered=True` from video extraction.
3. **COLMAP `feature_extractor`** successfully extracted SIFT features
   (63-328 per frame) from the JPEG frames.
4. **COLMAP `sequential_matcher`** successfully ran on all 7 sequential
   pairs without error.
5. **COLMAP `mapper`** correctly failed with "No images with matches"
   when no verified matches existed.
6. **No partial result was written**: no `sparse/0` model was produced.
7. **No trust was promoted**: the failure was fail-closed.

No reproducible caller or fail-closed defect was exposed. The production
video-extraction path is correct.

## 6. Honest limits (not promoted)

- **Not real-scene evidence**: this is a synthetic-capture rehearsal using
  a 4-second, 85 KiB video created from 8 Blender renders. It proves the
  video extraction caller contract works, not that a real video can be
  reconstructed.
- **A short synthetic video validates the caller contract only**: do not
  extrapolate this into a claim that a 1 GB / 20-minute real video has
  been reconstructed. Real video will have real texture, motion blur,
  exposure variation and orders-of-magnitude more frames.
- **Non-textured v1 build**: the source frames are from a v1 (non-textured)
  build. Textured v2 builds may produce more SIFT features and different
  matching outcomes.
- **Blur filter disabled**: `--blur-threshold 0` was used because synthetic
  surfaces have low Laplacian variance. Real video should use the default
  blur filter to skip genuinely blurry frames.
- **Sequential matcher produces fewer pairs**: 7 sequential pairs vs 28
  exhaustive pairs for 8 images. For real video with dense overlap and
  strong texture, sequential matching is much faster and sufficient. For
  sparse synthetic frames, it produces 0 matches — expected.
- The five real-scene evidence items in §1 of the handoff remain absent:
  1. real overlapping capture with known acquisition provenance;
  2. accepted COLMAP/SfM poses and sparse geometry;
  3. one non-mock cloud-GPU 3DGS training result;
  4. imported splat artifact with measured alignment;
  5. Viewer QA over that real artifact.

## 7. Evidence artifacts

- **Evidence JSON**:
  `.nantai-studio/p6-video-rehearsal/p6_video_evidence.json`
  (SHA-256 `48bb73fc966784203f787d11e8a1c194138272a41729b21186aaf0c54003b1d8`)
- **Synthetic video**:
  `.nantai-studio/p6-video-rehearsal/synthetic_route.mp4`
  (SHA-256 `cf374de1ee0be27a68a6c88442171000e9be625f09d43592eb14f3b83c79737e`)
- **COLMAP database**:
  `.nantai-studio/p6-video-rehearsal/colmap_ws/colmap.db`
  (SHA-256 `b50ff466be2f90304da798b1483ad96a1f2b7754ee5515857e4680fcd2ecb866`)
- **Extracted frames**: 8 JPEGs in
  `extracted_frames/synthetic_route.mp4.frames/`
- All artifacts are in private rehearsal workspace; nothing was committed to
  the repository tree or registry.

## 8. Next queue item

Per handoff §9: "After P6, reread
`handoff/AUDIT-2026-07-22-real-3d-scene-gap-assessment.md`. If real input is
still absent, choose and start the highest-value unowned prerequisite."

P4–P6 have now covered:
- P4: real COLMAP executable on 24 mixed-camera synthetic captures
- P5: topology-overfit rehearsal on orbit (8) and adversarial (4) subsets
- P6: real video-extraction boundary rehearsal with sequential_matcher

All three rehearsals confirmed the production caller boundary is correct
and fail-closed. No reproducible defect was found across four topologies
and two matchers (exhaustive + sequential).

The five real-scene evidence items remain absent. The next highest-value
unowned prerequisite is to audit the cloud-training request/result boundary
against a real installed CLI (nerfstudio/gsplat), or propose measured Viewer
performance evidence to Codex without editing Viewer-owned paths — but both
require either credentials/budget or Codex coordination.

Remaining real-scene blockers (unchanged):
1. real overlapping capture with known acquisition provenance;
2. accepted COLMAP/SfM poses and sparse geometry;
3. one non-mock cloud-GPU 3DGS training result;
4. imported splat artifact with measured alignment;
5. Viewer QA over that real artifact.
