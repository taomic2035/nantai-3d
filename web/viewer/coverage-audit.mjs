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
const CAMERA_CENTER_SOURCE =
  'renders/cameras/<camera_id>.json:measured_c2w_blender';
const NORMAL_SPREAD_SEMANTICS =
  'observed-surface-normal-angular-spread-not-facade-identity';
const NORMAL_SOURCE =
  'renders/normal/<camera_id>.exr:X,Y,Z-world-space-unit-vector';

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isFiniteInRange(value, min, max) {
  return Number.isFinite(value) && value >= min && value <= max;
}

function validDigest(value) {
  return typeof value === 'string' && SHA256.test(value);
}

function validEvidenceDigests(value) {
  if (
    !Array.isArray(value)
    || !value.every((item) => (
      isRecord(item)
      && typeof item.camera_id === 'string'
      && item.camera_id.length > 0
      && typeof item.path === 'string'
      && item.path.length > 0
      && validDigest(item.sha256)
    ))
  ) return false;
  const cameraIds = value.map((item) => item.camera_id);
  return new Set(cameraIds).size === cameraIds.length;
}

function sameStringSet(left, right) {
  if (left.size !== right.size) return false;
  return [...left].every((value) => right.has(value));
}

function validCoreCameraCenters(value, cameraIds) {
  if (
    !Array.isArray(value)
    || !value.every((item) => (
      isRecord(item)
      && typeof item.camera_id === 'string'
      && cameraIds.has(item.camera_id)
      && item.center_source === CAMERA_CENTER_SOURCE
      && Array.isArray(item.center_xy_m)
      && item.center_xy_m.length === 2
      && item.center_xy_m.every(Number.isFinite)
    ))
  ) return false;
  const centerIds = value.map((item) => item.camera_id);
  return new Set(centerIds).size === centerIds.length;
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

function validUnitVector(vector, tolerance) {
  if (
    !Array.isArray(vector)
    || vector.length !== 3
    || !vector.every(Number.isFinite)
  ) return false;
  const length = Math.hypot(...vector);
  return Math.abs(length - 1) <= tolerance;
}

function maxPairwiseNormalAngleDeg(vectors) {
  if (vectors.length < 2) return null;
  let widest = 0;
  for (let first = 0; first < vectors.length; first += 1) {
    for (let second = first + 1; second < vectors.length; second += 1) {
      const dot = vectors[first].reduce(
        (sum, value, index) => sum + value * vectors[second][index],
        0,
      );
      const denominator = Math.hypot(...vectors[first]) * Math.hypot(...vectors[second]);
      const cosine = Math.max(-1, Math.min(1, dot / denominator));
      widest = Math.max(widest, Math.acos(cosine) * 180 / Math.PI);
    }
  }
  return Math.round((widest + Number.EPSILON) * 1000) / 1000;
}

function validCoreObservation(observation, threshold, normalTolerance) {
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
    && (
      observation.mean_unit_normal_xyz === null
      || validUnitVector(observation.mean_unit_normal_xyz, normalTolerance)
    )
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

function validCoreNormalSpread(component, threshold) {
  const spread = component.normal_spread;
  if (
    !isRecord(spread)
    || spread.semantics !== NORMAL_SPREAD_SEMANTICS
    || spread.normal_source !== NORMAL_SOURCE
    || !Number.isInteger(spread.qualifying_camera_normal_count)
    || spread.qualifying_camera_normal_count < 0
  ) return false;
  const vectors = component.observations
    .filter(
      (observation) => (
        observation.pixels >= threshold.min_pixels
        && observation.mean_unit_normal_xyz !== null
      ),
    )
    .map((observation) => observation.mean_unit_normal_xyz);
  if (spread.qualifying_camera_normal_count !== vectors.length) return false;
  const expected = maxPairwiseNormalAngleDeg(vectors);
  if (expected === null) {
    return (
      spread.observed_normal_angular_spread_deg === null
      && typeof spread.unknown_reason === 'string'
      && spread.unknown_reason.length > 0
    );
  }
  return (
    spread.observed_normal_angular_spread_deg === expected
    && spread.unknown_reason === null
  );
}

function validCoreComponent(component, threshold, normalTolerance) {
  if (!isRecord(component)) return false;
  if (typeof component.object_id !== 'string' || component.object_id.length === 0) return false;
  if (!Number.isInteger(component.instance_id) || component.instance_id < 1) return false;
  if (typeof component.semantic_class !== 'string' || component.semantic_class.length === 0) {
    return false;
  }
  if (
    !Array.isArray(component.observations)
    || !component.observations.every(
      (observation) => validCoreObservation(observation, threshold, normalTolerance),
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
  return (
    validCoreAzimuth(component.azimuth)
    && validCoreNormalSpread(component, threshold)
  );
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
    value.build_report_sha256,
  ]) {
    if (!validDigest(digest)) return false;
  }
  if (value.glb_sha256 !== null && !validDigest(value.glb_sha256)) return false;
  if (!validCoreThreshold(value.threshold)) return false;
  if (!validEvidenceDigests(value.mask_digests)) return false;
  if (!validEvidenceDigests(value.camera_metadata_digests)) return false;
  if (!validEvidenceDigests(value.normal_digests)) return false;
  const maskCameraIds = new Set(value.mask_digests.map((item) => item.camera_id));
  const metadataCameraIds = new Set(
    value.camera_metadata_digests.map((item) => item.camera_id),
  );
  const normalCameraIds = new Set(value.normal_digests.map((item) => item.camera_id));
  if (!sameStringSet(maskCameraIds, metadataCameraIds)) return false;
  if (!sameStringSet(maskCameraIds, normalCameraIds)) return false;
  if (
    !Number.isFinite(value.normal_unit_length_tolerance)
    || value.normal_unit_length_tolerance <= 0
  ) return false;
  if (!validCoreCameraCenters(value.camera_centers, metadataCameraIds)) return false;
  if (value.glb_sha256 === null && value.camera_centers.length > 0) return false;
  const centerCameraIds = new Set(value.camera_centers.map((item) => item.camera_id));

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
      (component) => validCoreComponent(
        component,
        value.threshold,
        value.normal_unit_length_tolerance,
      ),
    )
  ) return false;
  const instanceIds = value.components.map((component) => component.instance_id);
  if (new Set(instanceIds).size !== instanceIds.length) return false;
  const componentsWithAzimuth = value.components.filter((component) => component.azimuth);
  if (componentsWithAzimuth.length > 0 && value.glb_sha256 === null) return false;
  if (
    componentsWithAzimuth.some((component) => component.observations.some(
      (observation) => observation.meets_threshold
        && !centerCameraIds.has(observation.camera_id),
    ))
  ) return false;

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
