# Nantai 3D Studio

照片/视频重建外围管线与浏览器 3D 漫游工作台：负责采集预检、COLMAP 位姿、
外部 Gaussian Splat 导入、坐标/尺度、分块/LOD、Studio 和 Viewer。

当前版本为 **Nantai 3D v1.0.0-preview.2**：一个可独立校验、可从干净机器运行的
synthetic 3D 场景包。默认首屏是可辨认的整村简化 PBR 模型，也可切换到
67,858 个 Gaussian 的 3DGS / 点云 LOD 后备；25 个世界分块和 11 个 registry
素材一并打包。

它不是照片重建，也没有真实照片纹理或米制对齐。场景可信度保持
`synthetic / preview-only / arbitrary / unaligned`，发布包校验成功不会提升这些事实。

## 先看效果

按 [Preview2 下载、校验与干净安装说明](docs/releases/1.0-preview.2.md) 下载唯一的
runtime ZIP，先校验 SHA-256，再解压、安装并运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe make.py serve
```

打开 <http://127.0.0.1:8000/web/studio/>。

[Preview1（历史版本）](docs/releases/1.0-preview.md) 仅保留旧发布的可追溯说明；
不要用它判断 Preview2 的包内容或运行流程。

从源码生成 synthetic 世界：

```powershell
.\.venv\Scripts\python.exe make.py assets world
.\.venv\Scripts\python.exe make.py serve
```

Linux/macOS 将虚拟环境路径替换为 `.venv/bin/python`。

## 能力与边界

| 能力 | 当前状态 |
|---|---|
| 图片/视频摄取 | 归一化、抽帧、质量预检、source/session 绑定 |
| COLMAP / SfM | 可消费真实位姿；注册质量必须单独验收 |
| 3DGS | 外部训练，本仓库负责导入、拼接、高阶 SH、分块与 LOD |
| 坐标与尺度 | Sim3、控制点/GPS、ENU；证据不足时保持 arbitrary/unaligned |
| Studio / Viewer | provenance 状态、Spark/fallback、分块加载与漫游 |
| Synthetic 场景 | 可替换素材、确定性世界与 Blender 质量门 |

高质量 3DGS 训练需要外部 NVIDIA GPU。本机无可用 CUDA；Brush 仅适合受限小场景。

## 真实场景工作流

```text
重叠照片/视频 → check-capture → COLMAP SfM → 外部 GPU 3DGS
→ 导入/分块/LOD → 控制点或 GPS 对齐 → inspect-recon → Viewer QA
```

开始前：

```powershell
.\.venv\Scripts\python.exe make.py doctor
$env:PHOTOS = "input"
.\.venv\Scripts\python.exe make.py check-capture
Remove-Item Env:PHOTOS
```

真实场景仍需五项验收：真实重叠采集、accepted real-photo SfM、非 mock GPU 3DGS、
实测米制对齐、真实 Viewer QA。`check-capture` 不能测跨图重叠，synthetic/mock
也不能提升这些信任。

## 验证

```powershell
.\.venv\Scripts\python.exe make.py test lint
git diff --check
```

## 文档

- [Preview2 下载、独立校验与运行](docs/releases/1.0-preview.2.md)
- [真实重建端到端手册](docs/manual/reconstruction-setup.md)
- [真实数据与 Sim3/ENU 契约（完整源码仓库）](https://github.com/taomic2035/nantai-3d/blob/v1.0.0-preview.2/docs/real-data-workflow.md)
- [Synthetic 素材 Releases](docs/releases/synthetic-design-inputs.md)

可信度只从机器字段、内容 SHA、transform history 和实测报告推导；未知或矛盾证据
一律 fail closed。
