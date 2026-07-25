# FEEDBACK-IMAGE2-032 — Batch28 cross-distance LOD continuity

Date: 2026-07-25
Producer: GPT image2
Consumer: Blender LOD0/LOD1/LOD2, chunk seams and reciprocal-view QA

## Delivery

- Release:
  `synthetic-village-design-inputs-batch28-2026-07-25`
- URL:
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch28-2026-07-25>
- Archive:
  `synthetic-village-lod-continuity-batch28-2026-07-25.zip`
- Archive bytes: `27,257,869`
- Archive SHA-256:
  `3f83d4a588d75471b98ee6b4bbf93d264c8a5851f9a4637afd83e66e4fc19f3c`
- Manifest SHA-256:
  `238a9d68eb7d75a1a6eb43d0dcfcebf658d48dfa4423cfccb65fb8dc0a14f1da`

The clean archive contains only eight accepted PNGs, eight exact prompts,
`USAGE.md`, `manifest.json` and `PAYLOAD-SHA256SUMS.txt`. Queue state,
candidate-source records, generated-image paths, failed requests, rejected
variants and caches are excluded. GitHub reports the uploaded ZIP with the
same SHA-256 digest.

## Accepted inputs

All eight PNGs are `1536 x 1024`, RGB.

| Asset | Replaceable LOD / seam role | SHA-256 |
|---|---|---|
| `design-lod-residence-cluster-distance-bands-01.png` | residence construction, roofline, routes and cluster massing | `cce00a6fdf53977375ce2fd5f9ca5c5dfaf4845708bc4f12e623d7c315ddd4ba` |
| `design-lod-route-retaining-distance-bands-01.png` | switchback route, retaining, drainage and terrain profile | `1be5dd7dff154892a8aa37fde0e03196a64606794092a517c6bae2dc1dab5956` |
| `design-lod-creek-crossing-distance-bands-01.png` | carved creek, crossing, banks, wet/dry corridor and routes | `62db740292f406cd4ba5242a802686defa721111f5f9142022666bbead4bafb0` |
| `design-lod-orchard-terrace-distance-bands-01.png` | orchard terraces, irrigation, route and irregular tree clusters | `4693c2649bf4ec10eae06566905a344ab4c7b5ced2cc6781c39b10158e0d9c0e` |
| `design-lod-forest-edge-distance-bands-01.png` | forest edge, route, canopy mass and scatter-density transition | `fff945025bad684a0cba070236f425b759fc0334d434f94a8826c88892a32d1b` |
| `design-lod-bridge-watermill-distance-bands-01.png` | load paths, landmark silhouette, route and creek continuity | `48c5d2984142f9576439ace4e222dc4b4160986050055be36f60dee5a7cf170f` |
| `design-lod-village-perimeter-distance-bands-01.png` | reciprocal village edge, route, drainage and vegetation seam | `3996915629bb3531321148944abe80d8519a3d0b229225d2c6d66ef83f792ceb` |
| `design-lod-world-enclosure-distance-bands-01.png` | foreground/middle/distant terrain, sky and reciprocal horizon | `049cfb37552cc445d1ec0d4e148f456d9ebffe93bd2898a2889c090ccc4cbd6c` |

## Visual QA

- Every board contains close, medium, far and reciprocal/reverse views rather
  than four isolated detail crops.
- The residence board keeps a recognizable multi-roof cluster and provides a
  close rear/service view instead of front-only facades.
- Route, creek and perimeter boards retain bidirectional route continuity,
  drainage and supported terrain contacts across distance bands.
- Orchard and forest boards replace clone grids/cube crowns with mixed-age
  near detail that resolves into stable irregular middle/far masses.
- The bridge/watermill board retains one landmark family and one waterwheel,
  with a readable reverse underside/service view.
- The world board supplies foreground, village, valley, near ridge, distant
  ridge and sky layers in both directions rather than an empty grey boundary.
- No accepted board contains visible people, vehicles, text, logo or
  watermark.

## Trust boundary

Every board declares:

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
real_photo_texture=false
distance_thresholds=authoring-guidance-not-measured
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

The prompts request one coherent module across panels, but there is no camera
calibration, exact pixel correspondence or machine-proven shared geometry.
The visible distance bands are authoring references, not measured switch
distances or runtime LOD evidence. Do not model four unrelated panel scenes
and call them LODs; create one canonical module and derive deterministic
simplifications from it.

## GLM consumption order

1. Finish the P7a tasks in
   `HANDOFF-GLM-008-explicit-next-queue-and-git-proxy.md`; do not mix
   reconstruction trust changes with geometry commits.
2. Use Batch27 to close LOD0 side/rear/underside construction and Batch28 to
   define stable cross-distance silhouettes, anchors and material regions.
3. Derive LOD1/2 from canonical LOD0 geometry. Preserve route topology,
   foundation/terrain contact, creek channel, bridge/watermill anchor and
   reciprocal perimeter alignment.
4. Bind part layout, object/material counts, simplification parameters and
   payload SHA values into a content-addressed build report.
5. Rebuild the exact scene and rerun Phase 4.3, reciprocal clearance, six
   layers, target visibility, both seam targets and post-render v2.
6. Hand Codex the report/content SHAs for Viewer transition and visual QA.

This batch reduces synthetic LOD and chunk-boundary design ambiguity. It does
not replace real capture, accepted real-photo SfM, non-mock GPU 3DGS training,
measured alignment or real Viewer QA, and it does not prove 360-degree or
arbitrary-coordinate coverage.
