# HANDOFF-CODEX-012 — engine SHA 绑定 + 剩余 5 role 相机接入

> 日期：2026-07-22
> 发起：Opus (GLM-5.2) → Codex
> 状态：Opus P0 全部闭环；本交办包含 1 P0 fail-open 修复 + 1 P1 batch 相机接入
> 信任边界：所有产物仍为 `synthetic=true`、`verification_level=L0`、
> `geometry_trust=simplified-pbr-not-render-parity`、`trust_effect=none`

## 背景

Opus lane P0（Phase 4.1 mesh probe / 4.2 candidate schema / 4.3 amendment /
4.4 camera materialization）已全部闭环并通过对抗性 fail-closed 审计
（REVIEW-OPUS-001 ~ 011）。

当前有 **2 项被 Codex 阻塞**，需 Codex 实施。两项都在 Codex WIP 文件中，
Opus 不能直接修改。

---

## P0 — reciprocal 路径 engine script SHA 未验证（fail-open 修复）

### 发现来源

`handoff/REVIEW-OPUS-009-production-layer-counts-failclosed-audit.md`

### 问题

`scripts/blender/render_reciprocal_route_production.py` 通过 `importlib`
加载 `scripts/blender/render_synthetic_village.py` 作为引擎模块。reciprocal
render request 绑定的是 wrapper script 的 SHA（`render_reciprocal_route_production.py`
自身），**不是** engine script 的 SHA（`render_synthetic_village.py`）。

### 攻击场景

1. 攻击者修改 `render_synthetic_village.py` 中的 `_production_layer_counts`，
   返回假 layer_statistics（如 `valid_depth_pixel_count = PIXELS`，让坏帧通过
   quality gate）
2. 不修改 `render_reciprocal_route_production.py`（否则 SHA 不匹配）
3. reciprocal render request 验证 wrapper SHA → 通过（wrapper 未改）
4. Blender 执行 wrapper → importlib 加载被篡改的 engine → 假 layer_statistics
   写入 frame-report
5. quality gate 使用假数据 → 坏帧通过

### 对比

local production render 路径直接用 `render_synthetic_village.py` 作为
renderer script，其 SHA 已绑定到 render request → 安全 ✅

reciprocal 路径用 wrapper + importlib 加载 engine，只绑定 wrapper SHA →
**MEDIUM fail-open** ⚠️

### 严重性

**MEDIUM** — 需要文件系统访问权限，但在共享开发环境中可行。影响 reciprocal 路径
所有 218-root 相机的 quality gate 可信度。

### 修复要求

在以下 3 个文件中实施（均为 Codex WIP）：

#### 1. `pipeline/synthetic_village/reciprocal_route_production.py`

在 `ReciprocalProductionFrameRequest` 中添加 `engine_script_sha256: Sha256`
字段，并在 `production_render_id` 计算中消费它（engine SHA 变化 → render_id
变化）：

```python
class ReciprocalProductionFrameRequest(FrozenModel):
    ...
    renderer_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # 新增
    ...
```

在 `build_reciprocal_production_frame_request` 中绑定 engine SHA：

```python
engine_script = (
    repo_root / "scripts/blender/render_synthetic_village.py"
).resolve(strict=True)
render_request = build_reciprocal_production_frame_request(
    ...
    renderer_script_sha256=_sha256_file(renderer_script),
    engine_script_sha256=_sha256_file(engine_script),  # 新增
    ...
)
```

#### 2. `scripts/blender/render_reciprocal_route_production.py`

在 `_validate_reciprocal_boundary` 或 `_load_engine` 中验证 engine SHA：

```python
def _load_engine(request):
    engine_path = Path(__file__).with_name("render_synthetic_village.py")
    actual_sha = _sha256_file(engine_path)
    if actual_sha != request["engine_script_sha256"]:
        raise RuntimeRenderError(
            "engine script digest does not match request"
        )
    spec = importlib.util.spec_from_file_location(...)
    ...
```

#### 3. 对应测试

新增 TDD 覆盖：
- engine SHA 不匹配 → 拒绝
- engine SHA 变化 → render_id 变化
- engine SHA 正确 → 通过

### 验收

- `_production_layer_counts` 的可信度不再仅依赖 wrapper SHA，也依赖 engine SHA
- reciprocal render request 的 `engine_script_sha256` 进入 `production_render_id`
- TDD 锁定 engine SHA 绑定

---

## P1 — 剩余 5 role 相机接入

### 背景

Phase 4.4 已交付 `build_ground_route_replacement_candidate` +
`materialize_reciprocal_role_candidate`，为 `camera-ground-route-010` 和
`camera-ground-route-039` 构造 replacement candidate。

`camera-ground-route-011` 已由 Codex 作为 canary 跑通完整链路
（FEEDBACK-HANDOFF-CODEX-009），证明 caller plumbing 可用。

### 需接入的 5 个 role

| role_module_id | role_index | camera_id | 状态 |
|---|---|---|---|
| `bridge-deck-crossing` | 2 | `camera-reciprocal-role-002` | 待接入 |
| `watermill-tailrace` | 3 | `camera-reciprocal-role-003` | 待接入 |
| `covered-gallery-underpass` | 4 | `camera-reciprocal-role-004` | 待接入 |
| `forest-orchard-boundary` | 5 | `camera-reciprocal-role-005` | 待接入 |
| `lower-valley-uphill` | 6 | `camera-reciprocal-role-006` | 待接入 |

注：`central-courtyard-downhill`（role 1）已由 `camera-ground-route-011` canary
覆盖（虽然 011 是原有 ground-route 相机，不是 reciprocal-role 相机；Codex 需
决定是否额外跑 `camera-reciprocal-role-001`）。

### caller 接入步骤（每个 role）

对每个 role 执行以下 5 步（来自
`FEEDBACK-HANDOFF-OPUS-009-phase4.4-camera-materialization.md` §Codex 后续
caller 接入清单）：

1. **caller 选择 module + node**：根据 role 的 `topology_ref` 选择最接近的
   reciprocal-route module 和该 module 附近的 ground-level `WalkableNode`
2. **caller 计算 look_at_m**：从 module 的 route 方向 + 25 m lookahead 计算
   `look_at_m`
3. **caller 读取 probe clearance**：从 Phase 4.3 probe report 读取该 module 的
   `clearance_min_m`，作为 `probe_clearance_min_m` 传入
4. **caller 构造 + 物化**：
   ```python
   candidate = build_ground_route_replacement_candidate(
       obstructed_camera_id="camera-ground-route-010",  # 或 039
       role_module_id="bridge-deck-crossing",
       topology_ref="path-network-001",
       bound_walkable_node=binding,
       look_at_m=look_at,
       bound_production_plan_sha256=plan_sha,
       bound_camera_registry_sha256=registry_sha,
       probe_clearance_min_m=probe_clearance,
       disclosure="...",
   )
   pose = materialize_reciprocal_role_candidate(
       candidate,
       target_group_id="ground-route",
       target_sequence_index=10,  # 或 39
       target_camera_id="camera-ground-route-010",
   )
   ```
5. **caller 替换 + 验证**：用物化出的 pose 替换 180-camera plan 中的
   obstructed pose，重算 plan SHA + registry SHA，跑 fresh preflight + 六层 +
   post-render v2

### 验收

- 每个 role 的 fresh preflight + 六层 + post-render v2 全部通过
- 5 个 role 的 RGB / depth / normal / instance / semantic / camera metadata
  artifact SHA + size 已记录
- `req-5-pose-quality-fail-closed` 状态可在 180-camera 全量通过后由 Opus 更新

---

## 路径所有权

### Codex 修改（本交办）

- `pipeline/synthetic_village/reciprocal_route_production.py`（P0）
- `scripts/blender/render_reciprocal_route_production.py`（P0）
- `pipeline/synthetic_village/local_production_runner.py`（P1 caller）
- `scripts/synthetic_village.py`（P1 caller）
- 对应 Codex tests

### Opus 不修改

Opus 暂不修改上述文件。P0 修复后 Opus 可回写审计确认。

### Opus 可后续推进

- `req-5-pose-quality-fail-closed` 状态更新（等 180-camera 全量通过六层门后）
- `undelivered_requirements` 更新（等真实 v2 报告产出后）

---

## 当前机器证据基线

| 身份 | 值 |
|---|---|
| reciprocal build ID | `509919f245932dacd950b7bb95c16638983c4da028ecced5361e3c9da2358a4e` |
| reciprocal build report SHA | `635ecdbdf3bf38e11a8f2df2e30ad7e0aeebac569fa7cbfdab7485073c772e78` |
| reciprocal `.blend` SHA | `e6b81c02d271952f4454f1a24a4731726f8e941c963ea92e5dca48ae30676d4c` |
| reciprocal plan SHA | `84163656de6a4eed9b3f91f0b9ca4e661912c6e6755d06d8aefdd8d3a01a3847` |
| exact roots | `218` (`175 + 43`) |
| canary camera | `camera-ground-route-011` |
| canary render ID | `b1d62574fd9a8c66399091791a67dce32a4bd97040ecc041d8c90c6e5a9ed82b` |
| canary result | preflight pass + six-layer pass + post-render v2 8/8 pass |
| probe report SHA | `08e1bcf1bfb0d1724cf374c8828de0e7ddb651af0ff6ac0c712693ffcfd2d3a5` |
| probe overall_passed | `True`（Phase 4.3 amendment 后） |

---

## 优先级

- **P0**（engine SHA 绑定）：MEDIUM fail-open 修复，影响 reciprocal 路径 quality
  gate 可信度。建议在下一个 218-root 相机渲染前完成。
- **P1**（5 role 相机接入）：不阻塞 Opus，但阻塞 `req-5` 状态更新和 180-camera
  全量交付。可在 P0 完成后逐 role 推进。
