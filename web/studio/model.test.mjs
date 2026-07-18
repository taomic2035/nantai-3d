import assert from 'node:assert/strict';
import test from 'node:test';

import {
  derivePrimaryAction,
  normalizeSnapshot,
  normalizeStepState,
  viewerCapabilityTokens,
} from './model.mjs';

function baseSnapshot() {
  return {
    schema_version: 2,
    adapter: { kind: 'local', connected: true },
    sources: { images: 5, videos: 1, frames: 24, rejected: 0 },
    coordinate: {
      source_frame: 'world-enu',
      world_frame: 'world-enu',
      source_provenance: 'measured',
      world_provenance: 'measured',
      contributor_provenance: ['measured'],
      units: 'meters',
      handedness: 'right',
      up_axis: 'z',
      transform_chain: [],
      metric_evidence: ['gps-control-points.json'],
      registered_images: 29,
      total_images: 29,
    },
    reconstruction: {
      requested_engine: 'import',
      actual_engine: 'import',
      synthetic: false,
      geometry_usability: 'metric-aligned',
      artifact: { immutable: true, sha256: 'abc', kind: '3dgs-ply' },
      attributes: [
        'x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
        'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3',
      ],
      sh_degree: 0,
      renderer_capabilities: ['anisotropic-covariance', 'alpha-composite', 'dc-color'],
      gaussian_count: 1000,
      lod: [0, 1, 2],
    },
    assets: { registered: 11, consumed: 11, blocked: 0 },
    pipeline: Object.fromEntries(
      ['sources', 'reconstruct', 'assets', 'stitch'].map((key) => [key, {
        availability: 'ready', execution: 'succeeded', freshness: 'current',
        preview: 'ready', trust: 'proxy',
      }]),
    ),
  };
}

test('unknown coordinate evidence fails closed', () => {
  const raw = baseSnapshot();
  raw.coordinate = {};
  const model = normalizeSnapshot(raw);
  assert.equal(model.derived.geometryUsability, 'preview-only');
  assert.equal(model.derived.trust, 'untrusted');
  assert.ok(model.derived.diagnostics.some((item) => item.includes('coordinate')));
});

test('synthetic output can never become measurable or verified', () => {
  const raw = baseSnapshot();
  raw.adapter.kind = 'mock';
  raw.reconstruction.synthetic = true;
  const model = normalizeSnapshot(raw);
  assert.equal(model.derived.geometryUsability, 'preview-only');
  assert.equal(model.derived.trust, 'proxy');
});

test('declared preview-only provenance cannot be promoted by metric coordinates', () => {
  const raw = baseSnapshot();
  raw.reconstruction.geometry_usability = 'preview-only';

  const model = normalizeSnapshot(raw);

  assert.equal(model.derived.geometryUsability, 'preview-only');
  assert.equal(model.derived.trust, 'proxy');
  assert.ok(model.derived.diagnostics.some((item) => item.includes('provenance')));
});

test('unknown frame provenance blocks measurable geometry and trust', () => {
  const raw = baseSnapshot();
  raw.coordinate.source_provenance = 'unknown';
  raw.coordinate.world_provenance = 'unknown';

  const model = normalizeSnapshot(raw);

  assert.equal(model.derived.geometryUsability, 'preview-only');
  assert.equal(model.derived.trust, 'untrusted');
  assert.ok(model.derived.diagnostics.some((item) => item.includes('frame provenance')));
});

test('unknown geometry contributor blocks a forged metric summary', () => {
  const raw = baseSnapshot();
  raw.coordinate.contributor_provenance = ['unknown'];

  const model = normalizeSnapshot(raw);

  assert.equal(model.derived.geometryUsability, 'preview-only');
  assert.equal(model.derived.trust, 'untrusted');
  assert.ok(model.derived.diagnostics.some((item) => item.includes('contributor')));
});

test('an explicit metric transform can align sfm-local into world-enu', () => {
  const raw = baseSnapshot();
  raw.coordinate.source_frame = 'sfm-local';
  raw.coordinate.source_provenance = 'sfm';
  raw.coordinate.transform_chain = [{
    id: 'sim3-control-v1', source_frame: 'sfm-local', target_frame: 'world-enu',
  }];
  const model = normalizeSnapshot(raw);
  assert.equal(model.derived.geometryUsability, 'measurable');
});

test('core transform_id is accepted as the canonical transform identifier', () => {
  const raw = baseSnapshot();
  raw.coordinate.source_frame = 'sfm-local';
  raw.coordinate.transform_chain = [{
    transform_id: 'sha256:canonical-transform',
    source_frame: 'sfm-local',
    target_frame: 'world-enu',
  }];
  assert.equal(normalizeSnapshot(raw).derived.geometryUsability, 'measurable');
});

test('cross-frame coordinates require one unambiguous transform chain', () => {
  const missing = baseSnapshot();
  missing.coordinate.source_frame = 'sfm-local';
  assert.equal(normalizeSnapshot(missing).derived.geometryUsability, 'preview-only');

  const duplicated = baseSnapshot();
  duplicated.coordinate.source_frame = 'sfm-local';
  duplicated.coordinate.transform_chain = [
    { id: 'same', source_frame: 'sfm-local', target_frame: 'world-enu' },
    { id: 'same', source_frame: 'sfm-local', target_frame: 'world-enu' },
  ];
  assert.equal(normalizeSnapshot(duplicated).derived.geometryUsability, 'preview-only');
});

test('renderer claims are cross-checked against artifact attributes', () => {
  const raw = baseSnapshot();
  raw.reconstruction.renderer_capabilities = ['dc-color'];
  const degraded = normalizeSnapshot(raw);
  assert.equal(degraded.derived.renderFidelity, 'dc-point-preview');

  const complete = normalizeSnapshot(baseSnapshot());
  assert.equal(complete.derived.renderFidelity, 'gaussian-splat-dc');
});

test('viewer capability tokens come from the live renderer handshake', () => {
  assert.deepEqual(viewerCapabilityTokens({
    renderer: {
      fidelity: 'gaussian-splat-sh',
      anisotropic_covariance: true,
      alpha_composite: true,
      spherical_harmonics: true,
    },
  }), [
    'dc-color', 'anisotropic-covariance', 'alpha-composite', 'spherical-harmonics',
  ]);
  assert.deepEqual(viewerCapabilityTokens({
    renderer: {
      fidelity: 'dc-point-preview',
      anisotropic_covariance: false,
      alpha_composite: false,
    },
  }), ['dc-color']);
  assert.deepEqual(viewerCapabilityTokens({
    renderer: {
      id: 'three-mesh',
      fidelity: 'simplified-pbr-not-render-parity',
      photo_textures: false,
      real_reconstruction: false,
    },
  }), ['mesh-simplified-pbr']);
});

test('synthetic mesh presentation never becomes reconstruction or metric evidence', () => {
  const raw = baseSnapshot();
  raw.reconstruction.geometry_usability = 'preview-only';
  raw.reconstruction.renderer_capabilities = ['mesh-simplified-pbr'];
  const model = normalizeSnapshot(raw);

  assert.equal(model.derived.renderFidelity, 'synthetic-mesh-simplified-pbr');
  assert.equal(model.derived.geometryUsability, 'preview-only');
  assert.equal(model.derived.trust, 'proxy');
  assert.ok(model.derived.diagnostics.some((item) => item.includes('separate synthetic model')));
});

test('high-order SH needs both coefficients and renderer capability', () => {
  const raw = baseSnapshot();
  raw.reconstruction.sh_degree = 1;
  raw.reconstruction.attributes.push(...Array.from({ length: 9 }, (_, i) => `f_rest_${i}`));
  raw.reconstruction.renderer_capabilities.push('spherical-harmonics');
  assert.equal(normalizeSnapshot(raw).derived.renderFidelity, 'gaussian-splat-sh');

  raw.reconstruction.attributes.pop();
  assert.equal(normalizeSnapshot(raw).derived.renderFidelity, 'gaussian-splat-dc');
});

test('illegal five-axis step combinations are normalized to fail closed', () => {
  const result = normalizeStepState({
    availability: 'missing',
    execution: 'succeeded',
    freshness: 'current',
    preview: 'ready',
    trust: 'verified',
  });
  assert.deepEqual(result.state, {
    availability: 'missing',
    execution: 'idle',
    freshness: 'stale',
    preview: 'unloaded',
    trust: 'untrusted',
  });
  assert.ok(result.diagnostics.length > 0);
});

test('exactly one global primary action is derived', () => {
  const empty = baseSnapshot();
  empty.sources = { images: 0, videos: 0 };
  const running = baseSnapshot();
  running.active_run = { status: 'running', command: 'reconstruct' };
  const cases = [
    [{ ...baseSnapshot(), adapter: { kind: 'local', connected: false } }, 'reconnect'],
    [{ ...baseSnapshot(), active_run: { status: 'failed' } }, 'inspect-failure'],
    [running, 'view-progress'],
    [empty, 'inspect-sources'],
    [baseSnapshot(), 'review'],
  ];
  for (const [snapshot, expected] of cases) {
    const action = derivePrimaryAction(normalizeSnapshot(snapshot));
    assert.equal(action.id, expected);
    assert.equal(typeof action.label, 'string');
  }
});
