import assert from 'node:assert/strict';
import test from 'node:test';

let cameraPose;
try {
  cameraPose = await import('./camera-pose.mjs');
} catch (error) {
  cameraPose = { __loadError: error };
}

function subject() {
  assert.equal(
    cameraPose.__loadError,
    undefined,
    `camera-pose.mjs must load: ${cameraPose.__loadError?.message}`,
  );
  return cameraPose;
}

const NO_KEYS = { w: false, s: false, a: false, d: false, q: false, e: false };

test('yaw/pitch 与方向互逆 (含负角多组)', () => {
  const {
    yawPitchFromDirectionThree,
    directionFromYawPitchThree,
  } = subject();
  const cases = [
    { yaw: 0, pitch: 0 },
    { yaw: 0.7, pitch: 0.4 },
    { yaw: -1.2, pitch: -0.6 },
    { yaw: 2.9, pitch: 0.9 },
    { yaw: -2.9, pitch: -0.9 },
  ];
  for (const { yaw, pitch } of cases) {
    const dir = directionFromYawPitchThree(yaw, pitch);
    // 单位向量
    assert.ok(Math.abs(Math.hypot(...dir) - 1) < 1e-9, `dir 应为单位向量 (${yaw},${pitch})`);
    const back = yawPitchFromDirectionThree(dir);
    assert.ok(Math.abs(back.yaw - yaw) < 1e-9, `yaw 还原 (${yaw})`);
    assert.ok(Math.abs(back.pitch - pitch) < 1e-9, `pitch 还原 (${pitch})`);
  }
});

test('yaw=0 朝世界北 (-Z)', () => {
  const { directionFromYawPitchThree } = subject();
  assert.deepEqual(
    directionFromYawPitchThree(0, 0).map((v) => Math.round(v * 1e9) / 1e9),
    [0, 0, -1],
  );
});

test('clampPitch 钳到 ±MAX_PITCH_RAD', () => {
  const { clampPitch, MAX_PITCH_RAD } = subject();
  assert.equal(clampPitch(10), MAX_PITCH_RAD);
  assert.equal(clampPitch(-10), -MAX_PITCH_RAD);
  assert.equal(clampPitch(0.3), 0.3);
  assert.ok(MAX_PITCH_RAD < Math.PI / 2);
});

test('yawPitchFromDirectionThree 零向量 / 非有限数抛错', () => {
  const { yawPitchFromDirectionThree } = subject();
  assert.throws(() => yawPitchFromDirectionThree([0, 0, 0]), /零向量/);
  assert.throws(() => yawPitchFromDirectionThree([NaN, 1, 0]), /有限数/);
  assert.throws(() => yawPitchFromDirectionThree([1, Infinity, 0]), /有限数/);
});

test('flyDisplacementThree 6-DOF: 抬头 45° 按 w 上升 (dy>0)', () => {
  const { flyDisplacementThree } = subject();
  const [dx, dy, dz] = flyDisplacementThree(0, Math.PI / 4, { ...NO_KEYS, w: true }, 10);
  assert.ok(dy > 0, 'w 沿完整视线 → dy>0');
  assert.ok(Math.abs(dz) > 0, '仍有水平分量');
  // yaw=0 时水平分量沿 -Z
  assert.ok(dz < 0, '朝北 (-Z)');
  assert.ok(Math.abs(dx) < 1e-9);
});

test('flyDisplacementThree w/s 相反、q 降 e 升、组合叠加', () => {
  const { flyDisplacementThree } = subject();
  const fwd = flyDisplacementThree(0.5, 0.2, { ...NO_KEYS, w: true }, 5);
  const bwd = flyDisplacementThree(0.5, 0.2, { ...NO_KEYS, s: true }, 5);
  for (let i = 0; i < 3; i++) {
    assert.ok(Math.abs(fwd[i] + bwd[i]) < 1e-12, 's 应与 w 相反');
  }
  const [, qy] = flyDisplacementThree(0, 0, { ...NO_KEYS, q: true }, 3);
  const [, ey] = flyDisplacementThree(0, 0, { ...NO_KEYS, e: true }, 3);
  assert.ok(qy < 0, 'q 降');
  assert.ok(ey > 0, 'e 升');

  // w + e 叠加 = w 单独 + e 单独
  const solo = flyDisplacementThree(0.3, 0.1, { ...NO_KEYS, w: true }, 2);
  const lift = flyDisplacementThree(0.3, 0.1, { ...NO_KEYS, e: true }, 2);
  const both = flyDisplacementThree(0.3, 0.1, { ...NO_KEYS, w: true, e: true }, 2);
  for (let i = 0; i < 3; i++) {
    assert.ok(Math.abs(both[i] - (solo[i] + lift[i])) < 1e-12, '可组合叠加');
  }
});

test('flyDisplacementThree a/d 水平且与视线正交 (yaw=0)', () => {
  const { flyDisplacementThree } = subject();
  const [dx, dy, dz] = flyDisplacementThree(0, 0, { ...NO_KEYS, d: true }, 4);
  // yaw=0 视线沿 -Z，右向应沿 +X
  assert.ok(Math.abs(dx - 4) < 1e-9, 'd 沿 +X');
  assert.ok(Math.abs(dy) < 1e-12, 'a/d 保持水平');
  assert.ok(Math.abs(dz) < 1e-9);
});

test('flyDisplacementThree 视线近竖直时退化保护 (right 仍水平且有限)', () => {
  const { flyDisplacementThree, MAX_PITCH_RAD } = subject();
  // 接近正上方 (pitch→90°)，cross(forward, up) 趋零，需退化到 [cos(yaw),0,sin(yaw)]
  const [dx, dy, dz] = flyDisplacementThree(0.9, MAX_PITCH_RAD, { ...NO_KEYS, d: true }, 1);
  assert.ok(Number.isFinite(dx) && Number.isFinite(dy) && Number.isFinite(dz));
  assert.ok(Math.abs(dy) < 1e-12, '退化后 a/d 仍水平');
  const horizontal = Math.hypot(dx, dz);
  assert.ok(Math.abs(horizontal - 1) < 1e-9, '退化后为单位水平右向');
  assert.ok(Math.abs(dx - Math.cos(0.9)) < 1e-9);
  assert.ok(Math.abs(dz - Math.sin(0.9)) < 1e-9);
});

test('parseEnuText 正例 (含空格)', () => {
  const { parseEnuText } = subject();
  assert.deepEqual(parseEnuText('10, 20, 5'), { east: 10, north: 20, up: 5 });
  assert.deepEqual(parseEnuText(' -3.5 ,0,  2.25 '), { east: -3.5, north: 0, up: 2.25 });
});

test('parseEnuText 误例抛中文错误', () => {
  const { parseEnuText } = subject();
  assert.throws(() => parseEnuText('1,2'), /3 段/);
  assert.throws(() => parseEnuText('1,2,3,4'), /3 段/);
  assert.throws(() => parseEnuText('1,,3'), /3 段/);
  assert.throws(() => parseEnuText('a,2,3'), /east 不是有效数字/);
  assert.throws(() => parseEnuText('1,b,3'), /north 不是有效数字/);
  assert.throws(() => parseEnuText(42), /文本/);
});

test('normalizeCameraPose 正例: ENU→Three 映射 [e,u,-n]', () => {
  const { normalizeCameraPose } = subject();
  const withoutLook = normalizeCameraPose({ position: { east: 10, north: 20, up: 5 } });
  assert.deepEqual(withoutLook.positionThree, [10, 5, -20]);
  assert.equal(withoutLook.lookAtThree, null);

  const withLook = normalizeCameraPose({
    position: { east: 1, north: 2, up: 3 },
    look_at: { east: 4, north: 5, up: 6 },
  });
  assert.deepEqual(withLook.positionThree, [1, 3, -2]);
  assert.deepEqual(withLook.lookAtThree, [4, 6, -5]);
});

test('normalizeCameraPose 误例抛中文错误', () => {
  const { normalizeCameraPose } = subject();
  assert.throws(() => normalizeCameraPose(null), /position/);
  assert.throws(() => normalizeCameraPose({}), /position/);
  assert.throws(() => normalizeCameraPose({ position: { east: 1, north: 2 } }), /position\.up/);
  assert.throws(
    () => normalizeCameraPose({ position: { east: NaN, north: 2, up: 3 } }),
    /position\.east/,
  );
  assert.throws(
    () => normalizeCameraPose({
      position: { east: 1, north: 2, up: 3 },
      look_at: { east: 4, north: 5 },
    }),
    /look_at\.up/,
  );
});
