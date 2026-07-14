# FEEDBACK-TAKEOVER-001 — 给 Opus 的接管回球

## What

- 建立版本化坐标真相源：`CoordinateFrame`、`FrameTransform`、`SplatInput`、registration v2、
  PLY `nantai_meta` 与 manifest ancestry/transform chain。
- 完成图像+视频 session 联合配准；COLMAP 读取 `cameras.txt/images.txt` 的逐图相机模型、
  distortion 原始参数与注册覆盖率，mock 永久标 synthetic。
- 完成 3DGS DC/高阶 SH/opacity/anisotropic scale/quaternion/normals/extras 的保真读写，
  拼接、去重、区域替换、LOD 与 exactly-once 变换历史；branched ancestry 以逐路径 metadata
  保存并拓扑合并，兄弟分支可继续作为 base。
- HANDOFF-001 交付 11 个确定性村庄 3DGS 素材、manifest v2、SHA 与 contact sheet；registry
  支持安全 ID、containment、幂等恢复、跨实例 CAS、失败回滚和实际 SHA 消费证据。
- 默认 5×5 world 真实消费 building 5、vegetation 3、prop 3，共 11/11。
- Viewer 改为右手 ENU→Three 映射、动态 framing/minimap/chunk/LOD；固定 Spark 2.1.0 渲染
  完整 3DGS，失败时明确降级 DC point preview。
- Studio 交付三栏工作台、六步状态、provenance gate、素材卡、job drawer、local/mock adapter、
  same-origin bridge 与 read-only server；浏览器验证了 Spark、LOD、图层、复位和 11/11 素材。
- Studio 证据边界验证 descriptor/hash/path、full/chunk/asset PLY 的结构与 Gaussian 语义；
  NaN/Inf、非法 quaternion/SH/scale、list/object 字段均 fail closed。

## Why

接管前最危险的不是缺按钮，而是 engine 名、文件存在与 registry 条目会被误当成真实、米制、
已消费或完整 3DGS。此次把可信度改成由 artifact 属性、坐标证据、transform history、实际 SHA
和 renderer runtime capability 联合推导；无法证明时仍允许预览，但 fail closed。

## Tradeoff

- `engine=mock` 只生成流程 proxy；真实训练由外部 gsplat/nerfstudio 等产出标准 PLY 后导入。
- COLMAP distortion 完整保留在 machine evidence，但当前 `CameraIntrinsics` 仍是 pinhole 消费面。
- 高阶 SH 平移/统一缩放安全，涉及旋转时阻断，尚未实现 SH basis rotation。
- Spark/Three 走固定 CDN 版本；离线时是 DC fallback，不是 full splat。
- local Studio server 只读，避免 UI 伪造任务成功；实际 ingest/reconstruct/world/assets 从 CLI 运行。
- asset transaction 没有 crash journal；SIGKILL 最坏留下未登记 orphan payload，registry 不会指向
  半成品。文件锁为 `fcntl`，未覆盖 Windows。
- canonical PLY 语义校验优先复用单一 loader，避免 Studio 与重建规则漂移；代价是首屏快照
  峰值约 144MB，未来高频并发时应改为共享的零复制 validator。

## Open

1. 选一套可公开的真实图+视频数据与控制点，完成 measured mixed reconstruction 基准。
2. 扩展 distortion-aware projection/training，并把 reprojection error 写入发布 gate。
3. 决定是否把任务执行白名单与持久 run ledger 接回 Studio；当前 API 有意只读。
4. 针对目标设备标定 world/vegetation 点预算与 Spark 内存上限。
5. 如需离线发行，vendoring Spark/Three/WASM，并补离线 E2E。
6. 若 Studio 改为高频轮询，先做 snapshot 请求合并/缓存或零复制 PLY validator，避免并发内存放大。

## Next

- 先运行 `make test PY=.venv/bin/python`，再按
  `docs/verification/2026-07-14-takeover-report.md` 复核产物证据。
- Review 路径建议：
  1. `pipeline/recon_schema.py`、`registration.py`、`gaussian_scene.py`、`reconstruct.py`；
  2. `pipeline/assets.py`、`validate_handoff.py`、`render_chunk_to_ply.py`、HANDOFF-001；
  3. `web/viewer/`、`web/studio/`、`pipeline/studio_server.py`。
- 当前 fresh gate：Python 232、Viewer 32、Studio 33、`make verify`/Ruff/diff PASS；PLY 语义
  reviewer 已放行 `18dbce0`。
- 整分支 reviewer 已放行 `7ab1a2c`：单行/汇总 consumption point budget 与真实 11/11
  world 均复验通过，无 P1/P2。
- PR [#1](https://github.com/taomic2035/nantai-3d/pull/1) 已 squash 合入 `main`，merge commit
  `e4a2e90`；开发分支保留小步提交历史。
- Opus 的未提交注释已精确保存在 stash
  `opus pre-takeover recon_schema honesty comment 2026-07-15`；binary diff SHA-256 为
  `7370b3614671abd8a348598af106dab91b2ca543f7080889b466b292a0d428fb`，恢复前先与当前契约核对。
- GitHub Codex reviewer 两次触发均未接单，依门禁采用整分支 peer reviewer 的明确 PASS；没有
  未处理的 P1/P2 或行内意见。
