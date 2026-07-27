# 协作历史摘要

本页取代已完成的逐轮 HANDOFF / FEEDBACK / REVIEW 文档。精简前最后一个完整快照为
Git commit `381f243ebed3bb8dcc0e47608ac1548c55e8c621`；需要逐字段追溯时从该提交或
更早历史读取原文。

## 2026-07-14 至 07-16：接管与安全基线

- 建立 provenance fail-closed、内容 SHA、坐标/尺度和 immutable job 约束。
- 完成 Studio 基础状态、严格 ingest、crash recovery 与跨平台素材门。
- 明确 synthetic/mock 只能预览，不能提升为 measured/metric/aligned。

## 2026-07-17 至 07-20：可运行 Viewer 与 Blender 基线

- 完成确定性分块、LOD、按需 synthetic 世界和大重建流式 Viewer。
- 打通照片/视频摄取、COLMAP 位姿消费、外部 3DGS 导入、Sim3/ENU 与高阶 SH 旋转。
- 建立 180-camera preflight、六层渲染、visibility 与 post-render 质量合同。
- image2 Batch 5–14 提供路线、建筑、院落、桥底和环境设计输入；均保持
  `design-only / forbidden-as-multiview`。

## 2026-07-21 至 07-23：拓扑、材质与 exact build

- reciprocal route、六角色相机、环境模块、生产 journal/caller 完成多轮 fail-closed
  修复。
- exact-218 与 Phase 4.3 得到 synthetic 实测证据；local-360 证明水车局部可读，
  但不证明全场景 coverage。
- Batch 15–25 扩展材质、路口、室内/边界、环境支撑和水车建模参考。
- 云训练 caller、registration-quality 和 artifact integrity 完成内容绑定；stub
  只能证明 argv/契约，不能代替真实云 GPU 训练。

## 2026-07-24 至 07-25：真实链路审计与 Preview 冻结

- GLM lane 完成真实 COLMAP 失败路径、dense overlap/P5b、长视频采样/P6b、P7
  recovered-pose 导入候选；Codex 修复 parser、事务替换与 WAL 收敛。
- roaming graph producer 完成纯模型与 Viewer 消费合同；当前 fixture 只有两房间、
  一 portal、零 loop，不代表任意坐标可达。
- image2 Batch 26–35 扩展近场、LOD、landmark、室内、闭环、材质和八类道具六视图。
- `Nantai 3D 1.0 Preview` 冻结为可下载 synthetic point-preview；Blender 封面是
  独立审计渲染，不与交互数据冒充同一 scene identity。

## 仍未关闭的门

1. 真实、密集、重叠且可发布的采集；
2. accepted real-photo COLMAP/SfM；
3. 非 mock 云 GPU 3DGS 输出；
4. 控制点或其它实测米制对齐；
5. 真实重建产物的 Viewer QA；
6. synthetic exact-266 的完整六目标、双 seam 和全角色质量门。

设计图、synthetic Blender、mock/COLMAP 失败 smoke、stub trainer 与本机 Brush 小样
都不能替代这些门。

## 2026-07-26 至 07-27：Preview2 与 Production V1 caller

- 发布 `v1.0.0-preview.2`；Release 仍是 synthetic point-preview，不冒充真实重建。
- 新增 rights/receipt、fresh real COLMAP、held-out split、local Brush、remote-shell
  Splatfacto、import/chunk、render/viewer/human review 和 aggregate acceptance 合同。
- fresh canary 得到真实照片 COLMAP 与本机 Brush `preview-only` 证据；未取得云 GPU
  production 结果、米制对齐或真实 Viewer/human acceptance。
- 最新 `main@348422f` 出现跨平台 CI 回归；当前修复与连续执行队列见
  [HANDOFF-GLM-011](HANDOFF-GLM-011-production-v1-critical-path.md)。
