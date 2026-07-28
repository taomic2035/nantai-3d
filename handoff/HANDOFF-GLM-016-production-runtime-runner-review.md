# GLM Production runtime runner 独立复核工单

> 当前唯一 GLM 入口。以最新 `origin/main` 为基线，最低应包含 Codex
> `f7b5a87`、`59b52b4`、`96d1ca8`。本工单不依赖真实素材、GPU、Blender 或
> Codex 后续输入；四个审计项按顺序连续执行，不要完成一项后停下等待新工单。

## 开始、分工与 Git

```powershell
git -c http.proxy=http://127.0.0.1:7890 fetch origin main
git status --short --branch
git log -6 --oneline
```

- 单一 `main` / worktree；禁止 reset、checkout、stash、rebase、`git add -A`
  和 `commit -a`。
- Codex 正在跑全仓慢测诊断，不修改 runner/builder/docs 文件；若工作树出现其它
  WIP，只读审计，绝不覆盖。
- 先审计、先复现。没有 RED 就不改代码，不为产生 commit 而重排格式。
- modeled fixture 只能证明公开合同，不能描述为真实 Production 或真实 3D。
- 交付统一写入
  `handoff/FEEDBACK-HANDOFF-GLM-016-production-runtime-runner-review.md`。
- 若只有 review 文档，路径限定提交该 feedback；若有真实漏洞，测试 RED、最小修复
  和 feedback 可拆成两个小提交。GLM 提交不写 Codex co-author。
- 每个绿色提交立即用一次性代理推送：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

## R1：公开 runner 对抗审计

审计：

- `release/production-runtime-runner.py`
- `tests/test_production_runtime_runner.py`
- `release/production-verify-and-run.md`
- `docs/manual/production-runtime-release.md`

逐项给出 `PASS / FINDING`、代码行和实际命令输出：

1. `help` 输出只含 `help / verify / serve`，ASCII-safe，不泄露仓库维护 target；
2. 空参数、未知 target、两个 target、`KEY=VALUE` 混入全部在创建子进程前返回 2；
3. `verify` 只执行当前解释器、
   `scripts/verify_production_release.py . --json`，cwd 是包根，原样传播非零退出码；
4. `serve` 只执行当前解释器、`-m pipeline.studio_server --host 127.0.0.1
   --port 8000`，不开放非 loopback、jobs 或私有 import 参数；
5. `ACCEPTANCE_ROOT / ARCHIVE / PRIVACY_POLICY / PRIVACY_REPORT /
   REAL_SCENE_IMPORT_ROOT / RELEASE_DIR / VERSION` 不进入子进程环境；
6. runner 不导入 builder、privacy、acceptance 或 asset staging 模块；
7. 所有错误信息 ASCII-safe，失败时不输出成功 JSON；
8. repository `make.py` 的 target/Preview/real-scene 行为没有被改写。

至少运行：

```powershell
python -m pytest -q tests/test_production_runtime_runner.py tests/test_make_runner.py
python -m ruff check release/production-runtime-runner.py tests/test_production_runtime_runner.py make.py tests/test_make_runner.py
git diff c887546 -- make.py pipeline/preview_release.py scripts/build_preview_release.py scripts/verify_preview_release.py
```

若发现环境变量大小写、组合参数或子进程启动失败存在真实 fail-open，先新增最小
RED，再修一个根因；不要扩充 public target。

## R2：builder 字节与 receipt 绑定审计

审计：

- `pipeline/production_release_builder.py`
- `tests/test_production_release_builder.py`
- `pipeline/production_release_contract.py`
- `pipeline/production_release_verifier.py`

使用 fresh modeled acceptance 构建新 ZIP，逐项证明：

1. repository `make.py` 是 tracked 也不会进入 runtime source payload；
2. `release/production-runtime-runner.py` 唯一映射到包根 `make.py`，role 精确为
   `runtime-runner`；
3. 缺 template、template symlink/non-regular、读取漂移、重复 portable
   destination 都 fail closed；
4. clean-source gate 检查 template，不因 repository `make.py` 的无关变化污染
   Production runtime closure；
5. 包内 `make.py` 字节 SHA 同时绑定
   `PRODUCTION-RELEASE.json` artifact 和 `SHA256SUMS.txt`；
6. development runner 的 `build-production / audit-production-privacy /
   stage-production-assets / real-scene` 字节或帮助文本未进入包内 runner；
7. tree verifier 与 ZIP verifier 都拒绝 runner drift、receipt drift、checksum
   drift 和额外文件；
8. 同一输入两次构建 archive SHA 与 package content ID 一致。

至少运行：

```powershell
python -m pytest -q tests/test_production_release_builder.py tests/test_production_release_verifier.py tests/test_production_release_contract.py
python -m ruff check pipeline/production_release_builder.py tests/test_production_release_builder.py
git diff --check
```

## R3：真实 clean-room 命令链（仍是 modeled 合同证据）

不能只 monkeypatch runner。必须从 R2 新建 ZIP 解压到一个此前不存在的目录，在包根
实际执行：

```powershell
python make.py help
python make.py verify
python make.py bogus
```

记录：

- `help` 返回 0；
- 第一次和第二次 `verify` 都返回 0 且 JSON `valid=true`；
- 两次 verify 后没有 `__pycache__` 或额外文件；
- `bogus` 返回 2，且没有启动 server；
- 包内 `make.py` 的 SHA 与 receipt/checksum 完全一致。

不要在自动审计里实际启动常驻 `serve`。通过静态 exact argv 测试和
`pipeline.studio_server` 既有测试证明 host/port；避免留下后台进程。

## R4：exact-HEAD 三平台 CI 与回归结论

检查包含本工单基线的最新 exact `origin/main`：

1. `production-release-contract` 在 Ubuntu / Windows / macOS 全绿，并实际包含
   `tests/test_production_runtime_runner.py` 与
   `tests/test_production_release_builder.py`；
2. compare Production content IDs 全绿；
3. Preview/repository runner 回归全绿；
4. 全量 test matrix 若仍运行，明确写 `pending`，不得用专项绿替代全量绿；
5. CI 若失败，只处理与 R1–R3 有直接因果证据的失败；其它 lane 只记录和移交。

推荐命令：

```powershell
$head = git rev-parse origin/main
$runs = gh run list --workflow ci.yml --branch main --limit 10 --json databaseId,headSha,status,conclusion,url | ConvertFrom-Json
$run = $runs | Where-Object { $_.headSha -eq $head } | Select-Object -First 1
if ($null -eq $run) { throw "no CI run found for exact HEAD $head" }
gh run view $run.databaseId --json headSha,status,conclusion,jobs,url
```

## 最终 feedback 必填结构

1. `Baseline`：审计 commit、运行环境、测试命令；
2. `R1`：八项逐项 `PASS / FINDING`；
3. `R2`：八项逐项 `PASS / FINDING`；
4. `R3`：fresh archive、package content ID、runner SHA、两次 verify 结果；
5. `R4`：exact-head CI job 表；
6. `Changes`：没有代码变更也明确写 `none`；
7. `Trust boundary`：本工单只关闭 runtime 自包含，真实场景五门状态不变；
8. `Next GLM queue`：完成后主动继续以下只读任务，不要停：
   - 检查新 CI 是否出现与 runner 直接相关的失败；
   - 对照 Production 手册逐条核对命令在 repository/runtime 中的归属；
   - 汇总可移入 `handoff/HISTORY.md` 的 015 关键信息，但先只在 feedback 提建议，
     不删除 015 原文。
