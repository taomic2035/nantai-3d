# AUDIT-2026-07-22 — 离真实 3D 场景的差距审计

> 日期：2026-07-22
> 发起：Opus lane (GLM-5.2 临时接替)
> 受众：用户 + Codex
> 方法：基于代码、文件、SHA 实测证据，不做推断
> 结论：**代码编排链已就绪，真实数据为零。7 维度中 5 个为 0% 真实，2 个为部分就绪。**

## 1. 一句话结论

仓库是一台精密的"烤箱"（管线+契约+Viewer），但没有"食材"（照片/视频）也没有"火力"（GPU）。所有 fail-closed 门都在正确地阻止合成代理冒充实测重建——这证明管线本身是健康的，但离"真实 3D 场景"还差三个外部依赖。

## 2. 7 维度差距矩阵

| 维度 | 真实度 | 代码就绪度 | 阻塞原因 |
|---|---|---|---|
| 模型几何 | **0%** | 30% | 程序化盒子高斯代理，无真实 mesh |
| 纹理贴图 | **0%** | 10% | AI 生成 albedo，无真实照片纹理 |
| 真实感 | **0%** | 20% | 几何+纹理均为合成 |
| 360° 视角 | — | **40%** | Viewer 就绪但只渲染合成代理 |
| 任意坐标 | — | **30%** | render-on-demand 内核就绪但输出合成代理 |
| 流畅感 | — | **60%** | LRU+LOD+ETag 机制最接近就绪 |
| 照片真实感 | **0%** | 10% | 需要 COLMAP+GPU 训练，均缺失 |

注：百分比表示"就绪度"而非"完成度"。0% 真实 = 无任何真实数据/几何；代码就绪度 = 管线机制是否可用。

## 3. 逐维度事实证据

### 3.1 模型几何 — 0% 真实

**当前状态**：程序化合成 boxout + 合成 mesh

- `pipeline/render_chunk_to_ply.py:693-717` 的 `render_single_chunk` 输出**程序化合成代理**：
  - `_emit_building` (L224-L324)：8 角点盒子 + 4 面墙采样 + 屋顶倾斜面，颜色硬编码 `COLOR_BUILDING_WALL=(170,130,90)` / `COLOR_BUILDING_ROOF=(140,50,40)`
  - `_emit_road` / `_emit_water`：沿线段平铺高斯，硬编码灰/蓝
  - `_emit_proxy_vegetation` (L327)：`"Missing-asset fallback: deterministic green volume proxy"`
  - `_emit_ground` (L130)：稀疏草色基础层 4000 点
  - docstring 明确写：`registry=None 走合成代理 (不触溯源写路径)`
- `pipeline/synthetic_village/mesh_asset_build_v2.py:135`：
  - `mesh_algorithm_id: Literal["synthetic-template-mesh-v1"]`
  - `synthetic: Literal[True] = True`
  - `verification_level: Literal["L0"] = "L0"`
- `scripts/blender/` 下 12 个 .py 脚本是真实 Blender headless 脚本，但生成的几何被显式声明为 `synthetic=True, verification_level="L0"`
- `.nantai-studio/synthetic-village/hybrid-v3/work/audit/batch6-prototype-v1.blend` 是私有原型（不进 registry/Git/Release），且是"稀疏块体村庄"
- **全仓无任何真实 mesh / .ply 重建产物**

### 3.2 纹理贴图 — 0% 真实

**当前状态**：AI 生成合成 albedo，无真实照片纹理

- `assets/registry.json` **不存在**（本 Windows 机器）；`assets/*.ply` **无任何文件**
- AGENTS.md 记录的 "11/11" 是 macOS 机器上的状态，本机为 0
- `pipeline/` 下无 `materials/` 目录；PBR 材质由 `pipeline/synthetic_village/` 下 `material_bundle.py`、`h3_material_authoring.py` 等模块管理（合成）
- `handoff/FEEDBACK-IMAGE2-019-batch15-material-albedo-sources.md` 关键事实：
  - 6 张原始来源全部为 imagegen 生成
  - 信任字段：`synthetic=true`、`metric_texel_scale=unknown`、`seamless_edges=not-verified`、`color_space=unknown-unprofiled-png`、`pbr_map_consistency=not-generated`、`texture_use=albedo-source-only-not-registered`、`real_photo_textures=false`、`trust_effect=none`
  - 接缝审计：6 张全部 `not seamless`
  - 明确记录：`"没有对应的 measured roughness、normal、height 或 displacement，也不是南台村真实墙体、屋瓦或溪床照片"`

### 3.3 真实感 — 0% 真实

**当前状态**：几何和纹理均为合成

- 看到的是"彩色盒子村庄"（硬编码颜色的程序化高斯聚簇），不是照片级场景
- Blender 实渲预览（build `4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc`）包含 PBR 材质和四张实渲预览，但仍是稀疏块体村庄
- 它声明 `geometry_usability=preview-only`、`fidelity=simplified-pbr-not-render-parity`

### 3.4 360° 视角 — 40% 就绪

**当前状态**：Viewer 渲染层就绪，但只渲染合成代理

- `web/viewer/main.js`：Three.js Web Viewer，支持 `WASD + 鼠标相机控制`
- `web/viewer/splat-chunks-layer.mjs`：高斯泼溅 chunk 渲染
- `web/viewer/world-chunks.mjs`：按需 chunk 加载（`grid?.on_demand === true`）
- `web/viewer/vendor/spark/spark.module.js`：Spark 3DGS 渲染器
- `pipeline/studio_server.py`：`GET /api/world/chunk/{x}/{y}.ply` 端点可用
- **阻塞**：端点调用的是 `render_single_chunk`（合成代理），不是真实重建分块

### 3.5 任意坐标 — 30% 就绪

**当前状态**：render-on-demand 内核已验证，但输出合成代理

- `render_single_chunk(cx, cy, world_seed=42, registry=None, lod=None)` 已对抗性验证 CLEAN：
  - 纯内存零落盘、任意含负坐标、确定性、跨进程字节一致
  - LOD 0/1/2 分级省带宽
  - registry 真实素材路径亦已验证字节确定且纯读无副作用
- world manifest 已带无限网格元数据：`grid{on_demand, url_template, world_seed}`
- **阻塞**：当前使用确定性合成代理（`registry=None`）；真实可替换素材的跨 worker 缓存须先有 asset version/SHA 内容键

### 3.6 流畅感 — 60% 就绪

**当前状态**：机制最接近就绪

- LRU chunk 加载/卸载 + 视野半径动态调度已实现
- LOD 0/1/2 三级密度分级
- ETag/304 缓存协商已实现
- Spark 3DGS 渲染器就绪（降级时标注 DC point preview）
- `GET /api/world/chunk/{x}/{y}.ply` 支持 stream-only 无落盘
- **阻塞**：无真实高斯可渲染——流畅的"合成盒子"不是目标

### 3.7 照片真实感 — 0% 真实

**当前状态**：需要 COLMAP + GPU 训练，均缺失

- 端到端链路：采集 → check_capture 预检 → COLMAP SfM → 云 GPU 3DGS 训练 → normalize_ply_quats → flatten_ply_sh → prepare_import → reconstruct --engine import → alignment → inspect_recon → 360° 漫游
- `docs/manual/reconstruction-setup.md` 是 232 行完整手册，明确：`"本仓库不把图片变成 3D 几何"`
- **三项外部依赖全部缺失**：
  1. 真实照片/视频：`photos/`、`input/`、`recon/`、`trained/` 均不存在
  2. COLMAP：`third/colmap/` 不存在，doctor.py 会报 `MISSING`
  3. NVIDIA GPU：开发机仅 Intel UHD 770 集显（无 CUDA），`third/brush/` 不存在

## 4. 三个缺失的外部依赖（按优先级）

### 4.1 真实照片/视频数据集（CRITICAL）

- `photos/`、`input/`、`recon/`、`trained/` 均不存在
- 全仓无任何真实照片或视频数据集
- **没有数据，COLMAP 和训练器都无从运行**
- `scripts/check_capture.py` 已就绪，可预检张数/模糊/分辨率/EXIF GPS
- 红线：`"重叠度是图之间的关系，单图分析测不到"` → 预检通过不等于能重建

### 4.2 COLMAP（HIGH）

- `third/colmap/` 不存在
- `third/README.md` 列出 COLMAP 4.1.0 下载 URL，但未实际下载
- `pipeline/registration.py:8`：无 colmap 时回退 mock 引擎 → 合成位姿，非真实重建
- 本机 CPU 可跑 COLMAP（~300 图 2-5+ 小时），但需先安装

### 4.3 NVIDIA GPU / 3DGS 训练器（HIGH）

- 开发机：Intel UHD 770 集显，无 CUDA
- `third/brush/` 不存在
- 仓库无训练器（按设计）
- `pipeline/reconstruct.py` 的 `engine="import"` 路径正是为消费外部训练产物设计
- 实际主路径 = **云 GPU 租赁**（gsplat/nerfstudio），但未执行
- macOS Apple Silicon 可跑 Brush 受限小场景试验档（不等于 CUDA 训练器替代）

## 5. 已就绪的代码机制（不是白做）

这些在真实数据到位后会立即发挥作用：

| 机制 | 代码位置 | 状态 |
|---|---|---|
| 坐标契约 + fail-closed provenance | `CoordinateFrame`、`FrameTransform` | verified |
| ENU 米制对齐 | `pipeline/alignment.py` | verified, fail-closed |
| 3DGS 导入/拼接/分块/LOD | `pipeline/reconstruct.py` + `pipeline/spatial_chunk.py` | verified |
| 高阶 SH 限制（fail-closed） | `flatten_ply_sh.py` | verified |
| Viewer 渲染层 | `web/viewer/` (Three.js + Spark) | verified with fallback |
| render-on-demand 无限世界 | `pipeline/render_chunk_to_ply.py` | verified CLEAN |
| 诚实 UX 三件套 | `scripts/doctor.py`、`check_capture.py`、`inspect_recon.py` | verified |
| 180 相机生产计划 | `pipeline/synthetic_village/production_profile.py` | verified plan |
| reciprocal route module | `pipeline/synthetic_village/reciprocal_route_module.py` | verified plan |
| elevated topology (4 loops) | `pipeline/synthetic_village/elevated_topology.py` | verified |

## 6. 通向真实场景的路径

```
采集真实照片(手机/无人机)     ← 用户需要做
    ↓
check_capture 预检          ← 代码就绪
    ↓
COLMAP SfM (CPU 数小时)      ← 需安装 COLMAP
    ↓
云 GPU 训练 3DGS            ← 需云账号，仓库无训练器
    ↓
normalize_ply_quats          ← 代码就绪
    ↓
flatten_ply_sh (米制对齐前)  ← 代码就绪
    ↓
prepare_import               ← 代码就绪
    ↓
reconstruct --engine import  ← 代码就绪
    ↓
alignment --from-gps         ← 代码就绪(消费级GPS精度3-10m)
    ↓  (或 --control-points for sub-metre)
inspect_recon                ← 代码就绪
    ↓
360° 漫游真实场景            ← Viewer 就绪
```

**最关键的阻塞**：第一步（采集照片）和第三步（云 GPU 训练）是**只有用户能做的事**——代码无法替代。

## 7. 机器环境实测

- **开发机**：Windows 11 / i7-14700 (20核) / 32GB / D盘 1.4TB / **Intel UHD 770 集显（无 NVIDIA、无 CUDA）**
- **COLMAP**：未安装（`third/colmap/` 不存在）
- **Brush**：未安装（`third/brush/` 不存在）
- **Blender**：`third/blender/` 不存在（canary 工具链锁定的 Windows x64 路径缺如）
- **素材 registry**：`assets/registry.json` 不存在，count=0
- **GPU**：无 nvidia-smi

## 8. 不假装的边界

按 AGENTS.md 非协商约定（2026-07-15）：

- **Provenance safety / fail-closed**：可信度只从机器可验证字段推导，绝不从文件名/engine 名推断；未知 → 可预览但永不静默提升为 measured/metric/aligned
- **不假装可以又不说实际问题**：如实标注每个限制、外部依赖、真实耗时
- 当前所有合成产物均声明 `synthetic=true`、`geometry_usability=preview-only`、`trust_effect=none`
- 管线正确地拒绝把合成代理冒充实测重建——这是特性不是 bug

---

Co-Authored-By: GLM-5.2 <noreply@z.ai.com>
