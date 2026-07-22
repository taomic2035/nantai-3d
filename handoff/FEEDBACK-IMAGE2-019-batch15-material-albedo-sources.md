# FEEDBACK-IMAGE2-019 — Batch 15 合成材质 albedo 源

> 日期：2026-07-22
> 生成：OpenAI built-in image generation，经 Codex imagegen
> 状态：`12/12` 原始来源 + `2` 个定向质量变体完成；私有候选；未注册、未进入 Release

## 结论

Batch 6–14 已有 82 张整景、方向、边界、垂直包络、斜向路线和模块板素材。
Batch 15 不再堆相似整景，改为补 Blender 近景和 360° 转向时反复出现的独立、
可替换表面来源。首组覆盖石墙、灰瓦、旧木、白灰墙、湿石路、浅溪床；第二组
补齐夯土墙、土路、梯田土、老竹材、果树叶冠和旧锻铁。

十二张原始来源均已生成并保存在私有候选区：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch15/
```

| slot | size | bytes | SHA-256 |
|---|---:|---:|---|
| `material-albedo-fieldstone-masonry-01` | `1254×1254` | `3,510,962` | `4bc32e67581aa7950ebc7034904b0e3c9aaeaf52c97b3a6294ffc0c31aa34da8` |
| `material-albedo-gray-clay-roof-tile-01` | `1254×1254` | `3,327,781` | `beb277389cdcceccb1b1084ba0c1e9e396954ae8cf02b4f44a609f7c04b82c05` |
| `material-albedo-weathered-dark-timber-01` | `1254×1254` | `2,306,675` | `ccbaa3ea18151be25ed4b06d7b2fbdd34e0ec5dcfb1cfde2ac6d05884d99ec55` |
| `material-albedo-aged-lime-plaster-01` | `1254×1254` | `3,327,306` | `d50f9139607b0d505f76c67d16e342cb4250938400f3d8898743da3a44a7f926` |
| `material-albedo-wet-stone-paving-01` | `1254×1254` | `3,024,258` | `0177bf8b8495f3fd18a34f37ee913bcbc8a5bee8503b59e0de7bb8dc782b547e` |
| `material-albedo-shallow-creek-bed-01` | `1254×1254` | `3,389,813` | `3eab5333c251bc165ed36d32855e6f06b338d70e30c2593fa04b1f2fc5f6dcad` |
| `material-albedo-rammed-earth-01` | `1254×1254` | `3,517,166` | `eed18f74643d260c5ddec8f40aaf58df664f3dc1cacd19d6d11729141d83a1d7` |
| `material-albedo-packed-earth-path-01` | `1254×1254` | `3,638,479` | `218c338a6154e094c8353374125d02737c16b59966645cf401b3db576e24d464` |
| `material-albedo-terrace-soil-01` | `1254×1254` | `3,604,474` | `f250dbc088c8e07fcad646405b936486dc4147d25184d156a5c6f8951235382d` |
| `material-albedo-aged-bamboo-culm-01` | `1254×1254` | `2,602,865` | `aace3f14b814f07de94c51702f511dbffccbde3b5a234f0b98aea5432adbac4f` |
| `material-albedo-orchard-leaf-canopy-01` | `1254×1254` | `3,077,023` | `f79c30a9e5340f03781c346c5f7547b0fb1df84672b4975abc11383af245e1e5` |
| `material-albedo-aged-forged-iron-01` | `1254×1254` | `3,245,034` | `ff09d8518e926f6e8fd2cc24c9db0b2fb5dcb6c7a59a33b85e7f874c56d50e06` |

所有原图均为 RGB PNG；十二份精确提示词、队列和候选 manifest 与原图同目录。
原始设计来源总数由 82 增至 94；另有一张未通过的旧木 seamfix 变体，保留在
私有候选区供审计，不计入 94 张原始来源。

## 视觉适用性

- 旧木：正交窄板、暗棕但仍保留木纹，没有门窗、五金和边框；
- 灰泥：满幅低对比暖白灰泥，细微抹痕，没有建筑特征；
- 湿石路：正交俯视不规则铺石，尺度均匀，没有路缘、排水口或积水焦点；
- 浅溪床：正交俯视的卵石与细砾，分布均匀，没有河岸、鱼、植物或天空反射；
- 夯土墙：水平夯筑层与细骨料可读，没有墙角、洞口和建筑轮廓；
- 土路与梯田土：分别保持压实细砾和松散土团差异，没有路缘、田埂或种植行；
- 老竹材：扁平展开的纵向纤维和少量节带，没有圆柱高光或竹林场景；
- 果树叶冠：满幅温带小叶，无天空、果实、树干或建筑；
- 旧锻铁：暗灰锻造颗粒与克制锈斑，无零件轮廓、铆钉或高光；
- 已有石墙和灰瓦仍满足“单一表面来源”构图要求。

这些特征让素材可在不同墙面、屋面、地面与水体节点复用，不绑定某一栋建筑或
单一镜头。但“可复用”不等于共享真实几何，也不提供任何 360° 相机覆盖证据。

## 接缝审计

提示词要求 seamless 不能成为机器证据。统一使用 19px 对侧条带、逐像素归一化
RGB mean absolute error 重算：

| slot | left/right MAE | top/bottom MAE | verdict |
|---|---:|---:|---|
| fieldstone | `0.108546` | `0.140949` | not seamless |
| gray roof tile | `0.095134` | `0.144136` | not seamless |
| dark timber | `0.048064` | `0.028864` | not seamless |
| lime plaster | `0.049134` | `0.051645` | not seamless |
| wet stone paving | `0.051281` | `0.041395` | not seamless |
| shallow creek bed | `0.095465` | `0.097305` | not seamless |
| rammed earth | `0.074526` | `0.078805` | not seamless |
| packed earth path | `0.084264` | `0.089470` | not seamless |
| terrace soil | `0.096337` | `0.101410` | not seamless |
| aged bamboo culm | `0.036317` | `0.031125` | not seamless |
| orchard leaf canopy | `0.101932` | `0.091062` | not seamless |
| aged forged iron | `0.054905` | `0.084034` | not seamless |

旧木 reference-edit canary 保留中心外观并略微降低误差：

| variant | SHA-256 | left/right | top/bottom | verdict |
|---|---|---:|---:|---|
| `material-albedo-weathered-dark-timber-01-seamfix-v1` | `667687aadb85f18600ddc41144a334d92077a58639bdfe3268c937b3ecd745b5` | `0.045088` | `0.027532` | rejected; still not verified seamless |

这证明单次 imagegen reference edit 不能保证对侧像素连续，因此没有继续浪费五次
同类请求，也没有把“看起来接近”写成通过。仓库已有 H3 确定性 quilting、对侧边缘
强制连续和 PBR 派生工具链；后续应把合格来源接到该机器验证链，而不是靠提示词或
主观目测授予 seamless。

## 定向质量变体（2026-07-22 追加）

逐张视觉审查发现：原灰瓦带明显局部烘焙明暗，原溪床有水下模糊/湿润观感。为降低
Blender 二次打光风险，使用 built-in image generation 生成两个独立 v2 候选；没有
覆盖原文件，也没有把变体选入生产合同：

| variant | bytes | SHA-256 | left/right | top/bottom | status |
|---|---:|---|---:|---:|---|
| `material-albedo-gray-clay-roof-tile-01-flatlight-v2.png` | `3,092,888` | `c6a29d6ada000661a5f4656c5dfc32021e5d611ebcd1c01eb57c2139fe7d5286` | `0.114964` | `0.067390` | design-only；仍有瓦片曲面局部明暗 |
| `material-albedo-shallow-creek-bed-01-dry-v2.png` | `3,678,841` | `1dddd1f0b956471daddbba4a8b7fb0b4505929ef225c61bf9dd55a429d033a4a` | `0.128768` | `0.120457` | 视觉优选；待 deterministic authoring |

两张均为 `1254×1254 RGB PNG`。灰瓦 v2 的上下边误差明显下降，但左右边仍未通过；
干溪床 v2 消除了原图的水感，却没有获得无缝证据。精确提示词、队列、SHA 和视觉
判定均已写回 Batch 15 私有候选目录。二者继续保持 `trust_effect=none`。

## 信任边界

十二张来源全部保持：

```text
synthetic=true
metric_texel_scale=unknown
seamless_edges=not-verified
color_space=unknown-unprofiled-png
pbr_map_consistency=not-generated
texture_use=albedo-source-only-not-registered
real_photo_textures=false
trust_effect=none
```

它们不能直接进入 material registry。没有对应的 measured roughness、normal、height
或 displacement，也不是南台村真实墙体、屋瓦或溪床照片。

## 后续消费顺序

1. 为 Batch 15 建立内容寻址 source-pack receipt，并显式记录 private-project-use；
2. 复用或扩展 H3 `sha-quilt-seam-pbr-v1`，生成确定性 4096 authored master；
3. 要求对侧像素严格一致、source SSIM 和 mean RGB drift 全部通过；
4. 生成并标注 heuristic normal/ORM，绝不称为物理测量；
5. 通过 KTX2 编译、解码质量与完整 mip 门；
6. 在 fresh Blender build 中跑近、中、远和斜视角 RGB/normal 审计；
7. 只有上述证据齐全后才进入一个干净 Release，不发布原图、失败 seamfix 或其它中间态。

本批推进的是 360° 漫游所需的“多表面可替换纹理来源”，不是由图片生成真实几何
本身。真实模型仍需要真实照片/视频、SfM 位姿、外部 GPU 重建及后处理证据。
