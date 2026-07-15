# Studio Milestone A Read-only Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Studio truthfully capability-driven in its default read-only mode, expose the real DAG, and route empty, running, and failed primary actions to useful evidence instead of guaranteed write failures.

**Architecture:** The Python server advertises one fail-closed capability document. Both adapters expose the same `loadCapabilities()` interface. Pure front-end functions normalize that document and derive write/run actions; `app.js` only renders and executes those decisions. Milestone A never starts a subprocess and all HTTP mutation methods remain 405.

**Tech Stack:** Python 3.13 `http.server`, pytest, browser-native ES modules, Node.js `node:test`, HTML/CSS, in-app browser smoke validation.

---

## File map

- Create `tests/test_studio_capabilities.py`: isolated HTTP contract tests, avoiding the dirty `tests/test_studio_server.py`.
- Modify `pipeline/studio_server.py`: add the fixed read-only capability document and GET route only.
- Create `web/studio/capabilities.mjs`: fail-closed capability normalization and command lookup.
- Create `web/studio/capabilities.test.mjs`: local malformed-input and read-only tests.
- Modify `web/studio/local-adapter.mjs` and its test: load and validate `/api/capabilities`.
- Modify `web/studio/mock-adapter.mjs` and its test: advertise explicit synthetic read-only capabilities.
- Create `web/studio/job-actions.mjs`: DAG definition, primary/run action derivation, and navigation intent.
- Create `web/studio/job-actions.test.mjs`: empty/running/failed/read-only/cancel/retry coverage.
- Modify `web/studio/model.mjs` and its test: remove duplicated primary-action policy and re-export the new function for compatibility.
- Modify `web/studio/app.js`: load capabilities before rendering, render disabled reasons, and execute navigation intents.
- Modify `web/studio/index.html`, `web/studio/styles.css`, and `web/studio/index-contract.test.mjs`: visible capability reason, DAG language, accessible disabled state.

### Task 1: Server read-only capability contract

**Files:**
- Create: `tests/test_studio_capabilities.py`
- Modify: `pipeline/studio_server.py`

- [x] **Step 1: Write the failing HTTP tests**

Add tests which start `make_server`, request `GET` and `HEAD /api/capabilities`, and assert:

```python
assert payload == {
    "schema_version": 1,
    "mode": "read-only",
    "reason": "Job execution is not enabled in this Studio milestone.",
    "request_token": None,
    "single_writer": True,
    "commands": {
        command: {"enabled": False, "cancel": False, "retry": False,
                  "reason": "Job execution is not enabled in this Studio milestone."}
        for command in ("ingest", "reconstruct", "world", "validate-assets")
    },
}
```

Also assert `Cache-Control: no-store`, HEAD has no body, and POST `/api/jobs` remains 405.

- [x] **Step 2: Verify RED**

Run: `python -m pytest tests/test_studio_capabilities.py -q`

Expected: FAIL because `/api/capabilities` returns `api_not_found`.

- [x] **Step 3: Implement the immutable read-only document**

Add `READ_ONLY_REASON`, `STUDIO_COMMAND_IDS`, and `read_only_capabilities()` returning a fresh dictionary. Route `/api/capabilities` through `_send_json`; do not add CLI flags, tokens, POST handlers, or subprocess code.

- [x] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_studio_capabilities.py -q`

Expected: all tests pass.

### Task 2: Adapter capability loading and fail-closed normalization

**Files:**
- Create: `web/studio/capabilities.mjs`
- Create: `web/studio/capabilities.test.mjs`
- Modify: `web/studio/local-adapter.mjs`
- Modify: `web/studio/local-adapter.test.mjs`
- Modify: `web/studio/mock-adapter.mjs`
- Modify: `web/studio/mock-adapter.test.mjs`

- [x] **Step 1: Write failing capability tests**

Test the wished-for API:

```js
const normalized = normalizeCapabilities(raw);
assert.equal(normalized.mode, 'read-only');
assert.equal(commandCapability(normalized, 'reconstruct').enabled, false);
assert.match(commandCapability(normalized, 'reconstruct').reason, /not enabled/i);
```

Malformed schema, unknown mode, missing commands, and a mock claim of `read-write` must normalize to read-only with all known commands disabled. Local adapter must request `/api/capabilities`; mock adapter must return an explicit read-only document rather than inferring write access from its methods.

- [x] **Step 2: Verify RED**

Run: `node --test web/studio/capabilities.test.mjs web/studio/local-adapter.test.mjs web/studio/mock-adapter.test.mjs`

Expected: FAIL because the module and adapter methods do not exist.

- [x] **Step 3: Implement minimal normalization and adapters**

`normalizeCapabilities()` accepts only schema 1 and modes `read-only`/`read-write`, returns all four known commands, coerces booleans only when exactly `true`, and forces every operation false in read-only mode. `commandCapability()` returns a disabled unknown-command result. `LocalStudioAdapter.loadCapabilities()` requests and normalizes `/api/capabilities`; `MockStudioAdapter.loadCapabilities()` returns the same normalized read-only contract with a synthetic-fixture reason.

- [x] **Step 4: Verify GREEN**

Run the same Node command; expect all tests to pass.

### Task 3: DAG and pure action policy

**Files:**
- Create: `web/studio/job-actions.mjs`
- Create: `web/studio/job-actions.test.mjs`
- Modify: `web/studio/model.mjs`
- Modify: `web/studio/model.test.mjs`

- [x] **Step 1: Write failing policy tests**

Cover these exact outcomes:

```js
assert.deepEqual(STUDIO_DAG.edges, [
  ['sources', 'reconstruct'], ['assets', 'compose'],
  ['reconstruct', 'review'], ['compose', 'review'],
]);
assert.equal(derivePrimaryAction(empty, readOnly).id, 'inspect-sources');
assert.equal(primaryNavigation('inspect-sources').step, 'sources');
assert.equal(derivePrimaryAction(running, readOnly).id, 'view-progress');
assert.equal(derivePrimaryAction(failed, readOnly).id, 'inspect-failure');
assert.equal(derivePrimaryAction(noArtifact, readOnly).enabled, false);
assert.match(derivePrimaryAction(noArtifact, readOnly).reason, /not enabled/i);
assert.deepEqual(deriveRunActions(runningRun, readOnly), []);
assert.deepEqual(deriveRunActions(failedRun, readOnly), []);
```

Add read-write fixtures proving cancel/retry appear only when both command capability and run state allow them.

- [x] **Step 2: Verify RED**

Run: `node --test web/studio/job-actions.test.mjs web/studio/model.test.mjs`

Expected: FAIL because the new policy module does not exist and running/read-only cases are unsupported.

- [x] **Step 3: Implement the pure policy**

Define the four real DAG edges while retaining six view IDs (`align` remains reconstruct evidence, `stitch` is the compatibility key displayed as Compose). Derive primary priority as disconnected → failed → queued/running → empty sources → ingest → reconstruct → validate assets → compose world → review. Each write step is disabled when capability is absent; Review is reached only after both branches have current succeeded evidence. Navigation intent maps empty to Sources, running/failed to the declared command view plus drawer without guessing from pipeline state, and review to Review. Re-export `derivePrimaryAction` from `model.mjs` to avoid breaking consumers.

- [x] **Step 4: Verify GREEN**

Run the same Node tests; expect all tests to pass.

### Task 4: Render and execute truthful actions

**Files:**
- Modify: `web/studio/app.js`
- Modify: `web/studio/index.html`
- Modify: `web/studio/styles.css`
- Modify: `web/studio/index-contract.test.mjs`

- [x] **Step 1: Write failing document/source contract tests**

Assert the HTML contains `id="primary-action-reason"` with `aria-live="polite"`; assert `app.js` imports `primaryNavigation`, awaits `adapter.loadCapabilities()`, applies `button.disabled = !action.enabled`, and focuses a `[data-source-empty-state]` target for `inspect-sources`.

- [x] **Step 2: Verify RED**

Run: `node --test web/studio/index-contract.test.mjs`

Expected: FAIL because capability reason and wiring are absent.

- [x] **Step 3: Implement minimal UI wiring**

Load capabilities once before the first snapshot. Label the left rail “Views · DAG”, display `stitch` as “Compose”, replace numbered circles with branch markers, and keep the adapter v2 state key. Render the primary action reason visibly and in `title`; disable only write actions denied by capabilities. Render the Assets validation button disabled with the command reason. Execute `inspect-sources`, `view-progress`, and `inspect-failure` via navigation intent; open the drawer where requested and focus the Sources empty card with `tabindex="-1"`. Remove the old catch-after-click validation behavior from read-only paths.

- [x] **Step 4: Verify GREEN**

Run: `node --test web/studio/*.test.mjs`

Expected: all Studio tests pass.

### Task 5: Integrated verification and visual smoke test

**Files:**
- Modify only if a test exposes a Milestone A defect.

- [x] **Step 1: Run repository gates**

Run:

```powershell
python -m pytest -q
node --test web/studio/*.test.mjs
node --test web/viewer/*.test.mjs
git diff --check
```

Expected: Python and Node suites pass; `git diff --check` reports no errors.

- [x] **Step 2: Run local browser smoke validation**

Start the read-only server on loopback, open `/web/studio/`, and verify:

- read-only capability reason is visible before any write click;
- missing reconstruction cannot submit a job;
- empty input action selects Sources and moves focus to the empty card;
- running/failed fixtures open the task drawer and select the relevant evidence view;
- rail wording and visual grouping do not imply a single linear pipeline;
- no console error appears during initial load or scenario switching.

- [x] **Step 3: Commit only Milestone A files**

Stage the plan and files listed above, excluding all pre-existing dirty files. Commit with:

```text
feat: make Studio read-only capabilities explicit

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

## Self-review

- Spec coverage: default read-only, capability as sole write authority, DAG truth, empty input, active/failed navigation, disabled reason, mock/local consistency, and no subprocess are each mapped to a task.
- Deferred by design: write token security, jobs, cancel execution, retry execution, polling, ledger, and publication belong to B–D; A only proves their controls remain absent without advertised capability.
- Placeholder scan: no TBD/TODO or unspecified implementation step remains.
- Type consistency: command IDs are `ingest`, `reconstruct`, `world`, `validate-assets`; compatibility state key is `stitch`, UI view is `compose` only in DAG labels; capability schema is version 1 throughout.
