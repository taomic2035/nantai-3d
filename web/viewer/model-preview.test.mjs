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

test('verifies the fetched GLB bytes against the manifest SHA-256', async () => {
  const { verifyModelPreviewBytes } = subject();
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
});

test('maps the content-addressed Blender preview camera into glTF coordinates', () => {
  const { modelPreviewCameraPose } = subject();
  const pose = modelPreviewCameraPose(VALID_MANIFEST);

  assert.deepEqual(pose.positionThree, [108, 140, 142]);
  assert.deepEqual(pose.targetThree, [0, 71, -10]);
  assert.ok(Math.abs(pose.verticalFovDeg - 27.1) < 0.2);
});
