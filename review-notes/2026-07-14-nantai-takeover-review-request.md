# Review Request: Nantai 3D takeover

Review-Target-ID: `nantai-takeover`
Branch: `codex/nantai-takeover`

## What

三段提交交付 provenance-safe mixed reconstruction、可替换且可复现的 11 项素材链，以及
Spark Viewer + Studio/local adapter UX：

- `ef333fd` core coordinate / registration / GaussianScene / reconstruct
- `2498b04` assets / handoff / world consumption
- `87b9bf8` Viewer / Studio / docs / quality evidence

## Why

接管前 engine 名、文件存在与 registry 条目会被误当成真实、米制、已消费或完整 splat。
本次把 frame、transform history、synthetic、artifact attributes、actual SHA 和 runtime renderer
capability 变为机器证据，并在未知时 fail closed。

## Original Requirements

> 完善 nantai-3d，支持图片+视频输入、统一3D坐标系、图视频混合重建、可拼接可变清晰、
> 高斯泼溅、素材可替换；做 UX、生成模拟素材。先查看当前项目状态。
> 你和 opus 之间用 handoff feedback 交流协作。
> 你独立推进自己范围的工作，opus 有反馈再说。
> opus 马上要歇菜了，暂时由你独立推进。

- 来源：当前 Codex 任务用户消息
- **请对照上述原始需求判断交付是否完整，并重点找会让机器证据失真的问题。**

## Tradeoff

GPU trainer、真实 control-point benchmark、distortion-aware projection、离线 Spark bundle 与
Studio 写任务未伪装完成；当前通过 import/read-only/explicit fallback 扩展。

## Open Questions

1. 是否还有路径能把 arbitrary/unknown frame 静默提升到 meters？
2. transform chain / PLY history / manifest ancestry 是否存在可绕过的不一致？
3. registry transaction、actual SHA consumption 与 11/11 UX 是否仍有 fail-open？
4. Spark capability、artifact fidelity 与 synthetic/proxy 文案是否有误导？
5. local server 是否存在路径穿越、缓存或写操作缺口？

## Next Action

只读审查 `b973104..87b9bf8`，只报告可复现的 P1/P2（文件/行、触发方式、建议测试）；不改文件。

## 自检证据

### Spec 合规

`docs/verification/2026-07-14-quality-gate.md`：愿景 10/10 覆盖，明确外部边界。

### 测试结果

- Python：164/164
- Viewer：30/30
- Studio：30/30
- `make verify`：exit 0
- Ruff：0 errors
- `git diff --check`：exit 0
- Browser：Spark full-3dgs、11/11 assets、LOD/layer/reset 实测

### 相关文档

- Plan：`docs/superpowers/plans/2026-07-14-nantai-3d-takeover.md`
- Spec：`docs/superpowers/specs/2026-07-14-nantai-3d-studio-ux-design.md`
- Handoff：`handoff/FEEDBACK-TAKEOVER-001.md`
- Verification：`docs/verification/2026-07-14-takeover-report.md`
