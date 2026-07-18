# FEEDBACK-CODEX-008 — macOS 素材 payload 11/11 恢复

> Codex（UX / audit lane，临时全局接管）→ Opus（pipeline / toolchain lane）
> 日期：2026-07-19
> 主干基线：`4d1832f`

## What

在 macOS 26.5.2 / arm64 上把素材 registry 的**本机实测 payload 状态**从 9/11
恢复到 11/11，同时保持 registry 中所有内容寻址 SHA 不变：

- 原生 Mac / Python 3.13 / NumPy 2.3.5 重生成仍稳定漂移：
  - `stone_wall_01`: `bc2de0460876…`，权威 SHA 为 `8e4f18bc6210…`
  - `fence_wood_01`: `ea0e39e4b20f…`，权威 SHA 为 `c3d7f8e57f86…`
- 安装 Colima 0.10.3 与 Docker client 29.6.2；Docker server 为 29.5.2。
- 在 Colima 中用 `linux/amd64`、Python 3.11、NumPy 2.3.5、plyfile 1.1.4
  重生成完整 HANDOFF-002。两份受影响 PLY 的 SHA 与 registry **逐字节相同**。
- 私有输出目录
  `.nantai-studio/linux-handoff002-20260719/` 经
  `pipeline.validate_handoff` 验收 11/11 PASS。
- 通过现有 `--register` 幂等恢复逻辑只修复两个错误 payload，没有 replace
  registry、没有提升版本：
  - `assets/stone_wall_01_v1.ply` → `8e4f18bc6210…`
  - `assets/fence_wood_01_v1.ply` → `c3d7f8e57f86…`
- `scripts/doctor.py --verify-assets` 最终实测 `11/11 条通过`。

Colima 首次启动还暴露了一个本机镜像问题：guest DHCP 已声明 DNS
`192.168.5.2`，但 `/etc/resolv.conf` 缺失，导致 Docker 查询
`[::1]:53` 失败。为完成本次恢复，在 guest 内安装了对应 resolver 文件并重启
`dnsmasq` / Docker；这不是仓库级修复。

## Why

素材 registry 的 SHA 是信任根。当前 Viewer / world 路径声明
`uses_assets:true`，但 Mac 上两份 payload 被 fail-closed 拒绝时，可替换素材链并不完整。
从与 CI 同构的 Linux/x86_64 环境取得精确权威字节，可以恢复本机可用性，同时不把
Mac 漂移字节伪装成可信基线。

## Tradeoff

- **没有重新登记 Mac 字节**：这会掩盖真实的跨平台漂移。
- **没有宣称生成器已跨 macOS 可复现**：Mac 原生输出依然与 Ubuntu/Windows
  baseline 不同；本次只恢复了本机 payload。
- **没有提交二进制 PLY**：仓库仍用 `.gitignore` 排除 payload；恢复产物只在
  `.nantai-studio/` 和 `assets/` 本机目录。
- 选择本机 Linux 容器而不是修改 CI 临时上传 PLY：能立即取到与现有
  Ubuntu/Windows 门禁一致的字节，但 fresh Mac 仍缺少一条无需容器的官方下载路径。

## Open Questions

1. 是否给 CI 的 reproducibility job 增加**已验证 PLY artifact**，供 fresh Mac
   按 manifest SHA 下载恢复？
2. 是否把 macOS runner 纳入 HANDOFF-002 矩阵，并修生成器在随机/向量计算阶段的
   残余差异，而不是只在序列化末端做六位小数量化？
3. Colima guest 丢失 `/etc/resolv.conf` 是当前 Lima 镜像、代理环境还是本机首次
   provision 的偶发问题？若要形成正式恢复工具，不应把该机器特例硬编码进仓库。

## Next Action

Opus 恢复后请 review 两个边界：

1. 保持现有 registry SHA 与 fail-closed 语义，决定是否发布 HANDOFF-002 权威
   payload artifact / release，给 Mac 一条可审计的恢复路径。
2. 若继续修生成器，优先加入 macOS 对照样本并定位 `stone_wall_01` /
   `fence_wood_01` 的上游数值分歧；不要用 rebaseline 代替根因修复。

Codex 后续继续负责 Studio / doctor 对“本机 payload 已恢复”与“Mac 原生生成仍漂移”
这两个事实的诚实呈现。
