# HANDOFF-GLM-011 — Production V1 当前连续队列

日期：2026-07-27

Owner：GLM lane
Reviewer：Codex

这是 GLM 当前唯一的 P0/P1 执行入口。完成一项后直接进入下一项，不等待再次分配。
已完成过程压缩在 [HISTORY.md](HISTORY.md)，完整原文从 Git 历史读取。

## GLM 当前开工单（2026-07-27 Codex review）

不要再回复“无待推进工作”。当前工作树中的 NOW-2 / NOW-3 / NOW-4 草稿尚未通过
Codex review；下面任务均不依赖真实 endpoint、secret、付费 GPU 或 Codex 新接口。
按 A → B → C 连续执行，完成一个就路径限定提交和 push，然后自动开始下一个。

### 当前唯一 active ticket：A1（现在就做）

当前工作树不是空闲状态。下面四条路径已有 GLM 草稿：

```text
cloud/remote_readiness_checker.py
tests/test_remote_readiness_checker.py
cloud/train_3dgs_nerfstudio.sh
tests/test_cloud_prepared_training_script.py
```

先冻结后两条 NOW-8 路径，不删除、不提交，也不要继续扩写。A1 只修改并提交前两条
readiness 路径。当前 `17 passed, 1 skipped` 不是完成信号，因为测试把错误行为写成了
GREEN：

- `test_checker_truncates_oversize_stdout` 接受“截断后继续解析”，必须改成超限立即
  blocked；
- `runtime_resolved` 只被读取和哈希，实际 `--version`、image inspect、scheduler
  probe 仍执行配置名 `docker`，PATH wrapper 可在解析后劫持；
- config 允许 `docker|podman`，scheduler probe 却对两者都使用 Docker 私有
  `{{json .Runtimes}}` 格式；
- 当前没有 stderr oversize、非 UTF-8、解析后 PATH 劫持和 Podman adapter RED。

A1 必须先增加并观察这些精确 RED：

```text
test_checker_executes_only_resolved_runtime_path
test_checker_rejects_path_wrapper_swap_after_resolution
test_checker_rejects_oversize_stdout_without_parsing_prefix
test_checker_rejects_oversize_stderr_without_leaking_content
test_checker_rejects_secret_bearing_oversize_output_without_leaking_secret
test_checker_rejects_non_utf8_observation
test_checker_uses_docker_scheduler_adapter_only_for_docker
test_checker_uses_podman_scheduler_adapter_only_for_podman
test_checker_rejects_unknown_scheduler_adapter_observation
```

实现边界：

1. 解析后所有 container runtime argv 的 `argv[0]` 只能是同一个绝对 regular-file
   path；前后内容 SHA/size/metadata 任一变化即 blocked；
2. `_run_bounded` 分别检查 stdout/stderr 原始 byte length，任一超限立即抛固定错误；
   不把截断前缀交给 JSON/version parser，错误、报告和日志不得包含原始输出；
3. adapter 用封闭 dispatch：Docker 与 Podman 各自只有固定 argv 和固定 parser；
   不支持或结构不符即 blocked，不能猜测 GPU scheduler 可用。Docker 可以解析其
   固定 `.Runtimes` observation；Podman 官方 `info` 合同没有等价的 NVIDIA
   scheduler 字段，NVIDIA 对 Podman 推荐 CDI。A1 又没有绑定 `nvidia-ctk` binary/
   CDI spec，因此当前 Podman adapter 必须稳定返回 unsupported/blocked，且不得
   执行 Docker 的 `.Runtimes` argv；后续若支持 CDI，需另行绑定 `nvidia-ctk`
   executable 与 `cdi list` observation；
4. 保持 `nantai.remote-readiness-evidence.v1` schema、canonical bytes 和 golden 字段
   不变；不要把 runtime binary SHA 塞进 v1 报告，也不要构造 G2 measurement；
5. Windows 的 symlink 测试可 skip，但绝对路径执行、oversize、adapter 与 TOCTOU
   测试不得 skip。

A1 完成门：

```powershell
python -m pytest -q tests/test_remote_readiness_checker.py
python -m ruff check cloud/remote_readiness_checker.py tests/test_remote_readiness_checker.py
git diff --check -- cloud/remote_readiness_checker.py tests/test_remote_readiness_checker.py
git add -- cloud/remote_readiness_checker.py tests/test_remote_readiness_checker.py
git commit --only cloud/remote_readiness_checker.py tests/test_remote_readiness_checker.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

提交信息不得写 Codex co-author；回执按本文模板给出，然后不等回复，立即进入 B。

### A — 修完并提交 host preflight

只允许修改：

- `cloud/remote_readiness_checker.py`
- `tests/test_remote_readiness_checker.py`

当前草稿拒绝原因与必须先补的 RED：

1. checker 已解析 `runtime_resolved`，但 probe 仍执行配置里的 `runtime` 名称；
   测试必须证明 PATH wrapper 不能在解析后劫持执行，所有 runtime probe 统一执行
   已解析的绝对 regular-file path；
2. `_run_bounded` 当前静默截断 stdout/stderr 后继续解析；oversize 必须产生稳定的
   fail-closed 错误，不能把截断数据当完整 observation，也不能把原始输出或 secret
   写进异常、evidence 或日志；
3. stdout 与 stderr 分别做 byte-cap；任一超限即 blocked。补齐 stdout oversize、
   stderr oversize、含 secret oversize、非 UTF-8 和 timeout RED；
4. 保持 `nantai.remote-readiness-evidence.v1` canonical 字段和 golden bytes 不变；
   host preflight 不得自称 production-ready，不得塞入 G2 measurement；
5. GPU scheduler probe 必须显式限定当前支持的 runtime/adapter。不能把
   Docker `Runtimes.nvidia` 的私有输出格式误当成所有 runtime 通用事实。

专项门：

```powershell
python -m pytest -q tests/test_remote_readiness_checker.py
python -m ruff check cloud/remote_readiness_checker.py tests/test_remote_readiness_checker.py
git diff --check
```

提交只包含上面两条路径。回执必须列出新增 RED 名称、GREEN 数量、v1 golden 是否
保持不变；随后直接开始 B。

### B — 给 fresh-container lifecycle 建立有效测试基线

只允许修改：

- `cloud/remote_training_worker.py`
- `tests/test_remote_training_worker.py`

#### `c66a00a` Codex review：拒绝，必须返修

`c66a00a` 的 `12 passed` 只能证明 mock 按当前实现返回成功，不能证明 NOW-4
完成。提交仍有以下 P0/P1：

1. **P0 image content 未绑定**：`docker inspect {{.Image}}` 只要是任意
   `sha256:*` 就被接受；随后核对的 `.Config.Image` 只是配置时使用的 ref，不证明
   实际 image content。先对 immutable `repo@sha256:...` 解析 expected image ID，
   再要求 container `.Image` 与该 exact ID 相等；配置 ref 也必须精确相等；
2. **P0 container ID 可被覆盖**：`container-id.txt` 仍通过 `os.replace` 写入。
   预置文件、replay 或不同 container 都会被静默覆盖；必须使用 durable
   no-replace publication，collision 一律 ambiguous/blocked；
3. **P0 cleanup 早于已证明的耐久状态**：`_atomic_write` 只 fsync 文件后
   `os.replace`，没有证明 parent directory 已同步；测试只断言 `rm > start`，没有
   对 status/result durability 注入 fault。terminal publication 返回
   “published but durability unknown”时禁止 remove；
4. **P1 cleanup failure 被静默吞掉**：注释写“logged”，代码却只是 `pass`。必须生成
   bounded、无 secret 的 cleanup observation；不得改写已发布训练结果；
5. **P1 测试命名夸大**：现有“partial publication”只模拟结果文件缺失，
   “reconnect replay”只测试第二次拿不到 lock。它们不能作为 durable publication
   fault 或 reconnect 恢复证据。

必须先增加并观察下列 RED：

```text
test_worker_rejects_wrong_resolved_image_id_when_config_ref_matches
test_worker_rejects_preexisting_container_id_without_overwrite
test_worker_does_not_remove_when_terminal_status_durability_is_unknown
test_worker_does_not_remove_when_result_publication_durability_is_unknown
test_worker_records_cleanup_failure_without_rewriting_terminal_result
test_worker_duplicate_start_is_not_reported_as_reconnect_recovery
```

fault injection 必须打在实际 durable primitive / publication boundary，不能只让 fake
少写几个文件。保留跨平台 fake runtime，但它必须分别建模 configured ref、resolved
image ID、container image ID、namespace publication、directory sync 和 cleanup
return code。返修提交不得再写 `Co-Authored-By: Codex...`；Codex review 通过前不能
自称 Codex 共同作者或 accepted。

当前草稿虽然把 `docker run --rm` 改成了 `create/start/rm`，但现有 golden-path
测试在 Windows 被 skip，且仍断言旧 `run --rm`；因此新生命周期目前等于没有测试。
先让平台无关单元测试能在 Windows/Linux 都运行，再做最小实现。

必须先出现并验证的 RED：

1. create 返回 short/空/非 hex ID；
2. inspect 返回别的 image ID、别的 RepoDigest 或 mutable tag；
3. `container-id.txt` 已存在、重放或 attempt/container swap；
4. start 失败、result publication partial、status sync/replace 失败；
5. durable success/failure evidence 尚未完成时调用 `rm`；
6. reconnect 恢复成不同 container，或静默 create 替代实例；
7. argv 中路径、digest 和参数始终为结构化参数，不能经 shell 拼接。

实现门：

- container ID 用 no-replace、可验证的 durable publication，重放不得覆盖；
- 不能仅因 `.Image` 以 `sha256:` 开头就接受；必须定义并测试
  configured immutable ref、resolved image content identity 与 container identity
  三者的等式/绑定；
- cleanup 只有在 durable terminal status/result 发布成功后可达；
- cleanup 失败可以记录为 cleanup 状态，但不得倒写或伪造已发布的训练结果；
- 不在本项构造 G2 schema，也不修改 Codex-owned closure/import 文件。

专项门：

```powershell
python -m pytest -q tests/test_remote_training_worker.py
python -m ruff check cloud/remote_training_worker.py tests/test_remote_training_worker.py
git diff --check
```

提交只包含上面两条路径。回执必须明确 `passed/skipped`，不能用被 skip 的测试声称
生命周期已覆盖；随后直接开始 C。

### C — worker 静态攻击面与 operator blocked report

B 通过 Codex review 后，不等待真实云资源，连续完成：

1. 按 NOW-8 审计 `cloud/train_3dgs_nerfstudio.sh`：mutable image/tag、未固定 CLI、
   shell injection、日志 secret、结果自报成功、发布前清理，每个真实问题先 RED；
2. 按 NOW-7 实现 canonical `blocked-external-input` report 和无 secret preflight
   CLI，使没有 endpoint/GPU 时仍能机器精确说明缺项；
3. 再进入 NOW-5 lifecycle receipt；只做 attempt/container/durable transition，
   不复制 G2/G5 schema。

只有碰到以下边界才停：需要用户提供 secret、需要付费资源、需要访问真实私有数据，
或必须修改 Codex-owned schema。普通代码设计、测试失败、Windows skip、网络 push
重试都不是停工理由。

### A1 之后的连续任务卡（GLM 不得再报空闲）

下列任务严格串行，每项都是独立小提交：

1. **B1 / NOW-4 返修**：只改
   `cloud/remote_training_worker.py`、`tests/test_remote_training_worker.py`，完成本文
   六个指定 lifecycle RED，精确绑定 resolved image ID，container ID durable
   no-replace，证明 terminal/result directory sync 后才允许 cleanup；
2. **C1 / NOW-8 行为审计**：只改
   `cloud/train_3dgs_nerfstudio.sh`、`tests/test_cloud_prepared_training_script.py`。
   恢复被当前草稿删除的 production golden-path 行为测试；静态 grep 只能辅助，
   不能用“脚本包含某个字符串”证明 CLI pin、结构化 argv、真实 exit code、发布顺序
   或无 secret。每个缺陷必须由可执行 fake-tool 测试复现；
3. **D1 / NOW-7 返修**：只改
   `pipeline/production_external_inputs.py`、`tests/test_production_external_inputs.py`，
   删除占位 host/digest/dataset/rights-cleared，完成本文七个精确 RED 和无 secret、
   no-replace CLI；
4. **E1 / NOW-5 caller receipt**：只改
   `pipeline/remote_shell_executor.py`、`tests/test_remote_shell_executor.py`，交付
   attempt/container/durable transition receipt；不得复制 G2/G5 schema，不得让
   caller 自报 GPU pass；
5. **F1 / NOW-6 adapter contract**：仍只改 E1 两条路径，用固定 fake transport
   证明同一 container 的六 probe、accepted 后才可训练，以及 container/executable/
   GPU UUID drift 全部 fail closed；不需要真实 endpoint；
6. **G1 / producer integration audit**：F1 通过 review 后，先只提交一份对现有 G4
   producer 到 `pipeline.production_runtime_evidence`、result bundle v2、closure
   import 的调用图和缺口清单到本文；未经 Codex review 不新建平行 schema。

如果 A1 尚未提交，GLM 不得继续 C1；如果 B1 尚未通过 Codex review，GLM 可以准备
C1 的 RED，但不得把 B1、C1 混为一个提交。Viewer v2、Studio UX、aggregate
acceptance 与 release 文件属于 Codex，不得抢改。

#### 当前 NOW-7 草稿 Codex 预审：禁止提交

当前未提交的 `production_external_inputs.py` / 测试虽然为绿，但模型要求 blocked
report 必须填写看似真实的 host、host key、image digest、dataset SHA，并强制
`rights_clearance="rights-cleared"`。测试为此发明 `gpu-host`、重复字符 SHA 和
虚构镜像。这违反 provenance fail closed：缺失值不能用格式正确的占位符代替，
也不能由 blocked report 自报 rights-cleared。

重写前先加入以下 RED：

```text
test_missing_endpoint_never_requires_or_emits_placeholder_host
test_missing_image_never_requires_or_emits_placeholder_digest
test_missing_dataset_never_requires_or_emits_placeholder_sha
test_rights_cannot_be_claimed_without_bound_source_and_receipt_sha
test_partial_inputs_report_only_exact_unresolved_requirement_ids
test_report_has_no_free_text_or_secret_bearing_value_fields
test_cli_emits_blocked_report_without_any_external_values
```

完成合同：

1. 顶层固定 `state="blocked-external-input"`，包含排序唯一的 requirement entries；
2. 每项使用封闭 `requirement_id` 与 `state=missing|unknown|present-unverified`，
   reason 使用封闭 `reason_code`，不要自由文本；
3. `missing` 时身份字段必须为 `None` 且 canonical JSON 中不出现伪 host/digest/SHA；
4. `present-unverified` 只能绑定 operator input 的内容 SHA，不得声明
   rights-cleared、GPU 可用、metric 或 Viewer accepted；
5. rights 只记录“需要 source/rights receipt”或其两份内容 SHA；是否允许发布仍由
   `validate_capture_rights` 推导，blocked report 无权推导；
6. 实现 NOW-7 要求的无 secret CLI，默认在没有任何外部值时也能成功输出准确 blocked
   report；输出 no-replace、canonical、duplicate-key-safe；
7. 不接受 `gpu-host`、`aaaa...`、`cccc...` 之类占位身份作为生产 fixture 成功路径。

当前 readiness 草稿也仍执行未解析的 `[runtime, ...]` 并静默截断超限输出，尚未满足
A；不要把 NOW-2/3 与 NOW-7 混成一个提交。先完成 A 的两路径小提交，再返修 B，
最后才提交 NOW-7。

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

### G6 — 把 result closure 接回 runner/import（消费端已关闭）

Codex `49c1f9b` 已完成：

- production import 缺失、非法、跨 job 或 identity-only closure 一律 blocked；
- 重新打开 result manifest v2 的每个成员并核对 SHA/size；
- 重新验证 G2 measurement/policy/decision；
- 从 production training ZIP 重新读取 held-out 原图；
- 重新执行 render split/transforms/RGB PNG/camera/report/policy validator；
- 重新推导 G5 closure，不能信任内存模型或旧 decision；
- `real-scene-import-receipt.v3` 绑定 G5 closure 与 G2 runtime decision SHA；
- runner 的现有 completed-receipt revalidation 会重开 v3 import receipt 和 closure；
- preview/Brush 路径保持 preview-only。

GLM G4 producer 现在必须精确交付下列本地文件，否则 import 会稳定 blocked：

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

顺序必须是：

1. 下载并运行 archive v2 raw verifier；
2. 运行 training provenance、identity dataparser 与 render raw validator；
3. 将 archive 内 manifest 和 runtime/render 成员 no-replace 落到上述路径；
4. caller 本地推导并持久化 render decision；
5. 最后调用 `derive_production_training_closure` 并 no-replace 持久化 closure；
6. closure durable publication 完成后才把 executor receipt 标为 succeeded。

`production-training-closure.json` 不能成为它所绑定的 archive manifest member，否则
会形成 manifest↔closure 循环 SHA；它是 raw archive 验证成功后由 caller 本地推导的
后置证据。GLM 只需完成 producer，不要再修改 import schema。

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
