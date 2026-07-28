# 文档入口

核心文档：

- [Preview2 下载、独立校验与运行](releases/1.0-preview.2.md)
- [Production Runtime 发布手册](manual/production-runtime-release.md)
- [真实重建端到端手册](manual/reconstruction-setup.md)
- [Production V1 状态与 TODO](production-v1-status.md)
- [真实数据与 Sim3/ENU 契约](real-data-workflow.md)
- [Synthetic 设计素材 Releases](releases/synthetic-design-inputs.md)

[Preview1 说明](releases/1.0-preview.md) 是历史版本记录，不是当前安装入口。

机器可读合同位于 [`contracts/`](contracts/)。当前版本的发布说明见根目录
[`CHANGELOG.md`](../CHANGELOG.md)。

## 内部证据

- `verification/` 只保留当前发布门和长期有效的 fail-closed / 可复现性证据。
- `superpowers/` 只保留仍有后续执行价值的近期设计与计划。
- `handoff/` 只保留当前协作规格和最近审计；旧过程已压缩到
  [`handoff/HISTORY.md`](../handoff/HISTORY.md)。
- `bug-report/` 是可复现缺陷记录，不是产品使用手册。

任何历史文档、设计图或 synthetic 证据都不能提升真实场景可信度。
