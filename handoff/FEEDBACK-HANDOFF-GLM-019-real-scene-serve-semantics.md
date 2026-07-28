# FEEDBACK-HANDOFF-GLM-019 — Real-scene `serve` stage semantics audit (v2)

Date: 2026-07-28
Owner: GLM-5.2
Reviewer: Codex
Status: REVIEWED — v2 accepted; implementation and tests owned by Codex

## v2 修订勘误

| # | v1 偏离 | v2 修正 |
|---|---|---|
| 1 | 建议删除 `scripts/real_scene.py serve` target | 保留 `serve` target；改为 authoritative-acceptance → resolve import → 前台启动 Studio 的组合入口 |
| 2 | 建议另起 `serve-studio` 新 target | 删除该建议；唯一 serve 入口为 `scripts/real_scene.py serve` |
| 3 | CLI 合同允许 `make.py real-scene serve` fail closed | 保留 `make.py real-scene serve` 子目标；与 `scripts/real_scene.py serve` 同合同 |
| 4 | 暗示可走 `make.py serve` 启动 Studio | `make.py serve` 仍可独立用于纯 Studio HTTP（无重验），但 **不** 是 real-scene serve；real-scene serve 必须经过 snapshot + resolve + studio_server.main 前台调用 |

## Baseline

- 基线：origin/main @ `6c10e47`（GLM-018 status 已合并；工作树干净）
- 范围：仅审计 `serve` 语义；不写代码、不提交、不 push
- 输出：本文档 + 三方案比较 + 推荐项；Codex review 后按 v2 合同实现

---

## 1. 现状证据（精确文件/行）

### 1.1 `serve` 在 real-scene journal 中

[.pipeline/real_scene_runner.py:47-55](file:///d:/vibecoding/nantai/pipeline/real_scene_runner.py)
`StageName` Literal 闭集含 `"serve"`。

[.pipeline/real_scene_runner.py:636-656](file:///d:/vibecoding/nantai/pipeline/real_scene_runner.py)
`_dependency(stage)`：`serve` 返回 `"accept"`（serve 依赖 accept）。

[.pipeline/real_scene_runner.py:658-662](file:///d:/vibecoding/nantai/pipeline/real_scene_runner.py)
`_all_stages()` 返回 6 阶段链 `("fetch","sfm",training,"import","accept","serve")`。

[.pipeline/real_scene_runner.py:1153-1190](file:///d:/vibecoding/nantai/pipeline/real_scene_runner.py)
`run()`：`"serve"` 是合法 target；对 production-acceptance 角色也跑 `_preflight_control_points`；最后调用 `_run_stage("serve", ...)`。

[.pipeline/real_scene_runner.py:1255](file:///d:/vibecoding/nantai/pipeline/real_scene_runner.py)
`if target in {"import", "accept", "serve"} and self.source.role == "production-acceptance"` 触发 control-points 预检。

[.pipeline/real_scene_runner.py:1192-1334](file:///d:/vibecoding/nantai/pipeline/real_scene_runner.py)
`snapshot_stages`：5 阶段链显式排除 serve（GLM-018 已落地）；serve 不出现在 `RealSceneStageSnapshot.stages`。

### 1.2 `serve` 在 `RealScenePipelineOperations.execute` 永远 blocked

[.pipeline/real_scene_operations.py:1425-1453](file:///d:/vibecoding/nantai/pipeline/real_scene_operations.py)
`execute` 显式分支只覆盖 `fetch/sfm/train-preview/train-production/import/accept`；serve 落入末尾 fallback：

```python
return StageExecution(
    state="blocked",
    artifacts=(),
    reason=f"{stage} integration is not available before its task",
)
```

后果：在 production 路径上 `runner.run("all")` 即便 accept 已 completed，第 6 阶段 serve 必然写入 blocked receipt 并抛 `RealSceneBlockedError`；`all` 在真实生产 operations 下永不 completed。

### 1.3 测试 stub 默认让 serve completed，掩盖了上述冲突

[.tests/test_real_scene_runner.py:35-86](file:///d:/vibecoding/nantai/tests/test_real_scene_runner.py)
`_Operations.execute` 对任意 stage（含 serve）默认返回 `completed` + 一个 artifact。

[.tests/test_real_scene_runner.py:243](file:///d:/vibecoding/nantai/tests/test_real_scene_runner.py) `runner.run("serve", resume=True)`；[L245-250](file:///d:/vibecoding/nantai/tests/test_real_scene_runner.py) 断言 serve 目录下出现 2 个 receipt（resume 失败记录）。

[.tests/test_real_scene_runner.py:253-264](file:///d:/vibecoding/nantai/tests/test_real_scene_runner.py) `test_internal_canary_all_uses_preview_not_production` 断言 `runner.run("all")` 返回 `receipt.stage == "serve"`；这是当前 repo 中唯一显式声称 `all` 结束于 serve 的契约测试。

### 1.4 `serve` 名称被三条入口复用（语义冲突）

| 入口 | 命令 | 实际行为 | 证据 |
|---|---|---|---|
| make.py serve | `python make.py serve` | 启动 `pipeline.studio_server` HTTP loopback（8000），可挂载 `REAL_SCENE_IMPORT_ROOT` | [.make.py:225-243,476](file:///d:/vibecoding/nantai/make.py) |
| runtime serve | `python make.py serve`（runtime 包内） | 启动 `pipeline.studio_server` HTTP loopback（8000），**不**接受 import root | [.release/production-runtime-runner.py:54-65,70](file:///d:/vibecoding/nantai/release/production-runtime-runner.py) |
| real-scene serve | `python scripts/real_scene.py serve` 或 `make.py real-scene ... serve` | `RealSceneRunner.run("serve")` → journal stage → 上述 blocked fallback | [.scripts/real_scene.py:37-48,110-114](file:///d:/vibecoding/nantai/scripts/real_scene.py), [.make.py:65,388,458](file:///d:/vibecoding/nantai/make.py) |

冲突点：同一个名字 `serve` 在 make.py / runtime-runner 表示"启动 Studio HTTP"，在 real_scene.py 表示"运行 journal serve stage"，二者产物、退出码、副作用模型完全不同。

### 1.5 Studio 已有 fail-closed 的 import 挂载入口

[.pipeline/studio_server.py:2412-2456](file:///d:/vibecoding/nantai/pipeline/studio_server.py)
`_load_verified_reconstruction_mount` 已对 `--real-scene-import-root` 做完整安全检查：拒绝 link-like、必须 regular dir、resolve strict、`validate_real_scene_import_receipt`、强制 `source_role=="production-acceptance"`、`geometry_usability=="metric-aligned"`、`target_units=="meters"`，并以 `MappingProxyType` 投影只读 bindings。

[.pipeline/studio_server.py:4590-4621](file:///d:/vibecoding/nantai/pipeline/studio_server.py)
`make_server` 接受 `real_scene_import_root` 参数并构造只读 `reconstruction_mount`；不在 journal 写 receipt。

[.pipeline/real_scene_paths.py:59-80](file:///d:/vibecoding/nantai/pipeline/real_scene_paths.py)
`resolve_latest_production_import` 已暴露为 CLI：解析最新 completed production import，输出 canonical JSON 路径。**仅** production-acceptance；canary 不支持。

### 1.6 文档承诺

- [.docs/production-v1-status.md:56](file:///d:/vibecoding/nantai/docs/production-v1-status.md)：明确 GLM-019 任务是 "`serve` stage 语义审计"
- [.docs/real-data-workflow.md:72](file:///d:/vibecoding/nantai/docs/real-data-workflow.md)："生产 `import/accept/serve` 还必须提供 `CONTROL_POINTS=` 和 `GEO_ORIGIN=`" — 把 serve 与 import/accept 并列为 production 阶段
- [.docs/manual/reconstruction-setup.md:325,388-389](file:///d:/vibecoding/nantai/docs/manual/reconstruction-setup.md) 与 [.docs/manual/production-runtime-release.md:126,136](file:///d:/vibecoding/nantai/docs/manual/production-runtime-release.md)：所有面向用户的 `serve` 都是 `make.py serve`（Studio HTTP），从未指 real-scene journal serve
- [.docs/releases/1.0-preview.2.md:64,89](file:///d:/vibecoding/nantai/docs/releases/1.0-preview.2.md)：发布版只暴露 `make.py serve`

### 1.7 runtime runner 显式屏蔽 REAL_SCENE_IMPORT_ROOT

[.release/production-runtime-runner.py:14-29](file:///d:/vibecoding/nantai/release/production-runtime-runner.py)
`PRIVATE_OVERRIDE_NAMES` 把 `REAL_SCENE_IMPORT_ROOT` 从子进程 env 中删除。

[.tests/test_production_runtime_runner.py:102-119](file:///d:/vibecoding/nantai/tests/test_production_runtime_runner.py)
已有测试断言 runtime `serve` 不接受 `REAL_SCENE_IMPORT_ROOT`，命令裸跑 `pipeline.studio_server`。

---

## 2. 方案比较

### 方案 A（推荐）：serve 从 journal 移除；`scripts/real_scene.py serve` 变为 authoritative-acceptance + resolve + 前台启动 Studio 的组合入口

**状态机**:
- `StageName` 删除 `"serve"`；`_all_stages()` 返回 5 阶段链 `("fetch","sfm",training,"import","accept")`。
- `run("all")` 最后阶段是 `accept`；accept completed 即 `all` completed。
- `RealScenePipelineOperations.execute` 的 serve fallback 删除（serve 不再走 journal execute 路径）。
- `_dependency`、`_validate_runtime_inputs` 中 serve 引用全部删除。
- `scripts/real_scene.py serve` **保留**：改为只接受 `--source/--workspace/--run-id`，按 §3.2 合同执行 snapshot → resolve → studio_server.main 前台调用，不写 StageReceipt。
- `make.py real-scene serve` 保留为 `scripts/real_scene.py serve` 的等价子目标（同合同）。
- `make.py serve`（独立 target，非 real-scene 子目标）行为不变：纯 Studio HTTP，无重验，可选 `REAL_SCENE_IMPORT_ROOT` env；不是 real-scene serve。
- `release/production-runtime-runner.py serve` 不变（已剥离 `REAL_SCENE_IMPORT_ROOT`，裸跑 `pipeline.studio_server`）。

**`scripts/real_scene.py serve` 新合同**:
1. argparse 限定 `--source/--workspace/--run-id`；其他可选参数（`--media-root`/`--rights`/`--policy`/`--control-points`/`--geo-origin`/`--remote-config`/`--preflight-report`/`--viewer-*`/`--human-*`/`--chunk-size`/`--resume`/`--retry`）若传入即 fail closed。
2. 调 `snapshot_real_scene_stages(source, workspace_base=workspace, run_id=run_id)`。
3. 强制 `snapshot.state == "accepted-from-authoritative-decision"`；否则 stderr 固定文本 `real-scene serve not accepted`，exit 2。
4. 调 `resolve_latest_production_import(source, workspace_base=workspace, run_id=run_id)` 获取 `ResolvedProductionImport`；非 production-acceptance source 或 import 不存在即 fail closed。
5. 以前台调用 `pipeline.studio_server.main`，固定 `--host 127.0.0.1 --port 8000 --real-scene-import-root <resolved.import_root>`；不 fork、不 detach；studio_server 退出码即 serve 退出码。
6. **不**产生 StageReceipt；**不**声称 Viewer QA；**不**接受 `--viewer-policy`/`--viewer-report`/`--human-review-policy`/`--human-visual-review`。

**优点**: 与 GLM-018 5-stage readiness 链一致；保留 `scripts/real_scene.py serve` 作为用户主入口（不破坏调用方）；serve 现在是真实可完成动作（Studio HTTP 启动），不是永远 blocked 的 journal stage；与 [.docs/manual/reconstruction-setup.md:388-389](file:///d:/vibecoding/nantai/docs/manual/reconstruction-setup.md) 描述的 `REAL_SCENE_IMPORT_ROOT` 流程一致；不引入新 target 名称。

**缺点**: `StageName` 删除 `"serve"` 是破坏性变更；`test_internal_canary_all_uses_preview_not_production`（L253-264）和 `test_resume_revalidates_transitive_prerequisite_bytes`（L231-250）需重写——前者断言 `receipt.stage == "serve"` 需改为 `"accept"`，后者 `runner.run("serve", resume=True)` 改为 `runner.run("accept", resume=True)`；`scripts/real_scene.py serve` 行为从"journal stage"变为"组合入口"，CLI 合同变更需在 release notes 注明。

### 方案 B：bounded boot-probe receipt

新增 schema `nantai.real-scene-serve-boot-probe.v1`：snapshot 之外记录"已重验 accept completed → 启动 Studio → 监听 loopback TCP 握手 → 关闭 → 写 receipt"。receipt 仅含 `boot_probe_state ∈ {ready, unreachable}`，不含真人 Viewer QA 决策。

**优点**: serve 仍是 journal stage，向后兼容 stub 测试。

**缺点**: 引入新的 receipt 类型与新的"成功"语义，仍不等于真实 Viewer QA；`production_release_allowed` 仍由 accept 决定，boot-probe receipt 易被误读为额外放行门；增加 schema 表面；与 studio_server 已有的 import mount 安全入口重复。

### 方案 C：维持现状

**为何不可接受**:
1. production 路径 `runner.run("all")` 永远在 serve 处 blocked，"all"承诺失真。
2. tests 用 stub 掩盖了 production 永远 blocked 的事实，contract test 与现实分歧。
3. `serve` 在 make.py / runtime-runner / real_scene.py 三个入口含义冲突，文档 [docs/real-data-workflow.md:72](file:///d:/vibecoding/nantai/docs/real-data-workflow.md) 把 journal serve 与 Studio serve 混为一谈。
4. snapshot 5-stage 链已排除 serve，与 `_all_stages` 6-stage 不一致，留下长期歧义。

---

## 3. 推荐状态机与 CLI 合同（方案 A v2）

### 3.1 状态机

```
StageName = Literal[
    "fetch", "sfm", "train-preview", "train-production",
    "import", "accept",
]
_all_stages() -> ("fetch", "sfm", training, "import", "accept")
_dependency(stage) -> 删除 serve 分支
RealScenePipelineOperations.execute -> 删除 serve fallback
_validate_runtime_inputs -> 删除 serve 分支
```

### 3.2 CLI 合同

| 入口 | 行为 |
|---|---|
| `python scripts/real_scene.py serve --source S --workspace W --run-id R` | snapshot(state=accepted) → resolve_latest_production_import → 前台 `pipeline.studio_server --host 127.0.0.1 --port 8000 --real-scene-import-root <import_root>`；不写 receipt；不接受其他可选参数 |
| `python make.py real-scene SOURCE=S serve WORKSPACE=W RUN_ID=R` | 等价于上一行（make.py 转译为 scripts/real_scene.py serve） |
| `python make.py serve` | **独立 target**：纯 Studio HTTP（loopback 8000），无重验；可选 `REAL_SCENE_IMPORT_ROOT` env；不是 real-scene serve |
| `release/production-runtime-runner.py serve` | 不变（已剥离 `REAL_SCENE_IMPORT_ROOT`，裸跑 `pipeline.studio_server`） |
| `scripts/real_scene.py status` | 不变（GLM-018 落地） |
| `scripts/real_scene.py <fetch/sfm/train-*/import/accept/all>` | 不变（journal stage 仍走 `run_real_scene`；`all` 末尾改为 accept） |

### 3.3 serve 失败语义

| 情况 | exit | stderr |
|---|---|---|
| snapshot.state != accepted | 2 | `real-scene serve not accepted` |
| source 非 production-acceptance（resolve 失败） | 1 | `real-scene serve invalid` |
| import 不存在 / receipt 损坏 / TOCTOU | 1 | `real-scene serve invalid` |
| studio_server 非零退出 | 透传 studio_server 退出码 | 透传 studio_server stderr |
| 传入非法可选参数 | 1 | `real-scene serve invalid` |

### 3.4 兼容迁移

- `StageName` 删除 `"serve"`：`RealSceneBlockedError` / `RealSceneStatusError` 已覆盖所有非法 target；老 receipt 目录 `receipts/serve/*.json` 在已有 workspace 中仍可存在但永远不被遍历，可在迁移工具中归档为 `legacy-serve/`。
- `run("serve")` 与 `run("all")` 末尾行为变更：`run("serve")` 抛 `RealSceneBlockedError`（未知 target）；`run("all")` 在 production 中由 blocked 变为 completed（末尾 accept）。
- `_validate_runtime_inputs`（[scripts/real_scene.py:110-114](file:///d:/vibecoding/nantai/scripts/real_scene.py)）的 `{"import","accept","serve"}` 集合改为 `{"import","accept"}`。
- `make.py REAL_SCENE_TARGETS` 保留 `serve`（现在映射到组合入口，不是 journal stage）。
- `scripts/real_scene.py _TARGETS` 保留 `serve`；`_run_serve` 新增实现（见 §3.2）。

---

## 4. 实现验证矩阵

| # | 测试 | 期望 |
|---|---|---|
| 1 | `test_all_stops_at_accept_for_canary` | `runner.run("all")` 返回 `receipt.stage == "accept"`，不调用 serve |
| 2 | `test_all_stops_at_accept_for_production` | production role + control points，`all` 返回 accept |
| 3 | `test_runner_rejects_serve_target` | `runner.run("serve")` 抛 `RealSceneBlockedError`/`ValueError`（StageName 已不含 serve） |
| 4 | `test_no_serve_receipt_written_after_all` | `runner.run("all")` 后 `receipts/serve/` 不存在或为空 |
| 5 | `test_legacy_serve_receipt_ignored_by_snapshot` | 老工作树若存在 `receipts/serve/*.json`，`snapshot_real_scene_stages` 仍只遍历 5 stage |
| 6 | `test_resume_revalidates_transitive_prerequisite_bytes_uses_accept` | 原 L231-250 改为 `runner.run("accept", resume=True)` |
| 7 | `test_cli_serve_requires_source_workspace_run_id` | `scripts/real_scene.py serve` 缺 `--source`/`--workspace`/`--run-id` → argparse error |
| 8 | `test_cli_serve_rejects_extra_arguments` | `scripts/real_scene.py serve --media-root ...` → stderr `real-scene serve invalid`，exit 1 |
| 9 | `test_cli_serve_rejects_when_snapshot_not_accepted` | snapshot.state != accepted → stderr `real-scene serve not accepted`，exit 2；不调用 studio_server |
| 10 | `test_cli_serve_invokes_studio_server_with_resolved_import_root` | accept 完成后，serve 前台调用 `pipeline.studio_server --host 127.0.0.1 --port 8000 --real-scene-import-root <resolved>`；不写 StageReceipt |
| 11 | `test_cli_serve_rejects_canary_source` | canary source（无 production import）→ stderr `real-scene serve invalid`，exit 1 |
| 12 | `test_make_py_serve_still_starts_studio_server` | 回归 make.py 独立 serve 行为不变（纯 Studio HTTP，无重验） |
| 13 | `test_runtime_runner_serve_still_strips_import_root` | 回归 [.tests/test_production_runtime_runner.py:102-119](file:///d:/vibecoding/nantai/tests/test_production_runtime_runner.py) |
| 14 | `test_real_scene_paths_resolver_still_returns_production_import` | 回归 [.pipeline/real_scene_paths.py](file:///d:/vibecoding/nantai/pipeline/real_scene_paths.py) |
| 15 | `test_studio_server_reconstruction_mount_fail_closed` | 回归 [.pipeline/studio_server.py:2412-2456](file:///d:/vibecoding/nantai/pipeline/studio_server.py) |

---

## 5. 风险清单

| # | 风险 | 缓解 |
|---|---|---|
| 1 | 破坏既有 `runner.run("all")` 调用方期望 6 阶段 | 仅 [.tests/test_real_scene_runner.py:253-264](file:///d:/vibecoding/nantai/tests/test_real_scene_runner.py) 一处契约；同步改测试 |
| 2 | 老 workspace 残留 `receipts/serve/` 误读为完成 | snapshot 永远只遍历 5 stage；可在迁移文档说明归档 |
| 3 | `scripts/real_scene.py serve` 行为从 journal stage 变为组合入口 | release notes 注明；新合同严格 fail closed，不接受旧参数 |
| 4 | `make.py real-scene serve` 既有脚本调用失败 | make.py 子目标转译到新 `scripts/real_scene.py serve` 合同；CI 立即暴露 |
| 5 | 删除 `StageName` serve 影响外部 import | `StageName` 是 pipeline 内部 Literal；无外部包 import |
| 6 | `serve` 组合入口被误读为 Viewer QA / release gate | §6 non-goals 明确禁止；`_run_serve` 不写 receipt，不调用 `validate_real_scene_acceptance` 之外的 viewer/human validator |
| 7 | studio_server 前台调用阻塞 CLI 测试 | 测试中用 monkeypatch 替换 `pipeline.studio_server.main` 为 stub；不真实启动 HTTP |
| 8 | `resolve_latest_production_import` 在 serve 时 TOCTOU | 已有 [.pipeline/real_scene_paths.py](file:///d:/vibecoding/nantai/pipeline/real_scene_paths.py) canonical 校验；serve 在 resolve 后立即传给 studio_server，窗口极小 |

---

## 6. Non-goals

- **不**修改 [.pipeline/studio_server.py](file:///d:/vibecoding/nantai/pipeline/studio_server.py) 已有的 import mount 安全入口；本审计确认其已 fail-closed。
- **不**修改 [.release/production-runtime-runner.py](file:///d:/vibecoding/nantai/release/production-runtime-runner.py) 的 serve（runtime 包内 `make.py serve` 语义不变）。
- **不**引入 boot-probe receipt 或任何新的"serve 完成"语义；boot probe **不得**提升为真实 Viewer QA 或 Production release 证据。
- **不**改 GLM-018 `RealSceneStageSnapshot` schema；5-stage 链保持不变。
- **不**触碰 Codex WIP 文件（README/CHANGELOG/release docs/CI/Viewer/web data）。
- **不**授权任何 tag 或 release；真实重建五门状态不变。
- **不**实现代码、不 commit、不 push；本工单仅审计 + 推荐方案。

---

## 7. 推荐与下一步

**推荐方案 A v2**：`serve` 从 `StageName` / journal / `_all_stages` 移除；`run("all")` 结束于 accept；`scripts/real_scene.py serve` 与 `make.py real-scene serve` 改为 authoritative-acceptance + resolve + 前台 studio_server 组合入口；`make.py serve`（独立 target）与 runtime runner serve 行为不变。

**理由**:
1. 与 GLM-018 5-stage readiness 链对齐，消除 schema/journal/operations 三者不一致。
2. 保留 `scripts/real_scene.py serve` 作为用户主入口，不破坏调用方（v1 删除 serve target 的建议被 Codex 否决）。
3. serve 现在是真实可完成动作（Studio HTTP 启动），不是永远 blocked 的 journal stage；与 [.docs/manual/reconstruction-setup.md:388-389](file:///d:/vibecoding/nantai/docs/manual/reconstruction-setup.md) 描述的 `REAL_SCENE_IMPORT_ROOT` 流程一致。
4. Studio 已有 fail-closed 的 import mount 安全入口（[.pipeline/studio_server.py:2412-2456](file:///d:/vibecoding/nantai/pipeline/studio_server.py)），serve 组合入口复用该入口，无需在 journal 内重复。
5. 方案 B 引入的 boot-probe receipt 在 release gate 上没有承载任何新决策，且容易被误读为额外放行门。
6. 方案 C 不可接受：production `all` 永远 blocked，contract test 与现实分歧。

**实现回执**：Codex 已按方案 A v2 实施，并额外固定错误文本、拒绝 canary
serve、要求五个最新 completed receipt 构成同一 prerequisite SHA 链，并交叉核对
snapshot/import 的 source SHA 与 import receipt SHA；acceptance report 引用的
import-receipt SHA 也必须等于 accept prerequisite 所绑定的 import 输出。专项与相关
回归通过后才允许提交。

---

## Trust boundary

本工单只交付审计与设计推荐，不实现代码，不改变任何信任状态。真实场景五门状态不变：
采集/SfM/GPU/米制对齐/Viewer QA 均未完成。`scene_trust_effect=none` 保持不变。
本工单不授权创建 tag 或 Release；`scripts/real_scene.py serve` 组合入口（若实施）仅启动 Studio HTTP，不得提升为 real Viewer QA 或 Production release 证据。
