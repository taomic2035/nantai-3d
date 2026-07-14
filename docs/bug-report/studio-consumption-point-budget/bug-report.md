# Studio 素材消费点数可超过 live chunk

## Bug 诊断胶囊

| 栏位 | 内容 |
|---|---|
| **1. 现象** | 期望素材的消费证据不超过对应 live chunk 的实际顶点预算；实际 1 点 chunk 可用一条 90 点消费行获得 `consumed=true / trust=verified`。 |
| **2. 证据** | 最终只读 review 在 `10d9f99` 复现：chunk header/manifest 都为 1，asset row `point_count=90`，Studio 仍把素材标为已消费。 |
| **3. 根因** | `_asset_snapshot()` 只把通过 PLY/声明点数验证的 chunk 降为 ID 集合，随后仅检查消费行点数为正，丢失了 live point count，无法判断单行或同 chunk 汇总是否物理可容纳。 |
| **4. 诊断策略** | 从 real world renderer 的 consumption report 反向验证语义：每行代表实际写入 chunk 的资产点，同 chunk 所有正点数行之和必须不超过已解析的 live vertex count。 |
| **5. 超时策略** | 若真实 25-chunk manifest 不满足该不变量，先检查 renderer 的 point_count 定义，不放宽 Studio gate 或伪造容差。 |
| **6. 预警策略** | 只拦单行、不拦汇总，或只按当前 asset 过滤汇总，都能被拆行/未知 asset 绕过；必须按 chunk 对全部有效正点数声明求和。 |
| **7. 用户可见交互修正** | 物理不可能或陈旧的 consumption report 不再让素材卡显示“世界已消费”，Assets 步骤降级为 blocked/proxy。 |
| **8. 验收** | 1-vs-90 单行与两条 1 点行汇总超过 1 点 chunk 的回归先红后绿；真实 25 chunks / 217 rows / 11 assets 仍全部通过。 |

## 五件套

### 1. 报告人

最终整分支只读 reviewer。

### 2. 复现步骤

1. 创建结构与数值均合法、live vertex count 为 1 的 simple PLY chunk。
2. world manifest 的 chunk `point_count` 同样声明 1。
3. 写一条其他字段与 registry 均匹配、但 `point_count=90` 的 asset consumption row。
4. 修复前 `build_project_snapshot()` 返回该素材 `consumed=true`。

### 3. 根因分析

chunk 入口已经验证 payload 与声明点数一致，但 reducer 只保留 `valid_chunk_ids`。消费匹配阶段
无法再访问 live count，因此任何正整数都被当成可信贡献量。

### 4. 修复方案

保留 `chunk_id -> live_point_count`，对所有指向有效 chunk 的正整数消费行按 chunk 求和；单行
或汇总超过 live count 时，该 chunk 不再供应任何消费证据。行级 version/hash/renderer gate 保持不变。

### 5. 验证方式

- 1 点 chunk / 90 点单行回归
- 1 点 chunk / 两条 1 点行汇总回归
- Studio/Python 全量测试
- 真实 world 快照仍为 11/11 consumed
- Ruff、`git diff --check`、`make verify`

修复后实测：两类越界回归 `12 passed`；Studio server `58 tests`；Python `232 passed`、
Viewer `32 passed`、Studio `33 passed`；`make verify`、Ruff 与 `git diff --check` 通过；真实
25-chunk 快照仍为 `registered=11 / consumed=11 / blocked=0`。
