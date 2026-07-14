# HANDOFF-001 — 村庄素材库模拟生成 (交办 GPT)

## 背景

nantai-3d 是照片/视频驱动的 3D 村庄世界生成系统, 场景表示采用**高斯泼溅 (3DGS)**。
布局引擎 (`pipeline/mock_layout.py` / GLM-4.6) 通过 `asset_id` 引用素材;
素材注册表 (`pipeline/assets.py`) 支持版本化替换 — 现阶段需要一批**模拟素材**
先把全链路跑起来, 后续用真实重建素材逐个替换 (asset_id 不变)。

**你的任务**: 按下方规格, 用程序化方法 (Python + numpy 即可) 生成 11 个村庄素材的
3DGS 点云 ply, 连同 manifest.json 一起交付。

## 交付物结构

```
handoff/deliverables/HANDOFF-001/
├── manifest.json
├── house_wood_01.ply
├── house_wood_02.ply
├── ...(共 11 个 ply)
└── scripts/          # 可选: 你的生成脚本
```

`manifest.json` 格式 (严格遵守, pydantic 强校验):

```json
{
  "handoff_id": "HANDOFF-001",
  "items": [
    {
      "asset_id": "house_wood_01",
      "kind": "building",
      "ply": "house_wood_01.ply",
      "footprint_m": [8.0, 6.0, 6.5]
    }
  ]
}
```

## 坐标与格式约定 (必须遵守)

| 约定 | 值 |
|---|---|
| 坐标系 | 右手系, **Z 轴向上**, 单位**米** |
| 素材原点 | XY 在素材水平中心, **地面 z=0** (最低点贴 0) |
| ply 编码 | binary_little_endian 1.0, element `vertex` |
| ply 属性 (标准 3DGS) | `x,y,z` (float32); `nx,ny,nz` (float32, 可全 0); `f_dc_0,f_dc_1,f_dc_2` (float32, 颜色 SH DC 系数: `f_dc = (rgb01 - 0.5) / 0.2820947917738781`); `opacity` (float32, **logit 域**: `log(o/(1-o))`, o∈(0,1)); `scale_0,scale_1,scale_2` (float32, **log 域**: `log(米)`); `rot_0..rot_3` (float32, 单位四元数 wxyz) |
| 高斯数量 | 每素材 1,000 – 50,000 (建筑建议 5k–20k, 树 2k–8k, 道具 1k–4k) |
| 高斯尺寸 | 线性域中位数 0.01 – 0.5 m (细节小、填充大) |
| 不透明度 | 平均 ≥ 0.5, 允许边缘羽化用低值 |
| 颜色 | 有真实纹理感的颜色变化 (std ≥ 0.02), 禁止纯色 |

> 备选: 也接受 simple 格式 (`x,y,z` float32 + `r,g,b` uint8 + `scale` float32),
> 但优先 3DGS 标准格式。

## 素材清单 (11 项)

| asset_id | kind | footprint_m [宽,深,高] | 外观要求 |
|---|---|---|---|
| house_wood_01 | building | [8, 6, 6.5] | 木板墙 (暖棕), 红陶瓦双坡屋顶, 深色门洞 + 窗 |
| house_wood_02 | building | [10, 7, 7] | 木墙 + 白灰抹面混合, 灰瓦屋顶, 带前廊柱 |
| house_stone_01 | building | [9, 7, 6.5] | 青灰石砌墙 (石块色差明显), 深灰瓦, 石阶 |
| house_thatch_01 | building | [7, 6, 6] | 土黄夯土墙, 厚茅草屋顶 (干草黄, 蓬松边缘) |
| house_barn_01 | building | [12, 8, 8] | 大跨度谷仓, 深红木板, 黑瓦, 大门洞 |
| tree_pine_01 | vegetation | [4, 4, 9] | 松树: 深绿圆锥形层叠冠, 棕色直干 |
| tree_broadleaf_01 | vegetation | [7, 7, 8] | 阔叶树: 球状浓密冠 (绿色多层次), 分叉干 |
| tree_bamboo_01 | vegetation | [3, 3, 10] | 竹丛: 多根细直青绿秆, 顶部稀疏羽叶 |
| stone_wall_01 | prop | [4, 0.5, 1.2] | 干砌石矮墙段, 灰色石块错缝 |
| stone_lamp_01 | prop | [0.8, 0.8, 2] | 石灯笼: 柱身 + 灯室 + 顶盖, 青灰色 |
| fence_wood_01 | prop | [3, 0.2, 1.1] | 木栅栏段: 两横多竖, 风化木色 |

**风格基调**: 中国东南丘陵村落 (福建南台岛一带), 湿润、绿意浓, 建筑低矮朴素。

## 生成建议 (供参考, 不强制)

- 墙面/屋顶用参数化平面 + 法向少量抖动采样; 屋顶做出坡度和出檐。
- 颜色在基色上叠 5–15% 的 per-gaussian 噪声 + 低频斑块 (风化感)。
- 树冠用多个椭球壳采样叠加; 竹竿用细圆柱线采样。
- 全部素材各自独立 ply, 局部坐标, **不要**共享一个大场景。

## 验收 (自动, 阈值硬性)

我方运行:

```bash
python -m pipeline.validate_handoff handoff/deliverables/HANDOFF-001
```

检查项: manifest schema / ply 可解析 / 数量区间 / 地面 z≈0 (±1m) /
实际尺寸与 footprint_m 偏差 ≤ ±50% / 颜色 std ≥ 0.01 / 尺寸中位数区间 /
平均不透明度 ≥ 0.05。

结果自动写入 `handoff/FEEDBACK-HANDOFF-001.md`; 有 FAIL 请按其整改后整目录重交。
全 PASS 后我方执行 `--register` 导入, 素材即刻在世界渲染中生效。
