# 2026-07-19 · H2 近景网格与植被验收

> 执行：Codex（Viewer / Studio UX lane）
> 规格：`docs/superpowers/specs/2026-07-19-high-fidelity-near-mesh-and-foliage-design.md`
> 计划：`docs/superpowers/plans/2026-07-19-high-fidelity-near-mesh-and-foliage.md`

## 结论

H2 候选通过本机浏览器验收，可作为当前本地无限网格世界的近景素材身份：

- v2 mesh bundle：
  `866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6`
- source v1 bundle：
  `2fbf8692ca8b1442c72177dc1954fb81959933bafd46623c1817002fc732c3e8`
- material bundle：
  `b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13`
- foliage atlas set：
  `a8457b85893887f283746fc8701eec0ec50236598bf793049ae4734a16b0aad1`
- 运行时 primitive 压缩提交：
  `1f21f9a7d984f8a7e8dda0667455d0a8bbbcc9e6`

本结论只表示「可信的合成 PBR 近景预览」。它不改变下列机器证据：

- `synthetic=true`
- `geometry_usability=preview-only`
- `real_photo_textures=false`
- 非真实重建、非源照片纹理一致性、不可据此提升米制信任

## 素材闭包

v2 manifest 由 11 个资产、每资产 3 个 LOD 组成，共 33 个 GLB：

| 层级 | GLB | 字节 | 三角形 | 导出 primitive |
|---|---:|---:|---:|---:|
| LOD0 | 11 | 123,648,168 | 280 | 24 |
| LOD1 | 11 | 123,704,632 | 752 | 29 |
| LOD2 | 11 | 22,743,452 | 81,796 | 13,459 |
| 合计 | 33 | 270,096,252 | 82,828 | 13,512 |

纹理闭包包含 45 个内容寻址 PNG，共 78,238,132 字节。运行时在下载完成后校验最终
URL、MIME、字节数和 SHA-256；GLB 只能引用声明过的纹理闭包。LOD0/LOD1 的 GLB SHA
逐资产与 source v1 完全一致，H2 只替换 LOD2。

### LOD2 逐资产证据

| Asset | 三角形 | primitive | Material slots |
|---|---:|---:|---|
| `fence_wood_01` | 1,192 | 44 | weathered timber |
| `house_barn_01` | 9,252 | 723 | dark timber, gray roof, weathered timber |
| `house_stone_01` | 10,276 | 723 | dark timber, fieldstone, gray roof |
| `house_thatch_01` | 9,252 | 723 | dark timber, rammed earth, woven bamboo |
| `house_wood_01` | 9,252 | 723 | gray roof, weathered timber |
| `house_wood_02` | 9,252 | 723 | dark timber, gray roof, pale plaster |
| `stone_lamp_01` | 1,632 | 60 | aged metal, fieldstone |
| `stone_wall_01` | 3,192 | 114 | dry stone wall |
| `tree_bamboo_01` | 9,648 | 3,204 | bamboo leaf, bamboo stem |
| `tree_broadleaf_01` | 8,944 | 3,181 | broadleaf bark, broadleaf canopy |
| `tree_pine_01` | 9,904 | 3,241 | orchard bark, orchard leaf |

每份 LOD2 的 AABB、三角形、primitive、material slot、GLB SHA/字节以及纹理依赖都在
内容寻址 bundle manifest 中；Studio server 每次加载均重新验证，不能靠文件名提升信任。

## 植被遮罩

foliage atlas 是 1024×1024、4×4 cell、4× supersample、8 px RGB dilation 的确定性
合成 atlas；alpha cutoff 为 0.45：

| Slot | Shape | 实测 alpha coverage | 声明范围 |
|---|---|---:|---:|
| bamboo leaf | lanceolate | 0.250242 | 0.20–0.36 |
| broadleaf canopy | ovate-serrated | 0.309781 | 0.28–0.52 |
| orchard leaf | elliptic | 0.377100 | 0.24–0.46 |

三份都在声明范围内。Viewer 保持 `MASK`、double-sided 和 alpha cutoff，并在天气材质 clone
中保留同一 base/normal/ORM 贴图身份。

## Blender 接触表

固定 4K 正交相机、相同灯光和相同材质 lineage 下，v1/v2 的 11 个资产全部可见。两次真实
Blender 渲染的 PNG 与 JSON 逐字节一致：

- PNG：`.nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-evidence/contact-sheet.png`
  - 3840×2160，4,152,014 bytes
  - SHA-256 `ff0ff63eff65bfd1e3efaab8f0f6fd7688f9559ba907903c33529488a29dfc92`
- report：`.nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-evidence/contact-sheet.json`
  - SHA-256 `dd710b9100bc346a6b4dbb6a16da2940292f3f516dd75b8bc762dcfc3da31531`

目视确认 v2 有屋檐/开口/瓦片层次、石墙砌块、围栏结构和真实叶片轮廓；未发现明显矩形
alpha card、黑边、主要部件漂浮、拉伸或取景裁切。此项是视觉 review，不是 provenance
提升。

## 同机浏览器 A/B

环境：同一个 in-app browser tab、同一 viewport/DPR、同一相机姿态、同一 Studio server。
frame sampler 预热 10 秒后采样 60 秒。

| 指标 | H1 / v1 | H2 优化前 | H2 最终 |
|---|---:|---:|---:|
| median frame interval | 16.7 ms | 29.2 ms | 16.7 ms |
| p95 frame interval | 18.7 ms | 31.5 ms | 18.6 ms |
| WebGL geometries | 77 | 8,780 | 67 |
| WebGL textures | 135 | 123 | 123 |
| active / pending / failed | 9 / 0 / 0 | 9 / 0 / 0 | 9 / 0 / 0 |

第一次 v2 运行虽然满足绝对 `median<=33.3ms` 和 `p95<=50ms`，但相对 v1 回退约 75%，
被规格门禁拒绝。根因是 LOD2 的 13,459 个 Blender primitive 原样进入 WebGL。提交
`1f21f9a` 在**字节与纹理闭包验证之后**把静态 primitive 按材质、应用 world matrix 后合并；
skinned / instanced / morph / multi-material mesh 一律 fail closed。最终：

- geometries：8,780 → 67
- median：29.2 ms → 16.7 ms
- p95：31.5 ms → 18.6 ms
- 相对 v1 median 回退：0%，低于 30% 门槛

H2 最终在 60 秒与 62 秒的资源计数逐项一致：

- byte objects / network fetches：59 / 59
- decoded bitmaps / bitmap decodes：39 / 39
- GPU textures / creations：39 / 39
- templates：20
- active / pending / failed：9 / 0 / 0

在 `(123456,-98765,12)` 与 `(-123456,98765,12)` 两处都加载 9/9 分块、0 pending、0 failed；
返回近景后正常。`clear → rain → overcast → fog → night → snow → clear` 全部可逆，最终
回到 clear。应用自身捕获 0 runtime error；本轮 Viewer URL 的控制台 warning/error 为 0。
浏览器控制层在一次 reload 同时产生了一个无 URL 的 MutationObserver 错误，已原样留在
证据 JSON 的 `automation_console_artifacts`，未伪装成应用错误。

私有证据：

- `v1-browser.json`：
  SHA-256 `8ab93f8bf0de6a168f8a18916ad872e574492deaeae08996196f6c4fc87f06c2`
- `v2-browser.json`：
  SHA-256 `c00455d45332a678c8c3bd03c2c7e46c58b90b30d3c333ca1d8ed3788095a398`
- `v2-detail-clear.png`：
  SHA-256 `27ee231c04ff7947a419718d8bce04328a8f4540e70721e3fc51a71781140554`
- 同目录另有 clear/rain/night 近景截图及优化前失败回执。

## 激活与可移植性

本地生成的 `web/data/manifest.json` 已选择 exact v2 bundle，普通 Viewer 默认选择
`presentation=mesh`。tracked `web/viewer/mesh-world.test.mjs` 同时 pin exact v2 identity
并锁定默认 mesh 选择。

但 `web/data/manifest.json` 和 `.nantai-studio/` payload 按仓库约定被 gitignore；因此 git
提交只能携带代码与 exact identity 契约，**不能把约 348 MB 私有 bundle 当作已发布 release
payload**。新机器要复现当前效果，仍须从 release/素材存储下载相同 SHA payload，或按锁定
build 重新生成后验证同一 bundle id。缺 payload 时 server 会 fail closed 返回
`mesh_asset_bundle_invalid`，不会静默回退成“已激活”。

## 离真实贴图还有多远

当前是 1K、合成派生的 PBR（base color + normal + ORM），不是拍摄/扫描贴图。主观视觉距离
只能作为产品判断，不能冒充测量证据：

- 行人距离的可信合成场景：约 65–75%
- 近距离照片级材质：约 45–55%
- 与用户源图逐像素/逐材质一致：尚未进入该链路

主要差距是 4K 以上真实采样材质、唯一污渍/边缘磨损、UV seam 与 decal、地表和建筑接缝
blend、室内、真实捕获几何以及 held-out 照片对照。H3 应从真实授权照片/扫描或真实重建输入
开始；继续在合成纹理上堆锐化不能诚实地跨过这条边界。

## 新鲜门禁

```text
NANTAI_RUN_REAL_MESH_COMPARISON=1 pytest tests/test_mesh_asset_comparison_runtime.py
3 passed in 623.17s

pytest tests -q
1412 passed, 128 skipped, 1 warning in 1375.12s

node --test web/viewer/*.test.mjs
169 passed, 0 failed
```

Python 全量门禁在 Viewer primitive 优化之前运行；该优化只修改浏览器 JS，随后完整 Viewer
门禁与真实浏览器 A/B 均重新运行。最终提交前另运行 ruff、compileall、diff-check。
