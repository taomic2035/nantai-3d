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
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
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
import {
  createSplatLayer,
  isSupersededLoadResult,
} from './splat-layer.mjs';
import { isSpatialChunkManifest } from './spatial-reconstruction.mjs';
import { createSpatialSplatLayer } from './splat-chunks-layer.mjs';
import { createSpatialPointLayer } from './spatial-point-layer.mjs';
import {
  clampPitch,
  directionFromYawPitchThree,
  flyDisplacementThree,
  normalizeCameraPose,
  parseEnuText,
  yawPitchFromDirectionThree,
} from './camera-pose.mjs';
import {
  resolveWorldChunkSource,
  shouldRetryWorldChunkFailure,
  worldChunkAvailable,
} from './world-chunks.mjs';
import {
  DEFAULT_WEATHER,
  DEFAULT_ZOOM,
  ENVIRONMENT_EFFECT_IDENTITY,
  ZOOM_STEP,
  createPrecipitationPositions,
  getWeatherPreset,
  normalizeWeather,
  normalizeZoom,
} from './environment.mjs';
import {
  coverageAuditViewModel,
  isCoverageAudit,
} from './coverage-audit.mjs';
import {
  atmosphereLightForRenderer,
  environmentNotice,
  meshWeatherResponse,
} from './mesh-weather.mjs';
import {
  modelPreviewCameraPose,
  modelPreviewDisclosure,
  modelPreviewSha256,
  modelPreviewTrustMetadata,
  resolveModelPreviewUrl,
  resolveRequestedModelPreviewManifestUrl,
  selectEmbeddedModelPreviewCamera,
  validateModelPreviewManifest,
  verifyModelPreviewBytes,
} from './model-preview.mjs';

// ============ 配置 ============
const CHUNK_VIEW_RADIUS = 2;   // 视野半径 (最远用低清 LOD)
const CHUNK_CACHE_MAX = 36;    // LRU 上限 (保留略多于视野)
const VIEWER_FOV_DEG = 65;

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
const failedChunkRetryAt = new Map(); // 请求失败后的有界重试，避免 404/5xx spam
const terminalChunkFailures = new Set(); // 已确认越过世界信封，不再重复请求
const stats = { loaded: 0, evicted: 0, cachedHits: 0 };
let qualityOverride = null;          // null=按距离自动, 0/1/2=强制 LOD (键 1/2/3, 0 恢复自动)
let reconManifest = null;            // 重建预览 artifact (recon_manifest.json, 可选)
let reconManifestUrl = new URL('../data/recon/recon_manifest.json', import.meta.url).href;
let coverageAudit = null;             // 独立覆盖审计，不改变重建 artifact
let coverageAuditUrl = null;
let reconMesh = null;
let reconLodLoaded = -1;
let reconVisible = true;
let reconLoading = false;
let worldVisible = true;
let presentationMode = 'points';     // 'points' | 'model'，禁止未对齐图层叠加
let modelPreviewManifest = null;
let modelPreviewManifestUrl = new URL(
  '../data/recon/model-preview/manifest.json',
  import.meta.url,
).href;
let modelPreviewRoot = null;
let modelPreviewBounds = null;
let modelPreviewEmbeddedCamera = null;
let modelPreviewWeatherMaterials = [];
let modelPreviewKeyLight = null;
let viewerBridge = null;
let splatLayer = null;
let spatialSplatLayer = null;
let spatialPointLayer = null;
let spatialReconUpdating = false;
let spatialSplatFallbackReason = null;
let viewerCapabilities = createViewerCapabilities();
const clock = new THREE.Clock();
const keys = { w: false, a: false, s: false, d: false, q: false, e: false, shift: false };
let cameraMode = 'orbit';            // 'orbit' | 'free'
const freeLook = { yaw: 0, pitch: 0 };
let orbitDistance = 10;              // 进入 free 模式时记录的相机→target 距离
let lastPlayerChunkKey = '';
let minimapCtx = null;
const environmentState = {
  weather: DEFAULT_WEATHER,
  zoom: DEFAULT_ZOOM,
  ...ENVIRONMENT_EFFECT_IDENTITY,
  precipitation_status: 'ready',
};
let hemisphereLight = null;
let precipitationPoints = null;
let precipitationEffect = null;

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
  if (!res.ok) {
    const error = new Error(`加载失败 ${label}: ${res.status}`);
    error.status = res.status;
    error.apiCode = null;
    if (res.headers.get('content-type')?.includes('application/json')) {
      try {
        error.apiCode = (await res.json())?.error?.code ?? null;
      } catch {
        error.apiCode = null;
      }
    }
    throw error;
  }
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

function disposePointMesh(mesh) {
  mesh?.geometry?.dispose();
  mesh?.material?.dispose();
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

async function loadChunk(cx, cy, wantLod = 2) {
  const key = `${cx}_${cy}`;
  if (chunkMeshes.has(key) && chunkLod.get(key) === wantLod) {
    stats.cachedHits++;
    touchLRU(key);
    return;
  }
  if (loadingSet.has(key)) return;
  if (terminalChunkFailures.has(key)) return;
  if ((failedChunkRetryAt.get(key) ?? 0) > Date.now()) return;
  const entry = chunkIndex.get(key);
  const source = resolveWorldChunkSource(manifest, entry, cx, cy, wantLod);
  if (!source) return;

  loadingSet.add(key);
  try {
    const parsed = await loadChunkPly(source.path);
    transformPositionsInPlace(parsed.positions);

    const mesh = makePointsMesh(parsed);
    mesh.name = `chunk_${key}`;
    mesh.visible = presentationMode === 'points' && worldVisible;

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
    failedChunkRetryAt.delete(key);
    terminalChunkFailures.delete(key);
    touchLRU(key);
    stats.loaded++;

    // 加入 chunk 边界线 (供 debug 用)
    if (!chunkBorders.has(key)) {
      const border = makeChunkBorder(cx, cy);
      border.visible = presentationMode === 'points' && worldVisible && bordersVisible;
      scene.add(border);
      chunkBorders.set(key, border);
    }
  } catch (e) {
    if (shouldRetryWorldChunkFailure(e)) {
      failedChunkRetryAt.set(key, Date.now() + 5000);
      console.error(`chunk ${key} 加载失败:`, e);
    } else {
      terminalChunkFailures.add(key);
      failedChunkRetryAt.delete(key);
      console.warn(`chunk ${key} 已越过世界可渲染边界，停止重试`);
    }
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
      if (worldChunkAvailable(manifest, chunkIndex.has(key))) needed.add(key);
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

// ============ Viewer 运行时环境 (不进入 artifact provenance) ============
function createPrecipitationRuntime() {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    'position',
    new THREE.BufferAttribute(new Float32Array(1200 * 3), 3),
  );
  geometry.setDrawRange(0, 0);
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uColor: { value: new THREE.Color(0xffffff) },
      uOpacity: { value: 0 },
      uPointSize: { value: 1 },
      uRain: { value: 0 },
    },
    vertexShader: `
      uniform float uPointSize;
      void main() {
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        gl_PointSize = uPointSize;
      }
    `,
    fragmentShader: `
      uniform vec3 uColor;
      uniform float uOpacity;
      uniform float uRain;
      void main() {
        vec2 point = gl_PointCoord - vec2(0.5);
        float alpha;
        if (uRain > 0.5) {
          if (abs(point.x) > 0.09 || abs(point.y) > 0.48) discard;
          alpha = 1.0 - abs(point.y) * 0.8;
        } else {
          float distanceToCenter = length(point);
          if (distanceToCenter > 0.5) discard;
          alpha = smoothstep(0.5, 0.16, distanceToCenter);
        }
        gl_FragColor = vec4(uColor, uOpacity * alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
  });
  precipitationPoints = new THREE.Points(geometry, material);
  precipitationPoints.name = 'viewer_runtime_precipitation';
  precipitationPoints.frustumCulled = false;
  precipitationPoints.visible = false;
  scene.add(precipitationPoints);
}

function syncEnvironmentUI() {
  const preset = getWeatherPreset(environmentState.weather);
  const zoomText = `${environmentState.zoom.toFixed(1)}×`;
  const meshRelighting = viewerCapabilities.renderer.dynamic_mesh_relighting === true;
  document.getElementById('weather-control').value = environmentState.weather;
  document.getElementById('weather-label').textContent = meshRelighting
    ? '天气（网格重光照 + 大气）'
    : '视觉天气（叠加）';
  document.getElementById('zoom-control').value = String(environmentState.zoom);
  document.getElementById('zoom-value').textContent = zoomText;
  document.getElementById('hud-weather').textContent = preset.label;
  document.getElementById('hud-zoom').textContent = zoomText;
  const notice = environmentNotice(viewerCapabilities.renderer);
  document.getElementById('environment-status').textContent =
    environmentState.precipitation_status === 'degraded'
      ? `${notice} · 降水粒子已降级，背景/雾仍生效`
      : meshRelighting
        ? `${notice} · 仅改变合成网格材质与灯光`
        : `${notice} · 不改变 3DGS 已烘焙光照`;
}

function configurePrecipitation(effect) {
  precipitationEffect = null;
  environmentState.precipitation_status = 'ready';
  if (!precipitationPoints) {
    if (effect) environmentState.precipitation_status = 'degraded';
    return;
  }

  const geometry = precipitationPoints.geometry;
  const positions = geometry.getAttribute('position');
  geometry.setDrawRange(0, 0);
  precipitationPoints.visible = false;
  if (!effect) return;

  try {
    const layout = createPrecipitationPositions(environmentState.weather);
    positions.array.fill(0);
    positions.array.set(layout);
    positions.needsUpdate = true;
    geometry.setDrawRange(0, effect.count);
    precipitationPoints.material.uniforms.uColor.value.setHex(effect.color);
    precipitationPoints.material.uniforms.uOpacity.value = effect.opacity;
    precipitationPoints.material.uniforms.uPointSize.value =
      effect.pointSize * Math.min(window.devicePixelRatio, 2);
    precipitationPoints.material.uniforms.uRain.value = effect.kind === 'rain' ? 1 : 0;
    precipitationPoints.visible = true;
    precipitationEffect = effect;
  } catch (error) {
    environmentState.precipitation_status = 'degraded';
    console.warn('降水粒子效果降级:', error);
  }
}

function updatePrecipitation(dt) {
  if (!precipitationPoints?.visible || !precipitationEffect) return;
  precipitationPoints.position.copy(camera.position);
  const positions = precipitationPoints.geometry.getAttribute('position');
  const [width, height] = precipitationEffect.volume;
  const halfWidth = width / 2;
  const halfHeight = height / 2;
  for (let index = 0; index < precipitationEffect.count; index += 1) {
    const offset = index * 3;
    positions.array[offset + 1] -= precipitationEffect.fallSpeed * dt;
    if (positions.array[offset + 1] < -halfHeight) positions.array[offset + 1] += height;
    positions.array[offset] += (
      Math.sin((index + 1) * 0.37) * precipitationEffect.drift * dt
    );
    if (positions.array[offset] > halfWidth) positions.array[offset] -= width;
    if (positions.array[offset] < -halfWidth) positions.array[offset] += width;
  }
  positions.needsUpdate = true;
}

function applyZoom(value) {
  environmentState.zoom = normalizeZoom(value);
  camera.zoom = environmentState.zoom;
  camera.updateProjectionMatrix();
  syncEnvironmentUI();
  return environmentState.zoom;
}

function resetModelWeatherMaterials() {
  for (const material of modelPreviewWeatherMaterials) {
    const baseColor = material.userData.nvBaseColor;
    const baseRoughness = material.userData.nvBaseRoughness;
    material.color.copy(baseColor);
    material.roughness = baseRoughness;
  }
}

function applyModelWeather() {
  resetModelWeatherMaterials();
  const active = (
    presentationMode === 'model'
    && viewerCapabilities.renderer.dynamic_mesh_relighting === true
    && modelPreviewWeatherMaterials.length > 0
  );
  if (!active) {
    renderer.toneMappingExposure = 1;
    modelPreviewKeyLight.color.setHex(0xffe4c4);
    modelPreviewKeyLight.intensity = 2.2;
    return false;
  }

  const response = meshWeatherResponse(environmentState.weather);
  const multiplier = new THREE.Color(...response.baseColorMultiplier);
  for (const material of modelPreviewWeatherMaterials) {
    material.color
      .copy(material.userData.nvBaseColor)
      .multiply(multiplier);
    material.roughness = THREE.MathUtils.clamp(
      material.userData.nvBaseRoughness * response.roughnessMultiplier,
      0,
      1,
    );
  }
  renderer.toneMappingExposure = response.exposure;
  modelPreviewKeyLight.color.setHex(response.keyColor);
  modelPreviewKeyLight.intensity = response.keyIntensity;
  return true;
}

function applyWeather(value) {
  environmentState.weather = normalizeWeather(value);
  const preset = getWeatherPreset(environmentState.weather);
  scene.background.setHex(preset.background);
  const fogNear = (currentFrame?.fogNear ?? 50) * preset.fog.nearScale;
  const fogFar = Math.max(
    fogNear + 1,
    (currentFrame?.fogFar ?? 500) * preset.fog.farScale,
  );
  scene.fog = new THREE.Fog(preset.fog.color, fogNear, fogFar);
  const atmosphereLight = atmosphereLightForRenderer(
    viewerCapabilities.renderer,
    preset.light,
    getWeatherPreset(DEFAULT_WEATHER).light,
  );
  hemisphereLight.color.setHex(atmosphereLight.sky);
  hemisphereLight.groundColor.setHex(atmosphereLight.ground);
  hemisphereLight.intensity = atmosphereLight.intensity;
  configurePrecipitation(preset.precipitation);
  applyModelWeather();
  syncEnvironmentUI();
  return environmentState.weather;
}

function setupEnvironmentControls() {
  document.getElementById('weather-control').addEventListener('change', (event) => {
    applyWeather(event.target.value);
  });
  document.getElementById('zoom-control').addEventListener('input', (event) => {
    applyZoom(Number(event.target.value));
  });
  document.getElementById('zoom-reset').addEventListener('click', () => {
    applyZoom(DEFAULT_ZOOM);
  });
  renderer.domElement.addEventListener('wheel', (event) => {
    if (cameraMode !== 'free') return;
    event.preventDefault();
    const direction = event.deltaY < 0 ? 1 : -1;
    applyZoom(environmentState.zoom + direction * ZOOM_STEP);
  }, { passive: false });
}

function setupDisplayMode() {
  const button = document.getElementById('display-toggle');
  const setFocused = (focused) => {
    document.body.classList.toggle('focus-mode', focused);
    button.setAttribute('aria-pressed', String(focused));
    button.textContent = focused ? '显示信息' : '专注画面';
  };
  const embedded = new URLSearchParams(window.location.search).get('embed') === '1';
  setFocused(embedded);
  button.addEventListener('click', () => {
    setFocused(!document.body.classList.contains('focus-mode'));
  });
}

function syncPresentationVisibility() {
  const pointsActive = presentationMode === 'points';
  const worldActive = pointsActive && worldVisible;
  const reconActive = pointsActive && reconVisible;

  for (const mesh of chunkMeshes.values()) mesh.visible = worldActive;
  for (const border of chunkBorders.values()) {
    border.visible = worldActive && bordersVisible;
  }
  if (gridHelper) gridHelper.visible = worldActive;
  if (axesHelper) axesHelper.visible = worldActive;
  if (reconMesh) reconMesh.visible = reconActive;
  splatLayer?.setVisible(reconActive);
  spatialSplatLayer?.setVisible(reconActive);
  spatialPointLayer?.setVisible(reconActive);
  if (modelPreviewRoot) modelPreviewRoot.visible = presentationMode === 'model';
  if (modelPreviewKeyLight) modelPreviewKeyLight.visible = presentationMode === 'model';

  const toggle = document.getElementById('presentation-toggle');
  const badge = document.getElementById('model-preview-badge');
  if (toggle) {
    toggle.hidden = !modelPreviewRoot;
    toggle.textContent = presentationMode === 'model' ? '查看点云' : '查看模型';
    toggle.setAttribute('aria-pressed', String(presentationMode === 'model'));
  }
  if (badge) badge.hidden = presentationMode !== 'model';
}

function applyModelPreviewFraming() {
  if (!modelPreviewBounds || modelPreviewBounds.isEmpty()) return;
  if (cameraMode === 'free') toggleCameraMode();

  if (modelPreviewManifest) {
    const authoredPose = modelPreviewCameraPose(modelPreviewManifest);
    if (authoredPose) {
      const {
        positionThree,
        targetThree,
        verticalFovDeg,
        near,
        far,
      } = authoredPose;
      camera.fov = verticalFovDeg;
      camera.near = near;
      camera.far = far;
      camera.position.set(...positionThree);
      camera.lookAt(...targetThree);
      camera.updateProjectionMatrix();
      controls.target.set(...targetThree);
      controls.maxDistance = 900;
      controls.update();
      applyZoom(DEFAULT_ZOOM);
      return;
    }
  }

  const center = modelPreviewBounds.getCenter(new THREE.Vector3());
  const size = modelPreviewBounds.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 1);
  if (modelPreviewEmbeddedCamera) {
    modelPreviewEmbeddedCamera.updateWorldMatrix(true, false);
    const position = modelPreviewEmbeddedCamera.getWorldPosition(new THREE.Vector3());
    const direction = modelPreviewEmbeddedCamera.getWorldDirection(new THREE.Vector3());
    if (
      position.toArray().every(Number.isFinite)
      && direction.toArray().every(Number.isFinite)
      && direction.lengthSq() > 0.5
    ) {
      const target = position.clone().addScaledVector(
        direction.normalize(),
        Math.max(radius * 0.25, 60),
      );
      camera.fov = modelPreviewEmbeddedCamera.fov;
      camera.near = Math.max(modelPreviewEmbeddedCamera.near, 0.05);
      camera.far = Math.max(modelPreviewEmbeddedCamera.far, radius * 6);
      camera.position.copy(position);
      camera.lookAt(target);
      camera.updateProjectionMatrix();
      controls.target.copy(target);
      controls.maxDistance = radius * 8;
      controls.update();
      applyZoom(DEFAULT_ZOOM);
      return;
    }
  }
  const verticalFov = THREE.MathUtils.degToRad(camera.fov);
  const distance = radius / Math.sin(verticalFov / 2) * 1.08;
  const direction = new THREE.Vector3(1, 0.72, 1).normalize();

  camera.near = Math.max(radius / 2000, 0.05);
  camera.far = Math.max(distance + radius * 6, 1000);
  camera.position.copy(center).addScaledVector(direction, distance);
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.maxDistance = radius * 8;
  controls.update();
  applyZoom(DEFAULT_ZOOM);
}

function reconstructionCapabilityMode() {
  const mode = activeReconstructionState()?.mode;
  return mode === 'spark' || mode === 'spark-chunks'
    ? mode
    : 'dc-point-preview';
}

function setPresentationMode(mode, { resetCamera = true } = {}) {
  if (mode !== 'points' && mode !== 'model') {
    throw new Error(`未知呈现模式: ${mode}`);
  }
  if (mode === 'model' && !modelPreviewRoot) {
    throw new Error('合成模型预览尚未加载');
  }
  presentationMode = mode;
  syncPresentationVisibility();
  if (resetCamera) {
    if (mode === 'model') {
      applyModelPreviewFraming();
    } else if (manifest && reconManifest !== undefined) {
      applyFraming(computeFraming(manifest, reconManifest), true);
      applyZoom(DEFAULT_ZOOM);
      updateChunks(camera.position.x, camera.position.z);
    }
  }
  const modelMode = modelPreviewManifest?.schema_version === 2
    ? 'textured-mesh-preview'
    : 'mesh-preview';
  viewerCapabilities = createViewerCapabilities(
    mode === 'model' ? modelMode : reconstructionCapabilityMode(),
  );
  applyWeather(environmentState.weather);
  viewerBridge?.announceCapabilities();
  updateHUD();
  return presentationMode;
}

function setupPresentationToggle() {
  document.getElementById('presentation-toggle').addEventListener('click', () => {
    try {
      setPresentationMode(presentationMode === 'model' ? 'points' : 'model');
    } catch (error) {
      console.warn('呈现模式切换失败:', error);
    }
  });
}

async function loadModelPreview(url = modelPreviewManifestUrl) {
  try {
    const absoluteManifestUrl = new URL(url, window.location.href);
    if (absoluteManifestUrl.origin !== window.location.origin) {
      throw new Error('Model preview manifest must be same-origin');
    }
    const manifestResponse = await fetch(absoluteManifestUrl.href);
    if (manifestResponse.status === 404) return { status: 'absent' };
    if (!manifestResponse.ok) {
      throw new Error(`Model preview manifest load failed: ${manifestResponse.status}`);
    }
    const nextManifest = validateModelPreviewManifest(await manifestResponse.json());
    const modelUrl = resolveModelPreviewUrl(
      absoluteManifestUrl.href,
      nextManifest,
      window.location.origin,
    );
    const modelResponse = await fetch(modelUrl);
    if (!modelResponse.ok) {
      throw new Error(`Model preview GLB load failed: ${modelResponse.status}`);
    }
    const bytes = await modelResponse.arrayBuffer();
    const expectedSha256 = modelPreviewSha256(nextManifest);
    await verifyModelPreviewBytes(bytes, expectedSha256);

    const loader = new GLTFLoader();
    const gltf = await loader.parseAsync(bytes, new URL('./', modelUrl).href);
    const root = gltf.scene;
    root.name = 'verified_synthetic_model_preview';
    root.visible = false;
    let meshCount = 0;
    root.traverse((object) => {
      if (object.isLight) object.visible = false;
      if (object.isMesh) meshCount += 1;
    });
    root.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(root);
    if (bounds.isEmpty()) throw new Error('Model preview GLB has no renderable bounds');
    const weatherMaterials = cloneModelWeatherMaterials(root, nextManifest);
    const embeddedCamera = selectEmbeddedModelPreviewCamera(
      nextManifest,
      gltf.cameras,
    );

    if (modelPreviewRoot) scene.remove(modelPreviewRoot);
    modelPreviewManifest = nextManifest;
    modelPreviewManifestUrl = absoluteManifestUrl.href;
    modelPreviewRoot = root;
    modelPreviewBounds = bounds;
    modelPreviewEmbeddedCamera = embeddedCamera;
    modelPreviewWeatherMaterials = weatherMaterials;
    scene.add(root);

    const badge = document.getElementById('model-preview-badge');
    if (badge) badge.textContent = modelPreviewDisclosure(nextManifest);
    const requestedMode = new URLSearchParams(window.location.search).get('presentation');
    setPresentationMode(requestedMode === 'points' ? 'points' : 'model');
    return {
      status: 'loaded',
      sha256: expectedSha256,
      mesh_count: nextManifest.counts?.mesh_objects ?? meshCount,
    };
  } catch (error) {
    modelPreviewManifest = null;
    modelPreviewRoot = null;
    modelPreviewBounds = null;
    modelPreviewEmbeddedCamera = null;
    modelPreviewWeatherMaterials = [];
    presentationMode = 'points';
    syncPresentationVisibility();
    console.warn('合成模型预览已拒绝，保留点云视图:', error);
    return { status: 'rejected', reason: error.message };
  }
}

function cloneModelWeatherMaterials(root, previewManifest) {
  if (
    previewManifest.schema_version !== 2
    || previewManifest.dynamic_mesh_relighting !== true
  ) return [];

  const clones = new Map();
  const cloneMaterial = (material) => {
    if (
      !material?.isMeshStandardMaterial
      || !material.color
      || !Number.isFinite(material.roughness)
    ) {
      throw new Error('Textured model relighting requires MeshStandardMaterial');
    }
    if (clones.has(material)) return clones.get(material);
    const clone = material.clone();
    clone.userData.nvBaseColor = clone.color.clone();
    clone.userData.nvBaseRoughness = clone.roughness;
    clones.set(material, clone);
    return clone;
  };

  root.traverse((object) => {
    if (!object.isMesh) return;
    if (Array.isArray(object.material)) {
      object.material = object.material.map(cloneMaterial);
    } else {
      object.material = cloneMaterial(object.material);
    }
  });
  if (clones.size === 0) {
    throw new Error('Textured model relighting requires renderable PBR materials');
  }
  return [...clones.values()];
}

function applyFraming(frame, resetCamera = true) {
  currentFrame = frame;
  camera.fov = VIEWER_FOV_DEG;
  camera.near = frame.near;
  camera.far = frame.far;
  camera.updateProjectionMatrix();
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
  gridHelper.visible = presentationMode === 'points' && worldVisible;
  scene.add(gridHelper);

  if (axesHelper) {
    scene.remove(axesHelper);
    axesHelper.geometry.dispose();
    axesHelper.material.dispose();
  }
  axesHelper = new THREE.AxesHelper(Math.max(chunkSizeM / 4, 1));
  axesHelper.visible = presentationMode === 'points' && worldVisible;
  scene.add(axesHelper);
  applyWeather(environmentState.weather);
}

// ============ 初始化 ============
function init() {
  setupDisplayMode();
  setupPresentationToggle();
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1a2228);

  camera = new THREE.PerspectiveCamera(
    VIEWER_FOV_DEG, window.innerWidth / window.innerHeight, 0.1, 1,
  );
  camera.position.set(0, 1, 1);
  camera.lookAt(0, 0, 0);

  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.domElement.tabIndex = 0;
  renderer.domElement.setAttribute('aria-label', '3D 场景画布');
  document.getElementById('canvas-container').appendChild(renderer.domElement);

  splatLayer = createSplatLayer({ scene, renderer });
  spatialSplatLayer = createSpatialSplatLayer({ scene, renderer });
  spatialPointLayer = createSpatialPointLayer({
    scene,
    loadPointMesh: async ({ key, lod, url }) => {
      const parsed = await loadPlyUrl(url, `reconstruction chunk ${key} LOD${lod}`);
      // Chunks are already in one absolute source frame. Apply only the
      // global ENU-to-Three axis mapping; never add a per-chunk translation.
      transformPositionsInPlace(parsed.positions);
      const mesh = makePointsMesh(parsed);
      mesh.name = `recon_chunk_${key}_lod${lod}`;
      return mesh;
    },
    disposeMesh: disposePointMesh,
  });

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.target.set(0, 0, 0);
  controls.minDistance = 0.5;
  setupFreeLookPointer();

  hemisphereLight = new THREE.HemisphereLight(0xbfd4ff, 0x404030, 0.6);
  scene.add(hemisphereLight);
  modelPreviewKeyLight = new THREE.DirectionalLight(0xffe4c4, 2.2);
  modelPreviewKeyLight.name = 'model_preview_key_light';
  modelPreviewKeyLight.position.set(240, 420, 180);
  modelPreviewKeyLight.visible = false;
  scene.add(modelPreviewKeyLight);
  try {
    createPrecipitationRuntime();
  } catch (error) {
    environmentState.precipitation_status = 'degraded';
    console.warn('降水粒子初始化失败:', error);
  }
  setupEnvironmentControls();
  applyWeather(DEFAULT_WEATHER);
  applyZoom(DEFAULT_ZOOM);

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
  if (['INPUT', 'SELECT', 'BUTTON'].includes(e.target?.tagName)) return;
  const k = e.key.toLowerCase();
  if (k in keys) keys[k] = true;
  if (e.key === 'Shift') keys.shift = true;
  // B 键切换 chunk 边界显示
  if (k === 'b') {
    bordersVisible = !bordersVisible;
    for (const b of chunkBorders.values()) {
      b.visible = presentationMode === 'points' && worldVisible && bordersVisible;
    }
  }
  // 1/2/3 强制画质 (低/中/高), 0 恢复按距离自动
  if (k === '1') qualityOverride = 0;
  if (k === '2') qualityOverride = 1;
  if (k === '3') qualityOverride = 2;
  if (k === '0') qualityOverride = null;
  // R 键切换重建预览图层
  if (k === 'r' && reconManifest) {
    reconVisible = !reconVisible;
    syncPresentationVisibility();
  }
  // F 键切换 自由/环绕 视角 (忽略长按重复与修饰键组合)
  if (k === 'f' && !e.repeat && !e.ctrlKey && !e.metaKey && !e.altKey) {
    toggleCameraMode();
  }
  // G 键传送到指定 ENU 坐标
  if (k === 'g' && !e.repeat && !e.ctrlKey && !e.metaKey && !e.altKey) {
    teleportPrompt();
  }
}
function onKeyUp(e) {
  const k = e.key.toLowerCase();
  if (k in keys) keys[k] = false;
  if (e.key === 'Shift') keys.shift = false;
}

// ============ 自由视角 (free) ============
// free 模式下按 freeLook 的 yaw/pitch 重新对准相机 (lookAt = position + 视线方向)。
function applyFreeLook() {
  const [dx, dy, dz] = directionFromYawPitchThree(freeLook.yaw, freeLook.pitch);
  camera.lookAt(camera.position.x + dx, camera.position.y + dy, camera.position.z + dz);
}

// F 键: orbit ↔ free 切换 (切换瞬间保持视线连续)。
function toggleCameraMode() {
  if (cameraMode === 'orbit') {
    orbitDistance = camera.position.distanceTo(controls.target);
    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    const { yaw, pitch } = yawPitchFromDirectionThree([dir.x, dir.y, dir.z]);
    freeLook.yaw = yaw;
    freeLook.pitch = clampPitch(pitch);
    cameraMode = 'free';
    controls.enabled = false;
  } else {
    // free → orbit: 把 target 放到相机前方，保持当前朝向
    const dir = directionFromYawPitchThree(freeLook.yaw, freeLook.pitch);
    const dist = Math.min(orbitDistance, 50);
    controls.target.set(
      camera.position.x + dir[0] * dist,
      camera.position.y + dir[1] * dist,
      camera.position.z + dir[2] * dist,
    );
    cameraMode = 'orbit';
    controls.enabled = true;
    controls.update();
  }
}

// free 模式鼠标环顾: 主键拖拽改变 yaw/pitch。orbit 模式下直接 return，
// 与 OrbitControls (enabled=false 时不消费事件) 互不冲突。
function setupFreeLookPointer() {
  const el = renderer.domElement;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  el.addEventListener('pointerdown', (event) => {
    if (cameraMode !== 'free' || event.button !== 0) return;
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    el.setPointerCapture(event.pointerId);
  });
  el.addEventListener('pointermove', (event) => {
    if (cameraMode !== 'free' || !dragging) return;
    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    lastX = event.clientX;
    lastY = event.clientY;
    freeLook.yaw -= dx * 0.0032;
    freeLook.pitch = clampPitch(freeLook.pitch - dy * 0.0032);
    applyFreeLook();
  });
  const endDrag = (event) => {
    if (!dragging) return;
    dragging = false;
    if (el.hasPointerCapture?.(event.pointerId)) el.releasePointerCapture(event.pointerId);
  };
  el.addEventListener('pointerup', endDrag);
  el.addEventListener('pointercancel', endDrag);
}

// 传送 / 姿态设置的共用内部实现 (setCameraPose 与 G 键复用)。
function moveCameraTo(positionThree, lookAtThree = null) {
  const previous = camera.position.clone();
  camera.position.set(...positionThree);
  const delta = camera.position.clone().sub(previous);

  if (lookAtThree) {
    if (cameraMode === 'free') {
      const { yaw, pitch } = yawPitchFromDirectionThree([
        lookAtThree[0] - positionThree[0],
        lookAtThree[1] - positionThree[1],
        lookAtThree[2] - positionThree[2],
      ]);
      freeLook.yaw = yaw;
      freeLook.pitch = clampPitch(pitch);
      applyFreeLook();
    } else {
      controls.target.set(...lookAtThree);
      camera.lookAt(...lookAtThree);
      controls.update();
    }
  } else if (cameraMode === 'free') {
    // 保持 yaw/pitch，位移后重新对准
    applyFreeLook();
  } else {
    // orbit: target 平移相同增量，保持朝向
    controls.target.add(delta);
    controls.update();
  }
  // 传送后立即拉取新位置的 chunk
  updateChunks(camera.position.x, camera.position.z);
}

// G 键: 输入 ENU 坐标传送 (无 look_at 分支，保持当前朝向)。
function teleportPrompt() {
  const input = window.prompt('传送到 E,N,U (米):');
  if (input === null) return;  // 用户取消
  try {
    const { east, north, up } = parseEnuText(input);
    moveCameraTo(worldToThree([east, north, up]), null);
  } catch (error) {
    console.warn('传送失败:', error.message);
  }
}

// ============ 相机控制 (WASD) ============
function updateCamera(dt) {
  const speed = (keys.shift ? 250 : 80) * dt;

  if (cameraMode === 'free') {
    // 6-DOF 飞行: 只移动相机本身，朝向由 freeLook 决定
    const [dx, dy, dz] = flyDisplacementThree(freeLook.yaw, freeLook.pitch, keys, speed);
    camera.position.x += dx;
    camera.position.y += dy;
    camera.position.z += dz;
    applyFreeLook();
    return;
  }

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
    const artifact = await res.json();
    if (artifact?.kind === 'spatial-chunks' && !isSpatialChunkManifest(artifact)) {
      return false;
    }
    reconManifest = artifact;
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

function activeReconstructionState() {
  const spatialSpark = spatialSplatLayer?.getState();
  if (spatialSpark?.mode === 'spark-chunks') return spatialSpark;
  const spatialPoints = spatialPointLayer?.getState();
  if (spatialPoints?.mode === 'dc-point-chunks') {
    return {
      ...spatialPoints,
      spark_fallback_reason: spatialSplatFallbackReason,
    };
  }
  return splatLayer?.getState() ?? null;
}

async function updateSpatialRecon() {
  if (spatialReconUpdating || !isSpatialChunkManifest(reconManifest)) return;
  const activeLayer = activeReconstructionState()?.mode === 'spark-chunks'
    ? spatialSplatLayer : spatialPointLayer;
  if (!activeLayer) return;
  spatialReconUpdating = true;
  try {
    await activeLayer.update({
      cameraWorld: threeToWorld([
        camera.position.x, camera.position.y, camera.position.z,
      ]),
      lodOverride: qualityOverride,
    });
  } finally {
    spatialReconUpdating = false;
  }
}

async function updateRecon() {
  if (!reconManifest || reconLoading) return;
  if (isSpatialChunkManifest(reconManifest)) {
    await updateSpatialRecon();
    return;
  }
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
    mesh.visible = presentationMode === 'points' && reconVisible;
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

function disposeSpatialReconstruction() {
  spatialSplatLayer?.dispose();
  spatialPointLayer?.dispose();
  spatialReconUpdating = false;
  spatialSplatFallbackReason = null;
}

async function loadReconstructionLayer() {
  disposeReconPointPreview();
  if (isSpatialChunkManifest(reconManifest)) {
    splatLayer?.dispose();
    spatialPointLayer?.dispose();
    const sparkResult = await spatialSplatLayer.load({
      manifest: reconManifest,
      manifestUrl: reconManifestUrl,
      visible: reconVisible,
    });
    if (isSupersededLoadResult(sparkResult)) return sparkResult;

    let result = sparkResult;
    if (sparkResult.mode !== 'spark-chunks') {
      spatialSplatFallbackReason = sparkResult.reason;
      const pointResult = spatialPointLayer.load({
        manifest: reconManifest,
        manifestUrl: reconManifestUrl,
        visible: reconVisible,
      });
      result = {
        ...pointResult,
        spark_fallback_reason: spatialSplatFallbackReason,
      };
      console.warn(`${spatialSplatFallbackReason}; 使用分块 DC point preview`);
    }
    viewerCapabilities = createViewerCapabilities(result.mode);
    viewerBridge?.announceCapabilities();
    await updateSpatialRecon();
    return result;
  }

  disposeSpatialReconstruction();
  const result = await splatLayer.load({
    manifest: reconManifest ?? {},
    manifestUrl: reconManifestUrl,
    visible: reconVisible,
  });
  if (isSupersededLoadResult(result)) return result;
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

  const modeEl = document.getElementById('hud-mode');
  if (modeEl) {
    modeEl.textContent = cameraMode === 'free' ? '自由 (F 切换)' : '环绕 (F 切换)';
  }

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
    const rendererState = activeReconstructionState();
    const spatial = isSpatialChunkManifest(reconManifest);
    const estimatedSpatialPoints = Number.isSafeInteger(
      rendererState?.active_estimated_points,
    )
      ? ` · ~${rendererState.active_estimated_points.toLocaleString()} splats`
      : '';
    reconEl.textContent = presentationMode === 'model'
      ? '当前未展示（独立合成模型模式）'
      : !reconManifest ? '无'
      : !reconVisible ? '已隐藏 (R 显示)'
      : spatial
        ? `${rendererState?.active ?? 0}/${reconManifest.chunks.length} chunks`
          + ` (${viewerCapabilities.renderer.label})${estimatedSpatialPoints}`
      : rendererState?.mode === 'spark'
        ? `${count} splats (${viewerCapabilities.renderer.label})`
      : reconLodLoaded >= 0
        ? `LOD${reconLodLoaded} (${count} points, ${viewerCapabilities.renderer.label})`
      : '加载中...';
  }
  const presentationEl = document.getElementById('hud-presentation');
  if (presentationEl) {
    const trust = modelPreviewManifest
      ? modelPreviewTrustMetadata(modelPreviewManifest)
      : null;
    presentationEl.textContent = presentationMode === 'model'
      ? `合成 GLB 网格（${trust?.fidelity ?? 'preview-only'}）`
      : '点云 / 高斯';
  }

  const rendererReason = document.getElementById('hud-renderer-reason');
  if (rendererReason) {
    const rendererState = activeReconstructionState();
    rendererReason.textContent = presentationMode === 'model'
      ? modelPreviewDisclosure(modelPreviewManifest)
      : (
      rendererState?.mode === 'spark'
      || rendererState?.mode === 'spark-chunks'
    )
      ? 'full_3dgs 已由 Spark 初始化'
      : (
        rendererState?.spark_fallback_reason
        ?? rendererState?.reason
        ?? 'DC point preview'
      );
  }

  const title = document.getElementById('hud-title');
  if (title) {
    title.textContent = presentationMode === 'model'
      ? 'Nantai Village · 合成网格模型预览'
      : `Nantai Village · ${viewerCapabilities.renderer.label}`;
  }

  const modelTrust = modelPreviewManifest
    ? modelPreviewTrustMetadata(modelPreviewManifest)
    : null;
  const provenance = presentationMode === 'model' && modelPreviewManifest
    ? {
      requested_engine: 'not-applicable',
      actual_engine: modelPreviewManifest.schema_version === 2
        ? 'verified-local-l0-glb'
        : 'verified-release-glb',
      synthetic: true,
      frame: modelTrust.coordinate_frame.frame_id,
      units: modelTrust.coordinate_frame.units,
      handedness: 'right-handed',
      geometry_usability: modelPreviewManifest.geometry_usability,
      artifact_fidelity: modelTrust.fidelity,
      viewer_fidelity: viewerCapabilities.renderer.fidelity,
    }
    : artifactProvenance(reconManifest ?? {}, viewerCapabilities);
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

  const coverage = coverageAuditViewModel(coverageAudit);
  const coverageFields = {
    'hud-coverage-status': {
      value: coverage.summary,
      color: coverage.color,
    },
    'hud-coverage-visibility': coverage.layers.visibility,
    'hud-coverage-geometry': coverage.layers.geometry,
    'hud-coverage-sfm': coverage.layers.sfm,
    'hud-coverage-provenance': coverage.layers.provenance,
  };
  for (const [id, field] of Object.entries(coverageFields)) {
    const element = document.getElementById(id);
    if (!element) continue;
    element.textContent = String(field.value ?? field.label);
    element.style.color = field.color;
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
  try {
    modelPreviewManifestUrl = resolveRequestedModelPreviewManifestUrl(
      window.location.href,
      modelPreviewManifestUrl,
    );
  } catch (error) {
    console.warn('已拒绝不安全的模型预览入口，使用内置预览:', error);
  }
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
  applyFraming(computeFraming(manifest, reconManifest));
  await loadReconstructionLayer();
  loadingText.textContent = '检查已校验的合成模型预览...';
  const modelPreviewResult = await loadModelPreview();

  const xCount = new Set(manifest.chunks.map((chunk) => chunk.x)).size;
  const yCount = new Set(manifest.chunks.map((chunk) => chunk.y)).size;
  const onDemand = worldChunkAvailable(manifest, false);
  loadingText.textContent =
    `已索引 ${manifest.chunks.length} chunks (${xCount}×${yCount})`
    + `${onDemand ? '，按需无限扩展已启用' : ''}，启动调度器...`;
  if (modelPreviewResult.status === 'loaded') {
    loadingText.textContent =
      `已校验合成模型 SHA-256，加载 ${modelPreviewResult.mesh_count} 个 mesh...`;
  }
  const minimapTitle = document.getElementById('minimap-title');
  if (minimapTitle) {
    minimapTitle.textContent = `Mini-map (${xCount}×${yCount}${onDemand ? ' + on-demand' : ''})`;
  }

  // 初始加载相机视野内 chunk
  const initPos = controls.target;
  if (presentationMode === 'points') updateChunks(initPos.x, initPos.z);

  // 等待初始加载 (轮询直到视野内所有 chunk 都加载完)
  const waitInit = () => new Promise(resolve => {
    const check = () => {
      const [cx, cy] = threeToChunk([initPos.x, initPos.y, initPos.z], chunkSizeM);
      let needed = 0, loaded = 0;
      for (let dx = -CHUNK_VIEW_RADIUS; dx <= CHUNK_VIEW_RADIUS; dx++) {
        for (let dy = -CHUNK_VIEW_RADIUS; dy <= CHUNK_VIEW_RADIUS; dy++) {
          const key = `${cx+dx}_${cy+dy}`;
          if (worldChunkAvailable(manifest, chunkIndex.has(key))) {
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
  if (presentationMode === 'points') await waitInit();

  document.getElementById('loading').style.display = 'none';
  document.getElementById('hud').style.display = 'block';
  document.getElementById('controls').style.display = 'block';
  document.getElementById('minimap-wrap').style.display = 'block';
  document.getElementById('environment-controls').style.display = 'block';

  const readState = () => {
    const [east, north, up] = threeToWorld([
      camera.position.x, camera.position.y, camera.position.z,
    ]);
    const modelTrust = modelPreviewManifest
      ? modelPreviewTrustMetadata(modelPreviewManifest)
      : null;
    return {
      renderer: viewerCapabilities.renderer,
      capabilities: viewerCapabilities,
      renderer_status: activeReconstructionState(),
      presentation: {
        mode: presentationMode,
        model_preview: modelPreviewManifest
          ? {
            synthetic: true,
            geometry_usability: modelPreviewManifest.geometry_usability,
            fidelity: modelTrust.fidelity,
            photo_textures: modelTrust.photo_textures,
            sha256: modelTrust.sha256,
          }
          : null,
      },
      lod: qualityOverride ?? 'auto',
      layers: {
        world: worldVisible,
        reconstruction: reconVisible,
        model: presentationMode === 'model',
      },
      chunk_size_m: chunkSizeM,
      bounds: currentFrame?.bounds ?? null,
      artifact: artifactProvenance(reconManifest ?? {}, viewerCapabilities),
      coverage: coverageAuditViewModel(coverageAudit),
      camera: { position: { east, north, up }, mode: cameraMode },
      environment: {
        ...environmentState,
        mesh_relighting: viewerCapabilities.renderer.dynamic_mesh_relighting === true,
        splat_relighting: viewerCapabilities.renderer.splat_relighting === true,
      },
    };
  };

  viewerBridge = createViewerBridge({
    windowObject: window,
    capabilities: () => viewerCapabilities,
    handlers: {
      getState: () => readState(),
      setCameraPose: (payload) => {
        const { positionThree, lookAtThree } = normalizeCameraPose(payload);
        moveCameraTo(positionThree, lookAtThree);
        return readState();
      },
      setWeather: ({ weather }) => {
        applyWeather(weather);
        return readState();
      },
      setZoom: ({ zoom }) => {
        applyZoom(zoom);
        return readState();
      },
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
        } else if (layer === 'reconstruction') {
          reconVisible = nextVisible;
        } else {
          throw new Error(`未知图层: ${layer}`);
        }
        syncPresentationVisibility();
        return readState();
      },
      resetCamera: () => {
        if (presentationMode === 'model') {
          applyModelPreviewFraming();
        } else {
          if (currentFrame) applyFraming(currentFrame, true);
          applyZoom(DEFAULT_ZOOM);
        }
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
        if (
          kind !== 'recon-manifest'
          && kind !== 'chunk-manifest'
          && kind !== 'coverage-audit'
        ) {
          throw new Error(`不支持的 artifact kind: ${kind}`);
        }
        if (kind === 'coverage-audit') {
          let nextAudit = artifact;
          let nextUrl = window.location.href;
          if (url) {
            const absoluteUrl = new URL(url, window.location.href);
            if (absoluteUrl.origin !== window.location.origin) {
              throw new Error('artifact URL 必须与 viewer 同源');
            }
            const response = await fetch(absoluteUrl.href);
            if (!response.ok) {
              throw new Error(`无法加载 coverage audit: ${response.status}`);
            }
            nextAudit = await response.json();
            nextUrl = absoluteUrl.href;
          } else if (!artifact) {
            throw new Error('loadArtifact 需要 url 或 manifest');
          }
          if (!isCoverageAudit(nextAudit)) {
            throw new Error('无效的 coverage-audit artifact');
          }
          coverageAudit = nextAudit;
          coverageAuditUrl = nextUrl;
          updateHUD();
          return {
            kind,
            url: coverageAuditUrl,
            coverage: coverageAuditViewModel(coverageAudit),
            state: readState(),
          };
        }
        if (url) {
          const absoluteUrl = new URL(url, window.location.href);
          if (absoluteUrl.origin !== window.location.origin) {
            throw new Error('artifact URL 必须与 viewer 同源');
          }
          if (!await loadReconManifest(absoluteUrl.href)) {
            throw new Error(`无法加载 artifact: ${absoluteUrl.href}`);
          }
          if (kind === 'chunk-manifest' && !isSpatialChunkManifest(reconManifest)) {
            throw new Error('chunk-manifest 必须是有效的 spatial-chunks manifest');
          }
        } else if (artifact) {
          if (
            (kind === 'chunk-manifest' || artifact.kind === 'spatial-chunks')
            && !isSpatialChunkManifest(artifact)
          ) {
            throw new Error('无效的 spatial-chunks manifest');
          }
          reconManifest = artifact;
          reconManifestUrl = window.location.href;
        } else {
          throw new Error('loadArtifact 需要 url 或 manifest');
        }

        if (presentationMode === 'points') {
          applyFraming(computeFraming(manifest, reconManifest), false);
        }
        const rendererResult = await loadReconstructionLayer();
        if (presentationMode === 'model') {
          viewerCapabilities = createViewerCapabilities('mesh-preview');
          viewerBridge?.announceCapabilities();
        }
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
  if (cameraMode === 'orbit') controls.update();
  updatePrecipitation(dt);

  // 每 50ms 调度一次 chunk (避免每帧都触发)
  const now = performance.now();
  if (!animate._lastCheck || now - animate._lastCheck > 50) {
    animate._lastCheck = now;
    if (presentationMode === 'points') {
      updateChunks(camera.position.x, camera.position.z);
      updateRecon();
    }
  }

  updateHUD();
  drawMinimap();
  renderer.render(scene, camera);
}

main().catch(err => {
  console.error(err);
  document.getElementById('loading-text').textContent = `错误: ${err.message}`;
});
