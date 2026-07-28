import assert from 'node:assert/strict';
import test from 'node:test';

import {
  STARTUP_STAGES,
  acceptStartupFallback,
  advanceStartup,
  completeStartup,
  createStartupState,
  failStartup,
  productionRuntimeRequirement,
  startupViewModel,
} from './startup-state.mjs';


test('startup advances through one ordered immutable stage sequence', () => {
  let state = createStartupState();
  assert.deepEqual(STARTUP_STAGES, [
    'world-manifest',
    'reconstruction-manifest',
    'reconstruction',
    'model-manifest',
    'model-bytes',
    'model-parse',
    'interactive',
  ]);

  for (const stage of STARTUP_STAGES.slice(0, -1)) {
    const previous = state;
    state = advanceStartup(state, stage, `${stage} detail`);
    assert.notEqual(state, previous);
    assert.equal(Object.isFrozen(state), true);
    assert.equal(state.status, 'loading');
    assert.equal(state.stage, stage);
  }
  state = completeStartup(state, '模型场景可交互');

  assert.deepEqual(startupViewModel(state), {
    status: 'ready',
    heading: '场景已就绪',
    detail: '模型场景可交互',
    show_spinner: false,
    show_retry: false,
    show_fallback: false,
  });
});


test('startup rejects skipped, repeated, backward, and post-terminal transitions', () => {
  const initial = createStartupState();
  assert.throws(
    () => advanceStartup(initial, 'reconstruction-manifest', 'skipped'),
    /startup stage/i,
  );
  const world = advanceStartup(initial, 'world-manifest', 'world');
  assert.throws(() => advanceStartup(world, 'world-manifest', 'again'), /startup stage/i);
  assert.throws(() => advanceStartup(world, 'model-manifest', 'skip'), /startup stage/i);

  const failed = failStartup(world, {
    stage: 'reconstruction-manifest',
    reason: 'HTTP 500',
    fallbackAvailable: false,
  });
  assert.throws(
    () => advanceStartup(failed, 'reconstruction-manifest', 'late'),
    /terminal/i,
  );
});


test('required model failure exposes retry and an explicit labelled fallback', () => {
  let state = createStartupState();
  for (const stage of STARTUP_STAGES.slice(0, 4)) {
    state = advanceStartup(state, stage, stage);
  }

  state = failStartup(state, {
    stage: 'model-bytes',
    reason: 'SHA-256 mismatch',
    fallbackAvailable: true,
  });

  assert.deepEqual(startupViewModel(state), {
    status: 'failed',
    heading: '模型数据加载失败',
    detail: 'SHA-256 mismatch',
    show_spinner: false,
    show_retry: true,
    show_fallback: true,
  });
  assert.equal(state.fallback_label, '查看高斯 / 点云后备');
  assert.equal(state.trust_effect, 'none');

  const fallback = acceptStartupFallback(state);
  assert.equal(fallback.status, 'loading');
  assert.equal(fallback.stage, 'model-parse');
  assert.equal(fallback.fallback_used, true);
  assert.equal(fallback.trust_effect, 'none');
  const ready = completeStartup(fallback, '高斯 / 点云后备可交互');
  assert.equal(ready.status, 'ready');
  assert.equal(ready.fallback_used, true);
});


test('non-model failure never offers an unrelated fallback', () => {
  const state = failStartup(createStartupState(), {
    stage: 'world-manifest',
    reason: 'HTTP 404',
    fallbackAvailable: true,
  });
  const view = startupViewModel(state);

  assert.equal(view.show_retry, true);
  assert.equal(view.show_fallback, false);
  assert.match(view.heading, /世界清单/);
  assert.throws(() => acceptStartupFallback(state), /fallback/i);
});

test('Production scene is required only for one exact verified snapshot', () => {
  const snapshot = {
    release: {
      package_kind: 'production',
      package_status: 'verified',
      release_contract: 'production-accepted-at-build',
      scene_trust_effect: 'none',
    },
    real_scene: {
      decision: 'accepted-production',
      production_release_allowed: true,
    },
  };
  assert.equal(productionRuntimeRequirement(snapshot).required, true);
  for (const [section, field, value] of [
    ['release', 'package_status', 'invalid'],
    ['release', 'release_contract', 'modeled-contract-only'],
    ['release', 'scene_trust_effect', 'promoted'],
    ['real_scene', 'decision', 'rejected'],
    ['real_scene', 'production_release_allowed', false],
  ]) {
    const changed = structuredClone(snapshot);
    changed[section][field] = value;
    assert.equal(productionRuntimeRequirement(changed).required, false);
  }
});
