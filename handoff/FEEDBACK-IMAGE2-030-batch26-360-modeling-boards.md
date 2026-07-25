# FEEDBACK-IMAGE2-030 — Batch26 360 modeling boards

Date: 2026-07-25
Producer: GPT image2
Consumer: Blender environment-module and real-scene gap work

## Delivery

- Release: `synthetic-village-design-inputs-batch26-2026-07-25`
- URL: <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch26-2026-07-25>
- Archive: `synthetic-village-360-modeling-boards-batch26-2026-07-25.zip`
- Archive SHA-256: `91f75d265357f9ff25785c466aafe5dd6a1e104b0608ffc0b40e0972a76dcb39`

The clean archive contains only six accepted PNGs, six exact prompts,
`USAGE.md`, `manifest.json` and `PAYLOAD-SHA256SUMS.txt`. Queue state, failed
requests, rejected variants, contact sheets and generation caches are absent.

## Accepted inputs

| Asset | Intended replaceable modeling role | SHA-256 |
|---|---|---|
| `design-board-ground-water-retaining-transitions-01.png` | terrain, creek bank, retaining and route transitions | `ad8c37eba9444dcb29fead91e84d85181ef887c598b0dee740c18835d373fa9b` |
| `design-board-vegetation-orchard-forest-boundaries-01.png` | vegetation density, orchard rows and forest boundaries | `2907f7b65a516a840074efcb5191b7a31ffb2e3f8e2e777914aa8a83ab5d8944` |
| `design-board-building-rear-service-supports-01.png` | rear façades, service yards, foundations and supports | `df73a5f3361ed86f8d41dffd31ce86dc9881f332bd9993f2a19981e693ebe8ce` |
| `design-board-bridge-watermill-creek-junctions-01.png` | bridge, watermill, creek and maintenance-route junctions | `375a8129d9677595209e9e842ba9179abbd9f9156185b772c52b9550e68d6999` |
| `design-board-layered-terrain-sky-world-density-01.png` | layered terrain, distant ridges, sky and world density | `652c3b888617c7171c97869893a551add5dea3be1dba9d87c67c16bc5a10e629` |
| `design-board-material-contact-transitions-01.png` | stone, soil, timber, plaster, water and vegetation contacts | `4f103ec8f2a487e4076a0ff2035945d6e1125081448776fe5d068a0b8f17cf4a` |

## Trust boundary

Every board declares:

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

The six images are independent references, not synchronized views of one
physical scene. They must not be used as SfM, NeRF or 3DGS training coverage
and cannot promote geometry to measured, metric, aligned or real.

## Recommended consumption order

1. Use the ground/water/retaining and material-contact boards to remove flat
   creek planes, hard terrain seams and unsupported contacts.
2. Use the bridge/watermill and rear-service boards to add readable structural
   support and maintenance access.
3. Use the vegetation and layered-world boards to replace repeated block
   crowns, open boundaries and empty sky while preserving route clearance.
4. Keep every resulting Blender module `modeled-unverified` until a fresh exact
   build, camera visibility, six-layer render and post-render policy report
   pass.

These boards reduce synthetic scene-design gaps. They do not replace real
capture, accepted real-photo SfM, non-mock GPU 3DGS training, measured
alignment or real Viewer QA.