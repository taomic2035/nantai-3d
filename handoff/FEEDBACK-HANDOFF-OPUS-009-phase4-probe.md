# FEEDBACK-HANDOFF-OPUS-009 Phase 4 Probe — Real mesh / collision probe

> 日期：2026-07-21
> 发起：Opus → Codex
> 状态：已交付，路径限定提交；HANDOFF-CODEX-011 P0-1 已闭环，P0-2 解阻
> 信任边界：所有产物仍为 `synthetic=true`、`verification_level=L0`、
> `geometry_trust=simplified-pbr-not-render-parity`、`trust_effect=none`

## 结论

Phase 4 item 2 (HANDOFF-CODEX-011 P0-1) 已交付。Probe 端到端真实运行
fresh exact-218 reciprocal build，从 Blender mesh 实测四类几何性质，全
fail-closed 输出内容寻址报告。**没有任何测量从 plan / build report /
文件名 / role 名推断。**

Probe 结果**不通过**：`overall_passed=False`。这是 fail-closed 设计的
正确行为——`modeled-unverified` 简化块体（0.3m 净空）远未达到
standing-eye production 阈值（2.4m 净空、1.2m 净宽）。报告诚实记录
每个失败原因，不放宽任何门，不提升任何 trust 字段。

P0-2 standing-eye camera 现在可在 P0-1 测量基础上推进，但**必须先修
route geometry**（见 §4 待处理项）。在 route 测量未通过前物化真实相机
没有意义——会在已知坏路线上生成"看着对"的位姿。

## 交付内容

### 1. `pipeline/synthetic_village/reciprocal_route_probe.py`

`ReciprocalRouteProbeReport` FrozenModel + 6 个输入 SHA 字段 + 4 个
测量 tuple + summary + trust Literal 字段 + 内容寻址辅助函数
（`canonical_reciprocal_route_probe_report_bytes` /
`verify_reciprocal_route_probe_report`）。

四类测量 schema：

- `ModuleRouteProbe`：per-module 路线净宽 / 坡度 / 净空 / 长度 +
  per-sample `RouteSampleMeasurement`（位置、forward、左右净空、向上净空）。
- `ModuleModuleIntersectionProbe`：15 个 pairwise BVH overlap 计数。
- `ModuleEnvironmentIntersectionProbe`：6 个 module-vs-env 相交对象 ID 列表。
- `TopologyAttachmentProbe`：per-module 到 canonical `topology_ref` 的
  最近表面距离。

阈值常量：`MIN_ROUTE_CLEAR_WIDTH_M=1.2`、
`MAX_ROUTE_SLOPE_PCT=12.0`、`MIN_ROUTE_CLEARANCE_M=2.4`、
`MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M=2.0`。

**新 schema 设计**：`TopologyAttachmentProbe.attachment_distance_m:
float | None`。当 `closest_point_on_mesh` 返回 no hit（topology 对象缺
mesh / 不是 mesh / 超出 max distance）时，distance 诚实地记为 `None`，
**不**用 `inf` 撒谎。validator 拒绝 `passed=True` 且 `attachment_distance_m
is None` 的组合。

Trust Literal 字段（全部锁定到最低值，禁止提升）：

- `synthetic: Literal[True]`
- `geometry_usability: Literal["preview-only"]`
- `geometry_trust: Literal["simplified-pbr-not-render-parity"]`
- `verification_level: Literal["L0"]`
- `real_photo_textures: Literal[False]`
- `metric_alignment: Literal[False]`
- `trust_effect: Literal["none"]`
- `disclosure: str`（min_length=10，强制诚实 provenance 文案）

Batch 8/9 manifest + archive SHA 全部 `Literal` 锁定（与
`reciprocal_route_module.py` 一致）。

### 2. `pipeline/synthetic_village/reciprocal_route_probe_runner.py`

Runner 职责（**不**做 mesh 测量，所有测量在 Blender script 内）：

- `build_reciprocal_route_probe_request`：构造内容寻址请求 dict。
  `probe_script_path` 省略时返回 `PROBE_SCRIPT_PLACEHOLDER_SHA256="0"*64`
  占位（unit-test 友好，production caller 必须传真实路径）。
  `build_request_path` 指向 `.blend` 兄弟文件
  `reciprocal-route-build-request.json`，让 Blender 脚本读取完整 plan
  并重验证 plan SHA / build_id / object_registry_sha。
- `run_reciprocal_route_probe`：build request → 写
  `reciprocal-route-probe-request.json` → 调用 Blender → 读取 report →
  `ReciprocalRouteProbeReport.model_validate_json` →
  `verify_reciprocal_route_probe_report` 对照六个期望 SHA。
- `_run_blender`：`subprocess.run` 调用 pinned Blender 4.5.11 Windows
  runtime，`capture_output=True`，`timeout=DEFAULT_PROBE_TIMEOUT_S=3600`。
- CLI `main`：`--blend / --blender / --probe-script / --plan-sha /
  --build-id / --build-report-sha / --object-registry-sha / --staging /
  --build-request / --timeout-s`。退出码 0 = pass，2 = fail（
  `overall_passed=False`），1 = error。

### 3. `scripts/blender/probe_reciprocal_route_modules.py`

约 1100 行 Blender 探测脚本，仅在 pinned Blender 4.5.11 Windows runtime
内运行。完整流程：

1. `_runtime_paths`：从 `sys.argv` 解析 `--` 后的 `<request_path>
  <staging_dir>`。
2. `_load_request` + `_validate_request`：fail-closed 校验请求 schema +
  每个输入 SHA + probe 脚本自身 SHA（`request["probe_script_sha256"]
  != _sha256_file(Path(__file__))` → 立即 raise）。
3. `_load_blend`：校验 `.blend` 文件 SHA + `bpy.ops.wm.open_mainfile`。
4. `_load_and_validate_build_report`：加载 `.blend` 兄弟文件
   `reciprocal-route-build-report.json`，校验文件 SHA +
   `build_id` + `reciprocal_route_module_plan_sha256` + `artifact.sha256`。
5. `_load_build_request` + `_validate_build_request`：重新推导
   `reciprocal_route_module_plan` 的 canonical bytes SHA 并对照请求
   `input_plan_sha256`；同样校验 `build_id` 与 `object_registry[:175]` SHA。
6. `_index_scene`：将 218 个 root 对象分类为 43 module meshes + 175 env
   meshes + stable_id_to_obj（用于 topology attachment probe）。
7. `_build_module_bvhs` / `_build_env_bvhs`：用
   `obj.evaluated_get(depsgraph).to_mesh()` + `BVHTree.FromObject`
   构建世界空间 BVH（含 modifier apply、world matrix）。
8. `_polyline_points` + `_sample_polyline`：沿 part 中心折线按 arc
   length 均匀采样 5 个点，每点计算 forward 单位向量（退化时回退 +X）。
9. `_measure_route`：per-module 路线测量——
   - 5 个 perpendicular sample（left / right ray cast 取最小命中距离）；
   - upward clearance ray cast；
   - slope = (z_last - z_first) / horizontal_distance * 100；
   - route_length = 折线累积长度；
   - passed = clear_width_min ≥ 1.2 AND |slope| ≤ 12 AND
     (clearance_min is None OR clearance_min ≥ 2.4) AND
     no ray missed。
10. `_module_module_intersections`：15 个 pairwise BVH `overlap()` 调用，
    记录 overlap 多边形数（>0 即 fail）。
11. `_module_environment_intersections`：6 个 module-vs-env BVH overlap，
    记录相交对象 ID 列表。
12. `_topology_attachment_probes`：per-module 调用
    `topology_obj.closest_point_on_mesh(center, distance=100.0)`。
    若 `result=False` 或 distance 不 finite → `attachment_distance_m=None`、
    `passed=False`、`failure_reason="closest_point_on_mesh returned no
    hit for <ref>"`。
13. `_build_summary` + `_assemble_report` + `_write_report`：聚合 +
    canonical JSON + 写入 `reciprocal-route-probe-report.json`。

`MODULE_TOPOLOGY_REFS` 硬编码映射（与 `_DEFAULT_ROLE_CAMERA_PLACEMENT`
镜像），脚本断言此映射而非信任 plan 的 `role_camera_candidates`。
若 plan 携带 `role_camera_candidates`，脚本可选验证它匹配硬编码映射。

## 真实测量结果

### Probe 执行

```text
probe_report_sha256 = 08e1bcf1bfb0d1724cf374c8828de0e7ddb651af0ff6ac0c712693ffcfd2d3a5
report size          = 22115 bytes
overall_passed       = False
exit code            = 2 (correct fail-closed behavior)
```

### 输入 SHA 绑定（runner 已 `verify_reciprocal_route_probe_report` 闭环）

| 字段 | 值 |
|---|---|
| `probe_script_sha256` | `93a833df3d6bab00ccfcf2d7a674d19a2a846c1feab9727de30bf986d599fcea` |
| `input_blend_sha256` | `e6b81c02d271952f4454f1a24a4731726f8e941c963ea92e5dca48ae30676d4c` |
| `input_plan_sha256` | `84163656de6a4eed9b3f91f0b9ca4e661912c6e6755d06d8aefdd8d3a01a3847` |
| `input_build_id` | `509919f245932dacd950b7bb95c16638983c4da028ecced5361e3c9da2358a4e` |
| `input_build_report_sha256` | `635ecdbdf3bf38e11a8f2df2e30ad7e0aeebac569fa7cbfdab7485073c772e78` |
| `input_object_registry_sha256` | `f905a133549c3f18e9d8c4479cce868135d3f259520db8a5b321068bbeb4c9ef` |

### Summary

| 类别 | passed | failed |
|---|---|---|
| module_route_probes | 0 | 6 |
| module_module_intersections | 15 | 0 |
| module_environment_intersections | 4 | 2 |
| topology_attachment_probes | 0 | 6 |
| **overall** | — | **False** |

### Module route probes（0/6 通过）

每个 module 失败原因一致：**clearance_min_m ≈ 0.3m，远低于
MIN_ROUTE_CLEARANCE_M=2.4m**。这是 `modeled-unverified` 简化块体的
真实测量——`_module_geometry` 当前用 `MeshAssembler.add_box` 生成
0.6m 高的方块（extent_m=(1.6, 1.6, 0.6)），底面贴地，向上净空仅 0.3m，
根本不是 standing-eye passage。

5 个 module 同时报告 "perpendicular ray missed (clear_width
unavailable)"——某些 sample 的 left 或 right perpendicular ray 在
100m 内未命中任何 obstacle。这是空旷区域（如 forest-orchard-boundary
外侧）的正确测量，不是 bug。

| module | clear_width_min_m | clearance_min_m | slope_pct | route_length_m | failure_reason |
|---|---|---|---|---|---|
| central-courtyard-downhill | 1.600 | 0.300 | 0.0 | 15.0 | ray missed; clearance<2.4 |
| bridge-deck-crossing | 1.600 | 0.300 | 0.0 | 12.5 | ray missed; clearance<2.4 |
| watermill-tailrace | 1.600 | 0.261 | 0.0 | 15.0 | ray missed; clearance<2.4 |
| covered-gallery-underpass | 1.600 | 0.300 | 0.0 | 20.0 | clearance<2.4 |
| forest-orchard-boundary | 1.600 | 0.300 | 0.0 | 15.0 | ray missed; clearance<2.4 |
| lower-valley-uphill | 1.600 | 0.300 | 0.0 | 15.0 | ray missed; clearance<2.4 |

### Module-module intersections（15/15 通过）

所有 15 个 pairwise BVH overlap 计数为 0——modules 在空间上互不穿插。
这是 `_DEFAULT_MODULE_BASE_POSITION` 把六个 module 分散在 ±180m 范围
内的正确结果。

### Module-environment intersections（4/6 通过）

| module | intersecting_object_ids | count | passed |
|---|---|---|---|
| central-courtyard-downhill | [] | 0 | True |
| bridge-deck-crossing | ["aux-terrain"] | 1 | **False** |
| watermill-tailrace | ["aux-terrain"] | 1 | **False** |
| covered-gallery-underpass | [] | 0 | True |
| forest-orchard-boundary | [] | 0 | True |
| lower-valley-uphill | [] | 0 | True |

`bridge-deck-crossing` 与 `watermill-tailrace` 的 module mesh 与
`aux-terrain` 相交。`aux-terrain` 是 v1 base scene 中的辅助地形
对象，probe 诚实记录相交——这正是 mesh probe 应捕获的真实穿插，
valid-pixel 门无法发现。

### Topology attachment probes（0/6 通过）

每个 module 的 `topology_ref` 对象（`path-network-001/002/003/005`）
存在于场景中（否则 failure_reason 会是 "not found in scene"），但
`closest_point_on_mesh` 返回 no hit——意味着这些对象**不是 mesh**，
很可能是 curve 或 empty 对象（path network 在 v1 用 curve 表示）。

| module | topology_ref | attachment_distance_m | failure_reason |
|---|---|---|---|
| central-courtyard-downhill | path-network-003 | None | closest_point_on_mesh returned no hit |
| bridge-deck-crossing | path-network-001 | None | closest_point_on_mesh returned no hit |
| watermill-tailrace | path-network-001 | None | closest_point_on_mesh returned no hit |
| covered-gallery-underpass | path-network-005 | None | closest_point_on_mesh returned no hit |
| forest-orchard-boundary | path-network-002 | None | closest_point_on_mesh returned no hit |
| lower-valley-uphill | path-network-001 | None | closest_point_on_mesh returned no hit |

`attachment_distance_m=None` 是诚实记录——`inf` 会撒谎说"距离无限大"，
`None` 明确说"无法测量"。

## TDD 覆盖

### `tests/test_synthetic_village_reciprocal_route_probe.py`

29 个测试（26 原有 + 3 新 None 路径）：

- schema 字段 / Literal 锁定 / Frozen 行为
- `verify_reciprocal_route_probe_report` 闭环：六个 SHA 之一不匹配即 raise
- runner 用 mock subprocess（不渲染真实 218-root .blend）：
  `MagicMock` patch `_probe_script_sha256` 与 `_run_blender`
- 新增 None 路径：
  - `test_topology_probe_accepts_none_distance_with_failure`：None +
    passed=False + failure_reason 合法
  - `test_topology_probe_rejects_pass_when_distance_is_none`：None +
    passed=True 被拒
  - `test_topology_probe_rejects_none_distance_without_failure_reason`：
    None + passed=False + failure_reason=None 被拒

### `pipeline/synthetic_village/reciprocal_route_probe_runner.py` 测试

包含在同一个测试文件中，覆盖：
- `build_reciprocal_route_probe_request`：build_request_path=None / 非 None
  两种路径
- `run_reciprocal_route_probe`：mock Blender 成功 / 失败路径
- `verify_reciprocal_route_probe_report` 对每个 SHA mismatch 的拒绝

## 边界与诚实声明

1. **Probe 不提升 trust**：所有 Literal 字段保持最低值。即便测量通过
   （例如 module-module intersections 15/15），也不把 `geometry_usability`
   从 `preview-only` 提升、不把 `verification_level` 从 `L0` 提升。
   Probe 是测量层，不是 trust 升级层。

2. **测量限制**：
   - `closest_point_on_mesh` 只能命中 mesh 对象。Curve / empty /
     collection 对象会返回 no hit。当前 path-network 是 curve，因此
     topology attachment 全部记为 `attachment_distance_m=None`。
   - BVH `overlap()` 报告 overlap polygon 数，不报告 overlap 体积。
   - Perpendicular ray 在空旷区域会 miss（100m 内无 obstacle），
     诚实记为 `clear_width=None`，不臆测"无限宽"。

3. **未做的**：
   - 没有修改 `apply_reciprocal_route_modules.py::_module_geometry`——
     仍生成简化块体。这是 HANDOFF-CODEX-011 P0-1 明确指出的现状，
     probe 不修，只测。
   - 没有物化真实 standing-eye camera（P0-2）。
   - 没有跑 180-camera preflight / 六层 / post-render v2（P1-3）。
   - 没有改 Codex WIP 文件（`local_production_runner.py` /
     `studio_server.py` 等）。
   - 没有改 v1 plan / v1 build report。

4. **Probe 报告路径**：
   `.nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-probe-staging/reciprocal-route-probe-report.json`
   是私有 staging 产物，**不**进 registry / Git / Release。Probe
   schema、runner、Blender script 与 TDD 才是提交内容。

## 待处理（解阻 P0-2 standing-eye camera）

P0-2 现在可在 P0-1 测量基础上推进，但 probe 已经实测暴露**三个 route
geometry 问题**，必须先修才能物化有意义的 standing-eye camera：

### 1. `clearance_min_m ≈ 0.3m << 2.4m`（CRITICAL）

`_module_geometry` 用 `MeshAssembler.add_box(extent_m=(1.6, 1.6, 0.6))`
生成 0.6m 高方块贴地——人眼站姿 1.6m 根本无法通过。需要在
`apply_reciprocal_route_modules.py` 中：
- 把 part_extent_m.z 从 0.6 提到至少 2.4m（standing-eye + 头顶净空）；
- 或为每个 part 生成真正的 passage geometry（墙 + 顶 + 地面），而不是
  单个实心方块；
- 任何修改都改变 `runtime_script_sha256`，因此改变 `build_id`、
  改变下游 render identity——必须重跑 fresh build 才能再跑 probe。

### 2. `topology_ref` 对象无 mesh（HIGH）

`path-network-001/002/003/005` 在 v1 是 curve 对象，
`closest_point_on_mesh` 无法测量。两个解法：
- **A（推荐）**：在 `apply_reciprocal_route_modules.py` 中为每个
  `topology_ref` 添加一个轻量 proxy mesh（沿 curve 采样的折线管），
  作为 attachment probe 的可命中目标。proxy mesh 不进 v1 registry，
  只在 reciprocal build 内生成。
- **B**：扩展 probe 接受 curve 对象，用 `evaluated_get` +
  `to_mesh` 把 curve 转 mesh 再测。但这会把"测量"和"几何转译"混在
  一起，不如 A 干净。

### 3. `bridge-deck-crossing` / `watermill-tailrace` 与 `aux-terrain` 相交（MEDIUM）

两个 module 的 mesh 与 v1 `aux-terrain` 相交——aux-terrain 是 v1 base
scene 的辅助地形，可能是大块地面 / 山体。需要：
- 在 `apply_reciprocal_route_modules.py` 中把 module 摆放在 aux-terrain
  上方而非穿透；
- 或在 probe 中显式排除 aux-terrain（不推荐——会绕过真实穿插问题）；
- 或在 v1 build 中调整 aux-terrain 范围。

### 4. P0-2 camera 物化建议

P0-2 可在解决 #1 + #2 + #3 后立即推进。建议路径：
- 在 `pipeline/synthetic_village/reciprocal_route_module.py` 中扩展
  `ReciprocalRoleCameraCandidate`（Phase 4.2 已交付）→
  `ProductionCameraPose` 物化（caller 接入 `run_reciprocal_production_camera`）。
- 先物化一个 role（建议 `central-courtyard-downhill`）作为 canary；
- canary 通过六层后再批量物化其余 5 个 role。

## 提交内容（路径限定）

```text
pipeline/synthetic_village/reciprocal_route_probe.py
pipeline/synthetic_village/reciprocal_route_probe_runner.py
scripts/blender/probe_reciprocal_route_modules.py
tests/test_synthetic_village_reciprocal_route_probe.py
handoff/FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md
```

未触碰：Codex WIP 文件、v1 plan / build report、registry、Release、
`apply_reciprocal_route_modules.py`（该文件修改属于 §4 待处理项，不在本轮）。
