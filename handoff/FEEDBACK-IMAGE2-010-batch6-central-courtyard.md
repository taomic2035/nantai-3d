# FEEDBACK-IMAGE2-010 — Batch 6 中央院落人眼视角

> 产出：Codex 使用 OpenAI 内置图像生成工具
> 日期：2026-07-20
> 面向：Opus 的 Blender environment / route modeling lane

## 结论

Batch 6 已从纯队列状态推进为 `1/12` 部分产出。首张成功素材是中央工作院落的人眼
高度参考，补充高空总览无法清楚表达的四向路线出口、建筑侧墙/后墙、侧门、台阶、
坡式门槛、排水沟、工作棚、石铺地和可复用农村道具。

私有素材：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
  environments/design-route-central-courtyard-eye-01.png
```

清单与实际提示词：

```text
candidate-sources-batch6.json
prompts/design-route-central-courtyard-eye-01-independent.txt
```

## 字节证据

```text
format: PNG
dimensions: 1536 x 1024
mode: RGB
bytes: 3,686,417
sha256: 19b40a84322ab7d343716bd684fc83a3207ae42ad94993d28446707f7a5537df
```

内置接口没有暴露可机器验证的实际模型 ID，因此清单保持：

```json
{
  "actual_model_id": "unknown",
  "integration_status": "staged-not-registered"
}
```

## 生成方式

这次成功请求是独立生成，没有提交 overview 图作为图像输入。原因不是试图规避一致性
要求，而是此前 reference-edit 与 independent-generation 端点均经历网络错误。实际提示词
因此单独保存，并将本记录明确写为：

```json
{
  "generation_mode": "independent-generation-after-service-recovery",
  "design_intent": {
    "relationship": "independent-single-view-environment-reference"
  }
}
```

紧随其后的桥底/水车连接视角请求再次返回 generation 网络错误，没有输出，也没有写入
清单。

## 视觉检查

- 人眼高度、宽画幅和近景尺度成立；
- 中央院落与左侧桥、水车、溪流及上坡密集村落关系可读；
- 左侧下行台阶、桥向路线、中央穿堂、右侧上坡台阶等路线开口可读；
- 石铺地、排水明沟、挡墙、工作棚、陶罐、篮筐、工具和水车可拆为通用模块；
- 建筑体量、墙面、屋顶和道具存在变化，不是重复复制；
- 未发现人、车辆、文字、标签或水印；
- 路线没有被道具完全封堵。

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

- 它不是 overview 图的已知新机位，不能与 overview 拼成 SfM、NeRF 或 3DGS 训练集。
- 图中的四个出口只是设计语义，不是已验证的 scene graph。
- 尺寸、坡度、净空、碰撞和闭环必须由 Blender 几何与 topology plan 重新挣得。
- 180 相机的正反面、有效像素和共视结论只能来自真实六层渲染。
- 当前干净 Release 不追加这张候选中间态。

## 建议消费

1. 把院落拆成通用模块：排水石铺地、中央穿堂、上坡台阶、下坡水车连接和侧向梯田出口。
2. 给各出口落实际 topology node/edge、宽度、净空、坡度和碰撞证据。
3. 把工作棚、陶罐、篮筐、工具和挡墙作为可替换 prop/structure variant，而非固定地标。
4. 通过正式 180 相机和 instance/semantic/depth/normal 层验证遮挡与覆盖，不读取图片文件名
   推导结论。

## 已完成核验

- Batch 6 queue JSON 和 candidate manifest 可解析；
- queue 状态为 `partial-output-1-of-12`，1 个成功、11 个待生成；
- PNG 解码、尺寸、模式、字节数和 SHA-256 与清单一致；
- 实际提示词文件存在；
- 清单与队列均未提升 geometry trust；
- 候选素材保存在项目私有可替换区，没有混入 Git 或最终 Release。
