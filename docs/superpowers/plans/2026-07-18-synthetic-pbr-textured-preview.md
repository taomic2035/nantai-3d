# Synthetic PBR Textured Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume all 24 verified synthetic material sources as embedded PBR textures, produce a structurally audited textured GLB, expose an honest macOS local preview, and make the six Viewer weather states relight textured mesh without pretending to relight 3DGS.

**Architecture:** A focused material-bundle module derives immutable base-color, tangent-space normal, and ORM maps from the existing `VisualSourceManifest`. A schema-v2 canary request snapshots those content-addressed maps into a private Blender invocation, the Blender builder creates deterministic UVs/tangents and embeds the maps, and an independent GLB parser gates publication. A separate L0 macOS preview is served through a strict same-origin Studio route; only the existing locked Windows x64 canary may publish the tracked release.

**Tech Stack:** Python 3.11, Pydantic v2, Pillow 10.4+, NumPy 1.26+, Blender 4.5.11 LTS Python API, binary glTF 2.0, Three.js 0.180.0, native ES modules, pytest, Node test runner, Ruff.

## Global Constraints

- Work only on `main`; do not create a branch or worktree.
- Do not dispatch subagents; execute this plan inline because the user asked Codex to proceed independently.
- Stage and commit only explicit paths; never use `git add -A` or `git commit -a`.
- Never stage the pre-existing `tests/test_synthetic_village_weather.py` working-tree change unless its owner has committed it.
- End every Codex-created commit with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Push each verified task commit to `origin/main` before accumulating the next task.
- Treat `.nantai-studio/` as private, Git-ignored runtime state; never stage its payloads.
- Reuse the existing 24 material visual slots and `VisualSourceManifest`; do not create a competing source registry.
- Missing, malformed, changed, redirected, incomplete, or hash-mismatched texture input fails closed with no flat-color fallback in a textured build.
- Preserve legacy schema-v1 flat canary behavior and truth labels; do not reinterpret `design-reference-only` or `pbr-material-v1` as proof that image bytes were consumed.
- New textured material records use `usage_mode=runtime-material-source-v1` and `implementation=derived-pbr-material-v1`.
- Keep canonical requests and manifests path-free. Runtime paths are ephemeral transport and never content identity.
- Keep `geometry_usability=preview-only`, `synthetic=true`, and `real_photo_textures=false`.
- The locked authoritative canary remains Blender 4.5.11 Windows x64 with runtime build hash `4db51e9d1e1e`.
- macOS output is always `verification_level=L0`, `authoritative=false`, and `local-preview-only`.
- Viewer mesh weather may relight cloned mesh materials; Viewer 3DGS weather remains an atmospheric overlay with `splat_relighting=false`.
- Every implementation task starts with a failing test, records the observed RED failure, makes the smallest complete change, and reruns focused plus relevant regression tests.

## Scope Boundary

This plan completes the material subsystem described by
`docs/superpowers/specs/2026-07-18-synthetic-pbr-material-consumption-design.md`:

1. immutable 24-slot derived material bundles;
2. path-independent schema-v2 Blender inputs;
3. deterministic UV/tangent and embedded PBR export;
4. independent binary-GLB material auditing;
5. honest private macOS preview;
6. mode-aware six-weather mesh relighting;
7. authoritative Windows release gate.

It does not claim the whole product goal complete. After this plan, textured
on-demand mesh chunks, grounded walk/fly navigation, and measured alignment of
real 3DGS pockets remain separate follow-on work.

## File Map

- Create `pipeline/synthetic_village/material_bundle.py`: bundle models, deterministic map derivation, verification, and absent-only publication.
- Create `tests/synthetic_material_fixtures.py`: hermetic 24-slot generated source pack used by clean-checkout tests.
- Create `tests/test_synthetic_village_material_bundle.py`: derivation, identity, failure, durability, and replacement tests.
- Modify `scripts/synthetic_village.py`: `build-materials`, `build-textured-preview`, and authoritative textured-canary commands.
- Create `pipeline/synthetic_village/glb_material_audit.py`: independent GLB container and PBR binding audit.
- Create `tests/test_glb_material_audit.py`: handcrafted positive and adversarial GLB fixtures.
- Modify `pipeline/synthetic_village/canary.py`: additive schema-v2 request/report types, exact material invocation snapshot, and authoritative Windows runner.
- Modify `tests/test_synthetic_village_canary.py`: schema-v2 identity, path-free transport, report, and runner tests.
- Modify `scripts/blender/build_synthetic_village.py`: schema-v2 validation, verified image loading, deterministic UV/tangent generation, material nodes, and v2 report evidence.
- Modify `tests/test_synthetic_village_blender_runtime.py`: real locked-runtime PBR build assertions where the platform gate is available.
- Create `pipeline/synthetic_village/local_textured_preview.py`: separate macOS L0 request, tool identity, private preview manifest, and publisher.
- Create `tests/test_local_textured_preview.py`: platform separation, truth labels, and publication tests.
- Modify `pipeline/studio_server.py`: strict same-origin private local-preview manifest/GLB route.
- Modify `tests/test_studio_server.py`: route containment, hash, HEAD, ETag, and traversal tests.
- Modify `web/viewer/model-preview.mjs`: legacy v1 plus textured v2 manifest validation and disclosure.
- Modify `web/viewer/model-preview.test.mjs`: v2 truth and downgrade rejection.
- Create `web/viewer/mesh-weather.mjs`: pure mode-specific surface response and disclosure state.
- Create `web/viewer/mesh-weather.test.mjs`: six-state mesh response and 3DGS overlay tests.
- Modify `web/viewer/environment.mjs`: add surface-response data without changing overlay identity.
- Modify `web/viewer/environment.test.mjs`: frozen six-weather response contract.
- Modify `web/viewer/main.js`: material cloning/restoration, light/exposure response, and local manifest query.
- Modify `web/viewer/bridge.mjs`: mode-specific textured mesh capability.
- Modify `web/viewer/bridge.test.mjs`: truthful capability assertions.
- Modify `web/viewer/index.html`: renderer-dependent weather label/status copy.
- Modify `web/viewer/index-contract.test.mjs`: dynamic copy contract.
- Create `pipeline/synthetic_village/model_preview_release.py`: authoritative-only tracked-release projection.
- Create `tests/test_model_preview_release.py`: reject L0/local and publish exact L2 Windows bytes.
- Create `docs/verification/2026-07-18-synthetic-pbr-local-preview.md`: textual local receipt with private screenshot hashes, never private image bytes.
- Modify `README.md`: operator commands, capability boundary, and current limitations.

---

### Task 1: Hermetic Material Fixtures and Deterministic Map Derivation

**Files:**
- Create: `tests/synthetic_material_fixtures.py`
- Create: `pipeline/synthetic_village/material_bundle.py`
- Create: `tests/test_synthetic_village_material_bundle.py`

**Interfaces:**
- Consumes: `VisualSourceManifest`, `VisualSourceRecord`, tracked `VisualSlotCatalog`, and verified source image bytes.
- Produces: `MaterialMapDescriptor`, `DerivedMaterialRecord`, `DerivedMaterialBundle`, `PreparedMaterialBundle`, `prepare_material_bundle(...)`, `canonical_material_bundle_bytes(...)`, and `verify_prepared_material_bundle(...)`.

- [ ] **Step 1: Add a hermetic 24-slot visual-pack fixture**

Create a helper that reads the tracked catalog, generates a distinct small PNG
for every material slot, and writes a canonical private manifest:

```python
# tests/synthetic_material_fixtures.py
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from pipeline.synthetic_village.defaults import load_default_visual_slots
from pipeline.synthetic_village.visual_sources import (
    VisualSourceManifest,
    VisualSourceRecord,
    canonical_manifest_bytes,
)


def write_material_visual_pack(root: Path) -> Path:
    object_root = root / "objects"
    object_root.mkdir(parents=True)
    records = []
    material_slots = sorted(
        (
            slot for slot in load_default_visual_slots().slots
            if slot.category == "material"
        ),
        key=lambda slot: slot.slot_id,
    )
    for index, slot in enumerate(material_slots, start=1):
        image = Image.new("RGB", (12, 8))
        pixels = image.load()
        for y in range(image.height):
            for x in range(image.width):
                pixels[x, y] = (
                    (index * 17 + x * 13) % 256,
                    (index * 29 + y * 19) % 256,
                    (index * 37 + x * 7 + y * 11) % 256,
                )
        temporary = root / f"{slot.slot_id}.png"
        image.save(temporary, format="PNG", compress_level=9, optimize=False)
        payload = temporary.read_bytes()
        temporary.unlink()
        digest = hashlib.sha256(payload).hexdigest()
        object_path = f"objects/{digest}.png"
        (root / object_path).write_bytes(payload)
        records.append(
            VisualSourceRecord(
                slot_id=slot.slot_id,
                category="material",
                object_path=object_path,
                sha256=digest,
                bytes=len(payload),
                width=12,
                height=8,
                prompt=slot.prompt,
                source_pack_id="pytest-material-fixture",
                source_manifest_sha256=f"{index:064x}",
                generator_interface="pytest-pillow-generated",
                actual_model_id="deterministic-test-fixture",
            ),
        )
    manifest = VisualSourceManifest(
        pack_id="synthetic-mountain-village-hybrid-v3",
        records=tuple(records),
    )
    (root / "visual-sources.json").write_bytes(canonical_manifest_bytes(manifest))
    return root
```

- [ ] **Step 2: Write failing derivation and identity tests**

```python
# tests/test_synthetic_village_material_bundle.py
import hashlib
from pathlib import Path

from pipeline.synthetic_village.material_bundle import (
    canonical_material_bundle_bytes,
    prepare_material_bundle,
    verify_prepared_material_bundle,
)
from tests.synthetic_material_fixtures import write_material_visual_pack


def test_prepare_material_bundle_derives_three_maps_for_all_24_slots(tmp_path: Path):
    visual_root = write_material_visual_pack(tmp_path / "visual")
    staging = tmp_path / "staging"

    prepared = prepare_material_bundle(
        visual_pack_root=visual_root,
        staging_root=staging,
    )

    assert len(prepared.manifest.records) == 24
    assert [row.slot_id for row in prepared.manifest.records] == sorted(
        row.slot_id for row in prepared.manifest.records
    )
    for row in prepared.manifest.records:
        assert row.base_color.color_space == "srgb"
        assert row.normal.color_space == "non-color"
        assert row.orm.color_space == "non-color"
        assert row.base_color.width == row.base_color.height == 1024
        assert row.normal.width == row.orm.width == 1024
        assert (staging / row.base_color.object_path).is_file()
        assert (staging / row.normal.object_path).is_file()
        assert (staging / row.orm.object_path).is_file()
    assert verify_prepared_material_bundle(staging) == prepared.manifest
    without_id = canonical_material_bundle_bytes(
        prepared.manifest,
        exclude_bundle_id=True,
    )
    assert hashlib.sha256(without_id).hexdigest() == prepared.manifest.bundle_id


def test_base_color_opposite_edges_are_byte_equal(tmp_path: Path):
    visual_root = write_material_visual_pack(tmp_path / "visual")
    prepared = prepare_material_bundle(
        visual_pack_root=visual_root,
        staging_root=tmp_path / "staging",
    )
    image = prepared.open_map(prepared.manifest.records[0].base_color)
    assert list(image.crop((0, 0, 1, 1024)).getdata()) == list(
        image.crop((1023, 0, 1024, 1024)).getdata()
    )
    assert list(image.crop((0, 0, 1024, 1)).getdata()) == list(
        image.crop((0, 1023, 1024, 1024)).getdata()
    )
```

Add tests that changing one source changes only that slot's derived descriptors
and changes the bundle ID, and that all decoded normal vectors are finite with a
positive blue component while every ORM channel stays in `0..255`.

- [ ] **Step 3: Run focused tests and capture RED**

Run:

```bash
python3 -m pytest tests/test_synthetic_village_material_bundle.py -q
```

Expected: collection fails because
`pipeline.synthetic_village.material_bundle` does not exist.

- [ ] **Step 4: Implement strict bundle models and canonical identity**

Define the public models and fixed algorithm identity:

```python
# pipeline/synthetic_village/material_bundle.py
MAP_SIZE = 1024
ALGORITHM_ID = "mirror-sobel-orm-v1"
UvPolicy = Literal[
    "world-xy", "dominant-axis-box", "roof-slope",
    "object-long-axis", "leaf-card",
]


class MaterialMapDescriptor(FrozenModel):
    object_path: str
    sha256: Sha256
    bytes: int = Field(ge=1)
    width: Literal[1024] = 1024
    height: Literal[1024] = 1024
    media_type: Literal["image/png"] = "image/png"
    color_space: Literal["srgb", "non-color"]


class DerivedMaterialRecord(FrozenModel):
    slot_id: str
    source_sha256: Sha256
    source_width: int = Field(ge=1)
    source_height: int = Field(ge=1)
    base_color: MaterialMapDescriptor
    normal: MaterialMapDescriptor
    orm: MaterialMapDescriptor
    uv_policy: UvPolicy
    nominal_tile_m: float = Field(gt=0, allow_inf_nan=False)
    normal_strength: float = Field(gt=0, allow_inf_nan=False)
    roughness_center: float = Field(ge=0, le=1, allow_inf_nan=False)
    metallic: float = Field(ge=0, le=1, allow_inf_nan=False)
    replacement_contract_sha256: Sha256
    synthetic: Literal[True] = True


class DerivedMaterialBundle(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.derived-material-bundle.v1"
    ] = "nantai.synthetic-village.derived-material-bundle.v1"
    bundle_id: Sha256
    synthetic: Literal[True] = True
    source_pack_id: str
    source_manifest_sha256: Sha256
    algorithm_id: Literal["mirror-sobel-orm-v1"] = ALGORITHM_ID
    python_version: str
    pillow_version: str
    module_sha256: Sha256
    records: tuple[DerivedMaterialRecord, ...] = Field(min_length=24, max_length=24)


@dataclass(frozen=True)
class PreparedMaterialBundle:
    staging_root: Path
    manifest: DerivedMaterialBundle

    def open_map(self, descriptor: MaterialMapDescriptor) -> Image.Image:
        path = self.staging_root / descriptor.object_path
        with Image.open(path) as image:
            image.load()
            return image.copy()
```

Validators require exact sorted unique material slot coverage, portable
`objects/<sha256>.png` paths, three map color-space roles, and a canonical
`bundle_id`.

- [ ] **Step 5: Freeze the exact 24-slot material parameters**

Keep the artistic scale explicitly nominal, not measured:

```python
MaterialParameters = tuple[UvPolicy, float, float, float, float]
# uv policy, nominal tile metres, normal strength, roughness centre, metallic
MATERIAL_PARAMETERS: dict[str, MaterialParameters] = {
    "material-aged-metal-01": ("dominant-axis-box", 0.8, 0.70, 0.52, 0.62),
    "material-bamboo-leaf-01": ("leaf-card", 0.35, 0.55, 0.74, 0.0),
    "material-bamboo-stem-01": ("object-long-axis", 0.6, 0.65, 0.58, 0.0),
    "material-broadleaf-bark-01": ("object-long-axis", 1.4, 0.85, 0.91, 0.0),
    "material-broadleaf-canopy-01": ("leaf-card", 0.9, 0.50, 0.82, 0.0),
    "material-clay-brick-01": ("dominant-axis-box", 1.2, 0.80, 0.83, 0.0),
    "material-creek-rock-01": ("world-xy", 2.5, 0.90, 0.88, 0.0),
    "material-dark-timber-01": ("object-long-axis", 1.6, 0.80, 0.78, 0.0),
    "material-dry-stone-wall-01": ("dominant-axis-box", 3.0, 1.00, 0.94, 0.0),
    "material-fieldstone-01": ("dominant-axis-box", 2.5, 1.00, 0.91, 0.0),
    "material-gray-roof-tile-01": ("roof-slope", 3.0, 0.90, 0.76, 0.0),
    "material-moss-stone-01": ("dominant-axis-box", 2.5, 0.95, 0.93, 0.0),
    "material-orchard-bark-01": ("object-long-axis", 1.2, 0.85, 0.88, 0.0),
    "material-orchard-leaf-01": ("leaf-card", 0.6, 0.50, 0.76, 0.0),
    "material-packed-earth-01": ("world-xy", 3.0, 0.70, 0.96, 0.0),
    "material-pale-plaster-01": ("dominant-axis-box", 3.5, 0.55, 0.88, 0.0),
    "material-rammed-earth-01": ("dominant-axis-box", 3.5, 0.85, 0.94, 0.0),
    "material-rice-paddy-water-01": ("world-xy", 6.0, 0.25, 0.19, 0.0),
    "material-shallow-water-01": ("world-xy", 5.0, 0.22, 0.14, 0.0),
    "material-terrace-soil-01": ("world-xy", 4.0, 0.75, 0.97, 0.0),
    "material-vegetable-leaf-01": ("leaf-card", 0.45, 0.55, 0.77, 0.0),
    "material-weathered-timber-01": ("object-long-axis", 1.8, 0.85, 0.86, 0.0),
    "material-wet-stone-paving-01": ("world-xy", 2.5, 0.80, 0.48, 0.0),
    "material-woven-bamboo-01": ("object-long-axis", 1.2, 0.70, 0.83, 0.0),
}
```

Validate that the mapping keys equal the tracked 24 material slot IDs exactly.
Any parameter adjustment changes `ALGORITHM_ID`; it is not an invisible tuning
knob.

- [ ] **Step 6: Implement deterministic map bytes**

Use Pillow only for decode/crop/resize and NumPy integer arrays for the fixed
pixel formulas:

```python
def _mirror_tile(image: Image.Image) -> Image.Image:
    square = ImageOps.fit(
        ImageOps.exif_transpose(image).convert("RGB"),
        (MAP_SIZE, MAP_SIZE),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    mosaic = Image.new("RGB", (MAP_SIZE * 2, MAP_SIZE * 2))
    mosaic.paste(square, (0, 0))
    mosaic.paste(ImageOps.mirror(square), (MAP_SIZE, 0))
    mosaic.paste(ImageOps.flip(square), (0, MAP_SIZE))
    mosaic.paste(ImageOps.flip(ImageOps.mirror(square)), (MAP_SIZE, MAP_SIZE))
    offset = MAP_SIZE // 2
    return mosaic.crop((offset, offset, offset + MAP_SIZE, offset + MAP_SIZE))


def _luminance(rgb: np.ndarray) -> np.ndarray:
    values = rgb.astype(np.uint16)
    return (
        values[..., 0] * 54
        + values[..., 1] * 183
        + values[..., 2] * 19
        + 128
    ) >> 8
```

Use wrapped `np.roll` neighbors for Sobel X/Y, normalize
`(-strength*gx, -strength*gy, 255)` in float64, round half to nearest integer,
and encode OpenGL normals. Compute ORM red from bounded local luminance
contrast, green from the fixed roughness centre plus bounded high-frequency
variation, and blue from the slot metallic scalar. Encode every PNG through one
helper using `compress_level=9`, `optimize=False`, and no metadata.

- [ ] **Step 7: Verify focused tests GREEN and run source-registry regression**

Run:

```bash
python3 -m pytest \
  tests/test_synthetic_village_material_bundle.py \
  tests/test_synthetic_village_visual_sources.py \
  tests/test_synthetic_village_contracts.py -q
python3 -m ruff check \
  pipeline/synthetic_village/material_bundle.py \
  tests/synthetic_material_fixtures.py \
  tests/test_synthetic_village_material_bundle.py
```

Expected: all selected tests pass and Ruff exits `0`.

- [ ] **Step 8: Commit and push Task 1**

```bash
git add \
  pipeline/synthetic_village/material_bundle.py \
  tests/synthetic_material_fixtures.py \
  tests/test_synthetic_village_material_bundle.py
git commit -m "feat(synthetic): derive content-addressed PBR maps" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 2: Durable Bundle Publication and Operator CLI

**Files:**
- Modify: `pipeline/synthetic_village/material_bundle.py`
- Modify: `tests/test_synthetic_village_material_bundle.py`
- Modify: `tests/synthetic_material_fixtures.py`
- Modify: `scripts/synthetic_village.py`
- Create: `tests/test_synthetic_village_cli.py`

**Interfaces:**
- Consumes: Task 1 `prepare_material_bundle(...)` and private visual pack.
- Produces: `MaterialBundleResult`, `publish_material_bundle(...)`,
  `load_material_bundle(...)`, test helper
  `publish_material_fixture(root: Path)`, and CLI
  `python scripts/synthetic_village.py build-materials`.

- [ ] **Step 1: Write failing absent-only and recovery tests**

```python
def test_publish_material_bundle_is_absent_only_and_idempotent(tmp_path):
    visual_root = write_material_visual_pack(tmp_path / "visual")
    publication_root = tmp_path / ".nantai-studio/material-bundles"
    work_root = tmp_path / ".nantai-studio/work"

    first = publish_material_bundle(
        visual_pack_root=visual_root,
        publication_root=publication_root,
        work_root=work_root,
    )
    second = publish_material_bundle(
        visual_pack_root=visual_root,
        publication_root=publication_root,
        work_root=work_root,
    )

    assert second.final_directory == first.final_directory
    assert second.reused is True
    assert load_material_bundle(first.final_directory).bundle_id == first.bundle_id
    assert not list(work_root.glob(".material-*"))
```

Add adversarial tests for an existing destination with altered bytes, symlinked
bundle/object paths, a source mutation between snapshot and publication,
directory-flush failure, and interrupted staging cleanup. Existing same-ID
bytes may be reused only after a full hash verification.

- [ ] **Step 2: Run focused tests and capture RED**

Run:

```bash
python3 -m pytest \
  tests/test_synthetic_village_material_bundle.py \
  tests/test_synthetic_village_cli.py -q
```

Expected: import or assertion failures because publication and CLI interfaces
do not exist.

- [ ] **Step 3: Implement lock-owned durable publication**

Use `ProjectFileLock`, existing file/directory flush primitives, stable bounded
reads, and a nonce staging directory:

```python
def publish_material_bundle(
    *,
    visual_pack_root: Path,
    publication_root: Path,
    work_root: Path,
) -> MaterialBundleResult:
    with ProjectFileLock(work_root / ".material-bundle.lock", role="writer"):
        staging = work_root / f".material-{uuid.uuid4().hex}"
        prepared = prepare_material_bundle(
            visual_pack_root=visual_pack_root,
            staging_root=staging,
        )
        verify_prepared_material_bundle(staging)
        destination = publication_root / prepared.manifest.bundle_id
        if destination.exists():
            existing = load_material_bundle(destination)
            if existing != prepared.manifest:
                raise MaterialBundleError(
                    "existing material bundle does not match its content identity"
                )
            _verify_bundle_objects(destination, existing)
            shutil.rmtree(staging)
            return MaterialBundleResult(
                bundle_id=existing.bundle_id,
                final_directory=destination,
                reused=True,
            )
        _durably_flush_bundle(staging)
        _move_directory_noreplace(staging, destination)
        return MaterialBundleResult(
            bundle_id=prepared.manifest.bundle_id,
            final_directory=destination,
            reused=False,
        )
```

Wrap filesystem and validation failures in one stable `MaterialBundleError`;
never catch and downgrade a hash or path failure.

- [ ] **Step 4: Add the exact CLI**

Add `build-materials` with optional private-root overrides for tests and a
stable JSON summary:

```python
build_materials = commands.add_parser(
    "build-materials",
    help="Derive and privately publish the complete 24-slot PBR material bundle.",
)
build_materials.add_argument(
    "--visual-pack-root",
    type=Path,
    default=DEFAULT_VISUAL_PACK_ROOT,
)
build_materials.add_argument(
    "--publication-root",
    type=Path,
    default=ROOT / ".nantai-studio/synthetic-village/hybrid-v3/material-bundles",
)
```

The command prints only `bundle_id`, `record_count`, `reused`, and the private
final directory for the local operator. It never writes the path into a
canonical manifest.

- [ ] **Step 5: Add the reusable hermetic publisher**

```python
# tests/synthetic_material_fixtures.py
def publish_material_fixture(root: Path):
    visual_root = write_material_visual_pack(root / "visual")
    result = publish_material_bundle(
        visual_pack_root=visual_root,
        publication_root=root / "material-bundles",
        work_root=root / "material-work",
    )
    return visual_root, result
```

- [ ] **Step 6: Run focused and CLI tests GREEN**

Run:

```bash
python3 -m pytest \
  tests/test_synthetic_village_material_bundle.py \
  tests/test_synthetic_village_cli.py -q
python3 -m ruff check \
  pipeline/synthetic_village/material_bundle.py \
  scripts/synthetic_village.py \
  tests/test_synthetic_village_material_bundle.py \
  tests/test_synthetic_village_cli.py
```

Expected: all selected tests pass; CLI test parses exactly one JSON object.

- [ ] **Step 7: Commit and push Task 2**

```bash
git add \
  pipeline/synthetic_village/material_bundle.py \
  scripts/synthetic_village.py \
  tests/synthetic_material_fixtures.py \
  tests/test_synthetic_village_material_bundle.py \
  tests/test_synthetic_village_cli.py
git commit -m "feat(synthetic): publish immutable material bundles" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 3: Independent Binary-GLB Material Audit

**Files:**
- Create: `pipeline/synthetic_village/glb_material_audit.py`
- Create: `tests/test_glb_material_audit.py`

**Interfaces:**
- Consumes: a GLB path and exact `ExpectedGlbMaterial` records.
- Produces: `GlbMaterialAudit`, `GlbMaterialAuditError`, and
  `audit_textured_glb(path, expected_materials)`.

- [ ] **Step 1: Write a minimal handcrafted GLB fixture and failing tests**

Build GLB bytes with one triangle, one material, embedded PNG buffer view,
`POSITION`, `NORMAL`, `TEXCOORD_0`, and `TANGENT` accessors:

```python
def _glb(document: dict, binary: bytes) -> bytes:
    json_bytes = json.dumps(
        document, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return b"".join((
        struct.pack("<4sII", b"glTF", 2, total),
        struct.pack("<I4s", len(json_bytes), b"JSON"),
        json_bytes,
        struct.pack("<I4s", len(binary), b"BIN\0"),
        binary,
    ))
```

The positive test expects:

```python
audit = audit_textured_glb(
    glb_path,
    expected_materials=(
        ExpectedGlbMaterial(
            slot_id="material-fieldstone-01",
            source_sha256="1" * 64,
            bundle_id="2" * 64,
            algorithm_id="mirror-sobel-orm-v1",
        ),
    ),
)
assert audit.material_count == 1
assert audit.textured_primitive_count == 1
assert audit.uv_primitive_count == 1
assert audit.tangent_primitive_count == 1
assert audit.external_uri_count == 0
```

Parametrize mutations that remove `TEXCOORD_0`, `TANGENT`,
`baseColorTexture`, `normalTexture`, `metallicRoughnessTexture`, embedded image
buffer views, required extras, or assign an external URI.

- [ ] **Step 2: Run the focused audit tests and capture RED**

Run:

```bash
python3 -m pytest tests/test_glb_material_audit.py -q
```

Expected: collection fails because the audit module does not exist.

- [ ] **Step 3: Implement bounded GLB parsing and structural checks**

Define the expected material identity independently of the canary request to
avoid an import cycle:

```python
class ExpectedGlbMaterial(FrozenModel):
    slot_id: str = Field(pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_sha256: Sha256
    bundle_id: Sha256
    algorithm_id: Literal["mirror-sobel-orm-v1"]
```

The parser reads the header and chunks with explicit bounds:

```python
def _load_glb(path: Path) -> tuple[bytes, dict[str, object], bytes]:
    raw = _read_stable_file(path, maximum_bytes=MAX_GLB_BYTES)
    if len(raw) < 20 or raw[:4] != b"glTF":
        raise GlbMaterialAuditError("GLB header is invalid")
    magic, version, declared = struct.unpack_from("<4sII", raw, 0)
    if magic != b"glTF" or version != 2 or declared != len(raw):
        raise GlbMaterialAuditError("GLB length or version is invalid")
    json_length, json_kind = struct.unpack_from("<I4s", raw, 12)
    if json_kind != b"JSON" or 20 + json_length > len(raw):
        raise GlbMaterialAuditError("GLB JSON chunk is invalid")
    document = json.loads(raw[20:20 + json_length].decode("utf-8"))
    binary_offset = 20 + json_length
    binary_length, binary_kind = struct.unpack_from("<I4s", raw, binary_offset)
    if binary_kind != b"BIN\0":
        raise GlbMaterialAuditError("GLB binary chunk is absent")
    binary = raw[binary_offset + 8:binary_offset + 8 + binary_length]
    if binary_offset + 8 + binary_length != len(raw):
        raise GlbMaterialAuditError("GLB binary length is invalid")
    return raw, document, binary
```

Verify every accessor and image buffer view range against the binary chunk,
map material extras by exact slot ID, require the three PBR texture roles, and
walk every mesh primitive. Return frozen count evidence plus GLB SHA-256.

- [ ] **Step 4: Run audit tests and Ruff GREEN**

Run:

```bash
python3 -m pytest tests/test_glb_material_audit.py -q
python3 -m ruff check \
  pipeline/synthetic_village/glb_material_audit.py \
  tests/test_glb_material_audit.py
```

Expected: all selected tests pass and every adversarial mutation is rejected.

- [ ] **Step 5: Commit and push Task 3**

```bash
git add \
  pipeline/synthetic_village/glb_material_audit.py \
  tests/test_glb_material_audit.py
git commit -m "feat(synthetic): audit embedded GLB materials" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 4: Schema-v2 Textured Canary Request and Invocation Snapshot

**Files:**
- Modify: `pipeline/synthetic_village/canary.py`
- Modify: `tests/test_synthetic_village_canary.py`

**Interfaces:**
- Consumes: verified Task 2 bundle and legacy scene/camera/object registries.
- Produces: `MaterialInputRecord`, `TexturedBuildRequest`,
  `TexturedBuildReport`, `build_textured_canary_request(...)`,
  `snapshot_material_inputs(...)`, `run_textured_canary_build(...)`, and
  `verify_textured_build_report(...)`.

- [ ] **Step 1: Write failing v2 identity and path-free tests**

```python
def test_textured_request_binds_exact_material_bundle_without_private_paths(
    tmp_path,
):
    visual_root, bundle = publish_material_fixture(tmp_path)
    request = build_textured_canary_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )

    assert request.schema_version == (
        "nantai.synthetic-village.blender-build-request.v2"
    )
    assert len(request.material_input_registry) == 24
    assert all(
        row.usage_mode == "runtime-material-source-v1"
        and row.implementation == "derived-pbr-material-v1"
        for row in request.visual_slot_registry
        if row.category == "material"
    )
    raw = canonical_textured_build_request_bytes(request)
    assert b".nantai-studio" not in raw
    assert str(Path.home()).encode() not in raw
    assert hashlib.sha256(
        canonical_textured_build_request_bytes(
            request, exclude_build_id=True
        )
    ).hexdigest() == request.build_id
```

Add tests rejecting 23 records, duplicate slots, wrong map hashes, wrong bundle
manifest digest, v1 usage values in v2, and a v2 build ID that omits material
identity.

- [ ] **Step 2: Write failing invocation-snapshot and fake-runner tests**

The fake subprocess must receive exactly:

```python
assert argv[-6:] == [
    "--request", argv[-5],
    "--materials", argv[-3],
    "--staging", argv[-1],
]
material_root = Path(argv[-3])
assert material_root.parent == Path(argv[-5]).parent
assert {path.name for path in material_root.iterdir()} == {
    f"{digest}.png"
    for row in request.material_input_registry
    for digest in (row.base_color_sha256, row.normal_sha256, row.orm_sha256)
}
```

Mutate one snapshot file during the fake subprocess and expect
`CanaryBuildError` after process exit with no final publication.

- [ ] **Step 3: Run focused canary tests and capture RED**

Run:

```bash
python3 -m pytest tests/test_synthetic_village_canary.py -q
```

Expected: failures because schema-v2 types and runner do not exist.

- [ ] **Step 4: Add strictly additive v2 models**

Keep `BuildRequest` and `BuildReport` untouched for schema v1. Add:

```python
TEXTURED_BUILD_REQUEST_SCHEMA = (
    "nantai.synthetic-village.blender-build-request.v2"
)
TEXTURED_BUILD_REPORT_SCHEMA = (
    "nantai.synthetic-village.blender-build-report.v2"
)


class MaterialInputRecord(FrozenModel):
    slot_id: str
    source_sha256: Sha256
    base_color_sha256: Sha256
    normal_sha256: Sha256
    orm_sha256: Sha256
    width: Literal[1024] = 1024
    height: Literal[1024] = 1024
    uv_policy: UvPolicy
    nominal_tile_m: float = Field(gt=0, allow_inf_nan=False)
    normal_strength: float = Field(gt=0, allow_inf_nan=False)
    synthetic: Literal[True] = True


class TexturedVisualSlotRegistryEntry(FrozenModel):
    slot_id: str
    category: SlotCategory
    usage_mode: Literal[
        "design-reference-only",
        "procedural-placeholder-v1",
        "runtime-material-source-v1",
    ]
    source_sha256: Sha256 | None
    reference_status: VisualReferenceStatus
    canary_critical: bool
    build_status: VisualBuildStatus
    implementation: Literal[
        "composition-reference-v1",
        "derived-pbr-material-v1",
        "geometry-detail-v1",
        "environment-element-v1",
        "prop-element-v1",
        "not-instantiated-v1",
    ]
    component_tag: str | None
    evidence_ids: tuple[EvidenceId, ...]


class TexturedBuildRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.blender-build-request.v2"
    ] = TEXTURED_BUILD_REQUEST_SCHEMA
    build_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L2"] = "L2"
    scene_plan: ScenePlan
    camera_plan: CameraPlan
    source_hashes: SourceHashes
    tool_identity: ToolIdentity
    object_registry: tuple[ObjectRegistryEntry, ...]
    auxiliary_registry: tuple[AuxiliaryRegistryEntry, ...]
    semantic_registry: tuple[SemanticRegistryEntry, ...]
    material_registry: tuple[MaterialRegistryEntry, ...]
    visual_slot_registry: tuple[TexturedVisualSlotRegistryEntry, ...]
    requested_artifacts: tuple[ArtifactRequest, ...]
    material_bundle_manifest_sha256: Sha256
    material_bundle_id: Sha256
    material_input_registry: tuple[MaterialInputRecord, ...] = Field(
        min_length=24, max_length=24
    )
```

Do not subclass `BuildRequest`: its v1 model validator correctly requires
`pbr-material-v1` and would reject or, if weakened, silently change the legacy
contract. Extract only side-effect-free common checks into a helper called by
both models. The v2 validator derives the build ID from v2 canonical bytes and
requires the 24 material visual slots to use the new usage/implementation
values. Define `TexturedBuildReport` as a separate frozen model for the same
reason.

- [ ] **Step 5: Implement exact invocation copying and authoritative runner**

`snapshot_material_inputs(...)` creates only
`<invocation>/material-inputs/<sha256>.png`, copies through already-open source
files, verifies the copied bytes, flushes them, and returns immutable snapshots.
`run_textured_canary_build(...)` calls the locked Windows executable and
independent GLB audit before final-directory publication.

Do not use hard links: a mutable source object must not share storage with an
invocation input. Re-verify source, request, and copied snapshots after Blender
returns and after staging flush.

- [ ] **Step 6: Run focused and legacy canary tests GREEN**

Run:

```bash
python3 -m pytest \
  tests/test_synthetic_village_canary.py \
  tests/test_synthetic_village_visual_sources.py \
  tests/test_glb_material_audit.py -q
python3 -m ruff check pipeline/synthetic_village/canary.py \
  tests/test_synthetic_village_canary.py
```

Expected: v1 and v2 tests pass; fake runner proves exact argv and cleanup.

- [ ] **Step 7: Commit and push Task 4**

```bash
git add \
  pipeline/synthetic_village/canary.py \
  tests/test_synthetic_village_canary.py
git commit -m "feat(synthetic): bind material bundles to canary requests" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 5: Blender UV, Tangent, and Embedded PBR Export

**Files:**
- Modify: `scripts/blender/build_synthetic_village.py`
- Modify: `tests/test_synthetic_village_blender_runtime.py`
- Modify: `tests/test_synthetic_village_canary.py`

**Interfaces:**
- Consumes: Task 4 v2 JSON and invocation material directory.
- Produces: a self-contained textured GLB, v2 build report material evidence,
  and unchanged schema-v1 flat behavior.

- [ ] **Step 1: Add source-level RED guards for actual consumption**

```python
def test_builder_source_contains_verified_texture_uv_and_tangent_path():
    source = (
        ROOT / "scripts/blender/build_synthetic_village.py"
    ).read_text("utf-8")
    for required in (
        '"--materials"',
        "ShaderNodeTexImage",
        "ShaderNodeNormalMap",
        "ShaderNodeSeparateColor",
        "uv_layers.new",
        "calc_tangents",
        "runtime-material-source-v1",
        "derived-pbr-material-v1",
    ):
        assert required in source
```

Add schema-v2 runtime tests that reject a missing material directory, extra
file, symlink, wrong digest, wrong dimensions, and a request that presents a
source as merely `design-reference-only`.

- [ ] **Step 2: Add real-runtime assertions behind the existing platform gate**

Extend the existing locked Blender test to parse the built GLB through Task 3:

```python
audit = audit_textured_glb(
    staging / "village-canary.glb",
    expected_materials=expected_glb_materials(request),
)
assert audit.material_count == 24
assert audit.textured_primitive_count == audit.primitive_count
assert audit.uv_primitive_count == audit.primitive_count
assert audit.tangent_primitive_count == audit.primitive_count
assert audit.external_uri_count == 0
```

- [ ] **Step 3: Run focused tests and capture RED**

Run:

```bash
python3 -m pytest \
  tests/test_synthetic_village_canary.py \
  tests/test_synthetic_village_blender_runtime.py -q
```

Expected: source guards fail and real runtime test skips on macOS until the
local request is added in Task 6.

- [ ] **Step 4: Add exact dual-schema runtime arguments**

Legacy v1 continues to require:

```text
--request <file> --staging <directory>
```

Textured v2 requires exactly:

```text
--request <file> --materials <directory> --staging <directory>
```

Read the canonical request before selecting the form. Reject all other
argument counts/orders, non-absolute runtime paths, redirected paths, or a
materials argument supplied to v1.

- [ ] **Step 5: Load and verify material maps**

For each v2 record, derive filenames from hashes, read stable regular files,
verify SHA-256 and decoded `1024 × 1024` dimensions, then load:

```python
base = bpy.data.images.load(str(base_path), check_existing=False)
base.colorspace_settings.name = "sRGB"
normal = bpy.data.images.load(str(normal_path), check_existing=False)
normal.colorspace_settings.name = "Non-Color"
orm = bpy.data.images.load(str(orm_path), check_existing=False)
orm.colorspace_settings.name = "Non-Color"
```

Immediately re-hash after Blender loads each image. Store no absolute filepath
in scene extras or the build report.

- [ ] **Step 6: Bind the exact PBR node graph**

For each material:

```python
base_node = nodes.new("ShaderNodeTexImage")
base_node.image = images.base_color
normal_node = nodes.new("ShaderNodeTexImage")
normal_node.image = images.normal
normal_map = nodes.new("ShaderNodeNormalMap")
normal_map.inputs["Strength"].default_value = record["normal_strength"]
orm_node = nodes.new("ShaderNodeTexImage")
orm_node.image = images.orm
separate = nodes.new("ShaderNodeSeparateColor")
links.new(base_node.outputs["Color"], principled.inputs["Base Color"])
links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
links.new(orm_node.outputs["Color"], separate.inputs["Color"])
links.new(separate.outputs["Green"], principled.inputs["Roughness"])
links.new(separate.outputs["Blue"], principled.inputs["Metallic"])
```

Set stable material extras for slot, source digest, bundle ID, algorithm,
synthetic provenance, and UV policy. Keep the old constant-node path only when
the request schema is v1.

- [ ] **Step 7: Generate deterministic world-scale UVs and tangents**

Add one polygon-loop projection dispatcher. For dominant-axis box mapping:

```python
def _dominant_axis_uv(world_vertex, world_normal, scale):
    axis = max(range(3), key=lambda index: abs(world_normal[index]))
    if axis == 0:
        return (world_vertex.y / scale, world_vertex.z / scale)
    if axis == 1:
        return (world_vertex.x / scale, world_vertex.z / scale)
    return (world_vertex.x / scale, world_vertex.y / scale)
```

Implement the four other approved policies with stable orientation rules,
populate one `nv_uv0` layer, triangulate before export, call
`mesh.calc_tangents(uvmap="nv_uv0")`, and fail if any loop UV/tangent component
is non-finite.

- [ ] **Step 8: Emit v2 report evidence and export embedded images**

Add report counts for primitives, images, textures, UV primitives, and tangent
primitives. Keep `fidelity=simplified-pbr-not-render-parity` and
`geometry_usability=preview-only`. Use binary GLB export and prove no external
URI through the independent audit rather than exporter settings alone.

- [ ] **Step 9: Run source, legacy, and available runtime tests GREEN**

Run:

```bash
python3 -m pytest \
  tests/test_synthetic_village_canary.py \
  tests/test_synthetic_village_blender_runtime.py \
  tests/test_glb_material_audit.py -q
python3 -m ruff check pipeline/synthetic_village/canary.py \
  tests/test_synthetic_village_canary.py \
  tests/test_synthetic_village_blender_runtime.py
python3 -m compileall -q pipeline scripts/blender
```

Expected: all non-platform tests pass; Windows-only runtime tests report an
explicit skip on macOS rather than a false pass.

- [ ] **Step 10: Commit and push Task 5**

```bash
git add \
  scripts/blender/build_synthetic_village.py \
  tests/test_synthetic_village_blender_runtime.py \
  tests/test_synthetic_village_canary.py
git commit -m "feat(synthetic): export textured PBR village GLB" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 6: Separate macOS L0 Preview and Strict Studio Route

**Files:**
- Create: `pipeline/synthetic_village/local_textured_preview.py`
- Create: `tests/test_local_textured_preview.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `pipeline/studio_server.py`
- Modify: `tests/test_studio_server.py`

**Interfaces:**
- Consumes: Task 2 bundle, Task 5 builder, actual
  `/Applications/Blender.app/Contents/MacOS/Blender`.
- Produces: `LocalTexturedPreviewRequest`, `LocalTexturedPreviewManifest`,
  `build_local_textured_preview_request(...)`,
  `build_local_textured_preview_manifest(...)`,
  `run_local_textured_preview(...)`, CLIs `build-textured-preview` and
  `audit-textured-glb`, and strict
  `/api/local-textured-preview/<preview-id>/{manifest.json,village-canary.glb}`.

- [ ] **Step 1: Write failing truth-separation tests**

```python
def _local_request(tmp_path):
    visual_root, bundle = publish_material_fixture(tmp_path)
    identity = LocalBlenderIdentity(
        executable_sha256="1" * 64,
        version="4.5.11",
        platform="macos-arm64",
        runtime_build_hash="4db51e9d1e1e",
        runtime_output_sha256="2" * 64,
    )
    return build_local_textured_preview_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
        tool_identity=identity,
    )


def test_local_request_cannot_validate_as_authoritative_request(tmp_path):
    request = _local_request(tmp_path)
    assert request.verification_level == "L0"
    assert request.authoritative is False
    assert request.release_channel == "local-preview-only"
    with pytest.raises(ValidationError):
        TexturedBuildRequest.model_validate(request.model_dump())


def test_local_manifest_is_preview_only_and_not_real_photo_texture(tmp_path):
    request = _local_request(tmp_path)
    manifest = build_local_textured_preview_manifest(
        request=request,
        glb_sha256="3" * 64,
        glb_bytes=1024,
        build_report_sha256="4" * 64,
        audit_sha256="5" * 64,
    )
    assert manifest.synthetic is True
    assert manifest.geometry_usability == "preview-only"
    assert manifest.material_fidelity == "synthetic-derived-pbr"
    assert manifest.synthetic_pbr_textures is True
    assert manifest.real_photo_textures is False
    assert manifest.dynamic_mesh_relighting is True
    assert manifest.splat_relighting is False
```

- [ ] **Step 2: Write failing HTTP containment and cache tests**

Create a fake private preview with canonical manifest and GLB hash. Assert GET,
HEAD, ETag/304, correct MIME types, and no-store manifest policy. Parametrize
`..`, percent-encoded traversal, slash-bearing IDs, symlinked preview roots,
changed GLB bytes, unknown IDs, and noncanonical manifests; all must fail
without returning payload bytes.

- [ ] **Step 3: Run focused tests and capture RED**

Run:

```bash
python3 -m pytest \
  tests/test_local_textured_preview.py \
  tests/test_studio_server.py -q
```

Expected: imports/routes fail because the local preview module and endpoint do
not exist.

- [ ] **Step 4: Implement exact macOS tool identity and private build**

Probe the configured executable with `--version`, record SHA-256, version,
platform `macos-arm64`, runtime output digest, and Blender build hash. The local
request has a separate schema:

```python
class LocalTexturedPreviewRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.local-textured-preview-request.v1"
    ]
    preview_id: Sha256
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    authoritative: Literal[False] = False
    release_channel: Literal["local-preview-only"] = "local-preview-only"
    tool_identity: LocalBlenderIdentity
    scene_plan: ScenePlan
    camera_plan: CameraPlan
    source_hashes: SourceHashes
    object_registry: tuple[ObjectRegistryEntry, ...]
    auxiliary_registry: tuple[AuxiliaryRegistryEntry, ...]
    semantic_registry: tuple[SemanticRegistryEntry, ...]
    material_registry: tuple[MaterialRegistryEntry, ...]
    visual_slot_registry: tuple[TexturedVisualSlotRegistryEntry, ...]
    requested_artifacts: tuple[ArtifactRequest, ...]
    material_bundle_manifest_sha256: Sha256
    material_bundle_id: Sha256
    material_input_registry: tuple[MaterialInputRecord, ...]
```

The builder accepts this schema only when the executing `bpy.app` identity
matches the local request. The authoritative publisher rejects the local
schema before reading artifacts. The duplicated typed fields are intentional:
the local request does not inherit the v2 authoritative model or its L2 trust
defaults.

- [ ] **Step 5: Publish canonical local manifest and verified GLB privately**

Publish below:

```text
.nantai-studio/synthetic-village/hybrid-v3/local-previews/<preview-id>/
  manifest.json
  village-canary.glb
  build-report.json
  glb-material-audit.json
```

Every descriptor is content-addressed. Publication is absent-only and reuses an
existing same-ID preview only after re-verifying all four files.

- [ ] **Step 6: Add strict same-origin Studio serving**

Match only:

```python
match = re.fullmatch(
    r"/api/local-textured-preview/([0-9a-f]{64})/"
    r"(manifest\.json|village-canary\.glb)",
    request_path,
)
```

Resolve the exact private root through strict real-path containment, revalidate
the manifest, verify GLB SHA before serving, return manifest `no-store`, GLB
`public, max-age=0, must-revalidate`, and SHA ETag. Do not expose directory
listing, build report, audit file, or an arbitrary filename.

- [ ] **Step 7: Add CLI and run focused tests GREEN**

`build-textured-preview` runs the local builder and prints preview identity.
`audit-textured-glb --preview-id <64hex>` resolves only the strict private
preview root, revalidates its manifest, and reruns Task 3 against the published
GLB. It never accepts a filesystem path from the command line.

Run:

```bash
python3 -m pytest \
  tests/test_local_textured_preview.py \
  tests/test_studio_server.py -q
python3 -m ruff check \
  pipeline/synthetic_village/local_textured_preview.py \
  pipeline/studio_server.py \
  scripts/synthetic_village.py \
  tests/test_local_textured_preview.py \
  tests/test_studio_server.py
```

Expected: local/authoritative schemas cannot cross-validate and all route
attacks are rejected.

- [ ] **Step 8: Commit and push Task 6**

```bash
git add \
  pipeline/synthetic_village/local_textured_preview.py \
  pipeline/studio_server.py \
  scripts/synthetic_village.py \
  tests/test_local_textured_preview.py \
  tests/test_studio_server.py
git commit -m "feat(studio): serve verified local textured previews" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 7: Textured Manifest v2 and Mode-Aware Six-Weather Relighting

**Files:**
- Modify: `web/viewer/model-preview.mjs`
- Modify: `web/viewer/model-preview.test.mjs`
- Create: `web/viewer/mesh-weather.mjs`
- Create: `web/viewer/mesh-weather.test.mjs`
- Modify: `web/viewer/environment.mjs`
- Modify: `web/viewer/environment.test.mjs`
- Modify: `web/viewer/main.js`
- Modify: `web/viewer/bridge.mjs`
- Modify: `web/viewer/bridge.test.mjs`
- Modify: `web/viewer/index.html`
- Modify: `web/viewer/index-contract.test.mjs`

**Interfaces:**
- Consumes: local manifest v2, GLB `MeshStandardMaterial` instances, six existing weather IDs.
- Produces: `meshWeatherResponse(weather)`,
  `environmentNotice(rendererCapabilities)`, reversible material state, and
  truthful textured mesh Viewer capabilities.

- [ ] **Step 1: Write failing manifest-v2 truth tests**

Define a valid v2 fixture with:

```javascript
const TEXTURED_MANIFEST = Object.freeze({
  ...VALID_MANIFEST,
  schema_version: 2,
  material_fidelity: 'synthetic-derived-pbr',
  synthetic_pbr_textures: true,
  real_photo_textures: false,
  dynamic_mesh_relighting: true,
  splat_relighting: false,
  authoritative: false,
  verification_level: 'L0',
  release_channel: 'local-preview-only',
  limitations: [
    'not-real-place',
    'not-measured-geometry',
    'not-completed-trained-reconstruction',
    'no-real-photo-textures',
    'local-preview-only',
  ],
});
```

Assert acceptance and disclosure containing `合成派生 PBR`, `非实拍贴图`,
`本机非权威预览`. Reject any v2 manifest with
`real_photo_textures=true`, `geometry_usability=metric-aligned`,
`splat_relighting=true`, or missing `local-preview-only`.

- [ ] **Step 2: Write failing pure six-weather response tests**

```javascript
test('six mesh weather responses are distinct and reversible', () => {
  const responses = WEATHER_IDS.map(meshWeatherResponse);
  assert.equal(new Set(responses.map(JSON.stringify)).size, WEATHER_IDS.length);
  assert.deepEqual(meshWeatherResponse('clear'), {
    exposure: 1,
    keyColor: 0xfff3dc,
    keyIntensity: 2.4,
    baseColorMultiplier: [1, 1, 1],
    roughnessMultiplier: 1,
  });
  assert.ok(meshWeatherResponse('rain').roughnessMultiplier < 1);
  assert.ok(meshWeatherResponse('rain').baseColorMultiplier[0] < 1);
  assert.ok(meshWeatherResponse('night').exposure < 0.5);
});

test('renderer notice never calls splat atmosphere relighting', () => {
  assert.match(
    environmentNotice({ dynamic_mesh_relighting: true }),
    /网格重光照.*大气叠加/,
  );
  assert.match(
    environmentNotice({ splat_relighting: false }),
    /3DGS.*仅大气叠加/,
  );
});
```

- [ ] **Step 3: Run Viewer tests and capture RED**

Run:

```bash
node --test \
  web/viewer/model-preview.test.mjs \
  web/viewer/mesh-weather.test.mjs \
  web/viewer/environment.test.mjs \
  web/viewer/bridge.test.mjs \
  web/viewer/index-contract.test.mjs
```

Expected: missing module and v2 validation failures.

- [ ] **Step 4: Add additive v2 validation and legacy preservation**

Dispatch on exact `schema_version`. Keep every v1 assertion and disclosure
unchanged. V2 requires all mode-specific truth fields and precise limitations.
`resolveModelPreviewUrl` and byte verification remain common and same-origin.

- [ ] **Step 5: Implement pure mesh weather response**

Freeze a complete response for every weather ID in
`mesh-weather.mjs`. The module contains no DOM or Three.js dependency.
`environmentNotice` selects copy from active renderer capability rather than a
global hard-coded claim.

```javascript
export const MESH_WEATHER_RESPONSES = Object.freeze({
  clear: Object.freeze({
    exposure: 1,
    keyColor: 0xfff3dc,
    keyIntensity: 2.4,
    baseColorMultiplier: Object.freeze([1, 1, 1]),
    roughnessMultiplier: 1,
  }),
  overcast: Object.freeze({
    exposure: 0.82,
    keyColor: 0xcfd8df,
    keyIntensity: 0.9,
    baseColorMultiplier: Object.freeze([0.92, 0.95, 0.98]),
    roughnessMultiplier: 1.08,
  }),
  rain: Object.freeze({
    exposure: 0.68,
    keyColor: 0xa9bfd0,
    keyIntensity: 0.65,
    baseColorMultiplier: Object.freeze([0.68, 0.74, 0.80]),
    roughnessMultiplier: 0.55,
  }),
  snow: Object.freeze({
    exposure: 1.1,
    keyColor: 0xeaf5ff,
    keyIntensity: 1.5,
    baseColorMultiplier: Object.freeze([1.06, 1.08, 1.10]),
    roughnessMultiplier: 1.12,
  }),
  fog: Object.freeze({
    exposure: 0.78,
    keyColor: 0xd2d8d7,
    keyIntensity: 0.55,
    baseColorMultiplier: Object.freeze([0.88, 0.90, 0.90]),
    roughnessMultiplier: 1.06,
  }),
  night: Object.freeze({
    exposure: 0.32,
    keyColor: 0x9cb8e8,
    keyIntensity: 0.32,
    baseColorMultiplier: Object.freeze([0.32, 0.38, 0.48]),
    roughnessMultiplier: 0.92,
  }),
});
```

- [ ] **Step 6: Apply reversible material clones in Viewer**

When a v2 textured GLB loads:

```javascript
root.traverse((object) => {
  if (!object.isMesh) return;
  const originals = Array.isArray(object.material)
    ? object.material : [object.material];
  const clones = originals.map((material) => {
    const clone = material.clone();
    clone.userData.nvBaseColor = clone.color.clone();
    clone.userData.nvBaseRoughness = clone.roughness;
    return clone;
  });
  object.material = Array.isArray(object.material) ? clones : clones[0];
});
```

`applyWeather` restores from `nvBase*` before multiplying, so repeated weather
switches never accumulate drift. Update model key light color/intensity and
renderer exposure from the pure response. V1 mesh and point/3DGS modes keep
atmosphere-only behavior.

- [ ] **Step 7: Load only strict same-origin local manifest query**

Accept `?modelManifest=/api/local-textured-preview/<64hex>/manifest.json`.
Resolve against `window.location`, require the same origin, and otherwise use
the tracked default manifest. Do not accept a full external URL.

- [ ] **Step 8: Advertise mode-specific capabilities and copy**

Textured v2 mesh capability includes:

```javascript
{
  material_fidelity: 'synthetic-derived-pbr',
  synthetic_pbr_textures: true,
  real_photo_textures: false,
  dynamic_mesh_relighting: true,
  splat_relighting: false,
  real_reconstruction: false,
}
```

The weather label becomes `天气（网格重光照 + 大气）` only in that mode; point
and Spark modes retain `视觉天气（叠加）`.

- [ ] **Step 9: Run all Viewer tests GREEN**

Run:

```bash
node --test web/viewer/*.test.mjs
```

Expected: all Viewer tests pass, including unchanged 3DGS provenance tests.

- [ ] **Step 10: Commit and push Task 7**

```bash
git add \
  web/viewer/model-preview.mjs \
  web/viewer/model-preview.test.mjs \
  web/viewer/mesh-weather.mjs \
  web/viewer/mesh-weather.test.mjs \
  web/viewer/environment.mjs \
  web/viewer/environment.test.mjs \
  web/viewer/main.js \
  web/viewer/bridge.mjs \
  web/viewer/bridge.test.mjs \
  web/viewer/index.html \
  web/viewer/index-contract.test.mjs
git commit -m "feat(viewer): relight textured mesh by weather" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 8: Build and Visually Verify the macOS Textured Preview

**Files:**
- Create: `docs/verification/2026-07-18-synthetic-pbr-local-preview.md`
- Modify only if visual evidence finds a defect: files owned by Tasks 1–7 plus their exact tests.

**Interfaces:**
- Consumes: the real private 24-source pack, installed macOS Blender, Studio server, and Viewer.
- Produces: one private L0 preview ID, structural audit evidence, browser screenshots in private storage, and a tracked textual verification receipt.

- [ ] **Step 1: Run the material builder against the real pack**

Run:

```bash
python3 scripts/synthetic_village.py build-materials
```

Expected JSON:

```json
{"bundle_id":"<64 lowercase hex>","record_count":24,"reused":false}
```

If `reused=true`, verify the existing bundle instead of treating reuse as a
fresh generation.

- [ ] **Step 2: Run the local Blender preview**

Run:

```bash
python3 scripts/synthetic_village.py build-textured-preview \
  --blender /Applications/Blender.app/Contents/MacOS/Blender \
  --timeout-seconds 1800
```

Expected JSON includes a 64-hex `preview_id`, `verification_level=L0`,
`authoritative=false`, `material_count=24`, and zero external URIs.

- [ ] **Step 3: Re-run structural audit from published bytes**

Run the CLI audit command added with Task 3:

```bash
python3 scripts/synthetic_village.py audit-textured-glb \
  --preview-id <preview-id>
```

Expected:

```json
{"external_uri_count":0,"material_count":24,"tangent_coverage":1.0,"texture_coverage":1.0,"uv_coverage":1.0}
```

- [ ] **Step 4: Start or reuse Studio and open the exact preview**

Run:

```bash
python3 -m pipeline.studio_server --host 127.0.0.1 --port 8767
```

Open:

```text
http://127.0.0.1:8767/web/viewer/?presentation=model&modelManifest=/api/local-textured-preview/<preview-id>/manifest.json
```

- [ ] **Step 5: Perform fixed visual checks**

Capture private screenshots for:

1. overview camera in clear weather;
2. close roof/timber view in clear weather;
3. close rammed-earth/fieldstone view in clear weather;
4. the same close view in rain;
5. the same close view in night.

Verify:

- roof, timber, earth, plaster, and stone are materially distinguishable;
- no magenta/black missing map appears;
- no dominant projection is visibly rotated on the inspected surfaces;
- no hard wrap seam crosses the inspected surface;
- rain darkens and lowers roughness;
- returning to clear restores the original response;
- local non-authoritative disclosure is visible;
- 360-degree orbit remains responsive.

- [ ] **Step 6: Record the exact textual receipt**

Write the preview ID, material bundle ID, Blender identity, GLB/report/audit
hashes, screenshot private relative paths and hashes, tested URL, weather
observations, remaining visual defects, and the statement:

```text
This receipt proves a synthetic macOS L0 textured preview only. It does not
authorize tracked release replacement, measured geometry, real photo textures,
3DGS relighting, or arbitrary-coordinate textured chunk completion.
```

- [ ] **Step 7: Fix only evidence-backed defects with RED tests**

For each observed defect, add the narrowest failing automated assertion before
changing the responsible Task 1–7 file. Rebuild under a new bundle/preview ID;
never mutate the published private preview.

- [ ] **Step 8: Run full local gates**

Run:

```bash
python3 -m pytest tests/ -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
python3 -m ruff check pipeline tests
python3 -m compileall -q pipeline scripts
git diff --check
```

Expected: all gates pass. Platform skips must name the unavailable Windows
runtime explicitly.

- [ ] **Step 9: Commit and push the receipt and any verified fixes**

Stage explicit tracked paths only. Never stage `.nantai-studio` or private
screenshots.

```bash
git add docs/verification/2026-07-18-synthetic-pbr-local-preview.md
git commit -m "docs(verification): record textured macOS preview" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

---

### Task 9: Authoritative Windows Canary and Tracked Release Projection

**Files:**
- Create: `pipeline/synthetic_village/model_preview_release.py`
- Create: `tests/test_model_preview_release.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `web/data/recon/model-preview/manifest.json`
- Replace only after all gates pass: `web/data/recon/model-preview/village-canary.glb`
- Modify: `README.md`

**Interfaces:**
- Consumes: exact Task 2 material bundle on a Windows x64 host, locked Blender receipt, schema-v2 authoritative build, and Task 3 audit.
- Produces: `AuthoritativeModelPreviewCandidate`,
  `publish_authoritative_model_preview(...)`, CLI
  `publish-textured-canary`, tracked manifest v2, and tracked authoritative GLB.

- [ ] **Step 1: Write failing release-eligibility tests**

```python
def _authoritative_candidate(tmp_path):
    glb = tmp_path / "candidate/village-canary.glb"
    glb.parent.mkdir(parents=True)
    glb.write_bytes(b"verified textured glb fixture")
    return AuthoritativeModelPreviewCandidate(
        build_id="1" * 64,
        verification_level="L2",
        authoritative=True,
        geometry_usability="preview-only",
        synthetic=True,
        real_photo_textures=False,
        tool_platform="windows-x64",
        tool_version="4.5.11",
        runtime_build_hash="4db51e9d1e1e",
        glb_path=glb,
        glb_sha256=hashlib.sha256(glb.read_bytes()).hexdigest(),
        build_report_sha256="2" * 64,
        audit_sha256="3" * 64,
        material_count=24,
        texture_coverage=1.0,
        uv_coverage=1.0,
        tangent_coverage=1.0,
    )


@pytest.mark.parametrize(
    "patch",
    (
        {"verification_level": "L0"},
        {"authoritative": False},
        {"tool_platform": "macos-arm64"},
        {"geometry_usability": "metric-aligned"},
        {"real_photo_textures": True},
    ),
)
def test_release_projection_rejects_untrusted_or_overstated_input(tmp_path, patch):
    candidate = _authoritative_candidate(tmp_path).model_copy(update=patch)
    with pytest.raises(ModelPreviewReleaseError):
        publish_authoritative_model_preview(
            candidate=candidate,
            target_root=tmp_path / "web/data/recon/model-preview",
            expected_current_manifest_sha256="0" * 64,
        )
```

Add a positive test that copies exactly the audited GLB bytes, writes canonical
schema-v2 manifest bytes, refuses a changed current-manifest digest, and leaves
the old pair intact on any failure before the final two-file projection.

- [ ] **Step 2: Run focused tests and capture RED**

Run:

```bash
python3 -m pytest tests/test_model_preview_release.py -q
```

Expected: collection fails because the release module does not exist.

- [ ] **Step 3: Implement authoritative-only projection**

Eligibility requires all of:

```python
candidate.verification_level == "L2"
candidate.authoritative is True
candidate.tool_identity.platform == "windows-x64"
candidate.tool_identity.version == "4.5.11"
candidate.tool_identity.runtime_build_hash == "4db51e9d1e1e"
candidate.audit.material_count == 24
candidate.audit.texture_coverage == 1.0
candidate.audit.uv_coverage == 1.0
candidate.audit.tangent_coverage == 1.0
```

Re-hash all candidate files, compare the operator-supplied current manifest
digest, stage both tracked outputs in the target directory, flush, and replace
only after every check. The manifest remains
`geometry_usability=preview-only`, `synthetic=true`,
`real_photo_textures=false`.

- [ ] **Step 4: Run the authoritative Windows build**

On the Windows x64 machine containing the exact private source/material bundle:

```powershell
python scripts/synthetic_village.py build-textured-canary --timeout-seconds 1800
python scripts/synthetic_village.py audit-textured-glb --build-id <build-id>
python -m pytest tests/test_synthetic_village_blender_runtime.py -q
```

Expected: locked runtime identity matches, build/report/audit all pass, and no
runtime test skips.

- [ ] **Step 5: Publish the tracked release from the verified candidate**

```powershell
python scripts/synthetic_village.py publish-textured-canary `
  --build-id <build-id> `
  --expected-current-manifest-sha256 <current-manifest-sha256>
```

Expected: JSON reports the new GLB SHA, manifest SHA, build ID, and
`authoritative=true`.

- [ ] **Step 6: Run complete cross-platform gates**

Run locally after syncing the Windows-created tracked bytes:

```bash
python3 -m pytest tests/ -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
python3 -m ruff check pipeline tests
python3 -m compileall -q pipeline scripts
git diff --check
```

Expected: every test passes and the tracked manifest's GLB digest matches the
tracked GLB bytes.

- [ ] **Step 7: Update README truth and commit the authoritative release**

Document:

- synthetic derived PBR textures are available;
- real photo textures remain false;
- textured mesh weather relighting is available;
- 3DGS remains atmosphere-only;
- geometry remains preview-only;
- arbitrary-coordinate textured mesh chunks remain follow-on work.

Stage only:

```bash
git add \
  pipeline/synthetic_village/model_preview_release.py \
  scripts/synthetic_village.py \
  tests/test_model_preview_release.py \
  web/data/recon/model-preview/manifest.json \
  web/data/recon/model-preview/village-canary.glb \
  README.md
git commit -m "feat(viewer): publish authoritative textured canary" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>"
git push origin main
```

## Plan Self-Review Record

- **Spec coverage:** Tasks 1–2 cover source reuse, deterministic derivation,
  content identity, replacement, private storage, and fail-closed publication.
  Task 3 covers independent GLB evidence. Tasks 4–5 cover schema-v2 transport,
  UV/tangent/material binding, and embedded export. Task 6 covers the separate
  macOS L0 path and strict same-origin projection. Task 7 covers manifest truth
  plus six-weather mesh relighting and 3DGS overlay separation. Task 8 covers
  visual acceptance. Task 9 covers the authoritative Windows gate and tracked
  release.
- **Full-goal boundary:** The plan explicitly leaves textured arbitrary-coordinate
  chunks, walk/fly navigation, and measured hybrid 3DGS integration for the next
  plans; it never uses this finite textured preview as proof those are complete.
- **Placeholder scan:** The plan contains no unassigned implementation item,
  unnamed error handling, or shorthand that delegates an unspecified change.
- **Type consistency:** `DerivedMaterialBundle` feeds `MaterialInputRecord`;
  `TexturedBuildRequest` feeds Blender; `ExpectedGlbMaterial` audits the exported
  extras; the audit feeds local and authoritative preview manifests; Viewer
  consumes only those manifests and never private paths.
- **Shared-tree safety:** Every commit command lists explicit paths and excludes
  the existing weather-test modification.
- **Blender API feasibility:** The installed macOS Blender 4.5.11 LTS
  (`4db51e9d1e1e`) was probed on 2026-07-18. `Mesh.calc_tangents`,
  `ShaderNodeSeparateColor`, and `ShaderNodeSeparateRGB` all instantiate
  successfully; Task 5 still requires exported-byte auditing rather than
  treating API availability as proof of GLB output.
