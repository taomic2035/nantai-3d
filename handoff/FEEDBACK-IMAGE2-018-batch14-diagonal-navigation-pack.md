# FEEDBACK-IMAGE2-018 — Batch 14 斜向路线与平移检查点素材包

> 日期：2026-07-21  
> 生成：OpenAI 图像生成，经已登录的 ChatGPT 内置浏览器；六次独立 text-only 请求  
> 状态：6/6 完成；干净 Release 候选通过校验

## 结论

Batch 14 补充了现有素材中最缺的斜向观察、后场反面、林果边界和前后平移遮挡补偿。
每张图都保持人眼高度、开放前景、至少八栋分层住宅和多条可继续行走的路线，并特别
强调挡墙背面、屋檐/阳台底面、基础支撑、排水、桥台、溪岸和隐藏侧巷。

六张图是独立的可替换设计参考，不是同一场景的标定多视图。它们提高 Blender 建模时
的隐藏结构与路线选择可读性，但不能直接用于 SfM、NeRF 或 3DGS 训练，也不能证明
360° 覆盖或任意坐标已有几何。

## 选中素材

| slot | size | bytes | SHA-256 |
|---|---:|---:|---|
| `design-navigation-diagonal-uphill-left-01` | `1536×1024` | `3,401,356` | `b2da54550f0fad97e880c7ad8bd0950565ab58f7f0b8e7b5c3fcb51bb8e04b54` |
| `design-navigation-diagonal-downhill-right-01` | `1536×1024` | `3,340,400` | `288f5485b0a3f13ac549a74d50e1e67a8d6ce6e814ea5229690c6d9f69571eaa` |
| `design-navigation-diagonal-rear-service-01` | `1536×1024` | `3,314,233` | `91bbc9ef9da03db7ea1e4c99d19b85a318c5db735eca71ec3e839cd76fa97436` |
| `design-navigation-diagonal-forest-orchard-edge-01` | `1536×1024` | `3,337,650` | `cb498b9121437dce2db01178b27e66f36d12b59b2dcf0077f92806704192c8ad` |
| `design-navigation-forward-translated-checkpoint-01` | `1536×1024` | `3,233,908` | `77e856aba00ddc3bc5d2eb1d1437ed739072d30546cc24df84c8f28ded65ed2e` |
| `design-navigation-backward-translated-checkpoint-01` | `1536×1024` | `3,356,117` | `f4fde026ffa40737b5ffd46891d20971c835ea41715ef598e49d84c797e9a9a9` |

逐张原图人工复核确认：

- 上坡左斜视图包含挡墙内角、排水盖板、楼梯/坡道和两条后续路线；
- 下坡右斜视图包含真实切入地形的溪床、斜看石桥、桥台、水车支撑和溪边路线；
- 后场图包含石基、木柱、后门、柴棚、菜地、排水沟和上下坡侧巷；
- 林果边界图包含竹林、果园、沟谷小桥、三向岔路和多层村庄背景；
- 前移检查点把近挡墙、中层住宅、溪桥和远梯田形成明显视差；
- 后移检查点暴露反向立面、阳台底面、排水出口、溪边和两侧路线口。

## 干净 Release 候选

```text
tag:
  synthetic-village-design-inputs-batch14-2026-07-21
archive:
  synthetic-village-diagonal-navigation-design-pack-batch14-2026-07-21.zip
archive_sha256:
  1470096e9f33cccd94c43be3bab8aa1e4592c4d305d835d3d112a4bc5150be27
archive_bytes:
  19186801
manifest_sha256:
  4ae4c0187c6c960ddf04639598d62cb2a6430311962d87e2b24f6606eadbf738
```

包内严格只有：

```text
6 images
6 prompts
manifest.json
USAGE.md
PAYLOAD-SHA256SUMS.txt
```

共 15 个文件。manifest 的 12 个 payload 逐项重算通过，checksum 12/12 通过；关闭
ZIP 文件时间字段后独立构建三次，三份长度与 SHA-256 一致；`7z t` 验证全部成员可读。
浏览器缓存、缩略图、失败请求、重试和候选中间态均未进入 ZIP。

## Fail-closed 边界

```text
synthetic=true
camera_calibration=unknown
camera_translation=prompt-intent-only-not-measured
geometry_consistency=not-verified
multiview_correspondence=not-verified
metric_scale=unknown
training_use=forbidden-as-multiview
coverage_use=forbidden
panorama_use=forbidden
texture_use=design-reference-only-not-seamless-texture
trust_effect=none
```

尤其是“向前/向后约 8 米”只属于提示词的构图意图，没有实测相机位姿、基线、内参或
像素对应。六张图不能拼成伪多视图数据集，也不能裁切成已注册 PBR 纹理。

## 下一步消费

1. Opus/GLM lane 将六个角色拆成版本化 route/enclosure module，坐标、尺寸、连接锚点、
   topology ref 和 collision 必须进入 canonical plan；
2. Blender runtime 构建实际坡道、台阶、挡墙、桥台、溪床、支撑和排水，不使用分散盒体
   代替路线合同；
3. 为斜向、前移、后移位置登记真实 standing-eye camera，保存完整 c2w 与 intrinsics；
4. 从同一 `.blend` 生成 RGB/depth/normal/instance/semantic/camera 六层；
5. 只有实测 topology/collision、artifact SHA、journal 与 post-render report 才能说明
   受测位置可漫游。真实照片纹理和任意坐标世界仍需独立重建/生成链路。

