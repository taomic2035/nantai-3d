# FEEDBACK-IMAGE2-023 — Batch 19 平移视角、隐藏体积与世界边界参考

> 日期：2026-07-22
> 生成：Codex 使用 OpenAI built-in imagegen
> 状态：`8/8` 已生成、单点修正、目视复核、逐字节登记
> 位置：`.nantai-studio/synthetic-village/hybrid-v4-candidates/batch19/`

## 1. 结论

Batch 19 不再增加相似的正面风景照，而是补齐三类对 360° 自由观察和任意坐标漫游更有
价值的设计输入：

1. 工坊、水磨坊、屋顶平台和门楼的**平移反向视角**，补参考图遮挡的后表面；
2. 架空层、屋顶上包络和道路/廊道/溪沟叠置节点的**闭合体积与竖向关系**；
3. 道路、溪流、灌溉、果园和森林共同延伸的**跨 chunk 渐变边界**。

前四张各绑定一张 Batch 18 source SHA。绑定只说明视觉语言与主要构件关系的输入来源；
imagegen 不能提供真实相机标定、像素对应或跨图几何一致性，因此这些图仍不得组合成
SfM、NeRF 或 3DGS 多视图训练集。

## 2. 素材身份

| file | size | bytes | SHA-256 | 建模用途 |
|---|---:|---:|---|---|
| `design-translated-workshop-rear-through-01.png` | `1536×1024` | `3301385` | `ba3db92eed14bb84656bcff7123682d39fa851a6955d40895bdb7b246e73e8fb` | 后巷反看工坊外壳、基础、檐底、排水并穿透至对侧院落 |
| `design-translated-watermill-opposite-bank-01.png` | `1536×1024` | `3846137` | `b33957c03d27042d7db0052ea7c4bcef4da7ea1d64a27b6deb18364e74e47f8e` | 对岸展开水轮、轴承、机架、磨坊内部、闸门、溢流与检修路线 |
| `design-translated-roof-landing-downhill-01.png` | `1024×1536` | `3463846` | `0d73e98a262031b0d71b68f3a88de9f059371760af5c0320a51fdd14dc0204b5` | 上层平台向下观察屋顶背坡、楼梯回接、廊道、栏杆和低层路线 |
| `design-translated-gatehouse-rear-uphill-01.png` | `1536×1024` | `3686895` | `c5f150e175b0acd5fe7c84dc23af192fe4ec4d2e99ec66f7b1c0d60b107e3ab5` | 门楼后侧上坡观察门槛、后立面、廊底、侧梯、院口和多向路线 |
| `design-volume-raised-house-undercroft-01.png` | `1536×1024` | `3395701` | `5e3ed96579021926469a7e0cabff2731576609b2149b9b225a75ea8e4a7fefbd` | 架空层柱基、斜撑、楼板底、排水、地窖和双端通行体积 |
| `design-volume-roofscape-upper-envelope-01.png` | `1536×1024` | `3656849` | `945ab650bd4d5c53df9c319f6b3d4854b24a455159d5a5cc6d0caff3bb9d67ad` | 大尺度屋顶包络、合法上层落点、栏墙、楼梯、廊桥和远村密度 |
| `design-boundary-multimodal-chunk-transition-01.png` | `1536×1024` | `3750010` | `465c156e0f3457ea330053b4256d942c6a3451926d52197cdf31e68caa570bb9` | 道路/溪沟/灌溉/果园/森林五向续接与世界边界密度渐变 |
| `design-volume-stacked-route-water-junction-01.png` | `1536×1024` | `3588974` | `9763b46fdd6c88121bacf44bebe21ea30462c7b40e9df88635ca3298bc7b97a8` | 下层溪沟—涵洞—中层廊道—上层道路—楼梯四层叠置节点 |

源图总字节数：`28689797`。

私有 QA contact sheet：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch19/
  contact-sheet-batch19.png
```

| bytes | size | SHA-256 |
|---:|---:|---|
| `1469374` | `1536×580` | `130e48e56bdfe202b9495ae4eecaf2db51a003e01cb40cbb78f87992f39b4b47` |

精确 prompt、修正 prompt、四个 reference binding、逐图尺寸/模式和 queue 位于：

```text
candidate-sources-batch19.json
generation-queue-batch19.json
prompts/*.txt
```

## 3. 修正与视觉复核

- 屋顶平台首版在低层路线旁出现醒目的绿色现代塑料容器；最终版以单点 edit 替换为中性
  乡村器物，相机、建筑、路线、材质和光照保持不变。主 prompt 与 correction prompt 均登记。
- 八张最终图均无可见文字、水印、人物、动物、车辆、悬空建筑或封死黑门洞。
- 四张平移图的观察位置与对应 Batch 18 图明显不同，不是镜像；但仍只声明
  `geometry_consistency=not-verified`。
- 架空层图能读出柱基、斜撑、楼板底和两端出口；叠置节点能同时读出四个高度层；世界
  边界图的道路、水路、果园和森林都继续出画，不把远景做成背景墙。
- 屋顶包络图扩大到数十栋建筑的层叠村庄尺度，同时保留合法人眼落点，不以无人机俯瞰
  替代可漫游视角。
- manifest 校验重新读取全部 PNG、prompt、correction prompt、reference 和 contact sheet，
  实测尺寸、字节数、SHA 全部一致：`PASS / 8 candidates / 28,689,797 source bytes`。

## 4. 对 360° / 任意坐标漫游的实际贡献

这批素材把后续 Blender 消费要求具体化为：

1. 工坊和门楼必须从两端可见真实连续的墙、顶、地、门槛和排水，不能用黑面隐藏室内；
2. 水磨坊必须保持水轮—穿墙轴—轴承—机架—磨盘—进水—尾水的可检查关系；
3. 屋顶视域必须包含背坡、谷沟、檐底、栏墙和回到地面的楼梯，不只做远景贴片；
4. 架空建筑必须有柱基、斜撑和楼板底，任意坐标走入下方不能暴露空壳或悬浮；
5. 上下叠置路线必须分别拥有真实支撑、净空、碰撞和排水，开放路径不得被统一隧道顶封死；
6. 相邻 chunk 的道路、水系、地形和植被要共同续接，避免密度断崖与世界边缘。

图片仍不能证明真正的 360° 几何一致性或任意坐标可达。Acceptance 必须来自闭合 Blender
mesh、可走 topology、碰撞/净空、多台平移相机六层实渲、visibility、post-render v2 和跨
chunk seam 实测。

## 5. 建议消费顺序

1. 在 GLM 的 role-aware mesh 修复中先消费 `raised-house-undercroft` 与
   `stacked-route-water-junction`，替换当前“所有 part 都是隧道盒”的错误形态。
2. 把 `workshop-rear-through` 和 `gatehouse-rear-uphill` 变成双向/多向 topology 模块，
   并用两端 production camera 检查门槛和转身后的背面。
3. 把 `watermill-opposite-bank` 拆成 wheel / shaft / bearing / machinery / flume / tailrace，
   每个对象保留独立 instance 与支撑关系。
4. 用 `roof-landing-downhill` 和 `roofscape-upper-envelope` 补 roof/eave/gutter/landing LOD，
   不能把屋顶仅当不可达背景。
5. 用 `multimodal-chunk-transition` 建立道路、水系、地形、果园与森林的共同 seam fixture，
   然后在按需相邻块中做两侧一致性实测。

## 6. Fail-closed / 发布边界

- `synthetic=true`
- `stage=design-only`
- `camera_calibration=unknown`
- `geometry_consistency=not-verified`
- `metric_scale=unknown`
- `training_use=forbidden-as-multiview`
- `coverage_use=forbidden`
- `trust_effect=none`

全部 PNG、prompt、manifest、queue 和 contact sheet 保持在 `.nantai-studio/` 私有候选区；
本轮不进 Git registry、不进默认资源、不进 Release。Release 只应包含后续筛选、建模、实渲
验证后形成的干净最终资源，不包含生成中间态。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
