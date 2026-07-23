# FEEDBACK-HANDOFF-GLM-005 — P1 Caller Closure Receipt

> Date: 2026-07-23
> Owner: GLM lane (HANDOFF-GLM-002 Task 3, P0.3 + P1)
> Status: Implemented — Codex reviewed (REVIEW-CODEX-023); 4 findings fixed (commit `e587a23`); 2 canaries added
> Baseline: `HANDOFF-GLM-005-current-gap-and-priority.md` §3 P0.3 + P1
> Spec: `REVIEW-CODEX-022-glm-registration-training-trust-contracts.md`

## 完成：具体合同/调用方/测试

### P0.3 hardening（commit `4cad3ad`）

trusted prefix 判定从 `quality_accepted` 收紧为 `training_allowed`：

- `scripts/prepare_import.py`：`_validate_registration_quality` 返回值从
  3-tuple `(validated, quality_accepted, report_sha)` 改为
  4-tuple `(validated, quality_accepted, training_allowed, report_sha)`；
  trusted prefix 条件 `quality_accepted` → `training_allowed`。
- `tests/test_prepare_import_p03.py`：原
  `test_trusted_prefix_with_accepted_registration_quality` 重命名为
  `test_mock_registration_never_yields_trusted_prefix`，断言 mock engine
  registration 只产 content-only receipt，永不产 trusted prefix。

### P1 caller work（commit `5fe4882`）

| 文件 | 类型 | 用途 |
|---|---|---|
| `cloud/train_3dgs_nerfstudio.sh` | 重写 | 训练前 emit `training-request.json`，训练后 emit `training-result.json`；内联生成 capture manifest；捕获 trainer 版本（`ns-train --version`）、GPU env（`nvidia-smi`）、config SHA、log SHA（`tee` + sha256sum）、PLY header（gaussian_count / sh_degree）；失败训练（exit != 0，无 PLY）也 emit result manifest |
| `scripts/emit_registration_quality.py` | 新建 | CLI：从 COLMAP sparse dir + registration.json + policy 派生 `RegistrationQualityReport`，跑 `build_registration_quality_report()` + `validate_registration_quality()` round-trip self-check |
| `scripts/emit_training_provenance.py` | 修改 | `--ply` 从 required 改为 optional，使失败/中断训练（无 PLY）也能 emit result manifest；completed run（exit 0）仍要求 `--ply` |
| `tests/test_p1_canary_e2e.py` | 新建 | 9 个测试：3 个 happy-path content-only receipt（mock engine `training_allowed=False` by construction）+ 6 个 adversarial tamper-detection |

## 机器证据：命令、pass 数、内容 SHA

### 测试

```text
.venv\Scripts\pytest.exe tests/test_p1_canary_e2e.py tests/test_prepare_import_p03.py -q
==> 23 passed in 16.51s

.venv\Scripts\pytest.exe tests/test_training_provenance.py tests/test_registration_quality.py tests/test_reconstruct.py tests/test_registration.py -q
==> 177 passed in 3.71s
```

总计 **200 passed**，0 failed。

### Ruff

```text
ruff check scripts/emit_training_provenance.py scripts/emit_registration_quality.py scripts/prepare_import.py tests/test_p1_canary_e2e.py tests/test_prepare_import_p03.py
==> All checks passed!
```

### Commits

```text
5fe4882 feat(provenance): P1 caller — cloud emit + registration quality emit + canary
4cad3ad fix(provenance): trusted prefix requires training_allowed (not quality_accepted)
```

### Canary tamper-detection 覆盖

`tests/test_p1_canary_e2e.py::TestP1CanaryAdversarial`（6 测试，全部 fail-closed）：

1. `test_tampered_ply_fails_closed` — 篡改 PLY 字节 → 拒绝
2. `test_tampered_config_fails_closed` — 篡改 config.yml → 拒绝
3. `test_tampered_log_fails_closed` — 篡改 training.log → 拒绝
4. `test_tampered_quality_report_fails_closed` — 篡改 quality-report.json → 拒绝
5. `test_tampered_registration_json_fails_closed` — 篡改 registration.json → 拒绝
6. `test_tampered_input_images_fails_closed` — 篡改输入图片目录 → 拒绝

## 仍未完成：真实数据 / accepted SfM / 云训练 / 真实导入中的哪几项

按 `HANDOFF-GLM-005` §1 的四项「首个真实 3D 场景闭环」判定线，**四项均未交付**：

1. **真实 capture manifest** — 未交付。canary 用合成图片目录生成 manifest，
   非 EXIF GPS 实拍照片。
2. **accepted COLMAP registration-quality report** — 未交付。canary 用 mock
   engine（`training_allowed=False` by construction），从未跑过真实 COLMAP
   sparse 目录的 accepted report。
3. **closed cloud-training receipt 与真实训练 PLY** — 未交付。canary 用合成
   PLY 字节 + 合成 config/log；`cloud/train_3dgs_nerfstudio.sh` 重写后**未在
   真实云 GPU 实例上跑过**，未产出真实 `training-result.json`。
4. **import/alignment/chunk/Viewer 产物及真实画面 QA** — 未交付。

### 其它未完成项

- **Follow-up C**（Codex lane）：Studio/Viewer 显示 training provenance status，
  未开始。
- **pytest 环境**：TRAE 内置 Python 无 pytest；项目 `.venv` 可用（pytest 9.1.1）。
  本轮回执全部用 `.venv\Scripts\pytest.exe` 验证。
- **stale references**：`training_provenance.v1=` 在 docs/handoff 中的部分历史
  描述仍按旧 `quality_accepted` 逻辑写成「accepted registration quality」；
  本次只更新了 spec/plan 两份核心文档，handoff 历史文档未逐条改写。

## 信任边界：本轮最多证明什么，明确不能证明什么

### 本轮最多证明

- **caller 机制闭环**：cloud 脚本 → emit_training_provenance →
  emit_registration_quality → prepare_import 的端到端调用链在合成 artifacts
  上能正确产出三层 evidence（trusted / content-only / none）。
- **篡改检测**：六个关键字节集合（PLY / config / log / quality-report /
  registration-json / input-images）任一被篡改，prepare_import fail-closed。
- **mock engine 信任边界**：mock engine `training_allowed=False` by construction，
  永不产 trusted prefix——即使 registration quality accepted 也只产 content-only。
- **失败训练 emit**：`--ply` 可选后，失败/中断训练也能 emit result manifest
  供诊断，且 `training_status.state` 正确反映 exit_code + PLY 是否非空。

### 本轮明确不能证明

- **不能证明真实云训练**：`cloud/train_3dgs_nerfstudio.sh` 重写后未在真实
  NVIDIA GPU 实例上执行过；`nvidia-smi` / `ns-train` 调用路径未经实测。
- **不能证明真实照片重建**：canary 全部用合成图片/PLY。
- **不能证明 accepted SfM**：canary 用 mock engine，从未消费真实 COLMAP sparse。
- **不能证明米制或地理对齐**：trusted prefix 仍不隐含 metric / aligned /
  real-photos（`prepare_import.py` 不改 `metric_status` / `geo_aligned`）。
- **不能证明 P1 caller 在真实跨平台环境的字节一致性**：cloud 脚本里的
  `sha256sum` / `awk` / `tee` 行为依赖 cloud 实例的 coreutils 版本，未实测。

## 需 Codex review：文件和对抗用例

### 核心合同文件

1. `scripts/prepare_import.py` — `training_allowed` 4-tuple 返回值与
   trusted prefix 判定（commit `4cad3ad`）
2. `cloud/train_3dgs_nerfstudio.sh` — 内联 capture manifest 生成、
   emit 调用、退出码捕获（commit `5fe4882`）
3. `scripts/emit_registration_quality.py` — 新建 CLI 的接口与
   round-trip self-check（commit `5fe4882`）
4. `scripts/emit_training_provenance.py` — `--ply` 可选后的失败训练
   emit 路径（commit `5fe4882`）

### 对抗用例

`tests/test_p1_canary_e2e.py::TestP1CanaryAdversarial` 全部 6 个篡改用例
需 Codex 确认 fail-closed 行为符合 REVIEW-CODEX-022 的信任合同。

### 待 Codex 决策

- `cloud/train_3dgs_nerfstudio.sh` 的 cloud 实例实测由 Codex 还是 GLM 负责
  （本机无 NVIDIA GPU，无法实测）。
- Follow-up C（Studio/Viewer 显示 training provenance status）属 Codex lane，
  启动时机由 Codex 决定。
