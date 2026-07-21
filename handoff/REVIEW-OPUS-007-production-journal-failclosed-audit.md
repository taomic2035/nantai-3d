# REVIEW-OPUS-007 — `production_journal.py` 对抗性 fail-closed 审计

> 审计方：Opus lane（GLM-5.2 临时接替）
> 日期：2026-07-21
> 对象：`pipeline/synthetic_village/production_journal.py`（380 行，
> Opus 自己写的生产档逐帧 durable render journal）
> 最近改动：commit `e121036`（2026-07-21，新增
> `environment_module_build_report_sha256` 可选 SHA 绑定）
> 对应 TDD：`tests/test_synthetic_village_production_journal.py`（41 测试全过）
> 范围：**只读审计 + 证据记录**，不修代码

## 总览

| 模块 | 行数 | 审计环节 | 通过 | 发现 |
|---|---:|---:|---:|---|
| `production_journal.py` | 380 | 13 | 13 | 2 INFO |

**结论：production_journal.py 全 13 环节 fail-closed，无 fail-open 风险。**
2 条 INFO-level finding 均不泄漏信任，不影响
`simplified-pbr-not-render-parity / L2 / trust_effect=none` 的 Literal 锁定。
被审文件由 Opus 自己写，本审计是 self-review（与 REVIEW-OPUS-001/002/003/006
同序列，但因 commit `e121036` 新增 `environment_module_build_report_sha256`
绑定后尚未做对抗性审计，故补做）。

## 审计方法

按 13 个执行环节逐节验证 fail-closed 强度，每节确认：
(a) 异常路径是否真的拒绝、(b) 字段是否被内容寻址绑定、
(c) 是否存在从元数据推断 SHA 的路径、(d) `model_copy` 绕过是否被 revalidate 兜住。

---

## 环节 1：Schema 标识 / Literal 锁定 / FrozenModel

通过。`FrozenModel`（line 57-58）：

```python
class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
```

`extra='forbid'` 拒绝任何未知字段；`frozen=True` 防止 mutation；`strict=True`
拒绝 list/非预期类型自动转换。所有 trust / provenance 字段都被 Literal 锁定：

```python
# ProductionRenderJournal
schema_version: Literal["nantai.synthetic-village.production-render-journal.v1"]
profile_id: Literal["synthetic-village-coverage-180-v1"]
synthetic: Literal[True] = True
geometry_trust: Literal["simplified-pbr-not-render-parity"]
verification_level: Literal["L2"] = "L2"

# ProductionArtifactRecord
kind: Literal["rgb", "depth", "normal", "instance-mask",
             "semantic-mask", "camera-metadata"]
sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

# ProductionFrameRecord
state: FrameState  # Literal["planned", "rendering", "verified", "failed", "timed-out"]
```

任何值变更因 Literal 验证失败而拒绝；任何字段新增因 `extra='forbid'` 拒绝。

---

## 环节 2：`_require_64_hex_sha` fail-closed 独立性

通过。`production_render_id` 是公开 API（被 `production_render` /
`production_repose` / `reciprocal_route_production` / tests 多处导入）。
即便 caller 端 Pydantic schema 已校验 64-hex，函数自身也独立 fail-closed：

```python
def _require_64_hex_sha(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in _HEX_CHARS for c in value)
    ):
        raise ProductionProfileError(
            f"{field_name} must be a 64-hex-char SHA-256 string",
        )
```

5 个可选 SHA 绑定键都走相同校验路径（line 236-272）：

| 字段 | 校验位置 | 测试 |
|---|---|---|
| `preflight_id` | line 252-254 | `test_render_id_rejects_non_hex_preflight_id` |
| `quality_policy_sha256` | line 255-257 | `test_render_id_rejects_non_hex_quality_policy_sha` |
| `post_render_policy_sha256` | line 258-263 | (covered by pattern校验) |
| `repose_search_sha256` | line 264-266 | `test_render_id_rejects_non_hex_repose_search_sha` |
| `environment_module_build_report_sha256` | line 236-240 | `test_render_id_rejects_non_hex_environment_module_build_report_sha` + `test_render_id_rejects_short_environment_module_build_report_sha` |

5 个绑定键的 fail-closed 行为**完全对称**：非 hex 字符 → reject，
非 64 长度 → reject，None → 不进入 payload（向后兼容）。

测试覆盖**显式**列出"这是为了防 caller 绕过 schema 直接调用"（test 文件
line 757-765 注释）：

```python
# production_render_id 是公开 API (被 production_render / production_repose /
# tests 多处导入)。即使 caller 端 Pydantic schema 已校验 64-hex, 函数自身也
# 必须 fail-closed —— 否则绕过 schema 直接调用就会让非 SHA 字符串静默进入
# canonical payload, 破坏内容寻址。
```

---

## 环节 3：`ProductionArtifactRecord` 字段约束

通过。每个 artifact 携带 `kind`（Literal 6 值）、`path`（min_length=1）、
`sha256`（64-hex pattern）、`size_bytes`（ge=1）。

`sha256` 字段的 `pattern=r"^[0-9a-f]{64}$"` 是 schema 层校验，与
`_require_64_hex_sha` 在不同层做相同检查（defense in depth）。
`size_bytes >= 1` 拒绝空文件冒充。

---

## 环节 4：`expected_production_artifacts` 六文件契约

通过。每个 camera_id 强制绑定 6 个 (kind, path) 元组（line 76-83）：

```python
return (
    ("rgb", f"rgb/{camera_id}.png"),
    ("depth", f"depth/{camera_id}.exr"),
    ("normal", f"normal/{camera_id}.exr"),
    ("instance-mask", f"instance/{camera_id}.png"),
    ("semantic-mask", f"semantic/{camera_id}.png"),
    ("camera-metadata", f"cameras/{camera_id}.json"),
)
```

`ProductionFrameRecord._validate_state`（line 104-107）逐元组比对：

```python
if tuple((item.kind, item.path) for item in self.artifacts) != (
    expected_production_artifacts(self.camera_id)
):
    raise ValueError("render frame artifacts are not the exact six-file contract")
```

这防止"六个产物全是 rgb"或"别的相机的 artifacts"被冒充 verified。

测试覆盖：
- `test_verified_frame_rejects_six_artifacts_of_the_same_kind`
- `test_verified_frame_rejects_a_missing_kind_even_when_the_count_is_six`
- `test_verified_frame_rejects_another_cameras_artifacts`
- `test_verified_frame_cannot_carry_zero_artifacts`
- `test_expected_artifacts_bind_every_path_to_its_camera`
- `test_production_six_file_contract_is_as_strong_as_the_canary_one`

---

## 环节 5：`ProductionFrameRecord._validate_state` 状态机一致性

通过。5 个状态分支严格 fail-closed：

| 状态 | 必须有 | 必须无 |
|---|---|---|
| `verified` | 六文件 artifacts + wall_clock_seconds | error |
| `failed` / `timed-out` | error + wall_clock_seconds | artifacts |
| `planned` / `rendering` | (none) | artifacts + error + wall_clock_seconds |

`timed-out` 额外要求 `wall_clock_seconds >= timeout_limit_seconds`
（line 124-127）—— 防止 caller 把短时运行冒充超时。

`verified` 必须报 `wall_clock_seconds`（line 110-111）——
"没跑过就是 None，绝不填 0 冒充"（docstring line 87）。

`failed` 必须报 `wall_clock_seconds`（line 117-118）——
即使失败也要如实记录耗时。

测试覆盖每个状态分支（7 个测试），无遗漏。

---

## 环节 6：`ProductionRenderJournal._validate_journal` frame uniqueness

通过。line 148-153：

```python
@model_validator(mode="after")
def _validate_journal(self) -> ProductionRenderJournal:
    ids = [frame.camera_id for frame in self.frames]
    if len(ids) != len(set(ids)):
        raise ValueError("journal frames must have unique camera IDs")
    return self
```

`frames: tuple[...] = Field(min_length=1)` 拒绝空 journal。

但 `_validate_journal` **不**校验 frames 的 camera_id 必须在 plan 中——
这是 `new_production_journal` 的职责（line 320-327）：

```python
known = {camera.camera_id for camera in plan.cameras}
...
unknown = [camera_id for camera_id in selected if camera_id not in known]
if unknown:
    raise ProductionProfileError(
        f"camera IDs are not in the production plan: {unknown}")
```

分层设计合理：schema 层只校验 uniqueness，构造函数层校验 plan 从属。
但 INFO-1 详见下文：直接 `ProductionRenderJournal(...)` 构造可绕过 plan 校验。

---

## 玞节 7：canonical bytes 稳定性

通过。`_canonical`（line 169-172）：

```python
def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
```

`sort_keys=True` 保证字段顺序稳定；`indent=2` + 末尾 `\n` 保证字节稳定。

`canonical_production_journal_bytes`（line 175-176）走 `model_dump(mode="json")`
再 `_canonical`，与 `reciprocal_route_production._canonical` 同强度。

INFO-2 详见下文：`ensure_ascii=False` 跨平台 Unicode normalization。

---

## 环节 8：`compute_journal_sha256` self-digest

通过。line 179-183：

```python
def compute_journal_sha256(journal: ProductionRenderJournal) -> str:
    payload = journal.model_dump(mode="json")
    payload.pop("journal_sha256", None)
    return hashlib.sha256(_canonical(payload)).hexdigest()
```

`payload.pop("journal_sha256", None)` 排除自身字段后再 hash。
default `None` 处理 journal_sha256 缺失情况（向后兼容）。

`new_production_journal`（line 330-344）的初始化流程：
1. 构造 journal with `journal_sha256="0" * 64`（占位）
2. `compute_journal_sha256(journal)` 算真实 digest
3. `journal.model_copy(update={"journal_sha256": ...})` 设置真实 digest

`model_copy` 绕过 validator，但因为 `journal_sha256` 字段无 validator 依赖
（只是 `pattern=r"^[0-9a-f]{64}$"`），且 `"0"*64` 与真实 digest 都满足 pattern，
所以最终 journal 合法。

测试 `test_journal_sha256_covers_the_recorded_durations`（line 248）确认
journal_sha256 包含耗时（即耗时变化 → journal_sha256 变化）。

---

## 环节 9：`production_render_id` 可选 SHA 绑定对称性

通过。5 个可选 SHA 绑定键都走相同流程：

```python
if <field> is not None:
    _require_64_hex_sha(<field>, "<field_name>")
    payload["<field_name>"] = <field>
```

None → 不进入 payload（向后兼容，既有 journal 不受影响）。
非 None → 必须通过 `_require_64_hex_sha`，否则 `ProductionProfileError`。

测试 `test_render_id_binds_environment_module_and_repose_simultaneously`
（line 690-753）确认两个 SHA 同时绑定时：
- `both != only_module`（repose SHA 影响身份）
- `both != only_repose`（module SHA 影响身份）
- 篡改任一 SHA → render_id 变化

测试 `test_render_id_changes_when_environment_module_build_report_sha_changes`
（line 634）确认 SHA 变化 → render_id 变化。

测试 `test_render_id_environment_module_binding_is_deterministic_across_processes`
（line 663）确认跨进程确定性。

`build_adapter` 字符串约束详见 INFO-1。

---

## 环节 10：`revalidate_journal` + `transition_frame` model_copy 绕过兜底

通过。这是 fail-closed 设计的关键环节。

`pydantic` 的 `model_copy(update=...)` **不做校验**——可以直接造出
`state='verified'` 却没有耗时/产物的非法 journal（测试
`test_model_copy_bypasses_validation_so_transitions_must_revalidate` line 278
显式验证这一点）。

`transition_frame`（line 288-302）的兜底机制：

```python
def transition_frame(journal, camera_id, **updates):
    ...
    frames = tuple(
        frame.model_copy(update=updates) if frame.camera_id == camera_id else frame
        for frame in journal.frames
    )
    moved = revalidate_journal(journal.model_copy(update={"frames": frames}))
    return moved.model_copy(update={"journal_sha256": compute_journal_sha256(moved)})
```

1. `frame.model_copy(update=updates)` 绕过 frame validator
2. `journal.model_copy(update={"frames": frames})` 绕过 journal validator
3. **`revalidate_journal(...)` 重新 `model_validate_json(canonical_bytes)`**，
   触发每个嵌套 frame 的 `_validate_state` + journal 的 `_validate_journal`
4. `moved.model_copy(update={"journal_sha256": ...})` 再次绕过 validator，
   但 `journal_sha256` 字段无 validator 依赖，不影响

测试 `test_transition_frame_revalidates_and_refreshes_the_digest`（line 293-313）
显式验证：
- 合法 transition 通过
- `state="verified"` 无 artifacts → `ValidationError: six-file contract`
- `state="verified"` 有耗时无产物 → `ValidationError: wall-clock`

兜底机制有效。

---

## 环节 11：`frames_needing_render` 信任语义

通过。line 305-309：

```python
def frames_needing_render(journal: ProductionRenderJournal) -> tuple[str, ...]:
    """只补【未验证】帧。verified 只是进入复验的门票, 不构成信任 ——
    真正的信任由调用方对磁盘字节重算 sha256 挣回 (沿用 canary 的语义)。
    """
    return tuple(frame.camera_id for frame in journal.frames if frame.state != "verified")
```

docstring 显式声明"verified 不构成信任"，调用方必须对磁盘字节重算 sha256。
这符合 AGENTS.md 的 "provenance safety / fail-closed" 原则。

测试 `test_only_unverified_frames_are_rerendered`（line 331）+ 
`test_every_unverified_state_is_rerendered_not_only_planned`（line 527）
覆盖。

---

## 环节 12：`new_production_journal` 初始化

通过。line 312-344：

```python
def new_production_journal(plan, *, render_id, camera_registry_sha256,
                           camera_ids=None, timeout_limit_seconds=...):
    known = {camera.camera_id for camera in plan.cameras}
    if camera_ids is None:
        selected = tuple(camera.camera_id for camera in plan.cameras)
    else:
        selected = camera_ids
    unknown = [camera_id for camera_id in selected if camera_id not in known]
    if unknown:
        raise ProductionProfileError(
            f"camera IDs are not in the production plan: {unknown}")
    if not selected:
        raise ProductionProfileError("a journal must cover at least one camera")
    journal = ProductionRenderJournal(
        render_id=render_id,
        journal_sha256="0" * 64,
        ...
    )
    return journal.model_copy(update={"journal_sha256": compute_journal_sha256(journal)})
```

3 道门：
1. `unknown` camera_ids → reject（plan 从属校验）
2. empty selected → reject（至少一帧）
3. Pydantic 构造触发 `_validate_journal`（uniqueness）

测试 `test_journal_rejects_camera_ids_outside_the_plan`（line 346）+
`test_journal_subset_matches_a_batch_slice`（line 351）覆盖。

---

## 环节 13：`extrapolate_total_seconds` 诚实性

通过。line 347-380：

```python
def extrapolate_total_seconds(journal, *, target_frame_count):
    measured = [
        frame.wall_clock_seconds
        for frame in journal.frames
        if frame.wall_clock_seconds is not None and frame.state == "verified"
    ]
    if not measured:
        raise ProductionProfileError(
            "cannot extrapolate without at least one measured verified frame")
    mean = sum(measured) / len(measured)
    return {
        "measured_frame_count": len(measured),
        "measured_total_seconds": round(sum(measured), 3),
        "measured_mean_seconds": round(mean, 3),
        "measured_min_seconds": round(min(measured), 3),
        "measured_max_seconds": round(max(measured), 3),
        "target_frame_count": target_frame_count,
        "extrapolated_total_seconds": round(mean * target_frame_count, 3),
        "basis": "arithmetic-mean-of-measured-verified-frames",
        "disclaimer": (
            "extrapolation-not-a-promise: measured frames are the cameras already known "
            "to succeed; unseen production viewpoints may sit closer to dense geometry "
            "and cost more. Do not treat this as a schedule commitment."
        ),
    }
```

诚实性体现：
1. **只取 verified 帧**（line 358-360）—— 不用 failed/planned 冒充
2. **无测量值时 raise**（line 361-364）—— 不静默返回 0
3. **同时返回 measured_* 与 extrapolated_***（line 367-373）—— 调用方无法
   在不看 measured 的情况下只取 extrapolated
4. **强制 disclaimer**（line 375-379）—— 字段名 `disclaimer` + 内容明确
   "extrapolation-not-a-promise"

测试 `test_extrapolation_refuses_without_measurements`（line 364）+
`test_extrapolation_carries_its_disclaimer`（line 370）覆盖。

---

## INFO-level findings

### INFO-1：`build_adapter` 字符串无最小长度约束

**位置**：`production_journal.py:198, 267-268`

**现状**：
```python
def production_render_id(
    ...
    build_adapter: str | None = None,
    ...
):
    ...
    if build_adapter is not None:
        payload["build_adapter"] = build_adapter
```

`build_adapter` 是字符串 adapter 名（如 `"windows-reciprocal-route-v1"`），
**不是 SHA**，所以不需要 `_require_64_hex_sha`。但也没有最小长度 / 字符约束：
- `build_adapter=""` 会被加到 payload（与 None 不同）
- `build_adapter=" "` 也会被加到 payload

**评估**：不是 fail-open（不提升信任）。`build_adapter` 只影响 render_id 的
内容寻址（不同 adapter → 不同 render_id），caller 已经是 trusted 端（caller
自己决定用什么 adapter）。如果 adapter 不存在，runtime 会拒绝执行。

但设计上不对称：5 个 SHA 字段有 `_require_64_hex_sha`，`build_adapter` 无任何
约束。如果未来 caller 传错字符串（如 `None` vs `""`），render_id 会不同。

**建议**：可选加 `Field(min_length=1)` 或在函数内 `if not build_adapter: raise`。
非紧急，可在下次清理时处理。

**风险等级**：INFO（不泄漏信任，仅一致性）。

---

### INFO-2：`_canonical` 的 `ensure_ascii=False` 跨平台 Unicode 一致性

**位置**：`production_journal.py:169-172`

**现状**：
```python
def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
```

`ensure_ascii=False` 允许非 ASCII 字符以 UTF-8 编码出现在 canonical bytes 中。
Python 默认 NFC normalization，但若上游输入使用 NFD（macOS HFS+ 默认），
可能产生不同字节。

**评估**：当前所有 journal 字段都是 ASCII（camera_id、artifact path、SHA
等），未触发。但 `ProductionFrameRecord.error` 字段是 `str | None`，理论上
可携带非 ASCII 错误消息（如中文 stack trace）。若跨平台写入/读取 journal，
可能产生不同 canonical bytes → 不同 journal_sha256 → 跨平台 build 不一致。

**与 REVIEW-OPUS-006 INFO-3 同类**：`reciprocal_route_production._canonical`
有相同 pattern。

**建议**：在 `_canonical` 顶部加 `unicodedata.normalize("NFC", ...)` 保险。
非紧急，与 REVIEW-OPUS-006 INFO-3 一并处理。

**风险等级**：INFO（未触发，预防性）。

---

## 未发现的问题（确认强项）

### 强项 1：5 个可选 SHA 绑定键的完全对称

所有 5 个可选 SHA 绑定键（`preflight_id` / `quality_policy_sha256` /
`post_render_policy_sha256` / `repose_search_sha256` /
`environment_module_build_report_sha256`）走**完全相同**的 fail-closed 路径：
None → 不进入 payload（向后兼容）；非 None → `_require_64_hex_sha` →
进入 payload。无任何不对称。

测试覆盖每个字段的非 hex 拒绝 + 短长度拒绝 + 篡改检测 + 跨进程确定性。

### 强项 2：`transition_frame` 的 model_copy 绕过被 revalidate 兜住

`pydantic` 的 `model_copy(update=...)` 不做校验是已知行为。
`transition_frame` 在 model_copy 后立即调用 `revalidate_journal`，重新
`model_validate_json(canonical_bytes)`，触发每个嵌套 frame 的 `_validate_state`
+ journal 的 `_validate_journal`。任何非法状态变更会被兜住。

测试 `test_model_copy_bypasses_validation_so_transitions_must_revalidate`
显式验证这个兜底机制。

### 强项 3：状态机的"必须报耗时"约束

`verified` / `failed` / `timed-out` 三个终态都强制 `wall_clock_seconds` 非 None。
"没跑过就是 None，绝不填 0 冒充"（docstring line 87）。

`timed-out` 额外要求 `wall_clock_seconds >= timeout_limit_seconds`——
防止 caller 把短时运行冒充超时。

### 强项 4：`extrapolate_total_seconds` 的诚实 disclaimer

返回 dict 同时包含 `measured_*` 与 `extrapolated_*`，调用方无法在不看
measured 的情况下只取 extrapolated。`disclaimer` 字段强制携带。

### 强项 5：render_id 与 journal_sha256 的职责分离

`render_id` = 内容寻址，**绝不**包含耗时（耗时不可复现）。
`journal_sha256` = 自摘要，**包含**耗时（记录"这一次真的跑了多久"）。

docstring（line 10-14）显式声明这个分工。

---

## 测试覆盖确认

被审文件对应的测试：

```text
tests/test_synthetic_village_production_journal.py
  41 tests: 全过

覆盖关键 fail-closed 路径:
- 状态机 5 个分支 (verified/failed/timed-out/planned/rendering)
- 六文件契约 (kind/path 元组比对)
- 5 个可选 SHA 绑定键的 _require_64_hex_sha
- model_copy 绕过被 revalidate 兜住
- transition_frame 的合法/非法 transition
- journal_sha256 包含耗时
- render_id 不包含耗时
- extrapolate 的 disclaimer + 无测量值时 raise
- 跨进程确定性

未覆盖:
- build_adapter 字符串约束 (INFO-1)
- _canonical 跨平台 Unicode normalization (INFO-2)
```

---

## 验证命令

```bash
# 被审文件的现有测试
D:\Python313\python.exe -m pytest \
  tests/test_synthetic_village_production_journal.py -q
# => 41 passed in 0.59s
```

---

## 结论

`production_journal.py` 是 fail-closed 的生产档逐帧 durable render journal。
13 个审计环节全过，无 fail-open 风险。2 条 INFO-level finding 均不泄漏信任，
不影响 `simplified-pbr-not-render-parity / L2 / trust_effect=none` 的 Literal
锁定。

commit `e121036` 新增的 `environment_module_build_report_sha256` 绑定与
既有 4 个可选 SHA 绑定键（`preflight_id` / `quality_policy_sha256` /
`post_render_policy_sha256` / `repose_search_sha256`）走完全对称的
fail-closed 路径，TDD 覆盖完整（非 hex 拒绝 + 短长度拒绝 + 篡改检测 +
跨进程确定性 + 同时绑定测试）。

被审文件的实现质量高：
- 5 个可选 SHA 绑定键完全对称的 `_require_64_hex_sha` 校验
- `transition_frame` 的 model_copy 绕过被 `revalidate_journal` 兜住
- 状态机的"必须报耗时"约束 + timed-out 的限额校验
- `extrapolate_total_seconds` 的强制 disclaimer + 无测量值时 raise
- `render_id`（内容寻址）与 `journal_sha256`（自摘要）的职责分离
- `_require_64_hex_sha` 独立于 caller Pydantic schema，公开 API 自身 fail-closed

Opus lane 对 production_journal.py 放行。INFO-1（build_adapter 字符串约束）
与 INFO-2（Unicode normalization）可与 REVIEW-OPUS-006 INFO-3 一并在下次
清理时处理，非紧急。

---

Co-Authored-By: GLM-5.2 <noreply@zai.com>
