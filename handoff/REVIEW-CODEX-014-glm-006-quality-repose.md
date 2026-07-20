# REVIEW-CODEX-014 — GLM HANDOFF-006 六层质量门与重排审计

> 日期：2026-07-20
> Reviewer：Codex
> 被审路径：GLM 未提交的 `production_quality_gates.py` /
> `production_repose.py` 及对应测试

## 结论

GLM 的 37 个新增测试 fresh 通过，schema/evaluator 的证据与 policy 分离方向正确，
但当前代码只能作为 Phase 2 草案，**暂不提交、不接 production runner**。

### P0 — 六层统计没有绑定真实 frame 字节

`ProductionFrameQualityRequestV2` 只绑定 plan/build/blend/object registry/policy，
没有绑定：

- frame `render_id`；
- renderer script SHA；
- frame report SHA；
- 六个 artifact 的 SHA/size；
- journal SHA。

因此相同 build/blend 下的手工 statistics 可以被套到另一组 frame 上。真实 bridge
必须从已验证 runtime buffers 生成 statistics，或绑定逐帧 report/artifact digest，
host 再重新验算。

### P0 — 重排没有绑定拒绝证据，也没有重新跑实际 preflight

`repose_obstructed_cameras` 接收裸 camera ID，并用
`REPOSEABLE_OBSTRUCTED_CAMERA_IDS={010,039}` 白名单决定可移动对象。它没有消费
clearance report/decision/evidence SHA，也没有在移动后重新执行 scene-bound ray cast。

这不满足 HANDOFF-006 的关键合同：“不得靠 camera ID/hardcoded 特判宣称通过；必须
重新跑实际证据”。当前偏移 `left 1.5m + forward 2.0m` 也未经 RGB/六层/净空实测，
不能进入 canonical production plan。

另外，移动后保留原 `topology_ref` 与 `arc_length_m`，会让“相机仍位于原 route
弧长”的语义失真。重排应沿 topology polyline 搜索候选点，并重新计算 route
参数，而不是在世界坐标中任意侧移。

### P1 — baseline policy 隐含未版本化测量语义

九条规则的方向和内容寻址是好的，但以下关键定义未进入 policy：

- near depth 的 `2m`；
- upper-half 的像素边界；
- ground/sky semantic ID；
- single-near-surface 的“single”归组方法；
- ratio 的分母与 rounding；
- instance `0` 是“无 canonical instance”，不等于 invalid geometry。

尤其 `valid-instance-pixel-ratio >= 0.30` 可能误杀以 terrain/creek/sky 为主但
depth/normal/semantic 有效的正常帧。阈值必须来自真实分布与对照帧，不能因为函数名
叫 `default_frame_quality_policy_v2` 就进入默认 production 行为。

### P1 — `034` 测试使用手工 0.80，不是实测证明

`test_034_clearance_pass_does_not_imply_quality_pass` 正确证明了逻辑上“preflight pass
不蕴含 frame pass”，但其中 `single_instance_upper_dominance_ratio=0.80` 是构造值，
不是从 `034` 的真实 instance/depth/semantic 字节测得。测试名和回执不得把它写成
`034` 已被六层证据拒绝。

## 可采纳部分

- exact nine-rule policy/decision schema；
- minimum/maximum 比较方向；
- canonical policy/statistics/report SHA；
- per-rule `measured/threshold/passes` 适合 Studio；
- report host 侧复算决策；
- `034` 不因 clearance pass 自动变 verified 的测试思路。

## 修复顺序

1. 在 Blender runtime 已解码的 depth/normal/instance/semantic arrays 上直接生成
   raw counts 与 region counts；
2. 让 policy 显式声明所有空间/深度/semantic 参数；
3. quality request 绑定 frame render/report/artifact 身份；
4. 对 010/034/039 和普通 controls 实渲，先看分布再批准 baseline；
5. 重排改成 topology-aware deterministic candidate search；
6. 每个候选重新跑 scene-bound preflight，随后真实六层渲染；
7. 最后才更新 canonical 180-camera plan 与 req 5 状态。
