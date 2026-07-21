# FEEDBACK-IMAGE2-019 — Batch 15 合成材质 albedo 源（部分）

> 日期：2026-07-21  
> 生成：OpenAI built-in image generation，经 Codex imagegen  
> 状态：`2/6` 成功；其余请求因图像服务网络错误可重试；没有 Release

## 结论

现有 Batch 6–14 已有 82 张整景、方向、边界、垂直包络、斜向路线和模块板素材。
继续堆相似整景的边际收益已经下降，因此 Batch 15 转向 Blender 当前更缺的独立、
可替换材质源。计划矩阵为：石墙、灰瓦、旧木、白灰墙、湿石路、浅溪床各一张。

本轮成功两张，私有候选区总图数由 82 增至 84：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch15/
```

| slot | size | bytes | SHA-256 |
|---|---:|---:|---|
| `material-albedo-fieldstone-masonry-01` | `1254×1254` | `3,510,962` | `4bc32e67581aa7950ebc7034904b0e3c9aaeaf52c97b3a6294ffc0c31aa34da8` |
| `material-albedo-gray-clay-roof-tile-01` | `1254×1254` | `3,327,781` | `beb277389cdcceccb1b1084ba0c1e9e396954ae8cf02b4f44a609f7c04b82c05` |

两张原图和六份精确提示词均保存在私有 Batch 15 目录；失败请求没有写空文件。
`generation-queue-batch15.json` 与 `candidate-sources-batch15.json` 已逐项解析，
两张图的 SHA、字节和尺寸重算一致。

## 接缝审计

提示词要求 seamless，但机器证据不能从提示词推导。本轮使用 19px 对侧边缘条带，
计算归一化 RGB mean absolute error：

| slot | left/right MAE | top/bottom MAE | verdict |
|---|---:|---:|---|
| fieldstone | `0.105230` | `0.135629` | not seamless |
| gray roof tile | `0.092204` | `0.141100` | not seamless |

两张图视觉上平直、尺度均匀、没有文字、水印、墙角、屋脊或明显透视，但对侧边缘
仍不匹配。因此它们只能声明：

```text
synthetic=true
metric_texel_scale=unknown
seamless_edges=not-verified
color_space=unknown-unprofiled-png
pbr_map_consistency=not-generated
texture_use=albedo-source-only-not-registered
trust_effect=none
```

它们不能直接进入 material registry，不能作为已验证 albedo，更没有对应的 roughness、
normal、height 或 displacement 一致性证据。

## 网络失败

石墙和灰瓦成功后，旧木、白灰墙与旧木低频重试共三次请求均在服务端返回：

```text
network error sending the built-in image generation request
```

图像服务没有返回文件。按照 imagegen 规则，本轮没有悄悄切换到 CLI、其它模型、网页
截图或未知来源素材。湿石路与浅溪床尚未发起，避免在服务异常时堆积请求。

## 续跑与消费顺序

1. 服务恢复后按 queue 继续旧木、白灰墙、湿石路、浅溪床，成功一张登记一张；
2. 使用 imagegen reference edit 分别做单一目标的对侧接缝修复，原图保留不覆盖；
3. 对修复图跑 2×2 平铺目视检查、对侧 MAE、低频重复结构和色彩梯度检查；
4. 只有 seam policy 通过后才进入干净 Release 候选；
5. 用版本化材质工具链生成并验证 roughness/normal/height，不能让生成模型手填
   “PBR 已完成”；
6. 在 fresh Blender build 上渲染近、中、远三个尺度的 RGB 与 normal，检查平铺、
   频率、色偏和摩尔纹后，才允许进入 registry。

本批改善的是“真实纹理贴图的可替换源”缺口，不证明真实照片纹理、材质物理参数、
360° coverage、任意坐标几何或 3DGS reconstruction 已经完成。

