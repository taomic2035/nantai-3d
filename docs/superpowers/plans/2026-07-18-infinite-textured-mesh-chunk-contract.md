# Infinite Textured Mesh Chunk Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first fail-closed vertical slice of arbitrary-coordinate textured mesh streaming: verified mesh-asset bundle evidence, deterministic signed-coordinate chunk manifests, and same-origin Studio routes with ETag/HEAD/304 behavior.

**Architecture:** A focused mesh-asset bundle module verifies immutable audited GLB templates and their bound PBR material identity. A pure mesh-chunk module converts the existing deterministic `MockLayoutGenerator` output into a canonical path-free manifest plus a runtime same-origin URL projection. Studio exposes chunk manifests and immutable template bytes only when the tracked world manifest explicitly opts into an exact, locally verifiable recipe.

**Tech Stack:** Python 3.11+, Pydantic v2, binary glTF 2.0, existing `GlbMaterialAudit`, `MockLayoutGenerator`, `ThreadingHTTPServer`, pytest, Ruff.

## Global Constraints

- Work only on `main`; do not create a branch or worktree.
- Execute inline; do not dispatch subagents because the user asked Codex to proceed independently.
- Stage and commit only explicit paths; never use `git add -A` or `git commit -a`.
- Never stage the pre-existing `tests/test_synthetic_village_weather.py` working-tree change unless its owner has committed it.
- End every Codex-created commit with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Push each verified task commit to `origin/main` before accumulating the next task.
- Treat `.nantai-studio/` as private Git-ignored runtime state.
- Do not modify the current PLY route or reinterpret Gaussian assets as mesh sources.
- Use `MockLayoutGenerator(world_seed).generate_chunk(cx, cy)` as the only layout source.
- Keep synthetic mesh `preview-only`, `synthetic=true`, `metric_alignment=false`, and `real_photo_textures=false`.
- Missing, redirected, malformed, changed, incomplete, or hash-mismatched evidence fails closed.
- Runtime routes are same-origin projection; route strings do not establish asset identity.
- The first terrain recipe is exactly `mock-flat-ground-v1`; it does not claim the finite canary's 120 m relief.
- Every implementation task starts with a failing test and records the observed RED failure.

## Scope Boundary

This plan implements Slice A from
`docs/superpowers/specs/2026-07-18-infinite-textured-mesh-chunks-design.md`.
It proves the protocol and one hermetic audited route. It does not claim visual
completion, the production eleven-template bundle, Viewer mesh rendering, or
browser acceptance. Those remain separate plans for Slices B, C, and D.

## File Map

- Create `pipeline/synthetic_village/mesh_asset_bundle.py`: strict immutable bundle models, bounded verification, canonical bytes, and exact GLB reads.
- Create `tests/test_mesh_asset_bundle.py`: hermetic bundle success, identity, GLB audit, redirection, mutation, and failure tests.
- Modify `pipeline/synthetic_village/glb_material_audit.py`: expose generic
  indexed triangle evidence already recomputed by the independent parser.
- Modify `tests/test_glb_material_audit.py`: lock generic triangle count.
- Modify `tests/test_local_textured_preview.py`: include the new required audit
  evidence in its typed fixture.
- Create `pipeline/synthetic_village/mesh_chunk.py`: deterministic layout-to-manifest conversion, terrain/ribbon records, instance closure, content key, and runtime projection.
- Create `tests/test_mesh_chunk.py`: positive/negative coordinates, deterministic bytes, bundle replacement, LOD, shared-edge terrain, and fail-closed tests.
- Modify `pipeline/studio_server.py`: exact opt-in validation and mesh chunk/template GET/HEAD routes.
- Modify `tests/test_studio_server.py`: signed coordinates, same-origin URLs, ETag/304, immutable GLB serving, traversal, missing bundle, tampering, and bounds tests.
- Create `docs/verification/2026-07-18-infinite-textured-mesh-contract.md`: machine outputs and explicit remaining limits.

---

### Task 1: Immutable Mesh-Asset Bundle Verification

**Files:**
- Create: `pipeline/synthetic_village/mesh_asset_bundle.py`
- Create: `tests/test_mesh_asset_bundle.py`
- Modify: `pipeline/synthetic_village/glb_material_audit.py`
- Modify: `tests/test_glb_material_audit.py`
- Modify: `tests/test_local_textured_preview.py`

**Interfaces:**
- Consumes: `ExpectedGlbMaterial`, `GlbMaterialAudit`, and `audit_textured_glb(...)` from `pipeline.synthetic_village.glb_material_audit`.
- Produces: `MeshTemplateLod`, `MeshAssetRecord`, `MeshAssetBundle`, `PreparedMeshAssetBundle`, `canonical_mesh_asset_bundle_bytes(...)`, `load_mesh_asset_bundle(...)`, and `read_verified_mesh_template_glb(...)`.

- [ ] **Step 1: Write the failing canonical identity and exact-read tests**

Add a hermetic helper that writes one minimal embedded-PBR GLB using the
existing handcrafted GLB fixture pattern, then test the public contract:

```python
def test_bundle_identity_and_exact_template_read(tmp_path: Path) -> None:
    bundle_root, expected_glb = write_mesh_bundle_fixture(tmp_path)

    bundle = load_mesh_asset_bundle(bundle_root)

    assert bundle.schema_version == MESH_ASSET_BUNDLE_SCHEMA
    assert bundle.asset_ids == ("house_wood_01",)
    descriptor = bundle.records[0].lod["2"]
    assert read_verified_mesh_template_glb(
        bundle_root,
        bundle=bundle,
        asset_id="house_wood_01",
        lod=2,
    ) == expected_glb
    assert hashlib.sha256(
        canonical_mesh_asset_bundle_bytes(bundle, exclude_bundle_id=True),
    ).hexdigest() == bundle.bundle_id
```

Also add parameterized failures for a changed GLB byte, changed manifest byte,
symlinked bundle root, symlinked GLB, wrong material bundle ID, duplicate asset
ID, unsorted asset IDs, unknown LOD, external GLB URI, and descriptor count
disagreement.

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
python3 -m pytest tests/test_mesh_asset_bundle.py -q
```

Expected: collection fails with
`ModuleNotFoundError: pipeline.synthetic_village.mesh_asset_bundle`.

- [ ] **Step 3: Implement strict models and canonical bytes**

Create immutable strict Pydantic models with these exact fields:

```python
MESH_ASSET_BUNDLE_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v1"
MESH_ASSET_BUNDLE_MANIFEST = "manifest.json"
MAX_MESH_ASSET_BUNDLE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_MESH_TEMPLATE_GLB_BYTES = 128 * 1024 * 1024

class MeshTemplateLod(FrozenModel):
    glb_object_path: str
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1, le=MAX_MESH_TEMPLATE_GLB_BYTES)
    triangle_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    aabb: Bounds3

class MeshAssetRecord(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: Literal["building", "vegetation", "prop"]
    mesh_algorithm_id: Literal["synthetic-template-mesh-v1"]
    footprint_m: tuple[float, float, float]
    lod: dict[Literal["0", "1", "2"], MeshTemplateLod]
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"

class MeshAssetBundle(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-asset-bundle.v1"
    ] = MESH_ASSET_BUNDLE_SCHEMA
    bundle_id: Sha256
    material_bundle_id: Sha256
    material_bundle_manifest_sha256: Sha256
    synthetic: Literal[True] = True
    real_photo_textures: Literal[False] = False
    build_tool_id: str = Field(min_length=1)
    verification_level: Literal["L0", "L2"]
    material_registry: tuple[ExpectedGlbMaterial, ...] = Field(min_length=1)
    records: tuple[MeshAssetRecord, ...] = Field(min_length=1, max_length=11)
```

Validators require sorted unique asset IDs, exact `objects/<sha>.glb` paths,
finite ordered AABBs, three positive footprint values, and exact canonical
`bundle_id`. The bundle exposes an `asset_ids` property returning its sorted
asset IDs. Material registry slot IDs are sorted and unique; each template LOD
may use only a non-empty subset of that registry.

- [ ] **Step 4: Implement bounded load, independent GLB audit, and exact read**

`load_mesh_asset_bundle(root)` must:

1. reject symlinks/junctions and non-real roots;
2. bounded-read `manifest.json` twice around payload verification;
3. require canonical JSON bytes;
4. verify every descriptor's byte count and SHA-256;
5. call `audit_textured_glb(...)` with the exact material registry subset named
   by the descriptor;
6. compare audited primitive, triangle/material evidence with the descriptor;
7. reject any before/after stat signature change.

`read_verified_mesh_template_glb(...)` repeats the exact descriptor read at
request time and never trusts bytes retained from an earlier verification.

Extend `GlbMaterialAudit` with
`triangle_count: int = Field(ge=1)`. Reuse
`_indexed_triangle_counts_by_mesh(...)` for every audited GLB, require indexed
triangle primitives, and return the sum regardless of whether the optional
70-building audit is requested. Update the one direct typed audit fixture in
`tests/test_local_textured_preview.py`.

- [ ] **Step 5: Run focused and regression tests**

Run:

```bash
python3 -m pytest tests/test_mesh_asset_bundle.py tests/test_glb_material_audit.py tests/test_local_textured_preview.py -q
python3 -m ruff check pipeline/synthetic_village/mesh_asset_bundle.py pipeline/synthetic_village/glb_material_audit.py tests/test_mesh_asset_bundle.py tests/test_glb_material_audit.py tests/test_local_textured_preview.py
python3 -m compileall -q pipeline/synthetic_village/mesh_asset_bundle.py pipeline/synthetic_village/glb_material_audit.py
git diff --check -- pipeline/synthetic_village/mesh_asset_bundle.py pipeline/synthetic_village/glb_material_audit.py tests/test_mesh_asset_bundle.py tests/test_glb_material_audit.py tests/test_local_textured_preview.py
```

Expected: all tests and static checks pass.

- [ ] **Step 6: Commit and push**

```bash
git add pipeline/synthetic_village/mesh_asset_bundle.py pipeline/synthetic_village/glb_material_audit.py tests/test_mesh_asset_bundle.py tests/test_glb_material_audit.py tests/test_local_textured_preview.py
git commit -m "feat(mesh): verify immutable template bundles" \
  -m "Add content-addressed mesh template bundle validation and exact request-time GLB reads.

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/synthetic_village/mesh_asset_bundle.py pipeline/synthetic_village/glb_material_audit.py tests/test_mesh_asset_bundle.py tests/test_glb_material_audit.py tests/test_local_textured_preview.py
git push origin main
```

### Task 2: Deterministic Signed-Coordinate Mesh Chunk Manifest

**Files:**
- Create: `pipeline/synthetic_village/mesh_chunk.py`
- Create: `tests/test_mesh_chunk.py`

**Interfaces:**
- Consumes: `MockLayoutGenerator`, `MeshAssetBundle`, and signed integer `chunk_x`, `chunk_y`, `world_seed`, `lod`.
- Produces: `MeshChunkManifest`, `MeshChunkRuntimeManifest`, `build_mesh_chunk_manifest(...)`, `project_mesh_chunk_runtime(...)`, `canonical_mesh_chunk_bytes(...)`, and `mesh_chunk_content_key(...)`.

- [ ] **Step 1: Write failing deterministic and provenance tests**

```python
def test_negative_chunk_is_deterministic_and_path_free(bundle: MeshAssetBundle) -> None:
    first = build_mesh_chunk_manifest(-2, 3, world_seed=42, bundle=bundle, lod=1)
    second = build_mesh_chunk_manifest(-2, 3, world_seed=42, bundle=bundle, lod=1)

    assert canonical_mesh_chunk_bytes(first) == canonical_mesh_chunk_bytes(second)
    assert first.chunk_id == {"x": -2, "y": 3}
    assert first.world_offset == (-400.0, 600.0, 0.0)
    assert first.synthetic is True
    assert first.geometry_usability == "preview-only"
    assert first.metric_alignment is False
    assert b"/api/" not in canonical_mesh_chunk_bytes(first)
    assert {item.asset_id for item in first.instances} <= set(bundle.asset_ids)
```

Add tests proving:

- `True`, floats, strings, unsafe magnitudes, and unsupported LOD fail;
- canonical bytes are cross-process stable;
- changing bundle ID or material bundle ID changes the content key;
- instance order is stable and every instance ID is unique;
- adjacent chunks have identical shared terrain-edge samples;
- runtime projection emits only exact `/api/world/mesh-assets/...` paths;
- a layout referencing an asset absent from the bundle fails closed;
- the current synthetic truth fields cannot be upgraded through model copying.

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
python3 -m pytest tests/test_mesh_chunk.py -q
```

Expected: collection fails with
`ModuleNotFoundError: pipeline.synthetic_village.mesh_chunk`.

- [ ] **Step 3: Implement the canonical models**

Use strict frozen models and these literal trust fields:

```python
MESH_CHUNK_SCHEMA = "nantai.synthetic-village.mesh-chunk.v1"
LAYOUT_ALGORITHM_ID = "mock-layout-v1"
TERRAIN_ALGORITHM_ID = "mock-flat-ground-v1"
RENDERER_CAPABILITY = "synthetic-textured-mesh-grid"

class MeshInstance(FrozenModel):
    instance_id: str
    asset_id: str
    kind: Literal["building", "vegetation", "prop"]
    local_position: tuple[float, float, float]
    rotation_z_degrees: float
    scale: float
    template_lod: Literal[0, 1, 2]

class MeshChunkManifest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-chunk.v1"
    ] = MESH_CHUNK_SCHEMA
    content_key: Sha256
    renderer_capability: Literal[
        "synthetic-textured-mesh-grid"
    ] = RENDERER_CAPABILITY
    world_seed: int
    chunk_id: ChunkID
    chunk_size_m: Literal[200] = 200
    world_offset: tuple[float, float, float]
    layout_algorithm_id: Literal["mock-layout-v1"] = LAYOUT_ALGORITHM_ID
    layout_sha256: Sha256
    terrain_algorithm_id: Literal[
        "mock-flat-ground-v1"
    ] = TERRAIN_ALGORITHM_ID
    mesh_asset_bundle_id: Sha256
    material_bundle_id: Sha256
    selected_lod: Literal[0, 1, 2]
    terrain: TerrainGrid
    roads: tuple[Ribbon, ...]
    water: tuple[Ribbon, ...]
    instances: tuple[MeshInstance, ...]
    aabb: Bounds3
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"
    coordinate_confidence: Literal["synthetic-layout"] = "synthetic-layout"
    metric_alignment: Literal[False] = False
    real_photo_textures: Literal[False] = False
```

`MeshChunkRuntimeManifest` wraps `chunk: MeshChunkManifest` plus an
`asset_urls` tuple. It does not recompute or replace `content_key`.

- [ ] **Step 4: Implement deterministic terrain, ribbons, and instances**

Generate a world-anchored terrain grid with LOD resolution `{0: 3, 1: 5, 2: 9}`.
The height at every sample is `0.0`; UV anchor coordinates are derived from
absolute ENU X/Y, so adjacent edge samples are identical.

Road and water ribbons retain the layout's sorted IDs, polylines, widths, and
fixed Z offsets `0.04` and `0.02`. Building and prop instances map directly
from layout records. Vegetation cluster instances use a local
`random.Random(_stable_seed(...))` and bounded counts `{0: 2, 1: 5, 2: 12}`;
they never consume process-global randomness.

Compute the layout SHA from canonical `layout.model_dump(mode="json")`. Compute
`content_key` from every canonical field except itself. Compute the chunk AABB
by transforming each selected template AABB through instance scale, Z rotation,
local translation, and world offset, then unioning terrain/ribbon bounds. Do
not use declared registry footprints as measured template bounds.

- [ ] **Step 5: Run focused and related layout tests**

Run:

```bash
python3 -m pytest tests/test_mesh_chunk.py tests/test_mock_layout_assets.py tests/test_asset_pipeline.py tests/test_render_on_demand.py -q
python3 -m ruff check pipeline/synthetic_village/mesh_chunk.py tests/test_mesh_chunk.py
python3 -m compileall -q pipeline/synthetic_village/mesh_chunk.py
git diff --check -- pipeline/synthetic_village/mesh_chunk.py tests/test_mesh_chunk.py
```

Expected: all tests and checks pass without changing existing PLY bytes.

- [ ] **Step 6: Commit and push**

```bash
git add pipeline/synthetic_village/mesh_chunk.py tests/test_mesh_chunk.py
git commit -m "feat(mesh): define deterministic chunk manifests" \
  -m "Derive path-free textured mesh chunk evidence from the existing signed-coordinate layout source.

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/synthetic_village/mesh_chunk.py tests/test_mesh_chunk.py
git push origin main
```

### Task 3: Fail-Closed Studio Mesh Routes

**Files:**
- Modify: `pipeline/studio_server.py`
- Modify: `tests/test_studio_server.py`

**Interfaces:**
- Consumes: a valid `mesh_grid` opt-in inside `web/data/manifest.json`, a verified bundle below `.nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles/<bundle-id>/`, and Task 2 manifest builders.
- Produces: `GET|HEAD /api/world/mesh-chunk/{x}/{y}.json?lod={0|1|2}` and `GET|HEAD /api/world/mesh-assets/{bundle-id}/{asset-id}/lod{0|1|2}.glb`.

- [ ] **Step 1: Write failing endpoint contract tests**

Add a fixture that writes a verified bundle and this exact opt-in:

```python
world["mesh_grid"] = {
    "on_demand": True,
    "url_template": "/api/world/mesh-chunk/{x}/{y}.json",
    "asset_url_template": (
        "/api/world/mesh-assets/{bundle_id}/{asset_id}/lod{lod}.glb"
    ),
    "world_seed": 42,
    "layout_engine": "mock",
    "terrain_algorithm_id": "mock-flat-ground-v1",
    "mesh_asset_bundle_id": bundle.bundle_id,
    "material_bundle_id": bundle.material_bundle_id,
}
```

Test:

```python
status, headers, payload = _request(
    server,
    "GET",
    "/api/world/mesh-chunk/-2/3.json?lod=1",
)
assert status == 200
assert headers["content-type"] == "application/json; charset=utf-8"
assert headers["etag"].startswith('"sha256:')
runtime = json.loads(payload)
assert runtime["chunk"]["chunk_id"] == {"x": -2, "y": 3}
assert all(url.startswith("/api/world/mesh-assets/") for url in runtime["asset_urls"])
```

Add exact GET/HEAD byte equality, `If-None-Match` 304, immutable GLB caching,
invalid query, encoded traversal, unknown asset/LOD, wrong bundle ID, missing
opt-in, missing bundle, symlinked bundle, changed GLB, changed manifest,
material identity mismatch, and WGS84 bounds exhaustion cases.

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
python3 -m pytest tests/test_studio_server.py -q -k mesh_chunk
```

Expected: endpoint tests receive 404 from the current static fallback.

- [ ] **Step 3: Implement exact opt-in and bundle resolution**

Add `_valid_on_demand_mesh_manifest(...)` that accepts only the exact keys and
literal values shown above. Resolve the private bundle directory with:

```python
root / ".nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles" / bundle_id
```

Reject symlinks, path redirection, non-canonical bundle IDs, material bundle
identity disagreement, and any manifest that claims mesh availability without
verifiable bytes.

- [ ] **Step 4: Implement manifest and immutable template routes**

Place both exact route handlers before static fallback. The chunk route loads
and re-verifies the bundle, builds the canonical manifest, projects exact
same-origin URLs, and sends canonical JSON bytes. The asset route loads and
re-verifies the bundle and calls `read_verified_mesh_template_glb(...)`.

Use:

- manifest: `Cache-Control: no-store`;
- immutable GLB:
  `Cache-Control: public, max-age=31536000, immutable`;
- strong ETag: `"sha256:<actual-response-sha256>"`;
- structured `400`, `404`, `409`, `422`, and `500` error codes distinct from
  existing PLY errors.

Neither route writes files or updates the registry.

- [ ] **Step 5: Run endpoint and full Studio gates**

Run:

```bash
python3 -m pytest tests/test_studio_server.py -q
node --test web/studio/*.test.mjs
python3 -m ruff check pipeline/studio_server.py tests/test_studio_server.py
python3 -m compileall -q pipeline/studio_server.py
git diff --check -- pipeline/studio_server.py tests/test_studio_server.py
```

Expected: all Studio Python/Node tests and static checks pass.

- [ ] **Step 6: Commit and push**

```bash
git add pipeline/studio_server.py tests/test_studio_server.py
git commit -m "feat(studio): serve verified mesh chunks" \
  -m "Expose signed-coordinate mesh manifests and immutable template GLBs only from exact verified opt-in evidence.

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/studio_server.py tests/test_studio_server.py
git push origin main
```

### Task 4: Contract Verification Receipt and Full Gate

**Files:**
- Create: `docs/verification/2026-07-18-infinite-textured-mesh-contract.md`

**Interfaces:**
- Consumes: exact outputs and commit IDs from Tasks 1–3.
- Produces: an evidence-backed receipt that says what the vertical slice proves and what Slices B–D still require.

- [ ] **Step 1: Run the complete repository gate**

Run:

```bash
python3 -m pytest -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
python3 -m ruff check .
python3 -m compileall -q pipeline scripts tests
git diff --check
```

Expected: all commands pass; record exact counts rather than writing “green”.

- [ ] **Step 2: Probe cross-process identity**

Run the same `build_mesh_chunk_manifest(-2, 3, world_seed=42, ...)` probe in
two fresh Python processes against the hermetic verified bundle and record:

- canonical manifest SHA-256;
- content key;
- byte count;
- instance count;
- terrain vertex count;
- all projected template URLs.

Expected: both processes produce byte-identical canonical manifests.

- [ ] **Step 3: Probe the live HTTP contract**

Start Studio on loopback, request negative and positive mesh chunk coordinates,
repeat with `HEAD` and `If-None-Match`, fetch one GLB, and record:

- status;
- content type;
- content length;
- ETag;
- cache control;
- response SHA-256;
- zero writes below the project root outside server logs.

Expected: GET/HEAD headers agree, conditional requests return 304, and GLB bytes
match their bundle descriptor.

- [ ] **Step 4: Write the verification receipt**

The receipt must include:

```markdown
## Proven

- Immutable mesh template evidence is independently audited and hash verified.
- Signed-coordinate chunk manifests are deterministic and path-free.
- Same-origin runtime routes support GET, HEAD, strong ETag, and 304.
- Invalid or missing evidence fails closed without a mesh placeholder.

## Not yet proven

- Production-quality geometry for all eleven template asset IDs.
- Viewer rendering, instancing, LOD hysteresis, and GPU LRU behavior.
- Arbitrary-coordinate textured browser roaming.
- Visual quality across all six weather states.
- Measured alignment with real 3DGS.
```

- [ ] **Step 5: Validate, commit, and push the receipt**

```bash
git add docs/verification/2026-07-18-infinite-textured-mesh-contract.md
git diff --cached --check -- docs/verification/2026-07-18-infinite-textured-mesh-contract.md
git commit -m "docs(verification): record mesh chunk contract" \
  -m "Record deterministic manifest, immutable asset route, and complete gate evidence without overstating Viewer readiness.

Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- docs/verification/2026-07-18-infinite-textured-mesh-contract.md
git push origin main
```

## Self-Review Result

- Spec coverage for Slice A: bundle verification, deterministic chunk contract,
  signed coordinates, terrain recipe, same-origin routes, HTTP caching, and
  fail-closed evidence all map to Tasks 1–4.
- Intentionally deferred to separate plans: production eleven-template build,
  Viewer rendering/scheduling, weather acceptance, and measured 3DGS mixing.
- Interface names are consistent across tasks.
- No implementation step depends on the collaborator-owned weather test file.
