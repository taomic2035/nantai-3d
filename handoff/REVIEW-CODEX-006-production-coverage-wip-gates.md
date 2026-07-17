# REVIEW-CODEX-006 — production coverage WIP gates

> Reviewer: Codex (Viewer / UX / audit lane)
> Target: Opus production coverage WIP
> Date: 2026-07-17
> Scope: read-only review; no Opus-owned source or test file was modified

## Current verdict

The WIP is correctly fail-closed, but it is not ready to commit or to feed a
production 3DGS run yet:

- `plan-production` declares `180` but honestly places `132`;
- all `48` `elevated-pedestrian` cameras remain unplaced because the scene has
  no walkable elevated topology;
- route-loop closure is therefore structurally unreachable;
- production bad-frame / valid-pixel / pose-quality rejection remains
  explicitly unimplemented;
- the coverage report's published JSON cannot currently round-trip through its
  own strict schema;
- Ruff still reports four errors.

This is the correct honesty boundary. Viewer and Studio must not present this
plan as a completed 180-camera capture or as 360-degree coverage.

## Blocking finding: coverage JSON cannot round-trip

Command:

```text
.venv\Scripts\python.exe -m pytest -q \
  tests/test_coverage_audit.py::test_report_refuses_to_publish_evidence_that_contradicts_its_own_summary
```

Observed result:

```text
1 failed
732 validation errors for CoverageAuditReport
components.*.observations.*.mean_unit_normal_xyz
Input should be a valid array [type=tuple_type, input_type=list]
```

The intended summary-integrity error never runs because parsing fails first.
The unmodified `report.model_dump(mode="json")` payload therefore also cannot
round-trip, so the test's adversarial mutations do not yet prove the structural
binding they intend to prove.

### Root cause

`CameraObservation._derive_frame_fraction` is a `mode="before"` model
validator. It copies and returns a Python `dict`. That transition loses
Pydantic's JSON input mode for the nested value. With `strict=True`,
`mean_unit_normal_xyz: tuple[float, float, float]` then rejects the JSON array
as an ordinary Python `list`.

A minimal reproduction confirmed the distinction:

```text
strict tuple without before-validator + model_validate_json: PASS
strict tuple with dict-copying before-validator + model_validate_json: FAIL tuple_type
```

The fix must preserve both requirements:

1. Python-mode strictness must not silently accept arbitrary lists as tuples.
2. Canonical JSON emitted by the report must validate and round-trip.

After the fix, keep all four adversarial checks:

- a clean payload round-trips to the same model;
- a lying summary fails for `components_meeting_threshold`;
- a lying component count fails for `qualifying_camera_count`;
- rewritten pixel evidence cannot remain consistent with the derived fields.

## Ruff gate

The current scoped Ruff run reports:

```text
pipeline/synthetic_village/coverage_audit.py:647  E501
pipeline/synthetic_village/coverage_audit.py:832  E501
tests/test_coverage_audit.py:448                 E501
tests/test_coverage_audit.py:916                 B905
```

These are mechanical, but the lane is not green until the same scoped command
passes.

## Measured production-plan state

Command:

```text
.venv\Scripts\python.exe scripts/synthetic_village.py plan-production --batch-count 6
```

Observed machine-readable facts:

```text
declared_target_count: 180
camera_count: 132
complete: false
batch_sizes: [22, 22, 22, 22, 22, 22]
unplaced elevated-pedestrian: 48
req-5-pose-quality-fail-closed: not-implemented
req-6-route-loop-closure: structurally-unreachable
```

The six stable batches cover only the 132 placed cameras. They are useful for
planning, but they are not evidence that the production profile is complete.

## Required next evidence

1. Fix the strict JSON round-trip root cause and make the scoped tests and Ruff
   command green.
2. Convert the Batch 5 design semantics into real walkable elevated
   polylines/surfaces with width, clearance, collision, drainage separation,
   and explicit upper/middle/lower exits. Image prompts and filenames remain
   forbidden as geometry or coverage evidence.
3. Re-resolve `elevated-pedestrian` from that topology and place the missing 48
   cameras without fabricating height offsets over the ground route.
4. Prove two real route loops, then render and verify all 180 cameras through
   the six-layer production journal.
5. Compare 24 versus 180 only under the same resolution, component registry,
   and explicit coverage policy, reporting per-component pixel, azimuth, and
   observed-normal changes.
