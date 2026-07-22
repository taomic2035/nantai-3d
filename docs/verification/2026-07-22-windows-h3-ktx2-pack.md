# 2026-07-22 · Windows H3 4K KTX2 实包验证

> 结论：当前 Windows x64 机器已从已验 H3 authored pack 生成并私有发布
> 8 个材质、24 张 4096 px KTX2。所有对象通过双编译字节一致、官方验证、
> 独立二进制审计和解码质量门。
>
> 信任边界：这些纹理仍为 `synthetic=true`、`ai_generated=true`、
> `real_photo_textures=false`、`geometry_usability=preview-only`、
> `metric_alignment=false`、`verification_level=L0`。本结果不证明真实几何、
> 源照片纹理一致性、SfM/3DGS 可训练性或任意坐标视觉完整性。

## 输入与运行时身份

| 字段 | 实测值 |
|---|---|
| Source pack | `be92da7d0c2d1956b7775d3422b914f32e5dc29bb233e394c1f33e346eff26b4` |
| Authored pack | `b27eb142bc23c79c5cdd52bc8215604634f115ab12c2f9240fb98ecfc9af1789` |
| KTX package | `KTX-Software-4.4.2-Windows-x64.exe` |
| Package SHA-256 | `1f323b0fec19794f5e6c0425a61d4b1da396872a10be862d105f4f4b2d2957fe` |
| Authenticode thumbprint | `CA07F94EBD7402F3F563FE5C3DF71DF1B88C1B06` |
| `toktx.exe` SHA-256 | `d6de473e340d1ad643146945c49b223e3ec7bc7a8a48b3f5f7698690f8eca5d5` |
| `ktx.exe` SHA-256 | `9718ed380605db33e18a74621978434cedaf119fa4fe25e142a30cabe02c34ef` |
| `ktx.dll` SHA-256 | `6a5bfec1731cbb41e36a54bba56c67b58a78e05acef809fed20190588178d873` |

运行时收据位于私有路径：

```text
.nantai-studio/tools/ktx-4.4.2-windows-x64/receipt.json
```

## 实包身份与闭包

| 字段 | 实测值 |
|---|---|
| Pack ID | `b1c71d1b643d1ce366b2764b7e7beda406908e930aa7107ab583284dfd57ae99` |
| Manifest SHA-256 | `c80da5fe542a9dc4cafbcc5ffd3b60e862482960338b7a32ccb4eb79c22101a1` |
| Manifest bytes | `37,503` |
| Material records | `8` |
| KTX2 objects | `24` unique |
| Pack files | `25`（manifest + 24 objects） |
| Pack bytes | `247,032,038` |
| Texture payload bytes | `246,994,535` |
| Mip closure | 每对象 `4096 → 1`，共 `13` 层 |
| Codecs | `16 UASTC`（base/normal）+ `8 ETC1S`（ORM） |

私有包路径：

```text
.nantai-studio/h3/ktx2/b1c71d1b643d1ce366b2764b7e7beda406908e930aa7107ab583284dfd57ae99/
```

最终目录闭包只有 `manifest.json`、`objects/` 和 24 个 manifest 声明对象；
24 条续跑缓存位于同级 `.texture-cache/`，未进入最终包。

### 逐角色字节与质量范围

| Role | 对象 | 字节 | 实测质量范围 |
|---|---:|---:|---|
| base color | 8 | `142,589,855` | SSIM `0.99590785–0.99725544` |
| normal | 8 | `96,037,215` | mean cosine `0.99997913–0.99999311`; p01 `0.99987710–0.99996394` |
| ORM | 8 | `8,367,465` | max channel error `0.03921569–0.04705882`（最高 `12/255`） |

## 真实故障与修复

1. 首轮真实 validator 拒绝 normal/ORM 的 BT.709 + linear DFD 组合。
   `23ea96f` 为 base color 显式写 sRGB primaries，为 normal/ORM 写 unspecified
   primaries；真实 4K normal probe 随后通过。
2. 原编译器只使用整包临时目录，中断会丢失数十分钟产物。
   `86970d3` 增加由 source/role/options/package/tool SHA 共同寻址的私有缓存；
   复用时仍重新执行结构、官方 validator、解码质量、字节和 SHA 验证。
3. 独立解析器错误要求所有 mip 的 `uncompressedByteLength > 0`。
   Khronos KTX 2.0 规范要求 BasisLZ 的该字段必须为 `0`；真实 ETC1S 文件经
   官方 validator 证明合法。`4d4c48b` 修正此规则，并只允许官方解码侧的
   无透明 palette PNG 进入 RGB 质量比较。规范：
   https://registry.khronos.org/KTX/specs/2.0/ktxspec.v2.html

## 运行命令与新鲜门禁

```powershell
python scripts/synthetic_village.py build-h3-ktx2 `
  --authored-root .nantai-studio/h3/authored/b27eb142bc23c79c5cdd52bc8215604634f115ab12c2f9240fb98ecfc9af1789 `
  --tool-receipt .nantai-studio/tools/ktx-4.4.2-windows-x64/receipt.json `
  --output-root .nantai-studio/h3/ktx2
```

- 最终续跑耗时：`6,398.3s`；前两条对象来自已完全复验的持久缓存。
- 相同命令幂等重跑：`9.094s`，返回同一 pack ID。
- 对最终 24 个对象重新执行官方
  `ktx validate --warnings-as-errors --gltf-basisu`：`24/24` valid，零 message。
- `pytest tests/test_ktx2_toolchain.py -q`：`47 passed`。
- `pytest tests/test_material_bundle_v2.py tests/test_mesh_asset_bundle_v3.py -q`：
  `9 passed`。
- Ruff、compileall、diff-check：通过。

## 下游阻塞与下一门

H3 KTX2 本身已就绪，但 MaterialBundle v2 / MeshAssetBundle v3 的 approved
fallback 仍严格绑定以下 H2 私有 payload：

- material bundle：
  `b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13`
- mesh bundle：
  `866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6`

当前 Windows 私有存储与 GitHub Release 均没有这两个 exact payload。现机从
同一 68-source manifest 重建得到的 material bundle 是 `88e35afe...`；其 72 个
派生对象与验收时代码隔离重建逐项相同，但 manifest 的工具身份不同，不能冒充
`b5f49...`。在恢复 exact H2 payload 或经单独 review 批准新的 fallback identity 前，
不得改 pin、不得组合 v2/v3 bundle，也不得把 H3 设为默认 profile。

恢复后下一门依次为：compose MaterialBundle v2 → compose MeshAssetBundle v3 →
Blender H2/H3 同视角接触表 → Viewer H3 加载与 H2 一字段回滚 → 私有 release。
公开 GitHub Release 仍需单独明确授权。
