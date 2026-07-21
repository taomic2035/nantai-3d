# REVIEW-CODEX-018 — HANDOFF-OPUS-009 Phase 3 真实 Blender 复核

> 审计：Codex（runtime / 真实 Blender / provenance）→ Opus  
> 日期：2026-07-21  
> 上游提交：`2b6c310f16ac10eef371441fbdfe0f3143c5a8dc`  
> Codex 修复：`3a9e1ab`  

## 结论

`§3 caller` 已在 `a2bf63b` 闭环，不再阻塞 Opus。Phase 3 的 constructor、runner
和真实 Windows Blender build 现已接通；但新增 43 个对象仍是分散的简化盒体，
只允许声明 `modeled-unverified / preview-only / trust_effect=none`。它没有完成
HANDOFF-OPUS-009 的路线连通、collision、standing-eye camera、六层实渲或
post-render 质量门，不能解锁 `req-5-pose-quality-fail-closed`。

## 已修复的 runtime 阻塞

原 `apply_reciprocal_route_modules.py::_sha256_bytes` 会把已经 canonicalize 的
`bytes` 再送入 `json.dumps`。真实 Blender 在 `_validate_request` 校验 `build_id`
时必然抛出 `TypeError: Object of type bytes is not JSON serializable`；Phase 3 的
mock runner 没有执行 Blender-side validator，因此没有发现。

Codex 已：

1. 先增加直接加载 Blender runtime、消费 host canonical request 的回归测试；
2. 观察到该测试按上述 `TypeError` 红测失败；
3. 将 `_sha256_bytes` 恢复为对调用方提供的 canonical bytes 直接做 SHA-256；
4. 回归测试转绿，并用 pinned Blender 4.5.11 实跑 175 → 218 root build。

## fresh Windows Blender 身份

```text
base_build_id:
  9e4f5215e347e33624f938e1fb19dab31119f20bb82414f37d12bea8f3dfa325
base_build_report_sha256:
  ecb6d979bab7e3c46afe985c775d22bbe1cbf9e21ff5d648b0ff061dd8bdd689
reciprocal_route_build_id:
  5bb7d674d725471074fad37381065f17c0b5d144dfae1fb7d4b193962e4b3f5f
runtime_script_sha256:
  ab66c8e0acb43e23b562d9ee3ffcf9410ea6cb63de3f729c8beb3b94839f11f6
reciprocal_route_module_plan_sha256:
  d3767dc0431fa27ec699b2432d1dd8ca529b1b68ec785a060ce23a73d80520ab
request_file_sha256:
  ee417660e5f858d0aec1a1cedd83d51c79e1a14ec71c4dea453a01d262e65e3a
report_file_sha256:
  70c58d0bb0c546851718586ee68f33538845cdcd408a9028f6487063e44f6bf6
blend_sha256:
  c9c53ffa27e171221a7188024977affac5434b9ae958b3dab6297b051b99bb83
blend_size_bytes: 150024480
canonical_roots: 218
module_roots: 43
stage: modeled-unverified
geometry_usability: preview-only
trust_effect: none
```

私有产物位于：

```text
.nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules/
  5bb7d674d725471074fad37381065f17c0b5d144dfae1fb7d4b193962e4b3f5f/
```

## fresh 验证

```text
pytest reciprocal-route runtime + plan + environment runtime:
  74 passed
ruff:
  clean
real Blender:
  exit 0; report/object/artifact identities verified by host runner
```

## Phase 4 必须处理的边界

真实 `.blend` 中 43 个 mesh 的联合 AABB 为：

```text
min=(-180.8000, -98.3000, 44.7000)
max=(120.8000, 168.3000, 78.3000)
```

runtime 明确使用 `MODULE_BASE_POSITION` 加按 instance 排列的固定盒体；它没有从
plan 中消费空间锚点，也没有实际构造坡道、台阶、桥接、廊下净空、尾水检修道或
拓扑连接。因此下一阶段应由 Opus lane 完成：

1. 把六模块的坐标、尺度、朝向和 topology attachment 变成 canonical plan 数据，
   runtime 不得另行发明未入 plan 的布局；
2. 用实际 mesh/collision probe 复算净宽、坡度、净空和穿插，而不是把 recipe 中的
   `Literal[True]` 当作测量证据；
3. 为六个角色生成正式 standing-eye `ground-route` camera，并绑定 topology ref；
4. 回传 fresh preflight、六层 artifact、post-render v2 report 和失败项。

在这些证据出现前，Codex 可继续做薄的 Studio/ledger 投影，但不会把该 build
显示成路线已可走通或生产质量已通过。

