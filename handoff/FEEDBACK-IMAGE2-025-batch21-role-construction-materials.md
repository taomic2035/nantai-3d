# FEEDBACK-IMAGE2-025 — Batch 21 角色构造与模拟材质参考

> 日期：2026-07-23
> 生成：Codex 使用 OpenAI built-in imagegen
> 状态：`8/8` 已生成、目视复核、逐字节登记、远端回下载复验并形成干净 Release

## 结论

Batch 21 不再增加普通村景，而是为 Batch 20 暴露的桥、水车和森林三个正式角色补充构造背面、
支撑系统、水路机械关系与可行走路线细节，并提供两张可替换的模拟 albedo 原型。它们能指导下一轮
Blender 几何/PBR 制作，但仍是独立 `design-only` 输入，不建立共享相机、像素对应、米制尺度、
真实纹理或 360° coverage 信任。

私有候选与 QA：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch21/
```

干净 Release：

- tag：`synthetic-village-design-inputs-batch21-2026-07-23`
- archive：`synthetic-village-role-construction-material-pack-batch21-2026-07-23.zip`
- archive bytes：`28335859`
- archive SHA-256：`cabfe3f7f080e15030d2400a4b1a976f4c739b149716262a0a3e88bf78721d84`
- URL：`https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch21-2026-07-23`

Release 只包含 8 张最终 PNG、8 份精确 prompt、`manifest.json`、`USAGE.md` 和
`PAYLOAD-SHA256SUMS.txt`。私有 contact sheet、generation queue、candidate manifest 和旧批次没有入包。

## 素材身份

| file | size | bytes | SHA-256 | 主要用途 |
|---|---:|---:|---|---|
| `design-construction-bridge-downstream-soffit-01.png` | `1536×1024` | `3757812` | `4bae8d5144dc5ad710813dae622e746a1c62de171a8c3ad2cfecbbd171dce261` | 拱腹、券石、排水、背桥台、回返楼梯与岸路 |
| `design-construction-bridge-upstream-dogleg-01.png` | `1536×1024` | `3849966` | `12219d5593e0d9c88e3c8753391fb8e0bb2aae33f29c50307dcc3df8e072b7c8` | 拱圈、桥台、栏墙、折线入口、台阶与底部支撑 |
| `design-construction-watermill-service-machinery-01.png` | `1536×1024` | `3534934` | `571c65e6046078ea8cc816534eb6f89cbee5de3011c2ac743988fc689f56d338` | 水轮、轴承、机架、引水槽、闸门与检修平台 |
| `design-construction-watermill-tailrace-rear-01.png` | `1536×1024` | `3429172` | `c4cdeafb96d8676da80931dce585b4f2123aab3b6d9857c44fc6968ec3945e09` | 后轴承、上层引水、楼梯支撑、涵洞与尾水出口 |
| `design-construction-forest-switchback-culvert-01.png` | `1536×1024` | `3846289` | `31eddb5acb9cf13e80108f6ada8be28ec66708dce277da9d21a531b238ab946c` | 三级折返、挡墙、涵洞、果园、护栏与返村支线 |
| `design-construction-forest-village-return-01.png` | `1536×1024` | `3954007` | `9cce56f8719be72d36af1c90c8abb93bf55e76ea9b92c832b4efa702ecee7948` | 墙背/基础、楼梯底部、排水口、涵洞出口与下层回路 |
| `design-material-fieldstone-albedo-prototype-01.png` | `1254×1254` | `3718983` | `86cf5037a42d3e3df44ad3e5856afff7ff42ab4c20ce59a1f034ab198a0c87f2` | 模拟石墙颜色输入；平铺与物理尺度未验证 |
| `design-material-weathered-timber-albedo-prototype-01.png` | `1254×1254` | `2875951` | `970b2194d626002a809f07f50df1a621203b2122918d2a76c89ada7487b5614a` | 模拟旧木颜色输入；平铺与物理尺度未验证 |

私有 QA contact sheet：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch21/contact-sheet-batch21.png
SHA-256 = de38547cf7438f61caf19cce0ece97084e69423f0fcf9844909009fae015df0a
```

## 目视与机器复核

- 八张图均无可见文字、水印、人物、动物、车辆或现代线缆。
- 桥的两个方向共同暴露拱圈/拱腹、桥台、排水、折线路线和楼梯支撑。
- 水车的服务侧与后侧共同暴露 wheel/shaft/bearing/flume/sluice/tailrace 的机械和水路关系。
- 森林两个方向共同暴露三层折返、挡墙背面、涵洞两端、排水、果园平台与返村分支。
- 两张材质原型为方形正视、均匀照明；无缝性、真实比例、色彩和 PBR 其它通道仍未知。
- GitHub 远端 archive 已重新下载；外层 SHA、19 个 ZIP 条目和 18 条 payload hash 全部通过。

## 下一轮 Blender 消费优先级

1. **水车机械/水路闭环**：先把 wheel、shaft、bearing、frame、flume、sluice、tailrace 做成独立对象，
   解决当前“有水车轮廓、没有可信驱动关系”的最大视觉缺口。
2. **桥下构造与折线入口**：补 arch ring/soffit、双侧 abutment、排水、岸梯和底部支撑，优先改善
   downstream/upstream 平移相机的近中景。
3. **森林折返基础设施**：补三层挡墙、涵洞、排水口、楼梯底面和返村支路，避免森林角色退化为
   单一路径与大面积坡面。
4. **材质只作为 provisional albedo**：建立真实 UV 后再消费；normal/roughness/displacement 必须
   独立生成或测量，不允许从文件名推断已具备完整 PBR。
5. 每次几何/落位变化后重建 plan/registry/exact-218，并重跑 fresh Phase 4.3、六角色 preflight、
   六层、target visibility 与 post-render v2；正式质量阈值不得因参考图好看而放宽。

## Fail-closed 边界

- `synthetic=true`
- `stage=design-only`
- `camera_calibration=unknown`
- `geometry_consistency=not-verified`
- `metric_scale=unknown`
- `training_use=forbidden-as-multiview`
- `coverage_use=forbidden`
- `trust_effect=none`
- 材质额外为 `simulated-albedo-prototype-only`；`normal/roughness/displacement=absent`

本批次推进的是合成 Blender 场景的构造完整度和可替换视觉输入，不是图片到真实 3D 的重建结果。
真实模型/真实纹理仍需真实采集、相机标定/COLMAP、外部 CUDA 训练或摄影测量纹理流程。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
