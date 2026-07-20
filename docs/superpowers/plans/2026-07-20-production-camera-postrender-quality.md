# Production Camera Post-render Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete HANDOFF-006 without fabricated evidence: compute region-aware quality statistics from the real six-layer frame buffers, bind them to exact frame bytes and an explicit policy, expose per-rule rejection reasons, and relocate obstructed cameras only through topology-aware candidates that pass fresh Blender evidence.

**Architecture:** Keep geometry preflight and post-render quality as separate evidence stages. Extend the verified renderer at the point where depth/normal/instance/semantic arrays are already decoded, emit raw integer counts plus explicit denominators, and let host code recompute ratios and decisions. Add a Windows v2-build adapter instead of weakening the existing Mac L0 gate. Replace camera-ID whitelist repose with a content-addressed route candidate search that consumes a failing clearance decision and must produce a fresh passing report before a new plan can be used.

**Tech Stack:** Python 3.11+, Pydantic v2, NumPy, Blender 4.5.11 Python/OpenImageIO, pytest, existing canonical JSON/SHA-256/render journal infrastructure.

---

## Task 1: Correct and adopt the post-render evidence contract

**Files:**

- Modify: `pipeline/synthetic_village/production_quality_gates.py`
- Modify: `tests/test_synthetic_village_production_quality_gates.py`

### Step 1: Write failing contract tests

Tests must require raw counts, not caller-supplied ratios:

```python
statistics = ProductionFrameLayerStatistics(
    camera_id="camera-ground-route-034",
    total_pixel_count=589824,
    upper_pixel_count=294912,
    valid_depth_pixel_count=500000,
    valid_normal_pixel_count=500000,
    registered_instance_pixel_count=120000,
    valid_semantic_pixel_count=500000,
    sky_pixel_count=89824,
    upper_ground_pixel_count=20000,
    near_depth_pixel_count=40000,
    dominant_upper_instance_id=42,
    dominant_upper_instance_pixel_count=180000,
)
assert statistics.valid_depth_pixel_ratio == round(500000 / 589824, 6)
assert statistics.single_instance_upper_dominance_ratio == round(
    180000 / 294912,
    6,
)
```

Add negative tests for:

- count above its denominator;
- dominant count without a registered instance ID;
- caller attempting to inject a ratio;
- instance ID absent from the bound object registry;
- semantic IDs or region definitions absent from policy;
- policy threshold/region/depth/denominator mutation changing policy SHA.

### Step 2: Make measurement semantics explicit

The policy must contain:

```python
near_depth_m: float
upper_region_end_row_exclusive: int
ground_semantic_ids: tuple[int, ...]
sky_semantic_id: Literal[0]
ratio_round_digits: Literal[6]
near_depth_denominator: Literal["all-pixels", "valid-depth-pixels"]
upper_dominance_denominator: Literal["upper-region-pixels"]
```

Rename `default_frame_quality_policy_v2()` to
`candidate_synthetic_village_frame_quality_policy_v2()`. No production CLI
may silently select it. Operator thresholds remain required inputs.

Remove `valid-instance-pixel-ratio` as a mandatory minimum rule. Instance `0`
is valid for auxiliary terrain/water/sky and is not equivalent to invalid
geometry. Preserve `registered_instance_pixel_count` as raw evidence and use
registered instances only for the dominant-instance rule.

### Step 3: Bind exact frame identity

Extend `ProductionFrameQualityRequestV2` with:

```python
render_id: Sha256
renderer_script_sha256: Sha256
journal_sha256: Sha256
frames: tuple[ProductionFrameEvidenceBinding, ...]

class ProductionFrameEvidenceBinding(FrozenModel):
    camera_id: str
    runtime_report_sha256: Sha256
    artifacts: tuple[ProductionArtifactRecord, ...]  # exact six-file contract
```

The request validator must require plan order, unique camera IDs, exact six
artifacts, and registered SHA/size for every file. Changing any artifact SHA
must change `request_id`.

### Step 4: Verify

```powershell
python -m pytest tests/test_synthetic_village_production_quality_gates.py -q
python -m ruff check pipeline/synthetic_village/production_quality_gates.py tests/test_synthetic_village_production_quality_gates.py
```

Expected: PASS.

### Step 5: Commit and push

```powershell
git add pipeline/synthetic_village/production_quality_gates.py tests/test_synthetic_village_production_quality_gates.py
git commit -m "feat(camera): bind post-render quality evidence" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/synthetic_village/production_quality_gates.py tests/test_synthetic_village_production_quality_gates.py
git push origin main
```

## Task 2: Compute statistics inside the verified Blender runtime

**Files:**

- Modify: `scripts/blender/render_synthetic_village.py`
- Modify: `pipeline/synthetic_village/production_render.py`
- Modify: `tests/test_synthetic_village_blender_runtime.py`
- Modify: `tests/test_synthetic_village_production_render.py`

### Step 1: Write failing array-level tests

Extract a pure runtime function:

```python
_production_layer_counts(
    depth,
    normals,
    instances,
    semantics,
    *,
    policy,
    object_registry,
)
```

Use 4x4 synthetic buffers in Blender probes to prove:

- top and bottom rows use the declared region boundary;
- background is depth=0/normal=0/instance=0/semantic=0;
- auxiliary terrain may have instance=0 while depth/normal/semantic stay valid;
- near depth uses strict `< near_depth_m`;
- dominant upper instance excludes ID 0;
- counts are integer and deterministic;
- unknown instance or semantic values fail closed.

### Step 2: Emit raw statistics from real decoded buffers

Call `_production_layer_counts` immediately after
`_validate_cross_layer_pixels`. The runtime report must include:

```python
"layer_statistics": {
    "schema_version": "...layer-statistics.v2",
    "camera_id": camera_id,
    "total_pixel_count": PIXELS,
    "upper_pixel_count": WIDTH * upper_rows,
    "...": integer_counts,
}
```

Do not derive identity from object names. Resolve a dominant instance through
the bound `object_registry` entry and report `object_id`.

### Step 3: Recompute on host

`LocalProductionRenderFrameReport` carries raw statistics. The runner:

1. verifies report and all artifact SHA/size;
2. recomputes ratios and decisions from raw counts;
3. writes the decision into journal;
4. rejects any runtime decision that differs from host recomputation.

### Step 4: Verify

```powershell
python -m pytest tests/test_synthetic_village_blender_runtime.py -q
python -m pytest tests/test_synthetic_village_production_render.py -q
python -m ruff check scripts/blender/render_synthetic_village.py pipeline/synthetic_village/production_render.py tests/test_synthetic_village_blender_runtime.py tests/test_synthetic_village_production_render.py
```

Expected: new focused probes PASS. If stale private fixture `344e...` remains
126-object/14-semantic, update or regenerate that fixture in a separate,
identity-recorded maintenance task; do not weaken the 130/15 contract.

## Task 3: Add a separate Windows v2 production-build adapter

**Files:**

- Create: `pipeline/synthetic_village/windows_production_build.py`
- Modify: `pipeline/synthetic_village/local_production_runner.py`
- Modify: `scripts/synthetic_village.py`
- Create: `tests/test_synthetic_village_windows_production_build.py`

### Step 1: Write failing adapter tests

The adapter must verify:

- Windows x64 pinned Blender executable;
- textured build request and canonical v2 build report;
- directory name/build ID/report SHA;
- `.blend` SHA/size against report;
- object/auxiliary/semantic registries;
- no redirected paths;
- no mutation of the Mac `LocalTexturedPreviewRequest` platform gate.

### Step 2: Implement explicit build selection

CLI shape:

```text
render-production-local --local-preview-build ...
render-production-windows --verified-v2-build ...
```

Do not overload one flag and guess platform from a path or executable name.
Both adapters return one common immutable `VerifiedProductionBuild` record.

### Step 3: Run preflight-only on Windows

```powershell
python scripts/synthetic_village.py render-production-windows `
  --verified-v2-build .nantai-studio/synthetic-village/hybrid-v3/work/canary/4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc `
  --min-valid-pixel-ratio 0.75 `
  --clearance-near-distance-m 2.0 `
  --min-upper-middle-near-hits 5 `
  --preflight-only
```

The output must reproduce preflight report SHA
`0b63bc6759e8a36d7ace04d760e43d27862082d084cc0cd50b73e30449224418`.

## Task 4: Measure representative real frames before approving thresholds

**Files:**

- Create: `handoff/REVIEW-CODEX-015-production-frame-quality-distribution.md`
- Modify: `tests/test_synthetic_village_blender_runtime.py`

### Step 1: Render a bounded control set

Render at least:

```text
camera-ground-route-010
camera-ground-route-034
camera-ground-route-039
camera-ground-route-011
camera-ground-route-025
camera-ground-route-026
```

Keep private six-layer artifacts below `.nantai-studio`. Record exact request,
report, renderer, blend, and six artifact SHAs.

### Step 2: Audit distributions

For every raw count/ratio, publish min/median/max and each camera value. Human
RGB inspection remains secondary evidence. Approve thresholds only after:

- normal controls pass;
- 010/039 fail preflight before render;
- 034 is either rejected by measured post-render rules or remains honestly
  unknown;
- no rule depends on fabricated camera-specific values.

## Task 5: Replace hardcoded repose with topology-aware candidate search

**Files:**

- Replace: `pipeline/synthetic_village/production_repose.py`
- Modify: `tests/test_synthetic_village_production_repose.py`
- Create: `handoff/REVIEW-CODEX-016-production-camera-repose.md`

### Step 1: Require evidence, not camera IDs

The public API consumes:

```python
search_replacement_pose(
    *,
    plan,
    camera_id,
    failing_decision,
    preflight_report_sha256,
    topology,
    candidate_policy,
)
```

It must reject a passing decision, wrong camera ID, wrong policy SHA, or report
SHA not bound by the caller's journal.

### Step 2: Search along topology

Candidates are deterministic arc-length offsets on the same route, with
explicit lateral offsets only when topology provides a walkable corridor.
Each candidate recalculates:

- position and look-at;
- `arc_length_m`;
- `c2w_opencv`;
- spacing and loop evidence;
- plan and registry SHA.

No fixed `{010,039}` whitelist is allowed.

### Step 3: Require fresh scene evidence

A candidate cannot be accepted by geometry alone. It must:

1. generate a new plan/request/preflight ID;
2. pass the actual Blender clearance report;
3. render the representative six layers;
4. pass the approved post-render policy;
5. produce before/after RGB and measured comparison.

Only then may the canonical 180-camera plan change.

## Task 6: Studio UX and completion boundary

**Files:**

- Modify: `web/studio/*`
- Modify: `pipeline/studio_server.py`
- Modify: Studio tests
- Modify: `pipeline/synthetic_village/production_profile.py`

Studio must show:

- stage: preflight / rendering / post-render-quality;
- camera ID and state;
- rule ID, measured, operator threshold, comparison direction;
- evidence/report SHA;
- `synthetic`, L0/L2, and `trust_effect=none-quality-filter-only`;
- “geometry preflight pass is not final frame pass”.

`req-5-pose-quality-fail-closed` may be removed from
`_undelivered_requirements()` only after fresh 180-camera evidence, real
six-layer bridge, accepted replacement poses, Studio presentation, and all
related tests pass.
