# Source-Consistent Multiscale Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved source-consistent multiscale surface profile so the
same Blender scene and Viewer GLB carry source-bound macro colour, denser
terrain/path geometry, and deterministic path details that remain readable at
a 1.6 m pedestrian camera.

**Architecture:** A host-side contract derives immutable macro palettes and a
deterministic path-detail plan from the verified material bundle and scene plan.
A standard-library runtime shared with Blender samples those inputs in world
coordinates. Blender authors geometry and float `COLOR_0`; an independent GLB
parser verifies the actual accessors, values, material closure, and budgets
before a private preview is published.

**Tech Stack:** Python 3.11+, Pydantic v2, Pillow, NumPy, Blender 4.5.11 Python
API, glTF/GLB 2.0, Three.js `GLTFLoader`, pytest, Node test runner, Ruff,
SHA-256 canonical JSON.

## Global Constraints

- Work on the single shared `main` branch; do not create a branch or worktree.
- Stage only explicit files. Never use `git add -A`, `git add .`, or
  `git commit -a`.
- Preserve the collaborator-owned
  `tests/test_synthetic_village_weather.py` modification.
- Every commit ends with
  `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Push each green, reviewable task to `origin/main` before starting the next
  task.
- Keep `synthetic=true`,
  `material_fidelity=synthetic-derived-pbr`,
  `real_photo_textures=false`, and `geometry_usability=preview-only`.
- Never infer trust from filenames, engine names, Blender material names, or
  presence of a `COLOR_0` accessor.
- The new profile ID is
  `source-consistent-multiscale-surface-v1`; historical artifacts remain
  `single-scale-derived-pbr-v0`.
- Macro multipliers are source-derived, quantized at `1/4096`, and bounded to
  `[0.88, 1.10]`.
- Terrain spacing is `4 m`; path longitudinal spacing is at most `1 m`; each
  path has six lateral rails.
- The new finite-preview caps are `125,000` indexed triangles, `580`
  primitives, `160,000,000` GLB bytes, exactly 24 materials, and exactly 18
  new detail mesh objects.
- Every renderable v1 primitive carries float component type `5126`, value type
  `VEC4` `COLOR_0`, plus the existing `TEXCOORD_0` and material. Every
  normal-mapped primitive retains `TANGENT`.
- Do not add a Viewer-only surface shader or unrelated macro image.
- Private Blender builds, screenshots, audit samples, and generated material
  bytes stay below `.nantai-studio/`.
- The finite profile does not prove arbitrary-coordinate textured chunks or
  real 3DGS completion.

---

## File Structure

### New files

- `scripts/blender/surface_realism_runtime.py` — pure standard-library hashing,
  smooth interpolation, fixed-point conversion, and macro-colour sampling
  shared by host Python and Blender.
- `pipeline/synthetic_village/surface_realism.py` — strict Pydantic profile,
  palette, path-detail, build-evidence, and canonical-byte contracts.
- `pipeline/synthetic_village/surface_quality.py` — registered path samples,
  repetition/gradient/source-fidelity metrics, and fail-closed quality report.
- `tests/test_synthetic_village_surface_realism.py` — deterministic profile,
  negative-coordinate, palette, cap, and compatibility tests.
- `tests/test_synthetic_village_surface_quality.py` — synthetic metric fixtures
  and acceptance-threshold tests.
- `docs/verification/2026-07-19-multiscale-surface-realism.md` — measured local
  build, GLB audit, browser evidence, limitations, and next boundary.

### Existing files changed

- `pipeline/synthetic_village/canary.py` — request/report fields, canonical
  compatibility, active input snapshots, and formal-build verification.
- `pipeline/synthetic_village/local_textured_preview.py` — local v1 request,
  report/audit verification, immutable publication, and training-build binding.
- `scripts/blender/build_synthetic_village.py` — profile validation, 4 m
  terrain, six-rail paths, details, vertex-colour material binding, build
  evidence, and v1 budgets.
- `pipeline/synthetic_village/glb_material_audit.py` — float `COLOR_0` decoding,
  node/mesh surface-mode verification, unique-colour evidence, and combined
  budgets.
- `tests/test_synthetic_village_canary.py` — authoritative request/report and
  historical canonical-byte coverage.
- `tests/test_local_textured_preview.py` — local request/report/publication
  coverage.
- `tests/test_synthetic_village_blender_runtime.py` — real Blender profile
  evidence.
- `tests/test_glb_material_audit.py` — handcrafted positive and adversarial
  float-colour GLBs.
- `web/viewer/model-preview.test.mjs` — unchanged truth disclosure and new GLB
  compatibility assertions.
- `handoff/FEEDBACK-CODEX-009-multiscale-surface-realism.md` — five-part
  What/Why/Tradeoff/Open/Next handoff for Opus.

---

### Task 1: Deterministic surface plan and shared runtime

**Files:**

- Create: `scripts/blender/surface_realism_runtime.py`
- Create: `pipeline/synthetic_village/surface_realism.py`
- Create: `tests/test_synthetic_village_surface_realism.py`

**Interfaces:**

- Consumes:
  `ScenePlan`,
  `DerivedMaterialBundle`,
  verified base-colour PNG bytes, and scene seed `20260715`.
- Produces:
  `SurfaceRealismPlan`,
  `SurfaceMacroPalette`,
  `PathSurfacePlan`,
  `PathDetailRecord`,
  `PathRutRun`,
  `build_surface_realism_plan(scene_plan, material_bundle_root)`,
  `canonical_surface_realism_plan_bytes(plan)`, and
  `sample_macro_color(palette_q, x_m, y_m, period_m, scene_seed,
  source_sha256)`.

- [ ] **Step 1: Write failing shared-runtime and host-contract tests**

```python
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from pipeline.synthetic_village.scene_plan import build_scene_plan
from pipeline.synthetic_village.surface_realism import (
    ACTIVE_MACRO_SLOTS,
    SURFACE_PROFILE_V1,
    build_surface_realism_plan,
    canonical_surface_realism_plan_bytes,
)
from scripts.blender.surface_realism_runtime import sample_macro_color
from tests.synthetic_material_fixtures import publish_material_fixture


def test_macro_sampler_is_stable_across_negative_lattice_boundaries() -> None:
    palette = tuple(
        (3604 + index % 320, 3650 + index % 280, 3700 + index % 240)
        for index in range(256)
    )
    first = sample_macro_color(
        palette,
        x_m=-20.0001,
        y_m=-0.0001,
        period_m=20.0,
        scene_seed=20260715,
        source_sha256="a" * 64,
    )
    second = sample_macro_color(
        palette,
        x_m=-20.0001,
        y_m=-0.0001,
        period_m=20.0,
        scene_seed=20260715,
        source_sha256="a" * 64,
    )
    assert first == second
    assert all(0.88 <= value <= 1.10 for value in first)
    assert first != sample_macro_color(
        palette,
        x_m=20.0001,
        y_m=-0.0001,
        period_m=20.0,
        scene_seed=20260715,
        source_sha256="a" * 64,
    )


def test_surface_plan_is_complete_content_addressed_and_path_free(
    tmp_path: Path,
) -> None:
    _visual_root, bundle = publish_material_fixture(tmp_path)
    plan = build_surface_realism_plan(
        build_scene_plan(),
        bundle.final_directory,
    )
    assert plan.profile_id == SURFACE_PROFILE_V1
    assert tuple(row.slot_id for row in plan.macro_palettes) == ACTIVE_MACRO_SLOTS
    assert len(plan.path_plans) == 6
    assert all(row.lateral_rail_count == 6 for row in plan.path_plans)
    assert all(row.longitudinal_step_m == 1.0 for row in plan.path_plans)
    assert all({detail.detail_class for detail in row.details} == {
        "damp-patch",
        "leaf-card",
        "stone-fragment",
    } for row in plan.path_plans)
    assert all(row.rut_runs for row in plan.path_plans)
    raw = canonical_surface_realism_plan_bytes(plan)
    assert hashlib.sha256(raw).hexdigest() == plan.plan_sha256
    assert str(tmp_path).encode() not in raw
    assert str(Path.home()).encode() not in raw
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_realism.py -q
```

Expected: collection fails because
`pipeline.synthetic_village.surface_realism` and
`scripts.blender.surface_realism_runtime` do not exist.

- [ ] **Step 3: Implement the standard-library sampler**

Create `scripts/blender/surface_realism_runtime.py` with these public
functions and exact fixed-point rules:

```python
"""Pure deterministic runtime shared by host Python and Blender."""

from __future__ import annotations

import hashlib
import math

PROFILE_ID = "source-consistent-multiscale-surface-v1"
FIXED_DENOMINATOR = 4096
MIN_MULTIPLIER_Q = round(0.88 * FIXED_DENOMINATOR)
MAX_MULTIPLIER_Q = round(1.10 * FIXED_DENOMINATOR)


def _digest(*parts: object) -> bytes:
    payload = "\0".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).digest()


def _palette_index(
    lattice_x: int,
    lattice_y: int,
    *,
    scene_seed: int,
    source_sha256: str,
) -> int:
    return int.from_bytes(
        _digest(PROFILE_ID, source_sha256, scene_seed, lattice_x, lattice_y)[:2],
        "big",
    ) % 256


def _smoothstep(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def _quantize(value: float) -> float:
    bounded = min(1.10, max(0.88, value))
    return round(bounded * FIXED_DENOMINATOR) / FIXED_DENOMINATOR


def sample_macro_color(
    palette_q: tuple[tuple[int, int, int], ...],
    *,
    x_m: float,
    y_m: float,
    period_m: float,
    scene_seed: int,
    source_sha256: str,
) -> tuple[float, float, float, float]:
    if len(palette_q) != 256 or period_m <= 0:
        raise ValueError("surface macro sampler inputs are invalid")
    lattice_x = math.floor(x_m / period_m)
    lattice_y = math.floor(y_m / period_m)
    u = _smoothstep(x_m / period_m - lattice_x)
    v = _smoothstep(y_m / period_m - lattice_y)
    rows = []
    for dy in (0, 1):
        for dx in (0, 1):
            rows.append(
                palette_q[
                    _palette_index(
                        lattice_x + dx,
                        lattice_y + dy,
                        scene_seed=scene_seed,
                        source_sha256=source_sha256,
                    )
                ],
            )
    channels = []
    for channel in range(3):
        low = rows[0][channel] * (1.0 - u) + rows[1][channel] * u
        high = rows[2][channel] * (1.0 - u) + rows[3][channel] * u
        channels.append(_quantize((low * (1.0 - v) + high * v) / 4096.0))
    return channels[0], channels[1], channels[2], 1.0
```

- [ ] **Step 4: Implement strict host models and palette/detail derivation**

Create `pipeline/synthetic_village/surface_realism.py`. The implementation
must define these constants and models without storing filesystem paths:

```python
LEGACY_SURFACE_PROFILE_ID = "single-scale-derived-pbr-v0"
SURFACE_PROFILE_V1 = "source-consistent-multiscale-surface-v1"
SURFACE_ALGORITHM_V1 = "source-palette-world-macro-path-detail-v1"
ACTIVE_MACRO_SLOTS = (
    "material-moss-stone-01",
    "material-packed-earth-01",
    "material-terrace-soil-01",
    "material-wet-stone-paving-01",
)
TERRAIN_SPACING_M = 4.0
PATH_STEP_M = 1.0
PATH_LATERAL_RAILS = 6
MAX_DETAIL_COUNTS = {
    "stone-fragment": 128,
    "leaf-card": 384,
    "damp-patch": 72,
    "rut-run": 96,
}

MultiplierQ = Annotated[int, Field(ge=3604, le=4506)]


class SurfaceMacroPalette(FrozenModel):
    slot_id: Literal[
        "material-moss-stone-01",
        "material-packed-earth-01",
        "material-terrace-soil-01",
        "material-wet-stone-paving-01",
    ]
    source_sha256: Sha256
    quantization_denominator: Literal[4096] = 4096
    multipliers_q: tuple[
        tuple[MultiplierQ, MultiplierQ, MultiplierQ],
        ...,
    ] = Field(
        min_length=256,
        max_length=256,
    )
    palette_sha256: Sha256

    @model_validator(mode="after")
    def validate_palette_digest(self) -> Self:
        actual = hashlib.sha256(
            _canonical_json_bytes(self.multipliers_q),
        ).hexdigest()
        if actual != self.palette_sha256:
            raise ValueError("surface macro palette digest does not match")
        return self


class PathDetailRecord(FrozenModel):
    detail_id: str = Field(pattern=r"^path-network-\d{3}:(?:stone|leaf|damp):\d{3}$")
    detail_class: Literal["stone-fragment", "leaf-card", "damp-patch"]
    arc_length_m: float = Field(ge=0, allow_inf_nan=False)
    side_fraction: float = Field(ge=-1, le=1, allow_inf_nan=False)
    scale: float = Field(gt=0, le=2, allow_inf_nan=False)
    yaw_deg: float = Field(ge=0, lt=360, allow_inf_nan=False)


class PathRutRun(FrozenModel):
    rut_id: str = Field(pattern=r"^path-network-\d{3}:rut:\d{3}$")
    start_arc_length_m: float = Field(ge=0, allow_inf_nan=False)
    length_m: float = Field(ge=6, le=18, allow_inf_nan=False)
    depth_m: float = Field(ge=0.015, le=0.035, allow_inf_nan=False)


class PathSurfacePlan(FrozenModel):
    object_id: str = Field(pattern=r"^path-network-\d{3}$")
    longitudinal_step_m: Literal[1.0] = 1.0
    lateral_rail_count: Literal[6] = 6
    details: tuple[PathDetailRecord, ...]
    rut_runs: tuple[PathRutRun, ...]


class SurfaceRealismPlan(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.surface-realism-plan.v1"
    ] = "nantai.synthetic-village.surface-realism-plan.v1"
    plan_sha256: Sha256
    profile_id: Literal["source-consistent-multiscale-surface-v1"]
    algorithm_id: Literal["source-palette-world-macro-path-detail-v1"]
    scene_seed: Literal[20260715]
    runtime_module_sha256: Sha256
    terrain_spacing_m: Literal[4.0] = 4.0
    terrain_period_m: Literal[20.0] = 20.0
    ground_period_m: Literal[10.0] = 10.0
    macro_palettes: tuple[SurfaceMacroPalette, ...] = Field(
        min_length=4,
        max_length=4,
    )
    path_plans: tuple[PathSurfacePlan, ...] = Field(
        min_length=6,
        max_length=6,
    )
```

Use the verified base-colour descriptor from each active bundle record, decode
with Pillow, resize to `16 × 16` using `Image.Resampling.BOX`, convert sRGB to
linear RGB, divide by the per-channel mean, clamp to `[0.88, 1.10]`, and store
`round(multiplier * 4096)`. Hash the sorted canonical integer palette.

Generate candidate positions by stable arc-length cells. Use SHA-256 bytes to
select acceptance, side, scale, yaw, rut length, and rut depth. Sort records by
ID, enforce the global caps, and select the lowest digest candidate when a path
would otherwise lack one class.

- [ ] **Step 5: Run focused tests and deterministic subprocess probe**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_realism.py -q
```

Then run the same plan build in two fresh Python processes and compare:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_realism.py \
  -k process -q
```

Expected: all focused tests pass; repeated process output hashes are identical.

- [ ] **Step 6: Commit and push the pure contract**

```bash
git add \
  scripts/blender/surface_realism_runtime.py \
  pipeline/synthetic_village/surface_realism.py \
  tests/test_synthetic_village_surface_realism.py
git commit -m "feat(surface): add deterministic realism plan" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  scripts/blender/surface_realism_runtime.py \
  pipeline/synthetic_village/surface_realism.py \
  tests/test_synthetic_village_surface_realism.py
git push origin main
```

---

### Task 2: Request, report, and immutable identity integration

**Files:**

- Modify: `pipeline/synthetic_village/canary.py`
- Modify: `pipeline/synthetic_village/local_textured_preview.py`
- Modify: `tests/test_synthetic_village_canary.py`
- Modify: `tests/test_local_textured_preview.py`

**Interfaces:**

- Consumes:
  `SurfaceRealismPlan`,
  `LEGACY_SURFACE_PROFILE_ID`,
  `SURFACE_PROFILE_V1`.
- Produces:
  request fields `surface_realism_profile_id` and `surface_realism_plan`;
  report field `surface_realism`; `SurfaceRealismBuildEvidence`; legacy
  canonical-byte omission; active runtime-module snapshots.

- [ ] **Step 1: Add failing request/report compatibility tests**

Add these assertions to both authoritative and local request suites:

```python
def test_local_request_selects_content_addressed_surface_profile(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)
    assert request.surface_realism_profile_id == SURFACE_PROFILE_V1
    assert request.surface_realism_plan is not None
    assert request.surface_realism_plan.plan_sha256 == hashlib.sha256(
        canonical_surface_realism_plan_bytes(request.surface_realism_plan),
    ).hexdigest()
    raw = canonical_local_textured_preview_request_bytes(request)
    assert b"source-consistent-multiscale-surface-v1" in raw
    assert b".nantai-studio" not in raw


def test_historical_local_request_omits_surface_defaults_from_canonical_bytes(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)
    payload = request.model_dump(mode="json")
    payload.pop("surface_realism_profile_id")
    payload.pop("surface_realism_plan")
    unsigned = dict(payload)
    unsigned.pop("preview_id")
    payload["preview_id"] = hashlib.sha256(
        canary._canonical_json_bytes(unsigned),
    ).hexdigest()
    historical = LocalTexturedPreviewRequest.model_validate(payload)
    raw = canonical_local_textured_preview_request_bytes(historical)
    assert b"surface_realism" not in raw
```

Add report mutation cases that reject:

```python
("surface_realism_profile_id", "single-scale-derived-pbr-v0")
("surface_realism", None)
("surface_realism.plan_sha256", "0" * 64)
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_canary.py \
  tests/test_local_textured_preview.py \
  -k "surface or canonical or local_request" -q
```

Expected: new attributes are absent.

- [ ] **Step 3: Add strict request/report fields**

Add the fields to `TexturedBuildRequest` and
`LocalTexturedPreviewRequest`:

```python
surface_realism_profile_id: SurfaceRealismProfileId = (
    LEGACY_SURFACE_PROFILE_ID
)
surface_realism_plan: SurfaceRealismPlan | None = None
```

Validate:

```python
if self.surface_realism_profile_id == LEGACY_SURFACE_PROFILE_ID:
    if self.surface_realism_plan is not None:
        raise ValueError("legacy surface profile cannot carry a v1 plan")
else:
    if (
        self.surface_realism_plan is None
        or self.surface_realism_plan.profile_id
        != self.surface_realism_profile_id
        or self.surface_realism_plan.scene_seed != self.scene_plan.seed
    ):
        raise ValueError("surface realism plan is absent or scene-mismatched")
```

Define `SurfaceRealismBuildEvidence` in
`pipeline/synthetic_village/surface_realism.py` with:

```python
class SurfaceRealismBuildEvidence(FrozenModel):
    profile_id: Literal["source-consistent-multiscale-surface-v1"]
    plan_sha256: Sha256
    runtime_module_sha256: Sha256
    terrain_resolution: tuple[Literal[176], Literal[126]]
    terrain_triangle_count: Literal[43750]
    path_interval_count: int = Field(ge=1452, le=1464)
    path_triangle_count: int = Field(ge=14520, le=14640)
    detail_counts: dict[
        Literal["damp-patch", "leaf-card", "stone-fragment", "rut-run"],
        int,
    ]
    detail_mesh_object_count: Literal[18]
    color_min: float = Field(ge=0.88, le=1.0)
    color_max: float = Field(ge=1.0, le=1.10)
    colored_primitive_count: int = Field(ge=1)
    white_primitive_count: int = Field(ge=1)
```

Add `surface_realism_profile_id` and `surface_realism` to textured build
reports. v1 reports require evidence matching the request; legacy reports
require no evidence.

- [ ] **Step 4: Preserve historical canonical bytes**

In `canonical_textured_build_request_bytes`,
`canonical_local_textured_preview_request_bytes`, and report equivalents,
remove both surface fields when neither was present in `model_fields_set`:

```python
if "surface_realism_profile_id" not in request.model_fields_set:
    payload.pop("surface_realism_profile_id")
if "surface_realism_plan" not in request.model_fields_set:
    payload.pop("surface_realism_plan")
```

The local request builder must call:

```python
surface_plan = build_surface_realism_plan(active_scene, bundle_root)
```

and include the profile and plan in the content-addressed payload.

- [ ] **Step 5: Snapshot and revalidate the runtime module**

Add
`repo_root / "scripts/blender/surface_realism_runtime.py"` to active v1 input
snapshots in both formal and local builders. The plan already carries its
module SHA. Reject a plan whose hash differs from current bytes before Blender
starts.

- [ ] **Step 6: Run contract tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_realism.py \
  tests/test_synthetic_village_canary.py \
  tests/test_local_textured_preview.py -q
```

Expected: all selected tests pass; old canonical fixtures remain byte-identical.

- [ ] **Step 7: Commit and push identity integration**

```bash
git add \
  pipeline/synthetic_village/canary.py \
  pipeline/synthetic_village/local_textured_preview.py \
  pipeline/synthetic_village/surface_realism.py \
  tests/test_synthetic_village_canary.py \
  tests/test_local_textured_preview.py
git commit -m "feat(surface): bind realism profile identity" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/canary.py \
  pipeline/synthetic_village/local_textured_preview.py \
  pipeline/synthetic_village/surface_realism.py \
  tests/test_synthetic_village_canary.py \
  tests/test_local_textured_preview.py
git push origin main
```

---

### Task 3: Blender macro colour, terrain, and continuous paths

**Files:**

- Modify: `scripts/blender/build_synthetic_village.py`
- Modify: `tests/test_synthetic_village_blender_runtime.py`
- Modify: `tests/test_local_textured_preview.py`

**Interfaces:**

- Consumes:
  request `surface_realism_plan`,
  `sample_macro_color`,
  existing material input registry and scene plan.
- Produces:
  4 m terrain, six-rail 1 m path ribbons, loop-domain `FLOAT_COLOR`,
  material multiplication, and measured terrain/path evidence.

- [ ] **Step 1: Write failing Blender-source and runtime assertions**

Add a source-level test that parses literal assignments and rejects accidental
budget drift:

```python
def test_builder_declares_approved_surface_profile_constants() -> None:
    tree = ast.parse(BLENDER_BUILDER.read_text("utf-8"))
    assert _literal_assignment(tree, "SURFACE_PROFILE_V1") == (
        "source-consistent-multiscale-surface-v1"
    )
    assert _literal_assignment(tree, "SURFACE_TERRAIN_SPACING_M") == 4.0
    assert _literal_assignment(tree, "SURFACE_PATH_STEP_M") == 1.0
    assert _literal_assignment(tree, "SURFACE_PATH_LATERAL_RAILS") == 6
    assert _literal_assignment(tree, "MAX_SURFACE_GLTF_TRIANGLES") == 125_000
    assert _literal_assignment(tree, "MAX_SURFACE_GLB_BYTES") == 160_000_000
```

Extend the real local Blender test:

```python
assert report["surface_realism"]["terrain_resolution"] == [176, 126]
assert report["surface_realism"]["terrain_triangle_count"] == 43_750
assert report["surface_realism"]["path_interval_count"] >= 1_452
assert report["surface_realism"]["path_triangle_count"] >= 14_520
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_blender_runtime.py \
  tests/test_local_textured_preview.py \
  -k surface -q
```

Expected: constant and evidence assertions fail.

- [ ] **Step 3: Validate and load the shared runtime**

At builder startup, resolve
`Path(__file__).with_name("surface_realism_runtime.py")`, verify its SHA-256
against `request["surface_realism_plan"]["runtime_module_sha256"]`, and load it
with `importlib.util.spec_from_file_location`. Reject legacy requests carrying
the module or v1 requests without it.

- [ ] **Step 4: Multiply the source texture by the vertex-colour node**

For each textured material, replace the direct base-colour link with:

```python
vertex_color = nodes.new("ShaderNodeVertexColor")
vertex_color.name = f"nv__surface-color-{slot_id}"
vertex_color.layer_name = "nv_surface_color"
multiply_color = nodes.new("ShaderNodeMixRGB")
multiply_color.name = f"nv__source-times-surface-{slot_id}"
multiply_color.blend_type = "MULTIPLY"
multiply_color.inputs[0].default_value = 1.0
links.new(base.outputs["Color"], multiply_color.inputs[1])
links.new(vertex_color.outputs["Color"], multiply_color.inputs[2])
links.new(multiply_color.outputs["Color"], principled.inputs["Base Color"])
```

Store `surface_realism_profile_id` and macro palette digest in material extras.

- [ ] **Step 5: Build the 4 m terrain**

Change `_create_terrain` to select spacing from the active profile:

```python
spacing = (
    SURFACE_TERRAIN_SPACING_M
    if _surface_profile_v1(request)
    else 5.0
)
columns = int(width / spacing) + 1
rows = int(depth / spacing) + 1
```

Thread `request` into `_create_terrain`, preserve the same height function and
material zoning, and record `[176, 126]` plus `43,750` triangles.

- [ ] **Step 6: Replace independent path quads with a continuous six-rail ribbon**

Add these exact arc-length helpers and
`_surface_path_ribbon(points, width, plan, extent)`:

```python
def _resample_polyline_at_most(points, *, step_m):
    source = [
        Vector((row["x_m"], row["y_m"], row["z_m"]))
        for row in points
    ]
    cumulative = [0.0]
    for first, second in zip(source, source[1:], strict=False):
        cumulative.append(cumulative[-1] + (second - first).length)
    interval_count = max(1, math.ceil(cumulative[-1] / step_m))
    sampled = []
    segment = 0
    for index in range(interval_count + 1):
        distance = cumulative[-1] * index / interval_count
        while segment + 1 < len(cumulative) - 1 and cumulative[segment + 1] < distance:
            segment += 1
        span = cumulative[segment + 1] - cumulative[segment]
        fraction = 0.0 if span == 0.0 else (distance - cumulative[segment]) / span
        position = source[segment].lerp(source[segment + 1], fraction)
        sampled.append((position.x, position.y, position.z, distance))
    return sampled


def _bounded_path_tangent(centerline, index):
    before = Vector(centerline[max(0, index - 1)][:2])
    after = Vector(centerline[min(len(centerline) - 1, index + 1)][:2])
    tangent = after - before
    if tangent.length <= 1e-9:
        raise RuntimeBuildError("surface path contains a zero-length tangent")
    return tangent.normalized()


def _rut_depth_at(path_plan, arc_length_m, side_fraction):
    rail_center = 0.58
    rail_width = 0.16
    if abs(abs(side_fraction) - rail_center) > rail_width:
        return 0.0
    return max(
        (
            run["depth_m"]
            for run in path_plan["rut_runs"]
            if run["start_arc_length_m"]
            <= arc_length_m
            <= run["start_arc_length_m"] + run["length_m"]
        ),
        default=0.0,
    )


def _surface_path_ribbon(points, width, path_plan, extent):
    centerline = _resample_polyline_at_most(
        points,
        step_m=path_plan["longitudinal_step_m"],
    )
    assembler = MeshAssembler()
    rails = path_plan["lateral_rail_count"]
    for index, point in enumerate(centerline):
        tangent = _bounded_path_tangent(centerline, index)
        normal = Vector((-tangent.y, tangent.x))
        for rail in range(rails):
            fraction = -1.0 + 2.0 * rail / (rails - 1)
            x_m = point[0] + normal.x * width * 0.5 * fraction
            y_m = point[1] + normal.y * width * 0.5 * fraction
            z_m = _terrain_height(x_m, y_m, extent) + 0.10
            z_m -= _rut_depth_at(path_plan, point[3], fraction)
            assembler.vertices.append((x_m, y_m, z_m))
    for row in range(len(centerline) - 1):
        for rail in range(rails - 1):
            lower = row * rails + rail
            assembler.faces.append(
                (lower, lower + 1, lower + rails + 1, lower + rails),
            )
    return assembler, len(centerline) - 1
```

At joins, replace the direct normal with the normalized sum of the incoming
and outgoing normals. Divide by its dot product with the outgoing normal and
clamp the resulting miter distance to `1.5 × width/2`; endpoints use their
single adjacent segment. Preserve the stable path root, part ID, width, and
material.

- [ ] **Step 7: Author float loop colours after triangulation**

Inside `_apply_textured_uvs_and_tangents`, after triangulation and UV creation:

```python
color_layer = mesh.color_attributes.get("nv_surface_color")
if color_layer is None:
    color_layer = mesh.color_attributes.new(
        name="nv_surface_color",
        type="FLOAT_COLOR",
        domain="CORNER",
    )
mesh.color_attributes.active_color = color_layer
for loop in mesh.loops:
    polygon = mesh.polygons[loop.polygon_index]
    world = obj.matrix_world @ mesh.vertices[loop.vertex_index].co
    color_layer.data[loop.index].color = _surface_color_for_loop(
        request,
        obj,
        polygon,
        world,
    )
```

`_surface_color_for_loop` returns exact white for unprofiled surfaces, uses
20 m sampling for terrain, and 10 m sampling for path/field/courtyard surfaces.
It selects the palette only through the object's verified material slot and
the plan's matching `slot_id`; missing, duplicate, or mismatched palette
records raise `RuntimeBuildError`. The helper calls `sample_macro_color` with
the loop's world `x/y`, plan seed, palette source SHA, and the selected period.
Tag each object with:

```text
nv_surface_realism_profile
nv_surface_color_mode
nv_surface_palette_sha256
```

- [ ] **Step 8: Run unit gates**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_realism.py \
  tests/test_synthetic_village_blender_runtime.py \
  tests/test_local_textured_preview.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/surface_realism.py \
  scripts/blender/build_synthetic_village.py \
  tests/test_synthetic_village_surface_realism.py \
  tests/test_synthetic_village_blender_runtime.py \
  tests/test_local_textured_preview.py
```

Expected: all non-opt-in tests and Ruff pass.

- [ ] **Step 9: Commit and push Blender macro geometry**

```bash
git add \
  scripts/blender/build_synthetic_village.py \
  tests/test_synthetic_village_blender_runtime.py \
  tests/test_local_textured_preview.py
git commit -m "feat(surface): author macro color geometry" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  scripts/blender/build_synthetic_village.py \
  tests/test_synthetic_village_blender_runtime.py \
  tests/test_local_textured_preview.py
git push origin main
```

---

### Task 4: Deterministic path detail geometry and build budgets

**Files:**

- Modify: `scripts/blender/build_synthetic_village.py`
- Modify: `pipeline/synthetic_village/surface_realism.py`
- Modify: `tests/test_synthetic_village_surface_realism.py`
- Modify: `tests/test_synthetic_village_blender_runtime.py`

**Interfaces:**

- Consumes:
  exact `PathDetailRecord` and `PathRutRun` records.
- Produces:
  `surface-stone-fragments`,
  `surface-leaf-litter`,
  `surface-damp-patches`,
  exactly 18 mesh parts, rut relief in the existing ribbon, and bounded build
  evidence.

- [ ] **Step 1: Add failing detail cap and builder evidence tests**

```python
def test_every_path_has_three_detail_parts_and_bounded_counts(
    tmp_path: Path,
) -> None:
    _visual_root, bundle = publish_material_fixture(tmp_path)
    plan = build_surface_realism_plan(
        build_scene_plan(),
        bundle.final_directory,
    )
    counts = Counter(
        detail.detail_class
        for path in plan.path_plans
        for detail in path.details
    )
    counts["rut-run"] = sum(len(path.rut_runs) for path in plan.path_plans)
    assert all(counts[key] <= MAX_DETAIL_COUNTS[key] for key in counts)
    assert all(
        {detail.detail_class for detail in path.details}
        == {"damp-patch", "leaf-card", "stone-fragment"}
        for path in plan.path_plans
    )
```

The real runtime report must assert:

```python
assert report["surface_realism"]["detail_mesh_object_count"] == 18
assert report["counts"]["mesh_objects"] == 572
assert report["counts"]["glb_primitives"] == 577
assert report["counts"]["glb_triangles"] <= 125_000
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_realism.py \
  tests/test_synthetic_village_blender_runtime.py \
  -k detail -q
```

Expected: Blender evidence is absent.

- [ ] **Step 3: Build consolidated detail assemblers**

Add exact path-frame and planar-detail helpers:

```python
def _path_detail_frame(points, arc_length_m, side_fraction, width_m, extent):
    centerline = _resample_polyline_at_most(points, step_m=1.0)
    index = min(
        range(len(centerline)),
        key=lambda row: abs(centerline[row][3] - arc_length_m),
    )
    tangent = _bounded_path_tangent(centerline, index)
    normal = Vector((-tangent.y, tangent.x))
    center = Vector(centerline[index][:3])
    position = center + Vector((
        normal.x * side_fraction * width_m * 0.5,
        normal.y * side_fraction * width_m * 0.5,
        0.0,
    ))
    position.z = _terrain_height(position.x, position.y, extent) + 0.115
    return position, tangent, normal


def _add_leaf_diamond(assembler, position, scale, yaw_deg):
    half_length = 0.14 * scale
    half_width = 0.055 * scale
    yaw = math.radians(yaw_deg)
    tangent = Vector((math.cos(yaw), math.sin(yaw), 0.0))
    normal = Vector((-tangent.y, tangent.x, 0.0))
    vertices = (
        position + tangent * half_length,
        position + normal * half_width,
        position - tangent * half_length,
        position - normal * half_width,
    )
    assembler.add(
        [tuple(vertex) for vertex in vertices],
        ((0, 1, 2), (0, 2, 3)),
    )


def _add_irregular_damp_patch(assembler, position, tangent, normal, detail):
    digest = hashlib.sha256(detail["detail_id"].encode("utf-8")).digest()
    vertices = [tuple(position)]
    for index in range(8):
        angle = 2.0 * math.pi * index / 8.0
        jitter = 0.82 + digest[index] / 255.0 * 0.28
        along = math.cos(angle) * 0.48 * detail["scale"] * jitter
        across = math.sin(angle) * 0.28 * detail["scale"] * jitter
        vertex = position + tangent * along + normal * across
        vertices.append((vertex.x, vertex.y, vertex.z + 0.003))
    assembler.add(
        vertices,
        tuple((0, index + 1, (index + 1) % 8 + 1) for index in range(8)),
    )
```

In `_build_linear_feature`, for a v1 path create exactly three assemblers:

```python
stones = MeshAssembler()
leaves = MeshAssembler()
damp = MeshAssembler()
for detail in path_plan["details"]:
    position, tangent, normal = _path_detail_frame(
        topology["points"],
        detail["arc_length_m"],
        detail["side_fraction"],
        topology["width_m"],
        extent,
    )
    if detail["detail_class"] == "stone-fragment":
        stones.add_ellipsoid(
            (position.x, position.y, position.z + 0.07 * detail["scale"]),
            (
                0.18 * detail["scale"],
                0.12 * detail["scale"],
                0.08 * detail["scale"],
            ),
            segments=7,
            rings=3,
        )
    elif detail["detail_class"] == "leaf-card":
        _add_leaf_diamond(leaves, position, detail["scale"], detail["yaw_deg"])
    else:
        _add_irregular_damp_patch(
            damp,
            position,
            tangent,
            normal,
            detail,
        )
```

Link the three non-empty parts with existing verified materials:

```text
surface-stone-fragments -> material-creek-rock-01
surface-leaf-litter -> material-broadleaf-canopy-01
surface-damp-patches -> material-packed-earth-01
```

Set `nv_surface_detail_class` and
`nv_surface_color_scale=0.88` on damp patches. Leaf diamonds must have a
non-square silhouette; all details stay outside the central 1.2 m corridor.

- [ ] **Step 4: Measure evidence and select profile budgets**

Set:

```python
MAX_SURFACE_GLTF_TRIANGLES = 125_000
MAX_SURFACE_GLB_BYTES = 160_000_000
EXPECTED_SURFACE_GLB_PRIMITIVES = 577
EXPECTED_SURFACE_DETAIL_MESH_OBJECTS = 18
```

Legacy and building-v2-only builds keep their previous `100_000`,
`150_000_000`, and `559` caps. Select limits only from the explicit profile.
Build `SurfaceRealismBuildEvidence` from measured Blender data; do not copy
request counts into the report.

- [ ] **Step 5: Run focused tests and source checks**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_realism.py \
  tests/test_synthetic_village_blender_runtime.py \
  tests/test_local_textured_preview.py -q
git diff --check
```

Expected: all selected non-opt-in tests pass and no whitespace errors exist.

- [ ] **Step 6: Commit and push path details**

```bash
git add \
  scripts/blender/build_synthetic_village.py \
  pipeline/synthetic_village/surface_realism.py \
  tests/test_synthetic_village_surface_realism.py \
  tests/test_synthetic_village_blender_runtime.py
git commit -m "feat(surface): add deterministic path detail" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  scripts/blender/build_synthetic_village.py \
  pipeline/synthetic_village/surface_realism.py \
  tests/test_synthetic_village_surface_realism.py \
  tests/test_synthetic_village_blender_runtime.py
git push origin main
```

---

### Task 5: Independent float `COLOR_0` and combined-budget GLB audit

**Files:**

- Modify: `pipeline/synthetic_village/glb_material_audit.py`
- Modify: `pipeline/synthetic_village/local_textured_preview.py`
- Modify: `tests/test_glb_material_audit.py`
- Modify: `tests/test_local_textured_preview.py`

**Interfaces:**

- Consumes:
  `ExpectedSurfaceRealism`,
  actual GLB JSON and binary chunks.
- Produces:
  `GlbSurfaceRealismEvidence`,
  decoded float colour bounds,
  unique-colour counts,
  mesh-mode agreement, and selected combined budgets.

- [ ] **Step 1: Extend the handcrafted GLB fixture and write RED tests**

Add a float colour buffer view and accessor:

```python
color_view = append(
    struct.pack(
        "<12f",
        0.88, 0.92, 1.04, 1.0,
        0.94, 1.00, 1.08, 1.0,
        1.02, 1.06, 1.10, 1.0,
    ),
    target=34962,
)
document["accessors"].append({
    "bufferView": color_view,
    "componentType": 5126,
    "count": 3,
    "type": "VEC4",
})
document["meshes"][0]["primitives"][0]["attributes"]["COLOR_0"] = 5
document["nodes"][0]["extras"] = {
    "nv_surface_realism_profile": SURFACE_PROFILE_V1,
    "nv_surface_color_mode": "macro",
    "nv_surface_palette_sha256": "3" * 64,
}
```

Test acceptance and mutations:

```python
def _expected_surface() -> ExpectedSurfaceRealism:
    return ExpectedSurfaceRealism(
        profile_id=SURFACE_PROFILE_V1,
        active_macro_slots=("material-packed-earth-01",),
    )


def test_surface_audit_accepts_float_color_above_one(tmp_path: Path) -> None:
    document, binary = _document_and_binary(surface=True)
    path = tmp_path / "surface.glb"
    path.write_bytes(_glb(document, binary))
    audit = audit_textured_glb(
        path,
        expected_materials=_expected(),
        expected_surface_realism=_expected_surface(),
    )
    assert audit.surface_realism is not None
    assert audit.surface_realism.color_primitive_count == 1
    assert audit.surface_realism.color_min == pytest.approx(0.88)
    assert audit.surface_realism.color_max == pytest.approx(1.10)


@pytest.mark.parametrize("case", [
    "missing",
    "normalized-u8",
    "vec3",
    "wrong-count",
    "below-bound",
    "above-bound",
    "white-macro",
    "colored-white",
    "profile-extra",
    "triangle-budget",
    "byte-budget",
])
def test_surface_audit_rejects_invalid_color_contract(
    case: str,
    tmp_path: Path,
) -> None:
    document, binary, expected, message = _invalid_surface_case(case)
    path = tmp_path / f"{case}.glb"
    path.write_bytes(_glb(document, binary))
    with pytest.raises(GlbMaterialAuditError, match=message):
        audit_textured_glb(
            path,
            expected_materials=_expected(),
            expected_surface_realism=expected,
        )
```

Implement `_invalid_surface_case` as a closed `match case` dispatcher with no
default acceptance path. Each case applies exactly one mutation:

| Case | Exact mutation | Stable message fragment |
|---|---|---|
| `missing` | remove primitive `COLOR_0` | `COLOR_0 is absent` |
| `normalized-u8` | set component `5121`, `normalized=true` | `must be unnormalized FLOAT` |
| `vec3` | set accessor type `VEC3` | `must be VEC4` |
| `wrong-count` | set colour count to `2` | `vertex count disagrees` |
| `below-bound` | replace first red float with `0.87` | `below 0.88` |
| `above-bound` | replace last blue float with `1.11` | `above 1.10` |
| `white-macro` | replace all RGB values with `1.0` | `macro color is constant` |
| `colored-white` | set node mode `white` without changing colours | `white mode is colored` |
| `profile-extra` | set node profile to a different ID | `profile mismatch` |
| `triangle-budget` | set expected maximum triangles one below actual | `triangle budget` |
| `byte-budget` | set expected maximum bytes one below GLB size | `byte budget` |

The dispatcher returns the mutated document/binary, expectation, and exact
message fragment. Unknown case names raise `AssertionError(case)`.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_glb_material_audit.py -k surface -q
```

Expected: audit has no surface expectation or evidence.

- [ ] **Step 3: Retain binary accessor metadata and decode float colours**

Extend `_AccessorEvidence`:

```python
@dataclass(frozen=True)
class _AccessorEvidence:
    count: int
    component_type: int
    value_type: str
    view_index: int
    byte_offset: int
    normalized: bool
```

Implement `_decode_float_vec4` using the validated buffer-view range and
`struct.iter_unpack("<4f", payload)`. Reject byte stride, sparse accessors,
non-finite values, normalized flags, count disagreement, and values outside
`[0.88, 1.10]`.

- [ ] **Step 4: Map node surface modes to meshes**

Define:

```python
class ExpectedSurfaceRealism(FrozenModel):
    profile_id: Literal["source-consistent-multiscale-surface-v1"]
    maximum_triangles: Literal[125000] = 125_000
    maximum_primitives: Literal[580] = 580
    maximum_bytes: Literal[160000000] = 160_000_000
    expected_detail_mesh_objects: Literal[18] = 18
    active_macro_slots: tuple[str, ...]
```

Require each mesh to be referenced by exactly one surface-tagged node. Modes
are `macro`, `damp`, or `white`. `macro` and `damp` require non-constant
colours; `white` requires every decoded component to equal `1.0`. Aggregate
non-constant evidence by active material slot.

- [ ] **Step 5: Preserve legacy audit compatibility**

When `expected_surface_realism` is `None`, retain the existing UV/tangent/PBR
audit and do not require `COLOR_0`. Historical stored audits continue through
`HistoricalLocalGlbMaterialAudit`. New v1 preview verification always passes an
expectation derived from the report.

Allow `ExpectedBuildingGeometry.maximum_total_triangles` to be `100_000` for
legacy and `125_000` for v1, bounded by `Field(ge=1, le=125_000)`.

- [ ] **Step 6: Run audit and local publication tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_glb_material_audit.py \
  tests/test_local_textured_preview.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/local_textured_preview.py \
  tests/test_glb_material_audit.py \
  tests/test_local_textured_preview.py
```

Expected: all tests and Ruff pass, including legacy fixtures.

- [ ] **Step 7: Commit and push the independent audit**

```bash
git add \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/local_textured_preview.py \
  tests/test_glb_material_audit.py \
  tests/test_local_textured_preview.py
git commit -m "feat(surface): audit portable vertex color" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/local_textured_preview.py \
  tests/test_glb_material_audit.py \
  tests/test_local_textured_preview.py
git push origin main
```

---

### Task 6: Real macOS Blender canary and immutable preview

**Files:**

- Modify: `tests/test_local_textured_preview.py`
- Modify: `tests/test_synthetic_village_blender_runtime.py`
- Private output:
  `.nantai-studio/synthetic-village/hybrid-v3/local-previews/<preview-id>/`
- Private output:
  `.nantai-studio/synthetic-village/hybrid-v3/local-training-builds/<report-sha>/`

**Interfaces:**

- Consumes:
  Mac Blender 4.5.11,
  visual revision `packed-earth-v2-be7dcd29`,
  material bundle
  `9874e4c4b56c6942ab0a73186dbb15b07500cd6b6ce5d723fbfd97e54756f992`.
- Produces:
  verified immutable `.blend`, GLB, report, GLB audit, manifest, preview
  images, and a same-origin Viewer URL.

- [ ] **Step 1: Add the opt-in real-runtime acceptance**

Extend the existing `NANTAI_RUN_LOCAL_ELEVATED_BUILD=1` test:

```python
assert report["surface_realism_profile_id"] == SURFACE_PROFILE_V1
surface = report["surface_realism"]
assert surface["terrain_resolution"] == [176, 126]
assert surface["terrain_triangle_count"] == 43_750
assert surface["detail_mesh_object_count"] == 18
assert surface["color_min"] <= 0.94
assert surface["color_max"] >= 1.04
assert report["counts"]["glb_triangles"] <= 125_000
assert report["counts"]["glb_primitives"] == 577
assert (staging / "village-canary.glb").stat().st_size <= 160_000_000
```

Re-run the independent audit inside the test and require its surface evidence
to agree with the report.

- [ ] **Step 2: Run the real Blender test**

Run:

```bash
NANTAI_RUN_LOCAL_ELEVATED_BUILD=1 \
  .venv/bin/python -m pytest \
  tests/test_local_textured_preview.py \
  -k "local_blender_builds_four_registered_elevated_components" -vv
```

Expected: one real Blender build passes. If it fails, keep its staging evidence
private, fix the first structural boundary, and rerun the same test.

- [ ] **Step 3: Publish the explicit pilot bundle**

Run `run_local_textured_preview` with:

```text
visual_pack_root =
  .nantai-studio/synthetic-village/hybrid-v3/
  visual-source-revisions/packed-earth-v2-be7dcd29
material_bundle_root =
  .nantai-studio/synthetic-village/hybrid-v3/material-bundles/
  9874e4c4b56c6942ab0a73186dbb15b07500cd6b6ce5d723fbfd97e54756f992
executable =
  /Applications/Blender.app/Contents/MacOS/Blender
training_build_root =
  .nantai-studio/synthetic-village/hybrid-v3/local-training-builds
```

Use an explicit content identity, never directory modification time. Print and
record:

```text
preview_id
build-report SHA-256
GLB SHA-256 and bytes
triangle count
primitive count
COLOR_0 primitive count
detail counts
training-build directory
```

- [ ] **Step 4: Reverify immutable output from current bytes**

Run `verify_local_textured_preview_directory` and
`verify_local_textured_training_build_layout`. Then parse the GLB again through
`audit_textured_glb`; require all recorded hashes and counts to match.

- [ ] **Step 5: Commit and push only real-runtime test adjustments**

```bash
git add \
  tests/test_local_textured_preview.py \
  tests/test_synthetic_village_blender_runtime.py
git commit -m "test(surface): gate real Blender profile" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  tests/test_local_textured_preview.py \
  tests/test_synthetic_village_blender_runtime.py
git push origin main
```

No private artifact is staged.

---

### Task 7: Registered 1.6 m quality metrics and Blender/Viewer parity

**Files:**

- Create: `pipeline/synthetic_village/surface_quality.py`
- Create: `tests/test_synthetic_village_surface_quality.py`
- Modify: `web/viewer/model-preview.test.mjs`

**Interfaces:**

- Consumes:
  verified source/derived images,
  Blender RGB/depth/semantic frames,
  stable path centreline,
  matched Viewer screenshots,
  camera matrices,
  surface/detail world anchors.
- Produces:
  `SurfaceQualityReport`,
  source SSIM,
  3 m path autocorrelation,
  detail gradient ratio,
  macro percentile bounds,
  anchor Spearman correlation,
  projection error, and pass/fail.

- [ ] **Step 1: Write failing metric tests with controlled arrays**

```python
def test_path_autocorrelation_detects_three_metre_repetition() -> None:
    arc = np.arange(0.0, 60.0, 0.1)
    repeated = np.sin(2.0 * np.pi * arc / 3.0)
    varied = np.sin(2.0 * np.pi * arc / 11.0) * 0.2
    assert path_lag_autocorrelation(arc, repeated, lag_m=3.0) > 0.9
    assert abs(path_lag_autocorrelation(arc, varied, lag_m=3.0)) < 0.35


def test_quality_report_requires_every_numeric_gate() -> None:
    report = SurfaceQualityReport(
        source_to_derived_ssim=0.95,
        candidate_three_m_peak=0.30,
        legacy_three_m_peak=0.50,
        detail_gradient_ratio=1.25,
        macro_p05=0.93,
        macro_p95=1.05,
        anchor_spearman=0.85,
        maximum_projection_error_px=2.0,
        camera_eye_height_m=1.6,
        sampled_camera_ids=(
            "camera-ground-route-001",
            "camera-ground-route-019",
            "camera-ground-route-037",
        ),
    )
    assert report.passes is True
    assert report.model_copy(
        update={"anchor_spearman": 0.79},
    ).passes is False
```

- [ ] **Step 2: Run metric tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_quality.py -q
```

Expected: module does not exist.

- [ ] **Step 3: Implement metric primitives**

Implement:

```python
def _validate_metric_vectors(
    left: np.ndarray,
    right: np.ndarray,
    *,
    minimum_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    if (
        left.ndim != 1
        or right.ndim != 1
        or left.shape != right.shape
        or left.size < minimum_size
        or not np.isfinite(left).all()
        or not np.isfinite(right).all()
    ):
        raise ValueError("metric vectors are absent, mismatched, or non-finite")
    return left, right


def path_lag_autocorrelation(
    arc_length_m: np.ndarray,
    luminance: np.ndarray,
    *,
    lag_m: float = 3.0,
    bin_m: float = 0.10,
    detrend_window_m: float = 10.0,
) -> float:
    arc, values = _validate_metric_vectors(
        arc_length_m,
        luminance,
        minimum_size=3,
    )
    if np.any(np.diff(arc) <= 0.0) or lag_m <= 0 or bin_m <= 0:
        raise ValueError("path samples or lag parameters are invalid")
    grid = np.arange(arc[0], arc[-1] + bin_m * 0.5, bin_m)
    if grid.size < 3:
        raise ValueError("registered path coverage is too short")
    regular = np.interp(grid, arc, values)
    window_bins = max(3, round(detrend_window_m / bin_m))
    kernel = np.ones(window_bins, dtype=np.float64) / window_bins
    trend = np.convolve(regular, kernel, mode="same")
    detrended = regular - trend
    lag_bins = round(lag_m / bin_m)
    if lag_bins <= 0 or lag_bins >= detrended.size - 1:
        raise ValueError("requested lag is outside registered path coverage")
    correlation = np.corrcoef(detrended[:-lag_bins], detrended[lag_bins:])[0, 1]
    if not np.isfinite(correlation):
        raise ValueError("path correlation is undefined")
    return float(correlation)


def rank_correlation(left: np.ndarray, right: np.ndarray) -> float:
    left, right = _validate_metric_vectors(left, right, minimum_size=12)

    def average_ranks(values):
        order = np.argsort(values, kind="mergesort")
        ranks = np.empty(values.size, dtype=np.float64)
        start = 0
        while start < values.size:
            stop = start + 1
            while stop < values.size and values[order[stop]] == values[order[start]]:
                stop += 1
            ranks[order[start:stop]] = (start + stop - 1) / 2.0
            start = stop
        return ranks

    correlation = np.corrcoef(average_ranks(left), average_ranks(right))[0, 1]
    if not np.isfinite(correlation):
        raise ValueError("rank correlation is undefined")
    return float(correlation)


class SurfaceQualityReport(FrozenModel):
    source_to_derived_ssim: float
    candidate_three_m_peak: float
    legacy_three_m_peak: float
    detail_gradient_ratio: float
    macro_p05: float
    macro_p95: float
    anchor_spearman: float
    maximum_projection_error_px: float
    camera_eye_height_m: Literal[1.6]
    sampled_camera_ids: tuple[str, str, str]

    @computed_field
    @property
    def passes(self) -> bool:
        return (
            self.source_to_derived_ssim >= 0.94
            and self.candidate_three_m_peak <= 0.35
            and self.candidate_three_m_peak <= self.legacy_three_m_peak * 0.70
            and self.detail_gradient_ratio >= 1.20
            and self.macro_p05 <= 0.94
            and self.macro_p95 >= 1.04
            and self.anchor_spearman >= 0.80
            and self.maximum_projection_error_px <= 3.0
            and self.camera_eye_height_m == 1.6
        )
```

Derive `passes` only from:

```python
source_to_derived_ssim >= 0.94
candidate_three_m_peak <= 0.35
candidate_three_m_peak <= legacy_three_m_peak * 0.70
detail_gradient_ratio >= 1.20
macro_p05 <= 0.94
macro_p95 >= 1.04
anchor_spearman >= 0.80
maximum_projection_error_px <= 3.0
camera_eye_height_m == 1.6
```

Reject non-finite arrays, inconsistent shapes, empty registered samples,
non-monotonic arc length, and fewer than 12 visible parity anchors.

- [ ] **Step 4: Generate matched private evidence**

Render clear-weather Blender RGB/depth/semantic layers at three
`camera-ground-route-*` poses with `eye_height_m=1.6`. Capture Viewer images at
`1920 × 1080` from the same matrices. Reconstruct visible packed-earth pixels
to world coordinates, project to the nearest stable path centreline, bin at
`0.10 m`, remove a `10 m` rolling mean, and measure the 3 m lag.

Build a verification-only no-detail control with identical source, macro
palette, subdivision, camera, and lighting. It cannot be published.

- [ ] **Step 5: Verify standard Viewer consumption**

Extend `web/viewer/model-preview.test.mjs` to prove the schema does not add a
Viewer surface-generation capability or trust field:

```javascript
assert.equal(manifest.material_fidelity, 'synthetic-derived-pbr');
assert.equal(manifest.real_photo_textures, false);
assert.equal(manifest.geometry_usability, 'preview-only');
assert.equal('surface_shader' in manifest, false);
```

The GLB loader itself owns `COLOR_0` consumption through standard Three.js.

- [ ] **Step 6: Run metric and Viewer tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_surface_quality.py -q
node --test web/viewer/model-preview.test.mjs
```

Expected: synthetic metric fixtures and Viewer truth tests pass. The real
private quality report must also return `passes=true` before the new preview
is recommended.

- [ ] **Step 7: Commit and push quality gates**

```bash
git add \
  pipeline/synthetic_village/surface_quality.py \
  tests/test_synthetic_village_surface_quality.py \
  web/viewer/model-preview.test.mjs
git commit -m "test(surface): measure pedestrian realism" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/surface_quality.py \
  tests/test_synthetic_village_surface_quality.py \
  web/viewer/model-preview.test.mjs
git push origin main
```

---

### Task 8: Browser acceptance, verification receipt, and handoff

**Files:**

- Create: `docs/verification/2026-07-19-multiscale-surface-realism.md`
- Create: `handoff/FEEDBACK-CODEX-009-multiscale-surface-realism.md`
- Modify only if current truth changed: `AGENTS.md`

**Interfaces:**

- Consumes:
  immutable preview/build IDs,
  GLB audit,
  surface quality report,
  browser console evidence,
  full repository gates.
- Produces:
  reproducible receipt, user-viewable localhost URL, and Opus five-part
  handoff.

- [ ] **Step 1: Open the immutable preview without replacing the default**

Use:

```text
http://127.0.0.1:8767/web/viewer/?modelPreview=
  %2Fapi%2Flocal-textured-preview%2F<preview-id>%2Fmanifest.json
```

Keep the current default manifest byte-unchanged. Confirm model mode, 360°
orbit, forward/reverse motion, zoom, and ground-level framing.

- [ ] **Step 2: Exercise all weather states**

Switch:

```text
clear
overcast
rain
snow
fog
night
```

Return to clear and confirm immutable material colour/roughness state is
restored. Check the browser console for errors and warnings caused by this
change.

- [ ] **Step 3: Inspect required visual defects**

At 1.6 m, verify:

- macro pattern remains world-locked through orbit;
- no 3 m dominant repeat remains;
- ruts are shallow;
- the central 1.2 m path remains visually clear;
- stones, leaf diamonds, and damp patches are visible without z-fighting;
- no black, magenta, stretched, or obvious rectangular-card material appears;
- overview remains secondary and does not override a ground-level failure.

Record every remaining synthetic defect, including simplified foliage,
buildings, chunk mismatch, or lack of improved 3DGS.

- [ ] **Step 4: Run full local gates**

Run:

```bash
.venv/bin/python -m pytest tests -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
.venv/bin/python -m ruff check pipeline scripts tests
.venv/bin/python -m compileall -q pipeline scripts
git diff --check
```

Expected: all tests pass, with only documented environment skips/warnings.

- [ ] **Step 5: Write the verification receipt**

Record:

- exact commit, preview ID, report SHA, GLB SHA/bytes;
- source revision, material bundle, surface plan, and runtime module hashes;
- triangle, primitive, mesh, detail, colour, UV, tangent, and image counts;
- all quality metrics and camera IDs;
- six-weather browser result and console status;
- local Mac L0/non-authoritative boundary;
- Windows L2 and infinite-chunk work still outstanding;
- real 3DGS rerender/retrain still outstanding.

- [ ] **Step 6: Write the Opus handoff**

Use these headings:

```markdown
## What
## Why
## Tradeoff
## Open
## Next
```

Include exact file/commit/artifact IDs, the combined-budget decision, the
finite-vs-infinite boundary, and any review request.

- [ ] **Step 7: Commit and push evidence**

```bash
git add \
  docs/verification/2026-07-19-multiscale-surface-realism.md \
  handoff/FEEDBACK-CODEX-009-multiscale-surface-realism.md
git commit -m "docs(surface): record multiscale realism evidence" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  docs/verification/2026-07-19-multiscale-surface-realism.md \
  handoff/FEEDBACK-CODEX-009-multiscale-surface-realism.md
git push origin main
```

If `AGENTS.md` truth changed, stage it explicitly in the same evidence commit
and include it after `--`.

- [ ] **Step 8: Verify remote state and CI**

Run:

```bash
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count HEAD...origin/main
git status --short --branch
gh run list --commit "$(git rev-parse HEAD)" --limit 5
```

Expected:

- local and remote hashes match;
- divergence is `0 0`;
- the collaborator weather WIP remains the only unrelated tracked change;
- GitHub CI completes successfully.

---

## Spec Coverage Review

- Decision and provenance: Tasks 1, 2, and 8.
- Exact PBR detail retention and source-derived macro palette: Tasks 1 and 3.
- Standard float `COLOR_0`, no Viewer shader: Tasks 3, 5, and 7.
- 4 m terrain and six-rail 1 m path: Task 3.
- Ruts, stones, leaves, damp patches, clear corridor: Task 4.
- 125k/580/160MB/24-material budgets: Tasks 4 and 5.
- Blender training and Viewer artifact binding: Tasks 2, 5, 6, and 7.
- 1.6 m numerical and visual gates: Tasks 7 and 8.
- Historical request/audit compatibility: Tasks 2 and 5.
- Fail-closed publication and recovery: Tasks 2, 5, and 6.
- Infinite chunk and real 3DGS limitations: Task 8; implementation remains a
  separately versioned follow-on exactly as the approved spec requires.
