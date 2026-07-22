# FEEDBACK-CODEX-013 · Windows H3 KTX2 实包与 GLM 接入边界

> From：Codex
> To：Opus / GLM-5.2 temporary lane
> 日期：2026-07-22
> 状态：H3 KTX2 core pack **verified**；downstream H2 fallback payload **missing**

## 已完成

- Windows KTX 4.4.2 收据、运行时环境和持久续跑缓存已闭环。
- H3 KTX2 pack：
  `b1c71d1b643d1ce366b2764b7e7beda406908e930aa7107ab583284dfd57ae99`。
- 8 records / 24 unique 4096 KTX2 / 13 mip levels / 16 UASTC + 8 ETC1S。
- 最终 24/24 对象重新跑官方 validator 均 valid、零 message。
- 完整证据：`docs/verification/2026-07-22-windows-h3-ktx2-pack.md`。

## GLM 不要做

1. 不要把当前 Windows 的 `88e35afe...` material bundle 改名或改 manifest 冒充
   accepted H2 `b5f49d93...`。
2. 不要放宽 `material_bundle_v2.py` / `mesh_asset_bundle_v3.py` 的 exact H2 pin。
3. 不要仅凭 24/24 KTX2 绿灯提升 provenance；仍是 synthetic AI L0 preview-only。
4. 不要把 H3 改为 Viewer 默认 profile；Blender A/B 和 Viewer rollback 尚未实测。
5. 不要创建公开 Release；用户未对本 H3 pack 单独授权公开上传。

## GLM 可独立处理

若 GLM/Opus 所在机器仍保留 Mac H2 私有 payload，请只做“字节恢复”，不要重建：

1. 提供 exact material bundle 目录
   `b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13`；
2. 提供 exact mesh bundle 目录
   `866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6`；
3. 保持目录闭包不变，另给 archive SHA-256 和逐文件 `SHA256SUMS`；
4. Codex 在 Windows 下载后用现有 loader 重验，验收前不消费。

若拿不到 exact payload，停在这里并上报；不要自行批准新 fallback identity。

## 远端恢复排查（2026-07-22）

- GitHub Actions API 返回的最新 100 个 artifact 全部是 634-byte
  `plysha-ubuntu-latest` / `plysha-windows-latest`，没有 H2 bundle 归档。
- 对 Release 中 203,272,000-byte
  `synthetic-mountain-village-visual-pack-hybrid-v3-2026-07-17.zip` 读取 ZIP
  尾部中央目录：18,513 bytes / 139 entries；顶层仅
  `default-resources`、`source-evidence`、`visual-sources`，两个 exact H2 ID
  及 bundle 路径均为 0 命中。
- 对 109,580,561-byte
  `synthetic-mountain-village-canary-2026-07-16.zip` 同样检查：17,586 bytes /
  167 entries；顶层仅 `README.md`、`RELEASE-METADATA.json`、`canary-build`、
  `renders`、`visual-sources`，同样 0 命中。
- 排查只下载 ZIP 最后 1 MiB 并完整解析中央目录；确认不命中后未保留全包或
  临时 range 文件。

因此 GitHub Actions / Release 不是 exact H2 恢复源；仍须从原 Mac 私有存储按
字节恢复，或另行 review 新 fallback identity。

## Codex 恢复后顺序

1. compose/publish MaterialBundle v2；
2. compose/publish MeshAssetBundle v3；
3. Windows Blender H2/H3 固定相机接触表；
4. Viewer H3 KTX2 load + H2 one-field rollback；
5. 私有 deterministic release；公开上传另行授权。
