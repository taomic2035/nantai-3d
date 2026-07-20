# REVIEW-CODEX-012 — Batch 6 私有 Blender 模块原型

> 日期：2026-07-20
> 角色：Codex（environment design / visual audit lane）
> 阶段：`modeled-unverified`
> 信任影响：`none`

## 结论

Batch 6 已生成的三张独立设计参考不再只停留在图片和文字规格：

- 已在当前 Windows v2 `.blend` 的私有副本中建立 3 个模块、47 个独立几何对象；
- 中央院落、桥拱/水车和建筑后场均能由 Blender RGB 实渲看到；
- 原型复用了当前 material bundle，但没有进入 object/material registry、production build、
  topology、collision、六层 journal 或 Release；
- 实渲同时证明当前 production 相机的“到锚点距离排序”不能作为模块可见性证据。

这表示素材已经开始转化为真实 Blender 几何，但离正式可训练、可漫游场景仍有明确阶段门。

## 输入与产物身份

设计输入仍是三张 `design-only` 独立参考：

| 输入 | SHA-256 |
|---|---|
| central courtyard | `19b40a84322ab7d343716bd684fc83a3207ae42ad94993d28446707f7a5537df` |
| bridge undercroft | `16b9f390f4550b2ec64bd98e4ccd799e05c4f44cd924a5da1503eec73ae8b4be` |
| rear service courtyard | `2c3900ab686cb45252538c8bdb6e507396ec9084ca7809a44fa3524810ab8b51` |

原型基于当前 v2 scene：

| 输入/产物 | SHA-256 |
|---|---|
| source `.blend` | `fa8cc4aabfe5049f2025e9d2ab34739c0914d87aa78a8fbda21ad86299cbebac` |
| private prototype `.blend` | `f6ac14fa1380905fc11bc50698d056fd3e13c4d6c01d6d3eaf4312f2fbb7bd5e` |

原型路径：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
  build_batch6_private_prototype.py
  batch6-prototype-v1.blend
```

文件大小 `149,453,895` bytes。它是可删除私有中间态，不进 Git 和 Release。

## 实测锚点

放置前从实际 `.blend` 读取了对象世界 AABB，并用向下 `scene.ray_cast` 实测地表：

- 中央院落表面：
  `x=-12..12, y=6..24, z=70.072..75.170m`；
- 下层桥体：
  `x=-182.784..-167.216, y=-124.626..-105.374, z=39.771..43.709m`；
- 后场服务院四角地表：
  `z=78.542..79.892m`；
- 所有绑定仍是建模目标选择，不是从设计图像素反求出的尺度或相机。

私有实测脚本：

```text
inspect_batch6_module_anchors.py
inspect_central_buildings.py
sample_batch6_surfaces.py
```

## 三个模块

### `prototype-central-courtyard-v1`

包括：

- 分段排水矮墙，保留东西方向入口；
- 南侧五级宽台阶；
- 明沟水面与两侧石质路缘；
- 四柱工作棚、双坡瓦顶和独立工作台。

它没有封堵院落中心和正式路线，但尚未做 walkable volume、碰撞、坡度、净宽或排水方向实测。

### `prototype-lower-bridge-undercroft-v1`

包括：

- 独立石拱环；
- 独立水车轮缘、8 根轮辐和轴；
- 木制引水槽；
- 非主路线维护平台。

石拱、水车、引水槽均为独立对象，符合后续 instance 分离方向。但当前 creek/terrain
没有真实河床切槽，水面仍像带状表面贴过坡体和拱口；这个问题不能靠增加贴图掩盖。

### `prototype-rear-service-courtyard-v1`

绑定到 `building-central-008` 后方实测地表，包括：

- 四角 terrain-conform 铺地；
- 两侧排水挡墙；
- 四柱服务棚和屋面；
- 三组柴堆、储物架和洗涤盆。

道具保持独立对象且不承担拓扑能力；当前仍是低细节块体，没有门窗/架空层、真实排水、
碰撞和 variant registry。

## RGB 实渲

### 非 registry 审计相机

这三台相机只用于看清模块，metadata 明确：

```text
audit_only=true
registry_status=not-registered
training_use=forbidden
trust_effect=none
```

| 视图 | SHA-256 | 结论 |
|---|---|---|
| central oblique | `8f0a75004fd9c40dc0805876efdf1c03a45cc7bc7846f7975ee15a74f6b9887c` | 院落入口、排水、棚架和工作台可读 |
| bridge undercroft | `449274affb672e56a186306a4eb1c5a30260aecfe197bf633e0f70d8106eac4b` | 石拱、水车和引水槽可读；河床错误明显 |
| rear service | `6d5eeabde141d1e3fe445e76d1454059dc691275ac7a6a9a25510577f0f7bed0` | 后场棚、挡墙和独立道具可读但过于块体 |

### 正式 production plan 候选

| camera | RGB SHA-256 | 实际结果 |
|---|---|---|
| `ground-route-025` | `e923cc1fc9692fd25c46a40ef27328cf553a2038907888c6ae039c1ee4d876c3` | 只看到中央工作棚边缘，不能验收完整模块 |
| `ground-route-011` | `45fa249e150fc5336b5a19872b80087316a3a164f188498bbc6a3cbe5a1ecae0` | 看不到桥侧拱底/水车主体 |
| `elevated-pedestrian-023` | `1fa748a8650c6a9afa50b709db56e31c4392aa8dc3972584e82c581e985fab44` | 被既有高架步道遮挡，看不到后场服务院 |

因此：

> “相机中心离锚点最近”只能生成验收候选，不能证明模块可见，更不能证明正反面 coverage。

后续 coverage 必须从正式 instance/semantic/depth 层实测，而不是继续调整距离排序。

## 进入生产场景前必须完成

1. 把原型几何转成 `build_synthetic_village.py` 中的确定性模块，不直接提交私有 `.blend`。
2. 给每个正式构件分配稳定 object/part/instance/semantic/material identity。
3. 对溪流和 terrain 做真实切槽/岸线支撑，解决水面穿坡和拱口问题。
4. 对中央院落及后场重新实测碰撞、净宽、净空、坡度和排水边界。
5. 用正式六层帧验证 module visibility；没有看到的相机不能进入该模块 coverage 计数。
6. production pose 变更继续遵守
   `HANDOFF-OPUS-006-production-camera-quality-gates.md` 的 registry/journal 隔离合同。
7. 通过阶段门前继续声明：
   `synthetic=true`、`geometry_usability=preview-only`、
   `simplified-pbr-not-render-parity`、`trust_effect=none`。

## 性能记录

三张正式机位首次并行渲染导致三个 Blender 进程各占约 `2.4–2.7GB`，总体约 3 分 39 秒
才完成；之后三张审计机位改为串行，每张约 5–6 秒。后续本机批量预览应限制并发，
避免用多进程争用纹理和 CPU。
