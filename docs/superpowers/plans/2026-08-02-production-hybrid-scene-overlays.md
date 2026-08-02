# Nantai 3D Production Hybrid Scene Overlays Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add immutable, content-addressed mesh overlays to one verified real
3DGS base so Studio can preview, replace, commit and roll back assets while
Viewer and Production releases preserve the exact base identity and disclose
overlay trust honestly.

**Architecture:** Keep the accepted real-scene import immutable and publish a
separate composition closure containing a canonical manifest, exact v3 mesh
bundle/profile resources, rights evidence and append-only revision lineage.
Studio validates candidates through bounded server-side handles; Viewer loads
and verifies the base before adding meshes in the same ENU-metre camera/depth
pass; Production release schema v2 binds only the active accepted composition
and keeps schema v1 verification read-only compatible.

**Tech Stack:** Python 3.11+, Pydantic v2, current durable no-replace filesystem
primitives, pytest and Ruff; browser ES modules, Three.js, the existing verified
GLB/KTX2 resource store, Node.js 22 built-in tests, Playwright 1.62.0; current
Studio HTTP service and Production release builder/verifier.

**Design:**
`docs/superpowers/specs/2026-08-02-production-hybrid-scene-overlays-design.md`

## Global Constraints

- The accepted real 3DGS import root and `RealSceneImportReceipt` exact file set
  remain byte-for-byte unchanged by every composition operation.
- A composition is `real-base-with-modeled-overlays`; overlay geometry is
  `modeled-unverified`, placement is `operator-authored`, and
  `trust_effect=none`.
- Preview inputs remain Preview. Production requires an already accepted real
  base, exact composition machine acceptance, human acceptance and public
  distribution rights for every active profile resource.
- The existing H3 source receipts have
  `public_release_authorized=false`; they may drive private Preview candidates
  but must fail the Production release gate until a separate exact public
  distribution receipt is supplied.
- Transforms are right-handed ENU metres with finite translation, unit
  quaternion and positive uniform scale. Reflection, shear, non-uniform scale,
  NaN/Inf and zero scale fail closed.
- Viewer loads and verifies the base first. Overlay failure is visible and may
  offer an explicit base-only action; it never silently reports hybrid success.
- Work only on `main` in the one shared worktree. Before each task, inspect
  `git status --short --branch` and preserve unrelated work.
- Stage and commit only the paths named by the current task. Never use
  `git add -A`, `git commit -a`, reset, checkout, stash or rebase.
- Start implementation tasks with `superpowers:test-driven-development` and
  run `superpowers:verification-before-completion` before each commit.
- Every Codex commit ends with:

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

- Push every green checkpoint with:

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- Modeled fixtures prove contracts only. Do not mark the original
  replaceable-assets requirement complete until Task 9 closes on one accepted
  real scene.

---

## File Structure

### Core composition and publication

- Create `pipeline/scene_composition.py`: canonical data models, base binding,
  v3 mesh/profile adapter, transform/bounds verification and composition
  verifier.
- Create `tests/scene_composition_fixtures.py`: explicit synthetic/Preview
  modeled fixtures with no Production claim.
- Create `tests/test_scene_composition.py`: canonical identity, adapter,
  transform and negative-matrix tests.
- Create `pipeline/scene_composition_publication.py`: candidate preparation,
  exact whitelist, durable no-replace publication and append-only selection
  chain.
- Create `tests/test_scene_composition_publication.py`: duplicate submission,
  lineage, crash residue and rollback tests.

### Studio and Viewer

- Modify `pipeline/studio_server.py`: verified composition snapshots, bounded
  candidate handles, read APIs and capability-gated commit/activate APIs.
- Modify `tests/test_studio_server.py`: endpoint, authorization, stale-handle,
  path-safety and read-only tests.
- Modify `web/studio/local-adapter.mjs` and its test: composition API methods.
- Create `web/studio/scene-composition.mjs` and its test: fail-closed Studio
  view model and candidate transaction state.
- Modify `web/studio/app.js`, `index.html`, `styles.css` and contract tests:
  base/composition cards, slot editor, compare, commit and rollback UX.
- Create `web/viewer/scene-composition.mjs` and its test: canonical projection,
  exact resource resolution, transforms and visible failure state.
- Modify `web/viewer/main.js`, `index.html`, `bridge.mjs` and tests: simultaneous
  splat/mesh render, `hybrid`/`base`/`overlays` modes and disclosure HUD.

### Acceptance and release

- Create `pipeline/scene_composition_acceptance.py`: hybrid machine/human
  evidence and accepted-decision contract.
- Create `tests/test_scene_composition_acceptance.py`: identity, category and
  evidence-byte negative matrix.
- Create `scripts/capture_scene_composition_acceptance.mjs`: receipt-bound
  Playwright hybrid/base/failed-fetch capture.
- Create `scripts/capture_scene_composition_acceptance.test.mjs`: route,
  browser-state and bounded-report tests.
- Modify `pipeline/production_release_contract.py`,
  `production_release_builder.py`, `production_release_verifier.py` and their
  tests: v2 optional composition closure that is mandatory when active.
- Modify `scripts/build_production_release.py`,
  `scripts/verify_production_release.py`, `make.py` and tests: explicit
  composition input and verification output.
- Modify `.github/workflows/ci.yml`, `package.json`, docs and status: focused
  cross-platform contract matrix and truthful completion boundary.

## Phase 1 — Canonical Composition Contract

### Task 1: Base binding, mesh adapter and canonical composition verifier

**Files:**

- Create: `pipeline/scene_composition.py`
- Create: `tests/scene_composition_fixtures.py`
- Create: `tests/test_scene_composition.py`

**Interfaces:**

- Consumes: `validate_real_scene_import_receipt(...)`,
  `load_mesh_asset_bundle_v3(...)`, `read_verified_mesh_variant_glb(...)`,
  `read_verified_mesh_texture_v3(...)`, `measure_mesh_template_enu_bounds(...)`
  and canonical acceptance evidence.
- Produces: `SceneCompositionManifest`, `VerifiedOverlayBundle`,
  `VerifiedSceneComposition`, `canonical_scene_composition_bytes(...)`,
  `load_scene_composition_bytes(...)`, `bind_scene_base(...)`,
  `verify_overlay_bundle(...)`, `compose_scene_composition(...)` and
  `verify_scene_composition(...)`.

- [ ] **Step 1: Write RED canonical identity and transform tests**

Create the exact public models and assertions used by downstream tasks:

```python
from pipeline.scene_composition import (
    BaseSceneBinding,
    OverlayDistributionRightsReceipt,
    OverlaySlot,
    SceneTransform,
    canonical_scene_composition_bytes,
    compose_scene_composition,
    transform_bounds,
)


def test_replacement_changes_composition_not_base(modeled_base, overlay_bundle):
    first = compose_scene_composition(
        base=modeled_base,
        bundles=(overlay_bundle.binding,),
        slots=(overlay_slot("village_house", version=1),),
        parent=None,
        operation=RevisionOperation(
            kind="initial",
            changed_slot_ids=("house-front",),
        ),
    )
    second = compose_scene_composition(
        base=modeled_base,
        bundles=(overlay_bundle.binding,),
        slots=(overlay_slot("stone_bridge", version=2),),
        parent=first,
        operation=RevisionOperation(
            kind="single-slot",
            changed_slot_ids=("house-front",),
        ),
    )
    assert first.composition_id != second.composition_id
    assert first.base == second.base == modeled_base
    assert second.slots[0].slot_id == first.slots[0].slot_id
    assert second.slots[0].slot_version == first.slots[0].slot_version + 1


def test_enu_transform_uses_all_eight_aabb_corners():
    result = transform_bounds(
        local_bounds={"min": (-1.0, -2.0, 0.0), "max": (1.0, 2.0, 3.0)},
        transform=SceneTransform(
            translation_enu_m=(10.0, 20.0, 1.0),
            rotation_xyzw=(0.0, 0.0, 2**-0.5, 2**-0.5),
            uniform_scale=2.0,
        ),
    )
    assert result.min == (6.0, 18.0, 1.0)
    assert result.max == (14.0, 22.0, 7.0)
```

Parameterize rejection of `NaN`, `Inf`, quaternion norm outside `1e-6`, zero
or negative scale, extra keys, duplicate/case-folded slot IDs, unsorted bundle
bindings, mismatched frame/units, declared bounds drift over `1e-5 m`, stale
parent SHA, undeclared changed slots, a `single-slot` transaction that changes
zero/multiple slots and a `multi-slot` transaction that changes fewer than two.

- [ ] **Step 2: Write RED bundle and rights adapter tests**

Use an actual generated v3 fixture root. Assert the adapter reopens the v3
manifest and selected LOD/profile bytes rather than trusting copied fields:

```python
def test_private_h3_rights_allow_preview_but_block_production(v3_root, rights_path):
    preview = verify_overlay_bundle(
        bundle_root=v3_root,
        profile_id="h3-ai-ktx2-4k",
        rights_receipt_path=rights_path,
        publication_role="preview",
    )
    assert preview.binding.trust_effect == "none"
    with pytest.raises(SceneCompositionError, match="public distribution"):
        verify_overlay_bundle(
            bundle_root=v3_root,
            profile_id="h3-ai-ktx2-4k",
            rights_receipt_path=rights_path,
            publication_role="production",
        )


def test_adapter_rejects_glb_changed_after_manifest_load(v3_root, rights_path):
    tamper_verified_object_during_read(v3_root)
    with pytest.raises(SceneCompositionError, match="resource changed"):
        verify_overlay_bundle(
            bundle_root=v3_root,
            profile_id="h2-png-1k-fallback",
            rights_receipt_path=rights_path,
            publication_role="preview",
        )
```

Mutate manifest bytes, GLB, KTX2/PNG, geometry fingerprint, selected profile,
source bundle IDs, local bounds, source receipt SHA and rights bundle/profile
binding independently. Include symlink/reparse, redirect, external URI,
case-fold path collision, extra resource and missing resource cases.

- [ ] **Step 3: Run RED**

```powershell
python -m pytest -q tests/test_scene_composition.py
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'pipeline.scene_composition'`.

- [ ] **Step 4: Implement the exact contract API**

Use frozen, strict, `extra="forbid"` Pydantic models and these signatures:

```python
SCENE_COMPOSITION_SCHEMA = "nantai.scene-composition-manifest.v1"
OVERLAY_RIGHTS_SCHEMA = "nantai.overlay-distribution-rights.v1"


class SceneCompositionError(ValueError):
    pass


class ContentBinding(FrozenModel):
    sha256: Sha256
    byte_length: int = Field(ge=1)


class BaseSceneBinding(FrozenModel):
    import_receipt: ContentBinding
    scene_identity: str = Field(pattern=r"^scene-[0-9a-f]{64}$")
    reconstruction_manifest: ContentBinding
    target_frame_id: str = Field(min_length=1, max_length=128)
    target_units: Literal["arbitrary", "meters"]
    axis_convention: Literal["right-handed-enu"]
    geometry_usability: Literal["preview-only", "metric-aligned"]
    acceptance_report: ContentBinding | None
    acceptance_decision: ContentBinding | None
    production_release_allowed: bool


class OverlayDistributionRightsReceipt(FrozenModel):
    schema_id: Literal["nantai.overlay-distribution-rights.v1"] = Field(
        default=OVERLAY_RIGHTS_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    bundle_id: Sha256
    allowed_profile_ids: tuple[
        Literal["h3-ai-ktx2-4k", "h2-png-1k-fallback"], ...
    ]
    source_receipt_sha256: Sha256
    review_reference_sha256: Sha256
    redistribution_allowed: bool
    release_inclusion_allowed: bool


class OverlayResourceBinding(FrozenModel):
    object_path: str
    role: Literal["glb", "texture"]
    media_type: Literal["model/gltf-binary", "image/png", "image/ktx2"]
    sha256: Sha256
    byte_length: int = Field(ge=1)


class OverlayLodBinding(FrozenModel):
    lod: Literal["0", "1", "2"]
    geometry_fingerprint: Sha256
    local_bounds: Bounds3
    glb_sha256: Sha256
    resource_sha256s: tuple[Sha256, ...]


class OverlayAssetBinding(FrozenModel):
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: Literal["building", "vegetation", "prop"]
    lods: tuple[OverlayLodBinding, OverlayLodBinding, OverlayLodBinding]
    local_bounds_union: Bounds3


class OverlayBundleBinding(FrozenModel):
    bundle_schema: Literal[
        "nantai.synthetic-village.mesh-asset-bundle.v3"
    ]
    bundle_id: Sha256
    manifest: ContentBinding
    profile_id: Literal["h3-ai-ktx2-4k", "h2-png-1k-fallback"]
    source_mesh_bundle_id: Sha256
    source_material_bundle_id: Sha256
    rights_receipt: ContentBinding
    assets: tuple[OverlayAssetBinding, ...]
    resources: tuple[OverlayResourceBinding, ...]
    synthetic: Literal[True]
    real_photo_textures: Literal[False]
    geometry_usability: Literal["preview-only"]
    trust_effect: Literal["none"]


class SceneTransform(FrozenModel):
    translation_enu_m: tuple[float, float, float]
    rotation_xyzw: tuple[float, float, float, float]
    uniform_scale: float = Field(gt=0.0, allow_inf_nan=False)


class OverlaySlot(FrozenModel):
    slot_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    slot_version: int = Field(ge=1)
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    bundle_id: Sha256
    profile_id: Literal["h3-ai-ktx2-4k", "h2-png-1k-fallback"]
    transform: SceneTransform
    target_frame_id: str
    target_units: Literal["meters"]
    local_bounds: Bounds3
    world_bounds: Bounds3
    placement_evidence: Literal["operator-authored"]
    semantic_label: str | None
    enabled: bool
    predecessor_slot_sha256: Sha256 | None


class RevisionOperation(FrozenModel):
    kind: Literal["initial", "single-slot", "multi-slot"]
    changed_slot_ids: tuple[str, ...]


class SceneCompositionManifest(FrozenModel):
    schema_id: Literal["nantai.scene-composition-manifest.v1"] = Field(
        default=SCENE_COMPOSITION_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    composition_id: str = Field(pattern=r"^composition-[0-9a-f]{64}$")
    parent_composition_sha256: Sha256 | None
    base: BaseSceneBinding
    overlay_bundles: tuple[OverlayBundleBinding, ...]
    slots: tuple[OverlaySlot, ...]
    revision_operation: RevisionOperation
    composition_kind: Literal["real-base-with-modeled-overlays"]
    overlay_geometry: Literal["modeled-unverified"]
    placement_frame: Literal["world-enu"]
    trust_effect: Literal["none"]
    acceptance_state: Literal["preview", "review-pending"]


@dataclass(frozen=True)
class VerifiedOverlayBundle:
    binding: OverlayBundleBinding
    bundle_root: Path
    rights_receipt_path: Path
    resource_paths: Mapping[str, Path]


@dataclass(frozen=True)
class VerifiedSceneComposition:
    manifest: SceneCompositionManifest
    manifest_sha256: str
    manifest_bytes: bytes
    base: BaseSceneBinding
    bundles: tuple[VerifiedOverlayBundle, ...]


def canonical_scene_composition_bytes(
    manifest: SceneCompositionManifest,
    *,
    exclude_composition_id: bool = False,
) -> bytes: ...


def load_scene_composition_bytes(payload: bytes) -> SceneCompositionManifest: ...


def bind_scene_base(
    *,
    import_receipt_path: Path,
    acceptance_report_path: Path | None,
) -> BaseSceneBinding: ...


def verify_overlay_bundle(
    *,
    bundle_root: Path,
    profile_id: str,
    rights_receipt_path: Path,
    publication_role: Literal["preview", "production"],
) -> VerifiedOverlayBundle: ...


def compose_scene_composition(
    *,
    base: BaseSceneBinding,
    bundles: tuple[OverlayBundleBinding, ...],
    slots: tuple[OverlaySlot, ...],
    parent: SceneCompositionManifest | None,
    operation: RevisionOperation,
) -> SceneCompositionManifest: ...


def verify_scene_composition(
    *,
    manifest_path: Path,
    import_receipt_path: Path,
    acceptance_report_path: Path | None,
    bundle_roots: Mapping[str, Path],
    rights_receipt_paths: Mapping[str, Path],
    expected_parent: SceneCompositionManifest | None = None,
    publication_role: Literal["preview", "production"],
) -> VerifiedSceneComposition: ...
```

Canonical JSON is UTF-8, LF-terminated, sorted, `ensure_ascii=True`, compact
and `allow_nan=False`. Derive `composition_id` as `composition-` plus SHA-256
of canonical bytes excluding `composition_id`; bind the full canonical
manifest SHA separately in receipts. Recompute local GLB bounds from verified
bytes, transform all eight corners independently, and compare every declared
component with absolute tolerance `1e-5`. Bind the exact ordered LOD 0/1/2
closure and derive one union AABB for every referenced asset; slot/world bounds
bind that union so an LOD switch cannot escape the accepted envelope. The
immutable manifest can say only
`preview` or `review-pending`; accepted/rejected state comes only from Task 7
evidence and cannot be self-authored into the manifest. Compare every parent
slot against `changed_slot_ids`: unchanged slots are byte-equal, changed slots
increment by exactly one, and multi-slot revisions name every changed slot.

- [ ] **Step 5: Run GREEN and the adjacent import/bundle gates**

```powershell
python -m pytest -q \
  tests/test_scene_composition.py \
  tests/test_real_scene_import.py \
  tests/test_mesh_asset_bundle_v3.py \
  tests/test_material_bundle_v2.py
python -m ruff check \
  pipeline/scene_composition.py \
  tests/scene_composition_fixtures.py \
  tests/test_scene_composition.py
git diff --check -- \
  pipeline/scene_composition.py \
  tests/scene_composition_fixtures.py \
  tests/test_scene_composition.py
```

Expected: all tests pass; no import or v3 bundle behavior changes.

- [ ] **Step 6: Commit and push Phase 1**

```powershell
git add -- pipeline/scene_composition.py tests/scene_composition_fixtures.py tests/test_scene_composition.py
git commit -m "feat: add fail-closed scene composition contract" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/scene_composition.py tests/scene_composition_fixtures.py tests/test_scene_composition.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

## Phase 2 — Immutable Publication and Lineage

### Task 2: Durable composition publication and append-only selection chain

**Files:**

- Create: `pipeline/scene_composition_publication.py`
- Create: `tests/test_scene_composition_publication.py`

**Interfaces:**

- Consumes: `VerifiedSceneComposition`, `publish_directory_noreplace(...)`,
  `write_file_atomic(...)`, directory/file flush primitives and the existing
  Studio writer lock.
- Produces: `SceneCompositionReceipt`, `SceneCompositionSelection`,
  `prepare_scene_composition_publication(...)`,
  `publish_scene_composition(...)`, `verify_scene_composition_publication(...)`,
  `append_scene_composition_selection(...)` and
  `load_active_scene_composition(...)`.

- [ ] **Step 1: Write RED publication, replay and rollback tests**

```python
def test_duplicate_publication_reuses_identical_receipt(
    tmp_path, verified_composition
):
    first = publish_scene_composition(
        studio_root=tmp_path / ".nantai-studio",
        verified=verified_composition,
        publisher_source_commit="a" * 40,
        publisher_tree_clean=True,
    )
    second = publish_scene_composition(
        studio_root=tmp_path / ".nantai-studio",
        verified=verified_composition,
        publisher_source_commit="a" * 40,
        publisher_tree_clean=True,
    )
    assert first.reused is False
    assert second.reused is True
    assert second.receipt == first.receipt
    assert second.final_directory == first.final_directory


def test_rollback_appends_selection_without_mutating_revisions(
    tmp_path, published_first, published_second
):
    activate_second = append_scene_composition_selection(
        studio_root=tmp_path / ".nantai-studio",
        scene_identity=published_first.receipt.base.scene_identity,
        composition_receipt_path=published_second.receipt_path,
        expected_previous_selection_sha256=None,
        reason="operator-commit",
    )
    rollback = append_scene_composition_selection(
        studio_root=tmp_path / ".nantai-studio",
        scene_identity=published_first.receipt.base.scene_identity,
        composition_receipt_path=published_first.receipt_path,
        expected_previous_selection_sha256=activate_second.selection_sha256,
        reason="audited-rollback",
    )
    assert rollback.sequence == activate_second.sequence + 1
    assert published_first.receipt_path.read_bytes() == published_first.receipt_bytes
    assert published_second.receipt_path.read_bytes() == published_second.receipt_bytes
```

Also test exact whitelist order, no receipt self-recursion, candidate/private
path omission, stale parent, selection fork, case-fold collision, existing
different destination, link/reparse, file replacement during copy, pre/post
fsync failure, no-replace failure residue and active-chain gap.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest -q tests/test_scene_composition_publication.py
```

Expected: missing publication module.

- [ ] **Step 3: Implement receipt and selection contracts**

```python
COMPOSITION_RECEIPT_SCHEMA = "nantai.scene-composition-receipt.v1"
COMPOSITION_SELECTION_SCHEMA = "nantai.scene-composition-selection.v1"


class SceneCompositionPublicationError(ValueError):
    pass


class CompositionArtifactBinding(FrozenModel):
    path: str
    role: Literal[
        "composition-manifest",
        "overlay-bundle-manifest",
        "overlay-glb",
        "overlay-texture",
        "overlay-rights",
        "validation-policy",
        "validation-decision",
    ]
    byte_length: int = Field(ge=1)
    sha256: Sha256


class SceneCompositionReceipt(FrozenModel):
    schema_id: Literal["nantai.scene-composition-receipt.v1"] = Field(
        default=COMPOSITION_RECEIPT_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    composition_id: str
    composition_manifest: ContentBinding
    base: BaseSceneBinding
    validation_policy: ContentBinding
    validation_decision: ContentBinding
    validation_accepted: Literal[True]
    artifacts: tuple[CompositionArtifactBinding, ...]
    source_rights_receipts: tuple[ContentBinding, ...]
    publisher_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    publisher_tree_clean: Literal[True]
    trust_effect: Literal["none"]


class SceneCompositionSelection(FrozenModel):
    schema_id: Literal["nantai.scene-composition-selection.v1"] = Field(
        default=COMPOSITION_SELECTION_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    selection_id: str = Field(pattern=r"^selection-[0-9a-f]{64}$")
    scene_identity: str
    sequence: int = Field(ge=1)
    previous_selection_sha256: Sha256 | None
    composition_receipt_sha256: Sha256
    composition_id: str
    reason: Literal["operator-commit", "audited-rollback"]


@dataclass(frozen=True)
class PreparedSceneCompositionPublication:
    staging_root: Path
    receipt: SceneCompositionReceipt
    receipt_bytes: bytes


@dataclass(frozen=True)
class SceneCompositionPublicationResult:
    final_directory: Path
    receipt_path: Path
    receipt: SceneCompositionReceipt
    receipt_bytes: bytes
    reused: bool


@dataclass(frozen=True)
class VerifiedPublishedComposition:
    root: Path
    receipt: SceneCompositionReceipt
    manifest: SceneCompositionManifest
    artifacts: Mapping[str, Path]


@dataclass(frozen=True)
class ActiveSceneComposition:
    selection: SceneCompositionSelection
    publication: VerifiedPublishedComposition


def prepare_scene_composition_publication(
    *,
    staging_parent: Path,
    verified: VerifiedSceneComposition,
    publisher_source_commit: str,
    publisher_tree_clean: bool,
) -> PreparedSceneCompositionPublication: ...


def publish_scene_composition(
    *,
    studio_root: Path,
    verified: VerifiedSceneComposition,
    publisher_source_commit: str,
    publisher_tree_clean: bool,
) -> SceneCompositionPublicationResult: ...


def verify_scene_composition_publication(
    receipt_path: Path,
) -> VerifiedPublishedComposition: ...


def append_scene_composition_selection(
    *,
    studio_root: Path,
    scene_identity: str,
    composition_receipt_path: Path,
    expected_previous_selection_sha256: str | None,
    reason: Literal["operator-commit", "audited-rollback"],
) -> SceneCompositionSelection: ...


def load_active_scene_composition(
    *,
    studio_root: Path,
    scene_identity: str,
) -> ActiveSceneComposition | None: ...
```

Publish below
`.nantai-studio/scene-compositions/<scene-identity>/<composition-id>/` and
selection events below
`.nantai-studio/scene-composition-selections/<scene-identity>/<sequence>-<selection-id>/`.
Determine active state only by reopening the complete contiguous selection
chain; never trust directory mtime or lexicographic ID alone.

- [ ] **Step 4: Verify cross-platform read behavior and mutation boundaries**

```powershell
python -m pytest -q \
  tests/test_scene_composition_publication.py \
  tests/test_durable_io.py \
  tests/test_studio_writer_lock.py
python -m pytest -q -m production_mutation tests/test_scene_composition_publication.py
```

Expected: portable read/verification passes everywhere; append-only mutation
runs only on a filesystem backend that passes the existing local self-test.

- [ ] **Step 5: Ruff and diff gate**

```powershell
python -m ruff check pipeline/scene_composition_publication.py tests/test_scene_composition_publication.py
git diff --check -- pipeline/scene_composition_publication.py tests/test_scene_composition_publication.py
```

- [ ] **Step 6: Commit and push Phase 2**

```powershell
git add -- pipeline/scene_composition_publication.py tests/test_scene_composition_publication.py
git commit -m "feat: publish immutable scene composition revisions" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/scene_composition_publication.py tests/test_scene_composition_publication.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

## Phase 3 — Studio and Hybrid Viewer

### Task 3: Studio composition API and bounded candidate handles

**Files:**

- Modify: `pipeline/studio_server.py`
- Modify: `tests/test_studio_server.py`
- Modify: `web/studio/local-adapter.mjs`
- Modify: `web/studio/local-adapter.test.mjs`

**Interfaces:**

- Consumes: Task 2 publication/selection API and current capability lease.
- Produces: GET composition state/resources and POST validate/commit/activate
  routes; `LocalAdapter.loadCompositions()`, `validateCompositionCandidate()`,
  `commitCompositionCandidate()` and `activateComposition()`.

- [ ] **Step 1: Write RED server route and capability tests**

Exercise these exact routes:

```text
GET  /api/scene-compositions
GET  /api/scene-compositions/active
GET  /api/scene-compositions/<composition-id>/manifest.json
GET  /api/scene-compositions/<composition-id>/resources/<portable-path>
POST /api/scene-composition-candidates/validate
GET  /api/scene-composition-candidates/<candidate-id>/manifest.json
GET  /api/scene-composition-candidates/<candidate-id>/resources/<portable-path>
POST /api/scene-composition-candidates/<candidate-id>/commit
POST /api/scene-compositions/<composition-id>/activate
```

The validate body contains only stable slot/profile IDs, finite transform
values, base receipt SHA and server-known bundle IDs; it never accepts an
arbitrary client filesystem path or URL.

```python
def test_read_only_server_can_compare_but_cannot_commit(studio_server):
    response = studio_server.post_json(
        "/api/scene-composition-candidates/validate",
        valid_candidate_request(),
    )
    assert response.status == 200
    assert response.json()["candidate"]["state"] == "validated-uncommitted"
    commit = studio_server.post_json(
        f"/api/scene-composition-candidates/{response.json()['candidate']['id']}/commit",
        {},
    )
    assert commit.status == 403
    assert commit.json()["error"]["code"] == "write_unavailable"
```

Test wrong Host, missing/future lease, stale request ID, body over limit,
unknown bundle/slot, expired candidate, candidate bytes drift, wrong base,
stale parent and concurrent commit. Public errors must contain no local path,
hash dump, token, rights text or environment value.

- [ ] **Step 2: Write RED adapter tests**

```js
test('adapter validates then commits one opaque candidate handle', async () => {
  const adapter = new LocalAdapter({ fetchImpl, baseUrl: 'http://studio.test' });
  const candidate = await adapter.validateCompositionCandidate(request);
  assert.equal(candidate.state, 'validated-uncommitted');
  await adapter.commitCompositionCandidate(candidate.id, {
    requestId: 'req-00000001',
  });
  assert.deepEqual(calls.map((row) => row.url), [
    'http://studio.test/api/scene-composition-candidates/validate',
    `http://studio.test/api/scene-composition-candidates/${candidate.id}/commit`,
  ]);
});
```

- [ ] **Step 3: Run RED**

```powershell
python -m pytest -q tests/test_studio_server.py -k composition
node --test web/studio/local-adapter.test.mjs
```

Expected: routes/methods absent.

- [ ] **Step 4: Implement server-owned candidates and exact projections**

Add a bounded in-memory `CompositionCandidateStore` with at most 32 entries,
15-minute monotonic expiry, opaque random 128-bit IDs and immutable expected
SHA bindings. Reopen all base/bundle/rights bytes on commit; a candidate handle
is not authority. Serve verified content-addressed bytes with immutable ETag;
serve list/active/candidate envelopes `no-store`.

Capability commands are exact:

```python
"composition-validate": {"enabled": True, "reason": "read-only validation"},
"composition-commit": {"enabled": write_enabled, "reason": write_reason},
"composition-activate": {"enabled": write_enabled, "reason": write_reason},
```

Add adapter methods without inferring write support from method presence.

- [ ] **Step 5: Run focused and full Studio gates**

```powershell
python -m pytest -q tests/test_studio_server.py tests/test_studio_capabilities.py tests/test_studio_publication.py
node --test web/studio/*.test.mjs
python -m ruff check pipeline/studio_server.py tests/test_studio_server.py
git diff --check -- pipeline/studio_server.py tests/test_studio_server.py web/studio/local-adapter.mjs web/studio/local-adapter.test.mjs
```

- [ ] **Step 6: Commit and push Studio API checkpoint**

```powershell
git add -- pipeline/studio_server.py tests/test_studio_server.py web/studio/local-adapter.mjs web/studio/local-adapter.test.mjs
git commit -m "feat: expose verified composition revisions in Studio" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/studio_server.py tests/test_studio_server.py web/studio/local-adapter.mjs web/studio/local-adapter.test.mjs
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 4: Studio replacement, compare, commit and rollback UX

**Files:**

- Create: `web/studio/scene-composition.mjs`
- Create: `web/studio/scene-composition.test.mjs`
- Modify: `web/studio/app.js`
- Modify: `web/studio/index.html`
- Modify: `web/studio/styles.css`
- Modify: `web/studio/index-contract.test.mjs`
- Modify: `web/studio/viewer-bridge.mjs`
- Modify: `web/studio/viewer-bridge.test.mjs`

**Interfaces:**

- Consumes: Task 3 API and Viewer bridge commands from Task 6 (feature-gated
  until available).
- Produces: normalized composition state, accessible slot editor, candidate
  compare and explicit commit/rollback confirmation.

- [ ] **Step 1: Write RED pure state-machine tests**

```js
test('candidate never becomes active without a matching commit receipt', () => {
  let state = createCompositionEditorState(activeFixture);
  state = beginCandidate(state, requestFixture);
  state = acceptValidatedCandidate(state, candidateFixture);
  assert.equal(compositionEditorView(state).commit_enabled, true);
  assert.equal(compositionEditorView(state).active_composition_id,
    activeFixture.composition_id);
  assert.throws(
    () => acceptCommit(state, { composition_id: 'composition-' + 'f'.repeat(64) }),
    /candidate identity/,
  );
});
```

Test disabled writes, validation failure, stale parent, retry, base-only
fallback, selected/current/candidate identity display, rollback confirmation
and disclosure strings. Unknown trust fields must render `unknown / blocked`,
never a Production-success badge.

- [ ] **Step 2: Write RED DOM contract tests**

Require semantic cards and controls:

```html
<section id="base-reconstruction-card" aria-labelledby="base-reconstruction-title">
<section id="active-composition-card" aria-labelledby="active-composition-title">
<form id="overlay-slot-editor">
<button id="composition-compare-base" type="button">
<button id="composition-compare-candidate" type="button">
<button id="composition-commit" type="submit">
<button id="composition-rollback" type="button">
<p id="composition-disclosure" role="status" aria-live="polite">
```

Assert the source contains no `innerHTML` for API data, no browser prompt,
no `fully-real`, `reconstructed-overlay` or `measured-overlay`, and no commit
enablement without `composition-commit` capability.

- [ ] **Step 3: Run RED**

```powershell
node --test web/studio/scene-composition.test.mjs web/studio/index-contract.test.mjs web/studio/viewer-bridge.test.mjs
```

- [ ] **Step 4: Implement the editor and truthful UX**

Export from `scene-composition.mjs`:

```js
export function normalizeCompositionSnapshot(value) {}
export function createCompositionEditorState(active) {}
export function beginCandidate(state, request) {}
export function acceptValidatedCandidate(state, candidate) {}
export function acceptCommit(state, receipt) {}
export function acceptActivation(state, selection) {}
export function compositionEditorView(state) {}
```

The view model emits fixed disclosures:

```js
{
  base_label: 'real 3DGS / metric-aligned / production-accepted',
  overlay_label: 'modeled-unverified / operator-authored / trust effect none',
  composition_label: 'hybrid / review pending',
}
```

Use bounded numeric inputs for east/north/up, quaternion and uniform scale.
Compare buttons call Viewer `setCompositionMode('base')` and load the opaque
candidate URL before `setCompositionMode('hybrid')`. Commit and rollback both
show exact current/target IDs and require an explicit user click; never auto
commit after validation.

- [ ] **Step 5: Run Studio tests and browser-width static checks**

```powershell
node --test web/studio/*.test.mjs
python -m pytest -q tests/test_studio_server.py -k composition
git diff --check -- web/studio/scene-composition.mjs web/studio/scene-composition.test.mjs web/studio/app.js web/studio/index.html web/studio/styles.css web/studio/index-contract.test.mjs web/studio/viewer-bridge.mjs web/studio/viewer-bridge.test.mjs
```

At 1280×720 and 390×844, the slot form, disclosure and commit state remain
visible or keyboard-scrollable without covering the embedded Viewer.

- [ ] **Step 6: Commit and push Studio UX checkpoint**

```powershell
git add -- web/studio/scene-composition.mjs web/studio/scene-composition.test.mjs web/studio/app.js web/studio/index.html web/studio/styles.css web/studio/index-contract.test.mjs web/studio/viewer-bridge.mjs web/studio/viewer-bridge.test.mjs
git commit -m "feat: add audited overlay replacement UX" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- web/studio/scene-composition.mjs web/studio/scene-composition.test.mjs web/studio/app.js web/studio/index.html web/studio/styles.css web/studio/index-contract.test.mjs web/studio/viewer-bridge.mjs web/studio/viewer-bridge.test.mjs
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 5: Pure Viewer composition loader and overlay layer runtime

**Files:**

- Create: `web/viewer/scene-composition.mjs`
- Create: `web/viewer/scene-composition.test.mjs`

**Interfaces:**

- Consumes: Task 1 JSON projection, same-origin fetch, SHA-256 and
  `createVerifiedMeshResourceStore(...)`.
- Produces: `validateSceneCompositionManifest(...)`,
  `resolveCompositionResources(...)`, `compositionViewModel(...)` and
  `createSceneCompositionLayer(...)`.

- [ ] **Step 1: Write RED manifest, fetch and transform tests**

```js
test('layer loads exact resources and maps ENU to Three once', async () => {
  const layer = createSceneCompositionLayer({
    THREE,
    scene,
    resourceStore,
    fetchImpl,
    sha256Hex,
  });
  await layer.load(manifestUrl, {
    expectedBase: baseBinding,
    mode: 'hybrid',
  });
  assert.deepEqual(resourceStore.loaded.map(({ asset_id, profile_id }) =>
    [asset_id, profile_id]), [['village_house', 'h2-png-1k-fallback']]);
  assert.deepEqual(scene.getObjectByName('overlay:house-front').position.toArray(),
    [12, 3, -8]);
  assert.equal(layer.snapshot().state, 'ready');
});
```

Reject cross-origin manifest/resource URLs, encoded traversal, redirects,
wrong content type, size/hash drift, duplicate keys/IDs, unknown profile,
base mismatch, frame/units mismatch, invalid quaternion/scale/bounds, resource
not consumed exactly, late fetch after dispose and unbounded cache growth. Move
the camera across both fixed LOD thresholds and assert that the acquired GLB
descriptor changes while `asset_id`, slot transform and world bounds stay
exactly unchanged.

- [ ] **Step 2: Write RED visible-failure and disposal tests**

```js
test('failed active overlay cannot report hybrid success', async () => {
  const layer = createSceneCompositionLayer({
    THREE, scene, resourceStore: failingStore, fetchImpl, sha256Hex,
  });
  await assert.rejects(() => layer.load(manifestUrl, {
    expectedBase: baseBinding,
    mode: 'hybrid',
  }));
  assert.deepEqual(layer.snapshot(), {
    state: 'failed',
    mode: 'hybrid',
    active_slots: 0,
    failed_slots: 1,
    disclosure: 'Overlay failed · verified base remains available',
  });
});
```

- [ ] **Step 3: Run RED**

```powershell
node --test web/viewer/scene-composition.test.mjs
```

- [ ] **Step 4: Implement the isolated layer**

```js
export const SCENE_COMPOSITION_SCHEMA =
  'nantai.scene-composition-manifest.v1';

export function validateSceneCompositionManifest(value, expectedBase) {}
export function resolveCompositionResources(manifestUrl, manifest, origin) {}
export function compositionViewModel(snapshot) {}

export function createSceneCompositionLayer({
  THREE,
  scene,
  resourceStore,
  fetchImpl = fetch,
  sha256Hex,
}) {
  return Object.freeze({
    load,
    setMode,
    update,
    snapshot,
    dispose,
  });
}
```

For each enabled slot, acquire the selected verified template, deep-clone the
scene while sharing immutable geometry/material resources, apply uniform scale
and normalized quaternion, then map ENU `(east,north,up)` to Three
`(east,up,-north)`. `update(cameraPositionThree)` selects only the bound LOD
0/1/2 descriptors through fixed distance thresholds, preserves the slot
transform and asset identity, and releases the superseded descriptor after the
replacement is ready. Release the exact descriptor when a slot/revision
unloads. The layer owns no trust promotion and returns bounded counts/IDs,
never URLs, raw hashes or bytes in diagnostics.

- [ ] **Step 5: Run Viewer resource regressions**

```powershell
node --test web/viewer/scene-composition.test.mjs web/viewer/verified-mesh-resources.test.mjs web/viewer/coordinates.test.mjs
git diff --check -- web/viewer/scene-composition.mjs web/viewer/scene-composition.test.mjs
```

- [ ] **Step 6: Commit and push isolated Viewer layer**

```powershell
git add -- web/viewer/scene-composition.mjs web/viewer/scene-composition.test.mjs
git commit -m "feat: load verified composition overlay layers" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- web/viewer/scene-composition.mjs web/viewer/scene-composition.test.mjs
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 6: Hybrid Viewer startup, modes, occlusion and disclosure

**Files:**

- Modify: `web/viewer/main.js`
- Modify: `web/viewer/index.html`
- Modify: `web/viewer/bridge.mjs`
- Modify: `web/viewer/bridge.test.mjs`
- Modify: `web/viewer/index-contract.test.mjs`
- Modify: `web/viewer/startup-state.mjs`
- Modify: `web/viewer/startup-state.test.mjs`
- Modify: `web/viewer/frame-performance.mjs`
- Modify: `web/viewer/frame-performance.test.mjs`

**Interfaces:**

- Consumes: Task 5 layer and existing full 3DGS renderer.
- Produces: `hybrid`/`base`/`overlays` presentation modes, bridge composition
  state, fixed-depth behavior and performance evidence.

- [ ] **Step 1: Write RED startup ordering and mode tests**

```js
test('Production hybrid startup verifies base before fetching overlays', async () => {
  const events = [];
  await startViewer({
    loadBase: async () => events.push('base'),
    loadComposition: async () => events.push('composition'),
  });
  assert.deepEqual(events, ['base', 'composition']);
});

test('overlay failure exposes base-only action without hybrid success', () => {
  const view = startupViewModel(failedOverlayState);
  assert.equal(view.heading, '组合图层加载失败');
  assert.equal(view.show_base_only, true);
  assert.equal(view.status, 'failed');
});
```

Require `base` to hide overlays only, `overlays` to hide base only and carry a
diagnostic label, and `hybrid` to require both full 3DGS and ready overlays.
No Production path may fall back to point preview or synthetic model mode.

- [ ] **Step 2: Write RED HUD/bridge/depth contract tests**

Assert the DOM has separate immutable identity rows and this exact disclosure:

```text
Base: real 3DGS / metric-aligned / production-accepted
Overlay: modeled-unverified / operator-authored / trust effect none
Composition: hybrid / review pending
```

Bridge capabilities add `scene-composition` artifact loading and
`setCompositionMode`. `getState()` returns base/composition identities,
mode/state, active/failed slot counts and frame metrics. Static tests require
opaque overlay materials to use `depthTest=true`, `depthWrite=true`; transparent
materials keep `depthTest=true` and explicit bounded render order. Browser
acceptance in Task 7, not this static assertion, decides occlusion quality.

- [ ] **Step 3: Run RED**

```powershell
node --test \
  web/viewer/startup-state.test.mjs \
  web/viewer/bridge.test.mjs \
  web/viewer/index-contract.test.mjs \
  web/viewer/frame-performance.test.mjs
```

- [ ] **Step 4: Integrate without expanding legacy `points/mesh/model` truth**

Keep `presentationMode` for legacy Preview representations. Add a separate
`compositionMode` with exact values `hybrid`, `base`, `overlays`; do not rename
Preview mesh/model to hybrid. Startup sequence is:

```js
await loadProductionBase();
if (compositionUrl) {
  await compositionLayer.load(compositionUrl, {
    expectedBase: productionBaseBinding,
    mode: 'hybrid',
  });
}
```

Both passes use the same `scene`, camera and renderer. Clear depth exactly once
per frame; do not render splats and meshes through independent cameras or
compositing canvases. `setCompositionMode('base')` leaves the accepted base
state intact. A failed overlay sets a visible terminal composition state and
offers a user-invoked base-only action.

- [ ] **Step 5: Run full Viewer gates**

```powershell
npm run test:viewer
python -m pytest -q tests/test_viewer_session.py tests/test_studio_server.py -k "viewer or composition"
git diff --check -- web/viewer/main.js web/viewer/index.html web/viewer/bridge.mjs web/viewer/bridge.test.mjs web/viewer/index-contract.test.mjs web/viewer/startup-state.mjs web/viewer/startup-state.test.mjs web/viewer/frame-performance.mjs web/viewer/frame-performance.test.mjs
```

- [ ] **Step 6: Commit and push hybrid Viewer checkpoint**

```powershell
git add -- web/viewer/main.js web/viewer/index.html web/viewer/bridge.mjs web/viewer/bridge.test.mjs web/viewer/index-contract.test.mjs web/viewer/startup-state.mjs web/viewer/startup-state.test.mjs web/viewer/frame-performance.mjs web/viewer/frame-performance.test.mjs
git commit -m "feat: render hybrid 3DGS and verified overlays" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- web/viewer/main.js web/viewer/index.html web/viewer/bridge.mjs web/viewer/bridge.test.mjs web/viewer/index-contract.test.mjs web/viewer/startup-state.mjs web/viewer/startup-state.test.mjs web/viewer/frame-performance.mjs web/viewer/frame-performance.test.mjs
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

## Phase 4 — Acceptance and Production Release Closure

### Task 7: Composition-bound machine and human acceptance

**Files:**

- Create: `pipeline/scene_composition_acceptance.py`
- Create: `tests/test_scene_composition_acceptance.py`
- Create: `scripts/capture_scene_composition_acceptance.mjs`
- Create: `scripts/capture_scene_composition_acceptance.test.mjs`
- Modify: `package.json`

**Interfaces:**

- Consumes: published composition receipt, Viewer bridge snapshots and exact
  receipt-bound camera poses/screenshots.
- Produces: `HybridQualityPolicy`, `HybridQualityReport`,
  `HybridHumanReviewPolicy`, `HybridHumanVisualReview`,
  `SceneCompositionAcceptance`, `validate_scene_composition_acceptance(...)`
  and a Playwright capture CLI.

- [ ] **Step 1: Write RED machine evidence tests**

```python
def test_machine_acceptance_binds_exact_composition_and_base(
    receipt, quality_policy, quality_report
):
    accepted = validate_hybrid_quality(
        receipt=receipt,
        policy=quality_policy,
        report=quality_report,
    )
    assert accepted.accepted is True
    assert accepted.composition_manifest_sha256 == receipt.composition_manifest.sha256
    assert accepted.base_manifest_sha256 == receipt.base.reconstruction_manifest.sha256
```

Reject wrong composition/base/profile, missing pose, wrong screenshot bytes,
unproven occlusion, base-only drift, non-finite or over-budget load/memory/p95,
LOD transform drift, failed-fetch hybrid-success and unknown renderer state.

- [ ] **Step 2: Write RED human category and decision tests**

The exact ordered categories are:

```python
HYBRID_VISUAL_CATEGORIES = (
    "contact-and-grounding",
    "splat-mesh-occlusion",
    "material-scale-exposure-colour",
    "replacement-and-lod-continuity",
    "captured-object-conflict",
    "truthful-disclosure",
)
```

Require at least three exact pose screenshots, UTC reviewer time, bounded
printable reviewer label and one disposition per category. An accepted base
plus any rejected/unknown hybrid category yields rejected composition without
changing the base decision.

- [ ] **Step 3: Write RED Playwright CLI tests**

Mock the browser/bridge boundary and assert the CLI visits the exact
composition URL, waits for `spark`/`spark-chunks` plus overlay `ready`, captures
`hybrid`, `base` and overlay-failure states, writes bounded canonical JSON and
never records query strings, cookies, local paths or raw environment values.

```powershell
python -m pytest -q tests/test_scene_composition_acceptance.py
node --test scripts/capture_scene_composition_acceptance.test.mjs
```

- [ ] **Step 4: Implement exact acceptance models and capture command**

```python
class EvidenceFileBinding(FrozenModel):
    path: str
    sha256: Sha256
    byte_length: int = Field(ge=1)


class HybridQualityPolicy(FrozenModel):
    schema_id: Literal["nantai.hybrid-quality-policy.v1"] = Field(
        default="nantai.hybrid-quality-policy.v1",
        alias="schema",
        serialization_alias="schema",
    )
    required_pose_ids: tuple[str, ...] = Field(min_length=3)
    maximum_cold_load_ms: int = Field(ge=1)
    maximum_peak_memory_bytes: int = Field(ge=1)
    maximum_p95_frame_ms: float = Field(gt=0.0, allow_inf_nan=False)
    minimum_base_only_ssim: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    require_opaque_occlusion: Literal[True]
    require_lod_identity_stability: Literal[True]
    require_visible_failed_fetch_state: Literal[True]


class HybridPoseEvidence(FrozenModel):
    pose_id: str = Field(pattern=r"^pose-[0-9a-f]{64}$")
    hybrid_screenshot: EvidenceFileBinding
    base_screenshot: EvidenceFileBinding
    opaque_occlusion_visible: bool
    base_only_ssim: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class HybridQualityReport(FrozenModel):
    schema_id: Literal["nantai.hybrid-quality-report.v1"] = Field(
        default="nantai.hybrid-quality-report.v1",
        alias="schema",
        serialization_alias="schema",
    )
    composition_receipt_sha256: Sha256
    composition_manifest_sha256: Sha256
    base_manifest_sha256: Sha256
    policy_sha256: Sha256
    renderer_mode: Literal["spark", "spark-chunks"]
    cold_load_ms: int = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    p95_frame_ms: float = Field(ge=0.0, allow_inf_nan=False)
    lod_identity_stable: bool
    failed_fetch_state_visible: bool
    poses: tuple[HybridPoseEvidence, ...] = Field(min_length=3)


class HybridHumanReviewPolicy(FrozenModel):
    schema_id: Literal["nantai.hybrid-human-review-policy.v1"] = Field(
        default="nantai.hybrid-human-review-policy.v1",
        alias="schema",
        serialization_alias="schema",
    )
    required_categories: tuple[Literal[
        "contact-and-grounding",
        "splat-mesh-occlusion",
        "material-scale-exposure-colour",
        "replacement-and-lod-continuity",
        "captured-object-conflict",
        "truthful-disclosure",
    ], ...]
    required_pose_ids: tuple[str, ...] = Field(min_length=3)


class HybridHumanDisposition(FrozenModel):
    category: str
    disposition: Literal["accepted", "rejected", "unknown"]


class HybridHumanVisualReview(FrozenModel):
    schema_id: Literal["nantai.hybrid-human-visual-review.v1"] = Field(
        default="nantai.hybrid-human-visual-review.v1",
        alias="schema",
        serialization_alias="schema",
    )
    composition_receipt_sha256: Sha256
    policy_sha256: Sha256
    reviewer: str = Field(min_length=2, max_length=100)
    reviewed_at: datetime
    dispositions: tuple[HybridHumanDisposition, ...]
    screenshots: tuple[EvidenceFileBinding, ...] = Field(min_length=3)


class SceneCompositionAcceptance(FrozenModel):
    schema_id: Literal["nantai.scene-composition-acceptance.v1"] = Field(
        default="nantai.scene-composition-acceptance.v1",
        alias="schema",
        serialization_alias="schema",
    )
    composition_receipt_sha256: Sha256
    composition_manifest_sha256: Sha256
    base_scene_identity: str
    base_acceptance_report_sha256: Sha256
    machine_policy_sha256: Sha256
    machine_report_sha256: Sha256
    human_policy_sha256: Sha256
    human_review_sha256: Sha256
    accepted: bool
    rejected_gates: tuple[str, ...]
    unknown_gates: tuple[str, ...]


class HybridQualityDecision(FrozenModel):
    accepted: bool
    rejected_gates: tuple[str, ...]
    unknown_gates: tuple[str, ...]
    composition_manifest_sha256: Sha256
    base_manifest_sha256: Sha256


class HybridHumanDecision(FrozenModel):
    accepted: bool
    rejected_categories: tuple[str, ...]
    unknown_categories: tuple[str, ...]


def validate_hybrid_quality(
    *,
    receipt: SceneCompositionReceipt,
    policy: HybridQualityPolicy,
    report: HybridQualityReport,
) -> HybridQualityDecision: ...


def validate_hybrid_human_review(
    *,
    receipt: SceneCompositionReceipt,
    policy: HybridHumanReviewPolicy,
    review: HybridHumanVisualReview,
) -> HybridHumanDecision: ...


def validate_scene_composition_acceptance(
    *,
    composition_receipt_path: Path,
    machine_policy_path: Path,
    machine_report_path: Path,
    human_policy_path: Path,
    human_review_path: Path,
) -> SceneCompositionAcceptance: ...
```

The browser command is:

```powershell
npm run capture:composition -- \
  --viewer-url http://127.0.0.1:8767/web/viewer/ \
  --composition-url /api/scene-compositions/<composition-id>/manifest.json \
  --camera-plan <canonical-plan.json> \
  --output <private-evidence-directory>
```

- [ ] **Step 5: Run acceptance and adjacent Viewer gates**

```powershell
python -m pytest -q tests/test_scene_composition_acceptance.py tests/test_viewer_acceptance.py tests/test_human_review_inputs.py
node --test scripts/capture_scene_composition_acceptance.test.mjs
npm run test:viewer
python -m ruff check pipeline/scene_composition_acceptance.py tests/test_scene_composition_acceptance.py
git diff --check -- pipeline/scene_composition_acceptance.py tests/test_scene_composition_acceptance.py scripts/capture_scene_composition_acceptance.mjs scripts/capture_scene_composition_acceptance.test.mjs package.json
```

- [ ] **Step 6: Commit and push acceptance checkpoint**

```powershell
git add -- pipeline/scene_composition_acceptance.py tests/test_scene_composition_acceptance.py scripts/capture_scene_composition_acceptance.mjs scripts/capture_scene_composition_acceptance.test.mjs package.json
git commit -m "feat: bind hybrid composition acceptance evidence" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/scene_composition_acceptance.py tests/test_scene_composition_acceptance.py scripts/capture_scene_composition_acceptance.mjs scripts/capture_scene_composition_acceptance.test.mjs package.json
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 8: Production release schema v2 composition closure

**Files:**

- Modify: `pipeline/production_release_contract.py`
- Modify: `pipeline/production_release_builder.py`
- Modify: `pipeline/production_release_verifier.py`
- Modify: `tests/production_release_fixtures.py`
- Modify: `tests/test_production_release_contract.py`
- Modify: `tests/test_production_release_builder.py`
- Modify: `tests/test_production_release_verifier.py`
- Modify: `scripts/build_production_release.py`
- Modify: `scripts/verify_production_release.py`
- Modify: `tests/test_production_release_cli.py`
- Modify: `make.py`
- Modify: `tests/test_make_runner.py`

**Interfaces:**

- Consumes: Task 7 accepted composition and existing accepted base release
  context.
- Produces: schema-v2 package receipt/public evidence with an optional but exact
  active composition closure; v1 remains verifier-only compatible.

- [ ] **Step 1: Write RED v2 contract and downgrade tests**

```python
def test_v2_receipt_binds_active_composition(modeled_public_evidence_v2):
    receipt = build_production_receipt(
        version="v1.0.0",
        source_commit="a" * 40,
        artifacts=modeled_artifacts_with_composition(),
        protected_roots=("evidence", "pipeline", "scripts", "web"),
        entrypoints=PRODUCTION_ENTRYPOINTS,
        public_evidence=modeled_public_evidence_v2,
        schema_version="nantai.production-runtime-release.v2",
    )
    assert receipt["composition"]["composition_id"].startswith("composition-")
    assert receipt["composition"]["trust_effect"] == "none"
```

Reject overlay bytes under `web/data/composition/` in v1, v2 overlay bytes
without receipt, receipt without bytes, inactive revision bytes, mixed bundle
revisions, extra candidates/contact sheets, private rights text, wrong base,
unaccepted composition, absent public rights, wrong cover/screenshot identity
and a v2 package presented as v1.

- [ ] **Step 2: Write RED builder and downloaded-verifier closure tests**

Build with two published revisions and select the second. Assert only the
second receipt's reachable manifest/GLB/textures/rights/public-acceptance bytes
enter the archive. Extract, tamper each role independently and assert both tree
and streaming archive verification fail.

```python
def test_builder_excludes_inactive_and_candidate_overlay_bytes(...):
    build = build_production_release_archive(
        repo_root=repo_root,
        acceptance_root=base_acceptance_root,
        composition_receipt_path=active_receipt,
        composition_acceptance_path=composition_acceptance,
        output_path=output,
        version="v1.0.0",
        source_commit=source_commit,
        tracked_files=tracked_files,
    )
    with zipfile.ZipFile(build.archive_path) as archive:
        names = set(archive.namelist())
    assert not any(first_revision_id in name for name in names)
    assert not any("candidate" in name or "contact-sheet" in name for name in names)
```

- [ ] **Step 3: Run RED**

```powershell
python -m pytest -q \
  tests/test_production_release_contract.py \
  tests/test_production_release_builder.py \
  tests/test_production_release_verifier.py \
  tests/test_production_release_cli.py \
  tests/test_make_runner.py
```

- [ ] **Step 4: Implement v2 while preserving v1 verification**

Add exact literals:

```python
PRODUCTION_RELEASE_SCHEMA_V2 = "nantai.production-runtime-release.v2"
PRODUCTION_PUBLIC_EVIDENCE_SCHEMA_V2 = "nantai.production-public-evidence.v2"
```

`build_production_release_archive(...)` gains required-together arguments:

```python
composition_receipt_path: Path | None = None,
composition_acceptance_path: Path | None = None,
```

When absent, build a base-only v2 package with `composition=null`. When
present, reopen the base acceptance, composition receipt, all exact reachable
bytes and composition acceptance; require exact base/import/scene identities,
accepted rights and accepted hybrid QA. Map closure under
`web/data/composition/`, bind composition ID into public evidence, package
content ID, default Viewer URL, machine captures and cover identity. The
verifier recomputes the same reachability graph and rejects extras/missing
members. Do not let composition acceptance upgrade base acceptance.

CLI flags are exact and paired:

```text
--composition-receipt PATH
--composition-acceptance PATH
```

- [ ] **Step 5: Run full Production release gates**

```powershell
python -m pytest -q \
  tests/test_production_release_contract.py \
  tests/test_production_release_builder.py \
  tests/test_production_release_verifier.py \
  tests/test_production_release_cli.py \
  tests/test_production_release_assets.py \
  tests/test_production_release_privacy.py \
  tests/test_make_runner.py
python -m ruff check \
  pipeline/production_release_contract.py \
  pipeline/production_release_builder.py \
  pipeline/production_release_verifier.py \
  scripts/build_production_release.py \
  scripts/verify_production_release.py
git diff --check -- pipeline/production_release_contract.py pipeline/production_release_builder.py pipeline/production_release_verifier.py tests/production_release_fixtures.py tests/test_production_release_contract.py tests/test_production_release_builder.py tests/test_production_release_verifier.py scripts/build_production_release.py scripts/verify_production_release.py tests/test_production_release_cli.py make.py tests/test_make_runner.py
```

- [ ] **Step 6: Commit and push release closure checkpoint**

```powershell
git add -- pipeline/production_release_contract.py pipeline/production_release_builder.py pipeline/production_release_verifier.py tests/production_release_fixtures.py tests/test_production_release_contract.py tests/test_production_release_builder.py tests/test_production_release_verifier.py scripts/build_production_release.py scripts/verify_production_release.py tests/test_production_release_cli.py make.py tests/test_make_runner.py
git commit -m "feat: bind active compositions into Production releases" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/production_release_contract.py pipeline/production_release_builder.py pipeline/production_release_verifier.py tests/production_release_fixtures.py tests/test_production_release_contract.py tests/test_production_release_builder.py tests/test_production_release_verifier.py scripts/build_production_release.py scripts/verify_production_release.py tests/test_production_release_cli.py make.py tests/test_make_runner.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 9: CI, operator docs and exact real-scene closure gate

**Files:**

- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/manual/production-runtime-release.md`
- Create: `docs/manual/scene-compositions.md`
- Modify: `docs/production-v1-status.md`
- Modify: `AGENTS.md`

**Interfaces:**

- Consumes: Tasks 1–8.
- Produces: cross-platform modeled contract evidence, a truthful operator
  workflow and the final external real-scene acceptance checklist.

- [ ] **Step 1: Add RED doc/CI contract tests before changing docs**

Extend `tests/test_production_release_docs.py` to require:

```text
real 3DGS base remains immutable
modeled-unverified
operator-authored
trust effect none
base | hybrid | overlays
current H3 private rights do not authorize a public release
```

Require CI jobs to run composition contract/publication/release tests on
Ubuntu, Windows and macOS; Linux private mutation tests remain separately
marked. Require Node tests for Viewer/Studio composition modules and a
Playwright runtime preflight without claiming visual acceptance.

- [ ] **Step 2: Run RED docs test**

```powershell
python -m pytest -q tests/test_production_release_docs.py
```

- [ ] **Step 3: Write the operator workflow and status boundary**

`docs/manual/scene-compositions.md` documents:

1. verify the accepted base and its scene/import identity;
2. register exact mesh bundle and distribution-rights receipts;
3. create/validate a slot candidate in ENU metres;
4. compare base/current/candidate at receipt-bound poses;
5. commit a new immutable revision and activate it;
6. capture machine evidence and submit human review;
7. build/verify the active v2 Production package;
8. roll back by appending a selection event, never deleting revisions.

The status page separates these states:

```text
Contract implemented: modeled CI evidence only
Real hybrid accepted: requires one accepted real base plus exact hybrid QA
Replaceable-assets requirement complete: only after real hybrid acceptance and downloaded-package verification
```

- [ ] **Step 4: Add the CI matrix without fake real evidence**

Use committed modeled fixtures only for schema, portability and deterministic
content-ID comparison. Never upload private candidates, source receipts,
screenshots or acceptance working directories. The CI summary must say
`modeled-contract-not-real-release`.

- [ ] **Step 5: Run the repository gate and one real-browser rehearsal**

```powershell
python -m pytest -q
npm run test:viewer
node --test web/studio/*.test.mjs scripts/*.test.mjs
python -m ruff check pipeline scripts tests
git diff --check
npm run preflight:viewer-runtime
```

Then, with a local modeled Preview fixture only, use the real browser to prove:

- base loads before overlay requests;
- `hybrid`, `base` and `overlays` switches are visible and reversible;
- one opaque mesh crosses a Gaussian surface and produces observable depth
  change;
- a forced overlay 404 shows failed composition and explicit base-only action;
- Studio compare, commit-disabled read-only behavior and disclosure are usable
  at desktop and mobile widths.

Record this as Preview rehearsal, not real acceptance.

- [ ] **Step 6: Commit and push docs/CI checkpoint**

```powershell
git add -- .github/workflows/ci.yml README.md docs/README.md docs/manual/production-runtime-release.md docs/manual/scene-compositions.md docs/production-v1-status.md AGENTS.md tests/test_production_release_docs.py
git commit -m "docs: operationalize hybrid scene compositions" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- .github/workflows/ci.yml README.md docs/README.md docs/manual/production-runtime-release.md docs/manual/scene-compositions.md docs/production-v1-status.md AGENTS.md tests/test_production_release_docs.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- [ ] **Step 7: Close the real-scene gate only with external evidence**

After Tasks 1–9 code is green, the requirement remains externally blocked
until one run supplies all of:

```text
accepted real-photo SfM
non-mock CUDA 3DGS
accepted metric alignment
accepted base Viewer evidence
public overlay distribution rights
accepted exact hybrid machine report
accepted exact hybrid human review
clean v2 Production archive plus downloaded-package verification
```

If any item is absent, leave `docs/production-v1-status.md` at
`implementation-ready / real-acceptance-blocked`. Do not create `v1.0.0`, a
formal release note or a public Production GitHub Release from modeled
fixtures.

## Final Acceptance Mapping

| Approved specification criterion | Implemented/proved by |
|---|---|
| Stable ENU-metre overlay slot | Tasks 1, 5 and 6 |
| Replacement changes composition, not base | Tasks 1, 2 and 4 |
| Clean reopen and audited rollback | Tasks 2 and 3 |
| Full 3DGS plus mesh with visible occlusion | Tasks 5, 6 and 7 |
| Base-only reproduces verified base | Tasks 6 and 7 |
| Corruption fails closed | Tasks 1, 2, 3, 5 and 8 |
| Exact hybrid machine/human acceptance | Task 7 |
| Release/download closure includes only active bytes | Task 8 |
| UI never calls overlay reconstructed/real | Tasks 4, 6 and 9 |
| Original five real-scene gates remain independent | Tasks 7, 8 and 9 |

The first eight tasks produce working, independently reviewable software.
Task 9 makes the proof boundary explicit: modeled CI can finish implementation,
but only exact real-scene evidence can finish the original Product requirement.
