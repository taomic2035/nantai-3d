import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';

let previewModule;
try {
  previewModule = await import('./model-preview.mjs');
} catch (error) {
  previewModule = { __loadError: error };
}

function subject() {
  assert.equal(
    previewModule.__loadError,
    undefined,
    `model-preview.mjs must load: ${previewModule.__loadError?.message}`,
  );
  return previewModule;
}

const VALID_MANIFEST = Object.freeze({
  schema_version: 1,
  kind: 'synthetic-model-preview',
  synthetic: true,
  geometry_usability: 'preview-only',
  fidelity: 'simplified-pbr-not-render-parity',
  coordinate_frame: {
    frame_id: 'synthetic-canary-gltf-local',
    axes: 'right-handed-y-up',
    units: 'unknown',
  },
  presentation: {
    camera: {
      eye_blender_xyz: [108, -142, 140],
      target_blender_xyz: [0, 10, 71],
      lens_mm: 42,
      sensor_width_mm: 36,
      aspect_ratio: 16 / 9,
      clip_start: 1,
      clip_end: 2000,
    },
  },
  model: {
    path: 'village-canary.glb',
    sha256: 'a'.repeat(64),
    media_type: 'model/gltf-binary',
  },
  source: {
    release: 'synthetic-village-canary-2026-07-16',
    build_id: 'b'.repeat(64),
    build_report_sha256: 'c'.repeat(64),
  },
  limitations: [
    'not-real-place',
    'not-measured-geometry',
    'not-completed-trained-reconstruction',
    'no-photo-textures',
  ],
});

const VALID_LOCAL_TEXTURED_MANIFEST = Object.freeze({
  schema_version: 2,
  preview_id: '1'.repeat(64),
  synthetic: true,
  verification_level: 'L0',
  authoritative: false,
  release_channel: 'local-preview-only',
  geometry_usability: 'preview-only',
  material_fidelity: 'synthetic-derived-pbr',
  synthetic_pbr_textures: true,
  real_photo_textures: false,
  dynamic_mesh_relighting: true,
  splat_relighting: false,
  model_url: `/api/local-textured-preview/${'1'.repeat(64)}/village-canary.glb`,
  glb_sha256: '2'.repeat(64),
  glb_bytes: 133_692_928,
  build_report_sha256: '3'.repeat(64),
  audit_sha256: '4'.repeat(64),
  material_bundle_id: '5'.repeat(64),
  limitations: [
    'not-real-place',
    'not-measured-geometry',
    'not-completed-trained-reconstruction',
    'no-real-photo-textures',
    'local-preview-only',
  ],
});

test('accepts only an explicitly synthetic preview-only GLB manifest', () => {
  const { validateModelPreviewManifest } = subject();

  assert.deepEqual(validateModelPreviewManifest(VALID_MANIFEST), VALID_MANIFEST);

  for (const patch of [
    { synthetic: false },
    { geometry_usability: 'metric-aligned' },
    { fidelity: 'photoreal' },
    { coordinate_frame: { ...VALID_MANIFEST.coordinate_frame, units: 'meters' } },
    { presentation: { camera: { ...VALID_MANIFEST.presentation.camera, lens_mm: 0 } } },
    { model: { ...VALID_MANIFEST.model, sha256: 'unknown' } },
    { limitations: VALID_MANIFEST.limitations.filter((item) => item !== 'no-photo-textures') },
  ]) {
    assert.throws(
      () => validateModelPreviewManifest({ ...VALID_MANIFEST, ...patch }),
      /model preview manifest/i,
    );
  }
});

test('accepts only a non-authoritative local L0 textured preview manifest', () => {
  const { validateModelPreviewManifest } = subject();

  const manifest = validateModelPreviewManifest(VALID_LOCAL_TEXTURED_MANIFEST);
  assert.deepEqual(manifest, VALID_LOCAL_TEXTURED_MANIFEST);
  assert.equal(manifest.material_fidelity, 'synthetic-derived-pbr');
  assert.equal(manifest.real_photo_textures, false);
  assert.equal(manifest.geometry_usability, 'preview-only');
  assert.equal('surface_shader' in manifest, false);

  for (const patch of [
    { verification_level: 'L2' },
    { authoritative: true },
    { release_channel: 'public' },
    { geometry_usability: 'metric-aligned' },
    { material_fidelity: 'photo-textured' },
    { real_photo_textures: true },
    { glb_sha256: 'unknown' },
    { glb_bytes: 0 },
    { model_url: '/web/data/unverified.glb' },
    {
      limitations: VALID_LOCAL_TEXTURED_MANIFEST.limitations.filter(
        (item) => item !== 'local-preview-only',
      ),
    },
  ]) {
    assert.throws(
      () => validateModelPreviewManifest({
        ...VALID_LOCAL_TEXTURED_MANIFEST,
        ...patch,
      }),
      /model preview manifest/i,
    );
  }
});

test('resolves only a same-origin GLB child of its manifest', () => {
  const { resolveModelPreviewUrl } = subject();
  const manifestUrl = 'https://viewer.example/web/data/recon/model-preview/manifest.json';

  assert.equal(
    resolveModelPreviewUrl(manifestUrl, VALID_MANIFEST, 'https://viewer.example'),
    'https://viewer.example/web/data/recon/model-preview/village-canary.glb',
  );
  assert.throws(
    () => resolveModelPreviewUrl(
      manifestUrl,
      {
        ...VALID_MANIFEST,
        model: { ...VALID_MANIFEST.model, path: 'https://evil.example/model.glb' },
      },
      'https://viewer.example',
    ),
    /same-origin/i,
  );
  assert.throws(
    () => resolveModelPreviewUrl(
      manifestUrl,
      {
        ...VALID_MANIFEST,
        model: { ...VALID_MANIFEST.model, path: '../unrelated.glb' },
      },
      'https://viewer.example',
    ),
    /manifest directory/i,
  );
});

test('binds a local L0 manifest and GLB to the same private preview id', () => {
  const { resolveModelPreviewUrl } = subject();
  const id = VALID_LOCAL_TEXTURED_MANIFEST.preview_id;
  const manifestUrl = `https://viewer.example/api/local-textured-preview/${id}/manifest.json`;

  assert.equal(
    resolveModelPreviewUrl(
      manifestUrl,
      VALID_LOCAL_TEXTURED_MANIFEST,
      'https://viewer.example',
    ),
    `https://viewer.example/api/local-textured-preview/${id}/village-canary.glb`,
  );
  assert.throws(
    () => resolveModelPreviewUrl(
      `https://viewer.example/api/local-textured-preview/${'9'.repeat(64)}/manifest.json`,
      VALID_LOCAL_TEXTURED_MANIFEST,
      'https://viewer.example',
    ),
    /preview id/i,
  );
  assert.throws(
    () => resolveModelPreviewUrl(
      manifestUrl,
      {
        ...VALID_LOCAL_TEXTURED_MANIFEST,
        model_url: `https://evil.example/api/local-textured-preview/${id}/village-canary.glb`,
      },
      'https://viewer.example',
    ),
    /same-origin/i,
  );
});

test('selects only a same-origin private L0 manifest from the page query', () => {
  const { resolveRequestedModelPreviewManifestUrl } = subject();
  const id = VALID_LOCAL_TEXTURED_MANIFEST.preview_id;
  const fallback = 'https://viewer.example/web/data/recon/model-preview/manifest.json';

  assert.equal(
    resolveRequestedModelPreviewManifestUrl(
      `https://viewer.example/web/viewer/?modelPreview=${encodeURIComponent(
        `/api/local-textured-preview/${id}/manifest.json`,
      )}`,
      fallback,
    ),
    `https://viewer.example/api/local-textured-preview/${id}/manifest.json`,
  );
  assert.equal(
    resolveRequestedModelPreviewManifestUrl(
      'https://viewer.example/web/viewer/',
      fallback,
    ),
    fallback,
  );
  for (const requested of [
    'https://evil.example/manifest.json',
    '/web/data/recon/model-preview/manifest.json',
    `/api/local-textured-preview/${'A'.repeat(64)}/manifest.json`,
    `/api/local-textured-preview/${id}/manifest.json?mutable=1`,
  ]) {
    assert.throws(
      () => resolveRequestedModelPreviewManifestUrl(
        `https://viewer.example/web/viewer/?modelPreview=${encodeURIComponent(requested)}`,
        fallback,
      ),
      /private local textured preview/i,
    );
  }
});

test('verifies the fetched GLB bytes against the manifest SHA-256', async () => {
  const { modelPreviewSha256, verifyModelPreviewBytes } = subject();
  const bytes = new TextEncoder().encode('verified model bytes');
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  const digest = async (algorithm, payload) => {
    assert.equal(algorithm, 'SHA-256');
    return createHash('sha256').update(new Uint8Array(payload)).digest();
  };

  assert.equal(
    await verifyModelPreviewBytes(bytes.buffer, sha256, digest),
    sha256,
  );
  assert.equal(modelPreviewSha256(VALID_MANIFEST), VALID_MANIFEST.model.sha256);
  assert.equal(
    modelPreviewSha256(VALID_LOCAL_TEXTURED_MANIFEST),
    VALID_LOCAL_TEXTURED_MANIFEST.glb_sha256,
  );
  await assert.rejects(
    verifyModelPreviewBytes(bytes.buffer, '0'.repeat(64), digest),
    /SHA-256 mismatch/,
  );
});

test('honest badge never describes simplified PBR as photo texture or reconstruction', () => {
  const { modelPreviewDisclosure } = subject();
  const disclosure = modelPreviewDisclosure(VALID_MANIFEST);

  assert.match(disclosure, /合成模型/);
  assert.match(disclosure, /简化 PBR/);
  assert.match(disclosure, /非照片纹理/);
  assert.match(disclosure, /非真实重建/);
  assert.doesNotMatch(disclosure, /真实纹理|照片级|已重建/);

  const localDisclosure = modelPreviewDisclosure(VALID_LOCAL_TEXTURED_MANIFEST);
  assert.match(localDisclosure, /本机 L0/);
  assert.match(localDisclosure, /合成 PBR 纹理/);
  assert.match(localDisclosure, /非照片/);
  assert.match(localDisclosure, /非真实重建/);
  assert.doesNotMatch(localDisclosure, /真实纹理|照片级|已重建/);
});

test('maps the content-addressed Blender preview camera into glTF coordinates', () => {
  const { modelPreviewCameraPose, selectEmbeddedModelPreviewCamera } = subject();
  const pose = modelPreviewCameraPose(VALID_MANIFEST);

  assert.deepEqual(pose.positionThree, [108, 140, 142]);
  assert.deepEqual(pose.targetThree, [0, 71, -10]);
  assert.ok(Math.abs(pose.verticalFovDeg - 27.1) < 0.2);
  assert.equal(modelPreviewCameraPose(VALID_LOCAL_TEXTURED_MANIFEST), null);

  const outer = { name: 'nv__camera-outer-001', isPerspectiveCamera: true };
  const ground = { name: 'nv__camera-ground-001', isPerspectiveCamera: true };
  const orthographicGround = {
    name: 'nv__camera-ground-001',
    isPerspectiveCamera: false,
  };
  assert.equal(
    selectEmbeddedModelPreviewCamera(
      VALID_LOCAL_TEXTURED_MANIFEST,
      [outer, ground],
    ),
    outer,
  );
  assert.equal(
    selectEmbeddedModelPreviewCamera(
      VALID_LOCAL_TEXTURED_MANIFEST,
      [orthographicGround, outer],
    ),
    outer,
  );
  assert.equal(
    selectEmbeddedModelPreviewCamera(VALID_MANIFEST, [ground]),
    null,
  );
  assert.equal(
    selectEmbeddedModelPreviewCamera(VALID_LOCAL_TEXTURED_MANIFEST, []),
    null,
  );
});
