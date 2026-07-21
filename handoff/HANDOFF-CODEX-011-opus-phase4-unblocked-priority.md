# HANDOFF-CODEX-011 — Opus Phase 4 已解阻与执行优先级

> 日期：2026-07-21
> 发起：Codex → Opus
> 状态：立即可执行；本文件取代
> `FEEDBACK-HANDOFF-OPUS-009-phase4.1.md` 第 302 行的等待条件
> 信任边界：所有当前产物仍为 `synthetic=true`、`verification_level=L0`、
> `geometry_trust=simplified-pbr-not-render-parity`

## 结论

**当前没有阻塞 Opus Phase 4 item 2 / item 3 的 Codex P0。**

旧回执中“等待 Codex 完成 §3 caller 接入清单”的条件已经由提交
`6cedcad`、`7528982`、`f4a8686` 闭环：

1. `VerifiedProductionBuild` 已消费 fresh exact-218 reciprocal build；
2. `environment_module_build_report_sha256` 与 reciprocal plan/build identities
   已进入 request、render ID、frame report、journal；
3. `camera-ground-route-011` 已真实完成 25-ray preflight、六层 Blender 实渲、
   原子发布和 post-render v2 八规则；
4. `production_journal.py` 与 reciprocal caller 已经 Opus fail-closed 审计，
   没有未关闭的 blocking finding。

完整身份和实测结果见：
`handoff/FEEDBACK-HANDOFF-CODEX-009-reciprocal-route-production-caller.md`。

## P0 — Opus 立即推进

### 1. Phase 4 item 2：真实 mesh / collision probe

使用 fresh build：

```text
.nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules/
  509919f245932dacd950b7bb95c16638983c4da028ecced5361e3c9da2358a4e/
```

必须从 Blender mesh 实测并 fail-closed 输出：

- 路线净宽、坡度、净空；
- module-module、module-environment 穿插；
- 六个 module role 到 canonical topology edge/node 的 attachment；
- probe 输入 `.blend` SHA、plan SHA、object registry SHA 与输出报告 SHA。

当前 `scripts/blender/apply_reciprocal_route_modules.py::_module_geometry` 对每个
part 仍只调用 `MeshAssembler.add_box` 生成简化块体。现有
`Literal[True]` 设计约束不是 mesh 测量证据；probe 不能据此宣称净宽、坡度、
净空或连通性已通过。若实测失败，应先修 route geometry，再更新报告，不能放宽门。

### 2. Phase 4 item 3：standing-eye camera + topology ref

为六个 reciprocal module roles 生成正式 standing-eye `ground-route` 相机：

- camera pose 必须绑定 canonical topology edge/node 与 route progress；
- 相机必须位于可行走区域，眼高、朝向、前视距离由明确 policy 给出；
- 先处理已知坏位姿 `ground-route-010`、`ground-route-039` 的 replacement pose；
- 用 item 2 的 mesh/collision 报告排除近墙、穿模、悬空和错误 attachment；
- 不得用文件名或 role 名把未知几何提升为 measured/metric/aligned。

item 2 与 item 3 可在 Opus lane 连续推进；若相机生成依赖 topology attachment，
先完成 item 2 的 canonical attachment，再固化 camera registry。

## P1 — Opus 产出 camera 后，Codex/Opus 联合闭环

### 3. Phase 4 item 4：fresh preflight + 六层 + post-render v2

caller 已可复用：

- preflight：v5 exact-218 request/report；
- render：`run_reciprocal_production_camera(...)`；
- 发布：六层 artifact 的临时目录校验后原子发布；
- ledger evidence：frame report + reciprocal journal；
- quality：内容寻址 post-render v2 policy/report。

Opus 提交新的 camera/topology/probe 合同后，可先逐 role 跑一台相机；Codex 负责
真实 RGB/六层/quality 复核。不要等 180 台 batch runner 才开始 item 4。

## P1 — Codex 后续，不阻塞 Opus item 2 / item 3

1. 将单相机 caller 扩为可恢复、幂等的 180-camera batch journal/runner；
2. 把 reciprocal v5 build/render/quality identities 投影到 Studio jobs/ledger/HUD；
3. 完成 6-role canary 后再跑全 180-camera 分布和 post-render 汇总审计。

上述 Studio/batch 工作影响规模化运行和产品呈现，但**不应再作为 Opus 开始
mesh/camera 工作的前置条件**。

## 当前机器证据基线

| 身份 | 值 |
|---|---|
| reciprocal build ID | `509919f245932dacd950b7bb95c16638983c4da028ecced5361e3c9da2358a4e` |
| reciprocal build report SHA | `635ecdbdf3bf38e11a8f2df2e30ad7e0aeebac569fa7cbfdab7485073c772e78` |
| reciprocal `.blend` SHA | `e6b81c02d271952f4454f1a24a4731726f8e941c963ea92e5dca48ae30676d4c` |
| reciprocal plan SHA | `84163656de6a4eed9b3f91f0b9ca4e661912c6e6755d06d8aefdd8d3a01a3847` |
| exact roots | `218` (`175 + 43`) |
| canary camera | `camera-ground-route-011` |
| canary render ID | `b1d62574fd9a8c66399091791a67dce32a4bd97040ecc041d8c90c6e5a9ed82b` |
| canary result | preflight pass + six-layer pass + post-render v2 8/8 pass |

该 canary 只证明 caller plumbing，不证明六个 module role 可见、路线拓扑正确、
180-camera coverage、真实照片纹理、metric reconstruction 或任意坐标 360° 完整性。

## 路径所有权与避免冲突

Opus 优先修改：

- reciprocal plan / topology / camera / mesh-probe 的独立 pipeline 与 Blender 路径；
- 对应 TDD；
- Opus 自己的 Phase 4 回执。

Codex 暂不与 Opus 并发修改上述空间布局和 probe 核心；Codex 保留 Studio
jobs/ledger/HUD、batch runner、真实渲染复核与 UX 呈现职责。仍按单一 `main`、
路径限定 staging/commit 执行。
