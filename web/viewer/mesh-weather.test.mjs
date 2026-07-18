import assert from 'node:assert/strict';
import test from 'node:test';

import { WEATHER_IDS } from './environment.mjs';
import {
  MESH_WEATHER_RESPONSES,
  atmosphereLightForRenderer,
  environmentNotice,
  meshWeatherResponse,
} from './mesh-weather.mjs';

test('six mesh weather responses are distinct, complete, and immutable', () => {
  const responses = WEATHER_IDS.map(meshWeatherResponse);

  assert.equal(new Set(responses.map(JSON.stringify)).size, WEATHER_IDS.length);
  assert.deepEqual(meshWeatherResponse('clear'), {
    exposure: 1,
    keyColor: 0xfff3dc,
    keyIntensity: 2.4,
    baseColorMultiplier: [1, 1, 1],
    roughnessMultiplier: 1,
  });
  assert.ok(meshWeatherResponse('rain').roughnessMultiplier < 1);
  assert.ok(meshWeatherResponse('rain').baseColorMultiplier[0] < 1);
  assert.ok(meshWeatherResponse('night').exposure < 0.5);
  assert.equal(Object.isFrozen(MESH_WEATHER_RESPONSES), true);
  for (const response of responses) {
    assert.equal(Object.isFrozen(response), true);
    assert.equal(Object.isFrozen(response.baseColorMultiplier), true);
  }
  assert.throws(() => meshWeatherResponse('storm'), /未知天气/);
});

test('night keeps textured mesh materials dark but readable', () => {
  const night = meshWeatherResponse('night');
  const darkestChannel = Math.min(...night.baseColorMultiplier);
  const materialResponse = night.exposure * darkestChannel;

  assert.ok(materialResponse >= 0.3);
  assert.ok(materialResponse < 0.5);
  assert.ok(night.keyIntensity >= 0.6);
  assert.ok(night.baseColorMultiplier[2] > night.baseColorMultiplier[0]);
});

test('rain darkens and cools textured mesh without crushing material detail', () => {
  const rain = meshWeatherResponse('rain');
  const darkestChannel = Math.min(...rain.baseColorMultiplier);
  const materialResponse = rain.exposure * darkestChannel;

  assert.ok(materialResponse >= 0.6);
  assert.ok(materialResponse < 0.75);
  assert.ok(rain.keyIntensity >= 0.8);
  assert.ok(rain.baseColorMultiplier[2] > rain.baseColorMultiplier[0]);
  assert.ok(rain.roughnessMultiplier < 1);
});

test('renderer notice distinguishes mesh relighting from 3DGS overlay', () => {
  assert.match(
    environmentNotice({
      dynamic_mesh_relighting: true,
      splat_relighting: false,
    }),
    /网格重光照.*大气叠加.*3DGS.*仅大气叠加/,
  );
  assert.match(
    environmentNotice({ splat_relighting: false }),
    /3DGS.*仅大气叠加.*非重光照/,
  );
  assert.doesNotMatch(
    environmentNotice({ splat_relighting: false }),
    /3DGS 已重光照/,
  );
});

test('mesh relighting keeps a stable fill while point modes retain weather light', () => {
  const clearLight = { intensity: 0.9 };
  const rainLight = { intensity: 0.48 };

  assert.equal(
    atmosphereLightForRenderer(
      { dynamic_mesh_relighting: true },
      rainLight,
      clearLight,
    ),
    clearLight,
  );
  assert.equal(
    atmosphereLightForRenderer(
      { dynamic_mesh_relighting: false },
      rainLight,
      clearLight,
    ),
    rainLight,
  );
});
