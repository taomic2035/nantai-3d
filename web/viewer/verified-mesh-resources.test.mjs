import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import test from 'node:test';

let resourcesModule;
try {
  resourcesModule = await import('./verified-mesh-resources.mjs');
} catch (error) {
  resourcesModule = { __loadError: error };
}

function subject() {
  assert.equal(
    resourcesModule.__loadError,
    undefined,
    `verified-mesh-resources.mjs must load: ${
      resourcesModule.__loadError?.message
    }`,
  );
  return resourcesModule;
}

test('near template compaction merges transformed primitives by material', () => {
  const { compactTemplateSceneByMaterial } = subject();
  const disposed = [];
  const transformed = [];
  const materialA = { id: 'a' };
  const materialB = { id: 'b' };
  function geometry(id) {
    return {
      id,
      clone() {
        return {
          id: `${id}-clone`,
          applyMatrix4(matrix) {
            transformed.push([id, matrix.id]);
          },
          dispose() {
            disposed.push(`${id}-clone`);
          },
        };
      },
    };
  }
  const sourceGeometries = [geometry('g1'), geometry('g2'), geometry('g3')];
  const sourceMeshes = [
    { isMesh: true, geometry: sourceGeometries[0], material: materialA, matrixWorld: { id: 'm1' } },
    { isMesh: true, geometry: sourceGeometries[1], material: materialA, matrixWorld: { id: 'm2' } },
    { isMesh: true, geometry: sourceGeometries[2], material: materialB, matrixWorld: { id: 'm3' } },
  ];
  const scene = {
    name: 'near-template',
    updateMatrixWorldCalls: 0,
    updateMatrixWorld() {
      this.updateMatrixWorldCalls += 1;
    },
    traverse(visitor) {
      sourceMeshes.forEach(visitor);
    },
  };
  class Group {
    constructor() {
      this.children = [];
    }

    add(child) {
      this.children.push(child);
    }
  }
  class Mesh {
    constructor(meshGeometry, material) {
      this.geometry = meshGeometry;
      this.material = material;
      this.isMesh = true;
    }
  }
  const mergeCalls = [];
  const mergeGeometriesFn = (geometries) => {
    mergeCalls.push(geometries.map((item) => item.id));
    return {
      id: `merged-${mergeCalls.length}`,
      dispose() {
        disposed.push(this.id);
      },
    };
  };

  const compacted = compactTemplateSceneByMaterial({
    scene,
    THREE: { Group, Mesh },
    mergeGeometriesFn,
  });

  assert.equal(scene.updateMatrixWorldCalls, 1);
  assert.deepEqual(transformed, [
    ['g1', 'm1'],
    ['g2', 'm2'],
    ['g3', 'm3'],
  ]);
  assert.deepEqual(mergeCalls, [['g1-clone', 'g2-clone']]);
  assert.deepEqual(disposed, ['g1-clone', 'g2-clone']);
  assert.equal(compacted.scene.name, 'near-template');
  assert.equal(compacted.scene.children.length, 2);
  assert.equal(compacted.scene.children[0].material, materialA);
  assert.equal(compacted.scene.children[0].geometry.id, 'merged-1');
  assert.equal(compacted.scene.children[1].material, materialB);
  assert.equal(compacted.scene.children[1].geometry.id, 'g3-clone');
  assert.deepEqual(
    [...compacted.sourceGeometries].map((item) => item.id),
    ['g1', 'g2', 'g3'],
  );
  assert.deepEqual(
    [...compacted.geometries].map((item) => item.id),
    ['merged-1', 'g3-clone'],
  );
});

test('near template compaction fails closed on unsupported mesh forms', () => {
  const { compactTemplateSceneByMaterial } = subject();
  const scene = {
    updateMatrixWorld() {},
    traverse(visitor) {
      visitor({
        isMesh: true,
        isSkinnedMesh: true,
        geometry: { clone() {} },
        material: {},
        matrixWorld: {},
      });
    },
  };
  assert.throws(
    () => compactTemplateSceneByMaterial({
      scene,
      THREE: { Group: class {}, Mesh: class {} },
      mergeGeometriesFn() {},
    }),
    /cannot compact skinned, instanced, morphed, or multi-material mesh/,
  );
});

const ORIGIN = 'https://viewer.test/web/viewer/';
const BUNDLE_ID = '1'.repeat(64);
const SLOT_ID = 'material-fieldstone-01';

function embeddedGlbBytes() {
  const json = new TextEncoder().encode(JSON.stringify({
    asset: { version: '2.0' },
    buffers: [{ byteLength: 4 }],
    bufferViews: [{
      buffer: 0,
      byteOffset: 0,
      byteLength: 4,
    }],
    images: [{
      bufferView: 0,
      mimeType: 'image/png',
    }],
    textures: [{ source: 0 }],
  }));
  const paddedJsonLength = Math.ceil(json.byteLength / 4) * 4;
  const totalLength = 12 + 8 + paddedJsonLength + 8 + 4;
  const bytes = new Uint8Array(totalLength);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, totalLength, true);
  view.setUint32(12, paddedJsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  bytes.fill(0x20, 20, 20 + paddedJsonLength);
  bytes.set(json, 20);
  const binOffset = 20 + paddedJsonLength;
  view.setUint32(binOffset, 4, true);
  view.setUint32(binOffset + 4, 0x004e4942, true);
  return bytes;
}

const GLB_BYTES = embeddedGlbBytes();
const BASE_BYTES = new TextEncoder().encode('verified-base-png');
const NORMAL_BYTES = new TextEncoder().encode('verified-normal-png');
const ORM_BYTES = new TextEncoder().encode('verified-orm-png');

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function dependency(role, bytes, overrides = {}) {
  const digest = sha256(bytes);
  return {
    url: `/api/world/mesh-assets/${BUNDLE_ID}/textures/${digest}.png`,
    sha256: digest,
    bytes: bytes.byteLength,
    role,
    colour_space: role === 'base_color' ? 'srgb' : 'non-color',
    material_slot_id: SLOT_ID,
    derivation_algorithm_id: 'pytest-near-map-v2',
    min_filter: 9987,
    mag_filter: 9729,
    wrap_s: 10497,
    wrap_t: 10497,
    ...overrides,
  };
}

function assetDescriptor({
  dependencies = [
    dependency('base_color', BASE_BYTES),
    dependency('normal', NORMAL_BYTES),
    dependency('orm', ORM_BYTES),
  ],
  ...overrides
} = {}) {
  const digest = sha256(GLB_BYTES);
  return {
    asset_id: 'house_stone_01',
    lod: 2,
    url: `/api/world/mesh-assets/${BUNDLE_ID}/house_stone_01/lod2.glb`,
    glb_sha256: digest,
    glb_bytes: GLB_BYTES.byteLength,
    texture_dependencies: dependencies,
    ...overrides,
  };
}

class FakeLoadingManager {
  setURLModifier(modifier) {
    this.modifier = modifier;
    return this;
  }

  resolveURL(url) {
    return this.modifier ? this.modifier(url) : url;
  }
}

class FakeTexture {
  constructor(image) {
    this.image = image;
    this.disposals = 0;
    this.needsUpdate = false;
  }

  dispose() {
    this.disposals += 1;
  }
}

function fakeThree() {
  return {
    LoadingManager: FakeLoadingManager,
    Texture: FakeTexture,
    SRGBColorSpace: 'srgb',
    NoColorSpace: 'none',
    LinearMipmapLinearFilter: 'linear-mipmap-linear',
    LinearFilter: 'linear',
    RepeatWrapping: 'repeat',
    DoubleSide: 'double',
    FrontSide: 'front',
  };
}

function response(bytes, descriptor, {
  contentType,
  redirected = false,
  finalUrl = new URL(descriptor.url, ORIGIN).href,
} = {}) {
  return {
    ok: true,
    status: 200,
    redirected,
    url: finalUrl,
    headers: {
      get(name) {
        return name.toLowerCase() === 'content-type'
          ? contentType
          : null;
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

function createSceneFixture(THREE, dependencies, {
  missingRole = null,
  extraMap = false,
  substitutedRole = null,
  foliage = false,
} = {}) {
  const transientByRole = new Map(
    dependencies.map((row) => [row.role, new FakeTexture(`transient:${row.role}`)]),
  );
  const images = dependencies.map((row) => ({
    uri: `../textures/${row.sha256}.png`,
  }));
  const textures = dependencies.map((row, index) => ({ source: index }));
  const associations = new Map(
    dependencies.map((row, index) => [
      transientByRole.get(row.role),
      { textures: index },
    ]),
  );
  const transient = (role) => (
    role === missingRole ? null : transientByRole.get(role)
  );
  const material = {
    userData: { slot_id: dependencies[0].material_slot_id },
    map: transient('base_color'),
    normalMap: transient('normal'),
    roughnessMap: transient('orm'),
    metalnessMap: transient('orm'),
    aoMap: transient('orm'),
    emissiveMap: extraMap ? new FakeTexture('transient:emissive') : null,
    alphaMap: null,
    bumpMap: null,
    displacementMap: null,
    lightMap: null,
    envMap: null,
    alphaTest: foliage ? 0.45 : 0,
    side: foliage ? THREE.DoubleSide : THREE.FrontSide,
    transparent: false,
    needsUpdate: false,
    disposals: 0,
    dispose() {
      this.disposals += 1;
    },
  };
  if (substitutedRole) {
    const source = substitutedRole === 'base_color' ? 'normal' : 'base_color';
    material[
      substitutedRole === 'base_color' ? 'map' : `${substitutedRole}Map`
    ] = transientByRole.get(source);
  }
  const geometry = {
    disposals: 0,
    dispose() {
      this.disposals += 1;
    },
  };
  const mesh = {
    isMesh: true,
    material,
    geometry,
    castShadow: true,
    receiveShadow: false,
  };
  const scene = {
    mesh,
    traverse(visitor) {
      visitor(mesh);
    },
    updateMatrixWorld() {},
  };
  return {
    scene,
    material,
    geometry,
    transientTextures: [
      ...transientByRole.values(),
      ...(material.emissiveMap ? [material.emissiveMap] : []),
    ],
    parser: {
      associations,
      json: { images, textures },
    },
  };
}

function createEmbeddedSceneFixture() {
  const embeddedBitmap = {
    closes: 0,
    close() {
      this.closes += 1;
    },
  };
  const map = new FakeTexture(embeddedBitmap);
  const material = {
    map,
    normalMap: null,
    roughnessMap: null,
    metalnessMap: null,
    disposals: 0,
    dispose() {
      this.disposals += 1;
    },
  };
  const geometry = {
    disposals: 0,
    dispose() {
      this.disposals += 1;
    },
  };
  const mesh = {
    isMesh: true,
    material,
    geometry,
    castShadow: true,
    receiveShadow: false,
  };
  const scene = {
    traverse(visitor) {
      visitor(mesh);
    },
    updateMatrixWorld() {},
  };
  return {
    scene,
    material,
    geometry,
    embeddedBitmap,
    transientTextures: [map],
    parser: {
      associations: new Map([[map, { textures: 0 }]]),
      json: {
        images: [{ bufferView: 0, mimeType: 'image/png' }],
        textures: [{ source: 0 }],
      },
    },
  };
}

function harness({
  descriptor = assetDescriptor(),
  sceneOptions,
  mutateResponse,
  maximumIdleTemplates = 0,
} = {}) {
  const THREE = fakeThree();
  const counters = {
    fetches: 0,
    parses: 0,
    bitmapDecodes: 0,
    objectUrls: 0,
    revokedUrls: 0,
  };
  const payloads = new Map([
    [descriptor.url, {
      bytes: GLB_BYTES,
      contentType: 'model/gltf-binary',
      descriptor: {
        url: descriptor.url,
        sha256: descriptor.glb_sha256,
        bytes: descriptor.glb_bytes,
      },
    }],
    ...(descriptor.texture_dependencies ?? []).map((row) => {
      const bytes = [
        BASE_BYTES,
        NORMAL_BYTES,
        ORM_BYTES,
      ].find((candidate) => sha256(candidate) === row.sha256)
        ?? (
          row.role === 'base_color'
            ? BASE_BYTES
            : row.role === 'normal'
              ? NORMAL_BYTES
              : ORM_BYTES
        );
      return [
        row.url,
        {
          bytes,
        contentType: 'image/png',
        descriptor: row,
        },
      ];
    }),
  ]);
  let lastFixture = null;
  class FakeGLTFLoader {
    constructor(manager) {
      this.manager = manager;
    }

    async parseAsync() {
      counters.parses += 1;
      for (const row of descriptor.texture_dependencies ?? []) {
        this.manager.resolveURL(`../textures/${row.sha256}.png`);
      }
      lastFixture = descriptor.texture_dependencies?.length
        ? createSceneFixture(
          THREE,
          descriptor.texture_dependencies,
          sceneOptions,
        )
        : createEmbeddedSceneFixture();
      return {
        scene: lastFixture.scene,
        parser: lastFixture.parser,
      };
    }
  }
  const fetchFn = async (url) => {
    counters.fetches += 1;
    const record = payloads.get(url);
    if (!record) throw new Error(`unexpected fetch: ${url}`);
    const base = response(
      record.bytes,
      record.descriptor,
      { contentType: record.contentType },
    );
    return mutateResponse
      ? mutateResponse(base, url, record)
      : base;
  };
  const bitmaps = [];
  const createImageBitmapFn = async () => {
    counters.bitmapDecodes += 1;
    const bitmap = {
      closes: 0,
      close() {
        this.closes += 1;
      },
    };
    bitmaps.push(bitmap);
    return bitmap;
  };
  const objectUrlBlobs = new Map();
  const createObjectURL = (blob) => {
    const url = `blob:verified-${counters.objectUrls}`;
    counters.objectUrls += 1;
    objectUrlBlobs.set(url, blob);
    return url;
  };
  const revokeObjectURL = (url) => {
    counters.revokedUrls += 1;
    objectUrlBlobs.delete(url);
  };
  const { createVerifiedMeshResourceStore } = subject();
  const store = createVerifiedMeshResourceStore({
    THREE,
    GLTFLoader: FakeGLTFLoader,
    fetchFn,
    cryptoSubtle: webcrypto.subtle,
    createImageBitmapFn,
    BlobCtor: Blob,
    locationHref: ORIGIN,
    createObjectURL,
    revokeObjectURL,
    maximumIdleTemplates,
  });
  return {
    THREE,
    descriptor,
    store,
    counters,
    bitmaps,
    get lastFixture() {
      return lastFixture;
    },
  };
}

test('semantic texture identity includes hash role colour sampler flip and alpha', () => {
  const { semanticTextureKey } = subject();
  const row = dependency('base_color', BASE_BYTES);

  assert.equal(
    semanticTextureKey(row, { alphaMode: 'MASK', flipY: false }),
    [
      row.sha256,
      row.role,
      row.colour_space,
      '9987:9729:10497:10497',
      'false',
      'MASK',
    ].join(':'),
  );
  assert.notEqual(
    semanticTextureKey(row, { alphaMode: 'OPAQUE', flipY: false }),
    semanticTextureKey(row, { alphaMode: 'MASK', flipY: false }),
  );
});

test('store verifies once, shares templates and semantic resources, then releases at zero', async () => {
  const setup = harness();

  const [first, second] = await Promise.all([
    setup.store.loadTemplate(setup.descriptor),
    setup.store.loadTemplate(setup.descriptor),
  ]);

  assert.equal(first, second);
  assert.equal(setup.counters.fetches, 4);
  assert.equal(setup.counters.parses, 1);
  assert.equal(setup.counters.bitmapDecodes, 3);
  assert.equal(setup.counters.objectUrls, 3);
  assert.equal(setup.counters.revokedUrls, 3);
  assert.equal(setup.lastFixture.material.map instanceof FakeTexture, true);
  assert.equal(setup.lastFixture.material.normalMap instanceof FakeTexture, true);
  assert.equal(setup.lastFixture.material.roughnessMap,
    setup.lastFixture.material.metalnessMap);
  assert.equal(setup.lastFixture.material.roughnessMap,
    setup.lastFixture.material.aoMap);
  assert.equal(setup.lastFixture.material.needsUpdate, true);
  assert.equal(
    setup.lastFixture.transientTextures.every((row) => row.disposals === 1),
    true,
  );
  assert.deepEqual(setup.store.diagnostics(), {
    byte_objects: 4,
    decoded_bitmaps: 3,
    gpu_textures: 3,
    templates: 1,
    network_fetches: 4,
    bitmap_decodes: 3,
    gpu_texture_creations: 3,
  });

  assert.equal(setup.store.releaseTemplate(setup.descriptor), true);
  assert.equal(setup.store.diagnostics().templates, 1);
  assert.equal(setup.store.releaseTemplate(setup.descriptor), true);
  assert.equal(setup.store.releaseTemplate(setup.descriptor), false);
  assert.deepEqual(setup.store.diagnostics(), {
    byte_objects: 0,
    decoded_bitmaps: 0,
    gpu_textures: 0,
    templates: 0,
    network_fetches: 4,
    bitmap_decodes: 3,
    gpu_texture_creations: 3,
  });
  assert.equal(setup.lastFixture.geometry.disposals, 1);
  assert.equal(setup.lastFixture.material.disposals, 1);
  assert.equal(setup.bitmaps.every((row) => row.closes === 1), true);
});

test('one bitmap can back distinct role-specific GPU textures', async () => {
  const sharedBytes = NORMAL_BYTES;
  const setup = harness({
    descriptor: assetDescriptor({
      dependencies: [
        dependency('base_color', BASE_BYTES),
        dependency('normal', sharedBytes),
        dependency('orm', sharedBytes),
      ],
    }),
  });

  await setup.store.loadTemplate(setup.descriptor);

  assert.equal(setup.counters.fetches, 3);
  assert.equal(setup.counters.bitmapDecodes, 2);
  assert.equal(setup.store.diagnostics().gpu_textures, 3);
});

test('bounded idle templates reuse content across runtime names and routes', async () => {
  const setup = harness({ maximumIdleTemplates: 36 });
  const first = await setup.store.loadTemplate(setup.descriptor);
  assert.equal(setup.store.releaseTemplate(setup.descriptor), true);
  assert.equal(setup.store.diagnostics().templates, 1);

  const alias = {
    ...setup.descriptor,
    asset_id: 'house_stone_alias_01',
    lod: 1,
    url: `/api/world/mesh-assets/${BUNDLE_ID}/house_stone_alias_01/lod1.glb`,
  };
  const second = await setup.store.loadTemplate(alias);

  assert.equal(second, first);
  assert.equal(setup.counters.fetches, 4);
  assert.equal(setup.counters.parses, 1);
  assert.equal(setup.counters.bitmapDecodes, 3);
  assert.equal(setup.store.releaseTemplate(alias), true);
  assert.equal(setup.store.diagnostics().templates, 1);
});

for (const [label, descriptor] of [
  [
    'v1 descriptor without a dependency field',
    (() => {
      const row = assetDescriptor({ lod: 2 });
      delete row.texture_dependencies;
      return row;
    })(),
  ],
  [
    'v2 LOD0 descriptor with an explicit empty dependency closure',
    assetDescriptor({ lod: 0, dependencies: [] }),
  ],
]) {
  test(`store retains and releases embedded resources for ${label}`, async () => {
    const setup = harness({ descriptor });

    await setup.store.loadTemplate(descriptor);

    assert.equal(setup.counters.fetches, 1);
    assert.equal(setup.counters.parses, 1);
    assert.equal(setup.counters.bitmapDecodes, 0);
    assert.deepEqual(setup.store.diagnostics(), {
      byte_objects: 1,
      decoded_bitmaps: 0,
      gpu_textures: 0,
      templates: 1,
      network_fetches: 1,
      bitmap_decodes: 0,
      gpu_texture_creations: 0,
    });
    assert.equal(setup.lastFixture.transientTextures[0].disposals, 0);

    assert.equal(setup.store.releaseTemplate(descriptor), true);
    assert.equal(setup.lastFixture.transientTextures[0].disposals, 1);
    assert.equal(setup.lastFixture.embeddedBitmap.closes, 1);
    assert.equal(setup.lastFixture.geometry.disposals, 1);
    assert.equal(setup.lastFixture.material.disposals, 1);
    assert.equal(setup.store.diagnostics().byte_objects, 0);
  });
}

test('store rejects an explicit empty LOD2 dependency closure before parsing', async () => {
  const descriptor = assetDescriptor({ dependencies: [] });
  const setup = harness({ descriptor });

  await assert.rejects(
    setup.store.loadTemplate(descriptor),
    /texture closure is absent/,
  );

  assert.equal(setup.counters.fetches, 0);
  assert.equal(setup.counters.parses, 0);
});

for (const [label, mutateResponse, descriptorMutation] of [
  [
    'redirect',
    (base) => ({ ...base, redirected: true }),
  ],
  [
    'changed final URL',
    (base) => ({ ...base, url: 'https://viewer.test/wrong-object' }),
  ],
  [
    'wrong PNG content type',
    (base, url) => (
      url.endsWith('.png')
        ? { ...base, headers: { get: () => 'text/plain' } }
        : base
    ),
  ],
  [
    'byte mismatch',
    null,
    (descriptor) => {
      descriptor.texture_dependencies[0].bytes += 1;
    },
  ],
  [
    'SHA mismatch',
    null,
    (descriptor) => {
      descriptor.texture_dependencies[0].sha256 = 'f'.repeat(64);
    },
  ],
]) {
  test(`store rejects ${label} before GLTF parsing`, async () => {
    const descriptor = assetDescriptor();
    descriptorMutation?.(descriptor);
    const setup = harness({ descriptor, mutateResponse });

    await assert.rejects(
      setup.store.loadTemplate(descriptor),
      /redirect|URL|content type|byte count|SHA-256/,
    );
    assert.equal(setup.counters.parses, 0);
    assert.equal(setup.store.diagnostics().templates, 0);
  });
}

for (const [label, sceneOptions] of [
  ['missing material map', { missingRole: 'normal' }],
  ['extra material map', { extraMap: true }],
  ['substituted material map', { substitutedRole: 'base_color' }],
]) {
  test(`store rejects ${label} and disposes transient parse resources`, async () => {
    const setup = harness({ sceneOptions });

    await assert.rejects(
      setup.store.loadTemplate(setup.descriptor),
      /material|texture closure/,
    );

    assert.equal(setup.store.diagnostics().templates, 0);
    assert.equal(
      setup.lastFixture.transientTextures.every(
        (row) => row.disposals === 1,
      ),
      true,
    );
    assert.equal(setup.lastFixture.geometry.disposals, 1);
    assert.equal(setup.lastFixture.material.disposals, 1);
    assert.equal(setup.counters.objectUrls, setup.counters.revokedUrls);
  });
}

test('diagnostics remain bounded counts without URLs hashes or raw bytes', async () => {
  const setup = harness();
  await setup.store.loadTemplate(setup.descriptor);

  const serialized = JSON.stringify(setup.store.diagnostics());

  assert.equal(serialized.includes('/api/'), false);
  assert.equal(serialized.includes(BUNDLE_ID), false);
  assert.equal(serialized.includes(sha256(BASE_BYTES)), false);
  assert.equal(serialized.includes('verified-base-png'), false);
});

function profileDependency(bytes, {
  profileId = 'h3-ai-ktx2-4k',
  role = 'base_color',
  mediaType = 'image/ktx2',
  ...overrides
} = {}) {
  const digest = sha256(bytes);
  const extension = mediaType === 'image/ktx2' ? 'ktx2' : 'png';
  return {
    url: (
      `/api/world/mesh-textures/${BUNDLE_ID}/${profileId}/`
      + `${digest}.${extension}`
    ),
    sha256: digest,
    bytes: bytes.byteLength,
    width: 4096,
    height: 4096,
    media_type: mediaType,
    role,
    transfer: role === 'base_color' ? 'srgb' : 'linear',
    material_slot_id: SLOT_ID,
    ...overrides,
  };
}

test('profile texture store verifies and shares one KTX transcode per semantic key', async () => {
  const { createVerifiedProfileTextureStore } = subject();
  const bytes = new TextEncoder().encode('verified-profile-ktx2');
  const descriptor = profileDependency(bytes);
  const events = [];
  const texture = new FakeTexture(null);
  texture.mipmaps = [{ data: new Uint8Array(16) }];
  const store = createVerifiedProfileTextureStore({
    THREE: fakeThree(),
    materialProfile: 'h3-ai-ktx2-4k',
    ktx2Loader: {
      parse(buffer, onLoad) {
        events.push(['transcode', buffer.byteLength]);
        onLoad(texture);
      },
    },
    fetchFn: async () => ({
      ok: true,
      status: 200,
      redirected: false,
      url: new URL(descriptor.url, ORIGIN).href,
      headers: {
        get(name) {
          if (name.toLowerCase() === 'content-type') return 'image/ktx2';
          if (name.toLowerCase() === 'content-length') {
            return String(bytes.byteLength);
          }
          return null;
        },
      },
      async arrayBuffer() {
        return bytes.buffer.slice(
          bytes.byteOffset,
          bytes.byteOffset + bytes.byteLength,
        );
      },
    }),
    cryptoSubtle: webcrypto.subtle,
    locationHref: ORIGIN,
  });

  const [first, second] = await Promise.all([
    store.acquire(descriptor, { alphaMode: 'OPAQUE', flipY: false }),
    store.acquire(descriptor, { alphaMode: 'OPAQUE', flipY: false }),
  ]);

  assert.equal(first.texture, second.texture);
  assert.equal(first.key, second.key);
  assert.deepEqual(events, [['transcode', bytes.byteLength]]);
  assert.deepEqual(store.diagnostics(), {
    active_textures: 1,
    network_fetches: 1,
    ktx_transcodes: 1,
    png_bitmap_decodes: 0,
    gpu_texture_creations: 1,
    compressed_mip_bytes: 16,
  });
  assert.equal(store.release(first.key), true);
  assert.equal(texture.disposals, 0);
  assert.equal(store.release(second.key), true);
  assert.equal(texture.disposals, 1);
  assert.equal(store.release(second.key), false);
  assert.equal(store.diagnostics().active_textures, 0);
});

test('profile texture store rejects profile drift and reports bounded H3 failure', async () => {
  const { createVerifiedProfileTextureStore } = subject();
  const bytes = new TextEncoder().encode('verified-profile-ktx2');
  const descriptor = profileDependency(bytes);
  const failures = [];
  let fetches = 0;
  const store = createVerifiedProfileTextureStore({
    THREE: fakeThree(),
    materialProfile: 'h3-ai-ktx2-4k',
    ktx2Loader: {
      parse(_buffer, _onLoad, onError) {
        onError(new Error(`private:${descriptor.url}`));
      },
    },
    fetchFn: async () => {
      fetches += 1;
      return {
        ok: true,
        status: 200,
        redirected: false,
        url: new URL(descriptor.url, ORIGIN).href,
        headers: {
          get(name) {
            if (name.toLowerCase() === 'content-type') return 'image/ktx2';
            if (name.toLowerCase() === 'content-length') {
              return String(bytes.byteLength);
            }
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
    },
    cryptoSubtle: webcrypto.subtle,
    locationHref: ORIGIN,
    onProfileFailure(reason) {
      failures.push(reason);
    },
  });

  await assert.rejects(
    store.acquire(descriptor, {
      alphaMode: 'OPAQUE',
      flipY: false,
    }),
    /KTX2 profile texture failed/,
  );
  await assert.rejects(
    store.acquire({
      ...descriptor,
      url: descriptor.url.replace(
        'h3-ai-ktx2-4k',
        'h2-png-1k-fallback',
      ),
    }, {
      alphaMode: 'OPAQUE',
      flipY: false,
    }),
    /profile texture descriptor/,
  );

  assert.equal(fetches, 1);
  assert.deepEqual(failures, [{
    code: 'runtime_h3_failure',
  }]);
  assert.equal(
    JSON.stringify(failures).includes(descriptor.url),
    false,
  );
  assert.equal(store.diagnostics().active_textures, 0);
});
