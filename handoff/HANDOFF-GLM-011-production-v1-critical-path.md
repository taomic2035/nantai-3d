# HANDOFF-GLM-011 — Production V1 关键路径连续队列

日期：2026-07-27

Owner：GLM lane

Reviewer：Codex
基线：`main@348422fa65176163129c9b44b7cd22ad14d8b35c`

本文件是 GLM 当前唯一的首要执行入口。旧的 GLM-007/008 只用于历史追溯；
GLM-009 和 Batch35 synthetic 工作排在本队列 P0/P1 之后。

## 当前事实

- `v1.0.0-preview.2` 标签及其 CI 是绿色的，已发布的 Preview2 不受本次回归影响。
- 最新 `main` 的
  [CI run 30208773810](https://github.com/taomic2035/nantai-3d/actions/runs/30208773810)
  四个 test matrix job 全部失败；两个 reproducibility job 和比较 job 通过。
- Linux 的确定性失败位于真实 SfM 的 COLMAP 版本证据边界。
- Windows 还暴露了 durable write、SSH key 权限/null device、换行、PATH
  分隔符及 Bash fixture 的跨平台问题。
- 当前 canary 只有真实照片 COLMAP 与本机 Brush `preview-only` 证据。尚无
  非 mock CUDA 3DGS、实测米制对齐、真实 Viewer/human acceptance，因此不能
  报告“真实 3D 场景完成”或“production accepted”。

## 执行规则

1. 严格按 P0-1 → P0-6 → P1-1 → P1-4 顺序连续推进；一个任务完成后直接进入
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

### P1-2：真实 remote-shell credential-free preflight

目标：在不提交训练 job、不读取/打印秘密的前提下，验证 remote config shape、
本地 ssh/scp、known_hosts/fingerprint、container digest 和必要 binary。

优先扩展现有 `real-scene` CLI；输出 canonical machine report，状态只能是
`ready`、`blocked-external-input` 或 `failed`。没有真实凭据时交付
`blocked-external-input` 是正确结果，不得生成假 ready receipt。

### P1-3：恢复/失败演练

对 submit、poll、fetch、checksum mismatch、远端 job 失败、网络中断与本地 journal
恢复做 fresh 演练。产物必须绑定 dataset/training config/container/result SHA；
失败 job 不得被 resume 为 succeeded。

### P1-4：fresh Windows canary

在 P0/P1-1/2/3 完成后，使用现有 rights-cleared canary 配置重跑：

```text
fetch/verify → fresh COLMAP → split/bundle → local Brush preview
→ import/chunk → acceptance aggregation
```

只提交机器报告、内容 SHA 和精简说明。预期仍是
`internal-only / preview-only / arbitrary / unaligned`；不得把本机 Brush 提升为
production 3DGS。

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
