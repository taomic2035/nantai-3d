import assert from 'node:assert/strict';
import test from 'node:test';

let coordinates;
try {
  coordinates = await import('./coordinates.mjs');
} catch (error) {
  coordinates = { __loadError: error };
}

function subject() {
  assert.equal(
    coordinates.__loadError,
    undefined,
    `coordinates.mjs must load: ${coordinates.__loadError?.message}`,
  );
  return coordinates;
}

function determinant3(matrix) {
  const [a, b, c] = matrix;
  return (
    a[0] * (b[1] * c[2] - b[2] * c[1])
    - b[0] * (a[1] * c[2] - a[2] * c[1])
    + c[0] * (a[1] * b[2] - a[2] * b[1])
  );
}

test('worldToThree maps right-handed ENU to Three as E,U,-N', () => {
  const { worldToThree } = subject();
  assert.deepEqual(worldToThree([12, 34, 5]), [12, 5, -34]);

  const matrixColumns = [
    worldToThree([1, 0, 0]),
    worldToThree([0, 1, 0]),
    worldToThree([0, 0, 1]),
  ];
  assert.equal(determinant3(matrixColumns), 1);
});

test('threeToWorld is the exact inverse of worldToThree', () => {
  const { threeToWorld, worldToThree } = subject();
  const point = [-48.5, 17.25, 3];
  assert.deepEqual(threeToWorld(worldToThree(point)), point);
});

test('transformPositionsInPlace converts every packed ENU position', () => {
  const { transformPositionsInPlace } = subject();
  const positions = new Float32Array([1, 2, 3, -4, -5, 6]);
  assert.equal(transformPositionsInPlace(positions), positions);
  assert.deepEqual([...positions], [1, 3, -2, -4, 6, 5]);
});

test('threeToChunk handles negative ENU chunks and non-200m chunk sizes', () => {
  const { threeToChunk, worldToThree } = subject();
  assert.deepEqual(threeToChunk(worldToThree([-1, -1, 0]), 50), [-1, -1]);
  assert.deepEqual(threeToChunk(worldToThree([149, 99, 8]), 50), [2, 1]);
});

test('horizontalDistanceToWorld uses east and north rather than Three z sign', () => {
  const { horizontalDistanceToWorld } = subject();
  assert.equal(horizontalDistanceToWorld([10, 7, -20], [13, 24]), 5);
});
