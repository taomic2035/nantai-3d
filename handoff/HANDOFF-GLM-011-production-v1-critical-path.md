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
`RemoteReadinessEvidence.v2` 信任模型。G3 必须把 fixed raw probe observations
适配为上述 measurement，再由该模块的 policy/decision 唯一派生 acceptance。

### G3 — Fixed read-only production probes

优先路径：

- `cloud/remote_readiness_checker.py`
- `tests/test_remote_readiness_checker.py`

完成定义：

- probe registry 固定且内容寻址，caller 不能传任意命令；
- 只连接已运行、全 ID 固定且镜像 digest 匹配的 container；不得启动新容器；
- 实测 `nvidia-smi` 的 GPU UUID/name/memory/driver；
- 实测 CUDA runtime，而不是从 driver 字符串推断；
- 在绑定的 production environment 内实测 Python、Nerfstudio `1.1.5` 和
  `ns-train splatfacto --help` 的结构化 schema；
- readiness 不安装包、不改 PATH、不启动新容器、不跑 SfM/训练；
- 每个 executable 和 environment identity 做前后快照；变化即 TOCTOU blocked；
- stdout/stderr 有大小上限和 secret redaction，非 UTF-8/截断/timeout 稳定 blocked。
- 每个 raw probe 绑定 `execution_environment_sha256`，并构造
  `ProductionRuntimeMeasurement.create(...)`；checker 不输出 caller 可填的
  `ready=true`。

无 GPU 时测试 fixture 仍要证明 parser 与状态机，但 `ready` 只能来自真实 probe
observations。

当前 1747 行草稿不能直接提交：它仍运行宿主任意 `nerfstudio_python`、没有真实
CUDA runtime 和 `ns-train splatfacto` CLI schema，并在 remote shell 内重复定义
G2 schema。先把 `training_executor.py` 中被 `4150cfb` 替代的 drill 草稿从 diff
移除；再只提交 checker + G2 adapter + 对应测试。`docs/manual` 等代码闭环后另交。

### G4 — Remote caller 端到端接入

允许路径：

- `pipeline/remote_shell_executor.py`
- `pipeline/real_scene_operations.py`
- 对应测试

完成定义：

- submit 固定 checker，poll/fetch 后验证 G2 canonical report；
- host key、durable job ref、workspace、commit 和 runtime evidence identity 串成一条
  closure chain；
- reconnect 后必须恢复同一 probe attempt，不得新建“看起来成功”的 attempt；
- absence/timeout/transport unknown 映射为 `blocked-external-input` 或 `unknown`，
  永远不映射 ready；
- no-replace durable publication，覆盖 collision、partial、sync failure、replay、
  wrong-attempt 和 result swap。

联合回归必须包含 readiness + remote shell + operations + runner，不只跑 checker。

### G5 — Production result closure

优先新增：

- `pipeline/production_training_result.py`
- `tests/test_production_training_result.py`

消费已有：

- training request/bundle/result/attempt；
- G2 runtime evidence；
- dataparser identity transform；
- `pipeline/render_evaluation.py` 的 held-out report；
- trained INRIA PLY。

完成定义：

- 验证同一 dataset/config/trainer/container/commit/attempt 从请求贯穿结果；
- 非 mock training log、export PLY、dataparser transform、held-out render/evaluation
  与 runtime evidence 全部以 canonical SHA 绑定；
- 至少 100,000 个 finite Gaussians、完整 INRIA schema、identity dataparser；
- stub/fake/local Brush/transport-fixture 永远不能满足 production closure；
- policy 和 measurement 分层；改验收阈值不能重写训练观测 SHA；
- 任一文件缺失、extra、link-like、替换、截断、重放、wrong attempt 或 evaluation
  未通过都返回 blocked，不产生 verified result。

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
