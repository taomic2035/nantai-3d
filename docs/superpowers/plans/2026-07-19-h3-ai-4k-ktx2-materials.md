# H3 AI 4K KTX2 Materials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, verify, package, and activate eight AI-generated 4096-authored
PBR material replacements delivered as KTX2, with exact H2 PNG fallback,
unchanged H2 geometry/coordinates/weather semantics, and an explicit
synthetic-preview trust boundary.

**Architecture:** Add immutable H3 source, authored-material, material-bundle,
and dual-variant mesh-bundle contracts beside the accepted H2 contracts. A
runtime-v3 projection exposes both complete asset closures; the Viewer performs
one renderer-aware KTX2 canary decision, freezes a session-wide profile, and
atomically rolls the complete visible mesh world back to H2 if later H3 loading
fails. All generated and release payloads remain private, content-addressed
objects under `.nantai-studio`; tracked code and evidence contain identities
and audits, not unreviewed binary payloads.

**Tech Stack:** Python 3.11+, Pydantic v2, Pillow, NumPy, scikit-image,
Khronos KTX-Software 4.4.2 (`toktx`, `ktx`), binary glTF 2.0 with
`KHR_texture_basisu`, Blender 4.5.11 LTS, Three.js 0.180.0
`GLTFLoader`/`KTX2Loader`, Basis Universal transcoder, Web Crypto, Node test
runner, Playwright browser acceptance, pytest, Ruff.

## Global Constraints

- Work only on the shared `main`; do not create a branch or worktree.
- Execute inline in the current task; do not dispatch subagents.
- Stage only explicit paths; never use `git add -A`, `git add .`, or
  `git commit -a`.
- Never stage the pre-existing `tests/test_synthetic_village_weather.py` WIP
  unless its owner has committed it.
- End every Codex commit with
  `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`.
- Push every verified task commit to `origin/main` before starting the next
  task.
- Preserve accepted H2 mesh bundle
  `866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6`
  and material bundle
  `b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13`
  byte-for-byte.
- Preserve H2 LOD0/LOD1/LOD2 geometry, UVs, topology, material-slot
  assignments, transforms, AABBs, local ENU coordinates, arbitrary-coordinate
  loading, and all six reversible weather IDs.
- H3 truth is always `synthetic=true`, `ai_generated=true`,
  `real_photo_textures=false`, `geometry_usability=preview-only`,
  `metric_alignment=false`, and `verification_level=L0`.
- The eight H3 slots are weathered timber, dark timber, gray roof tile,
  fieldstone, dry stone wall, rammed earth, packed earth, and terrace soil;
  foliage and topology replacement remain H3-B.
- Retain three captured candidates per slot in private selection evidence, but
  publish only the chosen native source in the source-pack closure.
- A 4096 derived output is called a `4096 authored master`; never call it a
  native 4K AI output unless the captured source itself is 4096 by 4096.
- KTX-Software is pinned to official `v4.4.2`; Three dependencies remain
  exactly `0.180.0`, and the loader/transcoder closure is vendored with exact
  hashes and licenses. No CDN is permitted.
- H3 base colour and normal use UASTC; ORM uses ETC1S only when its decoded
  channel error passes the frozen gate, otherwise UASTC.
- Base colour is sRGB; normal and ORM are linear; every KTX2 contains exact
  4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, and 1 mip levels.
- The H3 predicted compressed mip working set must not exceed 512 MiB.
- Viewer selection is session-wide. Mixed H2/H3 chunks, textures, or templates
  are forbidden.
- Integrity, schema, decode, transcode, geometry-fingerprint, memory, visual,
  or performance disagreement fails closed to explicit H2 fallback; it cannot
  promote trust.
- H3 activation requires 9/9 chunks at default and both
  `(123456,-98765,12)` and `(-123456,98765,12)`, six-weather round-trip,
  median frame interval at most 33.3 ms, p95 at most 50 ms, median regression
  at most 30 percent from accepted H2, stable resources at 60/62 seconds, and
  zero application warnings/errors.
- Do not upload a public release without separate explicit authorization.
- A locally verified Apple Silicon archive is not called portable: portable
  release identity additionally requires byte-equal KTX2 output and complete
  green validation on another supported platform.
- Every implementation task begins with an observed RED test and ends with
  focused gates, `git diff --check`, a path-limited commit, and a push.

## File Map

- Create `pipeline/synthetic_village/h3_material_sources.py`: strict source
  pack, candidate audit, canonical identity, selected-source publication, and
  verified reads.
- Create `tests/test_h3_material_sources.py`: source metadata, candidate,
  content-addressing, path safety, and tamper tests.
- Create `pipeline/synthetic_village/h3_material_authoring.py`: deterministic
  4096 quilting, source-preservation audit, PBR derivation, complete PNG mip
  evidence, and authored-pack publication.
- Create `tests/test_h3_material_authoring.py`: determinism, dimensions,
  seams, PBR semantics, source preservation, and negative gates.
- Create `pipeline/synthetic_village/ktx2_toolchain.py`: pinned-tool evidence,
  command construction, official validation parsing, KTX2 structural audit,
  decoded-quality evidence, and deterministic publication.
- Create `tests/test_ktx2_toolchain.py`: mocked process, KTX header/DFD/mip,
  repeatability, quality, and tamper tests.
- Modify `scripts/setup_synthetic_tools.py`: explicit
  `--install-ktx-4.4.2` download, SHA measurement, package-signature probe,
  install, and version receipt.
- Create `pipeline/synthetic_village/material_bundle_v2.py`: immutable H3
  material bundle with primary KTX2 and exact H2 PNG fallback closures.
- Modify `pipeline/synthetic_village/material_bundle.py`: schema-only v1/v2
  dispatch while preserving v1 canonical bytes.
- Create `tests/test_material_bundle_v2.py`: v1 stability and complete H3/H2
  closure tests.
- Create `pipeline/synthetic_village/glb_ktx2_variant.py`: binary-glTF JSON
  rewrite to `KHR_texture_basisu` and profile-independent geometry
  fingerprints.
- Create `pipeline/synthetic_village/mesh_asset_bundle_v3.py`: immutable
  dual-profile LOD2 variants, exact H2 LOD0/1 reuse, canonical identity,
  verified reads, and publication.
- Modify `pipeline/synthetic_village/mesh_asset_bundle.py`: schema-only v3
  dispatch.
- Create `tests/test_glb_ktx2_variant.py` and
  `tests/test_mesh_asset_bundle_v3.py`.
- Modify `pipeline/synthetic_village/mesh_chunk.py`: strict runtime-v3
  projection carrying complete primary and fallback descriptors without
  texture bytes.
- Modify `pipeline/studio_server.py`: verified profile-aware GLB/KTX2/PNG
  routes with exact MIME, length, SHA, ETag, HEAD, and immutable caching.
- Modify `tests/test_mesh_chunk.py`, `tests/test_studio_server.py`, and
  `tests/test_studio_job_http.py`: v3 contract and route tests.
- Modify `web/viewer/vendor/fetch-vendor.sh`,
  `web/viewer/vendor/VENDOR.md`, and `web/viewer/vendor.test.mjs`: exact
  Three 0.180.0 KTX2 loader, worker pool, Basis JS/WASM, and license closure.
- Create `web/viewer/material-profile.mjs`: renderer canary, 512 MiB gate,
  frozen session selection, fallback reasons, and atomic state transitions.
- Create `web/viewer/material-profile.test.mjs`.
- Modify `web/viewer/mesh-world.mjs`: strict runtime-v3 parsing and
  profile-specific URL resolution.
- Modify `web/viewer/verified-mesh-resources.mjs`: verified KTX2 parsing,
  profile-aware semantic caches, bounded diagnostics, and full-profile
  disposal.
- Modify `web/viewer/main.js`: selection before mesh load, one global rollback,
  whole-visible-set reload, weather texture-identity preservation, and
  diagnostics.
- Modify `web/viewer/index.html`, `web/viewer/bridge.mjs`,
  `web/studio/viewer-bridge.mjs`, and their tests: honest active-profile UI and
  bounded bridge evidence.
- Create `pipeline/synthetic_village/h3_release.py`: deterministic archive,
  `SHA256SUMS`, safe extraction, complete internal verification, and atomic
  local publication.
- Modify `scripts/synthetic_village.py`: H3 source import, author, KTX build,
  bundle build, release, verify, and install commands.
- Create `tests/test_h3_release.py` and update CLI tests.
- Modify `web/data/manifest.json` only after all activation gates pass.
- Create `docs/verification/2026-07-19-h3-ai-4k-ktx2-materials.md` and
  `handoff/FEEDBACK-CODEX-012-h3-ai-4k-ktx2-materials.md` with actual
  identities, visual/performance evidence, and remaining H3-B limits.

---

### Task 1: Strict AI Source-Pack Contract and Publication

**Files:**
- Create: `pipeline/synthetic_village/h3_material_sources.py`
- Create: `tests/test_h3_material_sources.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_cli.py`

**Interfaces:**
- Consumes: a private JSON selection receipt containing 24 candidate file
  paths, exact prompts, response-exposed generator metadata, and one explicit
  selected SHA per slot.
- Produces: `H3_SOURCE_PACK_SCHEMA`, `H3_HERO_SLOTS`,
  `H3MaterialSourcePack`, `PreparedH3MaterialSourcePack`,
  `canonical_h3_source_pack_bytes(...)`, `prepare_h3_source_pack(...)`,
  `load_h3_source_pack(...)`, and `read_verified_h3_source(...)`.

- [ ] **Step 1: Write strict RED tests**

```python
def test_prepare_source_pack_publishes_exact_selected_bytes(
    selection_receipt: Path,
    tmp_path: Path,
) -> None:
    prepared = prepare_h3_source_pack(selection_receipt, tmp_path / "pack")
    assert prepared.manifest.schema_version == H3_SOURCE_PACK_SCHEMA
    assert tuple(record.slot_id for record in prepared.manifest.records) == (
        H3_HERO_SLOTS
    )
    assert all(record.selection.candidate_count == 3 for record in prepared.manifest.records)
    assert all(record.rights_review.public_release_authorized is False
               for record in prepared.manifest.records)
    for record in prepared.manifest.records:
        payload = read_verified_h3_source(
            prepared.root,
            pack=prepared.manifest,
            slot_id=record.slot_id,
        )
        assert sha256(payload).hexdigest() == record.native_source.sha256


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_candidate",
        "duplicate_slot",
        "selected_sha_not_candidate",
        "path_escape",
        "prompt_hash_disagrees",
        "generator_version_inferred",
        "public_release_true",
        "alpha_present",
    ),
)
def test_source_pack_rejects_untrusted_selection(
    selection_receipt: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    mutate_selection_receipt(selection_receipt, mutation)
    with pytest.raises(H3MaterialSourceError):
        prepare_h3_source_pack(selection_receipt, tmp_path / mutation)
```

The fixture generates eight slots × three deterministic RGB PNG candidates and
records exact bytes, dimensions, prompts, and SHAs. It also proves the canonical
manifest contains no absolute path, timestamp, temporary URL, request ID, or
rejected candidate path.

- [ ] **Step 2: Run the tests and observe RED**

```bash
.venv/bin/python -m pytest \
  tests/test_h3_material_sources.py \
  tests/test_synthetic_village_cli.py -q
```

Expected: collection fails because `h3_material_sources` and
`import-h3-material-sources` do not exist.

- [ ] **Step 3: Implement exact frozen source models**

```python
H3_SOURCE_PACK_SCHEMA = "nantai.h3-ai-material-source-pack.v1"
H3_GENERATION_POLICY_ID = "h3-ai-material-candidates-v1"
H3_HERO_SLOTS = (
    "material-weathered-timber-01",
    "material-dark-timber-01",
    "material-gray-roof-tile-01",
    "material-fieldstone-01",
    "material-dry-stone-wall-01",
    "material-rammed-earth-01",
    "material-packed-earth-01",
    "material-terrace-soil-01",
)


class H3MaterialSourcePack(FrozenModel):
    schema_version: Literal[
        "nantai.h3-ai-material-source-pack.v1"
    ] = H3_SOURCE_PACK_SCHEMA
    source_pack_id: Sha256
    synthetic: Literal[True] = True
    ai_generated: Literal[True] = True
    real_photo_textures: Literal[False] = False
    generation_policy_id: Literal[
        "h3-ai-material-candidates-v1"
    ] = H3_GENERATION_POLICY_ID
    records: tuple[H3SourceRecord, ...]
```

`H3SourceRecord` validates the exact sorted slot list, prompt SHA, JSON-null
generator version with explicit absence evidence, selected SHA membership in
three private candidates, RGB/RGBA-with-opaque-alpha input, no text metadata,
`private-project-use-only`, and `public_release_authorized=false`.
`prepare_h3_source_pack` copies only the selected bytes to
`sources/{sha256}.png`, writes canonical LF JSON, derives `source_pack_id` from
canonical bytes excluding the ID, and verifies the closed directory before
returning.

- [ ] **Step 4: Add the path-explicit CLI**

```python
source_command = commands.add_parser("import-h3-material-sources")
source_command.add_argument("--selection-receipt", type=Path, required=True)
source_command.add_argument("--output-root", type=Path, required=True)
```

The command prints only schema, source-pack ID, output directory, slot count,
and truth flags. It does not print prompts, private candidate paths, or request
metadata.

- [ ] **Step 5: Run focused gates**

```bash
.venv/bin/python -m pytest \
  tests/test_h3_material_sources.py \
  tests/test_synthetic_village_cli.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/h3_material_sources.py \
  scripts/synthetic_village.py \
  tests/test_h3_material_sources.py \
  tests/test_synthetic_village_cli.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/h3_material_sources.py \
  scripts/synthetic_village.py
git diff --check -- \
  pipeline/synthetic_village/h3_material_sources.py \
  scripts/synthetic_village.py \
  tests/test_h3_material_sources.py \
  tests/test_synthetic_village_cli.py
```

Expected: all focused tests and static gates pass.

- [ ] **Step 6: Commit and push**

```bash
git add \
  pipeline/synthetic_village/h3_material_sources.py \
  scripts/synthetic_village.py \
  tests/test_h3_material_sources.py \
  tests/test_synthetic_village_cli.py
git commit -m "feat(materials): add strict H3 source packs" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  pipeline/synthetic_village/h3_material_sources.py \
  scripts/synthetic_village.py \
  tests/test_h3_material_sources.py \
  tests/test_synthetic_village_cli.py
git push origin main
```

### Task 2: Generate, Audit, and Select the Eight Native Sources

**Files:**
- Create privately: `.nantai-studio/h3/candidates/selection-receipt.json`
- Create privately: `.nantai-studio/h3/candidates/contact-sheet.png`
- Create privately: `.nantai-studio/h3/source-pack/<source-pack-id>/`
- Create: `docs/verification/2026-07-19-h3-source-selection.md`

**Interfaces:**
- Consumes: the image-generation tool and Task 1 importer.
- Produces: 24 retained private candidate objects, one fixed contact sheet,
  eight explicit human selections, a verified source pack, and frozen
  pre-selection audit thresholds.

- [ ] **Step 1: Generate three candidates for each exact slot**

Use the image-generation tool for one material-only image at a time. Every
prompt includes the slot identity and this common suffix:

```text
Seamless material source candidate, orthographic or near-orthographic surface
coverage, flat diffuse illumination, no hard shadows, no text, no logos, no
objects, no borders, no perspective corner, no scene horizon, stochastic
natural variation, synthetic reference only and not a real Nantai photograph.
```

Retain each returned file unchanged. Record its SHA, byte count, dimensions,
media type, complete prompt, generator response fields, and the statement
`generator_version_evidence=not-exposed-by-generation-response` when the
response does not expose a version.

- [ ] **Step 2: Freeze audit thresholds before visual selection**

Run the Task 1 audit across all 24 candidates and write the exact metric
distribution and policy to the private receipt. The deterministic policy is:

```python
hard_limits = {
    "minimum_width": 1024,
    "minimum_height": 1024,
    "maximum_clipped_fraction": 0.02,
    "maximum_alpha_nonopaque_fraction": 0.0,
    "maximum_dominant_perspective_score": 0.35,
}
calibrated_limits = {
    metric: float(np.quantile(values, 0.75) + 1.5 * iqr(values))
    for metric, values in negative_indicator_metrics.items()
}
```

Hard limits always dominate calibrated limits. Persist exact values before any
candidate is marked selected; never recompute them from winners.

- [ ] **Step 3: Build and inspect one fixed contact sheet**

The sheet uses identical 512 by 512 crops, neutral labels
`slot / candidate 1..3`, no enhancement, and a fixed sRGB export. Select one
passing candidate per slot based on material scale, absence of baked lighting,
natural nonrepeating structure, and later tiling suitability. Record one
specific reason per selection.

- [ ] **Step 4: Import and verify the source pack**

```bash
.venv/bin/python scripts/synthetic_village.py \
  import-h3-material-sources \
  --selection-receipt .nantai-studio/h3/candidates/selection-receipt.json \
  --output-root .nantai-studio/h3/source-pack
```

Expected: eight records, `synthetic=true`, `ai_generated=true`,
`real_photo_textures=false`, and one content-addressed native source per slot.

- [ ] **Step 5: Record selection evidence and commit only the report**

The report records source-pack ID, per-slot selected SHA prefix, dimensions,
audit metrics, selection reasons, contact-sheet SHA, and private path. It does
not embed prompts, candidate bytes, or absolute temporary paths.

```bash
git add docs/verification/2026-07-19-h3-source-selection.md
git commit -m "docs(verification): record H3 source selection" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- docs/verification/2026-07-19-h3-source-selection.md
git push origin main
```

### Task 3: Deterministic 4096 Authoring and PBR Derivation

**Files:**
- Create: `pipeline/synthetic_village/h3_material_authoring.py`
- Create: `tests/test_h3_material_authoring.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_cli.py`

**Interfaces:**
- Consumes: a verified `H3MaterialSourcePack`.
- Produces: `H3_AUTHORED_PACK_SCHEMA`, `H3AuthoredMaterialPack`,
  `canonical_h3_authored_pack_bytes(...)`,
  `build_h3_authored_material_pack(...)`, and verified 4096 base/normal/ORM
  PNG objects plus complete mip evidence.

- [ ] **Step 1: Write deterministic authoring RED tests**

```python
def test_authoring_is_byte_deterministic(
    source_pack_root: Path,
    tmp_path: Path,
) -> None:
    first = build_h3_authored_material_pack(
        source_pack_root, tmp_path / "first"
    )
    second = build_h3_authored_material_pack(
        source_pack_root, tmp_path / "second"
    )
    assert canonical_h3_authored_pack_bytes(first.manifest) == (
        canonical_h3_authored_pack_bytes(second.manifest)
    )
    assert first.object_payloads == second.object_payloads


def test_authored_maps_are_exact_4k_rgb_and_role_correct(
    authored_pack_root: Path,
) -> None:
    pack = load_h3_authored_material_pack(authored_pack_root)
    assert len(pack.records) == 8
    for record in pack.records:
        assert record.master.width == record.master.height == 4096
        assert record.master.mode == "RGB"
        assert record.base_color.colour_space == "srgb"
        assert record.normal.colour_space == "linear"
        assert record.orm.colour_space == "linear"
        assert record.mip_dimensions == tuple(
            (2 ** level, 2 ** level) for level in range(12, -1, -1)
        )
```

Also reject changed source bytes, alpha, a missing hero slot, seam score above
the frozen threshold, source-preservation SSIM below the frozen threshold,
noncanonical PNG metadata, wrong ORM channel policy, and any path/link escape.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/python -m pytest \
  tests/test_h3_material_authoring.py \
  tests/test_synthetic_village_cli.py -q
```

Expected: collection fails because the authoring module and command do not
exist.

- [ ] **Step 3: Implement SHA-seeded quilting and PBR maps**

```python
H3_AUTHORED_PACK_SCHEMA = "nantai.h3-authored-material-pack.v1"
H3_AUTHORING_ALGORITHM_ID = "sha-quilt-seam-pbr-v1"
H3_MASTER_SIZE = 4096
H3_PATCH_SIZE = 768
H3_PATCH_OVERLAP = 128
H3_EDGE_BAND = 192


def _rng_for_source(source_sha256: str) -> np.random.Generator:
    return np.random.default_rng(int(source_sha256[:16], 16))


def _mip_dimensions() -> tuple[tuple[int, int], ...]:
    return tuple((size, size) for size in (4096, 2048, 1024, 512, 256,
                                           128, 64, 32, 16, 8, 4, 2, 1))
```

Use minimum-error overlap cuts in linear-light RGB, enforce opposite edges
within `H3_EDGE_BAND`, and add only source-derived low-frequency variation with
amplitude capped at 4 percent. Encode canonical RGB8 PNG with fixed Pillow
options and no semantic metadata. Derive tangent-space normal using the
existing Sobel convention and ORM using existing per-slot roughness/metalness
contracts. Record full/interior SSIM, seam discontinuity, algorithm module SHA,
parameters, source identity, and each mip dimension.

- [ ] **Step 4: Add `author-h3-materials` CLI**

```python
author_command = commands.add_parser("author-h3-materials")
author_command.add_argument("--source-pack-root", type=Path, required=True)
author_command.add_argument("--output-root", type=Path, required=True)
```

- [ ] **Step 5: Run focused gates and a real private build**

```bash
.venv/bin/python -m pytest \
  tests/test_h3_material_authoring.py \
  tests/test_synthetic_village_material_bundle.py \
  tests/test_synthetic_village_cli.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/h3_material_authoring.py \
  tests/test_h3_material_authoring.py
.venv/bin/python -m compileall -q \
  pipeline/synthetic_village/h3_material_authoring.py
.venv/bin/python scripts/synthetic_village.py \
  author-h3-materials \
  --source-pack-root .nantai-studio/h3/source-pack \
  --output-root .nantai-studio/h3/authored
git diff --check
```

Expected: tests pass; the private build has eight 4096 RGB8 masters and 24
verified base/normal/ORM map objects.

- [ ] **Step 6: Commit and push**

Commit only the module, tests, and CLI paths with message
`feat(materials): author deterministic H3 PBR maps`, the required trailer, and
push `main`.

### Task 4: Pinned KTX 4.4.2 Toolchain and Verified Compilation

**Files:**
- Create: `pipeline/synthetic_village/ktx2_toolchain.py`
- Create: `tests/test_ktx2_toolchain.py`
- Modify: `scripts/setup_synthetic_tools.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_tool_lock.py`
- Modify: `tests/test_synthetic_village_cli.py`

**Interfaces:**
- Consumes: verified authored maps and an explicit KTX tool receipt.
- Produces: `KTX_TOOL_VERSION="4.4.2"`, `KtxToolReceipt`,
  `KtxTextureDescriptor`, `KtxCompilationReport`,
  `compile_h3_ktx2_pack(...)`, and `verify_h3_ktx2_pack(...)`.

- [ ] **Step 1: Write mocked process and binary-structure RED tests**

```python
def test_uastc_commands_are_role_and_colour_space_exact() -> None:
    assert toktx_command(
        Path("/opt/ktx/bin/toktx"),
        role="base_color",
        source=Path("base.png"),
        output=Path("base.ktx2"),
    ) == (
        "/opt/ktx/bin/toktx", "--t2", "--encode", "uastc",
        "--uastc_quality", "4", "--zcmp", "18", "--genmipmap",
        "--assign_oetf", "srgb", "base.ktx2", "base.png",
    )


def test_compilation_requires_exact_tool_receipt(
    authored_pack_root: Path,
    fake_tools: Path,
    tmp_path: Path,
) -> None:
    receipt = fake_ktx_receipt(version="4.4.1")
    with pytest.raises(KtxToolchainError, match="4.4.2"):
        compile_h3_ktx2_pack(
            authored_pack_root,
            tmp_path / "ktx",
            receipt=receipt,
        )
```

Mock `toktx`, `ktx validate`, and decode output. Reject wrong magic, dimensions,
level count, DFD transfer, media type, output SHA, command evidence, repeated
build drift, and role-specific decoded-quality failure.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/python -m pytest \
  tests/test_ktx2_toolchain.py \
  tests/test_synthetic_village_tool_lock.py -q
```

Expected: collection fails because `ktx2_toolchain` is absent.

- [ ] **Step 3: Implement exact tool and KTX evidence**

```python
KTX_TOOL_VERSION = "4.4.2"
KTX_DARWIN_ARM64_ASSET = "KTX-Software-4.4.2-Darwin-arm64.pkg"
KTX_DARWIN_ARM64_URL = (
    "https://github.com/KhronosGroup/KTX-Software/releases/download/"
    "v4.4.2/KTX-Software-4.4.2-Darwin-arm64.pkg"
)
KTX2_MAGIC = b"\xabKTX 20\xbb\r\n\x1a\n"
KTX_LEVEL_DIMENSIONS = (
    4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1
)
```

The install path downloads only the exact URL to staging, measures SHA-256,
runs `pkgutil --check-signature`, records signer output, installs only after a
valid Apple package signature, and probes `toktx --version` plus
`ktx --version`. The compile path uses `subprocess.run` with tuple arguments,
bounded output, timeout, isolated environment, and no shell. Official
validation plus independent header/DFD/mip parsing must both pass.

- [ ] **Step 4: Add explicit install and compile commands**

```bash
.venv/bin/python scripts/setup_synthetic_tools.py --install-ktx-4.4.2
.venv/bin/python scripts/synthetic_village.py \
  build-h3-ktx2 \
  --authored-root .nantai-studio/h3/authored \
  --tool-receipt .nantai-studio/tools/ktx-4.4.2/receipt.json \
  --output-root .nantai-studio/h3/ktx2
```

The build compiles twice to separate staging roots and requires byte equality
before publication. Decoded quality gates are exact: base-colour SSIM is at
least `0.97`; normal mean cosine similarity is at least `0.98` and its first
percentile is at least `0.90`; ORM per-channel maximum absolute error is at
most `12/255`. If ETC1S ORM misses the gate, rebuild ORM as UASTC and require
the same threshold.

- [ ] **Step 5: Run focused and real-tool gates**

Run the focused pytest/Ruff/compileall gates, then the two commands above and
`ktx validate` over every published KTX2. Expected: eight base, eight normal,
and eight ORM objects validate with 13 mip levels; any failure prevents
publication.

- [ ] **Step 6: Commit and push**

Commit the toolchain module, setup/CLI changes, and focused tests with message
`feat(materials): verify pinned KTX2 compilation`, the required trailer, and
push `main`. Never commit installer or generated KTX bytes.

### Task 5: Material Bundle v2 and Dual-Variant Mesh Bundle v3

**Files:**
- Create: `pipeline/synthetic_village/material_bundle_v2.py`
- Create: `pipeline/synthetic_village/glb_ktx2_variant.py`
- Create: `pipeline/synthetic_village/mesh_asset_bundle_v3.py`
- Create: `tests/test_material_bundle_v2.py`
- Create: `tests/test_glb_ktx2_variant.py`
- Create: `tests/test_mesh_asset_bundle_v3.py`
- Modify: `pipeline/synthetic_village/material_bundle.py`
- Modify: `pipeline/synthetic_village/mesh_asset_bundle.py`
- Modify: existing v1/v2 regression tests

**Interfaces:**
- Consumes: verified source/authored/KTX packs, exact H2 material bundle, and
  exact H2 mesh bundle.
- Produces: `MATERIAL_BUNDLE_V2_SCHEMA`,
  `MESH_ASSET_BUNDLE_V3_SCHEMA`, `MaterialBundleV2`,
  `MeshAssetBundleV3`, `geometry_fingerprint_glb(...)`,
  `rewrite_glb_for_ktx2(...)`, schema-dispatched loaders, and exact
  primary/fallback verified reads.

- [ ] **Step 1: Write v1/v2 stability and v3 dual-profile RED tests**

```python
def test_material_v2_binds_h3_and_exact_h2(
    material_bundle_v2: MaterialBundleV2,
) -> None:
    assert material_bundle_v2.source_pack_id == H3_SOURCE_PACK_ID
    assert material_bundle_v2.fallback_bundle_id == H2_MATERIAL_BUNDLE_ID
    assert set(material_bundle_v2.profiles) == {
        "h3-ai-ktx2-4k", "h2-png-1k-fallback"
    }


def test_mesh_v3_variants_have_identical_geometry(
    mesh_bundle_v3: MeshAssetBundleV3,
    bundle_root: Path,
) -> None:
    for record in mesh_bundle_v3.records:
        primary = record.lod["2"].variants["h3-ai-ktx2-4k"]
        fallback = record.lod["2"].variants["h2-png-1k-fallback"]
        assert geometry_fingerprint_glb(
            read_variant(bundle_root, primary)
        ) == record.lod["2"].geometry_fingerprint
        assert geometry_fingerprint_glb(
            read_variant(bundle_root, fallback)
        ) == record.lod["2"].geometry_fingerprint
```

Reject missing profile counterparts, changed H2 LOD0/1 SHAs, changed geometry
fingerprint, KTX2 without `KHR_texture_basisu`, PNG fallback with the extension,
incomplete eight-slot replacement, wrong content type, unsafe URI, and
canonical identity drift in existing v1/v2 fixtures.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_material_bundle.py \
  tests/test_material_bundle_v2.py \
  tests/test_mesh_asset_bundle.py \
  tests/test_mesh_asset_bundle_v2.py \
  tests/test_glb_ktx2_variant.py \
  tests/test_mesh_asset_bundle_v3.py -q
```

- [ ] **Step 3: Implement immutable v2/v3 models and dispatch**

```python
MATERIAL_BUNDLE_V2_SCHEMA = (
    "nantai.synthetic-village.derived-material-bundle.v2"
)
MESH_ASSET_BUNDLE_V3_SCHEMA = (
    "nantai.synthetic-village.mesh-asset-bundle.v3"
)
H3_PROFILE_ID = "h3-ai-ktx2-4k"
H2_PROFILE_ID = "h2-png-1k-fallback"
```

`MaterialBundleV2` owns both profile closures and all source/authoring/encoder
evidence. `MeshAssetBundleV3` keeps exact H2 LOD0/1 descriptors and puts two
complete descriptors under each LOD2. Geometry fingerprints canonicalize
accessor data for positions, indices, normals, tangents, UVs, primitive-slot
assignment, bounds, and node transforms; image bytes and URIs are excluded.
Schema dispatch reads the bounded manifest once and recognizes only exact
v1/v2/v3 strings.

- [ ] **Step 4: Implement KHR_texture_basisu GLB rewrite**

```python
def rewrite_glb_for_ktx2(
    fallback_glb: bytes,
    replacements: Mapping[str, KtxTextureDescriptor],
) -> bytes:
    document, binary_chunk = parse_glb(fallback_glb)
    for texture in document["textures"]:
        source = texture.pop("source")
        image = document["images"][source]
        image["uri"] = replacements[image["uri"]].object_uri
        image["mimeType"] = "image/ktx2"
        texture.setdefault("extensions", {})["KHR_texture_basisu"] = {
            "source": source
        }
    document["extensionsUsed"] = sorted(
        set(document.get("extensionsUsed", ())) | {"KHR_texture_basisu"}
    )
    return canonical_glb_bytes(document, binary_chunk)
```

Before publication, compute and compare fallback/primary geometry
fingerprints. Any disagreement rejects the complete bundle.

- [ ] **Step 5: Run gates and publish private bundles**

Run focused pytest/Ruff/compileall/diff-check gates. Build to private staging,
verify every object, then atomically publish under
`.nantai-studio/h3/material-bundles/{bundle_id}` and
`.nantai-studio/h3/mesh-bundles/{bundle_id}`.

- [ ] **Step 6: Commit and push**

Commit only the new modules, dispatch edits, and tests with message
`feat(mesh): bind H3 KTX2 and exact H2 variants`, the required trailer, and
push `main`.

### Task 6: Runtime-v3 Projection and Verified Studio Routes

**Files:**
- Modify: `pipeline/synthetic_village/mesh_chunk.py`
- Modify: `pipeline/studio_server.py`
- Modify: `tests/test_mesh_chunk.py`
- Modify: `tests/test_studio_server.py`
- Modify: `tests/test_studio_job_http.py`

**Interfaces:**
- Consumes: canonical mesh chunk v1, `MaterialBundleV2`, and
  `MeshAssetBundleV3`.
- Produces: `MESH_CHUNK_RUNTIME_V3_SCHEMA`,
  `MeshChunkRuntimeManifestV3`, `project_mesh_chunk_runtime_v3(...)`, and
  immutable verified asset routes.

- [ ] **Step 1: Write runtime and HTTP RED tests**

```python
def test_runtime_v3_exposes_both_profiles_without_payload_bytes(
    runtime_v3: MeshChunkRuntimeManifestV3,
) -> None:
    assert runtime_v3.schema_version.endswith("runtime.v3")
    assert set(runtime_v3.profiles) == {
        "h3-ai-ktx2-4k", "h2-png-1k-fallback"
    }
    assert runtime_v3.predicted_compressed_texture_bytes <= 512 * 1024 * 1024
    assert b"\\xabKTX 20" not in canonical_mesh_chunk_runtime_bytes(runtime_v3)


def test_profile_asset_route_is_exact_and_immutable(
    studio_server: StudioServerFixture,
    runtime_v3: MeshChunkRuntimeManifestV3,
) -> None:
    descriptor = runtime_v3.profiles["h3-ai-ktx2-4k"].textures[0]
    response = studio_server.get(descriptor.url)
    assert response.status == 200
    assert response.headers["Content-Type"] == "image/ktx2"
    assert response.headers["Content-Length"] == str(descriptor.bytes)
    assert response.headers["ETag"] == f'"{descriptor.sha256}"'
    assert sha256(response.body).hexdigest() == descriptor.sha256
```

Also test HEAD/304, negative coordinates, exact URL template, wrong bundle or
profile, redirects, unsafe extensions, missing counterpart, over-memory
manifest, and no accidental `on_demand=true` projection for reconstruction
chunks.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/python -m pytest \
  tests/test_mesh_chunk.py \
  tests/test_studio_server.py \
  tests/test_studio_job_http.py -q
```

- [ ] **Step 3: Add strict v3 projection**

```python
MESH_CHUNK_RUNTIME_V3_SCHEMA = (
    "nantai.synthetic-village.mesh-chunk-runtime.v3"
)
MAX_H3_COMPRESSED_TEXTURE_BYTES = 512 * 1024 * 1024
```

The v3 model carries source/material/mesh IDs, exact truth flags, predicted
compressed bytes, and two complete sorted profile descriptors. It reuses the
canonical chunk's coordinates, absolute world offset, bounds, terrain,
ribbons, and instances unchanged.

- [ ] **Step 4: Add verified profile-aware routes**

Implement exact routes:

```text
/api/world/mesh-assets/{mesh_bundle_id}/{profile_id}/{asset_id}/lod{lod}.glb
/api/world/mesh-textures/{mesh_bundle_id}/{profile_id}/{sha256}.ktx2
/api/world/mesh-textures/{mesh_bundle_id}/{profile_id}/{sha256}.png
```

Resolve only through verified bundle descriptors. Read stable bounded bytes,
recheck length/SHA, use `model/gltf-binary`, `image/ktx2`, or `image/png`,
support GET/HEAD/ETag/304, and return structured 404/409/422 errors without
leaking local paths.

- [ ] **Step 5: Run focused gates**

Run the three pytest files, Ruff/compileall on both Python modules, and
`git diff --check`. Expected: v1/v2 regression tests and new v3 tests pass.

- [ ] **Step 6: Commit and push**

Commit the five explicit files with message
`feat(studio): serve dual-profile mesh runtime`, the required trailer, and
push `main`.

### Task 7: Vendor Three KTX2 Runtime and Freeze Profile Selection

**Files:**
- Modify: `web/viewer/vendor/fetch-vendor.sh`
- Modify: `web/viewer/vendor/VENDOR.md`
- Modify: `web/viewer/vendor.test.mjs`
- Create: `web/viewer/vendor/three/addons/loaders/KTX2Loader.js`
- Create: `web/viewer/vendor/three/addons/utils/WorkerPool.js`
- Create: `web/viewer/vendor/three/examples/jsm/libs/basis/basis_transcoder.js`
- Create: `web/viewer/vendor/three/examples/jsm/libs/basis/basis_transcoder.wasm`
- Create: `web/viewer/material-profile.mjs`
- Create: `web/viewer/material-profile.test.mjs`

**Interfaces:**
- Consumes: Three.js `0.180.0`, a constructed `WebGLRenderer`, one verified
  canary descriptor, and predicted compressed bytes.
- Produces: an offline-closed KTX2 module graph and
  `createMaterialProfileController(...)`.

- [ ] **Step 1: Write vendoring and state-machine RED tests**

```javascript
test('profile freezes H3 only after renderer canary succeeds', async () => {
  const controller = createMaterialProfileController({
    maxCompressedBytes: 512 * 1024 * 1024,
    createKtx2Loader: () => fakeKtx2Loader({ canary: 'ok' }),
  });
  const selected = await controller.select({
    renderer: fakeRenderer(),
    canary: verifiedCanary(),
    predictedCompressedBytes: 128 * 1024 * 1024,
  });
  assert.equal(selected.profileId, 'h3-ai-ktx2-4k');
  assert.equal(controller.snapshot().state, 'frozen');
});


test('over-budget selection freezes explicit H2 fallback', async () => {
  const controller = createMaterialProfileController({
    maxCompressedBytes: 512 * 1024 * 1024,
  });
  const selected = await controller.select({
    renderer: fakeRenderer(),
    canary: verifiedCanary(),
    predictedCompressedBytes: 512 * 1024 * 1024 + 1,
  });
  assert.equal(selected.profileId, 'h2-png-1k-fallback');
  assert.equal(selected.fallbackReason.code, 'compressed_memory_budget');
});
```

Test capability absence, redirect/SHA/canary/decode failure, repeated selection,
state mutation after freeze, and bounded public snapshots with no URL/hash/raw
error leakage. Vendor tests require exact SHA/bytes/license rows and an offline
module graph.

- [ ] **Step 2: Run RED**

```bash
node --test \
  web/viewer/vendor.test.mjs \
  web/viewer/material-profile.test.mjs
```

- [ ] **Step 3: Vendor the exact Three 0.180.0 closure**

Add fixed jsDelivr `three@0.180.0` URLs for `KTX2Loader.js`, `WorkerPool.js`,
and `examples/jsm/libs/basis/{basis_transcoder.js,basis_transcoder.wasm}` to
`fetch-vendor.sh`. Measure exact SHA-256 and byte counts after download, record
them and upstream MIT license coverage in `VENDOR.md`, then make
`vendor.test.mjs` verify them.

- [ ] **Step 4: Implement the frozen profile controller**

```javascript
export const H3_PROFILE_ID = 'h3-ai-ktx2-4k';
export const H2_PROFILE_ID = 'h2-png-1k-fallback';

export function createMaterialProfileController({
  createKtx2Loader,
  verifyAndReadCanary,
  maxCompressedBytes = 512 * 1024 * 1024,
}) {
  // states: unselected -> selecting -> frozen; frozen may transition once
  // to rolling-back -> fallback-frozen after a runtime H3 failure.
}
```

Call `loader.setTranscoderPath(...)`, `loader.detectSupport(renderer)`, and
`loader.parse(verifiedBytes, onLoad, onError)`. Dispose the canary texture
after the decision. Normalize internal errors to enumerated fallback codes and
human-readable Chinese messages.

- [ ] **Step 5: Run Node and vendor gates**

```bash
node --test \
  web/viewer/vendor.test.mjs \
  web/viewer/material-profile.test.mjs
node tools/verify_vendor.mjs
git diff --check
```

- [ ] **Step 6: Commit and push**

Commit the exact vendor and controller files with message
`feat(viewer): add verified KTX2 profile selection`, the required trailer, and
push `main`.

### Task 8: Atomic Viewer Loading, Rollback, Weather, and UX Evidence

**Files:**
- Modify: `web/viewer/mesh-world.mjs`
- Modify: `web/viewer/mesh-world.test.mjs`
- Modify: `web/viewer/verified-mesh-resources.mjs`
- Modify: `web/viewer/verified-mesh-resources.test.mjs`
- Modify: `web/viewer/main.js`
- Modify: `web/viewer/index.html`
- Modify: `web/viewer/index-contract.test.mjs`
- Modify: `web/viewer/mesh-weather.test.mjs`
- Modify: `web/viewer/bridge.mjs`
- Modify: `web/viewer/bridge.test.mjs`
- Modify: `web/studio/viewer-bridge.mjs`
- Modify: `web/studio/viewer-bridge.test.mjs`

**Interfaces:**
- Consumes: runtime-v3, frozen material-profile controller, verified fetch,
  `KTX2Loader`, `GLTFLoader`, renderer, and existing H2 mesh scheduler.
- Produces: profile-aware URL resolution, verified KTX2 semantic caches,
  all-or-nothing H3/H2 visible-world state, and bounded honest diagnostics.

- [ ] **Step 1: Write runtime-v3 and atomic-rollback RED tests**

```javascript
test('runtime v3 resolves only the selected closure', () => {
  const runtime = validateMeshChunkRuntime(runtimeV3Fixture());
  const urls = resolveSelectedProfile(runtime, 'h3-ai-ktx2-4k');
  assert.ok(urls.every((entry) => entry.media_type === 'image/ktx2'
    || entry.media_type === 'model/gltf-binary'));
  assert.equal(JSON.stringify(urls).includes('.png'), false);
});


test('one H3 decode failure disposes every H3 resource before H2 reload', async () => {
  const events = [];
  const world = createProfileAwareWorld(harness({ events }));
  await world.loadVisible(h3RuntimeFixture({ failTextureIndex: 3 }));
  assert.deepEqual(events, [
    'h3-load-start', 'h3-failure', 'h3-chunks-disposed',
    'h3-templates-disposed', 'h3-textures-disposed',
    'ktx-workers-disposed', 'h2-reload-start', 'h2-reload-complete',
  ]);
  assert.equal(world.snapshot().mixedProfiles, false);
});
```

Also test exact MIME/length/SHA before parse, one fetch/transcode/GPU object per
semantic key, reference counts, 60/62-second stability, negative/far
coordinates, six weather states retaining exact texture object identity,
clear-state scalar restoration, bounded diagnostics, and no raw URL/hash/error
in bridge messages.

- [ ] **Step 2: Run RED**

```bash
node --test \
  web/viewer/mesh-world.test.mjs \
  web/viewer/verified-mesh-resources.test.mjs \
  web/viewer/mesh-weather.test.mjs \
  web/viewer/index-contract.test.mjs \
  web/viewer/bridge.test.mjs \
  web/studio/viewer-bridge.test.mjs
```

- [ ] **Step 3: Implement strict v3 parsing and verified KTX2 resources**

Add exact-key validation for runtime-v3 and both profile closures.
`createVerifiedMeshResourceStore` receives `materialProfile`,
`ktx2Loader`, and `onProfileFailure`. It verifies bytes before parsing, keys
textures by:

```javascript
[
  sha256, role, colourSpace, minFilter, magFilter, wrapS, wrapT,
  alphaMode, flipY, materialProfile,
].join('|')
```

Counters are bounded integers for network fetches, KTX transcodes, PNG
decodes, GPU creations, compressed mip bytes, and active/idle templates.

- [ ] **Step 4: Implement one global rollback and whole-set reload**

Select the profile before the first mesh request. On post-selection H3 failure,
pause scheduling, dispose all visible and cached H3 resources plus KTX workers,
transition the controller once to fallback, and reload the current 3 by 3
visible set using only H2 descriptors. Resume scheduling only after all nine
fallback chunks are ready.

- [ ] **Step 5: Add honest HUD and bridge fields**

Add:

```text
材质配置: AI 合成 4K · KTX2
or
材质配置: H2 1K 回退 · <human-readable reason>
真实性: synthetic · preview-only · not real-photo
压缩纹理: <observed MiB> / 512 MiB
```

Bridge evidence exposes only profile ID, fallback code, truth flags, bundle
identity, predicted/observed compressed bytes, and bounded counters.

- [ ] **Step 6: Run full Viewer gates and commit**

```bash
node --test web/viewer/*.test.mjs web/studio/*.test.mjs
git diff --check -- \
  web/viewer/mesh-world.mjs \
  web/viewer/verified-mesh-resources.mjs \
  web/viewer/main.js \
  web/viewer/index.html \
  web/viewer/bridge.mjs \
  web/studio/viewer-bridge.mjs
```

Commit all explicit Viewer/Studio files and tests with message
`feat(viewer): load H3 materials atomically`, the required trailer, and push
`main`.

### Task 9: Deterministic Local Release, Safe Download, and Install

**Files:**
- Create: `pipeline/synthetic_village/h3_release.py`
- Create: `tests/test_h3_release.py`
- Modify: `scripts/synthetic_village.py`
- Modify: `tests/test_synthetic_village_cli.py`

**Interfaces:**
- Consumes: verified source/authored/KTX/material/mesh bundles and vendored
  runtime identity.
- Produces: `H3_RELEASE_SCHEMA`, `build_h3_release(...)`,
  `verify_h3_release(...)`, `download_h3_release(...)`,
  `install_h3_release(...)`, deterministic `.tar.zst`, and `SHA256SUMS`.

- [ ] **Step 1: Write archive and hostile-extraction RED tests**

```python
def test_release_archive_is_byte_deterministic(
    complete_h3_closure: Path,
    tmp_path: Path,
) -> None:
    first = build_h3_release(complete_h3_closure, tmp_path / "first")
    second = build_h3_release(complete_h3_closure, tmp_path / "second")
    assert first.archive_sha256 == second.archive_sha256
    assert first.archive.read_bytes() == second.archive.read_bytes()


@pytest.mark.parametrize(
    "member",
    ("../escape", "/absolute", "objects/link", "duplicate/path"),
)
def test_install_rejects_unsafe_member(
    malicious_release: Path,
    member: str,
    tmp_path: Path,
) -> None:
    add_malicious_member(malicious_release, member)
    with pytest.raises(H3ReleaseError):
        install_h3_release(malicious_release, tmp_path / "studio")
```

Also reject wrong outer SHA, missing internal object, wrong manifest SHA,
symlink/hardlink/device entries, unbounded file count/size, partial extraction,
wrong active bundle identity, redirected/changed download URLs, download byte
overflow, response-length disagreement, and public-upload commands.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/python -m pytest \
  tests/test_h3_release.py \
  tests/test_synthetic_village_cli.py -q
```

- [ ] **Step 3: Implement canonical packaging and atomic installation**

Normalize member paths, order, mode, uid/gid, timestamp, and LF metadata.
Write sorted `SHA256SUMS`, verify every object before archive creation, and
extract only regular files to owned staging. Fsync files/directories, verify
again, then publish with a no-replace rename. Keep prior H2 active identity
available for one-field rollback. `download_h3_release` accepts an explicit
HTTPS URL and expected SHA-256, forbids redirects and URL changes, streams to
owned staging with a fixed maximum byte count, fsyncs, verifies the expected
SHA, and only then exposes the staged archive to the installer.

- [ ] **Step 4: Add exact CLI**

```bash
.venv/bin/python scripts/synthetic_village.py build-h3-release \
  --closure-root .nantai-studio/h3 \
  --output .nantai-studio/releases/h3-ai-4k-ktx2.tar.zst
.venv/bin/python scripts/synthetic_village.py verify-h3-release \
  --archive .nantai-studio/releases/h3-ai-4k-ktx2.tar.zst
.venv/bin/python scripts/synthetic_village.py download-h3-release \
  --url https://github.com/taomic2035/nantai-3d/releases/download/h3-local-canary/h3-ai-4k-ktx2.tar.zst \
  --sha256 0000000000000000000000000000000000000000000000000000000000000000 \
  --output .nantai-studio/downloads/h3-ai-4k-ktx2.tar.zst
.venv/bin/python scripts/synthetic_village.py install-h3-release \
  --archive .nantai-studio/downloads/h3-ai-4k-ktx2.tar.zst \
  --studio-root .nantai-studio
```

The all-zero SHA in the command example is a deliberate negative smoke and
must fail closed. The real local-release SHA printed by `build-h3-release`
replaces it only after an explicitly authorized upload or local HTTP fixture;
this plan does not authorize creating a public GitHub release.

- [ ] **Step 5: Run gates, commit, and push**

Run focused pytest/Ruff/compileall/diff-check gates. Commit the module,
CLI, and tests with message `feat(release): package verified H3 materials`,
the required trailer, and push `main`.

### Task 10: Real Build, Visual/Browser Acceptance, and Exact Activation

**Files:**
- Modify only after green: `web/data/manifest.json`
- Create: `docs/verification/2026-07-19-h3-ai-4k-ktx2-materials.md`
- Create: `handoff/FEEDBACK-CODEX-012-h3-ai-4k-ktx2-materials.md`

**Interfaces:**
- Consumes: the exact installed H3 release and fresh Studio server.
- Produces: fixed Blender/Viewer A/B evidence, browser performance and
  resource evidence, active exact bundle identities, and an honest H3-B gap.

- [ ] **Step 1: Run complete machine and artifact verification**

```bash
.venv/bin/python make.py doctor --verify-assets
.venv/bin/python scripts/synthetic_village.py verify-h3-release \
  --archive .nantai-studio/releases/h3-ai-4k-ktx2.tar.zst
.venv/bin/python -m pytest tests -q
node --test web/viewer/*.test.mjs web/studio/*.test.mjs
.venv/bin/python -m ruff check pipeline scripts tests
.venv/bin/python -m compileall -q pipeline scripts tests
git diff --check
```

Record exact counts and failures. Do not activate if any required gate fails.

- [ ] **Step 2: Render fixed H2/H3 visual comparisons**

Use identical Blender and Viewer camera matrices for three pedestrian close
cameras, clear/rain/night, and roof/timber/stone/ground crops. Reject visible
wrap seams, perspective-baked lighting, repeated motifs, inverted normals,
mip shimmer, compression blocks, colour-space errors, or weather material
identity loss.

- [ ] **Step 3: Run browser acceptance at all coordinates and weather modes**

Start a fresh Studio server and use the bridge to verify:

```text
default: 9 ready / 0 pending / 0 failed
(123456,-98765,12): 9 / 0 / 0
(-123456,98765,12): 9 / 0 / 0
weather: clear -> rain -> overcast -> fog -> night -> snow -> clear
frame median <= 33.3 ms
frame p95 <= 50 ms
median regression <= 30 percent from H2
compressed mip bytes <= 512 MiB
resources stable at 60 and 62 seconds
mixedProfiles == false
application warnings/errors == 0
```

Run a second session with forced KTX2 capability absence and require the same
coordinate/weather smoke under explicit H2 fallback.

- [ ] **Step 4: Activate only exact accepted identities**

Change `web/data/manifest.json` to the accepted material-v2 and mesh-v3 IDs
only after every prior gate passes. Restart the server from current `main`,
rerun 9/9 default and one far-coordinate smoke, and confirm HUD/bridge show
`AI 合成 4K · KTX2`, `synthetic`, `preview-only`, and `not real-photo`.

- [ ] **Step 5: Write evidence and remaining-limit reports**

Record exact source/authored/material/mesh/release identities, KTX tool and
vendor hashes, map dimensions/mips, memory/performance measurements, visual
contact-sheet paths and SHAs, server URL, activation commit, and rollback ID.
State explicitly that H3-A improves material fidelity but does not complete
building/roof/tree/fence/wall topology; that remaining model-realism work is
H3-B and requires a separate approved specification.
Also state that the current Apple Silicon build is local-platform verified,
not cross-platform portable, until an independent supported-platform build
produces byte-equal KTX2 objects and passes the same validation closure.

- [ ] **Step 6: Commit and push activation**

```bash
git add \
  web/data/manifest.json \
  docs/verification/2026-07-19-h3-ai-4k-ktx2-materials.md \
  handoff/FEEDBACK-CODEX-012-h3-ai-4k-ktx2-materials.md
git commit -m "feat(viewer): activate verified H3 materials" \
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" \
  -- \
  web/data/manifest.json \
  docs/verification/2026-07-19-h3-ai-4k-ktx2-materials.md \
  handoff/FEEDBACK-CODEX-012-h3-ai-4k-ktx2-materials.md
git push origin main
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
```

Expected final divergence: `0 0`; unrelated collaborator WIP remains unstaged.

## Spec Coverage Audit

- Source truth, three-candidate selection, private/public boundary: Tasks 1–2.
- Deterministic 4096 master, source preservation, PBR semantics: Task 3.
- Pinned KTX 4.4.2, complete mips, quality and repeatability: Task 4.
- Material bundle v2, mesh bundle v3, topology fingerprint parity: Task 5.
- Runtime-v3 and verified Studio delivery: Task 6.
- Exact Three/Basis vendoring and renderer canary: Task 7.
- Session-wide selection, atomic rollback, cache/memory/weather/UX evidence:
  Task 8.
- Deterministic local release and safe download/install: Task 9.
- Visual, coordinate, weather, performance, fallback, activation, and honest
  H3-B boundary: Task 10.
