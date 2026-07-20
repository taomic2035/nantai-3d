# FEEDBACK-IMAGE2-012 — Batch 8 反向路线与近景遮挡素材包

> Codex / image2
> 日期：2026-07-20
> Release：`synthetic-village-design-inputs-batch8-2026-07-20`

## 结论

Batch 8 已交付 6 个入选设计角色，覆盖既有素材最缺的反向路线、桥面行走主轴、
水车维护侧、廊下跨层通道、村庄—森林边界以及下谷返村视角。

这批图片来自 reference-conditioned image2 独立生成。内置图片编辑端点发生网络错误后，
使用用户已授权且登录的 ChatGPT image2 页面执行同一套提示词；产物通过认证图片页面以
自然尺寸捕获，因此 PNG 可能相对服务对象发生重编码。`actual_model_id=unknown`。

## 干净 Release

- 页面：
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch8-2026-07-20>
- 归档：
  `synthetic-village-reciprocal-route-design-pack-batch8-2026-07-20.zip`
- 字节：`2,327,861`
- SHA-256：
  `6bdafc92b9eb2df3a943c4e5df3466e9609c22db89844dc940db3dab6ca921eb`
- 内容：6 PNG + 6 精确提示词 + `manifest.json` + `USAGE.md` +
  `PAYLOAD-SHA256SUMS.txt`

使用 7-Zip 关闭文件时间字段后独立构建两次，两个 ZIP 的 SHA-256 逐字节一致；
`7z t` 验证 15 个归档成员全部可读。

## 入选矩阵

| slot | 角色 | 尺寸 | SHA-256 |
|---|---|---:|---|
| `design-route-central-courtyard-reverse-01` | 中央院落下行反向路线 | 1536×1024 | `05a49b4e085d555488e2ff1cc54ef7f643dc99fdbe184c3e09efe295af3c7408` |
| `design-route-bridge-deck-crossing-02` | 桥面第一人称穿越 | 1672×941 | `ba6f3838b5a07b1f18c07e67c61f1ef31ff5862cf79c4c0fa60a248c0105cada` |
| `design-detail-watermill-tailrace-rear-01` | 水车尾水与维护结构 | 1537×1023 | `77feef027408c2087dcb88f0d459eeab51e3a5f52b4af399eb9963ce3214a958` |
| `design-route-covered-gallery-underpass-eye-01` | 廊下跨层通道 | 1448×1086 | `6d124e3269418558f3d5c187b9919d93d8e6e35e7b1ee71dc83591e5a0338b35` |
| `design-boundary-forest-return-eye-01` | 森林/果园边界返村路线 | 1672×941 | `339dbd218c09733d80460580d60b4e4bbd4854d3cde13aa5744a0f2a2aba466c` |
| `design-route-lower-valley-return-eye-01` | 下谷出口上行回望 | 1672×941 | `0641e54144a11d52411e08905a556c698f8e8d19fb78ff2c01cb4c5104ab76a7` |

第一张桥面尝试偏成高位俯视，未满足“相机站在桥面入口”的角色要求。它保留在私有
candidate workspace 并标记为 `supplementary-role-deviation`，没有进入 Release。
第二次只收紧相机位置和桥面主轴后生成 `-02`，视觉复核确认桥面从画面底部贯穿中央，
两侧矮墙、对岸路口、侧向阶梯和溪流关系均可读。

## Fail-closed 证据边界

所有记录统一声明：

```text
camera_calibration = unknown
geometry_consistency = not-verified
training_use = forbidden-as-multiview
coverage_use = forbidden
panorama_projection = unknown
panorama_use = forbidden
trust_effect = none
```

图片之间不能被当作同一场景的已知相机组；宽幅边缘也不是已验证的 equirectangular
全景。它们只能作为可替换 Blender 建模输入，不能证明 360° 覆盖、任意坐标可达、
碰撞安全、米制尺度或 3DGS 训练有效性。

## 下一步消费

1. 将 6 个角色映射到版本化 `EnvironmentModulePlan` / visual source contract；
2. 建模时分别落实桥面、尾水维修面、廊下跨层和两条返村路线，禁止从图片反推相机标定；
3. 在 175-root production contract 上重新跑 collision、walkable topology、
   代表相机 preflight 与六层实渲；
4. 只有实际 Blender/Viewer 证据通过后，才能把这些设计意图提升为可漫游场景能力。
