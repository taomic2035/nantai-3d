import assert from 'node:assert/strict';
import test from 'node:test';

let pointModule;
try {
  pointModule = await import('./spatial-point-layer.mjs');
} catch (error) {
  pointModule = { __loadError: error };
}

function subject() {
  assert.equal(
    pointModule.__loadError,
    undefined,
    `spatial-point-layer.mjs must load: ${pointModule.__loadError?.message}`,
  );
  return pointModule;
}

function fakeScene() {
  const children = [];
  return {
    children,
    add(object) {
      if (!children.includes(object)) children.push(object);
    },
    remove(object) {
      const index = children.indexOf(object);
      if (index >= 0) children.splice(index, 1);
    },
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function pointMesh(url) {
  return { url, visible: true, disposed: false, kind: 'point-mesh' };
}

const MANIFEST_URL = 'https://studio.example/data/recon-chunks/chunks.json';
const MANIFEST = {
  schema_version: 1,
  kind: 'spatial-chunks',
  chunk_size_m: 50,
  lod_fractions: { 0: 0.07, 1: 0.25, 2: 1 },
  chunks: [
    {
      id: '0_0',
      x: 0,
      y: 0,
      point_count: 200,
      ply_file: 'chunk_0_0.ply',
      lod: { 0: 'chunk_0_0_lod0.ply', 1: 'chunk_0_0_lod1.ply', 2: 'chunk_0_0.ply' },
      aabb: { min: [0, 0, 0], max: [49, 49, 10] },
    },
    {
      id: '1_0',
      x: 1,
      y: 0,
      point_count: 120,
      ply_file: 'chunk_1_0.ply',
      lod: { 0: 'chunk_1_0_lod0.ply', 1: 'chunk_1_0_lod1.ply', 2: 'chunk_1_0.ply' },
      aabb: { min: [50, 0, 0], max: [99, 49, 10] },
    },
    {
      id: '2_0',
      x: 2,
      y: 0,
      point_count: 80,
      ply_file: 'chunk_2_0.ply',
      lod: { 0: 'chunk_2_0_lod0.ply', 1: 'chunk_2_0_lod1.ply', 2: 'chunk_2_0.ply' },
      aabb: { min: [100, 0, 0], max: [149, 49, 10] },
    },
  ],
};

test('loads nearby point chunks and keeps a bounded cache', async () => {
  const { createSpatialPointLayer } = subject();
  const scene = fakeScene();
  const loadedUrls = [];
  const layer = createSpatialPointLayer({
    scene,
    cacheMax: 2,
    loadPointMesh: async ({ url }) => {
      loadedUrls.push(url);
      return pointMesh(url);
    },
    disposeMesh: (mesh) => { mesh.disposed = true; },
  });

  const loaded = layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  assert.equal(loaded.mode, 'dc-point-chunks');
  await layer.update({ cameraWorld: [25, 25, 2] });

  assert.equal(scene.children.length, 2);
  assert.equal(layer.getState().active, 2);
  assert.equal(layer.getState().active_estimated_points, 230);
  assert.deepEqual(layer.getState().active_lod_fractions, [0.25, 1]);
  assert.deepEqual(
    loadedUrls.map((url) => url.split('/').at(-1)).sort(),
    ['chunk_0_0.ply', 'chunk_1_0_lod1.ply'],
  );
});

test('keeps aggregate point chunk density unknown when any active evidence is incomplete', async () => {
  const { createSpatialPointLayer } = subject();
  const scene = fakeScene();
  const incomplete = {
    ...MANIFEST,
    chunks: MANIFEST.chunks.map((entry, index) => (
      index === 1 ? { ...entry, point_count: undefined } : entry
    )),
  };
  const layer = createSpatialPointLayer({
    scene,
    cacheMax: 2,
    loadPointMesh: async ({ url }) => pointMesh(url),
    disposeMesh: (mesh) => { mesh.disposed = true; },
  });

  layer.load({ manifest: incomplete, manifestUrl: MANIFEST_URL });
  await layer.update({ cameraWorld: [25, 25, 2] });

  assert.equal(layer.getState().active, 2);
  assert.equal(layer.getState().active_estimated_points, null);
  assert.equal(layer.getState().active_lod_fractions, null);
});

test('LOD replacement disposes old point meshes and evicts non-needed chunks', async () => {
  const { createSpatialPointLayer } = subject();
  const scene = fakeScene();
  const layer = createSpatialPointLayer({
    scene,
    cacheMax: 2,
    loadPointMesh: async ({ url }) => pointMesh(url),
    disposeMesh: (mesh) => { mesh.disposed = true; },
  });

  layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  await layer.update({ cameraWorld: [25, 25, 2] });
  const old0 = scene.children.find((mesh) => mesh.url.endsWith('/chunk_0_0.ply'));
  const old1 = scene.children.find((mesh) => mesh.url.endsWith('/chunk_1_0_lod1.ply'));

  await layer.update({ cameraWorld: [125, 25, 2], lodOverride: 0 });

  assert.equal(old0.disposed, true);
  assert.equal(old1.disposed, true);
  assert.deepEqual(
    scene.children.map((mesh) => mesh.url.split('/').at(-1)).sort(),
    ['chunk_1_0_lod0.ply', 'chunk_2_0_lod0.ply'],
  );
});

test('visibility and dispose cover every active point chunk', async () => {
  const { createSpatialPointLayer } = subject();
  const scene = fakeScene();
  const layer = createSpatialPointLayer({
    scene,
    loadPointMesh: async ({ url }) => pointMesh(url),
    disposeMesh: (mesh) => { mesh.disposed = true; },
  });

  layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  await layer.update({ cameraWorld: [25, 25, 2] });
  const meshes = [...scene.children];
  layer.setVisible(false);
  assert.equal(meshes.every((mesh) => mesh.visible === false), true);
  layer.dispose();
  assert.deepEqual(scene.children, []);
  assert.equal(meshes.every((mesh) => mesh.disposed), true);
});

test('a stale point load after dispose is discarded and disposed', async () => {
  const { createSpatialPointLayer } = subject();
  const pending = deferred();
  const scene = fakeScene();
  const staleMeshes = [];
  const layer = createSpatialPointLayer({
    scene,
    loadPointMesh: async ({ url }) => {
      await pending.promise;
      const mesh = pointMesh(url);
      staleMeshes.push(mesh);
      return mesh;
    },
    disposeMesh: (mesh) => { mesh.disposed = true; },
  });

  layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  const update = layer.update({ cameraWorld: [25, 25, 2] });
  layer.dispose();
  pending.resolve();
  await update;

  assert.deepEqual(scene.children, []);
  assert.ok(staleMeshes.length > 0);
  assert.equal(staleMeshes.every((mesh) => mesh.disposed), true);
});

test('one failed point chunk does not hide successfully loaded neighbors', async () => {
  const { createSpatialPointLayer } = subject();
  const scene = fakeScene();
  const layer = createSpatialPointLayer({
    scene,
    loadPointMesh: async ({ url }) => {
      if (url.endsWith('chunk_1_0_lod1.ply')) throw new Error('bad PLY');
      return pointMesh(url);
    },
    disposeMesh: (mesh) => { mesh.disposed = true; },
  });

  layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  await layer.update({ cameraWorld: [25, 25, 2] });

  assert.ok(scene.children.length > 0);
  assert.match(layer.getState().last_error, /bad PLY/);
});
