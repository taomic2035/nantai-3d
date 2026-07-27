# 协作交接索引

`handoff/` 只保留仍在执行的规格、最近交付证据和关键审计。已完成或被替代的逐轮
对话记录已压缩到 [HISTORY.md](HISTORY.md)；完整原文仍可从 Git 历史读取。

## 当前真实场景主线

1. [GLM 当前连续工单](HANDOFF-GLM-012-active-production-queue.md) — 唯一执行入口：
   `H1 → I1 → J1 → K1` 连续完成；Codex G1 已由 `92b76b5` push 并解锁后两项。
   每项都有独立
   文件边界、RED/GREEN、提交和停止条件，不再从已关闭的 F1/G1 历史开工
2. [Production V1 关键路径审计背景](HANDOFF-GLM-011-production-v1-critical-path.md)
3. [Production V1 实现计划](../docs/superpowers/plans/2026-07-26-production-v1-real-golden-path.md)
4. [真实 golden-path canary 证据](../docs/verification/2026-07-26-real-golden-path-canary.md)
5. [Roaming graph producer（P2）](HANDOFF-GLM-009-roaming-graph-producer.md)

Production V1 的 P0/P1-1/P1-2/P1-3A-C 已关闭，P1-3D 固定演练由 `4150cfb`
关闭；P1-4A 由 `42df736` 关闭，P1-4B 的测量 / policy / decision 分层由
`0ad9417` 关闭；P1-4C production import 与 runner 字节复验由 `23a2ece`、
`8693848` 关闭。production runtime evidence 合同由 `cba2a19` 关闭，production
result bundle v2 与最终身份 closure 由 `5a0ca09` 关闭。B1/C1/D1/E1 已关闭，
E1 candidate `b71e5de` 与 Codex closure `ab0c7dc` 已推送；P0-CI registry 漂移由
Codex `70a965e` 关闭。GLM 当前先关闭可独立执行的 H1 deadline/executor-close 与
I1 bounded-memory import hashing；随后关闭 result-bundle 流式校验/提取与 v2 fetch
端到端矩阵。active paths、命令与停止条件只看
[HANDOFF-GLM-012](HANDOFF-GLM-012-active-production-queue.md)。HANDOFF-GLM-011
仅保留近期 review 背景，不再作为开工单。

真实重建仍需同时取得：真实重叠采集、accepted real-photo SfM、非 mock GPU
3DGS、实测米制对齐、真实 Viewer QA。缺一项都不能报告“真实场景已完成”。

GLM-007/008 与 P7 recovered-pose Viewer QA 已被 Production V1 主线替代，只在
Git 历史中用于回归追溯，不再作为当前任务入口。

## 当前 synthetic / Blender 主线

- [Production camera quality gates](HANDOFF-OPUS-006-production-camera-quality-gates.md)
- [Batch 6 modules productionization](HANDOFF-OPUS-007-batch6-modules-productionization.md)
- [Exact-266 perimeter evidence](FEEDBACK-HANDOFF-CODEX-028-batch24-exact266-perimeter-closure.md)
- [Batch35 prop turnarounds](FEEDBACK-IMAGE2-040-batch35-prop-turnarounds.md)

这些产物均不得从设计图、文件名或 renderer 名称推导可信度。只有 fresh build、
实测 SHA、六层/visibility/post-render 报告可以改变 acceptance 状态。

## 最近代码审计

- [P5b/P6b/P7 evidence](REVIEW-CODEX-030-glm-p5b-p6b-p7-evidence.md)
- [COLMAP parser](REVIEW-CODEX-031-glm-5a98ed9-colmap-parser.md)
- [Transactional replacement v3](REVIEW-CODEX-035-glm-p7-parser-wal-v3.md)
- [Roaming graph v1](REVIEW-CODEX-036-glm-roaming-graph-v1.md)

## 协作约定

- 单一 `main`、单一 worktree、路径限定提交。
- 提交前必须有对应测试和 `ruff`；证据不足时 fail closed。
- push 仅对单次命令使用临时代理：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- Release 不包含候选图、失败请求、私有 Blender 工作目录和中间日志。
