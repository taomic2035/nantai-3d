# Synthetic Mountain Village Foundation Canary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a tracked, replaceable default synthetic-village recipe and a verified private 24-camera Blender canary containing RGB, depth, normals, instance masks, semantic masks, and calibrated cameras.

**Architecture:** Git stores only contracts, prompts, hashes, deterministic scene/camera generators, and verification code. Image2 source images, Blender binaries, `.blend` files, renders, and dataset payloads live under ignored `.nantai-studio/` or `third/`. A content-addressed manifest connects private inputs to reproducible outputs without claiming synthetic imagery is measured real-world geometry.

**Tech Stack:** Python 3.11+, Pydantic 2, NumPy PCG64, Pillow, pytest, Ruff, Blender 4.5.11 LTS portable, Cycles/Eevee compositor passes, canonical JSON and SHA-256.

---

## Scope and invariants

- This is phase 1 of the confirmed hybrid-C design. Full 180-camera rendering, video routes, COLMAP, 3DGS, revision integration, and viewer optimization follow only after the canary produces measured timing and size evidence.
- The fictional scene is 700 m × 500 m with 120 m relief, right-handed Z-up meters, 60–80 buildings, three clusters, twelve 4×3 spatial cells, two bridges, creek, pond, fields, orchard, bamboo, paths, courtyards, and props.
- Canary output is 24 cameras at 1024×576: 8 outer/elevated, 8 ground route, 4 courtyard/intersection, and 4 bridge/detail. Split is 18 train, 4 validation, 2 test.
- Every camera emits RGB, linear metric depth, world normals, instance mask, semantic mask, intrinsics, and OpenCV camera-to-world extrinsics.
- Blender conversion is `c2w_blender = c2w_opencv @ diag(1, -1, -1, 1)`.
- Generated payloads are synthetic verification level L2. They cannot prove real-world metric fidelity, real capture coverage, COLMAP success, or 3DGS quality.
- Only controlled RGB/video projections may later enter the existing B1 `input/` boundary. Depth, normals, masks, materials, and `.blend` files never enter ingest input.
- Never commit `.nantai-studio/`, image2 PNGs, Blender binaries, `.blend`, EXR, rendered masks, generated video, or PLY payloads.
- Every Codex commit must end with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.

## Tracked deliverables

```text
.gitignore
assets/default-resources/synthetic-mountain-village-v1.json
assets/default-resources/synthetic-mountain-village-visual-slots-v1.json
tools.lock.json
pipeline/synthetic_village/__init__.py
pipeline/synthetic_village/contracts.py
pipeline/synthetic_village/defaults.py
pipeline/synthetic_village/tool_lock.py
pipeline/synthetic_village/visual_sources.py
pipeline/synthetic_village/scene_plan.py
pipeline/synthetic_village/camera_plan.py
pipeline/synthetic_village/canary.py
pipeline/synthetic_village/verify.py
scripts/setup_synthetic_tools.py
scripts/synthetic_village.py
scripts/blender/build_synthetic_village.py
scripts/blender/render_synthetic_village.py
tests/test_synthetic_village_contracts.py
tests/test_synthetic_village_tool_lock.py
tests/test_synthetic_village_visual_sources.py
tests/test_synthetic_village_scene_plan.py
tests/test_synthetic_village_camera_plan.py
tests/test_synthetic_village_canary.py
tests/test_synthetic_village_verify.py
docs/verification/2026-07-15-synthetic-village-canary.md
```

## Private runtime layout

```text
.nantai-studio/synthetic-village/hybrid-v3/visual-sources/
.nantai-studio/synthetic-village/hybrid-v3/work/canary/
.nantai-studio/synthetic-village/hybrid-v3/dataset/canary/
.nantai-studio/cache/tools/
third/blender/
```

### Task 1: Protect private runtime data

**Files:**

- Modify: `.gitignore`
- Test: Git command contract

- [ ] Add `/.nantai-studio/` under generated/runtime outputs without changing existing ignore rules.
- [ ] Verify the private root is ignored:

```powershell
New-Item -ItemType Directory -Force .nantai-studio/synthetic-village | Out-Null
New-Item -ItemType File -Force .nantai-studio/synthetic-village/probe.bin | Out-Null
git check-ignore -v .nantai-studio/synthetic-village/probe.bin
git status --short
```

- [ ] Require `git check-ignore` to point at `/.nantai-studio/` and require no private probe in `git status`.
- [ ] Commit only `.gitignore` with the required Codex trailer.

### Task 2: Define the tracked default resource and visual slot contracts

**Files:**

- Create: `pipeline/synthetic_village/__init__.py`
- Create: `pipeline/synthetic_village/contracts.py`
- Create: `pipeline/synthetic_village/defaults.py`
- Create: `assets/default-resources/synthetic-mountain-village-v1.json`
- Create: `assets/default-resources/synthetic-mountain-village-visual-slots-v1.json`
- Create: `tests/test_synthetic_village_contracts.py`

- [ ] Write failing tests requiring frozen Pydantic models for `SceneExtent`, `ElementBudget`, `CameraProfile`, `VisualSlot`, and `DefaultResourceRecipe`.
- [ ] Require seed `20260715`, extent `700/500/120`, building range `60..80`, three clusters, twelve cells, canary `24 @ 1024×576`, and full target `180 @ 2048×1152`.
- [ ] Require exactly 68 visual slots: 16 key views, 24 materials, 12 details, 8 environment references, and 8 props.
- [ ] Require every slot to have a stable slug, category, intended use, standalone complete prompt, synthetic flag, replacement contract, and no real place identity.
- [ ] Require these canary-critical slots:

```text
key-view-creekside-entrance-01
key-view-central-courtyard-01
key-view-upper-switchback-01
key-view-opposite-slope-01
material-rammed-earth-01
material-pale-plaster-01
material-gray-roof-tile-01
material-fieldstone-01
detail-timber-door-01
environment-stone-bridge-01
prop-water-jar-01
```

- [ ] Add a test that rejects `.png`, `.jpg`, `.webp`, `.blend`, `.exr`, `.ply`, or video files beneath `assets/default-resources/`.
- [ ] Implement canonical JSON loading and fail-closed validation with `extra="forbid"`.
- [ ] Run:

```powershell
python -m pytest tests/test_synthetic_village_contracts.py -q
python -m ruff check pipeline/synthetic_village tests/test_synthetic_village_contracts.py
```

### Task 3: Pin and install Blender safely

**Files:**

- Create: `tools.lock.json`
- Create: `pipeline/synthetic_village/tool_lock.py`
- Create: `scripts/setup_synthetic_tools.py`
- Create: `tests/test_synthetic_village_tool_lock.py`
- Modify: `third/README.md`

- [ ] Write failing tests for HTTPS-only tool URLs, exact SHA-256, zip-slip rejection, missing `blender.exe`, and absent-destination publication.
- [ ] Lock Blender 4.5.11 LTS portable:

```text
URL: https://download.blender.org/release/Blender4.5/blender-4.5.11-windows-x64.zip
SHA-256: e11d3a8e4d4249be5a7db4a9325c1f670037d4233467c3b0bda181001efe44d3
```

- [ ] Implement `blender --archive`, `blender --download`, and `blender --verify-only` modes.
- [ ] Download only the locked URL to `.nantai-studio/cache/tools/<sha256>.zip`, verify before extraction, reject archive paths escaping the staging directory, and publish only to an absent `third/blender/` destination.
- [ ] Verify runtime identity with:

```powershell
third/blender/blender.exe --background --factory-startup --version
```

- [ ] Record the exact output in the verification document; do not describe Blender as installed before this command succeeds.

### Task 4: Import image2 sources into a content-addressed private pack

**Files:**

- Create: `pipeline/synthetic_village/visual_sources.py`
- Create: `scripts/synthetic_village.py`
- Create: `tests/test_synthetic_village_visual_sources.py`

- [ ] Write failing tests for declared-slot enforcement, image suffix allowlist, SHA-256 naming, duplicate reuse, canonical manifest order, absent-destination publication, and unknown model identity when the built-in interface does not expose one.
- [ ] Define `VisualSourceRecord` with slot, category, private portable path, SHA-256, complete prompt, generator interface, actual model ID, reference hashes, and `synthetic=true`.
- [ ] Copy sources into `.nantai-studio/synthetic-village/hybrid-v3/visual-sources/objects/<sha256>.<suffix>`; never preserve absolute paths in the manifest.
- [ ] Import the existing images into declared key-view slots:

```powershell
python scripts/synthetic_village.py import-visual `
  --slot key-view-establishing-small-01 `
  --source input/mock-village/image2-2026-07-15-v1/01-establishing-elevated.png `
  --source-manifest input/mock-village/image2-2026-07-15-v1/synthetic-manifest.json

python scripts/synthetic_village.py import-visual `
  --slot key-view-establishing-expanded-01 `
  --source input/mock-village/image2-2026-07-15-v2/01-establishing-expanded-v2.png `
  --source-manifest input/mock-village/image2-2026-07-15-v2/synthetic-manifest.json
```

- [ ] Require the imported digests to remain:

```text
38bf5807b5f36f1e2861dab677db2960c6addc4013dde8af12aba9932d5a767a
75e9dda41978e9ff9ce04da7269d52a40d6d2e40961559e337f9c9fc76d7dcbf
```

- [ ] When built-in image2 is available, generate one asset per remaining slot, inspect it, then import it with the exact prompt and any reference-image hashes. Never substitute another image model silently.

### Task 5: Generate a deterministic scene plan

**Files:**

- Create: `pipeline/synthetic_village/scene_plan.py`
- Create: `tests/test_synthetic_village_scene_plan.py`

- [ ] Write failing tests for seed determinism, stable object ordering, globally unique IDs, 70 default buildings, three cluster budgets `22/28/20`, twelve populated spatial cells, two bridges, creek, pond, paths, fields, orchard, bamboo, and props.
- [ ] Use NumPy `Generator(PCG64(seed))`; do not use module-global randomness.
- [ ] Use cluster centers in scene meters:

```text
creekside: (-180, -90)
central:   (0, 0)
upper:     (170, 115)
```

- [ ] Implement an analytic terrain-height function shared by placement and camera validation.
- [ ] Place buildings with rejection sampling, minimum center separation 8 m, terrain/platform fit, and a hard 10,000-attempt failure limit.
- [ ] Emit stable IDs such as `building-central-001`, semantic class, instance ID, transform, dimensions, material family, spatial cell, and cluster.
- [ ] Keep the scene plan pure Python and JSON-serializable so tests do not require Blender.
- [ ] Run:

```powershell
python -m pytest tests/test_synthetic_village_scene_plan.py -q
```

### Task 6: Generate calibrated 24-camera coverage

**Files:**

- Create: `pipeline/synthetic_village/camera_plan.py`
- Create: `tests/test_synthetic_village_camera_plan.py`

- [ ] Write failing tests for exactly 24 cameras, unique IDs, profile counts `8/8/4/4`, split counts `18/4/2`, finite rigid transforms, positive focal lengths, and deterministic bytes.
- [ ] Use only 55°, 65°, or 75° horizontal fields of view.
- [ ] Keep every optical center at least 1.4 m above analytic terrain and outside building volumes.
- [ ] Require validation/test optical centers to be at least 8 m from every training center.
- [ ] Require each of the twelve spatial cells to be seen by at least two cameras and each cluster by at least six.
- [ ] Store intrinsics plus OpenCV camera axes `+X right, +Y down, +Z forward` in camera-to-world form.
- [ ] Test the Blender conversion matrix and reject reflections or non-rigid transforms.
- [ ] Run:

```powershell
python -m pytest tests/test_synthetic_village_camera_plan.py -q
```

### Task 7: Build the Blender scene non-interactively

**Files:**

- Create: `pipeline/synthetic_village/canary.py`
- Create: `scripts/blender/build_synthetic_village.py`
- Create: `tests/test_synthetic_village_canary.py`

- [ ] Write failing host-side tests requiring `shell=False`, fixed argv, private working paths, timeout, captured logs, and failure on missing build report.
- [ ] Keep the Blender-runtime script limited to Python standard library, `bpy`, and `mathutils`.
- [ ] Parse arguments only after `--`, load strict scene/camera JSON, clear the factory scene, and build terrain, water, paths, terraces, fields, buildings, roofs, bridges, vegetation, and props.
- [ ] Name Blender objects `nv__<stable-id>` and assign stable instance/pass indices and semantic material slots.
- [ ] Create cameras from the tested OpenCV-to-Blender conversion and save the camera matrix back into the build report.
- [ ] Save `village-canary.blend` and `build-report.json` under the private work directory via temporary files followed by absent-destination publication.
- [ ] Run:

```powershell
python scripts/synthetic_village.py build-canary
```

- [ ] Use Computer Use only to inspect representative geometry visually; do not manually edit the canonical `.blend`.

### Task 8: Render resumable multi-layer frames

**Files:**

- Create: `scripts/blender/render_synthetic_village.py`
- Extend: `pipeline/synthetic_village/canary.py`
- Extend: `tests/test_synthetic_village_canary.py`

- [ ] Write failing tests for journal states `planned/rendering/verified/failed`, verified-frame reuse, partial-frame rerender, hash mismatch quarantine, and no publication from incomplete temporary outputs.
- [ ] Configure 1024×576 deep-focus canary rendering with deterministic sampling and fixed color management.
- [ ] Render for every camera:

```text
rgb/<camera-id>.png
depth/<camera-id>.exr
normal/<camera-id>.exr
instance/<camera-id>.png
semantic/<camera-id>.png
cameras/<camera-id>.json
```

- [ ] Require depth to be linear metric camera distance, normals to be finite world-space vectors, instance IDs to match the scene plan, and semantic IDs to match the tracked taxonomy.
- [ ] Render each camera into a temporary directory, hash and validate all six outputs, then publish the camera atomically.
- [ ] On resume, skip a camera only if all expected files and recorded hashes verify.
- [ ] Run:

```powershell
python scripts/synthetic_village.py render-canary
```

### Task 9: Verify and publish the canary dataset

**Files:**

- Create: `pipeline/synthetic_village/verify.py`
- Create: `tests/test_synthetic_village_verify.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `make.py`

- [ ] Write failing tests for missing layers, wrong dimensions, empty masks, NaN/negative depth, reflected cameras, duplicate RGB hashes, invalid portable paths, and incomplete scene coverage.
- [ ] Require exactly 24 verified cameras and six files per camera.
- [ ] Require scene counts: 70 buildings, three clusters, twelve cells, two bridges, defined routes, creek, pond, fields, orchard, bamboo, and props.
- [ ] Require each major building instance to appear in at least three instance masks and every semantic class to appear in at least one frame.
- [ ] Require validation/test center separation and camera rigidity using the same tested contract as plan generation.
- [ ] Derive dataset ID `synthetic-dataset-<sha256-prefix>` from canonical manifest bytes excluding timestamps and absolute paths.
- [ ] Publish only to absent `.nantai-studio/synthetic-village/hybrid-v3/dataset/canary/<dataset-id>/`; reuse an identical existing dataset, reject any mismatch.
- [ ] Add commands:

```powershell
python scripts/synthetic_village.py verify-canary
python scripts/synthetic_village.py publish-canary
python make.py synthetic-village-canary
```

### Task 10: Record measured evidence and close the phase

**Files:**

- Create: `docs/verification/2026-07-15-synthetic-village-canary.md`

- [ ] Record exact Blender version output, OS/GPU/CPU context, image2 source hashes, recipe hash, scene/camera-plan hashes, frame/layer counts, total bytes, build time, render time, verification time, and coverage metrics.
- [ ] Inspect at least four representative RGB frames plus their depth, normal, instance, and semantic layers using Computer Use or local image inspection.
- [ ] State explicitly that the evidence proves only deterministic synthetic L2 generation and does not prove COLMAP registration, real capture quality, 3DGS metrics, or viewer performance.
- [ ] Run final gates:

```powershell
python -m pytest tests/ -q
python make.py test
python make.py lint
python scripts/synthetic_village.py verify-canary
git diff --check
git status --short
```

- [ ] Confirm no generated/private binary appears in `git status` or `git ls-files`.
- [ ] Commit tracked code, contracts, prompts, tests, and evidence with path-scoped staging and the required Codex trailer.

## Canary completion gate

The phase is complete only when all of the following are simultaneously true:

1. The tracked default recipe and all 68 visual slots validate.
2. The two existing image2 assets are present in the private content-addressed source pack with their known hashes.
3. Blender 4.5.11 LTS identity and archive checksum are verified.
4. The deterministic scene contains the required scale and element counts.
5. All 24 cameras and all six output layers per camera verify.
6. The private dataset publishes under a content-derived ID and reruns reuse identical verified outputs.
7. Full project tests and lint pass, or any unrelated failure is recorded with exact evidence and ownership without being misreported as a canary success.
8. No private/generated payload is tracked by Git.

After this gate, write the measured phase-2 plan for 180 cameras and three video routes. Do not guess its render concurrency, storage layout, or timeouts before the canary supplies real measurements.
