# Batch 23 Environment Envelope and Structural Support Design

> Date: 2026-07-23  
> Owner: Codex / built-in imagegen  
> Status: approved direction; written specification awaiting user review

## Goal

Generate sixteen reusable, independent design inputs that address the defects
measured in the fresh Batch22 exact-218 local-orbit renders:

- unsupported or floating bridge, watermill and walkway geometry;
- flat creek-bed and water-edge geometry;
- empty world/sky and insufficient middle-to-far environment enclosure;
- abrupt transitions between routes, soil, vegetation, retaining walls and
  water.

The images provide modeling and composition ideas for a generic mountain
village. They are not tied to a named real village and must remain replaceable.
They cannot create geometric consistency, calibrated 360-degree coverage or
arbitrary-coordinate navigation by themselves. Those properties must come from
the deterministic scene plan, modeled modules, camera/free-space audits and
runtime chunk system.

## Considered approaches

### A. Measured-gap closure

Create eight wide environment-envelope roles, four structural construction
roles and four surface-transition roles. This directly targets the defects
visible in the accepted Batch22 RGB evidence and gives downstream modeling
clear module boundaries.

**Adopted.**

### B. Scenic-variety pack

Add more houses, people, props and lifestyle scenes. This would make individual
images richer but would not resolve floating geometry, creek continuity or the
empty 360-degree envelope. It is deferred until the structural volume is
credible.

### C. Reference-edited pseudo-multiview ring

Repeatedly edit one source image into multiple viewpoints. This can improve
surface-level visual consistency but still does not create known intrinsics,
poses, pixel correspondences or shared geometry, and accumulated edits can
introduce hidden structural contradictions. It is rejected as a coverage
strategy.

## Asset set

### Wide environment envelope — 8 landscape images

These roles describe distinct, independently reusable environment modules. The
direction labels are composition roles, not calibrated camera poses.

| Stable asset ID | Modeling purpose |
|---|---|
| `envelope-upstream-creek-valley-01` | Upstream creek, flume approach, bank returns, enclosing ridges and a walkable crossing |
| `envelope-northeast-forest-terrace-01` | Forest-to-terrace transition, switchback path, retaining edges and layered skyline |
| `envelope-east-orchard-route-01` | Orchard slope, service path, drainage channel and distant village massing |
| `envelope-southeast-village-service-edge-01` | Rear service courts, storage edges, path continuation and hillside enclosure |
| `envelope-downstream-creek-basin-01` | Tailwater basin, downstream creek bends, flood shelf and route return |
| `envelope-southwest-stone-bank-return-01` | Stone-bank return, bridge landing, planted slope and alternate loop |
| `envelope-west-uphill-forest-loop-01` | Uphill forest loop, stepped retaining wall and a readable return toward the village |
| `envelope-northwest-flume-ridge-01` | Elevated flume support, ridge backdrop, upper path and lower creek relationship |

Each image must show a foreground connection, middle-ground module and
far-field enclosure. The skyline should be filled by terrain, forest or distant
village mass rather than a blank studio background. Routes must visibly
continue out of frame so they can connect to neighboring chunks.

### Structural construction — 4 landscape/detail images

| Stable asset ID | Modeling purpose |
|---|---|
| `construction-bridge-watermill-longitudinal-support-01` | Longitudinal relationship between bridge deck, watermill, piers, braces, foundations and creek bed |
| `construction-cross-bank-foundation-01` | Opposing bank foundations, abutment returns, scour protection and walkable landing |
| `construction-tailrace-creek-junction-01` | Tailrace outlet, dry/wet bed levels, retaining returns, drainage and accessible maintenance edge |
| `construction-retaining-stair-orbit-support-01` | Retaining wall, stair, maintenance platform and continuous pedestrian loop with visible load paths |

These are photorealistic construction references, not engineering drawings.
They must show how loads reach terrain and avoid unexplained floating slabs,
cantilevers or disconnected posts.

### Surface and geometry transitions — 4 square/detail images

| Stable asset ID | Modeling purpose |
|---|---|
| `transition-creek-bed-wet-dry-01` | Gravel, embedded stone, shallow water, wet margin and dry bank transition |
| `transition-moss-stone-drainage-wall-01` | Drained retaining wall, weep paths, cap stones, soil and vegetation contact |
| `transition-timber-stone-bearing-joint-01` | Weathered timber bearing on stone with metal strap, clearance and water protection |
| `transition-route-soil-vegetation-01` | Stone route, compacted soil, grass, roots, drainage edge and terrain blending |

These images are material-and-shape references only. They are not seamless
textures and do not provide measured normal, roughness, height, metallic or
displacement data.

## Shared generation direction

- Use case: `photorealistic-natural`.
- Setting: a generic humid mountain village with stone, timber, clay tile,
  creek water, mixed forest, terraces and orchard vegetation.
- Lighting: soft natural overcast or diffused daylight so construction and
  surface contact remain readable.
- Visual density: multiple foreground, middle-ground and far-field elements,
  but no decorative clutter that hides structure.
- Camera: human-eye or modest elevated survey viewpoint; no fisheye,
  impossible aerial orbit or extreme tilt.
- Avoid: text, labels, logos, watermark, people, animals, vehicles, duplicate
  waterwheels, fantasy architecture, floating platforms, unsupported bridges,
  sealed horizons and blank studio backgrounds.
- Landscape outputs should target a wide composition; transition studies
  should target square or near-square framing.

Every image receives a separate exact prompt. Distinct assets require distinct
built-in imagegen calls; a contact sheet is not a substitute for individual
source files.

## Provenance and trust boundary

Every manifest row must declare:

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

The eight environment roles are not claimed to depict one shared scene. The
directional arrangement is a downstream modeling brief only. No file name,
prompt wording, generation order or visual similarity may promote the images
to measured, metric, aligned or reconstruction evidence.

## Storage and clean Release

Private generation candidates and QA artifacts live under:

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/
```

The final Release archive contains exactly:

- 16 accepted PNG files;
- 16 exact prompt text files;
- `manifest.json`;
- `USAGE.md`;
- `PAYLOAD-SHA256SUMS.txt`.

Rejected variants, failed generations, contact sheets, screenshots, browser
downloads, queue state and generation logs remain private and must not enter
Git or Release.

## QA and acceptance

Each final image is inspected at original resolution for:

1. correct role and readable foreground/middle/far structure;
2. no visible text, watermark, people, animals or vehicles;
3. no unexplained floating or unsupported primary structure;
4. no duplicate main waterwheel where a watermill appears;
5. route, creek, terrain or structural connections continuing beyond the
   central subject;
6. sufficient element count to inform modular geometry rather than a single
   isolated hero object;
7. exact file bytes, dimensions, byte length and SHA-256 recorded in the
   manifest.

An image failing a role is replaced with a newly generated sibling; it is not
silently cropped or renamed into another role. Release verification must
recompute the archive SHA-256 and every payload checksum after extraction.

## Downstream consumption

The accepted images may inform:

- new environment-module `part_layout` entries;
- support objects and attachment relationships;
- creek-bed, bank, water and route transition geometry;
- far-field terrain/forest/terrace enclosure;
- camera targets and visual-review checklists.

They must not directly determine world coordinates or acceptance. After
modeling, downstream acceptance requires a fresh content-addressed scene build,
Phase 4.3 topology/intersection probe, translated camera/free-space checks,
six-layer renders, visibility gates, post-render v2 and human RGB review.

For arbitrary-coordinate synthetic roaming, newly modeled modules must also be
consumable by the deterministic chunk system with stable asset/version/SHA
keys. For real-scene roaming, the separate real capture, accepted COLMAP,
external GPU 3DGS training, import/alignment/chunk and Viewer QA chain remains
required.

## Success criteria

- all sixteen approved roles have one accepted original-size PNG and one exact
  prompt;
- the pack substantially expands environment enclosure, structural support and
  surface-transition coverage without duplicating Batch22's local waterwheel
  orbit;
- every file is replaceable and carries the explicit design-only trust
  boundary;
- the Release contains no intermediate state and all payload checksums verify;
- README and the image2 feedback document explain how to download, verify and
  consume the pack;
- no documentation claims that the images themselves enable 360-degree
  reconstruction or arbitrary-coordinate traversal.
