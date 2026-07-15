export const DEFAULT_INGEST_PARAMETERS = Object.freeze({
  fps: 2,
  max_frames: 300,
  blur_threshold: 80,
  max_long_edge: 2560,
});

const BOUNDS = Object.freeze({
  fps: { min: Number.MIN_VALUE, max: 30, integer: false },
  max_frames: { min: 1, max: 10_000, integer: true },
  blur_threshold: { min: 0, max: Number.MAX_VALUE, integer: false },
  max_long_edge: { min: 256, max: 16_384, integer: true },
});

export function validateIngestParameters(raw) {
  if (!raw || typeof raw !== 'object' || Object.keys(raw).some((key) => !(key in BOUNDS))) {
    throw new TypeError('ingest parameters contain an unknown field');
  }
  const value = { ...DEFAULT_INGEST_PARAMETERS, ...raw };
  Object.entries(BOUNDS).forEach(([key, bound]) => {
    const candidate = Number(value[key]);
    if (!Number.isFinite(candidate)
      || candidate < bound.min
      || candidate > bound.max
      || (bound.integer && !Number.isInteger(candidate))) {
      throw new RangeError(`invalid ingest parameter: ${key}`);
    }
    value[key] = candidate;
  });
  return value;
}

export function ingestConfirmationModel({ inputPath = './input' } = {}) {
  return {
    command: 'ingest',
    title: '确认处理输入素材',
    inputPath,
    stagingPath: '.nantai-studio/work/<run-id>/photos',
    formalTarget: 'photos/',
    impact: '验证通过后，photos/ 将以本次处理结果整体替换；旧版本在提交前受备份保护。',
    cancelNotice: '此里程碑不支持中途取消；提交后请等待任务进入终态。',
    parameters: { ...DEFAULT_INGEST_PARAMETERS },
  };
}
