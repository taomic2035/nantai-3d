# GLM 当前连续工单

> **执行方式：** 逐项使用 `superpowers:test-driven-development`。每张工单必须先产生
> 可复现的 RED，再做最小实现；专项测试、Ruff、diff-check 全绿后独立提交并立即用
> 临时代理 push。不要等待 Codex 口头确认，也不要回复“无事可做”。

## 立即执行令（2026-07-28）

**当前没有 Codex 阻塞。** Codex 已将 Linux CI / symlink evidence 修复以
`c650aa8` 推到 `origin/main`，`pipeline/real_scene_operations.py` 已释放。GLM 现在
必须按以下动作开始，不再做新的 gap audit，也不再询问是否有任务：

```powershell
git -c http.proxy=http://127.0.0.1:7890 fetch origin main
git status --short --branch
git log -1 --oneline
python -m pytest -q tests/test_real_scene_operations.py
```

确认工作树没有其它 agent 在 H1 两个路径上的在途修改后，**立即领取 H1**，先新增
四个明确列出的 RED 行为测试。若发现路径冲突，只允许跳到 I1；不得停工或回复
“等待 Codex”。H1 push 后连续做 I1、J1、K1，每张工单一个小提交并立即 push。

GLM 第一次回执只需报告：

```text
started H1 / baseline c650aa8 or newer
/ exact two claimed paths
/ first RED test name and actual failure
```

不要先写长篇状态总结；没有 RED 证据不算已经开工。

**目标：** 在没有真实 GPU、secret 和正式素材时，继续关闭 Production V1 远程训练与
大型产物链路中可由 repo-local fake transport / 文件系统证明的工程缺口。

**当前基线：** 至少 `c650aa8`。Codex 已 push G1 和 Linux CI evidence 修复，
H1 的 operations path 已释放，J1/K1 的 executor path 也已解锁。
共享 worktree 仍不得 reset、checkout、stash、rebase 或清理其它 agent 的修改。

## 连续顺序

```text
H1 deadline / executor-close（立即开始）
  → I1 import artifact bounded-memory digest（H1 push 后立即开始）
  → J1 result-bundle streaming verify/extract
  → K1 production v2 fetch integration matrix
```

H1 与 I1 现在即可连续完成，不依赖 CUDA、Blender、正式素材、secret 或 Codex 新接口。
G1 已由 `1727f8f` + `92b76b5` push；四项之间都不再等待 Codex 口头确认。

## 共享工作树边界

开始每张工单前先读 `git status --short`。因此：

- H1 只改 `pipeline/real_scene_operations.py` 和
  `tests/test_real_scene_operations.py`；
- I1 只改 `pipeline/real_scene_import.py`，并新建
  `tests/test_real_scene_import_streaming.py`，不要改
  `tests/test_real_scene_import.py`；
- J1/K1 只在 H1/I1 push 后开始，路径已经由 `92b76b5` 解锁；
- 禁止 `git add -A`、`git commit -a`；只做路径限定 stage/commit；
- GLM 提交不写 Codex co-author。

---

## H1：deadline 与 executor 显式关闭

**Files**

- Modify: `pipeline/real_scene_operations.py`
- Modify: `tests/test_real_scene_operations.py`

### RED

先新增并单跑：

```text
test_remote_poll_sleep_never_overshoots_deadline
test_train_production_closes_remote_executor_on_success
test_train_production_closes_remote_executor_on_failure
test_train_production_closes_remote_executor_on_exception
```

测试必须使用 fake monotonic、fake sleep 和带 `close_calls` 的 fake executor，证明：

1. 每个 poll sleep 都是
   `min(remote_poll_interval_seconds, max(0, deadline - monotonic()))`；
2. remaining deadline 为零时不再 sleep；
3. executor 构造成功后，prepare/submit/restore/poll/fetch 的 success、blocked、
   unknown、failed 和 exception 路径都恰好显式 `close()` 一次；
4. 不依赖 `__del__`，不通过 grep 源码冒充行为测试。

### GREEN

用 `try/finally` 或等价的显式生命周期包围 executor 的完整使用范围。不得改 remote
schema、receipt 状态语义或 retry/new-attempt 规则。

### Codex review addendum（2026-07-28，提交前必须关闭）

当前工作树版本的四个新增测试虽通过，但尚未满足上面的 H1 合同：

1. `executor.prepare(bundle)` 仍在 `try/finally: close()` 外；构造成功后 prepare
   抛出 `RemoteShellExecutionError` 会直接返回而不显式关闭 executor。必须让
   `finally` 覆盖从 prepare 开始的完整使用范围。
2. remaining 在前一次 deadline 判断后可能降为 `0`，当前实现仍调用
   `time.sleep(0)`；合同要求 remaining 为零时直接返回对应 timeout/unknown，不再
   调用 sleep，也不能在 deadline 后开始下一次 poll。
3. 当前 close 测试只覆盖 success、failed 和 poll exception；补参数化行为测试，
   至少逐项证明 prepare、submit、restore、poll、fetch 的 blocked/unknown/exception
   返回均恰好显式 close 一次，且不依赖 `__del__`。
4. deadline 测试必须新增“第二次 monotonic 跨过 deadline”的 case，同时断言
   `sleep_calls` 不包含 `0`，并断言 deadline 后没有额外 `poll()`。

不要仅把测试名称改成合同文字；先让这些新增 case 在当前实现上真实 RED，再修 GREEN。

```powershell
python -m pytest -q tests/test_real_scene_operations.py -k "deadline or closes_remote_executor"
python -m pytest -q tests/test_real_scene_operations.py
python -m ruff check pipeline/real_scene_operations.py tests/test_real_scene_operations.py
git diff --check -- pipeline/real_scene_operations.py tests/test_real_scene_operations.py
git add -- pipeline/real_scene_operations.py tests/test_real_scene_operations.py
git commit --only pipeline/real_scene_operations.py tests/test_real_scene_operations.py -m "fix: bound remote polling and close executor"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

H1 push 后立即开始 I1，不等待 review。

---

## I1：大型 import artifact 的稳定流式摘要

**Files**

- Modify: `pipeline/real_scene_import.py`
- Create: `tests/test_real_scene_import_streaming.py`

### RED

新增以下行为测试：

```text
test_import_artifact_bindings_hash_large_ply_in_bounded_chunks
test_receipt_revalidation_hashes_large_bound_ply_in_bounded_chunks
test_streaming_artifact_digest_rejects_mid_read_identity_change
test_streaming_artifact_digest_rejects_mid_read_size_or_mtime_change
test_streaming_artifact_digest_rejects_linklike_or_nonregular_member
test_streaming_artifact_digest_preserves_canonical_receipt_bytes
```

测试必须通过 read-size observer 证明单次读取不超过 `1 MiB`；至少一个 fixture 大于
`3 MiB`。替换/截断测试必须在读取过程中真实改变文件或 descriptor identity，不能
只 monkeypatch 最终 SHA。

### GREEN

实现一个私有 stable regular-file digest helper：

```text
input: direct Path
output: (byte_length, sha256)
chunk: <= 1 MiB
checks: lstat/open/fstat before + fstat/path lstat after
reject: symlink, junction/linklike, non-regular, device/inode/mode/size/mtime drift
```

把同一个 helper 接入 `_artifact_bindings` 与
`validate_real_scene_import_receipt` 的 artifact binding 循环。不得修改
`RealSceneImportReceipt` schema、artifact 排序、canonical bytes、metric alignment、
PLY semantic、chunk/manifest 或 production closure 语义。

```powershell
python -m pytest -q tests/test_real_scene_import_streaming.py
python -m pytest -q tests/test_real_scene_import.py tests/test_real_scene_import_streaming.py
python -m ruff check pipeline/real_scene_import.py tests/test_real_scene_import_streaming.py
git diff --check -- pipeline/real_scene_import.py tests/test_real_scene_import_streaming.py
git add -- pipeline/real_scene_import.py tests/test_real_scene_import_streaming.py
git commit --only pipeline/real_scene_import.py tests/test_real_scene_import_streaming.py -m "perf: stream real scene artifact digests"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

I1 push 后回报 H1/I1 证据并立即开始 J1；不要重新做审计或改
Viewer/Studio/release。

---

## J1：production result-bundle 流式校验与提取

**启动门：** 已满足。Codex G1 为 `92b76b5`；H1/I1 push 后立即开始。

**Files**

- Modify: `pipeline/remote_shell_executor.py`
- Create: `tests/test_remote_result_streaming.py`

### RED

```text
test_production_result_verifier_streams_large_ply_in_bounded_chunks
test_legacy_result_verifier_streams_large_ply_in_bounded_chunks
test_streaming_result_member_rejects_truncation
test_streaming_result_member_rejects_sha_or_size_mismatch
test_streaming_result_extraction_leaves_no_destination_on_failure
test_streaming_result_extraction_rejects_member_path_or_type_drift
```

fixture 使用 ZIP_STORED 且 PLY 大于 `8 MiB`；observer 必须证明单次成员读取
`<= 1 MiB`。不能用 `archive.read(member.path)` 读取大型成员，也不能把所有 payload
保存在 `dict[str, bytes]` 后再提取。

### GREEN

保留 canonical manifest、runtime/render JSON 与日志的有界内存解析；大型 PLY 等
artifact 在同一次 ZIP member stream 上完成 SHA/size 校验并写入 staging 文件。
校验完成前不发布 destination；失败删除 staging，destination 必须保持不存在。
v1/v2 schema、manifest bytes、closure SHA 依赖与 `VerifiedRemoteResultBundle` 的公开
信任语义不得放宽。

```powershell
python -m pytest -q tests/test_remote_result_streaming.py
python -m pytest -q tests/test_remote_shell_executor.py tests/test_remote_result_streaming.py tests/test_production_training_closure.py tests/test_real_scene_import.py
python -m ruff check pipeline/remote_shell_executor.py tests/test_remote_result_streaming.py
git diff --check -- pipeline/remote_shell_executor.py tests/test_remote_result_streaming.py
git add -- pipeline/remote_shell_executor.py tests/test_remote_result_streaming.py
git commit --only pipeline/remote_shell_executor.py tests/test_remote_result_streaming.py -m "perf: stream remote result bundle extraction"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

---

## K1：production v2 fetch 端到端 fake transport 矩阵

**Files**

- Modify only if RED requires: `pipeline/remote_shell_executor.py`
- Create: `tests/test_remote_result_fetch_v2.py`

### RED / acceptance

用真实 v2 ZIP、fake SCP 和 durable lifecycle/status fixture 覆盖：

```text
test_fetch_v2_materializes_exact_eight_import_contract_files
test_fetch_v2_derives_render_decision_and_closure_after_archive_verification
test_fetch_v2_rejects_cross_job_durable_job_ref_binding
test_fetch_v2_rejects_lifecycle_container_or_workspace_swap
test_fetch_v2_rejects_status_archive_sha_or_size_swap
test_fetch_v2_collision_or_sync_unknown_never_returns_succeeded
```

成功路径必须证明 result manifest、runtime 三件套、render policy/report/decision 与
production closure 全部来自同一 job/attempt/container/workspace；失败路径必须证明
destination 不存在、receipt 不前进为 succeeded、原始证据不被覆盖。

```powershell
python -m pytest -q tests/test_remote_result_fetch_v2.py
python -m pytest -q tests/test_remote_shell_executor.py tests/test_remote_result_streaming.py tests/test_remote_result_fetch_v2.py tests/test_remote_training_worker.py tests/test_production_training_closure.py tests/test_real_scene_import.py
python -m ruff check pipeline/remote_shell_executor.py tests/test_remote_result_fetch_v2.py
git diff --check -- pipeline/remote_shell_executor.py tests/test_remote_result_fetch_v2.py
git add -- pipeline/remote_shell_executor.py tests/test_remote_result_fetch_v2.py
git commit --only pipeline/remote_shell_executor.py tests/test_remote_result_fetch_v2.py -m "test: close production result fetch matrix"
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

## 每张工单回执格式

```text
ticket / commit SHA / changed paths
/ RED test + actual failure
/ GREEN passed + skipped
/ Ruff / diff-check
/ push and CI URL/status
/ next ticket already started or exact start gate
```

真实 GPU、正式素材、实测控制点和真实 Viewer QA 仍是外部门。上述工单只能关闭
repo-local 生产链路，不得据此声明 Production V1 或真实 3D 场景已经完成。
