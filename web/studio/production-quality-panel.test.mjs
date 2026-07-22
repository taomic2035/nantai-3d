import assert from 'node:assert/strict';
import test from 'node:test';

let panelModule;
try {
  panelModule = await import('./production-quality-panel.mjs');
} catch (error) {
  panelModule = { __loadError: error };
}

function subject() {
  assert.equal(
    panelModule.__loadError,
    undefined,
    `production-quality-panel.mjs must load: ${panelModule.__loadError?.message}`,
  );
  return panelModule;
}

function availableEvidence() {
  return {
    schema_version: 1,
    status: 'available',
    message: 'geometry preflight pass is not final frame pass',
    synthetic: true,
    verification_level: 'L0',
    trust_effect: 'none-quality-filter-only',
    report_sha256: 'a'.repeat(64),
    stages: [
      { id: 'preflight', state: 'passed' },
      { id: 'rendering', state: 'completed' },
      { id: 'post-render-quality', state: 'rejected' },
    ],
    cameras: [{
      camera_id: 'camera-ground-route-034',
      state: 'rejected',
      runtime_report_sha256: 'b'.repeat(64),
      statistics_sha256: 'c'.repeat(64),
      policy_sha256: 'd'.repeat(64),
      rules: [{
        rule_id: 'upper-instance-dominance',
        measured: 0.82,
        operator_threshold: 0.7,
        comparison_direction: 'maximum',
        passes: false,
      }],
    }],
  };
}

function reciprocalBatchEvidence() {
  return {
    schema_version: 1,
    status: 'available',
    message: 'geometry preflight pass is not final frame pass',
    synthetic: true,
    verification_level: 'L0',
    trust_effect: 'none-quality-filter-only',
    report_sha256: null,
    evidence_kind: 'reciprocal-six-role-batch',
    evidence_sha256: 'e'.repeat(64),
    batch_id: 'f'.repeat(64),
    stages: [
      { id: 'preflight', state: 'passed' },
      { id: 'rendering', state: 'completed' },
      { id: 'post-render-quality', state: 'rejected' },
    ],
    cameras: [{
      entry_id: 'central-courtyard-downhill',
      role_module_id: 'central-courtyard-downhill',
      camera_id: 'camera-ground-route-010',
      render_id: null,
      state: 'failed',
      runtime_report_sha256: null,
      statistics_sha256: null,
      policy_sha256: 'd'.repeat(64),
      quality_report_sha256: null,
      error_code: 'post-render-quality-rejected',
      error_message: 'post-render quality rejected camera: camera-ground-route-010',
      rules: [],
    }, {
      entry_id: 'bridge-deck-crossing',
      role_module_id: 'bridge-deck-crossing',
      camera_id: 'camera-ground-route-039',
      render_id: '1'.repeat(64),
      state: 'passed',
      runtime_report_sha256: '2'.repeat(64),
      statistics_sha256: '3'.repeat(64),
      policy_sha256: 'd'.repeat(64),
      quality_report_sha256: '4'.repeat(64),
      error_code: null,
      error_message: null,
      rules: [{
        rule_id: 'upper-ground-dominance',
        measured: 0.2,
        operator_threshold: 0.3,
        comparison_direction: 'maximum',
        passes: true,
      }],
    }],
  };
}

test('loader keeps honest absence as awaiting evidence', async () => {
  const { loadProductionQualityEvidence } = subject();
  const evidence = await loadProductionQualityEvidence({
    fetchImpl: async (url, options) => {
      assert.equal(url, '/api/production-quality');
      assert.deepEqual(options, { cache: 'no-store' });
      return {
        ok: true,
        json: async () => ({
          schema_version: 1,
          status: 'awaiting-evidence',
          message: 'geometry preflight pass is not final frame pass',
          synthetic: true,
          verification_level: null,
          trust_effect: 'none-quality-filter-only',
          report_sha256: null,
          stages: [
            { id: 'preflight', state: 'awaiting-evidence' },
            { id: 'rendering', state: 'awaiting-evidence' },
            { id: 'post-render-quality', state: 'awaiting-evidence' },
          ],
          cameras: [],
        }),
      };
    },
  });

  assert.equal(evidence.status, 'awaiting-evidence');
  assert.equal(evidence.cameras.length, 0);
});

test('normalizer rejects unbound or trust-promoting payloads fail closed', () => {
  const { normalizeProductionQualityEvidence } = subject();
  const forged = availableEvidence();
  forged.synthetic = false;
  forged.trust_effect = 'promote';
  forged.cameras[0].rules[0].measured = Number.NaN;

  const normalized = normalizeProductionQualityEvidence(forged);

  assert.equal(normalized.status, 'invalid-evidence');
  assert.equal(normalized.verification_level, null);
  assert.equal(normalized.report_sha256, null);
  assert.deepEqual(normalized.cameras, []);
});

test('panel shows stages, camera state, rules, hashes, and trust limits', () => {
  const { renderProductionQualityPanel } = subject();
  const html = renderProductionQualityPanel(availableEvidence());

  assert.match(html, /preflight[\s\S]*passed/);
  assert.match(html, /rendering[\s\S]*completed/);
  assert.match(html, /post-render-quality[\s\S]*rejected/);
  assert.match(html, /camera-ground-route-034/);
  assert.match(html, /upper-instance-dominance/);
  assert.match(html, /0\.82/);
  assert.match(html, /maximum\s+0\.7/);
  assert.match(html, /none-quality-filter-only/);
  assert.match(html, /synthetic/);
  assert.match(html, /L0/);
  assert.match(html, new RegExp('a'.repeat(64)));
  assert.match(html, new RegExp('b'.repeat(64)));
  assert.match(html, /geometry preflight pass is not final frame pass/);
});

test('camera selector renders one auditable camera at a time', () => {
  const { renderProductionQualityPanel } = subject();
  const evidence = availableEvidence();
  evidence.cameras.push({
    ...structuredClone(evidence.cameras[0]),
    camera_id: 'camera-ground-route-035',
    state: 'passed',
    rules: [{
      ...structuredClone(evidence.cameras[0].rules[0]),
      measured: 0.6,
      passes: true,
    }],
  });

  const html = renderProductionQualityPanel(
    evidence,
    'camera-ground-route-035',
  );

  assert.match(
    html,
    /option value="camera-ground-route-035" selected>camera-ground-route-035 · passed/,
  );
  assert.doesNotMatch(html, /<h4>camera-ground-route-034<\/h4>/);
  assert.match(html, /<h4>camera-ground-route-035<\/h4>/);
});

test('reciprocal batch keeps role identity and evidence-free failure honest', () => {
  const { normalizeProductionQualityEvidence, renderProductionQualityPanel } = subject();
  const evidence = normalizeProductionQualityEvidence(reciprocalBatchEvidence());

  assert.equal(evidence.status, 'available');
  assert.equal(evidence.evidence_kind, 'reciprocal-six-role-batch');
  assert.equal(evidence.cameras[0].entry_id, 'central-courtyard-downhill');
  assert.equal(evidence.cameras[0].state, 'failed');
  assert.equal(evidence.cameras[0].runtime_report_sha256, null);
  assert.deepEqual(evidence.cameras[0].rules, []);

  const html = renderProductionQualityPanel(
    evidence,
    'central-courtyard-downhill',
  );
  assert.match(html, /central-courtyard-downhill/);
  assert.match(html, /camera-ground-route-010/);
  assert.match(html, /post-render-quality-rejected/);
  assert.match(html, new RegExp('e'.repeat(64)));
  assert.doesNotMatch(html, /undefined/);
});
