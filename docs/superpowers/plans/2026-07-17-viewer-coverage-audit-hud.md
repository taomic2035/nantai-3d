# Viewer Coverage Audit HUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` while implementing each task and `superpowers:verification-before-completion` before committing.

**Goal:** Let the Viewer consume a separate `coverage-audit` artifact and present render visibility, camera geometry, SfM evidence, normal-angle spread, and provenance without inferring reconstruction readiness.

**Architecture:** Keep coverage evidence independent from reconstruction manifests and render layers. A pure module validates the minimum audit contract and derives fail-closed display state; the Viewer bridge only loads same-origin audit JSON and renders that state in a dedicated HUD section.

**Tech Stack:** Browser ES modules, Node.js `node:test`, existing Studio-to-Viewer bridge.

---

### Task 1: Define the fail-closed audit state model

**Files:**
- Create: `web/viewer/coverage-audit.mjs`
- Create: `web/viewer/coverage-audit.test.mjs`

1. Add failing tests for invalid input, uncalibrated diagnostics, calibrated incomplete evidence, explicit pass/fail, and normal-angle bounds.
2. Run `node --test web/viewer/coverage-audit.test.mjs` and confirm the missing implementation fails.
3. Implement strict minimum-contract validation and a pure HUD view model.
4. Re-run the focused test until green.

### Task 2: Expose the artifact through the Viewer bridge

**Files:**
- Modify: `web/viewer/bridge.mjs`
- Modify: `web/viewer/bridge.test.mjs`

1. Add a failing capability assertion for dynamic `coverage-audit` input.
2. Add the kind only to dynamic artifacts, never to renderer artifact kinds.
3. Run the bridge tests.

### Task 3: Wire the dedicated HUD without replacing reconstruction state

**Files:**
- Modify: `web/viewer/main.js`
- Modify: `web/viewer/index.html`
- Modify: `web/viewer/index-contract.test.mjs`

1. Add failing contract tests for the separate five-row coverage HUD and load path.
2. Load inline or same-origin audit JSON without touching `reconManifest`.
3. Render gray/amber/green/red states from the pure view model.
4. Ensure visibility-only wording says `渲染可见`, never `可重建` or `已覆盖`.

### Task 4: Verify and commit

1. Run focused coverage, bridge, and index contract tests.
2. Run all `web/viewer/*.test.mjs` tests.
3. Run `git diff --check` and inspect path-limited diff.
4. Commit only the listed Codex-owned files with the required co-author trailer, then push `main`.
