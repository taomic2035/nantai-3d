# FEEDBACK-IMAGE2-005 — 360° 覆盖候选素材（Batch 1–2）

> 产出：Codex 调用 OpenAI 内置图像生成工具
> 日期：2026-07-17
> 面向：Opus 的 `HANDOFF-OPUS-005` 180 相机生产档与程序化场景扩容

## 结论

已在私有工作区增加 8 张 1672×941 的通用山村覆盖候选图，重点补足建筑背面、院落环路、村田边界、溪流连通、林缘、公共作业院、果园回转坡道与屋檐下穿。

这些图是**彼此独立的 synthetic design references**。它们不是同一场景的多视图捕获，不能证明 camera co-visibility、共享场景身份、米制尺度、几何一致性或任何实测 provenance。它们适合指导 Blender 程序化几何、材质和相机路径扩容；不应直接拼成 3DGS 训练集。

## 私有素材位置

根目录：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
```

清单：

```text
candidate-sources.json
candidate-sources-batch2.json
```

图像位于 `objects/`，完整提示词位于 `prompts/`。两个清单均标记：

```json
{
  "synthetic": true,
  "actual_model_id": "unknown",
  "integration_status": "staged-not-registered"
}
```

## 候选与覆盖轴

| candidate | SHA-256 | 主要补足 |
|---|---|---|
| `coverage-rear-service-lane-01` | `ff79f96062205ac7d7eb3f75281aed56e73d93fd55b5708fdacb96961fed6cf2` | 建筑背面、后门/檐口、三岔后巷、作业角、明沟 |
| `coverage-stream-confluence-01` | `6ccc7a0c444c183718943d6ba05c54791d50506eb81516c267bb65d924a85aa3` | 双溪汇流、桥底、沿岸步道、灌溉取水、四向续行 |
| `coverage-upper-courtyard-ring-01` | `31f7a15bc19a7aa1d7903b9096f110f426261b88cb11b3b7405285eac151b0a3` | 高层步行环、屋顶背面、连院、四层分支、挡墙高差 |
| `coverage-village-field-transition-01` | `b867eea292d73893c7022bd142976c249480235b17e4f097a69016d5d4b2f4a7` | 聚落边界、梯田层级、田间三岔、灌溉网络、远端导航锚点 |
| `coverage-forest-village-boundary-01` | `0d4dd7779c54468279049e9d8b42d30d6c5850f9a4cbb9120d96d9ce1eb547c3` | 林村边界、建筑背面、三路林径、溪桥、挡墙高差 |
| `coverage-community-workyard-01` | `d42536553c6356520282721749ab1e0a9b9550b93bb3c7e30d22a73dc2a0e635` | 四出口院落、侧/后立面、道具环绕、供水、上下层连接 |
| `coverage-orchard-switchback-01` | `23d2f277dad1b2a77092fd98930cb7bd4d002de5f615165bf002cbf97bbdd10a` | 回转环路、三层果园、灌溉、桥涵、建筑背面、田间续行 |
| `coverage-roof-eave-underpass-01` | `6ef530f91c4cd2b26e2693dd158606ef02747972254ff20070de7c42b0ac7869` | 瓦底/梁架、连廊、上下分叉、明暗过渡、平台、挡墙排水 |

## 建议进入生产场景的方式

1. 把候选中的**结构语义**转成可实例化组件，例如后巷、连院、三岔、桥涵、下穿、挡墙、坡道与灌溉节点。
2. 组件使用稳定 ID，进入 instance/semantic/depth 六层渲染；视觉像素统计再决定其是否满足正反向和三视角覆盖。
3. 180 相机按照 `ground-route`、`elevated-pedestrian`、`perimeter-inward`、`environment-corridor` 与 `audit-overview` 分组覆盖这些组件。
4. 真实生产报告只从渲染证据计算 coverage；候选图名、提示词和本文件均不得作为 coverage 证明。
5. 等 `FEEDBACK-HANDOFF-OPUS-005.md` 给出正式 slot/component 契约后，再把这些候选注册成可替换输入并整理干净 Release。

## 已完成核验

- 两个 JSON 清单均可解析。
- 8 张图像的实际字节数与 SHA-256 均和清单一致。
- 8 份提示词均存在。
- Batch 2 四张图已逐张视觉检查：路径连接清楚、元素密度足、暗部可读；未发现水印或可读文字。
