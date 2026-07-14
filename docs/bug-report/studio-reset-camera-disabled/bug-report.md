# Studio 复位相机在 Viewer ready 后仍禁用

## Bug 诊断胶囊

| 栏位 | 内容 |
|---|---|
| **1. 现象** | Viewer iframe 已完成 ready/Spark capability 握手，图层与 LOD 控件可用，但“复位相机”仍是 disabled。期望支持 `resetCamera` 时自动解锁。 |
| **2. 证据** | 2026-07-14 在 `http://127.0.0.1:8770/web/studio/` 的浏览器 DOM 快照中，Viewer 为 `full-3dgs`，按钮仍 disabled；Viewer capability 明确包含 `resetCamera`。 |
| **3. 根因** | `setupViewerBridge()` 只遍历 `[data-viewer-command]` 解锁控件；layer/LOD 有该属性，`#reset-camera` 没有，虽然 click handler 已存在，却永远保持 HTML 初始 disabled。 |
| **4. 诊断策略** | 对照可工作的 layer/LOD 控件，从 capability → selector → DOM attribute → click handler 顺向追踪。 |
| **5. 超时策略** | 若补齐 attribute 后仍 disabled，转查 ready 消息时序和 `bridge.supports()`，不修改 Viewer capability 声明。 |
| **6. 预警策略** | 若单元合约绿但浏览器仍红，说明静态标记不是唯一根因，必须回到实际 DOM 状态与消息事件。 |
| **7. 用户可见交互修正** | Viewer ready 后“复位相机”可点击；degraded/unsupported 时仍保持禁用并显示原因。 |
| **8. 验收** | 静态 DOM 合约测试先红后绿；浏览器 reload 后按钮 enabled，点击命令成功；LOD 与 layer bridge 同步验证；Studio 30 tests 通过。 |

## 五件套

### 1. 报告人

Codex 在最终 Studio 浏览器验收中发现。

### 2. 复现步骤

1. 启动 `make serve` 并打开 Studio。
2. 等待 Viewer 显示 `full-3dgs`。
3. 观察图层/LOD 已解锁，但“复位相机”仍 disabled。

### 3. 根因分析

按钮 click handler 和 Viewer handler 都存在；断点仅在 Studio capability selector。静态 HTML 漏标
`data-viewer-command="resetCamera"`，所以 ready 回调永远不会更新这个按钮。

### 4. 修复方案

为按钮补齐与其他 bridge 控件相同的 capability 标记，不放宽 `supports()`，保持 fail-closed。

### 5. 验证方式

- `web/studio/index-contract.test.mjs`
- Studio 浏览器 DOM 快照与实际点击
- `node --test web/studio/*.test.mjs`

最终证据：Spark ready 后按钮从 disabled 变为 enabled；浏览器点击未报错，LOD0 与重建层隐藏/
恢复仍正常，说明 capability selector 与 bridge handler 同时生效。
