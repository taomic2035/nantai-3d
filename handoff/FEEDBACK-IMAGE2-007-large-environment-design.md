# FEEDBACK-IMAGE2-007 — 大尺度山村环境设计输入（Batch 4）

> 产出：Codex 通过已授权的 ChatGPT 网页图像生成
> 日期：2026-07-17
> 面向：Opus 的 Blender component / production profile lane

## 结论

新增 3 张 1672×941 的通用山村环境设计参考，共 10,035,820 bytes：

1. 大村总体布局：多组院落、环路/支路、石桥、水车、灌渠、梯田与工作场；
2. 下游服务区：桥下空间、水轮/引水槽、建筑下穿、涵洞、后巷和垂直路线；
3. 上村林缘农业区：梯田、果园、竹林、山脊路径、灌溉跌水和建筑背/侧立面。

三张图故意覆盖不同的模块与空间层级，作为 Blender 环境拆分和程序化布局参考。它们均为
synthetic、replaceable、staged-not-registered。

## 私有路径

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
```

Batch 4 清单：

```text
candidate-sources-batch4.json
```

## 图像与哈希

| image | SHA-256 | bytes | role |
|---|---|---:|---|
| `design-overview-large-village-01.png` | `713b39b2a1dfac91f9378515987930f2f7ebd5d191066e7fd3411a5f9c07c72c` | 3,503,081 | elevated three-quarter overview |
| `design-lower-service-district-01.png` | `2dd75ddd9c977b411a3bdf2885c129cead8f06f735ebbaa3b819a7b259b6b829` | 3,254,274 | low elevated downstream oblique |
| `design-upper-ridge-agriculture-01.png` | `97f9e7896ee8942d5347045e992117c02f71e8ac0dd8920933d02b5a62150e17` | 3,278,465 | elevated oblique upper ridge |

网页只暴露生成界面，未暴露可机器验证的实际模型 ID，因此清单如实记录
`actual_model_id: "unknown"`。第三张首次请求产生空回复；只重试一次后成功，清单保留这一事实。

## 诚实边界

清单固定声明：

```json
{
  "relationship": "single-view-design-reference-only",
  "camera_calibration": "unknown",
  "geometry_consistency": "not-applicable-single-view",
  "training_use": "forbidden-as-multiview"
}
```

- 三张图是彼此独立的设计输入，不是同一场景的不同相机视角；
- 不得用于 SfM、相机位姿、米制尺度、3DGS/NeRF 多视图训练或 coverage audit；
- 不得从文件名、画面相似度或生成界面推断几何一致性；
- coverage 仍只能由 Blender 实际场景渲染的 RGB/depth/normal/instance/semantic/camera
  证据产生。

## 建议拆分

建议把画面语义拆成可替换的稳定组件，而不是复刻整张图：

1. `village-cluster-tiered-v1`：分层院落与背/侧立面；
2. `bridge-watermill-junction-v1`：石桥、桥台、水轮、引水槽和维修路径；
3. `covered-service-lane-v1`：建筑下穿、后巷、涵洞和排水；
4. `ridge-agriculture-transition-v1`：梯田、果园、竹林与林缘；
5. `switchback-retaining-route-v1`：台阶、坡道、挡墙与垂直连接；
6. `spring-irrigation-cascade-v1`：泉屋、蓄水池、灌渠与跌水。

正式组件需获得稳定 component ID / instance ID，进入 production profile 并通过实际渲染覆盖审计后，
才允许加入下一版干净 Release。当前 Release 保持不变，不追加这些候选中间态。

## 已完成核验

- Batch 4 JSON 可解析；
- 3/3 图像字节数与 SHA-256 和清单一致；
- 3/3 提示词文件存在；
- 3/3 图像均为 1672×941 PNG；
- 逐张目视检查：关键路线、结构连接与高差清晰；
- 未发现水印、可读文字、人物或车辆；
- 当前 GitHub Release 仍只有 canary 数据集、最终 68 槽位视觉包和单一校验文件。
