# AGENTS.md — Nantai 3D 协作约定

本文件只保留所有 agent 当前必须知道的事实。阶段过程见
[`handoff/HISTORY.md`](handoff/HISTORY.md)，不要把历史对话重新堆回本文件。

## 分工

| 角色 | 主要责任 |
|---|---|
| Opus / GLM 接替 lane | pipeline、坐标、SfM/3DGS 外围合同、registry、Blender 构建与跨平台集成 |
| Codex | UX、Viewer/Studio、发布、审计/review、真实浏览器 QA |
| GPT image2 | 通用可替换的素材生成、设计和图像处理 |
| 共同 | 重难点分析、fail-closed 修复与发布门 |

GLM 交付必须经 Codex review；“测试绿”不能替代真实 scene/layer/render 证据。

## 非协商约定

- **Provenance fail closed**：信任只从机器字段、内容 SHA、FrameTransform、
  transform history、renderer capability 和实测报告推导。未知可以预览，但不能静默
  提升为 measured/metric/aligned。
- **如实报告边界**：不把 design-only、mock、stub、失败 smoke 或 synthetic 实渲
  描述成真实照片重建。
- **Git**：只保留 `main` 和一个 worktree；多 agent 共用工作树，必须路径限定
  `git add` / `git commit -- <paths>`，禁止 `git add -A`、`commit -a`。
- **提交门**：代码完成、专项测试与 lint 通过后再提交；Codex 提交尾行：
  `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`。
- **网络**：push 使用一次性代理，不修改持久 Git 配置：

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- **Release**：只包含最终白名单产物、manifest、使用说明与 checksum；候选图、失败
  请求、contact sheet、缓存、私有 Blender 工作目录和中间日志不发布。

## 当前发布状态

当前公开版本为 **Nantai 3D v1.0.0-preview.2**：

- Git tag：`v1.0.0-preview.2`
- Python version：`1.0.0rc2`
- 默认场景：539 个 mesh / 24 个视觉材质的 synthetic GLB；
- 后备数据：67,858 个 synthetic Gaussian、LOD 0/1/2、25 个围绕原点的
  world chunks 和 11/11 个 registry 素材；
- 封面：从最终 runtime 包的默认 Viewer 首屏截取；
- 信任：`synthetic / preview-only`，不可测量，也不证明任意坐标可达。

发布与运行说明：
[`docs/releases/1.0-preview.2.md`](docs/releases/1.0-preview.2.md)。

## 真实重建能力边界

仓库负责：

```text
摄取/抽帧 → 采集预检 → COLMAP 位姿合同 → 外部 3DGS 产物
→ SH/四元数处理 → 导入/拼接/LOD → Sim3/ENU → Studio/Viewer
```

仓库不内置高质量 3DGS 训练器。当前开发机没有可用 NVIDIA CUDA；COLMAP CPU 可跑
但慢，本机 Brush 只适合受限小场景，高质量主路径是外部云 GPU。

真实场景完成必须同时具备：

1. 真实重叠采集；
2. accepted real-photo SfM；
3. 非 mock GPU 3DGS；
4. 实测米制对齐；
5. 真实重建 Viewer QA。

任何一项缺失都必须保持 preview/unknown。天空、玻璃、水、无纹理面和未拍摄体积仍会
产生空洞或漂浮物，“完美 360° 任意坐标漫游”不是可承诺目标。

## Synthetic / Blender 当前边界

- image2 素材是独立设计输入：`geometry_consistency=not-verified`、
  `training_use=forbidden-as-multiview`、`trust_effect=none`。
- exact-218 / exact-266、local-360、六层与 post-render 只证明对应 synthetic build；
  不证明真实纹理、真实 SfM/3DGS 或 measured alignment。
- roaming graph 当前是 scene-bound 的纯模型合同；不能与不同 scene identity 的
  point-preview 数据强行拼接。
- Batch35 八类道具已进入纯模型 part graph；Blender emission、exact build 和实渲
  验收仍是后续门。

## 当前协作入口

- [`handoff/README.md`](handoff/README.md) — 当前 handoff 索引
- [`handoff/HANDOFF-GLM-011-production-v1-critical-path.md`](handoff/HANDOFF-GLM-011-production-v1-critical-path.md)
- [`handoff/HANDOFF-GLM-009-roaming-graph-producer.md`](handoff/HANDOFF-GLM-009-roaming-graph-producer.md)
- [`handoff/HANDOFF-OPUS-006-production-camera-quality-gates.md`](handoff/HANDOFF-OPUS-006-production-camera-quality-gates.md)
- [`handoff/HANDOFF-OPUS-007-batch6-modules-productionization.md`](handoff/HANDOFF-OPUS-007-batch6-modules-productionization.md)

## 权威用户文档

- [`README.md`](README.md) — 产品定位、能力、快速入口
- [`docs/README.md`](docs/README.md) — 文档导航
- [`docs/manual/reconstruction-setup.md`](docs/manual/reconstruction-setup.md) — 真实重建手册
- [`docs/real-data-workflow.md`](docs/real-data-workflow.md) — 坐标、证据与导入合同
- [`docs/releases/synthetic-design-inputs.md`](docs/releases/synthetic-design-inputs.md) — 素材 Releases

历史 plan/spec/review 仅在回归追溯时读取，不是当前执行入口。
