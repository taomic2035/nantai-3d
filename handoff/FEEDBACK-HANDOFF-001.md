# FEEDBACK — HANDOFF-001

**验收结果: ✅ 全部通过 (11/11)**

## 逐项结果

| asset_id | 结果 | 问题 |
|---|---|---|
| house_wood_01 | PASS | — |
| house_wood_02 | PASS | — |
| house_stone_01 | PASS | — |
| house_thatch_01 | PASS | — |
| house_barn_01 | PASS | — |
| tree_pine_01 | PASS | — |
| tree_broadleaf_01 | PASS | — |
| tree_bamboo_01 | PASS | — |
| stone_wall_01 | PASS | — |
| stone_lamp_01 | PASS | — |
| fence_wood_01 | PASS | — |

## 后续动作

- 导入注册表: `python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-001 --register`

## 人工备注（Codex 接管后复审）

### What

- HANDOFF-001 已升级为 manifest schema v2：11/11 PLY 均声明并通过 SHA-256 校验，
  generator 版本与脚本 hash 可审阅；坐标契约明确为 `meters / local-z-up`。
- registry 已采用内容寻址：同 SHA 重跑不升版，active payload 缺失或损坏会原位恢复；
  different SHA 才创建新版本，并保留结构化 history。
- 注册边界现强制小写安全 `asset_id`、canonical containment、重复 ID 拒绝、有限数值、
  正 scale/footprint 与有效单位四元数；schema v1 不再允许直接注册。
- replace 采用跨实例文件锁 + 磁盘态 CAS，并在 payload copy 或 registry 写入失败时回滚；
  加载、实例化与消费报告均以实际 payload SHA 复核后才放行。
- building、vegetation、prop 均真实消费 registry。`asset_consumption` 逐 asset 记录
  renderer、chunk、instances、point_count、version 与 SHA。
- `make assets` 已连续运行两次，均验收 11/11；registry 与 manifest SHA 保持不变。

### Why

自动验收、注册存在、renderer 实际消费是三个不同事实。内容寻址与 consumption report
让 fresh clone 可重建素材，也让 Studio 能依据运行证据显示“已使用”，而不是仅凭 registry 推断。

### Tradeoff

- PLY 继续不进入普通 Git，以 deterministic generator + manifest hash 换取可移植性；代价是
  首次使用需执行一次 `make assets`。
- vegetation 每 cluster 最多 12 个实例、6,000 点；选择确定性点预算而非盲目复制完整树，
  以控制 chunk 体积，近景更高保真可在后续 LOD 扩展。

### Open Questions

- 6,000 点/cluster 与 75m²/树的默认密度是否需要按终端性能档动态配置？
- Studio 是否需要把 fallback proxy 也单列为未消费原因与修复入口？

### Next Action

- 请主导 agent 将 `asset_consumption` 接入 Studio 的 validation/consumption 双状态，
  并在最终回球中附 fresh-clone `make assets` 与默认 chunk 消费证据。
