# 真实重建端到端手册

目标：把有足够重叠的照片或视频变成可在 Nantai Studio 中查看的 3DGS 场景。

## 先看清边界

- 本仓库负责摄取、COLMAP、证据合同、导入、对齐、分块、LOD 和 Viewer。
- 高质量 3DGS 训练需要外部 GPU 训练器；本仓库不内置训练器。
- 当前 Windows 开发机没有可用 NVIDIA CUDA。本机 Brush 可做受限小场景试验；
  高质量主路径是云端 NVIDIA GPU。
- 只能漫游被充分拍摄和成功重建的体积。天空、水、玻璃、无纹理面、移动物体和
  遮挡区可能出现空洞或漂浮物。
- `check-capture` 只分析单图，不能证明相邻图有足够重叠。

## 1. 安装与体检

需要 Python 3.11+、Node.js 20+。Windows：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe make.py doctor
```

`doctor` 会报告 COLMAP、Brush、CUDA、Python 依赖、素材 registry 和磁盘状态。
缺少 GPU 是机器结论，不是 `doctor` 命令失败。

## 2. 采集

建议：

- 绕目标形成闭环，保持连续移动；
- 相邻视角约 60%–80% 重叠；
- 同一区域有前后、左右和高低视差；
- 锁定曝光/焦距，避免数码变焦、运动模糊和大面积动态物体；
- 室内外转场、门洞、桥底和窄路要补双向视角。

图片目录或视频都可先做预检：

```powershell
$env:PHOTOS = "input"
.\.venv\Scripts\python.exe make.py check-capture
Remove-Item Env:PHOTOS
```

视频会在重建时抽帧。长视频应使用 `--fps` 和 `--max-frames` 控制规模；CPU
COLMAP 不适合把全部视频帧直接投入匹配。

## 3. 本机一键链路

本机 COLMAP + Brush 的受限路径：

```powershell
.\.venv\Scripts\python.exe scripts\reconstruct_local.py input `
  --steps 3000 --max-res 1024 --max-frames 300 --chunk-size-m 50
```

关键选项：

- 视频自动使用 sequential matcher；
- 有序连拍照片可加 `--sequential`；
- `--resume` 只复用内容指纹、状态和日志都一致的已完成阶段；
- 已有 COLMAP workspace 可用 `--precomputed-colmap <workspace>`；
- `--chunk-size-m 50` 适合大场景流式加载。

本机 Brush 的质量和规模受集显限制。该路径跑通不等于已经获得可发布的真实场景。

## 4. 高质量路径：COLMAP + 云 GPU

### 4.1 COLMAP

```powershell
.\.venv\Scripts\python.exe -m pipeline.ingest --input input --output photos
.\.venv\Scripts\python.exe -m pipeline.reconstruct `
  --photos photos --reg-engine colmap --engine mock
```

这里 `engine=mock` 只用于先得到/验证相机位姿；mock 几何不是最终 3DGS。
必须检查逐图注册覆盖和拒绝原因，不能只看 COLMAP 是否生成了目录。

### 4.2 云 GPU 训练

仓库提供 `cloud/train_3dgs_nerfstudio.sh`。云机示例：

```bash
bash cloud/train_3dgs_nerfstudio.sh ./photos
```

或手动：

```bash
ns-process-data images --data ./photos --output-dir ./processed
ns-train splatfacto --data ./processed
ns-export gaussian-splat \
  --load-config outputs/<scene>/splatfacto/<run>/config.yml \
  --output-dir exports/splat
```

下载以下最终产物：

```text
point_cloud.ply
training-request.json
training-result.json
```

request/result 只证明其声明并通过内容闭合检查的事实；stub 或失败 result 不能冒充
真实训练。

## 5. 导入、对齐和分块

先归一化训练器可能输出的非单位四元数：

```powershell
.\.venv\Scripts\python.exe scripts\normalize_ply_quats.py trained\point_cloud.ply
```

生成导入合同：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_import.py trained\point_cloud.ply `
  --training-request trained\training-request.json `
  --training-result trained\training-result.json
```

默认合同是 `sfm-local / arbitrary / unaligned`。需要米制 ENU 时，用实测控制点：

```powershell
.\.venv\Scripts\python.exe -m pipeline.alignment `
  --registration recon\registration.json `
  --control-points control_points.json `
  --max-rms 0.25 `
  --out recon\registration-aligned.json
```

也可使用 EXIF GPS：

```powershell
.\.venv\Scripts\python.exe -m pipeline.alignment `
  --registration recon\registration.json `
  --from-gps ingest\manifest.json `
  --out recon\registration-aligned.json
```

消费级 GPS 常见误差为数米；不能据此承诺 sub-metre。

导入并分块：

```powershell
.\.venv\Scripts\python.exe -m pipeline.reconstruct `
  --engine import `
  --registration recon\registration.json `
  --splat recon\splat-input.json `
  --dedup-voxel 0 --replace-margin 0 `
  --chunk-size-m 50 --photos photos
```

分块只做空间重打包，不改变坐标或 provenance，也不会把 `preview-only` 变成
`metric-aligned`。

## 6. 验证与查看

```powershell
.\.venv\Scripts\python.exe make.py inspect-recon
.\.venv\Scripts\python.exe make.py verify-recon-artifacts
.\.venv\Scripts\python.exe make.py serve
```

打开 <http://127.0.0.1:8000/web/studio/>。

验收时至少确认：

1. 注册覆盖率和输入来源；
2. PLY、训练 request/result 与配置 SHA；
3. 坐标 frame、units、transform history；
4. 对齐残差和控制点 span；
5. chunks/LOD 的内容 SHA；
6. 真实 Viewer 中的空洞、漂浮物、遮挡和可达范围。

详细字段与 `control_points.json` / `SplatInput` 结构见
[真实数据工作流](../real-data-workflow.md)。
