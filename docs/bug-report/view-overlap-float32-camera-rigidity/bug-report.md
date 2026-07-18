# View-overlap audit rejected verified Blender camera matrices

Date: 2026-07-19

## Diagnostic capsule

| Field | Evidence |
|---|---|
| Symptom | The first real `audit-view-overlap` run stopped before overlap measurement with `camera-to-world rotation must be rigid with determinant +1`. The render journal and all 24 frames were already verified. |
| Reproduction | Run the overlap audit against local render `217e1cac5c76a9d5644ce7c0ec46408d285d09cbef30312739100d855e77afae`. `camera-ground-001` reproduces the rejection. |
| Root cause | `MeasuredView` used an unapproved `1e-7` rigidity tolerance. Verified Blender matrices carry expected float32-scale round-trip error. Across the 24 real matrices, maximum orthogonality and determinant errors were both approximately `1.8041e-7`. The producer contract already validates measured rotations at `1e-6` after separately limiting per-entry rotation drift to `3.2e-7`. |
| Diagnostic strategy | Measure orthogonality, determinant, and requested-to-measured drift across all 24 matrices; compare the failing consumer with the working `CameraRegistryEntry` producer contract. |
| Timeout strategy | If the measured errors exceeded the producer limits or varied beyond float32 scale, stop rather than widening the consumer tolerance. |
| Warning strategy | A second unsuccessful tolerance change would require tracing matrix conversion and serialization instead of further widening. Reflections and clearly non-rigid matrices must remain rejected. |
| User-visible correction | The audit now accepts the already verified Blender camera evidence and proceeds to the actual overlap quality gate. |
| Acceptance | The exact measured `camera-ground-001` matrix is a regression fixture; the reflected-matrix rejection remains green; the real 24-frame audit must reach an overlap report. |

## Reporter

Codex found the issue while running the new overlap gate against the measured
Mac Blender render.

## Root-cause analysis

The failure was not corrupt render evidence. The consumer introduced a stricter
rigidity tolerance than the evidence-producing contract. This inverted the
contract relationship: data that had already passed the authoritative
float32-aware camera registry was rejected by a downstream audit using no
stronger evidence.

The fix reuses the existing `1e-6` measured-rotation rigidity boundary. It does
not remove the determinant check and does not weaken the producer's separate
per-entry drift limit.

## Verification

```text
python -m pytest tests/test_synthetic_village_view_overlap.py -q
7 passed
```

The real-render audit is the final runtime verification and is recorded with
the overlap-gate evidence.
