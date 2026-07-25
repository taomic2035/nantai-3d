import assert from 'node:assert/strict';
import test from 'node:test';

let loaderModule;
try {
  loaderModule = await import('./roaming-graph-loader.mjs');
} catch (error) {
  loaderModule = { __loadError: error };
}

function subject() {
  assert.equal(
    loaderModule.__loadError,
    undefined,
    `roaming-graph-loader.mjs must load: ${loaderModule.__loadError?.message}`,
  );
  return loaderModule;
}

test('unsupported Viewer capability avoids probing the optional roaming graph', async () => {
  const { loadOptionalRoamingGraph } = subject();
  let fetchCalls = 0;
  const result = await loadOptionalRoamingGraph({
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

test('missing canonical roaming graph is an honest quiet absence', async () => {
  const { loadOptionalRoamingGraph } = subject();
  let loadCalls = 0;
  const result = await loadOptionalRoamingGraph({
    bridge: {
      supportsArtifactKind: () => true,
      loadArtifact: () => {
        loadCalls += 1;
      },
    },
    fetchImpl: async (url, options) => {
      assert.equal(url, '/web/data/roaming-graph.json');
      assert.deepEqual(options, { method: 'HEAD', cache: 'no-store' });
      return { ok: false, status: 404 };
    },
  });

  assert.deepEqual(result, { status: 'absent' });
  assert.equal(loadCalls, 0);
});

test('present roaming graph is validated through the capability-gated Viewer bridge', async () => {
  const { loadOptionalRoamingGraph } = subject();
  const calls = [];
  const roamingGraph = {
    status: 'graph-connected',
    summary: '3/3 graph rooms reachable · not 360 evidence',
    navigation_nodes: [{
      room_id: 'room-entry',
      label: 'Entry',
      position: { east: 0, north: 0, up: 1.6 },
    }],
  };
  const result = await loadOptionalRoamingGraph({
    bridge: {
      supportsArtifactKind: (kind) => kind === 'roaming-graph',
      loadArtifact: async (kind, payload) => {
        calls.push({ kind, payload });
        return { roaming_graph: roamingGraph };
      },
    },
    fetchImpl: async () => ({ ok: true, status: 200 }),
  });

  assert.deepEqual(calls, [{
    kind: 'roaming-graph',
    payload: { url: '/web/data/roaming-graph.json' },
  }]);
  assert.deepEqual(result, {
    status: 'loaded',
    roaming_graph: roamingGraph,
  });
});

test('non-404 roaming graph probe failures remain visible', async () => {
  const { loadOptionalRoamingGraph } = subject();
  await assert.rejects(
    () => loadOptionalRoamingGraph({
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
