# Viewer embedded GLB texture CSP regression

## 报告人

Codex 在 2026-07-18 用本机内嵌浏览器验收 L0 合成 PBR 预览时发现。

## 复现步骤

1. 用 Studio server 打开带 72 张嵌入 PNG 的 `village-canary.glb`。
2. Viewer 能加载几何，但画面退化为近乎单色材质。
3. 控制台对每张贴图报告 `THREE.GLTFLoader: Couldn't load texture blob:...`。

期望：GLB 内嵌 base-color、normal、ORM 贴图被 Three.js 解码。实际：几何加载成功，
全部 Blob 贴图被拒绝。

## 根因分析

Studio CSP 已允许 `img-src blob:`，但现代 Three.js `GLTFLoader` 使用
`ImageBitmapLoader`，后者通过 `fetch(blob:)` 解码嵌入图像。该请求受 `connect-src`
约束，而原策略的 `connect-src` 未包含 `blob:`，所以加载器只保留材质回退色。

## 修复方案

仅向 `connect-src` 增加 `blob:`。不改变脚本、样式、frame、object 或外部网络权限。
同时保留 `img-src blob:`，兼容 Three.js 的 `TextureLoader` 回退路径。

## 验证方式

- HTTP 契约测试锁定 `connect-src 'self' data: blob:`。
- Studio 路由与 Viewer 全量测试保持通过。
- 同一 GLB 重载后不再产生新的纹理 Blob 错误，村庄总览可辨认道路、屋顶、墙面、
  地表与植被材质差异。
