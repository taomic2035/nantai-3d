# handoff/ — Claude ↔ GPT 协作协议

> 2026-07-14：Opus 暂停期间由 Codex 在隔离分支临时接管。当前状态、边界与恢复入口见
> [`TAKEOVER-2026-07-14.md`](TAKEOVER-2026-07-14.md)。原主工作区保持为 Opus 现场。

本目录承载两个 AI 之间的**交办 (handoff) / 回执 (feedback)** 双向协作闭环。

## 角色分工

| 角色 | 职责 |
|---|---|
| **Claude** (本仓库维护方) | 架构与管线代码、规格定义、交付物自动验收、集成 |
| **GPT** (素材生成方) | 按 HANDOFF 规格生成模拟素材 (3DGS ply / 测试数据), 按 FEEDBACK 整改 |

## 协作流程

```
Claude 写 handoff/HANDOFF-xxx-*.md   (素材规格 + 验收标准)
        │
        ▼
GPT 生成交付目录  handoff/deliverables/HANDOFF-xxx/
        │           (manifest.json + *.ply, 结构见各 HANDOFF 文档)
        ▼
Claude 运行自动验收:
        python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-xxx
        │
        ▼
生成 handoff/FEEDBACK-HANDOFF-xxx.md  (逐项 PASS/FAIL + 整改意见)
        │
        ├─ 有 FAIL → GPT 按 FEEDBACK 整改, 重新交付 → 回到验收
        │
        └─ 全 PASS → 导入素材注册表:
             python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-xxx --register
             (布局引用的 asset_id 不变, 重渲染即用新素材 → 素材可替换)
```

## 约定

- **一份 HANDOFF 一个交付目录**, 整改时整目录重新交付 (含 manifest.json)。
- HANDOFF 文档必须包含: 背景、交付物结构、坐标/格式约定、逐项规格、验收命令。
- FEEDBACK 由 `pipeline/validate_handoff.py` 自动生成, 不手写; 需要人工补充意见时
  追加在文档末尾 "人工备注" 一节。
- 交付物中的 ply 一律为**二进制 little-endian PLY**; 属性字段见 HANDOFF 文档。
- GPT 可以交付"生成脚本 + 运行产物", 脚本放交付目录 `scripts/` 子目录 (可选,
  验收只看产物)。

## 状态

| Handoff | 主题 | 状态 |
|---|---|---|
| [HANDOFF-001](HANDOFF-001-mock-assets.md) | 村庄素材库模拟生成 (11 个 3DGS 素材) | ✅ schema v2 验收/注册/默认 world 消费均 11/11；fresh-checkout 可由 generator 恢复 (仅同平台) |
| [HANDOFF-002](HANDOFF-002-cross-platform-reproducibility.md) | 素材字节跨平台可复现 (量化/重生成 + 重 baseline) | ⚠️ Ubuntu/Windows CI 11/11；当前 Mac payload 已由 Linux/x86_64 权威字节恢复为 11/11，但 Mac 原生生成器的 2/11 漂移与正式 payload 分发仍待处理 |
| [TAKEOVER-001](FEEDBACK-TAKEOVER-001.md) | 坐标、混合重建、3DGS、素材、Viewer 与 Studio 接管 | ✅ 本地门禁通过，等待 Opus review；外部 GPU/实测数据边界已列明 |
