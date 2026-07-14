/**
 * Optional Spark-backed reconstruction layer.
 *
 * Spark is imported lazily so the rest of the viewer remains usable when the
 * CDN or a full 3DGS artifact is unavailable.  Callers must render their DC
 * point preview whenever this controller reports `dc-point-preview`.
 */

export const SPARK_IMPORT_SPECIFIER = '@sparkjsdev/spark';

// A -90 degree rotation about +X maps ENU (E,N,U) to Three (E,U,-N).
export const ENU_TO_THREE_QUATERNION = Object.freeze({
  x: -Math.SQRT1_2,
  y: 0,
  z: 0,
  w: Math.SQRT1_2,
});

export function rotateVectorByQuaternion([x, y, z], quaternion) {
  const qx = quaternion.x;
  const qy = quaternion.y;
  const qz = quaternion.z;
  const qw = quaternion.w;

  const ix = qw * x + qy * z - qz * y;
  const iy = qw * y + qz * x - qx * z;
  const iz = qw * z + qx * y - qy * x;
  const iw = -qx * x - qy * y - qz * z;

  return [
    ix * qw + iw * -qx + iy * -qz - iz * -qy,
    iy * qw + iw * -qy + iz * -qx - ix * -qz,
    iz * qw + iw * -qz + ix * -qy - iy * -qx,
  ];
}

export function resolveFullSplatUrl(manifestUrl, manifest = {}) {
  const artifact = manifest.full_3dgs;
  if (typeof artifact !== 'string' || artifact.length === 0) return null;
  return new URL(artifact, manifestUrl).href;
}

function fallbackState(reason, url = null) {
  return {
    mode: 'dc-point-preview',
    fidelity: 'dc-point-preview',
    url,
    reason,
  };
}

function sparkState(url) {
  return {
    mode: 'spark',
    fidelity: 'full-3dgs',
    url,
    reason: null,
  };
}

function withTimeout(promise, timeoutMs) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`timed out after ${timeoutMs}ms`)),
      timeoutMs,
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/**
 * Construct a controller for the optional full-fidelity reconstruction layer.
 * Dependencies are injected to keep renderer selection deterministic in tests.
 */
export function createSplatLayer({
  scene,
  renderer,
  importSpark = () => import('@sparkjsdev/spark'),
  timeoutMs = 8000,
}) {
  let state = fallbackState('not loaded');
  let sparkRenderer = null;
  let splatMesh = null;

  function cleanup() {
    if (splatMesh) {
      scene.remove(splatMesh);
      splatMesh.dispose?.();
      splatMesh = null;
    }
    if (sparkRenderer) {
      scene.remove(sparkRenderer);
      sparkRenderer.dispose?.();
      sparkRenderer = null;
    }
  }

  async function load({ manifest, manifestUrl, visible = true }) {
    cleanup();
    const url = resolveFullSplatUrl(manifestUrl, manifest);
    if (!url) {
      state = fallbackState('full_3dgs artifact missing');
      return { ...state };
    }

    try {
      const sparkModule = await withTimeout(importSpark(), timeoutMs);
      if (
        typeof sparkModule?.SparkRenderer !== 'function'
        || typeof sparkModule?.SplatMesh !== 'function'
      ) {
        throw new Error('Spark module is missing renderer exports');
      }

      sparkRenderer = new sparkModule.SparkRenderer({ renderer });
      scene.add(sparkRenderer);

      splatMesh = new sparkModule.SplatMesh({ url });
      splatMesh.quaternion.set(
        ENU_TO_THREE_QUATERNION.x,
        ENU_TO_THREE_QUATERNION.y,
        ENU_TO_THREE_QUATERNION.z,
        ENU_TO_THREE_QUATERNION.w,
      );
      splatMesh.visible = visible;
      scene.add(splatMesh);
      await withTimeout(Promise.resolve(splatMesh.initialized), timeoutMs);

      state = sparkState(url);
      return { ...state };
    } catch (error) {
      cleanup();
      const message = error instanceof Error ? error.message : String(error);
      state = fallbackState(`Spark unavailable: ${message}`, url);
      return { ...state };
    }
  }

  function setVisible(visible) {
    if (splatMesh) splatMesh.visible = visible !== false;
  }

  function dispose() {
    cleanup();
    state = fallbackState('disposed');
  }

  return {
    load,
    setVisible,
    dispose,
    getState: () => ({ ...state }),
  };
}
