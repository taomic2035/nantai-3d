# FEEDBACK-IMAGE2-017 — Batch 13 模块化资产建模参考板

> 日期：2026-07-21
> 生成：OpenAI 图像生成，经 Codex imagegen；每张使用同一张本地场景语言参考
> 状态：6/6 模块类别完成；干净 Release 候选通过校验

## 结论

Batch 13 将前几批的整景、方向和包络参考进一步拆成 Blender 更容易消费的隔离模块板。
六张图不是六个场景，而是六类通用资产库：

| module board | size | bytes | SHA-256 |
|---|---:|---:|---|
| dwellings / roofs / walls / foundations / drainage | `1536×1024` | `2,686,814` | `816be16cc32d734e7fb3be118daeb9bf3a54cd234ffb183bfff962f15acb3a4f` |
| bridges / culverts / retaining walls / drainage | `1536×1024` | `3,058,306` | `d3cf4627dc8ab5a1780aabeec9f9d09c407408cdfad368b73c881efeda51497d` |
| paths / stairs / ramps / galleries / elevated access | `1536×1024` | `2,836,239` | `7e23cc6b0c3fee3f5db740e2142133898c033d6b0024d5e11f47d9f832de2c21` |
| terraces / irrigation / gates / channels / water power | `1536×1024` | `3,037,318` | `ba4f006d0e1921b7b536326edac281794c55c8c0f4f7e2ba518eae4000566b93` |
| vegetation / rocks / fences / terrain boundaries | `1536×1024` | `3,135,500` | `0f979fa9973ab77e222c90a40c5b721f0e6ac493d137fa9939cda146fb977fe0` |
| utilities / wells / storage / carts / tools / props | `1536×1024` | `2,530,865` | `4e6ce9e2fc7a24b31a4677044e0ad682cdab16c15f5dd04dbc1391eccfb3b9ca` |

每张图均包含大量隔离部件和结构细节。住宅板给出房体、屋面、框架、墙片、门窗、基础和落水；
桥涵板给出桥拱、木桥、桥台、挡墙转角、涵洞和排水；通行板给出路线片段、坡道、廊道、架空
平台、支撑和护栏；水系统板给出梯田、分水、闸门、渠道、水车轴承和尾水；植被板给出树木、
竹林、果树、岩石、围栏和岸坡；道具板给出电杆线缆、灯、井、储水、推车、棚架和工具。

人工视觉复核确认六张图没有可读文字、水印或人物，隔离布局适合建模拆解。但部分“多视图”
是模型生成的设计变体，并非同一物体经固定相机旋转得到的逐点一致 turntable。

## 参考输入与生成身份

```text
source_pack=synthetic-village-directional-reference-design-pack-batch12-2026-07-21
source_slot_id=design-direction-east-downstream-01
sha256=073304d049960c036475b41b9135a340fe9e60d3914c9ef8812f3bb57a11cc20
reference_role=palette-material-climate-and-design-language-only
camera_calibration=unknown
trust_effect=none
```

六张图均由 Codex 内置 imagegen 直接返回并保存原始 PNG；没有使用页面截图、缩略图或失败
中间态。生成接口没有返回可机器核验的具体模型 ID：

```text
actual_model_id=unknown
```

## 干净 Release 候选

```text
tag:
  synthetic-village-design-inputs-batch13-2026-07-21
archive:
  synthetic-village-modular-asset-reference-pack-batch13-2026-07-21.zip
archive_sha256:
  97e1f9d84ef0b42b8294c49cc74e27a2b9b3e4e5566cc8be29687ac19bf1a7f4
archive_bytes:
  16332806
```

包内严格只有：

```text
6 images
6 prompts
manifest.json
USAGE.md
PAYLOAD-SHA256SUMS.txt
```

共 15 个文件。图片尺寸、字节数和 SHA，以及 prompt 的字节数和 SHA 逐项复核通过；payload
checksum 12/12 通过。关闭 ZIP 文件时间字段后独立构建三次，三份 archive 的长度和 SHA-256
逐字节一致；`7z t` 验证全部成员可读。

## Fail-closed 边界

所有记录统一保持：

```text
synthetic=true
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
orthographic_projection=not-verified
watertight_topology=not-verified
texture_use=design-reference-only-not-seamless-texture
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

这些模块板不能证明：

- 画面中的正面、背面、侧面、顶面或剖面属于同一个精确 mesh；
- 部件具有米制尺寸、统一比例、无穿插拓扑或工程可行性；
- 桥拱、涵洞、挡墙、水车、廊道、电杆和基础满足结构或安全要求；
- 水路连通、步道可行走、护栏净高、碰撞体或 vegetation exclusion 已验证；
- 画面材质可作为无缝 albedo/roughness/normal/displacement 图；
- 360° coverage、任意坐标场景或真实照片重建已完成。

## 下一步消费

1. 为每类板建立版本化 kit/part/variant ID 与可替换 source SHA；
2. 显式声明米制尺寸、connection anchor、材料槽位、LOD、碰撞和可行走/水路语义；
3. 在 Blender 中按模块单独建模，再由 transition-hub/world recipe 组合，不能按像素反推尺寸；
4. PBR maps 从独立真实或显式 synthetic material source 制作，不裁切本板冒充纹理；
5. 为组合场景生成已知相机的 RGB/depth/normal/instance/semantic/camera metadata；
6. 由 collision/topology、artifact SHA、production journal 和 post-render policy 决定是否可用。

本批显著提高“可建模元素数量”和隐藏结构可读性，但不会把二维设计自动提升为真实三维资产。
