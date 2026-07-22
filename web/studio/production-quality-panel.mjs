const ENDPOINT = '/api/production-quality';
const MESSAGE = 'geometry preflight pass is not final frame pass';
const TRUST_EFFECT = 'none-quality-filter-only';
const STAGE_IDS = ['preflight', 'rendering', 'post-render-quality'];
const SHA256 = /^[0-9a-f]{64}$/;
const CAMERA_ID = /^camera-[a-z0-9-]+-[0-9]{3}$/;
const VALID_STATUS = new Set(['awaiting-evidence', 'invalid-evidence', 'available']);
const VALID_COMPARISON = new Set(['minimum', 'maximum']);
const RECIPROCAL_BATCH_KIND = 'reciprocal-six-role-batch';
const ENTRY_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function invalidEvidence() {
  return {
    schema_version: 1,
    status: 'invalid-evidence',
    message: MESSAGE,
    synthetic: true,
    verification_level: null,
    trust_effect: TRUST_EFFECT,
    report_sha256: null,
    evidence_kind: null,
    evidence_sha256: null,
    batch_id: null,
    stages: STAGE_IDS.map((id) => ({ id, state: 'invalid-evidence' })),
    cameras: [],
  };
}

function finiteRatio(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1;
}

function normalizeRule(rule) {
  if (
    !rule
    || typeof rule.rule_id !== 'string'
    || !rule.rule_id
    || !finiteRatio(rule.measured)
    || !finiteRatio(rule.operator_threshold)
    || !VALID_COMPARISON.has(rule.comparison_direction)
    || typeof rule.passes !== 'boolean'
  ) {
    throw new TypeError('invalid production quality rule');
  }
  const expected = rule.comparison_direction === 'minimum'
    ? rule.measured >= rule.operator_threshold
    : rule.measured <= rule.operator_threshold;
  if (rule.passes !== expected) {
    throw new TypeError('production quality decision disagrees with its numbers');
  }
  return {
    rule_id: rule.rule_id,
    measured: rule.measured,
    operator_threshold: rule.operator_threshold,
    comparison_direction: rule.comparison_direction,
    passes: rule.passes,
  };
}

function normalizeCamera(camera) {
  const entryId = camera?.entry_id ?? camera?.camera_id;
  const roleModuleId = camera?.role_module_id ?? null;
  if (
    !camera
    || !ENTRY_ID.test(entryId)
    || !CAMERA_ID.test(camera.camera_id)
    || !SHA256.test(camera.policy_sha256)
    || !Array.isArray(camera.rules)
  ) {
    throw new TypeError('invalid production quality camera evidence');
  }
  if (['failed', 'planned'].includes(camera.state)) {
    if (
      roleModuleId !== entryId
      || camera.runtime_report_sha256 !== null
      || camera.statistics_sha256 !== null
      || camera.quality_report_sha256 !== null
      || camera.rules.length !== 0
      || (camera.state === 'failed' && (
        typeof camera.error_code !== 'string'
        || !camera.error_code
        || typeof camera.error_message !== 'string'
        || !camera.error_message
      ))
      || (camera.state === 'planned' && (
        camera.error_code !== null || camera.error_message !== null
      ))
    ) {
      throw new TypeError('invalid evidence-free reciprocal outcome');
    }
    return {
      entry_id: entryId,
      role_module_id: roleModuleId,
      camera_id: camera.camera_id,
      render_id: camera.render_id ?? null,
      state: camera.state,
      runtime_report_sha256: null,
      statistics_sha256: null,
      policy_sha256: camera.policy_sha256,
      quality_report_sha256: null,
      error_code: camera.error_code,
      error_message: camera.error_message,
      rules: [],
    };
  }
  if (
    !['passed', 'rejected'].includes(camera.state)
    || !SHA256.test(camera.runtime_report_sha256)
    || !SHA256.test(camera.statistics_sha256)
    || camera.rules.length < 1
  ) {
    throw new TypeError('invalid production quality camera evidence');
  }
  const rules = camera.rules.map(normalizeRule);
  const passes = rules.every((rule) => rule.passes);
  if ((camera.state === 'passed') !== passes) {
    throw new TypeError('camera state disagrees with rule evidence');
  }
  return {
    entry_id: entryId,
    role_module_id: roleModuleId,
    camera_id: camera.camera_id,
    render_id: camera.render_id ?? null,
    state: camera.state,
    runtime_report_sha256: camera.runtime_report_sha256,
    statistics_sha256: camera.statistics_sha256,
    policy_sha256: camera.policy_sha256,
    quality_report_sha256: camera.quality_report_sha256 ?? null,
    error_code: null,
    error_message: null,
    rules,
  };
}

export function normalizeProductionQualityEvidence(raw) {
  try {
    if (
      !raw
      || raw.schema_version !== 1
      || !VALID_STATUS.has(raw.status)
      || raw.message !== MESSAGE
      || raw.synthetic !== true
      || raw.trust_effect !== TRUST_EFFECT
      || !Array.isArray(raw.stages)
      || raw.stages.length !== STAGE_IDS.length
      || raw.stages.some((stage, index) => (
        !stage || stage.id !== STAGE_IDS[index] || typeof stage.state !== 'string'
      ))
      || !Array.isArray(raw.cameras)
    ) {
      throw new TypeError('invalid production quality envelope');
    }
    if (raw.status !== 'available') {
      if (
        raw.verification_level !== null
        && !['L0', 'L2'].includes(raw.verification_level)
      ) {
        throw new TypeError('invalid production quality verification level');
      }
      return {
        schema_version: 1,
        status: raw.status,
        message: MESSAGE,
        synthetic: true,
        verification_level: raw.verification_level,
        trust_effect: TRUST_EFFECT,
        report_sha256: null,
        evidence_kind: raw.evidence_kind ?? null,
        evidence_sha256: null,
        batch_id: null,
        stages: raw.stages.map(({ id, state }) => ({ id, state })),
        cameras: [],
      };
    }
    const isReciprocalBatch = raw.evidence_kind === RECIPROCAL_BATCH_KIND;
    if (
      !['L0', 'L2'].includes(raw.verification_level)
      || (
        isReciprocalBatch
          ? !SHA256.test(raw.evidence_sha256) || !SHA256.test(raw.batch_id)
          : !SHA256.test(raw.report_sha256)
      )
      || raw.cameras.length < 1
    ) {
      throw new TypeError('available production quality evidence is incomplete');
    }
    const cameras = raw.cameras.map(normalizeCamera);
    if (
      (isReciprocalBatch && cameras.some((camera) => (
        camera.role_module_id !== camera.entry_id
      )))
      || (!isReciprocalBatch && cameras.some((camera) => (
        camera.role_module_id !== null
        || !['passed', 'rejected'].includes(camera.state)
      )))
    ) {
      throw new TypeError('quality entries disagree with their evidence kind');
    }
    if (new Set(cameras.map((camera) => camera.entry_id)).size !== cameras.length) {
      throw new TypeError('production quality entry IDs are duplicated');
    }
    return {
      schema_version: 1,
      status: 'available',
      message: MESSAGE,
      synthetic: true,
      verification_level: raw.verification_level,
      trust_effect: TRUST_EFFECT,
      report_sha256: raw.report_sha256,
      evidence_kind: isReciprocalBatch ? RECIPROCAL_BATCH_KIND : 'frame-quality-report',
      evidence_sha256: isReciprocalBatch ? raw.evidence_sha256 : raw.report_sha256,
      batch_id: isReciprocalBatch ? raw.batch_id : null,
      stages: raw.stages.map(({ id, state }) => ({ id, state })),
      cameras,
    };
  } catch {
    return invalidEvidence();
  }
}

export async function loadProductionQualityEvidence({
  fetchImpl = globalThis.fetch,
  url = ENDPOINT,
} = {}) {
  const response = await fetchImpl(url, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`production quality evidence load failed (${response.status})`);
  }
  return normalizeProductionQualityEvidence(await response.json());
}

function stageMarkup(stages) {
  return `<div class="quality-stages" aria-label="Production quality stages">${
    stages.map((stage) => (
      `<div class="quality-stage" data-state="${escapeHtml(stage.state)}">`
      + `<b>${escapeHtml(stage.id)}</b><span>${escapeHtml(stage.state)}</span></div>`
    )).join('')
  }</div>`;
}

function cameraMarkup(camera) {
  if (!camera) return '';
  const label = camera.role_module_id ?? camera.camera_id;
  const failure = camera.error_code
    ? `<div class="evidence-card is-warning"><b>${escapeHtml(camera.error_code)}</b><p>${escapeHtml(camera.error_message)}</p></div>`
    : '';
  return `<article class="quality-camera" data-state="${escapeHtml(camera.state)}">
    <h4>${escapeHtml(label)}</h4>
    ${camera.role_module_id ? `<p>${escapeHtml(camera.camera_id)}</p>` : ''}
    <p class="quality-camera-state">${escapeHtml(camera.state)}</p>
    ${failure}
    <dl class="quality-hashes">
      <div><dt>Runtime report SHA</dt><dd>${escapeHtml(camera.runtime_report_sha256 ?? 'not published')}</dd></div>
      <div><dt>Statistics SHA</dt><dd>${escapeHtml(camera.statistics_sha256 ?? 'not published')}</dd></div>
      <div><dt>Policy SHA</dt><dd>${escapeHtml(camera.policy_sha256)}</dd></div>
    </dl>
    <div class="quality-rules" role="table" aria-label="Per-rule decisions">
      ${camera.rules.length ? camera.rules.map((rule) => `<div class="quality-rule" role="row">
        <span role="cell">${escapeHtml(rule.rule_id)}</span>
        <span role="cell">${escapeHtml(rule.measured)}</span>
        <span role="cell">${escapeHtml(rule.comparison_direction)} ${escapeHtml(rule.operator_threshold)}</span>
        <b role="cell" data-state="${rule.passes ? 'passed' : 'rejected'}">${rule.passes ? 'PASS' : 'REJECT'}</b>
      </div>`).join('') : '<p>no canonical quality report was published</p>'}
    </div>
  </article>`;
}

export function renderProductionQualityPanel(raw, selectedCameraId = null) {
  const evidence = normalizeProductionQualityEvidence(raw);
  const selected = evidence.cameras.find(
    (camera) => camera.entry_id === selectedCameraId,
  ) ?? evidence.cameras.find((camera) => camera.state === 'rejected')
    ?? evidence.cameras.find((camera) => camera.state === 'failed')
    ?? evidence.cameras[0]
    ?? null;
  const options = evidence.cameras.map((camera) => (
    `<option value="${escapeHtml(camera.entry_id)}"${
      camera.entry_id === selected?.entry_id ? ' selected' : ''
    }>${escapeHtml(camera.role_module_id ?? camera.camera_id)} · ${escapeHtml(camera.state)}</option>`
  )).join('');
  return `<section class="production-quality-panel" data-production-quality="${escapeHtml(evidence.status)}">
    <p class="eyebrow">PRODUCTION FRAME QUALITY</p>
    <div class="summary-card ${evidence.status === 'available' ? '' : 'is-warning'}">
      <h3>${escapeHtml(evidence.status)}</h3>
      <p>${escapeHtml(evidence.message)}</p>
    </div>
    ${stageMarkup(evidence.stages)}
    <dl class="quality-contract">
      <div><dt>synthetic</dt><dd>${String(evidence.synthetic)}</dd></div>
      <div><dt>verification</dt><dd>${escapeHtml(evidence.verification_level ?? 'unverified')}</dd></div>
      <div><dt>trust effect</dt><dd>${escapeHtml(evidence.trust_effect)}</dd></div>
      <div><dt>evidence kind</dt><dd>${escapeHtml(evidence.evidence_kind ?? 'awaiting')}</dd></div>
      <div><dt>evidence SHA</dt><dd>${escapeHtml(evidence.evidence_sha256 ?? 'awaiting verified evidence')}</dd></div>
    </dl>
    ${evidence.cameras.length ? `<label class="quality-camera-picker">
      Camera
      <select id="production-quality-camera">${options}</select>
    </label>` : ''}
    ${cameraMarkup(selected)}
  </section>`;
}
