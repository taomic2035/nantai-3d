# 2026-07-17 · 无限世界闭环 + 天气/缩放 浏览器实测回归

> 执行：Opus（重建管线 / 集成 lane）。只读验证：驱动本机 `studio_server`，未写 `.nantai-studio/`、
> 未写仓库树（除本文档）、未触碰任何 trust root。
> 目的：`943398e`(HTTP 端点) + `93819ab`(viewer 消费) + `c737ebb`(天气/缩放) 落地后，
> 无人真实驱动过闭环。本文档是**实测**，非推断。

## 结论（TL;DR）

**「360° · 任意坐标漫游 · 无限地图 · 可缩放 · 天气自由切换」五项在真实浏览器中同时成立，
控制台零错误。** 天气与缩放切换**各造成 0 次 chunk 请求**——
[天气规格](../superpowers/specs/2026-07-17-viewer-weather-and-zoom-design.md) §10
「天气/缩放不触发 chunk 重建、reconstruction 重载或缓存增长」的运行时要求实测坐实。

## 实测环境

- 机器：macOS 15.5 / Apple M5 arm64（**注意：`AGENTS.md` 的 machine reality 描述的是 Windows
  i7-14700 / UHD 770，不适用于本机**。本机是本项目目前唯一的 macOS 验证面——CI 只有 ubuntu+windows）。
- Server：`.venv/bin/python -m pipeline.studio_server --host 127.0.0.1 --port 8030`。
  端口 8021 曾报 `OSError: [Errno 48]`，实为前次失败尝试的残留绑定，非真实占用；换端口即解。
- 浏览器：Playwright chromium-1223 headless + SwiftShader（软件 Vulkan）。
  实测 `WebGL 2.0 (OpenGL ES 3.0 Chromium)` 可用，Spark 2.1.0 报
  `renderer status: full_3dgs 已由 Spark 初始化`——**离线 vendored 依赖在真实渲染路径上跑通**。
  Playwright 装在会话 scratchpad 的独立 venv，**未污染项目 `.venv`**（多 agent 共享）。

## HTTP 层实测

| 验证项 | 结果 |
|---|---|
| 运行时闸门 | 磁盘 `on_demand: false`，HTTP 响应 `true`——静态/离线部署不被污染 |
| 越界坐标 `chunk/7/7.ply?lod=1` | 200，56,563 字节，2,967 高斯（烘焙范围仅 5×5） |
| 负坐标 `chunk/-3/-5.ply` | 200，178,714 字节，9,396 高斯 |
| ETag | `sha256:<hex>`，与实体字节 SHA-256 吻合 |
| 跨进程确定性 | 两个独立进程 `render_single_chunk(7,7,world_seed=42,lod=1)` 与 HTTP 响应**三者逐字节一致** |
| LOD 语义 | chunk(0,0)：lod0=834 / lod1=3,130 / lod2=None=10,436 高斯。**数字越大越精细**；`lod=None` 与 `lod=2` 逐字节相同 |
| 条件请求 | 正确 ETag → 304 无体；错误 ETag → 200 带体；`If-None-Match: *` → 304。三者皆正确 |
| 并发 | 12 线程并发不同 chunk，0.06s 全成功，每个响应与串行基线逐字节一致；无共享可变状态 |

## 浏览器层实测（决定性）

HUD 实读：

- **当前 chunk (7,-2)**，相机 ENU **(1428, -323, 1041)** → 越过烘焙范围且负坐标，任意坐标漫游成立。
- **活跃 chunks 36 / 缓存上限 36，已淘汰 14，缓存命中 200** → LRU 淘汰真实工作，
  **无限地图无无界内存增长**。
- **天气六种全切通**（clear/overcast/rain/snow/fog/night），场景可见差异，`chunk_reqs_caused_by_weather: 0`。
- **缩放滑杆 1× → 2.5×**，`chunk_reqs_caused: 0`。
- **视角模式：环绕 (F 切换)** → 360° 环绕 + 自由视角双模式。
- **控制台错误 0 条**。

## Provenance 诚实性（fail-closed 复核）

面板实读 `requested/actual: unknown/mock`、`synthetic/geometry: unknown/unknown`、
`frame/units/handedness: unknown/unknown/unknown`、`artifact fidelity: unknown`。
**全部 unknown，无一被提升为 measured/metric/aligned**——符合项目法：未知 → 可预览但永不静默提升。
天气控件旁标注「viewer 实时效果 · 不改变重建来源」，规格 §8 的诚实约束已落到 UI。

World chunk 的 provenance **按设计在 manifest 层**（`grid.world_seed`），不在 PLY 字节内：
`studio_server.py` 模块 docstring 明确声明该端点
"derives deterministic synthetic PLY bytes in memory and **does not write a project artifact or
trust root**"。`nantai_meta` 是 `gaussian_scene.py` 的重建 artifact save/load 概念，
**不适用于 world chunk**。两侧闸门一致 fail-closed：混种子时生成器写 `world_seed: null`
（`render_chunk_to_ply.py:661`），服务端 `type(world_seed) is not int` 拒绝（409），
viewer 的 `Number.isSafeInteger(null)` 亦为 false → 不发请求。无一侧自作主张。

> 实测细节：`type(True) is int` 为 False，故 JSON `true` 当种子会被正确拒绝——
> 这条容易踩的 Python 陷阱（`isinstance(True, int)` 为 True）此处**没有**踩。

## 对抗性审查结果

5 个独立视角（对抗输入/确定性/provenance/viewer 集成/服务端健壮性）攻击端点，
每条 finding 由 2 个独立验证者对抗性复核。**幸存缺陷仅 2 条，均 low，均不阻塞目标**：

1. `studio_server.py` 无 `do_OPTIONS` override → OPTIONS 返回 BaseHTTPRequestHandler 的
   原生 HTML 501（其余方法均返回结构化 JSON 405），且缺 `_security_headers()` 的安全头。
   同源部署下浏览器不发 preflight，**当前无真实影响**；属 API 一致性瑕疵。
2. chunk handler 只捕获 `ValueError/ArithmeticError/OSError/RuntimeError`；其余异常
   （如 `TypeError`/`AttributeError`）逃逸至 `ThreadingMixIn` → 裸连接关闭而非结构化 500，
   traceback 仅入 stderr（**无 HTTP 体泄漏**）。已实测 `render_single_chunk` 有类型守卫
   （非整数输入 → `ValueError`），且 chunk 体积恒定 162–198 KB 无 MemoryError 风险，
   故现有路径触发不到；属潜在缺陷的可诊断性缺口。

两条均在 `studio_server.py`（Codex lane），本 lane 不越界修改，转交。

## 边界声明（诚实标注）

- 浏览器验证运行于**工作树状态**（静态 server 直读磁盘），当时 Codex 有未提交 WIP
  （`web/viewer/main.js`、`web/viewer/environment.mjs`、`web/studio/*`）。
  结论对当时工作树成立，**不等同于 `c737ebb` 提交态**。
- SwiftShader 是软件光栅化，证明的是**功能正确性与逻辑无误**，**不构成真实 GPU 的性能证据**。
- 本地 `web/data/manifest.json`（gitignored 构建产物）为 7-16 09:12 旧代码所生，缺
  `bounds`/`baked_extent`/per-chunk `aabb`。当前生成器已会输出（`render_chunk_to_ply.py:602-655`），
  且 `framing.mjs` 对缺失有降级链。该字段**只服务取景/mini-map，不参与 chunk 调度**，
  故不影响本次漫游结论。
- 一处方法论教训：本轮 provenance 视角的 prompt 由我写入了「PLY header 带 `nantai_meta` 块」
  这一**假前提**，诱导出一条 phantom critical。双独立验证者据实驳回。
  结论：喂给验证 agent 的「已知事实」必须先自证，否则会制造幻影缺陷。

## 复现

```bash
.venv/bin/python -m pipeline.studio_server --host 127.0.0.1 --port 8030 &
node --test web/viewer/*.test.mjs          # 65/65（含 world-chunks 负坐标用例）
# 浏览器层需 playwright + chromium；脚本见会话 scratchpad，未入库（驱动的是本机临时 server）
```
