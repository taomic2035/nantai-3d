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
| 可替换素材 | **verified** | 11 个确定性 HANDOFF-001 程序素材；Release 另提供 68 个可替换 synthetic 视觉槽位和 44 张路线/包络/跨分块过渡/方向/模块板/构造与材质设计输入，均有 SHA 与来源边界 |
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

### Batch 8 反向路线与近景遮挡补充包

[Batch 8 Reciprocal-Route Design Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch8-2026-07-20)
额外提供 6 张经过人工视觉筛选的 image2 设计输入：中央院落下行回望、桥面第一人称穿越、
水车尾水维修面、廊下跨层通道、森林/果园边界回望和下谷返村视角。包内同时包含精确提示词、
内容寻址 manifest、使用边界和逐文件 SHA-256；没有收录未满足视角角色的桥区高位中间图。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch8-reciprocal-route"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch8-2026-07-20 `
  --pattern "synthetic-village-reciprocal-route-design-pack-batch8-2026-07-20.zip" `
  --pattern "synthetic-village-reciprocal-route-design-pack-batch8-2026-07-20.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-reciprocal-route-design-pack-batch8-2026-07-20.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-reciprocal-route-design-pack-batch8-2026-07-20.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 8 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch8" -Force
```

该补充包不是默认 registry payload，也不是可直接训练的“360 多视图”。它只补足 Blender 建模时容易遗漏的
反向路线、结构背面、跨层通道和近景遮挡设计；必须经过显式模块建模、相机规划、碰撞/可行走审计和真实六层渲染，
才能进入 Viewer 漫游场景。不得把图片边缘的宽幅构图解释为 equirectangular 全景。

### Batch 9 侧向路线与隐藏结构补充包

[Batch 9 Lateral-Route Design Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch9-2026-07-20)
继续提供 6 张 image2 原始 PNG：中央院落横向巷口、桥区下游岸侧、水车对岸检修面、
廊下下层反向通道、林果边界三岔路和下谷田边交叉口。它们与 Batch 8 形成前/后/侧向设计参考，
但仍是相互独立的生成结果，不是同一相机系统的几何一致多视图。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch9-lateral-route"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch9-2026-07-20 `
  --pattern "synthetic-village-lateral-route-design-pack-batch9-2026-07-20.zip" `
  --pattern "synthetic-village-lateral-route-design-pack-batch9-2026-07-20.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-lateral-route-design-pack-batch9-2026-07-20.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-lateral-route-design-pack-batch9-2026-07-20.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 9 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch9" -Force
```

ZIP 只包含 6 张选中图片、6 份精确提示词、manifest、使用说明和 payload checksum。
桥侧图额外出现一个小型泄水孔，因此只能指导主桥拱、桥面厚度、桥台和岸侧路线；canonical
bridge topology 必须由版本化 recipe 决定。其余图片也只能指导建模，不能提升 coverage、
metric、alignment 或 training 信任。

### Batch 10 垂直包络与近景遮挡补充包

[Batch 10 Vertical-Enclosure Design Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch10-2026-07-21)
补充 6 张 image2 原始 PNG：中央院落檐底与有顶侧廊、桥拱底面与桥台、水车轴端检修平台、
廊下楼板结构与净空、林果边界挡墙/排水/树冠接触，以及下谷建筑基础/涵洞/溪岸回接。
它们与 Batch 8 的前后互逆、Batch 9 的侧向路线形成平面方向之外的垂直包络参考。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch10-vertical-enclosure"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch10-2026-07-21 `
  --pattern "synthetic-village-vertical-enclosure-design-pack-batch10-2026-07-21.zip" `
  --pattern "synthetic-village-vertical-enclosure-design-pack-batch10-2026-07-21.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-vertical-enclosure-design-pack-batch10-2026-07-21.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-vertical-enclosure-design-pack-batch10-2026-07-21.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 10 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch10" -Force
```

包内严格只有 6 张入选图片、6 份精确提示词、manifest、使用说明和 payload checksum。
这些图片仍是独立生成、相机未标定且几何一致性未验证的可替换设计输入；不能直接用于
SfM/3DGS，不能证明 360° coverage、任意坐标几何、碰撞安全、米制尺度或 training
适用性。桥拱、水车轴承、排水和基础的最终拓扑必须来自版本化 recipe，并经过 fresh
Blender topology/collision、standing-eye 六层实渲和 post-render v2 policy。

### Batch 11 跨分块连续过渡补充包

[Batch 11 Boundary-Transition Design Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch11-2026-07-21)
补充 6 张通用 image2 设计输入：东西向等高道路、南北向折返步道与道路交会、连续溪流/岸路/
桥涵、梯田与灌溉、森林/竹林/果园到聚落，以及下谷道路/步道/溪流/田地/民居汇合。
它们面向跨 chunk transition module，不绑定某一个具体村庄，可以被真实素材逐槽替换。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch11-boundary-transition"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch11-2026-07-21 `
  --pattern "synthetic-village-boundary-transition-design-pack-batch11-2026-07-21.zip" `
  --pattern "synthetic-village-boundary-transition-design-pack-batch11-2026-07-21.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-boundary-transition-design-pack-batch11-2026-07-21.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-boundary-transition-design-pack-batch11-2026-07-21.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 11 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch11" -Force
```

ZIP 严格只有 6 张图片、6 份精确 prompt、manifest、使用说明和 payload checksum。
这些图片只指导跨分块道路、步道、水系、梯田、植被和聚落过渡的建模；共享边界坐标必须来自
确定性的 world-edge anchor，不能从像素推断。即使画面看起来连续，也不代表六张图共享相机或
几何，更不能作为 SfM/3DGS 多视图、360° coverage 或任意坐标场景完成度的证据。

### Batch 12 同一视觉家族六方向参考补充包

[Batch 12 Directional Reference Design Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch12-2026-07-21)
以 Batch 11 下谷汇合区的一张入选图作为 `scene-identity-only` 参考，补充东/下游、西/上游、
上坡聚落、下坡谷地、仰视檐底/树冠/线缆和俯视铺地/台阶/排水六个方向角色。相较完全独立的
提示词，它们更接近同一建筑与材质语言，适合指导一个通用 transition hub 的四周和上下包络建模。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch12-directional-reference"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch12-2026-07-21 `
  --pattern "synthetic-village-directional-reference-design-pack-batch12-2026-07-21.zip" `
  --pattern "synthetic-village-directional-reference-design-pack-batch12-2026-07-21.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-directional-reference-design-pack-batch12-2026-07-21.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-directional-reference-design-pack-batch12-2026-07-21.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 12 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch12" -Force
```

ZIP 严格只有 6 张图片、6 份精确 prompt、manifest、使用说明和 payload checksum。
参考图约束只能提高视觉家族一致性，不能证明六张图共享精确几何。它们尺寸与相机内参不一致，
不是 cubemap、全景或已标定多视图，不得拼接后声称 360° coverage，也不得直接送入 SfM/3DGS。
真实方向、共享锚点、碰撞和可行走结论必须来自版本化 3D recipe 与 fresh Blender 六层实渲。

### Batch 13 模块化资产建模参考板

[Batch 13 Modular Asset Reference Boards Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch13-2026-07-21)
六张 `1536×1024` 板把整景素材拆成可复用部件：住宅构造、桥涵/挡墙/排水、步道/台阶/
坡道/架空通行、梯田/灌溉/水车、植被/岩石/围栏，以及电杆/灯/井/推车/棚架/小道具。
每张同时展示大量隔离部件和隐藏侧、底面、接头、基础或细节建议，面向 Blender recipe、
instancing、LOD、collision proxy 和可行走拓扑建模。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch13-modular-assets"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch13-2026-07-21 `
  --pattern "synthetic-village-modular-asset-reference-pack-batch13-2026-07-21.zip" `
  --pattern "synthetic-village-modular-asset-reference-pack-batch13-2026-07-21.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-modular-asset-reference-pack-batch13-2026-07-21.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-modular-asset-reference-pack-batch13-2026-07-21.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 13 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch13" -Force
```

模块板是设计参考，不是有尺寸的工程图、精确 turntable 或已完成 3D 资产；同一部件在一张板上的
多个视图也不能证明共享同一个 mesh。画面材质不是 seamless PBR atlas，不得裁切后登记为真实
texture payload。最终尺度、连接锚点、拓扑、碰撞、材质图和实例身份必须在版本化 recipe 中声明
并经过 Blender 实测。

### Batch 14 斜向路线与平移检查点参考

[Batch 14 Diagonal Navigation Design Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch14-2026-07-21)
补充上坡左斜、下坡右斜、后场服务巷、林果边界、前移检查点和后移检查点六个角色。它们重点
暴露挡墙背面、屋檐/阳台底面、基础支撑、排水出口、溪床/桥台、隐藏侧巷和反向立面，帮助
Blender 建模摆脱只做正面“景观图”的问题。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch14-diagonal-navigation"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch14-2026-07-21 `
  --pattern "synthetic-village-diagonal-navigation-design-pack-batch14-2026-07-21.zip" `
  --pattern "synthetic-village-diagonal-navigation-design-pack-batch14-2026-07-21.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-diagonal-navigation-design-pack-batch14-2026-07-21.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-diagonal-navigation-design-pack-batch14-2026-07-21.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 14 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch14" -Force
```

ZIP 严格只有 6 张原始 `1536×1024` PNG、6 份精确 prompt、manifest、使用说明和 payload
checksum。六张图是独立生成的设计输入，不是同一场景的标定多视图；其中“前移/后移约 8 米”
只是提示词中的构图意图，没有实测 pose、baseline、intrinsics 或像素对应。不得把本包直接送入
SfM/NeRF/3DGS 或声称 360° coverage。应先建立 canonical 3D recipe 和已知相机，再由 collision/
topology 与 fresh RGB/depth/normal/instance/semantic/camera evidence 决定受测位置是否可漫游。

### Batch 20 角色拓扑与相机包络参考

[Batch 20 Role-Topology Design Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch20-2026-07-23)
提供 8 张最终筛选的 image2 原始 PNG：桥梁折线接近与院落侧回望、水车环形检修路线与上层平台
回望、森林折返节点与林内返村视角，以及桥—水车、森林—果园两个共享空间包络。它们针对正式
实渲中桥、水车、森林角色的构图缺口，强调非共线路线、近中远景密度和结构背面。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch20-role-topology"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch20-2026-07-23 `
  --pattern "synthetic-village-role-topology-design-pack-batch20-2026-07-23.zip" `
  --pattern "synthetic-village-role-topology-design-pack-batch20-2026-07-23.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-role-topology-design-pack-batch20-2026-07-23.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-role-topology-design-pack-batch20-2026-07-23.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 20 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch20" -Force
```

ZIP 只含 8 张入选图、8 份精确 prompt、manifest、使用说明和 payload checksum；不含 contact
sheet、生成队列、失败请求或旧批次。引用条件只传递视觉语言，不建立像素对应、共享几何、标定相机、
米制尺度或 360° coverage。Blender 消费后仍须通过 topology/clearance、平移相机六层实渲、
visibility 与 post-render v2，才能证明受测位置可漫游。

### Batch 21 角色构造与模拟材质参考

[Batch 21 Role-Construction & Simulated-Material Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch21-2026-07-23)
提供 6 张桥梁、水车、森林的互补构造视角，以及 2 张石材/旧木材模拟 albedo 原型。构造图重点
暴露拱腹、桥台、轮轴、引水槽、尾水、挡墙、涵洞、楼梯底部和路线支撑，供下一轮 Blender
独立构件建模；材质图只用于合成视觉 QA，不是实拍纹理或完整 PBR 材质组。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch21-role-construction"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch21-2026-07-23 `
  --pattern "synthetic-village-role-construction-material-pack-batch21-2026-07-23.zip" `
  --pattern "synthetic-village-role-construction-material-pack-batch21-2026-07-23.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-role-construction-material-pack-batch21-2026-07-23.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-role-construction-material-pack-batch21-2026-07-23.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 21 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch21" -Force
```

ZIP 只含 8 张最终 PNG、8 份精确 prompt、manifest、使用说明和 payload checksum；不含 contact
sheet、生成队列、失败请求或旧批次。6 张场景图是独立生成的设计参考，不是标定多视图；2 张材质图
未经色彩标定、物理尺度或无缝平铺验证，也没有 normal/roughness/displacement 通道。所有输入保持
`synthetic=true`、`design-only`、`preview-only` 和 `trust_effect=none`。

### Batch 22 水车局部环绕、构造与模拟材质参考

[Batch 22 Watermill Local-360 Design Inputs Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch22-2026-07-23)
提供 8 张围绕通用山村水车的前、右前、右、右后、后、左后、左、左前独立环境参考，另有
2 张引水槽/轮轴及架空层/尾水构造细节、2 张旧木与老铁模拟材质输入。它们用于完善单水轮
Blender 构件、环形巡游路线、近中远景密度与材质方向，不是同一物理现场的标定多视图。

```powershell
$releaseDir = ".nantai-studio\release-downloads\batch22-watermill-local360"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
gh release download synthetic-village-design-inputs-batch22-2026-07-23 `
  --pattern "synthetic-village-watermill-local360-design-pack-batch22-2026-07-23.zip" `
  --pattern "synthetic-village-watermill-local360-design-pack-batch22-2026-07-23.SHA256SUMS.txt" `
  --dir $releaseDir --clobber

$archiveName = "synthetic-village-watermill-local360-design-pack-batch22-2026-07-23.zip"
$archive = Join-Path $releaseDir $archiveName
$sumFile = Join-Path $releaseDir "synthetic-village-watermill-local360-design-pack-batch22-2026-07-23.SHA256SUMS.txt"
$expected = ((Get-Content $sumFile) -split '\s+')[0]
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Batch 22 design pack SHA-256 mismatch" }

Expand-Archive $archive `
  -DestinationPath ".nantai-studio\synthetic-village\hybrid-v4\design-inputs\batch22" -Force
```

ZIP SHA-256 为 `1f842f8ce5eb52bafb5bb6d8a581816e1c7571187537e45ace6af669365fb07f`，
严格只含 12 张最终 PNG、12 份逐图 prompt、manifest、使用说明与 payload checksum；不含旧候选、
判废图、联系表或生成队列。八个方向只是独立的设计角色，没有共享 intrinsics、pose、像素对应、
米制尺度或 360° coverage；禁止作为 SfM/NeRF/3DGS 多视图训练集。两张材质图也只是模拟 albedo
参考，未验证无缝平铺且不含 normal/roughness/metallic/displacement 通道。消费后仍须重建 exact
Blender 场景，并通过平移相机六层实渲、visibility 与 post-render v2 才能形成漫游验收证据。

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

高阶 SH 在平移和统一缩放时保持不变；涉及空间旋转时由 `pipeline/spherical_harmonics.py` 的 Wigner-D 旋转（degree 0–3）正确变换，保留视角相关颜色。`flatten_sh()` 保留为可选降级工具（丢高阶保 DC）。

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
