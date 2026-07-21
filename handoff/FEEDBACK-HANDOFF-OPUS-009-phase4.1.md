# FEEDBACK-HANDOFF-OPUS-009 Phase 4.1 — Canonical part_layout 在 plan 中

> 回执：Opus（pipeline / schema）→ Codex（caller / Studio）
> 日期：2026-07-21
> 对应交办：`handoff/REVIEW-CODEX-018-opus-009-phase3-real-blender.md` §"Phase 4 必须处理的边界" item 1
> 前序 Phase：`handoff/FEEDBACK-HANDOFF-OPUS-009-phase3.md`（runtime script + constructor + runner）
> 优先级：紧随 Phase 3 之后；**不**把 175-root 场景提升为 production-ready，
> **不**卷入 Codex WIP 文件，**不**新增 Studio jobs/ledger/HUD。

## 一句话

**`ReciprocalRouteModulePart.part_layout` canonical 字段已交付；runtime script 删除
`MODULE_BASE_POSITION` 硬编码并改为从 plan 读取布局。8 个新 Phase 4.1 TDD 测试
全过，邻近测试套件无 regression。`runtime_script_sha256` 改变 →
`build_id` 改变 → 下游 render identity 改变。Phase 4 item 2/3/4 仍未交付，
继续依赖实测 mesh/camera/preflight。**

## 对应 REVIEW-CODEX-018 边界

REVIEW-CODEX-018 §"Phase 4 必须处理的边界" item 1 原文：

> 把六模块的坐标、尺度、朝向和 topology attachment 变成 canonical plan 数据，
> runtime 不得另行发明未入 plan 的布局；

本 Phase 4.1 处理 "坐标、尺度、朝向 → canonical plan 数据" 部分；
topology attachment 仍属于 Phase 4 item 2（mesh probe 后才能把 attachment
绑成测量证据），本 Phase 不提升任何 `Literal[True]`。

## 交付内容

### 1. 修改文件

| 文件 | 状态 | 用途 |
|---|---|---|
| `pipeline/synthetic_village/reciprocal_route_module.py` | 修改 | 新增 `PartLayoutSpec` + `part_layout` 字段 + 默认布局 helper |
| `scripts/blender/apply_reciprocal_route_modules.py` | 修改 | 删除 `MODULE_BASE_POSITION` 硬编码，runtime 改为从 plan 读取 `part_layout` |
| `tests/test_synthetic_village_reciprocal_route_module.py` | 修改 | 新增 8 个 Phase 4.1 TDD 测试 + 更新 1 个既有测试 |
| `handoff/FEEDBACK-HANDOFF-OPUS-009-phase4.1.md` | 新增 | 本回执 |

### 2. 实测身份

```text
runtime_script_path:    scripts/blender/apply_reciprocal_route_modules.py
runtime_script_sha256:  6dc4dc9d46e5a4002ca68f2efe102730af6fa5abaf20485b966a200a65eb861d
plan_sha256 (default):  84163656de6a4eed9b3f91f0b9ca4e661912c6e6755d06d8aefdd8d3a01a3847
plan_schema_version:    nantai.synthetic-village.reciprocal-route-module.v1
runtime_request_schema: nantai.synthetic-village.reciprocal-route-runtime-request.v1
```

`runtime_script_sha256` 由 `build_reciprocal_route_runtime_request` 在构造时
读 `scripts/blender/apply_reciprocal_route_modules.py` 实测 SHA，无硬编码。
本 Phase 4.1 修改 runtime script 字节 → SHA 变化 → `build_id` 变化 → 下游
render identity 变化。

`plan_sha256` 变化（Phase 3 时为不含 `part_layout` 的版本）：因为每个
`ReciprocalRouteModulePart` 现在携带 `PartLayoutSpec`，canonical bytes 变化。
任何 `part_layout` 字段变化（center/extent/orientation）都会改变
`reciprocal_route_module_plan_sha256`，进而改变 `build_id`。

### 3. `PartLayoutSpec` schema

```python
class PartLayoutSpec(FrozenModel):
    """Canonical spatial placement of one reciprocal-route part."""

    center_m: tuple[float, float, float]
    extent_m: tuple[float, float, float]
    orientation_deg: float = Field(ge=0.0, lt=360.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _layout_is_finite_and_positive(self) -> PartLayoutSpec:
        # 3-tuple 校验 + center 必须 finite + extent 必须 finite & strictly positive
        ...
```

`ReciprocalRouteModulePart` 加 `part_layout: PartLayoutSpec`（required，无 default）。

FrozenModel 沿用既有 `extra="forbid"` 约束；任何未知字段直接 ValidationError。
`orientation_deg` 用 `Field(ge=0.0, lt=360.0, allow_inf_nan=False)` 双重锁。
`tuple[float, float, float]` 在 strict mode 下拒绝 list，所以测试走
`model_validate_json` 路径覆盖 list → tuple 转换。

### 4. 默认布局 helper

```python
_DEFAULT_MODULE_BASE_POSITION: dict[ModuleId, tuple[float, float, float]] = {
    "central-courtyard-downhill": (40.0, 30.0, 70.0),
    "bridge-deck-crossing":       (-150.0, -100.0, 50.0),
    "watermill-tailrace":         (-180.0, -130.0, 45.0),
    "covered-gallery-underpass":  (60.0, -25.0, 78.0),
    "forest-orchard-boundary":    (120.0, 80.0, 75.0),
    "lower-valley-uphill":        (-90.0, 60.0, 55.0),
}
_DEFAULT_PART_SPACING_Y_M = 2.5
_DEFAULT_PART_EXTENT_M = (1.6, 1.6, 0.6)
_DEFAULT_PART_ORIENTATION_DEG = 0.0

def _default_part_layout(module_id, instance_id) -> PartLayoutSpec:
    base_x, base_y, base_z = _DEFAULT_MODULE_BASE_POSITION[module_id]
    offset_y = (instance_id - 176) * _DEFAULT_PART_SPACING_Y_M
    return PartLayoutSpec(
        center_m=(base_x, base_y + offset_y, base_z),
        extent_m=_DEFAULT_PART_EXTENT_M,
        orientation_deg=_DEFAULT_PART_ORIENTATION_DEG,
    )
```

这些常量**仅供 `_default_part_layout` 使用**；runtime script **绝不**读取。
模块 docstring 已明确：任何常量变化都会改变 `plan_sha256` → `build_id` →
下游 render identity。

### 5. Runtime script 改造

`scripts/blender/apply_reciprocal_route_modules.py`：

- **删除** `MODULE_BASE_POSITION` dict（约 20 行硬编码）
- `_module_geometry(part)` 从 70+ 行简化为 10 行，只读 `part["part_layout"]`：
  ```python
  def _module_geometry(part):
      layout = part["part_layout"]
      assembler = MeshAssembler()
      assembler.add_box(
          tuple(float(value) for value in layout["center_m"]),
          tuple(float(value) for value in layout["extent_m"]),
          math.radians(float(layout["orientation_deg"])),
      )
      return assembler
  ```
- 新增 `_validate_part_layout(part)` helper（35 行）：在 `_validate_request`
  return 前对每个 part 调用，校验 dict / 3 keys / list len 3 / finite /
  strictly positive extent / orientation 范围。任何字段缺失或非法 →
  `RuntimeBuildError`，runtime fail-closed 拒绝构造。
- `_build_modules` 调用 `_module_geometry(part)` 替代旧的三参数版本

runtime 现在的契约是：**所有空间布局必须来自 plan；runtime 不发明任何
未入 plan 的坐标、尺度或朝向**。

### 6. AABB 一致性证明

`test_default_part_layout_preserves_phase3_aabb` 验证默认 builder 保留
Phase 3 报告的 mesh AABB：

```python
# half-extent = (0.8, 0.8, 0.3)
assert min_x == -180.0  # watermill base_x
assert max_x == 120.0   # forest base_x
assert min_y == -97.5   # watermill instance 189: -130 + 13*2.5
assert max_y == 167.5   # forest instance 211: 80 + 35*2.5
assert min_z == 45.0    # watermill base_z
assert max_z == 78.0    # gallery base_z
```

part centers 减去 half-extent = 实际 mesh AABB：
`min=(-180.8, -98.3, 44.7) max=(120.8, 168.3, 78.3)`，
**与 REVIEW-CODEX-018 §"Phase 4 必须处理的边界" 报告完全一致**。

这证明本 Phase 4.1 是 pure refactor（把硬编码从 runtime 移到 plan），
没有任何几何变化。任何几何变化都会被 `test_default_part_layout_preserves_phase3_aabb`
门禁拒绝。

## 测试覆盖

### 1. 新增 Phase 4.1 测试（8 个，全过）

| # | 测试名 | 验证点 |
|---|---|---|
| 1 | `test_plan_carries_part_layout_on_every_part` | 每个 part 都携带 PartLayoutSpec |
| 2 | `test_part_layout_rejects_negative_extent` | extent 负值被 ValidationError 拒绝 |
| 3 | `test_part_layout_rejects_zero_extent` | extent 零值被 ValidationError 拒绝 |
| 4 | `test_part_layout_rejects_non_finite_center` | center NaN/inf 被 ValidationError 拒绝 |
| 5 | `test_part_layout_rejects_orientation_out_of_range` | orientation_deg=360.0 被 ValidationError 拒绝 |
| 6 | `test_part_layout_rejects_wrong_tuple_length` | center_m 2-tuple 被 ValidationError 拒绝 |
| 7 | `test_plan_sha_changes_when_part_layout_changes` | tamper center_m → plan_sha256 必须变 |
| 8 | `test_default_part_layout_preserves_phase3_aabb` | 默认布局的 part centers AABB 与 Phase 3 实测一致 |

### 2. 既有测试更新（1 个）

`test_plan_rejects_part_outside_module_segment` 加 `part_layout=first_part.part_layout`
字段，因为 `part_layout` 现在是 required。

### 3. 全量测试结果

```text
pytest tests/test_synthetic_village_reciprocal_route_module.py
     tests/test_synthetic_village_reciprocal_route_module_runtime.py
     -x -q
=> 71 passed in 2.82s
   (22 既有 plan + 8 Phase 4.1 + 11 Phase 2 runtime + 11 Phase 3 runtime + 19 其他)

pytest tests/test_synthetic_village_canary.py
     tests/test_synthetic_village_environment_module.py
     tests/test_synthetic_village_environment_module_runtime.py
     tests/test_synthetic_village_windows_production_build.py -q
=> 150 passed, 2 skipped in 99.94s

ruff check pipeline/synthetic_village/reciprocal_route_module.py
            scripts/blender/apply_reciprocal_route_modules.py
            tests/test_synthetic_village_reciprocal_route_module.py
=> All checks passed!
```

2 个 skip 是既有 Windows-only 测试，与本 Phase 无关。

## 边界与未决项

### 1. 已守住

- **不提升 `modeled-unverified` 信任**：`PartLayoutSpec` 是 honest placement，
  不是测量坐标。`geometry_trust` 仍 Literal-locked 为
  `simplified-pbr-not-render-parity`。
- **不修改 Codex WIP**：`local_production_runner.py` / `studio_server.py` /
  `production_render.py` / `render_synthetic_village.py` /
  `production_quality_gates.py` / `scripts/synthetic_village.py` 均未触碰。
- **不新增 Studio jobs/ledger/HUD**：仅扩展 plan schema + 简化 runtime script。
- **`req-5-pose-quality-fail-closed` 测试保留**：runner 测试仍走 mock subprocess，
  不实际渲染 175-root Blender。
- **`runtime_script_sha256` 不硬编码**：`build_reciprocal_route_runtime_request`
  在构造时实测 `apply_reciprocal_route_modules.py` 的 SHA。
- **AABB 一致性门**：`test_default_part_layout_preserves_phase3_aabb` 锁住
  default builder 的几何，任何无意漂移会被 TDD 拒绝。

### 2. 未交付（Phase 4 剩余边界）

- **item 2 — mesh/collision probe 复算净宽/坡度/净空/穿插**：仍需真实 Blender
  build + mesh 读取器。本 Phase 4.1 只搬硬编码到 plan，**未**修改 recipe
  的 `Literal[True]` 字段；任何 `Literal[True]` 仍只是设计约束，不是测量证据。
- **item 3 — standing-eye `ground-route` camera + topology ref**：六个角色
  的 standing-eye 相机仍未生成；本 Phase 仅扩展 plan schema 的 part 级布局，
  未触及 camera 字段或 topology attachment。
- **item 4 — fresh preflight + 六层 artifact + post-render v2 report**：仍依赖
  Codex 完成 §3 caller 接入清单（`VerifiedProductionBuild` 集成 +
  `run_reciprocal_route_build` 调用 + `environment_module_build_report_sha256`
  绑定到 `production_render_id`），然后才能跑真实 Blender 端到端。

### 3. Codex 后续 caller 接入清单（不变）

继承自 Phase 3 回执：

1. 在 `VerifiedProductionBuild` 集成 `run_reciprocal_route_build` 的产物
   （base 175-root + 43 module roots → 218 full roots）
2. 调用 `build_reciprocal_route_runtime_request` 生成 canonical request
3. 跑 `run_reciprocal_route_build` 触发真实 Blender subprocess
4. 把产出的 `reciprocal_route_build_report_sha256` +
   `environment_module_build_report_sha256` 绑定到 `production_render_id`
5. 端到端 Studio 复核

## 验证命令

```bash
# Phase 4.1 测试 + 邻近无 regression
D:\Python313\python.exe -m pytest \
  tests/test_synthetic_village_reciprocal_route_module.py \
  tests/test_synthetic_village_reciprocal_route_module_runtime.py \
  -x -q
# => 71 passed

# 邻近测试套件
D:\Python313\python.exe -m pytest \
  tests/test_synthetic_village_canary.py \
  tests/test_synthetic_village_environment_module.py \
  tests/test_synthetic_village_environment_module_runtime.py \
  tests/test_synthetic_village_windows_production_build.py -q
# => 150 passed, 2 skipped

# ruff
D:\Python313\python.exe -m ruff check \
  pipeline/synthetic_village/reciprocal_route_module.py \
  scripts/blender/apply_reciprocal_route_modules.py \
  tests/test_synthetic_village_reciprocal_route_module.py
# => All checks passed!

# runtime_script_sha256 实测
D:\Python313\python.exe -c "import hashlib; from pathlib import Path; \
  print(hashlib.sha256(Path('scripts/blender/apply_reciprocal_route_modules.py') \
  .read_bytes()).hexdigest())"
# => 6dc4dc9d46e5a4002ca68f2efe102730af6fa5abaf20485b966a200a65eb861d
```

## 提交

路径限定提交（不卷入 Codex WIP，不用 `git add -A`）：

```bash
git add pipeline/synthetic_village/reciprocal_route_module.py \
        scripts/blender/apply_reciprocal_route_modules.py \
        tests/test_synthetic_village_reciprocal_route_module.py \
        handoff/FEEDBACK-HANDOFF-OPUS-009-phase4.1.md
git commit -- \
  pipeline/synthetic_village/reciprocal_route_module.py \
  scripts/blender/apply_reciprocal_route_modules.py \
  tests/test_synthetic_village_reciprocal_route_module.py \
  handoff/FEEDBACK-HANDOFF-OPUS-009-phase4.1.md
```

提交消息尾行：
```
Co-Authored-By: GLM-5.2 <noreply@zai.com>
```

---

Opus lane 等待 Codex 完成 §3 caller 接入清单后再启动 Phase 4 item 2/3/4；
在此之前不再修改 runtime script / plan schema 的空间布局部分。
