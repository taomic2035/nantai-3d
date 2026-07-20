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

## 当前协作与接管状态（2026-07-20）

- **Opus 当前不可用；GLM-5.2 已作为临时接替 lane 加入。** Codex 独立完成
  HANDOFF-006 Phase 1 的 production clearance 合同、Blender runtime、
  frame identity v2 与 journal/runner 接入；GLM 提供 post-render quality/repose
  草案与 Batch 6 environment-module 草案。GLM 未提交代码仍须 Codex review 后才能进入
  main，不能以“测试绿”替代真实 layer/scene 证据。
- 当前核心交办按顺序为：
  1. `handoff/HANDOFF-OPUS-006-production-camera-quality-gates.md`
     （Phase 1 已完成，Phase 2 仍未交付）；
  2. `handoff/HANDOFF-OPUS-007-batch6-modules-productionization.md`。
  007 仅可在独立新路径上并行，触及 renderer/runtime/journal 时必须先协调。
- Windows `180-camera` production runner 的推荐接管方案是新增独立 Windows v2-build
  验证适配器并复用现有六层 frame/journal/quality 合同；**不得**直接删除 Mac 平台门。
  用户已要求独立推进且一般操作不反复审批，按方案 A 实施；仍须 TDD 与真实 build 验证。

### HANDOFF-006 Phase 1 fresh evidence

- canonical 180-camera preflight：
  `preflight_id=42f65291a55f58c5b064a2785b3ee868a5d9c77c107ad233a4f9f235d7f10b9a`；
  report SHA
  `0b63bc6759e8a36d7ace04d760e43d27862082d084cc0cd50b73e30449224418`。
- 显式 `<2m / 5-of-15` operator policy 下仅 `ground-route-010`（15）与
  `ground-route-039`（5）拒绝，其它 178 台为 0；`034=0` 仍只表示几何门未拒绝，
  **不表示** post-render/training 通过。
- 实现与边界见 `handoff/FEEDBACK-HANDOFF-CODEX-006-phase1.md`；GLM 006 草案的
  阻断审计见 `handoff/REVIEW-CODEX-014-glm-006-quality-repose.md`；下一阶段计划见
  `docs/superpowers/plans/2026-07-20-production-camera-postrender-quality.md`。

## Batch 6 image2 素材与 v2 场景差距（2026-07-20）

Batch 6 当前为 **`3/12`**，三张成功素材均在私有、可替换、未注册候选区：

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/
```

| 素材 | SHA-256 |
|---|---|
| `design-route-central-courtyard-eye-01.png` | `19b40a84322ab7d343716bd684fc83a3207ae42ad94993d28446707f7a5537df` |
| `design-detail-bridge-undercroft-01.png` | `16b9f390f4550b2ec64bd98e4ccd799e05c4f44cd924a5da1503eec73ae8b4be` |
| `design-detail-rear-service-courtyard-01.png` | `2c3900ab686cb45252538c8bdb6e507396ec9084ca7809a44fa3524810ab8b51` |

- 三张图均为独立 `design-only` 参考；`camera_calibration=unknown`、
  `geometry_consistency=not-verified`、`training_use=forbidden-as-multiview`、
  `trust_effect=none`。不得组合成 SfM/NeRF/3DGS 多视图训练集。
- 精确提示词、SHA、字节数和 queue/manifest 位于同一私有候选区；Release 不放候选中间态。
- image2 的 generation 与 reference-edit 端点均出现间歇网络错误：允许低频重试；失败请求
  不占候选记录、不写空文件。
- 模块拆分、现有 ScenePlan/Topology 绑定和 180 相机验收候选见
  `handoff/HANDOFF-CODEX-008-batch6-to-blender-modular-consumption.md`。
- 三张成功参考现已转换为私有 `modeled-unverified` Blender 原型：中央院落、桥拱/水车、
  后场服务院共 3 个模块、47 个独立对象；审计 RGB 确认几何可读，同时暴露 creek/terrain
  无真实河床切槽和正式最近相机看不到模块的问题。原型不进 registry/Git/Release，
  详见 `handoff/REVIEW-CODEX-012-batch6-private-blender-prototype.md`。
- 同一中央院落人眼坐标已完成六面 cubemap 审计：天顶/脚下未封死，但四周反复出现悬空
  建筑、高架步道横切视野和地面三角接缝。同点六向没有平移基线，绝不是 SfM/3DGS
  coverage 证明，详见 `handoff/REVIEW-CODEX-013-central-cubemap-360-audit.md`。
- 当前 Windows textured L2 build
  `4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc`
  确实包含 Blender 几何、PBR 材质和四张实渲预览，但仍是稀疏块体村庄：
  中央院落、石拱/水车桥底节点和建筑后场尚未进入正式几何。它继续声明
  `geometry_usability=preview-only`、`fidelity=simplified-pbr-not-render-parity`。
  机器身份和逐图差距见 `handoff/REVIEW-CODEX-008-batch6-vs-v2-blender-preview.md`。
- 用该 v2 `.blend` 内已登记的 `camera-ground-001`、`camera-courtyard-001`、
  `camera-bridge-001` 做私有 RGB 实渲：前两者分别以 `-35.517°`、`-24.781°`
  明显俯视地表；桥相机虽仅 `-1.516°`，但被极近石材表面严重遮挡。它们属于
  24-camera canary registry，**不是** 180-camera production plan。valid-pixel
  门无法拒绝“全是地面/近墙”的帧，详见
  `handoff/REVIEW-CODEX-009-v2-canary-ground-view-diagnostic.md`。
- 把正式 180-camera 计划中的 `ground-route-010/011/025/026` 瞬态注入同一 v2
  `.blend` 做私有 RGB 探针：`011/025/026` 能形成方向合理的人眼路线视图，
  `010` 被近表面严重遮挡；可读帧同时暴露悬空建筑、稀疏街巷和支撑缺失。
  这证明相机计划可被当前坐标合同消费，但不等于六层帧或训练质量通过，详见
  `handoff/REVIEW-CODEX-010-v2-production-camera-rgb-probe.md`。
- 对这四个 production camera 做 Blender 九宫格射线探针后确认：`010` 的上/中
  `6/9` 射线均在 `0.433–0.574m` 命中
  `bridge-lower-001 / stone-deck-parapets-piers`；其它三相机没有相同近表面模式。
  后续已扩展为 180 台 × 25 射线全量审计：`010` 与 `039` 可由近表面模式明确拒绝，
  但 `034` 证明单一距离门仍会漏掉斜穿木廊/近坡面坏帧，必须结合正式六层质量门。
  详见 `handoff/REVIEW-CODEX-011-production-camera-clearance-180.md`。

## ⚠️ 机器现实与重建能力边界（2026-07-15，已确认）

- **开发机无 NVIDIA GPU**：仅 Intel UHD Graphics 770 集显（无 CUDA），i7-14700 / 32GB / D盘 1.4TB。
  （核对**任意**机器的同样事实：`python make.py doctor` 实测并报告，不必信本节的记录——详见下方「诚实 UX 三件套」。）
- **本仓库不训练 3DGS**：它是重建管线**外围**的诚实封装层——摄取 → 坐标/位姿契约 → 米制 ENU 对齐（`pipeline/alignment.py`）→ 3DGS 导入/拼接/LOD/素材 → Spark Viewer（360° 漫游可用）。**把图片变成 3D 几何的两步是外部的**：
  1. **相机位姿（SfM）**：COLMAP（本机 CPU 可跑，慢；未安装则回退 mock/synthetic，非真实）。
  2. **3DGS 训练**：**仓库无训练器**。CUDA 训练器（gsplat/nerfstudio/Inria）本机跑不了。**实际主路径 = 云 GPU 租赁**；本机 Intel 集显跑 Brush 仅为受限的小场景试验档。
- **"完美"不可达**：3DGS 对天空/玻璃/水面/无纹理面有空洞与漂浮物；只能漫游拍到的体积。
- 端到端安装/使用手册（COLMAP + 云 GPU 训练 + 导入本仓库）：见 **`docs/manual/reconstruction-setup.md`**（Opus 编写中；用户配合云 GPU 账号/注册）。

### macOS Apple Silicon 本机实测（2026-07-17）

- 机器：macOS 26.5.2 / arm64 / 32 GB。`make.py doctor` 实测 COLMAP 4.1.0（CPU SfM）、
  Brush 0.3.0（wgpu 受限训练）、Python 必需/可选依赖均可用；仍无 NVIDIA CUDA 栈。
- MLX 0.32.0 已隔离安装到 `.nantai-studio/venvs/mlx`，Metal 矩阵探针实测
  `Device(gpu, 0)`；它不是 gsplat/nerfstudio CUDA 自定义算子的透明替代。
- Blender 4.5.11 LTS Apple Silicon 官方包已安装到 `/Applications/Blender.app` 并通过
  SHA-256、codesign 与 headless version 探针；但正式 canary 工具链仍严格锁定
  Windows x64 `third/blender/blender.exe`，因此 Mac Blender **尚不能**被该链路当成已验证运行时。
- 素材 registry 当前本机实测为 **11/11**：`stone_wall_01`、`fence_wood_01`
  已通过 Linux/x86_64 容器生成与登记 SHA 逐字节一致的权威 payload 并幂等恢复。
  **这不表示 Mac 原生生成器已修复**：Mac / NumPy 2.3.5 重生成仍漂移，绝不能
  重新登记本机漂移字节来掩盖跨平台差异。原始环境审计见
  `handoff/FEEDBACK-CODEX-007-macos-environment-audit.md`，恢复证据与未决项见
  `handoff/FEEDBACK-CODEX-008-macos-asset-payload-recovery.md`。

## Render-on-demand 无限世界（2026-07-17，内核 + Studio/Viewer 集成就绪）

「无限村庄任意坐标漫游」的**管线内核已完整并对抗性验证 CLEAN**（Opus lane）：
- `pipeline.render_chunk_to_ply.render_single_chunk(cx, cy, world_seed=42, registry=None, lod=None)`
  → ply 字节（纯内存零落盘、任意含负坐标、确定性、**跨进程字节一致**可内容寻址缓存、
  LOD 0/1/2 分级省带宽、registry 真实素材路径亦已验证字节确定且纯读无副作用）。
- world manifest 已带无限网格元数据：`grid{on_demand:false, url_template, world_seed}`、全局
  `bounds`、per-chunk `aabb`、`baked_extent`（均 additive、LF 字节可复现）。
- `python -m pipeline.generate_world --center` 支持以原点为中心烘焙（含负象限）。

**Codex 集成已完成**：
1. HTTP 端点支持负坐标、LOD 0/1/2、ETag/304、HEAD、结构化失败与 stream-only 无落盘。
2. Viewer 预烘焙优先，越界时经严格同源模板按需请求，并消费真实三维 bounds。
3. 预烘焙 manifest 保持 `on_demand:false`；Studio server 仅在合法 seed/template 与端点实际
   可用时无落盘投影为 true，普通静态服务不会虚假宣称按需能力。

详细回执见 `handoff/FEEDBACK-HANDOFF-CODEX-003.md`。当前按需端点使用确定性合成代理
(`registry=None`)；真实可替换素材的跨 worker 缓存须先有 asset version/SHA 内容键，且跨异构
平台共享前仍须解 HANDOFF-002。

## 真实重建链路（2026-07-17，Opus lane 与 Codex 分块 Viewer 均已就位）

真实数据链路已端到端打通，每步的**真实限制均如实文档化**（`docs/manual/reconstruction-setup.md` /
`docs/real-data-workflow.md`）：

采集 → **`check_capture` 预检**（在烧掉 COLMAP 的几小时之前）→ COLMAP（本机 CPU，**每阶段 6h 卡死 backstop**）→
**外部云 GPU 训练 3DGS** → `normalize_ply_quats` → `flatten_ply_sh`（**仅米制对齐时需要**）→ `prepare_import` →
`reconstruct --engine import [--chunk-size-m 50]` → `alignment --from-gps | --control-points` →
**`inspect_recon`**（读懂产物能不能量）→ 360° 漫游

Opus lane 近期补齐的能力与**已知边界**（均 TDD 锁定）：
- **高阶 SH 限制**：加载器对「含 `f_rest_*` + 非恒等旋转」**故意 fail-closed**（可靠 SH 旋转未实现，
  绝不施加错误旋转出错色）。真实训练器（nerfstudio splatfacto）输出 degree-3 SH，故**米制对齐前**
  须 `scripts/flatten_ply_sh.py` 扁平化（丢高阶保 DC 视角无关基色；代价：失视角高光）。阻断信息自解释。
- **GPS 对齐的精度现实**：`alignment --from-gps <ingest-manifest>` 可从逐图 EXIF GPS 一键 turnkey
  对齐，但**消费级 GPS 精度 3~10m**，噪声无法被相似变换解释 → **默认 `--max-rms 2.0` 基本必然
  fail-closed**（这是正确的：拒绝为噪声盖米制章）。放宽到 5~10 才可能过门，但**精度不优于 GPS 本身**；
  要 sub-metre 须实测控制点（`enu_xyz`）。失败信息自解释。
- **大重建分块流式**：`reconstruct --chunk-size-m 50`（或 `scripts/chunk_reconstruction.py`）把上百万
  高斯的单个 `.ply` 切成 per-chunk ply + LOD + `chunks.json`。纯空间重打包：无损（每高斯恰好落一块）、
  坐标绝对不动、**provenance 不增不减**（分块**绝不**把 preview-only 变 metric-aligned）。
  **Codex 已完成**：Viewer 消费 `chunks.json`，按相机距离流式调度 Spark/DC 分块与声明的 LOD 密度，
  并以顶层 `source` 原样显示 provenance（验收见 `handoff/FEEDBACK-HANDOFF-CODEX-004.md`）。注意其
  **无 `grid`** —— 重建**不可**程序化续渲，**绝不可**对它投影 `on_demand:true`。
- **诚实 UX 三件套**（纯 CPU、零 GPU 依赖；`tests/test_doctor.py` / `test_capture_quality.py` /
  `test_inspect_recon.py` 锁定）。三者都是 provenance-safety 面向人的一侧：**把已有的严谨证据说成人话，
  绝不新增信任**。改它们时别破坏各自的诚实性约束（源码顶部有逐条说明）：
  - `scripts/doctor.py` ≡ `make.py doctor` —— 实测本机能跑重建的哪几步（COLMAP/Brush/GPU/Python 依赖/
    素材注册表/磁盘），给 can / cannot / **unclear** 小结（探不准进 unclear，**不替用户下结论**）。
    **退出码恒为 0**：报的是机器状态，「缺 COLMAP」是**结论**不是失败，非 0 会逼 CI 把正常报告当故障。
    GPU 只判「未探测到**可用的** CUDA 栈」（依据 `nvidia-smi` 缺席，**证据推理非硬件事实**）；
    素材 sha **默认不校验**，报告明写「未校验」，`--verify-assets` 才实测（`make.py doctor` 不带此开关）。
  - `scripts/check_capture.py` ≡ `make.py check-capture`（`PHOTOS=` 传目录）—— 跑 COLMAP 前用**单图证据**
    预检（张数/模糊/分辨率/EXIF GPS + 匹配器建议 + 由手册 §4 实测锚点外推的耗时**粗估**）。
    **红线**：**重叠度是图之间的关系，单图分析测不到** → `likely` 仅意味「没发现明显硬伤」，
    **绝不可**被描述成「预检通过就能重建」。退出码 `0` = 出了报告（无论结论好坏），`2` = 没法分析。
  - `scripts/inspect_recon.py` ≡ `make.py inspect-recon`（`MANIFEST=` 传路径）—— 把 `recon_manifest.json`
    翻成人话（能不能量 / 精度 / 变换链 / **未知项**）。**只翻译不提升信任**；矛盾（声称 `metric-*` 但证据
    `passed:false` / 无法解析 / 非米制 / `synthetic=true`）→ 指出矛盾 + 按 `preview-only` 处理 +
    **退出码 2**（可当 CI 门；判据与 `reconstruct._derive_geometry_usability` 同源）。
    **限制**：只读 manifest 声称 + 内部自洽性，**不碰 PLY 字节**、不校验 `artifacts.*.sha256`、不重算残差。

## 关键文档

- `README.md` — 能力矩阵、快速开始、核心工作流。
- `docs/manual/reconstruction-setup.md` — 真实重建端到端手册（本机/云 GPU）。
- `docs/real-data-workflow.md` — 已就绪的对齐/导入契约（control_points.json、SplatInput、metric-aligned 判定）。
- `handoff/HANDOFF-CODEX-003-render-on-demand-infinite-world.md` — render-on-demand 集成规格（内核 API + 端点 + 缓存约束）。
- `handoff/FEEDBACK-HANDOFF-CODEX-003.md` — Codex 集成回执（运行时开闸决策 + 真实素材未决项）。
- `handoff/REVIEW-CODEX-003-render-on-demand-integration.md` — Opus review 回执（字节/纯度/投影 sign-off + 4 项待处理：真实素材密度断崖 CRITICAL、布局引擎不对称 HIGH、投影 fail-open MEDIUM、越界码 LOW）。
- `handoff/HANDOFF-CODEX-004-stream-large-reconstructions.md` — 大重建分块流式交办（`chunks.json` 契约；与合成村庄 manifest 同构但**无 `grid`**、坐标绝对、`source` 是标注信任的唯一依据）。
- `handoff/HANDOFF-CODEX-008-batch6-to-blender-modular-consumption.md` — Batch 6 三张设计参考到 Blender 模块、现有 topology 与 180 相机验收候选的消费规格。
- `handoff/REVIEW-CODEX-008-batch6-vs-v2-blender-preview.md` — 三张 Batch 6 参考与当前 Windows v2 Blender 四张实渲预览的机器身份绑定差距审计。
- `handoff/REVIEW-CODEX-009-v2-canary-ground-view-diagnostic.md` — v2 `.blend` 三个近地 canary 相机的真实 RGB、姿态与近表面/俯视坏帧诊断；说明 valid-pixel 门为何不足。
- `handoff/REVIEW-CODEX-010-v2-production-camera-rgb-probe.md` — 四个正式 ground-route 相机瞬态注入 v2 `.blend` 的 RGB 探针；证明三张人眼方向可用并暴露近表面遮挡、悬空与支撑缺失。
- `docs/verification/2026-07-16-pipeline-reproducibility-audit.md` — pipeline 可复现性审计（随机源/字节/平台三维度）。
- `docs/verification/2026-07-16-failclosed-audit-and-fixes.md` — fail-closed/provenance 审计 + 四项 TDD 修复（含 1 项 medium fail-open：矛盾对齐证据不再被提升为 metric）。
- `handoff/` — Claude↔GPT 素材交办/回执（HANDOFF-00x）。
- CI：`.github/workflows/ci.yml`（ubuntu+windows × py3.11/3.13 + 素材跨平台可复现门）。
