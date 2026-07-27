# Production V1 状态与 TODO

更新：2026-07-27（状态以当前 `main` 与机器证据为准）

## 一句话状态

仓库已经能 fail-closed 地接收真实 SfM、远程训练证据、米制对齐和 Viewer 验收；
但目前还没有同时满足权利、真实 CUDA 3DGS、实测控制点与真实 Viewer QA 的同一
scene identity，因此仍是 Preview，不是 Production V1。

## 已完成的必要基础

| 能力 | 状态 |
|---|---|
| 真实数据 source/rights/receipt | caller 已实现，缺正式素材输入 |
| fresh COLMAP 与注册质量门 | canary 已实跑，正式素材尚未运行 |
| 本地 Brush preview | 已实跑，只能 preview-only |
| 远程 submit/poll/fetch/reconnect | lifecycle receipt、恢复语义与固定演练 registry 已关闭；远程固定演练 job 已通过 |
| 远程演练真实性边界 | transport-fixture 与 fresh-container evidence 已分层，不把 fixture 当真实 GPU |
| 米制 alignment 算法输入门 | 重复、非有限、共线/近共面均 fail closed |
| measurement / policy / decision | 已独立内容寻址 |
| production import、acceptance 与 runner 复验 | v3 receipt 绑定 G2/G5；import 与最终 acceptance 均重开原始 runtime、manifest、render、closure 字节 |
| production runtime evidence | 六 probe/六 executable 合同已完成，待 fresh job-container 接入 |
| production result closure | v2 manifest、closure 与 import 消费端已完成，待 remote caller 产出 |
| Viewer/Studio 与 synthetic QA | Viewer v2 可信采集与 aggregate 消费端已实现；尚未对真实重建运行，不能签署 production |

## 正式版关键路径

```text
P0 正式采集与权利
  ├─→ P1 accepted real-photo SfM ───────────┐
  └─→ P1 实测非共面控制点 ────────────────┤
                                            ↓
GLM G1-G4 云 GPU runtime / remote caller → P2 非 mock CUDA 3DGS
                                            ↓
Codex G5 closure / G6 import（已完成）→ GLM G4 producer 接入
                                            ↓
P3 metric alignment → P4 real Viewer QA → P5 Production V1 签署
```

## 当前最高价值 TODO

### P0 — GLM：云 GPU 信任链

按
[`HANDOFF-GLM-012`](../handoff/HANDOFF-GLM-012-active-production-queue.md)
连续执行，不等待新的口头分配：

1. `P0-CI`：已由 Codex `70a965e` 关闭，专项 `12 passed`；
2. **当前 `F1`**：在同一 lifecycle container 内运行六探针 clearance，身份或 TOCTOU 漂移
   均 fail closed；
3. `G1`：让 `train-production` producer 产出既有 import 合同要求的八个结果文件；
4. `H1`：poll 不越过 deadline，所有终态与异常路径显式关闭 executor。

A1–E1 与 P0-CI 已关闭，不再重做。GLM 现在从 F1 开始，完成后不等待口头确认，
直接连续执行 G1、H1。每项独立提交并 push；Codex review 前保持 candidate。

### P0 — 外部输入：正式素材与测量

必须准备同一个 scene identity 的：

- 权利明确、密集重叠、覆盖高低视角与遮挡面的照片或视频；
- 原始媒体内容锁和权利 receipt；
- 至少四个非共面实测控制点，建议增加独立 check points；
- 允许使用的云 GPU host、固定 host key 和 immutable container digest。

这些输入不得提交仓库或进入公开 Release，统一放入忽略的
`.nantai-studio/` 工作区。

### P1 — Codex：真实产物消费与 Viewer 验收

Viewer v2 代码门已经就绪：

- production CLI 必须显式提供 evidence root，并在派生决策前重开全部绑定文件；
- report 内容绑定 scene manifest、camera set、policy、capture script、probe、
  Playwright package、Node/browser executable 前后身份和三张截图；
- camera pose ID 与 report content SHA 使用跨 Python/JavaScript 一致的 IEEE-754
  数字投影，不再受 `1`/`1.0` 词法差异影响；
- production camera-set v2 producer 从复验通过的 metric-aligned COLMAP registration
  确定性选取三个空间分离机位，并绑定 import receipt、aligned registration 与 scene
  manifest；v1 或任一来源 SHA 漂移均 fail closed；
- `pipeline.real_scene_paths` 从 source/workspace/run ID 重建 runner identity，只返回
  最新且重新验证通过的 completed production import，不靠手写 attempt 目录；
- Studio 的 `--real-scene-import-root` 会在启动时复验整份 production import，并把
  receipt-bound `web/` 重建白名单只读映射到 Viewer 固定 URL；未绑定文件、preview
  receipt、仓库 demo 回落和启动后字节漂移均拒绝；
- `pipeline.viewer_session` 在临时回环端口启动该挂载、调用既有 production capture
  与 validator，并在所有退出路径关闭服务器；机器门成功后可直接生成绑定同一 report
  的人审策略，正式采集不再依赖人工同步两个终端或第二条策略命令；
- `pipeline.human_review_inputs` 从复验通过的 Viewer v2 report 确定性生成七类人审
  policy；记录脚本用 `--viewer-report` 导入原始截图绑定，reviewer 仍须逐类明确判定；
- capture input、截图与复制出的代码采用 root-bounded、no-symlink、no-replace
  路径；浏览器可执行文件流式哈希，不按文件大小整块分配内存；
- 浏览器实际收到的 scene manifest 与 acceptance probe 响应字节必须分别匹配
  receipt 绑定，不能用 scene A 采集却在报告里声明 scene B；
- production aggregate 拒绝 Viewer v1；human review 只能消费 v2 receipt 中
  pose/path/SHA/byte length 全部一致的截图。

这只是“可信采集器与消费端完成”，不是“真实 Viewer QA 已完成”。还缺同一真实 scene
identity 的 production import、fresh 浏览器采集和人工观感签署。Viewer v1 仅保留
internal-canary/兼容用途。

Fresh synthetic browser canary 已实际运行 v2 runner，report ID 为
`viewer-capture-b59449765e3a858fe7be7e68b92036f23f69f23eddb24c6de7cb31c5b46610ba`。
它生成并重开 3 张 receipt-bound PNG，最终因 SwiftShader 软件渲染器、三机位加载
超时及样本不足得到 `accepted=false`、10 个失败门。该结果只证明 v2 runner 的
跨 JavaScript/Python 内容锁与 fail-closed 路径实跑，不提升 synthetic scene 信任。
私有 canary 产物保留在忽略的 `.nantai-studio/`，不进入 Git 或 Release。

收到 remote caller 产生的 G5 verified result 后：

1. fresh production import，复验 runtime/result/alignment 全部 SHA；
2. 生成同一 scene identity 的 chunks、LOD 和 Viewer manifest；
3. cold load、三视角、移动/旋转/缩放、console、内存和帧时间测试；
4. 对空洞、漂浮物、遮挡错误、纹理/SH 观感做人工验收；
5. 汇总 machine acceptance 与 human review，决定是否签署正式版。

## 外部依赖与停止条件

| 缺失项 | 允许继续做什么 | 禁止声明 |
|---|---|---|
| 正式素材 | 完善 caller、fixture、blocked report | accepted real-photo SfM |
| CUDA endpoint | transport/readiness 合同 | non-mock 3DGS |
| 实测控制点 | arbitrary preview | metric/aligned |
| 真实 Viewer evidence | synthetic/browser fixture QA | real-scene accepted |

任何一门缺失，状态保持 `preview / unknown / arbitrary / unaligned`。image2 设计图、
synthetic Blender、mock、stub、本机 Brush 和绿色单测都不能提升正式版信任。

## 下一次可见效果

最早可展示的真实效果不是再增加 synthetic 素材，而是取得第一份 verified cloud
PLY 后完成 production import。届时可以先看“真实但未米制”的受限 Viewer 结果；
只有控制点对齐和真实 Viewer/human QA 也通过后，才进入 Production V1 候选。
