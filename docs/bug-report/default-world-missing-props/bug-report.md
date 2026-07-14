# Default world 未消费 prop 素材

## Bug 诊断胶囊

| 栏位 | 内容 |
|---|---|
| **1. 现象** | 期望默认 `make world` 的可替换素材证据覆盖 HANDOFF-001 的 11 个素材；实际 5×5 world 只消费 8 个，`stone_wall_01`、`stone_lamp_01`、`fence_wood_01` 缺失。 |
| **2. 证据** | 2026-07-14 在 takeover worktree 执行 `make world PY=.venv/bin/python`，25 chunks/2,878,331 points/142 consumption rows；对 `asset_consumption[].asset_id` 去重后仅 5 building + 3 vegetation。所有生成的 layout 都是 `props=[]`。 |
| **3. 根因** | `DEFAULT_ASSETS["props"]` 和 `_emit_prop` 均已实现，但 `MockLayoutGenerator.generate_chunk()` 硬编码 `props=[]`，数据流在 layout source 被截断。既有测试只给 renderer 手工构造 prop，没有覆盖默认 generator → renderer 路径。 |
| **4. 诊断策略** | 从 manifest 逆向追踪 consumption → renderer → layout → generator，并与 working 的 building/vegetation 生成路径逐项对照。 |
| **5. 超时策略** | 若单次 source 修复不能让默认 5×5 覆盖 11 个素材，停止叠加 renderer 改动，改查 schema 序列化与 registry load 边界。 |
| **6. 预警策略** | prop 已在 layout 但报告仍缺失，说明当前根因假设不完整；回到 `_emit_prop` 的实测 SHA/registry evidence，而不是继续改 generator。 |
| **7. 用户可见交互修正** | 默认村庄会出现石墙、石灯与木栅栏；Studio 的 11/11 消费状态与真实 world manifest 一致。 |
| **8. 验收** | `test_default_chunk_references_every_replaceable_prop_asset` 先红后绿；5×5 world 为 3,129,456 points/217 rows/11 unique assets，renderer 包含 building 5、vegetation 3、prop 3；总门禁保持通过。 |

## 五件套

### 1. 报告人

Codex 在全链路证据门禁中发现，不是由 UI fixture 或用户报告触发。

### 2. 复现步骤

1. 执行 `make assets PY=.venv/bin/python`。
2. 执行 `make world PY=.venv/bin/python`。
3. 对 `web/data/manifest.json` 中的 `asset_consumption[].asset_id` 去重。
4. 期望 11 个，实际 8 个；所有 `layouts/chunk_*.json` 的 `props` 为空。

### 3. 根因分析

素材注册、prop renderer 和 consumption report 都能在手工 fixture 下工作。默认生成器虽声明三种
prop，却没有生成 `Prop` 实例，因此后续组件没有机会消费它们。缺失的 generator 集成回归让这个
断点长期被局部绿测试遮蔽。

### 4. 修复方案

在 source 层为每个默认 chunk 确定性生成三种 prop，放在道路附近且避开建筑带；不改 renderer，
也不在 Studio 伪造消费状态。先用 generator 回归测试固定行为，再重跑真实 world manifest。

### 5. 验证方式

- `tests/test_mock_layout_assets.py`
- `make world PY=.venv/bin/python`
- manifest unique asset/renderer 汇总
- 全量 `make test`、Ruff 与 `git diff --check`

最终证据：默认 25 chunks 每个都生成三类 prop；world manifest 的 11 个注册素材全部存在
实测 SHA 消费记录，Studio local adapter 显示 `registered=11 / consumed=11 / blocked=0`。
