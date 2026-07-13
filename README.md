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
│  L1  3D 资产生成 (云端 GPU)                              │
│      Hunyuan3D-2.1 / TripoSR / Wonder3D / Trellis        │
│      SAM2 + GroundingDINO (构件分割)                     │
│      kohya-ss/sd-scripts (SDXL 风格 LoRA)                │
│      DUSt3R / MASt3R (几何验证)                          │
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
| LRU 缓存 + 淘汰 | `LRUChunkCache`（Python）/ `CHUNK_CACHE_MAX=16`（Web） |
| 跨 chunk 边界对齐 | 主路东西贯通，建筑避让道路 |
| 越界自动扩展 | 玩家走出已生成区域时按需生成新 chunk |

## 目录结构

```
nantai-3d/
├── pipeline/              # 核心管线
│   ├── ingest.py          # L0 统一输入 (视频抽帧 + 照片复制)
│   ├── glm_client.py       # L2 GLM-4.6 布局生成 (含 mock 降级)
│   ├── mock_layout.py      # L2 规则化布局生成 (离线可用)
│   ├── chunk_scheduler.py  # 无限 chunk 调度器
│   ├── generate_world.py   # 统一 CLI 入口
│   ├── render_chunk_to_ply.py  # layout → 3DGS ply 渲染
│   ├── schema.py          # pydantic ChunkLayout 数据契约
│   └── utils/
│       └── exif_scan.py   # EXIF + 视频元数据扫描
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

### 3. 生成无限世界

```bash
# 默认 5x5 = 25 chunks
python -m pipeline.generate_world --size 5 --seed 42

# 10x10 = 100 chunks, 真实 GLM 布局
python -m pipeline.generate_world --size 10 --use-glm
```

### 4. Web 浏览

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

## 当前状态

| 层 | 状态 | 说明 |
|---|---|---|
| L0 输入处理 | ✅ 已完成 | 照片 + 视频混合输入，cv2 抽帧 + 模糊筛选 |
| L0 元数据扫描 | ✅ 已完成 | EXIF + 视频元数据，CSV 报告 |
| L2 布局生成 | ✅ 已完成 | GLM-4.6 + mock 降级，pydantic schema |
| L2 chunk 调度 | ✅ 已完成 | 种子化确定性 + LRU 缓存 + 边界对齐 |
| L4 Web viewer | ✅ 已完成 | Three.js 动态 chunk 调度 + mini-map |
| L0 EXIF → ply | ✅ 已完成 | 合成 ply 验证 |
| L1 真实 3D 资产 | ⏳ 待云端 GPU | Hunyuan3D-2.1 + SAM2 + GroundingDINO |
| L2 GLM 真实 API | ⏳ 待 API key | mock 已可用 |
| L3 UE5 PCG | ⏳ 待 UE5 安装 | PCG Framework + World Partition |

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
