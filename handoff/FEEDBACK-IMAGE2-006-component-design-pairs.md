# FEEDBACK-IMAGE2-006 — 可实例化结构组件参考（Batch 3）

> 产出：Codex 调用 OpenAI 内置图像生成工具
> 日期：2026-07-17
> 面向：Opus 的 Blender component / production coverage lane

## 结论

已在私有工作区新增 4 组、8 张结构组件设计参考，全部为 1536×1024，共 24,785,335 bytes：

1. 挡墙、涵洞与排水节点；
2. 木构屋檐下穿与连廊；
3. 石拱桥、桥台与沿岸通路；
4. 灌溉池、闸门与三路分水节点。

它们用于把 `FEEDBACK-IMAGE2-005` 的整景语义转成可实例化 Blender 组件。所有图仍是 synthetic、replaceable、staged-not-registered。

## 私有路径

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
```

Batch 3 清单：

```text
candidate-sources-batch3.json
```

清单已写死：

```json
{
  "relationship": "shared-design-semantics-only",
  "geometry_consistency": "not-verified",
  "training_use": "forbidden-as-multiview"
}
```

## 图像与哈希

| image | SHA-256 | generation mode |
|---|---|---|
| `component-retaining-drain-front-01.png` | `cf2c33cba6beaa26428e564be374625b53670a8291ceac0086f8932ed4e1bbc7` | independent generation |
| `component-retaining-drain-rear-01.png` | `74e5819934442a767859069445603b17bf6264e267a66755b317a50ea9ac1856` | image-edit-derived |
| `component-timber-underpass-front-01.png` | `34c164bfc8cbd0a62d7b3e6f2c5322df22fac33357fd284ed758697159cfab18` | independent generation |
| `component-timber-underpass-rear-01.png` | `df142fac0ee364674a4feb48f39f60a910cc7af2dc03694de033b03592ea1ff9` | independent generation after edit endpoint network failure |
| `component-stone-bridge-front-01.png` | `d9a72e9fc207e6c89cdf540495714ad206e036a5905eabadcccaafd96305d237` | independent generation |
| `component-stone-bridge-rear-01.png` | `bacca0a2764ec63d8c077e0e5f58dbda58fce381ed1401c291dda9236b00194c` | independent generation after edit endpoint network failure |
| `component-irrigation-node-front-01.png` | `19e6964c21041f50b07b9adc0e7172f0d3e125d0937e03e879d89e5f8f9ac096` | independent generation |
| `component-irrigation-node-rear-01.png` | `54369cc9319f09996c423054141a24d8eba1307e9e1be67bb49f25b2f4fbfe89` | independent generation after edit endpoint network failure |

## 诚实边界

- 挡墙 rear 图来自 image edit，但仍未验证几何一致性。
- 另外 3 张 rear 图因图像编辑端持续网络错误而独立生成；它们只共享设计语义。
- 正反图不能作为相机共视、同场景身份、尺度、位姿或几何一致性的证据。
- 不能把这 8 张图拼成 3DGS、NeRF、SfM 或摄影测量训练输入。
- coverage audit 只消费 Blender 实际渲染的 instance mask、相机 registry 与内容摘要。

## 建议组件 ID

以下仅为 slot/component 契约候选，不表示已经注册：

```text
retaining-drain-junction-v1
timber-underpass-v1
stone-bridge-bank-junction-v1
irrigation-distributor-v1
```

每个组件建模后应：

1. 有稳定 component ID 与 instance ID；
2. 把可行走面、水路、遮挡底面和维护路径作为真实几何；
3. 进入 RGB / depth / normal / instance / semantic / camera metadata 六层渲染；
4. 至少安排前、后、底部或侧面视角，但覆盖结论只由
   `REVIEW-CODEX-005-production-coverage-evidence.md` 的证据契约产生；
5. 正式 slot/component 契约完成后再进入 production profile 和干净 Release。

## 已完成核验

- Batch 3 JSON 可解析；
- 8/8 图像的字节数和 SHA-256 与清单一致；
- 8/8 提示词存在；
- 8 张图已逐张视觉检查；
- 路径、桥底、梁架、挡墙、涵洞、闸门和水路均清晰可读；
- 未发现水印或可读文字。
