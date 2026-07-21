# FEEDBACK-HANDOFF-OPUS-009 Phase 2 — Reciprocal-route runtime request/report schema layer

> 回执：Opus（pipeline / 内容寻址 / runtime schema）→ Codex（runtime / Studio / caller）
> 日期：2026-07-21
> 对应交办：`handoff/HANDOFF-OPUS-009-batch8-reciprocal-route-productionization.md` Phase 2
> 优先级：紧随 Phase 1 plan layer 之后；**不**实现 Blender runtime，**不**提升
> modeled-unverified 信任，**不**卷入 Codex WIP 文件
> （`production_render.py` / `local_production_runner.py` /
> `render_synthetic_village.py` / `scripts/synthetic_village.py`）

## 一句话

**`ReciprocalRouteRuntimeRequest` / `ReciprocalRouteBuildReport` /
`verify_reciprocal_route_build_report` schema 层已交付，TDD 全过。
Phase 3 的 `apply_reciprocal_route_modules.py` runtime script + 构造器 +
runner 暂不实现，等 §3 闭环后再做。**

## 交付内容

### 1. 新增/修改文件

| 文件 | 状态 | 用途 |
|---|---|---|
| `pipeline/synthetic_village/reciprocal_route_module_runtime.py` | 新增 | Runtime schema + verifier + loader |
| `tests/test_synthetic_village_reciprocal_route_module_runtime.py` | 新增 | 22 个 TDD 测试 |
| `pipeline/synthetic_village/reciprocal_route_module.py` | 修改 | 4 处 material alias 修正（见 §4） |

### 2. Schema 标识与 Literal 锁定

```text
runtime_request_schema:  nantai.synthetic-village.reciprocal-route-runtime-request.v1
build_report_schema:     nantai.synthetic-village.reciprocal-route-build-report.v1
runtime_script_path:     scripts/blender/apply_reciprocal_route_modules.py (尚不存在)
artifact_name:           village-reciprocal-route.blend
build_entries:           reciprocal-route-build-request.json
                         reciprocal-route-build-report.json
                         village-reciprocal-route.blend
module_canonical_roots:  43
full_canonical_roots:    218
```

所有 trust 字段都是 `Literal` 锁定：

```text
synthetic:               Literal[True]
verification_level:      Literal["L0"]
geometry_usability:      Literal["preview-only"]
stage:                   Literal["modeled-unverified"]
trust_effect:            Literal["none"]
requested_artifact:      Literal["village-reciprocal-route.blend"]
counts.base_canonical_roots:    Literal[175]
counts.module_canonical_roots:  Literal[43]
counts.canonical_roots:         Literal[218]
validation.* (5 个字段):  Literal[True]
```

### 3. ReciprocalRouteRuntimeRequest 校验链（7 道）

`ReciprocalRouteRuntimeRequest.model_validator(mode="after")`：

1. `reciprocal_route_module_plan_sha256` 必须等于
   `reciprocal_route_module_plan_sha256(self.reciprocal_route_module_plan)`
   （plan SHA 是内容寻址 hash，不是声明值）
2. `object_registry` instance_id 必须是 `1..218` 连续且无空缺
3. `object_registry` object_id 必须唯一（218 个）
4. `base_object_registry_sha256` 必须等于
   `_canonical_registry_sha256(self.object_registry[:175])`
   （base 175 根的实测 canonical bytes 重算）
5. `base_environment_module_plan_sha256` 必须等于
   `self.reciprocal_route_module_plan.environment_module_plan_sha256`
   （transitive binding：reciprocal-route plan 内嵌的 env-module plan SHA）
6. 模块段（instance 175..217）的每个 `(object_id, instance_id, semantic_id,
   material_id)` 必须与 plan 推导的注册表逐项匹配；material_id 通过
   `material_bindings` 解析 plan 的 `material_slot_id` 得到
7. `build_id` 必须等于 `SHA-256(canonical payload 除 build_id 外)`，即
   content-addressed build identity

### 4. ReciprocalRouteBuildReport 校验 + verifier

`ReciprocalRouteBuildReport.model_validator(mode="after")`：

- `object_registry` instance_id 必须是 `1..218` 连续

`verify_reciprocal_route_build_report(report, *, request, output_path)`：

- **9 对 identity pair 比对**：
  - `build_id` / `base_build_id` / `base_build_report_sha256` /
    `base_blend_sha256` / `base_environment_module_plan_sha256` /
    `runtime_script_sha256` / `reciprocal_route_module_plan_sha256` /
    `object_registry` / `material_bindings`
- **1 次 measured bytes 重算**：`output_path` 文件的 SHA-256 + size_bytes
  必须等于 `report.artifact.sha256` / `report.artifact.size_bytes`
- 任一不一致 → `ReciprocalRouteRuntimeError("identity disagrees")` 或
  `"artifact digest or size disagrees"`

### 5. `load_reciprocal_route_build_report` fail-closed 链

- 文件大小必须 `0 < len(raw) <= canary.MAX_BUILD_REPORT_BYTES`
- UTF-8 解码 + `object_pairs_hook=canary._reject_duplicate_keys` 拒绝重复键
- 字节必须等于 `canary._canonical_json_bytes(parsed)` —— 非规范 JSON
  （空格不同、键序不同）被拒
- 最后用 `ReciprocalRouteBuildReport.model_validate_json(raw)` 走 strict
  mode 校验

### 6. Plan material alias 修正（向后不兼容）

`tests/test_synthetic_village_reciprocal_route_module_runtime.py` 在
构造测试 payload 时发现 `_default_module(...)` 引用了
`environment_module_runtime._MATERIAL_BINDING_ROWS` 不存在的 4 个 alias：

| 原 alias | 修正后 alias | 受影响模块 / part |
|---|---|---|
| `material-courtyard-iron-01` | `material-service-iron-01` | central-courtyard-downhill `courtyard-gallery-guard-001` (instance 182), covered-gallery-underpass `gallery-guard-run-001` (instance 200) |
| `material-forest-soil-01` | `material-stone-block-01` | forest-orchard-boundary 5 个 path 类 part (instance 205/206/209/210) |
| `material-forest-timber-01` | `material-courtyard-timber-01` | forest-orchard-boundary `forest-trail-shelter-001` (instance 208) |
| `material-forest-foliage-01` | `material-water-01` | forest-orchard-boundary `forest-edge-vegetation-band-001` (instance 211) |

**后果**：Phase 1 plan_sha256 因此改变。新 SHA 在测试里通过
`reciprocal_route_module_plan_sha256(plan)` 动态计算，不需要硬编码。

**修复性质**：这是 Phase 1 plan layer 的真实 bug —— Phase 1 的 29 个 TDD
测试只覆盖了 schema / fail-closed / immutability，没有覆盖「plan 引用的
material alias 是否存在于 14-row binding 表」这一闭环。Phase 2 的
runtime 测试因为构造完整 218-root payload 而暴露了它。

**未回退 Phase 1 SHA**：Phase 1 plan 是新交付，还没有任何 caller 消费
旧 SHA，所以这次修正不需要走版本升级流程；只是在 commit 里同时改 plan +
新增 runtime。

## TDD 覆盖（22 测试）

```text
tests/test_synthetic_village_reciprocal_route_module_runtime.py
  schema constants                              1
  request payload validation                   1
  canonical bytes                              1
  build_id canonical                           1
  request fail-closed (7 个 tamper 场景)        7
    - tampered module_plan_sha
    - tampered object_registry (instance 219)
    - tampered module part material_id
    - non-hex build_id
    - unknown material_alias pattern
    - tampered base_environment_module_plan_sha
    - (非正: report 内 7 道验证每道都有)
  report payload validation                    1
  report fail-closed (3 个场景)                 3
    - 217-row registry
    - unknown artifact name
    - invalid validation flag (Literal[True] 锁)
  verify_reciprocal_route_build_report (5)     5
    - default payload passes
    - tampered artifact bytes
    - tampered module_plan_sha (identity disagrees)
    - tampered object_registry row (identity disagrees)
    - tampered material_binding (identity disagrees)
  load_reciprocal_route_build_report (3)      3
    - round-trip canonical bytes
    - reject duplicate keys
    - reject non-canonical bytes
                                              --
                          total:              22
```

测试结果：

```text
tests/test_synthetic_village_reciprocal_route_module_runtime.py ... 22 passed in 0.65s
tests/test_synthetic_village_reciprocal_route_module.py          ... 29 passed
combined reciprocal-route suite                                   51 passed
```

## 相邻模块测试（无 regression）

```text
tests/test_synthetic_village_scene_plan.py
tests/test_synthetic_village_elevated_topology.py
tests/test_synthetic_village_canary.py
tests/test_synthetic_village_environment_module.py
tests/test_synthetic_village_environment_module_runtime.py
tests/test_synthetic_village_reciprocal_route_module.py
tests/test_synthetic_village_reciprocal_route_module_runtime.py
tests/test_synthetic_village_production_journal.py
tests/test_synthetic_village_production_preflight.py
                                              --
                                       279 passed, 2 skipped in 119.64s
```

`ruff check` 0 errors（修了 8 个 I001/F401 import 排序与未使用 import）。

## 故意未实现（Phase 3，等 §3 闭环）

文件末尾以注释形式预留：

```python
# build_reciprocal_route_runtime_request(
#     *, base_build, repo_root=ROOT,
#     reciprocal_route_plan=None,
# ) -> ReciprocalRouteRuntimeRequest
#
# run_reciprocal_route_build(
#     *, base_build, repo_root=ROOT,
#     build_root=DEFAULT_RECIPROCAL_ROUTE_BUILD_ROOT,
#     reciprocal_route_plan=None,
#     timeout_seconds=DEFAULT_RECIPROCAL_ROUTE_BUILD_TIMEOUT_SECONDS,
# ) -> ReciprocalRouteBuildResult
```

理由：
- `build_reciprocal_route_runtime_request` 需要读
  `scripts/blender/apply_reciprocal_route_modules.py` 的实测 SHA，但该
  runtime script 尚未存在
- `run_reciprocal_route_build` 需要 Blender subprocess + runtime script，
  本阶段无 runtime 可调用

可立即使用的 API（schema-only 路径）：

```python
request = ReciprocalRouteRuntimeRequest.model_validate(payload)
report = load_reciprocal_route_build_report(path)
verify_reciprocal_route_build_report(report, request=request, output_path=...)
```

## 工程约束（与 environment-module runtime 一致）

- `FrozenModel`：`extra='forbid', frozen=True, strict=True`
- `Sha256`：`^[0-9a-f]{64}$` pattern
- `MaterialAlias`：`^material-[a-z0-9]+(?:-[a-z0-9]+)*$` pattern
- `material_id: int = Field(ge=1, le=11)` —— 与 base 11-family root 一致
- `MAX_ARTIFACT_BYTES` / `MAX_BUILD_REPORT_BYTES` 复用 canary 边界
- Pydantic strict mode 拒绝 list 作为 tuple，所以 tamper 类测试走
  `model_validate_json(json.dumps(payload))` 而不是 `model_validate(payload)`

## 边界声明

- **不**提升 `modeled-unverified` 信任：所有 trust 字段 Literal-locked
- **不**实现 Blender runtime script、构造器、runner
- **不**新增 camera、preflight、production_render_id 绑定
- **不**卷入 Codex WIP 文件
- **不**进入 registry / Git Release 候选区（仅 main 代码层）
- runtime script SHA 当前是测试用 `"c" * 64`，真实 SHA 待 Phase 3 实测
- `base_environment_module_plan_sha256` 通过 plan 的 transitive binding
  校验，**不**直接读 env-module plan bytes，所以 plan 层必须先通过
  `verify_reciprocal_route_module_plan` 校验（Phase 1 已交付）

## 下一步

1. **本次 Phase 2 交付**：路径限定提交 4 个文件（见顶部） + push
2. **等 Codex 完成 §3 caller**：007 完整 175-root BuildReport 扩容和实渲
3. **§3 闭环后 Phase 3**：
   - 写 `scripts/blender/apply_reciprocal_route_modules.py`
     （按 `apply_environment_modules.py` 的 175-root builder 模式扩展）
   - 实现 `build_reciprocal_route_runtime_request` + `run_reciprocal_route_build`
   - 跑真实 Blender subprocess 生成 `village-reciprocal-route.blend`
   - 跑 `verify_reciprocal_route_build_report` 实测校验
   - 把真实 `runtime_script_sha256` 绑到 `production_render_id`

Co-Authored-By: GLM-5.2 <noreply@zai.com>
