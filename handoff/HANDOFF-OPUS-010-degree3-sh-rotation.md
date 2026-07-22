# HANDOFF-OPUS-010 — degree 1–3 SH rotation

> Date: 2026-07-22
> Owner: Opus lane（当前由 GLM-5.2 临时接替）
> Priority: P0；完成并经 Codex review 后再开始本文末尾的 P1
> Independence: 不触碰 Blender、Studio、Viewer、production plan、registry 或
> reciprocal caller；因此不得改变 Codex 正在重跑的 exact-218 / §3 SHA 链。

## 1. 为什么这是当前最高价值的独立任务

真实 nerfstudio/Brush/Inria 3DGS 通常带 `f_rest_*`。当前
`GaussianScene._validate_safe_rotation()` 对“高阶 SH + 非恒等 Sim3 旋转”正确地
fail-closed，操作者只能先 `flatten_sh()`，代价是永久丢失视角相关颜色。实现可靠的
degree 1–3 SH rotation，可以让真实重建在 sfm-local → ENU 米制对齐时保留这些系数，
直接关闭 `AUDIT-2026-07-22-real-3d-scene-gap-assessment.md` 的关键真实感缺口。

这项能力只消除一个有损 workaround；它不生成真实照片、模型、纹理或测量证据，也不
提升 `geometry_usability`。`flatten_sh()` 必须保留为显式兼容/降级工具。

## 2. 必须先锁定的表示与变换约定

在写实现前用 TDD 锁定以下事实，不允许凭 `f_rest_*` 文件名推断：

1. `pipeline/gaussian_scene.py` 的 degree 1/2/3 分别对应每色
   `3 / 8 / 15` 个 non-DC 系数，总 `9 / 24 / 45` 个 `f_rest_*`。
2. 先从标准 graphdeco/INRIA PLY 的实际 channel/coefficient flatten 顺序建立最小 fixture，
   再写 reshape helper；如果仓库当前顺序与标准样本不一致，先 fail-closed 并上报，不能
   通过转置“让测试好看”。
3. 明确方向约定，并用函数值不变式证明：若世界坐标按
   `x' = R x` 旋转，则旋转后系数在新方向上的颜色，必须等于旋转前系数在对应旧方向
   上的颜色。测试是信任根，不能只测矩阵 shape 或 norm。
4. DC (`sh_dc`) 不变；degree block 之间不混合；只接受 proper orthonormal rotation
   (`R.T @ R ~= I`, `det(R) ~= +1`)。

## 3. 推荐实现

新增一个小而独立的 SH 模块（建议
`pipeline/spherical_harmonics.py`），不要把 Wigner-D 细节继续堆进
`gaussian_scene.py`。

推荐使用与仓库实际 3DGS real-SH basis/order 完全一致的 degree 0–3 evaluator，并为
每个 `l=1,2,3` 构造一次旋转 block。可以使用固定、版本锁定且满秩的球面采样方向与
float64 线性求解得到 block，但必须同时满足：

- 采样矩阵的 rank/condition number 有显式门；病态直接失败；
- block 的正交误差有门；超过误差预算直接失败；
- 同一 `R` 只计算一次 block，再批量应用到所有 Gaussian / RGB channel；禁止逐点求逆；
- 输出有限、可写回 float32；任一验证失败时几何、SH 和 transform history 均不变；
- 不引入 SciPy 运行时依赖。NumPy 足够，避免部署面扩大。

若选择闭式 real Wigner-D，也必须由同一组函数值/组合律测试证明约定正确；不能仅引用公式。

## 4. TDD 验收（先红后绿）

主要测试路径：

- `tests/test_spherical_harmonics.py`（新增）；
- `tests/test_gaussian_fidelity.py`；
- 必要时 `tests/test_gaussian_scene.py`，但不要改 provenance 判定。

至少覆盖：

1. 标准 degree-3 PLY fixture 的 flatten/reshape 顺序逐项往返；
2. identity rotation 对全部系数字节/数值不变；
3. x/y/z 轴的 90°、任意轴非特殊角；
4. degree 1、2、3 分别做不少于 64 个确定性单位方向的函数值不变式；
5. composition：`rotate(R2, rotate(R1, c))` 与 `rotate(R2 @ R1, c)` 一致；
6. inverse round-trip 恢复原系数；
7. DC 完全不变，RGB channel 不串色，degree block 不串阶；
8. improper / non-orthonormal / NaN rotation fail-closed；
9. `GaussianScene.transform()` 和 `apply_frame_transform()` 成功旋转 degree-3，并同时
   保持 xyz、normals、Gaussian quaternion、frame/history 的既有语义；
10. 人为让 SH rotation 失败时，所有数组、frame 和 history 原子不变；
11. 3DGS PLY 保存后重载，旋转后 `f_rest_*` 在容许的 float32 误差内一致；
12. 现有 translation/uniform-scale SH 不变测试继续通过；`flatten_sh()` 继续可用。

误差预算必须在测试中写明来源。建议函数值 `atol <= 2e-10`（float64 内部），PLY
float32 round-trip 单独使用与序列化精度相称的预算。若达不到，不得静默放宽到肉眼门。

## 5. 修改边界与交付顺序

允许修改：

- `pipeline/spherical_harmonics.py`（新增）；
- `pipeline/gaussian_scene.py`；
- 上述相关测试；
- SH 限制解除后才更新 `docs/manual/reconstruction-setup.md` 与
  `docs/real-data-workflow.md`，同时保留 flatten 的有损降级说明。

禁止修改：

- `pipeline/synthetic_village/production_profile.py`；
- `pipeline/synthetic_village/reciprocal_route_*`；
- `scripts/blender/*`；
- `pipeline/studio_server.py`、`web/*`；
- 任何 `.nantai-studio` 私有产物或 registry。

提交必须小步且路径限定；每个提交尾行：

```text
Co-Authored-By: GLM-5.2 <noreply@z.ai.com>
```

交付给 Codex review 的机器证据：测试命令与完整 pass 数、ruff 结果、三个 degree 的最大
函数值误差、composition/inverse 最大误差、一个 degree-3 PLY 旋转往返 SHA/数值摘要，
以及确认 production plan/reciprocal SHA 未受影响的前后对比。

## 6. P1 排队项（P0 未 review 前不要启动）

P0 通过后，下一项是“云 GPU 训练 provenance handshake”：用 canonical
`training-request.json` / `training-result.json` 绑定 verified ingest/COLMAP 输入、trainer
版本与配置、导出变换、PLY SHA/size；本地验证器只验证内容闭包，不把操作者/云端声称自动
提升为 measured。此 P1 先写独立 design + TDD plan，不修改现有
`cloud/train_3dgs_nerfstudio.sh`，直到 Codex review 边界。

---

信任边界：通过本任务只能证明 SH 在已声明 proper rotation 下数值一致；不能证明 PLY
来自真实训练、相机覆盖充分、尺度为米或纹理是真实照片。
