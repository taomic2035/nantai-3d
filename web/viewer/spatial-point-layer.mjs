import {
  isSpatialChunkManifest,
  resolveSpatialChunkUrl,
  selectSpatialChunkRequests,
} from './spatial-reconstruction.mjs';

function unloadedState(reason, manifestUrl = null) {
  return {
    mode: 'dc-point-preview',
    fidelity: 'dc-point-preview',
    manifest_url: manifestUrl,
    reason,
    last_error: null,
  };
}

function loadedState(manifestUrl) {
  return {
    mode: 'dc-point-chunks',
    fidelity: 'dc-point-preview',
    manifest_url: manifestUrl,
    reason: 'Spark unavailable; streaming DC point chunks',
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

export function createSpatialPointLayer({
  scene,
  loadPointMesh,
  disposeMesh,
  cacheMax = 36,
  radiusChunks = 2,
}) {
  if (typeof loadPointMesh !== 'function' || typeof disposeMesh !== 'function') {
    throw new TypeError('point layer requires loadPointMesh and disposeMesh');
  }
  if (!Number.isInteger(cacheMax) || cacheMax < 1) {
    throw new RangeError('cacheMax must be a positive integer');
  }
  if (!Number.isInteger(radiusChunks) || radiusChunks < 0) {
    throw new RangeError('radiusChunks must be a non-negative integer');
  }

  let state = unloadedState('not loaded');
  let manifest = null;
  let manifestUrl = null;
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

  function touch(key) {
    const index = lruOrder.indexOf(key);
    if (index >= 0) lruOrder.splice(index, 1);
    lruOrder.push(key);
  }

  function disposeOne(mesh) {
    if (!mesh || disposedMeshes.has(mesh)) return;
    disposedMeshes.add(mesh);
    scene.remove(mesh);
    disposeMesh(mesh);
  }

  function removeActive(key, { evicted = false } = {}) {
    const record = active.get(key);
    if (!record) return;
    active.delete(key);
    const index = lruOrder.indexOf(key);
    if (index >= 0) lruOrder.splice(index, 1);
    disposeOne(record.mesh);
    if (evicted) stats.evicted += 1;
  }

  function cleanup() {
    for (const record of active.values()) disposeOne(record.mesh);
    active.clear();
    loading.clear();
    desiredLods.clear();
    lruOrder.length = 0;
    manifest = null;
    manifestUrl = null;
  }

  function load({
    manifest: nextManifest,
    manifestUrl: nextManifestUrl,
    visible: nextVisible = true,
  }) {
    loadGeneration += 1;
    cleanup();
    visible = nextVisible !== false;
    if (!isSpatialChunkManifest(nextManifest)) {
      state = unloadedState('invalid spatial-chunks manifest', nextManifestUrl);
      return snapshot();
    }
    manifest = nextManifest;
    manifestUrl = nextManifestUrl;
    state = loadedState(manifestUrl);
    return snapshot();
  }

  function cancelPending(key) {
    const record = loading.get(key);
    if (!record) return;
    keyGenerations.set(key, record.token + 1);
    loading.delete(key);
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
    const promise = (async () => {
      let mesh = null;
      try {
        mesh = await loadPointMesh({
          entry, key, lod, lodFraction, estimatedPointCount, url,
        });
        const current = (
          generation === loadGeneration
          && keyGenerations.get(key) === token
          && desiredLods.get(key) === lod
        );
        if (!current) {
          disposeOne(mesh);
          return;
        }

        mesh.visible = visible;
        scene.add(mesh);
        const old = active.get(key);
        active.set(key, {
          lod, lodFraction, estimatedPointCount, mesh, url,
        });
        if (old && old.mesh !== mesh) disposeOne(old.mesh);
        touch(key);
        stats.loaded += 1;
      } catch (error) {
        if (mesh) disposeOne(mesh);
        if (generation === loadGeneration && keyGenerations.get(key) === token) {
          const message = error instanceof Error ? error.message : String(error);
          state.last_error = `chunk ${key} LOD${lod}: ${message}`;
          stats.failed += 1;
        }
      } finally {
        if (loading.get(key)?.token === token) loading.delete(key);
      }
    })();
    loading.set(key, { lod, promise, token });
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
    if (state.mode !== 'dc-point-chunks' || !manifest) return snapshot();
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
  }

  function dispose() {
    loadGeneration += 1;
    cleanup();
    state = unloadedState('disposed');
  }

  return {
    load,
    update,
    setVisible,
    dispose,
    getState: snapshot,
  };
}
