# 2026-07-16 · fail-closed / provenance-safety 审计 + 四项 TDD 修复

> 执行：Opus(架构/管线 lane)。方法：3 个并行 Opus subagent(trust 提升路径 / silent
> failure / 契约边界)只读审计 + 构造触发输入实测取证(`.venv` 跑通, 拒绝"看起来可能")。
> 审计任务 id: wsvbmvm87。所有坐实项均有 file:line + 触发场景证据。

## 结论 TL;DR

pipeline 核心对 fail-closed / provenance-safety **极其自律**:三维度共列 20+ 条 clean_paths
(Sim3 反射/非正交拒绝、Umeyama 反射守卫、_validate_scene_history 抗篡改、PLY 自述字段绝不
当 trust、legacy/v1 迁移为 unknown、content-addressed id + hmac SHA + session_id 内容绑定、
symlink/junction/路径穿越阻断……)。**未发现任何 fail-open**(unknown 当 trusted / 未验证
证据被提升 / 错误被吞当成功)之外的 provenance 违反。

坐实 4 项(1 medium + 3 low),全部 Opus lane,已逐一 TDD 修复并提交:

| # | 维度 | 严重度 | 缺口 | 修复 commit |
|---|---|---|---|---|
| 1 | trust-promotion | **medium** | 矛盾对齐证据被忽略、声称获胜(真 fail-open) | `0eaceff` |
| 2 | silent-failure | low | colmap 子进程无超时 → 卡死永久 hang | `72fc745` |
| 3 | contract-boundary | low | render_single_chunk 非整数坐标抛未分类 TypeError | `0cf7ffe` |
| 4 | contract-boundary | low | schema.GeoOrigin 缺 GPS 范围/有限性校验 | `f28f251` |

## Finding 1(medium)：矛盾的对齐证据必须压倒声称 —— 核心价值缺口

**缺口**:`_derive_geometry_usability` 把 `metric_evidence` 当不透明 `list[str]`,只判"非空"。
一个 `world_frame` 声称 metric/aligned/measured + `alignment_status=ALIGNED`,但其内嵌
`sim3.alignment.v1` 证据明写 `passed=false / rms=999m`,仍被提升为 `geometry_usability=metric-aligned`。
**在手的机器可验证证据与声称矛盾,却被忽略、让声称获胜** —— 正是核心价值警告的 fail-open。

**放大面**:诚实生产者(`alignment.align_registration`)gate 未过先 raise,根本产不出该矛盾对象;
故只能来自**被篡改/外来的 registration.json**(trust root,无签名/摘要校验,经 `--registration`
从任意文件读入)。下游 web/viewer、web/studio、studio_server 据 manifest 的 geometry_usability /
metric_evidence 做 metric 提升(**codex 保护区**,仅报告放大面,根因修在产出层)。

**修复**(消费侧 chokepoint,与该函数 docstring 已承诺的"contradictory alignment facts fail
closed to preview-only"一致):`_alignment_evidence_consistent()` 扫 `metric_evidence` 里的
`sim3.alignment.v1=` 串,逐个 `Sim3AlignmentEvidence.parse`;任一解析失败或 `passed=false`
→ 否决 metric 提升(降级 preview-only)。证据**缺席不否决**(米制状态可建立在别的证据上,
如 survey scale bars 的 metric-unaligned)—— 故此门只压制被篡改/外来输入,不动诚实路径。

TDD:`test_coordinate_contract.py::TestMetricEvidenceGate`(passed=false→preview-only、
不可解析→preview-only、passed=true→metric-aligned 保持)。回归 87 绿。

## Finding 2(low)：colmap 子进程有界超时

返回码是被检查的(非零→RuntimeError,失败已 fail-closed),但三个重活(feature_extractor /
matcher / mapper)`subprocess.run` **无 timeout** → colmap 卡死(headless/集显 OpenGL SIFT
停滞、matcher 病态输入、I/O 挂起)则永久 hang 且不抛错(liveness 缺口,非 fail-open)。
修复:`colmap_register` 加 `stage_timeout_s`(默认 3600s/阶段,对文档化小场景极宽松),
捕获 `TimeoutExpired`→`RuntimeError`(与非零返回码同构)。

## Finding 3(low)：render_single_chunk 坐标类型闸

非整数/NaN/inf 坐标在 `MockLayoutGenerator._rng` 的 `random.Random(seed)` 深处抛未分类
`TypeError`;numpy 整数同因被拒(random 只认原生 int)。当前不可从不可信输入触达
(端点未接线),但 codex 后续上线 `/api/world/chunk` 路由时 URL 段直达内核。
修复:顶部 `numbers.Integral` 校验(非整数→清晰 ValueError)+ coerce 到 python int
(接受 numpy 整数)。**codex 侧**:路由层须先 `int(seg)`(失败→400),见 HANDOFF-CODEX-003 §1。

## Finding 4(low)：GeoOrigin GPS 范围/有限性校验

L2 布局 `schema.GeoOrigin.lat/lon/alt` 无约束裸 float(不同于信任根 `recon_schema.GeoAnchor`
的 ge/le + allow_inf_nan=False),从外部 layout JSON 加载故可声明越界/NaN 而被静默接受。
修复:lat ge=-90/le=90、lon ge=-180/le=180、三字段 allow_inf_nan=False。文档化 chunk 范围
(±10⁴ → lat≤46/lon≤139)内正常路径不受影响,仅荒谬索引(|chunk|>±3e4)的越界原点 fail-closed。

## 验证

- 四项均 TDD(先红后绿);四改动文件相关测试套件 **137 绿**(coordinate_contract + alignment
  + reconstruct + registration + render_on_demand + mock_layout_assets);ruff 干净。
- 完整回归剩余 54 failed 全在 **codex lane 既有环境问题**:`test_synthetic_village_blender_runtime`
  (本机未装 Blender)+ `test_studio_crash_recovery`(Windows venv 启动器 PID 间接,`skipif`
  仅 Windows 跑, Linux CI 跳过)。两模块均不 import 我改的任何文件,与本轮修复无关。

## 边界声明

审计覆盖 `pipeline/` 的 trust 提升 / silent failure / 契约边界三维度,深审 Opus lane、
codex 保护区(studio_server/studio_jobs/studio_ledger/web)仅报告级抽查(未见 fail-open,
建议 codex 复核其深层路径)。真实素材(registry)跨平台字节见 HANDOFF-CODEX-003 §4(同平台已证)。
