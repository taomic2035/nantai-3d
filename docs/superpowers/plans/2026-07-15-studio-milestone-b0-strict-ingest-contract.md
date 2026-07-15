# Studio Milestone B0 Strict Ingest Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current descriptive `ingest_manifest.json` prototype into a strict, portable, machine-verifiable staged artifact contract that Milestone B can safely validate before publishing `photos/`.

**Architecture:** `pipeline.ingest_manifest` owns immutable schema-v1 models, content-addressed session IDs, path/hash/field invariants, and a disk verifier. `pipeline.ingest` owns deterministic extraction into a fresh output root, stable-input checks, truthful EXIF/GPS capture, and fail-fast write handling. The contract remains independent of alignment/reconstruction schemas so incomplete measured-geometry work is not pulled into B0.

**Tech Stack:** Python 3.11+, Pydantic v2, Pillow/exifread, OpenCV, hashlib, pytest.

---

## Scope and file boundary

- Modify `pipeline/ingest.py`: deterministic fresh-output ingestion, stable source fingerprints, write/error handling.
- Create/replace `pipeline/ingest_manifest.py`: strict schema, portable GPS observation, and artifact verifier.
- Create/replace `tests/test_ingest_manifest.py`: schema, adversarial filesystem, input-race, and happy-path coverage.
- Modify `tests/test_reconstruct.py` only if its existing public `ingest_all()` integration requires an assertion update.
- Create `docs/verification/2026-07-15-studio-milestone-b0.md`: current-gate evidence and remaining B1 dependency.
- Do not stage or edit `pipeline/alignment.py`, `pipeline/recon_schema.py`, `pipeline/gaussian_scene.py`, their tests, or HANDOFF work.

### Task 1: Freeze the successful artifact schema

**Files:**
- Modify: `pipeline/ingest_manifest.py`
- Modify: `tests/test_ingest_manifest.py`

- [x] **Step 1: Write failing schema tests**

Add parameterized tests proving rejection of schema versions other than literal `1`, non-finite or out-of-range parameters, absolute/backslash/empty/`.`/`..` paths, malformed or uppercase SHA-256, non-positive byte counts, negative frame indices, contradictory photo/video fields, duplicate source/output paths, inconsistent output totals, non-canonical session IDs, and naive timestamps.

```python
@pytest.mark.parametrize("bad", [0.0, float("nan"), float("inf"), 30.01])
def test_ingest_params_reject_invalid_fps(bad):
    with pytest.raises(ValidationError):
        IngestParams(fps=bad, max_frames=1, blur_threshold=0, max_long_edge=256)

@pytest.mark.parametrize("path", ["", "/abs.jpg", "../escape.jpg", "a\\b.jpg", "a/./b.jpg"])
def test_source_paths_are_portable_relative_posix(path):
    with pytest.raises(ValidationError):
        successful_photo(source_path=path)
```

- [x] **Step 2: Run the schema tests and verify RED**

Run: `python -m pytest -q tests/test_ingest_manifest.py -k "reject or portable or contradictory or total or session"`

Expected: failures showing the prototype accepts invalid schema, paths, hashes, parameters, or combinations.

- [x] **Step 3: Implement strict immutable models**

Use `Literal[1]`, `ConfigDict(extra="forbid", frozen=True)`, finite numeric bounds, lowercase 64-hex SHA validation, timezone-aware ISO timestamps, and a portable relative-POSIX path validator. Replace `GeoAnchor` with a local evidence type so missing altitude remains unknown.

```python
class GpsObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    altitude_m: float | None = None

class IngestManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    session_id: str = Field(pattern=r"^ingest-[0-9a-f]{64}$")
    created_utc: datetime
    tool: Literal["pipeline.ingest"] = "pipeline.ingest"
    params: IngestParams
    sources: tuple[SourceRecord, ...]
    total_output_frames: int = Field(ge=1)
```

The manifest model validator must enforce unique sources and outputs, exact total count, kind-specific field combinations, photo output hash/size equality with its source, and `session_id == derive_session_id(params, sources)`.

- [x] **Step 4: Run schema tests and verify GREEN**

Run: `python -m pytest -q tests/test_ingest_manifest.py -k "schema or reject or portable or contradictory or total or session"`

Expected: all selected tests pass.

### Task 2: Add a strict staged-artifact verifier

**Files:**
- Modify: `pipeline/ingest_manifest.py`
- Modify: `tests/test_ingest_manifest.py`

- [x] **Step 1: Write failing disk-verifier tests**

Build one valid staged tree, then independently mutate it to cover: missing manifest, oversized/invalid JSON, symlink in the stage, missing output, extra undeclared output, output size/hash mismatch, source size/hash mismatch, and a source added/removed after manifest creation.

```python
def test_verify_rejects_extra_undeclared_file(valid_stage):
    (valid_stage.output / "stale.jpg").write_bytes(b"stale")
    with pytest.raises(IngestArtifactError, match="undeclared"):
        verify_ingest_artifact(valid_stage.output, input_dir=valid_stage.input)
```

- [x] **Step 2: Run verifier tests and verify RED**

Run: `python -m pytest -q tests/test_ingest_manifest.py -k "verify"`

Expected: import/attribute failures because `verify_ingest_artifact` does not exist.

- [x] **Step 3: Implement verifier with exact file-set equality**

```python
def verify_ingest_artifact(stage_dir: str | Path, *, input_dir: str | Path) -> IngestManifest:
    stage = require_real_directory(stage_dir)
    source = require_real_directory(input_dir)
    manifest = load_bounded_manifest(stage / MANIFEST_FILENAME)
    declared = {mapping.output_path for item in manifest.sources for mapping in item.outputs}
    actual = scan_regular_files(stage) - {MANIFEST_FILENAME}
    if actual != declared:
        raise IngestArtifactError("declared output set does not match staged files")
    verify_every_source_and_output_size_and_sha(manifest, source, stage)
    return manifest
```

Reject symlinks at any level, non-regular files, manifest JSON larger than 4 MiB, and any disk/manifest mismatch. Return the validated model only after every check succeeds.

- [x] **Step 4: Run verifier tests and verify GREEN**

Run: `python -m pytest -q tests/test_ingest_manifest.py -k "verify"`

Expected: all verifier tests pass.

### Task 3: Make ingest deterministic and fail closed

**Files:**
- Modify: `pipeline/ingest.py`
- Modify: `tests/test_ingest_manifest.py`
- Test: `tests/test_reconstruct.py::TestVideoIngest::test_video_frames_extracted`

- [x] **Step 1: Write failing execution tests**

Cover a non-empty output directory, nested duplicate basenames, two videos with the same stem but different suffixes, mocked `cv2.imwrite=False`, an unreadable/open-failed video, and a source changed or added while ingest runs.

```python
def test_ingest_requires_fresh_output(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "old.jpg").write_bytes(b"old")
    with pytest.raises(IngestError, match="fresh output"):
        ingest_all(tmp_path / "input", output)

def test_imwrite_false_aborts_without_manifest(monkeypatch, video_input, tmp_path):
    monkeypatch.setattr(ingest.cv2, "imwrite", lambda *args, **kwargs: False)
    with pytest.raises(IngestError, match="write"):
        ingest_all(video_input, tmp_path / "stage", blur_threshold=0)
    assert not (tmp_path / "stage" / MANIFEST_FILENAME).exists()
```

- [x] **Step 2: Run execution tests and verify RED**

Run: `python -m pytest -q tests/test_ingest_manifest.py -k "fresh or deterministic or imwrite or changed or added or open_failed"`

Expected: failures because existing ingest merges outputs, ignores write failure, and fingerprints after processing.

- [x] **Step 3: Implement stable source snapshots and deterministic paths**

Capture the complete candidate source map `{relative_path: (size, sha256)}` before processing and compare it with a fresh scan after processing. Abort on any add/remove/change. Use source-relative photo paths and `<source-name-with-suffix>.frames/frame_XXXXXX.jpg` for videos. Require output to be absent or an empty real directory.

```python
before = fingerprint_inputs(input_dir)
require_fresh_output(output_dir)
for relative_path, fingerprint in before.items():
    process_one(relative_path, fingerprint)
after = fingerprint_inputs(input_dir)
if after != before:
    raise IngestError("input changed while ingest was running")
```

Check `cv2.imwrite(...) is True`, require at least one output per accepted video, propagate open/decode/write failures as `IngestError`, and never write a success manifest after any source failure. Photo copies must be rehashed and match the captured source fingerprint.

- [x] **Step 4: Build and verify before writing the manifest**

Construct strict records from pre-processing fingerprints and measured outputs, derive the full 64-hex session ID, atomically write the manifest through a sibling temporary file, then call `verify_ingest_artifact(output_dir, input_dir=input_dir)`. Delete the temporary file on failure; a failed run may leave staging payload but never a valid success manifest.

- [x] **Step 5: Run ingest integration tests and verify GREEN**

Run:

```powershell
python -m pytest -q tests/test_ingest_manifest.py
python -m pytest -q tests/test_reconstruct.py::TestVideoIngest::test_video_frames_extracted
```

Expected: all tests pass.

### Task 4: Independent review and repository gates

**Files:**
- Create: `docs/verification/2026-07-15-studio-milestone-b0.md`

- [x] **Step 1: Ask the Opus architecture role to review the B0 diff**

Review only Critical/Important issues around provenance truth, path containment, symlink handling, input races, deterministic mapping, and verifier completeness. Do not merge alignment or job-kernel scope.

- [x] **Step 2: Run fresh repository gates**

Run:

```powershell
python -m pytest -q
node --test web/studio/*.test.mjs
node --test web/viewer/*.test.mjs
git diff --check
```

Expected: all tests pass; the eight capability-based Windows symlink skips remain explicitly reported rather than treated as POSIX evidence.

- [x] **Step 3: Record verification and remaining boundary**

Document test counts, supported/unsupported filesystem evidence, and that B0 creates no HTTP write route, subprocess job service, or formal publication. State that B1 may consume `verify_ingest_artifact` but may not weaken it.

- [x] **Step 4: Stage only B0 files and commit**

Verify the exact staged file list, then commit on `main` with:

```text
feat: make ingest artifacts publication-safe

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

## Self-review

- Spec coverage: B0 closes every ingest-manifest prerequisite identified by the approved safe-jobs design and Opus audit: strict schema, stable inputs, deterministic fresh staging, truthful GPS altitude, write failures, and manifest/disk verification.
- Scope: no alignment, reconstruction, Gaussian, HTTP job, ledger, cancellation, or publication implementation is included.
- Placeholder scan: no TBD/TODO or unspecified implementation step remains.
- Type consistency: session IDs use `ingest-` plus 64 lowercase hex; all source/output hashes are required lowercase SHA-256; GPS altitude is optional evidence and never becomes a `GeoAnchor` here.
