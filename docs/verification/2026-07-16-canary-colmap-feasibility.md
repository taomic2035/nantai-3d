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

## 边界声明

本实验只证明「**当前无纹理渲染** + CPU SIFT + 穷举匹配」注册失败及其量化原因；
不预言贴图后的结果，不评价 canary 本身（canary 的 L2 目标——确定性生成、六层输出、
标定相机——与本实验无关且均已达成）。
