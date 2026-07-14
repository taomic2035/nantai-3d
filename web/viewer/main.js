/**
 * Nantai Village - Three.js Web Viewer (动态 chunk 加载版)
 *
 * 特性:
 * - 按相机位置动态加载/卸载 chunk (LRU + 视野半径)
 * - 实时 mini-map 显示已加载 chunk + 玩家位置
 * - WASD + 鼠标相机控制
 * - HUD 显示活跃 chunk 数 / 已淘汰数 / 当前坐标
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ============ 配置 ============
const CHUNK_SIZE_M = 200;
const CHUNK_VIEW_RADIUS = 2;   // 视野半径 (5x5 = 25 chunk 活跃, 远处用低清 LOD)
const CHUNK_CACHE_MAX = 36;    // LRU 上限 (保留略多于视野)

// ============ 全局状态 ============
let scene, camera, renderer, controls;
let manifest = null;
const chunkMeshes = new Map();       // chunk_id → THREE.Points (已加载)
const chunkLod = new Map();          // chunk_id → 已加载的 LOD 级别
const chunkBorders = new Map();     // chunk_id → THREE.Line (边界线)
const lruOrder = [];                // chunk_id 数组, 末尾为最近访问
const loadingSet = new Set();        // 正在加载中的 chunk_id
const stats = { loaded: 0, evicted: 0, cachedHits: 0 };
let qualityOverride = null;          // null=按距离自动, 0/1/2=强制 LOD (键 1/2/3, 0 恢复自动)
let reconManifest = null;            // 真实重建图层 (recon_manifest.json, 可选)
let reconMesh = null;
let reconLodLoaded = -1;
let reconVisible = true;
let reconLoading = false;
const clock = new THREE.Clock();
const keys = { w: false, a: false, s: false, d: false, q: false, e: false, shift: false };
let lastPlayerChunkKey = '';
let minimapCtx = null;

// ============ PLY Loader ============
function parsePly(buffer) {
  const decoder = new TextDecoder();
  const headerText = decoder.decode(buffer.slice(0, 2048));
  const headerEnd = headerText.indexOf('end_header') + 'end_header\n'.length;
  const headerStr = headerText.slice(0, headerEnd);

  const m = headerStr.match(/element vertex (\d+)/);
  const nVertices = parseInt(m[1]);

  // PLY 标准类型名 → (大小字节, 读取函数)
  const PLY_TYPES = {
    'float': { size: 4, read: (dv, o) => dv.getFloat32(o, true) },
    'double': { size: 8, read: (dv, o) => dv.getFloat64(o, true) },
    'uchar':  { size: 1, read: (dv, o) => dv.getUint8(o) },
    'char':   { size: 1, read: (dv, o) => dv.getInt8(o) },
    'ushort': { size: 2, read: (dv, o) => dv.getUint16(o, true) },
    'short':  { size: 2, read: (dv, o) => dv.getInt16(o, true) },
    'uint':   { size: 4, read: (dv, o) => dv.getUint32(o, true) },
    'int':    { size: 4, read: (dv, o) => dv.getInt32(o, true) },
  };

  const propLines = headerStr
    .split('\n')
    .filter(l => l.startsWith('property'))
    .map(l => l.trim().split(/\s+/))
    .filter(p => p[1] !== 'list')
    .map(p => ({ type: p[1], name: p[2] }));

  const stride = propLines.reduce((s, p) => s + PLY_TYPES[p.type].size, 0);

  const dataView = new DataView(buffer, headerEnd);
  const positions = new Float32Array(nVertices * 3);
  const colors = new Float32Array(nVertices * 3);
  const sizes = new Float32Array(nVertices);

  for (let i = 0; i < nVertices; i++) {
    let offset = i * stride;
    for (const p of propLines) {
      const t = PLY_TYPES[p.type];
      const val = t.read(dataView, offset);
      if (p.name === 'x') positions[i * 3] = val;
      else if (p.name === 'y') positions[i * 3 + 1] = val;
      else if (p.name === 'z') positions[i * 3 + 2] = val;
      else if (p.name === 'r') colors[i * 3] = val / 255.0;
      else if (p.name === 'g') colors[i * 3 + 1] = val / 255.0;
      else if (p.name === 'b') colors[i * 3 + 2] = val / 255.0;
      else if (p.name === 'scale') sizes[i] = Math.max(0.05, val);
      offset += t.size;
    }
  }
  return { positions, colors, sizes, n: nVertices };
}

async function loadChunkPly(plyFile) {
  const url = `../data/${plyFile}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`加载失败 ${plyFile}: ${res.status}`);
  const buf = await res.arrayBuffer();
  return parsePly(buf);
}

function makePointsMesh(parsed) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(parsed.positions, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(parsed.colors, 3));
  geo.setAttribute('aSize', new THREE.BufferAttribute(parsed.sizes, 1));
  geo.computeBoundingSphere();

  const mat = new THREE.ShaderMaterial({
    uniforms: { uPixelRatio: { value: window.devicePixelRatio } },
    vertexShader: `
      attribute float aSize;
      varying vec3 vColor;
      uniform float uPixelRatio;
      void main() {
        vColor = color;
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        float dist = max(-mvPosition.z, 1.0);
        gl_PointSize = aSize * 80.0 / dist * uPixelRatio;
        gl_PointSize = clamp(gl_PointSize, 1.0, 24.0);
      }
    `,
    fragmentShader: `
      varying vec3 vColor;
      void main() {
        vec2 uv = gl_PointCoord - vec2(0.5);
        float d = length(uv);
        if (d > 0.5) discard;
        float alpha = smoothstep(0.5, 0.3, d);
        gl_FragColor = vec4(vColor, alpha);
      }
    `,
    vertexColors: true,
    transparent: true,
    depthWrite: false,
  });

  return new THREE.Points(geo, mat);
}

// ============ Chunk 索引 (从 manifest 建立 chunk_id → 元数据) ============
const chunkIndex = new Map();  // "x_y" → manifest entry
function buildChunkIndex() {
  for (const c of manifest.chunks) {
    chunkIndex.set(`${c.x}_${c.y}`, c);
  }
}

// ============ Chunk 调度 (LRU + 视野半径) ============
function touchLRU(key) {
  const i = lruOrder.indexOf(key);
  if (i >= 0) lruOrder.splice(i, 1);
  lruOrder.push(key);
}

function evictLRU(keepKeys) {
  // 从 LRU 头部开始淘汰, 直到 cache 大小 <= MAX 或所有可淘汰项已处理
  let scanned = 0;
  while (lruOrder.length > CHUNK_CACHE_MAX && scanned < lruOrder.length + CHUNK_CACHE_MAX) {
    scanned++;
    const victim = lruOrder.shift();
    if (keepKeys.has(victim)) {
      // 视野内的不淘汰, 重新放回末尾 (scanned 上界防止全在视野内时死循环)
      lruOrder.push(victim);
      continue;
    }
    const mesh = chunkMeshes.get(victim);
    if (mesh) {
      scene.remove(mesh);
      mesh.geometry.dispose();
      mesh.material.dispose();
      chunkMeshes.delete(victim);
      chunkLod.delete(victim);
      stats.evicted++;
    }
    // 同时移除边界线
    const border = chunkBorders.get(victim);
    if (border) {
      scene.remove(border);
      border.geometry.dispose();
      border.material.dispose();
      chunkBorders.delete(victim);
    }
  }
}

function swapYZ(parsed) {
  // 坐标变换: ply (x_world, y_world, z_height) → three.js (x, z, y)
  for (let j = 0; j < parsed.n; j++) {
    const tmp = parsed.positions[j * 3 + 1];
    parsed.positions[j * 3 + 1] = parsed.positions[j * 3 + 2];
    parsed.positions[j * 3 + 2] = tmp;
  }
}

function desiredLod(cx, cy, pcx, pcy) {
  // 可变清晰: 玩家所在 chunk 全清晰, 越远越粗
  if (qualityOverride !== null) return qualityOverride;
  const d = Math.max(Math.abs(cx - pcx), Math.abs(cy - pcy));
  return d === 0 ? 2 : (d === 1 ? 1 : 0);
}

function lodFile(entry, lod) {
  // manifest 无 lod 字段时回退全量 ply (向后兼容旧 manifest)
  if (entry.lod && entry.lod[String(lod)]) return entry.lod[String(lod)];
  return entry.ply_file;
}

async function loadChunk(cx, cy, wantLod = 2) {
  const key = `${cx}_${cy}`;
  if (chunkMeshes.has(key) && chunkLod.get(key) === wantLod) {
    stats.cachedHits++;
    touchLRU(key);
    return;
  }
  if (loadingSet.has(key)) return;
  const entry = chunkIndex.get(key);
  if (!entry) return;  // 越界

  loadingSet.add(key);
  try {
    const parsed = await loadChunkPly(lodFile(entry, wantLod));
    swapYZ(parsed);

    const mesh = makePointsMesh(parsed);
    mesh.name = `chunk_${key}`;

    // 换级重载: 先摘旧网格再挂新的
    const old = chunkMeshes.get(key);
    if (old) {
      scene.remove(old);
      old.geometry.dispose();
      old.material.dispose();
    }
    scene.add(mesh);
    chunkMeshes.set(key, mesh);
    chunkLod.set(key, wantLod);
    touchLRU(key);
    stats.loaded++;

    // 加入 chunk 边界线 (供 debug 用)
    if (!chunkBorders.has(key)) {
      const border = makeChunkBorder(cx, cy);
      border.visible = bordersVisible;
      scene.add(border);
      chunkBorders.set(key, border);
    }
  } catch (e) {
    console.error(`chunk ${key} 加载失败:`, e);
  } finally {
    loadingSet.delete(key);
  }
}

function makeChunkBorder(cx, cy) {
  // 在 Three.js X-Z 平面画 chunk 边界 (200m × 200m)
  // ply 的 (x_world, y_world) → three.js (x, z)
  const x0 = cx * CHUNK_SIZE_M;
  const z0 = cy * CHUNK_SIZE_M;
  const pts = [
    new THREE.Vector3(x0, 0.05, z0),
    new THREE.Vector3(x0 + CHUNK_SIZE_M, 0.05, z0),
    new THREE.Vector3(x0 + CHUNK_SIZE_M, 0.05, z0 + CHUNK_SIZE_M),
    new THREE.Vector3(x0, 0.05, z0 + CHUNK_SIZE_M),
    new THREE.Vector3(x0, 0.05, z0),
  ];
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  const mat = new THREE.LineBasicMaterial({ color: 0x7fd1ff, transparent: true, opacity: 0.4 });
  const line = new THREE.Line(geo, mat);
  line.name = `border_${cx}_${cy}`;
  return line;
}

function updateChunks(playerX, playerZ) {
  // ply 的 y → three.js 的 z, 所以用 camera.position.z 计算 chunk y
  const cx = Math.floor(playerX / CHUNK_SIZE_M);
  const cy = Math.floor(playerZ / CHUNK_SIZE_M);

  const needed = new Set();
  for (let dx = -CHUNK_VIEW_RADIUS; dx <= CHUNK_VIEW_RADIUS; dx++) {
    for (let dy = -CHUNK_VIEW_RADIUS; dy <= CHUNK_VIEW_RADIUS; dy++) {
      const x = cx + dx, y = cy + dy;
      const key = `${x}_${y}`;
      if (chunkIndex.has(key)) needed.add(key);
    }
  }

  // 异步加载所有 needed (按与玩家距离决定清晰度)
  for (const key of needed) {
    const [x, y] = key.split('_').map(Number);
    loadChunk(x, y, desiredLod(x, y, cx, cy));
  }

  // LRU 淘汰 (保留视野内的)
  evictLRU(needed);

  return { cx, cy, needed };
}

// ============ 初始化 ============
function init() {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1a2228);
  scene.fog = new THREE.Fog(0x1a2228, 200, 1200);

  camera = new THREE.PerspectiveCamera(
    65, window.innerWidth / window.innerHeight, 0.1, 5000
  );
  camera.position.set(500, 400, -500);  // 俯瞰 5x5 区域中心
  camera.lookAt(500, 0, 500);

  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  document.getElementById('canvas-container').appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.target.set(500, 0, 500);
  controls.maxDistance = 2000;
  controls.minDistance = 2;

  const hemi = new THREE.HemisphereLight(0xbfd4ff, 0x404030, 0.6);
  scene.add(hemi);

  // 网格地板 (5x5 = 1000m x 1000m)
  const gridHelper = new THREE.GridHelper(1000, 50, 0x444444, 0x2a2a2a);
  gridHelper.position.set(500, 0, 500);
  gridHelper.material.opacity = 0.25;
  gridHelper.material.transparent = true;
  scene.add(gridHelper);

  const axes = new THREE.AxesHelper(80);
  axes.position.set(0, 0, 0);
  scene.add(axes);

  // mini-map canvas
  const mm = document.getElementById('minimap');
  minimapCtx = mm.getContext('2d');

  window.addEventListener('resize', onResize);
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
}

function onResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

let bordersVisible = true;

function onKeyDown(e) {
  const k = e.key.toLowerCase();
  if (k in keys) keys[k] = true;
  if (e.key === 'Shift') keys.shift = true;
  // B 键切换 chunk 边界显示
  if (k === 'b') {
    bordersVisible = !bordersVisible;
    for (const b of chunkBorders.values()) b.visible = bordersVisible;
  }
  // 1/2/3 强制画质 (低/中/高), 0 恢复按距离自动
  if (k === '1') qualityOverride = 0;
  if (k === '2') qualityOverride = 1;
  if (k === '3') qualityOverride = 2;
  if (k === '0') qualityOverride = null;
  // R 键切换真实重建图层
  if (k === 'r' && reconMesh) {
    reconVisible = !reconVisible;
    reconMesh.visible = reconVisible;
  }
}
function onKeyUp(e) {
  const k = e.key.toLowerCase();
  if (k in keys) keys[k] = false;
  if (e.key === 'Shift') keys.shift = false;
}

// ============ 相机控制 (WASD) ============
function updateCamera(dt) {
  const speed = (keys.shift ? 250 : 80) * dt;
  const forward = new THREE.Vector3();
  camera.getWorldDirection(forward);
  forward.y = 0;
  forward.normalize();
  const right = new THREE.Vector3();
  right.crossVectors(forward, camera.up).normalize();

  if (keys.w) { camera.position.addScaledVector(forward, speed); controls.target.addScaledVector(forward, speed); }
  if (keys.s) { camera.position.addScaledVector(forward, -speed); controls.target.addScaledVector(forward, -speed); }
  if (keys.a) { camera.position.addScaledVector(right, -speed); controls.target.addScaledVector(right, -speed); }
  if (keys.d) { camera.position.addScaledVector(right, speed); controls.target.addScaledVector(right, speed); }
  if (keys.q) { camera.position.y -= speed; controls.target.y -= speed; }
  if (keys.e) { camera.position.y += speed; controls.target.y += speed; }
}

// ============ 真实重建图层 (可选, 由 pipeline.reconstruct 生成) ============
async function loadReconManifest() {
  try {
    const res = await fetch('../data/recon/recon_manifest.json');
    if (!res.ok) return;
    reconManifest = await res.json();
    console.log(`重建图层: ${reconManifest.gaussian_count} 高斯, ` +
                `LOD ${Object.keys(reconManifest.lod).join('/')}`);
  } catch (e) { /* 无重建图层, 静默跳过 */ }
}

async function updateRecon() {
  if (!reconManifest || reconLoading) return;
  let tier;
  if (qualityOverride !== null) {
    tier = qualityOverride;
  } else {
    // 按相机到重建区中心的水平距离选清晰度 (近清远粗)
    const b = reconManifest.bounds;
    const cxw = (b.min[0] + b.max[0]) / 2;
    const cyw = (b.min[1] + b.max[1]) / 2;
    // ply 世界 y → three.js z
    const dist = Math.hypot(camera.position.x - cxw, camera.position.z - cyw);
    tier = dist < 150 ? 2 : (dist < 400 ? 1 : 0);
  }
  if (tier === reconLodLoaded) return;
  const file = reconManifest.lod[String(tier)];
  if (!file) return;

  reconLoading = true;
  try {
    const parsed = await loadChunkPly(`recon/${file}`);
    swapYZ(parsed);
    const mesh = makePointsMesh(parsed);
    mesh.name = 'recon_layer';
    mesh.visible = reconVisible;
    if (reconMesh) {
      scene.remove(reconMesh);
      reconMesh.geometry.dispose();
      reconMesh.material.dispose();
    }
    reconMesh = mesh;
    scene.add(mesh);
    reconLodLoaded = tier;
  } catch (e) {
    console.error('重建图层加载失败:', e);
  } finally {
    reconLoading = false;
  }
}

// ============ HUD 更新 ============
function updateHUD() {
  const pos = camera.position;
  document.getElementById('hud-camera').textContent =
    `(${pos.x.toFixed(0)}, ${pos.y.toFixed(0)}, ${pos.z.toFixed(0)})`;

  // ply 的 y → three.js 的 z, 所以 chunk_y 用 pos.z 计算
  const cx = Math.floor(pos.x / CHUNK_SIZE_M);
  const cy = Math.floor(pos.z / CHUNK_SIZE_M);
  document.getElementById('hud-current').textContent = `(${cx},${cy})`;
  document.getElementById('hud-chunks').textContent = chunkMeshes.size;
  document.getElementById('hud-evicted').textContent = stats.evicted;
  document.getElementById('hud-hits').textContent = stats.cachedHits;

  const lodEl = document.getElementById('hud-lod');
  if (lodEl) {
    lodEl.textContent = qualityOverride === null
      ? '自动 (近清远粗)' : `强制 LOD${qualityOverride}`;
  }
  const reconEl = document.getElementById('hud-recon');
  if (reconEl) {
    reconEl.textContent = !reconManifest ? '无'
      : !reconVisible ? '已隐藏 (R 显示)'
      : reconLodLoaded >= 0 ? `LOD${reconLodLoaded} (${reconManifest.gaussian_count} 高斯)`
      : '加载中...';
  }

  // 加载中状态指示
  const loadEl = document.getElementById('hud-loading');
  if (loadEl) {
    if (loadingSet.size > 0) {
      const keys = Array.from(loadingSet).slice(0, 3).join(', ');
      loadEl.textContent = `${loadingSet.size} 个加载中 [${keys}...]`;
      loadEl.style.color = '#ffcc55';
    } else {
      loadEl.textContent = '空闲';
      loadEl.style.color = '#7fff7f';
    }
  }
}

// ============ Mini-map ============
function drawMinimap() {
  if (!minimapCtx) return;
  const ctx = minimapCtx;
  const W = ctx.canvas.width, H = ctx.canvas.height;

  // 计算可见 chunk 范围 (manifest 中 x_min/x_max, y_min/y_max)
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  for (const c of manifest.chunks) {
    if (c.x < xmin) xmin = c.x;
    if (c.x > xmax) xmax = c.x;
    if (c.y < ymin) ymin = c.y;
    if (c.y > ymax) ymax = c.y;
  }
  const cols = xmax - xmin + 1, rows = ymax - ymin + 1;
  const cw = W / cols, ch = H / rows;

  // 背景
  ctx.fillStyle = '#0a0e14';
  ctx.fillRect(0, 0, W, H);

  // 画每个 chunk
  for (const c of manifest.chunks) {
    const px = (c.x - xmin) * cw;
    // mini-map Y 反向: manifest y 增大 = 北方向 = 屏幕上方
    const py = H - (c.y + 1 - ymin) * ch;
    const isLoaded = chunkMeshes.has(`${c.x}_${c.y}`);
    ctx.fillStyle = isLoaded ? '#4a9b6f' : '#1a2a22';
    ctx.fillRect(px + 1, py + 1, cw - 2, ch - 2);
    ctx.strokeStyle = '#2a4030';
    ctx.lineWidth = 1;
    ctx.strokeRect(px + 1, py + 1, cw - 2, ch - 2);
  }

  // 玩家位置 (camera.position.x → mini-map x; camera.position.z → mini-map y)
  // 注意 ply.y → three.js.z, 所以玩家"世界 Y" = camera.z
  const wx = camera.position.x / CHUNK_SIZE_M;
  const wy = camera.position.z / CHUNK_SIZE_M;
  const px = (wx - xmin) * cw;
  const py = H - (wy - ymin) * ch;

  // 视野范围 (3x3 方框)
  ctx.strokeStyle = '#7fd1ff';
  ctx.lineWidth = 2;
  ctx.strokeRect(
    (wx - CHUNK_VIEW_RADIUS - xmin) * cw,
    H - (wy + CHUNK_VIEW_RADIUS + 1 - ymin) * ch,
    cw * (2 * CHUNK_VIEW_RADIUS + 1),
    ch * (2 * CHUNK_VIEW_RADIUS + 1)
  );

  // 玩家点
  ctx.fillStyle = '#ff5a5a';
  ctx.beginPath();
  ctx.arc(px, py, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 1;
  ctx.stroke();
}

// ============ 主流程 ============
async function main() {
  init();

  const loadingText = document.getElementById('loading-text');
  loadingText.textContent = '加载 manifest.json...';

  const res = await fetch('../data/manifest.json');
  if (!res.ok) {
    loadingText.textContent = `错误: 无法加载 manifest.json (${res.status})`;
    return;
  }
  manifest = await res.json();
  buildChunkIndex();
  await loadReconManifest();  // 真实重建图层 (可选)

  loadingText.textContent = '生成 5x5 chunk 索引完成, 启动调度器...';

  // 初始加载相机视野内 chunk
  const initPos = controls.target;
  updateChunks(initPos.x, initPos.z);

  // 等待初始加载 (轮询直到视野内所有 chunk 都加载完)
  const waitInit = () => new Promise(resolve => {
    const check = () => {
      const cx = Math.floor(initPos.x / CHUNK_SIZE_M);
      const cy = Math.floor(initPos.z / CHUNK_SIZE_M);
      let needed = 0, loaded = 0;
      for (let dx = -CHUNK_VIEW_RADIUS; dx <= CHUNK_VIEW_RADIUS; dx++) {
        for (let dy = -CHUNK_VIEW_RADIUS; dy <= CHUNK_VIEW_RADIUS; dy++) {
          const key = `${cx+dx}_${cy+dy}`;
          if (chunkIndex.has(key)) {
            needed++;
            if (chunkMeshes.has(key)) loaded++;
          }
        }
      }
      if (loaded >= needed || loadingSet.size === 0) resolve();
      else setTimeout(check, 80);
    };
    check();
  });
  await waitInit();

  document.getElementById('loading').style.display = 'none';
  document.getElementById('hud').style.display = 'block';
  document.getElementById('controls').style.display = 'block';
  document.getElementById('minimap-wrap').style.display = 'block';

  animate();
}

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.1);
  updateCamera(dt);
  controls.update();

  // 每 50ms 调度一次 chunk (避免每帧都触发)
  const now = performance.now();
  if (!animate._lastCheck || now - animate._lastCheck > 50) {
    animate._lastCheck = now;
    updateChunks(camera.position.x, camera.position.z);
    updateRecon();
  }

  updateHUD();
  drawMinimap();
  renderer.render(scene, camera);
}

main().catch(err => {
  console.error(err);
  document.getElementById('loading-text').textContent = `错误: ${err.message}`;
});
