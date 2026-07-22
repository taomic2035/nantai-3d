# FEEDBACK-IMAGE2-021 — Batch 17 遮挡面与远边界补全参考

> 日期：2026-07-22
> 生成：Codex 使用 OpenAI built-in imagegen
> 状态：`8/8` 已生成、目视复核、逐字节登记
> 位置：`.nantai-studio/synthetic-village/hybrid-v4-candidates/batch17/`

## 1. 结论

Batch 17 补的是自由 360° 漫游最容易暴露的“转身后”缺口，而不是再生成一批正面
风景照：建筑后立面、屋顶背坡、院落背角、廊下/地窖、桥涵内侧、果园挡墙背面、
道路挖填方和村庄—森林远边界。

八张图属于同一通用山村视觉家族，统一使用中性阴天、近中远三层和连续路线，但每张
仍是独立 imagegen 输出。它们可作为 Blender 模块拆分、构造收口、材质家族和世界边界
设计参考；**不能**互相声称是同一空间的标定多视图。

## 2. 素材身份

| file | size | bytes | SHA-256 | 建模用途 |
|---|---:|---:|---|---|
| `design-occlusion-rear-service-alley-01.png` | `1536×1024` | `3352498` | `6f2c88a9882e962fd077963b526937d6d1ba06df065abb3c42d1b7dbf06e6e30` | 后立面、服务巷、排水、道具和双向路线 |
| `design-occlusion-uphill-roof-backs-01.png` | `1536×1024` | `4024444` | `a5455c0f2a33f48e953d96fcc44527619cdc476126eafa6bee041ce726b5acd4` | 屋脊、背坡、谷沟、檐口和层叠天际线 |
| `design-occlusion-courtyard-rear-corners-01.png` | `1536×1024` | `3636908` | `6b08098729796916c33e86f1532ee1659876ff980f31d6f2457319f9fdf3c146` | 院落背角、墙体回接、楼梯、廊边和多出口 |
| `design-occlusion-gallery-undercroft-cellar-01.png` | `1535×1024` | `2969448` | `65c7f4b54ce58d57982e1742def457149ffd5c7aa35c064df26dd01907cdf4c3` | 梁底、柱脚、支撑、地窖门槛和可读暗部 |
| `design-occlusion-bridge-culvert-inner-bank-01.png` | `1536×1024` | `4158692` | `776c4a4220d1545cc44200253282ff32babe79dd6c3218ac4e5675e3fb866cef` | 桥底、桥台回接、河床、涵洞和对岸路线 |
| `design-occlusion-orchard-retaining-backs-01.png` | `1536×1024` | `3917723` | `a4ee472a3e6aa39153dc0aa56d81bd0c1dc4da551c7282ca7459148c07907787` | 挡墙背面、检修道、灌溉和果园分层 |
| `design-occlusion-road-cut-fill-hairpin-01.png` | `1536×1024` | `3733152` | `feeedfb67f591153b88af38eccbc5c5aff22f833d42decf352b04cb3191c57bc` | 挖方坡、填方墙、边沟、捷径梯和双向出口 |
| `design-occlusion-village-forest-boundary-01.png` | `1535×1024` | `3217192` | `7dd7f7a5d20d716573bbceafaeafad673b8868e2a42a4c8d847f8ff237a610f1` | 建筑—果园—竹林—森林—远山连续边界 |

源图总字节数：`29010057`。

私有 QA contact sheet：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch17/
  contact-sheet-batch17.png
```

| bytes | size | SHA-256 |
|---:|---:|---|
| `1719784` | `1536×572` | `e580f406becbbb3ae04ed691190836fbfce7896e14861ac6f28ccdb077601e06` |

contact sheet 只用于视觉筛选，不是 source candidate。

精确 prompt、prompt SHA、逐图尺寸/模式和 generation queue 已保存在同一私有目录：

```text
candidate-sources-batch17.json
generation-queue-batch17.json
prompts/*.txt
```

## 3. 视觉复核

- 八张都没有可见文字、水印、人物、车辆或漂浮建筑。
- 全部提供前景/中景/远景，不是单件资产孤立照。
- 后巷、院落、廊下、桥下都保留至少两个可读出口，适合设计可连续漫游的 topology。
- 屋顶和远边界两张扩大了场景尺度，避免当前 blockout 只在地面近景成立。
- 道路挖填方、果园挡墙和桥涵图补充了地形接触与排水收口，减少任意坐标观察时的
  悬空、硬切和世界边缘。
- `1535px` 宽的两张是 imagegen 实际源字节；没有为凑统一尺寸而重采样或重编码。

## 4. 对 360° / 任意坐标漫游的实际贡献

这批素材能改善“设计覆盖”，具体是：

1. 为每栋建筑要求 front / rear / side / roof / foundation 五类表面，而非只建正面；
2. 为桥、廊、屋檐要求 underside，且暗部必须有可读几何；
3. 为院落、道路和挡墙要求转角、端部、回接与排水，不允许平面在镜头外突然终止；
4. 为村庄边界要求多层过渡：建筑 → 围墙/果园 → 竹林/森林 → 远山；
5. 为跨 chunk 续接提供地形、道路、河床和植被边界的组合参考。

它仍不能提供真正的 360° 几何一致性。实际接受必须来自 Blender 内闭合 mesh、碰撞/
可走拓扑、来自多个**平移相机**的六层实渲和 post-render/coverage 机器证据。

## 5. 建议消费顺序

1. 优先把 `rear-service-alley`、`gallery-undercroft-cellar`、
   `bridge-culvert-inner-bank` 拆为可复用近景模块；它们直接补当前 blockout 的遮挡面。
2. 用 `road-cut-fill-hairpin` 和 `orchard-retaining-backs` 建立 terrain/path/retaining
   联合构造，不要各自悬浮建模。
3. 用 `uphill-roof-backs` 检查 roof/eave/gutter 的背向覆盖与天际线重复度。
4. 用 `village-forest-boundary` 约束 chunk 边界和远 LOD，而不是把图片贴成背景墙。
5. 每个模块进入正式 scene 前都必须绑定 canonical plan/registry/build SHA，并在
   production cameras 中实测可见；图片中的对象数量不能直接变成 coverage 数字。

## 6. Fail-closed / 发布边界

- `synthetic=true`
- `stage=design-only`
- `camera_calibration=unknown`
- `geometry_consistency=not-verified`
- `metric_scale=unknown`
- `training_use=forbidden-as-multiview`
- `coverage_use=forbidden`
- `trust_effect=none`

全部 PNG、prompt、manifest 和 contact sheet 保持在 `.nantai-studio/` 私有候选区；
本轮不进 Git registry、不进 Release。Release 只应出现后续筛选并转换出的干净最终资源，
不放 generation queue、prompt、contact sheet 或其它中间态。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
