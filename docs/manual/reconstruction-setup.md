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

需要 Python 3.11+、仓库锁定的 Node.js 22.14.0。Windows：

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

重复检查已完成阶段使用 `RESUME=1`；`blocked/failed` 后重新执行必须显式
`RETRY=1`。`train-production` 为 `unknown` 时，`RESUME=1` 会严格重开原
job/bundle/config 证据并继续 poll/fetch，同一 attempt 内不会再次 submit；
`RETRY=1` 才会创建新的 attempt。resume 会重新打开并哈希 receipt 绑定的 bytes，
不以“文件存在”作为成功。
发现已完成 bytes 损坏时会保留旧 completed receipt，并新增 blocked evidence。

内部 canary 的数据是 `internal-only`，没有声明可再发行许可；Brush 输出始终是
`preview-only / arbitrary / unaligned`。实测结果和完整 SHA 见
[2026-07-26 canary 报告](../verification/2026-07-26-real-golden-path-canary.md)。

正式生产训练需要 operator-owned remote config。它至少绑定：

- 安全的 SSH alias、绝对私钥路径；
- 独立 known-hosts 文件和预期 host-key fingerprint；
- 绝对 remote root / remote repository root；
- `docker` 或 `podman`；
- 带 digest 的不可变 CUDA container identity；
- 远端 readiness config SHA、worker 文件 SHA 和稳定 worker version。

配置、私钥和私有主机地址不得提交到仓库。具备这些输入后才运行：

```bash
.venv/bin/python make.py real-canary RUN_ID=my-real-canary \
  REMOTE_CONFIG=/absolute/private/remote.json train-production
```

### 3.6 先做无训练副作用的远端体检

远端体检不会上传 bundle、创建 job 或启动容器。它只运行固定命令
`nantai-remote-readiness-checker`，实测容器 runtime、不可变镜像 digest 和 worker
文件/版本。先在远端部署仓库内的纯标准库 checker：

```bash
sudo install -m 0755 cloud/remote_readiness_checker.py \
  /usr/local/bin/nantai-remote-readiness-checker
```

由 operator 在远端创建 `/etc/nantai/remote-readiness.json`。文件必须是单行、
key 排序、末尾换行的 canonical JSON，且只含以下字段：

```json
{"container_identity":"registry.example/nantai@sha256:<64-hex>","container_runtime":"docker","schema":"nantai.remote-readiness-config.v1","worker_path":"/srv/nantai-3d/cloud/remote_training_worker.py","worker_python":"/usr/bin/python3"}
```

先在远端直接运行一次，取得输出中的 `checker_config_sha256`、`worker_sha256` 和
`worker_version`，再把三者作为 `expected_checker_config_sha256`、
`expected_worker_sha256`、`expected_worker_version` 写入本地私有 remote config。
随后在本机执行：

```powershell
.\.venv\Scripts\python.exe make.py real-scene `
  REMOTE_CONFIG=C:/absolute/private/remote.json `
  PREFLIGHT_REPORT=C:/absolute/private/remote-preflight.json `
  preflight-remote
```

只有 canonical report 的 `status=ready` 才允许进入 production submit。超时、连接
失败、镜像/worker 不匹配、输入在检查期间被替换都会 fail closed。报告采用独占创建，
不会覆盖旧证据；请为每次 fresh 检查使用新文件名，并把报告留在 operator 私有目录。

连接丢失或远端状态无法验证时结果必须是 `unknown`；`RESUME=1` 只重连原 job，
不会上传、初始化、启动或重复提交；operator 明确使用 `RETRY=1` 才创建新 attempt。

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

### 6.1 生成 Production Viewer v2 证据

正式验收不能手写 Viewer 报告，也不能手写或任意挑选三机位。先从已经完整复验的
`production-acceptance` import 生成 provenance-bound Viewer 输入：

```powershell
$run = (Resolve-Path .nantai-studio\runs\production-site-a).Path
python -m pipeline.viewer_inputs `
  --import-root "$run\imported" `
  --output-dir "$run\viewer-inputs"
```

producer 只接受 metric-aligned production import，并从 accepted aligned COLMAP
registration 以 `registered-camera-maximin-v1` 确定性选出三个空间分离机位。生成的
`nantai.viewer-camera-set.v2` 同时内容锁定 import receipt、aligned registration 和
scene manifest；policy 精确绑定三个 pose ID。输出目录必须不存在，不会覆盖旧证据。

在另一个 PowerShell 终端启动 Studio，并显式只读挂载同一份 import：

```powershell
$env:REAL_SCENE_IMPORT_ROOT = "$run\imported"
python make.py serve
```

等价的底层命令是：

```powershell
python -m pipeline.studio_server `
  --root . `
  --host 127.0.0.1 `
  --port 8000 `
  --real-scene-import-root "$run\imported"
```

服务器启动前会重新验证 `import-receipt.json` 和全部绑定产物，只接受
`production-acceptance / metric-aligned / meters`。浏览器只能访问 receipt 白名单中
`web/` 下的重建 manifest、PLY 和 chunks；训练输入、控制点、日志及其它私有文件不会
被映射。挂载字节在启动后发生变化时请求会 fail closed，不能回落到仓库 demo。

保持该 Studio 进程运行，然后执行：

```powershell
node scripts\capture_viewer_acceptance.mjs `
  --policy "$run\viewer-inputs\policy.json" `
  --camera-set "$run\viewer-inputs\cameras.json" `
  --studio-url http://127.0.0.1:8000/web/studio/ `
  --scene-manifest "$run\imported\web\recon_manifest.json" `
  --output "$run\viewer\performance-report.v2.json" `
  --decision "$run\viewer\performance-decision.json" `
  --source-role production-acceptance `
  --evidence-root "$run" `
  --headless
```

`output`、`decision` 和固定截图路径必须尚不存在；碰撞会拒绝，不会覆盖。采集器在
启动浏览器前复核 camera-set v2 的 scene manifest SHA，并把
本次 capture script、Viewer probe 和 Playwright package 独占复制到证据根，记录
Node/Chromium 可执行文件前后身份，按 camera set 顺序生成三张内容寻址截图。随后
Python validator 使用同一个 `--evidence-root` 重开 policy、scene、camera set、代码、
package 和截图，重算报告 ID 与所有性能门。Studio 实际返回的
`recon_manifest.json` 和 `acceptance-probe.mjs` 响应字节也必须与 receipt 绑定一致；
否则即使本地路径正确也会拒绝。

最终 aggregate 还会把 camera-set v2 的 import receipt 和 aligned registration SHA
与 accepted import 重新交叉核对。人工 review 必须引用 v2 receipt 中完全相同的
pose ID、相对路径、SHA-256 和字节数。
Viewer v1 报告只可用于 `internal-canary`，不能进入 production aggregate。即使 v2
机器门通过，在同一 scene 的真实 CUDA 3DGS、米制对齐和人工观感验收完成前仍不得
声明 Production V1。

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
