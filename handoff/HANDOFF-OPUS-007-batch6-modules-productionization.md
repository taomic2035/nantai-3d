# HANDOFF-OPUS-007 — Batch 6 Blender 模块生产化

> 发起：Codex（environment design / visual audit lane）→ Opus（architecture /
> Blender build / registry / topology lane）
> 日期：2026-07-20
> 优先级：HANDOFF-006 之后；只有在路径不重叠时才可并行

## 目标

把 Codex 已验证可读的三个私有 `modeled-unverified` Blender 原型转换为确定性、
内容寻址、registry 完整、可进入正式六层渲染的 production 模块：

1. 中央工作院落；
2. 下层桥拱 / 水车 / 引水槽；
3. `building-central-008` 后场服务院。

不要提交私有 `.blend`，也不要直接复制图像像素、未验证坐标或原型中的粗糙块体。
请把设计意图重新实现为仓库内的 canonical plan/recipe，再由 Blender build 生成。

## 输入

设计与审计规格：

```text
handoff/HANDOFF-CODEX-008-batch6-to-blender-modular-consumption.md
handoff/REVIEW-CODEX-012-batch6-private-blender-prototype.md
```

私有可复跑原型：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
  build_batch6_private_prototype.py
  batch6-prototype-v1.blend
  rgb-prototype-audit-central-oblique-01.png
  rgb-prototype-audit-bridge-undercroft-01.png
  rgb-prototype-audit-rear-service-01.png
```

原型 `.blend` SHA：

```text
f6ac14fa1380905fc11bc50698d056fd3e13c4d6c01d6d3eaf4312f2fbb7bd5e
```

三个 image2 输入始终是 `design-only`：

```text
camera_calibration=unknown
geometry_consistency=not-verified
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

## 推荐架构

优先考虑沿用 `ElevatedTopologyPlan` 的 additive 模式：

```text
ScenePlan v1 (保持不变)
  + ElevatedTopologyPlan
  + content-addressed EnvironmentModulePlan
      -> Blender build request
      -> object/material/semantic registry
      -> deterministic .blend/.glb/previews
```

原因：

- 不重写已锁定的 ScenePlan v1 digest；
- 可明确绑定设计源 SHA、ScenePlan SHA、ElevatedTopology SHA 和 recipe version；
- 可让三个模块独立替换，而不是把它们写死成一张不可维护的大网格；
- 与现有 fail-closed build request、object registry 和 determinism contract 同构。

这是建议，不是越权拍板。若你选择其它结构，请在反馈中说明它如何保持相同的 identity、
determinism、registry 和 topology 边界。

## 模块合同

### 1. 中央工作院落

绑定：

```text
courtyard-public-002
central-ground-west
central-ground-east
edge-central-stair-001
edge-central-gallery-001
edge-central-ramp-001
```

正式模块至少包含：

- 湿石铺地变化与明确排水边界；
- 不封堵东西入口的分段挡墙；
- 连续可碰撞宽台阶；
- 明沟、路缘、盖板或跨沟节点；
- 独立工作棚、工作台和可替换道具；
- 既有 planter/tree 不得与新棚架、屋檐或路线碰撞。

验收必须实测：

- gallery 净宽 `>=2.6m`、净空 `>=2.4m`；
- stair 净宽 `>=2.4m` 且踏面连续；
- ramp 净宽 `>=3.0m` 且碰撞连续；
- west/east 入口仍分别连到 `path-network-002/003`；
- 排水和道具不侵入 walkable volume。

### 2. 下层桥拱 / 水车

绑定：

```text
bridge-lower-001
creek-main-001
path-network-001
path-network-005
```

当前真实缺口不是“少一张贴图”，而是：

- bridge 只有 deck/parapets/piers 的块体读感；
- creek ribbon 与 terrain 没有形成可信河床切槽；
- 水面会像带状表面穿过坡体和拱口；
- 没有正式水车、轴、支架、引水槽、回水与岸线维护空间。

正式实现必须：

- 生成有厚度、内外表面和桥台支撑的真实石拱；
- 让 creek floor、bank、water surface 与 terrain 形成无穿插的确定性截面；
- 水车轮、轴、支架、millrace、落水和回水保持独立 object/part identity；
- 水车与桥台/建筑不合并为一个 instance；
- 维护平台默认不是主路线，不得提升 route-loop evidence；
- 桥面原有主路线和 `path-network-001/005` 连通性不倒退。

### 3. 后场服务院

绑定：

```text
building-central-008
```

正式模块至少包含：

- terrain-conform 服务院铺地与建筑基础接触；
- 后墙/侧墙、门窗、檐底、雨槽和排水出口；
- 可解释的架空层/检修入口；
- 独立服务棚、储物架、柴堆、洗涤盆和至少三种可替换 variant；
- 道具不得承担 topology，也不得堵住门、巷或维护路径。

原型只验证了位置和可读性，不能作为门窗方向、尺寸或建筑“真实背面”的证据。

## Registry 与 provenance

- 每个 production module、part 和可替换 prop 都需要稳定 object/part/instance/semantic/
  material identity。
- 不从源图文件名、prompt 或模块名称推断可信度、朝向或 coverage。
- 若分配新 instance ID，应从当前 registry 后确定性追加并锁测试，避免与 elevated
  instances `127–130` 冲突。
- build report 必须记录 module plan SHA、recipe version、三张 design source SHA 和
  `trust_effect=none`。
- 模块加入后 object registry digest、build ID、`.blend/.glb` SHA 和 downstream render
  identity 必须变化；旧 journal 不可复用。
- 继续声明：
  `synthetic=true`、`geometry_usability=preview-only`、
  `simplified-pbr-not-render-parity`。

## 相机与六层验收

Codex 实渲已证明以下“最近相机”并不能看到完整模块：

- `ground-route-025` 只能看到中央棚架边缘；
- `ground-route-011` 看不到桥侧水车/拱底主体；
- `elevated-pedestrian-023` 被高架步道挡住，看不到后场服务院。

因此：

- 不得从相机到锚点距离宣称 coverage；
- 必须从实际 instance/semantic/depth 层统计模块可见像素和反向观察；
- 新 pose 或重排必须遵守
  `HANDOFF-OPUS-006-production-camera-quality-gates.md` 的 registry/render/journal
  隔离合同；
- production frames 通过前，三台自定义审计相机保持 `not-registered`、
  `training_use=forbidden`。

## TDD 与验收

请先写失败测试，至少覆盖：

1. module plan canonical bytes 跨进程一致，绑定 exact ScenePlan/ElevatedTopology/source SHA；
2. tampered source/module/build identity fail closed；
3. 三模块及所有正式 part 的 stable ID、instance、semantic、material registry 完整且唯一；
4. creek floor/bank/water/bridge arch 的几何截面无水面穿 terrain/拱壁；
5. 中央闭环宽度、净空、碰撞与入口连接不倒退；
6. 服务院道具不侵入门口、巷道或 walkable volume；
7. Blender build request 和 runtime 拒绝缺失、重复或顺序漂移的 module registry；
8. 同一请求重建 `.blend/.glb/previews` 的身份满足现有确定性合同；
9. 六层帧中桥、水车、院落、服务棚和道具拥有可区分的 instance/semantic evidence；
10. coverage 仅从实际帧统计，未渲染时保持 unknown/fail closed。

建议相关门禁：

```powershell
python -m pytest `
  tests/test_synthetic_village_scene_plan.py `
  tests/test_synthetic_village_elevated_topology.py `
  tests/test_synthetic_village_blender_runtime.py `
  tests/test_synthetic_village_canary.py `
  tests/test_coverage_audit.py -q
```

再跑你实际修改路径对应的完整 synthetic-village 测试集与 Blender runtime 探针。

## 路径与协作

预计核心路径由你决定，可能包括：

```text
pipeline/synthetic_village/
scripts/blender/build_synthetic_village.py
tests/test_synthetic_village_*.py
```

不要修改 Codex lane：

```text
web/studio/
web/viewer/
pipeline/studio_server.py
```

HANDOFF-006 优先。若 006 正在修改相同 profile/render/registry 路径，请先完成并推送 006，
再开始 007，避免共享工作树冲突。

提交继续使用路径限定 stage，并保留：

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

完成后请回执：

```text
handoff/FEEDBACK-HANDOFF-OPUS-007.md
```

Codex 后续负责：

1. 新旧 RGB 对照和模块可读性审计；
2. Studio job/ledger/HUD 的阶段与拒绝原因呈现；
3. instance/semantic/depth 可见性证据复核；
4. 通过后再整理干净 Release 和 README 消费路径。
