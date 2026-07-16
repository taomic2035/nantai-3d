# 2026-07-16 · pipeline 可复现性审计(三维度, 实测取证)

> 执行：Opus(架构/管线 lane)。触发：在 render_chunk_to_ply.py 发现 6 处未播种全局
> np.random 违反本仓库可复现性核心价值后, 系统清查同类隐患是否在管线其它地方存在。
> 方法：grep 全 `pipeline/` + 针对性实测(重复运行比 sha / 崩溃 traceback / 量化边界)。

## 结论 TL;DR

三维度审计完成。render 路径的 6 处全局 RNG 是**孤例**(其余随机源全部正确 seeded);
发现并修复了 world manifest + layout JSON 的跨平台 CRLF 隐患; 大坐标 float32 量化是
documented limitation(实际范围内精度充足)。无其它 Opus-lane 可复现性 bug 遗留。

## 维度 1：随机源 / 熵源 —— CLEAN

grep 全 `pipeline/` 的 `np.random.*` / `random.*` / 内置 `hash(` / 时间源。除已修的
render 路径外, 所有随机源均为**本地 seeded** 确定性生成器:
- `mock_layout.py:45-48` `_rng` = `random.Random((world_seed*100003+cx*1009+cy)&0xFFFFFFFF)` — 掩码种子, 负索引安全。
- `reconstruct.py:188` / `registration.py:330` / `mock_assets.py:48` / `assets.py:609` / `gaussian_scene.py:693` — 均 `np.random.default_rng(<seeded>)` 本地实例。
- `glm_client.py:95` `hash((chunk_x,chunk_y,world_seed))` — 整数元组 hash **跨进程稳定**(Python hash 随机盐只作用于 str/bytes/datetime, 整数元组恒定; 前经验证始终 59811), 非 bug。
- `synthetic_village/scene_plan.py`(codex lane) — `np.random.Generator(PCG64(seed))` 本地 seeded。

**判定**：render 路径的 6 处全局 `np.random`(已修 `e96c8a9`)是孤例, 非普遍问题。代码库整体遵守可复现性。

## 维度 2：字节序列化 —— 发现并修复 CRLF 隐患(`3a801d1`)

trust root 已正确强制 LF：`reconstruct.py:534,843,849`(registration/recon_manifest/sha256)、
`registration.py:672`、`alignment.py:345` 均 `newline="\n"`。

**但**以下 `write_text(...indent=2, encoding="utf-8")` 缺 `newline=""` → Windows 写成 CRLF, 跨平台字节分歧:
- `render_chunk_to_ply.py:635` — world manifest(render-on-demand 数据契约)
- `generate_world.py:59,101` / `chunk_scheduler.py:104` / `mock_layout.py:229` — layout JSON

layout 的 CRLF 会破坏 render-on-demand 的 **layout 缓存跨平台/跨进程一致性**
(chunk_scheduler.get_or_generate 把 layout 持久化到磁盘)。全部加 `newline="\n"`
与 trust root 惯例统一。TDD 回归测试 `test_manifest_and_layout_written_lf_not_crlf`(Windows 上先红后绿)。

## 维度 3：平台数值 —— 无 Opus-lane bug；两点须知

**大坐标 float32 量化(实测)**：ply/viewer 坐标为 float32('f4'), world_offset=cx*200。
实测 render_single_chunk 在各尺度的最小可分辨间隔:

| chunk 索引 | 世界距离 | 实测最小可分辨间隔 |
|---|---|---|
| ±1,000 | ±200 km | 0.016 m |
| ±10,000 | ±2,000 km | 0.125 m |
| ±100,000 | ±2 万 km | 2.0 m |
| ±1,000,000 | ±20 万 km | 16 m |

实际地理漫游范围(chunk ±10⁴ = ±2000km)float32 精度充足(≤0.125m)。仅在绕地球尺度以上
才明显量化——这是 float32 世界坐标的**固有限制**, 彻底解决需 viewer 端 world-offset 分离
(chunk-local float32 + double world origin), 属 viewer 坐标系架构(**codex lane**), 非 Opus-lane bug。
render-on-demand 缓存/漫游文档应注明有效范围 chunk ±10⁴。

**libm 跨平台(sin/cos)**：`_emit_building` 的旋转与 mock_layout 布局用 `np.cos/sin`。同平台确定
(维度 1 已证跨进程字节一致), 但 mac↔win↔linux 的 libm 微差是既有 **HANDOFF-002** 关注点
(那批 mock-asset 已 quantize 到 1e-6 缓解)。render 路径的 sin/cos 是否需同等量化**须双平台确认**,
本机(单平台)无法坐实; 若 render-on-demand 缓存要跨异构 worker 共享, 须先解 HANDOFF-002。

## 边界声明

审计覆盖 `pipeline/` 的随机性/字节序列化/平台数值三维度, 所有坐实项均有实测证据。
未覆盖: codex 保护区(studio_server/studio_jobs/studio_ledger/web)的内部逻辑(只在其与
manifest 契约的边界处审计)、真实素材(registry)跨平台字节(见 HANDOFF-CODEX-003 §4, 同平台已证确定)。
