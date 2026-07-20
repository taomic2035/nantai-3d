# REVIEW-CODEX-013 — 中央院落同点六向 360° 审计

> 日期：2026-07-20
> 角色：Codex（360° UX / visual audit lane）
> 结论：六向可渲染，但当前原型不满足可发布的 360° 漫游视觉质量。

## 审计目的与边界

从中央院落同一个人眼坐标渲染标准六面 cubemap：

```text
position = (0.0, 15.0, 74.3)m
faces = +X, -X, +Y, -Y, +Z, -Z
fov = 90°
resolution = 768×768 per face
```

这能检查用户原地 360° 转头时是否出现：

- 相机埋地或近表面完全遮挡；
- 天顶封死；
- 四周建筑悬空、穿插和断裂；
- 地面接缝；
- 高架结构跨视野遮挡；
- 模块只在一个方向可见、其它方向空洞。

它不能证明 3DGS/SfM coverage。六张图共享同一相机中心，没有平移基线，不能仅靠这组六向图
恢复可靠几何。所有输出都是私有 RGB 审计，不是 registered camera、六层帧或训练证据。

## 输入与机器身份

```text
source prototype blend SHA:
f6ac14fa1380905fc11bc50698d056fd3e13c4d6c01d6d3eaf4312f2fbb7bd5e

cubemap manifest SHA:
11933af45480533eddf98b5034c18f14cda387f94b4b3821fbfe45433220645d
```

私有路径：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
  render_central_cubemap_audit.py
  central-cubemap-audit-v1/
    cubemap-manifest.json
    rgb-central-cubemap-{px,nx,py,ny,pz,nz}.png
```

manifest 明确声明：

```text
private RGB visual audit only
not registered camera metadata
not six-layer frame
not training evidence
not coverage proof
not trust upgrade
```

## 六面结果

| 面 | 世界方向 | SHA-256 | 审计结论 |
|---|---|---|---|
| `px` | `+X` | `52e6985f70161a0f34f742d0fa262979d842709b98c66755c8da9b3be4ff1fed` | 路线有纵深，但高架栏杆横穿中景，树冠仍是低多边形块体 |
| `nx` | `-X` | `ff65994b12188c7dec4d6fd3a8b2c6849a2ed8ebc1f670c1737b5d1c709fd60a` | 工作棚可读；多栋建筑基础明显悬空 |
| `py` | `+Y` | `92a1d02d88d911933c29d39dcdecfe95cf46dce834c1d3c242ced9b62a9d1ffe` | `building-central-008` 大面积悬空，院落/坡面关系不可信 |
| `ny` | `-Y` | `56a61e4446fd07e98ef7bb50255a1e72e7d57fbe691ab08838f9545313d007c9` | 高架步道横向切断主要视野，远处建筑稀疏且悬空 |
| `pz` | `+Z` | `06626b0c4baa03e37dc8865680a27f6ee3123f61f587826361bde747ab49fbec` | 天顶开放，右上有棚架屋檐进入视野；没有封死 |
| `nz` | `-Z` | `12f7341e8482e5af86177c3aea567aaab1db9077ff67a9f1e2084042d0c457c2` | 相机未埋地，但湿石铺地与土路呈大块三角接缝/覆盖关系 |

## 结论分级

### 可确认

- 同一坐标六个方向都能完成 Blender RGB 渲染；
- 天顶没有被错误几何封住；
- 脚下能看到地面而不是黑屏或近表面全遮挡；
- 中央院落新增工作棚、排水和铺地至少在多个水平方向可辨。

### 明确失败

- 多栋建筑缺少地形接触和基础支撑，360° 转身时悬空问题反复出现；
- `-Y` 方向被高架步道横向切断，当前构件布局和净空不适合人眼漫游；
- 地面材料/网格边界在脚下形成明显三角拼接；
- 天空和远景过空，缺少可信山体、植被层和大气深度；
- 同一点的反向视图没有达到素材参考中的高密度、多出口、多层次村庄体验。

### 仍未证明

- 无六层 depth/normal/instance/semantic/camera metadata；
- 无相邻平移机位，因此没有共视、parallax、SfM 或 3DGS 训练证据；
- 无碰撞、净宽、路线闭环和任意坐标移动验证；
- 无 cubemap seam 的投影级校验；
- 无 Viewer 实际 cubemap/equirectangular 消费测试。

## 对“360° 漫游”的含义

同点六向 cubemap 只证明“原地转头时能看到什么”。真正的任意坐标漫游还需要：

```text
多个可行走坐标
  × 每个坐标的多方向观察
  × 相邻坐标之间有足够平移基线和共视
  × 实际六层/相机/位姿证据
  × 可碰撞、连续的路线体积
```

因此不能把这 6 张 RGB 当成“360° 训练集已完成”。

## 生产修复要求

1. 所有建筑加入地形接触、基础或支撑，禁止悬空块体。
2. 重新审计 elevated walkway 的高度、支撑、视野占比和路线净空。
3. 解决 path/courtyard/terrain 的重叠网格与大三角材质接缝。
4. 提高树木、远景山体、林缘和聚落密度，避免转身后出现大片空背景。
5. 在 production build 中生成内容寻址的六向审计相机或等价可验证采样，但继续与
   training camera registry 分离。
6. 对多个 ground/elevated/bridge/service 坐标重复该审计；一个中央点不能代表无限世界。
7. 最终 coverage 仍从 production 六层帧与相邻平移相机实测，不从 cubemap 文件名推断。

这些要求补充：

```text
handoff/HANDOFF-OPUS-007-batch6-modules-productionization.md
```
