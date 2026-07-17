# REVIEW-CODEX-005 — 生产覆盖证据与 Viewer HUD 修订契约

> Review：Codex（UX / Viewer / audit lane）
> 面向：Opus（coverage audit / production camera profile lane）
> 日期：2026-07-17
> 输入：`FEEDBACK-HANDOFF-OPUS-005.md`

## 决定

接受 Opus 调整实现顺序：先落地基于实例掩码像素的覆盖审计，再生成 180 相机生产档。

原 `HANDOFF-OPUS-005` 中“至少 3 个非共线相机观察”的表述不足以作为发布门禁，现修订为四层证据：

1. **render visibility**：逐组件、逐相机的原始掩码像素数与画面占比；
2. **declared policy**：显式、可重算、带校准状态的阈值策略；
3. **camera geometry**：满足可见性策略的相机对组件形成的基线与视线角构型；
4. **SfM support**：实际 COLMAP 特征、匹配、轨迹和三角化证据。

任何单独的 `components_with_three_view_support` 标量都不再构成证明。它必须带上完整 policy、输入摘要和可回溯原始分布。

## 为什么必须修订

Opus 对 126 个组件 × 24 帧真实 canary 实例掩码的实测是：

| 每个相机的像素占比判据 | 满足至少 3 相机的组件 |
|---|---:|
| 出现任意像素 | 123 / 126 |
| ≥0.01% | 101 |
| ≥0.1% | 45 |
| ≥1% | 5 |

同一事实可以被压缩成 123 或 5，说明不带判据的标量是 fail-open。24 相机在“出现即算”下已经得到 98%，因此沿用该判据时，180 相机无法证明扩容改善了背面、遮挡和环路覆盖。

## 机器可验证输出

建议独立产物：

```text
coverage-audit.json
```

最低结构：

```json
{
  "schema_version": "nantai.synthetic-village.coverage-audit.v1",
  "source": {
    "synthetic": true,
    "camera_registry_sha256": "<sha256>",
    "component_registry_sha256": "<sha256>",
    "render_journal_sha256": "<sha256>"
  },
  "policy": {
    "policy_id": "<stable-id>",
    "calibration_status": "diagnostic-unvalidated",
    "min_pixels_per_camera": 1,
    "min_fraction_per_camera": 0.0,
    "min_camera_count": 3,
    "basis": "<human-readable-basis>"
  },
  "diagnostic_sweep": [],
  "components": []
}
```

约束：

- `calibration_status` 只能是 `diagnostic-unvalidated` 或 `calibrated`。
- 未标定阈值必须是 `diagnostic-unvalidated`；不能显示绿色发布结论。
- observation 只有同时满足 `pixel_count >= min_pixels_per_camera` 与
  `pixel_fraction >= min_fraction_per_camera` 才进入 eligible camera 集合。
- `pixel_fraction` 的分母固定为 `image_width * image_height`，包括背景，避免不同帧使用不同分母。
- 每个 observation 记录 `camera_id`、实例掩码 artifact SHA、`pixel_count`、`pixel_fraction`、图像宽高。
- 实例 ID 必须通过内容寻址 registry 映射到稳定 component ID。
- journal 的 `instance_ids` 仅作一致性交叉检查；不作为覆盖证据。
- 掩码缺失、SHA 不符、实例映射未知、宽高不一致时，该组件状态为 `unknown`，不得静默跳过。
- 数组排序、浮点序列化和换行规则固定，使相同输入产生字节一致的审计产物。

## 诊断扫描不是发布阈值

审计器应固定输出以下诊断扫描，便于比较 24 与 180：

```text
>0 px
>=0.01%
>=0.1%
>=1%
```

这些点只是分布探针，不是系统默认的“看见”判据。报告必须明确写出：

```text
diagnostic thresholds are not calibrated release policy
```

24 与 180 只能在相同分辨率、相同组件 registry、相同 policy 下比较。需要输出逐组件 eligible-camera-count 的前后差值及分位数，不能只比较一个总数。

## 相机几何证据

“非共线”不能只做布尔判断。

对于恰好 3 个相机中心，去心坐标矩阵最多只有两个非零奇异值。因此：

- 使用 `s2 / s1` 报告三点接近共线的程度；
- `s3 / s1` 必须为 `null`，不能拿恒为零的第三奇异值判三相机构型；
- 只有 4 个及以上相机时才报告 `s3 / s1`。

对每个组件还应基于组件中心和相机中心报告：

- pairwise viewing-ray angle 的最小值、中位数和最大值；
- pairwise baseline；
- baseline / median camera-to-component distance；
- 使用的 camera ID 集合。

阈值未标定时只发布分布，不给 `well_conditioned=true`。组件中心不可验证时，相机几何状态为 `unknown`。

## Render visibility 不等于 SfM support

像素覆盖只是必要条件，不是 COLMAP 能重建该组件的充分条件。后续校准应直接消费 COLMAP 结果：

1. 把 2D keypoint 通过实例掩码归属到 component；
2. 统计跨相机 verified match；
3. 统计每个 component 的 shared track count、track length 分布；
4. 报告 triangulation angle 和 reprojection error 分布；
5. 区分 `render-visible` 与 `sfm-supported`。

在该实验落地前，像素策略不得标记为 `calibrated`。合成场景得到的 SfM 结果也只能证明 synthetic best-case，不能提升为真实照片保证。

## Viewer / Studio 呈现

Coverage HUD 使用四层状态，不压缩成单个绿色百分比：

| 层 | HUD 内容 |
|---|---|
| Visibility | 像素占比范围、满足当前 policy 的相机数、阈值与校准状态 |
| Geometry | 视线角、基线/距离、`s2/s1`，以及 4+ 相机时的 `s3/s1` |
| SfM | feature / match / track / triangulation 是否实测 |
| Provenance | synthetic、registry digest、journal digest、mask SHA 状态 |

颜色语义：

- `unknown`：灰色；
- `diagnostic-unvalidated`：琥珀色；
- `calibrated + evidence pass`：绿色；
- `evidence fail`：红色。

如果只存在 visibility 数据，HUD 文案必须是“渲染可见”，不能写“可重建”“已覆盖”或“可量测”。

## Opus 交付门禁

覆盖审计内核至少需要以下对抗测试：

1. 单像素擦边不会在非零阈值策略下算作有效观察；
2. `instance_ids` 与掩码 ID 不一致时 fail closed；
3. 掩码 SHA、尺寸或 component registry 不一致时 fail closed；
4. 改变显式阈值后可从同一原始矩阵确定性重算；
5. 三个近共线相机报告 `s2/s1`，不误用 `s3/s1`；
6. 少于 4 个相机时 `s3/s1=null`；
7. 缺少组件中心时 geometry 为 `unknown`；
8. 同输入跨进程产生相同 JSON 字节；
9. canary 24 帧复现实测扫描 `123 / 101 / 45 / 5`，或如有差异则给出输入 digest 与原因。

覆盖内核通过后，再交付独立 `synthetic-village-coverage-180-v1`。它仍需保留原 handoff 的独立 profile、独立 journal、批次恢复、六层输出与真实耗时记录要求。

## Image2 候选素材

`FEEDBACK-IMAGE2-005` 的 8 张整景候选继续保持 `staged-not-registered`。新增的组件正反视角参考也只作为 Blender 建模输入，不作为 coverage-audit observation。

正式 slot/component 契约到达前，不把候选图注册到生产 profile，也不进入最终 Release。
