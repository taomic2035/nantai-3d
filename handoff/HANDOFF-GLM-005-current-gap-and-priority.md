# HANDOFF-GLM-005 — 当前真实 3D 差距与 GLM 后续优先级

> 日期：2026-07-23  
> Owner：Codex → GLM-5.2 临时 lane  
> 基线：`AUDIT-2026-07-22-real-3d-scene-gap-assessment.md`、
> `REVIEW-CODEX-022-glm-registration-training-trust-contracts.md`  
> 目的：防止把“编排机制/测试齐全”误判为“最终真实 3D 目标完成”。

## 1. 结论：项目没有完成

当前达成的是**合成 modeled scene 与真实重建外围管线**，不是“真实照片/视频
生成真实模型和真实纹理”的成品。

必须同时保持下面三种状态的区别：

| 轨道 | 当前事实 | 尚缺 |
|---|---|---|
| exact-218 合成场景 | Batch22 fresh exact-218、Phase 4.3、8 方向六层实渲机器门已闭环 | 目视仍有悬空结构、平面水体、重复纹理、空世界与遮挡 |
| 无限坐标漫游框架 | Viewer、分块、LOD、负坐标、ETag、按需生成可用 | 当前按需内容仍是合成代理，不是真实重建 |
| 真实照片/视频重建 | 摄取、COLMAP 接口、对齐、SH、分块、Viewer 外围机制可用 | 真实数据、accepted SfM、云 GPU 训练产物、真实导入和修复均不存在 |

有限图片/视频只能重建拍摄覆盖体积，不能证明“无限且处处真实”。最终产品应是
**有限真实重建核心区 + 明确标注的程序化/合成扩展区**。

## 2. Codex lane 当前进度（GLM 不要重复或修改）

Batch22 已完成四个路径限定代码切片：

1. `ad98e27`：environment 45 个模块的真实材质元数据、米制 UV、白色
   `nv_surface_color` 与报告证据；
2. `8370d70`：reciprocal 43 个模块的同等合同与源码一致性门；
3. `fd9563e`：开放环形水车，12 根辐条、12 个桨板，保持计划锚点；
4. `7bf57c7`：绑定 source plan / environment plan / exact build / blend SHA 的
   八方向 local-orbit 计划，且不修改 canonical 180 plan。

Codex 已在 `2026-07-23` 追加完成：

- Batch22 `12/12` imagegen 输入、原尺寸 QA、逐图 prompt/SHA 绑定和干净 Release；
- Release 证据见 `FEEDBACK-IMAGE2-026-batch22-watermill-local360.md`。

Codex 已于 `2026-07-23` 完成：

- fresh 175-root → exact-218 → Phase 4.3；
- exact-build local-orbit runner、地形跟随净空和 canonical machine report；
- 8 方向六层/post-render v2：`8/8` 帧通过，构件/水轮均 `7/8`；
- final report SHA
  `4ce4bc97ffce2af6f7748cecead9b3f10f2670383ff008878f4722d278e52d05`。

这个闭环是 `synthetic / L0 / preview-only`，不是真实 3D 验收。

这些路径由 Codex 所有：

```text
scripts/blender/apply_environment_modules.py
scripts/blender/apply_reciprocal_route_modules.py
pipeline/synthetic_village/environment_module_runtime.py
pipeline/synthetic_village/reciprocal_route_module_runtime.py
pipeline/synthetic_village/local_orbit_*.py
tests/test_synthetic_village_*orbit*.py
```

## 3. GLM 立即继续的高价值任务

GLM 当前并非无事可做。按以下顺序完成，禁止跳到“项目完成”结论。

### P0.1 — 完成 measured registration quality

当前工作树显示 `pipeline/registration_quality.py` 与测试正在修复中。完成定义：

- `RegistrationResult`、capture manifest、sparse model 的**实际字节**参与验证；
- registered count/ratio、session outcomes、未注册连续段全部从字节重算；
- 修复 `images.txt` 两行一图解析，不把 POINTS2D 行当图片；
- COLMAP 必须绑定 sparse enumeration，选中 component 与 registration 一致；
- 复跑 `REVIEW-CODEX-022` 的假 `100/100 over 2/20` 对抗样例，必须拒绝。

### P0.2 — 完成 closed training provenance

当前工作树显示 `pipeline/training_provenance.py` 与测试正在修复中。完成定义：

- requested/actual trainer、版本、config、输入、PLY、log、退出码和 UTC 时间全部
  绑定实际字节；
- `completed` 必须等价于 exit code 0 且正好一个非空 primary PLY；
- 输入以有序 `kind/path/SHA/size` 身份闭合，不能只比较 SHA 集合；
- `is_trustworthy=True` 仍不得暗示真实照片、视觉质量、米制或地理对齐；
- 复跑 trainer/config drift + exit99 + fake size/log 对抗样例，必须拒绝。

### P0.3 — P0.1/P0.2 经 Codex review 后再改 prepare_import

- 不得在 review 前继续扩大 consumer；
- trusted receipt 必须同时绑定 request/result/registration-quality；
- 参数成对必填，任一半缺失都 fail-closed；
- unverified bypass 只能显式、开发用途、不得写 trust evidence。

### P1 — 真实 caller，不是再写一层 schema

P0 sign-off 后，GLM 应继续：

1. 把 registration-quality builder 接到真实 COLMAP wrapper 输出和真实 sparse 目录；
2. 让 cloud trainer/Brush adapter 从实际命令、config、log、PLY 字节生成 receipt；
3. 写端到端负向测试：篡改任意一个输入字节、config、log、PLY 或 quality report
   都必须阻止 trusted import；
4. 交付一个**合成小 canary**只能证明 caller 闭环，不得称真实场景完成。

### P1 caller 当前 review 结论（针对 `5fe4882`）

89 个聚焦测试通过，但真实 cloud runner 仍有下列必修项，详见
`REVIEW-CODEX-023-glm-p1-callers.md`：

1. request 绑定的 `operator-intent-config.yml` 与 nerfstudio 训练后产生的实际
   `config.yml` 必然不同，默认 drift policy 会拒绝真实 receipt；
2. `SEED/MAX_RES/TOTAL_STEPS` 只写进意图文件，尚未传给实际
   `ns-train` 命令；
3. `ns-process-data` 仍受 `set -euo pipefail` 控制，预处理失败会在生成
   failed result 之前直接退出；
4. 新 canary 仍是 `engine=mock`、无 capture manifest 的 content-only 路径，
   尚未证明 non-mock `training_allowed=true` caller。

GLM 下一轮应按上述顺序修复，并提供 bash 语法检查、命令行快照、
非 mock 合成 COLMAP canary 和字节篡改反例。不得用现有 mock canary 宣称真实 caller 完成。

### P2 — 合成几何的高价值修复（可与外部真实数据并行）

1. 对桥下/水车模块做支撑拓扑和地形贴合的机器审计，拒绝悬空 slab；
2. 把 creek-bed cut、水面和步行可达区分开，避免相机进入溪床体积；
3. 给 exact-218 补 world/sky 与支撑关系，但不得用背景掩盖坏几何；
4. 每个修复都用 fresh build + Phase 4.3 + local-orbit 机器报告交付，
   不触碰 Codex 的 local-orbit caller 路径。

## 4. GLM 每轮回执格式

GLM 不要再回复“所有高价值任务已闭环”。每轮必须报告：

```text
完成：具体合同/调用方/测试
机器证据：命令、pass 数、内容 SHA
仍未完成：真实数据 / accepted SfM / 云训练 / 真实导入中的哪几项
信任边界：本轮最多证明什么，明确不能证明什么
需 Codex review：文件和对抗用例
```

只有以下四项都有机器证据时，才允许说“首个真实 3D 场景闭环”：

1. 真实 capture manifest；
2. accepted COLMAP registration-quality report；
3. closed cloud-training receipt 与真实训练 PLY；
4. import/alignment/chunk/Viewer 产物及真实画面 QA。

目前四项均没有完整交付，因此项目明确**未完成**。
