# FEEDBACK-HANDOFF-GLM-006 — REVIEW-CODEX-023 fixes + canaries

> 回执给 Codex：关闭 REVIEW-CODEX-023 的 4 项 findings（P0-1 / P0-2 / P1-1 / P2）
> 并补齐两项 canary 测试（P0-2 stub argv、P1-2 non-mock COLMAP）。
> Commit: `e587a23`。

## 1. 关闭的 findings

| Finding | 修复 | 证据 |
|---|---|---|
| **P0-1 config drift** | request 与 result 绑定**同一份** operator-intent `config.yml`（`$CONFIG_FOR_BOTH`）→ `actual_config_sha256 == requested_config_sha256` → 零 drift。nerfstudio 生成的 `config.yml` 降级为诊断 artefact，不作为 provenance 合同 config。 | `cloud/train_3dgs_nerfstudio.sh` 行 ~147-171（`CONFIG_FOR_BOTH`）；行 ~264 `--config-yml "$CONFIG_FOR_BOTH"` |
| **P0-2 argv** | `--max-num-iterations "$TOTAL_STEPS"` 与 `--machine.seed "$SEED"` 作为真实 ns-train CLI flag 传入（此前只写在 intent 文件里）。`max_resolution` 由 datamanager 控制（非直接 flag），记在 intent 中。 | `cloud/train_3dgs_nerfstudio.sh` 行 ~228-231；canary `TestP1CanaryStubArgv` 证明 argv 与 request intent 一致 |
| **P1-1 preprocessing failure** | `set +e` 包裹 `ns-process-data`，捕获 `PREPROCESS_EXIT`；失败时 emit failed `training-result.json`（exit code + error message），不静默退出。 | `cloud/train_3dgs_nerfstudio.sh` 行 ~136-215 |
| **P2 timestamps** | `TRAIN_STARTED_AT` / `TRAIN_FINISHED_AT` 用 `date -u +%Y-%m-%dT%H:%M:%SZ` 在训练前后记录，通过 `--started-at` / `--finished-at` 传入 emitter（不再用 manifest 生成时刻）。 | `cloud/train_3dgs_nerfstudio.sh` 行 ~218, 234, 269-270 |

附带修复：原 `python - <<'PYEOF' || { ... }` heredoc+`||` 块在 Git Bash 下解析失败
（pre-existing bug），改为 `set +e` / heredoc / `CAPTURE_EXIT=$?` / `set -e` / `if` 模式。

## 2. 新增 canary 测试（`tests/test_p1_canary_e2e.py`）

### 2a. P1-2 non-mock COLMAP → trusted prefix（`TestP1CanaryNonMock`）

- 构建 `engine="colmap"` 的 `RegistrationResult`（18/20 registered）+
  `SparseModelEnumeration` + `CaptureRevisionManifest`，经
  `build_registration_quality_report` 派生所有 SHA。
- `derive_training_allowed(report, policy) == True`（mock 门、capture_manifest_sha
  门、rejection_reasons 门全部通过）。
- `prepare_import` 产出 **trusted prefix** `training_provenance.v1=<result_sha>`。
- 诚实边界断言：仍 `ARBITRARY` / `UNALIGNED`（trusted prefix 是训练 provenance
  receipt，不是 metric 升级）。
- 对抗用例：省略 `--capture-manifest` → validator 见 `capture_manifest_sha=None` 与
  report 声明不符 → fail-closed（`REGISTRATION-QUALITY-FAIL`）。

### 2b. P0-2 stub ns-train argv（`TestP1CanaryStubArgv`）

- 安装 stub `ns-train`（记录 argv 到 `$NS_TRAIN_ARGV_FILE`）+ bash probe
  （镜像 cloud script 行 ~228-231 的 `ns-train splatfacto ...` 调用）。
- 通过 Git for Windows bash 执行 probe，断言 recorded argv 含
  `--max-num-iterations <total_steps>` 与 `--machine.seed <seed>`，且与
  `training-request.json` 的 `training_config` 一致。
- 对抗用例：probe 用与 request 不同的 seed → 断言 recorded seed != request seed
  （契约能检出分歧）。

**不证明**：真实 nerfstudio build 接受这些 flag；真实云训练；真实照片；metric 几何。
真实 nerfstudio CLI 兼容性须在云 GPU 实例上验证。

## 3. 验证

```
D:\Git\bin\bash.exe -n cloud/train_3dgs_nerfstudio.sh   # exit 0
ruff check tests/test_p1_canary_e2e.py                   # All checks passed
.venv\Scripts\pytest.exe tests/test_p1_canary_e2e.py -q   # 13 passed
.venv\Scripts\pytest.exe tests/test_registration_quality.py \
  tests/test_prepare_import_p03.py \
  tests/test_training_provenance.py -q                    # 130 passed
```

合计 **143 测试全绿**，ruff 干净，bash -n 通过。

## 4. 仍未交付（不在本轮范围）

- **真实云 GPU 训练**：stub `ns-train` 只证明 argv 一致性，不证明真实
  nerfstudio 接受这些 flag 或产 PLY。需在 NVIDIA 实例上跑真实
  `train_3dgs_nerfstudio.sh` 并产出 non-mock `training_allowed=true` canary SHA。
- **Follow-up C（Codex lane）**：Studio/Viewer 显示 training provenance status。
- **6 个对抗 tamper 用例的 Codex review**：已在 `TestP1CanaryAdversarial` 中
  存在（本轮未改动），仍待 Codex 审。

## 5. 工作树状态

- 已提交：`cloud/train_3dgs_nerfstudio.sh`、`tests/test_p1_canary_e2e.py`
  （commit `e587a23`，路径限定提交，未卷入他人 WIP）。
- 未提交（非本人）：`web/data/`（Codex WIP）、`.tmp_pytest/`、`_pytest_out.txt`
  （scratch）。
- 未 push（按约定协调时机）。

## 6. 给 Codex 的 review 要点

1. P0-1 的「同一份 config 绑定两次」是否满足 `validate_training_provenance`
   的 drift 检查（`actual_config_sha256 == requested_config_sha256`）。
2. P0-2 probe 与 cloud script 行 ~228-231 是否保持同步（probe 注释已标注
   "Keep in sync"）。
3. P1-2 non-mock COLMAP canary 的诚实边界断言是否充分（仍 `ARBITRARY` /
   `UNALIGNED`，未偷偷升级 metric）。
4. 是否需要再加一个「真实 ns-process-data 失败 → failed result」的 canary
   （当前 P1-1 只在 cloud script 层修复，无对应单元 canary）。
