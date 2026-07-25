# Synthetic 素材 Releases

本页是可替换 synthetic 设计输入的统一发布入口。浏览或下载所有版本：

- [GitHub Releases](https://github.com/taomic2035/nantai-3d/releases)
- [基础 68 槽视觉包](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-canary-2026-07-16)
- [最新 Batch 33 材质源图](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch33-2026-07-25)

## 内容概览

| 发布范围 | 数量 | 主要用途 |
|---|---:|---|
| 基础视觉包 | 68 槽 | 山村角色与方向参考 |
| Batch 8–14 | 42 张 | 互逆路线、遮挡、垂直与跨分块结构 |
| Batch 20–25 | 68 张 | 角色拓扑、相机包络、环境支撑与局部 360° 建模 |
| Batch 26–33 | 62 张 | 近场转向、LOD、入口/室内、闭环路线与材质源图 |

设计与材质批次共 172 张最终输入。发布 tag 遵循：

```text
synthetic-village-design-inputs-batch<编号>-<日期>
```

## 下载与校验

通过网页下载，或使用 GitHub CLI：

```powershell
$tag = "synthetic-village-design-inputs-batch33-2026-07-25"
gh release download $tag --repo taomic2035/nantai-3d
```

先校验 Release 附带的 `*.SHA256SUMS.txt`，解压后再按
`PAYLOAD-SHA256SUMS.txt` 校验包内文件。基础视觉包安装到：

```text
.nantai-studio/synthetic-village/hybrid-v3/
```

设计板和材质源图是可替换输入，不会因下载而自动进入 registry 或 production build。

## 使用边界

所有素材均为 synthetic authoring reference：

```text
real_photo_texture=false
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

它们可指导 Blender 几何、材质、路线和相机设计，但不能证明真实纹理、SfM/3DGS
训练质量、米制尺度、360° coverage 或任意坐标可达性。材质源图还需经过现有 H3
authoring/打包/实渲链路，不能直接当作已验证 PBR 贴图。

Release 只保留最终图片、prompt 或提示链、`manifest.json`、`USAGE.md` 和 checksum；
候选图、失败请求、队列、contact sheet 与生成缓存不发布。逐批 SHA、机器报告和历史审计
保留在各 Release 附件及仓库 `handoff/` 中，不在本索引重复。
