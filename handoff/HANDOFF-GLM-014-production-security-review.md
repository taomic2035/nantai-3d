# GLM Production 安全复核工单

> 当前唯一 GLM 入口。目标是独立找漏洞和给出 exact-HEAD 证据，不重复实现已关闭的
> A1–N1，也不把 modeled fixture / 绿色测试描述为真实 Production。

## 开始条件

```powershell
git -c http.proxy=http://127.0.0.1:7890 fetch origin main
git status --short --branch
git log -1 --oneline
```

共享工作树禁止 reset、checkout、stash、rebase、`git add -A` 和 `commit -a`。
若看到 Codex WIP，保持只读并继续不冲突的复核。

## R1：Production 隐私审计器独立安全复核

审计 `86ab506` 或更新实现：

- `pipeline/production_release_privacy.py`
- `scripts/audit_production_release_privacy.py`
- `tests/test_production_release_privacy.py`
- `make.py`

逐项确认：

1. 未通过独立 Production verifier 的 tree/ZIP 不能开始扫描或产出通过报告；
2. private policy 是 canonical JSON，base64 也必须 canonical，needle 为
   8 bytes–1 MiB，policy 不进入公开 tree；
3. 每次读取不超过 1 MiB，跨 chunk binary needle 仍可发现；
4. PEM、明确 credential assignment 与私有 OS absolute path 可发现，但公开 schema
   字段、SHA 和 rights 摘要不误报；
5. symlink、非 regular、extra/missing、mid-read drift、pre/post identity drift 均
   fail closed；
6. public report 只含 category、公开相对路径、计数、package content ID 和
   `scene_trust_effect=none`，不回显 needle、policy path 或 workspace path；
7. report 使用 durable no-replace publication，且不能写回已验证 release tree；
8. clean/finding/合同错误的 CLI exit code 分别为 0/非零/非零。

```powershell
python -m pytest -q tests/test_production_release_privacy.py tests/test_production_release_verifier.py tests/test_make_runner.py
python -m ruff check pipeline/production_release_privacy.py scripts/audit_production_release_privacy.py tests/test_production_release_privacy.py make.py tests/test_make_runner.py
git diff --check
```

发现漏洞时先写真实 RED，再做最小修复并路径限定提交；没有 P0/P1 时只回报
`R1 APPROVED`，不要为了产生提交而改代码。

## R2：Portable release path identity 复核

审计 `e4a99cf` 或更新实现。确认 `archive → receipt/protected roots → builder
destinations → extracted tree verifier` 全部使用同一个 normalization + case-fold
identity，不能在某层退回裸 `.casefold()`。

至少验证这些等价对：

- `É.txt` 与 `e\u0301.txt`
- `Straße.txt` 与 `STRASSE.txt`
- 合法 NFC 中文/拉丁 UTF-8 单文件仍可通过

```powershell
rg -n "\.casefold\(\)" pipeline/release_archive.py pipeline/production_release_contract.py pipeline/production_release_builder.py pipeline/production_release_verifier.py
python -m pytest -q tests/test_release_archive.py tests/test_production_release_contract.py tests/test_production_release_verifier.py tests/test_production_release_builder.py
python -m ruff check pipeline/release_archive.py pipeline/production_release_contract.py pipeline/production_release_builder.py pipeline/production_release_verifier.py
git diff --check
```

`release_archive.py` 中共享 identity helper 自身的 `casefold()` 是预期结果；其他裸调用
必须说明为什么不会形成跨平台碰撞。处理方式同 R1。

## R3：Exact-HEAD CI 证据

等待不再有新 push 后，只检查 `origin/main` 精确 commit 的最新 CI：

- Ubuntu/Windows Python 3.11/3.13 全量测试；
- Ubuntu/Windows/macOS focused Production contract + privacy；
- Production content ID 三平台 compare；
- Viewer runtime、remote training drill 与 reproducible assets。

取消的旧 run 不是失败；只接受 exact current HEAD 的最终结论。若有失败，下载具体
job log、定位首个真实错误并提交 RED/修复；若全绿，回报 commit、run URL 和 job
结论。不得由 CI 绿推导真实素材、真实 CUDA、米制对齐或真实 Viewer QA 已完成。

## 提交与 push

只有发现并修复真实问题才提交。GLM 提交不写 Codex co-author；使用一次性代理：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

R1 → R2 → R3 连续执行，不等待新的口头分配。
