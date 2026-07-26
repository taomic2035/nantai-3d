const SHA256 = /^[0-9a-f]{64}$/;
const STAGE_IDS = Object.freeze([
  'dataset',
  'capture',
  'sfm',
  'production-training',
  'import-integrity',
  'render-quality',
  'viewer-performance',
  'human-review',
  'release-rights',
  'metric-alignment',
]);
const STAGE_STATES = new Set(['succeeded', 'failed', 'unknown', 'not-started']);
const DECISIONS = new Set([
  'not-started',
  'accepted-canary',
  'accepted-production',
  'rejected',
  'invalid-evidence',
]);
const ROLES = new Set(['unknown', 'internal-canary', 'production-acceptance']);
const ENVELOPE_KEYS = [
  'schema_version',
  'role',
  'decision',
  'production_release_allowed',
  'stages',
  'reasons',
  'report_sha256',
];
const NORMALIZED_ENVELOPE_KEYS = [
  ...ENVELOPE_KEYS,
  'role_label',
  'decision_label',
];

const STAGE_LABELS = Object.freeze({
  dataset: 'Dataset',
  capture: 'Capture',
  sfm: 'SfM',
  'production-training': '3DGS',
  'import-integrity': 'Import',
  'render-quality': 'Render',
  'viewer-performance': 'Viewer',
  'human-review': 'Review',
  'release-rights': 'Rights',
  'metric-alignment': 'Metric',
});
const STATE_LABELS = Object.freeze({
  succeeded: '通过',
  failed: '失败',
  unknown: '未知',
  'not-started': '未适用',
});
const DECISION_LABELS = Object.freeze({
  'not-started': '尚无真实场景验收',
  'accepted-canary': '内部 Canary 技术验收通过',
  'accepted-production': 'Production 验收通过',
  rejected: '验收未通过',
  'invalid-evidence': '验收证据无效',
});
const ROLE_LABELS = Object.freeze({
  unknown: '未声明',
  'internal-canary': 'Internal canary',
  'production-acceptance': 'Production acceptance',
});

function exactKeys(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const keys = Object.keys(value).sort();
  return keys.length === expected.length
    && keys.every((key, index) => key === [...expected].sort()[index]);
}

function invalidEvidence() {
  return {
    schema_version: 1,
    role: 'unknown',
    role_label: ROLE_LABELS.unknown,
    decision: 'invalid-evidence',
    decision_label: DECISION_LABELS['invalid-evidence'],
    production_release_allowed: false,
    stages: STAGE_IDS.map((id) => ({
      id,
      label: STAGE_LABELS[id],
      state: 'unknown',
      state_label: STATE_LABELS.unknown,
    })),
    reasons: ['configured real-scene acceptance evidence could not be verified'],
    report_sha256: null,
  };
}

function stageShape(raw) {
  if (
    !Array.isArray(raw)
    || raw.length !== STAGE_IDS.length
    || raw.some((stage, index) => (
      !exactKeys(stage, ['id', 'state'])
      || stage.id !== STAGE_IDS[index]
      || !STAGE_STATES.has(stage.state)
    ))
  ) {
    throw new TypeError('invalid real-scene stage evidence');
  }
  return raw.map(({ id, state }) => ({
    id,
    label: STAGE_LABELS[id],
    state,
    state_label: STATE_LABELS[state],
  }));
}

function reasonShape(raw) {
  if (
    !Array.isArray(raw)
    || raw.length > 32
    || raw.some((reason) => (
      typeof reason !== 'string'
      || reason.length < 1
      || reason.length > 512
      || /[\u0000-\u001f\u007f]/u.test(reason)
    ))
  ) {
    throw new TypeError('invalid real-scene reasons');
  }
  return [...raw];
}

function decisionShape(raw, stages, reasons) {
  const states = stages.map((stage) => stage.state);
  const all = (state) => states.every((actual) => actual === state);
  const has = (state) => states.includes(state);
  if (raw.decision === 'not-started') {
    return raw.role === 'unknown'
      && raw.production_release_allowed === false
      && raw.report_sha256 === null
      && all('not-started');
  }
  if (raw.decision === 'invalid-evidence') {
    return raw.role === 'unknown'
      && raw.production_release_allowed === false
      && raw.report_sha256 === null
      && all('unknown');
  }
  if (!SHA256.test(raw.report_sha256 ?? '')) return false;
  if (raw.decision === 'accepted-canary') {
    return raw.role === 'internal-canary'
      && raw.production_release_allowed === false
      && !has('failed')
      && !has('unknown')
      && states.slice(0, 8).every((state) => state === 'succeeded')
      && states.slice(8).every((state) => state === 'not-started')
      && reasons.length >= 1;
  }
  if (raw.decision === 'accepted-production') {
    return raw.role === 'production-acceptance'
      && raw.production_release_allowed === true
      && all('succeeded')
      && reasons.length === 0;
  }
  return ['internal-canary', 'production-acceptance'].includes(raw.role)
    && raw.production_release_allowed === false
    && has('failed')
    && !has('unknown')
    && reasons.length >= 1;
}

export function normalizeRealSceneEvidence(raw) {
  try {
    let candidate = raw;
    if (exactKeys(raw, NORMALIZED_ENVELOPE_KEYS)) {
      if (
        raw.role_label !== ROLE_LABELS[raw.role]
        || raw.decision_label !== DECISION_LABELS[raw.decision]
        || !Array.isArray(raw.stages)
        || raw.stages.some((stage) => (
          !exactKeys(stage, ['id', 'label', 'state', 'state_label'])
          || stage.label !== STAGE_LABELS[stage.id]
          || stage.state_label !== STATE_LABELS[stage.state]
        ))
      ) {
        throw new TypeError('normalized real-scene labels disagree');
      }
      candidate = {
        schema_version: raw.schema_version,
        role: raw.role,
        decision: raw.decision,
        production_release_allowed: raw.production_release_allowed,
        stages: raw.stages.map(({ id, state }) => ({ id, state })),
        reasons: raw.reasons,
        report_sha256: raw.report_sha256,
      };
    }
    if (
      !exactKeys(candidate, ENVELOPE_KEYS)
      || candidate.schema_version !== 1
      || !ROLES.has(candidate.role)
      || !DECISIONS.has(candidate.decision)
      || typeof candidate.production_release_allowed !== 'boolean'
    ) {
      throw new TypeError('invalid real-scene evidence envelope');
    }
    const stages = stageShape(candidate.stages);
    const reasons = reasonShape(candidate.reasons);
    if (!decisionShape(candidate, stages, reasons)) {
      throw new TypeError('real-scene decision disagrees with gate evidence');
    }
    return {
      schema_version: 1,
      role: candidate.role,
      role_label: ROLE_LABELS[candidate.role],
      decision: candidate.decision,
      decision_label: DECISION_LABELS[candidate.decision],
      production_release_allowed: candidate.production_release_allowed,
      stages,
      reasons,
      report_sha256: candidate.report_sha256,
    };
  } catch {
    return invalidEvidence();
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

export function renderRealSceneEvidence(raw) {
  const evidence = normalizeRealSceneEvidence(raw);
  const tone = evidence.decision === 'accepted-production'
    ? 'success'
    : evidence.decision === 'accepted-canary' || evidence.decision === 'not-started'
      ? 'warning'
      : 'danger';
  const canaryDisclosure = evidence.role === 'internal-canary'
    ? '<p class="real-scene-disclosure">Internal canary ≠ commercial acceptance. It cannot grant release rights or metric alignment.</p>'
    : '';
  const reasons = evidence.reasons.length
    ? `<ul class="real-scene-reasons">${evidence.reasons.map(
      (reason) => `<li>${escapeHtml(reason)}</li>`,
    ).join('')}</ul>`
    : '<p class="real-scene-reasons-empty">No rejected aggregate gate.</p>';
  return `<section class="real-scene-evidence" data-real-scene-decision="${escapeHtml(evidence.decision)}">
    <p class="eyebrow">REAL RECONSTRUCTION EVIDENCE</p>
    <div class="summary-card is-${tone}">
      <h3>${escapeHtml(evidence.decision_label)}</h3>
      <p>${escapeHtml(evidence.role_label)} · production release ${evidence.production_release_allowed ? 'allowed' : 'blocked'}</p>
    </div>
    ${canaryDisclosure}
    <div class="real-scene-stages" aria-label="Real-scene acceptance gates">
      ${evidence.stages.map((stage) => (
        `<div class="real-scene-stage" data-state="${escapeHtml(stage.state)}">`
        + `<b>${escapeHtml(stage.label)}</b><span>${escapeHtml(stage.state_label)}</span></div>`
      )).join('')}
    </div>
    <p class="real-scene-trust-note">Acceptance does not upgrade coordinate or geometry trust. Those remain derived from the scene manifest and transform evidence.</p>
    ${reasons}
    <dl class="real-scene-contract">
      <div><dt>Role</dt><dd>${escapeHtml(evidence.role)}</dd></div>
      <div><dt>Release</dt><dd>${evidence.production_release_allowed ? 'allowed' : 'blocked'}</dd></div>
      <div class="real-scene-report-sha"><dt>Report SHA-256</dt><dd>${escapeHtml(evidence.report_sha256 ?? 'not published')}</dd></div>
    </dl>
  </section>`;
}
