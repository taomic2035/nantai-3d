# FEEDBACK-IMAGE2-015 — Batch 11 跨分块连续过渡素材包

> 日期：2026-07-21
> 生成：OpenAI 图像生成；5 张经 Codex imagegen，1 张经已登录 ChatGPT 内部浏览器
> 状态：6/6 角色完成；干净 Release 候选通过校验

## 结论

Batch 8–10 分别补充了前后互逆、侧向路线和垂直包络；Batch 11 转向无限世界真正缺失的
跨 chunk transition module 设计输入：

| role | size | bytes | SHA-256 |
|---|---:|---:|---|
| east-west contour road, junction and drainage | `1536×1024` | `2,810,878` | `82a09de2af6a587e59db77760bc9898b66112b562f81dad7b571450c2a79611b` |
| north-south switchback trail and road junction | `1536×1024` | `3,667,337` | `03cf56f4b42caea43e5145eebfd0f5a54f1758c4c238d710068c8fbf0b5cf1ae` |
| stream corridor, bank paths, bridge and culvert | `1536×1024` | `3,635,954` | `1475aca0b35bace6a05ec03cb8efbbebbc4ffd25f6b99bc2625edf7defd9bc12` |
| terrace, irrigation and contour-path transition | `1536×1024` | `3,847,203` | `6b9b797094f140411062be8a2323c7226fb2561e4e25918bf0d3752127a171af` |
| forest, bamboo, orchard and village edge | `1536×1024` | `3,484,769` | `a31abe147e5bc71e004487848954510bfa37e37044b14b1eb614a1e07b347229` |
| lower-valley road/trail/stream/settlement convergence | `1672×941` | `3,055,365` | `4110129cfd8a819784bc640dc166ad2ad7fdb1fe15ec3b5f2c5ecf3c3d3e96e8` |

六张图覆盖道路、步道、溪流、梯田、林果边缘和下谷汇合区，并同时提供前景结构细节、
中景可行走连接和远景地形层次。素材是通用角色设计，不绑定某一个具体村庄或固定坐标，
可以按槽位替换为后续真实素材。

人工视觉复核发现道路图在中央建筑处形成分叉。它适合作为 junction、排水和街景密度参考，
但不能反推 canonical 主路应当分叉；这一限制已写入 manifest 与 USAGE。

## 生成与服务状态

前五张通过 Codex 内置图像生成能力直接取得原始 `1536×1024` PNG。第六张在同一端点连续
两次遇到 network error；失败请求没有留下空文件或候选记录。随后沿用用户已授权且登录中的
ChatGPT 内部浏览器，使用“生成图片”模式取得页面提供的原始 `1672×941` PNG，不是截图或
缩略图。

两个界面都没有提供可机器核验的具体模型 ID，因此：

```text
actual_model_id=unknown
```

## 干净 Release 候选

```text
tag:
  synthetic-village-design-inputs-batch11-2026-07-21
archive:
  synthetic-village-boundary-transition-design-pack-batch11-2026-07-21.zip
archive_sha256:
  7796df6549b46d525e698a8abfa9708d449ab718153645f458100995247095a4
archive_bytes:
  19642368
```

包内严格只有：

```text
6 images
6 prompts
manifest.json
USAGE.md
PAYLOAD-SHA256SUMS.txt
```

共 15 个文件。manifest 对 6 张图片的尺寸、字节数和 SHA，以及 6 份 prompt 的字节数
和 SHA 逐项复核通过；payload checksum 12/12 通过。使用 7-Zip 关闭文件时间字段后独立
构建两次，两份 ZIP 的 SHA-256 逐字节一致；`7z t` 检查全部成员可读。

## 与分块连续性修复的关系

同一轮先修复了 `MockLayoutGenerator` 的真实边界缺陷：

- 主路相邻边界原先错位 `2m` 或 `8m`；
- 南北步道的数量和边界 x 原先各 chunk 独立随机；
- 溪流原先可在东西 chunk 边界凭空出现或终止。

修复后的道路、步道和溪流使用共享 world-edge identity，负坐标与多 world seed 的 21 个
边界契约通过；既有布局/按需渲染/mesh chunk 的 75 个回归通过。详细证据见
`docs/verification/2026-07-21-infinite-chunk-boundary-continuity.md`。

Batch 11 只为这些共享锚点之上的 transition module 提供视觉设计参考。它不参与边界坐标
计算，也不能覆盖几何 validator。

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
trust_effect=none
```

这些图片不能证明：

- 两张图片共享同一条道路、溪流或地形；
- 图中道路宽度、坡度、水流方向、桥涵或挡墙具有米制精度；
- chunk 之间的 terrain、normal、UV、碰撞或植被 exclusion 已连续；
- 360° coverage、任意坐标几何或真实照片重建已经完成。

## 下一步消费

1. 新增版本化 `BoundaryModulePlan`，显式绑定共享边界锚点、素材 SHA 和 recipe version；
2. 将道路/步道/溪流/梯田/森林/聚落 transition recipe 作为可替换模块消费；
3. 在 Blender 中验证 terrain 接缝、mesh 宽度、UV、碰撞、净空和 exclusion zone；
4. 使用跨边界 standing-eye cameras 生成 fresh RGB/depth/normal/instance/semantic/
   camera metadata；
5. 由 post-render v2 policy 从真实 layer bytes 复算，并在 Viewer/Studio 中明确显示
   transition module 与证据身份。

即使以上全部通过，结论仍只限于合成 world recipe 中已建模、已受测的 chunk 过渡，不提升为
真实照片重建、metric、training-suitable 或无限真实几何。
