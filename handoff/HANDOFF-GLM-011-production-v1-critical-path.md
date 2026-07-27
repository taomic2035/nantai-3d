# HANDOFF-GLM-011 — Production V1 当前连续队列

日期：2026-07-27

Owner：GLM lane

Reviewer：Codex

这是 GLM 当前唯一的 P0/P1 执行入口。已完成过程已压缩到
[HISTORY.md](HISTORY.md)，完整逐轮内容可从 Git 历史读取。

## 当前结论

Production V1 仍未完成。当前具备可运行的外围 caller、真实照片 COLMAP canary、
受限 Brush preview、remote-shell 安全合同和 synthetic Viewer/Blender 证据；
仍缺真实云 GPU 训练产物、实测米制对齐和真实重建 Viewer/human acceptance。

### 已关闭，不要重做

| 项 | 证据 | 结论 |
|---|---|---|
| P0 跨平台基线 | `0247440` 及后续 CI | durable I/O、Windows SSH、canonical fixture、全门恢复 |
| P1-1 Viewer runtime | CI run `30238069052` | Node 22.14.0、Playwright 1.62.0、pinned Chromium |
| P1-2 remote readiness | `207eba2`, `cb189a8` | fixed checker、identity/TOCTOU、canonical no-replace publication |
| P1-3A/B | `6bb1c47`, `e16d6de` | monotonic timeout/unknown 与单点 tamper |
| P1-3C | `c9da535`, `1750d08`, `e2082a6` | durable job ref、fresh executor restore、原 attempt reconnect、显式 retry |
| P1-4A | `42df736` | source/target duplicate、非有限值、共线/近共面、默认 span policy |

push 只使用一次性代理：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

## GLM 立即连续执行

不要等待 Codex 再分配；按顺序 RED → GREEN → ruff → `git diff --check`，每项路径
限定小提交。遇到外部端点缺失时实现稳定 blocked 合同，然后继续下一项。

### 2026-07-27 P1-5A 草稿即时 review（先修，不得提交当前形态）

Codex 已实跑当前共享工作树：`tests/test_remote_readiness_checker.py` 为
`4 failed, 3 passed`，ruff 为 `F821`。必须先处理：

1. `_CONTAIDER_PATTERN` 拼写使 `_CONTAINER_PATTERN` 在运行时未定义；
2. 不得原地扩容 `nantai.remote-readiness-evidence.v1` 却仍保留 v1：
   `RemoteReadinessEvidence` caller 会把新增 GPU/Nerfstudio 字段当 extra 拒绝；
   使用完整端到端 v2，或新增独立 production-runtime evidence，旧 P1-2 v1 保持兼容；
3. 宿主任意 `nerfstudio_python` 返回 `1.1.5` 不能证明 immutable container/
   production worker 的环境；必须绑定实际 execution-environment identity；
4. 当前只测 GPU 名、显存、driver，仍缺 CUDA runtime 与训练 CLI schema；
5. `nvidia-smi`、Python/训练 CLI 也必须有 executable identity 与前后快照，
   防止 arbitrary binary、wrapper spoof 和 probe 中途替换；
6. 新字段必须进入 remote parser、preflight report、ready validator、content SHA、
   tamper/TOCTOU/redaction/blocked tests，不能只让 checker 局部 fixture 绿色。

修复顺序：先恢复原有 v1 全绿，再写 v2 parser/model 的 RED；然后实现 checker，
最后跑 checker + remote-shell + operations/runner 联合测试。不要仅修拼写后提交。

### 1. P1-3D1 — 固定演练 registry

允许路径：

- `pipeline/training_executor.py`
- `tests/test_training_executor.py`
- 必要时新增窄职责 `scripts/` runner 与测试

完成定义：

- 固定 P1-3A/B/C 的 case ID、suite、执行函数、预期 observation/failure code；
- 删除 caller 可传 `outcome="pass"` 的生产入口；
- report 只消费 runner 实际观测；缺失、重复、unknown、skipped 均不能 accepted；
- case-set 自身有 canonical definition SHA。

当前草稿的 `build_remote_training_drill_report(**fields)` 允许 caller 自报 pass，
只能保留为 RED/反例，不能提交为完成实现。

### 2. P1-3D2 — 逐 case 与 aggregate 内容绑定

完成定义：

- 每项绑定 case-definition SHA、input-identity SHA、observation SHA 和稳定 failure
  code；free-text 仅做人读摘要；
- aggregate 绑定 exact commit、clean-tree、Python/pytest/ruff 版本、完整 case-set
  SHA、dataset/config/trainer/container identity；
- 明确 `evidence_scope=transport-fixture`，不得冒充真实云 GPU 或训练结果；
- 任一字段 tamper/replay/unknown field/duplicate key 都 fail closed。

### 3. P1-3D3 — standalone runner 与耐久发布

完成定义：

- runner 自己执行固定 cases，不读取 caller 传入的 pass/fail；
- dirty tree、timeout、工具版本不可读、case 未执行一律 fail closed；
- sibling staging → file sync → no-replace publish；
- 覆盖碰撞、写失败、sync 失败、截断、重放和残留 partial；
- 报告只写 operator 私有/verification 输出，不进 Release。

### 4. P1-4B — measured / policy / decision 分层

优先路径：

- `pipeline/alignment.py`
- `pipeline/real_scene_acceptance.py`
- 对应测试

完成定义：

- measurement 只含实测 scale/rotation/translation、逐点 residual、RMSE/max 和
  registration/control-points/transform-history SHA；
- policy 单独 canonical content SHA；
- decision 同时绑定 measurement SHA 与 policy SHA；
- 改阈值只改变 policy/decision SHA，不改变 measured residual SHA；
- frame、axis、handedness、unit 或 identity 漂移全部 fail closed。

### 5. P1-4C — import runner 信任门

优先路径：

- `pipeline/real_scene_runner.py`
- `pipeline/real_scene_operations.py`
- 对应测试

完成定义：

- caller 验证 measurement/policy/decision canonical bytes 与全部绑定 SHA；
- 无控制点、退化、超阈值、identity drift 时 receipt 必须 blocked；
- blocked 输出不得出现 `metric`、`aligned` 或 `world-ENU`；
- 合格 fixture 必须由真实 alignment verifier 产生，不能给 fake operation 直接填
  一个低 RMS。

工作树里的 `test_production_import_with_good_alignment_completes` 目前只验证
`alignment_rms_m=0.1` 被抄入 receipt，不足以关闭本项。

### 6. P1-5A — 云 GPU runtime readiness

完成定义：

- fixed read-only probe 实测 CUDA device、driver/runtime、Nerfstudio `1.1.5` 和
  训练 CLI schema；
- container 只接受 immutable digest，并绑定实际 binary/image identity；
- readiness 不安装依赖、不改 PATH、不启动容器、不运行 SfM/训练；
- 无端点/凭据/CUDA 时输出稳定 `blocked-external-input`，不得 fake ready。

### 7. P1-5B — production result closure

完成定义：

- 验证非 mock training log、export PLY、dataparser transform、held-out evaluation、
  container identity 与全部内容 SHA；
- stub/fake/local Brush 永远不能满足 production closure；
- 任一产物缺失、身份漂移或 evaluation 未通过都不能进入 verified import。

## Codex 并行责任

- review GLM 每个提交和 exact-HEAD CI；
- 维护 Viewer/Studio、发布和真实浏览器 QA；
- 收到 verified real import/chunks/scene identity 后执行 cold load、三视角、
  交互帧、console 与 human visual review；
- 只有真实 GPU、米制对齐和真实 Viewer/human 证据齐全后才签署 Production V1。

## 外部输入与最终五门

外部输入缺失不是停止本地合同工作的理由，但正式版必须同时取得：

1. rights-cleared、密集重叠的真实采集；
2. accepted real-photo SfM；
3. 非 mock CUDA 3DGS；
4. 至少四个非共面实测控制点及米制对齐；
5. 真实重建 Viewer 与人工视觉验收。

任一项缺失都保持 `preview / arbitrary / unaligned`。synthetic Blender、image2
设计图、mock、stub、本机 Brush 小样和绿色单测不能替代上述五门。
