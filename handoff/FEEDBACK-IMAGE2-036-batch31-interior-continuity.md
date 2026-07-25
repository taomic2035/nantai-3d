# FEEDBACK-IMAGE2-036 — Batch31 interior continuity

Date: 2026-07-25
Producer: GPT image2
Consumer: Blender canonical interiors, portal graph and roaming QA

## Delivery

- Release:
  `synthetic-village-design-inputs-batch31-2026-07-25`
- URL:
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch31-2026-07-25>
- Archive:
  `synthetic-village-interior-continuity-batch31-2026-07-25.zip`
- Archive bytes:
  `24,748,420`
- Archive SHA-256:
  `a29c4032449367fe4efa376b2158b1fed807049fa2ac2bf535185153cdcf9805`
- Manifest SHA-256:
  `d25fbc027b55f760c410b996225673d6c263c4d4ab2a9945507acdecf2fab956`
- Payload-checksum SHA-256:
  `ed3d7b3beb6aee7a4136321fb4e1c812cfe2d1d972d5fee09acb44532815d7dc`
- External checksum sidecar SHA-256:
  `6b5a6e0c826d2636658ab24a848254e9b6d76e5afe95eda0c8c92be64bf46d28`

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
| `design-interior-attic-roof-structure-01.png` | attic arrival, ridge/eave/gable members, low rafter view and guarded return route | `b52a66b45a3636f89aa4ff249081d0e11f9ef8fad088c28c555874f36d7645e3` |
| `design-interior-covered-gallery-01.png` | reciprocal room portals, covered gallery, side opening, floor drain and supported return | `510c0de6c40a358669ae7c5eda726c0a398371d82173fcd8158649521e7e937b` |
| `design-interior-kitchen-service-01.png` | courtyard-to-kitchen-to-service route with hearth, work, storage, ventilation and drainage zones | `823503ddce6b38d64b0a516d6e214c42032652f1ba97c9cd1439657aaf1a1b9d` |
| `design-interior-main-room-360-01.png` | four directional room faces, low furniture clearance and reciprocal entrance orientation | `4e61f74b23a2c0f7dd05d85fa4cfcf36a2287b2b161af4de830644bae81c6116` |
| `design-interior-portal-threshold-01.png` | exterior approach, thick portal, threshold contacts, interior depth and reciprocal return | `7b41beb8a403702fa7632ec157130e00a96fd2db1cf89f4bbca1244f18708f08` |
| `design-interior-stair-landing-01.png` | ground approach, supported stair flight, upper landing, underside and daylight return | `aee9154712d0bd579e6658b72d836102fed875435483e99eceee63685aa03d11` |
| `design-interior-undercroft-cellar-01.png` | downhill entrance, undercroft bays, post footing, ventilation, drainage and return daylight | `e497bf5801969e3b148c5dad95a4d4860d0a344db2d1810ec3b72a17df0b53a7` |
| `design-interior-watermill-machinery-01.png` | single-wheel axle, guarded machinery, inspection walkway, drainage and two daylight exits | `e2fa8c9002535556651f0b2b98205b9fb020d32812299fa111bda8a1928a5448` |

## Original-resolution visual QA

- The entrance board exposes exterior approach, thick jamb/lintel, sill and
  floor contacts, readable room depth and reciprocal daylight without a black
  doorway cavity.
- The main room has distinct hearth, window, bench, storage and doorway faces,
  preserving a visible center and multiple lit openings.
- The kitchen separates hot, wet, work and storage zones beside a central
  route and shows both courtyard and service exits.
- The attic makes ridge, rafters, purlins, braces, eave and gable conditions
  readable while keeping access and return light visible.
- The stair board has real ground and upper endpoints, a guarded flight,
  underside support, daylight window and reciprocal return.
- The gallery joins two readable rooms with post/plinth support, side guard
  and an explicit drain outside the main walking line.
- The undercroft shows lit bays, post footings, ventilation, drainage and
  reciprocal exit rather than a sealed black cave.
- The watermill board shows one wheel/axle family, guarded machinery, bearing
  frame, inspection route, drainage and two daylight exits.
- No accepted image contains visible people, animals, vehicles, text, logo or
  watermark.

The panels intentionally favor useful construction and navigation cues over a
claim of exact cross-panel identity. Room dimensions, portal locations,
member sizing, furniture, machinery and camera positions can vary and must be
resolved in canonical geometry.

## Trust boundary

Every board declares:

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
real_photo_texture=false
interior_connectivity=authoring-guidance-not-measured
training_use=forbidden-as-multiview
coverage_use=forbidden
clearance_use=forbidden-as-evidence
trust_effect=none
```

Six apparent views are not six calibrated cameras. They cannot be treated as
SfM, NeRF or 3DGS input, and they do not prove one watertight room shell,
portal connectivity, stair clearance, collision safety, reciprocal
visibility, 360-degree coverage or arbitrary-coordinate reachability.

## GLM exact consumption queue

Do not start this geometry work until Codex has accepted the P7 COLMAP parser,
three-target transaction, content-addressed source report and exact-copy
source-reality rehearsal. Then execute continuously in this order:

1. Build an explicit portal-graph schema: stable `room_id`, stable
   `portal_id`, two directed edges per traversable opening, endpoint transforms,
   clear width/height values and collision-proxy SHA. Missing reverse edges,
   duplicate ids and dangling rooms must fail closed.
2. Replace black exterior doorway planes with the entrance/threshold module.
   Model thick jamb, lintel, sill, landing, drain contact and one readable
   interior shell. Add outside→inside and inside→outside cameras.
3. Add main room and kitchen/service shells with distinct four-face landmarks,
   multiple daylight apertures, real flue/drain geometry and routes kept clear
   of furniture. Do not project these PNG pixels as real texture.
4. Add stair/landing and covered-gallery edges. Test rise/run, headroom,
   guards, underside collision, both endpoints and reciprocal navigation.
5. Add attic and undercroft/cellar as separate volumes with visible supports,
   safe access, ventilation and drainage. Reject unsupported members, sealed
   black bays and stairs that do not terminate in graph nodes.
6. Add one watermill machinery room bound to the existing single-wheel module.
   Bind axle/bearing/guard/inspection-path ids; reject a second wheel or any
   moving-part proxy crossing the navigation route.
7. Emit one machine build report binding every room, portal, directed edge,
   transform, material slot, collision proxy, source image SHA and generated
   payload SHA. Status remains `candidate`; `Reviewer: pending Codex`.
8. Rebuild the exact scene and rerun portal-graph reachability, reciprocal
   clearance and target visibility, six layers, seam visibility, UV/material
   audit and post-render v2. Hand Codex only report/content SHAs and private RGB
   paths for review.

Batch31 improves synthetic interior modeling guidance. It does not replace
real overlapping capture, accepted real-photo SfM, non-mock cloud-GPU 3DGS,
measured alignment, real imported geometry/appearance or real Viewer QA.
