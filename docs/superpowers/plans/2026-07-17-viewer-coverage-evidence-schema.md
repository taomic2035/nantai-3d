# Viewer Coverage Evidence Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Viewer fail closed on the current coverage-audit evidence schema and present observed normal-span evidence without calling it facade or reconstruction coverage.

**Architecture:** Keep validation and view-model derivation in `web/viewer/coverage-audit.mjs`. Extend the existing core-schema branch with pure helpers for evidence digests, camera centers, unit normals, and re-derived normal spans; do not alter fetch, bridge, Studio, or renderer code.

**Tech Stack:** Native ES modules, Node.js 20 `node:test`, browser JavaScript.

---

### Task 1: Anchor every core coverage input

**Files:**
- Modify: `web/viewer/coverage-audit.test.mjs`
- Modify: `web/viewer/coverage-audit.mjs`

- [x] **Step 1: Extend the valid core fixture**

Add the current producer fields to `coreAudit()`:

```js
build_report_sha256: '4'.repeat(64),
glb_sha256: '5'.repeat(64),
camera_metadata_digests: [{
  camera_id: 'camera-outer-001',
  path: 'cameras/camera-outer-001.json',
  sha256: '6'.repeat(64),
}],
normal_digests: [{
  camera_id: 'camera-outer-001',
  path: 'normal/camera-outer-001.exr',
  sha256: '7'.repeat(64),
}],
normal_unit_length_tolerance: 0.001,
camera_centers: [{
  camera_id: 'camera-outer-001',
  center_source: 'renders/cameras/<camera_id>.json:measured_c2w_blender',
  center_xy_m: [10, 20],
}],
```

Add `mean_unit_normal_xyz: [1, 0, 0]` to the observation and add:

```js
normal_spread: {
  semantics: 'observed-surface-normal-angular-spread-not-facade-identity',
  normal_source: 'renders/normal/<camera_id>.exr:X,Y,Z-world-space-unit-vector',
  qualifying_camera_normal_count: 1,
  observed_normal_angular_spread_deg: null,
  unknown_reason: 'fewer than two qualifying camera normals',
},
```

- [x] **Step 2: Write failing identity tests**

Add one test that deep-clones the valid fixture and independently corrupts:

```js
build_report_sha256
glb_sha256
camera_metadata_digests[0].sha256
normal_digests[0].camera_id
camera_centers[0].center_source
```

Assert `isCoverageAudit()` is false for every corrupted report and for a report
whose three digest arrays do not name the same camera set.

- [x] **Step 3: Run the test and verify RED**

Run:

```text
node --test --test-name-pattern="anchors every core coverage input" web/viewer/coverage-audit.test.mjs
```

Expected: FAIL because the existing core validator ignores the new fields.

- [x] **Step 4: Implement minimal anchor validation**

Add pure helpers that validate:

- required SHA-256 `build_report_sha256`;
- nullable SHA-256 `glb_sha256`;
- digest arrays with unique non-empty camera IDs, paths, and SHA-256 values;
- identical camera-ID sets across mask, camera-metadata, and normal digests;
- camera centers with unique IDs, exact source semantics, and two finite values;
- a GLB digest whenever any component carries azimuth evidence.

- [x] **Step 5: Run the focused and existing coverage tests**

Run:

```text
node --test web/viewer/coverage-audit.test.mjs
```

Expected: all tests pass.

### Task 2: Re-derive observed normal evidence

**Files:**
- Modify: `web/viewer/coverage-audit.test.mjs`
- Modify: `web/viewer/coverage-audit.mjs`

- [x] **Step 1: Write failing normal-evidence tests**

Add tests proving that:

- `[2, 0, 0]` is rejected under tolerance `0.001`;
- a missing or malformed `normal_spread` is rejected;
- one qualifying normal requires `angle=null` and a non-empty unknown reason;
- two orthogonal qualifying normals require count `2`, angle `90`, and
  `unknown_reason=null`;
- changing the declared angle from `90` to `89` is rejected.

- [x] **Step 2: Run the tests and verify RED**

Run:

```text
node --test --test-name-pattern="normal evidence" web/viewer/coverage-audit.test.mjs
```

Expected: FAIL because the existing validator ignores per-observation normals
and the derived span.

- [x] **Step 3: Implement minimal normal validation**

Add pure helpers that:

```js
validUnitVector(vector, tolerance)
maxPairwiseNormalAngleDeg(vectors)
validCoreNormalSpread(spread, observations, threshold, tolerance)
```

Use only qualifying observations with non-null normals. Round the maximum
pairwise angle to three decimals, matching the producer. Preserve `null` as
unknown when fewer than two vectors exist.

- [x] **Step 4: Run the coverage tests**

Run:

```text
node --test web/viewer/coverage-audit.test.mjs
```

Expected: all tests pass.

### Task 3: Present normal span without trust elevation

**Files:**
- Modify: `web/viewer/coverage-audit.test.mjs`
- Modify: `web/viewer/coverage-audit.mjs`

- [x] **Step 1: Write the failing HUD test**

Build a valid two-normal core audit and assert:

```js
assert.equal(model.layers.geometry.status, 'unknown');
assert.match(model.layers.geometry.label, /observed surface normal span 90\.0/);
assert.match(model.layers.geometry.label, /not facade identity/);
assert.doesNotMatch(model.layers.geometry.label, /front|back|360.coverage/i);
```

- [x] **Step 2: Run the test and verify RED**

Run:

```text
node --test --test-name-pattern="presents observed normal span" web/viewer/coverage-audit.test.mjs
```

Expected: FAIL because the current core HUD mentions only azimuth.

- [x] **Step 3: Implement the minimal label**

Derive the finite measured span range from `component.normal_spread`. Keep the
Geometry layer status `unknown`; append the range and the exact disclaimer
`not facade identity`. Do not turn any span into a pass/fail decision.

- [x] **Step 4: Run the complete Viewer suite**

Run:

```text
node --test web/viewer/*.test.mjs web/studio/*.test.mjs
```

Expected: all tests pass.

- [x] **Step 5: Commit and push path-limited changes**

Stage only:

```text
docs/superpowers/plans/2026-07-17-viewer-coverage-evidence-schema.md
web/viewer/coverage-audit.mjs
web/viewer/coverage-audit.test.mjs
```

Every commit must end with:

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

## Verification evidence

- `node --test web/viewer/coverage-audit.test.mjs`: 15/15 passed.
- `node --test web/viewer/*.test.mjs web/studio/*.test.mjs`: 183/183 passed.
- The current Opus audit kernel produced a 126-component, 24-frame report in
  memory; the Viewer accepted it as `diagnostic-unvalidated` and kept Geometry
  `unknown` while presenting the measured `0.3–173.8°` observed-normal range.
- The stale untracked `web/data/coverage-audit.json` lacks the new anchored
  evidence and is intentionally rejected as invalid rather than silently
  upgraded.
