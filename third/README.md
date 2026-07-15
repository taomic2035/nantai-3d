# third/ — 第三方重建工具（下载物，不入库）

本目录放**外部重建工具的二进制**（由 `.gitignore` 忽略，仅本 README 入库）。
用途与端到端手册见 [`../docs/manual/reconstruction-setup.md`](../docs/manual/reconstruction-setup.md)。

## 清单

| 子目录 | 工具 | 版本 | 下载 URL |
|---|---|---|---|
| `third/blender/` | Blender（合成场景与多层渲染） | 4.5.11 LTS | https://download.blender.org/release/Blender4.5/blender-4.5.11-windows-x64.zip |
| `third/colmap/` | COLMAP（相机位姿 SfM，**no-CUDA/CPU**）| 4.1.0 | https://github.com/colmap/colmap/releases/download/4.1.0/colmap-x64-windows-nocuda.zip |
| `third/brush/` | Brush（无 CUDA 的 3DGS 训练器，wgpu/Vulkan；⚠️ 集显仅试验档）| v0.3.0 | https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-pc-windows-msvc.zip |

## Blender：锁定、校验、安装

Blender 必须通过仓库根目录的 `tools.lock.json` 安装。安装器会限定 HTTPS 地址、流式校验
SHA-256、安全解压到同卷临时目录、在临时目录运行版本门禁，并仅发布到尚不存在的
`third/blender/`。它不会覆盖已有目录。

```powershell
# 下载锁定归档到私有缓存并安装
python scripts/setup_synthetic_tools.py blender --download

# 或使用已取得的同一归档；SHA-256 仍必须与 lock 完全一致
python scripts/setup_synthetic_tools.py blender --archive D:\path\to\blender-4.5.11-windows-x64.zip

# 只读复验现有安装，不联网、不解压、不写入
python scripts/setup_synthetic_tools.py blender --verify-only
```

不要手动把 ZIP 直接解压到 `third/blender/`；那会绕过归档路径、运行时身份和防覆盖门禁。

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
