# Four-sided Building Geometry v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace front-only synthetic house detailing with a content-addressed, four-sided rural-building geometry profile that stays readable during 360-degree orbit.

**Architecture:** A small pure-Python contract module owns profile IDs, stable SHA-256 variant mapping, budgets, and report models. The standalone Blender builder consumes the explicit profile, appends geometry to existing building mesh parts, measures the result, and exports root-node evidence. The existing GLB material audit gains an optional v2 geometry audit so one immutable publication receipt covers both PBR and building geometry.

**Tech Stack:** Python 3.11+, Pydantic v2, Blender 4.5.11 Python API, glTF/GLB 2.0, pytest, SHA-256 canonical JSON.

## Global Constraints

- Work only on `main`; do not create branches or worktrees.
- Stage only explicit paths; never use `git add -A` or `git commit -a`.
- End every Codex commit with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Do not stage or edit the collaborator-owned `tests/test_synthetic_village_weather.py`.
- Preserve historical request/report canonical bytes when the geometry-profile field was absent.
- Unknown profile IDs fail closed.
- The v2 profile is `four-sided-rural-building-v2`; the compatibility default is `front-facade-box-v1`.
- Variant mapping is SHA-256 remainder `0=balanced-residence`, `1=side-entry-workshop`, `2=rear-service-house`.
- Expected variant counts for the current 70 stable building IDs are `21 / 29 / 20`.
- Keep 70 building roots, 126 object-registry rows, 24 materials, 544 GLB primitives, and zero external texture URIs.
- Add no Blender mesh objects or material/image identities for v2.
- Budgets: at most 220 added Blender polygons per building, 15,400 in total, 720 GLB triangles per building subtree, 100,000 total GLB triangles, and 150,000,000 GLB bytes.
- Preserve `synthetic / L0 / preview-only`; no geometry or trust promotion.

---

### Task 1: Pure geometry identity and evidence contract

**Files:**
- Create: `pipeline/synthetic_village/building_geometry.py`
- Create: `tests/test_building_geometry.py`

**Interfaces:**
- Produces: `BuildingGeometryProfileId`, `BuildingVariantId`, `BuildingGeometryEvidence`, `building_variant(object_id, profile_id)`, `expected_variant_counts(object_ids, profile_id)`.
- Consumed by: request/report models, local-preview publication, GLB audit tests, and the Blender-script parity checks.

- [ ] **Step 1: Write the failing identity and evidence tests**

```python
from collections import Counter

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.building_geometry import (
    BUILDING_GEOMETRY_V2,
    BuildingGeometryEvidence,
    building_variant,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


def test_v2_variant_mapping_is_stable_for_all_canonical_buildings() -> None:
    buildings = [
        row.object_id
        for row in build_scene_plan().objects
        if row.semantic_class == "building"
    ]
    first = [building_variant(row, BUILDING_GEOMETRY_V2) for row in buildings]
    second = [building_variant(row, BUILDING_GEOMETRY_V2) for row in buildings]

    assert first == second
    assert Counter(first) == {
        "balanced-residence": 21,
        "side-entry-workshop": 29,
        "rear-service-house": 20,
    }


def test_v2_evidence_rejects_missing_elevation_and_budget_overrun() -> None:
    valid = {
        "profile_id": BUILDING_GEOMETRY_V2,
        "building_count": 70,
        "covered_elevations": ("front", "left", "rear", "right"),
        "variant_counts": {
            "balanced-residence": 21,
            "side-entry-workshop": 29,
            "rear-service-house": 20,
        },
        "added_face_count": 1000,
        "maximum_added_faces_per_building": 20,
        "new_mesh_object_count": 0,
    }
    BuildingGeometryEvidence.model_validate(valid)
    for key, value in (
        ("covered_elevations", ("front", "rear", "right")),
        ("added_face_count", 15401),
        ("maximum_added_faces_per_building", 221),
        ("new_mesh_object_count", 1),
    ):
        mutated = dict(valid)
        mutated[key] = value
        with pytest.raises(ValidationError):
            BuildingGeometryEvidence.model_validate(mutated)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_building_geometry.py -q
```

Expected: collection fails because `pipeline.synthetic_village.building_geometry` does not exist.

- [ ] **Step 3: Implement the pure contract**

Create immutable constants and models:

```python
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from pipeline.synthetic_village.contracts import FrozenModel

BUILDING_GEOMETRY_V1 = "front-facade-box-v1"
BUILDING_GEOMETRY_V2 = "four-sided-rural-building-v2"
BUILDING_ELEVATIONS = ("front", "left", "rear", "right")
BUILDING_VARIANTS = (
    "balanced-residence",
    "side-entry-workshop",
    "rear-service-house",
)

BuildingGeometryProfileId = Literal[
    "front-facade-box-v1",
    "four-sided-rural-building-v2",
]
BuildingVariantId = Literal[
    "balanced-residence",
    "side-entry-workshop",
    "rear-service-house",
]


def building_variant(
    object_id: str,
    profile_id: BuildingGeometryProfileId,
) -> BuildingVariantId | None:
    if profile_id == BUILDING_GEOMETRY_V1:
        return None
    digest = hashlib.sha256(f"{BUILDING_GEOMETRY_V2}\0{object_id}".encode()).digest()
    return BUILDING_VARIANTS[digest[0] % len(BUILDING_VARIANTS)]


def expected_variant_counts(
    object_ids: tuple[str, ...],
    profile_id: BuildingGeometryProfileId,
) -> dict[str, int]:
    if profile_id == BUILDING_GEOMETRY_V1:
        return {}
    return dict(sorted(Counter(
        building_variant(object_id, profile_id) for object_id in object_ids
    ).items()))


class BuildingGeometryEvidence(FrozenModel):
    profile_id: Literal["four-sided-rural-building-v2"]
    building_count: Literal[70]
    covered_elevations: tuple[
        Literal["front"],
        Literal["left"],
        Literal["rear"],
        Literal["right"],
    ]
    variant_counts: dict[BuildingVariantId, int]
    added_face_count: int = Field(ge=1, le=15400)
    maximum_added_faces_per_building: int = Field(ge=1, le=220)
    new_mesh_object_count: Literal[0]

    @model_validator(mode="after")
    def _validate_exact_v2_counts(self) -> "BuildingGeometryEvidence":
        if self.variant_counts != {
            "balanced-residence": 21,
            "rear-service-house": 20,
            "side-entry-workshop": 29,
        }:
            raise ValueError("building variant counts do not match stable IDs")
        return self
```

- [ ] **Step 4: Run GREEN and lint**

Run:

```bash
.venv/bin/python -m pytest tests/test_building_geometry.py -q
.venv/bin/python -m ruff check pipeline/synthetic_village/building_geometry.py tests/test_building_geometry.py
```

Expected: all new tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit the contract**

```bash
git add pipeline/synthetic_village/building_geometry.py tests/test_building_geometry.py
git commit -m "feat(geometry): define building profile contract" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/synthetic_village/building_geometry.py tests/test_building_geometry.py
git push origin main
```

---

### Task 2: Content-addressed request and report integration

**Files:**
- Modify: `pipeline/synthetic_village/canary.py`
- Modify: `pipeline/synthetic_village/local_textured_preview.py`
- Modify: `tests/test_synthetic_village_canary.py`
- Modify: `tests/test_local_textured_preview.py`

**Interfaces:**
- Consumes: Task 1 profile IDs and `BuildingGeometryEvidence`.
- Produces: optional backward-compatible `building_geometry_profile_id` in textured request/report models and required v2 evidence in new local previews.

- [ ] **Step 1: Write failing compatibility and content-address tests**

Add tests that:

```python
def test_local_request_selects_v2_geometry_and_hashes_it(tmp_path: Path) -> None:
    request = _local_request(tmp_path)
    assert request.building_geometry_profile_id == "four-sided-rural-building-v2"
    payload = request.model_dump(mode="json")
    payload["building_geometry_profile_id"] = "front-facade-box-v1"
    payload.pop("preview_id")
    old_id = request.preview_id
    new_id = hashlib.sha256(
        canary._canonical_json_bytes(payload),
    ).hexdigest()
    assert new_id != old_id


def test_historical_textured_request_omits_absent_geometry_default(
    tmp_path: Path,
) -> None:
    visual_root, bundle = publish_material_fixture(tmp_path)
    request = build_canary_textured_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )
    raw = canonical_textured_build_request_bytes(request)
    assert b"building_geometry_profile_id" not in raw
```

Add report mutation cases that reject a v2 profile with missing
`building_geometry` evidence and reject evidence whose counts differ from the
request's 70 stable building IDs.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_canary.py \
  tests/test_local_textured_preview.py -q
```

Expected: failures mention missing `building_geometry_profile_id` and
`building_geometry`.

- [ ] **Step 3: Add profile fields with historical-byte omission**

Add these fields to `TexturedBuildRequest`, `TexturedBuildReport`,
`LocalTexturedPreviewRequest`, and `LocalTexturedBuildReport`:

```python
building_geometry_profile_id: BuildingGeometryProfileId = BUILDING_GEOMETRY_V1
building_geometry: BuildingGeometryEvidence | None = None
```

Only report models receive `building_geometry`. Validators require:

```python
if self.building_geometry_profile_id == BUILDING_GEOMETRY_V2:
    if self.building_geometry is None:
        raise ValueError("v2 building geometry requires measured evidence")
elif self.building_geometry is not None:
    raise ValueError("v1 building geometry cannot claim v2 evidence")
```

Canonical request/report serializers pop each new default field when it was
absent from `model_fields_set`, using the existing
`material_algorithm_id` compatibility pattern.

`build_local_textured_preview_request()` explicitly writes:

```python
"building_geometry_profile_id": BUILDING_GEOMETRY_V2,
```

`verify_local_textured_build_report()` compares the report profile to the
request and requires v2 evidence.

- [ ] **Step 4: Run GREEN and regression tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_building_geometry.py \
  tests/test_synthetic_village_canary.py \
  tests/test_local_textured_preview.py -q
.venv/bin/python -m ruff check pipeline/synthetic_village tests/test_building_geometry.py tests/test_synthetic_village_canary.py tests/test_local_textured_preview.py
```

Expected: focused tests pass and historical canonical-byte fixtures stay
unchanged.

- [ ] **Step 5: Commit and push request/report integration**

```bash
git add \
  pipeline/synthetic_village/canary.py \
  pipeline/synthetic_village/local_textured_preview.py \
  tests/test_synthetic_village_canary.py \
  tests/test_local_textured_preview.py
git commit -m "feat(geometry): address building profile evidence" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/synthetic_village/canary.py \
     pipeline/synthetic_village/local_textured_preview.py \
     tests/test_synthetic_village_canary.py \
     tests/test_local_textured_preview.py
git push origin main
```

---

### Task 3: Blender four-sided geometry and measured report

**Files:**
- Modify: `scripts/blender/build_synthetic_village.py`
- Create: `tests/test_synthetic_village_building_geometry_contract.py`

**Interfaces:**
- Consumes: request field `building_geometry_profile_id`.
- Produces: unchanged v1 meshes or v2 four-sided geometry, root-node extras, scene-level measured evidence, and the report `building_geometry` block.

- [ ] **Step 1: Write failing standalone-script contract tests**

Read the standalone script as text and extract its stable constants with
`ast.literal_eval`. Assert it declares both profile IDs, the exact ordered
variant tuple, elevation tuple, face/triangle/byte budgets, and does not call
Python `hash(`. Add a pure parity test that evaluates the script's
`_building_variant()` helper source and compares all 70 results with Task 1's
`building_variant()`.

Also assert `_validate_request()` requires the geometry field for new local
requests and rejects any value outside the two allowed profile IDs.

- [ ] **Step 2: Run the contract tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_building_geometry_contract.py -q
```

Expected: failures for absent profile constants and helper.

- [ ] **Step 3: Implement v1/v2 dispatch and deterministic variant helper**

Add standalone constants and:

```python
def _building_variant(object_id, profile_id):
    if profile_id == BUILDING_GEOMETRY_V1:
        return None
    digest = hashlib.sha256(
        f"{BUILDING_GEOMETRY_V2}\0{object_id}".encode("utf-8"),
    ).digest()
    return BUILDING_VARIANTS[digest[0] % 3]
```

The request validator accepts the new key for textured/local requests, requires
one of the two exact IDs, and keeps old non-textured v1 requests byte-for-byte
unchanged.

- [ ] **Step 4: Implement orientation-safe façade primitives**

Add helpers that append boxes to an existing `MeshAssembler`:

```python
def _facade_box(
    assembler,
    elevation,
    wall_width,
    wall_depth,
    center_u,
    center_z,
    size_u,
    size_z,
    thickness,
    offset,
):
    if elevation == "front":
        center = (center_u, -wall_depth / 2 - offset, center_z)
        size = (size_u, thickness, size_z)
    elif elevation == "rear":
        center = (center_u, wall_depth / 2 + offset, center_z)
        size = (size_u, thickness, size_z)
    elif elevation == "left":
        center = (-wall_width / 2 - offset, center_u, center_z)
        size = (thickness, size_u, size_z)
    elif elevation == "right":
        center = (wall_width / 2 + offset, center_u, center_z)
        size = (thickness, size_u, size_z)
    else:
        raise RuntimeBuildError(f"unsupported building elevation: {elevation}")
    assembler.add_box(center, size)


def _facade_quad(
    assembler,
    elevation,
    wall_width,
    wall_depth,
    center_u,
    center_z,
    size_u,
    size_z,
    offset,
):
    u0, u1 = center_u - size_u / 2, center_u + size_u / 2
    z0, z1 = center_z - size_z / 2, center_z + size_z / 2
    if elevation == "front":
        y = -wall_depth / 2 - offset
        vertices = ((u0, y, z0), (u1, y, z0), (u1, y, z1), (u0, y, z1))
    elif elevation == "rear":
        y = wall_depth / 2 + offset
        vertices = ((u0, y, z0), (u0, y, z1), (u1, y, z1), (u1, y, z0))
    elif elevation == "left":
        x = -wall_width / 2 - offset
        vertices = ((x, u0, z0), (x, u0, z1), (x, u1, z1), (x, u1, z0))
    elif elevation == "right":
        x = wall_width / 2 + offset
        vertices = ((x, u0, z0), (x, u1, z0), (x, u1, z1), (x, u0, z1))
    else:
        raise RuntimeBuildError(f"unsupported building elevation: {elevation}")
    assembler.add(vertices, ((0, 1, 2, 3),))


def _add_window_assembly(
    assembler,
    elevation,
    wall_width,
    wall_depth,
    center_u,
    center_z,
):
    window_width, window_height, rail = 1.05, 1.15, 0.10
    _facade_box(
        assembler, elevation, wall_width, wall_depth,
        center_u, center_z, window_width, window_height, 0.04, 0.03,
    )
    for u in (
        center_u - window_width / 2 + rail / 2,
        center_u + window_width / 2 - rail / 2,
        center_u,
    ):
        _facade_quad(
            assembler, elevation, wall_width, wall_depth,
            u, center_z, rail, window_height, 0.085,
        )
    for z in (
        center_z - window_height / 2 + rail / 2,
        center_z + window_height / 2 - rail / 2,
        center_z,
    ):
        _facade_quad(
            assembler, elevation, wall_width, wall_depth,
            center_u, z, window_width, rail, 0.085,
        )


def _add_door_assembly(
    assembler,
    elevation,
    wall_width,
    wall_depth,
    center_u,
    base_z,
    width=1.35,
):
    height, rail = 2.30, 0.10
    center_z = base_z + height / 2
    _facade_box(
        assembler, elevation, wall_width, wall_depth,
        center_u, center_z, width, height, 0.05, 0.035,
    )
    for u in (
        center_u - width / 2 + rail / 2,
        center_u + width / 2 - rail / 2,
        center_u - width / 6,
        center_u + width / 6,
    ):
        _facade_quad(
            assembler, elevation, wall_width, wall_depth,
            u, center_z, rail, height, 0.095,
        )
    for z in (
        base_z + rail / 2,
        base_z + height - rail / 2,
        base_z + height * 0.58,
    ):
        _facade_quad(
            assembler, elevation, wall_width, wall_depth,
            center_u, z, width, rail, 0.095,
        )
```

Use signed offsets so front detail is outside `-Y`, rear outside `+Y`, left
outside `-X`, and right outside `+X`. All box dimensions must be positive and
all surfaces must differ by at least `0.02 m` to avoid z-fighting.

- [ ] **Step 5: Extend `_build_building()` without adding mesh objects**

For v2:

- append a stone plinth band to the existing `base` assembler;
- append fascia/soffit and gable bargeboards to the existing `roof` assembler;
- append corner posts and top beams for all four elevations to `timber`;
- append variant-specific doors to `door`;
- append variant-specific four-side windows to `windows`;
- tag the root with:

```python
root["nv_building_geometry_profile"] = BUILDING_GEOMETRY_V2
root["nv_building_variant"] = variant
root["nv_facade_elevations"] = json.dumps(
    BUILDING_ELEVATIONS,
    separators=(",", ":"),
)
root["nv_added_face_count"] = added_faces
```

Measure `added_faces` as the difference between the five assembler face totals
before and after v2 additions. Return that integer to the scene validator.

- [ ] **Step 6: Fail closed on scene-level geometry evidence**

In `_validate_built_scene()`:

- collect exactly 70 building roots;
- recompute and compare every variant;
- require the exact four-elevation JSON;
- require each added-face value in `1..220`;
- require sum in `1..15400`;
- compare mesh-object count against the v1 construction count so the delta is
  zero;
- write `nv_building_geometry_evidence` as canonical compact JSON.

In `_build_report()`, parse that scene property and emit it as
`building_geometry` only for v2.

- [ ] **Step 7: Run source contract and existing builder contract tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_building_geometry_contract.py \
  tests/test_synthetic_village_canary.py \
  tests/test_local_textured_preview.py -q
.venv/bin/python -m ruff check \
  scripts/blender/build_synthetic_village.py \
  tests/test_synthetic_village_building_geometry_contract.py
```

Expected: all focused tests pass.

- [ ] **Step 8: Commit and push Blender geometry**

```bash
git add \
  scripts/blender/build_synthetic_village.py \
  tests/test_synthetic_village_building_geometry_contract.py
git commit -m "feat(geometry): build four-sided rural houses" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- scripts/blender/build_synthetic_village.py \
     tests/test_synthetic_village_building_geometry_contract.py
git push origin main
```

---

### Task 4: Independent GLB geometry audit

**Files:**
- Modify: `pipeline/synthetic_village/glb_material_audit.py`
- Modify: `pipeline/synthetic_village/local_textured_preview.py`
- Modify: `tests/test_glb_material_audit.py`
- Modify: `tests/test_local_textured_preview.py`

**Interfaces:**
- Consumes: `ExpectedBuildingGeometry(profile_id, variant_counts, maximum_*)`.
- Produces: optional v2 geometry fields in `GlbMaterialAudit`; local preview passes the expected v2 contract and publishes one combined immutable audit.

- [ ] **Step 1: Add failing handcrafted GLB tests**

Extend the fixture with indexed triangles, one `nv_root=true` building node,
child mesh nodes, and v2 extras. Generate 70 roots with stable IDs from
`build_scene_plan()` and variant extras from `building_variant()`.

Test acceptance, then mutate one case at a time:

- remove an elevation;
- change one variant;
- remove `nv_root`;
- set one building subtree to 721 triangles;
- set total triangle count above 100,000;
- change primitive count away from the expected value;
- change `nv_added_face_count` so it disagrees with report evidence.

Every mutation must raise `GlbMaterialAuditError` with a specific message.

- [ ] **Step 2: Run GLB tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_glb_material_audit.py -q
```

Expected: new imports or expected-geometry arguments fail.

- [ ] **Step 3: Implement node-tree and triangle audit**

Add:

```python
class ExpectedBuildingGeometry(FrozenModel):
    profile_id: Literal["four-sided-rural-building-v2"]
    variant_counts: dict[BuildingVariantId, int]
    maximum_triangles_per_building: Literal[720] = 720
    maximum_total_triangles: Literal[100000] = 100000
    expected_primitive_count: int = Field(ge=1)


class GlbBuildingGeometryEvidence(FrozenModel):
    profile_id: Literal["four-sided-rural-building-v2"]
    building_count: Literal[70]
    variant_counts: dict[BuildingVariantId, int]
    maximum_triangles_per_building: int = Field(ge=1, le=720)
    total_triangle_count: int = Field(ge=1, le=100000)
```

`audit_textured_glb()` receives
`expected_building_geometry: ExpectedBuildingGeometry | None = None`. When
present, it:

1. validates all index accessors and triangle mode;
2. selects exactly 70 nodes whose extras contain `nv_root=true` and semantic
   class `building`;
3. walks each root's child mesh nodes;
4. sums `indices.count // 3`;
5. validates profile, four elevations, variant recomputation, face extras, and
   budgets;
6. returns `building_geometry` evidence in `GlbMaterialAudit`.

Historical calls without expected geometry retain their original audit bytes.
The canonical local-audit serializer omits absent geometry fields using
`model_fields_set`.

- [ ] **Step 4: Wire the local publisher to the v2 expectation**

Pass the request profile and report variant counts into the audit call. Compare
the returned building-geometry evidence with the report before writing
`glb-material-audit.json`. Reused historical v1 preview directories remain
valid.

- [ ] **Step 5: Run GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_glb_material_audit.py \
  tests/test_local_textured_preview.py \
  tests/test_synthetic_village_canary.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/local_textured_preview.py \
  tests/test_glb_material_audit.py \
  tests/test_local_textured_preview.py
```

Expected: all focused tests pass and v1 audit fixtures remain byte compatible.

- [ ] **Step 6: Commit and push audit integration**

```bash
git add \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/local_textured_preview.py \
  tests/test_glb_material_audit.py \
  tests/test_local_textured_preview.py
git commit -m "feat(audit): verify published building geometry" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/synthetic_village/glb_material_audit.py \
     pipeline/synthetic_village/local_textured_preview.py \
     tests/test_glb_material_audit.py \
     tests/test_local_textured_preview.py
git push origin main
```

---

### Task 5: Real local preview, browser proof, and full quality gate

**Files:**
- Create: `docs/verification/2026-07-18-four-sided-building-geometry-v2.md`
- Private outputs only: `.nantai-studio/verification/2026-07-18-building-geometry-v2/`

**Interfaces:**
- Consumes: verified material bundle
  `b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13`.
- Produces: immutable local preview ID, combined GLB audit, matched-view screenshots, and tracked verification receipt.

- [ ] **Step 1: Run focused tests before the expensive build**

```bash
.venv/bin/python -m pytest \
  tests/test_building_geometry.py \
  tests/test_synthetic_village_building_geometry_contract.py \
  tests/test_glb_material_audit.py \
  tests/test_local_textured_preview.py \
  tests/test_synthetic_village_canary.py -q
```

Expected: zero failures.

- [ ] **Step 2: Build the immutable local preview**

```bash
.venv/bin/python scripts/synthetic_village.py build-textured-preview \
  --blender /Applications/Blender.app/Contents/MacOS/Blender \
  --timeout-seconds 1800
```

Expected: a new 64-hex preview ID, `reused=false`, L0, non-authoritative,
profile v2, 70 buildings, counts `21/29/20`, 544 primitives, GLB below
150,000,000 bytes, and combined audit success.

- [ ] **Step 3: Re-audit published bytes**

Run the CLI audit against the new preview directory and independently inspect
the GLB JSON/accessor counts. Record preview ID, bundle ID, GLB SHA-256, byte
count, total triangles, maximum per-building triangles, face delta, and variant
counts.

- [ ] **Step 4: Verify in the in-app browser**

Open:

```text
http://127.0.0.1:8767/web/viewer/?modelPreview=/api/local-textured-preview/<preview-id>/manifest.json
```

Capture at most three private screenshots:

1. clear overview;
2. front/side/rear orbit composite of one residence;
3. the other two variants with rain and night in a composite.

Confirm 360 orbit, zoom, front/side/rear detail, roof-edge depth, clear/rain/night
material readability, unchanged disclosure, and no console error.

- [ ] **Step 5: Run the complete gate**

```bash
.venv/bin/python -m pytest tests/ -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
.venv/bin/python -m ruff check pipeline scripts tests
.venv/bin/python -m compileall -q pipeline scripts
git diff --check
find designs -type f -name '*.pen' -print 2>/dev/null
git status --short | rg '^\\?\\? [^/]+\\.(png|jpe?g|webm|mp4)$' || true
```

Expected: all tests pass, Ruff and compileall pass, no matching `.pen` design,
and no untracked root-level media.

- [ ] **Step 6: Write and push the verification receipt**

Record exact commands and outputs, matched screenshot hashes, immutable IDs,
visual observations, and honest remaining limits: synthetic geometry,
non-enterable façades, simplified terrain/vegetation, no mesh LOD, no
arbitrary-coordinate textured chunks, and no real reconstruction claim.

```bash
git add docs/verification/2026-07-18-four-sided-building-geometry-v2.md
git commit -m "docs(verification): record building geometry v2" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- docs/verification/2026-07-18-four-sided-building-geometry-v2.md
git push origin main
```
