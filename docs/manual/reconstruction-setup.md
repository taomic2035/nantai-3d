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

正式采集先生成私有 rights/source/policy 输入包，不要手写 SHA 或 policy：

```powershell
New-Item -ItemType Directory -Force .nantai-studio\private | Out-Null
python -m pipeline.production_capture_inputs `
  --output-dir .nantai-studio\private\production-site-a `
  --dataset-id production-site-a `
  --operator "实际采集与权利负责人" `
  --capture-scope "南台村照片与视频采集" `
  --effective-date 2026-07-27 `
  --processing-purpose 3d-reconstruction `
  --processing-purpose internal-evaluation `
  --min-registered-count 90 `
  --min-registered-ratio 0.9 `
  --min-session-coverage-ratio 0.9 `
  --max-unregistered-consecutive-run 5 `
  --min-largest-connected-model-share 0.95
```

只有权利文件明确授权时才添加 redistribution/Release inclusion 参数。producer 不判断
权利，只把 operator 的明确事实写成 canonical、内容绑定且不可覆盖的三份输入。五个
registration 阈值没有默认值；示例值必须按本次采集和正式验收目标确认，不得为过门
而降低。

正式生产训练需要 operator-owned remote config。它至少绑定：

- 安全的 SSH alias、绝对私钥路径；
- 独立 known-hosts 文件和预期 host-key fingerprint；
- 绝对 remote root / remote repository root；
- `docker` 或 `podman`；
- 带 digest 的不可变 CUDA container identity；
- operator 内容锁定的 `remote_target_sha256`；
- 远端 readiness config SHA、worker 文件 SHA 和稳定 worker version。
- 一份 canonical `production-runtime-policy.v1` 的绝对私有路径及其
  `content_sha256`。

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

remote config 还必须包含：

```json
{"expected_runtime_policy_sha256":"<policy content_sha256>","remote_target_sha256":"<operator-bound remote target sha256>","runtime_policy_path":"C:/absolute/private/production-runtime-policy.json"}
```

这里的 policy 必须显式绑定 exact commit、远端目标身份 SHA、固定 probe-set SHA、
不可变 container identity、预期 GPU UUID/显存、CUDA/Python/Nerfstudio 版本、
`ns-train` CLI schema 及六个 executable SHA。没有来自获批镜像与目标 GPU 的真实值时
不得填写重复字符占位值。executor 会在任何 SSH 副作用前重新打开并验证 canonical
policy；worker 会在创建容器前再次验证同一 `content_sha256`，并把它写入 job spec、
status 和 lifecycle v2。policy 缺失、被替换或与 container/worker identity 不一致时
训练不可达。

固定 probe-set SHA 由代码推导，不能手写：

```powershell
python -c "from cloud.production_runtime_entrypoint import fixed_production_probe_set_sha256 as f; print(f())"
```

worker spec 已升级为 v3，status/lifecycle 保持 v2。fresh container 的主入口现在是
`cloud/production_runtime_entrypoint.py`：它在同一 container instance 内测量 GPU、
CUDA、Python、Nerfstudio、`ns-train splatfacto` CLI schema 与六个 executable 的
前后快照；measurement/policy/decision 以 no-replace 方式耐久发布且重新验证为
`accepted` 后，才用 `exec` 替换为训练进程。worker 在创建容器前还会校验 entrypoint
和 host container-runtime 的 policy SHA。旧 attempt 不能补写这些绑定，必须创建
fresh attempt；代码测试通过也不等于已经取得真实 GPU accepted evidence。

clearance 三件套先发布到 attempt 根目录的 `production-runtime/`，不会提前创建
训练脚本要求必须不存在的 `runtime/production-run`。只有训练成功后，worker 才以
no-replace 方式把三件套和完整 container ID 物化到 result root，生成严格白名单的
`nantai.remote-result-bundle.v2`。caller 会重新绑定 lifecycle、status、job/attempt、
remote target、durable job ref、workspace、container、训练与 held-out render；
archive 验证通过后才在本地派生 `render-evaluation/decision.json` 与
`production-training-closure.json`，避免 closure 反向进入自身绑定的 archive 形成
循环 SHA。

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

手动训练至少下载以下最终产物：

```text
point_cloud.ply
training-request.json
training-result.json
```

request/result 只证明其声明并通过内容闭合检查的事实；stub 或失败 result 不能冒充
真实训练。

通过 `train-production` 的正式远程路径时，不要手工拼装上述文件。verified fetch
会落地以下八个 import 合同入口：

```text
remote-result/result-bundle-manifest.json
remote-result/production-runtime/measurement.json
remote-result/production-runtime/policy.json
remote-result/production-runtime/decision.json
remote-result/render-evaluation/policy.json
remote-result/render-evaluation/report.json
remote-result/render-evaluation/decision.json
remote-result/production-training-closure.json
```

同目录还包含 archive 白名单中的 PLY、训练 provenance、dataparser transform、日志、
相机与渲染 payload。八个入口齐全也不自动代表正式版：runtime decision、render
decision、closure、米制 alignment 与最终 Viewer/human acceptance 必须对同一 scene
identity 全部重新验证通过。

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
$source = (Resolve-Path .nantai-studio\private\production-site-a\production-source.json).Path
$rights = (Resolve-Path .nantai-studio\private\production-site-a\capture-rights-receipt.json).Path
$registrationPolicy = (Resolve-Path .nantai-studio\private\production-site-a\registration-policy.json).Path
$workspace = (Resolve-Path .nantai-studio\real-scene).Path
$runId = "production-site-a"
$paths = python -m pipeline.real_scene_paths `
  --source "$source" `
  --workspace "$workspace" `
  --run-id "$runId" | ConvertFrom-Json
$run = $paths.workspace_root
$importRoot = $paths.import_root
python -m pipeline.viewer_inputs `
  --import-root "$importRoot" `
  --output-dir "$run\viewer-inputs"
```

`pipeline.real_scene_paths` 不猜目录名：它重开 source 和该 identity 的全部 import stage
receipts，选择时间与 attempt ID 最新的一份，只接受 `completed`，复核 receipt 文件名、
每个 output SHA/字节数和完整 production import。最新 receipt 为 blocked/unknown、
出现异源 receipt、路径链接或任何字节漂移时命令返回 2，不会回退到旧 import。

producer 只接受 metric-aligned production import，并从 accepted aligned COLMAP
registration 以 `registered-camera-maximin-v1` 确定性选出三个空间分离机位。生成的
`nantai.viewer-camera-set.v2` 同时内容锁定 import receipt、aligned registration 和
scene manifest；policy 精确绑定三个 pose ID。输出目录必须不存在，不会覆盖旧证据。

正式采集使用一个 session runner，不再手工协调两个终端和固定端口：

```powershell
python -m pipeline.viewer_session `
  --project-root . `
  --import-root "$run\imported" `
  --policy "$run\viewer-inputs\policy.json" `
  --camera-set "$run\viewer-inputs\cameras.json" `
  --output "$run\viewer\performance-report.v2.json" `
  --decision "$run\viewer\performance-decision.json" `
  --human-review-policy-output "$run\review\human-review-policy.json" `
  --evidence-root "$run"
```

runner 会先完整复验 import，在临时回环端口启动 receipt-bound Studio，把精确 URL、
scene manifest、policy、camera set 和 evidence root 传给现有 Node 采集器，并在成功、
拒绝或进程异常后关闭服务器。默认打开可见 Chromium，便于观察实际效果；CI 或无人值守
运行时才加 `--headless`。只有 Viewer v2 机器门通过后，它才调用
`pipeline.human_review_inputs` 生成绑定同一 report 的人审策略。

服务器只接受 `production-acceptance / metric-aligned / meters`。浏览器只能访问
receipt 白名单中 `web/` 下的 manifest、PLY 和 chunks；训练输入、控制点、日志及其它
私有文件不会被映射。挂载字节在启动后变化时资源与 Studio 状态都会 fail closed，也
不会回落到仓库 demo。

只做人工浏览、不生成验收证据时，使用同一个 source/workspace/run identity 启动：

```powershell
python make.py real-scene `
  "SOURCE=$source" `
  "WORKSPACE=$workspace" `
  "RUN_ID=$runId" serve
```

该入口先只读重验五阶段 authoritative acceptance，再解析并复验最新 production
import，交叉核对 source SHA 后以前台方式启动 loopback Studio。它不写 stage
receipt，也不代表 Viewer QA 已通过。正式证据必须使用上面的
`pipeline.viewer_session`，避免把人工浏览误当成可签署证据。

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

如已有独立 capture，也可单独运行 `python -m pipeline.human_review_inputs` 恢复生成
策略。检查三张原始截图后，reviewer 必须逐项填写全部七类结论；截图路径直接从已验证的
Viewer report 导入，不再重复填写 `--screenshot`：

```powershell
python scripts/record_real_scene_review.py `
  --run-root "$run" `
  --reviewer "实际审核人" `
  --policy "$run\review\human-review-policy.json" `
  --viewer-report "$run\viewer\performance-report.v2.json" `
  --disposition scene-envelope=accepted `
  --disposition floaters=accepted `
  --disposition view-dependent-colour=accepted `
  --disposition exposure-seams=accepted `
  --disposition transparent-surfaces=accepted `
  --disposition navigable-holes=accepted `
  --disposition fidelity-label=accepted
```

命令中的 `accepted` 只是填写格式示例，必须按截图真实观感改成 `accepted`、
`rejected` 或 `unknown`。脚本会重开 Viewer policy、camera set、scene、代码与截图，
任一 SHA、字节数、pose 顺序或路径漂移都会拒绝写入；它不会替 reviewer 自动接受。

七类结论全部完成后，使用同一个 source、workspace、run ID、控制点和 geo origin
执行最终 aggregate。local-capture 的媒体、rights 和 registration policy 也必须重复
提供，runner 会重新绑定既有阶段 receipt，不会相信命令行自报的成功：

```powershell
python -m scripts.real_scene accept `
  --source "$source" `
  --workspace "$workspace" `
  --run-id "$runId" `
  --media-root "C:\private\capture" `
  --rights "$rights" `
  --policy "$registrationPolicy" `
  --control-points "C:\private\control-points.json" `
  --geo-origin "26.0801,119.2967,12.5" `
  --viewer-policy "$run\viewer-inputs\policy.json" `
  --viewer-report "$run\viewer\performance-report.v2.json" `
  --human-review-policy "$run\review\human-review-policy.json" `
  --human-visual-review "$run\evidence\human-visual-review.json"
```

只有返回的 accept stage receipt 为 `status=completed`，且内容寻址 aggregate 与 latest
pointer 都发布成功，才说明仓库内 Production V1 门全部通过。`blocked`、`unknown`、
命令返回 2 或 Studio 显示 invalid evidence 都不得发布；修复证据后用 `--retry` 新建
attempt，不能覆盖旧 receipt。

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
