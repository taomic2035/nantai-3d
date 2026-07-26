import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
const app = await readFile(new URL('./app.js', import.meta.url), 'utf8');
const css = await readFile(new URL('./styles.css', import.meta.url), 'utf8');

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

test('Preview package verification is separate from scene provenance', () => {
  assert.match(html, /id="release-badge"/);
  assert.match(html, /Preview\s*只读模式/);
  assert.match(app, /snapshot\.release/);
  assert.match(app, /package_status\s*===\s*['"]verified['"]/);
  assert.match(app, /发布包\s*·\s*已校验/);
  assert.match(app, /scene_trust_effect/);
  assert.match(app, /chip\(['"]package['"]/);
  assert.doesNotMatch(app, /package_status[\s\S]{0,120}(?:metric|aligned|real)/i);
});

test('provenance chips cannot create a horizontal Studio page overflow', () => {
  const rule = css.match(/\.provenance-bar\s*\{[^}]+\}/s)?.[0] ?? '';
  assert.match(rule, /overflow(?:-x)?:\s*hidden/);
  assert.doesNotMatch(rule, /overflow-x:\s*auto/);
  assert.match(css, /\.provenance-bar\s+\.chip\s*\{[^}]*min-width:\s*0/s);
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
  assert.match(
    app,
    /bridge\.start\(\);\s*frame\.src\s*=\s*viewerFrameSource\(frame\);/,
  );
});

test('acceptance launch can request points before heavy mesh loading', () => {
  assert.match(
    app,
    /function viewerFrameSource\(frame\)[\s\S]*new URL\(frame\.dataset\.src,[\s\S]*viewerPresentation[\s\S]*requested\s*===\s*['"]points['"][\s\S]*searchParams\.set\(['"]presentation['"],\s*['"]points['"]\)/,
  );
  assert.doesNotMatch(
    app,
    /searchParams\.set\(['"]presentation['"],\s*requested\)/,
  );
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

test('Studio exposes only validated roaming nodes and distinguishes camera move from walkability', () => {
  assert.match(
    app,
    /import\s*\{[^}]*loadOptionalRoamingGraph[^}]*\}\s*from\s*['"]\.\/roaming-graph-loader\.mjs['"]/s,
  );
  for (const id of [
    'roaming-summary',
    'roaming-node-select',
    'roaming-node-jump',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /坐标跳转只移动相机，不证明该点可行走/);
  assert.match(
    app,
    /next\s*===\s*['"]ready['"][\s\S]*loadOptionalRoamingGraph\(\{\s*bridge\s*\}\)/s,
  );
  assert.match(app, /navigation_nodes/);
  assert.match(app, /new Option\(/);
  assert.match(app, /bridge\.command\('setCameraPose',\s*\{\s*position:\s*selected\.position\s*\}\)/);
  assert.match(app, /roamingSummary\.textContent/);
  assert.doesNotMatch(app, /roamingSummary\.innerHTML/);
});

test('stage toolbars keep primary controls readable at a medium desktop width', () => {
  assert.match(css, /\.stage-toolbar\s+\.button\s*\{[^}]*white-space:\s*nowrap/s);
  assert.match(css, /\.segmented button\s*\{[^}]*white-space:\s*nowrap/s);
  assert.match(css, /\.roaming-kicker\s*\{[^}]*white-space:\s*nowrap/s);
  assert.match(css, /#roaming-node-select\s*\{[^}]*min-width:\s*120px/s);
  assert.match(
    css,
    /@media\s*\(max-width:\s*1399px\)\s*\{[^}]*\.roaming-disclosure[^}]*display:\s*none/s,
  );
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

test('review inspector loads fail-closed production quality evidence', () => {
  assert.match(
    app,
    /import\s*\{[^}]*loadProductionQualityEvidence[^}]*renderProductionQualityPanel[^}]*\}\s*from\s*['"]\.\/production-quality-panel\.mjs['"]/s,
  );
  assert.match(app, /loadProductionQualityEvidence\(\)\.catch\(\(\)\s*=>\s*null\)/);
  assert.match(
    app,
    /renderProductionQualityPanel\(\s*productionQualityEvidence,\s*selectedQualityCameraId,\s*\)/,
  );
  assert.match(app, /byId\(['"]production-quality-camera['"]\)/);
  assert.match(app, /selectedQualityCameraId\s*=\s*event\.target\.value/);
});

test('production quality evidence has compact stage and rule layouts', () => {
  assert.match(css, /\.quality-stages\s*\{[^}]*grid-template-columns/s);
  assert.match(css, /\.quality-rule\s*\{[^}]*grid-template-columns/s);
  assert.match(css, /\.quality-hashes[^}]*overflow-wrap:\s*anywhere/s);
  assert.match(css, /\[data-state="rejected"\][^{]*\{[^}]*var\(--red\)/s);
});

test('review inspector renders fail-closed real-scene acceptance evidence', () => {
  assert.match(
    app,
    /import\s*\{[^}]*renderRealSceneEvidence[^}]*\}\s*from\s*['"]\.\/real-scene-evidence\.mjs['"]/s,
  );
  assert.match(app, /renderRealSceneEvidence\(\s*snapshot\.real_scene\s*\)/);
  assert.match(css, /\.real-scene-stages\s*\{[^}]*grid-template-columns/s);
  assert.match(css, /\.real-scene-stage\[data-state="failed"\]/);
  assert.match(css, /\.real-scene-report-sha[^}]*overflow-wrap:\s*anywhere/s);
});
