# Cloud GPU Training Provenance Handshake — Design

> Date: 2026-07-23
> Owner: GLM lane (HANDOFF-GLM-002 Task 3, design-only)
> Status: Design — awaiting Codex review before any runtime implementation
> Depends on: Task 2 `RegistrationQualityReport`
  ([spec](../plans/2026-07-23-registration-sfm-quality-policy.md))

## Goal

Define a canonical **training provenance handshake** that binds a cloud-GPU-trained
3DGS PLY to its verified inputs (capture, SfM quality), trainer identity, training
configuration, GPU environment, and output artefacts — via content-addressed JSON
manifests that a local validator can verify without trusting operator or cloud
claims.

Today, `cloud/train_3dgs_nerfstudio.sh` produces a `point_cloud.ply` with **zero
provenance**: no input SHA, no trainer version, no config snapshot, no seed, no
CUDA environment, no log hash. The PLY is imported as an opaque black box via
`prepare_import.py`, which honestly labels it `sfm-local` / `preview-only`. But
nothing prevents an operator from later swapping the PLY, or claiming a different
trainer, or hiding a failed run. This design closes that gap by making the
training run **content-addressed and locally verifiable**.

**Trust boundary:** a verified handshake only proves content closure — the PLY,
config, logs, and environment are mutually consistent and bound to verified
inputs. It does NOT prove the model is visually perfect, that the photos are
real, or that the geometry is metric. It is a necessary but not sufficient
condition for trusting a trained model.

## Current evidence

### What exists today

- `cloud/train_3dgs_nerfstudio.sh`: 60-line bash script. Re-runs COLMAP via
  `ns-process-data`, trains `ns-train splatfacto`, exports via `ns-export`.
  Produces `point_cloud.ply` + implicit `config.yml`. **No manifest, no SHA,
  no seed, no env capture.**
- `SplatInput` (`pipeline/recon_schema.py`): carries `path` + `source_frame` +
  optional `transform`. No trainer field, no training-run-id, no config, no seed.
- `reconstruct --engine import`: records `artifact_sha256` (PLY bytes SHA) +
  `source_frame` in ancestry. No trainer/config/seed/log binding.
- `FrameProvenance`: enum `MEASURED / SYNTHETIC / SFM / UNKNOWN`. No `TRAINED`
  value — a trained PLY is labeled `SFM` (honest: it is a reconstruction, not a
  survey).

### What is missing (confirmed by codebase search)

- No `TrainingRequest`, `TrainingResult`, or `training_manifest` schema anywhere.
- No trainer version pinning.
- No config snapshot.
- No random seed recording (`ns-train` uses its default seed).
- No GPU/CUDA environment capture.
- No training log SHA.
- No input-to-output content-addressed binding.

### The nerfstudio vs Brush asymmetry

- **nerfstudio (cloud)**: re-runs COLMAP via `ns-process-data` and re-centers /
  re-scales / re-orients the scene → output is **NOT** in the local sparse
  coordinate system → `splat_provenance` geometric check is **NOT applicable**
  (canary measured ratio=0.00x).
- **Brush (local)**: consumes the COLMAP workspace directly → preserves the
  coordinate system → `splat_provenance` IS applicable.

The handshake must accommodate both paths honestly and not pretend the same
geometric verification applies to both.

## Considered approaches

### A. Embed training provenance into `CoordinateFrame.evidence`

Rejected. The `evidence` tuple is free-form strings — it cannot be validated,
content-addressed, or tamper-detected. Stuffing a JSON blob into a string is
exactly the anti-pattern that `colmap.registration.coverage.v1=` established
and that Task 2 is replacing with structured fields. The handshake must be a
separate, schema-validated artefact.

### B. Extend `SplatInput` with training fields

Rejected for the same reason as Task 2's Approach A: `SplatInput` is part of
the coordinate trust root (`splat-input.json`), and embedding mutable training
metadata into it would conflate "what the PLY declares about its frame" with
"what the training run declared about itself." The handshake should be a
separate manifest that `SplatInput` references by SHA.

### C. Separate `training-request.json` + `training-result.json` manifests

Adopted. Two canonical JSON files, each content-addressed, each locally
validatable. The request is issued before training (binds inputs + intent);
the result is produced after training (binds outputs + environment + logs).
The local validator checks closure: result's inputs must match request's
inputs; result's output PLY SHA must match the actual PLY bytes.

## Architecture

### New module: `pipeline/training_provenance.py`

All new types live in a single new module. No changes to
`cloud/train_3dgs_nerfstudio.sh`, `pipeline/recon_schema.py`, or
`pipeline/reconstruct.py` in this design phase.

### `TrainingRequest`

Issued before training. Binds the verified inputs and the operator's intent.

```python
class TrainingInputBinding(FrozenModel):
    """Content-addressed binding to a verified input artefact."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["capture_manifest", "registration_json",
                           "registration_quality_report", "sparse_model_dir"]
    artifact_sha256: str  # 64-hex SHA-256 of the artefact's canonical bytes
    artifact_path: str   # relative path at issue time (for human readability)
    artifact_size_bytes: int = Field(ge=0)


class TrainingConfig(FrozenModel):
    """The training configuration that the operator intends to use."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    trainer_name: Literal["nerfstudio-splatfacto", "brush", "gsplat", "inria"]
    trainer_version: str  # e.g. "nerfstudio 0.3.4", "brush 0.3.0"
    max_resolution: int = Field(ge=64)
    total_steps: int = Field(ge=1)
    export_every: int | None = None  # Brush-specific checkpoint interval
    random_seed: int  # explicit, no defaults — operator must state it
    extra_config: tuple[tuple[str, str], ...] = Field(default=())
    # extra_config is a sorted tuple of (key, value) pairs for trainer-specific
    # flags not covered above; canonicalised by sort to ensure deterministic SHA


class TrainingRequest(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str  # operator-supplied unique id
    created_at_utc_iso: str  # ISO 8601 UTC timestamp
    input_bindings: tuple[TrainingInputBinding, ...] = Field(min_length=1)
    training_config: TrainingConfig
    expected_output_format: Literal["inria-3dgs-ply"]

    # Content-addressed identity
    @property
    def request_canonical_sha256(self) -> str: ...
```

**Why `random_seed` is required (no default):** `ns-train splatfacto` currently
uses its default seed. A training run without a recorded seed is not
reproducible and therefore not auditable. The operator must explicitly state the
seed — even if it's nerfstudio's default `42`, it must be declared.

**Why `input_bindings` is `min_length=1`:** a training request with no bound
inputs is unverifiable. At minimum, a capture manifest or registration JSON
must be bound. If a `RegistrationQualityReport` (Task 2) exists, it should be
bound too — and its `training_allowed` field is a prerequisite.

### `TrainingResult`

Produced after training. Binds the actual outputs, environment, and logs.

```python
class GpuEnvironment(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gpu_name: str  # e.g. "NVIDIA GeForce RTX 3060"
    gpu_memory_mb: int = Field(ge=0)
    cuda_version: str  # e.g. "11.8"
    driver_version: str  # e.g. "535.104.05"


class TrainingOutputBinding(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["trained_ply", "training_config_yml",
                           "training_log", "ns_process_data_dir"]
    artifact_sha256: str
    artifact_path: str
    artifact_size_bytes: int = Field(ge=0)
    # PLY-specific properties (only for trained_ply)
    gaussian_count: int | None = None
    sh_degree: int | None = None


class TrainingStatus(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["completed", "failed", "interrupted"]
    exit_code: int
    error_message: str | None = None  # required if state != "completed"


class TrainingResult(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_canonical_sha256: str  # binds to the TrainingRequest
    result_id: str  # operator-supplied unique id
    started_at_utc_iso: str
    finished_at_utc_iso: str

    # Actual inputs used (must match request's input_bindings by SHA)
    actual_input_shas: tuple[str, ...]  # SHAs of inputs actually consumed

    # Actual trainer + config used
    actual_trainer_name: str
    actual_trainer_version: str
    actual_config_sha256: str  # SHA of the config actually used (may differ from request)

    # Environment
    gpu_environment: GpuEnvironment

    # Outputs
    output_bindings: tuple[TrainingOutputBinding, ...]
    primary_ply_sha256: str  # the main trained PLY's SHA (must appear in output_bindings)

    # Status
    training_status: TrainingStatus

    # Training log
    training_log_sha256: str  # SHA of the full training log
    training_log_tail_lines: int = Field(default=50, ge=0)

    # Content-addressed identity
    @property
    def result_canonical_sha256(self) -> str: ...
```

### Content-addressed identity

Both `TrainingRequest` and `TrainingResult` use the same canonicalisation:

```python
canonical_bytes = model_dump_json(sort_keys=True, ensure_ascii=True).encode("utf-8")
sha256 = hashlib.sha256(canonical_bytes).hexdigest()
```

Files written with `newline="\n"` (forced LF) for cross-OS reproducibility,
same as `registration.json`.

### Validation contract

`validate_training_provenance(result, request, actual_ply_bytes)`:

1. **Input closure:** every SHA in `result.actual_input_shas` must match a SHA
   in `request.input_bindings`. Any extra or missing SHA → `ValueError`.
2. **Request binding:** `result.request_canonical_sha256` must equal
   `request.request_canonical_sha256`. Mismatch → `ValueError`.
3. **PLY binding:** `result.primary_ply_sha256` must appear in
   `result.output_bindings` as a `trained_ply` artefact. Mismatch → `ValueError`.
4. **PLY bytes:** `sha256(actual_ply_bytes)` must equal
   `result.primary_ply_sha256`. Mismatch → `ValueError` (tamper detection).
5. **Status consistency:** if `training_status.state != "completed"`, then
   `primary_ply_sha256` must be empty or absent, and `error_message` must be
   non-empty. A failed run cannot claim a valid PLY.
6. **Config consistency:** `result.actual_config_sha256` must equal
   `sha256(actual_config_bytes)` (tamper detection on the config itself), and
   must equal `request.requested_config_sha256` unless
   `TrainingDriftPolicy.allow_config_drift=True` (defaults `False` — drift is
   **rejected**, not merely noted). The cloud script avoids drift by binding
   the **same** operator-intent `config.yml` to both request and result
   (REVIEW-CODEX-023 P0 fix); nerfstudio's internally generated `config.yml`
   is a diagnostic artefact, not the provenance contract config. An explicit
   `allow_config_drift=True` opt-in exists for cases where the trainer
   legitimately overrides flags, but it is never the default.

### Trust derivation

`derive_training_trust(result, request, registration_quality_report)`:

```python
training_trust = TrainingTrust(
    content_closed=True,          # validate_training_provenance passed
    inputs_verified=True,         # all input SHAs match verified artefacts
    registration_quality_passed=registration_quality_report.training_allowed,
    trainer_identified=True,      # trainer_name + version are non-empty
    seed_recorded=True,           # random_seed is present and non-None
    log_bound=True,               # training_log_sha256 is present
    environment_captured=True,    # gpu_environment is fully populated
)
# training_trust.is_trustworthy = all of the above
# But is_trustworthy=True still does NOT imply metric/aligned/real-photos.
```

**Fail-closed rules (`is_trustworthy=False`):**
- `content_closed=False` (SHA mismatch / missing binding)
- `registration_quality_passed=False` (Task 2's `training_allowed` is False)
- `training_status.state != "completed"`
- `trainer_identified=False` (empty trainer name/version)
- `seed_recorded=False` (seed is None or absent)
- `log_bound=False` (no log SHA)

### Relationship to existing import path

The handshake does NOT replace `SplatInput` or `prepare_import`. Instead:

1. Operator runs `cloud/train_3dgs_nerfstudio.sh` (now modified to emit
   provenance manifests) which emits `training-request.json` +
   `training-result.json`.
2. Operator runs `prepare_import.py --training-result training-result.json
   --training-request training-request.json`.
3. `prepare_import` calls `validate_training_provenance()` for content closure
   and applies a **three-tier evidence** system (P0.3):
   - **Trusted prefix** (`training_provenance.v1=<result_sha>`): content closed
     AND registration quality accepted AND trainer identified. Requires
     `--registration-quality-report` + `--registration-json` +
     `--registration-quality-policy` all present.
   - **Content-only receipt** (`training_content_closed.v1=<result_sha>`):
     content closed but no registration quality, or registration quality
     rejected, or trainer drifted. Proves the PLY/config/log are mutually
     consistent but NOT that the SfM coverage is adequate.
   - **No evidence**: verification failed; fail-closed unless
     `--allow-unverified-training` is passed (development only).
4. In all cases, the evidence does NOT change the frame's `metric_status` or
   `geo_aligned` (the PLY is still `sfm-local` / `preview-only` until alignment
   evidence is applied).

### nerfstudio vs Brush handling

The handshake accommodates both:

- **nerfstudio (cloud):** `trainer_name="nerfstudio-splatfacto"`. The
  `ns_process_data_dir` output binding captures the re-run COLMAP output. The
  handshake notes that `splat_provenance` geometric check is NOT applicable
  (nerfstudio re-centers/rescales).
- **Brush (local):** `trainer_name="brush"`. No `ns_process_data_dir` binding.
  The handshake notes that `splat_provenance` IS applicable (Brush preserves
  workspace coords).

This distinction is informational (recorded in the result), not enforced —
the handshake does not run geometric checks itself; it only records enough
metadata for downstream code to know which checks are applicable.

## Canonical JSON contract

- All models: `extra="forbid"`, frozen.
- Canonical bytes: `model_dump_json(sort_keys=True, ensure_ascii=True)`,
  LF newlines.
- SHA-256 fields: validated as 64-hex (same `_require_64_hex_sha` pattern as
  `production_journal.py` — replicated, not imported cross-domain).
- Timestamps: ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`), no timezone offsets.

## Canary plan

The canary proves the **mechanism** works, not that cloud training is real:

1. Build a synthetic `TrainingRequest` with a mock capture manifest SHA.
2. Build a synthetic `TrainingResult` that closes against the request.
3. Write both to disk, reload, validate → passes.
4. Tamper with `primary_ply_sha256` → validation fails.
5. Tamper with `actual_input_shas` → input closure fails.
6. Set `training_status.state="failed"` but keep `primary_ply_sha256` non-empty
   → status consistency fails.
7. Set `trainer_name=""` → `trainer_identified=False` → `is_trustworthy=False`.
8. Set `random_seed=None` → `seed_recorded=False` → `is_trustworthy=False`.
9. Provide a `RegistrationQualityReport` with `training_allowed=False` →
   `registration_quality_passed=False` → `is_trustworthy=False`.

A synthetic canary cannot be used as real-cloud-training acceptance.

## What this design does NOT do

- ~~Does not modify `cloud/train_3dgs_nerfstudio.sh`.~~ **Updated (P1):** the
  cloud script now emits `training-request.json` + `training-result.json` via
  `scripts/emit_training_provenance.py`. See `cloud/train_3dgs_nerfstudio.sh`.
- Does not modify `pipeline/recon_schema.py` (`SplatInput`, `CoordinateFrame`).
- ~~Does not modify `pipeline/reconstruct.py` or `scripts/prepare_import.py`.~~
  **Updated (P0.3/P1):** `scripts/prepare_import.py` now consumes training
  provenance + registration quality and applies the three-tier evidence system.
  `scripts/emit_registration_quality.py` (P1) emits quality reports from COLMAP
  sparse dirs. `pipeline/reconstruct.py` is unchanged.
- Does not define what "good enough" training looks like — only that the
  training run is content-closed and bound to verified inputs.
- Does not prove the PLY is visually correct or that the geometry is metric.
- Does not run geometric consistency checks (`splat_provenance`) — it only
  records metadata that tells downstream code whether such checks are applicable.
- Does not touch Studio/Viewer.

## Precedent alignment

| Concern | Existing precedent | Training handshake |
|---|---|---|
| Content-addressed identity | `FrameTransform.transform_id` (`xf-<sha256[:20]>`) | `request_canonical_sha256` / `result_canonical_sha256` |
| Prefixed evidence string | `sim3.alignment.v1=...` | `training_provenance.v1=<result_sha>` |
| Separate policy/report | `production_quality_gates.py` | `TrainingRequest` / `TrainingResult` |
| Fail-closed SHA validation | `_require_64_hex_sha` in `production_journal.py` | Replicated validator in `training_provenance.py` |
| Frozen + `extra="forbid"` | `RegistrationQualityPolicy` (Task 2) | All handshake models |
| LF canonical JSON | `registration.json` | `training-request.json` / `training-result.json` |
