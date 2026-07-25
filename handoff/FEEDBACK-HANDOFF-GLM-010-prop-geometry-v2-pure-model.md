# FEEDBACK-HANDOFF-GLM-010 — Batch35 prop geometry v2 (pure model)

Date: 2026-07-25
Owner: GLM-5.2
Reviewer: Codex
Status: candidate — pending Codex review

## Outcome

FEEDBACK-IMAGE2-040 §1–§5 pure-model half delivered: one frozen
`nantai.synthetic-village.prop-geometry-v2.v1` plan covering exactly the
eight Batch35 slot ids, with stable part graphs, local transforms,
material slots, collision/support roles, render bounds, semantic kinds,
exact/min counts, source-SHA locks and declared intersections. No
Blender emission, no `web/data/`, no registry, no Release.

The plan is the canonical replacement target for the current coarse
`scripts/blender/build_synthetic_village.py::_build_prop` block-body
proxies (jar = two cylinders, firewood = twelve cylinders, bench has a
back-like slab despite its backless slot description, handcart wheels
are solid cylinders without spokes, etc.). Blender emission comes
**after** Codex signs off on this pure model, per FEEDBACK-IMAGE2-040 §6.

## Fresh content-addressed output

```text
plan_sha256   = 532e227dad96aea69fc3eeaf9f8d27760f4e96a3ac528acef188929e728e51de
plan_size_bytes = 54359
total_parts   = 79
```

The plan bytes are canonical LF JSON (sorted keys, trailing newline);
the SHA-256 is deterministic and can be used as a content address. No
private candidate directory is written yet — the pure model lives in
`pipeline.synthetic_village.prop_geometry_v2.PROP_SLOTS_V2` at import
time and is re-validated on every `build_prop_geometry_v2_plan()` call.

## Slot coverage (frozen canonical graphs)

| Slot | Parts | Declared intersections | Notes |
|---|---|---|---|
| `prop-water-jar-01` | 4 | 1 | body/rim/opening/foot; ovoid body, narrow neck, flat foot ring |
| `prop-firewood-stack-01` | 13 | 4 | frame + 3×4 varied logs; log lengths alternate (frozen) |
| `prop-bamboo-basket-01` | 6 | 4 | body/rim/two loop handles/open interior/base; tapered body |
| `prop-wooden-bench-01` | 11 | 16 | **backless** (explicitly drops the `_build_prop` back-like slab); seat/4 legs/2 braces/pegs |
| `prop-farming-tools-01` | 9 | 4 | rest + 4 distinct tool heads/handles (hoe/rake/sickle/spade) |
| `prop-grain-rack-01` | 10 | 31 | 2 A-frames/3 rails/2 braces/slatted shelf |
| `prop-stone-trough-01` | 8 | 8 | basin/4 walls/2 feet; open hollow basin |
| `prop-handcart-01` | 18 | 42 | bed/2 spoked wheels/axle/2 handles/braces/rests; **exactly 2 wheels** (EXACT_COUNT_KINDS) |

Source SHAs are locked to the FEEDBACK-IMAGE2-040 `Accepted modeling
inputs` table; a wrong/stale handoff cannot silently consume a different
image (`BATCH35_SOURCE_SHAS` + per-slot validator).

## Fail-closed contract (FEEDBACK-IMAGE2-040 §5)

The plan builder rejects, with `PropGeometryV2Error` and no partial
artifact:

- unknown `slot_id` outside the eight supported ids;
- duplicate part ids within a slot;
- non-finite `local_transform_m` / `render_bounds_m` / `envelope_m`
  components (NaN/Inf);
- zero-volume or negative-dimension parts (`render_bounds_m` with a 0
  or negative component);
- part render bounds overflowing the slot envelope on any axis
  (with 1 mm tolerance for floating-point contact);
- grounded parts floating above the floor (`pz - bh/2 > 1 mm`);
- **forbidden interpenetration** — every AABB overlap > 1 mm between
  `solid`/`hollow` parts must be declared in `allowed_intersections`,
  and every declared intersection must actually be observed. Unexpected
  overlaps and stale declarations both fail closed;
- an `allowed_intersections` pair referencing the same part twice, a
  duplicate pair, or an unknown part id;
- missing `collision_proxy_sha256` at `finalize_for_build` time
  (non-empty 64-hex SHA required);
- non-hex `collision_proxy_sha256` at finalize time;
- wrong source SHA (`source_image_sha256` disagreeing with
  `BATCH35_SOURCE_SHAS[slot_id]`);
- handcart wheel count ≠ 2 (`EXACT_COUNT_KINDS`);
- farming-tools tool-head/handle count < 4 (`MIN_COUNT_KINDS`);
- missing required semantic kinds per slot (`REQUIRED_SEMANTIC_KINDS`);
- trust fields mutated to a promoted value
  (`Literal`-locked: `synthetic=True`, `stage="design-only"`,
  `geometry_consistency="not-verified"`, `real_photo_texture=False`,
  `training_use="forbidden-as-multiview"`, `coverage_use="forbidden"`,
  `clearance_use="forbidden-as-evidence"`, `trust_effect="none"`);
- plan not covering exactly the eight supported slots.

`_Frozen` uses `extra='forbid'`, `frozen=True`,
`validate_assignment=True`; `model_copy(update=...)` re-validates
through `model_validate` so trust-locked Literal fields cannot be
silently mutated.

## Files produced (all new GLM-owned paths)

- `pipeline/synthetic_village/prop_geometry_v2.py` — pure-Python v1
  model + canonical LF JSON serializer. `PropPart`, `PropSlotPlan`,
  `PropGeometryV2Plan` frozen models; `BATCH35_SOURCE_SHAS`,
  `REQUIRED_SEMANTIC_KINDS`, `EXACT_COUNT_KINDS`, `MIN_COUNT_KINDS`
  contract tables; `CANONICAL_ALLOWED_INTERSECTIONS` frozen
  declarations; eight `_*_plan()` builders; `PROP_SLOTS_V2` frozen
  dict; `build_prop_geometry_v2_plan()` re-validating accessor;
  `serialize_prop_geometry_v2_plan()` content-addressable serializer.
- `tests/test_prop_geometry_v2.py` — 27 RED tests covering every
  fail-closed clause above plus positive canonical-plan coverage
  (water-jar four kinds, bench backless, handcart exactly two wheels,
  plan round-trip, trust-field Literal lock).

No Codex-owned files were modified.
`scripts/blender/build_synthetic_village.py::_build_prop` is NOT
touched — it will be replaced only after Codex signs off on this
schema, per FEEDBACK-IMAGE2-040 §6.

## Verification (fresh runs)

```powershell
# Python RED tests
.venv\Scripts\python.exe -m pytest tests\test_prop_geometry_v2.py -q --no-header
# Result: 27 passed in 0.12s

# Ruff
.venv\Scripts\python.exe -m ruff check pipeline\synthetic_village\prop_geometry_v2.py tests\test_prop_geometry_v2.py
# Result: All checks passed!

# Content-addressed plan SHA (deterministic; re-run reproduces the same SHA)
.venv\Scripts\python.exe -c "import hashlib; from pipeline.synthetic_village.prop_geometry_v2 import build_prop_geometry_v2_plan, serialize_prop_geometry_v2_plan; blob = serialize_prop_geometry_v2_plan(build_prop_geometry_v2_plan()); print(hashlib.sha256(blob).hexdigest())"
# Result: 532e227dad96aea69fc3eeaf9f8d27760f4e96a3ac528acef188929e728e51de
```

Commit: `381f243 feat: define Batch35 prop geometry contracts`
(path-limited to the two new GLM-owned files; no Codex WIP touched).

## Pre-existing failures disclosed (not caused by this work)

`tests/test_synthetic_village_blender_runtime.py` has 25 failures with
the same root cause: a stale canary build-report at
`.nantai-studio/synthetic-village/hybrid-v3/work/canary/4f38ecf4...`
whose `build_id` (`4f38ecf4...`) disagrees with the freshly rebuilt
request `build_id` (`29e2bdfb...`). This is a stale-cache / 1.0-preview
reorg artifact, not a regression from `prop_geometry_v2.py` (which is
pure Python, never imported by the blender runtime tests). The
`build_id` tamper guard is correctly fail-closed — the canary build
needs to be rebuilt, not the check relaxed.

## Trust boundary (Literal-locked, must never be promoted)

```text
synthetic               = true
stage                   = design-only
geometry_consistency    = not-verified
real_photo_texture      = false
training_use            = forbidden-as-multiview
coverage_use            = forbidden
clearance_use           = forbidden-as-evidence
trust_effect            = none
```

Batch35 improves the synthetic proxy's near-field geometry references.
It does NOT provide real photographs, real imported geometry/texture,
SfM/3DGS evidence, measured alignment, 360-degree coverage,
arbitrary-coordinate reachability or real Viewer QA.

## Limitations (disclosed, not promoted)

1. **Pure model only.** No Blender geometry, no collision-proxy SHA
   measurement, no exact-build probe, no post-render v2. The
   `collision_proxy_sha256` fields are empty at this stage;
   `finalize_for_build()` binds them only after Codex accepts and a
   real Blender emission measures the geometry.

2. **Panels frozen, not averaged.** Where Batch35 panels disagree
   (firewood log count, tool count, slat count, bench back presence),
   one canonical value is picked per slot per FEEDBACK-IMAGE2-040 §3.
   The bench is explicitly backless, dropping the current `_build_prop`
   back-like slab.

3. **AABB interpenetration only.** The `allowed_intersections` check
   uses axis-aligned bounding boxes from `render_bounds_m`, not the
   actual oriented mesh. Two parts whose OBBs overlap but AABBs do not
   would not be flagged. This is conservative on the safe side — every
   AABB overlap is caught — and is the v1 minimum. OBB-level checks
   can be added when the Blender emitter measures real geometry.

4. **Image proportions are guidance only.** Per FEEDBACK-IMAGE2-040 §2,
   `ScenePlan width_m/depth_m/height_m` and instance transforms remain
   authoritative. The `envelope_m` values here are metric prop
   envelopes, not derived from image pixel ratios.

5. **No prop instance placement.** This plan defines the canonical part
   graph per slot, not where instances go in the village. Instance
   placement stays with `ScenePlan` and the existing
   `_build_prop` caller contract.

## Ownership and stop conditions respected

- No edits to Codex WIP files (`local_production_runner.py`,
  `studio_server.py`, `scripts/synthetic_village.py`,
  `production_render.py`, `render_synthetic_village.py`,
  `production_quality_gates.py`, `ktx2_toolchain.py`,
  `test_ktx2_toolchain.py`, `web/data/`, `local_orbit_audit.py`).
- No edits to `scripts/blender/build_synthetic_village.py::_build_prop`
  — replacement happens only after Codex signs off.
- No `web/data/`, registry, Release, `accepted:true` or
  `Reviewer: Codex` field written anywhere.
- No Batch35 image was used as a texture, calibrated view, SfM input,
  or clearance evidence.
- Single `main` branch, path-limited commit (`git commit -- <two new
  paths>`). No `git add -A` / `commit -a`. No `git push` performed;
  GitHub operations, if needed, will use the temporary per-command proxy
  `git -c http.proxy=http://127.0.0.1:7890 ...` preceded by an
  `ls-remote` SHA verification.

## Next step (after Codex accepts this pure model)

Per FEEDBACK-IMAGE2-040 §6–§8, in order:

1. Emit each prop through the existing Blender builder using the
   canonical part graph (replace `_build_prop` block-body proxies).
2. Keep material slots registered independently; never project these
   boards as textures or use opaque camera-facing billboards.
3. Produce a private exact build and five-direction probe per prop
   (front/side/rear/top/underside-contact).
4. Rerun the six production roles, clearance/visibility and
   post-render v2.
5. Return content/report SHAs and private RGB paths to Codex as
   `candidate / Reviewer: pending Codex`. Do not edit `web/data/`,
   register defaults or claim acceptance.

Review status is left pending. Codex may sign off or request changes;
this candidate is not promoted to `accepted` until then.
