# Quality Gate Report — Nantai 3D takeover

Spec：`docs/superpowers/specs/2026-07-14-nantai-3d-studio-ux-design.md`  
Plan：`docs/superpowers/plans/2026-07-14-nantai-3d-takeover.md`  
原始需求：本任务用户原话（图片+视频、统一坐标、混合重建、拼接/可变清晰、Gaussian
Splat、素材替换、UX、模拟素材、Opus handoff）  
检查时间：2026-07-14 23:20 CST

## 愿景覆盖

| # | 原始需求 | Spec/计划覆盖 | 实现 |
|---|---|---|---|
| 1 | 图片 + 视频输入 | Sources + registration sessions | ✅ ingest、5 photo + 1 video / 11 poses |
| 2 | 统一 3D 坐标系 | coordinate truth model | ✅ frame/units/handedness/Sim3/history，未知时 fail closed |
| 3 | 图视频混合重建 | mixed registration + SplatInput | ✅ joint sessions、external 3DGS import、统一 target 后 merge |
| 4 | 可拼接、可变清晰 | stitch/replace/LOD | ✅ dedup、区域替换、616/2310/7700 LOD；非米制阻断空间参数 |
| 5 | Gaussian Splat | fidelity + real renderer | ✅ 标准属性 round-trip；Spark 2.1.0；DC fallback 诚实标注 |
| 6 | 素材可替换 | registry + two-phase UX | ✅ 版本/SHA/CAS/history；默认 world 11/11 实际消费 |
| 7 | UX | Studio three-column workflow | ✅ 六步工作台、provenance、inspector、drawer、bridge/local adapter |
| 8 | 生成模拟素材 | HANDOFF-001 | ✅ 11 个 deterministic 3DGS + manifest + contact sheet |
| 9 | Opus handoff/feedback | takeover protocol | ✅ `handoff/FEEDBACK-TAKEOVER-001.md` |
| 10 | Opus 暂停时独立推进且不覆盖现场 | isolated worktree | ✅ 原 `/nantai-3d` 未修改，交付 `codex/nantai-takeover` |

## 交付完整性

本次是完整的本地编排/import/查看切片，后续 GPU trainer、真实 control-point benchmark、
distortion-aware projection、离线 renderer 与 Studio 写任务均可在现有契约上扩展，不需要推翻
本次数据模型。synthetic/proxy 与 measured/full 的边界已在 UI 和 manifest 中机器化。

## 功能验收

| 要求 | 状态 | 代码 | 测试/运行证据 |
|---|---|---|---|
| COLMAP 不误标米制 | ✅ | `pipeline/registration.py`、`recon_schema.py` | registration/coordinate tests |
| transform exactly-once | ✅ | `FrameTransform`、`GaussianScene.apply_frame_transform` | coordinate/gaussian/reconstruct tests |
| 3DGS 属性保真 | ✅ | `pipeline/gaussian_scene.py` | degree-3 fixture + fidelity tests |
| 素材事务与消费 | ✅ | `assets.py`、`render_chunk_to_ply.py` | asset tests + 11/11 world manifest |
| 右手 Viewer + Spark | ✅ | `web/viewer/` | 30 Node tests + browser Spark runtime |
| Studio truth model | ✅ | `web/studio/`、`studio_server.py` | 30 Node + server tests + browser |
| 可移植交付 | ✅ | HANDOFF generator/manifest | fresh-checkout演练 |

## 设计稿与浏览器证据

- `rg --files -g '*.pen' .`：无匹配。当前环境也未提供 Pencil MCP，因此按 UX spec + HTML
  fallback 实现；这是“有 UI 改动但无 .pen 设计稿”的明确记录。
- 当前 worktree：`/Users/taomic/vibecoding/nantai-3d-codex-takeover`。
- 当前证据 URL：`http://127.0.0.1:8770/web/studio/`。
- 已在当前 Codex 任务中采集 1 张 1280×720 实现截图，并交互验证 assets inspector、LOD0、
  reconstruction layer、reset camera 与 Spark capability。未把截图掉入仓库根目录。

## Artifact hygiene

- 根目录未跟踪 `png/jpg/webm/mp4`：无。
- 正式媒体仅有 `handoff/deliverables/HANDOFF-001/previews/contact-sheet.png`。
- 生成的 PLY、world/recon 与验证输出均由 `.gitignore` 管理；registry/manifest/generator 入库。
- 敏感串扫描只命中 `ZHIPU_API_KEY=xxx` 文档占位，无凭据。

## Fresh verification

- Python：164/164 pass。
- Viewer：30/30 pass。
- Studio：30/30 pass。
- `make verify`：exit 0。
- Ruff：0 errors。
- `git diff --check`：exit 0。
- GLM schema：PASS；真实 API 因无 key 明确 SKIP。

结论：**PASS，适合提交隔离分支并请求 Opus review；不授权合并 main。**
