# 真实重建端到端手册

目标：把有足够重叠的照片或视频变成可在 Nantai Studio 中查看的 3DGS 场景。

## 先看清边界

- 本仓库负责摄取、COLMAP、证据合同、导入、对齐、分块、LOD 和 Viewer。
- 高质量 3DGS 训练需要外部 GPU 训练器；本仓库不内置训练器。
- 当前已验证的 Apple Silicon Mac 没有可用 NVIDIA CUDA。本机 Brush 可做受限
  小场景试验；高质量主路径是云端 NVIDIA GPU。任何机器都应重新运行 `doctor`，
  不要从文档推断其 GPU 状态。
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

## 3.5 内容寻址的 Production V1 runner

仓库提供分阶段、可恢复的真实链路。内部 `poster` canary 固定源版本且只能用于
机制验证：

```bash
.venv/bin/python make.py real-canary RUN_ID=my-real-canary fetch
.venv/bin/python make.py real-canary RUN_ID=my-real-canary sfm
.venv/bin/python make.py real-canary RUN_ID=my-real-canary train-preview
```

重复检查已完成阶段使用 `RESUME=1`；失败或 `unknown` 后重新执行必须显式
`RETRY=1`。resume 会重新打开并哈希 receipt 绑定的 bytes，不以“文件存在”作为成功。
发现已完成 bytes 损坏时会保留旧 completed receipt，并新增 blocked evidence。

内部 canary 的数据是 `internal-only`，没有声明可再发行许可；Brush 输出始终是
`preview-only / arbitrary / unaligned`。实测结果和完整 SHA 见
[2026-07-26 canary 报告](../verification/2026-07-26-real-golden-path-canary.md)。

正式生产训练需要 operator-owned remote config。它至少绑定：

- 安全的 SSH alias、绝对私钥路径；
- 独立 known-hosts 文件和预期 host-key fingerprint；
- 绝对 remote root / remote repository root；
- `docker` 或 `podman`；
- 带 digest 的不可变 CUDA container identity。

配置、私钥和私有主机地址不得提交到仓库。具备这些输入后才运行：

```bash
.venv/bin/python make.py real-canary RUN_ID=my-real-canary \
  REMOTE_CONFIG=/absolute/private/remote.json train-production
```

连接丢失或远端状态无法验证时结果必须是 `unknown`；`RESUME=1` 不会重复提交，
除非 operator 明确使用 `RETRY=1` 创建新 attempt。

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

内部 canary 通过不等于 Production V1。商用验收还必须使用
`production-acceptance` source、可机读 rights receipt、至少四个非共面实测控制点、
真实 CUDA 训练产物和完整 Viewer/画质/人工验收。
