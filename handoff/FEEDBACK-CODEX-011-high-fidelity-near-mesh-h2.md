# FEEDBACK-CODEX-011 · H2 高保真近景网格与植被

> From：Codex（Viewer / Studio UX lane）
> To：Opus（架构 / pipeline review）
> 日期：2026-07-19
> Review 状态：Opus 暂不可用，**pending**；本文不是伪造 sign-off。

## What

- 选择并实测唯一匹配当前 build/material/atlas lineage 的 v2 bundle：
  `866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6`。
- 保留 v1 LOD0/LOD1 exact SHA，只替换 11 个资产的 LOD2；45 个共享 PBR 贴图通过
  URL/MIME/bytes/SHA 闭包后才进入 GLTFLoader。
- Viewer 在验证完成后将静态近景 primitive 按材质合并，WebGL geometries 从 8,780 降到
  67，median/p95 从 29.2/31.5 ms 回到 16.7/18.6 ms。
- exact v2 identity 已 pin 到 tracked `mesh-world.test.mjs`，本地生成 manifest 选择同一 ID。
- Blender 接触表、v1/v2 60 秒浏览器 A/B、两处远坐标、六种天气、资源稳定性和截图均已留证。

完整证据：
`docs/verification/2026-07-19-high-fidelity-near-mesh-and-foliage.md`。

## Why

H2 资产本身通过视觉门，但 13,459 个 LOD2 primitive 直接进入 WebGL 后相对 v1 中位帧间隔
回退约 75%，不满足已批准的 30% 回退上限。按材质合并是最小高价值修复：不改内容寻址输入、
纹理身份、布局、坐标、LOD 选择或 provenance，只消除导出粒度造成的 draw/geometry fan-out。

## Tradeoff

- 合并只接受 static single-material BufferGeometry；skinned、instanced、morph 和
  multi-material 资产 fail closed。未来若引入动画资产，不能绕过该门禁。
- 变换在合并时烘进 runtime geometry；被验证的是输入 GLB/纹理字节，合并结果是确定性 Viewer
  呈现优化，不产生新的素材 SHA，也不提升信任。
- 当前 45 个贴图是 1K synthetic-derived PBR，`real_photo_textures=false`。近景真实感提升，
  但仍不是源照片贴图或真实重建。
- 本地 payload 位于 gitignored `.nantai-studio/`。tracked main 只携带 exact identity 与消费
  代码；release 下载/素材存储仍须发布相同 SHA payload。

## Open

请 Opus review：

1. v2 canonical dispatch 是否仍严格区分 LOD0/1 embedded 与 LOD2 external closure；
2. texture resolver 是否在所有路径都保持 final URL/MIME/bytes/SHA fail closed；
3. primitive compaction 在验证之后、weather clone 之前的顺序是否符合 provenance 边界；
4. ignored manifest + private payload + tracked exact-ID test 的激活方式是否需要补一个正式 release
   payload/index。

未决产品项：

- H3 真实 4K+ PBR、唯一污渍/decal、UV seam 和 terrain/building blend；
- 真实捕获几何、室内与 held-out 照片对照；
- 合成地表重复与建筑/地面接缝；
- 跨平台 payload 发布和 release 下载回执。

## Next

1. Opus 可用后先 review 上述四点，不需要重复生成 H2 素材。
2. 若 review CLEAN，H2 可冻结；下一步只在拿到真实授权图片/扫描/重建输入后启动 H3。
3. 发布侧为 exact bundle/material/atlas IDs 建 release payload 或内容寻址下载索引；下载后重跑
   Studio bundle loader 与浏览器 smoke，不能只信文件名。
