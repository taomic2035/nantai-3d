/**
 * 相机姿态纯逻辑 (自由视角 + 传送)。
 *
 * 本模块刻意不 import three / 不触碰 DOM，方便 node:test 单测。
 * Three 空间约定 (与 coordinates.mjs 一致): +Y = 世界 up，-Z = 世界 north。
 * yaw=0 朝 -Z (世界北)，pitch>0 抬头。
 */

// 俯仰角上限 89.9°，避免在正上/正下方向丢失 yaw (万向锁)。
export const MAX_PITCH_RAD = (89.9 * Math.PI) / 180;

function clampRange(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

/** 将俯仰角钳到 ±MAX_PITCH_RAD。 */
export function clampPitch(pitch) {
  return clampRange(pitch, -MAX_PITCH_RAD, MAX_PITCH_RAD);
}

/**
 * Three 视线方向 → {yaw, pitch}。
 * pitch = asin(y/|v|)，yaw = atan2(x, -z)。零向量 / 非有限数抛错。
 */
export function yawPitchFromDirectionThree([x, y, z]) {
  if (![x, y, z].every(Number.isFinite)) {
    throw new Error('方向向量必须是有限数');
  }
  const length = Math.hypot(x, y, z);
  if (length < 1e-9) {
    throw new Error('方向向量不能为零向量');
  }
  const pitch = Math.asin(clampRange(y / length, -1, 1));
  const yaw = Math.atan2(x, -z);
  return { yaw, pitch };
}

/**
 * {yaw, pitch} → 单位 Three 视线方向，是 yawPitchFromDirectionThree 的逆。
 * [sin(yaw)cos(pitch), sin(pitch), -cos(yaw)cos(pitch)]
 */
export function directionFromYawPitchThree(yaw, pitch) {
  const cosPitch = Math.cos(pitch);
  return [
    Math.sin(yaw) * cosPitch,
    Math.sin(pitch),
    -Math.cos(yaw) * cosPitch,
  ];
}

/**
 * 6-DOF 飞行位移 (Three 空间)。keysLike.{w,s,a,d,q,e} 可组合叠加：
 * - w/s 沿完整视线方向 (不压平，抬头按 w 会上升)
 * - a/d 沿 right = normalize(cross(forward, worldUp))；forward 近竖直时退化，
 *   回退到仅由 yaw 决定的水平右向 [cos(yaw), 0, sin(yaw)]
 * - q/e 沿世界竖直方向 (q 降 e 升)
 */
export function flyDisplacementThree(yaw, pitch, keysLike, distance) {
  const forward = directionFromYawPitchThree(yaw, pitch);
  // cross(forward, [0,1,0]) = [-fz, 0, fx]
  let rightX = -forward[2];
  let rightZ = forward[0];
  const rightLen = Math.hypot(rightX, rightZ);
  if (rightLen < 1e-6) {
    // forward 近竖直 → 退化保护
    rightX = Math.cos(yaw);
    rightZ = Math.sin(yaw);
  } else {
    rightX /= rightLen;
    rightZ /= rightLen;
  }

  let dx = 0;
  let dy = 0;
  let dz = 0;
  if (keysLike.w) { dx += forward[0]; dy += forward[1]; dz += forward[2]; }
  if (keysLike.s) { dx -= forward[0]; dy -= forward[1]; dz -= forward[2]; }
  if (keysLike.d) { dx += rightX; dz += rightZ; }
  if (keysLike.a) { dx -= rightX; dz -= rightZ; }
  if (keysLike.e) { dy += 1; }
  if (keysLike.q) { dy -= 1; }
  return [dx * distance, dy * distance, dz * distance];
}

/** 解析 "E,N,U" 文本 (允许空格) 为 {east, north, up}；非 3 段或非有限数抛中文错误。 */
export function parseEnuText(text) {
  if (typeof text !== 'string') {
    throw new Error('传送坐标必须是 "E,N,U" 文本');
  }
  const parts = text.split(',').map((segment) => segment.trim());
  if (parts.length !== 3 || parts.some((segment) => segment === '')) {
    throw new Error('传送坐标需要 3 段 "E,N,U"（逗号分隔）');
  }
  const [east, north, up] = parts.map(Number);
  for (const [name, value] of [['east', east], ['north', north], ['up', up]]) {
    if (!Number.isFinite(value)) {
      throw new Error(`传送坐标 ${name} 不是有效数字`);
    }
  }
  return { east, north, up };
}

function requireEnu(value, label) {
  if (!value || typeof value !== 'object') {
    throw new Error(`${label} 需要 {east, north, up} 对象`);
  }
  for (const name of ['east', 'north', 'up']) {
    const component = value[name];
    if (typeof component !== 'number' || !Number.isFinite(component)) {
      throw new Error(`${label}.${name} 必须是有限数`);
    }
  }
  return value;
}

// ENU → Three，内联 [east, up, -north]，与 coordinates.mjs worldToThree 契约一致
// (本模块保持零依赖，故不 import 而是复述该公式)。
function enuToThree({ east, north, up }) {
  return [east, up, -north];
}

/**
 * 校验 {position:{east,north,up}, look_at?:{east,north,up}}，字段缺失/非有限数抛中文错误。
 * 返回 {positionThree, lookAtThree|null} (ENU→Three 映射与 coordinates.mjs 一致)。
 */
export function normalizeCameraPose(payload) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('相机姿态需要 {position:{east,north,up}} 对象');
  }
  const position = requireEnu(payload.position, 'position');
  const positionThree = enuToThree(position);
  let lookAtThree = null;
  if (payload.look_at !== undefined && payload.look_at !== null) {
    lookAtThree = enuToThree(requireEnu(payload.look_at, 'look_at'));
  }
  return { positionThree, lookAtThree };
}
