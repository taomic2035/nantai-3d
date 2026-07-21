# FEEDBACK-CODEX-017 — Task 5 §3 v2 quality caller 已接通

> 交接：Codex（production runner / Studio）→ Opus（Phase 3）  
> 日期：2026-07-21  
> 实现提交：`a2bf63ba551e623108129ff09c1ab9666be2558b`

## 结论

Opus 等待的 §3 **v2 post-render quality caller 阻塞已解除**：正式 Windows/Mac
production runner 现在会从已完成 journal 帧构建
`ProductionFrameQualityRequestV2/ReportV2`，以 canonical JSON 持久化并随 journal
状态原子刷新；Studio 能从两类正式 runner 根目录投影阶段、逐相机规则和证据 SHA。

Opus 可以进入其声明的 Phase 3（Blender runtime script + 构造器 + 对应 runner）。
为避免共享工作树冲突，Opus 不要修改：

- `pipeline/synthetic_village/local_production_runner.py`
- `pipeline/studio_server.py`
- `scripts/synthetic_village.py`
- 对应 Codex tests

## 已交付合同

1. runner 只消费 journal 中 `verified/rejected` 的完成帧；每帧必须有 exact-six
   artifact、runtime report SHA 和 runtime 内测得的 `layer_statistics`。
2. request 绑定 plan、camera registry、build、render、Blender、renderer script、
   blend、build report、object/semantic registry、journal、逐帧 artifact 与 policy。
3. report 由 host 对 raw integer counts 重算八条规则；不是运行时自报结论。
4. journal 进入 rendering、失败、重试或完成时，sidecar 同步刷新；若没有完成帧，
   旧 sidecar 被清除，不能留下与新 journal SHA 不一致的可用证据。
5. Studio 同时发现：
   - `.nantai-studio/synthetic-village/hybrid-v3/local-production-renders/<render-id>`
   - `.nantai-studio/sv-prod-win/<build-report-sha>/<render-id>`
6. 修复 CLI canonical policy loader 的两个阻塞：不存在的 JSON constant 回调，以及
   strict tuple model 不能从普通 Python list dict 验证的问题。

## fresh Windows Blender 实证

使用 canonical Task 4 candidate v2 policy，对当前 130-instance Windows v2 build
实际复渲 `camera-ground-route-034`：

| 字段 | 实测值 |
|---|---|
| render ID | `299b939768c14a80a7292033d060e1a77cbf057ec2151a54d7322f8f5017bcbd` |
| journal SHA（模型内绑定） | `cd5d9e53d8290cda5c35d77b19379b49dcc2d41b63d38b68650dba5d23c4eaed` |
| journal 文件 SHA | `a1ea50f1dc0012eb3174570ca12f26af36453ba357d9d1f7ba1574cbd111c13d` |
| quality request 文件 SHA | `2232a652e55a8237e07a2c0c089ebb583cc132cef2fc565947c939aa72478396` |
| quality report 文件 SHA | `237d099e570d0fe6360f67c21fe1475b427a14f05b033b6253a5e1dd5db86ff2` |
| runner 结果 | `rendered_count=1`, `rejected_count=1` |
| Studio 阶段 | preflight passed → rendering completed → post-render-quality rejected |
| 实测失败规则 | `near-instance-dominance` |
| trust effect | `none-quality-filter-only` |

## 验证

- Python：`128 passed, 9 skipped`
- Studio 前端合同：`16 passed`
- ruff：通过
- `git diff --check`：通过
- fresh Windows Blender：退出码 0；真实六层帧、request、report、journal 均已落盘

## 尚未宣称完成

这份回执只解除 Phase 3 的 caller 阻塞，**不表示 Task 5 §3 整体验收完成**：

1. fresh 实证仍是 130-instance build，不是 175-root `EnvironmentModulePlan` build；
2. `environment_module_build_report_sha256` 尚未由 175-root 正式 build 输入到本 caller；
3. topology-aware replacement pose 尚未完成 fresh preflight + 六层复渲；
4. before/after RGB measured comparison 尚未交付；
5. `req-5-pose-quality-fail-closed` 继续保留，不能提前解锁。

## Opus Phase 3 回传要求

请回传 175-root build 的 canonical build report、环境模块 report SHA、实际 Blender
runtime report 和构造器/runner 的 fail-closed 测试。Codex 随后把该 SHA 接入正式
render identity，并完成 Studio 最终端到端复核。
