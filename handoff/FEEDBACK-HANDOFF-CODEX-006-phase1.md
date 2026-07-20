# FEEDBACK-HANDOFF-CODEX-006 Phase 1 — production 相机几何预检

> 日期：2026-07-20
> 角色：Codex（实现 / UX / visual-quality / audit lane）
> 对应：`handoff/HANDOFF-OPUS-006-production-camera-quality-gates.md`

## 结论

HANDOFF-006 的第一条纵向切片已经进入真实执行路径：

- 版本化、内容寻址的 `5×5` clearance policy/evidence/decision/request/report；
- 自包含 Blender runtime，一次进程可探测 1–180 台相机；
- local production request/report/metadata 与 journal schema v2；
- `preflight_id` 和 post-render quality policy SHA 已进入 frame `render_id`；
- `preflight-rejected` 是独立状态，不伪造 RGB/depth/normal/mask/metadata；
- `render-production-local --preflight-only` 可只做预检，不启动昂贵六层渲染。

它仍只降低 training suitability，绝不提升 geometry、metric、alignment 或
coverage trust。

## Fresh 180-camera 实测

输入为当前 Windows textured L2 build：

| 身份 | SHA-256 |
|---|---|
| production plan | `d5db85507a1f7bc4731e03c93d7b1232ddab7272dd5a52fd4d8df7bf6252a9f9` |
| camera registry | `9c8ad9b2bf299d51385822a2b40f071781d0c07e42aae6e1216887adb2563726` |
| `.blend` | `fa8cc4aabfe5049f2025e9d2ab34739c0914d87aa78a8fbda21ad86299cbebac` |
| preflight ID | `42f65291a55f58c5b064a2785b3ee868a5d9c77c107ad233a4f9f235d7f10b9a` |
| canonical request | `d674f739e6de507eb93ec2bab826e14a016b542f6e712432784a7a5302ee39aa` |
| canonical report | `0b63bc6759e8a36d7ace04d760e43d27862082d084cc0cd50b73e30449224418` |

显式 operator policy：

```text
sample_grid = (-0.9, -0.45, 0.0, 0.45, 0.9)
upper/middle = sample_y >= 0
near_distance_m = 2.0
reject_when_upper_middle_near_hits >= 5
```

结果：

| upper/middle `<2m` 命中数 | camera 数 |
|---:|---:|
| 0 | 178 |
| 5 | 1 |
| 15 | 1 |

- `camera-ground-route-010`：`15/15`，拒绝；
- `camera-ground-route-039`：`5/15`，拒绝；
- `camera-ground-route-034`：`0/15`，仅表示本几何门未拒绝，**不表示训练通过**；
- Blender 加载、180 台 × 25 射线、canonical report 写入总命令耗时约 3 秒。

私有原始字节位于：

```text
.nantai-studio/synthetic-village/hybrid-v3/work/audit/
  production-clearance-phase1-v1/
    preflight-request.json
    preflight-report.json
```

## 实现提交

| commit | 内容 |
|---|---|
| `08769f2` | clearance policy/evidence/decision 合同 |
| `30e65ef` | scene-bound Blender runtime 与真实 010/034/039 验收 |
| `e57f5ea` | frame identity / request / report / metadata v2 |
| `7a78e3e` | journal/runner/CLI 预检接入与 `--preflight-only` |

所有 Codex 提交均包含：

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

## Fresh 门禁

```text
65 passed in 15.52s
ruff: all checks passed
git diff --check: clean
```

这 65 个相关测试覆盖 canonical/duplicate-key/identity mutation、跨进程 SHA、
真实 Blender probe、journal 状态、render identity 和 CLI。

完整旧 Blender runtime 文件另有 25 个与本次变更无关的陈旧私有 fixture 失败：
`344e...` fixture 仍是 126-object/14-semantic 旧合同，而当前代码要求
130-object/15-semantic。Python 完整 production profile 集另受本机缺少 `OpenEXR`
阻断。上述问题没有被计入本切片的成功声明。

## 未完成边界

- `034` 尚无真实六层统计，不能自动通过或拒绝；
- post-render statistics 尚未从真实 layer 字节复算；
- `010/039` 尚未执行证据绑定的 route-aware 重排与再次 preflight；
- Studio 尚未展示 per-rule measured/threshold/reason；
- `req-5-pose-quality-fail-closed` 必须继续保持未交付。
