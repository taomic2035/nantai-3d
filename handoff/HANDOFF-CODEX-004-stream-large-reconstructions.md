# HANDOFF-CODEX-004 · 大重建的分块流式（管线已就绪，viewer 消费交办）

> 发起：Opus（架构/管线 lane）→ 交办：Codex（Web Viewer lane）
> 日期：2026-07-17
> 触发：用户「聚焦高价值功能」。合成村庄已能分块流式 + 按需无限漫游；**真实重建**却是
> 单个可能上百万高斯的 `.ply`，viewer 只能整块加载 —— 大场景下载慢、无空间裁剪，
> 站在村东也得载完整个村。这是真实重建 360° 任意坐标漫游的最后一块短板。

## 我（Opus）已做完并实测的部分

`pipeline/spatial_chunk.py` + `scripts/chunk_reconstruction.py`：把一个大重建 3DGS PLY
按 XY 网格切成 **per-chunk ply + LOD + `chunks.json` 流式 manifest**。

```powershell
.venv\Scripts\python scripts\chunk_reconstruction.py trained\point_cloud.ply `
  --out-dir web\data\recon-chunks --chunk-size-m 50
```

**实测（真实规模）**：12 万高斯 / 400m 场景 / 8MB ply → **64 块**（50m 网格），
源坐标契约（`world-enu` / `meters`）原样保留，LOD 全出，bounds 精确。

### 铁律（已 TDD 锁定，8 测）

- **无损分块**：半开区间 `[cx*size, (cx+1)*size)` 分箱（复用 `GaussianScene.crop_aabb`
  同语义）→ 每个高斯**恰好落一个块**，不丢不重复（重载全部块 == 源高斯数）。
- **坐标绝对不动**：块内高斯保持源 frame 的**绝对坐标**（不平移）。逐轴排序比对 == 源点集。
- **provenance 不增不减**：每块继承源 `frame_id`/`units`/`applied_transform_ids`
  （`crop_aabb`→`_subset` 保留）；manifest 的 `source` 如实记录源契约。
  **分块绝不把 `preview-only` 变成 `metric-aligned`** —— 米制得在 alignment 那步挣。
- manifest LF 字节可复现；同场景分块确定。

## `chunks.json` 契约（你消费的形状）

```json
{
  "schema_version": 1,
  "kind": "spatial-chunks",
  "chunk_size_m": 50.0,
  "chunks": [
    {
      "id": "0_-1", "x": 0, "y": -1,
      "ply_file": "chunk_0_-1.ply",
      "lod": {"0": "chunk_0_-1_lod0.ply", "1": "chunk_0_-1_lod1.ply", "2": "chunk_0_-1.ply"},
      "point_count": 1873,
      "aabb": {"min": [0.0, -50.0, 0.0], "max": [49.9, -0.1, 14.9]}
    }
  ],
  "lod_fractions": {"0": 0.08, "1": 0.3, "2": 1.0},
  "total_chunks": 64, "total_points": 120000,
  "bounds": {"min": [x,y,z], "max": [x,y,z]},
  "core_bounds": {
    "min": [x,y,z], "max": [x,y,z],
    "axis_percentile": 0.995,
    "contains_points": 66544, "contains_fraction": 0.9803
  },
  "extent": {"x_min": -4, "x_max": 3, "y_min": -4, "y_max": 3},
  "source": {
    "frame_id": "world-enu", "units": "meters", "applied_transform_ids": [],
    "geometry_usability": "metric-aligned",
    "recon_manifest_sha256": "…"
  }
}
```

**`source` 是你标注信任的唯一依据**：`geometry_usability`（`preview-only` / `metric-aligned` /
`preview-proxy`）是**源 recon manifest 挣得的判定**，由 `chunk_reconstruction.py --recon-manifest
recon/recon_manifest.json` 搬运进来，并附该 manifest 的内容寻址 `recon_manifest_sha256` 供回溯核验。
**分块从不产生判定**：未给 `--recon-manifest` 时这两个键**缺席 = 未知**（不是 preview-only，
更不是 metric）—— 缺席就别标注信任等级，别猜。

与合成村庄 manifest **同构**（`chunks`/`lod`/`aabb`/`bounds`；`lod2` == 全量），
故你的现有 chunk 流式路径应能大部分复用。差异：
- **无 `grid`**：重建**不可**按需程序化生成（几何来自真实训练，非 seed 派生）→
  **绝不可**对它投影 `on_demand:true`；越界就是没有内容。
- **`kind: "spatial-chunks"`** 用于与合成世界 manifest 区分。
- **坐标是绝对的**（块内已是源 frame 的真实坐标），无 `world_offset` 概念；请用
  `aabb` 做裁剪/取景，勿再对块做平移。
- **`source`** 带源坐标契约：viewer 若要显示"米制/preview-only"标注，**以它为准**，
  不要因为"分块了"就升级任何声称。

## 追加（2026-07-17）：**取景请用 `core_bounds`，别用 `bounds`**

> 契约**只增不改**，你已写的代码不会被破坏；但这条会直接影响你的取景观感。

我拿仓库里那个 **Brush 实训**重建（canary，67878 高斯）实测了自己发布的分块产物，
发现 `bounds` 正在**误导取景**：真实 3DGS 训练必然产出**漂浮物**（少数高斯被优化到
场景外几百米）。

| | 实测 |
|---|---|
| `bounds`（全量真相） | **1328 × 877 × 720 m** |
| Z 向 90% 分位 | **52.6 m** ← 被漂浮物撑大 **13 倍** |
| `core_bounds`（新） | **567 × 516 × 154 m**（Z 收紧 4.7×） |

**你若按 `bounds` 取景，相机会停在几百米外对着空气。请改用 `core_bounds`。**

**这不只影响分块 manifest。** 我查了你的 `web/viewer/framing.mjs`，它自述
*"Derive camera, clipping, fog, grid and target values from artifact bounds"* ——
也就是**相机、裁剪面、雾、地面网格**全部由 `bounds` 推导。所以在**不分块的主路径**上
同一个谎原样存在，而且影响的不只是取景。我已经把 `core_bounds` 也加进了
**主 manifest**（`recon_manifest.json`，`pipeline/reconstruct.py`，commit `aaa1e0e`），
形状与下面完全一致。`framing.mjs` 两条路径都可以直接读 `core_bounds`。

**兼容**：老产物没有 `core_bounds` 键。**缺席时请回退到 `bounds`**（缺席 = 未知，
不是"没有漂浮物"）—— 别因为键缺失就假设几何是干净的。

语义（这几条别搞混）：
- **`bounds` 仍是全量真相，永不缩水** —— `core_bounds` 是**附加**提示，**不是**替代品。
- **不隐藏任何几何**：落在 core 外的点**照常在块里、照常渲染**。`core_bounds` 只回答
  "主体在哪"，**不是**"哪些该丢"。**别拿它做裁剪**，它不是可见性判据。
- **`contains_fraction` 是实测数**（真去数了盒内点数），不是从 `axis_percentile` 推算的。
  实测 **0.9803**，而不是 `0.995` —— 逐轴分位盒的三轴联合覆盖**严格小于**单轴分位
  （各轴尾部不是同一批点）。**用 `contains_fraction`，别自己拿 0.995 当覆盖率**。
- `axis_percentile` 自述造盒判据（与 `lod_fractions` 同理：语义写出来，你不用猜）。
- **分位是启发式，不是"漂浮物"的定义**。所以我**没有**据此丢弃任何几何 —— 那是有损的
  判断，得用户显式拍板，不该由分块器偷偷替他决定。

顺带一个**我还没解决**的真实问题，先如实告诉你：那 67878 高斯被切成 **256 块**（×3 LOD
= 768 文件），**中位数每块仅 12 点**，**202 块（79%）≤100 点** —— 绝大多数块是漂浮物噪声。
你的调度器会老老实实去拉这 256 个块。**每块有 `point_count`，你可以据此排优先级**；
根治要在管线侧做显式的离群点剔除（我的 lane，正在推进）。

## 请你做

1. **viewer 消费 `kind:"spatial-chunks"` 的 manifest**：按 `aabb` 与相机距离选块 +
   选 LOD（0 远 / 1 中 / 2 近），只载视野附近的块。可直接复用现有 chunk 流式与 LOD 选级逻辑。
   **`lod_fractions` 声明了每一级的实际密度比例**（含 `"2": 1.0` 全量）—— 按距离选级时
   直接读它，不用猜 lod0 到底是 8% 还是别的（不同世界/不同烘焙可用不同比例）。
2. **provenance 显示以 `source` 为准**（分块不改信任等级）。
3. **不要给它开按需**（无 `grid`；重建无法程序化续渲，越界即无内容）。
4. 若你希望我调整 manifest 形状（键名/嵌套/加 `world_offset` 以复用更多现有代码），
   **直接说，我改** —— 这是基线，不是定局。

## 我做了 / 没做

- **做了**：`pipeline/spatial_chunk.py`（分块内核）+ `scripts/chunk_reconstruction.py`（CLI）
  + `tests/test_spatial_chunk.py`（8 测：无损/绝对坐标/manifest 契约/LOD/provenance/LF 确定性/
  非法尺寸与空场景 fail-closed）+ 手册 §6 文档。ruff 干净。
- **没碰**：`web/viewer/*`、`web/studio/*`、`studio_server.py` —— 你的 lane。
- **前提**：分块通常在 normalize → flatten（若 SH+对齐）→ import/align 之后做，对**已对齐**
  的产物分块最有用（否则块是 preview-only 的，仍可漫游但无米制意义）。
