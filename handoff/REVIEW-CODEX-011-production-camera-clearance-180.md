# REVIEW-CODEX-011 — 180 相机净空全量审计

> 日期：2026-07-20
> 角色：Codex（UX / visual-quality / audit lane）
> 结论：当前 180-camera production plan 至少包含两台可被几何探针明确拒绝的坏机位，
> 且仅靠近表面距离门无法覆盖所有视觉坏帧。

## 审计边界

本审计把 canonical 180-camera plan 瞬态注入当前 Windows v2 Blender 场景，以
`scene.ray_cast` 对每台相机做 `5×5` 视野采样，共 `4,500` 条第一命中射线。

它是私有诊断证据，不是正式六层帧、coverage proof、训练质量通过或 geometry trust
提升。所有输出仍位于 gitignored `.nantai-studio/`。

| 输入/输出 | 机器身份 |
|---|---|
| v2 build report | `aaf3a6b9fb6f48b3336e55f44f203504d58782a95a2738d70ee773464471e065` |
| `.blend` | `fa8cc4aabfe5049f2025e9d2ab34739c0914d87aa78a8fbda21ad86299cbebac` |
| production plan | `d5db85507a1f7bc4731e03c93d7b1232ddab7272dd5a52fd4d8df7bf6252a9f9` |
| camera registry | `9c8ad9b2bf299d51385822a2b40f071781d0c07e42aae6e1216887adb2563726` |
| 180×25 ray output | `77f26573d9d1f7ea8a5cc2ec44a28d6f8f5b84d603745dee2cbcae6d88b5febf` |

私有原始证据：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
  production-camera-plan.json
  production-camera-clearance-5x5.json
  probe_all_production_camera_clearance.py
  render_production_camera_from_plan.py
```

坐标转换严格复用现有合同：

```text
c2w_blender = c2w_opencv @ diag(1,-1,-1,1)
```

“上/中部”指 `sample_y >= 0` 的三排共 15 条射线。下部两排不直接参与近表面拒绝候选，
因为人眼相机正常会看到脚下地面。

## 全量结果

180 台相机中：

- 上/中部存在 `<1m` 命中的只有 `camera-ground-route-010`；
- 上/中部至少 5 条 `<2m` 命中的只有 `010` 与 `039`；
- 其它 178 台没有相同的 `<2m` 五射线模式；
- 但未触发该模式不等于画面可用，`034` 的 RGB 对照证明单一距离门仍会漏检。

### `camera-ground-route-010`

- 上/中部 `15/15` 条射线均在 `<1m` 命中；
- 其中 `10/15` 在 `<0.5m` 命中；
- 中心射线为 `0.432877m`；
- 15 条全部命中：
  `bridge-lower-001 / stone-deck-parapets-piers`；
- 原 RGB 探针 SHA：
  `d240d8d6a5f15e57c6521778efccc96e8400fa5734c86504ee1557160a72d6b5`。

这台相机被下层桥的 deck/parapet/pier 几乎铺满视野，明确不适合作为训练帧。

### `camera-ground-route-039`

- 上/中部 `5/15` 条射线在 `<2m` 命中；
- 中心射线为 `1.259315m`；
- 近命中均来自：
  `bridge-upper-002 / stone-deck-parapets-piers`；
- RGB 探针：
  `rgb-camera-ground-route-039.png`，
  SHA `287317ec7fb3608b6eb9314c78848746b30ca9a15043429bd72c7bac99912e2e`。

RGB 显示相机贴在桥面和两侧栏墙之间，画面主体是近距离石面，仅留狭窄天空，同样应拒绝。

### `camera-ground-route-034` — 距离门漏检对照

- 上/中部 `<2m` 命中为 `0/15`；
- 上/中部最小命中距离 `3.841703m`；
- 中心射线命中距离 `106.065713m`；
- RGB 探针：
  `rgb-camera-ground-route-034.png`，
  SHA `4d7396224c4d0205a9a4fd4d723fbadca95e2af4c449edb295e60ffc1efa1ec1`。

它仍被近坡面、斜穿画面的木廊/屋面和稀疏场景严重破坏。这个对照证明：

> 近表面射线适合做必要的几何净空门，但不能代替正式 RGB/depth/semantic/instance
> 坏帧审计。

## 产品结论

1. 当前 production plan 的 `undelivered_requirements` 已诚实声明 req 5
   `pose-quality-fail-closed` 未实现；本次实测确认这不是文档上的理论缺口。
2. `010`、`039` 必须重排或在训练用途上 fail closed。
3. `034` 表明重排之后仍需跑正式六层质量门，不能只看 camera center、pose 名称、
   valid-pixel ratio 或一组距离阈值。
4. 阈值必须是显式、版本化、绑定具体 build/profile 的 operator policy；本报告只提供分布，
   不把 `2m`、`5/15` 擅自提升为通用事实。
5. 任意 pose 变化必须改变 camera registry digest，并隔离旧 journal/render ID；不得静默
   覆盖旧证据。

核心 planner/runner 与 registry/journal 合同的实现交给 Opus，见：

```text
handoff/HANDOFF-OPUS-006-production-camera-quality-gates.md
```
