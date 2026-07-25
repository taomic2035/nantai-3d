# FEEDBACK-IMAGE2-034 — Batch29 material macrovariation

Date: 2026-07-25
Producer: GPT image2
Consumer: Blender material/UV authoring and close-range roaming QA

## Delivery

- Release:
  `synthetic-village-design-inputs-batch29-2026-07-25`
- URL:
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch29-2026-07-25>
- Archive:
  `synthetic-village-material-macrovariation-batch29-2026-07-25.zip`
- Archive bytes: `26,990,276`
- Archive SHA-256:
  `c3ff4cd08c7f2a2bf115f71e79d86afae2775d7f6c4a73efa00166d93f83469a`
- Manifest SHA-256:
  `f1800bf633d089af3f455986105c3ae4c76daa31dae405d982c1615b9e91529d`
- Checksum sidecar SHA-256:
  `b39ca4a3ba41ffeed86ee5a15f8ef74fb06ef66f9054188d7c4a6f95e91948db`

The clean archive contains only eight accepted PNGs, eight exact prompts,
`USAGE.md`, `manifest.json` and `PAYLOAD-SHA256SUMS.txt`. It has 19 sorted
entries and was rebuilt twice with fixed timestamps; both archives produced
the same SHA-256. Queue state, candidate-source records, generated-image
paths, rejected variants and caches are excluded. GitHub reports both uploaded
asset digests equal to the local files.

## Accepted inputs

| Asset | Dimensions | Replaceable authoring role | SHA-256 |
|---|---:|---|---|
| `design-material-macrovariation-creek-bed-01.png` | 1254×1254 | submerged bed, wet bank, dry bench and carved creek corridor | `a80c5d30060da517caa0eb6ced3c2640ef8e71423db71353e717f26afbfe909d` |
| `design-material-macrovariation-dark-timber-01.png` | 1254×1254 | grain/end grain, joints, post base, gallery frame and far timber mass | `fad3e41fde6b456908c7ed05780af795b9a63ef924d356cbadfff727ffc72f51` |
| `design-material-macrovariation-fieldstone-01.png` | 1536×1024 | stone scale, mortar, corner, drainage and long retaining walls | `27e301d94690285ad0c8efafb897cd32cf2a25c65d6c2ffb770360d50b42444d` |
| `design-material-macrovariation-forest-orchard-ground-01.png` | 1254×1254 | litter, roots, path contact, orchard floor and canopy density | `9584aac5b1029644997a9dc6d86086fc781b5837fc3f6a2ce1cd212ea3dfd881` |
| `design-material-macrovariation-grey-roof-tile-01.png` | 1254×1254 | tile faces, ridge/eave drainage, repairs and far roof masses | `84c783a034c33749c8f50b7d98cee0d5c2e90bde9eca2ff9003c9b2adcdae62f` |
| `design-material-macrovariation-lime-plaster-01.png` | 1254×1254 | aggregate, repairs, timber/plinth contacts, damp base and facade mass | `1d2d22ad9768d9fd6ef840df7b72f9d93c7abfd6dd1234cc2f97c3484c807a96` |
| `design-material-macrovariation-packed-earth-route-01.png` | 1254×1254 | compacted aggregate, step/drain contact, switchback and route ribbon | `7c3869a1911ec2fd98ab0d6c228c7378af066275394ecb809d4b0cf49915b513` |
| `design-material-macrovariation-rammed-earth-01.png` | 1254×1254 | lift bands, plinth/drain contact, cut slope and earthen building masses | `a866461aea8ba92b9a8b32ef93ab7f6c9bc4a83e8365a2c8d82504e63512d269` |

## Visual QA

- Every board exposes close surface, grounded contact, medium object/route and
  far mass/corridor behavior instead of one isolated swatch.
- Plaster, stone, timber, tiles and rammed earth retain recognizable material
  families while varying repairs, moisture, joint scale and age.
- Route and creek boards keep the walking corridor/channel continuous and
  show drainage, wet/dry and embedded aggregate at human-eye distance.
- Forest/orchard ground includes litter, roots, sparse understory and a
  simplified far canopy without a uniform green carpet or cube crowns.
- No accepted board contains visible people, vehicles, readable text, logo,
  watermark, PBR-channel labels or normal-map graphics.

The boards are intentionally not claimed seamless. Some joint dimensions,
stone placement, tile repair layout, vegetation species and water geometry
vary between panels and must be resolved in canonical Blender geometry and
procedural material parameters.

## Trust boundary

Every board declares:

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
real_photo_texture=false
pbr_channel_alignment=not-provided
visual_scale_bands=authoring-guidance-not-measured
training_use=forbidden-as-multiview
coverage_use=forbidden
direct_projection_use=forbidden-as-measured-texture
trust_effect=none
```

These images cannot be cropped and promoted to real-photo texture. They do not
provide registered albedo, normal, roughness, metalness or height channels,
and synthetic channel derivation would not make them calibrated PBR evidence.
They do not prove texel density, material accuracy, 360-degree consistency or
arbitrary-coordinate coverage.

## GLM consumption order

1. First close the P7 parser and transaction P0 items in
   `FEEDBACK-HANDOFF-CODEX-033-glm-stop-fix-5e1e5ec.md`; do not mix
   reconstruction trust code with material commits.
2. Preserve canonical LOD0 geometry from Batch27/28. Add real geometry only
   where it carries material structure: mortar depth, tile thickness, timber
   end grain/joints, creek-bed relief, route drainage and rooted contacts.
3. Build deterministic per-material macro masks, UV-region groups and
   wet/dry/contact zones from procedural or independently authorized payloads.
   Do not use these PNG pixels as measured textures.
4. Bind source payload SHA, image dimensions, material ids, UV scale
   parameters, mask seeds, object assignments and generated payload SHA values
   into a content-addressed material/build report.
5. Extend the Blender UV audit to bind texture pixel dimensions before
   reporting texels per metre. The current `551.01×` finding is UV-coordinate
   area per square metre and proves severe variation, not a target texel
   density.
6. Rebuild an exact scene and compare the same reciprocal cameras before and
   after. Rerun Phase 4.3, clearance, six layers, target/seam visibility, UV
   report and post-render v2.
7. Hand Codex only the content/report SHAs and private RGB location for visual
   review; keep status `candidate` and `Reviewer: pending Codex`.

This batch reduces synthetic material-authoring ambiguity. It does not replace
real overlapping capture, accepted real-photo SfM, non-mock cloud-GPU 3DGS,
measured alignment, real imported geometry/appearance or real Viewer QA.

