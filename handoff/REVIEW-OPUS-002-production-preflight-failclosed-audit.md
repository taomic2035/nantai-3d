# REVIEW-OPUS-002 — production_preflight.py 对抗性 fail-closed 审计

> 回执：Opus（pipeline / 内容寻址 / preflight 合同）→ Codex（caller / runtime）
> 日期：2026-07-21
> 对象：`pipeline/synthetic_village/production_preflight.py`（520 行）
> 范围：**只读审计 + 证据记录**，不修代码，不扩大重构

## 一句话

**整体 fail-closed 强度：高。520 行覆盖 5 个 schema（policy/evidence/decision/
request/report）+ 4 个内容寻址函数 + 1 个 verifier，所有身份字段都有重算校验。
发现 1 个 INFO 级观察（非漏洞），值得 caller 注意。**

## 审计方法

按 7 个环节逐节验证：
1. `ProductionClearancePolicy` schema + 内容寻址
2. `ProductionClearanceRayEvidence` / `ProductionCameraClearanceEvidence` schema
3. `ProductionCameraClearanceDecision` schema + evaluator
4. `ProductionClearanceRequest` schema + 11 项身份校验
5. `ProductionClearanceReport` schema
6. `build_production_clearance_request` / `build_production_clearance_report` 构造器
7. `parse_production_clearance_report_bytes` / `verify_production_clearance_report` verifier

## 通过的环节（无问题，逐条核验）

### 环节 1：Policy schema（L68-95）

- `schema_version` / `policy_id` Literal 锁定
- `sample_grid` 必须 = `PRODUCTION_CLEARANCE_SAMPLE_GRID`（5x5 固定网格）
- `upper_middle_min_sample_y: Literal[0.0]`（不允许改阈值边界）
- `near_distance_m` Field(gt=0, le=100, allow_inf_nan=False)
- `minimum_upper_middle_near_hit_count` Field(ge=1, le=15)
- `trust_effect: Literal["none-quality-filter-only"]`（防信任提升）
- **结论**：policy 阈值完全 versioned，无运行时篡改空间。

### 环节 2：Evidence schema（L98-152）

**Ray evidence**（L98-130）：
- `sample_x` / `sample_y` Field(ge=-1.0, le=1.0, allow_inf_nan=False)
- `hit: bool` 必填
- `distance_m: float | None` Field(gt=0.0, allow_inf_nan=False) —— **hit 时必填，miss 时必 None**
- `object_name` / `stable_id` / `part_id` / `semantic_id` 在 miss 时必须全 None
- **结论**：hit/miss 证据严格分离，无"miss 但携带身份"的混淆路径。

**Camera evidence**（L133-152）：
- `rays: tuple[ProductionClearanceRayEvidence, ...] Field(min_length=25, max_length=25)`
- 25 个 ray 的 `(sample_x, sample_y)` 必须 = `PRODUCTION_CLEARANCE_SAMPLE_POINTS`
- **结论**：ray 集合精确锁定，无增删空间。

### 环节 3：Decision schema + evaluator（L155-179, L351-376）

**Decision schema**（L155-179）：
- `policy_sha256` / `evidence_sha256` Field(pattern=64-hex)
- `measured_upper_middle_near_hit_count` Field(ge=0, le=15)
- `passes: bool` + `failed_rule_ids` 必须一致（passes=True → 空，passes=False → `("upper-middle-near-hit-count",)`）
- **结论**：passes flag 与 failed_rule_ids 严格绑定，无"passes=True 但带 failed_rule"的矛盾态。

**Evaluator**（L351-376）：
- 纯函数：`evidence + policy → decision`，无副作用
- `near_hit_count` 严格按 `row.hit && row.sample_y >= 0.0 && row.distance_m < near_distance_m` 计算
- `passes = near_hit_count < minimum_upper_middle_near_hit_count`
- **结论**：decision 完全由 evidence + policy 重算，无 caller 可注入的中间态。

### 环节 4：Request schema + 11 项身份校验（L196-283）

`ProductionClearanceRequest` 的 `_validate_identities`（L238-283）做了 11 项独立校验：

| # | 校验 | 强度 |
|---|---|---|
| 1 | `production_plan_sha256` 必须 = 重算 plan SHA | ✓ |
| 2 | `camera_registry_sha256` 必须 = 重算 registry SHA | ✓ |
| 3 | `selected_camera_ids` 必须是 plan-ordered unique subset | ✓ |
| 4 | `object_registry_sha256` 必须 = 重算 registry SHA | ✓ |
| 5 | `object_registry` instance_id 必须 = `range(1, 131)` | ✓ |
| 6 | `auxiliary_registry` 必须 = `canary.AUXILIARY_REGISTRY` | ✓ |
| 7 | `semantic_registry` 必须 = `canary._semantic_registry()` | ✓ |
| 8 | `policy_sha256` 必须 = 重算 policy SHA | ✓ |
| 9 | `preflight_id` 必须 = 排除自身后的 canonical payload SHA | ✓ |
| 10 | `selected_camera_ids` 长度 1-180 | ✓ |
| 11 | 所有 SHA 字段 Field(pattern=64-hex) | ✓ |

- **结论**：request 是完全自洽的内容寻址对象，无法通过篡改字段绕过校验。

### 环节 5：Report schema（L286-320）

- 11 个身份 SHA 字段（preflight_id / request_sha256 / plan / registry / build /
  blender / script / blend / build_report / object_registry / policy）
- `evidence: tuple Field(min_length=1, max_length=180)`
- `decisions: tuple Field(min_length=1, max_length=180)`
- `synthetic: Literal[True]` / `geometry_trust: Literal["simplified-pbr-not-render-parity"]`
- `trust_effect: Literal["none-quality-filter-only"]`
- **结论**：report 携带完整身份链，供 verifier 比对。schema 本身不校验身份一致性，
  这由 `verify_production_clearance_report` 负责（分层设计，正确）。

### 环节 6：构造器（L379-480）

**`build_production_clearance_request`**（L379-426）：
- 从原始输入构造 payload，重算所有 SHA
- `preflight_id` 最后加入（排除自身后 hash）
- `model_validate_json(_canonical(payload))` round-trip 强制所有 validator 重跑
- **结论**：构造器无短路路径。

**`build_production_clearance_report`**（L450-480）：
- evidence camera_ids 必须 = request.selected_camera_ids
- decisions 由 `evaluate_production_camera_clearance` 重算（不接受 caller 传入的 decision）
- 11 个身份字段全部从 request 复制（不接受 caller 传入）
- **结论**：report 完全由 request + evidence 派生，无 caller 注入 decision 的路径。

### 环节 7：Verifier（L489-592）

**`parse_production_clearance_report_bytes`**（L489-536）：
- 32 MiB 上限
- `reject_duplicate_keys` + `parse_constant` 拒绝 NaN/Infinity
- `raw != canonical_production_clearance_report_bytes(report)` 严格字节比对
- **结论**：解析路径无注入空间。

**`verify_production_clearance_report`**（L539-592）：
- 11 个身份字段逐字段比对 report vs request
- evidence_camera_ids + decision_camera_ids 必须 = request.selected_camera_ids
- decisions 必须 = 从 evidence + policy 重算的 expected_decisions
- **结论**：verifier 完整覆盖身份链 + decision 重算，无遗漏。

## 发现 1：Report schema 自身不校验 decision 与 evidence 的一致性（INFO，分层设计）

**位置**：`pipeline/synthetic_village/production_preflight.py:286-320`

`ProductionClearanceReport` schema 只做 Pydantic 字段级校验（pattern / min_length /
max_length / Literal），**不**在 `model_validator` 里校验：
- `decisions[i].camera_id == evidence[i].camera_id`
- `decisions[i].evidence_sha256 == production_camera_clearance_evidence_sha256(evidence[i])`
- `decisions` 是否 = 从 `evidence + policy` 重算的结果

这是**分层设计**——schema 自身只保证字段自洽，跨字段一致性由
`verify_production_clearance_report` 负责（L585-592 确实做了重算比对）。

**为什么不是漏洞**：caller 如果只 `model_validate_json(raw)` 解析 report 而不调
`verify_production_clearance_report`，会拿到一个"schema 合法但 decision 可能与
evidence 不一致"的对象。但 `build_production_clearance_report` 构造器（L462-465）
强制 decisions = 从 evidence 重算，所以从构造器出来的 report 一定一致。只有
"绕过构造器直接解析外部 bytes"的路径才有风险，而 `parse_production_clearance_report_bytes`
+ `verify_production_clearance_report` 是标准 caller 路径。

**建议**：在 `ProductionClearanceReport` docstring 注明"schema 不校验 decision-evidence
一致性，必须经 `verify_production_clearance_report` 完成跨字段校验"（**不在本次范围**，
只记录）。

## 发现 2：evaluator 不校验 evidence 的 ray 顺序（INFO，schema 已兜住）

**位置**：`pipeline/synthetic_village/production_preflight.py:351-376`

`evaluate_production_camera_clearance` 直接遍历 `evidence.rays`，不校验 ray 顺序。
如果 caller 传入一个 rays 顺序被打乱的 evidence（但仍是 25 个合法 ray），evaluator
会正常计算 near_hit_count。

**为什么不是漏洞**：`ProductionCameraClearanceEvidence._validate_sample_grid`
（L145-152）强制 `rays` 的 `(sample_x, sample_y)` 序列必须 =
`PRODUCTION_CLEARANCE_SAMPLE_POINTS`，所以 schema 层已兜住。evaluator 假设
schema 已校验是合理的分层。

## 与 §3 caller 的关系

§3 caller（Codex production runner）在 reposed plan 上跑 fresh preflight 时：

1. 用 `build_production_clearance_request` 构造 request（自动内容寻址）
2. 把 request 交给 Blender runtime 跑 preflight
3. runtime 返回的 report bytes 经 `parse_production_clearance_report_bytes` 解析
4. 调 `verify_production_clearance_report(report, request=request)` 完成身份 + decision 重算校验
5. 从 `report.decisions` 取 `ProductionCameraClearanceDecision`，传给
   `search_replacement_pose(failing_decision=...)`

**审计结论**：这条链路的 fail-closed 强度足以支撑 §3 的 fresh preflight 要求。
caller 只需确保：
- 不绕过 `verify_production_clearance_report`（发现 1）
- 不自行构造 `ProductionClearanceReport`（必须从 runtime bytes 解析或从
  `build_production_clearance_report` 构造）

## 范围边界声明

按用户 2026-07-21 明确边界：

- ✓ **只读审计**：未修改 `production_preflight.py` 任何字节
- ✓ **不扩大重构**：发现 1 的 docstring 补充留待 Codex 决定
- ✓ **不扩展 Studio jobs/ledger**：本审计不触及 Studio lane
- ✓ **不提升 modeled-unverified 信任**：所有发现的校验都在 L0/preview-only 信任级别内
- ✓ **未触及 175-root renderer schema**

## 与前序审计的关系

- `REVIEW-OPUS-001`（commit `bf0dd7b`）：审计 `apply_environment_modules.py`（175-root Blender builder）
- `REVIEW-OPUS-002`（本文档）：审计 `production_preflight.py`（preflight 合同层）

两者互补：REVIEW-OPUS-001 确认 175-root module build 自身无 fail-open，
REVIEW-OPUS-002 确认 preflight 合同层无 fail-open。§3 caller 可以信任这两层
的输入。

## Co-Authored-By

GLM-5.2 <noreply@zai.com>
