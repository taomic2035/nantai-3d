# Nantai 3D v1.0.0-preview.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `tdd` for every implementation
> task and `quality-gate` before release. Work only on `main`, use path-limited
> staging, preserve unrelated WIP, and push each verified logical checkpoint.

**Goal:** Publish a deterministic, clean-room-verifiable Preview2 runtime that
opens to a recognizable basic synthetic 3D scene and honestly exposes its mesh,
3DGS/LOD, coordinate and provenance boundaries.

**Architecture:** A standard-library `pipeline.preview_release` module owns the
canonical receipt, path safety, content hashing, deterministic ZIP layout and
independent extracted-tree verification. Thin scripts expose build/verify
commands. Studio projects the verified package state separately from scene
trust; Viewer defaults to the hash-verified model preview and keeps the
Gaussian/point representation explicit. Release inputs remain ignored binary
payloads, but a tracked lock document pins their exact manifests and hashes.

**Tech stack:** Python 3.11+ standard library for release tooling, existing
Pydantic/HTTP Studio server, browser ES modules, Node built-in test runner,
Three.js/Spark Viewer, GitHub CLI.

**Design:** `docs/superpowers/specs/2026-07-26-preview2-release-design.md`

---

## File structure

- Create `release/preview2-inputs.json`: tracked source lock and declared
  release roles.
- Create `pipeline/preview_release.py`: receipt model, canonical JSON, safe
  source resolution, deterministic builder and independent verifier.
- Create `scripts/build_preview_release.py`: build CLI.
- Create `scripts/verify_preview_release.py`: standard-library verification
  CLI.
- Create `tests/test_preview_release.py`: adversarial unit and archive tests.
- Create `tests/test_preview_release_cli.py`: CLI and clean extraction tests.
- Modify `make.py`: `build-preview` and `verify-preview` targets.
- Create `tests/test_make_preview_targets.py`: runner command contracts.
- Modify `pipeline/studio_server.py`: fail-closed package receipt projection and
  Preview2 capability wording.
- Modify `tests/test_studio_server.py` and
  `tests/test_studio_capabilities.py`: absent/valid/corrupt receipt and wording.
- Modify `web/studio/index.html`, `web/studio/styles.css`,
  `web/studio/app.js`: polished read-only state, package evidence and overflow
  fix.
- Modify corresponding Studio tests.
- Modify `web/viewer/index.html`, `web/viewer/main.js` and focused Viewer tests:
  staged loading/failure, model-default presentation and camera framing.
- Modify `pyproject.toml`, `README.md`, `docs/releases/1.0-preview.md`.
- Create `docs/releases/1.0-preview.2.md`: authoritative user guide.
- Create release outputs below ignored `.nantai-studio/releases/v1.0.0-preview.2/`.

### Task 1: Release source lock and fail-closed receipt verifier

**Files:**

- Create: `release/preview2-inputs.json`
- Create: `tests/test_preview_release.py`
- Create: `pipeline/preview_release.py`

- [ ] **Step 1: Record the actual frozen input facts**

Measure and record:

- `web/data/manifest.json`;
- `web/data/recon/recon_manifest.json` and every referenced full/LOD file;
- `web/data/recon/model-preview/manifest.json` and its GLB;
- `assets/registry.json` and exactly its 11 `ply` payloads.

The lock document declares `v1.0.0-preview.2`, the intended root paths, source
manifest SHA-256 values, presentation roles and explicit excluded prefixes. It
does not mark the scene real, metric or aligned.

- [ ] **Step 2: Write RED verifier tests**

Cover:

```python
def test_canonical_receipt_is_lf_sorted_and_stable(): ...
def test_safe_member_path_rejects_absolute_parent_backslash_and_reserved_names(): ...
def test_verify_tree_accepts_exact_receipt(tmp_path): ...
def test_verify_tree_rejects_missing_changed_and_unexpected_protected_files(tmp_path): ...
def test_package_verification_preserves_scene_trust(tmp_path): ...
```

Fixtures use tiny text/binary files; production data never enters the test
tree.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
python -m pytest tests/test_preview_release.py -q
```

Expected: collection fails because `pipeline.preview_release` is absent.

- [ ] **Step 4: Implement the minimal pure verifier**

Implement standard-library-only primitives:

```python
canonical_json_bytes(payload) -> bytes
safe_posix_member_path(value) -> PurePosixPath
sha256_file(path) -> str
build_receipt(...)
verify_release_tree(root) -> ReleaseVerification
```

Rules:

- lower-case 64-character SHA-256 only;
- canonical LF JSON with sorted keys and a trailing newline;
- no absolute paths, `..`, backslashes, empty components, symlinks or duplicate
  normalized names;
- receipt version, source commit and declared trust literals validate;
- all protected files are remeasured;
- missing, changed or unexpected protected content fails closed;
- package verification reports no scene-trust promotion.

- [ ] **Step 5: Run RED→GREEN and lint**

```bash
python -m pytest tests/test_preview_release.py -q
python -m ruff check pipeline/preview_release.py tests/test_preview_release.py
git diff --check
```

Expected: all focused tests pass; Ruff and diff check are clean.

- [ ] **Step 6: Commit and push checkpoint 1**

```bash
git add release/preview2-inputs.json pipeline/preview_release.py tests/test_preview_release.py
git commit -- <same paths>
git push origin main
```

Commit: `feat(release): add Preview2 receipt verifier`

### Task 2: Deterministic allowlisted runtime archive

**Files:**

- Modify: `tests/test_preview_release.py`
- Modify: `pipeline/preview_release.py`
- Create: `tests/test_preview_release_cli.py`
- Create: `scripts/build_preview_release.py`
- Create: `scripts/verify_preview_release.py`

- [ ] **Step 1: Add RED archive and CLI tests**

Assert:

- two builds from identical input bytes are byte-identical;
- ZIP members use one Preview2 root and POSIX paths;
- timestamp, permissions, compression and order are normalized;
- runtime code comes only from declared patterns;
- `*.test.mjs`, private roots, duplicate recon subtree and Batch35 are absent;
- manifest-declared world/recon/model/asset files are the only binary inputs;
- builder rejects dirty release-owned inputs, symlinks, missing manifests,
  hash mismatches and version/commit disagreement;
- verifier succeeds after extraction and rejects one-bit corruption;
- CLI exit codes are `0` valid, `2` invalid input/package.

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_preview_release.py tests/test_preview_release_cli.py -q
```

Expected: deterministic build and CLI assertions fail.

- [ ] **Step 3: Implement builder and thin CLIs**

The builder:

- resolves fixed runtime file patterns;
- follows only validated paths declared by the world, reconstruction,
  model-preview and asset-registry manifests;
- writes `RELEASE-MANIFEST.json` and `SHA256SUMS.txt`;
- uses a fixed ZIP epoch and normalized Unix modes;
- embeds a package content ID independent of the outer ZIP;
- verifies an extracted temporary copy before publishing the archive;
- writes the `.sha256` sidecar only after success;
- never writes below `web/data`, `assets` or private input roots.

The verify CLI accepts either an extracted root or an archive, extracts archives
to a temporary directory, and prints a compact machine-readable summary.

- [ ] **Step 4: Run focused and adversarial gates**

```bash
python -m pytest tests/test_preview_release.py tests/test_preview_release_cli.py -q
python -m ruff check pipeline/preview_release.py scripts/build_preview_release.py \
  scripts/verify_preview_release.py tests/test_preview_release.py \
  tests/test_preview_release_cli.py
git diff --check
```

- [ ] **Step 5: Commit and push checkpoint 2**

Commit only the five task paths.

Commit: `feat(release): build deterministic Preview2 runtime`

### Task 3: Cross-platform task-runner entry points and real package build

**Files:**

- Create: `tests/test_make_preview_targets.py`
- Modify: `make.py`
- Modify: `release/preview2-inputs.json` only if fresh measured facts require it
- Create outputs only under ignored
  `.nantai-studio/releases/v1.0.0-preview.2/`

- [ ] **Step 1: Write RED runner tests**

Patch `make.run` and assert:

```python
make.build_preview()
# invokes current Python + scripts/build_preview_release.py

make.verify_preview()
# invokes current Python + scripts/verify_preview_release.py
```

Paths come from optional `DIST` and `ARCHIVE` environment variables without
shell quoting assumptions.

- [ ] **Step 2: Implement and verify targets**

```bash
python -m pytest tests/test_make_preview_targets.py -q
python make.py help
```

- [ ] **Step 3: Build twice from current exact inputs**

```bash
python scripts/build_preview_release.py \
  --version v1.0.0-preview.2 \
  --output .nantai-studio/releases/v1.0.0-preview.2/build-a.zip
python scripts/build_preview_release.py \
  --version v1.0.0-preview.2 \
  --output .nantai-studio/releases/v1.0.0-preview.2/build-b.zip
shasum -a 256 .nantai-studio/releases/v1.0.0-preview.2/build-a.zip \
  .nantai-studio/releases/v1.0.0-preview.2/build-b.zip
```

Expected: identical SHA-256.

- [ ] **Step 4: Extract and independently verify**

```bash
python scripts/verify_preview_release.py \
  .nantai-studio/releases/v1.0.0-preview.2/build-a.zip
```

Expected: exact protected file count, 11/11 assets, 25 baked chunks, model and
reconstruction verified, trust still synthetic/preview-only.

- [ ] **Step 5: Commit and push checkpoint 3**

Commit only `make.py` and the new test, plus the lock document if its measured
facts changed. Never stage ignored build outputs.

Commit: `build: expose Preview2 archive gates`

### Task 4: Studio package evidence and polished Preview read-only state

**Files:**

- Modify: `tests/test_studio_capabilities.py`
- Modify: `tests/test_studio_server.py`
- Modify: `pipeline/studio_server.py`
- Modify: `web/studio/capabilities.test.mjs`
- Modify: `web/studio/index-contract.test.mjs`
- Modify: `web/studio/app.js`
- Modify: `web/studio/index.html`
- Modify: `web/studio/styles.css`

- [ ] **Step 1: Add Python RED tests**

Test roots with no receipt, a valid fixture receipt and a corrupt protected
file. Require `/api/project` to expose a separate release object:

```json
{
  "version": "v1.0.0-preview.2",
  "package_status": "verified",
  "package_content_id": "...",
  "scene_trust_effect": "none"
}
```

Missing receipt remains `not-packaged`; malformed/mismatched receipt becomes
`invalid`. Neither path changes reconstruction geometry, coordinate or
artifact fields.

Update read-only capability wording to the approved Preview Chinese copy and
retain fail-closed disabled commands.

- [ ] **Step 2: Add Studio RED contracts**

Require:

- `Preview 只读模式`;
- a package verification badge separate from scene evidence;
- no prominent English milestone error;
- active presentation fidelity text;
- no horizontal overflow rules on the evidence strip;
- command reasons remain accessible when a disabled control is inspected.

- [ ] **Step 3: Implement the smallest server and UI projection**

Reuse `pipeline.preview_release.verify_release_tree`; cache by receipt and
protected-file stat signature only if tests prove corruption cannot be hidden.
If a safe cache is not small, verify once at server startup and retain the
immutable report for that process.

Studio copy must distinguish:

```text
发布包：已校验
场景证据：合成 / 仅预览 / 任意尺度
```

Do not rename package verification to `trusted`, `real`, `aligned` or
`production`.

- [ ] **Step 4: Run focused and full Studio gates**

```bash
python -m pytest tests/test_studio_capabilities.py tests/test_studio_server.py -q
node --test web/studio/capabilities.test.mjs \
  web/studio/index-contract.test.mjs web/studio/model.test.mjs
node --test web/studio/*.test.mjs
python -m ruff check pipeline/studio_server.py \
  tests/test_studio_capabilities.py tests/test_studio_server.py
git diff --check
```

- [ ] **Step 5: Commit and push checkpoint 4**

Commit: `feat(studio): present verified Preview package honestly`

### Task 5: Viewer startup, model-default, failure and framing UX

**Files:**

- Modify focused tests under `web/viewer/*.test.mjs`
- Modify: `web/viewer/index.html`
- Modify: `web/viewer/main.js`
- Modify: `web/studio/viewer-bridge.test.mjs` only if bridge state changes

- [ ] **Step 1: Add Viewer RED contracts**

Test pure/static seams for:

- ordered stages: world manifest, reconstruction, model manifest, model bytes,
  interactive;
- a valid model preview selects `model` as the default presentation;
- point/Gaussian remains an explicit secondary mode;
- required model load failure shows the failed stage and retry action;
- fallback selection names the representation instead of silently degrading;
- reset uses the authored model camera and keeps useful near/far clipping;
- active presentation state reports matching fidelity/provenance.

- [ ] **Step 2: Implement minimal presentation controller changes**

Keep model manifest/GLB hash validation mandatory. Do not make mesh mode
available from private API bundles in the static release.

Required UI:

- visible staged loader;
- actionable retry;
- concise control help;
- model-default three-quarter view;
- explicit `查看高斯/点云` and return-to-model labels;
- no indefinite spinner after a rejected model.

- [ ] **Step 3: Run Viewer and bridge suites**

```bash
node --test web/viewer/*.test.mjs
node --test web/studio/viewer-bridge.test.mjs
git diff --check
```

- [ ] **Step 4: Commit and push checkpoint 5**

Commit: `feat(viewer): make Preview2 model-first and recoverable`

### Task 6: Version, single-source documentation and release cover

**Files:**

- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/releases/1.0-preview.md`
- Create: `docs/releases/1.0-preview.2.md`
- Modify/create focused documentation/version tests if an existing convention
  is present.
- Create cover only below ignored release output directory.

- [ ] **Step 1: Add RED version/document assertions**

Require:

- package version `1.0.0rc2`;
- README links Preview2;
- Preview1 is explicitly historical;
- the authoritative guide names the exact archive and verification commands;
- known limits include synthetic, arbitrary scale, no photo texture and no
  completed real reconstruction;
- Batch35/private PBR are not described as shipped.

- [ ] **Step 2: Update documentation**

Document Windows PowerShell and POSIX flows:

1. SHA-256 verify;
2. extract;
3. create venv and install;
4. verify release tree;
5. start Studio;
6. expected first load and browser URL;
7. model/Gaussian switch;
8. real-data next path.

- [ ] **Step 3: Generate the cover from the packaged default presentation**

Use the actual extracted runtime and browser screenshot. Crop/resize only; do
not use a separate Blender render or image generator. Record dimensions,
bytes and SHA in the release notes after final build.

- [ ] **Step 4: Run documentation and focused gates**

```bash
python -m pytest tests -q -k "preview or release or version"
git diff --check
```

- [ ] **Step 5: Commit and push checkpoint 6**

Commit: `docs: publish Preview2 clean-room guide`

### Task 7: Full quality gate and clean-room browser acceptance

**Files:**

- No source edits unless a failing gate exposes a release-owned defect.
- Final artifacts below ignored
  `.nantai-studio/releases/v1.0.0-preview.2/`.

- [ ] **Step 1: Confirm synchronization and WIP preservation**

```bash
git fetch --prune origin
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count HEAD...origin/main
```

Re-hash the pre-existing `tests/test_synthetic_village_weather.py` WIP and
confirm it remains unchanged and unstaged.

- [ ] **Step 2: Run complete automated gates**

```bash
python -m pytest tests/ -q
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
python -m ruff check pipeline tests
git diff --check
```

Run the existing vendor integrity command identified by the current repository
tests. Record exact totals and duration.

- [ ] **Step 3: Build the final candidate twice**

Both archives must have the same SHA. Rename only the verified candidate to:

```text
nantai-3d-v1.0.0-preview.2-runtime.zip
nantai-3d-v1.0.0-preview.2-runtime.zip.sha256
```

- [ ] **Step 4: Exercise a fresh extraction**

In a new temporary directory:

- verify before install using the standard-library script;
- create a clean venv and install declared dependencies;
- start the packaged server;
- request Studio, Viewer, world manifest, recon manifest, model manifest and
  model GLB over HTTP;
- verify 11/11 assets and no private path dependency.

- [ ] **Step 5: Real browser acceptance**

Use a fresh browser context and the extracted package:

- wait for interactive model scene;
- inspect default and near views;
- switch model → Gaussian/point → model;
- reset camera;
- inspect loading and error presentation;
- confirm no console warnings/errors;
- confirm no horizontal page scrollbar at the supported desktop viewport;
- capture the final cover and QA screenshots.

- [ ] **Step 6: Corruption drill**

Copy the extracted tree, flip one protected byte, restart, and confirm:

- verifier exits `2`;
- Studio reports invalid package;
- scene trust is not promoted;
- failure is visible and does not spin indefinitely.

- [ ] **Step 7: Final checkpoint commit if and only if needed**

Any QA fix repeats the relevant focused and full gates, then lands as a small
path-limited commit and is pushed before release.

### Task 8: Tag, publish, download and independently reverify

**Files:**

- Git tag and GitHub Release only; no new source changes after the final gate.

- [ ] **Step 1: Freeze final commit**

Confirm:

- `HEAD == origin/main`;
- only the preserved unrelated weather-test WIP is unstaged;
- version, receipt and guide all name `v1.0.0-preview.2`;
- final archive receipt source commit equals `HEAD`.

- [ ] **Step 2: Create and push the tag**

```bash
git tag -a v1.0.0-preview.2 -m "Nantai 3D v1.0.0-preview.2"
git push origin v1.0.0-preview.2
```

- [ ] **Step 3: Publish a GitHub prerelease**

Attach exactly:

- runtime ZIP;
- ZIP SHA-256 sidecar;
- actual packaged-view cover PNG.

Release notes must lead with what users can see, then disclose the synthetic,
preview-only, non-metric and non-photoreal limits.

- [ ] **Step 4: Download into a new directory**

Use `gh release download v1.0.0-preview.2` into an empty temporary directory.
Do not verify the upload from the original local files.

- [ ] **Step 5: Verify downloaded bytes**

Compare:

- asset names and count;
- GitHub-reported sizes;
- sidecar SHA and actual archive SHA;
- cover SHA;
- extracted receipt and every protected content hash;
- clean HTTP smoke.

- [ ] **Step 6: Report completion**

Report exact:

- tag and commit;
- archive/cover byte sizes and SHA-256;
- protected file, world chunk, registry asset and Gaussian counts;
- full automated test totals;
- clean-browser result;
- corruption-drill result;
- remaining real-data, texture and training limits.

Release is incomplete until this downloaded-artifact verification succeeds.
