# HANDOFF-CODEX-008 — Batch 6 素材到 Blender 模块化消费规格

> 作者：Codex
> 日期：2026-07-20
> 状态：可用于建模拆分；不能用于提升 geometry trust
> 接收方：Blender environment / route modeling lane（Opus 暂不可用时由 Codex 接管）

## 目标

把 Batch 6 已成功生成的两张独立设计参考转换成可复用的 Blender 构件、既有场景对象
绑定建议、路线拓扑检查点和 180 相机验收候选集。这样素材不是整张图片贴到场景中，也
不会被误当成同一地点的多视图照片。

本规格只描述如何消费设计意图。所有坐标、尺寸、碰撞、连通性和覆盖结论仍由仓库内的
`ScenePlan`、`ElevatedTopologyPlan`、Blender 产物和正式六层渲染重新挣得。

## 输入身份

| 设计参考 | SHA-256 | 用途 |
|---|---|---|
| `design-route-central-courtyard-eye-01.png` | `19b40a84322ab7d343716bd684fc83a3207ae42ad94993d28446707f7a5537df` | 中央院落、穿堂、排水、上下坡和侧向出口 |
| `design-detail-bridge-undercroft-01.png` | `16b9f390f4550b2ec64bd98e4ccd799e05c4f44cd924a5da1503eec73ae8b4be` | 桥拱底面、桥台、水车、磨坊水槽、溪边维护路 |

两张源图位于：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/environments/
```

它们的共同证据边界：

```json
{
  "camera_calibration": "unknown",
  "geometry_consistency": "not-verified",
  "training_use": "forbidden-as-multiview",
  "coverage_use": "forbidden",
  "trust_effect": "none"
}
```

## 建议场景绑定

以下绑定是**建模目标选择**，不是从图片内容识别出的真实位置：

| 模块组 | 建议绑定 | 当前机器可验证锚点 |
|---|---|---|
| 中央工作院落 | `courtyard-public-002` | `(0.0, 15.0, 72.668)` |
| 中央西侧地面入口 | `central-ground-west` | `path-network-002` |
| 中央东侧地面入口 | `central-ground-east` | `path-network-003` |
| 中央上层穿堂 | `edge-central-gallery-001` | 宽 `2.6m`，净空 `2.4m` |
| 中央上坡台阶 | `edge-central-stair-001` | 宽 `2.4m` |
| 中央梯田坡道 | `edge-central-ramp-001` | 宽 `3.0m` |
| 桥底与水车节点 | `bridge-lower-001` | `(-175.0, -115.0, 41.821)` |
| 桥边主路 | `path-network-001` | `village-network` segment `0` |
| 桥边回连路 | `path-network-005` | `village-network` segment `4` |
| 桥下水系 | `creek-main-001` | `creek-main` |

不得把源图中的任意像素位置直接转换成上述坐标。绑定只给建模工程指定消费位置。

## 模块拆分

### 1. 通行与承重模块

| 模块 ID | 必须建模的表面 | 必须记录的物理属性 |
|---|---|---|
| `route-wet-stone-paving-v1` | 路面、路缘、排水坡面 | 通行宽度、坡度、碰撞面 |
| `route-broad-stair-v1` | 踏步、平台、侧墙 | 踏步尺寸、净宽、连续碰撞 |
| `route-service-ramp-v1` | 坡道、转折平台、挡边 | 坡度、净宽、轮廓连续性 |
| `route-covered-passage-v1` | 地面、柱、梁、屋檐底 | 净宽、净高、遮挡体积 |
| `bridge-stone-arch-v1` | 桥面、拱底、侧脸、桥台 | 桥下净空、桥面宽、封闭碰撞 |
| `route-creek-stepping-stones-v1` | 踏石与水岸过渡 | 可选步行层；默认不计主路线闭环 |

### 2. 水与排水模块

| 模块 ID | 构成 | 约束 |
|---|---|---|
| `watermill-wheel-v1` | 轮体、轴、支架 | 与建筑/桥台分离为可替换实例 |
| `watermill-millrace-v1` | 引水槽、落水口、回水 | 不得穿过实体碰撞或悬空 |
| `drain-open-channel-v1` | 明沟、盖板、转角 | 与通行面保持排水边界 |
| `drain-wall-outlet-v1` | 墙体出水口、滴水区 | 不能从文件名推导水流方向 |
| `culvert-small-v1` | 入口、涵洞、出口 | 至少具有连续内壁和地形支撑 |

### 3. 建筑背面与遮挡模块

| 模块 ID | 构成 | 变化要求 |
|---|---|---|
| `facade-rear-service-v1` | 后墙、基础、小窗、厨房门 | 门窗位置至少三种 variant |
| `facade-side-eave-v1` | 侧墙、檐底、雨槽、排水管 | 屋檐深度和墙材变化 |
| `undercroft-access-v1` | 架空层入口、柱、梁、检修门 | 明确不可通行或可通行状态 |
| `work-shelter-v1` | 柱、顶棚、操作台、储物 | 不得封堵拓扑路线 |
| `retaining-wall-drained-v1` | 挡墙、压顶、排水孔 | 可复用在现有 8 个挡墙对象上 |

### 4. 可替换道具

`firewood-stack`、`basket`、`jar`、`hand-tool`、`roof-tile-stack`、
`bench`、`wash-basin` 和 `storage-rack` 必须作为独立 variant 放置：

- 不写进路线或建筑网格；
- 不承担拓扑连通性；
- 不允许完全堵住出入口；
- 每类至少三种尺度、旋转或组合变化；
- instance/semantic 身份由 Blender registry 明确给出，不能由图片名称推断。

## 拓扑验收

### 中央院落

必须保持既有中央闭环的三个构件：

```text
central-ground-west
  -> edge-central-stair-001
  -> central-upper-west
  -> edge-central-gallery-001
  -> central-upper-east
  -> edge-central-ramp-001
  -> central-ground-east
```

院落内新增排水、工作棚和道具后，以下条件仍须由几何实测成立：

- `edge-central-gallery-001` 净宽不小于 `2.6m`、净空不小于 `2.4m`；
- `edge-central-stair-001` 有连续可碰撞踏面；
- `edge-central-ramp-001` 有连续可碰撞坡面；
- 地面入口继续落在 `path-network-002` 与 `path-network-003`；
- 排水沟、屋檐和道具不侵入行走体积。

### 桥底与水车

桥节点不得把设计图中的五个视觉开口直接宣称成五条正式路线。最低可验证合同是：

- `bridge-lower-001` 继续直接跨越 `creek-main-001`；
- `path-network-001` 与 `path-network-005` 的现有主网络连通性不变；
- 桥面保持主路线能力；
- 桥下维护路如新增，只标为非主闭环 service route，直到碰撞和净空实测；
- 水车、磨坊水槽和踏石不提升 route-loop evidence；
- 桥拱底、桥台和水车平台必须拥有独立 instance 身份，便于遮挡审计。

## 180 相机验收候选集

以下相机由正式 `ProductionCameraPlan` 对锚点做三维距离排序得到。它们只是优先验收集，
不是“已覆盖”的证明。

### 中央院落附近

| 相机 | 组 | 拓扑引用 | 到锚点距离 |
|---|---|---|---:|
| `camera-ground-route-025` | `ground-route` | `path-network-002` | `10.004m` |
| `camera-ground-route-026` | `ground-route` | `path-network-003` | `10.310m` |
| `camera-elevated-pedestrian-011` 至 `017` | `elevated-pedestrian` | `edge-central-gallery-001` | `15.409m` 至 `19.665m` |

### 下游桥附近

| 相机 | 组 | 拓扑引用 | 到锚点距离 |
|---|---|---|---:|
| `camera-ground-route-011` | `ground-route` | `path-network-001` | `7.675m` |
| `camera-ground-route-010` | `ground-route` | `path-network-001` | `10.164m` |
| `camera-environment-corridor-004` | `environment-corridor` | `creek-main-001` | `14.044m` |
| `camera-ground-route-044` | `ground-route` | `path-network-005` | `17.217m` |
| `camera-ground-route-012` | `ground-route` | `path-network-001` | `24.751m` |

对这些相机必须检查：

1. RGB 中模块可辨但没有明显悬空、穿插或重复复制；
2. depth 有限且与桥拱、屋檐、楼梯和水车遮挡关系一致；
3. normal 有限并满足世界空间单位向量合同；
4. instance mask 能区分桥、水车、建筑、挡墙和独立道具；
5. semantic mask 没有用“unknown”掩盖新构件；
6. camera metadata 与 production plan、build、registry 和 renderer 摘要一致；
7. valid-pixel 门只做质量过滤，不提升 L0 synthetic geometry trust。

## 进入正式场景的阶段门

| 阶段 | 允许 | 禁止 |
|---|---|---|
| `design-only` | 视觉拆分、模块命名、variant 规划 | 相机求解、尺寸推断、coverage 声称 |
| `modeled-unverified` | Blender 几何、材质、碰撞候选 | 发布为可漫游已验证内容 |
| `topology-verified` | 实测宽度、净空、碰撞和闭环 | 冒充真实照片重建 |
| `render-verified-l0` | 六层产物、逐帧质量与遮挡审计 | 提升为 measured/metric/aligned |

Batch 6 源图永远停留在 `design-only`；只有重新建模并通过后续阶段门的 Blender 产物
才能进入正式场景。

## 依赖说明

- **Codex 可独立推进**：模块拆分、UX 命名、验收相机选择、遮挡/可读性审计。
- **原 Opus 职责、Codex 可接管**：Blender 构件实现、registry 扩展、碰撞与拓扑集成。
- **需用户确认方案 A 后接管**：Windows 180-camera production runner。
- **外部依赖**：其余 Batch 6 图像仍受 image2 网络服务影响。
