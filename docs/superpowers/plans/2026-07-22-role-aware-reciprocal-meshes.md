# Role-aware reciprocal meshes implementation plan

> Date: 2026-07-22
> Owner: Codex
> Status: approved design, ready for inline execution

## Outcome

Replace the universal four-panel tunnel emitted for every reciprocal-route
part with canonical, role-aware blockout geometry. Preserve the exact 218
canonical roots, the existing camera and attachment bindings, and all
fail-closed trust labels. Re-run the content-addressed exact-218 build,
Phase 4.3 probe, and six-role production caller before making any quality
claim.

This is a modeled-unverified blockout correction. It does not add real-photo
textures, surveyed geometry, or production trust.

## Design boundary

- Add an explicit `geometry_family` declaration to every
  `ReciprocalRouteModulePart`. Runtime code must consume this field verbatim;
  it must not infer geometry from `part_id`, module names, material names, or
  filenames.
- Keep canonical root instance IDs exactly `176..218`. No registry row is
  added, removed, or renumbered.
- Enforce a fail-closed geometry-family/semantic allowlist in the Pydantic
  model and again in the Blender runtime request validator.
- Route-bearing families are explicit. Phase 4.3 route sampling must ignore
  guards, props, drains, vegetation, retaining walls, and building-only
  structures.
- Open routes use a walkable slab plus low path-edge curbs. The curbs provide
  real left/right BVH measurements without creating full-height corridor
  walls. Their upward ray must remain open (`None`, meaning unbounded within
  the probe distance).
- Covered passages retain a finite roof and side clearance. Bridge decks and
  elevated/open route surfaces remain roofless.
- Non-route families receive class-appropriate simplified geometry: building
  shell, timber frame, drainage channel, retaining wall/step, guard rail,
  prop, and vegetation band. All remain one semantic per canonical part.
- Do not change Task-4 camera poses, clearance or post-render thresholds,
  runner/journal/Studio/Viewer code, topology-proxy derivation, or the
  recipe-derived junction vegetation opening.

## Task 1: lock canonical geometry classification with TDD

Files:

- Modify `pipeline/synthetic_village/reciprocal_route_module.py`
- Modify `tests/test_synthetic_village_reciprocal_route_module.py`

Steps:

1. Add failing tests that require every default part to serialize an explicit
   geometry family and that reject a missing, unknown, or semantically
   incompatible family.
2. Add a closed `GeometryFamily` literal and a family-to-semantic allowlist.
3. Extend `ReciprocalRouteModulePart` with required `geometry_family` and a
   model validator that rejects incompatible declarations.
4. Extend every default `part_specs` declaration with its authored family.
   Construction-time declarations may be keyed by the known part record, but
   the serialized plan is the only Blender runtime source of truth.
5. Run the focused model tests and Ruff.

Verification:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_module.py -q
python -m ruff check pipeline/synthetic_village/reciprocal_route_module.py tests/test_synthetic_village_reciprocal_route_module.py
```

## Task 2: replace universal runtime tunnel geometry with TDD

Files:

- Modify `scripts/blender/apply_reciprocal_route_modules.py`
- Modify `tests/test_synthetic_village_reciprocal_route_module_runtime.py`

Steps:

1. Replace the old universal-passage tests with failing tests for:
   - open path has a slab and low curbs but no ceiling;
   - covered passage retains a finite roof and full-height sides;
   - bridge deck is open overhead;
   - building/frame/drain/retaining/guard/prop/vegetation families serialize
     to non-identical vertices/faces appropriate to their class;
   - rotated local offsets remain correct;
   - absent, unknown, or semantic-incompatible classification fails closed.
2. Add a pure classification validator used by `_validate_request` and
   `_module_geometry`.
3. Split geometry assembly into small pure family builders using only
   canonical `part_layout` coordinates and the explicit family.
4. Keep the existing root/mesh render tags, material binding, exact-43 module
   mesh count, exact-218 canonical root validation, topology proxies, and
   junction vegetation logic unchanged.
5. Run focused runtime tests and Ruff.

Verification:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_module_runtime.py -q
python -m ruff check scripts/blender/apply_reciprocal_route_modules.py tests/test_synthetic_village_reciprocal_route_module_runtime.py
```

## Task 3: make Phase 4.3 route sampling family-aware with TDD

Files:

- Modify `scripts/blender/probe_reciprocal_route_modules.py`
- Add or modify the focused runtime tests that load this Blender script with
  stubs; keep `tests/test_synthetic_village_reciprocal_route_probe.py` for the
  host-side report contract.

Steps:

1. Add failing tests proving only route-bearing families contribute route
   samples and every module retains at least one measurable route part.
2. Sample real route-part centers instead of interpolating through unrelated
   structure/prop centers. Use all route-bearing centers when there are five
   or fewer and an evenly selected deterministic subset when there are more.
3. Keep a perpendicular miss fail-closed. Keep the already-supported upward
   miss as truthful open overhead; require finite clearance only where the
   explicit family is covered.
4. Reject missing/unknown classifications before scene probing.
5. Preserve camera-placement versus module-attachment topology checks.

Verification:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_module_runtime.py tests/test_synthetic_village_reciprocal_route_probe.py -q
python -m ruff check scripts/blender/probe_reciprocal_route_modules.py tests/test_synthetic_village_reciprocal_route_probe.py
```

## Task 4: integration verification and small commit

Files:

- Only the Task 1-3 source/tests above

Steps:

1. Run all reciprocal-route module, runtime, probe, production, Blender
   runner, and six-role caller tests.
2. Run Ruff on all changed Python files.
3. Inspect `git diff --check` and the path-limited diff; do not stage GLM SH
   work or private assets.
4. Commit the code/tests with the required co-author trailer and push `main`.

Verification:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_module.py tests/test_synthetic_village_reciprocal_route_module_runtime.py tests/test_synthetic_village_reciprocal_route_probe.py tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production_blender.py -q
python -m ruff check pipeline/synthetic_village/reciprocal_route_module.py scripts/blender/apply_reciprocal_route_modules.py scripts/blender/probe_reciprocal_route_modules.py tests/test_synthetic_village_reciprocal_route_module.py tests/test_synthetic_village_reciprocal_route_module_runtime.py tests/test_synthetic_village_reciprocal_route_probe.py
git diff --check -- pipeline/synthetic_village/reciprocal_route_module.py scripts/blender/apply_reciprocal_route_modules.py scripts/blender/probe_reciprocal_route_modules.py tests/test_synthetic_village_reciprocal_route_module.py tests/test_synthetic_village_reciprocal_route_module_runtime.py tests/test_synthetic_village_reciprocal_route_probe.py
```

## Task 5: fresh machine evidence

Private outputs:

- A new content-addressed reciprocal plan/build directory under
  `.nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules/`
- A new Phase 4.3 probe directory
- A new six-role production batch directory under
  `.nantai-studio/sv-prod-win/`

Steps:

1. Regenerate the canonical reciprocal plan and object registry.
2. Run the pinned Windows Blender 4.5.11 exact-218 build adapter.
3. Run a fresh Phase 4.3 mesh/collision probe against that exact `.blend`.
4. Run the six-role production caller with fresh preflight, six layers,
   visibility evidence, post-render v2 policy, journal, and ledger records.
5. Visually inspect all six RGB outputs against the previous repeated-tunnel
   baseline. Record every remaining defect; do not label a failed or merely
   modeled-unverified result production-ready.
6. Write one concise handoff containing the new plan/script/build/probe/report
   SHAs, category counts, six role paths and decisions, and remaining distance
   to real geometry plus real-photo texture parity.
7. Commit only the handoff and any deliberately updated tracked documentation,
   then push `main`.

## Stop conditions

- If exact-218 root count, registry order, instance IDs, semantic IDs, material
  bindings, or trust tags change unexpectedly, stop before rendering.
- If an open route needs a fake ceiling to pass, keep it failed and fix the
  measurement contract instead.
- If a covered route has no finite roof measurement, keep Phase 4.3 failed.
- If Blender, build, probe, or caller evidence is stale or SHA-mismatched, do
  not reuse it for acceptance.
