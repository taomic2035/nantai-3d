# FEEDBACK-IMAGE2-035 — Batch30 spatial landmarks

Date: 2026-07-25
Producer: GPT image2
Consumer: Blender canonical geometry, close-range density and roaming QA

## Delivery

- Release:
  `synthetic-village-design-inputs-batch30-2026-07-25`
- URL:
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch30-2026-07-25>
- Archive:
  `synthetic-village-spatial-landmarks-batch30-2026-07-25.zip`
- Archive bytes:
  `28,578,050`
- Archive SHA-256:
  `e7c0417d5f61f6063388264677fbd635adfcfaf16e7e400cdeef9d58dbad20a1`
- Manifest SHA-256:
  `e2e64628bd48f3a2371d96ad66f67852d28a063f1c9e1a02a2b326f1d80caa32`
- Payload-checksum SHA-256:
  `3377b4e8643b891ae60382ae82891745b4661edb5d912775f047d37024317d6f`
- External checksum sidecar SHA-256:
  `17d2a776f6c11ae832843fb84e6e902b27ed17bf461ecfee269a47a9c6f8d3cc`

The archive has 19 sorted entries and was built twice with fixed timestamps;
both builds produced the same SHA-256. It contains only eight accepted PNGs,
eight exact prompts, `USAGE.md`, `manifest.json` and
`PAYLOAD-SHA256SUMS.txt`. Queue state, candidate-source records, failed
requests, contact sheets, build-proof copies and generation caches are absent.
All 18 declared payload hashes were recomputed from the archive bytes.

## Accepted inputs

All eight PNGs are `1536 x 1024`, RGB24.

| Asset | Replaceable authoring role | SHA-256 |
|---|---|---|
| `design-landmark-bridge-watermill-maintenance-01.png` | bridge/watermill approaches, underside, mechanics, tailrace, supports and maintenance landmarks | `33ee0fe3d502592d5f2ad34a23144462eef93d482b2815745dbdcd6e841b1c1a` |
| `design-landmark-courtyard-workshop-01.png` | courtyard/workshop/service-strip/storage landmark zones with clear entry and exit | `97aba869b28efab266674fe9fa153ff50488e45f9258c388f2d9a212654171e4` |
| `design-landmark-forest-edge-01.png` | forest entry/interior/return, roots, boulders, runoff, switchback and canopy gaps | `9d09f57494c0eb3a744d5d876c0e38c8dd9c2a39df23c023db034948b457bb4b` |
| `design-landmark-orchard-agriculture-01.png` | orchard rows, terrace stair, irrigation, tool shelter, garden and return route | `1b97f1d091befabe26e5c59e97277d8751e4a87b396d1bf3501c75c43bbd2fd6` |
| `design-landmark-residence-six-face-01.png` | residence front/side/rear/underside/corner/return detail and route-safe props | `22c7c663fd2f0bcd8d2bccee20968caa7ecb40394b30842f822276adb57a7b17` |
| `design-landmark-route-drainage-retaining-01.png` | junction, switchback, stair, culvert, retaining and reciprocal-route drainage | `488c148bfebacb1738288453d6f0e25597ccf80e3d620d6f021619fb19d5002e` |
| `design-landmark-utility-perimeter-01.png` | open gates, fences, water nodes, poles/wires, shelter and landscape boundary | `bf021f51e7abf3e11f655816fc3f02715f8bd7ae8edc14dbd294b68650cfe6b8` |
| `design-landmark-vertical-undercroft-01.png` | undercroft, retaining side, stair, connector underside, eave and rear balcony | `3c8679347816484353fc88e10d037017c09b9b5f6c3f898d3b982615b1214344` |

## Original-resolution visual QA

- The residence board exposes six usable faces with plinth, eaves, balcony,
  drainage and small landmark objects while retaining a perimeter route.
- The courtyard board groups workbench, tools, wood, jars, water and storage in
  functional side zones rather than scattering them across the walk line.
- The vertical board makes undercroft posts, retaining walls, guarded stairs,
  connector underside, eaves and rear balcony readable without black cavities.
- The bridge/watermill board contains one wheel, two open approaches, deck
  underside, axle/bearing, abutments, tailrace and a dry maintenance platform.
- The route board provides visibly different paving, drains, wall apertures,
  culvert and boulder landmarks while keeping reciprocal paths open.
- Orchard and forest boards replace uniform scatter with varied trees, roots,
  boulders, irrigation/runoff and route-return cues.
- The utility/perimeter board keeps gates open and places poles, wires, water
  node, shelter, fences and boundary trees outside the main footway.
- No accepted image contains visible people, animals, vehicles, text, logo or
  watermark.

The panels intentionally favor useful detail density over a claim of exact
cross-panel identity. Apparent member dimensions, props, vegetation, route
width and camera positions can vary and must be resolved in canonical geometry.

## Trust boundary

Every board declares:

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
real_photo_texture=false
spatial_landmark_positions=authoring-guidance-not-measured
training_use=forbidden-as-multiview
coverage_use=forbidden
clearance_use=forbidden-as-evidence
trust_effect=none
```

Six apparent views are not six calibrated cameras. They cannot be treated as
SfM, NeRF or 3DGS input, and they do not prove geometry consistency, landmark
coordinates, route width, collision clearance, reciprocal visibility,
360-degree coverage or arbitrary-coordinate reachability.

## GLM consumption order

Do not mix this geometry work into the held P7 trust commits. After the parser,
transaction, source-report and source-reality P0 items are Codex-reviewed:

1. replace proxy residence faces and floating slabs with canonical rear,
   side, undercroft, eave, foundation and vertical-circulation parts;
2. add route/drain/retaining landmarks with collision exclusions and stable
   route clearances;
3. add bridge/watermill maintenance, support and tailrace details without
   creating a second wheel or blocking either approach;
4. add deterministic orchard/forest variation and utility/perimeter landmarks
   using stable instance IDs rather than undifferentiated random scatter;
5. bind every part family, instance transform, collision proxy, material id,
   source image SHA and generated payload SHA in a machine build report;
6. rebuild the exact scene and rerun Phase 4.3, reciprocal clearance, target
   and seam visibility, six layers, UV/material audit and post-render v2;
7. hand Codex only content/report SHAs plus private RGB paths, with status
   `candidate` and `Reviewer: pending Codex`.

Batch30 improves synthetic local detail and orientation cues. It does not
replace real overlapping capture, accepted real-photo SfM, non-mock cloud-GPU
3DGS, measured alignment, real imported geometry/appearance or real Viewer QA.
