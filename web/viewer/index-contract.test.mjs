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

test('runtime weather is visibly an overlay and never claims relighting', () => {
  assert.match(
    html,
    /<label[^>]*for="weather-control"[^>]*>视觉天气（叠加）<\/label>/,
  );
  assert.match(html, /<div class="stat">大气叠加: <b id="hud-weather">/);
  assert.match(
    html,
    /id="environment-status"[^>]*>大气叠加 atmospheric overlay · 非重光照 not relighting · 不改变 3DGS 已烘焙光照<\/p>/,
  );
  assert.doesNotMatch(html, /<div class="stat">天气:/);
  assert.match(main, /ENVIRONMENT_EFFECT_IDENTITY/);
  assert.match(main, /\.\.\.ENVIRONMENT_EFFECT_IDENTITY/);
  assert.match(main, /atmospheric overlay/);
  assert.match(main, /not relighting/);
});

test('viewer runtime wires environment state without mutating provenance', () => {
  assert.match(main, /from ['"]\.\/environment\.mjs['"]/);
  assert.match(
    main,
    /import\s*\{[\s\S]*ENVIRONMENT_EFFECT_IDENTITY[\s\S]*\}\s*from ['"]\.\/environment\.mjs['"]/,
  );
  assert.match(main, /setWeather:\s*\(\{\s*weather\s*\}\)\s*=>/);
  assert.match(main, /setZoom:\s*\(\{\s*zoom\s*\}\)\s*=>/);
  assert.match(main, /camera\.zoom\s*=\s*environmentState\.zoom/);
  assert.match(main, /updatePrecipitation\(dt\)/);
  assert.match(main, /renderer\.domElement\.tabIndex\s*=\s*0/);
  assert.match(main, /renderer\.domElement\.setAttribute\('aria-label',\s*'3D 场景画布'\)/);
  assert.doesNotMatch(main, /reconManifest\.environment\s*=/);
  assert.doesNotMatch(main, /manifest\.environment\s*=/);
});

test('viewer runtime accepts static spatial reconstruction chunks without world offsets', () => {
  assert.match(main, /from ['"]\.\/spatial-reconstruction\.mjs['"]/);
  assert.match(main, /from ['"]\.\/splat-chunks-layer\.mjs['"]/);
  assert.match(main, /from ['"]\.\/spatial-point-layer\.mjs['"]/);
  assert.match(main, /kind\s*!==\s*['"]recon-manifest['"][\s\S]*kind\s*!==\s*['"]chunk-manifest['"]/);
  assert.match(main, /isSpatialChunkManifest\(/);
  assert.match(main, /createSpatialSplatLayer\(/);
  assert.match(main, /createSpatialPointLayer\(/);
  assert.doesNotMatch(main, /world_offset/);
});

test('spatial reconstruction HUD exposes only evidence-backed active point estimates', () => {
  assert.match(main, /active_estimated_points/);
  assert.match(main, /Number\.isSafeInteger\(/);
  assert.match(main, /~\$\{rendererState\.active_estimated_points\.toLocaleString\(\)\} splats/);
});

test('coverage evidence has a dedicated fail-closed HUD separate from provenance', () => {
  const coverage = html.match(
    /<div class="coverage"[^>]*>([\s\S]*?)<\/div>\s*<div class="legend">/,
  )?.[1];
  assert.ok(coverage, 'coverage HUD section must exist before the legend');
  for (const id of [
    'hud-coverage-status',
    'hud-coverage-visibility',
    'hud-coverage-geometry',
    'hud-coverage-sfm',
    'hud-coverage-provenance',
  ]) {
    assert.match(coverage, new RegExp(`id="${id}"`));
  }
  assert.match(coverage, /渲染可见/);
  assert.doesNotMatch(coverage, /可重建|已覆盖|可测量/);
});

test('expanded evidence HUD stays within the viewport and can scroll', () => {
  const hudRule = html.match(/#hud\s*\{([\s\S]*?)\}/)?.[1];
  assert.ok(hudRule, 'HUD style rule must exist');
  assert.match(hudRule, /max-height:\s*calc\(100vh\s*-\s*96px\)/);
  assert.match(hudRule, /overflow-y:\s*auto/);
  assert.match(hudRule, /scrollbar-gutter:\s*stable/);
});

test('viewer loads coverage audit independently from reconstruction artifacts', () => {
  assert.match(main, /from ['"]\.\/coverage-audit\.mjs['"]/);
  assert.match(main, /kind\s*===\s*['"]coverage-audit['"]/);
  assert.match(main, /isCoverageAudit\(/);
  assert.match(main, /coverageAuditViewModel\(/);
  assert.match(main, /coverage:\s*coverageAuditViewModel\(/);
  assert.match(main, /absoluteUrl\.origin\s*!==\s*window\.location\.origin/);
});

test('terminal world-envelope failures do not enter the chunk retry loop', () => {
  assert.match(main, /shouldRetryWorldChunkFailure/);
  assert.match(main, /terminalChunkFailures/);
  assert.match(main, /error\.status\s*=\s*res\.status/);
  assert.match(main, /error\.apiCode\s*=/);
  assert.match(main, /terminalChunkFailures\.add\(key\)/);
});
