# GLM-020 Release Read Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to execute this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 连续关闭 Production Release 只读验证路径中剩余的
check-then-open、句柄漂移、reparse/junction 与错误信息泄漏边界；完成一项后自主进入
下一项，不等待 Codex 再次分配。

**Architecture:** 所有安全结论都从已经打开的文件描述符及其前后 `fstat` 推导，
路径级 `lstat` 只用于打开前约束和读取后的命名空间漂移检测，不能再次按名称读取内容。
每个改动先写 RED fault-injection test，再做最小实现；异常只使用固定相对 label，
不得回显绝对路径、系统错误文本或私有目录。

**Tech Stack:** Python 3.11/3.13、`os.open`、`os.fdopen`、`os.fstat`、
`O_NOFOLLOW`/`O_BINARY`、pytest、Ruff、Windows junction/reparse 合同。

---

## 执行纪律

- 当前是 GLM 辅助 lane，不得触碰 Codex 正在推进的 CUDA/发布证据主线：
  `.github/workflows/production-cuda-image.yml`、
  `containers/production-cuda/**`、`cloud/probe_production_cuda_image.py`、
  `README.md`、`docs/**`、`tests/test_real_golden_path_docs.py`。
- 允许修改的生产代码仅为：
  `pipeline/release_archive.py`、`pipeline/production_release_assets.py`。
- 允许修改的测试仅为：
  `tests/test_release_archive.py`、`tests/test_production_release_assets.py`；
  只有最终回归证明需要时才可对
  `tests/test_production_release_verifier.py` 增加调用方测试，不能改 verifier 生产代码。
- 每项单独 TDD、单独路径限定提交；禁止 `git add -A`、`git commit -a`、
  stash、reset、rebase、清理他人文件。
- 每次开始前运行 `git status --short`。若允许路径出现非本任务改动，跳过该项并继续
  后面不冲突的项；不要停下来等待。
- 不自行 push。连续完成本计划所有可执行项后一次性通知 Codex review。
- 测试绿只证明 repo-local 合同；不得声明真实 GPU、真实 3DGS、真实素材、米制对齐
  或 Production V1 已完成。

## Task 1 — `stable_regular_file_digest` 单句柄闭包

**Files**

- Modify: `pipeline/release_archive.py`
- Test: `tests/test_release_archive.py`

- [ ] 新增 RED 测试
  `test_stable_regular_file_digest_never_uses_path_open`：monkeypatch
  `Path.open`，目标文件若被按名称打开就失败；正常摘要仍必须成功。
- [ ] 新增 RED 测试
  `test_stable_regular_file_digest_rejects_open_handle_identity_drift`：
  monkeypatch 第二次 `os.fstat` 的 size/mtime/inode 任一字段，断言稳定返回
  `ReleaseArchiveError("release file changed during read")`。
- [ ] 新增 RED 测试
  `test_stable_regular_file_digest_oserror_does_not_leak_absolute_path`：
  `os.open` 抛带私有绝对路径的 `OSError`，最终消息只能是固定
  `release file cannot be read`。
- [ ] 运行：
  `python -m pytest -q tests/test_release_archive.py -k stable_regular_file_digest`
  并确认新测试先失败。
- [ ] 最小实现：使用
  `os.O_RDONLY | getattr(os, "O_BINARY", 0) |
  getattr(os, "O_NOFOLLOW", 0)` 打开一次；`os.fdopen(..., "rb")` 接管关闭；
  打开后立即校验 regular/non-link 与初始 signature，流式摘要后再次 `fstat`；
  保留读取后的 `lstat` 仅用于命名空间漂移检测，不允许再次读取内容。
- [ ] 运行专项测试与
  `python -m ruff check pipeline/release_archive.py tests/test_release_archive.py`。
- [ ] 路径限定提交，message：
  `fix(security): bind release digest reads to one handle`。

## Task 2 — 双文件比较的两个独立受控句柄

**Files**

- Modify: `pipeline/production_release_assets.py`
- Test: `tests/test_production_release_assets.py`

- [ ] 新增 RED 测试
  `test_acceptance_byte_comparison_never_uses_path_open`，同时禁止 left/right
  `Path.open`，相同文件内容仍返回相等。
- [ ] 新增 RED 测试
  `test_acceptance_byte_comparison_closes_left_if_right_open_fails`：记录
  `os.close`/包装 stream 的关闭状态，第二个 `os.open` 失败时第一个 fd 必须关闭。
- [ ] 新增 RED 参数化测试，对 left/right 的 descriptor-before、
  descriptor-after 分别注入 identity drift，全部 fail closed。
- [ ] 运行：
  `python -m pytest -q tests/test_production_release_assets.py -k "comparison or equal"`，
  确认新测试先失败。
- [ ] 最小实现：left/right 各 `os.open` 一次，按获取顺序建立 `fdopen`，任一中间
  失败均关闭已持有 fd；比较循环保持 1 MiB 分块，不把文件整体读入内存；两侧分别
  校验初始 path stat、descriptor-before/after 与 path-after。
- [ ] 运行专项、Ruff、`git diff --check`。
- [ ] 路径限定提交，message：
  `fix(security): bind release asset comparison handles`。

## Task 3 — standalone contract 与 archive contract 单句柄读取

**Files**

- Modify: `pipeline/production_release_assets.py`
- Test: `tests/test_production_release_assets.py`

- [ ] 新增 RED 测试
  `test_stable_contract_bytes_never_uses_path_open`；同时覆盖 byte cap、
  descriptor drift、读取后 namespace drift。
- [ ] 新增 RED 测试
  `test_archive_contract_payloads_never_reopens_archive_by_name`；禁止
  `Path.open` 后仍应从同一 fd 完成 central-directory preflight 与两个 contract
  member 的 bounded read。
- [ ] 新增 RED 测试：`os.open`/`os.fdopen`/`os.fstat`/ZIP 读取任一抛带绝对路径的
  `OSError` 时，顶层消息固定为
  `Production public contract cannot be read` 或
  `Production archive contracts cannot be read`，不得包含异常文本。
- [ ] 运行：
  `python -m pytest -q tests/test_production_release_assets.py -k "contract_bytes or archive_contract"`，
  确认新测试先失败。
- [ ] 最小实现：两个 path-based helper 都改为 `os.open` + `os.fdopen`；
  `_archive_contract_payloads_stream` 保持纯 stream API，不改变 ZIP schema、
  member 名或 16 MiB cap；descriptor-before/after 必须来自同一 fd。
- [ ] 运行专项、Ruff、`git diff --check`。
- [ ] 路径限定提交，message：
  `fix(security): bind release contract reads to descriptors`。

## Task 4 — 四件套目录枚举的 root identity 闭包

**Files**

- Modify: `pipeline/production_release_assets.py`
- Test: `tests/test_production_release_assets.py`

- [ ] 新增 RED 测试
  `test_public_bundle_scan_rejects_root_identity_swap_after_scandir`：
  `os.scandir` 建立后让 root 的 inode/reparse 状态漂移，必须拒绝。
- [ ] 新增 RED 测试
  `test_public_bundle_scan_rejects_entry_reparse_without_following`：
  模拟 Windows `FILE_ATTRIBUTE_REPARSE_POINT`，不得把 entry 当 regular file。
- [ ] 新增 RED 测试
  `test_public_bundle_scan_closes_iterator_on_iteration_error`：
  中途 `OSError` 后 iterator 必须关闭，错误消息不能含绝对路径。
- [ ] 运行：
  `python -m pytest -q tests/test_production_release_assets.py -k "public_bundle or scan"`，
  确认新测试先失败。
- [ ] 最小实现：用一个受控 `os.scandir(root)` iterator 取代 `Path.iterdir()`；
  对每个 `DirEntry.stat(follow_symlinks=False)` 做 regular/reparse 检查；iterator
  建立后和关闭前重新验证 root signature。不得递归，不得扩大四件套白名单。
- [ ] 运行专项、Ruff、`git diff --check`。
- [ ] 路径限定提交，message：
  `fix(security): close release bundle scan identity drift`。

## Task 5 — 完整 verifier 调用链与错误隐私矩阵

**Files**

- Test: `tests/test_production_release_assets.py`
- Optional test-only: `tests/test_production_release_verifier.py`

- [ ] 加一组端到端 fault-injection tests，分别在 archive digest、sidecar、
  standalone receipt、standalone checksums、archive contracts 与 second pass
  注入一次 OSError/identity drift；断言全部拒绝、无绝对路径/secret 回显。
- [ ] 加 64 MiB 稀疏或流式 fixture，记录单次 read 不超过
  `_COPY_CHUNK_BYTES`；不得使用 `read_bytes()` 或无上限 `read()` 处理 archive。
- [ ] 验证同一四件套目录在验证中发生文件替换、删除、size/mtime 漂移时均拒绝，
  且不会删除或覆盖任何输入。
- [ ] 只在测试证明真实代码缺口时，回到 Task 1–4 的两个允许生产文件做最小修复；
  不新建 schema，不改变 receipt/checksum bytes。
- [ ] 运行：
  `python -m pytest -q tests/test_release_archive.py tests/test_production_release_assets.py tests/test_production_release_verifier.py`。
- [ ] 运行：
  `python -m ruff check pipeline/release_archive.py pipeline/production_release_assets.py tests/test_release_archive.py tests/test_production_release_assets.py tests/test_production_release_verifier.py`。
- [ ] 若有生产修复，路径限定提交，message：
  `test(security): complete release read boundary matrix`；
  若没有新缺口，只保留测试提交，不制造无意义代码改动。

## Task 6 — 连续执行收尾

- [ ] 运行 `git status --short`，确认没有暂存或提交 Codex WIP。
- [ ] 运行上述三文件 pytest、Ruff 与
  `git diff --check HEAD~5..HEAD`（实际提交数少于 5 时用本队列首提交）。
- [ ] 用 `git log --oneline` 列出本队列每个小提交及其专项结果。
- [ ] 最终一次性报告：完成项、跳过项及冲突原因、提交 SHA、测试计数、剩余真实外部
  门。不要在每完成一个 Task 后停下来索要新工单。
- [ ] 结束后等待 Codex review；不得自行把 repo-local 安全修复描述为 Production V1。
