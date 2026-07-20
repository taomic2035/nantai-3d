# FEEDBACK-HANDOFF-GLM-001 — Batch 6 模块生产化 · 接管回执

> 回执：GLM（接替 Opus 的 pipeline / Blender build / registry / topology lane）→ Codex
> 日期：2026-07-20
> 对应：`handoff/HANDOFF-OPUS-007-batch6-modules-productionization.md`
> 身份变更：Opus lane 由 GLM 接管；UX / Viewer / Studio / 审计仍归 Codex

## 一句话

**接单，Opus 的非协商合同我全部继承（fail-closed、TDD、路径限定提交、不假装可以又不说实际问题）。但在动 007 之前必须先解决两件硬事：006 仍未回执、且 006 的 Windows v2-build 适配器方案仍等用户确认 —— 这两条不解决，007 做出来的模块没有质量门可验证可见性。**

## 我核实过的（没有转述，逐条自己查）

| Codex 在 007 里的说法 | 我查到的 |
|---|---|
| 推荐沿用 `ElevatedTopologyPlan` 的 additive 模式 | `pipeline/synthetic_village/elevated_topology.py` 存在，schema `nantai.synthetic-village.elevated-topology.v1`，明确"separate from immutable ScenePlan v1 and binds the exact canonical scene bytes it was checked against" —— 这正是 007 想要的形态 |
| 不重写已锁定的 ScenePlan v1 digest | `scene_plan.py` 的 `SceneObject` 已把 `courtyard-public-002`、`bridge-lower-001`、`building-central-008` 作为 object_id 锁定；elevated_topology 的 `_EXPECTED_COMPONENTS` 把 instance `127–130` 锁给四个 elevated 组件 —— 新模块不得撞这些 ID |
| `EnvironmentModulePlan` 是新增 | Grep 全仓库 `EnvironmentModulePlan\|environment_module`：**唯一匹配就是 007 这份 handoff 自己**。这是从零新建，不是改现有的 |
| 三个 image2 输入是 `design-only`、`trust_effect=none` | AGENTS.md Batch 6 节登记的三张 SHA（`19b40a84…`、`16b9f390…`、`2c3900ab…`）与 007 引用的 REVIEW-CODEX-012/013 一致；均声明 `camera_calibration=unknown`、`training_use=forbidden-as-multiview` |
| HANDOFF-006 优先，路径不重叠才并行 | **006 至今没有回执文件**（`FEEDBACK-HANDBACK-OPUS-006.md` 不存在）。006 要改 production camera plan / render pipeline 的几何净空门和六层坏帧门；007 要改 `scripts/blender/build_synthetic_village.py`、scene plan 周边、blender runtime 测试。两者都会触碰 Blender build 与 `tests/test_synthetic_village_blender_runtime.py` —— **路径重叠，必须串行** |
| 三模块加入后 downstream render identity 必须变化 | 当前 v2 `.blend` SHA `4f38ecf4…`（AGENTS.md Batch 6 节登记）仍是"稀疏块体村庄"，中央院落/桥底/后场未进入正式几何。模块生产化后该 SHA 必然变化，旧 journal 不可复用 |
| Co-Authored-By 保留 `Codex GPT-5.6 Sol` | 我是 GLM 接替 Opus。提交署名改为 `GLM-5.2 <glm-5.2@noreply.local>`（与仓库公开提交一致，见 topics 记录的 push 署名）；Codex 协作部分仍按 Codex 署名 |

## ⚠️ 阻断 007 的两件硬事（如实说，不绕过）

### 1. 006 仍无回执，且与 007 路径重叠

007 的 TDD 验收第 10 条要求"coverage 仅从实际帧统计，未渲染时保持 unknown/fail closed"。但**"实际帧统计"的质量门正是 006 要补的**——没有 006 的六层坏帧门和几何净空门，007 做完模块后无法诚实回答"这些模块在 production 帧里到底可见多少"。

按 AGENTS.md 的交办顺序："007 只有在与 006 路径不重叠时才可并行，否则先完成 006"。我已确认路径重叠（都改 Blender build 与 runtime 测试），所以**正确的顺序是先 006 再 007**。

我接替 Opus 后会先把 006 做完，再回来做 007。不会为了"看起来在推进 007"而跳过 006。

### 2. 006 的 Windows v2-build 适配器方案仍等用户确认

AGENTS.md 明确记录："Windows 180-camera production runner 的推荐接管方案是新增独立 Windows v2-build 验证适配器并复用现有六层 frame/journal/quality 合同；**不得**直接删除 Mac 平台门。该实现仍等待用户确认方案 A。"

这条决策不在我作为接替者的权限内——它改变 CI 矩阵和平台支持范围。**请用户在本回执之后明确**：
- 是否采用方案 A（新增独立 Windows v2-build 适配器，保留 Mac 门）？
- 还是有其它偏好？

用户不确认，006 无法落地；006 不落地，007 无法验收。这是真实阻断，不是我想拖。

## 对推荐架构的判断：同意 additive 模式，并补两条约束

Codex 推荐的 additive 模式我同意，理由与你说的一致：不改 ScenePlan v1 digest、可独立替换三模块、与现有 fail-closed build request 同构。

我落地为：

```text
pipeline/synthetic_village/environment_module.py        # 新增：EnvironmentModulePlan + 三个模块 recipe
pipeline/synthetic_village/environment_module_build.py  # 新增：Blender build request 编排
tests/test_synthetic_village_environment_module.py      # 新增：TDD 锁定
```

我在此基础上补两条约束（若你认为不对，告诉我）：

1. **`EnvironmentModulePlan` 必须绑定 `scene_plan_sha` + `elevated_topology_sha` + 三张 design source SHA + recipe version**，与 `ElevatedTopologyPlan` 绑定 `canonical_scene_plan_bytes` 同一套做法。少绑一个，模块身份就不能内容寻址。
2. **模块 part/instance/semantic/material ID 必须从 elevated instances `127–130` 之后的下一个稳定段追加**（当前看 `127–130` 已被 elevated 占用，新段从 `131` 起）。具体段号在 TDD 里锁死，避免与 elevated 撞 ID。

## 三模块的实现边界（我看到的潜在风险）

### 1. 中央工作院落

验收门（gallery `≥2.6m` 净宽 / `≥2.4m` 净空、stair `≥2.4m`、ramp `≥3.0m`）是硬几何约束，我会在 `EnvironmentModulePlan` 的 `model_validator` 里把它们做成 fail-closed——不满足直接 raise，不靠事后渲染发现。

**风险**：`courtyard-public-002` 在 ScenePlan v1 里已有 footprint，新模块的 west/east 入口连到 `path-network-002/003` 时不能改 path 的 polyline。我会在 plan 层做 footprint vs path 相交检查，撞了就 fail。

### 2. 下层桥拱 / 水车

这条最难。"creek floor / bank / water surface 与 terrain 形成无穿插的确定性截面"——我会在几何层做**显式截面剖面**（沿 creek polyline 的纵剖面 + 每隔 N 米的横剖面），在 plan 层校验水面 z ≤ bank z ≤ terrain z，且拱底 z ≥ deck z。这是纯几何约束，不需要 Blender 介入就能在 TDD 里锁。

**风险**：水车轮 / 轴 / 支架 / millrace / 落水 / 回水要保持独立 object identity——我会把它们建模为独立 part，每个有自己的 stable ID，不合并成一个 instance。但**"独立 part"在 Blender 侧意味着多个 object**，build request 必须接受多 part 输出，这会改 `build_synthetic_village.py` 的输出契约。这是 007 必须碰 Codex lane 之外、但影响 build report 的改动点。

### 3. 后场服务院

`building-central-008` 已在 ScenePlan v1 锁定，但原型"只验证了位置和可读性，不能作为门窗方向、尺寸或建筑真实背面的证据"。所以我会把原型当作**布局参考**，门窗/檐底/雨槽/排水出口的几何参数全部在 `EnvironmentModulePlan` 的 recipe 里显式声明，不从原型 `.blend` 反推。

**风险**："架空层/检修入口"在 ScenePlan v1 里没有对应字段。我会在 `EnvironmentModulePlan` 里新增一个 `service_courtyard_variant` 字段（至少三种 variant），但**不**把它塞回 ScenePlan v1。

## TDD 与验收

按 007 要求的 10 条，我会先写失败测试。门禁：

```powershell
python -m pytest `
  tests/test_synthetic_village_scene_plan.py `
  tests/test_synthetic_village_elevated_topology.py `
  tests/test_synthetic_village_environment_module.py `
  tests/test_synthetic_village_blender_runtime.py `
  tests/test_synthetic_village_canary.py `
  tests/test_coverage_audit.py -q
```

加上我实际修改路径对应的完整 synthetic-village 测试集。

**诚实说明**：Blender runtime 探针需要 Windows x64 `third/blender/blender.exe`。若该二进制在本机不可用，相关测试会 skip（不 fail，也不假装过）。我会用 `make.py doctor` 实测并报告，不替用户下结论。

## 协作合同（我接替 Opus 后继续遵守）

- **路径限定提交**：`git add <明确文件>` + `git commit -- <路径>`，禁用 `git add -A` / `commit -a`。
- **不碰 Codex lane**：`web/studio/`、`web/viewer/`、`pipeline/studio_server.py` 不动。
- **fail-closed**：未知 → 可预览但永不静默提升为 measured/metric/aligned。三模块继续声明 `synthetic=true`、`geometry_usability=preview-only`、`simplified-pbr-not-render-parity`、`verification_level=L0`、`real_photo_textures=false`。
- **不假装可以又不说实际问题**：每个限制、外部依赖、真实耗时如实标注。
- **Co-Authored-By**：提交署名 `GLM-5.2 <glm-5.2@noreply.local>`；若该提交含 Codex 协作内容，追加 `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`。
- **push 时机协调**：完成后先回执，不自行 push（共享工作树，他人可能有未推送工作）。

## 我需要 Codex 后续配合的

1. **新旧 RGB 对照与模块可读性审计**：模块 build 完成后，Codex 做实渲对照（与 REVIEW-CODEX-008/009/010 同一套方法）。
2. **Studio job/ledger/HUD 呈现**：模块 build 的阶段与拒绝原因要在 Studio UI 可见——这是 Codex lane，我不动。
3. **instance/semantic/depth 可见性证据复核**：模块加入后，Codex 用六层帧统计实际可见像素（与 HANDOFF-005 回执里我承诺的"掩码像素而非 ID 清单"判据一致）。
4. **Release 与 README 消费路径**：通过后由 Codex 整理。

## 下一步

1. **等用户确认 006 的 Windows v2-build 方案 A**（本回执阻断点 2）。
2. 用户确认后，我先把 **006** 做完并回执 `FEEDBACK-HANDOFF-OPUS-006.md`。
3. 006 回执后，开始 **007**，按 TDD 顺序：先 `environment_module.py` 的 plan + recipe + validator，再 build request，最后 Blender runtime 探针。
4. 007 完成后回执 `FEEDBACK-HANDOFF-OPUS-007.md`（按 007 要求的回执路径）。

若用户希望**并行推进**（接受 006/007 路径重叠的风险），请明确指示——我会按指示办，但会在每次提交里显式声明哪些路径与 006 重叠、哪些是 007 独有，方便 Codex review 时分辨。
