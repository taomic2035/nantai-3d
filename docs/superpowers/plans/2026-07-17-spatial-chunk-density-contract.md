# Spatial Chunk Density Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the spatial-reconstruction viewer consume declared per-LOD density instead of silently treating LOD labels as fixed 8/30/100-percent meanings.

**Architecture:** Keep the existing AABB/distance scheduler and both rendering paths. Normalize optional `lod_fractions` at the scheduler boundary, attach the declared density and an evidence-backed point-count estimate to each request, then expose only complete estimates through layer state and the existing Viewer bridge/HUD. Missing or invalid evidence remains unknown and never prevents legacy geometry from loading.

**Tech Stack:** Browser-native JavaScript modules, Node.js `node:test`, Three.js/Spark viewer layers.

---

### Task 1: Consume the manifest density declaration

**Files:**
- Modify: `web/viewer/spatial-reconstruction.test.mjs`
- Modify: `web/viewer/spatial-reconstruction.mjs`

- [ ] **Step 1: Write the failing scheduler tests**

Add `lod_fractions` and `point_count` to the valid fixture. Assert that auto-selected requests contain the exact declared `lodFraction` and `estimatedPointCount`, using `Math.ceil(point_count * lodFraction)`. Add a second assertion that a missing, non-finite, non-positive, or greater-than-one fraction yields `null` for both derived fields without rejecting an otherwise valid legacy manifest.

- [ ] **Step 2: Run the scheduler test and verify RED**

Run:

```powershell
node --test web/viewer/spatial-reconstruction.test.mjs
```

Expected: FAIL because selected requests do not yet expose `lodFraction` or `estimatedPointCount`.

- [ ] **Step 3: Add the minimal normalization and derivation**

Add exported helpers that return a finite fraction in `(0, 1]` or `null`, and return a safe estimated count only when both a positive safe `point_count` and a declared fraction exist. Include both values in each result from `selectSpatialChunkRequests`; do not invent defaults and do not make optional metadata a manifest-validity requirement.

- [ ] **Step 4: Run the scheduler test and verify GREEN**

Run:

```powershell
node --test web/viewer/spatial-reconstruction.test.mjs
```

Expected: all scheduler tests PASS.

- [ ] **Step 5: Commit and push the scheduler contract**

Stage only the two Viewer scheduler paths and commit with:

```text
feat(viewer): consume declared chunk density

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

Then push `main`.

### Task 2: Surface only complete active-density evidence

**Files:**
- Modify: `web/viewer/splat-chunks-layer.test.mjs`
- Modify: `web/viewer/splat-chunks-layer.mjs`
- Modify: `web/viewer/spatial-point-layer.test.mjs`
- Modify: `web/viewer/spatial-point-layer.mjs`
- Modify: `web/viewer/index-contract.test.mjs`
- Modify: `web/viewer/main.js`

- [ ] **Step 1: Write failing layer and HUD contract tests**

For both Spark and DC point layers, assert that state after a successful update reports `active_estimated_points` and sorted unique `active_lod_fractions`. Assert `null` for both fields when any active record lacks declared density/count evidence. In the index contract, require the HUD to read `active_estimated_points`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
node --test web/viewer/splat-chunks-layer.test.mjs web/viewer/spatial-point-layer.test.mjs web/viewer/index-contract.test.mjs
```

Expected: FAIL because layer snapshots and the HUD do not yet expose the density evidence.

- [ ] **Step 3: Store request evidence and expose a fail-closed summary**

Store `lodFraction` and `estimatedPointCount` with each active record in both layers. Snapshot an estimated total and unique densities only when every active record has complete evidence; otherwise return `null`. Append ` · ~N splats` to the spatial reconstruction HUD only when the estimate is a safe integer.

- [ ] **Step 4: Run focused and complete Viewer verification**

Run:

```powershell
node --test web/viewer/*.test.mjs
```

Expected: all Viewer tests PASS with zero failures.

- [ ] **Step 5: Commit and push the observable density state**

Stage only the six listed Viewer files and commit with:

```text
feat(viewer): report streamed chunk density

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

Then push `main`.

### Task 3: Record HANDOFF-CODEX-004 acceptance

**Files:**
- Create: `handoff/FEEDBACK-HANDOFF-CODEX-004.md`

- [ ] **Step 1: Write the acceptance evidence**

Record the existing streamed Spark/DC paths, AABB-distance scheduling, static-only fail-closed manifest validation, source-only provenance, `core_bounds` framing, and the new declared-density evidence. Include the exact fresh test command and result. State that chunk streaming does not make the reconstruction procedurally extendable and does not improve its trust level.

- [ ] **Step 2: Verify the final scoped diff**

Run:

```powershell
git status --short
git diff --check
git diff -- handoff/FEEDBACK-HANDOFF-CODEX-004.md
```

Expected: no whitespace errors; unrelated Opus WIP remains unstaged.

- [ ] **Step 3: Commit and push the handoff receipt**

Stage only the feedback file and commit with:

```text
docs(handoff): accept streamed reconstruction chunks

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

Then push `main`.
