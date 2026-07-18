import { normalizeWeather } from './environment.mjs';

function freezeResponse(response) {
  Object.freeze(response.baseColorMultiplier);
  return Object.freeze(response);
}

export const MESH_WEATHER_RESPONSES = Object.freeze({
  clear: freezeResponse({
    exposure: 1,
    keyColor: 0xfff3dc,
    keyIntensity: 2.4,
    baseColorMultiplier: [1, 1, 1],
    roughnessMultiplier: 1,
  }),
  overcast: freezeResponse({
    exposure: 0.82,
    keyColor: 0xcfd8df,
    keyIntensity: 0.9,
    baseColorMultiplier: [0.92, 0.95, 0.98],
    roughnessMultiplier: 1.08,
  }),
  rain: freezeResponse({
    exposure: 0.78,
    keyColor: 0xa9bfd0,
    keyIntensity: 0.85,
    baseColorMultiplier: [0.78, 0.82, 0.87],
    roughnessMultiplier: 0.55,
  }),
  snow: freezeResponse({
    exposure: 1.1,
    keyColor: 0xeaf5ff,
    keyIntensity: 1.5,
    baseColorMultiplier: [1.06, 1.08, 1.10],
    roughnessMultiplier: 1.12,
  }),
  fog: freezeResponse({
    exposure: 0.78,
    keyColor: 0xd2d8d7,
    keyIntensity: 0.55,
    baseColorMultiplier: [0.88, 0.90, 0.90],
    roughnessMultiplier: 1.06,
  }),
  night: freezeResponse({
    exposure: 0.48,
    keyColor: 0x9cb8e8,
    keyIntensity: 0.7,
    baseColorMultiplier: [0.68, 0.76, 0.94],
    roughnessMultiplier: 0.92,
  }),
});

export function meshWeatherResponse(weather) {
  return MESH_WEATHER_RESPONSES[normalizeWeather(weather)];
}

export function environmentNotice(rendererCapabilities = {}) {
  if (rendererCapabilities.dynamic_mesh_relighting === true) {
    return '网格重光照 + 大气叠加 · 3DGS 仅大气叠加（非重光照）';
  }
  return '3DGS 仅大气叠加 · 非重光照 not relighting';
}

export function atmosphereLightForRenderer(
  rendererCapabilities,
  weatherLight,
  stableMeshFillLight,
) {
  return rendererCapabilities.dynamic_mesh_relighting === true
    ? stableMeshFillLight
    : weatherLight;
}
