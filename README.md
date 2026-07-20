# Nantai 3D Studio

照片与视频驱动的 3D 重建、Gaussian Splat 拼接和可替换村庄素材工作台。

当前仓库交付的是一条可在本机复现的编排与审计链：混合媒体归一化、联合配准、显式坐标契约、外部训练器 3DGS 导入、拼接/区域增清/LOD、素材版本替换、Web Viewer 与 Studio。没有可用重建运行时时会使用明确标注的 synthetic/proxy 数据，不把演示产物冒充实测重建。

## 能力矩阵

| 能力 | 状态 | 当前边界 |
|---|---|---|
| 图片 + 视频输入 | **verified** | 图片复制、视频抽帧、模糊筛选；两者进入同一 session/registration 契约 |
| 图视频联合配准 | **verified / optional runtime** | COLMAP 可用时读取真实相机模型与注册覆盖率；否则使用确定性 synthetic mock |
| 统一 3D 坐标 | **verified, fail closed** | 所有 artifact 声明完整 `CoordinateFrame`；跨 frame 只接受显式、内容寻址的 `FrameTransform` |
| ENU 米制对齐 | **external evidence required** | 裸 COLMAP 结果保持 `sfm-arbitrary / arbitrary / unaligned`；只有外部控制点/GPS Sim3 证据才可升级 |
| 混合 3DGS 拼接 | **verified** | 导入 artifact 的 frame/units/transform history 一致后才 merge；不一致时拒绝 |
| 可拼接、可变清晰 | **verified** | 体素去重、区域替换、三级 LOD；度量型空间操作只允许在米制 frame 中执行 |
| 3DGS 属性保真 | **verified** | DC、完整高阶 SH、opacity、anisotropic scale、rotation、normals 与额外标量 round-trip |
| Web Gaussian Splat | **verified with runtime fallback** | Spark 2.1.0 渲染完整 3DGS；依赖不可用时降级并标注为 DC point preview |
| 可替换素材 | **verified** | 11 个确定性 HANDOFF-001 程序素材；Release 另提供 68 个可替换 synthetic 视觉槽位，均有 SHA、CAS 与来源证据 |
| 180 机位 synthetic 生产计划 | **verified plan / evidence pending** | 180 个有限且无重复 pose、两条 route loop；HUD 单独披露尚未交付的渲染/质量证据，不把机位数称为 360° 覆盖 |
| Studio UX | **verified local snapshot** | 三栏工作台、六步状态、provenance、LOD/图层控制、覆盖审计与 production plan HUD；本地 adapter 只读，任务仍从 CLI 启动 |
| 3DGS 训练（外部引擎） | **verified local small / cloud recommended** | 仓库不自研训练器；`scripts/reconstruct_local.py` 可驱动 `third/brush`，本机 Intel 集显已跑通中小场景；大场景/高质量走云 GPU |

## 快速开始

Python 3.11+、Node.js 20+。建议在项目根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

# 生成、验收并幂等注册 11 个模拟素材
make assets PY=.venv/bin/python

# input/ 中可混放 jpg/png 与 mp4/mov/avi
make ingest PY=.venv/bin/python

# 本机可复现的 synthetic 重建；产物会明确显示 mock-proxy
make reconstruct PY=.venv/bin/python

# 生成 5×5 村庄与逐素材消费证据
make world PY=.venv/bin/python

# Studio: http://127.0.0.1:8000/
make serve PY=.venv/bin/python
```

> **Windows / 无 GNU make 的环境**：用跨平台任务运行器 `make.py` 替代 `make`（同名 target），
> venv 解释器为 `.venv\Scripts\python`。它强制 UTF-8 输出，规避 CJK/emoji 在管道下的编码错误：
>
> ```powershell
> .venv\Scripts\python -m pip install -e ".[dev]"
> .venv\Scripts\python make.py assets   # 生成+验收+注册 11 素材
> .venv\Scripts\python make.py world     # 5×5 村庄
> .venv\Scripts\python make.py serve     # Studio
> .venv\Scripts\python make.py test lint  # 门禁
> ```
>
> 素材字节跨平台可复现（HANDOFF-002：写盘前量化到 1e-6 网格）；registration.json /
> recon_manifest.json 均以 LF 写出，保证信任根跨 OS 字节一致。CI 矩阵 (ubuntu+windows ×
> py3.11/3.13) 强制 `make.py test/lint` 与素材跨平台字节一致。

完整门禁：

```bash
make test PY=.venv/bin/python
make verify PY=.venv/bin/python
.venv/bin/python -m ruff check pipeline tests
git diff --check
```

## Release 模拟视觉素材包

没有真实素材时，可使用 [Synthetic Mountain Village Canary Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-canary-2026-07-16)
中的最终视觉包 `synthetic-mountain-village-visual-pack-hybrid-v3-2026-07-17.zip`。它包含 68 个通用山村设计槽位：
16 个关键视角、8 个环境视角、24 个材质、12 个建筑细节和 8 个道具；每条记录都指向内容寻址 PNG，并携带可核验的来源 manifest。

在 Windows PowerShell 中下载、校验并安装到 Studio/Canary 的默认读取位置：

```powershell
$releaseDir = ".nantai-studio\release-downloads\hybrid-v3"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-canary-2026-07-16 `
  --pattern "synthetic-mountain-village-visual-pack-hybrid-v3-2026-07-17.zip" `
  --pattern "SHA256SUMS.txt" --dir $releaseDir --clobber

$archive = Join-Path $releaseDir "synthetic-mountain-village-visual-pack-hybrid-v3-2026-07-17.zip"
$expected = ((Get-Content (Join-Path $releaseDir "SHA256SUMS.txt")) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "visual pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v3" -Force
```

macOS/Linux 可在下载目录执行 `sha256sum -c SHA256SUMS.txt`，再将 ZIP 解压到
`.nantai-studio/synthetic-village/hybrid-v3/`。安装后，默认 manifest 应位于
`.nantai-studio/synthetic-village/hybrid-v3/visual-sources/visual-sources.json`；可用下面的加载器校验数量与 synthetic 标记：

```bash
python -c "from pathlib import Path; from pipeline.synthetic_village.visual_sources import load_visual_source_manifest; m=load_visual_source_manifest(Path('.nantai-studio/synthetic-village/hybrid-v3/visual-sources/visual-sources.json')); assert m.synthetic and len(m.records) == 68; print('visual pack verified:', len(m.records))"
python scripts/synthetic_village.py build-canary
```

ZIP 内的 `default-resources/` 与 `source-evidence/` 是审计快照，不应覆盖仓库中受版本控制的
`assets/default-resources/`。槽位记录不可原地改写；替换图片时应通过
`pipeline.synthetic_village.visual_sources.import_visual_source(..., pack_root=<新目录>)` 创建新的 pack revision，
再将 `visual_pack_root` 显式传给 `run_canary_build`。CLI 的 `import-visual` 固定写默认 pack，已占用槽位会 fail closed。

这些图片全部明确标记为 `synthetic=true`，用途是可替换的设计参考、材质/细节/道具输入和流程演示；
它们不是同一真实场景的几何一致多视图，不是训练完成的 3DGS，也不能单独证明可在任意坐标 360° 漫游。
真实重建仍需按[端到端重建手册](docs/manual/reconstruction-setup.md)完成真实采集、COLMAP 位姿和外部 GPU 3DGS 训练。

## 核心工作流

### 1. 混合媒体输入

```bash
.venv/bin/python -m pipeline.ingest \
  --input input --output photos --fps 2 \
  --max-frames 300 --blur-threshold 80
```

照片和视频帧保留来源/session 信息。视频抽帧不是“视频已重建”的证据；只有 registration 的逐图覆盖率才能说明哪些帧真正注册成功。

### 2. 配准与坐标契约

```bash
# 自动选择 COLMAP；不可用时回退 synthetic mock
.venv/bin/python -m pipeline.reconstruct \
  --photos photos --reg-engine auto --engine mock
```

坐标模型包含：

- `CoordinateFrame`：handedness、axes、units、metric status、geo alignment、provenance 与证据。
- `FrameTransform`：source/target、Sim3、method 与内容寻址 `transform_id`。
- PLY `nantai_meta`：frame、units、已应用 transform history。

Viewer 的世界约定为右手 ENU：`(E, N, U) → Three.js (E, U, -N)`，行列式为 `+1`。裸 COLMAP 的相机与点位仍处于联合 SfM local frame，不会被静默重标为米制 ENU。COLMAP 相机模型和 `images.txt`/`cameras.txt` 的解析遵循其[官方输出格式](https://colmap.github.io/format.html)。

**从 sfm-local 升级到米制 ENU（`pipeline.alignment`）**：裸 COLMAP 停在 arbitrary sfm-local。只有提供控制点或 GPS 锚点（计数 ≥3，且源点需张成 3D → 实际 ≥4 非共面），用闭式 Umeyama 拟合 SfM→ENU Sim3（强制 det=+1，绝不产出反射），且非退化、scale>0、RMS 残差 ≤ 阈值时，才升级为 `world-enu`（MEASURED / metric / ALIGNED）：

```bash
# 配准得到 sfm-local registration.json 后，用控制点/GPS 拟合 Sim3；
# 退化(共线/共面)、高残差或缺 geo origin 均 fail-closed，registration 保持 sfm-local/UNALIGNED
.venv/bin/python -m pipeline.alignment \
  --registration recon/registration.json \
  --control-points control_points.json \
  --geo-origin 26.0801,119.2967,12.5 \
  --max-rms 2.0 --out recon/registration_aligned.json

# 用对齐后的 registration 驱动导入重建 → manifest 报告 metric-aligned ENU world
.venv/bin/python -m pipeline.reconstruct \
  --photos photos --engine import --splat trained/drone.json \
  --registration recon/registration_aligned.json
```

拟合的每点残差、退化裕度（源点奇异值）、阈值与门禁结果记入 `Sim3AlignmentEvidence`（`sim3.alignment.v1=<json>` 证据串），写在 `world_frame` 与 `pose_to_world` 上，可机器复核。这是把管线从 provenance-honest-mock 推进到 measured 的关键步骤；唯一外部依赖是真实的 COLMAP + GPU 训练 3DGS 产物。

完整输入格式（`control_points.json` / `SplatInput`）、逐步命令与 `metric-aligned` 判定模型见 [docs/real-data-workflow.md](docs/real-data-workflow.md)。

**想从零把照片/视频变成可漫游场景**（含 COLMAP 位姿、云 GPU 训练 3DGS、再导入本仓库）？看端到端手册 [docs/manual/reconstruction-setup.md](docs/manual/reconstruction-setup.md)——诚实说明本机能做什么、需要你做什么、以及真实限制。

### 3. 导入真实 3DGS 并混合拼接

每个外部 PLY 用 JSON 声明完整 `SplatInput`。如果 source 与 registration target 不同，必须携带显式 `FrameTransform`：

```json
{
  "session_id": "video_drone_orbit",
  "path": "trained/drone.ply",
  "source_frame": {
    "frame_id": "trainer-local",
    "handedness": "right",
    "axes": "local-z-up",
    "units": "meters",
    "metric_status": "metric",
    "geo_aligned": "unaligned",
    "provenance": "measured",
    "evidence": ["trainer export contract"]
  },
  "transform": {
    "source_frame": "trainer-local",
    "target_frame": "mock-local",
    "sim3": {
      "scale": 1.0,
      "quat_wxyz": [1.0, 0.0, 0.0, 0.0],
      "t_xyz": [0.0, 0.0, 0.0]
    },
    "method": "external-sim3",
    "evidence": ["control-point fit"]
  }
}
```

`transform_id` 可省略，由内容计算；传入错误 ID 会被拒绝。

```bash
.venv/bin/python -m pipeline.reconstruct \
  --photos photos --reg-engine mock --engine import \
  --splat trained/drone-splat-input.json
```

补拍增清使用旧的全量场景作为 base；frame/units/history 不匹配时不会替换：

```bash
.venv/bin/python -m pipeline.reconstruct \
  --photos photos --engine mock \
  --base-scene recon/scene_full.ply
```

高阶 SH 在平移和统一缩放时保持不变；涉及空间旋转且缺少正确 SH basis rotation 时会阻断，避免静默破坏视角相关颜色。

### 4. 可替换素材

```bash
# generator → manifest/SHA 验收 → registry 注册
make assets PY=.venv/bin/python

# 验收任意 handoff；通过后才能注册
.venv/bin/python -m pipeline.validate_handoff \
  handoff/deliverables/HANDOFF-001 --register --assets-dir assets
```

布局只引用稳定 `asset_id`。替换会创建新版本并保留历史；重跑 world 后，只有实际加载且 SHA 匹配的素材才会出现在 `asset_consumption` 证据中。素材本地坐标契约固定为右手、米制、Z-up、地面 `z≈0`。

### 5. Viewer 与 Studio

`make serve` 启动带安全静态白名单的本地服务器，并将 `/` 重定向到 Studio。Studio 自动优先连接 `/api/project`；在普通静态服务器下才进入永久标注的 mock adapter。

- Studio：`/web/studio/`
- 独立 Viewer：`/web/viewer/`
- API：`GET /api/project`、`GET /api/runs`
- 无落盘 production plan：`GET /web/data/production-camera-plan.json`

Studio 通过 bridge 读取 Viewer 的实际 runtime capability。只有 Spark 初始化成功后才显示 anisotropic covariance、alpha composite 和 spherical harmonics；否则显示降级预览。Production plan 与 coverage audit 是独立证据层，不会提升 reconstruction provenance。实现固定使用 [Spark 2.1.0](https://sparkjs.dev/docs/) 与兼容 Three.js 版本。

## 目录

```text
pipeline/                 输入、配准、坐标、3DGS、素材、world、Studio server
tests/                    Python 合约与端到端回归
web/viewer/               Spark 3DGS + DC fallback + iframe bridge
web/studio/               reducer/adapter 驱动的工作台 UX
docs/contracts/           Studio snapshot JSON Schema v2
docs/superpowers/         UX 规格与接管实施计划
handoff/                  Handoff / Feedback 与可复现模拟素材
assets/registry.json      活跃版本、历史与 payload SHA
verification/             独立技术验证脚本
```

## 产物与可信度

- `recon/registration.json`：位姿、相机内参、session、frame 与 coverage evidence。
- `recon/scene_full.ply`：审计用完整 3DGS PLY。
- `web/data/recon/recon_manifest.json`：artifact、LOD、ancestry、transform chain、requested/actual engine、synthetic 与 fidelity。
- `web/data/manifest.json`：world bounds、chunk LOD 和 `asset_consumption`。
- `/web/data/production-camera-plan.json`：Studio server 从当前 deterministic 180 机位契约按需投影；不写入 `web/data/`，未交付证据仍保留在 plan 中。
- `assets/registry.json`：素材 active/history、版本、SHA 与来源。

可信度从机器字段推导，不从文件名或 engine 名推断：

- `synthetic=true` 或 `actual_engine=mock-proxy` 只表示流程演示。
- `geometry_usability=preview-proxy` 不可用于测量。
- `artifact_fidelity=full-3dgs` 描述文件属性；`render_fidelity` 由 Viewer 实际能力决定。
- “registered” 不等于“consumed”；消费必须有渲染报告和实测 SHA。

接管背景、尚存限制和 Opus 恢复入口见 [handoff/TAKEOVER-2026-07-14.md](handoff/TAKEOVER-2026-07-14.md)。
