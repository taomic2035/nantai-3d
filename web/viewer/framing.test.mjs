import assert from 'node:assert/strict';
import test from 'node:test';

let framing;
try {
  framing = await import('./framing.mjs');
} catch (error) {
  framing = { __loadError: error };
}

function subject() {
  assert.equal(
    framing.__loadError,
    undefined,
    `framing.mjs must load: ${framing.__loadError?.message}`,
  );
  return framing;
}

test('computeWorldBounds supports negative, non-square, non-200m chunk manifests', () => {
  const { computeWorldBounds } = subject();
  const manifest = {
    chunk_size_m: 50,
    chunks: [{ x: -2, y: 3 }, { x: 1, y: 4 }],
  };
  assert.deepEqual(computeWorldBounds(manifest), {
    min: [-100, 150, 0],
    max: [100, 250, 0],
  });
});

test('computeFraming centers a single chunk without origin assumptions', () => {
  const { computeFraming } = subject();
  const frame = computeFraming({
    chunk_size_m: 64,
    chunks: [{ x: 3, y: -2 }],
  });

  assert.deepEqual(frame.bounds, {
    min: [192, -128, 0],
    max: [256, -64, 0],
  });
  assert.deepEqual(frame.centerWorld, [224, -96, 0]);
  assert.deepEqual(frame.targetThree, [224, 0, 96]);
  assert.equal(frame.gridSize, 64);
  assert.equal(frame.gridDivisions, 1);
  assert.ok(frame.near > 0 && frame.near < frame.far);

  const cameraDistance = Math.hypot(
    frame.cameraPositionThree[0] - frame.targetThree[0],
    frame.cameraPositionThree[1] - frame.targetThree[1],
    frame.cameraPositionThree[2] - frame.targetThree[2],
  );
  assert.ok(Math.abs(cameraDistance - frame.cameraDistance) < 1e-9);
});

test('computeFraming unions reconstruction bounds with chunk bounds', () => {
  const { computeFraming } = subject();
  const frame = computeFraming(
    { chunk_size_m: 200, chunks: [{ x: 0, y: 0 }] },
    { min: [-50, -25, -5], max: [350, 400, 30] },
  );
  assert.deepEqual(frame.bounds, {
    min: [-50, -25, -5],
    max: [350, 400, 30],
  });
  assert.deepEqual(frame.centerWorld, [150, 187.5, 12.5]);
  assert.deepEqual(frame.targetThree, [150, 12.5, -187.5]);
  assert.equal(frame.gridSize, 600);
  assert.ok(frame.fogNear < frame.fogFar);
  assert.ok(frame.far > frame.cameraDistance);
});

test('worldToMinimap keeps north at the top of the canvas', () => {
  const { worldToMinimap } = subject();
  const bounds = { min: [-100, 50, 0], max: [100, 250, 0] };
  assert.deepEqual(worldToMinimap([-100, 250], bounds, 180, 120), [0, 0]);
  assert.deepEqual(worldToMinimap([100, 50], bounds, 180, 120), [180, 120]);
});

test('selectReconLod measures distance in ENU after Three conversion', () => {
  const { selectReconLod } = subject();
  const reconBounds = { min: [0, 100, 0], max: [20, 120, 10] };
  assert.equal(selectReconLod([10, 30, -110], reconBounds), 2);
  assert.equal(selectReconLod([210, 30, -110], reconBounds), 1);
  assert.equal(selectReconLod([510, 30, -110], reconBounds), 0);
});
