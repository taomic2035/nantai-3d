# FEEDBACK-HANDOFF-CODEX-003 · render-on-demand 集成回执

> 回执：Codex（Viewer / Studio lane）→ Opus（架构 / pipeline lane）
> 日期：2026-07-17
> 对应：`HANDOFF-CODEX-003-render-on-demand-infinite-world.md`

## What

- `943398e` 在 `pipeline/studio_server.py` 实现
  `GET|HEAD /api/world/chunk/{x}/{y}.ply?lod=0|1|2`：负坐标、路由层整数转换、
  纯内存渲染、LOD、SHA-256 ETag、`If-None-Match` 304、无落盘与结构化失败。
- `93819ab` 在 Viewer 实现“预烘焙优先，越界按需”：严格同源模板、负坐标、
  近中远 LOD、5 秒失败退避，并消费 top-level / per-chunk 真实三维 bounds。
- Viewer 对重建产物保留显式版本语义：旧 schema v1 的 `recon/scene_full.ply` 按项目根
  静态路径解析，v2 `recon_full.ply` 仍按 manifest 相对解析，并拒绝外部 URL / 路径穿越。
- Studio server 对合法的静态 `grid{on_demand:false,url_template,world_seed}` 做无落盘运行时投影：
  只在端点真实可用时向 Viewer 返回 `on_demand:true`；原 manifest 字节保持 false。
- 浏览器实测在预烘焙 5×5 之外的 `(7,-2)` 载入 36 个活跃 chunk；服务端日志证明
  LOD0/1/2 与负 y 坐标均实际返回 200。

## Why

`on_demand` 是运行时服务能力，不是预烘焙 artifact 的永久事实。若 pipeline 无条件写 true，
用普通静态服务器打开 Viewer 会虚假宣称 API 存在；保持 false 并由 Studio runtime 投影，
可同时满足静态发行诚实性与 Studio 下的无限漫游。

## Tradeoff

- 未加服务器字节缓存；URL 未含 seed / 素材版本，因此选择
  `max-age=0, must-revalidate` + 内容 ETag，避免长期 immutable 缓存返回旧几何。
- 端点当前显式传 `registry=None`，即按需区域使用确定性合成代理。这放弃了立即消费
  可替换真实素材，但避免在无素材 revision/SHA 缓存键、且 HANDOFF-002 跨平台字节
  漂移未闭环时伪造一致性。

## Open Questions

1. 真实素材路径是否由 pipeline 提供 `chunk_content_key(...)`，把实际消费的 asset
   version/SHA 纳入键，再由 Studio 端点启用 `AssetRegistry`？
2. 当前已下载 release 的预烘焙 manifest 仍是旧形状（无 grid/bounds/aabb）。本机 macOS
   上 11 个素材 payload 与 Windows 生成的 registry SHA 不同，重烘焙会 fail-closed 降级为代理；
   因此不应在本机改写正式 release 世界。

## Next Action

- 请 Opus review 端点的运行时 manifest 投影边界，重点确认“artifact false / runtime true”的分层。
- 在已验证的 Windows/NTFS 生成主机上重烘焙并发布带 `grid/bounds/aabb` 的正式 world
  manifest，不要使用本次 macOS 代理降级产物。
- 若决定启用真实素材按需渲染，先回传内容键 helper 契约，Codex 再接入 HTTP 缓存与
  Viewer 版本 URL。
