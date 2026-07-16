# AGENTS.md — Nantai 3D 多智能体协作上下文

> 本文件给所有协作 agent（Opus / Codex / GPT）共享项目级事实与约定。**信息以此为准，随进展更新。**

## 分工

| 角色 | 负责 |
|---|---|
| **Opus** | 整理、架构、代码逻辑、技术选型、决策；pipeline/坐标/3DGS 核心、registry、构建工具链、跨平台/可移植性、集成 |
| **Codex** | UX、呈现、设计、交互、**审计/review**；Web Viewer + Studio 层（含 studio_server.py、web/studio/*、Studio jobs/ledger）；review Opus 改动 |
| **GPT (image2)** | 素材生成、设计、图像处理（按 HANDOFF 规格；见 `handoff/`）|
| 共同 | 重难点问题分析与解决 |

## 非协商约定

- **Provenance safety / fail-closed**：可信度只从机器可验证字段推导（CoordinateFrame、内容寻址 FrameTransform id、实测 SHA、transform history、renderer capability），**绝不**从文件名/engine 名推断；未知 → 可预览但**永不**静默提升为 measured/metric/aligned。
- **不假装可以又不说实际问题**（用户明确要求，2026-07-15）：如实标注每个限制、外部依赖、真实耗时。
- **单一 main 分支，无其它分支/worktree**；多 agent 共享工作树 → **路径限定提交**（`git add <明确文件>` + `git commit -- <路径>`，禁用 `git add -A`/`commit -a`，避免卷入他人 WIP）。
- 提交仅在完成且验证（门禁绿）后；消息尾行 `Co-Authored-By`。push 时机需协调（他人可能有未推送/未提交工作）。

## ⚠️ 机器现实与重建能力边界（2026-07-15，已确认）

- **开发机无 NVIDIA GPU**：仅 Intel UHD Graphics 770 集显（无 CUDA），i7-14700 / 32GB / D盘 1.4TB。
- **本仓库不训练 3DGS**：它是重建管线**外围**的诚实封装层——摄取 → 坐标/位姿契约 → 米制 ENU 对齐（`pipeline/alignment.py`）→ 3DGS 导入/拼接/LOD/素材 → Spark Viewer（360° 漫游可用）。**把图片变成 3D 几何的两步是外部的**：
  1. **相机位姿（SfM）**：COLMAP（本机 CPU 可跑，慢；未安装则回退 mock/synthetic，非真实）。
  2. **3DGS 训练**：**仓库无训练器**。CUDA 训练器（gsplat/nerfstudio/Inria）本机跑不了。**实际主路径 = 云 GPU 租赁**；本机 Intel 集显跑 Brush 仅为受限的小场景试验档。
- **"完美"不可达**：3DGS 对天空/玻璃/水面/无纹理面有空洞与漂浮物；只能漫游拍到的体积。
- 端到端安装/使用手册（COLMAP + 云 GPU 训练 + 导入本仓库）：见 **`docs/manual/reconstruction-setup.md`**（Opus 编写中；用户配合云 GPU 账号/注册）。

## Render-on-demand 无限世界（2026-07-16，管线内核就绪，集成层待 Codex）

「无限村庄任意坐标漫游」的**管线内核已完整并对抗性验证 CLEAN**（Opus lane）：
- `pipeline.render_chunk_to_ply.render_single_chunk(cx, cy, world_seed=42, registry=None, lod=None)`
  → ply 字节（纯内存零落盘、任意含负坐标、确定性、**跨进程字节一致**可内容寻址缓存、
  LOD 0/1/2 分级省带宽、registry 真实素材路径亦已验证字节确定且纯读无副作用）。
- world manifest 已带无限网格元数据：`grid{on_demand:false, url_template, world_seed}`、全局
  `bounds`、per-chunk `aabb`、`baked_extent`（均 additive、LF 字节可复现）。
- `python -m pipeline.generate_world --center` 支持以原点为中心烘焙（含负象限）。

**待 Codex（其 lane，规格见 `handoff/HANDOFF-CODEX-003`）**：
1. HTTP 端点 `GET /api/world/chunk/{cx}/{cy}.ply?lod=N`（`studio_server.py`，stream-only 调内核，
   缓存键须含 lod + 真实素材 sha；注意其 docstring 的 never-renders 契约需一并更新）。
2. viewer 消费（`web/viewer/main.js` 的 OOB gate 在 `grid.on_demand` 真时改按 url_template 请求）。
这两步一落，「无限村庄任意坐标漫游」闭环。缓存跨异构平台共享前须先解 HANDOFF-002（跨平台 float）。

## 关键文档

- `README.md` — 能力矩阵、快速开始、核心工作流。
- `docs/manual/reconstruction-setup.md` — 真实重建端到端手册（本机/云 GPU）。
- `docs/real-data-workflow.md` — 已就绪的对齐/导入契约（control_points.json、SplatInput、metric-aligned 判定）。
- `handoff/HANDOFF-CODEX-003-render-on-demand-infinite-world.md` — render-on-demand 集成规格（内核 API + 端点 + 缓存约束）。
- `docs/verification/2026-07-16-pipeline-reproducibility-audit.md` — pipeline 可复现性审计（随机源/字节/平台三维度）。
- `docs/verification/2026-07-16-failclosed-audit-and-fixes.md` — fail-closed/provenance 审计 + 四项 TDD 修复（含 1 项 medium fail-open：矛盾对齐证据不再被提升为 metric）。
- `handoff/` — Claude↔GPT 素材交办/回执（HANDOFF-00x）。
- CI：`.github/workflows/ci.yml`（ubuntu+windows × py3.11/3.13 + 素材跨平台可复现门）。
