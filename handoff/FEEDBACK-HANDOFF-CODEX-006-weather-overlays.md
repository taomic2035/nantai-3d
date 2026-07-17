# FEEDBACK · HANDOFF-CODEX-006 天气叠加诚实 UX 与 /goal 浏览器验收

> Codex（Viewer / Studio UX 与审计 lane）→ Opus（pipeline / model-weather lane）
> 日期：2026-07-18
> 对应规格：`handoff/HANDOFF-CODEX-006-weather-two-honest-halves.md`
> 代码提交：`ff8de52`、`d1239cd`

## What

Viewer 侧已把六种视觉天气明确收口为运行时大气叠加，而不是 3DGS 重光照：

- `ENVIRONMENT_EFFECT_IDENTITY` 固定导出
  `effect_kind: atmospheric-overlay`、`effect_source: viewer-runtime`、
  `relighting: false`，并随每次 bridge `getState` 返回。
- HUD、选择器和常驻状态文案同时写明「大气叠加 atmospheric overlay ·
  非重光照 not relighting · 不改变 3DGS 已烘焙光照」；降水粒子降级时也不会丢失
  这条物理边界。
- 晴、阴、雨、雪、雾、夜六态逐一在浏览器切换。HUD/选中值同步，雨线和雪粒分别可见；
  天气切换期间 active chunks 恒为 `36`，没有把视觉切换冒充模型或 chunk 重载。
- Studio 正式 `setCameraPose` bridge 实测：
  `(-1200,-800,75) → chunk(-6,-4)`、`(1400,1000,120) → chunk(7,5)`；
  两处均越过预烘焙范围，25 个按需请求完成后回到「空闲」，LRU 仍受 36 上限约束。
- 自由视角在实际画布完成 9 次 220 px 横向拖拽。按运行时灵敏度
  `0.0032 rad/px` 计为 `6.336 rad / 363.0°`；半周与终点画面均变化。
  切回环绕后继续拖拽，相机由 `(1400,1000,120)` 转到 `(1403,1050,120)`。
- 临时同源验收页调用正式 bridge `getState`，实际返回：

  ```json
  {
    "weather": "clear",
    "zoom": 1,
    "effect_kind": "atmospheric-overlay",
    "effect_source": "viewer-runtime",
    "relighting": false,
    "precipitation_status": "ready"
  }
  ```

  同一份状态仍将 artifact 的 frame / units / handedness / geometry 保持
  `unknown`，没有因天气或漫游提升 provenance。验收页取证后已删除，未留工作树文件。

新鲜门禁：

```text
Python: 1048 passed, 124 skipped, 1 deliberate non-finite warning
Viewer + Studio: 185 passed, 0 failed
Ruff: All checks passed
git diff --check: clean
Studio browser console: 0 warning / 0 error
```

## Why

用户批准的是 HANDOFF-CODEX-006 的诚实双层方案：当前就能交互的 Viewer 天气属于
相机前大气效果；真正改变高斯颜色、阴影和反射的天气属于另一个训练产物。人类可见文案与
机器可读 bridge 现在表达同一事实，避免 Studio、自动化或后续导出把运行时效果误认成
模型级 relighting。

## Tradeoff

- 当前六态能改变背景、雾、运行时光照与雨雪粒子，但不会重算 3DGS 已烘焙辐射度；
  夜景下高斯本体仍可能保留日照外观。这是明确边界，不是隐藏能力。
- 本轮任意坐标证明的是可程序化续渲的合成世界；有限 `spatial-chunks` 真实重建仍不能
  越界生成内容。
- 当前浏览器显示 `full-3dgs` Spark 渲染，但 artifact provenance 仍是
  `unknown / mock / preview-only`，不能当作用户实拍、米制或生产训练质量证据。
- 浏览器自动化运行面不支持原生 `prompt()`，所以未用 Viewer 的 `G` 弹窗取证；
  改用产品正式 Studio `setCameraPose` bridge。Studio 控制台为零错误，这不构成产品
  键盘路径失败。
- 363° 拖拽证明交互和方向连续，不是 Metal/真实 GPU 帧率或长期内存压力报告。

## Open Questions

1. 三份 `clear-noon / overcast / golden-hour` content-addressed profile 何时进入实际 Blender
   render → 云 GPU 训练 → 模型级 3DGS 产物链？目前它们仍只是输入契约。
2. 模型天气 manifest 是否采用独立 `weather-variants.json`，并为每个变体钉住输入 profile
   SHA、训练器版本、训练数据 SHA、坐标帧和 PLY SHA？在这些证据齐全前，Viewer 不应出现
   「模型天气」选择器。
3. 真正模型变体到位后，是否保留当前 overlay 作为可叠加的「即时大气」第二层，还是在
   模型天气模式下限制会造成双重降水/雾的组合？

## Next Action

Opus 可关闭 HANDOFF-CODEX-006 的 Viewer overlay 子项；`ff8de52` 与 `d1239cd` 已满足
人机一致的诚实边界。下一项高价值工作应在有 Blender 渲染输入、云 GPU 训练能力和可内容
寻址产物时，推进模型级天气 variant 契约及训练；在这些前置条件不存在时，不应继续堆叠
会暗示 relighting 的 Viewer 样式。Codex 暂停新增天气 UX，等待真实 variant handoff 后
再做独立模型选择器、差异展示与 fail-closed review。
