# REVIEW-OPUS-010 — production_repose.py fail-closed 对抗性审计

> 日期：2026-07-22
> 发起：Opus lane (GLM-5.2 临时接替)
> 审计对象：`pipeline/synthetic_village/production_repose.py`（780 行）
> 对应：HANDOFF-OPUS-006 Task 5 §1+§2 + REVIEW-CODEX-014 P0 修复后的重写
> 方法：逐环节对抗性审计，模拟攻击者尝试绕过每个 fail-closed 门
> 结论：**全部通过，无 fail-open 漏洞；3 INFO 观察保留为设计决策**

## 审计背景

`production_repose.py` 经历了三阶段演进：
1. GLM 草案（REVIEW-CODEX-014 P0 拒绝）：`{010, 039}` 白名单 + 固定世界坐标偏移，不消费 failing_decision
2. 重写（FEEDBACK-HANDOFF-OPUS-006-phase2-task5）：删除白名单，改为 topology-aware deterministic arc-length search
3. 本审计：对抗性验证重写后的代码

## 审计范围

| 组件 | 行号 | 类型 |
|---|---|---|
| `ReposeCandidatePolicy` | 74-135 | frozen dataclass + `__post_init__` 校验 |
| `canonical_repose_candidate_policy_bytes` | 138-151 | canonical JSON |
| `ReposeCandidate` | 159-185 | frozen dataclass |
| `ReplacementPoseSearch` | 188-242 | frozen dataclass + `search_sha256` |
| `_decision_sha256` | 250-254 | canonical decision SHA |
| `_pose_sha256` | 257-273 | canonical pose SHA（仅 mutable-on-repose 字段） |
| `_point_at_arc_length` | 276-313 | polyline arc-length 投影 + fail-closed |
| `_build_predicted_plan` | 316-344 | plan 重建 + re-validation |
| `search_replacement_pose` | 352-637 | 公共 API（§1 校验 + §2 候选搜索 + 6 道 geometry gates） |
| `build_reposed_plan` | 640-779 | 公共 API（§3 plan 重建 + 双重 SHA 验证） |

## 审计环节（8 项）

### 环节 1 — §1 输入校验 fail-closed ✅ PASS

**4 道绑定校验**（lines 372-401）：

1. `failing_decision.passes` 为 True → 拒绝（不能为已通过 clearance 的相机搜索 replacement）✅
2. `failing_decision.camera_id != camera_id` → 拒绝 ✅
3. `failing_decision.policy_sha256 != candidate_policy.clearance_policy_sha256` → 拒绝（绑定到拒绝该相机的 clearance policy）✅
4. `preflight_report_sha256` 非 64-hex → 拒绝 ✅

**对抗性测试**：
- 传一个 passing decision → 拒绝 ✅
- 传一个不同 camera_id 的 failing decision → 拒绝 ✅
- 传一个不同 policy 的 failing decision → 拒绝 ✅
- 传一个随机 64-hex 作为 preflight_report_sha256 → 通过格式检查，但不验证绑定（INFO 2）

**结论**：failing decision 绑定完整，白名单已删除。

### 环节 2 — topology 绑定 + arc_length 校验 ✅ PASS

**验证**（lines 417-438）：
- `topology` 不是 `PolylineTopologySource` → 拒绝 ✅
- `topology.topology_ref != original_pose.topology_ref` → 拒绝 ✅
- `original_pose.arc_length_m is None` → 拒绝（audit-overview 不可重排）✅
- `arc_length_m` 超出 `[0, total_length]` → 拒绝 ✅

**结论**：topology 绑定完整，arc-overview 相机正确排除。

### 环节 3 — 6 道 geometry gates ✅ PASS

| Gate | 行号 | 检查 | 拒绝方式 |
|---|---|---|---|
| Gate 1 | 467-492 | arc_length 在 `[0, total_length]` 内 | failure_reasons + passes=False |
| Gate 2 | 498-506 | `require_within_half_width` 时 `\|lateral\| <= half_width_m` | failure_reasons |
| Gate 3 | 511-521 | position 在 scene extent 内（1m safety margin） | failure_reasons |
| Gate 4 | 550-554 | position 不与已有相机 collision | failure_reasons |
| Gate 5 | 557-565 | min spacing to other cameras ≥ min_spacing_to_other_cameras_m | failure_reasons |
| Gate 6 | 567-580 | ground-route 30m spacing 保留 | failure_reasons |

**对抗性测试**：
- Gate 1 失败时仍 emit candidate（zeroed pose fields）→ 完整序列 + 确定性 ✅
- Gate 6 使用 `zip(sorted_arc, sorted_arc[1:], strict=False)` → 正确（project_memory 记录的 `strict=True` bug 已修复）✅
- Gate 6 `break` 只记录第一个 violation → 不影响拒绝决策（任何 violation 即拒绝）✅

**结论**：6 道 geometry gates 完整，无遗漏。

### 环节 4 — `_point_at_arc_length` fail-closed ✅ PASS

**验证**（lines 276-313）：
- 累积弧长计算正确（`zip(points, points[1:], strict=False)`）✅
- `target = max(0.0, min(total, arc_length))` → clamp 到有效范围 ✅
- 不可达时 `raise ProductionProfileError`（不返回虚构 tangent）✅

**project_memory 对比**：记录的 "_point_at_arc_length must raise ProductionProfileError instead of using fictional direction data when invariants are bypassed" 已正确实现。

**结论**：无虚构方向数据。

### 环节 5 — `_build_predicted_plan` re-validation ✅ PASS

**验证**（lines 316-344）：
- `model_copy(update={"cameras": new_cameras})` → 不触发 validator（pydantic model_copy 设计）✅
- `ProductionCameraPlan.model_validate_json(canonical_production_plan_bytes(...))` → 强制 re-validate ✅
- 失败时返回 `None`（不发明 SHA）✅
- 成功时返回 `(plan_sha, registry_sha)` ✅

**对抗性测试**：
- 如果新 pose 导致 plan validator 拒绝（如 centre collision 在 validator 层面）→ `return None` → `failure_reasons.append("predicted plan rebuild failed")` → `passes=False` ✅

**结论**：plan 重建路径完整。

### 环节 6 — `build_reposed_plan` 双重 SHA 验证 ✅ PASS

**验证**（lines 640-779）：

1. `accepted is None` → 拒绝 ✅
2. `accepted.predicted_plan_sha256 is None` → 拒绝 ✅
3. `accepted.predicted_camera_registry_sha256 is None` → 拒绝 ✅
4. `actual_plan_sha != search.previous_plan_sha256` → 拒绝（caller 传了不同的 plan）✅
5. camera 不在 plan 中 → 拒绝 ✅
6. rebuilt plan `model_validate_json` → 强制 re-validate ✅
7. `plan_sha != accepted.predicted_plan_sha256` → 拒绝 ✅
8. `registry_sha != accepted.predicted_camera_registry_sha256` → 拒绝 ✅

**对抗性测试**：
- caller 传一个与 search 时不同的 plan → 第 4 道检查拒绝 ✅
- caller 手工构造 `accepted` with fake predicted SHA → 第 7/8 道检查拒绝（rebuilt SHA 不匹配 fake）✅
- caller 修改了 original_pose 的 eye_height_m/fov_x_deg → `model_copy` 只更新 position/look_at/arc_length/c2w，其它字段从 original_pose 继承。如果 original_pose 被修改，plan SHA 变化 → 第 4 道检查拒绝 ✅

**结论**：`build_reposed_plan` 双重 SHA 验证完整，无遗漏。

### 环节 7 — `ReposeCandidatePolicy` 校验 ✅ PASS

**验证**（lines 95-129）：
- `clearance_policy_sha256`：64-hex 校验 ✅
- `arc_length_offsets_m`：非空 ✅
- `lateral_offsets_m`：非空 ✅
- offset 元素：`math.isfinite()` → NaN/Inf 拒绝 ✅
- `min_spacing_to_other_cameras_m`：finite + > 0 ✅

**对抗性测试**：
- `arc_length_offsets_m=(NaN,)` → `not math.isfinite(value)` → 拒绝 ✅
- `min_spacing_to_other_cameras_m=0.0` → `<= 0.0` → 拒绝 ✅
- `min_spacing_to_other_cameras_m=inf` → `not math.isfinite(...)` → 拒绝 ✅

**结论**：policy 校验完整，NaN/Inf 全拒绝。

### 环节 8 — `search_sha256` 内容寻址 ✅ PASS

**验证**（lines 210-242）：
- 包含 `camera_id` ✅
- 包含 `failing_decision_sha256` ✅
- 包含 `preflight_report_sha256` ✅
- 包含 `candidate_policy_sha256` ✅
- 包含 `topology_ref` ✅
- 包含 `previous_plan_sha256` + `previous_camera_registry_sha256` + `previous_pose_sha256` ✅
- 包含每个 candidate 的 `arc_length_offset_m` + `lateral_offset_m` + `passes_geometry_gates` + `failure_reasons` ✅

**INFO 1**：`search_sha256` 不包含 accepted candidate 的 `position_m` / `look_at_m` / `c2w_opencv` / `predicted_plan_sha256`。这是可接受的：这些字段从 `arc_length_offset_m + lateral_offset_m + topology` 确定性派生，给定相同 inputs 结果必然相同。且 `build_reposed_plan` 会验证 `predicted_plan_sha256` 一致性。

**结论**：内容寻址完整。

## 设计决策保留（INFO）

1. **Gate 6 `break` 只记录第一个 spacing violation**：不记录所有 violation。不影响拒绝决策——任何 violation 即拒绝。只影响诊断信息量。设计可接受。

2. **`preflight_report_sha256` 不验证绑定**：函数只校验格式（64-hex），不打开 journal 验证 SHA 是否对应真实 preflight report。这是设计决策——docstring 明确说 "This function does NOT open the journal; it records the SHA into every emitted candidate so downstream verification can re-derive the chain. A malformed SHA is rejected; an unbound SHA is the caller's lie to catch later, not this function's."

3. **`search_sha256` 不含 accepted position/c2w**：这些字段从 offsets + topology 确定性派生，给定相同 inputs 必然相同。`build_reposed_plan` 会验证 `predicted_plan_sha256` 一致性，覆盖了 position/c2w 的间接验证。

## 修复汇总

| 级别 | 发现 | 修复 |
|---|---|---|
| — | 无 MEDIUM/HIGH fail-open | — |
| INFO | Gate 6 break 只记录第一个 violation | 不修复（不影响拒绝） |
| INFO | preflight_report_sha256 不验证绑定 | 不修复（caller 责任） |
| INFO | search_sha256 不含 accepted position/c2w | 不修复（确定性派生 + build_reposed_plan 覆盖） |

## 设计优点（值得保留）

1. **白名单已删除**：不再有 `{010, 039}` 硬编码，改为消费 failing_decision + topology-aware search
2. **topology-aware deterministic search**：沿 polyline arc-length 搜索，不使用世界坐标任意偏移
3. **6 道 geometry gates**：arc range / lateral corridor / scene extent / unique centre / min spacing / ground-route 30m
4. **predicted plan SHA + registry SHA 双重绑定**：`build_reposed_plan` 验证 rebuilt SHA 与 search 时预测的 SHA 一致
5. **`_point_at_arc_length` fail-closed**：不返回虚构 tangent
6. **`original_pose.arc_length_m or 0.0`**：在 `arc_length_m is None` 检查之后，`or 0.0` 不会在 None 情况下执行
7. **NaN/Inf 全拒绝**：offsets 和 min_spacing 都用 `math.isfinite()` 前置检查
8. **`build_reposed_plan` 验证 caller 传了同一个 plan**：`actual_plan_sha != search.previous_plan_sha256` 拒绝

## 测试覆盖

`tests/test_synthetic_village_production_repose.py` 已有 TDD 覆盖（FEEDBACK-HANDOFF-OPUS-006 12 测）。

## 提交内容

本审计不涉及代码修改（无 fail-open 漏洞），仅交付审计文档：

```text
handoff/REVIEW-OPUS-010-production-repose-failclosed-audit.md
```
