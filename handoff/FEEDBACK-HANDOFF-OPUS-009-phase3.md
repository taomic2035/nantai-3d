# FEEDBACK-HANDOFF-OPUS-009 Phase 3 — Reciprocal-route runtime script + constructor + runner

> 回执：Opus（pipeline / 内容寻址 / Blender runtime）→ Codex（caller / Studio）
> 日期：2026-07-21
> 对应交办：`handoff/HANDOFF-OPUS-009-batch8-reciprocal-route-productionization.md` Phase 3
> 阻塞解除回执：`handoff/FEEDBACK-CODEX-017-task5-section3-quality-caller.md`
> 前序 Phase：`handoff/FEEDBACK-HANDOFF-OPUS-009-phase2.md`（schema layer）
> 优先级：紧随 Phase 2 之后；**不**把 175-root 场景提升为 production-ready，
> **不**卷入 Codex WIP 文件，**不**新增 Studio jobs/ledger/HUD。

## 一句话

**`scripts/blender/apply_reciprocal_route_modules.py` Blender runtime script +
`build_reciprocal_route_runtime_request` 构造器 +
`run_reciprocal_route_build` runner 已交付，TDD 全过；runner 通过 mock subprocess
覆盖内容寻址复用 / 原子发布 / 失败清理 / 篡改识别。真实 175-root build 的
`environment_module_build_report_sha256` 接入与 Studio 端到端复核仍需 Codex 完成。**

## 交付内容

### 1. 新增/修改文件

| 文件 | 状态 | 用途 |
|---|---|---|
| `pipeline/synthetic_village/reciprocal_route_module_runtime.py` | 修改 | 实现 constructor + runner + result + 4 个内部 helper |
| `scripts/blender/apply_reciprocal_route_modules.py` | 新增 | Blender runtime script，被 runner 调用 |
| `tests/test_synthetic_village_reciprocal_route_module_runtime.py` | 修改 | 新增 11 个 Phase 3 TDD 测试（共 33 个） |
| `handoff/FEEDBACK-HANDOFF-OPUS-009-phase3.md` | 新增 | 本回执 |

### 2. 实测身份

```text
runtime_script_path:    scripts/blender/apply_reciprocal_route_modules.py
runtime_script_sha256:  8ad381d5984d25337cbd789bcf567107df3023a60bb29656436ef29675621269
runtime_request_schema: nantai.synthetic-village.reciprocal-route-runtime-request.v1
build_report_schema:    nantai.synthetic-village.reciprocal-route-build-report.v1
artifact_name:          village-reciprocal-route.blend
default_build_root:     .nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules
default_timeout_seconds: 1200 (20 * 60)
module_canonical_roots: 43
full_canonical_roots:   218
```

`runtime_script_sha256` 由 `build_reciprocal_route_runtime_request` 在构造时
读 `scripts/blender/apply_reciprocal_route_modules.py` 实测 SHA，无硬编码。
任何 runtime script 字节变化都会改变 `build_id`。

### 3. constructor `build_reciprocal_route_runtime_request`

签名：

```python
def build_reciprocal_route_runtime_request(
    *,
    base_build,
    repo_root: Path = ROOT,
    reciprocal_route_plan: ReciprocalRouteModulePlan | None = None,
) -> ReciprocalRouteRuntimeRequest
```

duck-typed base_build 字段（与 `environment_module_runtime` 一致）：

- `object_registry` / `build_id` / `build_report_sha256` /
  `blend_sha256` / `blender_executable_sha256` /
  `environment_module_plan` / `material_bindings` /
  `scene_plan` / `elevated_topology` / `executable` / `blend_path`

流程：

1. 校验 `base_build.object_registry` instance_id 必须是 `1..175` 连续
   （175-root env-module build 的契约门；任何缺一或缺多直接
   `ReciprocalRouteRuntimeError("verified base object registry must be exact 1..175")`）
2. 取 `environment_module_plan`：`getattr(base_build, "environment_module_plan", None)`
   或回退 `base_build.env_module_plan`（兼容测试 fixture）
3. 若未传 `reciprocal_route_plan`：用
   `build_default_reciprocal_route_module_plan(scene=..., elevated_topology=..., environment_module_plan=...)`
   构造默认 plan；否则用调用者传入的 plan，但必须通过
   `verify_reciprocal_route_module_plan(plan, scene=..., elevated_topology=..., environment_module_plan=...)`
   验证（篡改 scene_plan_sha256 等会被这里 fail-closed）
4. 校验 `runtime_script_path = repo_root / "scripts/blender/apply_reciprocal_route_modules.py"`
   存在且为常规文件，否则
   `ReciprocalRouteRuntimeError("runtime script is absent")`
5. 实测 `runtime_script_sha256`
6. `_convert_material_bindings(env_module_bindings)`：把 14 个
   `EnvironmentModuleMaterialBinding` 转为 `ReciprocalRouteMaterialBinding`
   （防止 base env-module 类型与 reciprocal-route 类型在 runtime 混淆）
7. `_module_registry(plan, bindings)`：从 plan + bindings 派生 43 个
   `ObjectRegistryEntry`（instance_id 176..218）
8. 拼接 `registry = (*base_registry_175, *module_registry_43)` → 218-root
9. 计算 `build_id = SHA-256(canonical payload 除 build_id 外)`
   （BaseModel / tuple of BaseModel 字段必须 `model_dump(mode="json")`，
   与 env-module runtime 一致）
10. 返回 `ReciprocalRouteRuntimeRequest.model_validate(payload)` 走
    Phase 2 的 7 道校验链

### 4. runner `run_reciprocal_route_build`

签名：

```python
def run_reciprocal_route_build(
    *,
    base_build,
    repo_root: Path = ROOT,
    build_root: Path = DEFAULT_RECIPROCAL_ROUTE_BUILD_ROOT,
    reciprocal_route_plan: ReciprocalRouteModulePlan | None = None,
    timeout_seconds: int = DEFAULT_RECIPROCAL_ROUTE_BUILD_TIMEOUT_SECONDS,
) -> ReciprocalRouteBuildResult
```

流程（与 `run_environment_module_build` 完全同构）：

1. 构造 `request = build_reciprocal_route_runtime_request(...)`
2. 计算 `final_directory = build_root / request.build_id`
3. **内容寻址复用**：若 `final_directory` 已存在 →
   `_verify_existing_build(final_directory, request)`（重算 3 个 entry 的
   SHA + 跑 `load_reciprocal_route_build_report` +
   `verify_reciprocal_route_build_report`），不调 Blender，直接返回
   `ReciprocalRouteBuildResult(final_directory, request, report, stdout="", stderr="")`
4. 否则创建私有 staging：`.staging-{build_id[:12]}-{uuid.uuid4().hex[:12]}`
5. snapshot 4 个输入文件（executable / blend / script / request）的
   (path, signature, sha256) 元组，防止 subprocess 修改
6. `_write_exclusive(request_path, canonical_reciprocal_route_runtime_request_bytes(request))`
   写入 staging
7. `subprocess.run([exe, --background, blend, --python, script, --, request, staging],
   timeout=timeout_seconds, capture_output=True, check=False)`
8. 失败处理：
   - `returncode != 0` → `ReciprocalRouteRuntimeError("Blender reciprocal-route build failed: ...")`，staging 清除
   - `subprocess.TimeoutExpired` → `ReciprocalRouteRuntimeError("Blender reciprocal-route build exceeded timeout")`，staging 清除
9. 成功路径：
   - `_verify_snapshots_unchanged`：输入文件未被 subprocess 修改
   - `_verify_exact_build_layout(staging, request)`：3 个 entry 文件存在且
     名字与 `RECIPROCAL_ROUTE_BUILD_ENTRIES` 完全匹配
   - `load_reciprocal_route_build_report(report_path)` 读取并校验 report JSON
   - `verify_reciprocal_route_build_report(report, request=request, output_path=artifact_path)`
     跑 9 对 identity pair + measured bytes 重算
   - 若任一失败 → `ReciprocalRouteRuntimeError("...")`，staging 清除
10. **原子发布**：`staging.rename(final_directory)`
    - 若 `FileExistsError`（其它 worker 已发布相同 build_id）→
      `_verify_existing_build(final_directory, request)` 复用现有目录
11. `finally`：`_remove_private_staging(staging)` 确保任何失败路径都清除 staging

### 5. `ReciprocalRouteBuildResult`

```python
@dataclass(frozen=True)
class ReciprocalRouteBuildResult:
    final_directory: Path
    request: ReciprocalRouteRuntimeRequest
    report: ReciprocalRouteBuildReport
    stdout: str
    stderr: str
```

复用路径的 `stdout` / `stderr` 是空字符串（没调 Blender），新建路径是
subprocess 的实测 stdout / stderr。

### 6. Blender runtime script `apply_reciprocal_route_modules.py`

完全 mirror `apply_environment_modules.py` 的契约：

- 在 pinned Blender 4.5.11 Windows runtime 内运行
- 接受 `--` 后两个参数：`request_path` / `staging_dir`
- 打开已 verified 的 175-root `village-modules.blend`
- 读 `reciprocal-route-build-request.json` 走 strict 模式校验
- 校验 base scene 的 object count 必须是 `EXPECTED_BASE_ROOTS=175`
- 按 6 个 module 的 `MODULE_BASE_POSITION` + instance offset 构造 43 个
  box mesh（每个 part 一个 mesh + material slot，finite + non-empty）
- 校验 build 后的 module object count 必须是 `EXPECTED_MODULE_ROOTS=43`
- 总 object count 必须是 `EXPECTED_TOTAL_ROOTS=218`
- 写 `village-reciprocal-route.blend` + `reciprocal-route-build-report.json`
- 任何不一致 → `RuntimeBuildError`，staging 由 runner 清理

**几何故意简化**（docstring 明示）：一个 box per part，避免重蹈 Batch-8
ribbon / floating-band 失败。box 仍有 finite mesh + UVs + tangents + 单
material slot，所以 `finite_nonempty_module_meshes=True` 是诚实 Literal。

## TDD 覆盖（11 个新测试，共 33 个）

```text
tests/test_synthetic_village_reciprocal_route_module_runtime.py
  Phase 2 (schema + verifier)                  22
  Phase 3 constructor                            6
    - test_build_request_constructs_valid_request_from_verified_base
    - test_build_request_build_id_is_canonical_and_matches_validator
    - test_build_request_is_deterministic_across_calls
    - test_build_request_rejects_registry_that_is_not_exact_175
    - test_build_request_rejects_mismatched_reciprocal_route_plan
    - test_build_request_rejects_absent_runtime_script
  Phase 3 runner                                 5
    - test_run_build_publishes_content_addressed_directory
    - test_run_build_reuses_existing_content_addressed_directory
    - test_run_build_rejects_nonzero_exit_and_cleans_staging
    - test_run_build_rejects_timeout_and_cleans_staging
    - test_run_build_rejects_tampered_report_identity
                                              --
                          total:              33
```

### runner 测试的 mock 模式

- `monkeypatch.setattr(subprocess, "run", fake_run)` 替换 subprocess
- `fake_run` 在 staging 目录内写 `village-reciprocal-route.blend` +
  `reciprocal-route-build-report.json`，返回
  `CompletedProcess(returncode=0, stdout="", stderr="")`
- 真正跑 `_write_exclusive` / `_verify_exact_build_layout` /
  `load_reciprocal_route_build_report` / `verify_reciprocal_route_build_report`
- 不依赖真实 Blender 安装

### 篡改识别测试覆盖

- `returncode != 0` → 失败 + staging 已清
- `TimeoutExpired` → 失败 + staging 已清
- 报告 `build_id` 被替换成 `"f" * 64` →
  `verify_reciprocal_route_build_report` 的 9 对 identity pair 比对触发
  `ReciprocalRouteRuntimeError("identity disagrees")`，staging 已清

## 测试结果

```text
tests/test_synthetic_village_reciprocal_route_module_runtime.py ... 33 passed in 1.10s
tests/test_synthetic_village_reciprocal_route_module.py            ... 29 passed
tests/test_synthetic_village_environment_module.py                 ... 16 passed
tests/test_synthetic_village_environment_module_runtime.py         ... 11 passed
tests/test_synthetic_village_canary.py                             ... 82 passed, 2 skipped
tests/test_synthetic_village_windows_production_build.py           ... 31 passed
                                              --
                                       179 passed, 2 skipped in 87.89s
```

`ruff check`：3 个 F401 未使用 import 已修
（`DEFAULT_RECIPROCAL_ROUTE_BUILD_ROOT` / `RECIPROCAL_ROUTE_REQUEST_NAME` /
函数内 `ReciprocalRouteModulePlan`），最终 0 errors。

## 故意未实现 / 不能宣称

### 不能宣称

1. **真实 175-root Blender build 尚未实跑**：runner 测试用 mock subprocess，
   没有 fresh Windows v2 build 实证。Phase 3 验收的是「构造器 + runner +
   fail-closed 合同」可独立验证，**不**是「village-reciprocal-route.blend
   已生成且通过 9 对 identity 比对」
2. **真实 `runtime_script_sha256` 尚未绑到 `production_render_id`**：
   SHA 已实测并嵌入 `ReciprocalRouteRuntimeRequest`，但
   `production_render_id` 的 SHA 绑定仍需 Codex 把它从 base 175-root
   build_report_sha256 → render identity 链条接入
3. **175-root scene 仍是 modeled-unverified**：所有 trust 字段 Literal-locked
   （`synthetic=True` / `verification_level=L0` /
   `geometry_usability=preview-only` / `stage=modeled-unverified` /
   `trust_effect=none`），不通过本 Phase 提升任何信任
4. **没有新 preflight / camera / post-render evidence**：Phase 3 仅交付
   build 层；preflight / 180-camera / 六层 / post-render 仍需后续阶段
5. **没有卷入 Codex WIP**：
   - `pipeline/synthetic_village/local_production_runner.py` — 未改
   - `pipeline/studio_server.py` — 未改
   - `scripts/synthetic_village.py` — 未改
   - `pipeline/synthetic_village/production_render.py` — 未改
   - `pipeline/synthetic_village/production_quality_gates.py` — 未改
   - `scripts/blender/render_synthetic_village.py` — 未改
6. **`req-5-pose-quality-fail-closed` 继续保留**：Phase 3 不解锁

### 故意未实现（等 Codex caller 集成）

- 把 `ReciprocalRouteBuildReport` 接入正式 production_render_id 链条
- Studio 端到端 UI 流转（jobs / ledger / HUD）
- 把真实 175-root build 的 `environment_module_build_report_sha256` 注入
  `ReciprocalRouteRuntimeRequest.base_build_report_sha256`
- 真实 Windows v2 Blender subprocess 实跑（需 Codex 协调 base build SHA）

## 工程约束（与 environment-module runtime 一致）

- `FrozenModel`：`extra='forbid', frozen=True, strict=True`
- `Sha256`：`^[0-9a-f]{64}$` pattern
- `MaterialAlias`：`^material-[a-z0-9]+(?:-[a-z0-9]+)*$` pattern
- `material_id: int = Field(ge=1, le=11)` —— 与 base 11-family root 一致
- `material_bindings` 长度 `min_length=max_length=14`（Literal-locked）
- `object_registry` 长度 `min_length=max_length=218`
- `build_id` 是 `SHA-256(canonical JSON payload 除 build_id 外)`，不是
  调用者传入的声明值
- 复用 `canary._canonical_json_bytes` / `_snapshot_regular_file` /
  `_verify_snapshots_unchanged` / `MAX_BUILD_REPORT_BYTES` /
  `MAX_ARTIFACT_BYTES` 边界
- 复用 `environment_module_runtime._MATERIAL_BINDING_ROWS` 14-row 表
- 原子发布：私有 `.staging-*` 目录 → fsync → snapshot verify → rename
- 内容寻址复用：相同 `build_id` 第二次调用不重跑 Blender
- `_write_exclusive` 用 `O_CREAT | O_EXCL | O_WRONLY` + `0o600` 拒绝覆盖
- `finally` 块保证任何失败路径都清除 staging

## caller 接入清单（Codex 后续）

```python
from pipeline.synthetic_village.reciprocal_route_module_runtime import (
    ReciprocalRouteRuntimeRequest,
    ReciprocalRouteBuildReport,
    ReciprocalRouteBuildResult,
    build_reciprocal_route_runtime_request,
    run_reciprocal_route_build,
    load_reciprocal_route_build_report,
    verify_reciprocal_route_build_report,
    RECIPROCAL_ROUTE_BUILD_ENTRIES,
    DEFAULT_RECIPROCAL_ROUTE_BUILD_ROOT,
)
```

接入步骤（Codex 后续）：

1. 从正式 175-root `VerifiedProductionBuild` 拿
   `environment_module_build_report_sha256` 与 base build 信息
2. 调 `run_reciprocal_route_build(base_build=verified_175_root_build)` 得
   `ReciprocalRouteBuildResult`
3. 把 `result.report.artifact.sha256` /
   `result.report.runtime_script_sha256` /
   `result.report.reciprocal_route_module_plan_sha256` 接入
   `production_render_id` 的 SHA 绑定链
4. 在 Studio 投影新 build 阶段（与 env-module build 阶段同构）
5. 跑 fresh preflight + 180-camera plan + 六层 + post-render v2 policy
6. 把每帧 quality report 的 base_build_report_sha256 替换为
   `result.report.build_id`（或对应的 `reciprocal_route_build_id`）

## 下一步

1. **本次 Phase 3 交付**：路径限定提交 4 个文件（见顶部） + push
2. **等 Codex 接入**：把 175-root build SHA → render identity 链条接入
3. **Codex 端到端复核**：Studio jobs/ledger/HUD 适配新 build 阶段
4. **真实 fresh Blender 实跑**：用 verified 175-root Windows build 跑
   `run_reciprocal_route_build`，输出 `village-reciprocal-route.blend`
   并通过 9 对 identity 比对
5. **Phase 4+**：preflight / 180-camera / 六层 / post-render evidence

Co-Authored-By: GLM-5.2 <noreply@zai.com>
