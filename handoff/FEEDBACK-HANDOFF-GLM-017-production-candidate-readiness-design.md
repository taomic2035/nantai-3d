# FEEDBACK-HANDOFF-GLM-017 — Production candidate readiness 单入口设计与攻击面审计

Date: 2026-07-28
Owner: GLM-5.2
Reviewer: Codex
Status: DESIGN ONLY — 等待 Codex review，未实现任何代码

## Baseline

- 基线 commit：`d0b31ac fix: preserve remote public config evidence`（origin/main，
  工作树干净）
- 用户指定基线：`74b6c15`（已被 Codex `d0b31ac` 推进；GLM 只读，未改任何代码）
- 范围：repo-local 设计与审计，不依赖真实素材/GPU/SfM
- 交付物：本文件；不碰 Codex 的 release/Viewer/CI 文件，不实现代码

## 目标

为首个真实 scene candidate 补齐"单入口 Production readiness 预检"的设计与攻击面
审计。当前 `pipeline.production_external_inputs` 只是孤立 blocked-report CLI，
尚未接入真实 intake/caller；需要一个 fail-closed、不复制验证逻辑、不联网/不训练/
不发布/不自动宣称 rights-cleared 或 accepted 的 readiness 合同。

---

## Step 1：只读盘点与调用边界

### 1.1 七模块边界表

| 模块 | 职责 | 现有 validator（可复用，禁止复制） | 调用方状态 | exit code 约定 |
|---|---|---|---|---|
| [pipeline/production_external_inputs.py](file:///d:/vibecoding/nantai/pipeline/production_external_inputs.py) | 6 项 external-input blocked 报告，canonical/content-addressed/secret-free | `compute_blocked_report_sha256`、`_assert_no_forbidden_content`、`_duplicate_keys`、`blocked_report_signing_bytes` | **孤立项**：仅被自身测试 [tests/test_production_external_inputs.py](file:///d:/vibecoding/nantai/tests/test_production_external_inputs.py) 导入，零生产 caller | CLI `main` 返回 0/1 |
| [pipeline/production_capture_inputs.py](file:///d:/vibecoding/nantai/pipeline/production_capture_inputs.py) | 照片/视频摄取 receipt 与 atomic staging | `materialize_production_capture_inputs` | **孤立项**：仅被自身测试导入，零生产 caller | 无独立 CLI |
| [cloud/remote_readiness_checker.py](file:///d:/vibecoding/nantai/cloud/remote_readiness_checker.py) | 远端 GPU 容器 host preflight（容器版本/镜像 digest/worker SHA/nvidia runtime） | `collect_remote_readiness_evidence`；schema `nantai.remote-readiness-evidence.v1` | 被 `run_remote_shell_preflight` 调用 | 0/1；**docstring 明确声明 "This is NOT a production readiness check"** |
| [pipeline/remote_shell_executor.py](file:///d:/vibecoding/nantai/pipeline/remote_shell_executor.py) | 远端 shell preflight、result bundle 校验、durable job | `run_remote_shell_preflight_from_path`、`verify_remote_result_bundle`、`verify_production_remote_result_bundle` | 被 `scripts/real_scene.py` 调用 | 0/1 |
| [pipeline/metric_alignment_evidence.py](file:///d:/vibecoding/nantai/pipeline/metric_alignment_evidence.py) | 米制对齐测量/policy/decision 三层 | `measure_metric_alignment`、`decide_metric_alignment`、`verify_metric_alignment_decision`（:683，重算并比对 measurement 与 decision） | 被 `real_scene_import` 调用 | 库函数，无 CLI |
| [pipeline/real_scene_import.py](file:///d:/vibecoding/nantai/pipeline/real_scene_import.py) | 真实场景导入、PLY 流式校验、receipt 重开复验 | `validate_real_scene_import_receipt`（:1396，重开每个 byte 并复验 import integrity gates）、`import_real_scene` | 被 `scripts/real_scene.py import` 调用 | 库函数 |
| [pipeline/viewer_acceptance.py](file:///d:/vibecoding/nantai/pipeline/viewer_acceptance.py) + [pipeline/real_scene_acceptance.py](file:///d:/vibecoding/nantai/pipeline/real_scene_acceptance.py) | Viewer capture v2 报告 + 人工视觉复核 | `verify_viewer_capture_report`（:889，要求 v2 报告）、`validate_human_visual_review`（:541）、`validate_real_scene_acceptance`（:2117） | 被 `scripts/real_scene.py accept` 调用 | 库函数 |

### 1.2 现有 CLI 入口

- [scripts/real_scene.py](file:///d:/vibecoding/nantai/scripts/real_scene.py)：target
  包括 `preflight-remote / fetch / sfm / train-preview / train-production / import /
  accept / serve / all`，**无单一 readiness 聚合入口**；每个 target 独立校验自己的
  入参，没有跨 target 的统一 external-input 预检。
- [make.py](file:///d:/vibecoding/nantai/make.py)：`real-scene SOURCE=... <subtarget>`
  薄适配，委托 `scripts.real_scene`；`REAL_SCENE_IMPORT_ROOT` 环境变量仅传给 import。

### 1.3 可复用 validator 清单（禁止复制验证逻辑）

| # | validator | 信任层级 | 复用方式 |
|---|---|---|---|
| 1 | `validate_capture_rights` ([pipeline/real_dataset.py:322](file:///d:/vibecoding/nantai/pipeline/real_dataset.py)) | rights clearance | readiness 调用并绑定 receipt SHA，不重写 |
| 2 | `run_remote_shell_preflight_from_path` ([pipeline/remote_shell_executor.py](file:///d:/vibecoding/nantai/pipeline/remote_shell_executor.py)) | host preflight | readiness 读取已发布 preflight report，不重跑 |
| 3 | `verify_metric_alignment_decision` ([pipeline/metric_alignment_evidence.py:683](file:///d:/vibecoding/nantai/pipeline/metric_alignment_evidence.py)) | metric alignment | readiness 校验已落地 decision，不重算 |
| 4 | `validate_real_scene_import_receipt` ([pipeline/real_scene_import.py:1396](file:///d:/vibecoding/nantai/pipeline/real_scene_import.py)) | import integrity | readiness 复验 receipt，不重导 |
| 5 | `verify_viewer_capture_report` ([pipeline/viewer_acceptance.py:889](file:///d:/vibecoding/nantai/pipeline/viewer_acceptance.py)) | viewer capture | readiness 校验 v2 报告，不重采 |
| 6 | `validate_human_visual_review` ([pipeline/real_scene_acceptance.py:541](file:///d:/vibecoding/nantai/pipeline/real_scene_acceptance.py)) | human review | readiness 校验已落地 review，不重评 |

readiness orchestrator 只做"是否已存在通过校验的产物"的聚合判断，**不重跑任何上述
validator 的实质计算**；禁止复制 canonical JSON / secret 检测 / 路径校验逻辑，必须
从原模块导入或共享 helper。

---

## Step 2：三方案比较与推荐

### 方案 A（推荐）：独立 readiness orchestrator

新增 `pipeline/production_readiness.py` + `scripts/production_readiness.py`，
单入口 `python scripts/production_readiness.py check --candidate <dir> [--json]`。
只读聚合 6 项现有 validator 产物，输出 canonical readiness report，不联网/不训练/
不发布/不复制验证逻辑。

### 方案 B：make.py 薄适配

在 `make.py` 增加 `readiness` target，委托现有 `scripts/real_scene.py` 子命令组合。

### 方案 C：仅文档

只补 `docs/manual/` 说明手动检查步骤，不新增代码。

### 比较矩阵

| 维度 | A 独立 orchestrator | B make.py 薄适配 | C 仅文档 |
|---|---|---|---|
| secret/path 泄露面 | **最小**：单文件可审计，复用现有 `_assert_no_forbidden_content` | 中：需在 make.py 注入环境，泄露面扩散到 24 个 target 共享入口 | 低（无代码）但无强制 |
| 信任提升风险 | **最低**：readiness 报告 Literal-locked 为 `preview/unknown`，不得宣称 ready/accepted | 高：make.py 已有 `real-scene` target，易与生产 target 混用造成信任混淆 | 无强制，人工易误判 |
| 跨平台 | **最好**：独立 CLI 可单独做 Windows/py3.11 CI 门 | 中：make.py 已是跨平台入口，但 readiness 与生产 target 共用 dispatch | 无 CI 门 |
| 复验成本 | **最低**：readiness report 自带 content SHA，任何字段变化都改变 report_sha256 | 中：需复算多个子命令产物 | 高：全人工，不可复现 |
| 攻击面隔离 | **最好**：orchestrator 不接受 network/训练/发布参数，fail closed | 差：make.py dispatch 与 `train-production`/`serve` 同一入口 | N/A |
| 与现有 orphan 模块关系 | **可整合**：A 可让 `production_external_inputs` 的 blocked report 成为 readiness report 的一个子视图，消除 orphan | 不能整合 orphan | 不能整合 |

### 推荐：方案 A

理由：
1. 攻击面最小且可独立审计；readiness 报告与生产 closure 物理隔离。
2. 可复用 6 项现有 validator 而不复制逻辑。
3. 可整合现有 orphan 模块（`production_external_inputs` / `production_capture_inputs`）
   作为 readiness 的子视图，消除 caller 缺口。
4. 跨平台 CI 门可独立于生产 runtime runner。
5. Codex review 边界清晰：只看一个新文件 + 一份合同。

---

## Step 3：具体合同

### 3.1 CLI flags

```
python scripts/production_readiness.py check
    --candidate <dir>              # 真实 scene candidate 根目录（必填）
    [--rights-receipt <path>]      # 已发布的 capture rights receipt JSON
    [--preflight-report <path>]    # 已发布的 remote shell preflight report
    [--metric-decision <path>]     # 已发布的 metric alignment decision
    [--import-receipt <path>]      # 已发布的 real scene import receipt
    [--viewer-report <path>]       # 已发布的 viewer capture v2 报告
    [--human-review <path>]        # 已发布的人工视觉复核
    [--output <path>]              # readiness report 写出路径（no-replace）
    [--json]                       # 输出 canonical JSON 到 stdout
    [--max-bytes <int>]            # 单文件 bounded streaming 上限，默认 64 MiB
```

约束：
- 所有 `--*-receipt/--*-report/--*-decision/--*-review` 参数必须是**相对路径**
  且 POSIX 风格；拒绝绝对路径、反斜杠、symlink/junction。
- `--candidate` 必须存在且为目录；拒绝 symlink/junction。
- 不接受 `--remote-host`、`--ssh-key`、`--train`、`--publish`、`--serve` 等
  network/训练/发布参数。
- 缺省行为：未提供任何 `--*-path` 时，所有 6 项 requirement 状态为 `missing`，
  输出 blocked readiness report，exit code 2。

### 3.2 canonical schema / version

```json
{
  "schema": "nantai.production-readiness.v1",
  "state": "blocked" | "preview-only",
  "candidate_sha256": "<64 hex of candidate dir manifest>",
  "requirements": [
    {
      "requirement_id": "capture-rights",
      "state": "missing" | "unknown" | "present-unverified",
      "source_sha256": "<64 hex or null>",
      "reason_code": "<closed enum>"
    }
  ],
  "report_sha256": "<64 hex>",
  "generated_at_epoch_ms": <int>
}
```

- `schema` Literal-locked 为 `"nantai.production-readiness.v1"`。
- `state` Literal-locked 为 `"blocked"` 或 `"preview-only"`；**永远不得出现**
  `"ready"`、`"production"`、`"accepted"`、`"rights-cleared"`、`"metric-aligned"`。
- `requirement_id` 闭集（见 3.3）。
- `report_sha256` = SHA-256 of canonical signing bytes（排除 `report_sha256`
  字段本身），canonical JSON 使用 `sort_keys=True, ensure_ascii=True,
  separators=(",",":"), allow_nan=False` + 末尾 `\n`。
- `generated_at_epoch_ms` 必须是有限整数；用于防重放，不参与 `report_sha256`。

### 3.3 requirement 状态机

闭集 `RequirementId`（6 项，对齐现有 validator，不发明新 id）：

| requirement_id | 对应 validator | present 条件 |
|---|---|---|
| `capture-rights` | `validate_capture_rights` | 已绑定 rights receipt SHA |
| `remote-preflight` | `run_remote_shell_preflight_from_path` | 已绑定 preflight report SHA |
| `metric-alignment` | `verify_metric_alignment_decision` | 已绑定 metric decision SHA |
| `real-scene-import` | `validate_real_scene_import_receipt` | 已绑定 import receipt SHA |
| `viewer-capture` | `verify_viewer_capture_report` | 已绑定 v2 report SHA |
| `human-review` | `validate_human_visual_review` | 已绑定 human review SHA |

状态机：

```
missing ──(operator 提供路径)──► present-unverified ──(validator pass)──► present-unverified
   │                                     │
   │                                     └──(validator fail)──► invalid (exit 3)
   │
   └──(probe 无法判定)──► unknown (exit 4)
```

- `present-unverified` 是**最高可达**状态；readiness 永远不得把任何 requirement
  提升为 `verified`/`accepted`/`measured`。
- validator pass 只意味着"该产物的内部一致性已通过"，不意味着"真实场景已完成"。
- validator fail → `invalid`（exit 3），区别于 `missing`（exit 2）和
  `unknown`（exit 4）。

### 3.4 exit code

| code | 含义 |
|---|---|
| 0 | 所有 6 项 `present-unverified` 且 validator 全 pass（**仍不等于 Production ready**） |
| 2 | 至少一项 `missing` |
| 3 | 至少一项 `invalid`（validator fail / SHA drift / path drift） |
| 4 | 至少一项 `unknown`（无 invalid） |
| 5 | canonical bytes round-trip 失败 / schema 违例 / forbidden statement |
| 6 | secret-like content 检测到 |
| 7 | absolute path / symlink / junction / backslash 检测到 |
| 8 | TOCTOU（读取过程中文件变化） |
| 1 | 其他未分类错误 |

约束：exit code 单调；`invalid` 优先于 `missing` 优先于 `unknown`。任何非 0 退出
都不写出 readiness report 到 `--output`（fail closed，no partial publish）。

### 3.5 边界约束（no-secret / no-absolute-path / no-symlink / no-replace）

1. **no-secret**：复用 `production_external_inputs._SECRET_PATTERN` 扫描所有路径
   字段和 report payload；命中则 exit 6，不回显 secret 值。
2. **no-absolute-path**：所有 `--*-path` 参数必须相对；命中 `/abs` 或
   `C:\...` 则 exit 7。
3. **no-symlink / no-junction**：用 `getattr(path, "is_symlink", lambda: False)()`
   和 `getattr(path, "is_junction", lambda: False)()`（py3.11 兼容）拒绝；
   命中则 exit 7。
4. **no-replace**：`--output` 使用 `publish_file_noreplace`（来自
   `pipeline.durable_io`），已存在则 exit 1。
5. **no-backslash**：路径字段含 `\` 则 exit 7。
6. **no-network**：orchestrator 不发起任何 socket / HTTP / SSH 连接；不导入
   `requests`/`paramiko`/`fabric`。
7. **no-training / no-publish**：不接受也不传播任何训练/发布参数。
8. **no-auto-claim**：`_FORBIDDEN_STATEMENTS` 闭集（`ready`/`verified-production`/
   `metric-aligned`/`release-allowed`/`rights-cleared`/`gpu-available`/
   `viewer-accepted`）扫描 report payload；命中则 exit 5。

### 3.6 缺输入 vs invalid vs unknown 的区别

| 情况 | 判定 | exit |
|---|---|---|
| operator 未提供某 `--*-path` | `missing` | 2 |
| 提供了路径但文件不存在 / 不 readable | `invalid`（reason `path-not-found`） | 3 |
| 提供了路径但 SHA 不匹配 / validator fail | `invalid`（reason `sha-drift` / `validator-fail`） | 3 |
| 提供了路径但 probe 无法判定（如 preflight 超时标记） | `unknown`（reason `probe-inconclusive`） | 4 |
| 提供了路径但 duplicate key / schema 违例 | `invalid`（reason `schema-violation`） | 3 |

---

## Step 4：TDD 矩阵（12 项，可直接落地）

| # | 测试名 | 场景 | 期望 exit | 关键断言 |
|---|---|---|---|---|
| 1 | `test_missing_all_requirements` | 不提供任何 `--*-path` | 2 | 6 项全 `missing`，`report_sha256` 仍存在，report 不写入 `--output` |
| 2 | `test_partial_present_unverified` | 提供 3/6 路径且 validator pass | 2 | 3 项 `present-unverified`、3 项 `missing`，`state=blocked` |
| 3 | `test_validator_pass_real_receipt` | 用真实 fixture 构造合法 import receipt，validator pass | 0 或 2 | 该 requirement 为 `present-unverified`，`source_sha256` 绑定 |
| 4 | `test_validator_fail_sha_drift` | 篡改 receipt 字节使 SHA 不匹配 | 3 | 该 requirement `invalid`，reason `sha-drift`，report 不写入 |
| 5 | `test_duplicate_requirement_key` | 构造含重复 `requirement_id` 的 candidate manifest | 5 | canonical bytes round-trip fail，exit 5 |
| 6 | `test_sha_path_drift` | validator 产物存在但路径与 candidate manifest 声明不一致 | 3 | reason `path-drift` |
| 7 | `test_symlink_junction_rejected` | `--candidate` 或 `--*-path` 指向 symlink/junction | 7 | exit 7，不读字节，py3.11 兼容（`getattr` fallback） |
| 8 | `test_toctou_detection` | 在 streaming digest 期间替换文件字节 | 8 | exit 8，report 不写入 |
| 9 | `test_large_file_bounded_streaming` | 单个 `--*-path` > 64 MiB | 0/2/3 之一 | 不一次性 `read_bytes()`，分块流式 digest，`--max-bytes` 触顶则 `invalid` reason `size-limit` |
| 10 | `test_windows_python311_compatible` | 在 Windows + py3.11 运行 | 0/2 | `Path.is_junction` 缺失走 fallback，不 `AttributeError` |
| 11 | `test_secret_non_echo` | `--rights-receipt` 文件内容含 `password=xxx` | 6 | exit 6，stdout/stderr 不回显 `xxx`，只输出 `secret-like content detected` |
| 12 | `test_deterministic_bytes` | 同一 candidate + 同一 6 路径两次运行 | 0/2 | 两次 `report_sha256` 完全一致；`generated_at_epoch_ms` 不参与 signing bytes |

补充约束：
- 每个测试必须断言 `--output` 在非 0 exit 时不被创建（no partial publish）。
- 测试不得联网；所有 fixture 为 modeled data，`trust_effect=none`。
- 测试不得声明"Production ready"；docstring 必须标注
  `modeled-contract-not-real-release`。

---

## Step 5：现有 production_external_inputs 陈旧语义与 caller 缺口审计

### 5.1 caller 缺口（代码证据）

证据：`grep -r "from pipeline.production_external_inputs\|import production_external_inputs"`
只命中 [tests/test_production_external_inputs.py](file:///d:/vibecoding/nantai/tests/test_production_external_inputs.py)
（:36, :50, :1046, :1073）。`grep -r "from pipeline.production_capture_inputs\|import production_capture_inputs"`
只命中 [tests/test_production_capture_inputs.py](file:///d:/vibecoding/nantai/tests/test_production_capture_inputs.py)
（:8）。

结论：**两个模块都是 orphan**，零生产 caller。`scripts/real_scene.py` 和
`make.py` 都不导入它们。`production_external_inputs` 的 blocked report CLI
是"自产自销"——只有自己的测试消费它。

建议：
- 方案 A 的 readiness orchestrator 应**消费** `BlockedExternalInputsReport`
  作为 readiness report 的一个子视图（`capture-rights`/`remote-preflight` 等
  requirement 的 `missing` 默认值来源），从而消除 orphan。
- 不删除 `production_external_inputs`，但其 `main` CLI 应标注
  "legacy blocked-report emitter, prefer scripts/production_readiness.py"，
  由 Codex 决定是否在 readiness 接入后弃用。

### 5.2 陈旧语义逐项审计

#### 5.2.1 `RequirementId` 闭集与 v2 演进脱节

证据：[pipeline/production_external_inputs.py:59-76](file:///d:/vibecoding/nantai/pipeline/production_external_inputs.py)
定义 6 项 `RequirementId`，其中 `VIEWER_HUMAN_ACCEPTANCE = "viewer-human-acceptance"`
是**单一 slot**。但实际 [pipeline/viewer_acceptance.py:889-896](file:///d:/vibecoding/nantai/pipeline/viewer_acceptance.py)
的 `verify_viewer_capture_report` **强制要求 v2 报告**（`"production Viewer capture requires a v2 report"`），
且 [pipeline/real_scene_acceptance.py:541](file:///d:/vibecoding/nantai/pipeline/real_scene_acceptance.py)
有独立的 `validate_human_visual_review`。把 viewer capture 与 human review 合并
为单一 `viewer-human-acceptance` 掩盖了"v2 报告 pass ≠ human review pass"的信任
边界。

建议：readiness `RequirementId` 拆分为 `viewer-capture` 与 `human-review` 两项
（见 3.3 表），对齐实际 validator。`production_external_inputs` 的旧 enum 保留但
标注 deprecated。

#### 5.2.2 `reason_code` 闭集未覆盖 v2 validator 失败模式

证据：[pipeline/production_external_inputs.py:93-110](file:///d:/vibecoding/nantai/pipeline/production_external_inputs.py)
的 `ReasonCode` 只有 `OPERATOR_INPUT_BOUND_BUT_UNVERIFIED` 等粗粒度原因，没有
`sha-drift`、`path-drift`、`schema-violation`、`size-limit`、`probe-inconclusive`
等 readiness 需要的失败原因。

建议：readiness report 使用独立的 `reason_code` 闭集（见 3.6 表），不复用
`production_external_inputs.ReasonCode`。旧闭集保留，避免破坏现有测试。

#### 5.2.3 canonical JSON / secret / forbidden-statement 逻辑被复制

证据：`production_external_inputs.py` 内部定义了 `_canonical_json_bytes`（:251）、
`_duplicate_keys`（:268）、`_assert_no_forbidden_content`（:304）、
`_SECRET_PATTERN`（:127）、`_FORBIDDEN_STATEMENTS`（:115）。
`cloud/remote_readiness_checker.py:38-43` 也定义了自己的 `_SECRET_PATTERNS`
和 `_duplicate_keys`（:50）。两处独立实现，逻辑漂移风险已存在。

建议：readiness orchestrator **必须 import** 这些 helper 而非重写。若
`production_external_inputs._assert_no_forbidden_content` 当前是私有函数，
建议 Codex 在 readiness 接入时将其提升为模块级公共 helper
（`pipeline.production_external_inputs.assert_no_forbidden_content`），供
readiness 复用。本设计不实现该重构，仅标注为前置依赖。

#### 5.2.4 `PRODUCTION_DATASET` 的 rights-clearance 声明与实际 validator 脱节

证据：[pipeline/production_external_inputs.py:62-68](file:///d:/vibecoding/nantai/pipeline/production_external_inputs.py)
docstring 声明 rights-clearance 由 `pipeline.real_dataset.validate_capture_rights`
推导，但该模块的 `RequirementEntry` 只有 `rights_receipt_content_sha256` 字段，
**不绑定** `validate_capture_rights` 的实际输出。caller 缺口（5.1）意味着这个
"声明"从未被任何代码验证过。

建议：readiness 的 `capture-rights` requirement 必须实际调用
`validate_capture_rights`（或读取其已发布的 receipt 并校验 SHA），而非只绑定
一个游离的 `rights_receipt_content_sha256`。

#### 5.2.5 exit code 约定不一致

证据：`production_external_inputs.main` 返回 0/1；
`scripts/real_scene.py` 各 target 返回 0/非零但不区分 missing/invalid/unknown；
`cloud/remote_readiness_checker.py` 返回 0/1。三套约定不一致，caller 无法区分
"缺输入"与"输入损坏"。

建议：readiness 采用 3.4 表的 8 段 exit code，并要求 `scripts/real_scene.py`
在调用 readiness 后原样传播 exit code（由 Codex 决定是否在 real_scene.py 增加
`readiness` target）。

#### 5.2.6 TOCTOU / bounded streaming 未覆盖

证据：`production_external_inputs._build_blocked_report` 只处理内存对象，
不读取磁盘文件；`validate_real_scene_import_receipt` 有 `_stream_regular_digest`
（:1418）但 `production_external_inputs` 没有等价保护。readiness 需要读取多个
磁盘产物，必须 bounded streaming + TOCTOU 检测。

建议：readiness 复用 `real_scene_import._stream_regular_digest` 的模式（分块
+ reopen 校验），不重新发明。

### 5.3 审计结论

`production_external_inputs` 当前是**设计良好但未接入**的孤立模块：它的
canonical/secret-free/content-addressed 设计是 readiness 的良好基础，但：

1. `RequirementId` 闭集与 v2 validator 脱节（5.2.1）；
2. `reason_code` 闭集粒度不足（5.2.2）；
3. 验证逻辑被复制（5.2.3）；
4. rights-clearance 声明未落地（5.2.4）；
5. exit code 不一致（5.2.5）；
6. 缺 TOCTOU / streaming（5.2.6）；
7. 零生产 caller（5.1）。

**禁止凭测试绿声明 Production**：`tests/test_production_external_inputs.py`
通过只证明 blocked report 自洽，不证明任何真实 scene 已 ready。readiness
orchestrator（方案 A）是消除这些缺口的最小路径。

---

## 风险清单

| # | 风险 | 缓解 |
|---|---|---|
| 1 | readiness 被误用为"Production ready 证明" | `state` Literal-locked 为 `blocked`/`preview-only`；`_FORBIDDEN_STATEMENTS` 扫描；docstring 强标注 `not-a-production-readiness-proof` |
| 2 | 复制 validator 逻辑导致漂移 | 强制 import 6 项现有 validator；TDD #12 验证 deterministic bytes |
| 3 | secret 通过路径字段泄露 | `no-secret` 边界 + TDD #11 验证 non-echo |
| 4 | TOCTOU 绕过 SHA 校验 | bounded streaming + reopen 校验 + TDD #8 |
| 5 | py3.11 `Path.is_junction` 缺失 | `getattr` fallback + TDD #10 |
| 6 | readiness 与 `make.py real-scene train-production` 混用 | readiness 不接受训练/发布参数；exit code 单调；Codex 决定是否在 make.py 暴露 readiness target |
| 7 | `production_external_inputs` 重构破坏现有测试 | 本设计不实现代码；重构由 Codex review 后执行；旧 enum 保留 deprecated |
| 8 | candidate dir 含 symlink 导致路径逃逸 | `no-symlink/no-junction` 边界 + TDD #7 |

## 推荐接口（供 Codex review）

```python
# pipeline/production_readiness.py（设计，未实现）

def check_production_readiness(
    *,
    candidate: Path,
    rights_receipt: Path | None = None,
    preflight_report: Path | None = None,
    metric_decision: Path | None = None,
    import_receipt: Path | None = None,
    viewer_report: Path | None = None,
    human_review: Path | None = None,
    max_bytes: int = 64 * 1024 * 1024,
) -> ProductionReadinessReport:
    """单入口 readiness 预检。

    只读聚合 6 项现有 validator 产物，不联网/不训练/不发布/不复制验证逻辑。
    返回 canonical、content-addressed、secret-free 的 readiness report。
    任何 validator fail 或边界违例都 raise ProductionReadinessError。
    """
```

```python
# scripts/production_readiness.py（设计，未实现）

def main(argv: list[str] | None = None) -> int:
    """CLI 入口。exit code 见合同 3.4 表。"""
```

## Trust boundary

本工单只交付设计文档，不实现代码，不改变任何信任状态。真实场景五门状态不变：
采集/SfM/GPU/米制对齐/Viewer QA 均未完成。`scene_trust_effect=none` 保持不变。
本工单不授权创建 tag 或 Release。

## Next

等待 Codex review 本设计。若 Codex 接受方案 A，GLM 可在下一工单实现
`pipeline/production_readiness.py` + `scripts/production_readiness.py` + 12 项
TDD 测试，路径限定提交，不碰 Codex 的 release/Viewer/CI 文件。
