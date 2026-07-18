import assert from 'node:assert/strict';
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

const BUNDLE_ID = '1'.repeat(64);
const MATERIAL_ID = '2'.repeat(64);
const GLB_SHA = '3'.repeat(64);
const MAP_SHA = '6'.repeat(64);
const TERRAIN_ALGORITHM_ID =
  'synthetic-multiscale-relief-slope-macro-patch-v2';
const TERRAIN_RESOLUTION = 41;
const TERRAIN_MATERIAL_SLOTS = [
  'material-moss-stone-01',
  'material-packed-earth-01',
  'material-terrace-soil-01',
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
