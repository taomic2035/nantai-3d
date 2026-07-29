# Real-Scene Stable Evidence Reads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining real-scene journal, stage receipt/artifact, and
import input check-then-open gaps without weakening Windows reparse handling,
bounded reads, or error privacy.

**Architecture:** Each trust-critical file is opened once with `os.open`, then
validated from the same descriptor before and after its bounded or streaming
read. Path-level `lstat` remains only an opening constraint and a post-read
namespace drift check; Windows reparse state is part of every identity
signature.

**Tech Stack:** Python 3.11+, `os.open`, `os.fdopen`, `os.fstat`, `stat`,
pytest monkeypatch fault injection, Ruff.

---

### Task 1: Prove the remaining fail-open cases

**Files:**

- Modify: `tests/test_real_scene_import.py`
- Modify: `tests/test_real_scene_runner.py`
- Modify: `tests/test_training_executor.py`

- [x] **Step 1: Add reparse-identity RED tests**

For each module, wrap the second `os.fstat` result with the same POSIX fields
and a changed Windows reparse attribute:

```python
def with_reparse(observed):
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_file_attributes=getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ),
    )
```

Call the real loader and require its existing fixed “changed” error. The tests
must fail because the five-field signatures currently ignore reparse state.

- [x] **Step 2: Add journal path reparse RED test**

Patch only the target journal’s `Path.lstat()` result to carry the reparse bit
and require:

```python
with pytest.raises(
    TrainingExecutorError,
    match="real-scene journal is missing or link-like",
):
    load_real_scene_journal(journal_path)
```

The test must fail because `training_executor` currently checks only
`stat.S_ISLNK`.

- [x] **Step 3: Add short-read and pre-cap RED tests**

Wrap the stage artifact stream so it returns EOF before `st_size` bytes and
require `DatasetEvidenceError("stage artifact changed while hashing: ...")`.
For import input, patch the opening `lstat` size above
`_MAX_IMPORT_FILE_BYTES`, forbid `os.open`, and require
`RealSceneImportError("<label> size is outside the allowed range")`.

- [x] **Step 4: Run RED tests**

Run:

```powershell
python -m pytest -q `
  tests/test_real_scene_import.py `
  tests/test_real_scene_runner.py `
  tests/test_training_executor.py `
  -k "reparse or short_read or size_cap"
```

Expected: the newly added cases fail for the missing reparse binding, missing
journal link-like rejection, short-read acceptance, and late import size cap.

### Task 2: Bind reads to complete descriptor identities

**Files:**

- Modify: `pipeline/real_scene_import.py`
- Modify: `pipeline/real_scene_runner.py`
- Modify: `pipeline/training_executor.py`

- [x] **Step 1: Bind Windows reparse state**

Change every touched signature to six fields:

```python
def _stat_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )
```

- [x] **Step 2: Reject journal junctions and reparse points**

Add a local `_is_linklike(path, observed)` helper to
`pipeline/training_executor.py` that rejects symlinks, the Windows reparse bit,
and `Path.is_junction()`, treating inspection `OSError` as link-like. Use it in
`load_real_scene_journal` before `os.open`.

- [x] **Step 3: Enforce complete and bounded reads**

Before opening import input, reject `before.st_size >
_MAX_IMPORT_FILE_BYTES`. After streaming a stage artifact, require:

```python
measured == before.st_size
```

Keep receipt/journal bounded `read(cap + 1)` checks and do not reopen content by
path.

- [x] **Step 4: Run GREEN tests**

Run the Task 1 command and require all selected tests to pass.

### Task 3: Verify the caller surface and commit narrowly

**Files:**

- Verify: `pipeline/real_scene_import.py`
- Verify: `pipeline/real_scene_runner.py`
- Verify: `pipeline/training_executor.py`
- Verify: `tests/test_real_scene_import.py`
- Verify: `tests/test_real_scene_runner.py`
- Verify: `tests/test_training_executor.py`

- [x] **Step 1: Run focused suites**

```powershell
python -m pytest -q `
  tests/test_real_scene_import.py `
  tests/test_real_scene_runner.py `
  tests/test_training_executor.py
```

- [x] **Step 2: Run dependent trust-chain suites**

```powershell
python -m pytest -q `
  tests/test_real_scene_operations.py `
  tests/test_real_scene_paths.py `
  tests/test_real_scene_acceptance.py
```

- [x] **Step 3: Run static verification**

```powershell
python -m ruff check `
  pipeline/real_scene_import.py `
  pipeline/real_scene_runner.py `
  pipeline/training_executor.py `
  tests/test_real_scene_import.py `
  tests/test_real_scene_runner.py `
  tests/test_training_executor.py
git diff --check
```

- [x] **Step 4: Commit and push only the declared paths**

Commit subject:

```text
fix: bind real-scene stage reads to stable handles
```

Append exactly:

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

Push the exact commit SHA to `refs/heads/main` with the one-shot proxy.

### Task 4: Prove runtime and OCI identity gaps

**Files:**

- Modify: `tests/test_production_cuda_image_workflow.py`
- Modify: `tests/test_production_runtime_entrypoint.py`
- Modify: `tests/test_production_runtime_policy.py`

- [x] **Step 1: Add OCI reparse RED tests**

Call the real `_read_github_bundle` helper with a valid fixture. Inject the
Windows reparse bit into path `lstat` and descriptor-after `fstat` independently
and require a fixed `ProductionCudaOciInspectionError`.

- [x] **Step 2: Add runtime-entrypoint reparse RED tests**

Call `_read_stable` with a small evidence file. A path reparse bit must produce
`"<label> must be a bounded regular file"`; descriptor-after reparse drift must
produce `"<label> changed while being read"`.

- [x] **Step 3: Add policy short-read and opening-window RED tests**

Wrap `os.fdopen` so a regular input returns one byte and then EOF while its
descriptor size remains unchanged. Require:

```python
with pytest.raises(
    ProductionRuntimePolicyProducerError,
    match="changed while read",
):
    runtime_policy_producer._read_stable_regular_file(
        source,
        label="operator input",
        maximum_bytes=1024,
    )
```

Inject the Windows reparse bit into both descriptor `fstat` calls while path
`lstat` stays unchanged; require the same rejection.

- [x] **Step 4: Run the six new tests and confirm RED**

Expected failures are missing path/descriptor reparse binding, missing
path-to-descriptor ctime binding, and accepted short reads.

### Task 5: Close runtime and OCI reads

**Files:**

- Modify: `cloud/inspect_production_cuda_oci.py`
- Modify: `cloud/production_runtime_entrypoint.py`
- Modify: `pipeline/production_runtime_policy.py`

- [x] **Step 1: Use complete identities**

Use a cross-surface identity that Windows reports consistently:

```python
(
    st_dev,
    st_ino,
    stat.S_IFMT(st_mode),
    st_size,
    st_mtime_ns,
    st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT,
)
```

Reject a path-level symlink, junction, or reparse point before opening. For
same-surface path-before/path-after and fd-before/fd-after checks, additionally
bind the full mode and `st_ctime_ns`; do not compare those two Windows-variant
fields directly between path and descriptor surfaces.

- [x] **Step 2: Bind every transition**

Compare `path-before → descriptor-before → descriptor-after → path-after`;
each adjacent transition must preserve the complete identity. Keep fixed error
messages and never include `OSError` text.

- [x] **Step 3: Reject incomplete reads**

Require `len(payload) == path-before.st_size` for byte-returning helpers and
preserve existing byte caps.

- [x] **Step 4: Run GREEN tests**

Run the six tests from Task 4 and require all to pass.

### Task 6: Verify and publish the runtime-evidence batch

**Files:**

- Verify: `cloud/inspect_production_cuda_oci.py`
- Verify: `cloud/production_runtime_entrypoint.py`
- Verify: `pipeline/production_runtime_policy.py`
- Verify: `tests/test_production_cuda_image_workflow.py`
- Verify: `tests/test_production_runtime_entrypoint.py`
- Verify: `tests/test_production_runtime_policy.py`

- [x] **Step 1: Run all three focused suites**

```powershell
python -m pytest -q `
  tests/test_production_cuda_image_workflow.py `
  tests/test_production_runtime_entrypoint.py `
  tests/test_production_runtime_policy.py
```

- [x] **Step 2: Run Ruff and diff-check**

Run Ruff on the six paths and `git diff --check`.

- [x] **Step 3: Commit and push only the seven declared paths**

Use subject `fix: bind production runtime reads to full identities`, the exact
Codex co-author trailer, and the one-shot push proxy.
