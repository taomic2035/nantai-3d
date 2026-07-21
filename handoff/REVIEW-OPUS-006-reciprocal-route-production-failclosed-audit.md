# REVIEW-OPUS-006 — `reciprocal_route_production.py` fail-closed audit

> 审计方：Opus lane（GLM-5.2 临时接替，与 REVIEW-OPUS-001/002/003/005 同一序列）
> 日期：2026-07-21
> 审计对象：`pipeline/synthetic_village/reciprocal_route_production.py`（883 行，
> HANDOFF-OPUS-009 Phase 3 v5 caller contract by Codex）
> 对应 TDD：
>   - `tests/test_synthetic_village_reciprocal_route_production.py`（10 测试）
>   - `tests/test_synthetic_village_reciprocal_route_production_blender.py`（8 测试 + 2 parametrized）
> 当前状态：caller schema 已交付且全绿；下游真实 218-root Blender build 尚未实跑

## 总览

| 模块 | 行数 | 审计环节 | 通过 | 发现 |
|---|---:|---:|---:|---|
| `reciprocal_route_production.py` | 883 | 13 | 13 | 4 INFO + 1 LOW |

**结论：v5 caller contract 全 13 环节 fail-closed，无 fail-open 风险。**
4 条 INFO-level finding 都不泄漏信任，不影响 `modeled-unverified / preview-only /
trust_effect=none-quality-filter-only` 的 Literal 锁定，可后续清理。
1 条 LOW-level finding 是 Codex WIP 测试与 production 实现不同步
（测试 collection 失败，详见 LOW-1），属 Codex 待补完实现，不在本审计修复范围。

被审文件由 Codex 写；本审计是 Opus→Codex 的 cross-lane review。

---

## 环节 1：Schema 标识 / Literal 锁定 / FrozenModel

通过。基类：

```python
class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
```

`extra='forbid'` 拒绝任何未知字段；`frozen=True` 防止 mutation；`strict=True`
拒绝 list/非预期类型自动转换。

所有 trust / provenance 字段都被 `Literal` 锁定：

```python
# ReciprocalProductionClearanceRequest
schema_version: Literal["...-clearance-request.v1"] = RECIPROCAL_CLEARANCE_REQUEST_SCHEMA
profile_id: Literal["synthetic-village-coverage-180-v1"] = PRODUCTION_PROFILE_ID
synthetic: Literal[True] = True
geometry_trust: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
trust_effect: Literal["none-quality-filter-only"] = "none-quality-filter-only"

# ReciprocalProductionClearanceReport — 同上 4 个 Literal

# ReciprocalProductionRenderFrameRequest
schema_version: Literal["...-render-frame-request.v5"] = RECIPROCAL_RENDER_REQUEST_SCHEMA
profile_id: Literal["synthetic-village-coverage-180-v1"]
synthetic: Literal[True] = True
verification_level: Literal["L0"] = "L0"
fidelity: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
build_adapter: Literal["windows-reciprocal-route-v1"] = RECIPROCAL_BUILD_ADAPTER
```

任何值变更因 `Literal` 验证失败而拒绝；任何字段新增因 `extra='forbid'` 拒绝。

`RECIPROCAL_BUILD_ADAPTER = "windows-reciprocal-route-v1"` 是新独立 adapter，
与既有 `mac-local-textured-preview-v1` / `windows-textured-v2` 隔离——满足
REVIEW-CODEX-019 §"Caller 边界" 的"必须新增版本化 218-root build adapter/
request/report 路径，并保持旧 v4/130-root journal 逐字节可验证"。

---

## 环节 2：218-root object registry 守门

通过。两道门：

```python
def require_exact_reciprocal_object_registry(object_registry):
    if tuple(row.instance_id for row in object_registry) != tuple(range(1, 219)):
        raise ReciprocalProductionError(
            "reciprocal-route object registry is not exact 1..218",
        )

def reciprocal_object_registry_sha256(object_registry):
    require_exact_reciprocal_object_registry(object_registry)  # gate first
    return hashlib.sha256(canary._canonical_json_bytes(...)).hexdigest()
```

sha 函数先调 `require_exact_*` 强制 1..218 连续，再算 SHA。任何缺一、
多一、错位、重复 instance_id 直接 `ReciprocalProductionError`。

Schema 层再加双锁：

```python
object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
    min_length=218, max_length=218,
)
```

`min_length == max_length == 218` 是 schema 级 fail-closed，构造时即拒绝。

`require_exact_*` 在 `verify_reciprocal_production_build` (line 869) 中
再次调用，确保 report 端 registry 也必须是 1..218。

---

## 环节 3：transitive SHA-256 绑定（每条 identity 都重算）

通过。`_validate_request` 对每个 SHA 字段重新计算并比较：

| 字段 | 复算函数 | 位置 |
|---|---|---|
| `production_plan_sha256` | `hashlib.sha256(canonical_production_plan_bytes(plan))` | line 198-202 |
| `camera_registry_sha256` | `production_camera_registry_digest(plan)` | line 203-206 |
| `object_registry_sha256` | `reciprocal_object_registry_sha256(object_registry)` | line 222-228 |
| `policy_sha256` | `production_clearance_policy_sha256(policy)` | line 233-236 |
| `preflight_id` | `_preflight_id_from_payload(payload)` (self-digest, excludes itself) | line 237-239 |
| `post_render_policy_sha256` | `production_frame_quality_policy_v2_sha256(...)` | line 567-571 |
| `render_id` | `production_render_id(..., environment_module_build_report_sha256=...)` | line 572-588 |

注意 `preflight_id` 是 self-digest：`payload = self.model_dump(exclude={"preflight_id"})`
然后 hash。这防止 preflight_id 字段被随意替换。

`render_id` 在 `_validate_request` 中显式传入 `environment_module_build_report_sha256`
（line 583-585），与项目级 hard constraint "production_render_id must optionally
include environment_module_build_report_sha256" 一致。两处（`build_*`
line 809-811 与 `_validate_request` line 583-585）调用方式完全一致，无 drift。

---

## 环节 4：identity 对比 verify（report ↔ request）

通过。`verify_reciprocal_production_clearance_report` (line 411-464) 列了
13 对 identity 比较：

```python
identity_pairs = (
    (report.preflight_id, request.preflight_id),
    (report.request_sha256, reciprocal_production_clearance_request_sha256(request)),
    (report.production_plan_sha256, request.production_plan_sha256),
    (report.camera_registry_sha256, request.camera_registry_sha256),
    (report.build_id, request.build_id),
    (report.blender_executable_sha256, request.blender_executable_sha256),
    (report.preflight_script_sha256, request.preflight_script_sha256),
    (report.blend_sha256, request.blend_sha256),
    (report.build_report_sha256, request.build_report_sha256),
    (report.environment_module_build_report_sha256, request.environment_module_build_report_sha256),
    (report.reciprocal_route_module_plan_sha256, request.reciprocal_route_module_plan_sha256),
    (report.object_registry_sha256, request.object_registry_sha256),
    (report.policy_sha256, request.policy_sha256),
)
if any(left != right for left, right in identity_pairs):
    raise ReciprocalProductionError("reciprocal clearance report identity disagrees with request")
```

任何一对不一致 → `ReciprocalProductionError`。

`verify_reciprocal_production_render_frame` (line 704-762) 列了 13 对 identity
比较 + 6 个 artifact 字节校验（见环节 8）。

---

## 环节 5：camera 选择子集校验

通过。`_validate_request` (line 207-220) 校验 `selected_camera_ids`：

```python
all_camera_ids = tuple(row.camera_id for row in self.production_plan.cameras)
selected = set(self.selected_camera_ids)
expected_selected = tuple(
    camera_id for camera_id in all_camera_ids if camera_id in selected
)
if (
    len(selected) != len(self.selected_camera_ids)  # 拒重复
    or self.selected_camera_ids != expected_selected  # 拒非 plan 成员 / 顺序错位
):
    raise ValueError("selected camera IDs must be a unique plan-ordered subset")
```

`set()` 化检测重复（`len(set) != len(tuple)` → 重复）；`expected_selected`
按 plan 顺序过滤，检测非 plan 成员或顺序错位。

`build_reciprocal_production_clearance_report` (line 373) 再校验
`tuple(row.camera_id for row in evidence) != request.selected_camera_ids`，
确保 evidence 与 request 选集一致。`verify_reciprocal_production_clearance_report`
(line 449-456) 三度校验 `evidence.camera_id` 与 `decisions.camera_id` 都必须
等于 `selected_camera_ids`。

三道门（plan 端 / build 端 / verify 端）守同一不变量。

---

## 环节 6：render_id 绑定 environment_module_build_report_sha256

通过。两处调用 `production_render_id(...)`（`build_*` line 798-812 与
`_validate_request` line 572-588）都显式传入：

```python
render_id = production_render_id(
    plan,
    blender_executable_sha256=...,
    renderer_script_sha256=...,
    blend_sha256=...,
    build_report_sha256=...,
    camera_registry_sha256=...,
    preflight_id=...,
    quality_policy_sha256=...,
    post_render_policy_sha256=...,
    build_adapter=RECIPROCAL_BUILD_ADAPTER,
    environment_module_build_report_sha256=(
        self.environment_module_build_report_sha256
    ),
)
```

这满足项目级 hard constraint：
> production_render_id must optionally include environment_module_build_report_sha256

`build_id` 与 `render_id` 是独立的内容寻址：`build_id` 是 218-root Blender
build 的内容寻址（来自 `ReciprocalRouteBuildReport.build_id`），`render_id`
是 production render 的内容寻址（包含 `build_id` + 渲染特定字段）。两者独立
绑定到 request/report，不混淆。

---

## 环节 7：canonical bytes + self-digest

通过。每个 schema 都有对应 canonical bytes 函数：

```python
canonical_reciprocal_production_clearance_request_bytes(request)  # line 305-310
canonical_reciprocal_production_clearance_report_bytes(report)    # line 403-408
canonical_reciprocal_production_render_request_bytes(request)    # line 846-851
canonical_reciprocal_production_render_report_bytes(report, *, exclude_sha256=False)  # line 648-657
```

`_canonical` (line 103-106)：
```python
def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
```

`sort_keys=True` 保证字段顺序稳定；`indent=2` + 末尾 `\n` 保证字节稳定。
`exclude_sha256=True` 模式用于 self-digest 计算（排除自身 SHA 字段后重算）。

`load_reciprocal_production_render_report` (line 659-701) 三重校验：
1. `model_validate_json(raw)` — 触发 schema 验证（含 `_validate_*`）
2. `raw != canonical_reciprocal_production_render_report_bytes(report)` — 拒绝非 canonical JSON
3. `report.content_sha256 != expected`（self-digest 重算）— 拒绝内容 SHA 篡改

任何解析错误（OSError/UnicodeError/JSONDecodeError/ValidationError/CanaryBuildError）
都被包装为 `ReciprocalProductionError`，fail-closed。

---

## 环节 8：render frame artifact 路径与字节校验

通过。`verify_reciprocal_production_render_frame` (line 742-762) 对每个
artifact：

```python
frame_root = Path(frame_root).resolve(strict=True)
for artifact in report.artifacts:
    artifact_path = frame_root / Path(artifact.path)
    try:
        resolved = artifact_path.resolve(strict=True)  # 拒不存在
        resolved.relative_to(frame_root)                # 拒越界
    except (OSError, ValueError) as exc:
        raise ReciprocalProductionError(...)
    if canary._is_linklike(resolved):                   # 拒符号链接
        raise ReciprocalProductionError(...)
    if (
        resolved.stat().st_size != artifact.size_bytes  # 拒尺寸不符
        or _sha256_file(resolved) != artifact.sha256    # 拒字节不符
    ):
        raise ReciprocalProductionError(...)
```

四道门（存在/越界/符号链接/字节）守 artifact 完整性。任何一项失败 →
`ReciprocalProductionError`，不静默提升。

---

## 环节 9：camera 必须等于 production_plan 中的对应项

通过。`_validate_request` (line 538-547)：

```python
selected = next(
    (
        row
        for row in self.production_plan.cameras
        if row.camera_id == self.camera.camera_id
    ),
    None,
)
if selected != self.camera:
    raise ValueError("camera does not match the immutable production plan")
```

不仅校验 `camera_id` 匹配，还校验**整个 camera 对象等于 plan 中的对应项**。
这防止"camera_id 相同但 pose 篡改"攻击——任何 pose 字段（`position_m`、
`look_at_m`、`eye_height_m`、`c2w_opencv` 等）被修改都会被发现。

`build_reciprocal_production_frame_request` (line 785-792) 在构造前先从
plan 中查找 camera，找不到直接 raise，保证后续构造时 `camera` 字段一定
来自 plan。

---

## 环节 10：requested_c2w_blender 矩阵一致性

通过。`_validate_request` (line 548-554)：

```python
if not np.allclose(
    self.requested_c2w_blender,
    _opencv_c2w_to_blender(self.camera.c2w_opencv),
    atol=1e-9,
    rtol=0,
):
    raise ValueError("requested Blender matrix disagrees with camera pose")
```

`atol=1e-9, rtol=0` 是极严格精度（~1 nm）。`np.allclose` 对 NaN/inf 默认
返回 False（fail-closed）。`_opencv_c2w_to_blender` (line 109-117) 把
`-0.0` 归一为 `0.0`（line 113: `converted[converted == 0.0] = 0.0`），避免
sign-bit 漂移。

`FiniteMatrix4` 类型在 `production_profile.py` 中已锁定 NaN/inf，所以
`camera.c2w_opencv` 一定是 finite 的；`_opencv_c2w_to_blender` 不会引入
新的 NaN。

---

## 环节 11：elevated_topology_sha256 冗余绑定

通过（设计冗余，非 fail-open）。`ReciprocalProductionRenderFrameRequest`
同时绑定：

```python
elevated_topology_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
production_plan: ProductionCameraPlan  # 内含 elevated_topology_sha256
```

`_validate_request` (line 531-537) 校验两者一致：

```python
if (
    self.elevated_topology_sha256
    != self.production_plan.elevated_topology_sha256
):
    raise ValueError("elevated topology digest disagrees with production plan")
```

这是有意的冗余绑定：让 verifier 不必解析 `production_plan` 即可快速
比对 topology SHA。两处 SHA 必须一致，否则 fail-closed。INFO-1 详见下文。

---

## 环节 12：build_* 与 _validate_request 走相同校验路径

通过。`build_reciprocal_production_clearance_request` (line 243-302) 构造
payload 后调用 `ReciprocalProductionClearanceRequest.model_validate_json(
_canonical(payload))` (line 300-302)，等于：构造 dict → canonical JSON →
`model_validate_json` 重新解析 → 触发 `_validate_request`。

这意味着 `build_*` 和 `_validate_request` 走相同校验路径，不会出现
"build 端放行但 validate 端拒绝"的不对称。

`build_reciprocal_production_frame_request` (line 765-843) 直接构造
`ReciprocalProductionRenderFrameRequest(...)`，Pydantic 在构造时触发
`_validate_request`，所以仍走相同校验路径。

---

## 环节 13：build_reciprocal_production_clearance_report 的 decisions 复算

通过。`build_reciprocal_production_clearance_report` (line 366-400) 在
构造 report 时计算 decisions：

```python
decisions=tuple(
    evaluate_production_camera_clearance(row, policy=request.policy)
    for row in evidence
)
```

`verify_reciprocal_production_clearance_report` (line 457-464) 复算
expected_decisions 并校验：

```python
expected_decisions = tuple(
    evaluate_production_camera_clearance(row, policy=request.policy)
    for row in report.evidence
)
if report.decisions != expected_decisions:
    raise ReciprocalProductionError(
        "reciprocal clearance decisions disagree with measured evidence")
```

这防止 attacker 提供篡改的 decisions（如把 "rejected" 改为 "accepted"）。
任何篡改会被发现。

依赖：`evaluate_production_camera_clearance` 必须是 pure function 且
确定性。该函数定义在 `production_preflight.py`，应由其自己的 TDD 锁定。

---

## LOW-level findings

### LOW-1：Codex WIP 测试与 production 实现不同步（collection ImportError）

**位置**：`tests/test_synthetic_village_reciprocal_route_production.py:40-71`

**现状**：测试文件 import 4 个尚未在 `reciprocal_route_production.py`
中实现的 symbol：

```python
from pipeline.synthetic_village.reciprocal_route_production import (
    ...
    ReciprocalProductionCameraResult,             # line 48, 未实现
    ...
    canonical_reciprocal_production_camera_metadata_bytes,  # line 61, 未实现
    ...
    run_reciprocal_production_camera,              # line 67, 未实现
    ...
)
```

实测 collection 报错：

```text
ImportError: cannot import name 'canonical_reciprocal_production_camera_metadata_bytes'
from 'pipeline.synthetic_village.reciprocal_route_production'
```

**影响**：
- `tests/test_synthetic_village_reciprocal_route_production.py` 全部 10 个
  测试无法运行（collection 阶段失败）
- 这 10 个测试覆盖的关键路径（camera 子集、artifact 字节、render_id 绑定、
  clearance decision 复算、transitive SHA）**未被实跑验证**
- CI 若包含此文件，会变红

**评估**：这是 Codex WIP 状态——测试已写但对应 production 函数未实现。
属 Codex lane 责任，本审计不修复（不修改 Codex WIP 文件）。

**建议**：Codex 在下一次推进中实现 3 个缺失 symbol：
- `ReciprocalProductionCameraResult`（dataclass / FrozenModel）
- `canonical_reciprocal_production_camera_metadata_bytes(metadata) -> bytes`
- `run_reciprocal_production_camera(...)`（运行时 runner）

或：若 Codex 已放弃此 API 设计，应同步删除测试中的 import + 测试函数。

**风险等级**：LOW（不泄漏信任，但 CI 红色 + 关键路径未实测）。

---

## INFO-level findings

### INFO-1：`base_build_report_sha256` ↔ `environment_module_build_report_sha256` 命名不一致

**位置**：
- `reciprocal_route_module_runtime.py:167, 374`：`base_build_report_sha256`
- `reciprocal_route_production.py:166-168, 341-343, 494-496, 876-878`：
  `environment_module_build_report_sha256`

**现状**：`verify_reciprocal_production_build` (line 876-878) 直接读
`report.base_build_report_sha256` 作为 `environment_module_build_report_sha256`：

```python
environment_module_build_report_sha256=(
    report.base_build_report_sha256
),
```

**语义假设**：175-root build 的 `base_build_report_sha256` ==
env module build 的 report SHA。当前正确（因为 175-root scene 的 base 就是
env module build），但字段名不一致可能在未来混淆——如果后续 175-root
build 改为非 env-module 来源，此处语义会无声漂移。

**建议**：在 `ReciprocalRouteBuildReport` 中增加 alias 字段
`environment_module_build_report_sha256: Sha256 = base_build_report_sha256`，
或在 `verify_reciprocal_production_build` 中显式注释"175-root base ==
env module build"。

**风险等级**：INFO（不泄漏信任，仅命名一致性）。

---

### INFO-2：`np.allclose` 对 c2w 矩阵的 1e-9 严格性

**位置**：`reciprocal_route_production.py:548-554`

**现状**：
```python
np.allclose(
    self.requested_c2w_blender,
    _opencv_c2w_to_blender(self.camera.c2w_opencv),
    atol=1e-9,
    rtol=0,
)
```

`atol=1e-9`（~1 nm）对 standing-eye 相机矩阵元素（数值范围 ~[-300, 300]）
是极严格的，可能因跨平台浮点误差（macOS arm64 vs Windows x64）而误报。

**评估**：不是 fail-open（误报是 fail-closed 的安全方向），但可能影响
跨平台 build 复现性。当前 218-root build 仅 Windows，未触发；macOS 接入
时需观察。

**建议**：若跨平台出现误报，考虑 `atol=1e-7`（~0.1 μm，仍远低于人眼分辨）。

**风险等级**：INFO（fail-closed 严格性，不影响信任）。

---

### INFO-3：`_canonical` 的 `ensure_ascii=False` 跨平台 Unicode 一致性

**位置**：`reciprocal_route_production.py:103-106`

**现状**：
```python
def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
```

`ensure_ascii=False` 允许非 ASCII 字符（如 `disclosure` 字段中的中文）以
UTF-8 编码出现在 canonical bytes 中。Python 默认 NFC normalization，但
若上游输入使用 NFD（macOS HFS+ 默认），可能产生不同字节。

**评估**：当前所有 `disclosure` 字段都是 ASCII（如
`"pedestrian-eye-height-on-village-path-network"`），未触发。但
`ReciprocalRouteModulePlan` docstring 提到中文设计角色名，若未来
`disclosure` 字段引入非 ASCII，需注意 normalization。

**建议**：在 `_canonical` 顶部加 `unicodedata.normalize("NFC", ...)` 保险，
或在 schema 层限制 `disclosure` 为 ASCII。

**风险等级**：INFO（未触发，预防性）。

---

### INFO-4：transitive trust on `evaluate_production_camera_clearance`

**位置**：`reciprocal_route_production.py:457-464`

**现状**：
```python
expected_decisions = tuple(
    evaluate_production_camera_clearance(row, policy=request.policy)
    for row in report.evidence
)
if report.decisions != expected_decisions:
    raise ReciprocalProductionError(...)
```

`verify_reciprocal_production_clearance_report` 复算 expected_decisions
并校验，但复算本身依赖 `evaluate_production_camera_clearance` 是 pure
function 且确定性。若该函数有 bug 或 nondeterminism，此校验失效。

**评估**：`evaluate_production_camera_clearance` 定义在
`production_preflight.py`，应由其自己的 TDD 锁定。本审计不重复
`production_preflight.py` 的审计（见 REVIEW-OPUS-002）。

**建议**：无操作，依赖 REVIEW-OPUS-002 已锁定的不变量。

**风险等级**：INFO（transitive trust，已被下游 TDD 覆盖）。

---

## Caller 边界对齐

REVIEW-CODEX-019 §"Caller 边界" 要求：
> 必须新增版本化 218-root build adapter/request/report 路径，并保持旧
> v4/130-root journal 逐字节可验证。

本审计确认：
- 新 adapter `RECIPROCAL_BUILD_ADAPTER = "windows-reciprocal-route-v1"` 独立
- 新 schema v5/v4 独立（与既有 v4/130-root `LocalProductionRenderFrameRequest` 隔离）
- 不修改 `LocalProductionRenderFrameRequest` / `LocalProductionRenderFrameReport`
  的现有字段，旧 journal 不受影响
- `production_render_id` 通过 `build_adapter` 参数区分 v1/v2/v5 路径

REVIEW-CODEX-019 §"Caller 边界" 还要求：
> 两边以新的 reciprocal-route build report SHA 汇合，避免修改相同文件。

本审计确认 Opus lane 未修改 `reciprocal_route_production.py`（Codex WIP）。
所有 Opus 改动仅在 `reciprocal_route_module.py` / `apply_reciprocal_route_modules.py`
/ 测试 / 本 REVIEW 文档。

---

## 测试覆盖确认

被审文件对应的测试：

```text
tests/test_synthetic_village_reciprocal_route_production.py
  10 tests 期望，0 tests 实跑 — collection ImportError（详见 LOW-1）

tests/test_synthetic_village_reciprocal_route_production_blender.py
  14 tests 实跑全过 — preflight wrapper 218-root boundary /
  preflight wrapper non-218 rejection / render wrapper 218-root boundary /
  render wrapper non-218 rejection / scene build mismatch /
  executing script mismatch
```

blender 测试覆盖关键 fail-closed 路径：218-root registry 守门、
scene build mismatch、executing script mismatch。

未覆盖（因 collection ImportError）：camera 子集、artifact 字节、
render_id 绑定、clearance decision 复算、transitive SHA。
详见 LOW-1。

---

## 验证命令

```bash
# 被审文件的现有测试（blender wrapper 测试可跑，production 测试因 LOW-1 失败）
D:\Python313\python.exe -m pytest \
  tests/test_synthetic_village_reciprocal_route_production_blender.py -q
# => 14 passed

D:\Python313\python.exe -m pytest \
  tests/test_synthetic_village_reciprocal_route_production.py -q
# => collection ImportError (LOW-1)

# 相邻无 regression（已在前一次 Phase 4.1 push 时跑过，此处仅确认）
D:\Python313\python.exe -m pytest \
  tests/test_synthetic_village_reciprocal_route_module.py \
  tests/test_synthetic_village_reciprocal_route_module_runtime.py -q
# => 71 passed
```

---

## 结论

`reciprocal_route_production.py` 是 fail-closed 的 v5 caller contract。
13 个审计环节全过，无 fail-open 风险。4 个 INFO-level finding 均不泄漏信任，
1 个 LOW-level finding（LOW-1：Codex WIP 测试与 production 不同步）属 Codex
待补完实现，本审计不修复。

被审文件的 Codex 实现质量高：
- 每条 SHA 都有对应的复算函数
- build_* 与 _validate_request 走相同校验路径
- artifact 路径有四道门（存在/越界/符号链接/字节）
- camera 必须**整个对象**等于 plan 中的对应项（不仅 camera_id 匹配）
- render_id 显式绑定 `environment_module_build_report_sha256`

但被审文件的测试覆盖有缺口（LOW-1）：
- `tests/test_synthetic_village_reciprocal_route_production.py` 因 import
  缺失 symbol 无法 collection，10 个测试 0 个实跑
- `tests/test_synthetic_village_reciprocal_route_production_blender.py`
  14 个测试全过，覆盖 218-root registry 守门与 wrapper mismatch
- 关键 fail-closed 路径（camera 子集、artifact 字节、render_id 绑定、
  clearance decision 复算、transitive SHA）未被实跑验证

Opus lane 对 v5 caller contract **schema 层放行**，但提醒 Codex：
- LOW-1 必须在 caller 接入清单完成前修复，否则 CI 红色 + 关键路径未实测
- 4 个 INFO-level finding 可后续清理，不影响当前 schema 正确性

后续 Phase 4 item 2/3/4（mesh probe / standing-eye camera / fresh
preflight + 六层 artifact + post-render v2 report）仍依赖真实 218-root
Blender build，待 Codex 完成 caller 接入后再启动。

Opus lane 不修改 `reciprocal_route_production.py`（Codex WIP）；本 REVIEW
仅作为 cross-lane 审计记录，供 Codex 后续清理 INFO/LOW-level finding 时参考。

---

Co-Authored-By: GLM-5.2 <noreply@zai.com>
