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
import {
  threeToChunk,
  threeToWorld,
  transformPositionsInPlace,
  worldToThree,
} from './coordinates.mjs';
import {
  computeFraming,
  computeWorldBounds,
  selectReconLod,
  worldToMinimap,
} from './framing.mjs';
import {
  artifactProvenance,
  createViewerCapabilities,
  createViewerBridge,
  resolveArtifactUrl,
} from './bridge.mjs';
import { createSplatLayer } from './splat-layer.mjs';

// ============ 配置 ============
const CHUNK_VIEW_RADIUS = 2;   // 视野半径 (最远用低清 LOD)
const CHUNK_CACHE_MAX = 36;    // LRU 上限 (保留略多于视野)

// ============ 全局状态 ============
let scene, camera, renderer, controls;
let manifest = null;
let worldManifestUrl = new URL('../data/manifest.json', import.meta.url).href;
let chunkSizeM = null;
let currentFrame = null;
let gridHelper = null;
let axesHelper = null;
const chunkMeshes = new Map();       // chunk_id → THREE.Points (已加载)
const chunkLod = new Map();          // chunk_id → 已加载的 LOD 级别
const chunkBorders = new Map();     // chunk_id → THREE.Line (边界线)
const lruOrder = [];                // chunk_id 数组, 末尾为最近访问
const loadingSet = new Set();        // 正在加载中的 chunk_id
const stats = { loaded: 0, evicted: 0, cachedHits: 0 };
let qualityOverride = null;          // null=按距离自动, 0/1/2=强制 LOD (键 1/2/3, 0 恢复自动)
let reconManifest = null;            // 重建预览 artifact (recon_manifest.json, 可选)
let reconManifestUrl = new URL('../data/recon/recon_manifest.json', import.meta.url).href;
let reconMesh = null;
let reconLodLoaded = -1;
let reconVisible = true;
let reconLoading = false;
let worldVisible = true;
let viewerBridge = null;
let splatLayer = null;
let viewerCapabilities = createViewerCapabilities();
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

async function loadPlyUrl(url, label = url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`加载失败 ${label}: ${res.status}`);
  const buf = await res.arrayBuffer();
  return parsePly(buf);
}

async function loadChunkPly(plyFile) {
  const url = resolveArtifactUrl(worldManifestUrl, plyFile);
  return loadPlyUrl(url, plyFile);
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
    transformPositionsInPlace(parsed.positions);

    const mesh = makePointsMesh(parsed);
    mesh.name = `chunk_${key}`;
    mesh.visible = worldVisible;

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
      border.visible = worldVisible && bordersVisible;
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
  const east0 = cx * chunkSizeM;
  const north0 = cy * chunkSizeM;
  const corners = [
    [east0, north0, 0.05],
    [east0 + chunkSizeM, north0, 0.05],
    [east0 + chunkSizeM, north0 + chunkSizeM, 0.05],
    [east0, north0 + chunkSizeM, 0.05],
    [east0, north0, 0.05],
  ];
  const pts = corners.map((point) => new THREE.Vector3(...worldToThree(point)));
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  const mat = new THREE.LineBasicMaterial({ color: 0x7fd1ff, transparent: true, opacity: 0.4 });
  const line = new THREE.Line(geo, mat);
  line.name = `border_${cx}_${cy}`;
  return line;
}

function updateChunks(playerX, playerZ) {
  const [cx, cy] = threeToChunk([playerX, 0, playerZ], chunkSizeM);

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

function applyFraming(frame, resetCamera = true) {
  currentFrame = frame;
  camera.near = frame.near;
  camera.far = frame.far;
  camera.updateProjectionMatrix();
  scene.fog = new THREE.Fog(0x1a2228, frame.fogNear, frame.fogFar);
  controls.maxDistance = frame.far * 0.75;

  if (resetCamera) {
    camera.position.set(...frame.cameraPositionThree);
    controls.target.set(...frame.targetThree);
    camera.lookAt(...frame.targetThree);
    controls.update();
  }

  if (gridHelper) {
    scene.remove(gridHelper);
    gridHelper.geometry.dispose();
    gridHelper.material.dispose();
  }
  gridHelper = new THREE.GridHelper(
    frame.gridSize,
    frame.gridDivisions,
    0x444444,
    0x2a2a2a,
  );
  gridHelper.position.set(...frame.gridCenterThree);
  gridHelper.material.opacity = 0.25;
  gridHelper.material.transparent = true;
  gridHelper.visible = worldVisible;
  scene.add(gridHelper);

  if (axesHelper) {
    scene.remove(axesHelper);
    axesHelper.geometry.dispose();
    axesHelper.material.dispose();
  }
  axesHelper = new THREE.AxesHelper(Math.max(chunkSizeM / 4, 1));
  axesHelper.visible = worldVisible;
  scene.add(axesHelper);
}

// ============ 初始化 ============
function init() {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1a2228);

  camera = new THREE.PerspectiveCamera(
    65, window.innerWidth / window.innerHeight, 0.1, 1,
  );
  camera.position.set(0, 1, 1);
  camera.lookAt(0, 0, 0);

  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  document.getElementById('canvas-container').appendChild(renderer.domElement);

  splatLayer = createSplatLayer({ scene, renderer });

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.target.set(0, 0, 0);
  controls.minDistance = 2;

  const hemi = new THREE.HemisphereLight(0xbfd4ff, 0x404030, 0.6);
  scene.add(hemi);

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
    for (const b of chunkBorders.values()) b.visible = worldVisible && bordersVisible;
  }
  // 1/2/3 强制画质 (低/中/高), 0 恢复按距离自动
  if (k === '1') qualityOverride = 0;
  if (k === '2') qualityOverride = 1;
  if (k === '3') qualityOverride = 2;
  if (k === '0') qualityOverride = null;
  // R 键切换重建预览图层
  if (k === 'r' && reconManifest) {
    reconVisible = !reconVisible;
    if (reconMesh) reconMesh.visible = reconVisible;
    splatLayer?.setVisible(reconVisible);
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

// ============ 重建预览图层 (可选, 由 pipeline.reconstruct 生成) ============
async function loadReconManifest(url = reconManifestUrl) {
  try {
    const absoluteUrl = new URL(url, window.location.href).href;
    const res = await fetch(absoluteUrl);
    if (!res.ok) return false;
    reconManifest = await res.json();
    reconManifestUrl = absoluteUrl;
    const count = reconManifest.gaussian_count ?? reconManifest.point_count ?? 'unknown';
    console.log(`重建 artifact: ${count} points, ` +
                `${viewerCapabilities.renderer.label}, ` +
                `LOD ${Object.keys(reconManifest.lod ?? {}).join('/')}`);
    return true;
  } catch (e) {
    return false;
  }
}

async function updateRecon() {
  if (!reconManifest || reconLoading) return;
  if (splatLayer?.getState().mode === 'spark') return;
  let tier;
  if (qualityOverride !== null) {
    tier = qualityOverride;
  } else if (reconManifest.bounds) {
    tier = selectReconLod(
      [camera.position.x, camera.position.y, camera.position.z],
      reconManifest.bounds,
    );
  } else {
    tier = 2;
  }
  if (tier === reconLodLoaded) return;
  const file = reconManifest.lod[String(tier)];
  if (!file) return;

  reconLoading = true;
  try {
    const plyUrl = resolveArtifactUrl(reconManifestUrl, file);
    const parsed = await loadPlyUrl(plyUrl, file);
    transformPositionsInPlace(parsed.positions);
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

function disposeReconPointPreview() {
  if (!reconMesh) return;
  scene.remove(reconMesh);
  reconMesh.geometry.dispose();
  reconMesh.material.dispose();
  reconMesh = null;
  reconLodLoaded = -1;
}

async function loadReconstructionLayer() {
  disposeReconPointPreview();
  const result = await splatLayer.load({
    manifest: reconManifest ?? {},
    manifestUrl: reconManifestUrl,
    visible: reconVisible,
  });
  viewerCapabilities = createViewerCapabilities(result.mode);
  viewerBridge?.announceCapabilities();

  if (result.mode !== 'spark' && reconManifest) {
    console.warn(`${result.reason}; 使用 DC point preview`);
    await updateRecon();
  }
  return result;
}

// ============ HUD 更新 ============
function updateHUD() {
  const pos = camera.position;
  const worldPos = threeToWorld([pos.x, pos.y, pos.z]);
  document.getElementById('hud-camera').textContent =
    `(${worldPos[0].toFixed(0)}, ${worldPos[1].toFixed(0)}, ${worldPos[2].toFixed(0)})`;

  const [cx, cy] = threeToChunk([pos.x, pos.y, pos.z], chunkSizeM);
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
    const count = reconManifest?.gaussian_count ?? reconManifest?.point_count ?? 'unknown';
    const splatState = splatLayer?.getState();
    reconEl.textContent = !reconManifest ? '无'
      : !reconVisible ? '已隐藏 (R 显示)'
      : splatState?.mode === 'spark'
        ? `${count} splats (${viewerCapabilities.renderer.label})`
      : reconLodLoaded >= 0
        ? `LOD${reconLodLoaded} (${count} points, ${viewerCapabilities.renderer.label})`
      : '加载中...';
  }

  const rendererReason = document.getElementById('hud-renderer-reason');
  if (rendererReason) {
    const splatState = splatLayer?.getState();
    rendererReason.textContent = splatState?.mode === 'spark'
      ? 'full_3dgs 已由 Spark 初始化'
      : (splatState?.reason ?? 'DC point preview');
  }

  const title = document.getElementById('hud-title');
  if (title) title.textContent = `Nantai Village · ${viewerCapabilities.renderer.label}`;

  const provenance = artifactProvenance(reconManifest ?? {}, viewerCapabilities);
  const provenanceFields = {
    'hud-requested-engine': provenance.requested_engine,
    'hud-actual-engine': provenance.actual_engine,
    'hud-synthetic': provenance.synthetic,
    'hud-frame': `${provenance.frame} / ${provenance.units} / ${provenance.handedness}`,
    'hud-geometry': provenance.geometry_usability,
    'hud-artifact-fidelity': provenance.artifact_fidelity,
    'hud-viewer-fidelity': provenance.viewer_fidelity,
  };
  for (const [id, value] of Object.entries(provenanceFields)) {
    const element = document.getElementById(id);
    if (element) element.textContent = String(value);
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
  if (!minimapCtx || !manifest?.chunks?.length) return;
  const ctx = minimapCtx;
  const W = ctx.canvas.width, H = ctx.canvas.height;
  const bounds = computeWorldBounds(manifest);

  // 背景
  ctx.fillStyle = '#0a0e14';
  ctx.fillRect(0, 0, W, H);

  // 画每个 chunk
  for (const c of manifest.chunks) {
    const [left, top] = worldToMinimap(
      [c.x * chunkSizeM, (c.y + 1) * chunkSizeM],
      bounds,
      W,
      H,
    );
    const [right, bottom] = worldToMinimap(
      [(c.x + 1) * chunkSizeM, c.y * chunkSizeM],
      bounds,
      W,
      H,
    );
    const isLoaded = chunkMeshes.has(`${c.x}_${c.y}`);
    ctx.fillStyle = isLoaded ? '#4a9b6f' : '#1a2a22';
    ctx.fillRect(left + 1, top + 1, right - left - 2, bottom - top - 2);
    ctx.strokeStyle = '#2a4030';
    ctx.lineWidth = 1;
    ctx.strokeRect(left + 1, top + 1, right - left - 2, bottom - top - 2);
  }

  const cameraThree = [camera.position.x, camera.position.y, camera.position.z];
  const [east, north] = threeToWorld(cameraThree);
  const [px, py] = worldToMinimap([east, north], bounds, W, H);
  const [cx, cy] = threeToChunk(cameraThree, chunkSizeM);

  // 视野范围，north 增大始终在画布上方。
  const [viewLeft, viewTop] = worldToMinimap(
    [(cx - CHUNK_VIEW_RADIUS) * chunkSizeM,
      (cy + CHUNK_VIEW_RADIUS + 1) * chunkSizeM],
    bounds,
    W,
    H,
  );
  const [viewRight, viewBottom] = worldToMinimap(
    [(cx + CHUNK_VIEW_RADIUS + 1) * chunkSizeM,
      (cy - CHUNK_VIEW_RADIUS) * chunkSizeM],
    bounds,
    W,
    H,
  );
  ctx.strokeStyle = '#7fd1ff';
  ctx.lineWidth = 2;
  ctx.strokeRect(viewLeft, viewTop, viewRight - viewLeft, viewBottom - viewTop);

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

  const res = await fetch(worldManifestUrl);
  if (!res.ok) {
    loadingText.textContent = `错误: 无法加载 manifest.json (${res.status})`;
    return;
  }
  manifest = await res.json();
  if (!manifest.chunks?.length) throw new Error('manifest.json 没有可显示的 chunks');
  chunkSizeM = manifest.chunk_size_m ?? 200;
  buildChunkIndex();
  await loadReconManifest();
  applyFraming(computeFraming(manifest, reconManifest?.bounds));
  await loadReconstructionLayer();

  const xCount = new Set(manifest.chunks.map((chunk) => chunk.x)).size;
  const yCount = new Set(manifest.chunks.map((chunk) => chunk.y)).size;
  loadingText.textContent =
    `已索引 ${manifest.chunks.length} chunks (${xCount}×${yCount}), 启动调度器...`;
  const minimapTitle = document.getElementById('minimap-title');
  if (minimapTitle) minimapTitle.textContent = `Mini-map (${xCount}×${yCount})`;

  // 初始加载相机视野内 chunk
  const initPos = controls.target;
  updateChunks(initPos.x, initPos.z);

  // 等待初始加载 (轮询直到视野内所有 chunk 都加载完)
  const waitInit = () => new Promise(resolve => {
    const check = () => {
      const [cx, cy] = threeToChunk([initPos.x, initPos.y, initPos.z], chunkSizeM);
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

  const readState = () => ({
    renderer: viewerCapabilities.renderer,
    capabilities: viewerCapabilities,
    renderer_status: splatLayer?.getState() ?? null,
    lod: qualityOverride ?? 'auto',
    layers: { world: worldVisible, reconstruction: reconVisible },
    chunk_size_m: chunkSizeM,
    bounds: currentFrame?.bounds ?? null,
    artifact: artifactProvenance(reconManifest ?? {}, viewerCapabilities),
  });

  viewerBridge = createViewerBridge({
    windowObject: window,
    capabilities: () => viewerCapabilities,
    handlers: {
      getState: () => readState(),
      setLOD: async ({ lod }) => {
        const nextLod = lod === 'auto' || lod === null ? null : Number(lod);
        if (nextLod !== null && ![0, 1, 2].includes(nextLod)) {
          throw new Error(`不支持的 LOD: ${lod}`);
        }
        qualityOverride = nextLod;
        reconLodLoaded = -1;
        updateChunks(camera.position.x, camera.position.z);
        await updateRecon();
        return readState();
      },
      setLayer: ({ layer, visible }) => {
        const nextVisible = visible !== false;
        if (layer === 'world') {
          worldVisible = nextVisible;
          for (const mesh of chunkMeshes.values()) mesh.visible = worldVisible;
          for (const border of chunkBorders.values()) {
            border.visible = worldVisible && bordersVisible;
          }
          if (gridHelper) gridHelper.visible = worldVisible;
          if (axesHelper) axesHelper.visible = worldVisible;
        } else if (layer === 'reconstruction') {
          reconVisible = nextVisible;
          if (reconMesh) reconMesh.visible = reconVisible;
          splatLayer?.setVisible(reconVisible);
        } else {
          throw new Error(`未知图层: ${layer}`);
        }
        return readState();
      },
      resetCamera: () => {
        if (currentFrame) applyFraming(currentFrame, true);
        return readState();
      },
      setBounds: ({ bounds }) => {
        if (!bounds?.min || !bounds?.max) throw new Error('setBounds 需要 min/max');
        const frame = computeFraming(
          { chunk_size_m: chunkSizeM, chunks: [] },
          bounds,
        );
        applyFraming(frame, true);
        return readState();
      },
      loadArtifact: async ({ kind = 'recon-manifest', url, manifest: artifact }) => {
        if (kind !== 'recon-manifest') throw new Error(`不支持的 artifact kind: ${kind}`);
        if (url) {
          const absoluteUrl = new URL(url, window.location.href);
          if (absoluteUrl.origin !== window.location.origin) {
            throw new Error('artifact URL 必须与 viewer 同源');
          }
          if (!await loadReconManifest(absoluteUrl.href)) {
            throw new Error(`无法加载 artifact: ${absoluteUrl.href}`);
          }
        } else if (artifact) {
          reconManifest = artifact;
        } else {
          throw new Error('loadArtifact 需要 url 或 manifest');
        }

        applyFraming(computeFraming(manifest, reconManifest?.bounds), false);
        const rendererResult = await loadReconstructionLayer();
        return {
          kind,
          url: reconManifestUrl,
          provenance: artifactProvenance(reconManifest, viewerCapabilities),
          renderer: rendererResult,
          state: readState(),
        };
      },
    },
  });
  viewerBridge.start();

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
