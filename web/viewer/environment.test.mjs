import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_WEATHER,
  DEFAULT_ZOOM,
  WEATHER_IDS,
  WEATHER_PRESETS,
  ZOOM_MAX,
  ZOOM_MIN,
  createPrecipitationPositions,
  getWeatherPreset,
  normalizeWeather,
  normalizeZoom,
} from './environment.mjs';

test('weather ids and defaults are stable', () => {
  assert.deepEqual(WEATHER_IDS, [
    'clear', 'overcast', 'rain', 'snow', 'fog', 'night',
  ]);
  assert.equal(DEFAULT_WEATHER, 'clear');
  assert.equal(DEFAULT_ZOOM, 1);
  for (const id of WEATHER_IDS) {
    assert.equal(normalizeWeather(id), id);
    assert.equal(getWeatherPreset(id), WEATHER_PRESETS[id]);
    assert.equal(Object.isFrozen(WEATHER_PRESETS[id]), true);
  }
});

test('unknown weather ids fail instead of silently falling back', () => {
  assert.throws(() => normalizeWeather('storm'), /未知天气/);
  assert.throws(() => normalizeWeather('CLEAR'), /未知天气/);
  assert.equal(normalizeWeather(undefined), DEFAULT_WEATHER);
});

test('zoom rejects non-numbers and clamps finite values', () => {
  assert.equal(normalizeZoom(undefined), DEFAULT_ZOOM);
  assert.equal(normalizeZoom(1.25), 1.25);
  assert.equal(normalizeZoom(-10), ZOOM_MIN);
  assert.equal(normalizeZoom(99), ZOOM_MAX);
  for (const value of ['1', null, NaN, Infinity, -Infinity]) {
    assert.throws(() => normalizeZoom(value), /缩放必须是有限数字/);
  }
});

test('precipitation layouts are deterministic and hard capped', () => {
  const rainA = createPrecipitationPositions('rain');
  const rainB = createPrecipitationPositions('rain');
  const snow = createPrecipitationPositions('snow');
  assert.equal(rainA.length, 1200 * 3);
  assert.equal(snow.length, 800 * 3);
  assert.deepEqual(rainA, rainB);
  assert.equal(createPrecipitationPositions('clear').length, 0);
  assert.equal(WEATHER_PRESETS.rain.precipitation.count <= 1200, true);
  assert.equal(WEATHER_PRESETS.snow.precipitation.count <= 800, true);
});

test('precipitation volume surrounds the camera instead of hiding above it', () => {
  const positions = createPrecipitationPositions('rain');
  const heights = [];
  for (let index = 1; index < positions.length; index += 3) {
    heights.push(positions[index]);
  }
  assert.ok(Math.min(...heights) < 0);
  assert.ok(Math.max(...heights) > 0);
});
