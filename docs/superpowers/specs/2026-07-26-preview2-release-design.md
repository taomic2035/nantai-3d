# Nantai 3D v1.0.0-preview.2 Release Design

Date: 2026-07-26
Status: design direction approved; written specification awaiting user review
Owner: Codex
Target tag: `v1.0.0-preview.2`

## 1. Purpose

Preview2 is the first release that must reproduce the current basic 3D result
from a clean checkout without relying on ignored `web/data`, private
`.nantai-studio` caches or an already-prepared developer machine.

This is a quality checkpoint, not permission to lower the product boundary.
The release must be coherent, inspectable and honest:

- it opens to a visible synthetic 3D scene rather than a sparse point cloud;
- the packaged mesh preview, Gaussian splat, LODs, world chunks and registry
  assets are all content-bound;
- Studio and Viewer describe the same scene and the same trust boundary;
- loading, failure and read-only states are usable rather than looking like an
  unfinished control panel;
- a downloaded archive passes the same verification as the locally built one.

Preview2 does not claim photo realism, real-place geometry, metric alignment or
production reconstruction. Those require real capture, accepted SfM and
external GPU training.

## 2. Evidence behind the design

The public Preview1 data archive is insufficient as a clean-room product:

- Git source contains neither ignored `web/data` nor the ignored registry PLY
  payloads;
- the data archive uses backslashes in ZIP member names;
- after following the published install flow, Studio reports missing
  reconstruction and `0/11` consumable assets;
- the clean Viewer falls back to a sparse point preview;
- the developer machine appears better only because private generated data and
  API-served bundles are already present.

A clean-room probe with only the current content-bound reconstruction and model
preview added produced a working basic mesh scene:

- `actual_reconstruction_engine=imported-3dgs`;
- `synthetic=true`;
- `geometry_usability=preview-proxy` / `preview-only`;
- a 2.9 MB GLB containing 539 mesh objects and 24 visual materials;
- a 67,858-Gaussian degree-3 SH PLY plus three point-preview LODs;
- no browser console errors.

The registry has 11 exact v1 PLY payloads. Their current bytes match every
registered SHA-256 and total approximately 7.25 MB, so they are small enough
to ship and prevent a misleading `0/11` clean-room state.

The current private mesh and material bundles are not release inputs. Together
they are approximately 475 MB, declare only synthetic `L0` evidence and do not
carry an accepted release-channel decision. Packaging them would turn local
cache availability into an implicit promotion.

## 3. Chosen release shape

Publish one deterministic runtime archive:

```text
nantai-3d-v1.0.0-preview.2-runtime.zip
```

along with:

```text
nantai-3d-v1.0.0-preview.2-runtime.zip.sha256
nantai-3d-v1.0.0-preview.2-cover.png
```

The runtime archive is self-contained with respect to Nantai project content:
it contains the required application source, vendored browser modules and all
default scene data. It still requires a supported Python interpreter and the
documented Python dependency installation; it does not pretend to be a native
standalone application.

The archive root contains:

```text
nantai-3d-v1.0.0-preview.2/
  RELEASE-MANIFEST.json
  SHA256SUMS.txt
  README.md
  LICENSE
  pyproject.toml
  make.py
  pipeline/
  scripts/
  web/
    studio/
    viewer/
    data/
  assets/
  docs/releases/
  docs/manual/
```

Packaging uses a declared allowlist. Tests, handoff notes, Git metadata,
private work directories, failed candidates, local renders, caches and
unapproved generated inputs are absent.

## 4. Frozen scene inputs

The release builder consumes only the following verified inputs.

### 4.1 Basic model presentation

- `web/data/recon/model-preview/manifest.json`;
- `web/data/recon/model-preview/village-canary.glb`;
- exact model SHA-256
  `5196a0271e0c202abd0fc69616900313c33ac654ef0c06df82ab75c4f220cfc1`;
- explicit fidelity `simplified-pbr-not-render-parity`;
- limitations including `no-photo-textures`.

This model is the default first presentation because it produces recognizable
geometry and materials in a clean environment.

### 4.2 Gaussian and LOD presentation

- `web/data/recon/recon_manifest.json`;
- `recon_full.ply`;
- `recon_lod0.ply`, `recon_lod1.ply`, `recon_lod2.ply`;
- all paths, byte lengths and SHA-256 values must match the reconstruction
  manifest before packaging.

The full PLY remains an imported synthetic 3DGS artifact. The LOD files are DC
point previews. Switching between mesh and Gaussian/point presentation must
not alter provenance or imply that both representations have texture parity.

The duplicate
`web/data/recon/source-consistent-canary-v1/` subtree is not referenced by the
default manifest and must not be packaged.

### 4.3 Deterministic world and replaceable assets

- the 25 baked chunks centered on the origin;
- all declared baked LODs referenced by `web/data/manifest.json`;
- `grid.on_demand=false` in the shipped static manifest;
- `assets/registry.json`;
- exactly the 11 `*_v1.ply` payloads referenced by that registry.

Every registry payload SHA is remeasured during build and verification.
Shipping the assets demonstrates the replaceable-asset contract, not real
geometry or real textures.

### 4.4 Explicit exclusions

The release must reject or omit:

- Batch35 prop-geometry-v2 and any Blender output derived from it while
  `REVIEW-CODEX-037` remains changes-requested;
- private hybrid-v3 mesh/material bundles without an accepted release decision;
- private local previews, training builds, training renders and work dirs;
- `source-consistent-canary-v1` duplicate reconstruction bytes;
- real-data or metric-aligned claims;
- optional roaming-graph or coverage artifacts whose scene identity does not
  bind to the packaged default scene;
- any file discovered only through a broad directory walk rather than the
  release allowlist.

## 5. Release manifest and immutability

`RELEASE-MANIFEST.json` is the package receipt. It contains:

- release schema and version;
- source Git commit and tag;
- build tool version;
- deterministic archive layout version;
- entry points;
- scene identity and presentation modes;
- every packaged content artifact with POSIX path, bytes, SHA-256 and role;
- trust summary copied from validated source manifests;
- explicit exclusions and known limitations;
- archive-independent package content ID derived from canonical manifest
  content.

The release receipt may state that the package is content-addressed and
verified only after all listed bytes have been measured. It must not rewrite
the reconstruction's geometry, alignment or realism claims.

Studio must distinguish these two facts:

```text
Release package: verified
Scene evidence: synthetic / preview-only / arbitrary scale
```

It must not collapse them into a generic "trusted" badge. A changed, missing or
extra protected file makes package verification fail closed and removes the
verified-package presentation.

## 6. Deterministic packaging

The release builder must:

1. require a clean release-owned path set and a known source commit;
2. validate every source manifest before copying;
3. resolve only allowlisted relative paths beneath the repository root;
4. reject symlinks, traversal, duplicate normalized paths and backslashes;
5. sort archive members by POSIX path;
6. normalize timestamps, permissions and compression settings;
7. write LF canonical JSON and checksum files;
8. produce byte-identical archives from two builds at the same commit;
9. emit the archive SHA sidecar only after the archive is closed;
10. verify the extracted archive in a fresh temporary directory.

The verifier is independent of the builder's in-memory file list. It reads the
receipt from extracted bytes, recomputes all hashes and rejects missing,
changed or unexpected protected content.

## 7. Studio and Viewer UX

### 7.1 Default experience

Opening `/web/studio/` from the runtime archive must:

- show a deliberate loading state with current stage;
- open the model preview by default once its manifest and GLB verify;
- keep the Gaussian/point presentation available as a labeled secondary view;
- frame the scene at a useful three-quarter camera rather than a distant
  top-down overview;
- provide reset, orbit, zoom and basic roam help without obscuring the scene;
- show a clear retry/failure state if a required artifact cannot load.

### 7.2 Read-only product language

Preview2 is intentionally read-only. The current English warning
`Job execution is not enabled in this Studio milestone.` must be replaced by a
polished Preview state that says what is available, for example:

```text
Preview 只读模式 · 可浏览场景与证据，重建任务暂未开放
```

This is product scope, not an error. Controls that cannot work in Preview are
hidden or disabled with an explanation.

### 7.3 Provenance presentation

The primary evidence strip must show, without horizontal page overflow:

- synthetic;
- preview-only;
- arbitrary scale / unaligned;
- simplified PBR mesh or 3DGS/DC preview, matching the active presentation;
- verified or unverified release-package receipt as a separate field.

Details remain available without dominating the viewport. No filename,
engine name, presentation mode or package verification may promote metric,
alignment, realism or source provenance.

### 7.4 Responsive and failure behavior

At the supported desktop viewport, Studio and the embedded Viewer must not
create a horizontal page scrollbar. Required-artifact 404, hash mismatch,
invalid JSON and WebGL/model-loader failure must produce a visible actionable
message rather than an indefinite spinner or silent point fallback.

An explicitly selected fallback may still load when available, but the UI must
name what failed and what representation is being shown.

## 8. Documentation and versioning

- tag: `v1.0.0-preview.2`;
- Python package version: `1.0.0rc2`;
- public title: `Nantai 3D 1.0 Preview 2`;
- one authoritative guide:
  `docs/releases/1.0-preview.2.md`;
- README points to that guide;
- Preview1 documentation remains historical and is labeled accordingly;
- the release guide documents Windows, macOS and Linux commands, expected
  loading behavior, browser entry point, SHA verification, known limitations
  and the real-data path.

The guide must never use the cover image as evidence of interactive render
parity. The cover must come from the actual packaged default model
presentation or be explicitly labeled as a separate synthetic illustration.

## 9. Verification strategy

Implementation follows TDD for release tooling and changed UX contracts.

### 9.1 Packaging tests

- exact allowlist and exclusion behavior;
- path traversal, symlink, duplicate and backslash rejection;
- manifest/hash/byte mismatch rejection;
- unexpected protected-file rejection;
- two-build byte determinism;
- POSIX member names and normalized metadata;
- source commit and version consistency;
- clean extraction verification.

### 9.2 Server and provenance tests

- release receipt absent, valid and invalid states;
- package verification never promotes scene trust;
- 11/11 registry payload visibility from the packaged tree;
- reconstruction/model-preview selection consistency;
- static `on_demand=false` remains honest;
- duplicate/private inputs are not required.

### 9.3 Viewer and Studio tests

- default mesh presentation and explicit Gaussian/point switch;
- loading, retry and required-artifact failure states;
- read-only Preview wording;
- active fidelity label tracks active presentation;
- no metric/real/aligned promotion;
- desktop overflow regression;
- camera reset and near-scene framing;
- existing Viewer, Studio and bridge suites remain green.

### 9.4 Real clean-room acceptance

From a fresh extraction, on the supported Mac:

1. install declared Python dependencies;
2. run the release verifier;
3. start the packaged server;
4. open Studio in a fresh browser context;
5. wait for the model scene to become interactive;
6. capture default and near-view screenshots;
7. switch to Gaussian/point presentation and back;
8. verify no browser console errors;
9. corrupt one copied artifact and confirm fail-closed UX;
10. rebuild twice and compare archive SHA-256.

Then run the complete repository gates:

```text
python -m pytest
ruff check
Viewer Node tests
Studio Node tests
vendor integrity tests
git diff --check
```

Release is blocked by any failing gate, broken clean-room startup, indefinite
loading state, missing scene, provenance mismatch or horizontal layout
overflow.

## 10. Release process

1. land implementation in small path-limited commits on `main`;
2. push each verified logical checkpoint;
3. run the complete quality gate;
4. build the archive twice from the intended release commit;
5. verify the extracted archive and visual UX;
6. create the signed/frozen tag `v1.0.0-preview.2`;
7. publish the prerelease and three declared assets;
8. download the assets back from GitHub;
9. compare downloaded bytes and SHA sidecar to the local release outputs;
10. report exact tag, commit, asset sizes, hashes, test totals and known
    limitations.

The release is not complete at upload time. It is complete only after the
downloaded artifact passes independent verification.

## 11. Definition of done

Preview2 is ready to publish when all of the following are true:

1. a clean extraction opens to a recognizable basic 3D scene;
2. mesh and Gaussian/point modes are both usable and honestly labeled;
3. the 25 world chunks and 11 registered assets are present and hash-valid;
4. the runtime has no dependency on ignored local data or private caches;
5. the package receipt verifies without promoting scene trust;
6. loading, error, camera, read-only and overflow UX meet the stated gate;
7. documentation and version strings agree on Preview2;
8. the deterministic builder and independent verifier pass;
9. full automated and real-browser gates are green;
10. GitHub-downloaded bytes match the locally verified release;
11. Batch35 and all other unapproved/private artifacts remain excluded;
12. release notes clearly state that the scene is synthetic, non-metric,
    non-photorealistic and not a completed real-data reconstruction.
