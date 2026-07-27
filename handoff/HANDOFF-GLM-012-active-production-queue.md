# GLM Production Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:test-driven-development` task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** 让 GLM 连续关闭 Production V1 的远程 GPU 执行链缺口，不再因没有真实
endpoint 或等待 Codex 新分配而停工。

**Architecture:** GLM 只负责 worker、训练脚本、外部输入门和 remote caller。每项先用
平台无关的 fake runtime/transport 复现失败，再做最小修复；所有 publication、
container identity 和 runtime observation 都 fail closed。Codex 负责逐提交 review，
不与 GLM 同时修改本文件列出的 active paths。

**Tech Stack:** Python 3.11、pytest、Pydantic、POSIX shell、Docker/Podman fake
runtime、`pipeline.durable_io`、canonical JSON。

---

## GLM 立即执行卡（2026-07-27，当前有效）

不要再回复“无事可做”。`P0-CI` 已由 Codex 在 `70a965e` 关闭；现在从 `F1`
开始，随后连续执行 `G1 → H1 → I1`。完成一张就独立提交、用一次性代理 push，然后直接
开始下一张，不等待 Codex 回执。

### Codex 当前派发（2026-07-27，基线 `c8b7701`）

Codex 已用一次性代理重新 fetch 并核对 `origin/main`：远端仍停在 `c8b7701`，没有
F1 candidate，GLM 也没有领先远端的提交。“无待推进工作”是不正确的。下面四项都是
repo-local fake transport/文件系统任务，不依赖 GPU、secret、正式素材、Blender 或
Codex 新接口。GLM 不要再做差距审计、plan 或静态 review，直接执行：

```text
git -c http.proxy=http://127.0.0.1:7890 fetch origin main
git -c http.proxy=http://127.0.0.1:7890 pull --ff-only origin main

当前只做 F1：
  1. 先只修改 tests/test_remote_shell_executor.py；
  2. 先写 test_clearance_runs_fixed_probes_in_lifecycle_container；
  3. 立即单跑：
     python -m pytest -q tests/test_remote_shell_executor.py::test_clearance_runs_fixed_probes_in_lifecycle_container
  4. 第一轮只回执测试全名、真实失败断言和 git diff --stat；
  5. 然后修改 pipeline/remote_shell_executor.py 接通 GREEN；
  6. 再补齐 Task F1 余下 6 个行为门；
  7. 接入既有 pipeline.production_runtime_evidence，禁止新建 schema；
  8. 专项测试、Ruff、diff-check、独立提交、临时代理 push。

F1 push 后无需等待：
  G1 operations production result producer
  → H1 deadline / executor close
  → I1 bounded-memory import hashing
```

第一轮回执必须是一个真实 RED，不接受“计划已完成”“等待 Codex”“缺 GPU”或只给
静态分析。F1 全程使用 fake transport，不需要 secret、正式素材、付费 GPU、Blender
或 Codex 新接口。若第一个 RED 无法建立，必须报告具体代码符号和阻塞调用栈，不能回复
“无事可做”。

Codex 当前并行修改 `pipeline/production_capture_inputs.py`、
`tests/test_production_capture_inputs.py` 和真实数据用户文档；GLM 禁止碰这些文件，
也不要修改 `pipeline/studio_server.py`、Viewer、Studio、release、acceptance
aggregate 或本 handoff。

| 顺序 | Ticket | 只允许主动修改 | 必须交付的结果 |
|---|---|---|---|
| 已关闭 | `P0-CI` | `pipeline/remote_training_drill.py`、`tests/test_remote_training_drill.py` | `70a965e` 已刷新 registry；专项 `12 passed`，远程固定演练 job 通过 |
| 2 | `F1` | `pipeline/remote_shell_executor.py`、`tests/test_remote_shell_executor.py` | 同一 lifecycle container 内六探针 clearance；container/executable/GPU/TOCTOU 漂移全部 fail closed；非 accepted 时训练 argv 不可达 |
| 3 | `G1` | `pipeline/real_scene_operations.py`、`tests/test_real_scene_operations.py`；确有必要才继续修改 F1 两文件 | `train-production` 只在 accepted clearance 后运行并产出既有 import 所需八个 result 文件；不修改消费端 schema |
| 4 | `H1` | `pipeline/real_scene_operations.py`、`tests/test_real_scene_operations.py` | poll sleep 不越过 deadline；success/failed/exception 都显式关闭 executor |
| 5 | `I1` | `pipeline/real_scene_import.py`、`tests/test_real_scene_import.py` | receipt artifact SHA/size 用 bounded-memory stable descriptor 计算；不改 schema、canonical receipt 或语义门 |

**现在只从 `F1` 开始。** 先写本文件 Task F1 列出的七个行为 RED，再接入既有
`pipeline.production_runtime_evidence`；不要新建平行 schema。回执格式只写：

```text
ticket / commit SHA / changed paths / RED failure / GREEN counts + skipped
/ ruff / diff-check / CI URL or status / next ticket already started
```

以下情况才允许暂停：需要真实 secret、私有正式素材、付费 GPU，或者必须修改
Codex-owned Viewer/Studio/release/acceptance aggregate。普通实现选择、测试失败和 CI 等待
都不是暂停理由。

## 当前 active ticket：F1 同容器六探针 clearance adapter

E1 fresh-container lifecycle 已完成实现、两轮独立 review、提交并 push。关闭证据：

- worker 在 container ID durable、immutable digest 复核后且 start 前发布唯一
  canonical lifecycle receipt；
- caller 每次 `poll/fetch/restore` 都完整复核同一 receipt，terminal-first 不绕过，
  attempt history 不再自报 `running`；
- operations no-replace 持久化 job/lifecycle pair，首次异步延迟会在 deadline 内继续
  轮询，恢复只接受崩溃前持久化的同一 receipt；
- publication collision、`DurableIOError(published=False|True)`、staging collision、
  status timestamp 回退、malformed receipt 与 descriptor/symlink swap 均有真实 RED；
- fresh E1 联合门 `177 passed, 5 skipped`，全仓 Ruff、四组 Node 测试与
  `git diff --check` 通过；规格和代码质量 review 均 APPROVED。
- GLM candidate `b71e5de` 与 Codex closure `ab0c7dc` 已在 `origin/main`。

`P0-CI` 已由 Codex 在 `70a965e` 修复：稳定 case ID 现在绑定
`test_submit_keeps_receipt_not_started_until_authoritative_poll`，registry 测试同时
锁定新 node ID 与 not-started 语义；专项 `12 passed`、Ruff 和 diff-check 通过，
GitHub Actions `remote-training-drill (fixed transport fixtures)` 通过。GLM 应立即
执行 F1，不等待其余矩阵。

## Codex 即时指令（2026-07-27 20:35）

GLM 现在按以下顺序连续执行，不再返工已关闭任务，也不等待口头确认：

```text
F1 同容器六探针 clearance adapter
  → G1 operations caller 接入与结果 producer 闭环
  → H1 deadline / executor-close 硬化
  → I1 import artifact bounded-memory 内容锁
```

D1 关闭证据：

- GLM `081d818`：封闭 requirement/state/reason 模型、canonical report、无 secret CLI；
- Codex `91585c4`：增加 reason/state 一致性、CLI 畸形值不回显、symlink no-follow、
  cleanup 不覆盖 durability 状态四类对抗门；
- fresh 专项 `62 passed, 1 skipped`；联合 durable I/O、真实数据合同
  `110 passed, 3 skipped`；Ruff 与 `git diff --check` 通过；
- skipped 仅为当前 Windows 无 symlink 创建权限，不代表代码路径被接受为 verified。

除非需要 secret、真实私有数据、付费 GPU，或必须修改 Codex-owned
closure/import/Viewer/release schema，否则不得回复“无待推进工作”，也不得等待
Codex 口头确认。每个 ticket 独立提交并 push，随后立即开始下一项；Codex 在提交后
异步 review。

## 执行规则

这是 GLM 当前唯一执行入口。B1/C1/D1/E1/P0-CI 已关闭，当前 active ticket 是 F1；
随后按 F1 → G1 → H1 → I1 连续推进。一项提交并 push 后立即开始下一项，不等待
口头确认。只有需要
secret、真实私有数据、付费 GPU，或必须修改 Codex-owned
Viewer/Studio/release/schema 路径时才暂停。

- 共享单一 `main` / worktree；只能路径限定 `git add` 和 `git commit --only`。
- 不使用 `git add -A`、`commit -a`，不改 Viewer、Studio、release 或 acceptance
  aggregate。
- GLM 提交不写 Codex co-author。
- push 固定使用：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

每项回执只需给出：commit SHA、修改路径、RED 名称与失败原因、GREEN 数量与 skipped、
ruff、`git diff --check`、剩余风险、已经开始的下一项。

### Task B1: worker durability（已关闭）

**关闭证据：** GLM `9eebbc3` 关闭原四个 lifecycle RED；Codex `776fc25` 又用两个
真实 fault-injection RED 修复 staging cleanup 覆盖原始
`DurableIOError.published=False|True` 的漏洞。fresh worker 专项为 `22 passed`，
联合 durable I/O + worker + remote shell 为 `100 passed, 3 skipped`。

原失败测试如下，保留作回归索引：

```text
test_worker_rejects_partial_publication
test_worker_does_not_remove_when_terminal_status_durability_is_unknown
test_worker_does_not_remove_when_result_publication_durability_is_unknown
test_worker_cleanup_observation_failure_never_rewrites_terminal_status
```

**Files:**

- Modify: `cloud/remote_training_worker.py`
- Modify: `tests/test_remote_training_worker.py`

- [ ] **Step 1: 保留并复跑当前 RED**

```powershell
python -m pytest -q tests/test_remote_training_worker.py
```

Expected: 上述四项失败；不要删除、skip、放宽断言或整体 monkeypatch
`build_remote_result_bundle`。

- [ ] **Step 2: 明确 cleanup 状态机**

实现必须等价于下面的决策，不得用异常字符串推导 publication：

```python
if namespace_published_but_sync_unknown:
    preserve_container = True
    write_failure_status = False
    write_cleanup_observation = False
elif container_started_and_result_pipeline_failed:
    preserve_container = True
    write_cleanup_observation = False
elif terminal_status_is_durable:
    rm_exit_code = remove_container_once()
    cleanup_observation_published = record_cleanup_observation(rm_exit_code)
    if not cleanup_observation_published:
        return 75
```

具体要求：

1. 没有实际执行 `rm` 时不得写 `cleanup-observation.json`，也不得伪造
   `rm_exit_code=-1`；
2. container 已成功 start 后，result 缺失、bundle validation 失败或 bundle
   publication 失败都保留 container 供恢复，不允许 cleanup；
3. `DurableIOError(published=True)` 和
   `RemoteResultBundleError.published is True` 都保持磁盘现状、返回 75；
4. cleanup observation publication 失败时保持原 terminal status，只返回 75；
   不能第二次 `rm`，不能倒写 `failed`；
5. `_safe_record_cleanup_observation` 改为返回机器可判定的 `bool`，不能静默吞错后返回
   0；
6. staging file 的 `unlink` 只能做 best-effort，不能掩盖原始 durability 异常。

- [ ] **Step 3: 运行 worker 专项门**

```powershell
python -m pytest -q tests/test_remote_training_worker.py
python -m ruff check cloud/remote_training_worker.py tests/test_remote_training_worker.py
git diff --check -- cloud/remote_training_worker.py tests/test_remote_training_worker.py
```

Expected: `20 passed`，零失败；Windows 不得靠 skip 声称 lifecycle 已覆盖。

- [ ] **Step 4: 路径限定提交并 push**

```powershell
git add -- cloud/remote_training_worker.py tests/test_remote_training_worker.py
git commit --only cloud/remote_training_worker.py tests/test_remote_training_worker.py -m "fix: preserve ambiguous remote worker evidence"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

本项不得重做；直接执行 C1。

### Task C1: production shell 可执行安全门（已关闭）

**关闭证据：** GLM `b02a271` 恢复 fake-tool golden path；Codex `5557ed1` 根据
Nerfstudio v1.1.5 官方 entrypoint 源码修正了不存在的 `--version` 假设。包版本由
`importlib.metadata` 严格锁为 `1.1.5`，绝对 regular-file CLI 使用官方支持的
Tyro `-h` 做无副作用 probe，输出丢弃且不能泄露 canary。真实 PATH replacement
测试证明 probe 后移除原 CLI 不会落入后位恶意程序。fresh C1 为
`13 passed, 0 skipped`；与 durable I/O、worker、remote shell 联合回归为
`113 passed, 3 skipped`。

官方依据：

- `ns-train`：
  <https://github.com/nerfstudio-project/nerfstudio/blob/v1.1.5/nerfstudio/scripts/train.py>
- `ns-export`：
  <https://github.com/nerfstudio-project/nerfstudio/blob/v1.1.5/nerfstudio/scripts/exporter.py>
- package version / console scripts：
  <https://github.com/nerfstudio-project/nerfstudio/blob/v1.1.5/pyproject.toml>

**Files:**

- Modify: `cloud/train_3dgs_nerfstudio.sh`
- Modify: `tests/test_cloud_prepared_training_script.py`

- [ ] **Step 1: 先恢复并增加行为 RED**

必须用 fake `ns-train` / `ns-export` 真正执行脚本，覆盖：

```text
test_prepared_mode_runs_pinned_stubbed_golden_path
test_cloud_script_is_valid_bash
test_production_rejects_ns_train_version_output_from_nonzero_command
test_production_rejects_ns_train_version_substring_collision
test_production_rejects_ns_export_version_output_from_nonzero_command
test_production_uses_resolved_cli_paths_after_version_probe
test_production_mode_pins_runtime_and_coordinate_flags
test_invalid_container_identity_fails_before_runtime_probe
```

静态 grep 只能辅助，不能替代 argv、return code、执行顺序和结果文件断言。

- [ ] **Step 2: 最小修复 CLI identity**

实现边界：

```bash
NS_TRAIN_PATH="$(command -v ns-train)" || exit 1
NS_EXPORT_PATH="$(command -v ns-export)" || exit 1
```

解析结果必须是绝对 regular file；version probe 和后续执行始终使用同一绝对路径。
禁止 `|| true`，版本必须严格解析为 pinned exact version，不能 substring 接受。
production prepared mode 依次执行 train → evaluate → export，不安装依赖、不重跑
SfM、不把 secret 写入日志。

- [ ] **Step 3: 专项验证、提交并 push**

```powershell
python -m pytest -q tests/test_cloud_prepared_training_script.py
python -m ruff check tests/test_cloud_prepared_training_script.py
git diff --check -- cloud/train_3dgs_nerfstudio.sh tests/test_cloud_prepared_training_script.py
git add -- cloud/train_3dgs_nerfstudio.sh tests/test_cloud_prepared_training_script.py
git commit --only cloud/train_3dgs_nerfstudio.sh tests/test_cloud_prepared_training_script.py -m "fix: verify production training cli behavior"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

本项不得重做；直接执行 D1。

### Task D1: 交付无占位身份的 blocked-external-input report（已关闭）

**关闭证据：** GLM `081d818` + Codex `91585c4`；专项
`62 passed, 1 skipped`，联合回归 `110 passed, 3 skipped`。

**Files:**

- Modify: `pipeline/production_external_inputs.py`
- Modify: `tests/test_production_external_inputs.py`

- [ ] **Step 1: 建立七个精确 RED**

```text
test_missing_endpoint_never_requires_or_emits_placeholder_host
test_missing_image_never_requires_or_emits_placeholder_digest
test_missing_dataset_never_requires_or_emits_placeholder_sha
test_rights_cannot_be_claimed_without_bound_source_and_receipt_sha
test_partial_inputs_report_only_exact_unresolved_requirement_ids
test_report_has_no_free_text_or_secret_bearing_value_fields
test_cli_emits_blocked_report_without_any_external_values
```

- [ ] **Step 2: 实现封闭模型与 CLI**

固定：

```python
state = "blocked-external-input"
requirement_state = Literal["missing", "unknown", "present-unverified"]
```

requirement ID 与 reason code 使用封闭枚举；missing 身份为 `None`，禁止 `gpu-host`、
重复字符 SHA、虚构 digest 或 `rights-cleared`。CLI 默认不接收 secret，也能输出
canonical、duplicate-key-safe、no-replace report。present-unverified 只绑定输入
内容 SHA，不得推导 GPU ready、metric、Viewer accepted 或 release allowed。

- [ ] **Step 3: 专项验证、提交并 push**

```powershell
python -m pytest -q tests/test_production_external_inputs.py
python -m ruff check pipeline/production_external_inputs.py tests/test_production_external_inputs.py
git diff --check -- pipeline/production_external_inputs.py tests/test_production_external_inputs.py
git add -- pipeline/production_external_inputs.py tests/test_production_external_inputs.py
git commit --only pipeline/production_external_inputs.py tests/test_production_external_inputs.py -m "feat: report exact production input blockers"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

提交成功后自动开始 E1。

### Task E1: fresh-container lifecycle receipt（已关闭）

**Files:**

- Modify: `cloud/remote_training_worker.py`
- Modify: `tests/test_remote_training_worker.py`
- Modify: `pipeline/remote_shell_executor.py`
- Modify: `tests/test_remote_shell_executor.py`

**当前未提交草稿预审：拒绝，禁止按现状提交。**

`RemoteContainerLifecycleReceipt` + 新 standalone 测试目前只证明 caller 能自构造一份
自洽 JSON，未证明 worker 的真实 durable transition。返工必须满足：

1. 删除原始 `workspace` 路径，改为 caller/worker 可独立重算的
   `workspace_identity_sha256`；公开或异常输出不得含远程私有路径。
2. 删除 caller 自报的 `submitted/restored/polled/fetched` 时间线和任意
   `transition_evidence_sha256`。E1 只有 worker 发布的单一
   `container-created-identity-verified` transition；poll/fetch 状态不属于该 receipt。
3. receipt 必须由 worker 在真实 `container-id.txt` durable + runtime image inspect
   成功后发布。caller 只能 load/rederive/verify，不能用公开 builder 代替 producer。
4. 必须实现 worker bounded lifecycle reader，并接入 caller 的 poll/restore/fetch；
   standalone model round-trip 测试不能替代执行顺序、wrong-attempt/container-swap 和
   reconnect 行为测试。
5. `DurableIOError(published=True)` 表示 namespace 可能已经发布但 sync unknown；
   测试不得断言 destination 必然不存在或可安全 retry，cleanup 也不得掩盖该状态。
6. 新测试折回本任务列出的 worker/executor 测试路径；不要用额外测试文件绕过联合
   fixture 与调用图。

- [ ] **Step 1: 为 lifecycle receipt 建立 RED**

先固定这些行为测试：

```text
test_worker_publishes_canonical_lifecycle_after_digest_verification
test_worker_lifecycle_reader_is_bounded_stable_and_duplicate_safe
test_poll_does_not_report_running_before_lifecycle_is_bound
test_lifecycle_rejects_wrong_attempt
test_lifecycle_rejects_container_swap
test_lifecycle_publication_collision_is_fail_closed
test_lifecycle_sync_unknown_preserves_original_container
test_restore_requires_same_durable_lifecycle_receipt
```

receipt 只记录 job/attempt/workspace identity SHA、immutable image digest、完整
container ID 与单一 durable transition，不得让 caller 自报
GPU/CUDA/Nerfstudio pass。

- [ ] **Step 2: 实现并验证 lifecycle**

worker 在 `container-id.txt` 已 durable、image digest 已由 runtime inspect 复核后，
训练启动前 no-replace 发布 canonical `container-lifecycle.json`，transition 固定为
`container-created-identity-verified`。新增与 `status` 同等级的只读
`lifecycle --job-dir --max-bytes` 命令，使用 stable regular-file read；禁止 caller
直接 `cat`、信任 symlink 或解析自由文本。

caller 对 lifecycle 做 duplicate-key-safe、canonical、SHA 与完整 identity 复核。
receipt 未到只返回 `unknown`，不能返回 `running`；一旦绑定，`poll → fetch` 必须一直
复核同一 attempt/container。reconnect 只能恢复同一 durable receipt，不能创建替代
实例。F1 之前 receipt 模型中不得出现 GPU name/UUID、CUDA、Python、Nerfstudio 或
`accepted/ready` 字段。

- [ ] **Step 3: 联合回归**

```powershell
python -m pytest -q tests/test_remote_shell_executor.py tests/test_remote_training_worker.py
python -m ruff check cloud/remote_training_worker.py pipeline/remote_shell_executor.py tests/test_remote_training_worker.py tests/test_remote_shell_executor.py
git diff --check -- cloud/remote_training_worker.py pipeline/remote_shell_executor.py tests/test_remote_training_worker.py tests/test_remote_shell_executor.py
```

- [ ] **Step 4: 路径限定提交并 push**

```powershell
git add -- cloud/remote_training_worker.py pipeline/remote_shell_executor.py tests/test_remote_training_worker.py tests/test_remote_shell_executor.py
git commit --only cloud/remote_training_worker.py pipeline/remote_shell_executor.py tests/test_remote_training_worker.py tests/test_remote_shell_executor.py -m "feat: bind remote lifecycle receipt"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

E1 与 P0-CI 已关闭；现在立即开始 F1。

### Task P0-CI: 固定演练 registry 刷新（已由 `70a965e` 关闭）

**Files:**

- Modify: `pipeline/remote_training_drill.py`
- Modify: `tests/test_remote_training_drill.py`

只修 registry 漂移，不回退 E1 的 fail-closed 语义：

1. `P1-3A-submit-running` case ID 保持稳定，避免破坏既有报告消费者；
2. `pytest_node_id` 改为
   `tests/test_remote_shell_executor.py::test_submit_keeps_receipt_not_started_until_authoritative_poll`；
3. `expected_semantics` 改为 “submit remains not-started until authoritative
   lifecycle/status poll”；
4. registry 单元测试必须锁定新 node ID 与新语义，防止再次只锁 case ID；
5. 不修改 `remote_shell_executor.py`，不重新引入 submit 自报 `running`。

验证与提交：

```powershell
python -m pytest -q tests/test_remote_training_drill.py tests/test_remote_shell_executor.py::test_submit_keeps_receipt_not_started_until_authoritative_poll
python -m ruff check pipeline/remote_training_drill.py tests/test_remote_training_drill.py
git diff --check -- pipeline/remote_training_drill.py tests/test_remote_training_drill.py
git add -- pipeline/remote_training_drill.py tests/test_remote_training_drill.py
git commit --only pipeline/remote_training_drill.py tests/test_remote_training_drill.py -m "fix: refresh remote drill lifecycle case"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

专项结果：`12 passed`；Ruff、diff-check 与远程固定演练 job 通过。

### Task F1: 同容器六探针 clearance adapter

**Files:**

- Modify: `pipeline/remote_shell_executor.py`
- Modify: `tests/test_remote_shell_executor.py`

- [ ] **Step 1: 建立行为 RED**

fake transport 必须真实记录 argv 与执行顺序，至少覆盖：

```text
test_clearance_runs_fixed_probes_in_lifecycle_container
test_training_is_unreachable_when_runtime_decision_is_not_accepted
test_clearance_rejects_container_swap
test_clearance_rejects_executable_identity_drift
test_clearance_rejects_gpu_uuid_drift
test_clearance_rejects_probe_toctou
test_restore_revalidates_same_clearance_attempt
```

- [ ] **Step 2: 接入唯一权威 G2**

只收集同一 container ID 内六个 executable 的 raw observations，交给既有
`pipeline.production_runtime_evidence` 构造并重新验证
measurement/policy/decision。不得复制模型、建立 `RemoteReadinessEvidence.v2` 或让
caller 直接构造 accepted。decision 非 accepted 时 training argv 必须不可达。

- [ ] **Step 3: 验证、提交并继续**

```powershell
python -m pytest -q tests/test_remote_shell_executor.py tests/test_production_runtime_evidence.py
python -m ruff check pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
git diff --check -- pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
git add -- pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
git commit --only pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py -m "feat: gate training on container clearance"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

F1 完成后立即开始 G1。

### Task G1: operations caller 与 production result producer

**Files:**

- Modify: `pipeline/real_scene_operations.py`
- Modify: `tests/test_real_scene_operations.py`
- 如确有必要可继续修改：
  `pipeline/remote_shell_executor.py`、`tests/test_remote_shell_executor.py`

- [ ] **Step 1: 建立端到端 fake transport RED**

从 `train-production` stage 出发，证明：

1. 缺外部输入时输出 D1 canonical report，只列精确 unresolved IDs；
2. 外部输入 `present-unverified` 不能绕过 host preflight 或 F1 clearance；
3. accepted clearance 后同一 attempt/container 才能训练；
4. 下载后先运行 archive v2 raw verifier、training provenance、identity dataparser
   与 render raw validator；
5. manifest/runtime/render evidence no-replace 落盘后，才允许调用既有
   `derive_production_training_closure`；
6. closure durable publication 完成后，executor receipt 才能变成 succeeded；
7. collision、sync unknown、result swap、attempt/container drift 全部保持
   blocked/unknown，不能出现 completed。

- [ ] **Step 2: 只接 producer，不改消费端 schema**

精确交付 import 已要求的八个文件：

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

不得修改 `pipeline/production_training_closure.py`、
`pipeline/real_scene_import.py`、Viewer 或 release schema；不得把 closure 放回其所
绑定的 archive manifest 形成循环 SHA。

- [ ] **Step 3: 联合回归与独立提交**

```powershell
python -m pytest -q tests/test_real_scene_operations.py tests/test_remote_shell_executor.py tests/test_remote_training_worker.py tests/test_production_runtime_evidence.py tests/test_production_training_closure.py tests/test_real_scene_import.py
python -m ruff check pipeline/real_scene_operations.py pipeline/remote_shell_executor.py tests/test_real_scene_operations.py tests/test_remote_shell_executor.py
git diff --check -- pipeline/real_scene_operations.py pipeline/remote_shell_executor.py tests/test_real_scene_operations.py tests/test_remote_shell_executor.py
```

G1 完成后先回执调用图、测试数字、真实外部输入缺口与下一个最小 producer 缺口，
随后继续 H1；不要声称 Production V1 已完成。

### Task H1: deadline 与 executor close 硬化

G1 push 后继续做这两个已知 P2，不等待新指令：

- `train-production` 的 poll sleep 必须取
  `min(poll_interval, remaining_deadline)`，不得越过 deadline 一个完整轮询周期；
- `RemoteShellExecutor` 在 success、failed、exception 三条路径均显式 `close()`，
  不能依赖 `__del__` 释放 Windows private-key guard。

**Files:**

- Modify: `pipeline/real_scene_operations.py`
- Modify: `tests/test_real_scene_operations.py`

先建立：

```text
test_remote_poll_sleep_never_overshoots_deadline
test_train_production_closes_remote_executor_on_success
test_train_production_closes_remote_executor_on_failure
test_train_production_closes_remote_executor_on_exception
```

专项测试、Ruff、`git diff --check` 通过后独立提交
`fix: bound remote polling and close executor`，使用一次性代理 push。H1 完成后不等待
Codex review，直接开始 I1；若仍没有真实 endpoint/secret/GPU，只报告精确 external
gate，不能声明正式版已完成。

### Task I1: real-scene import 大文件 bounded-memory 内容锁

Codex `26a109c` 已让 Studio 对 receipt-bound PLY 采用 1 MiB 分块哈希与分块响应；
但 `validate_real_scene_import_receipt` 在启动前仍通过整块 `bytes` 计算 artifact
binding，百万级 Gaussian 会产生不必要的内存峰值。I1 关闭这个上游缺口，不依赖真实
GPU、secret、正式素材或 Codex 新接口。

**Files:**

- Modify: `pipeline/real_scene_import.py`
- Modify: `tests/test_real_scene_import.py`

先建立以下行为 RED：

```text
test_import_artifact_bindings_hash_large_ply_in_bounded_chunks
test_receipt_revalidation_hashes_large_bound_ply_in_bounded_chunks
test_streaming_artifact_digest_rejects_mid_read_identity_or_size_change
test_streaming_artifact_digest_rejects_linklike_or_nonregular_member
test_streaming_artifact_digest_preserves_existing_canonical_receipt
```

实现一个私有 stable regular-file digest helper，固定每次读取不超过 1 MiB，使用同一
descriptor 的 `lstat/fstat` 前后 identity、size、mtime 与 SHA-256 推导
`(byte_length, sha256)`。把它同时接入 `_artifact_bindings` 和
`validate_real_scene_import_receipt` 的 artifact binding 循环。要求：

1. 不修改 `RealSceneImportReceipt` schema、artifact 排序或现有 canonical bytes；
2. 不跳过 metric alignment、manifest/chunk/coordinate repack、PLY semantic 或
   production closure 验证；
3. symlink、junction、非 regular、读中替换/截断、hash/size mismatch 全部 fail closed；
4. 测试必须用 read-size observer 证明每次读取 `<= 1 MiB`，不能只 grep 源码；
5. 不修改 Studio、Viewer、release 或 acceptance aggregate。

验证与提交：

```powershell
python -m pytest -q tests/test_real_scene_import.py
python -m ruff check pipeline/real_scene_import.py tests/test_real_scene_import.py
git diff --check -- pipeline/real_scene_import.py tests/test_real_scene_import.py
git add -- pipeline/real_scene_import.py tests/test_real_scene_import.py
git commit --only pipeline/real_scene_import.py tests/test_real_scene_import.py -m "perf: stream real scene artifact digests"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

I1 push 后回执 Codex review；如果 F1/G1/H1/I1 均已完成且没有真实 endpoint，只提交
精确 external blocker，不要把 repo-local 队列说成“从未存在”。

## Codex review 门

GLM 的每个提交均为 candidate。`pytest` 绿色不能替代以下证据：

- fresh container 的真实身份与 durable transition；
- 同一 container 的 raw probe 与 G2 decision；
- verified G5 archive/closure；
- 真实素材、实测控制点和真实 Viewer/human acceptance。

Codex review 接受前，状态保持 `modeled-unverified` 或
`blocked-external-input`，不能声明 Production V1 已完成。
