# HANDOFF-CODEX-010 — Task 5 §3 caller 接入清单

> 交接：Codex（production caller / Blender wrapper / Studio）→ Opus（mesh / camera / topology）
> 日期：2026-07-21
> caller 基线提交：`083cd925f38675acfefcc5a63a38a982447ecc41`

## 一句话边界

Opus 可以立即恢复 mesh、topology、collision 和 standing-eye camera 工作；Codex 已提供 additive v5 exact-218 preflight/render 合同。Opus 不修改 caller、wrapper、journal 或 Studio 文件，Codex 不修改 Opus 的 plan/runtime mesh 构造器。

## Codex 已落地并推送

| 能力 | 路径 | 当前状态 |
|---|---|---|
| exact-218 host request / lineage / report contracts | `pipeline/synthetic_village/reciprocal_route_production.py` | unit + v4 邻接回归通过 |
| fresh Blender preflight wrapper | `scripts/blender/preflight_reciprocal_route_cameras.py` | fake-`bpy` boundary + v1 preflight 回归通过 |
| six-layer Blender render wrapper | `scripts/blender/render_reciprocal_route_production.py` | fake-`bpy` boundary + v4 renderer 回归通过 |
| host tests | `tests/test_synthetic_village_reciprocal_route_production.py` | exact 218 / lineage / deterministic bytes |
| Blender wrapper tests | `tests/test_synthetic_village_reciprocal_route_production_blender.py` | script SHA / registry / scene build+plan |

当前 wrapper 实测 SHA：

```text
preflight_reciprocal_route_cameras.py
d58fd0cc23e7024cb079459235c14b6cb2c3f974ffc4c40af67480af3e3d824f

render_reciprocal_route_production.py
5d8aebdbb23306716bc13894f93423d1c747b8a1c07947355afec0f7a39aa565
```

这些 SHA 只绑定当前提交字节；任何 wrapper 修改都会产生新 request/render identity。

## Opus 可以立即做的工作

- 在 `reciprocal_route_module.py` 的 canonical plan 中完善路线附件、坐标、尺寸、朝向、净宽、坡度、廊下净空和连接角色。
- 在 `apply_reciprocal_route_modules.py` 中消费 plan 数据构造真实 mesh/collision probe；不得把 recipe 中的 `Literal[True]` 当成实测证明。
- 为六个 reciprocal role 产出 topology-bound standing-eye camera 候选；候选先走现有 repose/search 合同，不直接替换 canonical 180-camera plan。
- 用 `run_reciprocal_route_build` 产出 fresh exact-218 三文件目录，并向 Codex 回传下列机器身份：

```text
reciprocal build_id
reciprocal build request file SHA-256
reciprocal build report file SHA-256
reciprocal .blend SHA-256 and byte size
base_build_report_sha256
reciprocal_route_module_plan_sha256
object registry IDs and count
```

## Opus 必须保持的 scene 合同

最终 `.blend` 必须继续包含 canonical JSON scene property：

```json
{
  "build_id": "<reciprocal build_id>",
  "geometry_usability": "preview-only",
  "module_root_count": 43,
  "reciprocal_route_module_plan_sha256": "<measured plan SHA-256>",
  "stage": "modeled-unverified",
  "trust_effect": "none"
}
```

属性名必须是 `nv_reciprocal_route_module_build`，JSON 使用排序 key 和紧凑分隔符。除非有新的机器证据，不得提升 `geometry_usability`、`stage` 或 `trust_effect`。

全部 218 个 canonical root 与其 mesh 必须保留以下一致标签：

```text
nv_root / nv_stable_id / nv_root_id
nv_instance_id / nv_semantic_id / nv_material_id / nv_variant_id
pass_index == instance_id
```

instance ID 必须严格为 `1..218`，stable ID 唯一。wrapper 不接受 130、175、217、219 或重复 ID，也不会截断 176..218。

## Opus camera 输出清单

每个 reciprocal role 至少提供一个候选，且每个候选必须声明：

```text
camera_id
group_id = ground-route 或明确的现有 production group
topology_ref
arc_length_m
position_m / look_at_m / c2w_opencv
eye_height_m
候选所绑定的 plan SHA / camera registry SHA
```

候选必须满足已有 production camera schema；不要另建旁路 camera schema。Codex caller 会依次执行：

```text
candidate/reposed plan
  -> fresh exact-218 preflight
  -> preflight pass only
  -> six-layer real Blender render
  -> frame report/artifact SHA verification
  -> ProductionFrameQualityRequestV2/ReportV2
  -> accepted only after all checks pass
```

单点 cubemap、valid-pixel 比率或相机朝向看起来合理都不能代替上面链路。

## 不冲突路径

Opus lane 可修改：

```text
pipeline/synthetic_village/reciprocal_route_module.py
pipeline/synthetic_village/reciprocal_route_module_runtime.py
scripts/blender/apply_reciprocal_route_modules.py
对应 tests 与 Opus handoff/review
```

Codex lane 独占：

```text
pipeline/synthetic_village/reciprocal_route_production.py
scripts/blender/preflight_reciprocal_route_cameras.py
scripts/blender/render_reciprocal_route_production.py
tests/test_synthetic_village_reciprocal_route_production*.py
pipeline/synthetic_village/local_production_runner.py
pipeline/studio_jobs.py
web/studio/*
```

如果 Opus 发现 caller 合同缺字段，只在新 handoff 中列出证据和所需字段，不直接修改 Codex lane 文件。

## Codex 尚未宣称完成的部分

- one-camera host subprocess runner、原子 publication 和 v2 quality sidecars 仍在实现。
- wrapper 尚未在 Phase 4.1 fresh `.blend` 上完成真实 preflight + 六层 render。
- 没有 180-camera 全量 journal 证据。
- 没有 route topology、真实照片纹理、metric reconstruction 或任意坐标 360° 完整性的证明。

因此 Opus 可恢复 mesh/camera 工作，但 `req-5-pose-quality-fail-closed` 继续保持阻断，直到 Codex 回传真实 Blender 和 quality evidence。
