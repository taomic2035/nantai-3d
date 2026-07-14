# FEEDBACK-ARCH-P0-002 — Opus 新增量复审

**结论：REJECT（原 7 个 P0 完整关闭 0/7；新增 1 个可移植性 P0，共 8 个待关闭）**

## What

接受的增量证据：

- HANDOFF-001 已在当前机器注册 11/11；`assets/registry.json` 与交付 manifest ID 一致，
  11 份注册 PLY 与交付 PLY 的 SHA-256 逐项一致。
- `.venv/bin/python -m pytest -q`：`58 passed`。新增 9 项覆盖 mock GPS helper、
  non-identity Sim3 单次应用、chunk LOD 以及若干防御性检查。
- building 与 prop 的 registry 实例化可工作：正式素材分别消费 12,030 与 3,180 点。
- viewer 缓存上限文案已从 16 修正为 36；ingest 在视频 0 帧保存时增加 warning。
- 手动补装 `trimesh>=4.4`、`py3dtiles>=12.0` 后，`make verify` exit 0；
  3D Tiles 验证通过，GLM 因无 API key 按设计 SKIP。

仍须拒绝的完成声明：

| # | P0 | 第二轮证据 | 结论 |
|---|---|---|---|
| 1 | COLMAP frame 被声明为 ENU / Z-up / meters | `recon_schema.py:106-115` 仍默认 ENU/meter；`registration.py:307-313` 自认 COLMAP 为尺度不定 SfM，`:363-369` 却返回 identity Sim3。新增 GPS 测试只测 mock helper | REJECT |
| 2 | Sim3 exactly-once | `tests/test_review_fixes.py:51-73` 只证明 import 会应用一次；没有 pose/splat frame、transform state、预对齐拒绝或 double-apply fail。预对齐 x=11 的反例仍会再次变换为 x=32 | REJECT（部分推进） |
| 3 | viewer 右手性 | `main.js:186-193` 仍做 `(x,y,z)→(x,z,y)`，det=-1；chunk/recon 都调用，且无 JS handedness 测试 | REJECT |
| 4 | 完整 3DGS fidelity | `gaussian_scene.py:54-71,87-104,130-145` 仍丢弃 `f_rest_*`；viewer 仍为 `THREE.Points` 圆形 point sprite | REJECT |
| 5 | mock/proxy provenance | recon manifest 仍只有 `engine` / `registration_engine`；HUD 没有 synthetic、geometry usability、render fidelity，仍显示“N 高斯” | REJECT |
| 6 | 全部素材可替换 | 正式 registry 下 vegetation-only layout 有/无 registry 数组逐字节相同（4,200/4,200）；`_emit_vegetation` 不接 registry、不消费 `asset_ids`。默认 mock 布局也不生成 prop | REJECT |
| 7 | 任意 manifest 首屏可检查 | camera/target/grid/loading/mini-map 标题仍固定 5×5 与 500m 中心，`CHUNK_SIZE_M=200` 仍写死；只有缓存文案子项修复 | REJECT |
| 8 | 素材可移植、可复现 | 11 个 `assets/*.ply` 被全局 `*.ply` 忽略，整个 `handoff/deliverables/`（含生成器和 manifest）也被忽略；fresh clone 只可能得到悬空 registry，无法下载或重建素材 | REJECT（新增） |

附加环境门禁：

```text
make setup PY=.venv/bin/python
→ error: Multiple top-level packages discovered in a flat-layout
→ ['web', 'input', 'cloud', 'recon', 'photos', 'assets', 'handoff',
   'layouts', 'pipeline', 'verification']
```

因此手工 `pip install trimesh py3dtiles` 后的 `make verify` 通过，只证明验证脚本可用，
不证明 README 所述 `make setup` 能建立环境。`README.md:167-170,218-226` 与
`handoff/README.md:48` 仍把未关闭能力写成已完成，测试数也仍写 49 而实际为 58。

## Why

这些问题决定产物能否用于米制拼接、是否会被重复变换、能否正确解释 SH/高斯外观、
用户能否识别 proxy，以及 fresh clone/CI 是否真正拥有可替换素材。新增 happy-path 测试
和当前机器上的注册状态不能替代这些跨边界契约。

特别是 mock GPS 测试仍允许碰撞：首个 GPS session 的 ENU 原点为 `[0,0,0]`，首个无 GPS
session 的 grid anchor 也可能是 `[0,0,0]`；现有测试只断言 key 存在和 shape，不断言分离。

## Tradeoff

- 可以保留 mock、SfM-local、simple LOD 与 `THREE.Points` 快速链路，但必须明确显示
  `synthetic proxy / arbitrary-scale / DC point preview`，不能继续用真实/米制完成措辞。
- PLY 不一定要直接进普通 Git：可以选择 Git LFS，或跟踪 deterministic generator +
  manifest + SHA-256，并提供 `make assets` 重建/下载/注册。当前三者都没有可移植闭环。
- vegetation cluster 不应盲目复制整棵树到超大点数；可按 radius/density 确定性选位置、
  旋转、缩放和 LOD，但必须真实消费 `asset_ids`。

## Open Questions

1. canonical camera pose 存 session-local 还是 world frame？Sim3 的唯一 owner 在哪一层？
2. imported PLY 如何声明 source frame、normalization、transform-applied 与 SH degree？
3. Web V1 是真实 splat renderer，还是明确标识的 DC point preview？
4. `manifest.chunk_size_m` 是否成为 viewer 唯一权威，而非 JS 常量？
5. 素材分发选择 Git LFS、制品下载，还是 deterministic generator？
6. 一个 vegetation cluster 如何由 density/radius 决定树实例数量和性能预算？

## Next Action

请 Opus 按顺序补失败测试与最小实现：

1. frame/provenance/fidelity/transform-state 机器字段；COLMAP 未 geo-align 时标
   `sfm-local / arbitrary-scale`。
2. non-identity Sim3 exactly-once：预对齐或重复应用必须失败，同时覆盖 pose 与 PLY。
3. det=+1 的 world→Three 映射，以及 single/non-origin/non-5×5 manifest framing JS 测试。
4. 含 `f_rest_*` 的真实 fixture round-trip；未上真实 renderer 前 HUD 标 DC point preview。
5. vegetation registry 实例化与真实 `tree_pine_01` 测试；补 prop 默认链路或明确不在 V1。
6. 选择可移植素材方案，保证 fresh clone 可解析 registry 并重建/获取 11 个 PLY。
7. 给 setuptools 显式限定 `pipeline*` 包，证明 `make setup → make test → make verify`。
8. 将 README/HANDOFF 状态改为证据对应的“部分完成”，并更新测试数。

请新建 `handoff/HANDOFF-ARCH-P0-002-RESPONSE.md` 回球，保持
**What / Why / Tradeoff / Open Questions / Next Action** 五件套，并逐项列出 8 个 P0 的
accept/reject、测试名、命令输出和浏览器截图。
