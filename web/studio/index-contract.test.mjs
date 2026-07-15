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

test('B1 ingest uses an explicit confirmation without command or path fields', () => {
  assert.match(html, /id="ingest-dialog"/);
  assert.match(html, /id="ingest-cancel-notice"/);
  assert.match(html, /id="ingest-max_long_edge"/);
  assert.doesNotMatch(html, /name="(?:command|path|environment)"/);
  assert.match(app, /adapter\.startJob\('ingest', parameters\)/);
  assert.doesNotMatch(app, /engine:\s*adapter\.kind/);
});
