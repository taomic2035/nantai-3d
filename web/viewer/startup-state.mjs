export const STARTUP_STAGES = Object.freeze([
  'world-manifest',
  'reconstruction-manifest',
  'reconstruction',
  'model-manifest',
  'model-bytes',
  'model-parse',
  'interactive',
]);

const MODEL_STAGES = new Set([
  'model-manifest',
  'model-bytes',
  'model-parse',
]);

const STAGE_HEADINGS = Object.freeze({
  'world-manifest': '读取世界清单',
  'reconstruction-manifest': '读取重建证据',
  reconstruction: '加载高斯 / 点云后备',
  'model-manifest': '读取模型清单',
  'model-bytes': '校验模型数据',
  'model-parse': '解析模型场景',
  interactive: '场景已就绪',
});

const FAILURE_HEADINGS = Object.freeze({
  'world-manifest': '世界清单加载失败',
  'reconstruction-manifest': '重建清单加载失败',
  reconstruction: '高斯 / 点云加载失败',
  'model-manifest': '模型数据加载失败',
  'model-bytes': '模型数据加载失败',
  'model-parse': '模型数据加载失败',
  interactive: '场景启动失败',
});

function freezeState(state) {
  return Object.freeze(state);
}

function stageIndex(stage) {
  const index = STARTUP_STAGES.indexOf(stage);
  if (index < 0) throw new Error(`Unknown startup stage: ${stage}`);
  return index;
}

function assertActive(state) {
  if (state.status === 'failed' || state.status === 'ready') {
    throw new Error(`Startup state is terminal (${state.status})`);
  }
}

export function createStartupState() {
  return freezeState({
    status: 'idle',
    stage: null,
    stage_index: -1,
    detail: '准备加载场景',
    fallback_available: false,
    fallback_used: false,
    fallback_label: null,
    trust_effect: 'none',
  });
}

export function advanceStartup(state, stage, detail = '') {
  assertActive(state);
  const nextIndex = stageIndex(stage);
  if (nextIndex !== state.stage_index + 1 || stage === 'interactive') {
    throw new Error(
      `Invalid startup stage transition: ${state.stage ?? 'initial'} -> ${stage}`,
    );
  }
  return freezeState({
    ...state,
    status: 'loading',
    stage,
    stage_index: nextIndex,
    detail: detail || STAGE_HEADINGS[stage],
  });
}

export function failStartup(
  state,
  { stage, reason, fallbackAvailable = false },
) {
  assertActive(state);
  const failedIndex = stageIndex(stage);
  if (
    failedIndex !== state.stage_index
    && failedIndex !== state.stage_index + 1
  ) {
    throw new Error(
      `Invalid startup stage failure: ${state.stage ?? 'initial'} -> ${stage}`,
    );
  }
  const modelFailure = MODEL_STAGES.has(stage);
  const offersFallback = modelFailure && fallbackAvailable === true;
  return freezeState({
    ...state,
    status: 'failed',
    stage,
    stage_index: failedIndex,
    detail: String(reason || '未知错误'),
    fallback_available: offersFallback,
    fallback_label: offersFallback ? '查看高斯 / 点云后备' : null,
    trust_effect: 'none',
  });
}

export function acceptStartupFallback(state) {
  if (state.status !== 'failed' || state.fallback_available !== true) {
    throw new Error('Startup fallback is not available');
  }
  return freezeState({
    ...state,
    status: 'loading',
    stage: 'model-parse',
    stage_index: stageIndex('model-parse'),
    detail: '已明确选择高斯 / 点云后备，正在准备交互',
    fallback_available: false,
    fallback_used: true,
    fallback_label: null,
    trust_effect: 'none',
  });
}

export function completeStartup(state, detail = '场景可交互') {
  assertActive(state);
  if (state.stage !== 'model-parse') {
    throw new Error(
      `Invalid startup stage completion from ${state.stage ?? 'initial'}`,
    );
  }
  return freezeState({
    ...state,
    status: 'ready',
    stage: 'interactive',
    stage_index: stageIndex('interactive'),
    detail,
    fallback_available: false,
    fallback_label: null,
  });
}

export function startupViewModel(state) {
  const loading = state.status === 'idle' || state.status === 'loading';
  return {
    status: state.status,
    heading: state.status === 'failed'
      ? FAILURE_HEADINGS[state.stage]
      : STAGE_HEADINGS[state.stage] ?? '准备场景',
    detail: state.detail,
    show_spinner: loading,
    show_retry: state.status === 'failed',
    show_fallback: state.status === 'failed' && state.fallback_available === true,
  };
}
