# FEEDBACK-IMAGE2-016 — Batch 12 同一视觉家族六方向参考素材包

> 日期：2026-07-21
> 生成：OpenAI 图像生成，经 Codex imagegen；每张使用同一张本地场景身份参考
> 状态：6/6 方向角色完成；干净 Release 候选通过校验

## 结论

Batch 12 不是再增加六张彼此无关的山村图，而是以 Batch 11 的下谷汇合区入选图作为
`scene-identity-and-design-language-only` 参考，生成同一视觉家族的四个水平角色和两个
垂直角色：

| role | size | bytes | SHA-256 |
|---|---:|---:|---|
| east/downstream standing-eye | `1672×941` | `3,057,076` | `073304d049960c036475b41b9135a340fe9e60d3914c9ef8812f3bb57a11cc20` |
| west/upstream standing-eye | `1672×941` | `3,078,493` | `0be5ae72dd4c6e5faf201f563543d03fbe6c8b42d9f4a850cfd7695a23f42056` |
| uphill/upper-settlement standing-eye | `1672×941` | `2,901,396` | `76e822515d8cf4fbc83b49d01982ddd2cebc88aba9ecaa23cc6b8fe6350930f6` |
| downhill/lower-valley standing-eye | `1672×941` | `3,039,008` | `019383b2158d0d913627a4adaee25306d036f02adf2d2e8e56b61172f4dc4675` |
| upward pitch: eaves/canopy/poles/wires | `941×1672` | `2,852,781` | `5b5a983d2dd6b6ba5c3c68307b30a9a4e6d650db13f005876a81b0def8afd69a` |
| downward pitch: paving/steps/drains/banks | `1086×1448` | `3,339,785` | `d60cb73863acdebafebc60ce4c2d2c54ec9108769ae10a9ca68453c95a99cb7d` |

参考输入：

```text
source_pack=synthetic-village-boundary-transition-design-pack-batch11-2026-07-21
source_slot_id=design-boundary-lower-valley-convergence-01
sha256=4110129cfd8a819784bc640dc166ad2ad7fdb1fe15ec3b5f2c5ecf3c3d3e96e8
camera_calibration=unknown
trust_effect=none
```

视觉复核确认六张图属于相近的山村建筑、石木材质、道路、溪流和植被语言，且上下方向补出了
此前水平视图容易遗漏的檐底、树冠、电杆线缆、铺地、台阶、沟渠、涵洞和溪岸细节。但建筑、
桥、道路和地形在不同图之间并不保持可机器验证的逐点一致。

## 生成与模型身份

六张图均由 Codex 内置 imagegen 直接生成并保存原始 PNG；每次调用都显式引用同一张本地
Batch 11 入选图，没有使用浏览器截图、页面缩略图或失败请求中间态。生成界面没有返回可机器
核验的具体模型 ID，因此：

```text
actual_model_id=unknown
```

参考条件只约束场景身份与设计语言，不提供相机内参、外参、镜头畸变、深度或共享 mesh。

## 干净 Release 候选

```text
tag:
  synthetic-village-design-inputs-batch12-2026-07-21
archive:
  synthetic-village-directional-reference-design-pack-batch12-2026-07-21.zip
archive_sha256:
  8b5fae794df167078e559a1dd1f2029e99b9decc4ef1cee96a370d6c1c2b77d5
archive_bytes:
  18114441
```

包内严格只有：

```text
6 images
6 prompts
manifest.json
USAGE.md
PAYLOAD-SHA256SUMS.txt
```

共 15 个文件。六张图片的尺寸、字节数和 SHA，以及六份 prompt 的字节数和 SHA 逐项复核
通过；payload checksum 12/12 通过。使用 7-Zip 关闭文件时间字段后独立构建三次，三份 ZIP
的长度和 SHA-256 逐字节一致；`7z t` 验证全部成员可读。

## Fail-closed 边界

所有记录统一保持：

```text
synthetic=true
camera_calibration=unknown
geometry_consistency=not-verified
training_use=forbidden-as-multiview
coverage_use=forbidden
panorama_projection=unknown
panorama_use=forbidden
cubemap_projection=unknown
cubemap_use=forbidden
trust_effect=none
```

这些图片不能证明：

- 六个方向来自同一个三维坐标或同一台相机；
- 建筑、桥、道路、溪流、树木或电线在视图间共享精确几何；
- 图像可组成 cubemap、equirectangular panorama 或闭合 360° 环；
- 视图具备 SfM 特征对应关系、已知尺度或 3DGS training suitability；
- 任意坐标场景、碰撞安全、可行走拓扑或真实照片重建已经完成。

## 下一步消费

1. 将六个方向角色映射到一个版本化 transition-hub recipe 的共享 world anchor；
2. 把道路、溪流、房屋、檐底、树冠、线缆、铺地、排水和涵洞拆成可替换模块；
3. 用明确的米制尺寸、拓扑、材质槽位和 instance/semantic identity 构建真实 Blender mesh；
4. 从同一共享坐标生成已知变换的水平环视、仰视和俯视相机；
5. fresh render RGB/depth/normal/instance/semantic/camera metadata，检查接缝、碰撞、遮挡和
   coverage；
6. 只有真实帧与 post-render policy 通过后，Viewer/Studio 才能把该锚点标记为已建模、已受测。

本包缩短的是“一个场景周围该建什么”的设计缺口，不缩短相机标定、几何一致性和真实重建
证据链。最终可漫游场景仍必须以版本化 3D recipe 或真实照片重建产物为事实源。
