# 设计、计划与验证历史

本页合并 2026-07-14 至 2026-07-24 已完成或被替代的内部 plan/spec/verification。
精简前完整原文可从 Git commit
`381f243ebed3bb8dcc0e47608ac1548c55e8c621` 读取。

## 已交付能力

- Studio immutable jobs、严格 ingest、recovery 与 read-only capability snapshot；
- synthetic village、四面建筑、PBR/材质包、近场 mesh、环境与高架拓扑；
- deterministic infinite chunks、LOD、boundary continuity 与大重建流式 Viewer；
- weather/zoom、coverage HUD、分块点预算与 fallback 状态；
- production camera preflight、六层渲染、post-render、repose 和 journal；
- registration-quality、云训练 provenance、artifact integrity 与 import contract；
- Windows/macOS/Linux runtime 与素材可复现性审计。

## 长期有效结论

- synthetic、mock、design-only、preview-only 不能变成 measured 证据；
- `check_capture` 不能测跨图重叠；COLMAP 注册覆盖必须单独报告；
- 高质量 3DGS 训练是外部 GPU 能力，本仓库负责诚实调用和产物消费；
- GPS 通常只有米级精度，sub-metre 需要实测控制点；
- 分块与 LOD 不改变坐标或 provenance；
- Renderer 能打开文件不等于六层、可见性或真实 Viewer QA 已通过。

## 仍保留的近期详细计划

- registration/SfM quality policy；
- cloud training provenance；
- roaming graph Viewer/Studio。

其它历史细节只在排查回归或追溯设计决策时从 Git 历史读取。
