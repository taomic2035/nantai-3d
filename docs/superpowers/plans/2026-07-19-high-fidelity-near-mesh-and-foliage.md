# High-Fidelity Near Mesh and Cutout Foliage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish and activate a backward-compatible v2 mesh-template bundle that keeps current LOD0/1 bytes exact, replaces all eleven LOD2 templates with audited high-detail geometry, and renders verified shared alpha-masked foliage textures in the arbitrary-coordinate Viewer.

**Architecture:** Keep the verified v1 bundle/build/audit path byte-stable and add focused v2 modules for bundle identity, foliage atlas derivation, external-texture GLB audit, and LOD2 build orchestration. The canonical mesh chunk stays v1, while a runtime-v2 projection carries exact shared-texture dependencies through Studio to a Viewer resource store that verifies, deduplicates, rebinds, and disposes them by content and rendering semantics.

**Tech Stack:** Python 3.11+, Pydantic v2, Pillow, NumPy, Trimesh 4.4+, Blender 4.5.11 LTS Python API, binary glTF 2.0, Three.js/GLTFLoader, Web Crypto, Node test runner, pytest, Ruff.

## Global Constraints

- Work only on the shared `main`; do not create a branch or worktree.
- Execute inline because the user asked Codex to proceed independently; do not dispatch subagents.
- Stage only explicit paths; never use `git add -A`, `git add .`, or `git commit -a`.
- Never stage the pre-existing `tests/test_synthetic_village_weather.py` WIP unless its owner has committed it.
- End every Codex commit with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Push every verified task commit to `origin/main` before accumulating the next task.
- Preserve v1 canonical bytes, parsers, embedded-only GLB audit behavior, LOD0/1 GLB hashes, asset IDs, footprints, ENU coordinates, instance IDs, and replacement semantics.
- Reused LOD0/1 descriptors retain `synthetic-template-mesh-v1`; only rebuilt LOD2 descriptors use `synthetic-template-mesh-near-v2`.
- LOD2 triangle bands are inclusive: buildings 8,000–15,000; vegetation 6,000–12,000; props 1,000–4,000.
- Foliage uses `alphaMode="MASK"`, `alphaCutoff=0.45`, `doubleSided=true`, `TEXCOORD_0`, and `TANGENT`; `BLEND` is forbidden.
- LOD2 geometry buffers remain embedded; the only allowed image URI is `../textures/<sha256>.png`.
- Texture bytes are cached by SHA-256; GPU textures are cached by `(sha256, role, colour_space, sampler, wrap, flip_y, alpha_mode)`.
- Local macOS output remains `verification_level=L0`, `synthetic=true`, `geometry_usability=preview-only`, and `real_photo_textures=false`.
- Do not add H3 external/AI GLBs, terrain-algorithm changes, interiors, collision, or real-reconstruction trust claims in this plan.
- Unknown, redirected, changed, malformed, under-detail, over-budget, or identity-mismatched evidence fails closed with no stale, box, opaque-canopy, or untextured fallback.
- `.nantai-studio/` remains private and Git-ignored.
- Every implementation task begins with an observed RED test and ends with focused tests, Ruff/compile or Node gates, `git diff --check`, a path-limited commit, and a push.

## File Map

- Create `pipeline/synthetic_village/mesh_asset_bundle_v2.py`: v2 immutable models, canonical bytes, schema-specific verification, reuse checks, shared texture reads, preparation, and publication.
- Modify `pipeline/synthetic_village/mesh_asset_bundle.py`: schema-only dispatch and `MeshAssetBundleAny` exports while keeping v1 canonical code unchanged.
- Create `pipeline/synthetic_village/foliage_atlas.py`: deterministic 1024 px RGBA foliage atlases and provenance.
- Create `pipeline/synthetic_village/glb_shared_texture_audit.py`: exact external-image closure, PNG/alpha/material/topology audit, and in-memory hydration.
- Create `pipeline/synthetic_village/mesh_asset_build_v2.py`: path-free v2 request/report, v1 LOD reuse, Blender invocation, cross-check, and publication orchestration.
- Create `pipeline/synthetic_village/mesh_near_geometry.py`: deterministic semantic component plans and expected triangle bands for eleven LOD2 assets.
- Create `scripts/blender/build_mesh_asset_bundle_v2.py`: realize component plans, author alpha-mask materials, export separate glTF, pack geometry into GLB, and report exact artifacts.
- Create `scripts/blender/render_mesh_asset_comparison.py`: fixed-camera v1/v2 contact-sheet renderer.
- Modify `scripts/synthetic_village.py`: `build-near-mesh-assets` CLI and stable JSON output.
- Modify `pipeline/synthetic_village/mesh_chunk.py`: runtime-v2 dependency projection while preserving canonical mesh chunks.
- Modify `pipeline/studio_server.py`: exact immutable v2 texture route and v1/v2 runtime dispatch.
- Create `web/viewer/verified-mesh-resources.mjs`: verified response, bitmap, semantic GPU texture, template, and reference-count caches.
- Create `web/viewer/frame-performance.mjs`: bounded same-session frame interval sampler and exact median/p95 evidence.
- Modify `web/viewer/mesh-world.mjs`: strict runtime-v1/runtime-v2 validation.
- Modify `web/viewer/main.js`: use the verified v2 loader and expose bounded mesh diagnostics.
- Modify `web/data/manifest.json`: activate the new bundle only after every machine, visual, and performance gate passes.
- Create focused tests next to every new module and update existing v1 regression tests.
- Create `docs/verification/2026-07-19-high-fidelity-near-mesh-and-foliage.md` and `handoff/FEEDBACK-CODEX-011-high-fidelity-near-mesh-h2.md` with actual evidence and remaining limits.

---

### Task 1: Backward-Compatible Bundle-v2 Identity and Dispatch

**Files:**
- Create: `pipeline/synthetic_village/mesh_asset_bundle_v2.py`
- Create: `tests/test_mesh_asset_bundle_v2.py`
- Modify: `pipeline/synthetic_village/mesh_asset_bundle.py:51-64,238-267,603-650`
- Modify: `tests/test_mesh_asset_bundle.py`

**Interfaces:**
- Consumes: existing `MeshAssetBundle`, `MeshTemplateLod`, `Bounds3`, and v1 canonical bytes.
- Produces: `MESH_ASSET_BUNDLE_V2_SCHEMA`, `TextureObjectV2`, `TextureBindingV2`, `MeshTemplateLodV2`, `MeshAssetRecordV2`, `MeshAssetBundleV2`, `MeshAssetBundleAny`, `canonical_mesh_asset_bundle_v2_bytes(...)`, and schema-dispatched `load_mesh_asset_bundle(...)`.

- [ ] **Step 1: Write failing v1-stability and v2-model tests**

Add fixtures that preserve the current v1 canonical bytes and construct one v2
record with exact reused LOD0/1 descriptors plus a shared-texture LOD2:

```python
def test_dispatch_preserves_v1_canonical_bytes(v1_bundle_root: Path) -> None:
    before = (v1_bundle_root / "manifest.json").read_bytes()
    loaded = load_mesh_asset_bundle(v1_bundle_root)
    assert type(loaded) is MeshAssetBundle
    assert canonical_mesh_asset_bundle_bytes(loaded) == before


def test_v2_requires_per_lod_algorithm_and_exact_triangle_bands() -> None:
    bundle = make_v2_bundle_fixture(kind="building", lod2_triangles=8_000)
    assert bundle.records[0].lod["0"].mesh_algorithm_id == (
        "synthetic-template-mesh-v1"
    )
    assert bundle.records[0].lod["2"].mesh_algorithm_id == (
        "synthetic-template-mesh-near-v2"
    )
    with pytest.raises(ValidationError, match="LOD2 triangle band"):
        make_v2_bundle_fixture(kind="building", lod2_triangles=7_999)
```

Also reject: unknown schema, v2 fields fed to v1, LOD0/1 marked v2, LOD2
marked v1, non-empty texture bindings on embedded LODs, empty bindings on
shared LOD2, duplicate texture objects, unsorted bindings, non-content-addressed
paths, and a changed `source_v1_bundle_id`.

- [ ] **Step 2: Run the focused tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py -q
```

Expected: collection fails because `mesh_asset_bundle_v2` and dispatch do not
exist; existing v1 tests remain green when run alone.

- [ ] **Step 3: Add exact v2 frozen models**

Implement these identities in the new module:

```python
MESH_ASSET_BUNDLE_V2_SCHEMA = "nantai.synthetic-village.mesh-asset-bundle.v2"
MESH_NEAR_ALGORITHM_ID = "synthetic-template-mesh-near-v2"
MESH_V1_ALGORITHM_ID = "synthetic-template-mesh-v1"
LOD2_TRIANGLE_BANDS = {
    "building": (8_000, 15_000),
    "vegetation": (6_000, 12_000),
    "prop": (1_000, 4_000),
}


class TextureObjectV2(FrozenModel):
    object_path: str
    sha256: Sha256
    bytes: int = Field(ge=1, le=32 * 1024 * 1024)
    mime_type: Literal["image/png"] = "image/png"
    width: Literal[1024] = 1024
    height: Literal[1024] = 1024


class TextureBindingV2(FrozenModel):
    uri: str
    sha256: Sha256
    role: Literal["base_color", "normal", "orm"]
    colour_space: Literal["srgb", "non-color"]
    material_slot_id: str
    derivation_algorithm_id: str
    min_filter: Literal[9987] = 9987
    mag_filter: Literal[9729] = 9729
    wrap_s: Literal[10497] = 10497
    wrap_t: Literal[10497] = 10497


class MeshTemplateLodV2(FrozenModel):
    glb_object_path: str
    glb_sha256: Sha256
    glb_bytes: int = Field(ge=1, le=MAX_MESH_TEMPLATE_GLB_BYTES)
    triangle_count: int = Field(ge=1)
    primitive_count: int = Field(ge=1)
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    aabb: Bounds3
    mesh_algorithm_id: Literal[
        "synthetic-template-mesh-v1",
        "synthetic-template-mesh-near-v2",
    ]
    recipe_id: str
    texture_storage: Literal["embedded", "shared-content-addressed"]
    texture_bindings: tuple[TextureBindingV2, ...] = ()
```

`TextureObjectV2` validates `object_path == f"textures/{sha256}.png"`.
`TextureBindingV2` validates
`uri == f"../textures/{sha256}.png"`, base colour is sRGB, and normal/ORM are
non-colour. `MeshAssetRecordV2` requires exact keys `"0"`, `"1"`, `"2"`,
strictly increasing triangles, embedded v1 LOD0/1, shared v2 LOD2, and the
kind-specific LOD2 band.

- [ ] **Step 4: Add canonical bundle identity and strict schema dispatch**

Implement:

```python
MeshAssetBundleAny: TypeAlias = MeshAssetBundle | MeshAssetBundleV2


def canonical_mesh_asset_bundle_v2_bytes(
    bundle: MeshAssetBundleV2,
    *,
    exclude_bundle_id: bool = False,
) -> bytes:
    payload = bundle.model_dump(mode="json")
    if exclude_bundle_id:
        payload.pop("bundle_id")
    return _canonical_json_bytes(payload)


def _manifest_schema(raw: bytes) -> str:
    payload = json.loads(
        raw,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not str:
        raise MeshAssetBundleError("mesh asset bundle schema is missing")
    return payload["schema_version"]
```

Rename the current cached loader internally to `_load_mesh_asset_bundle_v1`
without changing its validation or canonical serialization. The exported
`load_mesh_asset_bundle` reads the bounded manifest once, dispatches only exact
v1/v2 schema strings, and delegates to schema-specific cached loaders.

- [ ] **Step 5: Run v1 and v2 gates**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py \
  tests/test_mesh_chunk.py \
  tests/test_studio_server.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/mesh_asset_bundle.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/mesh_asset_bundle.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py
git diff --check -- \
  pipeline/synthetic_village/mesh_asset_bundle.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py
```

Expected: all pass, including byte-exact v1 manifest fixtures.

- [ ] **Step 6: Commit and push**

```bash
git add \
  pipeline/synthetic_village/mesh_asset_bundle.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py
git commit -m "feat(mesh): add backward-compatible bundle v2" \
  -m "Keep v1 canonical bytes exact while binding per-LOD algorithms and shared texture identities in a separate v2 contract." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/mesh_asset_bundle.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py
git push origin main
```

### Task 2: Deterministic Foliage Cutout Atlases

**Files:**
- Create: `pipeline/synthetic_village/foliage_atlas.py`
- Create: `tests/test_foliage_atlas.py`

**Interfaces:**
- Consumes: verified `DerivedMaterialBundle` records for the three foliage slots and an explicit real output directory.
- Produces: `FOLIAGE_ATLAS_ALGORITHM_ID`, `FoliageAtlasObject`, `FoliageAtlasRecord`, `FoliageAtlasSet`, `PreparedFoliageAtlasSet`, `canonical_foliage_atlas_set_bytes(...)`, and `build_foliage_atlas_set(...)`.

- [ ] **Step 1: Write deterministic, alpha, and provenance tests**

```python
FOLIAGE_SLOTS = (
    "material-bamboo-leaf-01",
    "material-broadleaf-canopy-01",
    "material-orchard-leaf-01",
)


def test_atlas_is_deterministic_rgba_and_nonuniform(
    material_bundle_root: Path,
    tmp_path: Path,
) -> None:
    first = build_foliage_atlas_set(material_bundle_root, tmp_path / "first")
    second = build_foliage_atlas_set(material_bundle_root, tmp_path / "second")
    assert canonical_foliage_atlas_set_bytes(first.manifest) == (
        canonical_foliage_atlas_set_bytes(second.manifest)
    )
    for slot_id in FOLIAGE_SLOTS:
        record = first.manifest.by_slot[slot_id]
        image = Image.open(first.root / record.base_color.object_path)
        assert image.mode == "RGBA"
        assert image.size == (1024, 1024)
        alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
        assert set(np.unique(alpha)) == {0, 255}
        assert 0.20 <= np.count_nonzero(alpha) / alpha.size <= 0.55
```

Add failures for changed source map bytes, missing slots, redirected roots,
wrong 1024 px dimensions, non-PNG input, changed-during-read, alpha coverage
outside the band, path leakage in canonical bytes, and output overwrite.

- [ ] **Step 2: Run the tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_foliage_atlas.py -q
```

Expected: collection fails because `foliage_atlas` does not exist.

- [ ] **Step 3: Implement the exact atlas identity and mask recipes**

Add:

```python
FOLIAGE_ATLAS_ALGORITHM_ID = "deterministic-foliage-cutout-v1"
ATLAS_SIZE_PX = 1024
ATLAS_GRID = 4
ALPHA_CUTOFF = 0.45
FOLIAGE_SHAPES = {
    "material-bamboo-leaf-01": ("lanceolate", 0.20, 0.36),
    "material-broadleaf-canopy-01": ("ovate-serrated", 0.28, 0.52),
    "material-orchard-leaf-01": ("elliptic", 0.24, 0.46),
}
```

For each 256 px cell, derive crop origin and angle from
`sha256(f"{slot_id}:{cell_index}".encode())`. Evaluate the binary mask at 4x
resolution with these normalized inequalities, then downsample alpha with
nearest-neighbour so it remains exactly `{0,255}`:

```python
def _inside_leaf(shape: str, x: float, y: float) -> bool:
    if shape == "lanceolate":
        return abs(y) <= (1.0 - abs(x)) ** 1.65 * 0.34
    if shape == "elliptic":
        return x * x + (y / 0.62) ** 2 <= 1.0
    radius = 1.0 - abs(x) ** 1.7
    serration = 0.055 * math.sin((math.atan2(y, x) + math.pi) * 18.0)
    return abs(y) <= max(0.0, radius * 0.58 + serration)
```

Sample colour, normal, and ORM from the exact same source crop and atlas
layout. Dilate RGB underneath transparent pixels by eight pixels using
nearest covered texels. Save with:

```python
image.save(
    path,
    format="PNG",
    compress_level=9,
    optimize=False,
)
```

Canonical records bind every input map SHA, Pillow version, algorithm ID,
layout parameters, output SHA/bytes/dimensions, and measured alpha coverage;
they contain no path or timestamp.

Return paths only in a non-canonical wrapper:

```python
@dataclass(frozen=True)
class PreparedFoliageAtlasSet:
    root: Path
    manifest: FoliageAtlasSet
```

- [ ] **Step 4: Run deterministic and cross-process gates**

Run:

```bash
.venv/bin/python -m pytest tests/test_foliage_atlas.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/foliage_atlas.py \
  tests/test_foliage_atlas.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/foliage_atlas.py
git diff --check -- \
  pipeline/synthetic_village/foliage_atlas.py \
  tests/test_foliage_atlas.py
```

Expected: all pass; two separate subprocess builds have identical manifests
and PNG SHA-256 values.

- [ ] **Step 5: Commit and push**

```bash
git add \
  pipeline/synthetic_village/foliage_atlas.py \
  tests/test_foliage_atlas.py
git commit -m "feat(mesh): derive deterministic foliage atlases" \
  -m "Generate content-addressed RGBA leaf atlases from verified synthetic PBR maps with exact alpha and provenance evidence." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/foliage_atlas.py \
  tests/test_foliage_atlas.py
git push origin main
```

### Task 3: Shared-Texture GLB Audit and Bundle-v2 Publication

**Files:**
- Create: `pipeline/synthetic_village/glb_shared_texture_audit.py`
- Create: `tests/test_glb_shared_texture_audit.py`
- Modify: `pipeline/synthetic_village/glb_material_audit.py:1093-1190`
- Modify: `tests/test_glb_material_audit.py`
- Modify: `pipeline/synthetic_village/mesh_asset_bundle_v2.py`
- Modify: `tests/test_mesh_asset_bundle_v2.py`

**Interfaces:**
- Consumes: original GLB bytes, exact `TextureObjectV2` bytes, ordered `TextureBindingV2` closure, and expected material identities.
- Produces: `SharedTextureGlbAudit`, `audit_shared_textured_glb(...)`, `hydrate_shared_texture_glb(...)`, `read_verified_mesh_texture(...)`, `prepare_mesh_asset_bundle_v2(...)`, and `publish_mesh_asset_bundle_v2(...)`.

- [ ] **Step 1: Write adversarial external-texture audit tests**

Build a minimal GLB whose geometry buffer is embedded and whose three images
are:

```json
[
  {"uri": "../textures/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png", "mimeType": "image/png"},
  {"uri": "../textures/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png", "mimeType": "image/png"},
  {"uri": "../textures/cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.png", "mimeType": "image/png"}
]
```

Assert a valid opaque material passes and a valid foliage material passes only
with:

```python
{
    "alphaMode": "MASK",
    "alphaCutoff": 0.45,
    "doubleSided": True,
}
```

Add failures for: absolute/HTTP/data/file URI, `..` outside the exact one-level
shape, query/fragment, external `.bin`, missing/extra/duplicate dependency,
SHA/byte/MIME/dimension mismatch, redirect, changed-during-read, `BLEND`,
wrong cutoff, single-sided foliage, no alpha channel, uniform alpha, bad
coverage, missing UV/tangent, non-indexed/non-triangle primitive, degenerate or
duplicate faces, unused mesh/material, non-finite vertex, and footprint
overflow.

- [ ] **Step 2: Run tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_glb_material_audit.py \
  tests/test_glb_shared_texture_audit.py \
  tests/test_mesh_asset_bundle_v2.py -q
```

Expected: new tests fail because shared-texture hydration/audit/publication do
not exist; all old embedded-only cases still pass alone.

- [ ] **Step 3: Split byte audit from path audit without relaxing defaults**

Refactor the existing entry point into:

```python
def audit_textured_glb_bytes(
    payload: bytes,
    expected_materials: tuple[ExpectedGlbMaterial, ...],
    expected_building_geometry: ExpectedBuildingGeometry | None = None,
    expected_surface_realism: ExpectedSurfaceRealism | None = None,
) -> GlbMaterialAudit:
    ...


def audit_textured_glb(path: Path, expected_materials: tuple[...], ...) -> GlbMaterialAudit:
    payload = _read_stable_file(path, maximum_bytes=MAX_GLB_BYTES)
    return audit_textured_glb_bytes(payload, expected_materials, ...)
```

`audit_textured_glb_bytes` retains the exact existing rule that any URI fails.
Keep all current errors and evidence fields stable.

- [ ] **Step 4: Hydrate only the verified in-memory closure**

Implement `hydrate_shared_texture_glb` by parsing the original JSON/BIN chunks,
matching the exact declared image URIs, appending verified PNG bytes at
four-byte boundaries, replacing each URI with a new image bufferView, and
rebuilding canonical GLB header/JSON/BIN chunks. No filesystem or network
resolver is accepted.

```python
def audit_shared_textured_glb(
    path: Path,
    *,
    expected_materials: tuple[ExpectedGlbMaterial, ...],
    texture_root: Path,
    bindings: tuple[TextureBindingV2, ...],
    objects: tuple[TextureObjectV2, ...],
    kind: Literal["building", "vegetation", "prop"],
    footprint_m: tuple[float, float, float],
) -> SharedTextureGlbAudit:
    original = read_stable_glb(path)
    dependencies = read_exact_texture_closure(texture_root, bindings, objects)
    hydrated = hydrate_shared_texture_glb(original, dependencies)
    core = audit_textured_glb_bytes(hydrated, expected_materials)
    topology = audit_mesh_topology(original, footprint_m=footprint_m)
    alpha = audit_foliage_alpha(original, dependencies) if kind == "vegetation" else None
    return SharedTextureGlbAudit.from_evidence(original, core, topology, alpha)
```

The returned GLB SHA/byte count describe the original external-texture GLB,
not the hydrated audit derivative.

- [ ] **Step 5: Implement absent-only v2 preparation/publication**

`prepare_mesh_asset_bundle_v2(...)` receives a verified source v1 bundle,
eleven rebuilt LOD2 sources, and exact texture objects. It copies exact v1
LOD0/1 bytes, writes each unique LOD2 GLB to `objects/<sha>.glb`, each unique
PNG to `textures/<sha>.png`, constructs sorted records, independently audits
every object, and emits canonical `manifest.json`.

`publish_mesh_asset_bundle_v2(...)` uses the existing project lock and
durability backends, publishes to `<publication-root>/<bundle-id>` only when
absent, verifies an existing destination byte-for-byte, and never mutates v1.

- [ ] **Step 6: Run audit/publication gates**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_glb_material_audit.py \
  tests/test_glb_shared_texture_audit.py \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/glb_shared_texture_audit.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py \
  tests/test_glb_material_audit.py \
  tests/test_glb_shared_texture_audit.py \
  tests/test_mesh_asset_bundle_v2.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/glb_shared_texture_audit.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py
git diff --check -- \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/glb_shared_texture_audit.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py \
  tests/test_glb_material_audit.py \
  tests/test_glb_shared_texture_audit.py \
  tests/test_mesh_asset_bundle_v2.py
```

Expected: all pass and the embedded-only audit still rejects every URI.

- [ ] **Step 7: Commit and push**

```bash
git add \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/glb_shared_texture_audit.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py \
  tests/test_glb_material_audit.py \
  tests/test_glb_shared_texture_audit.py \
  tests/test_mesh_asset_bundle_v2.py
git commit -m "feat(mesh): audit shared texture GLBs" \
  -m "Verify exact relative PNG closures in memory while preserving the embedded-only default and absent-only bundle publication." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/glb_material_audit.py \
  pipeline/synthetic_village/glb_shared_texture_audit.py \
  pipeline/synthetic_village/mesh_asset_bundle_v2.py \
  tests/test_glb_material_audit.py \
  tests/test_glb_shared_texture_audit.py \
  tests/test_mesh_asset_bundle_v2.py
git push origin main
```

### Task 4: Path-Free V2 Build Request and Reuse Orchestrator

**Files:**
- Create: `pipeline/synthetic_village/mesh_asset_build_v2.py`
- Create: `tests/test_mesh_asset_build_v2.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_cli.py`

**Interfaces:**
- Consumes: exact source v1 bundle root, exact material bundle root, verified foliage atlas set, Blender identity, builder script, asset registry, work root, and publication root.
- Produces: `MeshAssetBuildRequestV2`, `MeshAssetBuildReportV2`, `build_mesh_asset_request_v2(...)`, `run_mesh_asset_build_v2(...)`, and CLI `build-near-mesh-assets`.

- [ ] **Step 1: Write request/reuse/report/CLI RED tests**

```python
def test_request_binds_v1_reuse_and_only_rebuilds_lod2(
    v1_bundle_root: Path,
    material_bundle_root: Path,
    foliage_atlas_set: PreparedFoliageAtlasSet,
) -> None:
    request = build_mesh_asset_request_v2(
        source_v1_bundle_root=v1_bundle_root,
        material_bundle_root=material_bundle_root,
        foliage_atlas_set=foliage_atlas_set,
        builder_script=Path("scripts/blender/build_mesh_asset_bundle_v2.py"),
        blender_identity=LOCAL_BLENDER,
    )
    assert request.source_v1_bundle_id == load_mesh_asset_bundle(v1_bundle_root).bundle_id
    assert request.lod_levels_to_build == (2,)
    assert all(row.recipe_id.endswith("-near-v2") for row in request.recipes)
    assert b"/Users/" not in canonical_mesh_asset_build_request_v2_bytes(request)
```

Add identity changes for builder bytes, Blender build hash, v1 bundle bytes,
material map bytes, atlas bytes, asset registry, recipe parameters, cutoff,
triangle bands, and sampler state. Reject incomplete/extra report rows,
LOD0/1 output rows, path leakage, redirected inputs, changed snapshots,
builder timeout, non-zero exit, malformed report, mismatched SHA/count/bounds,
and partial publication.

CLI test:

```python
assert set(json.loads(result.stdout)) == {
    "build_id",
    "bundle_id",
    "bundle_root",
    "lod2_asset_count",
    "reused_lod_count",
    "synthetic",
    "verification_level",
}
```

- [ ] **Step 2: Run tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mesh_asset_build_v2.py \
  tests/test_synthetic_village_cli.py -q
```

Expected: collection/CLI failures because v2 orchestration is absent.

- [ ] **Step 3: Define exact recipes and request/report models**

Use these fixed bands and recipe suffixes:

```python
NEAR_RECIPE_IDS = {
    "fence_wood_01": "weathered-timber-fence-near-v2",
    "house_barn_01": "dark-timber-barn-near-v2",
    "house_stone_01": "fieldstone-house-near-v2",
    "house_thatch_01": "rammed-earth-thatch-house-near-v2",
    "house_wood_01": "weathered-timber-house-near-v2",
    "house_wood_02": "plaster-timber-house-near-v2",
    "stone_lamp_01": "stone-metal-lamp-near-v2",
    "stone_wall_01": "dry-stone-wall-near-v2",
    "tree_bamboo_01": "clustered-bamboo-near-v2",
    "tree_broadleaf_01": "humid-broadleaf-near-v2",
    "tree_pine_01": "layered-pine-near-v2",
}
```

`MeshAssetBuildRequestV2` uses schema
`nantai.synthetic-village.mesh-asset-build.v2`, exact
`lod_levels_to_build=(2,)`, exact v1 per-asset LOD0/1 descriptors, full
material/atlas registries, and no path/timestamp. `MeshAssetBuildReportV2`
contains exactly eleven sorted LOD2 rows.

- [ ] **Step 4: Implement bounded Blender orchestration and cross-check**

Snapshot exact inputs into a unique work directory, write the canonical
request, invoke:

```text
${BLENDER_EXECUTABLE}
--background
--factory-startup
--python scripts/blender/build_mesh_asset_bundle_v2.py
--
--request ${REQUEST_PATH}
--material-root ${MATERIAL_SNAPSHOT_ROOT}
--atlas-root ${ATLAS_SNAPSHOT_ROOT}
--output-root ${BUILD_OUTPUT_ROOT}
--report ${BUILD_REPORT_PATH}
```

After exit, independently read every GLB/texture/report row, call
`audit_shared_textured_glb`, compare all Blender-reported fields, pass the
sources plus v1 bundle to `prepare_mesh_asset_bundle_v2`, publish, reload, and
verify the final bundle. Cleanup only the owned work directory.

- [ ] **Step 5: Add the exact CLI**

Add parser options:

```text
build-near-mesh-assets
--source-v1-bundle-root PATH
--material-bundle-root PATH
--blender PATH
--work-root PATH
--publication-root PATH
--timeout-seconds INTEGER
```

Default Blender is
`/Applications/Blender.app/Contents/MacOS/Blender`; default work/publication
roots stay under `.nantai-studio/synthetic-village/hybrid-v3/`.

- [ ] **Step 6: Run orchestration gates**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mesh_asset_build_v2.py \
  tests/test_synthetic_village_cli.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/synthetic_village.py \
  tests/test_mesh_asset_build_v2.py \
  tests/test_synthetic_village_cli.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/synthetic_village.py
git diff --check -- \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/synthetic_village.py \
  tests/test_mesh_asset_build_v2.py \
  tests/test_synthetic_village_cli.py
```

Expected: all pass with a fake Blender process and fake audited artifacts.

- [ ] **Step 7: Commit and push**

```bash
git add \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/synthetic_village.py \
  tests/test_mesh_asset_build_v2.py \
  tests/test_synthetic_village_cli.py
git commit -m "feat(mesh): orchestrate near bundle builds" \
  -m "Bind exact v1 reuse, LOD2 recipes, shared textures, Blender identity, and publication into one path-free v2 request." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/synthetic_village.py \
  tests/test_mesh_asset_build_v2.py \
  tests/test_synthetic_village_cli.py
git push origin main
```

### Task 5: Deterministic Semantic Geometry Plans

**Files:**
- Create: `pipeline/synthetic_village/mesh_near_geometry.py`
- Create: `tests/test_mesh_near_geometry.py`

**Interfaces:**
- Consumes: exact `asset_id`, kind, and registry footprint.
- Produces: `NearComponent`, `NearGeometryPlan`, `build_near_geometry_plan(...)`, and `canonical_near_geometry_plan_bytes(...)` for the Blender builder.

- [ ] **Step 1: Write component, determinism, footprint, and detail tests**

```python
@pytest.mark.parametrize("asset_id", EXPECTED_ASSET_IDS)
def test_near_plan_is_deterministic_and_inside_footprint(asset_id: str) -> None:
    first = build_near_geometry_plan(asset_id, FOOTPRINTS[asset_id])
    second = build_near_geometry_plan(asset_id, FOOTPRINTS[asset_id])
    assert canonical_near_geometry_plan_bytes(first) == (
        canonical_near_geometry_plan_bytes(second)
    )
    assert first.aabb.min[0] >= -FOOTPRINTS[asset_id][0] / 2
    assert first.aabb.max[0] <= FOOTPRINTS[asset_id][0] / 2
    assert first.aabb.min[1] >= -FOOTPRINTS[asset_id][1] / 2
    assert first.aabb.max[1] <= FOOTPRINTS[asset_id][1] / 2
    assert first.aabb.min[2] == 0
```

Require component classes:

- every building: `foundation`, `wall`, `roof-shell`, `roof-detail`, `eave`,
  `door-opening`, `window-opening`, `frame`, and asset-specific components;
- every vegetation asset: `trunk-or-culm`, `branch`, and `leaf-card`;
- bamboo: at least 12 culms plus `culm-node`;
- every prop: asset-specific bevelled/layered components.

Require planned triangles inside the same LOD2 bands as the bundle model and
stable, sorted, unique component IDs. Every building plan also declares exact
`covered_elevations=("east", "north", "south", "west")`, with at least one
opening or structural detail on each elevation.

- [ ] **Step 2: Run tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_near_geometry.py -q
```

Expected: collection fails because the geometry planner does not exist.

- [ ] **Step 3: Implement exact component models and stable variation**

```python
class NearComponent(FrozenModel):
    component_id: str
    primitive: Literal[
        "box", "bevelled-box", "cylinder", "roof-tile", "thatch-strip",
        "branch", "leaf-card", "stone-block", "frame",
    ]
    material_slot_id: str
    position: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation_degrees: tuple[float, float, float]
    planned_triangles: int = Field(ge=2)
    parent_id: str | None = None


def stable_unit(asset_id: str, component_id: str, channel: str) -> float:
    digest = hashlib.sha256(
        f"{asset_id}:{component_id}:{channel}".encode("utf-8"),
    ).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)
```

The plan contains only semantic geometry and transformations; no machine path,
Blender object ID, timestamp, or unseeded randomness.

- [ ] **Step 4: Use exact detail counts**

Freeze these lower-detail counts so planned triangles cannot be padded invisibly:

```python
BUILDING_DETAIL = {
    "roof_tile_columns": 24,
    "roof_tile_rows": 12,
    "window_count_min": 6,
    "door_count_min": 2,
    "frame_members_per_opening": 4,
}
VEGETATION_DETAIL = {
    "tree_bamboo_01": {"culms": 12, "branches": 96, "leaf_cards": 3_000},
    "tree_broadleaf_01": {"trunks": 1, "branches": 180, "leaf_cards": 3_000},
    "tree_pine_01": {"trunks": 1, "branches": 240, "leaf_cards": 3_000},
}
PROP_DETAIL = {
    "fence_wood_01": {"posts": 12, "rails": 22, "braces": 10},
    "stone_lamp_01": {"bevelled_parts": 48, "cage_members": 12},
    "stone_wall_01": {"stone_blocks": 96, "cap_stones": 18},
}
```

Adjust primitive segment counts inside the planner so every total lands within
its exact kind band. The planner rejects an out-of-band result rather than
adding filler components.

- [ ] **Step 5: Run planner gates**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_near_geometry.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/mesh_near_geometry.py \
  tests/test_mesh_near_geometry.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/mesh_near_geometry.py
git diff --check -- \
  pipeline/synthetic_village/mesh_near_geometry.py \
  tests/test_mesh_near_geometry.py
```

Expected: all eleven plans are deterministic, within footprints, semantically
complete, and in-band.

- [ ] **Step 6: Commit and push**

```bash
git add \
  pipeline/synthetic_village/mesh_near_geometry.py \
  tests/test_mesh_near_geometry.py
git commit -m "feat(mesh): plan semantic near geometry" \
  -m "Define deterministic construction, vegetation, and prop components that meet visible-detail and triangle-band contracts." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/mesh_near_geometry.py \
  tests/test_mesh_near_geometry.py
git push origin main
```

### Task 6: Blender LOD2 Realization and Real Bundle Build

**Files:**
- Create: `scripts/blender/build_mesh_asset_bundle_v2.py`
- Create: `tests/test_mesh_asset_blender_runtime_v2.py`
- Modify: `pipeline/synthetic_village/mesh_asset_build_v2.py`
- Modify: `tests/test_mesh_asset_build_v2.py`

**Interfaces:**
- Consumes: canonical `MeshAssetBuildRequestV2`, material/atlas snapshots, and `NearGeometryPlan` records.
- Produces: exactly eleven LOD2 external-texture GLBs, exact shared PNG objects, `MeshAssetBuildReportV2`, and a real private v2 bundle.

- [ ] **Step 1: Write source and real-Blender RED tests**

Source tests require the script to:

- validate exact v2 schema/build ID/recipe closure before scene creation;
- consume `build_near_geometry_plan`;
- use a Blender preview transparency mode only for foliage, then emit exact
  glTF `MASK`, cutoff `0.45`, and double-sided JSON during deterministic
  packing;
- export `GLTF_SEPARATE`;
- pack geometry into GLB while leaving only exact shared PNG URIs;
- report actual SHA, bytes, triangles, primitives, bounds, materials, and
  dependencies.

Conditional real test:

```python
@pytest.mark.skipif(not BLENDER.is_file(), reason="local Mac Blender is absent")
def test_real_v2_build_publishes_eleven_audited_near_assets(
    real_material_bundle_root: Path,
    real_v1_bundle_root: Path,
    tmp_path: Path,
) -> None:
    result = run_mesh_asset_build_v2(
        repo_root=ROOT,
        source_v1_bundle_root=real_v1_bundle_root,
        material_bundle_root=real_material_bundle_root,
        blender_executable=BLENDER,
        builder_script=Path("scripts/blender/build_mesh_asset_bundle_v2.py"),
        work_root=tmp_path / "work",
        publication_root=tmp_path / "published",
        timeout_seconds=3_600,
    )
    bundle = load_mesh_asset_bundle(result.bundle.final_directory)
    assert isinstance(bundle, MeshAssetBundleV2)
    assert len(bundle.records) == 11
    assert all(
        bundle.records[index].lod[str(level)].glb_sha256
        == load_mesh_asset_bundle(real_v1_bundle_root).records[index].lod[
            str(level)
        ].glb_sha256
        for index in range(11)
        for level in (0, 1)
    )
```

Also assert exact triangle bands, no opaque ellipsoid canopy component,
foliage material mode, shared texture closure, and second-build byte identity.

- [ ] **Step 2: Run tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mesh_asset_build_v2.py \
  tests/test_mesh_asset_blender_runtime_v2.py -q
```

Expected: source tests fail because the builder is absent; the real runtime
test fails rather than skipping on this machine because Blender 4.5.11 exists.

- [ ] **Step 3: Implement deterministic Blender primitive realization**

Use one ordered collection per asset and one object per semantic component.
Map primitives exactly:

```python
PRIMITIVE_BUILDERS = {
    "box": build_box,
    "bevelled-box": build_bevelled_box,
    "cylinder": build_tapered_cylinder,
    "roof-tile": build_curved_roof_tile,
    "thatch-strip": build_thatch_strip,
    "branch": build_oriented_branch,
    "leaf-card": build_leaf_card,
    "stone-block": build_bevelled_stone,
    "frame": build_frame_member,
}
```

Every builder applies explicit transforms, triangulates deterministically,
applies modifiers before export, assigns exactly one declared material, writes
`nv_component_id` and `nv_part_class` extras, and never calls Blender noise or
Python `random`.

`build_leaf_card` produces one indexed quad with UVs inside its atlas cell,
oriented along its parent branch. `build_curved_roof_tile` produces a visible
12-triangle tile with overlap and no zero-area face. Bevel widths are capped at
10% of the component's smallest dimension.

- [ ] **Step 4: Implement exact PBR and alpha material authoring**

Opaque materials use the verified source maps and
`alphaMode="OPAQUE"`. Foliage materials use the derived RGBA atlas and:

```python
material.surface_render_method = "DITHERED"  # Blender preview only
material.use_transparency_overlap = False
material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
material["nv_gltf_alpha_mode"] = "MASK"
material["nv_gltf_alpha_cutoff"] = 0.45
material["nv_gltf_double_sided"] = True
```

After export, the packer overwrites glTF material JSON to exact
`alphaMode`, `alphaCutoff`, and `doubleSided` values and re-audits it; Blender
preview properties are not trusted as glTF evidence.

- [ ] **Step 5: Pack separate glTF without embedding repeated images**

Read the `.gltf`, verify exactly one local geometry `.bin`, embed that binary
as the GLB BIN chunk, remove the buffer URI, and replace each exact local PNG
URI with `../textures/<sha256>.png`. Reject extra files, multiple buffers,
non-PNG images, data URIs, missing samplers, and path escape. JSON serialization
uses sorted keys, compact separators, UTF-8, and four-byte space padding.

- [ ] **Step 6: Run source, real Blender, and repeatability gates**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mesh_asset_build_v2.py \
  tests/test_mesh_asset_blender_runtime_v2.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/blender/build_mesh_asset_bundle_v2.py \
  tests/test_mesh_asset_build_v2.py \
  tests/test_mesh_asset_blender_runtime_v2.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/blender/build_mesh_asset_bundle_v2.py
git diff --check -- \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/blender/build_mesh_asset_bundle_v2.py \
  tests/test_mesh_asset_build_v2.py \
  tests/test_mesh_asset_blender_runtime_v2.py
```

Expected: 11 audited LOD2 artifacts; all bands, material modes, external
closures, LOD0/1 reuse, bounds, and repeatability checks pass.

- [ ] **Step 7: Build the real private L0 bundle**

Run:

```bash
.venv/bin/python scripts/synthetic_village.py build-near-mesh-assets \
  --source-v1-bundle-root \
  .nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles/2fbf8692ca8b1442c72177dc1954fb81959933bafd46623c1817002fc732c3e8 \
  --material-bundle-root \
  .nantai-studio/synthetic-village/hybrid-v3/material-bundles/b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13 \
  --blender /Applications/Blender.app/Contents/MacOS/Blender \
  --work-root .nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-work \
  --publication-root .nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles \
  --timeout-seconds 3600 \
  | tee .nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-build-result.json
```

Expected: one JSON object reporting 11 LOD2 assets, 22 reused LODs,
`verification_level=L0`, and the new bundle ID/root.

- [ ] **Step 8: Commit and push**

```bash
git add \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/blender/build_mesh_asset_bundle_v2.py \
  tests/test_mesh_asset_build_v2.py \
  tests/test_mesh_asset_blender_runtime_v2.py
git commit -m "feat(mesh): build high-detail LOD2 templates" \
  -m "Realize audited construction details, branches, cutout foliage, and bevelled props in a deterministic Blender v2 build." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/mesh_asset_build_v2.py \
  scripts/blender/build_mesh_asset_bundle_v2.py \
  tests/test_mesh_asset_build_v2.py \
  tests/test_mesh_asset_blender_runtime_v2.py
git push origin main
```

### Task 7: Runtime-v2 Chunk Dependency Projection

**Files:**
- Modify: `pipeline/synthetic_village/mesh_chunk.py:40-42,186-263,718-814`
- Modify: `tests/test_mesh_chunk.py`

**Interfaces:**
- Consumes: `MeshAssetBundleAny`, canonical `MeshChunkManifest`, and material bundle.
- Produces: `MeshTextureRuntimeUrl`, `MeshAssetRuntimeUrlV2`, `MeshChunkRuntimeManifestV2`, and schema-dispatched `project_mesh_chunk_runtime(...)`.

- [ ] **Step 1: Write v1-stability and exact runtime-v2 tests**

```python
def test_v1_runtime_bytes_remain_exact(v1_runtime_fixture) -> None:
    before = canonical_mesh_chunk_runtime_bytes(v1_runtime_fixture)
    reloaded = MeshChunkRuntimeManifest.model_validate_json(before)
    assert canonical_mesh_chunk_runtime_bytes(reloaded) == before


def test_v2_runtime_projects_exact_lod2_texture_closure(
    v2_bundle: MeshAssetBundleV2,
    material_bundle: DerivedMaterialBundle,
) -> None:
    chunk = build_mesh_chunk_manifest(0, 0, world_seed=42, bundle=v2_bundle, lod=2)
    runtime = project_mesh_chunk_runtime(
        chunk,
        bundle=v2_bundle,
        material_bundle=material_bundle,
    )
    assert runtime.schema_version == "nantai.synthetic-village.mesh-chunk-runtime.v2"
    for asset in runtime.asset_urls:
        assert asset.texture_dependencies == tuple(sorted(
            asset.texture_dependencies,
            key=lambda row: (row.sha256, row.role, row.material_slot_id),
        ))
```

Reject cross-version bundle/runtime pairs, derived dependency URLs, missing or
extra bindings, duplicate semantic keys, wrong role/colour space, wrong
bundle ID, and non-exact route shapes. LOD0/1 v2-bundle runtime records have
empty dependency closures because their GLBs remain embedded.

- [ ] **Step 2: Run tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_chunk.py -q
```

Expected: new runtime-v2 assertions fail; current v1 tests remain green.

- [ ] **Step 3: Add exact runtime models**

```python
class MeshTextureRuntimeUrl(FrozenModel):
    url: str
    sha256: Sha256
    bytes: int = Field(ge=1)
    role: Literal["base_color", "normal", "orm"]
    colour_space: Literal["srgb", "non-color"]
    material_slot_id: str
    derivation_algorithm_id: str
    min_filter: Literal[9987]
    mag_filter: Literal[9729]
    wrap_s: Literal[10497]
    wrap_t: Literal[10497]


class MeshAssetRuntimeUrlV2(MeshAssetRuntimeUrl):
    texture_dependencies: tuple[MeshTextureRuntimeUrl, ...]


class MeshChunkRuntimeManifestV2(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.mesh-chunk-runtime.v2"
    ]
    chunk: MeshChunkManifest
    asset_urls: tuple[MeshAssetRuntimeUrlV2, ...]
    surface_materials: tuple[SurfaceMaterialRuntime, ...]
```

Every dependency URL is exactly
`/api/world/mesh-assets/{bundle_id}/textures/{sha256}.png`; it is projected
from the verified binding/object pair, never assembled from a material name.

- [ ] **Step 4: Run Python gates**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mesh_chunk.py \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/mesh_chunk.py \
  tests/test_mesh_chunk.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/mesh_chunk.py
git diff --check -- \
  pipeline/synthetic_village/mesh_chunk.py \
  tests/test_mesh_chunk.py
```

Expected: v1 bytes and v2 closure tests pass.

- [ ] **Step 5: Commit and push**

```bash
git add pipeline/synthetic_village/mesh_chunk.py tests/test_mesh_chunk.py
git commit -m "feat(mesh): project runtime v2 dependencies" \
  -m "Carry exact shared texture evidence to the Viewer without changing canonical chunk coordinates or provenance." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/synthetic_village/mesh_chunk.py tests/test_mesh_chunk.py
git push origin main
```

### Task 8: Strict Studio Texture-Object Route

**Files:**
- Modify: `pipeline/studio_server.py:68-82,1470-1570,1917-2210`
- Modify: `tests/test_studio_server.py`

**Interfaces:**
- Consumes: active v1/v2 mesh bundle, runtime projection, and `read_verified_mesh_texture(...)`.
- Produces: GET/HEAD
  `/api/world/mesh-assets/{bundle_id}/textures/{sha256}.png` with immutable exact bytes and runtime-v2 JSON.

- [ ] **Step 1: Write route and fail-closed RED tests**

Test GET, HEAD, ETag/304, exact `image/png`, content length, immutable cache,
`nosniff`, and byte identity. Reject:

- a texture not in the active bundle;
- a valid object from an inactive bundle;
- uppercase/short SHA, extra slash, query, fragment, encoded traversal;
- v1 bundle texture requests;
- redirected/missing/changed/corrupt bundle;
- a v2 chunk when one dependency is invalid.

```python
status, headers, payload = _request(
    server,
    "GET",
    f"/api/world/mesh-assets/{bundle.bundle_id}/textures/{texture.sha256}.png",
)
assert status == 200
assert headers["content-type"] == "image/png"
assert headers["etag"] == f'"sha256:{texture.sha256}"'
assert headers["cache-control"] == "public, max-age=31536000, immutable"
assert headers["x-content-type-options"] == "nosniff"
assert hashlib.sha256(payload).hexdigest() == texture.sha256
```

- [ ] **Step 2: Run tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_studio_server.py -q
```

Expected: texture route returns 404 and v2 runtime projection is unsupported.

- [ ] **Step 3: Add exact route before the asset-GLB route**

Match only:

```python
r"/api/world/mesh-assets/([0-9a-f]{64})/textures/([0-9a-f]{64})\.png"
```

Require the requested bundle to equal the active `mesh_grid` bundle, require a
loaded `MeshAssetBundleV2`, call `read_verified_mesh_texture`, re-hash the
returned bytes, and use the existing `_send_bytes`/`_send_not_modified`
helpers. Do not accept a query or redirect.

Update `_load_active_mesh_asset_bundle` and mesh-chunk handler to accept exact
v1/v2 unions and project the matching runtime schema. Keep the existing GLB
route byte-for-byte compatible for v1 and LOD0/1.

- [ ] **Step 4: Run server and cross-layer gates**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_studio_server.py \
  tests/test_mesh_chunk.py \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py -q
.venv/bin/python -m ruff check \
  pipeline/studio_server.py \
  tests/test_studio_server.py
.venv/bin/python -m compileall -q pipeline/studio_server.py
git diff --check -- pipeline/studio_server.py tests/test_studio_server.py
```

Expected: all pass, including v1 route regressions.

- [ ] **Step 5: Commit and push**

```bash
git add pipeline/studio_server.py tests/test_studio_server.py
git commit -m "feat(studio): serve verified mesh textures" \
  -m "Expose only active-bundle content-addressed PNG dependencies and project matching runtime-v2 evidence." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- pipeline/studio_server.py tests/test_studio_server.py
git push origin main
```

### Task 9: Viewer Runtime-v2 Contract Validation

**Files:**
- Modify: `web/viewer/mesh-world.mjs:1-105,282-365`
- Modify: `web/viewer/mesh-world.test.mjs`

**Interfaces:**
- Consumes: world manifest plus runtime-v1 or runtime-v2 JSON.
- Produces: strict `validateMeshChunkRuntime(...)` that returns only a matching,
  exact dependency closure.

- [ ] **Step 1: Write valid and adversarial runtime-v2 tests**

Extend the fixture with one LOD2 descriptor:

```javascript
texture_dependencies: [{
  url: `/api/world/mesh-assets/${BUNDLE_ID}/textures/${MAP_SHA}.png`,
  sha256: MAP_SHA,
  bytes: 4096,
  role: 'base_color',
  colour_space: 'srgb',
  material_slot_id: 'material-broadleaf-canopy-01',
  derivation_algorithm_id: 'deterministic-foliage-cutout-v1',
  min_filter: 9987,
  mag_filter: 9729,
  wrap_s: 10497,
  wrap_t: 10497,
}]
```

Reject cross-origin/absolute/derived/escaped/query routes, wrong bundle hash,
wrong role/colour space, unsafe byte counts, duplicates, unsorted closure,
wrong sampler, dependencies on runtime v1, and missing dependencies for v2
LOD2. Assert v1 fixture behavior remains exact.

- [ ] **Step 2: Run Node tests to observe RED**

Run:

```bash
node --test web/viewer/mesh-world.test.mjs
```

Expected: runtime-v2 fixture is rejected because only v1 is accepted.

- [ ] **Step 3: Implement version-paired validation**

Add:

```javascript
const RUNTIME_V1 = 'nantai.synthetic-village.mesh-chunk-runtime.v1';
const RUNTIME_V2 = 'nantai.synthetic-village.mesh-chunk-runtime.v2';

function expectedTexturePath(bundleId, sha256) {
  return `/api/world/mesh-assets/${bundleId}/textures/${sha256}.png`;
}
```

Validate exact keys for every runtime object. Runtime v1 accepts the current
asset descriptor only. Runtime v2 requires `texture_dependencies`; LOD0/1
require an empty array and LOD2 requires the exact sorted non-empty closure.
Do not normalize, sort, or repair incoming data.

- [ ] **Step 4: Run all Viewer unit gates**

Run:

```bash
node --test web/viewer/*.test.mjs
git diff --check -- \
  web/viewer/mesh-world.mjs \
  web/viewer/mesh-world.test.mjs
```

Expected: all Viewer tests pass.

- [ ] **Step 5: Commit and push**

```bash
git add web/viewer/mesh-world.mjs web/viewer/mesh-world.test.mjs
git commit -m "feat(viewer): validate mesh runtime v2" \
  -m "Bind every shared texture dependency to the active bundle, asset LOD, role, and exact same-origin route." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- web/viewer/mesh-world.mjs web/viewer/mesh-world.test.mjs
git push origin main
```

### Task 10: Verified Viewer Resource Store and GLTF Rebinding

**Files:**
- Create: `web/viewer/verified-mesh-resources.mjs`
- Create: `web/viewer/verified-mesh-resources.test.mjs`
- Create: `web/viewer/frame-performance.mjs`
- Create: `web/viewer/frame-performance.test.mjs`
- Modify: `web/viewer/main.js:12-15,141-145,565-687,1863-1910,2220-2290`
- Modify: `web/viewer/index-contract.test.mjs`

**Interfaces:**
- Consumes: validated runtime-v2 asset descriptor, injected `THREE`,
  `GLTFLoader`, `fetch`, Web Crypto digest, and `createImageBitmap`.
- Produces: `semanticTextureKey(...)`, `createVerifiedMeshResourceStore(...)`,
  `store.loadTemplate(...)`, `store.releaseTemplate(...)`,
  `store.diagnostics()`, and `createFrameIntervalSampler(...)`.

- [ ] **Step 1: Write pure cache/fetch/error RED tests**

Use fakes to assert:

- one network request and one bitmap decode for repeated SHA;
- distinct GPU textures for distinct role/colour-space/sampler/alpha keys;
- one GPU texture for the exact same semantic key;
- redirect, changed final URL, non-PNG, byte mismatch, SHA mismatch, and
  missing dependency reject before GLTF parse;
- GLTF material missing/extra/substituted maps rejects after parse;
- transient GLTF textures are disposed before return;
- refcounts never become negative and dispose only at zero;
- diagnostics expose counts, not paths or raw bytes.
- frame sampling discards the first 10 seconds, retains at most 3,600
  intervals, and calculates exact median and nearest-rank p95.

```javascript
assert.equal(
  semanticTextureKey(dependency, { alphaMode: 'MASK', flipY: false }),
  [
    dependency.sha256,
    dependency.role,
    dependency.colour_space,
    '9987:9729:10497:10497',
    'false',
    'MASK',
  ].join(':'),
);
```

- [ ] **Step 2: Run tests to observe RED**

Run:

```bash
node --test web/viewer/verified-mesh-resources.test.mjs
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement verified byte and bitmap caches**

`fetchExactObject` checks `response.ok`, `response.redirected === false`,
`response.url === new URL(descriptor.url, location).href`, exact
content-type, byte count, and Web Crypto SHA. Cache its promise by SHA only
after the descriptor agrees with an existing byte record.

Create bitmaps with:

```javascript
createImageBitmap(new Blob([bytes], { type: 'image/png' }), {
  imageOrientation: 'flipY',
  colorSpaceConversion: 'none',
  premultiplyAlpha: 'none',
});
```

Cache that promise by SHA. A failed promise removes only its own cache entry.

- [ ] **Step 4: Implement template-local LoadingManager and semantic rebinding**

Prefetch and verify the entire dependency closure. Create one object URL per
verified SHA and a template-local `THREE.LoadingManager` whose URL modifier
accepts only the exact GLB-declared relative URI and returns the matching
object URL. Parse the GLB, traverse every mesh/material, match material extras
to the descriptor closure, replace `map`, `normalMap`, `roughnessMap`, and
`metalnessMap` with globally cached semantic textures, then assert:

```javascript
material.map !== null
material.normalMap !== null
material.roughnessMap !== null
material.metalnessMap !== null
material.alphaTest === 0.45  // foliage only
material.side === THREE.DoubleSide  // foliage only
material.transparent === false
```

Dispose all transient loader-created textures and revoke template-local object
URLs in `finally`. Do not cache a template until post-parse closure validation
passes.

- [ ] **Step 5: Integrate with chunk cloning, weather, and diagnostics**

Replace `meshAssetCache` and `loadVerifiedMeshAsset` in `main.js` with one
store. Templates remain immutable; chunk instances clone object transforms
while sharing verified geometry/material resources. Weather continues to
clone only material scalar state and must preserve maps, alpha test, side, and
semantic texture identity.

Expose bounded diagnostics through the existing bridge state:

```javascript
mesh_resources: {
  byte_objects,
  decoded_bitmaps,
  gpu_textures,
  templates,
  network_fetches,
  bitmap_decodes,
  gpu_texture_creations,
  active_chunks,
  pending_chunks,
  failed_chunks,
}
```

No URL, local path, or raw hash list is exposed.

- [ ] **Step 6: Add the bounded frame sampler**

Implement:

```javascript
export function createFrameIntervalSampler({
  warmupMs = 10_000,
  maximumSamples = 3_600,
} = {}) {
  const intervals = [];
  let startedAt = null;
  let previousAt = null;
  return {
    record(nowMs) {
      if (!Number.isFinite(nowMs)) throw new TypeError('frame time must be finite');
      if (startedAt === null) startedAt = nowMs;
      if (previousAt !== null && nowMs - startedAt >= warmupMs) {
        intervals.push(nowMs - previousAt);
        if (intervals.length > maximumSamples) intervals.shift();
      }
      previousAt = nowMs;
    },
    snapshot() {
      const sorted = [...intervals].sort((a, b) => a - b);
      const medianIndex = Math.floor((sorted.length - 1) / 2);
      const p95Index = Math.max(0, Math.ceil(sorted.length * 0.95) - 1);
      return {
        sample_count: sorted.length,
        median_ms: sorted.length ? sorted[medianIndex] : null,
        p95_ms: sorted.length ? sorted[p95Index] : null,
      };
    },
  };
}
```

Call `record(performance.now())` once per `animate()` callback. Bridge
diagnostics include the sampler snapshot plus
`renderer.info.memory.geometries` and `renderer.info.memory.textures`.

- [ ] **Step 7: Run all Viewer gates**

Run:

```bash
node --test \
  web/viewer/verified-mesh-resources.test.mjs \
  web/viewer/frame-performance.test.mjs \
  web/viewer/mesh-world.test.mjs \
  web/viewer/mesh-weather.test.mjs \
  web/viewer/bridge.test.mjs \
  web/viewer/index-contract.test.mjs
node --test web/viewer/*.test.mjs
git diff --check -- \
  web/viewer/verified-mesh-resources.mjs \
  web/viewer/verified-mesh-resources.test.mjs \
  web/viewer/frame-performance.mjs \
  web/viewer/frame-performance.test.mjs \
  web/viewer/main.js \
  web/viewer/index-contract.test.mjs
```

Expected: all pass; no v1 behavior or weather identity changes.

- [ ] **Step 8: Commit and push**

```bash
git add \
  web/viewer/verified-mesh-resources.mjs \
  web/viewer/verified-mesh-resources.test.mjs \
  web/viewer/frame-performance.mjs \
  web/viewer/frame-performance.test.mjs \
  web/viewer/main.js \
  web/viewer/index-contract.test.mjs
git commit -m "feat(viewer): verify and share near textures" \
  -m "Deduplicate immutable bytes and bitmaps, bind GPU textures by rendering semantics, and fail closed after GLTF parsing." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  web/viewer/verified-mesh-resources.mjs \
  web/viewer/verified-mesh-resources.test.mjs \
  web/viewer/frame-performance.mjs \
  web/viewer/frame-performance.test.mjs \
  web/viewer/main.js \
  web/viewer/index-contract.test.mjs
git push origin main
```

### Task 11: Fixed-Camera V1/V2 Contact Sheet

**Files:**
- Create: `scripts/blender/render_mesh_asset_comparison.py`
- Create: `tests/test_mesh_asset_comparison_runtime.py`

**Interfaces:**
- Consumes: current v1 bundle and new verified v2 bundle.
- Produces: fixed-camera v1/v2 contact sheet plus canonical comparison report.

- [ ] **Step 1: Write the comparison-render runtime test**

The Blender script accepts exact v1/v2 bundle roots, imports all eleven LOD2
assets, places v1 and v2 in paired cells, and renders one 4K PNG under a neutral
fixed camera/light rig. Validate its JSON report with:

```python
assert report.schema_version == "nantai.synthetic-village.mesh-near-comparison.v1"
assert report.v1_bundle_id == v1_bundle.bundle_id
assert report.v2_bundle_id == v2_bundle.bundle_id
assert report.asset_ids == EXPECTED_ASSET_IDS
assert len(report.camera_matrix) == 16
assert all(math.isfinite(value) for value in report.camera_matrix)
assert re.fullmatch(r"[0-9a-f]{64}", report.image_sha256)
assert report.image_bytes > 0
assert report.synthetic is True
assert report.trust_effect == "none-visual-review-only"
```

Test exact asset closure, stable camera, finite bounds, no missing material,
same-machine repeat image SHA, redirected input rejection, and changed bundle
rejection.

- [ ] **Step 2: Run comparison tests to observe RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_asset_comparison_runtime.py -q
```

Expected: collection/source test fails because the comparison renderer is
absent.

- [ ] **Step 3: Implement and run the real contact sheet**

Run after tests pass:

```bash
V2_BUNDLE_ID="$(
  .venv/bin/python -c \
  'import json; print(json.load(open(".nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-build-result.json"))["bundle_id"])'
)"
V2_BUNDLE_ROOT=".nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles/$V2_BUNDLE_ID"
export V2_BUNDLE_ID V2_BUNDLE_ROOT
/Applications/Blender.app/Contents/MacOS/Blender \
  --background \
  --factory-startup \
  --python scripts/blender/render_mesh_asset_comparison.py \
  -- \
  --v1-bundle \
  .nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles/2fbf8692ca8b1442c72177dc1954fb81959933bafd46623c1817002fc732c3e8 \
  --v2-bundle "$V2_BUNDLE_ROOT" \
  --output \
  .nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-evidence/contact-sheet.png \
  --report \
  .nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-evidence/contact-sheet.json
```

Expected: all eleven side-by-side pairs are visible; no alpha halo/card
rectangle, floating part, facade-only back, texture stretch, or footprint
clip is present. Record any visible defect as a failed gate and return to Task
5 or Task 6 before activation.

- [ ] **Step 4: Run focused comparison gates**

Run:

```bash
.venv/bin/python -m pytest tests/test_mesh_asset_comparison_runtime.py -q
.venv/bin/python -m ruff check \
  scripts/blender/render_mesh_asset_comparison.py \
  tests/test_mesh_asset_comparison_runtime.py
.venv/bin/python -m compileall -q \
  scripts/blender/render_mesh_asset_comparison.py
git diff --check -- \
  scripts/blender/render_mesh_asset_comparison.py \
  tests/test_mesh_asset_comparison_runtime.py
```

Expected: source and real Blender comparison tests pass.

- [ ] **Step 5: Commit and push**

```bash
git add \
  scripts/blender/render_mesh_asset_comparison.py \
  tests/test_mesh_asset_comparison_runtime.py
git commit -m "test(mesh): render near fidelity comparison" \
  -m "Produce a fixed-camera v1/v2 contact sheet with immutable bundle and image evidence for all eleven assets." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  scripts/blender/render_mesh_asset_comparison.py \
  tests/test_mesh_asset_comparison_runtime.py
git push origin main
```

### Task 12: Browser Performance, Evidence, and Guarded Activation

**Files:**
- Create: `docs/verification/2026-07-19-high-fidelity-near-mesh-and-foliage.md`
- Create: `handoff/FEEDBACK-CODEX-011-high-fidelity-near-mesh-h2.md`
- Modify only after every gate passes: `web/data/manifest.json`
- Modify only after every gate passes: `web/viewer/mesh-world.test.mjs`

**Interfaces:**
- Consumes: verified v1/v2 bundles, comparison report, Studio server, Viewer
  bridge diagnostics, and all six weather states.
- Produces: same-machine browser evidence, guarded default activation,
  verification report, and honest Opus handoff status.

- [ ] **Step 1: Run complete pre-browser machine gates**

Run:

```bash
.venv/bin/python -m pytest tests -q
node --test web/viewer/*.test.mjs
.venv/bin/python -m ruff check \
  pipeline scripts tests
.venv/bin/python -m compileall -q \
  pipeline scripts
git diff --check
```

Expected: all pass; the collaborator weather WIP remains unstaged and is not
included in any commit.

- [ ] **Step 2: Start Studio and capture v1 baseline**

Run:

```bash
V2_BUNDLE_ID="$(
  .venv/bin/python -c \
  'import json; print(json.load(open(".nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-build-result.json"))["bundle_id"])'
)"
V2_BUNDLE_ROOT=".nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles/$V2_BUNDLE_ID"
export V2_BUNDLE_ID V2_BUNDLE_ROOT
.venv/bin/python -m pipeline.studio_server \
  --root /Users/taomic/vibecoding/nantai-3d \
  --host 127.0.0.1 \
  --port 8767
```

Open `http://127.0.0.1:8767/web/viewer/` in the in-app browser. With the v1
manifest, after 10 seconds record from the bridge:

- viewport and device-pixel ratio;
- active/pending/failed chunks;
- 60-second frame intervals during one pedestrian orbit;
- `renderer.info.memory` geometry/textures;
- network fetch, bitmap decode, and GPU texture creation counts;
- console warning/error count.

Store the canonical JSON evidence under
`.nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-evidence/v1-browser.json`.

- [ ] **Step 3: Select v2 in the working tree and capture the acceptance run**

The Studio server reads only tracked `web/data/manifest.json`; an unused
private copy would not test the real path. Use `apply_patch` to change only
the current v1 `mesh_asset_bundle_id` to the exact `$V2_BUNDLE_ID`, leave the
change uncommitted during the run, reload, and prove:

- nine active chunks at default camera;
- teleport to `(123456, -98765, 12)` and `(-123456, 98765, 12)` yields nine
  active chunks each with zero failed/permanently pending chunks;
- all six weather IDs apply and returning to clear restores base material
  state;
- clear/rain/night close-ups retain masked foliage and exact texture identity;
- one network fetch and bitmap decode per SHA;
- one GPU texture per exact semantic key;
- stable memory counts after 60 seconds;
- median frame interval `<=33.3ms`, p95 `<=50ms`, and median regression
  `<=30%` against v1 in the same viewport;
- zero console warnings/errors.

Store the canonical JSON evidence and screenshots under the same private
evidence root. If a gate fails, keep v1 default, record the actual failed
metric, restore the exact v1 bundle ID with `apply_patch`, fix the owning task,
and repeat both same-machine runs. If every gate passes, leave the one-line
bundle-ID edit for Step 4.

- [ ] **Step 4: Activate only the passing v2 identity**

After every gate passes, confirm that the uncommitted
`mesh_asset_bundle_id` in `web/data/manifest.json` equals the exact 64-hex
`bundle_id` printed in
`.nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-build-result.json`.
Keep `material_bundle_id`, world seed, terrain algorithm, URL templates,
coordinates, and layout unchanged. Update `mesh-world.test.mjs` to pin that
same exact identity and assert the default still selects `mesh`.

- [ ] **Step 5: Write evidence and handoff feedback**

The verification document records:

- commit and bundle IDs;
- exact 11 x 3 GLB and shared texture counts/bytes;
- per-asset LOD triangles/bounds/material slots;
- exact LOD0/1 hash equality;
- atlas coverage and material mode;
- repeated-build identities;
- contact-sheet SHA/path and visual review result;
- v1/v2 browser metrics, two teleports, nine chunks, six weather states, and
  console result;
- `synthetic=true`, `preview-only`, `real_photo_textures=false`;
- remaining terrain transitions, procedural repetition, no interiors, and no
  real capture geometry.

The handoff file uses What/Why/Tradeoff/Open/Next and asks Opus to review v2
canonical dispatch, texture resolver closure, and default-activation evidence.
If Opus remains unavailable, mark review status honestly as pending rather
than inventing feedback.

- [ ] **Step 6: Run final post-activation gates**

Run:

```bash
.venv/bin/python -m pytest tests -q
node --test web/viewer/*.test.mjs
.venv/bin/python -m ruff check pipeline scripts tests
.venv/bin/python -m compileall -q pipeline scripts
git diff --check -- \
  web/data/manifest.json \
  web/viewer/mesh-world.test.mjs \
  docs/verification/2026-07-19-high-fidelity-near-mesh-and-foliage.md \
  handoff/FEEDBACK-CODEX-011-high-fidelity-near-mesh-h2.md
git status --short --branch
git rev-list --left-right --count HEAD...origin/main
```

Expected: all pass; only the known collaborator weather WIP may remain;
divergence is `0 0` before the final commit.

- [ ] **Step 7: Commit and push activation/evidence**

```bash
git add \
  web/data/manifest.json \
  web/viewer/mesh-world.test.mjs \
  docs/verification/2026-07-19-high-fidelity-near-mesh-and-foliage.md \
  handoff/FEEDBACK-CODEX-011-high-fidelity-near-mesh-h2.md
git commit -m "feat(mesh): activate high-fidelity near world" \
  -m "Switch the infinite Viewer to the fully audited v2 bundle only after visual, cache, coordinate, weather, and performance gates pass." \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  web/data/manifest.json \
  web/viewer/mesh-world.test.mjs \
  docs/verification/2026-07-19-high-fidelity-near-mesh-and-foliage.md \
  handoff/FEEDBACK-CODEX-011-high-fidelity-near-mesh-h2.md
git push origin main
```

## Final Completion Check

H1 + H2 is complete only when current evidence proves all of the following
together:

1. v1 manifests and embedded-only audits remain byte/behavior compatible.
2. all 22 reused LOD0/1 object hashes equal the source v1 bundle.
3. all eleven rebuilt LOD2 assets pass geometry, topology, material, alpha,
   footprint, triangle-band, and repeatability gates.
4. Studio serves only active-bundle verified textures and matching runtime v2.
5. Viewer rejects corrupt/missing/substituted dependencies, deduplicates exact
   resources, and preserves weather alpha/material identity.
6. default, far-positive, and far-negative coordinates each show nine coherent
   chunks.
7. all six weather states remain reversible.
8. contact sheet and close browser views show materially improved buildings,
   vegetation, and props without listed defects.
9. measured same-machine performance meets every activation threshold.
10. default activation, verification report, and pending/received Opus handoff
    state are committed and pushed to `origin/main`.

These gates complete only the approved H1 + H2 slice. They do not prove the
broader real-capture realism objective complete; the verification report must
keep terrain quality, procedural repetition, interiors, and real
reconstruction as explicit remaining work.
