import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');

test('reset camera participates in capability-gated viewer controls', () => {
  assert.match(
    html,
    /id="reset-camera"[^>]*data-viewer-command="resetCamera"/,
  );
});
