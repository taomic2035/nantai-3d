# Nantai 3D 全项目接管实施计划

> **执行要求：** 逐任务遵守 Red → Green → Refactor；每次只改让当前失败测试转绿所需的最小范围。

**目标：** 在保留 Opus 现有 WIP 的前提下，把“图像+视频输入、统一且可证明的 3D
坐标、图视频混合重建、拼接与区域增清、真实 Gaussian Splat 展示、可替换素材、Studio UX”
从可演示原型推进到诚实、可测试、可复现的一条本地链路。

**工作区：** `/Users/taomic/vibecoding/nantai-3d-codex-takeover`
**分支：** `codex/nantai-takeover`
**基线：** Python 3.13 editable install 成功；2026-07-14 基线 `58 passed`。原始
`/Users/taomic/vibecoding/nantai-3d` 保持未修改，继续作为 Opus 现场。

**架构原则：** 重建 artifact 不因 engine 名字被推断为“真实/米制/完整”。坐标、变换链、
合成状态、属性保真、渲染能力与素材消费都有机器字段；不确定时允许预览但 fail closed。
Python 负责 artifact 与本地任务编排，Viewer 负责可验证的渲染 capability，Studio 只消费
版本化 JSON 与 viewer bridge。

**技术栈：** Python 3.11+、Pydantic v2、NumPy、plyfile、pytest、原生 ES modules、
Node `node:test`、Three.js。真实 3DGS 层采用固定版本 Spark（Three.js/WebGL2）并保留
明确的 DC point fallback。

## 执行结果（2026-07-14）

| Task | 状态 | 证据 |
|---|---|---|
| 1. 基线与文档 | ✅ | takeover/README/capability matrix |
| 2. 坐标与 provenance | ✅ | 完整 frame/Sim3/SplatInput、COLMAP intrinsics/coverage、fail closed tests |
| 3. 3DGS 保真 | ✅ | degree-3 SH round-trip、metadata/history、merge/LOD/replace tests |
| 4. 素材链 | ✅ | HANDOFF-001 11/11、SHA/CAS、building/vegetation/prop 全消费 |
| 5. Viewer | ✅ | 右手映射、动态 framing、Spark 2.1.0、bridge/fallback、浏览器验收 |
| 6. Studio | ✅ | reducer/adapter/ledger/bridge/local server、11/11 素材 UX |
| 7. 总门禁 | ✅ | `docs/verification/2026-07-14-takeover-report.md` |

范围边界：GPU trainer、真实控制点对齐、distortion-aware 投影、离线 renderer bundle 与
Studio 写任务仍是后续项；它们没有被 synthetic/proxy 结果冒充完成。

---

## Task 1：冻结事实基线与文档边界

**Files**

- Modify: `README.md`
- Modify: `handoff/README.md`
- Create: `handoff/TAKEOVER-2026-07-14.md`
- Verify: `docs/superpowers/specs/2026-07-14-nantai-3d-studio-ux-design.md`

**步骤**

1. 在 takeover 记录中写入基线 commit、原/隔离 worktree、58 tests、8 个 P0 与所有权。
2. 把 README 的“已完成”改成 capability matrix：`verified / proxy / blocked / planned`。
3. 记录 Spark 选择依据和固定版本；明确网络/renderer 不可用时只能标 DC point preview。
4. 运行 placeholder 扫描：

   ```bash
   rg -n "TBD|TODO|待定|全部完成|真实重建|高斯泼溅" README.md docs handoff
   ```

5. 只修正文档事实，不在此任务改算法。

## Task 2：统一坐标、配准 provenance 与 Sim3 exactly-once

**Files**

- Create: `tests/test_coordinate_contract.py`
- Modify: `tests/test_registration.py`
- Modify: `tests/test_review_fixes.py`
- Modify: `pipeline/recon_schema.py`
- Modify: `pipeline/registration.py`
- Modify: `pipeline/reconstruct.py`

**Red**

1. 写 `CoordinateFrame` 测试：右手、axes、units、metric status、geo alignment 均是枚举；
   NaN/Inf/零四元数与非正交、负行列式旋转被拒绝。
2. 写 COLMAP 契约测试：裸结果必须是 `sfm-local / arbitrary / unaligned`；单个 GPS origin
   不得把它升级成 ENU 米制。
3. 写 mock 契约测试：无 GPS 是 synthetic metric local frame；有 GPS 可为 synthetic ENU，
   但 provenance 仍为 synthetic。
4. 写 `FrameTransform` 测试：source/target 匹配、稳定 `transform_id`、同 id 二次应用拒绝且
   不改变 scene。
5. 写导入测试：每个 splat 必须带 `SplatInput(frame_id, path, transform?)`；不同 frame
   未对齐不能 merge；裸 COLMAP 不能导出“world-meter” manifest。

**Green**

6. 在 `recon_schema.py` 增加：

   - `CoordinateFrame(frame_id, handedness, axes, units, metric_status, geo_aligned)`
   - `FrameTransform(transform_id, source_frame, target_frame, sim3, method, evidence)`
   - `SplatInput(session_id, path, frame_id, transform)`
   - `RegistrationResult.pose_frame/world_frame/alignment_status/pose_to_world`

7. `registration.py` 让 mock 与 COLMAP 生成不同、准确的 frame contract；删除默认“所有结果
   都是 ENU meters”的自由字符串。
8. `reconstruct.py` 以 SplatInput 为导入边界，合并前统一到同一个 target frame；manifest
   输出 frame、units、alignment、metric evidence 与 transform chain。
9. 保留 schema v1 的只读迁移器，仅把缺字段标 unknown，不猜测米制。

**Verify**

10. 运行：

    ```bash
    .venv/bin/python -m pytest tests/test_coordinate_contract.py tests/test_registration.py tests/test_review_fixes.py -q
    .venv/bin/python -m pytest tests -q
    ```

## Task 3：3DGS 属性保真、帧历史与安全变换

**Files**

- Create: `tests/fixtures/degree3_gaussian.ply`
- Create: `tests/test_gaussian_fidelity.py`
- Modify: `tests/test_gaussian_scene.py`
- Modify: `pipeline/gaussian_scene.py`

**Red**

1. 用 Graphdeco 字段顺序生成 degree-3 fixture：`f_dc_0..2`、`f_rest_0..44`、opacity、
   三轴 log-scale、四元数。
2. 写 roundtrip 测试：`f_dc` 不经 RGB clip 改值，45 个 `f_rest`、normals 与未知标量字段
   bitwise/容差保持。
3. 写 crop/subset/LOD/replace/merge 属性测试；schema 不兼容的 merge 必须报错。
4. 写 PLY metadata 测试：`frame_id`、units、transform ids 经保存/读取不丢失。
5. 写安全旋转测试：高阶 SH 在未实现 SH basis rotation 前 fail closed；平移/统一缩放不改 SH。

**Green**

6. `GaussianScene` 增加 raw `sh_dc`、`sh_rest`、normals、extra properties、frame metadata；
   `rgb` 仅作为显示派生值。
7. `save_ply(flavor="3dgs")` 按原属性顺序写回，并在 PLY comments 写紧凑的 `nantai_meta`。
8. `_subset`、merge、dedup、replace 与 LOD 全部传播属性和 metadata。
9. 增加 `apply_frame_transform(FrameTransform)`；先检查 source frame 与 transform history，
   成功后原子更新 frame/history。保留内部低阶 `transform(Sim3)` 供新加载的素材实例化使用。
10. 旋转 normals 与高斯 quaternion；高阶 SH rotation 未交付前返回明确错误，不静默损坏。

**Verify**

11. 运行：

    ```bash
    .venv/bin/python -m pytest tests/test_gaussian_fidelity.py tests/test_gaussian_scene.py -q
    .venv/bin/python -m pipeline.gaussian_scene
    ```

## Task 4：可移植、幂等、全类型消费的素材链

**Files**

- Create: `tests/test_asset_pipeline.py`
- Modify: `tests/test_assets_and_handoff.py`
- Modify: `pipeline/assets.py`
- Modify: `pipeline/validate_handoff.py`
- Modify: `pipeline/render_chunk_to_ply.py`
- Modify: `handoff/deliverables/HANDOFF-001/manifest.json`
- Modify: `.gitignore`
- Modify: `Makefile`

**Red**

1. 写注册幂等测试：同 asset id + 同 SHA 再注册不加版本；registry 存在但 payload 缺失时
   恢复同版本；不同 SHA 才生成 v+1。
2. 写生成器确定性测试：两次生成的 11 个 SHA 完全一致，manifest 声明的 SHA 与实物一致。
3. 写 vegetation 测试：`asset_ids` 被确定性选用；替换 tree asset 后同 layout 输出改变；
   density/radius 控制实例数，但总点数受预算限制。
4. 写 consumption report 测试：逐 asset 记录 renderer、chunk、实例数与 hash；不能仅以 registry
   存在推断“已消费”。

**Green**

5. `AssetEntry` 增加 sha256、registered_at、active/history 结构；register/replace 采用 temp file
   + atomic rename，支持 expectedVersion 乐观锁。
6. HANDOFF manifest 增加 generator version 与逐 PLY sha256；验证器校验 hash。
7. `_emit_vegetation(..., registry)` 根据稳定 seed 布置实例，优先消费声明 asset ids，缺失时
   才回退 proxy；生成 `asset_consumption` manifest。
8. `.gitignore` 只忽略生成的 PLY，不再忽略 generator/manifest/contact sheet。
9. 增加幂等 `make assets`：generate → validate → register；连续执行两次 registry revision、
   version 与 SHA 不变。

**Verify**

10. 运行：

    ```bash
    .venv/bin/python -m pytest tests/test_asset_pipeline.py tests/test_assets_and_handoff.py -q
    make assets PY=.venv/bin/python
    shasum -a 256 assets/*.ply assets/registry.json
    make assets PY=.venv/bin/python
    shasum -a 256 assets/*.ply assets/registry.json
    ```

## Task 5：Viewer 右手映射、动态 framing 与真实 Splat layer

**Files**

- Create: `web/viewer/coordinates.mjs`
- Create: `web/viewer/coordinates.test.mjs`
- Create: `web/viewer/framing.mjs`
- Create: `web/viewer/framing.test.mjs`
- Create: `web/viewer/bridge.mjs`
- Create: `web/viewer/bridge.test.mjs`
- Create: `web/viewer/splat-layer.js`
- Modify: `web/viewer/main.js`
- Modify: `web/viewer/index.html`

**Red**

1. 测 `worldToThree(E,N,U) = (E,U,-N)` 与逆变换，3×3 determinant `+1`。
2. 测负 chunk、非 200m chunk、单 chunk、非方形 manifest 的 world bounds、center、camera
   distance、grid size 与 near/far。
3. 测 Three camera 坐标反查 ENU chunk、recon LOD 距离和 minimap 北向一致。
4. 测 viewer bridge 握手、request id、capability、unsupported command 与错误响应。

**Green**

5. 抽出纯坐标/framing 模块；删掉 `swapYZ`、500/1000/5x5 硬编码，所有初始化从 manifest
   和 recon bounds 派生。
6. HUD 显示 actual/requested engine、synthetic、frame/units、geometry usability 与 fidelity；
   chunk proxy 只称 point preview。
7. 固定 Spark release 与 Three 兼容版本，`splat-layer.js` 用标准 3DGS PLY 创建真正的
   anisotropic alpha-composited layer；失败时保留 DC point fallback 并显式降级。
8. viewer bridge 实现 `loadArtifact/setLOD/setLayer/resetCamera/getState`，回报实际 renderer
   capabilities；Studio 不能仅凭 URL 宣称 Gaussian splat。

**Verify**

9. 运行：

   ```bash
   node --test web/viewer/*.test.mjs
   make world PY=.venv/bin/python
   make reconstruct PY=.venv/bin/python
   ```

10. 浏览器验证 1280×720：世界层、真实 splat/降级标签、LOD、reset、minimap、console。

## Task 6：Studio state model、模拟运行与本地 adapter

**Files**

- Create: `web/studio/index.html`
- Create: `web/studio/styles.css`
- Create: `web/studio/app.js`
- Create: `web/studio/model.mjs`
- Create: `web/studio/model.test.mjs`
- Create: `web/studio/ledger.mjs`
- Create: `web/studio/ledger.test.mjs`
- Create: `web/studio/mock-adapter.mjs`
- Create: `web/studio/viewer-bridge.mjs`
- Create: `pipeline/studio_server.py`
- Create: `tests/test_studio_server.py`
- Create: `docs/contracts/studio-adapter-v2.schema.json`

**Red**

1. 状态 reducer 测试：未知 provenance、非法五层组合、synthetic/measurable 矛盾均 fail
   closed；每个场景只有一个 primary action。
2. ledger 测试：刷新恢复、上限清理、event id 去重、retry_of、失败不覆盖成功。
3. asset UI 测试：validation 与 consumption 分离；commit token 过期/expected version 冲突
   不改 active registry。
4. server 测试：只允许白名单 job、路径限制在 project root、snapshot schema v2、run ledger
   原子写入、cancel race 最终状态一致、静态文件带正确 MIME 与安全 header。

**Green**

5. 实现三栏工作台、六步 pipeline、Stage iframe、step inspector、底部 job drawer、场景切换。
6. mock adapter 走正式 interface，永久显示“模拟数据”；localStorage 只保存 mock 与 UI 偏好。
7. viewer bridge 未握手时禁用无效按钮，握手后按 capability 解锁。
8. 本地 adapter 使用 Python 标准库 HTTP server 暴露只读 snapshot/runs 与白名单任务；真实
   ledger 为唯一真相源。写操作先完成 ingest/reconstruct/assets，避免伪造成功。
9. 提供 11 个 HANDOFF-001 素材卡、三类消费状态、替换两阶段交互与证据链接。

**Verify**

10. 运行：

    ```bash
    node --test web/studio/*.test.mjs
    .venv/bin/python -m pytest tests/test_studio_server.py -q
    .venv/bin/python -m pipeline.studio_server --host 127.0.0.1 --port 8765
    ```

11. 浏览器验收 1024×768、1099×800、1280×720、1440×900；截图保存到
    `docs/screenshots/studio/`，验证 console、keyboard、drawer focus 与 reload ledger。

## Task 7：端到端能力门禁与 handoff feedback

**Files**

- Modify: `Makefile`
- Modify: `README.md`
- Create: `docs/verification/2026-07-14-takeover-report.md`
- Create: `handoff/FEEDBACK-TAKEOVER-001.md`

**步骤**

1. `make test` 增加 Python + Node tests；`make verify` 覆盖 assets、manifest schema、world、
   mock mixed reconstruction、viewer module tests。
2. 从全新临时目录执行 generate/register/world/reconstruct，证明交付配方可移植。
3. 运行完整门禁：

   ```bash
   make setup PY=/opt/homebrew/bin/python3.13
   make test PY=.venv/bin/python
   make assets PY=.venv/bin/python
   make world PY=.venv/bin/python
   make reconstruct PY=.venv/bin/python
   make verify PY=.venv/bin/python
   .venv/bin/python -m ruff check pipeline tests
   git diff --check
   ```

4. 逐项回填 8 个 P0：代码证据、测试、剩余限制；不把 dependency/network/GPU 缺失误报为
   算法完成。
5. 写给 Opus 的五件套 feedback：What / Why / Tradeoff / Open / Next，并明确可 cherry-pick
   的 commit 边界。
6. 在用户授权前不合并或覆盖 Opus 主工作区；只交付隔离分支与验证证据。

---

## 执行顺序与并行边界

- Task 2 → Task 3 严格串行：frame metadata 是 3DGS exactly-once 的前置。
- Task 4 可在 Task 2 之后与 Task 3 后半并行，但共享 `GaussianScene` 时必须先同步接口。
- Task 5 的纯坐标/framing 可与 Task 3 并行；真实 splat layer 等 Task 3 PLY fidelity。
- Task 6 的 shell/model/mock 可与 Task 4/5 并行；本地 adapter 等 manifest v2 稳定。
- Task 7 只在所有切片局部绿后执行。

## 明确不伪造的边界

- 没有 COLMAP/GPU 训练器时，端到端验收使用 mock/import fixture，并显示 synthetic/proxy。
- 没有三点以上 GPS/control-point Sim3 证据时，COLMAP 只称统一 SfM local frame，不称 ENU 米制。
- 真实 renderer 不可用时只称 DC point preview，不把圆形 sprite 称 Gaussian Splat。
- registry 中存在不等于 renderer 已消费；只有 consumption report 才能显示“已使用”。
- 高阶 SH 旋转若未正确实现，阻断该 transform，不静默保留错误 view-dependent color。
