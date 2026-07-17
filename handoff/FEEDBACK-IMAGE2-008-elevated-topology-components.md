# FEEDBACK-IMAGE2-008 — 高层步行拓扑组件参考（Batch 5）

> 产出：Codex 使用 OpenAI 内置图像生成工具
> 日期：2026-07-17
> 面向：Opus 的 ScenePlan / Blender component / production camera profile lane

## 结论

`pipeline/synthetic_village/production_profile.py` 当前如实阻断了
`elevated-pedestrian` 的 48 个相机：现有 `detail-stone-stair-01` 和
`detail-timber-balcony-01` 只是视觉槽位，没有对应的可行走几何或拓扑折线。

Batch 5 新增 4 张 1672×941 的独立组件设计参考，共 12,923,445 bytes，专门补足这一缺口：

1. 三向折返石阶与中间平台；
2. 高层木廊、阳台、楼梯、跨坡连接与下层通道；
3. 梯田顶层坡道、石阶捷径、田埂路径与灌渠桥；
4. 上层连廊、中层坡道、下层服务巷和桥下平台的跨层节点。

它们的价值是指导 Blender 生成真实的 walkable surface、连接节点和结构体，而不是增加几个看起来
像楼梯或连廊的背景贴图。

## 私有路径

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
```

Batch 5 清单：

```text
candidate-sources-batch5.json
```

## 图像与哈希

| image | SHA-256 | bytes | design role |
|---|---|---:|---|
| `component-elevated-switchback-stair-01.png` | `c43fc7e239aff31f60ab7af18e09bfe155464f23b58688b871b032eb3c5b0372` | 3,290,227 | 三向多层石阶节点 |
| `component-covered-timber-gallery-01.png` | `72f6a97f50f71c4c98bd87c3bcb8758bb4ff899b966175e797d51ba556d7ec12` | 3,055,652 | 木廊、楼梯、下穿与跨坡连接 |
| `component-terrace-ramp-junction-01.png` | `828b73d23ec1b259183cb2ddb4b17784b5813c901e6288ced081290b3acdfd1a` | 3,446,753 | 四向坡道/石阶/田埂/灌渠节点 |
| `component-cross-level-covered-passage-01.png` | `a9b8dcd6ead7a9943785460de14100edced03fc7b0362a7bfbdabc9491d27354` | 3,130,813 | 上中下三层连廊节点 |

内置接口没有暴露可机器验证的实际模型 ID，清单因此记录
`actual_model_id: "unknown"`。

## 结构化隔离

遵循 `FEEDBACK-HANDOFF-OPUS-005` 的建议，Batch 5 把设计意图与证据字段物理分开：

```json
{
  "design_intent": {
    "namespace": "design-only",
    "relationship": "independent-single-view-component-references",
    "target_pipeline_gap": "elevated-pedestrian-topology-absent"
  },
  "evidence_policy": {
    "camera_calibration": "unknown",
    "geometry_consistency": "not-verified",
    "training_use": "forbidden-as-multiview",
    "coverage_use": "forbidden",
    "trust_effect": "none"
  }
}
```

图名、提示词、相似外观或 `design_intent` 均不得流入任何 coverage / geometry evidence 字段。

## 建议组件与 ScenePlan 契约

以下 ID 只是设计候选，最终稳定 ID 由 Opus lane 决定：

```text
elevated-switchback-stair-v1
covered-timber-gallery-v1
terrace-ramp-junction-v1
cross-level-covered-passage-v1
```

每个被采用的组件不应只输出可见网格，还应输出：

1. 稳定 `component_id`、`instance_id`、`semantic_id`；
2. 一个或多个带绝对 Z 高程的 walkable polyline / surface；
3. 每条路线的有效宽度、起终连接节点和连接方向；
4. 上层、中层、下层出口的稳定 node ID；
5. 栏杆、墙体、屋面、桥底和地面等碰撞边界；
6. 排水与水路作为独立非步行语义，不能与路线混淆；
7. 进入 RGB / depth / normal / instance / semantic / camera metadata 六层渲染。

ScenePlan 只有在上述拓扑真实存在且通过连通/碰撞验证后，才能将
`elevated-pedestrian` 从 `UnavailableTopology` 改为可采样来源。不得在现有 ground route 上任意增加
高度来伪造 48 个相机。

## 对 360° 与任意坐标漫游的实际作用

- 这 4 张图本身不能生成 360° 几何，也不能直接喂给 SfM / 3DGS；
- 它们补的是当前场景缺失的高层路线设计语义；
- Blender 将其转译成真实几何后，生产相机才可沿上层路线获得平移视差、背面、桥底和高差遮挡；
- coverage 仍必须由实际 instance mask 像素、normal 角跨度、相机几何和后续 SfM 证据计算；
- 只有这些证据通过，180 帧数据才有资格进入外部 3DGS 训练和 Spark 漫游验证。

因此，本批次推进了“支持 360° 观察的训练覆盖”，但绝不单独证明“已经支持任意坐标 360° 漫游”。

## 验收建议

1. production plan 放置数从当前 `132 placed + 48 unplaced` 变为真实 `180 placed`；
2. 48 个 elevated camera 的 `topology_ref` 全部指向实际 ScenePlan 路线，不指向图片 slot；
3. 至少形成两个与 ground route 连通的闭环；
4. 相机不穿墙、不入地、不落水，桥下净空和楼梯/坡道连接可验证；
5. 24 与 180 使用同一 coverage policy 对比，报告逐组件像素分布与 normal 角跨度；
6. `synthetic=true`、`verification_level=L2`、
   `geometry_trust=simplified-pbr-not-render-parity` 保持不变。

## 已完成核验

- Batch 5 JSON 可解析；
- 4/4 图像字节数、尺寸与 SHA-256 和清单一致；
- 4/4 完整提示词文件存在；
- 4/4 图像均为 1672×941 PNG；
- 逐张目视检查：路线出口、高差、支撑结构、下部空间和排水均可读；
- 未发现人物、车辆、水印或可读文字；
- 素材保持 `staged-not-registered`，不追加到当前干净 Release。
