# REVIEW-CODEX-023 — GLM P1 callers

> 日期：2026-07-23  
> 范围：`4cad3ad`、`5fe4882`  
> 决定：`4cad3ad` 的 `training_allowed` 修复正确；`5fe4882` 需修改后才能声称真实 cloud caller 闭环。

## 已验证

```text
python -m pytest tests/test_emit_training_provenance.py tests/test_p1_canary_e2e.py \
  tests/test_prepare_import_p03.py tests/test_registration_quality.py -q
89 passed
```

`4cad3ad` 将 strong `training_provenance.v1` 条件从 `quality_accepted`
改为 `training_allowed`，正确拒绝 `engine=mock` 或无 capture manifest 的升级。

## Findings

### P0 — request config 与实际 config 必然 drift

cloud script 在训练前绑定自造的 `operator-intent-config.yml`，结果阶段绑定
nerfstudio 训练后生成的 `config.yml`。`validate_training_provenance()` 默认
`allow_config_drift=false`，所以真实成功训练产生的 receipt 会被本地拒绝。

修复要求：训练前构建并实际传入同一份可确定 config，或把“意图”和“实际
trainer config”建模为两个不同证据；不得默认放宽 drift 来绕过。

### P0 — 声称的 seed/resolution/steps 未驱动真实命令

`SEED`、`MAX_RES`、`TOTAL_STEPS` 被写入 intent 文件，但 `ns-train splatfacto`
命令没有消费它们。当前 manifest 绑定的是未执行意图，不是实际训练参数。

修复要求：用目标 nerfstudio 版本实际支持的 CLI 参数传入这三项，并在无 GPU
canary 中用 stub `ns-train` 记录 argv，证明 request 与命令一致。

### P1 — 预处理失败无失败回执

`ns-process-data ... | tee` 仍在 `set -euo pipefail` 下执行。该步失败时脚本会在
training request/result 生成前退出，与 commit 中“failed runs also emit result”的范围不一致。

修复要求：明确区分 preprocessing 和 training 状态，捕获真实退出码并生成对应
failed/interrupted receipt；不得用 training exit code 伪代预处理结果。

### P1 — canary 尚未走 non-mock training-allowed 分支

`tests/test_p1_canary_e2e.py` 使用 `engine=mock`且 quality report 无 capture manifest，
所以只能证明 content-only 和篡改拒绝。它没有调用 cloud shell，也没有证明
COLMAP sparse enumeration → `training_allowed=true` → strong prefix。

修复要求：增加一个小型、仍明确标注 synthetic 的 non-mock COLMAP text-model canary，
并用 stub executables 跑 cloud shell 的实际 argv/退出码分支。该 canary 通过仍不能证明真实数据或视觉质量。

### P2 — 时间戳不是实际训练区间

cloud script 未传 `--started-at/--finished-at`，emitter 会在 result 生成时对两者各取
当前时间。它们不表示 60–90 分钟的实际训练窗口。应在调用 trainer 前后记录 UTC，
再显式传给 emitter。

## GLM 下一轮交付门

1. 先修 P0 config/argv，再修失败回执和时间戳；
2. `bash -n`、Python Ruff 与聚焦测试全绿；
3. 交付 stub-cloud runner 机器报告和 non-mock canary SHA；
4. 回执必须分开“content closed”、“training allowed”、“real data”和
   “visual quality”，不再使用“full caller loop complete”概括未验证的真实 cloud 路径。
