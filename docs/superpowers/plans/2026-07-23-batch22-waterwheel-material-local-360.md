# Batch 22 Waterwheel Material and Local 360 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the later-module PBR input contract, replace the faceted waterwheel proxy with a readable open 12-spoke/12-paddle wheel, and prove the exact-218 scene from eight bound local azimuths while publishing twelve replaceable image2 design references.

**Architecture:** The two content-addressed Blender runtimes remain self-contained and each validates material metadata, deterministic UVs, white `nv_surface_color`, and tangents before saving. The existing waterwheel root/instance is upgraded in place. A separate local-orbit plan binds the exact build and materializes a derived audit plan for the existing six-layer renderer without changing the canonical 180-camera registry or its trust claims.

**Tech Stack:** Python 3.11+, Pydantic v2, Blender 4.5 LTS Python API, NumPy/Pillow for mask audits, pytest, Ruff, Git/GitHub Release, OpenAI image2.

---

## File map

- Modify `scripts/blender/apply_environment_modules.py`: environment-module UV/colour contract and open wheel geometry.
- Modify `scripts/blender/apply_reciprocal_route_modules.py`: reciprocal-module UV/colour contract.
- Modify `pipeline/synthetic_village/environment_module_runtime.py`: record and verify material-contract counts in the 175-root build report.
- Modify `pipeline/synthetic_village/reciprocal_route_module_runtime.py`: record and verify material-contract counts in the exact-218 report.
- Create `pipeline/synthetic_village/local_orbit_audit.py`: content-addressed eight-direction audit plan and frame audit.
- Create `pipeline/synthetic_village/local_orbit_runner.py`: exact-218 preflight/render adapter using existing Blender scripts and quality policies.
- Modify `scripts/synthetic_village.py`: private CLI entry points for plan/build/audit orchestration.
- Modify existing environment/reciprocal tests and create local-orbit tests.
- Create `handoff/FEEDBACK-HANDOFF-CODEX-027-batch22-material-local-360.md`: final identities, evidence and honest boundary.
- Update `README.md`: Release usage and the modeled-scene versus real-reconstruction distinction.

The GLM-owned `scripts/prepare_import.py`, `pipeline/registration_quality.py`,
`pipeline/training_provenance.py`, `scripts/emit_training_provenance.py` and
their tests are outside this plan.

### Task 1: Environment runtime material contract

**Files:**
- Modify: `tests/test_synthetic_village_environment_module_runtime.py`
- Modify: `scripts/blender/apply_environment_modules.py`
- Modify: `pipeline/synthetic_village/environment_module_runtime.py`

- [ ] **Step 1: Write failing metadata and source-contract tests**

Add tests that import the Blender runtime with the existing `bpy` stub and
exercise a new pure validator:

```python
@pytest.mark.parametrize("policy", [
    "world-xy", "dominant-axis-box", "roof-slope",
    "object-long-axis", "leaf-card",
])
def test_environment_material_contract_accepts_bound_uv_metadata(
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
) -> None:
    runtime = _load_blender_runtime(monkeypatch)
    material = {
        "uv_policy": policy,
        "nv_nominal_tile_m": 0.8,
        "nv_surface_color_input": "nv_surface_color",
    }
    assert runtime._material_contract(material) == (
        policy, 0.8, "nv_surface_color",
    )


@pytest.mark.parametrize("material", [
    {},
    {"uv_policy": "unknown", "nv_nominal_tile_m": 1.0,
     "nv_surface_color_input": "nv_surface_color"},
    {"uv_policy": "world-xy", "nv_nominal_tile_m": 0.0,
     "nv_surface_color_input": "nv_surface_color"},
    {"uv_policy": "world-xy", "nv_nominal_tile_m": 1.0,
     "nv_surface_color_input": "wrong"},
])
def test_environment_material_contract_rejects_unbound_metadata(
    monkeypatch: pytest.MonkeyPatch,
    material: dict[str, object],
) -> None:
    runtime = _load_blender_runtime(monkeypatch)
    with pytest.raises(runtime.RuntimeBuildError, match="material contract"):
        runtime._material_contract(material)


def test_environment_runtime_no_longer_uses_modulo_uv_proxy() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/blender/apply_environment_modules.py"
    ).read_text(encoding="utf-8")
    assert "% 1.0" not in source
    assert 'name="nv_uv0"' in source
    assert 'name="nv_surface_color"' in source
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/test_synthetic_village_environment_module_runtime.py -q
```

Expected: failures because `_material_contract`, `nv_uv0`, and the colour-layer
contract do not exist and the modulo proxy remains.

- [ ] **Step 3: Implement the self-contained environment contract**

Add these constants and validator near `_assign_uvs_and_tangents`:

```python
UV_POLICIES = frozenset({
    "world-xy", "dominant-axis-box", "roof-slope",
    "object-long-axis", "leaf-card",
})


def _material_contract(material):
    policy = material.get("uv_policy")
    tile_m = material.get("nv_nominal_tile_m")
    color_input = material.get("nv_surface_color_input")
    if (
        policy not in UV_POLICIES
        or isinstance(tile_m, bool)
        or not isinstance(tile_m, (int, float))
        or not math.isfinite(tile_m)
        or tile_m <= 0
        or color_input != "nv_surface_color"
    ):
        raise RuntimeBuildError("module material contract is invalid")
    return policy, float(tile_m), color_input
```

Replace the modulo projection with polygon-aware projection. Use world-space
coordinates for `world-xy`, `dominant-axis-box`, and `roof-slope`; local-space
coordinates for `object-long-axis` and `leaf-card`. Compute polygon UV area as
the maximum triangle-fan area and reject `<= 1e-12`. Write to `nv_uv0` without
modulo wrapping.

Create the neutral colour input exactly as follows:

```python
def _ensure_white_surface_color(obj, layer_name):
    mesh = obj.data
    layer = mesh.color_attributes.get(layer_name)
    if layer is None:
        layer = mesh.color_attributes.new(
            name=layer_name,
            type="FLOAT_COLOR",
            domain="CORNER",
        )
    if (
        layer.data_type != "FLOAT_COLOR"
        or layer.domain != "CORNER"
        or len(layer.data) != len(mesh.loops)
    ):
        raise RuntimeBuildError(
            f"module surface color contract is invalid: {obj.name}",
        )
    mesh.color_attributes.active_color = layer
    index = tuple(mesh.color_attributes).index(layer)
    mesh.color_attributes.active_color_index = index
    mesh.color_attributes.render_color_index = index
    for value in layer.data:
        value.color = (1.0, 1.0, 1.0, 1.0)
    obj["nv_surface_color_mode"] = "white"
```

Call `_material_contract`, project `nv_uv0`, call
`_ensure_white_surface_color`, and then `mesh.calc_tangents(uvmap="nv_uv0")`.
Set `nv_uv_layer="nv_uv0"`, `nv_tangents=True`, and
`nv_material_contract="textured-pbr-v1"` only after all checks pass.

- [ ] **Step 4: Extend the environment report contract**

Add required integer fields to the build counts and required literal-true
validation fields:

```python
textured_module_meshes: int = Field(ge=1)
valid_uv_module_meshes: int = Field(ge=1)
valid_surface_color_module_meshes: int = Field(ge=1)

uv_contracts_match: Literal[True]
surface_color_contracts_match: Literal[True]
```

The Blender report must count the 45 module meshes after reopening/iterating
their saved data and require all three counts to equal `module_mesh_objects`.
The host verifier must reject mismatches rather than default missing fields.

- [ ] **Step 5: Run focused tests and Ruff**

```powershell
python -m pytest tests/test_synthetic_village_environment_module_runtime.py -q
python -m ruff check scripts/blender/apply_environment_modules.py pipeline/synthetic_village/environment_module_runtime.py tests/test_synthetic_village_environment_module_runtime.py
```

Expected: all pass.

- [ ] **Step 6: Commit only Task 1 paths**

```powershell
git add -- scripts/blender/apply_environment_modules.py pipeline/synthetic_village/environment_module_runtime.py tests/test_synthetic_village_environment_module_runtime.py
git commit -m "fix(scene): enforce environment material contract" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- scripts/blender/apply_environment_modules.py pipeline/synthetic_village/environment_module_runtime.py tests/test_synthetic_village_environment_module_runtime.py
```

### Task 2: Reciprocal runtime material contract

**Files:**
- Modify: `tests/test_synthetic_village_reciprocal_route_module_runtime.py`
- Modify: `scripts/blender/apply_reciprocal_route_modules.py`
- Modify: `pipeline/synthetic_village/reciprocal_route_module_runtime.py`

- [ ] **Step 1: Write failing reciprocal contract tests**

Add the same accepted policy matrix and invalid metadata matrix, calling the
reciprocal runtime's own `_material_contract`. Add this regression test:

```python
def test_reciprocal_runtime_forbids_all_zero_uv_fallback() -> None:
    source = (
        _REPO_ROOT / "scripts/blender/apply_reciprocal_route_modules.py"
    ).read_text(encoding="utf-8")
    assert "uv = (0.0, 0.0)" not in source
    assert 'name="nv_uv0"' in source
    assert 'name="nv_surface_color"' in source
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_module_runtime.py -q
```

Expected: the new tests fail on the current `(0, 0)` UV implementation.

- [ ] **Step 3: Implement the reciprocal contract independently**

Repeat the complete `UV_POLICIES`, `_material_contract`, polygon projection,
polygon-area rejection, `_ensure_white_surface_color`, named `nv_uv0` tangent
generation, and object-property implementation from Task 1 inside
`apply_reciprocal_route_modules.py`. Do not import the environment script: the
reciprocal runtime script SHA must remain a complete executable dependency.

The final call order must be:

```python
policy, tile_m, color_input = _material_contract(material)
_assign_projected_uvs(obj, policy=policy, tile_m=tile_m)
_ensure_white_surface_color(obj, color_input)
try:
    obj.data.calc_tangents(uvmap="nv_uv0")
except Exception as exc:
    raise RuntimeBuildError(
        f"reciprocal tangent generation failed: {obj.name}",
    ) from exc
obj["nv_uv_layer"] = "nv_uv0"
obj["nv_tangents"] = True
obj["nv_material_contract"] = "textured-pbr-v1"
```

- [ ] **Step 4: Extend exact-218 build report verification**

Add reciprocal equivalents of the three required mesh counts and the two
literal-true validation fields. Require every one of the 43 reciprocal meshes
to have non-degenerate UVs and the required white surface colour after the
runtime builds the scene.

- [ ] **Step 5: Prove source parity without a runtime import**

Extract each runtime's contract function source with `inspect.getsource`,
normalise the environment/reciprocal error-label prefix, and assert the
projection and colour algorithms are byte-identical. The test must compare the
function bodies; it must not make one runtime import the other.

- [ ] **Step 6: Run focused tests and Ruff**

```powershell
python -m pytest tests/test_synthetic_village_environment_module_runtime.py tests/test_synthetic_village_reciprocal_route_module_runtime.py -q
python -m ruff check scripts/blender/apply_environment_modules.py scripts/blender/apply_reciprocal_route_modules.py pipeline/synthetic_village/environment_module_runtime.py pipeline/synthetic_village/reciprocal_route_module_runtime.py tests/test_synthetic_village_environment_module_runtime.py tests/test_synthetic_village_reciprocal_route_module_runtime.py
```

Expected: all pass.

- [ ] **Step 7: Commit only Task 2 paths**

```powershell
git add -- scripts/blender/apply_reciprocal_route_modules.py pipeline/synthetic_village/reciprocal_route_module_runtime.py tests/test_synthetic_village_reciprocal_route_module_runtime.py
git commit -m "fix(scene): enforce reciprocal material contract" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- scripts/blender/apply_reciprocal_route_modules.py pipeline/synthetic_village/reciprocal_route_module_runtime.py tests/test_synthetic_village_reciprocal_route_module_runtime.py
```

### Task 3: Open 12-spoke/12-paddle waterwheel

**Files:**
- Modify: `tests/test_synthetic_village_environment_module_runtime.py`
- Modify: `scripts/blender/apply_environment_modules.py`

- [ ] **Step 1: Write failing wheel-structure tests**

Add tests that retain the existing anchor-translation assertion and require
deterministic component evidence:

```python
def test_waterwheel_is_open_twelve_spoke_twelve_paddle_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_blender_runtime(monkeypatch)
    wheel = runtime._bridge_geometry(
        "waterwheel-wheel-001",
        {"waterwheel_assembly_anchor_m": [-185.2, -115.0, 43.15]},
    )
    assert wheel.component_counts == {
        "annular_rim": 1,
        "hub": 1,
        "spoke": 12,
        "paddle": 12,
    }
    assert all(all(math.isfinite(v) for v in vertex) for vertex in wheel.vertices)
    assert all(len(set(face)) >= 3 for face in wheel.faces)
```

Add a geometric hole assertion over rim-labelled vertices: inner radius must be
`>= 2.50 m`, outer radius must be `<= 3.20 m`; spoke-labelled geometry may enter
the rim hole but no old axis-aligned eight-box disc helper may remain.

- [ ] **Step 2: Run the test and verify RED**

```powershell
python -m pytest tests/test_synthetic_village_environment_module_runtime.py::test_waterwheel_is_open_twelve_spoke_twelve_paddle_assembly -q
```

Expected: failure because the current mesh has 8 axis-aligned proxy spokes and
no component evidence.

- [ ] **Step 3: Add labelled mesh components and oriented XZ prisms**

Extend `MeshAssembler` with component counts and vertex ranges:

```python
def mark_component(self, label, start_vertex):
    self.component_counts[label] = self.component_counts.get(label, 0) + 1
    self.component_vertex_ranges.append((label, start_vertex, len(self.vertices)))
```

Add `add_annulus_xz(center, inner_radius, outer_radius, depth, segments=32)`
that emits front/back annular faces plus inner/outer walls. Add
`add_oriented_prism_xz(center, radial, tangential, depth, angle)` by rotating
the four XZ rectangle corners and duplicating them at `y ± depth/2`.

Replace the wheel branch with:

```python
mesh.add_annulus_xz(anchor, 2.55, 3.15, 0.36, segments=32,
                    component="annular_rim")
mesh.add_cylinder(anchor, 0.42, 0.52, 24, axis="y", component="hub")
for index in range(12):
    angle = index * math.tau / 12.0
    mesh.add_oriented_prism_xz(
        center=(anchor_x + 1.48 * math.cos(angle), anchor_y,
                anchor_z + 1.48 * math.sin(angle)),
        radial=2.20, tangential=0.18, depth=0.24, angle=angle,
        component="spoke",
    )
    mesh.add_oriented_prism_xz(
        center=(anchor_x + 3.22 * math.cos(angle), anchor_y,
                anchor_z + 3.22 * math.sin(angle)),
        radial=0.48, tangential=0.82, depth=1.12, angle=angle,
        component="paddle",
    )
```

Keep all vertices anchor-relative and preserve root/instance/material identity.

- [ ] **Step 4: Run environment runtime tests and Ruff**

```powershell
python -m pytest tests/test_synthetic_village_environment_module_runtime.py -q
python -m ruff check scripts/blender/apply_environment_modules.py tests/test_synthetic_village_environment_module_runtime.py
```

Expected: all pass, including the original translation test.

- [ ] **Step 5: Commit only the wheel paths**

```powershell
git add -- scripts/blender/apply_environment_modules.py tests/test_synthetic_village_environment_module_runtime.py
git commit -m "feat(scene): model open waterwheel assembly" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- scripts/blender/apply_environment_modules.py tests/test_synthetic_village_environment_module_runtime.py
```

### Task 4: Content-addressed local orbit plan

**Files:**
- Create: `pipeline/synthetic_village/local_orbit_audit.py`
- Create: `tests/test_synthetic_village_local_orbit_audit.py`

- [ ] **Step 1: Write failing schema/builder tests**

Create tests that require:

```python
plan = build_waterwheel_local_orbit_plan(
    source_plan=build_production_camera_plan(scene, topology),
    environment_module_plan_sha256="a" * 64,
    exact_build_id="b" * 64,
    exact_blend_sha256="c" * 64,
    anchor_m=(-185.2, -115.0, 43.15),
)
assert tuple(row.orbit_camera_id for row in plan.cameras) == (
    "audit-waterwheel-az000", "audit-waterwheel-az045",
    "audit-waterwheel-az090", "audit-waterwheel-az135",
    "audit-waterwheel-az180", "audit-waterwheel-az225",
    "audit-waterwheel-az270", "audit-waterwheel-az315",
)
assert tuple(row.azimuth_deg for row in plan.cameras) == tuple(range(0, 360, 45))
assert all(row.radius_m == 12.0 for row in plan.cameras)
assert all(row.position_m[2] == pytest.approx(44.75) for row in plan.cameras)
assert canonical_local_orbit_plan_bytes(plan).endswith(b"\n")
assert local_orbit_plan_sha256(plan) == hashlib.sha256(
    canonical_local_orbit_plan_bytes(plan),
).hexdigest()
```

Also test missing/duplicate azimuth, changed source plan SHA, non-finite anchor,
wrong build/blend SHA, and any attempt to declare the plan as measured,
training, metric or canonical-production coverage.

- [ ] **Step 2: Run the new test and verify RED**

```powershell
python -m pytest tests/test_synthetic_village_local_orbit_audit.py -q
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement strict schemas and builder**

Implement frozen, strict, `extra="forbid"` models:

```python
class LocalOrbitCamera(FrozenModel):
    orbit_camera_id: str = Field(pattern=r"^audit-waterwheel-az(?:000|045|090|135|180|225|270|315)$")
    materialized_camera_id: str = Field(pattern=r"^camera-audit-overview-00[1-8]$")
    azimuth_deg: int = Field(ge=0, lt=360, multiple_of=45)
    radius_m: Literal[12.0] = 12.0
    position_m: FiniteVector3
    look_at_m: FiniteVector3
    fov_x_deg: Literal[65.0] = 65.0
    audit_only: Literal[True] = True


class LocalOrbitAuditPlan(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.local-orbit-audit-plan.v1"]
    source_production_plan_sha256: Sha256
    environment_module_plan_sha256: Sha256
    exact_build_id: Sha256
    exact_blend_sha256: Sha256
    anchor_m: FiniteVector3
    cameras: tuple[LocalOrbitCamera, ...] = Field(min_length=8, max_length=8)
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_usability: Literal["preview-only"] = "preview-only"
    training_use: Literal["forbidden-as-multiview"] = "forbidden-as-multiview"
    trust_effect: Literal["none-quality-filter-only"] = "none-quality-filter-only"
```

The model validator must require the exact ordered azimuth tuple, unique camera
and materialized IDs, exact radius/FOV, and positions re-derived from
`anchor + (12*cos(a), 12*sin(a), 1.6)`. Look targets are
`(anchor_x, anchor_y, anchor_z + 0.4)`.

- [ ] **Step 4: Implement derived audit production plan materialization**

Create `materialize_local_orbit_render_plan(source_plan, orbit_plan)` that
replaces all twelve source `audit-overview` poses: the first eight are required
orbit views; four support poses use azimuths `22.5, 112.5, 202.5, 292.5`, radius
18 m and `anchor_z + 4.0`. Preserve the canonical camera IDs and dense sequence
indices, set audit disclosure to local modeled-scene inspection, and change only
the audit-overview group expectation to `mixed`. Validate the resulting
`ProductionCameraPlan`; never modify `source_plan`.

- [ ] **Step 5: Run tests and Ruff**

```powershell
python -m pytest tests/test_synthetic_village_local_orbit_audit.py tests/test_synthetic_village_production_profile.py -q
python -m ruff check pipeline/synthetic_village/local_orbit_audit.py tests/test_synthetic_village_local_orbit_audit.py
```

Expected: all pass and the canonical source plan digest remains byte-identical.

- [ ] **Step 6: Commit the orbit plan**

```powershell
git add -- pipeline/synthetic_village/local_orbit_audit.py tests/test_synthetic_village_local_orbit_audit.py
git commit -m "feat(scene): add bound waterwheel orbit plan" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/synthetic_village/local_orbit_audit.py tests/test_synthetic_village_local_orbit_audit.py
```

### Task 5: Exact-218 local orbit caller and machine report

**Files:**
- Create: `pipeline/synthetic_village/local_orbit_runner.py`
- Create: `tests/test_synthetic_village_local_orbit_runner.py`
- Modify: `scripts/synthetic_village.py`

- [ ] **Step 1: Write failing caller tests with fake process runner**

Require the caller to reject a plan whose build/blend/environment SHA differs
from the verified exact-218 build, select exactly the first eight materialized
audit IDs, invoke preflight before render, and stop without publishing on any
failed layer or post-render decision.

The accepted fake run must emit:

```python
assert report.azimuth_bins_passed == 8
assert report.accepted_frame_count == 8
assert report.assembly_visible_frame_count == 8
assert report.wheel_visible_frame_count >= 6
assert report.required_instance_ids == (155, 156, 157, 158, 159, 160)
assert report.training_use == "forbidden-as-multiview"
assert report.trust_effect == "none-quality-filter-only"
```

Tamper tests must cover frame report SHA, instance-mask SHA, wrong build ID,
missing azimuth, duplicate render ID, unregistered instance value, and a wheel
visibility count of five.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest tests/test_synthetic_village_local_orbit_runner.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement the exact-218 adapter**

Reuse the verified reciprocal build loader and the same policy classes used by
`run_reciprocal_production_camera`. Materialize the derived plan from Task 4,
then call a factored internal exact-build frame runner for each of the eight
selected audit IDs. The factor must take explicit:

```python
def _run_exact_build_frame(
    *, verified_build, plan, source_plan, camera_id,
    required_visible_instance_ids, blender_executable, output_root,
    clearance_policy, quality_policy, post_render_policy,
    process_runner, timeout_seconds,
): ...
```

Move no trust logic out of the existing runner: both callers must use the same
preflight request, immutable snapshots, six-layer request/report verification,
local valid-pixel gate and post-render v2 decision. Existing reciprocal role
journal semantics remain unchanged; local orbit writes its own report schema.

- [ ] **Step 4: Decode instance masks and derive visibility**

For every accepted frame, verify the frame report and bound mask SHA, then load
the uint16 grayscale PNG with Pillow/NumPy:

```python
pixels = np.asarray(Image.open(instance_path), dtype=np.uint16)
counts = {int(value): int((pixels == value).sum()) for value in np.unique(pixels)}
assembly_visible = sum(counts.get(value, 0) for value in range(155, 161)) > 0
wheel_visible = counts.get(155, 0) > 0
```

Require assembly visibility in all eight frames and wheel visibility in at
least six. Bind every frame report SHA, render ID, RGB/layer SHA and count map in
canonical `LocalOrbitAuditReport` bytes.

- [ ] **Step 5: Add private CLI commands**

Add `build-local-orbit-plan` and `audit-local-orbit` subcommands to
`scripts/synthetic_village.py`. All file arguments are required; no default
exact build, policy or output directory may silently select stale evidence.

- [ ] **Step 6: Run focused and regression tests**

```powershell
python -m pytest tests/test_synthetic_village_local_orbit_audit.py tests/test_synthetic_village_local_orbit_runner.py tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_batch.py -q
python -m ruff check pipeline/synthetic_village/local_orbit_audit.py pipeline/synthetic_village/local_orbit_runner.py scripts/synthetic_village.py tests/test_synthetic_village_local_orbit_audit.py tests/test_synthetic_village_local_orbit_runner.py
```

Expected: all pass.

- [ ] **Step 7: Commit the caller**

```powershell
git add -- pipeline/synthetic_village/local_orbit_runner.py tests/test_synthetic_village_local_orbit_runner.py scripts/synthetic_village.py
git commit -m "feat(scene): run exact218 local orbit audit" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/synthetic_village/local_orbit_runner.py tests/test_synthetic_village_local_orbit_runner.py scripts/synthetic_village.py
```

### Task 6: Generate and publish twelve replaceable image2 references

**Files:**
- Private create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch22/*.png`
- Private create: prompts, queue, manifest and visual review sheet
- Release create: `synthetic-village-design-inputs-batch22-2026-07-23`
- Modify: `README.md`

- [ ] **Step 1: Generate the eight context views**

Use image2 with eight independent prompts. Every prompt must describe the same
generic mountainous East-Asian rural watermill vocabulary but explicitly say
“independent design reference, no claim of calibrated shared geometry.” Cover
front, front-right, right, rear-right, rear, rear-left, left and front-left;
include millhouse, open wheel, flume, axle, service deck, stair, guard,
tailrace, creek bank, forest, crops and distant village context.

- [ ] **Step 2: Generate four detail/material views**

Generate upstream flume/axle, underside bracket/tailrace, seamless weathered
timber albedo study, and seamless aged-metal albedo study. Keep lighting neutral
and avoid baked text, logos, people as scale evidence or camera metadata.

- [ ] **Step 3: Visually inspect every image**

Open each PNG at original detail. Reject duplicates, malformed wheel topology,
text/watermarks, implausible support, cropped critical parts, overly close
composition or a reference too specific to one exact coordinate. Failed images
remain outside manifest and Release.

- [ ] **Step 4: Write canonical private manifest**

For every accepted file record byte SHA-256, size, prompt path and:

```json
{
  "camera_calibration": "unknown",
  "geometry_consistency": "not-verified",
  "training_use": "forbidden-as-multiview",
  "trust_effect": "none",
  "replaceable": true,
  "design_scope": "generic-watermill-component-and-surface-reference"
}
```

- [ ] **Step 5: Build a clean Release archive**

The ZIP contains exactly 12 PNG, 12 prompt text files, `manifest.json`,
`CHECKSUMS.sha256` and `USAGE.md`. Exclude queue state, contact sheets,
downloads, browser metadata and failed attempts. Verify the archive by extracting
to a new temporary directory and recomputing every checksum.

- [ ] **Step 6: Publish Release and update README**

Create tag `synthetic-village-design-inputs-batch22-2026-07-23`, upload only the
clean ZIP, and document how to download, verify, unpack and replace these inputs.
State that image2 references guide the synthetic model but are forbidden as
SfM/NeRF/3DGS multiview evidence.

- [ ] **Step 7: Commit only documentation**

```powershell
git add -- README.md
git commit -m "docs(image2): publish batch22 watermill references" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- README.md
```

### Task 7: Fresh Blender builds, Phase 4.3, six-role and orbit acceptance

**Files:**
- Private create: fresh 175-root/exact-218 build directories and reports
- Private create: Phase 4.3, six-role and local-orbit artifacts
- Create: `handoff/FEEDBACK-HANDOFF-CODEX-027-batch22-material-local-360.md`

- [ ] **Step 1: Run the complete pre-build test gate**

```powershell
python -m pytest tests/test_synthetic_village_environment_module_runtime.py tests/test_synthetic_village_reciprocal_route_module_runtime.py tests/test_synthetic_village_local_orbit_audit.py tests/test_synthetic_village_local_orbit_runner.py tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_batch.py -q
python -m ruff check scripts/blender/apply_environment_modules.py scripts/blender/apply_reciprocal_route_modules.py pipeline/synthetic_village/environment_module_runtime.py pipeline/synthetic_village/reciprocal_route_module_runtime.py pipeline/synthetic_village/local_orbit_audit.py pipeline/synthetic_village/local_orbit_runner.py scripts/synthetic_village.py tests/test_synthetic_village_environment_module_runtime.py tests/test_synthetic_village_reciprocal_route_module_runtime.py tests/test_synthetic_village_local_orbit_audit.py tests/test_synthetic_village_local_orbit_runner.py
```

Expected: all pass.

- [ ] **Step 2: Build a fresh 175-root environment artifact**

Use the current production scene/topology/environment plan and current Blender
runtime. Verify exact `130 + 45 = 175` roots, all 45 module meshes, material
contract counts `45/45/45`, runtime script SHA, report SHA, blend SHA and build
ID. Reject any reuse of the Batch 21 blend.

- [ ] **Step 3: Build a production-bound fresh exact-218 artifact**

Consume the fresh 175 build and current reciprocal plan bound to the current
canonical 180 plan. Verify exact `175 + 43 = 218` roots, all 43 reciprocal
meshes, contract counts `43/43/43`, object registry `1..218`, runtime/report/
blend SHAs and build ID.

- [ ] **Step 4: Reopen the exact blend and audit material data**

Run a headless Blender probe over every textured mesh. Require valid
`nv_nominal_tile_m`, supported policy, non-degenerate `nv_uv0`, tangents and a
white CORNER/FLOAT_COLOR `nv_surface_color` of loop length. Store the machine
report and its SHA; do not use only pre-save in-memory evidence.

- [ ] **Step 5: Run unchanged Phase 4.3 and six-role caller**

Require route `6/6`, module-pair `15/15`, module/environment `6/6`, topology
`6/6`, then six accepted role frames with no threshold/allowlist change. Record
the watermill RGB and verify instances `155` and `189..195` remain visible.

- [ ] **Step 6: Run the eight-direction local orbit**

Build the orbit plan from the fresh exact build, run fresh preflight and
six-layer/post-render v2 for all eight primary cameras, and require `8/8`
accepted, assembly `8/8`, wheel `>=6/8`. Save the eight RGBs, bound layers,
contact sheet and canonical audit report SHA.

- [ ] **Step 7: Perform visual comparison**

Compare Batch 21 accepted RGB, the temporary white-colour hypothesis probe,
fresh Batch 22 watermill RGB and all eight orbit views. Confirm visible texture
frequency, open rim, hub, spokes, paddles, axle/flume relationship and absence
of black material multiplication. Record remaining simplification honestly.

- [ ] **Step 8: Write final handoff and run regression**

Document every machine identity, failed intermediate attempt, accepted counts,
Release URL/SHA, visual limitations and the distinction between finite exact-218
content, render-on-demand synthetic expansion and external real reconstruction.

Run:

```powershell
python -m pytest -q
python -m ruff check .
git diff --check
```

Expected: full suite and Ruff pass; diff check clean.

- [ ] **Step 9: Commit final evidence and push when GLM P0 is cleared**

```powershell
git add -- handoff/FEEDBACK-HANDOFF-CODEX-027-batch22-material-local-360.md
git commit -m "docs(scene): record batch22 material orbit evidence" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- handoff/FEEDBACK-HANDOFF-CODEX-027-batch22-material-local-360.md
git push origin main
```

Before pushing, verify `main` contains no unresolved GLM trust-contract commit
that contradicts `REVIEW-CODEX-022`; coordinate correction instead of silently
publishing it with Batch 22.
