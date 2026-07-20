# Production Camera Clearance Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver the first fail-closed vertical slice of HANDOFF-OPUS-006: a scene-bound, policy-versioned 5x5 camera-clearance preflight that can reject production cameras before rendering, binds every decision into the render journal, and never upgrades trust.

**Architecture:** Add a pure Pydantic contract/evaluator module for raw ray evidence and operator policy, a Blender-side single-process probe that produces canonical evidence for a selected camera set, and host integration that validates all content identities before transitioning journal frames to a distinct `preflight-rejected` state. Keep post-render six-layer classification and deterministic camera relocation out of this slice; use the resulting measured distribution to specify those thresholds in a follow-up plan instead of inventing cross-scene constants.

**Tech Stack:** Python 3.11+, Pydantic v2, Blender 4.x Python API (`bpy`, `mathutils`), pytest, existing canonical JSON/SHA-256 and local-production journal infrastructure.

---

## Scope and non-negotiable behavior

- This plan implements the geometry preflight portion of
  `handoff/HANDOFF-OPUS-006-production-camera-quality-gates.md`.
- The policy values are explicit operator inputs. The observed `2.0 m` and
  `5-of-15` values are an approved synthetic-village candidate, not a universal
  reconstruction truth.
- Raw ray evidence and the decision policy are separate content-addressed
  objects.
- The report binds the production plan, camera registry, build report, Blender
  scene, Blender executable, object registry, and preflight script by SHA-256.
- Unknown object/stable/part/semantic fields remain `null`; no filename or
  object-name inference may promote provenance.
- Every report states `synthetic=true`,
  `geometry_trust=simplified-pbr-not-render-parity`, and
  `trust_effect=none-quality-filter-only`.
- `preflight-rejected` is not `failed`, `rejected` (post-render), or
  `verified`. It publishes no six-layer artifacts.
- Camera `ground-route-034` passing this geometry preflight must remain
  `planned`; it cannot become verified until post-render evidence exists.
- Do not remove production profile requirement 5 in this slice.

## Task 1: Add canonical policy, evidence, and decision contracts

**Files:**

- Create: `pipeline/synthetic_village/production_preflight.py`
- Create: `tests/test_synthetic_village_production_preflight.py`

### Step 1: Write failing contract and evaluator tests

Add tests that construct a 5x5 evidence grid with explicit raw hits:

```python
def test_clearance_policy_is_content_addressed_and_never_upgrades_trust() -> None:
    policy = ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )

    assert policy.policy_id == "synthetic-village-clearance-v1"
    assert policy.sample_grid == (-0.9, -0.45, 0.0, 0.45, 0.9)
    assert policy.trust_effect == "none-quality-filter-only"
    assert len(production_clearance_policy_sha256(policy)) == 64


def test_evaluator_rejects_five_upper_middle_near_hits_but_not_lower_ground() -> None:
    policy = ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )
    obstructed = _evidence_with_hits(
        camera_id="camera-ground-route-010",
        hits={(x, y): 0.5 for x in policy.sample_grid for y in (0.0, 0.45, 0.9)},
    )
    ground_only = _evidence_with_hits(
        camera_id="camera-ground-route-001",
        hits={(x, -0.9): 0.5 for x in policy.sample_grid},
    )

    rejected = evaluate_production_camera_clearance(obstructed, policy=policy)
    passing = evaluate_production_camera_clearance(ground_only, policy=policy)

    assert rejected.passes is False
    assert rejected.failed_rule_ids == ("upper-middle-near-hit-count",)
    assert rejected.measured_upper_middle_near_hit_count == 15
    assert passing.passes is True
    assert passing.measured_upper_middle_near_hit_count == 0
```

Also test:

- exactly 25 unique `(sample_x, sample_y)` rows are required;
- `hit=false` requires `distance_m` and all identity fields to be `None`;
- `hit=true` requires finite positive distance but allows unknown identity fields;
- measured counts are recomputed from rows and cannot be supplied by a caller;
- canonical bytes and SHA are stable across two processes;
- malformed policy bounds fail closed.

Run:

```powershell
python -m pytest tests/test_synthetic_village_production_preflight.py -q
```

Expected: FAIL because `production_preflight` does not exist.

### Step 2: Implement the pure models and evaluator

Create these public types and functions:

```python
PRODUCTION_CLEARANCE_POLICY_SCHEMA = (
    "nantai.synthetic-village.production-clearance-policy.v1"
)
PRODUCTION_CLEARANCE_EVIDENCE_SCHEMA = (
    "nantai.synthetic-village.production-camera-clearance-evidence.v1"
)
PRODUCTION_CLEARANCE_DECISION_SCHEMA = (
    "nantai.synthetic-village.production-camera-clearance-decision.v1"
)


class ProductionClearancePolicy(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.production-clearance-policy.v1"
    ] = PRODUCTION_CLEARANCE_POLICY_SCHEMA
    policy_id: Literal["synthetic-village-clearance-v1"] = (
        "synthetic-village-clearance-v1"
    )
    sample_grid: tuple[float, ...] = (-0.9, -0.45, 0.0, 0.45, 0.9)
    upper_middle_min_sample_y: Literal[0.0] = 0.0
    near_distance_m: float = Field(gt=0.0, le=100.0, allow_inf_nan=False)
    minimum_upper_middle_near_hit_count: int = Field(ge=1, le=15)
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )


class ProductionClearanceRayEvidence(FrozenModel):
    sample_x: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    sample_y: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    hit: bool
    distance_m: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    object_name: str | None = Field(default=None, min_length=1)
    stable_id: int | None = Field(default=None, ge=0)
    part_id: int | None = Field(default=None, ge=0)
    semantic_id: int | None = Field(default=None, ge=0)


class ProductionCameraClearanceEvidence(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.production-camera-clearance-evidence.v1"
    ] = PRODUCTION_CLEARANCE_EVIDENCE_SCHEMA
    camera_id: str = Field(pattern=r"^camera-[a-z0-9-]+-[0-9]{3}$")
    rays: tuple[ProductionClearanceRayEvidence, ...] = Field(
        min_length=25,
        max_length=25,
    )


class ProductionCameraClearanceDecision(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.production-camera-clearance-decision.v1"
    ] = PRODUCTION_CLEARANCE_DECISION_SCHEMA
    camera_id: str
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    measured_upper_middle_near_hit_count: int = Field(ge=0, le=15)
    passes: bool
    failed_rule_ids: tuple[
        Literal["upper-middle-near-hit-count"], ...
    ] = ()
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )
```

Use `json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True) + "\n"` for
canonical bytes. Model validators must enforce the exact fixed grid, unique
sample pairs, and hit/no-hit field consistency. The evaluator computes the
count using `sample_y >= 0.0` and `distance_m < policy.near_distance_m`; the
strict `<` relation must be tested at the boundary.

### Step 3: Run focused tests and lint

```powershell
python -m pytest tests/test_synthetic_village_production_preflight.py -q
python -m ruff check pipeline/synthetic_village/production_preflight.py tests/test_synthetic_village_production_preflight.py
```

Expected: PASS.

### Step 4: Commit and push

```powershell
git add pipeline/synthetic_village/production_preflight.py tests/test_synthetic_village_production_preflight.py
git commit -m "feat(camera): add clearance evidence contract" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/synthetic_village/production_preflight.py tests/test_synthetic_village_production_preflight.py
git push origin main
```

## Task 2: Add a scene-bound Blender preflight report

**Files:**

- Modify: `pipeline/synthetic_village/production_preflight.py`
- Create: `scripts/blender/preflight_production_cameras.py`
- Modify: `tests/test_synthetic_village_production_preflight.py`
- Modify: `tests/test_synthetic_village_blender_runtime.py`

### Step 1: Write failing request/report identity tests

Add tests for:

```python
request = build_production_clearance_request(
    plan=plan,
    selected_camera_ids=(
        "camera-ground-route-010",
        "camera-ground-route-034",
        "camera-ground-route-039",
    ),
    build_id=build_id,
    blender_executable_sha256=executable_sha,
    preflight_script_sha256=script_sha,
    blend_sha256=blend_sha,
    build_report_sha256=build_report_sha,
    object_registry=object_registry,
)
assert request.production_plan_sha256 == production_plan_sha256(plan)
assert request.camera_registry_sha256 == production_camera_registry_digest(plan)
assert request.policy_sha256 == production_clearance_policy_sha256(policy)
assert request.preflight_id == production_clearance_preflight_id(request)
```

Mutation tests must reject:

- plan SHA or registry SHA mismatch;
- selected camera not in the plan or duplicate selected IDs;
- object registry digest mismatch;
- policy SHA mismatch;
- build, scene, executable, or script SHA mismatch in the runtime report;
- missing or extra camera evidence;
- duplicate JSON keys and non-canonical report bytes.

### Step 2: Implement the request/report contracts

Add:

```python
class ProductionClearanceRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.production-clearance-request.v1"
    ]
    production_plan: ProductionCameraPlan
    production_plan_sha256: str
    camera_registry_sha256: str
    selected_camera_ids: tuple[str, ...]
    build_id: str
    blender_executable_sha256: str
    preflight_script_sha256: str
    blend_sha256: str
    build_report_sha256: str
    object_registry_sha256: str
    policy: ProductionClearancePolicy
    policy_sha256: str
    preflight_id: str
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"]
    trust_effect: Literal["none-quality-filter-only"]


class ProductionClearanceReport(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.production-clearance-report.v1"
    ]
    preflight_id: str
    request_sha256: str
    production_plan_sha256: str
    camera_registry_sha256: str
    build_id: str
    blender_executable_sha256: str
    preflight_script_sha256: str
    blend_sha256: str
    build_report_sha256: str
    object_registry_sha256: str
    policy_sha256: str
    evidence: tuple[ProductionCameraClearanceEvidence, ...]
    decisions: tuple[ProductionCameraClearanceDecision, ...]
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"]
    trust_effect: Literal["none-quality-filter-only"]
```

`preflight_id` hashes every immutable identity and the selected camera IDs.
`request_sha256` hashes the canonical request bytes. Report validation
re-evaluates every decision from its evidence and policy.

### Step 3: Write the Blender runtime

`scripts/blender/preflight_production_cameras.py` must:

1. accept `--request <canonical-json> --report <new-private-path>`;
2. reject duplicate JSON keys and non-canonical request bytes;
3. hash the running Blender executable, script, opened `.blend`, and request;
4. validate scene `build_id` plus all registries against the request;
5. convert each OpenCV c2w matrix with
   `c2w_blender = c2w_opencv @ diag(1,-1,-1,1)`;
6. cast the fixed 5x5 rays through the declared intrinsics;
7. record the first hit distance and registry-backed IDs, leaving unavailable
   IDs null;
8. evaluate with the request policy;
9. write one canonical report atomically without embedding private paths.

The ray direction for normalized sample `(sx, sy)` is derived from the
intrinsics, not a hard-coded FOV:

```python
pixel_x = (sx + 1.0) * 0.5 * (width - 1)
pixel_y = (1.0 - sy) * 0.5 * (height - 1)
direction_camera = Vector(
    ((pixel_x - cx) / fx, (cy - pixel_y) / fy, -1.0)
).normalized()
direction_world = camera_rotation @ direction_camera
```

Use evaluated dependency-graph geometry and `scene.ray_cast`. Extract
registry-backed custom properties only; object names may be recorded for
diagnostics but cannot supply stable/part/semantic IDs.

### Step 4: Run unit and real Blender integration tests

```powershell
python -m pytest tests/test_synthetic_village_production_preflight.py -q
python -m pytest tests/test_synthetic_village_blender_runtime.py -q
python -m ruff check pipeline/synthetic_village/production_preflight.py scripts/blender/preflight_production_cameras.py tests/test_synthetic_village_production_preflight.py tests/test_synthetic_village_blender_runtime.py
```

Expected: PASS. The Blender test must use the bundled verified scene and at
least cameras `010`, `034`, and `039`; it must assert `010` and `039` fail the
candidate policy while `034` passes only this preflight.

### Step 5: Commit and push

```powershell
git add pipeline/synthetic_village/production_preflight.py scripts/blender/preflight_production_cameras.py tests/test_synthetic_village_production_preflight.py tests/test_synthetic_village_blender_runtime.py
git commit -m "feat(camera): probe production clearance in Blender" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/synthetic_village/production_preflight.py scripts/blender/preflight_production_cameras.py tests/test_synthetic_village_production_preflight.py tests/test_synthetic_village_blender_runtime.py
git push origin main
```

## Task 3: Integrate preflight execution and fail-closed journal state

**Files:**

- Modify: `pipeline/synthetic_village/production_render.py`
- Modify: `pipeline/synthetic_village/local_production_runner.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_local_production_runner.py`
- Modify: `tests/test_synthetic_village_production_render.py`

### Step 1: Write failing journal state tests

Add:

```python
def test_preflight_rejected_frame_has_bound_evidence_and_no_artifacts() -> None:
    rejected = transition_local_production_frame(
        journal,
        camera_id,
        state="preflight-rejected",
        preflight_report_sha256="a" * 64,
        clearance_decision=decision,
        wall_clock_seconds=1.25,
    )
    frame = _frame(rejected, camera_id)

    assert frame.state == "preflight-rejected"
    assert frame.artifacts == ()
    assert frame.runtime_report_sha256 is None
    assert frame.quality is None
    assert frame.clearance_decision == decision
```

Also prove:

- a passing preflight cannot enter `preflight-rejected`;
- a failing decision cannot be stored on `planned`, `rendering`, `verified`,
  `rejected`, `failed`, or `timed-out`;
- the journal binds `preflight_id`, policy SHA, request SHA, and report SHA;
- an existing journal with any mismatching preflight identity is rejected;
- a preflight-rejected frame is reused only when all identities match;
- a preflight pass remains `planned` and still needs six-layer rendering;
- `034` does not become verified because it passed geometry preflight.

Run:

```powershell
python -m pytest tests/test_synthetic_village_production_render.py tests/test_synthetic_village_local_production_runner.py -q
```

Expected: FAIL because the journal does not model preflight evidence.

### Step 2: Extend the journal contract

Add immutable journal fields:

```python
preflight_id: str = Field(pattern=r"^[0-9a-f]{64}$")
preflight_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
preflight_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
clearance_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
```

Add frame fields:

```python
state: Literal[
    "planned",
    "preflight-rejected",
    "rendering",
    "verified",
    "rejected",
    "failed",
    "timed-out",
]
clearance_decision: ProductionCameraClearanceDecision | None = None
```

The state validator must enforce:

- `preflight-rejected`: failing decision, report SHA, duration, no six-layer
  artifacts, no runtime report/statistics/post-render quality, no execution
  error;
- all other completed states retain their existing exact six-file rules;
- passing decisions never claim completion or verification.

The journal SHA and render ID must change when any preflight identity or policy
changes. Preserve the old render tree rather than overwrite it.

### Step 3: Invoke one preflight before the render loop

In `run_local_production_render`:

1. add required operator arguments `clearance_near_distance_m` and
   `minimum_upper_middle_near_hit_count`;
2. snapshot the new script with the executable, renderer script, blend, and
   build report;
3. build a request for the selected camera IDs;
4. run one Blender preflight process before rendering any selected frame;
5. validate the canonical report and immutable snapshots;
6. transition failing decisions to `preflight-rejected`;
7. render only passing decisions;
8. report `preflight_rejected_count` separately from post-render
   `rejected_count`.

Do not auto-relocate cameras in this task. Rejected evidence is needed before
the deterministic relocation policy can be designed and reviewed.

### Step 4: Expose explicit CLI policy

Add required arguments to `render-production-local`:

```python
render_production_local.add_argument(
    "--clearance-near-distance-m",
    type=float,
    required=True,
    help=(
        "Operator-selected near-hit threshold in metres for the versioned "
        "upper/middle 5x5 clearance policy; training filter only."
    ),
)
render_production_local.add_argument(
    "--min-upper-middle-near-hits",
    type=int,
    required=True,
    help=(
        "Operator-selected rejection count from the 15 upper/middle samples; "
        "the synthetic-village candidate is 5."
    ),
)
```

CLI JSON must include `preflight_id`, `preflight_report_path`, and
`preflight_rejected_count`. Keep the result explicitly L0 and
`trust_effect=none-quality-filter-only`.

### Step 5: Run focused and related tests

```powershell
python -m pytest tests/test_synthetic_village_production_render.py tests/test_synthetic_village_local_production_runner.py tests/test_synthetic_village_production_preflight.py -q
python -m pytest tests/test_synthetic_village_production_profile.py tests/test_synthetic_village_blender_runtime.py tests/test_synthetic_village_canary.py -q
python -m ruff check pipeline/synthetic_village/production_render.py pipeline/synthetic_village/local_production_runner.py pipeline/synthetic_village/production_preflight.py scripts/synthetic_village.py tests/test_synthetic_village_production_render.py tests/test_synthetic_village_local_production_runner.py
```

Expected: PASS.

### Step 6: Commit and push

```powershell
git add pipeline/synthetic_village/production_render.py pipeline/synthetic_village/local_production_runner.py scripts/synthetic_village.py tests/test_synthetic_village_local_production_runner.py tests/test_synthetic_village_production_render.py
git commit -m "feat(camera): reject obstructed production frames early" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/synthetic_village/production_render.py pipeline/synthetic_village/local_production_runner.py scripts/synthetic_village.py tests/test_synthetic_village_local_production_runner.py tests/test_synthetic_village_production_render.py
git push origin main
```

## Task 4: Produce fresh 180-camera evidence and specify Phase 2

**Files:**

- Create: `handoff/FEEDBACK-HANDOFF-CODEX-006-phase1.md`
- Create:
  `docs/superpowers/plans/2026-07-20-production-camera-postrender-quality.md`
- Modify: `AGENTS.md`

### Step 1: Run the full scene-bound preflight

Use the verified private build identity already recorded in the audit and run
all 180 cameras with the explicit candidate policy:

```powershell
python scripts/synthetic_village.py render-production-local `
  --build-directory .nantai-studio/synthetic-village/hybrid-v3/work/canary/4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc `
  --material-bundle-root .nantai-studio/synthetic-village/hybrid-v3/material-bundles/88e35afe5ed57b7d0187956d601b1470662aaf964f593a2fc08c543c7da2e2a3 `
  --min-valid-pixel-ratio 0.75 `
  --clearance-near-distance-m 2.0 `
  --min-upper-middle-near-hits 5 `
  --timeout-seconds 900
```

Stop the command after the preflight report is committed to the private render
root if the CLI does not yet support `--preflight-only`; add that bounded mode
with a failing CLI test rather than starting 180 expensive renders.

### Step 2: Audit the measured distribution

Record:

- all immutable input SHAs and the policy SHA;
- report SHA and rejected camera IDs;
- per-camera upper/middle near-hit count histogram;
- confirmation that `010` and `039` reject;
- confirmation that `034` passes only the geometry preflight and remains
  unverified;
- ordinary ground-route controls that pass despite lower-band ground hits;
- runtime and platform;
- explicit L0/synthetic/trust-effect limitations.

Keep large/private JSON below `.nantai-studio`; commit only its SHA and human
review.

### Step 3: Write the Phase 2 plan from evidence

The second plan must cover:

1. Blender-runtime per-region depth/normal/semantic/instance evidence;
2. versioned post-render rules that catch `034`-like diagonal occlusion;
3. deterministic route-aware relocation for rejected cameras;
4. a new 180-camera registry digest and render identity;
5. before/after RGB and six-layer comparison;
6. removal of production requirement 5 only after all 180 cameras satisfy the
   complete two-stage gate.

Do not select post-render thresholds until the Phase 1 distribution and
representative six-layer outputs have been measured.

### Step 4: Run final Phase 1 verification

```powershell
python -m pytest tests/test_synthetic_village_production_preflight.py tests/test_synthetic_village_production_render.py tests/test_synthetic_village_local_production_runner.py tests/test_synthetic_village_production_profile.py tests/test_synthetic_village_blender_runtime.py tests/test_synthetic_village_canary.py -q
python -m ruff check pipeline scripts tests
git diff --check
git status --short
```

Expected: all tests and lint pass; only intended tracked files plus the
pre-existing `web/data/` remain visible.

### Step 5: Commit and push the evidence

```powershell
git add handoff/FEEDBACK-HANDOFF-CODEX-006-phase1.md docs/superpowers/plans/2026-07-20-production-camera-postrender-quality.md AGENTS.md
git commit -m "docs(camera): verify production clearance phase one" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- handoff/FEEDBACK-HANDOFF-CODEX-006-phase1.md docs/superpowers/plans/2026-07-20-production-camera-postrender-quality.md AGENTS.md
git push origin main
```

## Completion boundary

Phase 1 is complete only when the full 180-camera report is freshly generated,
identity-validated, and its human audit is pushed. It does **not** mean:

- all production cameras are suitable for training;
- camera `034` is accepted;
- deterministic camera relocation is implemented;
- six-layer post-render quality is implemented;
- production profile requirement 5 is delivered;
- geometry, metric alignment, or reconstruction trust increased.
