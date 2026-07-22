# FEEDBACK-HANDOFF-CODEX-025 — Batch 20 非共线布局与六角色闭环

> 日期：2026-07-23
> Owner：Codex
> 基线：`main@cf9b1b6272456cf0770614024e67af2a46383f5c`
> 结果：fresh exact-218、Phase 4.3、六角色 preflight / six-layer /
> visibility / post-render v2 全部通过（`6 accepted / 0 failed`）。

## 信任边界

本轮所有结果仍是 synthetic L0、`preview-only`、`modeled-unverified`，
post-render 报告的 `trust_effect=none-quality-filter-only`。Batch 20 图片只作为
可替换的 `design-only` 构图参考；没有相机标定、真实纹理、测量几何、SfM、
3DGS coverage 或任意坐标 360° 完整性证明。

## 实现

- bridge / watermill / forest 不再使用全局 Y 轴等距直线占位；43 个 part 中这三组
  改为显式 dogleg、service loop、switchback XY/yaw 布局。
- 每组仍使用一个由所有 authored center 的 analytic terrain 峰值加 `0.5m`
  得出的共同地坪，保持现有 `<=12%` 路线坡度门且不埋入地形。
- 六个 role camera 都从前两个 route-bearing part 的真实首段切线退后 `5m`；
  水平看向所有 part 旋转后 world-space XY 包围盒中心。没有放宽质量阈值，也没有
  注入通用俯角。
- 新增回归证明三目标角色确实非共线、方向不止一种、全部 part 清地形，并证明
  六个 candidate 替换进完整 180-camera plan 后都能通过 spacing/outlier 合同。

首次 Blender probe 精确拒绝了 bridge 与既有 bridge/waterwheel 相交、forest 与
`cross-level-covered-passage-v1` 相交以及 bridge route width `1.000m < 1.200m`。
布局随后移至不相交位置；没有修改 probe、renderer 或阈值。

首次正式 batch 为 `5 accepted / 1 failed`：watermill candidate 本身合法，但写入
完整 180-camera plan 后距最近 ground-route 相机 `27.702m`，超过 IQR 离群门
`26.231m`。整组水车检修环平移 `2m` 后重新生成 plan/build/probe/batch；没有绕过
production-plan validator。

## focused 验证

```text
reciprocal module/runtime/probe/production/blender/batch/windows: 271 passed
Ruff: clean
```

## final exact-218 身份

| 字段 | 值 |
|---|---|
| build ID | `31cbbcde40053986d4ad321e93416c842c0ab858764203c5c835e79b15e21c98` |
| reciprocal plan SHA | `e892a50e4f647f52466d53261345855aa5e485e83141f908f13fafe1e0dce858` |
| production plan SHA | `54aced28d33adad63dcbb301be32ede28998e1d2996a0232b10a7df1f586cb3a` |
| camera registry SHA | `ea2abab801fcff1a823276c3b5851666ec0f0a82907778d8cdaba9ae4f189d42` |
| build request SHA | `522c01c27c356395268067538a4db4e2e975e153671147d8398f66fab812ee57` |
| build report SHA | `c5f198b1c9b3c752805fa1455010ffa1e6304e14fca42d9190cb7623afd8da11` |
| blend SHA / bytes | `edbe58ee0c5af2308f9036a27f563c3328c2fd047ef8078694738cfb54f4768f` / `150365933` |

私有可复核目录：

```text
.nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules/
  31cbbcde40053986d4ad321e93416c842c0ab858764203c5c835e79b15e21c98/
```

## final Phase 4.3

| 字段 | 值 |
|---|---|
| probe request SHA | `a4e5ffbd8b735f820960f909e981e18144cecaef1bacdf8e1ef8e42c1eb7f60c` |
| probe report SHA | `e72211df7c709ed9ceb72a6e75bb3e1a1809ba5934c579654852677b17f7b755` |
| probe script SHA | `26cf8adda6e2aa7f197b88c00b6251ecad94381dc616a8b736649810ca2b6b26` |
| 结果 | route `6/6`、module-pair `15/15`、environment `6/6`、attachment `6/6` |

`overall_passed=true`；没有残留失败项。

## final 六角色正式 batch

冻结 policy：clearance `<2m / 5-of-15`、legacy valid-pixel `0.05`、
post-render v2 SHA
`b60eabd0c9cf069b23982bf2cfb9149ea25add8c6d76df39541d5642cf880b17`。

```text
.nantai-studio/sv-prod-win/reciprocal-production-batches/
  batch20-noncollinear-v2/
```

| role | upper-ground | sky | valid depth | render ID | 结果 |
|---|---:|---:|---:|---|---|
| central-courtyard-downhill | `0.017025` | `0.253755` | `0.746245` | `e47724ab…` | accepted |
| bridge-deck-crossing | `0.121562` | `0.395837` | `0.604163` | `cffbc491…` | accepted |
| watermill-tailrace | `0.018446` | `0.454586` | `0.545414` | `04b154a1…` | accepted |
| covered-gallery-underpass | `0.263513` | `0.081967` | `0.918033` | `8de44401…` | accepted |
| forest-orchard-boundary | `0.200395` | `0.232283` | `0.767717` | `dac81d50…` | accepted |
| lower-valley-uphill | `0.184146` | `0.057173` | `0.942827` | `b867136b…` | accepted |

Batch ID：
`25ee56792698b36129609defba3af2f97347a2d47ac6f6a54c920f8a5a7d4f4d`；
journal self SHA：
`d3ed83c98bcfc2b7b99867549c5f7312902eca55e8ea4c3c21b29096cc903607`；
journal file SHA：
`889a580235e924e240ee972d280bef184ccf52eba03f32c3b2b38f8238c2828f`。

六个 preflight 的 upper/middle near-hit count 都是 `0`。角色实例
`176..218` 按所属模块全部出现在各自 frame 的实测 instance layer 中，无 missing。
所有 frame 的 dimensions、depth range、normal、instance registry、semantic registry
交叉验证字段均为 `true`。

## RGB 人工复核：机器绿不等于产品完成

相比 v5，bridge / watermill / forest 已不再是远处的一条小直线，折线布局和近中景
密度确实改善，且 formal upper-ground 三项都从拒绝变为通过。但六张 RGB 仍暴露明显
blockout 差距：

- reciprocal part 仍大量使用近黑色基础材质，缺少最终 UV、材质层次与结构细节；
- bridge 与 watermill 的功能构件虽可见，但还不是可读的完整石桥/水轮机械建筑；
- forest 的平地坪跨坡后仍出现视觉悬空，需要独立支撑、挡墙、台阶或合法坡段；
- central/gallery/lower-valley 仍有重复黑色门框式 passage；
- 基础环境仍有金字塔式远山、稀疏建筑和简化地表，离真实照片重建很远。

因此这次 `6/6` 只解除 §3 caller / synthetic gate 阻塞，不应写成“真实 3D 场景
完成”。下一步最高价值不是继续调相机或放宽门，而是把这三组布局消费成
role-specific 最终几何：石桥桥台/拱券/栏板、水车 wheel/shaft/bearing/flume/
tailrace、森林挡墙/涵洞/台阶/支撑，并为 reciprocal parts 加可替换 PBR/UV 输入。
真实模型和真实纹理仍需真实采集、COLMAP 位姿以及外部 CUDA SfM/3DGS 或 mesh
reconstruction 链路。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
