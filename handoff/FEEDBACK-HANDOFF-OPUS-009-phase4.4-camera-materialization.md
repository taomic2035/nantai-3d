# FEEDBACK-HANDOFF-OPUS-009 Phase 4.4 — Standing-eye camera materialization

> 日期：2026-07-21
> 发起：Opus lane (GLM-5.2 临时接替)
> 状态：Phase 4.4 三个子项已全部交付；P0-2 闭环
> 信任边界：所有产物仍为 `synthetic=true`、`verification_level=L0`、
> `geometry_trust=simplified-pbr-not-render-parity`、`trust_effect=none`

## 交付概览

Phase 4.4 (P0-2) 三个可独立推进子项全部完成：

1. **子项 1**：升级 `ReciprocalRoleCameraCandidate` 携带可选 `WalkableNodeBinding`
   canonical topology 引用。
2. **子项 2**：添加 `materialize_reciprocal_role_candidate` helper，把 candidate
   物化为 `ProductionCameraPose`（intrinsics + c2w_opencv）。
3. **子项 3**：添加 `build_ground_route_replacement_candidate`，为
   `camera-ground-route-010` / `camera-ground-route-039` 设计 replacement candidate。

测试：81 passed（含 22 个 Phase 4.4 新 TDD），ruff clean，邻近 suite 257 passed
无回归。

## 子项 1 — WalkableNodeBinding schema

### 设计

在 `ReciprocalRoleCameraCandidate` 添加可选字段
`bound_walkable_node: WalkableNodeBinding | None = None`：

- `WalkableNodeBinding` 是 `FrozenModel`（`extra="forbid"` + `frozen=True` +
  `strict=True`），携带：
  - `node_id: str`（pattern `^[a-z0-9]+(?:-[a-z0-9]+)*$`，1-64 字符）
  - `node_position_m: tuple[float, float, float]`（finite 校验）
  - `level: Literal["ground", "elevated"]`
- 字段是 additive 的：默认 `None` 时 Phase 4.2 plan SHA 不变（已由 TDD
  `test_candidate_defaults_to_no_walkable_node_binding` 锁定）。
- candidate validator 新增距离校验：candidate `position_m` 到
  `bound_walkable_node.node_position_m` 的 3D 距离必须 ≤
  `ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M = 30.0 m`
  （= `ROLE_CAMERA_LOOKAHEAD_M + 5.0`）。
- 这是 geometry gate，不是 trust gate：它验证 candidate 在 bound node 附近，
  但**绝不**把 `modeled-unverified` 几何提升为 `measured`/`metric`/`aligned`。

### TDD 覆盖（9 tests）

1. `test_walkable_node_binding_accepts_valid_node`
2. `test_walkable_node_binding_rejects_invalid_node_id_pattern`
3. `test_walkable_node_binding_rejects_non_finite_position`
4. `test_walkable_node_binding_rejects_unknown_level`
5. `test_candidate_defaults_to_no_walkable_node_binding`（Phase 4.2 plan SHA 不变）
6. `test_candidate_accepts_walkable_node_binding_within_distance`
7. `test_candidate_rejects_walkable_node_beyond_max_distance`
8. `test_plan_sha_changes_when_walkable_node_binding_changes`（tamper detection）
9. `test_walkable_node_max_distance_constant_is_locked`（防 30.0 漂移）

## 子项 2 — materialize_reciprocal_role_candidate helper

### 设计

新增公开函数 `materialize_reciprocal_role_candidate`，把 candidate 物化为
`ProductionCameraPose`：

- 复用 `production_profile._pose` 私有 helper（与 180-camera plan 相同的
  `_look_at_c2w` + `_intrinsics` + `_q3` 量化），保证物化出的 pose 与
  180-camera plan 对相同 placement 产出的 pose **字节一致**。
- candidate 的 `camera_id`（`RoleCameraId` Literal，`camera-reciprocal-role-001`..`006`）
  **不**传递给 production pose，因为 `ProductionCameraPose.camera_id` 的 regex
  拒绝 `reciprocal-role` 前缀。caller 必须提供 `target_camera_id`（匹配
  `^camera-(?:ground-route|elevated-pedestrian|perimeter-inward|environment-corridor|audit-overview)-[0-9]{3}$`）
  和 `target_sequence_index`（`[1, 180]`）。
- `target_group_id == "audit-overview"` 被**拒绝**：1.6 m standing-eye candidate
  不能物化为 ~190 m aerial overview（`ReciprocalRouteError`）。
- candidate 的 `audit_only: Literal[False] = False` 被原样尊重：物化出的 pose
  `audit_only=False`（`_pose` 对非 audit-overview group 已如此行为）。
- `RECIPROCAL_ROLE_TARGET_GROUP_IDS` frozenset 锁定四个合法目标 group：
  `{"ground-route", "elevated-pedestrian", "perimeter-inward", "environment-corridor"}`。

### TDD 覆盖（8 tests + 1 constant lock）

1. `test_reciprocal_role_target_group_ids_constant_is_locked`
2. `test_materialize_reciprocal_role_candidate_produces_valid_ground_route_pose`
3. `test_materialize_reciprocal_role_candidate_accepts_all_non_audit_groups`（parametrized ×3）
4. `test_materialize_reciprocal_role_candidate_rejects_audit_overview_group`
5. `test_materialize_reciprocal_role_candidate_rejects_invalid_target_camera_id_pattern`
6. `test_materialize_reciprocal_role_candidate_rejects_sequence_index_out_of_range`（parametrized ×4: 0, 181, -1, 200）
7. `test_materialize_reciprocal_role_candidate_quantizes_position_to_3_decimals`
8. `test_materialize_reciprocal_role_candidate_computes_intrinsics_and_c2w_consistently`

## 子项 3 — build_ground_route_replacement_candidate

### 设计

新增公开函数 `build_ground_route_replacement_candidate`，为
`camera-ground-route-010` / `camera-ground-route-039`（REVIEW-CODEX-011 拒绝的两台
近表面遮挡相机）设计 replacement candidate：

- `REPLACEMENT_OBSTRUCTED_CAMERA_IDS` frozenset 锁定两台可替换的 obstructed id。
- `MIN_ROUTE_CLEARANCE_M = 2.4`（与 probe script 阈值一致）。
- caller 必须提供 `probe_clearance_min_m`（Phase 4.3 probe 实测值）；
  `probe_clearance_min_m < 2.4` 被拒绝（fail-closed：无净空证据 → 不放 candidate）。
- caller 必须提供 `bound_walkable_node: WalkableNodeBinding`（ground-level）；
  elevated node 被拒绝（ground-route replacement 要求 ground-level node）。
- candidate 的 `position_m` = `node_position_m + (0, 0, 1.6)`（standing-eye height）。
- candidate 的 `camera_id` 复用 role 的 `RoleCameraId`（按
  `_RECIPROCAL_ROUTE_MODULE_ORDER` 映射 module → 1-based role index）。
  replacement candidate 是**standalone**的，**不**进入 plan 的
  `role_camera_candidates` tuple，所以复用 role camera_id 安全。
- 物化时，caller 用 `materialize_reciprocal_role_candidate(candidate,
  target_group_id="ground-route", target_sequence_index=10,
  target_camera_id="camera-ground-route-010")` 把 candidate 物化为
  替换 obstructed pose 的 `ProductionCameraPose`。

### TDD 覆盖（7 tests + 2 constant locks）

1. `test_replacement_obstructed_camera_ids_constant_is_locked`
2. `test_min_route_clearance_m_constant_is_locked`
3. `test_build_replacement_candidate_produces_valid_candidate`
4. `test_build_replacement_candidate_accepts_both_obstructed_ids`（parametrized ×2）
5. `test_build_replacement_candidate_rejects_unknown_obstructed_camera_id`
6. `test_build_replacement_candidate_rejects_insufficient_clearance`
7. `test_build_replacement_candidate_rejects_elevated_walkable_node`
8. `test_build_replacement_candidate_can_be_materialized_to_target_pose`（端到端：build → materialize → ProductionCameraPose）

## 未触碰

- Codex WIP 文件：`local_production_runner.py`、`studio_server.py`、
  `ktx2_toolchain.py`、`test_ktx2_toolchain.py`、`web/data/`、
  `scripts/synthetic_village.py`、`production_render.py`、
  `render_synthetic_village.py`、`production_quality_gates.py`。
- v1 plan / build report、registry、Release。
- `reciprocal_route_probe.py` / `reciprocal_route_probe_runner.py`
  （Phase 4 probe 已在 c2a851a 闭环）。
- `production_repose.py`（HANDOFF-OPUS-006 Task 5 已交付的 replacement 机制）。
- `production_profile.py` 的 `_pose` / `ProductionCameraPose` schema（只读复用）。
- `elevated_topology.py` 的 `WalkableNode` / `WalkableEdge` schema（只读复用）。
- `camera_plan.py` 的 `_look_at_c2w` / `_intrinsics` / `_q3`（只读复用）。

## 边界与诚实标注

- **replacement candidate 仍是 `modeled-unverified`**：probe clearance 通过
  （≥ 2.4 m）只证明 passage 几何可读，**不**证明 post-render/training 通过。
  Acceptance 仍需 fresh preflight + 六层 render + post-render policy
  （HANDOFF-CODEX-011 P1-3，Codex caller 负责）。
- **`build_ground_route_replacement_candidate` 不选择 module**：caller 必须显式
  提供 `role_module_id` + `topology_ref` + `look_at_m` + `bound_walkable_node`。
  本函数不从一个 obstructed_camera_id 推断该用哪个 module——那会从名字推断
  几何适用性，违反 provenance safety。
- **物化 helper 不重新计算 plan SHA**：`materialize_reciprocal_role_candidate`
  只构造单个 `ProductionCameraPose`，不构造完整 `ProductionCameraPlan`。
  plan SHA + camera registry SHA 的重新计算是 §3 caller 的责任。
- **`_pose` 跨模块私有导入**：`reciprocal_route_module.py` 从 `production_profile.py`
  导入 `_pose`（私有函数）。这是本仓库的既有模式（`production_profile.py` 已从
  `camera_plan.py` 导入 `_q3` / `_look_at_c2w` / `_intrinsics` / `_scene_digest`
  私有函数），在此上下文中可接受。

## Codex 后续 caller 接入清单

1. **caller 选择 module + node**：对 `camera-ground-route-010`，根据其原始
   `topology_ref` 选择最接近的 reciprocal-route module（如
   `bridge-deck-crossing`）和该 module 附近的 ground-level `WalkableNode`。
2. **caller 计算 look_at_m**：从 module 的 route 方向（`_DEFAULT_ROLE_CAMERA_PLACEMENT`
   的 look_at 方向）+ 25 m lookahead 计算 `look_at_m`。
3. **caller 读取 probe clearance**：从 Phase 4.3 probe report 读取该 module 的
   `clearance_min_m`，作为 `probe_clearance_min_m` 传入。
4. **caller 构造 + 物化**：
   ```python
   candidate = build_ground_route_replacement_candidate(
       obstructed_camera_id="camera-ground-route-010",
       role_module_id="bridge-deck-crossing",
       topology_ref="path-network-001",
       bound_walkable_node=binding,
       look_at_m=look_at,
       bound_production_plan_sha256=plan_sha,
       bound_camera_registry_sha256=registry_sha,
       probe_clearance_min_m=probe_clearance,
       disclosure="...",
   )
   pose = materialize_reciprocal_role_candidate(
       candidate,
       target_group_id="ground-route",
       target_sequence_index=10,
       target_camera_id="camera-ground-route-010",
   )
   ```
5. **caller 替换 + 验证**：用物化出的 pose 替换 180-camera plan 中的 obstructed
   pose，重算 plan SHA + registry SHA，跑 fresh preflight + 六层 + post-render v2。

## 提交内容（路径限定）

```text
pipeline/synthetic_village/reciprocal_route_module.py
tests/test_synthetic_village_reciprocal_route_module.py
handoff/FEEDBACK-HANDOFF-OPUS-009-phase4.4-camera-materialization.md
```

- `pipeline/synthetic_village/reciprocal_route_module.py`：
  - 新增 `WalkableNodeBinding` FrozenModel（子项 1）
  - `ReciprocalRoleCameraCandidate` 新增 `bound_walkable_node` 字段 + 距离校验（子项 1）
  - 新增 `RECIPROCAL_ROLE_TARGET_GROUP_IDS` + `materialize_reciprocal_role_candidate`（子项 2）
  - 新增 `MIN_ROUTE_CLEARANCE_M` + `REPLACEMENT_OBSTRUCTED_CAMERA_IDS` +
    `_RECIPROCAL_ROUTE_MODULE_ORDER` + `build_ground_route_replacement_candidate`（子项 3）
- `tests/test_synthetic_village_reciprocal_route_module.py`：
  - 22 个 Phase 4.4 新 TDD 测试（9 子项 1 + 8+1 子项 2 + 7+2 子项 3）
- `handoff/FEEDBACK-HANDOFF-OPUS-009-phase4.4-camera-materialization.md`：本回执
