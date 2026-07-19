# 2026-07-19 · Scheme A 多尺度地表与近地真实感验证回执

> 执行：Codex（UX / audit lane）
> 冻结规格：
> `docs/superpowers/specs/2026-07-19-source-consistent-multiscale-surface-design.md`
> 验证基线：`6e89c13`
> 结论：**可移植的源码一致地表已落地；照片级近地真实感尚未通过。**

## 规格确认

本轮按用户批准的 Scheme A 实施，profile 固定为
`source-consistent-multiscale-surface-v1`：

- 保留已验证的 1024 PBR 素材，不重新发明一套与源图无关的颜色。
- 以源图派生的 world-space macro color 打破近地重复纹理。
- 地形网格为 `176 × 126`、43,750 triangles；道路为 1,455 intervals、
  14,550 triangles。
- 确定性加入 50 个湿痕、268 张落叶、56 组车辙、52 组碎石，共 18 个
  detail mesh objects。
- 保持 Blender → GLB → Viewer 现有链路，仍标记
  `synthetic=true`、`verification_level=L0`、`preview-only`，
  **不提升为真实重建、metric-aligned 或 3DGS**。

## 不可变产物

本机 Blender 4.5.11 / macOS arm64 真实构建得到：

- preview id：
  `df2c782bf055741c5e0271da9fb42480162d031e016ea8b40a59aa8cff6babe2`
- material bundle id：
  `9874e4c4b56c6942ab0a73186dbb15b07500cd6b6ce5d723fbfd97e54756f992`
- surface plan SHA-256：
  `8c013f8f08dfcd8bf658f8005610d601789a1db5ed0f02a9e9e11a3d01b1830e`
- GLB SHA-256：
  `b3fd4fa2bcc7677109481226405c5bbbc0044beb415f638006e755679167539d`
- Blend SHA-256：
  `a53d2a34661f1c0d9efc09b70cd63ea0d9295945f3b857aed72ac58bcd6e858a`

独立 GLB audit 结果：

| 指标 | 实测 | 规格上限 |
|---|---:|---:|
| triangles | 116,348 | 125,000 |
| primitives | 577 | 580 |
| GLB bytes | 143,336,320 | 160 MB |
| visual materials | 24 | 24 |
| detail mesh objects | 18 | 18 |

577 个 primitive 全部具备纹理、UV、tangent 与标准 float `COLOR_0`；72 张图片均
内嵌，无外部 URI。HDR macro color 实测范围为 `0.880127–1.099854`，GLB 中不残留
导出期私有语义 `_NV_SURFACE_COLOR`。

## 自动化与运行时验证

本轮按 TDD 修复 Blender 标准 `COLOR_0` 将大于 1 的值钳成 1 的导出问题，并把真实
Blender 产物重新送入独立 GLB audit。

```text
local textured preview / GLB gate: 53 passed, 1 skipped
opt-in real Blender profile gate: 1 passed, 19 deselected
surface quality unit gate: 17 passed
combined quality + surface realism gate: 28 passed
Viewer focused gate: 8 passed
Ruff / pycompile / git diff --check: passed
```

Viewer 使用不可变 preview id 打开：

```text
http://127.0.0.1:8767/web/viewer/?modelPreview=%2Fapi%2Flocal-textured-preview%2Fdf2c782bf055741c5e0271da9fb42480162d031e016ea8b40a59aa8cff6babe2%2Fmanifest.json
```

在约 1.6 m 行人高度实测晴、阴、雨、雪、雾、夜六种天气，HUD 与画面状态一致；
浏览器 warning/error 日志为 0。天气效果仍是 mesh relighting 与 atmospheric
overlay，不得描述成针对每种天气重新训练的 3DGS。

## 近地真实感门禁：未通过

冻结规格要求用三机位、匹配相机矩阵的 1920 × 1080 Viewer/Blender 帧生成完整
`SurfaceQualityReport`。本轮先对真实构建执行了三机位 1024 × 576 production
canary；三帧均被既有 `minimum_valid_pixel_ratio=0.8` 门禁拒绝：

| camera | valid pixel ratio | background pixels | 状态 |
|---|---:|---:|---|
| `camera-ground-route-001` | 0.541072 | 270,687 | rejected |
| `camera-ground-route-019` | 0.586446 | 243,924 | rejected |
| `camera-ground-route-037` | 0.623940 | 221,809 | rejected |

拒绝仅是质量过滤，`trust_effect=none-quality-filter-only`。保留下来的 RGB /
depth / normal / instance / semantic / camera 六层只可用于诊断，不是生产通过证据。

人工查看三帧还发现了更直接的视觉问题：

- 道路、田地和地形之间存在大块硬三角/矩形色界；
- 屋顶、树木和远景轮廓仍明显低模，部分屋顶像大体块或金字塔；
- 近地碎石、落叶和车辙可见，但不足以掩盖主体模型和大尺度地表过渡的合成感。

因此本轮**没有**生成或声称一份通过的完整 `SurfaceQualityReport`。以下字段仍未取得
合规实测：3 m lag peak、细节梯度比、macro color 的 p05/p95、源图锚点
Spearman、Viewer/Blender 最大投影误差。规格里的 SSIM `0.94878` 是冻结前 pilot，
不是本轮完整报告结果。

## 结论与下一门

Scheme A 已解决“纹理颜色被导出器破坏”和“近地完全没有多尺度变化”这两个基础问题，
但尚未解决“主体模型与地表大分区看起来真实”。下一轮最高价值工作不是调低阈值，而是：

1. 优先替换/细化植被、屋顶和建筑主体轮廓，并软化道路—田地—地形的大尺度边界；
2. 保持现有素材 slot、source id 和可替换契约，不把视觉改进写成 provenance 提升；
3. 只重跑三个 1.6 m canary；如全画面 0.8 有效像素策略不适合地表评估，应新增有依据的
   surface ROI 门，而不是静默降低既有 production policy；
4. canary 视觉门通过后，再生成匹配矩阵的 1920 × 1080 帧并计算完整数值报告。

Windows L2、真实云 GPU 3DGS、真实照片/视频重建和无限分块在本回执中均未验证。
