# REVIEW-OPUS-012 — production_profile.py GLM-P0 变更 fail-closed 审计

> 日期：2026-07-22
> 发起：Opus lane (GLM-5.2 临时接替)
> 审计对象：`pipeline/synthetic_village/production_profile.py` 中 GLM-P0 Step 2/3
> 引入的 route_loops 扩展与 `_EXPECTED_ROUTE_LOOP_CONTRACT` 变更
> 方法：逐环节对抗性审计，模拟攻击者尝试绕过 route_loops 验证
> 结论：**全部通过，无 fail-open 漏洞；1 个 INFO 级 observation**

## 审计范围

| 变更 | 来源 | 行号 |
|---|---|---|
| `LoopId` 扩展为 4 值 | GLM-P0 Step 2/3 | elevated_topology.py:42 |
| `ElevatedTopologySummary` 约束放宽 | GLM-P0 Step 2/3 | elevated_topology.py:163-166 |
| `route_loops` Field max_length 2→4 | GLM-P0 Step 2/3 | production_profile.py:354 |
| `_EXPECTED_ROUTE_LOOP_CONTRACT` 扩展 | GLM-P0 Step 2/3 | production_profile.py:221-258 |
| `_validate_plan` route_loops 检查 | 既有 | production_profile.py:393-402 |
| `_route_loop_evidence` | 既有 | production_profile.py:525-585 |
| `_elevated_sources` | 既有 | production_profile.py:491-505 |
| `pose_separation_evidence` docstring | 本轮修复 | production_profile.py:979-980 |

## 审计环节（6 项）

### 环节 1 — route_loops validator 使用严格 tuple 相等 ✅ PASS

```python
route_contract = tuple(
    (row.loop_id, row.ground_attachment_node_ids, row.elevated_edge_ids)
    for row in self.route_loops
)
if route_contract != _EXPECTED_ROUTE_LOOP_CONTRACT:
    raise ValueError("route loop evidence must cover the stable topology")
```

**对抗分析**：
- **顺序攻击**：如果 route_loops 顺序不同于 contract（如 valley 在 bridge 前），tuple 比较失败 → fail-closed ✅
- **数量攻击**：如果只提供 2/3 个 loops（少于 4），tuple 长度不同 → fail-closed ✅
- **内容篡改**：如果 ground_attachment_node_ids 或 elevated_edge_ids 被篡改，tuple 元素不同 → fail-closed ✅
- **类型安全**：`RouteLoopEvidence.ground_attachment_node_ids: tuple[str, str]` 固定 2 元素；
  `elevated_edge_ids: Field(min_length=3)` 至少 3 条 edge。contract 中每个 loop 恰好 3 条 edge。

**结论**：严格 tuple 相等是最强的比较方式——任何偏差都会被拒绝。

### 环节 2 — route_loops Field max_length=4 不构成 fail-open ✅ PASS

`Field(min_length=2, max_length=4)` 允许 2-4 个 route_loops 进入 schema。
但 `_validate_plan` 的 route_contract 检查强制 route_loops 必须恰好匹配 4 元素的
`_EXPECTED_ROUTE_LOOP_CONTRACT`。因此 `min_length=2` 是一个更宽松的 schema 级约束，
被更严格的 validator 覆盖。少于 4 个 loops 的输入会在 validator 层被拒绝。

**对抗分析**：尝试构造 2 个 route_loops（central + upper）→ route_contract 是 2 元素 tuple
≠ 4 元素 `_EXPECTED_ROUTE_LOOP_CONTRACT` → fail-closed ✅

### 环节 3 — `_route_loop_evidence` 正确处理 4 loops ✅ PASS

函数遍历 `topology.loops`（现在 4 个），对每个 loop：
1. 收集该 loop 的所有 edge 涉及的 node IDs
2. 找出 ground level 的 nodes → **必须恰好 2 个**（`len(attachments) != 2` → fail-closed）
3. 验证两个 ground nodes 在 path graph 中连通（BFS）→ 不连通则 fail-closed
4. 构造 `RouteLoopEvidence`

**新增 bridge-loop 验证**：
- bridge-ground-east (-180,-90) 和 bridge-ground-west (-205,-82) 都在 path-network-001 上
- path-network-001 的 polyline 包含这些点作为端点 → path graph 顶点 → BFS 连通 ✅

**新增 valley-loop 验证**：
- valley-ground-north (-90,-35) 和 valley-ground-south (-130,-58) 都在 path-network-002 上
- path-network-002 的 polyline 包含这些点作为端点 → path graph 顶点 → BFS 连通 ✅

### 环节 4 — `_elevated_sources` 自动包含新 edges ✅ PASS

```python
def _elevated_sources(topology):
    return [
        ElevatedPolylineTopologySource(
            ..., topology_ref=edge.edge_id, loop_id=edge.loop_id, ...
        )
        for edge in topology.edges
    ]
```

遍历 **所有** `topology.edges`，无 loop_id 过滤。新增的 6 条 bridge/valley edges
自动被包含为 `ElevatedPolylineTopologySource`。elevated-pedestrian 相机自动分配到
这些新 edges 上（每条 edge 4 台，双向各 2 台 = 48 台 / 12 条 edge）。

### 环节 5 — production_repose.py 兼容性 ✅ PASS

`production_repose.py` 通过 `PolylineTopologySource`（ground-route path segments）
工作，不直接引用 `loop_id` 或 `route_loops`。repose 只替换一台相机的 position，
不修改 route_loops。替换后的 plan 重新通过 `_validate_plan` validator 时，
route_loops 检查仍然强制 4-loop contract。无兼容性问题。

### 环节 6 — pose_separation_evidence docstring 过时数据 ✅ PASS (已修复)

**发现**：docstring 中记录的最近 pair 距离 "约 3.5m" 在 4-loop 拓扑扩展后
已严重过时。当前实测最近 pair 为 **0.198m**（ground-route-013 与
elevated-pedestrian-007）。

**原因**：新增 bridge/valley elevated edges 导致 elevated-pedestrian 相机被分配到
更多 edges 上，某些 elevated 相机位置接近 ground-route 相机。

**修复**：更新 docstring 为 "约 0.2m (ground-route-013 与 elevated-pedestrian-007
在 central-loop 上方步道与地面路径交汇处)"。

**注意**：这不是 fail-open——`pose_separation_evidence` 只报告分布，不设阈值，
`threshold: None`。`_validate_plan` 只拒绝精确重复中心（`centres` 去重），
0.198m ≠ 0.0m 所以不触发。这是 req-5 的已知未交付项。

## INFO 级 observation

**0.198m 最近 pair 值得关注**：ground-route-013 与 elevated-pedestrian-007
相距仅 0.198m，几乎是重叠相机。虽然这不是 fail-open（req-5 not-implemented），
但如此近的 pair 可能影响下游 COLMAP 的基线质量。Codex 在 fresh Phase 4.3 probe
中应关注这对相机的实际渲染质量。如果六层门或 post-render 质量门拒绝其中一台，
repose 可通过 `search_replacement_pose` 搜索替代位置。

## 修复汇总

| 级别 | 数量 | 描述 |
|---|---|---|
| FAIL | 0 | 无 fail-open |
| MEDIUM | 0 | 无 |
| LOW | 0 | 无 |
| INFO | 1 | pose_separation_evidence docstring 过时数据已更新 |
| 代码修复 | 1 | docstring 最近 pair 3.5m → 0.2m |

## 附加验证

10 项拓扑完整性检查全部 PASS（详见对话记录）：
1. Ground nodes 在 path-network polylines 上（dist=0.000m）
2. Loop edges 匹配 _EXPECTED_ROUTE_LOOP_CONTRACT
3. ElevatedTopologySummary 约束诚实（loop_count=4, ground=8, component=4）
4. Loop 闭合性（bridge/valley 三角形闭合，central/upper 地面路径闭合）
5. 无孤立 edge
6. 无重复 node
7. Component edge binding（cross-level-covered-passage-v1: 9 edges 声明=实际）
8. route_loops evidence 与 topology 一致
9. 每个 loop ≥ 2 distinct ground attachment nodes
10. Covered edges 有有效 head_clearance

---

Co-Authored-By: GLM-5.2 <noreply@z.ai.com>
