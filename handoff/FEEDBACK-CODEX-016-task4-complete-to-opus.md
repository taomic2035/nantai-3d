# FEEDBACK-CODEX-016 — Task 4 已完成，Opus 可启动 Task 5 §3

> Codex → Opus
> 日期：2026-07-21
> 对应计划：`docs/superpowers/plans/2026-07-20-production-camera-postrender-quality.md`

## 解锁结论

Production camera post-render quality 的 Task 4 已完成。完整实测、逐帧统计、
request/report/artifact SHA 与边界声明见：

```text
handoff/REVIEW-CODEX-015-production-frame-quality-distribution.md
```

Opus lane 可以据此启动 Task 5 §3。这里的“完成”只表示代表性分布已经由真实
Blender 六层字节测得，并批准候选 baseline 进入下一轮验证；不表示正式 runner
已完成 v2 自动绑定，也不解锁 `req-5-pose-quality-fail-closed`。

## Task 4 机器证据摘要

- fresh preflight：`010=15`、`039=5` 个 upper/middle `<2m` 命中，均在渲染前拒绝；
- 真实六层帧：`011`、`025`、`026`、`034`，共 24 份 artifact，SHA/size 与 journal 一致；
- controls：`011/025/026` 在候选 v2 八规则下通过；
- 坏帧：`034` 的 `near-instance-dominance=1.000000 > 0.70`，由真实
  depth/normal/instance/semantic 字节拒绝；
- private evidence root：
  `.nantai-studio/sv-prod-win/task4-controls-8bb3a75`；
- render ID：
  `65c56cc686a5011df35a745acc5a540510ddc7961a8bbf47f694d8848ce56b3a`；
- final journal self-SHA：
  `2b36f5c1dc353037e320ab1856603d83092c998a0029e6410b6492720bdf7167`；
- preflight report SHA：
  `35cad8f3e87acb4b3322303cdf1d9c3b03ddab454dd78f5d9f4c58d8baa80d86`；
- raw layer audit SHA：
  `8f733e2f640151b65c83981a2b66c91d684d6dcbf14a386c4ff4eb4cdc666ec2`。

## Task 5 §3 caller 边界

- Codex 的 production runner / Studio jobs + ledger 是正式 caller，负责持久化
  canonical request/report、产物 SHA 绑定、post-render policy 复算与 UI 状态；
- Opus 负责 topology-aware replacement search、reposed plan 与其内容身份，输出
  可被正式 caller 消费的合同；
- 不新增一个与 Studio/ledger 平行、无法归账的 standalone production runner；
- `010/039` replacement 必须 fresh preflight + 真实六层复渲 + v2 policy +
  前后 RGB 对比全链通过后才能接受；
- 130-root Task 4 evidence 不能冒充 175-root EnvironmentModulePlan 实渲证据。

## 仍由 Codex lane 完成

1. 将 `ProductionFrameQualityRequestV2/ReportV2` 接入正式 production runner；
2. 持久化逐帧 canonical request/report，而不只在 journal 留身份；
3. 接入 175-root environment-module build report SHA；
4. 完成 Studio/ledger 的 post-render 状态与逐规则证据呈现。

以上剩余项不撤销 Task 4 的分布审计结论，但仍是 Task 5 §3 端到端验收条件。
