# Batch24 Exact-266 Perimeter-Closure Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify an additive, content-addressed 48-root Batch24 Blender overlay on the immutable exact-218 base, producing an honest exact-266 scene and reciprocal roaming evidence.

**Architecture:** A new frozen `PerimeterClosurePlan` owns only instances 219–266 and binds all sixteen Batch24 design hashes without reading geometry from pixels. A separate runtime verifies an explicit exact-218 base, invokes a measured Blender script, appends the overlay, and emits a canonical report; an audit caller then materializes sixteen bidirectional cameras and reuses existing clearance, six-layer, visibility and post-render contracts.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, Blender 4.5 LTS Python API, canonical JSON/SHA-256, existing Nantai synthetic-village production contracts.

---

## File map

- `pipeline/synthetic_village/perimeter_closure_module.py` — immutable 8-sector/48-part plan and verifier.
- `pipeline/synthetic_village/perimeter_closure_runtime.py` — exact-218 bindings, content-addressed build request/report and runner.
- `scripts/blender/apply_perimeter_closure_modules.py` — Blender-only exact-218 → exact-266 geometry builder.
- `pipeline/synthetic_village/perimeter_closure_audit.py` — sixteen-camera reciprocal/seam/render audit contract and report verifier.
- `scripts/blender/render_perimeter_closure_audit.py` — Blender camera materialization and six-layer render entrypoint.
- `scripts/synthetic_village.py` — additive `build-perimeter-closure` and `audit-perimeter-closure` commands.
- `tests/test_synthetic_village_perimeter_closure_module.py` — plan red/green tests.
- `tests/test_synthetic_village_perimeter_closure_runtime.py` — runtime/report red/green tests.
- `tests/test_synthetic_village_perimeter_closure_blender.py` — Blender script pure-boundary and real-build tests.
- `tests/test_synthetic_village_perimeter_closure_audit.py` — camera/report red/green tests.
- `tests/test_synthetic_village_cli.py` — bounded-truth CLI tests.
- `handoff/FEEDBACK-HANDOFF-CODEX-028-batch24-exact266-perimeter-closure.md` — final machine evidence and honest visual review.
- `README.md` — exact-266 usage and trust boundary.

### Task 1: Lock the plan schema, source bindings and exact instance partition

**Files:**

- Create: `tests/test_synthetic_village_perimeter_closure_module.py`
- Create: `pipeline/synthetic_village/perimeter_closure_module.py`

- [ ] **Step 1: Write the first failing canonical-plan tests**

Add tests that import:

```python
from pipeline.synthetic_village.perimeter_closure_module import (
    PERIMETER_CLOSURE_MODULE_ORDER,
    PerimeterClosureError,
    PerimeterClosurePlan,
    build_default_perimeter_closure_plan,
    canonical_perimeter_closure_plan_bytes,
    perimeter_closure_plan_sha256,
    verify_perimeter_closure_plan,
)
```

The fixture loads the private Batch24 candidate `manifest.json`, passes its actual SHA-256
and the fresh exact-218 production-plan/topology bindings, and asserts:

```python
assert PERIMETER_CLOSURE_MODULE_ORDER == (
    "closure-upstream",
    "closure-northeast",
    "closure-east",
    "closure-southeast",
    "closure-downstream",
    "closure-southwest",
    "closure-west",
    "closure-northwest",
)
assert [p.instance_id for m in plan.modules for p in m.parts] == list(
    range(219, 267)
)
assert all(len(module.parts) == 6 for module in plan.modules)
assert canonical_perimeter_closure_plan_bytes(plan).endswith(b"\n")
assert perimeter_closure_plan_sha256(plan) == hashlib.sha256(
    canonical_perimeter_closure_plan_bytes(plan)
).hexdigest()
```

Also assert exact role order per module:

```python
(
    "terrain-contact",
    "bidirectional-corridor",
    "support-retaining",
    "drainage-water",
    "boundary-seam",
    "vegetation-enclosure",
)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_synthetic_village_perimeter_closure_module.py
```

Expected: collection fails with
`ModuleNotFoundError: pipeline.synthetic_village.perimeter_closure_module`.

- [ ] **Step 3: Implement the minimal frozen plan**

Create literal/frozen Pydantic models with these public signatures:

```python
def build_default_perimeter_closure_plan(
    *,
    batch24_manifest: Mapping[str, object],
    batch24_manifest_sha256: str,
    production_plan_sha256: str,
    topology_plan_sha256: str,
    terrain_height_at: Callable[[float, float], float],
) -> PerimeterClosurePlan: ...

def canonical_perimeter_closure_plan_bytes(
    plan: PerimeterClosurePlan,
) -> bytes: ...

def perimeter_closure_plan_sha256(plan: PerimeterClosurePlan) -> str: ...

def verify_perimeter_closure_plan(
    plan: PerimeterClosurePlan,
    *,
    batch24_manifest: Mapping[str, object],
) -> None: ...
```

The builder must find every source by exact filename, verify its SHA against the
manifest, create the literal instance ranges, use explicit sector anchors, and
sample terrain Z for every center/anchor. Do not read image dimensions as
geometry.

- [ ] **Step 4: Add fail-closed mutations**

Write one test per mutation before implementing its validator:

- missing or extra sector;
- out-of-order module;
- duplicate/gapped instance;
- wrong semantic-role order;
- malformed/uppercase source SHA;
- reciprocal and section source swapped;
- manifest asset missing;
- source hash mismatch;
- non-finite coordinate or non-positive extent;
- inner/outer anchor equal;
- previous/next seam equal;
- `synthetic`, `verification_level`, `geometry_usability` or `trust_effect`
  promoted.

Each test must fail for the named invariant, then pass after the minimal
validator is added.

- [ ] **Step 5: Prove v1 compatibility**

Record canonical bytes and SHA for the current environment and reciprocal plans
before importing the new module, rebuild the same plans after import, and assert
byte-for-byte equality. Run:

```powershell
python -m pytest -q `
  tests/test_synthetic_village_perimeter_closure_module.py `
  tests/test_synthetic_village_environment_module.py `
  tests/test_synthetic_village_reciprocal_route_module.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add -- `
  pipeline/synthetic_village/perimeter_closure_module.py `
  tests/test_synthetic_village_perimeter_closure_module.py
git commit -m "feat(scene): add Batch24 perimeter closure plan" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- `
  pipeline/synthetic_village/perimeter_closure_module.py `
  tests/test_synthetic_village_perimeter_closure_module.py
```

### Task 2: Bind the exact-218 base and define exact-266 runtime evidence

**Files:**

- Create: `tests/test_synthetic_village_perimeter_closure_runtime.py`
- Create: `pipeline/synthetic_village/perimeter_closure_runtime.py`

- [ ] **Step 1: Write failing request determinism tests**

Define the wished-for API:

```python
request = build_perimeter_closure_runtime_request(
    base_build_directory=exact218_directory,
    plan=closure_plan,
    batch24_manifest_path=batch24_manifest_path,
    blender_executable=blender_executable,
    material_registry=material_registry,
)
```

Assert the request binds:

```python
assert request.base_canonical_roots == 218
assert request.overlay_canonical_roots == 48
assert request.canonical_roots == 266
assert request.object_registry[0].instance_id == 1
assert request.object_registry[-1].instance_id == 266
assert request.base_object_registry_sha256 == exact218_report.object_registry_sha256
assert request.perimeter_closure_plan_sha256 == perimeter_closure_plan_sha256(
    closure_plan
)
```

Assert canonical request bytes and build ID are deterministic across fresh
processes.

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest -q tests/test_synthetic_village_perimeter_closure_runtime.py
```

Expected: module import fails.

- [ ] **Step 3: Implement request construction**

Create:

```python
def build_perimeter_closure_runtime_request(
    *,
    base_build_directory: Path,
    plan: PerimeterClosurePlan,
    batch24_manifest_path: Path,
    blender_executable: Path,
    material_registry: AssetRegistry,
) -> PerimeterClosureRuntimeRequest: ...
```

It must explicitly load and verify:

```text
reciprocal-route-build-request.json
reciprocal-route-build-report.json
village-reciprocal-route.blend
```

and bind their bytes plus Blender/script/material identities. The 48 appended
registry rows must be derived from plan parts only.

- [ ] **Step 4: Write report verifier tests before implementation**

The valid report literal-locks:

```python
assert report.counts.base_canonical_roots == 218
assert report.counts.overlay_canonical_roots == 48
assert report.counts.canonical_roots == 266
assert report.validation.base_registry_preserved
assert report.validation.overlay_registry_exact
assert report.validation.material_bindings_exact
assert report.validation.terrain_support_contacts_passed
assert report.validation.corridor_continuity_passed
assert report.validation.drainage_continuity_passed
assert report.validation.sector_seams_passed
```

Mutate every request/report/artifact hash, count, registry row and validation
boolean separately. Verify each mutation fails closed.

- [ ] **Step 5: Implement content-addressed runner**

Create:

```python
def run_perimeter_closure_build(
    request: PerimeterClosureRuntimeRequest,
    *,
    build_root: Path = DEFAULT_PERIMETER_CLOSURE_BUILD_ROOT,
    timeout_seconds: int = DEFAULT_PERIMETER_CLOSURE_BUILD_TIMEOUT_SECONDS,
) -> PerimeterClosureBuildResult: ...
```

Follow the existing staging/snapshot pattern. Final entries are exactly:

```text
perimeter-closure-build-request.json
perimeter-closure-build-report.json
village-perimeter-closure.blend
```

Existing builds are immutable and reusable only after complete re-verification.

- [ ] **Step 6: Run Task 2 tests and commit**

```powershell
python -m pytest -q `
  tests/test_synthetic_village_perimeter_closure_runtime.py `
  tests/test_synthetic_village_reciprocal_route_production_blender.py
git add -- `
  pipeline/synthetic_village/perimeter_closure_runtime.py `
  tests/test_synthetic_village_perimeter_closure_runtime.py
git commit -m "feat(scene): add exact-266 closure runtime contract" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- `
  pipeline/synthetic_village/perimeter_closure_runtime.py `
  tests/test_synthetic_village_perimeter_closure_runtime.py
```

### Task 3: Implement Blender geometry with real contact and seam checks

**Files:**

- Create: `tests/test_synthetic_village_perimeter_closure_blender.py`
- Create: `scripts/blender/apply_perimeter_closure_modules.py`

- [ ] **Step 1: Write failing pure-script boundary tests**

Load the Blender script as a normal Python module with a stubbed `bpy`, then
assert:

```python
assert module.REQUEST_SCHEMA == (
    "nantai.synthetic-village.perimeter-closure-runtime-request.v1"
)
assert module.REPORT_SCHEMA == (
    "nantai.synthetic-village.perimeter-closure-build-report.v1"
)
assert module.EXPECTED_BASE_ROOTS == 218
assert module.EXPECTED_OVERLAY_ROOTS == 48
assert module.EXPECTED_TOTAL_ROOTS == 266
```

Test duplicate JSON keys, NaN/Infinity, unknown keys, wrong registry segments,
wrong script SHA and path escape.

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest -q tests/test_synthetic_village_perimeter_closure_blender.py
```

Expected: script path does not exist.

- [ ] **Step 3: Implement request validation and base-scene verification**

The script must load the explicit base `.blend`, verify exactly 218
`nv_root=True` objects and compare every base object ID/instance ID with the
request. Any extra, missing or changed base root aborts before geometry writes.

- [ ] **Step 4: Implement six geometry builders**

Add focused functions:

```python
def _build_terrain_contact(part, collection): ...
def _build_bidirectional_corridor(part, collection): ...
def _build_support_retaining(part, collection): ...
def _build_drainage_water(part, collection): ...
def _build_boundary_seam(part, collection): ...
def _build_vegetation_enclosure(part, collection): ...
```

Each returns a canonical root plus explicit mesh children. Roots are tagged
from the registry; children never carry `nv_root=True`. Corridors must include
both anchor endpoints, supports must reach terrain, drains must remain open,
and enclosure geometry must leave the camera sky/corridor open.

- [ ] **Step 5: Add measured geometry validators**

Test pure numeric helpers first, then implement:

```python
def _contact_gap_m(supported_bounds, terrain_bounds) -> float: ...
def _endpoint_gap_m(a, b) -> float: ...
def _validate_sector_geometry(module_roots, plan_module) -> dict: ...
def _validate_neighbor_seams(module_results) -> dict: ...
```

Use literal tolerances from the request. A fabricated success boolean without
measured distances is forbidden.

- [ ] **Step 6: Write report and real Blender smoke test**

The report must include per-sector measured contact, corridor, drainage and
previous/next seam gaps plus aggregate pass booleans. Run:

```powershell
python -m pytest -q tests/test_synthetic_village_perimeter_closure_blender.py
```

Then invoke the real checked-in Windows Blender executable against a minimal
fixture request and assert an exact-266 artifact and verified report.

- [ ] **Step 7: Commit Task 3**

```powershell
git add -- `
  scripts/blender/apply_perimeter_closure_modules.py `
  tests/test_synthetic_village_perimeter_closure_blender.py
git commit -m "feat(scene): build Batch24 closure geometry in Blender" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- `
  scripts/blender/apply_perimeter_closure_modules.py `
  tests/test_synthetic_village_perimeter_closure_blender.py
```

### Task 4: Add the CLI caller with bounded truth

**Files:**

- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_cli.py`

- [ ] **Step 1: Write failing parser/caller tests**

Test:

```text
synthetic_village.py build-perimeter-closure
  --base-build <exact218-dir>
  --batch24-manifest <manifest.json>
  --build-root <private-dir>
  --blender <third/blender/blender.exe>
```

Assert the command forwards only explicit paths, calls the new builder once and
prints JSON containing only IDs, hashes, counts, artifact/report paths and:

```json
{
  "synthetic": true,
  "verification_level": "L0",
  "geometry_usability": "preview-only",
  "trust_effect": "none-quality-filter-only"
}
```

Missing paths, mismatched hashes and build failures return non-zero and do not
print success-shaped output.

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
python -m pytest -q tests/test_synthetic_village_cli.py -k perimeter_closure
```

Expected: parser rejects the unknown command.

- [ ] **Step 3: Implement the additive command**

Add lazy imports mirroring existing environment/reciprocal builders. Do not
change existing commands or defaults.

- [ ] **Step 4: Run and commit**

```powershell
python -m pytest -q tests/test_synthetic_village_cli.py
git add -- scripts/synthetic_village.py tests/test_synthetic_village_cli.py
git commit -m "feat(cli): add exact-266 closure build caller" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- `
  scripts/synthetic_village.py tests/test_synthetic_village_cli.py
```

### Task 5: Add sixteen-camera reciprocal and render audit

**Files:**

- Create: `tests/test_synthetic_village_perimeter_closure_audit.py`
- Create: `pipeline/synthetic_village/perimeter_closure_audit.py`
- Create: `scripts/blender/render_perimeter_closure_audit.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_cli.py`

- [ ] **Step 1: Write failing camera materialization tests**

Build exactly two cameras per sector:

```python
assert len(plan.cameras) == 16
assert {camera.direction for camera in plan.cameras} == {"inward", "outward"}
assert all(camera.eye_height_m == 1.6 for camera in plan.cameras)
assert all(camera.source_plan_sha256 == closure_plan_sha for camera in plan.cameras)
```

Inward cameras stand at outer anchors and look toward inner anchors; outward
cameras reverse them. Terrain Z is sampled independently for each camera.
Identical, mirrored-only, underground, floating or near-surface pairs fail
closed.

- [ ] **Step 2: Implement the canonical audit plan/report**

Expose:

```python
def build_perimeter_closure_audit_plan(...) -> PerimeterClosureAuditPlan: ...
def run_perimeter_closure_audit(...) -> PerimeterClosureAuditResult: ...
def verify_perimeter_closure_audit_report(...) -> None: ...
```

Bind exact-266 build/report/artifact hashes, plan SHA, renderer capability,
six-layer frame identity, post-render policy SHA and every camera ID.

- [ ] **Step 3: Implement the Blender render entrypoint**

For each camera, render the existing six production layers and RGB. Emit
measured clearance, valid-pixel, target visibility and seam visibility values.
The script may reuse existing renderer helpers but must not duplicate or
reinterpret trust.

- [ ] **Step 4: Write fail-closed report tests**

Separately mutate:

- camera count/direction/ID;
- build, plan, renderer or policy SHA;
- missing layer or mismatched frame identity;
- clearance/visibility measurement;
- RGB artifact hash;
- post-render result;
- `modeled-unverified` trust fields.

Verify every mutation is rejected.

- [ ] **Step 5: Add `audit-perimeter-closure` CLI**

Require explicit exact-266 build directory and audit output root. Print bounded
truth plus rejected camera IDs and measured gate counts.

- [ ] **Step 6: Run and commit**

```powershell
python -m pytest -q `
  tests/test_synthetic_village_perimeter_closure_audit.py `
  tests/test_synthetic_village_cli.py -k "perimeter_closure"
git add -- `
  pipeline/synthetic_village/perimeter_closure_audit.py `
  scripts/blender/render_perimeter_closure_audit.py `
  scripts/synthetic_village.py `
  tests/test_synthetic_village_perimeter_closure_audit.py `
  tests/test_synthetic_village_cli.py
git commit -m "feat(scene): audit exact-266 reciprocal roaming views" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- `
  pipeline/synthetic_village/perimeter_closure_audit.py `
  scripts/blender/render_perimeter_closure_audit.py `
  scripts/synthetic_village.py `
  tests/test_synthetic_village_perimeter_closure_audit.py `
  tests/test_synthetic_village_cli.py
```

### Task 6: Produce fresh exact-266 Blender and render evidence

**Files:**

- Create under ignored private roots:
  `.nantai-studio/synthetic-village/hybrid-v4/work/perimeter-closure-builds/`
- Create under ignored private roots:
  `.nantai-studio/synthetic-village/hybrid-v4/work/perimeter-closure-audits/`

- [ ] **Step 1: Verify all explicit inputs**

Recompute and record:

- Batch24 manifest SHA and every manifest-declared payload checksum;
- exact-218 request/report/blend SHA values;
- Blender executable/version SHA;
- closure plan/runtime/render script SHA values;
- material registry/binding SHA values.

- [ ] **Step 2: Run the real exact-266 caller**

```powershell
.venv\Scripts\python.exe scripts\synthetic_village.py `
  build-perimeter-closure `
  --base-build .nantai-studio\synthetic-village\hybrid-v4\work\reciprocal-route-modules\ebb936346ea2f31a4d551f6fa9bf64d5e48bcac46593fa0ff195b34d699f6cdd `
  --batch24-manifest .nantai-studio\synthetic-village\hybrid-v4-candidates\batch24\manifest.json `
  --build-root .nantai-studio\synthetic-village\hybrid-v4\work\perimeter-closure-builds `
  --blender third\blender\blender.exe
```

Expected: verified exact-266 request/report/artifact directory.

- [ ] **Step 3: Run the real sixteen-camera audit**

Run `audit-perimeter-closure` against the exact build ID. Require all sixteen
preflights and six-layer identities to be present. Individual quality failures
remain rejected and must not be rewritten as success.

- [ ] **Step 4: Inspect every RGB at original resolution**

Record for each camera:

- inward/outward role readability;
- route/water continuity;
- neighbor seam visibility;
- terrain and support contact;
- near-surface obstruction;
- sky/world/background quality;
- repeated/stretching material defects;
- accepted/rejected decision.

Do not treat a passing synthetic frame as real reconstruction evidence.

- [ ] **Step 5: Run focused and regression tests**

```powershell
python -m pytest -q `
  tests/test_synthetic_village_perimeter_closure_module.py `
  tests/test_synthetic_village_perimeter_closure_runtime.py `
  tests/test_synthetic_village_perimeter_closure_blender.py `
  tests/test_synthetic_village_perimeter_closure_audit.py `
  tests/test_synthetic_village_environment_module.py `
  tests/test_synthetic_village_reciprocal_route_module.py `
  tests/test_synthetic_village_cli.py
.venv\Scripts\ruff.exe check `
  pipeline/synthetic_village/perimeter_closure_module.py `
  pipeline/synthetic_village/perimeter_closure_runtime.py `
  pipeline/synthetic_village/perimeter_closure_audit.py `
  scripts/synthetic_village.py `
  tests/test_synthetic_village_perimeter_closure_*.py
```

Expected: all selected tests and lint pass.

### Task 7: Document, review, commit and push evidence

**Files:**

- Create: `handoff/FEEDBACK-HANDOFF-CODEX-028-batch24-exact266-perimeter-closure.md`
- Modify: `README.md`
- Modify: `AGENTS.md` only if current evidence materially changes shared facts.

- [ ] **Step 1: Write the evidence report**

Record exact request/report/artifact/plan/policy/frame SHAs, root counts,
per-sector measured contact/seam values, 16-camera gate matrix, RGB findings,
rejected camera IDs, runtime durations and external limits.

- [ ] **Step 2: Update README usage**

Add explicit commands and state:

```text
exact-266 = synthetic modeled-unverified Blender geometry
not real mesh
not real-photo texture
not calibrated multiview
not metric-aligned unless separate measured evidence proves it
```

- [ ] **Step 3: Review current GLM/Opus changes before staging**

Inspect every concurrent commit and working-tree path. Do not stage unrelated
files. Run:

```powershell
git status --short --branch
git log --oneline origin/main..main
git diff --check
```

- [ ] **Step 4: Commit only owned documentation**

```powershell
git add -- `
  README.md `
  handoff/FEEDBACK-HANDOFF-CODEX-028-batch24-exact266-perimeter-closure.md
git commit -m "docs(scene): record Batch24 exact-266 evidence" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- `
  README.md `
  handoff/FEEDBACK-HANDOFF-CODEX-028-batch24-exact266-perimeter-closure.md
```

- [ ] **Step 5: Push main**

Run `git push origin main`. On a transient GitHub failure, wait five seconds
and retry without force-pushing or rewriting shared history.
