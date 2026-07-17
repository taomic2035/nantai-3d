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

function coreAudit(overrides = {}) {
  const threshold = {
    min_pixels: 590,
    min_cameras: 3,
    comparison: 'pixels-greater-or-equal',
    frame_pixel_count: 589824,
    min_frame_fraction: 590 / 589824,
  };
  const components = [{
    object_id: 'stone-bridge-01',
    instance_id: 17,
    semantic_class: 'bridge',
    observations: [{
      camera_id: 'camera-outer-001',
      pixels: 800,
      frame_fraction: 800 / 589824,
      meets_threshold: true,
      mean_unit_normal_xyz: [1, 0, 0],
    }],
    observed_camera_count: 1,
    qualifying_camera_count: 1,
    meets_threshold: false,
    azimuth: {
      semantics: 'camera-azimuth-around-component-center-not-facade-coverage',
      center_source: 'village-canary.glb:extras.nv_source_transform',
      qualifying_camera_azimuths_deg: [30],
      max_gap_deg: 360,
    },
    orientation_coverage: 'unknown',
    orientation_unknown_reason: 'component orientation is not declared',
    normal_spread: {
      semantics: 'observed-surface-normal-angular-spread-not-facade-identity',
      normal_source: 'renders/normal/<camera_id>.exr:X,Y,Z-world-space-unit-vector',
      qualifying_camera_normal_count: 1,
      observed_normal_angular_spread_deg: null,
      unknown_reason: 'fewer than two qualifying camera normals',
    },
  }];
  return {
    schema_version: 'nantai.synthetic-village.coverage-audit.v1',
    evidence_sha256: 'd'.repeat(64),
    synthetic: true,
    verification_level: 'L2',
    fidelity: 'simplified-pbr-not-render-parity',
    trust_effect: 'audit-only-no-trust-elevation',
    render_id: 'e'.repeat(64),
    build_id: 'f'.repeat(64),
    journal_sha256: '1'.repeat(64),
    object_registry_sha256: '2'.repeat(64),
    build_report_sha256: '4'.repeat(64),
    glb_sha256: '5'.repeat(64),
    threshold,
    mask_digests: [{
      camera_id: 'camera-outer-001',
      path: 'instance/camera-outer-001.png',
      sha256: '3'.repeat(64),
    }],
    camera_metadata_digests: [{
      camera_id: 'camera-outer-001',
      path: 'cameras/camera-outer-001.json',
      sha256: '6'.repeat(64),
    }],
    normal_digests: [{
      camera_id: 'camera-outer-001',
      path: 'normal/camera-outer-001.exr',
      sha256: '7'.repeat(64),
    }],
    normal_unit_length_tolerance: 0.001,
    camera_centers: [{
      camera_id: 'camera-outer-001',
      center_source: 'renders/cameras/<camera_id>.json:measured_c2w_blender',
      center_xy_m: [10, 20],
    }],
    instance_ids_crosscheck: {
      agrees: true,
      declared_only: [],
      observed_only: [],
    },
    components,
    summary: {
      component_count: 1,
      components_meeting_threshold: 0,
      components_never_observed: 0,
      frames_audited: 1,
    },
    audit_duration_seconds: 0.25,
    ...overrides,
  };
}

function twoNormalCoreAudit() {
  const audit = coreAudit();
  audit.mask_digests.push({
    camera_id: 'camera-outer-002',
    path: 'instance/camera-outer-002.png',
    sha256: '8'.repeat(64),
  });
  audit.camera_metadata_digests.push({
    camera_id: 'camera-outer-002',
    path: 'cameras/camera-outer-002.json',
    sha256: '9'.repeat(64),
  });
  audit.normal_digests.push({
    camera_id: 'camera-outer-002',
    path: 'normal/camera-outer-002.exr',
    sha256: 'a'.repeat(64),
  });
  audit.camera_centers.push({
    camera_id: 'camera-outer-002',
    center_source: 'renders/cameras/<camera_id>.json:measured_c2w_blender',
    center_xy_m: [20, 10],
  });
  const component = audit.components[0];
  component.observations.push({
    camera_id: 'camera-outer-002',
    pixels: 900,
    frame_fraction: 900 / 589824,
    meets_threshold: true,
    mean_unit_normal_xyz: [0, 1, 0],
  });
  component.observed_camera_count = 2;
  component.qualifying_camera_count = 2;
  component.azimuth.qualifying_camera_azimuths_deg = [30, 120];
  component.azimuth.max_gap_deg = 270;
  component.normal_spread = {
    semantics: 'observed-surface-normal-angular-spread-not-facade-identity',
    normal_source: 'renders/normal/<camera_id>.exr:X,Y,Z-world-space-unit-vector',
    qualifying_camera_normal_count: 2,
    observed_normal_angular_spread_deg: 90,
    unknown_reason: null,
  };
  audit.summary.frames_audited = 2;
  return audit;
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

test('core pixel-mask report is accepted only as uncalibrated visibility diagnostic', () => {
  const { coverageAuditViewModel, isCoverageAudit } = subject();
  const audit = coreAudit();

  assert.equal(isCoverageAudit(audit), true);
  const model = coverageAuditViewModel(audit);
  assert.equal(model.status, 'diagnostic-unvalidated');
  assert.match(model.summary, /渲染可见/);
  assert.match(model.layers.visibility.label, /0\/1 components/);
  assert.match(model.layers.visibility.label, />=590 px/);
  assert.equal(model.layers.geometry.status, 'unknown');
  assert.match(model.layers.geometry.label, /azimuth is not facade evidence/i);
  assert.equal(model.layers.sfm.status, 'unknown');
  assert.equal(model.layers.provenance.status, 'diagnostic-unvalidated');
});

test('core report anchors every coverage input before accepting derived evidence', () => {
  const { isCoverageAudit } = subject();
  const valid = coreAudit();
  assert.equal(isCoverageAudit(valid), true);

  const mutations = [
    (audit) => { delete audit.build_report_sha256; },
    (audit) => { audit.glb_sha256 = null; },
    (audit) => { audit.camera_metadata_digests[0].sha256 = 'not-a-digest'; },
    (audit) => { audit.normal_digests[0].camera_id = 'camera-ground-001'; },
    (audit) => { audit.camera_centers[0].center_source = 'filename-inference'; },
  ];
  for (const mutate of mutations) {
    const invalid = structuredClone(valid);
    mutate(invalid);
    assert.equal(isCoverageAudit(invalid), false);
  }
});

test('core normal evidence requires finite unit vectors and explicit unknowns', () => {
  const { isCoverageAudit } = subject();
  const valid = coreAudit();
  assert.equal(isCoverageAudit(valid), true);

  const nonUnit = structuredClone(valid);
  nonUnit.components[0].observations[0].mean_unit_normal_xyz = [2, 0, 0];
  assert.equal(isCoverageAudit(nonUnit), false);

  const missingSpread = structuredClone(valid);
  delete missingSpread.components[0].normal_spread;
  assert.equal(isCoverageAudit(missingSpread), false);

  const fakeMeasuredZero = structuredClone(valid);
  fakeMeasuredZero.components[0].normal_spread.observed_normal_angular_spread_deg = 0;
  fakeMeasuredZero.components[0].normal_spread.unknown_reason = null;
  assert.equal(isCoverageAudit(fakeMeasuredZero), false);
});

test('core normal evidence re-derives the declared angular span', () => {
  const { isCoverageAudit } = subject();
  const valid = twoNormalCoreAudit();
  assert.equal(isCoverageAudit(valid), true);

  const lyingCount = structuredClone(valid);
  lyingCount.components[0].normal_spread.qualifying_camera_normal_count = 1;
  assert.equal(isCoverageAudit(lyingCount), false);

  const lyingSpan = structuredClone(valid);
  lyingSpan.components[0].normal_spread.observed_normal_angular_spread_deg = 89;
  assert.equal(isCoverageAudit(lyingSpan), false);
});

test('core HUD presents observed normal span without claiming facade identity', () => {
  const { coverageAuditViewModel } = subject();
  const model = coverageAuditViewModel(twoNormalCoreAudit());

  assert.equal(model.layers.geometry.status, 'unknown');
  assert.match(model.layers.geometry.label, /observed surface normal span 90\.0/);
  assert.match(model.layers.geometry.label, /not facade identity/);
  assert.doesNotMatch(model.layers.geometry.label, /front|back|360.?coverage/i);
});

test('core report crosscheck disagreement is explicit evidence failure', () => {
  const { coverageAuditViewModel, isCoverageAudit } = subject();
  const audit = coreAudit({
    instance_ids_crosscheck: {
      agrees: false,
      declared_only: ['camera-outer-001:17'],
      observed_only: [],
    },
  });

  assert.equal(isCoverageAudit(audit), true);
  const model = coverageAuditViewModel(audit);
  assert.equal(model.status, 'fail');
  assert.equal(model.layers.visibility.status, 'fail');
  assert.match(model.summary, /instance_ids crosscheck failed/i);
});

test('core report rejects derived observation and summary contradictions', () => {
  const { isCoverageAudit } = subject();
  const inconsistentObservation = coreAudit();
  inconsistentObservation.components[0].observations[0].meets_threshold = false;
  const inconsistentSummary = coreAudit({
    summary: {
      component_count: 99,
      components_meeting_threshold: 0,
      components_never_observed: 0,
      frames_audited: 1,
    },
  });

  assert.equal(isCoverageAudit(inconsistentObservation), false);
  assert.equal(isCoverageAudit(inconsistentSummary), false);
});
