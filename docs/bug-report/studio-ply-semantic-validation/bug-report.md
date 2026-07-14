# Studio PLY 证据缺少数值与高斯语义校验

## Bug 诊断胶囊

| 栏位 | 内容 |
|---|---|
| **1. 现象** | 期望 Studio 只把可解析且语义合法的 full 3DGS、world chunk 与可替换素材标为可用；实际只要攻击者同步更新声明哈希，含 `NaN`、零四元数或断裂 SH 索引的 PLY 仍可进入 verified/consumed 状态。 |
| **2. 证据** | 只读审查在 `1f21378` 复现：full artifact 的 `x=NaN` 仍为 `v2-artifact-present`，chunk 的 `x=NaN` 仍可提供消费证据，asset 的 `x=NaN` 配套更新 registry/world SHA 后仍 validated。`_valid_ply_payload()` 只比对 header、顶点数与必需字段。 |
| **3. 根因** | Studio 证据边界把“PLY 结构可读”误当成“高斯载荷可消费”，没有复用 `GaussianScene.load_ply()` 已有的有限数值、完整 SH、正 scale 与单位四元数校验。 |
| **4. 诊断策略** | 对照 canonical Gaussian loader 与 Studio reducer 的三条调用链，分别用 full/chunk/asset 真实入口写失败测试；只在需要提供信任证据的入口启用语义校验，LOD 预览仍保持结构校验。 |
| **5. 超时策略** | 若 canonical loader 造成真实 25-chunk 快照明显变慢，保留同一测试契约，提取无复制的共享语义 validator；不退回结构校验。 |
| **6. 预警策略** | 若三次修复仍分别在 full/chunk/asset 暴露不一致，停止局部条件叠加，统一所有信任入口到单一 validator。 |
| **7. 用户可见交互修正** | 损坏或伪造的重建降级为 preview-only/missing；损坏 chunk 不再证明素材已消费；损坏素材显示 `payload-ply-invalid`。 |
| **8. 验收** | full 的 `NaN`、零四元数、断裂 SH，以及 chunk/asset 的 `NaN` 回归先红后绿；Studio 全套、Python 全套和真实项目快照性能检查通过。 |

## 五件套

### 1. 报告人

Codex 最终只读交叉审查发现。

### 2. 复现步骤

1. 生成一份结构合法的 full、chunk 或 asset PLY。
2. 将任一顶点数值改成 `NaN`，或破坏 3DGS 四元数/SH 索引。
3. 同步更新 manifest/registry 中对应的 SHA 与字节数。
4. 调用 `build_project_snapshot()`；修复前错误地返回可用或已消费状态。

### 3. 根因分析

`PlyData.read()` 只能证明容器和顶点表可解析。它不会拒绝浮点非有限值，也不会验证 3DGS 的
SH、scale 或 rotation 约束；Studio reducer 因而在 canonical Gaussian loader 之前错误建立信任。

### 4. 修复方案

让 full 3DGS、world chunk 和 asset payload 通过共享的 Gaussian 语义校验；拒绝不属于
simple/3DGS scalar 契约的 vertex list/object 属性，避免其中的非有限值绕过扫描。保留 LOD
proxy 仅需结构可读的兼容边界，并用三类入口测试固定 fail-closed 行为。

### 5. 验证方式

- `tests/test_studio_server.py` 的 full/chunk/asset 恶意载荷回归
- Studio 与 Python 全量测试
- 真实 25-chunk `build_project_snapshot()` 性能与结果检查
- Ruff、`git diff --check`、全量 `make verify`

修复提交前实测：新增语义边界回归 `12 passed`；`make test` 为 Python `230 passed`、Viewer
`32 passed`、Studio `33 passed`；Ruff 与 `git diff --check` 通过。真实项目快照约 `0.96s`，
保持 `v2-artifact-present`，素材 `registered=11 / consumed=11 / blocked=0`。
