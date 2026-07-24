# HANDOFF-CODEX-013 — Viewer QA proposal over P7 recovered-pose splat

Date: 2026-07-24
From: GLM lane
To: Codex lane (owns Viewer + studio_server + web/data)
Status: proposal + ready-to-consume immutable private artifact
Trigger: HANDOFF-GLM-007 §11 — "after P7, start the highest-value unowned
prerequisite; prefer a concrete Viewer evidence proposal to Codex."

## 1. Why this is the next step

P7 (closed in `FEEDBACK-HANDOFF-GLM-007-p7-recovered-pose-training-closure.md`)
proved the non-GT boundary is wired end-to-end:

```
synthetic Blender capture
  -> real COLMAP 4.1.0 SfM (60/60 registered, 44,426 sparse points, CPU)
  -> real Brush 0.3.0 training (Intel iGPU, 1000 steps, 512px)
  -> normalize + prepare_import
  -> private import (4 LOD PLYs)
```

The trained splat is the closest artifact we have to a real reconstruction
output, and it is **not** in `web/data/` or any Codex-owned path. GLM has now
chunked it into the same `chunks.json` contract the Viewer already consumes for
the synthetic village, with a verified per-payload SHA integrity block. This
unblocks Codex to run the first Viewer streaming/roaming QA over a non-GT,
non-synthetic-village splat — without touching GLM-owned code paths.

This does **not** close any of the five real-scene evidence items (still
synthetic capture / iGPU training / arbitrary / unaligned). It only proves the
Viewer can stream a recovered-pose splat through the existing chunk contract.

## 2. Immutable private artifact root

```text
tmp/p7-recovered-pose-training/chunks_viewer/
  chunks.json                 (102,844 B)
  chunk_*.ply + chunk_*_lod0/lod1.ply   (85 chunks x 3 LOD = 255 payloads)
```

| Field | Value |
|---|---|
| chunks.json SHA-256 | `89b5536d2faa1d6ed998967f093efb9b09fa5b83bac61424fad3b434b2f765b1` |
| total_chunks | 85 |
| chunk_size_m | 100.0 |
| total_points | 33,864 |
| verified_payloads | 255 / 255 (per_chunk_sha_verified=true, 0 mismatches) |
| has `grid` field | **false** (this is a reconstruction, NOT on-demand infinite world) |
| LOD fractions | {0: 0.08, 1: 0.30, 2: full} |

`source` block (provenance carried from the recon manifest, unchanged by
chunking):

```json
{
  "frame_id": "sfm-local",
  "units": "arbitrary",
  "geometry_usability": "preview-only",
  "recon_manifest_sha256": "9a6be10f717bab4ab5055e71185d2388a09f24de5b430e31941ad4e62e601987",
  "applied_transform_ids": []
}
```

The `recon_manifest_sha256` matches the actual bytes of
`tmp/p7-recovered-pose-training/recon_web/recon_manifest.json` (verified).

## 3. Source reconstruction artifacts (for traceability)

| Artifact | SHA-256 | Size |
|---|---|---|
| recon_manifest.json | `9a6be10f717bab4ab5055e71185d2388a09f24de5b430e31941ad4e62e601987` | — |
| trained.ply (Brush output) | `9abbfb47c7d3c744308945dedc4c69036995fada23b81431be3493e2f512f8f4` | 7,993,380 B |
| recon_full.ply (full 3DGS, sh_degree=3) | `50698971c7dfcc592cc67020130634e8409338e13d8fc7538535e13dd2b35bea` | 8,400,179 B |
| recon_lod0.ply | `0a466d0cbe702ed73ccca3f17e9059a68f7629b06a595341bd3aeeb0d6c69119` | 52,038 B |
| recon_lod1.ply | `85921d29464368823fffc16fa0cb0a28bf738beabaf8d3597bb85cf84d396bf6` | 193,589 B |
| recon_lod2.ply | `8893648dcf667345ebf63077158ecfc2e1e88a2256f3f2d8766163a6fba5383a` | 643,984 B |

All four `recon_web` PLYs were independently re-verified by
`scripts/verify_recon_artifacts.py` (exit 0): every declared SHA-256 and byte
count matches disk; trust preserved as `preview-only` (no promotion).

## 4. Spatial extent + suggested starting camera

The sparse SfM points include outliers, so the full bounds are spread out:

| Bounds | min | max |
|---|---|---|
| full | (-1247.3, -859.7, -135.4) | (704.1, 509.8, 1385.9) |
| core (99.5th percentile, 97.6% of points) | (-891.3, -229.3, -0.04) | (498.9, 100.8, 669.96) |

Suggested Viewer starting camera: near the **core_bounds centroid**
`(-196.2, -64.3, 335.0)` (arbitrary sfm-local units, not metres). Most chunks
and most gaussians lie in this core region; the outlier chunks are single-point
strays from the sparse SfM and will look like isolated specks.

## 5. What GLM has already verified (so Codex does not repeat it)

1. `verify_recon_artifacts.py` on `recon_web/recon_manifest.json` — exit 0, all
   4 PLY SHAs + sizes match, no path-safety violations, no contradictions.
2. `chunk_reconstruction.py` produced `chunks_viewer/chunks.json` from
   `recon_full.ply` with `--recon-manifest` bound; source provenance unchanged.
3. `verify_chunks_integrity(chunks_viewer)` — `valid=true`,
   `per_chunk_sha_verified=true`, 255/255 payloads verified, 0 mismatches.

## 6. What Codex owns and is asked to do

Codex owns `web/viewer/*`, `pipeline/studio_server.py`, `web/data/*`. GLM has
not touched any of them. The ask is narrowly:

1. Point the existing Viewer's splat-chunks layer at
   `tmp/p7-recovered-pose-training/chunks_viewer/chunks.json` (the contract is
   the same one the Viewer already consumes for the synthetic village — no new
   schema).
2. Verify streaming + roaming: chunk load/unload by camera distance, LOD 0/1/2
   transitions, ETag/304 negotiation if served over HTTP.
3. Confirm the Viewer does **not** project `on_demand:true` onto this artifact
   (it has no `grid` field; it is a finite reconstruction, not a procedurally
   continuable world — see AGENTS.md render-on-demand rules).
4. Confirm the provenance shown in any HUD/overlay remains
   `preview-only / sfm-local / arbitrary / unaligned` and is **not** promoted to
   metric/measured by virtue of being viewable.

## 7. QA acceptance criteria (proposal)

| Criterion | Required | Notes |
|---|---|---|
| Viewer loads chunks.json without error | yes | 85 chunks, schema = existing P3 contract |
| Splat renders (quality may be low — iGPU, 1000 steps) | yes | visual fidelity is explicitly not a gate |
| At least one LOD transition observed | yes | LOD0 (8%) -> LOD1 (30%) -> LOD2 (full) |
| No `on_demand:true` projection | yes | no `grid` field present |
| Provenance not promoted | yes | stays preview-only / sfm-local / arbitrary / unaligned |
| Camera can roam within core_bounds | yes | centroid (-196, -64, 335) |

## 8. What this proves and does NOT prove

Proves:
- The existing Viewer chunk contract can stream a non-GT, recovered-pose splat
  produced by the real COLMAP -> Brush -> import boundary.
- The P3 per-payload SHA integrity block survives a real (synthetic-origin)
  training + chunk round-trip.

Does NOT prove:
- real-photo capture (synthetic Blender)
- production-quality 3DGS (Intel iGPU Brush, 1000 steps — limited trial tier)
- metric alignment (arbitrary units, unaligned)
- any of the five real-scene evidence items

## 9. GLM next step after this handoff

Per HANDOFF-GLM-007 §11: this proposal is the actionable unowned prerequisite
that does not require cloud-GPU credentials or user-supplied real footage. The
remaining real-scene blockers (real capture, accepted SfM on real photos,
non-mock cloud-GPU training, measured alignment, Viewer QA over a *real*
artifact) are all externally gated. GLM will continue hunting unowned
caller/integrity defects; a pending Codex review of this proposal is not a stop
condition.
