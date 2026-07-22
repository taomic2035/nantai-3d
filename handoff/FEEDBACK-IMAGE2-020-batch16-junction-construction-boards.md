# FEEDBACK-IMAGE2-020 — Batch 16 路口与构造收口参考板

> 日期：2026-07-22
> 生成：OpenAI 内置图像生成，经 Codex `imagegen`；每个素材一次独立调用
> 状态：`8/8` 已生成、落盘、视觉复核并完成内容寻址；仅在私有可替换候选区

## 1. 结论

Batch 16 新增八张通用构造板，将已有整景和模块板继续拆到 Blender 可以明确建模的连接与
收口层级。前两张直接回应 fresh Phase 4.3 中已定位的
`gallery-branch-attachment-side-001 ↔ roadside-vegetation` 冲突：一张表达无遮挡廊道侧入口，
一张表达路径交叉口的确定性植被退让。其余六张补齐基础、排水、高差、柱脚、栏杆和檐沟。

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch16/
```

本批使私有原始设计输入从 `94` 增至 `102`。数量增长只表示参考覆盖增加，不表示 Blender
几何已实现、碰撞已通过、真实纹理已接入或任意坐标漫游已完成。

## 2. 素材身份

| 素材 | bytes | SHA-256 | 建模用途 |
|---|---:|---|---|
| `design-construction-gallery-side-entry-clearance-01.png` | `3100680` | `be0a780f7b4c6d0c743cd723ed8660ea82376720618c6ca0f93b42c5352d0cb1` | 廊道侧入口、门洞净空、植被退界 |
| `design-construction-vegetation-junction-opening-01.png` | `3133821` | `ee75bead6aebb613cabf8affa9d37c2303e8d33d88800b51f46506400dffd525` | 主/支路连续、植被分段、路口退让 |
| `design-construction-stone-plinth-foundation-01.png` | `3360053` | `c630d922977081bd3ffcacebfbf92290d0391ab106421ff0a53424e6c1ac8cff` | 石基、木槛、坡地接触、碎石排水 |
| `design-construction-path-drainage-transition-01.png` | `3165845` | `e29b156984b4d535ff525ea5b801a5845faa8d534b6e2a254239fd85d7c16af8` | 步道跨沟、涵洞、上下游收口 |
| `design-construction-ramp-stair-transition-01.png` | `3329751` | `3d641dc5333167527a27625bb41dce012e5e86a6e3c70737486902039f4095f6` | 坡道、短梯、平台、挡墙 |
| `design-construction-timber-post-base-01.png` | `2987059` | `6152f2ac3c15d5c6abee51e0ab7253d4f64be9dc3cebdadc54f456fa6435ec65` | 木柱脚、石靴、斜撑、隔潮和排水缝 |
| `design-construction-railing-termination-01.png` | `2967886` | `d94740589b79d515a2dd6d04bf238d4a83bb46b31303bffd0a04a6ad290be5c5` | 栏杆端柱、回接、相邻开口 |
| `design-construction-eave-gutter-closure-01.png` | `3196706` | `6935820e328db2dada2776b3fb4e3485ff2ea1df1607c39ee8e34498d7da330e` | 瓦口、椽头、檐沟、落水和墙根溅水控制 |

八张图片均为 `1536×1024`、`RGB PNG`。完整 prompt、prompt SHA/字节数、图片元数据、
生成队列和私有 QA contact sheet 位于同一 Batch 16 目录；机器闭环结果为：

```text
batch16_closure=PASS
candidates=8
prompts=8
images=8
queue_complete=8
metadata_and_sha=8
contact_sheet=PASS
```

生成接口没有返回可机器核验的具体模型 ID：

```text
actual_model_id=unknown
```

## 3. 视觉复核

八张图均采用四面板组件板构图。人工复核确认：

- 无人物、可读文字、水印、logo、尺寸线或箭头；
- 入口、路径、排水与高差关系在画面中可辨认；
- 材料语言与现有合成山村相容，但不依赖某一处场景坐标；
- 侧入口和植被路口两张均把步行开口画成连续、无遮挡状态；
- 每张的面板仅表达同一设计意图，不能证明逐点一致的同一三维物体。

## 4. Phase 4.3 消费边界

GLM/Codex 可把前两张作为 junction 几何的视觉参考，但正确修复仍必须在代码和 Blender 中：

1. 对正式 junction 净空包络内的 `roadside-vegetation` 做确定性开口或分段；
2. 保留 `terrain-conform-ribbon` 连续路面与可走连接；
3. 保持 stable instance `204`、材料与 semantic identity；
4. 用 fresh exact-218 build 和 Phase 4.3 BVH probe 证明零交叉。

这些 PNG 不允许替代 BVH、clear-width/height、拓扑、production journal 或 post-render
quality gate，也不能据此白名单 `path-network-003`。

## 5. Fail-closed 边界

所有素材统一保持：

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
orthographic_projection=not-verified
watertight_topology=not-verified
texture_use=design-reference-only-not-a-texture
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

因此它们不能证明工程尺寸、结构安全、无穿插拓扑、真实照片纹理、SfM/3DGS coverage、
360° 一致性或任意坐标可达性。候选图、prompt、manifest 和 contact sheet 不进入 Git、
registry 或 Release；真实消费产物应绑定独立 mesh/material/plan/build SHA。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
