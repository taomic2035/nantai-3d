import assert from 'node:assert/strict';
import test from 'node:test';

let loaderModule;
try {
  loaderModule = await import('./production-camera-plan-loader.mjs');
} catch (error) {
  loaderModule = { __loadError: error };
}

function subject() {
  assert.equal(
    loaderModule.__loadError,
    undefined,
    `production-camera-plan-loader.mjs must load: ${loaderModule.__loadError?.message}`,
  );
  return loaderModule;
}

test('unsupported Viewer capability avoids probing the optional production plan', async () => {
  const { loadOptionalProductionCameraPlan } = subject();
  let fetchCalls = 0;
  const result = await loadOptionalProductionCameraPlan({
    bridge: {
      supportsArtifactKind: () => false,
      loadArtifact: () => {
        throw new Error('must not load');
      },
    },
    fetchImpl: async () => {
      fetchCalls += 1;
      throw new Error('must not fetch');
    },
  });

  assert.deepEqual(result, { status: 'unsupported' });
  assert.equal(fetchCalls, 0);
});

test('missing canonical production plan is an honest quiet absence', async () => {
  const { loadOptionalProductionCameraPlan } = subject();
  let loadCalls = 0;
  const result = await loadOptionalProductionCameraPlan({
    bridge: {
      supportsArtifactKind: () => true,
      loadArtifact: () => {
        loadCalls += 1;
      },
    },
    fetchImpl: async (url, options) => {
      assert.equal(url, '/web/data/production-camera-plan.json');
      assert.deepEqual(options, { method: 'HEAD', cache: 'no-store' });
      return { ok: false, status: 404 };
    },
  });

  assert.deepEqual(result, { status: 'absent' });
  assert.equal(loadCalls, 0);
});

test('present production plan is loaded through the capability-gated Viewer bridge', async () => {
  const { loadOptionalProductionCameraPlan } = subject();
  const calls = [];
  const result = await loadOptionalProductionCameraPlan({
    bridge: {
      supportsArtifactKind: (kind) => kind === 'production-camera-plan',
      loadArtifact: async (kind, payload) => {
        calls.push({ kind, payload });
        return { production_plan: { status: 'incomplete', placed: 132, target: 180 } };
      },
    },
    fetchImpl: async () => ({ ok: true, status: 200 }),
  });

  assert.deepEqual(calls, [{
    kind: 'production-camera-plan',
    payload: { url: '/web/data/production-camera-plan.json' },
  }]);
  assert.deepEqual(result, {
    status: 'loaded',
    production_plan: { status: 'incomplete', placed: 132, target: 180 },
  });
});

test('non-404 production plan probe failures remain visible', async () => {
  const { loadOptionalProductionCameraPlan } = subject();
  await assert.rejects(
    () => loadOptionalProductionCameraPlan({
      bridge: {
        supportsArtifactKind: () => true,
        loadArtifact: () => {
          throw new Error('must not load');
        },
      },
      fetchImpl: async () => ({ ok: false, status: 500 }),
    }),
    /probe failed \(500\)/i,
  );
});
