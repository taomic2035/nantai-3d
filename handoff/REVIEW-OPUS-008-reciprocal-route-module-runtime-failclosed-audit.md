# REVIEW-OPUS-008 — reciprocal_route_module_runtime.py fail-closed 对抗性审计

> 日期：2026-07-21
> 发起：Opus lane (GLM-5.2 临时接替)
> 审计对象：`pipeline/synthetic_village/reciprocal_route_module_runtime.py`（~930 行）
> 对应：HANDOFF-OPUS-009 Phase 3 交付
> 方法：逐环节对抗性审计，模拟攻击者尝试绕过每个 fail-closed 门
> 结论：**全部通过，无 fail-open 漏洞；2 INFO 观察保留为设计决策**

## 审计范围

| 组件 | 行号 | 类型 |
|---|---|---|
| `ReciprocalRouteRuntimeRequest` | 145-305 | FrozenModel + 7 道 validator |
| `ReciprocalRouteBuildCounts` | 314-326 | Literal-locked counts |
| `ReciprocalRouteBuildValidation` | 329-340 | 5 道 Literal[True] |
| `ReciprocalRouteArtifact` | 343-349 | sha256 + size_bytes |
| `ReciprocalRouteBuildReport` | 352-405 | FrozenModel + registry validator |
| `verify_reciprocal_route_build_report` | 408-456 | 9 道 identity pair + bytes recomputation |
| `load_reciprocal_route_build_report` | 459-496 | canonical JSON + duplicate key rejection |
| `build_reciprocal_route_runtime_request` | 552-684 | 构造器 |
| `run_reciprocal_route_build` | 779-928 | runner（subprocess + timeout + atomic） |

## 审计环节（8 项）

### 环节 1 — Request schema 7 道 validator ✅ PASS

**7 道 fail-closed 校验**（`_identities_are_exact`，lines 203-305）：

1. `reciprocal_route_module_plan_sha256` 必须匹配内嵌 plan 的实际 SHA ✅
2. `object_registry` instances 必须是精确的 1..218 ✅
3. Object IDs 必须唯一（218 个唯一）✅
4. `base_object_registry_sha256` 必须匹配 `object_registry[:175]` 的重新计算 SHA ✅
5. `base_environment_module_plan_sha256` 必须匹配内嵌 plan 的 `environment_module_plan_sha256` 字段（transitive binding）✅
6. Module-declared parts（indices 175..217）必须匹配 plan-derived registry：每个 part 的 `object_id`/`instance_id`/`semantic_id`/`material_id` 一一对应 ✅
7. `build_id` 必须是 canonical payload digest（排除自身）✅

**对抗性测试**：
- 注入 fake `base_object_registry_sha256` → validator 4 重新计算 `object_registry[:175]` 的 SHA 并比较，fake 值不匹配 ✅
- 在 `object_registry` 放 218 行但 base 部分（1..175）是假的 → validator 4 重新计算 base SHA，但此 SHA 只与 request 内嵌字段比较（自洽性），不与**真实的 verified 175-root build** 比较。**这是构造器的责任**——`build_reciprocal_route_runtime_request` 从真实 `base_build` 对象读取 `object_registry`，构造路径可信。schema 保证自洽，构造器保证真实性，verify helper 保证 request ↔ report 一致。✅
- 篡改 plan 但保持 SHA 不变 → 不可能，SHA 是 cryptographic hash ✅

**结论**：schema 层 fail-closed 完整。

### 环节 2 — Report schema ✅ PASS

**验证项**：
- `counts` Literal-locked: `base_canonical_roots=175`、`module_canonical_roots=43`、`canonical_roots=218` ✅
- `validation` 5 道 `Literal[True]`：runtime script 必须 pass 每条规则或拒绝 emit report ✅
- `artifact` 有 `sha256`（pattern）+ `size_bytes`（gt=0, le=MAX_ARTIFACT_BYTES）✅
- `_registry_is_complete` validator 检查 instances == 1..218 ✅
- 所有 trust 字段 Literal-locked: `synthetic=True`、`verification_level=L0`、`geometry_usability=preview-only`、`stage=modeled-unverified`、`trust_effect=none` ✅

**对抗性测试**：
- 在 report 放假 `artifact.sha256` → `verify_reciprocal_route_build_report` 重新读文件并计算 SHA 比较 ✅
- 在 report 放假 `object_registry` → verify helper 比较第 8 对 `report.object_registry == request.object_registry` ✅
- 篡改 `counts.canonical_roots` 为 219 → `Literal[218]` 拒绝 ✅

**结论**：report schema 完整。

### 环节 3 — Runtime script 自验证 SHA ✅ PASS

**审计点**：`apply_reciprocal_route_modules.py` 是否验证自己的 SHA？

**验证**（line 253）：
```python
if request["runtime_script_sha256"] != _sha256_file(Path(__file__)):
```

runtime script 在执行前验证自己的文件 SHA 与 request 声明的 SHA 一致。如果 script 被篡改，SHA 不匹配，拒绝执行。这符合 project_memory.md 记录的 fail-closed 模式（`probe_reciprocal_route_modules.py` 建立的模式）。

**结论**：runtime script 防篡改完整。

### 环节 4 — verify_reciprocal_route_build_report 9 道 identity pair ✅ PASS

**9 道 identity pair 比较**（lines 420-436）：

1. `report.build_id == request.build_id` ✅
2. `report.base_build_id == request.base_build_id` ✅
3. `report.base_build_report_sha256 == request.base_build_report_sha256` ✅
4. `report.base_blend_sha256 == request.base_blend_sha256` ✅
5. `report.base_environment_module_plan_sha256 == request.base_environment_module_plan_sha256` ✅
6. `report.runtime_script_sha256 == request.runtime_script_sha256` ✅
7. `report.reciprocal_route_module_plan_sha256 == request.reciprocal_route_module_plan_sha256` ✅
8. `report.object_registry == request.object_registry`（tuple 逐元素比较）✅
9. `report.material_bindings == request.material_bindings`（tuple 逐元素比较）✅

**measured bytes recomputation**（lines 441-456）：
- 重新读 `output_path` 文件，计算 SHA + size ✅
- 比较 `report.artifact.name == output_path.name` ✅
- 比较 `report.artifact.sha256 == digest` ✅
- 比较 `report.artifact.size_bytes == size` ✅

**结论**：verify helper 完整，无遗漏身份对。

### 环节 5 — load_reciprocal_route_build_report canonical JSON ✅ PASS

**审计点**：report 加载是否拒绝非 canonical JSON？

**验证**（lines 459-496）：
- 读取 raw bytes ✅
- 拒绝 empty 或超过 `MAX_BUILD_REPORT_BYTES` 的 report ✅
- `object_pairs_hook=canary._reject_duplicate_keys` 拒绝重复 JSON key ✅
- `raw == canary._canonical_json_bytes(parsed)` 验证 raw bytes 是 canonical JSON（排序+缩进+换行）✅
- `ReciprocalRouteBuildReport.model_validate_json(raw)` pydantic 验证 ✅

**对抗性测试**：
- 注入重复 key → `_reject_duplicate_keys` 拒绝 ✅
- 注入非 canonical 排序 → `raw != canonical_bytes` 拒绝 ✅
- 注入超大 report → `MAX_BUILD_REPORT_BYTES` 拒绝 ✅

**结论**：加载路径完整。

### 环节 6 — Runner subprocess + timeout + atomic publish ✅ PASS

**审计点**：runner 是否安全执行 Blender subprocess？

**验证**（lines 779-928）：
- `timeout_seconds > 0` 检查 ✅
- `subprocess.run` 带 `timeout=timeout_seconds` ✅
- `check=False` + 手动检查 `returncode != 0` ✅
- `subprocess.TimeoutExpired` 被 catch 并转换为 `ReciprocalRouteRuntimeError` ✅
- snapshot 验证：executable/blend_path/script_path/request_path 在 subprocess 前后不变（`_verify_snapshots_unchanged`）✅
- `stdout`/`stderr` 截断到 `MAX_PROCESS_LOG_BYTES` ✅
- atomic publish via `staging.rename(final_directory)` ✅
- 竞态处理：如果 rename 失败但 final_directory 已存在（并发完成），`_verify_existing_build` + 清理 staging ✅
- `finally` 块清理 staging（即使成功也清理——虽然成功时 staging 已被 rename）✅
- `_remove_private_staging` 验证 staging 路径是 `.staging-` 前缀的直接子目录，拒绝 symlink ✅
- `_verify_exact_build_layout` 验证 staging 目录是精确的 3 文件集，拒绝 symlink/额外文件 ✅
- build root 必须在 `.nantai-studio` 私有目录下（`build_root.relative_to(private_root)`）✅

**对抗性测试**：
- 恶意 `base_build.executable` 路径 → runner 会执行它。但这是构造器的责任——`base_build` 必须是 verified 的 env-module build，其 `executable` 是 pinned Blender。runner 不验证 executable 身份，因为 env-module runtime 已经验证过了。✅
- subprocess 被 OOM kill → returncode != 0 → ReciprocalRouteRuntimeError ✅
- subprocess 超时 → TimeoutExpired → ReciprocalRouteRuntimeError + finally 清理 ✅

**结论**：runner 安全完整。

### 环节 7 — Report `_registry_is_complete` 缺少 object ID unique 检查 INFO

**审计点**：`ReciprocalRouteBuildReport._registry_is_complete` 只检查 `instances == 1..218`，不检查 object IDs unique。

**对比**：`ReciprocalRouteRuntimeRequest._identities_are_exact` 的第 3 道检查 `len({row.object_id for row in self.object_registry}) != 218`。

**分析**：report 可以有重复 object_id 但不同 instance_id（例如两个 instance 都叫 `wall-stone-001`）。这不是 fail-open：
1. report 的 `object_registry` 必须与 request 的 `object_registry` 完全相等（verify helper 第 8 对），而 request 有 object ID unique 检查。
2. 所以如果 report 有重复 object_id，verify helper 会因为 registry 不等而拒绝。

**严重性**：INFO — 不是 fail-open，只是 report 自洽性比 request 弱。设计可接受：report 自洽性 + verify helper 互补保证完整性。

**处理**：不修复。report 的自洽性已由 verify helper 的 registry 比较覆盖。

### 环节 8 — 跨进程确定性 ✅ PASS

**审计点**：canonical bytes 是否跨进程确定？

**验证**：
- `canary._canonical_json_bytes` 使用 `json.dumps(ensure_ascii=False, indent=2, sort_keys=True) + "\n"` ✅
- `build_id` 由 canonical payload SHA-256 计算（排除自身）✅
- `load_reciprocal_route_build_report` 验证 raw bytes == canonical bytes ✅
- TDD `test_runtime_request_is_deterministic_across_processes` 已锁定（Phase 3 交付）✅

**结论**：跨进程字节确定。

## 修复汇总

| 级别 | 发现 | 修复 |
|---|---|---|
| — | 无 MEDIUM/HIGH fail-open | — |
| INFO | Report `_registry_is_complete` 缺少 object ID unique 检查 | 不修复（verify helper 覆盖） |

## 设计决策保留（INFO）

1. **Report 自洽性弱于 Request**：`ReciprocalRouteBuildReport` 只检查 instances == 1..218，不检查 object IDs unique。这是可接受的设计——report 自洽性 + verify helper 的第 8 对 registry 比较互补保证完整性。如果 report 有重复 object_id，verify helper 会因为 `report.object_registry != request.object_registry` 而拒绝。

2. **Runner 不验证 `base_build.executable` 身份**：runner 从 duck-typed `base_build` 对象读取 `executable` 和 `blend_path`，但不验证它们的 SHA。这是 env-module runtime 的责任——env-module runtime 已经验证了 executable 的 SHA 并绑定到 build report。reciprocal-route runtime 信任 env-module runtime 的验证结果。如果未来需要端到端验证，可以在 `build_reciprocal_route_runtime_request` 中加 executable SHA 检查，但当前设计已足够。

## 测试覆盖

Phase 3 交付的 TDD 已覆盖（`tests/test_synthetic_village_reciprocal_route_module_runtime.py`）：
- 33 passed（22 Phase 2 + 11 Phase 3）
- ruff clean
- 无回归

## 提交内容

本审计不涉及代码修改（无 fail-open 漏洞），仅交付审计文档：

```text
handoff/REVIEW-OPUS-008-reciprocal-route-module-runtime-failclosed-audit.md
```
