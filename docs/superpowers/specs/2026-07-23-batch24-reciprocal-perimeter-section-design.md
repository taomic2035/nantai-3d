# Batch 24 Reciprocal Perimeter and Section-Closure Design

Date: 2026-07-23

## 1. Goal

Generate sixteen replaceable synthetic design inputs that make the current
eight-sector environment envelope more useful for 360-degree inspection and
arbitrary-coordinate Blender modeling:

1. eight translated reciprocal views looking inward from outside the Batch23
   perimeter roles; and
2. eight oblique construction studies that expose each sector's terrain,
   foundation, drainage, water-level or support closure.

The batch closes design blind spots. It does not create calibrated reciprocal
poses, pixel correspondences, real textures, measured geometry or reconstruction
coverage.

## 2. Why this batch is next

Batch18 and Batch19 already cover selected reverse, interior, undercroft,
roofscape and translated-volume roles. Batch20 through Batch22 cover bridge,
watermill and forest topology plus a local watermill orbit. Batch23 adds eight
perimeter environment envelopes but only one independent composition per
sector.

The remaining high-value visual gap is what a user sees after moving beyond
those perimeter modules and turning back toward the village. The same modules
also need readable cross-slope support and drainage so Blender consumption does
not recreate floating decks, flat creek planes or terrain seams.

More generic front-facing village images would add little. A reciprocal plus
section pair for every perimeter role directly constrains the missing back
surfaces and vertical closure.

## 3. Alternatives considered

### A. Reciprocal perimeter plus section closure — selected

Eight inbound translated views and eight paired construction studies. This is
the strongest direct input for bidirectional perimeter routes, cross-chunk
seams and terrain-contact modeling.

### B. Material and LOD pack

Forest, creek, stone, tile and ground references would improve appearance but
would not close back surfaces, route continuity or foundations. Existing
Batch15, Batch21, Batch22 and Batch23 transition inputs already provide a
partial material basis.

### C. Mixed reciprocal/material/interior pack

This would distribute sixteen images across too many independent gaps. It
would weaken the per-sector reciprocal constraint and duplicate Batch18/19.

## 4. Source bindings

Each Batch24 role binds exactly one accepted Batch23 PNG. The binding means
"use this image as visual and layout context." It never means that imagegen
recovered the same physical scene or camera.

| Sector | Batch23 source | Source SHA-256 |
|---|---|---|
| upstream creek valley | `envelope-upstream-creek-valley-01.png` | `4162f58ae98581d609785376c835d9dc858e54634ae63b8d11ab5b969b524a59` |
| northeast forest terrace | `envelope-northeast-forest-terrace-01.png` | `f8273831cabdd611226f806fec63cf210ab1ab122b043fe80bca333ad3211c98` |
| east orchard route | `envelope-east-orchard-route-01.png` | `e194896e2c2fedcac4d9f7b0665a58938b07bd86df02edc12b3bd6e6b0e081b5` |
| southeast service edge | `envelope-southeast-village-service-edge-01.png` | `49db1b5eaf41ae2c1c5c28efc96ec548eeb09155cbf686f5657885b56a477547` |
| downstream creek basin | `envelope-downstream-creek-basin-01.png` | `35836fa82d42942bdd5393c224e3beec7da12b2ce140b7ee2a31f5420f960ce3` |
| southwest stone bank | `envelope-southwest-stone-bank-return-01.png` | `6be0758b5ed1af4452924a9e7f1df06fe8484f32eef187bed44d402a444a1f76` |
| west uphill forest | `envelope-west-uphill-forest-loop-01.png` | `0491b126697f0713c6ec675749d97a20291a23bc7cb1f0eb5f507aa434b79e49` |
| northwest flume ridge | `envelope-northwest-flume-ridge-01.png` | `a76097052687970b945a8326427923f457055b710cf218d96de63437f4633a4f` |

## 5. Deliverables

Private candidate root:

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/
```

### 5.1 Reciprocal inbound views

| Asset ID | Required view |
|---|---|
| `reciprocal-upstream-creek-valley-inbound-01` | translated viewpoint farther upstream, looking back along the creek, crossing, supported flume and path toward the village |
| `reciprocal-northeast-forest-terrace-inbound-01` | viewpoint beyond the outer terrace, looking downhill through the switchback, drainage and retaining walls toward village roofs |
| `reciprocal-east-orchard-route-inbound-01` | viewpoint outside the orchard, looking back along the service route through stepped fruit trees toward the village |
| `reciprocal-southeast-service-edge-inbound-01` | viewpoint behind the service edge, looking through courtyards, wood storage, drainage and paths toward the village center |
| `reciprocal-downstream-creek-basin-inbound-01` | viewpoint farther downstream, looking upstream across flood bench, footbridge and tailwater toward the mill |
| `reciprocal-southwest-stone-bank-inbound-01` | viewpoint beyond the stone-bank loop, looking back across the supported landing, abutment and alternate route |
| `reciprocal-west-uphill-forest-inbound-01` | viewpoint above the forest loop, looking downhill through steps, landings and retaining walls toward village roofs |
| `reciprocal-northwest-flume-ridge-inbound-01` | viewpoint beyond the ridge connection, looking back along the braced flume, lower creek path and hillside route |

Every reciprocal view must be a translated position with a changed foreground,
not a horizontal mirror or crop of its source.

### 5.2 Section-closure studies

| Asset ID | Required construction relation |
|---|---|
| `section-upstream-flume-creek-support-01` | creek bed, bank, path, crossing foundation and repeated flume-support footings in one oblique cross-slope view |
| `section-northeast-terrace-drainage-01` | terrace retaining depth, stair landing, soil backfill, open drainage and forest-slope contact |
| `section-east-orchard-route-cutfill-01` | orchard bench, route cut/fill, low retaining wall, drainage edge, roots and downhill stabilization |
| `section-southeast-service-yard-drainage-01` | service courtyard slab or paving, building foundation, wall drain, open channel and lower terrain discharge |
| `section-downstream-tailwater-floodbench-01` | tailrace invert, ordinary creek water level, wet shelf, dry flood bench and supported footbridge foundation |
| `section-southwest-bridge-bank-foundation-01` | bridge landing beam, dry-stone abutment, stepped footing, bank return and scour-resistant creek edge |
| `section-west-forest-loop-retaining-01` | switchback stair, landing support, retaining depth, drainage and rooted forest-slope contact |
| `section-northwest-flume-ridge-support-01` | flume channel, braces, repeated tower footing, lower route clearance, creek bank and upper slope connection |

These are photorealistic oblique construction references, not labeled diagrams,
orthographic drawings or literal cutaway renders.

## 6. Image-generation method

- Use OpenAI built-in imagegen only.
- Make one call per asset.
- Use the exact paired Batch23 source as `referenced_image_paths`.
- Preserve the generic humid mountain-village material language but require a
  materially different viewpoint or construction emphasis.
- Keep the accepted image under the exact asset ID in the private Batch24 root.
- Keep rejected variants under `rejected/`; never publish them.
- Keep the exact complete prompt for every call under `prompts/`.
- A failed call writes no candidate row and no empty file.

## 7. Shared prompt constraints

All prompts require:

```text
Use case: photorealistic-natural
Asset type: reusable reciprocal-perimeter or section-closure design input for a generic mountain-village 3D scene
Style: highly detailed naturalistic environmental photography, physically plausible structure, no fantasy styling
Lighting: soft diffused overcast daylight with readable ground contact, support depth and drainage
Materials: weathered local stone, aged timber, clay roof tile where buildings appear, soil, gravel, shallow creek water and humid mixed vegetation
Constraints: generic and replaceable; no text, labels, symbols, diagrams, watermark, people, animals, vehicles, modern utilities or decorative clutter
Avoid: mirrored source, identical source viewpoint, drone view, fisheye, sealed horizon, black voids, floating slabs, unsupported bridge or flume, impossible cantilever, duplicated waterwheel and hidden foundations
```

Reciprocal prompts additionally require an outside translated camera looking
inward, a changed foreground, continuous entry/exit routes and a readable
relationship back to the source role.

Section prompts additionally require an oblique ground-level construction
study with visible terrain contact, water/drainage path and load path. They
must not become a labeled diagram or isolated object on a blank background.

## 8. Visual QA

Every original-resolution PNG must record:

```text
role_match
source_role_relationship
translated_viewpoint_or_section_emphasis
foreground_middle_far
route_or_water_continuity
terrain_and_support_contact
visible_text_or_watermark
people_animals_vehicles
floating_primary_structure
mirror_or_duplicate_source
accept_or_reject
```

Acceptance requires the first six applicable fields to be true and all four
defect fields to be false. A reciprocal image that merely mirrors, crops or
restyles the Batch23 source is rejected. A section image that hides the footing,
drainage or terrain contact is rejected.

The final top-level Batch24 directory must contain exactly sixteen accepted
PNGs. Rejected variants remain private and are excluded from the final payload.

## 9. Machine manifest and trust boundary

The manifest records actual file bytes, dimensions, lowercase SHA-256, exact
prompt path, source file, source SHA-256, kind, sector and role. Its trust
object is exactly:

```json
{
  "synthetic": true,
  "stage": "design-only",
  "camera_calibration": "unknown",
  "geometry_consistency": "not-verified",
  "metric_scale": "unknown",
  "real_photo_texture": false,
  "training_use": "forbidden-as-multiview",
  "coverage_use": "forbidden",
  "trust_effect": "none"
}
```

The source binding does not change these fields. No source filename, prompt
language or visual similarity may promote the images to reciprocal-pose,
measured, aligned or coverage evidence.

## 10. Clean Release

Publish a clean content-addressed Release:

```text
tag:
  synthetic-village-design-inputs-batch24-2026-07-23

archive:
  synthetic-village-reciprocal-perimeter-section-pack-batch24-2026-07-23.zip
```

The archive contains only:

- sixteen accepted PNGs;
- sixteen exact prompts;
- `manifest.json`;
- `USAGE.md`;
- `PAYLOAD-SHA256SUMS.txt`.

It excludes rejected variants, contact sheets, generation queues, temporary
files and any Batch23 source PNG duplicated as a payload.

## 11. Blender consumption

1. Map every reciprocal image to an inward-facing perimeter camera role and
   use it to list required back surfaces; do not infer camera coordinates from
   pixels.
2. Convert every section study into explicit terrain, footing, support,
   drainage and water-level components.
3. Build eight bidirectional perimeter modules with stable IDs and
   content-addressed source bindings.
4. Connect roads, creek, flume, terrain and vegetation across neighboring
   chunk boundaries without a density or elevation cliff.
5. Rebuild registry, production plan and exact Blender scene after topology or
   placement changes.
6. Require reciprocal translated cameras, cross-chunk seam probes, clearance,
   visibility, six-layer renders and post-render v2 before acceptance.

The images only guide modeling. Actual 360-degree and arbitrary-coordinate
acceptance comes from the rebuilt scene and machine-verifiable render and
navigation evidence.

## 12. Success criteria

Batch24 is complete only when:

- all sixteen exact roles exist as decodable PNGs at or above `1200×900`;
- every role passes original-resolution visual QA;
- all source bindings and payload bytes round-trip through SHA-256;
- the clean Release round-trips after extraction and contains no intermediates;
- README and a feedback document explain download, use and trust limits;
- tracked documentation is committed with the required Codex trailer and
  pushed to `main`.

This completes the Batch24 input pack, not the full real-3D objective. The
larger objective remains incomplete until real capture, SfM, external GPU
training/import, modeled-scene consumption and roaming evidence exist.
