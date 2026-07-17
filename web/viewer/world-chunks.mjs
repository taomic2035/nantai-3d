const ON_DEMAND_URL_TEMPLATE = '/api/world/chunk/{x}/{y}.ply';

function isValidOnDemandGrid(manifest) {
  const grid = manifest?.grid;
  return (
    grid?.on_demand === true
    && grid.url_template === ON_DEMAND_URL_TEMPLATE
    && Number.isSafeInteger(grid.world_seed)
  );
}

function assertSchedulerInputs(chunkX, chunkY, lod) {
  if (!Number.isSafeInteger(chunkX) || !Number.isSafeInteger(chunkY)) {
    throw new TypeError('chunk coordinates must be safe integers');
  }
  if (![0, 1, 2].includes(lod)) {
    throw new RangeError(`unsupported world chunk LOD: ${lod}`);
  }
}

function bakedChunkPath(entry, lod) {
  if (!entry || typeof entry !== 'object') return null;
  const lodPath = entry.lod?.[String(lod)];
  if (typeof lodPath === 'string' && lodPath) return lodPath;
  return typeof entry.ply_file === 'string' && entry.ply_file
    ? entry.ply_file
    : null;
}

export function worldChunkAvailable(manifest, hasBakedEntry) {
  return Boolean(hasBakedEntry) || isValidOnDemandGrid(manifest);
}

export function shouldRetryWorldChunkFailure(error) {
  return !(
    error?.status === 422
    && error.apiCode === 'world_bounds_exceeded'
  );
}

/** Resolve a baked chunk first, then the exact same-origin on-demand contract. */
export function resolveWorldChunkSource(manifest, entry, chunkX, chunkY, lod) {
  assertSchedulerInputs(chunkX, chunkY, lod);
  const bakedPath = bakedChunkPath(entry, lod);
  if (bakedPath) return { path: bakedPath, onDemand: false };
  if (!isValidOnDemandGrid(manifest)) return null;
  const path = ON_DEMAND_URL_TEMPLATE
    .replace('{x}', String(chunkX))
    .replace('{y}', String(chunkY));
  return { path: `${path}?lod=${lod}`, onDemand: true };
}
