import assert from 'node:assert/strict';
import test from 'node:test';

let chunksModule;
try {
  chunksModule = await import('./splat-chunks-layer.mjs');
} catch (error) {
  chunksModule = { __loadError: error };
}

function subject() {
  assert.equal(
    chunksModule.__loadError,
    undefined,
    `splat-chunks-layer.mjs must load: ${chunksModule.__loadError?.message}`,
  );
  return chunksModule;
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

function fakeSparkModule({ initializationFor = () => Promise.resolve() } = {}) {
  class SparkRenderer {
    constructor(options) {
      this.options = options;
      this.kind = 'spark-renderer';
      this.disposed = false;
    }

    dispose() { this.disposed = true; }
  }

  class SplatMesh {
    constructor(options) {
      this.options = options;
      this.kind = 'splat-mesh';
      this.visible = true;
      this.disposed = false;
      this.initialized = initializationFor(options.url);
      this.quaternion = {
        value: null,
        set: (...values) => { this.quaternion.value = values; },
      };
    }

    dispose() { this.disposed = true; }
  }

  return { SparkRenderer, SplatMesh };
}

const MANIFEST_URL = 'https://studio.example/data/recon-chunks/chunks.json';
const MANIFEST = {
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
      aabb: { min: [0, 0, 0], max: [49, 49, 10] },
    },
    {
      id: '1_0',
      x: 1,
      y: 0,
      ply_file: 'chunk_1_0.ply',
      lod: { 0: 'chunk_1_0_lod0.ply', 1: 'chunk_1_0_lod1.ply', 2: 'chunk_1_0.ply' },
      aabb: { min: [50, 0, 0], max: [99, 49, 10] },
    },
    {
      id: '2_0',
      x: 2,
      y: 0,
      ply_file: 'chunk_2_0.ply',
      lod: { 0: 'chunk_2_0_lod0.ply', 1: 'chunk_2_0_lod1.ply', 2: 'chunk_2_0.ply' },
      aabb: { min: [100, 0, 0], max: [149, 49, 10] },
    },
  ],
};

test('shares one SparkRenderer across bounded absolute-coordinate chunk meshes', async () => {
  const { createSpatialSplatLayer } = subject();
  const scene = fakeScene();
  const renderer = { kind: 'three-renderer' };
  const layer = createSpatialSplatLayer({
    scene,
    renderer,
    importSpark: async () => fakeSparkModule(),
    cacheMax: 2,
  });

  const loaded = await layer.load({
    manifest: MANIFEST,
    manifestUrl: MANIFEST_URL,
    visible: true,
  });
  assert.equal(loaded.mode, 'spark-chunks');

  await layer.update({ cameraWorld: [25, 25, 2] });

  const renderers = scene.children.filter((item) => item.kind === 'spark-renderer');
  const meshes = scene.children.filter((item) => item.kind === 'splat-mesh');
  assert.equal(renderers.length, 1);
  assert.equal(renderers[0].options.renderer, renderer);
  assert.equal(meshes.length, 2);
  assert.equal(meshes.every((mesh) => mesh.position === undefined), true);
  assert.equal(meshes.every((mesh) => mesh.quaternion.value?.length === 4), true);
  assert.equal(layer.getState().active, 2);
});

test('moving the camera replaces LODs and evicts non-needed chunks within cacheMax', async () => {
  const { createSpatialSplatLayer } = subject();
  const scene = fakeScene();
  const layer = createSpatialSplatLayer({
    scene,
    renderer: {},
    importSpark: async () => fakeSparkModule(),
    cacheMax: 2,
  });

  await layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  await layer.update({ cameraWorld: [25, 25, 2] });
  const oldChunk0 = scene.children.find(
    (item) => item.options?.url?.endsWith('/chunk_0_0.ply'),
  );
  const oldChunk1 = scene.children.find(
    (item) => item.options?.url?.endsWith('/chunk_1_0_lod1.ply'),
  );

  await layer.update({ cameraWorld: [125, 25, 2], lodOverride: 0 });

  assert.equal(oldChunk0.disposed, true);
  assert.equal(oldChunk1.disposed, true);
  assert.ok(layer.getState().active <= 2);
  assert.deepEqual(
    scene.children.filter((item) => item.kind === 'splat-mesh')
      .map((item) => item.options.url.split('/').at(-1))
      .sort(),
    ['chunk_1_0_lod0.ply', 'chunk_2_0_lod0.ply'],
  );
});

test('visibility toggles only desired active chunks', async () => {
  const { createSpatialSplatLayer } = subject();
  const scene = fakeScene();
  const layer = createSpatialSplatLayer({
    scene,
    renderer: {},
    importSpark: async () => fakeSparkModule(),
  });

  await layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  await layer.update({ cameraWorld: [25, 25, 2] });
  layer.setVisible(false);
  assert.equal(
    scene.children.filter((item) => item.kind === 'splat-mesh')
      .every((item) => item.visible === false),
    true,
  );
  layer.setVisible(true);
  assert.equal(
    scene.children.filter((item) => item.kind === 'splat-mesh')
      .every((item) => item.visible === true),
    true,
  );
});

test('Spark import failure leaves no partial renderer and reports truthful fallback', async () => {
  const { createSpatialSplatLayer } = subject();
  const scene = fakeScene();
  const layer = createSpatialSplatLayer({
    scene,
    renderer: {},
    importSpark: async () => { throw new Error('CDN offline'); },
  });

  const result = await layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });

  assert.equal(result.mode, 'dc-point-preview');
  assert.equal(result.reason, 'Spark chunks unavailable: CDN offline');
  assert.deepEqual(scene.children, []);
});

test('one bad chunk is cleaned up without dropping the shared renderer', async () => {
  const { createSpatialSplatLayer } = subject();
  const scene = fakeScene();
  const layer = createSpatialSplatLayer({
    scene,
    renderer: {},
    importSpark: async () => fakeSparkModule({
      initializationFor: (url) => (
        url.endsWith('chunk_1_0_lod1.ply')
          ? Promise.reject(new Error('bad chunk'))
          : Promise.resolve()
      ),
    }),
  });

  await layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  await layer.update({ cameraWorld: [25, 25, 2] });

  assert.equal(scene.children.filter((item) => item.kind === 'spark-renderer').length, 1);
  assert.equal(
    scene.children.some((item) => item.options?.url?.endsWith('chunk_1_0_lod1.ply')),
    false,
  );
  assert.match(layer.getState().last_error, /bad chunk/);
});

test('a stale initialization after dispose cannot re-enter the scene', async () => {
  const { createSpatialSplatLayer } = subject();
  const pending = deferred();
  const scene = fakeScene();
  const layer = createSpatialSplatLayer({
    scene,
    renderer: {},
    importSpark: async () => fakeSparkModule({
      initializationFor: () => pending.promise,
    }),
  });

  await layer.load({ manifest: MANIFEST, manifestUrl: MANIFEST_URL });
  const update = layer.update({ cameraWorld: [25, 25, 2] });
  await new Promise((resolve) => setImmediate(resolve));
  const pendingMeshes = scene.children.filter((item) => item.kind === 'splat-mesh');
  assert.ok(pendingMeshes.length > 0);

  layer.dispose();
  pending.resolve();
  await update;

  assert.deepEqual(scene.children, []);
  assert.equal(pendingMeshes.every((mesh) => mesh.disposed), true);
  assert.equal(layer.getState().mode, 'dc-point-preview');
});
