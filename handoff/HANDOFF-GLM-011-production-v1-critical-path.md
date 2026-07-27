# HANDOFF-GLM-011 — Production V1 当前连续队列

日期：2026-07-27

Owner：GLM lane
Reviewer：Codex

这是 GLM 当前唯一的 P0/P1 执行入口。完成一项后直接进入下一项，不等待再次分配。
已完成过程压缩在 [HISTORY.md](HISTORY.md)，完整原文从 Git 历史读取。

## 当前结论

Production V1 仍未完成。仓库已经闭合本地 caller、真实照片 COLMAP canary、受限
Brush preview、远程 transport 演练，以及测量 / policy / decision 分层的正式
import 门；仍缺真实云 GPU 训练产物、真实场景实测控制点和真实 Viewer/human
acceptance。

## GLM 立即执行队列（不要等待 Codex 再次分配）

下面 8 个任务按顺序连续执行。每完成一个就做路径限定小提交、用一次性代理 push，
然后立即进入下一项。Codex 当前独立实现
`pipeline/production_training_closure.py` 与对应测试；GLM 不得修改该路径，也不得
新建平行的 result-closure schema。

### NOW-1 — 删除 superseded drill 草稿

允许路径：

- `pipeline/training_executor.py`
- `tests/test_training_executor.py`

动作：

1. 从当前未提交 diff 删除 `RemoteTrainingDrill*`、caller 自报 `outcome="pass"` 及
   对应约 500 行测试；
2. 保留 main 上由 `pipeline/remote_training_drill.py` 和 `4150cfb` 提供的固定
   11-case drill；
3. 运行 `pytest -q tests/test_training_executor.py tests/test_remote_training_drill.py`。

完成信号：这两个文件相对 `HEAD` 无 superseded drill diff。不要为了“保留工作量”
迁移代码。

### NOW-2 — 把 G3 收窄为 host preflight

允许路径：

- `cloud/remote_readiness_checker.py`
- `tests/test_remote_readiness_checker.py`

动作：

1. 删除 caller 可传的任意 `nerfstudio_python`、GPU name/memory/driver 数值；
2. 固定只读 probe registry，只测 SSH/host key、container runtime、immutable
   image digest 可解析、worker/checker identity 与 GPU scheduler 前置条件；
3. 输出字段必须明确为 `host-preflight`，不得出现 production `ready=true`；
4. timeout、非 UTF-8、截断、缺命令、image 不可解析统一 fail closed；
5. 先写 RED：宿主有 Python/Nerfstudio 也不能证明训练容器 ready。

专项门：

```powershell
python -m pytest -q tests/test_remote_readiness_checker.py
python -m ruff check cloud/remote_readiness_checker.py tests/test_remote_readiness_checker.py
git diff --check
```

### NOW-3 — G3 防欺骗与 TOCTOU

仍只修改 NOW-2 两条路径：

1. probe command definition、resolved executable path、regular-file SHA 和 size
   内容寻址；
2. container runtime 与 checker executable 前后各取一次快照；
3. wrapper/path/file identity 任一变化即 blocked；
4. stdout/stderr 分别设固定 byte cap，先截断/标记再做 secret redaction；
5. 增加 wrapper spoof、symlink、mid-probe replace、oversize、secret、timeout、
   malformed observation 的 RED/GREEN 用例。

不要在本任务构造 G2 measurement；G2 只能来自 fresh job container。

### NOW-4 — 审计并测试 fresh container 生命周期

允许路径：

- `cloud/remote_training_worker.py`
- `tests/test_remote_training_worker.py`

先只做行为测试与最小实现，不接 G2 schema：

1. `docker create` 使用 immutable digest 并取得完整 container ID；
2. 后续 clearance 和 training 明确指向同一 ID；
3. durable result/failure publication 完成前不能 remove；
4. wrong ID、short ID、inspect digest drift、start 失败、publication partial、
   reconnect replay 都必须 fail closed；
5. shell argv 必须结构化传递，禁止字符串拼接 secret/路径。

如果现有 worker 的职责不适合直接创建容器，先交付一个可复现 RED 测试和最小接口
合同，不要大改 remote caller。

### NOW-5 — G4 remote caller 第一小步

允许路径：

- `pipeline/remote_shell_executor.py`
- `tests/test_remote_shell_executor.py`

动作：

1. 移除当前 diff 中平行 `RemoteReadinessEvidence.v2`；
2. 新增 fresh-container lifecycle receipt，只记录 job/attempt/workspace、
   immutable digest、完整 container ID 与 durable state transition；
3. receipt 不得包含 caller 自报 GPU/CUDA/Nerfstudio pass；
4. no-replace publication，覆盖 collision、wrong-attempt、container swap、
   result swap、partial/sync failure；
5. reconnect 必须恢复同一 attempt/container，不得静默创建替代实例。

完成后停在接口边界，等 Codex review；不要自行复制
`production_runtime_evidence.py` 的模型。

### NOW-6 — G4 clearance probe adapter

只有 NOW-5 review 通过后才开始。仍限 remote shell 两条路径：

1. 在同一 fresh container 内运行固定六 probe；
2. 原始 observation 交给 Codex 已有
   `pipeline.production_runtime_evidence` 构造 measurement/policy/decision；
3. decision 非 accepted，训练入口必须保持不可达；
4. accepted 后同一 container 才进入 training；
5. 增加 container swap、executable drift、GPU UUID drift、CUDA/Python/
   Nerfstudio/CLI schema drift 与 probe TOCTOU 用例。

### NOW-7 — 外部门控 blocked report

允许新增：

- `pipeline/production_external_inputs.py`
- `tests/test_production_external_inputs.py`

交付 canonical、duplicate-key-safe、内容寻址的 blocked report，逐项列出：

- SSH endpoint 与 pinned host key；
- immutable CUDA image digest；
- rights-cleared production dataset identity；
- Nerfstudio `1.1.5` / Splatfacto requirement；
- 至少四个非共面实测控制点；
- production Viewer human acceptance 尚未取得。

不得记录 token、私钥、完整环境变量、私有绝对路径；blocked report 不得含
`ready`、`verified-production`、`metric-aligned` 或 release-allowed 声明。

### NOW-8 — 静态安全审计转成 RED 测试

允许路径：

- `cloud/train_3dgs_nerfstudio.sh`
- `tests/test_cloud_prepared_training_script.py`
- `cloud/remote_training_worker.py`
- `tests/test_remote_training_worker.py`

逐项检查 mutable image/tag、未固定 CLI、shell injection、结果自报成功、发布前
清理、日志泄密。每发现一项先加入可重复 RED 测试，再做最小修复；没有复现的猜测
不要改生产代码。

### 每项回执模板

GLM 每次只回：

1. `NOW-n` 与 commit SHA；
2. 精确修改路径；
3. RED 测试名及失败原因；
4. GREEN 命令、passed/skipped 数；
5. ruff 与 `git diff --check`；
6. 尚未解决的风险；
7. 已自动开始的下一项编号。

这份队列本身就是继续工作的授权；只有需要新 secret、真实 endpoint、付费 GPU 或
修改 Codex-owned 路径时才停下来询问。

### 已关闭，不要重做

| 项 | 证据 |
|---|---|
| P0 跨平台基线 | `0247440` 及后续 exact-head CI |
| P1-1 Viewer runtime | CI run `30238069052` |
| P1-2 remote readiness v1 基线 | `207eba2`, `cb189a8` |
| P1-3A/B/C reconnect / retry | `6bb1c47` 至 `e2082a6` |
| P1-3D 固定 11-case 演练 | `4150cfb`，remote artifact accepted |
| P1-4A 对应点与退化门 | `42df736` |
| P1-4B 测量 / policy / decision | `0ad9417` |
| P1-4C production import / runner 复验 | `23a2ece`, `8693848` |
| G2 production runtime evidence 合同 | `cba2a19` |
| G5 production result closure 合同 | `5a0ca09` |

`P1-3D` 的证据范围只是 `transport-fixture`，不等于云 GPU 训练；`P1-4C` 已禁止
runner 仅凭低 RMS 放行，但这只证明 caller 能验证真实证据，不代表已经取得真实
测量。

## 共享工作树即时审计

GLM 当前未提交草稿涉及：

- `cloud/remote_readiness_checker.py`
- `pipeline/remote_shell_executor.py`
- `pipeline/training_executor.py`
- 四个对应测试文件

2026-07-27 当前窄回归为 `162 passed, 3 skipped`，ruff 绿色，但仍不得整体提交。
测试绿没有解决以下信任问题：

1. 不能原地改变 `nantai.remote-readiness-evidence.v1` 的字段语义；
2. caller 传入的 GPU 名、显存、driver 或任意 `nerfstudio_python` 不构成观测；
3. 宿主 Python 版本不等于 immutable production execution environment；
4. 还缺 CUDA runtime、GPU UUID、训练 CLI schema 和 executable identity；
5. 所有 probe 必须防 wrapper spoof、路径替换和 probe 中途 TOCTOU；
6. `training_executor.py` 中 caller 可自报 pass 的 P1-3D 草稿已经被 `4150cfb`
   替代，必须删除而不是提交。

## GLM 连续任务包

执行纪律：每包 RED → GREEN → ruff → `git diff --check`，路径限定小提交并使用一次性
代理 push。不得把当前 1600 行草稿一次提交。若没有真实端点，交付稳定
`blocked-external-input` 机器报告后继续下一包。

### G1 — 清理草稿与冻结 v1 兼容性

允许路径：

- `cloud/remote_readiness_checker.py`
- `pipeline/remote_shell_executor.py`
- `tests/test_remote_readiness_checker.py`
- `tests/test_remote_shell_executor.py`
- 删除 `pipeline/training_executor.py` / 对应测试中被 `4150cfb` 替代的草稿

完成定义：

- 原 P1-2 v1 fixture、canonical bytes、content SHA 和 caller 行为保持不变；
- 新生产 GPU 信息不得塞入 v1；
- 为生产 runtime 新建独立 schema，不接受 caller 自报 observation；
- 先提交一个仅恢复边界、删除 superseded 草稿的小提交。

拒绝条件：修改旧 v1 golden bytes、保留 caller `outcome="pass"`、一个提交混入后续
GPU 逻辑。

### G2 — Production runtime evidence schema（已由 Codex 关闭）

已交付：

- `pipeline/production_runtime_evidence.py`
- `tests/test_production_runtime_evidence.py`

`cba2a19` 已实现：

- 独立 canonical schema 绑定 remote host key、job/workspace identity、immutable
  container digest、GPU UUID/name/memory、driver、CUDA runtime、Python、
  Nerfstudio 和 `ns-train splatfacto` CLI schema；
- 每个可执行文件绑定 resolved path、regular-file bytes SHA、size、版本输出和
  probe command definition SHA；
- report 同时绑定 exact commit、clean tree、probe set SHA 与原始 observation SHA；
- duplicate key、unknown field、非 ASCII/noncanonical、NaN/Inf、缺项和 SHA 漂移
  全部拒绝；
- `ready` 必须由模型重算，不能由 caller 提供。

GLM 不要再在 `remote_shell_executor.py` 或 checker 内定义平行的
`RemoteReadinessEvidence.v2` 信任模型。G3 只保留无副作用 host preflight；
job-bound 的 G2 measurement 必须在 G4 的 fresh training container 内产生。

### G3 — Fixed read-only host preflight

优先路径：

- `cloud/remote_readiness_checker.py`
- `tests/test_remote_readiness_checker.py`

完成定义：

- probe registry 固定且内容寻址，caller 不能传任意命令；
- 只证明 SSH/host key、container runtime、immutable image 可解析、worker/checker
  identity 和 GPU 调度前置条件；
- preflight 不安装包、不改 PATH、不创建/启动容器、不跑 SfM/训练；
- host executable 做前后快照；变化即 TOCTOU blocked；
- stdout/stderr 有大小上限和 secret redaction，非 UTF-8/截断/timeout 稳定 blocked。
- checker 不输出 production `ready=true`，也不构造 G2 measurement；宿主
  `nvidia-smi`/Python/Nerfstudio 不能代表随后创建的训练容器。

无 GPU 时 fixture 仍要证明 parser 与状态机，但状态只能是 transport preflight
结果或 `blocked-external-input`。

当前 1747 行草稿不能直接提交：它仍运行宿主任意 `nerfstudio_python`、没有真实
CUDA runtime 和 `ns-train splatfacto` CLI schema，并在 remote shell 内重复定义
G2 schema。先把 `training_executor.py` 中被 `4150cfb` 替代的 drill 草稿从 diff
移除；再只提交 host preflight + 对应测试。`docs/manual` 等代码闭环后另交。

### G4 — Fresh job-container clearance 与 remote caller

允许路径：

- `pipeline/remote_shell_executor.py`
- `pipeline/real_scene_operations.py`
- 对应测试

完成定义：

- host preflight 通过后才分配 durable job/attempt/workspace；
- worker 不再用无法取得实例身份的 `docker run --rm`；先以 immutable digest
  `create` fresh job container，记录完整 container ID，再 `start` 同一实例；
- 同一 container 的入口先执行 fixed clearance probes，实测 GPU UUID/name/memory/
  driver、CUDA runtime、Python、Nerfstudio `1.1.5`、`ns-train splatfacto` CLI
  schema 和六个 executable 前后快照；
- raw probes 绑定 job/workspace/container environment SHA，构造并验证 G2
  measurement/policy/decision；decision 非 accepted 时绝不启动训练；
- clearance accepted 后才在同一 container process chain 中训练；不得换 container；
- result bundle v2 绑定 runtime measurement/policy/decision canonical bytes 与 SHA，
  旧 result bundle v1 保持历史可读但不能满足 Production V1；
- reconnect 后必须恢复同一 probe attempt，不得新建“看起来成功”的 attempt；
- absence/timeout/transport unknown 映射为 `blocked-external-input` 或 `unknown`，
  永远不映射 ready；
- no-replace durable publication，覆盖 collision、partial、sync failure、replay、
  wrong-attempt、container swap 和 result swap；
- result/failure evidence耐久发布完成后才清理 job container。

联合回归必须包含 readiness + remote shell + operations + runner，不只跑 checker。

### G5 — Production result closure（已由 Codex 关闭）

已交付：

- `pipeline/production_training_closure.py`
- `tests/test_production_training_closure.py`

`5a0ca09` 已实现严格 result-bundle v2 whitelist、fresh-container identity、
runtime measurement/policy/decision、request/result/attempt、训练输出、dataparser
与 held-out render 的内容寻址闭环。GLM 不要建立平行 schema。

接入时必须先运行已有 raw verifier，再调用 closure：

1. `verify_remote_result_bundle` 验 archive whitelist、regular file、SHA/size；
2. `validate_training_provenance` 验 authoritative config/log/PLY/input bytes；
3. dataparser validator 验 identity transform；
4. render evaluator 验相机、render bytes、帧指标与 policy；
5. 最后才允许 `derive_production_training_closure` 绑定上述身份。

closure 不是 PLY parser，也不替代 raw-byte verifier。caller 若跳过 1–4，即使能构造
模型对象也不得发布 verified result。

### G6 — 把 result closure 接回 runner/import

允许路径：

- `pipeline/real_scene_operations.py`
- `pipeline/real_scene_runner.py`
- `pipeline/real_scene_import.py`（只允许适配 G5，不改 `23a2ece` 的对齐信任门）
- 对应测试

完成定义：

- `train-production` 只有消费 G5 verified closure 才能进入 import；
- receipt 绑定 G2 runtime SHA 与 G5 closure SHA；
- resume 时重新打开所有字节，不信任内存对象或旧 stage 状态；
- blocked receipt 不得含 `production`、`metric`、`aligned` 或可发布声明；
- fake operation 直接填低 RMS / accepted / production 的测试必须被拒绝；
- preview/Brush 路径保持 preview-only，不回归。

### G7 — 外部执行前的机器清单

若仍无云 GPU/凭据，也必须交付：

- 一条不含 secret 的 production preflight CLI；
- 一个 canonical blocked report，明确只缺哪些 external inputs；
- operator 输入白名单：host、host key、workspace、immutable image digest、
  dataset/config identities；
- 禁止输出私钥、token、完整环境变量和私有数据路径；
- fresh endpoint 到位后可从同一 CLI 一次运行 G3 → G4 → G5，不需要改代码。

完成后继续审计 `cloud/remote_training_worker.py` 和
`cloud/train_3dgs_nerfstudio.sh` 是否仍有 mutable image、未固定 CLI、shell
injection、非耐久发布或结果自报成功，并提交一份机器可复现的 RED 用例；不要只写
文字结论。

## 提交与回执格式

每个 GLM 提交回执必须包含：

- commit SHA 和限定路径；
- RED 失败证据、GREEN 测试命令与数字；
- `git diff --check` 和 ruff；
- schema/version 兼容性说明；
- 未解决项与下一任务包编号；
- exact-head CI URL（若已触发）。

push：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

## Codex 责任与最终五门

Codex 负责 review GLM 每个小提交、exact-head CI、Viewer/Studio、发布和真实浏览器
QA。正式版必须同时取得：

1. rights-cleared、密集重叠的真实采集；
2. accepted real-photo SfM；
3. 非 mock CUDA 3DGS；
4. 至少四个非共面实测控制点及米制对齐；
5. 真实重建 Viewer 与人工视觉验收。

任一项缺失都保持 `preview / arbitrary / unaligned`。synthetic Blender、image2
设计图、mock、stub、本机 Brush 小样和绿色单测不能替代上述五门。
