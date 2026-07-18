# Production Mesh Template Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, independently verify, and privately publish all eleven replaceable textured mesh assets at three real LODs from the existing 24-slot PBR material bundle.

**Architecture:** A path-free Python request binds fixed asset recipes, the exact material-bundle identity, the Blender script identity, and a measured local Blender runtime. A dedicated Blender builder reuses the proven canary material/UV/tangent code and detailed four-sided building geometry, adds purpose-built vegetation and prop recipes, and exports one embedded-PBR GLB per asset per LOD. The Python publisher independently audits GLB structure, triangles, materials, and transformed ENU bounds before absent-only content-addressed publication.

**Tech Stack:** Python 3.11+, Pydantic v2, Trimesh 4.4+, Blender 4.5.11 LTS Python API, binary glTF 2.0, existing derived PBR material bundles, pytest, Ruff.

## Global Constraints

- Work only on `main`; do not create a branch or worktree.
- Execute inline; do not dispatch subagents because the user asked Codex to proceed independently.
- Stage and commit only explicit paths; never use `git add -A` or `git commit -a`.
- Never stage the pre-existing `tests/test_synthetic_village_weather.py` working-tree change unless its owner has committed it.
- End every Codex-created commit with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Push each verified task commit to `origin/main` before accumulating the next task.
- Treat `.nantai-studio/` as private Git-ignored runtime state.
- Use the exact eleven asset IDs and footprints in `assets/registry.json`; do not replace or mutate Gaussian PLY payloads.
- Use the existing immutable 24-slot derived PBR material bundle; do not create another texture registry.
- Every GLB embeds its used base-color, normal, and ORM maps and has no external URI.
- Every primitive has a material, `TEXCOORD_0`, and `TANGENT`.
- Local macOS builds remain `verification_level=L0`, `synthetic=true`, `geometry_usability=preview-only`, and `real_photo_textures=false`.
- Missing, redirected, changed, malformed, over-budget, non-finite, or identity-mismatched evidence fails closed with no primitive fallback.
- Blender report values do not verify themselves. Publication uses the independent Python GLB auditor and Trimesh scene graph.
- The GLB coordinate encoding is explicit: Three/glTF `(x, y, z) = (east, up, -north)`. Bundle AABBs are stored in synthetic ENU Z-up coordinates.
- Every implementation task starts with a failing test and records the observed RED failure.

## Scope Boundary

This plan implements Slice B from
`docs/superpowers/specs/2026-07-18-infinite-textured-mesh-chunks-design.md`.
It produces a real private L0 bundle and a visual contact sheet. It does not
activate mesh streaming in Viewer, publish an authoritative Windows release,
or claim real reconstruction.

## Audited Reuse Decision

The existing finite canary is not an asset-ID source:

- its 70 buildings are scene instances, not the five registry building assets;
- it has no exact `tree_pine_01`, `stone_lamp_01`, or `fence_wood_01` root;
- its terrain recipe and 120 m relief differ from the flat on-demand world.

The new builder therefore reuses code, not scene instances:

- reuse `_create_textured_materials(...)`,
  `_apply_textured_uvs_and_tangents(...)`, `MeshAssembler`,
  `_link_mesh(...)`, and the v2 `_build_building(...)` path;
- create dedicated vegetation and prop geometry tied to registry asset IDs;
- export each asset at the origin with no world-scene placement;
- do not surface-reconstruct Gaussian PLY bytes.

## File Map

- Modify `pipeline/synthetic_village/mesh_asset_bundle.py`: explicit GLB coordinate encoding, independent Trimesh ENU AABB measurement, per-kind/LOD triangle budgets, and publication.
- Modify `tests/test_mesh_asset_bundle.py`: transformed bounds, budget, coordinate, durability, and absent-only publication tests.
- Create `pipeline/synthetic_village/mesh_asset_build.py`: immutable build request/report models, exact material snapshot, Blender process, audit, and publisher orchestration.
- Create `tests/test_mesh_asset_build.py`: identity, path privacy, snapshot, subprocess, report, tamper, and publish tests.
- Create `scripts/blender/build_mesh_asset_bundle.py`: exact Blender request parser, eleven recipes, three LODs, embedded-PBR export, and measured report.
- Create `tests/test_mesh_asset_blender_runtime.py`: conditional real Blender build and structural assertions.
- Modify `scripts/synthetic_village.py`: explicit `build-mesh-assets` CLI command.
- Modify `tests/test_synthetic_village_cli.py`: stable single-object command output.
- Create `docs/verification/2026-07-18-production-mesh-template-bundle.md`: actual L0 bundle and contact-sheet evidence.

---

### Task 1: Coordinate-Aware Independent Bounds and Triangle Budgets

**Files:**
- Modify: `pipeline/synthetic_village/mesh_asset_bundle.py`
- Modify: `tests/test_mesh_asset_bundle.py`

**Interfaces:**
- Consumes: verified GLB bytes and current `MeshAssetBundle`.
- Produces: `GLB_COORDINATE_ENCODING`, `measure_mesh_template_enu_bounds(...)`, `MESH_TRIANGLE_BUDGETS`, `prepare_mesh_asset_bundle(...)`, and `publish_mesh_asset_bundle(...)`.

- [ ] **Step 1: Write failing transformed-bounds and budget tests**

Add a handcrafted GLB whose node translates a 1 m triangle in glTF space:

```python
def test_bundle_measures_transformed_glb_bounds_in_enu(tmp_path: Path) -> None:
    root, _ = write_mesh_bundle_fixture(
        tmp_path,
        gltf_node_translation=(10.0, 3.0, -20.0),
        declared_aabb={
            "min": [10.0, 20.0, 3.0],
            "max": [11.0, 20.0, 4.0],
        },
    )

    bundle = load_mesh_asset_bundle(root)

    assert bundle.coordinate_encoding == "three-east-up-negative-north"
    assert bundle.records[0].lod["2"].aabb.model_dump() == {
        "min": (10.0, 20.0, 3.0),
        "max": (11.0, 20.0, 4.0),
    }
```

Add failures for a changed declared bound, non-finite Trimesh result, unsupported
coordinate encoding, building LOD2 over 720 triangles, vegetation LOD2 over
1200, prop LOD2 over 600, and non-decreasing LOD triangle counts.

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_asset_bundle.py -q
```

Expected: failures because `coordinate_encoding`, independent bound
measurement, and triangle budgets do not exist.

- [ ] **Step 3: Add the exact coordinate and budget contract**

Add:

```python
GLB_COORDINATE_ENCODING = "three-east-up-negative-north"
MESH_TRIANGLE_BUDGETS = {
    "building": {0: 100, 1: 300, 2: 720},
    "vegetation": {0: 160, 1: 500, 2: 1200},
    "prop": {0: 80, 1: 240, 2: 600},
}

class MeshAssetBundle(FrozenModel):
    coordinate_encoding: Literal[
        "three-east-up-negative-north"
    ] = GLB_COORDINATE_ENCODING
```

Record validation requires:

```python
triangles = [record.lod[str(level)].triangle_count for level in (0, 1, 2)]
if not triangles[0] < triangles[1] < triangles[2]:
    raise ValueError("mesh asset LOD triangles must increase strictly")
if any(
    record.lod[str(level)].triangle_count
    > MESH_TRIANGLE_BUDGETS[record.kind][level]
    for level in (0, 1, 2)
):
    raise ValueError("mesh asset exceeds its kind/LOD triangle budget")
```

- [ ] **Step 4: Measure transformed bounds from actual GLB bytes**

Implement:

```python
def measure_mesh_template_enu_bounds(payload: bytes) -> Bounds3:
    scene = trimesh.load_scene(
        file_obj=io.BytesIO(payload),
        file_type="glb",
        resolver=None,
        allow_remote=False,
    )
    gltf_min, gltf_max = np.asarray(scene.bounds, dtype=np.float64)
    enu_min = (gltf_min[0], -gltf_max[2], gltf_min[1])
    enu_max = (gltf_max[0], -gltf_min[2], gltf_max[1])
    return Bounds3(
        min=tuple(float(value) for value in enu_min),
        max=tuple(float(value) for value in enu_max),
    )
```

Reject empty scenes, non-finite bounds, remote resolver behavior, Trimesh
warnings that indicate skipped geometry, and any declared/measured disagreement
larger than `1e-5` m.

- [ ] **Step 5: Add absent-only prepared publication**

`prepare_mesh_asset_bundle(...)` accepts verified per-asset/LOD GLB paths,
copies each unique payload into `objects/<sha256>.glb`, constructs the sorted
manifest, verifies the staging directory, and returns a frozen prepared result.

`publish_mesh_asset_bundle(...)` uses the same project lock and durability
backends as `publish_material_bundle(...)`, publishes to
`<publication-root>/<bundle-id>` only when absent, and verifies reused
destinations byte-for-byte.

- [ ] **Step 6: Run focused gates**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_asset_bundle.py tests/test_glb_material_audit.py -q
.venv/bin/python -m ruff check pipeline/synthetic_village/mesh_asset_bundle.py tests/test_mesh_asset_bundle.py
.venv/bin/python -m compileall -q pipeline/synthetic_village/mesh_asset_bundle.py
git diff --check -- pipeline/synthetic_village/mesh_asset_bundle.py tests/test_mesh_asset_bundle.py
```

Expected: all pass.

- [ ] **Step 7: Commit and push**

```bash
git add pipeline/synthetic_village/mesh_asset_bundle.py tests/test_mesh_asset_bundle.py
git commit -m "feat(mesh): verify template bounds and budgets" \
  -m "Measure transformed ENU bounds from actual GLB bytes and enforce strict per-kind LOD budgets.

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/synthetic_village/mesh_asset_bundle.py tests/test_mesh_asset_bundle.py
git push origin main
```

### Task 2: Path-Free Template Build Request and Blender Invocation

**Files:**
- Create: `pipeline/synthetic_village/mesh_asset_build.py`
- Create: `tests/test_mesh_asset_build.py`

**Interfaces:**
- Consumes: `repo_root`, explicit verified `material_bundle_root`, explicit Blender executable, work/publication roots, and timeout.
- Produces: `MeshAssetBuildRequest`, `MeshAssetBuildReport`, `build_mesh_asset_request(...)`, `canonical_mesh_asset_build_request_bytes(...)`, `run_mesh_asset_build(...)`, and `MeshAssetBuildResult`.

- [ ] **Step 1: Write failing request-identity and snapshot tests**

```python
def test_request_binds_exact_material_and_recipe_identity(
    material_bundle: MaterialBundleResult,
) -> None:
    request = build_mesh_asset_request(
        material_bundle_root=material_bundle.final_directory,
        builder_script=Path("scripts/blender/build_mesh_asset_bundle.py"),
        blender_identity=LOCAL_BLENDER,
    )

    assert request.asset_ids == tuple(sorted(FOOTPRINTS))
    assert request.lod_levels == (0, 1, 2)
    assert request.material_bundle_id == material_bundle.bundle_id
    assert len(request.material_input_registry) == 24
    assert b"/Users/" not in canonical_mesh_asset_build_request_bytes(request)
    assert request.build_id == hashlib.sha256(
        canonical_mesh_asset_build_request_bytes(
            request,
            exclude_build_id=True,
        ),
    ).hexdigest()
```

Add replacement tests for builder-script bytes, Blender version/build hash,
material manifest/map bytes, recipe parameters, asset footprint, and LOD
budget. Add path-redirection and changed-during-snapshot failures.

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_asset_build.py -q
```

Expected: collection fails because `mesh_asset_build` does not exist.

- [ ] **Step 3: Implement strict request/report models**

The canonical request contains:

```python
class MeshAssetRecipe(FrozenModel):
    asset_id: AssetId
    kind: Literal["building", "vegetation", "prop"]
    footprint_m: tuple[float, float, float]
    recipe_id: str
    material_slot_ids: tuple[str, ...]
    lod_triangle_budgets: tuple[int, int, int]

class MeshAssetBuildRequest(FrozenModel):
    schema_version: Literal["nantai.synthetic-village.mesh-asset-build.v1"]
    build_id: Sha256
    synthetic: Literal[True]
    verification_level: Literal["L0"]
    coordinate_encoding: Literal["three-east-up-negative-north"]
    material_bundle_id: Sha256
    material_bundle_manifest_sha256: Sha256
    material_algorithm_id: MaterialAlgorithmId
    material_input_registry: tuple[MaterialInputRecord, ...]
    blender_identity: LocalBlenderIdentity
    builder_script_sha256: Sha256
    recipes: tuple[MeshAssetRecipe, ...]
    lod_levels: tuple[Literal[0], Literal[1], Literal[2]]
```

Canonical bytes contain no machine path or timestamp.

The Blender report contains one row per exact asset/LOD with relative artifact
path, actual byte count, SHA-256, triangles, primitives, used material slots,
and builder-measured local ENU AABB. The report is evidence to cross-check, not
the final bundle manifest.

- [ ] **Step 4: Implement exact material invocation snapshot**

Under a unique work directory:

1. verify the material bundle;
2. write canonical `request.json`;
3. create `material-inputs/` containing exactly the unique requested
   `<sha256>.png` maps;
4. re-read every source map and request byte after the snapshot;
5. invoke Blender with exact arguments:

```text
--background
--factory-startup
--python scripts/blender/build_mesh_asset_bundle.py
--
--request <absolute request snapshot>
--materials <absolute material-inputs directory>
--staging <absolute absent staging directory>
```

No absolute path enters the canonical request.

- [ ] **Step 5: Implement post-Blender cross-check and publication**

After Blender exits:

1. re-verify request and material snapshots;
2. load canonical `build-report.json`;
3. require all 33 exact asset/LOD rows and no extra artifact;
4. independently call `audit_textured_glb(...)`;
5. independently measure ENU bounds with Trimesh;
6. compare report evidence;
7. call `publish_mesh_asset_bundle(...)`;
8. re-load the published bundle and return one stable result object.

Any timeout, nonzero exit, missing report, extra file, report mismatch, audit
failure, or changed input leaves no publication.

- [ ] **Step 6: Run focused gates**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_asset_build.py tests/test_mesh_asset_bundle.py -q
.venv/bin/python -m ruff check pipeline/synthetic_village/mesh_asset_build.py tests/test_mesh_asset_build.py
.venv/bin/python -m compileall -q pipeline/synthetic_village/mesh_asset_build.py
git diff --check -- pipeline/synthetic_village/mesh_asset_build.py tests/test_mesh_asset_build.py
```

Expected: all pass.

- [ ] **Step 7: Commit and push**

```bash
git add pipeline/synthetic_village/mesh_asset_build.py tests/test_mesh_asset_build.py
git commit -m "feat(mesh): orchestrate template bundle builds" \
  -m "Bind material, Blender, recipe, and artifact identities into one fail-closed private publisher.

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/synthetic_village/mesh_asset_build.py tests/test_mesh_asset_build.py
git push origin main
```

### Task 3: Eleven Textured Recipes and Three Real LODs

**Files:**
- Create: `scripts/blender/build_mesh_asset_bundle.py`
- Create: `tests/test_mesh_asset_blender_runtime.py`

**Interfaces:**
- Consumes: Task 2's exact request and material snapshot.
- Produces: 33 GLBs plus canonical `build-report.json` below the absent staging directory.

- [ ] **Step 1: Write failing source and conditional runtime tests**

The source-contract test requires exact reuse markers and recipe closure:

```python
def test_builder_source_uses_proven_textured_canary_primitives() -> None:
    source = BUILDER.read_text(encoding="utf-8")
    for token in (
        "_create_textured_materials",
        "_apply_textured_uvs_and_tangents",
        "_build_building",
        "export_tangents=True",
        "export_yup=True",
        "nv_asset_id",
        "nv_lod",
    ):
        assert token in source
```

The conditional real Blender test builds a hermetic 24-slot material fixture
and asserts 33 distinct GLB rows, three strictly increasing triangle counts per
asset, exact used material closure, finite bounds, and no output above the
staging directory.

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_asset_blender_runtime.py -q
```

Expected: collection/source test fails because the builder does not exist.

- [ ] **Step 3: Implement exact CLI and shared setup**

The script imports proven helpers from
`scripts.blender.build_synthetic_village` and accepts only:

```text
--request <absolute file>
--materials <absolute directory>
--staging <absolute absent directory>
```

It verifies exact request keys, request canonical bytes/build ID, builder
script SHA, all material map bytes, and the exact 24-slot input registry before
creating any staging directory.

- [ ] **Step 4: Implement near/medium/far building recipes**

Use these mappings:

| Asset | Near material/profile | Medium | Far |
|---|---|---|---|
| `house_wood_01` | v2 detailed, weathered timber | four walls/roof/frame/openings | platform/walls/roof |
| `house_wood_02` | v2 detailed, pale plaster + timber | four walls/roof/frame/openings | platform/walls/roof |
| `house_stone_01` | v2 detailed, fieldstone | four walls/roof/openings | platform/walls/roof |
| `house_thatch_01` | v2 detailed, rammed earth + woven-bamboo roof | four walls/thatch roof/openings | platform/walls/roof |
| `house_barn_01` | v2 detailed, dark timber, barn doors | four walls/roof/doors | platform/walls/roof |

Near LOD reuses `_build_building(...)` and swaps only declared asset-specific
material slots after mesh creation. Medium and far use `MeshAssembler` directly
so triangle reductions are real rather than metadata.

All building bases are at local Z `0`, dimensions match `assets/registry.json`,
and the root is at identity.

- [ ] **Step 5: Implement vegetation and prop recipes**

Vegetation:

- `tree_pine_01`: tapered bark trunk, layered conical branch volumes, irregular
  canopy rotation; near/medium/far use decreasing segment/layer counts.
- `tree_broadleaf_01`: trunk plus major branches and five/three/one overlapping
  canopy volumes.
- `tree_bamboo_01`: nine/five/three culms with visible nodes and clustered leaf
  volumes.

Props:

- `stone_wall_01`: irregular interlocking fieldstone courses, then simplified
  courses, then one bevel-free wall mass.
- `stone_lamp_01`: stone base/shaft/cap plus aged-metal lantern cage, simplified
  cage, then silhouette.
- `fence_wood_01`: four/two/two posts with two rails and near-only braces.

Each recipe stays within its registered footprint and declared triangle budget.

- [ ] **Step 6: UV, tangent, export, and report**

For each asset/LOD:

1. clear the factory scene;
2. create only used PBR materials from the exact snapshot;
3. create one identity root with `nv_asset_id`, `nv_lod`, `nv_synthetic=true`;
4. triangulate and generate UVs/tangents with the existing proven helper;
5. export selected root and children to
   `artifacts/<asset-id>/lod<level>.glb`;
6. hash and measure the file;
7. record triangles, primitives, material slots, and Blender ENU bounds.

Write `build-report.json` last with exclusive creation, flush, and fsync. Rename
the complete temporary directory to the requested staging path atomically.

- [ ] **Step 7: Run source and real runtime gates**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_asset_blender_runtime.py -q
.venv/bin/python -m ruff check scripts/blender/build_mesh_asset_bundle.py tests/test_mesh_asset_blender_runtime.py
.venv/bin/python -m compileall -q scripts/blender/build_mesh_asset_bundle.py
git diff --check -- scripts/blender/build_mesh_asset_bundle.py tests/test_mesh_asset_blender_runtime.py
```

Expected: source tests pass; real runtime passes on the installed Blender
4.5.11 macOS runtime and skips with an explicit reason where Blender is absent.

- [ ] **Step 8: Commit and push**

```bash
git add scripts/blender/build_mesh_asset_bundle.py tests/test_mesh_asset_blender_runtime.py
git commit -m "feat(mesh): build eleven textured asset LODs" \
  -m "Add dedicated audited building, vegetation, and prop templates with three real geometry levels.

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- scripts/blender/build_mesh_asset_bundle.py tests/test_mesh_asset_blender_runtime.py
git push origin main
```

### Task 4: CLI, Real L0 Build, Contact Sheet, and Verification

**Files:**
- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_cli.py`
- Create: `docs/verification/2026-07-18-production-mesh-template-bundle.md`

**Interfaces:**
- Consumes: explicit material bundle path and Blender executable.
- Produces: one stable CLI JSON result, one private content-addressed L0 bundle, and one evidence-backed receipt.

- [ ] **Step 1: Write the failing CLI test**

```python
def test_build_mesh_assets_prints_one_stable_json_object(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "_run_mesh_asset_build",
        lambda: fake_run_mesh_asset_build,
    )

    assert cli.main([
        "build-mesh-assets",
        "--material-bundle",
        "/verified/material-bundle",
        "--blender",
        "/Applications/Blender.app/Contents/MacOS/Blender",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "asset_count": 11,
        "bundle_id": "a" * 64,
        "final_directory": "/private/mesh-bundles/" + "a" * 64,
        "lod_count": 33,
        "reused": False,
        "verification_level": "L0",
    }
```

- [ ] **Step 2: Run the CLI test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_synthetic_village_cli.py -q
```

Expected: parser rejects `build-mesh-assets`.

- [ ] **Step 3: Add the explicit CLI**

Require `--material-bundle` and `--blender`; do not select “latest” material
bytes by directory mtime. Add optional work/publication roots and a default
30-minute timeout. Print exactly one sorted JSON object.

- [ ] **Step 4: Build the actual local bundle**

Run with the current verified v2 material bundle:

```bash
.venv/bin/python scripts/synthetic_village.py build-mesh-assets \
  --material-bundle .nantai-studio/synthetic-village/hybrid-v3/material-bundles/b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13 \
  --blender /Applications/Blender.app/Contents/MacOS/Blender \
  --timeout-seconds 1800
```

Record exact runtime, bundle ID, material bundle ID, asset/LOD counts, bytes,
triangle counts, material slots, and ENU AABBs.

- [ ] **Step 5: Render a private contact sheet**

Import all eleven LOD2 assets into one temporary Blender scene using neutral
clear lighting and fixed cameras. Render:

- all-assets overview;
- five-building row;
- three-vegetation row;
- three-prop close view.

Store PNGs only below
`.nantai-studio/verification/2026-07-18-production-mesh-assets/`. Record hashes
in the receipt; do not commit private image bytes.

- [ ] **Step 6: Run the complete repository gate**

Run:

```bash
.venv/bin/python -m pytest -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q pipeline scripts tests
git diff --check
```

Expected: all pass. Record exact counts.

- [ ] **Step 7: Write the honest receipt**

The receipt must state:

```markdown
## Proven

- All eleven registered asset IDs have independently audited textured GLBs.
- Every asset has three strictly increasing geometry LODs.
- Used PBR maps are embedded and bound to the exact material bundle identity.
- Bounds are independently measured from transformed GLB scene geometry.
- Replacing recipe, Blender, material, or payload bytes changes the bundle ID.

## Not yet proven

- Viewer template instancing, LOD hysteresis, and GPU LRU behavior.
- Arbitrary-coordinate textured browser roaming.
- Authoritative Windows x64 byte identity.
- Real reconstruction or measured 3DGS alignment.
```

- [ ] **Step 8: Commit and push CLI and receipt**

```bash
git add scripts/synthetic_village.py tests/test_synthetic_village_cli.py docs/verification/2026-07-18-production-mesh-template-bundle.md
git commit -m "feat(mesh): publish local production templates" \
  -m "Expose the explicit build command and record the real eleven-asset L0 bundle without overstating Viewer readiness.

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- scripts/synthetic_village.py tests/test_synthetic_village_cli.py docs/verification/2026-07-18-production-mesh-template-bundle.md
git push origin main
```

## Self-Review Result

- Slice B spec coverage: all eleven assets, three real LODs, shared PBR
  identity, independent GLB/bounds evidence, private publication, local visual
  review, and explicit remaining limits map to Tasks 1–4.
- No task mutates the Gaussian asset registry or claims PLY-to-mesh conversion.
- No task depends on the collaborator-owned weather test file.
- Viewer activation is intentionally deferred to Slice C so a clean checkout
  cannot advertise private local bytes it does not possess.
