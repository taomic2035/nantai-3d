# FEEDBACK-IMAGE2-014 — Batch 10 垂直包络与近景遮挡素材包

> 日期：2026-07-21
> 生成：OpenAI image2，使用已登录 ChatGPT 内部浏览器
> 状态：6/6 角色完成；干净 Release 候选通过校验

## 结论

Batch 8 已补前后互逆路线，Batch 9 已补 90°/120° 侧向路线；Batch 10 继续补齐
平面方向参考仍无法表达的垂直包络、结构底面、基础接触、排水和近景遮挡：

| role | size | bytes | SHA-256 |
|---|---:|---:|---|
| central courtyard covered side passage and eave underside | `1672×941` | `3,021,627` | `569cfb0d214ce7d7f9a2446f4b77ebf5e8a01dff8f33909042b5b40f4bdbda79` |
| bridge arch soffit, abutment and creek-bank route | `1672×941` | `3,313,460` | `643bef8af95416650f72ee24ffe8967a487904f8ee82ba82f7287dc40b471abf` |
| watermill axle-end platform, bracing and creek exit | `1448×1086` | `3,179,590` | `54c09ca90263d8b170bf6796c9bcfb71be1d4df7c24e43d77b1d77bb609e0323` |
| covered-gallery floor structure, headroom and route reconnection | `1672×941` | `2,805,544` | `62c320d637ebd9a2324f73faeab51c8d35a3f5a02542edcd9085343e094a2e5f` |
| forest/orchard retaining wall, drain and canopy contact | `1672×941` | `3,416,369` | `75371e3e317423646f7bef8b4108f2ae9f55768eb0653fe840b3e47dfe84a127` |
| lower-valley foundations, outlets, culverts and creek route | `1672×941` | `3,052,043` | `15411c5aed8893319be53f4fced66a628230e2cf72d6d7ef7dce9eaa2b7ec0cc` |

这六张图均通过人工视觉检查，所要求的主角色可读：

- 院落图能看到檐底、柱梁、明沟跨越、石阶/坡道和多个出口；
- 桥区图使用单个主拱，拱腹、券石、桥台、泄水孔、溪岸路线和远端水车可读；
- 水车图能看到轮体背侧、轴端、平台、支撑、护栏、台阶、建筑外壳和溪岸出口；
- 廊下图能看到楼板梁、斜撑、柱、基础、上层栏杆、下层净空和侧向楼梯；
- 林果图能看到挡墙面/墙趾、排水跨板、果树地面接触、竹林边界和路线分叉；
- 下谷图能看到挑出建筑基础、涵洞/排水口、溪岸步道、桥/水车和上坡回接。

## 生成与服务状态

内置 reference-conditioned image2 编辑端点对三张并行请求统一返回 network error。
没有切换到较低模型，也没有使用需要 API key 的 CLI。随后沿用用户已授权的、登录中的
ChatGPT 内部浏览器路径逐张生成。

第一、二张 PNG 通过浏览器 page-assets 取得；其余四张通过图片全屏页的原始 media
download 取得。所有文件均为页面提供的 PNG，不是截图、缩略图或二次视觉拼接。
网页没有提供可机器核验的模型 ID，因此：

```text
actual_model_id=unknown
```

## 干净 Release 候选

```text
tag:
  synthetic-village-design-inputs-batch10-2026-07-21
archive:
  synthetic-village-vertical-enclosure-design-pack-batch10-2026-07-21.zip
archive_sha256:
  affe92b238f442b765f495b75cb80c612ead193781f265f340c85fb141722fbf
archive_bytes:
  18658156
```

包内严格只有：

```text
6 images
6 prompts
manifest.json
USAGE.md
PAYLOAD-SHA256SUMS.txt
```

共 15 个文件。manifest 对 6 张图片的尺寸、字节数和 SHA，以及 6 份提示词的字节数
和 SHA 逐项复核通过；payload checksum 12/12 通过。使用 7-Zip 关闭文件时间字段后
独立构建两次，两份 ZIP 的 SHA-256 逐字节一致；`7z t` 检查 15 个归档成员全部可读。

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

Batch 10 使 Blender recipe 拥有更多结构底面、头顶、墙趾、基础和排水设计信息，但
不能证明六张图片共享同一几何，更不能直接支持 SfM、3DGS、360° coverage 或任意
坐标漫游。图像中的桥拱砌体、水车轴承、水流、排水和基础接触只能作为设计建议；
canonical topology 必须由版本化 recipe 明示。

## 下一步消费

1. 将六个角色作为 additive visual-source bindings 绑定到下一版环境模块 plan；
2. 在 Blender 中分别实现檐底/廊下净空、桥拱/桥台、轴端平台、挡墙/排水和基础接触；
3. route、headroom、collision、drainage 和 support 由几何 validator 实测，不从像素推断；
4. 使用 standing-eye production cameras 生成 fresh RGB/depth/normal/instance/semantic/
   camera metadata；
5. 由 post-render v2 policy 从真实 layer bytes 复算，再由 Studio 显示证据。

即使上述全部通过，结论仍只限于合成场景中已建模且已受测的路线与包络，不提升为真实
照片重建、metric、training-suitable 或无限真实几何。
