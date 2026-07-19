const SHA256 = /^[0-9a-f]{64}$/;
const TEXTURE_ROLES = new Set(['base_color', 'normal', 'orm']);
const TRANSIENT_TEXTURE_PROPERTIES = [
  'map',
  'normalMap',
  'roughnessMap',
  'metalnessMap',
  'aoMap',
  'emissiveMap',
  'alphaMap',
  'bumpMap',
  'displacementMap',
  'lightMap',
  'envMap',
  'clearcoatMap',
  'clearcoatNormalMap',
  'clearcoatRoughnessMap',
  'iridescenceMap',
  'iridescenceThicknessMap',
  'sheenColorMap',
  'sheenRoughnessMap',
  'specularColorMap',
  'specularIntensityMap',
  'transmissionMap',
  'thicknessMap',
];
const FORBIDDEN_MATERIAL_TEXTURE_PROPERTIES = new Set(
  TRANSIENT_TEXTURE_PROPERTIES.filter(
    (name) => ![
      'map',
      'normalMap',
      'roughnessMap',
      'metalnessMap',
      'aoMap',
    ].includes(name),
  ),
);

function bytesToHex(bytes) {
  return [...new Uint8Array(bytes)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
}

function arrayBufferFrom(bytes) {
  return bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  );
}

function contentType(response) {
  return response.headers.get('content-type')?.split(';', 1)[0]
    .trim()
    .toLowerCase() ?? '';
}

function textureUri(loaded, texture) {
  const association = loaded?.parser?.associations?.get(texture);
  const textureIndex = association?.textures;
  const textureRecord = Number.isInteger(textureIndex)
    ? loaded?.parser?.json?.textures?.[textureIndex]
    : null;
  const sourceIndex = Number.isInteger(textureRecord?.source)
    ? textureRecord.source
    : textureRecord?.extensions?.KHR_texture_basisu?.source;
  const imageRecord = Number.isInteger(sourceIndex)
    ? loaded?.parser?.json?.images?.[sourceIndex]
    : null;
  return typeof imageRecord?.uri === 'string' ? imageRecord.uri : null;
}

function expectedRelativeTextureUri(dependency) {
  const extension = dependency.media_type === 'image/ktx2'
    ? 'ktx2'
    : 'png';
  return `../textures/${dependency.sha256}.${extension}`;
}

function templateKey(descriptor) {
  return JSON.stringify([
    descriptor.glb_sha256,
    descriptor.glb_bytes,
    descriptor.profile_id ?? 'legacy-png',
    (descriptor.texture_dependencies ?? []).map((row) => [
      row.sha256,
      row.bytes,
      row.media_type ?? 'image/png',
    ]),
  ]);
}

function descriptorIdentity(descriptor, mimeType) {
  return {
    url: descriptor.url,
    sha256: descriptor.sha256,
    bytes: descriptor.bytes,
    mimeType,
  };
}

function descriptorAgrees(record, identity) {
  return (
    record.url === identity.url
    && record.sha256 === identity.sha256
    && record.bytes === identity.bytes
    && record.mimeType === identity.mimeType
  );
}

function collectParsedResources(loaded) {
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  let meshCount = 0;
  loaded?.scene?.traverse?.((object) => {
    if (!object?.isMesh) return;
    meshCount += 1;
    if (object.geometry) geometries.add(object.geometry);
    const objectMaterials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    for (const material of objectMaterials) {
      if (!material) continue;
      materials.add(material);
      for (const property of TRANSIENT_TEXTURE_PROPERTIES) {
        if (material[property]?.dispose) textures.add(material[property]);
      }
    }
  });
  for (const resource of loaded?.parser?.associations?.keys?.() ?? []) {
    if (resource?.dispose) {
      if (
        !materials.has(resource)
        && !geometries.has(resource)
      ) {
        textures.add(resource);
      }
    }
  }
  return {
    meshCount,
    geometries,
    materials,
    transientTextures: textures,
  };
}

/**
 * Flatten a verified static near template into one world-baked mesh per material.
 *
 * Blender intentionally emits many small authored parts for fidelity. Keeping
 * every part as a distinct WebGL geometry makes the runtime primitive count
 * scale into the thousands, even though parts sharing a material can be merged
 * without changing their verified bytes or visual material identity.
 */
export function compactTemplateSceneByMaterial({
  scene,
  THREE,
  mergeGeometriesFn,
}) {
  if (
    !scene?.traverse
    || !scene?.updateMatrixWorld
    || typeof THREE?.Group !== 'function'
    || typeof THREE?.Mesh !== 'function'
    || typeof mergeGeometriesFn !== 'function'
  ) {
    throw new TypeError('near template compaction dependencies are unavailable');
  }
  scene.updateMatrixWorld(true);
  const byMaterial = new Map();
  const sourceGeometries = new Set();
  const transformedGeometries = new Set();
  const outputGeometries = new Set();
  try {
    scene.traverse((object) => {
      if (!object?.isMesh) return;
      const morphed = (
        object.morphTargetInfluences != null
        || Object.keys(object.geometry?.morphAttributes ?? {}).length > 0
      );
      if (
        object.isSkinnedMesh
        || object.isInstancedMesh
        || morphed
        || Array.isArray(object.material)
      ) {
        throw new TypeError(
          'cannot compact skinned, instanced, morphed, or multi-material mesh',
        );
      }
      if (
        !object.geometry?.clone
        || !object.material
        || !object.matrixWorld
      ) {
        throw new TypeError('cannot compact incomplete static mesh');
      }
      const transformed = object.geometry.clone();
      if (!transformed?.applyMatrix4 || !transformed?.dispose) {
        throw new TypeError('cannot compact non-buffer geometry');
      }
      transformed.applyMatrix4(object.matrixWorld);
      sourceGeometries.add(object.geometry);
      transformedGeometries.add(transformed);
      const geometries = byMaterial.get(object.material) ?? [];
      geometries.push(transformed);
      byMaterial.set(object.material, geometries);
    });
    if (byMaterial.size === 0) {
      throw new TypeError('verified mesh template contains no compactable mesh');
    }

    const compactedScene = new THREE.Group();
    compactedScene.name = scene.name ?? '';
    for (const [material, geometries] of byMaterial) {
      let geometry;
      if (geometries.length === 1) {
        [geometry] = geometries;
        transformedGeometries.delete(geometry);
      } else {
        geometry = mergeGeometriesFn(geometries, false);
        if (!geometry?.dispose) {
          throw new TypeError('static mesh primitive merge failed');
        }
        for (const transformed of geometries) {
          transformed.dispose();
          transformedGeometries.delete(transformed);
        }
      }
      outputGeometries.add(geometry);
      const mesh = new THREE.Mesh(geometry, material);
      mesh.castShadow = false;
      mesh.receiveShadow = true;
      compactedScene.add(mesh);
    }
    compactedScene.updateMatrixWorld?.(true);
    return {
      scene: compactedScene,
      sourceGeometries,
      geometries: outputGeometries,
    };
  } catch (error) {
    for (const geometry of transformedGeometries) geometry.dispose?.();
    for (const geometry of outputGeometries) geometry.dispose?.();
    throw error;
  }
}

function disposeParsedTexture(texture) {
  texture.dispose();
  texture.image?.close?.();
}

function disposeParsedResources(resources) {
  for (const texture of resources.transientTextures) {
    disposeParsedTexture(texture);
  }
  for (const material of resources.materials) material.dispose?.();
  for (const geometry of resources.geometries) geometry.dispose?.();
}

function disposeTransientTextures(resources) {
  for (const texture of resources.transientTextures) {
    disposeParsedTexture(texture);
  }
  resources.transientTextures.clear();
}

function assertEmbeddedGlb(glbBytes) {
  if (!(glbBytes instanceof Uint8Array) || glbBytes.byteLength < 20) {
    throw new TypeError('embedded mesh GLB is malformed');
  }
  const view = new DataView(
    glbBytes.buffer,
    glbBytes.byteOffset,
    glbBytes.byteLength,
  );
  if (
    view.getUint32(0, true) !== 0x46546c67
    || view.getUint32(4, true) !== 2
    || view.getUint32(8, true) !== glbBytes.byteLength
    || view.getUint32(16, true) !== 0x4e4f534a
  ) {
    throw new TypeError('embedded mesh GLB header is invalid');
  }
  const jsonLength = view.getUint32(12, true);
  if (jsonLength === 0 || 20 + jsonLength > glbBytes.byteLength) {
    throw new TypeError('embedded mesh GLB JSON chunk is invalid');
  }
  let json;
  try {
    json = JSON.parse(
      new TextDecoder()
        .decode(glbBytes.subarray(20, 20 + jsonLength))
        .replace(/[\u0000\u0020]+$/u, ''),
    );
  } catch {
    throw new TypeError('embedded mesh GLB JSON is invalid');
  }
  if (
    !Array.isArray(json.buffers)
    || json.buffers.length === 0
    || json.buffers.some((buffer) => (
      buffer === null
      || typeof buffer !== 'object'
      || Object.hasOwn(buffer, 'uri')
    ))
    || !Array.isArray(json.images)
    || json.images.some((image) => (
      image === null
      || typeof image !== 'object'
      || Object.hasOwn(image, 'uri')
      || !Number.isSafeInteger(image.bufferView)
      || !['image/png', 'image/jpeg', 'image/webp'].includes(image.mimeType)
    ))
  ) {
    throw new TypeError('embedded mesh GLB contains an external resource');
  }
}

function normalizeDependency(row, materialProfile) {
  if (row?.media_type !== undefined) {
    const keys = new Set([
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
    const actualKeys = (
      row !== null
      && typeof row === 'object'
      && !Array.isArray(row)
    ) ? Object.keys(row) : [];
    const extension = row.media_type === 'image/ktx2'
      ? 'ktx2'
      : 'png';
    const expectedTransfer = row.role === 'base_color'
      ? 'srgb'
      : 'linear';
    const urlParts = typeof row.url === 'string'
      ? row.url.split('/')
      : [];
    if (
      !['h3-ai-ktx2-4k', 'h2-png-1k-fallback'].includes(
        materialProfile,
      )
      || actualKeys.length !== keys.size
      || actualKeys.some((key) => !keys.has(key))
      || typeof row.url !== 'string'
      || urlParts.length !== 7
      || urlParts[0] !== ''
      || urlParts[1] !== 'api'
      || urlParts[2] !== 'world'
      || urlParts[3] !== 'mesh-textures'
      || !SHA256.test(urlParts[4])
      || urlParts[5] !== materialProfile
      || urlParts[6] !== `${row.sha256}.${extension}`
      || !SHA256.test(row.sha256)
      || !Number.isSafeInteger(row.bytes)
      || row.bytes <= 0
      || !Number.isSafeInteger(row.width)
      || row.width <= 0
      || !Number.isSafeInteger(row.height)
      || row.height <= 0
      || !['image/png', 'image/ktx2'].includes(row.media_type)
      || (
        materialProfile === 'h2-png-1k-fallback'
        && row.media_type !== 'image/png'
      )
      || !TEXTURE_ROLES.has(row.role)
      || row.transfer !== expectedTransfer
      || typeof row.material_slot_id !== 'string'
      || row.material_slot_id.length === 0
    ) {
      throw new TypeError('verified mesh texture descriptor is invalid');
    }
    return {
      ...row,
      colour_space: row.transfer === 'srgb' ? 'srgb' : 'non-color',
      derivation_algorithm_id: 'verified-profile-runtime-v3',
      min_filter: 9987,
      mag_filter: 9729,
      wrap_s: 10497,
      wrap_t: 10497,
      material_profile: materialProfile,
      source_descriptor: row,
    };
  }
  if (
    typeof row?.url !== 'string'
    || !SHA256.test(row.sha256)
    || !Number.isSafeInteger(row.bytes)
    || row.bytes <= 0
    || !TEXTURE_ROLES.has(row.role)
    || !['srgb', 'non-color'].includes(row.colour_space)
    || typeof row.material_slot_id !== 'string'
    || row.material_slot_id.length === 0
    || typeof row.derivation_algorithm_id !== 'string'
    || row.derivation_algorithm_id.length === 0
    || row.min_filter !== 9987
    || row.mag_filter !== 9729
    || row.wrap_s !== 10497
    || row.wrap_t !== 10497
  ) {
    throw new TypeError('verified mesh texture descriptor is invalid');
  }
  return {
    ...row,
    media_type: 'image/png',
    transfer: row.colour_space === 'srgb' ? 'srgb' : 'linear',
    material_profile: materialProfile ?? 'legacy-png',
    source_descriptor: null,
  };
}

function validateDependencyClosure(
  dependencies,
  {
    materialProfile = null,
  } = {},
) {
  if (!Array.isArray(dependencies) || dependencies.length === 0) {
    throw new TypeError('verified mesh texture closure is absent');
  }
  const bySlot = new Map();
  const semanticKeys = new Set();
  for (const source of dependencies) {
    const row = normalizeDependency(source, materialProfile);
    const semanticKey = `${row.material_slot_id}:${row.role}`;
    if (semanticKeys.has(semanticKey)) {
      throw new TypeError('verified mesh texture closure is ambiguous');
    }
    semanticKeys.add(semanticKey);
    if (!bySlot.has(row.material_slot_id)) {
      bySlot.set(row.material_slot_id, new Map());
    }
    bySlot.get(row.material_slot_id).set(row.role, row);
  }
  for (const roles of bySlot.values()) {
    if (
      roles.size !== 3
      || [...TEXTURE_ROLES].some((role) => !roles.has(role))
    ) {
      throw new TypeError('verified mesh material texture closure is incomplete');
    }
  }
  return bySlot;
}

function validateTemplateDescriptor(descriptor, materialProfile = null) {
  if (
    typeof descriptor?.url !== 'string'
    || !SHA256.test(descriptor.glb_sha256)
    || !Number.isSafeInteger(descriptor.glb_bytes)
    || descriptor.glb_bytes <= 0
    || ![0, 1, 2].includes(descriptor.lod)
  ) {
    throw new TypeError('verified mesh template descriptor is invalid');
  }
  const dependencies = descriptor.texture_dependencies;
  if (dependencies === undefined) return;
  if (!Array.isArray(dependencies)) {
    throw new TypeError('verified mesh texture closure is invalid');
  }
  if (
    dependencies.some((row) => row?.media_type !== undefined)
    && descriptor.profile_id !== materialProfile
  ) {
    throw new TypeError('verified mesh material profile is inconsistent');
  }
  if (dependencies.length === 0) {
    if (descriptor.lod === 2) {
      throw new TypeError('verified mesh texture closure is absent');
    }
    return;
  }
  validateDependencyClosure(dependencies, { materialProfile });
}

function materialPlan(loaded, resources, bySlot, THREE) {
  if (resources.meshCount === 0) {
    throw new TypeError('verified mesh template contains no renderable mesh');
  }
  const plans = [];
  const consumedSlots = new Set();
  for (const material of resources.materials) {
    const slotId = material.userData?.slot_id;
    const roles = bySlot.get(slotId);
    if (!roles || consumedSlots.has(slotId)) {
      throw new TypeError('GLTF material closure is missing or ambiguous');
    }
    consumedSlots.add(slotId);
    if (
      [...FORBIDDEN_MATERIAL_TEXTURE_PROPERTIES].some(
        (property) => material[property] != null,
      )
      || material.map == null
      || material.normalMap == null
      || material.roughnessMap == null
      || material.metalnessMap == null
    ) {
      throw new TypeError('GLTF material texture closure is invalid');
    }
    const expectedUris = {
      base_color: expectedRelativeTextureUri(roles.get('base_color')),
      normal: expectedRelativeTextureUri(roles.get('normal')),
      orm: expectedRelativeTextureUri(roles.get('orm')),
    };
    if (
      textureUri(loaded, material.map) !== expectedUris.base_color
      || textureUri(loaded, material.normalMap) !== expectedUris.normal
      || textureUri(loaded, material.roughnessMap) !== expectedUris.orm
      || textureUri(loaded, material.metalnessMap) !== expectedUris.orm
      || (
        material.aoMap != null
        && textureUri(loaded, material.aoMap) !== expectedUris.orm
      )
    ) {
      throw new TypeError('GLTF material substituted a verified texture role');
    }
    let alphaMode;
    if (
      material.alphaTest === 0.45
      && material.side === THREE.DoubleSide
      && material.transparent === false
    ) {
      alphaMode = 'MASK';
    } else if (
      material.alphaTest === 0
      && material.transparent === false
    ) {
      alphaMode = 'OPAQUE';
    } else {
      throw new TypeError('GLTF material alpha contract is invalid');
    }
    plans.push({ material, roles, alphaMode });
  }
  if (
    consumedSlots.size !== bySlot.size
    || [...bySlot.keys()].some((slotId) => !consumedSlots.has(slotId))
  ) {
    throw new TypeError('GLTF material closure disagrees with runtime evidence');
  }
  return plans;
}

export function semanticTextureKey(
  dependency,
  {
    alphaMode,
    flipY,
  },
) {
  if (
    !['MASK', 'OPAQUE'].includes(alphaMode)
    || typeof flipY !== 'boolean'
  ) {
    throw new TypeError('semantic texture rendering state is invalid');
  }
  const parts = [
    dependency.sha256,
    dependency.role,
    dependency.colour_space,
    [
      dependency.min_filter,
      dependency.mag_filter,
      dependency.wrap_s,
      dependency.wrap_t,
    ].join(':'),
    String(flipY),
    alphaMode,
  ];
  if (
    dependency.material_profile
    && dependency.material_profile !== 'legacy-png'
  ) {
    parts.push(dependency.material_profile);
  }
  return parts.join(':');
}

export function createVerifiedProfileTextureStore({
  THREE,
  materialProfile,
  ktx2Loader = null,
  fetchFn = globalThis.fetch,
  cryptoSubtle = globalThis.crypto?.subtle,
  createImageBitmapFn = globalThis.createImageBitmap,
  BlobCtor = globalThis.Blob,
  locationHref = globalThis.location?.href,
  onProfileFailure = () => {},
} = {}) {
  const profiles = new Set([
    'h3-ai-ktx2-4k',
    'h2-png-1k-fallback',
  ]);
  if (
    !profiles.has(materialProfile)
    || typeof THREE?.Texture !== 'function'
    || typeof fetchFn !== 'function'
    || typeof cryptoSubtle?.digest !== 'function'
    || typeof locationHref !== 'string'
    || typeof onProfileFailure !== 'function'
    || (
      materialProfile === 'h3-ai-ktx2-4k'
      && typeof ktx2Loader?.parse !== 'function'
    )
  ) {
    throw new TypeError(
      'verified profile texture dependencies are unavailable',
    );
  }

  const byteObjects = new Map();
  const textures = new Map();
  const counters = {
    network_fetches: 0,
    ktx_transcodes: 0,
    png_bitmap_decodes: 0,
    gpu_texture_creations: 0,
  };
  let disposed = false;

  function validateDescriptor(descriptor) {
    const keys = new Set([
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
    const actualKeys = descriptor && typeof descriptor === 'object'
      ? Object.keys(descriptor)
      : [];
    const extension = descriptor?.media_type === 'image/ktx2'
      ? 'ktx2'
      : 'png';
    const parts = descriptor?.url?.split('/') ?? [];
    if (
      actualKeys.length !== keys.size
      || actualKeys.some((key) => !keys.has(key))
      || parts.length !== 7
      || parts.slice(0, 4).join('/') !== '/api/world/mesh-textures'
      || !SHA256.test(parts[4])
      || parts[5] !== materialProfile
      || parts[6] !== `${descriptor.sha256}.${extension}`
      || !SHA256.test(descriptor.sha256)
      || !Number.isSafeInteger(descriptor.bytes)
      || descriptor.bytes <= 0
      || !Number.isSafeInteger(descriptor.width)
      || descriptor.width <= 0
      || !Number.isSafeInteger(descriptor.height)
      || descriptor.height <= 0
      || !['image/ktx2', 'image/png'].includes(descriptor.media_type)
      || (
        materialProfile === 'h2-png-1k-fallback'
        && descriptor.media_type !== 'image/png'
      )
      || !TEXTURE_ROLES.has(descriptor.role)
      || descriptor.transfer !== (
        descriptor.role === 'base_color' ? 'srgb' : 'linear'
      )
      || typeof descriptor.material_slot_id !== 'string'
      || descriptor.material_slot_id.length === 0
    ) {
      throw new TypeError(
        'verified profile texture descriptor is invalid',
      );
    }
  }

  async function fetchExact(descriptor) {
    let record = byteObjects.get(descriptor.sha256);
    if (record) {
      if (
        record.url !== descriptor.url
        || record.bytes !== descriptor.bytes
        || record.mediaType !== descriptor.media_type
      ) {
        throw new TypeError(
          'verified profile byte descriptor disagrees',
        );
      }
      return record.promise;
    }
    record = {
      url: descriptor.url,
      bytes: descriptor.bytes,
      mediaType: descriptor.media_type,
      promise: null,
    };
    record.promise = (async () => {
      counters.network_fetches += 1;
      const response = await fetchFn(descriptor.url, {
        redirect: 'error',
        credentials: 'same-origin',
      });
      if (
        !response?.ok
        || response.status !== 200
        || response.redirected !== false
        || response.url
          !== new URL(descriptor.url, locationHref).href
        || contentType(response) !== descriptor.media_type
        || response.headers?.get?.('content-length')
          !== String(descriptor.bytes)
      ) {
        throw new Error('verified profile texture response disagrees');
      }
      const raw = await response.arrayBuffer();
      const bytes = new Uint8Array(raw);
      if (
        bytes.byteLength !== descriptor.bytes
        || bytesToHex(
          await cryptoSubtle.digest('SHA-256', raw),
        ) !== descriptor.sha256
      ) {
        throw new Error('verified profile texture identity disagrees');
      }
      return bytes;
    })();
    byteObjects.set(descriptor.sha256, record);
    record.promise.catch(() => {
      if (byteObjects.get(descriptor.sha256) === record) {
        byteObjects.delete(descriptor.sha256);
      }
    });
    return record.promise;
  }

  function keyFor(descriptor, rendering) {
    if (
      !['MASK', 'OPAQUE'].includes(rendering?.alphaMode)
      || typeof rendering.flipY !== 'boolean'
    ) {
      throw new TypeError(
        'verified profile texture rendering state is invalid',
      );
    }
    return [
      descriptor.sha256,
      descriptor.role,
      descriptor.transfer,
      '9987:9729:10497:10497',
      String(rendering.flipY),
      rendering.alphaMode,
      materialProfile,
    ].join(':');
  }

  function parseKtx2(bytes) {
    const buffer = arrayBufferFrom(bytes);
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        callback(value);
      };
      try {
        ktx2Loader.parse(
          buffer,
          (texture) => finish(resolve, texture),
          () => finish(
            reject,
            new Error('KTX2 profile texture failed'),
          ),
        );
      } catch {
        finish(
          reject,
          new Error('KTX2 profile texture failed'),
        );
      }
    });
  }

  function compressedMipBytes(texture) {
    let total = 0;
    const visit = (value) => {
      if (ArrayBuffer.isView(value)) {
        total += value.byteLength;
      } else if (Array.isArray(value)) {
        value.forEach(visit);
      } else if (value && typeof value === 'object') {
        if (Object.hasOwn(value, 'data')) visit(value.data);
        else if (Object.hasOwn(value, 'mipmaps')) visit(value.mipmaps);
      }
    };
    visit(texture?.mipmaps);
    return total;
  }

  async function createTexture(descriptor, rendering) {
    const bytes = await fetchExact(descriptor);
    let texture;
    let bitmap = null;
    let compressedBytes = 0;
    if (descriptor.media_type === 'image/ktx2') {
      counters.ktx_transcodes += 1;
      texture = await parseKtx2(bytes);
      compressedBytes = compressedMipBytes(texture);
    } else {
      if (
        typeof createImageBitmapFn !== 'function'
        || typeof BlobCtor !== 'function'
      ) {
        throw new Error('PNG profile texture failed');
      }
      counters.png_bitmap_decodes += 1;
      bitmap = await createImageBitmapFn(
        new BlobCtor([bytes], { type: 'image/png' }),
        {
          imageOrientation: rendering.flipY ? 'flipY' : 'none',
          colorSpaceConversion: 'none',
          premultiplyAlpha: 'none',
        },
      );
      texture = new THREE.Texture(bitmap);
    }
    if (!texture || typeof texture.dispose !== 'function') {
      bitmap?.close?.();
      throw new Error('profile texture produced no GPU resource');
    }
    texture.flipY = descriptor.media_type === 'image/ktx2'
      ? rendering.flipY
      : false;
    texture.colorSpace = descriptor.transfer === 'srgb'
      ? THREE.SRGBColorSpace
      : THREE.NoColorSpace;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.magFilter = THREE.LinearFilter;
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;
    texture.generateMipmaps = descriptor.media_type === 'image/png';
    texture.needsUpdate = true;
    counters.gpu_texture_creations += 1;
    return { texture, bitmap, compressedBytes };
  }

  async function acquire(descriptor, rendering) {
    if (disposed) {
      throw new Error('verified profile texture store is disposed');
    }
    validateDescriptor(descriptor);
    const key = keyFor(descriptor, rendering);
    let record = textures.get(key);
    if (!record) {
      record = {
        refs: 0,
        promise: null,
        texture: null,
        bitmap: null,
        compressedBytes: 0,
      };
      record.promise = createTexture(descriptor, rendering)
        .then((created) => {
          if (disposed) {
            created.texture.dispose();
            created.bitmap?.close?.();
            throw new Error(
              'verified profile texture store is disposed',
            );
          }
          Object.assign(record, created);
          return created.texture;
        })
        .catch(() => {
          if (textures.get(key) === record) textures.delete(key);
          if (
            !disposed
            && materialProfile === 'h3-ai-ktx2-4k'
          ) {
            try {
              onProfileFailure({ code: 'runtime_h3_failure' });
            } catch {
              // Failure reporting must not replace the fail-closed error.
            }
          }
          throw new Error(
            descriptor.media_type === 'image/ktx2'
              ? 'KTX2 profile texture failed'
              : 'PNG profile texture failed',
          );
        });
      textures.set(key, record);
    }
    const texture = await record.promise;
    record.refs += 1;
    return { key, texture };
  }

  function release(key) {
    const record = textures.get(key);
    if (!record || record.refs <= 0) return false;
    record.refs -= 1;
    if (record.refs === 0) {
      textures.delete(key);
      record.texture?.dispose();
      record.bitmap?.close?.();
    }
    return true;
  }

  function diagnostics() {
    let compressed_mip_bytes = 0;
    for (const record of textures.values()) {
      if (record.refs > 0) {
        compressed_mip_bytes += record.compressedBytes;
      }
    }
    return {
      active_textures: [...textures.values()].filter(
        (record) => record.refs > 0,
      ).length,
      network_fetches: counters.network_fetches,
      ktx_transcodes: counters.ktx_transcodes,
      png_bitmap_decodes: counters.png_bitmap_decodes,
      gpu_texture_creations: counters.gpu_texture_creations,
      compressed_mip_bytes,
    };
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    for (const record of textures.values()) {
      record.texture?.dispose();
      record.bitmap?.close?.();
      if (!record.texture) {
        record.promise.catch(() => {});
      }
    }
    textures.clear();
    byteObjects.clear();
  }

  return Object.freeze({
    acquire,
    release,
    diagnostics,
    dispose,
  });
}

export function createVerifiedMeshResourceStore({
  THREE,
  GLTFLoader,
  mergeGeometriesFn = null,
  fetchFn = globalThis.fetch,
  cryptoSubtle = globalThis.crypto?.subtle,
  createImageBitmapFn = globalThis.createImageBitmap,
  BlobCtor = globalThis.Blob,
  locationHref = globalThis.location?.href,
  createObjectURL = globalThis.URL?.createObjectURL?.bind(globalThis.URL),
  revokeObjectURL = globalThis.URL?.revokeObjectURL?.bind(globalThis.URL),
  maximumIdleTemplates = 36,
  materialProfile = null,
  profileTextureStore = null,
} = {}) {
  if (
    typeof THREE?.LoadingManager !== 'function'
    || typeof THREE?.Texture !== 'function'
    || typeof GLTFLoader !== 'function'
    || typeof fetchFn !== 'function'
    || typeof cryptoSubtle?.digest !== 'function'
    || typeof createImageBitmapFn !== 'function'
    || typeof BlobCtor !== 'function'
    || typeof locationHref !== 'string'
    || typeof createObjectURL !== 'function'
    || typeof revokeObjectURL !== 'function'
    || (mergeGeometriesFn !== null && typeof mergeGeometriesFn !== 'function')
    || !Number.isSafeInteger(maximumIdleTemplates)
    || maximumIdleTemplates < 0
    || (
      materialProfile !== null
      && !['h3-ai-ktx2-4k', 'h2-png-1k-fallback'].includes(
        materialProfile,
      )
    )
    || (
      materialProfile === 'h3-ai-ktx2-4k'
      && (
        typeof profileTextureStore?.acquire !== 'function'
        || typeof profileTextureStore?.release !== 'function'
      )
    )
  ) {
    throw new TypeError('verified mesh resource dependencies are unavailable');
  }

  const byteObjects = new Map();
  const decodedBitmaps = new Map();
  const gpuTextures = new Map();
  const templates = new Map();
  const idleTemplateKeys = [];
  const counters = {
    network_fetches: 0,
    bitmap_decodes: 0,
    gpu_texture_creations: 0,
  };
  let disposed = false;

  function deleteUnretainedByte(sha256) {
    const record = byteObjects.get(sha256);
    if (record?.refs === 0) byteObjects.delete(sha256);
  }

  async function fetchExactObject(descriptor, mimeType) {
    const identity = descriptorIdentity(descriptor, mimeType);
    if (
      typeof identity.url !== 'string'
      || !SHA256.test(identity.sha256)
      || !Number.isSafeInteger(identity.bytes)
      || identity.bytes <= 0
    ) {
      throw new TypeError('verified object descriptor is invalid');
    }
    const existing = byteObjects.get(identity.sha256);
    if (existing) {
      if (!descriptorAgrees(existing, identity)) {
        throw new TypeError(
          'content-addressed byte object descriptor disagrees',
        );
      }
      return existing.promise;
    }
    const record = {
      ...identity,
      refs: 0,
      promise: null,
    };
    record.promise = (async () => {
      counters.network_fetches += 1;
      const response = await fetchFn(identity.url, {
        redirect: 'error',
        credentials: 'same-origin',
      });
      if (!response?.ok) {
        throw new Error(`verified object fetch failed: ${response?.status}`);
      }
      if (response.redirected !== false) {
        throw new Error('verified object response redirected');
      }
      const expectedUrl = new URL(identity.url, locationHref).href;
      if (response.url !== expectedUrl) {
        throw new Error('verified object response URL changed');
      }
      if (contentType(response) !== mimeType) {
        throw new Error('verified object content type disagrees');
      }
      const raw = await response.arrayBuffer();
      const bytes = new Uint8Array(raw);
      if (bytes.byteLength !== identity.bytes) {
        throw new Error('verified object byte count disagrees');
      }
      const digest = bytesToHex(
        await cryptoSubtle.digest('SHA-256', raw),
      );
      if (digest !== identity.sha256) {
        throw new Error('verified object SHA-256 disagrees');
      }
      return bytes;
    })();
    byteObjects.set(identity.sha256, record);
    record.promise.catch(() => {
      if (byteObjects.get(identity.sha256) === record) {
        byteObjects.delete(identity.sha256);
      }
    });
    return record.promise;
  }

  function retainByte(sha256) {
    const record = byteObjects.get(sha256);
    if (!record) throw new Error('verified byte object disappeared');
    record.refs += 1;
  }

  function releaseByte(sha256) {
    const record = byteObjects.get(sha256);
    if (!record || record.refs <= 0) return false;
    record.refs -= 1;
    if (record.refs === 0) byteObjects.delete(sha256);
    return true;
  }

  function bitmapRecord(dependency) {
    const existing = decodedBitmaps.get(dependency.sha256);
    if (existing) return existing;
    const record = {
      refs: 0,
      bitmap: null,
      promise: null,
    };
    record.promise = (async () => {
      const bytes = await fetchExactObject(dependency, 'image/png');
      counters.bitmap_decodes += 1;
      const bitmap = await createImageBitmapFn(
        new BlobCtor([bytes], { type: 'image/png' }),
        {
          imageOrientation: 'flipY',
          colorSpaceConversion: 'none',
          premultiplyAlpha: 'none',
        },
      );
      record.bitmap = bitmap;
      retainByte(dependency.sha256);
      return bitmap;
    })();
    decodedBitmaps.set(dependency.sha256, record);
    record.promise.catch(() => {
      if (decodedBitmaps.get(dependency.sha256) === record) {
        decodedBitmaps.delete(dependency.sha256);
      }
      deleteUnretainedByte(dependency.sha256);
    });
    return record;
  }

  function releaseBitmap(sha256) {
    const record = decodedBitmaps.get(sha256);
    if (!record || record.refs <= 0) return false;
    record.refs -= 1;
    if (record.refs === 0) {
      decodedBitmaps.delete(sha256);
      if (record.bitmap) {
        record.bitmap.close?.();
      } else {
        record.promise.then((bitmap) => bitmap.close?.());
      }
      releaseByte(sha256);
    }
    return true;
  }

  function discardUnusedBitmap(sha256, record) {
    if (
      record.refs !== 0
      || decodedBitmaps.get(sha256) !== record
    ) {
      return;
    }
    decodedBitmaps.delete(sha256);
    record.bitmap?.close?.();
    if (!releaseByte(sha256)) deleteUnretainedByte(sha256);
  }

  async function acquireGpuTexture(dependency, rendering) {
    if (dependency.media_type === 'image/ktx2') {
      if (
        materialProfile !== 'h3-ai-ktx2-4k'
        || dependency.source_descriptor == null
      ) {
        throw new TypeError('verified KTX2 texture route is unavailable');
      }
      const acquired = await profileTextureStore.acquire(
        dependency.source_descriptor,
        rendering,
      );
      return {
        key: `profile:${acquired.key}`,
        texture: acquired.texture,
      };
    }
    await fetchExactObject(dependency, 'image/png');
    const key = semanticTextureKey(dependency, rendering);
    let record = gpuTextures.get(key);
    if (!record) {
      record = {
        refs: 0,
        sha256: dependency.sha256,
        texture: null,
        promise: null,
      };
      record.promise = (async () => {
        const bitmapState = bitmapRecord(dependency);
        const bitmap = await bitmapState.promise;
        try {
          const texture = new THREE.Texture(bitmap);
          texture.flipY = rendering.flipY;
          texture.colorSpace = dependency.colour_space === 'srgb'
            ? THREE.SRGBColorSpace
            : THREE.NoColorSpace;
          texture.minFilter = THREE.LinearMipmapLinearFilter;
          texture.magFilter = THREE.LinearFilter;
          texture.wrapS = THREE.RepeatWrapping;
          texture.wrapT = THREE.RepeatWrapping;
          texture.generateMipmaps = true;
          texture.needsUpdate = true;
          record.texture = texture;
          bitmapState.refs += 1;
          counters.gpu_texture_creations += 1;
          return texture;
        } catch (error) {
          discardUnusedBitmap(dependency.sha256, bitmapState);
          throw error;
        }
      })();
      gpuTextures.set(key, record);
      record.promise.catch(() => {
        if (gpuTextures.get(key) === record) gpuTextures.delete(key);
      });
    }
    const texture = await record.promise;
    record.refs += 1;
    return { key, texture };
  }

  function releaseGpuTexture(key) {
    if (key.startsWith('profile:')) {
      return profileTextureStore.release(key.slice('profile:'.length));
    }
    const record = gpuTextures.get(key);
    if (!record || record.refs <= 0) return false;
    record.refs -= 1;
    if (record.refs === 0) {
      gpuTextures.delete(key);
      if (record.texture) {
        record.texture.dispose();
      } else {
        record.promise.then((texture) => texture.dispose());
      }
      releaseBitmap(record.sha256);
    }
    return true;
  }

  async function buildEmbeddedTemplate(descriptor) {
    const glbDescriptor = {
      url: descriptor.url,
      sha256: descriptor.glb_sha256,
      bytes: descriptor.glb_bytes,
    };
    let parsedResources = null;
    let retainedGlb = false;
    try {
      const glbBytes = await fetchExactObject(
        glbDescriptor,
        'model/gltf-binary',
      );
      assertEmbeddedGlb(glbBytes);
      const manager = new THREE.LoadingManager();
      manager.setURLModifier((url) => {
        if (!url.startsWith('blob:')) {
          throw new TypeError(
            'embedded mesh GLB requested an external resource',
          );
        }
        return url;
      });
      const loader = new GLTFLoader(manager);
      const loaded = await loader.parseAsync(arrayBufferFrom(glbBytes), '');
      parsedResources = collectParsedResources(loaded);
      if (parsedResources.meshCount === 0) {
        throw new TypeError(
          'verified mesh template contains no renderable mesh',
        );
      }
      loaded.scene.traverse((object) => {
        if (!object.isMesh) return;
        object.castShadow = false;
        object.receiveShadow = true;
      });
      loaded.scene.updateMatrixWorld(true);
      retainByte(descriptor.glb_sha256);
      retainedGlb = true;
      return {
        scene: loaded.scene,
        parsedResources,
        gpuKeys: new Set(),
        glbSha256: descriptor.glb_sha256,
        ownsTransientTextures: true,
      };
    } catch (error) {
      if (parsedResources) disposeParsedResources(parsedResources);
      if (retainedGlb) releaseByte(descriptor.glb_sha256);
      throw error;
    } finally {
      deleteUnretainedByte(descriptor.glb_sha256);
    }
  }

  async function buildTemplate(descriptor) {
    const dependencies = descriptor.texture_dependencies;
    if (dependencies === undefined) {
      return buildEmbeddedTemplate(descriptor);
    }
    if (Array.isArray(dependencies) && dependencies.length === 0) {
      if (descriptor.lod === 2) {
        throw new TypeError('verified mesh texture closure is absent');
      }
      return buildEmbeddedTemplate(descriptor);
    }
    const normalizedDependencies = dependencies.map(
      (row) => normalizeDependency(row, materialProfile),
    );
    const bySlot = validateDependencyClosure(
      dependencies,
      { materialProfile },
    );
    const pngDependencies = normalizedDependencies.filter(
      (row) => row.media_type === 'image/png',
    );
    const glbDescriptor = {
      url: descriptor.url,
      sha256: descriptor.glb_sha256,
      bytes: descriptor.glb_bytes,
    };
    const dependencyBytes = new Map();
    const fetched = await Promise.all([
      fetchExactObject(glbDescriptor, 'model/gltf-binary'),
      ...pngDependencies.map(
        (row) => fetchExactObject(row, 'image/png'),
      ),
    ]);
    const [glbBytes, ...textureBytes] = fetched;
    pngDependencies.forEach((row, index) => {
      dependencyBytes.set(row.sha256, textureBytes[index]);
    });
    const objectUrls = new Map();
    const uriToObjectUrl = new Map();
    let loaded = null;
    let parsedResources = null;
    const acquiredGpuKeys = new Set();
    let retainedGlb = false;
    try {
      for (const row of normalizedDependencies) {
        if (
          row.media_type === 'image/png'
          && !objectUrls.has(row.sha256)
        ) {
          objectUrls.set(
            row.sha256,
            createObjectURL(
              new BlobCtor(
                [dependencyBytes.get(row.sha256)],
                { type: 'image/png' },
              ),
            ),
          );
        }
        uriToObjectUrl.set(
          expectedRelativeTextureUri(row),
          row.media_type === 'image/ktx2'
            ? `ktx2-placeholder:${row.sha256}`
            : objectUrls.get(row.sha256),
        );
      }
      const requestedUris = new Set();
      const manager = new THREE.LoadingManager();
      manager.setURLModifier((url) => {
        if (!uriToObjectUrl.has(url)) {
          throw new TypeError(
            'GLTF requested a texture outside its verified closure',
          );
        }
        requestedUris.add(url);
        return uriToObjectUrl.get(url);
      });
      const loader = new GLTFLoader(manager);
      if (
        normalizedDependencies.some(
          (row) => row.media_type === 'image/ktx2',
        )
      ) {
        if (typeof loader.setKTX2Loader !== 'function') {
          throw new TypeError('GLTF KTX2 placeholder route is unavailable');
        }
        loader.setKTX2Loader({
          load(_url, onLoad, _onProgress, onError) {
            try {
              const texture = new THREE.Texture();
              texture.needsUpdate = false;
              onLoad(texture);
              return texture;
            } catch {
              onError?.(
                new TypeError('GLTF KTX2 placeholder creation failed'),
              );
              return null;
            }
          },
        });
      }
      loaded = await loader.parseAsync(arrayBufferFrom(glbBytes), '');
      if (
        requestedUris.size !== uriToObjectUrl.size
        || [...uriToObjectUrl.keys()].some((uri) => !requestedUris.has(uri))
      ) {
        throw new TypeError('GLTF texture closure was not consumed exactly');
      }
      parsedResources = collectParsedResources(loaded);
      const plans = materialPlan(loaded, parsedResources, bySlot, THREE);
      const textureByKey = new Map();
      for (const plan of plans) {
        for (const row of plan.roles.values()) {
          const key = semanticTextureKey(row, {
            alphaMode: plan.alphaMode,
            flipY: false,
          });
          if (!textureByKey.has(key)) {
            const acquired = await acquireGpuTexture(row, {
              alphaMode: plan.alphaMode,
              flipY: false,
            });
            acquiredGpuKeys.add(acquired.key);
            textureByKey.set(key, acquired.texture);
          }
        }
      }
      for (const plan of plans) {
        const texture = (role) => textureByKey.get(
          semanticTextureKey(plan.roles.get(role), {
            alphaMode: plan.alphaMode,
            flipY: false,
          }),
        );
        plan.material.map = texture('base_color');
        plan.material.normalMap = texture('normal');
        plan.material.roughnessMap = texture('orm');
        plan.material.metalnessMap = texture('orm');
        if (plan.material.aoMap != null) {
          plan.material.aoMap = texture('orm');
        }
        plan.material.needsUpdate = true;
        if (
          plan.material.map == null
          || plan.material.normalMap == null
          || plan.material.roughnessMap == null
          || plan.material.metalnessMap == null
          || plan.material.transparent !== false
          || (
            plan.alphaMode === 'MASK'
            && (
              plan.material.alphaTest !== 0.45
              || plan.material.side !== THREE.DoubleSide
            )
          )
        ) {
          throw new TypeError('GLTF material rebinding failed');
        }
      }
      if (mergeGeometriesFn) {
        const compacted = compactTemplateSceneByMaterial({
          scene: loaded.scene,
          THREE,
          mergeGeometriesFn,
        });
        for (const geometry of compacted.sourceGeometries) geometry.dispose();
        parsedResources.geometries = compacted.geometries;
        parsedResources.meshCount = compacted.geometries.size;
        loaded.scene = compacted.scene;
      }
      disposeTransientTextures(parsedResources);
      loaded.scene.traverse((object) => {
        if (!object.isMesh) return;
        object.castShadow = false;
        object.receiveShadow = true;
      });
      loaded.scene.updateMatrixWorld(true);
      retainByte(descriptor.glb_sha256);
      retainedGlb = true;
      return {
        scene: loaded.scene,
        parsedResources,
        gpuKeys: acquiredGpuKeys,
        glbSha256: descriptor.glb_sha256,
        ownsTransientTextures: false,
      };
    } catch (error) {
      if (parsedResources) disposeParsedResources(parsedResources);
      for (const key of acquiredGpuKeys) releaseGpuTexture(key);
      if (retainedGlb) releaseByte(descriptor.glb_sha256);
      throw error;
    } finally {
      for (const url of objectUrls.values()) revokeObjectURL(url);
      deleteUnretainedByte(descriptor.glb_sha256);
      for (const row of pngDependencies) {
        deleteUnretainedByte(row.sha256);
      }
    }
  }

  function disposeTemplate(template) {
    for (const key of template.gpuKeys) releaseGpuTexture(key);
    if (template.ownsTransientTextures) {
      for (const texture of template.parsedResources.transientTextures) {
        disposeParsedTexture(texture);
      }
    }
    template.parsedResources.transientTextures.clear();
    for (const material of template.parsedResources.materials) {
      material.dispose?.();
    }
    for (const geometry of template.parsedResources.geometries) {
      geometry.dispose?.();
    }
    releaseByte(template.glbSha256);
  }

  function disposeTemplateRecord(record) {
    if (record.disposalScheduled) return;
    record.disposalScheduled = true;
    if (record.template) {
      disposeTemplate(record.template);
      return;
    }
    record.promise
      .then((template) => {
        record.template = template;
        disposeTemplate(template);
      })
      .catch(() => {});
  }

  async function loadTemplate(descriptor) {
    if (disposed) {
      throw new Error('verified mesh resource store is disposed');
    }
    validateTemplateDescriptor(descriptor, materialProfile);
    const key = templateKey(descriptor);
    let record = templates.get(key);
    if (!record) {
      record = {
        refs: 0,
        promise: null,
        disposalScheduled: false,
      };
      record.promise = buildTemplate(descriptor);
      templates.set(key, record);
      record.promise.catch(() => {
        if (templates.get(key) === record) templates.delete(key);
      });
    }
    const template = await record.promise;
    record.template = template;
    if (disposed) {
      disposeTemplateRecord(record);
      throw new Error('verified mesh resource store is disposed');
    }
    if (record.refs === 0) {
      const idleIndex = idleTemplateKeys.indexOf(key);
      if (idleIndex >= 0) idleTemplateKeys.splice(idleIndex, 1);
    }
    record.refs += 1;
    return template.scene;
  }

  function releaseTemplate(descriptor) {
    if (disposed) return false;
    const key = templateKey(descriptor);
    const record = templates.get(key);
    if (!record || record.refs <= 0) return false;
    record.refs -= 1;
    if (record.refs === 0) {
      idleTemplateKeys.push(key);
      while (idleTemplateKeys.length > maximumIdleTemplates) {
        const evictedKey = idleTemplateKeys.shift();
        const evicted = templates.get(evictedKey);
        if (!evicted || evicted.refs !== 0) continue;
        templates.delete(evictedKey);
        disposeTemplateRecord(evicted);
      }
    }
    return true;
  }

  function diagnostics() {
    return {
      byte_objects: byteObjects.size,
      decoded_bitmaps: decodedBitmaps.size,
      gpu_textures: gpuTextures.size,
      templates: templates.size,
      network_fetches: counters.network_fetches,
      bitmap_decodes: counters.bitmap_decodes,
      gpu_texture_creations: counters.gpu_texture_creations,
    };
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    for (const record of templates.values()) {
      disposeTemplateRecord(record);
    }
    templates.clear();
    idleTemplateKeys.length = 0;
  }

  return {
    loadTemplate,
    releaseTemplate,
    diagnostics,
    dispose,
  };
}
