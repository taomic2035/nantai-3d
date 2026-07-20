import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';

let meshWorld;
try {
  meshWorld = await import('./mesh-world.mjs');
} catch (error) {
  meshWorld = { __loadError: error };
}

function subject() {
  assert.equal(
    meshWorld.__loadError,
    undefined,
    `mesh-world.mjs must load: ${meshWorld.__loadError?.message}`,
  );
  return meshWorld;
}

const BUNDLE_ID =
  '866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6';
const MATERIAL_ID = '2'.repeat(64);
const MESH_V3_ID = 'a'.repeat(64);
const MATERIAL_V2_ID = 'b'.repeat(64);
const H3_PROFILE_ID = 'h3-ai-ktx2-4k';
const H2_PROFILE_ID = 'h2-png-1k-fallback';
const GLB_SHA = '3'.repeat(64);
const MAP_SHA = '6'.repeat(64);
const NORMAL_SHA = '7'.repeat(64);
const ORM_SHA = '8'.repeat(64);
const TERRAIN_ALGORITHM_ID =
  'synthetic-multiscale-relief-slope-macro-patch-v2';
const TERRAIN_RESOLUTION = 41;
const TERRAIN_MATERIAL_SLOTS = [
  'material-moss-stone-01',
  'material-packed-earth-01',
  'material-terrace-soil-01',
];
const MATERIAL_SLOTS = [
  'material-aged-metal-01',
  'material-bamboo-leaf-01',
  'material-bamboo-stem-01',
  'material-broadleaf-bark-01',
  'material-broadleaf-canopy-01',
  'material-clay-brick-01',
  'material-creek-rock-01',
  'material-dark-timber-01',
  'material-dry-stone-wall-01',
  'material-fieldstone-01',
  'material-gray-roof-tile-01',
  'material-moss-stone-01',
  'material-orchard-bark-01',
  'material-orchard-leaf-01',
  'material-packed-earth-01',
  'material-pale-plaster-01',
  'material-rammed-earth-01',
  'material-rice-paddy-water-01',
  'material-shallow-water-01',
  'material-terrace-soil-01',
  'material-vegetable-leaf-01',
  'material-weathered-timber-01',
  'material-wet-stone-paving-01',
  'material-woven-bamboo-01',
];
const WORLD = {
  mesh_grid: {
    on_demand: true,
    url_template: '/api/world/mesh-chunk/{x}/{y}.json',
    asset_url_template: '/api/world/mesh-assets/{bundle_id}/{asset_id}/lod{lod}.glb',
    world_seed: 42,
    layout_engine: 'mock',
    terrain_algorithm_id: TERRAIN_ALGORITHM_ID,
    mesh_asset_bundle_id: BUNDLE_ID,
    material_bundle_id: MATERIAL_ID,
  },
};

function runtime() {
  return {
    schema_version: 'nantai.synthetic-village.mesh-chunk-runtime.v1',
    chunk: {
      schema_version: 'nantai.synthetic-village.mesh-chunk.v1',
      renderer_capability: 'synthetic-textured-mesh-grid',
      content_key: '4'.repeat(64),
      world_seed: 42,
      chunk_id: { x: -1, y: 2 },
      chunk_size_m: 200,
      world_offset: [-200, 400, 0],
      layout_algorithm_id: 'mock-layout-v1',
      layout_sha256: '5'.repeat(64),
      terrain_algorithm_id: TERRAIN_ALGORITHM_ID,
      mesh_asset_bundle_id: BUNDLE_ID,
      material_bundle_id: MATERIAL_ID,
      selected_lod: 0,
      terrain: {
        algorithm_id: TERRAIN_ALGORITHM_ID,
        resolution: TERRAIN_RESOLUTION,
        material_slot_id: 'material-terrace-soil-01',
        material_slot_ids: Array.from(
          { length: (TERRAIN_RESOLUTION - 1) ** 2 },
          (_, index) => TERRAIN_MATERIAL_SLOTS[index % 3],
        ),
        vertices: Array.from(
          { length: TERRAIN_RESOLUTION ** 2 },
          (_, index) => {
            const row = Math.floor(index / TERRAIN_RESOLUTION);
            const column = index % TERRAIN_RESOLUTION;
            const x = column * 5;
            const y = row * 5;
            return {
              x,
              y,
              z: (row - column) * 0.25,
              world_u: -200 + x,
              world_v: 400 + y,
              macro_tint: 0.95 + ((row + column) % 3) * 0.025,
            };
          },
        ),
      },
      roads: [],
      water: [],
      instances: [{
        instance_id: 'building:house-01',
        asset_id: 'house_wood_01',
        kind: 'building',
        local_position: [20, 30, 1],
        rotation_z_degrees: 90,
        scale: 1.25,
        template_lod: 0,
      }],
      aabb: { min: [-200, 400, 0], max: [0, 600, 8] },
      synthetic: true,
      geometry_usability: 'preview-only',
      coordinate_confidence: 'synthetic-layout',
      metric_alignment: false,
      real_photo_textures: false,
    },
    asset_urls: [{
      asset_id: 'house_wood_01',
      lod: 0,
      url: `/api/world/mesh-assets/${BUNDLE_ID}/house_wood_01/lod0.glb`,
      glb_sha256: GLB_SHA,
      glb_bytes: 1024,
    }],
    surface_materials: TERRAIN_MATERIAL_SLOTS.map((slotId) => ({
      slot_id: slotId,
      uv_policy: 'world-xy',
      nominal_tile_m: 4,
      normal_strength: 0.75,
      roughness_center: 0.97,
      metallic: 0,
      base_color: {
        role: 'base_color',
        url: `/api/world/material-maps/${MATERIAL_ID}/${slotId}/base_color.png`,
        sha256: MAP_SHA,
        bytes: 4096,
        color_space: 'srgb',
      },
      normal: {
        role: 'normal',
        url: `/api/world/material-maps/${MATERIAL_ID}/${slotId}/normal.png`,
        sha256: MAP_SHA,
        bytes: 4096,
        color_space: 'non-color',
      },
      orm: {
        role: 'orm',
        url: `/api/world/material-maps/${MATERIAL_ID}/${slotId}/orm.png`,
        sha256: MAP_SHA,
        bytes: 4096,
        color_space: 'non-color',
      },
    })),
  };
}

function textureDependency({
  sha256 = MAP_SHA,
  role = 'base_color',
  colourSpace = role === 'base_color' ? 'srgb' : 'non-color',
  materialSlotId = 'material-fieldstone-01',
  ...overrides
} = {}) {
  return {
    url: `/api/world/mesh-assets/${BUNDLE_ID}/textures/${sha256}.png`,
    sha256,
    bytes: 4096,
    role,
    colour_space: colourSpace,
    material_slot_id: materialSlotId,
    derivation_algorithm_id: 'deterministic-near-map-v2',
    min_filter: 9987,
    mag_filter: 9729,
    wrap_s: 10497,
    wrap_t: 10497,
    ...overrides,
  };
}

function runtimeV2(lod = 2) {
  const payload = runtime();
  payload.schema_version = 'nantai.synthetic-village.mesh-chunk-runtime.v2';
  payload.chunk.selected_lod = lod;
  payload.chunk.instances[0].template_lod = lod;
  const asset = payload.asset_urls[0];
  asset.lod = lod;
  asset.url =
    `/api/world/mesh-assets/${BUNDLE_ID}/house_wood_01/lod${lod}.glb`;
  asset.texture_dependencies = lod === 2
    ? [
      textureDependency(),
      textureDependency({ sha256: NORMAL_SHA, role: 'normal' }),
      textureDependency({ sha256: ORM_SHA, role: 'orm' }),
    ]
    : [];
  return payload;
}

function digest(label) {
  return createHash('sha256').update(label).digest('hex');
}

function profileTexture(
  profileId,
  slotId,
  role,
  {
    namespace = 'surface',
    mediaType = profileId === H3_PROFILE_ID
      ? 'image/ktx2'
      : 'image/png',
  } = {},
) {
  const sha = digest(`${namespace}:${profileId}:${slotId}:${role}`);
  const extension = mediaType === 'image/ktx2' ? 'ktx2' : 'png';
  return {
    url: (
      `/api/world/mesh-textures/${MESH_V3_ID}/${profileId}/`
      + `${sha}.${extension}`
    ),
    sha256: sha,
    bytes: 2048,
    width: profileId === H3_PROFILE_ID ? 4096 : 1024,
    height: profileId === H3_PROFILE_ID ? 4096 : 1024,
    media_type: mediaType,
    role,
    transfer: role === 'base_color' ? 'srgb' : 'linear',
    material_slot_id: slotId,
    min_filter: 9987,
    mag_filter: 9729,
    wrap_s: 10497,
    wrap_t: 10497,
    alpha_mode: 'opaque',
    flip_y: false,
  };
}

function runtimeV3Profile(profileId) {
  const dependencies = ['base_color', 'normal', 'orm'].map((role) => (
    profileTexture(
      profileId,
      'material-fieldstone-01',
      role,
      { namespace: 'mesh' },
    )
  ));
  return {
    profile_id: profileId,
    asset_urls: [{
      profile_id: profileId,
      asset_id: 'house_wood_01',
      lod: 2,
      url: (
        `/api/world/mesh-assets/${MESH_V3_ID}/${profileId}/`
        + 'house_wood_01/lod2.glb'
      ),
      glb_sha256: digest(`glb:${profileId}`),
      glb_bytes: 8192,
      geometry_fingerprint: digest('shared-geometry'),
      texture_dependencies: dependencies,
    }],
    textures: MATERIAL_SLOTS.flatMap((slotId) => (
      ['base_color', 'normal', 'orm'].map((role) => (
        profileTexture(profileId, slotId, role)
      ))
    )),
  };
}

const WORLD_V3 = {
  mesh_grid: {
    runtime_schema: 'nantai.synthetic-village.mesh-chunk-runtime.v3',
    on_demand: true,
    url_template: '/api/world/mesh-chunk/{x}/{y}.json',
    asset_url_template: (
      '/api/world/mesh-assets/{bundle_id}/{profile_id}/'
      + '{asset_id}/lod{lod}.glb'
    ),
    texture_url_template: (
      '/api/world/mesh-textures/{bundle_id}/{profile_id}/'
      + '{sha256}.{extension}'
    ),
    world_seed: 42,
    layout_engine: 'mock',
    terrain_algorithm_id: TERRAIN_ALGORITHM_ID,
    source_mesh_asset_bundle_id: BUNDLE_ID,
    mesh_asset_bundle_id: MESH_V3_ID,
    fallback_material_bundle_id: MATERIAL_ID,
    material_bundle_id: MATERIAL_V2_ID,
  },
};

function runtimeV3() {
  const source = runtimeV2(2);
  const h2 = runtimeV3Profile(H2_PROFILE_ID);
  const h3 = runtimeV3Profile(H3_PROFILE_ID);
  const predicted = [
    ...h3.textures,
    ...h3.asset_urls.flatMap((asset) => asset.texture_dependencies),
  ].reduce(
    (sum, descriptor) => (
      descriptor.media_type === 'image/ktx2'
        ? sum + descriptor.bytes
        : sum
    ),
    0,
  );
  return {
    schema_version: 'nantai.synthetic-village.mesh-chunk-runtime.v3',
    chunk: source.chunk,
    source_mesh_asset_bundle_id: BUNDLE_ID,
    mesh_asset_bundle_id: MESH_V3_ID,
    material_bundle_id: MATERIAL_V2_ID,
    fallback_material_bundle_id: MATERIAL_ID,
    primary_profile_id: H3_PROFILE_ID,
    fallback_profile_id: H2_PROFILE_ID,
    predicted_compressed_texture_bytes: predicted,
    profiles: {
      [H2_PROFILE_ID]: h2,
      [H3_PROFILE_ID]: h3,
    },
    surface_materials: source.surface_materials.map((material) => ({
      slot_id: material.slot_id,
      uv_policy: material.uv_policy,
      nominal_tile_m: material.nominal_tile_m,
      normal_strength: material.normal_strength,
      roughness_center: material.roughness_center,
      metallic: material.metallic,
    })),
    synthetic: true,
    ai_generated: true,
    real_photo_textures: false,
    geometry_usability: 'preview-only',
    metric_alignment: false,
    verification_level: 'L0',
  };
}

test('mesh chunk URL is available only for the exact fail-closed grid contract', () => {
  const { meshWorldAvailable, resolveMeshChunkUrl } = subject();

  assert.equal(meshWorldAvailable(WORLD), true);
  assert.equal(
    resolveMeshChunkUrl(WORLD, -1, 2, 0),
    '/api/world/mesh-chunk/-1/2.json?lod=0',
  );
  assert.equal(
    meshWorldAvailable({
      mesh_grid: { ...WORLD.mesh_grid, asset_url_template: 'https://evil.test/{asset_id}' },
    }),
    false,
  );
  assert.equal(resolveMeshChunkUrl({}, 0, 0, 2), null);
  assert.throws(() => resolveMeshChunkUrl(WORLD, 0.5, 0, 2), /safe integer/);
  assert.throws(() => resolveMeshChunkUrl(WORLD, 0, 0, 3), /LOD/);
});

test('plain viewer prefers the arbitrary-coordinate textured mesh world', () => {
  const { selectInitialPresentationMode } = subject();

  assert.equal(
    selectInitialPresentationMode({
      manifest: WORLD,
      modelAvailable: true,
      search: '',
    }),
    'mesh',
  );
  assert.equal(
    selectInitialPresentationMode({
      manifest: WORLD,
      modelAvailable: false,
      search: '',
    }),
    'mesh',
  );
});

test('runtime v3 validates both profiles but resolves only the frozen closure', () => {
  const {
    meshWorldAvailable,
    resolveMeshChunkUrl,
    resolveSelectedProfile,
    validateMeshChunkRuntime,
  } = subject();
  const payload = runtimeV3();

  assert.equal(meshWorldAvailable(WORLD_V3), true);
  assert.equal(
    resolveMeshChunkUrl(WORLD_V3, -1, 2, 2),
    '/api/world/mesh-chunk/-1/2.json?lod=2',
  );
  assert.throws(
    () => resolveSelectedProfile(payload, H3_PROFILE_ID),
    /profile/,
  );
  assert.equal(validateMeshChunkRuntime(payload, {
    worldManifest: WORLD_V3,
    chunkX: -1,
    chunkY: 2,
    lod: 2,
  }), payload);

  const h3 = resolveSelectedProfile(payload, H3_PROFILE_ID);
  const h2 = resolveSelectedProfile(payload, H2_PROFILE_ID);
  const h3Json = JSON.stringify(h3);
  const h2Json = JSON.stringify(h2);
  assert.equal(h3.profile_id, H3_PROFILE_ID);
  assert.equal(h2.profile_id, H2_PROFILE_ID);
  assert.equal(h3Json.includes(`/${H2_PROFILE_ID}/`), false);
  assert.equal(h2Json.includes(`/${H3_PROFILE_ID}/`), false);
  assert.ok(h3.textures.some((row) => row.media_type === 'image/ktx2'));
  assert.ok(h2.textures.every((row) => row.media_type === 'image/png'));
  assert.ok(
    h2.asset_urls
      .flatMap((asset) => asset.texture_dependencies)
      .every((row) => row.media_type === 'image/png'),
  );
  assert.equal(
    h3.predicted_compressed_texture_bytes,
    payload.predicted_compressed_texture_bytes,
  );
  assert.equal(h2.predicted_compressed_texture_bytes, 0);
  assert.throws(() => {
    h3.profile_id = H2_PROFILE_ID;
  }, TypeError);
  assert.throws(() => {
    payload.profiles[H3_PROFILE_ID].textures[0].bytes += 1;
  }, TypeError);
  assert.throws(
    () => resolveSelectedProfile(payload, 'unknown-profile'),
    /profile/,
  );
});

test('runtime v3 rejects mixed profiles, topology drift, truth drift, and budget drift', () => {
  const { validateMeshChunkRuntime } = subject();
  const cases = {
    'missing counterpart': (payload) => {
      delete payload.profiles[H2_PROFILE_ID];
    },
    'cross-profile route': (payload) => {
      payload.profiles[H3_PROFILE_ID].asset_urls[0].url =
        payload.profiles[H2_PROFILE_ID].asset_urls[0].url;
    },
    'geometry fingerprint drift': (payload) => {
      payload.profiles[H3_PROFILE_ID]
        .asset_urls[0].geometry_fingerprint = digest('drifted-geometry');
    },
    'H2 KTX2 drift': (payload) => {
      const descriptor = payload.profiles[H2_PROFILE_ID].textures[0];
      descriptor.media_type = 'image/ktx2';
      descriptor.url = descriptor.url.replace(/\.png$/, '.ktx2');
    },
    'compressed budget drift': (payload) => {
      payload.predicted_compressed_texture_bytes += 1;
    },
    'real-photo truth drift': (payload) => {
      payload.real_photo_textures = true;
    },
    'unknown runtime field': (payload) => {
      payload.local_path = '/private/runtime.json';
    },
  };

  for (const [label, mutate] of Object.entries(cases)) {
    const payload = runtimeV3();
    mutate(payload);
    assert.throws(
      () => validateMeshChunkRuntime(payload, {
        worldManifest: WORLD_V3,
        chunkX: -1,
        chunkY: 2,
        lod: 2,
      }),
      /v3|profile|geometry|texture|budget|provenance|contract/,
      label,
    );
  }
});

test('explicit presentation and model-preview review links keep priority', () => {
  const { selectInitialPresentationMode } = subject();

  assert.equal(
    selectInitialPresentationMode({
      manifest: WORLD,
      modelAvailable: true,
      search: '?presentation=points',
    }),
    'points',
  );
  assert.equal(
    selectInitialPresentationMode({
      manifest: WORLD,
      modelAvailable: true,
      search: '?presentation=model',
    }),
    'model',
  );
  assert.equal(
    selectInitialPresentationMode({
      manifest: WORLD,
      modelAvailable: true,
      search: '?modelPreview=%2Fapi%2Flocal-textured-preview%2Fabc%2Fmanifest.json',
    }),
    'model',
  );
  assert.equal(
    selectInitialPresentationMode({
      manifest: {},
      modelAvailable: true,
      search: '',
    }),
    'model',
  );
  assert.equal(
    selectInitialPresentationMode({
      manifest: {},
      modelAvailable: false,
      search: '?presentation=mesh',
    }),
    'points',
  );
});

test('runtime validation binds chunk, bundle, LOD, assets, and exact same-origin routes', () => {
  const { validateMeshChunkRuntime } = subject();
  const payload = runtime();

  assert.equal(validateMeshChunkRuntime(payload, {
    worldManifest: WORLD,
    chunkX: -1,
    chunkY: 2,
    lod: 0,
  }), payload);

  const wrongBundle = structuredClone(payload);
  wrongBundle.chunk.mesh_asset_bundle_id = '9'.repeat(64);
  assert.throws(
    () => validateMeshChunkRuntime(wrongBundle, {
      worldManifest: WORLD,
      chunkX: -1,
      chunkY: 2,
      lod: 0,
    }),
    /bundle/,
  );

  const escaped = structuredClone(payload);
  escaped.asset_urls[0].url = 'https://evil.test/model.glb';
  assert.throws(
    () => validateMeshChunkRuntime(escaped, {
      worldManifest: WORLD,
      chunkX: -1,
      chunkY: 2,
      lod: 0,
    }),
    /asset route/,
  );

  const escapedMap = structuredClone(payload);
  escapedMap.surface_materials[0].normal.url = 'https://evil.test/normal.png';
  assert.throws(
    () => validateMeshChunkRuntime(escapedMap, {
      worldManifest: WORLD,
      chunkX: -1,
      chunkY: 2,
      lod: 0,
    }),
    /surface material/,
  );

  const wrongClosure = structuredClone(payload);
  wrongClosure.surface_materials[0].slot_id = 'material-packed-earth-01';
  assert.throws(
    () => validateMeshChunkRuntime(wrongClosure, {
      worldManifest: WORLD,
      chunkX: -1,
      chunkY: 2,
      lod: 0,
    }),
    /surface material/,
  );
});

test('runtime v2 accepts only its exact LOD-paired texture dependency closure', () => {
  const { validateMeshChunkRuntime } = subject();
  const near = runtimeV2(2);
  const far = runtimeV2(0);

  assert.equal(validateMeshChunkRuntime(near, {
    worldManifest: WORLD,
    chunkX: -1,
    chunkY: 2,
    lod: 2,
  }), near);
  assert.equal(validateMeshChunkRuntime(far, {
    worldManifest: WORLD,
    chunkX: -1,
    chunkY: 2,
    lod: 0,
  }), far);

  const v1WithDependencies = runtime();
  v1WithDependencies.asset_urls[0].texture_dependencies = [];
  assert.throws(
    () => validateMeshChunkRuntime(v1WithDependencies, {
      worldManifest: WORLD,
      chunkX: -1,
      chunkY: 2,
      lod: 0,
    }),
    /asset route/,
  );

  const missingNearDependencies = runtimeV2(2);
  missingNearDependencies.asset_urls[0].texture_dependencies = [];
  assert.throws(
    () => validateMeshChunkRuntime(missingNearDependencies, {
      worldManifest: WORLD,
      chunkX: -1,
      chunkY: 2,
      lod: 2,
    }),
    /texture dependenc/,
  );

  const farWithDependencies = runtimeV2(0);
  farWithDependencies.asset_urls[0].texture_dependencies = [
    textureDependency(),
  ];
  assert.throws(
    () => validateMeshChunkRuntime(farWithDependencies, {
      worldManifest: WORLD,
      chunkX: -1,
      chunkY: 2,
      lod: 0,
    }),
    /texture dependenc/,
  );
});

test('runtime v2 rejects repaired, escaped, ambiguous, or unsafe dependencies', () => {
  const { validateMeshChunkRuntime } = subject();
  const cases = {
    'cross-origin URL': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].url =
        'https://evil.test/base.png';
    },
    'absolute URL': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].url =
        `/textures/${MAP_SHA}.png`;
    },
    'wrong bundle': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].url =
        `/api/world/mesh-assets/${'9'.repeat(64)}/textures/${MAP_SHA}.png`;
    },
    'wrong object': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].url =
        `/api/world/mesh-assets/${BUNDLE_ID}/textures/${NORMAL_SHA}.png`;
    },
    query: (payload) => {
      payload.asset_urls[0].texture_dependencies[0].url += '?raw=1';
    },
    fragment: (payload) => {
      payload.asset_urls[0].texture_dependencies[0].url += '#map';
    },
    'wrong colour space': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].colour_space =
        'non-color';
    },
    'wrong role': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].role = 'emissive';
    },
    'unsafe bytes': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].bytes =
        Number.MAX_SAFE_INTEGER + 1;
    },
    'empty derivation': (payload) => {
      payload.asset_urls[0].texture_dependencies[0]
        .derivation_algorithm_id = '';
    },
    'wrong sampler': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].wrap_s = 33071;
    },
    unsorted: (payload) => {
      payload.asset_urls[0].texture_dependencies.reverse();
    },
    'duplicate semantic': (payload) => {
      payload.asset_urls[0].texture_dependencies[1] = textureDependency({
        sha256: NORMAL_SHA,
      });
    },
    'extra key': (payload) => {
      payload.asset_urls[0].texture_dependencies[0].local_path =
        '/private/map.png';
    },
  };

  for (const [label, mutate] of Object.entries(cases)) {
    const payload = runtimeV2(2);
    mutate(payload);
    assert.throws(
      () => validateMeshChunkRuntime(payload, {
        worldManifest: WORLD,
        chunkX: -1,
        chunkY: 2,
        lod: 2,
      }),
      /texture dependenc/,
      label,
    );
  }
});

test('runtime objects reject unknown fields instead of normalizing them', () => {
  const { validateMeshChunkRuntime } = subject();
  for (const mutate of [
    (payload) => { payload.debug = true; },
    (payload) => { payload.chunk.local_path = '/private/chunk.json'; },
    (payload) => { payload.chunk.chunk_id.extra = 1; },
    (payload) => { payload.chunk.terrain.vertices[0].extra = 1; },
    (payload) => { payload.chunk.instances[0].extra = 1; },
  ]) {
    const payload = runtimeV2(2);
    mutate(payload);
    assert.throws(
      () => validateMeshChunkRuntime(payload, {
        worldManifest: WORLD,
        chunkX: -1,
        chunkY: 2,
        lod: 2,
      }),
      /invalid|contract/,
    );
  }
});

test('instance and terrain conversion preserve ENU while mapping to Three axes', () => {
  const {
    meshInstanceThreeTransform,
    ribbonGeometryThree,
    terrainGeometryThree,
  } = subject();
  const payload = runtime();

  assert.deepEqual(
    meshInstanceThreeTransform(
      payload.chunk.instances[0],
      payload.chunk.world_offset,
    ),
    {
      position: [-180, 1, -430],
      rotationYRadians: Math.PI / 2,
      scale: 1.25,
    },
  );

  const geometry = terrainGeometryThree(payload.chunk);
  assert.deepEqual(Array.from(geometry.positions.slice(0, 6)), [
    -200, 0, -400,
    -195, -0.25, -400,
  ]);
  assert.deepEqual(
    Array.from(geometry.positions.slice(-3)),
    [0, 0, -600],
  );
  assert.equal(geometry.indices.length, 40 * 40 * 6);
  assert.deepEqual(geometry.materialSlotIds, TERRAIN_MATERIAL_SLOTS);
  assert.deepEqual(
    geometry.groups.map(({ materialSlotId, materialIndex }) => ({
      materialSlotId,
      materialIndex,
    })),
    TERRAIN_MATERIAL_SLOTS.map((materialSlotId, materialIndex) => ({
      materialSlotId,
      materialIndex,
    })),
  );
  assert.equal(
    geometry.groups.reduce((sum, group) => sum + group.count, 0),
    geometry.indices.length,
  );
  assert.ok(geometry.groups.every((group) => group.count > 0));
  assert.deepEqual(
    Array.from(geometry.uvs.slice(0, 4)),
    [-200, 400, -195, 400],
  );
  assert.equal(geometry.colors.length, TERRAIN_RESOLUTION ** 2 * 3);
  assert.ok(Math.abs(geometry.colors[0] - 0.95) < 1e-6);
  assert.ok(Math.abs(geometry.colors[3] - 0.975) < 1e-6);

  const ribbon = ribbonGeometryThree(payload.chunk, {
    ribbon_id: 'test-road',
    kind: 'road',
    feature_type: 'main',
    width: 2,
    z_offset: 0,
    material_slot_id: 'material-wet-stone-paving-01',
    points: [[0, 0, 0], [3, 4, 0]],
  });
  assert.deepEqual(Array.from(ribbon.uvs), [0, 0, 0, 2, 5, 0, 5, 2]);
});
