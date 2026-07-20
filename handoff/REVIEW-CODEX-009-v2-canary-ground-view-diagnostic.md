# REVIEW-CODEX-009 — v2 canary 人眼视角诊断

> 审计：Codex
> 日期：2026-07-20
> 对象：Windows textured L2 build `4f38ecf...`
> 性质：私有 RGB 诊断，不是正式 production frame

## 结论

当前 v2 `.blend` 中登记的三类近地 canary 相机不能作为“人眼漫游已经可用”的证据：

- `camera-ground-001` 以 `-35.517°` 明显俯视地表；
- `camera-courtyard-001` 以 `-24.781°` 俯视院落铺地；
- `camera-bridge-001` 虽接近水平（`-1.516°`），但视野大部分被极近石材几何遮挡。

三张实际 Blender RGB 的共同问题是：画面被地面或近表面主导，看不到可导航的村庄
空间、连续建筑立面和路线前方。因此高空 preview 与现有 24-camera canary registry
都不能替代尚未运行的 180-camera production 计划。

这不是“没有真实 Blender 场景”的问题，而是**当前可运行的相机合同没有给出可用的人眼
验收帧**。

## 输入身份

```text
build id:
4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc

build report sha256:
aaf3a6b9fb6f48b3336e55f44f203504d58782a95a2738d70ee773464471e065

blend sha256:
fa8cc4aabfe5049f2025e9d2ab34739c0914d87aa78a8fbda21ad86299cbebac

Blender:
4.5.11 LTS / windows-x64
```

报告仍声明：

```json
{
  "verification_level": "L2",
  "geometry_usability": "preview-only",
  "fidelity": "simplified-pbr-not-render-parity"
}
```

## 方法

私有诊断脚本：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
  render_ground_diagnostic.py
```

它只执行：

1. 读取现有 `village-canary.blend`；
2. 按 build report 已登记名称选择相机；
3. 设置 `1024 × 576` PNG 输出；
4. 调用 Blender render；
5. 不保存 `.blend`、不发布 journal、不写 registry、不提升信任。

这些图片不是 build artifact，也没有加入 Git 或 Release。

## 实测相机方向与 RGB 身份

Blender 相机以本地 `-Z` 为前向。下表的 world forward 与 pitch 由
`measured_c2w_blender` 计算；负 pitch 表示向下。

| 相机 | 世界坐标 | forward | pitch | RGB SHA-256 |
|---|---|---|---:|---|
| `camera-ground-001` | `(4.0, 0.0, 71.855)` | `(-0.813940, 0.0, -0.580949)` | `-35.517°` | `7092e12e290f9f009ca6548dc1b76e60c904eee7f5b9844b0d33b819d9231c39` |
| `camera-courtyard-001` | `(-211.0, -82.0, 51.397)` | `(0.907915, 0.0, -0.419154)` | `-24.781°` | `0d2a2e3f8a6bc7a4657ef297273970dad3c729c38dc3a6727cbf1e859df5c2bc` |
| `camera-bridge-001` | `(-171.0, -125.0, 40.856)` | `(-0.371261, 0.928152, -0.026452)` | `-1.516°` | `7f30c6781e6ab082a8672923d6034e154f03fad89799f09266f31978ddaac8db` |

对应私有 RGB：

```text
rgb-camera-ground-001.png       1,052,067 bytes
rgb-camera-courtyard-001.png    1,025,758 bytes
rgb-camera-bridge-001.png         844,702 bytes
```

## 逐帧结论

### `camera-ground-001`

- 主要内容是土路材质与地表块边界；
- 近景圆形/多边形物体遮住画面下部；
- 没有可读地平线、建筑立面或路线前方；
- 即使 valid-pixel 很高，也不是有用的人眼训练帧。

### `camera-courtyard-001`

- 主要内容是石铺地、土路板块和两个低细节植被对象；
- 看不到围合院落、门窗、工作棚、出口或路线前方；
- 俯视 pitch 与画面一致，不是偶发渲染错误；
- Batch 6 中央院落参考尚未被几何消费。

### `camera-bridge-001`

- pitch 接近水平，因此坏帧根因不是“只会向下看”；
- 画面大部分被极近石材表面占据，左侧仅残留狭窄远景；
- 可能是相机与地形/桥区大块体间距不足或视线被邻近表面截断；
- 没有桥拱、水车、桥下通道或可导航路线的有效视野。

本审计没有重算相机到每个网格的最近距离，因此“贴入几何”仍是待测假设；可以确认的
机器事实是相机姿态、真实 RGB 字节和严重近表面遮挡。

## 为什么 valid-pixel 门不够

这三张图很可能包含大量非背景像素，但非背景内容本身可能是：

- 单一地面；
- 极近墙面/石面；
- 无路线前方的遮挡体；
- 重复纹理。

所以当前 `minimum_valid_pixel_ratio` 只能拒绝大片背景，不能拒绝“全是地面”或“全是
近墙”的帧。这与 production profile 已诚实声明的 req-5 缺口一致：

- 尚无 sky/ground semantic bad-frame detector；
- 尚无 isolated camera detector；
- 尚无 defensible near-duplicate threshold。

不得因为画面填满有效像素就把它当成有用训练帧。

## 与 180-camera 计划的区别

本诊断使用的是 v2 build 内的 24-camera canary registry，不是 180-camera
`ProductionCameraPlan`。

当前生产计划的机器数据表明：

| 生产组 | 数量 | forward pitch 范围 | 平均值 |
|---|---:|---:|---:|
| `ground-route` | `72` | `-10.778°` 到 `14.745°` | `4.919°` |
| `elevated-pedestrian` | `48` | `-25.437°` 到 `25.437°` | `0.214°` |

生产相机姿态分布明显不同于 canary 的两个严重俯视相机，但姿态合理不等于实际画面
通过。只有把 production cameras 真正送入 v2 `.blend` 并生成六层帧，才能检查遮挡、
地面占比、碰撞、instance/semantic 和有效像素。

## 必须补齐

1. Windows runner 必须消费 v2 build，而不是复用 24-camera canary registry。
2. 首个 canary 应渲：
   `camera-ground-route-010`、`011`、`025`、`026`，覆盖桥区与中央院落。
3. 除 valid-pixel 外，应把 ground/sky semantic 占比和近表面主导帧列入 req-5，
   但阈值必须通过真实帧分布选择，不能在没有数据时拍脑袋。
4. 被拒帧仍保留完整证据，不能删除以隐藏场景或相机问题。
5. Batch 6 模块进入下一版 `.blend` 后，用同一组 production cameras 重渲比较。

## 依赖说明

- **Codex 可独立推进**：诊断渲染、视觉审计、坏帧语义规格。
- **原 Opus 职责、Codex 可接管**：相机/构建适配器、quality detector 和 Blender 场景修复。
- **等待用户确认方案 A**：Windows v2 production runner 实现。
- **外部依赖**：image2 继续间歇网络失败。
