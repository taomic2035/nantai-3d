# GLM Production 收尾连续工单

> 唯一执行入口。逐项 TDD、小提交、立即用临时代理 push；不要重新做 gap audit，
> 不要回复“无事可做”。

## 基线与 review 回执

- 起点：`0f6dc99` 或更新的 `origin/main`。
- E1 已获 Codex 双重 review APPROVED；F1/G1 已由 `0d6c9e7`、
  `1727f8f`、`92b76b5` 关闭，不得重新开工。
- H1 `8f97936`、I1 `536b03e`：Codex review 未发现 P0/P1。
- J1 `75f9e0c` / K1 `6f16a0c`：原提交只测了 verifier，真实 `fetch()`
  未传 staging，仍会整体读取大型 PLY。Codex 已在 `0f6dc99` 接通 caller、
  文件化 provenance、发布前漂移复验并补回归；以该提交为新基线。

开始前：

```powershell
git -c http.proxy=http://127.0.0.1:7890 fetch origin main
git status --short --branch
git log -1 --oneline
```

共享工作树禁止 reset、checkout、stash、rebase、`git add -A` 和 `commit -a`。

## 连续顺序

```text
L1 大型 render/log fetch 边界
  → M1 跨平台 archive 路径硬化
  → N1 Production 隐私机器审计器
```

三项均不依赖真实素材、CUDA、secret 或 Blender。若当前项路径冲突，跳到下一项，
不得停工。

## L1：大型 render/log 的真实 fetch 边界

**Files**

- Create: `tests/test_remote_result_streaming_edges.py`
- Modify only after real RED: `pipeline/remote_shell_executor.py`

**RED**

1. `test_fetch_v2_streams_large_render_without_archive_read`
2. `test_fetch_v2_large_log_preserves_semantic_validation`
3. `test_fetch_v2_rejects_streamed_render_drift_before_publication`
4. `test_fetch_v2_stream_failure_removes_staging_and_destination`

使用真实 v2 ZIP + fake transport。render fixture > 2 MiB，证明单次读取
`<= 1 MiB` 且不走 `ZipFile.read()`；log fixture > 2 MiB，仍须完成 log
provenance，不能因从 `member_bytes` 消失而 `KeyError`。校验后篡改 render 必须
fail closed，destination 与 staging 均不存在。不得放宽 SHA/size、job/attempt、
container/workspace 或 render evaluation 绑定。

```powershell
python -m pytest -q tests/test_remote_result_streaming_edges.py tests/test_remote_result_streaming.py tests/test_remote_result_fetch_v2.py
python -m ruff check pipeline/remote_shell_executor.py tests/test_remote_result_streaming_edges.py
git diff --check
```

## M1：跨平台 archive 路径硬化

**Files**

- Modify: `pipeline/release_archive.py`
- Modify: `tests/test_release_archive.py`
- Modify only if integration RED requires:
  `pipeline/production_release_verifier.py`,
  `tests/test_production_release_verifier.py`

**RED**

对每个 path component 拒绝：

- Windows device names：`CON`、`PRN`、`AUX`、`NUL`、`COM1`、`LPT1`
  及带扩展名形式；
- 末尾空格/点、冒号、NUL/control character、反斜杠；
- NFC/NFD 等价碰撞和 Unicode casefold 碰撞；
- wrapper root 正常但子路径非法的 ZIP。

必须在创建目标文件前拒绝；失败后 extraction destination 不存在。保留合法 UTF-8
文件名能力，不用“仅 ASCII”规避问题。共享 predicate 同时供 ZIP inspection 与
Production extraction 使用。

```powershell
python -m pytest -q tests/test_release_archive.py tests/test_production_release_verifier.py
python -m ruff check pipeline/release_archive.py pipeline/production_release_verifier.py tests/test_release_archive.py tests/test_production_release_verifier.py
git diff --check
```

## N1：Production 隐私机器审计器

**Files**

- Create: `pipeline/production_release_privacy.py`
- Create: `scripts/audit_production_release_privacy.py`
- Create: `tests/test_production_release_privacy.py`
- Modify: `make.py`
- Modify: `tests/test_make_runner.py`

**合同**

1. 先调用独立 Production verifier；未验证的 tree/archive 不扫描为“通过”。
2. 支持私有 canonical JSON needle policy；needle 至少 8 bytes，policy 自身永不
   复制进 public tree。
3. 内置检测 PEM/private-key marker、Windows/POSIX 绝对路径和明确 credential
   marker；不把正常 SHA、rights receipt 摘要或公开 schema 字段误报为秘密。
4. 所有文件以 `<= 1 MiB` 分块扫描，必须能发现跨 chunk 边界的 needle。
5. 报告只含 finding category、公开相对路径、计数和 package content ID；禁止回显
   匹配到的秘密或私有绝对路径。
6. symlink、非 regular、读取漂移、额外文件、policy 非 canonical、任何 finding
   都 fail closed。该报告不提升 scene trust。
7. `make.py audit-production-privacy ...` 提供稳定 argv；成功为 0，finding/合同错误
   为非零。

**RED**

至少覆盖 clean modeled tree、跨 chunk secret、绝对路径、PEM、binary payload、
symlink、mid-read drift、noncanonical policy、输出不泄密、CLI exit code。

```powershell
python -m pytest -q tests/test_production_release_privacy.py tests/test_make_runner.py
python -m ruff check pipeline/production_release_privacy.py scripts/audit_production_release_privacy.py tests/test_production_release_privacy.py make.py tests/test_make_runner.py
git diff --check
```

## 每项提交与回执

每项独立路径限定提交并 push；GLM 提交不写 Codex co-author。回执必须包含：

```text
ticket / commit / exact paths
/ first RED and actual failure
/ GREEN passed/skipped
/ Ruff + diff-check
/ push status and next ticket already started
```

这些工单只关闭 repo-local 发布安全门。真实重叠采集、accepted real-photo SfM、
非 mock GPU 3DGS、实测米制对齐和真实 Viewer QA 缺一项，仍保持 Preview。
