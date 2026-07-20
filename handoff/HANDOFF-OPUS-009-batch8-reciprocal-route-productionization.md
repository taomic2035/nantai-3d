# HANDOFF-OPUS-009 — Batch 8 互逆路线与边界模块生产化

> 发起：Codex（environment design / UX / visual audit lane）→ Opus
> （pipeline / Blender / registry / topology lane）
> 日期：2026-07-20
> 优先级：在 HANDOFF-006 Task 5 §3 的 130-root caller 闭环之后；与其路径不重叠时
> 可先做 plan/TDD，但不得把 175-root 场景提前提升为 production-ready

## 结论

Batch 8 的六张 image2 图片补齐了中央院落反向路线、桥面步行视角、水车尾水侧、
跨层廊下、林果边界回程和下谷上行回程的设计意图。它们足以指导下一版几何与路线
recipe，**不足以证明六个视角来自同一几何、360° 覆盖、相机标定或任意坐标可漫游**。

当前 175-root Blender 场景已实际包含 Batch 6 的 45 个模块对象，但 fresh RGB 审计
显示六个 Batch 8 角色仍未达到可走通、可读、可进入 production camera 计划的状态。
特别是：

- 林果边界设计探针距最近 standing-eye `ground-route` 相机 `286.70m`；
- 下谷回程探针距最近 standing-eye `ground-route` 相机 `50.30m`；
- 中央院落和跨层廊下仍呈大跨度细带/悬空条带，缺少可读的结构与路线连接；
- 水车只有孤立的大型轮体/支承件，尚无可通行的检修路径；
- 桥面虽存在，但与上下游路径连接在视觉上仍不连续。

因此下一步不是继续把图片标成“多视图”，而是把六个角色实现为版本化、可注册、
可做拓扑和六层验证的 additive scene module。

## 内容身份

### Batch 8 干净 Release

```text
release:
  https://github.com/taomic2035/nantai-3d/releases/tag/
    synthetic-village-design-inputs-batch8-2026-07-20
archive:
  synthetic-village-reciprocal-route-design-pack-batch8-2026-07-20.zip
archive_sha256:
  6bdafc92b9eb2df3a943c4e5df3466e9609c22db89844dc940db3dab6ca921eb
archive_bytes:
  2327861
release_manifest_sha256:
  be933fa37b56eee53e8acc78b7e2ff577c0bc4d6407fea91bfeb1da8d0637dbc
```

Release 内六张选中图片：

| role | image SHA-256 |
|---|---|
| `central-courtyard-downhill-reciprocal-route` | `05a49b4e085d555488e2ff1cc54ef7f643dc99fdbe184c3e09efe295af3c7408` |
| `standing-eye-bridge-deck-crossing` | `ba6f3838b5a07b1f18c07e67c61f1ef31ff5862cf79c4c0fa60a248c0105cada` |
| `watermill-tailrace-maintenance-three-quarter` | `77feef027408c2087dcb88f0d459eeab51e3a5f52b4af399eb9963ce3214a958` |
| `covered-gallery-cross-level-underpass` | `6d124e3269418558f3d5c187b9919d93d8e6e35e7b1ee71dc83591e5a0338b35` |
| `forest-orchard-boundary-return-route` | `339dbd218c09733d80460580d60b4e4bbd4854d3cde13aa5744a0f2a2aba466c` |
| `lower-valley-uphill-reciprocal-route` | `0641e54144a11d52411e08905a556c698f8e8d19fb78ff2c01cb4c5104ab76a7` |

所有图片必须保持：

```text
synthetic=true
replaceable=true
camera_calibration=unknown
geometry_consistency=not-verified
training_use=forbidden-as-multiview
coverage_use=forbidden
panorama_use=forbidden
trust_effect=none
```

### Batch 9 互补侧向输入

Batch 9 在 Batch 8 的正/反向角色之外补充 90°/120° 侧向与下层反向结构：

```text
release:
  https://github.com/taomic2035/nantai-3d/releases/tag/
    synthetic-village-design-inputs-batch9-2026-07-20
archive:
  synthetic-village-lateral-route-design-pack-batch9-2026-07-20.zip
archive_sha256:
  6f7cc48e40e3d323a98e5ca91633cb6a6a7f623d7544efe44317102b3e5648f8
archive_bytes:
  19344169
release_manifest_sha256:
  bf5e2a5c6907baf5acefa5c6cf7d85bf9cfe611b47013f5bb1b564eca3064339
```

| role | image SHA-256 |
|---|---|
| `central-courtyard-cross-slope-lateral-route` | `cd11d944f457c5dfb3415657eb85e38c0033c7c6e0d284771ede9f51d5d11cd8` |
| `bridge-downstream-bank-three-quarter-route` | `f0e9c029b06dfa9832d44ca0ff4fbde186d84e1ef6a3adfcc6a994a09d1e97be` |
| `watermill-opposite-bank-upstream-service-side` | `77137860a0b2f98d35747bde61a3852bcf10882343235a5c0faeb5d85f619f83` |
| `covered-gallery-lower-lane-reciprocal-underpass` | `a5f935bbdd2b6609aef40b92c0c8e57e746257274e2c21c81990835715df2ec0` |
| `forest-orchard-lateral-three-way-route-fork` | `afd44bbdb965be7a3f6a478cd9c2509aead86c1204389c992a5d7fbdcb9ed80e` |
| `lower-valley-field-edge-lateral-route-junction` | `788eb01187c13ca02807a20cee42720b1970100d9c714d1bd647c82dc353dd7b` |

这六张图与 Batch 8 共用同一 evidence policy。桥侧图出现一个小型次级泄水孔，
只能作为岸侧构图参考；canonical recipe 不得从该像素细节推导桥孔数量。

### 当前 175-root Blender 场景

```text
environment_module_build_id:
  9e4f5215e347e33624f938e1fb19dab31119f20bb82414f37d12bea8f3dfa325
environment_module_plan_sha256:
  d201c5985a00a07f402ff13c4e029c5600c8a2b2fd3bf47f1fc500299acc4629
blend_sha256:
  3f0b8ae0724a4dc587cddc024f289a388be2e0250e30e27c8e5d38be9ec4b8a9
canonical_roots:
  175
base_roots:
  130
module_roots:
  45
stage:
  modeled-unverified
geometry_usability:
  preview-only
verification_level:
  L0
trust_effect:
  none
```

旧 130-root production render/journal 和本 175-root report 都必须继续独立可验证。
不得通过修改旧 schema 或复用旧相机证据，把 175-root 场景静默提升。

## 六个角色的实施合同

下面列出的 part ID 是建议的稳定语义名。Opus 可按现有命名规范微调，但反馈必须提供
一一映射；新增 instance ID 必须由 canonical plan 顺序确定并锁定，不能在本交办中
手填一个未经 validator 推导的最终 root 数。

### 1. 中央院落下行互逆路线

当前可复用：

```text
courtyard-paving-001
courtyard-gallery-deck-001
courtyard-gallery-roof-001
courtyard-stair-run-001
courtyard-ramp-run-001
courtyard-drainage-channel-001
courtyard-segment-wall-001..002
courtyard-curb-edge-001
```

V2 至少新增：

```text
courtyard-downhill-gate-001
courtyard-covered-side-passage-001
courtyard-cross-slope-alley-001
courtyard-route-attachment-upper-001
courtyard-route-attachment-lower-001
courtyard-gallery-post-run-001
courtyard-gallery-guard-001
```

路线合同：

- 从中央院落必须能沿连续 walkable surface 下行离开；
- 楼梯与坡道是两个明确分支，不能共享一块不可判定的粗糙面；
- covered side passage 与 cross-slope alley 必须各自接回已注册 topology；
- 排水槽不得横断可走面而没有盖板、桥板或明确跨越节点。

### 2. 桥面站立视角与两端连接

当前可复用：

```text
bridge-arch-001
bridge-abutment-001..002
bridge-deck-slabs-001
bridge-parapet-001..002
creek-bed-cut-001
creek-bank-stone-001
creek-water-surface-001
```

V2 至少新增：

```text
bridge-route-attachment-upstream-001
bridge-route-attachment-downstream-001
bridge-access-ramp-001
bridge-side-maintenance-path-001
bridge-drainage-scuppers-001
bridge-deck-edge-transition-001
```

路线合同：

- 桥面两端与既有 path polyline 的高度、宽度和法线连续；
- 至少存在一条不依赖台阶的替代坡道；
- parapet、scupper 和维护侧道具有独立 instance/semantic 身份；
- 不能用 RGB“看起来接上了”代替 collision 与 walkable topology 证据。

### 3. 水车尾水侧检修路线

当前可复用：

```text
waterwheel-wheel-001
waterwheel-axle-001
waterwheel-bracket-001
waterwheel-millrace-001
waterwheel-spill-001
waterwheel-tailwater-001
```

V2 至少新增：

```text
watermill-building-shell-001
watermill-maintenance-platform-001
watermill-service-stair-001
watermill-access-panel-001
watermill-creek-bank-path-001
watermill-platform-guard-001
watermill-tailrace-retaining-wall-001
```

路线合同：

- 检修平台必须与 creek-bank path 连通，并能到达 wheel/axle access panel；
- 水车与建筑、引水槽、尾水槽的空间关系必须由 recipe 明示；
- wheel clearance、平台净宽、护栏和台阶坡度必须进入几何 validator；
- 动画能力不是本阶段通过条件，但 wheel/axle 不得与平台发生静态穿插。

### 4. 跨层有顶廊下

当前可复用：

```text
covered-timber-gallery-v1
cross-level-covered-passage-v1
courtyard-gallery-deck-001
courtyard-gallery-roof-001
```

V2 至少新增：

```text
gallery-underpass-lower-lane-001
gallery-post-run-001
gallery-beam-run-001
gallery-foundation-run-001
gallery-guard-run-001
gallery-side-door-001
gallery-branch-attachment-upper-001
gallery-branch-attachment-lower-001
gallery-branch-attachment-side-001
```

路线合同：

- 上层 gallery 和下层 lane 必须是两个独立 walkable topology edge；
- 下层净空、净宽和结构柱碰撞必须通过实际 Blender probe；
- 三个出口均绑定明确 topology node，不能只在画面中形成视觉分叉；
- 当前细长悬空 ribbon 不是合格结构实现。

### 5. 林果边界回程

当前只有可作为背景的：

```text
orchard-slope-001..002
bamboo-grove-001..004
field-terrace-001..012
```

V2 至少新增：

```text
forest-boundary-path-fork-001
forest-orchard-transition-001
forest-retaining-drain-001
forest-trail-shelter-001
forest-route-attachment-inbound-001
forest-route-attachment-outbound-001
forest-edge-vegetation-band-001
```

路线合同：

- 新 ground-route 必须经过林果边界，而不是依赖 `perimeter-inward` 高位相机；
- path fork 两个分支都必须在 baked topology 内闭合或明确连接到下一个路段；
- retaining drain 不得和可走面冲突；
- 植被带是可替换实例，不得参与 geometry/trust 提升。

### 6. 下谷上行互逆路线

当前最近的既有 `ground-route` 相机仍距设计探针 `50.30m`，不足以证明入口与回程。

V2 至少新增：

```text
lower-valley-entry-path-001
lower-valley-field-edge-path-001
lower-valley-creek-maintenance-trail-001
lower-valley-drainage-outlet-001
lower-valley-building-back-entry-001
lower-valley-route-reconnection-001
lower-valley-retaining-step-001
```

路线合同：

- 下谷入口、田边路、溪岸检修道和上行回程必须形成可追踪的 topology sequence；
- 至少一个 route edge 接到桥/水车片区，一个接回村庄主体；
- 建筑后门、排水出口和挡土高差必须在 standing-eye 相机中可读；
- 不得把程序化无限网格的 `on_demand` 能力误当作本段真实几何已存在。

## 推荐版本边界

采用 additive V2，而不是改写已有证据：

```text
ScenePlan v1
  + ElevatedTopologyPlan
  + EnvironmentModulePlan v1          # 45 parts / roots 131..175，保持不变
  + ReciprocalRouteModulePlan v1       # 本交办六角色，内容寻址
      -> EnvironmentModuleBuildReport v2 adapter
      -> expanded object/material/semantic registry
      -> fresh Blender scene
      -> fresh topology/collision/camera/render evidence
```

`ReciprocalRouteModulePlan` 至少绑定：

- ScenePlan SHA；
- ElevatedTopologyPlan SHA；
- EnvironmentModulePlan v1 SHA；
- Batch 8 与 Batch 9 Release manifest SHA；
- 两批共十二张 selected image SHA；
- recipe version；
- part 顺序、material alias、semantic ID、topology attachments；
- `synthetic=true`、`trust_effect=none`。

如果 Opus 选择把它作为 `EnvironmentModulePlanV2`，也必须保证 v1 canonical bytes、
v1 report 和旧 175-root `.blend` 继续逐字节可验证。

## 生产通过门

### Plan / runtime

1. canonical bytes 跨进程一致，任一 source/part/topology/material 改动都改变 plan SHA；
2. 所有新增 part 均有唯一 object ID、instance ID、semantic ID 和 material binding；
3. Blender runtime 从 verified 175-root base 追加构建，使用 staging + 原子发布；
4. build report 绑定 base blend/report、plan、runtime script、Blender executable 和
   最终 `.blend` SHA；
5. 第二次相同输入命中内容寻址复验，不重跑 Blender。

### 可漫游与相机

1. 六个角色各自存在 standing-eye `ground-route` camera，不接受
   `perimeter-inward`、高空 overview 或手写审计相机替代；
2. 相机必须绑定 topology ref、arc length、完整 intrinsics 和 c2w；
3. 新 route edges 通过宽度、坡度、净空、collision 和连通性门；
4. fresh 175+ root preflight 必须通过，旧 130-root 或旧 175-root report 不继承；
5. 六角色各生成 RGB/depth/normal/instance/semantic/camera metadata 六层；
6. post-render v2 policy 从真实 layer bytes 复算，并绑定六份 artifact SHA；
7. RGB 人工复核只解释机器统计，不替代机器门禁。

### 明确不能宣称

即使本交办全部通过，也只能证明合成场景内六条路线在受测 topology 和相机位置可用。
它仍不证明：

- Batch 8 图片是相机标定的真实多视图；
- 场景来自真实照片/视频重建；
- 任意世界坐标已经有真实几何；
- 所有 360° 方向无空洞、无碰撞；
- 具有 metric 或 training-suitable 信任。

## 私有审计证据

以下文件均在 `.nantai-studio`，不提交仓库：

```text
synthetic-village/hybrid-v4/work/audit/batch8-scene-inventory.json
  sha256=c98eca8c58d8f04ada5f00c6bd32315a092e70612e21373d4ec8a2e097a22549

synthetic-village/hybrid-v4/work/audit/batch8-design-role-rgb-v1/manifest.json
  sha256=477c81ae7301d1287099fd8bf8b161d400e72345be0a8993ff420caf05cbe8a5

synthetic-village/hybrid-v3/work/audit/production-camera-plan.json
  sha256=d5db85507a1f7bc4731e03c93d7b1232ddab7272dd5a52fd4d8df7bf6252a9f9
```

RGB 审计帧 SHA：

| role | SHA-256 |
|---|---|
| central courtyard | `90aba0f6feebeb4612af5fdabe6febeb63af6d951dd79a710bf5cd4db7f612cf` |
| bridge deck | `746375622eca4fd217ec3a6cb08261b298f42d5bd447fc76659ef5a354007b35` |
| watermill | `ba2e48a68bc44783dfedb1dc1896a56798ba4c5281f57145909fd1ae55369e3a` |
| covered gallery | `88c882e72adbb3a5ab066007b9f1c5389fdcf6ae088239d81f7e56c21668e36e` |
| forest/orchard | `6993e081d926943ea5e99ae7bdc06ffeba01045bcb335266655d2b436e81878f` |
| lower valley | `79e518ed25b4e2ea53050f86787da9ca1f081c8146ba1664f187431a8773f856` |

这些 pose 均为 `audit-only-not-registered`，其
`training_use=forbidden`、`coverage_use=forbidden`、`trust_effect=none`。

最近既有 standing-eye `ground-route` 相机：

| design probe | nearest production camera | distance | topology |
|---|---|---:|---|
| central courtyard | `camera-ground-route-026` | `9.43m` | `path-network-003` |
| bridge deck | `camera-ground-route-010` | `8.89m` | `path-network-001` |
| forest/orchard | `camera-ground-route-014` | `286.70m` | `path-network-002` |
| lower valley | `camera-ground-route-004` | `50.30m` | `path-network-001` |

距离只用于定位明显 coverage gap，不是“某个距离以下即覆盖”的通用阈值。

## Caller 与协作边界

Opus lane 负责：

- versioned plan/schema/validator；
- Blender build/runtime；
- registry、collision、walkable topology；
- production camera plan 与六层/post-render evidence runner。

Codex lane 负责：

- 对真实 RGB 与 per-rule 统计做 UX/视觉审计；
- Studio jobs/ledger/HUD 的薄适配与呈现；
- 不在 Studio 中复制 Blender 编排或质量裁决。

核心 runner 必须可 standalone/CLI 执行并生成 immutable evidence bundle；Studio 只
启动它、展示阶段/拒绝原因并导入绑定结果。

## 回执要求

请在回执中列出：

1. 最终采用的 plan 名称、schema version、plan SHA 与 recipe version；
2. 六个角色到 canonical part ID、instance ID、semantic/material 的映射；
3. 旧 v1 证据保持不变的测试；
4. 新 `.blend`、build request/report、registry 与 Blender executable SHA；
5. topology/collision/preflight 的实际报告；
6. 六角色逐帧 request/report、六层 artifact 与 post-render decision SHA；
7. 失败项及其 fail-closed 状态，不能只给成功截图。
