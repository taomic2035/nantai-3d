# GLM-017/018 — Real-scene status closure

Date: 2026-07-28
Owner: GLM-5.2 / Codex review
Status: IMPLEMENTED, local verification passed

## Decision

Production external-input intake and real-scene stage state remain separate
truth domains. The status command inspects only one source-bound runner
journal; it does not merge loose evidence paths, probe a remote host, train,
publish, or replace `validate_real_scene_acceptance`.

The public entry is:

```powershell
python scripts/real_scene.py status `
  --source <real-dataset-source.json> `
  --workspace <workspace> `
  --run-id <run-id>
```

It emits deterministic canonical JSON and returns:

- `0`: the five evidence stages completed and the role-aware authoritative
  acceptance decision allows the source;
- `2`: a valid blocked or incomplete snapshot;
- `1`: invalid source, receipt, artifact, path boundary, TOCTOU, or acceptance
  contradiction. In this case stdout is empty and stderr is fixed text.

## Trust boundary

The inspected chain is role-specific:

```text
fetch → sfm → train-production|train-preview → import → accept
```

Completed receipts are reopened with prerequisite and artifact verification.
Production import reopens its metric import receipt. Accept reopens the unique
bound `real-scene-acceptance-<sha>.json` and calls
`validate_real_scene_acceptance`; production checks
`production_release_allowed`, canary checks `canary_accepted`.

The snapshot never echoes `StageReceipt.reason` or local absolute paths.
Source, workspace, receipt and artifact traversal rejects symlinks, junctions
and Windows reparse points. Missing workspaces remain a valid all-missing
snapshot and are not created.

This status is not a Production release signature. Release construction and
downloaded-byte verification remain separate gates, and the five real-scene
external evidence gates are still required.

## Verification

- Focused runner/CLI: `50 passed, 2 skipped`
- Related real-scene/docs suite: `242 passed, 8 skipped`
- Ruff and `git diff --check`: passed

The skipped cases require local symlink privileges and run on capable CI
hosts.

## Next: GLM-019

Audit the existing `serve` semantic mismatch:

- `RealSceneRunner._all_stages()` currently includes `serve`;
- `RealScenePipelineOperations.execute()` currently returns blocked for it;
- starting a long-lived server is not naturally a durable completed stage.

Decide, before coding, whether `serve` becomes a non-journal launch action or
a bounded boot-probe receipt. Do not change release trust or claim real Viewer
QA from a boot probe.
