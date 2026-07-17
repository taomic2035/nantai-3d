# 2026-07-17 · /goal 复验:真实素材密度下的 360°/漫游/天气(密度断崖实时填平)

> 执行:Opus(集成 / 坐标 lane)。只读驱动本机 `studio_server` + headless chromium,
> 未写 `.nantai-studio/`、未写仓库树(除本文档)、未触碰 trust root。
> 关系:承接 [`2026-07-17-infinite-world-browser-regression.md`](2026-07-17-infinite-world-browser-regression.md),
> 闭合其中「本地 manifest 陈旧」caveat,并首次在**真实素材密度**下验证
> [`REVIEW-CODEX-003`](../../handoff/REVIEW-CODEX-003-render-on-demand-integration.md) finding #1
> (密度断崖 CRITICAL)的修复在实时 viewer 里成立。

## 背景:上一轮遗留的缺口咬人了

上一轮验证跑在 7-16 09:12 的旧 manifest 上,该 manifest **缺 `layout_engine`/`uses_assets`**。
其后服务端校验器 `_valid_on_demand_world_manifest` 收紧,现要求 `layout_engine == "mock"` 且
`uses_assets` 为 bool。用旧 manifest 复跑,`_valid_on_demand_world_manifest` 返回 None →
运行时 `on_demand: false` → **无限地图/任意坐标漫游在本地被 fail-closed 关闭**。

这不是仓库缺陷(manifest 是 gitignored 本地产物,陈旧被正确 fail-closed),而是本地测试面过期。

## 修复:用当前生成器重生成

```bash
python -m pipeline.generate_world --center --size 5
```

产出 25 chunks / 2,685,766 高斯 / 5.22s。新 manifest 通过校验器:

| grid 字段 | 旧(7-16) | 新(重生成) |
|---|---|---|
| `layout_engine` | **缺失** | `mock` |
| `uses_assets` | **缺失** | `true` |
| `world_seed` | 42 | 42 |
| `baked_extent` | — | `{x:-2..2, y:-2..2}`(负象限已烘焙) |
| `bounds` | null | 三维 bounds 齐全 |
| `_valid_on_demand_world_manifest` | **REJECT** | **PASS** |

诚实信号:重生成时 `stone_wall_01`/`fence_wood_01` 两素材 SHA-256 不匹配被 fail-closed 拒载
(回退合成代理)。端点用同一 `AssetRegistry` 做同样 SHA 校验 → 回退确定性且两侧一致 → 仍无接缝。

## HTTP 层实测(端口 8041)

- 运行时闸门:磁盘 `on_demand: false` → HTTP 响应 `on_demand: true`(静态部署不被污染)。
- **密度断崖填平(finding #1 实时验证)**:越界 on-demand tile 现为真实素材密度,不再是合成代理:

  | 坐标 | 上一轮(合成代理) | 本轮(真实素材) |
  |---|---:|---:|
  | on-demand tile | ~10,436 点 | `3/3`=109,755 · `4/4`=117,822 · `-3/-3`=118,156 · `7/7`=121,898 |
  | 烘焙 tile 对照 | — | chunk(2,2)=101,420,同密度带 |

  跨烘焙边界不再有 11× 密度突变。
- 确定性:`chunk/9/-5` 两次取字节一致,ETag = `sha256(body)`。

## 浏览器层实测(决定性)

headless chromium + SwiftShader,`WebGL 2.0` 可用,`renderer status: full_3dgs 已由 Spark 初始化`。

| /goal 能力 | HUD / 网络实测 |
|---|---|
| **360° 视角** | 「视角模式: 环绕 (F 切换)」——环绕+自由双模式 |
| **任意坐标漫游 / 无限地图** | 键盘漫游触发 **12 个新按需请求**;URL 按距离选 LOD:`2/-6?lod=0`(远粗)/`3/-5?lod=1`(中)/`4/-4?lod=2`(近全量);相机漫游至 ENU (999,-677,1021),当前 chunk (4,-4);**已淘汰 18 / 缓存命中 389** —— LRU 生效无泄漏 |
| **多种天气切换** | 六种(clear/overcast/rain/snow/fog/night)全切,`chunk_reqs_by_weather: 0`;HUD「大气叠加: 夜」 |
| 缩放 | 1.0×→2.5×,0 chunk 请求 |
| **控制台错误** | **0 条** —— 每 tile ~2MB 真实素材密度下漫游仍不卡死 |

**关键**:真实素材密度(~2MB/tile,10× 于上轮合成代理)未让 viewer 卡顿 —— 顺畅漫游、
LRU 淘汰、389 次缓存命中、零错误。密度断崖修复在实时渲染路径上成立。

## 诚实性复核(fail-closed)

- Provenance 面板全 `unknown`(requested/actual: unknown/mock;frame/units/handedness 全 unknown),
  **无一被提升为 measured/metric/aligned**。
- 天气被 Codex 诚实标注为「**大气叠加 atmospheric overlay · 非重光照 not relighting ·
  不改变 3DGS 已烘焙光照**」(commit `6a9c82f`)——不冒充实拍光照。
- 覆盖审计四层 HUD(Visibility/Geometry/SfM/Provenance)已接好,因未部署 `coverage-audit.json`
  诚实显示「coverage audit not loaded / unknown」——正确 fail-closed(REVIEW-CODEX-005 契约)。

## 测试基线

- macOS 全量 Python:**950 passed / 91 skipped / 0 failed**(本机是唯一 macOS 验证面,CI 仅 ubuntu+windows)。
- Viewer node:`web/viewer/*.test.mjs` **112 passed / 0 failed**(含 Codex 未提交的 environment.mjs 天气 WIP,未破坏任何测试)。

## 边界声明

- 浏览器验证跑在**工作树状态**(含 Codex 未提交的 `web/viewer/environment.mjs` 天气 WIP),
  结论对当时工作树成立,不等同于任一提交态。
- SwiftShader 是软件光栅化 —— 证功能正确性,**非真实 GPU 性能证据**。
- `web/data/manifest.json` 是 gitignored 本地产物;本文档记录的是「用当前生成器可产出通过校验的有效世界」
  这一可复现事实,而非某个入库文件的状态。
