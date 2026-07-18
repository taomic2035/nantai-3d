# 2026-07-18 · macOS 合成 PBR 本地预览验证回执

> 状态：**L0 本地预览通过，视觉质量有明确欠账**。
>
> 范围：只验证内容寻址的合成材质包、macOS Blender 导出的私有 GLB、
> Studio 私有路由、Viewer 网格呈现与六态天气响应。所有截图仅保存在
> `.nantai-studio/` 私有目录，不进入 Git。

## 结论

本次验证确认 24 个合成材质槽都被实际消费进一个自包含 GLB：542/542
primitive 同时具备纹理、UV 与切线，72 张 PBR PNG 全部内嵌，外部 URI 为 0。
Viewer 在浏览器中完成 SHA-256 校验后能显示模型、执行 orbit/pan/zoom，并明确显示
“本机 L0 · 合成 PBR 纹理 · 非照片 · 非真实重建”。

视觉上，屋瓦、石铺地、土路、灰泥和部分土墙已经可以区分；未见紫色缺图或明显的
硬断裂接缝。仍未达到最终纹理质量：深色木墙在部分角度接近黑色，夯土墙近景证据
不足，远景地表重复铺贴明显。固定夜景最初近乎全黑，已由
`793a203 fix(viewer): keep night textures readable` 修复并复验；修复后仍保持夜间
蓝调，但道路、屋顶与地表纹理可辨。

## 内容身份与信任边界

| 字段 | 实测值 |
|---|---|
| Preview ID | `497595a4ceadb0d4adf3bac1c434c77b1c7b2773442447bfb57d1d20c91e97d2` |
| Material bundle ID | `a151afe9373d6f345b76557d37d38b2889d045ee4c09415f68b1ec2db3be265d` |
| 验证级别 | `L0` |
| 发布通道 | `local-preview-only` |
| authoritative | `false` |
| geometry usability | `preview-only` |
| material fidelity | `synthetic-derived-pbr` |
| real photo textures | `false` |
| dynamic mesh relighting | `true` |
| splat relighting | `false` |

GLB 为 `133692928` bytes，SHA-256：

```text
d0e3bc4fd9976909aeed0535b32efea54da67e408e2dc1158981f08732527b58
```

私有四文件发布集的实测 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `manifest.json` | `21a6d02df43e236ed972f4a8139db9efcf5fea040f4aa58993e9d8a944dd8665` |
| `build-report.json` | `696ccc81d3278e64066e8cdde202de6531b440b9c9164372a9dbba2731e8643b` |
| `glb-material-audit.json` | `6437d59aa877588a48ead75ef24e3db58885d7d54c5a34fe0d9b660a2e7a8f4a` |
| `village-canary.glb` | `d0e3bc4fd9976909aeed0535b32efea54da67e408e2dc1158981f08732527b58` |

## Blender 身份

| 字段 | 实测值 |
|---|---|
| 平台 | `macos-arm64` |
| Blender | `4.5.11` |
| runtime build hash | `4db51e9d1e1e` |
| engine | `BLENDER_EEVEE_NEXT` |
| view transform | `AgX` |
| executable SHA-256 | `8156431a9b9ec1daf49bccea4bd92f327f6efc1ca330d5103881580f3e7773ef` |
| runtime output SHA-256 | `1f84aacb7bf1539d65ab51b4302975046e1c79681411218796f7d68ef5672562` |

这是 Mac 本机身份，不满足锁定的 Windows x64 L2 权威 canary 条件。

## 从发布字节重新执行的结构审计

`verify_local_textured_preview_directory(...)` 重新读取完整 133 MB GLB、构建报告、
审计 JSON 与 manifest，并重新计算材质审计，而不是只相信已保存的计数。

```json
{"authoritative":false,"embedded_image_count":72,"external_uri_count":0,"glb_sha256":"d0e3bc4fd9976909aeed0535b32efea54da67e408e2dc1158981f08732527b58","material_count":24,"preview_id":"497595a4ceadb0d4adf3bac1c434c77b1c7b2773442447bfb57d1d20c91e97d2","publication_file_count":4,"tangent_coverage":1.0,"texture_count":72,"texture_coverage":1.0,"uv_coverage":1.0,"verification_level":"L0"}
```

计划文档中的顶层命令
`scripts/synthetic_village.py audit-textured-glb --preview-id ...` 当前尚未接线，
argparse 会返回 `invalid choice`。本回执没有把该命令写成成功；使用的是同一
fail-closed 审计内核的已发布目录验证入口。这是 CLI 工具链缺口，不是 GLB
结构审计失败。

## 浏览器验证

实测 URL：

```text
http://127.0.0.1:8767/web/viewer/?modelPreview=%2Fapi%2Flocal-textured-preview%2F497595a4ceadb0d4adf3bac1c434c77b1c7b2773442447bfb57d1d20c91e97d2%2Fmanifest.json
```

私有证据根目录：

```text
.nantai-studio/verification/2026-07-18-synthetic-pbr-local-preview/
```

| 截图 | SHA-256 | 观察 |
|---|---|---|
| `01-overview-clear.png` | `146536ef0585e1554263d04a9f94fca7458441fd249a48bcc901798413efcbcc` | 晴天全景、模型已加载、L0 披露可见；远景地表重复铺贴明显 |
| `02-close-roof-timber-clear.png` | `ec1bc11cd2136f7a55b7fab2649dd92a6b8b0272db039db55214e0642b02ea3f` | 屋瓦纹理清楚；深色木墙可读性偏弱 |
| `03-close-earth-stone-clear.png` | `2a6db3c403f8f84120e6f91d99b97fff5952924d8a1cd8098e190dd2a37134ea` | 固定材质视角；石铺地、土路、灰泥及远处土墙可区分 |
| `04-close-earth-stone-rain.png` | `97cec24a1c600bf286b916ef3c3f8d7a4b0d3b69a39a70f725ead821acc6fdf6` | 同一视角雨天；整体变暗、冷色化、雨粒可见，纹理仍可辨 |
| `05-close-earth-stone-night-before-fix.png` | `2b11aebef4810e83667d74f75dd4f14b565264888f1e3f3299ca9ecaa0095852` | 修复前失败证据：夜景近乎全黑 |
| `05-close-earth-stone-night.png` | `f45b56503283c065dcb87b08d049419f1422cf450347616ef8dd5b9e242a13c8` | 修复后同一视角；保持夜色且道路、屋顶、铺地可辨 |
| `06-close-earth-stone-clear-restored.png` | `72665c949b528d18779f914251fe87a49db596b92b0eb917d53a0c3fc0d833c8` | 从夜天切回晴天，材质与灯光恢复 |

晴天恢复截图与首次晴天截图不是像素级相同；切换期间 OrbitControls damping 继续
收敛，导致相机姿态有轻微变化。因此这里只声明材质/灯光视觉恢复，并由自动化测试
锁定每次天气切换前都从原始颜色与 roughness 重置，不声明截图字节相等。

交互过程中多次执行 orbit、pan 与 zoom，Viewer 保持响应且没有丢失模型；这证明
当前人工检查路径可用，但不是一段完整 360° 性能录制。

## 固定检查判定

| 检查项 | 判定 | 证据边界 |
|---|---|---|
| 24 槽材质实际消费 | 通过 | 结构审计：24 materials、72 distinct embedded PBR images |
| 无缺图/外链依赖 | 通过 | 72 embedded PNG、external URI 0；浏览器未见紫色缺图 |
| 全 primitive 有纹理/UV/切线 | 通过 | 542/542，三项 coverage 均为 1.0 |
| 屋瓦、木、土、灰泥、石可区分 | 部分通过 | 屋瓦/石/土路/灰泥明确；深木墙和夯土近景仍不够 |
| 无旋转投影或硬包裹缝 | 检查帧未发现 | 不能外推为对全部 542 primitive 的视觉证明 |
| 雨天响应 | 通过 | 同视角截图 + `roughnessMultiplier < 1` 自动化约束 |
| 夜间可读 | 修复后通过 | 修复前/后私有证据 + 最低材质响应自动化约束 |
| 返回晴天可逆 | 通过 | 浏览器复验 + reset-before-apply 自动化测试 |
| L0 非权威披露 | 通过 | 每张固定截图均可见 |
| 360° 漫游 | 交互可用 | orbit/pan/zoom 实测；未做完整性能轨迹 |

## 门禁

在 `793a203` 代码状态上执行：

```text
.venv/bin/python -m pytest tests/ -q
1136 passed, 124 skipped, 1 warning in 393.42s

node --test web/viewer/*.test.mjs
129 passed

node --test web/studio/*.test.mjs
75 passed

.venv/bin/python -m ruff check pipeline tests
All checks passed!

.venv/bin/python -m compileall -q pipeline scripts
passed

git diff --check
passed
```

唯一 warning 来自非有限共享坐标的 fail-closed 对抗测试触发 NumPy overflow；
测试本身通过。未提交的 `tests/test_synthetic_village_weather.py` 属其他协作者 WIP，
本回执不提交该文件。

Studio 跨 API `ctime` 误报修复 `a3192c1` 的 GitHub Actions 也已完成：
Ubuntu/Windows × Python 3.11/3.13、两端素材复现及跨系统 hash compare 共七个作业
全部成功。运行记录：
<https://github.com/taomic2035/nantai-3d/actions/runs/29639834655>

## 尚未证明 / 下一道真实门槛

1. 尚未在锁定的 Windows x64 Blender 上运行权威 L2 textured canary，不能替换 tracked release。
2. 这些纹理是合成派生 PBR，不是实拍照片纹理，也不是从真实重建反投影得到的纹理。
3. 深色木墙、夯土近景可读性和大面积重复铺贴仍需后续素材/UV 质量迭代。
4. 天气只对合成网格做动态重光照；3DGS 仍仅叠加大气，绝不声称 splat relighting。
5. 尚未完成任意坐标 textured chunks 与真实 3DGS/网格混合重建呈现。

> This receipt proves a synthetic macOS L0 textured preview only. It does not
> authorize tracked release replacement, measured geometry, real photo textures,
> 3DGS relighting, or arbitrary-coordinate textured chunk completion.
