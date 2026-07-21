# FEEDBACK-HANDOFF-OPUS-009 Phase 4.2 — Standing-eye role camera candidate schema

> 日期：2026-07-21
> 发起：Opus → Codex
> 状态：已交付，路径限定提交；schema 层为 HANDOFF-CODEX-011 P0-2 的前置基础
> 信任边界：所有产物仍为 `synthetic=true`、`verification_level=L0`、
> `geometry_trust=simplified-pbr-not-render-parity`、`trust_effect=none`

## 结论

Phase 4.2 plan-layer schema 已交付并 TDD 锁定。`ReciprocalRouteModulePlan`
现在携带六个 standing-eye role camera candidate，每个候选绑定
`topology_ref` + `bound_production_plan_sha256` + `bound_camera_registry_sha256`
三件套，使下游 §3 caller chain（`run_reciprocal_production_camera`）能将
渲染产物溯源到本候选。

**本层是 candidate schema，不是真实 standing-eye camera。** HANDOFF-CODEX-011
P0-2 要求的"正式 standing-eye `ground-route` 相机，绑定 canonical topology
edge/node 与 route progress"需要：

1. Phase 4 item 2 的 mesh/collision probe 报告（先用 fresh build
   `509919f245932dacd950b7bb95c16638983c4da028ecced5361e3c9da2358a4e/`
   排除近墙、穿模、悬空、错误 attachment）；
2. 把候选的 `topology_ref`（当前绑定 path network）升级为 canonical
   topology edge/node 引用；
3. caller 把候选物化为完整 `ProductionCameraPose`（计算
   `intrinsics` + `c2w_opencv`）；
4. 处理已知坏位姿 `ground-route-010` / `ground-route-039` 的 replacement。

candidate schema 是上述升级的稳定输入：它锁住"六个 role、standing-eye、
有限几何、内容寻址绑定"四个不变量，使后续物化不会漂移。

## 交付内容

### 1. `pipeline/synthetic_village/reciprocal_route_module.py`

新增内容（additive，未触碰 v1 plan 任何字段）：

- 新增 `RoleCameraId` Literal：六个 `camera-reciprocal-role-001`..`006`。
- 新增三个常量：
  - `ROLE_CAMERA_EYE_HEIGHT_M = 1.6`（与 `production_profile.EYE_HEIGHT_M` 一致）
  - `ROLE_CAMERA_FOV_X_DEG = 65.0`（与 ground-route FOV 一致）
  - `ROLE_CAMERA_LOOKAHEAD_M = 25.0`（与 `production_profile.ROUTE_LOOKAHEAD_M` 一致）
- 新增 `ReciprocalRoleCameraCandidate` FrozenModel（`extra="forbid"` /
  `frozen=True` / `strict=True`），字段：
  - `role_module_id: ModuleId`、`camera_id: RoleCameraId`、`topology_ref: str`
  - `arc_length_m: float | None`、`position_m` / `look_at_m: tuple[float, float, float]`
  - `eye_height_m: Literal[1.6]`（Literal 锁定，禁 aerial）
  - `fov_x_deg: float`（`0 < fov < 180`，`allow_inf_nan=False`）
  - `audit_only: Literal[False]`（Literal 锁定，禁 audit-only）
  - `disclosure: str`（`min_length=10`，强制诚实 provenance）
  - `bound_production_plan_sha256: Sha256`、`bound_camera_registry_sha256: Sha256`
  - validator `_candidate_is_finite_and_standing_eye`：3-tuple 校验、
    finite 校验、`position_m`/`look_at_m` 距离 ≥ 1.0m（防退化 forward 轴）
- `ReciprocalRouteModulePlan` 加字段
  `role_camera_candidates: tuple[ReciprocalRoleCameraCandidate, ...] = Field(min_length=6, max_length=6)`。
- `_modules_are_exact_and_ordered` validator 末尾加 candidate 校验：
  恰好 6 个、按 module 顺序排列、`camera_id` 唯一。
- 新增 `_DEFAULT_ROLE_CAMERA_PLACEMENT`（六个 role 的 topology_ref +
  position_xy + look_at_xy，scene-local meters）。
- 新增 `_ROLE_CAMERA_DISCLOSURE` dict（每个 role 的诚实 disclosure 字符串，
  全部以 `modeled-unverified standing-eye ...` 开头）。
- 新增 `_default_role_camera_candidates(*, scene, plan_sha, registry_sha)`
  helper：用 `terrain_height_m` 计算 standing-eye z 高度，构造六个候选。
- `build_default_reciprocal_route_module_plan` 加可选参数
  `production_camera_plan: ProductionCameraPlan | None = None`：
  - None → 候选携带 placeholder `"0"*64` SHA（unit-test 友好）
  - 非 None → 候选绑定真实 `canonical_production_plan_bytes(production_camera_plan)`
    SHA + `production_camera_registry_digest(production_camera_plan)`。

### 2. `tests/test_synthetic_village_reciprocal_route_module.py`

新增 12 个 Phase 4.2 TDD 测试，覆盖：

1. `test_plan_carries_six_role_camera_candidates`：默认 plan 有 6 个候选 +
   role_module_id 顺序 + camera_id 唯一 + eye_height=1.6 + audit_only=False。
2. `test_role_camera_candidate_rejects_non_finite_position`：NaN position 被拒。
3. `test_role_camera_candidate_rejects_degenerate_view_direction`：
   `position`/`look_at` 距离 < 1.0m 被拒。
4. `test_role_camera_candidate_rejects_wrong_eye_height`：eye_height=6.0 被拒。
5. `test_role_camera_candidate_rejects_audit_only_true`：audit_only=True 被拒。
6. `test_role_camera_candidate_rejects_short_disclosure`：disclosure < 10 chars 被拒。
7. `test_role_camera_candidate_rejects_non_sha256_plan_binding`：
   `bound_production_plan_sha256` 非 64-hex 被拒。
8. `test_plan_rejects_wrong_candidate_count`：5 个或 7 个候选被拒。
9. `test_plan_rejects_wrong_candidate_order`：candidate role_module_id 错位被拒。
10. `test_plan_rejects_duplicate_candidate_camera_ids`：重复 camera_id 被拒。
11. `test_plan_sha_changes_when_candidate_position_changes`：
    tamper `position_m` → plan_sha256 必须变（tamper detection）。
12. `test_role_camera_candidates_default_to_placeholder_sha`：
    无 production_camera_plan 时候选携带 `"0"*64` placeholder。
13. `test_role_camera_candidates_bind_to_production_plan_sha`：
    传入真实 production_camera_plan 时候选绑定真实 SHA + registry digest。

## 实测证据

| 项 | 值 |
|---|---|
| Phase 4.2 plan_sha256 | `d910e7034f863d7bebf2ee4376f329131bf054eb409dffaf4dd11fc0936ac41b` |
| Phase 4.1 plan_sha256 (上一版) | `84163656de6a4eed9b3f91f0b9ca4e661912c6e6755d06d8aefdd8d3a01a3847` |
| 候选数量 | 6 |
| 首候选 | `camera-reciprocal-role-001` / `central-courtyard-downhill` |
| 末候选 | `camera-reciprocal-role-006` / `lower-valley-uphill` |
| 首候选 position_m | `(40.0, 30.0, 76.143)` (terrain + 1.6m standing-eye) |
| 首候选 look_at_m | `(40.0, 5.0, 70.247)` |

plan_sha256 由 Phase 4.1 的 `84163656...` 变为 Phase 4.2 的 `d910e703...`，
证明 `role_camera_candidates` 字段已进入 content-addressed canonical bytes；
任何 candidate 字段变化 → plan_sha 变 → build_id 变 → 下游 render identity 变。

## 测试结果

```
tests/test_synthetic_village_reciprocal_route_module.py
  50 passed in 1.15s (38 既有 + 12 新 Phase 4.2)

tests/test_synthetic_village_reciprocal_route_module_runtime.py
  35 passed

合计：85 passed in 2.18s

邻近套件：
  tests/test_synthetic_village_canary.py
  tests/test_synthetic_village_environment_module.py
  tests/test_synthetic_village_environment_module_runtime.py
  tests/test_synthetic_village_windows_production_build.py
  150 passed, 2 skipped in 86.60s

ruff：
  Found 1 error (1 fixed, 0 remaining) — import 排序自动修复
```

## 边界与不变量

- **没有提升 `modeled-unverified` 信任**：所有 disclosure 字符串以
  `modeled-unverified standing-eye ...` 开头；trust 字段保持
  `synthetic=True`、`geometry_usability=preview-only`、
  `verification_level=L0`、`trust_effect=none`。
- **没有改 Codex WIP 文件**：未触碰
  `reciprocal_route_production.py`、`studio_server.py`、
  `scripts/synthetic_village.py`、`production_render.py`、
  `render_synthetic_village.py`、`production_quality_gates.py`、
  `local_production_runner.py`。
- **没有新增 Studio jobs / ledger / HUD**：候选只是 plan schema 字段，
  Studio 投影由 Codex lane 负责（HANDOFF-CODEX-011 P1 Codex 侧）。
- **没有改 v1 plan**：所有改动 additive；v1 plan 字段 + instance segment
  131..175 完全不动；reciprocal-route 仍拥有 176..218。
- **没有提前绑定 production_render_id**：候选携带
  `bound_production_plan_sha256` + `bound_camera_registry_sha256`，
  仅作为下游 caller chain 的内容寻址溯源锚点；是否绑定到
  `production_render_id` 由 Codex 的 `VerifiedProductionBuild` 处理。
- **没有用文件名 / role 名提升 measured/metric/aligned**：
  `topology_ref` 是 path network 字符串，不是 surveyed edge/node；
  真实 edge/node 绑定在 Phase 4 item 2 mesh probe 完成后再固化。
- **standing-eye 高度 Literal 锁定**：`eye_height_m: Literal[1.6]`，
  拒绝 aerial (6.0) 或 0.0；与 `production_profile.EYE_HEIGHT_M` 同源。
- **audit_only Literal 锁定 False**：role camera 是真实候选位姿，
  不是 audit-only aerial viewpoint。
- **distance ≥ 1.0m 强制**：防退化 forward 轴，caller 物化
  `_look_at_c2w(position, look_at, up)` 不会得到全零 forward。
- **placeholder SHA 设计**：当 caller 不传 `production_camera_plan` 时
  候选携带 `"0"*64` placeholder，使 plan 可在 unit-test 中构造；
  placeholder 永远不会与真实 64-hex SHA 冲突（hex 字符集限定）。

## 路径限定提交

```bash
git add pipeline/synthetic_village/reciprocal_route_module.py
git add tests/test_synthetic_village_reciprocal_route_module.py
git add handoff/FEEDBACK-HANDOFF-OPUS-009-phase4.2.md
git commit -- pipeline/synthetic_village/reciprocal_route_module.py \
                tests/test_synthetic_village_reciprocal_route_module.py \
                handoff/FEEDBACK-HANDOFF-OPUS-009-phase4.2.md
```

未触碰 Codex WIP 文件、Studio jobs/ledger/HUD、v1 plan、180-camera
canonical plan。`git add -A` / `commit -a` 未使用。

## Codex 后续接入清单（来自 HANDOFF-CODEX-011 P0/P1）

1. **Opus P0-1 mesh/collision probe**（独立可推进）：用 fresh build
   `509919f245932dacd950b7bb95c16638983c4da028ecced5361e3c9da2358a4e/`
   实测路线净宽、坡度、净空、module-module / module-environment 穿插、
   六个 module role 到 canonical topology edge/node 的 attachment；
   probe 输入 `.blend` SHA、plan SHA、object registry SHA 与输出报告 SHA。
2. **Opus P0-2 真实 standing-eye camera**（依赖 P0-1 完成）：
   - 把 candidate `topology_ref` 升级为 canonical topology edge/node 引用；
   - caller 用 `_look_at_c2w` + `_intrinsics` 物化候选为完整 `ProductionCameraPose`；
   - 先处理 `ground-route-010` / `ground-route-039` 的 replacement pose；
   - 用 P0-1 报告排除近墙、穿模、悬空、错误 attachment。
3. **Opus P1-3 fresh preflight + 六层 + post-render v2**：
   Codex caller 已可复用（`run_reciprocal_production_camera`），Opus
   提交新 camera/topology/probe 合同后逐 role 跑一台相机即可。
4. **Codex P1 后续（不阻塞 Opus）**：180-camera batch runner、
   Studio jobs/ledger/HUD 投影、6-role canary 后全 180-camera 分布汇总。

## 待处理 / 未解阻

- **Phase 4.2 schema 仅覆盖 candidate layer**：真实 standing-eye camera
  物化（含 `intrinsics` + `c2w_opencv` + canonical topology edge/node 绑定）
  依赖 mesh/collision probe 结果，Phase 4.2 不交付物化层。
- **`ground-route-010` / `ground-route-039` replacement**：HANDOFF-CODEX-011
  P0-2 要求先处理这两个已知坏位姿，但 replacement 需要 mesh/collision 报告
  排除遮挡 / 穿模，所以也依赖 P0-1 完成。
- **req-5-pose-quality-fail-closed**：继续保持阻断，未在 Phase 4.2 解锁。
