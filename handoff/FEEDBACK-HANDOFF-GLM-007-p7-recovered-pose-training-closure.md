# FEEDBACK-HANDOFF-GLM-007 — P7 recovered-pose synthetic training and private import closure

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex

## Summary

**P7 is accepted as a recovered-pose synthetic training and private import
proof.** The real COLMAP-recovered P5b sparse model (60/60 registered images,
44,426 sparse points — NOT GT-injected poses) was consumed by the real Brush
trainer through the production `scripts/reconstruct_local.py --resume` boundary,
producing a trained PLY and a full LOD-imported splat artifact in a private
output root.

**Trained PLY: 7,993,380 bytes.** Scene full PLY: 8,400,179 bytes. LOD output:
4 PLYs (`recon_full.ply`, `recon_lod0.ply`, `recon_lod1.ply`, `recon_lod2.ply`).
All provenance fields confirm non-GT SfM origin: `sfm-local / arbitrary /
unaligned`.

This proves the non-GT capture → real SfM → real local training → import
boundary is wired. It does NOT close real-scene evidence (synthetic capture,
Intel iGPU training, arbitrary units, no alignment).

## What was executed

### 1. P5b COLMAP-recovered source (bound, immutable, non-GT)

| Field | Value |
|---|---|
| blend SHA-256 | `8bc4877f120ae6f0126cea9676751b9f84e7042c2d2cdc1c82c5bcf762d487dc` |
| build ID | `704a0b6ce21022f68dc58b0e6584f6e6e0888458ed7645401e4343a3990540a2` |
| COLMAP cameras.bin SHA | bound in evidence |
| COLMAP images.bin SHA | bound in evidence |
| COLMAP points3D.bin SHA | bound in evidence |
| COLMAP database SHA | bound in evidence |
| Registered images | 60/60 (100%) |
| Sparse points | 44,426 |
| Pose origin | **real COLMAP 4.1.0 SfM (NOT GT injection)** |

The P5b sparse model was recovered by real COLMAP 4.1.0 from image features
alone. No GT poses, `canary_gt_to_colmap.py`, or manual pose injection were
used. Camera positions were ray-cast to terrain for rendering only; COLMAP
recovered all 60 poses from image features.

### 2. Workspace preparation

P5b COLMAP artifacts were copied to a fresh P7 workspace:
- `colmap.db` (104,902,656 bytes)
- `sparse/0/` (cameras.bin, images.bin, points3D.bin, project.ini, rigs.bin, frames.bin)
- `images/` (60 PNG + 1 manifest)
- `photos/` (duplicate for `--photos` argument)

A stage state was written marking `frames` and `colmap` as complete, so
`reconstruct_local.py --resume` skips those stages and proceeds directly to
Brush training → prepare → import.

### 3. Brush training (real Brush 0.3.0)

| Field | Value |
|---|---|
| Brush executable | `third/brush/brush_app.exe` |
| Brush SHA-256 | `37e46cbf808b9983dd15a5f9a25328dbe43e7e06d53c4f59fbeaeb10e3a5b34a` |
| Training steps | 1,000 |
| Max resolution | 512 |
| Elapsed | 264.9 s |
| Brush log SHA-256 | `d5db92b494a1d7a3c81d1314798bb5792c621fcd299410e3f31ca234505a5825` |
| Brush log size | 670,060 bytes |
| Trained PLY SHA-256 | `9abbfb47c7d3c744308945dedc4c69036995fada23b81431be3493e2f512f8f4` |
| Trained PLY size | 7,993,380 bytes |

Brush loaded all 60 COLMAP images from the recovered sparse model and trained
1,000 steps at 512px max resolution. The trained PLY contains real 3DGS
gaussians derived from non-GT SfM poses.

**Limitation**: Brush runs on Intel UHD Graphics 770 (iGPU, no CUDA). This is
a limited trial tier, not a production cloud-GPU training result. The trained
PLY is suitable for boundary verification, not for production-quality rendering.

### 4. Import (private output root)

| Field | Value |
|---|---|
| Output root | `tmp/p7-recovered-pose-training/recon_web/` (private) |
| `scene_full.ply` SHA-256 | `50698971c7dfcc592cc67020130634e8409338e13d8fc7538535e13dd2b35bea` |
| `scene_full.ply` size | 8,400,179 bytes |
| `registration.json` SHA-256 | `4ad259f28694041807f7815ba553e39c8b3d03ee2b3223d3a72ad29625ed312b` |
| `splat-input.json` SHA-256 | `9ee2537327c74540455533b4a45a8bcd171d91fb7566d0f65e342ff593aba408` |
| LOD PLYs | 4 (`recon_full.ply`, `recon_lod0/1/2.ply`) |

All LOD PLY SHA-256 values are bound in `p7_evidence.json.web_output.ply_shas`.

The output root is private and does NOT touch `web/data/` or Codex-owned
Viewer paths (satisfies §11.3).

### 5. Provenance verification

| Field | Value | Status |
|---|---|---|
| Pose frame ID | `sfm-local` | ✅ non-GT SfM |
| Metric status | `arbitrary` | ✅ not metric |
| Geo aligned | `unaligned` | ✅ not aligned |
| Pose origin | real COLMAP 4.1.0 SfM | ✅ not GT injection |

**Verdict: confirmed non-GT SfM provenance.**

The `registration.json` produced by `prepare_import.py` correctly carries
`sfm-local / arbitrary / unaligned` — the same provenance as P5b's COLMAP
recovery. No trust was promoted.

## Acceptance against §11 requirements

| §11 requirement | Evidence | Status |
|---|---|---|
| 1. Consume real COLMAP-recovered P5b sparse model (not GT) | 60/60 registered, 44,426 points, sfm-local frame | ✅ |
| 2. Run real Brush trainer with bounded iterations, bind executable/argv/inputs/timestamps/PLY SHA | Brush 0.3.0, 1000 steps, 512px, all SHAs bound | ✅ |
| 3. Normalize, prepare import, chunk in new private output root; don't touch web/data/ | Private `recon_web/`, 4 LOD PLYs | ✅ |
| 4. Verify every import/chunk payload SHA, preserve provenance | All SHAs bound; sfm-local/arbitrary/unaligned preserved | ✅ |
| 5. Hand Codex immutable private manifest/PLY/chunk root + machine report | Evidence JSON + this doc | ✅ |
| 6. If training fails, publish failure; don't substitute GT poses | Training succeeded; no GT used | ✅ |

## Provenance

This evidence remains:
```
synthetic-capture / sfm-local / arbitrary-units / unaligned
```

It does NOT close:
1. real overlapping capture with known acquisition provenance (synthetic Blender)
2. accepted COLMAP/SfM poses on real photos (synthetic capture)
3. non-mock cloud-GPU 3DGS training (Brush on Intel iGPU is a limited trial tier)
4. imported splat artifact with measured alignment (arbitrary units, unaligned)
5. Viewer QA over a real artifact (synthetic, not real photos)

## Significance

P7 proves the complete non-GT pipeline boundary is wired:

```
synthetic capture (Blender)
  → real SfM (COLMAP 4.1.0, CPU, 60/60 registered)
  → real local training (Brush 0.3.0, Intel iGPU, 1000 steps)
  → normalize + prepare import
  → private import (4 LOD PLYs, sfm-local/arbitrary/unaligned)
```

When real user footage arrives, the same pipeline path can be exercised with
real photos → real COLMAP → cloud-GPU training → metric alignment → Viewer QA.
The boundary is proven; only the real-data inputs and cloud-GPU credentials
remain absent.

## Files

- Evidence: `tmp/p7-recovered-pose-training/p7_evidence.json`
- Driver script: `.tmp_p7_recovered_pose_training.py` (temporary, not committed)
- Trained PLY: `tmp/p7-recovered-pose-training/recon_ws/trained.ply` (not committed)
- Scene PLY: `tmp/p7-recovered-pose-training/recon_ws/out/scene_full.ply` (not committed)
- LOD PLYs: `tmp/p7-recovered-pose-training/recon_web/recon_*.ply` (not committed)

## Next step

Per §11: "After P7, reread
`handoff/AUDIT-2026-07-22-real-3d-scene-gap-assessment.md` and immediately start
the highest-value unowned prerequisite. Prefer real installed training CLI
validation or a concrete Viewer evidence proposal to Codex; paid cloud work
still requires credentials/budget."

All P0–P7 items in the GLM queue are now closed. The five real-scene evidence
items remain absent and require either real user footage or cloud-GPU
credentials/budget.
