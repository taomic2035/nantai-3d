# FEEDBACK-IMAGE2-024 — Batch 20 角色拓扑与相机包络参考

> 日期：2026-07-23
> 生成：Codex 使用 OpenAI built-in imagegen
> 状态：`8/8` 已生成、目视复核、逐字节登记并形成干净 Release

## 结论

Batch 20 针对 v5 正式六角色实渲仍拒绝的桥、水车和森林三类场景，不再增加普通正面村景，
而是补充非共线路线、反向入口、共享空间关系和站立视角附近的结构密度。八张图片均为可替换
`design-only` 输入，不是标定多视图，也不提升任何几何信任。

私有候选与 QA：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch20/
```

干净 Release：

- tag：`synthetic-village-design-inputs-batch20-2026-07-23`
- archive：`synthetic-village-role-topology-design-pack-batch20-2026-07-23.zip`
- archive bytes：`29481913`
- archive SHA-256：`55251c47fd4b25fa1bca9a2a5b5ee1cc98a567ce98131fdb0d628f00ce8cb360`
- URL：`https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch20-2026-07-23`

Release 只包含 8 张最终 PNG、8 份精确 prompt、`manifest.json`、`USAGE.md` 和
`PAYLOAD-SHA256SUMS.txt`。私有 contact sheet、生成队列、聚合调用的失败状态和旧批次没有进入包。

## 素材身份

| file | size | bytes | SHA-256 | 主要建模角色 |
|---|---:|---:|---|---|
| `design-topology-bridge-dogleg-approach-01.png` | `1536×1024` | `3699240` | `978277df5af65edc48ebb10cf1bfc29d4245f3c1c23d82088eab9a1582778502` | 折线接近、转角平台、桥面/桥台、上下层分支 |
| `design-topology-bridge-exit-courtyard-return-01.png` | `1536×1024` | `3768782` | `e9f249a86a84fae23e373a0f90ff9fb25f8ebe79f38f228295e8978a1a0bcbaa` | 桥后院落回望、出口、背桥台、回返楼梯 |
| `design-topology-watermill-service-loop-01.png` | `1536×1024` | `3950277` | `c74858290f98d6e5aba7c2450caf2a189d16cc5413abe7fff98047a48608474a` | 水轮/轴承/机架与绕行检修环 |
| `design-topology-watermill-upper-terrace-return-01.png` | `1536×1024` | `3908776` | `dfe4c1126f876d923ad232b2026042753439fda9804e57df059a5a3ee296986f` | 上层平台、下行楼梯、短桥与对岸路线 |
| `design-topology-forest-switchback-cluster-01.png` | `1536×1024` | `3954819` | `1e30d7c02312aefc295ce835a5abb2bf53af371cc0d29ec50c835afc78ce7b32` | 三层折返、果园平台、挡墙/涵洞与边缘建筑 |
| `design-topology-forest-village-return-01.png` | `1535×1024` | `3436775` | `e1d1b823f71a7862df5e219079ec22e49e8d3bceee358df88e145dc28d114c0f` | 林内返村、路线分叉、挡墙背面与排水出口 |
| `design-topology-bridge-watermill-shared-node-01.png` | `1536×1024` | `3755058` | `d6ddf60fb23eddba919ac81c27d9287d327b36e310dc6a062f8ab1d037657679` | 桥—水车共享节点与多高度连接 |
| `design-topology-forest-orchard-shared-envelope-01.png` | `1536×1024` | `3763288` | `ac3983d41715802c8508e0648c400222d8338edc15c1c416ca80f1e8fe17db1e` | 村巷—果园—森林包络与跨 chunk 连续关系 |

源图总字节数：`30237015`。私有 QA contact sheet：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch20/contact-sheet-batch20.png
SHA-256 = e9e1630e09f955262db5a77c29d276aa9bc7af57597efa49144eb173e7156e02
```

## 目视复核

- 八张均无可见文字、水印、人物、动物、车辆、现代线缆或悬空建筑。
- 桥、水车和森林构件均占据主体，不再是大面积空地中的微小远景。
- 桥两侧、水车上下层和森林内外侧均有不同观察位置；这些只是设计角色，不声称 reciprocal pose。
- 水车两张均可读水轮、轴、机架、进/尾水和检修路线，没有用大黑门洞遮蔽结构。
- 森林两张均包含挡墙、涵洞、果园/建筑和多向路线，直接针对 v5 森林帧上部地面占比过高问题。
- manifest、prompt、PNG 与解压后的 18 条 payload checksum 全部复核通过。

## Blender 消费建议

1. 先用桥两张生成显式 `part_layout`：接近段、约 65° 转角平台、桥面、远端院落和上下层分支；
   不得退化为一条中心直线。
2. 水车拆为 wheel / shaft / bearing / machinery / flume / tailrace，并把检修环与上层平台作为
   独立可行走段；相机从真实路线切线与全部 part 的空间包络推导。
3. 森林以挡墙和涵洞为锚点，建立三层折返、果园平台与返村支路；构件需围绕相机形成近中远景，
   但道路坡度仍须由 canonical terrain 与不超过 12% 的路线约束决定。
4. 使用共享节点图协调桥—水车和森林—果园的相对关系；图片像素不能直接成为世界坐标。
5. topology 每次变化后重算 production plan / reciprocal plan / exact-218 build，并重跑 Phase 4.3、
   六角色 preflight、六层、visibility 与 post-render v2。正式 `upper_ground_max=0.30` 不得放宽。

## Fail-closed 边界

- `synthetic=true`
- `stage=design-only`
- `camera_calibration=unknown`
- `geometry_consistency=not-verified`
- `metric_scale=unknown`
- `training_use=forbidden-as-multiview`
- `coverage_use=forbidden`
- `trust_effect=none`

Release 让素材可以被其它机器下载和替换，但不把图片变成真实纹理、真实模型或 360° coverage
证据。真实重建仍需真实采集、COLMAP 位姿和外部 CUDA 3DGS 训练；本包只推进合成 Blender 场景
的设计完整度与相机包络。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
