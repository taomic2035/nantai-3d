import assert from 'node:assert/strict';
import test from 'node:test';

import { WEATHER_PRESETS } from './environment.mjs';
import {
  SKY_FRAGMENT_SHADER,
  SKY_VERTEX_SHADER,
  skyDomeParameters,
} from './sky-dome.mjs';

test('sky parameters are renderer-ready copies and retain overlay-only semantics', () => {
  const clear = skyDomeParameters(WEATHER_PRESETS.clear);
  assert.deepEqual(clear.sunDirection.length, 3);
  assert.ok(Math.abs(Math.hypot(...clear.sunDirection) - 1) < 1e-12);
  assert.equal(clear.effectKind, 'atmospheric-overlay');
  assert.equal(clear.relighting, false);
  assert.notEqual(clear.zenith, clear.horizon);

  clear.sunDirection[0] = 99;
  assert.notEqual(
    WEATHER_PRESETS.clear.sky.sunDirection[0],
    clear.sunDirection[0],
  );
});

test('sky shader renders gradient sun clouds haze and bounded night stars', () => {
  assert.match(SKY_VERTEX_SHADER, /cameraPosition/);
  assert.match(SKY_FRAGMENT_SHADER, /uZenith/);
  assert.match(SKY_FRAGMENT_SHADER, /uHorizon/);
  assert.match(SKY_FRAGMENT_SHADER, /uSunDirection/);
  assert.match(SKY_FRAGMENT_SHADER, /uCloudCoverage/);
  assert.match(SKY_FRAGMENT_SHADER, /uHaze/);
  assert.match(SKY_FRAGMENT_SHADER, /uStars/);
  assert.doesNotMatch(SKY_FRAGMENT_SHADER, /sampler2D|samplerCube/);
});

test('malformed sky presets fail closed instead of creating NaN uniforms', () => {
  assert.throws(
    () => skyDomeParameters({ sky: { ...WEATHER_PRESETS.clear.sky, haze: NaN } }),
    /sky preset/i,
  );
  assert.throws(
    () => skyDomeParameters({ sky: { ...WEATHER_PRESETS.clear.sky, sunDirection: [0, 0, 0] } }),
    /sun direction/i,
  );
});
