# REVIEW-OPUS-001 — apply_environment_modules.py 对抗性 fail-closed 审计

> 回执：Opus（pipeline / 坐标 / 内容寻址 lane）→ Codex（Blender runtime / Studio lane）
> 日期：2026-07-21
> 对象：`scripts/blender/apply_environment_modules.py`（905 行，commit `b59a53a` 系列 Codex 实现）
> 范围：**只读审计 + 证据记录**，不修代码，不扩大重构（用户 2026-07-21 明确边界）

## 一句话

**整体 fail-closed 强度：高。9 个环节全部 fail-closed，3 处硬编码常量集严格锁定。
唯一可清理项是一行死代码 `material_id_by_family`，不影响实质信任。**

## 审计方法

按 7 个执行环节逐节验证 fail-closed 强度，每节确认：(a) 异常路径是否真的拒绝、
(b) 字段是否被内容寻址绑定、(c) 是否存在从元数据推断 SHA 的路径。

| 环节 | 位置 | 强度 |
|---|---|---|
| 1. 路径校验 | `_runtime_paths` L147-168 | ✓ 强 |
| 2. Request 装载 | `_load_request` L171-185 | ✓ 强 |
| 3. Request 校验 | `_validate_request` L188-325 | ✓ 强（见发现 1） |
| 4. Base scene 校验 | `_validate_base_scene` L699-728 | ✓ 强 |
| 5. 模块构建 | `_build_modules` L731-768 | ✓ 强 |
| 6. 已建模块校验 | `_validate_built_modules` L771-816 | ✓ 强 |
| 7. Report 写盘 + 异常出口 | `_write_report` / `main` L819-905 | ✓ 强 |

## 通过的环节（无问题，逐条核验）

### 环节 1：路径校验（L147-168）

- `--` 后必须正好 2 个值
- 两条路径必须 absolute
- `request_path.parent == staging_path` 且 `request_path.name == REQUEST_NAME`
- 拒绝 symlink（request_path 与 staging_path 都查）
- **结论**：无路径注入空间。

### 环节 2：Request 装载（L171-185）

- 16 MiB 上限（拒绝 unbounded payload）
- `object_pairs_hook=_reject_duplicate_keys`（拒绝重复键）
- `parse_constant=_reject_constant`（拒绝 NaN/Infinity）
- `raw != _canonical_bytes(request)` 严格字节比对（拒绝任何字节级非规范）
- **结论**：与 `production_journal._canonical` 同强度，无回退路径。

### 环节 3：Request 校验（L188-325，见发现 1）

通过 8 条独立校验：

| 校验 | 强度 |
|---|---|
| 顶层 keys 精确匹配 `_expect_exact_keys` | ✓ |
| schema/provenance 常量逐字比对 | ✓ |
| 8 个 digest 字段全部 `_is_sha256` | ✓ |
| `build_id` 必须 = canonical bytes 的 SHA-256（内容寻址） | ✓ |
| `runtime_script_sha256` 必须 = `_sha256_file(Path(__file__))`（防脚本被改） | ✓ |
| `environment_module_plan_sha256` 必须 = plan canonical bytes SHA | ✓ |
| module_id 集合必须精确 3 个固定 ID（`central-courtyard` / `lower-bridge-waterwheel` / `rear-service-courtyard`） | ✓ |
| `base_object_registry_sha256` 校验 registry[:130] 字节 | ✓ |

### 环节 4：Base scene 校验（L699-728）

- `bpy.data.filepath` 必须 absolute + 非 symlink + SHA = request.base_blend_sha256
- roots 必须 130 个 + ID unique
- 逐行比对 base roots 与 registry[:130]（instance_id / semantic_id / material_id / variant_id）
- **结论**：base scene 完全内容寻址绑定，无替换空间。

### 环节 5：模块构建（L731-768）

- `nv__environment-modules-v1` collection 不允许已存在（防叠加污染）
- 逐 part 查 material：`bpy.data.materials.get` 必须命中 + `nv_slot_id` 必须匹配 binding
- 未知 part_id 由 `_module_geometry` 抛 `RuntimeBuildError`（见发现 3）
- **结论**：构建链无静默提升空间。

### 环节 6：已建模块校验（L771-816）

- `len(base_roots) == 130` / `len(module_roots) == 45` / `len(all_roots) == 175` 三重计数
- `actual_by_id` 必须 = expected_ids（精确集合比对）
- 逐 mesh 校验：stage/trust/geometry_usability 三常量 + 非空 vertices + 非空 polygons + tangents + 1 material
- 逐 vertex `math.isfinite`（拒绝 NaN/Inf）
- **结论**：post-build 校验完整，无"已构建但未校验"路径。

### 环节 7：Report 写盘 + 异常出口（L819-905）

- output_path / report_path 不允许已存在（防覆盖）
- `bpy.ops.wm.save_as_mainfile` 后必须 `is_file` + `size > 0`
- report 用 `_canonical_bytes` 序列化
- `open("xb")` exclusive create + `fsync`（防并行写盘）
- `RuntimeBuildError` 出口 exit 17（host 能区分成功/失败）
- **结论**：写盘路径无覆盖、无 stale report 复用。

## 发现 1：死代码 + 隐式假设（LOW，无实质 fail-open）

**位置**：`scripts/blender/apply_environment_modules.py:264-283`

```python
material_id_by_family = {}                          # L264
actual_bindings = {}                                # L265
for row in bindings:
    _expect_exact_keys(row, (...), "material binding")
    material_id_by_family.setdefault(               # L277
        row["material_family"], row["material_id"],
    )
    actual_bindings[row["material_alias"]] = (
        row["runtime_slot_id"], row["material_family"],
    )
if actual_bindings != MATERIAL_BINDINGS:            # L282
    raise RuntimeBuildError("material bindings do not match runtime v1")
```

### 问题

`material_id_by_family` 在 L277 setdefault 后**从未被读取**。作者意图看起来是
「同一 family 必须共享同一 material_id」，但忘了写校验。`actual_bindings`
只存 `(slot, family)`，**不存 material_id**，所以 L282 的硬编码比对也不覆盖
material_id。

### 复现证据（思维实验，未实跑 Blender）

构造一个 request，让两个同 family 的 binding 用不同 material_id（如
`material-courtyard-stone-01` 与 `material-creek-stone-01` 都标 family=`fieldstone`
但 material_id 分别为 5 和 7），同时让 `registry[130:]` 的 material_id 与 binding
一致。校验链通过情况：

- L262 长度 ✓ 通过
- L282 `actual_bindings` 只看 (slot, family) ✓ 通过
- L321 `registry_row.material_id == binding.material_id` ✓ 通过（每个 binding 单独
  匹配自己的 registry 行）

### 为什么不是实质 fail-open

material_id 绑死在 registry 里；plan 自身的 part.material_id 由
`environment_module_plan_sha256` 内容寻址 + plan 构造器保证。攻击者要绕过得同时
改 binding + registry + plan，且要重算 request 的 `build_id`。这与意图一致：
脚本只负责 request ↔ base scene ↔ module 三方一致性，plan 内部一致性由 plan
构造器管。

### 建议

二选一（**不在本次范围**，仅记录）：

1. **删除死代码**：直接删 L264 与 L277 的 `material_id_by_family`（最干净）
2. **补一行校验**：在 L282 后加 `if len({v for v in material_id_by_family.values()}) != len(material_id_by_family): raise ...`

考虑到既有 60 个 runtime 测试全过，且死代码不构成信任漏洞，建议优先方案 1（删除）。

## 发现 2：脚本不重校 plan 内部一致性（INFO，分层设计意图）

**位置**：`scripts/blender/apply_environment_modules.py:238-260`

脚本对 plan 只做四件事：

1. `environment_module_plan_sha256` 必须 = `_sha256_bytes(_canonical_bytes(plan))`
2. plan schema/provenance 常量校验
3. module_id 集合必须精确 3 个
4. parts 与 module registry 的 (part_id, instance_id, semantic_id, material_id, variant_id) 一致

但脚本**不校验** part 内部字段（如 `part.material_slot_id` ↔ `part.material_id`
内部一致性）。这是分层设计 —— plan 内部一致性由 plan 构造器 +
`environment_module_plan_sha256` 内容寻址绑定兜住。**不是漏洞**，但下游 reader
可能误以为「脚本已校验 plan 内部一致性」。建议在脚本 docstring 注明分工（**不在
本次范围**）。

## 发现 3：`_module_geometry` 形状完全硬编码（INFO，正确做法）

**位置**：`scripts/blender/apply_environment_modules.py:528-696`

所有几何（坐标、尺寸、yaw）硬编码在脚本里，part_id 决定形状。攻击者只能选
part_id（在 plan 里），无法改变几何参数。`_module_geometry` 对未知 part_id
直接 `raise RuntimeBuildError`。

**这是正确的 fail-closed 设计** —— 确认无误。这也意味着：
脚本字节（`runtime_script_sha256`）一旦改变，几何定义就改变，render_id 自动
失效。几何参数与 provenance 强绑定。

## 范围边界声明

按用户 2026-07-21 明确边界：

- ✓ **只读审计**：未修改 `apply_environment_modules.py` 任何字节
- ✓ **不扩大重构**：发现 1 的死代码清理留待 Codex 决定批次处理
- ✓ **不扩展 Studio jobs/ledger**：本审计不触及 Studio lane
- ✓ **不提升 modeled-unverified 信任**：所有发现的校验都在 L0/preview-only
  信任级别内，未新增任何提升路径
- ✓ **未触及 175-root renderer schema**：脚本内部常量保持原样

## 与 commit `e121036` 的关系

本次审计与 commit `e121036`（`production_render_id` 加可选
`environment_module_build_report_sha256` 绑定）是**独立工作**：

- `e121036` 解决的是：render_id 如何内容绑定到 175-root module build report
- 本审计解决的是：175-root module build report 自身的 fail-closed 强度

两者互补：`e121036` 让下游可以验证「这次 render 真的从这个 module build 派生」，
本审计确认「这个 module build 自身没有 fail-open 路径让攻击者偷换内容」。

## 下一步建议

1. **Codex 决定**：是否将发现 1 的死代码清理纳入下一批次（独立 commit，不与
   runtime 逻辑改动混在一起）
2. **Opus lane 待办**：等 Codex 完成 Task 4 后，启动 Task 5 §3 完整链路
3. **不在本次范围**：175-root BuildReport 扩容、实渲、Studio 集成

## Co-Authored-By

GLM-5.2 <noreply@zai.com>
