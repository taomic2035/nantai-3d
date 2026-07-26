# Changelog

## 1.0.0rc2 — Nantai 3D v1.0.0-preview.2

第二个预览版把当前可见的基础 3D 成果冻结为可独立校验的 runtime：

- 单一确定性 ZIP 同时包含源码、离线 Viewer 依赖、默认整村模型、67,858 个
  Gaussian、三级点云 LOD、25 个世界分块和 11 个 registry 素材；
- `RELEASE-MANIFEST.json`、逐文件 SHA-256 与独立 verifier 区分“包未被篡改”和
  “场景仍是 synthetic / preview-only / arbitrary / unaligned”；
- Studio 显示包校验状态与只读 Preview 范围；
- Viewer 提供分阶段加载、明确失败、重试和用户确认的高斯/点云后备；
- Batch35、私有 PBR bundles、缓存、失败候选和重复 reconstruction bytes 均不发布。

下载、校验和干净安装见
[`docs/releases/1.0-preview.2.md`](docs/releases/1.0-preview.2.md)。

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
