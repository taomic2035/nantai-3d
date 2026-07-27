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

## 执行规则

这是 GLM 当前唯一执行入口。B1 已关闭，当前 active ticket 是 C1；随后按
C1 → D1 → E1 连续推进。一项提交并 push 后立即开始下一项，不等待口头确认。只有
需要 secret、真实私有数据、付费 GPU，或必须修改 Codex-owned
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

### Task C1: 恢复 production shell 的可执行安全门（当前 active）

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

提交成功后自动开始 D1。

### Task D1: 交付无占位身份的 blocked-external-input report

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

### Task E1: 接通 caller lifecycle receipt 与同容器 clearance adapter

**Files:**

- Modify: `pipeline/remote_shell_executor.py`
- Modify: `tests/test_remote_shell_executor.py`

- [ ] **Step 1: 为 lifecycle receipt 建立 RED**

测试必须覆盖 no-replace collision、wrong attempt、container swap、result swap、
namespace 已发布但 sync unknown、reconnect 恢复不同 container。receipt 只记录
job/attempt/workspace、immutable image digest、完整 container ID 与 durable
transition，不得让 caller 自报 GPU/CUDA/Nerfstudio pass。

- [ ] **Step 2: 为同容器六探针建立 RED**

固定 adapter 在同一 container ID 内收集六个 executable 的 raw observations，再交给
既有 `pipeline.production_runtime_evidence` 推导 measurement/policy/decision。
decision 非 accepted 时 training argv 必须不可达；container ID、executable identity
或 GPU UUID 任一 drift 都 blocked。不得创建平行 G2/G5 schema。

- [ ] **Step 3: 联合回归**

```powershell
python -m pytest -q tests/test_remote_shell_executor.py tests/test_remote_training_worker.py tests/test_production_runtime_evidence.py tests/test_production_training_closure.py
python -m ruff check pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
git diff --check -- pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
```

- [ ] **Step 4: 路径限定提交并 push**

```powershell
git add -- pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py
git commit --only pipeline/remote_shell_executor.py tests/test_remote_shell_executor.py -m "feat: bind remote lifecycle clearance receipt"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

E1 完成后停在 review 边界，把调用图、测试数字和未接通的 producer 缺口交给 Codex；
不要自行修改 production closure/import、Viewer 或 release schema。

## Codex review 门

GLM 的每个提交均为 candidate。`pytest` 绿色不能替代以下证据：

- fresh container 的真实身份与 durable transition；
- 同一 container 的 raw probe 与 G2 decision；
- verified G5 archive/closure；
- 真实素材、实测控制点和真实 Viewer/human acceptance。

Codex review 接受前，状态保持 `modeled-unverified` 或
`blocked-external-input`，不能声明 Production V1 已完成。
