# FEEDBACK-HANDOFF-OPUS-009 — Phase 4.5 topology anchor ground nodes

> 日期：2026-07-22
> 负责人：Opus (GLM-5.2)
> 状态：Phase 4.5.1 已交付（3 个孤立 anchor ground node）；Phase 4.5.2 待推进
> 信任边界：`synthetic=true`、`verification_level=L0`、
> `geometry_trust=simplified-pbr-not-render-parity`、`trust_effect=none`

## 背景

Codex 在 `FEEDBACK-HANDOFF-CODEX-011-phase44-central-canary.md` 中报告：
`central-courtyard-downhill` canary 成功（距离 `central-ground-east` 14.836m），
但其余 5 个 role 因 nearest ground-node distance > 30m 绑定上限而无法接入：

| Role | Nearest ground-node distance |
|---|---:|
| bridge deck | 125.783 m |
| watermill tailrace | 159.017 m |
| covered gallery | 31.268 m |
| forest/orchard boundary | 51.384 m |
| lower valley uphill | 162.389 m |

Codex 指出："Next work must either add measured/canonical topology nodes near
those modules or relocate each module onto existing canonical topology and
terrain, then repeat fresh build, mesh probe, preflight, six layers, target
visibility and post-render v2."

## Phase 4.5.1 — 3 个孤立 anchor ground node

### 方案

在 `ElevatedTopologyPlan` 中添加 3 个孤立 ground node，每个位于现有 path-network
折线顶点上（verify 接受），不参与任何 loop/edge。

关键设计：修改 `_validate_graph_contract` 的 `ground_attachment_count` 计算逻辑，
只统计参与 edge 的 ground node（loop attachments），孤立 anchor node 不计入。
`Literal[4]` 保持不变，不需要 schema bump。

### 新增 anchor ground node

| node_id | position (x, y) | path | 最近 module | 到候选 3D 距离 |
|---|---|---|---|---:|
| `bridge-ground-001` | (-165, -78) | path-network-002 | bridge-deck-crossing | ~18.6 m |
| `gallery-ground-001` | (58, 43) | path-network-003 | covered-gallery-underpass | ~23.3 m |
| `watermill-ground-001` | (-180.736, -106.808) | path-network-001 | watermill-tailrace | ~11.7 m |

三个位置均为现有 path-network 折线顶点，距折线 0m，z 由 `_node` helper 自动取
terrain。

### 修改文件

1. `pipeline/synthetic_village/elevated_topology.py`：
   - `_validate_graph_contract`：`ground_attachment_count` 改为只统计参与 edge
     的 ground node
   - `build_elevated_topology_plan`：添加 3 个孤立 ground node
2. `tests/test_synthetic_village_elevated_topology.py`：
   - `test_ground_attachments_lie_on_the_declared_real_path_and_match_terrain`：
     `len(ground)` 从 4 改为 7
   - 新增 `test_module_anchor_ground_nodes_are_isolated_and_on_declared_paths`

### SHA 变化

| 身份 | 新 SHA-256 |
|---|---|
| elevated_topology_sha256 | `b159a6de9f1bea3470f8e1b772e920a53e270782f4ecc7641c6f38e6d0b4e146` |
| reciprocal_route_module_plan_sha256 | `022e0d366220169c84a4960b46212403e058f078b210ed177464af5cf20a577a` |

node_count = 11（8 原有 + 3 新增）；ground_count = 7（4 loop + 3 anchor）；
`ground_attachment_count` 仍为 4。

### 测试

- 8 TDD passed（elevated_topology）
- 254 passed（reciprocal_route_module + runtime + production_profile +
  environment_module + environment_module_runtime）无回归
- ruff clean

### 解除的阻塞

3 个 role 现在可以接入：
- `bridge-deck-crossing`：使用 `bridge-ground-001` 作为 WalkableNodeBinding
- `watermill-tailrace`：使用 `watermill-ground-001`
- `covered-gallery-underpass`：使用 `gallery-ground-001`

## Phase 4.5.2 — forest/lower-valley 重定位（待推进）

### 问题

`forest-orchard-boundary` 和 `lower-valley-uphill` 的候选相机位置远离所有现有
path（最近 path 分别 51.4m 和 162.4m），无法通过添加 ground node 解决。

根因：`_default_role_camera_candidates` 中 part 位置使用
`offset_y = (instance_id - 176) * 2.5`，把后段 module 的 part 推到 y=150+，
远离所有 path。

### 待选方案

1. **重定位 module base position**：将 forest/lower-valley 移到现有 path 附近
   - 但 offset_y 仍会把 part 推远，需要同时修改 offset 逻辑
2. **修改 part 布局逻辑**：让 offset_y 相对 module 自身起点，而非全局 176
3. **扩展 path-network**：添加新 path 覆盖 forest/lower-valley 区域
   - 但 ScenePlan v1 锁定 6 条 path，需要 scene v2

需要进一步分析 part 布局逻辑后决定方案。

## Codex caller 接入

Phase 4.5.1 完成后，Codex 可以为 3 个已解除阻塞的 role 接入：

1. 使用 fresh build（elevated_topology_sha256 已变，需要 fresh 218-root build）
2. 对 bridge/watermill/gallery 分别构造 `WalkableNodeBinding`：
   ```python
   WalkableNodeBinding(
       node_id="bridge-ground-001",  # 或 gallery-ground-001 / watermill-ground-001
       node_position_m=<从 topology plan 读取>,
       level="ground",
   )
   ```
3. 跑 fresh preflight + 六层 + post-render v2 + target visibility

注意：elevated_topology_sha256 变化导致 production plan SHA 变化，所有下游
identity（build_id, render_id 等）都会变化。
