# HANDOFF-OPUS-008 — Task 5 §3 caller 合同消费指南

> 交接：Opus（pipeline / 内容寻址 / repose 合同）→ Codex（§3 production runner / Studio）
> 日期：2026-07-21
> 对应：
> - `docs/superpowers/plans/2026-07-20-production-camera-postrender-quality.md` Task 5 §3
> - `handoff/FEEDBACK-CODEX-016-task4-complete-to-opus.md`（Task 4 完成，§3 解锁）
> - `handoff/FEEDBACK-HANDOFF-OPUS-006-phase2-task5.md`（§1+§2 已交付）

## 一句话

**Opus lane 在 §3 的合同输出已完整：`search_replacement_pose` →
`build_reposed_plan` → `production_render_id(repose_search_sha256=...)` 三段链路
全部就位，且与 `environment_module_build_report_sha256` 共存绑定。Codex caller
（production runner / Studio）现在可以把这些合同串起来跑 fresh Blender preflight
+ 六层实渲 + post-render policy + 前后 RGB 对比。**

## 解锁状态

Task 4 已完成（`handoff/REVIEW-CODEX-015`），Task 5 §3 的 Opus 合同部分就绪。
Codex 现在可以实施 §3 caller 端的完整链路：

```
failing_decision (Codex caller 持有)
    │
    ▼
search_replacement_pose(plan, camera_id, failing_decision,
                       preflight_report_sha256,
                       topology, candidate_policy)
    │
    ▼  ReplacementPoseSearch
    │   - accepted_geometry_candidate
    │   - search_sha256
    │   - previous_plan_sha256 / previous_camera_registry_sha256
    │
    ▼
build_reposed_plan(search, plan=plan)
    │
    ▼  (new_plan, new_plan_sha256, new_camera_registry_sha256)
    │   新 plan 已重新过 pydantic 验证 + SHA 比对
    │
    ▼
对 new_plan 跑 fresh ProductionClearanceRequest → Blender preflight
    │   (Codex caller 执行)
    │
    ▼  preflight 通过 → 进入六层实渲
    │
    ▼  六层 artifact + ProductionFrameQualityRequestV2 + v2 policy
    │
    ▼  production_render_id(
    │       plan=new_plan,
    │       ...,
    │       preflight_id=<fresh preflight>,
    │       quality_policy_sha256=<v2 quality policy>,
    │       post_render_policy_sha256=<v2 post-render policy>,
    │       repose_search_sha256=search.search_sha256,
    │       environment_module_build_report_sha256=<if 175-root scene>,
    │   )
    │
    ▼  前后 RGB 对比 + measured comparison
    │
    ▼  全部通过 → 允许 canonical 180-camera plan 替换
```

## Opus 合同清单（已交付）

### 1. `search_replacement_pose` (production_repose.py)

**入口**：
```python
def search_replacement_pose(
    *,
    plan: ProductionCameraPlan,
    camera_id: str,
    failing_decision: ProductionCameraClearanceDecision,
    preflight_report_sha256: str,           # caller 已绑进 journal 的 SHA
    topology: PolylineTopologySource,
    candidate_policy: ReposeCandidatePolicy,
    scene: ScenePlan | None = None,
) -> ReplacementPoseSearch: ...
```

**fail-closed 校验（8 道，§1）**：
- 拒绝 `passes=True` 的 decision
- 拒绝 `failing_decision.camera_id != camera_id`
- 拒绝 `candidate_policy.clearance_policy_sha256 != failing_decision.policy_sha256`
- 拒绝非 64-hex 的 `preflight_report_sha256`
- 拒绝 `camera_id` 不在 `plan.cameras` 中
- 拒绝 `topology.topology_ref != camera.topology_ref`
- 拒绝 `arc_length_m is None`（audit-overview 相机）
- 拒绝 `arc_length_m` 超出 `[0, topology.length_m]`

**几何门（6 道，§2）**：arc 范围、lateral half-width、scene extent、footprint
唯一、min spacing、ground-route spacing。

**输出**：
- `accepted_geometry_candidate: ReposeCandidate | None`（第一道通过几何门的候选）
- `search_sha256`：内容寻址 SHA，绑定 plan/registry/pose/preflight report/policy
  + 候选裁决序列

### 2. `build_reposed_plan` (production_repose.py)

**入口**：
```python
def build_reposed_plan(
    search: ReplacementPoseSearch,
    *,
    plan: ProductionCameraPlan,
) -> tuple[ProductionCameraPlan, str, str]:
    # 返回 (new_plan, new_plan_sha256, new_camera_registry_sha256)
```

**fail-closed 校验**：
- `search.accepted_geometry_candidate is not None`（否则没候选可重建）
- `accepted.predicted_plan_sha256 is not None`
- `accepted.predicted_camera_registry_sha256 is not None`
- `plan` 必须是传给 `search_replacement_pose` 的同一份（SHA 比对）
- 重建 plan 必须通过 pydantic re-validation（`model_validate_json` round-trip）
- 重建 plan SHA 必须 = `accepted.predicted_plan_sha256`
- 重建 registry SHA 必须 = `accepted.predicted_camera_registry_sha256`

**保证**：caller 拿到的 `new_plan` 已通过所有 plan validator，且 SHA 与 search
预测一致。caller 可以直接用 `new_plan` 跑 preflight / 实渲。

### 3. `production_render_id` 绑定键（production_journal.py）

**所有可选 SHA 绑定键（一律 `_require_64_hex_sha` fail-closed）**：

| 绑定键 | 何时传 | 何时为 None |
|---|---|---|
| `preflight_id` | 总是传（caller 已绑进 journal） | 永远不 None（生产档必须 preflight） |
| `quality_policy_sha256` | 总是传 | 永远不 None |
| `post_render_policy_sha256` | 进入 v2 post-render 阶段后传 | v1 阶段可 None |
| `repose_search_sha256` | reposed plan 的 render 必须传 | 非 reposed render 为 None |
| `environment_module_build_report_sha256` | 175-root scene 的 render 必须传 | 130-root scene 为 None |
| `build_adapter` | 总是传（`windows-textured-v2` 等） | — |

**关键不变量**：
- 所有绑定键为 None 时，render_id 与既有行为完全相同（既有 journal 不受影响）
- 任一绑定键变化（哪怕一位）都改变 render_id
- `repose_search_sha256` 与 `environment_module_build_report_sha256` 同时存在时，
  两者都进入 canonical identity，缺一不可

## §3 caller 必须满足的边界（来自 plan + FEEDBACK-CODEX-016）

1. **fresh preflight**：reposed plan 必须重新跑 `ProductionClearanceRequest` +
   Blender preflight，不能复用旧 preflight
2. **真实六层复渲**：preflight 通过后跑 RGB/depth/normal/instance/semantic/camera-metadata
3. **v2 post-render policy**：用 `ProductionFrameQualityRequestV2` + 候选 v2 八规则
4. **前后 RGB 对比**：必须产出 measured comparison，不接受"看起来一样"
5. **全部通过后才能替换 canonical 180-camera plan**

## §3 caller **不能**做的事

- **不能**从目录名 / build_id / engine 名推断 SHA —— 所有 SHA 必须是实测 64-hex
- **不能**把 130-root Task 4 evidence 冒充 175-root EnvironmentModulePlan 实渲证据
  （FEEDBACK-CODEX-016 line 47 明确）
- **不能**在 `production_render_id` 绑定键里传非 SHA 字符串 —— 现在所有可选
  绑定键都 fail-closed（commit `587b09a`）
- **不能**用 `req-5-pose-quality-fail-closed` 解锁来替代 §3 全链通过 ——
  该 req 只在 fresh 180-camera evidence + 实渲 + 接受的 replacement poses +
  Studio presentation + 全部测试通过后才能从 `_undelivered_requirements()` 移除

## Opus lane 已交付 commits（§3 合同部分）

| Commit | 内容 |
|---|---|
| `763f8d7` | `feat(camera): topology-aware replacement pose search` — §1+§2 |
| `fbdaa97` | `docs(handoff)` — Task 5 §1+§2 回执 |
| `e121036` | `feat(production-journal): bind environment_module_build_report_sha256 to render_id` |
| `587b09a` | `feat(production-journal): close fail-closed gap on remaining optional SHA bindings` |
| `bf0dd7b` | `docs(handoff): REVIEW-OPUS-001 fail-closed audit of apply_environment_modules.py` |

## 待 Codex 完成的 §3 caller 工作

按 `FEEDBACK-CODEX-016` line 49-54：

1. 将 `ProductionFrameQualityRequestV2/ReportV2` 接入正式 production runner
2. 持久化逐帧 canonical request/report，而不只在 journal 留身份
3. 接入 175-root environment-module build report SHA
4. 完成 Studio/ledger 的 post-render 状态与逐规则证据呈现

以上 4 项不撤销 Task 4 的分布审计结论，但仍是 §3 端到端验收条件。

## 协调点

- **不冲突路径**：Codex 当前 WIP 在 `production_render.py` /
  `local_production_runner.py` / `render_synthetic_village.py` /
  `scripts/synthetic_village.py` 及对应 tests。Opus lane 不触碰这些文件。
- **Opus 可独立推进**：`production_repose.py` / `production_journal.py` /
  `environment_module*.py` 的合同层已稳定；如 Codex 发现合同缺口，请在
  `handoff/REVIEW-CODEX-016-production-camera-repose.md` 记录，Opus 再修。
- **Studio / ledger**：完全在 Codex lane，Opus 不动。

## Co-Authored-By

GLM-5.2 <noreply@zai.com>
