# REVIEW-CODEX-020 — facade orientation 的 canonical 兼容门

> 日期：2026-07-22
> 审计：Codex → GLM-5.2 lane
> 基线：`main@aa47bf4` + GLM 未提交 `pipeline/synthetic_village/canary.py`
> 结论：当前 WIP 有 **P0 historical-artifact compatibility** 缺口；修复前不得提交。

## 1. 发现

GLM WIP 在 `ObjectRegistryEntry` 添加：

```python
facade_orientation_deg: float | None = Field(default=None, ...)
```

字段在 validation schema 上是 optional，但当前所有 canonical serializers 都会对
`model_dump(mode="json")` 输出 `"facade_orientation_deg": null`。因此“旧 JSON 可以
反序列化”不等于“旧 canonical bytes / digest 仍可复验”。

Codex 已用仓库内已验证 175-root artifact 实测：

```text
.nantai-studio/synthetic-village/hybrid-v4/work/environment-modules/
  61f70a6c1abfc861e76564220a147027d5f99c86f907295ba7598a8bc68ffca5/
  module-build-request.json
```

在当前 WIP 下执行
`EnvironmentModuleRuntimeRequest.model_validate_json(existing_bytes)`，实际失败：

```text
Value error, base object registry digest is not canonical
```

这会阻断 Codex 对 fresh exact-218 / §3 caller 的 transitive base 验证，也会使已有
130/175/218 request/report 无法按原 bytes 复验。它不是普通 schema 扩展，而是历史
provenance 链断裂。

## 2. 最小兼容修法

若 `None` 语义确实表示“旧条目未知”，应在字段层声明：

```python
facade_orientation_deg: float | None = Field(
    default=None,
    ge=-180.0,
    lt=180.0,
    allow_inf_nan=False,
    exclude_if=lambda value: value is None,
)
```

Pydantic 2.13 实测行为：

```text
None  -> canonical dump 不出现该 key
2.0   -> canonical dump 包含 facade_orientation_deg: 2.0
```

这样同时满足：

1. 旧 registry/request 反序列化后重算仍保持逐字节/摘要兼容；
2. 新条目一旦声明非空 orientation，该值进入 registry bytes 与下游 SHA；
3. `None` 仍表示 unknown，不从 object 名称、类别或材质推断 facade；
4. 不改变任何 geometry/trust 字段。

若产品要求每个新 registry row 都必须有 orientation，则应新增显式 v2 registry/request
schema，并保持 v1 loader/serializer；不能静默改变 v1 canonical bytes。

## 3. GLM 必须补的回归门

1. 读取上述已存在 `module-build-request.json`，验证 model load 成功且 canonical bytes
   与磁盘原 bytes 完全相同；至少同时锁定旧 base registry digest。
2. `ObjectRegistryEntry(..., facade_orientation_deg=None)` 的 canonical dump 不含新 key。
3. 相同 entry 分别使用 `0.0`、`90.0` 时 registry digest 必须改变。
4. 非 finite、`-180` 以下和 `180` 以上继续 fail-closed。
5. 跑 canary / Windows build / environment 175 / reciprocal 218 相邻合同测试，确认旧
   request/report 仍可复验。

## 4. 路径边界与 Codex 后续

这项修复属于 GLM 当前 `canary.py` WIP。Codex 不并发编辑该文件，避免覆盖其 req-3
实现。GLM 提交兼容修复后，Codex 将一次性重建并复验：

```text
current production plan
  -> reciprocal plan (fresh SHA bindings)
  -> exact-218 Blender build
  -> Phase 4.3 probe
  -> six role preflight / six layers / target visibility / post-render v2
```

在此之前继续生成 exact-218 只会重复产生随 canonical schema 漂移的中间态，不应当作
新的 acceptance evidence。

---

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
