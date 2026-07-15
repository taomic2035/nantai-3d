import { commandCapability, readOnlyCapabilities } from './capabilities.mjs';

const DEFAULT_CAPABILITIES = readOnlyCapabilities('Studio write capabilities are unavailable.');

export const STUDIO_DAG = Object.freeze({
  views: Object.freeze([
    { id: 'sources', stateKey: 'sources', label: '输入 Sources', branch: 'capture' },
    {
      id: 'align', stateKey: 'align', label: '配准证据 Align',
      branch: 'capture', evidenceOf: 'reconstruct',
    },
    { id: 'reconstruct', stateKey: 'reconstruct', label: '重建 Reconstruct', branch: 'capture' },
    { id: 'assets', stateKey: 'assets', label: '素材 Assets', branch: 'world' },
    { id: 'compose', stateKey: 'stitch', label: '编排 Compose', branch: 'world' },
    { id: 'review', stateKey: 'review', label: '验收 Review', branch: 'join' },
  ]),
  edges: Object.freeze([
    ['sources', 'reconstruct'],
    ['assets', 'compose'],
    ['reconstruct', 'review'],
    ['compose', 'review'],
  ]),
});

const COMMAND_STEPS = Object.freeze({
  ingest: 'sources',
  reconstruct: 'reconstruct',
  world: 'stitch',
  'validate-assets': 'assets',
});

export function displayInputPath(storage = '.') {
  const root = String(storage || '.');
  if (root.endsWith('/') || root.endsWith('\\')) return `${root}input`;
  return root.includes('\\') && !root.includes('/') ? `${root}\\input` : `${root}/input`;
}

export function preferredRunId(runs, selectedRunId, activeRunId) {
  const ids = new Set((runs ?? []).map((run) => run.id));
  if (activeRunId && ids.has(activeRunId)) return activeRunId;
  if (selectedRunId && ids.has(selectedRunId)) return selectedRunId;
  return runs?.[0]?.id ?? null;
}

function runTarget(snapshot) {
  return COMMAND_STEPS[snapshot.active_run?.command] ?? 'review';
}

function navigationAction(id, label, targetStep, { openDrawer = false, focus = null } = {}) {
  return {
    id,
    label,
    enabled: true,
    reason: '',
    targetStep,
    openDrawer,
    focus,
    command: null,
  };
}

function writeAction(command, label, targetStep, capabilities) {
  const capability = commandCapability(capabilities, command);
  return {
    id: command,
    label,
    enabled: capability.enabled,
    reason: capability.enabled ? '' : capability.reason,
    targetStep,
    openDrawer: false,
    focus: null,
    command,
  };
}

function stepComplete(snapshot, stateKey) {
  const state = snapshot.pipeline?.[stateKey];
  return state?.availability === 'ready'
    && state?.execution === 'succeeded'
    && state?.freshness === 'current';
}

function assetsComplete(snapshot) {
  const assets = snapshot.assets ?? {};
  const itemsValid = !Array.isArray(assets.items)
    || assets.items.every((item) => item?.validated === true);
  return stepComplete(snapshot, 'assets')
    && Number.isInteger(assets.registered)
    && assets.registered > 0
    && assets.blocked === 0
    && itemsValid;
}

export function derivePrimaryAction(snapshot, capabilities = DEFAULT_CAPABILITIES) {
  if (!snapshot.adapter?.connected) {
    return navigationAction('reconnect', '重新连接本地管线', null);
  }
  if (snapshot.active_run?.status === 'failed') {
    return navigationAction(
      'inspect-failure', '查看失败原因', runTarget(snapshot),
      { openDrawer: true, focus: 'drawer' },
    );
  }
  if (['queued', 'running'].includes(snapshot.active_run?.status)) {
    return navigationAction(
      'view-progress', '查看任务进度', runTarget(snapshot),
      { openDrawer: true, focus: 'drawer' },
    );
  }
  const sources = snapshot.sources ?? {};
  if ((sources.images ?? 0) + (sources.videos ?? 0) === 0) {
    return navigationAction(
      'inspect-sources', '查看输入目录', 'sources', { focus: 'source-empty' },
    );
  }
  if (!stepComplete(snapshot, 'sources')) {
    return writeAction('ingest', '扫描输入素材', 'sources', capabilities);
  }
  if (!snapshot.reconstruction?.artifact || !stepComplete(snapshot, 'reconstruct')) {
    return writeAction('reconstruct', '开始混合重建', 'reconstruct', capabilities);
  }
  if (!assetsComplete(snapshot)) {
    return writeAction('validate-assets', '验证素材', 'assets', capabilities);
  }
  if (!stepComplete(snapshot, 'stitch')) {
    return writeAction('world', '生成世界编排', 'stitch', capabilities);
  }
  return navigationAction('review', '查看验收摘要', 'review');
}

export function primaryNavigation(action) {
  return {
    step: action.targetStep ?? null,
    openDrawer: action.openDrawer === true,
    focus: action.focus ?? null,
    submitCommand: action.enabled && action.command ? action.command : null,
  };
}

export function deriveRunActions(run, capabilities = DEFAULT_CAPABILITIES) {
  const capability = commandCapability(capabilities, run?.command);
  const actions = [];
  if (['queued', 'running'].includes(run?.status) && capability.enabled && capability.cancel) {
    actions.push({ id: 'cancel', label: '取消任务', runId: run.id });
  }
  if (run?.status === 'failed' && capability.enabled && capability.retry) {
    actions.push({ id: 'retry', label: '重试任务', runId: run.id });
  }
  return actions;
}
