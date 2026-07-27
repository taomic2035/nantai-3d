# Production V1 状态与 TODO

更新：2026-07-27，基线 `1688c6e`

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
| 远程 submit/poll/fetch/reconnect | 固定 transport 演练已通过 |
| 远程演练真实性边界 | 固定 11 cases，明确为 transport-fixture |
| 米制 alignment 算法输入门 | 重复、非有限、共线/近共面均 fail closed |
| measurement / policy / decision | 已独立内容寻址 |
| production import、acceptance 与 runner 复验 | v3 receipt 绑定 G2/G5；import 与最终 acceptance 均重开原始 runtime、manifest、render、closure 字节 |
| production runtime evidence | 六 probe/六 executable 合同已完成，待 fresh job-container 接入 |
| production result closure | v2 manifest、closure 与 import 消费端已完成，待 remote caller 产出 |
| Viewer/Studio 与 synthetic QA | 可用；Viewer v1 数值报告尚未绑定可信采集 runner，不能签署 production |

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
[`HANDOFF-GLM-011`](../handoff/HANDOFF-GLM-011-production-v1-critical-path.md)
连续执行 NOW-1–NOW-8：

1. 冻结旧 readiness v1，删除被替代的 caller 自报 pass 草稿；
2. 收窄 fixed read-only host preflight；
3. 加固 executable identity、输出上限与 TOCTOU；
4. 实现 fresh container 生命周期与耐久发布；
5. 在同一 container 内接入 G2 clearance；
6. 只允许 G5 verified closure 进入 runner/import；
7. 无端点时交付 canonical `blocked-external-input`；
8. 把 worker / shell 静态安全发现转成可重复 RED 测试。

当前候选不能视为关闭：

- `7febb81` 的 host preflight 尚未拒绝 relative resolved path，Podman 仍误用
  Docker `.Runtimes`，并缺真实 secret/non-UTF8/wrapper-swap RED；
- `c66a00a` 的 container lifecycle 仍允许任意 resolved image ID、覆盖
  `container-id.txt`，也未证明 terminal 状态耐久后才 cleanup；
- `da86a81` 的 blocked report 要求用占位 host/digest/dataset SHA 表示缺失输入，
  并由报告自报 `rights-cleared`，已被 Codex review 拒绝；
- `b02f6ab` 删除 production prepared-bundle 的可执行 golden-path 与 bash 语法
  测试，改用源码 grep 证明 runtime 行为，并以 `|| true` / substring 接受 CLI
  version observation，已被 Codex review 拒绝；
- 三项精确返修入口均在 `HANDOFF-GLM-011`，测试绿色不能替代返修。

### P0 — 外部输入：正式素材与测量

必须准备同一个 scene identity 的：

- 权利明确、密集重叠、覆盖高低视角与遮挡面的照片或视频；
- 原始媒体内容锁和权利 receipt；
- 至少四个非共面实测控制点，建议增加独立 check points；
- 允许使用的云 GPU host、固定 host key 和 immutable container digest。

这些输入不得提交仓库或进入公开 Release，统一放入忽略的
`.nantai-studio/` 工作区。

### P1 — Codex：真实产物消费与 Viewer 验收

import/runner/aggregate 的原始证据消费端已经就绪。下一项仓库内 P0 是 Viewer v2
采集凭证：production acceptance 必须绑定 scene manifest、camera set、policy、
采集脚本、Node/Playwright/browser executable 的内容身份与前后快照；可手写的
Viewer v1 报告只保留 canary/兼容用途。

现有 human-review v1 会重开并复验 PNG、policy 与 pose ID，但 PNG 本身尚未绑定
可信 Viewer capture receipt 或 scene manifest。仅把任意 PNG 标成同一 pose ID
不能构成 production 视觉证据。Viewer v2 必须产出内容寻址的截图集合，human
review 只能消费该 receipt 绑定的精确截图 SHA；capture receipt、report 与截图任一
漂移都必须 blocked。

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
