# REVIEW-OPUS-003 — `reciprocal_route_module.py` + `reciprocal_route_module_runtime.py` fail-closed audit

> 审计方：Opus self-audit（与 REVIEW-OPUS-001 / REVIEW-OPUS-002 同一序列）
> 日期：2026-07-21
> 审计对象：
>   - `pipeline/synthetic_village/reciprocal_route_module.py`（1275 行，HANDOFF-OPUS-009 Phase 1）
>   - `pipeline/synthetic_village/reciprocal_route_module_runtime.py`（496 行，HANDOFF-OPUS-009 Phase 2）
> 对应 TDD：`tests/test_synthetic_village_reciprocal_route_module.py`（29 测试）+
>   `tests/test_synthetic_village_reciprocal_route_module_runtime.py`（22 测试）
> 当前状态：51 测试全过，279 相邻模块测试无 regression（2 skipped，OpenEXR 模块未装）

## 总览

| 层 | 文件 | 行数 | 审计环节 | 通过 | 发现 |
|---|---|---:|---:|---:|---|
| Plan layer | `reciprocal_route_module.py` | 1275 | 7 | 7 | 1 INFO |
| Runtime schema | `reciprocal_route_module_runtime.py` | 496 | 8 | 8 | 1 INFO + 2 LOW |

**结论：两层均 fail-closed，无 fail-open 风险。** 三条 finding 都不会泄漏信任，
不影响 modeled-unverified Literal 锁定，可后续清理。

---

## Plan layer 审计（`reciprocal_route_module.py`）

### 环节 1：Schema 标识 / Literal 锁定 / FrozenModel

通过。所有 trust 字段都被 `Literal` 锁定，不可在实例里改值：

```python
synthetic: Literal[True] = True
geometry_usability: Literal["preview-only"] = "preview-only"
verification_level: Literal["L0"] = "L0"
metric_alignment: Literal[False] = False
real_photo_textures: Literal[False] = False
geometry_trust: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
trust_effect: Literal["none"] = "none"
```

`schema_version`、`plan_id`、`recipe_version`、4 个 Batch SHA 全部 `Literal` 锁定。
任何值变更都会因 `Literal` 验证失败而拒绝；任何字段新增因 `extra='forbid'` 拒绝。
`frozen=True` 防止 mutation；`strict=True` 拒绝 list/非预期类型。
6 个 `module_id` 的取值集合也由 `ModuleId` Literal 锁定。

### 环节 2：Instance ID segment partition（176..218）

通过。`ReciprocalRouteModulePart._instance_in_module_segment` validator 强制
每个 part 的 instance_id 必须落在其 module 的 `range(...)` 内：

```python
CENTRAL_DOWNHILL_INSTANCE_RANGE = range(176, 183)   # 7 parts
BRIDGE_CROSSING_INSTANCE_RANGE = range(183, 189)    # 6 parts
WATERMILL_TAILRACE_INSTANCE_RANGE = range(189, 196) # 7 parts
GALLERY_UNDERPASS_INSTANCE_RANGE = range(196, 205)  # 9 parts
FOREST_BOUNDARY_INSTANCE_RANGE = range(205, 212)     # 7 parts
LOWER_VALLEY_UPHILL_INSTANCE_RANGE = range(212, 219) # 7 parts
# 合计 7+6+7+9+7+7 = 43
```

Plan 顶层 `_modules_are_exact_and_ordered` 再校验：
- modules 顺序与 6 元组 expected 一致
- 跨 module 的 instance_id 唯一
- 跨 module 的 part_id 唯一
- 全部 instance_id 集合 = `set(range(176, 219))`

不会出现 segment overlap / gap / 错位。改任一 range 数字 → plan SHA 变 →
下游 render_id 变（如果绑了）。

### 环节 3：SHA-256 绑定（transitive provenance）

通过。`ReciprocalRouteModulePlan` 顶层绑定：

- `scene_plan_sha256: Sha256`
- `elevated_topology_sha256: Sha256`
- `environment_module_plan_sha256: Sha256`
- `batch8_release_manifest_sha256: Literal[BATCH8_RELEASE_MANIFEST_SHA256]`
- `batch8_archive_sha256: Literal[BATCH8_ARCHIVE_SHA256]`
- `batch9_release_manifest_sha256: Literal[BATCH9_RELEASE_MANIFEST_SHA256]`
- `batch9_archive_sha256: Literal[BATCH9_ARCHIVE_SHA256]`

每个 `ReciprocalRouteModule` 再绑定自己的
`batch8_design_source_sha256` 和 `batch9_design_source_sha256`，
通过 `_module_batch8_source` / `_module_batch9_source` 硬编码 lookup
强制 SHA 与 module_id 一一对应。改任一 module 的 SHA →
`_recipe_matches_module` validator 拒绝。

所有 SHA 字段都满足 `^[0-9a-f]{64}$` pattern（`Sha256` 类型约束）。

### 环节 4：默认 recipe 构造器（_default_module）

通过。`_default_module(module_id)` 按分支返回 `ReciprocalRouteModule`，
其 part_specs 是硬编码 4 元组 `(part_id, instance_id, semantic_id, material_slot_id)`，
没有从图片/外部文件/参数推导。任何字段变更 → plan SHA 变。

`build_default_reciprocal_route_module_plan` 计算 scene/topology/env-module
的 canonical SHA 后注入 plan。SHA 通过
`canonical_scene_plan_bytes` / `canonical_elevated_topology_bytes` /
`canonical_environment_module_plan_bytes` 计算，三个函数都是
`json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True) + "\n"`，
所以跨进程确定性。

最后 `plan.model_copy(update={})` 触发完整 re-validation，
确保返回的 plan 真的通过了所有 validator。

### 环节 5：plan_sha256 content-addressing

通过。`canonical_reciprocal_route_module_plan_bytes(plan)` 调用
`_canonical(plan.model_dump(mode="json"))`，输出与 plan 实例完全绑定。
`reciprocal_route_module_plan_sha256` 对其做 SHA-256。

`_canonical` 使用 `sort_keys=True`，所以字段顺序与代码顺序无关 ——
跨 Python 版本与跨进程都给出相同字节。`"\n"` 结尾保证文件可读且末字节确定。

### 环节 6：verify_reciprocal_route_module_plan

通过。`verify_reciprocal_route_module_plan(plan, *, scene, elevated_topology,
environment_module_plan)` 重新计算三个 canonical SHA 并与 plan 声称值比对。
任何一项不一致 → `ReciprocalRouteError`。

最后用 `ReciprocalRouteModulePlan.model_validate_json(canonical_bytes)` 重新
parse 自己的 canonical 输出，确保 plan 是 round-trip 自洽的 ——
即 canonical bytes 能重新构造出完全相同的 plan。这一步实际上重跑所有
`model_validator`，包括 6 module 顺序、instance segment、part_id unique 等。
`revalidated != plan` → 拒绝。

### 环节 7：6 个 recipe 内部 validator

通过。每个 recipe 都有 `_attachments_unique` / `_branches_unique` /
`_orchard_ids_unique` / `_waterwheel_ids_unique` / `_recipe_matches_module`
等内部 validator，确保：
- `bound_path_networks` 唯一（不能重复 path-network-002/003）
- `bound_orchard_object_ids` 唯一
- `bound_waterwheel_part_ids` 唯一且 6 个
- `upper_branch.branch == "upper"` 等（防呆）

`BridgeRouteAttachmentSpec.upstream_or_downstream` 通过
`BridgeDeckCrossingRecipe._attachments_unique` 强制 upstream/downstream
不可互换。

### Plan layer finding

#### INFO-1：`ReciprocalRouteModulePart.semantic_id` 范围与 canary `ObjectRegistryEntry` 不完全一致

`reciprocal_route_module.py:527`:

```python
semantic_id: int = Field(ge=0, le=14)
```

`canary.py:332`:

```python
class ObjectRegistryEntry(FrozenModel):
    semantic_id: int = Field(ge=3, le=255)
```

Plan 允许 `semantic_id ∈ [0, 14]`，但 canary ObjectRegistryEntry 要求
`semantic_id ∈ [3, 255]`。即 plan schema 接受 `0/1/2`，但这样的 part 进入
object_registry 会被 canary 拒绝。

实际影响：`SEMANTIC_ID_BY_CLASS` 用 `enumerate(SEMANTIC_ORDER, start=3)` 枚举，
所以 default plan 的所有 part 的 `semantic_id ∈ [3, 3+len(SEMANTIC_ORDER)-1]`。
12 个 SEMANTIC_ORDER class → `semantic_id ∈ [3, 14]`，不会触发 canary 拒绝。

但一个手写的非 default plan 可以填入 `semantic_id=0`，通过 plan 验证后
无法进入 runtime request 的 object_registry（runtime 的 instance 175..217 是
plan-derived，由 `ReciprocalRouteRuntimeRequest._identities_are_exact` 第 6 道比对
plan_part.semantic_id 与 registry_row.semantic_id，所以 plan 不会拿到不一致
的 registry —— plan 与 registry 是绑定的）。

更准确说：plan schema 允许 0/1/2，但任何 caller 在构造 registry 时会被
canary `ObjectRegistryEntry` 拒绝；所以 plan schema 的 `ge=0` 是宽松约束，
运行时不会 fail-open。建议未来收紧为 `ge=3, le=14` 与 canary 对齐。

**等级：INFO**（无 fail-open 风险，default plan 不触发，运行时已被 canary 兜底）

---

## Runtime schema layer 审计（`reciprocal_route_module_runtime.py`）

### 环节 1：Schema 标识 / Literal 锁定 / FrozenModel

通过。`ReciprocalRouteRuntimeRequest` 与 `ReciprocalRouteBuildReport`
所有 trust 字段都是 `Literal`：

```python
synthetic: Literal[True] = True
verification_level: Literal["L0"] = "L0"
geometry_usability: Literal["preview-only"] = "preview-only"
stage: Literal["modeled-unverified"] = "modeled-unverified"
trust_effect: Literal["none"] = "none"
requested_artifact: Literal["village-reciprocal-route.blend"]
```

`ReciprocalRouteBuildCounts`：
- `base_canonical_roots: Literal[175]`
- `module_canonical_roots: Literal[43]`
- `canonical_roots: Literal[218]`

`ReciprocalRouteBuildValidation` 全部 5 个字段 `Literal[True]`，
runtime script 必须通过每个或拒绝 emit report。

`ReciprocalRouteArtifact.name` 与 `.kind` 也 Literal 锁定。

### 环节 2：ReciprocalRouteRuntimeRequest 7 道 validator

通过。`_identities_are_exact` model_validator：

1. `reciprocal_route_module_plan_sha256` 必须等于
   `reciprocal_route_module_plan_sha256(self.reciprocal_route_module_plan)`
   的实测值
2. `object_registry` instance_id 必须是 `1..218` 连续
3. `object_registry` object_id 必须唯一（218 个）
4. `base_object_registry_sha256` 必须等于
   `_canonical_registry_sha256(self.object_registry[:175])` 重算
5. `base_environment_module_plan_sha256` 必须等于
   `self.reciprocal_route_module_plan.environment_module_plan_sha256`
   （transitive binding：runtime request 必须与 plan 内嵌的 env-module plan SHA 一致）
6. 模块段（instance 175..217）的每个 `(object_id, instance_id, semantic_id,
   material_id)` 必须与 plan 推导的注册表逐项匹配；material_id 通过
   `material_bindings` 解析 plan 的 `material_slot_id` 得到
7. `build_id` 必须等于 `SHA-256(canonical payload 除 build_id 外)`

任何一道失败 → `ValidationError`。所有 SHA 字段都满足 `^[0-9a-f]{64}$`。

第 5 道（transitive env-module SHA）是关键：
request 不直接读 env-module plan bytes，而是依赖 plan 内嵌的 SHA。
这意味着如果 plan 是伪造的（即 `reciprocal_route_module_plan_sha256` 与
plan 不匹配），第 1 道就会拒绝。所以第 5 道实质是「plan 自己说它的 env-module
SHA 是 X，request 也说 base env-module SHA 是 X」的一致性检查。Plan 自身的
`environment_module_plan_sha256` 必须由 caller 用真实 env-module plan bytes 计算。

### 环节 3：ReciprocalRouteBuildReport 自校验

通过。`_registry_is_complete` model_validator 只校验
`object_registry` instance_id 是 `1..218` 连续。其它字段（material_id /
object_id / material_bindings / counts / validation / artifact）的
精确一致性靠 `verify_reciprocal_route_build_report` 的 9 对 identity pair
比对兜底。

这是合理的分层：schema 自校验防呆，verifier 是身份比对最终防线。
Report 自身可以「形式上合法」（instance 连续），但 verifier 会拒绝
任何与 request 不一致的 report。

### 环节 4：verify_reciprocal_route_build_report（9 对 identity + 1 measured bytes）

通过。

```python
identity_pairs = (
    (report.build_id, request.build_id),
    (report.base_build_id, request.base_build_id),
    (report.base_build_report_sha256, request.base_build_report_sha256),
    (report.base_blend_sha256, request.base_blend_sha256),
    (report.base_environment_module_plan_sha256, request.base_environment_module_plan_sha256),
    (report.runtime_script_sha256, request.runtime_script_sha256),
    (report.reciprocal_route_module_plan_sha256, request.reciprocal_route_module_plan_sha256),
    (report.object_registry, request.object_registry),
    (report.material_bindings, request.material_bindings),
)
if any(left != right for left, right in identity_pairs):
    raise ReciprocalRouteRuntimeError("reciprocal-route build report identity disagrees")
```

第 8、9 对（object_registry / material_bindings）是元组逐项 `==` 比对，
所以每个 `ObjectRegistryEntry` 的 `(object_id, instance_id, semantic_id,
material_id, variant_id)` 与每个 `ReciprocalRouteMaterialBinding` 的
`(material_alias, runtime_slot_id, material_family, material_id)` 都必须
完全一致。

之后重算 `output_path` 的 SHA-256 + size_bytes，必须等于
`report.artifact.sha256` / `report.artifact.size_bytes`。
artifact.name 必须等于 `output_path.name`。

任何一项不一致 → `ReciprocalRouteRuntimeError`。

### 环节 5：load_reciprocal_route_build_report fail-closed

通过。

```python
- raw bytes 必须满足 0 < len(raw) <= MAX_BUILD_REPORT_BYTES (16 MiB)
- UTF-8 decode 失败 → ReciprocalRouteRuntimeError
- json.loads 用 object_pairs_hook=_reject_duplicate_keys 拒绝重复键
- 字节必须等于 _canonical_json_bytes(parsed) —— 非规范 JSON 拒
- model_validate_json(raw) 走 strict mode 校验
```

`except` clause 覆盖 `ReciprocalRouteRuntimeError`（reraise）+
`UnicodeDecodeError` / `json.JSONDecodeError` / `ValueError` /
`canary.CanaryBuildError`（wrap）。

### 环节 6：与 environment_module_runtime.py 的一致性

通过。两层 schema 共享同一组约定：
- `FrozenModel`：`extra='forbid', frozen=True, strict=True`
- `Sha256`：`^[0-9a-f]{64}$`
- `MaterialAlias`：`^material-[a-z0-9]+(?:-[a-z0-9]+)*$`
- `material_id: int = Field(ge=1, le=11)` —— 11-family root identity
- `_sha256_file` 流式哈希
- `_canonical_registry_sha256` canonical bytes 重算
- `MAX_ARTIFACT_BYTES` / `MAX_BUILD_REPORT_BYTES` 复用 canary 边界
- Pydantic strict mode 拒绝 list 作为 tuple，所以 tamper 类测试走
  `model_validate_json(json.dumps(payload))`

### 环节 7：_MATERIAL_BINDING_ROWS 复用 + 14-row alias 集合

通过。`reciprocal_route_module_runtime.py` 通过
`from .environment_module_runtime import _MATERIAL_BINDING_ROWS` 复用
14-row alias 表，避免双源真理。任何 alias 改名只在 environment-module
runtime 一处生效。

测试 helper `_material_bindings_reciprocal(base)` 用 base 175-root 的
`material_bindings`（即 environment-module runtime 给的 14 个 binding），
转成 `ReciprocalRouteMaterialBinding` —— 这是运行时复用的标准路径。

### 环节 8：runtime_script_sha256 占位符

通过。Phase 2 测试用 `"c" * 64` 作为 runtime_script_sha256，
因为 `scripts/blender/apply_reciprocal_route_modules.py` 尚未存在。
Schema 层 `Sha256` pattern 校验仍然要求 64-hex，所以测试值合规。
真实 SHA 待 Phase 3 实测后绑定到 `production_render_id`。

### Runtime schema layer findings

#### INFO-2：`ReciprocalRouteBuildReport._registry_is_complete` 只校验 instance_id 连续性

`reciprocal_route_module_runtime.py:374`:

```python
@model_validator(mode="after")
def _registry_is_complete(self) -> ReciprocalRouteBuildReport:
    if tuple(row.instance_id for row in self.object_registry) != tuple(
        range(1, 219),
    ):
        raise ValueError(
            "reciprocal-route build report registry is not exact 1..218",
        )
    return self
```

Report 自校验只验证 instance_id 连续。其它字段（material_id / object_id /
material_bindings）的精确性靠 `verify_reciprocal_route_build_report` 的
9 对 identity pair 比对兜底。

**等级：INFO**（合理分层：schema 防呆 + verifier 防伪；与
environment_module_runtime 行为一致）

#### LOW-1：`material_bindings` 用 `Field(min_length=1)` 而非 14-row 锁

`reciprocal_route_module_runtime.py:167`:

```python
material_bindings: tuple[
    ReciprocalRouteMaterialBinding, ...
] = Field(min_length=1)
```

对比 `environment_module_runtime.py:232`:

```python
material_bindings: tuple[EnvironmentModuleMaterialBinding, ...] = Field(
    min_length=len(_MATERIAL_BINDING_ROWS),
    max_length=len(_MATERIAL_BINDING_ROWS),
)
```

环境模块 runtime 把 material_bindings 长度严格锁定为 14
（与 `_MATERIAL_BINDING_ROWS` 行数一致）。reciprocal-route runtime
只要求 `min_length=1`，理论允许 1..N 个 binding。

实际影响分析：
- default plan 用了 14 个不同 alias（material-courtyard-stone-01 /
  material-courtyard-timber-01 / material-service-iron-01 /
  material-stone-block-01 / material-water-01 / material-waterwheel-wood-01 /
  material-waterwheel-iron-01 / material-creek-stone-01 / 等），
  所以 request 7 道 validator 中第 6 道（module part material_id 一致性）
  会要求 bindings 至少覆盖 plan 引用的所有 alias
- 但若一个非 default plan 只引用 8 个 alias，理论上 8 个 binding 也能通过
- 不会泄漏信任：所有 trust 字段 Literal-locked，material_id 限定 `ge=1, le=11`，
  9 对 identity pair 比对保证 request/report 完全一致
- 违反「与 environment-module runtime 一致」的工程约定

**等级：LOW**（不会 fail-open，但与同模块链路约定不一致；建议未来统一为
`Field(min_length=len(_MATERIAL_BINDING_ROWS), max_length=len(_MATERIAL_BINDING_ROWS))`）

#### LOW-2：`load_reciprocal_route_build_report` 的 except 不含 `ValidationError`

`reciprocal_route_module_runtime.py:463`:

```python
except (UnicodeDecodeError, json.JSONDecodeError, ValueError, canary.CanaryBuildError) as exc:
    raise ReciprocalRouteRuntimeError(
        "reciprocal-route build report validation failed",
    ) from exc
```

JSON 解析成功但 Pydantic `model_validate_json` schema 验证失败时
抛 `pydantic.ValidationError`，它是 `ValueError` 的子类，
所以实际上会被 `except ValueError` 捕获并 wrap 成 `ReciprocalRouteRuntimeError`。

实际验证：`pydantic.ValidationError` 继承链 →
`pydantic_core._pydantic_core.ValidationError` → `ValueError`。
所以现有 except 会覆盖。这是一个 **「看起来像 finding 但实际不是」** 的
审计点。文档记录用于未来回顾。

**等级：LOW**（已隐式覆盖；建议未来显式写出 `ValidationError` 以提高可读性）

---

## TDD 覆盖回归（与 audit 配套）

- Plan layer 29 测试：schema 常量 / default plan / canonical bytes /
  plan_sha256 / fail-closed（9 种 tamper 场景）/ verify / v1 immutability /
  trust invariant / 6 module order / instance segment / part_id unique
- Runtime schema 22 测试：schema constants / request 7 道 validator /
  canonical bytes / build_id canonical / request fail-closed（7 种 tamper）/
  report validation / report fail-closed（3 种）/ verify（5 种，含
  tampered artifact bytes / tampered module_plan_sha /
  tampered object_registry row / tampered material_binding）/
  load（3 种：round-trip / duplicate keys / non-canonical bytes）

51 测试全过；279 相邻模块测试无 regression。

## 整体结论

两层（plan + runtime schema）都满足 fail-closed 合同：

1. **没有 fail-open 风险**：所有 trust 字段 `Literal`-locked；
   material_id 通过 binding 解析，不允许 plan 声明未绑定 slot；
   build_id 是 canonical payload digest，不可伪造；
   report 通过 9 对 identity pair 与 measured bytes 重算兜底。
2. **provenance 不增不减**：modeled-unverified 不会被静默提升；
   design-only image SHA 仅作为 plan 字段绑定，不进入多视图训练证据。
3. **跨进程字节一致**：`_canonical` 用 `sort_keys=True + "\n"` 终止符，
   plan_sha256 跨进程确定。
4. **三条 finding 都不阻塞**：INFO-1 是 plan schema 宽松（canary 兜底）；
   INFO-2 是合理分层；LOW-1 是与 environment-module runtime 不一致的
   工程约定（未来收紧）；LOW-2 是 except 隐式覆盖（未来显式化）。

## 后续建议

1. LOW-1 可与下次触碰 reciprocal-route runtime 时一起修：把
   `material_bindings` 长度严格锁到 `len(_MATERIAL_BINDING_ROWS)`。
   不必单独发 commit，可与 Phase 3 改动合并。
2. INFO-1 可与下次触碰 plan schema 时一起修：把
   `ReciprocalRouteModulePart.semantic_id` 收紧为 `Field(ge=3, le=14)`。
3. LOW-2 显式化 `ValidationError`：可与 LOW-1 合并提交。

无需立即修复任何 finding。

Co-Authored-By: GLM-5.2 <noreply@zai.com>
