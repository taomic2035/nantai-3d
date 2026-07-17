const LOD_LEVELS = Object.freeze([0, 1, 2]);

function validAabb(aabb) {
  return (
    aabb
    && ['min', 'max'].every(
      (key) => (
        Array.isArray(aabb[key])
        && aabb[key].length >= 3
        && aabb[key].slice(0, 3).every(Number.isFinite)
      ),
    )
    && aabb.min.slice(0, 3).every((value, index) => value <= aabb.max[index])
  );
}

function safeRelativeArtifactPath(path) {
  if (
    typeof path !== 'string'
    || path.length === 0
    || path.startsWith('/')
    || path.includes('\\')
    || path.includes(':')
    || /[?#]/.test(path)
  ) return false;

  const parts = path.split('/');
  try {
    return parts.every((part) => {
      const decoded = decodeURIComponent(part);
      return (
        decoded.length > 0
        && decoded !== '.'
        && decoded !== '..'
        && !/[\\/]/.test(decoded)
      );
    });
  } catch {
    return false;
  }
}

function bakedChunkPath(entry, lod) {
  if (!entry || typeof entry !== 'object') return null;
  const lodPath = entry.lod?.[String(lod)];
  if (safeRelativeArtifactPath(lodPath)) return lodPath;
  return safeRelativeArtifactPath(entry.ply_file) ? entry.ply_file : null;
}

export function declaredLodFraction(manifest, lod) {
  const fraction = manifest?.lod_fractions?.[String(lod)];
  return Number.isFinite(fraction) && fraction > 0 && fraction <= 1
    ? fraction
    : null;
}

export function estimatedLodPointCount(entry, lodFraction) {
  if (
    !Number.isSafeInteger(entry?.point_count)
    || entry.point_count <= 0
    || !Number.isFinite(lodFraction)
    || lodFraction <= 0
    || lodFraction > 1
  ) return null;
  return Math.ceil(entry.point_count * lodFraction);
}

export function isSpatialChunkManifest(manifest) {
  if (
    manifest?.schema_version !== 1
    || manifest.kind !== 'spatial-chunks'
    || Object.hasOwn(manifest, 'grid')
    || !Number.isFinite(manifest.chunk_size_m)
    || manifest.chunk_size_m <= 0
    || !Array.isArray(manifest.chunks)
    || manifest.chunks.length === 0
  ) return false;

  const coordinates = new Set();
  for (const entry of manifest.chunks) {
    if (
      !Number.isSafeInteger(entry?.x)
      || !Number.isSafeInteger(entry?.y)
      || !validAabb(entry?.aabb)
      || !LOD_LEVELS.some((lod) => bakedChunkPath(entry, lod))
    ) return false;
    const key = `${entry.x}_${entry.y}`;
    if (coordinates.has(key)) return false;
    coordinates.add(key);
  }
  return true;
}

export function resolveSpatialChunkUrl(manifestUrl, entry, lod) {
  if (!LOD_LEVELS.includes(lod)) {
    throw new RangeError(`unsupported reconstruction chunk LOD: ${lod}`);
  }
  const path = bakedChunkPath(entry, lod);
  return path ? new URL(path, manifestUrl).href : null;
}

export function horizontalDistanceToAabb(cameraWorld, aabb) {
  if (
    !Array.isArray(cameraWorld)
    || cameraWorld.length < 2
    || !cameraWorld.slice(0, 2).every(Number.isFinite)
    || !validAabb(aabb)
  ) {
    throw new TypeError('camera and AABB must contain finite coordinates');
  }
  const [east, north] = cameraWorld;
  const dx = Math.max(aabb.min[0] - east, 0, east - aabb.max[0]);
  const dy = Math.max(aabb.min[1] - north, 0, north - aabb.max[1]);
  return Math.hypot(dx, dy);
}

export function selectSpatialChunkRequests(
  manifest,
  cameraWorld,
  { radiusChunks = 2, lodOverride = null } = {},
) {
  if (!isSpatialChunkManifest(manifest)) {
    throw new TypeError('invalid spatial-chunks manifest');
  }
  if (!Number.isInteger(radiusChunks) || radiusChunks < 0) {
    throw new RangeError('radiusChunks must be a non-negative integer');
  }
  if (lodOverride !== null && !LOD_LEVELS.includes(lodOverride)) {
    throw new RangeError(`unsupported reconstruction chunk LOD: ${lodOverride}`);
  }
  if (
    !Array.isArray(cameraWorld)
    || cameraWorld.length < 3
    || !cameraWorld.slice(0, 3).every(Number.isFinite)
  ) {
    throw new TypeError('cameraWorld must contain three finite coordinates');
  }

  const chunkSize = manifest.chunk_size_m;
  const maxDistance = chunkSize * radiusChunks;
  return manifest.chunks
    .map((entry) => {
      const distance = horizontalDistanceToAabb(cameraWorld, entry.aabb);
      const lod = lodOverride ?? (
        distance < chunkSize * 0.5 ? 2
          : distance < chunkSize * 1.5 ? 1 : 0
      );
      const lodFraction = declaredLodFraction(manifest, lod);
      return {
        key: `${entry.x}_${entry.y}`,
        entry,
        distance,
        lod,
        lodFraction,
        estimatedPointCount: estimatedLodPointCount(entry, lodFraction),
      };
    })
    .filter(({ distance }) => distance <= maxDistance)
    .sort((left, right) => (
      left.distance - right.distance || left.key.localeCompare(right.key)
    ));
}
