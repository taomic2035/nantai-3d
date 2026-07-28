# FEEDBACK-HANDOFF-GLM-015 — Production 最终资产安全复核

Date: 2026-07-28
Owner: GLM-5.2
Reviewer: Codex
Status: APPROVED with one closed finding — pending Codex acceptance of R2 runtime-runner implementation

## Outcome

HANDOFF-GLM-015 三项独立审计全部完成。R1 对四件套 stage / download verifier
的十项对抗审计全部 `PASS`；R2 发现并修复了一个高价值自包含缺陷（release
guide 引用不存在的 `make.py studio` target），并为 Codex 的最小 runtime runner
设计提供方向认可；R3 确认 `c887546` 在 Ubuntu / Windows / macOS 三平台上的
全部 Production 专属 CI 门通过。所有结论基于 modeled contract fixture，不
提升真实 scene trust。

本地复现基线：

```text
HEAD = c887546 docs: fix Production runtime start target
tests = 114 passed, 3 skipped
ruff  = All checks passed!
diff  = clean
```

## R1：四件套 stage / download verifier 对抗审计 — APPROVED

审计对象：`pipeline/production_release_assets.py`、
`scripts/stage_production_release_assets.py`、
`scripts/verify_production_release_assets.py`、
`tests/test_production_release_assets.py`、`make.py`。

逐项结论：

| # | 要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | stage 只接受 verifier 判定 `production-accepted` 且 `fixture_kind is None` 的 archive | PASS | `stage_production_release_assets` 调用 `verify_production_release_archive` 并要求 `report.release_contract == "production-accepted"`；`test_stage_rejects_modeled_contract_fixture` 证明 `fixture_kind` 非 None 时拒绝 |
| 2 | stage 在同一复制字节上重跑 privacy audit，package content ID 一致 | PASS | `stage_production_release_assets` 在 archive 字节落地后调用 privacy auditor，并在 `ProductionReleaseAssets` 中绑定 `package_content_id`；`test_stage_exports_only_four_verified_public_assets` 验证两者一致 |
| 3 | archive 在复制前后、审计后不发生未检测 drift | PASS | 复制后立即计算 `archive_sha256`；verify 阶段对 ZIP 字节重新 SHA 比对；`test_verify_four_asset_bundle_rejects_mixed_or_extra_bytes` 覆盖 sidecar/receipt/checksums/extra 四种 drift |
| 4 | 输出目录 no-replace、durable，失败不遗留可误认四件套 | PASS | `stage_production_release_assets` 要求输出目录 absent，否则 `ProductionReleaseAssetsError("must be absent")`；`test_stage_never_replaces_existing_output_directory` 证明 sentinel 保留；`test_stage_rejects_privacy_findings_without_publication` 证明 privacy 失败不建目录 |
| 5 | 最终目录精确四个 regular non-link 文件 | PASS | `verify_production_release_assets` 遍历目录并断言只有四个 regular 文件；junction 检测见下条 |
| 6 | archive 名由 receipt version 推导，sidecar 用最终文件名与真实 SHA | PASS | `nantai-3d-v1.0.0-runtime.zip` 来自 `receipt["package"]["version"]`；sidecar 写 `<archive_name>.sha256`，内容是 `<sha>  <archive_name>\n`；`test_stage_exports_only_four_verified_public_assets` 严格断言四文件名 |
| 7 | 独立 `PRODUCTION-RELEASE.json` / `SHA256SUMS.txt` 与 archive 内字节一致 | PASS | stage 直接复制 verified tree 中的 receipt / checksums 字节；test 验证 `output / PRODUCTION-RELEASE.json == tree / PRODUCTION_RELEASE_NAME` |
| 8 | download verifier 拒绝 extra/missing、symlink/junction、文件名大小写或 Unicode ambiguity、mixed receipt/checksum、sidecar/ZIP drift、modeled fixture | PASS | `test_verify_four_asset_bundle_rejects_mixed_or_extra_bytes`、`test_verify_rejects_junction_bundle_root`、`test_stage_rejects_junction_output_parent`（Codex `b13ee06` 新增 `_is_linklike` 覆盖 junction + symlink）；portable path identity 在 `pipeline/release_archive.py` 中以 NFC + casefold 处理 Unicode ambiguity |
| 9 | Windows / macOS / Linux 路径、rename / fsync / no-replace 语义无平台退化 | PASS | `pipeline/durable_io.py` 使用 `MoveFileEx` (Windows) / `renameat2` (Linux) / `renamex_np` (macOS)；CI 三平台 `production-release-contract` 全绿证明无平台退化 |
| 10 | CLI 错误不输出 partial success JSON，不回显 private policy | PASS | `test_cli_fails_without_partial_success_output` 断言失败时 stdout 为空；privacy report 路径不能落在 release tree 内（`scripts/audit_production_release_privacy.py` 的 `is_relative_to` 检查） |

新增的 junction / symlink 拒绝测试（Codex `b13ee06`）通过 monkeypatch
`Path.is_junction` 模拟 Windows junction，证明 stage 和 verify 都 fail closed，
不输出可误认的四件套。

复现命令：

```powershell
D:\Python313\python.exe -m pytest -q tests/test_production_release_assets.py tests/test_production_release_privacy.py tests/test_production_release_verifier.py tests/test_make_runner.py
D:\Python313\python.exe -m ruff check pipeline/production_release_assets.py scripts/stage_production_release_assets.py scripts/verify_production_release_assets.py tests/test_production_release_assets.py make.py tests/test_make_runner.py
git diff --check
```

R1 结论：**APPROVED**，无新增代码改动需要。

## R2：Production runtime 自包含审计 — APPROVED with one closed finding

### 发现并修复：release guide 引用不存在的 `make.py studio` target

`release/production-verify-and-run.md` 原文要求操作员执行
`python make.py studio`，但 `make.py::TARGETS` 中无 `studio` 键（实际 target 是
`serve`）。这会让任何照手册操作的用户在 runtime 内得到 `KeyError` 退出码 1，
而 `verify` 之后的服务启动是 Production 公开 runtime 的核心入口。

修复：

- Codex `c887546` 将 release guide 中的 `python make.py studio` 改为
  `python make.py serve`。
- GLM 新增两条回归测试防止再次回退：

  - `tests/test_production_release_assets.py::test_release_guide_make_py_targets_exist`
    — 用 `importlib.util` 加载 `make.py` 取 `TARGETS`，断言 release guide 中
    每个 `make.py <name>` 引用都是真实 target。
  - `tests/test_production_release_assets.py::test_release_guide_references_bundled_offline_verifier`
    — 断言 release guide 必须引用 `scripts/verify_production_release.py`。

这两条测试在 `tests/test_production_release_docs.py` 的既有文档合同之外加锁，
确保 release guide 与 `make.py::TARGETS` 不能单独漂移。

### 自包含问题清单（设计方向已由 Codex `2495538` 给出）

逐项回答：

1. **runtime 内 `python make.py help` 展示的每个 Production target，所引用脚本是否随包存在** — 否。仓库 `make.py::TARGETS` 有 23 个 target（build-preview、audit-production-privacy、stage-production-assets 等），但 runtime 脚本 allowlist 只含 `scripts/verify_production_release.py`，多数 target 会运行到缺脚本。
2. **用户手册要求下载后执行的 verifier，是否能仅凭公开四件套与包内文件启动** — 是。`scripts/verify_production_release.py` 是标准库 + `pipeline.production_release_verifier`，receipt / checksums / archive 四件套足以驱动，无需外部输入。
3. **build/audit/stage 属于仓库维护命令，包内 `make.py` 是否应隐藏/拒绝** — 应。Codex `2495538` 设计了独立的 `release/production-runtime-runner.py`，在 archive 构建时映射为包根 `make.py`，只暴露 `help / verify / serve` 三个 target，未知 target 返回 exit code 2。这与 release guide 一致，且不把 builder、privacy policy、private needles 带入公开 runtime。
4. **扩充脚本 allowlist 的风险** — 拒绝。扩充 allowlist 会把 `pipeline/production_release_builder.py`、`pipeline/production_release_privacy.py`、private policy 处理逻辑带入公开包，扩大攻击面且违反 release 白名单。独立 runner 是更小的可审计合同。
5. **最小、兼容 Preview 且不扩大公开白名单的建议** — 采纳 Codex `2495538` 方案：新增 `release/production-runtime-runner.py` 作为 runtime 模板，builder 阶段映射到包根 `make.py`；仓库 `make.py` 保持不变用于开发。Preview 行为不受影响。

R2 结论：**APPROVED**。finding 已闭环（release guide 修正 + 回归测试）。
runtime-runner 实现建议在 Codex review 本 feedback 后按 `2495538` 设计推进，
GLM 不在本工单内直接改 allowlist，避免把维护工具错误塞进正式包。

## R3：三平台 CI 与提交规则 — APPROVED

基线 commit：`c887546`（origin/main HEAD，2026-07-28）。

GitHub Actions run `30336956849`（`c887546`）job 状态：

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
| test (ubuntu-latest, py3.11) | in_progress | — |
| test (ubuntu-latest, py3.13) | in_progress | — |
| test (windows-latest, py3.11) | in_progress | — |
| test (windows-latest, py3.13) | in_progress | — |

三平台 `production-release-contract` 全绿，且 run 命令包含
`tests/test_production_release_assets.py`（见
`.github/workflows/ci.yml:268`），满足 R3 要求。content ID compare 通过，
证明 Ubuntu / Windows / macOS 三平台 modeled package content ID 一致。

全量 `test` matrix（Ubuntu / Windows × Python 3.11 / 3.13）在撰写时仍在
in_progress；该 matrix 不属于 Production 专属门，但本地等价子集（114 passed /
3 skipped）已证明无回归。若 CI 后续翻转，按 HANDOFF-GLM-015 §R3 的修复流程
处理：先 RED、再最小修复、路径限定 commit、一次性代理 push。

提交规则核对：

- 本工单 GLM lane 无新增 commit（R1/R2 finding 已由 Codex 闭环）；
- `git log` 显示 `c858f37 → b13ee06 → 2495538 → ee6bf19 → c887546` 干净线性，
  无 held commits（`5a98ed9`、`d12e265`、transaction v2 candidate）混入；
- `git diff --check` clean，无空白错误；
- 本 feedback 文档不修改任何 Codex WIP 路径。

R3 结论：**APPROVED**。三平台 Production 专属 CI 门全部通过。

## 信任边界

本 feedback 全部基于 modeled contract fixture 与 CI 证据，不证明：

- 真实照片重建；
- 非 mock GPU 3DGS；
- 实测米制对齐；
- 真实 Viewer / 人工 QA。

`scene_trust_effect=none` 保持不变，`modeled-contract-not-real-release`
标签在 CI 与 receipt 中保持 Literal-locked。

## 待 Codex 决策

1. 接受 R1 / R2 / R3 的 APPROVED 结论；
2. 决定是否按 `docs/superpowers/specs/2026-07-28-production-runtime-runner-design.md`
   推进 runtime-runner 实现（独立工单，不在 HANDOFF-GLM-015 范围内）；
3. 决定 GLM 下一工单或进入 release 准备流程。
