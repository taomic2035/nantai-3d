import { ENU_TO_THREE_QUATERNION } from './splat-layer.mjs';
import {
  isSpatialChunkManifest,
  resolveSpatialChunkUrl,
  selectSpatialChunkRequests,
} from './spatial-reconstruction.mjs';

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

function fallbackState(reason, manifestUrl = null) {
  return {
    mode: 'dc-point-preview',
    fidelity: 'dc-point-preview',
    manifest_url: manifestUrl,
    reason,
    last_error: null,
  };
}

function sparkState(manifestUrl) {
  return {
    mode: 'spark-chunks',
    fidelity: 'full-3dgs',
    manifest_url: manifestUrl,
    reason: null,
    last_error: null,
  };
}

function activeDensityEvidence(active) {
  const records = [...active.values()];
  if (
    records.length === 0
    || records.some((record) => (
      !Number.isSafeInteger(record.estimatedPointCount)
      || !Number.isFinite(record.lodFraction)
    ))
  ) {
    return {
      active_estimated_points: null,
      active_lod_fractions: null,
    };
  }
  return {
    active_estimated_points: records.reduce(
      (total, record) => total + record.estimatedPointCount,
      0,
    ),
    active_lod_fractions: [
      ...new Set(records.map((record) => record.lodFraction)),
    ].sort((left, right) => left - right),
  };
}

export function createSpatialSplatLayer({
  scene,
  renderer,
  importSpark = () => import('@sparkjsdev/spark'),
  timeoutMs = 8000,
  cacheMax = 36,
  radiusChunks = 2,
}) {
  if (!Number.isInteger(cacheMax) || cacheMax < 1) {
    throw new RangeError('cacheMax must be a positive integer');
  }
  if (!Number.isInteger(radiusChunks) || radiusChunks < 0) {
    throw new RangeError('radiusChunks must be a non-negative integer');
  }

  let state = fallbackState('not loaded');
  let manifest = null;
  let manifestUrl = null;
  let sparkModule = null;
  let sparkRenderer = null;
  let visible = true;
  let loadGeneration = 0;
  let desiredLods = new Map();
  const active = new Map();
  const loading = new Map();
  const keyGenerations = new Map();
  const lruOrder = [];
  const disposedMeshes = new WeakSet();
  const stats = {
    cache_hits: 0,
    evicted: 0,
    failed: 0,
    loaded: 0,
  };

  function touch(key) {
    const index = lruOrder.indexOf(key);
    if (index >= 0) lruOrder.splice(index, 1);
    lruOrder.push(key);
  }

  function disposeMesh(mesh) {
    if (!mesh || disposedMeshes.has(mesh)) return;
    disposedMeshes.add(mesh);
    scene.remove(mesh);
    mesh.dispose?.();
  }

  function removeActive(key, { evicted = false } = {}) {
    const record = active.get(key);
    if (!record) return;
    active.delete(key);
    const index = lruOrder.indexOf(key);
    if (index >= 0) lruOrder.splice(index, 1);
    disposeMesh(record.mesh);
    if (evicted) stats.evicted += 1;
  }

  function cleanupObjects() {
    for (const record of active.values()) disposeMesh(record.mesh);
    for (const record of loading.values()) disposeMesh(record.mesh);
    active.clear();
    loading.clear();
    desiredLods.clear();
    lruOrder.length = 0;
    if (sparkRenderer) {
      scene.remove(sparkRenderer);
      sparkRenderer.dispose?.();
      sparkRenderer = null;
    }
    sparkModule = null;
    manifest = null;
    manifestUrl = null;
  }

  function snapshot() {
    return {
      ...state,
      active: active.size,
      loading: loading.size,
      cache_max: cacheMax,
      ...activeDensityEvidence(active),
      ...stats,
    };
  }

  async function load({
    manifest: nextManifest,
    manifestUrl: nextManifestUrl,
    visible: nextVisible = true,
  }) {
    const generation = ++loadGeneration;
    cleanupObjects();
    visible = nextVisible !== false;
    if (!isSpatialChunkManifest(nextManifest)) {
      state = fallbackState('invalid spatial-chunks manifest', nextManifestUrl);
      return snapshot();
    }

    state = fallbackState('Spark chunks loading', nextManifestUrl);
    try {
      const imported = await withTimeout(importSpark(), timeoutMs);
      if (generation !== loadGeneration) {
        return {
          ...snapshot(),
          mode: 'superseded',
          fidelity: 'unknown',
          reason: 'load superseded by a newer request',
          superseded: true,
        };
      }
      if (
        typeof imported?.SparkRenderer !== 'function'
        || typeof imported?.SplatMesh !== 'function'
      ) {
        throw new Error('Spark module is missing renderer exports');
      }

      sparkModule = imported;
      sparkRenderer = new sparkModule.SparkRenderer({ renderer });
      scene.add(sparkRenderer);
      manifest = nextManifest;
      manifestUrl = nextManifestUrl;
      state = sparkState(manifestUrl);
      return snapshot();
    } catch (error) {
      cleanupObjects();
      if (generation !== loadGeneration) {
        return {
          ...snapshot(),
          mode: 'superseded',
          fidelity: 'unknown',
          reason: 'load superseded by a newer request',
          superseded: true,
        };
      }
      const message = error instanceof Error ? error.message : String(error);
      state = fallbackState(`Spark chunks unavailable: ${message}`, nextManifestUrl);
      return snapshot();
    }
  }

  function cancelPending(key) {
    const record = loading.get(key);
    if (!record) return;
    keyGenerations.set(key, record.token + 1);
    loading.delete(key);
    disposeMesh(record.mesh);
  }

  function startChunkLoad(request, generation) {
    const {
      key, entry, lod, lodFraction, estimatedPointCount,
    } = request;
    const url = resolveSpatialChunkUrl(manifestUrl, entry, lod);
    if (!url) {
      state.last_error = `chunk ${key} has no safe LOD${lod} artifact`;
      stats.failed += 1;
      return Promise.resolve();
    }

    cancelPending(key);
    const token = (keyGenerations.get(key) ?? 0) + 1;
    keyGenerations.set(key, token);
    const mesh = new sparkModule.SplatMesh({ url });
    mesh.quaternion.set(
      ENU_TO_THREE_QUATERNION.x,
      ENU_TO_THREE_QUATERNION.y,
      ENU_TO_THREE_QUATERNION.z,
      ENU_TO_THREE_QUATERNION.w,
    );
    mesh.visible = visible && desiredLods.get(key) === lod;
    scene.add(mesh);

    const promise = (async () => {
      try {
        await withTimeout(Promise.resolve(mesh.initialized), timeoutMs);
        const current = (
          generation === loadGeneration
          && keyGenerations.get(key) === token
          && desiredLods.get(key) === lod
        );
        if (!current) {
          disposeMesh(mesh);
          return;
        }

        const old = active.get(key);
        active.set(key, {
          lod, lodFraction, estimatedPointCount, mesh, url,
        });
        if (old && old.mesh !== mesh) disposeMesh(old.mesh);
        mesh.visible = visible;
        touch(key);
        stats.loaded += 1;
      } catch (error) {
        disposeMesh(mesh);
        if (generation === loadGeneration && keyGenerations.get(key) === token) {
          const message = error instanceof Error ? error.message : String(error);
          state.last_error = `chunk ${key} LOD${lod}: ${message}`;
          stats.failed += 1;
        }
      } finally {
        if (loading.get(key)?.token === token) loading.delete(key);
      }
    })();
    loading.set(key, { lod, mesh, promise, token });
    return promise;
  }

  function evictOverflow() {
    while (active.size > cacheMax) {
      const victim = lruOrder.find((key) => !desiredLods.has(key)) ?? lruOrder[0];
      if (victim === undefined) break;
      removeActive(victim, { evicted: true });
    }
  }

  async function update({ cameraWorld, lodOverride = null }) {
    if (state.mode !== 'spark-chunks' || !manifest || !sparkModule) return snapshot();
    const generation = loadGeneration;
    const requests = selectSpatialChunkRequests(
      manifest,
      cameraWorld,
      { radiusChunks, lodOverride },
    ).slice(0, cacheMax);
    desiredLods = new Map(requests.map(({ key, lod }) => [key, lod]));

    for (const [key, record] of active) {
      record.mesh.visible = visible && desiredLods.has(key);
    }

    const pending = [];
    for (const request of requests) {
      const current = active.get(request.key);
      if (current?.lod === request.lod) {
        current.mesh.visible = visible;
        stats.cache_hits += 1;
        touch(request.key);
        continue;
      }
      const inFlight = loading.get(request.key);
      if (inFlight?.lod === request.lod) {
        pending.push(inFlight.promise);
        continue;
      }
      pending.push(startChunkLoad(request, generation));
    }

    await Promise.allSettled(pending);
    if (generation === loadGeneration) evictOverflow();
    return snapshot();
  }

  function setVisible(nextVisible) {
    visible = nextVisible !== false;
    for (const [key, record] of active) {
      record.mesh.visible = visible && desiredLods.has(key);
    }
    for (const [key, record] of loading) {
      record.mesh.visible = visible && desiredLods.has(key);
    }
  }

  function dispose() {
    loadGeneration += 1;
    cleanupObjects();
    state = fallbackState('disposed');
  }

  return {
    load,
    update,
    setVisible,
    dispose,
    getState: snapshot,
  };
}
