# Preview2 clean-tree verifier bootstrapping

Date: 2026-07-26
Reporter: Codex，Preview2 clean-room release gate
Status: fixed and regression-tested

## Bug 诊断胶囊

| 栏位 | 内容 |
|---|---|
| **1. 现象** | 在未安装依赖的 Preview2 提取树执行 `python -S scripts/verify_preview_release.py .`，期望只用标准库校验，实际以 `ModuleNotFoundError: No module named 'pipeline'` 退出 1。改用 `python -m` 后，import 生成的 `pipeline/__pycache__` 又被 verifier 自己判为 unexpected。 |
| **2. 证据** | macOS arm64、Python 3.13、候选 source `e4bab89`；archive SHA `138043dd…7ae10` 已先通过 archive verifier。两个错误在新目录 `clean-room-e4bab89` 稳定复现。 |
| **3. 根因** | 直接运行 `scripts/*.py` 时 `sys.path[0]` 是 `scripts/`，开发机过去仅因 editable install 才能 import 根目录 `pipeline`。同时 tree verifier 把所有额外文件都当注入，未区分 receipt 已声明的 protected content 与安装必然生成的 `.venv`、`__pycache__`、项目 egg-info。 |
| **4. 诊断策略** | 用 `-S` 关闭 site-packages 复现入口边界；对照 receipt 的 `protected_roots` 和 `_all_release_files` 数据流；分别锁定 bootstrap 与 runtime-mutable allowlist。 |
| **5. 超时策略** | 若一次最小修复仍不能通过 pristine tree、post-install tree、injected source 三种测试，停止叠加例外并重新设计 receipt schema。 |
| **6. 预警策略** | 任意 protected root 内新增文件被放行、任意普通 `pipeline/*.py` 注入被放行、或 verifier 执行自身改变 package content，都说明方向错误。 |
| **7. 用户可见交互修正** | 用户可在安装前直接验证原始提取树；创建指南规定的 `.venv` 并 editable install 后，Studio 仍显示 package verified，而不是把正常安装痕迹误报为篡改。 |
| **8. 验收** | 新增无 site-packages 的 subprocess RED、runtime-mutable 与 injected source 对抗测试；跑 Preview release/Studio 专项；从新 archive 重做 pre-install verify、install、post-install verify、Studio API 和 corruption drill。 |

## 修复方案与取舍

Verifier 入口只把自身不可变的父目录加入 import path，并禁写 bytecode；不依赖用户
设置 `PYTHONPATH`。Tree verifier 只忽略指南产生的三个明确目录类别：

- 根目录 `.venv/`；
- 根目录 `nantai_infinite_village.egg-info/`；
- 任意非 protected 路径下的 `__pycache__/`。

它仍逐字节校验 receipt 中的全部 artifact，并继续拒绝 protected root 新文件、
普通源码注入、symlink、缺失或 SHA drift。没有改 scene trust 或 package content ID
算法。
