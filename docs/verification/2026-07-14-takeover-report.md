# Nantai 3D takeover verification — 2026-07-14

## 结论

本地可复现切片通过：图片+视频输入、联合配准、显式坐标、混合 3DGS import/merge、区域增清、
LOD、完整 3DGS 属性、11 个可替换素材、真实 Spark Viewer 与 Studio UX 均有自动测试和运行证据。
当前样例是 `mock-proxy / synthetic / preview-proxy`，没有宣称为实测场景。

## 验证环境

- worktree：`/Users/taomic/vibecoding/nantai-3d-codex-takeover`
- branch：`codex/nantai-takeover`
- Python：3.13.13 editable install
- Browser：Codex 内嵌浏览器，same-origin local adapter
- 当前机器未使用 COLMAP/GPU；registration auto 正确选择 mock

## 自动门禁

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest tests -q` | PASS，164/164；坐标、COLMAP、3DGS、素材、server、端到端覆盖 |
| `node --test web/viewer/*.test.mjs` | PASS，30/30；bridge、右手映射、framing、Spark/fallback |
| `node --test web/studio/*.test.mjs` | PASS，30/30；reducer、adapter、ledger、bridge、DOM capability gate |
| `.venv/bin/python -m ruff check pipeline tests` | PASS |
| `git diff --check` | PASS |
| `make assets` 连跑两次 | PASS 11/11；registry/manifest SHA 不变 |
| `make world` | PASS；25 chunks，3,129,456 points，11/11 unique assets |
| `make reconstruct` | PASS；2 sessions / 11 frames / 7,700 synthetic gaussians / 3 LOD |
| `make verify` | PASS；完整 tests + assets + world + JSON + 3DTiles + GLM schema |

## 关键运行证据

### 图像 + 视频与坐标

- 输入：5 张图片、1 个视频；视频抽帧 6，合计 11 registration poses。
- session：`video_drone_orbit` 与 `photos_batch_0` 进入同一 registration result。
- mock target：`mock-local / right / local-z-up / meters / unaligned / synthetic`。
- manifest：`actual_reconstruction_engine=mock-proxy`、`synthetic=true`、
  `geometry_usability=preview-proxy`，transform chain 为空且与 PLY history/ancestry 一致。
- COLMAP 测试覆盖 SIMPLE_PINHOLE、PINHOLE、SIMPLE_RADIAL、RADIAL、OPENCV、多相机、
  CAMERA_ID、部分注册覆盖率、未知模型和非法参数 fail closed。

### 3DGS fidelity

- 全量 artifact：7,700 gaussians，`full-3dgs`，DC/opacity/scale/quaternion 属性齐全。
- LOD：616 / 2,310 / 7,700，明确为 `dc-point-preview`。
- degree-3 fixture 验证 45 个 `f_rest_*`、normals/extras 与 raw DC round-trip。
- incompatible frame/schema、重复 transform、高阶 SH 旋转、非米制去重/区域替换均 fail closed。

### 素材与 world

- HANDOFF-001：11/11 schema v2、meters/local-z-up、正 footprint、实际 SHA、PLY 数值通过。
- 幂等 SHA：
  - `assets/registry.json`：`c20e12b46821649e6f7bd80098611d721e346a156c7e29769b1fdbe407c22144`
  - `manifest.json`：`7df0182fb34238a39b4896e55740c773c4f22e054c8f9ab4bbae3a432159f33d`
- world：217 consumption records；unique assets = building 5 + vegetation 3 + prop 3 = 11。
- 每条记录包含 renderer、chunk、instances、point_count、version 和实测 payload SHA。

### Fresh-checkout 可移植性

用 `git ls-files --cached --others --exclude-standard` 复制一份只含可入库文件的临时项目：

1. `make assets` 从 generator 恢复 11 个被 Git 忽略的 PLY payload；
2. `make world` 生成 25 chunks/11 类素材消费；
3. 以仓内 contact sheet 运行 mock reconstruct，得到 2,853 gaussians/3 LOD；
4. `test_mock_layout_assets + test_reconstruct`：18 passed；
5. 删除临时目录。

### Browser / UX

- local adapter：schema v2，`registered=11 / consumed=11 / blocked=0`。
- Viewer：25 active chunks；Spark 2.1.0 初始化；7,700 splats；artifact/runtime fidelity 均
  `full-3dgs`；synthetic watermark 与 `mock-proxy / preview-proxy` 同时可见。
- Studio：LOD auto→0 生效；reconstruction layer 可隐藏/恢复；reset control 在 ready 后解锁；
  素材 inspector 显示 11 张卡全部“格式 PASS · 世界已消费”。
- HTTP：GET `/api/project` 200 + no-store/CSP；POST 405 structured error；路径穿越 403。

## 接管 P0 closure

| P0 | 证据 |
|---|---|
| arbitrary COLMAP 被误标 ENU/meters | `CoordinateFrame` + registration tests；无显式 Sim3 不升级 |
| transform exactly-once | 内容寻址 ID、same-frame nonidentity 拒绝、PLY/manifest history tests |
| Viewer 镜像 | `worldToThree(E,N,U)=(E,U,-N)` 与 determinant `+1` tests |
| SH 丢失 / 假 splat | degree-3 round-trip + Spark runtime + DC fallback tests |
| provenance/fidelity 缺失 | recon manifest v2 + bridge capability + Studio normalization tests |
| 素材类型未消费 | default 5×5 manifest 11/11，含 prop regression |
| framing 硬编码 | negative/non-square/non-200m/single-chunk tests |
| fresh clone 不可恢复 | generator/manifest/contact sheet 临时目录演练 |

## 明确限制

- 未在本机执行真实 COLMAP binary 或 GPU 3DGS training；真实 PLY 通过 import 契约接入。
- 单个 GPS 不足以证明 SfM→ENU；需要控制点 Sim3 与残差证据。
- distortion 当前完整存证，尚未进入畸变感知投影/训练。
- 高阶 SH 空间旋转未实现 basis rotation，当前阻断而非静默保留。
- Spark/Three/WASM 当前为固定 CDN 版本；离线时降级。
- Studio server 有意只读；GLM API 因未配置 key 只验证 schema/mock fallback。
- world 3.1M points 的终端性能尚需在目标设备测量。
