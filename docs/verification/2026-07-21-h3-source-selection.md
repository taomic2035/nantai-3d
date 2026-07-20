# H3-A AI 材质原生源选片与私有发布证据

日期：2026-07-21

## 结论

H3-A 八个 hero 材质槽位已各生成三张 image2 候选，共 24 张；先冻结像素审计分布与门限，再检查固定 contact sheet，最后人工选择一张。严格 importer 已发布只含八张入选原始字节的私有 source pack：

```text
source_pack_id = be92da7d0c2d1956b7775d3422b914f32e5dc29bb233e394c1f33e346eff26b4
manifest_sha256 = c80e7093c04cd8c94dc80b4a0e107545acf10ff91c7e7c125b470963d8975bee
records = 8
native source size = 1254 x 1254
```

这些字节是 AI 合成材质输入，不是真实南台照片，也不是 4K 原生输出。后续 4096 输出只能称为 `4096 authored master`。

## 信任与使用边界

```text
synthetic = true
ai_generated = true
real_photo_textures = false
geometry_usability = preview-only
metric_alignment = false
verification_level = L0
rights = private-project-use-only
public_release_authorized = false
```

本 source pack 改善程序化村庄的表面材质输入；它不提供相机标定、共视关系、真实几何、SfM/3DGS 训练证据或 360° 覆盖证明。任意坐标漫游仍由已有确定性分块几何、LOD 和 Viewer 调度提供，八张图仅作为可替换材质源。

## 冻结审计

审计在选片前写入：

```text
.nantai-studio/h3/candidates/preselection-audit-freeze.json
sha256 = 3fe6c76bf545496067405537098a187cd3474a299ddd56c7c15e76aeb7122ccd
candidate_count = 24
algorithm = h3-candidate-pixel-audit-v1
```

冻结 policy：

| 门 | 最大值或最小值 |
|---|---:|
| width | >= 1024 |
| height | >= 1024 |
| alpha nonopaque fraction | <= 0.0 |
| clipped fraction | <= 0.02 |
| dominant perspective score | <= 0.35 |
| edge energy，Q3 + 1.5 IQR | <= 0.07349356 |
| opposite-edge disagreement，Q3 + 1.5 IQR | <= 0.1104970075 |

两张未选候选超过冻结的 opposite-edge 门：

- `material-gray-roof-tile-01 / candidate 1`：`0.17169789`；
- `material-dry-stone-wall-01 / candidate 3`：`0.12958637`。

它们保留在私有候选证据中，但不会进入发布 source pack。Importer 修复提交 `8d8ef75` 锁定了这个边界：未选失败候选可保留，任何被选失败候选仍 fail closed。

## 固定 contact sheet

```text
path = .nantai-studio/h3/candidates/contact-sheet.png
dimensions = 1536 x 4384
sha256 = ff2d1df67361104c03eb9b4503d3b1dab1787ef2bb6e8575c905e22ddabf7b9c
layout = 8 rows x 3 candidates
cell = 512 x 512, neutral label strip, sRGB PNG, no enhancement
```

## 入选对象

| 槽位 | 入选 SHA 前缀 | 尺寸 | clipping | perspective | edge | opposite edge | 选择理由 |
|---|---|---:|---:|---:|---:|---:|---|
| `material-weathered-timber-01` | `fb20a8ff4f14` | 1254x1254 | 0.00184163 | 0.03326008 | 0.04891735 | 0.08437523 | Candidate 3 keeps readable medium-gray board scale with restrained tonal drift and fewer large stains, making later quilting less likely to repeat a focal weather patch. |
| `material-dark-timber-01` | `7dd7bbd592d0` | 1254x1254 | 0.01163783 | 0.08356263 | 0.03697735 | 0.06149889 | Candidate 3 preserves charcoal-brown grain and fine checks without the broad soot-like tonal bands visible in candidate 2, giving a more neutral reusable dark timber source. |
| `material-gray-roof-tile-01` | `3c14e2872b18` | 1254x1254 | 0.00008055 | 0.03648317 | 0.03182186 | 0.05449594 | Candidate 3 is the passing curved barrel-tile option with consistent human-scale overlaps; candidate 2 depicts the wrong flat shingle family and candidate 1 fails the frozen edge-disagreement gate. |
| `material-fieldstone-01` | `1385659a46c2` | 1254x1254 | 0.00000000 | 0.03548084 | 0.05348680 | 0.06324755 | Candidate 3 offers the most evenly distributed small-to-medium mortared stones, stable scale, and no single oversized stone cluster that would dominate repeated quilting. |
| `material-dry-stone-wall-01` | `bf56aea3ddd1` | 1254x1254 | 0.00704349 | 0.05452839 | 0.05252280 | 0.06339244 | Candidate 2 passes the frozen audit and balances warm and cool angular stones with convincing chinking and no mortar; candidate 3 is excluded by the edge-disagreement gate. |
| `material-rammed-earth-01` | `49fd3e0f22f5` | 1254x1254 | 0.00000170 | 0.05253996 | 0.04242535 | 0.03944658 | Candidate 2 retains readable irregular lift boundaries and fine aggregate while avoiding the stronger regular banding of candidate 3 and the flatter low-detail appearance of candidate 1. |
| `material-packed-earth-01` | `7acbfb5fe257` | 1254x1254 | 0.00000085 | 0.03298152 | 0.04960436 | 0.05145000 | Candidate 1 is the balanced mid-brown compacted surface with subtle grit and minimal baked moisture pattern, providing the most reusable path and courtyard ground base. |
| `material-terrace-soil-01` | `14fceffb2559` | 1254x1254 | 0.00000636 | 0.01944232 | 0.06749030 | 0.08303260 | Candidate 3 separates cultivated soil from packed ground through varied clods and sparse root fibres while keeping moisture variation restrained and evenly distributed. |

## 私有证据与封闭性

```text
selection receipt:
  .nantai-studio/h3/candidates/selection-receipt.json
  sha256 = c7ded9fffc45b0090d0ac5faa97a5983088f806469e247d6ff0d895e59934f54

published root:
  .nantai-studio/h3/source-pack/be92da7d0c2d1956b7775d3422b914f32e5dc29bb233e394c1f33e346eff26b4/
```

发布目录恰好包含一个 canonical `manifest.json` 和八个 `sources/<sha256>.png`；未选候选、临时生成路径、请求 ID、时间戳和机器名均不进入 source-pack closure。本轮没有创建或上传公开 Release。

## 下一阶段

下一步从这八张经验证原始源确定性生成 4096 authored master、base-colour/normal/ORM 与完整 mip 证据，再进行 KTX2 编码、Blender 同机位材质替换和 Viewer 全局 H2 回退验证。建筑、屋顶、植被、围栏和挡墙的真实拓扑仍属于 H3-B，不能由本材质包代替。
