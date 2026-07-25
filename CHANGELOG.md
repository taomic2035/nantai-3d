# Changelog

## 1.0.0rc1 — Nantai 3D 1.0 Preview

首个可下载预览版，聚焦“诚实可运行”：

- Studio / Viewer 可查看并漫游 deterministic synthetic 山村；
- 25 个空间分块及 LOD 0/1/2，可按距离加载；
- 可替换素材 registry、按需 synthetic 世界和 roaming graph；
- 图片/视频预检、COLMAP 位姿消费、外部 3DGS 导入、Sim3/ENU 对齐；
- 大重建分块流式、高阶 SH 旋转和 fail-closed provenance；
- Windows / Ubuntu、Python 3.11 / 3.13 CI。

本版本仍是 `synthetic / preview-only`。真实场景还缺真实重叠采集、
accepted real-photo SfM、非 mock GPU 3DGS、实测米制对齐和真实 Viewer QA。
本仓库不内置高质量 3DGS 训练器。

下载、校验和运行见
[`docs/releases/1.0-preview.md`](docs/releases/1.0-preview.md)。
