# GLM-021 Production Evidence Boundary Long Queue

**Baseline:** `49b928f842d91328fc66206e5ba322aa4b18e3f8`

**Goal:** 连续关闭正式版真实场景链路中仍按路径重开、无界读取、身份漂移、reparse /
junction 与错误信息泄漏的 repo-local 边界。任务完成一项后自动进入下一项，不等待
Codex 再分配；只有真实冲突才跳过，跳过后继续后续不冲突项。

这是一条辅助安全 lane。它减少正式版误接受和私有路径泄漏风险，但不会产生真实素材、
accepted SfM、CUDA 3DGS、米制控制点或真实 Viewer QA，不能据此宣称 Production V1。

## 0. 连续执行纪律

1. 开始前读取 `AGENTS.md`、本文件和当前 `git status --short --branch`。
2. 单一 `main`、单一共享 worktree。禁止 branch、worktree、stash、reset、rebase、
   `git add -A`、`git commit -a` 和清理他人文件。
3. 每个包严格 RED → 最小 GREEN → 完整模块测试 → Ruff → `git diff --check`。
4. 每个包只提交声明路径；提交后立即用精确 SHA 和一次性代理推送：

   ```powershell
   $sha = git rev-parse HEAD
   git -c http.proxy=http://127.0.0.1:7890 push origin "${sha}:refs/heads/main"
   ```

5. 每次提交前先用同一临时代理 `fetch origin main`，确认 `HEAD == origin/main`。若远端
   已前进或声明路径有 Codex WIP，不覆盖、不合并、不等待：记录冲突并切到后续不冲突包。
6. 禁止触碰 `.github/**`、`README.md`、`docs/**`、`handoff/**`、Release/tag、
   CUDA image/runtime、Viewer 前端和任何私有 `.nantai-studio/**` 产物。
7. 不为制造提交而重构。若审计确认已闭环，补真正能防回归的测试；若没有可证明的新
   缺口，记录 `NO-FINDING` 并继续。
8. 至少连续完成或审计完四个包后再汇报。汇报必须包含：包编号、RED 证据、提交 SHA、
   测试计数、跳过原因、下一包编号；不得只说“无事可做”。

## 1. Canonical dataset / rights loaders

**Allowed files**

- `pipeline/real_dataset.py`
- `tests/test_real_dataset.py`

**Required audit and acceptance**

- `load_real_dataset_source` 与 `load_capture_rights_receipt` 不得使用
  `Path.read_bytes()` 或按名称二次读取。
- 新增有界单句柄 canonical JSON loader：path-before、fd-before、fd-after、
  path-after 完整相邻 identity；Windows 跨 surface 只比较 file type，不直接比较
  path/fd 的完整 `st_mode` 或 `st_ctime_ns`。
- 拒绝 symlink、junction、reparse、非 regular、空文件、超上限、短读和读取中身份
  漂移；OSError 顶层文本不得回显绝对路径、secret 或系统异常文本。
- RED 至少覆盖：禁止 `Path.read_bytes`、descriptor-after reparse、early EOF、
  1 MiB 上限、私有路径 OSError；保留 canonical JSON、重复 key 与 schema 语义。

**Commit:** `fix: stabilize real dataset evidence loaders`

## 2. COLMAP text evidence streaming

**Allowed files**

- `pipeline/registration_quality.py`
- `tests/test_registration_quality.py`

**Required audit and acceptance**

- `_parse_colmap_images_txt` 与 `_parse_colmap_points3d_count` 不得
  `read_text().splitlines()` 整体载入。
- 使用单个受控 fd 流式 UTF-8 解析，并在 EOF 后完成 fd/path identity 重验；拒绝
  link/reparse、invalid UTF-8、short read、单行异常膨胀和读取中替换。
- 保持 COLMAP `images.txt` 两物理行一图的语义；空 POINTS2D 行仍合法；错误只报告
  portable label 与行号，不回显绝对路径。
- RED 至少覆盖：超过 2 MiB 的流式 fixture、单次读取有界、第二次 fstat 漂移、
  path-after swap、缺失 POINTS2D 行与异常长行。

**Commit:** `fix: stream COLMAP quality evidence safely`

## 3. Human-review input and publication boundary

**Allowed files**

- `pipeline/human_review_inputs.py`
- `tests/test_human_review_inputs.py`

**Required audit and acceptance**

- `_read_regular_bytes` 改为有明确 byte cap 的单句柄读取；拒绝 reparse/junction、
  early EOF 与 path/fd identity drift；不使用无参数 `read()`。
- 修正 Windows path stat 与 descriptor stat 的 surface 差异，不能因正常
  `st_mode` / `st_ctime_ns` 差异误拒绝。
- 审计输出父目录从 `_prepare_output` 到 staging 创建、fsync、no-replace publish 的
  identity；若父目录可在检查后替换，先写 RED，再做最小 dirfd/capability 修复。
- 所有错误保持固定 label，不泄漏 evidence root、用户名、临时目录或 OSError 文本。
- 保留 Viewer v2、policy/report SHA 和七类人工审查语义，不改变 schema。

**Commit:** `fix: close human review evidence boundaries`

## 4. Viewer acceptance CLI inputs and decision output

**Allowed files**

- `pipeline/viewer_acceptance.py`
- `tests/test_viewer_acceptance.py`

**Required audit and acceptance**

- CLI 的 `--policy` / `--report` 不得直接 `Path.read_bytes()`；复用或实现有界稳定读取，
  并对 v2 report 保留 evidence-root 权威复验。
- `--decision` 不得 `write_text()` 覆盖既有文件、跟随 link 或接受父目录 swap。
  使用 private staging + fsync + no-replace publication，失败保留诚实状态。
- RED 至少覆盖：输入 reparse、descriptor drift、short read、超限、输出已存在、
  output symlink/junction、父目录漂移、OSError 私有路径。
- CLI 退出码与 `ACCEPTED` / `REJECTED` 语义保持兼容；安全错误返回 2，不产生部分
  decision。

**Commit:** `fix: harden Viewer acceptance CLI evidence`

## 5. Viewer session launch preflight

**Allowed files**

- `pipeline/viewer_session.py`
- `tests/test_viewer_session.py`

**Required audit and acceptance**

- 审计 `_require_regular_file` / `_validated_options` 到 `subprocess.run` 之间的
  policy、camera set、capture script、Node/Python executable 身份是否仅凭路径检查。
- 用 fault injection 证明 launch 前 swap、junction/reparse、可执行文件替换和输出
  预占全部 fail closed；不得仅凭 `resolve()` / `is_file()` 推导稳定 identity。
- 如果 caller 下游已经重新绑定完整字节，测试必须证明该事实；如果没有，最小修复应
  传递已测 identity 或在 mutation 前重新验证，不新建第二套 Viewer schema。
- 子进程失败和 timeout 文本不得回显私有 argv/path；所有退出路径仍关闭 Studio
  server，并且失败不得生成 human-review policy。

**Commit:** `fix: bind production Viewer session launch inputs`

## 6. Real capture authoritative artifacts

**Allowed files**

- `pipeline/real_scene_capture.py`
- `tests/test_real_scene_capture.py`

**Required audit and acceptance**

- 基于 `49b928f` 已完成的 `_sha256_file` 模式，审计并关闭仍存在的
  ingest manifest、registration JSON 与 capture manifest 的 raw `read_bytes()`。
- 每个权威文件必须有明确上限、单句柄读取、完整相邻 identity、canonical / object
  equality；禁止 check-then-reopen。
- 外部 `ingest_all` / `register` 抛错时，顶层消息不能携带绝对媒体路径或 secret；
  只保留固定阶段 label，同时通过 exception chaining 保留本地调试因果。
- 不改变真实/模拟信任语义，不把 `synthetic=False` 提升为 accepted real-photo SfM。

**Commit:** `fix: stabilize real capture artifact reads`

## 7. Real-scene import manifest and generated contracts

**Allowed files**

- `pipeline/real_scene_import.py`
- `tests/test_real_scene_import.py`
- `tests/test_real_scene_import_streaming.py`

**Required audit and acceptance**

- 关闭 chunks manifest、reconstruction manifest、generated registration 与 splat
  contract 的 raw `read_text()` / `read_bytes()`；优先复用现有
  `_read_regular_bytes` / `_load_canonical_model`，不要再造不一致 helper。
- 同一验证调用不得先摘要后又按名称重开内容；manifest parse、SHA、receipt claims 和
  coordinate repack 必须来自同一组受控 bytes 或显式重新绑定的 stable handle。
- 大 PLY 仍流式处理，禁止为“统一接口”整块载入；JSON/contract 设置保守 byte cap。
- RED 至少覆盖：manifest 在两次消费间替换、descriptor reparse、short read、
  generated contract swap、超限 JSON 与 OSError 隐私。
- 保留 exact Gaussian count、chunk integrity、metric alignment 与 production
  closure 现有门，不修改 receipt schema。

**Commit:** `fix: bind real-scene import manifests to stable bytes`

## 8. Cumulative adversarial matrix and review handoff

**Allowed files**

- 上述各包对应测试文件
- 只有 RED 证明真实缺口时，才回到对应生产文件做最小修复

**Required acceptance**

1. 为完成的每个模块统一覆盖：
   path-before → fd-before → fd-after → path-after、reparse bit、early EOF、byte cap、
   fixed error privacy、close-on-error。
2. 运行所有相关完整测试：

   ```powershell
   python -m pytest -q `
     tests/test_real_dataset.py `
     tests/test_registration_quality.py `
     tests/test_human_review_inputs.py `
     tests/test_viewer_acceptance.py `
     tests/test_viewer_session.py `
     tests/test_real_scene_capture.py `
     tests/test_real_scene_import.py `
     tests/test_real_scene_import_streaming.py
   ```

3. 对所有实际修改路径运行 Ruff 与 `git diff --check`。
4. 检查最新精确 HEAD 的 GitHub CI；CI pending 可以汇报，失败必须定位到具体 job，
   不得把“已触发”写成“已通过”。
5. 最终仅给 Codex 一次汇总 review 请求；若前四包完成后 Codex 尚未回复，继续执行
   5–8，不停下来索要工作。

**Commit（仅测试矩阵确有新增时）:** `test: complete production evidence fault matrix`

## GLM 自主续航规则

完成 Task 8 后仍不得直接说“无事可做”。先做一次只读扫描：

```powershell
rg -n -e '\.read_bytes\(' -e '\.read_text\(' -e '\.open\("rb"' `
  pipeline cloud scripts `
  -g 'production_*.py' -g 'real_scene_*.py' `
  -g 'viewer_*.py' -g 'human_review_*.py'
```

只对信任关键输入提出后续候选，并按以下顺序处理：

1. 真实场景 acceptance 决策所消费的字节；
2. 私有素材、权利、测量和 Viewer 证据；
3. GPU result / import receipt / release 四件套；
4. 纯预览或 synthetic convenience 路径。

扫描结果必须先写“利用路径 + 可观察后果 + RED 方案”；没有这三项就不是工单。禁止
扩大到 UI 美化、历史文档、synthetic 素材或新产品功能。若全部无真实缺口，提交一份
简短聊天审计回执给 Codex，等待 review；不要制造代码改动。
