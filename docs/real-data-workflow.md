# 真实数据 measured 重建工作流

把真实拍摄（照片/视频）+ COLMAP + GPU 训练的 3DGS 变成一个 **metric-aligned ENU 世界**。
管线机制已就位并经 CI 验证（`tests/test_reconstruct.py::...::test_import_into_aligned_world_is_metric_aligned`）；
本文档给出输入文件格式与逐步命令。**唯一外部依赖是真实 COLMAP 与训练产物**。

> 命令示例用 `.venv/bin/python`（macOS/Linux）。Windows 用 `.venv\Scripts\python`。

## 判定模型（为什么会/不会是 metric-aligned）

`geometry_usability` 只从坐标证据契约推导，与 engine 名无关（`pipeline/reconstruct.py::_derive_geometry_usability`）：

| 结果 | 条件 |
|---|---|
| `metric-aligned` | 非 synthetic + target frame 为 ENU/米/metric + 有 metric_evidence + `alignment_status=ALIGNED` |
| `metric-unaligned` | 非 synthetic + 米制 + 但 frame 仍 UNALIGNED |
| `preview-proxy` | 任一参与 frame 的 provenance 是 SYNTHETIC（例如 mock 配准）→ 即使几何真实也降级 |
| `preview-only` | provenance 未知，或缺米制/证据 |

**关键**：配准必须非合成（COLMAP sfm，provenance=SFM）。用 mock 配准对齐真实几何会正确降级为 `preview-proxy`——合成配准上的对齐不可信。

## 步骤 1 · COLMAP 配准（sfm-local）

```bash
# 需本机安装 colmap；不可用时会回退 mock（synthetic，不能产出 measured）
.venv/bin/python -m pipeline.reconstruct \
  --photos photos --reg-engine colmap --engine mock
# 产出 recon/registration.json：pose_frame = sfm-local (arbitrary / unaligned)
```

裸 COLMAP 停在 arbitrary sfm-local，**不会**被静默标为米制 ENU。

## 步骤 2 · 控制点 → SfM→ENU Sim3 对齐

准备 `control_points.json`（≥3 计数，且源点需张成 3D → 实际 **≥4 非共面**点）。每个控制点：
**源**用 `source_xyz`（sfm-local 坐标）**或** `image`（解析到该位姿相机中心）；
**目标**用 `enu_xyz`（米）**或** `geo`（GPS，经 `gps_to_enu` 归一到 geo origin）。

```json
[
  {"label": "gcp1", "image": "IMG_0007.jpg", "geo": {"lat": 26.0801, "lon": 119.2967, "alt": 12.5}},
  {"label": "gcp2", "image": "IMG_0042.jpg", "geo": {"lat": 26.0805, "lon": 119.2971, "alt": 12.8}},
  {"label": "gcp3", "source_xyz": [3.1, -0.4, 1.2], "enu_xyz": [10.0, 4.0, 1.1]},
  {"label": "gcp4", "source_xyz": [0.0, 8.7, 2.0], "enu_xyz": [0.0, 20.0, 2.0]}
]
```

```bash
.venv/bin/python -m pipeline.alignment \
  --registration recon/registration.json \
  --control-points control_points.json \
  --max-rms 2.0 --out recon/registration_aligned.json
```

**fail-closed 门**（任一不满足 → 保持 sfm-local/UNALIGNED，绝不升级为米制）：
计数 ≥3；源点非退化（共线/共面被拒）；Umeyama 拟合强制 det=+1（不产反射）；`scale>0`；
`rms_residual ≤ --max-rms`。拟合残差/退化裕度/门禁结果记入 `sim3.alignment.v1=<json>` 证据串，
挂在 `world_frame` 与 `pose_to_world` 上，可机器复核。缺 geo origin 时用 `--registration` 里的 `geo_origin`，
或在控制点全用 `enu_xyz` 时不需要 GPS。

## 步骤 3 · 导入真实 3DGS → measured 世界

为每个训练产物写一个 `SplatInput`。若训练 frame 与对齐后的 target（`world-enu`）不同，必须带显式 `transform`：

```json
{
  "session_id": "video_drone_orbit",
  "path": "trained/drone.ply",
  "source_frame": {
    "frame_id": "trainer-local", "handedness": "right", "axes": "local-z-up",
    "units": "meters", "metric_status": "metric", "geo_aligned": "unaligned",
    "provenance": "measured", "evidence": ["trainer export contract"]
  },
  "transform": {
    "source_frame": "trainer-local", "target_frame": "world-enu",
    "sim3": {"scale": 1.0, "quat_wxyz": [1.0, 0.0, 0.0, 0.0], "t_xyz": [0.0, 0.0, 0.0]},
    "method": "external-sim3", "evidence": ["control-point fit"]
  }
}
```

```bash
.venv/bin/python -m pipeline.reconstruct \
  --photos photos --engine import \
  --registration recon/registration_aligned.json \
  --splat trained/drone-splat-input.json
```

## 步骤 4 · 验证是 measured

```bash
.venv/bin/python -c "import json; m=json.load(open('web/data/recon/recon_manifest.json')); \
c=m['coordinate_contract']; p=m['provenance']; \
print('target=',c['target_frame']['frame_id'], 'aligned=',c['alignment_status'], \
'synthetic=',p['synthetic'], 'usability=',p['geometry_usability'])"
# 期望: target= world-enu aligned= aligned synthetic= False usability= metric-aligned
```

信任根 `recon_manifest.json` / `recon/registration.json` 以 LF 写出（跨 OS 字节可复现）；
`recon_manifest.sha256` sidecar 可对 manifest 整体做完整性校验/签名。

## 边界

- 没有真实 COLMAP + GPU 训练产物时，全链只能跑 mock/synthetic（明确标注 `preview-proxy`），不冒充 measured。
- ENU→米制升级只发生在控制点/GPS + 残差达标时；任何降级/退化都 fail-closed，不静默提升。
