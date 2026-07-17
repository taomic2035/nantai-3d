# Viewer Spatial Reconstruction Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream `kind: "spatial-chunks"` reconstruction manifests as nearby full-fidelity 3DGS chunks with distance LOD, bounded cache, DC fallback, and fail-closed provenance.

**Architecture:** `spatial-reconstruction.mjs` is the pure manifest and AABB scheduler. `splat-chunks-layer.mjs` owns one Spark renderer and many absolute-coordinate chunk meshes. `spatial-point-layer.mjs` provides the same lifecycle with DC point meshes when Spark is unavailable. `main.js` selects these layers only for an explicit spatial manifest and keeps the existing whole-reconstruction path unchanged.

**Tech Stack:** Browser ES modules, Three.js, Spark 2.1.0, Node `node:test`, existing Viewer bridge and PLY loader.

---

### Task 1: Spatial manifest contract and deterministic scheduler

**Files:**
- Create: `web/viewer/spatial-reconstruction.mjs`
- Create: `web/viewer/spatial-reconstruction.test.mjs`

- [ ] **Step 1: Write RED tests**

```js
assert.equal(isSpatialChunkManifest(validManifest), true);
assert.equal(isSpatialChunkManifest({ ...validManifest, kind: 'world' }), false);
assert.equal(isSpatialChunkManifest({ ...validManifest, grid: { on_demand: true } }), false);
assert.equal(
  resolveSpatialChunkUrl(MANIFEST_URL, validManifest.chunks[0], 2),
  'https://studio.example/data/recon-chunks/chunk_0_0.ply',
);
assert.equal(resolveSpatialChunkUrl(MANIFEST_URL, { lod: { 2: '../escape.ply' } }, 2), null);
assert.deepEqual(
  selectSpatialChunkRequests(validManifest, [25, 25, 3], { radiusChunks: 2 })
    .map(({ key, lod }) => [key, lod]),
  [['0_0', 2], ['1_0', 1], ['2_0', 0]],
);
```

- [ ] **Step 2: Verify RED**

```powershell
node --test web/viewer/spatial-reconstruction.test.mjs
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement the public API**

```js
export function isSpatialChunkManifest(manifest) {}
export function resolveSpatialChunkUrl(manifestUrl, entry, lod) {}
export function horizontalDistanceToAabb(cameraWorld, aabb) {}
export function selectSpatialChunkRequests(
  manifest,
  cameraWorld,
  { radiusChunks = 2, lodOverride = null } = {},
) {}
```

The implementation requirements are exact:

- require schema `1`, kind `spatial-chunks`, positive finite `chunk_size_m`, non-empty chunks, safe integer `x/y`, valid three-dimensional AABBs, and no `grid` key;
- choose only `entry.lod[String(lod)]`, falling back to `entry.ply_file`;
- reject absolute paths, backslashes, query/fragment, empty/dot/dot-dot segments, and percent-decoded separators;
- include AABBs within `chunk_size_m * radiusChunks`;
- assign LOD2 below `0.5 * chunk_size_m`, LOD1 below `1.5 * chunk_size_m`, otherwise LOD0;
- sort by distance then chunk key;
- never generate an API URL, apply `world_offset`, or derive provenance.

- [ ] **Step 4: Verify GREEN**

```powershell
node --test web/viewer/spatial-reconstruction.test.mjs
```

- [ ] **Step 5: Commit only these two paths with the required trailer.**

### Task 2: Multi-chunk Spark lifecycle

**Files:**
- Create: `web/viewer/splat-chunks-layer.mjs`
- Create: `web/viewer/splat-chunks-layer.test.mjs`
- Modify: `web/viewer/bridge.mjs`
- Modify: `web/viewer/bridge.test.mjs`

- [ ] **Step 1: Write RED tests**

```js
const layer = createSpatialSplatLayer({
  scene,
  renderer,
  importSpark: async () => fakeSparkModule(),
  cacheMax: 2,
});
await layer.load({ manifest: validManifest, manifestUrl: MANIFEST_URL, visible: true });
await layer.update({ cameraWorld: [25, 25, 2] });
assert.equal(scene.children.filter((item) => item.kind === 'spark-renderer').length, 1);
assert.ok(scene.children.filter((item) => item.kind === 'splat-mesh').length <= 2);
assert.equal(
  scene.children.filter((item) => item.kind === 'splat-mesh')
    .every((item) => item.position === undefined),
  true,
);
const oldMesh = scene.children.find((item) => item.options.url.endsWith('chunk_0_0.ply'));
await layer.update({ cameraWorld: [125, 25, 2], lodOverride: 0 });
assert.equal(oldMesh.disposed, true);
```

Also test import failure, mesh initialization failure, hidden state, disposal, and stale completion after a newer manifest load.

- [ ] **Step 2: Verify RED**

```powershell
node --test web/viewer/splat-chunks-layer.test.mjs
```

- [ ] **Step 3: Implement**

```js
export function createSpatialSplatLayer({
  scene,
  renderer,
  importSpark = () => import('@sparkjsdev/spark'),
  timeoutMs = 8000,
  cacheMax = 36,
}) {
  return {
    load,
    update,
    setVisible,
    dispose,
    getState,
  };
}
```

One shared `SparkRenderer` serves all chunks. Each `SplatMesh` gets only
`ENU_TO_THREE_QUATERNION`; no translation is set. Manifest and per-key
generations prevent stale completions from entering the scene. LOD replacement
disposes the old mesh after the new mesh initializes. Eviction never exceeds
`cacheMax`.

- [ ] **Step 4: Add `createViewerCapabilities("spark-chunks")`**

`spark-chunks` has full 3DGS renderer fidelity and
`lod.reconstruction_tiers = true`. Existing `spark` behavior stays unchanged.

- [ ] **Step 5: Verify and path-limited commit**

```powershell
node --test web/viewer/splat-chunks-layer.test.mjs web/viewer/bridge.test.mjs
```

### Task 3: DC point fallback with the same scheduling contract

**Files:**
- Create: `web/viewer/spatial-point-layer.mjs`
- Create: `web/viewer/spatial-point-layer.test.mjs`

- [ ] **Step 1: Write RED tests**

```js
const layer = createSpatialPointLayer({
  scene,
  cacheMax: 2,
  loadPointMesh: async ({ url }) => ({ url, visible: true, disposed: false }),
  disposeMesh: (mesh) => { mesh.disposed = true; },
});
await layer.load({ manifest: validManifest, manifestUrl: MANIFEST_URL });
await layer.update({ cameraWorld: [25, 25, 2] });
assert.ok(layer.getState().active <= 2);
layer.setVisible(false);
assert.equal(scene.children.every((mesh) => mesh.visible === false), true);
layer.dispose();
assert.equal(scene.children.length, 0);
```

Add a deferred loader test proving completion after `dispose()` is discarded
and disposed.

- [ ] **Step 2: Verify RED**

```powershell
node --test web/viewer/spatial-point-layer.test.mjs
```

- [ ] **Step 3: Implement the same `load/update/setVisible/dispose/getState` API**

The module receives point loading and disposal functions from `main.js`, so it
contains no Three.js or PLY parser duplication.

- [ ] **Step 4: Verify GREEN and path-limited commit.**

### Task 4: Viewer, bridge, provenance, and HUD integration

**Files:**
- Modify: `web/viewer/main.js`
- Modify: `web/viewer/bridge.mjs`
- Modify: `web/viewer/bridge.test.mjs`
- Modify: `web/viewer/index-contract.test.mjs`

- [ ] **Step 1: Write RED provenance tests**

```js
const known = artifactProvenance({
  kind: 'spatial-chunks',
  source: {
    frame_id: 'world-enu',
    units: 'meters',
    geometry_usability: 'metric-aligned',
  },
});
assert.equal(known.frame, 'world-enu');
assert.equal(known.units, 'meters');
assert.equal(known.geometry_usability, 'metric-aligned');
assert.equal(
  artifactProvenance({ kind: 'spatial-chunks', source: {} }).geometry_usability,
  'unknown',
);
```

- [ ] **Step 2: Add explicit artifact loading**

`loadArtifact` accepts `recon-manifest` and `chunk-manifest`. A chunk manifest
must pass `isSpatialChunkManifest`; URL loading remains same-origin. A normal
reconstruction manifest may reference a safe relative `spatial_chunks` path.
The Viewer does not guess a sibling path.

- [ ] **Step 3: Wire runtime selection**

For spatial chunks, dispose the whole-reconstruction layer, start the Spark
chunk layer, and activate the point layer only when Spark returns a truthful
fallback result. Every 50 ms, call the active reconstruction chunk layer with
`threeToWorld(camera.position)` and the current LOD override. Visibility, HUD,
bridge state, camera framing, and disposal use the active layer state.

- [ ] **Step 4: Verify**

```powershell
node --test web/viewer/*.test.mjs
```

- [ ] **Step 5: Path-limited commit with the required trailer.**

### Task 5: Real 256-chunk browser proof and Opus feedback

**Files:**
- Create: `docs/verification/2026-07-17-spatial-reconstruction-viewer.md`
- Create: `handoff/FEEDBACK-HANDOFF-CODEX-004.md`

- [ ] **Step 1: Serve the private canary artifact**

Use the already generated 67,878-Gaussian, 256-chunk `chunks.json`. Do not add
PLY, images, renders, or private manifests to Git.

- [ ] **Step 2: Verify in the browser**

- only manifest-declared nearby chunks are requested;
- movement changes active keys and LOD;
- cache remains at or below 36;
- no request uses an out-of-manifest coordinate or an on-demand endpoint;
- positions stay absolute with no per-chunk translation;
- `source.geometry_usability = preview-proxy` is displayed without promotion;
- Spark reports full 3DGS, or the DC fallback reason is visible.

- [ ] **Step 3: Write evidence and request the additive producer link**

Ask Opus to add:

```json
{ "spatial_chunks": "chunks/chunks.json" }
```

to `recon_manifest.json`. Direct `chunk-manifest` loading remains supported.

- [ ] **Step 4: Run final targeted gates**

```powershell
node --test web/viewer/*.test.mjs
.venv\Scripts\python.exe -m pytest tests/test_spatial_chunk.py tests/test_reconstruct.py -q
git diff --check
```

- [ ] **Step 5: Commit docs, push `main`, and wait five seconds before retrying any transient GitHub failure.**
