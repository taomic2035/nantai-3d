# FEEDBACK-HANDOFF-CODEX-009 — Task 5 §3 reciprocal-route production caller

> 日期：2026-07-21  
> 负责人：Codex  
> 状态：单相机 caller 闭环完成；Opus 可启动 mesh/camera/preflight 后续  
> 信任边界：`synthetic=true`、`verification_level=L0`、
> `geometry_trust=simplified-pbr-not-render-parity`、
> `trust_effect=none-quality-filter-only`

## 结论

exact-218 v5 caller 已完成真实 Windows Blender canary：

1. fresh 218-root Blender build；
2. `camera-ground-route-011` fresh 25-ray preflight；
3. RGB / depth / normal / instance / semantic / camera metadata 六层实渲；
4. artifact SHA/size、camera pose、journal、post-render v2 report 逐项复核；
5. 仅在全部门通过后原子发布最终相机目录。

本次 preflight 测得 upper/middle `<2m` 命中 `0/25`，通过；post-render v2
八条规则全部通过。caller 因此已足够作为 Opus §3 mesh/camera/preflight 的接入
基线，但还不是 180-camera 完整交付。

## 接入过程中发现并关闭的真实缺陷

第一次 fresh build `94b57fa1...` 能通过 build report 和 preflight，却在 renderer
开渲前 fail-closed：43 个新增 mesh 没有六层渲染所需的 stable/instance/semantic/
material/pass-index tags。没有帧被发布。

修复提交：`7528982`。构建器现在同时绑定并验证 root/mesh 的完整 render tags。
修复后的 `.blend` 在 Blender 内全量检查得到：

```text
TAG_ERRORS 0
COVERAGE_MISSING 0
```

这说明之前的 `finite_nonempty_module_meshes=true` 只证明 mesh 存在，不足以证明
它们可进入 instance/semantic 六层；该缺口现已在保存 `.blend` 前 fail-closed。

## Fresh build 身份

私有、可重建、未进 Git/Release 的产物目录：

```text
.nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules/
  509919f245932dacd950b7bb95c16638983c4da028ecced5361e3c9da2358a4e/
```

| 身份 | SHA-256 / 值 |
|---|---|
| 175-root environment build ID | `9e4f5215e347e33624f938e1fb19dab31119f20bb82414f37d12bea8f3dfa325` |
| transitive environment build report | `ecb6d979bab7e3c46afe985c775d22bbe1cbf9e21ff5d648b0ff061dd8bdd689` |
| reciprocal build ID | `509919f245932dacd950b7bb95c16638983c4da028ecced5361e3c9da2358a4e` |
| reciprocal runtime request | `dcfb17f9a69858a6fe9ba235ff4b5fba84867eca254060ca71e2db17f7a783bd` |
| reciprocal build report | `635ecdbdf3bf38e11a8f2df2e30ad7e0aeebac569fa7cbfdab7485073c772e78` |
| reciprocal `.blend` | `e6b81c02d271952f4454f1a24a4731726f8e941c963ea92e5dca48ae30676d4c` |
| `.blend` bytes | `150,097,995` |
| Blender executable | `0949e462f677c3e341913a838c6e2f54cc1c811ccb6f281ae9b3ff5926a2b255` |
| reciprocal runtime script | `9a8667ac8924d12373a1f6bc67858ed2aad92eb0ecd35d041af6251d93b9e5f9` |
| reciprocal module plan | `84163656de6a4eed9b3f91f0b9ca4e661912c6e6755d06d8aefdd8d3a01a3847` |
| canonical roots | `218` (`175 + 43`) |

## 单相机 caller 身份

私有最终目录：

```text
.nantai-studio/sv-prod-win/reciprocal-v5-one-camera/
  b1d62574fd9a8c66399091791a67dce32a4bd97040ecc041d8c90c6e5a9ed82b/
  camera-ground-route-011/
```

| 身份 | SHA-256 / ID |
|---|---|
| production plan | `d5db85507a1f7bc4731e03c93d7b1232ddab7272dd5a52fd4d8df7bf6252a9f9` |
| camera registry | `9c8ad9b2bf299d51385822a2b40f071781d0c07e42aae6e1216887adb2563726` |
| exact-218 object registry | `c02c70d73860eac74267da52d9d1e0413c15d02a2e358eba3c1e237b48ca2edc` |
| preflight wrapper | `d58fd0cc23e7024cb079459235c14b6cb2c3f974ffc4c40af67480af3e3d824f` |
| preflight request | `acd2f14294abb12b78402bd619dadebfe34b847d886a744a125d1d7669d0d846` |
| preflight report | `5c95d4f4d3411ad933e79b56cb899d011d040fd706ac00b34a389adfe34cf976` |
| preflight ID | `e46b4f760dc9fbecdbb5e915304266adc92b2adee7e8c15ff801b79b77811e05` |
| clearance policy | `520e72ee9b0b62c8540ecf8866ab2a1c1cf3f6638f4ed86d52de1bcaac0bdf40` |
| renderer wrapper | `5d8aebdbb23306716bc13894f93423d1c747b8a1c07947355afec0f7a39aa565` |
| render request | `fbbec440cbc17220638e69cd5ccd4fa431d7802331364d9856774d237858da90` |
| render ID | `b1d62574fd9a8c66399091791a67dce32a4bd97040ecc041d8c90c6e5a9ed82b` |
| frame report file | `3553a70db56bdeb6b2bb847ce0c4fac7ee02b8fe6ea1b165d281bf345c741b7d` |
| frame report self digest | `fdf9c055ae108ef73db4a71abc20ef92963bab0e76395e22afdebbf4328448dc` |
| journal self digest | `ad1bc03a1fdf4255976d86259f8274b4e85848e608aab71d4e87ce88a4abdeb2` |
| journal file | `c734c77f217ff1a8813c924c7e7cd42425cee0a49194ef31601540d6b40404b5` |
| quality request | `8649f39ace56b7a0980c75ca01c0383785409f874e1caa8d4cbca1ce77b418d2` |
| quality report | `e849d91dede75cf1f5e2e026132f35d1ccce317a7f045c1f5b81bffcd09a2df5` |
| post-render policy | `b60eabd0c9cf069b23982bf2cfb9149ea25add8c6d76df39541d5642cf880b17` |

真实墙钟耗时：preflight `1.983s`；六层 Blender 子进程 `11.787s`。

## 六层 artifact

| kind | bytes | SHA-256 |
|---|---:|---|
| RGB | 732,774 | `dbfd9e2baaa59e1e922e95c8dbf662a9b10ecd090018ed745ed00d3eb0da731d` |
| depth | 1,208,571 | `b3a5f8cd8ac32fe55684f2aa22bbd8dc727a0953f091b3fc89291bd092ffc1f1` |
| normal | 4,136,549 | `ba6c33d54dd9a203ab2dae6c36dc6ef0eb434fd77bcf33189b5aae406e10fc83` |
| instance mask | 6,954 | `2ab1d40029baa7336294c28c20f6b7c0ae2f3b6471ba459812dc1cec759f4e5b` |
| semantic mask | 7,183 | `ae8aefd13dff57ab589f210be49a63d01fb37aaae1bc80a1bd96d106296ad296` |
| camera metadata | 5,452 | `4a7fdc17da1746e14528da58c9587fc49da5b1a725a96380bf2ab990fe4643a9` |

六条 journal artifact 记录均与最终目录实际字节一致。

## Quality 结果

candidate v2 baseline 继续使用 Task 4 实测阈值：valid depth/normal/semantic
最低 `0.30`；sky 最高 `0.55`；upper-ground `0.30`；near-depth `0.35`；
near/upper-instance dominance `0.70`。

| 指标 | 实测 | 门限 | 结果 |
|---|---:|---:|---|
| valid depth | `0.591873` | `>=0.30` | pass |
| valid normal | `0.591873` | `>=0.30` | pass |
| valid semantic | `0.591873` | `>=0.30` | pass |
| sky | `0.408127` | `<=0.55` | pass |
| upper ground | `0.003306` | `<=0.30` | pass |
| near depth | `0.000000` | `<=0.35` | pass |
| near instance dominance | `0.000000` | `<=0.70` | pass |
| upper instance dominance | `0.065660` | `<=0.70` | pass |

旧 valid-pixel 门在本 canary 中只保留 `0.05` 兼容性底线，实测
`0.591873`；主要判定来自上述内容寻址 v2 八规则，不能把 0.05 误写成新的
production baseline。

## 真实限制与目视复核

RGB 能看到道路、挡墙、建筑、植被和可连续阅读的视线；没有近墙遮满画面。
但它仍暴露简化建模：背景山体/屋顶块体重复、地形接缝明显、局部存在黑色矩形
和低细节几何，新 43-part 模块也不能由这一台相机证明全量可见。

This one-camera L0 synthetic render proves caller plumbing only.
It does not prove route topology, real photographic texture, 180-camera coverage,
metric reconstruction, or 360-degree arbitrary-coordinate visual completeness.

## Opus 接入状态

Opus 可从此处继续 §3：

- mesh/topology 检查使用 fresh build ID `509919...`，不要复用旧 `5bb7...` 或
  首次失败的 `94b57...`；
- standing-eye camera / preflight 可复用 v5 exact-218 request/report；
- 六层和 post-render evidence 可复用本 caller 的原子发布与 journal 绑定；
- Task 4 的 `010/039` replacement pose、180-camera 分布和最终 Studio jobs/ledger
  仍是后续独立工作，不应被本单相机 pass 替代。

## 验证命令摘要

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_module_runtime.py `
  tests/test_synthetic_village_reciprocal_route_production_blender.py `
  tests/test_synthetic_village_reciprocal_route_production.py -q

# 真实构建由 run_reciprocal_route_build(base_build=<verified 175-root>) 执行。
# 真实相机由 run_reciprocal_production_camera(... camera-ground-route-011 ...) 执行。
```

