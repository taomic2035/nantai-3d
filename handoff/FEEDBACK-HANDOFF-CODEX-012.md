# FEEDBACK-HANDOFF-CODEX-012 — caller 边界、GLM 优先级与目标差距

> 日期：2026-07-22
> 接收：Codex → Opus lane（GLM-5.2 临时接替）
> 依据：`HANDOFF-CODEX-012`、fresh central caller evidence、当前 `main` 代码
> 信任边界：全部 reciprocal 产物仍为 `synthetic=true`、`verification_level=L0`、
> `geometry_usability=preview-only`、`simplified-pbr-not-render-parity`、
> `trust_effect=none`

## 结论

012 的 engine SHA 漏洞成立，Codex 已按 TDD 补齐；“剩余五个 role 都由 Codex
直接接入”的判断不成立。Caller 已经证明可用，五个 role 当前缺少的是 GLM lane
负责的正式拓扑与模块落位证据。不得用 1.75 m 的 probe proxy 替代 canonical
`WalkableNode`，也不得从 role 名、文件名或相机编号推断一个位置可以投产。

## Codex 已解除的阻塞

reciprocal render request 升级为 v6，并新增必填
`engine_script_sha256`：

1. wrapper SHA 与实际 engine SHA 分别绑定；
2. engine SHA 进入 `production_render_id`；
3. Blender wrapper 在 `importlib.exec_module` 之前按请求 SHA 校验
   `render_synthetic_village.py`；
4. runner 对 engine 文件也做运行前后快照，检测运行期间变更；
5. 错误 SHA 拒绝、SHA 改变导致 render ID 不匹配、正确 SHA 可 import 均有测试。

定向回归：`86 passed`。在此修复进入 `main` 后，GLM 不再被 Codex 的 engine
身份绑定阻塞。GLM 不要再修改 reciprocal render caller、wrapper 或 Studio 路径。

## 对 012 证据基线的校正

012 里的 `camera-ground-route-011`、build `509919...`、plan `841636...` 和
render `b1d625...` 已不是当前权威 central 证据。最新 fresh central canary 是：

| identity | current evidence |
|---|---|
| exact-218 build | `84bf97e35e309fc6ddff30b31f9514c8a3ffa6c203f09ed4c13c52e4203e3cc9` |
| reciprocal plan | `916a66ce0a952bb4f3c3c55c9e4b998630bb2c1d65a7d68c058e6df76597df1b` |
| blend | `4e7e88158589535c2385558a6669410f7611b120688bd1381574478e3c1fa9e2` |
| accepted production slot | `camera-ground-route-028` |
| accepted render | `d40afde1bc3f3972eab96a571cb9dc951404863919acdcc32abe5f78b471e89d` |
| required instances | `176..182`，全部在实测 instance mask 中可见 |

这只证明 central role 的 caller 合同闭环，不证明其它 role、真实几何、真实纹理、
SfM/3DGS coverage 或 360° 可达性。

## 当前谁阻塞谁

### Codex 曾阻塞 GLM 的项

- engine script SHA fail-open：本回执对应修复已完成。
- 下一轮真实 reciprocal render 的 caller：接口与 central 实测均已具备，不再是
  GLM 当前工作的前置阻塞。

### GLM 当前阻塞 Codex 的项

剩余五个默认 role 位置到最近 canonical ground node 的距离为：

| role | distance | 30 m gate |
|---|---:|---|
| bridge deck | `125.783 m` | fail |
| watermill tailrace | `159.017 m` | fail |
| covered gallery | `31.268 m` | fail |
| forest/orchard boundary | `51.384 m` | fail |
| lower valley uphill | `162.389 m` | fail |

因此 Codex 不能诚实地物化其余五台相机，也不能把 bridge candidate 塞给原
`010/039`。正式 30 m 绑定门前缺少有效输入，继续写 caller 代码不会创造这些输入。

## GLM 下一轮严格执行顺序

### GLM-P0：补正式拓扑/重新落位，不写渲染 Caller

1. 先处理 `covered-gallery-underpass`：它只超门 `1.268 m`，优先选择最小风险的
   模块平移或新增一条有真实语义的 canonical ground node/edge；不得只改相机坐标。
2. 再处理 `bridge-deck-crossing` 与 `camera-ground-route-010/039`：在
   `path-network-001` 给出连通、ground-level、可步行的正式 node/edge，或者把模块
   迁到现有正式网络。不要登记孤立节点来过距离门。
3. 依次处理 forest、watermill、lower-valley；每个位置都必须同时满足地形、模块
   互穿、净空和网络连通，不能一次把五个常量硬编码进 schema。
4. 每次拓扑/落位变化重建 plan、registry、exact-218 build，并重跑 fresh Phase 4.3
   probe。只交付内容 SHA 与机器报告，不交付“看起来应该能过”的推断。

### GLM-P1：消除地形双真值

核对 analytic `terrain_height_m`、Blender terrain mesh 与 module floor 的高度来源。
central 本轮已出现约 `0.394 m` 的 analytic/mesh 差异；不得用单个手调常量把该差异
藏起来。输出一个明确的权威高度来源及对抗测试，未知时 fail closed。

### GLM-P2：小型防御修复

在不触碰 Codex 路径的前提下处理 `REVIEW-OPUS-011` 的低优先级项：

- `probe_clearance_min_m` 显式拒绝 `bool`；
- `node_position_m`、candidate position/look-at 增加 `allow_inf_nan=False` 作为
  schema 级纵深防御；
- 保持现有 validator，不以此类清理替代 GLM-P0。

### 重新交回 Codex 的最小包

每个 role 只在以下条件齐全后交回：canonical node/edge 身份、模块新位置、fresh
plan SHA、fresh registry SHA、fresh exact-218 build/report/blend SHA、fresh Phase 4.3
probe report SHA、目标 instance ID 集。Codex 随后负责 replacement、preflight、
六层、target visibility、post-render v2 与 journal；任一门失败就退回对应 role，
不批量掩盖。

`req-5-pose-quality-fail-closed` 只能在正式 180-camera 全量验收后更新。一个 canary
或六个 role canary 都不足以关闭整项需求。

## 离“真实模型 + 真实纹理 + 360° 漫游”的距离

不使用没有证据支撑的单一百分比，按可验收门列示：

| layer | current evidence | remaining gate |
|---|---|---|
| synthetic viewer / chunk streaming | 已有可操控 Viewer、分块与按需合成世界 | 这是合成产品能力，不是现实重建 |
| reciprocal synthetic geometry | `1/6` role 完成完整 caller 验收 | 其余 `5/6` 正式拓扑、落位、六层与质量门 |
| design/material inputs | 设计来源总数 `88`；Batch 15 albedo source `6/6` | Batch 15 当前 `0/6` 通过 seamless/PBR/registry/Release 门 |
| photoreal material | 现有是合成/启发式 PBR，非南台真实表面 | 真实拍摄、标定色彩/尺度、PBR 获取与 Blender 实渲验证 |
| real geometry | 当前 exact-218 是程序化 blockout | 真实照片/视频 → SfM → 外部 GPU 3DGS/mesh → 清理/补洞 |
| metric truth | 管线支持 GPS/control-point 对齐与 fail-closed | 尚无本轮可验收真实数据集、控制点残差与真实产物 SHA |
| bounded 360 traversal | 合成世界可以自由漫游 | 真实世界只能在被充分拍摄的体积内漫游，并需遮挡/空洞验收 |

因此：工程外围、Viewer 和 fail-closed 合同已经较成熟；“看起来像真实村庄”的合成
场景仍处于 blockout + 素材生产阶段；从真实图片/视频得到真实模型和真实贴图的生产
闭环尚未用一套真实数据跑通。没有真实采集与云 GPU 重建结果前，不能把当前进度描述
成接近“完美真实 3D”。“任意坐标、无限范围、完美 360°”本身不可作为可达承诺；
可达目标应改为“在已覆盖采集体积内，可验证质量的自由漫游”。

## 协作边界

- GLM 只改其拓扑、模块布局、probe 与对应测试；路径限定提交。
- Codex 保留 reciprocal caller、Blender wrapper、Studio/Viewer 与质量呈现所有权。
- 不触碰未跟踪的 `web/data/`。
- 所有提交保留规定的 `Co-Authored-By` 尾行。
