# nantai-3d

**照片/视频驱动的无限 3D 村庄世界生成系统**

基于有限照片（手机/相机/无人机）或视频作为风格/语料参考，生成可无限扩展的 3D 村庄场景，支持 Web 浏览、漫游探索与交互。

---

## 项目定位

| 维度 | 说明 |
|---|---|
| **输入** | 少量（< 30 张）混合来源照片 / 视频（mp4/mov/avi） |
| **输出** | 可生成无限地图的 3D 场景，支持 Web 浏览 |
| **用途** | 纯漫游探索 + 交互式游戏 + 训练仿真 |
| **工作流** | superpower 范式：调研 → 分析 → 设计 → 验证 → 实现 |

## 整体架构（L0–L4 五层管线）

```
┌──────────────────────────────────────────────────────────┐
│  L0  照片/视频语料化                                     │
│      ingest.py (视频抽帧 + 照片归一)                      │
│      exif_scan.py (元数据扫描 + 设备/GPS/批次报告)        │
│      ↓                                                   │
│  L0.5 统一坐标系配准                                     │
│      registration.py (COLMAP 联合 SfM / 确定性 mock,     │
│      照片+视频帧 → 同一世界坐标系, GPS/ENU 锚定)          │
│      recon_schema.py (位姿/会话/Sim3 数据契约)           │
│      ↓                                                   │
│  L1  3D 资产生成 (云端 GPU)                              │
│      Hunyuan3D-2.1 / TripoSR / Wonder3D / Trellis        │
│      SAM2 + GroundingDINO (构件分割)                     │
│      kohya-ss/sd-scripts (SDXL 风格 LoRA)                │
│      DUSt3R / MASt3R (几何验证)                          │
│      ↓                                                   │
│  L1.5 高斯泼溅场景 (gaussian_scene.py)                   │
│      3DGS ply 读写 / Sim3 变换 / 拼接 merge + 体素去重    │
│      区域替换 (补拍变清晰) / LOD 分级导出                 │
│      reconstruct.py (端到端: 配准 → 泼溅 → 拼接 → LOD)   │
│      assets.py (素材注册表: 版本化可替换)                 │
│      ↓                                                   │
│  L2  布局生成                                            │
│      glm_client.py (GLM-4.6 LLM 村庄规划师)              │
│      mock_layout.py (规则化降级方案, 无 API key 也能跑)   │
│      schema.py (pydantic ChunkLayout 数据契约)          │
│      GaussianCity (CVPR 2025 生成式扩展)                 │
│      ↓                                                   │
│  L3  UE5 PCG 实例化                                      │
│      UE5.5 PCG Framework + World Partition                │
│      Nanite + Lumen (高保真渲染)                          │
│      ↓                                                   │
│  L4  Web 双前端                                          │
│      PlayCanvas / Three.js (轻量, 当前实现)              │
│      UE5 Pixel Streaming (重端, 备选)                     │
│      py3dtiles 12.x (3DGS → 3DTiles 流式加载)            │
└──────────────────────────────────────────────────────────┘
```

## 无限世界机制

| 机制 | 实现 |
|---|---|
| 种子化确定性 chunk | `mock_layout.py` 用 `(seed, chunk_x, chunk_y)` 哈希生成，同坐标必生成同布局 |
| 视野半径调度 | `ChunkScheduler.get_visible_chunks(cx, cy, view_radius)` |
| LRU 缓存 + 淘汰 | `LRUChunkCache`（Python）/ `CHUNK_CACHE_MAX=36`（Web） |
| 跨 chunk 边界对齐 | 主路东西贯通，建筑避让道路 |
| 越界自动扩展 | 玩家走出已生成区域时按需生成新 chunk |

## 目录结构

```
nantai-3d/
├── pipeline/              # 核心管线
│   ├── ingest.py          # L0 统一输入 (视频抽帧 + 照片复制)
│   ├── recon_schema.py    # L0.5 位姿/会话/Sim3 数据契约 (统一坐标系约定)
│   ├── registration.py    # L0.5 配准 (COLMAP 联合 SfM / mock, 图+视频同系)
│   ├── gaussian_scene.py  # L1.5 3DGS 场景 (读写/变换/拼接/去重/LOD)
│   ├── reconstruct.py     # L1.5 端到端重建 CLI (配准→泼溅→拼接→LOD)
│   ├── assets.py          # 素材注册表 (版本化可替换)
│   ├── validate_handoff.py # GPT 交付物自动验收 (handoff/feedback 闭环)
│   ├── glm_client.py       # L2 GLM-4.6 布局生成 (含 mock 降级)
│   ├── mock_layout.py      # L2 规则化布局生成 (离线可用)
│   ├── chunk_scheduler.py  # 无限 chunk 调度器
│   ├── generate_world.py   # 统一 CLI 入口
│   ├── render_chunk_to_ply.py  # layout → 3DGS ply 渲染 (支持注册素材实例化 + LOD)
│   ├── schema.py          # pydantic ChunkLayout 数据契约
│   └── utils/
│       └── exif_scan.py   # EXIF + 视频元数据扫描
├── handoff/               # Claude ↔ GPT 协作 (交办/回执闭环)
│   ├── README.md          # 协作协议
│   └── HANDOFF-001-mock-assets.md  # 素材库模拟生成交办
├── tests/                 # pytest 测试套件
├── assets/                # 素材注册表 (registry.json + ply, 可替换)
├── verification/          # 关键技术验证
│   ├── verify_3dtiles_conversion.py
│   └── verify_glm_layout.py
├── cloud/                 # 云端 GPU 环境
│   └── setup_autodl.sh    # AutoDL 一键环境
├── web/                   # Web 前端
│   └── viewer/            # Three.js 动态 chunk 调度 viewer
│       ├── index.html
│       └── main.js
├── input/                 # 放置原始照片/视频 (git 忽略)
├── photos/                # 处理后的图片 (git 忽略)
├── layouts/               # 生成的 chunk 布局 JSON (git 忽略)
├── pyproject.toml         # 依赖清单
├── Makefile               # make 命令
├── .env.example           # 环境变量模板
└── README.md
```

## 快速开始

### 1. 环境准备

```bash
# Python 3.11+ (本机用 3.13 也能跑编排层)
pip install -e .

# 复制环境变量模板
cp .env.example .env
# 填入 ZHIPU_API_KEY (可选, 不填则用 mock 布局)

# fresh clone 后重建素材库 (assets/*.ply 不入库, 由确定性生成器还原 + sha256 自校验)
make assets   # 等价于 python -m pipeline.mock_assets
```

### 2. 输入处理（照片 + 视频）

```bash
# 放素材到 input/ 目录（照片视频混放）
#   input/
#   ├── DSC_0001.jpg
#   ├── DJI_0002.mp4
#   └── IMG_0003.mov

# 一键处理 (视频抽帧 + 照片复制 + 模糊筛选)
python -m pipeline.ingest --input input --output photos --fps 2

# 扫描元数据 (输出设备/GPS/批次报告)
python -m pipeline.utils.exif_scan input photos/exif_report.csv
```

**抽帧参数**

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--fps` | 2.0 | 每秒抽帧数 |
| `--max-frames` | 300 | 单视频最大抽帧数 |
| `--blur-threshold` | 80.0 | 模糊检测阈值（Laplacian variance，设 0 关闭） |
| `--max-long-edge` | 2560 | 长边降采样阈值 |

### 3. 真实重建: 图 + 视频 → 统一坐标系 → 高斯泼溅

```bash
# 端到端重建 (无 GPU / 无 colmap 也能跑通: mock 配准 + 代理泼溅)
python -m pipeline.reconstruct --photos photos

# 装了 colmap 时自动改用联合 SfM (照片与视频帧共同注册, 坐标系天然一致)
brew install colmap && python -m pipeline.reconstruct --photos photos

# 云端训练好的 3DGS ply 按会话导入并对齐拼接
python -m pipeline.reconstruct --engine import --splat video_DJI_0001=trained/dji.ply

# 补拍变清晰: 新重建自动替换旧场景对应区域
python -m pipeline.reconstruct --base-scene recon/scene_full.ply
```

设计目标 (真实 COLMAP + 云端泼溅路径成立; 本机 mock 链路仅验证数据流, 见下方"已知缺口"):
- **坐标系一致**: 所有照片/视频帧位姿收敛到同一 ENU 世界系 (Z 上, 米制, GPS 锚定)。
  ⚠️ 当前 mock 配准返回 arbitrary-scale sfm-local 位姿, 未做真实地理对齐。
- **图视频混合**: 视频帧与照片进同一配准模型, 泼溅后 merge + 体素去重无缝拼接
- **可变清晰**: LOD 三级导出 (8%/30%/100%), viewer 近清远粗; 补拍区域替换增清

### 4. GPT 素材协作 (handoff/feedback)

```bash
# 交办规格见 handoff/HANDOFF-001-mock-assets.md, GPT 交付后验收:
python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-001
# → 自动生成 handoff/FEEDBACK-HANDOFF-001.md; 全 PASS 后导入:
python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-001 --register
# 布局 JSON 不变, 重渲染即用新素材 (素材可替换):
python -m pipeline.generate_world --size 5 --seed 42
```

### 5. 生成无限世界

```bash
# 默认 5x5 = 25 chunks
python -m pipeline.generate_world --size 5 --seed 42

# 10x10 = 100 chunks, 真实 GLM 布局
python -m pipeline.generate_world --size 10 --use-glm
```

### 6. Web 浏览

```bash
cd web && python -m http.server 8000
# 浏览器打开 http://127.0.0.1:8000/viewer/index.html
```

**操作键位**

| 键 | 功能 |
|---|---|
| `W A S D` | 移动相机 |
| `Shift` | 加速 |
| `Q E` | 升降 |
| `鼠标拖拽` | 旋转视角 |
| `B` | 切换 chunk 边界显示 |
| `1 2 3` | 强制画质 (低/中/高), `0` 恢复按距离自动 |
| `R` | 切换真实重建图层显示 |

## 当前状态

| 层 | 状态 | 说明 |
|---|---|---|
| L0 输入处理 | ✅ 已完成 | 照片 + 视频混合输入，cv2 抽帧 + 模糊筛选 |
| L0 元数据扫描 | ✅ 已完成 | EXIF + 视频元数据，CSV 报告 |
| L0.5 统一坐标系配准 | 🟡 部分完成 | COLMAP 联合 SfM + mock 降级已通; **mock 返回 arbitrary-scale sfm-local 位姿, 真实 ENU/米制地理对齐待接真实 COLMAP+GPS** |
| L1.5 高斯泼溅场景 | ✅ 已完成 | 3DGS 读写/Sim3 变换/拼接去重/区域替换/LOD 分级 |
| L1.5 端到端重建 CLI | ✅ 已完成 | mock 代理泼溅本机可跑通; import 引擎接云端训练产物 |
| 素材注册表 | ✅ 已完成 | 版本化可替换, 渲染器自动实例化注册素材 |
| GPT 协作闭环 | ✅ 已完成 | handoff 交办 + 自动验收 + feedback 回执 (HANDOFF-001 待 GPT 交付) |
| L2 布局生成 | ✅ 已完成 | GLM-4.6 + mock 降级，pydantic schema |
| L2 chunk 调度 | ✅ 已完成 | 种子化确定性 + LRU 缓存 + 边界对齐 |
| L4 Web viewer | 🟡 部分完成 | Three.js 动态 chunk 调度 + 距离 LOD + 重建图层 + mini-map; **当前为 DC point preview (THREE.Points), 非真实 splat renderer** |
| 素材可移植 | ✅ 已完成 | 确定性生成器 (`pipeline/mock_assets.py`) + registry sha256 自校验, fresh clone 用 `make assets` 还原 |
| 测试套件 | ✅ 已完成 | pytest 63 项 (配准/泼溅/素材/验收/端到端/可移植性) |
| L1 真实 3D 资产 | ⏳ 待云端 GPU | Hunyuan3D-2.1 + gsplat 训练 → --engine import 导入 |
| L2 GLM 真实 API | ⏳ 待 API key | mock 已可用 |
| L3 UE5 PCG | ⏳ 待 UE5 安装 | PCG Framework + World Partition |

### 已知缺口 (架构复审 P0)

内部复审 `handoff/FEEDBACK-ARCH-P0-002.md` 列出 8 个待关闭 P0, 本轮已关闭可移植性 (P0#8)
与打包门禁 (P0#7); 其余为需真实 GPU/COLMAP 或深层重写的项, 尚未完成, 不应按"真实/米制"解读:

- **P0#1** COLMAP 位姿实为 arbitrary-scale sfm-local, 未做 ENU/米制地理对齐
- **P0#2** Sim3 "恰好应用一次" 缺预对齐拒绝 / 重复变换失败保护
- **P0#3** viewer world→Three 映射 det=-1 (右手性翻转), 缺 JS handedness 测试
- **P0#4** 泼溅仍丢弃 `f_rest_*` (仅 DC), viewer 为 point sprite 非真实 splat
- **P0#5** provenance 未在产物/HUD 标注 synthetic proxy
- **P0#6** vegetation 尚未真实消费 registry `asset_ids`

## 技术栈

**编排（本机 CPU）**
- Python 3.11+, pydantic 2.x, loguru, rich
- opencv-python (视频抽帧 + 模糊检测)
- exifread (照片元数据)
- py3dtiles 12.x (3DGS → 3DTiles 流式)
- trimesh / plyfile (几何处理)

**AI 推理（云端 GPU）**
- Hunyuan3D-2.1 (照片 → 3D 资产)
- SAM2 + GroundingDINO (构件分割)
- kohya-ss/sd-scripts (SDXL 风格 LoRA)
- DUSt3R / MASt3R (几何验证)

**LLM 布局**
- GLM-4.6 (智谱，注册送 1 亿 token)

**Web 前端**
- Three.js (动态 chunk 调度 + LRU 缓存)
- PlayCanvas SuperSplat (备选)
- UE5 Pixel Streaming (重端备选)

**云端 GPU**
- AutoDL 按秒计费（RTX 3060 12GB ¥1/h 起）
- 详见 [cloud/setup_autodl.sh](cloud/setup_autodl.sh)

## 参考

- **生成式世界模型**：Hunyuan3D-World Voyager、CityDreamer4D、GaussianCity (CVPR 2025)、SceneDreamer
- **照片到 3D 资产**：Hunyuan3D-2.1、TripoSR、Wonder3D、Trellis
- **程序化生成**：UE5.5 PCG Framework、World Partition、Nanite、Lumen
- **3DGS 入门**：3DGS 入门指南、Polycam Gaussian Splats 移动方案
- **COLMAP**：多视图几何实践
- **RealityCapture**：全面解析

## License

[MIT](LICENSE)
