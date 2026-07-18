const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const LOCAL_PREVIEW_PATH_PATTERN =
  /^\/api\/local-textured-preview\/([0-9a-f]{64})\/manifest\.json$/;
const LEGACY_REQUIRED_LIMITATIONS = Object.freeze([
  'not-real-place',
  'not-measured-geometry',
  'not-completed-trained-reconstruction',
  'no-photo-textures',
]);
const LOCAL_REQUIRED_LIMITATIONS = Object.freeze([
  'not-real-place',
  'not-measured-geometry',
  'not-completed-trained-reconstruction',
  'no-real-photo-textures',
  'local-preview-only',
]);

function invalidManifest(reason) {
  throw new Error(`Invalid model preview manifest: ${reason}`);
}

function finiteVec3(value) {
  return (
    Array.isArray(value)
    && value.length === 3
    && value.every((item) => Number.isFinite(item))
  );
}

export function validateModelPreviewManifest(manifest) {
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
    invalidManifest('document must be an object');
  }
  if (manifest.schema_version === 1) {
    return validateLegacyManifest(manifest);
  }
  if (manifest.schema_version === 2) {
    return validateLocalTexturedManifest(manifest);
  }
  invalidManifest('unsupported schema or kind');
}

function validateLegacyManifest(manifest) {
  if (manifest.kind !== 'synthetic-model-preview') {
    invalidManifest('unsupported schema or kind');
  }
  if (
    manifest.synthetic !== true
    || manifest.geometry_usability !== 'preview-only'
    || manifest.fidelity !== 'simplified-pbr-not-render-parity'
  ) {
    invalidManifest('synthetic preview-only fidelity declaration is required');
  }
  if (
    !manifest.coordinate_frame
    || manifest.coordinate_frame.frame_id !== 'synthetic-canary-gltf-local'
    || manifest.coordinate_frame.axes !== 'right-handed-y-up'
    || manifest.coordinate_frame.units !== 'unknown'
  ) {
    invalidManifest('explicit unmeasured glTF-local coordinate frame is required');
  }
  const camera = manifest.presentation?.camera;
  if (
    !camera
    || !finiteVec3(camera.eye_blender_xyz)
    || !finiteVec3(camera.target_blender_xyz)
    || !Number.isFinite(camera.lens_mm)
    || camera.lens_mm <= 0
    || !Number.isFinite(camera.sensor_width_mm)
    || camera.sensor_width_mm <= 0
    || !Number.isFinite(camera.aspect_ratio)
    || camera.aspect_ratio <= 0
    || !Number.isFinite(camera.clip_start)
    || camera.clip_start <= 0
    || !Number.isFinite(camera.clip_end)
    || camera.clip_end <= camera.clip_start
  ) {
    invalidManifest('finite content-addressed presentation camera is required');
  }
  if (
    !manifest.model
    || typeof manifest.model.path !== 'string'
    || !manifest.model.path.toLowerCase().endsWith('.glb')
    || manifest.model.media_type !== 'model/gltf-binary'
    || !SHA256_PATTERN.test(manifest.model.sha256 ?? '')
  ) {
    invalidManifest('content-addressed GLB descriptor is required');
  }
  if (
    !manifest.source
    || typeof manifest.source.release !== 'string'
    || !SHA256_PATTERN.test(manifest.source.build_id ?? '')
    || !SHA256_PATTERN.test(manifest.source.build_report_sha256 ?? '')
  ) {
    invalidManifest('content-addressed release source is required');
  }
  if (
    !Array.isArray(manifest.limitations)
    || LEGACY_REQUIRED_LIMITATIONS.some((item) => !manifest.limitations.includes(item))
  ) {
    invalidManifest('all trust and texture limitations are required');
  }
  return manifest;
}

function validateLocalTexturedManifest(manifest) {
  if (
    manifest.synthetic !== true
    || manifest.verification_level !== 'L0'
    || manifest.authoritative !== false
    || manifest.release_channel !== 'local-preview-only'
    || manifest.geometry_usability !== 'preview-only'
    || manifest.material_fidelity !== 'synthetic-derived-pbr'
    || manifest.synthetic_pbr_textures !== true
    || manifest.real_photo_textures !== false
    || manifest.dynamic_mesh_relighting !== true
    || manifest.splat_relighting !== false
  ) {
    invalidManifest('non-authoritative local L0 synthetic PBR declaration is required');
  }
  if (
    !SHA256_PATTERN.test(manifest.preview_id ?? '')
    || !SHA256_PATTERN.test(manifest.glb_sha256 ?? '')
    || !Number.isSafeInteger(manifest.glb_bytes)
    || manifest.glb_bytes <= 0
    || !SHA256_PATTERN.test(manifest.build_report_sha256 ?? '')
    || !SHA256_PATTERN.test(manifest.audit_sha256 ?? '')
    || !SHA256_PATTERN.test(manifest.material_bundle_id ?? '')
  ) {
    invalidManifest('content-addressed local preview evidence is required');
  }
  let modelUrl;
  try {
    modelUrl = new URL(manifest.model_url, 'https://local-preview.invalid');
  } catch {
    invalidManifest('private local textured preview GLB route is required');
  }
  const expectedPath =
    `/api/local-textured-preview/${manifest.preview_id}/village-canary.glb`;
  if (
    modelUrl.pathname !== expectedPath
    || modelUrl.search !== ''
    || modelUrl.hash !== ''
  ) {
    invalidManifest('private local textured preview GLB route is required');
  }
  if (
    !Array.isArray(manifest.limitations)
    || LOCAL_REQUIRED_LIMITATIONS.some((item) => !manifest.limitations.includes(item))
  ) {
    invalidManifest('all local trust and texture limitations are required');
  }
  return manifest;
}

export function resolveModelPreviewUrl(manifestUrl, manifest, expectedOrigin) {
  validateModelPreviewManifest(manifest);
  const absoluteManifestUrl = new URL(manifestUrl);
  const modelUrl = new URL(
    manifest.schema_version === 2 ? manifest.model_url : manifest.model.path,
    absoluteManifestUrl,
  );
  if (
    absoluteManifestUrl.origin !== expectedOrigin
    || modelUrl.origin !== expectedOrigin
  ) {
    throw new Error('Model preview must use a same-origin manifest and GLB');
  }
  if (manifest.schema_version === 2) {
    const id = manifest.preview_id;
    if (
      absoluteManifestUrl.pathname
        !== `/api/local-textured-preview/${id}/manifest.json`
      || absoluteManifestUrl.search !== ''
      || absoluteManifestUrl.hash !== ''
      || modelUrl.pathname
        !== `/api/local-textured-preview/${id}/village-canary.glb`
      || modelUrl.search !== ''
      || modelUrl.hash !== ''
    ) {
      throw new Error('Local model preview manifest and GLB must bind to the same preview id');
    }
    return modelUrl.href;
  }
  const directory = new URL('./', absoluteManifestUrl).pathname;
  if (!modelUrl.pathname.startsWith(directory)) {
    throw new Error('Model preview GLB must stay inside its manifest directory');
  }
  return modelUrl.href;
}

export function resolveRequestedModelPreviewManifestUrl(pageUrl, fallbackManifestUrl) {
  const page = new URL(pageUrl);
  const requested = page.searchParams.get('modelPreview');
  if (requested === null) return fallbackManifestUrl;

  let manifestUrl;
  try {
    manifestUrl = new URL(requested, page);
  } catch {
    throw new Error('Requested model preview must be a private local textured preview');
  }
  if (
    manifestUrl.origin !== page.origin
    || !LOCAL_PREVIEW_PATH_PATTERN.test(manifestUrl.pathname)
    || manifestUrl.search !== ''
    || manifestUrl.hash !== ''
  ) {
    throw new Error('Requested model preview must be a private local textured preview');
  }
  return manifestUrl.href;
}

function bytesToHex(bytes) {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function verifyModelPreviewBytes(
  buffer,
  expectedSha256,
  digest = globalThis.crypto?.subtle?.digest?.bind(globalThis.crypto.subtle),
) {
  if (!(buffer instanceof ArrayBuffer) || !SHA256_PATTERN.test(expectedSha256 ?? '')) {
    throw new Error('Model preview SHA-256 verification requires valid bytes and digest');
  }
  if (typeof digest !== 'function') {
    throw new Error('Model preview SHA-256 verification is unavailable');
  }
  const actual = bytesToHex(new Uint8Array(await digest('SHA-256', buffer)));
  if (actual !== expectedSha256) {
    throw new Error(`Model preview SHA-256 mismatch: expected ${expectedSha256}, got ${actual}`);
  }
  return actual;
}

export function modelPreviewSha256(manifest) {
  validateModelPreviewManifest(manifest);
  return manifest.schema_version === 2
    ? manifest.glb_sha256
    : manifest.model.sha256;
}

export function modelPreviewDisclosure(manifest) {
  validateModelPreviewManifest(manifest);
  if (manifest.schema_version === 2) {
    return '本机 L0 · 合成 PBR 纹理 · 非照片 · 非真实重建';
  }
  return '合成模型 · 简化 PBR · 非照片纹理 · 非真实重建';
}

function blenderToThree([x, y, z]) {
  return [x, z, -y];
}

export function modelPreviewCameraPose(manifest) {
  validateModelPreviewManifest(manifest);
  if (manifest.schema_version === 2) return null;
  const authored = manifest.presentation.camera;
  const horizontalFov = 2 * Math.atan(
    authored.sensor_width_mm / (2 * authored.lens_mm),
  );
  const verticalFov = 2 * Math.atan(
    Math.tan(horizontalFov / 2) / authored.aspect_ratio,
  );
  return {
    positionThree: blenderToThree(authored.eye_blender_xyz),
    targetThree: blenderToThree(authored.target_blender_xyz),
    verticalFovDeg: verticalFov * 180 / Math.PI,
    near: authored.clip_start,
    far: authored.clip_end,
  };
}

export function selectEmbeddedModelPreviewCamera(manifest, cameras) {
  validateModelPreviewManifest(manifest);
  if (manifest.schema_version !== 2 || !Array.isArray(cameras)) return null;
  for (const name of [
    'nv__camera-outer-001',
    'nv__camera-ground-001',
  ]) {
    const camera = cameras.find(
      (candidate) => (
        candidate?.name === name
        && candidate.isPerspectiveCamera === true
      ),
    );
    if (camera) return camera;
  }
  return null;
}

export function modelPreviewTrustMetadata(manifest) {
  validateModelPreviewManifest(manifest);
  if (manifest.schema_version === 2) {
    return {
      coordinate_frame: {
        frame_id: 'local-preview-gltf-unmeasured',
        axes: 'right-handed-y-up',
        units: 'unknown',
      },
      fidelity: manifest.material_fidelity,
      photo_textures: manifest.real_photo_textures,
      sha256: manifest.glb_sha256,
    };
  }
  return {
    coordinate_frame: manifest.coordinate_frame,
    fidelity: manifest.fidelity,
    photo_textures: false,
    sha256: manifest.model.sha256,
  };
}
