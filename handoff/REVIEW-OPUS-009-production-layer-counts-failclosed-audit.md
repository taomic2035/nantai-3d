# REVIEW-OPUS-009 — _production_layer_counts fail-closed 对抗性审计

> 日期：2026-07-22
> 发起：Opus lane (GLM-5.2 临时接替)
> 审计对象：`scripts/blender/render_synthetic_village.py::_production_layer_counts`（lines 1670-1834）
> + `scripts/blender/render_reciprocal_route_production.py` 引擎复用路径
> 对应：FEEDBACK-HANDOFF-OPUS-006 §1（renderer script v2 statistics 桥接，已被 Codex 交付）
> 方法：逐环节对抗性审计，模拟攻击者尝试绕过每个 fail-closed 门
> 结论：**1 MEDIUM fail-open（reciprocal 路径引擎 SHA 未验证）+ 6 环节 PASS**

## 审计范围

| 组件 | 文件 | 行号 | 类型 |
|---|---|---|---|
| `_production_layer_counts` | `render_synthetic_village.py` | 1670-1834 | 逐像素计数核心函数 |
| policy 校验 | `render_synthetic_village.py` | 1691-1716 | 4 key + NaN/Inf/bool 拒绝 |
| semantic_registry 校验 | `render_synthetic_village.py` | 1717-1729 | unique ID + policy 引用 |
| object_registry 校验 | `render_synthetic_village.py` | 1730-1747 | instance_id unique/正/semantic 绑定 |
| 逐像素主循环 | `render_synthetic_village.py` | 1767-1817 | depth/normal/instance/semantic 计数 |
| dominant instance | `render_synthetic_village.py` | 1819-1833 | count 最大 + ID 最小 tie-breaker |
| layer_statistics 写入 | `render_synthetic_village.py` | 2003-2088 | dict → report → content_sha256 |
| reciprocal 引擎复用 | `render_reciprocal_route_production.py` | 134-144, 205-210 | importlib 加载 + self-SHA 验证 |

## 审计环节（7 项）

### 环节 1 — 输入缓冲长度校验 ✅ PASS

**验证**（lines 1682-1690）：
```python
if not (
    len(depth) == PIXELS
    and len(normals) == PIXELS * 3
    and len(instances) == PIXELS
    and len(semantics) == PIXELS
):
    raise RuntimeRenderError("production layer buffers do not share one pixel grid")
```

四个缓冲长度严格匹配 `PIXELS` / `PIXELS * 3`。如果任何一个缓冲长度不匹配，拒绝执行。✅

### 环节 2 — policy 参数校验 ✅ PASS

**验证**（lines 1691-1716）：

| 字段 | 校验 | NaN/Inf/bool 防护 |
|---|---|---|
| `near_depth_m` | int/float, 非 bool, finite, > 0 | ✅ `math.isfinite()` 前置 |
| `upper_region_end_row_exclusive` | int, 非 bool, 1 ≤ x ≤ HEIGHT | ✅ bool 拒绝 |
| `ground_semantic_ids` | list, 非空, 元素 int, sorted(set(...)) 一致 | ✅ 类型检查 |
| `sky_semantic_id` | int | ✅ `type(...) is not int` |

**对抗性测试**：
- `near_depth_m=NaN` → `not math.isfinite(...)` → 拒绝 ✅
- `near_depth_m=inf` → `not math.isfinite(...)` → 拒绝 ✅
- `near_depth_m=True`（bool） → `isinstance(..., bool)` → 拒绝 ✅
- `near_depth_m=-1.0` → `<= 0.0` → 拒绝 ✅
- `upper_region_end_row_exclusive=0` → `not 1 <= ...` → 拒绝 ✅
- `ground_semantic_ids=[1, 1]` → `!= sorted(set(...))` → 拒绝 ✅
- `set(policy) != expected_policy_keys` → 拒绝额外/缺失 key ✅

**结论**：policy 校验非常严格，NaN/Inf/bool 全部被拒绝。无 fail-open。

### 环节 3 — semantic_registry + object_registry 校验 ✅ PASS

**semantic_registry**（lines 1717-1729）：
- `len(semantic_ids) != len(semantic_registry)` → 拒绝重复 semantic_id ✅
- `policy["sky_semantic_id"] not in semantic_ids` → 拒绝未注册 ✅
- `not set(policy["ground_semantic_ids"]) <= semantic_ids` → 拒绝未注册 ✅

**object_registry**（lines 1730-1747）：
- `len(semantic_by_instance) != len(object_registry)` → 拒绝重复 instance_id ✅
- `None in semantic_by_instance` → 拒绝缺失 key ✅
- `type(instance_id) is not int` → 拒绝非 int ✅
- `instance_id <= 0` → 拒绝非正 ✅
- `semantic_id not in semantic_ids` → 拒绝未注册 ✅

**对抗性测试**：
- 非 dict 元素 → `isinstance(row, dict)` 过滤 → `len(...)` 不等 → 拒绝 ✅
- 缺失 `instance_id` key → `row.get("instance_id")` = None → `None in semantic_by_instance` → 拒绝 ✅

**结论**：registry 校验完整。

### 环节 4 — 逐像素计数主循环 ✅ PASS

**instance_id / semantic_id 校验**（lines 1775-1796）：
- `type(instance_id) is not int` → 拒绝 ✅
- `type(semantic_id) is not int` → 拒绝 ✅
- `semantic_id not in semantic_ids` → 拒绝 ✅
- `instance_id > 0` 且 `instance_id not in semantic_by_instance` → 拒绝 ✅
- `instance_id > 0` 且 `semantic_by_instance[instance_id] != semantic_id` → 拒绝（instance/semantic 交叉校验）✅
- `instance_id < 0` → 拒绝 ✅
- `instance_id == 0` → 合法（"无 canonical instance"），不计入 registered_instance ✅

**depth 计数**（lines 1797-1804）：
- `math.isfinite(depth_value)` → NaN/Inf/-inf 全部排除 ✅
- `depth_value > 0.0` → 零深度和负深度排除 ✅
- `depth_value < near_depth_m` → near_depth 正确分类 ✅

**normal 计数**（lines 1805-1806）：
- `math.isfinite(normal_length)` → NaN/Inf 排除 ✅
- `abs(normal_length - 1.0) <= 0.001` → 非单位法向量排除 ✅

**sky / valid_semantic 计数**（lines 1807-1810）：
- 互斥：每个像素要么 sky 要么 valid_semantic ✅

**upper_ground 计数**（lines 1811-1817）：
- `pixel_index // WIDTH < upper_rows` → 正确的行号计算 ✅
- `< upper_rows` 与 `upper_region_end_row_exclusive` 语义一致（exclusive）✅

**结论**：逐像素计数完整，NaN/Inf/边界值全部正确处理。

### 环节 5 — dominant instance 选取 ✅ PASS

**验证**（lines 1819-1825）：
```python
def dominant(rows):
    if not rows:
        return None, 0
    return min(rows.items(), key=lambda row: (-row[1], row[0]))
```

- `rows` 为空 → `(None, 0)` ✅
- `-row[1]` 让 count 最大的排在最前 ✅
- `row[0]` 让 instance_id 最小的排在最前（tie-breaker）✅

**对抗性测试**：
- `{5: 100, 3: 100}` → 选 `(3, 100)`（count 相同时选最小 ID）✅
- `{5: 50, 3: 100}` → 选 `(3, 100)`（选 count 最大）✅

**结论**：dominant 选取逻辑正确。

### 环节 6 — layer_statistics 写入 + content_sha256 ✅ PASS

**验证**（lines 2003-2088）：
- `layer_statistics` dict 被写入 `report` ✅
- `content_sha256` 在 `layer_statistics` 写入后计算 → layer_statistics 内容进入 SHA ✅
- 如果 report 文件被篡改，`content_sha256` 不匹配 ✅

**设计说明**：`content_sha256` 保证 report 文件完整性。layer_statistics 的可信度来自 Blender runtime 的 pinned script SHA（`renderer_script_sha256`），不是来自 `content_sha256`。如果 runtime script 被篡改，SHA 不匹配，request 被拒绝。

**结论**：写入路径完整。

### 环节 7 — reciprocal 路径引擎 SHA 未验证 ⚠️ MEDIUM

**审计点**：`render_reciprocal_route_production.py` 通过 `importlib` 加载 `render_synthetic_village.py` 作为引擎模块。reciprocal render request 绑定的是 wrapper script 的 SHA，**不是** engine script 的 SHA。

**验证**：

1. **wrapper self-SHA 验证**（lines 107-109, 205-210）：
```python
# _validate_reciprocal_boundary:
if _sha256_file(script_path) != request["renderer_script_sha256"]:
    raise RuntimeRenderError("renderer script digest does not match executing script")

# _validate_request:
_validate_reciprocal_boundary(request, scene=..., script_path=Path(__file__))
```
`script_path=Path(__file__)` → 验证的是 `render_reciprocal_route_production.py` 自身 SHA ✅

2. **engine 加载**（lines 134-144）：
```python
def _load_engine():
    path = Path(__file__).with_name("render_synthetic_village.py")
    spec = importlib.util.spec_from_file_location(...)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```
**不验证** `render_synthetic_village.py` 的 SHA ⚠️

3. **render request 绑定**（`reciprocal_route_production.py:1222-1230`）：
```python
renderer_script = (
    repo_root / "scripts/blender/render_reciprocal_route_production.py"
).resolve(strict=True)
render_request = build_reciprocal_production_frame_request(
    ...
    renderer_script_sha256=_sha256_file(renderer_script),
    ...
)
```
只绑定 wrapper SHA，**不绑定** engine SHA ⚠️

**攻击场景**：
1. 攻击者修改 `render_synthetic_village.py` 中的 `_production_layer_counts`，返回假 layer_statistics（如 `valid_depth_pixel_count = PIXELS`，让坏帧通过 quality gate）
2. 不修改 `render_reciprocal_route_production.py`（否则 SHA 不匹配）
3. reciprocal render request 验证 wrapper SHA → 通过（wrapper 未改）
4. Blender 执行 wrapper → importlib 加载被篡改的 engine → 假 layer_statistics 写入 frame-report
5. quality gate 使用假数据 → 坏帧通过

**对比**：local production render 路径直接用 `render_synthetic_village.py` 作为 renderer script，其 SHA 被绑定到 render request → 安全 ✅

**严重性**：MEDIUM — 需要文件系统访问权限，但在共享开发环境中可行。影响 reciprocal 路径所有 218-root 相机的 quality gate 可信度。

**修复方案**（由 Codex 实施，两个文件都是 Codex WIP）：
1. 在 `ReciprocalProductionFrameRequest` 中添加 `engine_script_sha256: Sha256` 字段
2. 在 `_load_engine()` 或 `_validate_reciprocal_boundary` 中验证 `render_synthetic_village.py` 的 SHA
3. 在 `build_reciprocal_production_frame_request` 中绑定 engine script SHA

## 修复汇总

| 级别 | 发现 | 修复 |
|---|---|---|
| **MEDIUM** | reciprocal 路径 engine script SHA 未验证 | Codex 需在 reciprocal render request 中绑定 `render_synthetic_village.py` SHA 并在 runtime 验证 |
| — | 6 环节 PASS | — |

## 设计优点（值得保留）

1. **逐像素计数**：不依赖后处理脚本，在 Blender runtime 进程内对已解码内存缓冲直接计数
2. **NaN/Inf 防护**：depth 和 normal 都用 `math.isfinite()` 前置检查
3. **bool 排除**：`isinstance(x, bool)` 检查防止 Python bool 伪装为 int
4. **instance/semantic 交叉校验**：每个 instance_id > 0 的像素必须与 object_registry 声明的 semantic_id 一致
5. **policy 严格校验**：4 key 精确匹配，NaN/Inf/bool/范围全拒绝
6. **dominant tie-breaker**：count 相同时选最小 instance_id，确定性
7. **互斥计数**：sky / valid_semantic 互斥，每个像素只计入一个

## 测试覆盖

Codex 已有端到端真实测量测试（`tests/test_synthetic_village_blender_runtime.py:504-563`）：
`test_runtime_embeds_measured_production_layer_statistics` — 真实渲染 camera-ground-route-034，验证 layer_statistics 的 13 个字段全部匹配。

## 提交内容

本审计不涉及代码修改（`render_synthetic_village.py` 和 `render_reciprocal_route_production.py` 都是 Codex WIP），仅交付审计文档：

```text
handoff/REVIEW-OPUS-009-production-layer-counts-failclosed-audit.md
```
