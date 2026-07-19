import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import test from 'node:test';

let profileModule;
try {
  profileModule = await import('./material-profile.mjs');
} catch (error) {
  profileModule = { __loadError: error };
}

function subject() {
  assert.equal(
    profileModule.__loadError,
    undefined,
    `material-profile.mjs must load: ${profileModule.__loadError?.message}`,
  );
  return profileModule;
}

const H3 = 'h3-ai-ktx2-4k';
const H2 = 'h2-png-1k-fallback';
const CANARY_BYTES = new TextEncoder().encode('verified-ktx2-canary');

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function canary(overrides = {}) {
  return {
    url: '/api/world/mesh-textures/1/h3/canary.ktx2',
    sha256: sha256(CANARY_BYTES),
    bytes: CANARY_BYTES.byteLength,
    media_type: 'image/ktx2',
    ...overrides,
  };
}

function fakeRenderer() {
  return { capabilities: { isWebGL2: true } };
}

function loaderHarness({ decodeError = null, supportError = null } = {}) {
  const events = [];
  const texture = {
    dispose() {
      events.push('canary-texture-disposed');
    },
  };
  const loader = {
    setTranscoderPath(path) {
      events.push(['transcoder-path', path]);
      return this;
    },
    detectSupport(renderer) {
      events.push(['detect-support', renderer]);
      if (supportError) throw supportError;
      return this;
    },
    parse(bytes, onLoad, onError) {
      events.push(['parse', bytes.byteLength]);
      if (decodeError) onError(decodeError);
      else onLoad(texture);
    },
    dispose() {
      events.push('loader-disposed');
    },
  };
  return { loader, events };
}

test('profile freezes H3 only after renderer canary succeeds', async () => {
  const { createMaterialProfileController } = subject();
  const harness = loaderHarness();
  const controller = createMaterialProfileController({
    createKtx2Loader: () => harness.loader,
    verifyAndReadCanary: async () => CANARY_BYTES,
  });

  const selected = await controller.select({
    renderer: fakeRenderer(),
    canary: canary(),
    predictedCompressedBytes: 128 * 1024 * 1024,
  });

  assert.equal(selected.profileId, H3);
  assert.equal(selected.fallbackReason, null);
  assert.equal(controller.snapshot().state, 'frozen');
  assert.equal(controller.getRuntimeLoader(), harness.loader);
  assert.deepEqual(harness.events, [
    [
      'transcoder-path',
      './vendor/three/examples/jsm/libs/basis/',
    ],
    ['detect-support', fakeRenderer()],
    ['parse', CANARY_BYTES.byteLength],
    'canary-texture-disposed',
  ]);
});

test('over-budget selection freezes explicit H2 without creating a loader', async () => {
  const { createMaterialProfileController } = subject();
  let loaderCreations = 0;
  const controller = createMaterialProfileController({
    createKtx2Loader() {
      loaderCreations += 1;
      return loaderHarness().loader;
    },
    verifyAndReadCanary: async () => CANARY_BYTES,
  });

  const selected = await controller.select({
    renderer: fakeRenderer(),
    canary: canary(),
    predictedCompressedBytes: 512 * 1024 * 1024 + 1,
  });

  assert.equal(selected.profileId, H2);
  assert.equal(
    selected.fallbackReason.code,
    'compressed_memory_budget',
  );
  assert.equal(controller.snapshot().state, 'frozen');
  assert.equal(controller.getRuntimeLoader(), null);
  assert.equal(loaderCreations, 0);
});

test('capability and decode failures freeze bounded honest fallbacks', async () => {
  const { createMaterialProfileController } = subject();
  const cases = [
    {
      harness: loaderHarness({
        supportError: new Error('renderer fingerprint should stay private'),
      }),
      expected: 'ktx2_capability_unavailable',
    },
    {
      harness: loaderHarness({
        decodeError: new Error('decoder stack should stay private'),
      }),
      expected: 'canary_decode_failed',
    },
  ];

  for (const { harness, expected } of cases) {
    const controller = createMaterialProfileController({
      createKtx2Loader: () => harness.loader,
      verifyAndReadCanary: async () => CANARY_BYTES,
    });
    const selected = await controller.select({
      renderer: fakeRenderer(),
      canary: canary(),
      predictedCompressedBytes: 1024,
    });
    assert.equal(selected.profileId, H2);
    assert.equal(selected.fallbackReason.code, expected);
    assert.equal(controller.getRuntimeLoader(), null);
    assert.equal(harness.events.at(-1), 'loader-disposed');
    const publicJson = JSON.stringify(controller.snapshot());
    assert.equal(publicJson.includes('fingerprint'), false);
    assert.equal(publicJson.includes('decoder stack'), false);
    assert.deepEqual(
      Object.keys(controller.snapshot()).sort(),
      ['fallbackReason', 'profileId', 'state'],
    );
  }
});

test('verified canary rejects redirects, MIME, length, and SHA drift', async () => {
  const { verifyKtx2Canary, MaterialProfileError } = subject();
  const descriptor = canary();
  const exactUrl = new URL(descriptor.url, 'https://viewer.test/').href;
  function response({
    bytes = CANARY_BYTES,
    redirected = false,
    url = exactUrl,
    mime = 'image/ktx2',
    length = String(descriptor.bytes),
  } = {}) {
    return {
      ok: true,
      status: 200,
      redirected,
      url,
      headers: {
        get(name) {
          if (name.toLowerCase() === 'content-type') return mime;
          if (name.toLowerCase() === 'content-length') return length;
          return null;
        },
      },
      async arrayBuffer() {
        return bytes.buffer.slice(
          bytes.byteOffset,
          bytes.byteOffset + bytes.byteLength,
        );
      },
    };
  }
  const cases = [
    [response({ redirected: true }), 'canary_redirect_rejected'],
    [response({ mime: 'application/octet-stream' }), 'canary_mime_mismatch'],
    [response({ length: String(descriptor.bytes + 1) }), 'canary_length_mismatch'],
    [
      response({
        bytes: Uint8Array.from(
          CANARY_BYTES,
          (value, index) => value ^ (index === 0 ? 1 : 0),
        ),
      }),
      'canary_sha256_mismatch',
    ],
  ];

  for (const [result, code] of cases) {
    await assert.rejects(
      verifyKtx2Canary({
        descriptor,
        fetchFn: async () => result,
        subtle: webcrypto.subtle,
        baseUrl: 'https://viewer.test/',
      }),
      (error) => (
        error instanceof MaterialProfileError
        && error.code === code
      ),
    );
  }
});

test('verification failure is normalized and never leaks canary evidence', async () => {
  const {
    createMaterialProfileController,
    MaterialProfileError,
  } = subject();
  const descriptor = canary();
  const controller = createMaterialProfileController({
    createKtx2Loader: () => loaderHarness().loader,
    verifyAndReadCanary: async () => {
      throw new MaterialProfileError(
        'canary_sha256_mismatch',
        `${descriptor.url} ${descriptor.sha256}`,
      );
    },
  });

  const selected = await controller.select({
    renderer: fakeRenderer(),
    canary: descriptor,
    predictedCompressedBytes: 1024,
  });

  assert.equal(selected.profileId, H2);
  assert.equal(
    selected.fallbackReason.code,
    'canary_sha256_mismatch',
  );
  const publicJson = JSON.stringify(controller.snapshot());
  assert.equal(publicJson.includes(descriptor.url), false);
  assert.equal(publicJson.includes(descriptor.sha256), false);
});

test('selection is concurrent-safe, idempotent, and snapshot mutation-proof', async () => {
  const { createMaterialProfileController } = subject();
  const harness = loaderHarness();
  let verifications = 0;
  let releaseVerification;
  const verificationGate = new Promise((resolve) => {
    releaseVerification = resolve;
  });
  const controller = createMaterialProfileController({
    createKtx2Loader: () => harness.loader,
    async verifyAndReadCanary() {
      verifications += 1;
      await verificationGate;
      return CANARY_BYTES;
    },
  });
  const input = {
    renderer: fakeRenderer(),
    canary: canary(),
    predictedCompressedBytes: 1024,
  };

  const first = controller.select(input);
  const second = controller.select({
    ...input,
    predictedCompressedBytes: 512 * 1024 * 1024 + 1,
  });
  assert.equal(controller.snapshot().state, 'selecting');
  releaseVerification();
  assert.deepEqual(await first, await second);
  assert.equal(verifications, 1);

  const snapshot = controller.snapshot();
  assert.throws(() => {
    snapshot.profileId = H2;
  }, TypeError);
  const third = await controller.select({
    ...input,
    predictedCompressedBytes: 512 * 1024 * 1024 + 1,
  });
  assert.equal(third.profileId, H3);
  assert.equal(verifications, 1);
});

test('runtime H3 failure rolls back once after caller resources, then workers', async () => {
  const { createMaterialProfileController } = subject();
  const events = [];
  const harness = loaderHarness();
  harness.loader.dispose = () => events.push('ktx-workers-disposed');
  const controller = createMaterialProfileController({
    createKtx2Loader: () => harness.loader,
    verifyAndReadCanary: async () => CANARY_BYTES,
  });
  await controller.select({
    renderer: fakeRenderer(),
    canary: canary(),
    predictedCompressedBytes: 1024,
  });

  const first = await controller.rollbackToFallback({
    async disposePrimary() {
      events.push('h3-resources-disposed');
    },
  });
  const second = await controller.rollbackToFallback({
    async disposePrimary() {
      events.push('must-not-run-twice');
    },
  });

  assert.equal(first.profileId, H2);
  assert.equal(first.fallbackReason.code, 'runtime_h3_failure');
  assert.deepEqual(second, first);
  assert.deepEqual(events, [
    'h3-resources-disposed',
    'ktx-workers-disposed',
  ]);
  assert.equal(controller.snapshot().state, 'fallback-frozen');
  assert.equal(controller.getRuntimeLoader(), null);
});
