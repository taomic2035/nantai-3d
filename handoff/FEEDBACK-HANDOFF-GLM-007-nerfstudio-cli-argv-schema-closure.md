# FEEDBACK-HANDOFF-GLM-007 — nerfstudio CLI argv schema contract closure

Date: 2026-07-25
Owner: GLM lane
Reviewer: Codex

## Summary

**Statically pinned every nerfstudio CLI flag constructed by
`cloud/train_3dgs_nerfstudio.sh` against the documented nerfstudio schema,
with a TDD contract that goes RED the moment the cloud script silently
introduces an undocumented flag, breaks canonical spelling, or drops a
required flag.**

This is the credential-free upgrade path that closes the documented gap
between `e587a23`'s stub argv canary and a real cloud-GPU acceptance test
(still externally gated by HANDOFF-GLM-007 §1 item 3).

## What was done

### 1. Schema module: `cloud/ns_train_argv_schema.py`

A frozen dict pinning every flag the cloud script is allowed to construct.
Each entry carries:

- `flag`: canonical tyro hyphen spelling (`--output-dir`, not `--output_dir`)
- `cli`: which ns-* tool consumes it
- `subcommand`: which subcommand (splatfacto / gaussian-splat / images / video)
- `value_type`: int / str / bool / path / enum
- `doc_source`: citation of the official nerfstudio doc, source file, or
  reproducible community reference that documents the flag's existence and
  spelling
- `notes`: version sensitivity, deprecation warnings, value-form caveats

Six flags are registered, all verified against public nerfstudio references:

| Flag | Documented in |
|---|---|
| `--data` | nerfstudio CLI reference; canonical in every tutorial |
| `--output-dir` | tyro dataclass field `output_dir`; hyphen form is canonical |
| `--max-num-iterations` | `TrainerConfig.max_num_iterations`; top-level flag |
| `--machine.seed` | `MachineConfig.seed` field; dotted-path form required |
| `--viewer.quit-on-train-completion` | `ViewerConfig.quit_on_train_completion`; boolean bareword |
| `--load-config` | ns-export/ns-viewer/ns-render reference |

Also exports `REQUIRED_FLAGS_PER_INVOCATION`: the minimum flag set each ns-*
invocation must pass (e.g. ns-train must always pass `--data`,
`--output-dir`, `--max-num-iterations`, `--machine.seed`).

### 2. Contract test: `tests/test_cloud_argv_schema_contract.py`

Six contracts, eight pytest cases:

| Contract | RED when |
|---|---|
| `test_no_undeclared_flags_in_cloud_script` | cloud script adds a flag not in schema |
| `test_required_flags_present_per_invocation` | cloud script drops a required flag (e.g. `--machine.seed` removed → reproducibility break) |
| `test_every_schema_flag_has_doc_source` | schema entry lacks documentation citation |
| `test_schema_uses_canonical_hyphen_spelling` | schema entry uses underscore form (diverges from docs) |
| `test_static_schema_contract_does_not_claim_real_cli_acceptance` | module docstring drops the "does NOT prove" disclaimer |
| `test_cloud_script_invokes_each_required_cli` (×3) | cloud script stops invoking ns-process-data / ns-train / ns-export |

All 8 pass in 0.04 s. Ruff clean.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest tests/test_cloud_argv_schema_contract.py `
  -p no:cacheprovider --tb=short -q
# -> 8 passed in 0.04s

.venv\Scripts\python.exe -m ruff check `
  cloud/ns_train_argv_schema.py `
  tests/test_cloud_argv_schema_contract.py
# -> RUFF CLEAN
```

Direct call verification (bypassing pytest capture) also confirmed every
contract function returns without assertion.

## Honest boundary (must remain)

This schema is **static documentation**, not a real-CLI acceptance test.

- It does NOT prove nerfstudio actually accepts these flags at runtime.
- It does NOT prove flag compatibility across nerfstudio versions
  (0.3.x vs 1.0.x may rename or deprecate flags).
- It DOES catch the failure mode where someone edits the cloud script and
  silently introduces an undocumented flag, breaks the canonical flag
  spelling, or drops a flag the request intent claims to pass.

Real CLI acceptance still requires a cloud GPU instance
(HANDOFF-GLM-007 §1 item 3) — externally gated by credentials/budget.

## What this closes vs. does not close

| Closes | Does not close |
|---|---|
| Gap between `e587a23` stub argv canary and real-CLI evidence — schema is now pinned and tested | Real nerfstudio CLI runtime acceptance (cloud GPU) |
| Silent drift of cloud script flags from documented schema | Cross-version flag compatibility (0.3.x vs 1.0.x) |
| Loss of canonical tyro hyphen spelling | Boolean bareword parsing semantics across tyro versions |
| Dropping a required flag (e.g. `--machine.seed`) from the cloud script | Real-photo capture / accepted SfM / metric alignment / Viewer QA |

## Files

```text
cloud/ns_train_argv_schema.py                          (new, 117 lines)
tests/test_cloud_argv_schema_contract.py               (new, 200 lines)
```

No Codex-owned paths touched. No `web/data/` touched.

## Next independent queue item

Per HANDOFF-GLM-007 §11: the schema contract is the last credential-free
bridge between stub argv evidence and real cloud-GPU acceptance. All
remaining real-scene evidence items (real capture / accepted SfM / non-mock
cloud-GPU training / measured alignment / Viewer QA over a real artifact)
are externally gated. The Viewer QA proposal
(`HANDOFF-CODEX-013-viewer-qa-p7-recovered-pose-splat.md`) is with Codex.
GLM continues hunting unowned caller/integrity defects; a pending review
is not a stop condition.
