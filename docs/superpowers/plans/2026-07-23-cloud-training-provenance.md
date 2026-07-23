# Cloud GPU Training Provenance Handshake — TDD Plan

> Date: 2026-07-23
> Owner: GLM lane (HANDOFF-GLM-002 Task 3, design + TDD plan only)
> Status: Plan — awaiting Codex review before implementation
> Spec: `docs/superpowers/specs/2026-07-23-cloud-training-provenance-design.md`

## Scope

This plan covers the **implementation phase** (not started until Codex reviews
the design). The implementation phase creates `pipeline/training_provenance.py`
and `tests/test_training_provenance.py` only — it does NOT modify
`cloud/train_3dgs_nerfstudio.sh`, `pipeline/recon_schema.py`,
`pipeline/reconstruct.py`, or `scripts/prepare_import.py`.

The cloud script integration (modifying `train_3dgs_nerfstudio.sh` to emit
`training-request.json` + `training-result.json`) and the `prepare_import`
integration (consuming the handshake) are **separate follow-up plans** that
depend on this module shipping first.

## Test file

`tests/test_training_provenance.py` — all tests in a single file, grouped by
phase.

## Phase 1: Red — TrainingRequest schema

### 1.1 `test_request_requires_all_fields`
- **Arrange:** attempt `TrainingRequest()` with no args.
- **Expect:** `ValidationError` (request_id, created_at_utc_iso, input_bindings,
  training_config, expected_output_format all required).
- **Why:** no implicit defaults — operator must explicitly declare intent.

### 1.2 `test_input_bindings_min_length_1`
- **Arrange:** `TrainingRequest(..., input_bindings=())`.
- **Expect:** `ValidationError`.
- **Why:** a request with no bound inputs is unverifiable.

### 1.3 `test_config_requires_seed`
- **Arrange:** `TrainingConfig(trainer_name="nerfstudio-splatfacto",
  trainer_version="0.3.4", max_resolution=1024, total_steps=30000)` — no seed.
- **Expect:** `ValidationError` (random_seed required).
- **Why:** a training run without a recorded seed is not reproducible.

### 1.4 `test_request_is_frozen_and_forbids_extra`
- Standard immutability + extra-field rejection.

### 1.5 `test_input_binding_sha_must_be_64_hex`
- **Arrange:** `TrainingInputBinding(artifact_kind="capture_manifest",
  artifact_sha256="not-a-sha", ...)`.
- **Expect:** `ValidationError`.

## Phase 2: Red — TrainingResult schema

### 2.1 `test_result_requires_all_fields`
- **Arrange:** attempt `TrainingResult()` with no args.
- **Expect:** `ValidationError`.

### 2.2 `test_result_sha_fields_must_be_64_hex`
- **Arrange:** result with `request_canonical_sha256="bad"`,
  `primary_ply_sha256="bad"`, `training_log_sha256="bad"`.
- **Expect:** `ValidationError` for each.

### 2.3 `test_failed_status_requires_error_message`
- **Arrange:** `TrainingStatus(state="failed", exit_code=1)` — no error_message.
- **Expect:** `ValidationError`.
- **Why:** a failed run must explain why.

### 2.4 `test_completed_status_rejects_error_message`
- **Arrange:** `TrainingStatus(state="completed", exit_code=0,
  error_message="oops")`.
- **Expect:** `ValidationError`.
- **Why:** a completed run must not carry an error message.

### 2.5 `test_result_is_frozen_and_forbids_extra`
- Standard immutability + extra-field rejection.

## Phase 3: Red — canonical SHA

### 3.1 `test_request_canonical_sha_is_deterministic`
- **Arrange:** construct the same request twice.
- **Expect:** identical `request_canonical_sha256`.

### 3.2 `test_request_sha_changes_when_config_changes`
- **Arrange:** two requests differing only in `random_seed`.
- **Expect:** different SHAs.

### 3.3 `test_result_canonical_sha_is_deterministic`
- Same pattern for `TrainingResult`.

### 3.4 `test_canonical_uses_lf_and_sort_keys`
- **Arrange:** construct a request; compare SHA against manually computed
  `sha256(model_dump_json(sort_keys=True, ensure_ascii=True).encode("utf-8"))`
  with LF newlines.
- **Expect:** exact match.

## Phase 4: Red — validate_training_provenance (content closure)

### 4.1 `test_validate_accepts_honest_result`
- **Arrange:** request + matching result + actual PLY bytes whose SHA matches
  `primary_ply_sha256`.
- **Expect:** `validate_training_provenance()` returns without error.

### 4.2 `test_validate_rejects_input_sha_mismatch`
- **Arrange:** result's `actual_input_shas` contains a SHA not in request's
  `input_bindings`.
- **Expect:** `ValueError` (input closure broken).

### 4.3 `test_validate_rejects_missing_input_sha`
- **Arrange:** request binds 2 inputs; result's `actual_input_shas` has only 1.
- **Expect:** `ValueError` (input closure incomplete).

### 4.4 `test_validate_rejects_request_sha_mismatch`
- **Arrange:** result's `request_canonical_sha256` doesn't match request's
  actual SHA.
- **Expect:** `ValueError`.

### 4.5 `test_validate_rejects_ply_sha_not_in_outputs`
- **Arrange:** result's `primary_ply_sha256` is not in `output_bindings`.
- **Expect:** `ValueError`.

### 4.6 `test_validate_rejects_ply_bytes_mismatch`
- **Arrange:** actual PLY bytes' SHA doesn't match `primary_ply_sha256`.
- **Expect:** `ValueError` (tamper detection).

### 4.7 `test_validate_rejects_failed_status_with_ply`
- **Arrange:** `training_status.state="failed"` but `primary_ply_sha256` is
  non-empty.
- **Expect:** `ValueError` (failed run cannot claim valid PLY).

### 4.8 `test_validate_accepts_failed_status_without_ply`
- **Arrange:** `training_status.state="failed"`,
  `primary_ply_sha256=""`, `error_message="OOM"`.
- **Expect:** validation passes (failed runs are valid results, just not
  trustworthy).

## Phase 5: Red — TrainingTrust derivation

### 5.1 `test_trust_true_when_all_conditions_met`
- **Arrange:** content-closed, inputs verified, registration quality passed,
  trainer identified, seed recorded, log bound, environment captured,
  `training_status.state="completed"`.
- **Expect:** `is_trustworthy == True`.

### 5.2 `test_trust_false_when_registration_quality_failed`
- **Arrange:** `RegistrationQualityReport.training_allowed=False`.
- **Expect:** `is_trustworthy == False`.

### 5.3 `test_trust_false_when_trainer_empty`
- **Arrange:** `actual_trainer_name=""`.
- **Expect:** `is_trustworthy == False`.

### 5.4 `test_trust_false_when_seed_none`
- **Arrange:** `TrainingConfig.random_seed=None` (or missing).
- **Expect:** `is_trustworthy == False`.

### 5.5 `test_trust_false_when_log_missing`
- **Arrange:** `training_log_sha256=""`.
- **Expect:** `is_trustworthy == False`.

### 5.6 `test_trust_false_when_training_failed`
- **Arrange:** `training_status.state="failed"`.
- **Expect:** `is_trustworthy == False`.

### 5.7 `test_trust_false_when_content_not_closed`
- **Arrange:** validation would fail (tampered SHA).
- **Expect:** `is_trustworthy == False`.

### 5.8 `test_trust_true_does_not_imply_metric`
- **Arrange:** fully trustworthy result.
- **Expect:** `is_trustworthy == True` but the `TrainingTrust` model has no
  `metric` / `aligned` / `real_photos` field — trust is explicitly limited to
  "content-closed and inputs verified."

## Phase 6: Red — round-trip and tamper detection

### 6.1 `test_request_survives_json_roundtrip`
- Standard `model_dump_json` → `model_validate_json` round-trip.

### 6.2 `test_result_survives_json_roundtrip`
- Standard round-trip.

### 6.3 `test_files_written_with_lf_newlines`
- **Arrange:** write request + result to files, read raw bytes.
- **Expect:** `\n` line endings, no `\r\n`.

### 6.4 `test_tampered_result_file_fails_validation`
- **Arrange:** write result, manually edit `primary_ply_sha256` in the JSON,
  reload and validate against the request + actual PLY bytes.
- **Expect:** `ValueError`.

## Phase 7: Red — GpuEnvironment and output bindings

### 7.1 `test_gpu_env_requires_all_fields`
- **Arrange:** `GpuEnvironment()` with no args.
- **Expect:** `ValidationError`.

### 7.2 `test_output_binding_ply_properties_optional`
- **Arrange:** `TrainingOutputBinding(artifact_kind="trained_ply", ...)`
  without `gaussian_count` / `sh_degree`.
- **Expect:** passes (optional fields).

### 7.3 `test_output_binding_log_kind`
- **Arrange:** `TrainingOutputBinding(artifact_kind="training_log", ...)`.
- **Expect:** passes (log artefacts don't need PLY properties).

## Phase 8: Red — nerfstudio vs Brush metadata

### 8.1 `test_nerfstudio_trainer_name_accepted`
- **Arrange:** `TrainingConfig(trainer_name="nerfstudio-splatfacto", ...)`.
- **Expect:** passes.

### 8.2 `test_brush_trainer_name_accepted`
- **Arrange:** `TrainingConfig(trainer_name="brush", ...)`.
- **Expect:** passes.

### 8.3 `test_unknown_trainer_name_rejected`
- **Arrange:** `TrainingConfig(trainer_name="random-trainer", ...)`.
- **Expect:** `ValidationError` (not in Literal).

## Phase 9: Green — minimal implementation

Implement `pipeline/training_provenance.py` to make all Phase 1–8 tests pass:

1. `TrainingInputBinding`, `TrainingConfig`, `TrainingRequest` (frozen,
   `extra="forbid"`).
2. `GpuEnvironment`, `TrainingOutputBinding`, `TrainingStatus`,
   `TrainingResult` (frozen, `extra="forbid"`).
3. `request_canonical_sha256` / `result_canonical_sha256` properties.
4. `validate_training_provenance()` function.
5. `TrainingTrust` model + `derive_training_trust()` function.
6. `_require_64_hex_sha` validator (replicated, not cross-domain imported).
7. `TrainingStatus` validator: failed → `error_message` required; completed →
   `error_message` forbidden.

**Constraints:**
- No SciPy. No imports from `pipeline/synthetic_village/*`.
- No imports from `pipeline/registration.py` (decoupled; the handshake consumes
  Task 2's `RegistrationQualityReport` schema, but only its `training_allowed`
  field — passed as a plain bool to avoid cross-module coupling at this stage).
- No imports from `cloud/` or `scripts/`.

## Phase 10: Regression and commit

1. Run tests:
   ```powershell
   python -m pytest tests/test_training_provenance.py -q
   python -m pytest tests/test_reconstruct.py tests/test_registration.py -q
   ```
2. Run ruff:
   ```powershell
   python -m ruff check pipeline/training_provenance.py tests/test_training_provenance.py
   ```
3. Verify no existing tests break (the new module does not touch any existing
   code path).
4. Path-limited commit:
   ```text
   git add pipeline/training_provenance.py tests/test_training_provenance.py
   git commit -- pipeline/training_provenance.py tests/test_training_provenance.py
   ```
   Commit message tail: `Co-Authored-By: GLM-5.2 <noreply@z.ai.com>`

## Follow-up (not in this plan)

After Codex reviews this module:

- **Follow-up A:** Modify `cloud/train_3dgs_nerfstudio.sh` to emit
  `training-request.json` before training and `training-result.json` after
  training (capture trainer version via `ns-train --version`, GPU via
  `nvidia-smi`, config via `config.yml` SHA, log via `tee` + SHA).
- **Follow-up B:** Modify `scripts/prepare_import.py` to accept
  `--training-result` and call `validate_training_provenance()`. If
  `is_trustworthy=True`, add `training_provenance.v1=<result_sha>` to
  `CoordinateFrame.evidence`. If False, fail-closed unless
  `--allow-unverified-training` is passed.
- **Follow-up C:** Studio/Viewer displays training provenance status. This is
  Codex's lane.
