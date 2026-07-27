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

## Codex 即时指令（2026-07-27 18:40）

GLM 当前不是“无事可做”。工作树里的 D1 草稿在第一轮 `45 passed, 10 failed`
后已推进到 fresh `51 passed, 4 failed`，另有 `11` 个 Ruff 错误，尚不可提交。
按下面顺序连续执行：

```text
D1.1 修复 canonical report 与 CLI 10 个失败
  → D1.2 跑专项门并小步提交/push
  → E1 fresh-container lifecycle receipt
  → F1 同容器六探针 clearance adapter
  → G1 operations caller 接入与结果 producer 闭环
```

除非需要 secret、真实私有数据、付费 GPU，或必须修改 Codex-owned
closure/import/Viewer/release schema，否则不得回复“无待推进工作”，也不得等待
Codex 口头确认。每个 ticket 独立提交并 push，随后立即开始下一项；Codex 在提交后
异步 review。

### D1.1 当前四个 RED 与 Ruff 返修单

1. 保留合法的顶层 `report_sha256`。修正
   `test_missing_dataset_never_requires_or_emits_placeholder_sha` 与
   `test_cli_emits_blocked_report_without_any_external_values`，只解析并检查
   `production-dataset` requirement 的 identity/receipt 字段，不得用全文
   64-hex 正则误杀报告自身内容 SHA。
2. CLI 参数错误必须经过 `argparse` 的 `type=` 或 `parser.error(...)`，对直接调用
   `main([...])` 产生有界 `SystemExit(2)`；不得把 `ArgumentTypeError` / `ValueError`
   traceback 泄漏给调用者，也不得回显 secret-bearing 原值。当前剩余用例是
   `test_cli_rejects_invalid_operator_sha` 与
   `test_cli_rejects_unknown_requirement_id`。
3. requirement ID 固定为 `production-dataset`，不能命名成
   `rights-cleared-dataset` 并自证 rights。rights 只有 source content SHA 与
   receipt SHA 同时绑定时才是 `present-unverified`，仍不能推导 release allowed。
4. Ruff 当前 `11` 项：三个 `str, Enum` 改为 `StrEnum`，`Optional[T]` 改为
   `T | None`，移除不必要的 forward-reference 引号并整理测试 import。机械修复后
   必须重跑专项，不能只运行 `ruff --fix` 就提交。
5. 不删测试、不降 strict、不放宽 canonical/no-replace 门。完成后预期本文件全部
   `55 passed`，再跑 ruff 与 `git diff --check`。

## 执行规则

这是 GLM 当前唯一执行入口。B1/C1 已关闭，当前 active ticket 是 D1；随后按
D1 → E1 → F1 → G1 连续推进。一项提交并 push 后立即开始下一项，不等待口头确认。只有需要
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

### Task D1: 交付无占位身份的 blocked-external-input report（当前 active）

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

### Task E1: fresh-container lifecycle receipt

**Files:**

- Modify: `pipeline/remote_shell_executor.py`
- Modify: `tests/test_remote_shell_executor.py`

- [ ] **Step 1: 为 lifecycle receipt 建立 RED**

测试必须覆盖 no-replace collision、wrong attempt、container swap、result swap、
namespace 已发布但 sync unknown、reconnect 恢复不同 container。receipt 只记录
job/attempt/workspace、immutable image digest、完整 container ID 与 durable
transition，不得让 caller 自报 GPU/CUDA/Nerfstudio pass。

- [ ] **Step 2: 实现并验证 lifecycle**

receipt 只能记录 job/attempt/workspace、immutable image digest、完整 container ID 与
durable transition。`prepare → submit → restore → poll → fetch` 的每一步都必须复核
同一 attempt/container；reconnect 只能恢复原实例。不得把 host preflight 或 caller
自报值写成 GPU/CUDA/Nerfstudio 通过。

- [ ] **Step 3: 联合回归**

```powershell
python -m pytest -q tests/test_remote_shell_executor.py tests/test_remote_training_worker.py
python -m ruff check pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
git diff --check -- pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
```

- [ ] **Step 4: 路径限定提交并 push**

```powershell
git add -- pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
git commit --only pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py -m "feat: bind remote lifecycle receipt"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

E1 完成后立即开始 F1。

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

G1 完成后才停在 Codex review 边界，回执调用图、测试数字、真实外部输入缺口与下一个
最小 producer 缺口；不要声称 Production V1 已完成。

## Codex review 门

GLM 的每个提交均为 candidate。`pytest` 绿色不能替代以下证据：

- fresh container 的真实身份与 durable transition；
- 同一 container 的 raw probe 与 G2 decision；
- verified G5 archive/closure；
- 真实素材、实测控制点和真实 Viewer/human acceptance。

Codex review 接受前，状态保持 `modeled-unverified` 或
`blocked-external-input`，不能声明 Production V1 已完成。
