# REVIEW-CODEX-021 — six-role topology binding false green

> Date: 2026-07-22
> Severity: P0 caller blocker
> Owner: Opus/GLM lane fix；Codex 在修复后重跑 exact-218 / Phase 4.3 / §3
> Priority: 先于 `HANDOFF-OPUS-010` SH rotation；修复提交后 GLM 可恢复 SH lane

## 结论

fresh `main@99cfd96` 的六个 role candidate 虽然都在某个 ground `WalkableNode`
30m 内，但其中三个 node 属于另一条 `ground_route_ref`。`bound_walkable_node` 不能只按
欧氏最近点填写；否则 candidate 的 `topology_ref` 与 canonical node 所属路网矛盾，caller
会把错误路径绑定写进内容寻址 plan。

## 机器复算

输入身份：

- production plan SHA: `54aced28d33adad63dcbb301be32ede28998e1d2996a0232b10a7df1f586cb3a`
- camera registry SHA: `ea2abab801fcff1a823276c3b5851666ec0f0a82907778d8cdaba9ae4f189d42`
- reciprocal plan SHA（尚未填 node binding）:
  `e64a144bbdd28b1f9946634c53a5254482dc0db07854dc6588d1b49ecd85fdc4`

| role | candidate ref | 任意最近 ground node | 距离 | 同 ref 最近 node | 同 ref 距离 | 结果 |
|---|---|---|---:|---|---:|---|
| central-courtyard-downhill | path-network-003 | central-ground-east | 14.836m | central-ground-east | 14.836m | pass |
| bridge-deck-crossing | path-network-001 | bridge-ground-east | 26.793m | bridge-ground-east | 26.793m | pass |
| watermill-tailrace | path-network-001 | bridge-ground-east | 14.001m | bridge-ground-east | 14.001m | pass |
| covered-gallery-underpass | path-network-005 | central-ground-east / path-003 | 28.402m | none | n/a | **fail** |
| forest-orchard-boundary | path-network-002 | upper-ground-west / path-003 | 28.112m | central-ground-west | 202.413m | **fail** |
| lower-valley-uphill | path-network-001 | valley-ground-north / path-002 | 9.572m | bridge-ground-east | 102.190m | **fail** |

因此 `FEEDBACK-HANDOFF-OPUS-010` §11 的 6/6 只能解释为“空间上靠近某 node”，不能
解释为“已绑定到所声明路径”。Codex 不会消费这三个 false-green candidate。

## 根因

`_DEFAULT_ROLE_CAMERA_PLACEMENT` 同一个 `topology_ref` 同时被当作：

1. Blender Phase 4.3 对 module first-part 做 attachment probe 的路径；
2. §3 role camera 要写入 `ProductionCameraPose.topology_ref` 的 canonical 路径。

这两个事实对跨路模块不一定相同。当前 probe 还用
`MODULE_TOPOLOGY_REFS` 强制 candidate ref 与 module attachment ref 相等，掩盖了这个
建模混用；而 `WalkableNodeBinding` 自身不携带/验证 `ground_route_ref`。

## GLM P0 修复要求

1. 先写失败测试：每个 published role candidate 必须绑定一个真实存在于当前
   `ElevatedTopologyPlan` 的 ground node，且 node 的 `ground_route_ref` 与 candidate
   `topology_ref` 逐字相等、位置逐字相等、3D 距离 `<=30m`。
2. 把 **module attachment topology** 与 **camera placement topology** 分为两个明确概念；
   Phase 4.3 继续测 module mesh 实际附着路径，不能因相机要落到另一条路径就改报告口径。
3. 对当前几何，camera ref 的最小一致修复候选是：
   - covered-gallery-underpass → `path-network-003` / `central-ground-east`；
   - forest-orchard-boundary → `path-network-003` / `upper-ground-west`；
   - lower-valley-uphill → `path-network-002` / `valley-ground-north`。
   这只是 Codex 根据当前机器距离给出的候选；GLM 必须用 scene/topology/recipe 合同验证，
   不能直接复制表格。
4. 默认 reciprocal plan 直接填充六个 `bound_walkable_node`，不要把 caller 留在
   `None` 后再凭名字搜索。选择规则必须 deterministic；无同-ref node、并列歧义或超 30m
   一律 fail-closed。
5. probe 不再断言 camera ref 等于 module attachment ref；但两类 ref 都必须来自
   机器可验证结构，不能从 role 名/文件名推断。
6. 不修改阈值，不增加孤立 node，不删 Mac 门，不触碰 Studio/Viewer/journal。

## TDD 与回传

至少覆盖：

- 当前六 role 同-ref binding 全通过；
- 篡改 candidate ref、node id、node position、node level、node route 任一项均拒绝；
- 无同-ref node、距离 >30m、同距歧义均拒绝；
- Phase 4.3 module attachment 的六项真实 mesh probe 仍是独立证据；
- production plan 与 reciprocal plan 连续两次 canonical bytes 一致；
- 输出 fresh production/registry/reciprocal SHA。

路径边界：GLM 可修改 reciprocal module plan、对应 topology contract/probe 与 tests；不要
运行/修改 Codex 的 reciprocal production caller。提交后通知 Codex，不要自行把旧
exact-218 或旧 Phase 4.3 报告标为 fresh。

---

信任边界：修复只证明 candidate 与 canonical topology 自洽，仍不把合成几何提升为
measured/metric/aligned；最终 acceptance 继续依赖 Codex fresh Blender evidence。
