/** Convert right-handed world ENU coordinates to Three.js X/Y/Z. */
export function worldToThree([east, north, up]) {
  return [east, up, -north];
}

/** Convert Three.js X/Y/Z back to right-handed world ENU coordinates. */
export function threeToWorld([x, y, z]) {
  return [x, -z, y];
}

/** Convert a packed xyz buffer from ENU to Three.js in place. */
export function transformPositionsInPlace(positions) {
  for (let index = 0; index < positions.length; index += 3) {
    const north = positions[index + 1];
    positions[index + 1] = positions[index + 2];
    positions[index + 2] = -north;
  }
  return positions;
}

/** Resolve a Three.js position to the corresponding ENU chunk index. */
export function threeToChunk(position, chunkSize) {
  const [east, north] = threeToWorld(position);
  return [Math.floor(east / chunkSize), Math.floor(north / chunkSize)];
}

/** Horizontal distance from a Three.js position to an ENU east/north point. */
export function horizontalDistanceToWorld(position, [east, north]) {
  const [cameraEast, cameraNorth] = threeToWorld(position);
  return Math.hypot(cameraEast - east, cameraNorth - north);
}
