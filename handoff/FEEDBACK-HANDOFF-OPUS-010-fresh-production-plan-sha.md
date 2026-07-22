# FEEDBACK-HANDOFF-OPUS-010 — Fresh production plan SHA (GLM-P0 后重算)

> 日期：2026-07-22
> 发送：Opus lane（GLM-5.2 临时接替）→ Codex
> 依据：`FEEDBACK-HANDOFF-CODEX-012` item 4（"每次拓扑/落位变化重建 plan"）
> 信任边界：全部产物仍为 `synthetic=true`、`verification_level=L2`、
> `geometry_usability=simplified-pbr-not-render-parity`、`trust_effect=none`。
> **此回执只交付 plan 内容 SHA 与机器报告，不提升为 acceptance。**

## 1. 边界声明

- **只重算 plan**：调用 `build_production_camera_plan()` 在内存中构建，不落盘、
  不运行 Blender、不修改 wrapper/caller、不触碰 Studio/Viewer。
- **不提升 acceptance**：fresh plan SHA 仅证明拓扑/落位变化已传播到 production
  plan 的内容寻址。它不证明几何质量、渲染质量、SfM/3DGS coverage 或 360° 可达性。
- **未跟踪文件**：`web/data/` 未被触碰。
- **不触碰 Codex 所有权**：reciprocal render caller、Blender wrapper、Studio/Viewer
  均未修改。registry、exact-218 build 和 fresh Phase 4.3 probe 由 Codex 负责。

## 2. Git HEAD

```
commit b0a414e6645a1378b2421ef5054d81e45d98427d
Author: taomic
Date:   Wed Jul 22 14:05:27 2026 +0800
    docs(materials): audit Batch15 quality variants
```

GLM-P0 Step 1–3 的 commits（`801eed1`、`f6de2e9`、`f58e060`）已全部在 `main`
上且已推送。`b0a414e` 是 Codex lane 在其之上的 docs commit，不影响 plan 内容。

## 3. 生成命令

```python
# 最小复现命令（项目根目录，.venv\Scripts\python.exe）
import hashlib
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)

plan = build_production_camera_plan()
raw = canonical_production_plan_bytes(plan)
sha = hashlib.sha256(raw).hexdigest()
```

`canonical_production_plan_bytes` 定义于
[pipeline/synthetic_village/production_profile.py:1132](file:///d:/vibecoding/nantai/pipeline/synthetic_village/production_profile.py#L1132-L1137)，
使用 `plan.model_dump(mode="json")` + `json.dumps(ensure_ascii=False, indent=2,
sort_keys=True) + "\n"`。

## 4. 输入 SHA

production plan 绑定的内容寻址输入（均在 plan JSON 中有对应字段）：

| 输入 | SHA-256 | 字节数 |
|---|---|---|
| `scene_plan_sha256` | `1a05b678a61ca15228ac3be219864699d0ad333e9a2210cb16277147a32283d4` | 109577 |
| `elevated_topology_sha256` | `bdd7b914c65fb65c45b5302f05720705fe310126e4ea5e9605a943419301807e` | 16384 |
| `environment_module_plan_sha256` | `7a9b1e8a4256402165eaf9ad6f662fda977ffb87fe2f956944b97752789af147` | 16660 |
| `reciprocal_route_module_plan_sha256` | `23d33ae284905424c130d82da707b8f1001922508ac72617f2d24a6d0bb2cec3` | 35674 |

注：`environment_module_plan_sha256` 不直接出现在 production plan JSON 中，
但它通过 `reciprocal_route_module_plan` 间接影响 production plan（reciprocal plan
的构建依赖 env module plan）。

## 5. Fresh production plan SHA + 字节数

| identity | value |
|---|---|
| `plan_sha256` | `1d42349bf9c6cb7658e4418593e38d9d200ade61f4e4ba05f4ae2f3bd491907c` |
| `plan_bytes_len` | `205725` |
| `registry_digest` | `4100117e7a6e7fb0d5ed356eaea6864569aafeff26020a50721b552ff0d2fa09` |

plan JSON 中的关键声明字段：

| 字段 | 值 |
|---|---|
| `plan.schema_version` | `1` |
| `plan.plan_schema` | `nantai.synthetic-village.production-camera-plan.v1` |
| `plan.profile_id` | `synthetic-village-coverage-180-v1` |
| `plan.journal_schema` | `nantai.synthetic-village.production-render-journal.v1` |
| `plan.camera_count` | `180` |
| `plan.complete` | `true` |
| `plan.synthetic` | `true` |
| `plan.geometry_trust` | `simplified-pbr-not-render-parity` |
| `plan.verification_level` | `L2` |
| `plan.route_loops` count | `4` |
| `plan.route_loops` ids | `central-loop`, `upper-loop`, `bridge-loop`, `valley-loop` |
| `plan.undelivered_requirements` count | `2` |

## 6. 相机数量 + ID 顺序

| group_id | count | first | last |
|---|---:|---|---|
| `ground-route` | 72 | `camera-ground-route-001` | `camera-ground-route-072` |
| `elevated-pedestrian` | 48 | `camera-elevated-pedestrian-001` | `camera-elevated-pedestrian-048` |
| `perimeter-inward` | 32 | `camera-perimeter-inward-001` | `camera-perimeter-inward-032` |
| `environment-corridor` | 16 | `camera-environment-corridor-001` | `camera-environment-corridor-016` |
| `audit-overview` | 12 | `camera-audit-overview-001` | `camera-audit-overview-012` |
| **TOTAL** | **180** | | |

`sequence_index` dense `1..180`：**通过**（`actual == list(range(1, 181))`）。

## 7. 连续两次字节一致证明

在同一进程内连续调用 `build_production_camera_plan()` 两次，分别计算
`canonical_production_plan_bytes` 和 SHA-256：

| build | `plan_sha256` | `plan_bytes_len` | `registry_digest` |
|---|---|---:|---|
| #1 | `1d42349bf9c6cb7658e4418593e38d9d200ade61f4e4ba05f4ae2f3bd491907c` | 205725 | `4100117e7a6e7fb0d5ed356eaea6864569aafeff26020a50721b552ff0d2fa09` |
| #2 | `1d42349bf9c6cb7658e4418593e38d9d200ade61f4e4ba05f4ae2f3bd491907c` | 205725 | `4100117e7a6e7fb0d5ed356eaea6864569aafeff26020a50721b552ff0d2fa09` |

```
bytes_a == bytes_b   = True
sha_a   == sha_b     = True
registry_a == registry_b = True
```

确定性成立。

## 8. 旧→新 SHA 变化原因

Codex 回执 `FEEDBACK-HANDOFF-CODEX-012` 记录的旧 reciprocal plan SHA 为
`916a66ce0a952bb4f3c3c55c9e4b998630bb2c1d65a7d68c058e6df76597df1b`。
当前 reciprocal plan SHA 为 `23d33ae284905424c130d82da707b8f1001922508ac72617f2d24a6d0bb2cec3`，
已变化。production plan SHA 随之必然变化，原因如下：

| commit | 变更 | 对 production plan 的影响 |
|---|---|---|
| `801eed1` (GLM-P0 Step 1) | 移除 4 个孤立 anchor ground nodes；covered-gallery 模块 base_x `-170 → -175` | topology 节点/edge 集合变化 → `elevated_topology_sha256` 变化；模块位置变化 → `reciprocal_route_module_plan_sha256` 变化；ground-route 相机位置可能变化 |
| `f6de2e9` (GLM-P0 Step 2) | 新增 bridge-loop（3 ground+elevated nodes + 3 edges）；bridge 模块 base_x `-150 → -155` | `elevated_topology_sha256` 变化；`reciprocal_route_module_plan_sha256` 变化；`route_loops` 从 2 扩展为 3；elevated-pedestrian 相机从 6 条 edge 增至 9 条 edge |
| `f58e060` (GLM-P0 Step 3) | 新增 valley-loop（3 ground+elevated nodes + 3 edges） | `elevated_topology_sha256` 变化；`reciprocal_route_module_plan_sha256` 变化；`route_loops` 从 3 扩展为 4；elevated-pedestrian 相机从 9 条 edge 增至 12 条 edge |

production plan 绑定 `elevated_topology_sha256` 作为内容寻址字段，且其
`route_loops` evidence 和 `elevated-pedestrian` 相机均直接消费 topology edges。
topology 从 `2 loops / 8 nodes / 6 edges` 变为 `4 loops / 14 nodes / 12 edges`，
reciprocal plan 从 `916a66ce...` 变为 `23d33ae2...`，两重变化均必然改变
production plan 的 canonical bytes。

未执行 checkout 到旧 commit 计算 production plan 旧 SHA（避免脱离 `main`）；
变化原因已由输入 SHA 变化和 plan 内容变化充分论证。

## 9. undelivered_requirements（机器可读，不提升）

production plan 声明 2 条未交付需求：

1. **`req-3-front-back-facade-coverage`**：`status=not-implemented`。req 3 要求
   每个建筑/桥/院落/环境组件有正反立面覆盖；当前无任何前后判定实现，object_registry
   无 per-component orientation。180-camera profile 不渲染任何帧，故 canary 的
   `observed_normal_angular_spread_deg` 证据在此也不存在。
2. **`req-5-pose-quality-fail-closed`**：`status=not-implemented`。req 5 整体未实现。
   macOS runner 有 render failure/timeout 记录和 operator-selected valid-pixel
   门，但此 pre-render plan 不携带已完成的 180-frame journal，不能声称帧通过。
   缺少：near-duplicate pose threshold、isolated camera detection、sky/ground
   semantic bad-frame detection。`_validate_plan` 仅拒绝精确重复中心。

`req-5-pose-quality-fail-closed` 只能在正式 180-camera 全量验收后更新。

## 10. Codex 接管项

以下由 Codex 负责，GLM 不触碰：

- registry 重建（`production_camera_registry_digest` 已提供，但 registry 落盘
  属 Codex）。
- exact-218 build / report / blend SHA。
- fresh Phase 4.3 probe（含 Blender wrapper 调用）。
- reciprocal render caller、Studio/Viewer 路径。

## 11. 6 个 role 候选距离门状态（供 Codex 参考）

| role | nearest connected node | distance | 30m gate |
|---|---|---:|---|
| central-courtyard-downhill | central-ground-east | 14.836m | PASS |
| forest-orchard-boundary | upper-ground-west | 28.112m | PASS |
| covered-gallery-underpass | central-ground-east | 28.402m | PASS |
| bridge-deck-crossing | bridge-ground-east | 26.793m | PASS |
| watermill-tailrace | bridge-ground-east | 14.001m | PASS |
| lower-valley-uphill | valley-ground-north | 9.572m | PASS |

所有 6 个 role 候选均通过 30m 距离门。但这**不等于**六层帧或 post-render 质量通过；
正式 probe 需由 Codex 在 fresh exact-218 build 上执行。

---

Co-Authored-By: GLM-5.2 <noreply@z.ai.com>
