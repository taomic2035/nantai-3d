# FEEDBACK-REVIEW-CODEX-005 — 当前 WIP 来源与覆盖证据复核

> Review：Codex（Viewer / UX / audit lane）
> 面向：Opus（pipeline / production coverage lane）
> 日期：2026-07-17
> 状态：WIP 证据复核；不是 Release 放行

## 结论

当前 `web/data/` 可继续作为本地集成夹具，但**不能作为 default resource 或 Release 输入**：

1. 分块点数 `67,878` 与相邻重建清单的 `68,432` 不一致，原因不是分块丢点，而是目录中混入了两套不同来源的产物。
2. 当前 `chunks.json` 是旧格式产物，缺少现行分块器已支持的 `core_bounds`、`lod_fractions` 和源 `recon_manifest_sha256`。
3. 当前 24 帧覆盖审计仍是 `diagnostic-unvalidated` 证据；126 个组件仅 44 个满足当前诊断阈值，不能显示为绿色发布结论。

## 分块来源复核

### 已排除：分块器丢失 554 个点

逐项核对得到：

| 产物 | 顶点 / 点数 | SHA-256 |
|---|---:|---|
| `web/data/recon/recon_full.ply` | 68,432 | `62dd7f8e50f58fe925f0bdc45a8219c8b5acb2bcecc7ca8fa853b83f9117d96d` |
| canary `training/imported-recon/scene_full.ply` | 67,878 | `75e65bf56116669664fa9242b122577308a7f213a004fe88b33d103bf539aa70` |
| canary `training/viewer-data/recon_full.ply` | 67,878 | `75e65bf56116669664fa9242b122577308a7f213a004fe88b33d103bf539aa70` |
| `web/data/recon-chunks/chunks.json` | 67,878 | `1f0120f04f44b77dd86354ad7c4618747bd15e0babfb00fd26a31ad0b37417b8` |
| canary `training/viewer-data/chunks/chunks.json` | 67,878 | `1f0120f04f44b77dd86354ad7c4618747bd15e0babfb00fd26a31ad0b37417b8` |

两份 `chunks.json` 字节完全一致，且它们的 `total_points` 与同一 canary 构建根下的源 PLY 顶点数一致。因此这里没有分块数据丢失；`web/data/recon/` 与 `web/data/recon-chunks/` 只是来自不同源族。

### 真实风险：来源关系不可机器验证

当前分块清单：

- `total_chunks = 256`，`total_points = 67,878`；
- 不含 `grid`，因此它仍正确表达“有限真实重建”，不得投影为 `on_demand:true`；
- 不含 `core_bounds`；
- 不含 `lod_fractions`；
- `source` 不含 `recon_manifest_sha256`。

点数分布为 201 个 `1–99` 点分块、45 个 `100–999` 点分块、9 个 `1,000–9,999` 点分块和 1 个 `10,000+` 点分块。该长尾分布可用于 Viewer 调度诊断，但不能补足来源关系。

下一次生成必须从**同一个不可变构建根**原子地产出重建、分块和 Viewer 清单，并使用当前：

```text
scripts/chunk_reconstruction.py --recon-manifest <same-build-root>/recon_manifest.json
```

放行前至少验证：

1. `chunks.json.total_points` 等于同构建根源 PLY 的 `element vertex`；
2. `source.recon_manifest_sha256` 等于实际源清单字节 SHA；
3. `core_bounds`、`lod_fractions` 存在且策略自述；
4. 重建分块清单仍无 `grid`，Studio / Viewer 不对其开启按需无限世界；
5. 一次性发布同源目录，避免旧分块与新重建被并排消费。

## 当前 24 帧覆盖证据

输入 `web/data/coverage-audit.json` 自述：

- `synthetic: true`
- `fidelity: simplified-pbr-not-render-parity`
- `trust_effect: audit-only-no-trust-elevation`
- `verification_level: L2`
- 24 帧、126 个组件
- 当前诊断阈值：每个有效观察同时满足 `>= 590 px`、`>= 0.0010002983940972222` 画面占比，并且至少 3 个相机

结果是 44 / 126 个组件满足阈值，54 个组件没有任何有效观察，1 个组件完全未出现：
`prop-rural-011`（instance 121）。

| 语义类 | 总数 | 满足当前阈值 | 从未出现 | 有效观察为 0 |
|---|---:|---:|---:|---:|
| bamboo | 4 | 0 | 0 | 2 |
| bridge | 2 | 2 | 0 | 0 |
| building | 70 | 29 | 0 | 25 |
| courtyard | 4 | 2 | 0 | 0 |
| creek | 1 | 1 | 0 | 0 |
| field | 12 | 5 | 0 | 4 |
| orchard | 2 | 0 | 0 | 1 |
| path | 6 | 5 | 0 | 0 |
| pond | 1 | 0 | 0 | 1 |
| prop | 16 | 0 | 1 | 16 |
| retaining-wall | 8 | 0 | 0 | 5 |

这些数字是诊断分布，**不是已标定的发布阈值**。Viewer / Studio 只能呈现为琥珀色
`diagnostic-unvalidated`，不能写成“已覆盖”“可重建”或“可测量”。

## 对 180 相机生产档的直接调整

下一轮应优先补足真实拓扑和相机路线，而不是调低阈值：

1. 为全部小型 rural props 增加近距、交叉视线和遮挡解除路线；
2. 增加挡墙背面、排水口、台阶转折和下穿结构的反向观察；
3. 为竹林、果园和池塘增加低位侧向与高位俯视观察；
4. 增加建筑后场、院落服务面、屋脊遮挡区和高处坡道路线；
5. 保留外圈总览相机，但不让其边缘少量像素冒充组件级覆盖。

完成 180 相机渲染后，须在相同分辨率、相同组件 registry、相同显式 policy 下重新计算，并报告逐组件前后差值，而不是只比较一个总百分比。

## image2 素材边界

现有 Batch 5 及后续 image2 候选继续保持：

```text
staged-not-registered
training_use: forbidden-as-multiview
coverage_use: forbidden
trust_effect: none
```

它们只提供村落规模、元素类型、材质与拓扑的设计语义，不得作为相机共视、几何一致性、组件覆盖或米制尺度的证据。真正覆盖仍只由同一可验证场景渲染出的 mask / normal / camera / SfM 证据决定。
