# 2026-07-18 · 合成纹理质量 v2 修复回执

> 状态：**当前有限 L0 纹理预览的三项已知缺陷已修复并复验**。
>
> 边界：这是 macOS 本机、非权威、合成来源的预览修复；不证明真实照片纹理、
> 实测几何、任意坐标纹理分块或真实 3DGS/网格混合重建已经完成。

## 根因诊断

| 症状 | 首个错误边界 | 实测证据 |
|---|---|---|
| 大地形出现整齐重复的“万花筒”纹理 | 材质派生 + terrain 绑定 | `mirror-sobel-orm-v1` 产物在水平和垂直方向都逐像素镜像；整个地形又只使用 2.5m 周期的 `material-moss-stone-01` |
| 夯土、土路近景凹凸层次偏弱 | Blender PBR 节点 | `normal_strength` 已烘进 normal PNG，`ShaderNodeNormalMap` 又以同一系数二次衰减 |
| 深木在部分角度接近黑色 | 源图进入场景前 | v1 深木 base-color 亮度 p5/p50/p95 为 `25.4 / 39.7 / 61.3` |
| 雨天切换后纹理过暗 | Viewer 网格天气响应 | 最暗通道的材质响应为 `0.68 × 0.68 = 0.4624`，浏览器同视角可读性不足 |

修复前先加入回归测试并实测得到 `3 failed`；实现后同一组变为 `3 passed`。
雨天可读性测试也先以 `materialResponse >= 0.6` 得到 RED，再修改参数转绿。

## 修复内容

1. 材质算法升级为 `edge-feather-sobel-orm-v2`。它只在 128px 边带平滑相对边缘，
   保留中心原始特征；对边仍逐像素相等，但不再生成水平/垂直精确镜像。
2. 深木只做保色相的阴影 gamma 提升。新产物亮度 p5/p50/p95 为
   `44.4 / 66.5 / 96.7`，仍是深木，不被漂成浅木。
3. Blender normal 节点固定 `scale=1.0`，声明强度只在派生 normal 字节中应用一次。
4. 大地形改为可验证的三材质宏观分区，并只对 terrain 放大 3 倍 UV 周期：
   苔石用于坡面/湿润露头，夯实土和梯田土用于其余地表。导出的 GLB 保留分区
   profile、每类三角形计数和 UV scale extras。
5. 雨天网格响应调整为 exposure `0.78`、key intensity `0.85`、
   base-color multiplier `[0.78, 0.82, 0.87]`；仍比阴天更暗、更冷、更湿，
   但不再压黑材质。
6. `material_algorithm_id` 进入构建请求、构建报告、Blender extras 与独立 GLB
   审计。旧 v1 本地预览报告缺省为 v1 并保持原 canonical 字节，未被新算法静默
   重解释。

## 新产物身份

| 字段 | 实测值 |
|---|---|
| Preview ID | `494eeac81fe6355a87de336cd793fbbc884f835f4ab2067b1f997932448c4297` |
| Material bundle ID | `b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13` |
| Algorithm | `edge-feather-sobel-orm-v2` |
| GLB SHA-256 | `46f822ab2d2ddbfb7c3a8d828dd95ef693258c1239bd99af98810328e416233f` |
| GLB bytes | `132149792` |
| Verification | `L0`, `authoritative=false`, `geometry_usability=preview-only` |

从发布 GLB JSON chunk 重新读取的结构证据：

- 24/24 materials 的 `algorithm_id` 都是 v2；
- 72 张 PBR PNG 全部内嵌，外部 URI 为 0；
- 544/544 primitives 都有纹理、UV 与切线；
- 24 个 normal texture 的有效 scale 全部为 `1.0`；
- terrain 分成三个 primitive：苔石 `4445`、夯实土 `4405`、梯田土 `5150`
  个原始面，`nv_uv_tile_scale=3.0`。

旧预览 `497595a4...` 也在当前代码下重新执行了完整目录和 GLB 审计：
仍识别为 `mirror-sobel-orm-v1`，24 materials、542 primitives，不因升级失效。

## 浏览器复验

当前工作树：

```text
/Users/taomic/vibecoding/nantai-3d
```

实测 URL：

```text
http://127.0.0.1:8767/web/viewer/?modelPreview=%2Fapi%2Flocal-textured-preview%2F494eeac81fe6355a87de336cd793fbbc884f835f4ab2067b1f997932448c4297%2Fmanifest.json
```

私有截图位于 `.nantai-studio/verification/2026-07-18-texture-quality-v2/`：

| 文件 | SHA-256 | 观察 |
|---|---|---|
| `01-overview-clear.png` | `19e7f9b0ad26400a51cc1647f02db17d46b8c6e490aa53ddd9a5377263b9fe1c` | 原来的全场规则石头万花筒已消失，地形成为三种真实 PBR 来源的宏观分区 |
| `02-rain-readable.png` | `5c217c51cbc505e9c034eecad8eca862f263cd030bb4b1eb0380ed3eaa16cada` | 雨粒、冷色和湿润 roughness 保留，路径、田土、苔石与房屋仍可辨 |
| `03-night-readable.png` | `b32c705a29299ff113577e7ece0be8c6182e9fd5da89247e1279c6df494d730f` | 夜景维持低照蓝调，主要材质轮廓和地表纹理没有回归到全黑 |
| `04-close-orbit-clear.png` | `13da41ef2dd4d757ecab85b3b27f9a4212304fde23b4d90a4dde9d0da614a309` | 环绕后木纹、屋瓦、石/土分区仍可读，未见 v1 的四向镜像图案 |

浏览器最终恢复为晴天、合成 GLB 网格模式，并保留为用户可继续操作的标签页。

## 质量门禁

```text
.venv/bin/python -m pytest tests/ -q
1139 passed, 124 skipped, 1 warning in 385.48s

node --test web/viewer/*.test.mjs
130 passed

node --test web/studio/*.test.mjs
75 passed

.venv/bin/python -m ruff check pipeline scripts tests
All checks passed!

.venv/bin/python -m compileall -q pipeline scripts
passed

git diff --check
passed
```

warning 仍来自非有限共享坐标的 fail-closed 对抗测试触发 NumPy overflow，测试本身
通过。本轮没有 `.pen` 设计稿可对照；截图只写入 Git 忽略的私有验证目录，仓库根目录
没有未跟踪媒体文件。

## 仍未满足

1. 地形材质分区边界在远景仍偏规则，模型几何也仍是简化 canary；本回执不把它描述为
   “接近真实最终品质”。
2. 没有新的草地/山体专用照片级 source slot；当前只使用既有 24 槽中的可验证来源。
3. 未在锁定 Windows x64 Blender 上跑 L2 权威 canary，不能替换 tracked release。
4. 任意坐标 textured chunks、素材跨 worker 内容键和真实 3DGS/网格米制混合仍属于下一阶段，
   继续保持方案审批暂停，等待用户先看本次修复。
