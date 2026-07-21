# REVIEW-OPUS-006 — Phase 4.4 fail-closed 对抗性审计

> 日期：2026-07-21
> 发起：Opus lane (GLM-5.2 临时接替)
> 审计对象：`pipeline/synthetic_village/reciprocal_route_module.py` Phase 4.4 新增代码
> （commit `cad7508`：3 公开函数 + 2 FrozenModel + 4 常量）
> 方法：逐环节对抗性审计，模拟攻击者尝试绕过每个 fail-closed 门
> 结论：**1 MEDIUM fail-open 已修复 + TDD 锁定；1 LOW 已修复；其余 9 环节通过**

## 审计范围

Phase 4.4 (P0-2) 新增代码（`reciprocal_route_module.py` lines 653-1020）：

| 组件 | 行号 | 类型 |
|---|---|---|
| `ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M` | 665 | 常量 |
| `WalkableNodeBinding` | 668-706 | FrozenModel |
| `ReciprocalRoleCameraCandidate.bound_walkable_node` | 753 | 字段 |
| `ReciprocalRoleCameraCandidate._candidate_is_finite_and_standing_eye` | 774-791 | validator |
| `RECIPROCAL_ROLE_TARGET_GROUP_IDS` | 802-804 | frozenset 常量 |
| `materialize_reciprocal_role_candidate` | 807-874 | 公开函数 |
| `MIN_ROUTE_CLEARANCE_M` | 883 | 常量 |
| `REPLACEMENT_OBSTRUCTED_CAMERA_IDS` | 892-894 | frozenset 常量 |
| `RECIPROCAL_ROUTE_MODULE_ORDER` | 904-911 | tuple 常量 |
| `build_ground_route_replacement_candidate` | 914-1020 | 公开函数 |

## 审计环节（11 项）

### 环节 1 — WalkableNodeBinding schema 完整性 ✅ PASS

**审计点**：`FrozenModel`（`extra="forbid"` + `frozen=True` + `strict=True`）+ `node_id` pattern + `node_position_m` finite 校验 + `level` Literal。

**对抗性测试**：
- 注入未知字段 → `extra="forbid"` 拒绝 ✅
- 注入任意 `node_id`（如 `"CENTRAL_GROUND_EAST"`、`"central ground east"`）→ pattern `^[a-z0-9]+(?:-[a-z0-9]+)*$` 拒绝 ✅
- 注入 `node_id` 长度 0 或 65 → `min_length=1` / `max_length=64` 拒绝 ✅
- 注入 `node_position_m=[NaN, 0, 0]` → `math.isfinite` 拒绝 ✅
- 注入 `level="underground"` → `Literal["ground", "elevated"]` 拒绝 ✅

**结论**：schema 层 fail-closed 完整。

### 环节 2 — WalkableNodeBinding 死代码 LOW → 已记录

**审计点**：`_node_position_is_finite` validator 的 `len(self.node_position_m) != 3` 检查（line 704-705）。

**发现**：pydantic 的 `tuple[float, float, float]` 类型注解在解析时已强制 3 元组长度。如果 caller 传 `{"node_position_m": [1, 2]}`，pydantic 先抛 `ValidationError`，永远不会到达 `len != 3` 检查。

**严重性**：LOW — 死代码，不构成 fail-open（行为正确：错误长度被拒绝，只是拒绝发生在 pydantic 层而非 validator 层）。

**处理**：保留作为防御性双重检查（如果未来有人改 `node_position_m` 为 `tuple[float, ...]`，此检查仍会捕获）。不清理。

### 环节 3 — ReciprocalRoleCameraCandidate 距离校验 ✅ PASS

**审计点**：candidate `position_m` 到 `bound_walkable_node.node_position_m` 的 3D 距离 ≤ 30.0m。

**对抗性测试**：
- candidate `position_m` 远离 node 50m → 拒绝 ✅
- candidate `position_m` 在 node 上方 1.6m（replacement 场景）→ 距离 1.6m，通过 ✅
- `bound_walkable_node=None` → 跳过校验（additive，Phase 4.2 plan SHA 不变）✅

**结论**：距离门正确，不提升 trust。

### 环节 4 — materialize_reciprocal_role_candidate group 白名单 ✅ PASS

**审计点**：`audit-overview` 显式拒绝 + `RECIPROCAL_ROLE_TARGET_GROUP_IDS` 白名单。

**对抗性测试**：
- `target_group_id="audit-overview"` → `ReciprocalRouteError` ✅
- `target_group_id="unknown-group"` → `ReciprocalRouteError`（防御性，虽然 `CameraGroupId` Literal 应阻止）✅
- `target_group_id="ground-route"` → 通过 ✅

**结论**：group 门正确。

### 环节 5 — materialize 委托 _pose 的 camera_id / sequence_index 校验 ✅ PASS

**审计点**：helper 不重复校验 `target_camera_id` pattern 和 `target_sequence_index` 范围，而是委托给 `_pose` → `ProductionCameraPose` validator。

**对抗性测试**：
- `target_camera_id="camera-reciprocal-role-001"` → `ProductionCameraPose.camera_id` pattern 拒绝 ✅
- `target_sequence_index=0` / `181` / `-1` / `200` → `ProductionCameraPose.sequence_index` `le=180` / `ge=1` 拒绝 ✅

**结论**：分层校验正确，不重复但也不遗漏。

### 环节 6 — materialize audit_only 一致性 ✅ PASS

**审计点**：candidate 的 `audit_only: Literal[False] = False` 与物化 pose 的 `audit_only` 一致。

**分析**：`_pose` 计算 `audit_only=group_id == "audit-overview"`。由于 helper 已拒绝 audit-overview，`_pose` 的 `audit_only` 总是 False。与 candidate 的 `Literal[False]` 一致。

**结论**：无矛盾。

### 环节 7 — build_ground_route_replacement_candidate obstructed_camera_id 白名单 ✅ PASS

**审计点**：`REPLACEMENT_OBSTRUCTED_CAMERA_IDS` frozenset 锁定两台 obstructed id。

**对抗性测试**：
- `obstructed_camera_id="camera-ground-route-099"` → 拒绝 ✅
- `obstructed_camera_id="camera-ground-route-010"` / `"camera-ground-route-039"` → 通过 ✅

**结论**：白名单正确。

### 环节 8 — probe_clearance_min_m NaN/Inf fail-open MEDIUM → 已修复

**审计点**：`probe_clearance_min_m: float` 未校验 finite。

**发现（fail-open）**：
```python
# 修复前：
if probe_clearance_min_m < MIN_ROUTE_CLEARANCE_M:
    raise ReciprocalRouteError(...)
```

Python 比较语义：
- `float("nan") < 2.4` → `False`（NaN 通过门）
- `float("inf") < 2.4` → `False`（Inf 通过门）
- `float("-inf") < 2.4` → `True`（-Inf 被拒绝，但语义错误）

caller 可以传 `probe_clearance_min_m=float("nan")` 来绕过 clearance 门，构造一个无净空证据的 replacement candidate。这是真实的 fail-open。

**严重性**：MEDIUM — 需要 caller 主动传 NaN/Inf 才能触发，但一旦触发，candidate 会被构造出来。

**修复**（`reciprocal_route_module.py` lines 972-985）：
```python
# 修复后：
if not isinstance(probe_clearance_min_m, (int, float)) or not math.isfinite(
    probe_clearance_min_m,
):
    raise ReciprocalRouteError(
        f"probe_clearance_min_m={probe_clearance_min_m!r} must be a "
        f"finite real number; NaN/Inf silently bypass the clearance "
        f"gate and are rejected"
    )
```

**TDD 锁定**：
- `test_build_replacement_candidate_rejects_non_finite_clearance[nan]`
- `test_build_replacement_candidate_rejects_non_finite_clearance[inf]`
- `test_build_replacement_candidate_rejects_non_finite_clearance[-inf]`

**验证**：85 passed，ruff clean。

### 环节 9 — RECIPROCAL_ROUTE_MODULE_ORDER 与 plan 一致性 LOW → 已修复

**审计点**：`RECIPROCAL_ROUTE_MODULE_ORDER`（line 904）与 `ReciprocalRouteModulePlan._modules_are_exact_and_ordered` 的 `expected` tuple（line 1207）是两个独立硬编码 tuple，必须一致。

**发现**：两者目前一致，但没有 TDD 锁定。如果有人改了一个没改另一个，`build_ground_route_replacement_candidate` 会用错误的 role index，产生 `camera_id` 错误的 candidate（如 `camera-reciprocal-role-002` 实际对应 `bridge-deck-crossing` 但被映射到 `watermill-tailrace`）。

**严重性**：LOW — 目前一致无 bug，但缺乏锁定，未来修改可能漂移。

**修复**：
1. 把 `_RECIPROCAL_ROUTE_MODULE_ORDER`（私有）改为 `RECIPROCAL_ROUTE_MODULE_ORDER`（公开，可测试）。
2. 加 TDD `test_reciprocal_route_module_order_matches_plan_module_order`，验证两者完全一致。

**验证**：85 passed。

### 环节 10 — probe_clearance_min_m 真实来源未绑定 INFO

**审计点**：helper 接受任意 float 作为 `probe_clearance_min_m`，不绑定到 probe report SHA。

**分析**：caller 可以撒谎，传一个假的 2.5 而真实 probe 报告是 0.3。helper 无法验证 caller 传的值是否来自真实 probe report。

**严重性**：INFO — 这是 caller 责任，不是 helper 的 fail-open。helper 的契约是"如果 caller 提供了 probe clearance 值，验证它 ≥ 2.4"。验证值来自真实 probe report 是 caller chain 的责任（§3 caller 应同时提供 `probe_report_sha256`，下游可验证）。

**处理**：不修复。在回执中如实标注，提醒 Codex caller 在接入时应同时传 probe report SHA 以便下游验证。

### 环节 11 — 跨进程确定性 ✅ PASS

**审计点**：`materialize_reciprocal_role_candidate` 使用 `_pose`，`_pose` 使用 `_q3` 量化 + `_look_at_c2w` 确定性矩阵。

**验证**：
- TDD `test_materialize_reciprocal_role_candidate_computes_intrinsics_and_c2w_consistently` 已验证物化 pose 的 `intrinsics` + `c2w_opencv` 与手动调用 `_intrinsics` + `_look_at_c2w` 一致。
- TDD `test_materialize_reciprocal_role_candidate_quantizes_position_to_3_decimals` 已验证 `position_m` + `look_at_m` 量化到 3 位小数。

**结论**：跨进程字节确定。

## 修复汇总

| 级别 | 发现 | 修复 | TDD |
|---|---|---|---|
| MEDIUM | `probe_clearance_min_m` NaN/Inf fail-open | 加 `math.isfinite` 检查 | 3 parametrized tests |
| LOW | `RECIPROCAL_ROUTE_MODULE_ORDER` 无 TDD 锁定 | 公开常量 + 加一致性 TDD | 1 test |
| LOW | `WalkableNodeBinding._node_position_is_finite` 死代码 | 保留为防御性双重检查 | — |
| INFO | `probe_clearance_min_m` 真实来源未绑定 | 不修复（caller 责任） | — |

## 未修复项（设计决策）

- **`probe_clearance_min_m` 不绑定 probe report SHA**：helper 的契约是验证值 ≥ 2.4，不是验证值来自真实 report。后者是 caller chain 责任。Codex caller 接入时应同时传 `probe_report_sha256` 以便下游验证。
- **`materialize_reciprocal_role_candidate` 不校验 `target_camera_id` 与 `target_group_id` 一致性**：`ProductionCameraPose` 的 schema 只校验 camera_id pattern 和 group_id Literal，不校验两者一致性。这是 caller 责任——如果 caller 传 `ground-route` + `camera-elevated-pedestrian-001`，pose 会被构造出来但语义错误。不提升 trust，不是 fail-open。

## 测试覆盖

- Phase 4.4 新增 TDD：22 tests（commit `cad7508`）
- 审计修复新增 TDD：4 tests（本次）
  - `test_reciprocal_route_module_order_matches_plan_module_order`
  - `test_build_replacement_candidate_rejects_non_finite_clearance[nan]`
  - `test_build_replacement_candidate_rejects_non_finite_clearance[inf]`
  - `test_build_replacement_candidate_rejects_non_finite_clearance[-inf]`
- 总计：85 passed，ruff clean

## 提交内容（路径限定）

```text
pipeline/synthetic_village/reciprocal_route_module.py
tests/test_synthetic_village_reciprocal_route_module.py
handoff/REVIEW-OPUS-006-phase4.4-failclosed-audit.md
```

- `pipeline/synthetic_village/reciprocal_route_module.py`：
  - `build_ground_route_replacement_candidate` 加 `probe_clearance_min_m` finite 校验（MEDIUM 修复）
  - `_RECIPROCAL_ROUTE_MODULE_ORDER` → `RECIPROCAL_ROUTE_MODULE_ORDER`（公开，LOW 修复）
- `tests/test_synthetic_village_reciprocal_route_module.py`：
  - 4 个审计修复 TDD 测试
- `handoff/REVIEW-OPUS-006-phase4.4-failclosed-audit.md`：本审计文档
