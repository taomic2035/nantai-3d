# Batch 24 Reciprocal Perimeter and Section Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate, inspect, package, publish and document sixteen source-bound design-only inputs that add reciprocal perimeter views and terrain/support closure references to Batch23.

**Architecture:** Each asset is one OpenAI built-in imagegen reference-edit call bound to exactly one verified Batch23 PNG. Private candidate storage retains prompts, source bindings, rejected variants and QA state; a clean content-addressed Release contains only accepted PNGs, exact prompts, manifest, usage note and checksums. All trust stays design-only and downstream Blender acceptance remains machine-gated.

**Tech Stack:** OpenAI built-in imagegen, Codex `view_image`, PowerShell, Pillow, JSON, SHA-256, ZIP, GitHub CLI, Markdown.

---

## File map

**Private, ignored inputs and working state**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/*.png`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/prompts/*.prompt.txt`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/rejected/`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/source-bindings.json`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/qa-results.json`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/manifest.json`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/USAGE.md`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/PAYLOAD-SHA256SUMS.txt`

**Private Release staging**

- Create: `.nantai-studio/release-staging/batch24-reciprocal-perimeter-section/`
- Create: `.nantai-studio/release-staging/synthetic-village-reciprocal-perimeter-section-pack-batch24-2026-07-23.zip`
- Create: `.nantai-studio/release-staging/synthetic-village-reciprocal-perimeter-section-pack-batch24-2026-07-23.SHA256SUMS.txt`

**Tracked documentation**

- Create: `handoff/FEEDBACK-IMAGE2-028-batch24-reciprocal-perimeter-section.md`
- Modify: `README.md`

## Exact prompt construction

Every prompt file starts with this exact shared contract:

```text
Use case: photorealistic-natural
Asset type: reusable reciprocal-perimeter or section-closure design input for a generic mountain-village 3D scene
Style: highly detailed naturalistic environmental photography, physically plausible structure, no fantasy styling
Lighting: soft diffused overcast daylight with readable ground contact, support depth and drainage
Materials: weathered local stone, aged timber, clay roof tile where buildings appear, soil, gravel, shallow creek water and humid mixed vegetation
Constraints: generic and replaceable; no text, labels, symbols, diagrams, watermark, people, animals, vehicles, modern utilities or decorative clutter
Avoid: mirrored source, identical source viewpoint, drone view, fisheye, sealed horizon, black voids, floating slabs, unsupported bridge or flume, impossible cantilever, duplicated waterwheel and hidden foundations
```

Reciprocal prompt files then append this exact mode contract:

```text
Reference handling: use the supplied Batch23 image only as visual language and role context. Render a new camera position translated outside the perimeter and looking inward. Change the foreground and reveal back-facing surfaces. Do not mirror, crop, trace or restyle the source composition.
Composition: human-eye survey viewpoint with continuous entry and exit routes, readable foreground/middle/far layers, surrounding terrain and a visible relationship back toward the village.
```

Section prompt files append this exact mode contract:

```text
Reference handling: use the supplied Batch23 image only as visual language and role context. Render a new oblique ground-level construction study emphasizing terrain contact, drainage or water path and the complete support/load path. Do not create a labeled diagram, orthographic drawing, literal cutaway, isolated object or blank background.
Composition: show enough surrounding route, terrain, vegetation and built context to model the connection; foundations and discharge paths must remain visible rather than hidden by decorative foliage.
```

Append exactly one role block:

| Asset ID | Exact role block |
|---|---|
| `reciprocal-upstream-creek-valley-inbound-01` | `Primary request: move the camera farther upstream beyond the source viewpoint and look back toward the village along the shallow creek. Use a new creek-bank foreground; show the modest crossing, supported timber flume, bank path and village-facing route from their reverse sides, with layered ridges beyond.` |
| `reciprocal-northeast-forest-terrace-inbound-01` | `Primary request: move the camera beyond the outer forest terrace and look downhill toward the village. Use a new upper-slope foreground; reveal the reverse faces of the switchback path, dry-stone retaining walls, stair connections and open drainage, with village roofs and mountains in the distance.` |
| `reciprocal-east-orchard-route-inbound-01` | `Primary request: move the camera outside the orchard perimeter and look back along the service route toward the village. Use a new fruit-tree and uphill-soil foreground; reveal downhill retaining, drainage and route surfaces while preserving continuous route exits and layered mountains.` |
| `reciprocal-southeast-service-edge-inbound-01` | `Primary request: move the camera behind the village service edge and look inward through several small service courtyards toward the village center. Use a new rear-yard foreground; reveal back walls, foundations, wood storage, drainage channels and connecting paths without a single hero building.` |
| `reciprocal-downstream-creek-basin-inbound-01` | `Primary request: move the camera farther downstream and look upstream toward the watermill. Use a new wet-gravel and dry-flood-bench foreground; show the supported footbridge, tailwater junction, bank-return paths and mill from their downstream sides, with a continuous creek corridor.` |
| `reciprocal-southwest-stone-bank-inbound-01` | `Primary request: move the camera beyond the southwest stone-bank loop and look back toward the village. Use a new outer-bank foreground; show the supported bridge landing, dry-stone abutment, alternate pedestrian loop, drainage and planted slope from their reverse sides.` |
| `reciprocal-west-uphill-forest-inbound-01` | `Primary request: move the camera above the west forest loop and look downhill toward the village. Use a new upper-forest foreground; reveal the descending faces of steps, supported landings, retaining walls, roots and drainage with village roofs visible through the trees.` |
| `reciprocal-northwest-flume-ridge-inbound-01` | `Primary request: move the camera beyond the northwest ridge connection and look back along the elevated flume toward the village. Use a new upper-route foreground; reveal the reverse faces of repeated braced supports, lower creek path, tower footings and hillside connection.` |
| `section-upstream-flume-creek-support-01` | `Primary request: an oblique cross-slope construction study containing the upstream creek bed, both bank levels, the path, modest crossing foundation and multiple timber-flume support footings. Show ordinary water level, embedded stone, erosion protection, bracing and every support touching terrain.` |
| `section-northeast-terrace-drainage-01` | `Primary request: an oblique construction study across the northeast forest terrace. Show retaining-wall depth and batter, soil backfill, cap stones, switchback stair landing, open drainage path and rooted forest-slope contact in one physically plausible assembly.` |
| `section-east-orchard-route-cutfill-01` | `Primary request: an oblique construction study across the east orchard route. Show the orchard bench, uphill cut, downhill fill, low retaining wall, compacted route build-up, gravel drainage edge, fruit-tree roots and slope stabilization.` |
| `section-southeast-service-yard-drainage-01` | `Primary request: an oblique construction study across a southeast service courtyard. Show permeable paving or slab build-up, stone-and-timber building foundation, rear wall drain, open surface channel, threshold and discharge to lower terrain without a black void.` |
| `section-downstream-tailwater-floodbench-01` | `Primary request: an oblique construction study at the downstream tailwater junction. Show outlet invert, ordinary creek water level, submerged gravel, wet shelf, dry flood bench, retaining return, supported footbridge foundation and bank route in one readable hydraulic relation.` |
| `section-southwest-bridge-bank-foundation-01` | `Primary request: an oblique construction study of the southwest bridge landing and creek bank. Show deck or landing beam, dry-stone abutment, stepped footing, bank-return wall, drainage, scour-resistant creek edge and the route continuing on both sides.` |
| `section-west-forest-loop-retaining-01` | `Primary request: an oblique construction study across the west forest switchback. Show stair build-up, supported landing, retaining-wall depth, soil backfill, drainage outlet, tree roots and lower-slope contact without a floating platform.` |
| `section-northwest-flume-ridge-support-01` | `Primary request: an oblique construction study across the northwest flume ridge. Show water channel, longitudinal beam, diagonal braces, repeated tower footing, lower-route clearance, creek-bank contact and upper-slope connection with a complete visible load path.` |

## Source bindings

Use these exact local source paths and SHA-256 values:

| Sector | Source path | SHA-256 |
|---|---|---|
| upstream | `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-upstream-creek-valley-01.png` | `4162f58ae98581d609785376c835d9dc858e54634ae63b8d11ab5b969b524a59` |
| northeast | `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-northeast-forest-terrace-01.png` | `f8273831cabdd611226f806fec63cf210ab1ab122b043fe80bca333ad3211c98` |
| east | `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-east-orchard-route-01.png` | `e194896e2c2fedcac4d9f7b0665a58938b07bd86df02edc12b3bd6e6b0e081b5` |
| southeast | `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-southeast-village-service-edge-01.png` | `49db1b5eaf41ae2c1c5c28efc96ec548eeb09155cbf686f5657885b56a477547` |
| downstream | `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-downstream-creek-basin-01.png` | `35836fa82d42942bdd5393c224e3beec7da12b2ce140b7ee2a31f5420f960ce3` |
| southwest | `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-southwest-stone-bank-return-01.png` | `6be0758b5ed1af4452924a9e7f1df06fe8484f32eef187bed44d402a444a1f76` |
| west | `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-west-uphill-forest-loop-01.png` | `0491b126697f0713c6ec675749d97a20291a23bc7cb1f0eb5f507aa434b79e49` |
| northwest | `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-northwest-flume-ridge-01.png` | `a76097052687970b945a8326427923f457055b710cf218d96de63437f4633a4f` |

### Task 1: Prepare private prompt and binding layout

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/prompts/*.prompt.txt`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/source-bindings.json`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/rejected/`

- [ ] **Step 1: Confirm private target is ignored**

Run:

```powershell
git check-ignore -v .nantai-studio/synthetic-village/hybrid-v4-candidates/batch24
```

Expected: `/.nantai-studio/`.

- [ ] **Step 2: Verify all eight source bytes**

Run `Get-FileHash -Algorithm SHA256` for every source path in the binding table.
Expected: every lowercase digest exactly matches its row.

- [ ] **Step 3: Create private directories**

Create `batch24/prompts` and `batch24/rejected` without deleting any Batch23
source.

- [ ] **Step 4: Write sixteen exact prompt files**

Each file is the shared contract, its exact mode contract and its exact role
block, separated by one blank line. Use the asset ID from the exact role table
as the filename stem and append `.prompt.txt` under `prompts/`.

- [ ] **Step 5: Write `source-bindings.json`**

Use schema version `1`, batch ID
`synthetic-village-design-inputs-batch24-2026-07-23`, and sixteen rows. Every
row contains `asset_id`, `kind`, `sector`, workspace-relative `source_file` and
`source_sha256`. Both roles in one sector bind to the same Batch23 source.

- [ ] **Step 6: Verify layout**

Run a Python check that asserts `16` prompt files, `16` binding rows, `8`
distinct source paths, exact source hashes and no duplicate asset IDs.

### Task 2: Generate eight reciprocal inbound assets

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/reciprocal-*.png`

For each step call built-in `image_gen` once with the exact prompt and
`referenced_image_paths` containing only the exact absolute Batch23 source
path. Copy the returned generated image from `$CODEX_HOME/generated_images/`
to the exact candidate path and verify it is non-empty.

- [ ] **Step 1:** Generate `reciprocal-upstream-creek-valley-inbound-01.png`.
- [ ] **Step 2:** Generate `reciprocal-northeast-forest-terrace-inbound-01.png`.
- [ ] **Step 3:** Generate `reciprocal-east-orchard-route-inbound-01.png`.
- [ ] **Step 4:** Generate `reciprocal-southeast-service-edge-inbound-01.png`.
- [ ] **Step 5:** Generate `reciprocal-downstream-creek-basin-inbound-01.png`.
- [ ] **Step 6:** Generate `reciprocal-southwest-stone-bank-inbound-01.png`.
- [ ] **Step 7:** Generate `reciprocal-west-uphill-forest-inbound-01.png`.
- [ ] **Step 8:** Generate `reciprocal-northwest-flume-ridge-inbound-01.png`.

### Task 3: Generate eight section-closure assets

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/section-*.png`

Use one built-in imagegen reference-edit per exact prompt and exact paired
source. After every call, verify that the exact destination path exists and
that `(Get-Item -LiteralPath $destination).Length` is greater than zero.

- [ ] **Step 1:** Generate `section-upstream-flume-creek-support-01.png`.
- [ ] **Step 2:** Generate `section-northeast-terrace-drainage-01.png`.
- [ ] **Step 3:** Generate `section-east-orchard-route-cutfill-01.png`.
- [ ] **Step 4:** Generate `section-southeast-service-yard-drainage-01.png`.
- [ ] **Step 5:** Generate `section-downstream-tailwater-floodbench-01.png`.
- [ ] **Step 6:** Generate `section-southwest-bridge-bank-foundation-01.png`.
- [ ] **Step 7:** Generate `section-west-forest-loop-retaining-01.png`.
- [ ] **Step 8:** Generate `section-northwest-flume-ridge-support-01.png`.

### Task 4: Original-resolution visual QA

**Files:**

- Inspect: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/*.png`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/qa-results.json`

- [ ] **Step 1: Verify PNG count, decode and dimensions**

Use Pillow to assert exactly sixteen top-level PNGs, all decodable, with width
and height each at least `900` and the longer dimension at least `1200`.

- [ ] **Step 2: Inspect every original with `view_image`**

For every row record the eleven fields from design section 8. A reciprocal
asset must have a different foreground and camera position from its source.
A section asset must show the applicable footing, drainage/water path and
terrain contact.

- [ ] **Step 3: Reject and replace defects**

For the first rejected variant, move the image to
`rejected/` with `-rejected-1.png` appended to its asset ID; increment the
integer for later rejected variants of that role. Issue a new reference-edit
call with the unchanged exact prompt plus one correction sentence naming only
the observed defect. Reinspect the replacement.

- [ ] **Step 4: Write `qa-results.json`**

Use schema version `1`, one row per accepted asset, booleans for every QA field,
`decision: "accept"`, and `rejected_variant_count`.

- [ ] **Step 5: Confirm final cardinality**

Expected: sixteen accepted top-level PNGs and sixteen accepted QA rows.

### Task 5: Build manifest, usage note and payload checksums

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/manifest.json`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/USAGE.md`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch24/PAYLOAD-SHA256SUMS.txt`

- [ ] **Step 1: Create manifest**

Use schema version `1` with batch ID, generator, `asset_count: 16`,
`prompt_count: 16`, Release tag/archive, the exact trust object from the design
and one asset row with actual `file`, `prompt`, `kind`, `sector`, `role`,
`source_file`, `source_sha256`, `width`, `height`, `bytes` and lowercase
`sha256`.

- [ ] **Step 2: Write `USAGE.md`**

State that the pack contains eight source-bound reciprocal design views and
eight source-bound closure studies. Explain that source binding is not camera
calibration or geometry correspondence, and require rebuilt Blender geometry
plus fresh reciprocal/seam/render gates after consumption.

- [ ] **Step 3: Generate payload checksums**

Include the sixteen accepted PNGs, sixteen prompt files, `manifest.json` and
`USAGE.md`; exclude the private `source-bindings.json`, private
`qa-results.json` and `PAYLOAD-SHA256SUMS.txt` itself. Sort relative POSIX
paths. Source bindings and QA facts needed by public consumers are already
embedded in `manifest.json`.

- [ ] **Step 4: Round-trip verify**

Recompute all listed hashes and require zero mismatch.

### Task 6: Assemble and publish clean Release

**Files:**

- Create: `.nantai-studio/release-staging/batch24-reciprocal-perimeter-section/`
- Create: `.nantai-studio/release-staging/synthetic-village-reciprocal-perimeter-section-pack-batch24-2026-07-23.zip`
- Create: `.nantai-studio/release-staging/synthetic-village-reciprocal-perimeter-section-pack-batch24-2026-07-23.SHA256SUMS.txt`

- [ ] **Step 1: Copy only allowed payloads**

Copy the sixteen PNGs, sixteen prompts, `manifest.json`, `USAGE.md` and
`PAYLOAD-SHA256SUMS.txt`. Do not publish `source-bindings.json`,
`qa-results.json`, rejected variants or Batch23 source PNGs. The public
manifest contains the binding facts needed by consumers.

- [ ] **Step 2: Assert staging shape**

Expected: `16` PNGs, `16` prompts, three top-level metadata files and no
`rejected`, contact sheet, queue, `source-bindings.json` or `qa-results.json`.

- [ ] **Step 3: Build and verify ZIP**

Use `Compress-Archive`, compute lowercase ZIP SHA-256, extract to a separate
private verification directory and rerun `PAYLOAD-SHA256SUMS.txt`.

- [ ] **Step 4: Publish GitHub Release**

Run:

```powershell
gh release create synthetic-village-design-inputs-batch24-2026-07-23 `
  .nantai-studio/release-staging/synthetic-village-reciprocal-perimeter-section-pack-batch24-2026-07-23.zip `
  .nantai-studio/release-staging/synthetic-village-reciprocal-perimeter-section-pack-batch24-2026-07-23.SHA256SUMS.txt `
  --repo taomic2035/nantai-3d `
  --title "Synthetic Village Batch 24 Reciprocal Perimeter and Section Inputs" `
  --notes "16 source-bound synthetic design-only inputs. Source binding is not camera calibration or multiview geometry; forbidden as SfM/NeRF/3DGS training evidence."
```

Expected: exact tag URL and two uploaded assets.

### Task 7: Document, verify, commit and push

**Files:**

- Create: `handoff/FEEDBACK-IMAGE2-028-batch24-reciprocal-perimeter-section.md`
- Modify: `README.md`

- [ ] **Step 1: Write feedback document**

Record Release URL/tag, archive bytes/SHA, every PNG's dimensions/bytes/SHA,
source binding, original-resolution QA, rejected count, Blender consumption
order and the exact trust boundary.

- [ ] **Step 2: Update README**

Add Batch24 download, archive verification and extraction commands. State that
the pack improves reciprocal perimeter and vertical closure design but cannot
prove 360-degree reconstruction or arbitrary-coordinate navigation.

- [ ] **Step 3: Verify**

Run candidate manifest/binding/hash checks, Release extraction/hash checks,
`git diff --check`, remote Release inspection and `git status --short`.

- [ ] **Step 4: Commit with path-limited staging**

```powershell
git add -- README.md handoff/FEEDBACK-IMAGE2-028-batch24-reciprocal-perimeter-section.md
git commit -m "docs(scene): publish Batch24 reciprocal inputs" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- `
  README.md handoff/FEEDBACK-IMAGE2-028-batch24-reciprocal-perimeter-section.md
```

Expected: only those two tracked paths.

- [ ] **Step 5: Push main**

Run `git push origin main`. If GitHub transiently fails, wait five seconds and
retry without force-pushing or rewriting commits.
