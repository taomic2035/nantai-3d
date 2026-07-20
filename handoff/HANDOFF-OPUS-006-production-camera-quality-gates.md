# HANDOFF-OPUS-006 — production 相机净空与坏帧门禁

> 发起：Codex（UX / visual-quality / audit lane）→ Opus（pipeline / planner /
> renderer / registry / journal lane）
> 日期：2026-07-20
> 优先级：高；它直接阻断 180-camera production capture 被诚实用于 3DGS 训练。

## 交办结论

请在 production camera plan / render pipeline 中补齐 req 5 的两层 fail-closed 质量门：

1. **预渲染几何净空门**：使用实际、已绑定身份的 Blender 场景几何检测近表面遮挡；
2. **后渲染六层坏帧门**：使用真实 RGB/depth/normal/instance/semantic/frame metadata
   检测全地面、近墙、狭窄天空、斜穿遮挡和低信息帧。

不能只实现一个固定距离阈值。Codex 已完成 180 台全量私有探针，证据见：

```text
handoff/REVIEW-CODEX-011-production-camera-clearance-180.md
```

## 已确认的坏机位

| camera | 机器证据 | 视觉结论 |
|---|---|---|
| `ground-route-010` | 上/中部 `15/15` 射线 `<1m`，中心 `0.432877m`，全部命中 lower bridge deck/parapets/piers | 严重遮挡，拒绝 |
| `ground-route-039` | 上/中部 `5/15` 射线 `<2m`，中心 `1.259315m`，命中 upper bridge deck/parapets/piers | 严重遮挡，拒绝 |
| `ground-route-034` | 无上/中部 `<2m` 命中，最小 `3.841703m` | RGB 仍被近坡面和斜穿木廊破坏，证明距离门会漏检 |

全量输入身份：

- plan SHA：
  `d5db85507a1f7bc4731e03c93d7b1232ddab7272dd5a52fd4d8df7bf6252a9f9`
- registry SHA：
  `9c8ad9b2bf299d51385822a2b40f071781d0c07e42aae6e1216887adb2563726`
- `.blend` SHA：
  `fa8cc4aabfe5049f2025e9d2ab34739c0914d87aa78a8fbda21ad86299cbebac`
- 4,500-ray 输出 SHA：
  `77f26573d9d1f7ea8a5cc2ec44a28d6f8f5b84d603745dee2cbcae6d88b5febf`

原始私有证据与可复跑脚本位于：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
```

## 必须满足的实现合同

### 1. 身份与来源

- 几何预检必须绑定 production plan SHA、camera registry SHA、build report SHA 和实际
  Blender scene SHA。
- 第一命中证据至少保留 camera ID、sample location、distance、object/stable/part/semantic
  ID；未知字段保持未知，不从名字补可信度。
- 使用现有唯一坐标转换：
  `c2w_blender=c2w_opencv@diag(1,-1,-1,1)`。

### 2. 策略与证据分离

- 原始射线/六层统计是 evidence；阈值是显式 operator policy，两者不得混写。
- policy 必须有稳定 ID/version，并进入 report/render identity。
- 本次 `5×5` 与 `<2m`/`5-of-15` 只可作为候选基线，不得未经分布与对照验证写成
  跨场景通用真理。
- 质量门只降低 training suitability；绝不提升 geometry trust、metric/aligned 或
  coverage 可信度。

### 3. 坏机位重排

- 对 `010`、`039` 做确定性重排，`034` 必须由六层门判定后重排或拒绝。
- 保持 production profile 为 180 台、排序稳定、camera ID 唯一、路径/环路覆盖不倒退。
- pose 改变后必须产生新的 camera registry digest、render ID 和 journal 身份；旧 journal
  不得被复用或覆盖。
- 重排不得靠文件名、camera ID 或 hardcoded 特判宣称通过；必须重新跑实际证据。

### 4. 后渲染质量门

至少从实际六层产物推导并公开：

- valid depth/normal/semantic/instance 的有效像素比例；
- 天空、地面及单一近表面占画面比例；
- 深度近端集中度与视野区域分布，避免下缘正常地面造成误杀；
- 单一 instance/part 对中心及上部视野的支配程度；
- 被拒 camera ID、规则 ID、实测值和失败原因。

RGB 可用于人工审计，但自动门不能只依赖未经版本化的颜色启发式。

## TDD / 验收

请先写失败测试，再实现。至少覆盖：

1. 当前 plan/build 下 `010`、`039` 被预检拒绝；
2. `034` 证明预检“未拒绝”不等于最终通过，必须等待正式帧质量证据；
3. 普通下部地面命中不会误杀正常 ground-route；
4. plan/build/registry 任一身份不匹配时 fail closed；
5. pose 重排导致 registry/render/journal 身份变化；
6. 重跑后 180 台完整、稳定、无重复中心，既有两个 route loop 与 group count 不倒退；
7. 所有新增报告明确 `synthetic=true`、
   `simplified-pbr-not-render-parity`、`trust_effect=none`。

请提供最小针对性测试与完整相关门禁的 fresh 输出，不以已有 CI 绿灯代替。

## 路径与协作边界

预计会涉及但由你最终选型：

```text
pipeline/synthetic_village/production_profile.py
pipeline/synthetic_village/production_render.py
pipeline/synthetic_village/production_journal.py
pipeline/synthetic_village/local_production_runner.py
scripts/blender/render_synthetic_village.py
tests/test_synthetic_village_production_*.py
```

不要修改 Codex 正在维护的：

```text
web/studio/
web/viewer/
pipeline/studio_server.py
```

提交继续使用路径限定 stage，并保留：

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

完成后请回执：

```text
handoff/FEEDBACK-HANDOFF-OPUS-006.md
```

Codex 收到后负责：

1. 复核 operator policy 的可解释性和 fail-closed UX；
2. 实渲新旧相机对照；
3. 检查 Studio jobs/ledger/HUD 是否诚实呈现拒绝原因；
4. 再决定是否进入 180-frame 小批量生产渲染。
