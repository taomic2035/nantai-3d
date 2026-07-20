# FEEDBACK-IMAGE2-009 — 代表性山村住宅四面设计参考（Batch 7）

> 产出：Codex 使用 OpenAI 内置图像生成工具
> 日期：2026-07-20
> 面向：Opus 的 four-sided building / production coverage lane

## 结论

已在私有工作区新增同一份文字建筑契约下的正面、背面、左侧和右侧参考图，共
4 张 1536×1024 PNG、13,471,930 bytes。它们补充住宅背立面、侧立面、附属小屋、
排水沟、石基、窗洞和材料老化的设计信息。

私有目录：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
```

清单：

```text
candidate-sources-batch7.json
```

完整提示词位于 `prompts/`，图像位于 `objects/`。

## 图像与哈希

| image | SHA-256 | bytes | generation mode |
|---|---|---:|---|
| `component-canonical-rural-house-front-01.png` | `3516ec8174f60b37add3b64ee77dfcdb67cd4450323d8ad353a70a20731817f1` | 3,561,314 | independent identity anchor |
| `component-canonical-rural-house-left-01.png` | `f6992853e043b94569bcf9432827b7776eadd5fae07a05e71bbd07b4ed8a0b7e` | 3,241,574 | independent after one network retry |
| `component-canonical-rural-house-rear-01.png` | `ae9ebf533e60d2d75f28dc829ef5d63417fa1d30b754e137894840f71b00d69f` | 3,307,826 | independent after edit endpoint failed twice |
| `component-canonical-rural-house-right-01.png` | `fd5659165f78c380c6f2f5dff7a9977808a4f12c8c4861dda517fccf4d282216` | 3,361,216 | independent |

内置接口未暴露可机器验证的实际模型 ID，因此清单保持：

```json
{
  "actual_model_id": "unknown",
  "integration_status": "staged-not-registered"
}
```

## 诚实边界

首次正面图作为外观身份锚点生成成功；随后尝试以它为参考生成背面时，内置 image edit
端点连续两次返回网络错误。为继续推进，背面和两侧改为消费同一份完整文字建筑契约的独立
生成，其中左侧首次 generation 请求也经历一次网络错误后成功。

清单因此写死：

```json
{
  "design_intent": {
    "namespace": "design-only",
    "relationship": "shared-written-building-semantics"
  },
  "evidence_policy": {
    "camera_calibration": "unknown",
    "geometry_consistency": "not-verified",
    "training_use": "forbidden-as-multiview",
    "coverage_use": "forbidden",
    "trust_effect": "none"
  }
}
```

- 四张图不能直接拼成 SfM、NeRF 或 3DGS 训练集。
- `front/rear/left/right` 只是设计角色，不能作为同一几何或相机轨迹证据。
- 正反面覆盖仍只从 Blender 实际 normal / instance / camera 证据计算。
- 住宅的权威四面几何继续由 `four-sided-rural-building-v2` 和 Blender 输出承担。
- 当前干净 Release 不追加本批候选中间态。

## 建议消费

1. 把四面共同出现的稳定语义映射进 `four-sided-rural-building-v2`：
   石基、上下墙体分层、木构角柱、主屋坡屋顶和右侧附属小屋。
2. 把各面差异作为可替换 variant 约束：前阳台/正门、后勤门与排水、左右窗洞和附属屋。
3. Blender 仍须输出可审核的真实四面网格、绝对变换、instance/semantic ID 与 normal 层。
4. 180 相机对这些实例的覆盖结论只由像素分布、观察法线跨度和相机几何产生。

## 已完成核验

- Batch 7 JSON 可解析；
- 4/4 图片 SHA-256、字节数、PNG 格式和 1536×1024 尺寸与清单一致；
- 4/4 完整提示词存在；
- 4 张图已逐张检查，四面、屋顶、石基、木构、附属屋和周边路线可读；
- 未发现水印、可读文字或车辆；
- 素材保存在项目私有可替换区，没有混入 Git 或 68 槽最终视觉包。
