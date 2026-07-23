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

## 步骤 0 · 采集预检（可选，但下一步很贵）

步骤 1 的 COLMAP 是整条链最贵的一步（无序 ~300 图实测 2–5+ 小时，见
[reconstruction-setup.md §4](manual/reconstruction-setup.md)）。开跑前先用单图证据看一眼这批照片：

```bash
.venv/bin/python scripts/check_capture.py photos/
.venv/bin/python scripts/check_capture.py photos/ --json > precheck.json   # 机读
```

对**本文档**尤其有用的一项：它报告**多少图带 EXIF GPS**——那决定了步骤 2 能不能走
`--from-gps` 免手写 `control_points.json`（一张都没有时，它会直接告诉你「无法走 `--from-gps`
米制对齐，要米制请用实测控制点」）。

⚠️ **这是启发式预检，不能替代真跑 COLMAP**：决定成败的**重叠度**是图**之间**的关系，
**单图分析测不出来**。`likely` 只意味「没发现明显硬伤」，**不是**能重建的保证；`unlikely`
也不保证一定失败。退出码 `0` = 出了报告（无论结论好坏），`2` = 没法分析（fail-closed）。

## 步骤 1 · COLMAP 配准（sfm-local）

```bash
# 需本机安装 colmap；不可用时会回退 mock（synthetic，不能产出 measured）
.venv/bin/python -m pipeline.reconstruct \
  --photos photos --reg-engine colmap --engine mock
# 产出 recon/registration.json：pose_frame = sfm-local (arbitrary / unaligned)
```

裸 COLMAP 停在 arbitrary sfm-local，**不会**被静默标为米制 ENU。

### 步骤 1b · 产出 registration quality report（可选，但为 trusted prefix 所必需）

若后续要走云 GPU 训练并想拿到 `training_provenance.v1` trusted prefix（见步骤 2b），
COLMAP 配准后必须额外产出一份从 sparse 字节派生的 quality report：

```bash
.venv/bin/python scripts/emit_registration_quality.py \
  --registration-json recon/registration.json \
  --sparse-dir recon/colmap_ws/sparse \
  --capture-manifest ingest/manifest.json \
  --policy policy.json \
  --output rq/quality-report.json
```

- `--sparse-dir` 指向 COLMAP `sparse/` 目录（含 `<index>/images.txt` + `points3D.txt`），
  脚本会枚举多组件模型并选最大连通块。
- `--capture-manifest` 指向 ingest 阶段的 `CaptureRevisionManifest`（可选但
  trusted prefix 必需——它把照片源 provenance 绑到 quality report）。
- `--policy` 是一份 `RegistrationQualityPolicy` JSON（5 个阈值：min_registered_count、
  min_registered_ratio、min_session_coverage_ratio、max_unregistered_consecutive_run、
  min_largest_connected_model_share）。用 Python one-liner 生成：

  ```bash
  .venv/bin/python -c "from pipeline.registration_quality import \
    RegistrationQualityPolicy as P; print(P(min_registered_count=10, \
    min_registered_ratio=0.7, min_session_coverage_ratio=0.6, \
    max_unregistered_consecutive_run=5, \
    min_largest_connected_model_share=0.6).model_dump_json(indent=2))" > policy.json
  ```

报告里的 `training_allowed=True` 只证明配准满足 operator 覆盖策略 + non-mock engine +
有 capture manifest——**不证明**照片真实、几何对 3DGS 充分或尺度米制。详见
[reconstruction-setup.md §5a-2](manual/reconstruction-setup.md)。

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
  --geo-origin 26.0801,119.2967,12.5 \
  --max-rms 2.0 --out recon/registration_aligned.json
```

**geo origin 必填**：world-enu 是相对某个 ENU 切平面原点定义的，即使控制点全用 `enu_xyz`
（那些坐标本就相对该原点），也必须提供 geo origin。用 `--geo-origin lat,lon,alt` 提供，
或让 `registration.json` 自带 `geo_origin`（COLMAP 从 EXIF GPS 读到时）；两者都没有则 fail-closed。

> 💡 **GPS 标记采集（无人机/手机）免手工写控制点**：若每张图都带 EXIF GPS，相机位置本身
> 就是控制点。直接用 `--from-gps` 指向 ingest manifest，一键 turnkey 对齐（免写 `control_points.json`）：
>
> ```bash
> .venv/bin/python -m pipeline.alignment \
>   --registration recon/registration.json \
>   --from-gps ingest/manifest.json \
>   --geo-origin 26.0801,119.2967,12.5 --out recon/registration_aligned.json
> ```
>
> 只有【既注册又带 EXIF GPS】的图成为控制点（视频帧无 EXIF GPS 自动排除）；图名以 manifest
> 的 `output_path` 匹配 `pose.image`，不匹配者静默排除。拟合门（≥3 点/退化/RMS）仍权威，
> 匹配不足即 fail-closed 并给出清晰错误。Python API 亦可：
> `pipeline.alignment.control_points_from_geo_anchors(reg, {image: GeoAnchor})`。
>
> ⚠️ **精度现实（重要，别被默认门挡住还不明白为什么）**：消费级 EXIF GPS（手机/无人机）
> 精度约 **3–10 m**。GPS 噪声**无法**被相似变换解释，所以拟合残差 ≈ 噪声量级 →
> **默认 `--max-rms 2.0` 基本必然 fail-closed**（这是**正确**的：它拒绝为噪声数据盖上米制章）。
> 实务：① 放宽到 `--max-rms 5`~`10` 才可能过门，但**对齐精度不会好于 GPS 本身**——
> 得到的 `metric-aligned` 只在米级尺度可信，别拿它做厘米级测量；② 要高精度就用**实测控制点**
> （`enu_xyz`，全站仪/RTK），那才是 sub-metre 的路；③ RTK 无人机（~2–5 cm）则 GPS 路径就够好。
> 证据串 `sim3.alignment.v1` 里记着实际 `rms_residual_m`——**以它判断你的对齐到底多准**。

**fail-closed 门**（任一不满足 → 保持 sfm-local/UNALIGNED，绝不升级为米制）：
计数 ≥3；源点非退化（共线/共面被拒）；Umeyama 拟合强制 det=+1（不产反射）；`scale>0`；
`rms_residual ≤ --max-rms`。拟合残差/退化裕度/门禁结果记入 `sim3.alignment.v1=<json>` 证据串，
挂在 `world_frame` 与 `pose_to_world` 上，可机器复核。输出的 `registration_aligned.json` 以 LF 写出。

## 步骤 2b · 云 GPU 训练 provenance manifest（可选但推荐）

若用云 GPU（nerfstudio `ns-train splatfacto`）训练 3DGS，`cloud/train_3dgs_nerfstudio.sh`
会在训练前后自动产出两个 content-addressed manifest，本机 `prepare_import.py` 会重算每个
SHA 并 fail-closed 拒绝任何字节漂移：

- **`training-request.json`**（训练前）：绑定输入照片/视频 SHA + operator-intent `config.yml` SHA
  + 训练意图（trainer / max_res / total_steps / seed）。
- **`training-result.json`**（训练后）：从实际 PLY / config / training.log 字节派生所有 SHA
  + GPU 环境（nvidia-smi）+ trainer 版本 + 退出码 + 真实训练 UTC 起止时间。

**关键设计**（REVIEW-CODEX-023 修复后）：
- request 和 result 绑定**同一份** operator-intent `config.yml` → `actual_config_sha256 == requested_config_sha256` → 零 drift。nerfstudio 内部生成的 `config.yml` 是诊断 artefact，不作为 provenance 合同 config。
- `--max-num-iterations` 和 `--machine.seed` 通过真实 ns-train CLI 参数传入（不只写在 intent 文件里）。
- `ns-process-data` 预处理失败时也会 emit failed result（不静默退出）。

把这两个 manifest 和 PLY 一起下回本机 `trained/`，步骤 3 会消费它们绑定 trust evidence。

**三层 evidence**（`prepare_import` 根据证据强度选择追加哪层，均不提升几何信任到 metric）：

| Evidence | 条件 | 含义 |
|---|---|---|
| `training_provenance.v1=<result_sha>` | 步骤 1b 的 registration quality `training_allowed=True`（non-mock engine + capture manifest + 无拒绝原因）+ content closed + trainer identified | **trusted prefix**——仍不证明真实照片或米制 |
| `training_content_closed.v1=<result_sha>` | content closed 但 registration quality 未通过或缺 capture manifest | **content-only receipt**——只证明输入/输出字节闭合 |
| 无 evidence | 无 training-request/result 或验证失败 | 不追加任何 trust evidence |

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

### 步骤 3b · 用 `prepare_import.py` 一键生成契约 + 绑定 trust evidence

上面手写 `SplatInput` 是底层路径。更推荐用 `scripts/prepare_import.py` 一键生成
`registration.json` + `splat-input.json`，并在有 provenance manifest 时绑定三层 evidence：

```bash
.venv/bin/python scripts/prepare_import.py trained/point_cloud.ply
#   有云训练 provenance manifest 时追加：
#   --training-request trained/training-request.json \
#   --training-result   trained/training-result.json
#   有步骤 1b 的 registration quality report 时再追加（engine=colmap 才能获 trusted prefix）：
#   --registration-quality-report rq/quality-report.json \
#   --registration-json         rq/registration.json \
#   --registration-quality-policy rq/policy.json \
#   --capture-manifest           rq/capture_manifest.json \
#   --sparse-model-dir           rq/sparse/
```

`prepare_import` 会重算每个 manifest 的 SHA 并 fail-closed 拒绝任何字节漂移；
trust evidence 的三层选择见步骤 2b。**绑定 evidence 不提升几何信任到 metric**——
米制仍由步骤 2 的控制点/GPS 对齐挣得。

> ⚠️ **高阶 SH + 旋转对齐**：真实对齐的 `sim3` 一般含**非恒等旋转**（上例的 `quat_wxyz:[1,0,0,0]` 只是占位），而真实 3DGS（nerfstudio splatfacto 等）带高阶球谐 `f_rest_*`。`pipeline/spherical_harmonics.py` 已实现 degree 0–3 Wigner-D SH 旋转，含高阶 SH 的场景可直接经非恒等 Sim3 旋转对齐，**无需** 先 flatten。如需降级（减小体积或仅需视角无关基色）：`python scripts/flatten_ply_sh.py trained/drone.ply`（丢 `f_rest_*`、保 DC）。

## 步骤 4 · 验证是 measured

```bash
.venv/bin/python -c "import json; m=json.load(open('web/data/recon/recon_manifest.json')); \
c=m['coordinate_contract']; p=m['provenance']; \
print('target=',c['target_frame']['frame_id'], 'aligned=',c['alignment_status'], \
'synthetic=',p['synthetic'], 'usability=',p['geometry_usability'])"
# 期望: target= world-enu aligned= aligned synthetic= False usability= metric-aligned
```

人话版（同一份 manifest，另外做**矛盾检查**并读出**实际对齐精度**）：

```bash
.venv/bin/python scripts/inspect_recon.py web/data/recon/recon_manifest.json
```

米制通过时它会印出「真实尺度 + 地理对齐，可测量（对齐残差 X 米）」并附一句
「**别做比 X 米更精细的测量**」——X 取自 `sim3.alignment.v1` 证据串里**实际记录**的
`rms_residual_m`（多条证据时取**最差**的一条，保守）；没有该证据串时它说「精度未知」，**不猜数字**。

**它同时是个门**：manifest 声称 `metric-*` 却与自带证据矛盾（`passed:false` / 证据无法解析 /
`metric_evidence` 为空 / target frame 不是米制 / `synthetic=true` / 声称 `metric-aligned` 但没挣得
地理对齐）→ 指出矛盾、按 `preview-only` 处理、**退出码 2**。这与 `pipeline/reconstruct.py` 的
fail-closed 判据同源，用于识别外来的/被篡改的/旧版有 bug 的代码产出的 manifest。

⚠️ **限制**：它只读 manifest 的**声称**与 manifest **内部**自洽性——**不碰 PLY 字节**、不校验
`artifacts.*.sha256`、不重算残差。所以「检查通过」= manifest **自洽**，**不等于**产物没被换过：
manifest 里记着每个 artifact 的 `sha256`（PLY 摘要）、sidecar `recon_manifest.sha256` 覆盖 manifest
本身，但**这两个 `inspect_recon` 都不校验**——要查「manifest 自洽但 PLY 被换了」得另跑完整性校验。

信任根 `recon_manifest.json` / `recon/registration.json` 以 LF 写出（跨 OS 字节可复现）；
`recon_manifest.sha256` sidecar 可对 manifest 整体做完整性校验/签名。

## 边界

- 没有真实 COLMAP + GPU 训练产物时，全链只能跑 mock/synthetic（明确标注 `preview-proxy`），不冒充 measured。
- ENU→米制升级只发生在控制点/GPS + 残差达标时；任何降级/退化都 fail-closed，不静默提升。
- **provenance manifest（步骤 2b/3b）只绑定训练 provenance 信任，不提升几何信任到 metric**：
  `training_provenance.v1` trusted prefix 仍不证明照片真实、几何对 3DGS 充分或尺度米制；
  `training_content_closed.v1` content-only receipt 只证明输入/输出字节闭合。米制仍由步骤 2 挣得。
