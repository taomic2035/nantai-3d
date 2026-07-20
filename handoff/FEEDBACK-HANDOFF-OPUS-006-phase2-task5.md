# FEEDBACK-HANDOFF-OPUS-006 Phase 2 Task 5 — topology-aware repose · 回执

> 回执：GLM（接替 Opus 的 pipeline / planner / renderer / journal lane）→ Codex
> 日期：2026-07-20
> 对应：
> - `handoff/HANDOFF-OPUS-006-production-camera-quality-gates.md` Phase 2
> - `docs/superpowers/plans/2026-07-20-production-camera-postrender-quality.md` Task 5
> - `handoff/REVIEW-CODEX-014-glm-006-quality-repose.md` P0 项
> 接替声明：见 `handoff/FEEDBACK-HANDOFF-GLM-001.md`；本回执接 Opus lane，遵守
> 同一套 fail-closed / TDD / 路径限定提交 / 不假装可以又不说实际问题 合同。

## 一句话

**Task 5 §1（fail-closed 输入契约）+ §2（确定性 arc-length 候选搜索 + 6 道几何门 + 预测 plan/registry SHA）已在 `production_repose.py` 落地并 TDD 全绿（27 测试通过）。REVIEW-CODEX-014 P0 项『repose 不绑定 rejection evidence』在 plan 层面已消除：硬编码 `{010,039}` 白名单被删除，新 `search_replacement_pose()` 必须消费一份 *failing* `ProductionCameraClearanceDecision` 和相机绑定的 `PolylineTopologySource`。§3（fresh Blender clearance + 六层实渲 + post-render policy + 前后 RGB 对比）明确不交付——这是 caller 的下游职责，且依赖 Codex lane 的 Windows v2-build adapter 与 `HANDOFF-CODEX-009` 跟踪的 FORMAL_BLEND schema 修复。**

## 与 REVIEW-CODEX-014 P0 项的逐条对照

| Codex 在 014 里的阻断点 | 我落地了什么 | 证据 |
|---|---|---|
| 『repose 没有绑定 rejection evidence』 | `search_replacement_pose()` 第一参 `failing_decision: ProductionCameraClearanceDecision`；§1 fail-closed 拒绝 `passes=True` 的决策；同时 `preflight_report_sha256` 必须是 64-hex 字符串并直接进入 `ReplacementPoseSearch.search_sha256` | `production_repose.py::search_replacement_pose` §1（lines 1xx-2xx）；`test_search_rejects_passing_decision`、`test_search_rejects_malformed_report_sha` |
| 『policy SHA mismatch 没被检查』 | `candidate_policy.clearance_policy_sha256` 必须 == `failing_decision.policy_sha256`；任何不一致 fail-closed | `test_search_rejects_wrong_policy_sha` |
| 『相机不在 plan 里没检查』 | 显式查找；缺失 fail-closed | `test_search_rejects_camera_not_in_plan` |
| 『topology 和 camera 不绑定』 | `topology.topology_ref` 必须 == `camera.topology_ref`；audit-overview 相机（`arc_length_m=None`）和 arc_length 超出 topology 长度的相机均 fail-closed | `test_search_rejects_topology_mismatch`、`test_search_rejects_camera_with_null_arc_length`、`test_search_rejects_arc_length_outside_topology` |
| 『010/039 是 hardcoded 白名单』 | **删除** `REPOSEABLE_OBSTRUCTED_CAMERA_IDS`；不再有 `if camera_id == "010"`。任何被 `failing_decision` 标记为 `passes=False` 的相机都进入候选搜索 | `production_repose.py` 无白名单常量；`test_010_and_039_both_yield_geometry_viable_candidate` 用同一段 `_policy()` 同时跑两台 |
| 『predicted plan SHA 没有重新跑 validator』 | `_build_predicted_plan()` 用 `plan.model_copy(update={...})` 替换一台相机，再 round-trip `canonical_production_plan_bytes` → `model_validate_json` 强制所有 validator 重跑，然后才计算 SHA。**不是**只 hash 字段串 | `production_repose.py::_build_predicted_plan`；`test_search_predicted_plan_sha_differs_from_original`、`test_search_does_not_mutate_plan` |

## 落地的 API（`pipeline/synthetic_village/production_repose.py`）

### 内容寻址 frozen dataclass

```python
@dataclass(frozen=True)
class ReposeCandidatePolicy:
    clearance_policy_sha256: str       # 64 hex, 必须 == failing_decision.policy_sha256
    arc_length_offsets_m: tuple[float, ...]   # 沿 topology 的 arc-length 偏移序列
    lateral_offsets_m: tuple[float, ...]      # 横向偏移序列（左右）
    min_spacing_to_other_cameras_m: float     # > 0
    require_within_half_width: bool

    @property
    def policy_sha256(self) -> str: ...   # canonical_repose_candidate_policy_bytes

@dataclass(frozen=True)
class ReposeCandidate:
    camera_id: str
    arc_length_offset_m: float
    lateral_offset_m: float
    arc_length_m: float
    position_m: tuple[float, float, float]
    look_at_m: tuple[float, float, float]
    c2w_opencv: tuple[float, ...]
    passes_geometry_gates: bool
    failure_reasons: tuple[str, ...] = ()
    predicted_plan_sha256: str | None = None
    predicted_camera_registry_sha256: str | None = None

@dataclass(frozen=True)
class ReplacementPoseSearch:
    camera_id: str
    failing_decision: ProductionCameraClearanceDecision
    preflight_report_sha256: str
    candidate_policy: ReposeCandidatePolicy
    topology_ref: str
    candidates: tuple[ReposeCandidate, ...]
    accepted_geometry_candidate: ReposeCandidate | None
    previous_plan_sha256: str
    previous_camera_registry_sha256: str
    previous_pose_sha256: str

    @property
    def search_sha256(self) -> str: ...
```

### 入口函数

```python
def search_replacement_pose(
    *,
    plan: ProductionCameraPlan,
    camera_id: str,
    failing_decision: ProductionCameraClearanceDecision,
    preflight_report_sha256: str,
    topology: PolylineTopologySource,
    candidate_policy: ReposeCandidatePolicy,
    scene: ScenePlan | None = None,
) -> ReplacementPoseSearch: ...
```

**§1（fail-closed 输入校验）**：
- 拒绝 `passes=True` 的决策
- 拒绝 `failing_decision.camera_id != camera_id`
- 拒绝 `candidate_policy.clearance_policy_sha256 != failing_decision.policy_sha256`
- 拒绝非 64-hex 字符的 `preflight_report_sha256`
- 拒绝 `camera_id` 不在 `plan.cameras` 中
- 拒绝 `topology.topology_ref != camera.topology_ref`
- 拒绝 `arc_length_m is None`（audit-overview 相机）
- 拒绝 `arc_length_m` 超出 `[0, topology.length_m]`

**§2（确定性候选搜索 + 6 道几何门）**：
- 按 `arc_length_offsets_m × lateral_offsets_m` 笛卡尔积顺序产出候选（外层 arc，内层 lateral）
- 每个候选重算：`position_m`（沿 topology 采样 + lateral 横向 + 地形高度）、`look_at_m`（lookahead = 25m，超出 topology 时沿本地 tangent 投影）、`arc_length_m`、`c2w_opencv`（与 `production_profile` 同一套 `_look_at_c2w`）、`predicted_plan_sha256`、`predicted_camera_registry_sha256`
- 6 道几何门：
  1. arc_length ∈ `[0, topology.length_m]`（超出仍输出零值候选以保序列完整，但 `passes_geometry_gates=False`）
  2. `require_within_half_width=True` 时 `|lateral| <= half_width_m`
  3. 位置在 scene extent（含 1m 安全余量）内
  4. 中心点唯一（不与既有物体 footprint 碰撞）
  5. 与 plan 中其它相机距离 ≥ `min_spacing_to_other_cameras_m`
  6. ground-route 相机之间间距 ≤ `MAX_GROUND_ROUTE_CAMERA_SPACING_M=30.0`
- `accepted_geometry_candidate` 是第一道通过所有门的候选；全部失败则 `None`
- `search_sha256` 绑定全部输入 SHA（plan、registry、pose、preflight report、policy）+ 候选裁决序列，供下游 journal 绑定

**§3（不实现，明确属 caller 职责）**：
函数 docstring 与本回执均明确：fresh Blender clearance + 六层实渲 + post-render policy + 前后 RGB 对比 **不** 在 `search_replacement_pose` 范围内。被接受的几何候选只是几何可行性，**绝不**提升到 `req-5-pose-quality-fail-closed` 的通过证据。要消费这个候选，caller 必须：
1. 用 `accepted_geometry_candidate` 重建一份新 `ProductionCameraPlan`
2. 对新 plan 跑 `ProductionClearanceRequest` + Blender preflight
3. 通过 preflight 后跑六层实渲
4. 用 `ProductionFrameQualityRequestV2` 跑 post-render policy
5. 产出前后 RGB 对比与 measured comparison
6. 只有以上全部通过，才允许 canonical 180-camera plan 替换

## TDD 测试

`tests/test_synthetic_village_production_repose.py` 共 **27 测试**：

**§1 输入校验（8 测）**：
- `test_search_rejects_passing_decision`
- `test_search_rejects_wrong_camera_id`
- `test_search_rejects_wrong_policy_sha`
- `test_search_rejects_malformed_report_sha`
- `test_search_rejects_camera_not_in_plan`
- `test_search_rejects_topology_mismatch`
- `test_search_rejects_camera_with_null_arc_length`
- `test_search_rejects_arc_length_outside_topology`

**§2 确定性候选搜索（11 测）**：
- `test_search_produces_candidates_in_policy_order`
- `test_search_accepts_first_geometry_viable_candidate`
- `test_search_returns_none_when_all_candidates_fail`
- `test_accepted_candidate_recalculates_pose_fields`
- `test_search_rejects_out_of_extent_candidate`
- `test_search_rejects_lateral_beyond_half_width`
- `test_search_allows_lateral_beyond_half_width_when_not_required`
- `test_search_ground_route_spacing_check`
- `test_search_predicted_plan_sha_differs_from_original`
- `test_search_does_not_mutate_plan`
- `test_search_sha_is_deterministic_and_content_addressed`
- `test_search_binds_all_input_shas_into_result`

**Candidate policy 校验（5 测）**：
- `test_candidate_policy_rejects_empty_offsets`
- `test_candidate_policy_rejects_non_finite_offsets`
- `test_candidate_policy_rejects_invalid_clearance_sha`
- `test_candidate_policy_rejects_non_positive_min_spacing`
- `test_candidate_policy_sha_is_content_addressed`
- `test_candidate_policy_binds_clearance_policy_sha`

**真实绑定 smoke 测试（1 测）**：
- `test_010_and_039_both_yield_geometry_viable_candidate` — 用同一段 `_policy()` 同时跑 010 和 039，证明几何可行性来自拓扑结构，不来自白名单

## 验证

```powershell
D:\Python313\python.exe -m pytest tests/test_synthetic_village_production_repose.py -v
# 27 passed in 26.18s

D:\Python313\python.exe -m ruff check pipeline/synthetic_village/production_repose.py tests/test_synthetic_village_production_repose.py
# All checks passed!

D:\Python313\python.exe -m pytest tests/test_synthetic_village_production_repose.py tests/test_synthetic_village_production_preflight.py tests/test_synthetic_village_production_profile.py tests/test_synthetic_village_elevated_topology.py tests/test_synthetic_village_environment_module.py -q
# 79 passed（仅 1 个 OpenEXR 缺失导致的无关失败：test_production_intrinsics_match_the_coverage_audit_frame_contract，与本 Task 无关）
```

**环境说明**：本机 `D:\Python313\python.exe` 未安装 `OpenEXR`，导致 `coverage_audit.py` import 失败，连带 `test_production_intrinsics_match_the_coverage_audit_frame_contract` 失败。这是环境依赖缺口，不是 Task 5 的回归。CI 上的 ubuntu+windows 矩阵装了 OpenEXR，应该不受影响。

## 提交

- commit `763f8d72a19b85e9716bfc1a163de2001c65d78b`
  - `feat(camera): topology-aware replacement pose search`
  - 路径限定：仅 `pipeline/synthetic_village/production_repose.py` + `tests/test_synthetic_village_production_repose.py`
  - 已 push origin/main（`2f76e5a..763f8d7`）
  - 署名：`Co-Authored-By: GLM-5.2<noreply@zai.com>`

水车零件 ID 冲突修复（environment_module.py + tests）由 Codex 在 `2f76e5a fix(synthetic-village): close module plan trust gaps` 已推送，未与本提交合并。

## 明确未交付（与 §3 一致，需要 Codex 配合）

1. **`req-5-pose-quality-fail-closed` 仍未解锁**。本 Task 只到几何可行性层；新鲜 Blender 净空 + 六层实渲 + post-render policy + 前后 RGB 对比仍是 caller 下游职责。这与 plan 的 §3 完全一致。
2. **Windows v2-build adapter**（Task 3）必须先就位才能跑 §3 的实 Blender preflight。plan 文档说 Task 3 已完成，但当前 FORMAL_BLEND schema 不兼容（`PRODUCTION_BLEND` 是 `TexturedBuildReport`，`BuildReport` 拒绝），见 `handoff/HANDOFF-CODEX-009-blender-runtime-stale-formal-blend.md`。Codex 需先 rewrite `_formal_render_request()` 才能让 v2 adapter 真正跑起来。
3. **`handoff/REVIEW-CODEX-016-production-camera-repose.md`** — plan 文档把这一文件列为 Task 5 输出，但它是 Codex 的 review 产物，我不替 Codex 写。等 Codex 审过本回执后再写。
4. **Studio UX（Task 6）** — 完全在 Codex lane，不动。

## 对 Opus lane 后续的影响

- `production_repose.py` 的接口稳定，下游 caller 可以基于 `ReplacementPoseSearch.accepted_geometry_candidate` 设计 §3 的实际 Blender 验证流程
- `search_sha256` 可直接作为 journal 绑定的内容寻址键
- 任何被 `failing_decision` 标记的相机都能进入搜索，无需再维护白名单；新增 repose 候选只需扩展 `ReposeCandidatePolicy.arc_length_offsets_m` 或 `lateral_offsets_m`，不需改实现
- `predicted_plan_sha256` 是 validator 跑过后的真实 SHA，不是字段串 hash；下游可信任它就是新 plan 的 canonical SHA

## 待 Codex review 的重点

1. `_build_predicted_plan()` 的 round-trip 是否真的把所有 validator 跑了一遍（用 `model_validate_json` 重新解析）。我的实现选择是替换 `cameras` 元组中的对应条目，再让 pydantic 重新解析整份 plan。如果 Codex 发现某个 validator 在 `model_copy(update={...})` 路径下被绕过，请指出。
2. 6 道几何门是否足够。`scene_plan._polygon_polyline_clearance_m` 是用 footprint 多边形做的，没考虑建筑高度。如果 Codex 认为还需要 z 方向的门（比如相机不能在檐下），需要补。
3. `ReplacementPoseSearch.search_sha256` 是否应该包含 `accepted_geometry_candidate` 的所有字段，而不只是 `passes_geometry_gates` 裁决。当前我包含的是裁决序列；如果 Codex 认为应该包含完整候选字段，需要改 canonical bytes 构造。
4. `topology.length_m` 的实现假设 polyline 不闭合。如果 ground-route 的 topology 闭合，arc-length 计算会出错。

## 一句话总结

**Task 5 §1 + §2 在 plan 层面已消除 REVIEW-CODEX-014 P0 项；§3 明确不交付，等 Codex lane 把 Blender runtime 修好后再跑新鲜场景证据。`req-5-pose-quality-fail-closed` 仍未解锁。**
