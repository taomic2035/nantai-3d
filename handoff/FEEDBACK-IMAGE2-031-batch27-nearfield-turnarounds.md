# FEEDBACK-IMAGE2-031 — Batch27 near-field turnaround inputs

Date: 2026-07-25
Producer: GPT image2
Consumer: Blender near-field geometry, environment modules and 360-degree QA

## Delivery

- Release:
  `synthetic-village-design-inputs-batch27-2026-07-25`
- URL:
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch27-2026-07-25>
- Archive:
  `synthetic-village-nearfield-turnarounds-batch27-2026-07-25.zip`
- Archive bytes: `27,811,987`
- Archive SHA-256:
  `79d9555e24f7f37c02fb7e10aabe1a99277d7c79ef7f9e693e8dd66545916a09`

The clean archive contains only eight accepted PNGs, eight exact prompts,
`USAGE.md`, `manifest.json` and `PAYLOAD-SHA256SUMS.txt`. Candidate-source
records, queue state, failed requests, rejected variants and generation caches
are excluded. GitHub reports the uploaded ZIP with the same SHA-256 digest.

## Accepted inputs

All eight PNGs are `1536 x 1024`, RGB.

| Asset | Replaceable modeling role | SHA-256 |
|---|---|---|
| `design-turnaround-four-sided-residence-01.png` | front/side/rear elevations, eaves, drainage and grounded plinth | `878a2011dc690702040e469646075b1b76ff580719fd65a23b1ccf7ec50e52f7` |
| `design-turnaround-side-entry-workshop-01.png` | side-entry workshop, service yard, drains and route clearance | `1a9a7d765ecfcb66770bd5ab85a5a2d31843ecf06274085c39039f3938a6f94b` |
| `design-turnaround-hillside-undercroft-house-01.png` | uphill/downhill foundation, undercroft, bracing, stair and retaining | `9554520f0e37d2d4ad0e50c6d34293bd9b0283705a921f2bb7c600f46ee6f970` |
| `design-detail-roof-eaves-junctions-01.png` | roof thickness, eave, verge, ridge, gutter, tie-in and flashing | `29cdc97846e06d8caf25de5961774ec4713bed59d5437845fdb7a692d08fb9d0` |
| `design-turnaround-waterwheel-mechanism-01.png` | spokes, paddles, axle, bearings, flume, tailrace and maintenance support | `45daef12a973d79e2f6308ed966f4a7f9d6fc34e4af69ba13ba50b9f695cb45d` |
| `design-turnaround-bridge-gallery-underside-01.png` | approaches, side span, underside beams, abutment and gallery return | `3428e8e21d84c9f1f6be6af99e8b320243c5878437f2e9294b9db735975719e0` |
| `design-board-forest-edge-canopy-underside-01.png` | forest edge/return, under-canopy route, roots and canopy underside | `f7af75b1c4dd557b32680f3f891f9a6f96105edf169ac132293c3230d57fd1df` |
| `design-board-route-drainage-junctions-01.png` | drains, step crossing, culvert, switchback, guard, gate, ramp and bridge transition | `b2452c5c8ab97dbbb2bb8661819a35f0449c53d3b44c963a2ce2b5c594edd857` |

## Visual QA

- The residence and workshop boards keep recognizable roof, material,
  opening and foundation families across four directions.
- The hillside board exposes uphill and downhill sides, short bearings,
  cross-bracing, undercroft and path continuity.
- The roof board exposes six readable close junctions without labels or
  floating product samples.
- The waterwheel board makes the wheel faces, paddles, hub, axle, bearing,
  inlet water, tailrace and maintenance path readable.
- The bridge board includes approaches, side elevation, underside beams,
  abutment contact and roofed return route.
- The forest board contains open-edge, return, interior, root-contact and
  canopy-underside views without cube/sphere crowns or a blocked path.
- The route board keeps all eight junctions walkable and shows downhill
  drainage, wall, guard and bridge contacts.
- No accepted board contains visible people, vehicles, text, logo or watermark.

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

The prompts request one coherent module across panels, but that consistency is
not machine verified. Apparent turnaround views are not calibrated cameras and
must not be treated as SfM, NeRF or 3DGS input. Blender authors must resolve
panel contradictions in canonical geometry rather than copying pixels or
silently inventing measured dimensions.

## Recommended consumption order

1. Use the residence, hillside and roof boards to replace oversized proxy
   buildings, thin roof edges and missing side/rear/underside construction.
2. Use the waterwheel and bridge boards to make load paths, bearings, paddles,
   beams, abutments and maintenance routes readable from below and behind.
3. Use the forest and route boards to add rooted near-field density, canopy
   underside, drainage and guard details without reducing route clearance.
4. Rebuild the content-addressed Blender artifact and rerun production camera
   clearance, target visibility, six-layer rendering and post-render v2.

This batch improves synthetic near-field design coverage. It does not replace
real capture, accepted real-photo SfM, non-mock GPU 3DGS training, measured
alignment or real Viewer QA.
