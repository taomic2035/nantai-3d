# Viewer Weather and Zoom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持 360°、ENU 任意坐标漫游和按需无限地图不变的前提下，为 Viewer 增加六种实时天气、统一光学缩放、可访问控件和 Studio bridge 命令。

**Architecture:** `environment.mjs` 是唯一的环境状态、天气 preset、缩放边界和确定性粒子布局真相源；`main.js` 只把纯数据适配到 Three.js、DOM 和相机。`bridge.mjs` 只声明命令能力，现有同源消息路由不引入天气专用分支。天气是 `viewer-runtime` 效果，不进入 artifact provenance，也不触发 chunk/reconstruction 重载。

**Tech Stack:** 原生 ES modules、Node.js 20 `node:test`、Three.js 0.180.0、OrbitControls、Spark 2.1.0、原生 HTML/CSS。

## Global Constraints

- 只在单一 `main` 分支工作；禁止新分支和 worktree。
- 每个 task 必须 Red→Green、路径限定暂存、独立提交并立即 `git push origin main`。
- weather id 仅允许 `clear`、`overcast`、`rain`、`snow`、`fog`、`night`。
- 光学缩放范围固定为 `0.5x–3.0x`，默认 `1.0x`，步进 `0.1x`。
- 雨最多 1200 粒子，雪最多 800 粒子；一个 `THREE.Points` 对象复用，数量不得随漫游增长。
- 天气/缩放不得调用 `updateChunks()`、`updateRecon()`，不得修改 world/recon manifest 或 artifact provenance。
- 环绕模式滚轮继续 OrbitControls 推拉；自由模式滚轮调整光学缩放；两种模式的滑杆和 bridge 都设置 `PerspectiveCamera.zoom`。
- `resetCamera` 同时恢复 framing 与 `1.0x` 光学缩放；切换视角、传送、LOD、天气均保留当前缩放。
- 无效 weather、非数字/NaN/Infinity zoom 必须失败；有限越界 zoom 限制到最近边界。
- 所有新增依赖保持离线闭合；不增加第三方包。

---

## File Map

- Create `web/viewer/environment.mjs`: 纯天气 preset、输入归一化、确定性粒子布局。
- Create `web/viewer/environment.test.mjs`: 纯契约与边界测试。
- Modify `web/viewer/bridge.mjs`: capability 命令白名单加入 `setWeather`、`setZoom`。
- Modify `web/viewer/bridge.test.mjs`: 新命令路由、错误和 provenance 隔离测试。
- Create `web/viewer/index-contract.test.mjs`: HTML 可访问性与 `main.js` 接线静态契约。
- Modify `web/viewer/index.html`: 环境控制卡、HUD 状态和响应式样式。
- Modify `web/viewer/main.js`: Three.js 天气运行时、粒子、缩放、UI、bridge 和 state 接入。

### Task 1: Pure Environment Contract

**Files:**
- Create: `web/viewer/environment.mjs`
- Create: `web/viewer/environment.test.mjs`

**Interfaces:**
- Consumes: 无；模块不得导入 DOM 或 Three.js。
- Produces: `WEATHER_IDS`, `WEATHER_PRESETS`, `DEFAULT_WEATHER`, `DEFAULT_ZOOM`, `ZOOM_MIN`, `ZOOM_MAX`, `ZOOM_STEP`, `normalizeWeather(value)`, `normalizeZoom(value)`, `getWeatherPreset(value)`, `createPrecipitationPositions(value)`。

- [ ] **Step 1: Write the failing environment contract tests**

Create `web/viewer/environment.test.mjs`:

```js
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_WEATHER,
  DEFAULT_ZOOM,
  WEATHER_IDS,
  WEATHER_PRESETS,
  ZOOM_MAX,
  ZOOM_MIN,
  createPrecipitationPositions,
  getWeatherPreset,
  normalizeWeather,
  normalizeZoom,
} from './environment.mjs';

test('weather ids and defaults are stable', () => {
  assert.deepEqual(WEATHER_IDS, [
    'clear', 'overcast', 'rain', 'snow', 'fog', 'night',
  ]);
  assert.equal(DEFAULT_WEATHER, 'clear');
  assert.equal(DEFAULT_ZOOM, 1);
  for (const id of WEATHER_IDS) {
    assert.equal(normalizeWeather(id), id);
    assert.equal(getWeatherPreset(id), WEATHER_PRESETS[id]);
    assert.equal(Object.isFrozen(WEATHER_PRESETS[id]), true);
  }
});

test('unknown weather ids fail instead of silently falling back', () => {
  assert.throws(() => normalizeWeather('storm'), /未知天气/);
  assert.throws(() => normalizeWeather('CLEAR'), /未知天气/);
  assert.equal(normalizeWeather(undefined), DEFAULT_WEATHER);
});

test('zoom rejects non-numbers and clamps finite values', () => {
  assert.equal(normalizeZoom(undefined), DEFAULT_ZOOM);
  assert.equal(normalizeZoom(1.25), 1.25);
  assert.equal(normalizeZoom(-10), ZOOM_MIN);
  assert.equal(normalizeZoom(99), ZOOM_MAX);
  for (const value of ['1', null, NaN, Infinity, -Infinity]) {
    assert.throws(() => normalizeZoom(value), /缩放必须是有限数字/);
  }
});

test('precipitation layouts are deterministic and hard capped', () => {
  const rainA = createPrecipitationPositions('rain');
  const rainB = createPrecipitationPositions('rain');
  const snow = createPrecipitationPositions('snow');
  assert.equal(rainA.length, 1200 * 3);
  assert.equal(snow.length, 800 * 3);
  assert.deepEqual(rainA, rainB);
  assert.equal(createPrecipitationPositions('clear').length, 0);
  assert.equal(WEATHER_PRESETS.rain.precipitation.count <= 1200, true);
  assert.equal(WEATHER_PRESETS.snow.precipitation.count <= 800, true);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
node --test web/viewer/environment.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `environment.mjs`.

- [ ] **Step 3: Implement the pure environment module**

Create `web/viewer/environment.mjs` with these exact data shapes and rules:

```js
export const WEATHER_IDS = Object.freeze([
  'clear', 'overcast', 'rain', 'snow', 'fog', 'night',
]);
export const DEFAULT_WEATHER = 'clear';
export const DEFAULT_ZOOM = 1;
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 3;
export const ZOOM_STEP = 0.1;

function freezePreset(preset) {
  if (preset.fog) Object.freeze(preset.fog);
  if (preset.light) Object.freeze(preset.light);
  if (preset.precipitation) {
    Object.freeze(preset.precipitation.volume);
    Object.freeze(preset.precipitation);
  }
  return Object.freeze(preset);
}

export const WEATHER_PRESETS = Object.freeze({
  clear: freezePreset({
    label: '晴', background: 0x8fc5e8,
    fog: { color: 0x8fc5e8, nearScale: 1.15, farScale: 1.35 },
    light: { sky: 0xd8efff, ground: 0x4b5035, intensity: 0.9 },
    precipitation: null,
  }),
  overcast: freezePreset({
    label: '阴', background: 0x667582,
    fog: { color: 0x667582, nearScale: 0.8, farScale: 1.0 },
    light: { sky: 0xaeb9c2, ground: 0x3e423d, intensity: 0.62 },
    precipitation: null,
  }),
  rain: freezePreset({
    label: '雨', background: 0x394b5b,
    fog: { color: 0x465867, nearScale: 0.55, farScale: 0.78 },
    light: { sky: 0x8599aa, ground: 0x303734, intensity: 0.48 },
    precipitation: {
      kind: 'rain', count: 1200, color: 0xaedcff, pointSize: 8,
      opacity: 0.72, fallSpeed: 32, drift: 1.2, volume: [70, 42, 70],
    },
  }),
  snow: freezePreset({
    label: '雪', background: 0xa9b8c3,
    fog: { color: 0xb8c4cc, nearScale: 0.5, farScale: 0.72 },
    light: { sky: 0xe8f0f5, ground: 0x68716d, intensity: 0.72 },
    precipitation: {
      kind: 'snow', count: 800, color: 0xffffff, pointSize: 6,
      opacity: 0.88, fallSpeed: 4.5, drift: 1.8, volume: [64, 36, 64],
    },
  }),
  fog: freezePreset({
    label: '雾', background: 0x899497,
    fog: { color: 0x899497, nearScale: 0.12, farScale: 0.32 },
    light: { sky: 0xc4cbca, ground: 0x5c605b, intensity: 0.5 },
    precipitation: null,
  }),
  night: freezePreset({
    label: '夜', background: 0x07111f,
    fog: { color: 0x0b1725, nearScale: 0.65, farScale: 0.95 },
    light: { sky: 0x354e72, ground: 0x111821, intensity: 0.28 },
    precipitation: null,
  }),
});

export function normalizeWeather(value = DEFAULT_WEATHER) {
  if (typeof value !== 'string' || !WEATHER_IDS.includes(value)) {
    throw new Error(`未知天气: ${String(value)}`);
  }
  return value;
}

export function normalizeZoom(value = DEFAULT_ZOOM) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error('缩放必须是有限数字');
  }
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
}

export function getWeatherPreset(value = DEFAULT_WEATHER) {
  return WEATHER_PRESETS[normalizeWeather(value)];
}

function deterministicUnit(index, axis) {
  const raw = Math.sin((index + 1) * 12.9898 + axis * 78.233) * 43758.5453;
  return raw - Math.floor(raw);
}

export function createPrecipitationPositions(value = DEFAULT_WEATHER) {
  const effect = getWeatherPreset(value).precipitation;
  if (!effect) return new Float32Array(0);
  const [width, height, depth] = effect.volume;
  const positions = new Float32Array(effect.count * 3);
  for (let index = 0; index < effect.count; index += 1) {
    const offset = index * 3;
    positions[offset] = (deterministicUnit(index, 0) - 0.5) * width;
    positions[offset + 1] = deterministicUnit(index, 1) * height;
    positions[offset + 2] = (deterministicUnit(index, 2) - 0.5) * depth;
  }
  return positions;
}
```

- [ ] **Step 4: Run focused and Viewer tests and verify GREEN**

Run:

```bash
node --test web/viewer/environment.test.mjs
node --test web/viewer/*.test.mjs
```

Expected: both commands PASS; the Viewer suite count increases by four tests.

- [ ] **Step 5: Commit and push the pure contract**

```bash
git add web/viewer/environment.mjs web/viewer/environment.test.mjs
git diff --cached --check
git commit -m "feat(viewer): define weather and zoom state"
git push origin main
```

Commit must include the exact trailer `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.

### Task 2: Bridge Capabilities and Routing

**Files:**
- Modify: `web/viewer/bridge.mjs:8-16`
- Modify: `web/viewer/bridge.test.mjs:42-97,136-178,249-318`

**Interfaces:**
- Consumes: existing `createViewerBridge({ windowObject, handlers, capabilities })` generic routing.
- Produces: capabilities advertise `setWeather` and `setZoom`; supplied handlers receive unchanged payloads and return `stateChanged`.

- [ ] **Step 1: Add failing capability, routing, and isolation tests**

Extend the first capability test:

```js
assert.ok(VIEWER_CAPABILITIES.commands.includes('setWeather'));
assert.ok(VIEWER_CAPABILITIES.commands.includes('setZoom'));
```

Add:

```js
test('environment commands route payloads and return stateChanged', async () => {
  const { createViewerBridge } = subject();
  const fake = fakeWindow();
  const calls = [];
  const bridge = createViewerBridge({
    windowObject: fake.windowObject,
    handlers: {
      setWeather: ({ weather }) => {
        calls.push(['weather', weather]);
        return { environment: { weather, zoom: 1 } };
      },
      setZoom: ({ zoom }) => {
        calls.push(['zoom', zoom]);
        return { environment: { weather: 'clear', zoom } };
      },
    },
  });

  await bridge.handleMessage({
    origin: fake.windowObject.location.origin,
    source: fake.parent,
    data: command('setWeather', 'weather-1', { weather: 'snow' }),
  });
  await bridge.handleMessage({
    origin: fake.windowObject.location.origin,
    source: fake.parent,
    data: command('setZoom', 'zoom-1', { zoom: 2.5 }),
  });

  assert.deepEqual(calls, [['weather', 'snow'], ['zoom', 2.5]]);
  assert.deepEqual(fake.sent.map(({ message }) => message.type), [
    'stateChanged', 'stateChanged',
  ]);
  assert.equal(fake.sent[0].message.request_id, 'weather-1');
  assert.equal(fake.sent[1].message.request_id, 'zoom-1');
});

test('runtime environment fields never alter artifact provenance', () => {
  const { artifactProvenance } = subject();
  const manifest = {
    actual_engine: 'imported-3dgs',
    synthetic: false,
    environment: { weather: 'rain', zoom: 3, effect_source: 'viewer-runtime' },
  };
  assert.deepEqual(artifactProvenance(manifest), {
    requested_engine: 'unknown',
    actual_engine: 'imported-3dgs',
    synthetic: false,
    frame: 'unknown',
    units: 'unknown',
    handedness: 'unknown',
    geometry_usability: 'unknown',
    artifact_fidelity: 'unknown',
    viewer_fidelity: 'dc-point-preview',
  });
});
```

- [ ] **Step 2: Run bridge tests and verify RED**

Run:

```bash
node --test web/viewer/bridge.test.mjs
```

Expected: capability assertions fail and routed commands return `unsupported-command`.

- [ ] **Step 3: Add the commands to the existing capability whitelist**

In `BASE_CAPABILITIES.commands`, add exactly:

```js
'setWeather',
'setZoom',
```

Do not add command-specific routing; the existing generic handler lookup remains the single path.

- [ ] **Step 4: Run bridge and all Viewer tests and verify GREEN**

```bash
node --test web/viewer/bridge.test.mjs
node --test web/viewer/*.test.mjs
```

Expected: both PASS and all existing cross-origin, request id and provenance tests remain green.

- [ ] **Step 5: Commit and push the bridge contract**

```bash
git add web/viewer/bridge.mjs web/viewer/bridge.test.mjs
git diff --cached --check
git commit -m "feat(viewer): expose environment bridge commands"
git push origin main
```

Commit must include the exact trailer.

### Task 3: Accessible Environment Controls

**Files:**
- Create: `web/viewer/index-contract.test.mjs`
- Modify: `web/viewer/index.html:9-59,64-114`

**Interfaces:**
- Consumes: stable ids used by Task 4.
- Produces: `weather-control`, `zoom-control`, `zoom-value`, `zoom-reset`, `environment-status`, `hud-weather`, `hud-zoom`, `environment-controls`.

- [ ] **Step 1: Write the failing HTML contract test**

Create `web/viewer/index-contract.test.mjs`:

```js
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');

test('environment controls expose six weather ids and bounded zoom', () => {
  assert.match(html, /id="environment-controls"[^>]*aria-label="环境控制"/);
  assert.match(html, /<label[^>]*for="weather-control"/);
  assert.match(html, /id="weather-control"/);
  for (const id of ['clear', 'overcast', 'rain', 'snow', 'fog', 'night']) {
    assert.match(html, new RegExp(`<option value="${id}"`));
  }
  assert.match(html, /<label[^>]*for="zoom-control"/);
  assert.match(html, /id="zoom-control"[^>]*min="0\.5"[^>]*max="3"[^>]*step="0\.1"/);
  assert.match(html, /id="zoom-value"[^>]*aria-live="polite"/);
  assert.match(html, /id="zoom-reset"[^>]*type="button"/);
});

test('environment status and HUD values are visible but separate from provenance', () => {
  assert.match(html, /id="environment-status"[^>]*aria-live="polite"/);
  assert.match(html, /id="hud-weather"/);
  assert.match(html, /id="hud-zoom"/);
  const provenance = html.match(/<div class="provenance">([\s\S]*?)<\/div>\s*<div class="legend">/)[1];
  assert.doesNotMatch(provenance, /hud-weather|hud-zoom|environment-status/);
});
```

- [ ] **Step 2: Run the HTML contract and verify RED**

```bash
node --test web/viewer/index-contract.test.mjs
```

Expected: FAIL because the environment controls and HUD ids do not exist.

- [ ] **Step 3: Add responsive styles and accessible markup**

Add a bottom-right card with this exact semantic structure:

```html
<section id="environment-controls" aria-label="环境控制" style="display:none">
  <div class="environment-row">
    <label for="weather-control">天气</label>
    <select id="weather-control">
      <option value="clear">晴</option>
      <option value="overcast">阴</option>
      <option value="rain">雨</option>
      <option value="snow">雪</option>
      <option value="fog">雾</option>
      <option value="night">夜</option>
    </select>
  </div>
  <div class="environment-row zoom-row">
    <label for="zoom-control">缩放</label>
    <input id="zoom-control" type="range" min="0.5" max="3" step="0.1" value="1">
    <output id="zoom-value" for="zoom-control" aria-live="polite">1.0×</output>
    <button id="zoom-reset" type="button">1×</button>
  </div>
  <p id="environment-status" aria-live="polite">Viewer 实时效果 · 不改变重建来源</p>
</section>
```

Add `天气: <b id="hud-weather">晴</b>` and `缩放: <b id="hud-zoom">1.0×</b>` directly before the existing provenance block. Style the card with the existing translucent panel tokens, `right: 12px; bottom: 12px; z-index: 10`, visible focus outlines, and a `max-width: 720px` media query that moves it above the bottom key help without covering the mini-map.

Use this CSS:

```css
#environment-controls {
  position: fixed; right: 12px; bottom: 12px; width: min(360px, calc(100vw - 24px));
  padding: 12px 14px; color: #e0e0e0; background: rgba(20, 22, 28, 0.88);
  border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;
  backdrop-filter: blur(6px); z-index: 10;
}
.environment-row { display: flex; align-items: center; gap: 10px; margin: 4px 0; }
.environment-row label { min-width: 36px; color: #b8c3cc; font-size: 12px; }
.environment-row select, .environment-row button {
  min-height: 32px; color: #fff; background: #27313b;
  border: 1px solid rgba(255,255,255,0.18); border-radius: 5px;
}
.environment-row select { flex: 1; padding: 4px 8px; }
.zoom-row input { flex: 1; min-width: 80px; accent-color: #7fd1ff; }
.zoom-row output { min-width: 38px; color: #7fd1ff; font-variant-numeric: tabular-nums; }
.zoom-row button { padding: 3px 9px; cursor: pointer; }
#environment-controls :focus-visible { outline: 2px solid #7fd1ff; outline-offset: 2px; }
#environment-status { margin-top: 7px; color: #8997a3; font-size: 11px; }
@media (max-width: 720px) {
  #environment-controls { bottom: 74px; }
  #controls { right: 12px; max-height: 54px; overflow: auto; }
}
```

- [ ] **Step 4: Run HTML and all Viewer tests and verify GREEN**

```bash
node --test web/viewer/index-contract.test.mjs
node --test web/viewer/*.test.mjs
```

Expected: PASS; vendor import-map closure remains green.

- [ ] **Step 5: Commit and push the visible controls**

```bash
git add web/viewer/index.html web/viewer/index-contract.test.mjs
git diff --cached --check
git commit -m "feat(viewer): add environment controls"
git push origin main
```

Commit must include the exact trailer.

### Task 4: Three.js Weather Runtime and Unified Zoom

**Files:**
- Modify: `web/viewer/index-contract.test.mjs`
- Modify: `web/viewer/main.js:10-83,337-414,424-568,685-759,860-1027`

**Interfaces:**
- Consumes: all exports from Task 1 and DOM ids from Task 3.
- Produces: `environment` in `getState`; working `setWeather({weather})`, `setZoom({zoom})`; camera-local precipitation and free-mode wheel zoom.

- [ ] **Step 1: Add failing source-wiring contracts**

Read `main.js` beside `index.html` in `index-contract.test.mjs` and add:

```js
const main = await readFile(new URL('./main.js', import.meta.url), 'utf8');

test('viewer runtime wires environment state without mutating provenance', () => {
  assert.match(main, /from ['"]\.\/environment\.mjs['"]/);
  assert.match(main, /effect_source:\s*['"]viewer-runtime['"]/);
  assert.match(main, /setWeather:\s*\(\{\s*weather\s*\}\)\s*=>/);
  assert.match(main, /setZoom:\s*\(\{\s*zoom\s*\}\)\s*=>/);
  assert.match(main, /camera\.zoom\s*=\s*environmentState\.zoom/);
  assert.match(main, /updatePrecipitation\(dt\)/);
  assert.doesNotMatch(main, /reconManifest\.environment\s*=/);
  assert.doesNotMatch(main, /manifest\.environment\s*=/);
});
```

- [ ] **Step 2: Run the contract and verify RED**

```bash
node --test web/viewer/index-contract.test.mjs
```

Expected: FAIL because `main.js` does not import or wire the environment module.

- [ ] **Step 3: Add state, light, reusable precipitation, and application functions**

Import the Task 1 exports. Add one state object:

```js
const environmentState = {
  weather: DEFAULT_WEATHER,
  zoom: DEFAULT_ZOOM,
  effect_source: 'viewer-runtime',
  precipitation_status: 'ready',
};
let hemisphereLight = null;
let precipitationPoints = null;
let precipitationEffect = null;
```

Create one `THREE.Points` with a `Float32Array(1200 * 3)`, a shader material whose fragment shader renders a narrow vertical streak when `uRain > 0.5` and a soft circle otherwise, and `geometry.setDrawRange(0, 0)`:

```js
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
  precipitationPoints.frustumCulled = false;
  precipitationPoints.visible = false;
  scene.add(precipitationPoints);
}
```

Add these complete state transitions:

```js
function syncEnvironmentUI() {
  const preset = getWeatherPreset(environmentState.weather);
  const zoomText = `${environmentState.zoom.toFixed(1)}×`;
  document.getElementById('weather-control').value = environmentState.weather;
  document.getElementById('zoom-control').value = String(environmentState.zoom);
  document.getElementById('zoom-value').textContent = zoomText;
  document.getElementById('hud-weather').textContent = preset.label;
  document.getElementById('hud-zoom').textContent = zoomText;
  document.getElementById('environment-status').textContent =
    environmentState.precipitation_status === 'degraded'
      ? '降水粒子已降级 · 背景/雾/光照仍生效'
      : 'Viewer 实时效果 · 不改变重建来源';
}

function applyZoom(value) {
  environmentState.zoom = normalizeZoom(value);
  camera.zoom = environmentState.zoom;
  camera.updateProjectionMatrix();
  syncEnvironmentUI();
  return environmentState.zoom;
}

function applyWeather(value) {
  environmentState.weather = normalizeWeather(value);
  const preset = getWeatherPreset(environmentState.weather);
  scene.background.setHex(preset.background);
  const fogNear = (currentFrame?.fogNear ?? 50) * preset.fog.nearScale;
  const fogFar = Math.max(fogNear + 1, (currentFrame?.fogFar ?? 500) * preset.fog.farScale);
  scene.fog = new THREE.Fog(preset.fog.color, fogNear, fogFar);
  hemisphereLight.color.setHex(preset.light.sky);
  hemisphereLight.groundColor.setHex(preset.light.ground);
  hemisphereLight.intensity = preset.light.intensity;
  configurePrecipitation(preset.precipitation);
  syncEnvironmentUI();
  return environmentState.weather;
}
```

Use these particle transition/update functions:

```js
function configurePrecipitation(effect) {
  precipitationEffect = null;
  environmentState.precipitation_status = 'ready';
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
    precipitationPoints.material.uniforms.uPointSize.value = effect.pointSize;
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
  for (let index = 0; index < precipitationEffect.count; index += 1) {
    const offset = index * 3;
    positions.array[offset + 1] -= precipitationEffect.fallSpeed * dt;
    if (positions.array[offset + 1] < 0) positions.array[offset + 1] += height;
    positions.array[offset] += (
      Math.sin((index + 1) * 0.37) * precipitationEffect.drift * dt
    );
    if (positions.array[offset] > halfWidth) positions.array[offset] -= width;
    if (positions.array[offset] < -halfWidth) positions.array[offset] += width;
  }
  positions.needsUpdate = true;
}
```

These functions allocate no geometry, material, or `THREE.Points` during animation. Particle configuration errors only degrade precipitation; the already-applied background, fog, and light remain active.

- [ ] **Step 4: Wire controls, free-mode wheel, framing, state, bridge, and animation**

Add `setupEnvironmentControls()` that:

```js
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
```

Call it from `init()`. Ignore movement/hotkey handling when `event.target` is an `INPUT`, `SELECT`, or `BUTTON`. Store the HemisphereLight in `hemisphereLight`, create the precipitation runtime once, then call `applyWeather(DEFAULT_WEATHER)` and `applyZoom(DEFAULT_ZOOM)` after DOM and camera exist.

At the end of `applyFraming()`, call `applyWeather(environmentState.weather)` so a bounds change recomputes fog distances without resetting zoom. Change `resetCamera` to call `applyZoom(DEFAULT_ZOOM)` after framing.

Add to `readState()`:

```js
environment: { ...environmentState },
```

Add handlers:

```js
setWeather: ({ weather }) => {
  applyWeather(weather);
  return readState();
},
setZoom: ({ zoom }) => {
  applyZoom(zoom);
  return readState();
},
```

Call `updatePrecipitation(dt)` once per animation frame before `renderer.render`. Reveal `environment-controls` with the other Viewer panels after initial loading.

- [ ] **Step 5: Run focused and complete local gates**

```bash
node --test web/viewer/index-contract.test.mjs web/viewer/environment.test.mjs web/viewer/bridge.test.mjs
node --test web/viewer/*.test.mjs
.venv/bin/python make.py test
git diff --check
```

Expected: every command PASS; no change to Python, Studio, coordinate, infinite-world, Spark or provenance tests.

- [ ] **Step 6: Commit and push the runtime**

```bash
git add web/viewer/main.js web/viewer/index-contract.test.mjs
git diff --cached --check
git commit -m "feat(viewer): render switchable weather and zoom"
git push origin main
```

Commit must include the exact trailer.

### Task 5: Browser Acceptance and Goal Audit

**Files:**
- No planned file changes.

**Interfaces:**
- Consumes: live Viewer at `http://127.0.0.1:8767/web/viewer/` or a freshly started `python -m pipeline.studio_server` port.
- Produces: current-run evidence for every explicit goal requirement and regression boundary.

- [ ] **Step 1: Verify the server and load a fresh Viewer tab**

Run `curl -fsS http://127.0.0.1:8767/web/viewer/ >/dev/null`; if it fails, start `.venv/bin/python -m pipeline.studio_server --host 127.0.0.1 --port 8767`. Open a fresh in-app Browser tab and bind it to the localhost app.

Expected: loading overlay clears, HUD/mini-map/environment controls appear, and console has no uncaught exception.

- [ ] **Step 2: Exercise all six weather states**

Select `晴→阴→雨→雪→雾→夜→晴` and capture state/screenshot evidence.

Expected: background, fog and illumination visibly change every time; rain has streak particles; snow has soft particles; particles stop outside rain/snow; chunk count does not jump merely from switching weather.

- [ ] **Step 3: Exercise both zoom modes and reset**

Use the slider at `0.5×`, `1.0×`, `3.0×`; press `F`, use free-mode wheel in both directions; press `F` back and use orbit wheel; press `1×` and invoke `resetCamera` through the bridge.

Expected: slider/bridge work in both modes; free wheel changes HUD optical zoom; orbit wheel still changes camera-target distance; reset restores `1.0×`.

- [ ] **Step 4: Exercise full goal regressions**

Drag through a full orbit, switch to free look, use `WASDQE`, invoke `G` with a far positive and negative ENU coordinate, and observe on-demand chunks/mini-map/LOD.

Expected: 360° view, arbitrary ENU movement, infinite on-demand loading, variable LOD and Spark/DC reconstruction layer remain operational under every environment state.

- [ ] **Step 5: Audit runtime truth and final repository state**

Use the bridge `getState` and inspect the HUD.

Expected:

- `environment` reports `{weather, zoom, effect_source:'viewer-runtime'}`;
- artifact provenance fields are byte-for-byte semantically unchanged by weather/zoom;
- no manifest file is rewritten;
- `git status --short --branch` is clean;
- `git rev-list --left-right --count HEAD...origin/main` prints `0 0`;
- GitHub Actions for the final runtime commit is observed green when the API is available.

Only after every item is proven may the persistent goal be marked complete.
