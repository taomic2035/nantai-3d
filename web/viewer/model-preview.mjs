const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const REQUIRED_LIMITATIONS = Object.freeze([
  'not-real-place',
  'not-measured-geometry',
  'not-completed-trained-reconstruction',
  'no-photo-textures',
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
  if (manifest.schema_version !== 1 || manifest.kind !== 'synthetic-model-preview') {
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
    || REQUIRED_LIMITATIONS.some((item) => !manifest.limitations.includes(item))
  ) {
    invalidManifest('all trust and texture limitations are required');
  }
  return manifest;
}

export function resolveModelPreviewUrl(manifestUrl, manifest, expectedOrigin) {
  validateModelPreviewManifest(manifest);
  const absoluteManifestUrl = new URL(manifestUrl);
  const modelUrl = new URL(manifest.model.path, absoluteManifestUrl);
  if (
    absoluteManifestUrl.origin !== expectedOrigin
    || modelUrl.origin !== expectedOrigin
  ) {
    throw new Error('Model preview must use a same-origin manifest and GLB');
  }
  const directory = new URL('./', absoluteManifestUrl).pathname;
  if (!modelUrl.pathname.startsWith(directory)) {
    throw new Error('Model preview GLB must stay inside its manifest directory');
  }
  return modelUrl.href;
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

export function modelPreviewDisclosure(manifest) {
  validateModelPreviewManifest(manifest);
  return '合成模型 · 简化 PBR · 非照片纹理 · 非真实重建';
}

function blenderToThree([x, y, z]) {
  return [x, z, -y];
}

export function modelPreviewCameraPose(manifest) {
  validateModelPreviewManifest(manifest);
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
