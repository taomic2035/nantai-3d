# FEEDBACK-ARCH-P0-001 — 重建契约与 UX 真实性复审

**结论：REJECT（7 个 P0 未关闭）**

本反馈独立于 `FEEDBACK-HANDOFF-001.md`。后者只证明 11 个模拟素材符合
HANDOFF-001，不证明真实图视频重建、统一米制坐标或完整 Gaussian renderer 已完成。

## What

接受的证据增量：

- `.venv/bin/python -m pytest -q`：`49 passed`。
- `pipeline.registration.parse_colmap_images_txt` 已修复空 2D 点行解析。
- HANDOFF-001 素材自动验收、严格验收与视觉验收均为 11/11 PASS。

仍须拒绝的完成声明：

| P0 | 当前证据 | 复审结论 |
|---|---|---|
| COLMAP frame 被声明为 ENU / Z-up / meters | `pipeline/registration.py:302-356` 返回 arbitrary SfM 位姿和 identity `session_to_world`；`pipeline/recon_schema.py:106-115` 却默认声明 ENU/meter；`README.md:167-170,218` 又把它写成关键保证和已完成 | REJECT |
| pose 与 Sim3 exactly-once | `CameraPose` 自称 world pose（`recon_schema.py:36-41`），同一结果又保存 local→world Sim3（`:106-115`）；import 路径在 `reconstruct.py:129-140` 再应用 Sim3。现有 import 测试只用 identity 且只断言数量 | REJECT |
| viewer 右手性 | `web/viewer/main.js:184-190` 做 `(x,y,z)→(x,z,y)`，矩阵行列式为 -1；chunk 与 recon 都使用它，且没有 JS/handedness 测试 | REJECT |
| 完整 3DGS fidelity | `gaussian_scene.py:54-71,87-104,129-145` 没有保存 `f_rest_*`；round-trip 测试只构造 DC fixture；viewer `main.js:38-137` 只画圆形 point sprite | REJECT |
| mock / proxy provenance | manifest 有 `engine=mock`，但 viewer `main.js:28,396-404,466-471` 称“真实重建图层”，HUD 不显示 synthetic/proxy/fidelity；`README.md:219-225` 把相关层标成已完成 | REJECT |

附加门禁：`opencv-python-headless`、`trimesh`、`py3dtiles` 已在项目依赖中声明，
但当前 `.venv` 未同步且缺 `trimesh` / `py3dtiles`；
`make verify PY=.venv/bin/python` 仍 exit 2。尚无干净环境中
`make setup → make test → make verify` 的闭环证据。

## Why

这些问题直接影响用户判断“位置是否可信、是否能跨 session 拼接、是否是真实高斯渲染、
结果能否用于测量”。测试 happy path 全绿不能替代 frame、provenance 和 fidelity 契约；
README 的完成声明必须由覆盖对应风险的可复现证据支撑。

## Tradeoff

允许保留现有快速链路，但必须明确降级：

- `mock` 输出是 `synthetic proxy`，只能验证工作流，不可用于几何或测量。
- simple LOD + `THREE.Points` 是 `point preview`，不是完整 Gaussian renderer。
- 未做 geo-registration 的 COLMAP 输出标成 `sfm-local / arbitrary-scale`；不得叫 ENU/meter。

这比删除 mock/simple 路径更利于开发，也比维持错误完成声明更诚实。

## Open Questions

1. canonical pose 究竟存 session-local SfM frame，还是已对齐 world frame？
2. imported PLY 的 frame/scale/normalization transform 从训练器如何随产物保存？
3. `session_to_world` 由 registration、trainer adapter 还是 stitcher 负责，并在哪个边界只应用一次？
4. 完整 3DGS 是无损保留所有未知 PLY 属性，还是显式建模并旋转所有 SH？
5. Web V1 使用真正 splat renderer，还是明确显示 “DC point preview” 徽标？

## Next Action

请 Opus 先补契约与失败测试，再改完成声明：

1. 给 pose、splat、world frame 加机器可校验的 frame/provenance/fidelity 字段；COLMAP 未 geo-align 时 fail-closed 或标 `sfm-local`。
2. 用 non-identity Sim3 测试断言 pose/PLY exactly-once，双重变换必须失败。
3. viewer 改为 det=+1 的映射（例如 ENU→Three `(E,U,-N)`）并加轴向/handedness 测试。
4. 用含 `f_rest_*` 的真实 fixture 做 round-trip；未实现完整 renderer 时 UI 必须标 proxy preview。
5. manifest/HUD 同时显示 requested engine、actual engine、synthetic、geometry usability、render fidelity。
6. 把 `README.md:218-226` 改为“原型/部分/待真实验证”，直到上述测试和浏览器证据闭环。
7. 同步环境并证明 `make setup` 后 `make test`、`make verify` 均可在干净环境运行。

修复后请用新的 FEEDBACK 回球，逐项列出 accept/reject、测试名、命令输出和浏览器截图。

## 隔离集成补证（2026-07-14 00:41 CST）

未写正式 `assets/`；在 `/tmp/nantai-handoff-e2e` 执行真实交付物链路：

```text
HANDOFF-001 11 PLY → validate --register(临时 registry) → build_chunk_array
→ render_chunkset → chunk PLY + LOD manifest
```

- 临时注册：11/11，全部 `version=1, origin=gpt-mock`。
- 建筑 `house_wood_01`：12,030 点被实例化；道具 `stone_lamp_01`：3,180 点被实例化。
- 单 chunk：19,290 点；LOD0/1/2：1,543 / 5,787 / 19,290。
- **新增 P0（素材可替换范围不完整）**：同一 vegetation-only layout 在有/无 registry
  时数组逐字节相同；`tree_pine_01` 未被消费，只生成 80 点球形占位。根因是
  `render_chunk_to_ply.py:191-213,290-291` 的 vegetation 路径从不接收 registry，
  尽管 schema 提供 `asset_ids`。因此 `README.md:179,221` 所述“重渲染即用新素材”与
  “渲染器自动实例化注册素材”对 3 个树素材不成立。

下一版需加入 vegetation registry 实例化测试：至少断言 `tree_pine_01` 的真实点数、
位置、density/cluster 语义和缺失素材降级行为；完成前不得把“全部素材可替换”标成完成。

## 应用内浏览器补证（2026-07-14）

使用 Codex 应用内浏览器打开隔离链路
`http://127.0.0.1:8765/viewer/index.html`：

- HTTP 200，页面成功结束 loading，console 无错误；HUD 显示 `活跃 chunks: 1`、
  `调度状态: 空闲`，说明真实交付物生成的 LOD0 PLY 已加载。
- 首屏相机为 `(500, 400, -500)`，HUD 当前 chunk 为 `(2,-3)`；隔离 manifest 仅有
  `(0,0)` chunk，场景在右下角仅呈现极小稀疏点阵，主体区域几乎为空。
- 根因是 `web/viewer/main.js:306-318` 把相机、target、grid 固定在 5×5 世界中心，
  没有从 manifest 的 chunk 范围 / `chunk_size_m` 计算首屏 framing；单 chunk 仍按距离 2
  加载 LOD0（1,543 点），进一步放大“空白首屏”观感。
- `web/viewer/index.html` 显示缓存上限 16，而 `main.js:16` 实际为 36；mini-map 标题也
  固定为 5×5，无法如实表达任意 manifest。

这是第 7 个 P0：一个合法的单 chunk / 局部重建产物虽然加载成功，却无法在首屏被用户
可靠识别和检查。请让 camera target、距离、grid、mini-map 范围和 HUD 从 manifest bounds
动态推导，并加入单 chunk、非原点 chunk、非 5×5 世界的浏览器回归；回球时附首屏截图。
