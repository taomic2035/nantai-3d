# FEEDBACK-IMAGE2-026 — Batch 22 水车局部环绕设计输入

> 日期：2026-07-23  
> 生成：Codex 使用 OpenAI built-in imagegen  
> 状态：`12/12` 已生成、原尺寸目视复核、逐图提示词/SHA 绑定并形成干净 Release

## 结论

Batch 22 提供 8 个围绕通用山村水车的方向角色、2 个构造细节和 2 个模拟材质输入，供开放
单水轮、引水槽/轮轴、架空层/尾水、环形步道与 Blender 材质方向建模。所有图片都是独立
`design-only` 参考，不是同一物理场景的标定多视图，也不增加 360° coverage 或真实重建信任。

私有候选与 QA：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch22/
```

干净 Release：

- tag：`synthetic-village-design-inputs-batch22-2026-07-23`
- archive：`synthetic-village-watermill-local360-design-pack-batch22-2026-07-23.zip`
- archive bytes：`37067447`
- archive SHA-256：`1f842f8ce5eb52bafb5bb6d8a581816e1c7571187537e45ace6af669365fb07f`
- URL：`https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch22-2026-07-23`

Release 严格只含 12 张最终 PNG、12 份逐图 prompt、`manifest.json`、`USAGE.md` 与
`PAYLOAD-SHA256SUMS.txt`。解压复核为 27 个条目，26 个 payload checksum 全部一致；私有
`rejected/`、旧候选、失败图、联系表和生成队列没有进入包。

## 素材身份

| file | size | bytes | SHA-256 | 用途 |
|---|---:|---:|---|---|
| `context-watermill-front-01.png` | `1672×941` | `3135992` | `d77450622e657d886e73226e238a636142680106e21888662493458b407173c4` | 前向入口、双侧路径、单水轮 |
| `context-watermill-front-right-01.png` | `1672×941` | `2967597` | `d83d0e8790b40bacd3751a920211cac84b820c3c9c0793ff24f48c15ac75a23f` | 右前轮轴、平台、溪流 |
| `context-watermill-right-01.png` | `1672×941` | `2996894` | `0d4bb0cfaa3b46bdeb13d7f865bb0b00e288fb316ca5669428f87f511f7d95d7` | 右侧轮廓、引水槽、村庄远景 |
| `context-watermill-rear-right-01.png` | `1672×941` | `2896431` | `8641ab18ea82b8c6abb4ea598ec976282ed8428b205643399092539059c0d21b` | 右后检修、架空层、尾水 |
| `context-watermill-rear-01.png` | `1672×941` | `3169198` | `808f9c0127e23d1c65840cdcd3f82cf9bfdc89ff002a4753327a88851011b0a1` | 后场、楼梯、服务路径 |
| `context-watermill-rear-left-01.png` | `1672×941` | `2829320` | `ca895c56e62d040c5e1c6a25686880b58961625cb2e2d5d2d0e6ecf576493460` | 左后支撑、作业区、回路 |
| `context-watermill-left-01.png` | `1672×941` | `3172178` | `ee6c36e3f2c991f6f04528bcd766f539a4e481b7fbc4137c53552505d49fbd35` | 左墙、坡路、引水槽支撑 |
| `context-watermill-front-left-01.png` | `1672×941` | `3154995` | `2c85903afc697879762df5b4dc1e5d0d0bf1f61b9a857ba1b253b9beccf5f625` | 左前入口、跨溪、路线分叉 |
| `detail-watermill-flume-axle-01.png` | `1536×1024` | `3352611` | `f614335dd9a24d7766787c9bac3f03fcc1319ff0ec496e5059da11ab41293097` | 引水槽、轮轴、检修台构造 |
| `detail-watermill-undercroft-tailrace-01.png` | `1536×1024` | `3532869` | `3cbcf35d62c18dc8e8f13b0ce4a3591b34b690f84ace1013ff9be1d2b1852d23` | 架空层、基础、尾水构造 |
| `material-weathered-timber-01.png` | `1254×1254` | `3017401` | `24f088f93678976ec4afe1a8c5c2ecc03068b8bcfe219d9203726ce1549580a0` | 旧木模拟 albedo 参考 |
| `material-aged-iron-01.png` | `1254×1254` | `3152921` | `f28cf5090e5244024b52af07e4b058036f2b98c56c0c3f52b81ece04fa75fa0b` | 老铁模拟 albedo 参考 |

源图总字节数：`37378407`。

## 目视复核与淘汰

- 12 张最终图均在原始分辨率检查，无可见文字、水印、人物、动物或车辆。
- 八个方向均保持单水轮约束，并提供可读的路径、溪流、挡墙、林地和近中远层次。
- 一张初始后视图因疑似双水轮直接淘汰；四张早期图因缺精确 prompt 绑定被等角色新图替换。
- 被淘汰/替换图只在私有 `rejected/` 留证，不进入 manifest、Git 或 Release。
- 两张材质图没有宣称真实采集、无缝平铺或完整 PBR 通道。

## Blender 消费建议

1. 八方向只用于提取构件清单、可行走环路、遮挡目标和相机角色；图片像素不得直接解释为
   世界坐标或同一物体的严格背面。
2. 水轮应继续保持开放环形 rim、hub、12 spokes、12 paddles，并将 flume、axle/bearing、
   maintenance deck、undercroft、tailrace 与 route support 作为独立对象/part。
3. 材质图只作为 base-color 方向；真实 PBR 仍需合法来源的 albedo/normal/roughness 等通道，
   或在合成场景中明确标为 simulated。
4. 完成消费后必须 fresh 重建 production plan / reciprocal plan / exact build，并运行 clearance、
   八个平移相机的 RGB/depth/normal/instance/semantic/camera 六层、visibility 与 post-render v2。

## Fail-closed 边界

- `synthetic=true`
- `stage=design-only`
- `camera_calibration=unknown`
- `geometry_consistency=not-verified`
- `metric_scale=unknown`
- `training_use=forbidden-as-multiview`
- `coverage_use=forbidden`
- `trust_effect=none`

本包推进的是合成 Blender 水车局部体积的设计完整度，不是“真实照片/视频已经生成真实模型和
真实纹理”。真实场景目标仍依赖真实 capture、accepted COLMAP registration、闭合的外部 GPU
训练 receipt/PLY、导入对齐、分块 Viewer 与真实画面 QA。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
