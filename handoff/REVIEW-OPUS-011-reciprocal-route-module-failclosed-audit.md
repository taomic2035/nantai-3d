# REVIEW-OPUS-011 — reciprocal_route_module.py fail-closed 对抗性审计

> 日期：2026-07-22
> 发起：Opus lane (GLM-5.2 临时接替)
> 审计对象：`pipeline/synthetic_village/reciprocal_route_module.py`（2024 行）
> 对应：HANDOFF-OPUS-009 Phase 2/4.2/4.3/4.4 交付（Opus 最近修改最频繁的文件）
> 方法：逐环节对抗性审计，模拟攻击者尝试绕过每个 fail-closed 门
> 结论：**全部通过，无 fail-open 漏洞；3 LOW 代码 smell + 3 INFO 设计观察**

## 审计范围

| 组件 | 行号 | 类型 |
|---|---|---|
| `FrozenModel` 基类 | 247-248 | extra=forbid + frozen + strict |
| 26 个 schema 类 | 262-1218 | 全部继承 FrozenModel |
| `WalkableNodeBinding` | 668-706 | Phase 4.2 新增 |
| `ReciprocalRoleCameraCandidate` | 709-792 | Phase 4.2 新增 + 3 道 validator |
| `materialize_reciprocal_role_candidate` | 807-874 | Phase 4.4 新增（candidate → ProductionCameraPose） |
| `build_ground_route_replacement_candidate` | 914-1028 | Phase 4.4 新增（010/039 replacement） |
| `RECIPROCAL_ROUTE_MODULE_ORDER` | 904-911 | 6 元素 tuple |
| `_canonical` / `plan_sha256` / `verify` | 251-1336 | canonical JSON + SHA + 4 重验证 |

## 审计环节（10 项）

### 环节 1 — FrozenModel + extra='forbid' ✅ PASS

三重锁定：`extra="forbid"` + `frozen=True` + `strict=True`。所有 26 个 schema 类继承 FrozenModel。攻击者无法注入字段、运行时篡改或类型强制。

### 环节 2 — trust 字段全部 Literal-locked ✅ PASS

14 个 trust 字段全部 Literal-locked：
- `synthetic=True`、`geometry_usability="preview-only"`、`verification_level="L0"`
- `metric_alignment=False`、`real_photo_textures=False`、`trust_effect="none"`
- `batch8/9_release_manifest_sha256` / `batch8/9_archive_sha256` 均绑定到常量
- `eye_height_m: Literal[1.6]`、`audit_only: Literal[False]`

### 环节 3 — WalkableNodeBinding schema ✅ PASS (1 LOW)

- `node_id`: pattern + min/max_length ✅
- `level: Literal["ground", "elevated"]` ✅
- `node_position_m`: validator `math.isfinite` 检查每个 axis ✅
- **LOW**: `node_position_m` 未设置 `allow_inf_nan=False`，依赖 validator 兜底。当前不构成 fail-open（validator 正确拒绝 NaN/Inf），但防御深度不足。

### 环节 4 — ReciprocalRoleCameraCandidate schema ✅ PASS (1 LOW)

3 道 validator：
1. `position_m` 每个 axis `math.isfinite` ✅
2. `look_at_m` 每个 axis `math.isfinite` ✅
3. `math.dist(position_m, look_at_m) >= 1.0`（防 degenerate c2w）✅
4. `bound_walkable_node` 非None时 `math.dist(position_m, node_position_m) <= 30.0` ✅

**NaN 与 math.dist 的潜在 fail-open 分析**：如果 position_m 或 node_position_m 包含 NaN，`math.dist` 返回 NaN，`NaN > 30.0` 返回 False，看似 fail-open。**但** validator 在 `math.dist` 之前已用 `math.isfinite` 检查 position_m，WalkableNodeBinding validator 也已检查 node_position_m。NaN 无法进入 `math.dist` 调用。**fail-closed**。

**LOW**: `position_m`/`look_at_m` 未设置 `allow_inf_nan=False`，依赖 validator 兜底。

### 环节 5 — materialize_reciprocal_role_candidate ✅ PASS

双重 group_id 检查：
1. `target_group_id == "audit-overview"` → 拒绝（防止 1.6m 人眼伪装 190m 鸟瞰）
2. `target_group_id not in RECIPROCAL_ROLE_TARGET_GROUP_IDS` → 拒绝

下游 `ProductionCameraPose` schema 验证：
- `camera_id` pattern 必须匹配 `group_id` prefix ✅
- `sequence_index` ge=1, le=180 ✅
- `audit_only` 与 `group_id` 一致性 ✅

### 环节 6 — build_ground_route_replacement_candidate ✅ PASS (1 LOW)

四重验证：
1. `obstructed_camera_id in REPLACEMENT_OBSTRUCTED_CAMERA_IDS` ✅
2. `probe_clearance_min_m` 显式 `isinstance + math.isfinite` 前置，NaN/Inf 拒绝 ✅
3. `role_module_id in RECIPROCAL_ROUTE_MODULE_ORDER` ✅
4. `bound_walkable_node.level == "ground"` ✅

**NaN/Inf 防护**（L974-987）：显式 `isinstance(probe_clearance_min_m, (int, float)) or not math.isfinite(...)` 在 `< MIN_ROUTE_CLEARANCE_M` 比较之前。注释明确说明 `nan < 2.4` 是 False 的攻击向量。**fail-closed 设计典范**。

**LOW**: `isinstance(True, (int, float))` 返回 True（bool 是 int 子类）。当前 `MIN_ROUTE_CLEARANCE_M=2.4 > 1.0`，bool 值（0/1）都会被 `< 2.4` 拒绝。如果未来 MIN_ROUTE_CLEARANCE_M 改小到 ≤1.0，`probe_clearance_min_m=True` (==1) 可能绕过。建议加 `and not isinstance(x, bool)`。

### 环节 7 — RECIPROCAL_ROUTE_MODULE_ORDER 一致性 ✅ PASS

三处定义完全一致：
1. `RECIPROCAL_ROUTE_MODULE_ORDER` 常量
2. `_modules_are_exact_and_ordered` validator 的 `expected` tuple
3. `build_default_reciprocal_route_module_plan` 的模块构造顺序

TDD `test_reciprocal_route_module_order_matches_plan_module_order` 锁定一致性。

### 环节 8 — canonical bytes + plan_sha256 + verify ✅ PASS

`_canonical`：`json.dumps(ensure_ascii=False, indent=2, sort_keys=True) + "\n"` → UTF-8 ✅

`verify_reciprocal_route_module_plan` 四重验证：
1. 重算 `scene_plan_sha256` 比对 ✅
2. 重算 `elevated_topology_sha256` 比对 ✅
3. 重算 `environment_module_plan_sha256` 比对 ✅
4. `model_validate_json(canonical_bytes(plan))` 重新验证 + `revalidated != plan` 比对 ✅

### 环节 9 — NaN/Inf 全字段检查 ✅ PASS

所有 float 字段都有防护：
- 16 个 spec 字段用 `allow_inf_nan=False` ✅
- `PartLayoutSpec.center_m/extent_m` 用 validator `math.isfinite` ✅
- `WalkableNodeBinding.node_position_m` 用 validator `math.isfinite` ✅
- `ReciprocalRoleCameraCandidate` 的 position/look_at/arc_length/fov 用 validator 或 `allow_inf_nan=False` ✅
- `probe_clearance_min_m` 显式 `isinstance + math.isfinite` ✅

### 环节 10 — bool 伪装 int 风险 ✅ PASS (同环节 6 LOW)

Pydantic strict 模式下所有 int 字段拒绝 bool ✅。函数参数 `probe_clearance_min_m` 的 isinstance 接受 bool，但当前 MIN_ROUTE_CLEARANCE_M=2.4 兜底。

## 设计决策保留（INFO）

1. **candidate_camera_ids 顺序未验证**：`_modules_are_exact_and_ordered` 检查 role_ids 顺序和 camera_ids 唯一，但不检查 camera_ids 顺序（001..006）。不影响 trust，但可能混淆 caller。INFO 级别。

2. **placeholder SHA 允许**：`build_default_reciprocal_route_module_plan` 在 `production_camera_plan=None` 时用 `"0"*64`。文档明确说明为单元测试设计，caller 应在发布前提供真实 SHA。INFO 级别。

3. **verify 不验证 candidate bound SHA**：`verify_reciprocal_route_module_plan` 不验证 `role_camera_candidates[*].bound_production_plan_sha256`。设计选择——verify 函数签名不接收 production_camera_plan，candidate SHA 绑定是 §3 caller chain 的职责。INFO 级别。

## 修复汇总

| 级别 | 数量 | 描述 |
|---|---|---|
| FAIL | 0 | 无实际 fail-open |
| MEDIUM | 0 | 无 |
| LOW | 3 | (1) node_position_m 缺 allow_inf_nan=False; (2) position_m/look_at_m 缺 allow_inf_nan=False; (3) probe_clearance_min_m isinstance 接受 bool（当前 MIN_ROUTE_CLEARANCE_M=2.4 兜底）|
| INFO | 3 | candidate_camera_ids 顺序 / placeholder SHA / verify 不验证 candidate bound SHA |

## 设计亮点

1. **probe_clearance_min_m NaN/Inf 防护**：显式 `isinstance + math.isfinite` 在 `<` 比较之前，注释明确说明 `nan < 2.4` 攻击向量。**fail-closed 设计典范**。
2. **materialize audit-overview 拒绝**：双重检查防止 1.6m 人眼伪装 190m 鸟瞰。
3. **14 个 trust 字段全 Literal-locked**：攻击者无法通过 JSON 篡改提升信任等级。
4. **FrozenModel 三锁定**：extra=forbid + frozen + strict 在所有 26 个 schema 类上统一应用。
5. **verify 四重验证**：三个外部 SHA 重算 + canonical bytes 重新 validate + `revalidated != plan` 比对。

## 测试覆盖

`tests/test_synthetic_village_reciprocal_route_module.py` 已有 81 TDD（Phase 4.2/4.3/4.4）+ 4 审计修复 TDD（REVIEW-OPUS-006）= 85 测试。

## 提交内容

本审计不涉及代码修改（无 fail-open 漏洞），仅交付审计文档：

```text
handoff/REVIEW-OPUS-011-reciprocal-route-module-failclosed-audit.md
```
