# FEEDBACK-HANDOFF-GLM-004 — Cloud Training Provenance Handshake Design

> Date: 2026-07-23
> Owner: GLM lane (HANDOFF-GLM-002 Task 3)
> Status: Implemented — Codex reviewed (REVIEW-CODEX-022); P0 findings fixed; Follow-up A/B done, C pending (Codex lane)
> Priority: P1 (design-only at publication; implementation tracked in FEEDBACK-HANDOFF-GLM-006)

## 交付物

| 文件 | 用途 |
|---|---|
| `docs/superpowers/specs/2026-07-23-cloud-training-provenance-design.md` | 设计规格（schema、验证合同、信任派生、nerfstudio/Brush 非对称性） |
| `docs/superpowers/plans/2026-07-23-cloud-training-provenance.md` | TDD 计划（10 阶段，35+ 测试用例） |
| `handoff/FEEDBACK-HANDOFF-GLM-004-cloud-training-provenance-design.md` | 本回执 |

## 动机

`cloud/train_3dgs_nerfstudio.sh` 当前产出 `point_cloud.ply` 时**不绑定任何
provenance**：无输入 SHA、无 trainer 版本、无配置快照、无种子、无 CUDA 环境、
无训练日志 SHA。PLY 作为不透明黑箱通过 `prepare_import.py` 导入，诚实标注为
`sfm-local` / `preview-only`。但没有任何机制阻止操作者事后替换 PLY、声称不同
trainer、或隐藏失败运行。

本设计通过 canonical `training-request.json` + `training-result.json` 两个
content-addressed manifest，使训练运行在本地可验证、可审计、可篡改检测。

## 设计要点

### 1. 双 manifest 结构

- **`TrainingRequest`**（训练前发出）：绑定已验证输入 + 操作者意图
  - `input_bindings`：capture manifest SHA / registration JSON SHA /
    registration quality report SHA（Task 2）
  - `training_config`：trainer name + version + max_resolution + total_steps +
    **random_seed（required，无默认值）** + extra_config
- **`TrainingResult`**（训练后产出）：绑定实际输出 + 环境 + 日志
  - `actual_input_shas`：实际消费的输入 SHA（必须匹配 request）
  - `gpu_environment`：GPU name + memory + CUDA version + driver version
  - `output_bindings`：trained_ply SHA + config_yml SHA + training_log SHA
  - `training_status`：completed / failed / interrupted + exit_code + error_message
  - `training_log_sha256`：完整训练日志 SHA

### 2. 验证合同（content closure）

`validate_training_provenance(result, request, actual_ply_bytes)`：

1. **输入闭包**：result 的 `actual_input_shas` 必须完全匹配 request 的
   `input_bindings` SHA 集合
2. **请求绑定**：result 的 `request_canonical_sha256` 必须等于 request 的实际 SHA
3. **PLY 绑定**：`primary_ply_sha256` 必须出现在 `output_bindings` 中
4. **PLY 字节**：`sha256(actual_ply_bytes)` 必须等于 `primary_ply_sha256`（篡改检测）
5. **状态一致性**：failed 状态不能有非空 PLY SHA；completed 不能有 error_message

### 3. 信任派生

`TrainingTrust` 含 7 个独立布尔字段，`is_trustworthy = all(True)`：

| 字段 | 含义 |
|---|---|
| `content_closed` | validate_training_provenance 通过 |
| `inputs_verified` | 所有输入 SHA 匹配已验证 artefact |
| `registration_quality_passed` | Task 2 的 `training_allowed == True` |
| `trainer_identified` | trainer name + version 非空 |
| `seed_recorded` | random_seed 非 None |
| `log_bound` | training_log_sha256 非空 |
| `environment_captured` | gpu_environment 完整填充 |

**`is_trustworthy=True` 不意味着 metric/aligned/real-photos。** 它只证明内容闭包
和输入绑定一致——训练的必要但非充分条件。

### 4. nerfstudio vs Brush 非对称性

| | nerfstudio (cloud) | Brush (local) |
|---|---|---|
| 重跑 COLMAP | 是（`ns-process-data`） | 否（直接消费 workspace） |
| 坐标系 | re-center/rescale，**不在** local sparse 坐标 | 保留 workspace 坐标 |
| `splat_provenance` 适用性 | **不适用**（canary ratio=0.00x） | **适用** |

handshake 记录此区别（result 中的 metadata），但不自行运行几何检查——只记录
足够信息让下游代码知道哪些检查适用。

### 5. 与现有导入路径的关系

handshake **不替换** `SplatInput` 或 `prepare_import`：

1. 操作者运行修改后的 `cloud/train_3dgs_nerfstudio.sh`（Follow-up A），产出
   `training-request.json` + `training-result.json`
2. 操作者运行 `prepare_import.py --training-result training-result.json`
3. `prepare_import` 调用 `validate_training_provenance()`；若
   `is_trustworthy=True`，在 `CoordinateFrame.evidence` 中追加
   `training_provenance.v1=<result_sha>`——**不改变** `metric_status` 或
   `geo_aligned`（PLY 仍是 `sfm-local` / `preview-only`，直到对齐证据施加）
4. 若 `is_trustworthy=False`，`prepare_import` fail-closed，除非显式
   `--allow-unverified-training`（开发用）

## 与 HANDOFF-GLM-002 Task 3 / HANDOFF-OPUS-010 §6 的逐项对照

| 要求 | 设计位置 | 状态 |
|---|---|---|
| canonical `training-request.json` | `TrainingRequest` + LF canonical JSON | ✅ |
| canonical `training-result.json` | `TrainingResult` + LF canonical JSON | ✅ |
| 绑定 verified ingest/COLMAP 输入 | `TrainingInputBinding` (capture_manifest / registration_json / registration_quality_report / sparse_model_dir) | ✅ |
| trainer 名称和精确版本 | `TrainingConfig.trainer_name` (Literal) + `trainer_version` | ✅ |
| CUDA/GPU 环境 | `GpuEnvironment` (gpu_name, gpu_memory_mb, cuda_version, driver_version) | ✅ |
| 完整配置 | `TrainingConfig` (max_resolution, total_steps, seed, extra_config) | ✅ |
| 随机种子 | `TrainingConfig.random_seed` (required, no default) | ✅ |
| 导出命令 | 记录在 `output_bindings` (config_yml SHA)；export command 本身记录在 Follow-up A 的 cloud script | ✅ |
| PLY SHA/bytes/properties | `TrainingOutputBinding` (artifact_sha256, artifact_size_bytes, gaussian_count, sh_degree) | ✅ |
| 训练日志 SHA | `TrainingResult.training_log_sha256` | ✅ |
| 失败状态 | `TrainingStatus` (state, exit_code, error_message) | ✅ |
| 验证器只验证内容闭包 | `validate_training_provenance()` — 6 项闭包检查 | ✅ |
| 不把操作者/云端声称自动提升为 measured | `TrainingTrust` 无 metric/aligned 字段；evidence string 不改 `metric_status` | ✅ |
| 不修改 `cloud/train_3dgs_nerfstudio.sh` | Scope 明确禁止；Follow-up A 分离 | ✅ |
| 不触碰 Studio | Scope 明确禁止；Follow-up C 是 Codex lane | ✅ |
| 不伪造云 GPU 实测结果 | Canary plan 明确为合成机制证明 | ✅ |
| 消费 Task 2 的 registration quality contract | `registration_quality_passed` 消费 `training_allowed` | ✅ |

## 信任边界

- `is_trustworthy=True` 只证明训练运行的内容闭包和输入绑定一致，**不证明**：
  - PLY 视觉上正确
  - 几何是米制的
  - 照片是真实的
  - 相机覆盖对 3DGS 几何上充分
- 真实模型仍必须来自真实采集 + 合格 SfM（Task 2）+ 外部 GPU 训练产物（本 handshake）
- 合成 canary 只能证明机制工作，不能冒充真实云训练 acceptance

## 当前 GLM lane 状态

- **Task 1（SH rotation）**：已完成并推送（commit `4926d46`），等待 Codex review
- **Task 2（SfM quality gate 设计）**：已完成并推送（commit `039da69`），等待 Codex review
- **Task 3（本设计）**：设计完成，等待 Codex review

HANDOFF-GLM-002 的三项任务（Task 1 实现 + Task 2/3 设计）全部完成。GLM lane
现在等待 Codex review Task 1 的数值实现，以及 Task 2/3 的设计 review，才能推进
到实现阶段和 Follow-up 集成。
