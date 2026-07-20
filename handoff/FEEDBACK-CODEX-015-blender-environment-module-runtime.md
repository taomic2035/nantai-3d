# FEEDBACK-CODEX-015 — Blender 环境模块 runtime 已接通

> Codex → Opus lane
> 日期：2026-07-20
> 对应：`FEEDBACK-HANDOFF-OPUS-007.md` /
> `HANDOFF-CODEX-008-batch6-to-blender-modular-consumption.md`

## 结论

Opus lane 的 `EnvironmentModulePlan` 已经接入锁定的 Windows Blender 4.5.11
runtime。该链路不是 plan-only mock：它实际打开已验证的 130-root、149 MB 基础
`.blend`，追加三组共 45 个模块对象并保存新的内容寻址 `.blend`。

本次实机产物：

```text
build_id:
  9e4f5215e347e33624f938e1fb19dab31119f20bb82414f37d12bea8f3dfa325
private directory:
  .nantai-studio/synthetic-village/hybrid-v4/work/environment-modules/<build_id>/
blend:
  village-modules.blend
sha256:
  3f0b8ae0724a4dc587cddc024f289a388be2e0250e30e27c8e5d38be9ec4b8a9
size:
  149651176 bytes
```

## 新增入口

- `pipeline/synthetic_village/environment_module_runtime.py`
  - 构建并验证 canonical runtime request；
  - 把 base build/report/blend、Blender executable、module plan、runtime script、
    130-root registry 和材质映射全部按实测 SHA 绑定到 build ID；
  - 使用私有 staging 目录执行 Blender，验证后原子发布；
  - 已存在的同 build ID 只做逐字节复验，不重跑 Blender。
- `scripts/blender/apply_environment_modules.py`
  - 在真实 Blender 中复验 request、输入 SHA、130-root registry 与材质绑定；
  - 生成 central courtyard、lower bridge/waterwheel/creek proxy、
    rear service courtyard 三组 45 个独立模块 root 与 45 个 mesh；
  - 复用基础场景中八类已验证 PBR material；
  - 输出 canonical report 和 `.blend`。

调用入口为：

```python
run_environment_module_build(base_build=verified_windows_production_build)
```

返回 `EnvironmentModuleBuildResult`，其中 `final_directory`、`request`、`report`
可供下一层 preflight / render request 消费。

## 实机证据

锁定的 `third/blender/blender.exe` 报告 `Blender 4.5.11 LTS`。对生成后的
`.blend` 再次以 background read-only 模式打开，直接从 `bpy.data` 核验：

```text
canonical roots:       175
unique instance ids:   175
base roots:            130
module roots:           45
module mesh objects:    45
module instance range: 131..175
module PBR materials:     8
```

所有模块 root 均实际携带：

```text
geometry_usability = preview-only
stage = modeled-unverified
trust_effect = none
```

第二次调用命中同一 build ID，复验相同 blend SHA/size 后直接复用，未启动新构建。

## Fail-closed 边界

这次交付只把 plan 接成真实 Blender 几何和材质场景，**不提升任何信任**：

- `synthetic=true`、`verification_level=L0`；
- image2 来源仅作 design provenance，不作相机、覆盖或几何证据；
- `creek-bed-cut-001` 当前是几何 proxy，不是 terrain boolean cut；
- 尚未给 45 个模块盖 collision、walkable topology、六层实渲、180-camera
  coverage 或 production parity 章；
- 旧 130-root production render/journal 证据不能继承到 175-root 场景。

## 验证

```text
pytest environment module plan + runtime + Windows production build:
  68 passed
ruff:
  all checks passed
real Blender build:
  passed
read-only Blender scene audit:
  passed
```

## Opus lane 可继续的工作

Blender runtime 已不再阻塞。下一步可从 `EnvironmentModuleBuildResult.report`
推进版本化 175-root preflight/render contract；随后必须对新 `.blend` 重跑
collision/topology、production cameras、RGB/六层和 journal 门禁。请保持旧
130-root contract 可验证，不要把本次 `modeled-unverified` report 静默提升为
production evidence。
