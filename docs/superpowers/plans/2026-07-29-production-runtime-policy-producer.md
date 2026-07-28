# Production Runtime Policy Producer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, no-network producer for the canonical private production runtime policy without inventing or self-attesting external GPU facts.

**Architecture:** A focused `pipeline.production_runtime_policy` module loads one canonical operator fact file, derives only repository-owned identities from an exact clean Git commit, constructs the existing `ProductionRuntimePolicy`, and publishes it durably with no-replace semantics. Existing readiness, worker, closure, import, and acceptance consumers remain unchanged and continue to decide trust from fresh measurements.

**Tech Stack:** Python 3.11, Pydantic v2, Git plumbing commands, existing durable I/O and production runtime evidence models, pytest, Ruff.

---

## File structure

- Create `pipeline/production_runtime_policy.py`: operator-input schema, stable bounded reads, exact-commit repository bindings, deterministic policy construction, durable publication, CLI.
- Create `tests/test_production_runtime_policy.py`: unit and executable CLI coverage for the producer.
- Modify `docs/manual/reconstruction-setup.md`: replace manual policy authoring with the producer command and preserve the external-evidence boundary.
- Modify `docs/production-v1-status.md`: record producer readiness without claiming real GPU acceptance.
- Modify `tests/test_real_golden_path_docs.py`: lock the documented operator path.

### Task 1: Lock the producer contract with failing tests

**Files:**
- Create: `tests/test_production_runtime_policy.py`

- [x] **Step 1: Write deterministic construction and repository-binding tests**

Create a temporary Git repository containing committed copies of
`cloud/production_runtime_entrypoint.py` and `cloud/remote_training_worker.py`.
Write canonical operator facts using a strict model with these fields:

```python
{
    "schema": "nantai.production-runtime-policy-input.v1",
    "expected_remote_target_sha256": sha("target"),
    "expected_container_identity": f"registry.example/nantai@sha256:{sha('image')}",
    "expected_gpu_uuid": "GPU-12345678-1234-1234-1234-123456789abc",
    "min_gpu_memory_mib": 16384,
    "expected_cuda_runtime_version": "12.8",
    "expected_python_version": "3.11.9",
    "expected_nerfstudio_version": "1.1.5",
    "expected_training_cli_schema_sha256": sha("ns-train-help"),
    "required_training_cli_options": ["--data", "--output-dir"],
    "expected_container_runtime_sha256": sha("docker"),
    "expected_nvidia_smi_sha256": sha("nvidia-smi"),
    "expected_python_sha256": sha("python"),
    "expected_training_cli_sha256": sha("ns-train"),
}
```

Assert the output uses the repository HEAD, `fixed_production_probe_set_sha256()`,
and SHA-256 of the two committed repository artifacts. Repeated construction
must return byte-identical canonical policy bytes.

- [x] **Step 2: Write fail-closed input and publication tests**

Cover duplicate JSON keys, noncanonical JSON, extra fields, repeated-character
placeholder SHA values, symlinked input, repository artifact bytes that differ
from HEAD, absent output parent, existing output, and output publication races.
Assert every failure leaves the requested output absent and never mutates Git.

- [x] **Step 3: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/test_production_runtime_policy.py -q
```

Expected: collection fails because `pipeline.production_runtime_policy` does not
exist.

### Task 2: Implement the minimal fail-closed producer

**Files:**
- Create: `pipeline/production_runtime_policy.py`
- Test: `tests/test_production_runtime_policy.py`

- [x] **Step 1: Add a strict canonical operator-input model**

Define `ProductionRuntimePolicyInput` with schema
`nantai.production-runtime-policy-input.v1`, `extra="forbid"`,
`allow_inf_nan=False`, strict values, and the exact external fields listed in
Task 1. Reject obvious placeholder identities where a 64-hex value consists of
one repeated character; this input is a production allow-policy, not a fixture.

- [x] **Step 2: Add bounded stable readers and exact Git bindings**

Resolve a real repository directory and real regular input/artifact files.
Read every file with pre/post `lstat` signatures and an explicit maximum size.
Use `git --no-replace-objects rev-parse --verify HEAD^{commit}` and
`git --no-replace-objects show HEAD:<path>`; require the committed blob bytes to
equal the working-tree bytes before hashing them.

- [x] **Step 3: Construct only the existing policy schema**

Call:

```python
ProductionRuntimePolicy.create(
    expected_exact_commit=head,
    expected_probe_set_sha256=fixed_production_probe_set_sha256(),
    expected_checker_sha256=sha256(entrypoint_bytes),
    expected_worker_sha256=sha256(worker_bytes),
    **operator_input.model_dump(exclude={"schema_id"}),
)
```

Do not add a second decision, readiness, or acceptance schema.

- [x] **Step 4: Publish canonical bytes with no-replace durability**

Write a mode-`0600` sibling staging file, flush it, publish with
`publish_file_noreplace`, reopen with
`load_production_runtime_policy_bytes`, compare exact bytes, flush the parent
directory, and remove only the owned staging file on failure.

- [x] **Step 5: Add the CLI**

Expose:

```powershell
python -m pipeline.production_runtime_policy `
  --repo-root D:/vibecoding/nantai `
  --operator-input C:/private/runtime-policy-input.json `
  --output C:/private/production-runtime-policy.json
```

Success prints only the policy content SHA and output path. Failures return
nonzero without echoing operator input bytes.

- [x] **Step 6: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_production_runtime_policy.py -q
python -m ruff check pipeline/production_runtime_policy.py tests/test_production_runtime_policy.py
```

Expected: all tests and Ruff pass.

### Task 3: Document the operator workflow and trust boundary

**Files:**
- Modify: `docs/manual/reconstruction-setup.md`
- Modify: `docs/production-v1-status.md`
- Modify: `tests/test_real_golden_path_docs.py`

- [x] **Step 1: Add failing documentation assertions**

Require the manual to contain the module command, the operator-input schema,
no-replace behavior, automatic exact commit/probe/entrypoint/worker binding, and
the sentence that producer output is not accepted runtime evidence.

- [x] **Step 2: Update the manual and status**

Place policy generation before remote preflight. Explain which fields are
external approved facts, which four identities the producer derives, and why a
fresh GPU measurement plus decision is still required.

- [x] **Step 3: Run documentation tests**

Run:

```powershell
python -m pytest tests/test_real_golden_path_docs.py tests/test_production_release_docs.py -q
```

Expected: all tests pass.

### Task 4: Verify, commit, and push the isolated increment

**Files:**
- Verify only the files in Tasks 1–3 plus their dependent runtime suites.

- [x] **Step 1: Run the full relevant regression matrix**

```powershell
python -m pytest tests/test_production_runtime_policy.py tests/test_production_runtime_evidence.py tests/test_production_runtime_entrypoint.py tests/test_remote_readiness_checker.py tests/test_remote_shell_executor.py tests/test_remote_training_worker.py tests/test_real_golden_path_docs.py tests/test_production_release_docs.py -q
python -m ruff check pipeline/production_runtime_policy.py tests/test_production_runtime_policy.py tests/test_real_golden_path_docs.py
git diff --check
```

- [x] **Step 2: Review the final diff**

Confirm no secrets, hostnames, credentials, generated private policy, runtime
evidence, or parallel trust schema entered Git.

- [ ] **Step 3: Commit with the required attribution**

```powershell
git add -- pipeline/production_runtime_policy.py tests/test_production_runtime_policy.py docs/manual/reconstruction-setup.md docs/production-v1-status.md tests/test_real_golden_path_docs.py docs/superpowers/plans/2026-07-29-production-runtime-policy-producer.md
git commit -m "feat: produce bound production runtime policy" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
```

- [ ] **Step 4: Push through the one-shot proxy and compare SHAs**

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

Expected: local and remote `main` SHA values match.
