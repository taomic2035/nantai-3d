# FEEDBACK-HANDOFF-CODEX-027 — Batch22 exact-218 / local-360

> 日期：2026-07-23  
> Owner：Codex  
> 结论：caller 和机器证据闭环；真实感未达标。

## Fresh 机器身份

| 产物 | 身份 / 结果 |
|---|---|
| 175-root environment | build `c572ca037b39d5ae5694c1ea81afcfc0b9742e20d63d7ef0aff0123cc0444e99`；report `c6c63028559307b767418b235631441debcfa2643848eacea95f751314666006` |
| exact-218 | build `ebb936346ea2f31a4d551f6fa9bf64d5e48bcac46593fa0ff195b34d699f6cdd`；blend `b13b435310f5505a98e6f181a506a5663acabbdca102498cda47242df552cf3c`；report `3421d3f199e954773588b39548be271cb6db16ff7e83b4d2c0dc5e0dd05c03bc` |
| reciprocal plan | `9a8d60702306e5df404ac0cada316da79d4432ace02aea3aa7bf6b050774e9e0` |
| Phase 4.3 | route `6/6`；module pairs `15/15`；environment intersections `6/6`；topology attachments `6/6` |
| local-orbit plan | `b01a71b5b85df854cc07d2f757a4c694eb96c5b320d8c5b6d31ba1ddf4ad0b64` |
| local-orbit report | `4ce4bc97ffce2af6f7748cecead9b3f10f2670383ff008878f4722d278e52d05` |

final private report：

```text
.nantai-studio/o/b22/4ce4bc97ffce2af6f7748cecead9b3f10f2670383ff008878f4722d278e52d05/local-orbit-audit-report.json
```

## 验收结果

- 8 个主方位均按当地地形 + `1.8m` 净空生成；`az315` 经实测把半径扩到
  `24m`，避免进入 creek-bed cut；
- `accepted_frame_count=8`、`assembly_visible_frame_count=7`、
  `wheel_visible_frame_count=7`；
- `audit-waterwheel-az000` 被桥体结构性遮挡，报告显式记入
  `occluded_assembly_camera_ids`，不用重复相邻机位或非步行高度伪造 `8/8`。

## 目视复核

八张 RGB 证明水轮已是可读的开放辐条/桨板构件，但仍有明显生产差距：

1. `az000` 几乎被桥体与近距离石面占满；
2. 多方向出现悬空 slab、缺少承重支撑和不连续的平台；
3. 石材/苔藓纹理重复且拉伸，几何仍是块体/切面级；
4. creek/水面是平的不透明面，world/sky 是空灰背景；
5. `az180` 水轮接近侧缘，`az315` 主要被溪床墙体和平面几何切割。

因此报告仍是 `synthetic=true`、`verification_level=L0`、
`geometry_usability=preview-only`、`training_use=forbidden-as-multiview`、
`trust_effect=none-quality-filter-only`。它不是真实 mesh + 真实纹理的验收证书。

## 下一步

GLM/Opus 核心 lane 先修支撑拓扑、creek/water 几何与 world/sky，每次修复后重跑
fresh exact-218 → Phase 4.3 → local-orbit。真实目标仍需另外完成真实 capture、
accepted COLMAP、云 GPU 3DGS 训练、导入/对齐/分块与 Viewer 画面 QA。
