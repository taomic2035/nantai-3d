import assert from 'node:assert/strict';
import test from 'node:test';

let splatModule;
try {
  splatModule = await import('./splat-layer.mjs');
} catch (error) {
  splatModule = { __loadError: error };
}

function subject() {
  assert.equal(
    splatModule.__loadError,
    undefined,
    `splat-layer.mjs must load: ${splatModule.__loadError?.message}`,
  );
  return splatModule;
}

function fakeScene() {
  const children = [];
  return {
    children,
    add(object) { children.push(object); },
    remove(object) {
      const index = children.indexOf(object);
      if (index >= 0) children.splice(index, 1);
    },
  };
}

function fakeSparkModule({ initialize = Promise.resolve() } = {}) {
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
      this.initialized = initialize;
      this.quaternion = {
        value: null,
        set: (...values) => { this.quaternion.value = values; },
      };
    }

    dispose() { this.disposed = true; }
  }

  return { SparkRenderer, SplatMesh };
}

test('resolveFullSplatUrl selects full_3dgs relative to the recon manifest', () => {
  const { resolveFullSplatUrl } = subject();
  assert.equal(
    resolveFullSplatUrl(
      'https://studio.example/data/recon/recon_manifest.json',
      { full_3dgs: 'scene_full.ply' },
    ),
    'https://studio.example/data/recon/scene_full.ply',
  );
  assert.equal(
    resolveFullSplatUrl('https://studio.example/recon.json', { lod: {} }),
    null,
  );
});

test('ENU_TO_THREE_QUATERNION rotates (E,N,U) to (E,U,-N)', () => {
  const { ENU_TO_THREE_QUATERNION, rotateVectorByQuaternion } = subject();
  const actual = rotateVectorByQuaternion(
    [3, 5, 7],
    ENU_TO_THREE_QUATERNION,
  );
  assert.ok(Math.abs(actual[0] - 3) < 1e-12);
  assert.ok(Math.abs(actual[1] - 7) < 1e-12);
  assert.ok(Math.abs(actual[2] + 5) < 1e-12);
});

test('successful Spark initialization activates the splat and applies ENU rotation', async () => {
  const { createSplatLayer, ENU_TO_THREE_QUATERNION } = subject();
  const scene = fakeScene();
  const renderer = { kind: 'three-webgl-renderer' };
  const layer = createSplatLayer({
    scene,
    renderer,
    importSpark: async () => fakeSparkModule(),
  });

  const result = await layer.load({
    manifestUrl: 'https://studio.example/data/recon/recon_manifest.json',
    manifest: { full_3dgs: 'scene_full.ply' },
    visible: false,
  });

  assert.deepEqual(result, {
    mode: 'spark',
    fidelity: 'full-3dgs',
    url: 'https://studio.example/data/recon/scene_full.ply',
    reason: null,
  });
  const sparkRenderer = scene.children.find((item) => item.kind === 'spark-renderer');
  const splatMesh = scene.children.find((item) => item.kind === 'splat-mesh');
  assert.equal(sparkRenderer.options.renderer, renderer);
  assert.equal(splatMesh.options.url, result.url);
  assert.deepEqual(splatMesh.quaternion.value, [
    ENU_TO_THREE_QUATERNION.x,
    ENU_TO_THREE_QUATERNION.y,
    ENU_TO_THREE_QUATERNION.z,
    ENU_TO_THREE_QUATERNION.w,
  ]);
  assert.equal(splatMesh.visible, false);
  assert.equal(layer.getState().mode, 'spark');
});

test('a Spark import failure leaves no partial objects and reports DC fallback', async () => {
  const { createSplatLayer } = subject();
  const scene = fakeScene();
  const layer = createSplatLayer({
    scene,
    renderer: {},
    importSpark: async () => { throw new Error('CDN offline'); },
  });

  const result = await layer.load({
    manifestUrl: 'https://studio.example/data/recon/recon_manifest.json',
    manifest: { full_3dgs: 'scene_full.ply' },
  });

  assert.equal(result.mode, 'dc-point-preview');
  assert.equal(result.fidelity, 'dc-point-preview');
  assert.equal(result.reason, 'Spark unavailable: CDN offline');
  assert.deepEqual(scene.children, []);
  assert.equal(layer.getState().mode, 'dc-point-preview');
});

test('a SplatMesh initialization failure disposes partial Spark objects', async () => {
  const { createSplatLayer } = subject();
  const scene = fakeScene();
  const spark = fakeSparkModule({ initialize: Promise.reject(new Error('bad PLY')) });
  const layer = createSplatLayer({
    scene,
    renderer: {},
    importSpark: async () => spark,
  });

  const result = await layer.load({
    manifestUrl: 'https://studio.example/data/recon/recon_manifest.json',
    manifest: { full_3dgs: 'scene_full.ply' },
  });

  assert.equal(result.mode, 'dc-point-preview');
  assert.equal(result.reason, 'Spark unavailable: bad PLY');
  assert.deepEqual(scene.children, []);
});

test('a manifest without full_3dgs stays on DC fallback without importing Spark', async () => {
  const { createSplatLayer } = subject();
  let importCalls = 0;
  const layer = createSplatLayer({
    scene: fakeScene(),
    renderer: {},
    importSpark: async () => { importCalls += 1; return fakeSparkModule(); },
  });

  const result = await layer.load({
    manifestUrl: 'https://studio.example/data/recon/recon_manifest.json',
    manifest: { lod: { 2: 'recon_lod2.ply' } },
  });

  assert.equal(result.mode, 'dc-point-preview');
  assert.equal(result.reason, 'full_3dgs artifact missing');
  assert.equal(importCalls, 0);
});

test('a stalled Spark load times out into DC fallback instead of blocking the viewer', async () => {
  const { createSplatLayer } = subject();
  const layer = createSplatLayer({
    scene: fakeScene(),
    renderer: {},
    timeoutMs: 5,
    importSpark: () => new Promise(() => {}),
  });

  const result = await layer.load({
    manifestUrl: 'https://studio.example/data/recon/recon_manifest.json',
    manifest: { full_3dgs: 'scene_full.ply' },
  });

  assert.equal(result.mode, 'dc-point-preview');
  assert.equal(result.reason, 'Spark unavailable: timed out after 5ms');
});
