const CORE_SPLAT_ATTRIBUTES = [
  'x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
  'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3',
];

const VALID = {
  availability: new Set(['missing', 'ready']),
  execution: new Set(['idle', 'queued', 'running', 'succeeded', 'failed', 'canceled']),
  freshness: new Set(['current', 'stale']),
  preview: new Set(['unloaded', 'loading', 'ready', 'degraded']),
  trust: new Set(['verified', 'proxy', 'untrusted']),
};

/** Translate the live viewer handshake into the closed Studio capability enum. */
export function viewerCapabilityTokens(capabilities = {}) {
  const renderer = capabilities.renderer ?? {};
  if (Object.keys(renderer).length === 0) return [];
  const tokens = ['dc-color'];
  if (renderer.anisotropic_covariance === true) tokens.push('anisotropic-covariance');
  if (renderer.alpha_composite === true) tokens.push('alpha-composite');
  if (renderer.spherical_harmonics === true) tokens.push('spherical-harmonics');
  return tokens;
}

function safeEnum(axis, value, fallback) {
  return VALID[axis].has(value) ? value : fallback;
}

export function normalizeStepState(input = {}) {
  const diagnostics = [];
  const state = {
    availability: safeEnum('availability', input.availability, 'missing'),
    execution: safeEnum('execution', input.execution, 'idle'),
    freshness: safeEnum('freshness', input.freshness, 'stale'),
    preview: safeEnum('preview', input.preview, 'unloaded'),
    trust: safeEnum('trust', input.trust, 'untrusted'),
  };

  if (state.availability === 'missing') {
    if (state.execution !== 'idle' || state.freshness !== 'stale'
        || state.preview !== 'unloaded' || state.trust !== 'untrusted') {
      diagnostics.push('missing step cannot be executed, current, previewed, or trusted');
    }
    Object.assign(state, {
      execution: 'idle', freshness: 'stale', preview: 'unloaded', trust: 'untrusted',
    });
  }
  if (state.execution === 'failed') {
    if (state.freshness !== 'stale' || state.trust !== 'untrusted') {
      diagnostics.push('failed step cannot be current or trusted');
    }
    state.freshness = 'stale';
    state.trust = 'untrusted';
    if (state.preview === 'ready') state.preview = 'degraded';
  }
  if (state.preview === 'unloaded' && state.trust === 'verified') {
    diagnostics.push('unloaded preview cannot be verified');
    state.trust = 'untrusted';
  }
  return { state, diagnostics };
}

function deriveRenderFidelity(reconstruction = {}, diagnostics) {
  const attributes = new Set(Array.isArray(reconstruction.attributes)
    ? reconstruction.attributes : []);
  const capabilities = new Set(Array.isArray(reconstruction.renderer_capabilities)
    ? reconstruction.renderer_capabilities : []);
  const hasCore = CORE_SPLAT_ATTRIBUTES.every((name) => attributes.has(name));
  const hasRenderer = capabilities.has('anisotropic-covariance')
    && capabilities.has('alpha-composite') && capabilities.has('dc-color');
  if (!hasCore || !hasRenderer) {
    diagnostics.push('renderer/artifact contract only supports DC point preview');
    return 'dc-point-preview';
  }

  const degree = Number.isInteger(reconstruction.sh_degree)
    ? Math.max(0, reconstruction.sh_degree) : 0;
  if (degree > 0) {
    const expected = 3 * ((degree + 1) ** 2 - 1);
    const completeRest = Array.from({ length: expected }, (_, i) => `f_rest_${i}`)
      .every((name) => attributes.has(name));
    if (completeRest && capabilities.has('spherical-harmonics')) {
      return 'gaussian-splat-sh';
    }
    diagnostics.push('high-order SH declaration lacks coefficients or renderer capability');
  }
  return 'gaussian-splat-dc';
}

function hasUnambiguousFramePath(coordinate, diagnostics) {
  const source = coordinate.source_frame;
  const target = coordinate.world_frame;
  const chain = Array.isArray(coordinate.transform_chain)
    ? coordinate.transform_chain : [];
  const ids = chain.map((step) => step?.transform_id ?? step?.id);
  if (ids.some((id) => typeof id !== 'string' || id.length === 0)
      || new Set(ids).size !== ids.length) {
    diagnostics.push('coordinate transform ids are missing or duplicated');
    return false;
  }
  let current = source;
  for (const step of chain) {
    if (step?.source_frame !== current || typeof step?.target_frame !== 'string') {
      diagnostics.push('coordinate transform chain is discontinuous');
      return false;
    }
    current = step.target_frame;
  }
  if (current !== target) {
    diagnostics.push('coordinate transform chain does not reach the world frame');
    return false;
  }
  return true;
}

function deriveGeometryUsability(snapshot, diagnostics) {
  const coordinate = snapshot.coordinate ?? {};
  const reconstruction = snapshot.reconstruction ?? {};
  const frameAligned = hasUnambiguousFramePath(coordinate, diagnostics);
  const complete = coordinate.world_frame === 'world-enu'
    && coordinate.units === 'meters'
    && coordinate.handedness === 'right'
    && coordinate.up_axis === 'z'
    && Array.isArray(coordinate.metric_evidence)
    && coordinate.metric_evidence.length > 0
    && frameAligned;
  if (!complete) {
    diagnostics.push('coordinate evidence is incomplete; metric use is blocked');
    return 'preview-only';
  }
  if (reconstruction.synthetic || snapshot.adapter?.kind === 'mock') {
    diagnostics.push('synthetic/mock geometry is never measurable evidence');
    return 'preview-only';
  }
  return 'measurable';
}

function deriveTrust(snapshot, geometryUsability, renderFidelity, diagnostics) {
  const reconstruction = snapshot.reconstruction ?? {};
  if (reconstruction.synthetic || snapshot.adapter?.kind === 'mock') return 'proxy';
  const artifact = reconstruction.artifact ?? {};
  if (geometryUsability === 'measurable'
      && renderFidelity.startsWith('gaussian-splat')
      && artifact.immutable === true
      && typeof artifact.sha256 === 'string'
      && artifact.sha256.length > 0) {
    return 'verified';
  }
  if (geometryUsability === 'preview-only'
      && (!snapshot.coordinate || Object.keys(snapshot.coordinate).length === 0)) {
    return 'untrusted';
  }
  diagnostics.push('artifact is usable only as a proxy');
  return 'proxy';
}

export function normalizeSnapshot(raw = {}) {
  const snapshot = structuredClone(raw);
  const diagnostics = [];
  if (snapshot.schema_version !== 2) diagnostics.push('unsupported snapshot schema version');
  if (!snapshot.adapter || typeof snapshot.adapter.connected !== 'boolean') {
    snapshot.adapter = { kind: 'unknown', connected: false };
    diagnostics.push('adapter state is missing');
  }
  const renderFidelity = deriveRenderFidelity(snapshot.reconstruction, diagnostics);
  const geometryUsability = deriveGeometryUsability(snapshot, diagnostics);
  const trust = deriveTrust(snapshot, geometryUsability, renderFidelity, diagnostics);
  snapshot.derived = { renderFidelity, geometryUsability, trust, diagnostics };
  return snapshot;
}

export function derivePrimaryAction(snapshot) {
  if (!snapshot.adapter?.connected) {
    return { id: 'reconnect', label: '重新连接本地管线' };
  }
  if (snapshot.active_run?.status === 'failed') {
    return { id: 'inspect-failure', label: '查看失败原因' };
  }
  const sources = snapshot.sources ?? {};
  if ((sources.images ?? 0) + (sources.videos ?? 0) === 0) {
    return { id: 'inspect-sources', label: '开始检查输入' };
  }
  if (!snapshot.reconstruction?.artifact) {
    return { id: 'reconstruct', label: '开始混合重建' };
  }
  return { id: 'review', label: '查看验收摘要' };
}

export const MODEL_ENUMS = Object.freeze({
  availability: [...VALID.availability],
  execution: [...VALID.execution],
  freshness: [...VALID.freshness],
  preview: [...VALID.preview],
  trust: [...VALID.trust],
});
