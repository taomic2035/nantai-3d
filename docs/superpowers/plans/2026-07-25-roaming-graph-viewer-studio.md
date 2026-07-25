# Roaming Graph Viewer / Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed optional room/portal graph that Viewer validates and Studio presents as graph-only reachability with reachable-room camera jumps.

**Architecture:** A focused `web/viewer/roaming-graph.mjs` module validates the additive v1 artifact and derives all counts and navigation nodes. Viewer owns same-origin loading and truth presentation; Studio uses a capability-gated optional loader and never receives unvalidated graph bytes. No production artifact is committed until an exact Blender build emits one.

**Tech Stack:** Browser ES modules, Node.js built-in test runner, existing Viewer postMessage bridge, semantic HTML/CSS.

---

## File structure

- Create `web/viewer/roaming-graph.mjs`: pure validation, graph traversal and view model.
- Create `web/viewer/roaming-graph.test.mjs`: valid, fragmented and adversarial fixtures.
- Modify `web/viewer/bridge.mjs`: advertise the dynamic non-rendering artifact kind.
- Modify `web/viewer/bridge.test.mjs`: literal capability contract.
- Modify `web/viewer/main.js`: load, retain and expose only validated graph state.
- Modify `web/viewer/index.html`: roaming graph HUD.
- Modify `web/viewer/index-contract.test.mjs`: static loading/HUD contract.
- Create `web/studio/roaming-graph-loader.mjs`: optional capability-gated probe.
- Create `web/studio/roaming-graph-loader.test.mjs`: unsupported/404/load/error behavior.
- Modify `web/studio/app.js`: load summary, populate reachable nodes and jump.
- Modify `web/studio/index.html`: summary, selector and graph-node jump control.
- Modify `web/studio/styles.css`: compact responsive layout.
- Modify `web/studio/index-contract.test.mjs`: loader and accessible-control contract.
- Create `handoff/HANDOFF-GLM-009-roaming-graph-producer.md`: exact producer checklist.

### Task 1: Pure roaming graph validator and view model

**Files:**

- Create: `web/viewer/roaming-graph.test.mjs`
- Create: `web/viewer/roaming-graph.mjs`

- [ ] **Step 1: Write the connected and malformed RED tests**

Create a fixture with three rooms, three portals, six reciprocal edges and one
three-edge closed loop. Assert:

```js
assert.equal(isRoamingGraph(graph), true);
assert.deepEqual(roamingGraphViewModel(graph), {
  status: 'graph-connected',
  room_count: 3,
  reachable_room_count: 3,
  component_count: 1,
  portal_count: 3,
  loop_count: 1,
  summary: '3/3 graph rooms reachable · not 360 evidence',
  reachability_label: 'graph-connected · candidate',
  provenance_label: 'synthetic · modeled-unverified · graph only',
  navigation_nodes: [
    { room_id: 'room-entry', label: 'Entry', position: { east: 0, north: 0, up: 1.6 } },
    { room_id: 'room-hall', label: 'Hall', position: { east: 4, north: 0, up: 1.6 } },
    { room_id: 'room-gallery', label: 'Gallery', position: { east: 4, north: 4, up: 3.2 } },
  ],
});
```

Clone and mutate the fixture to assert rejection for duplicate IDs, unknown
rooms, dangling rooms, missing/extra reverse edges, NaN/Infinity, zero
clearance, broken loop order, malformed SHA, wrong coordinate frame and trust
promotion.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
node --test web/viewer/roaming-graph.test.mjs
```

Expected: failure because `roaming-graph.mjs` does not exist.

- [ ] **Step 3: Implement the minimal pure module**

Export:

```js
export const ROAMING_GRAPH_SCHEMA =
  'nantai.synthetic-village.roaming-graph.v1';

export function isRoamingGraph(graph) {
  // Validate literal schema/trust/frame/hash fields and bounded arrays.
  // Build unique room/portal/edge/loop maps.
  // Require every room to be incident to a portal.
  // Require exactly two exact reverse directed edges per portal.
  // Require every loop to be a closed ordered chain of >= 3 edges.
  return valid;
}

export function roamingGraphViewModel(graph) {
  if (!isRoamingGraph(graph)) return unknownModel();
  // BFS from entry_room_id; separately count all connected components.
  // Return only reachable room centers as navigation_nodes.
}
```

Use finite-number checks, bounded array sizes, lowercase SHA validation and
exact literal trust declarations. Never derive trust from labels or filenames.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
node --test web/viewer/roaming-graph.test.mjs
```

Expected: all roaming-graph tests pass with zero failures.

### Task 2: Viewer capability, loading, state and HUD

**Files:**

- Modify: `web/viewer/bridge.test.mjs`
- Modify: `web/viewer/index-contract.test.mjs`
- Modify: `web/viewer/bridge.mjs`
- Modify: `web/viewer/main.js`
- Modify: `web/viewer/index.html`

- [ ] **Step 1: Add Viewer RED contracts**

Require:

```js
assert.deepEqual(VIEWER_CAPABILITIES.dynamic_artifact_kinds, [
  'recon-manifest',
  'chunk-manifest',
  'coverage-audit',
  'production-camera-plan',
  'roaming-graph',
]);
assert.equal(VIEWER_CAPABILITIES.artifact_kinds.includes('roaming-graph'), false);
```

In `index-contract.test.mjs`, require:

```js
for (const id of [
  'hud-roaming-status',
  'hud-roaming-reachability',
  'hud-roaming-portals',
  'hud-roaming-provenance',
]) assert.match(roamingSection, new RegExp(`id="${id}"`));

assert.match(main, /from ['"]\.\/roaming-graph\.mjs['"]/);
assert.match(main, /kind\s*===\s*['"]roaming-graph['"]/);
assert.match(main, /isRoamingGraph\(/);
assert.match(main, /roaming_graph:\s*roamingGraphViewModel\(/);
```

- [ ] **Step 2: Run Viewer RED tests**

Run:

```powershell
node --test web/viewer/bridge.test.mjs web/viewer/index-contract.test.mjs
```

Expected: capability and HUD/loading assertions fail.

- [ ] **Step 3: Implement Viewer integration**

Add `roaming-graph` only to `dynamic_artifact_kinds`. In `main.js`, import the
pure module, retain `roamingGraph`/`roamingGraphUrl`, expose the view model from
`readState`, update HUD text/colors, and add a same-origin load branch:

```js
if (kind === 'roaming-graph') {
  const { artifact: nextGraph, artifactUrl: nextUrl } =
    await loadSameOriginJson({ url, artifact, label: 'roaming graph' });
  if (!isRoamingGraph(nextGraph)) {
    throw new Error('无效的 roaming-graph artifact');
  }
  roamingGraph = nextGraph;
  roamingGraphUrl = nextUrl;
  updateHUD();
  return {
    kind,
    url: roamingGraphUrl,
    roaming_graph: roamingGraphViewModel(roamingGraph),
    state: readState(),
  };
}
```

If extracting the repeated same-origin helper would broaden scope, retain the
existing branch style and change only the new artifact path.

Add the HUD section before coverage:

```html
<div class="roaming-graph" aria-label="漫游连通图">
  <div class="stat">漫游图: <b id="hud-roaming-status">roaming graph not loaded</b></div>
  <div class="stat">入口可达: <b id="hud-roaming-reachability">unknown</b></div>
  <div class="stat">门洞 / 回路: <b id="hud-roaming-portals">unknown</b></div>
  <div class="stat">证据边界: <b id="hud-roaming-provenance">unknown · fail-closed</b></div>
</div>
```

- [ ] **Step 4: Run Viewer focused and full suites**

Run:

```powershell
node --test web/viewer/roaming-graph.test.mjs web/viewer/bridge.test.mjs web/viewer/index-contract.test.mjs
node --test web/viewer/*.test.mjs
```

Expected: zero failures.

### Task 3: Studio optional loader

**Files:**

- Create: `web/studio/roaming-graph-loader.test.mjs`
- Create: `web/studio/roaming-graph-loader.mjs`

- [ ] **Step 1: Write loader RED tests**

Mirror the existing optional production-plan loader and require:

```js
assert.deepEqual(
  await loadOptionalRoamingGraph({ bridge: unsupportedBridge, fetchImpl }),
  { status: 'unsupported' },
);
assert.deepEqual(
  await loadOptionalRoamingGraph({ bridge: supportedBridge, fetchImpl: head404 }),
  { status: 'absent' },
);
assert.deepEqual(await loadOptionalRoamingGraph({
  bridge: supportedBridge,
  fetchImpl: head200,
}), {
  status: 'loaded',
  roaming_graph: connectedViewModel,
});
```

Assert that unsupported performs no fetch, 404 performs no bridge load,
successful loading calls:

```js
bridge.loadArtifact('roaming-graph', {
  url: '/web/data/roaming-graph.json',
});
```

and HTTP 500 remains a rejected promise.

- [ ] **Step 2: Run the loader test and verify RED**

Run:

```powershell
node --test web/studio/roaming-graph-loader.test.mjs
```

Expected: missing-module failure.

- [ ] **Step 3: Implement the optional loader**

```js
export const ROAMING_GRAPH_URL = '/web/data/roaming-graph.json';

export async function loadOptionalRoamingGraph({
  bridge,
  fetchImpl = globalThis.fetch,
  url = ROAMING_GRAPH_URL,
}) {
  if (!bridge.supportsArtifactKind('roaming-graph')) {
    return { status: 'unsupported' };
  }
  const response = await fetchImpl(url, { method: 'HEAD', cache: 'no-store' });
  if (response.status === 404) return { status: 'absent' };
  if (!response.ok) throw new Error(`roaming graph probe failed (${response.status})`);
  const loaded = await bridge.loadArtifact('roaming-graph', { url });
  return { status: 'loaded', roaming_graph: loaded.roaming_graph };
}
```

- [ ] **Step 4: Run the loader test and verify GREEN**

Run:

```powershell
node --test web/studio/roaming-graph-loader.test.mjs
```

Expected: all loader tests pass.

### Task 4: Studio graph summary and reachable-room jump

**Files:**

- Modify: `web/studio/index-contract.test.mjs`
- Modify: `web/studio/index.html`
- Modify: `web/studio/styles.css`
- Modify: `web/studio/app.js`

- [ ] **Step 1: Add Studio RED contracts**

Require accessible controls:

```js
for (const id of [
  'roaming-summary',
  'roaming-node-select',
  'roaming-node-jump',
]) assert.match(html, new RegExp(`id="${id}"`));

assert.match(app, /loadOptionalRoamingGraph\(\{\s*bridge\s*\}\)/);
assert.match(app, /navigation_nodes/);
assert.match(app, /bridge\.command\(['"]setCameraPose['"]/);
```

Also require the visible warning text:

```text
坐标跳转只移动相机，不证明该点可行走
```

- [ ] **Step 2: Run Studio contract test and verify RED**

Run:

```powershell
node --test web/studio/index-contract.test.mjs
```

Expected: missing loader/control/warning assertions fail.

- [ ] **Step 3: Implement Studio presentation**

Add a compact toolbar group:

```html
<div class="roaming-jump" role="group" aria-label="漫游图节点跳转">
  <span id="roaming-summary">漫游图 · 未加载</span>
  <select id="roaming-node-select" aria-label="入口可达房间" disabled>
    <option value="">无可验证节点</option>
  </select>
  <button id="roaming-node-jump" class="button button-quiet"
          type="button" disabled>前往节点</button>
</div>
```

After Viewer readiness, run one optional probe. On loaded state, use only the
validated `navigation_nodes` returned by Viewer, set option values to
`room_id`, keep positions in an in-memory map, and call:

```js
bridge.command('setCameraPose', { position: selected.position });
```

Use `textContent`/`new Option` rather than interpolated HTML for graph labels.
When absent, unsupported or failed, keep controls disabled and keep the
summary honest.

- [ ] **Step 4: Run Studio focused and full suites**

Run:

```powershell
node --test web/studio/roaming-graph-loader.test.mjs web/studio/index-contract.test.mjs
node --test web/studio/*.test.mjs
```

Expected: zero failures.

### Task 5: Producer handoff and full verification

**Files:**

- Create: `handoff/HANDOFF-GLM-009-roaming-graph-producer.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write the exact GLM checklist**

The handoff must require:

1. emit the literal v1 schema from an exact Blender scene;
2. bind scene/build/source/collision SHA values;
3. emit stable room/portal/edge/loop IDs;
4. include exactly two reverse edges per portal;
5. use measured endpoints and clearance from collision proxies;
6. keep `status=candidate`, `modeled-unverified`, `not-verified`,
   `not-claimed` and `trust_effect=none`;
7. run the browser validator as an independent consumer;
8. provide only artifact/report SHAs and private paths for Codex review;
9. do not touch `web/data` or claim 360/arbitrary-coordinate completion.

- [ ] **Step 2: Run all relevant gates**

Run:

```powershell
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
git diff --check
```

Expected: zero failures and no whitespace errors.

- [ ] **Step 3: Review the diff for ownership and trust**

Run:

```powershell
git status --short
git diff -- web/viewer web/studio docs/superpowers handoff/HANDOFF-GLM-009-roaming-graph-producer.md AGENTS.md
rg -n "360-ready|coverage-complete|arbitrary-coordinate-ready|metric-aligned" `
  web/viewer/roaming-graph.mjs web/studio
```

Expected:

- GLM `scripts/reconstruct_local.py`, `tests/test_reconstruct_local.py`,
  `.tmp_*` and `web/data` changes remain untouched;
- readiness search has no positive status emitted by the new implementation.

- [ ] **Step 4: Make one path-limited commit**

Stage only the files listed in this plan and commit with:

```text
feat: expose fail-closed roaming graph UX

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

Do not push while held GLM P0 commits remain ancestors of `main`. Once those
holds are closed, fetch/push/verify with the temporary proxy on
`127.0.0.1:7890` and leave no persistent Git proxy.
