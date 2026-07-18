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
const WORLD = {
  mesh_grid: {
    on_demand: true,
    url_template: '/api/world/mesh-chunk/{x}/{y}.json',
    asset_url_template: '/api/world/mesh-assets/{bundle_id}/{asset_id}/lod{lod}.glb',
    world_seed: 42,
    layout_engine: 'mock',
    terrain_algorithm_id: 'mock-flat-ground-v1',
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
      terrain_algorithm_id: 'mock-flat-ground-v1',
      mesh_asset_bundle_id: BUNDLE_ID,
      material_bundle_id: MATERIAL_ID,
      selected_lod: 0,
      terrain: {
        algorithm_id: 'mock-flat-ground-v1',
        resolution: 3,
        material_slot_id: 'material-terrace-soil-01',
        vertices: [
          { x: 0, y: 0, z: 0, world_u: -200, world_v: 400 },
          { x: 100, y: 0, z: 0, world_u: -100, world_v: 400 },
          { x: 200, y: 0, z: 0, world_u: 0, world_v: 400 },
          { x: 0, y: 100, z: 0, world_u: -200, world_v: 500 },
          { x: 100, y: 100, z: 0, world_u: -100, world_v: 500 },
          { x: 200, y: 100, z: 0, world_u: 0, world_v: 500 },
          { x: 0, y: 200, z: 0, world_u: -200, world_v: 600 },
          { x: 100, y: 200, z: 0, world_u: -100, world_v: 600 },
          { x: 200, y: 200, z: 0, world_u: 0, world_v: 600 },
        ],
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
});

test('instance and terrain conversion preserve ENU while mapping to Three axes', () => {
  const {
    meshInstanceThreeTransform,
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
  assert.deepEqual(Array.from(geometry.positions), [
    -200, 0, -400,
    -100, 0, -400,
    0, 0, -400,
    -200, 0, -500,
    -100, 0, -500,
    0, 0, -500,
    -200, 0, -600,
    -100, 0, -600,
    0, 0, -600,
  ]);
  assert.deepEqual(
    Array.from(geometry.indices),
    [0, 3, 1, 1, 3, 4, 1, 4, 2, 2, 4, 5, 3, 6, 4, 4, 6, 7, 4, 7, 5, 5, 7, 8],
  );
  assert.deepEqual(Array.from(geometry.uvs), [
    -200, 400, -100, 400, 0, 400,
    -200, 500, -100, 500, 0, 500,
    -200, 600, -100, 600, 0, 600,
  ]);
});
