# FEEDBACK-IMAGE2-012 — Batch 6 建筑后场与服务院落

> 产出：Codex 使用 OpenAI 内置图像生成工具
> 日期：2026-07-20
> 面向：Blender environment / route modeling lane

## 结论

Batch 6 从 `2/12` 推进为 `3/12`。新增素材补齐建筑正面与高空总览看不到的后墙、
侧墙、屋檐底部、石基础、架空层、工作棚、跨巷廊桥、上下行维护巷和小型菜圃。

私有素材：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
  environments/design-detail-rear-service-courtyard-01.png
```

清单与实际提示词：

```text
candidate-sources-batch6.json
prompts/design-detail-rear-service-courtyard-01-independent.txt
```

## 字节证据

```text
format: PNG
dimensions: 1536 x 1024
mode: RGB
bytes: 3,342,507
sha256: 2c3900ab686cb45252538c8bdb6e507396ec9084ca7809a44fa3524810ab8b51
```

内置接口没有暴露可机器验证的实际模型 ID：

```json
{
  "actual_model_id": "unknown",
  "integration_status": "staged-not-registered"
}
```

## 生成状态

本次成功发生在同一请求此前多次网络失败之后。它仍是独立设计参考，不是既有村庄总览
或院落图的已知反向机位：

```json
{
  "generation_mode": "independent-generation-after-service-recovery",
  "design_intent": {
    "relationship": "independent-single-view-environment-reference"
  }
}
```

## 视觉检查

- 主要建筑的后墙、侧墙、檐底、石基础和架空储物区清晰可拆；
- 左侧连续台阶形成上行路线，右侧窄巷形成下行维护路线；
- 跨巷木廊桥补充了上层横向连接与下层净空设计；
- 前景工作院、洗涤台、柴堆、篮筐、陶罐、工具和菜圃可拆为独立模块；
- 建筑、屋顶、门窗和道具存在明显变化，没有整齐重复复制；
- 排水、挡墙和路面高差可读；
- 未见人物、动物、车辆、文字、标签或水印。

画面中的“四向连接”并非全部同时可见，因此不能把提示词要求直接当成实际画面或
scene graph 证据。可见路线只用于提出建模候选。

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

- 它不能与前两张 Batch 6 素材组成 SfM、NeRF 或 3DGS 多视图训练集。
- 建筑、台阶、廊桥、巷道和菜圃之间的尺寸关系未验证。
- 跨巷廊桥的净空、承重、碰撞和路线闭环必须由 Blender 几何实测。
- 图像的设计语义不能覆盖机器可验证的 `ScenePlan` 与 `ElevatedTopologyPlan`。
- 当前干净 Release 不加入该候选中间态。

## 建议消费

1. 以 `facade-rear-service-v1`、`facade-side-eave-v1` 和
   `undercroft-access-v1` 补齐当前建筑背面组件。
2. 将跨巷廊桥作为 `covered-passage` variant，明确下层净空与上下层碰撞面。
3. 把工作棚、柴堆、洗涤台、菜圃、篮筐、陶罐和工具拆为独立可替换对象。
4. 复用 `HANDOFF-CODEX-008` 中的中央 topology 与 180 相机候选门禁，不从源图
   推导坐标、覆盖或 geometry trust。

## 已完成核验

- Batch 6 queue JSON 与 candidate manifest 可解析；
- queue 状态为 `partial-output-3-of-12`，3 个成功、9 个待生成；
- PNG 解码、尺寸、模式、字节数和 SHA-256 与清单一致；
- 精确提示词文件存在；
- 清单与队列均未提升 geometry trust；
- 模块消费规格已加入第三张输入身份；
- 候选素材保存在项目私有可替换区，没有混入 Git 或最终 Release。
