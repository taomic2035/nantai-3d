# FEEDBACK-IMAGE2-022 — Batch 18 反向视角与室内体积参考

> 日期：2026-07-22
> 生成：Codex 使用 OpenAI built-in imagegen
> 状态：`8/8` 已生成、目视复核、逐字节登记
> 位置：`.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/`

## 1. 结论

Batch 18 补齐 Batch 17 之后仍缺的两类自由漫游设计覆盖：

1. 后巷、院落、廊下、桥涵四个节点的**反向观察设计**；
2. 双开口工坊、水磨坊内部、三层折返楼梯、三向门楼四个**室内/垂直体积**。

前四张各绑定一张 Batch 17 source SHA，只用于视觉语言和主要布局关系参考。它们是
imagegen 生成的反向设计补面，**不是**同一空间的标定反向相机、像素对应或多视图训练对。

## 2. 素材身份

| file | size | bytes | SHA-256 | 建模用途 |
|---|---:|---:|---|---|
| `design-reverse-rear-service-alley-uphill-01.png` | `1536×1024` | `3475536` | `29e2a476c5614b9f00599d6950327f02efec0d3fd4533b4df7b5dcf226124e49` | 上坡反看后巷、三路分叉、连续排水、外梯和建筑落地 |
| `design-reverse-courtyard-covered-edge-01.png` | `1536×1024` | `3594882` | `cf8be4dcadcc6fc2a4acf6f745e2873f6aec9ad5db7aa40aa11d7a655086868a` | 对侧廊下反看院落、柱脚/梁底、楼梯回接和多出口 |
| `design-reverse-gallery-undercroft-outbound-01.png` | `1535×1024` | `2898862` | `8441d3a56066c4710d9a6cac943a72b72c8a63a905d23b5687768bc401a18372` | 反向穿越廊下、柱背、梁接头、楼梯底、地窖阈值和侧沟 |
| `design-reverse-bridge-opposite-bank-01.png` | `1536×1024` | `3876825` | `8416b029bf5e33437cf8475aecd1f514e81f7c659578ed9420462d53df335f56` | 对岸观察完整桥底、两侧桥台、河床、涵洞和双岸路线 |
| `design-interior-through-workshop-01.png` | `1536×1024` | `2685445` | `573c974b58aa248db2b3ecd5347c81f825226e0628320c0b6e05b6119ad7b3ce` | 院落—工坊—后巷贯通，完整墙/顶/地壳体、楼梯与工作区 |
| `design-interior-watermill-machinery-tailrace-01.png` | `1536×1024` | `3444878` | `b3fb2d0a84e99a8e0be30394153bf2bafbad4d35e646bfa7ff0c548949a8a920` | 磨盘、齿轮、穿墙轴、外水轮、楼板下尾水和维护路线 |
| `design-vertical-stair-roof-landing-01.png` | `1024×1536` | `3307993` | `eb59747b8e8f39f56172b5c976258db1be6a4b05cedc01bb5d28b7c9170b032c` | 三层高差、折返楼梯、廊道、檐底、屋顶和排水回接 |
| `design-threshold-gatehouse-three-way-01.png` | `1536×1024` | `3245609` | `7d609986ec1eda7b604295b1bfd47e4640f4e015e408925f0749867aedfb12d7` | 门楼内部前行/侧院/上坡三向转身、阈值与上层廊道 |

源图总字节数：`26530030`。

私有 QA contact sheet：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/
  contact-sheet-batch18.png
```

| bytes | size | SHA-256 |
|---:|---:|---|
| `1489014` | `1536×580` | `8f28723418edf1a4021452f269f61837bc45177de50f2ac0f4c15bb544b2505e` |

精确主 prompt、两份修正 prompt、四个 reference binding、逐图尺寸/模式和 queue 位于：

```text
candidate-sources-batch18.json
generation-queue-batch18.json
prompts/*.txt
```

## 3. 修正与视觉复核

- 工坊首版后墙出现疑似字迹纸片；最终版用单点 edit 移除纸片/文字，双出口、楼梯、工具、
  梁柱、地面和光照保持不变。主 prompt 与 correction prompt 均已登记。
- 楼梯首版含现代线缆/电杆；最终版仅移除线缆/电杆，三层路线、建筑和相机保持不变。
- 八张最终图均无可见文字、水印、人物、动物、车辆或现代标志。
- 四张反向设计均形成明显不同的观察位置，不是简单镜像；但 imagegen 无法证明真实几何
  对应，因此 manifest 继续声明 `geometry_consistency=not-verified`。
- 工坊与门楼同时提供多出口；水磨坊同时展开室内机械和下层水道；楼梯图覆盖地面到屋顶
  的垂直连接。这些都是建模参考，不是可走性或碰撞验收。
- `1535px` 宽和 `1024×1536` 竖图均为 imagegen 原始源字节，没有为统一尺寸重采样。

## 4. 对 360° / 任意坐标漫游的实际贡献

这批素材把设计要求从“外部正面可看”推进到以下闭合体积：

1. 路线节点需要正向与反向均有可建模表面，转身后不能暴露空壳；
2. 建筑需要墙、顶、地、阈值、柱基、梁底、楼梯底和排水连续；
3. 室内外两端必须通过真实门槛和落地表面连接，不能用黑门洞遮蔽；
4. 水磨坊的机械、轴、楼板、基础、外水轮和尾水应作为联动模块建模；
5. 垂直路线必须明确每段楼梯、平台、护栏、檐口和上层出口；
6. 三向门楼可用于检查进入节点后前进、侧转和上坡三种导航选择。

它仍不证明真正的 360° 几何一致性或任意坐标可达。实际 acceptance 必须来自闭合 Blender
mesh、可走 topology、碰撞/净空、多台平移相机六层实渲、visibility 与 post-render v2。

## 5. 建议消费顺序

1. 先把 `through-workshop` 和 `three-way-gatehouse` 转为双向/三向 topology 模块，关闭
   当前建筑内部黑箱。
2. 把 `watermill-machinery-tailrace` 拆为 machinery / shaft / wheel / millrace / tailrace
   独立对象，并显式绑定支撑和水流关系。
3. 用 `vertical-stair-roof-landing` 建立可复用 stair/landing/gallery/eave 组合，避免只建
   地面 XY 路网。
4. 四张 reverse 图只用于补 surface checklist 与落位参考；不得把正反两图输入 SfM。
5. 正式消费后重建 plan/registry/exact build，并以 production cameras 实测可见像素。

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
本轮不进 Git registry、不进 Release。Release 只应出现后续筛选、建模、验证后形成的干净
最终资源，不放生成中间态。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
