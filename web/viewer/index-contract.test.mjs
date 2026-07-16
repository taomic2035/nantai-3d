import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
const main = await readFile(new URL('./main.js', import.meta.url), 'utf8');

test('environment controls expose six weather ids and bounded zoom', () => {
  assert.match(html, /id="environment-controls"[^>]*aria-label="环境控制"/);
  assert.match(html, /<label[^>]*for="weather-control"/);
  assert.match(html, /id="weather-control"/);
  for (const id of ['clear', 'overcast', 'rain', 'snow', 'fog', 'night']) {
    assert.match(html, new RegExp(`<option value="${id}"`));
  }
  assert.match(html, /<label[^>]*for="zoom-control"/);
  assert.match(
    html,
    /id="zoom-control"[^>]*min="0\.5"[^>]*max="3"[^>]*step="0\.1"/,
  );
  assert.match(html, /id="zoom-value"[^>]*aria-live="polite"/);
  assert.match(html, /id="zoom-reset"[^>]*type="button"/);
});

test('environment status and HUD values are visible but separate from provenance', () => {
  assert.match(html, /id="environment-status"[^>]*aria-live="polite"/);
  assert.match(html, /id="hud-weather"/);
  assert.match(html, /id="hud-zoom"/);
  const provenance = html.match(
    /<div class="provenance">([\s\S]*?)<\/div>\s*<div class="legend">/,
  )[1];
  assert.doesNotMatch(provenance, /hud-weather|hud-zoom|environment-status/);
});

test('viewer runtime wires environment state without mutating provenance', () => {
  assert.match(main, /from ['"]\.\/environment\.mjs['"]/);
  assert.match(main, /effect_source:\s*['"]viewer-runtime['"]/);
  assert.match(main, /setWeather:\s*\(\{\s*weather\s*\}\)\s*=>/);
  assert.match(main, /setZoom:\s*\(\{\s*zoom\s*\}\)\s*=>/);
  assert.match(main, /camera\.zoom\s*=\s*environmentState\.zoom/);
  assert.match(main, /updatePrecipitation\(dt\)/);
  assert.match(main, /renderer\.domElement\.tabIndex\s*=\s*0/);
  assert.match(main, /renderer\.domElement\.setAttribute\('aria-label',\s*'3D 场景画布'\)/);
  assert.doesNotMatch(main, /reconManifest\.environment\s*=/);
  assert.doesNotMatch(main, /manifest\.environment\s*=/);
});
