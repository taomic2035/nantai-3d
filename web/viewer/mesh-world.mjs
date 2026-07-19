const CHUNK_TEMPLATE = '/api/world/mesh-chunk/{x}/{y}.json';
const ASSET_TEMPLATE =
  '/api/world/mesh-assets/{bundle_id}/{asset_id}/lod{lod}.glb';
const ASSET_TEMPLATE_V3 =
  '/api/world/mesh-assets/{bundle_id}/{profile_id}/{asset_id}/lod{lod}.glb';
const TEXTURE_TEMPLATE_V3 =
  '/api/world/mesh-textures/{bundle_id}/{profile_id}/{sha256}.{extension}';
const SHA256 = /^[0-9a-f]{64}$/;
const ASSET_ID = /^[a-z0-9]+(?:_[a-z0-9]+)*$/;
const MATERIAL_SLOT_ID = /^material-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const RUNTIME_V1 = 'nantai.synthetic-village.mesh-chunk-runtime.v1';
const RUNTIME_V2 = 'nantai.synthetic-village.mesh-chunk-runtime.v2';
const RUNTIME_V3 = 'nantai.synthetic-village.mesh-chunk-runtime.v3';
const H3_PROFILE_ID = 'h3-ai-ktx2-4k';
const H2_PROFILE_ID = 'h2-png-1k-fallback';
const MAX_H3_COMPRESSED_TEXTURE_BYTES = 512 * 1024 * 1024;
const MAX_PROFILE_PNG_BYTES = 64 * 1024 * 1024;
const MAX_PROFILE_TEXTURE_DIMENSION = 16_384;
const VALIDATED_RUNTIME_V3 = new WeakSet();
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
const GRID_V3_KEYS = new Set([
  'runtime_schema',
  'on_demand',
  'url_template',
  'asset_url_template',
  'texture_url_template',
  'world_seed',
  'layout_engine',
  'terrain_algorithm_id',
  'source_mesh_asset_bundle_id',
  'mesh_asset_bundle_id',
  'fallback_material_bundle_id',
  'material_bundle_id',
]);
const RUNTIME_KEYS = new Set([
  'schema_version',
  'chunk',
  'asset_urls',
  'surface_materials',
]);
const RUNTIME_V3_KEYS = new Set([
  'schema_version',
  'chunk',
  'source_mesh_asset_bundle_id',
  'mesh_asset_bundle_id',
  'material_bundle_id',
  'fallback_material_bundle_id',
  'primary_profile_id',
  'fallback_profile_id',
  'predicted_compressed_texture_bytes',
  'profiles',
  'surface_materials',
  'synthetic',
  'ai_generated',
  'real_photo_textures',
  'geometry_usability',
  'metric_alignment',
  'verification_level',
]);
const CHUNK_KEYS = new Set([
  'schema_version',
  'content_key',
  'renderer_capability',
  'world_seed',
  'chunk_id',
  'chunk_size_m',
  'world_offset',
  'layout_algorithm_id',
  'layout_sha256',
  'terrain_algorithm_id',
  'mesh_asset_bundle_id',
  'material_bundle_id',
  'selected_lod',
  'terrain',
  'roads',
  'water',
  'instances',
  'aabb',
  'synthetic',
  'geometry_usability',
  'coordinate_confidence',
  'metric_alignment',
  'real_photo_textures',
]);
const CHUNK_ID_KEYS = new Set(['x', 'y']);
const BOUNDS_KEYS = new Set(['min', 'max']);
const TERRAIN_KEYS = new Set([
  'algorithm_id',
  'resolution',
  'material_slot_id',
  'material_slot_ids',
  'vertices',
]);
const TERRAIN_VERTEX_KEYS = new Set([
  'x',
  'y',
  'z',
  'world_u',
  'world_v',
  'macro_tint',
]);
const RIBBON_KEYS = new Set([
  'ribbon_id',
  'kind',
  'feature_type',
  'width',
  'z_offset',
  'material_slot_id',
  'points',
]);
const INSTANCE_KEYS = new Set([
  'instance_id',
  'asset_id',
  'kind',
  'local_position',
  'rotation_z_degrees',
  'scale',
  'template_lod',
]);
const ASSET_RUNTIME_V1_KEYS = new Set([
  'asset_id',
  'lod',
  'url',
  'glb_sha256',
  'glb_bytes',
]);
const ASSET_RUNTIME_V2_KEYS = new Set([
  ...ASSET_RUNTIME_V1_KEYS,
  'texture_dependencies',
]);
const TEXTURE_DEPENDENCY_KEYS = new Set([
  'url',
  'sha256',
  'bytes',
  'role',
  'colour_space',
  'material_slot_id',
  'derivation_algorithm_id',
  'min_filter',
  'mag_filter',
  'wrap_s',
  'wrap_t',
]);
const PROFILE_V3_KEYS = new Set([
  'profile_id',
  'asset_urls',
  'textures',
]);
const ASSET_RUNTIME_V3_KEYS = new Set([
  'profile_id',
  'asset_id',
  'lod',
  'url',
  'glb_sha256',
  'glb_bytes',
  'geometry_fingerprint',
  'texture_dependencies',
]);
const TEXTURE_RUNTIME_V3_KEYS = new Set([
  'url',
  'sha256',
  'bytes',
  'width',
  'height',
  'media_type',
  'role',
  'transfer',
  'material_slot_id',
]);
const SURFACE_POLICY_V3_KEYS = new Set([
  'slot_id',
  'uv_policy',
  'nominal_tile_m',
  'normal_strength',
  'roughness_center',
  'metallic',
]);
const MATERIAL_SLOTS_V3 = [
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
  if (grid?.runtime_schema === RUNTIME_V3) {
    return (
      exactKeys(grid, GRID_V3_KEYS)
      && grid.on_demand === true
      && grid.url_template === CHUNK_TEMPLATE
      && grid.asset_url_template === ASSET_TEMPLATE_V3
      && grid.texture_url_template === TEXTURE_TEMPLATE_V3
      && Number.isSafeInteger(grid.world_seed)
      && grid.layout_engine === 'mock'
      && grid.terrain_algorithm_id === TERRAIN_ALGORITHM_ID
      && SHA256.test(grid.source_mesh_asset_bundle_id)
      && SHA256.test(grid.mesh_asset_bundle_id)
      && SHA256.test(grid.fallback_material_bundle_id)
      && SHA256.test(grid.material_bundle_id)
    );
  }
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

function expectedTexturePath(bundleId, sha256) {
  return `/api/world/mesh-assets/${bundleId}/textures/${sha256}.png`;
}

function validateTerrain(chunk) {
  const terrain = chunk.terrain;
  const resolution = TERRAIN_RESOLUTION;
  if (
    !exactKeys(terrain, TERRAIN_KEYS)
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
      !exactKeys(vertex, TERRAIN_VERTEX_KEYS)
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
      !exactKeys(ribbon, RIBBON_KEYS)
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
      !exactKeys(instance, INSTANCE_KEYS)
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

function validateTextureDependencies(descriptor, grid, lod) {
  const dependencies = descriptor.texture_dependencies;
  if (
    !Array.isArray(dependencies)
    || (lod === 2 && dependencies.length === 0)
    || (lod !== 2 && dependencies.length !== 0)
  ) {
    throw new TypeError('mesh texture dependency closure is invalid');
  }
  const semanticKeys = new Set();
  let previousSortKey = null;
  for (const dependency of dependencies) {
    const expectedColourSpace =
      dependency?.role === 'base_color' ? 'srgb' : 'non-color';
    const sortKey = [
      dependency?.sha256,
      dependency?.role,
      dependency?.material_slot_id,
    ].join(':');
    const semanticKey =
      `${dependency?.material_slot_id}:${dependency?.role}`;
    if (
      !exactKeys(dependency, TEXTURE_DEPENDENCY_KEYS)
      || dependency.url !== expectedTexturePath(
        grid.mesh_asset_bundle_id,
        dependency.sha256,
      )
      || !SHA256.test(dependency.sha256)
      || !Number.isSafeInteger(dependency.bytes)
      || dependency.bytes <= 0
      || !['base_color', 'normal', 'orm'].includes(dependency.role)
      || dependency.colour_space !== expectedColourSpace
      || !MATERIAL_SLOT_ID.test(dependency.material_slot_id)
      || typeof dependency.derivation_algorithm_id !== 'string'
      || dependency.derivation_algorithm_id.length === 0
      || dependency.min_filter !== 9987
      || dependency.mag_filter !== 9729
      || dependency.wrap_s !== 10497
      || dependency.wrap_t !== 10497
      || (previousSortKey !== null && sortKey < previousSortKey)
      || semanticKeys.has(semanticKey)
    ) {
      throw new TypeError('mesh texture dependency closure is invalid');
    }
    previousSortKey = sortKey;
    semanticKeys.add(semanticKey);
  }
}

function validateChunkContract(
  chunk,
  {
    grid,
    chunkX,
    chunkY,
    lod,
    meshAssetBundleId,
    materialBundleId,
  },
) {
  if (
    !exactKeys(chunk, CHUNK_KEYS)
    || chunk.schema_version !== 'nantai.synthetic-village.mesh-chunk.v1'
    || chunk.renderer_capability !== 'synthetic-textured-mesh-grid'
    || !SHA256.test(chunk.content_key)
    || !SHA256.test(chunk.layout_sha256)
    || chunk.world_seed !== grid.world_seed
    || !exactKeys(chunk.chunk_id, CHUNK_ID_KEYS)
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
    || chunk.mesh_asset_bundle_id !== meshAssetBundleId
    || chunk.material_bundle_id !== materialBundleId
    || chunk.selected_lod !== lod
    || chunk.synthetic !== true
    || chunk.geometry_usability !== 'preview-only'
    || chunk.coordinate_confidence !== 'synthetic-layout'
    || chunk.metric_alignment !== false
    || chunk.real_photo_textures !== false
    || !exactKeys(chunk.aabb, BOUNDS_KEYS)
    || !finiteTuple(chunk.aabb?.min, 3)
    || !finiteTuple(chunk.aabb?.max, 3)
  ) {
    throw new TypeError(
      'mesh chunk bundle, coordinate, or provenance contract is invalid',
    );
  }
  validateTerrain(chunk);
  validateRibbons(chunk.roads, 'road');
  validateRibbons(chunk.water, 'water');
  validateInstances(chunk);
}

function expectedProfileTexturePath(
  bundleId,
  profileId,
  sha256,
  mediaType,
) {
  const extension = mediaType === 'image/ktx2' ? 'ktx2' : 'png';
  return (
    `/api/world/mesh-textures/${bundleId}/${profileId}/`
    + `${sha256}.${extension}`
  );
}

function validateTextureRuntimeV3(
  descriptor,
  {
    bundleId,
    profileId,
  },
) {
  const expectedTransfer = descriptor?.role === 'base_color'
    ? 'srgb'
    : 'linear';
  if (
    !exactKeys(descriptor, TEXTURE_RUNTIME_V3_KEYS)
    || !SHA256.test(descriptor.sha256)
    || !Number.isSafeInteger(descriptor.bytes)
    || descriptor.bytes <= 0
    || (
      descriptor.media_type === 'image/png'
      && descriptor.bytes > MAX_PROFILE_PNG_BYTES
    )
    || (
      descriptor.media_type === 'image/ktx2'
      && descriptor.bytes > MAX_H3_COMPRESSED_TEXTURE_BYTES
    )
    || !Number.isSafeInteger(descriptor.width)
    || descriptor.width <= 0
    || descriptor.width > MAX_PROFILE_TEXTURE_DIMENSION
    || !Number.isSafeInteger(descriptor.height)
    || descriptor.height <= 0
    || descriptor.height > MAX_PROFILE_TEXTURE_DIMENSION
    || !['image/png', 'image/ktx2'].includes(descriptor.media_type)
    || (profileId === H2_PROFILE_ID
      && descriptor.media_type !== 'image/png')
    || !['base_color', 'normal', 'orm'].includes(descriptor.role)
    || descriptor.transfer !== expectedTransfer
    || !MATERIAL_SLOT_ID.test(descriptor.material_slot_id)
    || descriptor.url !== expectedProfileTexturePath(
      bundleId,
      profileId,
      descriptor.sha256,
      descriptor.media_type,
    )
  ) {
    throw new TypeError('mesh runtime v3 texture contract is invalid');
  }
}

function validateSurfacePoliciesV3(payload, chunk) {
  const requiredSlots = [...new Set([
    chunk.terrain.material_slot_id,
    ...chunk.terrain.material_slot_ids,
    ...chunk.roads.map((ribbon) => ribbon.material_slot_id),
    ...chunk.water.map((ribbon) => ribbon.material_slot_id),
  ])].sort();
  if (
    !Array.isArray(payload.surface_materials)
    || payload.surface_materials.length !== requiredSlots.length
  ) {
    throw new TypeError('mesh runtime v3 surface contract is invalid');
  }
  for (const [index, material] of payload.surface_materials.entries()) {
    if (
      !exactKeys(material, SURFACE_POLICY_V3_KEYS)
      || material.slot_id !== requiredSlots[index]
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
      throw new TypeError('mesh runtime v3 surface contract is invalid');
    }
  }
}

function validateProfileV3(payload, profileId, requiredAssets) {
  const profile = payload.profiles[profileId];
  if (
    !exactKeys(profile, PROFILE_V3_KEYS)
    || profile.profile_id !== profileId
    || !Array.isArray(profile.asset_urls)
    || profile.asset_urls.length !== requiredAssets.length
    || !Array.isArray(profile.textures)
    || profile.textures.length !== MATERIAL_SLOTS_V3.length * 3
  ) {
    throw new TypeError('mesh runtime v3 profile contract is invalid');
  }
  const expectedTextureKeys = MATERIAL_SLOTS_V3.flatMap((slotId) => (
    ['base_color', 'normal', 'orm'].map((role) => `${slotId}:${role}`)
  ));
  for (const [index, descriptor] of profile.textures.entries()) {
    validateTextureRuntimeV3(descriptor, {
      bundleId: payload.mesh_asset_bundle_id,
      profileId,
    });
    if (
      `${descriptor.material_slot_id}:${descriptor.role}`
      !== expectedTextureKeys[index]
    ) {
      throw new TypeError(
        'mesh runtime v3 profile texture closure is invalid',
      );
    }
  }
  for (const [index, descriptor] of profile.asset_urls.entries()) {
    const expectedAssetId = requiredAssets[index];
    if (
      !exactKeys(descriptor, ASSET_RUNTIME_V3_KEYS)
      || descriptor.profile_id !== profileId
      || descriptor.asset_id !== expectedAssetId
      || descriptor.lod !== payload.chunk.selected_lod
      || descriptor.url !== (
        `/api/world/mesh-assets/${payload.mesh_asset_bundle_id}/`
        + `${profileId}/${expectedAssetId}/lod${descriptor.lod}.glb`
      )
      || !SHA256.test(descriptor.glb_sha256)
      || !Number.isSafeInteger(descriptor.glb_bytes)
      || descriptor.glb_bytes <= 0
      || !Array.isArray(descriptor.texture_dependencies)
    ) {
      throw new TypeError('mesh runtime v3 profile asset contract is invalid');
    }
    if (descriptor.lod === 2) {
      if (
        !SHA256.test(descriptor.geometry_fingerprint)
        || descriptor.texture_dependencies.length === 0
      ) {
        throw new TypeError(
          'mesh runtime v3 profile geometry contract is invalid',
        );
      }
    } else if (
      descriptor.geometry_fingerprint !== null
      || descriptor.texture_dependencies.length !== 0
    ) {
      throw new TypeError(
        'mesh runtime v3 embedded profile contract is invalid',
      );
    }
    let previousKey = null;
    const semanticKeys = new Set();
    for (const dependency of descriptor.texture_dependencies) {
      validateTextureRuntimeV3(dependency, {
        bundleId: payload.mesh_asset_bundle_id,
        profileId,
      });
      const semanticKey =
        `${dependency.material_slot_id}:${dependency.role}`;
      if (
        (previousKey !== null && semanticKey < previousKey)
        || semanticKeys.has(semanticKey)
      ) {
        throw new TypeError(
          'mesh runtime v3 asset texture closure is invalid',
        );
      }
      previousKey = semanticKey;
      semanticKeys.add(semanticKey);
    }
  }
  return profile;
}

function predictedCompressedBytesV3(profile) {
  const unique = new Map();
  for (const descriptor of [
    ...profile.textures,
    ...profile.asset_urls.flatMap(
      (asset) => asset.texture_dependencies,
    ),
  ]) {
    if (descriptor.media_type !== 'image/ktx2') continue;
    unique.set(
      [
        descriptor.sha256,
        descriptor.material_slot_id,
        descriptor.role,
      ].join(':'),
      descriptor.bytes,
    );
  }
  return [...unique.values()].reduce((sum, bytes) => sum + bytes, 0);
}

function validateMeshChunkRuntimeV3(
  payload,
  {
    grid,
    chunkX,
    chunkY,
    lod,
  },
) {
  const chunk = payload?.chunk;
  if (
    !exactKeys(payload, RUNTIME_V3_KEYS)
    || payload.schema_version !== RUNTIME_V3
    || payload.source_mesh_asset_bundle_id
      !== grid.source_mesh_asset_bundle_id
    || payload.mesh_asset_bundle_id !== grid.mesh_asset_bundle_id
    || payload.material_bundle_id !== grid.material_bundle_id
    || payload.fallback_material_bundle_id
      !== grid.fallback_material_bundle_id
    || payload.primary_profile_id !== H3_PROFILE_ID
    || payload.fallback_profile_id !== H2_PROFILE_ID
    || !Number.isSafeInteger(payload.predicted_compressed_texture_bytes)
    || payload.predicted_compressed_texture_bytes <= 0
    || payload.predicted_compressed_texture_bytes
      > MAX_H3_COMPRESSED_TEXTURE_BYTES
    || !exactKeys(
      payload.profiles,
      new Set([H2_PROFILE_ID, H3_PROFILE_ID]),
    )
    || payload.synthetic !== true
    || payload.ai_generated !== true
    || payload.real_photo_textures !== false
    || payload.geometry_usability !== 'preview-only'
    || payload.metric_alignment !== false
    || payload.verification_level !== 'L0'
  ) {
    throw new TypeError(
      'mesh runtime v3 identity, budget, or provenance contract is invalid',
    );
  }
  validateChunkContract(chunk, {
    grid,
    chunkX,
    chunkY,
    lod,
    meshAssetBundleId: grid.source_mesh_asset_bundle_id,
    materialBundleId: grid.fallback_material_bundle_id,
  });
  const requiredAssets = [...new Set(
    chunk.instances.map((instance) => instance.asset_id),
  )].sort();
  const h2 = validateProfileV3(payload, H2_PROFILE_ID, requiredAssets);
  const h3 = validateProfileV3(payload, H3_PROFILE_ID, requiredAssets);
  for (const assetId of requiredAssets) {
    const h2Asset = h2.asset_urls.find(
      (descriptor) => descriptor.asset_id === assetId,
    );
    const h3Asset = h3.asset_urls.find(
      (descriptor) => descriptor.asset_id === assetId,
    );
    if (
      h2Asset.geometry_fingerprint !== h3Asset.geometry_fingerprint
    ) {
      throw new TypeError(
        'mesh runtime v3 profile geometry contract is invalid',
      );
    }
  }
  if (
    predictedCompressedBytesV3(h3)
    !== payload.predicted_compressed_texture_bytes
  ) {
    throw new TypeError(
      'mesh runtime v3 compressed texture budget contract is invalid',
    );
  }
  validateSurfacePoliciesV3(payload, chunk);
  VALIDATED_RUNTIME_V3.add(payload);
  return deepFreeze(payload);
}

function deepFreeze(value) {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
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
  const runtimeVersion = payload?.schema_version;
  if (
    grid.runtime_schema === RUNTIME_V3
    || runtimeVersion === RUNTIME_V3
  ) {
    if (
      grid.runtime_schema !== RUNTIME_V3
      || runtimeVersion !== RUNTIME_V3
    ) {
      throw new TypeError(
        'mesh runtime v3 and world grid contract disagree',
      );
    }
    return validateMeshChunkRuntimeV3(payload, {
      grid,
      chunkX,
      chunkY,
      lod,
    });
  }
  const chunk = payload?.chunk;
  const isRuntimeV2 = runtimeVersion === RUNTIME_V2;
  if (
    !exactKeys(payload, RUNTIME_KEYS)
    || ![RUNTIME_V1, RUNTIME_V2].includes(runtimeVersion)
  ) {
    throw new TypeError('mesh runtime contract is invalid');
  }
  validateChunkContract(chunk, {
    grid,
    chunkX,
    chunkY,
    lod,
    meshAssetBundleId: grid.mesh_asset_bundle_id,
    materialBundleId: grid.material_bundle_id,
  });
  validateSurfaceMaterials(payload, chunk, grid);

  if (!Array.isArray(payload.asset_urls)) {
    throw new TypeError('mesh asset route registry is invalid');
  }
  const descriptors = new Map();
  for (const descriptor of payload.asset_urls) {
    const expectedKeys = isRuntimeV2
      ? ASSET_RUNTIME_V2_KEYS
      : ASSET_RUNTIME_V1_KEYS;
    if (
      !exactKeys(descriptor, expectedKeys)
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
    if (isRuntimeV2) {
      validateTextureDependencies(descriptor, grid, lod);
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

export function resolveSelectedProfile(payload, profileId) {
  if (
    payload?.schema_version !== RUNTIME_V3
    || !VALIDATED_RUNTIME_V3.has(payload)
    || ![H3_PROFILE_ID, H2_PROFILE_ID].includes(profileId)
    || !exactKeys(
      payload.profiles,
      new Set([H2_PROFILE_ID, H3_PROFILE_ID]),
    )
    || payload.profiles[profileId]?.profile_id !== profileId
  ) {
    throw new TypeError('mesh runtime v3 selected profile is invalid');
  }
  const profile = payload.profiles[profileId];
  return deepFreeze(JSON.parse(JSON.stringify({
    profile_id: profileId,
    asset_urls: profile.asset_urls,
    textures: profile.textures,
    surface_materials: payload.surface_materials,
    predicted_compressed_texture_bytes: (
      profileId === H3_PROFILE_ID
        ? payload.predicted_compressed_texture_bytes
        : 0
    ),
    synthetic: payload.synthetic,
    ai_generated: payload.ai_generated,
    real_photo_textures: payload.real_photo_textures,
    geometry_usability: payload.geometry_usability,
    metric_alignment: payload.metric_alignment,
    verification_level: payload.verification_level,
  })));
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
