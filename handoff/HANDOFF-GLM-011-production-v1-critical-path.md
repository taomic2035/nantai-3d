# HANDOFF-GLM-011 — Production V1 关键路径连续队列

日期：2026-07-27

Owner：GLM lane

Reviewer：Codex
已验证实现基线：`main@02474403d0f2ca769a3a8f38d77a4a420b0ad01e`

本文件是 GLM 当前唯一的首要执行入口。旧的 GLM-007/008 只用于历史追溯；
GLM-009 和 Batch35 synthetic 工作排在本队列 P0/P1 之后。

## 当前事实

- `v1.0.0-preview.2` 标签及其 CI 是绿色的，已发布的 Preview2 不受本次回归影响。
- 旧的
  [CI run 30208773810](https://github.com/taomic2035/nantai-3d/actions/runs/30208773810)
  是冻结的失败基线，不再代表当前 `main`。
- P0-2 已由 `2351805` / `76c5bad` 关闭：COLMAP 版本证据绑定实际执行的
  resolved binary。
- P0-3 已由 `84cce65` / `34ea9b6` 关闭：ZIP、journal 与 remote result 使用统一
  durable publication，并显式区分 `published-unconfirmed`。
- P0-4 已由 `1d07d54` / `fe75ea6` 关闭：Windows 私钥 ACL 与单 handle 生命周期、
  host key、null device 和 secret-redaction 保持 fail closed。
- P0-5 已由 `2d34021` 关闭：Windows canonical bytes、PATH、Git Bash 结构检查和
  ZIP 原始反斜杠成员拒绝专项门为 `81 passed, 7 skipped`。
- P0-6 已由 `0247440` 关闭：本地 `make.py test` 为 Python
  `3353 passed, 90 skipped`、Viewer Node `216/216`、Studio Node `103/103`，
  全范围 ruff 与 `git diff --check` 通过。
- exact-HEAD
  [CI run 30234423540](https://github.com/taomic2035/nantai-3d/actions/runs/30234423540)
  的 Ubuntu/Windows × Python 3.11/3.13 四个 test job、两个 reproducibility job
  与 compare job 全部通过。
- **GLM 当前立即领取 P1-1A**，随后连续执行 P1-1B、P1-1C、P1-1D、P1-2A；
  不再等待 P0 或 Codex 另行提醒。
- 当前 canary 只有真实照片 COLMAP 与本机 Brush `preview-only` 证据。尚无
  非 mock CUDA 3DGS、实测米制对齐、真实 Viewer/human acceptance，因此不能
  报告“真实 3D 场景完成”或“production accepted”。

## GLM 立即领取清单

不要等待 Codex 逐项提醒。按下表从上到下连续领取；每完成一行就提交、push、回报
SHA 与机器结果，然后立刻进入下一行。`blocked-external-input` 只阻塞真实远端调用，
不阻塞 schema、preflight、恢复测试、CLI 和 CI 集成。

| ID | 任务 | 完成定义 |
|---|---|---|
| P0-6A | 运行 exact-HEAD 本地全门 | `make.py test`、全范围 ruff、`git diff --check` 有完整结果 |
| P0-6B | 修复全门剩余失败 | 每个失败先有最小 RED；禁止删除、xfail 或放宽安全门 |
| P0-6C | 关闭远端矩阵 | 记录 exact commit 与 Actions URL，4 个 test + 3 个 repro job 全绿 |
| P1-1A | Viewer acceptance 单测入标准门 | `make.py test` 明确运行 `scripts/capture_viewer_acceptance.test.mjs` |
| P1-1B | Chromium 能力预检 | 缺 package、browser binary 或 launch 能力时输出明确机器状态 |
| P1-1C | CI 安装锁定 Node/browser | `npm ci` 使用 lockfile；Ubuntu/Windows 都验证同一 pinned runtime |
| P1-2A | remote preflight schema | canonical report 只允许 `ready` / `blocked-external-input` / `failed` |
| P1-2B | 本地 transport 预检 | 检查 ssh/scp、配置 shape、私钥权限、known_hosts/fingerprint，不提交 job |
| P1-2C | 远端只读能力预检 | 检查 runtime、immutable image digest 与 worker binary；不得创建训练目录 |
| P1-2D | secret-redaction 回归 | argv、报告、异常、traceback 都不含 key 路径、token 或私有 config 原文 |
| P1-3A | submit/poll/fetch 状态演练 | success、remote failed、timeout、断网、unknown 均有 monotonic 状态断言 |
| P1-3B | checksum/内容漂移演练 | result、journal、dataset/config/container SHA 任一漂移均 fail closed |
| P1-3C | crash/resume/retry 演练 | published-unconfirmed、重启恢复、显式 retry 不得把失败提升为 succeeded |
| P1-3D | canonical drill report | 发布机器可读演练报告，绑定测试版本、输入 SHA 与 exact commit |
| P1-4A | 控制点输入预检 | 至少 4 个非共面点、单位/坐标系/对应关系可证；不满足保持 unaligned |
| P1-4B | 米制对齐证据门 | Sim3 残差、尺度、FrameTransform 与 transform history 内容绑定 |
| P1-5A | 云 GPU runtime readiness | 校验 CUDA、Nerfstudio 版本与 digest image；无端点时诚实 blocked |
| P1-5B | production result closure | 非 mock result 必须绑定 bundle/config/container/result/evaluation SHA |
| P1-6A | fresh Windows canary | fetch → COLMAP → bundle → Brush → import/chunk → acceptance aggregation |
| P1-6B | canary 信任审计 | 预期仍为 preview-only / arbitrary / unaligned，不得人工提升 |
| P1-7A | 给 Codex 的 Viewer 输入包 | 只交 verified import/chunks、scene identity、政策与 checksum |

P1-7A 后由 Codex 执行真实浏览器 cold-load、交互帧、关键视角与人工视觉审查；GLM
继续处理机器报告或 review 修复，不得因为等待 Codex 视觉结论而宣称队列为空。

## 执行规则

1. 严格按 P0-1 → P0-6 → P1-1 → P1-7 顺序连续推进；一个任务完成后直接进入
   下一个，不因“测试已绿”而停下。
2. 先写能复现缺陷的 RED 测试，再做最小修复。不得删除/跳过测试、降低阈值、
   把 unknown 变成 accepted，或用 fixture 字符串冒充机器证据。
3. 只改每项列出的路径。需要跨出边界时先写明原因交 Codex review；不要修改
   `web/`、Release、公开 acceptance 状态或已有证据文件。
4. 每项独立小提交，路径限定 `git add` / `git commit -- <paths>`。GLM 使用自己
   的署名；push 用一次性代理，不修改 Git 配置：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

5. 每次交付回报：commit SHA、修改路径、RED/GREEN 命令与结果、剩余风险、
   远端 exact-HEAD CI URL。没有真实外部输入时继续做下一项本地任务，不要等待。

## P0 — 恢复可信的跨平台主干

### P0-1：冻结失败基线，不提交

目标：把远端失败归类为产品缺陷、测试缺陷或平台合同缺口，避免边修边猜。

读取范围：

- `.github/workflows/ci.yml`
- `pipeline/real_scene_capture.py`
- `pipeline/real_scene_training.py`
- `pipeline/training_executor.py`
- `pipeline/remote_shell_executor.py`
- 对应 `tests/test_*.py`

必须复现：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_real_scene_capture.py::test_sfm_accepts_only_matching_colmap_model_capture_and_poses -q
.\.venv\Scripts\python.exe -m pytest `
  tests/test_real_scene_training.py `
  tests/test_training_executor.py `
  tests/test_remote_shell_executor.py -q
```

输出一张简短映射表：失败测试 → 根因 → P0-2/3/4/5。不要为本项单独提交文档。

### P0-2：COLMAP 二进制与版本证据同源

目标：版本必须来自实际执行 registration 的同一个 resolved binary；测试替身也
必须显式提供版本证据，不能重新扫描 PATH，也不能从 `engine="colmap"` 推导。

允许路径：

- `pipeline/registration.py`
- `pipeline/real_scene_capture.py`
- 必要时 `pipeline/real_scene_operations.py`
- `tests/test_real_scene_capture.py`
- `tests/test_registration.py`

RED 用例至少覆盖：

- registration 成功但版本证据缺失时 fail closed；
- 实际 binary 与版本探针绑定；
- fake registration 只有在显式注入 measured version 时通过；
- 非 COLMAP engine 不伪造 COLMAP version。

验收：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_registration.py tests/test_real_scene_capture.py -q
.\.venv\Scripts\python.exe -m ruff check `
  pipeline/registration.py pipeline/real_scene_capture.py `
  pipeline/real_scene_operations.py tests/test_registration.py `
  tests/test_real_scene_capture.py
```

### P0-3：统一 durable write/replace 跨平台语义

目标：消除 Windows 对只读 descriptor `fsync` 的 `Bad file descriptor`，同时保留
“内容落盘后才发布/替换”的 fail-closed 语义。

允许路径：

- 可新增一个窄职责的 `pipeline/` durable I/O helper；
- `pipeline/real_scene_training.py`
- `pipeline/training_executor.py`
- `pipeline/remote_shell_executor.py`
- 对应测试。

实现要求：

- 文件必须在可写 handle 上 `flush` 后同步，再 close/replace；
- 目录同步若平台不支持，必须由 helper 明确表达平台语义，不能散落
  `except OSError: pass`；
- ZIP、journal、remote result bundle 都要有成功、同步失败、replace 失败和残留
  `.partial`/journal 恢复测试；
- 不改 canonical bytes、SHA 或 transaction state machine。

验收：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_real_scene_training.py `
  tests/test_training_executor.py `
  tests/test_remote_shell_executor.py -q
```

### P0-4：Windows SSH transport fail-closed 适配

目标：Windows 上不再用 POSIX mode bits 误判所有私钥，同时保持 host key、
fingerprint、identity 和 secret-redaction 的强约束。

允许路径：

- `pipeline/remote_shell_executor.py`
- `tests/test_remote_shell_executor.py`
- 如确有必要，可新增一个只负责平台权限验证的 `pipeline/` helper。

实现要求：

- POSIX 继续拒绝 group/other 可读私钥；
- Windows 使用可测试的 ACL/可读性策略；若无法证明安全，应返回明确的
  preflight failure，不得默认放行；
- null device 使用平台适配，不硬编码 `/dev/null`；
- 保留 `StrictHostKeyChecking=yes`、指定 `UserKnownHostsFile`、
  `IdentitiesOnly=yes`、已锁定 fingerprint；
- 日志、argv、异常仍不得泄露 key、token 或完整敏感配置。

验收至少包含 Windows/POSIX 参数与权限策略单测，以及：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_remote_shell_executor.py -q
```

### P0-5：修复跨平台测试与 canonical fixture

目标：修测试基础设施，不削弱生产校验。

允许路径：

- `tests/test_real_scene_training.py`
- `tests/test_training_executor.py`
- `tests/test_remote_shell_executor.py`
- `tests/test_local_brush_executor.py`
- `tests/test_cloud_prepared_training_script.py`
- 由 P0-1 映射出的其它直接相关测试文件。

要求：

- canonical JSON fixture 用明确 UTF-8/LF bytes，不能依赖 Windows
  `Path.write_text()` 默认换行；
- PATH 解析使用 `os.pathsep`；
- Git Bash fixture 必须正确处理 Windows drive path，或把真正 Linux-only 的行为
  隔离成有明确理由的 platform case；
- 不更改生产 manifest verifier 来接受非 canonical 输入。

### P0-6：exact HEAD 全门恢复

本地：

```powershell
.\.venv\Scripts\python.exe make.py test
.\.venv\Scripts\python.exe -m ruff check pipeline tests cloud scripts make.py
git diff --check
```

随后 push，并等待 exact commit 的 Ubuntu/Windows × Python 3.11/3.13 test 与
reproducibility 全绿。只有远端 exact-HEAD 全绿，P0 才完成。

## P1 — 把真实场景 caller 变成可执行产品门

### P1-1：把 Viewer acceptance 测试纳入标准门

Owner：GLM 负责 build/CI 集成；Codex 负责浏览器语义与 UI review。

允许路径：

- `make.py`
- `Makefile`
- `package.json` / `package-lock.json`
- `.github/workflows/ci.yml`
- `scripts/capture_viewer_acceptance.test.mjs`

要求：

- 标准 setup/CI 安装锁定的 Node 依赖；
- `make.py test` 必须运行 `scripts/capture_viewer_acceptance.test.mjs`；
- lint 范围覆盖新增的 `cloud/`、`scripts/` 和 `make.py` Python 代码；
- 浏览器 binary 缺失必须由显式 preflight 报告，不能晚到 capture 阶段才模糊失败；
- 本项不改 `web/` UI 或 acceptance 阈值。

拆分提交：

1. P1-1A 只改 `make.py` / `Makefile` 与对应 runner 测试，把
   `scripts/capture_viewer_acceptance.test.mjs` 加入 `test`；
2. P1-1B 在 `scripts/` 新增窄职责 runtime preflight，机器报告绑定 Node、
   Playwright、Chromium executable/launch 结果，缺能力不得返回 ready；
3. P1-1C 更新 workflow：`npm ci` 后安装 pinned Chromium，并在 Ubuntu/Windows
   都运行 preflight；不得使用 floating global package；
4. P1-1D 把 Python lint 范围扩到 `cloud/`、`scripts/`、`make.py`，仅修本项暴露的
   lint，不做无关格式化。

验收：

```powershell
.\.venv\Scripts\python.exe make.py test
node --test scripts/capture_viewer_acceptance.test.mjs
npm ci
npx playwright install chromium
```

### P1-2：真实 remote-shell credential-free preflight

目标：在不提交训练 job、不读取/打印秘密的前提下，验证 remote config shape、
本地 ssh/scp、known_hosts/fingerprint、container digest 和必要 binary。

优先扩展现有 `real-scene` CLI；输出 canonical machine report，状态只能是
`ready`、`blocked-external-input` 或 `failed`。没有真实凭据时交付
`blocked-external-input` 是正确结果，不得生成假 ready receipt。

允许路径：

- `pipeline/remote_shell_executor.py`
- `pipeline/real_scene_operations.py`
- `pipeline/real_scene_runner.py`
- `scripts/real_scene.py`
- `make.py`
- `tests/test_remote_shell_executor.py`
- `tests/test_real_scene_operations.py`
- `tests/test_real_scene_runner.py`
- `tests/test_real_scene_cli.py`

必须覆盖：

- 无配置、无凭据、ssh/scp 缺失分别得到稳定错误码和
  `blocked-external-input`，不是异常 traceback；
- config JSON、私钥、known_hosts 在检查期间被替换或改变时失败；
- host fingerprint、container identity 必须是 immutable digest；
- 可选的远端探针只能执行固定 argv 的只读命令，禁止 shell 拼接、上传 bundle、
  mkdir 或启动容器；
- report 不包含用户名之外的连接秘密，不包含私钥路径、config 原文或 stderr
  未过滤内容；canonical bytes 与内容 SHA 可复算；
- 同一 report 不能被其它 config/container/host 复用。

建议 CLI：

```powershell
.\.venv\Scripts\python.exe make.py real-scene preflight-remote `
  SOURCE=<source.json> REMOTE_CONFIG=<private-config.json>
```

若新增 target，必须同步 `Makefile`、`make.py`、帮助文本与 CLI 测试。

#### P1-2 实施顺序与 Codex review 门

GLM 不要只交付一个 report model 后停下；按以下四个独立小提交连续推进：

1. **P1-2A — schema 与安全状态机**
   - `ready` 必须要求本地 transport、known_hosts、私钥保护、远端 container digest
     和 worker binary 全部有实测 `true`；远端结果为 `None` 时不能 ready；
   - 使用稳定、有限集合的 `failure_code`，不得把任意异常、stderr 或 config 原文
     放进 `failure_reason`；
   - report 必须绑定去密后的 config identity、known_hosts 内容 SHA、container digest、
     worker identity 和检查器版本；只列这些安全 identity，不列私钥/config 路径；
   - canonical report ID/content SHA 从除自身 ID 外的完整 payload 复算，跨 config、
     host、container 或 worker 不能复用。
2. **P1-2B — 本地 credential-free probe**
   - 配置缺失、凭据缺失、`ssh` 缺失、`scp` 缺失分别得到稳定
     `blocked-external-input` code；
   - JSON duplicate key、shape 错误、known_hosts/fingerprint 不匹配、私钥保护不合格
     得到 `failed`；
   - config、known_hosts、key 在检查前后替换、变更或变成 link-like entry 必须失败；
     不在 argv、stdout、异常或 report 中暴露路径/内容。
3. **P1-2C — 固定只读远端 probe**
   - 只有 P1-2B 全绿后才允许启动 transport；
   - 命令和参数来自固定模板与严格验证的 immutable identifiers，禁止 shell 拼接、
     bundle upload、`mkdir`、container create/run 或训练；
   - 验证实际 runtime、immutable image digest 和 worker executable；超时、host-key
     漂移、连接失败与 capability mismatch 使用不同稳定 code；
   - probe 后再次验证所有本地输入快照，TOCTOU 漂移不得发布 ready。
4. **P1-2D — CLI、canonical publication 与对抗回归**
   - `real-scene preflight-remote` 只写 operator 私有、独占创建的 canonical report，
     不创建训练目录或 job；
   - 覆盖 success、三类 blocked、shape/fingerprint/ACL/tamper/timeout/remote mismatch、
     output 已存在和中途写失败；
   - 对 argv、stdout/stderr、异常文本、traceback 和 report 做 secret/path canary 搜索；
   - Linux/Windows 测试门明确区分真实平台能力与 fixture，不用 fixture 字符串冒充
     remote-ready 机器证据。

当前 `RemoteShellPreflightReport` 草稿在进入 P1-2B 前必须先关闭三项：

- `ready` 不能只检查四个本地 boolean；
- 任意自由文本 `failure_reason` 不能成为机器状态或错误透传通道；
- 缺少去密 config/input snapshot binding 时，report 仍可被其它配置重放。

### P1-3：恢复/失败演练

对 submit、poll、fetch、checksum mismatch、远端 job 失败、网络中断与本地 journal
恢复做 fresh 演练。产物必须绑定 dataset/training config/container/result SHA；
失败 job 不得被 resume 为 succeeded。

按四个独立提交交付：

1. P1-3A：fake transport 覆盖 submit-before-ack、ack-after-disconnect、
   poll timeout、remote failed、fetch interrupted；
2. P1-3B：对 bundle、result、evaluation、container identity、journal 的每个 SHA
   做单点 tamper；
3. P1-3C：重启后只从 durable journal 恢复，`unknown` 仍为 unknown，显式 retry
   产生新 attempt identity；
4. P1-3D：生成 `nantai.remote-training-drill.v1` canonical report，绑定 exact
   commit、场景/数据/config/container SHA、用例结果和工具版本。

优先路径：

- `pipeline/training_executor.py`
- `pipeline/remote_shell_executor.py`
- `pipeline/real_scene_operations.py`
- 对应三组 tests；如新增脚本只放 `scripts/`，报告样例只放 test 临时目录。

### P1-4：实测控制点与米制对齐门

目标：在真实训练结果到来前完成输入和证据合同；没有合格控制点时明确保持
`arbitrary / unaligned`。

必须覆盖：

- 至少 4 个唯一、非共面的 3D 对应点，拒绝重复、共线、近共面和 rank-deficient；
- 声明源/目标 frame、轴、handedness、单位和点身份；
- Sim3 必须报告 scale、rotation、translation、每点 residual、RMSE/max residual；
- policy 与决定分离；阈值变化改变 policy SHA，不能改写 measured residual；
- alignment 绑定 registration、control-points、policy 与 transform history SHA；
- 未通过时禁止写 `metric`、`aligned` 或 ENU frame。

优先路径：`pipeline/alignment.py`、`pipeline/real_scene_runner.py`、
`pipeline/real_scene_acceptance.py` 及对应 tests。

### P1-5：云 GPU runtime readiness 与 production result closure

目标：把“能连接服务器”和“真实训练已接受”分开。无 CUDA/端点/凭据时可交付
`blocked-external-input`，但 schema、CLI、探针和 fixture 必须完成。

必须覆盖：

- 远端 CUDA device、driver/runtime、Nerfstudio `1.1.5`、训练 CLI 实际 schema；
- container 必须用 digest，探针结果绑定实际执行 binary/image；
- 禁止在 readiness probe 安装依赖、改 PATH、运行 SfM 或启动训练；
- production result 必须有非 mock training log、export PLY、dataparser transform、
  held-out evaluation、container identity 与 content SHA；
- stub/fake/local Brush 结果永远不能满足 production closure。

优先路径：`cloud/`、`pipeline/remote_shell_executor.py`、
`pipeline/training_executor.py`、`pipeline/training_provenance.py` 及对应 tests。

### P1-6：fresh Windows canary

在 P0/P1-1/2/3/4/5 完成后，使用现有 rights-cleared canary 配置重跑：

```text
fetch/verify → fresh COLMAP → split/bundle → local Brush preview
→ import/chunk → acceptance aggregation
```

只提交机器报告、内容 SHA 和精简说明。预期仍是
`internal-only / preview-only / arbitrary / unaligned`；不得把本机 Brush 提升为
production 3DGS。

每一段必须使用 fresh 输出目录；报告绑定 `main` exact commit。失败阶段保留机器状态
和最小日志摘要，不提交媒体、缓存、私有配置、绝对用户目录或完整中间输出。

### P1-7：交给 Codex 的真实 Viewer 输入包

只有真实非 mock 云结果到达后执行。GLM 交付：

- verified import receipt 与 scene identity；
- chunks manifest、LOD、坐标/transform history；
- render/viewer policy；
- 全部内容 SHA 与生成工具 exact commit；
- 明确仍缺失的 meter alignment 或人工验收门。

Codex 消费这些输入做真实浏览器 QA；GLM 在等待期间继续 P2 的 review 修复，不能把
“等待人工视觉结论”报告为整个 lane 无任务。

## P2 — 不阻塞真实主线的后续工作

1. 修复 `REVIEW-CODEX-037-glm-prop-geometry-v2.md` 的两项 P1 和一项 P2，
   复审通过后才开始 Batch35 Blender emission。
2. 完成 Batch35 八类道具的 Blender emission、exact build 和实渲验收。
3. 继续 `HANDOFF-GLM-009-roaming-graph-producer.md` 的真实 producer 接入；
   不得把不同 scene identity 的 synthetic graph 与 real splat 强拼。
4. 对 synthetic 材质做 causal A/B 实渲；设计图相似不能代替材质因果证据。

## Codex 并行责任

- review GLM 每个 P0/P1 提交和 exact-HEAD CI；
- 补齐 Playwright Chromium 的用户安装/体检入口；
- 用真实浏览器执行 cold load、交互帧、关键视角和 human visual review，并区分
  cold-start latency 与 scene-loaded pose interaction；
- 只有真实 GPU 结果、米制对齐和真实 Viewer/human 证据齐全后，才签署 production
  acceptance 或准备真实场景 Release。

## 外部输入（不是 GLM 停工理由）

- rights-cleared、密集重叠的生产照片/视频；
- 可用的 NVIDIA CUDA 训练端、immutable container digest 和 operator credentials；
- 至少 4 个非共面实测控制点及坐标系/单位；
- 最终人工视觉验收人。

这些输入缺失时，GLM 应继续完成 P0/P1 的代码、preflight、恢复演练和机器合同；
只有在某一步会真实提交云任务、接触凭据或签署人工结论时才停下请求外部输入。
