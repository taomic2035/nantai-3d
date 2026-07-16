# HANDOFF-CODEX-001 · Studio 崩溃恢复/写锁测试的子进程 PID 间接问题

> 发起：Opus（架构/管线 lane）→ 交办：Codex（Studio lane）
> 日期：2026-07-15
> 触发：用户「CI 修好了，确认一下」→ 我在推 held 批次前做全量验证时发现。

> ## ✅ 已解决（2026-07-16，CI 实证）
> 批次推上 origin 后，CI 矩阵在**干净 Windows runner** 上跑过：
> `test_studio_crash_recovery.py` 与 `test_studio_writer_lock.py` **均未出现在失败列表** →
> **在 CI 通过**（run 29463949300：windows py3.11 = 28 failed / 587 passed，失败全部是
> 另一个文件 `test_synthetic_village_canary.py`，无 crash_recovery/writer_lock）。
> 印证本文诊断：本机 32 个失败是**这台开发机的 venv python trampoline PID 间接**，非代码 bug。
> **本条关闭。** 下方「建议：helper 测试改用真 PID」仍是可选的健壮性改进（让 Opus 本地也能跑绿），
> 不阻塞、不紧急。当前 CI 红另有其因，见 **HANDOFF-CODEX-002**。

## TL;DR

1. **你的 CI 修复（`90e1941`）确认有效** ✅ —— 我本地按 CI 的 Windows 步骤装了
   `.[dev,windows-jobs]`（pywin32-312），之前 CI 红的
   `test_studio_job_http`（23）+ `test_studio_publication`（2）**全部转绿**
   （29 passed / 2 skipped）。根因诊断（Windows CI 缺 pywin32 → 写模式降级只读 →
   写测试硬期望可写而失败）正确，`skipif(os.name != "nt")` guard + 新增
   `test_studio_ci_contract.py` 元测试是诚实的锁定，不是「skip 掉充绿」。

2. **但全量跑又暴露一批你 lane 的失败，你的 CI 修复没覆盖**：
   - `test_studio_crash_recovery.py` —— **31 failed**
   - `test_studio_writer_lock.py::test_writer_lock_is_released_when_the_owner_process_is_killed` —— **1 failed**

3. **根因 = 本机 venv 的 `python.exe` 是 trampoline（PID 间接），不是代码 bug。**
   证据见下。**大概率只在 Opus 这台开发机复现，GitHub 干净 Windows runner 上会过**，
   但 `crash_recovery` 从没在 CI 跑过（在未推提交里），需要你确认/加固。

## 根因证据

两个 helper 都这样起子进程：

```python
subprocess.Popen([sys.executable, str(HELPER), *args], ...)   # crash_recovery._start_helper
subprocess.Popen([sys.executable, str(HELPER), str(path), "writer"], ...)  # writer_lock._locked_child
```

测试随后用 `Popen.pid` 去追踪/kill 这个 worker。但在 Opus 这台机器上：

```
$ .venv/Scripts/python.exe -c "起子进程打印它自己的 os.getpid()，对比 Popen.pid"
Popen.pid       = 17820
子进程 os.getpid = 3952
一致?           = False        # ← Popen 拿到的是 trampoline 的 PID，真 worker 是另一个
```

崩溃恢复失败的断言也印证：

```
tests/test_studio_crash_recovery.py:314
>   assert signal["parent_pid"] == parent.pid
E   AssertionError: assert 6592 == 30596     # helper 自报 os.getpid()=6592 ≠ Popen.pid=30596
```

`.venv/Scripts/python.exe` 的 `realpath` 是它自己（非 symlink 重定向），但启动时仍会
再 spawn 真解释器（Windows venv 常见的 launcher/trampoline 行为）。于是
`Popen.pid` 指向 trampoline，kill 它不会终结真正持锁的 worker → 锁没释放 / 父子 PID 对不上。

## 为什么判定是本机特定、非 CI 问题

- **铁证**：`test_writer_lock_is_released_when_the_owner_process_is_killed` **已在 origin/main**
  （CI 跑过），而按 roadmap CI 此前只红在 job_http/publication —— 即这个测试**在 CI 是绿的**，
  只在 Opus 本地挂。同一个 `Popen.pid`-追踪机制，CI 绿 / 本地红 → 差异只能是运行环境
  （GitHub Windows runner 的 `sys.executable` 是 setup-python 装的标准 CPython，不 trampoline
  → `Popen.pid == worker pid` → 过）。
- `test_studio_crash_recovery.py` **不在 origin**（在未推提交 `a6e6294 fix: harden B1 crash
  and HTTP recovery` 里），**CI 从没跑过它**。它和上面同根因，所以「CI 会过」是**合理推断但未证**。

## 请你（Codex）处理

1. **确认 `crash_recovery` 在真实 CI Windows runner 上为绿** —— 它随 held 批次首次进 CI，
   是唯一未证的一环。绿则本条 handoff 关闭。
2. **（建议，提升健壮性）让 helper 测试不依赖 `Popen.pid`**：worker 启动后本就通过 stdout
   handshake 回传信息（`_read_json_line`），让它**把自己的 `os.getpid()` 一并回传**，测试用
   这个「真 PID」去 kill/断言，而不是 `Popen.pid`。这样对 python launcher/trampoline
   （venv 拷贝、py launcher、App Execution Alias 等）都稳，Opus 本地也能跑绿，
   不必依赖「CI 环境恰好不 trampoline」。

## 边界（我没碰的）

- 未改动 `test_studio_crash_recovery.py` / `test_studio_writer_lock.py` / Studio job kernel
  —— 你的 lane，且你有并发在写，避免冲突。
- 我只在**我 lane**修了一个连带回归：`tests/test_coordinate_contract.py` 的 `fake_run` mock
  没接受我 held 提交 `1d96585` 给 COLMAP 探测加的 `timeout=` → 推后会**全平台**红。
  已修并提交（`21880c3`，只碰该测试文件）。

## 复现命令（你的机器上，装了 windows-jobs extra 后）

```powershell
.venv\Scripts\python -m pip install -e ".[dev,windows-jobs]"
.venv\Scripts\python -m pytest tests/test_studio_crash_recovery.py tests/test_studio_writer_lock.py -q
# 快速验证 PID 间接是否也在你机器上:
.venv\Scripts\python -c "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import os;print(os.getpid())'],stdout=subprocess.PIPE,text=True); c=p.stdout.read().strip(); p.wait(); print('Popen.pid',p.pid,'child',c,'match',str(p.pid)==c)"
```

## 附：与 CI 门无关的一个 FYI（非本 handoff 范围）

`ruff check .` 会报 `handoff/deliverables/HANDOFF-002/scripts/generate.py:10 I001`
（import 未排序）。这是 GPT 的交付脚本，且 **CI lint 门是 `ruff check pipeline tests`
（`make.py lint`），不扫 `handoff/`** → 不挡 CI、不挡推送。留给 GPT 的 image2 lane 顺手
`ruff --fix` 即可，我未改动其文件。

---
*一句话给用户看的确认结论：你的旧 CI 红（job_http+publication）已被 codex 真正修好；
我推前全量验证又拦下两类新问题——我自己的 coordinate 回归（已修）、和一批 crash_recovery/
writer_lock 本机 PID 间接失败（codex lane，CI 大概率安全但 crash_recovery 需 CI 实证）。*
