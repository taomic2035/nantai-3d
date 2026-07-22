# HANDOFF-GLM-002 — 真实重建独立 lane 执行队列

> **For GLM-5.2:** 立即执行 Task 1；逐项 TDD、小步路径限定提交。Task 1
> 完成后交 Codex review，同时可执行 Task 2 的设计与计划；未经 review 不并行修改
> Task 3 的运行时代码。

**Goal:** 在不触碰 Codex 的 Blender / Studio / Viewer / production caller 的前提下，
关闭真实 3DGS 对齐的高阶 SH 损失，并把真实 SfM 与云 GPU 训练的下一批 fail-closed
合同准备到可实现状态。

**Architecture:** 三项工作严格串行分层：数值内核先解决 degree 1–3 SH 旋转；SfM
质量门只解释和约束已有 registration 证据；云 GPU handshake 只绑定训练输入、环境、
配置与输出。三层都只消费机器证据，不从引擎名、文件名或操作者声明提升信任。

**Tech Stack:** Python 3.11/3.13、NumPy、Pydantic v2、pytest、ruff、标准 INRIA /
graphdeco 3DGS PLY、canonical JSON + SHA-256。

---

## 0. 立即开工指令

当前 `main@68bd983` 已关闭 `REVIEW-CODEX-021` 的 topology false-green，并由 Codex
完成 reciprocal role candidate caller 绑定。GLM **不再等待 Blender caller**，也不要
继续修改 junction、exact-218、Phase 4.3 或 reciprocal plan。

现在立即开始：

```text
handoff/HANDOFF-OPUS-010-degree3-sh-rotation.md
```

该文件的 §2–§5 是 Task 1 的规范性合同；本文件只补充执行顺序、回执路径和后续队列。
若两份文件冲突，以更严格的 fail-closed 条款为准并在回执中逐项列出冲突，不自行放宽。

## Task 1（现在开始，P0）：degree 1–3 SH rotation

**所有权与路径：**

- Create: `pipeline/spherical_harmonics.py`
- Modify: `pipeline/gaussian_scene.py`
- Create: `tests/test_spherical_harmonics.py`
- Modify only when required by the normative acceptance:
  `tests/test_gaussian_fidelity.py`, `tests/test_gaussian_scene.py`
- Modify only after numerical acceptance:
  `docs/manual/reconstruction-setup.md`, `docs/real-data-workflow.md`
- Feedback: `handoff/FEEDBACK-HANDOFF-GLM-002-degree3-sh-rotation.md`

**禁止路径：**

- `pipeline/synthetic_village/**`
- `scripts/blender/**`
- `pipeline/studio_server.py`
- `web/**`
- `.nantai-studio/**`
- `assets/registry.json`

### 执行步骤

- [ ] **Step 1: 锁定 graphdeco 字段顺序并让测试先红。**

  从仓库现有 degree-3 PLY fixture 建立 `f_rest_0..44` 的实际 flatten / reshape
  往返测试。degree 1/2/3 每色 non-DC 系数必须分别为 `3/8/15`，总列数为
  `9/24/45`。不允许靠猜测 reshape 或转置让测试通过。

- [ ] **Step 2: 写 real SH degree 0–3 evaluator 与 rotation block 测试。**

  先覆盖 identity、X/Y/Z 90°、任意轴非特殊角、composition、inverse、DC 不变、
  RGB 不串色、degree block 不串阶。每个 degree 至少 64 个确定性单位方向验证函数值
  不变式；测试必须明确采用的世界旋转方向约定。

- [ ] **Step 3: 运行红灯并把根因记录到回执草稿。**

  ```powershell
  python -m pytest tests/test_spherical_harmonics.py -q
  ```

  预期：在 `pipeline.spherical_harmonics` 尚不存在或旋转入口尚未实现时失败；不能以
  skip、xfail 或放宽误差预算代替红灯。

- [ ] **Step 4: 实现 NumPy-only rotation block。**

  用固定、版本锁定、满秩的球面采样与 float64 线性求解，或用闭式 real Wigner-D。
  无论选择哪条路线，都必须验证 sample matrix rank / condition number、block 正交误差、
  proper rotation、finite output 和 float32 可写回性。同一 `R` 只构造一次 block，再批量
  应用到全部 Gaussian / RGB channel；不得引入 SciPy。

- [ ] **Step 5: 原子接入 `GaussianScene`。**

  `transform()` 与 `apply_frame_transform()` 在所有 xyz、normal、quaternion、SH 派生值
  都通过验证后才能一次性提交数组与 history。任一 SH 错误都必须保证 geometry、SH、
  frame、units、transform history 全部逐字不变。`flatten_sh()` 继续保留为显式有损降级。

- [ ] **Step 6: 跑数值与回归门。**

  ```powershell
  python -m pytest tests/test_spherical_harmonics.py tests/test_gaussian_fidelity.py tests/test_gaussian_scene.py tests/test_reconstruct.py -q
  python -m ruff check pipeline/spherical_harmonics.py pipeline/gaussian_scene.py tests/test_spherical_harmonics.py tests/test_gaussian_fidelity.py tests/test_gaussian_scene.py
  ```

  回执必须给出 degree 1/2/3 最大函数值误差、composition 最大误差、inverse 最大误差、
  degree-3 PLY float32 round-trip 最大误差，以及完整 pass 数。

- [ ] **Step 7: 更新文档，但不夸大能力。**

  删除“高阶 SH 旋转未实现”的当前限制，改为“degree 1–3 已由数值不变式验证”；保留
  `flatten_sh()` 的兼容/降级说明。不得声称这会生成真实纹理、补相机覆盖或提升
  `geometry_usability`。

- [ ] **Step 8: 路径限定提交并回传 review。**

  只暂存本 Task 列出的文件；不得使用 `git add -A` 或 `git commit -a`，不得卷入当前
  未跟踪的 `web/data/`。提交尾行：

  ```text
  Co-Authored-By: GLM-5.2 <noreply@z.ai.com>
  ```

  一个可 review 的小步提交完成后立即把 commit SHA 和回执路径发给 Codex；在 Codex
  review 前不要开始 Task 3 的实现。

## Task 2（Task 1 等 review 时可做，仅设计 + TDD 计划）：SfM quality gate v1

**为什么高价值：** `FEEDBACK-HANDOFF-OPUS-011` 已证明真实 COLMAP wrapper 能运行，
但 `2/20` 只代表调用成功，绝不代表可训练。当前缺少把“调用成功 / 覆盖不足 /
允许训练”分开的内容寻址机器门。

**本轮只允许创建：**

- `docs/superpowers/specs/2026-07-22-registration-quality-policy-design.md`
- `docs/superpowers/plans/2026-07-22-registration-quality-policy.md`
- `handoff/FEEDBACK-HANDOFF-GLM-003-registration-quality-design.md`

**设计必须锁定：**

1. `RegistrationQualityPolicy` 的阈值全部由操作者显式给出，至少包含最小注册图数、最小
   注册比例、最小 session 覆盖比例和允许的最大未注册连续段；不得从这次 `2/20` 反推。
2. `RegistrationQualityReport` 必须绑定 registration JSON SHA/bytes、输入 capture
   manifest SHA、policy canonical SHA、实际 engine、registered/total、per-session 指标、
   选中 COLMAP model identity 与拒绝原因。
3. 状态必须至少区分 `invocation_succeeded`、`quality_accepted`、`training_allowed`；
   缺报告、缺输入 SHA、覆盖 evidence 不可解析、mock、内容不匹配时都必须
   `training_allowed=false`。
4. 若 mapper 产出多个 sparse model，设计必须说明如何确定性枚举并绑定选中 model，
   以及如何计算 largest connected model share；不得继续把目录名 `0` 当作质量证明。
5. canonical JSON 必须 LF、跨平台稳定、Pydantic `extra=forbid`；验证器重算内容 SHA，
   不信任报告自报的 `passed`。
6. canary 方案必须保留输入、命令 receipt、registration JSON 与 report 的 SHA；合成
   canary 只能证明机制，不能冒充真实采集 acceptance。
7. 计划必须按 TDD 拆为红灯、最小实现、绿灯、回归、提交五类步骤，并列出精确测试名
   与期望失败原因；本轮不修改 `pipeline/registration.py` 或 Studio。

完成这三份文档后路径限定提交并交 Codex review；不要提前实现 schema。

## Task 3（Task 1 review 通过后，先设计）：云 GPU training provenance handshake

**输入：** `handoff/HANDOFF-OPUS-010-degree3-sh-rotation.md` §6、
`cloud/train_3dgs_nerfstudio.sh`、Task 2 的 registration quality contract。

**第一阶段只产出：**

- `docs/superpowers/specs/2026-07-22-cloud-training-provenance-design.md`
- `docs/superpowers/plans/2026-07-22-cloud-training-provenance.md`
- `handoff/FEEDBACK-HANDOFF-GLM-004-cloud-training-provenance-design.md`

设计必须用 canonical `training-request.json` / `training-result.json` 绑定：verified
capture/ingest、通过 Task 2 的 registration quality report、训练器名称和精确版本、CUDA /
GPU 环境、完整配置、随机种子、导出命令、PLY SHA/bytes/properties、训练日志 SHA 与失败
状态。验证器只能证明内容闭包与 policy 一致，不能因 `nerfstudio`、`splatfacto` 或云厂商
名称自动提升为真实、米制或已验收。

在 Codex review 这三份设计前，不修改 `cloud/train_3dgs_nerfstudio.sh`，不触碰 Studio，
不伪造云 GPU 实测结果。

## 4. 与 Codex lane 的边界

Codex 继续独占并推进：fresh production plan、reciprocal plan、exact-218、Phase 4.3、
六角色 preflight、六层 Blender 实渲、visibility、post-render v2、Studio jobs/ledger 与 RGB
审计。GLM 不等待这些工作，也不修改这些路径。

GLM 当前唯一需要 Codex 的同步点是：Task 1 数值实现 review，以及 Task 2/3 设计 review。
其余步骤均可独立推进。

## 5. 完成定义与信任边界

- Task 1 通过只证明 proper rotation 下 degree 1–3 SH 数值一致，不证明输入 PLY 来自真实
  训练，不证明相机覆盖或米制精度。
- Task 2 通过只证明 registration 是否满足一份显式 policy，不证明未拍摄区域存在几何。
- Task 3 通过只证明云端训练输入、配置、环境与输出内容闭包，不证明模型视觉上完美。
- 真实模型与真实照片纹理仍必须来自真实采集 + 合格 SfM + 外部 GPU 训练产物；任何合成
  山村、image2 参考或 Blender blockout 都不能替代这条证据链。
