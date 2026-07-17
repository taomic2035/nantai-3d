import assert from 'node:assert/strict';
import test from 'node:test';

let worldChunks;
try {
  worldChunks = await import('./world-chunks.mjs');
} catch (error) {
  worldChunks = { __loadError: error };
}

function subject() {
  assert.equal(
    worldChunks.__loadError,
    undefined,
    `world-chunks.mjs must load: ${worldChunks.__loadError?.message}`,
  );
  return worldChunks;
}

const ON_DEMAND_MANIFEST = {
  grid: {
    on_demand: true,
    url_template: '/api/world/chunk/{x}/{y}.ply',
    world_seed: 42,
  },
};

test('baked chunk LOD stays preferred when an entry exists', () => {
  const { resolveWorldChunkSource } = subject();
  const entry = {
    ply_file: 'chunk_1_2.ply',
    lod: { 0: 'chunk_1_2_lod0.ply', 2: 'chunk_1_2.ply' },
  };

  assert.deepEqual(resolveWorldChunkSource(ON_DEMAND_MANIFEST, entry, 1, 2, 0), {
    path: 'chunk_1_2_lod0.ply',
    onDemand: false,
  });
  assert.deepEqual(resolveWorldChunkSource(ON_DEMAND_MANIFEST, entry, 1, 2, 1), {
    path: 'chunk_1_2.ply',
    onDemand: false,
  });
});

test('missing baked chunk resolves to the same-origin endpoint with negative coordinates and LOD', () => {
  const { resolveWorldChunkSource, worldChunkAvailable } = subject();

  assert.equal(worldChunkAvailable(ON_DEMAND_MANIFEST, false), true);
  assert.deepEqual(resolveWorldChunkSource(ON_DEMAND_MANIFEST, null, -2, 3, 0), {
    path: '/api/world/chunk/-2/3.ply?lod=0',
    onDemand: true,
  });
});

test('static and malformed grid manifests never request an out-of-bounds chunk', () => {
  const { resolveWorldChunkSource, worldChunkAvailable } = subject();
  const malformed = [
    {},
    { grid: { ...ON_DEMAND_MANIFEST.grid, on_demand: false } },
    { grid: { ...ON_DEMAND_MANIFEST.grid, url_template: 'https://example.com/{x}/{y}' } },
    { grid: { ...ON_DEMAND_MANIFEST.grid, world_seed: null } },
    { grid: { ...ON_DEMAND_MANIFEST.grid, world_seed: Number.MAX_SAFE_INTEGER + 1 } },
  ];

  for (const manifest of malformed) {
    assert.equal(worldChunkAvailable(manifest, false), false);
    assert.equal(resolveWorldChunkSource(manifest, null, 4, 5, 2), null);
  }
});

test('coordinates and LOD must be safe integers from the viewer scheduler', () => {
  const { resolveWorldChunkSource } = subject();

  assert.throws(
    () => resolveWorldChunkSource(ON_DEMAND_MANIFEST, null, 1.5, 2, 0),
    /safe integers/,
  );
  assert.throws(
    () => resolveWorldChunkSource(ON_DEMAND_MANIFEST, null, 1, 2, 3),
    /LOD/,
  );
});

test('only the geographic-envelope response is a terminal chunk failure', () => {
  const { shouldRetryWorldChunkFailure } = subject();

  assert.equal(shouldRetryWorldChunkFailure({
    status: 422,
    apiCode: 'world_bounds_exceeded',
  }), false);
  assert.equal(shouldRetryWorldChunkFailure({
    status: 422,
    apiCode: 'other_validation_error',
  }), true);
  assert.equal(shouldRetryWorldChunkFailure({
    status: 500,
    apiCode: 'world_chunk_render_failed',
  }), true);
  assert.equal(shouldRetryWorldChunkFailure(new TypeError('network failure')), true);
});
