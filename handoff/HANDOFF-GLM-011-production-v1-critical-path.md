# HANDOFF-GLM-011 — Production V1 关键路径连续队列

日期：2026-07-27

Owner：GLM lane

Reviewer：Codex
已验证实现基线：`main@02474403d0f2ca769a3a8f38d77a4a420b0ad01e`

本文件是 GLM 当前唯一的首要执行入口。旧的 GLM-007/008 只用于历史追溯；
GLM-009 和 Batch35 synthetic 工作排在本队列 P0/P1 之后。

## 2026-07-27 Codex 同步与复审更新（当前，以本节为准）

### 已关闭，不要重做

- `origin/main@cb189a8` 已同步。P1-2 的固定远端 checker、runtime/image/worker
  identity、输入 TOCTOU、canonical report、durable no-replace publication、
  `real-scene preflight-remote` 与 operator 手册均已闭环；专项
  `134 passed, 4 skipped`，ruff 通过。
- P1-3A 的 timeout 不能通过修改测试断言来“关闭”。Codex 已把真实 transport
  timeout 修成 monotonic `unknown` observation，并保留其它本地安全错误为异常。
- `e16d6de` 的 P1-3B 单点 tamper 测试已进入 main；现有 verifier 对这些用例
  fail closed。GLM 不要继续堆重复的 bundle/status tamper fixture。
- push 固定使用一次性代理，不写持久配置：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### 当前 P1-3C/D 草稿复审：尚未通过，先修这两项

工作树中的 `pipeline/training_executor.py` 与
`tests/test_training_executor.py` 是 GLM 的未提交草稿，Codex 没有把它们混入
`cb189a8`。当前 `43/43` 绿色只证明 model-level fixture 自洽，不能关闭 P1-3C/D：

1. **P1-3C 缺真实进程恢复路径**：`RemoteShellExecutor._jobs` 仍只在内存中。
   新进程读取 journal 后无法恢复 `_JobContext`，`real_scene_operations.py` 也始终
   新建 executor、重新 `submit`。现有“restart”测试只是重新加载
   `RealSceneJournal`，没有构造第二个 executor、没有证明零次重复 submit，也没有
   poll/fetch 原 job。
2. **P1-3D 可自报 pass**：`build_remote_training_drill_report(**fields)` 接受调用者
   任意传入的 `outcome="pass"`，没有固定 case registry、case evidence SHA、
   实际 runner、exact-HEAD/clean-tree 校验和 durable publication。这样的 canonical
   JSON 只能证明“声明被哈希”，不能证明 drill 真运行过。

### GLM 连续执行清单（明确顺序，不要再报告无事可做）

#### P1-3C — 真实 crash/restart/reconnect

1. **C1：严格恢复输入**
   - 为 private remote job ref、attempt receipt、training bundle 增加 duplicate-key、
     canonical、大小、regular-file、before/after stat 与内容 SHA 校验；
   - 恢复输入必须重新绑定 job/attempt/request/dataset/config/trainer/container SHA，
     任一不一致 fail closed。
2. **C2：executor rehydrate**
   - 增加显式 `restore`/`attach` API，从已验证 job ref + bundle + receipt 重建
     `_JobContext`；
   - 禁止 restore 调用 upload、remote init/start 或 submit；
   - unknown/running 只允许继续 poll；只有远端 succeeded status 和本地 result
     closure 后才允许 fetch 成功。
3. **C3：runner reconnect**
   - `real_scene_operations.py` 在同一 stage attempt 已存在有效 private job evidence
     时走 reconnect，不重新 submit；
   - `RESUME=1` 重连原 attempt；显式 `RETRY=1` 使用新 stage attempt/new
     attempt identity，旧证据不可覆盖；
   - reconnect 输入损坏或身份不一致返回 blocked/unknown 的稳定机器原因。
4. **C4：真实新进程演练**
   - fake transport 第一个 executor submit 后销毁；
   - 第二个全新 executor 只从 durable bytes restore；
   - 断言 submit/start/upload 调用数为 0，poll/fetch 使用原 job identity；
   - 覆盖 running、unknown、remote failed、succeeded+verified result、截断 journal、
     job-ref tamper、bundle tamper 和 fetch 中断。
5. **C5：小提交与验收**
   - production code、专项测试、ruff 同一小提交；报告测试数与 exact commit；
   - 不把临时目录、私有 host/config、fake 媒体或 fixture report 提交到仓库。

#### P1-3D — 不可自报 pass 的 drill runner

6. **D1：固定 case registry**
   - case ID、suite、执行函数与预期机器结果由代码固定，report caller 不能传
     `pass`；
   - P1-3A/B/C 的必需 case 必须恰好一次，缺失、重复、unknown/skipped 均不能
     得到 accepted report。
7. **D2：逐 case 证据绑定**
   - 每项绑定 case definition SHA、输入 identity SHA、观测结果 canonical SHA
     和稳定 failure code；
   - free-text 只做人读摘要，不参与机器状态，不透传 traceback/stderr/私有路径。
8. **D3：真实 runner 与 Git 门**
   - 新增 standalone runner，自己执行固定 drills、读取实际 Python/pytest/ruff
     版本，并验证 exact HEAD；
   - dirty tree、case 未执行、进程超时或工具版本不可读时 fail closed；
   - synthetic/fake drill 报告必须明确 `evidence_scope=transport-fixture`，不得冒充
     云 GPU 或真实训练证明。
9. **D4：durable publication**
   - canonical content SHA/report ID；
   - sibling staging → file sync → no-replace publish；刷盘失败和 output 已存在均有
     对抗测试；报告只写 operator 私有或 verification output，不进 Release。

#### P1-4 / P1-5 — P1-3 完成后直接继续

10. **P1-4A**：4+ 唯一非共面控制点的 rank/condition/span 门，覆盖重复、共线、
    近共面、单位/frame/handedness 冲突。
11. **P1-4B**：Sim3 measured residual 与 policy decision 分离，绑定 registration、
    control-points、policy、transform-history SHA；失败禁止 metric/aligned/ENU。
12. **P1-4C**：把对齐门接入 import/accept runner，并做无控制点、阈值失败和合格
    fixture 三条端到端测试。
13. **P1-5A**：扩展 fixed readiness checker，实测 CUDA device、driver/runtime、
    Nerfstudio `1.1.5` 与训练 CLI schema；探针仍不得安装、启动容器或训练。
14. **P1-5B**：production result closure 覆盖非 mock log、export PLY、dataparser
    identity、held-out evaluation、container identity 与全部内容 SHA；stub/Brush
    永远不能通过。

每项绿后立即做路径限定小提交并继续下一项；Codex 会并行 review。若 review 指出
P0/P1 问题，先修该问题再继续叠加同一文件，不要用“测试绿”替代 caller/实渲/真实
训练证据。

## 2026-07-27 Codex 当前回执（GLM 先读）

### 当前判定

- P1-1A～P1-1D 的实现已交付；Node 22.14.0、Playwright 1.62.0 和 pinned
  Chromium 的 Ubuntu/Windows runtime job 已有绿色机器结果。exact-head
  [CI run 30238069052](https://github.com/taomic2035/nantai-3d/actions/runs/30238069052)
  的四个 test、两个 viewer-runtime、两个 repro-assets 和 repro-compare 已全部
  通过。P1-1 正式关闭，GLM 不要回头重做。
- `58dfc5e feat: credential-free remote-shell preflight (P1-2)` 的 Codex
  spec review **不通过，不得 push**。局部测试 `19 passed, 1 skipped` 只能证明
  草稿内部一致，不能证明合同满足。
- 当前未提交的 P1-3A 测试可以保留在工作树，但在 P1-2 复审通过前不得提交、
  不得继续叠加 P1-3B/C/D。当前草稿还在 `RemoteShellJobRef` 两处类型标注上触发
  `F821`；先保留草稿，P1-2 关闭后再补 import 并进入 P1-3A。现在先把 P1-2
  修成可复核的小提交。Codex 已实跑草稿的九项 focused tests：`8 passed,
  1 failed`；`test_poll_timeout_returns_unknown` 当前得到
  `RemoteShellExecutionError`，不是预期的 monotonic `unknown` observation。
  P1-2 关闭后，P1-3A 还必须为这个 timeout 行为补生产修复，不能只改断言。
- Codex 的 Viewer 近景相机修复已经专项测试和视觉复核通过；它因父提交
  `58dfc5e` 尚未获准而暂不 push。GLM 修 P1-2 时禁止修改 `web/`。
- Codex 继续关闭了 Viewer acceptance 的真实浏览器阻塞：显式
  `viewerPresentation=points` 现在跳过不存在的可选模型预览，但仍按顺序记录启动
  stages，且不会声称模型已验证。提交 `c32e13f`；Viewer `222/222`、capture
  contract `7/7` 通过。
- Windows 11 / Intel UHD 770 / Chromium 151 / Playwright 1.62 的三视角
  synthetic internal-canary 报告已实测通过：三视角均为 `full-3dgs`，
  `max interactive=16.0 ms`、`max p50=19.0 ms`、`max p95=29.2 ms`、
  `max worst=38.4 ms`、console/unhandled 均为 0。scene manifest SHA
  `dccec3b229afe86f1e4bfc460747f45312952c643d0b67f56fb5875e0b438e5a`，
  report SHA
  `2b89bb0e91d2f3e915666f9f352138e81a008354471d6076fff5a5bae91c7a43`，
  decision SHA
  `4926157acdec68f672058fab1b6ff7a883f8bd6d31ee385ef3ad1ab367bf87cc`。
  这些私有报告不进 Git/Release，只证明 synthetic Viewer runtime/performance，
  不证明真实照片重建、真实纹理、米制对齐或 Production V1 acceptance。
- Codex 已在 `9e61374` 把 human visual review receipt 改为同目录 staging →
  file sync → no-replace durable publication；刷盘失败不会留下可误认的 final。
  `00c613c` 又把 content-addressed aggregate 与 latest pointer 接入同一耐久
  原语，失败不留截断 final、也不替换旧 pointer。Task 11 回归 `46 passed,
  1 skipped`，ruff 通过。Task 12/13 的 Studio real-scene evidence 与正式路径
  文档门也已复核：Python `60 passed,
  6 skipped`、Studio Node `38/38`、ruff 通过。GLM 不需要重做这些 Codex
  路径，继续优先关闭 P1-2/P1-3。
- Codex 在 `04aa0ca` 修复了 `scripts/real_scene.py` 与 `scripts/doctor.py`
  的隔离直接执行入口；`python -I scripts/real_scene.py --help` 已通过，
  doctor 不再因 `No module named 'pipeline'` 误报 registry 损坏。当前 Windows
  实测为 COLMAP 4.1.0、Brush 0.3.0、素材 SHA `11/11`、磁盘充足、无 NVIDIA
  CUDA。`python scripts/real_scene.py preflight-remote --help` 仍明确失败为
  `invalid choice: preflight-remote`，因此 P1-2 FIX-D 尚未实现，GLM 不得把
  现有 `train-production` 参数误报成 credential-free readiness CLI。
- Codex 在 `964bd6c` 继续修复了 Production V1 计划明确要求直接运行的
  `fetch_real_dataset.py`、`validate_render_evaluation.py` 和
  `record_real_scene_review.py`；三者的隔离 `python -I ... --help` 契约已覆盖，
  下载/评估/审核业务回归 `71 passed, 2 skipped`，ruff 通过。GLM 新增
  `preflight-remote` 时必须同步加入同类隔离直接执行测试，不能只在 pytest
  已注入仓库根的环境里验证。

### `58dfc5e` 必须关闭的问题

1. **假 `ready`（P0）**：`probe_remote=False` 时仅四个本地 boolean 为真就返回
   `ready`，远端字段仍是 `None`；删除/反转
   `test_ready_without_remote_probe`，模型层也必须拒绝这种 report。
2. **没有测量声明的 identity（P0）**：`docker --version` 加 `test -f` 不能证明
   immutable image digest 或 worker executable/version/SHA。不得把 config 中的
   digest 原样抄入 report 当作实测。
3. **report 可重放（P0）**：缺去密 config identity、known_hosts 内容 SHA、
   worker identity、checker version、输入 snapshot、report ID/content SHA。
4. **没有 TOCTOU 闭环（P0）**：config/key/known_hosts/transport 在远端 probe 前后
   都要重开、重算并比较；替换、内容漂移或 link-like entry 必须失败。
5. **状态不稳定（P1）**：任意 `failure_reason` 和 `str(exc)` 不能成为机器合同；
   使用有限 `failure_code`，并保证 `failed` 不被同时出现的 missing input 降成
   `blocked-external-input`。
6. **缺 CLI/publication（P1）**：还没有 `real-scene preflight-remote`、严格 JSON
   loader、独占创建 canonical report、写失败清理和输出碰撞测试。
7. **redaction 不完整（P1）**：`UserKnownHostsFile=<absolute path>` 会进入
   `command_audit`；补 config/known_hosts/key/token/path canary，覆盖 argv、
   stdout/stderr、异常、traceback 和 report。
8. **远端 command 合同不闭合（P1）**：OpenSSH CLI 的远端命令天然是字符串；
   因此只允许一个不含任何 config 派生值的硬编码只读 checker 命令，禁止用
   `shlex.join()` 拼接动态 argv。checker 返回 canonical identity report，由本地
   与期望 digest/SHA 比较。

### GLM 立即执行的四个提交

不要等 Codex 再提醒；严格按以下顺序，每项 RED → GREEN → ruff →
`git diff --check`，只改列出的路径。

#### GLM-P1-2-FIX-A — report 状态机与防重放 identity

允许路径：

- `pipeline/remote_shell_executor.py`
- `tests/test_remote_shell_executor.py`

完成定义：

- `ready` 必须要求 remote runtime/image/worker 三项实测为真，任一 `None` 都拒绝；
- `failure_code` 是有限枚举；文本只来自 code 的固定安全映射；
- report 绑定去密 config identity、known_hosts SHA、期望/实测 container digest、
  worker SHA/version、checker version 和每个本地 transport identity；
- `report_id` / `content_sha256` 能从排除自身 ID 的 canonical payload 复算；
- tamper/replay/unknown field/fake-ready 的模型级 RED 全部转绿。

提交主题：`fix: close remote preflight report identity`

#### GLM-P1-2-FIX-B — 本地 snapshot 与 TOCTOU

允许路径：

- `pipeline/remote_shell_executor.py`
- `tests/test_remote_shell_executor.py`

完成定义：

- 对 config、key、known_hosts、ssh、scp 建立不含秘密的 before snapshot；
- JSON loader 拒绝 duplicate key、未知字段、link-like entry 和非普通文件；
- 远端 probe 后重新打开并重算全部 snapshot，任何漂移返回稳定 `failed` code；
- 缺配置/凭据/ssh/scp 分别是稳定 `blocked-external-input` code；
- fingerprint/shape/ACL/tamper 是 `failed`，不能被其它缺失项覆盖。

提交主题：`fix: close remote preflight input drift`

#### GLM-P1-2-FIX-C — 单一固定远端 checker

允许路径：

- `pipeline/remote_shell_executor.py`
- `tests/test_remote_shell_executor.py`
- 若确有必要，可新增一个窄职责的 `cloud/` checker 与对应测试

完成定义：

- 只执行一个硬编码、只读、无 config 派生参数的 checker 命令；
- 不 upload、`mkdir`、create/run container 或启动训练；
- canonical 响应必须实测 runtime、immutable image digest、worker executable、
  worker SHA/version；缺字段、重复字段、非 canonical bytes、identity mismatch
  全部 fail closed；
- timeout、transport、host-key drift、runtime missing、image mismatch、
  worker mismatch 使用不同稳定 code；
- `command_audit` 与所有异常输出不含任何本地敏感路径或远端连接秘密。

提交主题：`feat: verify immutable remote runtime identity`

#### GLM-P1-2-FIX-D — CLI、原子发布与对抗门

允许路径：

- `pipeline/real_scene_operations.py`
- `pipeline/real_scene_runner.py`
- `scripts/real_scene.py`
- `make.py`
- `Makefile`
- 对应 `tests/test_*.py`

完成定义：

- `real-scene preflight-remote` 在没有凭据时诚实输出
  `blocked-external-input`，不用 traceback；
- report 只写 operator 私有路径，以 exclusive-create + durable publication 发布；
- 已存在、中途写失败、replace/sync 失败不覆盖旧 report、不留可误认 final；
- CLI/help/Makefile/Windows/Linux、canonical bytes/content SHA、全域 canary
  redaction 测试齐全；
- 不创建 training job、bundle、远端目录或容器。

提交主题：`feat: publish remote readiness preflight`

### P1-2 之后的连续高价值队列

P1-2 四项全部复审通过后，GLM 不要报告“无事可做”，连续领取：

1. P1-3A：submit-before-ack、ack-after-disconnect、poll timeout、remote failed、
   fetch interrupted 的 monotonic 状态演练；
2. P1-3B：bundle/result/evaluation/container/journal 每个 SHA 的单点 tamper；
3. P1-3C：journal 截断、replace 失败、published-unconfirmed、显式 retry/new
   attempt；
4. P1-3D：canonical drill report，绑定 exact commit、测试版本和输入 SHA；
5. P1-4A：至少四个非共面控制点、单位/坐标系/对应关系 fail-closed 输入门；
6. P1-4B：Sim3 残差、尺度、FrameTransform、transform history 的内容绑定；
7. P1-5A：CUDA/Nerfstudio/immutable image/worker readiness；无真实端点时输出
   blocked，而不是停止做本地合同；
8. P1-6A/B：fresh Windows canary 与信任审计；
9. P1-7A：交付 verified import/chunks/scene identity/policy/checksum 给 Codex 做
   真实浏览器 QA。

这九项中只有真实远端 probe、真实 GPU 训练和人工验收需要外部输入；schema、
状态机、tamper/recovery、CLI、canonical report、控制点验证和 blocked 路径都可
独立推进。

## 当前事实

- `v1.0.0-preview.2` 标签及其 CI 是绿色的，已发布的 Preview2 不受本次回归影响。
- 旧的
  [CI run 30208773810](https://github.com/taomic2035/nantai-3d/actions/runs/30208773810)
  是冻结的失败基线，不再代表当前 `main`。
- P0-2 已由 `2351805` / `76c5bad` 关闭：COLMAP 版本证据绑定实际执行的
  resolved binary。
- P0-3 已由 `84cce65` / `34ea9b6` 关闭：ZIP、journal 与 remote result 使用统一
  durable publication，并显式区分 `published-unconfirmed`。
- P0-4 已由 `1d07d54` / `fe75ea6` 关闭：Windows 私钥 ACL 与单 handle 生命周期、
  host key、null device 和 secret-redaction 保持 fail closed。
- P0-5 已由 `2d34021` 关闭：Windows canonical bytes、PATH、Git Bash 结构检查和
  ZIP 原始反斜杠成员拒绝专项门为 `81 passed, 7 skipped`。
- P0-6 已由 `0247440` 关闭：本地 `make.py test` 为 Python
  `3353 passed, 90 skipped`、Viewer Node `216/216`、Studio Node `103/103`，
  全范围 ruff 与 `git diff --check` 通过。
- exact-HEAD
  [CI run 30234423540](https://github.com/taomic2035/nantai-3d/actions/runs/30234423540)
  的 Ubuntu/Windows × Python 3.11/3.13 四个 test job、两个 reproducibility job
  与 compare job 全部通过。
- **GLM 当前立即领取 GLM-P1-2-FIX-A**，随后连续执行 FIX-B、FIX-C、FIX-D；
  四项经 Codex 复审通过后再进入 P1-3A，不再等待另行提醒。
- 当前 canary 只有真实照片 COLMAP 与本机 Brush `preview-only` 证据。尚无
  非 mock CUDA 3DGS、实测米制对齐、真实 Viewer/human acceptance，因此不能
  报告“真实 3D 场景完成”或“production accepted”。

## GLM 当前连续领取清单（只执行未完成项）

不要等待 Codex 逐项提醒。按下表从上到下连续领取；每完成一行就提交、push、回报
SHA 与机器结果，然后立刻进入下一行。`blocked-external-input` 只阻塞真实远端调用，
不阻塞 schema、preflight、恢复测试、CLI 和 CI 集成。

| ID | 任务 | 完成定义 |
|---|---|---|
| P1-2 FIX-A | report 状态机与防重放 identity | fake-ready、重放、tamper、未知字段、错误状态优先级全部模型级拒绝 |
| P1-2 FIX-B | 本地 snapshot 与 TOCTOU | config/key/known_hosts/ssh/scp 前后重开重算，漂移与 link-like entry fail closed |
| P1-2 FIX-C | 固定远端只读 checker | 实测 runtime/image/worker identity；canonical 响应与稳定错误码；零远端写入 |
| P1-2 FIX-D | CLI、耐久发布与 redaction | `preflight-remote`、exclusive durable report、碰撞/写失败/秘密 canary 对抗门 |
| P1-3A | submit/poll/fetch 状态演练 | success、remote failed、timeout、断网、unknown 均有 monotonic 状态断言 |
| P1-3B | checksum/内容漂移演练 | result、journal、dataset/config/container SHA 任一漂移均 fail closed |
| P1-3C | crash/resume/retry 演练 | published-unconfirmed、重启恢复、显式 retry 不得把失败提升为 succeeded |
| P1-3D | canonical drill report | 发布机器可读演练报告，绑定测试版本、输入 SHA 与 exact commit |
| P1-4A | 控制点输入预检 | 至少 4 个非共面点、单位/坐标系/对应关系可证；不满足保持 unaligned |
| P1-4B | 米制对齐证据门 | Sim3 残差、尺度、FrameTransform 与 transform history 内容绑定 |
| P1-5A | 云 GPU runtime readiness | 校验 CUDA、Nerfstudio 版本与 digest image；无端点时诚实 blocked |
| P1-5B | production result closure | 非 mock result 必须绑定 bundle/config/container/result/evaluation SHA |
| P1-6A | fresh Windows canary | fetch → COLMAP → bundle → Brush → import/chunk → acceptance aggregation |
| P1-6B | canary 信任审计 | 预期仍为 preview-only / arbitrary / unaligned，不得人工提升 |
| P1-7A | 给 Codex 的 Viewer 输入包 | 只交 verified import/chunks、scene identity、政策与 checksum |

P1-7A 后由 Codex 执行真实浏览器 cold-load、交互帧、关键视角与人工视觉审查；GLM
继续处理机器报告或 review 修复，不得因为等待 Codex 视觉结论而宣称队列为空。

## 执行规则

1. 严格按 P1-2 FIX-A → FIX-D → P1-3 → P1-7 顺序连续推进；P0 与 P1-1
   已关闭，不回头重做。一个任务完成后直接进入下一个，不因“测试已绿”而停下。
2. 先写能复现缺陷的 RED 测试，再做最小修复。不得删除/跳过测试、降低阈值、
   把 unknown 变成 accepted，或用 fixture 字符串冒充机器证据。
3. 只改每项列出的路径。需要跨出边界时先写明原因交 Codex review；不要修改
   `web/`、Release、公开 acceptance 状态或已有证据文件。
4. 每项独立小提交，路径限定 `git add` / `git commit -- <paths>`。GLM 使用自己
   的署名；push 用一次性代理，不修改 Git 配置：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

5. 每次交付回报：commit SHA、修改路径、RED/GREEN 命令与结果、剩余风险、
   远端 exact-HEAD CI URL。没有真实外部输入时继续做下一项本地任务，不要等待。

## 已关闭基线的详细参考（非当前任务）

以下 P0 与 P1-1 只在回归追溯时读取，GLM 不得把它们重新领取为当前工作。

## P0 — 恢复可信的跨平台主干（已关闭）

### P0-1：冻结失败基线，不提交

目标：把远端失败归类为产品缺陷、测试缺陷或平台合同缺口，避免边修边猜。

读取范围：

- `.github/workflows/ci.yml`
- `pipeline/real_scene_capture.py`
- `pipeline/real_scene_training.py`
- `pipeline/training_executor.py`
- `pipeline/remote_shell_executor.py`
- 对应 `tests/test_*.py`

必须复现：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_real_scene_capture.py::test_sfm_accepts_only_matching_colmap_model_capture_and_poses -q
.\.venv\Scripts\python.exe -m pytest `
  tests/test_real_scene_training.py `
  tests/test_training_executor.py `
  tests/test_remote_shell_executor.py -q
```

输出一张简短映射表：失败测试 → 根因 → P0-2/3/4/5。不要为本项单独提交文档。

### P0-2：COLMAP 二进制与版本证据同源

目标：版本必须来自实际执行 registration 的同一个 resolved binary；测试替身也
必须显式提供版本证据，不能重新扫描 PATH，也不能从 `engine="colmap"` 推导。

允许路径：

- `pipeline/registration.py`
- `pipeline/real_scene_capture.py`
- 必要时 `pipeline/real_scene_operations.py`
- `tests/test_real_scene_capture.py`
- `tests/test_registration.py`

RED 用例至少覆盖：

- registration 成功但版本证据缺失时 fail closed；
- 实际 binary 与版本探针绑定；
- fake registration 只有在显式注入 measured version 时通过；
- 非 COLMAP engine 不伪造 COLMAP version。

验收：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_registration.py tests/test_real_scene_capture.py -q
.\.venv\Scripts\python.exe -m ruff check `
  pipeline/registration.py pipeline/real_scene_capture.py `
  pipeline/real_scene_operations.py tests/test_registration.py `
  tests/test_real_scene_capture.py
```

### P0-3：统一 durable write/replace 跨平台语义

目标：消除 Windows 对只读 descriptor `fsync` 的 `Bad file descriptor`，同时保留
“内容落盘后才发布/替换”的 fail-closed 语义。

允许路径：

- 可新增一个窄职责的 `pipeline/` durable I/O helper；
- `pipeline/real_scene_training.py`
- `pipeline/training_executor.py`
- `pipeline/remote_shell_executor.py`
- 对应测试。

实现要求：

- 文件必须在可写 handle 上 `flush` 后同步，再 close/replace；
- 目录同步若平台不支持，必须由 helper 明确表达平台语义，不能散落
  `except OSError: pass`；
- ZIP、journal、remote result bundle 都要有成功、同步失败、replace 失败和残留
  `.partial`/journal 恢复测试；
- 不改 canonical bytes、SHA 或 transaction state machine。

验收：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_real_scene_training.py `
  tests/test_training_executor.py `
  tests/test_remote_shell_executor.py -q
```

### P0-4：Windows SSH transport fail-closed 适配

目标：Windows 上不再用 POSIX mode bits 误判所有私钥，同时保持 host key、
fingerprint、identity 和 secret-redaction 的强约束。

允许路径：

- `pipeline/remote_shell_executor.py`
- `tests/test_remote_shell_executor.py`
- 如确有必要，可新增一个只负责平台权限验证的 `pipeline/` helper。

实现要求：

- POSIX 继续拒绝 group/other 可读私钥；
- Windows 使用可测试的 ACL/可读性策略；若无法证明安全，应返回明确的
  preflight failure，不得默认放行；
- null device 使用平台适配，不硬编码 `/dev/null`；
- 保留 `StrictHostKeyChecking=yes`、指定 `UserKnownHostsFile`、
  `IdentitiesOnly=yes`、已锁定 fingerprint；
- 日志、argv、异常仍不得泄露 key、token 或完整敏感配置。

验收至少包含 Windows/POSIX 参数与权限策略单测，以及：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_remote_shell_executor.py -q
```

### P0-5：修复跨平台测试与 canonical fixture

目标：修测试基础设施，不削弱生产校验。

允许路径：

- `tests/test_real_scene_training.py`
- `tests/test_training_executor.py`
- `tests/test_remote_shell_executor.py`
- `tests/test_local_brush_executor.py`
- `tests/test_cloud_prepared_training_script.py`
- 由 P0-1 映射出的其它直接相关测试文件。

要求：

- canonical JSON fixture 用明确 UTF-8/LF bytes，不能依赖 Windows
  `Path.write_text()` 默认换行；
- PATH 解析使用 `os.pathsep`；
- Git Bash fixture 必须正确处理 Windows drive path，或把真正 Linux-only 的行为
  隔离成有明确理由的 platform case；
- 不更改生产 manifest verifier 来接受非 canonical 输入。

### P0-6：exact HEAD 全门恢复

本地：

```powershell
.\.venv\Scripts\python.exe make.py test
.\.venv\Scripts\python.exe -m ruff check pipeline tests cloud scripts make.py
git diff --check
```

随后 push，并等待 exact commit 的 Ubuntu/Windows × Python 3.11/3.13 test 与
reproducibility 全绿。只有远端 exact-HEAD 全绿，P0 才完成。

## P1 — 把真实场景 caller 变成可执行产品门

### P1-1：把 Viewer acceptance 测试纳入标准门（已关闭）

Owner：GLM 负责 build/CI 集成；Codex 负责浏览器语义与 UI review。

允许路径：

- `make.py`
- `Makefile`
- `package.json` / `package-lock.json`
- `.github/workflows/ci.yml`
- `scripts/capture_viewer_acceptance.test.mjs`

要求：

- 标准 setup/CI 安装锁定的 Node 依赖；
- `make.py test` 必须运行 `scripts/capture_viewer_acceptance.test.mjs`；
- lint 范围覆盖新增的 `cloud/`、`scripts/` 和 `make.py` Python 代码；
- 浏览器 binary 缺失必须由显式 preflight 报告，不能晚到 capture 阶段才模糊失败；
- 本项不改 `web/` UI 或 acceptance 阈值。

拆分提交：

1. P1-1A 只改 `make.py` / `Makefile` 与对应 runner 测试，把
   `scripts/capture_viewer_acceptance.test.mjs` 加入 `test`；
2. P1-1B 在 `scripts/` 新增窄职责 runtime preflight，机器报告绑定 Node、
   Playwright、Chromium executable/launch 结果，缺能力不得返回 ready；
3. P1-1C 更新 workflow：`npm ci` 后安装 pinned Chromium，并在 Ubuntu/Windows
   都运行 preflight；不得使用 floating global package；
4. P1-1D 把 Python lint 范围扩到 `cloud/`、`scripts/`、`make.py`，仅修本项暴露的
   lint，不做无关格式化。

验收：

```powershell
.\.venv\Scripts\python.exe make.py test
node --test scripts/capture_viewer_acceptance.test.mjs
npm ci
npx playwright install chromium
```

### P1-2：真实 remote-shell credential-free preflight

目标：在不提交训练 job、不读取/打印秘密的前提下，验证 remote config shape、
本地 ssh/scp、known_hosts/fingerprint、container digest 和必要 binary。

优先扩展现有 `real-scene` CLI；输出 canonical machine report，状态只能是
`ready`、`blocked-external-input` 或 `failed`。没有真实凭据时交付
`blocked-external-input` 是正确结果，不得生成假 ready receipt。

允许路径：

- `pipeline/remote_shell_executor.py`
- `pipeline/real_scene_operations.py`
- `pipeline/real_scene_runner.py`
- `scripts/real_scene.py`
- `make.py`
- `tests/test_remote_shell_executor.py`
- `tests/test_real_scene_operations.py`
- `tests/test_real_scene_runner.py`
- `tests/test_real_scene_cli.py`

必须覆盖：

- 无配置、无凭据、ssh/scp 缺失分别得到稳定错误码和
  `blocked-external-input`，不是异常 traceback；
- config JSON、私钥、known_hosts 在检查期间被替换或改变时失败；
- host fingerprint、container identity 必须是 immutable digest；
- 可选的远端探针只能执行固定 argv 的只读命令，禁止 shell 拼接、上传 bundle、
  mkdir 或启动容器；
- report 不包含用户名之外的连接秘密，不包含私钥路径、config 原文或 stderr
  未过滤内容；canonical bytes 与内容 SHA 可复算；
- 同一 report 不能被其它 config/container/host 复用。

建议 CLI：

```powershell
.\.venv\Scripts\python.exe make.py real-scene preflight-remote `
  SOURCE=<source.json> REMOTE_CONFIG=<private-config.json>
```

若新增 target，必须同步 `Makefile`、`make.py`、帮助文本与 CLI 测试。

#### P1-2 实施顺序与 Codex review 门

GLM 不要只交付一个 report model 后停下；按以下四个独立小提交连续推进：

1. **P1-2A — schema 与安全状态机**
   - `ready` 必须要求本地 transport、known_hosts、私钥保护、远端 container digest
     和 worker binary 全部有实测 `true`；远端结果为 `None` 时不能 ready；
   - 使用稳定、有限集合的 `failure_code`，不得把任意异常、stderr 或 config 原文
     放进 `failure_reason`；
   - report 必须绑定去密后的 config identity、known_hosts 内容 SHA、container digest、
     worker identity 和检查器版本；只列这些安全 identity，不列私钥/config 路径；
   - canonical report ID/content SHA 从除自身 ID 外的完整 payload 复算，跨 config、
     host、container 或 worker 不能复用。
2. **P1-2B — 本地 credential-free probe**
   - 配置缺失、凭据缺失、`ssh` 缺失、`scp` 缺失分别得到稳定
     `blocked-external-input` code；
   - JSON duplicate key、shape 错误、known_hosts/fingerprint 不匹配、私钥保护不合格
     得到 `failed`；
   - config、known_hosts、key 在检查前后替换、变更或变成 link-like entry 必须失败；
     不在 argv、stdout、异常或 report 中暴露路径/内容。
3. **P1-2C — 固定只读远端 probe**
   - 只有 P1-2B 全绿后才允许启动 transport；
   - 命令和参数来自固定模板与严格验证的 immutable identifiers，禁止 shell 拼接、
     bundle upload、`mkdir`、container create/run 或训练；
   - 验证实际 runtime、immutable image digest 和 worker executable；超时、host-key
     漂移、连接失败与 capability mismatch 使用不同稳定 code；
   - probe 后再次验证所有本地输入快照，TOCTOU 漂移不得发布 ready。
4. **P1-2D — CLI、canonical publication 与对抗回归**
   - `real-scene preflight-remote` 只写 operator 私有、独占创建的 canonical report，
     不创建训练目录或 job；
   - 覆盖 success、三类 blocked、shape/fingerprint/ACL/tamper/timeout/remote mismatch、
     output 已存在和中途写失败；
   - 对 argv、stdout/stderr、异常文本、traceback 和 report 做 secret/path canary 搜索；
   - Linux/Windows 测试门明确区分真实平台能力与 fixture，不用 fixture 字符串冒充
     remote-ready 机器证据。

当前 `RemoteShellPreflightReport` 草稿在进入 P1-2B 前必须先关闭三项：

- `ready` 不能只检查四个本地 boolean；
- 任意自由文本 `failure_reason` 不能成为机器状态或错误透传通道；
- 缺少去密 config/input snapshot binding 时，report 仍可被其它配置重放。

### P1-3：恢复/失败演练

对 submit、poll、fetch、checksum mismatch、远端 job 失败、网络中断与本地 journal
恢复做 fresh 演练。产物必须绑定 dataset/training config/container/result SHA；
失败 job 不得被 resume 为 succeeded。

按四个独立提交交付：

1. P1-3A：fake transport 覆盖 submit-before-ack、ack-after-disconnect、
   poll timeout、remote failed、fetch interrupted；
2. P1-3B：对 bundle、result、evaluation、container identity、journal 的每个 SHA
   做单点 tamper；
3. P1-3C：重启后只从 durable journal 恢复，`unknown` 仍为 unknown，显式 retry
   产生新 attempt identity；
4. P1-3D：生成 `nantai.remote-training-drill.v1` canonical report，绑定 exact
   commit、场景/数据/config/container SHA、用例结果和工具版本。

优先路径：

- `pipeline/training_executor.py`
- `pipeline/remote_shell_executor.py`
- `pipeline/real_scene_operations.py`
- 对应三组 tests；如新增脚本只放 `scripts/`，报告样例只放 test 临时目录。

### P1-4：实测控制点与米制对齐门

目标：在真实训练结果到来前完成输入和证据合同；没有合格控制点时明确保持
`arbitrary / unaligned`。

必须覆盖：

- 至少 4 个唯一、非共面的 3D 对应点，拒绝重复、共线、近共面和 rank-deficient；
- 声明源/目标 frame、轴、handedness、单位和点身份；
- Sim3 必须报告 scale、rotation、translation、每点 residual、RMSE/max residual；
- policy 与决定分离；阈值变化改变 policy SHA，不能改写 measured residual；
- alignment 绑定 registration、control-points、policy 与 transform history SHA；
- 未通过时禁止写 `metric`、`aligned` 或 ENU frame。

优先路径：`pipeline/alignment.py`、`pipeline/real_scene_runner.py`、
`pipeline/real_scene_acceptance.py` 及对应 tests。

### P1-5：云 GPU runtime readiness 与 production result closure

目标：把“能连接服务器”和“真实训练已接受”分开。无 CUDA/端点/凭据时可交付
`blocked-external-input`，但 schema、CLI、探针和 fixture 必须完成。

必须覆盖：

- 远端 CUDA device、driver/runtime、Nerfstudio `1.1.5`、训练 CLI 实际 schema；
- container 必须用 digest，探针结果绑定实际执行 binary/image；
- 禁止在 readiness probe 安装依赖、改 PATH、运行 SfM 或启动训练；
- production result 必须有非 mock training log、export PLY、dataparser transform、
  held-out evaluation、container identity 与 content SHA；
- stub/fake/local Brush 结果永远不能满足 production closure。

优先路径：`cloud/`、`pipeline/remote_shell_executor.py`、
`pipeline/training_executor.py`、`pipeline/training_provenance.py` 及对应 tests。

### P1-6：fresh Windows canary

在 P0/P1-1/2/3/4/5 完成后，使用现有 rights-cleared canary 配置重跑：

```text
fetch/verify → fresh COLMAP → split/bundle → local Brush preview
→ import/chunk → acceptance aggregation
```

只提交机器报告、内容 SHA 和精简说明。预期仍是
`internal-only / preview-only / arbitrary / unaligned`；不得把本机 Brush 提升为
production 3DGS。

每一段必须使用 fresh 输出目录；报告绑定 `main` exact commit。失败阶段保留机器状态
和最小日志摘要，不提交媒体、缓存、私有配置、绝对用户目录或完整中间输出。

### P1-7：交给 Codex 的真实 Viewer 输入包

只有真实非 mock 云结果到达后执行。GLM 交付：

- verified import receipt 与 scene identity；
- chunks manifest、LOD、坐标/transform history；
- render/viewer policy；
- 全部内容 SHA 与生成工具 exact commit；
- 明确仍缺失的 meter alignment 或人工验收门。

Codex 消费这些输入做真实浏览器 QA；GLM 在等待期间继续 P2 的 review 修复，不能把
“等待人工视觉结论”报告为整个 lane 无任务。

## P2 — 不阻塞真实主线的后续工作

1. 修复 `REVIEW-CODEX-037-glm-prop-geometry-v2.md` 的两项 P1 和一项 P2，
   复审通过后才开始 Batch35 Blender emission。
2. 完成 Batch35 八类道具的 Blender emission、exact build 和实渲验收。
3. 继续 `HANDOFF-GLM-009-roaming-graph-producer.md` 的真实 producer 接入；
   不得把不同 scene identity 的 synthetic graph 与 real splat 强拼。
4. 对 synthetic 材质做 causal A/B 实渲；设计图相似不能代替材质因果证据。

## Codex 并行责任

- review GLM 每个 P0/P1 提交和 exact-HEAD CI；
- 补齐 Playwright Chromium 的用户安装/体检入口；
- 用真实浏览器执行 cold load、交互帧、关键视角和 human visual review，并区分
  cold-start latency 与 scene-loaded pose interaction；
- 只有真实 GPU 结果、米制对齐和真实 Viewer/human 证据齐全后，才签署 production
  acceptance 或准备真实场景 Release。

## 外部输入（不是 GLM 停工理由）

- rights-cleared、密集重叠的生产照片/视频；
- 可用的 NVIDIA CUDA 训练端、immutable container digest 和 operator credentials；
- 至少 4 个非共面实测控制点及坐标系/单位；
- 最终人工视觉验收人。

这些输入缺失时，GLM 应继续完成 P0/P1 的代码、preflight、恢复演练和机器合同；
只有在某一步会真实提交云任务、接触凭据或签署人工结论时才停下请求外部输入。
