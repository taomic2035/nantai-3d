# REVIEW-CODEX-008 — Batch 6 参考与当前 v2 Blender 场景差距

> 审计：Codex（UX / 呈现 / 设计 / 交互 / review）
> 日期：2026-07-20
> 对象：Windows textured L2 build `4f38ecf...`
> 结论：已有真实 Blender 几何与纹理，但尚未达到人眼尺度真实村庄

## 结论

当前 Windows v2 build 不是空壳：它有内容寻址的 `.blend`、GLB、四张 Blender 实渲预览、
稳定对象 registry 和 `simplified-pbr` 材质。然而四张预览共同证明，它仍是稀疏的宏观
布局模型：

- 建筑多为孤立小块体，缺少围合院落、连续街巷和前后侧立面细节；
- 桥是矩形桥板/线性通道，没有桥拱底面、桥台、水车或桥下维护空间；
- 路面、耕地和植被材质存在明显大面积重复；
- 四张预览均为高位观察，不能检验人眼高度的净空、遮挡与沉浸感；
- Batch 6 三张参考中的中央院落、桥底节点和建筑后场尚未进入正式几何。

因此，当前状态可以诚实描述为：

```json
{
  "real_blender_geometry": true,
  "real_material_assignments": true,
  "human_scale_environment_detail": false,
  "batch6_modules_integrated": false,
  "geometry_usability": "preview-only",
  "fidelity": "simplified-pbr-not-render-parity"
}
```

“真实几何”只表示 Blender 中存在可渲染网格；不表示它已经与真实照片一致或达到产品级
真实感。

## 机器身份

```text
build id:
4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc

schema:
nantai.synthetic-village.blender-build-report.v2

platform:
windows-x64

Blender:
4.5.11

geometry usability:
preview-only

fidelity:
simplified-pbr-not-render-parity
```

四张预览的当前字节与 `build-report.json` 登记值逐字节一致：

| 预览 | 字节 | SHA-256 |
|---|---:|---|
| `preview-central.png` | `1,008,814` | `100a5968cb55e77d755b6e22c5dc0e2d2d4b6949afcc4f319724941a46ed867d` |
| `preview-bridge.png` | `1,085,997` | `7ababbbd958b305205016da195a0ee97d95f50c7e95e5022bc36a73380df4ff3` |
| `preview-outer.png` | `956,688` | `82ac7b422c49806034db7d62e43a492e0b0c44179809c7b3412b8821392fbcc7` |
| `preview-upper.png` | `908,732` | `7fc7ace7de912ecd9ec6019bc722486b7252659da975e313747264f95556dda0` |

本审计不以截图文件名推导质量。上述身份来自报告字段和实测 SHA；视觉结论来自逐张查看
对应字节。

## 逐张视觉审计

### `preview-central.png`

已具备：

- 高低通道和线性廊桥；
- 分散建筑、道路、耕地和植被区；
- 石、土、瓦、木、灰泥等材质区分。

明显缺口：

- 没有围合的中央院落空间；
- 建筑距离过疏，路线两侧缺少连续立面和遮挡层；
- 现有廊桥像独立工程构件，缺少与建筑入口、檐口和院落的自然连接；
- 看不到门槛、排水、工作棚、台阶细节和生活道具；
- 高位取景无法证明人眼通行净空。

### `preview-bridge.png`

已具备：

- 桥与道路存在拓扑连接；
- 桥体位置与周围路径、地表可读；
- 桥区有可渲染实体而非纯 UI 占位。

明显缺口：

- 桥体是直线矩形桥板，没有石拱底面和结构化桥台；
- 没有水车、磨坊水槽、落水、排水口或服务平台；
- 溪流与岸线缺少人眼尺度的深度、湿润和维护路径表现；
- 桥下是否可通行、净空多少、碰撞是否连续均无法从该预览验证；
- 周围建筑与桥区关系稀疏，未形成村庄节点。

### `preview-outer.png`

已具备：

- 大范围地形、村落、耕地和道路的宏观布局；
- 建筑和场地覆盖多个高度区；
- 可作为场景总体范围与密度下限的构图参考。

明显缺口：

- 大片地表使用重复纹理，视觉尺度与地形尺度不一致；
- 建筑分布像散点而非连续聚落；
- 路线两侧没有丰富边界、挡墙、排水和植被过渡；
- 山体几何过于规则；
- 无法从高空总览判断任意坐标漫游时的近景质量。

### `preview-upper.png`

已具备：

- 上层 gallery/ramp 结构；
- 高位场地与下层路线有高度差；
- 上层聚落位置已进入真实场景。

明显缺口：

- 上层平台近似空旷平面，没有上层道路的建筑边界；
- 缺少屋顶背坡、建筑后墙、梯田入口和服务院落；
- gallery 两端连接语义弱，像孤立的桥梁构件；
- 缺少侧向路线和人眼尺度的转折遮挡；
- 与 Batch 6 “上层道路”待生成素材之间仍有显著设计空洞。

## Batch 6 集成差距

| Batch 6 设计输入 | 当前 v2 build | 必须补齐 |
|---|---|---|
| 中央院落人眼视角 | 只有稀疏建筑与线性通道 | 围合院落、穿堂、台阶、坡道、排水、工作棚、独立道具 |
| 桥底与水车路口 | 只有矩形桥板 | 石拱、桥台、水车、磨坊水槽、桥下净空、溪边维护路 |
| 建筑后场与服务院落 | 无连续建筑背面 | 后/侧立面、檐底、架空层、跨巷廊桥、菜圃、服务道具 |

模块拆分、拓扑约束和候选验收相机已定义在
`HANDOFF-CODEX-008-batch6-to-blender-modular-consumption.md`。

## 下一版 build 的最低视觉门

下一版 Windows textured build 至少要满足：

1. `preview-central` 能看见有空间边界的院落，而不是散点建筑；
2. `preview-bridge` 能区分桥面、拱底、桥台和水车节点；
3. `preview-upper` 能看见建筑背坡、上层街巷和梯田入口；
4. 至少增加一个人眼高度的 central/bridge/upper 预览或通过 production camera 生成同等证据；
5. 新增模块拥有明确 instance 与 semantic registry 身份；
6. 路线净宽、桥下/檐下净空、碰撞和排水不从参考图推断；
7. 所有输入与产物继续绑定 SHA，build 仍保持 `preview-only` 和
   `simplified-pbr-not-render-parity`，除非另有更强机器证据。

这些门证明“素材已进入可检查的真实几何”，不证明真实照片重建或 3DGS 质量。

## 与 180-camera runner 的关系

当前 v2 build 自带的四张预览足以证明宏观布局和材质存在，但不足以证明：

- 人眼漫游连续性；
- 正反面与遮挡覆盖；
- 六层训练帧完整性；
- near-duplicate、孤立机位或天空/地面坏帧质量。

因此 Windows 180-camera runner 仍是正式覆盖验收的阻塞项。它不应通过删除 Mac 平台门
实现；建议采用已提出的方案 A：增加独立 Windows v2-build 验证适配器，并复用现有
六层 frame/journal/quality 合同。

## 依赖说明

- **Codex 可独立推进**：视觉差距审计、Batch 6 继续生成、模块与验收规格。
- **原 Opus 职责、Codex 可接管**：下一版 Blender 构件、registry 和场景集成。
- **等待用户确认 A**：Windows 180-camera production runner。
- **外部依赖**：image2 仍间歇网络失败；真实照片/视频与云 GPU 3DGS 训练仍未到位。
