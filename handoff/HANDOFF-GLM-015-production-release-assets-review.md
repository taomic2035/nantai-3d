# GLM Production 最终资产安全复核工单

> 当前唯一 GLM 入口。基线为 Codex `c858f37` 或更新的 `origin/main`。本工单不依赖
> 真实素材/GPU；目标是独立找出最终 Release 四件套的安全、跨平台和自包含缺口。
> H1–N1、`0f6dc99` caller 接入与旧 014 不得重新实现。

## 开始与协作边界

```powershell
git -c http.proxy=http://127.0.0.1:7890 fetch origin main
git status --short --branch
git log -1 --oneline
```

- 共享单一 `main` / worktree；禁止 reset、checkout、stash、rebase、`git add -A`
  和 `commit -a`。
- 若有 Codex WIP，保持只读；等工作树干净后才改相同路径。
- 先完成 R1/R2 并写
  `handoff/FEEDBACK-HANDOFF-GLM-015-production-release-assets-review.md`。
- 只有真实 P0/P1 且有 RED 时才修代码；没有问题就明确写 `APPROVED`，不为产生
  commit 而改文件。
- modeled test fixture 只能证明合同，不能称为真实 Production。

## R1：四件套 stage / download verifier 对抗审计

审计：

- `pipeline/production_release_assets.py`
- `scripts/stage_production_release_assets.py`
- `scripts/verify_production_release_assets.py`
- `tests/test_production_release_assets.py`
- `make.py`

逐项给出 `PASS / FINDING`、代码行和复现命令：

1. stage 只接受独立 verifier 判定为 `production-accepted` 且
   `fixture_kind is None` 的 archive；
2. stage 在同一复制字节上重跑 privacy audit，package content ID 必须一致；
3. archive 在复制前后、审计后都不能发生未检测 drift；
4. 输出目录 no-replace、durable，失败不遗留可误认的四件套；
5. 最终目录精确四个 regular non-link 文件，不含 policy/report/QA/private bytes；
6. 公开 archive 名由已验证 receipt version 推导，sidecar 使用最终文件名与真实 SHA；
7. standalone `PRODUCTION-RELEASE.json` / `SHA256SUMS.txt` 与 archive 内字节完全一致；
8. download verifier 拒绝 extra/missing、symlink/junction、文件名大小写或 Unicode
   ambiguity、mixed receipt/checksum、sidecar/ZIP drift 与 modeled fixture；
9. Windows、macOS、Linux 的路径、rename/fsync/no-replace 语义没有平台退化；
10. CLI 错误不输出 partial success JSON，也不回显 private policy 内容。

至少运行：

```powershell
python -m pytest -q tests/test_production_release_assets.py tests/test_production_release_privacy.py tests/test_production_release_verifier.py tests/test_make_runner.py
python -m ruff check pipeline/production_release_assets.py scripts/stage_production_release_assets.py scripts/verify_production_release_assets.py tests/test_production_release_assets.py make.py tests/test_make_runner.py
git diff --check
```

## R2：Production runtime 自包含审计

这是独立高价值问题，不要假设现状正确。`pipeline/production_release_builder.py`
会把 `make.py` 和全部 `pipeline/*.py` 放入 runtime，但脚本 allowlist 很窄。

回答并用 fresh modeled archive 证明：

1. runtime 内 `python make.py help` 展示的每个 Production target，所引用脚本是否
   确实随包存在；
2. 用户手册要求下载后执行的 verifier，是否能仅凭公开四件套与包内文件启动；
3. 若 build/audit/stage 属于“仓库维护命令而非 runtime 命令”，包内 `make.py`
   是否应隐藏/拒绝这些 target，而不是运行到缺脚本；
4. 如果选择扩充脚本 allowlist，会不会把 release builder、private policy 处理或
   其它不必要攻击面带入公开 runtime；
5. 给出最小、兼容 Preview 且不扩大公开白名单的建议。

R2 先只写 feedback，不直接改 allowlist。Codex review 结论后再实现，避免把维护工具
错误塞进正式包。

## R3：三平台 CI 与提交规则

检查 `c858f37` 或更新 exact `origin/main` 的 GitHub CI：

- Ubuntu/Windows Python 3.11/3.13 全量门；
- Ubuntu/Windows/macOS focused Production contract，必须包含
  `tests/test_production_release_assets.py`；
- Production content ID compare、Viewer runtime、remote drill、repro assets。

若发现代码漏洞：

1. 先提交可复现 RED；
2. 做最小修复；
3. 路径限定 `git add` / `git commit -- <paths>`；
4. 使用一次性代理 push：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

GLM 提交不写 Codex co-author。CI 绿色也不能提升真实 scene trust。
