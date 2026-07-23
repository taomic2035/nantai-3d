# Registration SfM Quality Policy — Design

> Date: 2026-07-23
> Owner: GLM lane (HANDOFF-GLM-002 Task 2, design-only)
> Status: Implemented — Codex reviewed (REVIEW-CODEX-022); P0 findings fixed; P0.3 + P1 callers done

## Goal

Define an independent, configurable **registration quality policy** that separates
three currently-conflated states into distinct machine-verifiable outcomes:

1. **`invocation_succeeded`** — the SfM engine ran and produced a result (vs. crashed/timeout).
2. **`quality_accepted`** — the result meets an explicit operator-supplied coverage policy.
3. **`training_allowed`** — the result is good enough to feed into 3DGS training.

Today, `pipeline/registration.py::colmap_register()` only fail-closes on engine
crash/timeout or missing `sparse/0`. A result like **2/20 registered images**
(FEEDBACK-HANDOFF-OPUS-011) is accepted as a valid `RegistrationResult` with a
`logger.warning` — no gate stops it from flowing into downstream training. This
design closes that gap.

**Trust boundary:** a `training_allowed=True` report only proves the registration
satisfies a coverage policy. It does NOT prove the photos are real, the camera
coverage is geometrically sufficient for 3DGS, or the scale is metric. It is a
necessary but not sufficient condition for training.

## Current evidence

### What exists today

- `RegistrationResult` (`pipeline/recon_schema.py`): carries `engine`,
  `sessions`, `poses`, `alignment_status`. No quality field.
- `colmap_register()` (`pipeline/registration.py`): runs the four-stage COLMAP
  pipeline, builds a transient `coverage` dict, serializes it as an **evidence
  string** (`colmap.registration.coverage.v1={...}`) stuffed into
  `pose_frame.evidence`. Emits `logger.warning` on partial coverage — no
  enforcement.
- **Sparse model selection**: hardcoded `sparse/"0"` (line 587-597). If COLMAP's
  mapper produces multiple connected-component models (`sparse/0`, `sparse/1`, …),
  only `0` is read; others are silently discarded. The directory name `"0"` is
  implicitly treated as quality proof — the exact anti-pattern this design must
  eliminate.

### What is missing (confirmed by codebase search)

- No `RegistrationQualityPolicy`, `RegistrationQualityReport`, or any quality
  gate type exists anywhere.
- No `invocation_succeeded`, `quality_accepted`, or `training_allowed` fields.
- No structured per-session registration outcome on `RegistrationResult` (only
  an ephemeral dict serialized to a string).
- No sparse-model enumeration, selection, or largest-connected-model-share
  calculation.
- The existing `production_quality_gates.py` (synthetic-village post-render
  domain) is the **closest structural precedent** — frozen pydantic models,
  content-addressed policy SHA, separate POLICY/REQUEST/DECISION/REPORT schema
  constants — but operates in a completely separate domain (camera-frame depth/
  semantic/sky dominance, not SfM registration).

### The 2/20 lesson (FEEDBACK-HANDOFF-OPUS-011)

COLMAP 4.1.0 genuinely ran on 20 synthetic test photos; only 2 registered due to
textureless surfaces. This is not a bug — COLMAP correctly reported what it could
do. The problem is that the pipeline has no gate to say "2/20 is below your
coverage threshold, stop before training." The thresholds in this design are
**defined by the operator**, not reverse-engineered from the 2/20 run.

## Considered approaches

### A. Add quality fields directly to `RegistrationResult`

Rejected. `RegistrationResult` is a coordinate trust root written as
`registration.json` with forced LF and stable digest. Embedding mutable policy
thresholds into it would conflate "what COLMAP measured" with "what the operator
required" — two different provenance concerns. The report should be a **separate
artefact** that references the registration JSON by SHA.

### B. Reuse `production_quality_gates.py` patterns in the registration domain

Adopted (structurally, not by code reuse). The post-render quality gate pattern —
frozen policy model → content-addressed SHA → separate request/decision/report
schemas → `passed: bool` that never grants trust on its own — maps cleanly to SfM
registration. The key differences:

- SfM policy thresholds are about **coverage** (registered/total, connected-model
  share), not per-frame pixel quality (depth/semantic/sky dominance).
- The "engine" is COLMAP (or mock/external), not a Blender renderer.
- The report must capture **model selection** (which sparse model was chosen and
  why), which has no analogue in the post-render domain.

### C. Treat model enumeration as a COLMAP wrapper concern only

Rejected. If model selection stays inside `colmap_register()`, it remains
untestable and unauditable. The design mandates a **separate model-enumeration
step** that produces a structured `SparseModelEnumeration` consumed by the report,
so the selection rule is visible and fail-closed.

## Architecture

### New module: `pipeline/registration_quality.py`

All new types live in a single new module. No changes to
`pipeline/registration.py` or `pipeline/recon_schema.py` in this design phase.

### `RegistrationQualityPolicy`

Frozen pydantic model, `extra="forbid"`. All thresholds are operator-supplied.

```python
class RegistrationQualityPolicy(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_registered_count: int = Field(ge=0)
    min_registered_ratio: float = Field(ge=0.0, le=1.0)
    min_session_coverage_ratio: float = Field(ge=0.0, le=1.0)
    max_unregistered_consecutive_run: int = Field(ge=0)
    min_largest_connected_model_share: float = Field(ge=0.0, le=1.0)
```

**Why all thresholds are mandatory (no defaults):** a default threshold is an
implicit recommendation. The operator must explicitly state "I require at least
N registered images and M% coverage" — silence is not consent. This also prevents
the 2/20 run from implicitly defining a threshold.

**Canonical bytes & SHA:** `policy_canonical_sha256()` serializes via
`model_dump_json(sort_keys=True, ensure_ascii=True)` with LF newlines, then
SHA-256. Same pattern as `production_frame_quality_policy_v2_sha256()`.

### `SparseModelEnumeration`

Structured output of model discovery from a COLMAP `sparse/` directory.

```python
class SparseModelEntry(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_index: int = Field(ge=0)
    image_count: int = Field(ge=0)
    point3d_count: int = Field(ge=0)
    images: tuple[str, ...] = Field(default=())


class SparseModelEnumeration(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    models: tuple[SparseModelEntry, ...]
    selected_model_index: int = Field(ge=0)
    selection_rule: Literal["largest_image_count", "largest_point3d_count", "single_model"]

    @property
    def largest_connected_model_share(self) -> float: ...
```

**Selection rule:** deterministic — the model with the most registered images.
Ties broken by point3d count, then by lowest model index. This replaces the
hardcoded `sparse/"0"` assumption.

**Enumeration:** scan `sparse/*/` subdirectories, parse `images.txt` and
`points3D.txt` (COLMAP TXT output) to count images and points per model.
Deterministic sort by model index before selection.

### `RegistrationQualityReport`

The final auditable artefact. Binds the registration JSON, capture manifest,
policy, and measured outcome.

```python
class SessionQualityOutcome(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    registered: int = Field(ge=0)
    total: int = Field(ge=0)
    unregistered_images: tuple[str, ...] = Field(default=())
    longest_unregistered_run: int = Field(ge=0)


class RegistrationQualityReport(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Content-addressed bindings (all 64-hex SHA-256)
    registration_json_sha256: str  # = sha256 of registration.json bytes
    capture_manifest_sha256: str | None  # None = manifest not provided
    policy_canonical_sha256: str  # = policy_canonical_sha256()

    # Engine identity
    engine: Literal["colmap", "mock", "external"]
    engine_version: str | None = None  # e.g. "COLMAP 4.1.0"

    # Measured outcome
    registered_count: int = Field(ge=0)
    total_input_images: int = Field(ge=0)
    registered_ratio: float = Field(ge=0.0, le=1.0)
    session_outcomes: tuple[SessionQualityOutcome, ...] = Field(default=())
    model_enumeration: SparseModelEnumeration | None = None

    # Three-state decision (the core contract)
    invocation_succeeded: bool
    quality_accepted: bool
    training_allowed: bool

    # Rejection reasons (empty list = accepted; non-empty = why rejected)
    rejection_reasons: tuple[str, ...] = Field(default=())
```

### Three-state logic

```
invocation_succeeded = (engine produced a non-empty RegistrationResult
                         without crash/timeout)

quality_accepted     = invocation_succeeded
                       AND registered_count >= policy.min_registered_count
                       AND registered_ratio >= policy.min_registered_ratio
                       AND every session's ratio >= policy.min_session_coverage_ratio
                       AND max consecutive unregistered run <= policy.max_unregistered_consecutive_run
                       AND largest_connected_model_share >= policy.min_largest_connected_model_share

training_allowed     = quality_accepted
                       AND engine != "mock"
                       AND capture_manifest_sha256 is not None
```

**Fail-closed rules (training_allowed=False regardless of coverage):**
- `engine == "mock"` — synthetic registration can never be training-eligible.
- `capture_manifest_sha256 is None` — without a bound capture manifest, the
  registration cannot be audited against the actual input photos.
- `invocation_succeeded == False` — crash/timeout is never training-eligible.
- Any `rejection_reasons` entry — explicit rejection is never overridden.

### Validation contract

A `validate_registration_quality(report, policy, registration_json_bytes)` function:

1. Recompute `policy_canonical_sha256` from the policy and compare to the
   report's claim. Mismatch → `ValueError`.
2. Recompute `registration_json_sha256` from the bytes and compare. Mismatch →
   `ValueError`.
3. Re-derive `registered_ratio`, `quality_accepted`, `training_allowed` from the
   measured fields + policy thresholds. Compare to the report's claims. Mismatch
   → `ValueError`.
4. Check `rejection_reasons` consistency: if `quality_accepted=False`, reasons
   must be non-empty; if `quality_accepted=True`, reasons must be empty.

This mirrors `reconstruct._validate_scene_history` — the validator re-derives
rather than trusting self-reported booleans.

## Canonical JSON contract

- All models use `model_dump_json(sort_keys=True, ensure_ascii=True)` for
  canonical bytes.
- Files written with `newline="\n"` (forced LF) for cross-OS reproducibility,
  same as `registration.json`.
- `extra="forbid"` on all models — unknown fields are rejected, not silently
  dropped.

## Canary plan

The canary proves the **mechanism** works, not that real photos are trainable:

1. Build a synthetic fixture with known registered/total counts (mock engine).
2. Define a policy with thresholds that the fixture passes.
3. Generate a `RegistrationQualityReport`, write it to disk, reload it.
4. Verify `validate_registration_quality` passes.
5. Tamper with `registered_count` → validation fails.
6. Tamper with `policy_canonical_sha256` → validation fails.
7. Set `engine="mock"` → `training_allowed=False` even with full coverage.
8. Set `capture_manifest_sha256=None` → `training_allowed=False`.

The canary retains: input fixture, command receipt, `registration.json`,
`quality_report.json`, and their SHAs. A synthetic canary cannot be used as
real-collection acceptance.

## What this design does NOT do

- Does not modify `pipeline/registration.py` (the COLMAP wrapper). The wrapper
  will be modified in the implementation phase, after Codex reviews this design.
- Does not modify `pipeline/recon_schema.py` (`RegistrationResult`). The quality
  report is a separate artefact that references the registration by SHA.
- Does not define thresholds. All thresholds are operator-supplied at runtime.
- Does not prove camera coverage is geometrically sufficient for 3DGS. A 20/20
  registration with perfect overlap policy is still not a guarantee that the
  specific scene can be trained to high quality — that requires actual training.
- Does not touch Studio/Viewer. The three-state display is a future Codex task
  that consumes the `RegistrationQualityReport` schema defined here.

## Precedent alignment

| Concern | Post-render precedent | Registration design |
|---|---|---|
| Frozen policy model | `ProductionFrameQualityPolicyV2` | `RegistrationQualityPolicy` |
| Content-addressed SHA | `production_frame_quality_policy_v2_sha256()` | `policy_canonical_sha256()` |
| Separate schemas | `POLICY_SCHEMA` / `REQUEST_SCHEMA` / `DECISION_SCHEMA` / `REPORT_SCHEMA` | `RegistrationQualityPolicy` / `RegistrationQualityReport` |
| `passed` never grants trust | `passed: bool` (metric frame still needs alignment evidence) | `training_allowed: bool` (training still needs real photos + GPU) |
| Fail-closed on tamper | SHA mismatch → `ValueError` | Same validator pattern |
