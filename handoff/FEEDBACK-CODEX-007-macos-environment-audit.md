# FEEDBACK-CODEX-007 — macOS 本机构建与重建环境审计

> Codex（UX / audit lane）→ Opus（pipeline / toolchain lane）
> 日期：2026-07-17
> 主干基线：`858c367`

## What

在 macOS 26.5.2 / arm64 / 32 GB 机器上完成了可行依赖的安装与真实运行探针：

- Homebrew COLMAP 4.1.0；`colmap version` 明确为 without CUDA，`make.py doctor`
  判定 CPU SfM 可用。
- 官方 Brush 0.3.0 Apple Silicon release 安装到 `third/brush/brush_app`；归档
  SHA-256 为
  `65b2631398c839be3c1d4d7160fe2326389dec87830aac0710985e6690a1048c`，
  `brush_app --version` 返回 `brush-cli 0.3.0`。
- 官方 Blender 4.5.11 LTS Apple Silicon DMG 安装到
  `/Applications/Blender.app`；DMG SHA-256 为
  `1fad76c7da9451c7d6db99f1a5ed3c0a1a461d0aa07bf2b639e2fb4804ca4f13`，
  codesign 有效，headless version 返回 build hash `4db51e9d1e1e`。
- MLX 0.32.0 / mlx-metal 0.32.0 隔离安装到
  `.nantai-studio/venvs/mlx`；实际矩阵运算返回 `Device(gpu, 0)`，证明 Metal
  路径可执行。
- FFmpeg / ffprobe 8.1.2、项目 Python 必需与可选依赖均可执行。
- HANDOFF-002 Mac 重生成后，九份 PLY 与现有 registry SHA 逐字节一致，已作为
  本机 ignored payload 安装；`doctor --verify-assets` 由 0/11 提升到 **9/11**。
  `stone_wall_01` 与 `fence_wood_01` 仍不一致并继续 fail-closed。

安装后完整门禁：

```text
Python: 950 passed, 91 skipped, 1 deliberate non-finite warning
Viewer: 106 passed
Studio: 73 passed
```

随后已同步远端至 `858c367` 的 coverage evidence 更新；该批只修改 Viewer coverage
校验、测试与文档，不改变上述工具链结论。同步后 Viewer + Studio 最新合并门禁为
`183 passed, 0 failed`，Ruff 也为 `All checks passed`。

## Why

用户要求先把 Mac 上能安装的环境补齐，同时必须如实区分：

1. “二进制存在”；
2. “项目 doctor 能真实执行”；
3. “被正式内容寻址工具链接受”。

MLX、MPS 或 Metal 只能提供 Apple GPU 计算后端，不会自动实现
gsplat/nerfstudio 的 CUDA 自定义算子。当前本机最直接的非 CUDA 3DGS 路径是
Brush；正式生产训练仍应走云 NVIDIA GPU。

## Tradeoff

- 没有把 Mac Blender 伪装成 `third/blender/blender.exe`。当前
  `tools.lock.json`、`LockedTool.platform`、receipt、canary host 与 runtime tests
  都精确锁定 Windows x64；软链接或改名会绕过平台身份与可执行摘要约束。
- 没有在项目 `.venv` 安装未被仓库消费的 PyTorch MPS。MLX 已满足 Apple GPU
  探针需求，Brush 又是现有直接训练路径；额外安装大型框架不会让 CUDA-only
  训练器变得可用。
- 没有用 Mac SHA 重新登记 `stone_wall_01` / `fence_wood_01`。这会把仍未解决的
  跨平台漂移伪装成可信新基线。
- 只安装了九份与现有 registry 完全一致的 payload；另外两份继续拒绝加载，
  因而当前素材状态诚实地保持 degraded，而不是“完整可用”。

## Open Questions

1. 正式 Blender 锁是否要扩展为按平台选择的不可变 variant（Windows x64 +
   macOS arm64），同时分别钉住 archive SHA、运行时首行、build timestamp、
   executable digest 与 receipt platform？
2. HANDOFF-002 的量化生成器在当前 Mac / NumPy 2.3.5 上仍让
   `stone_wall_01`、`fence_wood_01` 漂移；现有 CI 仅比较 Ubuntu/Windows，
   未覆盖 macOS。应修生成器后统一 rebaseline，还是先发布两份权威
   content-addressed payload？
3. 是否将 macOS 加入素材 reproducibility matrix，并上传经过 manifest 校验的
   PLY artifact，供新 Mac 环境按 SHA 恢复，而不是本机重算？

## Next Action

请 Opus 优先处理两个 toolchain 边界：

1. 设计并 TDD 实现多平台 Blender lock/receipt/canary executable 解析，保持现有
   absent-only、无链接、内容寻址和运行时身份门禁；不要只增加一个 PATH fallback。
2. 把 macOS 纳入 HANDOFF-002 可复现门，定位两份 PLY 的残余数值漂移；在修复前，
   可先从已验证 Windows/Linux 主机发布与 registry SHA 完全一致的两份 payload。

Codex 后续可 review Mac variant 的诚实 UX、安装错误呈现及 Studio capability 投影。
