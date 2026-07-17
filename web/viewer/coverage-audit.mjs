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

function stringArray(value) {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function nearlyEqual(left, right) {
  return Math.abs(left - right) <= 1e-12;
}

function validCoreThreshold(threshold) {
  return (
    isRecord(threshold)
    && Number.isInteger(threshold.min_pixels)
    && threshold.min_pixels >= 1
    && Number.isInteger(threshold.min_cameras)
    && threshold.min_cameras >= 1
    && threshold.comparison === 'pixels-greater-or-equal'
    && Number.isInteger(threshold.frame_pixel_count)
    && threshold.frame_pixel_count >= threshold.min_pixels
    && isFiniteInRange(threshold.min_frame_fraction, 0, 1)
    && nearlyEqual(
      threshold.min_frame_fraction,
      threshold.min_pixels / threshold.frame_pixel_count,
    )
  );
}

function validCoreObservation(observation, threshold) {
  return (
    isRecord(observation)
    && typeof observation.camera_id === 'string'
    && observation.camera_id.length > 0
    && Number.isInteger(observation.pixels)
    && observation.pixels >= 1
    && observation.pixels <= threshold.frame_pixel_count
    && isFiniteInRange(observation.frame_fraction, 0, 1)
    && nearlyEqual(
      observation.frame_fraction,
      observation.pixels / threshold.frame_pixel_count,
    )
    && observation.meets_threshold === (observation.pixels >= threshold.min_pixels)
  );
}

function validCoreAzimuth(azimuth) {
  if (azimuth === undefined || azimuth === null) return true;
  return (
    isRecord(azimuth)
    && azimuth.semantics
      === 'camera-azimuth-around-component-center-not-facade-coverage'
    && azimuth.center_source === 'village-canary.glb:extras.nv_source_transform'
    && Array.isArray(azimuth.qualifying_camera_azimuths_deg)
    && azimuth.qualifying_camera_azimuths_deg.every(
      (angle) => isFiniteInRange(angle, 0, 360) && angle < 360,
    )
    && (
      azimuth.max_gap_deg === null
      || isFiniteInRange(azimuth.max_gap_deg, 0, 360)
    )
  );
}

function validCoreComponent(component, threshold) {
  if (!isRecord(component)) return false;
  if (typeof component.object_id !== 'string' || component.object_id.length === 0) return false;
  if (!Number.isInteger(component.instance_id) || component.instance_id < 1) return false;
  if (typeof component.semantic_class !== 'string' || component.semantic_class.length === 0) {
    return false;
  }
  if (
    !Array.isArray(component.observations)
    || !component.observations.every(
      (observation) => validCoreObservation(observation, threshold),
    )
  ) return false;
  const cameraIds = component.observations.map((observation) => observation.camera_id);
  if (new Set(cameraIds).size !== cameraIds.length) return false;
  const qualifying = component.observations.filter(
    (observation) => observation.meets_threshold,
  ).length;
  if (component.observed_camera_count !== component.observations.length) return false;
  if (component.qualifying_camera_count !== qualifying) return false;
  if (component.meets_threshold !== (qualifying >= threshold.min_cameras)) return false;
  if (component.orientation_coverage !== 'unknown') return false;
  if (
    typeof component.orientation_unknown_reason !== 'string'
    || component.orientation_unknown_reason.length === 0
  ) return false;
  return validCoreAzimuth(component.azimuth);
}

function isCoreCoverageAudit(value) {
  if (!isRecord(value) || value.schema_version !== COVERAGE_AUDIT_SCHEMA_VERSION) return false;
  if (!validDigest(value.evidence_sha256)) return false;
  if (value.synthetic !== true || value.verification_level !== 'L2') return false;
  if (value.fidelity !== 'simplified-pbr-not-render-parity') return false;
  if (value.trust_effect !== 'audit-only-no-trust-elevation') return false;
  for (const digest of [
    value.render_id,
    value.build_id,
    value.journal_sha256,
    value.object_registry_sha256,
  ]) {
    if (!validDigest(digest)) return false;
  }
  if (!validCoreThreshold(value.threshold)) return false;
  if (
    !Array.isArray(value.mask_digests)
    || !value.mask_digests.every((item) => (
      isRecord(item)
      && typeof item.camera_id === 'string'
      && item.camera_id.length > 0
      && typeof item.path === 'string'
      && item.path.length > 0
      && validDigest(item.sha256)
    ))
  ) return false;
  const maskCameraIds = value.mask_digests.map((item) => item.camera_id);
  if (new Set(maskCameraIds).size !== maskCameraIds.length) return false;

  const crosscheck = value.instance_ids_crosscheck;
  if (
    !isRecord(crosscheck)
    || typeof crosscheck.agrees !== 'boolean'
    || !stringArray(crosscheck.declared_only)
    || !stringArray(crosscheck.observed_only)
  ) return false;
  const crosscheckArraysEmpty = (
    crosscheck.declared_only.length === 0
    && crosscheck.observed_only.length === 0
  );
  if (crosscheck.agrees !== crosscheckArraysEmpty) return false;

  if (
    !Array.isArray(value.components)
    || !value.components.every(
      (component) => validCoreComponent(component, value.threshold),
    )
  ) return false;
  const instanceIds = value.components.map((component) => component.instance_id);
  if (new Set(instanceIds).size !== instanceIds.length) return false;

  const summary = value.summary;
  if (!isRecord(summary)) return false;
  if (summary.component_count !== value.components.length) return false;
  if (
    summary.components_meeting_threshold
    !== value.components.filter((component) => component.meets_threshold).length
  ) return false;
  if (
    summary.components_never_observed
    !== value.components.filter((component) => component.observations.length === 0).length
  ) return false;
  if (summary.frames_audited !== value.mask_digests.length) return false;
  return Number.isFinite(value.audit_duration_seconds) && value.audit_duration_seconds >= 0;
}

/** Validate only machine-verifiable coverage fields; never infer trust from names. */
function isPolicyCoverageAudit(value) {
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

export function isCoverageAudit(value) {
  return isPolicyCoverageAudit(value) || isCoreCoverageAudit(value);
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

function coreCoverageAuditViewModel(audit) {
  const threshold = audit.threshold;
  const visibilityLabel = (
    `渲染可见 · ${audit.summary.components_meeting_threshold}`
    + `/${audit.summary.component_count} components`
    + ` · >=${threshold.min_pixels} px in >=${threshold.min_cameras} cameras`
  );
  const crosscheckFailed = !audit.instance_ids_crosscheck.agrees;
  const status = crosscheckFailed ? 'fail' : 'diagnostic-unvalidated';
  const summary = crosscheckFailed
    ? 'instance_ids crosscheck failed'
    : `${visibilityLabel} · diagnostic-unvalidated`;
  const hasAzimuth = audit.components.some((component) => component.azimuth);
  return {
    status,
    color: COVERAGE_STATUS_COLORS[status],
    summary,
    policy: {
      id: null,
      calibration_status: 'diagnostic-unvalidated',
      min_pixels_per_camera: threshold.min_pixels,
      min_fraction_per_camera: threshold.min_frame_fraction,
      min_camera_count: threshold.min_cameras,
    },
    layers: {
      visibility: layer(
        status,
        crosscheckFailed ? 'instance_ids crosscheck failed' : visibilityLabel,
      ),
      geometry: layer(
        'unknown',
        hasAzimuth ? 'unknown · azimuth is not facade evidence' : 'unknown',
      ),
      sfm: layer('unknown', 'unknown · no measured SfM evidence'),
      provenance: layer(
        'diagnostic-unvalidated',
        `${audit.synthetic ? 'synthetic' : 'real'} · ${audit.verification_level}`
          + ` · ${audit.trust_effect}`,
      ),
    },
  };
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
  if (isCoreCoverageAudit(audit)) return coreCoverageAuditViewModel(audit);

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
