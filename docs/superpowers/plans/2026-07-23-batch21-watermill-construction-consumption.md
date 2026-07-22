# Batch 21 Watermill Construction Consumption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the accepted exact-218 watermill role visibly consume the existing wheel assembly through plan-bound placement, specialized service geometry and a wheel-aware standing-eye composition.

**Architecture:** Add one canonical waterwheel assembly anchor to the environment plan, derive reciprocal placement/camera composition from that anchor, and specialize the existing seven watermill part meshes without adding roots or changing semantic identities. Every identity change flows through existing canonical SHA bindings and unchanged Blender probes.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, Ruff, Blender 4.5.11 headless, existing Nantai six-layer/Phase 4.3 callers.

---

### Task 1: Bind the existing environment waterwheel to a canonical anchor

**Files:**
- Modify: `pipeline/synthetic_village/environment_module.py`
- Modify: `scripts/blender/apply_environment_modules.py`
- Test: `tests/test_synthetic_village_environment_module.py`
- Test: `tests/test_synthetic_village_environment_module_runtime.py`

- [ ] **Step 1: Write the failing plan test**

Add a test asserting that the default lower-bridge recipe serializes
`waterwheel_assembly_anchor_m == (-185.2, -115.0, 43.15)` and rejects `nan`,
`inf`, booleans and non-3-tuples through strict Pydantic validation.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m pytest tests/test_synthetic_village_environment_module.py -k waterwheel_assembly_anchor -q
```

Expected: FAIL because `LowerBridgeRecipe` has no anchor field.

- [ ] **Step 3: Add the minimal plan field**

Add a strict finite tuple field and populate it in `_default_lower_bridge_recipe`:

```python
waterwheel_assembly_anchor_m: tuple[_FiniteFloat, _FiniteFloat, _FiniteFloat]

waterwheel_assembly_anchor_m=(-185.2, -115.0, 43.15),
```

- [ ] **Step 4: Write the failing Blender-runtime anchor test**

Load `apply_environment_modules.py` with `bpy` stubbed. Build
`waterwheel-wheel-001` twice from recipe dictionaries whose anchors differ by
`(+10, +20, +30)`. Assert every corresponding vertex differs by that vector.

- [ ] **Step 5: Run the runtime test and verify RED**

Run:

```powershell
python -m pytest tests/test_synthetic_village_environment_module_runtime.py -k waterwheel_geometry_uses_plan_anchor -q
```

Expected: FAIL because `_bridge_geometry` still hard-codes world coordinates.

- [ ] **Step 6: Make environment geometry anchor-relative**

Change `_bridge_geometry(part_id, recipe)` to read the exact anchor and express
the six waterwheel part centers as offsets from it. Pass the module recipe from
`_module_geometry` without changing the 45-root registry or material bindings.

- [ ] **Step 7: Verify GREEN**

Run both focused test files. Expected: all tests pass.

### Task 2: Derive watermill service placement and camera composition from the anchor

**Files:**
- Modify: `pipeline/synthetic_village/reciprocal_route_module.py`
- Test: `tests/test_synthetic_village_reciprocal_route_module.py`
- Test: `tests/test_synthetic_village_reciprocal_route_module_runtime.py`

- [ ] **Step 1: Write failing anchor propagation tests**

Assert that the watermill recipe copies the bound environment anchor, all seven
part centers are deterministic offsets from it, their XY centers are not
collinear, and their instance segment remains exactly `189..195`.

- [ ] **Step 2: Write the failing camera composition test**

Call `_role_camera_geometry` with an explicit `composition_points` tuple and
assert the watermill look envelope contains the wheel XY. Assert an empty tuple
preserves the existing result for a non-watermill fixture.

- [ ] **Step 3: Run tests and verify RED**

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_module.py tests/test_synthetic_village_reciprocal_route_module_runtime.py -k "watermill and (anchor or composition or layout)" -q
```

Expected: FAIL because the environment plan is not passed into `_default_module`
and camera envelopes accept no composition points.

- [ ] **Step 4: Implement anchor-relative layout and target**

Pass `environment_module_plan` through `_default_module` and
`_default_part_layout`. Derive the seven XY/yaw entries from the lower-bridge
anchor, propagate it into `WatermillTailraceRecipe`, and include only that point
when building the watermill role camera.

- [ ] **Step 5: Verify GREEN and frozen identities**

Run the two focused test files. Confirm root instances/semantic IDs are still
exact and all non-watermill camera tests remain unchanged.

### Task 3: Replace generic watermill meshes with construction-specific geometry

**Files:**
- Modify: `scripts/blender/apply_reciprocal_route_modules.py`
- Test: `tests/test_synthetic_village_reciprocal_route_module_runtime.py`

- [ ] **Step 1: Write failing part-geometry tests**

For each part ID `watermill-building-shell-001` through
`watermill-tailrace-retaining-wall-001`, call `_module_geometry` and assert its
expected structural signature: open wheel bay, platform supports, five distinct
tread elevations, bearing/axle cylinders or faceted rings, guard posts/rails and
tailrace opening. Assert every payload differs from its generic family payload.

- [ ] **Step 2: Run the seven tests and verify RED**

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_module_runtime.py -k batch21_watermill -q
```

Expected: FAIL because dispatch is family-only.

- [ ] **Step 3: Implement deterministic specialized builders**

Extend `MeshAssembler` with only the faceted cylinder/ring primitives required
by the bearing; use existing local transforms for boxes. Dispatch by exact
watermill part ID before falling back to `geometry_family`. Do not add Blender
objects, roots, materials or unbound image files.

- [ ] **Step 4: Verify GREEN and refactor**

Run the focused runtime tests, then the full environment/reciprocal module test
set. Keep the specialized dispatch table and builder names explicit.

### Task 4: Rebuild and run unchanged machine gates

**Files:**
- Private artifacts only: `.nantai-studio/synthetic-village/hybrid-v4/work/`
- Private batch only: `.nantai-studio/sv-prod-win/reciprocal-production-batches/`

- [ ] **Step 1: Run source verification**

```powershell
python -m pytest tests/test_synthetic_village_environment_module.py tests/test_synthetic_village_environment_module_runtime.py tests/test_synthetic_village_reciprocal_route_module.py tests/test_synthetic_village_reciprocal_route_module_runtime.py tests/test_synthetic_village_reciprocal_route_probe.py tests/test_synthetic_village_reciprocal_production.py -q
python -m ruff check pipeline/synthetic_village/environment_module.py pipeline/synthetic_village/reciprocal_route_module.py scripts/blender/apply_environment_modules.py scripts/blender/apply_reciprocal_route_modules.py tests/test_synthetic_village_environment_module.py tests/test_synthetic_village_environment_module_runtime.py tests/test_synthetic_village_reciprocal_route_module.py tests/test_synthetic_village_reciprocal_route_module_runtime.py
```

Expected: all tests pass; Ruff exits 0.

- [ ] **Step 2: Build fresh 175-root environment scene**

Run:

```powershell
python scripts/synthetic_village.py build-environment-modules `
  --verified-v2-build ".nantai-studio/synthetic-village/hybrid-v3/work/canary/2982ebcc3bd62d3a874123a08d4ad2655f5f672e83eab946d2d3143fe8608d4f" `
  --material-bundle-root ".nantai-studio/synthetic-village/hybrid-v3/material-bundles/88e35afe5ed57b7d0187956d601b1470662aaf964f593a2fc08c543c7da2e2a3" `
  --surface-realism-profile source-consistent-multiscale-surface-v1 `
  --build-root ".nantai-studio/synthetic-village/hybrid-v4/work/environment-modules" `
  --timeout-seconds 1800
```

Do not reuse environment build
`61f70a6c1abfc861e76564220a147027d5f99c86f907295ba7598a8bc68ffca5`
because the plan and runtime SHAs changed. Expected report: exact instances
`1..175`, `45` finite module meshes, `preview-only`.

- [ ] **Step 3: Build fresh exact-218 and run Phase 4.3**

Update the private, untracked runner
`.nantai-studio/synthetic-village/hybrid-v4/work/phase43_validate_fresh_build.py`
to the fresh environment build ID, make it reconstruct the current production
plan before `run_reciprocal_route_build`, and run it with:

```powershell
python .nantai-studio/synthetic-village/hybrid-v4/work/phase43_validate_fresh_build.py
```

It calls `run_reciprocal_route_build` followed by
`run_reciprocal_route_probe`. Expected:
route `6/6`, pair `15/15`, environment `6/6`, attachment `6/6`, with unchanged
thresholds and `overall_passed=true`.

- [ ] **Step 4: Run the six-role caller**

Run against the fresh build ID printed by Step 3:

```powershell
python scripts/synthetic_village.py render-reciprocal-production `
  --reciprocal-build ".nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules/$freshBuildId" `
  --blender "third/blender/blender.exe" `
  --target "central-courtyard-downhill=camera-ground-route-010" `
  --target "bridge-deck-crossing=camera-ground-route-039" `
  --target "watermill-tailrace=camera-ground-route-010" `
  --target "covered-gallery-underpass=camera-ground-route-039" `
  --target "forest-orchard-boundary=camera-ground-route-010" `
  --target "lower-valley-uphill=camera-ground-route-039" `
  --min-valid-pixel-ratio 0.05 `
  --post-render-policy ".nantai-studio/sv-prod-win/policies/post-render-v2-b60eabd0.json" `
  --clearance-near-distance-m 2.0 `
  --min-upper-middle-near-hits 5 `
  --output-root ".nantai-studio/sv-prod-win/reciprocal-production-batches/batch21-watermill-construction-v1" `
  --timeout-seconds 1800
```

`$freshBuildId` is assigned from the exact `new build_id=` line emitted by Step
3, not selected by modification time. Expected: six accepted rows with complete
RGB/depth/normal/instance/semantic and camera evidence.

### Task 5: Visual review, evidence and publication

**Files:**
- Create: `handoff/FEEDBACK-HANDOFF-CODEX-026-batch21-watermill-construction.md`

- [ ] **Step 1: Compare the watermill RGB**

Open the fresh watermill RGB beside Batch 20 render ID `04b154a1...`. Confirm the
existing wheel is visible and the millhouse/platform/stair/tailrace relationship
is legible. Record any remaining blockout defects rather than hiding them.

- [ ] **Step 2: Validate non-RGB layers**

Read the frame report and quality report; verify the role instances remain
present in instance/semantic layers and depth/normal identities match the fresh
build. Do not infer geometry quality from RGB alone.

- [ ] **Step 3: Record exact identities**

Write plan, registry, build/report/blend, probe, batch/journal and per-frame
SHA-256 values plus the unchanged trust boundary to the handoff.

- [ ] **Step 4: Path-limit commit and push**

Stage only the files changed by this plan and commit with:

```text
feat(scene): integrate batch21 watermill construction

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

Push `main`, then compare `git ls-remote origin refs/heads/main` with local HEAD.
