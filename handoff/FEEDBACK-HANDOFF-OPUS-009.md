# FEEDBACK-HANDOFF-OPUS-009 — ReciprocalRouteModulePlan v1 plan layer delivery

> 回执：Opus（pipeline / 内容寻址 / plan layer）→ Codex（runtime / Studio / caller）
> 日期：2026-07-21
> 对应交办：`handoff/HANDOFF-OPUS-009-batch8-reciprocal-route-productionization.md`
> 优先级：在 HANDOFF-006 Task 5 §3 闭环之后；本次仅交付 plan/TDD，**不**实现
> Blender runtime，**不**提升 modeled-unverified 信任

## 一句话

**`ReciprocalRouteModulePlan v1` 的 plan schema + 内容寻址 + TDD 已交付。
六角色 × 43 parts，instance ID 176..218，additive 不动 v1 131..175。**
未实现 Blender runtime、相机扩展、preflight、实渲 —— 这些等 §3 闭环后再做。

## 交付内容

### 1. Plan 名称、schema version、SHA、recipe version

```text
plan_name:           ReciprocalRouteModulePlan
schema_version:      nantai.synthetic-village.reciprocal-route-module.v1
plan_id:             synthetic-village-reciprocal-route-module-v1
recipe_version:      v1
plan_sha256:         bb321382e9d1182a414b9ae881a1703a677ce891af0a18a07f2e7ee0585d24e1
module_count:        6
part_count:          43
instance_id_segment: 176..218
```

### 2. 六角色到 canonical part ID / instance ID / semantic / material 的映射

#### 模块 1: central-courtyard-downhill (instance 176..182, 7 parts)

| part_id | instance_id | semantic_class | material_slot_id |
|---|---|---|---|
| courtyard-downhill-gate-001 | 176 | path | material-courtyard-stone-01 |
| courtyard-covered-side-passage-001 | 177 | building | material-courtyard-timber-01 |
| courtyard-cross-slope-alley-001 | 178 | path | material-courtyard-stone-01 |
| courtyard-route-attachment-upper-001 | 179 | path | material-courtyard-stone-01 |
| courtyard-route-attachment-lower-001 | 180 | path | material-courtyard-stone-01 |
| courtyard-gallery-post-run-001 | 181 | building | material-courtyard-timber-01 |
| courtyard-gallery-guard-001 | 182 | prop | material-courtyard-iron-01 |

#### 模块 2: bridge-deck-crossing (instance 183..188, 6 parts)

| part_id | instance_id | semantic_class | material_slot_id |
|---|---|---|---|
| bridge-route-attachment-upstream-001 | 183 | path | material-stone-block-01 |
| bridge-route-attachment-downstream-001 | 184 | path | material-stone-block-01 |
| bridge-access-ramp-001 | 185 | path | material-stone-block-01 |
| bridge-side-maintenance-path-001 | 186 | path | material-stone-block-01 |
| bridge-drainage-scuppers-001 | 187 | creek | material-stone-block-01 |
| bridge-deck-edge-transition-001 | 188 | bridge | material-stone-block-01 |

#### 模块 3: watermill-tailrace (instance 189..195, 7 parts)

| part_id | instance_id | semantic_class | material_slot_id |
|---|---|---|---|
| watermill-building-shell-001 | 189 | building | material-waterwheel-wood-01 |
| watermill-maintenance-platform-001 | 190 | path | material-waterwheel-wood-01 |
| watermill-service-stair-001 | 191 | path | material-waterwheel-wood-01 |
| watermill-access-panel-001 | 192 | prop | material-waterwheel-iron-01 |
| watermill-creek-bank-path-001 | 193 | path | material-creek-stone-01 |
| watermill-platform-guard-001 | 194 | prop | material-waterwheel-iron-01 |
| watermill-tailrace-retaining-wall-001 | 195 | retaining-wall | material-stone-block-01 |

#### 模块 4: covered-gallery-underpass (instance 196..204, 9 parts)

| part_id | instance_id | semantic_class | material_slot_id |
|---|---|---|---|
| gallery-underpass-lower-lane-001 | 196 | path | material-courtyard-timber-01 |
| gallery-post-run-001 | 197 | building | material-courtyard-timber-01 |
| gallery-beam-run-001 | 198 | building | material-courtyard-timber-01 |
| gallery-foundation-run-001 | 199 | retaining-wall | material-stone-block-01 |
| gallery-guard-run-001 | 200 | prop | material-courtyard-iron-01 |
| gallery-side-door-001 | 201 | building | material-courtyard-timber-01 |
| gallery-branch-attachment-upper-001 | 202 | path | material-courtyard-stone-01 |
| gallery-branch-attachment-lower-001 | 203 | path | material-courtyard-stone-01 |
| gallery-branch-attachment-side-001 | 204 | path | material-courtyard-stone-01 |

#### 模块 5: forest-orchard-boundary (instance 205..211, 7 parts)

| part_id | instance_id | semantic_class | material_slot_id |
|---|---|---|---|
| forest-boundary-path-fork-001 | 205 | path | material-forest-soil-01 |
| forest-orchard-transition-001 | 206 | path | material-forest-soil-01 |
| forest-retaining-drain-001 | 207 | retaining-wall | material-stone-block-01 |
| forest-trail-shelter-001 | 208 | building | material-forest-timber-01 |
| forest-route-attachment-inbound-001 | 209 | path | material-forest-soil-01 |
| forest-route-attachment-outbound-001 | 210 | path | material-forest-soil-01 |
| forest-edge-vegetation-band-001 | 211 | prop | material-forest-foliage-01 |

#### 模块 6: lower-valley-uphill (instance 212..218, 7 parts)

| part_id | instance_id | semantic_class | material_slot_id |
|---|---|---|---|
| lower-valley-entry-path-001 | 212 | path | material-creek-stone-01 |
| lower-valley-field-edge-path-001 | 213 | path | material-creek-stone-01 |
| lower-valley-creek-maintenance-trail-001 | 214 | path | material-creek-stone-01 |
| lower-valley-drainage-outlet-001 | 215 | creek | material-stone-block-01 |
| lower-valley-building-back-entry-001 | 216 | building | material-waterwheel-wood-01 |
| lower-valley-route-reconnection-001 | 217 | path | material-creek-stone-01 |
| lower-valley-retaining-step-001 | 218 | retaining-wall | material-stone-block-01 |

### 3. 旧 v1 证据保持不变的测试

TDD 锁定 `tests/test_synthetic_village_reciprocal_route_module.py`:

- `test_environment_module_plan_v1_remains_canonical`: 构建 reciprocal-route plan
  之前和之后，`EnvironmentModulePlan v1` 的 canonical bytes 与 SHA 完全相同
- `test_environment_module_plan_v1_instance_segment_untouched`: v1 plan 仍只
  持有 instance 131..175，reciprocal-route plan 不触碰该段
- `test_default_plan_uses_exactly_176_to_218`: reciprocal-route plan 只用
  176..218，不与 v1 段重叠

### 4. 新 `.blend` / build request/report / registry / Blender executable SHA

**未交付**。本次只交付 plan layer，不实现 Blender runtime。`ReciprocalRouteModulePlan`
的 SHA (`bb321382...`) 只绑定 plan 内容，不绑定任何 .blend 字节。后续 runtime
实现时，必须按 `apply_environment_modules.py` 同样的内容寻址模式追加：

- `reciprocal_route_module_plan_sha256`
- `base_blend_sha256`（175-root v2 blend）
- `base_build_report_sha256`（175-root v2 build report）
- `runtime_script_sha256`（新增的 apply_reciprocal_route_modules.py）
- `blender_executable_sha256`

### 5. Topology / collision / preflight 实际报告

**未交付**。plan layer 只声明 recipe 中的拓扑约束（connects_to_topology、
continuous_collision、not_crossing_walkable_surface 等），不实测。实测由后续
Blender runtime + preflight 提供。

### 6. 六角色逐帧 request/report、六层 artifact 与 post-render decision SHA

**未交付**。本次只交付 plan layer，不实现相机扩展、preflight、实渲、post-render。

### 7. 失败项及其 fail-closed 状态

无失败项。29 个 TDD 测试全过。

但**显式列出本次不交付的边界**（fail-closed，不静默提升）：

| 边界 | 状态 |
|---|---|
| Blender runtime 扩展 | 未交付 |
| 相机扩展（standing-eye ground-route camera） | 未交付 |
| preflight + fresh 175+root preflight | 未交付 |
| 六层 artifact 实渲 | 未交付 |
| post-render v2 policy | 未交付 |
| RGB 人工复核 | 未交付 |
| metric_alignment / real_photo_textures / training_use | 仍 `false` / `forbidden` |
| geometry_trust | 仍 `simplified-pbr-not-render-parity` |
| verification_level | 仍 `L0` |
| trust_effect | 仍 `none` |

## Plan layer fail-closed 强度

### 内容寻址

- `canonical_reciprocal_route_module_plan_bytes`：JSON 排序 + UTF-8 + 换行结尾
- `reciprocal_route_module_plan_sha256`：64-hex SHA-256 of canonical bytes
- 任一 recipe 字段、part material_slot、instance_id 变化都改变 SHA（TDD 锁定）

### FrozenModel + extra='forbid'

- `ReciprocalRouteModulePlan` / `ReciprocalRouteModule` / `ReciprocalRouteModulePart` /
  6 个 recipe 类型 / 5 个 spec 类型 全部 `extra='forbid', frozen=True, strict=True`
- 任何未知字段都被拒绝

### Literal 锁定

- `schema_version` / `plan_id` / `recipe_version` Literal 锁定
- `synthetic: Literal[True]` / `metric_alignment: Literal[False]` /
  `real_photo_textures: Literal[False]` /
  `geometry_trust: Literal["simplified-pbr-not-render-parity"]` /
  `trust_effect: Literal["none"]` /
  `verification_level: Literal["L0"]` /
  `geometry_usability: Literal["preview-only"]` —— 信任字段全部 Literal 锁定，
  无法在 plan layer 提升
- Batch 8/9 manifest + archive SHA-256 全部 Literal 锁定
- 12 张 image SHA-256 通过 `_module_batch8_source` / `_module_batch9_source`
  字典 + `Sha256` pattern 双重锁定

### Instance ID 分段锁定

- 六个 module 各自的 instance range 硬编码：
  - `CENTRAL_DOWNHILL_INSTANCE_RANGE = range(176, 183)`  (7 parts)
  - `BRIDGE_CROSSING_INSTANCE_RANGE = range(183, 189)`   (6 parts)
  - `WATERMILL_TAILRACE_INSTANCE_RANGE = range(189, 196)` (7 parts)
  - `GALLERY_UNDERPASS_INSTANCE_RANGE = range(196, 205)` (9 parts)
  - `FOREST_BOUNDARY_INSTANCE_RANGE = range(205, 212)`    (7 parts)
  - `LOWER_VALLEY_UPHILL_INSTANCE_RANGE = range(212, 219)` (7 parts)
- `ReciprocalRouteModulePart._instance_in_module_segment` 校验 part 必须落在
  其 module 的 range 内
- `ReciprocalRouteModulePlan._modules_are_exact_and_ordered` 校验全 plan instance
  集合必须 = `set(range(176, 219))`，无重叠、无缺漏
- TDD 锁定六段不重叠且并集精确为 176..218

### 模块顺序锁定

- `ModuleId` Literal tuple 锁定六种 module_id
- `_modules_are_exact_and_ordered` 校验 modules 序列必须精确等于
  `("central-courtyard-downhill", "bridge-deck-crossing", "watermill-tailrace",
   "covered-gallery-underpass", "forest-orchard-boundary", "lower-valley-uphill")`
- TDD 锁定乱序拒绝

### Recipe ↔ Module 一致性

- `ReciprocalRouteModule._recipe_matches_module` 校验 recipe.module_id == module_id
- `recipe: Annotated[Recipe, Field(discriminator="module_id")]` 用 pydantic
  discriminator 自动选正确的 recipe 类型
- TDD 锁定 recipe 与 module 不匹配时拒绝

### Cross-request 身份校验

- `verify_reciprocal_route_module_plan` 重算 scene/topology/env_module_plan SHA
  并与 plan 内的 SHA 比对
- 重算 canonical bytes 走 `model_validate_json` round-trip，强制所有 validator
  重跑
- TDD 锁定 scene 不匹配、env_module_plan 不匹配、非 canonical bytes 都拒绝

## TDD 覆盖（29 测试）

### Schema constants (2)
- `test_schema_constants_are_locked`
- `test_instance_segments_partition_176_to_218`

### Default plan structure (7)
- `test_default_plan_has_six_ordered_modules`
- `test_default_plan_part_count_matches_summary`
- `test_default_plan_uses_exactly_176_to_218`
- `test_default_plan_part_ids_are_unique_across_plan`
- `test_default_plan_provenance_constants_are_locked`
- `test_default_plan_binds_batch8_batch9_manifest_and_archive`
- `test_default_plan_binds_environment_module_v1_sha`

### Canonical bytes + content addressing (6)
- `test_canonical_bytes_end_with_newline`
- `test_plan_sha256_is_64_hex`
- `test_plan_sha256_is_deterministic_across_processes`
- `test_plan_sha256_changes_when_module_replaced`
- `test_plan_sha256_changes_when_part_material_slot_swapped`
- `test_plan_sha256_changes_when_environment_module_plan_sha_swapped`
- `test_plan_sha256_changes_when_batch8_manifest_swapped`

### Fail-closed validators (6)
- `test_plan_rejects_wrong_module_order`
- `test_plan_rejects_part_outside_module_segment`
- `test_plan_rejects_missing_module`
- `test_plan_rejects_duplicate_part_id_within_module`
- `test_plan_rejects_non_sha256_environment_module_binding`
- `test_plan_rejects_wrong_recipe_module_id`

### verify_reciprocal_route_module_plan (4)
- `test_verify_passes_for_default_plan`
- `test_verify_rejects_scene_mismatch`
- `test_verify_rejects_environment_module_plan_mismatch`
- `test_verify_rejects_non_canonical_bytes`

### v1 immutability (2)
- `test_environment_module_plan_v1_remains_canonical`
- `test_environment_module_plan_v1_instance_segment_untouched`

### Trust invariant (1)
- `test_plan_does_not_promote_trust`

## 验证命令

```bash
d:\vibecoding\nantai\.venv\Scripts\python.exe -m pytest tests/test_synthetic_village_reciprocal_route_module.py -q
# 29 passed

d:\vibecoding\nantai\.venv\Scripts\python.exe -m ruff check pipeline/synthetic_village/reciprocal_route_module.py tests/test_synthetic_village_reciprocal_route_module.py
# All checks passed!
```

跨套件无 regression：

```bash
d:\vibecoding\nantai\.venv\Scripts\python.exe -m pytest tests/test_synthetic_village_environment_module.py tests/test_synthetic_village_environment_module_runtime.py tests/test_synthetic_village_reciprocal_route_module.py tests/test_synthetic_village_cli.py tests/test_synthetic_village_production_journal.py tests/test_synthetic_village_production_repose.py -q
# 177 passed
```

## 边界声明（按交办"明确不能宣称"）

即使本 plan layer 全部通过，也**只**证明：

- 6 个 reciprocal-route module 的 plan schema 自洽、内容寻址、跨进程确定性
- 43 个新 part 有唯一 object ID、instance ID（176..218）、semantic ID、material slot
- Batch 8/9 release manifest + archive + 12 张 image SHA 已绑定到 plan provenance
- v1 `EnvironmentModulePlan` canonical bytes 不受影响
- 信任级别未提升

**不**证明：

- 6 个视角来自同一几何或 360° 覆盖
- 任何几何已被 Blender 实际构建
- 任何 standing-eye 相机已注册或可漫游
- topology/collision 实测通过
- 场景来自真实照片/视频重建
- 任意世界坐标已经有真实几何
- 具有 metric 或 training-suitable 信任

## 下一步

按交办 §"推荐版本边界"，后续工作（**不在本次范围**）：

1. `ReciprocalRouteRuntimeRequest` + `ReciprocalRouteBuildReport` schema
2. `apply_reciprocal_route_modules.py` Blender runtime（参考 `apply_environment_modules.py`）
3. 扩展 `EnvironmentModuleBuildReport` adapter 接受 v2 175+43=218 root scene
4. 新 standing-eye `ground-route` 相机扩展（六角色各一台）
5. fresh 218-root preflight + 六层实渲 + v2 post-render policy
6. 前后 RGB 对比

按用户 2026-07-21 边界，这些等 §3 caller 闭环后再做。Opus lane 进入等待状态。

## Co-Authored-By

GLM-5.2 <noreply@zai.com>
