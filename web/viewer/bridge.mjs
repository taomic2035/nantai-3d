export const VIEWER_BRIDGE_CHANNEL = 'nantai-viewer';
export const VIEWER_BRIDGE_SCHEMA_VERSION = 1;

const BASE_CAPABILITIES = Object.freeze({
  dynamic_artifact_kinds: Object.freeze([
    'recon-manifest', 'chunk-manifest', 'coverage-audit',
  ]),
  layers: Object.freeze(['world', 'reconstruction']),
  camera_reset: true,
  commands: Object.freeze([
    'loadArtifact',
    'setLOD',
    'setLayer',
    'resetCamera',
    'setBounds',
    'getState',
    'setCameraPose',
    'setWeather',
    'setZoom',
  ]),
});

const POINT_ARTIFACT_KINDS = Object.freeze([
  'chunk-manifest', 'recon-manifest', 'simple-ply',
]);
const SPARK_ARTIFACT_KINDS = Object.freeze([...POINT_ARTIFACT_KINDS, '3dgs-ply']);
const MESH_ARTIFACT_KINDS = Object.freeze([
  ...POINT_ARTIFACT_KINDS, 'synthetic-model-preview',
]);
const LOD_LEVELS = Object.freeze([0, 1, 2]);
const SHA256 = /^[0-9a-f]{64}$/;
const H3_PROFILE_ID = 'h3-ai-ktx2-4k';
const H2_PROFILE_ID = 'h2-png-1k-fallback';
const MAX_H3_COMPRESSED_TEXTURE_BYTES = 512 * 1024 * 1024;
const MAX_DIAGNOSTIC_COUNTER = 1_000_000_000;
const MATERIAL_PROFILE_FALLBACK_LABELS = Object.freeze({
  compressed_memory_budget: '压缩纹理超过 512 MiB 预算',
  invalid_selection_evidence: '材质配置证据不完整',
  ktx2_capability_unavailable: '当前渲染器无法确认 KTX2 支持',
  canary_descriptor_invalid: 'KTX2 探针描述无效',
  canary_fetch_failed: 'KTX2 探针不可用',
  canary_redirect_rejected: 'KTX2 探针发生未授权跳转',
  canary_mime_mismatch: 'KTX2 探针媒体类型不符',
  canary_length_mismatch: 'KTX2 探针字节数不符',
  canary_sha256_mismatch: 'KTX2 探针完整性校验失败',
  canary_verification_failed: 'KTX2 探针无法验证',
  canary_decode_failed: 'KTX2 探针解码失败',
  runtime_h3_failure: 'H3 运行时加载失败',
});

const DC_POINT_RENDERER = Object.freeze({
  id: 'three-points',
  label: 'DC point preview',
  fidelity: 'dc-point-preview',
  anisotropic_covariance: false,
  alpha_composite: false,
  spherical_harmonics: false,
  max_sh_degree: 0,
  dynamic_mesh_relighting: false,
  splat_relighting: false,
});

const SPARK_RENDERER = Object.freeze({
  id: 'spark',
  label: 'Gaussian splat (Spark 2.1.0)',
  version: '2.1.0',
  fidelity: 'full-3dgs',
  anisotropic_covariance: true,
  alpha_composite: true,
  spherical_harmonics: true,
  max_sh_degree: 3,
  dynamic_mesh_relighting: false,
  splat_relighting: false,
});

const MESH_PREVIEW_RENDERER = Object.freeze({
  id: 'three-mesh',
  label: 'Synthetic mesh (simplified PBR)',
  fidelity: 'simplified-pbr-not-render-parity',
  anisotropic_covariance: false,
  alpha_composite: false,
  spherical_harmonics: false,
  max_sh_degree: 0,
  photo_textures: false,
  dynamic_mesh_relighting: false,
  splat_relighting: false,
  real_reconstruction: false,
});

const TEXTURED_MESH_PREVIEW_RENDERER = Object.freeze({
  ...MESH_PREVIEW_RENDERER,
  label: 'Synthetic mesh (embedded PBR)',
  fidelity: 'synthetic-pbr-textured-mesh',
  material_fidelity: 'synthetic-derived-pbr',
  synthetic_pbr_textures: true,
  real_photo_textures: false,
  dynamic_mesh_relighting: true,
  splat_relighting: false,
});

export function createViewerCapabilities(mode = 'dc-point-preview') {
  const sparkActive = mode === 'spark' || mode === 'spark-chunks';
  const texturedMeshActive = mode === 'textured-mesh-preview';
  const meshPreviewActive = mode === 'mesh-preview' || texturedMeshActive;
  return Object.freeze({
    ...BASE_CAPABILITIES,
    renderer: sparkActive
      ? SPARK_RENDERER
      : texturedMeshActive
        ? TEXTURED_MESH_PREVIEW_RENDERER
        : meshPreviewActive ? MESH_PREVIEW_RENDERER : DC_POINT_RENDERER,
    artifact_kinds: sparkActive
      ? SPARK_ARTIFACT_KINDS
      : meshPreviewActive ? MESH_ARTIFACT_KINDS : POINT_ARTIFACT_KINDS,
    lod: Object.freeze({
      supported: true,
      levels: LOD_LEVELS,
      world_chunks: true,
      reconstruction_tiers: mode === 'spark-chunks' || !sparkActive,
    }),
    three_dgs_properties: sparkActive
      ? Object.freeze({
        consumed: Object.freeze([
          'f_dc_*', 'f_rest_* (SH0-SH3)', 'opacity', 'scale_*', 'rot_*',
        ]),
        unsupported: Object.freeze(['f_rest_* above SH3', 'arbitrary-extra-properties']),
      })
      : Object.freeze({
        consumed: Object.freeze([]),
        unsupported: Object.freeze([
          'f_dc_*', 'f_rest_*', 'opacity', 'scale_*', 'rot_*',
        ]),
      }),
  });
}

export const VIEWER_CAPABILITIES = createViewerCapabilities();

function boundedInteger(value, maximum = MAX_DIAGNOSTIC_COUNTER) {
  return (
    Number.isSafeInteger(value)
    && value >= 0
    && value <= maximum
  ) ? value : null;
}

function exactMaterialTruth(runtime) {
  if (
    runtime?.synthetic !== true
    || runtime.ai_generated !== true
    || runtime.real_photo_textures !== false
    || runtime.geometry_usability !== 'preview-only'
    || runtime.metric_alignment !== false
    || runtime.verification_level !== 'L0'
  ) {
    return null;
  }
  return {
    synthetic: true,
    ai_generated: true,
    real_photo_textures: false,
    geometry_usability: 'preview-only',
    metric_alignment: false,
    verification_level: 'L0',
  };
}

export function materialProfileEvidence({
  snapshot,
  runtime,
} = {}) {
  const profileId = snapshot?.profileId;
  const truth = exactMaterialTruth(runtime);
  if (
    ![H3_PROFILE_ID, H2_PROFILE_ID].includes(profileId)
    || !truth
    || !SHA256.test(runtime?.mesh_asset_bundle_id)
  ) {
    return null;
  }
  const materialBundleId = profileId === H3_PROFILE_ID
    ? runtime.material_bundle_id
    : runtime.fallback_material_bundle_id;
  if (!SHA256.test(materialBundleId)) return null;

  const textures = snapshot.resources?.profile_textures ?? {};
  const templates = snapshot.resources?.mesh_templates ?? {};
  const fallbackCode = profileId === H2_PROFILE_ID
    && Object.hasOwn(
      MATERIAL_PROFILE_FALLBACK_LABELS,
      snapshot.fallbackCode,
    )
    ? snapshot.fallbackCode
    : null;
  const predictedBytes = profileId === H3_PROFILE_ID
    ? boundedInteger(
      runtime.predicted_compressed_texture_bytes,
      MAX_H3_COMPRESSED_TEXTURE_BYTES,
    )
    : 0;
  const observedBytes = profileId === H3_PROFILE_ID
    ? boundedInteger(
      textures.compressed_mip_bytes,
      MAX_H3_COMPRESSED_TEXTURE_BYTES,
    )
    : 0;

  return {
    profile_id: profileId,
    fallback_code: fallbackCode,
    mesh_asset_bundle_id: runtime.mesh_asset_bundle_id,
    material_bundle_id: materialBundleId,
    predicted_compressed_texture_bytes: predictedBytes,
    observed_compressed_texture_bytes: observedBytes,
    truth,
    counters: {
      active_textures: boundedInteger(textures.active_textures),
      texture_network_fetches: boundedInteger(textures.network_fetches),
      ktx_transcodes: boundedInteger(textures.ktx_transcodes),
      png_bitmap_decodes: boundedInteger(textures.png_bitmap_decodes),
      texture_gpu_creations: boundedInteger(
        textures.gpu_texture_creations,
      ),
      mesh_templates: boundedInteger(templates.templates),
      mesh_network_fetches: boundedInteger(templates.network_fetches),
      mesh_bitmap_decodes: boundedInteger(templates.bitmap_decodes),
      mesh_gpu_texture_creations: boundedInteger(
        templates.gpu_texture_creations,
      ),
    },
  };
}

export function materialProfileHud(evidence) {
  if (!evidence) {
    return {
      profile: '未启用（非 runtime-v3）',
      truth: 'unknown · fail-closed',
      compressed: 'unknown / 512 MiB',
    };
  }
  const fallback = MATERIAL_PROFILE_FALLBACK_LABELS[
    evidence.fallback_code
  ] ?? '回退原因未知';
  const profile = evidence.profile_id === H3_PROFILE_ID
    ? 'AI 合成 4K · KTX2'
    : `H2 1K 回退 · ${fallback}`;
  const truthful = (
    evidence.truth?.synthetic === true
    && evidence.truth?.real_photo_textures === false
    && evidence.truth?.geometry_usability === 'preview-only'
  );
  const observed = evidence.observed_compressed_texture_bytes;
  return {
    profile,
    truth: truthful
      ? 'synthetic · preview-only · not real-photo'
      : 'unknown · fail-closed',
    compressed: Number.isSafeInteger(observed)
      ? `${(observed / 1024 / 1024).toFixed(1)} MiB / 512 MiB`
      : 'unknown / 512 MiB',
  };
}

function unknown(value) {
  return value ?? 'unknown';
}

function renderedArtifactFidelity(manifest, provenance, capabilities) {
  const declared = provenance.artifact_fidelity ?? manifest.artifact_fidelity;
  const declaredObject = (
    declared && typeof declared === 'object' ? declared : {}
  );
  const artifacts = manifest.artifacts ?? {};

  if (capabilities.renderer.fidelity === 'full-3dgs') {
    return (
      artifacts.full_3dgs?.fidelity
      ?? declaredObject.full_3dgs
      ?? manifest.render_fidelity
      ?? provenance.render_fidelity
      ?? (typeof declared === 'string' ? declared : undefined)
      ?? manifest.fidelity
    );
  }

  const lodArtifact = Object.values(artifacts.lod ?? {})
    .find((artifact) => artifact?.fidelity);
  return (
    declaredObject.lod_preview
    ?? lodArtifact?.fidelity
    ?? manifest.render_fidelity
    ?? provenance.render_fidelity
    ?? (typeof declared === 'string' ? declared : undefined)
    ?? manifest.fidelity
  );
}

/** Normalize machine-readable artifact provenance without inferring trust. */
export function artifactProvenance(manifest = {}, capabilities = VIEWER_CAPABILITIES) {
  const spatialChunks = manifest.kind === 'spatial-chunks';
  const provenance = spatialChunks ? (manifest.source ?? {}) : (manifest.provenance ?? {});
  const coordinateContract = spatialChunks ? {} : (manifest.coordinate_contract ?? {});
  const worldFrame = spatialChunks
    ? {
      frame_id: provenance.frame_id,
      units: provenance.units,
      handedness: provenance.handedness,
    }
    : (
      manifest.world_frame
      ?? provenance.world_frame
      ?? coordinateContract.target_frame
      ?? {}
    );
  return {
    requested_engine: unknown(
      (spatialChunks ? undefined : manifest.requested_engine)
      ?? provenance.requested_engine
      ?? provenance.requested_reconstruction_engine,
    ),
    actual_engine: unknown(
      (spatialChunks ? undefined : manifest.actual_engine)
      ?? provenance.actual_engine
      ?? provenance.actual_reconstruction_engine
      ?? (spatialChunks ? undefined : manifest.engine),
    ),
    synthetic: unknown((spatialChunks ? undefined : manifest.synthetic) ?? provenance.synthetic),
    frame: unknown(
      worldFrame.name
      ?? worldFrame.frame
      ?? worldFrame.frame_id
      ?? (spatialChunks ? undefined : manifest.frame),
    ),
    units: unknown(worldFrame.units ?? (spatialChunks ? undefined : manifest.units)),
    handedness: unknown(
      worldFrame.handedness ?? (spatialChunks ? undefined : manifest.handedness),
    ),
    geometry_usability: unknown(
      (spatialChunks ? undefined : manifest.geometry_usability)
      ?? provenance.geometry_usability,
    ),
    artifact_fidelity: unknown(
      renderedArtifactFidelity(manifest, provenance, capabilities),
    ),
    viewer_fidelity: capabilities.renderer.fidelity,
  };
}

/** Resolve an artifact child path relative to its manifest URL. */
export function resolveArtifactUrl(manifestUrl, artifactPath) {
  return new URL(artifactPath, manifestUrl).href;
}

function responseType(command) {
  return command === 'loadArtifact' ? 'artifactLoaded' : 'stateChanged';
}

/** Create a same-origin, versioned Studio-to-viewer postMessage bridge. */
export function createViewerBridge({
  windowObject,
  handlers,
  capabilities = VIEWER_CAPABILITIES,
}) {
  const targetOrigin = windowObject.location.origin;
  let started = false;

  const getCapabilities = () => (
    typeof capabilities === 'function' ? capabilities() : capabilities
  );

  function send(type, requestId, payload) {
    if (windowObject.parent === windowObject) return;
    windowObject.parent.postMessage({
      channel: VIEWER_BRIDGE_CHANNEL,
      schema_version: VIEWER_BRIDGE_SCHEMA_VERSION,
      type,
      request_id: requestId,
      payload,
    }, targetOrigin);
  }

  async function handleMessage(event) {
    if (event.origin !== targetOrigin || event.source !== windowObject.parent) return false;
    const message = event.data;
    if (
      !message
      || message.channel !== VIEWER_BRIDGE_CHANNEL
      || message.schema_version !== VIEWER_BRIDGE_SCHEMA_VERSION
    ) return false;

    if (typeof message.request_id !== 'string' || message.request_id.length === 0) {
      send('error', null, { code: 'invalid-request', message: 'request_id is required' });
      return true;
    }

    const handler = handlers[message.type];
    if (!getCapabilities().commands.includes(message.type) || typeof handler !== 'function') {
      send('error', message.request_id ?? null, {
        code: 'unsupported-command',
        command: message.type ?? 'unknown',
      });
      return true;
    }

    try {
      const result = await handler(message.payload ?? {}, message);
      send(responseType(message.type), message.request_id, {
        command: message.type,
        result,
      });
    } catch (error) {
      send('error', message.request_id ?? null, {
        code: 'command-failed',
        command: message.type,
        message: error instanceof Error ? error.message : String(error),
      });
    }
    return true;
  }

  function start() {
    if (started) return;
    started = true;
    windowObject.addEventListener('message', handleMessage);
    send('ready', 'viewer-ready', { capabilities: getCapabilities() });
  }

  function announceCapabilities() {
    if (!started) return;
    send('capabilitiesChanged', 'viewer-capabilities', {
      capabilities: getCapabilities(),
    });
  }

  function stop() {
    if (!started) return;
    started = false;
    windowObject.removeEventListener('message', handleMessage);
  }

  return { start, stop, handleMessage, announceCapabilities };
}
