# Batch 23 Environment Envelope and Structural Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate, inspect, package, publish and document sixteen replaceable design-only image inputs that close the measured Batch22 environment, support and surface-transition gaps.

**Architecture:** Built-in imagegen produces one independent PNG per approved role. Private candidate storage keeps prompts, rejected variants and QA state outside Git; a clean content-addressed Release contains only accepted PNGs, exact prompts, manifest, usage note and checksums. Documentation preserves the distinction between design references, modeled synthetic geometry and real reconstruction evidence.

**Tech Stack:** OpenAI built-in imagegen, Codex `view_image`, PowerShell, Pillow, JSON, SHA-256, ZIP, GitHub CLI, Markdown.

---

## File map

**Private, ignored inputs and working state**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/*.png`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/prompts/*.prompt.txt`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/rejected/`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/manifest.json`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/USAGE.md`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/PAYLOAD-SHA256SUMS.txt`

**Private Release staging**

- Create: `.nantai-studio/release-staging/batch23-environment-envelope-support/`
- Create: `.nantai-studio/release-staging/synthetic-village-environment-envelope-support-pack-batch23-2026-07-23.zip`
- Create: `.nantai-studio/release-staging/synthetic-village-environment-envelope-support-pack-batch23-2026-07-23.SHA256SUMS.txt`

**Tracked documentation**

- Create: `handoff/FEEDBACK-IMAGE2-027-batch23-environment-envelope-support.md`
- Modify: `README.md`

## Exact shared prompt contract

Every prompt file begins with this text:

```text
Use case: photorealistic-natural
Asset type: reusable environment and construction design input for a generic mountain-village 3D scene
Style/medium: highly detailed naturalistic environmental photography, physically plausible construction, no fantasy styling
Lighting/mood: soft diffused overcast daylight, readable material contact and structural depth, balanced exposure
Materials/textures: weathered local stone, aged timber, clay roof tile where buildings appear, soil, gravel, shallow creek water, humid mixed vegetation
Composition: clear foreground connection, readable middle-ground module, layered far-field enclosure; routes and terrain continue beyond the frame
Constraints: generic and replaceable; structurally supported primary forms; dense enough to inform modular 3D modeling; no text, labels, logos, watermark, people, animals or vehicles
Avoid: blank studio background, sealed horizon, fisheye distortion, extreme aerial view, impossible cantilevers, floating slabs, unsupported bridges, duplicated waterwheels, decorative clutter hiding the construction
```

Append exactly one role block from the following table:

| Asset ID | Exact role block |
|---|---|
| `envelope-upstream-creek-valley-01` | `Primary request: a wide upstream mountain-creek valley approaching a small water-powered village; a timber flume on visible stone-and-timber supports follows one bank, a shallow creek with embedded boulders bends through the foreground, a modest walkable crossing and path continue to both frame edges, layered forested ridges enclose the skyline; human-eye survey viewpoint, landscape composition` |
| `envelope-northeast-forest-terrace-01` | `Primary request: a wide forest-to-terrace transition in a humid mountain village; a supported switchback footpath climbs between low dry-stone retaining walls, cultivated terraces and mixed bamboo forest, drainage channels and stair connections are visible, distant roofs and ridges fill the far field; human-eye survey viewpoint, landscape composition` |
| `envelope-east-orchard-route-01` | `Primary request: a broad orchard slope and village service route; fruit trees on stepped terrain, a compacted stone-and-soil path with drainage edges, small retaining walls and a distant cluster of generic clay-tile houses, with continuous uphill and downhill route exits and layered mountains; human-eye survey viewpoint, landscape composition` |
| `envelope-southeast-village-service-edge-01` | `Primary request: the rear service edge of a generic mountain village; several small stone-and-timber service courtyards, wood storage, drainage, retaining walls and connecting paths occupy the middle ground while forest and terraced slopes enclose the horizon; no inhabitants, no isolated hero building, human-eye survey viewpoint, landscape composition` |
| `envelope-downstream-creek-basin-01` | `Primary request: a wide downstream creek basin below a village watermill; a physically readable tailwater channel joins a shallow meandering creek, with wet gravel shelf, dry flood bench, supported footbridge, bank-return paths and forested valley enclosure; no dramatic waterfall, human-eye survey viewpoint, landscape composition` |
| `envelope-southwest-stone-bank-return-01` | `Primary request: a stone-lined creek bank returning toward a mountain village; a supported bridge landing meets a dry-stone abutment, planted slope and alternate pedestrian loop, with visible foundations, drainage and continuous routes into the middle and far distance; human-eye survey viewpoint, landscape composition` |
| `envelope-west-uphill-forest-loop-01` | `Primary request: an uphill forest loop west of a generic mountain village; a stepped path with supported landings, low retaining walls, roots, drainage and bamboo or broadleaf forest curves back toward distant village roofs and ridges; foreground and background route continuity, human-eye survey viewpoint, landscape composition` |
| `envelope-northwest-flume-ridge-01` | `Primary request: a wide elevated flume and ridge relationship; a timber water channel runs on repeated braced supports above a lower creek and path, connecting to an upper hillside route while a forested ridge and scattered generic village buildings fill the skyline; show visible load paths and terrain contact, human-eye survey viewpoint, landscape composition` |
| `construction-bridge-watermill-longitudinal-support-01` | `Primary request: a close but complete longitudinal view through a small mountain-village bridge and adjacent single-waterwheel mill; show deck beams, stone piers, timber braces, axle support, maintenance platform, foundations and creek bed in one readable load path, with enough surrounding bank and route context to model connections; photorealistic construction reference, landscape framing` |
| `construction-cross-bank-foundation-01` | `Primary request: a close construction view across both sides of a shallow mountain creek; opposing stone abutments, stepped foundations, scour protection, supported bridge landing, dry and wet bed levels and walkable bank connections are all visible; physically plausible contact with terrain, photorealistic construction reference, landscape framing` |
| `construction-tailrace-creek-junction-01` | `Primary request: a detailed mountain-watermill tailrace outlet joining a natural shallow creek; show outlet masonry, channel invert, wet and dry bed levels, retaining returns, drainage, maintenance edge, gravel and embedded stone without hiding structural contact; photorealistic construction reference, landscape framing` |
| `construction-retaining-stair-orbit-support-01` | `Primary request: a detailed retaining-wall, stair and maintenance-platform assembly on sloped mountain-village terrain; stone wall foundations, timber or stone landing supports, guard edge, drainage and a continuous pedestrian loop are visible, with no floating slab or unexplained cantilever; photorealistic construction reference, landscape framing` |
| `transition-creek-bed-wet-dry-01` | `Primary request: a near-square close environmental study of a shallow mountain creek edge transitioning from clear water through submerged gravel and embedded stone to damp silt, wet moss and a dry vegetated bank; physically plausible waterline and bed shape, broad enough to inform geometry, not a seamless texture` |
| `transition-moss-stone-drainage-wall-01` | `Primary request: a near-square close environmental study of a drained dry-stone retaining wall in a humid mountain village; cap stones, irregular joints, weep paths, soil contact, moss concentration, roots and ground transition are visible; physically plausible construction, not a seamless texture` |
| `transition-timber-stone-bearing-joint-01` | `Primary request: a near-square close construction study of a weathered timber beam bearing on a stone pier beside a creek; show bearing seat, metal strap, end-grain clearance, water shedding, stone contact and adjacent support geometry; physically plausible and reusable, not a seamless texture` |
| `transition-route-soil-vegetation-01` | `Primary request: a near-square close environmental study of a mountain-village route blending from irregular stone paving into compacted soil, gravel drainage edge, grass, roots and sloped terrain; show a wide transition zone useful for geometry and material blending, not a seamless texture` |

### Task 1: Prepare private prompt and candidate layout

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/prompts/*.prompt.txt`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/rejected/`

- [ ] **Step 1: Confirm the private target is ignored**

Run:

```powershell
git check-ignore -v .nantai-studio/synthetic-village/hybrid-v4-candidates/batch23
```

Expected: an ignore rule covering `.nantai-studio`.

- [ ] **Step 2: Create the directories**

Run:

```powershell
New-Item -ItemType Directory -Force `
  .nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/prompts, `
  .nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/rejected | Out-Null
```

Expected: both directories exist under the intended workspace root.

- [ ] **Step 3: Write sixteen exact prompt files**

Use `apply_patch`. Each file is the shared prompt contract followed by its
single exact role block. File names are
`prompts/<asset-id>.prompt.txt`.

- [ ] **Step 4: Verify prompt cardinality and names**

Run:

```powershell
$prompts = Get-ChildItem `
  .nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/prompts `
  -Filter *.prompt.txt -File
if ($prompts.Count -ne 16) { throw "expected 16 prompts, got $($prompts.Count)" }
$prompts.Name | Sort-Object
```

Expected: exactly the sixteen IDs in the specification.

### Task 2: Generate eight environment-envelope assets

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/envelope-*.png`

For every step, call built-in `image_gen` once with the complete corresponding
prompt file. Save the returned generated image from
`$CODEX_HOME/generated_images/...` into the exact candidate path. Do not use a
contact sheet or one multi-panel image.

- [ ] **Step 1:** Generate and save `envelope-upstream-creek-valley-01.png`.
- [ ] **Step 2:** Generate and save `envelope-northeast-forest-terrace-01.png`.
- [ ] **Step 3:** Generate and save `envelope-east-orchard-route-01.png`.
- [ ] **Step 4:** Generate and save `envelope-southeast-village-service-edge-01.png`.
- [ ] **Step 5:** Generate and save `envelope-downstream-creek-basin-01.png`.
- [ ] **Step 6:** Generate and save `envelope-southwest-stone-bank-return-01.png`.
- [ ] **Step 7:** Generate and save `envelope-west-uphill-forest-loop-01.png`.
- [ ] **Step 8:** Generate and save `envelope-northwest-flume-ridge-01.png`.

After each call, verify the file exists and is non-empty:

```powershell
$path = ".nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/<asset-id>.png"
if (-not (Test-Path -LiteralPath $path)) { throw "missing $path" }
if ((Get-Item -LiteralPath $path).Length -eq 0) { throw "empty $path" }
```

### Task 3: Generate four structural-construction assets

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/construction-*.png`

- [ ] **Step 1:** Generate and save `construction-bridge-watermill-longitudinal-support-01.png`.
- [ ] **Step 2:** Generate and save `construction-cross-bank-foundation-01.png`.
- [ ] **Step 3:** Generate and save `construction-tailrace-creek-junction-01.png`.
- [ ] **Step 4:** Generate and save `construction-retaining-stair-orbit-support-01.png`.

Use one built-in imagegen call per exact prompt and run the same non-empty file
check after each call.

### Task 4: Generate four surface-transition assets

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/transition-*.png`

- [ ] **Step 1:** Generate and save `transition-creek-bed-wet-dry-01.png`.
- [ ] **Step 2:** Generate and save `transition-moss-stone-drainage-wall-01.png`.
- [ ] **Step 3:** Generate and save `transition-timber-stone-bearing-joint-01.png`.
- [ ] **Step 4:** Generate and save `transition-route-soil-vegetation-01.png`.

Use one built-in imagegen call per exact prompt and run the same non-empty file
check after each call.

### Task 5: Original-resolution visual QA

**Files:**

- Inspect: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/*.png`
- Move rejected files only to:
  `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/rejected/`

- [ ] **Step 1: Verify PNG count and dimensions**

Run:

```powershell
@'
from pathlib import Path
from PIL import Image

root = Path(".nantai-studio/synthetic-village/hybrid-v4-candidates/batch23")
files = sorted(root.glob("*.png"))
assert len(files) == 16, len(files)
for path in files:
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        width, height = im.size
        assert width >= 1200 and height >= 900, (path.name, im.size)
        print(path.name, width, height, path.stat().st_size)
'@ | .\.venv\Scripts\python.exe -
```

Expected: sixteen decodable PNGs, each at least `1200×900`.

- [ ] **Step 2: Inspect every image with `view_image`**

For each original image, record:

```text
role_match
foreground_middle_far
route_or_terrain_continuity
structural_support_readability
visible_text_or_watermark
people_animals_vehicles
floating_primary_structure
duplicate_waterwheel
accept_or_reject
```

Acceptance requires the first four fields to be true where applicable and the
next four defect fields to be false.

- [ ] **Step 3: Replace rejected roles**

Move a rejected image to
`rejected/<asset-id>-rejected-<n>.png`, issue a new built-in imagegen call with
the same exact prompt plus one targeted correction sentence describing only
the observed defect, and repeat Steps 1–2 for that role.

- [ ] **Step 4: Confirm final cardinality**

Run:

```powershell
$final = Get-ChildItem `
  .nantai-studio/synthetic-village/hybrid-v4-candidates/batch23 `
  -Filter *.png -File
if ($final.Count -ne 16) { throw "final PNG count is $($final.Count), expected 16" }
```

Expected: exactly sixteen accepted top-level PNGs.

### Task 6: Build manifest, usage note and payload checksums

**Files:**

- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/manifest.json`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/USAGE.md`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch23/PAYLOAD-SHA256SUMS.txt`

- [ ] **Step 1: Create the manifest**

Generate schema version 1 JSON with:

```json
{
  "batch_id": "synthetic-village-design-inputs-batch23-2026-07-23",
  "generated_with": "OpenAI built-in imagegen",
  "asset_count": 16,
  "prompt_count": 16,
  "release_tag": "synthetic-village-design-inputs-batch23-2026-07-23",
  "release_archive": "synthetic-village-environment-envelope-support-pack-batch23-2026-07-23.zip"
}
```

Add the complete trust object from the specification and one asset row per PNG
with `file`, `prompt`, `kind`, `role`, `width`, `height`, `bytes` and lowercase
`sha256`, all derived from actual bytes.

- [ ] **Step 2: Write `USAGE.md`**

State that the pack contains eight independent environment envelopes, four
construction references and four transition studies. Repeat that it is not a
calibrated multiview set or a complete PBR texture set, and require fresh
modeled-scene build and render gates after consumption.

- [ ] **Step 3: Generate payload checksums**

Include every accepted PNG, prompt, `manifest.json` and `USAGE.md`, but exclude
`PAYLOAD-SHA256SUMS.txt` itself. Paths are relative and sorted.

- [ ] **Step 4: Round-trip verify**

Run:

```powershell
Push-Location .nantai-studio/synthetic-village/hybrid-v4-candidates/batch23
Get-Content .\PAYLOAD-SHA256SUMS.txt | ForEach-Object {
  $sha, $path = $_ -split '  ', 2
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
  if ($actual -ne $sha) { throw "SHA-256 mismatch: $path" }
}
Pop-Location
```

Expected: exit `0`, no mismatch.

### Task 7: Assemble and publish the clean Release

**Files:**

- Create: `.nantai-studio/release-staging/batch23-environment-envelope-support/`
- Create: `.nantai-studio/release-staging/synthetic-village-environment-envelope-support-pack-batch23-2026-07-23.zip`
- Create: `.nantai-studio/release-staging/synthetic-village-environment-envelope-support-pack-batch23-2026-07-23.SHA256SUMS.txt`

- [ ] **Step 1: Copy only allowed payloads**

Copy the sixteen PNGs, `prompts/`, `manifest.json`, `USAGE.md` and
`PAYLOAD-SHA256SUMS.txt` to the empty staging directory. Do not copy
`rejected/`.

- [ ] **Step 2: Assert the staging shape**

Run:

```powershell
$stage = ".nantai-studio/release-staging/batch23-environment-envelope-support"
$png = Get-ChildItem $stage -Filter *.png -File
$prompts = Get-ChildItem "$stage/prompts" -Filter *.prompt.txt -File
if ($png.Count -ne 16 -or $prompts.Count -ne 16) {
  throw "unexpected release counts: png=$($png.Count) prompts=$($prompts.Count)"
}
if (Test-Path "$stage/rejected") { throw "rejected directory leaked into Release" }
```

Expected: `16` PNGs, `16` prompts, no `rejected/`.

- [ ] **Step 3: Build and verify the ZIP**

Use `Compress-Archive`, compute the ZIP SHA-256, extract it to a separate
private verification directory, and rerun `PAYLOAD-SHA256SUMS.txt`.

- [ ] **Step 4: Publish the GitHub Release**

Run:

```powershell
gh release create synthetic-village-design-inputs-batch23-2026-07-23 `
  .nantai-studio/release-staging/synthetic-village-environment-envelope-support-pack-batch23-2026-07-23.zip `
  .nantai-studio/release-staging/synthetic-village-environment-envelope-support-pack-batch23-2026-07-23.SHA256SUMS.txt `
  --repo taomic2035/nantai-3d `
  --title "Synthetic Village Batch 23 Environment Envelope and Support Inputs" `
  --notes "16 replaceable synthetic design-only inputs. Not calibrated multiview, not real textures, and forbidden as SfM/NeRF/3DGS training evidence."
```

Expected: a Release URL for the exact tag.

### Task 8: Document, verify, commit and push

**Files:**

- Create: `handoff/FEEDBACK-IMAGE2-027-batch23-environment-envelope-support.md`
- Modify: `README.md`

- [ ] **Step 1: Write the feedback document**

Record the Release URL/tag, archive name/bytes/SHA, each PNG's dimensions,
bytes, SHA and role, original-resolution QA decisions, rejected variants count,
Blender/chunk consumption guidance and the complete trust boundary.

- [ ] **Step 2: Update README**

Add a Batch23 section with download, archive SHA verification and extraction
commands. State that the images target environment enclosure and structural
modeling but cannot prove 360-degree reconstruction or arbitrary-coordinate
navigation.

- [ ] **Step 3: Verify documentation and remote Release**

Run:

```powershell
git diff --check
gh release view synthetic-village-design-inputs-batch23-2026-07-23 `
  --repo taomic2035/nantai-3d --json url,tagName,assets
git status --short
```

Expected: no whitespace errors; tag and two assets present; only the intended
tracked docs plus known unrelated private paths appear.

- [ ] **Step 4: Commit with path-limited staging**

Run:

```powershell
git add -- README.md handoff/FEEDBACK-IMAGE2-027-batch23-environment-envelope-support.md
git commit -m "docs(scene): publish Batch23 design inputs" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- `
  README.md handoff/FEEDBACK-IMAGE2-027-batch23-environment-envelope-support.md
```

Expected: one documentation commit containing only those paths.

- [ ] **Step 5: Push main**

Run:

```powershell
git push origin main
```

Expected: remote `main` advances to the new commit.
