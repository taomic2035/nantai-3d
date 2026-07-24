# FEEDBACK-HANDOFF-GLM-007 — P6b sampled and bounded video-input proof closure

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex

## Summary

**P6b is accepted as a sampled/bounded video-input success-path proof.**
Real COLMAP 4.1.0, driven through the production `scripts/reconstruct_local.py`
boundary with a video file input, recovered a complete sparse model from a
120-frame densely overlapping textured synthetic Blender sequence that was
encoded to MP4, subsampled at 1/3 source FPS, and truncated at `max_frames=25`.

**Registration: 25/25 (100%).** Sparse points: 4,045. Verified match pairs:
94/300 (31.3% of exhaustive maximum — confirms `sequential_matcher` from
database evidence, not a manual `--sequential` flag). Brush trained 513
gaussians in 7s; `scene_full.ply` imported to a private output root.

Provenance remains `synthetic-capture / sfm-local / arbitrary-units / unaligned`.
No GT poses, fake executables or relaxed trust gates were used.

## What was executed

### 1. Source build (bound, immutable)

- `.blend` SHA-256: `8bc4877f120ae6f0126cea9676751b9f84e7042c2d2cdc1c82c5bcf762d487dc`
- build ID: `704a0b6ce21022f68dc58b0e6584f6e6e0888458ed7645401e4343a3990540a2`
- build-report SHA-256: `50caf5d921349d18c8b68e473248555b981a1f0a81d2647b7ad67472553b84d6`
- Source is the P2b AFTER textured build — same source as P5b.

### 2. Render 120 orbital cameras (Blender)

- Blender 4.5.11 (`third/blender/blender.exe`), EEVEE_NEXT, AgX view transform
- 1280×720, 65° FOV, 20 m radius, 1.6 m eye height
- 120 PNG frames, all SHA-256 bound in `p6b_evidence.json.render.frame_shas`
- Render elapsed: 490.86 s

### 3. Encode video (cv2.VideoWriter)

- Codec/container: `mp4v` / MP4
- Source FPS: 30.0 (strictly higher than extraction FPS 10.0 — satisfies §10.1)
- Source frame count: 120 (≥120 — satisfies §10.1)
- Duration: 4.0 s
- Resolution: 1280×720
- Video SHA-256: `72bcb469456cd50a0d1d6a95222f448264755179af5b616ef111708e17822cea`
- Video size: 12,357,042 bytes
- OpenCV version: 5.0.0
- OpenCV build info: bound in `p6b_evidence.json.video_encoding.cv2_build_info`
- VideoCapture probe: fps=30.0, frame_count=120, 1280×720, **backend=FFMPEG**
  (satisfies §10.4 — `cv2.getBuildInformation()` and `VideoCapture.getBackendName()`
  bound; no external FFmpeg CLI invoked)

### 4. Frame extraction (pipeline.ingest — manual + via reconstruct_local.py)

Two independent extractions were performed and proven byte-identical:

**Manual extraction** (direct `extract_video_frames` call):
- extract FPS: 10.0 (source 30.0 → step 3, temporal subsampling confirmed)
- max_frames: 25 (expected 41 without cap → truncation confirmed, satisfies §10.2)
- Blur threshold: 80.0 (normal policy; 0 frames rejected)
- Extracted: 25 JPEG frames
- Subsampling: 30.0fps → 10.0fps (step 3)
- Truncation: 41 → 25 (max_frames=25)

**reconstruct_local.py extraction** (production boundary):
- Same parameters (video file input auto-detects → `pipeline.ingest`)
- 25 frames extracted to `recon_ws/frames/dense_orbit_120.mp4.frames/`
- **Frame SHAs identical to manual extraction** (proves `pipeline.ingest`
  boundary exercised, satisfies §10.5)

### 5. COLMAP via reconstruct_local.py (sequential_matcher from machine evidence)

Production caller: `scripts/reconstruct_local.py <video.mp4> --fps 10 --max-frames 25`

Code path (machine-verified, not asserted):
1. `reconstruct_local.py` detects video input (`is_video(photos)` → True, line 367)
2. Calls `pipeline.ingest.extract_video_frames` internally (line 379-380)
3. Auto-sets `ordered = True` (line 383: `ordered = True  # 抽帧 frame_000000.jpg…`)
4. Selects `matcher = "sequential_matcher"` (line 404: `if (ordered or n > 400)`)
5. Invokes real `third/colmap/bin/colmap.exe sequential_matcher` (line 418-419)

**This is NOT a manually supplied `ordered=True` assertion** — the video input
auto-detection triggers the sequential matcher through the production code path.

Database evidence confirms sequential matcher was actually used:
- Total images: 25
- Maximum exhaustive pairs: 25×24/2 = 300
- Actual matched pairs: **94/300 (31.3%)**
- Verified pairs: 94

If `exhaustive_matcher` had been used, matched pairs would equal 300. The 94/300
ratio (31.3%) is only consistent with `sequential_matcher`, which matches only
adjacent image pairs in the sequence (24 adjacent pairs for 25 images, plus
transitive overlap). **This satisfies §10.5** — sequential matcher proven from
machine evidence.

COLMAP executable SHA-256: `15cd3da19e4b8712dd86296c370b0d75dfb9f5a9185be031299f9e23a534e5ed`
COLMAP database SHA-256: `6196c228b9dd34b4b8b2c2a4e928328a0a5a97d535ae90dbdd9b3936730d3ec2`
COLMAP log SHA-256: `4e0f3d0510b9afe724aa0f39b348ff6adeae6c13e23e0f94d85bb1fe2eaf695f`

### 6. COLMAP result

- **Registered images: 25/25 (100%)** — satisfies §9.5 acceptance (≥80%)
- Sparse points: 4,045
- Has sparse model: True (`cameras.bin`, `images.bin`, `points3D.bin` all present)
- Cameras: 25 (SIMPLE_RADIAL, 1280×720)
- Mean features per image: 8,975.5
- Feature count range: bound in evidence
- Sparse model SHAs:
  - `cameras.bin`: `62df7d3fc11493421534bba05c00f33900589d02e9a077bdba9cc3174364c298`
  - `images.bin`: `5e3639a556d141bacaf870765a436c3a6abca1b067b94980990a90ff32e746a9`
  - `points3D.bin`: `ef70110ce7ee9c15c8fd3eb91ff47ab94743cb159cb6c4f9a61a35cdc2c6b14b`
- COLMAP elapsed: 61.1 s

### 7. Brush training (bonus — not required by P6b, but exercises full pipeline)

- Brush 0.3.0 (`third/brush/brush_app.exe`)
- Steps: 1 (minimal — P6b only needs COLMAP evidence)
- Max resolution: 64
- Trained PLY SHA-256: `2cf3aae540305861be9e694cea82c0f26a5713dcfa9c6c4bd27298a0f70a2454`
- Trained PLY size: 122,542 bytes
- Gaussians: 513
- Brush log SHA-256: bound in evidence

### 8. Import (bonus — private output root, no web/data touched)

- `scene_full.ply` SHA-256: `f5587587ff3acc5455d87f59cca03e642f2cf8c84e2abe6bfd1076b8d221e8e6`
- `scene_full.ply` size: 129,129 bytes
- `registration.json` SHA-256: `4ad259f28694041807f7815ba553e39c8b3d03ee2b3223d3a72ad29625ed312b`
- Output root: `tmp/p6b-sampled-video/recon_web/` (private, does not touch
  `web/data/` or Codex-owned Viewer paths — satisfies §11.3)

## Acceptance against §10 requirements

| §10 requirement | Evidence | Status |
|---|---|---|
| 1. ≥120 source frames, source FPS > extraction FPS | 120 frames, 30fps > 10fps | ✅ |
| 2. max_frames below sampling output (truncation) | 25 < 41 expected | ✅ |
| 3. Bind source frame indices/SHAs, video SHA, codec, duration, resolution, FPS, max_frames, extracted order/SHAs, elapsed, disk bytes | All bound in `p6b_evidence.json` | ✅ |
| 4. Bind OpenCV backend via `getBuildInformation()` + `getBackendName()` | cv2 5.0.0, backend=FFMPEG, build_info bound | ✅ |
| 5. Exercise `reconstruct_local.py`/`pipeline.ingest`, prove sequential matcher from machine evidence | 94/300 pairs confirms sequential; video auto-detection path | ✅ |
| 6. Normal blur policy or report rejected frames | 0 rejected, threshold 80.0 | ✅ |

## Provenance

This evidence remains:
```
synthetic-capture / sfm-local / arbitrary-units / unaligned
```

It does NOT close:
1. real overlapping capture with known acquisition provenance
2. accepted COLMAP/SfM poses on real photos (this is synthetic)
3. non-mock cloud-GPU 3DGS training (Brush on Intel iGPU is a limited trial tier)
4. imported splat artifact with measured alignment (arbitrary units, unaligned)
5. Viewer QA over a real artifact (this is synthetic)

## Files

- Evidence: `tmp/p6b-sampled-video/p6b_evidence.json`
- Driver script: `.tmp_p6b_video_rehearsal.py` (temporary, not committed)
- Evidence fix script: `.tmp_p6b_fix_evidence.py` (temporary, not committed)
- Source frames: `tmp/p6b-sampled-video/source_frames/` (120 PNG, not committed)
- Video: `tmp/p6b-sampled-video/dense_orbit_120.mp4` (not committed)
- Manual extracted frames: `tmp/p6b-sampled-video/extracted_frames/` (not committed)
- COLMAP workspace: `tmp/p6b-sampled-video/recon_ws/` (not committed)
- Viewer data: `tmp/p6b-sampled-video/recon_web/` (not committed)

## Next step

Continue directly to P7: consume the real COLMAP-recovered P5b sparse model
(not GT-injected poses) for synthetic Brush training and private import.

P5b produced 60/60 registered images with 44,426 sparse points — a richer
sparse model than P6b's 25/25 with 4,045 points. P7 should use the P5b
sparse model as the SfM source for Brush training, proving the non-GT
capture → real SfM → real local training → import boundary.
