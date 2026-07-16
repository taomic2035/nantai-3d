# HANDOFF-CODEX-003 · render-on-demand 无限世界(内核已就绪,集成层交办)

> 发起：Opus(架构/管线 lane)→ 交办：Codex(Web Viewer + Studio 层, 含 studio_server.py + web/viewer/*)
> 日期：2026-07-16
> 触发：用户 /goal「360 视角 + 任意坐标漫游」达成后「继续推进」。漫游的位置移动已通,
> 但 viewer 走出预烘的 5×5 网格后无 chunk 可加载(`main.js:253 if(!entry) return`)——
> 真正的「无限村庄」需要服务器按需渲染任意 (cx,cy)。

## 背景与 lane 边界(为什么是 HANDOFF)

按 `AGENTS.md:10`，**整个 `pipeline/studio_server.py`** 与 **`web/viewer/*`** 属你(Codex)的 lane;
且 studio_server 模块 docstring(:1-6) 明确声明「never starts … rendering」。因此 render-on-demand
的 **HTTP 端点** 与 **viewer 消费** 必须由你实现或你我协商改契约,我不单方面加。

**我(Opus)已在管线内核侧做完并验证的部分**(commit 见下),你可直接调用:

### 已就绪内核 API(纯管线, 已测)

```python
from pipeline.render_chunk_to_ply import render_single_chunk

# 任意 (含负) chunk 坐标 → ply 字节, 纯内存零落盘, 确定性
ply_bytes: bytes = render_single_chunk(chunk_x, chunk_y, world_seed=42, registry=None)
```

- **确定性**: 布局由 `(world_seed, cx, cy)` 经 `MockLayoutGenerator` 完全确定; 渲染确定性由
  per-chunk 本地 RNG 保证; ply 无时间戳/无熵 → **相同入参跨进程字节一致**(已测
  `test_render_on_demand.py::...across_processes`)→ 可安全用于内容寻址缓存 / 多实例服务器。
- **负象限安全**: 任意负坐标可渲染(已测 (-2,-3) 等)。
- **零落盘**: `BytesIO` 内存序列化, 不碰 `web/data/` 单写者锁 / provenance trust root。
- **不触溯源写路径**: `registry=None` 走合成代理, 不调 `_record_asset_consumption`(审计保护区)。

> 为让上面成立, 我先修了内核里两个真实 bug(独立于本 handoff 就该修, 违反可复现性核心价值):
> (1) `_emit_ground` 负 chunk 种子崩溃 → 掩码 `&0xFFFFFFFF`(非负 offset 字节零回归);
> (2) `_emit_road`/`_emit_building` 合成路径 6 处未播种的进程级全局 `np.random` → 改 chunk_id
> 派生的本地 `Generator`。现有测试没抓到是因为它用 vegetation-only 布局绕开了这两条路径。

## 请你实现(三件, 均在你 lane)

### 1. HTTP 端点 `GET /api/world/chunk/{cx}/{cy}.ply`(pipeline/studio_server.py)

- 插入点: `_serve`(约 :1333)里, **在 `/api/` 404 catch-all(:1428)之前**, 镜像现有
  `head_only` + `_send_bytes(status, payload, content_type, cache_control, head_only)` 模式。
- 行为: 解析 cx/cy(**必须接受负整数**), 调 `render_single_chunk(cx, cy, world_seed)`,
  以 `application/octet-stream`(:1201-1215 已映射 .ply)流式返回。**stream-only, 严禁落盘**
  (勿写 web/data、勿经 ChunkScheduler 的 layouts_dir 持久化)——否则踩 job kernel 的单写者锁。
- 安全: 走现有 GET 静态服务的 header/安全策略即可; 此端点**不经 do_POST / JobService**,
  与 codex 保护的 job/write kernel(studio_jobs.py/studio_ledger.py)结构上不相交。
- world_seed 来源: 从 project 配置 / manifest 读一个恒定值(见下 manifest 契约), 保证服务端
  按需渲染的几何与预烘的种子区一致。
- 缓存: 建议 `Cache-Control` 长期可缓存 + 未来可加 ETag=sha256(ply)(内容寻址, 因字节确定)。
- 契约冲突提示: 这会让 studio_server「渲染」, 与其 docstring(:1-6)只读声明冲突——请你(文件 owner)
  连同 docstring 一起更新, 明确「按需合成渲染是无副作用的只读派生, 不改任何 trust root」。
- 测试模板: `tests/test_studio_server.py:213-233`(_running_server/_request)+ `TestHttpContract`。
  建议断言: 正坐标 200+ply 头、负坐标 200、同坐标两次请求字节一致、坐标非整数 400。

### 2. world manifest 无限网格元数据(契约提议, 写入方待定)

viewer 要能区分「越界 → 请求」与「真无内容」, 并正确框定垂直范围。建议 manifest 增(additive):
- top-level: `grid: { on_demand: bool, url_template: "/api/world/chunk/{x}/{y}.ply", world_seed: int }`
  —— `on_demand` 默认 **false**, 保持现有静态 5×5 行为不变; 你的端点上线后置 true 才开闸。
- top-level: `bounds: {min:[x,y,z], max:[x,y,z]}` 全局 AABB(含真实 z_range)—— 让
  `framing.mjs:31-32` 别再把 z 硬编码为 0。
- per-chunk: `aabb: {min:[x,y,z], max:[x,y,z]}` —— 精确框定/垂直裁剪, 无需下载 ply。
- **勿引入 nested `grid.chunk_size_m`**: viewer 读 flat `manifest.chunk_size_m`(main.js:839,
  framing.mjs:27), 两个源会被 `?? 200` 静默掩盖 mismatch。保持单一 flat key。

写入方(**我 lane, 已做好基线, commit 见下**): `render_chunkset` 现已写出上述字段(实测):
```json
"grid": {"on_demand": false, "url_template": "/api/world/chunk/{x}/{y}.ply", "world_seed": 42},
"bounds": {"min": [x,y,z], "max": [x,y,z]},        // 全局 AABB, z 为真实建筑高度跨度(非 0)
"baked_extent": {"x_min": -1, "x_max": 1, "y_min": -1, "y_max": 1},  // 已烘焙索引闭区间
// 每个 chunk 项新增: "aabb": {"min": [x,y,z], "max": [x,y,z]}
```
`generate_world --center` 已支持以原点为中心烘焙(含负象限, 实测 3×3 → -1..1)。
这是**基线**——字段形状你若要调整(键名/嵌套)直接说, 我改; 但已可直接消费。

### 3. viewer 消费(web/viewer/*, 你 lane)

- `main.js:253-254` 的 OOB gate `if(!entry) return` → 当 `manifest.grid.on_demand` 为真时,
  改为按 `url_template` 请求 `/api/world/chunk/{cx}/{cy}.ply` 并走现有 loadChunkPly 路径。
  `coordinates.mjs:22 threeToChunk` 已能产出任意/负 (cx,cy)。gate 在 flag 后, 静态 5×5 不受影响。
- `framing.mjs:31-32`: 有了 manifest `bounds`/per-chunk `aabb` 后, 用真实 z_range 替代硬编码 0。
- 防 404 spam: on-demand 分支只在 `grid.on_demand && url_template` 存在时启用。

## 验证 / 复现

```bash
# 内核已就绪, 你可直接试:
.venv/Scripts/python.exe -c "from pipeline.render_chunk_to_ply import render_single_chunk; \
  b=render_single_chunk(9,9,world_seed=42); print(len(b), b[:3])"   # 任意越界 chunk 秒出 ply
.venv/Scripts/python.exe -m pytest tests/test_render_on_demand.py -q   # 6 绿(确定性/负索引/跨进程)
```

## 我做了 / 没做

- **做了**: 内核 bug 修复 + `render_single_chunk` + `tests/test_render_on_demand.py`(6 测)。纯 Opus lane,
  ruff 干净, asset/layout 回归 37 绿。
- **没碰**: studio_server.py、web/viewer/*、web/studio/*、studio_jobs/ledger、`_record_asset_consumption`
  溯源逻辑 —— 你的 lane / 审计保护区。
- **也已做好(我 lane, 基线可调)**: manifest 无限网格元数据写入(render_chunkset)+ generate_world
  `--center` 负象限烘焙。字段形状见 §2, 你要改直接说。
- **仍需你做**: §1 HTTP 端点 + §3 viewer 消费(把 `main.js:253 if(!entry) return` 在
  `grid.on_demand` 为真时改为按 url_template 请求)。这两步一落, "无限村庄任意坐标漫游"闭环。
