# FEEDBACK IMAGE2 029 — Batch25 environment realism inputs

Date: 2026-07-24
Status: published, design-only

## Delivery

- Release:
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch25-2026-07-24>
- Tag: `synthetic-village-design-inputs-batch25-2026-07-24`
- Archive:
  `synthetic-village-environment-realism-pack-batch25-2026-07-24.zip`
- Archive bytes: `28,092,682`
- Archive SHA-256:
  `6673d94c7651a21d73706b1810626d0a9559668eb229dd13a7aa028599906575`
- Generator: OpenAI built-in imagegen
- Accepted images: `8/8`
- Rejected variants: `0`
- Transient requests that wrote no candidate: `2`

The clean archive contains eight final PNGs, eight exact per-image prompts,
`manifest.json`, `USAGE.md` and `PAYLOAD-SHA256SUMS.txt`. It excludes the
private contact sheet, failed network requests, browser attempts, generated
image caches and release-verification staging. Independent extraction verified
all eighteen declared payload checksums.

## Asset inventory

| File | Role | Dimensions | Bytes | SHA-256 |
|---|---|---:|---:|---|
| `environment-creek-bank-cut-bed-crossing-01.png` | carved creek bed, wet/dry bank and supported path | 1536×1024 | 4,034,510 | `6dc8f4a81af1b35f3c60c0b10b63fe1eba02ac651d81dbd3f6ee4965f9b35c10` |
| `environment-forest-orchard-transition-01.png` | gradual forest, scrub and orchard transition | 1536×1024 | 3,777,449 | `666c70b266e3f5c2fd7382e70e445aaaca9b7baa7ab121b823b6dd6a2df7c890` |
| `environment-orchard-terraces-service-route-01.png` | varied orchard ages, terraces, drainage and route | 1536×1024 | 3,810,773 | `ff3a278aeda21fe7d2ca8427a0682b9f7edf333033ca4c77ef5ffc2de14873bf` |
| `environment-overcast-world-lighting-panorama-01.png` | overcast world, ridge horizon and atmospheric depth | 1799×874 | 1,717,587 | `5d003ccc4936b3958342ba876a440d70d88d836938eab5d28c5abcb33cc3b8a3` |
| `environment-path-slope-drainage-transition-01.png` | stone path, earth shoulder, drainage and cut slope | 1536×1024 | 4,039,846 | `d4d594072f94d7536b9f098f99bbfbf20a85ce3318aebb1af267998faf4b6542` |
| `environment-retaining-wall-vegetated-seam-01.png` | variable retaining wall and vegetated termination | 1536×1024 | 4,095,732 | `bc9240658233caf72f135a25b51fc76a5ed4febd931a7ced600f7a09714f2293` |
| `environment-vegetation-open-corridor-01.png` | rooted mixed vegetation around an open corridor | 1536×1024 | 4,181,481 | `b23a96364cf2bff5301fc14a5a3f4915263db5337b530d50d5e7a67421ca800c` |
| `environment-village-edge-layered-horizon-01.png` | outer route, village middle layer and distant ridges | 1536×1024 | 3,367,932 | `f8f924ea24eebc6561a81deee68485eb9ba23792b0d35da30fc1aeea66295419` |

All eight PNG SHA values are distinct. The manifest binds every PNG to one
exact prompt path, byte size and prompt SHA-256.

## Original-resolution visual QA

All PNGs decode successfully. Seven are `1536×1024`; the world-lighting
reference is `1799×874`. Visual inspection confirmed:

- every route continues through the frame and is not blocked by a primary
  trunk, support or retaining structure;
- trunks, shrubs, walls and path supports visibly contact soil, stone or the
  creek bed;
- vegetation uses mixed ages and silhouettes rather than cube crowns or one
  repeated tree;
- creek water occupies a carved bed with distinct submerged, wet-bank and dry
  bench layers;
- path, wall, drainage and slope materials meet in surrounding context rather
  than as isolated texture swatches;
- village and world references contain readable foreground, middle and distant
  layers;
- no visible text, watermark, UI, people, vehicles or fantasy elements.

The images are appearance and construction references. Visual plausibility
does not establish camera calibration, shared geometry or metric scale.

## Trust boundary

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
real_photo_texture=false
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

Each PNG was generated independently. The batch is not a multiview set and
does not prove 360-degree consistency or arbitrary-coordinate coverage. The
panorama is not a measured equirectangular HDRI and must not be treated as
image-based-lighting evidence.

## Blender consumption order

1. Replace exact-266 box crowns with deterministic mixed tree families and
   preserve the measured six-metre route-center clearance.
2. Add ground-contact understory, rock and litter clusters with route and
   drainage exclusion masks.
3. Model a cut creek bed and separate submerged, wet-bank and dry-bench
   surfaces before applying water.
4. Split long path, terrain and retaining surfaces into non-repeating material
   regions; do not project one PNG directly as a scene texture.
5. Add a synthetic neutral world, ridge horizon and atmospheric depth while
   retaining `synthetic / preview-only`.
6. Rebuild the content-addressed scene, rerun all sixteen reciprocal RGB views,
   then run formal clearance, six-layer visibility and post-render v2.

The decisive real-scene gap remains unchanged: real overlapping capture,
accepted SfM poses, non-mock GPU reconstruction/training, real imported
geometry and measured alignment are still absent.
