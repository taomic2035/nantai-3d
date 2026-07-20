# FEEDBACK-IMAGE2-013 — Batch 9 侧向路线与隐藏结构素材包

> 日期：2026-07-20
> 生成：OpenAI image2，使用已登录 ChatGPT 内部浏览器
> 状态：6/6 角色完成；干净 Release 候选通过校验

## 结果

Batch 9 生成并筛选了六张互补路线素材：

| role | size | SHA-256 |
|---|---:|---|
| central courtyard cross-slope lateral route | `1536×1024` | `cd11d944f457c5dfb3415657eb85e38c0033c7c6e0d284771ede9f51d5d11cd8` |
| bridge downstream-bank three-quarter route | `1672×941` | `f0e9c029b06dfa9832d44ca0ff4fbde186d84e1ef6a3adfcc6a994a09d1e97be` |
| watermill opposite-bank upstream service side | `1448×1086` | `77137860a0b2f98d35747bde61a3852bcf10882343235a5c0faeb5d85f619f83` |
| covered-gallery lower-lane reciprocal underpass | `1537×1023` | `a5f935bbdd2b6609aef40b92c0c8e57e746257274e2c21c81990835715df2ec0` |
| forest/orchard lateral three-way route fork | `1672×941` | `afd44bbdb965be7a3f6a478cd9c2509aead86c1204389c992a5d7fbdcb9ed80e` |
| lower-valley field-edge lateral route junction | `1672×941` | `788eb01187c13ca02807a20cee42720b1970100d9c714d1bd647c82dc353dd7b` |

内置 image edit endpoint 首次请求返回 network error；没有降级模型，也没有使用需要
API key 的 CLI。随后在用户已授权的已登录 ChatGPT 内部浏览器中使用 image2，
并通过浏览器 page-assets 接口取得原始 PNG，而非截图或缩略图。

## Release

```text
tag:
  synthetic-village-design-inputs-batch9-2026-07-20
archive:
  synthetic-village-lateral-route-design-pack-batch9-2026-07-20.zip
archive_sha256:
  6f7cc48e40e3d323a98e5ca91633cb6a6a7f623d7544efe44317102b3e5648f8
archive_bytes:
  19344169
release_manifest_sha256:
  bf5e2a5c6907baf5acefa5c6cf7d85bf9cfe611b47013f5bb1b564eca3064339
```

Release URL：

```text
https://github.com/taomic2035/nantai-3d/releases/tag/
synthetic-village-design-inputs-batch9-2026-07-20
```

包内严格只有：

```text
6 images
6 prompts
manifest.json
USAGE.md
PAYLOAD-SHA256SUMS.txt
```

共 15 个文件。两个独立 7-Zip 构建逐字节一致，`7z t` 通过。候选、浏览器缓存、
失败重试和私有审计文件均未进入 ZIP。

## 视觉筛选

- 中央院落：横向巷、坡道、楼梯、排水槽和院落出口可读；
- 桥区：主桥拱、桥面厚度、桥台、岸侧路线和水车关系可读；
- 水车：建筑壳体、轮轴、平台、台阶、尾水和检修入口最完整；
- 廊下：下层路线、上层廊道、结构柱梁与侧向楼梯可读；
- 林果边界：三岔路、果园排水、林缘和村庄联系可读；
- 下谷：田边路、排水、建筑后场、桥/水车和上行路线可读。

桥侧图存在一个小型次级泄水孔，已在 manifest 标为显式限制；它不能决定
canonical bridge topology。

## Fail-closed 边界

所有记录均保持：

```text
synthetic=true
replaceable=true
camera_calibration=unknown
geometry_consistency=not-verified
training_use=forbidden-as-multiview
coverage_use=forbidden
panorama_use=forbidden
trust_effect=none
```

Batch 8 + Batch 9 只能让 Blender recipe 拥有更多正/反/侧向设计信息。它们不能证明
十二张图片共享同一几何，也不能直接支持 SfM、3DGS、360° 覆盖或任意坐标漫游。
进入 Viewer 前仍需版本化模块建模、walkable topology、collision、standing-eye
production cameras、fresh Blender preflight、六层渲染与 post-render v2 policy。

相关生产化交办：

```text
handoff/HANDOFF-OPUS-009-batch8-reciprocal-route-productionization.md
```
