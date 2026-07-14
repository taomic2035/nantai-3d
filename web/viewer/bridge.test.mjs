import assert from 'node:assert/strict';
import test from 'node:test';

let bridgeModule;
try {
  bridgeModule = await import('./bridge.mjs');
} catch (error) {
  bridgeModule = { __loadError: error };
}

function subject() {
  assert.equal(
    bridgeModule.__loadError,
    undefined,
    `bridge.mjs must load: ${bridgeModule.__loadError?.message}`,
  );
  return bridgeModule;
}

function fakeWindow(origin = 'https://studio.example') {
  const sent = [];
  const listeners = new Map();
  const parent = {
    postMessage(message, targetOrigin) {
      sent.push({ message, targetOrigin });
    },
  };
  const windowObject = {
    location: { origin },
    parent,
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
  };
  return { windowObject, parent, sent, listeners };
}

function command(type, requestId, payload = {}) {
  return {
    channel: 'nantai-viewer',
    schema_version: 1,
    type,
    request_id: requestId,
    payload,
  };
}

test('start announces same-origin ready with honest DC point capabilities', () => {
  const { createViewerBridge, VIEWER_CAPABILITIES } = subject();
  const fake = fakeWindow();
  const bridge = createViewerBridge({ windowObject: fake.windowObject, handlers: {} });

  bridge.start();

  assert.equal(fake.listeners.has('message'), true);
  assert.equal(fake.sent.length, 1);
  assert.equal(fake.sent[0].targetOrigin, fake.windowObject.location.origin);
  assert.equal(fake.sent[0].message.type, 'ready');
  assert.deepEqual(fake.sent[0].message.payload.capabilities, VIEWER_CAPABILITIES);
  assert.equal(VIEWER_CAPABILITIES.renderer.fidelity, 'dc-point-preview');
  assert.equal(VIEWER_CAPABILITIES.renderer.anisotropic_covariance, false);
  assert.deepEqual(VIEWER_CAPABILITIES.dynamic_artifact_kinds, ['recon-manifest']);
  assert.deepEqual(VIEWER_CAPABILITIES.three_dgs_properties.consumed, []);
  assert.ok(VIEWER_CAPABILITIES.commands.includes('resetCamera'));
  assert.ok(VIEWER_CAPABILITIES.commands.includes('setBounds'));
});

test('capability factory only claims Gaussian rendering for an active Spark layer', () => {
  const { createViewerCapabilities } = subject();
  const fallback = createViewerCapabilities('dc-point-preview');
  assert.equal(fallback.renderer.id, 'three-points');
  assert.equal(fallback.renderer.fidelity, 'dc-point-preview');
  assert.equal(fallback.renderer.anisotropic_covariance, false);
  assert.equal(fallback.renderer.spherical_harmonics, false);
  assert.equal(fallback.renderer.max_sh_degree, 0);
  assert.deepEqual(fallback.three_dgs_properties.consumed, []);
  assert.equal(fallback.artifact_kinds.includes('3dgs-ply'), false);
  assert.equal(fallback.lod.reconstruction_tiers, true);

  const spark = createViewerCapabilities('spark');
  assert.equal(spark.renderer.id, 'spark');
  assert.equal(spark.renderer.version, '2.1.0');
  assert.equal(spark.renderer.fidelity, 'full-3dgs');
  assert.equal(spark.renderer.anisotropic_covariance, true);
  assert.equal(spark.renderer.alpha_composite, true);
  assert.equal(spark.renderer.spherical_harmonics, true);
  assert.equal(spark.renderer.max_sh_degree, 3);
  assert.equal(spark.artifact_kinds.includes('3dgs-ply'), true);
  assert.equal(spark.lod.reconstruction_tiers, false);
  assert.deepEqual(spark.three_dgs_properties.consumed, [
    'f_dc_*', 'f_rest_* (SH0-SH3)', 'opacity', 'scale_*', 'rot_*',
  ]);
});

test('bridge resolves capabilities at announcement time and can report a renderer change', () => {
  const { createViewerBridge, createViewerCapabilities } = subject();
  const fake = fakeWindow();
  let mode = 'dc-point-preview';
  const bridge = createViewerBridge({
    windowObject: fake.windowObject,
    handlers: {},
    capabilities: () => createViewerCapabilities(mode),
  });

  bridge.start();
  assert.equal(fake.sent[0].message.type, 'ready');
  assert.equal(fake.sent[0].message.payload.capabilities.renderer.id, 'three-points');

  mode = 'spark';
  bridge.announceCapabilities();
  assert.equal(fake.sent[1].message.type, 'capabilitiesChanged');
  assert.equal(fake.sent[1].message.payload.capabilities.renderer.id, 'spark');
});

test('start does not post ready back to the top-level viewer itself', () => {
  const { createViewerBridge } = subject();
  const sent = [];
  const windowObject = {
    location: { origin: 'https://viewer.example' },
    addEventListener() {},
    removeEventListener() {},
    postMessage(message) { sent.push(message); },
  };
  windowObject.parent = windowObject;

  createViewerBridge({ windowObject, handlers: {} }).start();
  assert.deepEqual(sent, []);
});

test('getState preserves request_id and returns stateChanged', async () => {
  const { createViewerBridge } = subject();
  const fake = fakeWindow();
  const bridge = createViewerBridge({
    windowObject: fake.windowObject,
    handlers: { getState: () => ({ lod: 1, layers: { world: true } }) },
  });

  await bridge.handleMessage({
    origin: fake.windowObject.location.origin,
    source: fake.parent,
    data: command('getState', 'req-17'),
  });

  assert.equal(fake.sent.length, 1);
  assert.equal(fake.sent[0].message.type, 'stateChanged');
  assert.equal(fake.sent[0].message.request_id, 'req-17');
  assert.deepEqual(fake.sent[0].message.payload.result, {
    lod: 1,
    layers: { world: true },
  });
});

test('loadArtifact responds with artifactLoaded and the same request_id', async () => {
  const { createViewerBridge } = subject();
  const fake = fakeWindow();
  const bridge = createViewerBridge({
    windowObject: fake.windowObject,
    handlers: { loadArtifact: ({ url }) => ({ url, loaded: true }) },
  });

  await bridge.handleMessage({
    origin: fake.windowObject.location.origin,
    source: fake.parent,
    data: command('loadArtifact', 'load-3', { url: '/artifact.json' }),
  });

  assert.equal(fake.sent[0].message.type, 'artifactLoaded');
  assert.equal(fake.sent[0].message.request_id, 'load-3');
  assert.equal(fake.sent[0].message.payload.result.loaded, true);
});

test('unsupported commands return a correlated error', async () => {
  const { createViewerBridge } = subject();
  const fake = fakeWindow();
  const bridge = createViewerBridge({ windowObject: fake.windowObject, handlers: {} });

  await bridge.handleMessage({
    origin: fake.windowObject.location.origin,
    source: fake.parent,
    data: command('launchSpark', 'bad-9'),
  });

  assert.equal(fake.sent[0].message.type, 'error');
  assert.equal(fake.sent[0].message.request_id, 'bad-9');
  assert.equal(fake.sent[0].message.payload.code, 'unsupported-command');
});

test('commands without request_id return an invalid-request error', async () => {
  const { createViewerBridge } = subject();
  const fake = fakeWindow();
  const bridge = createViewerBridge({
    windowObject: fake.windowObject,
    handlers: { getState: () => ({ shouldNotRun: true }) },
  });
  const message = command('getState', 'temporary');
  delete message.request_id;

  await bridge.handleMessage({
    origin: fake.windowObject.location.origin,
    source: fake.parent,
    data: message,
  });

  assert.equal(fake.sent[0].message.type, 'error');
  assert.equal(fake.sent[0].message.request_id, null);
  assert.equal(fake.sent[0].message.payload.code, 'invalid-request');
});

test('bridge ignores cross-origin messages', async () => {
  const { createViewerBridge } = subject();
  const fake = fakeWindow();
  const bridge = createViewerBridge({
    windowObject: fake.windowObject,
    handlers: { getState: () => ({}) },
  });

  await bridge.handleMessage({
    origin: 'https://attacker.example',
    source: fake.parent,
    data: command('getState', 'ignored'),
  });
  assert.equal(fake.sent.length, 0);
});

test('artifactProvenance never infers trust from engine names', () => {
  const { artifactProvenance } = subject();
  assert.deepEqual(artifactProvenance({ engine: 'mock' }), {
    requested_engine: 'unknown',
    actual_engine: 'mock',
    synthetic: 'unknown',
    frame: 'unknown',
    units: 'unknown',
    handedness: 'unknown',
    geometry_usability: 'unknown',
    artifact_fidelity: 'unknown',
    viewer_fidelity: 'dc-point-preview',
  });

  assert.deepEqual(
    artifactProvenance({
      requested_engine: 'auto',
      actual_engine: 'import',
      synthetic: false,
      world_frame: { name: 'village-enu', units: 'meters', handedness: 'right' },
      geometry_usability: 'metric',
      render_fidelity: 'full-3dgs',
    }),
    {
      requested_engine: 'auto',
      actual_engine: 'import',
      synthetic: false,
      frame: 'village-enu',
      units: 'meters',
      handedness: 'right',
      geometry_usability: 'metric',
      artifact_fidelity: 'full-3dgs',
      viewer_fidelity: 'dc-point-preview',
    },
  );
});

test('artifactProvenance reads the reconstruction v2 coordinate contract', () => {
  const { artifactProvenance } = subject();
  assert.deepEqual(
    artifactProvenance({
      schema_version: 2,
      coordinate_contract: {
        target_frame: {
          frame_id: 'village-enu',
          units: 'meters',
          handedness: 'right',
        },
      },
      provenance: {
        requested_reconstruction_engine: 'auto',
        actual_reconstruction_engine: 'imported-3dgs',
        synthetic: false,
        geometry_usability: 'metric-aligned',
        render_fidelity: 'full-3dgs',
      },
    }),
    {
      requested_engine: 'auto',
      actual_engine: 'imported-3dgs',
      synthetic: false,
      frame: 'village-enu',
      units: 'meters',
      handedness: 'right',
      geometry_usability: 'metric-aligned',
      artifact_fidelity: 'full-3dgs',
      viewer_fidelity: 'dc-point-preview',
    },
  );
});

test('artifactProvenance reports the fidelity of the artifact actually being rendered', () => {
  const { artifactProvenance, createViewerCapabilities } = subject();
  const manifest = {
    artifacts: {
      full_3dgs: { fidelity: 'full-3dgs' },
      lod: { 2: { fidelity: 'dc-point-preview' } },
    },
    provenance: {
      artifact_fidelity: {
        full_3dgs: 'full-3dgs',
        lod_preview: 'dc-point-preview',
      },
      render_fidelity: 'dc-point-preview',
    },
  };

  const spark = artifactProvenance(
    manifest,
    createViewerCapabilities('spark'),
  );
  assert.equal(spark.artifact_fidelity, 'full-3dgs');
  assert.equal(spark.viewer_fidelity, 'full-3dgs');

  const fallback = artifactProvenance(
    manifest,
    createViewerCapabilities('dc-point-preview'),
  );
  assert.equal(fallback.artifact_fidelity, 'dc-point-preview');
  assert.equal(fallback.viewer_fidelity, 'dc-point-preview');
});

test('resolveArtifactUrl resolves LOD files beside their manifest', () => {
  const { resolveArtifactUrl } = subject();
  assert.equal(
    resolveArtifactUrl(
      'https://studio.example/data/recon/recon_manifest.json',
      'recon_lod1.ply',
    ),
    'https://studio.example/data/recon/recon_lod1.ply',
  );
});
