# FEEDBACK-CODEX-009 — Scheme A 多尺度地表真实感回执

> Codex（UX / audit lane）→ Opus（pipeline / architecture lane）
> 日期：2026-07-19
> 基线：`6e89c13`

## What

用户批准的 Scheme A 已按
`source-consistent-multiscale-surface-v1` 落地并在真实 Blender 4.5.11 macOS
arm64 运行时验证：

- `2174e3f` 修复标准 GLB `COLOR_0` 对 HDR macro color 的钳制；导出期使用私有
  float vector，独立后处理为标准 float VEC4 后删除私有语义。
- `fd2559d` 把真实 Blender profile 和独立 GLB audit 加入 opt-in 门禁。
- `6e89c13` 增加严格、fail-closed 的近地地表质量测量内核和 Viewer 契约测试。
- 不可变 preview id 为
  `df2c782bf055741c5e0271da9fb42480162d031e016ea8b40a59aa8cff6babe2`。

真实 GLB 为 116,348 triangles、577 primitives、24 materials、
143,336,320 bytes；全部 577 个 primitive 有 texture / UV / tangent / `COLOR_0`。
晴、阴、雨、雪、雾、夜六种 Viewer 状态均实测可切换，浏览器 warning/error 为 0。

## Why

用户指出当前纹理与源图不够一致、近地真实感不足。Scheme A 的目标是先建立可验证的
底座：保留已批准源素材的 PBR 身份，以源图派生的 world-space macro color 和确定性
几何细节打破平铺重复，同时保持素材 slot 可替换。

这条路线没有改变 provenance：产物仍是 `synthetic=true`、L0、
`local-preview-only`、`preview-only`。它也没有把 mesh 天气效果、程序化无限村庄或
真实照片/视频 3DGS 混为一谈。

## Tradeoff

- 采用 private-to-standard `COLOR_0` 导出桥接，换取 HDR 数据完整性；独立 GLB audit
  会拒绝私有语义残留、非 float accessor 或缺失属性。
- 在 125k triangles / 580 primitives / 160 MB / 24 materials 的 canary 预算内加入
  4 m terrain、约 1 m path 采样与 18 个 detail meshes，没有无限增加近地几何。
- 冻结前 source-to-derived pilot SSIM 为 `0.94878`，但本轮未把它冒充新的完整
  `SurfaceQualityReport`。
- 三个 1024 × 576 production canary 的 valid pixel ratio 分别为
  `0.541072 / 0.586446 / 0.623940`，均低于既有 0.8 policy 并被拒绝；没有为得到
  “绿灯”降低阈值。

## Open

1. 三帧人工审计仍能看到道路/田地硬边、低模植被和屋顶大体块；主体模型真实感已成为
   比继续堆微观纹理更大的瓶颈。
2. 当前 production policy 面向全画面有效像素；地表专项比较是否应增加有证据的
   surface ROI gate，需要 Opus review，不能静默改写现有 0.8 门。
3. 3 m lag peak、细节梯度比、macro p05/p95、锚点 Spearman、匹配 Viewer/Blender
   投影误差尚未取得合规实测；完整报告继续 fail-closed。
4. 本机只验证 macOS L0。Windows L2、云 GPU 真实 3DGS、图视频混合重建和无限真实
   chunk 不在此次 sign-off 范围内。

## Next

请 Opus 恢复后 review 两个架构决定：

1. 在保持 material slot / source id / replacement contract 的前提下，优先替换或细化
   植被、屋顶、建筑轮廓，并把道路—田地—地形硬边改为可重复、可审计的渐变/过渡带；
2. 决定地表门禁沿用全画面 0.8 policy，还是新增独立的 surface ROI policy。若新增，
   必须记录 ROI 定义、适用范围与不影响 provenance 的语义。

Codex 下一步只在上述视觉瓶颈得到实质改善后重跑三个 1.6 m canary，再生成匹配相机矩阵
的 1920 × 1080 Viewer/Blender 帧并产出完整 `SurfaceQualityReport`。
