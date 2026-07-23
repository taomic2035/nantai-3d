# FEEDBACK-HANDOFF-GLM-003 — Registration Quality Policy Design

> Date: 2026-07-23
> Owner: GLM lane (HANDOFF-GLM-002 Task 2)
> Status: Design complete — awaiting Codex review
> Priority: P1 (design-only; no runtime code modified)

## 交付物

| 文件 | 用途 |
|---|---|
| `docs/superpowers/specs/2026-07-23-registration-sfm-quality-policy-design.md` | 设计规格（schema、三态逻辑、模型枚举、验证合同） |
| `docs/superpowers/plans/2026-07-23-registration-sfm-quality-policy.md` | TDD 计划（9 阶段，30+ 测试用例，精确测试名与期望失败原因） |
| `handoff/FEEDBACK-HANDOFF-GLM-003-registration-quality-design.md` | 本回执 |

## 动机

`FEEDBACK-HANDOFF-OPUS-011-colmap-sfm-verified.md` 证明 COLMAP wrapper 真实运行，
但 2/20 registered images 被接受为合法 `RegistrationResult`，只有 `logger.warning`，
没有任何门阻止它流入训练。当前 `pipeline/registration.py` 的 coverage 信息只作为
evidence string 存在 `pose_frame.evidence` 中，且 `sparse/"0"` 目录名被当作质量证明。

本设计在 SfM domain 引入一个独立的、操作者显式提供阈值的 quality policy，将
"调用成功 / 覆盖不足 / 允许训练"三个状态分开为机器可验证字段。

## 设计要点

### 1. 三态决策（核心合同）

```
invocation_succeeded = 引擎产出非空 RegistrationResult（非崩溃/超时）
quality_accepted     = invocation_succeeded AND 所有阈值通过
training_allowed     = quality_accepted AND engine != "mock" AND capture_manifest_sha256 is not None
```

**Fail-closed 规则（training_allowed=False regardless of coverage）：**
- `engine == "mock"` — 合成注册永远不可训练
- `capture_manifest_sha256 is None` — 无绑定 capture manifest 不可审计
- `invocation_succeeded == False` — 崩溃/超时不可训练
- 任何 `rejection_reasons` 条目 — 显式拒绝不可被覆盖

### 2. 模型枚举取代 `sparse/"0"` 硬编码

当前 `colmap_register()` 硬编码 `sparse/"0"` 作为唯一模型。若 mapper 产出多个
connected-component models（`sparse/0`, `sparse/1`, ...），只有 `0` 被读取，其余被
静默丢弃。本设计引入 `SparseModelEnumeration`：

- 确定性枚举 `sparse/*/` 子目录，解析 `images.txt` + `points3D.txt`
- 选择规则：registered image count 最多 → tie break by point3d count → tie break by lowest index
- `largest_connected_model_share = selected_model_image_count / total_input_images`

### 3. 阈值全部由操作者显式给出（无默认值）

`RegistrationQualityPolicy` 的所有字段都是 required：
- `min_registered_count`
- `min_registered_ratio`
- `min_session_coverage_ratio`
- `max_unregistered_consecutive_run`
- `min_largest_connected_model_share`

**为什么无默认值**：默认阈值是隐式推荐。操作者必须显式声明"我要求至少 N 张注册
和 M% 覆盖率"——沉默不是同意。这也防止 2/20 这次运行隐式定义阈值。

### 4. Content-addressed 绑定

`RegistrationQualityReport` 绑定：
- `registration_json_sha256` — registration.json 字节的 SHA-256
- `capture_manifest_sha256` — 输入 capture manifest 的 SHA-256（None = 未提供）
- `policy_canonical_sha256` — policy 的 canonical JSON SHA-256

验证器 `validate_registration_quality()` 重算这三个 SHA 并与报告声称的比对；
重算 `quality_accepted` / `training_allowed` 并与报告声称的比对。
任何不一致 → `ValueError`（fail-closed，不信任自报布尔值）。

### 5. 与 `registration.json` 的关系

**不修改 `RegistrationResult` 或 `registration.json`。** quality report 是独立 artefact，
通过 SHA 引用 registration JSON。这保持了：
- `registration.json` 作为坐标信任根的稳定性（forced LF, stable digest）
- "COLMAP 测量了什么" vs "操作者要求了什么" 的 provenance 分离

## 与 HANDOFF-GLM-002 Task 2 要求的逐项对照

| HANDOFF 要求 | 设计位置 | 状态 |
|---|---|---|
| 阈值全部由操作者显式给出 | `RegistrationQualityPolicy` — all fields required, no defaults | ✅ |
| 至少包含最小注册图数、最小注册比例、最小 session 覆盖比例、允许的最大未注册连续段 | 5 个 threshold fields | ✅ |
| 不得从 2/20 反推 | 无默认值；设计文档明确禁止 | ✅ |
| `RegistrationQualityReport` 绑定 registration JSON SHA/bytes、输入 capture manifest SHA、policy canonical SHA | 3 个 SHA 绑定字段 | ✅ |
| 实际 engine、registered/total、per-session 指标 | `engine`, `registered_count`, `total_input_images`, `session_outcomes` | ✅ |
| 选中 COLMAP model identity 与拒绝原因 | `model_enumeration`, `rejection_reasons` | ✅ |
| 状态至少区分 `invocation_succeeded`、`quality_accepted`、`training_allowed` | 三态布尔字段 | ✅ |
| 缺报告、缺输入 SHA、覆盖 evidence 不可解析、mock、内容不匹配时 `training_allowed=false` | fail-closed 规则 §1 | ✅ |
| mapper 产出多个 sparse model 时确定性枚举并绑定选中 model | `SparseModelEnumeration` + `enumerate_sparse_models()` | ✅ |
| 计算 largest connected model share | `largest_connected_model_share` property | ✅ |
| 不得继续把目录名 `0` 当作质量证明 | 设计明确替代 hardcode；Follow-up A 实施替换 | ✅ |
| canonical JSON 必须 LF、跨平台稳定、Pydantic `extra=forbid` | §Canonical JSON contract | ✅ |
| 验证器重算内容 SHA，不信任报告自报的 `passed` | `validate_registration_quality()` | ✅ |
| canary 方案保留输入、命令 receipt、registration JSON 与 report 的 SHA | §Canary plan (TDD Phase 7) | ✅ |
| 合成 canary 只能证明机制，不能冒充真实采集 acceptance | §Canary plan + 信任边界 | ✅ |
| 计划按 TDD 拆为红灯、最小实现、绿灯、回归、提交五类步骤 | TDD Plan Phase 1–9 | ✅ |
| 列出精确测试名与期望失败原因 | 每个 test 用例的 Arrange/Expect/Why | ✅ |
| 本轮不修改 `pipeline/registration.py` 或 Studio | Scope 明确禁止；Follow-up A/B/C 分离 | ✅ |

## 信任边界

- `training_allowed=True` 只证明注册满足一份覆盖策略，**不证明**：
  - 照片是真实的
  - 相机覆盖对 3DGS 几何上充分
  - 尺度是米制的
  - 训练会产生高质量模型
- 它是训练的**必要但非充分**条件
- 真实模型与真实照片纹理仍必须来自真实采集 + 合格 SfM + 外部 GPU 训练产物

## 实现后的下一步（不在本轮范围）

- **Follow-up A**（GLM lane，Codex review 后）：修改 `colmap_register()` 调用
  `enumerate_sparse_models()` 并产出 `RegistrationQualityReport`，替换 `sparse/"0"` 硬编码
- **Follow-up B**（Codex lane）：Studio/Viewer 消费 `RegistrationQualityReport` 显示三态
- **Follow-up C**（GLM lane）：`scripts/prepare_import.py` / `reconstruct_local.py` 在进入
  训练前检查 `training_allowed`

## 当前 GLM lane 状态

- **Task 1（SH rotation）**：已完成并推送（commit `4926d46`），等待 Codex review
- **Task 2（本设计）**：设计完成，等待 Codex review
- **Task 3（云 GPU handshake）**：待 Task 1 review 通过后开始设计

GLM lane 不等待 Codex 的 Blender/Studio/Viewer 工作。在 Codex review Task 1/2 前，
GLM lane 可独立推进 Task 3 的设计准备。
