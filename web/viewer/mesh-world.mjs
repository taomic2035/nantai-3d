const CHUNK_TEMPLATE = '/api/world/mesh-chunk/{x}/{y}.json';
const ASSET_TEMPLATE =
  '/api/world/mesh-assets/{bundle_id}/{asset_id}/lod{lod}.glb';
const SHA256 = /^[0-9a-f]{64}$/;
const ASSET_ID = /^[a-z0-9]+(?:_[a-z0-9]+)*$/;
const MATERIAL_SLOT_ID = /^material-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const TERRAIN_ALGORITHM_ID =
  'synthetic-multiscale-relief-slope-macro-patch-v2';
const TERRAIN_RESOLUTION = 41;
const TERRAIN_MATERIAL_SLOTS = new Set([
  'material-moss-stone-01',
  'material-packed-earth-01',
  'material-terrace-soil-01',
]);
const UV_POLICIES = new Set([
  'world-xy',
  'dominant-axis-box',
  'roof-slope',
  'object-long-axis',
  'leaf-card',
]);
const SURFACE_MATERIAL_KEYS = new Set([
  'slot_id',
  'uv_policy',
  'nominal_tile_m',
  'normal_strength',
  'roughness_center',
  'metallic',
  'base_color',
  'normal',
  'orm',
]);
const MATERIAL_MAP_KEYS = new Set([
  'role',
  'url',
  'sha256',
  'bytes',
  'color_space',
]);
const GRID_KEYS = new Set([
  'on_demand',
  'url_template',
  'asset_url_template',
  'world_seed',
  'layout_engine',
  'terrain_algorithm_id',
  'mesh_asset_bundle_id',
  'material_bundle_id',
]);

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value, expected) {
  if (!isObject(value)) return false;
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function finiteTuple(value, length) {
  return (
    Array.isArray(value)
    && value.length === length
    && value.every(Number.isFinite)
  );
}

function assertSchedulerInputs(chunkX, chunkY, lod) {
  if (!Number.isSafeInteger(chunkX) || !Number.isSafeInteger(chunkY)) {
    throw new TypeError('mesh chunk coordinates must be safe integers');
  }
  if (![0, 1, 2].includes(lod)) {
    throw new RangeError(`unsupported mesh chunk LOD: ${lod}`);
  }
}

function validGrid(manifest) {
  const grid = manifest?.mesh_grid;
  return (
    exactKeys(grid, GRID_KEYS)
    && grid.on_demand === true
    && grid.url_template === CHUNK_TEMPLATE
    && grid.asset_url_template === ASSET_TEMPLATE
    && Number.isSafeInteger(grid.world_seed)
    && grid.layout_engine === 'mock'
    && grid.terrain_algorithm_id === TERRAIN_ALGORITHM_ID
    && SHA256.test(grid.mesh_asset_bundle_id)
    && SHA256.test(grid.material_bundle_id)
  );
}

function expectedAssetPath(grid, assetId, lod) {
  return `/api/world/mesh-assets/${grid.mesh_asset_bundle_id}/${assetId}/lod${lod}.glb`;
}

function expectedMaterialMapPath(grid, slotId, role) {
  return `/api/world/material-maps/${grid.material_bundle_id}/${slotId}/${role}.png`;
}

function validateTerrain(chunk) {
  const terrain = chunk.terrain;
  const resolution = TERRAIN_RESOLUTION;
  if (
    !isObject(terrain)
    || terrain.algorithm_id !== TERRAIN_ALGORITHM_ID
    || terrain.material_slot_id !== 'material-terrace-soil-01'
    || terrain.resolution !== resolution
    || !Array.isArray(terrain.material_slot_ids)
    || terrain.material_slot_ids.length !== (resolution - 1) ** 2
    || !terrain.material_slot_ids.every(
      (slotId) => TERRAIN_MATERIAL_SLOTS.has(slotId),
    )
    || !Array.isArray(terrain.vertices)
    || terrain.vertices.length !== resolution ** 2
  ) {
    throw new TypeError('mesh terrain contract is invalid');
  }
  for (const vertex of terrain.vertices) {
    if (
      !isObject(vertex)
      || !['x', 'y', 'z', 'world_u', 'world_v', 'macro_tint'].every(
        (key) => Number.isFinite(vertex[key]),
      )
      || vertex.macro_tint < 0.9
      || vertex.macro_tint > 1.1
      || Math.abs(vertex.world_u - (chunk.world_offset[0] + vertex.x)) > 1e-6
      || Math.abs(vertex.world_v - (chunk.world_offset[1] + vertex.y)) > 1e-6
    ) {
      throw new TypeError('mesh terrain vertex is invalid');
    }
  }
}

function validateRibbons(ribbons, kind) {
  if (!Array.isArray(ribbons)) {
    throw new TypeError(`mesh ${kind} ribbons are invalid`);
  }
  for (const ribbon of ribbons) {
    if (
      !isObject(ribbon)
      || ribbon.kind !== kind
      || typeof ribbon.ribbon_id !== 'string'
      || typeof ribbon.feature_type !== 'string'
      || typeof ribbon.material_slot_id !== 'string'
      || !Number.isFinite(ribbon.width)
      || ribbon.width <= 0
      || !Number.isFinite(ribbon.z_offset)
      || ribbon.z_offset < 0
      || !Array.isArray(ribbon.points)
      || ribbon.points.length < 2
      || !ribbon.points.every((point) => finiteTuple(point, 3))
    ) {
      throw new TypeError(`mesh ${kind} ribbon contract is invalid`);
    }
  }
}

function validateInstances(chunk) {
  if (!Array.isArray(chunk.instances)) {
    throw new TypeError('mesh instances are invalid');
  }
  const instanceIds = new Set();
  for (const instance of chunk.instances) {
    if (
      !isObject(instance)
      || typeof instance.instance_id !== 'string'
      || instanceIds.has(instance.instance_id)
      || !ASSET_ID.test(instance.asset_id)
      || !['building', 'vegetation', 'prop'].includes(instance.kind)
      || !finiteTuple(instance.local_position, 3)
      || !Number.isFinite(instance.rotation_z_degrees)
      || instance.rotation_z_degrees < 0
      || instance.rotation_z_degrees >= 360
      || !Number.isFinite(instance.scale)
      || instance.scale <= 0
      || instance.scale > 3
      || instance.template_lod !== chunk.selected_lod
    ) {
      throw new TypeError('mesh instance contract is invalid');
    }
    instanceIds.add(instance.instance_id);
  }
  const sorted = [...instanceIds].sort();
  if (sorted.some((value, index) => value !== chunk.instances[index].instance_id)) {
    throw new TypeError('mesh instances are not stably sorted');
  }
}

function validateSurfaceMaterials(payload, chunk, grid) {
  if (!Array.isArray(payload.surface_materials)) {
    throw new TypeError('mesh surface material registry is invalid');
  }
  const expectedSlots = [...new Set([
    chunk.terrain.material_slot_id,
    ...chunk.terrain.material_slot_ids,
    ...chunk.roads.map((ribbon) => ribbon.material_slot_id),
    ...chunk.water.map((ribbon) => ribbon.material_slot_id),
  ])].sort();
  if (
    payload.surface_materials.length !== expectedSlots.length
    || payload.surface_materials.some(
      (material, index) => material?.slot_id !== expectedSlots[index],
    )
  ) {
    throw new TypeError('mesh surface material closure is invalid');
  }
  for (const material of payload.surface_materials) {
    if (
      !exactKeys(material, SURFACE_MATERIAL_KEYS)
      || !MATERIAL_SLOT_ID.test(material.slot_id)
      || !UV_POLICIES.has(material.uv_policy)
      || !Number.isFinite(material.nominal_tile_m)
      || material.nominal_tile_m <= 0
      || !Number.isFinite(material.normal_strength)
      || material.normal_strength <= 0
      || !Number.isFinite(material.roughness_center)
      || material.roughness_center < 0
      || material.roughness_center > 1
      || !Number.isFinite(material.metallic)
      || material.metallic < 0
      || material.metallic > 1
    ) {
      throw new TypeError('mesh surface material contract is invalid');
    }
    for (const role of ['base_color', 'normal', 'orm']) {
      const descriptor = material[role];
      const expectedColorSpace = role === 'base_color' ? 'srgb' : 'non-color';
      if (
        !exactKeys(descriptor, MATERIAL_MAP_KEYS)
        || descriptor.role !== role
        || descriptor.url !== expectedMaterialMapPath(
          grid,
          material.slot_id,
          role,
        )
        || !SHA256.test(descriptor.sha256)
        || !Number.isSafeInteger(descriptor.bytes)
        || descriptor.bytes <= 0
        || descriptor.color_space !== expectedColorSpace
      ) {
        throw new TypeError('mesh surface material map contract is invalid');
      }
    }
  }
}

export function meshWorldAvailable(manifest) {
  return validGrid(manifest);
}

export function selectInitialPresentationMode({
  manifest,
  modelAvailable,
  search = '',
}) {
  const parameters = new URLSearchParams(search);
  const requested = parameters.get('presentation');
  const meshAvailable = meshWorldAvailable(manifest);

  if (requested === 'points') return 'points';
  if (requested === 'mesh') {
    return meshAvailable ? 'mesh' : modelAvailable ? 'model' : 'points';
  }
  if (requested === 'model') {
    return modelAvailable ? 'model' : meshAvailable ? 'mesh' : 'points';
  }
  if (parameters.has('modelPreview') && modelAvailable) return 'model';
  if (meshAvailable) return 'mesh';
  return modelAvailable ? 'model' : 'points';
}

export function resolveMeshChunkUrl(manifest, chunkX, chunkY, lod) {
  assertSchedulerInputs(chunkX, chunkY, lod);
  if (!validGrid(manifest)) return null;
  const path = CHUNK_TEMPLATE
    .replace('{x}', String(chunkX))
    .replace('{y}', String(chunkY));
  return `${path}?lod=${lod}`;
}

export function validateMeshChunkRuntime(
  payload,
  {
    worldManifest,
    chunkX,
    chunkY,
    lod,
  },
) {
  assertSchedulerInputs(chunkX, chunkY, lod);
  if (!validGrid(worldManifest)) {
    throw new TypeError('mesh world grid is unavailable');
  }
  const grid = worldManifest.mesh_grid;
  const chunk = payload?.chunk;
  if (
    !isObject(payload)
    || payload.schema_version !== 'nantai.synthetic-village.mesh-chunk-runtime.v1'
    || !isObject(chunk)
    || chunk.schema_version !== 'nantai.synthetic-village.mesh-chunk.v1'
    || chunk.renderer_capability !== 'synthetic-textured-mesh-grid'
    || !SHA256.test(chunk.content_key)
    || !SHA256.test(chunk.layout_sha256)
    || chunk.world_seed !== grid.world_seed
    || chunk.chunk_id?.x !== chunkX
    || chunk.chunk_id?.y !== chunkY
    || !Number.isSafeInteger(chunk.chunk_size_m)
    || chunk.chunk_size_m <= 0
    || !finiteTuple(chunk.world_offset, 3)
    || chunk.world_offset[0] !== chunkX * chunk.chunk_size_m
    || chunk.world_offset[1] !== chunkY * chunk.chunk_size_m
    || chunk.world_offset[2] !== 0
    || chunk.layout_algorithm_id !== 'mock-layout-v1'
    || chunk.terrain_algorithm_id !== grid.terrain_algorithm_id
    || chunk.mesh_asset_bundle_id !== grid.mesh_asset_bundle_id
    || chunk.material_bundle_id !== grid.material_bundle_id
    || chunk.selected_lod !== lod
    || chunk.synthetic !== true
    || chunk.geometry_usability !== 'preview-only'
    || chunk.coordinate_confidence !== 'synthetic-layout'
    || chunk.metric_alignment !== false
    || chunk.real_photo_textures !== false
    || !finiteTuple(chunk.aabb?.min, 3)
    || !finiteTuple(chunk.aabb?.max, 3)
  ) {
    throw new TypeError('mesh chunk bundle, coordinate, or provenance contract is invalid');
  }
  validateTerrain(chunk);
  validateRibbons(chunk.roads, 'road');
  validateRibbons(chunk.water, 'water');
  validateInstances(chunk);
  validateSurfaceMaterials(payload, chunk, grid);

  if (!Array.isArray(payload.asset_urls)) {
    throw new TypeError('mesh asset route registry is invalid');
  }
  const descriptors = new Map();
  for (const descriptor of payload.asset_urls) {
    if (
      !isObject(descriptor)
      || !ASSET_ID.test(descriptor.asset_id)
      || descriptors.has(descriptor.asset_id)
      || descriptor.lod !== lod
      || descriptor.url !== expectedAssetPath(grid, descriptor.asset_id, lod)
      || !SHA256.test(descriptor.glb_sha256)
      || !Number.isSafeInteger(descriptor.glb_bytes)
      || descriptor.glb_bytes <= 0
    ) {
      throw new TypeError('mesh asset route contract is invalid');
    }
    descriptors.set(descriptor.asset_id, descriptor);
  }
  const assetIds = [...new Set(chunk.instances.map((instance) => instance.asset_id))].sort();
  if (
    assetIds.length !== descriptors.size
    || assetIds.some((assetId, index) => (
      payload.asset_urls[index]?.asset_id !== assetId
      || !descriptors.has(assetId)
    ))
  ) {
    throw new TypeError('mesh instance and asset route closures disagree');
  }
  return payload;
}

export function meshInstanceThreeTransform(instance, worldOffset) {
  if (
    !finiteTuple(worldOffset, 3)
    || !finiteTuple(instance?.local_position, 3)
    || !Number.isFinite(instance?.rotation_z_degrees)
    || !Number.isFinite(instance?.scale)
  ) {
    throw new TypeError('mesh instance transform is invalid');
  }
  const east = worldOffset[0] + instance.local_position[0];
  const north = worldOffset[1] + instance.local_position[1];
  const up = worldOffset[2] + instance.local_position[2];
  return {
    position: [east, up, -north],
    rotationYRadians: instance.rotation_z_degrees * Math.PI / 180,
    scale: instance.scale,
  };
}

export function meshInstanceThreeTransformInChunk(instance, chunk) {
  return meshInstanceThreeTransform(instance, chunk?.world_offset);
}

export function terrainGeometryThree(chunk) {
  validateTerrain(chunk);
  const { resolution, vertices } = chunk.terrain;
  const positions = new Float32Array(vertices.length * 3);
  const uvs = new Float32Array(vertices.length * 2);
  const colors = new Float32Array(vertices.length * 3);
  for (let index = 0; index < vertices.length; index += 1) {
    const vertex = vertices[index];
    positions.set([vertex.world_u, vertex.z, -vertex.world_v], index * 3);
    uvs.set([vertex.world_u, vertex.world_v], index * 2);
    colors.set(
      [vertex.macro_tint, vertex.macro_tint, vertex.macro_tint],
      index * 3,
    );
  }
  const materialSlotIds = [...new Set(
    chunk.terrain.material_slot_ids,
  )].sort();
  const buckets = new Map(
    materialSlotIds.map((slotId) => [slotId, []]),
  );
  for (let row = 0; row < resolution - 1; row += 1) {
    for (let column = 0; column < resolution - 1; column += 1) {
      const southwest = row * resolution + column;
      const southeast = southwest + 1;
      const northwest = southwest + resolution;
      const northeast = northwest + 1;
      const slotId = chunk.terrain.material_slot_ids[
        row * (resolution - 1) + column
      ];
      buckets.get(slotId).push(
        southwest,
        northwest,
        southeast,
        southeast,
        northwest,
        northeast,
      );
    }
  }
  const indices = new Uint32Array((resolution - 1) ** 2 * 6);
  const groups = [];
  let cursor = 0;
  for (
    let materialIndex = 0;
    materialIndex < materialSlotIds.length;
    materialIndex += 1
  ) {
    const materialSlotId = materialSlotIds[materialIndex];
    const bucket = buckets.get(materialSlotId);
    indices.set(bucket, cursor);
    groups.push({
      start: cursor,
      count: bucket.length,
      materialIndex,
      materialSlotId,
    });
    cursor += bucket.length;
  }
  return {
    positions,
    uvs,
    colors,
    indices,
    groups,
    materialSlotIds,
  };
}

export function ribbonGeometryThree(chunk, ribbon) {
  validateRibbons([ribbon], ribbon?.kind);
  const halfWidth = ribbon.width / 2;
  const positions = new Float32Array(ribbon.points.length * 2 * 3);
  const uvs = new Float32Array(ribbon.points.length * 2 * 2);
  let distance = 0;
  for (let index = 0; index < ribbon.points.length; index += 1) {
    const point = ribbon.points[index];
    const previous = ribbon.points[Math.max(0, index - 1)];
    const following = ribbon.points[Math.min(ribbon.points.length - 1, index + 1)];
    const tangentX = following[0] - previous[0];
    const tangentY = following[1] - previous[1];
    const length = Math.hypot(tangentX, tangentY);
    if (length <= 1e-9) {
      throw new TypeError('mesh ribbon contains a zero-length tangent');
    }
    if (index > 0) {
      distance += Math.hypot(
        point[0] - ribbon.points[index - 1][0],
        point[1] - ribbon.points[index - 1][1],
      );
    }
    const normalX = -tangentY / length * halfWidth;
    const normalY = tangentX / length * halfWidth;
    const east = chunk.world_offset[0] + point[0];
    const north = chunk.world_offset[1] + point[1];
    const up = chunk.world_offset[2] + point[2];
    positions.set([east + normalX, up, -(north + normalY)], index * 6);
    positions.set([east - normalX, up, -(north - normalY)], index * 6 + 3);
    uvs.set([distance, 0], index * 4);
    uvs.set([distance, ribbon.width], index * 4 + 2);
  }
  const indices = new Uint32Array((ribbon.points.length - 1) * 6);
  for (let index = 0; index < ribbon.points.length - 1; index += 1) {
    const left = index * 2;
    const right = left + 1;
    const nextLeft = left + 2;
    const nextRight = left + 3;
    indices.set([left, nextLeft, right, right, nextLeft, nextRight], index * 6);
  }
  return { positions, uvs, indices };
}
