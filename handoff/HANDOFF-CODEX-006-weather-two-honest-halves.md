# HANDOFF-CODEX-006 · 天气切换 —— 诚实的两半，与 3DGS 的物理边界

> 发起：Opus（pipeline / Blender / 3DGS core lane）→ 协调：Codex（viewer / studio lane）
> 日期：2026-07-17
> 触发：用户目标「天气切换」。

## 先说物理边界（这决定了什么能做、什么是谎）

**3DGS 把光照【烘死】在高斯的球谐系数里。** 每个高斯的 SH 编码的是**渲染/拍摄当时**那套
光照下的外观。所以"切天气"在不同路径上代价与诚实度完全不同：

| 路径 | 能做吗 | lane | 诚实边界 |
|---|---|---|---|
| **合成村庄多天气**（改 Blender 场景光照 → 重渲 → 重训一套 3DGS） | ✅ 真能做 | **我（Opus）** | 每种天气一套 3DGS，viewer 切模型。代价 = N ×（渲染+训练） |
| **viewer 深度雾/霾** | ✅ 诚实可做 | **你（Codex）** | viewer 有深度 → 深度雾**物理上讲得通**，不是贴图 |
| **雨雪粒子叠加** | ✅ 可做 | **你（Codex）** | 是**大气叠加层**，不是重光照。标清楚就不是谎 |
| **对一个已训好的 3DGS 改太阳角 / 晴↔阴** | ❌ **做不到** | — | 需要 relighting，而 SH 里烘的是原光照 |

**最后一行是硬边界。** 真实重建**尤其**做不到 —— 你不可能让 COLMAP 把晴天和阴天两批照片
对齐成同一场景。**谁要是拿 `exposure` / `look`（色调映射）改一改叫"天气"，那是拿滤镜冒充
重光照** —— 而且 `RenderSettings` 里这两个字段是 `Literal` 冻结值，架构已经帮我们堵死了这条路。

## 我（Opus）做的这半：合成村庄多天气变体

**落点已勘察清楚**：
- 光照在 **build 侧**（`scripts/blender/build_synthetic_village.py:1821` 定义 sun：
  energy=2.2 / angle=14° / rotation）与 World。
- 改天气 = 改 sun/world = **改 `.blend`** = **新 `blend_sha256`** = 新 build_id。
  **provenance 天然区分两种天气，不用新造机制。**
- 天气变体**绝不走 `RenderSettings`**（那些 `Literal` 是 canary 字节可复现的命脉，一改就废）。

我会做一个**天气 profile**（如 `overcast` / `golden-hour` / `clear-noon`），每个是一套
独立的 build（独立 blend/build_id/输出根），走已有的六层渲染 + 内容寻址 + durable journal。
`synthetic=true` / `simplified-pbr-not-render-parity` 原样保留 —— **换光照不提升 geometry trust**。

## 你（Codex）做的那半：viewer 大气叠加

`web/viewer/environment.mjs` 已存在。深度雾 / 霾 / 雨雪粒子是**你的 lane**，我不碰 `web/`。
唯一请求：**任何大气叠加在 UI 与任何导出的 metadata 里都必须标为「atmospheric overlay,
not relighting」** —— 让用户（和任何下游）知道这是**叠加层**，不是重新照明。深度雾用 viewer
已有的深度是诚实的；把它说成"这个场景真的变阴天了"就不是。

## 交接点（等我这半落地后给你）

- 多天气 build 产物各自独立的 `build_id` / 输出根 —— viewer 切天气 = 切 3DGS 模型源。
- 一份 `weather-variants.json` 清单（每个变体的 profile_id / build_id / 光照参数 / 一句人类可读
  描述），**光照参数如实记录**（太阳高度角、色温、云量），让"这是哪种光照"机器可溯，
  而不是靠变体名自称。

## 我没做 / 不承诺

- **不承诺重训**：3DGS 训练要云 GPU（本机无 CUDA）。我交付的是**多天气渲染输入 + 契约**；
  重训是云侧，耗时先小批次实测再外推，不转述"小时级"。
- **不做 relighting**：见上表最后一行。一个已训好的 3DGS 改太阳角，我不会做，也不会假装能做。
