# Production V1 Real Reconstruction Golden Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one repeatable, fail-closed real-image reconstruction path from a
content-addressed source through local COLMAP, local Brush preview, remote
Nerfstudio Splatfacto, import/chunking, render evaluation, browser performance
and an auditable acceptance report.

**Architecture:** Keep the approved Capture → Reconstruction → Scene → Product
boundary. New modules own only source receipts, orchestration and acceptance;
existing ingest, capture revision, registration quality, training provenance,
SplatInput, reconstruction integrity and Viewer contracts remain authoritative.
The internal poster dataset proves mechanism only, while a separately
rights-cleared and control-point-aligned capture is required for Production V1.

**Tech Stack:** Python 3.11+, Pydantic v2, standard-library HTTP/SSH subprocess
orchestration, COLMAP 4.1.0 CPU, Brush 0.3.0 wgpu, Nerfstudio 1.1.5 Splatfacto
on pinned CUDA infrastructure, NumPy/scikit-image, Node 22+, Playwright, Spark
Viewer and the existing Studio schema-v2 adapter.

**Spec:** `docs/superpowers/specs/2026-07-26-production-v1-real-golden-path-design.md`

## Global Constraints

- Work directly on the single `main` branch; do not create branches or
  worktrees.
- Use path-limited staging and commits only. Never run `git add -A`,
  `git commit -a`, `git reset --hard` or `git checkout --`.
- Preserve the unrelated
  `tests/test_synthetic_village_weather.py` worktree change. Before and after
  every commit its SHA-256 must remain
  `7ae1f53962d466bfcdddf360381e25489b86bed193b4844e395a30097412b46d`.
- Every Codex-created commit ends with exactly
  `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Fetch and verify that `HEAD` and `origin/main` have identical identities
  before each commit. Push each green,
  reviewable task to `origin/main` instead of accumulating a large local stack.
- External dataset bytes, private captures, EXIF/GPS, control points, cloud
  bundles, credentials and trainer outputs stay under ignored
  `.nantai-studio/`; none may enter source commits or public Release assets.
- The poster source is pinned to repository revision
  `461701c17e83c3f4d2481db32315aa7df703d2f8`, declared as 408 files and
  379,280,986 bytes, has `license_status=not-declared`, and is always
  `internal-canary` with redistribution and Release inclusion disabled.
- A Hugging Face download starts at `https://huggingface.co`, must receive the
  exact pinned `x-repo-commit`, and may redirect only over HTTPS to
  `*.cdn.hf.co`. Never forward credentials to the CDN.
- Local Mac duties are source verification, ingest, COLMAP CPU SfM, optional
  Brush preview, import, integrity verification, Studio/Viewer and reporting.
  Production Splatfacto requires an externally configured CUDA host.
- The canary remains `sfm-local`, arbitrary-unit and unaligned. Only a
  production capture with at least four non-coplanar control points, metre
  units, an explicit content-addressed Sim3 and RMS ≤ 0.25 m may become
  `metric-aligned`.
- Frozen poster SfM thresholds are count ≥ 90, ratio ≥ 0.90, per-session
  coverage ≥ 0.90, longest unregistered run ≤ 5 and largest connected model
  share ≥ 0.95.
- Production Splatfacto output must contain at least 100,000 finite,
  semantically valid Gaussians and a complete INRIA property schema.
- Accepted Nerfstudio training must use `orientation_method=none`,
  `center_method=none`, `auto_scale_poses=false`, `scale_factor=1.0` and bind a
  saved identity dataparser transform. Missing/non-identity evidence blocks
  import instead of guessing a PLY transform or rotating high-order SH.
- Held-out thresholds are mean PSNR ≥ 24.0 dB, mean SSIM ≥ 0.80, mean LPIPS
  ≤ 0.25 and every held-out frame PSNR ≥ 18.0 dB.
- Browser thresholds at 1280×720 are first interactive full-3DGS frame ≤ 10 s,
  median frame time ≤ 33.3 ms and p95 ≤ 50.0 ms after 120 warm-up and 600
  measured frames, with no browser errors, unhandled rejection, indefinite
  load or horizontal overflow.
- Unknown evidence stays `unknown`; it is never coerced to failed, succeeded,
  measured, metric, aligned, real or commercially releasable.

---

## File and responsibility map

| File | Responsibility |
|---|---|
| `config/real-scene/nerfstudio-poster.json` | Committed internal-canary source and policy record, without dataset bytes |
| `config/real-scene/poster-registration-policy.json` | Frozen poster SfM thresholds |
| `pipeline/real_dataset.py` | Source, rights, lock and receipt schemas plus byte revalidation |
| `pipeline/real_dataset_fetch.py` | Hugging Face tree resolution and safe streaming download |
| `scripts/fetch_real_dataset.py` | Narrow fetch/verify CLI |
| `pipeline/real_scene_capture.py` | Select original media, derive capture revision and run fresh COLMAP/quality |
| `pipeline/real_scene_training.py` | Deterministic held-out split and canonical training job bundle |
| `pipeline/training_executor.py` | Provider-neutral executor states, attempts and journal contract |
| `pipeline/remote_shell_executor.py` | Strict SSH submit/poll/fetch implementation |
| `cloud/prepare_real_scene_dataset.py` | Convert the supplied COLMAP model to Nerfstudio data without rerunning SfM |
| `cloud/train_3dgs_nerfstudio.sh` | Pinned prepared-bundle training/evaluation path; legacy mode cannot be accepted |
| `pipeline/real_scene_runner.py` | Resume-safe stage orchestration |
| `scripts/real_scene.py` | `fetch/sfm/train-preview/train-production/import/accept/serve/all` CLI |
| `pipeline/render_evaluation.py` | Held-out metric policy/report and fail-closed decision derivation |
| `pipeline/viewer_acceptance.py` | Browser performance policy/report validation |
| `scripts/capture_viewer_acceptance.mjs` | Playwright real-browser measurement |
| `pipeline/real_scene_acceptance.py` | Human review receipt and aggregate acceptance re-derivation |
| `scripts/record_real_scene_review.py` | Explicit human visual-review receipt CLI |
| `pipeline/studio_server.py` | Read-only accepted/failed/unknown real-scene evidence projection |
| `web/studio/real-scene-evidence.mjs` | Pure presentation model for the evidence panel |
| `web/studio/app.js` | Attach the evidence panel to Review without trust promotion |
| `make.py` | Cross-platform `real-scene` and `real-canary` entry points |

## Task 1: Dataset source, rights, lock and receipt contracts

**Files:**
- Create: `pipeline/real_dataset.py`
- Create: `config/real-scene/nerfstudio-poster.json`
- Create: `config/real-scene/poster-registration-policy.json`
- Create: `tests/test_real_dataset.py`

**Interfaces:**
- Produces:
  `load_real_dataset_source(path: Path) -> HfDatasetSource | LocalCaptureSource`,
  `canonical_model_bytes(model: BaseModel) -> bytes`,
  `validate_dataset_receipt(source, lock, receipt, dataset_root) -> None`,
  `validate_capture_rights(source, rights) -> None`.
- Consumed by Tasks 2, 3, 4, 8 and 11.

- [ ] **Step 1: Write schema and policy rejection tests**

```python
def test_internal_canary_cannot_enable_release():
    with pytest.raises(ValidationError, match="release"):
        HfDatasetSource(
            schema="nantai.real-dataset-source.v1",
            dataset_id="poster",
            role="internal-canary",
            source_kind="hf-dataset",
            repository="nerfstudioteam/datasets",
            repository_revision="4" * 40,
            subtree="poster",
            capture_subtree="poster/images",
            declared_file_count=408,
            declared_total_bytes=379_280_986,
            license_status="not-declared",
            redistribution_allowed=False,
            release_inclusion_allowed=True,
        )


def test_receipt_rejects_live_byte_tamper(tmp_path):
    source, lock, receipt, root = honest_fixture(tmp_path)
    (root / "poster/images/frame.png").write_bytes(b"tampered")
    with pytest.raises(DatasetEvidenceError, match="sha256"):
        validate_dataset_receipt(source, lock, receipt, root)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
python -m pytest tests/test_real_dataset.py -q
```

Expected: collection fails because `pipeline.real_dataset` does not exist.

- [ ] **Step 3: Implement strict frozen contracts and byte-derived validation**

Implement these exact public models and adapter:

```python
class HfDatasetSource(FrozenModel):
    schema: Literal["nantai.real-dataset-source.v1"]
    dataset_id: str
    role: Literal["internal-canary"]
    source_kind: Literal["hf-dataset"]
    repository: str
    repository_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    subtree: PortablePath
    capture_subtree: PortablePath
    declared_file_count: int = Field(ge=1)
    declared_total_bytes: int = Field(ge=1)
    license_status: Literal["not-declared"]
    redistribution_allowed: Literal[False]
    release_inclusion_allowed: Literal[False]


class LocalCaptureSource(FrozenModel):
    schema: Literal["nantai.real-dataset-source.v1"]
    dataset_id: str
    role: Literal["production-acceptance"]
    source_kind: Literal["local-capture"]
    rights_receipt_sha256: Sha256
    redistribution_allowed: bool
    release_inclusion_allowed: bool


RealDatasetSource = Annotated[
    HfDatasetSource | LocalCaptureSource,
    Field(discriminator="source_kind"),
]
REAL_DATASET_SOURCE = TypeAdapter(RealDatasetSource)
```

`DatasetLockEntry` records `relative_path`, `expected_bytes`,
`server_identity`; `DatasetReceiptEntry` records the same path plus measured
`actual_bytes` and `actual_sha256`. Validation must reject duplicate/casefold
collisions, absolute/backslash/parent paths, links, missing/extra files,
non-regular files, size drift, SHA drift, source/lock count drift and source
total-byte drift. Canonical JSON is sorted ASCII with one LF.

- [ ] **Step 4: Add the exact committed poster source and registration policy**

`nerfstudio-poster.json` must contain the pinned revision/count/bytes above and
must never contain a URL with a mutable branch. The policy JSON must parse as:

```python
RegistrationQualityPolicy(
    min_registered_count=90,
    min_registered_ratio=0.90,
    min_session_coverage_ratio=0.90,
    max_unregistered_consecutive_run=5,
    min_largest_connected_model_share=0.95,
)
```

- [ ] **Step 5: Verify and commit Task 1**

Run:

```bash
python -m pytest tests/test_real_dataset.py tests/test_registration_quality.py -q
python -m ruff check pipeline/real_dataset.py tests/test_real_dataset.py
python -m json.tool config/real-scene/nerfstudio-poster.json >/dev/null
python -m json.tool config/real-scene/poster-registration-policy.json >/dev/null
git diff --check
```

Then path-limited commit and push:

```bash
git add pipeline/real_dataset.py tests/test_real_dataset.py \
  config/real-scene/nerfstudio-poster.json \
  config/real-scene/poster-registration-policy.json
git commit -- pipeline/real_dataset.py tests/test_real_dataset.py \
  config/real-scene/nerfstudio-poster.json \
  config/real-scene/poster-registration-policy.json
git push origin main
```

Commit subject: `feat: add real dataset evidence contracts`

## Task 2: Safe Hugging Face fetch and revalidation

**Files:**
- Create: `pipeline/real_dataset_fetch.py`
- Create: `scripts/fetch_real_dataset.py`
- Create: `tests/test_real_dataset_fetch.py`

**Interfaces:**
- Consumes: `HfDatasetSource`, `DatasetLock`, `DatasetReceipt` from Task 1.
- Produces:
  `resolve_hf_lock(source, transport) -> DatasetLock`,
  `fetch_hf_dataset(source, workspace, transport=None) -> DatasetReceipt`,
  `verify_hf_dataset(source, workspace) -> DatasetReceipt`.

- [ ] **Step 1: Write adversarial HTTP fixture tests**

Use an in-process `ThreadingHTTPServer` and assert:

```python
def test_fetch_accepts_pinned_origin_and_approved_cdn(http_fixture, tmp_path):
    http_fixture.origin_commit = "4" * 40
    http_fixture.redirect_host = "localhost"  # injected test policy only
    receipt = fetch_hf_dataset(source("4" * 40), tmp_path, http_fixture.transport)
    assert receipt.entries[0].actual_sha256 == sha256(b"image")


@pytest.mark.parametrize("mode", [
    "wrong-origin-commit", "http-redirect", "unapproved-host",
    "wrong-size", "wrong-server-identity", "truncated-body",
])
def test_fetch_fails_closed(mode, http_fixture, tmp_path):
    http_fixture.mode = mode
    with pytest.raises(DatasetDownloadError):
        fetch_hf_dataset(source("4" * 40), tmp_path, http_fixture.transport)
```

Also test pagination, duplicate tree entries, interruption leaving only an
untrusted `.part` file, retry replacing the part file, and a second verify pass
that performs no network call.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_real_dataset_fetch.py -q
```

Expected: import failure for `pipeline.real_dataset_fetch`.

- [ ] **Step 3: Implement tree resolution and safe streaming**

Use `urllib.request` with a custom redirect handler. The production host policy
is exactly:

```python
def _approved_download_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    return normalized == "huggingface.co" or normalized.endswith(".cdn.hf.co")
```

The origin URL is constructed from repository, immutable revision and encoded
relative path. Check `x-repo-commit` before following a redirect, strip
`Authorization`/`Cookie`, require HTTPS in production, stream to a sibling
`.part`, derive SHA/length while streaming, `fsync`, then `os.replace`. Never
store an expiring signed CDN URL in the lock.

- [ ] **Step 4: Implement the narrow CLI**

Exact interface:

```text
python scripts/fetch_real_dataset.py SOURCE_JSON WORKSPACE [--verify-only]
```

It writes `dataset-lock.json`, `dataset-receipt.json` and
`dataset-policy.json` under the supplied ignored workspace and prints the
source SHA, lock SHA and receipt SHA. Exit 2 means validation/download failure.

- [ ] **Step 5: Verify and commit Task 2**

```bash
python -m pytest tests/test_real_dataset.py tests/test_real_dataset_fetch.py -q
python -m ruff check pipeline/real_dataset_fetch.py scripts/fetch_real_dataset.py \
  tests/test_real_dataset_fetch.py
git diff --check
```

Commit only the three Task 2 paths with subject
`feat: fetch pinned real datasets safely`, then push `main`.

## Task 3: Capture revision, fresh COLMAP and frozen quality gate

**Files:**
- Create: `pipeline/real_scene_capture.py`
- Create: `tests/test_real_scene_capture.py`
- Modify: `pipeline/registration.py`
- Test: `tests/test_registration.py`
- Modify: `scripts/emit_registration_quality.py`
- Test: `tests/test_emit_registration_quality.py`

**Interfaces:**
- Consumes: verified dataset receipt (Task 2), `pipeline.ingest.ingest_all`,
  `prepare_capture_bundle`, `pipeline.registration.register`,
  `build_registration_quality_report`.
- Produces:
  `prepare_real_capture(source, source_root, run_root) -> PreparedRealCapture`,
  `run_real_sfm(capture, run_root, policy) -> RealSfmResult`.

- [ ] **Step 1: Write tests for selection and trust closure**

```python
def test_canary_selects_only_original_images(verified_poster_fixture, tmp_path):
    prepared = prepare_real_capture(
        verified_poster_fixture.source,
        verified_poster_fixture.root,
        tmp_path / "run",
    )
    assert prepared.capture_manifest.output_count == 100
    assert all("/images_2/" not in p for p in prepared.selected_paths)


def test_sfm_rejects_mock_even_when_counts_pass(monkeypatch, prepared, tmp_path):
    monkeypatch.setattr(
        "pipeline.real_scene_capture.register",
        lambda *a, **k: full_coverage_registration(engine="mock"),
    )
    result = run_real_sfm(prepared, tmp_path, poster_policy())
    assert result.quality.training_allowed is False
```

Also prove that the capture revision is derived from verified ingest bytes,
that a source mutation after receipt validation aborts before ingest, and that
a rejected/unknown quality report never creates a training bundle. Add a
two-model fixture where `sparse/1` is larger than `sparse/0` and assert the
registration poses, enumeration and training input all use model 1.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_real_scene_capture.py \
  tests/test_emit_registration_quality.py -q
```

- [ ] **Step 3: Implement capture selection and revision derivation**

`PreparedRealCapture` is a frozen dataclass containing only paths under the run
root plus verified models and SHAs. For HF sources, select regular files whose
parent is exactly `capture_subtree`; reject nested extras. Materialize them
into an absent staging directory, run `ingest_all`, then call
`prepare_capture_bundle` with:

```python
revision_id = "capture-" + sha256(ingest_manifest_bytes).hexdigest()[:32]
synthetic = False
```

This `synthetic=False` describes captured media bytes only and must not change
geometry trust.

- [ ] **Step 4: Run non-mock COLMAP and emit quality directly from verified bytes**

Call `register(capture.payload_root, engine="colmap",
workspace=run_root/"sfm/colmap")`. Use the
result bytes, capture revision manifest bytes and
`enumerate_sparse_models()` to build/validate the report. Amend
`emit_registration_quality.py` only so its capture loader accepts the same
canonical capture-revision path used here; do not add support for arbitrary
JSON-shaped manifests.

Update `colmap_register()` to convert every numeric mapper model to text,
enumerate them with the existing deterministic largest-image/point-count/index
rule, and parse the selected model rather than hardcoded `sparse/0`. Record the
selected model index and enumeration identity in registration evidence. The
same `SparseModelEnumeration` instance is passed into the quality builder and
training bundle so pose and coverage evidence cannot diverge.

- [ ] **Step 5: Verify and commit Task 3**

```bash
python -m pytest tests/test_real_scene_capture.py \
  tests/test_emit_registration_quality.py tests/test_registration_quality.py \
  tests/test_registration.py tests/test_studio_capture_revisions.py -q
python -m ruff check pipeline/real_scene_capture.py \
  pipeline/registration.py scripts/emit_registration_quality.py \
  tests/test_real_scene_capture.py
git diff --check
```

Commit the declared paths with subject
`feat: add real capture and SfM gate`, then push `main`.

## Task 4: Deterministic held-out split and canonical training bundle

**Files:**
- Create: `pipeline/real_scene_training.py`
- Create: `tests/test_real_scene_training.py`

**Interfaces:**
- Consumes: `PreparedRealCapture`, `RealSfmResult`.
- Produces:
  `build_held_out_split(capture, ratio=0.10) -> HeldOutSplit`,
  `build_training_job_bundle(capture, sfm, config, output_dir) -> TrainingJobBundle`,
  `verify_training_job_bundle(path) -> VerifiedTrainingJobBundle`.

- [ ] **Step 1: Write split and deterministic bundle tests**

```python
def test_canary_split_is_exact_and_content_ordered(capture_100):
    split = build_held_out_split(capture_100, ratio=0.10)
    assert len(split.train) == 90
    assert len(split.held_out) == 10
    assert not set(split.train) & set(split.held_out)
    assert split == build_held_out_split(capture_100.reversed(), ratio=0.10)


def test_bundle_is_byte_identical_across_roots(tmp_path, fixtures):
    one = build_training_job_bundle(*fixtures, output_dir=tmp_path / "one")
    two = build_training_job_bundle(*fixtures, output_dir=tmp_path / "two")
    assert one.bundle_sha256 == two.bundle_sha256
```

Add failures for duplicate `(sha, logical_id)`, held-out count drift, mock SfM,
quality rejection, capture/report SHA drift, archive traversal, symlink,
absolute path, duplicate ZIP member and modified bundle bytes.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_real_scene_training.py -q
```

- [ ] **Step 3: Implement explicit split identity**

Sort by `(payload.sha256, payload.logical_path)` and select the first
`round_half_up(count * ratio)` identities as held-out. `HeldOutSplit` stores
both capture logical paths and their SHAs. It is canonical JSON and is bound as
an input to the training request; filename ordering alone is never evidence.

- [ ] **Step 4: Build a deterministic ZIP job bundle**

The ZIP contains:

```text
bundle-manifest.json
capture/manifest.json
capture/payload/<selected original images>
sfm/registration.json
sfm/registration-quality-policy.json
sfm/registration-quality-report.json
sfm/sparse/0/{cameras,images,points3D}.{bin,txt}
training/held-out-split.json
training/operator-intent-config.yml
training/training-request.json
```

Use fixed ZIP timestamps/mode bits, sorted portable names and no host paths.
`bundle-manifest.json` binds every member SHA/length. Verification reopens the
archive and validates all nested contracts, not only ZIP SHA. The selected
COLMAP model is copied byte-for-byte to the canonical bundle `sparse/0` path;
the manifest records its original numeric model index and the enumeration SHA.

- [ ] **Step 5: Verify and commit Task 4**

```bash
python -m pytest tests/test_real_scene_training.py \
  tests/test_training_provenance.py tests/test_prepare_import_training.py -q
python -m ruff check pipeline/real_scene_training.py \
  tests/test_real_scene_training.py
git diff --check
```

Commit the two paths with subject
`feat: build content-addressed training bundles`, then push `main`.

## Task 5: Executor state machine and resume journal

**Files:**
- Create: `pipeline/training_executor.py`
- Create: `tests/test_training_executor.py`

**Interfaces:**
- Consumes: `VerifiedTrainingJobBundle`.
- Produces:
  `TrainingExecutor` protocol,
  `ExecutorJobRef`,
  `ExecutorObservation`,
  `ExecutorAttemptReceipt`,
  `RealSceneJournal`,
  `resume_decision(previous: ExecutorAttemptReceipt,
  current_request_sha256: str) -> Literal["reuse", "retry", "block-unknown"]`.

- [ ] **Step 1: Write state and resume tests**

```python
def test_lost_remote_job_is_unknown_not_failed():
    observation = normalize_poll_result(exit_code=None, reachable=False)
    assert observation.state == "unknown"


def test_resume_requires_every_identity_to_match(honest_attempt):
    changed = honest_attempt.model_copy(
        update={"training_config_sha256": "f" * 64},
    )
    assert resume_decision(changed, current_inputs()) == "retry"
```

Cover all transitions:
`not-started→running`, `running→succeeded|failed|unknown`,
`unknown→running|succeeded|failed`, and forbid `failed→succeeded` mutation of
the same attempt. Retry creates a new attempt id.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_training_executor.py -q
```

- [ ] **Step 3: Implement the protocol and immutable observations**

```python
class TrainingExecutor(Protocol):
    def prepare(self, bundle: VerifiedTrainingJobBundle) -> ExecutorJobBundle:
        raise NotImplementedError

    def submit(self, bundle: ExecutorJobBundle) -> ExecutorJobRef:
        raise NotImplementedError

    def poll(self, job: ExecutorJobRef) -> ExecutorObservation:
        raise NotImplementedError

    def fetch(
        self,
        job: ExecutorJobRef,
        destination: Path,
    ) -> ExecutorAttemptReceipt:
        raise NotImplementedError
```

Receipts bind executor kind, request SHA, dataset receipt SHA, config SHA,
trainer identity, job id, attempt id, state, observed timestamps, exit code,
stdout/stderr hashes and result-bundle hash. They never store credentials,
private hostnames, environment dumps or raw logs.

- [ ] **Step 4: Implement an atomic journal**

`RealSceneJournal` uses canonical LF JSON, absent-or-matching creation,
same-directory temporary file, file flush/fsync and `os.replace`. It validates
the entire previous journal before every write. A corrupt journal blocks
resume; it is never silently replaced.

- [ ] **Step 5: Verify and commit Task 5**

```bash
python -m pytest tests/test_training_executor.py -q
python -m ruff check pipeline/training_executor.py tests/test_training_executor.py
git diff --check
```

Commit only the declared paths with subject
`feat: add training executor state machine`, then push.

## Task 6: Verified local Brush preview executor

**Files:**
- Create: `pipeline/local_brush_executor.py`
- Create: `tests/test_local_brush_executor.py`
- Modify: `scripts/reconstruct_local.py`
- Test: `tests/test_reconstruct_local.py`

**Interfaces:**
- Implements `TrainingExecutor` from Task 5 for `executor_kind=local-brush`.
- Produces an existing `TrainingResult` whose trainer is `brush`, plus an
  `ExecutorAttemptReceipt` explicitly labelled `preview-only`.

- [ ] **Step 1: Write subprocess and provenance tests**

Stub Brush, feed a verified precomputed COLMAP bundle and assert:

```python
result = LocalBrushExecutor(config).run(bundle)
assert result.training_request.training_config.trainer_name == "brush"
assert result.receipt.quality_role == "preview-only"
assert result.training_result.training_status.state == "completed"
assert result.training_result.gpu_environment.cuda_version == "not-applicable"
```

Add failures for nonzero exit, exit zero without PLY, PLY mutation after
execution, log/config mismatch and trying to use the preview receipt as the
production training gate.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_local_brush_executor.py \
  tests/test_reconstruct_local.py -q
```

- [ ] **Step 3: Expose a stable no-import execution result from the local script**

Add `--stop-after-brush` and `--receipt-out` to
`scripts/reconstruct_local.py`. The receipt records the existing immutable
`trained.brush-export.ply`, Brush argv, exact binary SHA, timestamps,
returncode and log SHA. It must not run prepare/import when stopped.

- [ ] **Step 4: Implement the adapter**

The adapter invokes the script with `--precomputed-colmap`, `--resume` and
`--stop-after-brush`; derives `GpuEnvironment` honestly as wgpu/Metal with
`cuda_version="not-applicable"`; then calls `build_training_result` from real
PLY/config/log/input bytes. Its trust role remains preview-only regardless of
content closure.

- [ ] **Step 5: Verify and commit Task 6**

```bash
python -m pytest tests/test_local_brush_executor.py \
  tests/test_reconstruct_local.py tests/test_training_provenance.py -q
python -m ruff check pipeline/local_brush_executor.py \
  scripts/reconstruct_local.py tests/test_local_brush_executor.py
git diff --check
```

Commit the four paths with subject `feat: verify local Brush preview runs`,
then push.

## Task 7: Strict remote-shell Splatfacto executor

**Files:**
- Create: `pipeline/remote_shell_executor.py`
- Create: `cloud/prepare_real_scene_dataset.py`
- Modify: `cloud/train_3dgs_nerfstudio.sh`
- Modify: `cloud/ns_train_argv_schema.py`
- Modify: `pipeline/training_provenance.py`
- Create: `tests/test_remote_shell_executor.py`
- Create: `tests/test_prepare_real_scene_dataset.py`
- Test: `tests/test_cloud_argv_schema_contract.py`
- Test: `tests/test_p1_canary_e2e.py`
- Test: `tests/test_training_provenance.py`

**Interfaces:**
- Implements `TrainingExecutor` for `remote-shell-nerfstudio`.
- The remote job consumes only a verified prepared bundle and returns
  `training-result.json`, PLY, the dataparser transform and bounded logs.

- [ ] **Step 1: Write SSH argv, redaction and unknown-state tests**

```python
def test_submit_uses_strict_host_key_and_no_shell(monkeypatch, config, bundle):
    calls = capture_subprocess(monkeypatch)
    RemoteShellExecutor(config).submit(bundle)
    argv = calls.single_argv
    assert "StrictHostKeyChecking=yes" in argv
    assert config.private_key_path not in " ".join(calls.logged_argv)
    assert calls.used_shell is False


def test_unreachable_poll_returns_unknown(executor, job):
    executor.runner.returncode = 255
    assert executor.poll(job).state == "unknown"
```

Also reject newline/control characters in aliases/remote roots, host-key
fingerprint mismatch, changed job id, result traversal, over-size result and a
result whose request SHA differs from the submitted bundle.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_remote_shell_executor.py \
  tests/test_prepare_real_scene_dataset.py -q
```

- [ ] **Step 3: Implement prepared COLMAP conversion without rerunning SfM**

`cloud/prepare_real_scene_dataset.py` verifies the bundle, materializes
`images/` and `colmap/sparse/0`, calls the pinned Nerfstudio 1.1.5
`colmap_utils.colmap_to_json`, and then adds explicit `train_filenames` and
`test_filenames` from `held-out-split.json`. It writes
`orientation_override="none"` and verifies:

```python
assert set(meta["train_filenames"]) == expected_train
assert set(meta["test_filenames"]) == expected_held_out
assert not expected_train & expected_held_out
assert set(meta["train_filenames"]) | set(meta["test_filenames"]) == all_frames
```

No held-out filename may enter the training split. The COLMAP sparse bytes are
copied unchanged and hashed again.

- [ ] **Step 4: Add a production prepared-bundle mode to the cloud script**

The accepted path is:

```text
bash cloud/train_3dgs_nerfstudio.sh \
  --prepared-bundle /job/training-bundle.zip \
  --container-identity registry/image@sha256:<64-hex>
```

In this mode:

- never run `pip install`;
- require exact Nerfstudio 1.1.5 and record the container digest;
- never run COLMAP or `ns-process-data`;
- call `prepare_real_scene_dataset.py`;
- run Splatfacto with the request seed/steps and
  `nerfstudio-data --orientation-method none --center-method none
  --auto-scale-poses False --scale-factor 1.0`;
- bind `dataparser_transforms.json` and require its transform to be identity
  with scale 1.0;
- export the PLY and provenance manifests even on a diagnosed failure;
- return nonzero on trainer/export/evaluator failure.

Keep the old positional-input mode only as a documented manual path and mark
its receipt ineligible for real-scene acceptance.

Extend the existing training-provenance output-kind enum with
`dataparser_transform_json` and let `build_training_result` bind this one
additional output. Historical results remain valid for their old scope, but
the real-scene import gate requires the new binding.

- [ ] **Step 5: Implement submit/poll/fetch**

Use local `ssh`/`scp` binaries with argv arrays and an operator-owned
known-hosts file. Submission uploads to an absent directory keyed by bundle
SHA. Poll reads one bounded status JSON. Fetch downloads to an absent staging
directory, validates every result member, then atomically publishes it locally.

- [ ] **Step 6: Verify and commit Task 7**

```bash
python -m pytest tests/test_remote_shell_executor.py \
  tests/test_prepare_real_scene_dataset.py \
  tests/test_cloud_argv_schema_contract.py tests/test_p1_canary_e2e.py \
  tests/test_training_provenance.py -q
python -m ruff check pipeline/remote_shell_executor.py \
  cloud/prepare_real_scene_dataset.py tests/test_remote_shell_executor.py \
  tests/test_prepare_real_scene_dataset.py
bash -n cloud/train_3dgs_nerfstudio.sh
git diff --check
```

Commit the declared paths with subject
`feat: add strict remote Splatfacto execution`, then push.

## Task 8: Resume-safe `real-scene` runner and make targets

**Files:**
- Create: `pipeline/real_scene_runner.py`
- Create: `scripts/real_scene.py`
- Create: `tests/test_real_scene_runner.py`
- Modify: `make.py`
- Test: `tests/test_make_runner.py`

**Interfaces:**
- Consumes Tasks 1–7.
- Produces `run_real_scene(source_path, target, options) -> StageReceipt`.
- CLI targets: `fetch`, `sfm`, `train-preview`, `train-production`, `import`,
  `accept`, `serve`, `all`.

- [ ] **Step 1: Write orchestration and fail-stop tests**

```python
def test_all_stops_before_training_when_sfm_rejected(runner):
    runner.sfm_result = rejected_sfm()
    with pytest.raises(RealSceneBlocked, match="registration"):
        runner.run("all")
    assert runner.executor.submit_calls == 0


def test_resume_revalidates_bytes_not_file_existence(runner):
    runner.run("fetch")
    runner.workspace.joinpath("dataset/poster/images/frame.png").write_bytes(b"x")
    with pytest.raises(DatasetEvidenceError):
        runner.run("fetch", resume=True)


def test_production_import_requires_measured_control_points(runner):
    runner.source = production_source()
    with pytest.raises(RealSceneBlocked, match="control points"):
        runner.run("import")
```

Test every target's prerequisites, the `unknown` remote-state block, retry
attempt preservation, canary wrapper source binding and source-parameterized
production path. A production import must reject missing, fewer than four,
coplanar, non-finite or RMS > 0.25 m control-point evidence.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_real_scene_runner.py tests/test_make_runner.py -q
```

- [ ] **Step 3: Implement stage orchestration**

Use `.nantai-studio/real-scene/<dataset-id>/<source-sha-prefix>/`. Each stage
prints and records exact input and output identities. `--resume` calls the
stage validator before reuse. A failed or unknown prerequisite stops later
stages with the receipt path and reason.

- [ ] **Step 4: Implement cross-platform make.py argument parsing**

Both forms must work without shell-specific environment assignment:

```text
python make.py real-canary fetch
python make.py real-scene SOURCE=config/real-scene/private.json sfm
python make.py real-scene SOURCE=config/real-scene/private.json \
  MEDIA_ROOT=/private/capture RIGHTS=.nantai-studio/private/rights.json \
  CONTROL_POINTS=.nantai-studio/private/control-points.json \
  GEO_ORIGIN=31.2,121.5,4.0 import
```

`real-canary` always binds the committed poster source and rejects a `SOURCE`
override. `real-scene` requires exactly one `SOURCE=` token and one known
subtarget. A `local-capture` additionally requires runtime `MEDIA_ROOT=` and
`RIGHTS=` paths; their private absolute locations never enter portable
receipts, while their exact content SHAs must match the source record. For
`production-acceptance`, the `import` target calls the existing
`pipeline.alignment.align_registration` with the private control-point file,
fixed `max_rms_m=0.25` and explicit ENU origin before constructing SplatInput.
The resulting content-addressed Sim3 must appear in transform history; failure
blocks import without an unaligned fallback.

- [ ] **Step 5: Verify and commit Task 8**

```bash
python -m pytest tests/test_real_scene_runner.py tests/test_make_runner.py -q
python -m ruff check pipeline/real_scene_runner.py scripts/real_scene.py \
  tests/test_real_scene_runner.py make.py
python make.py help
git diff --check
```

Commit the declared paths with subject
`feat: orchestrate the real reconstruction path`, then push.

## Task 9: Import, chunking and artifact closure

**Files:**
- Create: `pipeline/real_scene_import.py`
- Create: `tests/test_real_scene_import.py`
- Modify: `scripts/prepare_import.py`
- Test: `tests/test_prepare_import_training.py`
- Test: `tests/test_reconstruction_artifact_integrity.py`

**Interfaces:**
- Consumes a verified production `TrainingResult`, PLY and SfM evidence.
- Produces `import_real_scene(run_root, output_root) -> RealSceneImportReceipt`.

- [ ] **Step 1: Write production-only import tests**

```python
def test_brush_preview_cannot_satisfy_production_import(bundle, tmp_path):
    with pytest.raises(RealSceneImportError, match="preview-only"):
        import_real_scene(bundle.with_brush_result(), tmp_path)


def test_canary_import_stays_arbitrary_and_unaligned(imported):
    assert imported.manifest["coordinate_contract"]["target_frame"]["units"] == "arbitrary"
    assert imported.manifest["provenance"]["geometry_usability"] == "preview-only"


def test_non_identity_nerfstudio_transform_blocks_import(bundle, tmp_path):
    bundle.write_dataparser_transform(scale=0.25)
    with pytest.raises(RealSceneImportError, match="dataparser transform"):
        import_real_scene(bundle, tmp_path)
```

Add PLY semantic failures for NaN position, zero quaternion, missing opacity,
non-contiguous SH, fewer than 100,000 Gaussians, result/request drift and
artifact SHA mismatch. Verify every Gaussian appears in exactly one chunk and
chunking does not change coordinates or trust.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_real_scene_import.py \
  tests/test_prepare_import_training.py \
  tests/test_reconstruction_artifact_integrity.py -q
```

- [ ] **Step 3: Implement the import adapter**

Validate training closure and require the bound dataparser transform to be
identity with scale 1.0. For the internal canary, consume the unaligned
registration. For a production source, require the runner's verified aligned
registration with metre units, RMS ≤ 0.25 m and an unbroken transform history.
Then run quaternion normalization on a copy, call
`scripts.prepare_import` with request/result/quality bindings, then call
`pipeline.reconstruct.reconstruct` with:

```python
reconstruct(
    photos_dir=capture_payload_root,
    out_dir=run_root / "scene/recon",
    web_dir=run_root / "scene/web",
    engine="import",
    splat_map=[splat_input],
    registration=registration,
    dedup_voxel=0.0,
    replace_margin=0.0,
    chunk_size_m=chunk_size,
)
```

For arbitrary-unit canary output, report the configured chunk value as source
units and preserve `source.units=arbitrary`; do not display metres in the
acceptance UI. Production capture chunk values are metres only after verified
metric alignment.

- [ ] **Step 4: Emit and validate an import receipt**

Bind normalized/source PLY SHAs, SplatInput SHA, registration SHA, recon
manifest SHA, integrity-report SHA, chunks SHA and every emitted artifact SHA.
Reopen all bytes before marking the stage succeeded.

- [ ] **Step 5: Verify and commit Task 9**

```bash
python -m pytest tests/test_real_scene_import.py \
  tests/test_prepare_import_training.py tests/test_reconstruct.py \
  tests/test_spatial_chunk.py tests/test_reconstruction_artifact_integrity.py -q
python -m ruff check pipeline/real_scene_import.py \
  scripts/prepare_import.py tests/test_real_scene_import.py
git diff --check
```

Commit only the declared paths with subject
`feat: close real scene import artifacts`, then push.

## Task 10: Held-out render evaluation contract

**Files:**
- Create: `pipeline/render_evaluation.py`
- Create: `cloud/evaluate_real_scene.py`
- Create: `tests/test_render_evaluation.py`
- Create: `tests/test_cloud_real_scene_evaluator.py`
- Create: `scripts/validate_render_evaluation.py`
- Modify: `cloud/train_3dgs_nerfstudio.sh`
- Modify: `pipeline/remote_shell_executor.py`

**Interfaces:**
- Produces:
  `RenderEvaluationPolicy`,
  `RenderFrameMetric`,
  `RenderEvaluationReport`,
  `validate_render_evaluation(policy, report, root) -> RenderDecision`.

- [ ] **Step 1: Write metric-boundary and tamper tests**

```python
def test_exact_metric_thresholds_pass(honest_report, root):
    report = honest_report(psnr=24.0, ssim=0.80, lpips=0.25, worst_psnr=18.0)
    assert validate_render_evaluation(POLICY, report, root).accepted is True


def test_report_boolean_cannot_override_bad_frame(honest_report, root):
    report = honest_report(psnr=30, ssim=.9, lpips=.1, worst_psnr=17.99)
    with pytest.raises(RenderEvaluationError, match="decision"):
        validate_render_evaluation(POLICY, report.claiming(accepted=True), root)
```

Also reject missing held-out ids, duplicate frames, any training frame, source
or render SHA drift, camera SHA drift, evaluator digest drift, resolution/crop/
colour-space/mask/SSIM-window/LPIPS-backbone drift and non-finite metrics.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
python -m pytest tests/test_render_evaluation.py -q
```

- [ ] **Step 3: Implement policy and fail-closed derivation**

The policy requires explicit resolution, crop, colour space, alpha/mask
handling, SSIM window parameters, LPIPS backbone and evaluator container
digest. The validator re-hashes source/render/camera bytes, recomputes mean and
minimum decisions from per-frame numeric results, and never trusts the
report-authored `accepted` field. The evaluator digest attests metric
production; local validation does not pretend to recompute LPIPS.

- [ ] **Step 4: Implement the pinned remote evaluator**

`cloud/evaluate_real_scene.py` loads the exact successful trainer config,
renders only the explicit `test_filenames` cameras, writes lossless RGB PNG
files, and computes per-frame PSNR, SSIM and LPIPS through the pinned
Nerfstudio/PyTorch environment. It receives the expected split SHA and
container digest, reopens every source/render byte, records each camera/source/
render SHA and emits numeric metrics without an authored acceptance boolean.

Amend the prepared-bundle path in `cloud/train_3dgs_nerfstudio.sh` to invoke
this evaluator only after successful training and before result publication.
Amend remote fetch validation to allow the declared render directory and
evaluation report only when their complete file set, sizes and SHAs match the
result-bundle manifest.

- [ ] **Step 5: Implement the validator CLI**

```text
python scripts/validate_render_evaluation.py \
  POLICY.json REPORT.json --root RUN_ROOT
```

Exit 0 accepted, 2 rejected/unknown/invalid. Print every failed threshold and
the exact report/policy SHAs.

- [ ] **Step 6: Verify and commit Task 10**

```bash
python -m pytest tests/test_render_evaluation.py \
  tests/test_cloud_real_scene_evaluator.py -q
python -m ruff check pipeline/render_evaluation.py \
  cloud/evaluate_real_scene.py scripts/validate_render_evaluation.py \
  tests/test_render_evaluation.py tests/test_cloud_real_scene_evaluator.py
bash -n cloud/train_3dgs_nerfstudio.sh
git diff --check
```

Commit only the declared paths with subject
`feat: validate held-out render quality`, then push.

## Task 11: Real-browser performance, human review and aggregate acceptance

**Files:**
- Create: `package.json`
- Create: `package-lock.json`
- Create: `pipeline/viewer_acceptance.py`
- Create: `pipeline/real_scene_acceptance.py`
- Create: `scripts/capture_viewer_acceptance.mjs`
- Create: `scripts/record_real_scene_review.py`
- Create: `tests/test_viewer_acceptance.py`
- Create: `tests/test_real_scene_acceptance.py`
- Create: `web/viewer/acceptance-probe.mjs`
- Create: `web/viewer/acceptance-probe.test.mjs`
- Modify: `web/viewer/main.js`
- Test: `web/viewer/index-contract.test.mjs`

**Interfaces:**
- Produces `ViewerPerformancePolicy`, `ViewerPerformanceReport`,
  `HumanVisualReview`, `RealSceneAcceptance`,
  `validate_real_scene_acceptance(report_path) -> AcceptanceDecision`.

- [ ] **Step 1: Write browser-policy and aggregate adversarial tests**

```python
def test_point_fallback_cannot_pass_full_3dgs_gate(report):
    report = report.model_copy(update={"representation": "dc-point-preview"})
    assert derive_viewer_decision(POLICY, report).accepted is False


def test_internal_canary_never_unblocks_release(complete_canary_report):
    decision = validate_real_scene_acceptance(complete_canary_report)
    assert decision.canary_accepted is True
    assert decision.production_release_allowed is False
```

Test exact performance boundaries, fewer than 600 measured frames, wrong
viewport, warmup drift, browser console errors, unhandled rejection, fallback,
loading timeout, overflow, missing reviewer, `unknown` disposition, screenshot
tamper, missing production rights, missing metric alignment and self-authored
aggregate booleans.

- [ ] **Step 2: Add a pure Viewer acceptance probe**

`acceptance-probe.mjs` owns a bounded 120+600 sample buffer and exposes no
mutating scene API. `main.js` feeds real animation-frame durations and signals
first full-3DGS interactivity only after the requested representation rendered.
Unit tests use a fake clock and prove fallback never emits the full-3DGS event.

- [ ] **Step 3: Implement the Playwright harness**

Pin Playwright in `package-lock.json`. The script launches a fresh browser
context with an empty HTTP cache, sets 1280×720, captures hardware/browser/
renderer identity, navigates to the accepted local Studio URL, visits three
content-addressed camera poses, waits for full 3DGS and collects the exact
warmup/measurement windows. It emits canonical JSON and returns 2 on any
threshold, console, rejection, timeout or overflow failure.

- [ ] **Step 4: Implement explicit human-review receipts**

The CLI accepts one disposition per required visual category and paths to
content-addressed screenshots/renders:

```text
python scripts/record_real_scene_review.py \
  --run-root RUN --reviewer NAME --policy POLICY.json \
  --disposition scene-envelope=accepted \
  --disposition floaters=accepted \
  --disposition view-dependent-colour=accepted \
  --disposition exposure-seams=accepted \
  --disposition transparent-surfaces=unknown \
  --disposition navigable-holes=accepted \
  --disposition fidelity-label=accepted \
  --screenshot POSE_ID=relative/path.png
```

A missing category becomes `unknown`; the CLI never defaults to accepted.

- [ ] **Step 5: Implement aggregate re-derivation**

`real-scene-acceptance.v1` contains only relative references, SHAs and derived
decisions. Validation reopens source/lock/receipt, capture, SfM, training,
import, evaluation, performance, alignment and human-review evidence. For
`internal-canary`, it may set `canary_accepted` but always sets
`production_release_allowed=False`. For `production-acceptance`, rights,
metric alignment and every gate are mandatory.

- [ ] **Step 6: Verify and commit Task 11**

```bash
npm ci
npx playwright install chromium
node --test web/viewer/acceptance-probe.test.mjs \
  web/viewer/index-contract.test.mjs
python -m pytest tests/test_viewer_acceptance.py \
  tests/test_real_scene_acceptance.py -q
python -m ruff check pipeline/viewer_acceptance.py \
  pipeline/real_scene_acceptance.py scripts/record_real_scene_review.py \
  tests/test_viewer_acceptance.py tests/test_real_scene_acceptance.py
git diff --check
```

Commit only the declared files with subject
`feat: add real scene acceptance gates`, then push.

## Task 12: Read-only Studio evidence presentation

**Files:**
- Create: `web/studio/real-scene-evidence.mjs`
- Create: `web/studio/real-scene-evidence.test.mjs`
- Modify: `pipeline/studio_server.py`
- Test: `tests/test_studio_server.py`
- Modify: `web/studio/model.mjs`
- Test: `web/studio/model.test.mjs`
- Modify: `web/studio/app.js`
- Test: `web/studio/index-contract.test.mjs`
- Modify: `web/studio/styles.css`

**Interfaces:**
- Server snapshot adds `real_scene` with stage states and evidence references.
- Studio derives labels but cannot derive or upgrade geometry trust from the
  canary acceptance result.

- [ ] **Step 1: Write server fail-closed projection tests**

```python
def test_tampered_acceptance_projects_invalid_not_success(project):
    write_acceptance(project, claim="accepted", tamper_training_result=True)
    snapshot = build_project_snapshot(project)
    assert snapshot["real_scene"]["decision"] == "invalid-evidence"
    assert snapshot["pipeline"]["reconstruct"]["trust"] != "verified"
```

Test missing, failed and unknown stages; internal canary accepted; production
accepted; unsafe/symlink path; malformed JSON; and that poster acceptance never
changes coordinate or Release trust.

- [ ] **Step 2: Implement a bounded server projection**

Add `_real_scene_snapshot(root)` that finds only the configured latest
acceptance pointer below `.nantai-studio/real-scene/`, resolves real paths,
invokes the aggregate validator and projects:

```json
{
  "role": "internal-canary",
  "decision": "accepted-canary",
  "production_release_allowed": false,
  "stages": [{"id": "sfm", "state": "succeeded"}],
  "reasons": ["internal-only source"],
  "report_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

Do not expose private paths, media names, hostnames, GPS, raw logs or control
points.

- [ ] **Step 3: Implement a pure presentation model**

`normalizeRealSceneEvidence(raw)` accepts only closed enums and returns labels
for `succeeded/failed/unknown/not-started`. It shows canary, production,
licensing, geometry and renderer gates independently. Invalid input becomes
`invalid-evidence`; it never defaults to passed.

- [ ] **Step 4: Render the Review evidence panel**

Add a compact stage rail, explicit “internal canary ≠ commercial acceptance”
message, rejection reasons and links only to safe local reports. Do not enable
general Studio writes or new job buttons in this subproject.

- [ ] **Step 5: Verify and commit Task 12**

```bash
python -m pytest tests/test_studio_server.py tests/test_studio_capabilities.py -q
node --test web/studio/real-scene-evidence.test.mjs \
  web/studio/model.test.mjs web/studio/index-contract.test.mjs
python -m ruff check pipeline/studio_server.py tests/test_studio_server.py
git diff --check
```

Commit the declared paths with subject
`feat: present real scene evidence in Studio`, then push.

## Task 13: Canary execution, drills, documentation and formal handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/manual/reconstruction-setup.md`
- Modify: `docs/real-data-workflow.md`
- Create: `docs/verification/2026-07-26-real-golden-path-canary.md`
- Modify: `tests/test_project_dependencies.py`
- Create: `tests/test_real_golden_path_docs.py`

**Interfaces:**
- Exercises every target produced by Tasks 1–12.
- Produces local ignored receipts and a committed verification report that
  contains hashes/results only, never dataset or private bytes.

- [ ] **Step 1: Run full repository gates before expensive acceptance work**

```bash
python -m pytest tests/ -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
python -m ruff check pipeline tests
```

Expected: zero failures. Stop and fix regressions before downloading or running
COLMAP.

- [ ] **Step 2: Execute local canary stages from an empty ignored workspace**

```bash
python make.py real-canary RUN_ID=production-v1-canary-20260726 fetch
python make.py real-canary RUN_ID=production-v1-canary-20260726 sfm
python make.py real-canary RUN_ID=production-v1-canary-20260726 train-preview
```

The runner creates an absent workspace for the run id and rejects collisions;
reuse requires an explicit resume invocation. Record wall times, exact
source/receipt/SfM/preview SHAs, COLMAP version and the frozen quality decision.
If the SfM gate rejects, stop; do not lower thresholds.

- [ ] **Step 3: Run corruption and unknown-remote drills**

On a disposable copy, flip one dataset byte and prove `fetch --resume` rejects.
Use the fake remote executor to make a submitted job unreachable and prove
`train-production --resume` blocks as unknown without resubmitting. Preserve
both failure receipts in the ignored run workspace and summarize hashes only.

- [ ] **Step 4: Execute real remote production training when the external host is available**

Required operator-owned inputs are an SSH alias, strict known-hosts file,
remote root and CUDA container image digest. Run:

```bash
python make.py real-canary RUN_ID=production-v1-canary-20260726 train-production
python make.py real-canary RUN_ID=production-v1-canary-20260726 import
python make.py real-canary RUN_ID=production-v1-canary-20260726 accept
python make.py real-canary RUN_ID=production-v1-canary-20260726 serve
```

Do not mark this step complete without a real successful remote result, at
least 100,000 semantically valid Gaussians, held-out metrics, three-pose browser
report and human review. If the host is not available, report this exact
external blocker while continuing all repo-local steps.

- [ ] **Step 5: Document exact achieved and blocked scope**

The verification document records:

- commit and tool/container identities;
- source/lock/receipt and every stage/report SHA;
- measured thresholds and pass/reject/unknown outcomes;
- internal-only licensing boundary;
- arbitrary/unaligned canary coordinate boundary;
- real external blockers;
- why subprojects 2–5 and the production acceptance capture remain required.

It must not describe the canary as commercial, metric, complete or a formal V1
release.

- [ ] **Step 6: Verify documentation and perform the final path-limited commit**

```bash
python -m pytest tests/test_real_golden_path_docs.py \
  tests/test_project_dependencies.py -q
git diff --check
git status --short --branch
```

Commit only the six declared documentation/test paths with subject
`docs: record real golden path canary`, push `main`, fetch again and verify
`HEAD == origin/main`. Confirm the weather-test SHA remains unchanged.

## Execution checkpoints

1. **Checkpoint A — downloadable truth:** Tasks 1–3. The Mac can fetch, verify,
   ingest and run the frozen real COLMAP gate.
2. **Checkpoint B — training truth:** Tasks 4–8. The same content-addressed
   request runs as honest local preview or strict remote production training.
3. **Checkpoint C — scene truth:** Tasks 9–10. The real PLY is imported,
   chunked and evaluated without coordinate or quality promotion.
4. **Checkpoint D — product truth:** Tasks 11–12. Browser, human and aggregate
   evidence are visible in Studio and remain fail-closed.
5. **Checkpoint E — measured canary:** Task 13. Real local stages run now;
   remote completion is reported as passed only after real CUDA evidence.

After each checkpoint, run the task-specific gates, inspect the complete diff,
commit only declared paths, push `main`, refetch, and verify the preserved
weather-test SHA before starting the next checkpoint.
