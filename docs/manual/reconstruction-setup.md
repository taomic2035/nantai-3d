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
- 想理解每一步或单独调，看下面 §4–§6 的分步版。

---

## 4. 步骤 A · COLMAP 相机位姿（本机 CPU）✅

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

# Step 3：查看，360° 漫游
.venv\Scripts\python make.py serve   # http://127.0.0.1:8000/web/studio/
```

- 结果 `geometry_usability` = **`preview-only`**（sfm-local 非米制/未对齐）——这是**诚实**的：没有控制点就不冒充米制。
- 想要 **`metric-aligned`**（真实尺度/地理对齐）：提供控制点/GPS，走 `pipeline.alignment`（见 [real-data-workflow.md](../real-data-workflow.md)），流程我已打通并验证。

---

## 真实风险清单（不藏）

- 本机 Brush **已实测跑通**（Intel UHD 770，中小场景，见 §5b）；但集显共享内存，**图多/高分辨率/大步数仍可能 OOM 或驱动超时**，高质量天花板仍在云 GPU。
- COLMAP CPU 计时看匹配器：**无序照片走 exhaustive（O(n²)），~300 图可能 2–5+ 小时**；**视频/有序连拍走 sequential（只配相邻帧），同样帧数快一个数量级**（脚本已自动选，见一键段）。
- **长视频的帧密度权衡**：`--max-frames 300` 从 20 分钟里只抽 ~300 帧≈每 4 秒一帧，漫游可能太稀疏→空洞。要么拍更短/更聚焦的视频，要么调大 `--max-frames`（COLMAP 更慢），要么大场景直接上云 GPU。**宁可多段短视频分别重建，也别一条 20 分钟长视频稀疏抽帧。**
- Colab 免费档会断线清空——导出后立即下载。
- AutoDL 是国内云，计费与 GitHub/HuggingFace 权重拉取可能需要相应网络配置。
- COLMAP 选项组命名跨版本不同（`--FeatureExtraction.use_gpu` vs 旧 `--SiftExtraction.use_gpu`）——仓库现**自动探测**已装 build 的命名，两者都适配（本机实测 4.1.0 nocuda 通过）。
- `third/` 大文件自动下载依赖 GitHub 可达；不可达时你手动下（URL 见 `third/README.md`）。
- 结果只覆盖拍到的体积；反光/透明/天空/动体是已知弱项。

## 我已为此做的代码改动

- `pipeline/registration.py`：COLMAP SIFT **默认走 CPU**（`use_gpu=False`），无 N 卡/headless 可靠；`reconstruct --colmap-gpu` 可显式开 GPU 提速。
- `scripts/normalize_ply_quats.py`：训练器 PLY 的四元数归一化预处理（加载器 fail-closed 拒绝非单位四元数，Studio 复用同一语义校验，故不改门、提供预处理）。
- `scripts/prepare_import.py`：一键生成导入契约（registration.json + splat-input.json），消除手写易错步骤；生成诚实的 sfm-local frame。
- `scripts/reconstruct_local.py`：**一键本机重建**——串起 COLMAP→Brush→normalize→prepare_import→import。**图片目录与视频文件两种输入均已本机实测端到端跑通**（视频自动抽帧，时序帧走 sequential 匹配）。
- `pipeline/recon_schema.py`：RegistrationResult.engine 增 `"external"`（外部声明的导入配准，比冒充 colmap/mock 诚实）。
- `third/`（gitignored）下载物 + `third/README.md`（下载清单/URL）+ 本手册。整条本机导入链有端到端认证测试（`test_full_local_import_flow_via_scripts`）。
