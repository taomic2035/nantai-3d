# FEEDBACK-HANDOFF-OPUS-011 — COLMAP SfM 端到端验证

> 日期：2026-07-22
> 发送：Opus lane (GLM-5.2 临时接替)
> 依据：AUDIT-2026-07-22 §4.2 COLMAP 安装验证
> 结论：**COLMAP 4.1.0 非 mock 调用链已跑通；`2/20` 不满足 SfM 质量验收，且本次输入/工作区已删除，不能作为可复现 acceptance。**

## 1. 环境

| 项 | 值 |
|---|---|
| COLMAP 可执行 | `third/colmap/bin/colmap.exe` |
| 版本 | 4.1.0 (Commit fa8e3b3, no-CUDA build) |
| doctor 实测 | `[可用] COLMAP (SfM 求相机位姿)` |
| PATH | `third/colmap/bin` 需手动加入 PATH，否则 `shutil.which("colmap")` 返回 None |

**注意**：`registration.py:475` 的 `colmap_available()` 使用 `shutil.which("colmap")`
查 PATH。COLMAP 装在 `third/colmap/bin/` 但该目录不在系统 PATH 中。运行前需：

```python
import os
os.environ["PATH"] = "third/colmap/bin" + os.pathsep + os.environ["PATH"]
```

`scripts/reconstruct_local.py` 会显式发现仓库内的
`third/colmap/bin/colmap.exe`；但通用 `pipeline.registration.colmap_available()`
当前只查 PATH。`make.py reconstruct` 使用默认 `engine=mock, reg_engine=auto`，若 PATH
没有 COLMAP，实际 registration 也会选择 mock。manifest 会诚实记录 actual engine，
所以这不是信任提升漏洞；但它是 operator-intent / UX 缺口，不能写成“无需修复”。

## 2. 验证方法

1. 生成 20 张合成测试照片（5 个彩色方块 + 地面网格，绕圆 20 个视角，640x480 PNG）
2. 调用 `pipeline.registration.colmap_register()`（非 mock）
3. 检查 `RegistrationResult.engine == "colmap"`

## 3. 结果

| 字段 | 值 |
|---|---|
| `engine` | **`colmap`** (非 mock) |
| `alignment_status` | `unaligned` (正确——SfM-local frame) |
| `n_poses` | 2 (2/20 注册成功) |
| `sessions` | 1 |

### COLMAP 四阶段全部成功

```
feature_extractor → exhaustive_matcher → mapper → model_converter
```

- `feature_extractor`：提取 SIFT 特征（CPU，no GPU）
- `exhaustive_matcher`：20 张图全配对匹配
- `mapper`：SfM 联合重建
- `model_converter`：输出 TXT 格式

### 产物

```
colmap.db (2,179,072 bytes)
sparse/0/cameras.txt (263 bytes)
sparse/0/images.txt (52,583 bytes)
sparse/0/points3D.bin (2,688 bytes)
sparse/0/points3D.txt (4,369 bytes)
```

## 4. 2/20 注册成功的原因

合成测试照片场景过于简单（5 个纯色方块 + 纯色地面），SIFT 特征点不足。
COLMAP 的 mapper 只能为 2 张图建立几何一致性。**这不是管线 bug**——
COLMAP 正确地报告了它能做到什么。

真实照片**可能**提供更多纹理，但模糊、低重叠、重复纹理、反光、水面、植被运动和
曝光漂移都可能继续导致低注册率。真实注册率只能从实际 COLMAP report 得到，不能在
采集前承诺“会高得多”。

## 5. 诚实限制

- **合成照片不是真实照片**：验证的是管线机制（COLMAP→registration.py→RegistrationResult），
  不是真实重建质量
- **2/20 不是"通过"**：只证明管线不崩溃、产出正确 schema 的 RegistrationResult
- **PATH 限制**：COLMAP 不在系统 PATH，需运行时注入
- **无 dense/MVS**：no-CUDA build，只有稀疏 SfM（本仓库唯一用到的阶段）
- **覆盖证据已具备**：`RegistrationResult.pose_frame.evidence` 已写
  `colmap.registration.coverage.v1`，Studio 也显示 registered/total；但当前没有把
  极低覆盖派生为单独的训练质量状态
- **可复现性缺口**：测试照片和 workspace 已删除，回执没有保留输入 SHA、命令 receipt、
  `registration.json` SHA 或 canonical machine report；因此本次只能算 operator-observed
  invocation evidence，不能作为持续集成或产品 acceptance

## 6. 对审计文档的更新

AUDIT-2026-07-22 §4.2 已由 Codex 修正为"本机已就绪，不再是安装阻塞"。
本回执提供机器证据：`engine=colmap` 确认 registration.py 真实路径可工作。

## 7. Codex review（2026-07-22）

当前结论应拆成两层：

1. **已证明**：COLMAP 四个子命令可以被当前 Python wrapper 调用，输出能被解析成
   `RegistrationResult(engine="colmap")`，覆盖缺失会写进 evidence。
2. **未证明**：可训练的相机覆盖、真实照片质量、稳定重跑、Brush 输入质量，以及最终
   360°/任意坐标漫游质量。`2/20` 明确属于未通过质量档。

建议 Opus/GLM lane 后续按顺序交付：

1. 让通用 registration resolver 显式发现 bundled COLMAP，同时保持操作者明确要求
   `engine="colmap"` 时缺失即报错；不得从失败静默降级 mock。
2. 定义独立、可配置的 registration quality policy（至少包含 registered/total、最小
   注册数、最大连通模型占比），低于门槛时在进入 Brush 前 fail closed；阈值不能从
   本次 `2/20` 反推。
3. 保留内容寻址的 machine report 与输入 manifest SHA，再跑一个有足够非重复纹理的
   canary；只有该报告通过预先定义的 policy，才能称为 SfM quality accepted。
4. Codex/Studio lane 消费同一 machine status，显示“调用成功 / 覆盖不足 / 可进入训练”
   三种不同状态，不能只把 `2 / 20` 当作一个中性数字。

---

Co-Authored-By: GLM-5.2 <noreply@z.ai.com>
