# Nantai 3D Studio UX 设计

日期：2026-07-14
状态：批准基线（用户授权 Codex 暂时接管全项目推进）
实现边界：`web/studio/`、`web/viewer/` 的消息桥与真实性展示、`pipeline/studio_server.py`；
重建算法仍通过版本化 artifact contract 接入，Studio 不直接导入算法内部对象。

## 1. 背景与目标

当前项目已有命令行 ingest、registration、reconstruct、LOD、asset registry 和 Three.js
viewer 原型，但用户必须理解目录、命令、manifest 与多个真假层级，才能判断一次重建是否可用。
现有 viewer 还是调试面板，不是完整创作工作台。

Studio 的目标是让用户完成一条可解释、可恢复的流程：

1. 混合选择图片与视频，并看见抽帧、模糊和重复检查结果。
2. 检查相机是否进入同一坐标契约；任意尺度 SfM 不得伪装成 ENU 米制。
3. 启动或观察重建、拼接、LOD 与补拍替换任务。
4. 在 3D 舞台中检查产物，同时看见 provenance、geometry usability 和 render fidelity。
5. 验收、注册和替换素材；明确哪些素材已经被世界实际消费。
6. 保留本机任务历史，失败后可读日志、可重试、可恢复上下文。

本设计不在浏览器实现 COLMAP 或 3DGS 训练器。真实 splat renderer、资产实例化与坐标
契约由核心管线和 viewer 分阶段补齐；Studio 只通过稳定 adapter contract 消费其状态，
并由本地服务桥接任务，而不是让浏览器直接运行 shell。

## 2. 决策与假设

用户授权 Codex 独立推进，因此采用以下默认决策：

- 采用三栏工作台 A，而不是纯向导或多页面 dashboard。
- V1 同时包含素材验收/注册和持久任务历史。
- 本地优先、单机运行；一次只激活一个项目，但保留多个历史 run。
- 中文为主，保留 ENU、SfM、Sim3、LOD、3DGS 等技术缩写。
- 桌面端优先，设计宽度 1280–1600px；低于 1100px 时右侧 inspector 变为抽屉。
- `web/viewer/` 通过 iframe 嵌入，并补充最小 `postMessage` bridge；Studio 不读取 viewer
  内部变量，也不复制渲染状态。
- Opus adapter 尚未提供时，Studio 使用可替换的 mock adapter 演示完整状态机；UI 必须
  显示“模拟数据”，不得把模拟任务伪装成真实执行。

## 3. 方案比较

### A. 单页三栏工作台（采用）

左管线、中央舞台、右 inspector、底部 job drawer。用户始终看见当前产物与下一步，适合
重建这种需要反复检查、补拍、替换和回看的非线性流程。代价是首屏信息密度较高，需要
清晰的层级和 progressive disclosure。

### B. 线性向导

首次使用最轻松，但重建失败、补拍变清晰、资产替换和历史回看都会迫使用户跳出线性流。
适合作为工作台内的 first-run overlay，不作为主架构。

### C. Dashboard + 多详情页

适合多项目运营，但会拆散 3D 舞台、任务日志与检查上下文。当前项目仍是本地单项目工具，
路由和导航成本大于收益。

## 4. 信息架构

```text
Nantai 3D Studio
├── Top bar
│   ├── Project identity / storage status
│   ├── active run / dirty artifact indicator
│   └── adapter mode: Mock / Local Pipeline / Disconnected
├── Pipeline navigator
│   ├── 1 输入 Sources
│   ├── 2 配准 Align
│   ├── 3 重建 Reconstruct
│   ├── 4 拼接与清晰度 Stitch & LOD
│   ├── 5 素材 Assets
│   └── 6 发布 Review & Export
├── Stage
│   ├── embedded viewer
│   ├── layer / LOD / comparison toolbar
│   ├── provenance and fidelity badges
│   └── empty / loading / stale / error overlays
├── Inspector
│   ├── step-specific controls
│   ├── quality evidence
│   └── recommended next action
└── Job drawer
    ├── queued / running / succeeded / failed / canceled
    ├── structured events and logs
    └── retry / reveal artifact / compare run
```

## 5. 布局与视觉语言

Studio 延续现有 viewer，而不是做成另一个产品：

| Token | 值 | 用途 |
|---|---|---|
| Canvas | `#1a2228` / `#1a1a1a` | 舞台与应用背景 |
| Panel | `rgba(20,22,28,.92)` | 导航、inspector、drawer |
| Border | `rgba(255,255,255,.10)` | 面板与分隔 |
| Text | `#e0e0e0` | 主文本 |
| Muted | `#aaa` | 次级文本 |
| Cyan | `#7fd1ff` | 选中、链接、坐标信息 |
| Green | `#7fff7f` | 已验证、成功 |
| Amber | `#ffcc55` | proxy、stale、需确认 |
| Red | `#ff6b6b` | 失败、不可测量、阻断 |
| Radius | `8px` | 面板、按钮、徽标 |
| Font | system UI | 与 viewer 一致 |

默认网格：左侧 232px，中央 `minmax(520px, 1fr)`，右侧 320px；job drawer 收起 44px，
展开 240px。主舞台优先获得空间。所有区域使用 8px 基准间距。

## 6. 核心组件

### 6.0 所有权矩阵

| 能力 | 浏览器 Studio | 本地 adapter 服务 | 核心/Viewer |
|---|---|---|---|
| 状态归一化、gate、展示 | 负责 | 提供原始证据 | 不负责 UI 推断 |
| mock 场景与 UI 偏好 | `localStorage` 唯一真相源 | 不参与 | 不参与 |
| 真实 run/event ledger | 只读缓存，可重连 | 唯一真相源 | 产生日志与 artifact |
| 启动/取消任务 | 发结构化请求 | 白名单编排、持久化 | 执行 Python 能力 |
| viewer 图层/LOD/相机 | 发 bridge 命令 | 提供 artifact URL | 执行并回报 capability/state |
| 素材替换 | 发 validate/commit | 乐观锁与原子提交 | registry 与 consumption report |
| 发布/导出 | 发冻结请求 | 创建不可变 artifact snapshot | 提供产物和验证证据 |

无本地 adapter 时，Studio 明确称为“可运行 UX 原型”：真实写操作禁用，但 mock adapter
仍走同一接口和状态机，避免接入时重写组件。

### 6.1 Top bar

- 项目名、保存位置与最近更新时间。
- 当前 run 状态与产物 freshness。
- adapter badge：`模拟数据`、`本地管线` 或 `未连接`。
- 全局主操作只允许一个：根据当前状态显示“开始检查”“继续重建”或“查看失败”。

### 6.2 Pipeline navigator

每一步同时显示五层状态，不能只用一个绿色勾：

1. availability：`missing / ready`
2. execution：`idle / queued / running / succeeded / failed / canceled`
3. freshness：`current / stale`
4. preview：`unloaded / loading / ready / degraded`
5. trust：`verified / proxy / untrusted`

步骤可非线性访问，但只有满足前置 contract 才显示主操作。用户仍可打开失败步骤读取证据。

### 6.3 Stage

- 默认嵌入现有 viewer；无产物时展示输入覆盖引导，而不是空画布。
- 顶部 toolbar：世界/重建层、LOD、当前/上一 run、边界、相机复位。
- 左下角永久显示 provenance chips：actual engine、frame、scale、synthetic、fidelity。
- 当缺少机器字段时显示 `未知 — 不可用于测量`，不推测为真实。
- Point preview 必须显示 `DC point preview`；只有 adapter 明确报告完整 renderer 才显示
  `Gaussian splat`。最终文字由 Studio 对 renderer capability 和 artifact 属性交叉验证，
  不能由 adapter 提供一段自由字符串直接决定。
- iframe 未完成握手或能力不足时，对应 toolbar 按钮必须 disabled 并显示原因，不能发送
  无效命令。

### 6.4 Inspector

内容随步骤切换：

- Sources：图片/视频计数、视频时长、目标抽帧率、模糊和重复策略。
- Align：registered/total、frame、scale、GPS origin、session 分布与 QC 阻断。
- Reconstruct：requested/actual engine、训练产物、Gaussian count、fidelity。
- Stitch & LOD：session transform、overlap、dedup、区域替换与三级 LOD。
- Assets：11 项 registry 状态、origin、version、renderer consumption、替换入口。
- Review：发布前 checklist、不可测量警告、输出路径。

Inspector 的首屏顺序固定为：结论 → 证据 → 参数 → 危险操作。

### 6.5 Job drawer

- 使用时间线展示结构化 event，而不是整块 stdout。
- 每个 job 保存输入摘要、参数快照、adapter、开始/结束时间、状态、artifact URI 和日志。
- 浏览器原型使用 `localStorage` 持久化 mock run 摘要与 UI 偏好，单项目最多保留 50 个 run
  和每个 run 200 条结构化事件；不保存二进制、完整 stdout 或文件句柄。
- 真实模式以 adapter ledger 为唯一真相源。前端只保存最近 cursor 和显示偏好，重连后用
  event id 去重，不把缓存回写为服务器事实。
- 重试创建新 run，并保留 `retry_of`，不覆盖失败记录。

### 6.6 Asset workspace

- 网格卡片展示 asset id、kind、version、origin、验收和实际消费状态。
- “格式 PASS”和“已在世界消费”是两个独立徽标。
- 替换流程使用两阶段协议：`validateAssetCandidate` 返回一次性 commit token，随后
  `commitAssetVersion(expectedVersion, token)` 原子写入新 version；验证失败、token 过期
  或版本冲突都不得改变 active registry。
- 当前确定性 world renderer 已消费 building、prop 与 vegetation，并逐项输出 hash/version/
  instance count 证据。缺少或不匹配该 consumption report 时，Studio 必须显示 `未消费`，
  不能仅凭 registry 中存在就标完成；`assets-partial` 专门保留这一失败场景。

## 7. 首次使用流程

1. 欢迎层说明这是本地工具，并显示 adapter 状态。
2. 同一个文件选择器接受图片和视频；输入摘要按类型分组。
3. 运行 ingest 后展示保存帧、跳过模糊、失败媒体和磁盘位置。
4. 配准完成后必须先过 QC：frame、scale、registered ratio、session overlap。
5. 未 geo-align 的 COLMAP 结果标 `sfm-local / arbitrary-scale`，允许预览但阻断“米制发布”。
6. 重建完成进入 Stage；用户可比较 LOD 和上一 run。
7. Assets 可在重建前后进入；注册成功只更新 registry，不自动声称世界已消费。
8. Review 页面汇总可用性，用户明确选择导出 proxy 或可测量产物。

向导只在首次使用出现；完成后进入常驻工作台。

## 8. 数据与适配器契约

Studio 不读取 Python 内部对象，只消费版本化 JSON。固定接口：

```js
StudioAdapter = {
  loadProject(): Promise<ProjectSnapshot>,
  listRuns(cursor): Promise<{items: RunRecord[], cursor: string}>,
  startJob(command, payload): Promise<RunRecord>,
  cancelJob(runId): Promise<void>,
  subscribe(cursor, listener): Unsubscribe,
  validateAssetCandidate(candidate): Promise<ValidationReport>,
  commitAssetVersion(assetId, expectedVersion, commitToken): Promise<AssetRegistrySnapshot>,
  getConsumptionReport(registryRevision): Promise<ConsumptionReport>,
  freezeExport(runId, format): Promise<ArtifactRef>,
  getPreviewUrl(artifactId): Promise<string>
}
```

公共 envelope、枚举和错误模型：

```json
{
  "schema_version": 2,
  "generated_at": "ISO-8601",
  "request_id": "req-uuid",
  "error": null
}
```

- `error.code` 是稳定枚举：`invalid_input / precondition_failed / conflict /
  adapter_disconnected / execution_failed / not_supported`；`message` 只用于人读。
- artifact 统一为 `{id, kind, uri, sha256, bytes, created_at, immutable}`。
- frame 是 `world-enu / sfm-local / unknown`；units 是 `meters / arbitrary / unknown`；
  handedness 是 `right / left / unknown`；禁止把这些字段塞进自由字符串。
- transform step 是 `{id, source_frame, target_frame, kind, matrix4x4, applied_at,
  evidence_artifact_id}`；同一个 id 在一条 chain 中只能出现一次。

`ProjectSnapshot` 必须包含：

```json
{
  "schema_version": 2,
  "project": {"id": "nantai-demo", "name": "南台村", "updated_at": "ISO-8601"},
  "adapter": {"kind": "mock", "connected": true},
  "sources": {"images": 48, "videos": 3, "frames": 132, "rejected": 7},
  "coordinate": {
    "source_frame": "sfm-local",
    "world_frame": "world-enu",
    "units": "arbitrary",
    "handedness": "right",
    "up_axis": "z",
    "transform_chain": [],
    "metric_evidence": [],
    "registered_images": 173,
    "total_images": 180
  },
  "reconstruction": {
    "requested_engine": "import",
    "actual_engine": "mock",
    "synthetic": true,
    "artifact": {
      "id": "artifact-recon-001",
      "kind": "3dgs-ply",
      "uri": "recon/scene_full.ply",
      "sha256": "hex",
      "bytes": 1234,
      "created_at": "ISO-8601",
      "immutable": true
    },
    "attributes": ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"],
    "sh_degree": 0,
    "renderer_capabilities": ["dc-color", "anisotropic-covariance", "alpha-composite"],
    "gaussian_count": 7700,
    "lod": [0, 1, 2]
  },
  "assets": {"registered": 11, "consumed": 11, "blocked": 0},
  "active_run_id": "run-20260714-001"
}
```

`RunRecord` 至少包含 `{id, command, status, retry_of, input_summary, parameters,
adapter_kind, started_at, finished_at, artifact_ids, last_event_id}`；`RunEvent` 包含
`{id, run_id, seq, at, level, phase, progress, message, data}`，其中 `(run_id, id)` 幂等。
取消与完成竞态以 adapter 最终状态为准；`canceled` 只有 adapter 确认后成立。

`ValidationReport` 包含 candidate hash、规则列表、通过状态、基于 active revision 生成的
一次性 token 与过期时间。`AssetRegistrySnapshot` 包含 revision、逐 asset version/origin/hash；
`ConsumptionReport` 逐 asset 给出 renderer、chunk/artifact、实例数与证据 artifact。

来源文件细节、session、Sim3、overlap、LOD 和逐素材数据保存在相应 evidence/artifact 列表，
Inspector 通过 artifact id 关联，不依赖聚合计数猜测详情。

未知字段必须按“不可信”处理；`geometry_usability`、`trust` 与显示的 render fidelity 均由
Studio 的纯函数根据上述证据派生，前端不得从 engine 名字或 adapter 文案推断米制、真实性
或完整渲染。

### 8.1 Viewer bridge

Studio 和 iframe 只交换同源 `postMessage`，每条消息包含 `schema_version`、`request_id`：

- Studio → Viewer：`loadArtifact`、`setLOD`、`setLayer`、`resetCamera`、`setBounds`、
  `getState`。
- Viewer → Studio：`ready(capabilities)`、`stateChanged`、`artifactLoaded`、`error`。
- `ready` 之前命令排队但按钮保持 disabled；超时进入 degraded，不假装成功。
- `capabilities` 至少声明 renderer、支持的 artifact kind、LOD、layers、camera reset 与
  3DGS 属性；Studio 再与 artifact attributes 交叉验证。

## 9. 原型状态机

独立 UX 原型提供下列可切换场景：

- `ready-proxy`：混合输入完成，mock reconstruction 可预览。
- `align-warning`：SfM local、任意尺度，阻断米制发布。
- `running`：reconstruct job 正在运行，展示进度与事件。
- `failed`：视频 0 帧或 adapter 断开，展示恢复操作。
- `assets-partial`：11 项注册、8 项消费、3 个 vegetation 阻断。
- `contract-complete-simulated`：字段齐全的未来目标 fixture，但 trust 仍为 proxy。

场景切换仅用于设计与测试，并永久显示 `模拟状态`。fixture 先进入规范化 reducer；合法
不变量包括：`missing` 不能有 succeeded execution，`failed` 不能是 current，unloaded preview
不能是 verified，mock/synthetic 永远不能导出 measurable trust。非法组合归一化为
`untrusted` 并生成可见诊断。

## 10. 错误与恢复

- 文件级错误留在 Sources 列表，不用全局 toast 代替。
- Job 失败时 drawer 自动展开，Inspector 显示根因、影响范围和可执行恢复动作。
- adapter 断开时保留最后 snapshot，但所有写操作 disabled，并显示数据时间。
- artifact stale 时仍可打开旧预览，但不得与 active parameters 混淆。
- 未知 frame/scale/fidelity 时 fail closed：可预览，不可显示“可测量/真实/完整”。
- 注册资产必须两阶段：validate → register；validate 失败不改变 registry。
- 发布前由 adapter 冻结不可变 artifact snapshot，并运行 coordinate、fidelity、hash 与
  freshness gate。V1 格式为 `proxy-ply` 和 `3dgs-ply`；不满足米制证据时只能导出 proxy。

## 11. 可访问性与交互

- 所有状态不仅靠颜色，还使用图标和文字。
- 键盘顺序：top bar → pipeline → stage toolbar → inspector → drawer。
- `aria-live=polite` 宣布 job 状态变化；长日志不逐行播报。
- 所有按钮有动词和对象，如“验证 11 个素材”，不用“确定”。
- 高对比文本达到 WCAG AA；动画遵循 `prefers-reduced-motion`。

## 12. 测试策略

### 单元测试

- 状态归一化：未知 provenance 必须成为 `untrusted`。
- pipeline gate：缺 frame/scale 时阻断米制发布。
- run ledger：刷新后恢复、重试保留父 run、失败不覆盖成功记录。
- asset badge：validation 与 renderer consumption 分离。
- reducer：非法五层状态被 fail-closed 归一化。
- provenance：frame/units/handedness/transform chain/renderer capability 交叉派生。
- viewer bridge：握手、超时、能力不足、请求响应关联。

### 组件与 DOM 测试

- 六个 pipeline step、Stage、Inspector、drawer 均有稳定 `data-testid`。
- 场景切换后唯一主操作与状态文字正确。
- adapter disconnect、stale artifact、failed job 的恢复动作可见且唯一。

### 浏览器验收

- 1024×768、1099×800、1280×720 和 1440×900 无横向溢出；窄屏 inspector 抽屉有
  focus trap、Escape 关闭和焦点归还。
- 选择 pipeline step、展开 drawer、切换模拟场景、刷新 ledger。
- 截图映射：正常 proxy、坐标阻断、素材 partial 三张。
- console 无 error；不依赖真实 GPU 或外部 API。
- 面板各自纵向滚动，drawer 展开后舞台可见高度不少于 320px；长路径和中英文混排不撑破。
- 有 skip link、可见 focus token、iframe title；running → failed 时焦点移动到错误摘要。

## 13. 实现切片

1. `web/studio/` 静态 shell、视觉 token、响应式布局。
2. 纯 JS state model、mock adapter 与 localStorage run ledger。
3. Pipeline/Stage/Inspector/Job drawer 组件与状态切换。
4. Asset workspace 与 HANDOFF-001 模拟数据。
5. viewer bridge 与本地 adapter 的最小只读集成。
6. 浏览器测试、截图与 Opus adapter contract HANDOFF。

每个切片扩展上一层，不为接真实核心而重写 UI。

## 14. 非目标

- 不在浏览器直接启动 shell 命令。
- 不把 mock job 结果写入正式 `recon/` 或 `assets/`。
- 不承诺移动端编辑体验。
- 不在 UX 切片内实现 COLMAP/3DGS 训练算法；核心 P0 在独立实施切片修复并通过 artifact
  contract 暴露证据。

## 15. 设计自审

- 无未定义主流程；尚未接入的核心能力有显式 disabled/degraded 状态。
- 架构、状态模型和测试围绕版本化 adapter、viewer bridge 与 `web/studio/` 边界。
- mock 与真实核心共享 adapter、reducer、viewer bridge 和两阶段素材协议。
- 完整 V1 包含资产验收/注册和持久任务历史，符合用户授权的独立推进范围。
- Pencil MCP 当前不可用；视觉验证采用可运行 HTML 原型和浏览器截图，不伪造 `.pen` 文件。
