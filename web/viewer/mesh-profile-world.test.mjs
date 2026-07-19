import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createMaterialProfileController,
} from './material-profile.mjs';
import {
  createAtomicMeshProfileWorld,
} from './mesh-profile-world.mjs';

const H3 = 'h3-ai-ktx2-4k';
const H2 = 'h2-png-1k-fallback';

function runtime(id) {
  const canary = new TextEncoder().encode(`canary-${id}`);
  return {
    id,
    predicted_compressed_texture_bytes: 1024,
    profiles: {
      [H3]: {
        profile_id: H3,
        textures: [{
          url: `/h3/${id}.ktx2`,
          sha256: 'a'.repeat(64),
          bytes: canary.byteLength,
          media_type: 'image/ktx2',
          testBytes: canary,
        }],
      },
      [H2]: {
        profile_id: H2,
        textures: [{
          url: `/h2/${id}.png`,
          media_type: 'image/png',
        }],
      },
    },
  };
}

function harness({
  failH3Id = null,
  failH3Lane = false,
} = {}) {
  const events = [];
  const loader = {
    setTranscoderPath() {},
    detectSupport() {},
    parse(_bytes, onLoad) {
      onLoad({ dispose() {} });
    },
    dispose() {
      events.push('ktx-workers-disposed');
    },
  };
  const controller = createMaterialProfileController({
    createKtx2Loader: () => loader,
    verifyAndReadCanary: async (descriptor) => descriptor.testBytes,
  });
  const world = createAtomicMeshProfileWorld({
    profileController: controller,
    renderer: {},
    resolveSelectedProfile(source, profileId) {
      const profile = source.profiles[profileId];
      return {
        ...profile,
        predicted_compressed_texture_bytes: (
          profileId === H3
            ? source.predicted_compressed_texture_bytes
            : 0
        ),
      };
    },
    createLane(profileId) {
      if (profileId === H3 && failH3Lane) {
        throw new Error('bounded H3 lane creation failure');
      }
      return {
        async build(selected, source) {
          if (profileId === H3 && source.id === failH3Id) {
            throw new Error('bounded H3 decode failure');
          }
          return { id: source.id, profileId, selected };
        },
        async disposeTemplates() {},
        async disposeTextures() {},
      };
    },
    async replaceVisible() {},
    disposeRecord() {},
    onEvent(code) {
      events.push(code);
    },
  });
  return { controller, events, world };
}

test('one H3 failure clears the whole batch before one H2 reload', async () => {
  const setup = harness({ failH3Id: 2 });

  const records = await setup.world.loadVisible([
    runtime(1),
    runtime(2),
    runtime(3),
  ]);

  assert.deepEqual(setup.events, [
    'h3-load-start',
    'h3-failure',
    'h3-chunks-disposed',
    'h3-templates-disposed',
    'h3-textures-disposed',
    'ktx-workers-disposed',
    'h2-reload-start',
    'h2-reload-complete',
  ]);
  assert.deepEqual(
    records.map((record) => record.profileId),
    [H2, H2, H2],
  );
  assert.deepEqual(setup.world.snapshot(), {
    state: 'fallback-frozen',
    profileId: H2,
    fallbackCode: 'runtime_h3_failure',
    activeRecords: 3,
    mixedProfiles: false,
    loading: false,
  });
});

test('a complete H3 batch publishes without creating an H2 lane', async () => {
  const setup = harness();

  const records = await setup.world.loadVisible([
    runtime(-1),
    runtime(0),
    runtime(1),
  ]);

  assert.deepEqual(setup.events, [
    'h3-load-start',
    'h3-load-complete',
  ]);
  assert.deepEqual(
    records.map((record) => record.profileId),
    [H3, H3, H3],
  );
  assert.equal(setup.world.snapshot().mixedProfiles, false);
  assert.equal(setup.controller.snapshot().profileId, H3);
});

test('H3 lane creation failure follows the same complete H2 rollback', async () => {
  const setup = harness({ failH3Lane: true });

  const records = await setup.world.loadVisible([runtime(1)]);

  assert.equal(records[0].profileId, H2);
  assert.deepEqual(setup.events, [
    'h3-load-start',
    'h3-failure',
    'h3-chunks-disposed',
    'h3-templates-disposed',
    'h3-textures-disposed',
    'ktx-workers-disposed',
    'h2-reload-start',
    'h2-reload-complete',
  ]);
});
