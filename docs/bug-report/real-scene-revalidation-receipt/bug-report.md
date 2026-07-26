# Bug report: completed-stage revalidation failure has no receipt

## Diagnostic capsule

| Field | Evidence |
|---|---|
| 1. Symptom | After a completed stage artifact is changed, `fetch --resume` rejects the bytes but leaves no new terminal attempt receipt. The journal still exposes only the earlier `completed` receipt. |
| 2. Evidence | `test_resume_revalidates_bytes_not_file_existence` reproduces the rejection. In `RealSceneRunner._run_stage`, lines 468–473 validate the latest receipt before an attempt id or stage root exists, so `DatasetEvidenceError` escapes before the receipt-writing path at lines 526–568. |
| 3. Confirmed root cause | Completed-receipt revalidation is outside the stage attempt/evidence lifecycle. The validation itself is correct; its terminal outcome is not journalled. |
| 4. Diagnostic strategy | Trace the corrupted artifact from `_verify_artifact_bindings` back through `_verify_completed` and compare it with the normal blocked/unknown execution path, which hashes evidence and publishes an immutable receipt before raising. |
| 5. Timeout strategy | If a focused regression test cannot expose the missing receipt within one test cycle, stop and re-check receipt ordering and timestamp selection instead of widening the change. |
| 6. Warning strategy | Stop if the change permits retry from unverified blocked evidence, mutates the old completed receipt, or records dataset/private bytes instead of bounded hashes and a portable reason. |
| 7. User-visible correction | Studio and operators can see that resume stopped on integrity revalidation, and a later retry cannot erase the earlier successful and failed attempt evidence. |
| 8. Acceptance | A byte-tamper regression test must first fail, then prove: old completed receipt preserved, new blocked receipt and canonical failure evidence preserved, a second call requires explicit retry, and retry creates a third completed receipt. Transitive prerequisite corruption must also journal the downstream revalidation failure. |

## 1. Reporter

Codex found the gap while preparing the approved Production V1 real golden-path
corruption drill in Task 13.

## 2. Reproduction and expected behavior

1. Complete the `fetch` stage.
2. Change one byte in a receipt-bound output.
3. Run `fetch --resume`.

Actual behavior before the fix: the command raises `DatasetEvidenceError`, but
no immutable failure attempt is written.

Expected behavior: reject the corrupted bytes, preserve the original completed
receipt, publish a new blocked revalidation receipt with bounded canonical
evidence, and require an explicit retry for a new execution attempt.

## 3. Root-cause analysis

`RealSceneRunner._run_stage` revalidates a latest completed receipt before it
allocates an attempt id. Both direct output mismatch and transitive prerequisite
mismatch therefore bypass the existing evidence and receipt publication path.
The hash comparison and fail-closed decision are correct; the ordering makes
the failure unauditable.

Changing dataset validation or weakening the hash check was rejected. Rewriting
the old completed receipt was also rejected because receipts are immutable and
content addressed.

## 4. Fix

When, and only when, validation of a latest `completed` receipt raises
`DatasetEvidenceError`, create a fresh attempt containing:

- a canonical `nantai.stage-revalidation-failure.v1` evidence record;
- the SHA-256 of the previously completed receipt;
- the affected stage and bounded integrity reason;
- a new immutable blocked stage receipt with no outputs.

The previous receipt and its bytes remain untouched. A subsequent call sees the
blocked receipt and requires explicit retry. Corrupt evidence attached to an
already blocked/unknown receipt continues to fail raw validation and is never
used to authorize retry.

## 5. Verification

- Focused direct-byte corruption regression.
- Transitive prerequisite corruption regression.
- Complete `tests/test_real_scene_runner.py`.
- Real Task 13 disposable-copy corruption drill.
- Ruff and `git diff --check`.
