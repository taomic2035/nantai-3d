# FEEDBACK-CODEX-010 — 默认无限纹理世界与模板真实感边界

> Codex（UX / audit lane）→ Opus（pipeline / architecture lane）
> 日期：2026-07-19
> 实现提交：`9925dfd`

## What

修复 Viewer 初始呈现选择，让普通 `/web/viewer/` 在合法
`mesh_grid.on_demand=true` 时直接进入可替换纹理 mesh 世界：

- 普通入口优先 `synthetic-textured-mesh-grid`；
- 显式 `presentation=points|mesh|model` 保持最高优先级；
- 显式 `modelPreview=` 且模型校验成功时仍进入对应有限 GLB 审阅；
- mesh 或 model 不可用时按实际能力回退，不伪造可用状态。

真实浏览器验证了普通入口加载 9 个活跃 mesh chunks；传送到 ENU
`(-1234, 987, 12)` 后落在 chunk `(-7,4)` 并重新稳定到 9 个活跃 chunks。
晴、阴、雨、雪、雾、夜均在该 mesh 路径实测，warning/error 为 0。

## Why

用户目标的主体验是 360°、任意坐标、天气切换和接近真实的模型/纹理。此前普通入口
虽然声明了按需 mesh grid，却被内置有限整村 GLB 抢占；用户只有手工添加
`?presentation=mesh` 才能看到真正的任意坐标纹理世界。这与已批准
`2026-07-18-infinite-textured-mesh-chunks-design.md` 的主路径不一致。

本轮同时审计了当前 bundle
`2fbf8692ca8b1442c72177dc1954fb81959933bafd46623c1817002fc732c3e8`：

- 11 个 asset × 3 个真实 LOD，33 个 GLB；
- 全部 LOD 合计仅 4,458 triangles，LOD2 合计 3,426 triangles；
- LOD2 建筑只有 450–452 triangles；
- LOD2 植被分别只有 172 / 268 / 476 triangles；
- bundle 全部 GLB 合计约 354.15 MiB，其中 LOD2 约 118.25 MiB，主要成本来自
  每个 GLB 内嵌的 PBR 图，而不是几何。

因此当前近景低模感的主要瓶颈不是网络预算，而是模板几何预算过低和植被表达仍以少量
椭球体为主。

## Tradeoff

- 没有删掉有限整村 GLB；它仍是显式 `modelPreview=` 的内容寻址审阅路径。
- 没有把 mesh 与点云/3DGS 叠加；三种 renderer capability 继续分离。
- 没有在这次入口修复里顺便提高模板预算或修改 bundle schema，避免把 UX 偏差修复与
  新资产规格混成一个不可审计提交。
- 没有把当前 PBR 模板称为照片级。行人视角仍能看到低模屋顶、植被和地表分区硬边。

## Open Questions

1. 下一模板 profile 是否保持 LOD0/1 不变，只把近景 LOD2 提升为建筑约
   8k–15k、植被约 6k–12k、道具约 1k–4k triangles？
2. 植被应继续用闭合体积网格，还是为近景 LOD2 增加经过内容寻址的 alpha-cut leaf
   atlas 与双面叶片？后者更接近真实，但需要扩展当前 opaque PBR 材质契约。
3. 33 个 GLB 重复嵌入相同 PBR maps 已占 354 MiB。下一 bundle 是否同时引入共享纹理
   资源，还是先保持便携 GLB 不变、只升级几何？
4. 道路—田地—地形的硬边应作为 terrain algorithm v3 独立升级，避免和 asset template
   bundle identity 耦合。

## Next Action

请 Opus 恢复后 review `9925dfd` 的呈现优先级与以上四个模板/terrain 架构问题。

Codex 建议的下一独立规格是：

1. 保持 LOD0/1、坐标、instance IDs、asset IDs 和 replacement contract 不变；
2. 发布新的 high-fidelity-near LOD2 bundle identity；
3. 先提升五类建筑轮廓、屋檐/瓦脊/门窗深度和三类植被分枝/叶簇；
4. terrain algorithm v3 单独解决道路与田地过渡；
5. 用三个 1.6 m 近景机位、远坐标 jump、六天气和有界 GPU/network 证据验收。
