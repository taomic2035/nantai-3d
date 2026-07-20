# FEEDBACK-IMAGE2-011 — Batch 6 桥底与水车路口参考

> 产出：Codex 使用 OpenAI 内置图像生成工具
> 日期：2026-07-20
> 面向：Blender environment / route modeling lane

## 结论

Batch 6 从 `1/12` 推进为 `2/12`。新增素材是桥底、水车和多高度路线交汇处的人眼
视角，重点补充总览与中央院落图难以表达的桥拱底面、桥台、水车服务院、排水结构、
建筑侧后墙和上下坡路线。

私有素材：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
  environments/design-detail-bridge-undercroft-01.png
```

清单与实际提示词：

```text
candidate-sources-batch6.json
prompts/design-detail-bridge-undercroft-01-independent.txt
```

## 字节证据

```text
format: PNG
dimensions: 1536 x 1024
mode: RGB
bytes: 3,705,037
sha256: 16b9f390f4550b2ec64bd98e4ccd799e05c4f44cd924a5da1503eec73ae8b4be
```

内置接口仍未暴露可机器验证的实际模型 ID，因此清单保持：

```json
{
  "actual_model_id": "unknown",
  "integration_status": "staged-not-registered"
}
```

## 生成与服务状态

本素材是独立生成，没有把既有总览或中央院落图冒充为已知相机参考。生成模式记录为：

```json
{
  "generation_mode": "independent-generation-after-service-recovery",
  "design_intent": {
    "relationship": "independent-single-view-environment-reference"
  }
}
```

紧随其后的“建筑后场/服务院落”请求再次返回 image generation network error。该失败
请求没有输出、没有占用候选记录；queue 保持 `2/12`，并保存最后一次错误。

## 视觉检查

- 桥拱底面、侧面和石砌桥台清楚可读；
- 水车、磨坊水槽、落水与水车服务平台可以拆为独立模块；
- 左侧桥下溪边维护路、右侧宽台阶、右缘坡道、前景踏石和水车平台形成多高度路口；
- 排水口、挡墙、栏杆、湿石铺地、建筑侧后墙、门窗和屋檐底部可用于补齐遮挡区；
- 房屋、地形、道路和道具不是单一重复实例；
- 未见人物、动物、车辆、文字、标签或水印；
- 画面适合做环境拓扑和构件设计参考，但并未证明这些路线真实连通。

## 诚实边界

```json
{
  "camera_calibration": "unknown",
  "geometry_consistency": "not-verified",
  "training_use": "forbidden-as-multiview",
  "coverage_use": "forbidden",
  "trust_effect": "none"
}
```

- 它不是中央院落或总览图的已知反向机位，不能参与 SfM、NeRF 或 3DGS 多视图训练。
- 图中水流、水车、台阶、坡道和桥拱之间的尺寸关系未经过几何验证。
- 路线宽度、净空、坡度、碰撞和闭环必须在 Blender topology 中重新建模与测量。
- 180 相机的覆盖结论只能由正式六层渲染产物提供。
- 当前干净 Release 不加入这张候选中间态。

## 建议消费

1. 将画面拆成石桥拱与桥台、水车与磨坊水槽、溪边维护路、宽台阶、坡道和踏石六类模块。
2. 为桥下通道和建筑檐下空间记录净空、碰撞、排水及相机可通过性。
3. 将挡墙、排水口、栏杆、门窗、火柴堆和容器作为可替换 variant，避免复制整张场景。
4. 在 Windows 180-camera runner 可用后，以 depth/normal/instance/semantic 层验证真实遮挡，
   不从图片文件名或设计说明提升 geometry trust。

## 已完成核验

- Batch 6 queue JSON 与 candidate manifest 可解析；
- queue 状态为 `partial-output-2-of-12`，2 个成功、10 个待生成；
- PNG 解码、尺寸、模式、字节数和 SHA-256 与清单一致；
- 精确提示词文件存在；
- 清单与队列均未提升 geometry trust；
- 候选素材保存在项目私有可替换区，没有混入 Git 或最终 Release。
