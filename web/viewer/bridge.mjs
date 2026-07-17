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
const LOD_LEVELS = Object.freeze([0, 1, 2]);

const DC_POINT_RENDERER = Object.freeze({
  id: 'three-points',
  label: 'DC point preview',
  fidelity: 'dc-point-preview',
  anisotropic_covariance: false,
  alpha_composite: false,
  spherical_harmonics: false,
  max_sh_degree: 0,
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
});

export function createViewerCapabilities(mode = 'dc-point-preview') {
  const sparkActive = mode === 'spark' || mode === 'spark-chunks';
  return Object.freeze({
    ...BASE_CAPABILITIES,
    renderer: sparkActive ? SPARK_RENDERER : DC_POINT_RENDERER,
    artifact_kinds: sparkActive ? SPARK_ARTIFACT_KINDS : POINT_ARTIFACT_KINDS,
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
