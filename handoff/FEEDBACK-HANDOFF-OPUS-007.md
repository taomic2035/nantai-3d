# FEEDBACK-HANDOFF-OPUS-007 — Batch 6 模块生产化回执

> 接收：HANDOFF-OPUS-007-batch6-modules-productionization.md
> 接管：GLM（接替 Opus lane）
> 日期：2026-07-20
> 状态：plan-level 完整、TDD CLEAN；downstream Blender build 未实现（明确未交付）

## 总览

按 HANDOFF-OPUS-007 §推荐架构，新增 additive、内容寻址的
`EnvironmentModulePlan`，绑定 `ScenePlan` SHA-256、`ElevatedTopologyPlan`
SHA-256 和三张 `design-only` 参考 SHA-256，作为 `ElevatedTopologyPlan`
的同构外延。**不重写** ScenePlan v1 / ElevatedTopology v1 的已锁定 digest。

新增文件：

```text
pipeline/synthetic_village/environment_module.py
tests/test_synthetic_village_environment_module.py
```

测试结果：**39/39 通过**，覆盖 HANDOFF-OPUS-007 §TDD 列出的全部十项验收。

## 实现要点

### 1. 中央工作院落（`central-courtyard`）

- 绑定 `courtyard-public-002`、`central-ground-west/east`、
  `edge-central-stair-001 / edge-central-gallery-001 / edge-central-ramp-001`。
- `CourtyardGallerySpec` 强制 `clear_width_m >= 2.6m`、
  `clear_height_m >= 2.4m`、东西入口分别固定连到
  `path-network-002 / 003`。
- `CourtyardStairSpec` 强制 `clear_width_m >= 2.4m`、`tread_count >= 3`、
  `tread_depth_m > 0.25m`、`continuous_collision=True`。
- `CourtyardRampSpec` 强制 `clear_width_m >= 3.0m`、
  `slope_pct ∈ (0, 8.3]`、`continuous_collision=True`。
- `CourtyardPropSpec` 强制 workshed/workbench/replaceable prop slot 数 ≥1，
  显式声明 `planter_tree_non_collision=True`。

### 2. 下层桥拱 / 水车（`lower-bridge-waterwheel`）

- 绑定 `bridge-lower-001`、`creek-main-001`、`path-network-001 / 005`。
- `CreekCrossSectionSpec` 几何截面非穿透约束：
  `water_z <= bank_z <= terrain_z` 且 `arch_soffit_z >= deck_z`。
  违反任一约束 → fail closed。
- `WaterwheelPartSpec` 让水车轮 / 轴 / 支架 / millrace / 落水 / 回水
  共 6 件保持独立 `part_id` / `instance_id` / `material_slot_id`。
- `maintenance_platform_is_main_route: Literal[False] = False` ——
  维护平台永不提升为 route-loop evidence。
- `main_route_connectivity_preserved=True` 强制为真，否则 fail closed。

### 3. 后场服务院（`rear-service-courtyard`）

- 绑定 `building-central-008`。
- `paving_conform_to_terrain=True`、
  `door_window_eaves_gutter_declared=True`、
  `elevated_access_deck_present=True`。
- 至少三种 `ServiceCourtyardVariantSpec`，`variant_id` 唯一。
- `props_do_not_carry_topology: Literal[True] = True`、
  `props_do_not_block_paths: Literal[True] = True` —— 道具不承担 topology、
  不堵门 / 巷 / 维护路径。

## Instance ID 分区（硬锁）

```text
elevated components   127-130   (elevated_topology.py 已锁)
central-courtyard     131-145   (15 parts)
lower-bridge-waterwheel 146-160 (15 parts: 146-154 桥构/creek,
                                 155-160 水车 6 件)
rear-service-courtyard 161-175 (15 parts)
```

`EnvironmentModulePart._instance_in_module_segment` validator 强制每个
part 的 instance_id 必须落在所属 module 的 segment 内；越界即 fail closed。
`EnvironmentModulePlan._modules_are_exact_and_ordered` 强制全段恰好被
131-175 占满、无空缺、无重叠。

## Provenance 与 fail-closed

顶层 `EnvironmentModulePlan` 显式声明并硬锁：

```text
synthetic=true
geometry_usability=preview-only
verification_level=L0
metric_alignment=false
real_photo_textures=false
geometry_trust=simplified-pbr-not-render-parity
trust_effect=none
```

三张 `design-only` 参考 SHA-256 仅作 provenance 绑定，**不**用于推断
coverage / orientation / training suitability。`test_design_source_shas_are_for_provenance_only`
显式断言 plan 顶层不存在 `coverage` / `orientation` / `training_use` /
`camera_calibration` / `geometry_consistency` 字段。

`verify_environment_module_plan(plan, scene=..., elevated_topology=...)`
重绑所有 identity：scene SHA、topology SHA、canonical bytes 重验证，
任一不一致即抛 `EnvironmentModuleError`。

## 修复的 bug

实现过程中发现并修复了一个 recipe/parts 不一致的 bug：

- `_default_lower_bridge_recipe()` 的 `waterwheel_parts` 原本声明 instance
  IDs `146-151`，但 `_default_module("lower-bridge-waterwheel")` 的
  `EnvironmentModulePart` 列表把水车 6 件放在 `155-160`。两者必须一致。
- 修复：recipe 改用 `155-160`，与 parts 列表对齐；同时新增
  `LowerBridgeRecipe._waterwheel_parts_independent` 校验水车 instance ID
  必须在 bridge segment（146-160）内，以及
  `EnvironmentModule._recipe_matches_module` 跨校验 recipe 声明的水车
  part_id / instance_id 必须在 parts 列表中出现。
- 新增 TDD 回归测试 `test_waterwheel_recipe_must_match_parts_list` 与
  `test_waterwheel_recipe_part_id_must_appear_in_parts_list`，
  防止此类漂移再次发生。

## TDD 覆盖（39 个测试，对应 §TDD 十项）

| §TDD | 测试 |
|---|---|
| 1 canonical bytes 绑定 | `test_canonical_bytes_bind_exact_scene_and_topology_hashes` `test_canonical_bytes_are_stable_across_processes` `test_verify_environment_module_plan_passes_on_canonical` |
| 2 tampered identity fail-closed | `test_tampered_scene_sha256_fails_closed` `test_tampered_topology_sha256_fails_closed` `test_tampered_design_source_sha256_fails_closed` `test_verify_rejects_plan_bound_to_a_different_scene` |
| 3 stable ID/instance/semantic/material | `test_three_modules_are_exact_and_ordered` `test_module_instance_id_partition_is_locked` `test_full_instance_segment_occupied_exactly` `test_part_ids_unique_across_plan` `test_material_slot_ids_are_stable` `test_summary_part_count_matches` |
| 4 creek 截面无穿透 | `test_creek_section_accepts_canonical_ordering` `test_creek_section_rejects_water_above_terrain` `test_creek_section_rejects_water_above_bank` `test_creek_section_rejects_soffit_below_deck` `test_lower_bridge_module_has_at_least_three_sections` |
| 5 中央院落门/宽/净空 | `test_central_courtyard_thresholds_match_spec` `test_central_courtyard_rejects_undersized_gallery` `test_central_courtyard_entries_bind_path_network_002_and_003` `test_central_courtyard_rejects_duplicate_ground_attachments` |
| 6 服务院道具不侵入 | `test_rear_service_recipe_props_do_not_carry_topology` `test_rear_service_recipe_rejects_missing_variants` |
| 7 module registry 拒绝缺失/重复/乱序 | `test_plan_rejects_missing_module` `test_plan_rejects_duplicate_module` `test_plan_rejects_out_of_order_modules` `test_plan_rejects_unknown_module_id` `test_module_rejects_part_in_wrong_segment` `test_module_rejects_part_in_other_module_segment` |
| 8 同请求 byte-identical | `test_deterministic_canonical_bytes` `test_plan_sha_changes_when_a_part_changes` |
| 9 六层帧 instance/semantic 可区分 | `test_bridge_waterwheel_courtyard_service_props_have_distinct_semantics` `test_waterwheel_parts_have_independent_identity` `test_waterwheel_recipe_must_match_parts_list` `test_waterwheel_recipe_part_id_must_appear_in_parts_list` |
| 10 coverage 未渲染即 unknown | `test_plan_does_not_claim_coverage` `test_design_source_shas_are_for_provenance_only` `test_plan_does_not_silently_promote_to_metric` |

## 验证命令与结果

```powershell
d:\vibecoding\nantai\.venv\Scripts\python.exe -m pytest `
  tests/test_synthetic_village_scene_plan.py `
  tests/test_synthetic_village_elevated_topology.py `
  tests/test_synthetic_village_production_quality_gates.py `
  tests/test_synthetic_village_production_repose.py `
  tests/test_synthetic_village_environment_module.py -q
```

结果：**97 passed in 25.86s**。

## 明确未交付

按 fail-closed 合同，下列工作 **不在本回执范围内**：

1. **Blender build request / runtime 适配**：本回执只交付 plan-level
   recipe 与 validator。`scripts/blender/build_synthetic_village.py` 仍未
   消费 `EnvironmentModulePlan` 生成 `.blend/.glb/previews`，也未在 build
   report 里写入 `module_plan_sha256` / `recipe_version` / 三张
   `design_source_sha256` / `trust_effect=none`。§TDD 7、8、9 的
   build-level 验收（实际 Blender 产物 SHA 一致性、registry 130→175
   扩容、六层帧实际像素统计）仍待下游实现。

2. **`object_registry` / `semantic_registry` 扩容**：当前 BuildReport
   schema 仍锁定 `canonical_roots=130` / `object_registry >= 130` /
   `semantic_registry >= 15`。环境模块正式入册前需要把
   `canonical_roots` 扩到 175、`object_registry` 扩到 ≥ 175、新增
   `source_hashes.elevated_topology_sha256` 与 `module_plan_sha256` 字段。
   **此项扩容未做** —— 它需要先和 Codex 协调 build report v2 schema。

3. **`undelivered_requirements` 未更新**：`production_profile.py` 的
   `UNDELIVERED_REQUIREMENT_IDS` 仍包含 `req-5-pose-quality-fail-closed`；
   要等 Blender runtime bridge 完成且 180 真实帧通过六层门后再翻牌。

4. **三台自定义审计相机仍未注册**：按 §相机与六层验收，三台审计相机
   保持 `not-registered`、`training_use=forbidden`，直至 production frames
   通过六层门。

5. **180-camera 真实帧重渲未跑**：本次只交付 plan-level 合同，未触发
   任何 Blender 实渲。

## 预存的 Blender runtime 测试失败（与本回执无关）

跑 `tests/test_synthetic_village_blender_runtime.py` 时观察到 25 个
失败（`test_runtime_rejects_scene_contract_tampering_before_staging[...]`
等），错误信息为：

```text
BuildReport validation failed: 4 validation errors
- source_hashes.elevated_topology_sha256  Field required [type=missing]
- object_registry  Tuple should have at least 130 items, not 126
- semantic_registry  Tuple should have at least 15 items, not 14
- counts.canonical_roots  Input should be 130, got 126
```

经 `git stash` 验证：**这些失败在本回执改动之前就已存在**，是 build
report schema 与 Blender build script 之间未对齐的预存问题，**不是**
本回执引入的回归。修复路径属于上面「未交付 §2」的 build report v2
schema 扩容。

## 协作交接

按 HANDOFF-OPUS-007 §路径与协作，本回执未触碰 Codex lane
（`web/studio/`、`web/viewer/`、`pipeline/studio_server.py`）。

下一步建议（按依赖顺序）：

1. 与 Codex 协调 build report v2 schema 扩容（`canonical_roots=175`、
   `object_registry >= 175`、新增 `module_plan_sha256` 等字段），并解锁
   25 个预存 Blender runtime 失败。
2. 实现 `scripts/blender/build_synthetic_village.py` 对
   `EnvironmentModulePlan` 的消费：生成三个模块的几何、写
   `module_plan_sha256` / `recipe_version` / 三张 source SHA 到 build
   report。
3. 跑 180-camera 真实帧重渲，由六层 production frame quality gate
   （HANDOFF-OPUS-006-A）判定，通过后再翻 `req-5-pose-quality-fail-closed`。
4. 通过后由 Codex 整理 RGB 新旧对照、instance/semantic 可见性证据与
   Studio job/ledger/HUD 呈现。

## 提交说明

按 AGENTS.md 路径限定提交约定，本次新增的两份文件可独立 stage：

```text
git add pipeline/synthetic_village/environment_module.py
git add tests/test_synthetic_village_environment_module.py
git commit -- pipeline/synthetic_village/environment_module.py \
             tests/test_synthetic_village_environment_module.py
```

提交消息尾行保留：

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

（用户未要求 push；按约定 push 时机需协调。）

## 引用

- HANDOFF-OPUS-007 全文：`handoff/HANDOFF-OPUS-007-batch6-modules-productionization.md`
- 006 回执（前置）：`handoff/FEEDBACK-HANDOFF-OPUS-006.md`
- 接管声明：`handoff/FEEDBACK-HANDOFF-GLM-001.md`
- 实现源：[pipeline/synthetic_village/environment_module.py](file:///d:/vibecoding/nantai/pipeline/synthetic_village/environment_module.py)
- TDD 源：[tests/test_synthetic_village_environment_module.py](file:///d:/vibecoding/nantai/tests/test_synthetic_village_environment_module.py)
