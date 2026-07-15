# third/ — 第三方重建工具（下载物，不入库）

本目录放**外部重建工具的二进制**（由 `.gitignore` 忽略，仅本 README 入库）。
用途与端到端手册见 [`../docs/manual/reconstruction-setup.md`](../docs/manual/reconstruction-setup.md)。

## 清单

| 子目录 | 工具 | 版本 | 下载 URL |
|---|---|---|---|
| `third/colmap/` | COLMAP（相机位姿 SfM，**no-CUDA/CPU**）| 4.1.0 | https://github.com/colmap/colmap/releases/download/4.1.0/colmap-x64-windows-nocuda.zip |
| `third/brush/` | Brush（无 CUDA 的 3DGS 训练器，wgpu/Vulkan；⚠️ 集显仅试验档）| v0.3.0 | https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-pc-windows-msvc.zip |

## 重新获取（GitHub 可达时）

```powershell
# COLMAP（无 N 卡用 nocuda 版；有 N 卡想提速可用 cuda 版）
curl -L -o third\colmap.zip https://github.com/colmap/colmap/releases/download/4.1.0/colmap-x64-windows-nocuda.zip
Expand-Archive third\colmap.zip -DestinationPath third\colmap -Force

# Brush（无 CUDA 训练器）
curl -L -o third\brush.zip https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-pc-windows-msvc.zip
Expand-Archive third\brush.zip -DestinationPath third\brush -Force
```

> GitHub 不可达时请手动从上表 URL 下载并解压到对应子目录。
> 云 GPU 训练（nerfstudio/gsplat）**不在此目录**——在你租的云机上跑，见手册 §5。
