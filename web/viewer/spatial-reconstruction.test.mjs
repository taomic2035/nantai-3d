import assert from 'node:assert/strict';
import test from 'node:test';

let spatialModule;
try {
  spatialModule = await import('./spatial-reconstruction.mjs');
} catch (error) {
  spatialModule = { __loadError: error };
}

function subject() {
  assert.equal(
    spatialModule.__loadError,
    undefined,
    `spatial-reconstruction.mjs must load: ${spatialModule.__loadError?.message}`,
  );
  return spatialModule;
}

const MANIFEST_URL = 'https://studio.example/data/recon-chunks/chunks.json';
const VALID_MANIFEST = {
  schema_version: 1,
  kind: 'spatial-chunks',
  chunk_size_m: 50,
  chunks: [
    {
      id: '0_0',
      x: 0,
      y: 0,
      ply_file: 'chunk_0_0.ply',
      lod: { 0: 'chunk_0_0_lod0.ply', 1: 'chunk_0_0_lod1.ply', 2: 'chunk_0_0.ply' },
      aabb: { min: [0, 0, -2], max: [49, 49, 12] },
    },
    {
      id: '1_0',
      x: 1,
      y: 0,
      ply_file: 'chunk_1_0.ply',
      lod: { 0: 'chunk_1_0_lod0.ply', 1: 'chunk_1_0_lod1.ply', 2: 'chunk_1_0.ply' },
      aabb: { min: [50, 0, -1], max: [99, 49, 11] },
    },
    {
      id: '2_0',
      x: 2,
      y: 0,
      ply_file: 'chunk_2_0.ply',
      lod: { 0: 'chunk_2_0_lod0.ply', 1: 'chunk_2_0_lod1.ply', 2: 'chunk_2_0.ply' },
      aabb: { min: [100, 0, 0], max: [149, 49, 10] },
    },
    {
      id: '5_0',
      x: 5,
      y: 0,
      ply_file: 'chunk_5_0.ply',
      lod: { 2: 'chunk_5_0.ply' },
      aabb: { min: [250, 0, 0], max: [299, 49, 10] },
    },
  ],
};

test('accepts only explicit static spatial-chunks manifests with valid baked entries', () => {
  const { isSpatialChunkManifest } = subject();

  assert.equal(isSpatialChunkManifest(VALID_MANIFEST), true);
  assert.equal(isSpatialChunkManifest({ ...VALID_MANIFEST, kind: 'world' }), false);
  assert.equal(isSpatialChunkManifest({ ...VALID_MANIFEST, chunks: [] }), false);
  assert.equal(isSpatialChunkManifest({ ...VALID_MANIFEST, chunk_size_m: 0 }), false);
  assert.equal(
    isSpatialChunkManifest({ ...VALID_MANIFEST, grid: { on_demand: true } }),
    false,
  );
  assert.equal(
    isSpatialChunkManifest({
      ...VALID_MANIFEST,
      chunks: [{ ...VALID_MANIFEST.chunks[0], x: 0.5 }],
    }),
    false,
  );
  assert.equal(
    isSpatialChunkManifest({
      ...VALID_MANIFEST,
      chunks: [{
        ...VALID_MANIFEST.chunks[0],
        aabb: { min: [1, 0, 0], max: [0, 1, 1] },
      }],
    }),
    false,
  );
});

test('resolves only safe manifest-relative baked LOD files', () => {
  const { resolveSpatialChunkUrl } = subject();
  const entry = VALID_MANIFEST.chunks[0];

  assert.equal(
    resolveSpatialChunkUrl(MANIFEST_URL, entry, 0),
    'https://studio.example/data/recon-chunks/chunk_0_0_lod0.ply',
  );
  assert.equal(
    resolveSpatialChunkUrl(MANIFEST_URL, { ...entry, lod: {} }, 1),
    'https://studio.example/data/recon-chunks/chunk_0_0.ply',
  );

  for (const path of [
    '../escape.ply',
    '%2e%2e/escape.ply',
    '/absolute.ply',
    'https://attacker.example/chunk.ply',
    'folder\\chunk.ply',
    'chunk.ply?lod=2',
  ]) {
    assert.equal(
      resolveSpatialChunkUrl(
        MANIFEST_URL,
        { ...entry, ply_file: null, lod: { 2: path } },
        2,
      ),
      null,
    );
  }
  assert.throws(() => resolveSpatialChunkUrl(MANIFEST_URL, entry, 3), /LOD/);
});

test('computes zero distance inside an AABB and Euclidean distance outside', () => {
  const { horizontalDistanceToAabb } = subject();
  const aabb = VALID_MANIFEST.chunks[0].aabb;

  assert.equal(horizontalDistanceToAabb([25, 25, 3], aabb), 0);
  assert.equal(horizontalDistanceToAabb([52, 25, 3], aabb), 3);
  assert.equal(horizontalDistanceToAabb([52, 53, 3], aabb), 5);
});

test('selects nearby AABBs with deterministic near-high far-low LOD', () => {
  const { selectSpatialChunkRequests } = subject();

  assert.deepEqual(
    selectSpatialChunkRequests(VALID_MANIFEST, [25, 25, 3], { radiusChunks: 2 })
      .map(({ key, lod, distance }) => [key, lod, distance]),
    [
      ['0_0', 2, 0],
      ['1_0', 1, 25],
      ['2_0', 0, 75],
    ],
  );
  assert.deepEqual(
    selectSpatialChunkRequests(
      VALID_MANIFEST,
      [25, 25, 3],
      { radiusChunks: 2, lodOverride: 0 },
    ).map(({ key, lod }) => [key, lod]),
    [['0_0', 0], ['1_0', 0], ['2_0', 0]],
  );
});
