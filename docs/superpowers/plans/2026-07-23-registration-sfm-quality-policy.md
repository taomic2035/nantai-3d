# Registration SfM Quality Policy — TDD Plan

> Date: 2026-07-23
> Owner: GLM lane (HANDOFF-GLM-002 Task 2, design + TDD plan only)
> Status: Plan — awaiting Codex review before implementation
> Spec: `docs/superpowers/specs/2026-07-23-registration-sfm-quality-policy-design.md`

## Scope

This plan covers the **implementation phase** (not started until Codex reviews
the design). The implementation phase creates `pipeline/registration_quality.py`
and `tests/test_registration_quality.py` only — it does NOT modify
`pipeline/registration.py` or `pipeline/recon_schema.py`.

The COLMAP wrapper integration (modifying `colmap_register()` to emit a quality
report, replacing the `sparse/"0"` hardcode with model enumeration) is a
**separate follow-up plan** that depends on this module shipping first.

## Test file

`tests/test_registration_quality.py` — all tests in a single file, grouped by
phase.

## Phase 1: Red — schema existence and field validation

These tests fail because `pipeline/registration_quality` does not exist yet.

### 1.1 `test_policy_requires_all_thresholds`
- **Arrange:** attempt to construct `RegistrationQualityPolicy()` with no args.
- **Expect:** `ValidationError` (all fields required, no defaults).
- **Why:** prevents implicit thresholds; silence is not consent.

### 1.2 `test_policy_threshold_bounds`
- **Arrange:** construct `RegistrationQualityPolicy` with out-of-range values:
  `min_registered_ratio=1.5`, `min_session_coverage_ratio=-0.1`,
  `min_largest_connected_model_share=2.0`.
- **Expect:** `ValidationError` for each.
- **Why:** thresholds are clamped to valid domains.

### 1.3 `test_policy_is_frozen_and_forbids_extra`
- **Arrange:** construct a valid policy; attempt `policy.min_registered_count = 5`
  and attempt to pass `unknown_field=42`.
- **Expect:** `ValidationError` / `TypeError` on mutation; `ValidationError` on
  extra field.
- **Why:** policy immutability is a provenance-safety requirement.

### 1.4 `test_report_requires_all_binding_shas`
- **Arrange:** attempt `RegistrationQualityReport()` with no args.
- **Expect:** `ValidationError` (registration_json_sha256, policy_canonical_sha256
  required).
- **Why:** a report without content-addressed bindings is unvalidatable.

### 1.5 `test_report_sha_fields_must_be_64_hex`
- **Arrange:** construct a report with `registration_json_sha256="not-a-sha"`.
- **Expect:** `ValidationError`.
- **Why:** mirrors the `_require_64_hex_sha` fail-closed pattern.

## Phase 2: Red — policy canonical SHA

### 2.1 `test_policy_canonical_sha256_is_deterministic`
- **Arrange:** construct the same policy twice.
- **Expect:** identical `policy_canonical_sha256()`.
- **Why:** content-addressed identity must be reproducible.

### 2.2 `test_policy_sha_changes_when_thresholds_change`
- **Arrange:** construct two policies differing in one threshold.
- **Expect:** different SHAs.
- **Why:** any threshold change must alter the policy identity.

### 2.3 `test_policy_sha_uses_lf_and_sort_keys`
- **Arrange:** construct a policy; compare SHA against manually computed
  `sha256(model_dump_json(sort_keys=True, ensure_ascii=True).encode("utf-8"))`
  with LF newlines.
- **Expect:** exact match.
- **Why:** cross-OS byte reproducibility (same contract as `registration.json`).

## Phase 3: Red — sparse model enumeration

### 3.1 `test_enumeration_selects_largest_model_by_image_count`
- **Arrange:** create a fake `sparse/` dir with models `0` (3 images) and `1`
  (7 images), each with valid `images.txt` + `points3D.txt`.
- **Expect:** `selected_model_index == 1`, `largest_connected_model_share == 0.7`
  (7/10 total).
- **Why:** replaces the `sparse/"0"` hardcode with a deterministic rule.

### 3.2 `test_enumeration_single_model_has_share_1`
- **Arrange:** one model with all images.
- **Expect:** `selection_rule == "single_model"`, `largest_connected_model_share == 1.0`.

### 3.3 `test_enumeration_ties_broken_by_point3d_then_index`
- **Arrange:** two models with same image count, model `1` has more points.
- **Expect:** `selected_model_index == 1`.
- **Arrange:** two models with same image count AND same point count.
- **Expect:** `selected_model_index == 0` (lowest index).

### 3.4 `test_enumeration_empty_dir_fails_closed`
- **Arrange:** `sparse/` dir with no subdirectories.
- **Expect:** `ValueError` (no models found).

### 3.5 `test_enumeration_is_frozen_and_forbids_extra`
- Standard immutability + extra-field rejection.

## Phase 4: Red — three-state decision logic

### 4.1 `test_invocation_succeeded_true_on_valid_engine`
- **Arrange:** report with `engine="colmap"`, non-empty registered count.
- **Expect:** `invocation_succeeded == True`.

### 4.2 `test_invocation_succeeded_false_on_crash`
- **Arrange:** report with `engine="colmap"`, `registered_count=0`,
  `rejection_reasons=("colmap crashed",)`.
- **Expect:** `invocation_succeeded == False`,
  `training_allowed == False`.

### 4.3 `test_quality_accepted_true_when_all_thresholds_met`
- **Arrange:** policy `min_registered_count=10, min_registered_ratio=0.8, ...`;
  report with `registered_count=18, total_input_images=20, registered_ratio=0.9`,
  full session coverage, single model share=1.0.
- **Expect:** `quality_accepted == True`.

### 4.4 `test_quality_accepted_false_below_registered_count`
- **Arrange:** policy `min_registered_count=15`; report with
  `registered_count=10`.
- **Expect:** `quality_accepted == False`, `rejection_reasons` contains
  "registered_count".

### 4.5 `test_quality_accepted_false_below_ratio`
- **Arrange:** policy `min_registered_ratio=0.8`; report with
  `registered_ratio=0.4`.
- **Expect:** `quality_accepted == False`.

### 4.6 `test_quality_accepted_false_low_session_coverage`
- **Arrange:** policy `min_session_coverage_ratio=0.8`; one session has
  `registered=2, total=10`.
- **Expect:** `quality_accepted == False`, reason mentions session id.

### 4.7 `test_quality_accepted_false_long_unregistered_run`
- **Arrange:** policy `max_unregistered_consecutive_run=3`; session has
  `longest_unregistered_run=5`.
- **Expect:** `quality_accepted == False`.

### 4.8 `test_quality_accepted_false_low_model_share`
- **Arrange:** policy `min_largest_connected_model_share=0.8`; report with
  share=0.5.
- **Expect:** `quality_accepted == False`.

## Phase 5: Red — training_allowed fail-closed

### 5.1 `test_training_allowed_false_for_mock_engine`
- **Arrange:** full coverage, `engine="mock"`.
- **Expect:** `quality_accepted == True`, `training_allowed == False`.

### 5.2 `test_training_allowed_false_without_capture_manifest_sha`
- **Arrange:** full coverage, `engine="colmap"`,
  `capture_manifest_sha256=None`.
- **Expect:** `training_allowed == False`.

### 5.3 `test_training_allowed_true_only_when_all_conditions_met`
- **Arrange:** `engine="colmap"`, manifest SHA present, all thresholds met.
- **Expect:** `training_allowed == True`.

### 5.4 `test_training_allowed_false_overrides_rejection_reasons`
- **Arrange:** all thresholds met, but `rejection_reasons=("manual block",)`.
- **Expect:** `quality_accepted == False`, `training_allowed == False`.

## Phase 6: Red — validation (re-derive, don't trust)

### 6.1 `test_validate_recomputes_policy_sha`
- **Arrange:** report with a tampered `policy_canonical_sha256`.
- **Expect:** `ValueError` (sha mismatch).

### 6.2 `test_validate_recomputes_registration_sha`
- **Arrange:** report with a tampered `registration_json_sha256`.
- **Expect:** `ValueError`.

### 6.3 `test_validate_rederives_quality_accepted`
- **Arrange:** report claims `quality_accepted=True` but registered count is
  below the policy threshold.
- **Expect:** `ValueError` (claim contradicts derivation).

### 6.4 `test_validate_rederives_training_allowed`
- **Arrange:** report claims `training_allowed=True` but `engine="mock"`.
- **Expect:** `ValueError`.

### 6.5 `test_validate_rejection_reasons_consistency`
- **Arrange:** `quality_accepted=False` but `rejection_reasons=()`.
- **Expect:** `ValueError` (rejection must be explained).
- **Arrange:** `quality_accepted=True` but `rejection_reasons=("x",)`.
- **Expect:** `ValueError` (acceptance must have no reasons).

### 6.6 `test_validate_accepts_honest_report`
- **Arrange:** fully honest report matching all derivations.
- **Expect:** `validate_registration_quality()` returns without error.

## Phase 7: Red — report round-trip and tamper detection

### 7.1 `test_report_survives_json_roundtrip`
- **Arrange:** construct a report, `model_dump_json`, reload via
  `RegistrationQualityReport.model_validate_json`.
- **Expect:** all fields identical.

### 7.2 `test_report_written_with_lf_newlines`
- **Arrange:** write report to file, read raw bytes.
- **Expect:** `\n` line endings, no `\r\n`.

### 7.3 `test_tampered_report_file_fails_validation`
- **Arrange:** write report, manually edit `registered_count` in the JSON file,
  reload and validate.
- **Expect:** `ValueError` (derived quality_accepted no longer matches claim).

## Phase 8: Green — minimal implementation

Implement `pipeline/registration_quality.py` to make all Phase 1–7 tests pass:

1. `RegistrationQualityPolicy` (frozen, `extra="forbid"`, all fields required).
2. `policy_canonical_sha256()` function.
3. `SparseModelEntry`, `SparseModelEnumeration` with `enumerate_sparse_models()`.
4. `SessionQualityOutcome`, `RegistrationQualityReport` (frozen, `extra="forbid"`).
5. `_derive_quality_accepted()` and `_derive_training_allowed()` functions.
6. `validate_registration_quality()` function.
7. All SHA fields validated as 64-hex via `_require_64_hex_sha` (reuse from
   `pipeline/synthetic_village/production_journal.py` or replicate the pattern —
   do NOT import across domains; replicate the small validator).

**Constraint:** no SciPy. NumPy + pydantic only. No imports from
`pipeline/synthetic_village/*` (cross-domain coupling). No imports from
`pipeline/registration.py` (the wrapper integration is a separate follow-up).

## Phase 9: Regression and commit

1. Run full test suite:
   ```powershell
   python -m pytest tests/test_registration_quality.py -q
   python -m pytest tests/test_reconstruct.py tests/test_registration.py -q
   ```
2. Run ruff:
   ```powershell
   python -m ruff check pipeline/registration_quality.py tests/test_registration_quality.py
   ```
3. Verify `registration.json` canonical bytes unchanged (the new module does not
   touch `RegistrationResult`).
4. Path-limited commit:
   ```text
   git add pipeline/registration_quality.py tests/test_registration_quality.py
   git commit -- pipeline/registration_quality.py tests/test_registration_quality.py
   ```
   Commit message tail: `Co-Authored-By: GLM-5.2 <noreply@z.ai.com>`

## Follow-up (not in this plan)

After Codex reviews this module:

- **Follow-up A:** Modify `colmap_register()` to call `enumerate_sparse_models()`
  and emit a `RegistrationQualityReport` alongside `RegistrationResult`. Replace
  the `sparse/"0"` hardcode. This is in `pipeline/registration.py`.
- **Follow-up B:** Studio/Viewer consumes `RegistrationQualityReport` to display
  the three-state decision. This is Codex's lane.
- **Follow-up C:** `scripts/prepare_import.py` / `scripts/reconstruct_local.py`
  checks `training_allowed` before proceeding to training. This is a CLI
  integration step.
