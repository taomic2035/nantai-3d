# 2026-07-16 · Canary 渲染帧 COLMAP 可行性实测（phase-2 前置证据）

> 执行：Opus（重建管线 lane）。只读试验：输入为 canary 已 verified 的 24 帧 RGB 的**副本**，
> 全部输出在会话 scratchpad，未写 `.nantai-studio/`、未写仓库树、未触碰任何 trust root。
> 目的：在 phase-2（180 相机 + 视频路线 + COLMAP + 3DGS）规划前，实测「当前 canary 渲染
> 能否被 COLMAP 注册」，替代猜测。

## 结论（TL;DR）

**当前（无纹理白模阶段）的 24 帧 canary 渲染无法被 COLMAP 注册：注册率 0%**
（mapper 找不到任何合格初始图像对，未产生任何稀疏模型）。根因是两个因素叠加：

1. **平面着色、无纹理材质**：SIFT 依赖纹理梯度。实测平均每帧仅 **345** 个特征点
   （min **0** / max 1134；真实照片同分辨率通常数千）。纯色墙面/屋顶/地形提供不了可区分特征。
2. **24 个离散宽基线机位覆盖 700×500 m**：即使有特征的帧，视角差异过大。
   276 个图像对中仅 **13 对**通过两视几何验证，验证匹配总数仅 **263**
   （COLMAP 初始化单对通常就需要 ~100+ 内点）。

## 实测环境与命令

- 输入：`work/canary/344e643c…/renders/rgb/` 的 24 帧 1024×576 PNG（journal 全部 `verified`）副本。
- 工具链：`third/colmap` COLMAP 4.1.0 no-CUDA，CPU SIFT + exhaustive_matcher（24 图 → 276 对）；
  驱动脚本 `scripts/reconstruct_local.py`（含 `b3fb3f1` 多子模型/注册率报告）。
- 结果：`feature_extractor`/`exhaustive_matcher` 完成；`mapper` 反复放宽初始化约束后
  `No good initial image pair found` → `Failed to create any sparse model`，exit 1。
- 包装层行为正确：fail-closed、如实报错、无部分结果被注册（注册率报告路径未及触发——
  连一个子模型都没有）。

数据库统计（`colmap.db`）：

| 指标 | 值 |
|---|---|
| images | 24 |
| keypoints / 帧 (min/avg/max) | 0 / 345 / 1134 |
| 有原始匹配的对 | 17（匹配总数 442） |
| 几何验证通过的对 | 13（匹配总数 263） |
| 稀疏模型 | **0 个** |

## 对 phase-2 的含义

1. **codex 正在生成的材质/细节素材是承重结构，不是装饰**。任何「COLMAP-on-renders」
   的验证故事都必须在**贴图后的**场景上重测；本文档是贴图前的基线（0%），
   贴图后的对照实验可直接复用同一流程。
2. **离散稀疏机位即使贴图后也可能不够**。规划中的视频路线（时间上稠密重叠 →
   sequential_matcher）才是可靠的注册路径；180 discrete 机位若仍宽基线，风险类似。
3. **合成数据其实可以绕过 COLMAP**：canary 已输出每相机标定内参 + OpenCV c2w 外参
   （ground truth）。3DGS 训练可直接吃 GT 相机；COLMAP-on-renders 只在需要
   「按真实照片流程彩排整条管线」时才必要。phase-2 应显式区分这两条路线的目标。

## 复现

```powershell
# 副本输入 + 隔离工作目录（勿指向 .nantai-studio 或仓库树）
.venv\Scripts\python.exe scripts/reconstruct_local.py <rgb副本目录> `
  --work <隔离目录>\recon-ws --web <隔离目录>\web-out --steps 800
# 统计匹配质量
python -c "import sqlite3; db=sqlite3.connect(r'<...>\recon-ws\colmap.db'); ..."
```

## 对照实验：GT 相机直训路线 —— 全链路打通（同日补充）

COLMAP 路线证死后，实测了绕过 SfM 的路线：**canary GT 相机 + GT 深度 → 直接训练 3DGS**。
新脚本 `scripts/canary_gt_to_colmap.py`（消费 codex 的
`nantai.synthetic-village.camera-metadata.v1` 契约）把 24 个 GT 相机转成 COLMAP 文本模型
（3 组 FOV 内参去重、w2c 四元数），并用 GT 深度反投影出 **49,308** 个带色初始化点
（stride 16）。三道 fail-closed 自校验全过：

| 校验 | 结果 |
|---|---|
| c2w 旋转刚性 (正交 + det=+1) | 24/24 通过 |
| 四元数往返复原旋转矩阵 | 24/24 通过 (atol 1e-6) |
| 跨相机深度一致性 (A 反投影 → B 投影 vs B 的 GT 深度) | 中位相对误差 **0.0008–0.0011** |
| 转换确定性 (两次独立运行) | sparse/0 三文件逐字节一致 |

深度一致性 ≈0.1% 同时证明了 **codex 的相机元数据契约精确且互洽**
（measured_c2w_opencv + 欧氏距离深度编码 + 像素中心偏移 [0.5,0.5]），可放心消费。

下游全链路（本机 Intel iGPU，无 CUDA）：Brush 2000 步 (~3 分钟) → trained.ply
**68,432 高斯、四元数全单位**（normalize 报 0 个需修）→ prepare_import 契约 →
`pipeline.reconstruct --engine import` 成功：68,432 高斯 + LOD 0/1/2 + manifest。
尺度合理性：高斯 5–95 分位包围盒 x∈[-202,180] y∈[-121,141] z∈[40,100] m，
恰好括住三个聚落中心 creekside(-180,-90) / central(0,0) / upper(170,115) ——
**米制世界系数值在整条链路中原样保留**（尽管契约按 sfm-local/preview-only 申报，见下）。

### 两条路线对照（phase-2 决策依据）

| | COLMAP-on-renders | GT 相机直训 |
|---|---|---|
| 白模阶段 | **0% 注册，死路** | **全链路通** |
| 依赖纹理 | 是（SIFT） | 否 |
| 贴图后 | 需重测（本文档流程可复用） | 不变 |
| 证明什么 | 「按真实照片流程彩排」 | 合成数据的 3DGS 生产路径 |
| 尺度 | sfm-local 任意尺度 | 数值即米制（申报仍 preview-only） |

诚实边界：GT 路线产物目前走 prepare_import 默认契约 = `sfm-local` 非米制申报
（preview-only），尽管数值就是米制 Blender 世界。要 MEASURED 米制申报需走
`pipeline.alignment` 控制点路线（合成场景可从场景计划生成完美控制点）——
留待 phase-2 按需决定，勿默默提升信任等级。

### 顺带修复：导入路径的 synthetic 错标（在真实 viewer 里发现）

把导入产物部署进 Studio viewer 实机验证时发现面板显示 `synthetic: false`——
canary 衍生的 3DGS 被错标为非合成。根因：操作者明知来源合成，但 `prepare_import`
硬编码 `FrameProvenance.SFM`，没有申报通道；而 `reconstruct.py` 的分类逻辑本就支持
从 frame provenance 推导 synthetic（且已有参数化测试覆盖）。修复（TDD，先红后绿）：
`prepare_import.py --synthetic` → frame `synthetic-local` + `FrameProvenance.SYNTHETIC`
+ 证据标签，刻意不改 units/metric_status（申报合成是纯降级声明，不得夹带米制提升）。
以 `--synthetic` 重导入后 Studio 实机确认：`synthetic/geometry: true / preview-proxy`、
黄色 SYNTHETIC ARTIFACT 徽章点亮、viewer 按其代理策略降级渲染保真——
codex 的 Studio 对合成产物的一等 UI 处理首次被导入路径真实触发，工作正常。

## 边界声明

本实验只证明「**当前无纹理渲染** + CPU SIFT + 穷举匹配」注册失败及其量化原因；
不预言贴图后的结果，不评价 canary 本身（canary 的 L2 目标——确定性生成、六层输出、
标定相机——与本实验无关且均已达成）。GT 路线实验全部只读 canary、输出在会话
scratchpad，未写 `.nantai-studio/`、未触碰任何 trust root。
