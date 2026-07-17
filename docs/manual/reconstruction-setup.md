# 真实重建端到端手册：照片/视频 → 可漫游 3D 场景

> 面向使用者。**诚实优先**——明确区分「本仓库已做的」「你需要做的」「真实限制」。
> 工具版本/命令为 2026-07-15 联网查证。标注：✅ 已验证 · ⚠️ 有风险/未在本机实测。

## 0. 先认清系统边界（重要）

**本仓库不把图片变成 3D 几何。** 它是重建管线**外围**的诚实封装层：
摄取抽帧 → 坐标/位姿契约 → 米制 ENU 对齐 → 3DGS **导入**/拼接/LOD/素材 → Spark Viewer（360° 漫游）。

把图片变成 3D 的**两步是外部的**：

| 步骤 | 做什么 | 本机(Intel UHD 770, 无 CUDA) |
|---|---|---|
| **A. 相机位姿 (SfM)** | COLMAP 求每张图的相机位姿 | ✅ CPU 可跑（我已把仓库默认改成 CPU）|
| **B. 3DGS 训练** | 从图+位姿优化出高斯泼溅 `.ply`（真正的"重建大脑"）| ✅ **本机 Brush 实测可跑**（Intel 集显, 中小场景）；大场景/高质量仍首选云 GPU（gsplat 等要 CUDA）|
| C. 导入+对齐+漫游 | 本仓库 `reconstruct --engine import` + `alignment` + Viewer | ✅ 已就绪 |

**"完美"做不到**（任何技术都不行）：3DGS 对天空/玻璃/水面/无纹理面有空洞和漂浮物；只能漫游你**拍到过**的体积；移动物体会糊。合理预期是"好但有瑕疵"。

## 1. 本机现实（已确认 2026-07-15）

Windows 11 / i7-14700(20核) / 32GB / D盘 1.4TB / **Intel UHD 770 集显（无 NVIDIA、无 CUDA）**。
→ **实测：位姿(A)本机 COLMAP CPU 可跑（30 图 ~46 秒）；训练(B)本机 Brush 也能跑（中小场景）——全本机闭环已跑通**。大场景/高质量仍首选云 GPU。

**核对你自己的机器**（上面是这台开发机的记录，不是你的机器）——跑一次体检，实测同样的事实：

```powershell
.venv\Scripts\python make.py doctor
.venv\Scripts\python scripts\doctor.py --json        # 机读，供脚本/CI 消费
```

它实测 COLMAP / Brush / GPU / Python 依赖 / 素材注册表 / 磁盘，并给出「本机能跑 / 本机不能跑 / **无法判定**」小结——探测不确定的一律进「无法判定」，不替你下结论。

- **退出码恒为 0**：报的是机器状态，不是「合不合格」。「缺 COLMAP」是体检的**结论**（也是很多机器的正常状态），不是体检失败。要按结论决策，请读 `--json` 的 `checks[*].status`（`ok` / `missing` / `degraded` / `unknown`）。
- ⚠️ **GPU 那条是证据推理，不是硬件事实**：它判的是「**未探测到可用的 NVIDIA CUDA 栈**」，依据是找不到随 NVIDIA 驱动一起安装的 `nvidia-smi`。它**不**声称你机器里没有 N 卡（比如驱动没装的 N 卡机也会报这个）。
- ⚠️ **素材 sha 默认不校验**：报告会明写「sha **未校验**」（要哈希全部 PLY，慢）。要实测校验加 `--verify-assets`（`make.py doctor` 不带这个开关，直接调脚本）。

## 2. 已为你准备好的（`third/`，我下载的）

| 工具 | 用途 | 位置 | 能否本机跑 |
|---|---|---|---|
| **COLMAP 4.1.0 no-CUDA** | 相机位姿 (SfM) | `third/colmap/` | ✅ CPU |
| **Brush v0.3.0** | 无 CUDA 的 3DGS 训练器（wgpu/Vulkan）| `third/brush/` | ✅ **实测在 Intel UHD 770 上能训练**（见 §5b）；大场景/高质量受显存与速度限制 |

> `third/` 内容不入库（`.gitignore`），需要时重下即可。

## 3. 需要你做的（我做不了）

1. **拍摄**：50–300 张**高重叠(≥60%)、清晰、曝光稳定**的照片，或一段缓慢平稳的视频（静态主体）。数据质量是结果上限，自动化替代不了。
2. **租云 GPU + 注册账号**（训练那步）：二选一——
   - **Google Colab 免费 T4**（零成本，但会话有时限/断线会清空）：需 Google 账号；
   - **AutoDL / vast.ai / RunPod**（更稳，按小时付费）：需注册+充值。
   账号/实名/付费只能你来。
3. **在云机上跑并看护训练**（~60–90 min on T4），**导出 `.ply` 后再关机**（Colab 断线即清空）。
4. 把训练出的 `point_cloud.ply` 下载回本机。
5. （可选，若要米制/地理对齐）提供控制点或 GPS——见 §6 与 [real-data-workflow.md](../real-data-workflow.md)。

---

## 先跑采集预检（在烧掉几小时之前）

下面那条一键命令的第一步就是 COLMAP——**无序 ~300 图可能跑 2–5+ 小时**（§4 实测）才发现这批图根本没法重建。开跑之前先用**单图就能拿到的证据**看一眼：

```powershell
.venv\Scripts\python make.py check-capture                        # 默认检 photos\
$env:PHOTOS='<你的图片目录>'; .venv\Scripts\python make.py check-capture
.venv\Scripts\python scripts\check_capture.py photos\ --json      # 机读
```

它只做单图分析（解码 + Laplacian 模糊分 + 读 EXIF），**不跑匹配**，成本远低于它想帮你省下的那步。报告内容：张数（对照建议的 50–300）、模糊度、分辨率、EXIF 拍摄时间/GPS 覆盖、建议匹配器（`exhaustive` / `sequential`）与图对数、**由 §4 实测锚点外推的耗时粗估**，最后给一个 `likely` / `risky` / `unlikely` 结论。

> **只吃图片目录**（递归扫描；支持 `.bmp` `.heic` `.jpeg` `.jpg` `.png` `.tif` `.tiff` `.webp`）。**视频输入先抽帧再预检**：`.venv\Scripts\python make.py ingest`（`input\` → `photos\`），再 `make.py check-capture`。直接把 `.mp4` 或只含视频的目录喂给它 → fail-closed 退出码 `2`（实测），报错本身就会告诉你先去抽帧。

⚠️ **别把预检当成功保证**——工具自己在报告末尾也会把这些限制重复一遍，请一起读：

- **重叠度它测不到。** 相邻图重叠 ≥60% 是 SfM 成败的**首要**因素，但那是图**之间**的关系，**单图分析测不出来**。要确知只能真跑 COLMAP——而那正是最贵的一步。
- 所以 `likely` 只意味着「**没发现明显硬伤**」，**不等于能重建**；`unlikely` 也不保证一定失败。
- 一律测不到的还有：曝光一致性、纹理独特性、玻璃/水面/天空占比、移动物体、是否绕拍成环。
- 模糊阈值（默认 `80.0`，与 `pipeline.ingest` 抽帧一致）是**启发式**经验值，受分辨率/纹理/曝光影响：低分不等于一定匹配失败，高分也不保证匹配得上。请自己抽查低分图再决定重不重拍。
- 耗时是**粗估**（由 §4 实测锚点线性外推），不是承诺；小批量（<50 图）它会明显过估，报告里会自己说明。
- **退出码**：`0` = 出了报告（**无论结论好坏**）；`2` = 没法分析（目录不存在 / 没有图片），fail-closed。

## 最简：一键本机重建 ✅（已实测）

拍好图后，一条命令跑完 COLMAP→Brush→导入（无需手动分步）。已在本机合成场景实测通过：

```powershell
# 输入可以是图片目录, 或直接一个视频文件 (自动抽帧)
.venv\Scripts\python scripts\reconstruct_local.py <图片目录或视频.mp4> --steps 3000 --max-res 1024
# 完成后:  .venv\Scripts\python make.py serve   # http://127.0.0.1:8000/web/studio/  360° 漫游
```

- 自动找 `third/` 下的 COLMAP/Brush，探测选项组，全 CPU/集显，无需 CUDA。
- **视频输入**自动抽帧（`--fps`/`--max-frames`，20 分钟视频建议 `--max-frames 300` 左右，别全帧喂 COLMAP）。
- **匹配器自动选**：视频（时序连续帧）→ `sequential_matcher`（只配相邻帧，CPU 上远快于全配对，真实几百帧才跑得动）；无序照片 ≤400 张 → `exhaustive_matcher`。航拍/环绕**连拍照片**若按拍摄顺序命名，加 `--sequential` 同样走快路径。
- `--steps` 越大质量越好越慢（集显上 2000 步 ~5.5 分钟）；`--max-res` 控显存。
- **大场景加 `--chunk-size-m 50`**：额外产出可流式空间分块，viewer 只载相机附近的块（上百万高斯才漫游得动）；本次重建的信任判定自动随分块产物走。缺省不分块。
- 想理解每一步或单独调，看下面 §4–§6 的分步版。

---

## 4. 步骤 A · COLMAP 相机位姿（本机 CPU）✅

> **这就是可能烧掉 2–5 小时的那一步。** 跑之前先过一遍上面的[采集预检](#先跑采集预检在烧掉几小时之前)（`make.py check-capture`）——它只做单图分析、不跑匹配。但记住它**测不到重叠度**，通过预检不是能重建的保证。

**已下到 `third/colmap/`。** 加入 PATH 后本仓库会自动调用（我已把仓库默认改为 **CPU SIFT**，无 N 卡也可靠；有卡想提速加 `--colmap-gpu`）：

```powershell
# 1) 解压后把 colmap 目录加进 PATH（当前会话）
$env:Path = 'D:\vibecoding\nantai\third\colmap;' + $env:Path ; colmap -h   # 验证

# 2) 放图：photos/ 下放照片；视频先抽帧
.venv\Scripts\python -m pipeline.ingest --input input --output photos

# 3) 让仓库驱动 COLMAP → registration.json（CPU，自动）
.venv\Scripts\python -m pipeline.reconstruct --photos photos --reg-engine colmap --engine mock
#   （此步只为得到 recon/registration.json 的真实位姿；engine=mock 的几何是占位，B 步才是真几何）
```

- **⏱ 真实耗时（CPU，i7-14700）**：~100 图 ≈ 20–60 min；~300 图 ≈ 2–5+ 小时（穷举匹配是 O(n²)，视频帧务必控制在几百张、用顺序匹配）。
- ⚠️ 若重叠不足，mapper 可能只注册部分图或不产模型；仓库会报错并建议加重叠/提高帧率。
- COLMAP 只出**稀疏**位姿（`cameras.txt`/`images.txt`），**不是** 3DGS；dense/MVS 需 CUDA，本仓库从不调用（不需要）。

## 5. 步骤 B · 3DGS 训练

### 5a. 云 GPU（推荐 / 质量路）✅

用 **nerfstudio `ns-train splatfacto`**（gsplat 后端）。免费档 = Colab T4；稳定档 = AutoDL RTX 3060 12GB+。
把 `cloud/train_3dgs_nerfstudio.sh` 上传到云机一键跑（内含下列步骤 + 排错提示）：`bash train_3dgs_nerfstudio.sh <图片目录|视频>`。手动等价命令：

```bash
# 云机上（Colab 官方 notebook 会自动装 nerfstudio；AutoDL 选 PyTorch2.x+CUDA11.8 镜像后 pip install nerfstudio，
#   或直接用上面的 cloud/train_3dgs_nerfstudio.sh 一键装+跑。注意: cloud/setup_autodl.sh 是旧素材生成愿景, 不装 nerfstudio）
ns-process-data images --data ./my_images --output-dir ./processed   # 视频用 'video --data my.mp4'
ns-train splatfacto --data ./processed                                # 普通版 ~6GB 显存, 适合免费 T4
ns-export gaussian-splat \
  --load-config outputs/<scene>/splatfacto/<时间戳>/config.yml \
  --output-dir exports/splat                                          # 得 exports/splat/point_cloud.ply
```

- **⏱** T4 上约 60–90 min。**导出 `.ply` 后再断开**（Colab 断线清空一切）。
- 把 `point_cloud.ply` 下回本机 `trained/point_cloud.ply`。
- 输出是标准 INRIA-3DGS PLY，本仓库直接认（`f_dc_*`/`f_rest_*`/`opacity`/`scale_*`/`rot_*`）。
- ⚠️ nerfstudio 历史上 `ns-export` 有颜色/opacity 小 quirk，且四元数可能未归一化 → §6 的 Step 0 归一化**必做**。

### 5b. 本机 Brush（✅ 已实测在 Intel UHD 770 上跑通）

**实测结果（2026-07-15，本机 Intel UHD 770，30 图合成场景）**：Brush 在集显上**成功训练并导出标准 3DGS `.ply`**，**未 OOM、未崩**：
- 200 步 / max-res 512 → 约 **13 秒**，6.9MB ply（29389 高斯）；
- 2000 步 / max-res 1024 → 约 **5.5 分钟**，9.0MB ply。

四元数已是单位、无需归一化，直接被本仓库导入（`geometry_usability=preview-only, synthetic=False`）。**本机确实能做 3DGS 训练，不是只能上云。** 外推：真实质量（数千~上万步）约数十分钟一场景——慢但可用。

用法（COLMAP 数据集布局 `<root>/images/` + `<root>/sparse/0/`）：
```powershell
third\brush\brush_app.exe <数据集目录> --total-steps 2000 --max-resolution 1024 `
  --export-every 2000 --export-path trained --export-name scene.ply
#   --with-viewer 可开训练可视化窗口; 导出普通 .ply(非 .compressed.ply, 加载器不认)
```

**诚实的限制（仍成立）**：集显共享系统内存，**图多/分辨率高/步数大时会显著变慢，超大场景可能 OOM 或驱动超时**；`--total-steps` 越大质量越好但越慢（200 步只是打通流程的欠训练结果，真实质量需数千步）。**用 `--max-resolution` 与 `--max-frames` 控制规模**。质量/速度的天花板仍在云 GPU，但"本机能不能行"的答案是：**能，中小场景可用。**

## 6. 步骤 C · 导入本仓库 → 漫游 ✅（契约已就绪）

拿到 `trained/point_cloud.ply` 后（纯 CPU，本机）：

```powershell
# Step 0（若训练器输出非单位四元数）：归一化 rot_0..3——加载器 fail-closed 拒绝非单位四元数
.venv\Scripts\python scripts\normalize_ply_quats.py trained\point_cloud.ply

# Step 1（一键生成导入契约 registration.json + splat-input.json，并打印导入命令）
.venv\Scripts\python scripts\prepare_import.py trained\point_cloud.ply
#   —— 生成的是诚实的 sfm-local（arbitrary/unaligned）契约；要 metric 见下方与 real-data-workflow.md

# Step 2：导入（prepare_import 打印的命令；--dedup-voxel 0 必须：非米制 frame 拒绝 0.10 默认）
.venv\Scripts\python -m pipeline.reconstruct --engine import `
  --registration recon\registration.json --splat recon\splat-input.json `
  --dedup-voxel 0 --replace-margin 0 --photos photos

# Step 3：读懂你拿到的东西 —— 这几何到底能不能拿去量？
.venv\Scripts\python make.py inspect-recon
#   默认读 web\data\recon\recon_manifest.json；换一份: $env:MANIFEST='<路径>'

# Step 4：查看，360° 漫游
.venv\Scripts\python make.py serve   # http://127.0.0.1:8000/web/studio/
```

- **大场景流式（可选，推荐一步到位）**：真实重建是**单个**可能上百万高斯的 `.ply`，viewer 整块加载——大场景下载慢、无空间裁剪。给导入加 `--chunk-size-m` 即额外产出可流式的块 + LOD，viewer 只载相机附近的块：
  ```powershell
  # 在上面 Step 2 的导入命令后追加即可（本次重建的信任判定自动随分块产物走）
  .venv\Scripts\python -m pipeline.reconstruct --engine import ... --chunk-size-m 50
  ```
  也可对已有 `.ply` 单独分块（需手工传 `--recon-manifest` 才能带上信任判定，否则该字段缺席=未知）：
  ```powershell
  .venv\Scripts\python scripts\chunk_reconstruction.py trained\point_cloud.ply `
    --out-dir web\data\recon-chunks --chunk-size-m 50 --recon-manifest recon\recon_manifest.json
  ```
  纯空间重打包：**不改几何/坐标/provenance**（每个高斯恰好落一个块，无损不重复；每块继承源 `frame_id`/`units`/transform 历史）。**分块不会**把 `preview-only` 变成 `metric-aligned`——米制要在对齐那步挣。实测 12 万高斯/400m 场景 → 64 块（50m 网格）。（viewer 消费 `chunks.json` 待 Codex 接线，见 `handoff/HANDOFF-CODEX-004`。）
- **Step 3 在做什么**：`recon_manifest.json` 里的 `geometry_usability` / `coordinate_contract` / `metric_evidence` 是机器可验证的严谨字段，但人读不出「**这玩意儿能不能量尺寸**」。`inspect-recon` 只做翻译：高斯数、包围盒（**带单位**——不是米制时会直说「不是米，别拿去量」）、能不能测量、精度、变换链、LOD/分块产物，以及**哪些是未知**。
  - **只翻译，绝不提升信任**：manifest 说 `preview-only` 就是 `preview-only`，哪怕包围盒数字看起来像米制。缺字段一律报「未知」，不给好看的数字。
  - **矛盾时证据打败声称**：manifest 声称 `metric-*` 却带 `passed:false` / 对齐证据无法解析 / 单位不是米 / `synthetic=true` —— 它**指出矛盾并按 `preview-only` 处理**，退出码 **2**（可当 CI 门用）。反过来，**检查通过不等于产物能用**：它只说明 manifest **自洽**。
  - **限制**：只读 manifest **声称**的内容 + manifest 内部自洽性。**不碰 PLY 字节**、不校验 artifacts 的 `sha256`、不重算残差——所以它查不出「manifest 自洽但 PLY 被换了」（那要另跑完整性校验）。精度只从 `sim3.alignment.v1` 证据串读；米制若靠别的证据（如实测标尺）挣得，它只能如实说「精度未知」。
  - **退出码**：`0` = 读通了；`2` = manifest 自相矛盾；`1` = 文件不存在 / 不是合法 JSON。
- 结果 `geometry_usability` = **`preview-only`**（sfm-local 非米制/未对齐）——这是**诚实**的：没有控制点就不冒充米制。`inspect-recon` 会把它翻成「不能测量：尺度是任意的，只能看」，并告诉你怎么升级。
- 想要 **`metric-aligned`**（真实尺度/地理对齐）：提供控制点/GPS，走 `pipeline.alignment`（见 [real-data-workflow.md](../real-data-workflow.md)），流程我已打通并验证。
  - ⚠️ **高阶 SH 限制（米制对齐才会遇到）**：米制/地理对齐会把场景经含**旋转**的 Sim3 变到 ENU 世界；而高阶球谐（`f_rest_*`，nerfstudio splatfacto 等训练器都会输出）的**正确旋转本仓库未实现**，加载器对「含高阶 SH + 旋转」**故意 fail-closed 阻断**（绝不施加错误 SH 旋转产生错误颜色）。**诚实解法**：对齐前先扁平化 SH——`python scripts/flatten_ply_sh.py trained/point_cloud.ply`（丢高阶 `f_rest_*`、保 DC 视角无关基色）。代价：失去视角相关高光，保留正确基色。**仅米制对齐需要**；基本 `preview-only` 漫游（不含旋转）无需此步。

---

## 真实风险清单（不藏）

- 本机 Brush **已实测跑通**（Intel UHD 770，中小场景，见 §5b）；但集显共享内存，**图多/高分辨率/大步数仍可能 OOM 或驱动超时**，高质量天花板仍在云 GPU。
- COLMAP CPU 计时看匹配器：**无序照片走 exhaustive（O(n²)），~300 图可能 2–5+ 小时**；**视频/有序连拍走 sequential（只配相邻帧），同样帧数快一个数量级**（脚本已自动选，见一键段）。
- **COLMAP 卡死 backstop**：每阶段（feature/match/map/convert）子进程有 **6 小时** 墙钟上界（`colmap_register(stage_timeout_s=...)` 默认 21600s）。这只防**真正卡死**（headless/集显 OpenGL SIFT 停滞、病态输入、I/O 挂起）时管线无限 hang——6h 远超上面的合法 2–5h，不会误杀慢但在推进的重建。超大 CPU 数据集若合法超 6h/阶段可调大（但已超本手册范围，宜改用云 GPU / sequential）。超时按 fail-closed 抛 `RuntimeError`。
- **长视频的帧密度权衡**：`--max-frames 300` 从 20 分钟里只抽 ~300 帧≈每 4 秒一帧，漫游可能太稀疏→空洞。要么拍更短/更聚焦的视频，要么调大 `--max-frames`（COLMAP 更慢），要么大场景直接上云 GPU。**宁可多段短视频分别重建，也别一条 20 分钟长视频稀疏抽帧。**
- Colab 免费档会断线清空——导出后立即下载。
- AutoDL 是国内云，计费与 GitHub/HuggingFace 权重拉取可能需要相应网络配置。
- COLMAP 选项组命名跨版本不同（`--FeatureExtraction.use_gpu` vs 旧 `--SiftExtraction.use_gpu`）——仓库现**自动探测**已装 build 的命名，两者都适配（本机实测 4.1.0 nocuda 通过）。
- `third/` 大文件自动下载依赖 GitHub 可达；不可达时你手动下（URL 见 `third/README.md`）。
- 结果只覆盖拍到的体积；反光/透明/天空/动体是已知弱项。

## 我已为此做的代码改动

- `pipeline/registration.py`：COLMAP SIFT **默认走 CPU**（`use_gpu=False`），无 N 卡/headless 可靠；`reconstruct --colmap-gpu` 可显式开 GPU 提速。
- `scripts/normalize_ply_quats.py`：训练器 PLY 的四元数归一化预处理（加载器 fail-closed 拒绝非单位四元数，Studio 复用同一语义校验，故不改门、提供预处理）。
- `scripts/flatten_ply_sh.py`：米制对齐前扁平化高阶球谐（丢 `f_rest_*` 保 DC）的诚实预处理——高阶 SH 的正确旋转未实现，加载器对「SH + 旋转」fail-closed，flatten 后旋转对 DC 恒等即可安全对齐（`GaussianScene.flatten_sh()` 同语义，均有测试）。
- `pipeline/spatial_chunk.py` + `scripts/chunk_reconstruction.py`：大重建的空间分块（XY 网格 → per-chunk ply + LOD + `chunks.json` 流式 manifest），让上百万高斯的真实重建可只载相机附近的块。纯重打包：半开区间分箱保证无损不重复，provenance 逐块继承、manifest 如实记录源契约，绝不提升信任。
- `scripts/prepare_import.py`：一键生成导入契约（registration.json + splat-input.json），消除手写易错步骤；生成诚实的 sfm-local frame。
- `scripts/reconstruct_local.py`：**一键本机重建**——串起 COLMAP→Brush→normalize→prepare_import→import。**图片目录与视频文件两种输入均已本机实测端到端跑通**（视频自动抽帧，时序帧走 sequential 匹配）。
- `pipeline/recon_schema.py`：RegistrationResult.engine 增 `"external"`（外部声明的导入配准，比冒充 colmap/mock 诚实）。
- `third/`（gitignored）下载物 + `third/README.md`（下载清单/URL）+ 本手册。整条本机导入链有端到端认证测试（`test_full_local_import_flow_via_scripts`）。
