# HANDOFF-CODEX-009 — Blender runtime 测试遗留 24 项 schema 不兼容

> 发起：GLM（Opus 接替 lane）→ Codex
> 日期：2026-07-20
> 优先级：MEDIUM（测试门未绿，但不阻塞 plan-level 工作）
> 路径：`tests/test_synthetic_village_blender_runtime.py`、`pipeline/synthetic_village/canary.py`

## 背景

在接替 Opus lane 推进 HANDOFF-OPUS-007 plan-level 工作时，跑完整 synthetic-village
测试集发现 `tests/test_synthetic_village_blender_runtime.py` 中存在 25 个预存失败
（不是我引入的回归）。已修复 1 项（commit pending），剩余 24 项是 Codex 提交
`cc371a4 feat(camera): measure production layer counts` 引入 schema 扩容后的
遗留测试 bug，按 AGENTS.md「触及 renderer/runtime/journal 时必须先协调」属于
Codex lane。

## 已修复（GLM，1 项）

```text
test_runtime_rejects_relative_argv_before_path_resolution
```

`scripts/blender/build_synthetic_village.py:5638` 的错误信息在引入 textured build
模式时由 `'request and staging paths must be absolute'` 改为
`'request, material, and staging paths must be absolute'`，但测试断言未同步。
修复方式：更新断言字符串并补一段注释说明 builder 同时支持 legacy 和 textured
argv 形状、绝对路径守卫覆盖三条路径。

该修复只触及一个字符串字面量，**未触及** renderer / runtime / journal 合同。

## 未修复（移交 Codex，24 项）

### 失败列表

```text
test_runtime_registry_requires_exact_renderable_coverage
test_runtime_rejects_scene_contract_tampering_before_staging[root-id]
test_runtime_rejects_scene_contract_tampering_before_staging[material-id]
test_runtime_rejects_scene_contract_tampering_before_staging[variant-id]
test_runtime_rejects_scene_contract_tampering_before_staging[parent]
test_runtime_rejects_scene_contract_tampering_before_staging[hide-render]
test_runtime_rejects_scene_contract_tampering_before_staging[hide-get]
test_runtime_rejects_scene_contract_tampering_before_staging[collection-exclude]
test_runtime_rejects_scene_contract_tampering_before_staging[extra-auxiliary]
test_runtime_rejects_scene_contract_tampering_before_staging[unclassified]
test_runtime_rejects_scene_contract_tampering_before_staging[auxiliary-semantic]
test_runtime_rejects_scene_contract_tampering_before_staging[auxiliary-id]
test_runtime_rejects_scene_contract_tampering_before_staging[auxiliary-name]
test_runtime_rejects_scene_contract_tampering_before_staging[auxiliary-hidden]
test_runtime_rejects_scene_contract_tampering_before_staging[world-id]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-lens]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-type]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-sensor-fit]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-sensor-width]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-shift-x]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-shift-y]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-clip-start]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-clip-end]
test_runtime_rejects_scene_contract_tampering_before_staging[camera-dof]
```

### 根因

24 项失败的根因相同：测试通过 `_formal_render_request()`（位于
`tests/test_synthetic_village_blender_runtime.py:277`）加载 `FORMAL_BLEND` 的
`build-report.json`，但 `FORMAL_BLEND` 指向的 canary 目录：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/canary/
  344e643c81753e986d8945ca2b4a8713f26efedc755ab2055bd4235b1c656d1b/
  build-report.json
```

是 **elevated topology 扩容之前** 的 build，其 report 内容为：

```text
canonical_roots: 126
object_registry:  126 entries
semantic_registry: 14 entries
（无 elevated_topology_sha256 字段）
```

当前 BuildReport schema（`pipeline/synthetic_village/canary.py:949`）要求：

```text
canonical_roots: Literal[130]
object_registry:  Field(min_length=130, max_length=130)
semantic_registry: Field(min_length=15, max_length=15)
source_hashes.elevated_topology_sha256: 必填
```

`_formal_render_request()` 调用 `canary.load_build_report()` 加载该 report
时立即抛 `CanaryBuildError: build report validation failed: 4 validation errors`。
所有 24 个测试在进入主体断言之前就 fail 了。

### 同一目录下的 `PRODUCTION_BLEND` 不是替代品

`PRODUCTION_BLEND`（`4f38ecf4...`）虽然满足 `canonical_roots=130` 和
`elevated_topology_sha256`，但它是 **TexturedBuildReport**，包含 BuildReport
schema **没有** 的字段：

```text
visual_slot_registry: 68 entries（BuildReport 期望 44，不允许 extra）
counts.glb_embedded_images / glb_primitives / glb_tangent_primitives /
  glb_textures / glb_triangles / glb_uv_primitives  （extra_forbidden）
material_bundle_manifest_sha256 / material_bundle_id / material_algorithm_id /
  building_geometry_profile_id / surface_realism_profile_id /
  material_input_registry  （extra_forbidden）
```

因此直接把 `FORMAL_BLEND` 改成 `PRODUCTION_BLEND` 路径会让 `load_build_report`
在另一组字段上 fail（已实测）。

### `test_runtime_registry_requires_exact_renderable_coverage` 的差异

此项不在 `_formal_render_request()` 路径上，直接读
`FORMAL_BLEND/build-report.json` 做 `_validate_object_registry_contract`。
renderer probe 脚本启动时 Blender 退出码 17，原因是 report JSON 里
`object_registry` 只有 126 项，schema 校验在 `_validate_object_registry_contract`
内部失败。本质根因和其它 23 项相同。

## 推荐修复方案

按风险从低到高，**任选其一**：

### 方案 A（推荐）：重写 `_formal_render_request()` 走 textured 路径

1. 把 `_formal_render_request()` 改名为 `_textured_render_request()` 或
   保留旧名但内部改用 `canary.load_textured_build_report(PRODUCTION_BLEND.parent
   / "build-report.json")` 和 `canary.build_textured_canary_request(...)`。
2. `RenderFrameRequest` 的 `build_id` / `object_registry` / `auxiliary_registry` /
   `semantic_registry` / `measured_c2w_blender` 改从 textured report 取。
3. 24 个失败测试的 `FORMAL_BLEND` 引用改为 `PRODUCTION_BLEND`。
4. `test_runtime_registry_requires_exact_renderable_coverage` 的
   `report_path = FORMAL_BLEND.with_name("build-report.json")` 改为
   `PRODUCTION_BLEND.with_name("build-report.json")`，并把
   `_validate_object_registry_contract` 的调用换成 textured 路径。

预期影响：测试逻辑完全不变，只是换了 schema 兼容的 build。

### 方案 B：另起一个 legacy BuildReport canary

1. 用 builder legacy 路径（不带 `--materials`）重新生成一个 `village-canary.blend`
   和 `build-report.json`，写到新目录 `4f38ecf4.../legacy-canary/`。
2. `FORMAL_BLEND` 指向该新目录；保持 `_formal_render_request()` 走
   `load_build_report()`。
3. 风险：legacy builder 路径可能不再被维护，且要确认新 legacy build 真的产生
   `canonical_roots=130`（否则等价于现状）。

### 方案 C：把 `BuildReport` schema 改为 `min_length=126, max_length=130`

不行。这违反 fail-closed：schema 当前硬锁 `Literal[130]` 是为了让任何
registry 缺失立即报错。降到 126 等于静默接受 build 缺 elevated topology。
**不要选这个。**

## 验证命令

```powershell
d:\vibecoding\nantai\.venv\Scripts\python.exe -m pytest `
  tests/test_synthetic_village_blender_runtime.py --tb=short -q
```

修复后期望：**37 passed, 0 failed, 3 skipped**（3 个 skipped 是
`NANTAI_RUN_BLENDER_RUNTIME_TESTS=1` 才跑的端到端真实渲染测试）。

## 边界与 provenance

- 本回执未触碰 `web/studio/`、`web/viewer/`、`pipeline/studio_server.py`。
- 本回执未修改 `pipeline/synthetic_village/canary.py` 的任何 schema 字段。
- 1 项已修复（error message 字符串）属于独立字符串字面量，未触及 renderer
  合同；24 项未修复项明确移交 Codex lane。
- 若 Codex 选择方案 A，须先确认 `canary.build_textured_canary_request` /
  `canary.load_textured_build_report` 的签名是否兼容
  `RenderFrameRequest` 现有字段；若不兼容需要 Codex 自行评估是否同时改
  `RenderFrameRequest` schema。

## GLM 当前提交状态

```text
pipeline/synthetic_village/production_repose.py            (已提交 d8f009a)
tests/test_synthetic_village_production_repose.py           (已提交 d8f009a)
pipeline/synthetic_village/environment_module.py            (已提交 5b0a066)
tests/test_synthetic_village_environment_module.py          (已提交 5b0a066)
handoff/FEEDBACK-HANDOFF-GLM-001.md                         (已提交 fdea3b9)
handoff/FEEDBACK-HANDOFF-OPUS-006.md                        (已提交 fdea3b9)
handoff/FEEDBACK-HANDOFF-OPUS-007.md                        (已提交 fdea3b9)
tests/test_synthetic_village_blender_runtime.py             (本次修复 1 项待提交)
handoff/HANDOFF-CODEX-009-blender-runtime-stale-formal-blend.md  (本回执)
```
