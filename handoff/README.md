# 协作交接索引

`handoff/` 只保留仍在执行的规格、最近交付证据和关键审计。已完成或被替代的逐轮
对话记录已压缩到 [HISTORY.md](HISTORY.md)；完整原文仍可从 Git 历史读取。

## 当前真实场景主线

1. [真实场景差距与连续队列](HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md)
2. [明确下一队列与临时 Git 代理](HANDOFF-GLM-008-explicit-next-queue-and-git-proxy.md)
3. [Roaming graph producer](HANDOFF-GLM-009-roaming-graph-producer.md)
4. [P7 recovered-pose Viewer QA](HANDOFF-CODEX-013-viewer-qa-p7-recovered-pose-splat.md)

真实重建仍需同时取得：真实重叠采集、accepted real-photo SfM、非 mock GPU
3DGS、实测米制对齐、真实 Viewer QA。缺一项都不能报告“真实场景已完成”。

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
