# 协作历史摘要

本页取代已完成的逐轮 HANDOFF / FEEDBACK / REVIEW 文档。精简前最后一个完整快照为
Git commit `381f243ebed3bb8dcc0e47608ac1548c55e8c621`；需要逐字段追溯时从该提交或
更早历史读取原文。

## 2026-07-14 至 07-16：接管与安全基线

- 建立 provenance fail-closed、内容 SHA、坐标/尺度和 immutable job 约束。
- 完成 Studio 基础状态、严格 ingest、crash recovery 与跨平台素材门。
- 明确 synthetic/mock 只能预览，不能提升为 measured/metric/aligned。

## 2026-07-17 至 07-20：可运行 Viewer 与 Blender 基线

- 完成确定性分块、LOD、按需 synthetic 世界和大重建流式 Viewer。
- 打通照片/视频摄取、COLMAP 位姿消费、外部 3DGS 导入、Sim3/ENU 与高阶 SH 旋转。
- 建立 180-camera preflight、六层渲染、visibility 与 post-render 质量合同。
- image2 Batch 5–14 提供路线、建筑、院落、桥底和环境设计输入；均保持
  `design-only / forbidden-as-multiview`。

## 2026-07-21 至 07-23：拓扑、材质与 exact build

- reciprocal route、六角色相机、环境模块、生产 journal/caller 完成多轮 fail-closed
  修复。
- exact-218 与 Phase 4.3 得到 synthetic 实测证据；local-360 证明水车局部可读，
  但不证明全场景 coverage。
- Batch 15–25 扩展材质、路口、室内/边界、环境支撑和水车建模参考。
- 云训练 caller、registration-quality 和 artifact integrity 完成内容绑定；stub
  只能证明 argv/契约，不能代替真实云 GPU 训练。

## 2026-07-24 至 07-25：真实链路审计与 Preview 冻结

- GLM lane 完成真实 COLMAP 失败路径、dense overlap/P5b、长视频采样/P6b、P7
  recovered-pose 导入候选；Codex 修复 parser、事务替换与 WAL 收敛。
- roaming graph producer 完成纯模型与 Viewer 消费合同；当前 fixture 只有两房间、
  一 portal、零 loop，不代表任意坐标可达。
- image2 Batch 26–35 扩展近场、LOD、landmark、室内、闭环、材质和八类道具六视图。
- `Nantai 3D 1.0 Preview` 冻结为可下载 synthetic point-preview；Blender 封面是
  独立审计渲染，不与交互数据冒充同一 scene identity。

## 仍未关闭的门

1. 真实、密集、重叠且可发布的采集；
2. accepted real-photo COLMAP/SfM；
3. 非 mock 云 GPU 3DGS 输出；
4. 控制点或其它实测米制对齐；
5. 真实重建产物的 Viewer QA；
6. synthetic exact-266 的完整六目标、双 seam 和全角色质量门。

设计图、synthetic Blender、mock/COLMAP 失败 smoke、stub trainer 与本机 Brush 小样
都不能替代这些门。

## 2026-07-26 至 07-27：Preview2 与 Production V1 caller

- 发布 `v1.0.0-preview.2`；Release 仍是 synthetic point-preview，不冒充真实重建。
- 新增 rights/receipt、fresh real COLMAP、held-out split、local Brush、remote-shell
  Splatfacto、import/chunk、render/viewer/human review 和 aggregate acceptance 合同。
- fresh canary 得到真实照片 COLMAP 与本机 Brush `preview-only` 证据；未取得云 GPU
  production 结果、米制对齐或真实 Viewer/human acceptance。
- 跨平台 CI 回归已关闭；P1-1 的 pinned Node/Playwright/Chromium Viewer runtime
  门和 P1-2 的 remote-shell readiness/identity/TOCTOU/canonical publication
  已进入 main。
- P1-3A/B/C 已完成 monotonic remote observation、单点 tamper、durable job ref、
  fresh executor restore、同 attempt reconnect 与显式 retry/new attempt。
- P1-4A 已完成 source/target 重复对应点、非有限值、共线/近共面与生产默认 span
  policy 的 fail-closed 输入门。
- 当前未关闭的是 P1-3D 固定演练 runner、P1-4B/C 米制证据/runner 信任门、
  P1-5 云 CUDA readiness 与 production result closure；连续执行队列见
  [HANDOFF-GLM-011](HANDOFF-GLM-011-production-v1-critical-path.md)。
- 旧的真实差距矩阵、P1 caller/transaction/WAL 逐轮 review、GLM-005/007/008、
  P7 Viewer handoff、Batch33/34 image2 说明与跨平台 HANDOFF-002 已在
  `65dae27` 之后清理；关键边界已并入本页与当前队列，原文仍可从 Git 历史读取。

## 2026-07-28：远程 runtime clearance 与当前硬化队列

- remote target、durable job ref、worker/entrypoint/runtime source SHA 已进入固定
  worker spec；同一 lifecycle container 只有在六探针 measurement/policy/decision
  重算为 accepted 后才可 `exec` 训练。
- 旧 GLM-012 中 B1–G1 的逐步过程已从活动入口移除；完整内容保留在 Git 历史。
- 当前 GLM 队列只保留四项：poll deadline/executor close、大型 import artifact
  流式摘要、result-bundle 流式校验/提取、production v2 fetch 端到端矩阵。
- 这些仍是 repo-local 代码门；没有 fresh 云 GPU、正式素材、实测控制点和真实
  Viewer QA 时，状态继续保持 Preview / modeled-unverified。

## 2026-07-28：H1–K1 关闭与发布安全收尾

- H1 `8f97936` 显式关闭 executor 并限制 polling deadline；I1 `536b03e`
  对大型 import artifact 做稳定分块摘要。
- J1 `75f9e0c`、K1 `6f16a0c` 的直接 verifier/matrix 测试通过，但 Codex review
  发现真实 fetch caller 未启用 staging；`0f6dc99` 补齐端到端流式接入、
  file-backed PLY provenance 和发布前漂移复验。
- 旧 H1–K1 详细工单由 Git 历史保存；活动队列切换为 GLM-013 的大型 render/log、
  跨平台 archive path 与 Production 隐私机器审计。

## 2026-07-28：L1–N1 与 portable release identity 关闭

- L1 `e07658b` 补齐大型 render/log 的真实 fetch 边界；Codex fresh review 的
  18 个 streaming/fetch 专项通过。
- M1 `945f3f7` 完成基础 archive path 硬化；Codex `4b3d3de` 补上大小写与 Unicode
  normalization 组合碰撞。
- N1 `86ab506` 增加 verified-first、分块、no-secret-echo、durable no-replace 的
  Production 隐私机器审计；`8efd304` 将其加入三平台 focused CI。
- `e4a99cf` 把 portable path identity 继续贯穿 receipt、protected roots、builder
  destinations 与 extracted tree verifier。GLM-013 结束，活动入口切换为 GLM-014
  独立安全复核。

## 2026-07-28：最终四件套与 GLM-015

- Codex `c858f37` 补齐 Production 最终四件套的 privacy-gated no-replace 导出、
  standalone receipt/checksum 绑定与下载整体复验；modeled fixture 不能进入正式
  资产目录。
- 014 的隐私/path identity/exact-HEAD 安全背景已被上述实现和最新 CI 吸收；详细原文
  留在 Git 历史。GLM 活动入口切换为 015，聚焦四件套对抗审计和 runtime 自包含，
  不重开 H1–N1。
