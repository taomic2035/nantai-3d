import { horizontalDistanceToWorld, worldToThree } from './coordinates.mjs';

function isBounds(value) {
  return (
    value
    && Array.isArray(value.min)
    && value.min.length >= 3
    && Array.isArray(value.max)
    && value.max.length >= 3
    && value.min.slice(0, 3).every(Number.isFinite)
    && value.max.slice(0, 3).every(Number.isFinite)
    && value.min.slice(0, 3).every((item, index) => item <= value.max[index])
  );
}

function copyBounds(value) {
  return isBounds(value)
    ? { min: value.min.slice(0, 3), max: value.max.slice(0, 3) }
    : null;
}

function unionBounds(left, right) {
  if (!left) return right;
  if (!right) return left;
  return {
    min: left.min.map((value, index) => Math.min(value, right.min[index])),
    max: left.max.map((value, index) => Math.max(value, right.max[index])),
  };
}

/** Compute ENU world bounds from manifest chunk indices. */
export function computeWorldBounds(manifest) {
  const chunks = manifest?.chunks ?? [];
  if (chunks.length === 0) throw new Error('manifest has no chunks');

  const declaredBounds = copyBounds(manifest?.bounds);
  if (declaredBounds) return declaredBounds;

  const chunkSize = Number.isFinite(manifest.chunk_size_m) && manifest.chunk_size_m > 0
    ? manifest.chunk_size_m
    : 200;
  const xs = chunks.map((chunk) => chunk.x);
  const ys = chunks.map((chunk) => chunk.y);
  if (![...xs, ...ys].every(Number.isFinite)) {
    throw new Error('manifest has invalid chunk coordinates');
  }
  const chunkBounds = chunks.map((chunk) => copyBounds(chunk.aabb)).filter(Boolean);
  const minZ = chunkBounds.length > 0
    ? Math.min(...chunkBounds.map((bounds) => bounds.min[2]))
    : 0;
  const maxZ = chunkBounds.length > 0
    ? Math.max(...chunkBounds.map((bounds) => bounds.max[2]))
    : 0;
  let bounds = {
    min: [Math.min(...xs) * chunkSize, Math.min(...ys) * chunkSize, minZ],
    max: [(Math.max(...xs) + 1) * chunkSize, (Math.max(...ys) + 1) * chunkSize, maxZ],
  };
  for (const aabb of chunkBounds) bounds = unionBounds(bounds, aabb);
  return bounds;
}

/** Derive camera, clipping, fog, grid and target values from artifact bounds. */
export function computeFraming(manifest, reconBounds = null, fovDegrees = 65) {
  const chunks = manifest?.chunks ?? [];
  const chunkSize = manifest?.chunk_size_m ?? 200;
  const chunkBounds = chunks.length > 0 ? computeWorldBounds(manifest) : null;
  const validReconBounds = copyBounds(reconBounds);
  const bounds = unionBounds(chunkBounds, validReconBounds);
  if (!bounds) throw new Error('cannot frame an empty world');

  const spans = bounds.max.map((value, index) => value - bounds.min[index]);
  const centerWorld = bounds.min.map(
    (value, index) => (value + bounds.max[index]) / 2,
  );
  const targetThree = worldToThree(centerWorld);
  const horizontalSpan = Math.max(spans[0], spans[1], chunkSize);
  const gridSize = Math.max(
    chunkSize,
    Math.ceil(horizontalSpan / chunkSize) * chunkSize,
  );
  const gridDivisions = Math.max(1, Math.round(gridSize / chunkSize));
  const fitRadius = Math.max(
    chunkSize / 2,
    Math.hypot(spans[0], spans[1], Math.max(spans[2], chunkSize * 0.05)) / 2,
  );
  const halfFovRadians = (fovDegrees * Math.PI / 180) / 2;
  const cameraDistance = Math.max(
    chunkSize * 1.2,
    (fitRadius / Math.sin(halfFovRadians)) * 1.2,
  );
  const direction = [0.58, 0.64, 0.5];
  const directionLength = Math.hypot(...direction);
  const cameraPositionThree = targetThree.map(
    (value, index) => value + (direction[index] / directionLength) * cameraDistance,
  );
  const near = Math.max(0.01, chunkSize * 0.0001, cameraDistance * 0.001);
  const far = Math.max(chunkSize * 8, cameraDistance * 6 + fitRadius * 2);
  const fogNear = Math.max(chunkSize / 2, cameraDistance * 0.9);
  const fogFar = Math.max(fogNear + chunkSize, cameraDistance * 3 + fitRadius);

  return {
    bounds,
    centerWorld,
    targetThree,
    gridCenterThree: worldToThree([centerWorld[0], centerWorld[1], 0]),
    gridSize,
    gridDivisions,
    cameraDistance,
    cameraPositionThree,
    near,
    far,
    fogNear,
    fogFar,
  };
}

/** Map ENU east/north to minimap pixels, with north at the canvas top. */
export function worldToMinimap([east, north], bounds, width, height) {
  const eastSpan = bounds.max[0] - bounds.min[0];
  const northSpan = bounds.max[1] - bounds.min[1];
  return [
    ((east - bounds.min[0]) / eastSpan) * width,
    ((bounds.max[1] - north) / northSpan) * height,
  ];
}

/** Select reconstruction LOD from horizontal ENU distance. */
export function selectReconLod(
  cameraPositionThree,
  reconBounds,
  thresholds = { high: 150, medium: 400 },
) {
  const center = [
    (reconBounds.min[0] + reconBounds.max[0]) / 2,
    (reconBounds.min[1] + reconBounds.max[1]) / 2,
  ];
  const distance = horizontalDistanceToWorld(cameraPositionThree, center);
  if (distance < thresholds.high) return 2;
  if (distance < thresholds.medium) return 1;
  return 0;
}
