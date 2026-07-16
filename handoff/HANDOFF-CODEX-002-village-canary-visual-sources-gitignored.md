# HANDOFF-CODEX-002 · synthetic_village canary 依赖的 visual-sources 资源被 gitignore，CI 全红

> 发起：Opus（架构/管线 lane）→ 交办：Codex（synthetic_village lane）
> 日期：2026-07-16
> 触发：`01f68ff` 推上 origin 后，CI 矩阵 `test` job 在 **ubuntu + windows 全红**。我按用户
> 「确认一下」查 CI 定位到根因——**唯一红点是 codex 刚推的 village canary，与我 lane / crash_recovery 无关**。

## 现象

CI run **29463949300**（push `01f68ff feat: render resumable village data layers`）：

| job | 结果 | 失败 |
|---|---|---|
| repro-assets (ubuntu/windows) | ✅ | — |
| test (ubuntu py3.11) | ❌ | **31 failed** / 530 passed，全部 `tests/test_synthetic_village_canary.py` |
| test (ubuntu py3.13) | ❌ | 同上 |
| test (windows py3.11) | ❌ | **28 failed** / 587 passed，全部 `tests/test_synthetic_village_canary.py` |

报错统一为：

```
FileNotFoundError: [Errno 2] No such file or directory:
  '.../.nantai-studio/synthetic-village/hybrid-v3/visual-sources/visual-sources.json'
pipeline.synthetic_village.visual_sources.VisualSourceError: cannot inspect JSON input: visual-sources.json
```

## 根因（已坐实）

- `tests/test_synthetic_village_canary.py:52`
  `VISUAL_PACK_ROOT = ROOT / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"`
  `:569` `load_visual_source_manifest(VISUAL_PACK_ROOT / "visual-sources.json")`
  —— **无条件直接读**，无 skip guard、无缺失时生成兜底。
- 该文件**本机存在**（`visual-sources.json`，9504 字节，你本地生成过），但位于
  **`.nantai-studio/` —— 被 gitignore**（你自己的 `2c1a084 chore: protect private studio runtime`）。
  `git ls-files` 确认：git 里 **0 个** `visual-sources.json` 被跟踪。
- 于是：**本机绿（文件在）↔ CI 红（checkout 里没有）**。属「保护了私有运行时目录，但测试又从该目录读 fixture」的自伤。

## 请你（Codex）选一种修法（属你 lane，我不替你猜意图）

这个 visual-source pack 到底是「**提交进仓的测试 fixture**」还是「**运行时生成的私有产物**」，只有你清楚。据此：

1. **若是测试 fixture**：把它放到**受版本控制的**测试资源目录（如 `tests/fixtures/synthetic-village/…`
   或 `handoff/deliverables/…`），canary 从那里读；或对该具体路径 `git add -f` 破 ignore 并
   在 `.gitignore` 用 `!` 例外。让 CI checkout 里有它。
2. **若是运行时生成物**：canary 在读之前**先确保存在**——调用你的 `scripts/setup_synthetic_tools.py` /
   `synthetic_village` 生成步骤把 pack 建出来（CI 里加一步），或测试内 fixture 生成到 `tmp_path`。
3. **若 CI 本就不该跑它**：像 blender runtime 测试那样加门
   `pytest.mark.skipif(not VISUAL_PACK.exists(), reason=...)` 或环境门，让它在 CI 自 skip
   （但这会降低覆盖，最好配合 1/2 让 CI 真能跑）。

**注意别把 `.nantai-studio` 整个解 ignore**——里面有你 `2c1a084` 想保护的私有运行时。只针对
canary 需要的那份 pack 处理。

## 我做了/没做

- **没碰** `test_synthetic_village_canary.py` / `pipeline/synthetic_village/*` / `.gitignore`
  —— 你的 lane、且你在活跃开发，避免打架。
- 我 lane 全绿：`repro-assets` 双平台过；video/sequential-matcher/coordinate 修复均已在 origin。
- **顺带确认**：crash_recovery/writer_lock 在 CI 干净 Windows runner 上**已通过**，
  HANDOFF-CODEX-001 关闭。

## 复现 / 验证命令

```bash
gh run view 29463949300                                   # 看 job 矩阵
gh run view --job 87513036275 --log-failed | grep -m3 FileNotFoundError   # ubuntu 根因
git check-ignore .nantai-studio                          # 证实被 ignore
git ls-files '.nantai-studio/**/visual-sources.json'     # 空 = 未跟踪
```

修好后：`git push` 触发新 CI；`test` job 应转全绿（crash_recovery 已证 CI 安全）。
