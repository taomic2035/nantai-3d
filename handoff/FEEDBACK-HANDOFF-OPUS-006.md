# FEEDBACK-HANDOFF-OPUS-006 — production 相机净空与坏帧门禁 · 回执

> 回执：GLM（接替 Opus 的 pipeline / planner / renderer / journal lane）→ Codex
> 日期：2026-07-20
> 对应：`handoff/HANDOFF-OPUS-006-production-camera-quality-gates.md`
> 接替声明：见 `handoff/FEEDBACK-HANDOFF-GLM-001.md`；本回执接 Opus lane，遵守
> 同一套 fail-closed / TDD / 路径限定提交 / 不假装可以又不说实际问题 合同。

## 一句话

**Opus 的预渲染几何净空门已在 `production_preflight.py` 完整落地（policy + evidence + decision + report + verify，TDD 全绿）。我补齐了 006 明确说"没做到"的另一半——后渲染六层坏帧门（独立 v2 schema，不破坏 canary/local production 既有契约）与坏机位确定性重排（010/039 可重排，034 由六层门判定）。两层门 + 重排 + 既有 preflight 共 140 个 TDD 测试通过。**

## 核实结果（逐条自己查，不转述）

| Codex 在 006 里的说法 | 我查到的 |
|---|---|
| 推荐沿用既有 `c2w_blender=c2w_opencv@diag(1,-1,-1,1)` | `production_render.py::_opencv_c2w_to_blender` 已实现该矩阵；本回执不重写 |
| 几何预检必须绑定 plan/registry/build/blend/build_report SHA + 第一命中证据 | `production_preflight.py::ProductionClearanceRequest` 已绑定全部这些身份；`ProductionClearanceRayEvidence` 保留 camera ID/sample/distance/object_name/stable_id/part_id/semantic_id（未知保持 None，绝不从名字补可信度） |
| 策略与证据分离；policy 必须有稳定 ID/version 并进入 report 身份 | `ProductionClearancePolicy.policy_id="synthetic-village-clearance-v1"`、`policy_sha256` 进入 `ProductionClearanceReport`；`ProductionClearanceDecision.policy_sha256 + evidence_sha256` 双向绑定 |
| `5×5` 与 `<2m`/`5-of-15` 只可作为候选基线 | `ProductionClearancePolicy.sample_grid == (-0.9, -0.45, 0.0, 0.45, 0.9)`、`near_distance_m`、`minimum_upper_middle_near_hit_count` 均为字段；`evaluate_production_camera_clearance` 是单一规则评估器，未宣称跨场景通用 |
| 质量门只降低 training suitability，绝不提升 trust | 所有 v2 schema 强制 `synthetic=True`、`geometry_trust="simplified-pbr-not-render-parity"`、`trust_effect="none-quality-filter-only"`、`verification_level` 不变 |
| 010、039 做确定性重排 | `production_repose.py::REPOSEABLE_OBSTRUCTED_CAMERA_IDS = {"camera-ground-route-010", "camera-ground-route-039"}`；`repose_obstructed_cameras` 用固定 `lateral_offset_m` + `forward_offset_m` 偏移，结果内容寻址 |
| 034 必须由六层门判定后重排或拒绝 | `REPOSEABLE_OBSTRUCTED_CAMERA_IDS` **不含** 034；六层门 `production_quality_gates.py` 可独立拒绝 034（见 `test_034_clearance_pass_does_not_imply_quality_pass`） |
| pose 改变后必须产生新的 camera registry digest、render ID 和 journal 身份；旧 journal 不得被复用或覆盖 | `test_repose_does_not_reuse_old_journal_identity` 锁定 registry digest 必变；render_id 在 `production_journal.production_render_id` 派生时消费 `camera_registry_sha256`，故 render_id 必变；journal schema 独立，旧 journal 在 `run_local_production_render` 中由 `immutable != expected` 拒绝复用 |
| 重排不得靠文件名、camera ID 或 hardcoded 特判宣称通过 | `_repose_pose` 用纯数学（forward + lateral 单位向量 + 地形高度），没有 `if camera_id == "010"` 这类特判；硬编码只在 `REPOSEABLE_OBSTRUCTED_CAMERA_IDS` 这一白名单上，**只允许** 010/039 被重排 |
| TDD：先写失败测试，再实现 | 006-A 25 测 + 006-B 12 测，先写测试再实现，全绿 |

## 我落地了什么

### 006-A：后渲染六层坏帧门（`pipeline/synthetic_village/production_quality_gates.py`）

**独立 v2 schema**，与 canary 的 `RenderStatistics` 和 `LocalProductionFrameQuality` 物理隔离。理由同 `production_journal.py` 的设计：给 canary 加字段会破坏 24 帧真实 journal 的 canonical bytes 与 `journal_sha256`。

九条规则，每条带 `rule_id` + `rule_version="v1"` + `threshold` + `description`：

| rule_id | 方向 | 基线阈值 | 来源 |
|---|---|---|---|
| `valid-depth-pixel-ratio` | minimum ≥ | 0.30 | 避免背景或几何空洞压倒一切 |
| `valid-normal-pixel-ratio` | minimum ≥ | 0.30 | 同上 |
| `valid-instance-pixel-ratio` | minimum ≥ | 0.30 | 同上 |
| `valid-semantic-pixel-ratio` | minimum ≥ | 0.30 | 同上 |
| `sky-dominance` | maximum ≤ | 0.55 | 狭窄天空 = 低信息 |
| `upper-ground-dominance` | maximum ≤ | 0.30 | **只看上半视野**，下缘正常地面不误杀 |
| `single-near-surface-dominance` | maximum ≤ | 0.40 | REVIEW-CODEX-011：010 近表面 0.43m |
| `depth-near-concentration` | maximum ≤ | 0.35 | <2m 像素占比 |
| `single-instance-upper-dominance` | maximum ≤ | 0.55 | REVIEW-CODEX-011：034 斜穿木廊 |

策略 `ProductionFrameQualityPolicyV2`：
- `policy_id="synthetic-village-frame-quality-v2"`（稳定公开 ID）
- `policy_sha256`（canonical bytes 的 SHA-256，绑定每条阈值和描述，阈值变化在密码学上可见）
- `ProductionFrameQualityRequestV2`：绑定 plan/registry/object_registry/policy/blend/build_report 身份 + `request_id`（内容寻址）
- `ProductionFrameQualityReportV2`：批量决策 + `rejected_camera_ids` + `verify_production_frame_quality_report_v2` 重新计算每条决策与身份

决策 `ProductionFrameQualityDecisionV2`：
- `rule_decisions`：每条规则独立 verdict（`threshold` + `measured` + `passes`）
- `failed_rule_ids`：自动从 `rule_decisions` 派生，与 `passes` 标志一致（防伪造）
- 暴露 `camera_id` + `rule_id` + `threshold` + `measured` + `trust_effect` 给 Studio 渲染拒绝原因（§4 最后一条）

TDD（25 测全绿）覆盖：
- 策略内容寻址与阈值变化的密码学可见性
- minimum/maximum 规则的方向正确性
- 下缘地面不误杀（`upper_ground_pixel_ratio` 只看上半视野）
- 010 的近表面支配度被拒绝
- 034 的"预检未拒绝 ≠ 后渲染通过"（核心证据分离）
- 统计 canonical bytes 跨进程稳定
- 决策伪造与身份不匹配 fail-closed
- 请求字段重定向 fail-closed
- `rejected_camera_ids` 与决策一致性

### 006-B：坏机位确定性重排（`pipeline/synthetic_village/production_repose.py`）

`repose_obstructed_cameras(plan, obstructed_camera_ids, offsets)`：

- **白名单**：`REPOSEABLE_OBSTRUCTED_CAMERA_IDS = {"camera-ground-route-010", "camera-ground-route-039"}`，**不含 034**
- **偏移**：`ReposeOffsets(lateral_offset_m=1.5, forward_offset_m=2.0)`，沿相机前向 + 左侧（前向逆时针 90°）偏移
- **约束**：
  - 新位置必须在 scene extent 内
  - 新中心不得与任何已有相机中心碰撞
  - `ground-route` 相邻间距仍 ≤ 30m（`MAX_GROUND_ROUTE_CAMERA_SPACING_M`）
  - 180 台契约保留，camera ID 唯一，sequence_index 密集有序
  - route loop evidence 与 group coverage 不倒退（拓扑字段不变）
- **身份变化（fail-closed）**：
  - 若新 `plan_sha256 == 旧 plan_sha256` → 抛错（说明偏移未实际生效）
  - 若新 `camera_registry_sha256 == 旧` → 抛错
  - 二者必变 → render_id 必变 → 旧 journal 不可复用

TDD（12 测全绿）覆盖：
- 010/039 重排后 plan/registry digest 必变
- 非重排相机 pose 字节相同
- 034 不可重排（fail-closed）
- 不在 plan 中的 camera ID fail-closed
- 重复 ID fail-closed
- 非正偏移 fail-closed
- 180 台、唯一中心、sequence 密集有序保留
- route loop + group coverage 不倒退
- ground-route 间距仍 ≤ 30m
- 旧 journal 不可复用（registry digest 变化）

## 我**没做**的（如实说，不绕过）

### 1. Blender renderer script 未扩展产出六层统计

`production_quality_gates.py` 定义了 `ProductionFrameLayerStatistics` 的 schema 和 evaluator，但 `scripts/blender/render_synthetic_village.py` 还没有实际从渲染产物计算这些 ratio 的代码。这意味着：
- v2 schema 已就绪、评估器已就绪、TDD 已就绪
- 但**真实渲染产物到 statistics 的桥接**未实现
- 当前 v2 报告只能用手工构造的 statistics 验证；要让 production runner 真实产出 v2 报告，需要扩展 renderer script

**为什么没做**：renderer script 的 SHA 进入 `render_id`；改它会让所有既有 24 帧 + 180 帧 journal 失效。这个改动应该和 Codex 的 Studio UI 协调（Studio jobs/ledger/HUD 呈现 v2 拒绝原因），不能 Opus 单方面推进。

### 2. production camera plan 的 `undelivered_requirements` 未更新

`production_profile.py::_undelivered_requirements()` 仍把 `req-5-pose-quality-fail-closed` 标为 `not-implemented`。我**没有**改成 `structurally-unreachable` 或 `partial`，因为：
- 六层门 schema 已就绪，但 renderer script 未产出真实 statistics（见上）
- 重排已就绪，但 production runner 未调用它
- 在 renderer 桥接完成前，把 `req-5` 标成 `partial` 会**假装**它已经在真实渲染路径上工作

正确的做法是等 renderer 桥接完成后，由真实 v2 报告的产出路径证明 `req-5` 落地，再更新 `undelivered_requirements`。这是 fail-closed 的体现。

### 3. `run_local_production_render` 未集成 v2 门

`local_production_runner.py` 仍只调用 `evaluate_local_production_frame_quality`（单 valid-pixel-ratio 规则）。集成 v2 门需要：
- 在 renderer script 中产出 `ProductionFrameLayerStatistics`（或从既有 `RenderStatistics` 派生）
- 在 runner 中调用 `evaluate_production_frame_quality_v2`
- 在 journal 中并存 v1（`LocalProductionFrameQuality`）和 v2 决策（兼容现有 journal schema）

这个集成应作为下一步交办，由 Opus lane + Codex lane 协调完成（Studio HUD 要呈现 v2 决策的 9 条规则，不是单 valid-pixel-ratio）。

### 4. 180 台全量重排后的真实重渲未跑

我没有跑 010/039 重排后的真实 Blender 渲染来验证 RGB 是否真的改善了。原因：
- 开发机无 NVIDIA GPU，Blender CPU 渲染慢
- renderer script 未扩展产出 v2 statistics（见 §1）
- 即使跑，也只是单帧验证，不是 180 台全量

真实重渲应由 Codex 在 Studio UI 协调下做，对照 REVIEW-CODEX-008/009/010 的方法。

## TDD / 验收门禁

```powershell
d:\vibecoding\nantai\.venv\Scripts\python.exe -m pytest `
  tests/test_synthetic_village_production_preflight.py `
  tests/test_synthetic_village_production_profile.py `
  tests/test_synthetic_village_production_render.py `
  tests/test_synthetic_village_production_journal.py `
  tests/test_synthetic_village_production_quality_gates.py `
  tests/test_synthetic_village_production_repose.py -q
```

结果：**140 passed in 42.34s**。既有 103 测无回归，新增 37 测全绿。

## 提交合同

- 路径限定提交：本次只动 `pipeline/synthetic_village/production_quality_gates.py`、`pipeline/synthetic_village/production_repose.py`、`tests/test_synthetic_village_production_quality_gates.py`、`tests/test_synthetic_village_production_repose.py`。
- **未提交**：等用户确认后再 `git add <明确文件>` + `git commit -- <路径>`，保留 `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`（006 明确要求）和 `Co-Authored-By: GLM-5.2 <glm-5.2@noreply.local>`（接替 Opus 署名）。
- push 时机由用户协调。

## 我需要 Codex 后续配合的

1. **renderer script 扩展**：让 `scripts/blender/render_synthetic_village.py` 在渲染后产出 `ProductionFrameLayerStatistics`（六层各 valid 像素、sky/ground/near-surface 占比、depth-near 集中度、single-instance 上半支配度）。这是 Opus → Codex 的潜在交办点（或仍由 Opus 做，因为改 renderer SHA 会变 render_id）。
2. **Studio jobs/ledger/HUD 呈现 v2 决策的 9 条规则**：当前只呈现单 valid-pixel-ratio；v2 有 9 条 `rule_id` + `measured` + `threshold` 要展示。这是 Codex lane，我不动。
3. **实渲新旧相机对照**：010/039 重排后的 RGB 对照（与 REVIEW-CODEX-008/009/010 同一套方法）。Codex 做。
4. **`undelivered_requirements` 更新时机**：renderer 桥接完成 + 真实 v2 报告产出后，由 Opus 更新 `req-5-pose-quality-fail-closed` 状态。

## 下一步

1. **等用户确认是否提交本次代码**（4 个文件）。
2. 用户确认后，我进入 **007**（Batch 6 模块生产化）。007 路径与 006 不再重叠（006 已落地，007 改 scene plan / blender build / environment module），可并行推进。
3. 007 完成后回执 `FEEDBACK-HANDOFF-OPUS-007.md`。
4. renderer script 的 v2 statistics 桥接作为**后续交办**（HANDOFF-OPUS-008 或由 Codex 提出）。

若 Codex 对 v2 schema 的 9 条规则或重排偏移有不同意见，请在 review 时指出，我会调整。当前阈值是 baseline，不是跨场景通用真理（§2 要求）。
