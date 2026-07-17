# HANDOFF-OPUS-005 — 180 相机生产覆盖档

> 发起：Codex（UX / Viewer / synthetic visual-input lane）→ 交办：Opus（camera plan / pipeline / Blender integration lane）  
> 日期：2026-07-17  
> 目标：把当前 24 相机 canary 扩展为可恢复、可审计的 180 相机生产采集档，作为 synthetic 3DGS
> 训练输入；**保留 24 相机 canary 的快速门禁职责，不直接放大 canary**。

## 为什么现在需要你接

当前实现把相机数量硬限制在 24：

- `pipeline/synthetic_village/camera_plan.py` 的 coverage model 上限为 24；
- `pipeline.synthetic_village.canary.RENDER_CAMERA_IDS` 是固定 canonical 24 元组；
- `run_canary_render()` 拒绝非 canonical camera ID。

24 视角已经证明 Blender → 六层帧 → GT COLMAP → Brush → 3DGS → spatial chunks
链路可用，但它只是 canary。它不能证明大型村庄的背面、院落环线、屋脊遮挡、村田交界和溪流沿线
具备足够共视覆盖，不能作为“360° 任意坐标漫游”的生产输入规模。

## 请求的架构边界

新增独立的 production capture profile，例如：

```text
profile_id = synthetic-village-coverage-180-v1
camera_count = 180
```

现有 canary profile 保持：

```text
profile_id = synthetic-village-canary-24-v1
camera_count = 24
```

两者可复用现有 Blender 场景、六层输出、逐帧 journal、内容寻址和恢复机制，但 production profile
必须有自己的请求 schema/profile 字段、camera registry digest、render ID 和输出根，不能与 canary
journal 混用。

## 180 相机分配

| 组 | 数量 | 目的 |
|---|---:|---|
| ground-route | 72 | 主要步行路网、院落、后巷、石阶、桥面；人眼高度，连续环路和岔路 |
| elevated-pedestrian | 48 | 上层步道、木廊、屋脊交叉、挡墙高差；仍为可到达步行视角 |
| perimeter-inward | 32 | 聚落外圈反向看建筑背面、边界、田地与山体连接 |
| environment-corridor | 16 | 溪流、池塘、果园、梯田、竹林和山路的纵向覆盖 |
| audit-overview | 12 | 仅用于覆盖审计与远景约束的中低空概览；显式标记，不冒充地面漫游视角 |
| **合计** | **180** | |

不要按一个圆平均撒点。相机中心应沿实际可行走图和环境走廊布置；每个节点用相邻方向形成连续
共视，重点补足建筑背面和高差遮挡。

## 必须挣得的覆盖证据

生产档只有在机器可验证证据满足下列条件时才可发布：

1. camera ID 唯一、排序稳定，180 个 pose 全部有限且无重复中心；
2. 相邻路网相机形成可验证共视；每个主要 scene cluster 至少由 3 个非共线相机观察；
3. 每个 instantiated building / bridge / courtyard / environment component 至少有正面和反向覆盖，
   不能只凭 camera 名称宣称；
4. 使用现有 instance/semantic/depth 层统计实际可见像素，记录 per-camera 与 per-component coverage；
5. 检测近重复 pose、孤立相机、只看天空/地面的坏帧和过低有效像素占比，并 fail closed；
6. ground-route 和 elevated-pedestrian 构成至少两个闭环，给 COLMAP loop closure；
7. 所有 RGB、depth、normal、instance、semantic、camera metadata 继续保持现有六文件契约；
8. production report 写明 `synthetic=true`、`simplified-pbr-not-render-parity`，不因相机增多提升
   geometry trust。

建议发布摘要至少包含：

```json
{
  "profile_id": "synthetic-village-coverage-180-v1",
  "camera_count": 180,
  "verified_frame_count": 180,
  "coverage": {
    "components_total": 0,
    "components_with_three_view_support": 0,
    "route_loops": 0,
    "isolated_cameras": 0,
    "near_duplicate_pairs": 0
  }
}
```

数字必须从实际渲染的 instance/semantic/depth 证据计算，不能从 camera ID 或预期布局推断。

## CLI 与恢复

期望有独立入口，命名可由你定：

```powershell
python scripts/synthetic_village.py build-production --profile coverage-180
python scripts/synthetic_village.py render-production --profile coverage-180
```

要求：

- 沿用逐帧 durable journal，失败后只补未验证帧；
- 支持明确 camera ID 子集或稳定 batch 切片，方便分阶段运行；
- 单帧超时与总任务耗时如实报告；
- 私有大产物继续位于 `.nantai-studio/` 并进入干净 Release，不提交 Git；
- 不使用额外 branch/worktree。

## 现实耗时

180 帧是当前 24 帧的 7.5 倍，并且每帧有六层输出。本机 CPU / Intel iGPU 路径预计是小时级，
不是交互秒级；实现应优先保证 resumable 和批次发布。不要为了缩短时间降低到彼此不共视的独立
image2 单图，也不要把 audit-overview 当作地面漫游覆盖。

## Codex 后续

你交付 production profile 后，我负责：

1. 检查 180 帧覆盖可读性、坏帧和用户可理解的 coverage HUD；
2. 运行 GT COLMAP / Brush canary 的扩大版或将产物交给云 GPU；
3. 用已完成的 spatial-chunks Viewer 做真实分块漫游验证；
4. 整理干净 Release 与 README 使用路径。

请把实现回执写到：

`handoff/FEEDBACK-HANDOFF-OPUS-005.md`
