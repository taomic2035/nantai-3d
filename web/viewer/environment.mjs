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
  if (preset.sky) {
    Object.freeze(preset.sky.sunDirection);
    Object.freeze(preset.sky);
  }
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
    sky: {
      zenith: 0x398ccc,
      horizon: 0xd4e7ee,
      lower: 0x9fb5b5,
      sunColor: 0xffefbf,
      sunDirection: [0.46, 0.74, -0.49],
      sunSharpness: 720,
      cloudCoverage: 0.72,
      cloudOpacity: 0.2,
      haze: 0.14,
      stars: 0,
    },
    precipitation: null,
  }),
  overcast: freezePreset({
    label: '阴',
    background: 0x667582,
    fog: { color: 0x667582, nearScale: 0.8, farScale: 1.0 },
    light: { sky: 0xaeb9c2, ground: 0x3e423d, intensity: 0.62 },
    sky: {
      zenith: 0x5d6e7c,
      horizon: 0xb2bdc2,
      lower: 0x7c898b,
      sunColor: 0xdde3df,
      sunDirection: [-0.38, 0.62, -0.69],
      sunSharpness: 28,
      cloudCoverage: 0.3,
      cloudOpacity: 0.72,
      haze: 0.38,
      stars: 0,
    },
    precipitation: null,
  }),
  rain: freezePreset({
    label: '雨',
    background: 0x394b5b,
    fog: { color: 0x465867, nearScale: 0.55, farScale: 0.78 },
    light: { sky: 0x8599aa, ground: 0x303734, intensity: 0.48 },
    sky: {
      zenith: 0x263b4d,
      horizon: 0x738493,
      lower: 0x38494e,
      sunColor: 0xaebac2,
      sunDirection: [-0.4, 0.55, -0.73],
      sunSharpness: 18,
      cloudCoverage: 0.18,
      cloudOpacity: 0.9,
      haze: 0.55,
      stars: 0,
    },
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
    sky: {
      zenith: 0x93a9ba,
      horizon: 0xe0e7e8,
      lower: 0xc5cfd0,
      sunColor: 0xf5f1df,
      sunDirection: [0.25, 0.66, -0.71],
      sunSharpness: 42,
      cloudCoverage: 0.26,
      cloudOpacity: 0.65,
      haze: 0.48,
      stars: 0,
    },
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
    sky: {
      zenith: 0x879396,
      horizon: 0xc0c5c3,
      lower: 0xa3aaa7,
      sunColor: 0xd8d8cd,
      sunDirection: [0.2, 0.58, -0.79],
      sunSharpness: 12,
      cloudCoverage: 0.42,
      cloudOpacity: 0.36,
      haze: 0.88,
      stars: 0,
    },
    precipitation: null,
  }),
  night: freezePreset({
    label: '夜',
    background: 0x07111f,
    fog: { color: 0x0b1725, nearScale: 0.65, farScale: 0.95 },
    light: { sky: 0x354e72, ground: 0x111821, intensity: 0.28 },
    sky: {
      zenith: 0x020916,
      horizon: 0x142943,
      lower: 0x07111c,
      sunColor: 0xd9e7ff,
      sunDirection: [-0.52, 0.6, -0.61],
      sunSharpness: 1050,
      cloudCoverage: 0.58,
      cloudOpacity: 0.3,
      haze: 0.12,
      stars: 0.72,
    },
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
