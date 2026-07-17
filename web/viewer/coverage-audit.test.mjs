import assert from 'node:assert/strict';
import test from 'node:test';

let auditModule;
try {
  auditModule = await import('./coverage-audit.mjs');
} catch (error) {
  auditModule = { __loadError: error };
}

function subject() {
  assert.equal(
    auditModule.__loadError,
    undefined,
    `coverage-audit.mjs must load: ${auditModule.__loadError?.message}`,
  );
  return auditModule;
}

const SHA = 'a'.repeat(64);

function diagnosticAudit(overrides = {}) {
  return {
    schema_version: 'nantai.synthetic-village.coverage-audit.v1',
    source: {
      synthetic: true,
      camera_registry_sha256: SHA,
      component_registry_sha256: 'b'.repeat(64),
      render_journal_sha256: 'c'.repeat(64),
    },
    policy: {
      policy_id: 'visibility-sweep-v1',
      calibration_status: 'diagnostic-unvalidated',
      min_pixels_per_camera: 1,
      min_fraction_per_camera: 0,
      min_camera_count: 3,
      basis: 'diagnostic thresholds are not calibrated release policy',
    },
    diagnostic_sweep: [],
    components: [],
    ...overrides,
  };
}

test('missing or malformed audits stay unknown', () => {
  const { coverageAuditViewModel } = subject();

  assert.equal(coverageAuditViewModel(null).status, 'unknown');
  const malformed = diagnosticAudit({
    source: {
      ...diagnosticAudit().source,
      render_journal_sha256: 'not-a-digest',
    },
  });
  const model = coverageAuditViewModel(malformed);
  assert.equal(model.status, 'unknown');
  assert.match(model.summary, /invalid audit/i);
});

test('minimum valid uncalibrated audit is diagnostic, never reconstruction-ready', () => {
  const { coverageAuditViewModel, isCoverageAudit } = subject();
  const audit = diagnosticAudit({
    components: [{
      component_id: 'stone-bridge-01',
      eligible_camera_count: 4,
      observed_normal_angular_spread_deg: 174,
    }],
  });

  assert.equal(isCoverageAudit(audit), true);
  const model = coverageAuditViewModel(audit);
  assert.equal(model.status, 'diagnostic-unvalidated');
  assert.match(model.summary, /渲染可见/);
  assert.doesNotMatch(model.summary, /可重建|已覆盖|可测量/);
  assert.equal(model.layers.visibility.status, 'diagnostic-unvalidated');
  assert.match(model.layers.visibility.label, /174\.0°/);
  assert.equal(model.layers.geometry.status, 'unknown');
  assert.equal(model.layers.sfm.status, 'unknown');
});

test('a convenient three-view scalar cannot become release evidence', () => {
  const { coverageAuditViewModel } = subject();
  const audit = diagnosticAudit({
    components_with_three_view_support: 126,
  });

  const model = coverageAuditViewModel(audit);
  assert.equal(model.status, 'diagnostic-unvalidated');
  assert.doesNotMatch(model.summary, /pass|通过|已覆盖/i);
});

test('calibrated pass remains unknown without every evidence layer', () => {
  const { coverageAuditViewModel } = subject();
  const audit = diagnosticAudit({
    policy: {
      ...diagnosticAudit().policy,
      calibration_status: 'calibrated',
    },
    release_decision: { status: 'pass' },
    evidence: {
      visibility: { status: 'pass' },
    },
  });

  const model = coverageAuditViewModel(audit);
  assert.equal(model.status, 'unknown');
  assert.match(model.summary, /incomplete evidence/i);
});

test('calibrated explicit decision only passes with visibility, geometry, and SfM pass', () => {
  const { coverageAuditViewModel } = subject();
  const audit = diagnosticAudit({
    policy: {
      ...diagnosticAudit().policy,
      calibration_status: 'calibrated',
    },
    release_decision: { status: 'pass' },
    evidence: {
      visibility: { status: 'pass', label: 'visibility policy passed' },
      geometry: { status: 'pass', label: 'baseline policy passed' },
      sfm: { status: 'pass', label: 'measured COLMAP support passed' },
    },
  });

  const model = coverageAuditViewModel(audit);
  assert.equal(model.status, 'pass');
  assert.equal(model.layers.visibility.status, 'pass');
  assert.equal(model.layers.geometry.status, 'pass');
  assert.equal(model.layers.sfm.status, 'pass');
});

test('explicit evidence failure is red even before policy calibration', () => {
  const { coverageAuditViewModel } = subject();
  const audit = diagnosticAudit({
    evidence: {
      visibility: { status: 'fail', label: 'mask digest mismatch' },
    },
  });

  const model = coverageAuditViewModel(audit);
  assert.equal(model.status, 'fail');
  assert.equal(model.layers.visibility.status, 'fail');
  assert.match(model.summary, /evidence fail/i);
});

test('observed normal spread must be a finite angle from 0 through 180 degrees', () => {
  const { isCoverageAudit } = subject();
  const negative = diagnosticAudit({
    components: [{
      component_id: 'wall-01',
      observed_normal_angular_spread_deg: -1,
    }],
  });
  const over = diagnosticAudit({
    components: [{
      component_id: 'wall-01',
      observed_normal_angular_spread_deg: 181,
    }],
  });

  assert.equal(isCoverageAudit(negative), false);
  assert.equal(isCoverageAudit(over), false);
});

test('exactly three camera centers cannot claim a third singular-value ratio', () => {
  const { isCoverageAudit } = subject();
  const invalid = diagnosticAudit({
    components: [{
      component_id: 'bridge-01',
      geometry: {
        camera_count: 3,
        s2_s1: 0.4,
        s3_s1: 0.2,
      },
    }],
  });
  const valid = diagnosticAudit({
    components: [{
      component_id: 'bridge-01',
      geometry: {
        camera_count: 3,
        s2_s1: 0.4,
        s3_s1: null,
      },
    }],
  });

  assert.equal(isCoverageAudit(invalid), false);
  assert.equal(isCoverageAudit(valid), true);
});
