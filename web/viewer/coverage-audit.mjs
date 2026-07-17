export const COVERAGE_AUDIT_SCHEMA_VERSION =
  'nantai.synthetic-village.coverage-audit.v1';

export const COVERAGE_STATUS_COLORS = Object.freeze({
  unknown: '#9aa4ad',
  'diagnostic-unvalidated': '#ffbf47',
  pass: '#7fff7f',
  fail: '#ff6b6b',
});

const SHA256 = /^[0-9a-f]{64}$/;
const CALIBRATION_STATUSES = new Set([
  'diagnostic-unvalidated',
  'calibrated',
]);
const EVIDENCE_STATUSES = new Set([
  'unknown',
  'diagnostic-unvalidated',
  'pass',
  'fail',
]);
const RELEASE_STATUSES = new Set(['unknown', 'pass', 'fail']);

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isFiniteInRange(value, min, max) {
  return Number.isFinite(value) && value >= min && value <= max;
}

function validDigest(value) {
  return typeof value === 'string' && SHA256.test(value);
}

function validEvidenceSection(section) {
  if (section === undefined) return true;
  return (
    isRecord(section)
    && EVIDENCE_STATUSES.has(section.status)
    && (section.label === undefined || typeof section.label === 'string')
  );
}

function validGeometry(geometry) {
  if (geometry === undefined) return true;
  if (!isRecord(geometry)) return false;
  if (!Number.isInteger(geometry.camera_count) || geometry.camera_count < 0) return false;
  if (
    geometry.s2_s1 !== undefined
    && geometry.s2_s1 !== null
    && !isFiniteInRange(geometry.s2_s1, 0, 1)
  ) return false;
  if (
    geometry.s3_s1 !== undefined
    && geometry.s3_s1 !== null
    && !isFiniteInRange(geometry.s3_s1, 0, 1)
  ) return false;
  if (geometry.camera_count < 4 && geometry.s3_s1 != null) return false;
  return true;
}

function validComponent(component) {
  if (!isRecord(component)) return false;
  if (typeof component.component_id !== 'string' || component.component_id.length === 0) {
    return false;
  }
  if (
    component.eligible_camera_count !== undefined
    && (
      !Number.isInteger(component.eligible_camera_count)
      || component.eligible_camera_count < 0
    )
  ) return false;
  if (
    component.observed_normal_angular_spread_deg !== undefined
    && component.observed_normal_angular_spread_deg !== null
    && !isFiniteInRange(component.observed_normal_angular_spread_deg, 0, 180)
  ) return false;
  return validGeometry(component.geometry);
}

/** Validate only machine-verifiable coverage fields; never infer trust from names. */
export function isCoverageAudit(value) {
  if (!isRecord(value)) return false;
  if (value.schema_version !== COVERAGE_AUDIT_SCHEMA_VERSION) return false;

  const source = value.source;
  if (!isRecord(source) || typeof source.synthetic !== 'boolean') return false;
  if (!validDigest(source.camera_registry_sha256)) return false;
  if (!validDigest(source.component_registry_sha256)) return false;
  if (!validDigest(source.render_journal_sha256)) return false;

  const policy = value.policy;
  if (!isRecord(policy)) return false;
  if (typeof policy.policy_id !== 'string' || policy.policy_id.length === 0) return false;
  if (!CALIBRATION_STATUSES.has(policy.calibration_status)) return false;
  if (!Number.isInteger(policy.min_pixels_per_camera) || policy.min_pixels_per_camera < 1) {
    return false;
  }
  if (!isFiniteInRange(policy.min_fraction_per_camera, 0, 1)) return false;
  if (!Number.isInteger(policy.min_camera_count) || policy.min_camera_count < 1) return false;
  if (typeof policy.basis !== 'string' || policy.basis.length === 0) return false;

  if (!Array.isArray(value.diagnostic_sweep)) return false;
  if (!Array.isArray(value.components) || !value.components.every(validComponent)) return false;

  if (value.evidence !== undefined) {
    if (!isRecord(value.evidence)) return false;
    for (const layer of ['visibility', 'geometry', 'sfm', 'provenance']) {
      if (!validEvidenceSection(value.evidence[layer])) return false;
    }
  }
  if (
    value.release_decision !== undefined
    && (
      !isRecord(value.release_decision)
      || !RELEASE_STATUSES.has(value.release_decision.status)
    )
  ) return false;

  return true;
}

function layer(status, label) {
  return {
    status,
    color: COVERAGE_STATUS_COLORS[status],
    label,
  };
}

function unknownModel(summary = 'coverage audit not loaded') {
  return {
    status: 'unknown',
    color: COVERAGE_STATUS_COLORS.unknown,
    summary,
    policy: null,
    layers: {
      visibility: layer('unknown', 'unknown'),
      geometry: layer('unknown', 'unknown'),
      sfm: layer('unknown', 'unknown'),
      provenance: layer('unknown', 'unknown'),
    },
  };
}

function evidenceLayer(section, fallbackStatus, fallbackLabel) {
  if (!section) return layer(fallbackStatus, fallbackLabel);
  return layer(section.status, section.label || section.status);
}

function normalSpreadLabel(components) {
  const values = components
    .map((component) => component.observed_normal_angular_spread_deg)
    .filter(Number.isFinite);
  if (values.length === 0) return '';
  const min = Math.min(...values).toFixed(1);
  const max = Math.max(...values).toFixed(1);
  return min === max
    ? ` · observed normal span ${min}°`
    : ` · observed normal span ${min}–${max}°`;
}

/**
 * Derive honest Viewer language from a valid audit.
 *
 * A diagnostic visibility sweep is amber. Green requires a calibrated policy,
 * an explicit release pass, and explicit pass states for visibility, camera
 * geometry, and measured SfM support.
 */
export function coverageAuditViewModel(audit) {
  if (audit == null) return unknownModel();
  if (!isCoverageAudit(audit)) return unknownModel('unknown · invalid audit');

  const evidence = audit.evidence ?? {};
  const diagnostic = audit.policy.calibration_status === 'diagnostic-unvalidated';
  const visibilityFallback = diagnostic ? 'diagnostic-unvalidated' : 'unknown';
  const visibilityLabel = (
    `渲染可见 · ${audit.components.length} components`
    + ` · ${audit.policy.min_camera_count}+ cameras`
    + normalSpreadLabel(audit.components)
  );
  const hasGeometry = audit.components.some((component) => component.geometry);

  const layers = {
    visibility: evidenceLayer(
      evidence.visibility,
      visibilityFallback,
      visibilityLabel,
    ),
    geometry: evidenceLayer(
      evidence.geometry,
      hasGeometry && diagnostic ? 'diagnostic-unvalidated' : 'unknown',
      hasGeometry ? 'camera geometry diagnostic' : 'unknown',
    ),
    sfm: evidenceLayer(evidence.sfm, 'unknown', 'unknown'),
    provenance: evidenceLayer(
      evidence.provenance,
      diagnostic ? 'diagnostic-unvalidated' : 'unknown',
      `${audit.source.synthetic ? 'synthetic' : 'real'} · digests declared`,
    ),
  };

  const explicitFailure = (
    audit.release_decision?.status === 'fail'
    || Object.values(evidence).some((entry) => entry?.status === 'fail')
  );
  let status;
  let summary;
  if (explicitFailure) {
    status = 'fail';
    summary = 'evidence fail';
  } else if (diagnostic) {
    status = 'diagnostic-unvalidated';
    summary = `${visibilityLabel} · diagnostic-unvalidated`;
  } else {
    const allRequiredEvidencePassed = (
      evidence.visibility?.status === 'pass'
      && evidence.geometry?.status === 'pass'
      && evidence.sfm?.status === 'pass'
    );
    if (audit.release_decision?.status === 'pass' && allRequiredEvidencePassed) {
      status = 'pass';
      summary = 'calibrated evidence pass';
    } else {
      status = 'unknown';
      summary = 'unknown · calibrated policy has incomplete evidence';
    }
  }

  return {
    status,
    color: COVERAGE_STATUS_COLORS[status],
    summary,
    policy: {
      id: audit.policy.policy_id,
      calibration_status: audit.policy.calibration_status,
      min_pixels_per_camera: audit.policy.min_pixels_per_camera,
      min_fraction_per_camera: audit.policy.min_fraction_per_camera,
      min_camera_count: audit.policy.min_camera_count,
    },
    layers,
  };
}
