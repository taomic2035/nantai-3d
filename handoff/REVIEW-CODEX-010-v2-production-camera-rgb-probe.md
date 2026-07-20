# REVIEW-CODEX-010 — v2 场景的 production camera RGB 探针

> 审计：Codex
> 日期：2026-07-20
> 对象：当前 Windows textured v2 `.blend` + 正式 180-camera 计划中的四个机位
> 性质：私有、瞬态 RGB 探针；不是正式六层 production frame

## 结论

把正式 `ProductionCameraPlan` 的四个 ground-route 机位瞬态注入当前 v2 `.blend` 后：

- `camera-ground-route-011`、`025`、`026` 能看到人眼尺度的路线和村庄空间；
- `camera-ground-route-010` 几乎被近表面/上方几何完全遮挡；
- 三张可读帧仍暴露建筑悬空、支撑缺失、路线边界稀疏、建筑背面单薄和低细节植被；
- Batch 6 的中央院落、桥底/水车和后场模块确实对应当前画面的真实缺口。

因此 production camera 计划不是全盘无效；它已经能产生方向正确的人眼视图。当前主差距
收敛为：

1. Windows v2 production runner 尚未接通；
2. 个别相机缺少几何碰撞/净空拒绝；
3. 当前 v2 场景的人眼尺度几何与材质仍不足；
4. valid-pixel 无法拒绝“全是近表面”的坏帧。

## 身份链

### v2 build

```text
build id:
4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc

build report sha256:
aaf3a6b9fb6f48b3336e55f44f203504d58782a95a2738d70ee773464471e065

blend sha256:
fa8cc4aabfe5049f2025e9d2ab34739c0914d87aa78a8fbda21ad86299cbebac
```

### production plan

```text
profile:
synthetic-village-coverage-180-v1

plan sha256:
d5db85507a1f7bc4731e03c93d7b1232ddab7272dd5a52fd4d8df7bf6252a9f9

camera registry sha256:
9c8ad9b2bf299d51385822a2b40f071781d0c07e42aae6e1216887adb2563726

elevated topology sha256:
1eabf220e2d9e2a91c2371d3587d2f612b17d164765c0ed86059cc2ac8ddaf43
```

## 方法和边界

私有脚本：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
  render_production_camera_diagnostic.py
```

它使用 `c2w_blender = c2w_opencv @ diag(1,-1,-1,1)` 的现有单一转换规则，将四个
production camera 的当前稳定矩阵写入瞬态 Blender camera：

- `camera-ground-route-010`
- `camera-ground-route-011`
- `camera-ground-route-025`
- `camera-ground-route-026`

固定 `1024 × 576`、水平 FOV `65°`，只写 PNG。Blender 进程退出时不保存 `.blend`。

这些探针**不是**：

- `render-production-local` 的产物；
- 六层 frame contract；
- journal 中的 verified/rejected 记录；
- coverage、training suitability 或 geometry trust 证据。

它们只回答一个问题：当前生产相机矩阵放入当前 v2 场景后，RGB 大致看见什么。

## RGB 与相机证据

OpenCV camera 的本地 `+Z` 是前向；下表 pitch 由 `c2w_opencv` 的世界 forward 计算。

| 相机 | 位置 | look-at | pitch | RGB 字节 | RGB SHA-256 |
|---|---|---|---:|---:|---|
| `camera-ground-route-010` | `(-170.918,-124.068,39.720)` | `(-181.884,-103.899,45.067)` | `13.111°` | `688,006` | `d240d8d6a5f15e57c6521778efccc96e8400fa5734c86504ee1557160a72d6b5` |
| `camera-ground-route-011` | `(-179.260,-108.916,43.755)` | `(-182.856,-89.086,49.059)` | `14.745°` | `767,776` | `0f539b53679ad68f3aaa894accf4eea84cad3c648eac8a0b84f67bd175762042` |
| `camera-ground-route-025` | `(0.000,5.033,71.804)` | `(0.000,15.000,74.168)` | `13.343°` | `780,771` | `05e7ed21f4c5d9357d15d9e8897a0fbc6994c19f6a5d5bc7031eff83a897cfe3` |
| `camera-ground-route-026` | `(9.384,18.753,74.708)` | `(32.427,28.387,76.070)` | `3.121°` | `794,084` | `6151e0cb77c65c4540029bee737e11754853fe24076a5312c6e619d9aed9fce0` |

私有 RGB 位于：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
  rgb-camera-ground-route-010.png
  rgb-camera-ground-route-011.png
  rgb-camera-ground-route-025.png
  rgb-camera-ground-route-026.png
```

## 逐帧审计

### `camera-ground-route-010` — 桥区，严重失败

- 画面上部几乎全部是极近、失焦的石材表面；
- 中间出现横向近表面，剩余区域只有破碎地表；
- pitch 为向上 `13.111°`，所以失败不是“相机向下”；
- 当前证据符合相机位于/贴近地形、桥体或其它大块几何的表现；
- 没有可读桥、路线或村庄空间。

正式 runner 应保留该坏帧的六层证据并拒绝训练用途，不能移动相机后覆盖原问题。

### 九宫格射线复核

为区分“画面像贴墙”和“相机确实被桥体近距离遮挡”，又从四个相机的视野九宫格方向
调用 Blender `scene.ray_cast`，记录第一命中。

`camera-ground-route-010` 的结果是确定的：

| 视野区域 | 第一命中 | 距离 |
|---|---|---:|
| 左上、中上、右上 | `bridge-lower-001 / stone-deck-parapets-piers` | `0.483–0.574m` |
| 左中、中心、右中 | `bridge-lower-001 / stone-deck-parapets-piers` | `0.433–0.516m` |
| 左下 | `aux-terrain / terrain-4m-grid` | `8.440m` |
| 中下、右下 | `path-network-001 / terrain-conform-ribbon` | `4.643–5.259m` |

因此上部和中心共 `6/9` 探针被同一桥体部件在不足 `0.6m` 处截断。中心射线具体为：

```json
{
  "camera_id": "camera-ground-route-010",
  "stable_id": "bridge-lower-001",
  "part_id": "stone-deck-parapets-piers",
  "semantic_id": 4,
  "distance_m": 0.432877
}
```

其它三张样本没有相同模式：

- `011` 的上排三条和中左射线无命中；中心到建筑平台 `80.846m`；
- `025` 的上排三条无命中；中心到建筑墙面 `27.780m`；
- `026` 的上排三条和中右无命中；中心到上游桥体 `124.648m`。

它们的下排射线正常命中脚下路径/地形约 `2.787–10.897m`，符合人眼视野下部看见
地面的预期。这个对照使 `010` 的近桥遮挡成为可机器区分的异常，而非所有 ground-route
相机的共同性质。

### `camera-ground-route-011` — 桥区，可读但稀疏

- 能看到路线、坡面、房屋、挡墙和远处山体；
- 视野与人眼尺度一致，证明生产相机计划在桥区至少有可用方向；
- 大面积天空与空旷地表占据画面；
- 房屋像孤立块体，桥/水车/桥底节点不在有效视野中；
- 树木仍是低面数晶体状代理。

### `camera-ground-route-025` — 中央院落入口，方向可用

- 能看见道路前方和多栋建筑，构图是人眼路线视角；
- 建筑底部存在明显悬空/薄板感，入口和基础不可信；
- 道路中心被重复小型植被代理占用；
- 缺少围合院落、连续立面、门槛、排水和工作棚；
- Batch 6 中央院落与建筑后场模块可直接针对这些缺口。

### `camera-ground-route-026` — 中央外向路线，方向可用

- 路线有明显纵深，能看到跨高差通道和村庄层次；
- 左侧建筑明显悬空，缺少基础、柱和地形接触；
- 右侧长通道/廊桥支撑稀疏；
- 建筑密度不足，街巷边界不连续；
- 远处有较多空背景，当前仍不像真实密集村庄。

## 已证明和未证明

已证明：

- production camera 的 c2w 可以被当前 Blender 坐标合同正确消费；
- 三个样本能产生合理方向的人眼尺度 RGB；
- 当前 v2 几何在该视角下暴露悬空、稀疏和支撑缺失；
- `010` 暴露近表面遮挡坏帧；
- Batch 6 模块对应真实可见的场景缺口。

未证明：

- 其余 `176/180` 相机都可用；
- 四张 RGB 满足六层 frame contract；
- valid-pixel、ground/sky、near-surface 或 near-duplicate 门已通过；
- 这些帧可用于训练；
- 当前场景达到真实照片重建或 3DGS 质量。

## 对实现的直接要求

### Windows runner

1. 首个 TDD canary 应包含这四个相机。
2. `010` 必须留下 rejected/failed 证据，而不是静默改位或删除。
3. runner 必须验证 v2 build、`.blend`、plan、camera registry、renderer 和 material 输入。
4. 六层产物必须进入耐久 journal，并允许复跑复用相同字节。

### req-5 质量门

1. valid-pixel 继续保留，但不能作为唯一通过条件。
2. 从真实 semantic mask 测量 ground/sky 占比；阈值由首批真实分布选择。
3. 加入近表面主导检测候选，例如 depth 分布与有效区域的近距离集中度；不得仅凭 RGB
   模糊度猜测。
4. 加入相机与 walkable/collision geometry 的最小净空预检候选。
5. 所有新质量门只做 training suitability，不提升 geometry trust。

九宫格射线是净空预检的一项可行输入，但当前四张样本不足以制定通用阈值。不能直接把
`0.6m` 或 `6/9` 写成产品门限；应先对 180 相机测分布，再选择并公开 operator policy。

### 下一版 Blender 场景

1. 修复建筑基础和地形接触，禁止悬空块体；
2. 为 gallery/bridge 增加可见支撑和连续碰撞；
3. 将 Batch 6 的中央院落、桥底水车与建筑后场模块进入几何；
4. 用同四个 production cameras 重渲，做 before/after 对比；
5. 继续保持 `preview-only`，直到更强机器证据成立。

## 依赖说明

- **Codex 可独立推进**：RGB 探针、视觉审计、before/after 验收。
- **原 Opus 职责、Codex 可接管**：Windows runner、req-5 detector、Blender 场景修复。
- **等待用户确认方案 A**：正式 Windows v2 runner 实现。
- **外部依赖**：image2 仍间歇网络失败。
