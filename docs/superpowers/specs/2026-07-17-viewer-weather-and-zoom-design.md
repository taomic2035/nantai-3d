# Viewer 天气切换与统一缩放设计

日期：2026-07-17
状态：已批准，待书面规格复核
范围：`web/viewer/` 的运行时环境表现、缩放交互与 Studio bridge 契约

## 1. 背景与目标

Nantai Viewer 已支持：

- 360° 环绕观察与自由视角；
- ENU 任意坐标传送和六自由度移动；
- 预烘焙优先、越界按需加载的无限地图；
- OrbitControls 的环绕视角滚轮推拉。

本次补齐两个用户可见缺口：

1. 晴、阴、雨、雪、雾、夜六种天气可随时切换；
2. 环绕与自由视角都有明确、可编程、可复位的光学缩放。

天气仅是 Viewer 的实时渲染效果。它不得改写重建 artifact、世界坐标、素材内容、可信度或
provenance，也不得伪装成拍摄时的真实天气。

## 2. 已选方案与取舍

采用“实时天气状态机 + 相机局部粒子 + 独立光学缩放”方案。

没有选择仅换背景色，因为它无法让雨雪产生足够明确的视觉反馈；也没有选择为每种天气重建一套
3DGS，因为那会把渲染偏好错误地混入 artifact provenance，且训练和存储成本不符合本阶段目标。

实时方案保留同一世界、同一坐标和同一重建结果，只改变背景、雾、环境光及有界粒子。它能即时
切换、适配无限地图，并且能诚实地标记为 runtime effect。

## 3. 范围

### 3.1 本次实现

- 六个稳定 weather id：`clear`、`overcast`、`rain`、`snow`、`fog`、`night`；
- Viewer 内可见的天气选择器、缩放滑杆、缩放复位按钮；
- 自由视角滚轮缩放；环绕视角继续保留既有滚轮推拉；
- 两种视角都响应同一个光学缩放值；
- HUD 显示当前天气和光学缩放倍率；
- bridge 新增 `setWeather`、`setZoom`，`getState` 回报当前值；
- 天气切换即时生效，不重载 chunk 或 reconstruction；
- 纯状态测试、bridge 测试、浏览器交互和视觉验收。

### 3.2 非目标

- 不模拟积雪堆积、地面积水、反射、雷电、风场或昼夜时间轴；
- 不修改 Gaussian 数据、点云颜色、素材文件或 world/recon manifest；
- 不声明天气是 measured、captured 或 reconstructed；
- 不为 Studio 新增完整天气编辑工作流；Studio 可先通过 bridge 使用能力；
- 不改变无限地图缓存、LOD 或坐标调度规则。

## 4. 架构与组件边界

### 4.1 `environment.mjs`：纯状态与契约

新增无 DOM、无 Three.js 依赖的模块，负责：

- weather id 白名单与默认值；
- 六套不可变天气 preset；
- `normalizeWeather(value)`；
- `normalizeZoom(value)`；
- 缩放上下限和步进常量；
- 天气切换所需的粒子类型、数量上限和视觉参数。

所有 UI、键鼠和 bridge 输入必须先经过该模块归一化，避免出现多套边界规则。未知 weather id
抛出明确错误；缩放必须是有限数字，并限制在 `0.5x` 至 `3.0x`。

### 4.2 `main.js`：Three.js 运行时适配

`main.js` 保留场景生命周期，新增一个小型 environment runtime：

- 保存当前 `weather` 和 `zoom`；
- 将 preset 应用于 `scene.background`、`scene.fog` 和 HemisphereLight；
- 复用单个 precipitation `THREE.Points` 对象；
- 在动画帧中只更新活动粒子；
- 把粒子体积锚定在相机 ENU 位置附近；
- 将 `zoom` 映射到 `PerspectiveCamera.zoom` 并调用
  `camera.updateProjectionMatrix()`。

天气和缩放更新不得调用 `updateChunks()`、`updateRecon()` 或重新创建 renderer。相机移动仍是
chunk 调度的唯一空间输入。

### 4.3 `index.html`：直接可发现的控制

新增紧凑的环境控制卡：

- 天气下拉选择器，使用中文显示名但提交稳定英文 id；
- 缩放滑杆，显示实时倍率；
- “1×”复位按钮；
- 原生 label、键盘焦点和 `aria-live` 状态文本。

控件应避开 HUD、mini-map 和底部键位说明，在窄屏上允许换行或缩小宽度。画布上的键盘移动在
表单控件获得焦点时不得误触发。

## 5. 天气模型

六个 preset 只包含渲染参数，不包含 provenance 字段：

| id | 背景/光照 | 雾 | 粒子 |
|---|---|---|---|
| `clear` | 明亮冷蓝天空，正常环境光 | 很淡或无 | 无 |
| `overcast` | 中性灰蓝，降低环境光 | 轻雾 | 无 |
| `rain` | 暗灰蓝，降低环境光 | 中等距离雾 | 最多 1200 个雨滴 |
| `snow` | 浅灰蓝，柔和环境光 | 中等距离雾 | 最多 800 个雪点 |
| `fog` | 低对比灰，显著降低可视距离 | 浓雾 | 无 |
| `night` | 深蓝黑背景，低强度冷色光 | 远距离夜雾 | 无 |

粒子使用确定性初始化，避免每次切换产生不可复现的随机布局。雨滴快速下落、雪点缓慢下落并有
小幅横向漂移；粒子越过局部体积底部后回收到顶部。局部体积随相机平移，但不改变 ENU 相机状态
或世界对象。关闭雨雪时隐藏并停止更新粒子。

设备像素比仍沿用既有上限。天气系统不得提高 renderer pixel ratio。粒子数量是硬上限，避免在
无限地图长距离漫游时随加载次数增长。

## 6. 缩放语义

本设计区分两种行为：

- **光学缩放**：统一状态 `zoom`，映射 `PerspectiveCamera.zoom`，范围 `0.5x–3.0x`；
- **环绕推拉**：OrbitControls 既有的相机到 target 距离变化，继续由环绕模式滚轮控制。

因此：

- 环绕和自由模式都能通过滑杆或 bridge 设置同一个光学倍率；
- 自由模式下，画布滚轮按固定步进改变光学倍率；
- 环绕模式下，画布滚轮保持既有推拉手感，不被新逻辑拦截；
- `resetCamera` 同时恢复 framing 和 `1.0x` 光学缩放；
- 切换环绕/自由模式时保留光学倍率；
- 坐标传送、LOD 和天气切换不改变光学倍率。

这让“缩放”在两种视角中都有稳定 API，同时不破坏用户已经熟悉的环绕推拉。

## 7. 状态流与 bridge 契约

Viewer 单一运行时状态增加：

```json
{
  "environment": {
    "weather": "clear",
    "zoom": 1.0,
    "effect_source": "viewer-runtime"
  }
}
```

`effect_source` 是展示性声明，不进入 artifact provenance。

bridge capability 的 `commands` 增加：

- `setWeather({ weather })`：验证 id、应用 preset、返回完整 Viewer state；
- `setZoom({ zoom })`：验证有限数字、限制边界、应用 projection、返回完整 Viewer state。

两条命令沿用现有同源、schema version、request id、错误响应和 `stateChanged` 回包。无效 weather
返回 `command-failed`；非数字缩放返回 `command-failed`；超出有效范围的有限缩放值被限制到最近
边界。`getState`、HUD 和控件都读取同一个 runtime state，避免状态分叉。

## 8. 错误处理与真实性约束

- 未知 weather id 不静默回退，避免调用方误以为切换成功；
- `NaN`、无限值和非数字缩放不写入 camera；
- WebGL 不支持或粒子创建失败时，背景、雾和光照仍可切换，HUD 显示粒子效果降级；
- 天气切换失败不影响当前世界、重建层和相机；
- artifact provenance、geometry usability、artifact fidelity 与 viewer fidelity 计算保持原样；
- runtime weather 不能被提升为真实采集环境证据。

## 9. 测试策略

按 TDD 顺序实现：

1. `environment.test.mjs`
   - 六种合法 weather；
   - 未知 weather 拒绝；
   - 缩放默认值、有限数字、上下界和非数字拒绝；
   - preset 不可变且粒子数量不超过硬上限。
2. `bridge.test.mjs`
   - capabilities 宣告新命令；
   - 两个命令正确路由并返回 `stateChanged`；
   - 缺失 handler 仍按既有契约返回 unsupported；
   - provenance 归一化结果不随 environment state 改变。
3. Viewer 静态/DOM 测试
   - 天气选择器、缩放滑杆、label、复位按钮存在；
   - 本地模块引用可加载。
4. 浏览器验收
   - 六种天气可连续切换且无控制台错误；
   - 雨雪粒子随相机移动、数量不累积；
   - 自由模式滚轮和滑杆缩放有效；
   - 环绕滚轮仍推拉，滑杆仍能光学缩放；
   - `G` 传送后无限 chunk 继续按需加载；
   - HUD、mini-map、provenance 和 3DGS 图层仍正常。

## 10. 完成标准

满足以下全部条件才算完成：

- 用户能在 Viewer 内自由切换六种天气，并立即看见差异；
- 用户能在环绕和自由模式设置、观察和复位 `0.5x–3.0x` 光学缩放；
- Studio bridge 可读取和设置天气/缩放；
- 天气/缩放不触发 chunk 重建、reconstruction 重载或缓存增长；
- 无限地图、任意 ENU 坐标传送、360° 视角和 LOD 原能力回归通过；
- provenance 和 fidelity 仍严格 fail-closed；
- Node 测试、Python 相关回归、浏览器交互与视觉验收全部通过。
