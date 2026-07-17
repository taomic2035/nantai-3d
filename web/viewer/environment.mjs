export const WEATHER_IDS = Object.freeze([
  'clear', 'overcast', 'rain', 'snow', 'fog', 'night',
]);
export const DEFAULT_WEATHER = 'clear';
export const DEFAULT_ZOOM = 1;
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 3;
export const ZOOM_STEP = 0.1;
export const ENVIRONMENT_EFFECT_IDENTITY = Object.freeze({
  effect_kind: 'atmospheric-overlay',
  effect_source: 'viewer-runtime',
  relighting: false,
});

function freezePreset(preset) {
  if (preset.fog) Object.freeze(preset.fog);
  if (preset.light) Object.freeze(preset.light);
  if (preset.precipitation) {
    Object.freeze(preset.precipitation.volume);
    Object.freeze(preset.precipitation);
  }
  return Object.freeze(preset);
}

export const WEATHER_PRESETS = Object.freeze({
  clear: freezePreset({
    label: '晴',
    background: 0x8fc5e8,
    fog: { color: 0x8fc5e8, nearScale: 1.15, farScale: 1.35 },
    light: { sky: 0xd8efff, ground: 0x4b5035, intensity: 0.9 },
    precipitation: null,
  }),
  overcast: freezePreset({
    label: '阴',
    background: 0x667582,
    fog: { color: 0x667582, nearScale: 0.8, farScale: 1.0 },
    light: { sky: 0xaeb9c2, ground: 0x3e423d, intensity: 0.62 },
    precipitation: null,
  }),
  rain: freezePreset({
    label: '雨',
    background: 0x394b5b,
    fog: { color: 0x465867, nearScale: 0.55, farScale: 0.78 },
    light: { sky: 0x8599aa, ground: 0x303734, intensity: 0.48 },
    precipitation: {
      kind: 'rain',
      count: 1200,
      color: 0xaedcff,
      pointSize: 8,
      opacity: 0.72,
      fallSpeed: 32,
      drift: 1.2,
      volume: [70, 42, 70],
    },
  }),
  snow: freezePreset({
    label: '雪',
    background: 0xa9b8c3,
    fog: { color: 0xb8c4cc, nearScale: 0.5, farScale: 0.72 },
    light: { sky: 0xe8f0f5, ground: 0x68716d, intensity: 0.72 },
    precipitation: {
      kind: 'snow',
      count: 800,
      color: 0xffffff,
      pointSize: 6,
      opacity: 0.88,
      fallSpeed: 4.5,
      drift: 1.8,
      volume: [64, 36, 64],
    },
  }),
  fog: freezePreset({
    label: '雾',
    background: 0x899497,
    fog: { color: 0x899497, nearScale: 0.12, farScale: 0.32 },
    light: { sky: 0xc4cbca, ground: 0x5c605b, intensity: 0.5 },
    precipitation: null,
  }),
  night: freezePreset({
    label: '夜',
    background: 0x07111f,
    fog: { color: 0x0b1725, nearScale: 0.65, farScale: 0.95 },
    light: { sky: 0x354e72, ground: 0x111821, intensity: 0.28 },
    precipitation: null,
  }),
});

export function normalizeWeather(value = DEFAULT_WEATHER) {
  if (typeof value !== 'string' || !WEATHER_IDS.includes(value)) {
    throw new Error(`未知天气: ${String(value)}`);
  }
  return value;
}

export function normalizeZoom(value = DEFAULT_ZOOM) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error('缩放必须是有限数字');
  }
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
}

export function getWeatherPreset(value = DEFAULT_WEATHER) {
  return WEATHER_PRESETS[normalizeWeather(value)];
}

function deterministicUnit(index, axis) {
  const raw = Math.sin((index + 1) * 12.9898 + axis * 78.233) * 43758.5453;
  return raw - Math.floor(raw);
}

export function createPrecipitationPositions(value = DEFAULT_WEATHER) {
  const effect = getWeatherPreset(value).precipitation;
  if (!effect) return new Float32Array(0);
  const [width, height, depth] = effect.volume;
  const positions = new Float32Array(effect.count * 3);
  for (let index = 0; index < effect.count; index += 1) {
    const offset = index * 3;
    positions[offset] = (deterministicUnit(index, 0) - 0.5) * width;
    positions[offset + 1] = (deterministicUnit(index, 1) - 0.5) * height;
    positions[offset + 2] = (deterministicUnit(index, 2) - 0.5) * depth;
  }
  return positions;
}
