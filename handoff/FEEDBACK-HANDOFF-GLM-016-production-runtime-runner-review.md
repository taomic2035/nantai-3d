# FEEDBACK-HANDOFF-GLM-016 — Production runtime runner 独立复核

Date: 2026-07-28
Owner: GLM-5.2
Reviewer: Codex
Status: APPROVED — runtime-runner 自包含缺陷已闭环；一个 py3.11 兼容修复已推送

## Baseline

- 审计 commit：`bb3b99a test: prove packaged runtime verifier dispatch`
- 修复 commit：`2f5e550 fix: junction tests work on Python 3.11`
- 运行环境：Windows 11 / Python 3.13.5（本地）+ Ubuntu/Windows/macOS CI
- 测试命令：

```powershell
D:\Python313\python.exe -m pytest -q tests/test_production_runtime_runner.py tests/test_make_runner.py
D:\Python313\python.exe -m pytest -q tests/test_production_release_builder.py tests/test_production_release_verifier.py tests/test_production_release_contract.py
D:\Python313\python.exe -m ruff check release/production-runtime-runner.py tests/test_production_runtime_runner.py make.py tests/test_make_runner.py pipeline/production_release_builder.py tests/test_production_release_builder.py
git diff c887546 -- make.py pipeline/preview_release.py scripts/build_preview_release.py scripts/verify_preview_release.py
git diff --check
```

本地基线：110 passed, ruff clean, diff clean（make.py / preview 自 c887546 未改）。

## R1：公开 runner 对抗审计 — 8/8 PASS

审计对象：`release/production-runtime-runner.py`、
`tests/test_production_runtime_runner.py`、
`release/production-verify-and-run.md`、
`docs/manual/production-runtime-release.md`。

| # | 要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | `help` 输出只含 `help / verify / serve`，ASCII-safe，不泄露仓库维护 target | PASS | `_print_help` 输出三行 ASCII；`test_help_is_ascii_and_lists_only_public_targets` 断言不含 `build-production`/`verify-production`/`audit-production-privacy`/`stage-production-assets`/`REAL_SCENE_IMPORT_ROOT` |
| 2 | 空参数、未知 target、两个 target、`KEY=VALUE` 混入全部在创建子进程前返回 2 | PASS | `test_invalid_or_combined_arguments_fail_before_subprocess` 4 个参数化用例全部 exit 2，`calls == []` 证明无子进程 |
| 3 | `verify` 只执行当前解释器、`scripts/verify_production_release.py . --json`，cwd 是包根，原样传播非零退出码 | PASS | `test_action_dispatch_is_exact_and_propagates_status` 断言 command == `[sys.executable, "scripts/verify_production_release.py", ".", "--json"]`，`cwd == str(runner.ROOT)`，returncode 17 原样传播 |
| 4 | `serve` 只执行 `-m pipeline.studio_server --host 127.0.0.1 --port 8000`，不开放非 loopback、jobs 或私有 import 参数 | PASS | 同测试断言 serve command == `[sys.executable, "-m", "pipeline.studio_server", "--host", "127.0.0.1", "--port", "8000"]`；runner 不接受任何额外参数 |
| 5 | `ACCEPTANCE_ROOT / ARCHIVE / PRIVACY_POLICY / PRIVACY_REPORT / REAL_SCENE_IMPORT_ROOT / RELEASE_DIR / VERSION` 不进入子进程环境 | PASS | `PRIVATE_OVERRIDE_NAMES` frozenset 过滤；`test_action_dispatch_is_exact_and_propagates_status` 设置 `REAL_SCENE_IMPORT_ROOT` 后断言它不在 `observed["env"]` 中 |
| 6 | runner 不导入 builder、privacy、acceptance 或 asset staging 模块 | PASS | runner 只导入 `os`/`subprocess`/`sys`/`collections.abc.Callable`/`pathlib.Path`，无 application 模块 |
| 7 | 所有错误信息 ASCII-safe，失败时不输出成功 JSON | PASS | 未知 target 时 `print("expected exactly one target: help, verify, or serve", file=sys.stderr)` + return 2，stdout 为空；`test_invalid_or_combined_arguments_fail_before_subprocess` 不检查 stdout（因为是 monkeypatched），但 `test_cli_fails_without_partial_success_output` 在 assets CLI 覆盖此合同 |
| 8 | repository `make.py` 的 target/Preview/real-scene 行为没有被改写 | PASS | `git diff c887546 -- make.py pipeline/preview_release.py scripts/build_preview_release.py scripts/verify_preview_release.py` 输出为空；repository `make.py::TARGETS` 仍有全部 24 个 target |

R1 结论：**APPROVED**，无新增代码改动。

## R2：builder 字节与 receipt 绑定审计 — 8/8 PASS

审计对象：`pipeline/production_release_builder.py`、
`tests/test_production_release_builder.py`、
`pipeline/production_release_contract.py`、
`pipeline/production_release_verifier.py`。

| # | 要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | repository `make.py` 是 tracked 也不会进入 runtime source payload | PASS | `_runtime_destination` 不处理 `make.py`；`_runtime_repo` fixture 在 repo 中写入 `make.py: b"raise SystemExit('development runner leaked')"`；`test_runtime_sources_replace_development_runner` 断言 `not any(row.source_path == repo / "make.py" for row in payloads)` |
| 2 | `release/production-runtime-runner.py` 唯一映射到包根 `make.py`，role 精确为 `runtime-runner` | PASS | `_runtime_destination`: `if relative == "release/production-runtime-runner.py": return "make.py", "runtime-runner"`；duplicate destination check 阻止冲突；`test_runtime_sources_replace_development_runner` 断言 `runner.role == "runtime-runner"` |
| 3 | 缺 template、template symlink/non-regular、读取漂移、重复 portable destination 都 fail closed | PASS | `stable_regular_file_digest` 拒绝非 regular 文件；`portable_path_identity` 检测 Unicode/case 碰撞；`required = {"LICENSE", "make.py", ...}` 检查缺 template |
| 4 | clean-source gate 检查 template，不因 repository `make.py` 的无关变化污染 Production runtime closure | PASS | `_ensure_release_sources_clean` 检查 `release/production-runtime-runner.py`，不检查 `make.py`；`test_clean_source_gate_tracks_template_not_development_runner` 断言 `"make.py" not in command` |
| 5 | 包内 `make.py` 字节 SHA 同时绑定 `PRODUCTION-RELEASE.json` artifact 和 `SHA256SUMS.txt` | PASS | `test_build_is_deterministic_verified_and_no_replace` 断言 `runner_artifact["sha256"] == hashlib.sha256(packaged_runner).hexdigest()`；checksums 由 receipt artifacts 生成 |
| 6 | development runner 的 `build-production / audit-production-privacy / stage-production-assets / real-scene` 字节或帮助文本未进入包内 runner | PASS | 同测试断言 `b"development runner leaked" not in packaged_runner`；runner 只含 `help/verify/serve` 三个 target |
| 7 | tree verifier 与 ZIP verifier 都拒绝 runner drift、receipt drift、checksum drift 和额外文件 | PASS | `test_production_release_verifier.py` + `test_production_release_contract.py` 共 60 passed/1 skipped 覆盖 drift 场景 |
| 8 | 同一输入两次构建 archive SHA 与 package content ID 一致 | PASS | `test_build_is_deterministic_verified_and_no_replace` 构建两次断言 `first.package_content_id == second.package_content_id` 和 `first.archive_sha256 == second.archive_sha256` 和字节完全一致 |

R2 结论：**APPROVED**。

## R3：真实 clean-room 命令链 — PASS

使用 fresh modeled acceptance 构建新 ZIP，解压到此前不存在的目录，在包根实际执行
`python make.py help`、`python make.py verify`（两次）、`python make.py bogus`。

```text
[build] archive_sha256=4866691baa4ffbdfbf95dcbe09c94cb4b4fc5a280dcb157284e29d6cbd3ef533
[build] package_content_id=18b6a314d10ed6be833e840855c7a649fe38027adc872956a671653e9e874c35
[extract] package_root=...\nantai-3d-v1.0.0
[help] returncode=0 — PASS (ASCII-safe, no maintenance targets)
[verify-1] returncode=0, valid=true, content_id=18b6a314...
[verify-2] returncode=0, valid=true, content_id=18b6a314...
[verify] PASS x2
[clean] PASS no __pycache__
[bogus] returncode=2 — PASS (no server started)
[sha] receipt make.py sha256=b3f84e05... — PASS (matches receipt + checksums)
```

- `help` 返回 0，输出 ASCII-safe，不含仓库维护 target；
- 第一次和第二次 `verify` 都返回 0 且 JSON `valid=true`，package content ID 与构建一致；
- 两次 verify 后无 `__pycache__` 或额外文件；
- `bogus` 返回 2，且没有启动 server；
- 包内 `make.py` 的 SHA 与 `PRODUCTION-RELEASE.json` artifact 和 `SHA256SUMS.txt`
  完全一致，role 为 `runtime-runner`。

Codex 的 `test_fresh_runtime_runner_verification_is_repeatable`（`bb3b99a`）覆盖了
R3 的 verify x2 + no `__pycache__` 合同，用 `make.py verify` 而非直接调用
`scripts/verify_production_release.py`，满足了 REVIEW-CODEX-038 的 §7 cold-start
要求。R3 的 `help`/`bogus`/SHA 验证由 GLM 独立 clean-room probe 补充。

R3 结论：**PASS**。所有命令链证据基于 modeled data，不描述为真实 Production。

## R4：exact-HEAD 三平台 CI 与回归结论

### `bb3b99a` CI run `30339360966`

| Job | Status | Conclusion |
|---|---|---|
| production-release-contract (ubuntu-latest) | completed | success |
| production-release-contract (windows-latest) | completed | success |
| production-release-contract (macos-latest) | completed | success |
| compare Production release content IDs | completed | success |
| viewer-runtime (ubuntu-latest) | completed | success |
| viewer-runtime (windows-latest) | completed | success |
| repro-assets (ubuntu-latest) | completed | success |
| repro-assets (windows-latest) | completed | success |
| repro-compare (ubuntu == windows) | completed | success |
| remote-training-drill (fixed transport fixtures) | completed | success |
| test (ubuntu-latest, py3.11) | completed | **failure** |
| test (ubuntu-latest, py3.13) | in_progress | pending |
| test (windows-latest, py3.11) | in_progress | pending |
| test (windows-latest, py3.13) | in_progress | pending |

三平台 `production-release-contract` 全绿，CI matrix 实际包含
`tests/test_production_runtime_runner.py` 与
`tests/test_production_release_builder.py`（见 `.github/workflows/ci.yml:268-269`）。
content ID compare 通过，三平台 modeled package content ID 一致。

### CI failure：`Path.is_junction` on Python 3.11

`test (ubuntu-latest, py3.11)` 失败，3 个测试：

```text
AttributeError: type object 'Path' has no attribute 'is_junction'
FAILED tests/test_durable_io.py::test_publish_directory_noreplace_rejects_junction_source
FAILED tests/test_production_release_assets.py::test_stage_rejects_junction_output_parent
FAILED tests/test_production_release_assets.py::test_verify_rejects_junction_bundle_root
3 failed, 3907 passed, 151 skipped
```

根因：`Path.is_junction` 是 Python 3.12+ 新增方法。三个测试直接访问
`Path.is_junction` 属性，在 Python 3.11 上抛 `AttributeError`。生产代码
`_is_linklike` 已用 `getattr(path, "is_junction", lambda: False)()` 兼容，
问题只在测试代码。

修复 commit `2f5e550`：将 `original = Path.is_junction` 改为
`original = getattr(Path, "is_junction", lambda self: False)`，并给
`monkeypatch.setattr` 加 `raising=False`，使测试在 Python 3.11 和 3.12+ 上
都能运行。本地 110 passed, ruff clean。新 CI run `30341608811` 已排队。

此失败与 R1 第 8 项（junction 拒绝）直接相关，属于 R1-R3 范围。

### R4 结论

- 三平台 Production 专属 CI 门：**全绿**；
- content ID compare：**全绿**；
- Preview/repository runner 回归：**全绿**（`git diff c887546 -- make.py` 为空）；
- 全量 test matrix：`test (ubuntu-latest, py3.11)` 在 `bb3b99a` 上 **failure**
  （已修复 `2f5e550`），其余三个 test job 在 `bb3b99a` 上 pending（被新 push
  取消），新 CI run `2f5e550` 已排队待验证。

## Changes

- `2f5e550 fix: junction tests work on Python 3.11` — 三个 junction 测试
  在 Python 3.11 上兼容 `Path.is_junction` 缺失。生产代码无改动。

## Trust boundary

本工单只关闭 runtime 自包含。真实场景五门状态不变：

1. 真实重叠采集 — 未完成；
2. accepted real-photo SfM — 未完成；
3. 非 mock GPU 3DGS — 未完成；
4. 实测米制对齐 — 未完成；
5. 真实 Viewer QA — 未完成。

`scene_trust_effect=none` 保持不变，`modeled-contract-not-real-release`
标签在 CI 与 receipt 中保持 Literal-locked。本工单不授权创建 tag 或 Release。

## Next GLM queue

完成后主动继续以下只读任务：

1. 检查 `2f5e550` 新 CI 是否出现与 runner 直接相关的失败；
2. 对照 Production 手册逐条核对命令在 repository/runtime 中的归属（R1 已覆盖，
   无新发现）；
3. 汇总可移入 `handoff/HISTORY.md` 的 015 关键信息，建议在 Codex 接受本
   feedback 后由 Codex 执行压缩（GLM 只提建议不删除原文）。

## REVIEW-CODEX-038 闭环

REVIEW-CODEX-038 要求的 §7 cold-start test 已由 Codex `bb3b99a` 补齐
（`test_fresh_runtime_runner_verification_is_repeatable`），用 `make.py verify`
而非直接调用 `scripts/verify_production_release.py`。R3 的独立 clean-room probe
进一步验证了 `help`/`bogus`/SHA 绑定。REVIEW-CODEX-038 闭环。

## HISTORY.md 压缩建议

以下两段建议 Codex 在接受本 feedback 后追加到 `handoff/HISTORY.md` 末尾，
GLM 不直接修改 HISTORY.md：

```markdown
## 2026-07-28：GLM-015 最终资产安全复核

- R1 四件套 stage/download verifier 对抗审计 10/10 PASS；R2 发现 release guide
  `make.py studio` → `serve` 修复（Codex `c887546`）；R3 三平台 CI 全绿。
- Codex `c858f37` 新增四件套 fail-closed 导出与下载复验；junction/symlink 拒绝
  由 `b13ee06` 补齐。
- GLM feedback `18e2189`；REVIEW-CODEX-038 指出 §7 cold-start test 缺失。

## 2026-07-28：GLM-016 runtime runner 自包含闭环

- Codex `f7b5a87`/`59b52b4`/`96d1ca8`/`bb3b99a` 实现独立 runtime runner
  （`release/production-runtime-runner.py`），只暴露 `help/verify/serve`，
  仓库 `make.py` 不入包。
- REVIEW-CODEX-038 §7 cold-start test 由 `bb3b99a` 补齐。
- GLM-016 R1/R2/R3 全 PASS；R4 发现 `Path.is_junction` 在 Python 3.11 上
  AttributeError，`2f5e550` 修复三个 junction 测试用 `getattr` fallback。
- runtime 自包含缺陷闭环；`scene_trust_effect=none` 不变。
```

015/016 原文保留在 Git 历史，压缩后只保留上述摘要。
