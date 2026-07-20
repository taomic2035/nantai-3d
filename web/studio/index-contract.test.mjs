import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
const app = await readFile(new URL('./app.js', import.meta.url), 'utf8');

test('reset camera participates in capability-gated viewer controls', () => {
  assert.match(
    html,
    /id="reset-camera"[^>]*data-viewer-command="resetCamera"/,
  );
});

test('near-view preview is capability-gated and sends an explicit look target', () => {
  assert.match(
    html,
    /id="showcase-camera"[^>]*data-viewer-command="setCameraPose"/,
  );
  assert.match(app, /showcaseCameraPose\(state\.bounds\)/);
  assert.match(
    app,
    /bridge\.command\('setCameraPose',\s*showcaseCameraPose\(state\.bounds\)\)/,
  );
});

test('primary write availability has a visible live reason', () => {
  assert.match(html, /id="primary-action-reason"[^>]*aria-live="polite"/);
  assert.match(html, /Views\s*·\s*DAG/);
});

test('app loads capabilities and executes primary navigation intent', () => {
  assert.match(app, /import\s*\{[^}]*primaryNavigation[^}]*\}\s*from\s*['"]\.\/job-actions\.mjs['"]/s);
  assert.match(app, /await\s+adapter\.loadCapabilities\(\)/);
  assert.match(app, /button\.disabled\s*=\s*!action\.enabled/);
  assert.match(app, /\[data-source-empty-state\]/);
});

test('coordinate jump is a capability-gated viewer control wired to setCameraPose', () => {
  assert.match(html, /id="coord-east"[^>]*type="number"/);
  assert.match(html, /id="coord-north"[^>]*type="number"/);
  assert.match(html, /id="coord-up"[^>]*type="number"/);
  assert.match(html, /id="coord-jump-btn"[^>]*data-viewer-command="setCameraPose"/);
  // number inputs stay out of the统一 disabled sweep so only the button is gated.
  assert.doesNotMatch(html, /id="coord-(?:east|north|up)"[^>]*data-viewer-command/);
  assert.match(app, /bridge\.command\('setCameraPose',\s*\{\s*position:\s*\{\s*east,\s*north,\s*up\s*\}\s*\}\)/);
  assert.match(app, /announce\('坐标必须是有限数字'\)/);
});

test('viewer iframe loads only after the bridge listener starts', () => {
  assert.match(
    html,
    /id="viewer-frame"[^>]*data-src="\/web\/viewer\/index\.html\?embed=1"/s,
  );
  assert.doesNotMatch(html, /id="viewer-frame"[^>]*\ssrc=/s);
  assert.match(app, /bridge\.start\(\);\s*frame\.src\s*=\s*frame\.dataset\.src;/);
});

test('Studio quietly probes and loads the canonical coverage audit after Viewer readiness', () => {
  assert.match(
    app,
    /import\s*\{[^}]*loadOptionalCoverageAudit[^}]*\}\s*from\s*['"]\.\/coverage-audit-loader\.mjs['"]/s,
  );
  assert.match(app, /next\s*===\s*['"]ready['"][\s\S]*loadOptionalCoverageAudit\(\{\s*bridge/s);
  assert.match(app, /result\.status\s*===\s*['"]loaded['"]/);
});

test('Studio quietly probes the canonical production camera plan after Viewer readiness', () => {
  assert.match(
    app,
    /import\s*\{[^}]*loadOptionalProductionCameraPlan[^}]*\}\s*from\s*['"]\.\/production-camera-plan-loader\.mjs['"]/s,
  );
  assert.match(
    app,
    /next\s*===\s*['"]ready['"][\s\S]*loadOptionalProductionCameraPlan\(\{\s*bridge/s,
  );
  assert.match(app, /production_plan\.status/);
  assert.match(app, /production_plan\.placed/);
  assert.match(app, /production_plan\.target/);
});

test('B1 ingest uses an explicit confirmation without command or path fields', () => {
  assert.match(html, /id="ingest-dialog"/);
  assert.match(html, /id="ingest-cancel-notice"/);
  assert.match(html, /id="ingest-max_long_edge"/);
  assert.doesNotMatch(html, /name="(?:command|path|environment)"/);
  assert.match(app, /adapter\.startJob\('ingest', parameters\)/);
  assert.doesNotMatch(app, /engine:\s*adapter\.kind/);
});

test('asset workspace derives the current handoff from snapshot evidence', () => {
  assert.match(app, /assets\.current_handoff/);
  assert.match(app, /currentHandoff\.id/);
  assert.doesNotMatch(app, /asset_id:\s*['"]HANDOFF-001['"]/);
});
